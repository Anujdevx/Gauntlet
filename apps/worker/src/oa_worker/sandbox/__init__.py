"""Zero-trust sandbox: payload staging, container spec, execution harness.

Convenience re-exports are **lazy** (PEP 562). An eager
``from .orchestrator import SandboxOrchestrator`` here makes importing any
submodule of this package pull in the orchestrator, which imports the warm-pool
registry, which imports the pool manager, which imports the container spec in
this package. That loop is real, and it failed at import time on the first
``import oa_worker.pool.manager`` -- which is how it presented inside the worker
container.

Lazy resolution keeps the ergonomics of ``from oa_worker.sandbox import
SandboxOrchestrator`` and removes the cycle structurally, rather than relying on
import ordering that a later refactor will silently break.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only; never executed at runtime
    from oa_engine.execution.payload import PayloadError, SubmissionPayload
    from oa_worker.sandbox.harness import PhaseOutcome, StagedWorkspace
    from oa_worker.sandbox.orchestrator import OrchestratorError, SandboxOrchestrator
    from oa_worker.sandbox.spec import build_container_spec, build_tmpfs_mount_spec

#: Public name -> submodule that owns it.
_EXPORTS: dict[str, str] = {
    "OrchestratorError": "oa_worker.sandbox.orchestrator",
    "PayloadError": "oa_engine.execution.payload",
    "SubmissionPayload": "oa_engine.execution.payload",
    "PhaseOutcome": "oa_worker.sandbox.harness",
    "SandboxOrchestrator": "oa_worker.sandbox.orchestrator",
    "StagedWorkspace": "oa_worker.sandbox.harness",
    "build_container_spec": "oa_worker.sandbox.spec",
    "build_tmpfs_mount_spec": "oa_worker.sandbox.spec",
}

__all__ = [
    "OrchestratorError",
    "PayloadError",
    "PhaseOutcome",
    "SandboxOrchestrator",
    "StagedWorkspace",
    "SubmissionPayload",
    "build_container_spec",
    "build_tmpfs_mount_spec",
]


def __getattr__(name: str) -> Any:
    """Resolve a re-exported symbol on first access.

    Args:
        name: A name listed in ``__all__``.

    Returns:
        The symbol, imported from its owning submodule.

    Raises:
        AttributeError: If ``name`` is not a known export. The module
            ``__getattr__`` protocol requires this so ``hasattr`` and
            ``getattr(obj, name, default)` behave as callers expect.
    """
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    """Advertise the lazy exports to ``dir()`` and tab completion."""
    return list(_EXPORTS)
