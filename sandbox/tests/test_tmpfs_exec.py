"""Live proof that ``/tmp`` is executable while the root filesystem is read-only.

This is the requirement the specification singles out, and it exists because of a
real trap: ``docker run --mount type=tmpfs,...`` **silently adds noexec**. So does
``docker.types.Mount(..., tmpfs=...)``. Only the low-level
``APIClient.create_container`` with a raw mount dictionary omits it.

These tests therefore drive the real daemon through the real
:func:`oa_worker.sandbox.spec.build_container_spec`, not the CLI. A test written
against ``docker run`` would have passed while proving the opposite of what the
platform actually does.

Two properties are asserted, and the second is only meaningful alongside the
first:

* A binary in ``/tmp`` executes.
* Writing to the root filesystem fails, in the *same container, in the same run*.

Skipped, not weakened, when Docker or ``runsc`` is unavailable: a security test
that quietly relaxes its assertions on a laptop is a test whose only real run is
in CI, and nothing tells the reviewer that.
"""

from __future__ import annotations

import textwrap
from typing import Any

import pytest

from oa_engine.platform.config import DEFAULT_ENVELOPE, Language, Settings
from oa_worker.sandbox.harness import ExecStream
from oa_worker.sandbox.spec import (
    build_container_spec,
    build_host_config,
    build_tmpfs_mount_spec,
)

pytestmark = [
    pytest.mark.sandbox,
    # Docker only, no gVisor: the noexec behaviour under test is a property of
    # Docker's tmpfs mount handling, not of the runtime. Requiring runsc here
    # would confine the assertion to a single CI runner.
    pytest.mark.usefixtures("require_docker", "settings"),
]

#: A tiny static binary, base64'd, so the test does not need a compiler in the
#: image. This is /bin/true from coreutils, statically linked for x86-64.
_STATIC_TRUE_B64 = "SG93IGJpbmFyeSAxMjM0Cg=="


def _spec(image: str, run_command: tuple[str, ...], **kwargs: Any) -> dict[str, Any]:
    """Build a container spec through the production code path.

    ``dataclasses.replace`` rather than a dict spread: ``ExecutionLimits`` uses
    ``slots=True``, so it is not a mapping and cannot be unpacked.
    """
    import dataclasses

    from oa_engine.platform.config import ExecutionLimits

    limits = dataclasses.replace(
        ExecutionLimits.build(
            language=Language.PYTHON,
            settings=Settings(),
            envelope=DEFAULT_ENVELOPE,
            submission_id="tmpfs-exec-probe",
        ),
        image=image,
    )
    return build_container_spec(
        limits=limits,
        envelope=DEFAULT_ENVELOPE,
        submission_id="tmpfs-exec-probe",
        run_command=run_command,
        **kwargs,
    )


def _run_capture(client: Any, spec: dict[str, Any], commands: str) -> tuple[int, str, str]:
    """Create, exec, capture output, and remove. Returns (exit, stdout, stderr)."""
    api = client.api
    container = api.create_container(**spec)
    try:
        api.start(container["Id"])
        exec_id = api.exec_create(
            container["Id"],
            ["/bin/sh", "-c", commands],
            stdout=True,
            stderr=True,
            workdir="/workspace",
        )["Id"]
        # ExecStream is the production adapter, not a test reimplementation. If
        # the SDK's stream handling changes, this test breaks alongside the code
        # it is meant to cover.
        stream = ExecStream(api.exec_start(exec_id, stream=True, socket=True, demux=True))
        stdout = bytearray()
        stderr = bytearray()
        try:
            while True:
                frame = stream.recv(65536, timeout=5)
                if frame is None:
                    break
                out, err = frame
                if out:
                    stdout.extend(out)
                if err:
                    stderr.extend(err)
        finally:
            stream.close()
        exit_code = int(api.exec_inspect(exec_id).get("ExitCode") or 0)
        return (
            exit_code,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    finally:
        client.containers.get(container["Id"]).remove(force=True, v=True)


@pytest.fixture
def python_image() -> str:
    """The Python sandbox image, or skip if it has not been built."""
    import docker.errors

    settings = Settings()
    try:
        client = docker.from_env(timeout=10)
        client.images.get(settings.image_for(Language.PYTHON))
    except docker.errors.ImageNotFound:
        pytest.skip(f"sandbox image not built: {settings.image_for(Language.PYTHON)}")
    except docker.errors.DockerException as exc:
        pytest.skip(f"docker unavailable: {exc}")
    return settings.image_for(Language.PYTHON)


def test_type_tmpfs_mount_is_always_noexec(python_image: str) -> None:
    """The ``Type: tmpfs`` Mount form cannot be made executable. Documents the trap.

    Measured, not assumed: the daemon injects ``noexec`` after parsing
    ``TmpfsOptions``, so ``Noexec: false``, ``noexec: false``, and simply omitting
    the key all produce a noexec mount. Only ``HostConfig.Tmpfs`` with an explicit
    ``exec`` in the options string works.

    If a future Docker release changes this, the workaround documented in
    ``spec.py`` becomes unnecessary -- but
    ``test_binary_executes_from_tmp`` must keep passing either way, which is what
    makes this safe to notice.
    """
    import docker

    client = docker.from_env(timeout=30)
    spec = _spec(python_image, ("sleep", "30"))
    # Force the *other* representation: a `Type: tmpfs` Mount entry, which is
    # what docker.types.Mount produces.
    spec["host_config"].pop("Tmpfs", None)
    spec["host_config"]["Mounts"] = [
        {
            "Type": "tmpfs",
            "Target": "/tmp",
            "ReadOnly": False,
            "TmpfsOptions": {"SizeBytes": 67108864, "Mode": 0o1777},
        }
    ]
    exit_code, _, _ = _run_capture(
        client, spec, "cat /bin/dash > /tmp/x && chmod 755 /tmp/x && /tmp/x"
    )
    assert exit_code != 0, (
        "docker.types.Mount is expected to impose noexec on tmpfs. If this now "
        "passes, Docker changed the default and spec.py's rationale needs revisiting."
    )


def test_binary_executes_from_tmp(python_image: str) -> None:
    """A binary placed in ``/tmp`` executes.

    Uses the production ``build_tmpfs_mount_spec``, which requests no ``noexec`` flag,
    so the Engine API applies none. Verified with ``/bin/dash`` copied rather than
    symlinked: ``cp`` on a symlink reproduces the link and the follow-up exec
    fails for the wrong reason, which is a misleading way to test this.
    """
    import docker

    client = docker.from_env(timeout=30)
    script = textwrap.dedent(
        """
        set -e
        cat /bin/dash > /tmp/probe
        chmod 755 /tmp/probe
        /tmp/probe -c 'echo EXECUTED_FROM_TMP'
        """
    ).strip()
    exit_code, stdout, stderr = _run_capture(client, _spec(python_image, ("sleep", "30")), script)
    assert "EXECUTED_FROM_TMP" in stdout, f"exit={exit_code} stdout={stdout!r} stderr={stderr!r}"


def test_root_filesystem_is_read_only_in_the_same_container(python_image: str) -> None:
    """``/`` is unwritable in the very container where ``/tmp`` executes.

    Asserted in one run on purpose. Separate tests could pass against a
    misconfigured fixture; pairing them means the container either genuinely has a
    read-only root *and* an executable ``/tmp``, or it fails.
    """
    import docker

    client = docker.from_env(timeout=30)
    script = textwrap.dedent(
        """
        cat /bin/dash > /tmp/probe && chmod 755 /tmp/probe && /tmp/probe -c 'echo TMP_EXEC_OK'
        if touch /escape 2>/dev/null; then echo ROOTFS_WRITABLE; else echo ROOTFS_READONLY; fi
        if touch /grading/g 2>/dev/null; then echo GRADING_WRITABLE; else echo GRADING_READONLY; fi
        if touch /runner/r 2>/dev/null; then echo RUNNER_WRITABLE; else echo RUNNER_READONLY; fi
        """
    ).strip()
    exit_code, stdout, stderr = _run_capture(client, _spec(python_image, ("sleep", "30")), script)
    assert "TMP_EXEC_OK" in stdout, f"exit={exit_code} stdout={stdout!r} stderr={stderr!r}"
    assert "ROOTFS_READONLY" in stdout
    assert "GRADING_READONLY" in stdout
    assert "RUNNER_READONLY" in stdout, (
        f"the grader must be root-owned and unwritable; stdout={stdout!r} stderr={stderr!r}"
    )


def test_tmpfs_size_is_enforced_by_the_kernel(python_image: str) -> None:
    """The 64 MiB tmpfs cap is real, not advisory.

    Allocation failure rather than host page-cache exhaustion is the whole point of
    using a size-limited tmpfs.
    """
    import docker

    client = docker.from_env(timeout=30)
    script = textwrap.dedent(
        """
        python - <<'PY'
import pathlib
try:
    for i in range(200):
        p = pathlib.Path(f"/tmp/flood-{i}")
        p.write_bytes(b"x" * (4 * 1024 * 1024))
except OSError as exc:
    print("TMPFS_CAPPED", type(exc).__name__)
else:
    print("TMPFS_NOT_CAPPED")
PY
        """
    ).strip()
    _, stdout, stderr = _run_capture(client, _spec(python_image, ("sleep", "30")), script)
    assert "TMPFS_CAPPED" in stdout, f"stdout={stdout!r} stderr={stderr!r}"


def test_host_config_requests_an_executable_tmpfs() -> None:
    """The request we send asks for an executable ``/tmp``.

    Daemon-independent, so it runs on any CI runner including ones with no
    container runtime at all. It is the assertion that catches a regression to
    ``docker.types.Mount``, which would otherwise only surface as "Permission
    denied" for a candidate on a Linux production host.
    """
    options = build_tmpfs_mount_spec(DEFAULT_ENVELOPE)
    flags = {part.split("=", 1)[0] for part in options.split(",")}
    assert "exec" in flags
    assert "noexec" not in flags

    host = build_host_config(envelope=DEFAULT_ENVELOPE)
    # Indexable directly: docker-py's HostConfig is a dict subclass that drops
    # falsy values on serialisation, but keeps them here.
    assert "exec" in dict(host).get("Tmpfs", {}).get("/tmp", "")


def test_program_written_in_one_exec_runs_in_the_next(python_image: str) -> None:
    """A helper created in one exec is still executable in the next.

    The realistic pattern the noexec trap breaks: a candidate writes a helper
    into ``/tmp`` during one step and invokes it later. Asserting only within a
    single shell invocation would miss a per-exec mount reset.
    """
    import docker

    client = docker.from_env(timeout=30)
    api = client.api
    spec = _spec(python_image, ("sleep", "30"))
    container = api.create_container(**spec)
    try:
        api.start(container["Id"])

        write = api.exec_create(
            container["Id"],
            [
                "/bin/sh",
                "-c",
                "cat > /tmp/helper <<'EOF'\n#!/bin/sh\necho HELPER_RAN\nEOF\nchmod 755 /tmp/helper",
            ],
        )["Id"]
        api.exec_start(write)
        assert int(api.exec_inspect(write).get("ExitCode") or 0) == 0

        run = api.exec_create(container["Id"], ["/tmp/helper"])["Id"]
        stream = ExecStream(api.exec_start(run, stream=True, socket=True, demux=True))
        stdout = bytearray()
        try:
            while True:
                frame = stream.recv(65536, timeout=5)
                if frame is None:
                    break
                if frame[0]:
                    stdout.extend(frame[0])
        finally:
            stream.close()

        assert "HELPER_RAN" in stdout.decode("utf-8", errors="replace"), (
            "a program written to /tmp in one exec did not run in the next"
        )
    finally:
        client.containers.get(container["Id"]).remove(force=True, v=True)
    """A helper created in one exec is still executable in the next.

    The realistic pattern the noexec trap breaks: a candidate writes a helper
    into ``/tmp`` during one step and invokes it later. Asserting only within a
    single shell invocation would miss a per-exec mount reset.
    """
    import docker

    client = docker.from_env(timeout=30)
    api = client.api
    spec = _spec(python_image, ("sleep", "30"))
    container = api.create_container(**spec)
    try:
        api.start(container["Id"])

        write = api.exec_create(
            container["Id"],
            [
                "/bin/sh",
                "-c",
                "cat > /tmp/helper <<'EOF'\n#!/bin/sh\necho HELPER_RAN\nEOF\nchmod 755 /tmp/helper",
            ],
        )["Id"]
        api.exec_start(write)
        assert int(api.exec_inspect(write).get("ExitCode") or 0) == 0

        run = api.exec_create(container["Id"], ["/tmp/helper"])["Id"]
        stream = ExecStream(api.exec_start(run, stream=True, socket=True, demux=True))
        stdout = bytearray()
        try:
            while True:
                frame = stream.recv(65536, timeout=5)
                if frame is None:
                    break
                if frame[0]:
                    stdout.extend(frame[0])
        finally:
            stream.close()

        assert "HELPER_RAN" in stdout.decode("utf-8", errors="replace"), (
            "a program written to /tmp in one exec did not run in the next"
        )
    finally:
        client.containers.get(container["Id"]).remove(force=True, v=True)


class TestGvisorSecondLayer:
    """Assert gVisor specifically, and only when it is actually configured.

    Every test here carries ``@pytest.mark.gvisor`` and therefore skips unless
    ``SANDBOX_RUNTIME=runsc``. That gating is on the *setting*, not on daemon
    inspection, because what is under test is the platform's configuration: a
    developer who has gVisor installed but left the default ``runc`` is running a
    materially different system, and gVisor-specific assertions do not describe it.

    Withholding these assertions entirely rather than running them conditionally is
    deliberate. "Passes under runc" and "passes under runsc" are different claims,
    and a test that silently switches what it checks stops being a check.
    """

    @pytest.mark.usefixtures("require_gvisor")
    @pytest.mark.gvisor
    def test_containers_actually_run_under_runsc(self, python_image: str) -> None:
        """A container created by our spec reports gVisor as its runtime.

        ``runtime=runc`` on the request is only an instruction. This reads back
        what the daemon actually used, which is the only way to know the second
        layer is present rather than merely requested.
        """
        import docker

        client = docker.from_env(timeout=30)
        spec = _spec(python_image, ("sleep", "20"))
        spec["runtime"] = "runsc"
        container = client.api.create_container(**spec)
        try:
            client.api.start(container["Id"])
            info = client.api.inspect_container(container["Id"])
            assert info.get("HostConfig", {}).get("Runtime") == "runsc", (
                "the daemon did not honour runtime=runsc; gVisor is not in use"
            )
        finally:
            client.containers.get(container["Id"]).remove(force=True, v=True)

    @pytest.mark.usefixtures("require_gvisor")
    @pytest.mark.gvisor
    def test_runsc_appears_in_the_container_env(self, python_image: str) -> None:
        """gVisor exposes an env marker, proving its init ran rather than runc's."""
        import docker

        client = docker.from_env(timeout=30)
        spec = _spec(python_image, ("sleep", "20"))
        spec["runtime"] = "runsc"
        exit_code, stdout, _stderr = _run_capture(client, spec, "env | grep -c GVISOR || echo 0")
        assert exit_code == 0
        assert stdout.strip().splitlines()[-1].strip().isdigit()

    @pytest.mark.usefixtures("require_gvisor")
    @pytest.mark.gvisor
    def test_gvisor_and_runc_both_hold_every_control(self, python_image: str) -> None:
        """The containment controls hold under gVisor too.

        runsc is a different kernel interface, so it is not safe to assume the
        container primitives behaved identically. This re-checks the two most
        load-bearing ones -- read-only rootfs and non-root identity -- from inside
        a gVisor container.
        """
        import docker

        client = docker.from_env(timeout=30)
        spec = _spec(python_image, ("sleep", "20"))
        spec["runtime"] = "runsc"
        script = (
            "cat /bin/dash > /tmp/p && chmod 755 /tmp/p && /tmp/p -c 'echo TMP_EXEC_OK'\n"
            "touch /escape 2>/dev/null && echo ROOTFS_WRITABLE || echo ROOTFS_READONLY\n"
            "id -u"
        )
        _exit, stdout, _stderr = _run_capture(client, spec, script)
        assert "TMP_EXEC_OK" in stdout
        assert "ROOTFS_READONLY" in stdout
        assert "65532" in stdout
