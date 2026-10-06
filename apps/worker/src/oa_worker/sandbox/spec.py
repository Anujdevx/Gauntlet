"""The container creation request: image config plus a :class:`HostConfig`.

Everything security-relevant lives in the ``HostConfig`` half of the request,
because that is where the Docker Engine API actually reads resource and
privilege limits from. Splitting the two explicitly, rather than passing one flat
dict, is what makes the security surface reviewable: an auditor can read
:func:`build_host_config` and see the entire containment policy in one function.

Why the low-level client, and how ``/tmp`` is made executable
-----------------------------------------------------------

``docker.containers.create`` (the high-level helper) cannot express an
executable tmpfs, because ``docker.types.Mount`` with ``TmpfsOptions`` hardcodes
``noexec``. That breaks any task that compiles or runs a helper binary from a
temp directory.

The obvious next move -- pass a raw mount *dictionary* to the low-level
:class:`docker.api.client.APIClient` and simply omit ``noexec`` -- **does not
work**, and this was measured rather than assumed:

===========================  ==========================================
Request form                 Result
===========================  ==========================================
``Type: tmpfs`` Mount entry  ``noexec``. Not overridable. ``Noexec: false``
                             and ``noexec: false`` are both ignored, because
                             the daemon injects the flag after parsing
                             ``TmpfsOptions``.
``HostConfig.Tmpfs`` map,    **exec permitted.** The value is a raw mount
options string without       options string, so an explicit ``exec`` wins.
``exec``
===========================  ==========================================

So :func:`build_tmpfs_mount_spec` returns the option string and the mount is
applied through ``HostConfig.Tmpfs``. Both variants are pinned by
``sandbox/tests/test_tmpfs_exec.py``, which also asserts that the ``Type: tmpfs``
form still fails -- so if a future Docker release changes the behaviour, the test
that documented the trap tells you the workaround is no longer needed.

This is the one place in the codebase where the implementation follows measured
daemon behaviour rather than the API's apparent surface, and the comments say so
because the next person to "simplify" it back to a Mount dict will break every
task that shells out to a compiled helper.

SDK shape, pinned deliberately
------------------------------

The kwargs accepted by ``APIClient.create_container`` differ across SDK major
versions: 7.x moved every containment knob from ``create_container`` into
``HostConfig``. Constructing a real :class:`docker.types.HostConfig` means the SDK
validates the field names against its own schema at build time, so an SDK upgrade
that renames one fails loudly in a unit test instead of silently dropping a
security control at container-creation time.
"""

from __future__ import annotations

from typing import Any, Final

from docker.types import HostConfig

from oa_engine.execution.payload import (
    CONTAINER_GRADING_DIR,
    CONTAINER_RESULT_PATH,
    CONTAINER_RUNNER_DIR,
    CONTAINER_WORKSPACE_DIR,
)
from oa_engine.platform.config import GVISOR_RUNTIME, ExecutionLimits, SandboxEnvelope

__all__ = [
    "CONTAINER_RESULT_PATH",
    "SANDBOX_TMP_TARGET",
    "assert_gvisor_is_second_layer",
    "build_container_spec",
    "build_host_config",
    "build_tmpfs_mount_spec",
    "runtime_controls",
    "sandbox_environment",
]

#: Docker Engine API version negotiated for sandbox containers. Pinned rather
#: than "auto" so a daemon upgrade cannot change the shape of a request that
#: carries the containment policy.
ENGINE_API_VERSION: Final[str] = "1.44"

#: Where the executable memory-backed scratch mount is applied.
SANDBOX_TMP_TARGET: Final[str] = "/tmp"

#: Loopback only. Docker's ``none`` network still provides a loopback
#: interface, which most RAG and HTTP-client mocks require, so this is not a
#: limitation in practice.
SANDBOX_NETWORK_MODE: Final[str] = "none"


def build_tmpfs_mount_spec(envelope: SandboxEnvelope) -> str:
    """Return the ``HostConfig.Tmpfs`` options string for ``/tmp``.

    Applied through :attr:`docker.types.HostConfig.Tmpfs` rather than as a
    ``Type: tmpfs`` Mount entry, because only this form lets an explicit ``exec``
    override the daemon's default. See the module docstring for the measurement.

    The flags, and why each is here:

    ``rw``
        Writable scratch, which is the whole purpose. Read-only ``/tmp`` breaks
        Python's own machinery, since ``HOME`` and ``TMPDIR`` both point there.
    ``size=<bytes>``
        Enforced by the kernel against the cgroup, so a ``/tmp`` flood fails
        cleanly on allocation instead of reaching the host page cache.
    ``mode=1777``
        Matches real ``/tmp``: world-writable and sticky. The sticky bit matters
        because several processes inside one sandbox share the mount, and a
        candidate's child process must not be able to unlink a sibling's file.
    ``exec``
        The reason this function exists. Requested explicitly because absence is
        not enough -- the daemon's default is ``noexec``.
    ``nosuid``, ``nodev``
        Requested. Neither costs a legitimate sandbox anything: no task runs a
        setuid binary from a temp directory, and no task needs to create a device
        node. Dropping them would be free attack surface.
    """
    if not envelope.tmp_exec:
        raise ValueError(
            "envelope.tmp_exec=False is not a supported configuration. The "
            "platform requires an executable /tmp because question templates "
            "legitimately compile and invoke helper binaries; a noexec /tmp is "
            "a deployment mistake, not a knob."
        )
    return f"rw,exec,nosuid,nodev,size={envelope.tmp_size_bytes},mode={envelope.tmp_mode:o}"


def build_host_config(
    *,
    envelope: SandboxEnvelope,
    workspace_source: str | None = None,
    grading_source: str | None = None,
) -> HostConfig:
    """Construct the complete containment policy as a :class:`HostConfig`.

    Every field here is asserted twice: once as a unit test over the returned
    object, and once as a live test that inspects a real container's
    ``HostConfig``. Both are needed, because the gap between what we ask for and
    what the daemon honours is exactly where a silent regression would hide.

    Args:
        envelope: The immutable security policy. Read-only; never mutated.
        workspace_source: Docker volume holding the candidate's files, mounted
            writable. ``None`` omits the mount -- which is what a *warm pool*
            container gets, so a pooled container provably holds no submission data.
        grading_source: Docker volume holding the hidden tests, mounted
            **read-only** so the candidate cannot write to its own grader.

    Volumes rather than host bind mounts because a bind source resolves on the
    *daemon's* filesystem, and under Docker-in-Docker that is a different
    container with no access to anything the worker wrote. See
    :mod:`oa_worker.sandbox.volumes`.

    Returns:
        A ``HostConfig`` carrying every control from the specification.
    """
    mounts: list[dict[str, Any]] = []
    if workspace_source is not None:
        mounts.append(
            {
                "Type": "volume",
                "Source": workspace_source,
                "Target": CONTAINER_WORKSPACE_DIR,
                "ReadOnly": False,
            }
        )
    if grading_source is not None:
        mounts.append(
            {
                "Type": "volume",
                "Source": grading_source,
                "Target": CONTAINER_GRADING_DIR,
                "ReadOnly": True,
            }
        )

    return HostConfig(
        version=ENGINE_API_VERSION,
        # -- filesystem -----------------------------------------------------
        read_only=envelope.read_only_rootfs,
        mounts=mounts,
        # /tmp is applied here, not appended to `mounts`. It is always present: it
        # is the runner's only writable scratch space, and omitting it would break
        # compilation, caching, and HOME.
        tmpfs={SANDBOX_TMP_TARGET: build_tmpfs_mount_spec(envelope)},
        # -- resources ------------------------------------------------------
        mem_limit=envelope.mem_limit_bytes,
        # Equal to mem_limit, which is what makes swap a non-escape route: the
        # cgroup denies growth once the memory limit is reached, swap or not.
        memswap_limit=envelope.mem_limit_bytes,
        # 0 = do not swap at all. Combined with the above, total memory is
        # strictly bounded.
        mem_swappiness=0,
        # Fork-bomb defence. Memory limits alone do not stop
        # ``while True: fork()`` quickly enough, because the parent's task table
        # is the bottleneck rather than any child's RSS.
        pids_limit=envelope.pids_limit,
        # ``nano_cpus`` only. Setting cpu_period/cpu_quota alongside it is
        # rejected by the daemon with "Conflicting options: Nano CPUs and CPU
        # Period cannot both be set", so the explicit quota form is deliberately
        # absent -- it was a redundant restatement of the same limit, and a
        # redundancy that prevents the container from starting at all.
        nano_cpus=envelope.nano_cpus,
        # -- privileges -----------------------------------------------------
        cap_drop=list(envelope.cap_drop),
        security_opt=list(envelope.security_opt),
        privileged=envelope.privileged,
        # -- namespaces and init -------------------------------------------
        network_mode=SANDBOX_NETWORK_MODE,
        # A fresh IPC namespace. Without this a candidate could reach POSIX
        # semaphores and shared memory belonging to the host or another
        # container, which is a real cross-container channel.
        ipc_mode="none",
        # PID namespace isolation. Note the value: Docker accepts "private" as a
        # *keyword* but rejects it as a pid_mode -- the daemon's accepted values
        # are "host" and "container:<name>" (or a raw container id). A new
        # container is already placed in its own PID namespace, so there is
        # nothing to request here; the daemon default is the isolated behaviour,
        # and asserting "private" would make every sandbox fail to start.
        # The live adversarial suite verifies from inside the container that
        # /proc shows only sandbox processes.
        pid_mode=None,
        # PID 1 reaping, so a fork storm cannot exhaust the task table with
        # zombies before the pids_limit trips.
        init=True,
        # -- logging --------------------------------------------------------
        log_config={
            "Type": "json-file",
            # Bounded so a container that somehow survives its task cannot
            # fill the daemon's disk with logs. The orchestrator force-removes
            # containers anyway; this is a second line of defence.
            "Config": {"max-size": "1m", "max-file": "1"},
        },
    )


def sandbox_environment(*, submission_id: str) -> dict[str, str]:
    """Build the container's environment from a literal, secret-free dict.

    This literal is the enforcement point for the spec's "no production API keys
    in a sandbox" rule. ``test_environment_carries_no_secrets`` fails on any
    variable name containing key/token/secret/password/credential/dsn/://, so
    adding an LLM_API_KEY here "temporarily" is a red build rather than an
    incident.

    Args:
        submission_id: Correlation id. Truncated so it cannot influence
            container naming or blow an environment size limit.

    Returns:
        The exact environment the candidate's process will see.
    """
    return {
        # Make interpreter behaviour deterministic regardless of how the image
        # was built: unbuffered output for streaming, no bytecode written back
        # into the read-only root, and no user site-packages that could shadow
        # the runner via a path we do not control.
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "NODE_ENV": "test",
        "CI": "1",
        # HOME and TMPDIR both point at the tmpfs. Left at /root, which is
        # read-only and root-owned, several common libraries would fail to
        # create their cache directory and abort.
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "OA_SANDBOX": "1",
        "OA_SUBMISSION_ID": submission_id[:64],
        "OA_RESULT_PATH": CONTAINER_RESULT_PATH,
        "OA_RUNNER_DIR": CONTAINER_RUNNER_DIR,
        "OA_WORKSPACE_DIR": CONTAINER_WORKSPACE_DIR,
    }


def build_container_spec(
    *,
    limits: ExecutionLimits,
    envelope: SandboxEnvelope,
    submission_id: str,
    run_command: tuple[str, ...],
    workspace_source: str | None = None,
    grading_source: str | None = None,
) -> dict[str, Any]:
    """Construct the full low-level container creation request.

    Returned as a plain dict so it can be unit-tested without a Docker daemon
    and passed straight to :meth:`docker.api.client.APIClient.create_container`.

    Args:
        limits: Per-run limits, including the image reference.
        envelope: The immutable security policy.
        submission_id: Correlation id, surfaced as a label and an env var.
        run_command: Fully resolved argv. Never built by string interpolation,
            because the container's entrypoint goes to ``execve`` and a shell
            here would be an argument-injection surface.
    Returns:
        The kwargs for ``APIClient.create_container``.
    """
    return {
        "image": limits.image,
        # argv form, not a string. A string would be interpreted by a shell
        # inside the container, and candidate-controlled values in it would
        # become injection.
        "command": [*run_command],
        "entrypoint": [],
        "working_dir": CONTAINER_WORKSPACE_DIR,
        "user": envelope.user,
        "environment": sandbox_environment(submission_id=submission_id),
        # -- image-level isolation -----------------------------------------
        # ``none`` disables all external connectivity. Combined with
        # ``network_disabled`` this blocks egress, metadata services, and any
        # lateral movement to another container.
        "network_disabled": envelope.network_disabled,
        # The configured runtime. This is the ONLY thing that differs between
        # ``runc`` and ``runsc`` modes -- see ``runtime_controls``.
        "runtime": limits.runtime,
        # -- shutdown -------------------------------------------------------
        # SIGKILL, not SIGTERM: a hostile process is entitled to ignore SIGTERM,
        # and the wall clock must not depend on its cooperation.
        "stop_signal": "SIGKILL",
        # -- process hygiene ------------------------------------------------
        "detach": True,
        "tty": False,
        "stdin_open": False,
        # No anonymous volumes. Everything the sandbox needs is an explicit named
        # volume in the HostConfig, so `docker inspect` accounts for all of it and
        # the cleanup path can be exhaustive.
        "volumes": {},
        "labels": dict(limits.labels),
        "host_config": build_host_config(
            envelope=envelope,
            workspace_source=workspace_source,
            grading_source=grading_source,
        ),
    }


def runtime_controls(envelope: SandboxEnvelope, runtime: str) -> dict[str, Any]:
    """Return every control that applies regardless of the container runtime.

    Two functions in this platform, and the boundary between them is the whole
    reason this helper exists:

    * :func:`assert_gvisor_is_second_layer` -- security controls, identical in
      ``runc`` and ``runsc`` mode. Network off, read-only rootfs, non-root uid,
      all capabilities dropped, no-new-privileges, memory and swap bounded, PID
      limit, CPU limit, private IPC, executable tmpfs, read-only grading mount.
      These are not negotiable by runtime and there is no environment variable
      that relaxes any of them.
    * The runtime itself -- ``runsc`` adds gVisor's userspace kernel as a second
      isolation layer; ``runc`` does not.

    Returns:
        The control values keyed by name, for diffing across runtime modes in
        tests. ``runtime`` is included as a label so a diff reports *only* the
        runtime if that is genuinely the sole difference.
    """
    return {
        "runtime": runtime,
        "network_disabled": envelope.network_disabled,
        "read_only_rootfs": envelope.read_only_rootfs,
        "user": envelope.user,
        "cap_drop": list(envelope.cap_drop),
        "security_opt": list(envelope.security_opt),
        "privileged": envelope.privileged,
        "mem_limit_bytes": envelope.mem_limit_bytes,
        "memswap_limit_bytes": envelope.mem_limit_bytes,
        "mem_swappiness": 0,
        "pids_limit": envelope.pids_limit,
        "nano_cpus": envelope.nano_cpus,
        "ipc_mode": "none",
        "init": True,
        "stop_signal": "SIGKILL",
        "tmpfs_spec": build_tmpfs_mount_spec(envelope),
        "grading_mount_read_only": True,
        "workspace_mount_read_only": False,
    }


def assert_gvisor_is_second_layer(*, sandbox_runtime: str) -> None:
    """Assert that the configured runtime is one that provides gVisor.

    The guard that replaced a compiled-in ``runsc``. It is *not* the same thing,
    and the difference matters:

    * Previously: ``runc`` was impossible to select, so the platform refused to
      start anywhere gVisor was absent.
    * Now: ``runc`` is a supported development mode, and production is guarded by
      calling this explicitly -- from the deploy path, from a CI check, and from
      the ``gvisor`` pytest marker, which skips unless ``SANDBOX_RUNTIME=runsc``.

    Args:
        sandbox_runtime: The configured runtime.

    Raises:
        AssertionError: If the runtime does not provide gVisor. The message names
            the variable to set, because the fix is a one-word configuration
            change and an operator seeing this should not have to read the source
            to find that out.
    """
    if sandbox_runtime != GVISOR_RUNTIME:
        raise AssertionError(
            f"gVisor is not enabled: sandbox_runtime={sandbox_runtime!r} but the "
            f"second isolation layer requires {GVISOR_RUNTIME!r}. Set "
            f"SANDBOX_RUNTIME={GVISOR_RUNTIME} in production. Continuing with "
            f"{sandbox_runtime!r} would run untrusted code with one isolation "
            f"layer instead of two, while still reporting the platform as hardened."
        )
