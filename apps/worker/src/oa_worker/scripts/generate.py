"""CLI for the question bank.

    python -m oa_worker.scripts.generate generate --node python.iterators --count 3
    python -m oa_worker.scripts.generate validate-existing

Runs inside the worker container, because only the worker holds the Docker socket
and only the worker is given ``GEMINI_API_KEY``. Progress goes to stderr; a JSON
report goes to stdout so it can be piped or saved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import select

from oa_api.models.db import dispose_engine, session_scope
from oa_api.models.tables import ConceptNode, QuestionTemplate, QuestionTemplateNode
from oa_engine.generation import Question, resolve_language
from oa_engine.llm import GeminiClient, LLMError
from oa_engine.platform.config import get_settings
from oa_worker.generation.gates import GateReport, run_core_gates
from oa_worker.generation.pipeline import Outcome, _judge_relevance, generate_question
from oa_worker.generation.sandbox import DockerSandbox
from oa_worker.generation.store import existing_titles, save_question

SUPPORTED = ("python", "sql", "java")

#: One loop for every database call. The async engine pools connections bound to the
#: loop that opened them, so a fresh ``asyncio.run`` per call leaves connections
#: pointing at a dead loop and crashes noisily on teardown.
_LOOP = asyncio.new_event_loop()


def _run(coro: Any) -> Any:
    return _LOOP.run_until_complete(coro)


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)  # noqa: T201 - CLI progress


async def _node(node_id: str) -> ConceptNode | None:
    async with session_scope() as session:
        return await session.scalar(select(ConceptNode).where(ConceptNode.id == node_id))


async def _titles(language: str, scope_node: str | None) -> list[str]:
    async with session_scope() as session:
        return await existing_titles(session, language, scope_node=scope_node)


async def _save(outcome: Outcome) -> str | None:
    assert outcome.question is not None  # noqa: S101 - caller checks `accepted`
    async with session_scope() as session:
        return await save_question(
            session,
            question=outcome.question,
            node_id=outcome.node,
            difficulty=outcome.difficulty,
            reference_runtime_ms=outcome.metrics.get("reference_runtime_ms"),
        )


def _summarise(outcome: Outcome, slug: str | None, *, show: bool) -> dict[str, Any]:
    q = outcome.question
    full = (
        {
            "prompt": q.prompt,
            "starter_files": q.starter_files,
            "reference_files": q.reference_files,
            "hidden_tests": q.hidden_tests,
            "buggy_files": q.buggy_files,
        }
        if show and q
        else {}
    )
    return {
        **full,
        "node": outcome.node,
        "difficulty": outcome.difficulty,
        "accepted": outcome.accepted,
        "slug": slug,
        "title": outcome.question.title if outcome.question else None,
        "metrics": outcome.metrics,
        "aborted": outcome.aborted,
        "attempts": [
            {"n": a.number, "stage": a.stage, "ok": a.ok, "detail": a.detail, "gates": a.gates}
            for a in outcome.attempts
        ],
    }


def cmd_generate(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value():
        _say("GEMINI_API_KEY is not set. Add it to .env and pass it to the worker.")
        return 2
    node = _run(_node(args.node))
    if node is None:
        _say(f"unknown concept node {args.node!r}; run `make seed` first")
        return 2
    try:
        language = resolve_language(node.id, args.language)
    except ValueError as exc:
        _say(str(exc))
        return 2

    llm = GeminiClient(api_key=settings.gemini_api_key, model=settings.gemini_model)
    sandbox = DockerSandbox(settings)
    scope = node.domain if node.domain == "dsa" else None
    titles = _run(_titles(language, scope))
    results: list[dict[str, Any]] = []

    for index in range(args.count):
        _say(f"\n=== {index + 1}/{args.count}: {node.id} ({language}, {args.difficulty}) ===")
        outcome = generate_question(
            llm=llm,
            sandbox=sandbox,
            language=language,
            node_id=node.id,
            node_label=node.label,
            node_description=node.description or "",
            difficulty=args.difficulty,
            existing_titles=titles,
            max_attempts=args.max_attempts,
            blind_runs=args.blind_runs,
            blind_required=args.blind_required,
            on_progress=_say,
        )
        slug = None
        if outcome.accepted and outcome.question is not None:
            titles.append(outcome.question.title)
            if args.dry_run:
                _say("  (dry run: not saved)")
            else:
                slug = _run(_save(outcome))
                _say(f"  saved as {slug}" if slug else "  skipped: identical tests already exist")
        results.append(_summarise(outcome, slug, show=args.show))
        if outcome.aborted:
            _say(f"  ABORTED: {outcome.aborted}")
            break

    accepted = sum(1 for r in results if r["accepted"])
    report = {"requested": args.count, "accepted": accepted, "results": results}
    print(json.dumps(report, indent=2))  # noqa: T201 - machine-readable report
    return 0 if accepted else 1


def _as_question(row: QuestionTemplate) -> Question:
    return Question(
        language=row.language,
        title=row.title,
        prompt=row.summary,
        starter_files=dict(row.starter_files),
        reference_files=dict(row.reference_solution),
        hidden_tests=dict(row.hidden_tests),
        buggy_files=[],
        run_command=tuple(row.run_command),
        solution_path=next(iter(row.starter_files), "solution.py"),
    )


def cmd_validate_existing(_args: argparse.Namespace) -> int:
    """Run the reference/starter gates on every active template in the bank."""

    async def load() -> list[QuestionTemplate]:
        async with session_scope() as session:
            rows = await session.scalars(
                select(QuestionTemplate).where(QuestionTemplate.is_active.is_(True))
            )
            return list(rows)

    sandbox = DockerSandbox(get_settings())
    failures = 0
    rows = _run(load())
    for row in rows:
        if row.language not in {*SUPPORTED, "react"}:  # react: validated, not generated
            _say(f"- {row.slug}: skipped ({row.language} gates not implemented)")
            continue
        report = GateReport()
        run_core_gates(_as_question(row), sandbox, report)
        status = "OK  " if report.ok else "FAIL"
        failures += 0 if report.ok else 1
        _say(f"{status} {row.slug}")
        for gate in report.gates:
            _say(f"       {'pass' if gate.ok else 'FAIL'} {gate.name}: {gate.detail[:300]}")
    return 1 if failures else 0


def cmd_audit_relevance(args: argparse.Namespace) -> int:
    """Ask the relevance judge whether each question tests the skill it is filed under.

    Each template is judged against its deepest linked concept node. Without ``--apply``
    this only reports; with it, mismatches are deactivated (``is_active = false``), which
    hides them from candidates without deleting anything.
    """
    settings = get_settings()
    if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value():
        _say("GEMINI_API_KEY is not set.")
        return 2
    llm = GeminiClient(api_key=settings.gemini_api_key, model=settings.gemini_model)

    async def load() -> list[tuple[QuestionTemplate, ConceptNode]]:
        async with session_scope() as session:
            rows = await session.execute(
                select(QuestionTemplate, ConceptNode)
                .join(QuestionTemplateNode, QuestionTemplateNode.template_id == QuestionTemplate.id)
                .join(ConceptNode, ConceptNode.id == QuestionTemplateNode.node_id)
                .where(QuestionTemplate.is_active.is_(True), ConceptNode.id.like(f"{args.scope}%"))
            )
            deepest: dict[str, tuple[QuestionTemplate, ConceptNode]] = {}
            for template, node in rows.all():
                current = deepest.get(template.slug)
                if current is None or node.depth > current[1].depth:
                    deepest[template.slug] = (template, node)
            return list(deepest.values())

    mismatches: list[str] = []
    for template, node in _run(load()):
        question = _as_question(template)
        verdict = _judge_relevance(
            llm,
            node_id=node.id,
            node_label=node.label,
            node_description=node.description or "",
            question=question,
        )
        if verdict is None:
            _say(f"?    {template.slug}: judge gave no usable answer")
        elif verdict.assesses_target_skill:
            _say(f"OK   {template.slug}  [{node.id}] technique: {verdict.primary_technique}")
        else:
            mismatches.append(template.slug)
            _say(f"MISMATCH {template.slug}  [{node.id}]")
            _say(f"       technique: {verdict.primary_technique}; {verdict.reason}")

    if args.apply and mismatches:

        async def deactivate() -> None:
            async with session_scope() as session:
                for slug in mismatches:
                    row = await session.scalar(
                        select(QuestionTemplate).where(QuestionTemplate.slug == slug)
                    )
                    if row is not None:
                        row.is_active = False

        _run(deactivate())
        _say(f"deactivated {len(mismatches)} mismatched question(s)")
    return 1 if mismatches else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="author new questions with the LLM")
    gen.add_argument("--node", required=True, help="concept node id, e.g. sql.joins.outer")
    gen.add_argument(
        "--language",
        choices=["python", "java"],
        help="language for DSA nodes (default python); other nodes fix their own language",
    )
    gen.add_argument("--difficulty", choices=["easy", "medium", "hard"], default="medium")
    gen.add_argument("--count", type=int, default=1)
    gen.add_argument("--max-attempts", type=int, default=3)
    gen.add_argument("--blind-runs", type=int, default=2)
    gen.add_argument("--blind-required", type=int, default=1)
    gen.add_argument("--dry-run", action="store_true", help="validate but do not save")
    gen.add_argument("--show", action="store_true", help="include the full question in the report")
    gen.set_defaults(func=cmd_generate)

    val = sub.add_parser("validate-existing", help="re-check every template in the bank")
    val.set_defaults(func=cmd_validate_existing)

    aud = sub.add_parser("audit-relevance", help="judge whether questions test their skill")
    aud.add_argument("--scope", default="", help="only nodes whose id starts with this, e.g. dsa")
    aud.add_argument("--apply", action="store_true", help="deactivate mismatched questions")
    aud.set_defaults(func=cmd_audit_relevance)

    args = parser.parse_args()
    try:
        return int(args.func(args))
    except LLMError as exc:
        _say(f"LLM error: {exc}")
        return 2
    finally:
        _run(dispose_engine())
        _LOOP.close()


if __name__ == "__main__":
    raise SystemExit(main())
