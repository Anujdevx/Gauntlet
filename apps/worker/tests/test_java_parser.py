"""The Java grader emits the pytest report shape; the parser must map it faithfully."""

from __future__ import annotations

from oa_engine.execution import ExecutionStatus
from oa_engine.execution.payload import SubmissionPayload
from oa_engine.platform.config import Language
from oa_worker.parsers.base import get_parser
from oa_worker.parsers.java_parser import JavaParser


def parse(document: dict | None, stderr: str = "") -> object:
    payload = SubmissionPayload(submission_id="s", language="java")
    return JavaParser().parse(
        payload=payload,
        result_document=document,
        stdout="",
        stderr=stderr,
        exit_code=0,
        runtime_ms=1,
        stdout_truncated=False,
        stderr_truncated=False,
        submission_id="s",
    )


def test_registry_returns_the_java_parser() -> None:
    assert isinstance(get_parser(Language.JAVA), JavaParser)


def test_passed_failed_and_parameterised_cases() -> None:
    result = parse(
        {
            "tests": [
                {"nodeid": "SolutionTest::a", "outcome": "passed", "duration": 0.01},
                {"nodeid": "SolutionTest::b [1] x, 2", "outcome": "passed", "duration": 0.0},
                {
                    "nodeid": "SolutionTest::c",
                    "outcome": "failed",
                    "duration": 0.02,
                    "longrepr": "Assertion failed. Hidden tests do not reveal expected values.",
                },
            ],
            "collectors": [],
            "summary": {"total": 3},
        }
    )
    assert result.status is ExecutionStatus.FAILED  # type: ignore[attr-defined]
    assert (result.score.passed, result.score.total) == (2, 3)  # type: ignore[attr-defined]
    names = [t.name for t in result.tests]  # type: ignore[attr-defined]
    assert names == ["a", "b [1] x, 2", "c"]


def test_a_compile_error_is_a_candidate_failure_with_the_compiler_output() -> None:
    result = parse(
        {
            "tests": [],
            "collectors": [
                {"nodeid": "compilation", "outcome": "failed", "longrepr": "Solution.java:3: error"}
            ],
        }
    )
    # A compile error is the candidate's problem: FAILED, counted, with javac's output.
    assert result.status is ExecutionStatus.FAILED  # type: ignore[attr-defined]
    assert result.counts_against_candidate is True  # type: ignore[attr-defined]
    assert [t.name for t in result.tests] == ["compilation"]  # type: ignore[attr-defined]
    assert "Solution.java:3" in (result.tests[0].message or "")  # type: ignore[attr-defined]


def test_missing_document_is_an_error_not_a_pass() -> None:
    result = parse(None, stderr="java.lang.OutOfMemoryError")
    assert result.status is ExecutionStatus.ERROR  # type: ignore[attr-defined]
