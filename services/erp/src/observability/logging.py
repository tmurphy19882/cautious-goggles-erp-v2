"""Structured logging for ERP v2.

We use `structlog` for ergonomics and render JSON to stdout. The stdlib
`logging` module is bridged so third-party libs (sqlalchemy, aiokafka,
uvicorn) emit structured records too.

Every log line includes `trace_id` and `span_id` when an OTel context is
active, plus `tenant_id` and `request_id` when set on a contextvar.
"""
from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

# ContextVars populated by ObservabilityMiddleware and read by the log
# processor. Default to None so logs work outside a request scope.
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
span_id_var: ContextVar[str | None] = ContextVar("span_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def _add_context(_, __, event_dict: dict) -> dict:
    """Inject request-scoped context into every log record."""
    if (tid := trace_id_var.get()):
        event_dict.setdefault("trace_id", tid)
    if (sid := span_id_var.get()):
        event_dict.setdefault("span_id", sid)
    if (tenant := tenant_id_var.get()):
        event_dict.setdefault("tenant_id", tenant)
    if (rid := request_id_var.get()):
        event_dict.setdefault("request_id", rid)
    return event_dict


def init_logging(level: str = "INFO") -> None:
    """Idempotently configure structlog + stdlib logging as JSON to stdout."""
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        _add_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet noisy libs unless we're at DEBUG.
    for noisy in ("sqlalchemy.engine", "aiokafka", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(max(getattr(logging, level), logging.INFO))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
