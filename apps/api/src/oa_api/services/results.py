"""Rebuilding the unified execution result from the durable submission row.

The worker's :class:`~oa_engine.execution.ExecutionResult` is the canonical shape.
This module reconstructs an equivalent document from Postgres so that a client
which reconnects after its event stream expired still sees the same result.

Two properties matter more than convenience here:

**Hidden tests are never included.** The durable row stores stdout, stderr, and
per-test outcomes; it does not store the hidden test *sources*, because
``test_results`` holds only a key, a verdict, and a message. So the reconstruction
is incapable of leaking them, by construction rather than by omission.

**The schema matches the worker's exactly.** The frontend has one TypeScript type
for a result and consumes both the WebSocket frames and this endpoint. A divergent
shape here would mean two code paths in the client for the same object.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from oa_api.models.tables import Submission
from oa_engine.execution import ExecutionResult, ExecutionStatus, TestCaseResult

__all__ = ["build_result_document"]


def build_result_document(submission: Submission) -> dict[str, Any]:
    """Render a terminal submission as a result document.

    Args:
        submission: A submission that has left the ``queued`` state. Its
            ``test_results`` relationship must be loaded.

    Returns:
        A dict matching :meth:`ExecutionResult.model_dump`, ready to JSON-encode.

    Raises:
        ValueError: If the stored status is not a member of ``ExecutionStatus``.
            Surfaced loudly rather than coerced: an unknown status means the
            database and the code have diverged, and guessing which verdict to show
            a candidate would be worse than a 500.
    """
    status = ExecutionStatus(submission.status)

    tests = [
        TestCaseResult(
            id=result.test_key,
            name=result.test_key.split("::")[-1] or result.test_key,
            passed=result.passed,
            duration_ms=result.duration_ms,
            message=result.message,
            stack_trace=result.stack_trace,
            skipped=result.skipped,
        ).model_dump(mode="json")
        for result in submission.test_results
    ]

    passed = sum(1 for t in tests if t["passed"] and not t["skipped"])
    runnable = [t for t in tests if not t["skipped"]]
    ratio = (passed / len(runnable)) if runnable else 0.0

    return {
        "submission_id": str(submission.id),
        "question_id": str(submission.question_id) if submission.question_id else None,
        "status": status.value,
        "tests": tests,
        "stdout": submission.stdout or "",
        "stderr": submission.stderr or "",
        "runtime_ms": submission.runtime_ms or 0,
        "stdout_truncated": submission.stdout_truncated,
        "stderr_truncated": submission.stderr_truncated,
        "score": {
            "passed": passed,
            "failed": len(runnable) - passed,
            "skipped": len(tests) - len(runnable),
            "total": len(tests),
            "ratio": ratio,
            "percentage": round(ratio * 100, 2),
        },
        "exit_code": submission.exit_code,
        "language": submission.language,
        "image_digest": submission.image_digest,
        "created_at": submission.created_at.isoformat(),
        "duration_ms_total": submission.duration_ms_total,
        "counts_against_candidate": status.counts_against_candidate,
    }


async def persist_result(session: AsyncSession, *, result: ExecutionResult) -> Submission:
    """Write a worker's result into the durable submission row.

    Async because the session is: calling ``session.scalar`` without ``await``
    produces a coroutine that is silently discarded, and the function then reports
    success while writing nothing. The runtime warning is the only clue, which is
    why the signature says so explicitly.

    Uses an upsert on ``test_results`` keyed by ``(submission_id, test_key)``
    because Celery acknowledges late: a redelivered task re-applying the same
    result must not fail on the unique constraint, and must certainly not
    duplicate a test row.

    Args:
        session: An open async database session.
        result: The canonical result from the sandbox.

    Returns:
        The persisted ``Submission`` row.

    Raises:
        ValueError: If no submission row exists for this result.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from oa_api.models.tables import TestResult as TestResultRow

    status = ExecutionStatus(result.status)

    submission: Submission | None = await session.scalar(
        select(Submission).where(Submission.id == result.submission_id)
    )
    if submission is None:
        raise ValueError(f"no submission row for {result.submission_id}; cannot persist a result")

    submission.status = status.value
    submission.image_digest = result.image_digest
    submission.score_percentage = result.score.percentage
    submission.tests_passed = result.score.passed
    submission.tests_total = result.score.total
    submission.exit_code = result.exit_code
    submission.runtime_ms = result.runtime_ms
    submission.duration_ms_total = result.duration_ms_total
    submission.stdout = result.stdout
    submission.stderr = result.stderr
    submission.stdout_truncated = result.stdout_truncated
    submission.stderr_truncated = result.stderr_truncated
    submission.finished_at = datetime.now(UTC)
    submission.error_message = None

    for case in result.tests:
        stmt = (
            pg_insert(TestResultRow)
            .values(
                submission_id=result.submission_id,
                test_key=case.id,
                passed=case.passed,
                skipped=case.skipped,
                duration_ms=case.duration_ms,
                message=case.message,
                stack_trace=case.stack_trace,
            )
            .on_conflict_do_update(
                index_elements=[TestResultRow.submission_id, TestResultRow.test_key],
                set_={
                    "passed": case.passed,
                    "skipped": case.skipped,
                    "duration_ms": case.duration_ms,
                    "message": case.message,
                    "stack_trace": case.stack_trace,
                },
            )
        )
        await session.execute(stmt)

    return submission
