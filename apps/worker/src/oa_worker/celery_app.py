"""Celery application wiring for sandbox execution workers.

Constraints that shape this module:

* Workers are the **only** process holding a Docker socket. The API enqueues
  and never executes.
* Tasks are idempotent and safe to redeliver. Celery acknowledges late, so a
  worker crash mid-execution redelivers the task. Because containers are
  single-use and named deterministically from the submission id, a redelivery
  converges on the same terminal state rather than double-charging the skill
  model.
* Execution is bounded by a **hard** wall clock enforced inside the task.
  Celery's soft time limit only exists to reclaim the worker slot; it cannot be
  the only guarantee, because a task stuck in a blocking native call will not
  observe it.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging, worker_process_init, worker_process_shutdown
from structlog.contextvars import clear_contextvars

from oa_engine.platform.config import DEFAULT_ENVELOPE, get_settings
from oa_engine.platform.logging import configure_logging, get_logger
from oa_worker.pool.registry import start_warm_pool, stop_warm_pool

__all__ = ["celery_app", "get_task"]

log = get_logger(__name__, service="oa-worker")

settings = get_settings()

#: The sandbox wall clock, and the worker-level budgets layered around it.
#:
#: ``soft`` lets the task unwind so we can emit a clean timeout result and
#: destroy the container ourselves. ``hard`` is the backstop for a task wedged
#: in uninterruptible work; the parent kills the child process group.
SANDBOX_WALL_CLOCK_S = DEFAULT_ENVELOPE.wall_clock_timeout_s
TASK_SOFT_TIME_LIMIT_S = SANDBOX_WALL_CLOCK_S + 15
TASK_HARD_TIME_LIMIT_S = SANDBOX_WALL_CLOCK_S + 45

#: How long the broker must withhold a message before considering it lost.
#: Must exceed the hard limit or a healthy-but-slow worker gets its task
#: redelivered underneath it, creating a duplicate container.
VISIBILITY_TIMEOUT_S = TASK_HARD_TIME_LIMIT_S + 60


def _broker_transport_options() -> dict[str, Any]:
    """Broker transport tuning derived from the task timeouts above."""
    return {
        "visibility_timeout": VISIBILITY_TIMEOUT_S * 1000,
        "queue_order_strategy": "priority",
        "socket_keepalive": True,
        "socket_connect_timeout": 5,
        "socket_timeout": 10,
        "max_connections": max(settings.worker_concurrency * 2, 8),
    }


celery_app = Celery(
    "oa_worker",
    broker=settings.broker_url(),
    backend=settings.result_backend(),
    include=["oa_worker.tasks.execute"],
)

celery_app.conf.update(
    # -- wire protocol ------------------------------------------------------
    # 2 = the [args, kwargs, embed] envelope. Pinned explicitly rather than left
    # at Celery's default because the API publishes protocol 2 (see
    # `oa_engine.platform.messaging`), and a mismatch fails with
    # "not enough values to unpack (expected 3, got 1)" deep in the consumer --
    # an error that names neither the producer nor the payload shape.
    task_protocol=2,
    result_protocol=2,
    # -- serialisation ------------------------------------------------------
    # JSON only, never pickle. A pickle payload is arbitrary code execution, and
    # the broker is a trust boundary a compromised API instance could write to.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_accept_content=["json"],
    # -- delivery semantics -------------------------------------------------
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_acks_on_failure_or_timeout=True,
    # -- timeouts -----------------------------------------------------------
    task_soft_time_limit=TASK_SOFT_TIME_LIMIT_S,
    task_time_limit=TASK_HARD_TIME_LIMIT_S,
    # -- results ------------------------------------------------------------
    result_expires=int(settings.stream_ttl_s),
    result_extended=True,
    task_track_started=True,
    # -- routing ------------------------------------------------------------
    task_default_queue=settings.queue_name,
    task_routes={
        "oa_worker.tasks.execute.execute_submission": {
            "queue": settings.queue_name,
            "priority": 5,
        },
        "oa_worker.tasks.execute.replenish_warm_pool": {
            "queue": f"{settings.queue_name}.pool",
            "priority": 1,
        },
    },
    # -- worker -------------------------------------------------------------
    # Prefetch of 1 gives round-robin fairness: with a higher prefetch a single
    # fast worker hoards messages and the warm pools on idle workers sit there
    # doing nothing, which is exactly when we need them hot.
    worker_prefetch_multiplier=1,
    worker_concurrency=settings.worker_concurrency,
    # Recycle children periodically. Long-lived workers accumulate leaked
    # file descriptors from thousands of create/inspect/remove cycles, and a
    # recycled child is cheaper to debug than an exhausted one.
    worker_max_tasks_per_child=200,
    worker_max_memory_per_child=1_073_741_824,  # 1 GiB
    worker_send_task_events=True,
    worker_heartbeat_interval=30,
    broker_connection_retry_on_startup=True,
    # -- retry --------------------------------------------------------------
    task_default_retry_delay=5,
    task_autoretry_for=(ConnectionError,),
    task_max_retries=3,
    broker_transport_options=_broker_transport_options(),
    # -- misc ---------------------------------------------------------------
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry=True,
)


@setup_logging.connect
def _route_celery_logs_through_structlog(**_kwargs: object) -> None:
    """Install our logging config, suppressing Celery's own dictConfig."""
    configure_logging(level=settings.log_level, json_output=settings.log_json)


@worker_process_init.connect
def _configure_worker_child(**_kwargs: object) -> None:
    """Rebuild logging after the pre-fork, then warm this child's pool.

    Handlers inherited across a fork share a file descriptor and interleave
    corrupted output; reconfiguring post-fork is the standard remedy.

    The pool is started here rather than in the parent because a pool must be
    per-child: two children sharing idle containers would race to hand out the
    same one, and the inherited sockets would be corrupted by the fork. Starting
    it eagerly means the first submission of a fresh child does not pay a cold
    start, which is the whole reason the pool exists.
    """
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    clear_contextvars()
    if settings.pool_size_per_language > 0:
        start_warm_pool()
    else:
        log.warning("warm_pool_disabled_forcing_cold_starts")


@worker_process_shutdown.connect
def _teardown_worker_child(**_kwargs: object) -> None:
    """Destroy this child's idle containers before it exits.

    ``worker_max_tasks_per_child`` recycles children periodically. Without this,
    each recycled child would leak its pool of idle containers, and a long-lived
    worker would accumulate hundreds of idle containers until the daemon ran out
    of memory. Draining on shutdown is what makes the recycle policy safe.
    """
    clear_contextvars()
    stop_warm_pool(drain=True)


def get_task(name: str) -> Any:
    """Look up a registered task by dotted name, with an actionable error."""
    task = celery_app.tasks.get(name)
    if task is None:
        registered = ", ".join(sorted(celery_app.tasks))
        raise KeyError(f"Task {name!r} is not registered. Registered: {registered}")
    return task
