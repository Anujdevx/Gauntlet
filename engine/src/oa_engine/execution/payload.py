"""Submission payloads: candidate code and hidden tests, kept apart.

Lives in ``oa_engine`` rather than in ``oa_worker`` because both sides need it: the
API validates a submission at the edge, and the worker executes it. Putting the
validator behind the worker package would force the API to import it, which drags
Celery and the Docker SDK into the control-plane image -- and the control plane's
entire security argument is that it cannot execute code. One shared validator, two
independently deployable packages, no import edge between the services.

The single most important property of this module is that
:attr:`SubmissionPayload.hidden_tests` and :attr:`SubmissionPayload.files` are
separate fields with separate lifetimes, and the orchestrator only ever places
them in *different container paths at different times*. Concretely:

* Candidate files land at ``/workspace``, writable, and are the only thing
  present when the candidate's own ``run`` command executes.
* Hidden tests land at ``/grading``, which is bind-mounted read-only and which
  the candidate's code has no reference to.

If these two were ever merged into one tarball handed to the container before
the candidate's code runs, the candidate could simply read their own grader.
That is why ``hidden_tests`` is only ever serialised at grading time, and why
:func:`SubmissionPayload.assert_isolated` exists as a runtime guard.
"""

from __future__ import annotations

import io
import json
import tarfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Final

from oa_engine.platform.config import DEFAULT_ENVELOPE, Settings
from oa_engine.platform.logging import get_logger

log = get_logger(__name__, service="oa-worker")

__all__ = [
    "CONTAINER_GRADING_DIR",
    "CONTAINER_WORKSPACE_DIR",
    "PayloadError",
    "SubmissionPayload",
]

#: Where candidate-authored files live inside the sandbox.
#:
#: Under ``/tmp`` rather than ``/workspace``, and that is forced by the container
#: topology rather than chosen. Bind mounts resolve on the *daemon's* filesystem,
#: and under Docker-in-Docker the daemon is a separate container with its own
#: filesystem -- a path the worker creates is simply absent to it, and the daemon
#: rejects the container with "bind source path does not exist". The only writable
#: filesystem available inside the sandbox is the exec-enabled tmpfs at ``/tmp``,
#: so that is where both directories live.
#:
#: The security properties the specification asks for are preserved:
#:
#: * The root filesystem is still read-only. ``/tmp`` is a bounded, memory-backed
#:   tmpfs, not the image.
#: * Hidden tests are still injected only after the candidate's process has
#:   exited, and the grading directory is wiped and recreated at that moment, so
#:   there is no window in which candidate code can read or forge one.
#: * The grading directory is still a distinct path the candidate's code has no
#:   legitimate reason to name.
CONTAINER_WORKSPACE_DIR: Final[str] = "/tmp/workspace"

#: Where hidden tests are placed inside the sandbox. Distinct path, wiped and
#: repopulated between the candidate phase and the grading phase.
CONTAINER_GRADING_DIR: Final[str] = "/tmp/grading"

#: Where the pre-baked images keep their runner entrypoints. Root-owned and not
#: group-writable, so candidate code cannot replace the thing that grades it.
CONTAINER_RUNNER_DIR: Final[str] = "/runner"

#: Where the grader writes its machine-readable result.
#:
#: **Not** inside the grading volume, which is mounted read-only: inputs are
#: immutable, outputs are not. Writing the result next to the tests fails with a
#: read-only filesystem, and suppressing that failure makes every submission look
#: like a collection error. The grading *inputs* therefore stay read-only and the
#: *outputs* go to the writable tmpfs.
CONTAINER_RESULT_DIR: Final[str] = "/tmp/grading-out"
CONTAINER_RESULT_PATH: Final[str] = f"{CONTAINER_RESULT_DIR}/result.json"

#: Longest accepted relative path. Linux caps a path at 4096 bytes; a template
#: never needs more than 512, and a tight bound keeps the rejection log readable.
_MAX_PATH_CHARS: Final[int] = 512


class PayloadError(ValueError):
    """The payload is structurally unusable, e.g. after a traversal attempt.

    Raised before any container is created, so a malformed payload costs no
    sandbox time.
    """


@dataclass(frozen=True, slots=True)
class SubmissionPayload:
    """Immutable bundle of everything needed to grade one submission.

    Constructed by :meth:`load` from a Redis blob written by the API, or
    directly by tests and the load harness.
    """

    submission_id: str
    language: str
    #: Candidate-authored files, relative POSIX path -> UTF-8 text content.
    files: dict[str, str] = field(default_factory=dict)
    #: Hidden test files, relative POSIX path -> text content. Never leaves the
    #: grading boundary.
    hidden_tests: dict[str, str] = field(default_factory=dict)
    #: Command the image's runner executes for this language.
    run_command: tuple[str, ...] = ()
    #: Path, relative to :data:`CONTAINER_GRADING_DIR`, of the machine-readable
    #: result the runner writes.
    result_path: str = "result.json"
    #: Entry file the runner loads first, relative to :data:`CONTAINER_WORKSPACE_DIR`.
    entry_file: str | None = None

    # -- validation ---------------------------------------------------------

    def assert_isolated(self) -> None:
        """Verify the hidden tests cannot be shadowed or reached from workspace.

        Checks, in order of how badly an attacker would want them to pass:

        1. No candidate file path escapes ``/workspace`` (``../``, absolute
           paths, Windows drive letters, NUL bytes, or a symlink-ish segment).
        2. No candidate file collides with the grading mount point.
        3. Candidate file count and total size are inside the envelope.

        This is a hard failure rather than a silent strip. Silently dropping a
        rejected file would let a candidate probe which names are filtered by
        observing that their file "disappeared", which is exactly the oracle we
        do not want.
        """
        limits = DEFAULT_ENVELOPE
        if len(self.files) > limits.max_submission_files:
            raise PayloadError(
                f"submission declares {len(self.files)} files, "
                f"limit is {limits.max_submission_files}"
            )

        total = 0
        for raw_path, content in self.files.items():
            # Relative-path enforcement is what keeps candidate files inside
            # /workspace: an absolute path is refused outright, and ``..`` is
            # refused, so no candidate-supplied name can resolve to /grading or
            # any other mount point. Checking the file *content* for the mount
            # path would prove nothing -- a candidate is of course allowed to
            # write the string "/grading" into their source.
            self._assert_safe_relative_path(raw_path, scope="candidate")
            encoded = content.encode("utf-8")
            total += len(encoded)
            if total > limits.max_submission_bytes:
                raise PayloadError(
                    f"submission exceeds {limits.max_submission_bytes} bytes after {raw_path!r}"
                )

        for raw_path in self.hidden_tests:
            self._assert_safe_relative_path(raw_path, scope="hidden_test")

        if not self.run_command:
            raise PayloadError("payload has no run_command; the image runner must be explicit")

    @staticmethod
    def _assert_safe_relative_path(path: str, *, scope: str) -> None:
        """Reject anything that is not a plain, contained, relative POSIX path.

        Rejects ``..`` segments, absolute paths, drive letters, UNC paths, NUL
        bytes, backslashes, and dot segments. Deliberately conservative: a path
        that a legitimate question template would never produce is refused, and
        the question template author is the one who has to notice.
        """
        if not path or len(path) > _MAX_PATH_CHARS:
            raise PayloadError(f"{scope}: empty or overlong path")
        if "\x00" in path:
            raise PayloadError(f"{scope}: NUL byte in path")
        if "\\" in path:
            raise PayloadError(f"{scope}: backslash in path {path!r}")
        pure = PurePosixPath(path)
        if pure.is_absolute():
            raise PayloadError(f"{scope}: absolute path {path!r} is not allowed")
        if PureWindowsPath(path).drive:
            raise PayloadError(f"{scope}: drive-qualified path {path!r} is not allowed")
        parts = pure.parts
        if not parts:
            raise PayloadError(f"{scope}: path resolves to nothing")
        for part in parts:
            if part in {"..", "."} or part.startswith(".."):
                raise PayloadError(f"{scope}: traversal segment in {path!r}")
            if part.startswith("-") and scope == "run_command":
                raise PayloadError(f"{scope}: leading dash in {path!r} is argument-injection risk")

    # -- serialisation ------------------------------------------------------

    def candidate_tar(self) -> bytes:
        """Build the archive injected at ``/workspace``.

        Explicit ``arcname`` on every member and a hard-coded ``/workspace``
        prefix: the archive is written by us, and a tar member with a
        ``../`` name is a container-write primitive if we let one through.
        Files are added with their own relative name only, never the caller
        supplied path verbatim.
        """
        return _build_tar({name: content.encode("utf-8") for name, content in self.files.items()})

    def grading_tar(self) -> bytes:
        """Build the archive injected at ``/grading``.

        Separate from :meth:`candidate_tar` on purpose so the two can never be
        confused at a call site, and so the grading archive is produced only when
        the candidate's run has already completed.
        """
        return _build_tar(
            {name: content.encode("utf-8") for name, content in self.hidden_tests.items()}
        )

    # -- construction -------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubmissionPayload:
        """Rebuild a payload from its Redis JSON representation.

        Raises:
            PayloadError: On any structural problem. Never partially constructs,
                because a half-built payload is a security hazard: it could look
                valid while missing the hidden tests, and grading would then
                silently pass everything.
        """
        try:
            submission_id = str(data["submission_id"])
            language = str(data["language"])
        except KeyError as exc:
            raise PayloadError(f"payload missing required key {exc}") from exc

        files = _coerce_file_map(data.get("files"), "files")
        hidden = _coerce_file_map(data.get("hidden_tests"), "hidden_tests")
        raw_cmd = data.get("run_command") or ()
        if isinstance(raw_cmd, str):
            run_command: tuple[str, ...] = (raw_cmd,)
        elif isinstance(raw_cmd, list | tuple):
            run_command = tuple(str(part) for part in raw_cmd)
        else:
            raise PayloadError(f"run_command must be a string or sequence, got {type(raw_cmd)}")

        payload = cls(
            submission_id=submission_id,
            language=language,
            files=files,
            hidden_tests=hidden,
            run_command=run_command,
            result_path=str(data.get("result_path") or "result.json"),
            entry_file=(str(data["entry_file"]) if data.get("entry_file") else None),
        )
        payload.assert_isolated()
        return payload

    @classmethod
    def load(cls, *, settings: Settings, key: str) -> SubmissionPayload:
        """Fetch and validate a payload from Redis by key.

        The blob is deleted immediately after a successful load. Rationale: the
        payload contains both candidate code and hidden tests, and leaving it
        resident in Redis for the full TTL widens the window in which a Redis
        dump exposes every grader in the system. A redelivered Celery task that
        finds no payload reports infrastructure failure rather than re-running
        against stale content.
        """
        import redis

        # ``payload_url``, not ``redis_url``: the API writes payloads to the
        # dedicated payload database, and reading from the broker's database is
        # how a submission silently disappears with no error on either side.
        client = redis.Redis.from_url(settings.payload_url(), socket_timeout=5)
        try:
            blob = client.get(key)
        finally:
            client.close()

        if blob is None:
            raise PayloadError(f"payload key {key!r} not found in Redis")
        return cls.from_dict(json.loads(blob))

    @classmethod
    def forget(cls, *, settings: Settings, key: str) -> bool:
        """Best-effort delete of a payload blob. Returns whether it existed."""
        import redis

        client = redis.Redis.from_url(settings.payload_url(), socket_timeout=5)
        try:
            return bool(client.delete(key))
        finally:
            client.close()

    def as_dict(self) -> dict[str, Any]:
        """Serialise back to the Redis representation.

        Includes ``hidden_tests``. Callers must not hand this to anything the
        browser or the candidate can read; the only legitimate destinations are
        Redis and the grading step inside the sandbox.
        """
        return {
            "submission_id": self.submission_id,
            "language": self.language,
            "files": dict(self.files),
            "hidden_tests": dict(self.hidden_tests),
            "run_command": list(self.run_command),
            "result_path": self.result_path,
            "entry_file": self.entry_file,
        }


def _build_tar(members: dict[str, bytes]) -> bytes:
    """Serialise ``members`` into an uncompressed tar with deterministic order.

    ``mtime=0`` and a fixed ``uid``/``gid``/``uname``/``gname`` make the archive
    byte-identical for identical input, which lets the load tests compare
    payloads and lets the submission hash be a stable content address.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(members):
            data = members[name]
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0
            # 0644, not 0400. The seeder extracts these as root, so the sandbox's
            # non-root user needs the read bit; the write bit is withheld so the
            # candidate cannot modify its own submission or its grader.
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _coerce_file_map(raw: Any, field_name: str) -> dict[str, str]:
    """Validate a ``{path: content}`` mapping from untrusted JSON.

    Path safety is deliberately *not* checked here beyond type, because the
    caller may legitimately stage the payload before validation; the authoritative
    check is :meth:`SubmissionPayload.assert_isolated`, which runs on every
    construction path.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PayloadError(f"{field_name} must be an object, got {type(raw)}")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise PayloadError(f"{field_name} must map str to str")
        out[key] = value
    return out
