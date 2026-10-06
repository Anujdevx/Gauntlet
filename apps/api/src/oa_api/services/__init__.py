"""Business logic that orchestrates persistence, queueing, and the engine.

Routers stay thin; anything with a branch in it belongs here so it can be unit
tested without an HTTP client.
"""

from __future__ import annotations
