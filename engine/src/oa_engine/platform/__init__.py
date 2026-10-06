"""Cross-service infrastructure: configuration, logging, and metrics.

``platform`` holds the concerns that both the API and the workers need and that
must have exactly one implementation. Duplicating any of these across services
is how the security envelope ends up differing between the process that thinks
it is enforcing it and the process that is.

The skill-modelling subpackages (``bkt``, ``elo``, ``dag``, ``retrieval``,
``mutation``, ``llm``) sit alongside this one and are pure: they take values,
return values, and touch nothing here.
"""

from __future__ import annotations
