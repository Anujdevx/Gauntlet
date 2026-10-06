"""Structured logging setup shared by the API and the worker.

JSON in every deployed environment so logs are queryable; human-readable
colours when ``log_json=false`` for local development. Either way the key/value
contract is identical, so dashboards do not care where a log line came from.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor


def configure_logging(*, level: str, json_output: bool) -> None:
    """Install structlog as the root logger for the process.

    Must be called once, before any application module logs. Idempotent in
    practice: repeated calls simply rebuild the same configuration chain.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer(sort_keys=True)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # A stdlib handler, not a structlog one: structlog renders into the
    # record via ProcessorFormatter, and the stdlib handler is what actually
    # writes the final bytes to the stream.
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # uvicorn installs its own handlers; route them through ours so access logs
    # carry the same JSON envelope as everything else.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(noisy)
        logger.handlers.clear()
        logger.propagate = True

    # Celery's own loggers are chatty at INFO; keep the app's own logs readable.
    logging.getLogger("celery.utils.functional").setLevel(logging.WARNING)


def get_logger(name: str, **initial_values: Any) -> structlog.stdlib.BoundLogger:
    """Return a bound logger.

    Args:
        name: Dotted logger name, conventionally ``__name__``.
        initial_values: Fields bound to every record from this logger. Never
            pass secrets, tokens, or candidate source code here: these values
            are serialised into shipped logs.
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name, **initial_values)
    return logger
