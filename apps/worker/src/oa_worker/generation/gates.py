"""The sandbox gates a generated question must pass before it is trusted.

Each gate answers one question about the LLM's work, using only deterministic
evidence from real execution:

* **reference** -- does the author's own solution pass its own tests, every time?
* **starter** -- do the tests fail on the code we hand the candidate?
* **mutants** -- does every deliberately wrong solution get caught?
* **blind** -- can an independent solver, who never sees the tests, pass them?

The last one is the strongest: it is what catches ambiguous prompts and tests that
over-fit the author's own implementation.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from oa_engine.execution import ExecutionResult, ExecutionStatus
from oa_engine.generation import Question
from oa_worker.generation.sandbox import Sandbox

__all__ = [
    "GateReport",
    "GateResult",
    "check_blind",
    "check_mutants",
    "run_core_gates",
    "run_gates",
    "strip_fences",
]

MIN_TESTS = {"python": 6, "sql": 3, "java": 6, "react": 2}
MAX_REFERENCE_MS = 8_000
MIN_VALID_MUTANTS = 3
#: A 'wrong' solution that passes may simply be equivalent to the reference (an LLM
#: often writes a LEFT JOIN where an INNER JOIN is identical); no test can kill it.
#: One such survivor is tolerated, and recorded for review, if enough others die.
MAX_SURVIVORS = 1
_FENCE = re.compile(r"^```[a-zA-Z]*\n(.*?)\n```\s*$", re.S)


@dataclass
class GateResult:
    name: str
    ok: bool
    detail: str


@dataclass
class GateReport:
    gates: list[GateResult] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.gates) and all(g.ok for g in self.gates)

    def feedback(self) -> str:
        """Everything that failed, written for the model to act on."""
        return "\n\n".join(f"[{g.name}] {g.detail}" for g in self.gates if not g.ok)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.gates.append(GateResult(name, ok, detail))
        return ok


def _failing(result: ExecutionResult, limit: int = 5) -> str:
    lines = []
    for test in result.tests:
        if not test.passed and not test.skipped:
            message = (test.message or "").strip().splitlines()
            lines.append(f"  - {test.name}: {(message[-1] if message else '')[:300]}")
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def _passed_all(result: ExecutionResult, total: int) -> bool:
    score = result.score
    return (
        result.status is ExecutionStatus.PASSED
        and score.failed == 0
        and score.skipped == 0
        and score.total == total
    )


def strip_fences(text: str) -> str:
    """Models sometimes wrap code in a markdown fence even when told not to."""
    stripped = text.strip()
    match = _FENCE.match(stripped)
    return (match.group(1) if match else stripped) + "\n"


def run_core_gates(question: Question, sandbox: Sandbox, report: GateReport) -> int | None:
    """Reference passes (twice) and starter fails. Returns the test count, or ``None``."""

    def run(files: dict[str, str]) -> ExecutionResult:
        return sandbox.run(
            language=question.language,
            files=files,
            hidden_tests=question.hidden_tests,
            run_command=question.run_command,
        )

    first = run(question.reference_files)
    minimum = MIN_TESTS[question.language]
    if first.status is not ExecutionStatus.PASSED or first.score.failed or first.score.skipped:
        report.add(
            "reference",
            False,
            f"The reference solution does NOT pass your tests (status={first.status.value}). "
            f"Either the solution or the tests are wrong.\n{_failing(first)}\n"
            f"stderr tail: {first.stderr[-500:]}",
        )
        return None
    if first.score.total < minimum:
        report.add(
            "reference",
            False,
            f"Only {first.score.total} tests ran; at least {minimum} are required "
            "(parametrized cases count individually).",
        )
        return None
    if first.runtime_ms > MAX_REFERENCE_MS:
        report.add("reference", False, f"Reference took {first.runtime_ms} ms; too slow.")
        return None

    second = run(question.reference_files)
    if {t.id for t in first.tests if t.passed} != {t.id for t in second.tests if t.passed}:
        report.add("reference", False, "Test results differ between two identical runs (flaky).")
        return None
    total = first.score.total
    report.add("reference", True, f"{total} tests pass, stable across 2 runs")
    report.metrics.update(tests_total=total, reference_runtime_ms=first.runtime_ms)

    starter = run(question.starter_files)
    if starter.score.total != total:
        report.add(
            "starter",
            False,
            "The starter code does not import/collect cleanly, so the tests could not run "
            f"against it (collected {starter.score.total} of {total}). "
            f"stderr tail: {starter.stderr[-400:]}",
        )
    elif starter.score.passed > 0:
        passing = [t.name for t in starter.tests if t.passed][:5]
        report.add(
            "starter",
            False,
            f"{starter.score.passed} tests already PASS on the unmodified starter code "
            f"(e.g. {passing}). Every test must fail until the candidate implements it.",
        )
    else:
        report.add("starter", True, f"all {total} tests fail on the starter")
    report.metrics["starter_passed"] = starter.score.passed
    return total


def check_mutants(question: Question, sandbox: Sandbox, total: int, report: GateReport) -> None:
    killed = survivors = invalid = 0
    notes: list[str] = []
    for index, files in enumerate(question.buggy_files):
        result = sandbox.run(
            language=question.language,
            files=files,
            hidden_tests=question.hidden_tests,
            run_command=question.run_command,
        )
        if result.score.total != total:
            invalid += 1
            notes.append(
                f"buggy solution #{index + 1} does not import/collect cleanly "
                f"(collected {result.score.total} of {total}); it must be wrong in "
                f"behaviour, not broken. stderr: {result.stderr[-200:]}"
            )
        elif _passed_all(result, total):
            survivors += 1
            code = next(iter(files.values())).strip()[:500]
            notes.append(
                f"buggy solution #{index + 1} PASSES all your tests:\n{code}\n"
                "Either add data/tests that expose its bug (a scenario whose expected result "
                "differs for it), or replace it with a solution that is genuinely wrong."
            )
        else:
            killed += 1
    valid = killed + survivors
    ok = invalid == 0 and survivors <= MAX_SURVIVORS and killed >= MIN_VALID_MUTANTS
    if valid < MIN_VALID_MUTANTS and not notes:
        notes.append(f"only {valid} usable wrong solutions; need {MIN_VALID_MUTANTS}+")
    summary = f"killed {killed}/{valid}"
    if survivors and ok:
        summary += f" ({survivors} survivor tolerated as possibly equivalent)"
    report.add("mutants", ok, "\n".join(notes) if (notes and not ok) else summary)
    if survivors and ok:
        report.metrics["mutant_survivor_notes"] = notes
    report.metrics.update(
        mutants_killed=killed, mutants_survived=survivors, mutants_invalid=invalid
    )


def check_blind(
    question: Question,
    sandbox: Sandbox,
    solve: Callable[[], str],
    total: int,
    report: GateReport,
    *,
    runs: int,
    required: int,
) -> None:
    passes = 0
    first_failure = ""
    for _ in range(runs):
        solution = strip_fences(solve())
        result = sandbox.run(
            language=question.language,
            files={question.solution_path: solution},
            hidden_tests=question.hidden_tests,
            run_command=question.run_command,
        )
        if _passed_all(result, total):
            passes += 1
        elif not first_failure:
            first_failure = (
                "An independent engineer who saw ONLY your prompt and starter code wrote:\n"
                f"{solution[:1500]}\n...and failed these tests:\n{_failing(result)}\n"
                "Decide which is wrong: the prompt is ambiguous or incomplete, or a test is "
                "too strict. Fix the prompt and/or the tests."
            )
    report.metrics.update(blind_passes=passes, blind_runs=runs)
    if first_failure:
        # Kept even when the gate passes (1 of 2), so a reviewer can judge whether the
        # miss was solver noise or a genuinely ambiguous prompt.
        report.metrics["blind_failure"] = first_failure[:1500]
    report.add(
        "blind",
        passes >= required,
        f"{passes}/{runs} blind solves passed" if passes >= required else first_failure,
    )


def run_gates(
    question: Question,
    sandbox: Sandbox,
    solve: Callable[[], str],
    *,
    blind_runs: int = 2,
    blind_required: int = 1,
) -> GateReport:
    """Run every gate. Later gates are skipped when an earlier, cheaper one fails.

    Mutants run even when the starter gate fails, so one retry can fix both; the
    blind solve costs LLM quota, so it only runs once everything else is clean.
    """
    report = GateReport()
    total = run_core_gates(question, sandbox, report)
    if total is None:
        return report
    check_mutants(question, sandbox, total, report)
    if report.ok:
        check_blind(
            question, sandbox, solve, total, report, runs=blind_runs, required=blind_required
        )
    return report
