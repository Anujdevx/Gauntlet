"""Adapter for ``vitest --reporter=json``.

Vitest's JSON reporter emits, per test file, ``assertionResults`` with
``ancestorTitles``, ``title``, ``status`` (``passed``/``failed``/``pending``/``todo``)
and ``failureMessages``, plus ``duration`` in milliseconds. Top-level
``numTotalTests``, ``numPassedTests`` etc. are summary counters; we use them only
for logging, never to compute the verdict, because they disagree with the
per-test array whenever a hook or a reporter throws.

The full ``title`` is reconstructed as ``describe > it > should work`` because
that is what a human needs to locate the failing assertion in a large suite.
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
)

if TYPE_CHECKING:  # pragma: no cover
    from oa_engine.execution.payload import SubmissionPayload

__all__ = ["VitestParser"]

log = get_logger(__name__, service="oa-worker")

_COLLECTION_ERROR_ID = "vitest.collection_error"

#: Vitest statuses that mean "ran and passed". Anything else is not a pass.
_PASSING_STATUSES = frozenset({"passed"})

#: Statuses Vitest reports for a test that was never really executed.
_SKIPPED_STATUSES = frozenset({"pending", "todo", "skipped"})


class VitestParser:
    """Normalise Vitest JSON reporter output."""

    language = Language.REACT

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
        """Build an :class:`ExecutionResult` from the Vitest report document.

        Args:
            result_document: Parsed Vitest JSON, or ``None`` when the reporter
                never wrote one (compile error, unresolvable import, or a crash
                in a global setup file).
            stdout: Combined stream output, already capped and sanitised.
            stderr: Likewise. Carries esbuild/babel diagnostics, which is the
                actionable content for a React submission that fails to compile.
            exit_code: The grader's exit code, recorded but not graded on.
            runtime_ms: Grading-phase wall clock.
            stdout_truncated: Whether the stdout cap was hit.
            stderr_truncated: Whether the stderr cap was hit.
            submission_id: Echoed onto the result for correlation.

        Returns:
            The unified result envelope.
        """
        if not result_document:
            cases = [
                _collection_error_case(
                    test_id=_COLLECTION_ERROR_ID,
                    name="Test suite compiled and ran",
                    message=stderr,
                    stderr=stderr,
                    exit_code=exit_code,
                )
            ]
            return self._envelope(
                payload=payload,
                tests=cases,
                status_override=ExecutionStatus.ERROR,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                runtime_ms=runtime_ms,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )

        raw_suites = result_document.get("testResults")
        cases = []
        if isinstance(raw_suites, list):
            for suite in raw_suites:
                if not isinstance(suite, dict):
                    continue
                file_path = str(suite.get("name") or suite.get("testFilePath") or "unknown")
                assertions = suite.get("assertionResults")
                if isinstance(assertions, list):
                    for entry in assertions:
                        if isinstance(entry, dict):
                            cases.append(self._to_case(entry, file_path=file_path))
                elif suite.get("status") == "failed":
                    # The file failed without producing assertions: a module-level
                    # throw or an unhandled rejection in setup.
                    cases.append(
                        _collection_error_case(
                            test_id=f"{file_path}::{_COLLECTION_ERROR_ID}",
                            name=f"{file_path} (suite failed to run)",
                            message=str(suite.get("message") or ""),
                            stderr=stderr,
                            exit_code=exit_code,
                        )
                    )

        self._log_summary_mismatch(result_document=result_document, tests=cases)

        return self._envelope(
            payload=payload,
            tests=cases,
            status_override=None,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            runtime_ms=runtime_ms,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    # -- internals ----------------------------------------------------------

    def _to_case(self, entry: dict[str, Any], *, file_path: str) -> TestCaseResult:
        """Map one Vitest assertion onto a :class:`TestCaseResult`.

        A stable id is composed from the file path and the full title so the same
        assertion keeps its identity across runs. That stability is what lets
        the skill engine attribute a failure to a specific concept node over
        time rather than to "some React question".
        """
        ancestors = [str(a) for a in (entry.get("ancestorTitles") or []) if a]
        title = str(entry.get("title") or "anonymous")
        full_title = " > ".join([*ancestors, title])
        status = str(entry.get("status") or "")
        failure_messages = entry.get("failureMessages")
        message: str | None = None
        if isinstance(failure_messages, list) and failure_messages:
            message = "\n\n".join(str(m) for m in failure_messages[:4])
        elif isinstance(failure_messages, str):
            message = failure_messages

        return TestCaseResult(
            id=f"{file_path}::{full_title}",
            name=full_title[:512],
            passed=status in _PASSING_STATUSES,
            duration_ms=float(entry.get("duration") or 0.0),
            message=_clean(message, limit=_MAX_MESSAGE_CHARS),
            stack_trace=None,
            skipped=status in _SKIPPED_STATUSES,
        )

    @staticmethod
    def _log_summary_mismatch(
        *, result_document: dict[str, Any], tests: list[TestCaseResult]
    ) -> None:
        """Warn when Vitest's counters disagree with its own assertion array.

         Disagreement means a reporter or hook threw, which is worth an alert. It
         does *not* change the verdict: the per-assertion array is the finer
        -grained, more trustworthy source, and trusting the coarse counter would
         let a single crashing suite erase the evidence for every other suite.
        """
        reported = result_document.get("numTotalTests")
        if not isinstance(reported, int):
            return
        observed = sum(1 for t in tests if not t.skipped)
        if observed != reported:
            log.warning(
                "vitest_summary_mismatch",
                reported_total=reported,
                observed_total=observed,
                num_passed=result_document.get("numPassedTests"),
                num_failed=result_document.get("numFailedTests"),
            )

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
