"""The sandbox execution task.

This is the single entry point from a queued submission to a persisted, graded
result. Everything security-relevant lives one layer down in
:mod:`oa_worker.sandbox.orchestrator`; this module owns orchestration of the
run's *lifecycle* and its bookkeeping in Redis.

Lifecycle of one submission:

1. Load the immutable submission payload (files plus the hidden test bundle)
   from Redis. Hidden tests are stored in a key the container cannot reach.
2. Take a warm container for the language from the local pool.
3. Hand off to the orchestrator, which applies the full security envelope.
4. Normalise the runner output into :class:`ExecutionResult`.
5. Publish progress events to a Redis stream the WebSocket layer tails.
6. Destroy the container unconditionally, then replenish the pool.
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from celery import Task
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from oa_engine.execution import ExecutionResult, ExecutionStatus
from oa_engine.execution.payload import SubmissionPayload
from oa_engine.platform import metrics
from oa_engine.platform.config import (
    DEFAULT_ENVELOPE,
    ExecutionLimits,
    Language,
    Settings,
    get_settings,
)
from oa_engine.platform.logging import get_logger
from oa_worker.celery_app import celery_app
from oa_worker.sandbox.orchestrator import OrchestratorError, SandboxOrchestrator

log = get_logger(__name__, service="oa-worker")

__all__ = ["ExecuteSubmissionArgs", "execute_submission", "replenish_warm_pool"]


class ExecuteSubmissionArgs(BaseModel):
    """Serialisable arguments for :func:`execute_submission`.

    Bound with :class:`pydantic.BaseModel` rather than a bare dict so that a
    malformed enqueue fails at the API boundary with a useful 422 instead of
    failing deep inside a worker.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    submission_id: Annotated[str, Field(min_length=1, max_length=128)]
    question_id: Annotated[str | None, Field(default=None, max_length=128)]
    language: Language
    #: Key under which the candidate's files and the hidden test bundle live.
    payload_key: Annotated[str, Field(min_length=1, max_length=256)]
    #: Redis stream key for live progress events.
    stream_key: Annotated[str, Field(min_length=1, max_length=256)]
    #: Pin the grading run to an exact image digest for reproducibility.
    image_digest: Annotated[str | None, Field(default=None, max_length=256)] = None

    def envelope_limits(self, settings: Settings) -> ExecutionLimits:
        """Derive per-run limits, pinning the image digest when one was given."""
        return ExecutionLimits.build(
            language=self.language,
            settings=settings,
            envelope=DEFAULT_ENVELOPE,
            submission_id=self.submission_id,
            image_digest=self.image_digest,
        )


@celery_app.task(
    bind=True,
    name="oa_worker.tasks.execute.execute_submission",
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
)
def execute_submission(self: Task, **kwargs: Any) -> dict[str, Any]:
    """Run one submission in a hardened sandbox and return a graded result.

    Args:
        self: Celery task instance, used for retries and logging context.
        **kwargs: The fields of :class:`ExecuteSubmissionArgs`, passed as Celery
            protocol 2 keyword arguments. Keywords rather than a single positional
            blob so that a redelivered message names the offending field in the
            worker's log instead of failing with an opaque arity error.

    Returns:
        Serialised :class:`ExecutionResult`. Always returns a result, even on
        infrastructure failure -- an exception here would be retried by Celery
        and, after exhausting retries, would leave the candidate staring at a
        spinner with no grade. Distinguishing the two failure classes in the
        payload is strictly better for the user and for the skill model's
        integrity.

    Raises:
        Nothing under normal operation. Orchestrator faults are converted into
        ``INFRA_ERROR`` results. The only exception raised is a programming error
        in argument parsing, which should not be retried.
    """
    settings = get_settings()
    started = time.perf_counter()

    try:
        parsed = ExecuteSubmissionArgs.model_validate(kwargs)
    except ValidationError as exc:
        module_log = get_logger(__name__, service="oa-worker")
        module_log.error(
            "submission_args_invalid",
            error=str(exc),
            received_keys=sorted(kwargs),
        )
        return ExecutionResult(
            submission_id=str(kwargs.get("submission_id", "unknown")),
            status=ExecutionStatus.INFRA_ERROR,
            stderr=f"Malformed execution request: {exc}",
        ).model_dump(mode="json")

    limits = parsed.envelope_limits(settings)
    log = get_logger(
        __name__,
        service="oa-worker",
        submission_id=parsed.submission_id,
        language=parsed.language.value,
        image=limits.image,
    )

    # ``self.request.id`` may be retried, so the same logical submission can see
    # several task ids. Bind it but never key state on it.
    log.info("execution.start", task_id=self.request.id)

    orchestrator = SandboxOrchestrator(settings=settings)
    payload: SubmissionPayload | None = None
    try:
        payload = SubmissionPayload.load(settings=settings, key=parsed.payload_key)
        result = orchestrator.run(payload=payload, limits=limits, stream_key=parsed.stream_key)
    except OrchestratorError as exc:
        metrics.EXECUTION_INFRA_FAILURES.labels(language=parsed.language.value).inc()
        log.exception("execution.infra_error", error=str(exc), error_type=type(exc).__name__)
        result = ExecutionResult(
            submission_id=parsed.submission_id,
            question_id=parsed.question_id,
            status=ExecutionStatus.INFRA_ERROR,
            language=parsed.language.value,
            stderr=f"Sandbox infrastructure error: {exc}",
        )
    except Exception as exc:
        metrics.EXECUTION_INFRA_FAILURES.labels(language=parsed.language.value).inc()
        log.exception("execution.unhandled", error=str(exc))
        result = ExecutionResult(
            submission_id=parsed.submission_id,
            question_id=parsed.question_id,
            status=ExecutionStatus.INFRA_ERROR,
            language=parsed.language.value,
            stderr=f"Unhandled orchestrator error: {type(exc).__name__}",
        )
    finally:
        # Containers are single-use and destroyed by ``run`` itself. This
        # finally exists to guarantee the payload is dropped from worker memory
        # so a long-lived worker process does not accumulate candidate code.
        del payload

    total_ms = int((time.perf_counter() - started) * 1000)
    result = result.model_copy(update={"duration_ms_total": total_ms})
    if result.image_digest is None:
        result = result.model_copy(
            update={"image_digest": orchestrator.resolve_image_digest(limits.image)}
        )

    # Celery runs tasks synchronously in a forked child, so the async finaliser is
    # driven explicitly. `asyncio.run` here rather than a background task: the
    # result must be durable before the task acknowledges, or a crash in the gap
    # loses the grade.
    import asyncio

    asyncio.run(_finalise(settings=settings, result=result, stream_key=parsed.stream_key))

    metrics.EXECUTION_TOTAL.labels(language=parsed.language.value, status=result.status.value).inc()
    metrics.EXECUTION_DURATION.labels(language=parsed.language.value).observe(total_ms / 1000)

    log.info(
        "execution.finish",
        status=result.status.value,
        score=result.score.percentage,
        tests_total=result.score.total,
        runtime_ms=result.runtime_ms,
        total_ms=total_ms,
        counts_against_candidate=result.counts_against_candidate,
    )
    return result.model_dump(mode="json")


async def _finalise(*, settings: Any, result: ExecutionResult, stream_key: str) -> None:
    """Persist the result and notify every watcher.

    Three things must happen after grading, and each is independently useful:

    1. **Publish the terminal event** to the Redis stream the WebSocket tails, so
       a candidate watching their submission sees it finish immediately.
    2. **Write the durable row** to Postgres. Redis is a cache with an expiry;
       mastery history must not evaporate when a TTL lapses.
    3. **Publish stdout/stderr** events so the terminal panel is populated for a
       client that connects after the run finished -- the reconnect path reads the
       buffered stream.

    Failures are logged, not raised. A grading result that could not be persisted
    is an operational problem, not a reason to fail the task and trigger a retry
    that re-grades the same submission and double-charges the skill model.
    """
    from oa_api.models.db import dispose_engine, session_scope
    from oa_api.services import submission_store
    from oa_api.services.results import persist_result

    try:
        if result.stdout:
            await submission_store.publish_event(
                settings, submission_id=result.submission_id, event="stdout", data=result.stdout
            )
        if result.stderr:
            await submission_store.publish_event(
                settings, submission_id=result.submission_id, event="stderr", data=result.stderr
            )

        async with session_scope() as session:
            await persist_result(session, result=result)
        await dispose_engine()
        log.info("result_persisted", submission_id=result.submission_id, status=result.status.value)
    except Exception:
        log.exception("result_persist_failed", submission_id=result.submission_id)

    try:
        await submission_store.publish_event(
            settings,
            submission_id=result.submission_id,
            event="result",
            data=result.to_stream_event(),
        )
    except Exception:
        log.warning("stream_publish_failed", submission_id=result.submission_id)


@celery_app.task(name="oa_worker.tasks.execute.replenish_warm_pool")
def replenish_warm_pool(language: str | None = None, target: int | None = None) -> dict[str, Any]:
    """Top a language's warm pool back up to ``target`` idle containers.

    Runs on its own low-priority queue so a large replenishment burst can never
    delay a candidate's submission.
    """
    settings = get_settings()
    orchestrator = SandboxOrchestrator(settings=settings)
    outcome = orchestrator.warm_pool.replenish(
        language=Language(language) if language else None,
        target=target,
    )
    return dict(outcome)


@celery_app.task(name="oa_worker.tasks.execute.drain_warm_pool")
def drain_warm_pool(language: str | None = None) -> dict[str, int]:
    """Destroy every idle warm container, e.g. after an image rebuild."""
    settings = get_settings()
    orchestrator = SandboxOrchestrator(settings=settings)
    return dict(orchestrator.warm_pool.drain(language=Language(language) if language else None))
