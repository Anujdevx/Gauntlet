"""What a candidate may be told about a failing hidden test.

Failure text from pytest, Vitest and the SQL harness quotes the hidden test: its source
line, its input, its expected value. The policy keeps those out of candidate-facing
results while leaving real debugging information about the candidate's own code.
"""

from __future__ import annotations

import pytest

from oa_worker.parsers.base import ASSERTION_NOTICE, candidate_message


@pytest.mark.parametrize(
    "raw",
    [
        # pytest, short traceback form
        "../grading/test_x.py:18: in test_x\n    assert f(events) == expected\n"
        "E   AssertionError: assert 3 == 1\nE    +  where 3 = f(['a', 'b', 'a'])",
        # pytest reprcrash form
        "AssertionError: assert [1, 2] == [2, 1]",
        # Vitest
        "AssertionError: expected 'a' to be 'b' // Object.is equality",
        # JUnit
        "org.opentest4j.AssertionFailedError: expected: <99> but was: <2>",
        # SQL harness
        "AssertionError: query result mismatch\n  expected (2 rows):\n    ('ada', 400)",
    ],
)
def test_assertion_failures_never_reveal_expected_values(raw: str) -> None:
    out = candidate_message(raw)
    assert out == ASSERTION_NOTICE
    for secret in ("99", "ada", "400", "'a'", "[2, 1]", "events", "mismatch"):
        assert secret not in (out or "")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("E   NameError: name 'helper' is not defined", "NameError: name 'helper' is not defined"),
        ("TypeError: unsupported operand type(s) for +: 'int' and 'str'", None),
        ("E   IndexError: list index out of range", "IndexError: list index out of range"),
        (
            "sqlite3.OperationalError: no such column: totl",
            "OperationalError: no such column: totl",
        ),
        ("E   ImportError: cannot import name 'f' from 'solution'", None),
    ],
)
def test_errors_about_the_candidates_own_code_are_shown(raw: str, expected: str | None) -> None:
    out = candidate_message(raw)
    assert out is not None
    assert out.split(":")[0] == (expected or raw.replace("E   ", "")).split(":")[0].split(".")[-1]


@pytest.mark.parametrize(
    "raw",
    ["E   KeyError: 'secret-input-42'", "ValueError: invalid literal for int(): 'hidden'"],
)
def test_data_bearing_exceptions_show_only_their_type(raw: str) -> None:
    out = candidate_message(raw)
    assert out is not None and out.endswith("(details withheld)")
    assert "secret-input-42" not in out and "hidden" not in out


@pytest.mark.parametrize(
    "raw",
    [
        "Failed: Timeout >10.0s",
        "java.util.concurrent.TimeoutException: test() timed out after 3 seconds",
    ],
)
def test_timeouts_are_reported_plainly(raw: str) -> None:
    assert candidate_message(raw) == "Timed out: a test did not finish within the time limit."


@pytest.mark.parametrize("raw", [None, "", "   \n", "no exception line here, just prose"])
def test_nothing_recognisable_means_no_message(raw: str | None) -> None:
    assert candidate_message(raw) is None
