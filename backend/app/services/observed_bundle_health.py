from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


OBSERVED_DELAYED_THRESHOLD_MINUTES = 10
OBSERVED_STALE_THRESHOLD_MINUTES = 15
OBSERVED_SOURCE_THRESHOLDS: dict[str, tuple[int, int]] = {
    # Preserve MRMS's established alerting behavior; tests and operational
    # expectations already treat an 8-minute lag as delayed.
    "mrms": (8, OBSERVED_STALE_THRESHOLD_MINUTES),
    # GOES-East publishes on a 15-minute cadence, but freshness is currently
    # evaluated from the frame's scan-valid timestamp rather than from publish
    # time. Keep the runtime health window aligned with the publish-time window
    # so a newly published bundle does not immediately read as delayed.
    "goes-east": (30, 45),
}


def is_observed_model_capability(model_capability: Any | None) -> bool:
    if model_capability is None:
        return False
    product = str(getattr(model_capability, "product", "") or "").strip().lower()
    if product == "obs":
        return True
    ui_constraints = getattr(model_capability, "ui_constraints", {}) or {}
    return str(ui_constraints.get("time_axis_mode", "") or "").strip().lower() == "observed"


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def build_observed_bundle_health(
    *,
    latest_run: str | None,
    manifest: dict[str, Any] | None,
    source: str,
    now_utc: datetime | None = None,
    delayed_threshold_minutes: int | None = None,
    stale_threshold_minutes: int | None = None,
) -> dict[str, Any]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    source_id = str(source).strip().lower() or "observed"
    delayed_threshold, stale_threshold = _resolve_observed_thresholds(
        source=source_id,
        delayed_threshold_minutes=delayed_threshold_minutes,
        stale_threshold_minutes=stale_threshold_minutes,
    )
    expected_frames, available_frames = _manifest_frame_counts(manifest)
    latest_scan_dt = _latest_scan_valid_time(manifest)
    bundle_published_at = parse_iso_datetime(manifest.get("last_updated")) if isinstance(manifest, dict) else None

    latest_scan_age_minutes: int | None = None
    bundle_age_seconds: int | None = None
    observation_to_publish_latency_seconds: int | None = None

    if latest_scan_dt is not None:
        latest_scan_age_minutes = max(0, int((now - latest_scan_dt).total_seconds() // 60))
    if bundle_published_at is not None:
        bundle_age_seconds = max(0, int((now - bundle_published_at).total_seconds()))
    if latest_scan_dt is not None and bundle_published_at is not None:
        observation_to_publish_latency_seconds = max(
            0,
            int((bundle_published_at - latest_scan_dt).total_seconds()),
        )

    freshness_state = "unavailable"
    usable = False
    degraded_reason: str | None = None

    if not latest_run:
        degraded_reason = "no_publishable_bundle"
    elif available_frames <= 0:
        degraded_reason = "no_available_frames"
    elif latest_scan_dt is None:
        degraded_reason = "missing_latest_scan_time"
    else:
        usable = True
        if latest_scan_age_minutes is not None and latest_scan_age_minutes >= stale_threshold:
            freshness_state = "stale"
        elif latest_scan_age_minutes is not None and latest_scan_age_minutes >= max(1, delayed_threshold):
            freshness_state = "delayed"
        else:
            freshness_state = "live"

        if expected_frames > 0 and available_frames < expected_frames:
            degraded_reason = "incomplete_bundle"
        elif freshness_state == "stale":
            degraded_reason = "stale_source"
        elif freshness_state == "delayed":
            degraded_reason = "delayed_source"

    return {
        "source": source_id,
        "time_axis_mode": "observed",
        "latest_scan_valid_time": _isoformat_or_none(latest_scan_dt),
        "latest_scan_age_minutes": latest_scan_age_minutes,
        "bundle_published_at": _isoformat_or_none(bundle_published_at),
        "bundle_age_seconds": bundle_age_seconds,
        "observation_to_publish_latency_seconds": observation_to_publish_latency_seconds,
        "target_frame_count": expected_frames,
        "available_frame_count": available_frames,
        "stale": freshness_state == "stale",
        "usable": usable,
        "degraded_reason": degraded_reason,
        "freshness_state": freshness_state,
    }


def _resolve_observed_thresholds(
    *,
    source: str,
    delayed_threshold_minutes: int | None,
    stale_threshold_minutes: int | None,
) -> tuple[int, int]:
    default_delayed, default_stale = OBSERVED_SOURCE_THRESHOLDS.get(
        source,
        (OBSERVED_DELAYED_THRESHOLD_MINUTES, OBSERVED_STALE_THRESHOLD_MINUTES),
    )
    delayed = default_delayed if delayed_threshold_minutes is None else max(1, int(delayed_threshold_minutes))
    stale_floor = max(delayed + 1, default_stale)
    stale = stale_floor if stale_threshold_minutes is None else max(delayed + 1, int(stale_threshold_minutes))
    return delayed, stale


def _manifest_frame_counts(manifest: dict[str, Any] | None) -> tuple[int, int]:
    if not isinstance(manifest, dict):
        return 0, 0
    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return 0, 0

    expected_total = 0
    available_total = 0
    for entry in variables.values():
        if not isinstance(entry, dict):
            continue
        expected = entry.get("expected_frames")
        available = entry.get("available_frames")
        frames = entry.get("frames")
        expected_total += max(0, int(expected)) if isinstance(expected, int) else (
            len(frames) if isinstance(frames, list) else 0
        )
        available_total += max(0, int(available)) if isinstance(available, int) else (
            len(frames) if isinstance(frames, list) else 0
        )
    return expected_total, available_total


def _latest_scan_valid_time(manifest: dict[str, Any] | None) -> datetime | None:
    if not isinstance(manifest, dict):
        return None
    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return None

    newest: datetime | None = None
    for entry in variables.values():
        if not isinstance(entry, dict):
            continue
        frames = entry.get("frames")
        if not isinstance(frames, list):
            continue
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            parsed = parse_iso_datetime(frame.get("valid_time"))
            if parsed is None:
                continue
            if newest is None or parsed > newest:
                newest = parsed
    return newest


def _isoformat_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
