"""Driving a container: staged files, two-phase execution, hard timeouts.

Two responsibilities:

* **Staging.** Turn a :class:`SubmissionPayload` into tar archives on a
  private, per-worker temp directory that is never bind-mounted.
* **Execution.** Run a command inside the container against a wall-clock
  deadline, with output size caps, and return what happened.

The wall clock is enforced here with a polling deadline and ``SIGKILL``,
because a hostile process is entitled to ignore ``SIGTERM`` and Docker's own
``stop_timeout`` is not a guarantee. ``SIGKILL`` cannot be caught, blocked, or
traded, which is the property we need.

``exec_start(stream=True)`` returns an *iterator* that drains lazily, so its
per-read timeout cannot be controlled. Passing ``socket=True`` instead returns a
raw socket, which is what lets each ``recv`` carry its own short timeout while
the loop checks a monotonic deadline on every iteration. That is a stronger guarantee than
an HTTP timeout, because it is measured against our own clock rather than the
client's, and it cannot be reset by a slow byte arriving just often enough.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Final

import docker.errors
from docker.api.container import ContainerApiMixin

from oa_engine.execution import ExecutionStatus
from oa_engine.execution.payload import (
    CONTAINER_GRADING_DIR,
    CONTAINER_WORKSPACE_DIR,
    SubmissionPayload,
)
from oa_engine.platform.config import SandboxEnvelope
from oa_engine.platform.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from docker.models.containers import Container

__all__ = [
    "CapturedStream",
    "ExecStream",
    "ExecStreamProtocolError",
    "PhaseOutcome",
    "StagedWorkspace",
    "inject_archive",
    "prepare_workspace",
    "reset_grading_directory",
    "run_candidate_phase",
    "run_grading_phase",
    "stage_grading_bundle",
]

log = get_logger(__name__, service="oa-worker")

#: Grace period between the deadline firing and SIGKILL. Short, because the
#: kill is not a negotiation -- SIGTERM only exists to let cooperative cleanup
#: (flushing the result file) succeed.
_SIGTERM_GRACE_S: Final[float] = 1.0

#: How often the deadline loop polls. 50 ms keeps the timeout accurate to well
#: under the 3 s end-to-end SLO without generating meaningful overhead.
_DEADLINE_POLL_S: Final[float] = 0.05

#: Chunk size for a single socket read off the exec stream.
_READ_CHUNK_BYTES: Final[int] = 65_536

#: Delimiters framing the grader's result document on stdout. Candidate code
#: controls stdout too, so the document is bracketed rather than bare -- otherwise
#: a candidate who printed their own JSON could inject a passing result.
_RESULT_BEGIN = "OA_RESULT_BEGIN"
_RESULT_END = "OA_RESULT_END"

#: stdcopy stream identifiers in Docker's multiplexed exec framing. 1 selects
#: stdout, 2 selects stderr; any other value is a protocol error rather than
#: something to guess at.
_STDOUT_STREAM_ID: Final[int] = 1
_STDERR_STREAM_ID: Final[int] = 2

#: Hard ceiling on bytes pulled off the stream before we stop reading. Four times
#: the per-stream cap: enough headroom that a legitimate large result still fits,
#: small enough that an output flood is cut off well inside the wall clock.
_STREAM_HARD_CAP_FACTOR: Final[int] = 4

#: Exit code ``timeout(1)`` produces. The only exit code we map to TIMEOUT.
_EXIT_CODE_TIMEOUT: Final[int] = 124


@dataclass(slots=True)
class CapturedStream:
    """One captured output stream, with its truncation state preserved.

    Truncation is recorded rather than silently applied because a candidate who
    floods stdout should be told the view is partial. Silently cutting at 256
    KiB would make an infinite ``print`` loop look like a complete run.
    """

    text: str = ""
    truncated: bool = False

    @classmethod
    def capture(cls, chunks: list[bytes | None], limit: int) -> CapturedStream:
        """Decode and cap a demuxed stream.

        Args:
            chunks: Raw byte chunks from ``exec_run(demux=True)``.
            limit: Byte budget. Retained from the front: the head of a stream is
                where the useful diagnostic lives.
        """
        raw = b"".join(c for c in chunks if c)
        if len(raw) <= limit:
            return cls(text=raw.decode("utf-8", errors="replace"), truncated=False)
        kept = raw[:limit].decode("utf-8", errors="ignore")
        dropped = len(raw) - limit
        return cls(
            text=f"{kept}\n[truncated: {dropped} more bytes suppressed by the output cap]",
            truncated=True,
        )


class ExecStreamProtocolError(RuntimeError):
    """The daemon sent an exec frame we cannot interpret.

    Raised rather than handled: an unknown stdcopy stream id means we cannot tell
    which bucket the payload belongs to, and putting a candidate's stderr into
    stdout (or vice versa) would corrupt a grading result. Failing loudly is the
    only safe disposition.
    """


@dataclass(slots=True)
class PhaseOutcome:
    """Result of one execution phase."""

    status: ExecutionStatus
    stdout: CapturedStream = field(default_factory=CapturedStream)
    stderr: CapturedStream = field(default_factory=CapturedStream)
    exit_code: int | None = None
    runtime_ms: int = 0
    result_document: dict[str, Any] | None = None
    timed_out: bool = False


@dataclass(slots=True)
class StagedWorkspace:
    """The two archives staged on the host for injection into the sandbox.

    ``put_archive`` into the container's exec-enabled ``/tmp`` rather than a bind
    mount. See :data:`CONTAINER_WORKSPACE_DIR` for why bind mounts are not usable
    under Docker-in-Docker.
    """

    root: str
    candidate_tar: str
    candidate_tar_bytes: int

    def cleanup(self) -> None:
        """Remove the staged archive, tolerating a partially written file."""
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> StagedWorkspace:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.cleanup()


def prepare_workspace(payload: SubmissionPayload) -> StagedWorkspace:
    """Validate the submission and stage its archive on the host.

    Args:
        payload: The validated submission.

    Returns:
        The staged candidate archive.

    Raises:
        PayloadError: If validation fails, before any container is created.
    """
    payload.assert_isolated()
    root = tempfile.mkdtemp(prefix=f"oa-sub-{payload.submission_id[:12]}-")
    tar_path = Path(root) / "workspace.tar"
    data = payload.candidate_tar()
    tar_path.write_bytes(data)
    log.debug("workspace_staged", files=len(payload.files), bytes=len(data))
    return StagedWorkspace(root=root, candidate_tar=str(tar_path), candidate_tar_bytes=len(data))


def stage_grading_bundle(payload: SubmissionPayload) -> str:
    """Stage the hidden-test archive.

    Injected only after the candidate's process has exited, and into a directory
    that is wiped and recreated immediately beforehand. Together those two facts
    are what make "the candidate cannot read or forge their grader" structural
    rather than a matter of timing.

    Args:
        payload: The submission whose hidden tests to stage.

    Returns:
        The host path of the staged grading archive.
    """
    root = tempfile.mkdtemp(prefix=f"oa-grade-{payload.submission_id[:12]}-")
    tar_path = Path(root) / "grading.tar"
    tar_path.write_bytes(payload.grading_tar())
    return str(tar_path)


def _exec_with_deadline(
    *,
    client: ContainerApiMixin,
    container: Container,
    command: list[str],
    envelope: SandboxEnvelope,
    workdir: str,
    env_extra: dict[str, str] | None = None,
) -> tuple[PhaseOutcome, list[bytes | None], list[bytes | None]]:
    """Execute ``command`` under a hard wall clock and output caps.

    Args:
        client: Low-level container API client.
        container: The live sandbox.
        command: Fully resolved argv. Never built by string interpolation.
        envelope: Supplies the wall clock and stream caps.
        workdir: Working directory inside the container.
        env_extra: Additional environment for this exec only. Kept separate from
            the container env so a phase cannot poison the next one.

    Returns:
        ``(outcome, raw_stdout_chunks, raw_stderr_chunks)``. The raw chunks are
        returned so a caller can apply a *different* cap per phase without
        re-reading the stream.
    """
    deadline = time.monotonic() + envelope.wall_clock_timeout_s
    env = _resolve_env(container, env_extra)

    started = time.perf_counter()
    timed_out = False
    exit_code: int | None = None
    stdout_chunks: list[bytes | None] = []
    stderr_chunks: list[bytes | None] = []
    stderr_text = ""

    try:
        # No ``socket_read_timeout`` here: docker-py 7.x removed it from
        # ``exec_create``/``exec_start``. Socket read timeouts are ours to set on
        # each ``recv``, which is what the deadline loop below does -- a stricter
        # guarantee anyway, since it is measured against our own monotonic clock
        # rather than the HTTP client's.
        exec_result = client.exec_create(
            container.id,
            command,
            workdir=workdir,
            environment=env,
            stdout=True,
            stderr=True,
            tty=False,
        )
    except (docker.errors.APIError, docker.errors.DockerException) as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return (
            PhaseOutcome(
                status=ExecutionStatus.INFRA_ERROR,
                stderr=CapturedStream(text=f"failed to create exec: {exc}"),
                runtime_ms=elapsed,
            ),
            [],
            [],
        )

    exec_id = exec_result["Id"]
    try:
        while True:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            try:
                # `socket=True` returns a raw socket, which is the only form that
                # supports a per-read timeout. `ExecStream` unwraps it and
                # demultiplexes Docker's stdcopy framing. See ExecStream for why
                # neither of those is available from the default stream API.
                sock = ExecStream(client.exec_start(exec_id, stream=True, socket=True, demux=True))
            except (docker.errors.APIError, docker.errors.DockerException):
                # The daemon can refuse or drop an exec while the sandbox is still
                # starting. Retry until the deadline, then escalate to a timeout.
                continue

            try:
                for out, err in _iter_with_deadline(sock, deadline, envelope):
                    if out:
                        stdout_chunks.append(out)
                    if err:
                        stderr_chunks.append(err)
            finally:
                sock.close()

            if time.monotonic() < deadline:
                break

        if timed_out:
            _terminate_exec(client, container, exec_id, deadline)
            exit_code = None
            stderr_text = (
                f"Execution exceeded the hard wall-clock limit of "
                f"{envelope.wall_clock_timeout_s}s and was killed (SIGKILL)."
            )
            status = ExecutionStatus.TIMEOUT
        else:
            inspect = client.exec_inspect(exec_id)
            exit_code = int(inspect.get("ExitCode") or 0)
            status = _status_for_exit(exit_code)
    finally:
        _best_effort_exec_remove(client, exec_id)

    elapsed = int((time.perf_counter() - started) * 1000)
    outcome = PhaseOutcome(
        status=status,
        stdout=CapturedStream.capture(stdout_chunks, envelope.max_stream_bytes),
        stderr=CapturedStream.capture(stderr_chunks, envelope.max_stream_bytes),
        exit_code=exit_code,
        runtime_ms=elapsed,
        timed_out=timed_out,
    )
    if timed_out:
        outcome.stderr = CapturedStream(text=stderr_text, truncated=False)
    log.debug("phase_exec_complete", exit_code=exit_code, timed_out=timed_out, elapsed_ms=elapsed)
    return outcome, stdout_chunks, stderr_chunks


class ExecStream:
    """A stdcopy-demultiplexed exec stream with a per-read timeout.

    Exists because ``docker.api.client.APIClient.exec_start`` has no usable
    timeout or demultiplexing story at the same time:

    * ``stream=True`` alone returns a ``CancellableStream`` iterator. Draining it
      is a single blocking call, so a wall-clock deadline can only be checked
      *after* the entire output has arrived -- which for a hostile process means
      after it has already consumed the whole budget.
    * ``socket=True`` gives a raw socket, so the deadline *is* enforceable per
      read, but the caller then receives Docker's multiplexed frames verbatim and
      must demultiplex them.

    Taking the socket and demultiplexing here gets both properties, and gets them
    in one place. Getting stdout and stderr attributed correctly is not cosmetic:
    the terminal panel renders them separately, and a candidate who writes to
    stderr must not have their error output appear as program output.

    Frame format (Docker's "stdcopy" multiplexing)::

        +--------+--------+--------+--------+--------+--------+--------+--------+
        | stream | 0     | 0     | 0     |          length (BE)           |
        +--------+--------+--------+--------+--------+--------+--------+--------+
        |                    payload ...                                        |
        +-----------------------------------------------------------------------+

    where ``stream`` is 1 for stdout and 2 for stderr.

    The SDK's private ``_sock`` attribute is reached around once, here, so an SDK
    upgrade breaks one method rather than the entire timeout mechanism. The
    fallback keeps it working if that attribute moves.
    """

    __slots__ = ("_buffer", "_sock")

    #: Header length of one stdcopy frame.
    _HEADER: Final[int] = 8

    def __init__(self, raw: Any) -> None:
        """Wrap whatever ``exec_start(socket=True)`` returned.

        Args:
            raw: The SDK's return value: a ``SocketIO``, or in some versions an
                already-unwrapped socket.
        """
        sock = getattr(raw, "_sock", None)
        self._sock: Any = sock if sock is not None else raw
        #: Bytes read but not yet forming a complete frame. A partial frame is
        #: never dropped: discarding it would silently corrupt output at an
        #: arbitrary chunk boundary, which for a candidate's compiler diagnostics
        #: is the difference between a usable error and a mystery.
        self._buffer = bytearray()

    def recv(self, size: int, *, timeout: float) -> tuple[bytes | None, bytes | None] | None:
        """Read up to ``size`` raw bytes and return complete frame payloads.

        Args:
            size: Maximum bytes to read from the socket.
            timeout: Socket timeout in seconds. A timeout is a *readiness*
                signal rather than an error: the process may simply not be
                writing yet, so this returns ``b""`` and lets the caller re-check
                its deadline instead of treating silence as failure.

        Returns:
            ``(stdout_bytes, stderr_bytes)`` with at most one side populated,
            ``(b"", b"")`` on a read timeout, or ``None`` at end of stream.

        Raises:
            ExecStreamProtocolError: If a frame carries an unknown stream id. That
                means the daemon is speaking something we do not understand, and
                guessing which bucket the bytes belong to would corrupt a grading
                result -- so it is a hard error instead.
        """
        try:
            data = self._sock.recv(size)
        except (TimeoutError, OSError):
            return b"", b""

        if not data:
            return None

        self._buffer.extend(data)
        stdout = bytearray()
        stderr = bytearray()

        while len(self._buffer) >= self._HEADER:
            stream_id = self._buffer[0]
            length = int.from_bytes(bytes(self._buffer[4:8]), "big")
            if len(self._buffer) < self._HEADER + length:
                break  # frame still arriving
            payload = bytes(self._buffer[self._HEADER : self._HEADER + length])
            del self._buffer[: self._HEADER + length]
            if stream_id == _STDOUT_STREAM_ID:
                stdout.extend(payload)
            elif stream_id == _STDERR_STREAM_ID:
                stderr.extend(payload)
            else:
                raise ExecStreamProtocolError(
                    f"unknown stdcopy stream id {stream_id}; refusing to guess which "
                    f"stream the payload belongs to"
                )

        if not stdout and not stderr:
            return b"", b""
        return bytes(stdout), bytes(stderr)

    def close(self) -> None:
        """Close the underlying socket, ignoring an already-closed stream."""
        with contextlib.suppress(OSError):
            self._sock.close()


def _iter_with_deadline(sock: ExecStream, deadline: float, envelope: SandboxEnvelope) -> Any:
    """Yield demuxed chunks until EOF, the deadline, or the byte cap is hit.

    The generator is bounded rather than open-ended so a process emitting data
    forever cannot keep us in this loop past the wall clock. Once the cap is
    reached the socket is closed by the caller, which makes the daemon kill the
    exec, which is the intended behaviour for an output flood.
    """
    total = 0
    while True:
        if time.monotonic() >= deadline:
            log.warning("output_stream_cut_by_deadline")
            return
        frame = sock.recv(_READ_CHUNK_BYTES, timeout=_DEADLINE_POLL_S * 2)
        if frame is None:
            return  # end of stream
        out, err = frame
        total += len(out or b"") + len(err or b"")
        if total >= envelope.max_stream_bytes * _STREAM_HARD_CAP_FACTOR:
            log.warning("output_stream_exceeded_hard_cap", bytes=total)
            return
        yield out, err


def _resolve_env(container: Container, env_extra: dict[str, str] | None) -> dict[str, str]:
    """Merge the container's inherited environment with per-exec additions.

    Docker reports a container's environment as a list of ``"KEY=VALUE"`` strings,
    not a mapping, so ``dict()`` on it raises ``ValueError``. Parsed explicitly.

    The inherited variables matter: the base image's ``ENV`` is merged by the
    daemon at create time and cannot be removed via the API. That is why the
    no-secrets guarantee is enforced by review on our own images rather than by
    this function.
    """
    raw = (getattr(container, "attrs", {}) or {}).get("Config", {}).get("Env") or []
    base: dict[str, str] = {}
    for entry in raw:
        name, sep, value = str(entry).partition("=")
        if sep and name:
            base[name] = value
    if env_extra:
        base.update(env_extra)
    return base


def _status_for_exit(exit_code: int) -> ExecutionStatus:
    """Map a runner's exit code onto a coarse execution status.

    Deliberately coarse. The *grading* verdict comes from per-test booleans in
    the result document, never from this code: a non-zero exit with all tests
    passing is a real scenario (the runner's own teardown failed), and mapping
    exit codes to pass/fail is exactly the string-and-exit-code grading the
    spec forbids.
    """
    if exit_code == 0:
        return ExecutionStatus.PASSED
    # 137 = 128 + SIGKILL, 152 = 128 + SIGTERM: the runtime killed the process,
    # which on this platform means a resource limit rather than a test failure.
    if exit_code in (137, 152):
        return ExecutionStatus.RESOURCE_EXCEEDED
    if exit_code == _EXIT_CODE_TIMEOUT:
        return ExecutionStatus.TIMEOUT
    return ExecutionStatus.ERROR


def _terminate_exec(
    client: ContainerApiMixin,
    container: Container,
    exec_id: str,
    deadline: float,
) -> None:
    """Escalate SIGTERM to SIGKILL against the container's PID 1.

    We signal the whole container rather than only the exec because a candidate
    that forked has descendants, and a process group inside the container is not
    addressable from the daemon API. SIGKILL is sent to PID 1, which the kernel
    and gVisor deliver to every process in the PID namespace.
    """
    grace_until = deadline + _SIGTERM_GRACE_S
    try:
        client.exec_start(
            client.exec_create(container.id, ["/bin/kill", "-TERM", "1"], privileged=False)["Id"],
            detach=True,
        )
    except (docker.errors.APIError, docker.errors.DockerException) as exc:
        log.debug("sigterm_failed_going_straight_to_kill", error=str(exc))

    while time.monotonic() < grace_until:
        inspect = client.exec_inspect(exec_id)
        if not inspect.get("Running"):
            log.warning("container_exited_on_sigterm")
            return
        time.sleep(_DEADLINE_POLL_S)

    try:
        client.exec_start(
            client.exec_create(container.id, ["/bin/kill", "-KILL", "1"], privileged=False)["Id"],
            detach=True,
        )
        log.warning("container_killed_sigkill", exec_id=exec_id[:12])
    except (docker.errors.APIError, docker.errors.DockerException) as exc:
        # SIGKILL failing to send is close to fatal: the container is stuck and
        # will be force-removed by the orchestrator's cleanup path.
        log.exception("sigkill_failed", exec_id=exec_id[:12], error=str(exc))


def _best_effort_exec_remove(client: ContainerApiMixin, exec_id: str) -> None:
    """Drop the exec record. Failure is harmless; skip it and log at debug."""
    try:
        client.exec_resize  # noqa: B018 - attribute probe for older SDKs
        client.exec_remove(exec_id)
    except AttributeError:
        log.debug("exec_remove_unsupported_by_sdk")
    except (docker.errors.APIError, docker.errors.DockerException) as exc:
        log.debug("exec_remove_failed", exec_id=exec_id[:12], error=str(exc))


def inject_archive(
    *,
    client: ContainerApiMixin,
    container: Container,
    archive_path: str,
    target: str,
) -> int:
    """Unpack a staged archive into ``target`` inside the container.

    Uses ``put_archive``, which writes *through* the container's root filesystem.
    That is why ``target`` must be inside the writable tmpfs rather than
    ``/workspace``: against ``read_only=True`` the daemon refuses with
    ``container rootfs is marked read-only``. The read-only rootfs is the stronger
    control, so the tmpfs is where the work happens.

    Args:
        client: Low-level container API client.
        container: The live sandbox.
        archive_path: Host path of the staged ``.tar``.
        target: Absolute directory inside the container.

    Returns:
        The number of bytes injected.

    Raises:
        OrchestratorError: If the archive cannot be read or injected.
    """
    from oa_worker.sandbox.orchestrator import OrchestratorError

    try:
        data = Path(archive_path).read_bytes()
    except OSError as exc:
        raise OrchestratorError(f"staged archive {archive_path} is unreadable: {exc}") from exc

    try:
        client.put_archive(container.id, target, data)
    except docker.errors.APIError as exc:
        raise OrchestratorError(f"failed to inject archive into {target}: {exc}") from exc
    return len(data)


def reset_grading_directory(*, client: ContainerApiMixin, container: Container) -> None:
    """Wipe and recreate ``/tmp/grading`` between the two phases.

    The directory shares a writable tmpfs with the candidate's workspace, so
    anything the candidate left behind -- a forged ``result.json``, a symlink
    pointing at the workspace -- would survive into grading. Removing the whole
    directory and recreating it is the only way to be sure it is empty.

    Failures are fatal to the run rather than logged. A grading directory that
    could not be reset may contain attacker-controlled content, and grading anyway
    would let that content reach the grader.
    """
    from oa_worker.sandbox.orchestrator import OrchestratorError

    script = (
        f"rm -rf {CONTAINER_GRADING_DIR} && "
        f"mkdir -p {CONTAINER_GRADING_DIR} && "
        f"chmod 700 {CONTAINER_GRADING_DIR}"
    )
    try:
        exec_id = client.exec_create(
            container.id, ["/bin/sh", "-c", script], stdout=True, stderr=True
        )["Id"]
        client.exec_start(exec_id, detach=True)
    except docker.errors.DockerException as exc:
        raise OrchestratorError(
            f"cannot reset the grading directory; refusing to grade with a "
            f"candidate-writable path still present: {exc}"
        ) from exc
    log.debug("grading_dir_reset", target=CONTAINER_GRADING_DIR)


def run_candidate_phase(
    *,
    client: ContainerApiMixin,
    container: Container,
    payload: SubmissionPayload,
    envelope: SandboxEnvelope,
    stream_key: str | None = None,
) -> tuple[CapturedStream, CapturedStream, int | None]:
    """Run the candidate's own code with **no hidden tests present**.

    This is the "Run" button in the IDE. The grading mount is empty here, so a
    candidate cannot read, log, or tamper with the tests they are about to be
    marked against, and a naive ``print(open('/grading/test_x.py').read())``
    yields nothing at all.
    """
    command = payload.run_command or ("/runner/run-python.sh",)
    if stream_key:
        log.info("stream_attached", phase="candidate", stream_key=stream_key[:16])
    outcome, _, _ = _exec_with_deadline(
        client=client,
        container=container,
        command=list(command),
        envelope=envelope,
        workdir=CONTAINER_WORKSPACE_DIR,
    )
    return outcome.stdout, outcome.stderr, outcome.exit_code


def run_grading_phase(
    *,
    client: ContainerApiMixin,
    container: Container,
    payload: SubmissionPayload,
    envelope: SandboxEnvelope,
    stream_key: str | None = None,
) -> PhaseOutcome:
    """Inject nothing further and run the image's grader against ``/grading``.

    The grader is the image's pre-baked runner. It is executed from
    :data:`~oa_engine.execution.payload.CONTAINER_RUNNER_DIR`, which is part of the
    read-only root filesystem, so the candidate cannot replace it.
    """
    grader = _grader_argv(payload)
    outcome, _, _ = _exec_with_deadline(
        client=client,
        container=container,
        command=grader,
        envelope=envelope,
        workdir=CONTAINER_GRADING_DIR,
        env_extra={"OA_GRADING": "1"},
    )
    outcome.result_document = _read_result_document(outcome.stdout.text)
    if stream_key:
        log.info("stream_attached", phase="grading", stream_key=stream_key[:16])
    return outcome


def _grader_argv(payload: SubmissionPayload) -> list[str]:
    """Resolve the grader argv for the payload's language.

    Dispatch is by the payload's declared language rather than by file
    extension, because the template author knows the language and a candidate
    cannot influence this field.
    """
    scripts: dict[str, list[str]] = {
        "python": ["/runner/grade-python.sh"],
        "react": ["/runner/grade-react.sh"],
        "sql": ["/runner/grade-sql.sh"],
    }
    try:
        return scripts[payload.language]
    except KeyError as exc:
        raise ValueError(
            f"no grader for language {payload.language!r}; known: {', '.join(sorted(scripts))}"
        ) from exc


def _read_result_document(stdout: str) -> dict[str, Any] | None:
    """Extract the grader's result document from stdout.

    Read from stdout rather than from the container's filesystem. ``get_archive``
    reads the container's *root filesystem layer*, so it cannot see the ``/tmp``
    tmpfs -- where the grader must write, because the grading volume is mounted
    read-only. Asking the daemon for a tmpfs file returns 404 for a file that
    exists, which is exactly the kind of failure that looks like "the grader never
    ran".

    Args:
        stdout: Combined stdout of the grading phase.

    Returns:
        The parsed document, or ``None`` if the markers are absent or the body is
        not valid JSON. Never raises: a missing document maps to a reported
        ``ERROR`` with the stderr we do have, which is more useful than an
        exception.
    """
    begin = stdout.rfind(_RESULT_BEGIN)
    end = stdout.rfind(_RESULT_END)
    if begin == -1 or end == -1 or end < begin:
        log.warning("result_markers_absent", exit_hint="the grader did not emit a result")
        return None

    body = stdout[begin + len(_RESULT_BEGIN) : end].strip()
    if not body:
        log.warning("result_document_empty")
        return None
    try:
        document = json.loads(body)
    except json.JSONDecodeError as exc:
        log.warning("result_document_unparseable", error=str(exc), bytes=len(body))
        return None
    return document if isinstance(document, dict) else None
