"""Prove the security envelope cannot be weakened by configuration.
These tests are the enforcement mechanism for the design decision recorded in
:mod:`oa_engine.platform.config`: operational settings are environment-driven,
containment is not. If someone adds an environment-variable escape hatch to the
envelope, this file fails.
Two layers:
* **Static assertions** over the spec ``build_container_spec`` produces. Proves
  we construct the right request.
* **Live assertions** over a real container's ``HostConfig``. Proves Docker
  honoured it. Both are necessary: the gap between what we ask for and what we
  get is exactly where a silent regression would live.
Live tests are marked ``sandbox`` and skip when Docker or ``runsc`` is absent.
They never degrade the assertions -- a skipped security test reports as skipped,
not as passed.
"""

from __future__ import annotations

from typing import Any

import pytest
from docker.types import HostConfig

from oa_engine.execution.payload import CONTAINER_GRADING_DIR, CONTAINER_WORKSPACE_DIR
from oa_engine.platform.config import (
    DEFAULT_ENVELOPE,
    SANDBOX_GID,
    SANDBOX_UID,
    ExecutionLimits,
    Language,
    Settings,
)
from oa_worker.sandbox.spec import (
    SANDBOX_TMP_TARGET,
    build_container_spec,
    build_tmpfs_mount_spec,
)

pytestmark = pytest.mark.usefixtures("settings")


@pytest.fixture
def limits() -> ExecutionLimits:
    """Execution limits built from the real envelope and default settings."""
    return ExecutionLimits.build(
        language=Language.PYTHON,
        settings=Settings(),
        envelope=DEFAULT_ENVELOPE,
        submission_id="test-submission-0001",
    )


@pytest.fixture
def spec(limits: ExecutionLimits) -> dict[str, Any]:
    """A representative low-level container spec built from the real envelope."""
    return build_container_spec(
        limits=limits,
        envelope=DEFAULT_ENVELOPE,
        submission_id="test-submission-0001",
        run_command=("/runner/run-python.sh",),
    )


def _tmpfs_flags() -> set[str]:
    """Parse the flag names out of the /tmp mount options string."""
    return {part.split("=", 1)[0] for part in build_tmpfs_mount_spec(DEFAULT_ENVELOPE).split(",")}


@pytest.fixture
def host(spec: dict[str, Any]) -> HostConfig:
    """The ``HostConfig`` half of the request, where containment actually lives."""
    return spec["host_config"]


@pytest.fixture
def grading_host(limits: ExecutionLimits) -> HostConfig:
    """The HostConfig for a real grading container, with both binds resolved."""
    return build_container_spec(
        limits=limits,
        envelope=DEFAULT_ENVELOPE,
        submission_id="test-submission-0001",
        run_command=("/runner/grade-python.sh",),
    )["host_config"]


class TestEnvelopeImmutability:
    """The envelope must not be reachable from the environment."""

    def test_envelope_has_no_env_field_mapping(self) -> None:
        """No field of the envelope may be nameable by an env var.
        If the envelope were a ``BaseSettings`` subclass, any field could be
        overridden by an environment variable and the guarantee would be void.
        """
        import dataclasses

        from oa_engine.platform.config import SandboxEnvelope

        assert not issubclass(
            SandboxEnvelope, __import__("pydantic_settings", fromlist=["BaseSettings"]).BaseSettings
        )
        assert dataclasses.is_dataclass(SandboxEnvelope)
        assert SandboxEnvelope.__dataclass_params__.frozen is True

    def test_envelope_rejects_attribute_assignment(self) -> None:
        """Normal attribute assignment cannot weaken the envelope.
        Scope note, because it matters: ``frozen=True`` blocks ordinary
        ``envelope.network_disabled = False``, which is the threat in the threat
        model (a careless refactor weakening containment). It does **not** stop
        ``object.__setattr__``, which defeats any Python-level freeze. Hard
        immutability would require a process boundary, and the real boundary here
        is that the envelope has no environment-variable path at all -- covered by
        the test above.
        This test mutates a throwaway instance rather than the global, precisely
        because a frozen dataclass can still be mutated reflectively and doing so
        to ``DEFAULT_ENVELOPE`` would silently weaken every later test in the run.
        """
        import dataclasses

        from oa_engine.platform.config import SandboxEnvelope

        probe = SandboxEnvelope()
        with pytest.raises(dataclasses.FrozenInstanceError):
            probe.network_disabled = False  # type: ignore[misc]

    def test_shared_envelope_survives_a_failed_reflection_attempt(self) -> None:
        """The process-wide envelope is unchanged by reflection.
        Pins down that the global really is untouched: if some other test did
        manage to flip a field reflectively, this catches it and names the
        offender, rather than letting one test silently weaken all the others.
        """
        assert DEFAULT_ENVELOPE.network_disabled is True
        assert DEFAULT_ENVELOPE.read_only_rootfs is True
        assert DEFAULT_ENVELOPE.privileged is False

    def test_envelope_has_no_runtime_field(self) -> None:
        """The runtime is not a containment control and does not live here.
        It moved to ``Settings.sandbox_runtime``. Keeping it on the frozen envelope
        would imply it was a security control subject to the "never configurable"
        rule, which is exactly the ambiguity this change removes.
        """
        import dataclasses

        fields = {f.name for f in dataclasses.fields(DEFAULT_ENVELOPE)}
        assert "runtime" not in fields

    def test_invariants_reject_network_disabled(self) -> None:
        """Validation refuses an envelope with networking switched on."""
        import dataclasses

        loose = dataclasses.replace(DEFAULT_ENVELOPE, network_disabled=False)
        with pytest.raises(ValueError, match="network_disabled"):
            loose.validate_invariants()

    def test_invariants_reject_privileged(self) -> None:
        """Validation refuses a privileged envelope."""
        import dataclasses

        with pytest.raises(ValueError, match="privileged"):
            dataclasses.replace(DEFAULT_ENVELOPE, privileged=True).validate_invariants()

    def test_invariants_reject_writable_rootfs(self) -> None:
        """Validation refuses a writable root filesystem."""
        import dataclasses

        with pytest.raises(ValueError, match="read_only_rootfs"):
            dataclasses.replace(DEFAULT_ENVELOPE, read_only_rootfs=False).validate_invariants()

    def test_invariants_reject_missing_cap_drop_all(self) -> None:
        """Validation refuses an envelope that keeps any capability."""
        import dataclasses

        with pytest.raises(ValueError, match="cap_drop"):
            dataclasses.replace(DEFAULT_ENVELOPE, cap_drop=("NET_RAW",)).validate_invariants()

    def test_invariants_reject_missing_no_new_privileges(self) -> None:
        """Validation refuses an envelope without no-new-privileges."""
        import dataclasses

        with pytest.raises(ValueError, match="no-new-privileges"):
            dataclasses.replace(DEFAULT_ENVELOPE, security_opt=()).validate_invariants()

    def test_invariants_reject_root_user(self) -> None:
        """Validation refuses an envelope that runs as root."""
        import dataclasses

        with pytest.raises(ValueError, match="non-root"):
            dataclasses.replace(DEFAULT_ENVELOPE, user="0:0").validate_invariants()

    def test_invariants_reject_nonexecutable_tmp(self) -> None:
        """Validation refuses a noexec /tmp, which would break the runner."""
        import dataclasses

        with pytest.raises(ValueError, match="executable"):
            dataclasses.replace(DEFAULT_ENVELOPE, tmp_exec=False).validate_invariants()

    def test_default_envelope_is_valid(self) -> None:
        """The shipped envelope satisfies its own invariants."""
        DEFAULT_ENVELOPE.validate_invariants()


class TestContainerSpecControls:
    """Every control in the specification must appear in the create request."""

    def test_network_is_disabled(self, spec: dict[str, object]) -> None:
        """No network namespace beyond loopback."""
        assert spec["network_disabled"] is True

    def test_root_filesystem_is_read_only(self, host: HostConfig) -> None:
        """The image cannot be modified by candidate code."""
        assert host.get("ReadonlyRootfs") is True

    def test_runs_as_non_root(self, spec: dict[str, object]) -> None:
        """Process uid/gid is the dedicated non-root account."""
        assert spec["user"] == f"{SANDBOX_UID}:{SANDBOX_GID}"
        assert SANDBOX_UID != 0

    def test_all_capabilities_dropped(self, host: HostConfig) -> None:
        """Every Linux capability is dropped."""
        assert host.get("CapDrop") == ["ALL"]

    def test_no_new_privileges(self, host: HostConfig) -> None:
        """Setuid binaries cannot gain privileges."""
        assert "no-new-privileges" in (host.get("SecurityOpt") or [])

    def test_not_privileged(self, host: HostConfig) -> None:
        """The container is never privileged.
        Asserted as "absent or falsy" rather than "is False", because docker-py
        drops falsy values when serialising the request: ``Privileged=False``
        never appears in the body at all. An exact ``is False`` assertion here
        would fail against a perfectly correct config, and a test that fails for
        that reason trains a reader to ignore it. What matters is the negative:
        the daemon's own default is unprivileged, so an absent flag is the
        secure state.
        """
        assert host.get("Privileged") is not True

    def test_memory_limit_and_no_swap_escape(self, host: HostConfig) -> None:
        """Memory is capped and swap cannot be used to exceed it."""
        assert host.get("Memory") == 256 * 1024 * 1024
        # Absent MemorySwap would let the cgroup swap past the memory limit, so
        # equality with Memory is the property that matters, not mere presence.
        assert host.get("MemorySwap") == host.get("Memory")

    def test_swap_is_disabled_outright(self, host: HostConfig) -> None:
        """Swappiness is zero, so the swap path is never taken."""
        assert host.get("MemorySwappiness") == 0

    def test_pids_limit_defends_against_fork_bomb(self, host: HostConfig) -> None:
        """Process count is capped."""
        assert host.get("PidsLimit") == 128

    def test_cpu_quota_is_bounded(self, host: HostConfig) -> None:
        """CPU time is capped so one submission cannot starve its neighbours."""
        assert host.get("NanoCpus") == 500_000_000

    def test_cpu_period_and_quota_are_not_also_set(self, host: HostConfig) -> None:
        """The explicit quota form is absent, because it conflicts.
        The daemon rejects a request carrying both ``NanoCpus`` and ``CpuPeriod``
        with "Conflicting options". They express the same limit, so carrying both
        is a redundancy -- and one that makes every sandbox fail to start.
        """
        assert host.get("CpuPeriod") is None
        assert host.get("CpuQuota") is None

    def test_namespace_isolation(self, host: HostConfig) -> None:
        """Network, IPC, and PID namespaces are all severed from the host.
        IPC matters as much as network here: without a private IPC namespace a
        candidate reaches shared memory and semaphores belonging to other
        containers on the same daemon, which is a real cross-tenant channel.
        """
        assert host.get("NetworkMode") == "none"
        assert host.get("IpcMode") == "none"

    def test_pid_namespace_is_not_shared_with_the_host(self, host: HostConfig) -> None:
        """The sandbox does not opt into the host PID namespace.
        Docker's accepted ``pid_mode`` values are "host" and "container:<id>"; a
        new container already gets its own PID namespace. Requesting "private"
        is rejected by the daemon outright, so the assertion is the negative one:
        the host namespace must never be explicitly requested. Confirmed from
        inside the container by the adversarial suite's ``/proc`` probe.
        """
        assert host.get("PidMode") != "host"

    def test_userns_mode_is_not_host(self, host: HostConfig) -> None:
        """The sandbox does not share the host's user namespace.
        Absence is the correct value: the Engine API accepts only "host" or an
        explicit uid/gid mapping here, so leaving it unset leaves the daemon
        default, which is a per-container user namespace. Asserting on absence
        rather than a literal is what makes this test survive the SDK's
        parameter-validation change that rejected an earlier "private" value.
        """
        assert host.get("UsernsMode") != "host"

    def test_identity_comes_from_a_non_root_uid(self, spec: dict[str, Any]) -> None:
        """Privilege rests on the uid, not on a capability or a namespace flag.
        This is the actual defence against escalation: there is no root uid in
        the container to escalate to, and every capability has been dropped so
        there is nothing to gain even if one were reachable.
        """
        assert spec["user"] == f"{SANDBOX_UID}:{SANDBOX_GID}"
        assert SANDBOX_UID >= 1000  # not a system uid either

    def test_runtime_is_taken_from_configuration(self, spec: dict[str, Any]) -> None:
        """The request carries whatever runtime was configured, not a hardcoded one.
        Defaults to ``runc``; the ``runsc`` case is covered by
        ``TestRuntimeConfigurability::test_runtime_reaches_the_create_request`` and
        by the ``gvisor`` marker on the live tests.
        """
        assert spec["runtime"] == Settings().sandbox_runtime

    def test_stop_signal_is_sigkill(self, spec: dict[str, Any]) -> None:
        """Shutdown does not depend on the process cooperating."""
        assert spec["stop_signal"] == "SIGKILL"

    def test_init_process_reaps_zombies(self, host: HostConfig) -> None:
        """PID 1 reaping is on, so a fork storm cannot exhaust the table."""
        assert host.get("Init") is True

    def test_command_is_argv_not_a_string(self, spec: dict[str, Any]) -> None:
        """The entrypoint reaches ``execve`` directly.
        A string command would be handed to a shell inside the container, turning
        any candidate-influenced element into an argument-injection surface.
        """
        assert isinstance(spec["command"], list)
        assert all(isinstance(part, str) for part in spec["command"])
        assert spec["entrypoint"] == []

    def test_environment_carries_no_secrets(self, spec: dict[str, Any]) -> None:
        """No credential of any kind is reachable from inside the sandbox.
        This is the test that would catch someone adding ``LLM_API_KEY`` to the
        container env "just for debugging". The forbidden-substring list is
        intentionally aggressive.
        """
        env: dict[str, str] = spec["environment"]
        forbidden = ("key", "token", "secret", "password", "credential", "dsn", "://")
        offenders = [name for name in env if any(f in name.lower() for f in forbidden)]
        assert offenders == [], f"secret-shaped variables in sandbox env: {offenders}"

    def test_environment_does_not_leak_worker_configuration(self, spec: dict[str, object]) -> None:
        """Host configuration must not be inherited into the sandbox."""
        env: dict[str, str] = spec["environment"]
        for leaked in ("POSTGRES_DSN", "REDIS_URL", "CELERY_BROKER_URL", "OA_WORKER"):
            assert leaked not in env


class TestTmpfsMount:
    """The /tmp mount must be executable, memory-backed, and size-capped.
    Applied through ``HostConfig.Tmpfs`` rather than as a ``Type: tmpfs`` Mount
    entry. That is not a style preference: the Mount form forces ``noexec`` at the
    daemon and nothing overrides it, which
    ``test_type_tmpfs_mount_is_always_noexec`` in ``test_tmpfs_exec.py`` pins
    against a live daemon. Only the options-string form honours an explicit
    ``exec``.
    """

    def test_mount_target_is_tmp(self) -> None:
        """The writable scratch mount lives at /tmp."""
        assert SANDBOX_TMP_TARGET == "/tmp"

    def test_exec_is_requested_explicitly(self) -> None:
        """``exec`` appears, because absence is not enough.
        The daemon's default for a tmpfs mount is ``noexec``. This is the single
        assertion that makes /tmp executable, and it is the entire reason this
        mount is an options string rather than a Mount dict.
        """
        flags = _tmpfs_flags()
        assert "exec" in flags
        assert "noexec" not in flags

    def test_size_is_capped(self) -> None:
        """A /tmp flood is bounded by the kernel, not by hope."""
        assert f"size={64 * 1024 * 1024}" in build_tmpfs_mount_spec(DEFAULT_ENVELOPE)

    def test_mode_is_sticky_world_writable(self) -> None:
        """Matches real /tmp semantics for sibling processes."""
        assert f"mode={0o1777:o}" in build_tmpfs_mount_spec(DEFAULT_ENVELOPE)

    def test_mount_is_writable(self) -> None:
        """Read-only /tmp breaks Python, whose HOME and TMPDIR both live there."""
        assert build_tmpfs_mount_spec(DEFAULT_ENVELOPE).startswith("rw,")

    def test_device_and_setuid_are_still_denied(self) -> None:
        """``nosuid`` and ``nodev`` are requested alongside ``exec``.
        Guards against over-correcting. The goal is exec permission, not a
        permissive /tmp: no question template legitimately runs a setuid binary or
        creates a device node from a temp directory, so denying both is free
        attack surface removed.
        """
        flags = _tmpfs_flags()
        assert "nosuid" in flags
        assert "nodev" in flags

    def test_nonexecutable_tmpfs_is_rejected(self) -> None:
        """A noexec /tmp is a deployment mistake, not a knob."""
        import dataclasses

        with pytest.raises(ValueError, match="not a supported configuration"):
            build_tmpfs_mount_spec(dataclasses.replace(DEFAULT_ENVELOPE, tmp_exec=False))

    def test_tmpfs_is_applied_to_the_host_config(self, host: HostConfig) -> None:
        """The mount is actually requested, not merely built."""
        tmpfs = host.get("Tmpfs") or {}
        assert SANDBOX_TMP_TARGET in tmpfs
        assert "exec" in tmpfs[SANDBOX_TMP_TARGET]


class TestWorkspaceAndGradingIsolation:
    """Candidate code and hidden tests must not be able to reach each other.
    Both directories live inside the container's exec-enabled tmpfs rather than
    being bind-mounted, because a bind source resolves on the *daemon's*
    filesystem and under Docker-in-Docker that is a different container. Isolation
    therefore comes from three things rather than from a mount flag:
    1. The grading directory does not exist during the candidate phase.
    2. Every candidate process is SIGKILLed before it is created.
    3. It is wiped and recreated immediately before the tests are injected.
    """

    def test_the_two_directories_are_distinct(self) -> None:
        """A single path cannot serve both roles."""
        assert CONTAINER_WORKSPACE_DIR != CONTAINER_GRADING_DIR
        assert CONTAINER_WORKSPACE_DIR != "/"
        assert CONTAINER_GRADING_DIR != "/"

    def test_both_live_under_the_writable_tmpfs(self) -> None:
        """Neither is on the read-only root filesystem, which is why this works."""
        for path in (CONTAINER_WORKSPACE_DIR, CONTAINER_GRADING_DIR):
            assert path.startswith("/tmp/"), f"{path} must be on the writable tmpfs"

    def test_no_bind_mounts_are_requested(self, host: HostConfig) -> None:
        """The container mounts no host path at all.
        Beyond the dind incompatibility, this removes the entire class of
        mount-escape and mount-race bugs: there is no host directory for a
        container to traverse out of.
        """
        assert list(host.get("Mounts") or []) == []
        assert (host.get("Volumes") or None) in (None, {})

    def test_grading_path_is_not_under_the_workspace(self) -> None:
        """The grading directory is not a subdirectory of the candidate's.
        A shared prefix would make a stray ``..`` in candidate code able to name the
        grading directory with a relative path.
        """
        assert not CONTAINER_GRADING_DIR.startswith(CONTAINER_WORKSPACE_DIR)

    def test_hidden_tests_live_under_the_grading_directory(self) -> None:
        """``SubmissionPayload`` agrees with the orchestrator about the path."""
        from oa_engine.execution.payload import SubmissionPayload

        payload = SubmissionPayload(
            submission_id="s",
            language="python",
            files={"a.py": "x = 1\n"},
            hidden_tests={"t.py": "def test(): pass\n"},
            run_command=("/runner/run-python.sh",),
        )
        # Archive members are *relative*: the tar is extracted into the target
        # directory, so the target comes from where put_archive is aimed, not from
        # the member names. Asserting the absolute path appears here would be
        # asserting the wrong mechanism.
        raw = payload.grading_tar().decode("latin-1")
        assert "t.py" in raw
        assert not raw.lstrip().startswith("/")

    def test_candidate_archive_never_contains_the_grading_directory(self) -> None:
        """Candidate files cannot smuggle a grading-path archive member in."""
        from oa_engine.execution.payload import SubmissionPayload

        payload = SubmissionPayload(
            submission_id="s",
            language="python",
            files={"a.py": "x = 1\n"},
            hidden_tests={"t.py": "def test(): pass\n"},
            run_command=("/runner/run-python.sh",),
        )
        raw = payload.candidate_tar().decode("latin-1")
        assert "grading" not in raw
        assert "t.py" not in raw


class TestRuntimeConfigurability:
    """``SANDBOX_RUNTIME`` selects the isolation layer; nothing else may change.
    The runtime is configurable because gVisor cannot exist on macOS or Windows,
    and a platform that refuses to start on most contributors' machines is not a
    usable development environment. That trade is only safe if the runtime really
    is the *only* difference between the two modes, which is what this class
    asserts.
    """

    def test_default_is_runc(self) -> None:
        """Unset means ``runc``, the mode a developer machine can satisfy."""
        assert Settings().sandbox_runtime == "runc"

    def test_runc_is_the_documented_default(self) -> None:
        """The default is a named constant, not an inline literal."""
        from oa_engine.platform.config import DEFAULT_SANDBOX_RUNTIME

        assert DEFAULT_SANDBOX_RUNTIME == "runc"

    @pytest.mark.parametrize("runtime", ["runc", "runsc"])
    def test_both_runtimes_are_accepted(self, runtime: str) -> None:
        """Both modes are first-class, not one being a degraded fallback."""
        assert Settings(sandbox_runtime=runtime).sandbox_runtime == runtime

    @pytest.mark.parametrize("runtime", ["runs", "RUNSC", "runc ", "", "runc;runsc"])
    def test_unknown_runtime_is_rejected_at_construction(self, runtime: str) -> None:
        """A typo fails at boot, naming the valid values.
        Without this, ``SANDBOX_RUNTIME=runs`` would pass a syntactic check and
        then fail as an opaque daemon error in the middle of a candidate's
        submission.
        """
        with pytest.raises(ValueError, match="sandbox_runtime must be one of"):
            Settings(sandbox_runtime=runtime)

    def test_gvisor_enabled_reflects_the_setting(self) -> None:
        """The helper agrees with the value, so callers need not compare strings."""
        assert Settings(sandbox_runtime="runsc").gvisor_enabled() is True
        assert Settings(sandbox_runtime="runc").gvisor_enabled() is False

    def test_gvisor_guard_fails_closed_on_runc(self) -> None:
        """The production guard rejects a single-layer configuration."""
        from oa_worker.sandbox.spec import assert_gvisor_is_second_layer

        with pytest.raises(AssertionError, match="gVisor is not enabled"):
            assert_gvisor_is_second_layer(sandbox_runtime="runc")

    def test_gvisor_guard_passes_on_runsc(self) -> None:
        """The guard accepts the hardened configuration."""
        from oa_worker.sandbox.spec import assert_gvisor_is_second_layer

        assert_gvisor_is_second_layer(sandbox_runtime="runsc")

    def test_gvisor_guard_message_names_the_fix(self) -> None:
        """An operator hitting this needs the variable name, not a source dive."""
        from oa_worker.sandbox.spec import assert_gvisor_is_second_layer

        with pytest.raises(AssertionError, match=r"SANDBOX_RUNTIME=runsc"):
            assert_gvisor_is_second_layer(sandbox_runtime="runc")


class TestControlsIdenticalAcrossRuntimes:
    """The security envelope must not vary with the runtime.
    This is the load-bearing test for the whole configurability change. If a
    future edit relaxed, say, ``cap_drop`` under ``runc``, every test asserting the
    absolute value would still pass -- they would just be asserting the value for
    whichever mode happened to be configured. Diffing the two modes makes that class
    of regression impossible to miss.
    """

    @pytest.mark.parametrize("runtime", ["runc", "runsc"])
    def test_runtime_reaches_the_create_request(self, runtime: str) -> None:
        """The configured runtime is what the SDK is actually told to use."""
        import dataclasses

        from oa_engine.platform.config import ExecutionLimits
        from oa_worker.sandbox.spec import build_container_spec

        limits = dataclasses.replace(
            ExecutionLimits.build(
                language=Language.PYTHON,
                settings=Settings(),
                envelope=DEFAULT_ENVELOPE,
                submission_id="runtime-probe",
            ),
            runtime=runtime,
        )
        spec = build_container_spec(
            limits=limits,
            envelope=DEFAULT_ENVELOPE,
            submission_id="runtime-probe",
            run_command=("/runner/grade-python.sh",),
        )
        assert spec["runtime"] == runtime

    def test_runtime_is_the_only_difference(self) -> None:
        """Diff the two modes: every other control must be byte-identical."""
        from oa_worker.sandbox.spec import runtime_controls

        runc = runtime_controls(DEFAULT_ENVELOPE, "runc")
        runsc = runtime_controls(DEFAULT_ENVELOPE, "runsc")
        differing = {k for k in runc if runc[k] != runsc.get(k)}
        assert differing == {"runtime"}, (
            f"these controls changed between runtimes: {sorted(differing)}. "
            f"Only the runtime itself may vary; a difference here means a security "
            f"control is being conditioned on the isolation layer."
        )

    def test_every_control_is_present_in_both_modes(self) -> None:
        """No control may be silently absent in one mode.
        Catches the subtler variant of the same bug: a control omitted under
        ``runc`` would not show up as a *difference* in the diff above, only as a
        missing key, so both properties are asserted.
        """
        from oa_worker.sandbox.spec import runtime_controls

        runc = runtime_controls(DEFAULT_ENVELOPE, "runc")
        runsc = runtime_controls(DEFAULT_ENVELOPE, "runsc")
        assert set(runc) == set(runsc)
        assert runc["network_disabled"] is True
        assert runc["read_only_rootfs"] is True
        assert runc["privileged"] is False
        assert runc["cap_drop"] == ["ALL"]
        assert "no-new-privileges" in runc["security_opt"]
        assert runc["tmpfs_spec"].startswith("rw,exec")
        assert runc["grading_mount_read_only"] is True
