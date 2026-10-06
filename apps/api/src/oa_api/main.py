"""FastAPI application factory for the control plane.

The API's job is deliberately narrow: authenticate, persist, enqueue, and
stream. It has no Docker client, no test runner, and no path to user source
code beyond an opaque submission identifier. Enforcing that separation is what
keeps a web-server RCE from becoming a host compromise.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from oa_api.routers import auth, challenges, health, stream
from oa_engine.platform import metrics
from oa_engine.platform.config import get_settings
from oa_engine.platform.logging import configure_logging, get_logger

__all__ = ["create_app"]

log = get_logger(__name__)

DESCRIPTION = """
Control plane for the Adaptive Technical Assessment Simulator.

**This service never executes candidate code.** It accepts a submission,
stores it, hands an opaque `submission_id` to the Celery broker, and streams the
result back. Execution happens on workers behind a zero-trust sandbox, using
hidden unit tests as the only grading mechanism.

LLMs appear in exactly two places, neither of which can affect a pass/fail
grade: task *presentation* mutation, and post-submission interview feedback.
"""


def create_app() -> FastAPI:
    """Build and configure the ASGI application.

    Returned rather than instantiated at module scope so tests can construct an
    app against patched settings without importing side effects.
    """
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Startup and shutdown lifecycle.

        Two things happen at startup, both deliberate:

        * ``validate_environment`` runs first and raises on an unrecognised
          variable in ``.env``. A misspelling is otherwise silently ignored and
          surfaces much later as a mystery -- and a failed boot is a far better
          time to learn about it than a production incident.
        * No eager connection to Postgres or Redis. The app must still boot and
          serve ``/health`` when every dependency is down, otherwise a database
          outage becomes an API outage and there is nothing left to serve
          diagnostics from.
        """
        settings.validate_environment()
        log.info(
            "api_startup",
            environment=settings.environment,
            queue=settings.queue_name,
            log_level=settings.log_level,
            sandbox_runtime=settings.sandbox_runtime,
            gvisor=settings.gvisor_enabled(),
        )
        yield
        from oa_api.models.db import dispose_engine

        await dispose_engine()
        log.info("api_shutdown")

    app = FastAPI(
        title="Adaptive Assessment Simulator API",
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
    )

    # -- CORS ---------------------------------------------------------------
    # Explicit allowlist, never `*`. Credentials are required for the auth
    # cookie flow, and the wildcard-plus-credentials combination is rejected by
    # browsers anyway, so an allowlist is both correct and intentional.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.web_origin.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Submission-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    # -- Observability middleware -------------------------------------------
    @app.middleware("http")
    async def instrument(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Attach a request id, time the handler, and emit metrics.

        The request id is bound into the structlog context so every log line
        emitted while handling the request -- including from downstream
        services that inherit the context -- can be joined back together.
        """
        request_id = request.headers.get("X-Request-ID") or _mint_request_id()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = time.perf_counter() - started
            # Label with the *route template*, not the raw path: raw paths embed
            # unbounded resource ids and would explode metric cardinality.
            route = request.scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            metrics.HTTP_REQUESTS.labels(
                method=request.method, path=template, status=str(status_code)
            ).inc()
            metrics.HTTP_LATENCY.labels(method=request.method, path=template).observe(elapsed)
            log.info(
                "http_request",
                status_code=status_code,
                duration_ms=round(elapsed * 1000, 2),
                client=request.client.host if request.client else None,
            )
            structlog.contextvars.clear_contextvars()

    # -- security headers ---------------------------------------------------
    @app.middleware("http")
    async def security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Attach conservative baseline browser security headers.

        The API serves JSON only and must never be framed or used as a script
        source, which matters because the sandbox hosts attacker-controlled
        strings that a naive frontend might otherwise reflect.
        """
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        return response

    # Health first: it is the only router that must answer when everything else is
    # broken.
    # -- error handling ----------------------------------------------------

    @app.exception_handler(ResponseValidationError)
    async def on_response_validation_error(
        request: Request, exc: ResponseValidationError
    ) -> Response:
        """Log why a response failed to serialise, then return an opaque 500.

        Without this handler FastAPI logs only the exception *type*, so a
        response-model mismatch is undiagnosable from production logs -- the class
        of bug most likely to appear there, because it only appears once a
        response shape drifts from its declared model.

        The client gets a generic message on purpose. The detail names internal
        fields, which is free reconnaissance.
        """
        log.error(
            "response_validation_failed",
            path=request.url.path,
            errors=exc.errors()[:10],
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "The server produced an invalid response."},
        )

    @app.exception_handler(Exception)
    async def on_unhandled_exception(request: Request, exc: Exception) -> Response:
        """Catch-all that logs the traceback and returns an opaque 500.

        The traceback goes to the log, never to the client. An unhandled error in
        a request handler must not become an information leak about the internals
        of a grading platform.
        """
        log.exception("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(challenges.router)
    # Mounted last. A WebSocket route intercepts an entire path prefix, so putting
    # it earlier would shadow the REST routes under /submissions/{id}.
    app.include_router(stream.router)

    @app.get("/version", tags=["meta"], summary="Build metadata")
    async def version() -> dict[str, Any]:
        """Return the running build identity.

        Kept separate from ``/health`` so a deploy can assert the exact build
        on a pod without perturbing liveness semantics.
        """
        return {
            "service": "oa-api",
            "version": app.version,
            "environment": settings.environment,
        }

    return app


def _mint_request_id() -> str:
    """Generate a correlation id for a request that arrived without one."""
    import uuid

    return uuid.uuid4().hex
