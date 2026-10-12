"""Generate one question: draft, statically check, run gates, retry with feedback.

The loop is the whole quality strategy. The model is allowed to be wrong; it is not
allowed to be *accepted* while wrong. Every rejection reason becomes the next
prompt's feedback, so each attempt is better informed than the last.

What is an infrastructure failure and what is the model's fault matters here:
a dead Docker daemon or an exhausted API quota must never be reported as "the
question was bad", or the bank would silently fill with false rejections.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from oa_engine.generation import (
    DraftError,
    JavaDraft,
    PythonDraft,
    Question,
    SqlDraft,
    build_java_question,
    build_python_question,
    build_sql_question,
)
from oa_engine.generation.prompts import (
    SYSTEM,
    BlindSolution,
    RelevanceVerdict,
    authoring_prompt,
    blind_prompt,
    relevance_prompt,
)
from oa_engine.llm import LLMError, LLMOutputError
from oa_engine.platform.logging import get_logger
from oa_worker.generation.gates import GateReport, run_gates
from oa_worker.generation.sandbox import Sandbox, SandboxUnavailableError

__all__ = ["LLM", "Attempt", "Outcome", "generate_question"]

log = get_logger(__name__, service="oa-worker")


class LLM(Protocol):
    model: str

    def generate(
        self, *, system: str, prompt: str, schema: type[Any], temperature: float = ...
    ) -> Any: ...


@dataclass
class Attempt:
    number: int
    stage: str  # llm | static | gates | accepted
    ok: bool
    detail: str = ""
    gates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Outcome:
    accepted: bool
    node: str
    language: str
    difficulty: str
    question: Question | None = None
    attempts: list[Attempt] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    aborted: str | None = None  # infrastructure reason; the question was not judged


def _gate_dicts(report: GateReport) -> list[dict[str, Any]]:
    return [{"gate": g.name, "ok": g.ok, "detail": g.detail[:600]} for g in report.gates]


def _judge_relevance(
    llm: LLM,
    *,
    node_id: str,
    node_label: str,
    node_description: str,
    question: Question,
) -> RelevanceVerdict | None:
    """Ask for a second opinion on skill fidelity; ``None`` if the answer is unusable.

    An unusable (malformed) answer is not treated as a rejection: the question already
    passed every deterministic gate, and a flaky judge should not discard good work. It is
    recorded as ``unjudged`` so it can be audited later.
    """
    reference = next(iter(question.reference_files.values()))
    try:
        verdict = llm.generate(
            system="You are a strict reviewer of assessment questions. Output only JSON.",
            prompt=relevance_prompt(
                node_id=node_id,
                node_label=node_label,
                node_description=node_description,
                title=question.title,
                prompt=question.prompt,
                reference=reference,
            ),
            schema=RelevanceVerdict,
            temperature=0.0,
        )
    except LLMOutputError:
        return None
    return verdict if isinstance(verdict, RelevanceVerdict) else None


def generate_question(
    *,
    llm: LLM,
    sandbox: Sandbox,
    language: str,
    node_id: str,
    node_label: str,
    node_description: str,
    difficulty: str,
    existing_titles: list[str],
    max_attempts: int = 3,
    blind_runs: int = 2,
    blind_required: int = 1,
    on_progress: Callable[[str], None] = lambda _msg: None,
) -> Outcome:
    """Produce one validated question, or explain why none could be produced."""
    outcome = Outcome(accepted=False, node=node_id, language=language, difficulty=difficulty)
    schemas: dict[str, type[BaseModel]] = {
        "python": PythonDraft,
        "sql": SqlDraft,
        "java": JavaDraft,
    }
    schema = schemas[language]
    builders: dict[str, Callable[..., Question]] = {
        "python": build_python_question,
        "sql": build_sql_question,
        "java": build_java_question,
    }
    feedback: str | None = None

    for number in range(1, max_attempts + 1):
        on_progress(f"attempt {number}/{max_attempts}: asking {llm.model}")
        prompt = authoring_prompt(
            language=language,
            node_id=node_id,
            node_label=node_label,
            node_description=node_description,
            difficulty=difficulty,
            existing_titles=existing_titles,
            feedback=feedback,
        )
        try:
            draft = llm.generate(system=SYSTEM, prompt=prompt, schema=schema, temperature=0.8)
        except LLMOutputError as exc:
            feedback = f"Your output was not valid for the required JSON schema: {exc}"
            outcome.attempts.append(Attempt(number, "llm", False, str(exc)[:600]))
            continue
        except LLMError as exc:
            outcome.aborted = f"LLM unavailable: {exc}"
            outcome.attempts.append(Attempt(number, "llm", False, str(exc)[:600]))
            return outcome

        try:
            question = builders[language](draft, difficulty=difficulty)
        except DraftError as exc:
            feedback = "Static checks failed:\n" + "\n".join(f"- {p}" for p in exc.problems)
            outcome.attempts.append(Attempt(number, "static", False, "; ".join(exc.problems)[:800]))
            on_progress(f"  static checks rejected it ({len(exc.problems)} problems)")
            continue

        on_progress("  static checks passed; running sandbox gates")

        def solve(prompt_text: str = question.prompt, q: Question = question) -> str:
            reply = llm.generate(
                system="You are a careful senior engineer. Output only the requested JSON.",
                prompt=blind_prompt(
                    language=language,
                    prompt=prompt_text,
                    starter=next(iter(q.starter_files.values())),
                ),
                schema=BlindSolution,
                temperature=0.4,
            )
            return str(reply.solution)

        try:
            report = run_gates(
                question, sandbox, solve, blind_runs=blind_runs, blind_required=blind_required
            )
        except SandboxUnavailableError as exc:
            outcome.aborted = f"sandbox unavailable: {exc}"
            return outcome
        except LLMOutputError as exc:
            feedback = f"The blind solver produced unusable output ({exc}); simplify the prompt."
            outcome.attempts.append(Attempt(number, "gates", False, feedback))
            continue
        except LLMError as exc:
            outcome.aborted = f"LLM unavailable during blind solve: {exc}"
            return outcome

        if report.ok:
            try:
                verdict = _judge_relevance(
                    llm,
                    node_id=node_id,
                    node_label=node_label,
                    node_description=node_description,
                    question=question,
                )
            except LLMError as exc:
                outcome.aborted = f"LLM unavailable during relevance check: {exc}"
                return outcome
            if verdict is None:
                report.metrics["relevance"] = "unjudged"
            else:
                report.metrics["primary_technique"] = verdict.primary_technique
                report.add(
                    "relevance",
                    verdict.assesses_target_skill,
                    ""
                    if verdict.assesses_target_skill
                    else (
                        f"This question is really about '{verdict.primary_technique}', not the "
                        f"target skill {node_id} ({node_label}). {verdict.reason} Rewrite it so "
                        f"that {node_label.lower()} is the core of the solution."
                    ),
                )

        if report.ok:
            on_progress("  ACCEPTED: " + ", ".join(f"{k}={v}" for k, v in report.metrics.items()))
            outcome.attempts.append(Attempt(number, "accepted", True, gates=_gate_dicts(report)))
            outcome.accepted = True
            outcome.question = question
            outcome.metrics = {**report.metrics, "attempts": number, **question.meta}
            return outcome

        feedback = report.feedback()
        failed = [g.name for g in report.gates if not g.ok]
        on_progress(f"  rejected by gates: {failed}")
        outcome.attempts.append(
            Attempt(number, "gates", False, feedback[:800], gates=_gate_dicts(report))
        )

    return outcome
