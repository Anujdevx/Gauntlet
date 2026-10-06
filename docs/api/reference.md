# API reference

Base URL: `http://localhost:8000`. OpenAPI at `/openapi.json`, Swagger UI at `/docs`.

Phase 1 exposes only health and metrics. Assessment, submission, and streaming
endpoints arrive in Phase 2.

## `GET /health`

Liveness. Touches **no** dependency, so a database outage cannot cause the API to
be restarted into the same outage. This is what compose's `healthcheck` polls.

```json
{
  "status": "ok",
  "service": "oa-api",
  "version": "1.0.0",
  "environment": "local",
  "checks": { "process": "ok" }
}
```

Always `200`.

## `GET /health/ready`

Readiness. Verifies Redis (a real `PING`) and TCP-reaches PostgreSQL and the
broker. This is what a load balancer polls.

```json
{
  "status": "ok",
  "service": "oa-api",
  "version": "1.0.0",
  "environment": "local",
  "checks": {
    "redis": "ok",
    "redis_detail": "PING returned PONG",
    "postgres": "ok",
    "postgres_detail": "tcp connect to postgres:5432 succeeded",
    "celery_broker": "ok",
    "celery_broker_detail": "tcp connect to redis:6379 succeeded"
  }
}
```

| `status` | Code | Meaning |
| --- | --- | --- |
| `ok` | 200 | All dependencies reachable |
| `degraded` | 200 | Some failed. **Still serves traffic.** |
| `fail` | 503 | All failed |

Degraded deliberately returns `200`. Draining every replica on a single slow
dependency converts a partial outage into a total one, and the queue and the
database are independent concerns.

## `GET /version`

Build identity, separate from `/health` so a deploy can assert the exact build on
a pod without perturbing liveness semantics.

## `GET /metrics`

Prometheus text exposition.

| Metric | Type | Labels | Why you care |
| --- | --- | --- | --- |
| `oa_http_requests_total` | counter | `method`, `path`, `status` | Traffic and error rate |
| `oa_http_request_duration_seconds` | histogram | `method`, `path` | Control-plane latency |
| `oa_submissions_enqueued_total` | counter | `language` | Accepted submissions (Phase 2) |
| `oa_queue_depth` | gauge | `queue` | Backlog. Rising means add workers. |
| `oa_execution_total` | counter | `language`, `status` | Throughput and verdict mix |
| `oa_execution_duration_seconds` | histogram | `language` | **The SLO metric.** p95 < 3 s. |
| `oa_execution_infrastructure_failures_total` | counter | `language` | Must stay near zero |
| `oa_warm_pool_size` | gauge | `language`, `state` | Pool health |
| `oa_warm_pool_acquisitions_total` | counter | `language`, `outcome` | Warm-hit rate is the leading SLO indicator |
| `oa_warm_pool_replenish_errors_total` | counter | `language` | Pool not refilling |

### Why `submission_id` is never a label

It is unbounded and would melt Prometheus. Per-submission detail belongs in the
log stream (JSON, `submission_id` bound into every line) and in Redis, joined on
that id.

## Response headers

Every response carries:

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'
Permissions-Policy: geolocation=(), microphone=(), camera=()
```

Conservative because the API serves JSON only and must never be framed or used
as a script source — which matters when a frontend displays candidate-authored
strings and runner output.

CORS uses an explicit `WEB_ORIGIN` allowlist, never `*`, with credentials enabled
for the auth cookie flow.

## Error shape

Phase 1 has no domain endpoints, so there is no application error envelope yet.
Pydantic validation failures use FastAPI's default `422` shape. A Phase 2
envelope will be added alongside the first domain route, not retrofitted here.

## Planned (Phase 2)

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/assessments` | Create from a syllabus spec |
| `GET` | `/api/v1/questions/next` | Next adaptive question |
| `POST` | `/api/v1/submissions` | Enqueue; returns `submission_id` |
| `GET` | `/api/v1/submissions/{id}` | Polling fallback for a result |
| `WS` | `/api/v1/submissions/{id}/stream` | Live stdout, stderr, per-test results |
| `GET` | `/api/v1/skills` | Mastery matrix |
| `POST` | `/api/v1/interviews` | Interview-mode session |

`POST /api/v1/submissions` returns immediately with a `submission_id` and never
blocks on execution. That is the architectural commitment: the web server has no
Docker socket, so it could not execute code even if it wanted to.
