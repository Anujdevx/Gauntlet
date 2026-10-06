"""Warm pool of pre-started, single-use sandbox containers.

Lazy re-exports for the same reason as :mod:`oa_worker.sandbox`: an eager import
here makes :mod:`oa_worker.pool.manager` and :mod:`oa_worker.sandbox.orchestrator`
mutually recursive through the package ``__init__`` files.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only; never executed at runtime
    from oa_worker.pool.manager import PooledContainer, WarmPoolManager
    from oa_worker.pool.registry import (
        get_warm_pool,
        reset_warm_pool,
        start_warm_pool,
        stop_warm_pool,
    )

#: Public name -> submodule that owns it.
_EXPORTS: dict[str, str] = {
    "PooledContainer": "oa_worker.pool.manager",
    "WarmPoolManager": "oa_worker.pool.manager",
    "get_warm_pool": "oa_worker.pool.registry",
    "reset_warm_pool": "oa_worker.pool.registry",
    "start_warm_pool": "oa_worker.pool.registry",
    "stop_warm_pool": "oa_worker.pool.registry",
}

__all__ = [
    "PooledContainer",
    "WarmPoolManager",
    "get_warm_pool",
    "reset_warm_pool",
    "start_warm_pool",
    "stop_warm_pool",
]


def __getattr__(name: str) -> Any:
    """Resolve a re-exported symbol on first access.

    Args:
        name: A name listed in ``__all__``.

    Returns:
        The symbol, imported from its owning submodule.

    Raises:
        AttributeError: If ``name`` is not a known export.
    """
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    """Advertise the lazy exports to ``dir()`` and tab completion."""
    return list(_EXPORTS)
