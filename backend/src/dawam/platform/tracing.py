"""OpenTelemetry tracing of the AI side, off unless an OTLP endpoint is configured.

One span per model call and per tool call, named and attributed after the GenAI semantic
conventions. A span carries metadata only: model, token counts, tool name and (as the span's
own duration) latency. Prompt, answer, tool arguments and tool results are added only when
``DAWAM_OTEL_CAPTURE_CONTENT`` is on, and then redacted like everything else DAWAM stores.

A broken exporter must never fail an answer: starting tracing never raises.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from dawam.platform.pii_validators import redact_text

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 8000
"""A captured field is cut to this length, so one span stays small."""


class TracingSettings(Protocol):
    otel_endpoint: str | None
    otel_capture_content: bool
    otel_service_name: str


class _NoSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        pass


_NO_SPAN: Any = _NoSpan()


class ModelSpan:
    def __init__(self, span: Span, tracing: Tracing) -> None:
        self._span = span
        self._tracing = tracing

    def usage(self, input_tokens: int, output_tokens: int) -> None:
        self._span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        self._span.set_attribute("gen_ai.usage.output_tokens", output_tokens)

    def content(self, prompt: str, answer: str) -> None:
        if self._tracing.capture_content:
            self._span.set_attribute("gen_ai.input.messages", self._tracing.redacted(prompt))
            self._span.set_attribute("gen_ai.output.messages", self._tracing.redacted(answer))


class ToolSpan:
    def __init__(self, span: Span, tracing: Tracing) -> None:
        self._span = span
        self._tracing = tracing

    def result(self, status: str, content: str) -> None:
        self._span.set_attribute("dawam.tool.status", status)
        if self._tracing.capture_content:
            self._span.set_attribute("gen_ai.tool.call.result", self._tracing.redacted(content))


class Tracing:
    """Starts the spans. Built without a tracer, every method is a cheap no-op."""

    def __init__(self, tracer: Tracer | None = None, *, capture_content: bool = False) -> None:
        self._tracer = tracer
        self.capture_content = capture_content

    @property
    def enabled(self) -> bool:
        return self._tracer is not None

    def redacted(self, text: str) -> str:
        """``text`` redacted and cut: the only form in which content reaches a span."""
        return redact_text(text)[:MAX_CONTENT_CHARS]

    @contextmanager
    def model_call(self, model: str, role: str) -> Iterator[ModelSpan]:
        with self._span(f"chat {model}", "chat", {"gen_ai.request.model": model}) as span:
            span.set_attribute("dawam.model_role", role)
            yield ModelSpan(span, self)

    @contextmanager
    def tool_call(self, name: str, arguments: dict[str, Any]) -> Iterator[ToolSpan]:
        with self._span(f"execute_tool {name}", "execute_tool", {"gen_ai.tool.name": name}) as span:
            if self.capture_content:
                span.set_attribute(
                    "gen_ai.tool.call.arguments",
                    self.redacted(json.dumps(arguments, default=str)),
                )
            yield ToolSpan(span, self)

    @contextmanager
    def _span(self, name: str, operation: str, attributes: dict[str, Any]) -> Iterator[Span]:
        if self._tracer is None:
            yield _NO_SPAN
            return
        # Not made the "current" span: a model call is a generator that stays open while the
        # caller works, and a context attached there cannot be detached safely elsewhere.
        span = self._tracer.start_span(
            name, attributes={"gen_ai.operation.name": operation, **attributes}
        )
        try:
            yield span
        except BaseException as exc:
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        finally:
            span.end()


_current = Tracing()


def tracing() -> Tracing:
    """The process's tracing (off until ``configure_tracing`` turns it on)."""
    return _current


def set_tracing(value: Tracing) -> None:
    global _current
    _current = value


def configure_tracing(
    settings: TracingSettings, *, exporter: SpanExporter | None = None
) -> Tracing:
    """Install the process's tracing from the settings: off without an endpoint, unless an
    ``exporter`` is given (tests pass an in-memory one to read the spans back)."""
    if exporter is None and not settings.otel_endpoint:
        set_tracing(Tracing())
        return _current
    try:
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            processor: Any = BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint))
        else:
            processor = SimpleSpanProcessor(exporter)
        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.otel_service_name})
        )
        provider.add_span_processor(processor)
        set_tracing(
            Tracing(provider.get_tracer("dawam.ai"), capture_content=settings.otel_capture_content)
        )
    except Exception:
        logger.exception("tracing could not start; it stays off")
        set_tracing(Tracing())
    return _current
