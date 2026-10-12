# ruff: noqa: E501  (test fixtures quote real runner output verbatim)
"""End to end through each parser: a failing hidden test exposes no hidden data."""

from __future__ import annotations

from typing import Any

from oa_engine.execution.payload import SubmissionPayload
from oa_engine.platform.config import Language
from oa_worker.parsers.base import get_parser

SECRETS = ("== 1", "['a', 'b', 'a']", "test_longest", "../grading", "'hidden-answer'", "to be")


def parse(language: Language, document: dict[str, Any]) -> Any:
    payload = SubmissionPayload(submission_id="s", language=language.value)
    return get_parser(language).parse(
        payload=payload,
        result_document=document,
        stdout="",
        stderr="",
        exit_code=1,
        runtime_ms=1,
        stdout_truncated=False,
        stderr_truncated=False,
        submission_id="s",
    )


def assert_clean(result: Any) -> None:
    blob = " ".join(f"{t.message} {t.stack_trace}" for t in result.tests)
    for secret in SECRETS:
        assert secret not in blob, f"{secret!r} leaked: {blob[:300]}"
    assert all(t.stack_trace is None for t in result.tests)


def test_pytest_report() -> None:
    result = parse(
        Language.PYTHON,
        {
            "tests": [
                {
                    "nodeid": "grading/test_longest.py::test_a",
                    "outcome": "failed",
                    "longrepr": "../grading/test_longest.py:18: in test_a\n    assert f(x) == expected\n"
                    "E   AssertionError: assert 3 == 1\nE    +  where 3 = f(['a', 'b', 'a'])",
                },
            ],
            "collectors": [
                {
                    "nodeid": "grading/test_longest.py",
                    "outcome": "failed",
                    "longrepr": "ImportError while importing test module '/tmp/grading/test_longest.py'.\n"
                    "../grading/test_longest.py:3: in <module>\n    from solution import f\n"
                    "E   ImportError: cannot import name 'f' from 'solution'",
                }
            ],
        },
    )
    assert_clean(result)
    assert any("ImportError" in (t.message or "") for t in result.tests)  # still actionable


def test_vitest_report() -> None:
    result = parse(
        Language.REACT,
        {
            "testResults": [
                {
                    "name": "/tmp/oa-react/x.test.ts",
                    "status": "failed",
                    "assertionResults": [
                        {
                            "title": "t",
                            "status": "failed",
                            "failureMessages": [
                                "AssertionError: expected 'x' to be 'hidden-answer' // Object.is equality\n"
                                "    at /tmp/oa-react/x.test.ts:12:5"
                            ],
                        }
                    ],
                }
            ]
        },
    )
    assert_clean(result)


def test_sql_report() -> None:
    result = parse(
        Language.SQL,
        {
            "tests": [
                {
                    "nodeid": "t.py::Scenario0::test_a",
                    "outcome": "failed",
                    "longrepr": "AssertionError: query result mismatch\n  expected (1 rows):\n"
                    "    ('hidden-answer', 1)",
                }
            ]
        },
    )
    assert_clean(result)


def test_runtime_errors_in_candidate_code_are_visible_but_assertions_are_not() -> None:
    result = parse(
        Language.PYTHON,
        {
            "tests": [
                {
                    "nodeid": "g/t.py::test_a",
                    "outcome": "failed",
                    "call": {"crash": {"message": "NameError: name 'helper' is not defined"}},
                },
                {
                    "nodeid": "g/t.py::test_b",
                    "outcome": "failed",
                    "call": {
                        "crash": {"message": "AssertionError: assert 3 == 1\n + where 3 = f(['a'])"}
                    },
                },
            ]
        },
    )
    by_name = {t.name: t.message for t in result.tests}
    assert by_name["test_a"] == "NameError: name 'helper' is not defined"
    assert by_name["test_b"] == "Assertion failed. Hidden tests do not reveal expected values."
    assert_clean(result)


def test_sql_runtime_errors_in_the_candidates_query_are_visible() -> None:
    result = parse(
        Language.SQL,
        {
            "tests": [
                {
                    "nodeid": "t.py::Scenario0::test_a",
                    "outcome": "failed",
                    "call": {
                        "crash": {"message": "sqlite3.OperationalError: no such column: totl"}
                    },
                }
            ]
        },
    )
    assert result.tests[0].message == "OperationalError: no such column: totl"
