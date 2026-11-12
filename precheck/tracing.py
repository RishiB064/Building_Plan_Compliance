"""D5: OpenTelemetry tracing. One trace follows a request from the API, through the Redis queue, into the worker,
the agent steps, every model call and every tool call, and shows up in Jaeger.

Switched on by OTEL_EXPORTER_OTLP_ENDPOINT. When it is not set (or the packages are missing) everything here is a
no-op, so the service never depends on the tracing backend being up.
"""
import contextlib
import os

_tracer = None


def setup(service: str, exporter=None) -> bool:
    """Start tracing for this process. `exporter` is for tests (an in-memory span exporter)."""
    global _tracer
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint and exporter is None:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError:
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", service)}))
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")))
    else:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        trace.set_tracer_provider(provider)
    except Exception:
        pass
    _tracer = provider.get_tracer("precheck")
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    except Exception:
        pass
    return True


def instrument_fastapi(app) -> None:
    if _tracer is None:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,health")
    except Exception:
        pass


def current_traceparent() -> str:
    """The W3C traceparent of the active span, to store with a queued run so the worker can continue the trace."""
    if _tracer is None:
        return ""
    from opentelemetry import propagate
    carrier: dict = {}
    propagate.inject(carrier)
    return carrier.get("traceparent", "")


@contextlib.contextmanager
def span(name: str, parent_carrier: str | None = None, **attrs):
    if _tracer is None:
        yield None
        return
    from opentelemetry import propagate
    ctx = propagate.extract({"traceparent": parent_carrier}) if parent_carrier else None
    clean = {k: v for k, v in attrs.items() if isinstance(v, (str, int, float, bool))}
    with _tracer.start_as_current_span(name, context=ctx, attributes=clean) as sp:
        yield sp
