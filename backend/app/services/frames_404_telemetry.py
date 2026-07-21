"""Telemetry for frames/grid route 404s (build-pipeline roadmap Wave 0, item 4).

Distinguishes residual publish-swap-gap 404s (audit class 2.1 — a published
run is transiently inconsistent while ``scheduler._promote_run_to_published``
swaps its directory) from stale-run-id 404s (class 2.2 — already fixed). Pure
observability: it never alters a response. Recording is best-effort and any
failure is swallowed by the caller.

State lives in-process (the frames routes run in FastAPI's threadpool, so a
lock guards it) and is throttle-persisted to
``data_root/status/frames_404/telemetry.json`` so an API restart keeps the
week of data the roadmap needs.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "1.0"
RECENT_MAXLEN = 50
PER_DAY_RETENTION_DAYS = 14
_PERSIST_THROTTLE_S = 5.0
# Only these reasons carry the 2.1 "did the 404 land inside a publish swap"
# signal, so only they get seconds-since-publish recency sub-buckets.
BUCKETED_REASONS = frozenset({"swap_gap", "manifest_skew"})

_lock = threading.RLock()
_loaded = False
_since_iso: str | None = None
# endpoint -> reason -> cumulative count (never expires)
_cumulative: dict[str, dict[str, int]] = {}
# "YYYY-MM-DD" -> key -> count, key is "<reason>" or "<reason>:<bucket>"
_per_day: dict[str, dict[str, int]] = {}
_recent: deque[dict[str, Any]] = deque(maxlen=RECENT_MAXLEN)
_last_persist_ts = 0.0


def _telemetry_path(data_root: Path) -> Path:
    return Path(data_root) / "status" / "frames_404" / "telemetry.json"


def _today_str(now_ts: float | None = None) -> str:
    ts = time.time() if now_ts is None else now_ts
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _recent_day_strs(days: int, now_ts: float | None = None) -> list[str]:
    ts = time.time() if now_ts is None else now_ts
    base = datetime.fromtimestamp(ts, tz=timezone.utc)
    return [(base - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(days)]


def _bucket_for_seconds(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 1.0:
        return "lt1s"
    if seconds < 5.0:
        return "lt5s"
    return "gte5s"


def _prune_per_day(now_ts: float | None = None) -> None:
    keep = set(_recent_day_strs(PER_DAY_RETENTION_DAYS, now_ts))
    for day in list(_per_day.keys()):
        if day not in keep:
            del _per_day[day]


def _ensure_loaded(data_root: Path) -> None:
    global _loaded, _since_iso, _last_persist_ts
    if _loaded:
        return
    _loaded = True
    _since_iso = datetime.now(tz=timezone.utc).isoformat()
    try:
        payload = json.loads(_telemetry_path(data_root).read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    cumulative = payload.get("cumulative")
    if isinstance(cumulative, dict):
        for endpoint, reasons in cumulative.items():
            if not isinstance(reasons, dict):
                continue
            bucket = _cumulative.setdefault(str(endpoint), {})
            for reason, count in reasons.items():
                try:
                    bucket[str(reason)] = int(count)
                except (TypeError, ValueError):
                    continue
    per_day = payload.get("per_day")
    if isinstance(per_day, dict):
        for day, keys in per_day.items():
            if not isinstance(keys, dict):
                continue
            day_bucket = _per_day.setdefault(str(day), {})
            for key, count in keys.items():
                try:
                    day_bucket[str(key)] = int(count)
                except (TypeError, ValueError):
                    continue
    recent = payload.get("recent")
    if isinstance(recent, list):
        for sample in recent:
            if isinstance(sample, dict):
                _recent.append(sample)
    since = payload.get("since")
    if isinstance(since, str) and since:
        _since_iso = since
    _prune_per_day()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _snapshot_payload() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "since": _since_iso,
        "updated_at": int(time.time()),
        "cumulative": {endpoint: dict(reasons) for endpoint, reasons in _cumulative.items()},
        "per_day": {day: dict(keys) for day, keys in _per_day.items()},
        "recent": list(_recent),
    }


def _persist(data_root: Path, *, force: bool = False) -> None:
    global _last_persist_ts
    now = time.time()
    if not force and (now - _last_persist_ts) < _PERSIST_THROTTLE_S:
        return
    _last_persist_ts = now
    _write_json_atomic(_telemetry_path(data_root), _snapshot_payload())


def record_frames_404(
    *,
    data_root: Path,
    endpoint: str,
    reason: str,
    model: str | None = None,
    run_requested: str | None = None,
    run_resolved: str | None = None,
    var: str | None = None,
    filename_or_fh: str | None = None,
    seconds_since_publish: float | None = None,
) -> None:
    """Record one classified frames/grid 404. Best-effort; never raises."""
    endpoint = str(endpoint).strip() or "unknown"
    reason = str(reason).strip() or "unknown"
    now = time.time()
    with _lock:
        _ensure_loaded(data_root)

        reasons = _cumulative.setdefault(endpoint, {})
        reasons[reason] = reasons.get(reason, 0) + 1

        day = _today_str(now)
        day_bucket = _per_day.setdefault(day, {})
        day_bucket[reason] = day_bucket.get(reason, 0) + 1
        bucket = _bucket_for_seconds(seconds_since_publish)
        if reason in BUCKETED_REASONS and bucket is not None:
            sub_key = f"{reason}:{bucket}"
            day_bucket[sub_key] = day_bucket.get(sub_key, 0) + 1
        _prune_per_day(now)

        _recent.appendleft(
            {
                "ts_iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                "endpoint": endpoint,
                "model": model,
                "run_requested": run_requested,
                "run_resolved": run_resolved,
                "var": var,
                "filename_or_fh": filename_or_fh,
                "reason": reason,
                "seconds_since_publish": (
                    None if seconds_since_publish is None else round(float(seconds_since_publish), 3)
                ),
            }
        )
        _persist(data_root)

    # Prometheus is bounded to (endpoint, reason) labels only; full detail is
    # in the sample deque above. Guarded so a metrics failure never bubbles up.
    try:
        from . import prometheus_metrics

        if prometheus_metrics.prometheus_enabled():
            prometheus_metrics.record_frames_404(endpoint=endpoint, reason=reason)
    except Exception:  # noqa: BLE001 - telemetry must never break the route
        pass


def load_frames_404_summary(data_root: Path) -> dict[str, Any]:
    """Aggregate the current telemetry state for the admin status dashboard.

    Reads live in-process state (frames routes and the admin API share one
    process); lazy-loads the persisted file first so a restart still surfaces
    the retained data.
    """
    with _lock:
        _ensure_loaded(data_root)
        now = time.time()
        _prune_per_day(now)

        totals_by_reason: dict[str, int] = {}
        for reasons in _cumulative.values():
            for reason, count in reasons.items():
                totals_by_reason[reason] = totals_by_reason.get(reason, 0) + count

        today = _today_str(now)
        last_7_days_set = set(_recent_day_strs(7, now))
        today_counts: dict[str, int] = {}
        last_7_days: dict[str, int] = {}
        recency_buckets: dict[str, dict[str, int]] = {
            reason: {"lt1s": 0, "lt5s": 0, "gte5s": 0} for reason in BUCKETED_REASONS
        }
        for day, keys in _per_day.items():
            in_last_7 = day in last_7_days_set
            for key, count in keys.items():
                if ":" in key:
                    reason, bucket = key.split(":", 1)
                    if reason in recency_buckets and bucket in recency_buckets[reason]:
                        recency_buckets[reason][bucket] += count
                    continue
                if day == today:
                    today_counts[key] = today_counts.get(key, 0) + count
                if in_last_7:
                    last_7_days[key] = last_7_days.get(key, 0) + count

        return {
            "since": _since_iso,
            "totals_by_reason": totals_by_reason,
            "totals_by_endpoint": {
                endpoint: dict(reasons) for endpoint, reasons in _cumulative.items()
            },
            "today": today_counts,
            "last_7_days": last_7_days,
            "recency_buckets": recency_buckets,
            "recent": list(_recent),
        }


def reset() -> None:
    """Clear all in-memory state. For test isolation only."""
    global _loaded, _since_iso, _last_persist_ts
    with _lock:
        _loaded = False
        _since_iso = None
        _last_persist_ts = 0.0
        _cumulative.clear()
        _per_day.clear()
        _recent.clear()
