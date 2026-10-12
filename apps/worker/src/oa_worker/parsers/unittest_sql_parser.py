"""Adapter for the SQL grading harness.

SQL questions are graded by a ``unittest.TestCase`` running against
``sqlite3.connect(":memory:")``. Each test method gets a fresh in-memory
database from ``setUp``, which is what makes SQL grading deterministic: no
state leaks between tests, so a candidate's ``ORDER BY`` omission on test 4
cannot be masked by their ``CREATE TABLE`` on test 2.

That harness runs *inside the sandbox* and writes its verdicts as a JSON
document matching the ``pytest-json-report`` shape, so this adapter shares
almost all of its logic with :mod:`oa_worker.parsers.pytest_parser` and only
adds the SQL-specific failure mode: a query that is syntactically invalid
raises ``sqlite3.OperationalError`` and produces zero assertions for that test.

Two SQL-specific concerns this adapter handles:

``sqlite3.OperationalError: too much SQL for DDL statements`` and friends are
candidate errors, reported as failing tests, not infrastructure failures.
A candidate who tries to drop the table their query reads from has written a
wrong answer, and the system must say so.
A query that never calls ``fetchall`` yields an empty result set. The harness
asserts on the materialised rows, so an un-fetched query fails rather than
silently matching an expected-empty result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from oa_engine.execution import ExecutionResult, ExecutionStatus, TestCaseResult
from oa_engine.platform.config import Language
from oa_engine.platform.logging import get_logger
from oa_worker.parsers.base import (
    _MAX_MESSAGE_CHARS,
    _base_kwargs,
    _clean,
    _collection_error_case,
    _terminal_status,
    candidate_message,
    crash_text,
)

if TYPE_CHECKING:  # pragma: no cover
    from oa_engine.execution.payload import SubmissionPayload

__all__ = ["SqlUnittestParser"]

log = get_logger(__name__, service="oa-worker")

_COLLECTION_ERROR_ID = "sql.harness_error"
_SQL_ERROR_ID = "sql.query_error"


class SqlUnittestParser:
    """Normalise the in-sandbox SQL unittest harness output."""

    language = Language.SQL

    def parse(
        self,
        *,
        payload: SubmissionPayload,
        result_document: dict[str, Any] | None,
        stdout: str,
        stderr: str,
        exit_code: int | None,
        runtime_ms: int,
        stdout_truncated: bool,
        stderr_truncated: bool,
        submission_id: str,
    ) -> ExecutionResult:
        """Build an :class:`ExecutionResult` from the SQL harness document.

        Args:
            result_document: The harness's JSON document, shaped like
                ``pytest-json-report`` for reuse. ``None`` means the harness
                itself failed to start, which is always an orchestrator-side
                problem because the harness is part of the image, not the
                submission.
            stdout: Combined stream output, already capped and sanitised.
            stderr: Likewise; carries ``sqlite3`` tracebacks on failure.
            exit_code: The grader's exit code, recorded but not graded on.
            runtime_ms: Grading-phase wall clock.
            stdout_truncated: Whether the stdout cap was hit.
            stderr_truncated: Whether the stderr cap was hit.
            submission_id: Echoed onto the result for correlation.

        Returns:
            The unified result envelope.
        """
        if not result_document:
            tests = [
                _collection_error_case(
                    test_id=_COLLECTION_ERROR_ID,
                    name="SQL grading harness started",
                    message=(
                        "The SQL grading harness produced no result document. This is a "
                        f"platform fault, not a problem with the submitted query. "
                        f"stderr: {stderr[:500]}"
                    ),
                    stderr=stderr,
                    exit_code=exit_code,
                )
            ]
            # INFRA_ERROR, not ERROR: the harness is ours. Marking it ERROR would
            # record a fabricated skill gap for the candidate.
            return self._envelope(
                payload=payload,
                tests=tests,
                status_override=ExecutionStatus.INFRA_ERROR,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                runtime_ms=runtime_ms,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )

        tests = self._collect_cases(
            result_document=result_document, stderr=stderr, exit_code=exit_code
        )
        return self._envelope(
            payload=payload,
            tests=tests,
            status_override=None,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            runtime_ms=runtime_ms,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    # -- internals ----------------------------------------------------------

    def _collect_cases(
        self,
        *,
        result_document: dict[str, Any],
        stderr: str,
        exit_code: int | None,
    ) -> list[TestCaseResult]:
        """Gather every test case, including the synthetic query-error case."""
        cases: list[TestCaseResult] = []
        raw_tests = result_document.get("tests")
        if isinstance(raw_tests, list):
            for entry in raw_tests:
                if not isinstance(entry, dict):
                    continue
                nodeid = str(entry.get("nodeid") or "sql.unknown")
                outcome = str(entry.get("outcome") or "")
                message = self._message_from(entry)
                cases.append(
                    TestCaseResult(
                        id=nodeid,
                        name=nodeid.rsplit("::", maxsplit=1)[-1] or nodeid,
                        passed=outcome == "passed",
                        duration_ms=float(entry.get("duration") or 0.0),
                        message=_clean(candidate_message(message), limit=_MAX_MESSAGE_CHARS),
                        stack_trace=None,
                        skipped=outcome in {"skipped", "xfailed"},
                    )
                )

        # The harness reports a sqlite3 failure as a runner-level error rather
        # than a per-test outcome, because the exception escapes the candidate's
        # query call. Promote it to an explicit failing case so the candidate
        # sees a syntax error as a failed question, not as an empty result set.
        harness_errors = result_document.get("harness_errors")
        if isinstance(harness_errors, list):
            for index, raw in enumerate(harness_errors):
                detail = raw if isinstance(raw, str) else str((raw or {}).get("message", raw))
                cases.append(
                    TestCaseResult(
                        id=f"{_SQL_ERROR_ID}.{index}",
                        name="Submitted query executes",
                        passed=False,
                        duration_ms=0.0,
                        message=_clean(
                            candidate_message(detail) or stderr, limit=_MAX_MESSAGE_CHARS
                        ),
                    )
                )

        if not cases:
            cases.append(
                _collection_error_case(
                    test_id=_COLLECTION_ERROR_ID,
                    name="SQL grading harness started",
                    message=stderr or f"the harness produced no test cases (exit code {exit_code})",
                    stderr=stderr,
                    exit_code=exit_code,
                )
            )
        return cases

    @staticmethod
    def _message_from(entry: dict[str, Any]) -> str | None:
        """Extract the human-facing failure message from a report entry."""
        longrepr = entry.get("longrepr")
        if isinstance(longrepr, str):
            return longrepr
        if isinstance(longrepr, dict):
            crash = longrepr.get("reprcrash")
            if isinstance(crash, dict):
                return str(crash.get("message") or "")
            return str(longrepr.get("message") or "")
        return crash_text(entry)

    @staticmethod
    def _stack_from(entry: dict[str, Any]) -> str | None:
        """Extract the traceback body from a report entry, if present."""
        longrepr = entry.get("longrepr")
        if not isinstance(longrepr, dict):
            return None
        crash = longrepr.get("reprcrash")
        if isinstance(crash, dict):
            trace = crash.get("reprtraceback")
            if isinstance(trace, list):
                return "\n".join(str(line) for line in trace)
        return None

    @staticmethod
    def _envelope(
        *,
        payload: SubmissionPayload,
        tests: list[TestCaseResult],
        status_override: ExecutionStatus | None,
        stdout: str,
        stderr: str,
        exit_code: int | None,
        runtime_ms: int,
        stdout_truncated: bool,
        stderr_truncated: bool,
    ) -> ExecutionResult:
        """Wrap parsed cases into the result envelope with the right status."""
        return ExecutionResult.from_tests(
            submission_id=payload.submission_id,
            tests=tests,
            status=status_override or _terminal_status(tests=tests, exit_code=exit_code),
            **_base_kwargs(
                payload=payload,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                runtime_ms=runtime_ms,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            ),
        )
