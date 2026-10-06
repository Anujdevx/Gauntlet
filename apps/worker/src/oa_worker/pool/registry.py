"""Process-wide registry for the warm pool.

Celery preforks, then forks worker *children* which execute tasks. A warm pool
must be per-child for two independent reasons:

* A forked pool would share file descriptors and queue state with its parent,
  which is the same class of bug that forces logging to be reconfigured after a
  fork. Two children would also race to hand out the same container.
* Each child holds its own idle containers, so the pool size is a per-process
  budget. ``worker_concurrency * pool_size_per_language`` containers total.

So the pool is created lazily *inside* the child, on first use, and started by
the ``worker_process_init`` signal. This module is the single place that knows
that, which is what keeps the wiring from being duplicated -- and forgotten --
by each consumer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import docker

from oa_engine.platform.config import DEFAULT_ENVELOPE, get_settings
from oa_engine.platform.logging import get_logger
from oa_worker.pool.manager import WarmPoolManager

if TYPE_CHECKING:  # pragma: no cover
    pass

__all__ = ["get_warm_pool", "reset_warm_pool", "start_warm_pool", "stop_warm_pool"]

log = get_logger(__name__, service="oa-worker")

_lock = threading.Lock()


@dataclass(slots=True)
class _PoolSlot:
    """Mutable holder for the process's single pool.

    A holder object rather than a module-level ``global``: rebinding a global
    under a lock is the pattern ``global`` exists for, but the rebind is invisible
    at the declaration site, so a reader has to prove no other thread read a
    stale value. Mutating one attribute of an immutable-by-convention holder makes
    the shared state explicit and greppable.
    """

    value: WarmPoolManager | None = None


_slot = _PoolSlot()


def get_warm_pool() -> WarmPoolManager:
    """Return this process's pool, creating and starting it on first call.

    Idempotent and thread-safe. The double-checked lock exists because task
    threads within one child all call this, and each would otherwise build its
    own manager and its own set of idle containers.

    Returns:
        A started :class:`WarmPoolManager`.
    """
    existing = _slot.value
    if existing is not None:
        return existing

    with _lock:
        if _slot.value is None:
            manager = WarmPoolManager(
                client=docker.from_env(timeout=30),
                settings=get_settings(),
                envelope=DEFAULT_ENVELOPE,
            )
            # ``start`` is best-effort. A failure here must not stop the worker
            # from accepting submissions: a cold container still works, just
            # slower. Failing the child would turn a latency problem into an
            # outage, which is the wrong direction for degradation.
            try:
                manager.start()
            except Exception:
                log.exception("warm_pool_start_failed_falling_back_to_cold_starts")
            _slot.value = manager
        return _slot.value


def start_warm_pool() -> WarmPoolManager:
    """Warm the pool. Called from Celery's ``worker_process_init`` hook."""
    pool = get_warm_pool()
    log.info("warm_pool_ready", **{f"idle_{k}": v for k, v in pool.snapshot().items()})
    return pool


def stop_warm_pool(*, drain: bool = True) -> None:
    """Tear down this process's pool. Called from the worker shutdown hook."""
    with _lock:
        manager = _slot.value
        if manager is None:
            return
        _slot.value = None
    try:
        manager.stop(drain=drain)
    except Exception:
        log.exception("warm_pool_stop_failed")


def reset_warm_pool() -> None:
    """Drop the pool without stopping it. For tests that need a clean slate."""
    with _lock:
        _slot.value = None
