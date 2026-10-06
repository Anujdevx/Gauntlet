"""Adapter for ``pytest --json-report``.

``pytest-json-report`` emits a stable schema: ``tests`` with ``nodeid``,
``outcome``, ``duration``, ``longrepr``, ``setup``/``teardown``. We map that onto
the unified envelope.

The runner is invoked from :file:`sandbox/runners/python/grade.py` with
``--json-report`` and ``--json-report-file``. Note that pytest's *exit code* is
``1`` for test failures but also ``1`` for collection errors and ``2`` for
interrupted runs, so it is never used to derive the verdict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from oa_engine.execution import ExecutionResult, ExecutionStatus, TestCaseResult
from oa_engine.platform.config import Language
from oa_engine.platform.logging import get_logger
from oa_worker.parsers.base import (
    _MAX_MESSAGE_CHARS,
    _MAX_STACK_CHARS,
    _base_kwargs,
    _clean,
    _collection_error_case,
    _terminal_status,
)

if TYPE_CHECKING:  # pragma: no cover
    from oa_engine.execution.payload import SubmissionPayload

__all__ = ["PytestParser"]

log = get_logger(__name__, service="oa-worker")

#: Synthetic ids for non-test outcomes, namespaced so they cannot collide with a
#: real ``nodeid``.
_COLLECTION_ERROR_ID = "pytest.collection_error"
_INTERRUPTED_ID = "pytest.interrupted"


class PytestParser:
    """Normalise ``pytest-json-report`` output."""

    language = Language.PYTHON

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
        """Build an :class:`ExecutionResult` from the pytest report document.

        Args:
            result_document: Parsed ``pytest-json-report`` output, or ``None``
                when the runner never wrote one.
            stdout: Combined run-phase and grading-phase stdout, already capped.
            stderr: Likewise for stderr. Carries the traceback for a collection
                error, which is why a missing document is still actionable.
            exit_code: The grader's exit code. Recorded, never used as a verdict.
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
                    name="Test session collected and ran",
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

        raw_tests = result_document.get("tests")
        cases = []
        if isinstance(raw_tests, list):
            for entry in raw_tests:
                if isinstance(entry, dict):
                    cases.append(self._to_case(entry))

        collectors = result_document.get("collectors")
        if isinstance(collectors, list):
            for entry in collectors:
                if isinstance(entry, dict) and entry.get("outcome") == "failed":
                    cases.append(
                        _collection_error_case(
                            test_id=_COLLECTION_ERROR_ID,
                            name=str(entry.get("nodeid") or "collection"),
                            message=str(entry.get("longrepr") or ""),
                            stderr=stderr,
                            exit_code=exit_code,
                        )
                    )

        # A pytest run interrupted mid-way (SIGTERM from our own wall clock, or
        # a KeyboardInterrupt in the sandbox) reports fewer tests than the
        # question declared. We surface that explicitly rather than letting a
        # partial run look complete.
        summary = result_document.get("summary")
        declared = None
        if isinstance(summary, dict):
            declared = summary.get("total")
        if declared is None:
            declared = _declared_test_count(stderr)
        if isinstance(declared, int) and len(cases) < declared:
            log.warning("pytest_partial_run", collected=len(cases), declared=declared)

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

    def _to_case(self, entry: dict[str, Any]) -> TestCaseResult:
        """Map one pytest report entry onto a :class:`TestCaseResult`.

        ``outcome`` values: ``passed``, ``failed``, ``skipped``, ``error``,
        ``xfailed``, ``xpassed``. Only a literal ``passed`` counts as a pass.
        ``xpassed`` is deliberately *not* a pass: an unexpectedly passing test is
        a signal the candidate's implementation does not behave as the question
        intended, and grading it as correct hides a real misunderstanding.
        """
        outcome = str(entry.get("outcome") or "")
        nodeid = str(entry.get("nodeid") or entry.get("nodeId") or "pytest.unknown")
        longrepr = entry.get("longrepr")
        stack: str | None = None
        message: str | None = None
        if isinstance(longrepr, str):
            message = longrepr
        elif isinstance(longrepr, dict):
            # ``-rA`` form: separate reprcrash message and a reprtraceback list.
            crash = longrepr.get("reprcrash")
            if isinstance(crash, dict):
                message = str(crash.get("message") or "")
                crash_trace = crash.get("reprtraceback")
                if isinstance(crash_trace, list):
                    stack = "\n".join(str(line) for line in crash_trace)
            if not message:
                message = str(longrepr.get("message") or "")

        params: list[str] = []
        raw_calls = entry.get("calls")
        if isinstance(raw_calls, list):
            for call in raw_calls:
                if isinstance(call, dict) and "params" in call:
                    params.append(str(call["params"]))

        return TestCaseResult(
            id=nodeid,
            name=nodeid.rsplit("::", maxsplit=1)[-1] or nodeid,
            passed=outcome == "passed",
            duration_ms=float(entry.get("duration") or 0.0),
            message=_clean(message, limit=_MAX_MESSAGE_CHARS),
            stack_trace=_clean(stack, limit=_MAX_STACK_CHARS),
            skipped=outcome in {"skipped", "xfailed"},
            param_ids=params[:64],
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


def _declared_test_count(stderr: str) -> int | None:
    """Best-effort parse of pytest's ``N passed, M failed`` summary line.

    Used only to detect a partial run for logging. Never to grade: the summary
    line is text the candidate can influence by printing, which is precisely why
    it is not a source of truth anywhere in this module.
    """
    for line in reversed(stderr.strip().splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            digits = [
                int(tok.strip(".,"))
                for tok in line.replace(",", " ").split()
                if tok.strip(".,").isdigit()
            ]
            if digits:
                return sum(digits)
    return None
