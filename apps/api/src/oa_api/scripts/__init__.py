"""Operational scripts run from the command line.

Separate from :mod:`oa_api.services` because these are entrypoints invoked by an
operator (``make seed``), not request-path code. Importing a script must not pull
in routers, middleware, or an application instance.
"""

from __future__ import annotations
