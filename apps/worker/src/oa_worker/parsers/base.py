"""Deterministic result parsers.

Each adapter's only job is to translate one test runner's machine-readable output
into :class:`~oa_engine.execution.ExecutionResult`. Three invariants hold across
all of them:

1. **No string matching for verdicts.** The pass/fail bit comes from the
   runner's own ``passed``/``outcome`` field. We never grep stdout for "OK" or
   "0 failed", because that is how a candidate forges a pass by printing the
   right words.
2. **A missing or unparseable document is ``ERROR``, not ``FAILED``.** We could
   not determine the outcome, and reporting "we could not tell" as a failure
   would corrupt the candidate's skill estimate.
3. **Exit code is diagnostic context, not a verdict.** The runner can exit
   non-zero after every test passed (a teardown crash) and exit zero with tests
   failing (a reporter bug). Both are represented faithfully.

The adapters also refuse to invent coverage they do not have: if the runner
reports a collection error, that becomes an explicit synthetic test case so the
candidate sees *why* nothing ran, instead of an empty result list that looks
like an infrastructure problem.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from oa_engine.execution import ExecutionResult, ExecutionStatus, TestCaseResult
from oa_engine.platform.config import Language
from oa_engine.platform.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from oa_engine.execution.payload import SubmissionPayload

__all__ = ["ResultParser", "get_parser"]

log = get_logger(__name__, service="oa-worker")

#: Applied to every free-text field so one enormous assertion message cannot
#: blow up the stored result or the WebSocket frame.
_MAX_MESSAGE_CHARS = 8_000
_MAX_STACK_CHARS = 16_000
_MAX_STREAM_CHARS = 8_000

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)


@runtime_checkable
class ResultParser(Protocol):
    """Contract every language adapter satisfies."""

    language: Language

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
        """Normalise one run into the unified result envelope."""
        ...


def _clean(text: str | None, *, limit: int) -> str | None:
    """Strip ANSI escapes and trailing whitespace, then cap the length.

    Runner output is attacker-influenced: a candidate can print raw escape
    sequences. Leaving them in would let them rewrite the terminal the browser
    renders, which is a real (if minor) injection vector into the IDE view.
    """
    if not text:
        return None
    stripped = _TRAILING_WS_RE.sub("", _ANSI_RE.sub("", text)).strip()
    if not stripped:
        return None
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit] + f"\n[truncated at {limit} characters]"


def _collection_error_case(
    *,
    test_id: str,
    name: str,
    message: str,
    stderr: str,
    exit_code: int | None,
) -> TestCaseResult:
    """Build the synthetic failing test used when the runner never ran.

    Reported as a failing test rather than a bare ``ERROR`` status because the
    candidate's code *is* the reason collection failed (an import that throws,
    a syntax error, a missing file). Distinguishing "your code broke the
    collection" from "our orchestrator broke" is exactly what this preserves.
    """
    detail = (
        message or stderr or f"the test runner exited with code {exit_code} before running any test"
    )
    return TestCaseResult(
        id=test_id,
        name=name,
        passed=False,
        duration_ms=0.0,
        message=_clean(detail, limit=_MAX_MESSAGE_CHARS),
        stack_trace=None,
    )


def _terminal_status(*, tests: list[TestCaseResult], exit_code: int | None) -> ExecutionStatus:
    """Decide the final status from per-test verdicts plus transport facts.

    Precedence matters and is intentional:

    * Any runnable test present -> the verdict is theirs (``PASSED``/``FAILED``),
      regardless of exit code.
    * No tests but a timeout -> ``TIMEOUT``: real evidence about their code.
    * No tests otherwise -> ``ERROR``: we genuinely do not know, and must not
      guess in either direction.
    """
    runnable = [t for t in tests if not t.skipped]
    if runnable:
        return ExecutionStatus.PASSED if all(t.passed for t in runnable) else ExecutionStatus.FAILED
    return ExecutionStatus.ERROR


def _base_kwargs(
    *,
    payload: SubmissionPayload,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    runtime_ms: int,
    stdout_truncated: bool,
    stderr_truncated: bool,
) -> dict[str, Any]:
    """Assemble the envelope fields shared by every adapter's return value."""
    # `submission_id` is deliberately absent: every caller already passes it
    # explicitly to `from_tests`, and duplicating it here made that call raise
    # "got multiple values for keyword argument".
    return {
        "question_id": getattr(payload, "question_id", None),
        "language": payload.language,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "runtime_ms": runtime_ms,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


def get_parser(language: Language) -> ResultParser:
    """Return the adapter for ``language``.

    Imports are local so that pulling in the Python adapter's dependencies does
    not initialise the Vitest or SQL paths, and so a missing optional dependency
    surfaces only for the language that needs it.
    """
    match language:
        case Language.PYTHON:
            from oa_worker.parsers.pytest_parser import PytestParser

            return PytestParser()
        case Language.REACT:
            from oa_worker.parsers.vitest_parser import VitestParser

            return VitestParser()
        case Language.SQL:
            from oa_worker.parsers.unittest_sql_parser import SqlUnittestParser

            return SqlUnittestParser()
    raise ValueError(f"no result parser for language {language!r}")
