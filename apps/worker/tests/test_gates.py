"""Each gate must reject exactly what it claims to, and accept a good question."""

from __future__ import annotations

from conftest import BUGGY, REF, START, TOTAL, FakeSandbox, make_result

from oa_engine.execution import ExecutionStatus
from oa_engine.generation import Question
from oa_worker.generation.gates import run_gates, strip_fences

GOOD_BLIND = "def longest(xs):\n    return 'blind'\n"


def solve_with(sandbox: FakeSandbox, *, passes: bool) -> object:
    sandbox.when(GOOD_BLIND, make_result(TOTAL if passes else 3))
    return lambda: GOOD_BLIND


def test_good_question_passes_every_gate(question: Question, sandbox: FakeSandbox) -> None:
    report = run_gates(question, sandbox, solve_with(sandbox, passes=True))  # type: ignore[arg-type]
    assert report.ok, report.feedback()
    assert [g.name for g in report.gates] == ["reference", "starter", "mutants", "blind"]
    assert report.metrics["mutants_killed"] == 3
    assert report.metrics["blind_passes"] == 2


def test_failing_reference_stops_early_and_says_so(
    question: Question, sandbox: FakeSandbox
) -> None:
    sandbox.when(REF, make_result(4))
    report = run_gates(question, sandbox, lambda: "unused")
    assert not report.ok
    assert "does NOT pass your tests" in report.feedback()
    assert len(sandbox.runs) == 1  # nothing else was spent


def test_too_few_tests_is_rejected(question: Question, sandbox: FakeSandbox) -> None:
    sandbox.when(REF, make_result(3, 3))
    assert "at least 6" in run_gates(question, sandbox, lambda: "").feedback()


def test_skipped_tests_are_not_a_pass(question: Question, sandbox: FakeSandbox) -> None:
    sandbox.when(REF, make_result(TOTAL - 1, TOTAL, skipped=1))
    assert not run_gates(question, sandbox, lambda: "").ok


def test_flaky_reference_is_rejected(question: Question, sandbox: FakeSandbox) -> None:
    results = iter(
        [make_result(TOTAL), make_result(TOTAL, ids=[f"other{i}" for i in range(TOTAL)])]
    )
    sandbox.run = lambda **_: next(results)  # type: ignore[method-assign]
    assert "flaky" in run_gates(question, sandbox, lambda: "").feedback()


def test_infra_error_on_reference_is_not_a_pass(question: Question, sandbox: FakeSandbox) -> None:
    sandbox.when(REF, make_result(0, 0, status=ExecutionStatus.INFRA_ERROR, stderr="docker down"))
    assert not run_gates(question, sandbox, lambda: "").ok


def test_starter_that_already_passes_tests_is_rejected(
    question: Question, sandbox: FakeSandbox
) -> None:
    sandbox.when(START, make_result(2))
    feedback = run_gates(question, sandbox, lambda: "").feedback()
    assert "already PASS on the unmodified starter" in feedback


def test_starter_that_cannot_import_is_rejected(question: Question, sandbox: FakeSandbox) -> None:
    sandbox.when(START, make_result(0, 0, stderr="ImportError"))
    assert "does not import/collect cleanly" in run_gates(question, sandbox, lambda: "").feedback()


def test_surviving_mutant_means_weak_tests(question: Question, sandbox: FakeSandbox) -> None:
    sandbox.when(BUGGY[1], make_result(TOTAL))
    report = run_gates(question, sandbox, lambda: "")
    assert "PASSES all your tests" in report.feedback()
    assert BUGGY[1].strip().splitlines()[0] in report.feedback()  # the model sees the code
    assert report.metrics["mutants_survived"] == 1


def test_mutant_that_does_not_import_is_a_fake_kill(
    question: Question, sandbox: FakeSandbox
) -> None:
    sandbox.when(BUGGY[0], make_result(0, 0, stderr="SyntaxError"))
    report = run_gates(question, sandbox, lambda: "")
    assert "must be wrong in behaviour" in report.feedback()
    assert report.metrics["mutants_killed"] == 2


def test_blind_solver_failure_feeds_back_its_solution(
    question: Question, sandbox: FakeSandbox
) -> None:
    report = run_gates(question, sandbox, solve_with(sandbox, passes=False))  # type: ignore[arg-type]
    assert not report.ok
    assert GOOD_BLIND.strip() in report.feedback()
    assert "prompt is ambiguous" in report.feedback()
    assert report.metrics["blind_passes"] == 0


def test_blind_gate_does_not_run_when_earlier_gates_fail(
    question: Question, sandbox: FakeSandbox
) -> None:
    called: list[int] = []
    sandbox.when(BUGGY[2], make_result(TOTAL))
    run_gates(question, sandbox, lambda: called.append(1) or "")  # type: ignore[func-returns-value]
    assert called == []  # LLM quota is only spent on questions that are otherwise clean


def test_strip_fences() -> None:
    assert strip_fences("```python\nx = 1\n```") == "x = 1\n"
    assert strip_fences("x = 1") == "x = 1\n"


def test_one_possibly_equivalent_survivor_is_tolerated_and_recorded(
    question: Question, sandbox: FakeSandbox
) -> None:
    import dataclasses

    extra = "def longest(xs):\n    return 99\n"
    sandbox.when(extra, make_result(2))
    q = dataclasses.replace(question, buggy_files=[*question.buggy_files, {"solution.py": extra}])
    sandbox.when(BUGGY[1], make_result(TOTAL))  # one survivor, 3 killed
    report = run_gates(q, sandbox, solve_with(sandbox, passes=True))  # type: ignore[arg-type]
    assert report.ok
    assert report.metrics["mutants_survived"] == 1
    assert report.metrics["mutant_survivor_notes"]


def test_two_survivors_are_not_tolerated(question: Question, sandbox: FakeSandbox) -> None:
    import dataclasses

    extra = "def longest(xs):\n    return 99\n"
    sandbox.when(extra, make_result(2))
    q = dataclasses.replace(question, buggy_files=[*question.buggy_files, {"solution.py": extra}])
    sandbox.when(BUGGY[0], make_result(TOTAL))
    sandbox.when(BUGGY[1], make_result(TOTAL))
    assert not run_gates(q, sandbox, lambda: "").ok


def test_a_too_slow_mutant_ended_by_the_sandbox_counts_as_killed(
    question: Question, sandbox: FakeSandbox
) -> None:
    sandbox.when(
        BUGGY[0], make_result(0, 0, status=ExecutionStatus.TIMEOUT, stderr="wall clock exceeded")
    )
    report = run_gates(question, sandbox, lambda: "")
    assert report.metrics["mutants_killed"] == 3
    assert report.metrics["mutants_invalid"] == 0
