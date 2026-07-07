from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import rasterio

from ..models.registry import MODEL_REGISTRY
from .grid import expected_grid_frame_size_bytes, grid_manifest_path, grid_supported
from .observed_bundle_health import build_observed_bundle_health, is_observed_model_capability, parse_iso_datetime
from .run_ids import RUN_ID_RE, parse_run_id_datetime

TELEMETRY_DB_PATH = Path(
    os.environ.get("CARTOSKY_TELEMETRY_DB_PATH")
    or os.environ.get("TWM_TELEMETRY_DB_PATH", "./data/admin_telemetry.sqlite3")
)

MRMS_RUNTIME_ARTIFACTS_PENDING_KEY = "runtime_artifacts_pending"
RUNTIME_ARTIFACT_PENDING_GRACE_SECONDS = 300
DEFAULT_STALLED_RUN_IDLE_MINUTES = 90

ALLOWED_PERF_EVENT_NAMES = {
    "viewer_first_frame",
    "frame_change",
    "loop_start",
    "scrub_latency",
    "variable_switch",
    "tile_fetch",
    "animation_stall",
    "loop_manifest_resolve",
    "loop_decode_ready",
    "loop_decode_to_commit",
    "loop_commit_to_visible",
    "loop_queue_to_visible",
    "loop_first_visible_paint",
    "long_task_blocking",
    "loop_frame_drop_gap",
}

ALLOWED_USAGE_EVENT_NAMES = {
    "model_selected",
    "variable_selected",
    "region_selected",
    "animation_play",
}

ALLOWED_RUM_METRIC_NAMES = {
    "lcp",
    "inp",
    "cls",
    "manifest_fetch_duration",
    "bootstrap_fetch_duration",
    "capabilities_fetch_duration",
    "regions_fetch_duration",
    "frames_fetch_duration",
    "grid_manifest_fetch_duration",
    "grid_binary_fetch_duration",
    "grid_binary_array_buffer_duration",
    "grid_texture_prepare_duration",
    "grid_texture_upload_duration",
    "grid_webgl1_expand_duration",
    "sample_request_duration",
    "sample_batch_request_duration",
    "contour_fetch_duration",
    "first_map_render_duration",
    "first_overlay_visible_duration",
    "product_switch_paint_duration",
    "tile_request_failure_count",
    "animation_stall_count",
    "frame_drop_bucket",
}

RUM_METRIC_UNITS = {
    "lcp": "ms",
    "inp": "ms",
    "cls": "score",
    "manifest_fetch_duration": "ms",
    "bootstrap_fetch_duration": "ms",
    "capabilities_fetch_duration": "ms",
    "regions_fetch_duration": "ms",
    "frames_fetch_duration": "ms",
    "grid_manifest_fetch_duration": "ms",
    "grid_binary_fetch_duration": "ms",
    "grid_binary_array_buffer_duration": "ms",
    "grid_texture_prepare_duration": "ms",
    "grid_texture_upload_duration": "ms",
    "grid_webgl1_expand_duration": "ms",
    "sample_request_duration": "ms",
    "sample_batch_request_duration": "ms",
    "contour_fetch_duration": "ms",
    "first_map_render_duration": "ms",
    "first_overlay_visible_duration": "ms",
    "product_switch_paint_duration": "ms",
    "tile_request_failure_count": "count",
    "animation_stall_count": "count",
    "frame_drop_bucket": "count",
}

NETWORK_DIAGNOSTIC_METRIC_NAMES = (
    "bootstrap_fetch_duration",
    "capabilities_fetch_duration",
    "regions_fetch_duration",
    "manifest_fetch_duration",
    "frames_fetch_duration",
    "grid_manifest_fetch_duration",
    "grid_binary_fetch_duration",
    "grid_binary_array_buffer_duration",
    "grid_texture_prepare_duration",
    "grid_texture_upload_duration",
    "grid_webgl1_expand_duration",
    "sample_request_duration",
    "sample_batch_request_duration",
    "contour_fetch_duration",
)

NETWORK_DIAGNOSTIC_LABELS = {
    "bootstrap_fetch_duration": "Bootstrap",
    "capabilities_fetch_duration": "Capabilities",
    "regions_fetch_duration": "Regions",
    "manifest_fetch_duration": "Manifest",
    "frames_fetch_duration": "Frames",
    "grid_manifest_fetch_duration": "Grid Manifest",
    "grid_binary_fetch_duration": "Grid Binary",
    "grid_binary_array_buffer_duration": "Grid ArrayBuffer",
    "grid_texture_prepare_duration": "Grid Texture Prepare",
    "grid_texture_upload_duration": "Grid Texture Upload",
    "grid_webgl1_expand_duration": "Grid WebGL1 Expand",
    "sample_request_duration": "Sample",
    "sample_batch_request_duration": "Sample Batch",
    "contour_fetch_duration": "Contour",
}

WEB_VITAL_THRESHOLDS = {
    "lcp": {
        "good_threshold": 2500.0,
        "needs_improvement_threshold": 4000.0,
    },
    "inp": {
        "good_threshold": 200.0,
        "needs_improvement_threshold": 500.0,
    },
    "cls": {
        "good_threshold": 0.1,
        "needs_improvement_threshold": 0.25,
    },
}

PERF_TARGETS_MS = {
    "viewer_first_frame": 1500.0,
    "frame_change": 250.0,
    "loop_start": 1000.0,
    "scrub_latency": 150.0,
    "variable_switch": 600.0,
    "tile_fetch": 800.0,
    "animation_stall": 750.0,
    "loop_manifest_resolve": 400.0,
    "loop_decode_ready": 250.0,
    "loop_decode_to_commit": 120.0,
    "loop_commit_to_visible": 80.0,
    "loop_queue_to_visible": 120.0,
    "loop_first_visible_paint": 80.0,
    "long_task_blocking": 50.0,
    "loop_frame_drop_gap": 500.0,
}

STATUS_KEEP_RUNS_PER_MODEL = 4
STATUS_RESULTS_CACHE_TTL_SECONDS = max(
    1.0,
    float(os.environ.get("CARTOSKY_ADMIN_STATUS_CACHE_TTL_SECONDS") or os.environ.get("TWM_ADMIN_STATUS_CACHE_TTL_SECONDS") or "30"),
)

_db_init_lock = threading.Lock()
_db_initialized = False
_operational_status_cache_lock = threading.Lock()
_operational_status_cache: dict[tuple[str, str | None], dict[str, Any]] = {}


def _ensure_parent_dir(path: Path) -> None:
    parent = path.parent
    if str(parent) and str(parent) != ".":
        parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_parent_dir(TELEMETRY_DB_PATH)
    conn = sqlite3.connect(TELEMETRY_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    global _db_initialized
    if _db_initialized:
        return
    with _db_init_lock:
        if _db_initialized:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS perf_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                member_id INTEGER,
                event_name TEXT NOT NULL,
                duration_ms REAL NOT NULL,
                model_id TEXT,
                variable_id TEXT,
                run_id TEXT,
                region_id TEXT,
                forecast_hour INTEGER,
                device_type TEXT,
                viewport_bucket TEXT,
                page TEXT,
                meta_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_perf_events_event_created
                ON perf_events(event_name, created_at);
            CREATE INDEX IF NOT EXISTS idx_perf_events_created
                ON perf_events(created_at);
            CREATE INDEX IF NOT EXISTS idx_perf_events_model_var_created
                ON perf_events(model_id, variable_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_perf_events_device_created
                ON perf_events(device_type, created_at);

            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                member_id INTEGER,
                event_name TEXT NOT NULL,
                model_id TEXT,
                variable_id TEXT,
                run_id TEXT,
                region_id TEXT,
                forecast_hour INTEGER,
                device_type TEXT,
                viewport_bucket TEXT,
                page TEXT,
                meta_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_usage_events_event_created
                ON usage_events(event_name, created_at);
            CREATE INDEX IF NOT EXISTS idx_usage_events_created
                ON usage_events(created_at);

            CREATE TABLE IF NOT EXISTS rum_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                member_id INTEGER,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                metric_unit TEXT NOT NULL,
                sample_rate REAL,
                model_id TEXT,
                variable_id TEXT,
                run_id TEXT,
                region_id TEXT,
                forecast_hour INTEGER,
                device_type TEXT,
                viewport_bucket TEXT,
                page TEXT,
                meta_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_rum_events_metric_created
                ON rum_events(metric_name, created_at);
            CREATE INDEX IF NOT EXISTS idx_rum_events_created
                ON rum_events(created_at);
            CREATE INDEX IF NOT EXISTS idx_rum_events_device_created
                ON rum_events(device_type, created_at);

            CREATE TABLE IF NOT EXISTS synthetic_perf_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                commit_sha TEXT,
                branch TEXT,
                environment TEXT,
                scenario TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value_ms REAL NOT NULL,
                threshold_ms REAL,
                status TEXT NOT NULL,
                details_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_synthetic_perf_runs_metric_created
                ON synthetic_perf_runs(metric_name, created_at);

            CREATE TABLE IF NOT EXISTS build_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                model_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                cycle_hour TEXT,
                duration_seconds REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_build_events_model_created
                ON build_events(model_id, created_at DESC);

            """
        )
        try:
            conn.execute("ALTER TABLE build_events ADD COLUMN cycle_hour TEXT")
        except Exception:
            pass
        _db_initialized = True


def _normalize_text(value: Any, *, max_length: int = 120) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length]


def _normalize_forecast_hour(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _serialize_meta(value: Any) -> str | None:
    if value is None:
        return None
    try:
        encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return None
    return encoded[:4000]


def _load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _parse_run_id_datetime(run_id: str) -> datetime | None:
    return parse_run_id_datetime(run_id)


def _parse_manifest_timestamp(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(parsed.timestamp())


def _expected_latest_run_time(*, model_id: str, now_utc: datetime) -> datetime | None:
    plugin = MODEL_REGISTRY.get(model_id)
    capabilities = getattr(plugin, "capabilities", None) if plugin is not None else None
    run_discovery = getattr(capabilities, "run_discovery", {}) if capabilities is not None else {}
    cycle_release_offsets = run_discovery.get("stale_cycle_release_minutes_by_hour") if isinstance(run_discovery, dict) else None
    if isinstance(cycle_release_offsets, dict):
        resolved_offsets: dict[int, int] = {}
        for raw_hour, raw_minutes in cycle_release_offsets.items():
            try:
                hour = int(raw_hour)
                minutes = int(raw_minutes)
            except (TypeError, ValueError):
                continue
            if 0 <= hour <= 23 and minutes >= 0:
                resolved_offsets[hour] = minutes

        if resolved_offsets:
            latest_expected: datetime | None = None
            base_day = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            for day_offset in (-1, 0, 1):
                day_base = base_day + timedelta(days=day_offset)
                for hour, release_offset_minutes in resolved_offsets.items():
                    cycle_dt = day_base.replace(hour=hour)
                    if cycle_dt > now_utc:
                        continue
                    release_dt = cycle_dt + timedelta(minutes=release_offset_minutes)
                    if release_dt <= now_utc and (latest_expected is None or cycle_dt > latest_expected):
                        latest_expected = cycle_dt
            if latest_expected is not None:
                return latest_expected

    cadence = int(run_discovery.get("cycle_cadence_hours") or 0)
    fallback_lag = int(run_discovery.get("fallback_lag_hours") or 0)
    if cadence <= 0:
        return None
    reference = now_utc - timedelta(hours=max(0, fallback_lag))
    floored_hour = reference.hour - (reference.hour % cadence)
    return reference.replace(hour=floored_hour, minute=0, second=0, microsecond=0)


def _stalled_run_idle_minutes(model_id: str) -> int:
    plugin = MODEL_REGISTRY.get(model_id)
    capabilities = getattr(plugin, "capabilities", None) if plugin is not None else None
    run_discovery = getattr(capabilities, "run_discovery", {}) if capabilities is not None else {}
    if isinstance(run_discovery, dict):
        try:
            configured = int(run_discovery.get("stalled_run_idle_minutes") or 0)
        except (TypeError, ValueError):
            configured = 0
        if configured > 0:
            return configured
    return DEFAULT_STALLED_RUN_IDLE_MINUTES


def clear_operational_status_cache() -> None:
    with _operational_status_cache_lock:
        _operational_status_cache.clear()


def _published_run_ids(data_root: Path, model_id: str, *, keep_runs: int) -> list[str]:
    model_root = data_root / "published" / model_id
    if not model_root.is_dir():
        return []
    run_ids = sorted(
        (path.name for path in model_root.iterdir() if path.is_dir() and RUN_ID_RE.match(path.name)),
        key=lambda run_id: (
            _parse_run_id_datetime(run_id).timestamp() if _parse_run_id_datetime(run_id) is not None else float("-inf"),
            run_id,
        ),
        reverse=True,
    )
    return run_ids[: max(1, int(keep_runs))]


def _value_cog_path(data_root: Path, model_id: str, run_id: str, variable_id: str, forecast_hour: int) -> Path:
    return data_root / "published" / model_id / run_id / variable_id / f"fh{forecast_hour:03d}.val.cog.tif"


def _sidecar_path(data_root: Path, model_id: str, run_id: str, variable_id: str, forecast_hour: int) -> Path:
    return data_root / "published" / model_id / run_id / variable_id / f"fh{forecast_hour:03d}.json"


def _time_axis_mode_for_model(model_id: str) -> str:
    plugin = MODEL_REGISTRY.get(model_id)
    capabilities = getattr(plugin, "capabilities", None) if plugin is not None else None
    constraints = getattr(capabilities, "ui_constraints", {}) if capabilities is not None else {}
    raw = str(constraints.get("time_axis_mode", "") or "").strip().lower()
    return raw if raw in {"observed", "valid"} else "forecast"


def _variable_render_substrates(model_id: str, variable_id: str) -> list[str]:
    plugin = MODEL_REGISTRY.get(model_id)
    capabilities = getattr(plugin, "capabilities", None) if plugin is not None else None
    variable_catalog = getattr(capabilities, "variable_catalog", {}) if capabilities is not None else {}
    capability = variable_catalog.get(variable_id) if isinstance(variable_catalog, dict) else None
    configured = getattr(capability, "render_substrates", None) if capability is not None else None
    if isinstance(configured, (list, tuple)):
        normalized: list[str] = []
        for item in configured:
            substrate = str(item or "").strip().lower()
            if substrate and substrate not in normalized:
                normalized.append(substrate)
        if normalized:
            return normalized
    return ["grid"]


def _artifact_variable_id(model_id: str, variable_id: str, entry: dict[str, Any] | None = None) -> str:
    plugin = MODEL_REGISTRY.get(model_id)
    if plugin is None or not hasattr(plugin, "resolve_runtime_var_id"):
        return variable_id

    ensemble_view = None
    if isinstance(entry, dict):
        raw_ensemble_view = entry.get("ensemble_view")
        if isinstance(raw_ensemble_view, str) and raw_ensemble_view.strip():
            ensemble_view = raw_ensemble_view.strip().lower()

    resolved = str(plugin.resolve_runtime_var_id(variable_id, ensemble_view)).strip()
    return resolved or variable_id


def _vector_artifact_paths(
    data_root: Path,
    model_id: str,
    run_id: str,
    variable_id: str,
    forecast_hour: int,
    sidecar: dict[str, Any] | None,
) -> list[Path]:
    if not isinstance(sidecar, dict):
        return []
    vector_layers = sidecar.get("vector_layers")
    if not isinstance(vector_layers, dict):
        return []
    var_root = data_root / "published" / model_id / run_id / variable_id
    paths: list[Path] = []
    for layer_meta in vector_layers.values():
        relative_path = layer_meta.get("path") if isinstance(layer_meta, dict) else None
        if isinstance(relative_path, str) and relative_path.strip():
            paths.append(var_root / relative_path.strip())
    return paths


def _append_sample_path(sample_paths: list[dict[str, Any]], payload: dict[str, Any]) -> None:
    if len(sample_paths) < 6:
        sample_paths.append(payload)


def _grid_runtime_frames_by_hour(
    manifest: dict[str, Any],
) -> tuple[dict[int, list[dict[str, Any]]], int]:
    lods = manifest.get("lods")
    if not isinstance(lods, list):
        return {}, 1

    frames_by_hour: dict[int, list[dict[str, Any]]] = {}
    unreadable_count = 0
    for lod in lods:
        if not isinstance(lod, dict):
            unreadable_count += 1
            continue
        width = int(lod.get("width") or 0)
        height = int(lod.get("height") or 0)
        level = int(lod.get("level") or 0)
        frames = lod.get("frames")
        if width <= 0 or height <= 0 or not isinstance(frames, list):
            unreadable_count += 1
            continue
        for frame in frames:
            if not isinstance(frame, dict):
                unreadable_count += 1
                continue
            fh = frame.get("fh")
            filename = str(frame.get("file") or "").strip()
            if not isinstance(fh, int) or not filename:
                unreadable_count += 1
                continue
            frames_by_hour.setdefault(fh, []).append(
                {
                    "level": level,
                    "width": width,
                    "height": height,
                    "file": filename,
                }
            )
    return frames_by_hour, unreadable_count


def _artifact_validation_hours(frame_hours: list[int], *, include_details: bool) -> list[int]:
    if include_details or len(frame_hours) <= 2:
        return frame_hours
    return sorted({frame_hours[0], frame_hours[-1]})


def _contour_artifact_paths(
    *,
    data_root: Path,
    model_id: str,
    run_id: str,
    variable_id: str,
    sidecar: dict[str, Any] | None,
    contour_keys: Iterable[str],
) -> list[tuple[str, Path]]:
    if not isinstance(sidecar, dict):
        return []
    contours = sidecar.get("contours")
    if not isinstance(contours, dict):
        return []
    var_root = data_root / "published" / model_id / run_id / variable_id
    paths: list[tuple[str, Path]] = []
    for key in contour_keys:
        contour_meta = contours.get(key)
        relative_path = contour_meta.get("path") if isinstance(contour_meta, dict) else None
        if isinstance(relative_path, str) and relative_path.strip():
            paths.append((str(key), var_root / relative_path.strip()))
    return paths


def _manifest_path(data_root: Path, model_id: str, run_id: str) -> Path:
    return data_root / "manifests" / model_id / f"{run_id}.json"


def _run_build_started_at(data_root: Path, model_id: str, run_id: str, manifest_path: Path) -> int | None:
    candidates: list[int] = []
    run_root = data_root / "published" / model_id / run_id
    if run_root.is_dir():
        for root, _dirs, files in os.walk(run_root):
            for filename in files:
                try:
                    candidates.append(int((Path(root) / filename).stat().st_mtime))
                except OSError:
                    continue
    try:
        if manifest_path.exists():
            candidates.append(int(manifest_path.stat().st_mtime))
    except OSError:
        pass
    return min(candidates) if candidates else None


def _scheduled_forecast_hours_for_variable(
    *,
    plugin: Any,
    variable_id: str,
    run_dt: datetime | None,
    expected_frames: int,
    available_hours: list[int],
) -> list[int]:
    cycle_hour = int(run_dt.hour) if run_dt is not None else 0
    raw_hours: Iterable[Any] = []
    if plugin is not None and hasattr(plugin, "scheduled_fhs_for_var"):
        try:
            raw_hours = plugin.scheduled_fhs_for_var(variable_id, cycle_hour)
        except Exception:
            raw_hours = []
    if not raw_hours and plugin is not None and hasattr(plugin, "target_fhs"):
        try:
            raw_hours = plugin.target_fhs(cycle_hour)
        except Exception:
            raw_hours = []

    resolved: list[int] = []
    for raw_hour in raw_hours:
        parsed = _normalize_forecast_hour(raw_hour)
        if parsed is not None:
            resolved.append(parsed)
    if resolved:
        return sorted(set(resolved))
    if available_hours and expected_frames <= len(available_hours):
        return sorted(set(available_hours))
    return []


def record_perf_event(payload: dict[str, Any], *, member_id: int | None = None) -> None:
    event_name = _normalize_text(payload.get("event_name") or payload.get("name"), max_length=64)
    if event_name not in ALLOWED_PERF_EVENT_NAMES:
        raise ValueError("Unsupported performance event")

    duration_ms = float(payload.get("duration_ms"))
    if duration_ms < 0 or duration_ms > 600000:
        raise ValueError("Invalid performance duration")

    created_at = int(time.time())
    session_id = _normalize_text(payload.get("session_id"), max_length=128) or "anonymous"

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO perf_events (
                created_at,
                session_id,
                member_id,
                event_name,
                duration_ms,
                model_id,
                variable_id,
                run_id,
                region_id,
                forecast_hour,
                device_type,
                viewport_bucket,
                page,
                meta_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                session_id,
                member_id,
                event_name,
                duration_ms,
                _normalize_text(payload.get("model_id"), max_length=32),
                _normalize_text(payload.get("variable_id"), max_length=64),
                _normalize_text(payload.get("run_id"), max_length=32),
                _normalize_text(payload.get("region_id"), max_length=32),
                _normalize_forecast_hour(payload.get("forecast_hour")),
                _normalize_text(payload.get("device_type"), max_length=24),
                _normalize_text(payload.get("viewport_bucket"), max_length=24),
                _normalize_text(payload.get("page"), max_length=300),
                _serialize_meta(payload.get("meta")),
            ),
        )


def record_build_duration(*, model_id: str, run_id: str, duration_seconds: float, cycle_hour: str | None = None) -> None:
    safe_model_id = _normalize_text(model_id, max_length=32)
    safe_run_id = _normalize_text(run_id, max_length=32)
    safe_cycle_hour = _normalize_text(cycle_hour, max_length=4) if cycle_hour else None
    safe_duration = max(0.0, float(duration_seconds))
    if not safe_model_id or not safe_run_id:
        return
    created_at = int(time.time())
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO build_events (created_at, model_id, run_id, cycle_hour, duration_seconds)
            VALUES (?, ?, ?, ?, ?)
            """,
            (created_at, safe_model_id, safe_run_id, safe_cycle_hour, safe_duration),
        )


def get_latest_build_durations() -> list[dict[str, Any]]:
    """Return the most recent build duration for each model and cycle hour."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT model_id, run_id, cycle_hour, duration_seconds, created_at
            FROM build_events
            WHERE id IN (
                SELECT MAX(id) FROM build_events GROUP BY model_id, cycle_hour
            )
            ORDER BY model_id, cycle_hour
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_build_duration_averages() -> list[dict[str, Any]]:
    """Return average build duration in minutes per model and cycle hour."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                model_id,
                cycle_hour,
                ROUND(AVG(duration_seconds) / 60.0, 2) as avg_minutes,
                COUNT(*) as build_count
            FROM build_events
            WHERE cycle_hour IS NOT NULL
              AND duration_seconds > 60
            GROUP BY model_id, cycle_hour
            ORDER BY model_id, cycle_hour
            """
        ).fetchall()
    return [dict(row) for row in rows]


def record_usage_event(payload: dict[str, Any], *, member_id: int | None = None) -> None:
    event_name = _normalize_text(payload.get("event_name") or payload.get("name"), max_length=64)
    if event_name not in ALLOWED_USAGE_EVENT_NAMES:
        raise ValueError("Unsupported usage event")

    created_at = int(time.time())
    session_id = _normalize_text(payload.get("session_id"), max_length=128) or "anonymous"

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO usage_events (
                created_at,
                session_id,
                member_id,
                event_name,
                model_id,
                variable_id,
                run_id,
                region_id,
                forecast_hour,
                device_type,
                viewport_bucket,
                page,
                meta_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                session_id,
                member_id,
                event_name,
                _normalize_text(payload.get("model_id"), max_length=32),
                _normalize_text(payload.get("variable_id"), max_length=64),
                _normalize_text(payload.get("run_id"), max_length=32),
                _normalize_text(payload.get("region_id"), max_length=32),
                _normalize_forecast_hour(payload.get("forecast_hour")),
                _normalize_text(payload.get("device_type"), max_length=24),
                _normalize_text(payload.get("viewport_bucket"), max_length=24),
                _normalize_text(payload.get("page"), max_length=300),
                _serialize_meta(payload.get("meta")),
            ),
        )


def record_rum_metric(payload: dict[str, Any], *, member_id: int | None = None) -> None:
    metric_name = _normalize_text(payload.get("metric_name") or payload.get("name"), max_length=64)
    if metric_name not in ALLOWED_RUM_METRIC_NAMES:
        raise ValueError("Unsupported rum metric")

    metric_value = float(payload.get("metric_value"))
    if metric_value < 0 or metric_value > 600000:
        raise ValueError("Invalid rum metric value")

    metric_unit = _normalize_text(payload.get("metric_unit"), max_length=16)
    expected_unit = RUM_METRIC_UNITS.get(metric_name)
    if metric_unit != expected_unit:
        raise ValueError("Invalid rum metric unit")

    sample_rate = payload.get("sample_rate")
    parsed_sample_rate: float | None = None
    if sample_rate is not None:
        parsed_sample_rate = float(sample_rate)
        if parsed_sample_rate <= 0 or parsed_sample_rate > 1:
            raise ValueError("Invalid rum sample rate")

    created_at = int(time.time())
    session_id = _normalize_text(payload.get("session_id"), max_length=128) or "anonymous"

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rum_events (
                created_at,
                session_id,
                member_id,
                metric_name,
                metric_value,
                metric_unit,
                sample_rate,
                model_id,
                variable_id,
                run_id,
                region_id,
                forecast_hour,
                device_type,
                viewport_bucket,
                page,
                meta_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                session_id,
                member_id,
                metric_name,
                metric_value,
                metric_unit,
                parsed_sample_rate,
                _normalize_text(payload.get("model_id"), max_length=32),
                _normalize_text(payload.get("variable_id"), max_length=64),
                _normalize_text(payload.get("run_id"), max_length=32),
                _normalize_text(payload.get("region_id"), max_length=32),
                _normalize_forecast_hour(payload.get("forecast_hour")),
                _normalize_text(payload.get("device_type"), max_length=24),
                _normalize_text(payload.get("viewport_bucket"), max_length=24),
                _normalize_text(payload.get("page"), max_length=300),
                _serialize_meta(payload.get("meta")),
            ),
        )


def _compute_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = max(0.0, min(1.0, percentile)) * (len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(len(ordered) - 1, lower_index + 1)
    weight = position - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * weight


def _build_perf_filters(
    *,
    since_ts: int,
    metric: str | None = None,
    device_type: str | None = None,
    model_id: str | None = None,
    variable_id: str | None = None,
    latest_runs: int | None = None,
) -> tuple[str, list[Any]]:
    clauses = ["created_at >= ?"]
    params: list[Any] = [since_ts]
    if metric:
        clauses.append("event_name = ?")
        params.append(metric)
    if device_type:
        clauses.append("device_type = ?")
        params.append(device_type)
    if model_id:
        clauses.append("model_id = ?")
        params.append(model_id)
    if variable_id:
        clauses.append("variable_id = ?")
        params.append(variable_id)
    if latest_runs is not None:
        eligible_pairs = _resolve_latest_run_pairs(
            since_ts=since_ts,
            latest_runs=latest_runs,
            device_type=device_type,
            model_id=model_id,
            variable_id=variable_id,
        )
        if not eligible_pairs:
            clauses.append("1 = 0")
        else:
            pair_clauses: list[str] = []
            for eligible_model_id, eligible_run_id in eligible_pairs:
                pair_clauses.append("(model_id = ? AND run_id = ?)")
                params.extend([eligible_model_id, eligible_run_id])
            clauses.append("(" + " OR ".join(pair_clauses) + ")")
    return " WHERE " + " AND ".join(clauses), params


def _resolve_latest_run_pairs(
    *,
    since_ts: int,
    latest_runs: int,
    device_type: str | None = None,
    model_id: str | None = None,
    variable_id: str | None = None,
) -> list[tuple[str, str]]:
    clauses = ["created_at >= ?", "model_id IS NOT NULL", "run_id IS NOT NULL"]
    params: list[Any] = [since_ts]
    if device_type:
        clauses.append("device_type = ?")
        params.append(device_type)
    if model_id:
        clauses.append("model_id = ?")
        params.append(model_id)
    if variable_id:
        clauses.append("variable_id = ?")
        params.append(variable_id)

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT model_id, run_id
            FROM perf_events
            WHERE {' AND '.join(clauses)}
            ORDER BY model_id ASC, run_id DESC
            """,
            params,
        ).fetchall()

    counts_by_model: dict[str, int] = {}
    eligible_pairs: list[tuple[str, str]] = []
    for row in rows:
        eligible_model_id = str(row["model_id"])
        eligible_run_id = str(row["run_id"])
        seen_count = counts_by_model.get(eligible_model_id, 0)
        if seen_count >= latest_runs:
            continue
        counts_by_model[eligible_model_id] = seen_count + 1
        eligible_pairs.append((eligible_model_id, eligible_run_id))
    return eligible_pairs


def _metric_summary(values: Iterable[float], *, target_ms: float | None = None) -> dict[str, Any]:
    samples = [float(value) for value in values]
    if not samples:
        return {
            "count": 0,
            "avg_ms": None,
            "min_ms": None,
            "max_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "target_ms": target_ms,
        }
    avg_ms = sum(samples) / len(samples)
    return {
        "count": len(samples),
        "avg_ms": round(avg_ms, 1),
        "min_ms": round(min(samples), 1),
        "max_ms": round(max(samples), 1),
        "p50_ms": round(_compute_percentile(samples, 0.50) or 0.0, 1),
        "p95_ms": round(_compute_percentile(samples, 0.95) or 0.0, 1),
        "target_ms": target_ms,
    }


def _rum_metric_summary(
    values: Iterable[float],
    *,
    metric_unit: str,
    good_threshold: float | None = None,
    needs_improvement_threshold: float | None = None,
) -> dict[str, Any]:
    samples = [float(value) for value in values]
    if not samples:
        return {
            "count": 0,
            "unit": metric_unit,
            "avg": None,
            "min": None,
            "max": None,
            "p50": None,
            "p75": None,
            "p95": None,
            "total_value": 0.0,
            "good_threshold": good_threshold,
            "needs_improvement_threshold": needs_improvement_threshold,
        }
    avg_value = sum(samples) / len(samples)
    return {
        "count": len(samples),
        "unit": metric_unit,
        "avg": round(avg_value, 3),
        "min": round(min(samples), 3),
        "max": round(max(samples), 3),
        "p50": round(_compute_percentile(samples, 0.50) or 0.0, 3),
        "p75": round(_compute_percentile(samples, 0.75) or 0.0, 3),
        "p95": round(_compute_percentile(samples, 0.95) or 0.0, 3),
        "total_value": round(sum(samples), 3),
        "good_threshold": good_threshold,
        "needs_improvement_threshold": needs_improvement_threshold,
    }


def get_perf_summary(
    *,
    since_ts: int,
    device_type: str | None = None,
    model_id: str | None = None,
    variable_id: str | None = None,
    latest_runs: int | None = None,
) -> dict[str, Any]:
    where_sql, params = _build_perf_filters(
        since_ts=since_ts,
        device_type=device_type,
        model_id=model_id,
        variable_id=variable_id,
        latest_runs=latest_runs,
    )
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT event_name, duration_ms
            FROM perf_events
            {where_sql}
            ORDER BY created_at ASC
            """,
            params,
        ).fetchall()

    values_by_metric: dict[str, list[float]] = {name: [] for name in ALLOWED_PERF_EVENT_NAMES}
    for row in rows:
        values_by_metric[str(row["event_name"])].append(float(row["duration_ms"]))

    return {
        "metrics": {
            metric_name: _metric_summary(values, target_ms=PERF_TARGETS_MS.get(metric_name))
            for metric_name, values in sorted(values_by_metric.items())
        }
    }


def get_perf_timeseries(
    *,
    since_ts: int,
    metric: str,
    bucket: str,
    device_type: str | None = None,
    model_id: str | None = None,
    variable_id: str | None = None,
    latest_runs: int | None = None,
) -> list[dict[str, Any]]:
    if metric not in ALLOWED_PERF_EVENT_NAMES:
        raise ValueError("Unsupported performance metric")
    if bucket not in {"hour", "day"}:
        raise ValueError("Unsupported timeseries bucket")

    bucket_expr = "%Y-%m-%dT%H:00:00Z" if bucket == "hour" else "%Y-%m-%dT00:00:00Z"
    where_sql, params = _build_perf_filters(
        since_ts=since_ts,
        metric=metric,
        device_type=device_type,
        model_id=model_id,
        variable_id=variable_id,
        latest_runs=latest_runs,
    )
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT strftime('{bucket_expr}', created_at, 'unixepoch') AS bucket_start,
                   duration_ms
            FROM perf_events
            {where_sql}
            ORDER BY created_at ASC
            """,
            params,
        ).fetchall()

    buckets: dict[str, list[float]] = {}
    for row in rows:
        key = str(row["bucket_start"])
        buckets.setdefault(key, []).append(float(row["duration_ms"]))

    return [
        {
            "bucket_start": bucket_start,
            **_metric_summary(values, target_ms=PERF_TARGETS_MS.get(metric)),
        }
        for bucket_start, values in sorted(buckets.items())
    ]


def get_perf_breakdown(
    *,
    since_ts: int,
    metric: str,
    breakdown_by: str,
    limit: int = 8,
    device_type: str | None = None,
    model_id: str | None = None,
    variable_id: str | None = None,
    latest_runs: int | None = None,
) -> list[dict[str, Any]]:
    if metric not in ALLOWED_PERF_EVENT_NAMES:
        raise ValueError("Unsupported performance metric")
    column_by_breakdown = {
        "model": "model_id",
        "variable": "variable_id",
        "device": "device_type",
    }
    column = column_by_breakdown.get(breakdown_by)
    if column is None:
        raise ValueError("Unsupported breakdown")

    where_sql, params = _build_perf_filters(
        since_ts=since_ts,
        metric=metric,
        device_type=device_type,
        model_id=model_id,
        variable_id=variable_id,
        latest_runs=latest_runs,
    )
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT COALESCE({column}, 'unknown') AS bucket_key,
                   duration_ms
            FROM perf_events
            {where_sql}
            ORDER BY created_at ASC
            """,
            params,
        ).fetchall()

    values_by_bucket: dict[str, list[float]] = {}
    for row in rows:
        key = str(row["bucket_key"] or "unknown")
        values_by_bucket.setdefault(key, []).append(float(row["duration_ms"]))

    ranked = sorted(
        values_by_bucket.items(),
        key=lambda item: (len(item[1]), item[0]),
        reverse=True,
    )[: max(1, limit)]

    return [
        {
            "key": key,
            **_metric_summary(values, target_ms=PERF_TARGETS_MS.get(metric)),
        }
        for key, values in ranked
    ]


def _scan_run_issue(
    *,
    data_root: Path,
    model_id: str,
    run_id: str,
    latest_run_id: str | None,
    include_details: bool = True,
) -> dict[str, Any]:
    manifest_path = _manifest_path(data_root, model_id, run_id)
    manifest = _load_json_file(manifest_path)
    now_utc = datetime.now(timezone.utc)
    now_ts = int(now_utc.timestamp())
    run_dt = _parse_run_id_datetime(run_id)
    run_timestamp = int(run_dt.timestamp()) if run_dt is not None else None
    build_started_at = _run_build_started_at(data_root, model_id, run_id, manifest_path)
    latest_for_model = run_id == latest_run_id
    plugin = MODEL_REGISTRY.get(model_id)
    model_capability = getattr(plugin, "capabilities", None) if plugin is not None else None
    observed_model = is_observed_model_capability(model_capability)
    observed_bundle = (
        build_observed_bundle_health(
            latest_run=run_id,
            manifest=manifest,
            source=model_id,
            now_utc=now_utc,
        )
        if observed_model
        else {}
    )
    runtime_artifacts_pending = _runtime_artifacts_pending(
        manifest=manifest,
        latest_for_model=latest_for_model,
        now_utc=now_utc,
    )

    base_row = {
        "id": f"{model_id}:{run_id}",
        "model_id": model_id,
        "run_id": run_id,
        "latest_for_model": latest_for_model,
        "time_axis_mode": _time_axis_mode_for_model(model_id),
        "run_timestamp": run_timestamp,
        "build_started_at": build_started_at,
        "cycle_age_hours": round(max(0.0, (now_ts - (run_timestamp or now_ts)) / 3600.0), 1),
        "run_age_hours": round(max(0.0, (now_ts - (build_started_at or now_ts)) / 3600.0), 1),
        "expected_frames": 0,
        "available_frames": 0,
        "completion_pct": 0.0,
        "latest_forecast_hour_min": None,
        "latest_forecast_hour_max": None,
        "target_forecast_hour_min": None,
        "target_forecast_hour_max": None,
        "variable_forecast_progress": [],
        "missing_artifact_count": 0,
        "unreadable_artifact_count": 0,
        "incomplete_variable_count": 0,
        "incomplete_variables": [],
        "sample_paths": [],
        "latest_scan_valid_time": observed_bundle.get("latest_scan_valid_time"),
        "latest_scan_age_minutes": observed_bundle.get("latest_scan_age_minutes"),
        "bundle_published_at": observed_bundle.get("bundle_published_at"),
        "bundle_age_seconds": observed_bundle.get("bundle_age_seconds"),
        "freshness_state": observed_bundle.get("freshness_state"),
        "usable": observed_bundle.get("usable"),
        "degraded_reason": observed_bundle.get("degraded_reason"),
        "observation_to_publish_latency_seconds": observed_bundle.get("observation_to_publish_latency_seconds"),
        "runtime_artifacts_pending": runtime_artifacts_pending,
    }

    if manifest is None:
        return {
            **base_row,
            "status": "error",
            "issue_type": "manifest_missing",
            "summary": f"Manifest is missing or unreadable for {model_id}/{run_id}.",
            "last_updated_at": int(manifest_path.stat().st_mtime) if manifest_path.exists() else None,
        }

    variables = manifest.get("variables")
    if not isinstance(variables, dict) or not variables:
        return {
            **base_row,
            "status": "error",
            "issue_type": "manifest_invalid",
            "summary": f"Manifest is missing variable entries for {model_id}/{run_id}.",
            "last_updated_at": _parse_manifest_timestamp(manifest.get("last_updated")),
        }

    expected_frames = 0
    available_frames = 0
    incomplete_variables: list[str] = []
    missing_artifact_count = 0
    unreadable_artifact_count = 0
    sample_paths: list[dict[str, Any]] = [] if include_details else []
    incomplete_variable_count = 0
    latest_forecast_hours: list[int] = []
    target_forecast_hours: list[int] = []
    variable_forecast_progress: list[dict[str, Any]] = []

    for variable_id, entry in sorted(variables.items()):
        if not isinstance(entry, dict):
            continue
        public_variable_id = str(variable_id)
        artifact_variable_id = _artifact_variable_id(model_id, public_variable_id, entry)
        expected = int(entry.get("expected_frames") or 0)
        available = int(entry.get("available_frames") or 0)
        expected_frames += max(0, expected)
        available_frames += max(0, available)
        if expected > available:
            incomplete_variable_count += 1
            if include_details:
                incomplete_variables.append(public_variable_id)

        frame_entries = entry.get("frames")
        if not isinstance(frame_entries, list):
            continue

        frame_hours = sorted(
            int(frame.get("fh"))
            for frame in frame_entries
            if isinstance(frame, dict) and isinstance(frame.get("fh"), int)
        )
        expected_hours = _scheduled_forecast_hours_for_variable(
            plugin=plugin,
            variable_id=public_variable_id,
            run_dt=run_dt,
            expected_frames=max(0, expected),
            available_hours=frame_hours,
        )
        latest_forecast_hour = max(frame_hours) if frame_hours else None
        target_forecast_hour = max(expected_hours) if expected_hours else None
        if latest_forecast_hour is not None:
            latest_forecast_hours.append(latest_forecast_hour)
        if target_forecast_hour is not None:
            target_forecast_hours.append(target_forecast_hour)
        if include_details:
            variable_forecast_progress.append(
                {
                    "variable_id": public_variable_id,
                    "display_name": str(entry.get("display_name") or public_variable_id),
                    "latest_forecast_hour": latest_forecast_hour,
                    "target_forecast_hour": target_forecast_hour,
                    "available_frames": max(0, available),
                    "expected_frames": max(0, expected),
                }
            )
        if runtime_artifacts_pending:
            # Freshly published bundle still within the runtime-artifact grace window:
            # skip artifact validation so in-flight runtime writes aren't reported as failures.
            continue
        substrates = _variable_render_substrates(model_id, public_variable_id)
        has_vector_substrate = "vector" in substrates
        uses_grid_runtime = "grid" in substrates and grid_supported(model_id, artifact_variable_id)
        grid_manifest_payload: dict[str, Any] | None = None
        grid_frames_by_hour: dict[int, list[dict[str, Any]]] = {}
        contour_keys: list[str] = []
        grid_manifest_dir: Path | None = None

        if uses_grid_runtime and frame_hours:
            grid_manifest_file = grid_manifest_path(data_root, model_id, run_id, artifact_variable_id)
            grid_manifest_dir = grid_manifest_file.parent
            if not grid_manifest_file.is_file():
                missing_artifact_count += 1
                _append_sample_path(
                    sample_paths,
                    {
                        "variable_id": public_variable_id,
                        "forecast_hour": frame_hours[0] if frame_hours else 0,
                        "issue": "missing_grid_manifest",
                        "artifact_path": str(grid_manifest_file),
                    },
                )
            else:
                grid_manifest_payload = _load_json_file(grid_manifest_file)
                if grid_manifest_payload is None:
                    unreadable_artifact_count += 1
                    _append_sample_path(
                        sample_paths,
                        {
                            "variable_id": public_variable_id,
                            "forecast_hour": frame_hours[0] if frame_hours else 0,
                            "issue": "unreadable_grid_manifest",
                            "artifact_path": str(grid_manifest_file),
                        },
                    )
                else:
                    grid_frames_by_hour, grid_manifest_errors = _grid_runtime_frames_by_hour(grid_manifest_payload)
                    if grid_manifest_errors > 0:
                        unreadable_artifact_count += grid_manifest_errors
                        _append_sample_path(
                            sample_paths,
                            {
                                "variable_id": public_variable_id,
                                "forecast_hour": frame_hours[0] if frame_hours else 0,
                                "issue": "invalid_grid_manifest_entries",
                                "artifact_path": str(grid_manifest_file),
                            },
                        )
                    contours = grid_manifest_payload.get("contours")
                    if isinstance(contours, dict):
                        contour_keys = [str(key) for key in contours.keys() if str(key).strip()]

        validation_hours = _artifact_validation_hours(frame_hours, include_details=include_details)

        for fh in validation_hours:
            value_path = _value_cog_path(data_root, model_id, run_id, artifact_variable_id, fh)
            sidecar_path = _sidecar_path(data_root, model_id, run_id, artifact_variable_id, fh)
            sidecar_exists = sidecar_path.is_file()
            needs_sidecar_payload = has_vector_substrate or bool(contour_keys)
            sidecar_payload = _load_json_file(sidecar_path) if needs_sidecar_payload and sidecar_exists else None
            vector_paths = _vector_artifact_paths(data_root, model_id, run_id, artifact_variable_id, fh, sidecar_payload) if has_vector_substrate else []
            missing_here = False
            artifact_path: str | None = None
            if uses_grid_runtime:
                runtime_frames = grid_frames_by_hour.get(fh, [])
                if not runtime_frames:
                    missing_artifact_count += 1
                    missing_here = True
                    artifact_path = str(grid_manifest_dir or grid_manifest_path(data_root, model_id, run_id, artifact_variable_id))
                for runtime_frame in runtime_frames:
                    runtime_path = (grid_manifest_dir or grid_manifest_path(data_root, model_id, run_id, artifact_variable_id).parent) / str(runtime_frame["file"])
                    if not runtime_path.is_file():
                        missing_artifact_count += 1
                        missing_here = True
                        artifact_path = artifact_path or str(runtime_path)
                        continue
                    expected_size = expected_grid_frame_size_bytes(
                        width=int(runtime_frame["width"]),
                        height=int(runtime_frame["height"]),
                        dtype=str(grid_manifest_payload.get("grid", {}).get("dtype") or "uint16") if isinstance(grid_manifest_payload, dict) else "uint16",
                    )
                    if expected_size > 0 and runtime_path.stat().st_size != expected_size:
                        unreadable_artifact_count += 1
                        missing_here = True
                        artifact_path = artifact_path or str(runtime_path)
                        if include_details:
                            _append_sample_path(
                                sample_paths,
                                {
                                    "variable_id": public_variable_id,
                                    "forecast_hour": fh,
                                    "issue": "invalid_grid_frame",
                                    "artifact_path": str(runtime_path),
                                },
                            )
                if contour_keys:
                    if not sidecar_exists:
                        missing_artifact_count += 1
                        missing_here = True
                        artifact_path = artifact_path or str(sidecar_path)
                    elif sidecar_payload is None:
                        unreadable_artifact_count += 1
                        missing_here = True
                        artifact_path = artifact_path or str(sidecar_path)
                    else:
                        contour_paths = _contour_artifact_paths(
                            data_root=data_root,
                            model_id=model_id,
                            run_id=run_id,
                            variable_id=artifact_variable_id,
                            sidecar=sidecar_payload,
                            contour_keys=contour_keys,
                        )
                        if len(contour_paths) < len(contour_keys):
                            missing_artifact_count += max(1, len(contour_keys) - len(contour_paths))
                            missing_here = True
                            artifact_path = artifact_path or str(sidecar_path)
                        for _, contour_path in contour_paths:
                            if not contour_path.is_file():
                                missing_artifact_count += 1
                                missing_here = True
                                artifact_path = artifact_path or str(contour_path)
                                continue
                            try:
                                json.loads(contour_path.read_text())
                            except (OSError, json.JSONDecodeError):
                                unreadable_artifact_count += 1
                                missing_here = True
                                artifact_path = artifact_path or str(contour_path)
                                if include_details:
                                    _append_sample_path(
                                        sample_paths,
                                        {
                                            "variable_id": public_variable_id,
                                            "forecast_hour": fh,
                                            "issue": "unreadable_contour_artifact",
                                            "artifact_path": str(contour_path),
                                            "sidecar_path": str(sidecar_path),
                                        },
                                    )
            else:
                if "grid" in substrates and not value_path.is_file():
                    missing_artifact_count += 1
                    missing_here = True
                    artifact_path = str(value_path)
                if not sidecar_exists:
                    missing_artifact_count += 1
                    missing_here = True
                    artifact_path = artifact_path or str(sidecar_path)
            if has_vector_substrate:
                if vector_paths:
                    for vector_path in vector_paths:
                        if vector_path.is_file():
                            continue
                        missing_artifact_count += 1
                        missing_here = True
                        artifact_path = artifact_path or str(vector_path)
                elif sidecar_exists:
                    missing_artifact_count += 1
                    missing_here = True
                    artifact_path = artifact_path or str(sidecar_path)
            if missing_here and include_details:
                _append_sample_path(
                    sample_paths,
                    {
                        "variable_id": public_variable_id,
                        "forecast_hour": fh,
                        "issue": "missing_artifact",
                        "value_grid_path": str(value_path) if "grid" in substrates and not uses_grid_runtime else None,
                        "artifact_path": artifact_path,
                        "sidecar_path": str(sidecar_path) if sidecar_exists or contour_keys or has_vector_substrate or not uses_grid_runtime else None,
                    },
                )

        if include_details:
            sample_hours = frame_hours[:1]
            if len(frame_hours) > 1:
                sample_hours.append(frame_hours[-1])
            for fh in sorted(set(sample_hours)):
                value_path = _value_cog_path(data_root, model_id, run_id, artifact_variable_id, fh)
                sidecar_path = _sidecar_path(data_root, model_id, run_id, artifact_variable_id, fh)
                sidecar_payload = _load_json_file(sidecar_path) if has_vector_substrate and sidecar_path.is_file() else None
                vector_paths = _vector_artifact_paths(data_root, model_id, run_id, artifact_variable_id, fh, sidecar_payload) if has_vector_substrate else []
                if not uses_grid_runtime and "grid" in substrates and value_path.is_file():
                    try:
                        with rasterio.open(value_path):
                            pass
                    except Exception as exc:
                        unreadable_artifact_count += 1
                        _append_sample_path(
                            sample_paths,
                            {
                                "variable_id": public_variable_id,
                                "forecast_hour": fh,
                                "issue": "unreadable_value_grid",
                                "value_grid_path": str(value_path),
                                "artifact_path": str(value_path),
                                "read_error": str(exc),
                            },
                        )
                if has_vector_substrate:
                    for vector_path in vector_paths:
                        if not vector_path.is_file():
                            continue
                        try:
                            json.loads(vector_path.read_text())
                        except Exception as exc:
                            unreadable_artifact_count += 1
                            _append_sample_path(
                                sample_paths,
                                {
                                    "variable_id": public_variable_id,
                                    "forecast_hour": fh,
                                    "issue": "unreadable_vector_artifact",
                                    "artifact_path": str(vector_path),
                                    "sidecar_path": str(sidecar_path),
                                    "read_error": str(exc),
                                },
                            )
                            break

    completion_pct = round((available_frames / expected_frames) * 100.0, 1) if expected_frames > 0 else 0.0
    last_updated_at = _parse_manifest_timestamp(manifest.get("last_updated")) or int(manifest_path.stat().st_mtime)
    idle_minutes = max(0.0, (now_ts - last_updated_at) / 60.0)
    stalled_run_idle_minutes = _stalled_run_idle_minutes(model_id)
    expected_latest_dt = _expected_latest_run_time(model_id=model_id, now_utc=now_utc)
    stale_latest = bool(
        latest_for_model
        and run_dt is not None
        and expected_latest_dt is not None
        and run_dt < expected_latest_dt
    )
    stale_latest_incomplete = latest_for_model and stale_latest and available_frames < expected_frames
    stale_latest_incomplete_idle = stale_latest_incomplete and idle_minutes >= stalled_run_idle_minutes

    status = "healthy"
    issue_type = "healthy"
    summary = "Retained published run looks healthy."
    if unreadable_artifact_count > 0 or missing_artifact_count > 0:
        status = "error"
        issue_type = "artifact_failure"
        summary = f"{missing_artifact_count} missing artifacts and {unreadable_artifact_count} unreadable runtime artifacts detected."
    elif observed_model and latest_for_model and observed_bundle.get("freshness_state") == "unavailable":
        status = "error"
        issue_type = "bundle_unavailable"
        summary = "Latest observed bundle is unavailable or missing required scan metadata."
    elif observed_model and latest_for_model and observed_bundle.get("freshness_state") == "stale" and available_frames < expected_frames:
        status = "error"
        issue_type = "bundle_stalled"
        summary = f"Latest observed bundle is stale and incomplete at {available_frames}/{expected_frames} frames."
    elif observed_model and latest_for_model and observed_bundle.get("freshness_state") == "stale":
        status = "warning"
        issue_type = "stale_bundle"
        summary = "Latest observed bundle is older than the freshness threshold."
    elif observed_model and latest_for_model and observed_bundle.get("freshness_state") == "delayed":
        status = "warning"
        issue_type = "delayed_bundle"
        summary = "Latest observed bundle is delayed beyond the normal freshness window."
    elif stale_latest_incomplete_idle:
        status = "error"
        issue_type = "run_stalled"
        summary = (
            f"Latest published run is stale, incomplete at {available_frames}/{expected_frames} frames, "
            f"and idle for {int(idle_minutes)} minutes."
        )
    elif stale_latest_incomplete:
        status = "info"
        issue_type = "run_ongoing"
        summary = (
            f"Latest published run is older than the expected cycle but still updating "
            f"at {available_frames}/{expected_frames} frames."
        )
    elif latest_for_model and stale_latest:
        status = "warning"
        issue_type = "stale_run"
        summary = "Latest published run is older than the expected cycle for this model."
    elif latest_for_model and available_frames < expected_frames:
        status = "info"
        issue_type = "run_ongoing"
        summary = f"Latest published run is still building at {available_frames}/{expected_frames} frames."
    elif available_frames < expected_frames:
        status = "warning"
        issue_type = "run_incomplete"
        summary = f"Run is incomplete at {available_frames}/{expected_frames} frames."

    build_age_reference_ts = now_ts if latest_for_model and available_frames < expected_frames else last_updated_at
    run_age_hours = (
        round(max(0.0, (build_age_reference_ts - build_started_at) / 3600.0), 1)
        if build_started_at is not None
        else base_row["run_age_hours"]
    )

    return {
        **base_row,
        "run_age_hours": run_age_hours,
        "status": status,
        "issue_type": issue_type,
        "summary": summary,
        "last_updated_at": last_updated_at,
        "expected_frames": expected_frames,
        "available_frames": available_frames,
        "completion_pct": completion_pct,
        "latest_forecast_hour_min": min(latest_forecast_hours) if latest_forecast_hours else None,
        "latest_forecast_hour_max": max(latest_forecast_hours) if latest_forecast_hours else None,
        "target_forecast_hour_min": min(target_forecast_hours) if target_forecast_hours else None,
        "target_forecast_hour_max": max(target_forecast_hours) if target_forecast_hours else None,
        "variable_forecast_progress": variable_forecast_progress if include_details else [],
        "missing_artifact_count": missing_artifact_count,
        "unreadable_artifact_count": unreadable_artifact_count,
        "incomplete_variable_count": incomplete_variable_count,
        "incomplete_variables": incomplete_variables[:12] if include_details else [],
        "sample_paths": sample_paths if include_details else [],
        "runtime_artifacts_pending": runtime_artifacts_pending,
    }


def _runtime_artifacts_pending(
    *,
    manifest: dict[str, Any] | None,
    latest_for_model: bool,
    now_utc: datetime,
) -> bool:
    if not latest_for_model or not isinstance(manifest, dict):
        return False
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict) or metadata.get(MRMS_RUNTIME_ARTIFACTS_PENDING_KEY) is not True:
        return False
    bundle_published_at = parse_iso_datetime(metadata.get("bundle_published_at"))
    if bundle_published_at is None:
        bundle_published_at = _parse_manifest_timestamp(manifest.get("last_updated"))
    if bundle_published_at is None:
        return True
    age_seconds = max(0, int((now_utc - bundle_published_at).total_seconds()))
    return age_seconds < RUNTIME_ARTIFACT_PENDING_GRACE_SECONDS


def _scan_operational_status_rows(*, data_root: Path, model_id: str | None = None, include_details: bool = True) -> list[dict[str, Any]]:
    candidate_models = [model_id] if model_id else sorted(MODEL_REGISTRY.keys())
    rows: list[dict[str, Any]] = []

    for candidate_model in candidate_models:
        run_ids = _published_run_ids(data_root, candidate_model, keep_runs=STATUS_KEEP_RUNS_PER_MODEL)
        latest_run_id = run_ids[0] if run_ids else None
        for run_id in run_ids:
            row = _scan_run_issue(
                data_root=data_root,
                model_id=candidate_model,
                run_id=run_id,
                latest_run_id=latest_run_id,
                include_details=include_details,
            )
            rows.append(row)

    rows.sort(
        key=lambda item: (
            0 if item["status"] == "error" else 1 if item["status"] == "warning" else 2 if item["status"] == "info" else 3,
            -int(item.get("last_updated_at") or 0),
            item["model_id"],
            item["run_id"],
        )
    )
    return rows


def _get_operational_status_rows_cached(*, data_root: Path, model_id: str | None = None, include_details: bool = False) -> list[dict[str, Any]]:
    cache_key = (str(data_root.resolve()), model_id, "details" if include_details else "summary")
    now = time.time()
    cached_rows: list[dict[str, Any]] | None = None

    with _operational_status_cache_lock:
        entry = _operational_status_cache.get(cache_key)
        if entry is not None:
            cached_rows = [dict(row) for row in entry.get("rows", [])]
            expires_at = float(entry.get("expires_at") or 0.0)
            if cached_rows and now < expires_at:
                return cached_rows
            if cached_rows and entry.get("refreshing"):
                return cached_rows

        _operational_status_cache[cache_key] = {
            "rows": cached_rows or [],
            "expires_at": now,
            "refreshing": True,
        }

    try:
        rows = _scan_operational_status_rows(data_root=data_root, model_id=model_id, include_details=include_details)
    except Exception:
        with _operational_status_cache_lock:
            if cached_rows:
                _operational_status_cache[cache_key] = {
                    "rows": cached_rows,
                    "expires_at": time.time() + min(5.0, STATUS_RESULTS_CACHE_TTL_SECONDS),
                    "refreshing": False,
                }
            else:
                _operational_status_cache.pop(cache_key, None)
        raise

    with _operational_status_cache_lock:
        _operational_status_cache[cache_key] = {
            "rows": [dict(row) for row in rows],
            "expires_at": time.time() + STATUS_RESULTS_CACHE_TTL_SECONDS,
            "refreshing": False,
        }
    return [dict(row) for row in rows]


def get_operational_status_results(
    *,
    data_root: Path,
    since_ts: int,
    model_id: str | None = None,
    status_filter: str | None = None,
    limit: int = 200,
    include_details: bool = False,
) -> list[dict[str, Any]]:
    normalized_status_filter = (status_filter or "").strip().lower() or None
    rows = _get_operational_status_rows_cached(data_root=data_root, model_id=model_id, include_details=include_details)
    filtered_rows: list[dict[str, Any]] = []
    for row in rows:
        updated_at = int(row.get("last_updated_at") or row.get("run_timestamp") or 0)
        if updated_at < since_ts:
            continue
        if normalized_status_filter and row["status"] != normalized_status_filter:
            continue
        filtered_rows.append(row)
    return filtered_rows[: max(1, min(500, int(limit)))]


def get_operational_status_run_detail(*, data_root: Path, model_id: str, run_id: str) -> dict[str, Any]:
    run_ids = _published_run_ids(data_root, model_id, keep_runs=STATUS_KEEP_RUNS_PER_MODEL)
    latest_run_id = run_ids[0] if run_ids else None
    return _scan_run_issue(
        data_root=data_root,
        model_id=model_id,
        run_id=run_id,
        latest_run_id=latest_run_id,
        include_details=True,
    )


def get_usage_summary(*, since_ts: int) -> dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT event_name, COUNT(*) AS total
            FROM usage_events
            WHERE created_at >= ?
            GROUP BY event_name
            ORDER BY total DESC, event_name ASC
            """,
            (since_ts,),
        ).fetchall()
    return {
        "events": [
            {
                "event_name": str(row["event_name"]),
                "count": int(row["total"]),
            }
            for row in rows
        ]
    }


def get_overview_summary(*, since_ts: int) -> dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT metric_name, metric_value, created_at
            FROM rum_events
            WHERE created_at >= ?
            ORDER BY created_at ASC
            """,
            (since_ts,),
        ).fetchall()

    values_by_metric: dict[str, list[float]] = {name: [] for name in ALLOWED_RUM_METRIC_NAMES}
    last_seen_by_metric: dict[str, int | None] = {name: None for name in ALLOWED_RUM_METRIC_NAMES}
    for row in rows:
        metric_name = str(row["metric_name"])
        if metric_name not in values_by_metric:
            continue
        values_by_metric[metric_name].append(float(row["metric_value"]))
        last_seen_by_metric[metric_name] = int(row["created_at"])

    web_vitals = {
        metric_name: _rum_metric_summary(
            values_by_metric.get(metric_name, []),
            metric_unit=RUM_METRIC_UNITS[metric_name],
            good_threshold=WEB_VITAL_THRESHOLDS[metric_name]["good_threshold"],
            needs_improvement_threshold=WEB_VITAL_THRESHOLDS[metric_name]["needs_improvement_threshold"],
        )
        for metric_name in ("lcp", "inp", "cls")
    }
    web_vitals_names = ("lcp", "inp", "cls")
    rum_diagnostic_names = (
        "manifest_fetch_duration",
        "bootstrap_fetch_duration",
        "capabilities_fetch_duration",
        "regions_fetch_duration",
        "frames_fetch_duration",
        "grid_manifest_fetch_duration",
        "grid_binary_fetch_duration",
        "grid_binary_array_buffer_duration",
        "grid_texture_prepare_duration",
        "grid_texture_upload_duration",
        "grid_webgl1_expand_duration",
        "sample_request_duration",
        "sample_batch_request_duration",
        "contour_fetch_duration",
        "first_map_render_duration",
        "first_overlay_visible_duration",
        "product_switch_paint_duration",
        "tile_request_failure_count",
        "animation_stall_count",
        "frame_drop_bucket",
    )
    rum_diagnostics = {
        metric_name: _rum_metric_summary(
            values_by_metric.get(metric_name, []),
            metric_unit=RUM_METRIC_UNITS[metric_name],
        )
        for metric_name in rum_diagnostic_names
    }

    def _latest_timestamp(metric_names: tuple[str, ...]) -> int | None:
        timestamps = [last_seen_by_metric.get(metric_name) for metric_name in metric_names]
        known_timestamps = [timestamp for timestamp in timestamps if isinstance(timestamp, int)]
        return max(known_timestamps) if known_timestamps else None

    def _sample_count(metric_names: tuple[str, ...]) -> int:
        return sum(len(values_by_metric.get(metric_name, [])) for metric_name in metric_names)

    return {
        "web_vitals": web_vitals,
        "rum_diagnostics": rum_diagnostics,
        "telemetry_health": {
            "web_vitals_last_seen_at": _latest_timestamp(web_vitals_names),
            "rum_last_seen_at": _latest_timestamp(rum_diagnostic_names),
            "web_vitals_sample_count": _sample_count(web_vitals_names),
            "rum_sample_count": _sample_count(rum_diagnostic_names),
        },
    }


PRODUCT_LOAD_METRIC_NAMES = (
    "first_overlay_visible_duration",
    "product_switch_paint_duration",
)


def get_product_load_breakdown(*, since_ts: int) -> dict[str, Any]:
    """Per-model percentiles for viewer first-paint RUM metrics.

    ``first_overlay_visible_duration`` covers sessions that land on the product
    (page load → first frame painted); ``product_switch_paint_duration`` covers
    mid-session switches to the product (selection change → first frame painted).
    """
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT metric_name, metric_value, model_id, created_at
            FROM rum_events
            WHERE created_at >= ?
              AND metric_name IN ({",".join("?" for _ in PRODUCT_LOAD_METRIC_NAMES)})
            ORDER BY created_at ASC
            """,
            (since_ts, *PRODUCT_LOAD_METRIC_NAMES),
        ).fetchall()

    values_by_model: dict[str, dict[str, list[float]]] = {}
    last_seen_by_model: dict[str, int] = {}
    for row in rows:
        model_key = str(row["model_id"] or "unknown").strip() or "unknown"
        metric_name = str(row["metric_name"])
        per_metric = values_by_model.setdefault(
            model_key,
            {name: [] for name in PRODUCT_LOAD_METRIC_NAMES},
        )
        per_metric[metric_name].append(float(row["metric_value"]))
        last_seen_by_model[model_key] = int(row["created_at"])

    models = [
        {
            "model_id": model_key,
            "last_seen_at": last_seen_by_model.get(model_key),
            "metrics": {
                metric_name: _rum_metric_summary(per_metric.get(metric_name, []), metric_unit="ms")
                for metric_name in PRODUCT_LOAD_METRIC_NAMES
            },
        }
        for model_key, per_metric in values_by_model.items()
    ]
    models.sort(
        key=lambda item: sum(int(metric["count"]) for metric in item["metrics"].values()),
        reverse=True,
    )
    return {"models": models}


def get_network_diagnostics_summary(*, since_ts: int, limit_per_breakdown: int = 4) -> dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT metric_name, metric_value, created_at, model_id, device_type, meta_json
            FROM rum_events
            WHERE created_at >= ?
              AND metric_name IN ({",".join("?" for _ in NETWORK_DIAGNOSTIC_METRIC_NAMES)})
            ORDER BY created_at ASC
            """,
            (since_ts, *NETWORK_DIAGNOSTIC_METRIC_NAMES),
        ).fetchall()

    values_by_metric: dict[str, list[float]] = {name: [] for name in NETWORK_DIAGNOSTIC_METRIC_NAMES}
    last_seen_by_metric: dict[str, int | None] = {name: None for name in NETWORK_DIAGNOSTIC_METRIC_NAMES}
    cache_values_by_metric: dict[str, dict[str, list[float]]] = {name: {} for name in NETWORK_DIAGNOSTIC_METRIC_NAMES}
    model_values_by_metric: dict[str, dict[str, list[float]]] = {name: {} for name in NETWORK_DIAGNOSTIC_METRIC_NAMES}
    device_values_by_metric: dict[str, dict[str, list[float]]] = {name: {} for name in NETWORK_DIAGNOSTIC_METRIC_NAMES}
    webgl_values_by_metric: dict[str, dict[str, list[float]]] = {name: {} for name in NETWORK_DIAGNOSTIC_METRIC_NAMES}
    encoding_values_by_metric: dict[str, dict[str, list[float]]] = {name: {} for name in NETWORK_DIAGNOSTIC_METRIC_NAMES}
    payload_values_by_metric: dict[str, dict[str, list[float]]] = {name: {} for name in NETWORK_DIAGNOSTIC_METRIC_NAMES}

    def _payload_size_bucket(value: Any) -> str:
        try:
            size = int(value)
        except (TypeError, ValueError):
            return "unknown"
        if size < 0:
            return "unknown"
        if size < 256 * 1024:
            return "<256KB"
        if size < 1024 * 1024:
            return "256KB-1MB"
        if size < 4 * 1024 * 1024:
            return "1MB-4MB"
        if size < 8 * 1024 * 1024:
            return "4MB-8MB"
        if size < 16 * 1024 * 1024:
            return "8MB-16MB"
        return "16MB+"

    def _append_breakdown(
        store: dict[str, dict[str, list[float]]],
        metric_name: str,
        bucket_key: str,
        metric_value: float,
    ) -> None:
        bucket = store[metric_name].setdefault(bucket_key, [])
        bucket.append(metric_value)

    for row in rows:
        metric_name = str(row["metric_name"])
        metric_value = float(row["metric_value"])
        values_by_metric[metric_name].append(metric_value)
        last_seen_by_metric[metric_name] = int(row["created_at"])

        model_key = str(row["model_id"] or "unknown").strip() or "unknown"
        device_key = str(row["device_type"] or "unknown").strip() or "unknown"
        cache_key = "unknown"
        webgl_key = "unknown"
        encoding_key = "unknown"
        payload_key = "unknown"
        meta_json = row["meta_json"]
        if isinstance(meta_json, str) and meta_json.strip():
            try:
                meta = json.loads(meta_json)
            except Exception:
                meta = None
            if isinstance(meta, dict):
                raw_cache = str(meta.get("cf_cache_status") or "").strip().upper()
                if raw_cache:
                    cache_key = raw_cache
                raw_webgl = str(meta.get("webgl_backend") or "").strip().lower()
                if raw_webgl:
                    webgl_key = raw_webgl
                raw_encoding = str(meta.get("content_encoding") or "").strip().lower()
                if raw_encoding:
                    encoding_key = raw_encoding
                payload_value = meta.get("payload_bytes")
                if payload_value is None:
                    payload_value = meta.get("array_buffer_byte_length")
                if payload_value is None:
                    payload_value = meta.get("content_length_bytes")
                payload_key = _payload_size_bucket(payload_value)

        _append_breakdown(model_values_by_metric, metric_name, model_key, metric_value)
        _append_breakdown(device_values_by_metric, metric_name, device_key, metric_value)
        _append_breakdown(cache_values_by_metric, metric_name, cache_key, metric_value)
        _append_breakdown(webgl_values_by_metric, metric_name, webgl_key, metric_value)
        _append_breakdown(encoding_values_by_metric, metric_name, encoding_key, metric_value)
        _append_breakdown(payload_values_by_metric, metric_name, payload_key, metric_value)

    def _rank_breakdowns(
        store: dict[str, dict[str, list[float]]],
        metric_name: str,
    ) -> list[dict[str, Any]]:
        values_by_bucket = store.get(metric_name, {})
        items = sorted(
            values_by_bucket.items(),
            key=lambda item: (len(item[1]), item[0]),
            reverse=True,
        )[: max(1, int(limit_per_breakdown))]
        return [
            {
                "key": key,
                **_rum_metric_summary(values, metric_unit=RUM_METRIC_UNITS[metric_name]),
            }
            for key, values in items
        ]

    metrics = []
    for metric_name in NETWORK_DIAGNOSTIC_METRIC_NAMES:
        metrics.append(
            {
                "metric_name": metric_name,
                "label": NETWORK_DIAGNOSTIC_LABELS.get(metric_name, metric_name),
                "summary": _rum_metric_summary(values_by_metric[metric_name], metric_unit=RUM_METRIC_UNITS[metric_name]),
                "last_seen_at": last_seen_by_metric[metric_name],
                "by_cf_cache_status": _rank_breakdowns(cache_values_by_metric, metric_name),
                "by_model_id": _rank_breakdowns(model_values_by_metric, metric_name),
                "by_device_type": _rank_breakdowns(device_values_by_metric, metric_name),
                "by_webgl_backend": _rank_breakdowns(webgl_values_by_metric, metric_name),
                "by_content_encoding": _rank_breakdowns(encoding_values_by_metric, metric_name),
                "by_payload_size_bucket": _rank_breakdowns(payload_values_by_metric, metric_name),
            }
        )

    return {
        "metrics": metrics,
    }
