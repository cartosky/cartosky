from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import rasterio
from PIL import Image, ImageFilter
from rasterio.enums import Resampling

from app.models.registry import MODEL_REGISTRY
from app.config import grid_build_enabled
from app.services import climatology
from app.services.builder.colorize import float_to_rgba
from app.services.builder.fetch import HerbieTransientUnavailableError, fetch_variable
from app.services.builder.derive import FetchContext
from app.services.builder.pipeline import build_frame, build_frame_bundle
from app.services.grid import build_grid_manifests_for_run_root
from app.services.render_resampling import (
    compute_loop_output_shape,
    high_quality_loop_resampling,
    log_fixed_loop_size_once,
    loop_fixed_width_for_tier,
    loop_max_dim_for_tier,
    loop_quality_for_tier,
    rasterio_resampling_for_loop,
    use_value_render_for_variable,
    variable_kind,
    variable_color_map_id,
)
from app.services.run_ids import RUN_ID_RE, format_run_id, parse_run_id_datetime

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_PRIMARY_VAR = "tmp2m"
DEFAULT_VARS = "auto"
DEFAULT_POLL_SECONDS = 300
DEFAULT_KEEP_RUNS = 4
INCOMPLETE_RUN_POLL_SECONDS = 60
DEFAULT_PROMOTION_FHS = (0, 1, 2)
DEFAULT_PROBE_VAR = "tmp2m"
CANONICAL_COVERAGE = "conus"
AUTO_VARS_TOKENS = {"auto", "default", "all", "buildable", "*"}
MODEL_CATEGORY_KEEP_RUNS = 6
ENSEMBLE_CATEGORY_KEEP_RUNS = 6
ENV_DEFAULT_VARS = ("CARTOSKY_SCHEDULER_VARS", "CARTOSKY_V3_SCHEDULER_VARS", "TWF_V3_SCHEDULER_VARS")
ENV_DEFAULT_PRIMARY_VARS = (
    "CARTOSKY_SCHEDULER_PRIMARY_VARS",
    "CARTOSKY_V3_SCHEDULER_PRIMARY_VARS",
    "TWF_V3_SCHEDULER_PRIMARY_VARS",
)
ENV_DEFAULT_POLL_SECONDS = (
    "CARTOSKY_SCHEDULER_POLL_SECONDS",
    "CARTOSKY_V3_SCHEDULER_POLL_SECONDS",
    "TWF_V3_SCHEDULER_POLL_SECONDS",
)
ENV_DEFAULT_KEEP_RUNS = ("CARTOSKY_SCHEDULER_KEEP_RUNS", "CARTOSKY_V3_SCHEDULER_KEEP_RUNS", "TWF_V3_SCHEDULER_KEEP_RUNS")
ENV_PROBE_VAR = ("CARTOSKY_SCHEDULER_PROBE_VAR", "CARTOSKY_V3_SCHEDULER_PROBE_VAR", "TWF_V3_SCHEDULER_PROBE_VAR")
ENV_HERBIE_PRIORITY = ("CARTOSKY_HERBIE_PRIORITY", "TWF_HERBIE_PRIORITY")
ENV_HERBIE_SAVE_DIR = ("HERBIE_SAVE_DIR", "CARTOSKY_HERBIE_SAVE_DIR")
ENV_LOOP_PREGENERATE_ENABLED = (
    "CARTOSKY_LOOP_PREGENERATE_ENABLED",
    "CARTOSKY_V3_LOOP_PREGENERATE_ENABLED",
    "TWF_V3_LOOP_PREGENERATE_ENABLED",
)
ENV_LOOP_CACHE_ROOT = ("CARTOSKY_LOOP_CACHE_ROOT", "CARTOSKY_V3_LOOP_CACHE_ROOT", "TWF_V3_LOOP_CACHE_ROOT")
ENV_LOOP_PREGENERATE_WORKERS = (
    "CARTOSKY_LOOP_PREGENERATE_WORKERS",
    "CARTOSKY_V3_LOOP_PREGENERATE_WORKERS",
    "TWF_V3_LOOP_PREGENERATE_WORKERS",
)
ENV_PROGRESS_PUBLISH_MIN_NEW_FRAMES = (
    "CARTOSKY_PROGRESS_PUBLISH_MIN_NEW_FRAMES",
    "CARTOSKY_V3_PROGRESS_PUBLISH_MIN_NEW_FRAMES",
    "TWF_V3_PROGRESS_PUBLISH_MIN_NEW_FRAMES",
)
ENV_SCHEDULER_FH_LOOKAHEAD = "CARTOSKY_SCHEDULER_FH_LOOKAHEAD"
ENV_LOOP_WEBP_QUALITY = ("CARTOSKY_LOOP_WEBP_QUALITY", "CARTOSKY_V3_LOOP_WEBP_QUALITY", "TWF_V3_LOOP_WEBP_QUALITY")
ENV_LOOP_WEBP_MAX_DIM = ("CARTOSKY_LOOP_WEBP_MAX_DIM", "CARTOSKY_V3_LOOP_WEBP_MAX_DIM", "TWF_V3_LOOP_WEBP_MAX_DIM")
ENV_LOOP_WEBP_TIER1_QUALITY = (
    "CARTOSKY_LOOP_WEBP_TIER1_QUALITY",
    "CARTOSKY_V3_LOOP_WEBP_TIER1_QUALITY",
    "TWF_V3_LOOP_WEBP_TIER1_QUALITY",
)
ENV_LOOP_WEBP_TIER1_MAX_DIM = (
    "CARTOSKY_LOOP_WEBP_TIER1_MAX_DIM",
    "CARTOSKY_V3_LOOP_WEBP_TIER1_MAX_DIM",
    "TWF_V3_LOOP_WEBP_TIER1_MAX_DIM",
)
ENV_LOOP_WEBP_TIER0_FIXED_W = (
    "CARTOSKY_LOOP_WEBP_TIER0_FIXED_W",
    "CARTOSKY_V3_LOOP_WEBP_TIER0_FIXED_W",
    "TWF_V3_LOOP_WEBP_TIER0_FIXED_W",
)
ENV_LOOP_WEBP_TIER1_FIXED_W = (
    "CARTOSKY_LOOP_WEBP_TIER1_FIXED_W",
    "CARTOSKY_V3_LOOP_WEBP_TIER1_FIXED_W",
    "TWF_V3_LOOP_WEBP_TIER1_FIXED_W",
)
ENV_LOOP_SHARPEN_ENABLE = (
    "CARTOSKY_LOOP_SHARPEN_ENABLE",
    "CARTOSKY_V3_LOOP_SHARPEN_ENABLE",
    "TWF_V3_LOOP_SHARPEN_ENABLE",
)
ENV_LOOP_SHARPEN_RADIUS = ("CARTOSKY_LOOP_SHARPEN_RADIUS", "CARTOSKY_V3_LOOP_SHARPEN_RADIUS", "TWF_V3_LOOP_SHARPEN_RADIUS")
ENV_LOOP_SHARPEN_PERCENT = (
    "CARTOSKY_LOOP_SHARPEN_PERCENT",
    "CARTOSKY_V3_LOOP_SHARPEN_PERCENT",
    "TWF_V3_LOOP_SHARPEN_PERCENT",
)
ENV_LOOP_SHARPEN_THRESHOLD = (
    "CARTOSKY_LOOP_SHARPEN_THRESHOLD",
    "CARTOSKY_V3_LOOP_SHARPEN_THRESHOLD",
    "TWF_V3_LOOP_SHARPEN_THRESHOLD",
)
# Optional derived bundle mode. Enable when multiple derived snowfall/liquid
# products (for example Kuchera + 10:1 + precip total) should share caches.
ENV_DERIVE_BUNDLE = ("CARTOSKY_DERIVE_BUNDLE", "CARTOSKY_V3_DERIVE_BUNDLE", "TWF_V3_DERIVE_BUNDLE")

DEFAULT_LOOP_PREGENERATE_ENABLED = True
DEFAULT_LOOP_CACHE_ROOT = DEFAULT_DATA_ROOT / "loop_cache"
DEFAULT_LOOP_PREGENERATE_WORKERS = 4
DEFAULT_LOOP_PREWARM_FRAME_COUNT = 8
DEFAULT_PROGRESS_PUBLISH_MIN_NEW_FRAMES = 4
DEFAULT_SCHEDULER_FH_LOOKAHEAD = 4
DEFAULT_LOOP_WEBP_QUALITY = 82
DEFAULT_LOOP_WEBP_MAX_DIM = 2300
DEFAULT_LOOP_WEBP_TIER1_QUALITY = 86
DEFAULT_LOOP_WEBP_TIER1_MAX_DIM = 2400
DEFAULT_LOOP_WEBP_TIER0_FIXED_W = 2300
DEFAULT_LOOP_WEBP_TIER1_FIXED_W = 2400
DEFAULT_LOOP_SHARPEN_ENABLE = True
DEFAULT_LOOP_SHARPEN_RADIUS = 1.2
DEFAULT_LOOP_SHARPEN_PERCENT = 35
DEFAULT_LOOP_SHARPEN_THRESHOLD = 3
DEFAULT_DERIVE_BUNDLE = False

BuildTarget = tuple[str, str, int]
BuildOutcome = tuple[str, str, int, bool, int | None, str]
BUILD_STATUS_OK = "ok"
BUILD_STATUS_FAILED = "failed"
BUILD_STATUS_TRANSIENT = "transient_unavailable"


class SchedulerConfigError(RuntimeError):
    pass


def _env_value(env_name: str | tuple[str, ...], fallback: str = "") -> str:
    names = (env_name,) if isinstance(env_name, str) else env_name
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return fallback


def _parse_run_id_datetime(run_id: str) -> datetime | None:
    return parse_run_id_datetime(run_id)


def _run_id_from_dt(run_dt: datetime) -> str:
    return format_run_id(run_dt, include_minutes=False)


def _parse_vars(value: str) -> list[str]:
    vars_list = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not vars_list:
        raise SchedulerConfigError("--vars cannot be empty")
    return vars_list


def _parse_vars_or_auto(value: str | None) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    if raw.lower() in AUTO_VARS_TOKENS:
        return []
    return _parse_vars(raw)


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _path_permission_debug(path: Path) -> str:
    try:
        st = path.stat()
        mode = oct(st.st_mode & 0o777)
        return f"exists uid={st.st_uid} gid={st.st_gid} mode={mode}"
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "unstatable(permission denied)"
    except OSError as exc:
        return f"unstatable({exc.__class__.__name__}: {exc})"


def _data_root(cli_data_root: str | None) -> Path:
    if cli_data_root:
        return Path(cli_data_root).resolve()
    return Path(
        _env_value(("CARTOSKY_DATA_ROOT", "CARTOSKY_V3_DATA_ROOT", "TWF_V3_DATA_ROOT"), str(DEFAULT_DATA_ROOT))
    ).resolve()


def _workers(cli_workers: int | None) -> int:
    if cli_workers is not None and cli_workers > 0:
        return cli_workers
    raw = _env_value(("CARTOSKY_WORKERS", "CARTOSKY_V3_WORKERS", "TWF_V3_WORKERS"), "4").strip()
    try:
        value = int(raw)
    except ValueError:
        return 4
    return value if value > 0 else 4


def _int_from_env(env_name: str | tuple[str, ...], fallback: int, *, min_value: int) -> int:
    raw = _env_value(env_name).strip()
    if not raw:
        return fallback
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using fallback=%d", env_name, raw, fallback)
        return fallback
    return parsed if parsed >= min_value else fallback


def _float_from_env(env_name: str | tuple[str, ...], fallback: float, *, min_value: float) -> float:
    raw = _env_value(env_name).strip()
    if not raw:
        return fallback
    try:
        parsed = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using fallback=%s", env_name, raw, fallback)
        return fallback
    return parsed if parsed >= min_value else fallback


def _bool_from_env(env_name: str | tuple[str, ...], fallback: bool) -> bool:
    raw = _env_value(env_name).strip().lower()
    if not raw:
        return fallback
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid %s=%r; using fallback=%s", env_name, raw, fallback)
    return fallback


@lru_cache(maxsize=1)
def _loop_sharpen_config() -> tuple[bool, float, int, int]:
    enable = _bool_from_env(ENV_LOOP_SHARPEN_ENABLE, DEFAULT_LOOP_SHARPEN_ENABLE)
    radius = _float_from_env(ENV_LOOP_SHARPEN_RADIUS, DEFAULT_LOOP_SHARPEN_RADIUS, min_value=0.0)
    percent = _int_from_env(ENV_LOOP_SHARPEN_PERCENT, DEFAULT_LOOP_SHARPEN_PERCENT, min_value=0)
    threshold = _int_from_env(ENV_LOOP_SHARPEN_THRESHOLD, DEFAULT_LOOP_SHARPEN_THRESHOLD, min_value=0)
    return enable, radius, percent, threshold


def _int_or_default(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _resolve_model(model_id: str):
    plugin = MODEL_REGISTRY.get(model_id)
    if plugin is None:
        raise SchedulerConfigError(f"Unknown model: {model_id}")
    return plugin


def _resolve_vars_to_schedule(plugin, requested: list[str]) -> list[str]:
    resolved: list[str] = []

    if requested:
        for raw in requested:
            normalized = plugin.normalize_var_id(raw)
            capability = plugin.get_var_capability(normalized)
            if capability is not None:
                if not bool(getattr(capability, "buildable", False)):
                    logger.info("Skipping non-buildable var: %s", normalized)
                    continue
                resolved.append(normalized)
                continue
            spec = plugin.get_var(normalized)
            if spec is None:
                logger.warning("Skipping unknown var for model=%s: %s", plugin.id, raw)
                continue
            if not (bool(getattr(spec, "primary", False)) or bool(getattr(spec, "derived", False))):
                logger.info("Skipping component-only var: %s", normalized)
                continue
            resolved.append(normalized)
        return _dedupe_preserve_order(resolved)

    for var_id, spec in plugin.vars.items():
        normalized = plugin.normalize_var_id(var_id)
        capability = plugin.get_var_capability(normalized)
        if capability is not None:
            if bool(getattr(capability, "buildable", False)):
                resolved.append(normalized)
            continue
        if plugin.get_var(normalized) is None:
            continue
        if bool(getattr(spec, "primary", False)) or bool(getattr(spec, "derived", False)):
            resolved.append(normalized)
    return _dedupe_preserve_order(resolved)


def _default_build_region(plugin: Any) -> str:
    capabilities = getattr(plugin, "capabilities", None)
    configured = str(getattr(capabilities, "canonical_region", "") or "").strip().lower()
    return configured or CANONICAL_COVERAGE


def _build_regions_for_var(plugin: Any, var_id: str) -> list[str]:
    default_region = _default_build_region(plugin)
    if hasattr(plugin, "get_region") and plugin.get_region(default_region) is None:
        raise SchedulerConfigError(
            f"Model {getattr(plugin, 'id', 'unknown')!r} does not define default build region {default_region!r}"
        )
    return [default_region]


def _companion_vars_for_var(plugin: Any, var_id: str) -> list[str]:
    full_catalog = getattr(getattr(plugin, "capabilities", None), "variable_catalog", {}) or {}
    capability = full_catalog.get(plugin.normalize_var_id(var_id)) if isinstance(full_catalog, dict) else None
    frontend = getattr(capability, "frontend", {}) if capability is not None else {}
    companion_vars = frontend.get("companion_vars") if isinstance(frontend, dict) else None
    if not isinstance(companion_vars, list):
        return []
    resolved: list[str] = []
    for companion_var in companion_vars:
        if isinstance(companion_var, str) and companion_var.strip():
            resolved.append(plugin.normalize_var_id(companion_var))
    return resolved


def _probe_search_pattern(plugin: Any, probe_var: str) -> str:
    probe_var_key = plugin.normalize_var_id(probe_var)
    probe_capability = plugin.get_var_capability(probe_var_key)
    probe_spec = plugin.get_var(probe_var_key)
    if probe_capability is None and probe_spec is None:
        raise SchedulerConfigError(f"Probe var {probe_var!r} not found for model={plugin.id}")

    selectors = (
        getattr(probe_capability, "selectors", None)
        if probe_capability is not None
        else getattr(probe_spec, "selectors", None)
    )
    searches = getattr(selectors, "search", None) if selectors is not None else None
    if not searches:
        raise SchedulerConfigError(
            f"Probe var {probe_var_key!r} has no search pattern and cannot be used for run probing"
        )
    return str(searches[0])


def _resolve_probe_fhs(plugin: Any) -> list[int]:
    run_discovery = plugin.run_discovery_config()
    raw_probe_fhs = run_discovery.get("probe_fhs")
    if isinstance(raw_probe_fhs, (list, tuple)):
        resolved: list[int] = []
        for value in raw_probe_fhs:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed < 0:
                continue
            resolved.append(parsed)
        if resolved:
            return resolved
    return [0]


def _probe_run_exists(*, plugin: Any, run_dt: datetime, probe_var: str) -> bool:
    from herbie.core import Herbie

    search_pattern = _probe_search_pattern(plugin, probe_var)
    probe_var_key = plugin.normalize_var_id(probe_var)
    herbie_date = run_dt.replace(tzinfo=None) if run_dt.tzinfo else run_dt
    last_exc: Exception | None = None
    probe_fhs = _resolve_probe_fhs(plugin)
    run_discovery = plugin.run_discovery_config() if hasattr(plugin, "run_discovery_config") else {}
    allow_grib_without_idx = bool(run_discovery.get("allow_grib_without_idx", False))
    for probe_fh in probe_fhs:
        request = plugin.herbie_request(
            product=getattr(plugin, "product", "sfc"),
            var_key=probe_var_key,
            ensemble_view=_var_default_ensemble_view(plugin, probe_var_key),
            run_date=run_dt,
            fh=probe_fh,
            search_pattern=search_pattern,
        )
        request_kwargs = dict(getattr(request, "herbie_kwargs", {}) or {})
        raw_priorities = request_kwargs.pop("priority", None)
        if isinstance(raw_priorities, (list, tuple)):
            priorities = [str(item).strip().lower() for item in raw_priorities if str(item).strip()]
        elif raw_priorities:
            priorities = [str(raw_priorities).strip().lower()]
        else:
            priority_raw = _env_value(ENV_HERBIE_PRIORITY, "aws,nomads,google,azure,pando,pando2")
            priorities = [item.strip().lower() for item in priority_raw.split(",") if item.strip()]
        if not priorities:
            priorities = ["aws", "nomads", "google", "azure", "pando", "pando2"]

        for priority in priorities:
            H = None
            try:
                H = Herbie(
                    herbie_date,
                    model=request.model,
                    product=request.product,
                    fxx=probe_fh,
                    priority=priority,
                    **request_kwargs,
                )
                inventory = H.inventory(search_pattern)
                if inventory is not None and len(inventory) > 0:
                    logger.info(
                        "Run probe success: model=%s run=%s probe_var=%s fh=%s priority=%s",
                        plugin.id,
                        _run_id_from_dt(run_dt),
                        probe_var_key,
                        probe_fh,
                        priority,
                    )
                    return True
            except Exception as exc:
                last_exc = exc
                if allow_grib_without_idx and "no index file was found for" in str(exc).lower() and getattr(H, "grib", None):
                    logger.info(
                        "Run probe success via GRIB fallback: model=%s run=%s probe_var=%s fh=%s priority=%s",
                        plugin.id,
                        _run_id_from_dt(run_dt),
                        probe_var_key,
                        probe_fh,
                        priority,
                    )
                    return True
                continue

    logger.info(
        "Run probe miss: model=%s run=%s probe_var=%s fhs=%s (%s)",
        plugin.id,
        _run_id_from_dt(run_dt),
        probe_var_key,
        probe_fhs,
        last_exc,
    )
    return False


def _align_to_cycle_hour(run_dt: datetime, cadence_hours: int) -> datetime:
    cadence = max(1, int(cadence_hours))
    aligned_hour = (run_dt.hour // cadence) * cadence
    return run_dt.replace(hour=aligned_hour, minute=0, second=0, microsecond=0)


def _resolve_latest_run_dt(*, plugin: Any, probe_var: str | None) -> datetime:
    now = datetime.now(timezone.utc)
    run_discovery = plugin.run_discovery_config()
    cadence_hours = _int_or_default(run_discovery.get("cycle_cadence_hours"), 1, minimum=1)
    probe_enabled = bool(run_discovery.get("probe_enabled", False))
    probe_attempts = _int_or_default(run_discovery.get("probe_attempts"), 1, minimum=1)
    fallback_lag_hours = _int_or_default(run_discovery.get("fallback_lag_hours"), 3, minimum=0)

    if probe_enabled and probe_var:
        base = _align_to_cycle_hour(now, cadence_hours)
        attempts_used = 0
        for offset in range(probe_attempts):
            attempts_used += 1
            candidate = base - timedelta(hours=offset * cadence_hours)
            if _probe_run_exists(plugin=plugin, run_dt=candidate, probe_var=probe_var):
                logger.info(
                    "Run probe summary: model=%s base_run=%s target_run=%s probe_var=%s attempts=%d/%d success=true reason=probe_hit fallback_used=false",
                    plugin.id,
                    _run_id_from_dt(base),
                    _run_id_from_dt(candidate),
                    probe_var,
                    attempts_used,
                    probe_attempts,
                )
                return candidate
        fallback = _align_to_cycle_hour(now - timedelta(hours=fallback_lag_hours), cadence_hours)
        logger.warning(
            "Run probe failed after %d attempts for model=%s; falling back to run=%s",
            probe_attempts,
            plugin.id,
            _run_id_from_dt(fallback),
        )
        logger.info(
            "Run probe summary: model=%s base_run=%s target_run=%s probe_var=%s attempts=%d/%d success=false reason=probe_miss fallback_used=true fallback_run=%s",
            plugin.id,
            _run_id_from_dt(base),
            _run_id_from_dt(base),
            probe_var,
            attempts_used,
            probe_attempts,
            _run_id_from_dt(fallback),
        )
        return fallback

    if probe_enabled and not probe_var:
        logger.warning("Run probe requested for model=%s but no probe var resolved; using heuristic", plugin.id)
    target = now - timedelta(hours=fallback_lag_hours)
    resolved = _align_to_cycle_hour(target, cadence_hours)
    logger.info(
        "Run probe summary: model=%s base_run=%s target_run=%s probe_var=%s attempts=0/%d success=%s reason=%s fallback_used=%s",
        plugin.id,
        _run_id_from_dt(_align_to_cycle_hour(now, cadence_hours)),
        _run_id_from_dt(resolved),
        probe_var or "none",
        probe_attempts,
        "false" if probe_enabled and not probe_var else "true",
        "probe_var_unset" if probe_enabled and not probe_var else "heuristic",
        "true" if probe_enabled and not probe_var else "false",
    )
    return resolved


def _resolve_run_dt(run_arg: str | None, *, plugin: Any, probe_var: str | None) -> datetime:
    if run_arg:
        parsed = _parse_run_id_datetime(run_arg)
        if parsed is None:
            raise SchedulerConfigError(
                f"Invalid --run value {run_arg!r}. Expected YYYYMMDD_HHz (e.g. 20260217_06z)."
            )
        return parsed
    return _resolve_latest_run_dt(plugin=plugin, probe_var=probe_var)


def _var_default_ensemble_view(plugin: Any, var_id: str) -> str | None:
    if hasattr(plugin, "default_ensemble_view"):
        value = plugin.default_ensemble_view(plugin.normalize_var_id(var_id))
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    capabilities = getattr(plugin, "capabilities", None)
    defaults = getattr(capabilities, "ui_defaults", {}) if capabilities is not None else {}
    if isinstance(defaults, dict):
        value = str(defaults.get("default_ensemble_view") or "").strip().lower()
        if value:
            return value
    return None


def _runtime_var_id(plugin: Any, var_id: str, ensemble_view: str | None = None) -> str:
    normalized_var = plugin.normalize_var_id(var_id) if hasattr(plugin, "normalize_var_id") else str(var_id)
    if hasattr(plugin, "resolve_runtime_var_id"):
        return str(plugin.resolve_runtime_var_id(normalized_var, ensemble_view)).strip() or normalized_var
    return normalized_var


def _scheduled_targets_for_cycle(plugin, vars_to_build: list[str], cycle_hour: int) -> list[BuildTarget]:
    targets: list[BuildTarget] = []
    seen: set[BuildTarget] = set()

    def _append_target(region: str, var_id: str, fh: int) -> None:
        key = (str(region).strip().lower(), plugin.normalize_var_id(var_id), int(fh))
        if key in seen:
            return
        seen.add(key)
        targets.append(key)

    for var_id in vars_to_build:
        fhs = (
            list(plugin.scheduled_fhs_for_var(var_id, cycle_hour))
            if hasattr(plugin, "scheduled_fhs_for_var")
            else [int(fh) for fh in plugin.target_fhs(cycle_hour)]
        )
        normalized_var = plugin.normalize_var_id(var_id)
        for fh in fhs:
            for region in _build_regions_for_var(plugin, normalized_var):
                _append_target(region, normalized_var, int(fh))
            for companion_var in _companion_vars_for_var(plugin, normalized_var):
                for region in _build_regions_for_var(plugin, companion_var):
                    _append_target(region, companion_var, int(fh))
    return targets


def _frame_sidecar_path(
    data_root: Path,
    model: str,
    run_id: str,
    var_id: str,
    fh: int,
    *,
    region: str = CANONICAL_COVERAGE,
) -> Path:
    plugin = MODEL_REGISTRY.get(model)
    runtime_var_id = _runtime_var_id(plugin, var_id, _var_default_ensemble_view(plugin, var_id)) if plugin is not None else str(var_id)
    del region
    return data_root / "staging" / model / run_id / runtime_var_id / f"fh{fh:03d}.json"


def _frame_value_path(
    data_root: Path,
    model: str,
    run_id: str,
    var_id: str,
    fh: int,
    *,
    region: str = CANONICAL_COVERAGE,
) -> Path:
    plugin = MODEL_REGISTRY.get(model)
    runtime_var_id = _runtime_var_id(plugin, var_id, _var_default_ensemble_view(plugin, var_id)) if plugin is not None else str(var_id)
    del region
    return data_root / "staging" / model / run_id / runtime_var_id / f"fh{fh:03d}.val.cog.tif"


def _frame_artifacts_exist(
    data_root: Path,
    model: str,
    run_id: str,
    var_id: str,
    fh: int,
    *,
    region: str = CANONICAL_COVERAGE,
) -> bool:
    val = _frame_value_path(data_root, model, run_id, var_id, fh, region=region)
    side = _frame_sidecar_path(data_root, model, run_id, var_id, fh, region=region)

    def _safe_exists(path: Path) -> bool:
        try:
            return path.exists()
        except PermissionError:
            logger.warning("Permission denied while checking artifact path: %s", path)
            return False

    return _safe_exists(val) and _safe_exists(side)


def _sidecar_quality(
    data_root: Path,
    model: str,
    run_id: str,
    var_id: str,
    fh: int,
    *,
    region: str = CANONICAL_COVERAGE,
) -> tuple[str, list[str]]:
    sidecar_path = _frame_sidecar_path(data_root, model, run_id, var_id, fh, region=region)
    if not sidecar_path.exists():
        return "full", []
    try:
        payload = json.loads(sidecar_path.read_text())
    except (OSError, json.JSONDecodeError):
        return "full", []

    quality = str(payload.get("quality", "full")).strip().lower()
    normalized_quality = "degraded" if quality == "degraded" else "full"
    flags_raw = payload.get("quality_flags", [])
    if not isinstance(flags_raw, list):
        return normalized_quality, []
    flags = [
        item for item in dict.fromkeys(str(flag).strip() for flag in flags_raw)
        if item
    ]
    return normalized_quality, flags


def _collect_slr_rebuild_candidates(
    *,
    data_root: Path,
    model_id: str,
    run_id: str,
    targets: list[BuildTarget],
    attempts: dict[tuple[str, str, str, int], int],
    max_attempts: int,
) -> list[BuildTarget]:
    seen: set[BuildTarget] = set()
    candidates: list[BuildTarget] = []
    for region, var_id, fh in targets:
        key = (run_id, str(region), str(var_id), int(fh))
        if int(attempts.get(key, 0)) >= int(max_attempts):
            continue
        quality, quality_flags = _sidecar_quality(
            data_root,
            model_id,
            run_id,
            str(var_id),
            int(fh),
            region=str(region),
        )
        if quality != "degraded":
            continue
        if "slr_fallback_10to1" not in quality_flags:
            continue
        candidate = (str(region), str(var_id), int(fh))
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return sorted(candidates, key=lambda item: (item[1], item[0]))


def _parse_hint_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_hint_int(value: Any, *, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _parse_kuchera_levels_hpa(value: Any) -> list[int]:
    if value is None:
        tokens = [925, 850, 700, 600, 500]
    elif isinstance(value, (list, tuple, set)):
        tokens = list(value)
    else:
        tokens = [item.strip() for item in str(value).replace(";", ",").split(",") if item.strip()]

    levels: list[int] = []
    for token in tokens:
        try:
            level = int(token)
        except (TypeError, ValueError):
            continue
        if level <= 0 or level in levels:
            continue
        levels.append(level)
    return levels if levels else [925, 850, 700, 600, 500]


def _component_precheck_available(
    *,
    plugin: Any,
    model_id: str,
    product: str,
    run_dt: datetime,
    fh: int,
    var_key: str,
) -> bool:
    spec = plugin.get_var(var_key) if hasattr(plugin, "get_var") else None
    selectors = getattr(spec, "selectors", None)
    search_patterns = list(getattr(selectors, "search", []) or [])
    if not search_patterns:
        return False

    for pattern in search_patterns:
        try:
            request = plugin.herbie_request(
                product=product,
                var_key=var_key,
                ensemble_view=_var_default_ensemble_view(plugin, var_key),
                run_date=run_dt,
                fh=int(fh),
                search_pattern=str(pattern),
            )
            fetch_variable(
                model_id=model_id,
                product=request.product,
                search_pattern=str(pattern),
                run_date=run_dt,
                fh=int(fh),
                herbie_kwargs=getattr(request, "herbie_kwargs", None),
            )
            return True
        except (HerbieTransientUnavailableError, RuntimeError, ValueError):
            continue
        except Exception:
            continue
    return False


def _kuchera_rebuild_profile_ready(
    *,
    plugin: Any,
    model_id: str,
    run_dt: datetime,
    var_id: str,
    fh: int,
) -> bool:
    normalized_var = (
        plugin.normalize_var_id(var_id)
        if hasattr(plugin, "normalize_var_id")
        else str(var_id)
    )
    if normalized_var != "snowfall_kuchera_total":
        return False

    var_spec = plugin.get_var(normalized_var) if hasattr(plugin, "get_var") else None
    selectors = getattr(var_spec, "selectors", None)
    hints = dict(getattr(selectors, "hints", {}) or {})

    profile_product_raw = str(hints.get("kuchera_profile_product", "")).strip()
    profile_product = profile_product_raw or str(getattr(plugin, "product", "sfc"))
    levels_hpa = _parse_kuchera_levels_hpa(hints.get("kuchera_levels_hpa"))
    require_rh = _parse_hint_bool(hints.get("kuchera_require_rh"), default=True)
    min_levels = _parse_hint_int(hints.get("kuchera_min_levels"), default=4, minimum=1)

    available_levels = 0
    for level_hpa in levels_hpa:
        temp_ok = _component_precheck_available(
            plugin=plugin,
            model_id=model_id,
            product=profile_product,
            run_dt=run_dt,
            fh=fh,
            var_key=f"tmp{int(level_hpa)}",
        )
        if not temp_ok:
            continue
        if require_rh:
            rh_ok = _component_precheck_available(
                plugin=plugin,
                model_id=model_id,
                product=profile_product,
                run_dt=run_dt,
                fh=fh,
                var_key=f"rh{int(level_hpa)}",
            )
            if not rh_ok:
                continue
        available_levels += 1

    return available_levels >= min_levels


def _run_is_superseded(*, plugin: Any, run_dt: datetime) -> bool:
    try:
        latest = _resolve_latest_run_dt(plugin=plugin, probe_var=None)
    except Exception:
        return False
    return bool(latest > run_dt)


def _build_one(
    *,
    model_id: str,
    region: str,
    var_id: str,
    fh: int,
    run_dt: datetime,
    data_root: Path,
    plugin,
    fetch_ctx: FetchContext | None = None,
    readiness_cache: dict[str, bool] | None = None,
    log_fetch_cache_stats: bool = True,
    derive_component_warp_cache: bool = False,
) -> tuple[str, str, int, bool, int, str]:
    started_at = time.perf_counter()
    ensemble_view = _var_default_ensemble_view(plugin, var_id)
    runtime_var_id = _runtime_var_id(plugin, var_id, ensemble_view)
    result = build_frame(
        model=model_id,
        region=region,
        var_id=runtime_var_id,
        fh=fh,
        run_date=run_dt,
        data_root=data_root,
        product=getattr(plugin, "product", "sfc"),
        model_plugin=plugin,
        ensemble_view=ensemble_view,
        fetch_ctx=fetch_ctx,
        readiness_cache=readiness_cache,
        log_fetch_cache_stats=log_fetch_cache_stats,
        derive_component_warp_cache=derive_component_warp_cache,
        return_status=True,
    )
    frame_path, status = result if isinstance(result, tuple) else (result, BUILD_STATUS_OK if result is not None else BUILD_STATUS_FAILED)
    normalized_status = str(status or "").strip().lower() or (BUILD_STATUS_OK if frame_path is not None else BUILD_STATUS_FAILED)
    ok = frame_path is not None and normalized_status == BUILD_STATUS_OK
    return region, var_id, fh, ok, int((time.perf_counter() - started_at) * 1000), normalized_status


def _is_derive_bundle_candidate(plugin: Any, var_id: str) -> bool:
    normalize = getattr(plugin, "normalize_var_id", None)
    normalized: str = str(normalize(var_id)) if callable(normalize) else str(var_id)
    plugin_id = str(getattr(plugin, "id", "") or "").strip().lower()
    if plugin_id == "gfs" and (
        normalized == "ptype_intensity" or normalized.startswith("ptype_intensity_")
    ):
        return True
    capability = plugin.get_var_capability(normalized) if hasattr(plugin, "get_var_capability") else None
    if plugin_id == "gfs":
        derive_strategy = getattr(capability, "derive_strategy_id", None)
        if str(derive_strategy or "").strip().lower() == "precip_accum_anomaly_departure":
            return True
    if plugin_id in {"hrrr", "nam"} and (
        normalized == "radar_ptype" or normalized.startswith("radar_ptype_")
    ):
        return True
    if plugin_id == "eps":
        return True
    if normalized == "precip_total":
        return False
    if normalized == "snowfall_total" or normalized.startswith("snowfall_"):
        return False

    var_spec = plugin.get_var(normalized) if hasattr(plugin, "get_var") else None
    derive_kind = (
        getattr(capability, "derive_strategy_id", None)
        or getattr(var_spec, "derive", None)
    )
    derive_kind_str = str(derive_kind or "").strip().lower()
    if derive_kind_str == "precip_total_cumulative":
        return False
    return False


def _build_bundle(
    *,
    model_id: str,
    region: str,
    var_ids: list[str],
    fh: int,
    run_dt: datetime,
    data_root: Path,
    plugin: Any,
) -> list[tuple[str, str, int, bool, int, str]]:
    normalized_vars: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for var_id in var_ids:
        normalized = plugin.normalize_var_id(var_id) if hasattr(plugin, "normalize_var_id") else str(var_id)
        ensemble_view = _var_default_ensemble_view(plugin, normalized)
        runtime_var_id = _runtime_var_id(plugin, normalized, ensemble_view)
        if runtime_var_id in seen:
            continue
        seen.add(runtime_var_id)
        normalized_vars.append((normalized, runtime_var_id, ensemble_view))

    if not normalized_vars:
        return []

    bundle_result = build_frame_bundle(
        model=model_id,
        region=region,
        var_keys=[runtime_var_id for _, runtime_var_id, _ in normalized_vars],
        fh=fh,
        run_date=run_dt,
        data_root=data_root,
        product=getattr(plugin, "product", "sfc"),
        model_plugin=plugin,
        include_timings=True,
        include_statuses=True,
    )
    if len(bundle_result) == 3:
        results, timings_ms, statuses = bundle_result
    else:
        results, timings_ms = bundle_result
        statuses = {}

    def _bundle_status(runtime_var_id: str) -> str:
        fallback = BUILD_STATUS_OK if results.get(runtime_var_id) is not None else BUILD_STATUS_FAILED
        return str(statuses.get(runtime_var_id, fallback)).strip().lower()

    return [
        (
            region,
            var_key,
            fh,
            results.get(runtime_var_id) is not None and _bundle_status(runtime_var_id) == BUILD_STATUS_OK,
            int(timings_ms.get(runtime_var_id, 0)),
            _bundle_status(runtime_var_id),
        )
        for var_key, runtime_var_id, _ensemble_view in normalized_vars
    ]


def _coerce_build_outcome(outcome: tuple[Any, ...]) -> BuildOutcome:
    if len(outcome) < 4:
        raise ValueError(f"Invalid build outcome: {outcome!r}")
    region = str(outcome[0]).strip().lower()
    var_id = str(outcome[1])
    fh = int(outcome[2])
    ok = bool(outcome[3])
    elapsed_ms: int | None = None
    if len(outcome) >= 5 and outcome[4] is not None:
        elapsed_ms = int(outcome[4])
    status = BUILD_STATUS_OK if ok else BUILD_STATUS_FAILED
    if len(outcome) >= 6 and outcome[5] is not None:
        parsed_status = str(outcome[5]).strip().lower()
        if parsed_status:
            status = parsed_status
    return region, var_id, fh, ok, elapsed_ms, status


def _log_frame_timing(
    *,
    run_id: str,
    model_id: str,
    region: str,
    var_id: str,
    fh: int,
    ok: bool,
    elapsed_ms: int | None,
    mode: str,
) -> None:
    logger.info(
        "Frame timing: run=%s model=%s region=%s var=%s fh%03d ok=%s mode=%s elapsed_ms=%s",
        run_id,
        model_id,
        region,
        var_id,
        fh,
        "true" if ok else "false",
        mode,
        str(int(elapsed_ms)) if elapsed_ms is not None else "unknown",
    )


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    tmp.replace(path)


def _normalized_publish_region(region: str | None) -> str:
    normalized = str(region or "").strip().lower()
    return normalized or CANONICAL_COVERAGE


def _latest_pointer_path(data_root: Path, model: str, *, region: str | None = None) -> Path:
    del region
    return data_root / "published" / model / "LATEST.json"


def _manifest_path(data_root: Path, model: str, run_id: str, *, region: str | None = None) -> Path:
    del region
    return data_root / "manifests" / model / f"{run_id}.json"


def _copy_or_link_file(src: str, dst: str) -> str:
    if os.path.exists(dst):
        try:
            if os.path.samefile(src, dst):
                return dst
        except OSError:
            pass
        os.unlink(dst)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


_TEMPFILE_NAME_RE = re.compile(r"^tmp[a-z0-9_]{6,}$", re.IGNORECASE)
_RUNTIME_VAR_NAME_RE = re.compile(r"^[a-z0-9_]+__[a-z0-9_]+$", re.IGNORECASE)
_TEMPFILE_VAR_DIR_ALLOWLIST = {
    "tmp2m",
    "tmp2m_anom",
    "tmp925",
    "tmp850",
    "tmp850_anom",
    "tmp700",
    "tmp600",
    "tmp500",
}


def _is_transient_promotion_artifact(path: Path) -> bool:
    name = path.name
    if name.endswith(".tmp") or (name.startswith(".") and name.endswith(".tmp")):
        return True
    if _RUNTIME_VAR_NAME_RE.match(name):
        return False
    if name in _TEMPFILE_VAR_DIR_ALLOWLIST:
        return False
    if _TEMPFILE_NAME_RE.match(name):
        return True
    return False


def _promotion_copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    parent = Path(directory)
    for name in names:
        try:
            if _is_transient_promotion_artifact(parent / name):
                ignored.add(name)
        except OSError:
            ignored.add(name)
    return ignored


def _write_latest_pointer(data_root: Path, model: str, run_id: str, *, region: str = CANONICAL_COVERAGE) -> None:
    run_dt = _parse_run_id_datetime(run_id)
    if run_dt is None:
        raise SchedulerConfigError(f"Cannot write LATEST.json for invalid run_id={run_id!r}")
    normalized_region = _normalized_publish_region(region)
    payload = {
        "run_id": run_id,
        "cycle_utc": run_dt.strftime("%Y-%m-%dT%H:00:00Z"),
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scheduler_v3",
        "region": normalized_region,
    }
    latest_path = _latest_pointer_path(data_root, model, region=normalized_region)
    _write_json_atomic(latest_path, payload)


def _promotion_ready_regions(
    data_root: Path,
    model: str,
    run_id: str,
    primary_vars: list[str],
    promotion_fhs: Iterable[int],
) -> list[str]:
    plugin = MODEL_REGISTRY.get(model)
    ready_regions: list[str] = []
    seen: set[str] = set()
    for var_id in primary_vars:
        regions = _build_regions_for_var(plugin, var_id) if plugin is not None else [CANONICAL_COVERAGE]
        for region in regions:
            if region in seen:
                continue
            for fh in promotion_fhs:
                val = _frame_value_path(data_root, model, run_id, var_id, int(fh), region=region)
                side = _frame_sidecar_path(data_root, model, run_id, var_id, int(fh), region=region)
                if val.exists() and side.exists():
                    seen.add(region)
                    ready_regions.append(region)
                    break
    return ready_regions


def _should_promote(
    data_root: Path,
    model: str,
    run_id: str,
    primary_vars: list[str],
    promotion_fhs: Iterable[int],
) -> bool:
    return bool(_promotion_ready_regions(data_root, model, run_id, primary_vars, promotion_fhs))


def _resolve_promotion_fhs(plugin: Any, primary_vars: list[str], cycle_hour: int) -> tuple[int, ...]:
    desired = max(1, len(DEFAULT_PROMOTION_FHS))
    available_fhs: set[int] = set()

    for var_id in primary_vars:
        try:
            if hasattr(plugin, "scheduled_fhs_for_var"):
                var_fhs = plugin.scheduled_fhs_for_var(var_id, cycle_hour)
            else:
                var_fhs = plugin.target_fhs(cycle_hour)
        except Exception:
            continue
        for fh in var_fhs:
            try:
                parsed = int(fh)
            except (TypeError, ValueError):
                continue
            if parsed < 0:
                continue
            available_fhs.add(parsed)

    if not available_fhs:
        return tuple(int(fh) for fh in DEFAULT_PROMOTION_FHS)
    resolved = tuple(sorted(available_fhs)[:desired])
    if resolved:
        return resolved
    return tuple(int(fh) for fh in DEFAULT_PROMOTION_FHS)


def _resolve_loop_prewarm_var(plugin: Any, vars_to_build: list[str], primary_vars: list[str]) -> str | None:
    normalized_available: list[str] = []
    seen: set[str] = set()

    def _normalize(candidate: object) -> str | None:
        if not isinstance(candidate, str):
            return None
        trimmed = candidate.strip().lower()
        if not trimmed:
            return None
        normalize_var_id = getattr(plugin, "normalize_var_id", None)
        if callable(normalize_var_id):
            trimmed = str(normalize_var_id(trimmed)).strip().lower()
        return trimmed or None

    for collection in (vars_to_build, primary_vars):
        for item in collection:
            normalized = _normalize(item)
            if normalized is None or normalized in seen:
                continue
            normalized_available.append(normalized)
            seen.add(normalized)

    if not normalized_available:
        return None

    candidates: list[str] = []
    capabilities = getattr(plugin, "capabilities", None)
    ui_defaults = getattr(capabilities, "ui_defaults", None)
    if isinstance(ui_defaults, dict):
        default_var = _normalize(ui_defaults.get("default_var_key"))
        if default_var is not None:
            candidates.append(default_var)

    for item in primary_vars:
        normalized = _normalize(item)
        if normalized is not None:
            candidates.append(normalized)
    for item in vars_to_build:
        normalized = _normalize(item)
        if normalized is not None:
            candidates.append(normalized)

    fallback = _normalize(DEFAULT_PRIMARY_VAR)
    if fallback is not None:
        candidates.append(fallback)

    for candidate in candidates:
        if candidate in seen:
            return candidate
    return normalized_available[0]


def _resolve_loop_prewarm_fhs(plugin: Any, var_id: str, cycle_hour: int, *, limit: int) -> tuple[int, ...]:
    if not var_id or limit <= 0:
        return ()

    scheduled: list[int] = []
    seen: set[int] = set()
    try:
        if hasattr(plugin, "scheduled_fhs_for_var"):
            raw_fhs = plugin.scheduled_fhs_for_var(var_id, cycle_hour)
        else:
            raw_fhs = plugin.target_fhs(cycle_hour)
    except Exception:
        raw_fhs = []

    for raw in raw_fhs:
        try:
            fh = int(raw)
        except (TypeError, ValueError):
            continue
        if fh < 0 or fh in seen:
            continue
        seen.add(fh)
        scheduled.append(fh)

    if not scheduled:
        return ()

    default_fh: int | None = None
    get_var_capability = getattr(plugin, "get_var_capability", None)
    if callable(get_var_capability):
        capability = get_var_capability(var_id)
        raw_default_fh = getattr(capability, "default_fh", None)
        if isinstance(raw_default_fh, (int, float)) and np.isfinite(raw_default_fh):
            default_fh = int(raw_default_fh)

    pivot_index = 0
    if default_fh is not None:
        pivot_index = min(
            range(len(scheduled)),
            key=lambda idx: (abs(scheduled[idx] - default_fh), scheduled[idx]),
        )

    forward = scheduled[pivot_index:pivot_index + limit]
    if len(forward) >= min(limit, len(scheduled)):
        return tuple(forward)

    needed = min(limit, len(scheduled)) - len(forward)
    backfill_start = max(0, pivot_index - needed)
    return tuple(scheduled[backfill_start:pivot_index] + forward)


def _promote_run(data_root: Path, model: str, run_id: str) -> None:
    stage_run = data_root / "staging" / model / run_id
    if not stage_run.is_dir():
        raise SchedulerConfigError(f"Cannot promote missing staging run dir: {stage_run}")

    published_model = data_root / "published" / model
    published_model.mkdir(parents=True, exist_ok=True)

    published_run = published_model / run_id
    tmp_run = published_model / f".{run_id}.tmp"

    if tmp_run.exists():
        shutil.rmtree(tmp_run, ignore_errors=True)
    if tmp_run.exists():
        raise SchedulerConfigError(f"Cannot clear temporary promotion dir: {tmp_run}")

    if published_run.exists():
        shutil.copytree(
            published_run,
            tmp_run,
            ignore=_promotion_copy_ignore,
            copy_function=_copy_or_link_file,
        )
        shutil.copytree(
            stage_run,
            tmp_run,
            dirs_exist_ok=True,
            ignore=_promotion_copy_ignore,
            copy_function=_copy_or_link_file,
        )
    else:
        shutil.copytree(
            stage_run,
            tmp_run,
            ignore=_promotion_copy_ignore,
            copy_function=_copy_or_link_file,
        )

    if published_run.exists():
        shutil.rmtree(published_run, ignore_errors=True)
    if published_run.exists():
        raise SchedulerConfigError(f"Cannot clear existing published run dir: {published_run}")

    shutil.move(str(tmp_run), str(published_run))


@contextmanager
def _scheduler_model_lock(data_root: Path, model: str) -> Iterator[None]:
    lock_dir = data_root / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{model}.scheduler.lock"

    try:
        import fcntl
    except ImportError:
        logger.warning(
            "Scheduler lock requested but fcntl is unavailable; proceeding unlocked for model=%s",
            model,
        )
        yield
        return

    lock_file = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SchedulerConfigError(
                "Another scheduler is already running for "
                f"model={model} data_root={data_root}. Stop the service or wait for it to finish "
                "before starting a one-shot scheduler."
            ) from exc

        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_file.close()


def _available_target_count(
    data_root: Path,
    model: str,
    run_id: str,
    targets: list[BuildTarget],
) -> int:
    available = 0
    for region, var_id, fh in targets:
        if _frame_artifacts_exist(data_root, model, run_id, var_id, fh, region=region):
            available += 1
    return available


def _write_run_manifest(
    *,
    data_root: Path,
    model: str,
    run_id: str,
    targets: list[BuildTarget],
    plugin: Any | None = None,
    region: str = CANONICAL_COVERAGE,
) -> None:
    run_dt = _parse_run_id_datetime(run_id)
    if run_dt is None:
        raise SchedulerConfigError(f"Invalid run id for manifest: {run_id}")

    manifest_region = _normalized_publish_region(region)

    expected_by_var: dict[str, list[int]] = {}
    for target in targets:
        if len(target) == 3:
            _target_region, var_id, fh = target
        elif len(target) == 2:
            var_id, fh = target
        else:
            raise SchedulerConfigError(f"Invalid manifest target: {target!r}")
        expected_by_var.setdefault(str(var_id), []).append(int(fh))

    manifest_path = _manifest_path(data_root, model, run_id)
    variables: dict[str, dict] = {}
    if manifest_path.exists():
        try:
            existing_payload = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            existing_payload = None
        existing_variables = existing_payload.get("variables") if isinstance(existing_payload, dict) else None
        if isinstance(existing_variables, dict):
            variables.update(
                {
                    str(var_id): dict(entry)
                    for var_id, entry in existing_variables.items()
                    if isinstance(entry, dict)
                }
            )

    for var_id, fhs in sorted(expected_by_var.items()):
        expected_fhs = sorted(set(fhs))
        frames: list[dict] = []
        units = ""
        kind = ""
        display_name = var_id

        if plugin is not None:
            capability = plugin.get_var_capability(var_id) if hasattr(plugin, "get_var_capability") else None
            if capability is not None and getattr(capability, "name", None):
                display_name = str(getattr(capability, "name"))
            else:
                var_spec = plugin.get_var(var_id) if hasattr(plugin, "get_var") else None
                if var_spec is not None and getattr(var_spec, "name", None):
                    display_name = str(getattr(var_spec, "name"))

            full_capability_catalog = getattr(getattr(plugin, "capabilities", None), "variable_catalog", {}) or {}
            raw_capability = full_capability_catalog.get(var_id) if isinstance(full_capability_catalog, dict) else None
            raw_frontend = getattr(raw_capability, "frontend", {}) if raw_capability is not None else {}
            if isinstance(raw_frontend, dict) and bool(raw_frontend.get("internal_only")):
                continue

        for fh in expected_fhs:
            sidecar_path = _frame_sidecar_path(data_root, model, run_id, var_id, fh, region=manifest_region)
            if not sidecar_path.exists():
                continue
            try:
                meta = json.loads(sidecar_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue

            if not units:
                units = str(meta.get("units", ""))
            if not kind:
                kind = str(meta.get("kind", ""))

            valid_time = meta.get("valid_time")
            frame_entry: dict[str, Any] = {"fh": fh}
            if isinstance(valid_time, str) and valid_time:
                frame_entry["valid_time"] = valid_time
            frames.append(frame_entry)

        variables[var_id] = {
            "display_name": display_name,
            "kind": kind,
            "units": units,
            "expected_frames": len(expected_fhs),
            "available_frames": len(frames),
            "frames": sorted(frames, key=lambda item: item["fh"]),
        }
        ensemble_view = _var_default_ensemble_view(plugin, var_id) if plugin is not None else None
        if ensemble_view:
            variables[var_id]["ensemble_view"] = ensemble_view

    payload = {
        "contract_version": "3.0",
        "model": model,
        "run": run_id,
        "region": manifest_region,
        "variables": variables,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    _write_json_atomic(manifest_path, payload)


def _enforce_run_retention(root: Path, keep_runs: int) -> None:
    if keep_runs < 1 or not root.is_dir():
        return

    runs: list[tuple[datetime, Path]] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        run_dt = _parse_run_id_datetime(child.name)
        if run_dt is None:
            continue
        runs.append((run_dt, child))

    if len(runs) <= keep_runs:
        return

    runs.sort(key=lambda pair: pair[0], reverse=True)
    for _, old_run_dir in runs[keep_runs:]:
        logger.info("Removing old run dir: %s", old_run_dir)
        shutil.rmtree(old_run_dir, ignore_errors=True)


def _scheduler_product_category(plugin: Any | None) -> str:
    capabilities = getattr(plugin, "capabilities", None)
    if capabilities is None:
        return "model"

    time_axis_mode = str(getattr(capabilities, "ui_constraints", {}).get("time_axis_mode", "") or "").strip().lower()
    if time_axis_mode == "observed":
        return "obs"
    if time_axis_mode == "valid":
        return "forecasts"

    product = str(getattr(capabilities, "product", "") or getattr(plugin, "product", "") or "").strip().lower()
    if product == "obs":
        return "obs"
    if product == "forecast":
        return "forecasts"

    ensemble = getattr(capabilities, "ensemble", None)
    if isinstance(ensemble, dict) and ensemble:
        return "ensemble"

    return "model"


def _resolved_keep_runs_for_scheduler_plugin(plugin: Any | None, keep_runs: int) -> int:
    category = _scheduler_product_category(plugin)
    if keep_runs != DEFAULT_KEEP_RUNS:
        return keep_runs
    if category == "ensemble":
        return ENSEMBLE_CATEGORY_KEEP_RUNS
    if category == "model":
        return MODEL_CATEGORY_KEEP_RUNS
    return keep_runs


def _extract_herbie_run_id(path: Path, *, model_root: Path) -> str | None:
    try:
        relative = path.relative_to(model_root)
    except ValueError:
        return None
    if len(relative.parts) < 2:
        return None

    day_token = next((part for part in relative.parts[:-1] if re.fullmatch(r"\d{8}", part)), None)
    if day_token is None:
        return None

    name = path.name.lower()
    if name.endswith(".lock"):
        name = name[:-5]

    timestamp_match = re.search(r"(?P<stamp>\d{14})-\d+h-", name)
    if timestamp_match is not None:
        stamp = timestamp_match.group("stamp")
        try:
            return _run_id_from_dt(
                datetime(
                    int(stamp[0:4]),
                    int(stamp[4:6]),
                    int(stamp[6:8]),
                    int(stamp[8:10]),
                    tzinfo=timezone.utc,
                )
            )
        except ValueError:
            return None

    match = re.search(r"t(?P<hour>\d{2})z", name)
    if match is None:
        return None
    return _run_id_from_dt(
        datetime(
            int(day_token[0:4]),
            int(day_token[4:6]),
            int(day_token[6:8]),
            int(match.group("hour")),
            tzinfo=timezone.utc,
        )
    )


def _prune_empty_dirs(root: Path) -> None:
    if not root.is_dir():
        return
    for child in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        try:
            child.rmdir()
        except OSError:
            continue


def _enforce_herbie_cache_retention(root: Path, model_id: str, keep_runs: int) -> None:
    if keep_runs < 1:
        return

    normalized_model_id = str(model_id).strip().lower()
    model_roots = (
        [
            child
            for child in root.iterdir()
            if child.is_dir() and child.name.strip().lower() == normalized_model_id
        ]
        if root.is_dir() and normalized_model_id
        else []
    )
    if not model_roots:
        return

    for model_root in model_roots:
        run_files: dict[str, list[Path]] = {}
        for path in model_root.rglob("*"):
            if not path.is_file():
                continue
            run_id = _extract_herbie_run_id(path, model_root=model_root)
            if run_id is None:
                continue
            run_files.setdefault(run_id, []).append(path)

        if len(run_files) <= keep_runs:
            continue

        sorted_runs = sorted(
            run_files,
            key=lambda run_id: _parse_run_id_datetime(run_id) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for run_id in sorted_runs[keep_runs:]:
            for path in run_files.get(run_id, []):
                logger.info("Removing old Herbie cache file: %s", path)
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    logger.warning("Failed removing old Herbie cache file: %s", path)
        _prune_empty_dirs(model_root)


def _process_run(
    *,
    plugin,
    model_id: str,
    vars_to_build: list[str],
    primary_vars: list[str],
    run_dt: datetime,
    data_root: Path,
    workers: int,
    keep_runs: int,
    loop_pregenerate_enabled: bool,
    loop_cache_root: Path,
    loop_workers: int,
    loop_tier0_quality: int,
    loop_tier0_max_dim: int,
    loop_tier0_fixed_w: int,
    loop_tier1_quality: int,
    loop_tier1_max_dim: int,
    loop_tier1_fixed_w: int,
    rebuild_existing: bool,
) -> tuple[str, int, int]:
    run_id = _run_id_from_dt(run_dt)
    cycle_hour = run_dt.hour
    targets = _scheduled_targets_for_cycle(plugin, vars_to_build, cycle_hour)
    target_regions = sorted({region for region, _var_id, _fh in targets})
    regions_label = ",".join(target_regions) if target_regions else CANONICAL_COVERAGE
    promotion_fhs = _resolve_promotion_fhs(plugin, primary_vars, cycle_hour)
    logger.info(
        "Promotion gate: run=%s model=%s primary=%s fhs=%s",
        run_id,
        model_id,
        primary_vars,
        list(promotion_fhs),
    )

    # Catch up within a single poll cycle: for each variable, keep advancing
    # forecast hours until we hit the first unavailable/failed hour.
    fhs_by_target: dict[tuple[str, str], list[int]] = {}
    for region, var_id, fh in targets:
        fhs_by_target.setdefault((region, var_id), []).append(int(fh))

    total = len(targets)
    built_ok = 0
    blocked_targets: set[tuple[str, str]] = set()
    derive_bundle_enabled = _bool_from_env(ENV_DERIVE_BUNDLE, DEFAULT_DERIVE_BUNDLE)
    progress_publish_min_new_frames = _int_from_env(
        ENV_PROGRESS_PUBLISH_MIN_NEW_FRAMES,
        DEFAULT_PROGRESS_PUBLISH_MIN_NEW_FRAMES,
        min_value=1,
    )
    fh_lookahead = _int_from_env(
        ENV_SCHEDULER_FH_LOOKAHEAD,
        DEFAULT_SCHEDULER_FH_LOOKAHEAD,
        min_value=1,
    )
    loop_prewarm_var = _resolve_loop_prewarm_var(plugin, vars_to_build, primary_vars)
    loop_prewarm_fhs = _resolve_loop_prewarm_fhs(
        plugin,
        loop_prewarm_var or "",
        cycle_hour,
        limit=DEFAULT_LOOP_PREWARM_FRAME_COUNT,
    )
    published_once = False
    built_ok_at_last_publish = -1
    rebuild_attempts: dict[tuple[str, str, str, int], int] = {}
    rebuild_max_attempts = 2
    rebuild_existing_pending = bool(rebuild_existing)
    transient_targets: set[tuple[str, str]] = set()
    shared_parallel_fetch_ctx_by_target: dict[tuple[str, str], FetchContext] = {}
    shared_parallel_readiness_cache_by_target: dict[tuple[str, str], dict[str, bool]] = {}

    def _publish_run_snapshot(*, reason: str, pregenerate_loops: bool) -> None:
        del pregenerate_loops
        ready_regions = _promotion_ready_regions(data_root, model_id, run_id, primary_vars, promotion_fhs)
        if grid_build_enabled():
            try:
                manifest_ok = build_grid_manifests_for_run_root(
                    run_root=data_root / "staging" / model_id / run_id,
                    model=model_id,
                    run=run_id,
                )
                logger.info(
                    "grid manifest build: run=%s model=%s reason=%s manifests=%d",
                    run_id,
                    model_id,
                    reason,
                    manifest_ok,
                )
            except Exception:
                logger.exception("grid manifest build failed: run=%s model=%s reason=%s", run_id, model_id, reason)
        _promote_run(data_root, model_id, run_id)
        canonical_region = _default_build_region(plugin)
        _write_run_manifest(
            data_root=data_root,
            model=model_id,
            run_id=run_id,
            targets=targets,
            plugin=plugin,
            region=canonical_region,
        )
        if ready_regions:
            _write_latest_pointer(data_root, model_id, run_id, region=canonical_region)
        logger.info(
            "Published run snapshot: run=%s model=%s reason=%s built=%d/%d ready_regions=%s canonical_region=%s",
            run_id,
            model_id,
            reason,
            built_ok,
            total,
            ready_regions,
            canonical_region,
        )

    def _log_parallel_shared_fetch_cache(*, regions: set[str]) -> None:
        if not regions:
            return
        totals_by_region: dict[str, dict[str, int]] = {}
        for (region, _var_id), shared_fetch_ctx in shared_parallel_fetch_ctx_by_target.items():
            if region not in regions:
                continue
            totals = totals_by_region.setdefault(
                region,
                {"hits": 0, "misses": 0, "warp_hits": 0, "warp_misses": 0},
            )
            totals["hits"] += int(shared_fetch_ctx.stats.get("hits", 0))
            totals["misses"] += int(shared_fetch_ctx.stats.get("misses", 0))
            totals["warp_hits"] += int(shared_fetch_ctx.warp_stats.get("hits", 0))
            totals["warp_misses"] += int(shared_fetch_ctx.warp_stats.get("misses", 0))

        for region in sorted(totals_by_region):
            totals = totals_by_region[region]
            logger.info(
                "catchup shared_fetch_cache region=%s hits=%d misses=%d warp_hits=%d warp_misses=%d",
                region,
                totals["hits"],
                totals["misses"],
                totals["warp_hits"],
                totals["warp_misses"],
            )

    rounds = 0
    while True:
        if rebuild_existing_pending:
            rebuild_existing_pending = False
            rounds += 1
            round_work = list(targets)
            logger.info(
                "Run=%s model=%s coverage=%s targets=%d catchup_round=%d pending=%d blocked=%d rebuild_round=%s rebuild_existing=%s",
                run_id,
                model_id,
                regions_label,
                total,
                rounds,
                len(round_work),
                len(blocked_targets),
                False,
                True,
            )

            round_successes = 0
            round_transient_failures = 0
            if workers == 1:
                shared_fetch_ctx_by_region: dict[str, FetchContext] = {}
                shared_readiness_cache_by_region: dict[str, dict[str, bool]] = {}
                for region, var_id, fh in round_work:
                    shared_fetch_ctx = shared_fetch_ctx_by_region.setdefault(region, FetchContext(coverage=region))
                    shared_readiness_cache = shared_readiness_cache_by_region.setdefault(region, {})
                    result = _build_one(
                        model_id=model_id,
                        region=region,
                        var_id=var_id,
                        fh=fh,
                        run_dt=run_dt,
                        data_root=data_root,
                        plugin=plugin,
                        fetch_ctx=shared_fetch_ctx,
                        readiness_cache=shared_readiness_cache,
                        log_fetch_cache_stats=False,
                        derive_component_warp_cache=True,
                    )
                    region, var_id, fh, ok, elapsed_ms, status = _coerce_build_outcome(tuple(result))
                    _log_frame_timing(
                        run_id=run_id,
                        model_id=model_id,
                        region=region,
                        var_id=str(var_id),
                        fh=int(fh),
                        ok=ok,
                        elapsed_ms=elapsed_ms,
                        mode="single",
                    )
                    if ok:
                        built_ok += 1
                        round_successes += 1
                        logger.info("Build success: %s %s/%s fh%03d", run_id, region, var_id, fh)
                    elif status == BUILD_STATUS_TRANSIENT:
                        round_transient_failures += 1
                        logger.warning("Build transiently unavailable: %s %s/%s fh%03d", run_id, region, var_id, fh)
                    else:
                        blocked_targets.add((region, var_id))
                        logger.warning("Build skipped/failed: %s %s/%s fh%03d", run_id, region, var_id, fh)
                for region, shared_fetch_ctx in sorted(shared_fetch_ctx_by_region.items()):
                    logger.info(
                        "rebuild_existing shared_fetch_cache region=%s hits=%d misses=%d warp_hits=%d warp_misses=%d",
                        region,
                        int(shared_fetch_ctx.stats.get("hits", 0)),
                        int(shared_fetch_ctx.stats.get("misses", 0)),
                        int(shared_fetch_ctx.warp_stats.get("hits", 0)),
                        int(shared_fetch_ctx.warp_stats.get("misses", 0)),
                    )
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [
                        pool.submit(
                            _build_one,
                            model_id=model_id,
                            region=region,
                            var_id=var_id,
                            fh=fh,
                            run_dt=run_dt,
                            data_root=data_root,
                            plugin=plugin,
                        )
                        for region, var_id, fh in round_work
                    ]

                    for future in concurrent.futures.as_completed(futures):
                        region, var_id, fh, ok, elapsed_ms, status = _coerce_build_outcome(tuple(future.result()))
                        _log_frame_timing(
                            run_id=run_id,
                            model_id=model_id,
                            region=region,
                            var_id=str(var_id),
                            fh=int(fh),
                            ok=ok,
                            elapsed_ms=elapsed_ms,
                            mode="single",
                        )
                        if ok:
                            built_ok += 1
                            round_successes += 1
                            logger.info("Build success: %s %s/%s fh%03d", run_id, region, var_id, fh)
                        elif status == BUILD_STATUS_TRANSIENT:
                            round_transient_failures += 1
                            logger.warning("Build transiently unavailable: %s %s/%s fh%03d", run_id, region, var_id, fh)
                        else:
                            blocked_targets.add((region, var_id))
                            logger.warning("Build skipped/failed: %s %s/%s fh%03d", run_id, region, var_id, fh)

            if not published_once and _should_promote(data_root, model_id, run_id, primary_vars, promotion_fhs):
                _publish_run_snapshot(reason="rebuild_existing", pregenerate_loops=False)
                published_once = True
                built_ok_at_last_publish = built_ok
            continue

        next_missing: list[BuildTarget] = []
        for (region, var_id), fhs in fhs_by_target.items():
            if (region, var_id) in blocked_targets:
                continue
            if (region, var_id) in transient_targets:
                continue
            collected = 0
            frontier_started = False
            for fh in sorted(set(fhs)):
                frame_exists = _frame_artifacts_exist(data_root, model_id, run_id, var_id, fh, region=region)
                if not frontier_started:
                    if frame_exists:
                        continue
                    frontier_started = True
                elif frame_exists:
                    break
                next_missing.append((region, var_id, fh))
                collected += 1
                if collected >= fh_lookahead:
                    break

        rebuild_round = False
        round_work: list[BuildTarget]
        if next_missing:
            round_work = list(next_missing)
        else:
            rebuild_candidates = _collect_slr_rebuild_candidates(
                data_root=data_root,
                model_id=model_id,
                run_id=run_id,
                targets=targets,
                attempts=rebuild_attempts,
                max_attempts=rebuild_max_attempts,
            )
            if not rebuild_candidates:
                break
            if _run_is_superseded(plugin=plugin, run_dt=run_dt):
                logger.info(
                    "Abandoning degraded rebuilds for superseded run=%s model=%s",
                    run_id,
                    model_id,
                )
                break

            ready_rebuilds: list[BuildTarget] = []
            for region, var_id, fh in rebuild_candidates:
                if not _kuchera_rebuild_profile_ready(
                    plugin=plugin,
                    model_id=model_id,
                    run_dt=run_dt,
                    var_id=var_id,
                    fh=fh,
                ):
                    continue
                key = (run_id, str(region), str(var_id), int(fh))
                rebuild_attempts[key] = int(rebuild_attempts.get(key, 0)) + 1
                ready_rebuilds.append((region, var_id, int(fh)))

            if not ready_rebuilds:
                break
            round_work = ready_rebuilds
            rebuild_round = True

        rounds += 1
        logger.info(
            "Run=%s model=%s coverage=%s targets=%d catchup_round=%d pending=%d blocked=%d rebuild_round=%s",
            run_id,
            model_id,
            regions_label,
            total,
            rounds,
            len(round_work),
            len(blocked_targets),
            rebuild_round,
        )

        round_successes = 0
        round_transient_failures = 0
        round_fetch_ctx_regions: set[str] = set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures: list[concurrent.futures.Future] = []
            if derive_bundle_enabled and not rebuild_round:
                bundle_by_region_fh: dict[tuple[str, int], list[str]] = {}
                single_jobs: list[BuildTarget] = []
                for region, var_id, fh in round_work:
                    if _is_derive_bundle_candidate(plugin, var_id):
                        bundle_by_region_fh.setdefault((region, int(fh)), []).append(var_id)
                        continue
                    single_jobs.append((region, var_id, int(fh)))

                for (region, fh), var_ids in sorted(bundle_by_region_fh.items(), key=lambda item: item[0]):
                    futures.append(
                        pool.submit(
                            _build_bundle,
                            model_id=model_id,
                            region=region,
                            var_ids=var_ids,
                            fh=fh,
                            run_dt=run_dt,
                            data_root=data_root,
                            plugin=plugin,
                        )
                    )

                for region, var_id, fh in single_jobs:
                    normalized_var_id = (
                        plugin.normalize_var_id(var_id)
                        if hasattr(plugin, "normalize_var_id")
                        else str(var_id)
                    )
                    fetch_ctx_key = (str(region), str(normalized_var_id))
                    shared_fetch_ctx = shared_parallel_fetch_ctx_by_target.setdefault(
                        fetch_ctx_key,
                        FetchContext(coverage=region),
                    )
                    shared_readiness_cache = shared_parallel_readiness_cache_by_target.setdefault(fetch_ctx_key, {})
                    round_fetch_ctx_regions.add(str(region))
                    futures.append(
                        pool.submit(
                            _build_one,
                            model_id=model_id,
                            region=region,
                            var_id=var_id,
                            fh=fh,
                            run_dt=run_dt,
                            data_root=data_root,
                            plugin=plugin,
                            fetch_ctx=shared_fetch_ctx,
                            readiness_cache=shared_readiness_cache,
                            log_fetch_cache_stats=False,
                            derive_component_warp_cache=True,
                        )
                    )
            else:
                if rebuild_round:
                    for region, var_id, fh in round_work:
                        normalized_var_id = (
                            plugin.normalize_var_id(var_id)
                            if hasattr(plugin, "normalize_var_id")
                            else str(var_id)
                        )
                        fetch_ctx_key = (str(region), str(normalized_var_id))
                        shared_fetch_ctx = shared_parallel_fetch_ctx_by_target.setdefault(
                            fetch_ctx_key,
                            FetchContext(coverage=region),
                        )
                        shared_readiness_cache = shared_parallel_readiness_cache_by_target.setdefault(fetch_ctx_key, {})
                        round_fetch_ctx_regions.add(str(region))
                        futures.append(
                            pool.submit(
                                _build_one,
                                model_id=model_id,
                                region=region,
                                var_id=var_id,
                                fh=fh,
                                run_dt=run_dt,
                                data_root=data_root,
                                plugin=plugin,
                                fetch_ctx=shared_fetch_ctx,
                                readiness_cache=shared_readiness_cache,
                                log_fetch_cache_stats=False,
                                derive_component_warp_cache=True,
                            )
                        )
                else:
                    queued_by_target: dict[tuple[str, str], list[int]] = {}
                    queue_order: list[tuple[str, str]] = []
                    future_to_target: dict[concurrent.futures.Future, tuple[str, str]] = {}

                    def _submit_single(region: str, var_id: str, fh: int) -> None:
                        normalized_var_id = (
                            plugin.normalize_var_id(var_id)
                            if hasattr(plugin, "normalize_var_id")
                            else str(var_id)
                        )
                        fetch_ctx_key = (str(region), str(normalized_var_id))
                        shared_fetch_ctx = shared_parallel_fetch_ctx_by_target.setdefault(
                            fetch_ctx_key,
                            FetchContext(coverage=region),
                        )
                        shared_readiness_cache = shared_parallel_readiness_cache_by_target.setdefault(fetch_ctx_key, {})
                        round_fetch_ctx_regions.add(str(region))
                        future = pool.submit(
                            _build_one,
                            model_id=model_id,
                            region=region,
                            var_id=var_id,
                            fh=fh,
                            run_dt=run_dt,
                            data_root=data_root,
                            plugin=plugin,
                            fetch_ctx=shared_fetch_ctx,
                            readiness_cache=shared_readiness_cache,
                            log_fetch_cache_stats=False,
                            derive_component_warp_cache=True,
                        )
                        future_to_target[future] = (str(region), str(var_id))

                    for region, var_id, fh in round_work:
                        queue_key = (str(region), str(var_id))
                        if queue_key not in queued_by_target:
                            queued_by_target[queue_key] = []
                            queue_order.append(queue_key)
                        queued_by_target[queue_key].append(int(fh))

                    for region, var_id in queue_order:
                        target_fhs = queued_by_target[(region, var_id)]
                        if target_fhs:
                            _submit_single(region, var_id, target_fhs.pop(0))

                    while future_to_target:
                        done, _pending = concurrent.futures.wait(
                            set(future_to_target),
                            return_when=concurrent.futures.FIRST_COMPLETED,
                        )
                        for future in done:
                            queue_key = future_to_target.pop(future)
                            future_result = future.result()
                            region, var_id, fh, ok, elapsed_ms, status = _coerce_build_outcome(tuple(future_result))
                            rebuild_key = (run_id, str(region), str(var_id), int(fh))
                            is_rebuild_job = rebuild_round and rebuild_key in rebuild_attempts
                            _log_frame_timing(
                                run_id=run_id,
                                model_id=model_id,
                                region=region,
                                var_id=str(var_id),
                                fh=int(fh),
                                ok=ok,
                                elapsed_ms=elapsed_ms,
                                mode="single",
                            )
                            if ok:
                                built_ok += 1
                                round_successes += 1
                                if is_rebuild_job:
                                    quality, quality_flags = _sidecar_quality(
                                        data_root,
                                        model_id,
                                        run_id,
                                        str(var_id),
                                        int(fh),
                                        region=region,
                                    )
                                    logger.info(
                                        "Rebuild success: %s %s/%s fh%03d quality=%s flags=%s attempt=%d/%d",
                                        run_id,
                                        region,
                                        var_id,
                                        fh,
                                        quality,
                                        quality_flags,
                                        int(rebuild_attempts.get(rebuild_key, 0)),
                                        rebuild_max_attempts,
                                    )
                                else:
                                    logger.info("Build success: %s %s/%s fh%03d", run_id, region, var_id, fh)
                            else:
                                if is_rebuild_job:
                                    logger.warning(
                                        "Rebuild skipped/failed: %s %s/%s fh%03d attempt=%d/%d",
                                        run_id,
                                        region,
                                        var_id,
                                        fh,
                                        int(rebuild_attempts.get(rebuild_key, 0)),
                                        rebuild_max_attempts,
                                    )
                                elif status == BUILD_STATUS_TRANSIENT:
                                    round_transient_failures += 1
                                    transient_targets.add((region, var_id))
                                    logger.warning("Build transiently unavailable: %s %s/%s fh%03d", run_id, region, var_id, fh)
                                else:
                                    blocked_targets.add((region, var_id))
                                    logger.warning("Build skipped/failed: %s %s/%s fh%03d", run_id, region, var_id, fh)

                            if queue_key in blocked_targets or queue_key in transient_targets:
                                continue
                            remaining_fhs = queued_by_target.get(queue_key, [])
                            if remaining_fhs:
                                _submit_single(queue_key[0], queue_key[1], remaining_fhs.pop(0))
                    futures = []

            for future in concurrent.futures.as_completed(futures):
                future_result = future.result()
                result_mode = "single" if isinstance(future_result, tuple) else "bundle"
                if isinstance(future_result, tuple):
                    round_results = [future_result]
                else:
                    round_results = list(future_result)

                for outcome in round_results:
                    region, var_id, fh, ok, elapsed_ms, status = _coerce_build_outcome(tuple(outcome))
                    rebuild_key = (run_id, str(region), str(var_id), int(fh))
                    is_rebuild_job = rebuild_round and rebuild_key in rebuild_attempts
                    _log_frame_timing(
                        run_id=run_id,
                        model_id=model_id,
                        region=region,
                        var_id=str(var_id),
                        fh=int(fh),
                        ok=ok,
                        elapsed_ms=elapsed_ms,
                        mode=result_mode,
                    )
                    if ok:
                        built_ok += 1
                        round_successes += 1
                        if is_rebuild_job:
                            quality, quality_flags = _sidecar_quality(
                                data_root,
                                model_id,
                                run_id,
                                str(var_id),
                                int(fh),
                                region=region,
                            )
                            logger.info(
                                "Rebuild success: %s %s/%s fh%03d quality=%s flags=%s attempt=%d/%d",
                                run_id,
                                region,
                                var_id,
                                fh,
                                quality,
                                quality_flags,
                                int(rebuild_attempts.get(rebuild_key, 0)),
                                rebuild_max_attempts,
                            )
                        else:
                            logger.info("Build success: %s %s/%s fh%03d", run_id, region, var_id, fh)
                    else:
                        if is_rebuild_job:
                            logger.warning(
                                "Rebuild skipped/failed: %s %s/%s fh%03d attempt=%d/%d",
                                run_id,
                                region,
                                var_id,
                                fh,
                                int(rebuild_attempts.get(rebuild_key, 0)),
                                rebuild_max_attempts,
                            )
                        elif status == BUILD_STATUS_TRANSIENT:
                            round_transient_failures += 1
                            transient_targets.add((region, var_id))
                            logger.warning("Build transiently unavailable: %s %s/%s fh%03d", run_id, region, var_id, fh)
                        else:
                            blocked_targets.add((region, var_id))
                            logger.warning("Build skipped/failed: %s %s/%s fh%03d", run_id, region, var_id, fh)

        _log_parallel_shared_fetch_cache(regions=round_fetch_ctx_regions)

        if round_successes == 0 and not rebuild_round:
            if round_transient_failures > 0:
                logger.info(
                    "Catch-up paused: run=%s no progress in round=%d; transient_unavailable=%d blocked_vars=%s transient_vars=%s",
                    run_id,
                    rounds,
                    round_transient_failures,
                    sorted(f"{region}/{var_id}" for region, var_id in blocked_targets),
                    sorted(f"{region}/{var_id}" for region, var_id in transient_targets),
                )
                break
            logger.info(
                "Catch-up paused: run=%s no progress in round=%d; blocked_vars=%s transient_vars=%s",
                run_id,
                rounds,
                sorted(f"{region}/{var_id}" for region, var_id in blocked_targets),
                sorted(f"{region}/{var_id}" for region, var_id in transient_targets),
            )
            break

        # Publish as soon as promotion criteria is met so "latest" can switch
        # before the full catch-up pass exits.
        if not published_once and _should_promote(data_root, model_id, run_id, primary_vars, promotion_fhs):
            _publish_run_snapshot(reason=f"catchup_round_{rounds}", pregenerate_loops=False)
            published_once = True
            built_ok_at_last_publish = built_ok
        elif (
            published_once
            and built_ok > built_ok_at_last_publish
            and (built_ok - built_ok_at_last_publish) >= progress_publish_min_new_frames
        ):
            _publish_run_snapshot(reason=f"catchup_progress_{rounds}", pregenerate_loops=False)
            built_ok_at_last_publish = built_ok

    available = _available_target_count(data_root, model_id, run_id, targets)
    if _should_promote(data_root, model_id, run_id, primary_vars, promotion_fhs):
        if (not published_once) or (available > built_ok_at_last_publish):
            _publish_run_snapshot(
                reason="catchup_complete",
                pregenerate_loops=available >= total,
            )
            published_once = True
            built_ok_at_last_publish = available

    effective_keep_runs = _resolved_keep_runs_for_scheduler_plugin(plugin, keep_runs)
    _enforce_run_retention(data_root / "staging" / model_id, effective_keep_runs)
    _enforce_run_retention(data_root / "published" / model_id, effective_keep_runs)
    herbie_save_dir_raw = _env_value(ENV_HERBIE_SAVE_DIR).strip()
    if herbie_save_dir_raw:
        herbie_model_id = model_id
        try:
            herbie_request = plugin.herbie_request(product=getattr(plugin, "product", None), run_date=run_dt)
            resolved_model = str(getattr(herbie_request, "model", "") or "").strip().lower()
            if resolved_model:
                herbie_model_id = resolved_model
        except Exception:
            logger.exception("Failed resolving Herbie cache model for retention: scheduler_model=%s", model_id)
        _enforce_herbie_cache_retention(Path(herbie_save_dir_raw).resolve(), herbie_model_id, effective_keep_runs)

    return run_id, available, total


def run_scheduler(
    *,
    model: str,
    vars_to_build: list[str],
    primary_vars: list[str],
    data_root: Path,
    workers: int,
    keep_runs: int,
    poll_seconds: int,
    run_arg: str | None,
    once: bool,
    probe_var: str | None,
    loop_pregenerate_enabled: bool,
    loop_cache_root: Path,
    loop_workers: int,
    loop_tier0_quality: int,
    loop_tier0_max_dim: int,
    loop_tier0_fixed_w: int,
    loop_tier1_quality: int,
    loop_tier1_max_dim: int,
    loop_tier1_fixed_w: int,
    rebuild_existing: bool,
) -> int:
    plugin = _resolve_model(model)
    default_region = _default_build_region(plugin)
    if plugin.get_region(default_region) is None:
        raise SchedulerConfigError(
            f"Model {model!r} does not define default build region {default_region!r}"
        )

    normalized_vars = _resolve_vars_to_schedule(plugin, vars_to_build)
    if not normalized_vars:
        raise SchedulerConfigError("No schedulable vars resolved")

    resolved_primary: list[str] = []
    for item in primary_vars:
        normalized = plugin.normalize_var_id(item)
        capability = plugin.get_var_capability(normalized)
        if capability is not None:
            if bool(getattr(capability, "buildable", False)):
                resolved_primary.append(normalized)
            continue
        if plugin.get_var(normalized) is not None:
            resolved_primary.append(normalized)
    resolved_primary = _dedupe_preserve_order(resolved_primary)
    if not resolved_primary:
        fallback = plugin.normalize_var_id(DEFAULT_PRIMARY_VAR)
        if plugin.get_var(fallback) is not None:
            resolved_primary = [fallback]
        else:
            resolved_primary = [normalized_vars[0]]

    resolved_probe_var = plugin.resolve_probe_var_key(probe_var)
    if resolved_probe_var is None:
        resolved_probe_var = plugin.resolve_probe_var_key(DEFAULT_PROBE_VAR)

    if rebuild_existing and workers > 1:
        logger.info(
            "Rebuild-existing mode throttling frame workers from %d to 1 to reduce ECMWF Kuchera memory and GDAL pressure",
            workers,
        )
        workers = 1

    logger.info(
        "Scheduler starting model=%s default_region=%s vars=%s primary=%s probe_var=%s data_root=%s workers=%d poll_incomplete=%ds poll_complete=%ds rebuild_existing=%s",
        model,
        default_region,
        normalized_vars,
        resolved_primary,
        resolved_probe_var or "none",
        data_root,
        workers,
        INCOMPLETE_RUN_POLL_SECONDS,
        poll_seconds,
        rebuild_existing,
    )

    if rebuild_existing and not (once or run_arg):
        raise SchedulerConfigError("--rebuild-existing requires --run or --once")

    with _scheduler_model_lock(data_root, model):
        last_run_id: str | None = None
        last_run_available: int = 0
        last_run_total: int = 0
        while True:
            run_dt = _resolve_run_dt(run_arg, plugin=plugin, probe_var=resolved_probe_var)
            run_id = _run_id_from_dt(run_dt)

            run_complete = last_run_total > 0 and last_run_available >= last_run_total
            if last_run_id == run_id and not run_arg and run_complete:
                logger.info("No new run yet (latest=%s complete); sleeping %ss", run_id, poll_seconds)
                time.sleep(poll_seconds)
                continue

            processed_run_id, available, total = _process_run(
                plugin=plugin,
                model_id=model,
                vars_to_build=normalized_vars,
                primary_vars=resolved_primary,
                run_dt=run_dt,
                data_root=data_root,
                workers=workers,
                keep_runs=keep_runs,
                loop_pregenerate_enabled=loop_pregenerate_enabled,
                loop_cache_root=loop_cache_root,
                loop_workers=loop_workers,
                loop_tier0_quality=loop_tier0_quality,
                loop_tier0_max_dim=loop_tier0_max_dim,
                loop_tier0_fixed_w=loop_tier0_fixed_w,
                loop_tier1_quality=loop_tier1_quality,
                loop_tier1_max_dim=loop_tier1_max_dim,
                loop_tier1_fixed_w=loop_tier1_fixed_w,
                rebuild_existing=rebuild_existing,
            )
            last_run_id = processed_run_id
            last_run_available = available
            last_run_total = total
            logger.info("Run summary: %s available=%d/%d", processed_run_id, available, total)

            if once or run_arg:
                return 0

            run_complete_now = total > 0 and available >= total
            next_poll_seconds = poll_seconds if run_complete_now else INCOMPLETE_RUN_POLL_SECONDS
            logger.info(
                "Next poll in %ss (run=%s complete=%s)",
                next_poll_seconds,
                processed_run_id,
                run_complete_now,
            )
            time.sleep(next_poll_seconds)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the CartoSky model scheduler.")
    parser.add_argument("--model", required=True, help="Model id (e.g. hrrr, nam, gfs)")
    parser.add_argument(
        "--vars",
        default=None,
        help="Comma-separated vars to build; omit or use 'auto' to build all model buildable vars",
    )
    parser.add_argument("--primary-vars", default=None, help="Comma-separated primary vars for promotion")
    parser.add_argument(
        "--data-root",
        default=None,
        help="Override CARTOSKY_DATA_ROOT (legacy CARTOSKY_V3_DATA_ROOT and TWF_V3_DATA_ROOT also supported)",
    )
    parser.add_argument("--workers", type=int, default=None, help="Parallel frame workers")
    parser.add_argument("--keep-runs", type=int, default=None, help="Retention count for staging/published runs")
    parser.add_argument("--poll-seconds", type=int, default=None, help="Poll interval in loop mode")
    parser.add_argument("--probe-var", default=None, help="Var key used to probe run availability")
    parser.add_argument("--run", default=None, help="Explicit run id YYYYMMDD_HHz; implies one-shot")
    parser.add_argument("--once", action="store_true", help="Build one cycle then exit")
    parser.add_argument(
        "--rebuild-existing",
        action="store_true",
        help="Rebuild existing frame artifacts for the selected run instead of only filling missing frames",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)

    data_root = _data_root(args.data_root)
    climatology.configure_data_root(data_root)
    workers = _workers(args.workers)
    vars_raw = args.vars if isinstance(args.vars, str) else _env_value(ENV_DEFAULT_VARS, DEFAULT_VARS)
    primary_raw = (
        args.primary_vars
        if isinstance(args.primary_vars, str) and args.primary_vars.strip()
        else _env_value(ENV_DEFAULT_PRIMARY_VARS, DEFAULT_PRIMARY_VAR)
    )
    poll_seconds = (
        int(args.poll_seconds)
        if args.poll_seconds is not None
        else _int_from_env(ENV_DEFAULT_POLL_SECONDS, DEFAULT_POLL_SECONDS, min_value=15)
    )
    keep_runs = (
        int(args.keep_runs)
        if args.keep_runs is not None
        else _int_from_env(ENV_DEFAULT_KEEP_RUNS, DEFAULT_KEEP_RUNS, min_value=1)
    )
    probe_var = None
    if isinstance(args.probe_var, str) and args.probe_var.strip():
        probe_var = args.probe_var
    else:
        probe_var_env = _env_value(ENV_PROBE_VAR).strip()
        if probe_var_env:
            probe_var = probe_var_env
    loop_pregenerate_enabled = _bool_from_env(ENV_LOOP_PREGENERATE_ENABLED, DEFAULT_LOOP_PREGENERATE_ENABLED)
    loop_cache_root = Path(_env_value(ENV_LOOP_CACHE_ROOT, str(DEFAULT_LOOP_CACHE_ROOT))).resolve()
    loop_workers = _int_from_env(
        ENV_LOOP_PREGENERATE_WORKERS,
        DEFAULT_LOOP_PREGENERATE_WORKERS,
        min_value=1,
    )
    loop_tier0_quality = _int_from_env(ENV_LOOP_WEBP_QUALITY, DEFAULT_LOOP_WEBP_QUALITY, min_value=1)
    loop_tier0_quality = max(1, min(100, loop_tier0_quality))
    loop_tier0_max_dim = _int_from_env(ENV_LOOP_WEBP_MAX_DIM, DEFAULT_LOOP_WEBP_MAX_DIM, min_value=64)
    loop_tier0_fixed_w = _int_from_env(
        ENV_LOOP_WEBP_TIER0_FIXED_W,
        DEFAULT_LOOP_WEBP_TIER0_FIXED_W,
        min_value=64,
    )
    loop_tier1_quality = _int_from_env(ENV_LOOP_WEBP_TIER1_QUALITY, DEFAULT_LOOP_WEBP_TIER1_QUALITY, min_value=1)
    loop_tier1_quality = max(1, min(100, loop_tier1_quality))
    loop_tier1_max_dim = _int_from_env(ENV_LOOP_WEBP_TIER1_MAX_DIM, DEFAULT_LOOP_WEBP_TIER1_MAX_DIM, min_value=64)
    loop_tier1_fixed_w = _int_from_env(
        ENV_LOOP_WEBP_TIER1_FIXED_W,
        DEFAULT_LOOP_WEBP_TIER1_FIXED_W,
        min_value=64,
    )

    vars_list = _parse_vars_or_auto(vars_raw)
    primary_list = _parse_vars(primary_raw)

    try:
        return run_scheduler(
            model=args.model.strip().lower(),
            vars_to_build=vars_list,
            primary_vars=primary_list,
            data_root=data_root,
            workers=workers,
            keep_runs=max(1, keep_runs),
            poll_seconds=max(15, poll_seconds),
            run_arg=args.run.strip().lower() if isinstance(args.run, str) and args.run.strip() else None,
            once=bool(args.once),
            probe_var=probe_var,
            loop_pregenerate_enabled=loop_pregenerate_enabled,
            loop_cache_root=loop_cache_root,
            loop_workers=loop_workers,
            loop_tier0_quality=loop_tier0_quality,
            loop_tier0_max_dim=loop_tier0_max_dim,
            loop_tier0_fixed_w=loop_tier0_fixed_w,
            loop_tier1_quality=loop_tier1_quality,
            loop_tier1_max_dim=loop_tier1_max_dim,
            loop_tier1_fixed_w=loop_tier1_fixed_w,
                rebuild_existing=bool(args.rebuild_existing),
        )
    except SchedulerConfigError as exc:
        logger.error("Scheduler configuration error: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.info("Scheduler shutdown requested")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
