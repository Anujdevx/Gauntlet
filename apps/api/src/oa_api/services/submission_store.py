"""Redis-backed state for the submission pipeline.

Three distinct concerns share one Redis, on separate databases, because they have
opposite failure requirements:

===  ==========================  ==========================================
DB   Contents                    Failure requirement
===  ==========================  ==========================================
0    Celery broker               Never evict: a lost submission is
                                 candidate-visible.
1    Celery results              Evictable: a stale result can be refetched
                                 from Postgres.
2    Payloads, event streams     Short-lived by design. A payload is deleted
                                 the moment a worker loads it, because it holds
                                 every hidden test in the system.
===  ==========================  ============================================

**Payloads are deleted on read.** The payload blob contains candidate code *and*
the hidden tests for its question. Leaving it resident for a full TTL widens the
window in which a Redis dump exposes every grader on the platform. A redelivered
Celery task that finds no payload reports an infrastructure failure rather than
re-running against stale content, which is the correct outcome -- grading twice
would double-charge the skill model.

**Event streams are pub/sub, not durable.** They exist so a browser watching a
submission sees output live. If a client was not connected, it polls
``GET /submissions/{id}``, which reads the durable row. Making the stream durable
would add write amplification to the hot path to serve a case that already has an
answer.
"""

from __future__ import annotations

import json
from typing import Any, Final

import redis
from redis.asyncio import Redis

from oa_engine.platform.config import Settings, get_settings
from oa_engine.platform.logging import get_logger

log = get_logger(__name__, service="oa-api")

__all__ = [
    "PUBLISH_DB",
    "delete_payload",
    "load_payload",
    "publish_event",
    "read_events_since",
    "store_payload",
    "stream_key_for",
]

#: Redis database holding payload blobs and event streams.
PUBLISH_DB: Final[int] = 2

#: How long a payload survives if no worker claims it. Long enough to survive a
#: worker restart, short enough that an abandoned submission does not sit there.
PAYLOAD_TTL_S: Final[int] = 900

#: How long an event stream stays readable for a late-connecting client.
STREAM_TTL_S: Final[int] = 900


def _redis_url(settings: Settings) -> str:
    """Return the Redis URL for the payload/stream database.

    Delegates to ``Settings.payload_url`` rather than deriving it here. Both the
    API (writer) and the worker (reader) must agree on this database, and two
    independent derivations of "which Redis DB" is precisely how they stop
    agreeing -- with no error on either side, just submissions that vanish.
    """
    return settings.payload_url()


def stream_key_for(submission_id: str) -> str:
    """Namespaced stream key for one submission's events."""
    return f"oa:stream:{submission_id}"


def payload_key_for(submission_id: str) -> str:
    """Namespaced payload key for one submission."""
    return f"oa:payload:{submission_id}"


def _client(settings: Settings | None = None) -> Redis[str]:
    """Construct an async Redis client for the payload database."""
    effective = settings or get_settings()
    return Redis.from_url(
        _redis_url(effective),
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=3,
        health_check_interval=30,
    )


async def store_payload(
    settings: Settings | None = None,
    *,
    submission_id: str,
    payload: dict[str, Any],
) -> None:
    """Write a submission payload for a worker to claim.

    Args:
        settings: Injected for tests.
        submission_id: Correlation id; the key is derived from it.
        payload: Candidate files and hidden tests. Serialised with ``json`` because
            the broker and the sandbox are both JSON-only by policy -- a pickle
            payload would be an arbitrary code execution primitive.
    """
    client = _client(settings)
    try:
        await client.set(
            payload_key_for(submission_id),
            json.dumps(payload),
            ex=PAYLOAD_TTL_S,
        )
        log.info("payload_stored", submission_id=submission_id)
    finally:
        await client.close()


async def load_payload(
    settings: Settings | None = None, *, submission_id: str
) -> dict[str, Any] | None:
    """Read and immediately delete a payload.

    Returns ``None`` when the key is absent or already claimed. The delete is the
    point: see the module docstring.
    """
    client = _client(settings)
    try:
        raw = await client.get(payload_key_for(submission_id))
        if raw is None:
            log.info("payload_missing", submission_id=submission_id)
            return None
        await client.delete(payload_key_for(submission_id))
        # `decode_responses=True` means the client hands back str, so no isinstance
        # guard is needed and mypy correctly reports one as unreachable.
        parsed: dict[str, Any] = json.loads(raw)
        return parsed
    finally:
        await client.close()


async def delete_payload(settings: Settings | None = None, *, submission_id: str) -> bool:
    """Remove a payload without reading it. Returns whether it existed.

    Used when a submission is abandoned or a claim fails, so a payload containing
    hidden tests does not outlive its usefulness.
    """
    client = _client(settings)
    try:
        return bool(await client.delete(payload_key_for(submission_id)))
    finally:
        await client.close()


async def publish_event(
    settings: Settings | None = None,
    *,
    submission_id: str,
    event: str,
    data: Any,
) -> None:
    """Append one event to a submission's stream.

    Failures are logged and swallowed. A streaming hiccup must never turn a
    candidate's successful submission into an error -- the durable result is in
    Postgres, and the client falls back to polling.
    """
    client = _client(settings)
    try:
        key = stream_key_for(submission_id)
        pipe = client.pipeline()
        pipe.xadd(key, {"event": event, "data": json.dumps(data)})
        pipe.expire(key, STREAM_TTL_S)
        await pipe.execute()
    except redis.RedisError as exc:
        log.warning("stream_publish_failed", submission_id=submission_id, error=str(exc))
    finally:
        await client.close()


async def read_events_since(
    settings: Settings | None = None,
    *,
    submission_id: str,
    last_id: str = "0-0",
    count: int = 500,
) -> list[dict[str, Any]]:
    """Read buffered events after ``last_id``.

    Backs the reconnect path: a client that dropped its socket reads what it
    missed rather than losing output. Returns at most ``count`` events, because an
    unbounded read on a reconnect is how a client ends up holding a megabyte of
    stdout in memory.
    """
    client = _client(settings)
    try:
        entries = await client.xrange(
            stream_key_for(submission_id), min=last_id, max="+", count=count
        )
    except redis.RedisError as exc:
        log.warning("stream_read_failed", submission_id=submission_id, error=str(exc))
        return []
    finally:
        await client.close()

    events: list[dict[str, Any]] = []
    for entry_id, fields in entries:
        raw = fields.get("data")
        try:
            data: Any = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            data = raw
        events.append({"id": entry_id, "event": fields.get("event", "unknown"), "data": data})
    return events
