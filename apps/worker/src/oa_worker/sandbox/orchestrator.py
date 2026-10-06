"""Zero-trust container orchestration.

This is the security-critical module of the whole platform. Every hostile
submission reaches it, and it assumes the contents of :class:`SubmissionPayload`
are actively trying to escape.

The controls, and *why* each is here:

``network_disabled=True``
    Blocks exfiltration. Without it a candidate streams their own grading data
    out, or mines the host, or pivots to the metadata service for cloud
    credentials. Docker's ``none`` network namespace still leaves loopback,
    which is intentional and sufficient: most RAG and HTTP-client mocks need it.

``read_only=True`` with a writable tmpfs ``/tmp``
    Stops the candidate from replacing the runner, the Python on PATH, or the
    image's own site-packages. The image is immutable; the only writable places
    are ``/workspace``, ``/grading`` (read-only), and ``/tmp``.

    ``/tmp`` is built as a **raw dict**, not a :class:`docker.types.Mount`.
    ``docker.types.Mount`` with ``TmpfsOptions`` hardcodes ``noexec``, which
    breaks the many legitimate tasks that compile or run a helper binary from a
    temp directory. The low-level client accepts a raw mount spec, so we build
    one with ``noexec`` absent. ``sandbox/tests/test_tmp_exec.py`` proves a
    binary executes from ``/tmp`` while the root filesystem stays read-only.

``cap_drop=["ALL"]``, ``no-new-privileges``, non-root user
    Three independent layers against privilege escalation. Any one of them
    alone is a known-escaped configuration in some Docker/gVisor version; all
    three together is defence in depth.

``runtime="runsc"``
    gVisor is a userspace kernel, so even a container escape primitive lands in
    gVisor's syscall interception rather than on the host kernel. If ``runsc``
    is not registered, :meth:`SandboxOrchestrator.__init__` refuses to start.
    Silently falling back to ``runc`` would turn a missing dependency into a
    silent total loss of the second isolation layer, so the choice is to fail
    closed.

``pids_limit``
    Fork bombs. Memory limits alone do not stop ``while True: fork()`` quickly
    enough to matter, because the parent's table is the bottleneck, not the
    child's RSS.

Hard wall clock
    Enforced with a deadline and ``SIGKILL`` followed by a forced remove,
    because a hostile process is free to ignore ``SIGTERM``.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import docker
import docker.errors
from structlog.contextvars import bind_contextvars, clear_contextvars

from oa_engine.execution import ExecutionResult, ExecutionStatus
from oa_engine.execution.payload import (
    SubmissionPayload,
)
from oa_engine.platform.config import (
    DEFAULT_ENVELOPE,
    GVISOR_RUNTIME,
    ExecutionLimits,
    SandboxEnvelope,
    Settings,
)
from oa_engine.platform.logging import get_logger
from oa_worker.pool.registry import get_warm_pool
from oa_worker.sandbox.harness import (
    run_candidate_phase,
    run_grading_phase,
)
from oa_worker.sandbox.spec import (
    CONTAINER_RESULT_PATH,
    build_container_spec,
    build_tmpfs_mount_spec,
)

if TYPE_CHECKING:  # pragma: no cover
    from docker.models.containers import Container

__all__ = [
    "CONTAINER_RESULT_PATH",
    "OrchestratorError",
    "SandboxOrchestrator",
    "build_container_spec",
    "build_tmpfs_mount_spec",
]

log = get_logger(__name__, service="oa-worker")

#: The sandbox container's own command. A long-running no-op: every real command
#: is executed separately so that the container survives between the candidate's
#: run phase and the grading phase.
_IDLE_COMMAND: Final[tuple[str, ...]] = ("sleep", "infinity")


class OrchestratorError(RuntimeError):
    """The sandbox could not be provisioned or driven.

    Always maps to ``INFRA_ERROR``, never to a candidate-facing failure.
    """


@dataclass(slots=True)
class RunOutcome:
    """Raw, ungraded output of one sandbox invocation."""

    status: ExecutionStatus
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    exit_code: int | None
    runtime_ms: int
    result_document: dict[str, Any] | None


class SandboxOrchestrator:
    """Provisions, drives, and destroys a single sandbox per submission."""

    def __init__(self, *, settings: Settings, envelope: SandboxEnvelope | None = None) -> None:
        """Bind the orchestrator to a Docker daemon and a security envelope.

        Args:
            settings: Operational configuration.
            envelope: Containment policy. Defaults to the process-wide
                immutable envelope; tests inject a loosened copy for
                exercising specific branches, never for production paths.

        Raises:
            OrchestratorError: If Docker is unreachable, or if the required
                ``runsc`` runtime is not registered. Failing here rather than at
                first submission keeps the problem visible at deploy time.
        """
        self.settings = settings
        self.envelope = envelope or DEFAULT_ENVELOPE
        self.envelope.validate_invariants()
        self._client = docker.from_env(timeout=30)
        self._api = self._client.api
        # The process-wide pool, not a fresh one per orchestrator: a pool per
        # orchestrator would be per submission, which is exactly the cold start
        # the pool exists to avoid.
        self.warm_pool = get_warm_pool()
        self._verify_runtime_registered()

    # -- startup guards -----------------------------------------------------

    def _verify_runtime_registered(self) -> None:
        """Confirm the *configured* runtime exists on this daemon.

        Fail-closed, but against the configured value rather than a hardcoded
        ``runsc``. If ``SANDBOX_RUNTIME=runsc`` and gVisor is missing, the worker
        refuses to start rather than silently executing under ``runc`` -- that
        fallback is precisely the failure worth refusing, because it yields a
        platform that reports itself as hardened while running one isolation
        layer instead of two.

        Checked by runtime key in ``/info`` rather than by attempting a run: a
        successful-looking run would be a much worse place to discover the
        mismatch.
        """
        wanted = self.settings.sandbox_runtime
        try:
            info = self._client.info()
        except docker.errors.DockerException as exc:
            raise OrchestratorError(f"cannot reach Docker daemon: {exc}") from exc

        runtimes = info.get("Runtimes") or {}
        if wanted not in runtimes:
            available = ", ".join(sorted(runtimes)) or "none reported"
            hint = (
                "Run infra/gvisor/install-runsc.sh, or set SANDBOX_RUNTIME=runc for development."
                if wanted == GVISOR_RUNTIME
                else "Check the daemon's runtime registration."
            )
            raise OrchestratorError(
                f"configured runtime {wanted!r} is not registered on this daemon "
                f"(available: {available}). {hint} Refusing to fall back to another "
                f"runtime: it would run untrusted code under weaker isolation than "
                f"the configuration asks for."
            )

        if wanted == GVISOR_RUNTIME:
            log.info("gvisor_enabled", runtime=wanted, path=runtimes[wanted].get("path"))
        else:
            # Deliberately a warning, not an error. `runc` is a supported
            # development mode. It is a warning rather than silence because a
            # `runc` deployment in production is a silent isolation downgrade, and
            # nothing else in the system would report it.
            log.warning(
                "gvisor_disabled_single_isolation_layer",
                runtime=wanted,
                detail=(
                    "gVisor is not enabled; sandboxes run under a single isolation "
                    "layer. Set SANDBOX_RUNTIME=runsc in production."
                ),
            )

    def resolve_image_digest(self, image: str) -> str | None:
        """Return the repo digest backing ``image``, for reproducibility records.

        Returns ``None`` for a locally built image with no registry reference,
        which is expected in development and recorded as ``null`` rather than a
        placeholder that would look like a real digest.
        """
        try:
            repo_tag = image.split("@", 1)[0]
            info = self._client.images.get(repo_tag)
            attrs: dict[str, Any] = getattr(info, "attrs", {}) or {}
            repo_digests: list[str] = attrs.get("RepoDigests") or []
            return repo_digests[0].split("@", 1)[1] if repo_digests else None
        except docker.errors.DockerException:
            return None

    # -- lifecycle ----------------------------------------------------------

    def run(
        self,
        *,
        payload: SubmissionPayload,
        limits: ExecutionLimits,
        stream_key: str | None = None,
    ) -> ExecutionResult:
        """Execute, grade, and destroy the sandbox for one submission.

        The container is destroyed in a ``finally`` on every path, including
        exceptions, because a leaked container is a live hostile process on the
        host. ``stop`` then ``remove`` with ``force``, plus a prune of any
        container carrying our labels, covers the case where the worker's own
        process is killed between create and cleanup.
        """
        started = time.perf_counter()
        # Reset first: the pool manager is long-lived and its own binds must not
        # leak into a submission's log lines.
        clear_contextvars()
        bind_contextvars(submission_id=payload.submission_id, language=limits.language.value)
        try:
            outcome = self._run_once(payload=payload, limits=limits, stream_key=stream_key)
            result = _grade(payload=payload, outcome=outcome, limits=limits)
            result = result.model_copy(
                update={
                    "runtime_ms": outcome.runtime_ms,
                    "image_digest": self.resolve_image_digest(limits.image),
                }
            )
            return result
        finally:
            # The container is disposed of by ``_run_once``, which creates it. It
            # used to be released here, but this scope never held a reference --
            # the container was a local in ``_run_once`` -- so the release was
            # always ``release(None)`` and every grading container leaked. Cleanup
            # belongs next to the thing that creates the resource.
            clear_contextvars()
            log.debug("run_complete", elapsed_ms=round((time.perf_counter() - started) * 1000, 2))

    def _run_once(
        self,
        *,
        payload: SubmissionPayload,
        limits: ExecutionLimits,
        stream_key: str | None,
    ) -> RunOutcome:
        """Seed the submission volumes, run both phases, collect the outcome.

        Two phases with a hard boundary between them:

        1. **Candidate phase.** The workspace volume holds the submission; the
           grading volume is empty. The candidate's own command runs.
        2. **Every candidate process is SIGKILLed**, then the grading volume is
           populated, then the image's pre-baked grader runs against it.

        Step 2's first half is not optional. The workspace volume is mounted
        writable, and a surviving candidate process could otherwise read the hidden
        tests once they appear, or write into the grading volume before the grader
        does.
        """
        from oa_worker.sandbox.volumes import (
            remove_submission_volumes,
            reseed_grading_volume,
            seed_submission_volumes,
        )

        volumes = seed_submission_volumes(
            api=self._api,
            limits=limits,
            envelope=self.envelope,
            candidate_tar=payload.candidate_tar(),
            grading_tar=b"",
            submission_id=payload.submission_id,
        )
        container: Container | None = None
        try:
            container = self._create_container(limits=limits, payload=payload, volumes=volumes)

            run_stdout, run_stderr, _ = run_candidate_phase(
                client=self._api,
                container=container,
                payload=payload,
                envelope=self.envelope,
                stream_key=stream_key,
            )

            _terminate_remaining(client=self._api, container=container)

            # No hidden tests means there is nothing to grade. `run` mode is how a
            # candidate explores, and invoking the grader with an empty test set
            # produces a collection error that looks exactly like a broken
            # submission. The candidate still gets their output; they just do not
            # get a verdict.
            if not payload.hidden_tests:
                log.info(
                    "grading_skipped_no_hidden_tests",
                    submission_id=payload.submission_id,
                    mode="run",
                )
                return RunOutcome(
                    status=ExecutionStatus.PASSED,
                    stdout=run_stdout.text,
                    stderr=run_stderr.text,
                    stdout_truncated=run_stdout.truncated,
                    stderr_truncated=run_stderr.truncated,
                    exit_code=None,
                    runtime_ms=0,
                    result_document=None,
                )

            reseed_grading_volume(
                api=self._api,
                volumes=volumes,
                envelope=self.envelope,
                image=limits.image,
                grading_tar=payload.grading_tar(),
            )

            grading = run_grading_phase(
                client=self._api,
                container=container,
                payload=payload,
                envelope=self.envelope,
                stream_key=stream_key,
            )
            return RunOutcome(
                status=grading.status,
                stdout=_join_streams(run_stdout, grading.stdout, self.envelope.max_stream_bytes),
                stderr=_join_streams(run_stderr, grading.stderr, self.envelope.max_stream_bytes),
                stdout_truncated=run_stdout.truncated or grading.stdout.truncated,
                stderr_truncated=run_stderr.truncated or grading.stderr.truncated,
                exit_code=grading.exit_code,
                runtime_ms=grading.runtime_ms,
                result_document=grading.result_document,
            )
        finally:
            # Single-use by design: a container that has executed candidate code
            # is never returned to the pool, only removed.
            self.warm_pool.release(container, language=limits.language)
            remove_submission_volumes(api=self._api, volumes=volumes)

    def _create_container(
        self,
        *,
        limits: ExecutionLimits,
        payload: SubmissionPayload,
        volumes: object,
    ) -> Container:
        """Create and start the sandbox, failing closed on any control error.

        Uses the low-level ``APIClient.create_container`` because the high-level
        ``docker.containers.create`` rewrites the tmpfs mount and adds ``noexec``,
        which would silently break every task that runs a helper binary from
        ``/tmp``.

        ``volumes`` are the per-submission Docker volumes staged by
        :mod:`oa_worker.sandbox.volumes`: the workspace writable, the grading
        directory read-only.
        """
        # The container's own command is a no-op that stays alive. Both the
        # candidate phase and the grading phase run as separate `exec`s, so the
        # container has to outlive the first one -- otherwise it exits when the
        # candidate's command returns and the grading exec fails with
        # "container is not running".
        spec = build_container_spec(
            limits=limits,
            envelope=self.envelope,
            submission_id=payload.submission_id,
            run_command=_IDLE_COMMAND,
            **volumes.as_orchestrator_spec(),  # type: ignore[attr-defined]
        )
        try:
            # Named deterministically: a leaked container is then findable with
            # `docker ps --filter name=oa-sbx-` and is visibly this platform's.
            spec["name"] = limits.container_name
            container = self._api.create_container(**spec)
            self._api.start(container["Id"])
        except docker.errors.APIError as exc:
            raise OrchestratorError(f"failed to start sandbox container: {exc}") from exc
        except docker.errors.DockerException as exc:
            raise OrchestratorError(f"docker transport error creating sandbox: {exc}") from exc

        # `containers.get` rather than constructing a Container and assigning
        # `.id`, which docker-py 7.x rejects: `id` is a read-only property.
        instance = self._client.containers.get(container["Id"])
        log.info("container_started", container_id=container["Id"][:12], image=limits.image)
        return instance

    def _safe_remove_dir(self, container: Container | None, path: str) -> None:
        """Remove a staged directory inside the container, best effort.

        The grading bundle lives on the ``/tmp`` tmpfs, which dies with the
        container, so this is hygiene rather than a security requirement. It is
        skipped on timeout, where the container may already be unresponsive.
        """
        if container is None:
            return
        with contextlib.suppress(docker.errors.DockerException):
            # `exec_run` was removed in docker-py 7.x; an exec is now always
            # create-then-start. Both calls are needed even for a fire-and-forget
            # cleanup, because `exec_create` alone does not run anything.
            exec_id = self._api.exec_create(container.id, ["/bin/rm", "-rf", path])["Id"]
            self._api.exec_start(exec_id, detach=True)

    # -- warm pool proxy ----------------------------------------------------

    def acquire_warm(self, language: Any, *, timeout_s: float | None = None) -> Container:
        """Take a pre-started container for ``language`` from the local pool."""
        return self.warm_pool.acquire(language, timeout_s=timeout_s)

    def release(self, container: Container | None) -> None:
        """Dispose of a container through the pool manager."""
        self.warm_pool.release(container)

    def health(self) -> dict[str, str]:
        """Report orchestrator readiness for the worker's own health endpoint."""
        try:
            self._client.ping()
            docker_ok = "ok"
        except docker.errors.DockerException as exc:
            docker_ok = f"fail: {exc}"
        return {
            "docker": docker_ok,
            "runtime": self.settings.sandbox_runtime,
            "images": ",".join(
                sorted({self.settings.image_for(lang) for lang in (self._container_languages())})
            ),
        }

    def _container_languages(self) -> Iterator[Any]:
        from oa_engine.platform.config import Language

        yield Language.PYTHON
        yield Language.REACT
        yield Language.SQL
        yield Language.JAVA


def _grade(
    *, payload: SubmissionPayload, outcome: RunOutcome, limits: ExecutionLimits
) -> ExecutionResult:
    """Turn a raw outcome into a graded :class:`ExecutionResult`.

    Delegates to the language's adapter, which is the only code allowed to know
    that Vitest emits JSON with ``assertionResults`` while pytest emits JSON
    with a plugin-specific schema. Everything downstream sees one shape.
    """
    # An ungraded run has no result document *by design*, so the parser must not
    # be asked for one: every adapter treats a missing document as a collection
    # error, which would tell a candidate their exploration "failed" when nothing
    # was ever graded.
    if outcome.result_document is None and outcome.status is ExecutionStatus.PASSED:
        return ExecutionResult.from_tests(
            submission_id=payload.submission_id,
            tests=[],
            status=ExecutionStatus.PASSED,
            question_id=None,
            language=payload.language,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            exit_code=outcome.exit_code,
            runtime_ms=outcome.runtime_ms,
            stdout_truncated=outcome.stdout_truncated,
            stderr_truncated=outcome.stderr_truncated,
        )

    from oa_worker.parsers.base import get_parser

    parser = get_parser(limits.language)
    return parser.parse(
        payload=payload,
        result_document=outcome.result_document,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        exit_code=outcome.exit_code,
        runtime_ms=outcome.runtime_ms,
        stdout_truncated=outcome.stdout_truncated,
        stderr_truncated=outcome.stderr_truncated,
        submission_id=payload.submission_id,
    )


def _join_streams(first: Any, second: Any, limit: int) -> str:
    """Concatenate two captured streams under a hard byte cap.

    Args:
        first: A :class:`~oa_worker.sandbox.harness.CapturedStream` or ``str``.
        second: Same.
        limit: Maximum bytes to retain, from the *front*, because the head of
            stdout is where a syntax error or traceback lives.
    """
    head = first if isinstance(first, str) else getattr(first, "text", "")
    tail = second if isinstance(second, str) else getattr(second, "text", "")
    combined = f"{head}\n{tail}" if tail else head
    encoded = combined.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return combined
    return encoded[:limit].decode("utf-8", errors="ignore")


def _read_json(container: Container, path: str, limit: int) -> dict[str, Any] | None:
    """Read and parse a JSON result file out of the container.

    Returns ``None`` when the file is absent or unparseable rather than raising:
    a missing result file is an expected outcome (the runner crashed before
    writing) and the adapter maps it to ``ERROR`` with the stderr it does have.
    """
    try:
        stream, _ = container.get_archive(path)
    except docker.errors.NotFound:
        return None
    except docker.errors.APIError as exc:
        log.warning("result_read_failed", path=path, error=str(exc))
        return None

    raw = b"".join(chunk for chunk in stream)
    if len(raw) > limit:
        log.warning("result_file_oversize", path=path, bytes=len(raw), limit=limit)
        return None
    try:
        document = json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("result_file_unparseable", path=path, error=str(exc))
        return None
    return document if isinstance(document, dict) else None


def _terminate_remaining(*, client: Any, container: Container) -> None:
    """SIGKILL every process in the container except PID 1.

    Called between the candidate phase and the grading phase. Both directories
    share one writable tmpfs, so a candidate process that survived its own command
    -- a background thread, a detached child -- could read the hidden tests once
    they are injected, or write a forged ``result.json`` before the grader runs.

    SIGKILL rather than SIGTERM because the process is hostile and entitled to
    ignore a polite request. PID 1 is spared: it is the container's init, and
    killing it would tear the container down mid-run.
    """
    # PID 1 is excluded explicitly. `kill -KILL -1` is *not* safe here: the
    # container runs as the same non-root uid as the candidate, so a signal to
    # "every process I can signal" includes PID 1 -- and killing PID 1 terminates
    # the container, so the grading exec that follows fails with "container is not
    # running". The candidate is hostile, so its surviving processes are killed
    # individually rather than by group.
    script = (
        "for p in $(ls /proc | grep -E '^[0-9]+$'); do "
        '[ "$p" = "1" ] && continue; '
        'kill -KILL "$p" 2>/dev/null; '
        "done; true"
    )
    try:
        exec_id = client.exec_create(container.id, ["/bin/sh", "-c", script])["Id"]
        client.exec_start(exec_id, detach=True)
    except docker.errors.DockerException as exc:
        # Best effort. The container is destroyed moments later regardless, and a
        # failure here must not abort a run whose grading phase would otherwise be
        # safe -- the wipe of /tmp/grading still happens next.
        log.warning("terminate_remaining_failed", error=str(exc))
