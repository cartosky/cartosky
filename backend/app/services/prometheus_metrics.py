from __future__ import annotations

import os
import threading
from collections import deque
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

HTTP_REQUEST_DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

_REGISTRY = CollectorRegistry()
_SUMMARY_LOCK = threading.Lock()
_RECENT_HTTP_OBSERVATIONS: deque[tuple[str, float, str]] = deque(maxlen=4096)
_SAMPLE_CACHE_RESULTS: dict[tuple[str, str], int] = {}
_PUBLISHED_RUN_HEALTH: dict[str, dict[str, Any]] = {}
_SAMPLE_CACHE_ENTRIES: dict[str, int] = {}

HTTP_REQUESTS_TOTAL = Counter(
    "cartosky_http_requests_total",
    "Total HTTP requests handled by the CartoSky API.",
    labelnames=("service", "route", "method", "status_class"),
    registry=_REGISTRY,
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "cartosky_http_request_duration_seconds",
    "HTTP request latency for the CartoSky API.",
    labelnames=("service", "route", "method", "status_class"),
    buckets=HTTP_REQUEST_DURATION_BUCKETS,
    registry=_REGISTRY,
)

SAMPLE_CACHE_RESULT_TOTAL = Counter(
    "cartosky_sample_cache_result_total",
    "Sample cache outcomes for point and batch sampling endpoints.",
    labelnames=("endpoint", "result"),
    registry=_REGISTRY,
)

SAMPLE_CACHE_ENTRIES = Gauge(
    "cartosky_sample_cache_entries",
    "Current number of active sample cache entries.",
    labelnames=("endpoint",),
    registry=_REGISTRY,
)

PUBLISHED_RUN_AGE_HOURS = Gauge(
    "cartosky_published_run_age_hours",
    "Age in hours of the latest published run per model.",
    labelnames=("model_id",),
    registry=_REGISTRY,
)

PUBLISHED_RUN_COMPLETION_RATIO = Gauge(
    "cartosky_published_run_completion_ratio",
    "Completion ratio of the latest published run per model.",
    labelnames=("model_id",),
    registry=_REGISTRY,
)

BUILD_DURATION_SECONDS = Histogram(
    "cartosky_build_duration_seconds",
    "Time in seconds to complete a full model run build.",
    labelnames=("model_id", "cycle_hour"),
    buckets=(60, 120, 300, 600, 900, 1800, 3600, 7200, 14400),
    registry=_REGISTRY,
)

BUILD_DURATION_AVG_MINUTES = Gauge(
    "cartosky_build_duration_avg_minutes",
    "Average build duration in minutes per model and cycle hour.",
    labelnames=("model_id", "cycle_hour"),
    registry=_REGISTRY,
)


def prometheus_enabled() -> bool:
    raw = os.getenv("CARTOSKY_PROMETHEUS_ENABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def status_class_from_code(status_code: int) -> str:
    if status_code < 100:
        return "unknown"
    return f"{int(status_code) // 100}xx"


def observe_http_request(*, route: str, method: str, status_code: int, duration_seconds: float) -> None:
    route_label = str(route).strip() or "unmatched"
    method_label = str(method).strip().upper() or "GET"
    status_class = status_class_from_code(status_code)
    safe_duration = max(0.0, float(duration_seconds))

    HTTP_REQUESTS_TOTAL.labels(
        service="api",
        route=route_label,
        method=method_label,
        status_class=status_class,
    ).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(
        service="api",
        route=route_label,
        method=method_label,
        status_class=status_class,
    ).observe(safe_duration)

    with _SUMMARY_LOCK:
        _RECENT_HTTP_OBSERVATIONS.append((route_label, safe_duration * 1000.0, status_class))


def record_sample_cache_result(*, endpoint: str, result: str, amount: int = 1) -> None:
    endpoint_label = str(endpoint).strip() or "unknown"
    result_label = str(result).strip() or "unknown"
    increment = max(0, int(amount))
    if increment == 0:
        return

    SAMPLE_CACHE_RESULT_TOTAL.labels(endpoint=endpoint_label, result=result_label).inc(increment)
    with _SUMMARY_LOCK:
        key = (endpoint_label, result_label)
        _SAMPLE_CACHE_RESULTS[key] = _SAMPLE_CACHE_RESULTS.get(key, 0) + increment


def set_sample_cache_entries(*, endpoint: str, entries: int) -> None:
    endpoint_label = str(endpoint).strip() or "all"
    safe_entries = max(0, int(entries))
    SAMPLE_CACHE_ENTRIES.labels(endpoint=endpoint_label).set(safe_entries)
    with _SUMMARY_LOCK:
        _SAMPLE_CACHE_ENTRIES[endpoint_label] = safe_entries


def replace_published_run_health(rows: list[dict[str, float | str | bool | None]]) -> None:
    next_snapshot: dict[str, dict[str, Any]] = {}
    for row in rows:
        model_id = str(row.get("model_id") or "").strip().lower()
        if not model_id:
            continue
        age_hours = max(0.0, float(row.get("run_age_hours") or 0.0))
        completion_ratio = min(1.0, max(0.0, float(row.get("completion_ratio") or 0.0)))
        PUBLISHED_RUN_AGE_HOURS.labels(model_id=model_id).set(age_hours)
        PUBLISHED_RUN_COMPLETION_RATIO.labels(model_id=model_id).set(completion_ratio)
        next_snapshot[model_id] = {
            "run_age_hours": age_hours,
            "completion_ratio": completion_ratio,
        }
        freshness_state = row.get("freshness_state")
        if isinstance(freshness_state, str) and freshness_state:
            next_snapshot[model_id]["freshness_state"] = freshness_state
        latest_scan_age_minutes = row.get("latest_scan_age_minutes")
        if isinstance(latest_scan_age_minutes, (int, float)):
            next_snapshot[model_id]["latest_scan_age_minutes"] = max(0.0, float(latest_scan_age_minutes))
        if "usable" in row:
            next_snapshot[model_id]["usable"] = bool(row.get("usable"))
    with _SUMMARY_LOCK:
        _PUBLISHED_RUN_HEALTH.clear()
        _PUBLISHED_RUN_HEALTH.update(next_snapshot)


def observe_build_duration(*, model_id: str, duration_seconds: float, cycle_hour: str | None = None) -> None:
    safe_duration = max(0.0, float(duration_seconds))
    safe_cycle_hour = str(cycle_hour).zfill(2) if cycle_hour is not None else "unknown"
    BUILD_DURATION_SECONDS.labels(model_id=model_id, cycle_hour=safe_cycle_hour).observe(safe_duration)


def set_build_duration_avg(*, model_id: str, cycle_hour: str, avg_minutes: float) -> None:
    BUILD_DURATION_AVG_MINUTES.labels(
        model_id=model_id,
        cycle_hour=cycle_hour,
    ).set(avg_minutes)


def reset_build_duration_avgs() -> None:
    BUILD_DURATION_AVG_MINUTES.clear()


def metrics_payload() -> bytes:
    return generate_latest(_REGISTRY)


def metrics_content_type() -> str:
    return CONTENT_TYPE_LATEST


def _compute_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = percentile * (len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(len(ordered) - 1, lower_index + 1)
    if lower_index == upper_index:
        return ordered[lower_index]
    lower_weight = upper_index - position
    upper_weight = position - lower_index
    return ordered[lower_index] * lower_weight + ordered[upper_index] * upper_weight


def get_observability_summary() -> dict[str, Any]:
    with _SUMMARY_LOCK:
        recent_requests = list(_RECENT_HTTP_OBSERVATIONS)
        sample_results = dict(_SAMPLE_CACHE_RESULTS)
        published_health = dict(_PUBLISHED_RUN_HEALTH)
        sample_cache_entries = dict(_SAMPLE_CACHE_ENTRIES)

    all_latencies = [duration_ms for _, duration_ms, _ in recent_requests]
    error_count = sum(1 for _, _, status_class in recent_requests if status_class in {"4xx", "5xx"})
    request_count = len(recent_requests)
    point_hits = sample_results.get(("sample", "hit"), 0)
    point_misses = sample_results.get(("sample", "miss"), 0)
    point_hit_rate = None
    if point_hits + point_misses > 0:
        point_hit_rate = round(point_hits / (point_hits + point_misses), 3)

    return {
        "metrics_enabled": prometheus_enabled(),
        "http": {
            "recent_request_count": request_count,
            "p95_ms": round(_compute_percentile(all_latencies, 0.95) or 0.0, 2) if all_latencies else None,
            "error_rate": round(error_count / request_count, 3) if request_count > 0 else None,
        },
        "sample_cache": {
            "point_hit_rate": point_hit_rate,
            "entries": sample_cache_entries.get("all", 0),
            "hits": point_hits,
            "misses": point_misses,
        },
        "published_runs": [
            {
                "model_id": model_id,
                "run_age_hours": round(values["run_age_hours"], 2),
                "completion_ratio": round(values["completion_ratio"], 3),
                "freshness_state": values.get("freshness_state"),
                "latest_scan_age_minutes": (
                    round(float(values["latest_scan_age_minutes"]), 1)
                    if isinstance(values.get("latest_scan_age_minutes"), (int, float))
                    else None
                ),
                "usable": bool(values.get("usable")) if "usable" in values else None,
            }
            for model_id, values in sorted(published_health.items())
        ],
    }


def reset_metrics_for_tests() -> None:
    with _SUMMARY_LOCK:
        _RECENT_HTTP_OBSERVATIONS.clear()
        _SAMPLE_CACHE_RESULTS.clear()
        _PUBLISHED_RUN_HEALTH.clear()
        _SAMPLE_CACHE_ENTRIES.clear()
    HTTP_REQUESTS_TOTAL.clear()
    HTTP_REQUEST_DURATION_SECONDS.clear()
    SAMPLE_CACHE_RESULT_TOTAL.clear()
    SAMPLE_CACHE_ENTRIES.clear()
    PUBLISHED_RUN_AGE_HOURS.clear()
    PUBLISHED_RUN_COMPLETION_RATIO.clear()
    BUILD_DURATION_SECONDS.clear()
