"""Prometheus metrics registry shared by the API and every worker.

One module-level registry so all processes expose a consistent multiset. Metric
names are an operational contract: dashboards and alerts bind to them, so never
rename one in place. Add a new metric and deprecate the old.

Cardinality discipline: ``submission_id`` is deliberately **not** a label. It is
unbounded and would melt Prometheus. Per-submission detail belongs in the log
stream, joined on ``submission_id``.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

#: Explicit registry rather than the global default, so importing this module
#: from a test twice cannot raise duplicate-timeseries errors, and so a test can
#: build an isolated registry when it needs one.
REGISTRY = CollectorRegistry(auto_describe=True)

CONTENT_TYPE = CONTENT_TYPE_LATEST

# -- HTTP ------------------------------------------------------------------


HTTP_REQUESTS = Counter(
    "oa_http_requests_total",
    "HTTP requests handled by the API.",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)

HTTP_LATENCY = Histogram(
    "oa_http_request_duration_seconds",
    "Wall-clock latency of API request handling. Excludes sandbox execution, "
    "which is asynchronous by design and measured by oa_execution_* instead.",
    labelnames=("method", "path"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=REGISTRY,
)

# -- Enqueue path ----------------------------------------------------------


SUBMISSIONS_ENQUEUED = Counter(
    "oa_submissions_enqueued_total",
    "Submissions accepted by the API and handed to the broker.",
    labelnames=("language",),
    registry=REGISTRY,
)

ENQUEUE_FAILURES = Counter(
    "oa_submissions_enqueue_failed_total",
    "Submissions that could not be enqueued, e.g. broker unreachable.",
    registry=REGISTRY,
)

# -- Queue topology --------------------------------------------------------


QUEUE_DEPTH = Gauge(
    "oa_queue_depth",
    "Messages waiting in the sandbox execution queue.",
    labelnames=("queue",),
    registry=REGISTRY,
)

# -- Execution latency, the SLO metric --------------------------------------


EXECUTION_TOTAL = Counter(
    "oa_execution_total",
    "Sandboxed executions that reached a terminal state.",
    labelnames=("language", "status"),
    registry=REGISTRY,
)

EXECUTION_DURATION = Histogram(
    "oa_execution_duration_seconds",
    "End-to-end sandboxed execution latency. Product target: p95 under 3s, under 2s in load tests.",
    labelnames=("language",),
    buckets=(0.2, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 30.0, 60.0),
    registry=REGISTRY,
)

EXECUTION_INFRA_FAILURES = Counter(
    "oa_execution_infrastructure_failures_total",
    "Executions that failed for orchestrator reasons, NOT candidate code. Must "
    "stay near zero: a non-trivial rate means the platform is corrupting the "
    "mastery signal, because infrastructure failures would otherwise be "
    "recorded against a candidate's skill nodes.",
    labelnames=("language",),
    registry=REGISTRY,
)

# -- Warm pool --------------------------------------------------------------


POOL_SIZE = Gauge(
    "oa_warm_pool_size",
    "Idle, pre-started containers currently held, by language and state.",
    labelnames=("language", "state"),
    registry=REGISTRY,
)

POOL_ACQUISITIONS = Counter(
    "oa_warm_pool_acquisitions_total",
    "Warm container acquisitions by outcome. A low warm-hit rate is the "
    "leading indicator of an SLO breach.",
    labelnames=("language", "outcome"),
    registry=REGISTRY,
)

POOL_REPLENISH_ERRORS = Counter(
    "oa_warm_pool_replenish_errors_total",
    "Failures while topping the warm pool back up.",
    labelnames=("language",),
    registry=REGISTRY,
)


def render_latest() -> bytes:
    """Serialise the registry in the Prometheus text exposition format."""
    return generate_latest(REGISTRY)


__all__ = [
    "CONTENT_TYPE",
    "CONTENT_TYPE_LATEST",
    "ENQUEUE_FAILURES",
    "EXECUTION_DURATION",
    "EXECUTION_INFRA_FAILURES",
    "EXECUTION_TOTAL",
    "HTTP_LATENCY",
    "HTTP_REQUESTS",
    "POOL_ACQUISITIONS",
    "POOL_REPLENISH_ERRORS",
    "POOL_SIZE",
    "QUEUE_DEPTH",
    "REGISTRY",
    "SUBMISSIONS_ENQUEUED",
    "render_latest",
]
