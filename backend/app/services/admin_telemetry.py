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
from .observed_bundle_health import build_observed_bundle_health, is_observed_model_capability
from .run_ids import RUN_ID_RE, parse_run_id_datetime

TELEMETRY_DB_PATH = Path(
    os.environ.get("CARTOSKY_TELEMETRY_DB_PATH")
    or os.environ.get("TWM_TELEMETRY_DB_PATH", "./data/admin_telemetry.sqlite3")
)
STATUS_DB_PATH = Path(
    os.environ.get("CARTOSKY_STATUS_DB_PATH")
    or os.environ.get("TWM_STATUS_DB_PATH", str(TELEMETRY_DB_PATH))
)

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
    "vector_fetch_duration",
    "first_map_render_duration",
    "first_overlay_visible_duration",
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
    "vector_fetch_duration": "ms",
    "first_map_render_duration": "ms",
    "first_overlay_visible_duration": "ms",
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
    "vector_fetch_duration",
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
    "vector_fetch_duration": "Vector",
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

_db_init_lock = threading.Lock()
_db_initialized = False
_status_db_init_lock = threading.Lock()
_status_db_initialized = False


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


def _connect_status() -> sqlite3.Connection:
    _ensure_parent_dir(STATUS_DB_PATH)
    conn = sqlite3.connect(STATUS_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    _init_status_db(conn)
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

            """
        )
        _db_initialized = True


def _init_status_db(conn: sqlite3.Connection) -> None:
    global _status_db_initialized
    if _status_db_initialized:
        return
    with _status_db_init_lock:
        if _status_db_initialized:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS qa_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                model_id TEXT NOT NULL,
                variable_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                forecast_hour INTEGER NOT NULL,
                auto_status TEXT NOT NULL,
                manual_status TEXT,
                auto_checks_json TEXT,
                coverage_fraction REAL,
                valid_pixel_count INTEGER,
                total_pixel_count INTEGER,
                range_min REAL,
                range_max REAL,
                warning_summary TEXT,
                severity TEXT,
                diagnostics_json TEXT,
                last_checked_at INTEGER,
                UNIQUE(model_id, variable_id, run_id, forecast_hour)
            );

            CREATE INDEX IF NOT EXISTS idx_qa_reviews_updated
                ON qa_reviews(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_qa_reviews_model_run
                ON qa_reviews(model_id, run_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_qa_reviews_status_updated
                ON qa_reviews(auto_status, updated_at DESC);
            """
        )
        _status_db_initialized = True


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
    cadence = int(run_discovery.get("cycle_cadence_hours") or 0)
    fallback_lag = int(run_discovery.get("fallback_lag_hours") or 0)
    if cadence <= 0:
        return None
    reference = now_utc - timedelta(hours=max(0, fallback_lag))
    floored_hour = reference.hour - (reference.hour % cadence)
    return reference.replace(hour=floored_hour, minute=0, second=0, microsecond=0)


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


def _manifest_path(data_root: Path, model_id: str, run_id: str) -> Path:
    return data_root / "manifests" / model_id / f"{run_id}.json"


def _reviewable_run_ids_from_disk(data_root: Path, model_id: str, *, keep_runs: int) -> list[str]:
    published_model_root = data_root / "published" / model_id
    manifests_model_root = data_root / "manifests" / model_id
    if not published_model_root.is_dir():
        return []

    published_runs = sorted(
        (path.name for path in published_model_root.iterdir() if path.is_dir() and RUN_ID_RE.match(path.name)),
        key=lambda run_id: (
            _parse_run_id_datetime(run_id).timestamp() if _parse_run_id_datetime(run_id) is not None else float("-inf"),
            run_id,
        ),
        reverse=True,
    )

    reviewable: list[str] = []
    for run_id in published_runs:
        if manifests_model_root.joinpath(f"{run_id}.json").is_file():
            reviewable.append(run_id)
        if len(reviewable) >= max(1, int(keep_runs)):
            break
    return reviewable


def _format_small_percent(value: float) -> str:
    if value <= 0:
        return "0.0%"
    if value < 0.1:
        return "<0.1%"
    return f"{value:.1f}%"


def _format_small_value(value: float) -> str:
    if value <= 0:
        return "0.0"
    if value < 0.1:
        return "<0.1"
    return f"{value:.1f}"


def _finite_grid_stats(path: Path) -> tuple[int, int, float | None, float | None]:
    with rasterio.open(path) as dataset:
        data = dataset.read(1, masked=False)
    finite_mask = np.isfinite(data)
    valid_count = int(finite_mask.sum())
    total_count = int(data.size)
    if valid_count <= 0:
        return valid_count, total_count, None, None
    finite_values = data[finite_mask]
    return valid_count, total_count, float(np.min(finite_values)), float(np.max(finite_values))


def _pixel_to_lon_lat(path: Path, row: int, col: int) -> tuple[float | None, float | None]:
    with rasterio.open(path) as dataset:
        x, y = dataset.xy(row, col)
        src_crs = dataset.crs
    if src_crs is None:
        return None, None
    transformer = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(x, y)
    return float(lon), float(lat)


def _monotonic_diagnostics(current_path: Path, previous_path: Path, *, tolerance: float = 0.01) -> dict[str, Any] | None:
    if not current_path.exists() or not previous_path.exists():
        return None
    with rasterio.open(current_path) as current_ds:
        current = current_ds.read(1, masked=False)
    with rasterio.open(previous_path) as previous_ds:
        previous = previous_ds.read(1, masked=False)
    if current.shape != previous.shape:
        return {
            "ok": False,
            "reason": "shape_mismatch",
        }
    valid_mask = np.isfinite(current) & np.isfinite(previous)
    if not valid_mask.any():
        return None
    deltas = current[valid_mask] - previous[valid_mask]
    threshold = -abs(float(tolerance))
    decreased_mask = valid_mask & ((current - previous) < threshold)
    decreased_count = int(decreased_mask.sum())
    valid_count = int(valid_mask.sum())
    min_delta = float(np.min(deltas))
    max_increase = float(np.max(deltas))
    if decreased_count <= 0:
        return {
            "ok": True,
            "decreased_pixel_count": 0,
            "decreased_fraction": 0.0,
            "max_decrease": 0.0,
            "max_increase": round(max_increase, 3),
        }

    delta_grid = current - previous
    decrease_values = np.where(decreased_mask, delta_grid, np.inf)
    row, col = np.unravel_index(int(np.argmin(decrease_values)), decrease_values.shape)
    lon, lat = _pixel_to_lon_lat(current_path, int(row), int(col))
    return {
        "ok": False,
        "decreased_pixel_count": decreased_count,
        "decreased_fraction": round(decreased_count / max(1, valid_count), 6),
        "max_decrease": round(abs(min_delta), 3),
        "max_increase": round(max_increase, 3),
        "max_decrease_lon": round(lon, 3) if lon is not None else None,
        "max_decrease_lat": round(lat, 3) if lat is not None else None,
    }


def _severity_from_diagnostics(*, checks: dict[str, Any], diagnostics: dict[str, Any]) -> str:
    if checks.get("has_valid_pixels") is not True or checks.get("coverage_present") is not True:
        return "high"
    monotonic = diagnostics.get("monotonic")
    if isinstance(monotonic, dict) and monotonic.get("ok") is False:
        fraction = float(monotonic.get("decreased_fraction") or 0.0)
        max_decrease = float(monotonic.get("max_decrease") or 0.0)
        if fraction >= 0.05 or max_decrease >= 2.0:
            return "high"
        if fraction >= 0.01 or max_decrease >= 1.0:
            return "medium"
        return "low"
    return "none"


def _warning_summary(*, variable_id: str, checks: dict[str, Any], diagnostics: dict[str, Any]) -> str | None:
    if checks.get("has_valid_pixels") is not True:
        return "No valid pixels found in published value grid."
    if checks.get("coverage_present") is not True:
        return "Coverage fell below the minimum expected threshold."
    if checks.get("range_present") is not True:
        return "Value range metadata is missing or invalid."
    monotonic = diagnostics.get("monotonic")
    if isinstance(monotonic, dict) and monotonic.get("ok") is False:
        if monotonic.get("reason") == "shape_mismatch":
            return "Current and previous forecast hours are on different grid shapes."
        decreased_fraction = float(monotonic.get("decreased_fraction") or 0.0) * 100.0
        max_decrease = float(monotonic.get("max_decrease") or 0.0)
        lat = monotonic.get("max_decrease_lat")
        lon = monotonic.get("max_decrease_lon")
        location = f" near {lat}, {lon}" if lat is not None and lon is not None else ""
        return (
            f"Cumulative {variable_id} decreased versus the previous hour at "
            f"{_format_small_percent(decreased_fraction)} of valid pixels; max drop {_format_small_value(max_decrease)}{location}."
        )
    return None


def _build_auto_checks(
    *,
    data_root: Path,
    model_id: str,
    variable_id: str,
    run_id: str,
    forecast_hour: int,
    previous_forecast_hour: int | None = None,
) -> dict[str, Any]:
    value_path = _value_cog_path(data_root, model_id, run_id, variable_id, forecast_hour)
    sidecar = _load_json_file(_sidecar_path(data_root, model_id, run_id, variable_id, forecast_hour))

    checks: dict[str, Any] = {
        "has_valid_pixels": False,
        "range_present": False,
        "coverage_present": False,
        "monotonic": None,
    }
    metrics: dict[str, Any] = {
        "coverage_fraction": None,
        "valid_pixel_count": 0,
        "total_pixel_count": 0,
        "range_min": None,
        "range_max": None,
    }
    diagnostics: dict[str, Any] = {}
    status = "warning"

    if not value_path.exists():
        diagnostics = {
            "artifact": {
                "issue_type": "missing_value_grid",
                "value_grid_exists": False,
                "value_grid_path": str(value_path),
                "sidecar_exists": _sidecar_path(data_root, model_id, run_id, variable_id, forecast_hour).exists(),
                "sidecar_path": str(_sidecar_path(data_root, model_id, run_id, variable_id, forecast_hour)),
            }
        }
        return {
            "status": status,
            "checks": checks,
            "metrics": metrics,
            "diagnostics": diagnostics,
            "severity": "high",
            "warning_summary": f"Published value grid is missing for {model_id}/{run_id}/{variable_id}/fh{forecast_hour:03d}.",
        }

    try:
        valid_count, total_count, range_min, range_max = _finite_grid_stats(value_path)
    except Exception as exc:
        diagnostics = {
            "artifact": {
                "issue_type": "unreadable_value_grid",
                "value_grid_exists": True,
                "value_grid_path": str(value_path),
                "sidecar_exists": _sidecar_path(data_root, model_id, run_id, variable_id, forecast_hour).exists(),
                "sidecar_path": str(_sidecar_path(data_root, model_id, run_id, variable_id, forecast_hour)),
                "read_error": str(exc),
            }
        }
        return {
            "status": status,
            "checks": checks,
            "metrics": metrics,
            "diagnostics": diagnostics,
            "severity": "high",
            "warning_summary": f"Published value grid could not be read for {model_id}/{run_id}/{variable_id}/fh{forecast_hour:03d}.",
        }

    coverage_fraction = (valid_count / total_count) if total_count > 0 else 0.0
    checks["has_valid_pixels"] = valid_count > 0
    checks["coverage_present"] = coverage_fraction >= 0.01

    sidecar_min = sidecar.get("min") if isinstance(sidecar, dict) else None
    sidecar_max = sidecar.get("max") if isinstance(sidecar, dict) else None
    checks["range_present"] = (
        isinstance(sidecar_min, (int, float))
        and isinstance(sidecar_max, (int, float))
        and np.isfinite(sidecar_min)
        and np.isfinite(sidecar_max)
        and float(sidecar_max) >= float(sidecar_min)
    ) or (
        range_min is not None and range_max is not None and float(range_max) >= float(range_min)
    )

    if variable_id in STATUS_CUMULATIVE_VARIABLE_IDS:
        previous_path = (
            _value_cog_path(data_root, model_id, run_id, variable_id, previous_forecast_hour)
            if previous_forecast_hour is not None
            else None
        )
        monotonic = _monotonic_diagnostics(value_path, previous_path) if previous_path is not None else None
        diagnostics["monotonic"] = monotonic
        checks["monotonic"] = monotonic.get("ok") if isinstance(monotonic, dict) else None

    status = "pass"
    for check_name, value in checks.items():
        if check_name == "monotonic" and value is None:
            continue
        if value is not True:
            status = "warning"
            break

    metrics = {
        "coverage_fraction": round(float(coverage_fraction), 6),
        "valid_pixel_count": valid_count,
        "total_pixel_count": total_count,
        "range_min": round(float(range_min), 3) if range_min is not None else None,
        "range_max": round(float(range_max), 3) if range_max is not None else None,
    }
    return {
        "status": status,
        "checks": checks,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "severity": _severity_from_diagnostics(checks=checks, diagnostics=diagnostics),
        "warning_summary": _warning_summary(variable_id=variable_id, checks=checks, diagnostics=diagnostics),
    }


def sync_status_run(*, data_root: Path, model_id: str, run_id: str) -> int:
    manifest = _load_json_file(_manifest_path(data_root, model_id, run_id))
    if not isinstance(manifest, dict):
        return 0

    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return 0

    now = int(time.time())
    synced = 0
    with _connect_status() as conn:
        for variable_id, variable_meta in variables.items():
            if str(variable_id) not in STATUS_VARIABLE_IDS:
                continue
            if not isinstance(variable_meta, dict):
                continue
            frames = variable_meta.get("frames")
            if not isinstance(frames, list):
                continue
            available_hours = sorted(
                int(frame.get("fh"))
                for frame in frames
                if isinstance(frame, dict) and isinstance(frame.get("fh"), int)
            )
            for forecast_hour in available_hours:
                previous_forecast_hour = None
                if str(variable_id) in STATUS_CUMULATIVE_VARIABLE_IDS:
                    previous_values = [fh for fh in available_hours if fh < forecast_hour]
                    if previous_values:
                        previous_forecast_hour = previous_values[-1]

                auto_result = _build_auto_checks(
                    data_root=data_root,
                    model_id=model_id,
                    variable_id=str(variable_id),
                    run_id=run_id,
                    forecast_hour=forecast_hour,
                    previous_forecast_hour=previous_forecast_hour,
                )
                conn.execute(
                    """
                    INSERT INTO qa_reviews (
                        created_at,
                        updated_at,
                        model_id,
                        variable_id,
                        run_id,
                        forecast_hour,
                        auto_status,
                        manual_status,
                        auto_checks_json,
                        coverage_fraction,
                        valid_pixel_count,
                        total_pixel_count,
                        range_min,
                        range_max,
                        warning_summary,
                        severity,
                        diagnostics_json,
                        last_checked_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(model_id, variable_id, run_id, forecast_hour)
                    DO UPDATE SET
                        updated_at=excluded.updated_at,
                        auto_status=excluded.auto_status,
                        auto_checks_json=excluded.auto_checks_json,
                        coverage_fraction=excluded.coverage_fraction,
                        valid_pixel_count=excluded.valid_pixel_count,
                        total_pixel_count=excluded.total_pixel_count,
                        range_min=excluded.range_min,
                        range_max=excluded.range_max,
                        warning_summary=excluded.warning_summary,
                        severity=excluded.severity,
                        diagnostics_json=excluded.diagnostics_json,
                        last_checked_at=excluded.last_checked_at
                    """,
                    (
                        now,
                        now,
                        str(model_id),
                        str(variable_id),
                        str(run_id),
                        int(forecast_hour),
                        str(auto_result["status"]),
                        "review",
                        _serialize_meta(auto_result["checks"]),
                        auto_result["metrics"]["coverage_fraction"],
                        auto_result["metrics"]["valid_pixel_count"],
                        auto_result["metrics"]["total_pixel_count"],
                        auto_result["metrics"]["range_min"],
                        auto_result["metrics"]["range_max"],
                        _normalize_text(auto_result["warning_summary"], max_length=240),
                        _normalize_text(auto_result["severity"], max_length=24),
                        _serialize_meta(auto_result["diagnostics"]),
                        now,
                    ),
                )
                synced += 1
    return synced


def sync_recent_status_runs(*, data_root: Path, limit_runs_per_model: int = 2) -> int:
    manifests_root = data_root / "manifests"
    if not manifests_root.is_dir():
        return 0

    synced = 0
    for model_dir in sorted(path for path in manifests_root.iterdir() if path.is_dir()):
        run_ids = sorted(
            [path.stem for path in model_dir.glob("*.json") if path.is_file()],
            reverse=True,
        )[: max(1, int(limit_runs_per_model))]
        for run_id in run_ids:
            synced += sync_status_run(data_root=data_root, model_id=model_dir.name, run_id=run_id)
    return synced


def prune_status_rows(*, data_root: Path, keep_runs_per_model: int = STATUS_KEEP_RUNS_PER_MODEL) -> int:
    published_root = data_root / "published"
    if not published_root.is_dir():
        return 0

    allowed_by_model: dict[str, set[str]] = {}
    for model_dir in sorted(path for path in published_root.iterdir() if path.is_dir()):
        allowed_by_model[model_dir.name] = set(
            _reviewable_run_ids_from_disk(data_root, model_dir.name, keep_runs=keep_runs_per_model)
        )

    deleted = 0
    with _connect_status() as conn:
        rows = conn.execute("SELECT DISTINCT model_id, run_id FROM qa_reviews").fetchall()
        for row in rows:
            model_id = str(row["model_id"])
            run_id = str(row["run_id"])
            allowed_runs = allowed_by_model.get(model_id, set())
            if run_id in allowed_runs:
                continue
            before = conn.total_changes
            conn.execute(
                "DELETE FROM qa_reviews WHERE model_id = ? AND run_id = ?",
                (model_id, run_id),
            )
            deleted += conn.total_changes - before
    return deleted


def sync_latest_missing_status_runs(*, data_root: Path, limit_runs_per_model: int = 2) -> int:
    published_root = data_root / "published"
    if not published_root.is_dir():
        return 0

    synced = 0
    with _connect_status() as conn:
        for model_dir in sorted(path for path in published_root.iterdir() if path.is_dir()):
            run_ids = _reviewable_run_ids_from_disk(data_root, model_dir.name, keep_runs=limit_runs_per_model)
            for run_id in run_ids:
                row = conn.execute(
                    """
                    SELECT 1
                    FROM qa_reviews
                    WHERE model_id = ? AND run_id = ?
                    LIMIT 1
                    """,
                    (model_dir.name, run_id),
                ).fetchone()
                if row is not None:
                    continue
                synced += sync_status_run(data_root=data_root, model_id=model_dir.name, run_id=run_id)
    return synced


def status_rows_count() -> int:
    with _connect_status() as conn:
        row = conn.execute("SELECT COUNT(*) AS total FROM qa_reviews").fetchone()
    return int(row["total"] or 0)


def ensure_status_seeded(*, data_root: Path, limit_runs_per_model: int = 2) -> int:
    if status_rows_count() > 0:
        return 0
    return sync_recent_status_runs(data_root=data_root, limit_runs_per_model=limit_runs_per_model)


def refresh_missing_status_diagnostics(*, data_root: Path, limit_runs: int = 50) -> int:
    with _connect_status() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT model_id, run_id
            FROM qa_reviews
            WHERE auto_status = 'warning'
              AND (
                warning_summary IS NULL OR warning_summary = ''
                OR severity IS NULL OR severity = ''
                OR diagnostics_json IS NULL OR diagnostics_json = '' OR diagnostics_json = '{}'
              )
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit_runs)),),
        ).fetchall()

    refreshed = 0
    for row in rows:
        refreshed += sync_status_run(
            data_root=data_root,
            model_id=str(row["model_id"]),
            run_id=str(row["run_id"]),
        )
    return refreshed


def ensure_status_ready(*, data_root: Path, seed_limit_runs_per_model: int = 2, refresh_limit_runs: int = 50) -> int:
    pruned = prune_status_rows(data_root=data_root, keep_runs_per_model=STATUS_KEEP_RUNS_PER_MODEL)
    seeded = ensure_status_seeded(data_root=data_root, limit_runs_per_model=seed_limit_runs_per_model)
    latest = sync_latest_missing_status_runs(
        data_root=data_root,
        limit_runs_per_model=max(STATUS_KEEP_RUNS_PER_MODEL, seed_limit_runs_per_model),
    )
    refreshed = refresh_missing_status_diagnostics(data_root=data_root, limit_runs=refresh_limit_runs)
    return pruned + seeded + latest + refreshed


def get_status_results(
    *,
    since_ts: int,
    model_id: str | None = None,
    variable_id: str | None = None,
    flagged_only: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses = ["updated_at >= ?"]
    params: list[Any] = [since_ts]
    if model_id:
        clauses.append("model_id = ?")
        params.append(model_id)
    if variable_id:
        clauses.append("variable_id = ?")
        params.append(variable_id)
    if flagged_only:
        clauses.append("auto_status = 'warning'")

    params.append(max(1, min(500, int(limit))))
    where_sql = " WHERE " + " AND ".join(clauses)

    with _connect_status() as conn:
        rows = conn.execute(
            f"""
            SELECT
                id,
                created_at,
                updated_at,
                model_id,
                variable_id,
                run_id,
                forecast_hour,
                auto_status,
                auto_checks_json,
                coverage_fraction,
                valid_pixel_count,
                total_pixel_count,
                range_min,
                range_max,
                warning_summary,
                severity,
                diagnostics_json,
                last_checked_at
            FROM qa_reviews
            {where_sql}
            ORDER BY updated_at DESC, model_id ASC, run_id DESC, variable_id ASC, forecast_hour ASC
            LIMIT ?
            """,
            params,
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        auto_checks = {}
        diagnostics = {}
        if row["auto_checks_json"]:
            try:
                parsed = json.loads(str(row["auto_checks_json"]))
                if isinstance(parsed, dict):
                    auto_checks = parsed
            except json.JSONDecodeError:
                auto_checks = {}
        if row["diagnostics_json"]:
            try:
                parsed = json.loads(str(row["diagnostics_json"]))
                if isinstance(parsed, dict):
                    diagnostics = parsed
            except json.JSONDecodeError:
                diagnostics = {}
        results.append(
            {
                "id": int(row["id"]),
                "created_at": int(row["created_at"]),
                "updated_at": int(row["updated_at"]),
                "model_id": str(row["model_id"]),
                "variable_id": str(row["variable_id"]),
                "run_id": str(row["run_id"]),
                "forecast_hour": int(row["forecast_hour"]),
                "auto_status": str(row["auto_status"]),
                "auto_checks": auto_checks,
                "diagnostics": diagnostics,
                "coverage_fraction": float(row["coverage_fraction"]) if row["coverage_fraction"] is not None else None,
                "valid_pixel_count": int(row["valid_pixel_count"] or 0),
                "total_pixel_count": int(row["total_pixel_count"] or 0),
                "range_min": float(row["range_min"]) if row["range_min"] is not None else None,
                "range_max": float(row["range_max"]) if row["range_max"] is not None else None,
                "warning_summary": _normalize_text(row["warning_summary"], max_length=240),
                "severity": _normalize_text(row["severity"], max_length=24) or "none",
                "last_checked_at": int(row["last_checked_at"]),
            }
        )
    return results


def get_status_qa_summary() -> dict[str, Any]:
    with _connect_status() as conn:
        total_row = conn.execute("SELECT COUNT(*) AS total FROM qa_reviews").fetchone()
        warning_row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM qa_reviews
            WHERE auto_status = 'warning'
            """
        ).fetchone()
        run_row = conn.execute(
            """
            SELECT COUNT(DISTINCT model_id || '|' || run_id) AS total
            FROM qa_reviews
            """
        ).fetchone()
        latest_row = conn.execute(
            """
            SELECT MAX(last_checked_at) AS latest_checked_at
            FROM qa_reviews
            """
        ).fetchone()

    telemetry_path = str(TELEMETRY_DB_PATH.resolve())
    status_path = str(STATUS_DB_PATH.resolve())
    return {
        "store_mode": "shared" if status_path == telemetry_path else "separate",
        "db_path": status_path,
        "total_reviews": int(total_row["total"] or 0),
        "warning_reviews": int(warning_row["total"] or 0),
        "distinct_runs": int(run_row["total"] or 0),
        "latest_checked_at": int(latest_row["latest_checked_at"]) if latest_row["latest_checked_at"] else None,
    }


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
                _normalize_text(payload.get("page"), max_length=120),
                _serialize_meta(payload.get("meta")),
            ),
        )


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
                _normalize_text(payload.get("page"), max_length=120),
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
                _normalize_text(payload.get("page"), max_length=120),
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
) -> dict[str, Any]:
    manifest_path = _manifest_path(data_root, model_id, run_id)
    manifest = _load_json_file(manifest_path)
    now_utc = datetime.now(timezone.utc)
    run_dt = _parse_run_id_datetime(run_id)
    run_timestamp = int(run_dt.timestamp()) if run_dt is not None else None
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

    base_row = {
        "id": f"{model_id}:{run_id}",
        "model_id": model_id,
        "run_id": run_id,
        "latest_for_model": latest_for_model,
        "time_axis_mode": _time_axis_mode_for_model(model_id),
        "run_timestamp": run_timestamp,
        "run_age_hours": round(max(0.0, (now_utc.timestamp() - (run_timestamp or now_utc.timestamp())) / 3600.0), 1),
        "expected_frames": 0,
        "available_frames": 0,
        "completion_pct": 0.0,
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
    sample_paths: list[dict[str, Any]] = []

    for variable_id, entry in sorted(variables.items()):
        if not isinstance(entry, dict):
            continue
        expected = int(entry.get("expected_frames") or 0)
        available = int(entry.get("available_frames") or 0)
        expected_frames += max(0, expected)
        available_frames += max(0, available)
        if expected > available:
            incomplete_variables.append(str(variable_id))

        frame_entries = entry.get("frames")
        if not isinstance(frame_entries, list):
            continue

        frame_hours = sorted(
            int(frame.get("fh"))
            for frame in frame_entries
            if isinstance(frame, dict) and isinstance(frame.get("fh"), int)
        )
        for fh in frame_hours:
            substrates = _variable_render_substrates(model_id, str(variable_id))
            value_path = _value_cog_path(data_root, model_id, run_id, str(variable_id), fh)
            sidecar_path = _sidecar_path(data_root, model_id, run_id, str(variable_id), fh)
            sidecar_payload = _load_json_file(sidecar_path) if sidecar_path.is_file() else None
            vector_paths = _vector_artifact_paths(data_root, model_id, run_id, str(variable_id), fh, sidecar_payload)
            missing_here = False
            artifact_path: str | None = None
            if "grid" in substrates and not value_path.is_file():
                missing_artifact_count += 1
                missing_here = True
                artifact_path = str(value_path)
            if not sidecar_path.is_file():
                missing_artifact_count += 1
                missing_here = True
                artifact_path = artifact_path or str(sidecar_path)
            if "vector" in substrates:
                if vector_paths:
                    for vector_path in vector_paths:
                        if vector_path.is_file():
                            continue
                        missing_artifact_count += 1
                        missing_here = True
                        artifact_path = artifact_path or str(vector_path)
                elif sidecar_path.is_file():
                    missing_artifact_count += 1
                    missing_here = True
                    artifact_path = artifact_path or str(sidecar_path)
            if missing_here and len(sample_paths) < 6:
                sample_paths.append(
                    {
                        "variable_id": str(variable_id),
                        "forecast_hour": fh,
                        "issue": "missing_artifact",
                        "value_grid_path": str(value_path) if "grid" in substrates else None,
                        "artifact_path": artifact_path,
                        "sidecar_path": str(sidecar_path),
                    }
                )

        sample_hours = frame_hours[:1]
        if len(frame_hours) > 1:
            sample_hours.append(frame_hours[-1])
        for fh in sorted(set(sample_hours)):
            substrates = _variable_render_substrates(model_id, str(variable_id))
            value_path = _value_cog_path(data_root, model_id, run_id, str(variable_id), fh)
            sidecar_payload = _load_json_file(_sidecar_path(data_root, model_id, run_id, str(variable_id), fh))
            vector_paths = _vector_artifact_paths(data_root, model_id, run_id, str(variable_id), fh, sidecar_payload)
            if "grid" in substrates and value_path.is_file():
                try:
                    with rasterio.open(value_path):
                        pass
                except Exception as exc:
                    unreadable_artifact_count += 1
                    if len(sample_paths) < 6:
                        sample_paths.append(
                            {
                                "variable_id": str(variable_id),
                                "forecast_hour": fh,
                                "issue": "unreadable_value_grid",
                                "value_grid_path": str(value_path),
                                "artifact_path": str(value_path),
                                "read_error": str(exc),
                            }
                        )
            if "vector" in substrates:
                for vector_path in vector_paths:
                    if not vector_path.is_file():
                        continue
                    try:
                        json.loads(vector_path.read_text())
                    except Exception as exc:
                        unreadable_artifact_count += 1
                        if len(sample_paths) < 6:
                            sample_paths.append(
                                {
                                    "variable_id": str(variable_id),
                                    "forecast_hour": fh,
                                    "issue": "unreadable_vector_artifact",
                                    "artifact_path": str(vector_path),
                                    "sidecar_path": str(_sidecar_path(data_root, model_id, run_id, str(variable_id), fh)),
                                    "read_error": str(exc),
                                }
                            )
                        break

    completion_pct = round((available_frames / expected_frames) * 100.0, 1) if expected_frames > 0 else 0.0
    expected_latest_dt = _expected_latest_run_time(model_id=model_id, now_utc=now_utc)
    stale_latest = bool(
        latest_for_model
        and run_dt is not None
        and expected_latest_dt is not None
        and run_dt < expected_latest_dt
    )

    status = "healthy"
    issue_type = "healthy"
    summary = "Retained published run looks healthy."
    if unreadable_artifact_count > 0 or missing_artifact_count > 0:
        status = "error"
        issue_type = "artifact_failure"
        summary = f"{missing_artifact_count} missing artifacts and {unreadable_artifact_count} unreadable value grids detected."
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
    elif latest_for_model and stale_latest and available_frames < expected_frames:
        status = "error"
        issue_type = "run_stalled"
        summary = f"Latest published run is stale and incomplete at {available_frames}/{expected_frames} frames."
    elif latest_for_model and stale_latest:
        status = "warning"
        issue_type = "stale_run"
        summary = "Latest published run is older than the expected cycle for this model."
    elif available_frames < expected_frames:
        status = "warning"
        issue_type = "run_incomplete"
        summary = f"Run is incomplete at {available_frames}/{expected_frames} frames."

    return {
        **base_row,
        "run_age_hours": (
            round(float(observed_bundle["latest_scan_age_minutes"]) / 60.0, 1)
            if observed_model and isinstance(observed_bundle.get("latest_scan_age_minutes"), (int, float))
            else base_row["run_age_hours"]
        ),
        "status": status,
        "issue_type": issue_type,
        "summary": summary,
        "last_updated_at": _parse_manifest_timestamp(manifest.get("last_updated")) or int(manifest_path.stat().st_mtime),
        "expected_frames": expected_frames,
        "available_frames": available_frames,
        "completion_pct": completion_pct,
        "missing_artifact_count": missing_artifact_count,
        "unreadable_artifact_count": unreadable_artifact_count,
        "incomplete_variable_count": len(incomplete_variables),
        "incomplete_variables": incomplete_variables[:12],
        "sample_paths": sample_paths,
    }


def get_operational_status_results(
    *,
    data_root: Path,
    since_ts: int,
    model_id: str | None = None,
    status_filter: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    candidate_models = [model_id] if model_id else sorted(MODEL_REGISTRY.keys())
    normalized_status_filter = (status_filter or "").strip().lower() or None
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
            )
            updated_at = int(row.get("last_updated_at") or row.get("run_timestamp") or 0)
            if updated_at < since_ts:
                continue
            if normalized_status_filter and row["status"] != normalized_status_filter:
                continue
            rows.append(row)

    rows.sort(
        key=lambda item: (
            0 if item["status"] == "error" else 1 if item["status"] == "warning" else 2,
            -int(item.get("last_updated_at") or 0),
            item["model_id"],
            item["run_id"],
        )
    )
    return rows[: max(1, min(500, int(limit)))]


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
        "vector_fetch_duration",
        "first_map_render_duration",
        "first_overlay_visible_duration",
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
        encoding_key = "identity"
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
