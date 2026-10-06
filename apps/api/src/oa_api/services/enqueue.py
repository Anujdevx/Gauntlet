"""Publishing submissions to the broker.

**Why this imports Celery**, given that the control plane otherwise has no Celery
dependency and no Docker SDK:

An earlier version of this module published the wire format by hand with ``kombu``
specifically to keep Celery out of the API image. That was a false economy. Celery's
Redis transport wraps the message body in its own envelope, so a hand-built
protocol-2 body arrived at the worker with an extra list layer and every message
crash-looped the consumer with
``not enough values to unpack (expected 3, got 1)`` -- an error that names neither
the producer nor the payload, and that only appears once something is actually
deployed. Reproducing another system's serialisation format by hand means owning
every implicit rule in it, forever.

The dependency that actually matters for security is the Docker SDK, and this
module does not touch it. The API still cannot create a container, cannot execute
a submission, and has no route to a container runtime -- which is verified by
``test_api_image_cannot_import_docker``. Celery is a message queue client; it
cannot spawn a process in a container.

So: correctness over purity, and the purity claim is narrowed to the one that
bears on the threat model. The message *shape* still lives in
:mod:`oa_engine.platform.messaging`, so producer and consumer still agree by
construction rather than by convention.
"""

from __future__ import annotations

from dataclasses import dataclass

from celery import Celery

from oa_engine.platform.config import Settings, get_settings
from oa_engine.platform.logging import get_logger
from oa_engine.platform.messaging import SUBMISSION_TASK_NAME, SubmissionMessage

log = get_logger(__name__, service="oa-api")

__all__ = ["enqueue_submission"]


@dataclass(slots=True)
class _AppSlot:
    """Holds the process-wide sending app.

    A holder rather than a ``global``: rebinding a global under a lock hides the
    shared state at the declaration site, so a reader has to prove no other thread
    raced. One attribute on one named object is greppable.
    """

    app: Celery | None = None


_slot = _AppSlot()


def _get_app(settings: Settings) -> Celery:
    """Return the cached Celery app used only for sending.

    Created once per process. Constructing a Celery app per request would open a
    fresh broker connection pool on every submission and leak it.

    Not lock-protected, deliberately: two threads racing here would each build an
    app, and the loser's would be garbage-collected along with its connection.
    That is a transient duplicate connection, which is a far better failure than
    serialising every enqueue behind a lock.
    """
    existing = _slot.app
    if existing is not None:
        return existing
    app = Celery(
        "oa_api_send",
        broker=settings.broker_url(),
        # Nothing to consume and nothing to store. Declaring them keeps this client
        # from opening a result-backend connection or trying to prefetch.
        backend=None,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        broker_connection_retry_on_startup=True,
    )
    _slot.app = app
    log.debug("enqueue_app_created", broker=settings.broker_url())
    return app


def enqueue_submission(message: SubmissionMessage, *, settings: Settings | None = None) -> str:
    """Publish one submission message. Returns the task id.

    Args:
        message: The validated message.
        settings: Injected for tests.

    Returns:
        The generated task id, for correlating the submission row with a worker
        log line.

    Raises:
        RuntimeError: If the broker is unreachable or rejects the publish. The
            caller turns this into a ``503``. Returning ``202`` for work that will
            never happen tells the candidate a lie they will act on -- they will
            sit watching a spinner for a submission that was never queued.
    """
    effective = settings or get_settings()
    app = _get_app(effective)

    try:
        async_result = app.send_task(
            SUBMISSION_TASK_NAME,
            kwargs=message.model_dump(mode="json"),
            queue=effective.queue_name,
            # The worker's soft/hard limits are derived from the sandbox envelope,
            # not declared per message; setting them here would let a caller ask
            # for a longer run than containment allows.
            ignore_result=True,
        )
    except Exception as exc:
        log.exception(
            "publish_failed",
            submission_id=message.submission_id,
            broker=effective.broker_url(),
            error=str(exc),
        )
        raise RuntimeError(f"cannot reach the broker: {exc}") from exc

    log.info("published", submission_id=message.submission_id, task_id=async_result.id)
    return str(async_result.id)
