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

SCREENSHOT_PHASE_DURATION_SECONDS = Histogram(
    "cartosky_screenshot_phase_duration_seconds",
    "Per-phase latency of server-side screenshot rendering.",
    labelnames=("path", "phase"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0),
    registry=_REGISTRY,
)

SCREENSHOT_REQUESTS_TOTAL = Counter(
    "cartosky_screenshot_requests_total",
    "Server-side screenshot render outcomes.",
    labelnames=("path", "outcome"),
    registry=_REGISTRY,
)

FRAMES_404_TOTAL = Counter(
    "cartosky_frames_404_total",
    "Classified 404s from frames/grid serving routes.",
    labelnames=("endpoint", "reason"),
    registry=_REGISTRY,
)

HERBIE_RUNTIME_COUNTER = Gauge(
    "cartosky_herbie_runtime_counter",
    "Cumulative Herbie fetch counter snapshot retained across scheduler process restarts.",
    labelnames=("model_id", "metric"),
    registry=_REGISTRY,
)

HERBIE_RUNTIME_TIMER_COUNT = Gauge(
    "cartosky_herbie_runtime_timer_count",
    "Cumulative Herbie timer observation count retained across scheduler process restarts.",
    labelnames=("model_id", "metric"),
    registry=_REGISTRY,
)

HERBIE_RUNTIME_TIMER_SUM_MILLISECONDS = Gauge(
    "cartosky_herbie_runtime_timer_sum_milliseconds",
    "Cumulative Herbie timer milliseconds retained across scheduler process restarts.",
    labelnames=("model_id", "metric"),
    registry=_REGISTRY,
)

HERBIE_RUNTIME_TIMER_MAX_MILLISECONDS = Gauge(
    "cartosky_herbie_runtime_timer_max_milliseconds",
    "Current process-local Herbie timer maximum milliseconds.",
    labelnames=("model_id", "metric"),
    registry=_REGISTRY,
)

HERBIE_RUNTIME_SNAPSHOT_TIMESTAMP_SECONDS = Gauge(
    "cartosky_herbie_runtime_snapshot_timestamp_seconds",
    "Unix timestamp of the scheduler's latest Herbie runtime snapshot.",
    labelnames=("model_id",),
    registry=_REGISTRY,
)

# --- Fast-path ingestion (ECMWF Open-Meteo source, design §8) ---------------
# Sourced from the scheduler's JSON snapshots, the same handoff the Herbie
# runtime gauges above use. Absent when no scheduler has the fast path on.

FASTPATH_READY_THROUGH_FH = Gauge(
    "cartosky_fastpath_ready_through_fh",
    "Contiguous published forecast-hour frontier per fast-owned (variable, domain).",
    labelnames=("model_id", "var", "domain"),
    registry=_REGISTRY,
)

FASTPATH_STALL_COUNT = Gauge(
    "cartosky_fastpath_stall_count",
    "Fast-path stall events recorded for a run (fetch failures, bad steps, failovers).",
    labelnames=("model_id", "run"),
    registry=_REGISTRY,
)

FASTPATH_BLOCKED_PAIRS = Gauge(
    "cartosky_fastpath_blocked_pairs",
    "(variable, domain) pairs no source will build until an operator intervenes. "
    "Any value above zero is an outage for those variables — alert on it.",
    labelnames=("model_id",),
    registry=_REGISTRY,
)

FASTPATH_PROMOTION_RETRIES_PENDING = Gauge(
    "cartosky_fastpath_promotion_retries_pending",
    "Timesteps staged by the fast path but not yet promoted to the published tree.",
    labelnames=("model_id",),
    registry=_REGISTRY,
)

FASTPATH_CANARY_BIAS = Gauge(
    "cartosky_fastpath_canary_bias",
    "Fast-vs-delayed mean difference on the canary frame, in the variable's units.",
    labelnames=("model_id", "var", "domain"),
    registry=_REGISTRY,
)

FASTPATH_CANARY_MAE = Gauge(
    "cartosky_fastpath_canary_mae",
    "Fast-vs-delayed mean absolute difference on the canary frame.",
    labelnames=("model_id", "var", "domain"),
    registry=_REGISTRY,
)

FASTPATH_CANARY_SYNOPTIC_MAE = Gauge(
    "cartosky_fastpath_canary_synoptic_mae",
    "Fast-vs-delayed mean absolute difference after ~1 degree mean-pooling.",
    labelnames=("model_id", "var", "domain"),
    registry=_REGISTRY,
)

FASTPATH_CANARY_CORR = Gauge(
    "cartosky_fastpath_canary_corr",
    "Fast-vs-delayed correlation on the canary frame.",
    labelnames=("model_id", "var", "domain"),
    registry=_REGISTRY,
)

FASTPATH_CANARY_FAILED = Gauge(
    "cartosky_fastpath_canary_failed",
    "1 when the latest canary comparison for this (variable, domain) tripped a threshold.",
    labelnames=("model_id", "var", "domain"),
    registry=_REGISTRY,
)

FASTPATH_CANARY_SEAM_FAILURES = Gauge(
    "cartosky_fastpath_canary_seam_failures",
    "1 when the latest failover-seam continuity check for this (variable, domain) "
    "failed — the run-cumulative series steps backwards, meaning two sources were "
    "spliced into one accumulation series. Alert on it.",
    labelnames=("model_id", "var", "domain"),
    registry=_REGISTRY,
)

FASTPATH_SNAPSHOT_TIMESTAMP_SECONDS = Gauge(
    "cartosky_fastpath_snapshot_timestamp_seconds",
    "Unix timestamp of the scheduler's latest fast-path metrics snapshot.",
    labelnames=("model_id",),
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


def record_frames_404(*, endpoint: str, reason: str) -> None:
    endpoint_label = str(endpoint).strip() or "unknown"
    reason_label = str(reason).strip() or "unknown"
    FRAMES_404_TOTAL.labels(endpoint=endpoint_label, reason=reason_label).inc()


def replace_herbie_runtime_metrics(rows: list[dict[str, Any]]) -> None:
    HERBIE_RUNTIME_COUNTER.clear()
    HERBIE_RUNTIME_TIMER_COUNT.clear()
    HERBIE_RUNTIME_TIMER_SUM_MILLISECONDS.clear()
    HERBIE_RUNTIME_TIMER_MAX_MILLISECONDS.clear()
    HERBIE_RUNTIME_SNAPSHOT_TIMESTAMP_SECONDS.clear()
    for row in rows:
        model_id = str(row.get("model_id") or "").strip().lower()
        if not model_id:
            continue
        HERBIE_RUNTIME_SNAPSHOT_TIMESTAMP_SECONDS.labels(model_id=model_id).set(
            max(0.0, float(row.get("recorded_at") or 0.0))
        )
        counters = row.get("counters")
        if isinstance(counters, dict):
            for metric, value in counters.items():
                HERBIE_RUNTIME_COUNTER.labels(
                    model_id=model_id,
                    metric=str(metric),
                ).set(max(0, int(value)))
        timers = row.get("timers_ms")
        if isinstance(timers, dict):
            for metric, aggregate in timers.items():
                if not isinstance(aggregate, dict):
                    continue
                labels = {
                    "model_id": model_id,
                    "metric": str(metric),
                }
                HERBIE_RUNTIME_TIMER_COUNT.labels(**labels).set(
                    max(0, int(aggregate.get("count", 0)))
                )
                HERBIE_RUNTIME_TIMER_SUM_MILLISECONDS.labels(**labels).set(
                    max(0.0, float(aggregate.get("sum_ms", 0.0)))
                )
                HERBIE_RUNTIME_TIMER_MAX_MILLISECONDS.labels(**labels).set(
                    max(0.0, float(aggregate.get("max_ms", 0.0)))
                )


def replace_fastpath_metrics(rows: list[dict[str, Any]]) -> None:
    """Republish the fast-path gauges from the scheduler's JSON snapshots.

    Clear-then-set, like :func:`replace_herbie_runtime_metrics`: a pair that
    stops appearing (ownership flipped back to delayed, a run aged out) must
    lose its series rather than freeze at its last value — a stale
    ``fastpath_blocked_pairs`` reading would page for an outage that is over.
    """
    FASTPATH_READY_THROUGH_FH.clear()
    FASTPATH_STALL_COUNT.clear()
    FASTPATH_BLOCKED_PAIRS.clear()
    FASTPATH_PROMOTION_RETRIES_PENDING.clear()
    FASTPATH_CANARY_BIAS.clear()
    FASTPATH_CANARY_MAE.clear()
    FASTPATH_CANARY_SYNOPTIC_MAE.clear()
    FASTPATH_CANARY_CORR.clear()
    FASTPATH_CANARY_FAILED.clear()
    FASTPATH_CANARY_SEAM_FAILURES.clear()
    FASTPATH_SNAPSHOT_TIMESTAMP_SECONDS.clear()

    for row in rows:
        model_id = str(row.get("model_id") or "").strip().lower()
        if not model_id:
            continue
        FASTPATH_SNAPSHOT_TIMESTAMP_SECONDS.labels(model_id=model_id).set(
            max(0.0, float(row.get("recorded_at") or 0.0))
        )
        FASTPATH_BLOCKED_PAIRS.labels(model_id=model_id).set(
            max(0, int(row.get("blocked_pair_count") or 0))
        )
        FASTPATH_PROMOTION_RETRIES_PENDING.labels(model_id=model_id).set(
            max(0, int(row.get("promotion_retries_pending") or 0))
        )
        for entry in row.get("ready_through_fh") or ():
            if not isinstance(entry, dict):
                continue
            frontier = entry.get("ready_through_fh")
            if frontier is None:
                continue
            FASTPATH_READY_THROUGH_FH.labels(
                model_id=model_id,
                var=str(entry.get("var") or ""),
                domain=str(entry.get("domain") or ""),
            ).set(max(0, int(frontier)))
        stalls = row.get("stall_count_by_run")
        if isinstance(stalls, dict):
            for run_id, count in stalls.items():
                try:
                    FASTPATH_STALL_COUNT.labels(
                        model_id=model_id, run=str(run_id)
                    ).set(max(0, int(count)))
                except (TypeError, ValueError):
                    continue
        for result in row.get("canary_results") or ():
            if not isinstance(result, dict):
                continue
            labels = {
                "model_id": model_id,
                "var": str(result.get("var") or ""),
                "domain": str(result.get("domain") or ""),
            }
            # A metric recorded as null is undefined, not zero (a constant
            # field has no correlation) — leave the series absent.
            for gauge, key in (
                (FASTPATH_CANARY_BIAS, "bias"),
                (FASTPATH_CANARY_MAE, "mae"),
                (FASTPATH_CANARY_SYNOPTIC_MAE, "synoptic_mae"),
                (FASTPATH_CANARY_CORR, "corr"),
            ):
                value = result.get(key)
                if value is None:
                    continue
                try:
                    gauge.labels(**labels).set(float(value))
                except (TypeError, ValueError):
                    continue
            FASTPATH_CANARY_FAILED.labels(**labels).set(
                1 if result.get("failed") else 0
            )
        for seam in row.get("canary_seams") or ():
            if not isinstance(seam, dict):
                continue
            # Only a seam that was actually continuity-checked can pass or
            # fail. An instantaneous 9 km→0.25° seam is recorded but expected,
            # so publishing a 0 for it would imply a check that never ran.
            if not seam.get("continuity_checked"):
                continue
            FASTPATH_CANARY_SEAM_FAILURES.labels(
                model_id=model_id,
                var=str(seam.get("var") or ""),
                domain=str(seam.get("domain") or ""),
            ).set(0 if seam.get("ok") else 1)


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


def observe_screenshot_render(
    *,
    path: str,
    success: bool,
    phases: dict[str, float | None],
    queue_depth: int,
) -> None:
    path_label = str(path).strip() or "unknown"
    outcome = "success" if success else "error"
    SCREENSHOT_REQUESTS_TOTAL.labels(path=path_label, outcome=outcome).inc()
    for phase, value in phases.items():
        if value is None:
            continue
        SCREENSHOT_PHASE_DURATION_SECONDS.labels(
            path=path_label,
            phase=str(phase),
        ).observe(max(0.0, float(value)))


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
    SCREENSHOT_PHASE_DURATION_SECONDS.clear()
    SCREENSHOT_REQUESTS_TOTAL.clear()
    HERBIE_RUNTIME_COUNTER.clear()
    HERBIE_RUNTIME_TIMER_COUNT.clear()
    HERBIE_RUNTIME_TIMER_SUM_MILLISECONDS.clear()
    HERBIE_RUNTIME_TIMER_MAX_MILLISECONDS.clear()
    HERBIE_RUNTIME_SNAPSHOT_TIMESTAMP_SECONDS.clear()
