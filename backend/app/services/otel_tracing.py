from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from typing import Any, Callable

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExportResult,
    SpanExporter,
)
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)

_DEFAULT_SERVICE_NAME = "cartosky-api"
_DEFAULT_OTLP_ENDPOINT = "http://127.0.0.1:4318/v1/traces"
_DEFAULT_SAMPLE_RATIO = 0.05
_DEFAULT_SLOW_REQUEST_MS = 1000.0
_PENDING_TRACE_TTL_SECONDS = 60.0
_RECENT_TRACE_LIMIT = 25
_NOISE_ROUTES = frozenset({
    "/auth/twf/status",
    "/metrics",
})


def _env_value(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_value(name).lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _env_float(name: str, default: float) -> float:
    raw = _env_value(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using fallback=%s", name, raw, default)
        return default


def tracing_enabled() -> bool:
    return _env_bool("CARTOSKY_OTEL_ENABLED", False)


def service_name() -> str:
    value = _env_value("CARTOSKY_OTEL_SERVICE_NAME", _DEFAULT_SERVICE_NAME)
    return value or _DEFAULT_SERVICE_NAME


def exporter_endpoint() -> str:
    value = _env_value("CARTOSKY_OTEL_EXPORTER_OTLP_ENDPOINT", _DEFAULT_OTLP_ENDPOINT)
    return value or _DEFAULT_OTLP_ENDPOINT


def sample_ratio() -> float:
    ratio = _env_float("CARTOSKY_OTEL_SAMPLE_RATIO", _DEFAULT_SAMPLE_RATIO)
    return max(0.0, min(1.0, ratio))


def slow_request_ms() -> float:
    threshold = _env_float("CARTOSKY_OTEL_SLOW_REQUEST_MS", _DEFAULT_SLOW_REQUEST_MS)
    return max(1.0, threshold)


@dataclass
class _TraceSummary:
    trace_id: str
    name: str
    route: str | None
    duration_ms: float | None
    status_code: int | None
    decision: str
    ended_at: float


@dataclass
class _PendingTrace:
    spans: list[ReadableSpan]
    first_seen_at: float


class _FilteringSpanExporter(SpanExporter):
    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self._pending: dict[int, _PendingTrace] = {}
        self._recent: deque[_TraceSummary] = deque(maxlen=_RECENT_TRACE_LIMIT)
        self._exported_total = 0
        self._slow_total = 0
        self._error_total = 0
        self._last_export_error: str | None = None

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        now = time.time()
        to_export: list[ReadableSpan] = []

        with self._lock:
            self._prune_pending_locked(now)
            for span in spans:
                trace_id = span.context.trace_id
                pending = self._pending.get(trace_id)
                if pending is None:
                    pending = _PendingTrace(spans=[], first_seen_at=now)
                    self._pending[trace_id] = pending
                pending.spans.append(span)

            completed_trace_ids: list[int] = []
            for trace_id, pending in self._pending.items():
                root_span = self._root_span(pending.spans)
                if root_span is None:
                    continue
                route = self._route_for_span(root_span)
                if route in _NOISE_ROUTES:
                    completed_trace_ids.append(trace_id)
                    continue
                decision = self._decision_for_trace(root_span)
                if decision != "drop":
                    self._recent.appendleft(self._build_summary(root_span, decision))
                    to_export.extend(sorted(pending.spans, key=lambda item: item.start_time))
                    self._exported_total += 1
                    if decision == "slow":
                        self._slow_total += 1
                    elif decision == "error":
                        self._error_total += 1
                completed_trace_ids.append(trace_id)

            for trace_id in completed_trace_ids:
                self._pending.pop(trace_id, None)

        if not to_export:
            return SpanExportResult.SUCCESS

        result = self._inner.export(to_export)
        if result is not SpanExportResult.SUCCESS:
            self._last_export_error = str(result)
        else:
            self._last_export_error = None
        return result

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        flush = getattr(self._inner, "force_flush", None)
        if callable(flush):
            return bool(flush(timeout_millis=timeout_millis))
        return True

    def summary(self) -> dict[str, Any]:
        with self._lock:
            traces = [asdict(item) for item in self._recent]
            last_trace_at = traces[0]["ended_at"] if traces else None
            return {
                "service_name": service_name(),
                "exporter_endpoint": exporter_endpoint(),
                "sample_ratio": sample_ratio(),
                "slow_request_ms": slow_request_ms(),
                "recent": {
                    "exported_traces": self._exported_total,
                    "slow_traces": self._slow_total,
                    "error_traces": self._error_total,
                    "last_trace_at": last_trace_at,
                    "last_export_error": self._last_export_error,
                },
                "traces": traces,
            }

    def reset(self) -> None:
        with self._lock:
            self._pending.clear()
            self._recent.clear()
            self._exported_total = 0
            self._slow_total = 0
            self._error_total = 0
            self._last_export_error = None

    def _prune_pending_locked(self, now: float) -> None:
        stale_trace_ids = [
            trace_id
            for trace_id, pending in self._pending.items()
            if now - pending.first_seen_at > _PENDING_TRACE_TTL_SECONDS
        ]
        for trace_id in stale_trace_ids:
            self._pending.pop(trace_id, None)

    @staticmethod
    def _root_span(spans: list[ReadableSpan]) -> ReadableSpan | None:
        for span in spans:
            if span.attributes.get("cartosky.trace.root"):
                return span
        return None

    @staticmethod
    def _trace_id_hex(span: ReadableSpan) -> str:
        return f"{span.context.trace_id:032x}"

    @staticmethod
    def _route_for_span(span: ReadableSpan) -> str | None:
        route = span.attributes.get("http.route")
        if isinstance(route, str) and route:
            return route
        return None

    def _decision_for_trace(self, span: ReadableSpan) -> str:
        duration_ms = span.attributes.get("cartosky.request.duration_ms")
        status_code = span.attributes.get("http.status_code")
        if span.status.status_code is StatusCode.ERROR or (isinstance(status_code, int) and status_code >= 500):
            return "error"
        if bool(span.attributes.get("cartosky.slow_request")) or (
            isinstance(duration_ms, (int, float)) and float(duration_ms) >= slow_request_ms()
        ):
            return "slow"
        trace_bucket = (span.context.trace_id % 10_000) / 10_000.0
        return "sampled" if trace_bucket < sample_ratio() else "drop"

    def _build_summary(self, span: ReadableSpan, decision: str) -> _TraceSummary:
        duration_ms: float | None = None
        if span.start_time is not None and span.end_time is not None:
            duration_ms = max(0.0, (span.end_time - span.start_time) / 1_000_000.0)
        status_code = span.attributes.get("http.status_code")
        return _TraceSummary(
            trace_id=self._trace_id_hex(span),
            name=span.name,
            route=str(span.attributes.get("http.route") or "") or None,
            duration_ms=duration_ms,
            status_code=int(status_code) if isinstance(status_code, int) else None,
            decision=decision,
            ended_at=(span.end_time or 0) / 1_000_000_000.0,
        )


_state_lock = threading.Lock()
_provider: TracerProvider | None = None
_filtering_exporter: _FilteringSpanExporter | None = None
_test_exporter_factory: Callable[[], SpanExporter] | None = None


def _build_inner_exporter() -> SpanExporter:
    if _test_exporter_factory is not None:
        return _test_exporter_factory()
    return OTLPSpanExporter(endpoint=exporter_endpoint(), timeout=5.0)


def _get_provider() -> TracerProvider:
    global _provider, _filtering_exporter
    with _state_lock:
        if _provider is None:
            provider = TracerProvider(resource=Resource.create({"service.name": service_name()}))
            filtering_exporter = _FilteringSpanExporter(_build_inner_exporter())
            if _test_exporter_factory is not None:
                processor = SimpleSpanProcessor(filtering_exporter)
            else:
                processor = BatchSpanProcessor(
                    filtering_exporter,
                    max_queue_size=1024,
                    max_export_batch_size=256,
                    schedule_delay_millis=750,
                    export_timeout_millis=5000,
                )
            provider.add_span_processor(processor)
            _provider = provider
            _filtering_exporter = filtering_exporter
        return _provider


def start_as_current_span(name: str, *, attributes: dict[str, Any] | None = None, kind: SpanKind = SpanKind.INTERNAL):
    if not tracing_enabled():
        return nullcontext(None)
    tracer = _get_provider().get_tracer("cartosky.api")
    return tracer.start_as_current_span(name, kind=kind, attributes=attributes or {})


def current_trace_id() -> str | None:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx or not ctx.is_valid:
        return None
    return f"{ctx.trace_id:032x}"


def set_current_attributes(attributes: dict[str, Any]) -> None:
    span = trace.get_current_span()
    if not span.is_recording():
        return
    for key, value in attributes.items():
        if value is None:
            continue
        span.set_attribute(key, value)


def finalize_request_span(
    span: Span | None,
    *,
    route: str,
    status_code: int,
    duration_seconds: float,
    request_id: str | None = None,
    error: BaseException | None = None,
) -> None:
    if span is None or not span.is_recording():
        return
    duration_ms = max(0.0, duration_seconds * 1000.0)
    span.update_name(f"{span.attributes.get('http.method', 'HTTP')} {route}")
    span.set_attribute("http.route", route)
    span.set_attribute("http.status_code", int(status_code))
    span.set_attribute("cartosky.request.duration_ms", duration_ms)
    span.set_attribute("cartosky.slow_request", duration_ms >= slow_request_ms())
    if request_id:
        span.set_attribute("cartosky.request_id", request_id)
    if error is not None:
        span.record_exception(error)
        span.set_status(Status(StatusCode.ERROR, str(error)))
    elif status_code >= 500:
        span.set_status(Status(StatusCode.ERROR))


def force_flush() -> None:
    provider = _provider
    if provider is not None:
        provider.force_flush(timeout_millis=1000)


def get_traces_summary() -> dict[str, Any]:
    force_flush()
    summary = {
        "enabled": tracing_enabled(),
        "service_name": service_name(),
        "exporter_endpoint": exporter_endpoint(),
        "sample_ratio": sample_ratio(),
        "slow_request_ms": slow_request_ms(),
        "recent": {
            "exported_traces": 0,
            "slow_traces": 0,
            "error_traces": 0,
            "last_trace_at": None,
            "last_export_error": None,
        },
        "traces": [],
    }
    if _filtering_exporter is None:
        return summary
    summary.update(_filtering_exporter.summary())
    summary["enabled"] = tracing_enabled()
    return summary


def configure_test_exporter_factory(factory: Callable[[], SpanExporter] | None) -> None:
    global _test_exporter_factory
    _test_exporter_factory = factory


def reset_for_tests() -> None:
    global _provider, _filtering_exporter, _test_exporter_factory
    with _state_lock:
        provider = _provider
        if provider is not None:
            provider.force_flush(timeout_millis=100)
            provider.shutdown()
        _provider = None
        _filtering_exporter = None
        _test_exporter_factory = None
