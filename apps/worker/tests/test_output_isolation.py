"""Hidden-test transcripts must never reach a candidate.

pytest's console output contains the hidden tests' source lines, inputs and expected
values. These tests pin the three properties that keep it away from candidates.
"""

from __future__ import annotations

from oa_engine.execution import ExecutionResult, ExecutionStatus
from oa_worker.sandbox.orchestrator import _candidate_safe_errors

PYTEST_FAILURE = """\
____ test_longest_streak[events3-1] ____
../grading/test_longest_streak.py:18: in test_longest_streak
    assert longest_streak(events) == expected
E   AssertionError: assert 3 == 1
E    +  where 3 = longest_streak(['a', 'b', 'a'])
"""


def test_grader_output_is_never_serialised() -> None:
    result = ExecutionResult(
        submission_id="s", status=ExecutionStatus.FAILED, grader_output=PYTEST_FAILURE
    )
    assert result.grader_output == PYTEST_FAILURE  # trusted in-process callers can read it
    for dumped in (result.model_dump(), result.model_dump(mode="json")):
        assert "grader_output" not in dumped
    assert "longest_streak" not in result.model_dump_json()  # nor via JSON


def test_assertion_details_are_not_in_the_safe_error_allowlist() -> None:
    # AssertionError lines carry expected values; they must not be selected.
    assert _candidate_safe_errors(PYTEST_FAILURE) == ""


def test_errors_about_the_candidates_own_code_are_kept() -> None:
    text = (
        "E   ImportError: cannot import name 'topWords' from 'solution'\n"
        "E   SyntaxError: invalid syntax\n"
        "E   ImportError: cannot import name 'topWords' from 'solution'\n"  # duplicate
        "E   AssertionError: assert 3 == 1\n"
    )
    assert _candidate_safe_errors(text) == (
        "ImportError: cannot import name 'topWords' from 'solution'\nSyntaxError: invalid syntax"
    )


def test_the_allowlist_is_capped() -> None:
    text = "".join(f"E   NameError: name 'x{i}' is not defined\n" for i in range(10))
    assert len(_candidate_safe_errors(text).splitlines()) == 3
