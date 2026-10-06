"""Shared pytest fixtures.

Two things worth noting:

* The ``settings`` fixture clears the settings cache and neutralises ambient
  environment variables that would otherwise leak the developer's ``.env`` into
  a test run. Without it, a test can pass locally and fail in CI purely because
  CI has no ``REDIS_URL``.
* Sandbox tests need a real Docker daemon with ``runsc``. Rather than skipping
  silently, the fixture probes once and exposes the reason, so a skipped
  security test is always explicable in the report.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Iterator

import pytest

from oa_engine.platform.config import DEFAULT_ENVELOPE, get_settings

#: Root of the repository, used to locate ``.env.example``.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _declared_env_keys() -> frozenset[str]:
    """Every variable name declared in ``.env.example``.

    Derived by reading the template rather than hardcoding a list, because a
    hardcoded list rots: someone adds a variable to ``.env.example``, a developer's
    local ``.env`` sets it, and every test that constructs :class:`Settings`
    suddenly fails with ``extra_forbidden``. Reading the template makes the
    isolation self-maintaining, so the failure mode cannot reappear.

    ``extra="forbid"`` on Settings is what turns a leaked variable into a loud
    failure instead of a mystery, which is precisely the behaviour we want here.
    """
    template = _REPO_ROOT / ".env.example"
    if not template.is_file():
        return frozenset()
    keys: set[str] = set()
    for line in template.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return frozenset(keys)


#: Variables that must not leak from a developer's environment into tests.
_ISOLATED_ENV_VARS = _declared_env_keys()


@pytest.fixture(autouse=True)
def settings() -> Iterator[None]:
    """Give every test deterministic settings, then restore the environment.

    Two separate leaks have to be closed, and closing only one is a trap that
    produced a real failure here:

    1. Variables in the **process environment**, e.g. exported by a shell or a CI
       job. Popped from ``os.environ``.
    2. The **``.env`` file on disk**, which pydantic-settings reads directly and
       which nothing in ``os.environ`` can shadow. Neutralised by pointing
       ``env_file`` at a path that does not exist.

    Without (2), any developer with a populated ``.env`` gets a test run that
    differs from CI, and ``extra="forbid"`` turns the stray variable into a
    confusing ``extra_forbidden`` failure naming a port rather than a real bug.

    Yields ``None``; tests read configuration through
    :func:`oa_engine.platform.config.get_settings`, exactly as production code
    does, so the fixture validates the real construction path rather than
    bypassing it.
    """
    from oa_engine.platform.config import Settings

    saved_env = {name: os.environ.pop(name, None) for name in _ISOLATED_ENV_VARS}
    original_env_file = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = pathlib.Path("/nonexistent/.env")
    get_settings.cache_clear()
    try:
        yield
    finally:
        Settings.model_config["env_file"] = original_env_file
        for name, value in saved_env.items():
            if value is not None:
                os.environ[name] = value
        get_settings.cache_clear()


def _docker_available() -> tuple[bool, str]:
    """Probe for a reachable Docker daemon. Does NOT require gVisor.

    Kept separate from :func:`_sandbox_available` on purpose. Some security
    properties are about Docker's own behaviour -- notably that a raw tmpfs mount
    is executable while ``docker.types.Mount`` is not -- and demanding gVisor to
    verify them would confine the assertion to one kind of CI runner. Those tests
    would then be red on every macOS developer machine and green only in a single
    job, which is how a security test quietly stops being read.
    """
    try:
        import docker
    except ImportError:
        return False, "the docker SDK is not installed"
    try:
        client = docker.from_env(timeout=5)
        client.ping()
        client.info()
    except Exception as exc:
        return False, f"docker daemon unreachable: {type(exc).__name__}: {exc}"
    return True, "ok"


def _sandbox_available() -> tuple[bool, str]:
    """Probe for a Docker daemon that can actually run a hardened sandbox.

    Requires both a reachable daemon and that the *configured* runtime
    (``SANDBOX_RUNTIME``) is registered on it. macOS and Windows can never satisfy
    the ``runsc`` case: Docker Desktop virtualises the kernel, and gVisor *is* a
    kernel. Under the default ``runc`` setting, this fixture only requires a
    reachable daemon.
    """
    reachable, reason = _docker_available()
    if not reachable:
        return False, reason

    import docker

    from oa_engine.platform.config import get_settings

    wanted = get_settings().sandbox_runtime
    runtimes = docker.from_env(timeout=5).info().get("Runtimes") or {}
    if wanted not in runtimes:
        return False, (
            f"configured runtime {wanted!r} is not registered on this daemon "
            f"(available: {', '.join(sorted(runtimes)) or 'none'})"
        )
    return True, "ok"


@pytest.fixture(scope="session")
def docker_probe() -> tuple[bool, str]:
    """Session-scoped probe of plain Docker reachability."""
    return _docker_available()


@pytest.fixture(scope="session")
def sandbox_probe() -> tuple[bool, str]:
    """Session-scoped probe of Docker plus the ``runsc`` runtime."""
    return _sandbox_available()


@pytest.fixture
def require_docker(docker_probe: tuple[bool, str]) -> None:
    """Skip unless a Docker daemon is reachable. No runtime requirement."""
    available, reason = docker_probe
    if not available:
        pytest.skip(f"this test requires a Docker daemon: {reason}")


@pytest.fixture
def require_gvisor() -> None:
    """Skip unless gVisor is the configured isolation layer.

    Gated on ``SANDBOX_RUNTIME == "runsc"`` rather than on daemon inspection,
    because these tests assert on *the platform's configuration*, not on what a
    particular machine happens to support. A developer with gVisor installed but
    ``SANDBOX_RUNTIME`` left at the ``runc`` default is running a genuinely
    different configuration, and the gVisor-specific assertions do not apply to
    it. Daemon-level unreachability is a separate concern, handled by
    ``require_docker`` and ``require_sandbox``.
    """
    from oa_engine.platform.config import GVISOR_RUNTIME, get_settings

    configured = get_settings().sandbox_runtime
    if configured != GVISOR_RUNTIME:
        pytest.skip(
            f"gVisor-specific test: requires SANDBOX_RUNTIME={GVISOR_RUNTIME}, "
            f"got {configured!r}. Run with SANDBOX_RUNTIME={GVISOR_RUNTIME} to enable."
        )


@pytest.fixture
def require_sandbox(sandbox_probe: tuple[bool, str]) -> None:
    """Skip the calling test unless a hardened sandbox can actually run.

    Skipping rather than weakening the assertions is deliberate. A security test
    that relaxes its checks on a developer laptop and enforces them in CI is a
    test whose CI run is the only one that counts, and nothing tells a reviewer
    that. An explicit skip is honest.
    """
    available, reason = sandbox_probe
    if not available:
        pytest.skip(f"sandbox tests require Docker + {DEFAULT_ENVELOPE.runtime}: {reason}")
