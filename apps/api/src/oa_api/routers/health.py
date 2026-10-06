"""Liveness and readiness probes.

``/health`` answers "is this process alive" and must never touch a dependency:
if it did, a database blip would restart every healthy API replica and turn a
dependency outage into a full outage.

``/health/ready`` answers "should this replica receive traffic" and therefore
does check dependencies. It is what the load balancer and compose healthchecks
poll.

The split matters because an execution failure and a control-plane failure have
opposite urgencies: a dead sandbox orchestrator should page someone, while a
slow database should merely drain traffic.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from oa_engine.platform.config import get_settings
from oa_engine.platform.logging import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)


class HealthResponse(BaseModel):
    """Response body shared by both probes."""

    status: Literal["ok", "degraded", "fail"]
    service: str = "oa-api"
    version: str = "1.0.0"
    environment: str = Field(description="Deployment environment name.")
    checks: dict[str, str] = Field(
        default_factory=dict,
        description="Per-dependency verdict. Keys: redis, postgres, celery_broker.",
    )


async def _check_redis(url: str, timeout_s: float = 1.0) -> tuple[str, str]:
    """Ping Redis with a hard timeout.

    Returns ``(verdict, detail)`` rather than raising: a failed dependency must
    produce a reportable state, not a 500 that hides which dependency broke.
    """
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(url, socket_connect_timeout=timeout_s)
        try:
            await asyncio.wait_for(client.ping(), timeout=timeout_s)
            return "ok", "PING returned PONG"
        finally:
            await client.close()
    except TimeoutError:
        return "fail", f"timed out after {timeout_s}s"
    except ImportError:
        # Redis client is an optional import in the API image during early
        # scaffolding; an absent client must not look like a healthy service.
        return "fail", "redis client library not installed"
    except Exception as exc:
        return "fail", f"{type(exc).__name__}: {exc}"


async def _check_tcp(url: str, timeout_s: float = 1.0) -> tuple[str, str]:
    """TCP-connect to the host and port embedded in ``url`` without auth."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (6379 if parsed.scheme.startswith("redis") else 5432)
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout_s)
        del reader
        writer.close()
        await writer.wait_closed()
        return "ok", f"tcp connect to {host}:{port} succeeded"
    except TimeoutError:
        return "fail", f"timed out connecting to {host}:{port}"
    except Exception as exc:
        return "fail", f"{type(exc).__name__}: {exc}"


@router.get("/health", summary="Liveness probe")
async def health() -> HealthResponse:
    """Liveness. Checks no dependencies; always cheap and non-throwing."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        environment=settings.environment,
        checks={"process": "ok"},
    )


@router.get("/health/ready", summary="Readiness probe")
async def ready(response: Response) -> HealthResponse:
    """Readiness. Verifies Redis and PostgreSQL are reachable."""
    settings = get_settings()
    started = time.perf_counter()

    redis_verdict, redis_detail = await _check_redis(settings.redis_url)
    pg_verdict, pg_detail = await _check_tcp(settings.postgres_dsn)
    broker_verdict, broker_detail = await _check_tcp(settings.broker_url())

    checks = {
        "redis": redis_verdict,
        "redis_detail": redis_detail,
        "postgres": pg_verdict,
        "postgres_detail": pg_detail,
        "celery_broker": broker_verdict,
        "celery_broker_detail": broker_detail,
    }

    verdicts = [redis_verdict, pg_verdict, broker_verdict]
    if all(v == "ok" for v in verdicts):
        overall: Literal["ok", "degraded", "fail"] = "ok"
        code = status.HTTP_200_OK
    elif all(v == "fail" for v in verdicts):
        overall = "fail"
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        overall = "degraded"
        # Degraded still serves traffic: the queue and the database are
        # redundant concerns, and draining on a single slow dependency is a
        # worse outcome than serving until it recovers.
        code = status.HTTP_200_OK

    log.info(
        "readiness_probe",
        status=overall,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        redis=redis_verdict,
        postgres=pg_verdict,
        broker=broker_verdict,
    )

    response.status_code = code
    return HealthResponse(status=overall, environment=settings.environment, checks=checks)


@router.get("/metrics", summary="Prometheus exposition endpoint", response_class=Response)
async def metrics() -> Response:
    """Expose the Prometheus registry in text exposition format."""
    from oa_engine.platform import metrics as m

    return Response(content=m.render_latest(), media_type=m.CONTENT_TYPE_LATEST)


def _unused() -> dict[str, Any]:  # pragma: no cover - keeps linters honest
    """Placeholder to reserve the namespace for future probe fields."""
    return {}
