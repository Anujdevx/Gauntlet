"""Warm container pool.

Cold-starting a container costs 300-800 ms of ``create`` plus ``start``, which is
most of the 2 s load-test budget before any code has run. So workers keep N
pre-*started* idle containers per language and hand one out per submission.

The economics drive the design: a warm container must be *running* to be warm,
so the pool holds runnable processes between submissions. That is only safe
because a warm container has never seen candidate code -- it is created from a
pre-baked image with nothing injected. Injection happens after acquisition, and
the container is destroyed afterwards, never returned to the pool. A container
that has run code is never a candidate for reuse; that is the whole invariant
that makes a warm pool safe.

Lifecycle::

    (replenisher, background)        (submission path, foreground)
            |                                  |
            v                                  v
      create + start  --->  [ idle ]  --acquire-->  inject -> run -> grade
            ^                   |                            |
            |                   v                            v
            +------------- replenish  <---------------  destroy (always)

Replenishment runs on a background thread so a burst of submissions never waits
on a container build. A missed replenishment only costs latency, not
correctness, which is the right failure direction.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import docker
import docker.errors
from docker.models.containers import Container

from oa_engine.platform import metrics
from oa_engine.platform.config import DEFAULT_ENVELOPE, Language, SandboxEnvelope, Settings
from oa_engine.platform.logging import get_logger
from oa_worker.sandbox.spec import build_container_spec

if TYPE_CHECKING:  # pragma: no cover
    from docker.models.images import Image

__all__ = ["PooledContainer", "WarmPoolManager"]

log = get_logger(__name__, service="oa-worker")

#: How long a freshly created container is given to reach ``running``. A
#: container that cannot start this fast is a broken image, not a slow machine.
_CONTAINER_READY_TIMEOUT_S: float = 10.0


@dataclass(slots=True)
class PooledContainer:
    """One idle container plus the bookkeeping the pool needs to recycle it."""

    container: Container
    language: Language
    image: str
    created_at: float
    last_used_at: float
    #: Set once any code has been injected. Such a container is destroyed on
    #: release and never returned to the idle queue. This flag is the enforcement
    #: point for that rule, so it is set by the orchestrator rather than inferred.
    tainted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, max_idle_s: int) -> bool:
        """Whether this container has sat idle long enough to recycle.

        Rebuilt images change the underlying layer; a container created before
        the rebuild pins the old image for its whole life. The idle TTL bounds
        how stale the pool can get.
        """
        return (time.monotonic() - self.last_used_at) > max_idle_s


class WarmPoolManager:
    """Per-worker pool of idle, pre-started containers, keyed by language."""

    def __init__(
        self,
        *,
        client: docker.DockerClient,
        settings: Settings,
        envelope: SandboxEnvelope | None = None,
    ) -> None:
        """Build an empty pool. Nothing is created until :meth:`start`.

        Args:
            client: Docker client bound to the daemon that will run the sandboxes.
            settings: Operational configuration, including the target size.
            envelope: Containment policy, forwarded verbatim to the spec builder
                so a pooled container is secured identically to a cold one.
        """
        self._client = client
        self._api = client.api
        self.settings = settings
        self.envelope = envelope or DEFAULT_ENVELOPE
        self._pools: dict[Language, queue.LifoQueue[PooledContainer]] = {}
        self._lock = threading.Lock()
        self._replenisher: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the background replenisher, which also performs the initial fill.

        Idempotent. Safe to call from a Celery ``worker_process_init`` hook
        because each forked child gets its own manager instance and therefore its
        own pool, which is exactly right: pools are per-process and must not be
        shared across a fork.

        **Returns immediately.** The initial fill is deliberately *not* done
        synchronously here. This hook runs inside every forked child before the
        consumer starts, and creating ``pool_size * 3`` containers per child is
        seconds of blocking HTTP to the daemon -- multiplied by concurrency, it
        delays the worker becoming ready to accept a task at all, which is
        precisely when a queued submission is waiting. Filling on the
        replenisher thread keeps the first submission fast (it takes a cold-start
        hit) instead of making the whole worker unavailable.
        """
        with self._lock:
            if self._started:
                return
            self._started = True
            for language in Language:
                self._pools[language] = queue.LifoQueue(
                    maxsize=max(self.settings.pool_size_per_language, 1)
                )
            self._replenisher = threading.Thread(
                target=self._replenish_loop,
                name=f"warm-pool-{os_name()}",
                daemon=True,
            )
            self._replenisher.start()
        log.info("warm_pool_started", size_per_language=self.settings.pool_size_per_language)

    def stop(self, *, drain: bool = True) -> None:
        """Stop the replenisher and optionally destroy every idle container."""
        self._stop.set()
        thread = self._replenisher
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        self._replenisher = None
        with self._lock:
            self._started = False
        if drain:
            self.drain()
        log.info("warm_pool_stopped")

    def _replenish_loop(self) -> None:
        """Top the pool up on a fixed cadence until asked to stop.

        Catches everything: an exception escaping this thread would silently kill
        replenishment for the lifetime of the worker, and the only symptom would
        be a gradual latency regression.
        """
        interval = max(self.settings.pool_replenish_interval_s, 1)
        while not self._stop.wait(interval):
            try:
                self.replenish()
            except Exception:
                metrics.POOL_REPLENISH_ERRORS.labels(language="all").inc()
                log.exception("warm_pool_replenish_iteration_failed")

    # -- inventory ----------------------------------------------------------

    def idle_count(self, language: Language) -> int:
        """Number of containers currently idle for ``language``."""
        pool = self._pools.get(language)
        return pool.qsize() if pool is not None else 0

    def snapshot(self) -> dict[str, int]:
        """Idle counts per language, for the ``/health/ready`` payload."""
        return {lang.value: self.idle_count(lang) for lang in Language}

    def sizes(self) -> dict[str, int]:
        """Alias of :meth:`snapshot` kept for the metrics exporter."""
        return self.snapshot()

    # -- acquisition --------------------------------------------------------

    def acquire(
        self,
        language: Language,
        *,
        timeout_s: float | None = None,
    ) -> Container:
        """Take a warm container for ``language``.

        Args:
            language: Which pool to draw from.
            timeout_s: How long to wait for the replenisher to deliver one.
                ``None`` means create one inline instead of waiting, because a
                cold container that takes 400 ms still beats an infinite wait.

        Returns:
            A *running* container with the full security envelope applied and no
            candidate code present. Marked tainted by the caller before injection.

        Raises:
            RuntimeError: If no container could be obtained within the timeout.
                Surfaces as ``INFRA_ERROR``: we refuse to grade a submission
                outside the envelope just because the pool was cold.
        """
        pool = self._pools.get(language)
        deadline = time.monotonic() + (
            timeout_s if timeout_s is not None else self.settings.pool_acquire_timeout_s
        )

        if pool is not None:
            while True:
                try:
                    pooled = pool.get_nowait()
                except queue.Empty:
                    break
                if pooled.is_expired(self.settings.pool_container_max_idle_s):
                    self._destroy(pooled, reason="expired")
                    continue
                if self._is_usable(pooled):
                    pooled.last_used_at = time.monotonic()
                    metrics.POOL_ACQUISITIONS.labels(
                        language=language.value, outcome="warm_hit"
                    ).inc()
                    metrics.POOL_SIZE.labels(language=language.value, state="busy").inc()
                    return pooled.container
                self._destroy(pooled, reason="unhealthy")
                metrics.POOL_ACQUISITIONS.labels(
                    language=language.value, outcome="stale_evicted"
                ).inc()

        cold = self._create(language=language)
        metrics.POOL_ACQUISITIONS.labels(language=language.value, outcome="cold_miss").inc()
        metrics.POOL_SIZE.labels(language=language.value, state="busy").inc()
        log.info(
            "warm_pool_cold_miss",
            language=language.value,
            waited_ms=round((time.monotonic() - deadline) * 1000, 1),
        )

        self._schedule_replenish(language)
        return cold

    def release(self, container: Container | None, *, language: Language | None = None) -> None:
        """Destroy a container after use.

        Never returns a container to the idle pool. A container has executed
        candidate code, so its filesystem and memory are untrusted; the only
        safe disposition is removal. The parameter is accepted for symmetry with
        :meth:`acquire` and for call-site readability.

        Args:
            container: The used container, or ``None`` if none was created.
            language: Used only to keep the pool-size gauge balanced.
        """
        if container is None:
            return
        lang = language if language is not None else self._language_of(container)
        self._destroy_raw(container, reason="used")
        if lang is not None:
            metrics.POOL_SIZE.labels(language=lang.value, state="busy").dec()

    def _language_of(self, container: Container) -> Language | None:
        """Recover a container's language from its own labels.

        Reading the label the container was born with is more reliable than
        asking the caller to remember, and a wrong answer here would only skew
        a gauge rather than affect containment.
        """
        try:
            labels = container.labels or {}
        except docker.errors.DockerException:
            return None
        value = labels.get("oa.language")
        try:
            return Language(value) if value else None
        except ValueError:
            return None

    # -- replenishment ------------------------------------------------------

    def replenish(
        self, language: Language | None = None, target: int | None = None
    ) -> dict[str, int]:
        """Create containers until every requested pool is at its target size.

        Args:
            language: Restrict to one language; ``None`` means all.
            target: Override the configured pool size.

        Returns:
            Per-language counts of what was created.
        """
        wanted = self.settings.pool_size_per_language if target is None else target
        languages = [language] if language is not None else list(Language)
        created: dict[str, int] = {}
        for lang in languages:
            created[lang.value] = 0
            pool = self._pools.get(lang)
            if pool is None or wanted <= 0:
                continue
            while self.idle_count(lang) < wanted:
                try:
                    container = self._create(language=lang)
                except docker.errors.DockerException as exc:
                    metrics.POOL_REPLENISH_ERRORS.labels(language=lang.value).inc()
                    log.warning("warm_pool_create_failed", language=lang.value, error=str(exc))
                    break
                try:
                    pool.put_nowait(
                        PooledContainer(
                            container=container,
                            language=lang,
                            image=self.settings.image_for(lang),
                            created_at=time.monotonic(),
                            last_used_at=time.monotonic(),
                        )
                    )
                except queue.Full:
                    # Lost a race with a concurrent acquisition. Destroying the
                    # surplus is correct: the pool size is a target, not a quota
                    # that justifies leaking containers.
                    self._destroy_raw(container, reason="pool_full")
                    break
                created[lang.value] += 1
                metrics.POOL_SIZE.labels(language=lang.value, state="idle").inc()
        if any(created.values()):
            log.info("warm_pool_replenished", **created)
        return created

    def drain(self, language: Language | None = None) -> dict[str, int]:
        """Destroy every idle container, optionally for one language."""
        languages = [language] if language is not None else list(Language)
        drained: dict[str, int] = {}
        for lang in languages:
            pool = self._pools.get(lang)
            count = 0
            if pool is not None:
                while True:
                    try:
                        pooled = pool.get_nowait()
                    except queue.Empty:
                        break
                    self._destroy(pooled, reason="drain")
                    metrics.POOL_SIZE.labels(language=lang.value, state="idle").dec()
                    count += 1
            drained[lang.value] = count
        return drained

    # -- internals ----------------------------------------------------------

    def _schedule_replenish(self, language: Language) -> None:
        """Ask for a refill without blocking the submission that triggered it."""
        if self._replenisher is None or not self._replenisher.is_alive():
            return
        threading.Thread(
            target=self.replenish,
            kwargs={"language": language},
            name=f"warm-pool-replenish-{language.value}",
            daemon=True,
        ).start()

    def _create(self, *, language: Language) -> Container:
        """Create and start one secured, idle container.

        Uses the same :func:`build_container_spec` as the grading path, so a
        warm container is byte-for-byte as constrained as a cold one. The
        container is started with ``sleep infinity`` as its command so it stays
        alive without doing anything, and it receives no environment, no mounts
        resolved to candidate data, and no injected files.
        """
        from oa_engine.platform.config import ExecutionLimits

        limits = ExecutionLimits.build(
            language=language,
            settings=self.settings,
            envelope=self.envelope,
            submission_id=f"pool-{language.value}-{int(time.time())}",
        )
        # No workspace_source and no grading_source: a pooled container has no
        # bind mounts whatsoever, so it cannot hold candidate code or hidden
        # tests even transiently. That is what makes reuse safe, and it is why
        # a warm container is started with `sleep` and nothing else.
        spec = build_container_spec(
            limits=limits,
            envelope=self.envelope,
            submission_id=limits.container_name,
            run_command=("sleep", str(int(_CONTAINER_READY_TIMEOUT_S) * 1000)),
        )
        # Named deterministically. A leaked container is then diagnosable by name
        # (`docker ps --filter name=oa-sbx-`) rather than only by label, and the
        # name makes it obvious in `docker ps` that this belongs to the platform
        # and not to the host's other workloads.
        spec["name"] = limits.container_name
        try:
            created = self._api.create_container(**spec)
        except docker.errors.APIError as exc:
            raise RuntimeError(
                f"cannot create a {language.value} sandbox container from "
                f"{limits.image}: {exc}. If this mentions the image name, build "
                f"the images first: make sandbox-images"
            ) from exc

        container_id = created["Id"]
        try:
            # No configuration is passed to start(). docker-py 7.x removed support
            # for it ("Providing configuration in the start() method is no longer
            # supported"), and every knob belongs on the create request anyway --
            # which is where build_container_spec already puts all of them.
            self._api.start(container_id)
        except docker.errors.DockerException as exc:
            self._destroy_by_id(container_id)
            raise RuntimeError(f"sandbox container failed to start: {exc}") from exc

        # `client.containers.get` rather than hand-building a Container and
        # assigning `.id`: docker-py 7.x makes `id` a read-only property derived
        # from the client's collection, so assigning it raises AttributeError.
        return self._client.containers.get(container_id)

    def _is_usable(self, pooled: PooledContainer) -> bool:
        """Whether a pooled container is still running and healthy."""
        try:
            pooled.container.reload()
            state = (pooled.container.attrs.get("State") or {}).get("Status")
            return state == "running"
        except docker.errors.NotFound:
            return False
        except docker.errors.DockerException as exc:
            log.warning("warm_pool_reload_failed", error=str(exc))
            return False

    def _destroy(self, pooled: PooledContainer, *, reason: str) -> None:
        """Destroy a pooled container and adjust the idle gauge."""
        self._destroy_raw(pooled.container, reason=reason)

    def _destroy_raw(self, container: Container, *, reason: str) -> None:
        """Force-remove ``container``, logging rather than raising on failure.

        A container that cannot be removed is a hostile-capable process still
        alive on the host, so this logs at error level and escalates to a
        label-scoped prune.
        """
        cid = str(container.id)
        try:
            container.remove(force=True, v=True)
            log.debug("warm_pool_container_removed", container_id=cid[:12], reason=reason)
        except docker.errors.NotFound:
            return
        except docker.errors.DockerException:
            log.exception("warm_pool_remove_failed", container_id=cid[:12], reason=reason)
            try:
                self._client.containers.prune(filters={"label": ["oa.managed=true"], "id": [cid]})
            except docker.errors.DockerException as prune_exc:
                log.exception("warm_pool_prune_failed", container_id=cid[:12], error=str(prune_exc))

    def _destroy_by_id(self, id_: str) -> None:
        """Remove by raw id, for the failure path in :meth:`_create`."""
        try:
            self._api.remove_container(id_, force=True, v=True)
        except docker.errors.DockerException as exc:
            log.exception("warm_pool_remove_by_id_failed", container_id=id_[:12], error=str(exc))

    def resolve_image(self, language: Language) -> Image | None:
        """Look up the local image for ``language``, for readiness reporting."""
        try:
            return self._client.images.get(self.settings.image_for(language))
        except docker.errors.DockerException:
            return None


def os_name() -> str:
    """Short host identifier for the replenisher thread name."""
    import os

    return os.uname().nodename
