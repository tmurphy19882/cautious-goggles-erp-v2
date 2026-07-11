"""OpenTelemetry tracing for ERP v2.

Sets up a single `TracerProvider` with an OTLP gRPC exporter, plus the
W3C TraceContext propagator so `traceparent` headers flow across services.
"""
from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

logger = logging.getLogger(__name__)

_initialised = False
DEFAULT_SERVICE_NAME = "erp-v2"


def init_tracing(
    service_name: str = DEFAULT_SERVICE_NAME,
    otlp_endpoint: str | None = None,
) -> Tracer:
    """Idempotent tracer init. Returns the singleton tracer."""
    global _initialised
    if _initialised:
        return trace.get_tracer(service_name)

    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.environ.get("ERP_VERSION", "0.1.0"),
            "deployment.environment": os.environ.get("DEPLOY_ENV", "dev"),
        }
    )
    provider = TracerProvider(resource=resource)
    if endpoint:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("OTel: OTLP exporter configured to %s", endpoint)
    else:
        logger.info("OTel: no OTLP endpoint set, spans are in-memory only")
    trace.set_tracer_provider(provider)
    _initialised = True
    return trace.get_tracer(service_name)


def tracer(name: str = DEFAULT_SERVICE_NAME) -> Tracer:
    """Get a tracer without re-initialising. Safe to call from any module."""
    return trace.get_tracer(name)
