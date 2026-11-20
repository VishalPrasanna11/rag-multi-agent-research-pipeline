"""Tracing: OpenTelemetry spans for run nodes and LLM calls.

Export is enabled only when OTEL_EXPORTER_OTLP_ENDPOINT is set (any OTLP/HTTP backend: Honeycomb, Grafana,
or Langfuse's OTel endpoint for LLM tracing; put auth in OTEL_EXPORTER_OTLP_HEADERS). Without it, spans are
created and dropped at near-zero cost.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import APP_VERSION, get_settings

_configured = False


def setup_tracing() -> None:
    global _configured
    if _configured:
        return
    s = get_settings()
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": "earnings-intel",
                "service.version": APP_VERSION,
                "deployment.environment": s.ENV,
            }
        )
    )
    if s.OTEL_EXPORTER_OTLP_ENDPOINT:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True


tracer = trace.get_tracer("earnings-intel")


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    with tracer.start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v if isinstance(v, str | int | float | bool) else str(v))
        yield sp
