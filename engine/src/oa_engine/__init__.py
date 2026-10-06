"""Adaptive engine: skill modelling, adaptive retrieval, LLM task mutation.

Subpackages are imported lazily by consumers; nothing here has import-time side
effects beyond :mod:`oa_engine.platform.config` validating the security envelope.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
