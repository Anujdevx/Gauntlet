"""Per-language result adapters normalising into the unified result schema.

Lazy re-exports so importing an adapter pulls in only what that language needs,
which keeps a missing optional dependency confined to the one language that
requires it rather than breaking the whole worker.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only; never executed at runtime
    from oa_worker.parsers.base import ResultParser, get_parser

#: Public name -> submodule that owns it.
_EXPORTS: dict[str, str] = {
    "ResultParser": "oa_worker.parsers.base",
    "get_parser": "oa_worker.parsers.base",
}

__all__ = ["ResultParser", "get_parser"]


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
