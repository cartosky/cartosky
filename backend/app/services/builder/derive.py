"""Phase 2 derivation helpers for multi-component variables.

Builds derived fields directly from model component VarSpecs:
  - wspd10m: hypot(10u, 10v) converted to mph
  - radar_ptype_combo: indexed palette field from refc + categorical masks
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Literal, overload

import numpy as np
import rasterio
import rasterio.transform
import rasterio.crs
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject

from app.services.builder.cog_writer import compute_transform_and_shape, warp_to_target_grid
from app.services.builder.fetch import convert_units, fetch_variable, inventory_lines_for_pattern
from app.services.builder.fetch import HerbieTransientUnavailableError
from app.services.climatology import (
    DEFAULT_BASELINE_SOURCE,
    get_baseline_grid_params,
    get_baseline_target_grid,
    load_accumulation_climatology_baseline,
    load_climatology_baseline,
    normalize_baseline_source,
)
from app.services.colormaps import (
    RADAR_PTYPE_BREAKS,
    RADAR_PTYPE_ORDER,
    RADAR_PTYPE_LEVELS_BY_TYPE,
)
from app.services.process_memory import current_rss_bytes, peak_rss_bytes

logger = logging.getLogger(__name__)
_EARTH_RADIUS_M = np.float64(6_371_000.0)
_EARTH_ANGULAR_VELOCITY_RAD_S = np.float64(7.2921159e-5)
_MIN_COS_LAT = np.float64(1.0e-6)
_MISSING_CSNOW_SAMPLE_LOG_COUNT = 0
_MISSING_PTYPE_SAMPLE_LOG_COUNT = 0
_KUCHERA_PTYPE_GATE_WARN_INTERVAL_SECONDS = 60.0
_KUCHERA_PTYPE_GATE_LAST_WARN_TS = 0.0
_KUCHERA_PTYPE_GATE_WARN_LOCK = threading.Lock()
_KUCHERA_DEFAULT_LEVELS_HPA: tuple[int, ...] = (925, 850, 700, 600, 500)
_KUCHERA_VENDOR_T0_K = np.float32(271.16)
_KUCHERA_RATIO_CLAMP_MIN = np.float32(5.0)
_KUCHERA_RATIO_CLAMP_MAX = np.float32(30.0)
_KUCHERA_INCREMENTAL_WINDOW_DEFAULT = 6
_KUCHERA_SIMPLIFIED_PROFILE_MAX_LEVELS = 4
_KUCHERA_SFC_PRESSURE_MARGIN_PA_DEFAULT = np.float32(2500.0)
_KUCHERA_SURFACE_TEMP_CAP_COLD_F_DEFAULT = np.float32(30.0)
_KUCHERA_SURFACE_TEMP_CAP_WARM_F_DEFAULT = np.float32(34.0)
_KUCHERA_SURFACE_TEMP_CAP_COLD_RATIO_DEFAULT = np.float32(18.0)
_KUCHERA_SURFACE_TEMP_CAP_WARM_RATIO_DEFAULT = np.float32(10.0)
_APCP_ACCUM_HOUR_WINDOW_RE = re.compile(
    r":APCP:surface:(\d+)-(\d+)\s*hour acc(?:\s*fcst|@\([^)]*\))",
    re.IGNORECASE,
)
_APCP_ACCUM_DAY_WINDOW_RE = re.compile(
    r":APCP:surface:(\d+)-(\d+)\s*day acc(?:\s*fcst|@\([^)]*\))",
    re.IGNORECASE,
)


@dataclass
class FetchContext:
    fetch_cache: dict[
        tuple[str, str, str, int, str, str, str, str],
        tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine],
    ] = field(default_factory=dict)
    warp_cache: dict[
        tuple[str, str, str, int, str, str, str, str],
        tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine],
    ] = field(default_factory=dict)
    fetch_meta_cache: dict[
        tuple[str, str, str, int, str, str, str, str],
        dict[str, Any],
    ] = field(default_factory=dict)
    warp_meta_cache: dict[
        tuple[str, str, str, int, str, str, str, str],
        dict[str, Any],
    ] = field(default_factory=dict)
    resolved_apcp_cache: dict[
        tuple[str, str, str, int, str, str, int, str, str],
        tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]],
    ] = field(default_factory=dict)
    ptype_family_cache: dict[
        tuple[str, str, str, int, str, str, str],
        dict[str, Any],
    ] = field(default_factory=dict)
    derive_quality: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=lambda: {"hits": 0, "misses": 0})
    warp_stats: dict[str, int] = field(default_factory=lambda: {"hits": 0, "misses": 0})
    coverage: str | None = None
    bundle_fetch_cache: Any | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


def _bytes_to_mib(num_bytes: int) -> float:
    return float(num_bytes) / (1024.0 * 1024.0)


def _estimate_array_bytes(value: Any, *, _seen: set[int] | None = None) -> int:
    if _seen is None:
        _seen = set()
    obj_id = id(value)
    if obj_id in _seen:
        return 0
    if isinstance(value, np.ndarray):
        _seen.add(obj_id)
        return int(value.nbytes)
    if isinstance(value, dict):
        _seen.add(obj_id)
        return sum(_estimate_array_bytes(item, _seen=_seen) for item in value.values())
    if isinstance(value, (tuple, list)):
        _seen.add(obj_id)
        return sum(_estimate_array_bytes(item, _seen=_seen) for item in value)
    return 0


def _fetch_context_array_stats(ctx: FetchContext | None) -> dict[str, int]:
    if ctx is None:
        return {
            "fetch": 0,
            "fetch_bytes": 0,
            "warp": 0,
            "warp_bytes": 0,
            "resolved_apcp": 0,
            "resolved_apcp_bytes": 0,
            "ptype": 0,
            "ptype_bytes": 0,
            "kuchera": 0,
            "kuchera_bytes": 0,
        }
    kuchera_cache = getattr(ctx, "kuchera_cumulative_cache", None)
    kuchera_count = len(kuchera_cache) if isinstance(kuchera_cache, dict) else 0
    kuchera_bytes = _estimate_array_bytes(kuchera_cache) if kuchera_count else 0
    return {
        "fetch": len(ctx.fetch_cache),
        "fetch_bytes": _estimate_array_bytes(ctx.fetch_cache),
        "warp": len(ctx.warp_cache),
        "warp_bytes": _estimate_array_bytes(ctx.warp_cache),
        "resolved_apcp": len(ctx.resolved_apcp_cache),
        "resolved_apcp_bytes": _estimate_array_bytes(ctx.resolved_apcp_cache),
        "ptype": len(ctx.ptype_family_cache),
        "ptype_bytes": _estimate_array_bytes(ctx.ptype_family_cache),
        "kuchera": kuchera_count,
        "kuchera_bytes": kuchera_bytes,
    }


def _log_fetch_context_memory(
    *,
    label: str,
    ctx: FetchContext | None,
    model_id: str | None = None,
    var_key: str | None = None,
    fh: int | None = None,
    step_fh: int | None = None,
    extra: str | None = None,
) -> None:
    stats = _fetch_context_array_stats(ctx)
    logger.info(
        "fetch_ctx_memory label=%s model=%s var=%s fh=%s step_fh=%s fetch=%d fetch_mib=%.1f warp=%d warp_mib=%.1f resolved_apcp=%d resolved_apcp_mib=%.1f ptype=%d ptype_mib=%.1f kuchera=%d kuchera_mib=%.1f current_rss_mib=%.1f peak_rss_mib=%.1f%s",
        label,
        model_id or "-",
        var_key or "-",
        f"{fh:03d}" if fh is not None else "-",
        f"{step_fh:03d}" if step_fh is not None else "-",
        stats["fetch"],
        _bytes_to_mib(stats["fetch_bytes"]),
        stats["warp"],
        _bytes_to_mib(stats["warp_bytes"]),
        stats["resolved_apcp"],
        _bytes_to_mib(stats["resolved_apcp_bytes"]),
        stats["ptype"],
        _bytes_to_mib(stats["ptype_bytes"]),
        stats["kuchera"],
        _bytes_to_mib(stats["kuchera_bytes"]),
        _bytes_to_mib(current_rss_bytes()),
        _bytes_to_mib(peak_rss_bytes()),
        f" {extra}" if extra else "",
    )


def _prune_cache_dict_by_forecast_hours(cache: dict[Any, Any], *, keep_fhs: set[int]) -> int:
    removed = 0
    for key in list(cache.keys()):
        if not isinstance(key, tuple) or len(key) < 4:
            continue
        try:
            cache_fh = int(key[3])
        except (TypeError, ValueError):
            continue
        if cache_fh in keep_fhs:
            continue
        cache.pop(key, None)
        removed += 1
    return removed


def _prune_kuchera_cumulative_cache(cache: dict[Any, Any], *, keep_fhs: set[int]) -> int:
    removed = 0
    for key in list(cache.keys()):
        if not isinstance(key, tuple) or len(key) < 4:
            continue
        try:
            cache_fh = int(key[3])
        except (TypeError, ValueError):
            continue
        if cache_fh in keep_fhs:
            continue
        cache.pop(key, None)
        removed += 1
    return removed


def prune_fetch_context_after_frame(
    *,
    ctx: FetchContext | None,
    var_spec_model: Any,
    fh: int,
) -> dict[str, int]:
    if ctx is None:
        return {}
    derive_kind = str(getattr(var_spec_model, "derive", "") or "").strip().lower()
    handled_derive_kinds = {
        "snowfall_kuchera_total_cumulative",
        "ptype_accumulation_ecmwf",
        "ptype_accumulation_cumulative",
        "ptype_intensity_ecmwf",
        "ptype_intensity_gfs",
        "radar_ptype_combo",
    }
    if derive_kind not in handled_derive_kinds:
        return {}

    keep_fhs = {int(fh)}
    removed_fetch = _prune_cache_dict_by_forecast_hours(ctx.fetch_cache, keep_fhs=keep_fhs)
    removed_fetch_meta = _prune_cache_dict_by_forecast_hours(ctx.fetch_meta_cache, keep_fhs=keep_fhs)
    removed_warp = _prune_cache_dict_by_forecast_hours(ctx.warp_cache, keep_fhs=keep_fhs)
    removed_warp_meta = _prune_cache_dict_by_forecast_hours(ctx.warp_meta_cache, keep_fhs=keep_fhs)
    removed_resolved_apcp = _prune_cache_dict_by_forecast_hours(ctx.resolved_apcp_cache, keep_fhs=keep_fhs)
    removed_ptype = _prune_cache_dict_by_forecast_hours(ctx.ptype_family_cache, keep_fhs=keep_fhs)
    kuchera_cache = getattr(ctx, "kuchera_cumulative_cache", None)
    removed_kuchera = 0
    if isinstance(kuchera_cache, dict):
        removed_kuchera = _prune_kuchera_cumulative_cache(kuchera_cache, keep_fhs=keep_fhs)

    return {
        "fetch": removed_fetch,
        "fetch_meta": removed_fetch_meta,
        "warp": removed_warp,
        "warp_meta": removed_warp_meta,
        "resolved_apcp": removed_resolved_apcp,
        "ptype": removed_ptype,
        "kuchera": removed_kuchera,
    }


def destroy_fetch_context(ctx: FetchContext | None) -> None:
    if ctx is None:
        return
    ctx.fetch_cache.clear()
    ctx.warp_cache.clear()
    ctx.fetch_meta_cache.clear()
    ctx.warp_meta_cache.clear()
    ctx.resolved_apcp_cache.clear()
    ctx.ptype_family_cache.clear()
    ctx.derive_quality.clear()
    ctx.stats.clear()
    ctx.warp_stats.clear()
    kuchera_cache = getattr(ctx, "kuchera_cumulative_cache", None)
    if isinstance(kuchera_cache, dict):
        kuchera_cache.clear()
    bundle_fetch_cache = getattr(ctx, "bundle_fetch_cache", None)
    if hasattr(bundle_fetch_cache, "clear"):
        try:
            bundle_fetch_cache.clear()
        except Exception:
            pass
    ctx.bundle_fetch_cache = None


@dataclass(frozen=True)
class DeriveStrategy:
    id: str
    required_inputs: tuple[str, ...]
    output_var_key: str | None
    execute: Callable[..., tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]]


@dataclass(frozen=True)
class _ConversionCapabilityOverride:
    conversion: str


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


def _parse_hint_int(value: Any, *, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(minimum), int(parsed))


def _is_apcp_incremental_rebuild_retryable_error(exc: BaseException) -> bool:
    message = str(exc)
    return isinstance(exc, ValueError) and "APCP_STEP_RESOLUTION" in message


def _parse_kuchera_levels_hpa(value: Any) -> list[int]:
    if isinstance(value, (list, tuple, set)):
        tokens = list(value)
    elif value is None:
        tokens = list(_KUCHERA_DEFAULT_LEVELS_HPA)
    else:
        raw = str(value).replace(";", ",")
        tokens = [token.strip() for token in raw.split(",") if token.strip()]

    levels: list[int] = []
    for token in tokens:
        try:
            parsed = int(token)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed in levels:
            continue
        levels.append(parsed)

    if not levels:
        levels = list(_KUCHERA_DEFAULT_LEVELS_HPA)
    return levels


def _kuchera_maxt_low500_from_temp_stack_k(temp_stack_c: list[np.ndarray]) -> np.ndarray:
    if not temp_stack_c:
        raise ValueError("kuchera requires at least one temperature level")

    shape = temp_stack_c[0].shape
    for layer in temp_stack_c[1:]:
        if layer.shape != shape:
            raise ValueError(f"kuchera temperature shape mismatch: {layer.shape} != {shape}")

    max_temp_k = np.full(shape, -np.inf, dtype=np.float32)
    any_valid = np.zeros(shape, dtype=bool)
    for temp_layer_c in temp_stack_c:
        temp_layer_k = (temp_layer_c.astype(np.float32, copy=False) + np.float32(273.15)).astype(np.float32, copy=False)
        finite = np.isfinite(temp_layer_k)
        any_valid = any_valid | finite
        max_temp_k = np.maximum(
            max_temp_k,
            np.where(finite, temp_layer_k, -np.inf).astype(np.float32, copy=False),
        )

    return np.where(any_valid, max_temp_k, np.nan).astype(np.float32, copy=False)


def _kuchera_ratio_from_maxt_low500_k(max_temp_k: np.ndarray) -> np.ndarray:
    ratio = np.full(max_temp_k.shape, np.nan, dtype=np.float32)
    finite = np.isfinite(max_temp_k)
    if not np.any(finite):
        return ratio

    warm_branch = finite & (max_temp_k > _KUCHERA_VENDOR_T0_K)
    if np.any(warm_branch):
        ratio[warm_branch] = 12.0 + 2.0 * (_KUCHERA_VENDOR_T0_K - max_temp_k[warm_branch])

    cold_branch = finite & ~warm_branch
    if np.any(cold_branch):
        ratio[cold_branch] = 12.0 + 1.0 * (_KUCHERA_VENDOR_T0_K - max_temp_k[cold_branch])

    return np.clip(ratio, _KUCHERA_RATIO_CLAMP_MIN, _KUCHERA_RATIO_CLAMP_MAX).astype(np.float32, copy=False)


def _compute_kuchera_slr(
    *,
    levels_hpa: list[int],
    temp_stack_c: list[np.ndarray],
) -> np.ndarray:
    if len(temp_stack_c) != len(levels_hpa):
        raise ValueError("kuchera temperature level count mismatch")
    max_temp_k = _kuchera_maxt_low500_from_temp_stack_k(temp_stack_c)
    ratio = _kuchera_ratio_from_maxt_low500_k(max_temp_k)
    return np.where(np.isfinite(ratio), ratio, 10.0).astype(np.float32, copy=False)


def _apply_kuchera_surface_temp_slr_cap(
    step_slr: np.ndarray,
    surface_temp_c: np.ndarray,
    *,
    cold_threshold_f: float,
    warm_threshold_f: float,
    cold_cap_ratio: float,
    warm_cap_ratio: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if step_slr.shape != surface_temp_c.shape:
        raise ValueError(
            f"kuchera surface-temp cap shape mismatch: {step_slr.shape} != {surface_temp_c.shape}"
        )

    slr = np.asarray(step_slr, dtype=np.float32)
    temp_c = np.asarray(surface_temp_c, dtype=np.float32)
    temp_f = (temp_c * np.float32(9.0 / 5.0) + np.float32(32.0)).astype(np.float32, copy=False)

    cold_f = np.float32(cold_threshold_f)
    warm_f = np.float32(max(float(warm_threshold_f), float(cold_f)))
    cold_ratio = np.float32(cold_cap_ratio)
    warm_ratio = np.float32(min(float(warm_cap_ratio), float(cold_ratio)))

    cap_ratio = np.full(slr.shape, np.nan, dtype=np.float32)
    finite_temp = np.isfinite(temp_f)
    warm_zone = finite_temp & (temp_f >= warm_f)
    if np.any(warm_zone):
        cap_ratio[warm_zone] = warm_ratio

    if warm_f > cold_f:
        taper_zone = finite_temp & (temp_f > cold_f) & (temp_f < warm_f)
        if np.any(taper_zone):
            fraction = (temp_f[taper_zone] - cold_f) / np.float32(warm_f - cold_f)
            cap_ratio[taper_zone] = cold_ratio + (warm_ratio - cold_ratio) * fraction
    else:
        cap_ratio[finite_temp & (temp_f > cold_f)] = warm_ratio

    capped = slr.copy()
    applied_mask = np.isfinite(slr) & np.isfinite(cap_ratio) & (slr > cap_ratio)
    if np.any(applied_mask):
        capped[applied_mask] = cap_ratio[applied_mask]
    return capped.astype(np.float32, copy=False), applied_mask, cap_ratio


def _run_id_from_date(run_date: datetime) -> str:
    run_date_utc = run_date.astimezone(timezone.utc) if run_date.tzinfo else run_date.replace(tzinfo=timezone.utc)
    return run_date_utc.strftime("%Y%m%d_%Hz")


def _kuchera_select_profile_levels(levels_hpa: list[int], *, simplified: bool) -> list[int]:
    """Select Kuchera profile levels deterministically for operational mode.

    In simplified mode we cap profile fetches to a small fixed set (prefer
    925/850/700/600 hPa) to keep per-frame cost low while retaining a stable
    warm-layer estimate.
    """
    if not levels_hpa:
        return []
    if not simplified:
        return list(levels_hpa)
    preferred_order = (925, 850, 700, 600, 500)
    selected: list[int] = []
    for level in preferred_order:
        if level in levels_hpa and level not in selected:
            selected.append(level)
        if len(selected) >= _KUCHERA_SIMPLIFIED_PROFILE_MAX_LEVELS:
            break
    if len(selected) < _KUCHERA_SIMPLIFIED_PROFILE_MAX_LEVELS:
        for level in levels_hpa:
            if level not in selected:
                selected.append(level)
            if len(selected) >= _KUCHERA_SIMPLIFIED_PROFILE_MAX_LEVELS:
                break
    return selected


def _cumulative_cache_grid_key(
    *,
    use_warped: bool,
    target_grid_id: str,
    resampling: str,
    cache_version: str | None = None,
) -> str:
    if use_warped:
        base_key = f"warped:{str(target_grid_id).strip()}:{str(resampling).strip()}"
    else:
        base_key = "native"
    resolved_cache_version = str(cache_version or "").strip()
    if resolved_cache_version:
        return f"{base_key}:v={resolved_cache_version}"
    return base_key


def _kuchera_cumulative_cache_file_path(
    *,
    data_root: Path,
    model_id: str,
    run_id: str,
    var_key: str,
    fh: int,
    root_name: str,
) -> Path:
    return (
        data_root
        / root_name
        / str(model_id)
        / str(run_id)
        / str(var_key)
        / f"fh{int(fh):03d}.cumulative-cache.npz"
    )


def _affine_to_cache_values(transform: rasterio.transform.Affine) -> np.ndarray:
    return np.asarray(
        (
            float(transform.a),
            float(transform.b),
            float(transform.c),
            float(transform.d),
            float(transform.e),
            float(transform.f),
        ),
        dtype=np.float64,
    )


def _affine_from_cache_values(values: Any) -> rasterio.transform.Affine | None:
    transform_values = np.asarray(values, dtype=np.float64).reshape(-1)
    if transform_values.size == 9:
        transform_values = transform_values[:6]
    if transform_values.size != 6:
        return None
    return rasterio.transform.Affine(*transform_values.tolist())


def _unpack_kuchera_cumulative_cache_entry(
    cached: Any,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]] | None:
    if not isinstance(cached, tuple):
        return None
    if len(cached) == 3:
        data, crs, transform = cached
        return np.asarray(data, dtype=np.float32), crs, transform, {}
    if len(cached) == 4:
        data, crs, transform, metadata = cached
        safe_metadata = metadata if isinstance(metadata, dict) else {}
        return np.asarray(data, dtype=np.float32), crs, transform, safe_metadata
    return None


def _kuchera_cache_has_full_run_coverage(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    coverage_start_fh = metadata.get("coverage_start_fh")
    try:
        return int(coverage_start_fh) == 0
    except (TypeError, ValueError):
        return False


def _kuchera_load_prior_cumulative(
    *,
    model_id: str,
    run_date: datetime,
    var_key: str,
    fh: int,
    ctx: FetchContext | None,
    grid_cache_key: str,
    scale_divisor: float = 0.03937007874015748,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]] | None:
    if fh <= 0:
        return None
    del scale_divisor
    run_id = _run_id_from_date(run_date)
    cache_key = (str(model_id), str(run_id), str(var_key), int(fh), str(grid_cache_key))
    if ctx is not None:
        cache = getattr(ctx, "kuchera_cumulative_cache", None)
        if isinstance(cache, dict) and cache_key in cache:
            return _unpack_kuchera_cumulative_cache_entry(cache[cache_key])

    data_root_raw = getattr(ctx, "data_root", None) if ctx is not None else None
    if data_root_raw is None:
        data_root_raw = (
            os.getenv("CARTOSKY_DATA_ROOT")
            or os.getenv("CARTOSKY_V3_DATA_ROOT")
            or os.getenv("TWF_V3_DATA_ROOT", "./data")
        )
    try:
        data_root = Path(str(data_root_raw))
    except Exception:
        return None

    candidate_paths = [
        _kuchera_cumulative_cache_file_path(
            data_root=data_root,
            model_id=model_id,
            run_id=run_id,
            var_key=var_key,
            fh=fh,
            root_name="staging",
        ),
        _kuchera_cumulative_cache_file_path(
            data_root=data_root,
            model_id=model_id,
            run_id=run_id,
            var_key=var_key,
            fh=fh,
            root_name="published",
        ),
    ]
    for candidate in candidate_paths:
        try:
            if not candidate.exists():
                continue
            with np.load(candidate, allow_pickle=False) as cached_npz:
                if str(cached_npz["grid_cache_key"].tolist()) != str(grid_cache_key):
                    continue
                loaded_data = np.asarray(cached_npz["data"], dtype=np.float32)
                loaded_transform = _affine_from_cache_values(cached_npz["transform"])
                if loaded_transform is None:
                    continue
                crs_wkt = str(cached_npz["crs_wkt"].tolist()).strip()
                if not crs_wkt:
                    continue
                loaded_crs = rasterio.crs.CRS.from_wkt(crs_wkt)
                loaded_metadata = {}
                if "coverage_start_fh" in cached_npz.files:
                    try:
                        loaded_metadata["coverage_start_fh"] = int(cached_npz["coverage_start_fh"].tolist())
                    except (TypeError, ValueError):
                        loaded_metadata["coverage_start_fh"] = None
            loaded = (loaded_data, loaded_crs, loaded_transform, loaded_metadata)
            if ctx is not None:
                cache = getattr(ctx, "kuchera_cumulative_cache", None)
                if not isinstance(cache, dict):
                    cache = {}
                    setattr(ctx, "kuchera_cumulative_cache", cache)
                cache[cache_key] = loaded
            return loaded
        except Exception:
            continue
    return None


def _kuchera_store_cumulative_cache(
    *,
    model_id: str,
    run_date: datetime,
    var_key: str,
    fh: int,
    data: np.ndarray,
    crs: rasterio.crs.CRS,
    transform: rasterio.transform.Affine,
    ctx: FetchContext | None,
    grid_cache_key: str,
    coverage_start_fh: int = 0,
) -> None:
    run_id = _run_id_from_date(run_date)
    cache_key = (str(model_id), run_id, str(var_key), int(fh), str(grid_cache_key))
    cache_metadata = {"coverage_start_fh": int(coverage_start_fh)}
    if ctx is not None:
        cache = getattr(ctx, "kuchera_cumulative_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(ctx, "kuchera_cumulative_cache", cache)
        cache[cache_key] = (data.astype(np.float32, copy=False), crs, transform, cache_metadata)

    data_root_raw = getattr(ctx, "data_root", None) if ctx is not None else None
    if data_root_raw is None:
        data_root_raw = (
            os.getenv("CARTOSKY_DATA_ROOT")
            or os.getenv("CARTOSKY_V3_DATA_ROOT")
            or os.getenv("TWF_V3_DATA_ROOT", "./data")
        )
    try:
        data_root = Path(str(data_root_raw))
    except Exception:
        return

    cache_path = _kuchera_cumulative_cache_file_path(
        data_root=data_root,
        model_id=model_id,
        run_id=run_id,
        var_key=var_key,
        fh=fh,
        root_name="staging",
    )
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_name(f"{cache_path.stem}.tmp{cache_path.suffix}")
        np.savez_compressed(
            tmp_path,
            data=np.asarray(data, dtype=np.float32),
            crs_wkt=crs.to_wkt(),
            transform=_affine_to_cache_values(transform),
            grid_cache_key=str(grid_cache_key),
            coverage_start_fh=np.int32(coverage_start_fh),
        )
        tmp_path.replace(cache_path)
    except Exception:
        logger.debug(
            "Failed to persist cumulative cache file model=%s run=%s var=%s fh=%03d",
            model_id,
            run_id,
            var_key,
            fh,
            exc_info=True,
        )


def derive_variable(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None = None,
    model_plugin: Any,
    fetch_ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    """Compute a derived variable field and return source grid metadata."""
    derive_kind = (
        getattr(var_capability, "derive_strategy_id", None)
        or getattr(var_spec_model, "derive", None)
    )
    strategy = DERIVE_STRATEGIES.get(str(derive_kind))
    if strategy is None:
        raise ValueError(f"Unsupported derive strategy: {derive_kind!r}")
    return strategy.execute(
        model_id=model_id,
        var_key=var_key,
        product=product,
        run_date=run_date,
        fh=fh,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
        model_plugin=model_plugin,
        ctx=fetch_ctx,
        derive_component_target_grid=derive_component_target_grid,
        derive_component_resampling=derive_component_resampling,
    )


def _resolve_component_var(model_plugin: Any, var_key: str) -> tuple[str, Any]:
    normalized_key = model_plugin.normalize_var_id(var_key)
    capability = model_plugin.get_var_capability(normalized_key)
    spec = model_plugin.get_var(normalized_key)
    if capability is None and spec is None:
        raise ValueError(
            f"Component var {normalized_key!r} not found in plugin "
            f"{getattr(model_plugin, 'id', '?')!r}"
        )
    selectors = (
        getattr(capability, "selectors", None)
        if capability is not None
        else getattr(spec, "selectors", None)
    )
    if selectors is None or not getattr(selectors, "search", None):
        raise ValueError(f"Component var {normalized_key!r} has no search patterns")
    return normalized_key, selectors


def _selector_fingerprint(selectors: Any) -> str:
    search = tuple(
        " ".join(str(pattern).split())
        for pattern in getattr(selectors, "search", [])
        if str(pattern).strip()
    )
    filter_by_keys = tuple(
        sorted(
            (str(key), str(value))
            for key, value in dict(getattr(selectors, "filter_by_keys", {}) or {}).items()
        )
    )
    hints = tuple(
        sorted(
            (str(key), str(value))
            for key, value in dict(getattr(selectors, "hints", {}) or {}).items()
        )
    )
    return repr((search, filter_by_keys, hints))


def _record_fetch_stat(ctx: FetchContext | None, metric: str) -> None:
    if ctx is None:
        return
    with ctx._lock:
        ctx.stats[metric] = int(ctx.stats.get(metric, 0)) + 1


def _record_warp_stat(ctx: FetchContext | None, metric: str) -> None:
    if ctx is None:
        return
    with ctx._lock:
        ctx.warp_stats[metric] = int(ctx.warp_stats.get(metric, 0)) + 1


def _record_derive_quality(
    ctx: FetchContext | None,
    *,
    var_key: str,
    fh: int,
    quality_flags: list[str],
) -> None:
    if ctx is None:
        return
    deduped_flags = [
        flag for flag in dict.fromkeys(str(item).strip() for item in quality_flags)
        if flag
    ]
    payload = {
        "quality": "degraded" if deduped_flags else "full",
        "quality_flags": deduped_flags,
    }
    with ctx._lock:
        ctx.derive_quality[(str(var_key), int(fh))] = payload


def _record_derive_sidecar_metadata(
    ctx: FetchContext | None,
    *,
    var_key: str,
    fh: int,
    sidecar_metadata: dict[str, Any],
) -> None:
    if ctx is None:
        return
    normalized: dict[str, Any] = {}
    for key, value in sidecar_metadata.items():
        normalized_key = str(key).strip()
        if normalized_key and value is not None:
            normalized[normalized_key] = value
    if not normalized:
        return
    with ctx._lock:
        payload = dict(ctx.derive_quality.get((str(var_key), int(fh)), {}))
        existing = payload.get("sidecar_metadata")
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(normalized)
        payload["sidecar_metadata"] = merged
        payload.setdefault("quality", "full")
        payload.setdefault("quality_flags", [])
        ctx.derive_quality[(str(var_key), int(fh))] = payload


def _canonical_region_for_plugin(model_plugin: Any) -> str:
    capabilities = getattr(model_plugin, "capabilities", None)
    region = getattr(capabilities, "canonical_region", None)
    normalized = str(region or "").strip().lower()
    return normalized or "conus"


def _derive_anomaly_departure(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_capability
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {}) or {}
    base_component = str(hints.get("base_component") or "").strip() or "tmp2m"
    base_conversion = str(hints.get("base_conversion") or "").strip()
    anomaly_conversion = str(hints.get("anomaly_conversion") or "").strip()
    baseline_field = str(hints.get("baseline_field") or "").strip() or base_component.split("__", 1)[0]
    baseline_source = normalize_baseline_source(
        str(hints.get("baseline_source") or DEFAULT_BASELINE_SOURCE).strip()
        or DEFAULT_BASELINE_SOURCE
    )
    legacy_baseline_model_family = str(
        hints.get("legacy_baseline_model_family") or hints.get("baseline_model_family") or ""
    ).strip().lower()
    baseline_region = str(hints.get("baseline_region") or "").strip().lower()
    baseline_version = str(hints.get("baseline_version") or "v1").strip() or "v1"
    reference_period = str(hints.get("reference_period") or "1991-2020").strip() or "1991-2020"

    if not baseline_region:
        baseline_region = str((derive_component_target_grid or {}).get("region", "")).strip().lower()
    if not baseline_region:
        baseline_region = _canonical_region_for_plugin(model_plugin)
    baseline_target_grid = get_baseline_target_grid(
        baseline_source=baseline_source,
        region=baseline_region,
    )
    target_region = str((derive_component_target_grid or {}).get("region", "")).strip().lower() or baseline_region
    target_grid_id = str((derive_component_target_grid or {}).get("id", "")).strip()
    if not target_grid_id:
        target_grid_id = str(baseline_target_grid.get("id", "")).strip() or f"{model_id}:{target_region}"
    resampling = str(derive_component_resampling or "bilinear").strip() or "bilinear"

    forecast_raw, src_crs, src_transform = _fetch_component_warped(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        model_plugin=model_plugin,
        var_key=base_component,
        target_region=target_region,
        target_grid_id=target_grid_id,
        resampling=resampling,
        ctx=ctx,
    )
    base_capability = model_plugin.get_var_capability(model_plugin.normalize_var_id(base_component))
    if base_conversion:
        base_capability = _ConversionCapabilityOverride(conversion=base_conversion)
    forecast_values = convert_units(
        forecast_raw,
        base_component,
        model_id=model_id,
        var_capability=base_capability,
    ).astype(np.float32, copy=False)

    valid_time = (run_date + timedelta(hours=fh)).astimezone(timezone.utc)
    baseline_values, baseline_crs, baseline_transform, baseline_meta = load_climatology_baseline(
        version=baseline_version,
        baseline_source=baseline_source,
        field=baseline_field,
        valid_time=valid_time,
        region=baseline_region,
        reference_period=reference_period,
        legacy_model_family_fallback=legacy_baseline_model_family or None,
    )
    if forecast_values.shape != baseline_values.shape:
        raise ValueError(
            f"Anomaly baseline shape mismatch: forecast={forecast_values.shape} baseline={baseline_values.shape}"
        )
    if src_crs != baseline_crs:
        raise ValueError(
            f"Anomaly baseline CRS mismatch: forecast={src_crs} baseline={baseline_crs}"
        )
    if any(
        abs(float(actual) - float(expected)) > 1.0e-6
        for actual, expected in zip(src_transform[:6], baseline_transform[:6])
    ):
        raise ValueError(
            f"Anomaly baseline transform mismatch: forecast={src_transform} baseline={baseline_transform}"
        )

    anomaly = (forecast_values - baseline_values).astype(np.float32, copy=False)
    if anomaly_conversion:
        anomaly = convert_units(
            anomaly,
            var_key,
            model_id=model_id,
            var_capability=_ConversionCapabilityOverride(conversion=anomaly_conversion),
        ).astype(np.float32, copy=False)
    _record_derive_sidecar_metadata(
        ctx,
        var_key=var_key,
        fh=fh,
        sidecar_metadata={
            "anomaly_kind": "departure",
            "baseline_region": baseline_region,
            **baseline_meta,
        },
    )
    return anomaly, baseline_crs, baseline_transform


def _derive_precip_accum_anomaly_departure(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {}) or {}
    base_component = str(hints.get("base_component") or "precip_total").strip() or "precip_total"
    baseline_field = str(hints.get("baseline_field") or var_key.removesuffix("_anom")).strip().lower()
    baseline_source = normalize_baseline_source(
        str(hints.get("baseline_source") or DEFAULT_BASELINE_SOURCE).strip()
        or DEFAULT_BASELINE_SOURCE
    )
    baseline_region = str(hints.get("baseline_region") or "").strip().lower()
    baseline_version = str(hints.get("baseline_version") or "v1").strip() or "v1"
    reference_period = str(hints.get("reference_period") or "1991-2020").strip() or "1991-2020"
    target_fh_raw = str(hints.get("target_fh") or "").strip()
    try:
        target_fh = int(target_fh_raw) if target_fh_raw else int(fh)
    except ValueError:
        target_fh = int(fh)
    window_hours_raw = str(hints.get("accumulation_window_hours") or "").strip()
    try:
        accumulation_window_hours = int(window_hours_raw) if window_hours_raw else 0
    except ValueError:
        accumulation_window_hours = 0
    if accumulation_window_hours <= 0:
        match = re.match(r"^precip_(\d+)d$", baseline_field)
        if match:
            accumulation_window_hours = int(match.group(1)) * 24
    if accumulation_window_hours <= 0:
        accumulation_window_hours = target_fh
    window_start_fh = target_fh - accumulation_window_hours
    if window_start_fh < 0:
        raise ValueError(
            f"Precip anomaly target fh{target_fh:03d} is shorter than accumulation window "
            f"{accumulation_window_hours}h for {var_key}"
        )

    if not baseline_region:
        baseline_region = str((derive_component_target_grid or {}).get("region", "")).strip().lower()
    if not baseline_region:
        baseline_region = _canonical_region_for_plugin(model_plugin)
    baseline_target_grid = get_baseline_target_grid(
        baseline_source=baseline_source,
        region=baseline_region,
    )
    target_region = str((derive_component_target_grid or {}).get("region", "")).strip().lower() or baseline_region
    target_grid_id = str((derive_component_target_grid or {}).get("id", "")).strip()
    if not target_grid_id:
        target_grid_id = str(baseline_target_grid.get("id", "")).strip() or f"{model_id}:{target_region}"
    resampling = str(derive_component_resampling or "bilinear").strip() or "bilinear"

    normalized_base_component = model_plugin.normalize_var_id(base_component)
    base_spec = model_plugin.get_var(normalized_base_component)
    base_capability = model_plugin.get_var_capability(normalized_base_component)
    if base_spec is None and base_capability is None:
        raise ValueError(f"Precip anomaly base component not found: {normalized_base_component!r}")

    base_is_derived = bool(
        getattr(base_spec, "derived", False)
        or getattr(base_capability, "derived", False)
        or getattr(base_spec, "derive", None)
        or getattr(base_capability, "derive_strategy_id", None)
    )
    if base_is_derived:
        if base_spec is None:
            base_spec = base_capability.to_var_spec()

        def _load_cumulative(cumulative_fh: int) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
            values, crs, transform = derive_variable(
                model_id=model_id,
                var_key=normalized_base_component,
                product=product,
                run_date=run_date,
                fh=cumulative_fh,
                var_spec_model=base_spec,
                var_capability=base_capability,
                model_plugin=model_plugin,
                fetch_ctx=ctx,
                derive_component_target_grid={"region": target_region, "id": target_grid_id},
                derive_component_resampling=resampling,
            )
            return values.astype(np.float32, copy=False), crs, transform
    else:

        def _load_cumulative(cumulative_fh: int) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
            forecast_raw, crs, transform = _fetch_component_warped(
                model_id=model_id,
                product=product,
                run_date=run_date,
                fh=cumulative_fh,
                model_plugin=model_plugin,
                var_key=normalized_base_component,
                target_region=target_region,
                target_grid_id=target_grid_id,
                resampling=resampling,
                ctx=ctx,
            )
            values = convert_units(
                forecast_raw,
                normalized_base_component,
                model_id=model_id,
                var_capability=base_capability,
            ).astype(np.float32, copy=False)
            return values, crs, transform

    target_values, src_crs, src_transform = _load_cumulative(target_fh)
    if window_start_fh > 0:
        start_values, start_crs, start_transform = _load_cumulative(window_start_fh)
        if target_values.shape != start_values.shape:
            raise ValueError(
                f"Precip anomaly rolling window shape mismatch: target={target_values.shape} start={start_values.shape}"
            )
        if src_crs != start_crs:
            raise ValueError(f"Precip anomaly rolling window CRS mismatch: target={src_crs} start={start_crs}")
        if any(
            abs(float(actual) - float(expected)) > 1.0e-6
            for actual, expected in zip(src_transform[:6], start_transform[:6])
        ):
            raise ValueError(
                f"Precip anomaly rolling window transform mismatch: target={src_transform} start={start_transform}"
            )
        target_valid = np.isfinite(target_values)
        start_valid = np.isfinite(start_values)
        forecast_values = np.where(
            target_valid & start_valid,
            np.maximum(target_values - start_values, 0.0),
            np.nan,
        ).astype(np.float32, copy=False)
    else:
        forecast_values = target_values.astype(np.float32, copy=False)

    init_date = run_date.astimezone(timezone.utc) if run_date.tzinfo else run_date.replace(tzinfo=timezone.utc)
    baseline_reference_date = init_date + timedelta(hours=window_start_fh)
    baseline_values, baseline_crs, baseline_transform, baseline_meta = load_accumulation_climatology_baseline(
        version=baseline_version,
        baseline_source=baseline_source,
        field=baseline_field,
        reference_date=baseline_reference_date,
        region=baseline_region,
        reference_period=reference_period,
    )
    if forecast_values.shape != baseline_values.shape:
        raise ValueError(
            f"Precip anomaly baseline shape mismatch: forecast={forecast_values.shape} baseline={baseline_values.shape}"
        )
    if src_crs != baseline_crs:
        raise ValueError(
            f"Precip anomaly baseline CRS mismatch: forecast={src_crs} baseline={baseline_crs}"
        )
    if any(
        abs(float(actual) - float(expected)) > 1.0e-6
        for actual, expected in zip(src_transform[:6], baseline_transform[:6])
    ):
        raise ValueError(
            f"Precip anomaly baseline transform mismatch: forecast={src_transform} baseline={baseline_transform}"
        )

    anomaly = (forecast_values - baseline_values).astype(np.float32, copy=False)
    _record_derive_sidecar_metadata(
        ctx,
        var_key=var_key,
        fh=fh,
        sidecar_metadata={
            **baseline_meta,
            "anomaly_kind": "accumulated_precip_departure",
            "baseline_alignment": "init_date" if window_start_fh == 0 else "window_start_date",
            "baseline_region": baseline_region,
            "target_fh": target_fh,
            "window_start_fh": window_start_fh,
            "window_end_fh": target_fh,
            "accumulation_window_hours": accumulation_window_hours,
            "baseline_reference_fh": window_start_fh,
            "model_accumulation_units": "in",
        },
    )
    return anomaly, baseline_crs, baseline_transform


# ---------------------------------------------------------------------------
# Bounded parallel prefetch for cumulative derive strategies
# ---------------------------------------------------------------------------

_PREFETCH_DEFAULT_WORKERS = 6
_PREFETCH_ENV_WORKERS = (
    "CARTOSKY_DERIVE_PREFETCH_WORKERS",
    "CARTOSKY_V3_DERIVE_PREFETCH_WORKERS",
    "TWF_V3_DERIVE_PREFETCH_WORKERS",
)
# If this fraction of prefetch tasks fail, stop launching new ones.
_PREFETCH_FAIL_ABORT_RATIO = 0.5
# Minimum tasks that must have completed before the abort ratio is evaluated.
_PREFETCH_FAIL_ABORT_MIN_COMPLETED = 4
# Brief sleep injected after a failed prefetch to back off upstream sources.
_PREFETCH_BACKOFF_SECONDS = 0.3


def _prefetch_max_workers() -> int:
    """Resolve bounded worker count from env or default."""
    raw = ""
    for env_name in _PREFETCH_ENV_WORKERS:
        raw = os.getenv(env_name, "").strip()
        if raw:
            break
    if raw:
        try:
            return max(1, min(int(raw), 12))
        except ValueError:
            pass
    return _PREFETCH_DEFAULT_WORKERS


@dataclass(frozen=True)
class _PrefetchTask:
    """Describes one GRIB component to pre-warm in the FetchContext cache."""
    model_id: str
    product: str
    run_date: datetime
    fh: int
    model_plugin: Any
    var_key: str
    warped: bool = False
    target_region: str = ""
    target_grid_id: str = ""
    resampling: str = ""

    @property
    def _dedup_key(self) -> tuple:
        if self.warped:
            return (self.model_id, self.product, self.fh, self.var_key,
                    self.target_grid_id, self.resampling)
        return (self.model_id, self.product, self.fh, self.var_key)


def _prefetch_components_parallel(
    tasks: list[_PrefetchTask],
    ctx: FetchContext | None,
    *,
    label: str = "",
) -> int:
    """Prefetch GRIB components with bounded concurrency and backoff.

    Warms the FetchContext cache so the subsequent sequential accumulation
    loop sees near-100% cache hits.  Failures are silently skipped — the
    main loop will attempt its own fetch and handle errors with existing
    error-handling logic.

    Returns the number of successfully prefetched items.
    """
    if not tasks or ctx is None:
        return 0

    # Deduplicate by cache-relevant fields.
    seen: set[tuple] = set()
    unique: list[_PrefetchTask] = []
    for task in tasks:
        key = task._dedup_key
        if key in seen:
            continue
        seen.add(key)
        unique.append(task)

    if not unique:
        return 0

    workers = min(_prefetch_max_workers(), len(unique))
    _log_fetch_context_memory(
        label="prefetch_before",
        ctx=ctx,
        extra=f"requested={len(tasks)} unique={len(unique)} workers={workers} label={label or '-'}",
    )

    # For very small task lists, skip the thread-pool overhead entirely.
    if workers <= 1 or len(unique) <= 2:
        succeeded = _prefetch_sequential(unique, ctx)
        _log_fetch_context_memory(
            label="prefetch_after",
            ctx=ctx,
            extra=f"requested={len(tasks)} unique={len(unique)} ok={succeeded} failed={len(unique) - succeeded} workers=sequential label={label or '-'}",
        )
        return succeeded

    succeeded = 0
    failed = 0
    lock = threading.Lock()
    log_label = f" [{label}]" if label else ""

    def _run_one(task: _PrefetchTask) -> bool:
        # Early abort check: if many tasks have already failed, skip new ones
        # to avoid hammering a struggling upstream source.
        with lock:
            total_done = succeeded + failed
            if (
                total_done >= _PREFETCH_FAIL_ABORT_MIN_COMPLETED
                and failed > total_done * _PREFETCH_FAIL_ABORT_RATIO
            ):
                return False
        try:
            if task.warped:
                _fetch_component_warped(
                    model_id=task.model_id,
                    product=task.product,
                    run_date=task.run_date,
                    fh=task.fh,
                    model_plugin=task.model_plugin,
                    var_key=task.var_key,
                    target_region=task.target_region,
                    target_grid_id=task.target_grid_id,
                    resampling=task.resampling,
                    ctx=ctx,
                )
            else:
                _fetch_component(
                    model_id=task.model_id,
                    product=task.product,
                    run_date=task.run_date,
                    fh=task.fh,
                    model_plugin=task.model_plugin,
                    var_key=task.var_key,
                    ctx=ctx,
                )
            return True
        except Exception:
            # Backoff briefly so concurrent workers don't stampede a failing source.
            time.sleep(_PREFETCH_BACKOFF_SECONDS)
            return False

    t0 = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_one, task): task for task in unique}
            for future in as_completed(futures):
                try:
                    ok = future.result()
                except Exception:
                    ok = False
                with lock:
                    if ok:
                        succeeded += 1
                    else:
                        failed += 1
    except RuntimeError as exc:
        if "interpreter shutdown" in str(exc).lower():
            logger.info(
                "prefetch%s aborted during interpreter shutdown: completed=%d/%d workers=%d",
                log_label,
                succeeded,
                len(unique),
                workers,
            )
            return succeeded
        raise

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "prefetch%s complete: %d/%d ok, %d failed, workers=%d, %.0fms",
        log_label,
        succeeded,
        len(unique),
        failed,
        workers,
        elapsed_ms,
    )
    _log_fetch_context_memory(
        label="prefetch_after",
        ctx=ctx,
        extra=f"requested={len(tasks)} unique={len(unique)} ok={succeeded} failed={failed} workers={workers} label={label or '-'} elapsed_ms={elapsed_ms:.0f}",
    )
    return succeeded


def _prefetch_sequential(
    tasks: list[_PrefetchTask],
    ctx: FetchContext | None,
) -> int:
    """Fallback: prefetch a small task list without thread-pool overhead."""
    if not tasks or ctx is None:
        return 0
    ok = 0
    for task in tasks:
        try:
            if task.warped:
                _fetch_component_warped(
                    model_id=task.model_id,
                    product=task.product,
                    run_date=task.run_date,
                    fh=task.fh,
                    model_plugin=task.model_plugin,
                    var_key=task.var_key,
                    target_region=task.target_region,
                    target_grid_id=task.target_grid_id,
                    resampling=task.resampling,
                    ctx=ctx,
                )
            else:
                _fetch_component(
                    model_id=task.model_id,
                    product=task.product,
                    run_date=task.run_date,
                    fh=task.fh,
                    model_plugin=task.model_plugin,
                    var_key=task.var_key,
                    ctx=ctx,
                )
            ok += 1
        except Exception:
            pass
    return ok


def _resolve_component_cache_identity(model_plugin: Any, var_key: str) -> tuple[str, str]:
    normalized_var_key, selectors = _resolve_component_var(model_plugin, var_key)
    return normalized_var_key, _selector_fingerprint(selectors)


def _resolved_apcp_cache_key(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    step_fh: int,
    model_plugin: Any,
    apcp_component: str,
    expected_start_fh: int,
    use_warped: bool,
    target_grid_id: str,
    resampling: str,
    ctx: FetchContext | None,
) -> tuple[str, str, str, int, str, str, int, str, str] | None:
    try:
        apcp_cache_var_key, apcp_selector_fingerprint = _resolve_component_cache_identity(
            model_plugin,
            apcp_component,
        )
    except Exception:
        return None

    run_date_utc = (
        run_date.astimezone(timezone.utc)
        if run_date.tzinfo else run_date.replace(tzinfo=timezone.utc)
    )
    if use_warped:
        scope_primary = str(target_grid_id)
        scope_secondary = str(resampling)
    else:
        scope_primary = str(getattr(ctx, "coverage", "")) if ctx is not None else ""
        scope_secondary = str(getattr(model_plugin, "coverage", ""))

    return (
        str(model_id),
        str(product),
        run_date_utc.isoformat(),
        int(step_fh),
        str(apcp_cache_var_key),
        str(apcp_selector_fingerprint),
        int(expected_start_fh),
        scope_primary,
        scope_secondary,
    )


def _parse_apcp_accum_window_hours(inventory_line: str | None) -> tuple[int, int] | None:
    if not inventory_line:
        return None
    line = str(inventory_line)
    match = _APCP_ACCUM_HOUR_WINDOW_RE.search(line)
    if match is not None:
        try:
            start_hour = int(match.group(1))
            end_hour = int(match.group(2))
        except (TypeError, ValueError):
            return None
    else:
        match = _APCP_ACCUM_DAY_WINDOW_RE.search(line)
        if match is None:
            return None
        try:
            start_hour = int(match.group(1)) * 24
            end_hour = int(match.group(2)) * 24
        except (TypeError, ValueError):
            return None
    if start_hour < 0 or end_hour < 0:
        return None
    return start_hour, end_hour


def _is_probabilistic_apcp_inventory_line(inventory_line: str | None) -> bool:
    line = str(inventory_line or "").strip().lower()
    if not line:
        return False
    return "probability" in line or ":prob " in line


def _apcp_inventory_search_pattern(inventory_line: str | None) -> str:
    line = str(inventory_line or "").strip()
    marker = line.find(":APCP:")
    if marker < 0:
        return ""
    message = line[marker:]
    if message.endswith("$"):
        return message
    return message + "$"


def _classify_apcp_mode_for_kuchera(
    *,
    inventory_line: str | None,
    step_fh: int,
    expected_start_fh: int,
) -> str:
    if _is_probabilistic_apcp_inventory_line(inventory_line):
        return "invalid"
    window = _parse_apcp_accum_window_hours(inventory_line)
    if window is None:
        return "invalid"
    start_hour, end_hour = window
    if end_hour != int(step_fh):
        return "invalid"
    if start_hour > int(expected_start_fh):
        return "invalid"
    if start_hour == int(expected_start_fh):
        return "exact_step"
    if start_hour == 0 and int(expected_start_fh) > 0:
        return "cumulative_from_zero"
    if 0 <= start_hour < int(expected_start_fh):
        return "overlap_window"
    return "invalid"


def _apcp_exact_window_pattern(start_fh: int, end_fh: int) -> str:
    return f":APCP:surface:{int(start_fh)}-{int(end_fh)} hour acc fcst:$"


def _kuchera_primary_herbie_priority() -> str:
    raw = os.getenv("CARTOSKY_HERBIE_PRIORITY") or os.getenv("TWF_HERBIE_PRIORITY", "aws")
    for token in str(raw).split(","):
        candidate = token.strip()
        if candidate:
            return candidate
    return "aws"


def _kuchera_inventory_lines(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    search_pattern: str,
) -> list[str]:
    priority = _kuchera_primary_herbie_priority()
    try:
        return inventory_lines_for_pattern(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=int(fh),
            search_pattern=search_pattern,
            herbie_kwargs={"priority": priority},
        )
    except Exception:
        return []


def _kuchera_select_apcp_window_from_inventory(
    *,
    inventory_lines: list[str],
    step_fh: int,
    expected_start_fh: int,
) -> dict[str, Any] | None:
    exact: dict[str, Any] | None = None
    cumulative: dict[str, Any] | None = None
    overlap: dict[str, Any] | None = None
    for line in inventory_lines:
        window = _parse_apcp_accum_window_hours(line)
        if window is None:
            continue
        start_hour, end_hour = window
        mode = _classify_apcp_mode_for_kuchera(
            inventory_line=line,
            step_fh=step_fh,
            expected_start_fh=expected_start_fh,
        )
        if mode == "invalid":
            continue
        candidate = {
            "start_hour": int(start_hour),
            "end_hour": int(end_hour),
            "selected_window": f"{int(start_hour)}-{int(end_hour)}",
            "inventory_line": str(line),
            "search_pattern": _apcp_inventory_search_pattern(line),
            "mode": mode,
        }
        if mode == "exact_step":
            exact = candidate
            break
        if mode == "cumulative_from_zero":
            cumulative = candidate
            continue
        if mode == "overlap_window" and (
            overlap is None or int(start_hour) > int(overlap["start_hour"])
        ):
            overlap = candidate

    if exact is not None:
        return exact
    if cumulative is not None:
        return cumulative
    return overlap


def _normalize_ptype_probability(data: np.ndarray) -> np.ndarray:
    values = np.asarray(data, dtype=np.float32)
    finite = np.isfinite(values)
    max_val = float(np.nanmax(values[finite])) if np.any(finite) else 0.0
    scale = 100.0 if max_val > 1.5 else 1.0
    normalized = values / np.float32(scale)
    normalized = np.clip(normalized, 0.0, 1.0).astype(np.float32, copy=False)
    return normalized


def _ptype_intensity_temp_signal(temp_c: np.ndarray | None, *, cold_at_c: float, warm_at_c: float) -> tuple[np.ndarray, np.ndarray]:
    if temp_c is None:
        empty = np.zeros((1, 1), dtype=np.float32)
        return empty, empty
    values = np.asarray(temp_c, dtype=np.float32)
    finite = np.isfinite(values)
    cold = np.zeros(values.shape, dtype=np.float32)
    warm = np.zeros(values.shape, dtype=np.float32)
    cold_span = max(float(warm_at_c) - float(cold_at_c), 1e-6)
    warm_span = cold_span
    if np.any(finite):
        cold[finite] = np.clip((float(warm_at_c) - values[finite]) / cold_span, 0.0, 1.0)
        warm[finite] = np.clip((values[finite] - float(cold_at_c)) / warm_span, 0.0, 1.0)
    return cold.astype(np.float32, copy=False), warm.astype(np.float32, copy=False)


def _ptype_intensity_fetch_optional_component(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    model_plugin: Any,
    var_key: str | None,
    ctx: FetchContext | None,
    use_warped: bool = False,
    target_region: str = "",
    target_grid_id: str = "",
    resampling: str = "",
) -> np.ndarray | None:
    candidate = str(var_key or "").strip()
    if not candidate:
        return None
    try:
        data, _, _ = _fetch_step_component(
            model_id=model_id,
            product=product,
            run_date=run_date,
            step_fh=fh,
            model_plugin=model_plugin,
            var_key=candidate,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            ctx=ctx,
        )
    except Exception:
        logger.debug("ptype_intensity optional component unavailable: model=%s var=%s fh=%03d", model_id, candidate, fh)
        return None
    return np.asarray(data, dtype=np.float32)


def _ptype_intensity_thermal_fields(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    model_plugin: Any,
    ctx: FetchContext | None,
    hints: dict[str, Any],
    expected_shape: tuple[int, ...],
    use_warped: bool = False,
    target_region: str = "",
    target_grid_id: str = "",
    resampling: str = "",
) -> tuple[np.ndarray, np.ndarray]:
    temp2m = _ptype_intensity_fetch_optional_component(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        model_plugin=model_plugin,
        var_key=str(hints.get("surface_temp_component") or "tmp2m"),
        ctx=ctx,
        use_warped=use_warped,
        target_region=target_region,
        target_grid_id=target_grid_id,
        resampling=resampling,
    )
    temp850 = _ptype_intensity_fetch_optional_component(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        model_plugin=model_plugin,
        var_key=str(hints.get("mid_temp_component") or "tmp850"),
        ctx=ctx,
        use_warped=use_warped,
        target_region=target_region,
        target_grid_id=target_grid_id,
        resampling=resampling,
    )

    cold_fields: list[np.ndarray] = []
    warm_fields: list[np.ndarray] = []
    thermal_weights: list[float] = []
    for values, cold_at_c, warm_at_c, weight in (
        (temp2m, -1.0, 2.0, 0.35),
        (temp850, -4.0, 1.0, 0.65),
    ):
        if values is None or values.shape != expected_shape:
            continue
        cold, warm = _ptype_intensity_temp_signal(values, cold_at_c=cold_at_c, warm_at_c=warm_at_c)
        cold_fields.append(cold)
        warm_fields.append(warm)
        thermal_weights.append(float(weight))

    if not cold_fields:
        zeros = np.zeros(expected_shape, dtype=np.float32)
        return zeros, zeros

    weights = np.asarray(thermal_weights, dtype=np.float32)
    cold_profile = np.average(np.stack(cold_fields, axis=0), axis=0, weights=weights).astype(np.float32, copy=False)
    warm_profile = np.average(np.stack(warm_fields, axis=0), axis=0, weights=weights).astype(np.float32, copy=False)
    return cold_profile, warm_profile


def _ptype_intensity_fetch_step_intensity(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    model_plugin: Any,
    ctx: FetchContext | None,
    hints: dict[str, Any],
    expected_shape: tuple[int, ...],
    use_warped: bool = False,
    target_region: str = "",
    target_grid_id: str = "",
    resampling: str = "",
) -> np.ndarray | None:
    """Fetch the per-step APCP accumulation for ptype intensity display.

    GFS publishes APCP in 6-hour accumulation buckets.  At 6-hour boundary
    forecast hours (6, 12, 18, …, 72, 78, …) the only step record covers
    the full 6-hour bucket (e.g. ``66-72 hour acc``); at intermediate hours
    (9, 15, 21, …, 75, …) a shorter step record exists (e.g. ``72-75 hour
    acc``).  A cumulative ``0-N`` record may also exist.

    Strategy:
      1. Query the Herbie inventory for all APCP:surface records at *fh*.
      2. Parse each record's accumulation window.  Keep only windows that
         end at *fh* and do **not** start at 0 (i.e. skip cumulative).
      3. Among those, pick the **narrowest** window (shortest duration)
         — this is the actual step/bucket record.
      4. Fetch that specific record using its exact inventory search pattern.
      5. Normalise to a 3-hour equivalent by dividing by the step's duration
         in hours and multiplying by 3, so that 6-hour boundary values are
         comparable to 3-hour step values.

    Falls back to ``None`` (triggering the prate fallback in the caller)
    if the inventory is unavailable or no non-cumulative record is found.
    """
    apcp_product: str | None = hints.get("apcp_product")
    if apcp_product is not None:
        apcp_product = str(apcp_product).strip() or None
    resolved_product = str(apcp_product or product)
    inch_scale = np.float32(0.03937007874015748)
    nominal_step_hours = np.float32(3.0)

    # --- inventory lookup ---
    inventory_lines = _kuchera_inventory_lines(
        model_id=model_id,
        product=resolved_product,
        run_date=run_date,
        fh=fh,
        search_pattern=":APCP:surface:",
    )
    if not inventory_lines:
        logger.debug(
            "ptype_intensity APCP inventory empty: model=%s fh=%03d",
            model_id, fh,
        )
        return None

    # --- select the best non-cumulative step record ---
    best_line: str | None = None
    best_start: int | None = None
    best_duration: int | None = None
    for line in inventory_lines:
        if _is_probabilistic_apcp_inventory_line(line):
            continue
        window = _parse_apcp_accum_window_hours(line)
        if window is None:
            continue
        start_hour, end_hour = window
        if int(end_hour) != int(fh):
            continue
        if int(start_hour) == 0 and int(fh) > 0:
            # Skip cumulative 0-N records.
            continue
        duration = int(end_hour) - int(start_hour)
        if duration <= 0:
            continue
        if best_duration is None or duration < best_duration:
            best_line = line
            best_start = int(start_hour)
            best_duration = duration

    # Special case: fh ≤ first bucket boundary — the only record may start
    # at 0 (e.g. FH 3: ``0-3 hour acc`` is both cumulative and the first
    # step).  Accept it.
    if best_line is None:
        for line in inventory_lines:
            if _is_probabilistic_apcp_inventory_line(line):
                continue
            window = _parse_apcp_accum_window_hours(line)
            if window is None:
                continue
            start_hour, end_hour = window
            if int(end_hour) != int(fh):
                continue
            if int(start_hour) != 0:
                continue
            duration = int(end_hour) - int(start_hour)
            if duration <= 0:
                continue
            if best_duration is None or duration < best_duration:
                best_line = line
                best_start = int(start_hour)
                best_duration = duration

    if best_line is None or best_duration is None:
        logger.debug(
            "ptype_intensity APCP no suitable step record: model=%s fh=%03d lines=%s",
            model_id, fh, inventory_lines,
        )
        return None

    search_pattern = _apcp_inventory_search_pattern(best_line)
    if not search_pattern:
        return None

    # --- fetch the selected record ---
    try:
        fetch_kwargs: dict[str, Any] = {}
        if ctx is not None and getattr(ctx, "bundle_fetch_cache", None) is not None:
            fetch_kwargs["bundle_fetch_cache"] = getattr(ctx, "bundle_fetch_cache")
        step_data, step_crs, step_transform, step_meta = fetch_variable(
            model_id=model_id,
            product=resolved_product,
            search_pattern=search_pattern,
            run_date=run_date,
            fh=fh,
            **fetch_kwargs,
            return_meta=True,
        )
    except Exception:
        logger.debug(
            "ptype_intensity APCP fetch failed: model=%s fh=%03d pattern=%s",
            model_id, fh, search_pattern,
            exc_info=True,
        )
        return None

    if tuple(np.shape(step_data)) != tuple(expected_shape) and use_warped:
        try:
            step_data, _ = _warp_component_to_target_grid(
                raw_data=np.asarray(step_data, dtype=np.float32),
                raw_crs=step_crs,
                raw_transform=step_transform,
                model_id=model_id,
                target_region=target_region,
                target_grid_id=target_grid_id,
                resampling=resampling,
            )
        except Exception:
            logger.debug(
                "ptype_intensity APCP warp failed: model=%s fh=%03d pattern=%s",
                model_id, fh, search_pattern,
                exc_info=True,
            )
            return None

    if tuple(np.shape(step_data)) != tuple(expected_shape):
        return None

    step_values = np.asarray(step_data, dtype=np.float32)
    apcp_valid = np.isfinite(step_values) & (step_values >= 0.0)

    # Convert kg/m² → inches.
    step_inches = (step_values * inch_scale).astype(np.float32, copy=False)

    # Normalise to a 3-hour equivalent so that 6-hour bucket values are
    # comparable to 3-hour step values in the display bins.
    actual_hours = np.float32(max(1, best_duration))
    if actual_hours != nominal_step_hours:
        step_inches = (step_inches * (nominal_step_hours / actual_hours)).astype(
            np.float32, copy=False,
        )
        logger.info(
            "ptype_intensity APCP normalised %d-hour step to %.0f-hour equivalent: "
            "model=%s fh=%03d window=%d-%d",
            best_duration, float(nominal_step_hours),
            model_id, fh, best_start, fh,
        )

    logger.info(
        "ptype_intensity APCP step: model=%s fh=%03d window=%d-%d duration=%dh pattern=%s",
        model_id, fh, best_start, fh, best_duration, search_pattern,
    )
    return np.where(apcp_valid, step_inches, np.nan).astype(np.float32, copy=False)


def _ptype_intensity_fetch_direct_cumulative_step(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    model_plugin: Any,
    ctx: FetchContext | None,
    hints: dict[str, Any],
    component_var_key: str,
    use_warped: bool,
    target_region: str,
    target_grid_id: str,
    resampling: str,
    normalize_to_3h: bool = True,
) -> tuple[np.ndarray, np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    step_fhs = _resolve_cumulative_step_fhs(
        hints=hints,
        fh=fh,
        run_date=run_date,
        default_step_hours=3,
    )
    prev_fh: int | None = None
    for candidate in reversed(step_fhs):
        if int(candidate) < int(fh):
            prev_fh = int(candidate)
            break

    current_data, current_crs, current_transform = _fetch_step_component(
        model_id=model_id,
        product=product,
        run_date=run_date,
        step_fh=fh,
        model_plugin=model_plugin,
        var_key=component_var_key,
        use_warped=use_warped,
        target_region=target_region,
        target_grid_id=target_grid_id,
        resampling=resampling,
        ctx=ctx,
    )
    current_values = np.asarray(current_data, dtype=np.float32)
    current_valid = np.isfinite(current_values) & (current_values >= 0.0)
    current_clean = np.where(current_valid, current_values, 0.0).astype(np.float32, copy=False)

    if prev_fh is None:
        step_clean = current_clean
        step_valid = current_valid
        duration_hours = int(step_fhs[0]) if step_fhs else max(1, int(fh))
    else:
        previous_data, previous_crs, previous_transform = _fetch_step_component(
            model_id=model_id,
            product=product,
            run_date=run_date,
            step_fh=prev_fh,
            model_plugin=model_plugin,
            var_key=component_var_key,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            ctx=ctx,
        )
        previous_values = np.asarray(previous_data, dtype=np.float32)
        if (
            previous_values.shape != current_values.shape
            or previous_crs != current_crs
            or previous_transform != current_transform
        ):
            raise ValueError(
                f"ptype_intensity cumulative grid mismatch for {model_id}/{component_var_key} fh{fh:03d}"
            )
        previous_valid = np.isfinite(previous_values) & (previous_values >= 0.0)
        previous_clean = np.where(previous_valid, previous_values, 0.0).astype(np.float32, copy=False)
        step_clean = np.clip(current_clean - previous_clean, 0.0, None).astype(np.float32, copy=False)
        step_valid = current_valid & previous_valid
        duration_hours = max(1, int(fh) - int(prev_fh))

    step_inches = (step_clean * np.float32(39.37007874015748)).astype(np.float32, copy=False)
    if normalize_to_3h and duration_hours != 3:
        step_inches = (step_inches * (np.float32(3.0) / np.float32(duration_hours))).astype(np.float32, copy=False)
        logger.info(
            "ptype_intensity cumulative step normalised model=%s component=%s fh=%03d duration=%dh target=3h",
            model_id,
            component_var_key,
            fh,
            duration_hours,
        )
    return np.where(step_valid, step_inches, np.nan).astype(np.float32, copy=False), step_valid, current_crs, current_transform


def _ptype_intensity_ecmwf_phase_signals(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    model_plugin: Any,
    ctx: FetchContext | None,
    hints: dict[str, Any],
    expected_shape: tuple[int, ...],
    use_warped: bool = False,
    target_region: str = "",
    target_grid_id: str = "",
    resampling: str = "",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    component_specs = (
        (str(hints.get("surface_temp_component") or "tmp2m"), -1.0, 1.0, 0.40, "surface"),
        (str(hints.get("low_temp_component") or "tmp925"), -1.5, 1.5, 0.25, "low"),
        (str(hints.get("mid_temp_component") or "tmp850"), -4.0, 2.0, 0.35, "mid"),
    )

    cold_fields: list[np.ndarray] = []
    cold_weights: list[float] = []
    surface_cold = np.zeros(expected_shape, dtype=np.float32)
    warm_nose = np.zeros(expected_shape, dtype=np.float32)

    for var_key, cold_at_c, warm_at_c, weight, role in component_specs:
        values = _ptype_intensity_fetch_optional_component(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=var_key,
            ctx=ctx,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
        )
        if values is None or values.shape != expected_shape:
            continue
        cold, warm = _ptype_intensity_temp_signal(
            values,
            cold_at_c=float(cold_at_c),
            warm_at_c=float(warm_at_c),
        )
        cold_fields.append(cold)
        cold_weights.append(float(weight))
        if role == "surface":
            surface_cold = cold
        else:
            warm_nose = np.maximum(warm_nose, warm)

    if not cold_fields:
        zeros = np.zeros(expected_shape, dtype=np.float32)
        return zeros, zeros, zeros

    deep_cold = np.average(
        np.stack(cold_fields, axis=0),
        axis=0,
        weights=np.asarray(cold_weights, dtype=np.float32),
    ).astype(np.float32, copy=False)
    return deep_cold, surface_cold.astype(np.float32, copy=False), warm_nose.astype(np.float32, copy=False)


def _ptype_intensity_family_rates_ecmwf(
    *,
    intensity: np.ndarray,
    snow_lwe: np.ndarray,
    deep_cold: np.ndarray,
    surface_cold: np.ndarray,
    warm_nose: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    base_intensity = np.where(
        np.isfinite(intensity),
        np.maximum(np.asarray(intensity, dtype=np.float32), 0.0),
        np.nan,
    ).astype(np.float32, copy=False)
    snow_lwe_clean = np.where(
        np.isfinite(snow_lwe),
        np.maximum(np.asarray(snow_lwe, dtype=np.float32), 0.0),
        0.0,
    ).astype(np.float32, copy=False)

    positive_precip = np.isfinite(base_intensity) & (base_intensity >= np.float32(0.01))
    snow_frac = np.zeros(base_intensity.shape, dtype=np.float32)
    np.divide(
        np.minimum(snow_lwe_clean, np.nan_to_num(base_intensity, nan=0.0)),
        np.maximum(np.nan_to_num(base_intensity, nan=0.0), np.float32(1e-6)),
        out=snow_frac,
        where=positive_precip,
    )
    snow_frac = np.clip(snow_frac, 0.0, 1.0).astype(np.float32, copy=False)

    deep_cold = np.clip(np.nan_to_num(deep_cold, nan=0.0), 0.0, 1.0).astype(np.float32, copy=False)
    surface_cold = np.clip(np.nan_to_num(surface_cold, nan=0.0), 0.0, 1.0).astype(np.float32, copy=False)
    warm_nose = np.clip(np.nan_to_num(warm_nose, nan=0.0), 0.0, 1.0).astype(np.float32, copy=False)

    strong_snow = positive_precip & (
        (snow_frac >= np.float32(0.55))
        | ((snow_frac >= np.float32(0.20)) & (deep_cold >= np.float32(0.55)))
        | ((snow_frac >= np.float32(0.05)) & (deep_cold >= np.float32(0.85)) & (warm_nose <= np.float32(0.20)))
        | ((snow_frac < np.float32(0.05)) & (deep_cold >= np.float32(0.92)) & (warm_nose <= np.float32(0.10)))
    )
    ice_mask = positive_precip & (~strong_snow) & (
        (surface_cold >= np.float32(0.45))
        & (warm_nose >= np.float32(0.35))
        & (snow_frac < np.float32(0.55))
    )
    rain_mask = positive_precip & (~strong_snow) & (~ice_mask)

    rain_rate = np.zeros(base_intensity.shape, dtype=np.float32)
    snow_rate = np.zeros(base_intensity.shape, dtype=np.float32)
    ice_rate = np.zeros(base_intensity.shape, dtype=np.float32)
    rain_rate[rain_mask] = base_intensity[rain_mask]
    snow_rate[strong_snow] = base_intensity[strong_snow]
    ice_rate[ice_mask] = base_intensity[ice_mask]
    invalid = ~np.isfinite(base_intensity)
    rain_rate[invalid] = np.nan
    snow_rate[invalid] = np.nan
    ice_rate[invalid] = np.nan
    return base_intensity, rain_rate, snow_rate, ice_rate


def _ptype_intensity_index_from_family_rates(
    *,
    rain_rate: np.ndarray,
    snow_rate: np.ndarray,
    ice_rate: np.ndarray,
    snow_display_boost: float,
) -> np.ndarray:
    family_rate_by_code = {
        "rain": rain_rate,
        "snow": snow_rate,
        "ice": ice_rate,
    }
    family_stack = np.stack([ice_rate, snow_rate, rain_rate], axis=0).astype(np.float32, copy=False)
    family_idx = np.argmax(np.nan_to_num(family_stack, nan=-1.0), axis=0).astype(np.int32)
    family_codes = np.array(["ice", "snow", "rain"])
    ptype = family_codes[family_idx]
    has_any_ptype = (
        np.isfinite(rain_rate)
        & (
            (np.nan_to_num(rain_rate, nan=0.0) > 0.0)
            | (np.nan_to_num(snow_rate, nan=0.0) > 0.0)
            | (np.nan_to_num(ice_rate, nan=0.0) > 0.0)
        )
    )

    type_levels = {
        "rain": np.asarray([0.0, 0.01, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0], dtype=np.float32),
        "snow": np.asarray([0.05, 0.25, 0.50, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0], dtype=np.float32),
        "ice": np.asarray([0.01, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0, 1.25, 1.5, 2.0], dtype=np.float32),
    }
    type_breaks = {
        "rain": {"offset": 0, "count": 16},
        "snow": {"offset": 16, "count": 10},
        "ice": {"offset": 26, "count": 18},
    }
    min_visible = {"rain": 0.01, "snow": 0.01, "ice": 0.01}

    indexed = np.full(rain_rate.shape, np.nan, dtype=np.float32)
    for code in ("rain", "snow", "ice"):
        levels = type_levels[code]
        offset = int(type_breaks[code]["offset"])
        count = int(type_breaks[code]["count"])
        family_rate = family_rate_by_code[code]
        display_rate = family_rate
        if code == "snow":
            display_rate = (np.float32(snow_display_boost) * np.nan_to_num(family_rate, nan=0.0)).astype(np.float32, copy=False)
        selector = (
            (ptype == code)
            & np.isfinite(family_rate)
            & (family_rate >= float(min_visible[code]))
            & has_any_ptype
        )
        if not np.any(selector):
            continue
        local_bin = np.digitize(display_rate[selector], levels, right=False) - 1
        local_bin = np.clip(local_bin, 0, count - 1)
        indexed[selector] = (offset + local_bin).astype(np.float32)
    return indexed


def _derive_ptype_intensity_rates_ecmwf(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    model_plugin: Any,
    ctx: FetchContext | None,
    derive_component_target_grid: dict[str, str] | None,
    derive_component_resampling: str | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    _log_fetch_context_memory(
        label="ptype_intensity_ecmwf_entry",
        ctx=ctx,
        model_id=model_id,
        var_key="ptype_intensity_ecmwf",
        fh=fh,
        extra=f"product={product}",
    )
    use_warped, target_region, target_grid_id, resampling = _resolve_warped_state(
        derive_component_target_grid,
        derive_component_resampling,
        model_id,
    )
    precip_component = str(hints.get("precip_component") or "precip_total")
    snow_component = str(hints.get("snow_component") or "sf")

    total_step, _, src_crs, src_transform = _ptype_intensity_fetch_direct_cumulative_step(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        model_plugin=model_plugin,
        ctx=ctx,
        hints=hints,
        component_var_key=precip_component,
        use_warped=use_warped,
        target_region=target_region,
        target_grid_id=target_grid_id,
        resampling=resampling,
    )
    try:
        snow_step, _, snow_crs, snow_transform = _ptype_intensity_fetch_direct_cumulative_step(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            ctx=ctx,
            hints=hints,
            component_var_key=snow_component,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
        )
        if snow_step.shape != total_step.shape or snow_crs != src_crs or snow_transform != src_transform:
            raise ValueError(
                f"ptype_intensity ECMWF snow/precip grid mismatch for fh{fh:03d}"
            )
    except Exception:
        logger.debug(
            "ptype_intensity ECMWF snow component unavailable: model=%s fh=%03d var=%s",
            model_id,
            fh,
            snow_component,
            exc_info=True,
        )
        snow_step = np.zeros(total_step.shape, dtype=np.float32)
        snow_step[~np.isfinite(total_step)] = np.nan
    _log_fetch_context_memory(
        label="ptype_intensity_ecmwf_after_fetch",
        ctx=ctx,
        model_id=model_id,
        var_key="ptype_intensity_ecmwf",
        fh=fh,
        extra=f"shape={total_step.shape}",
    )

    deep_cold, surface_cold, warm_nose = _ptype_intensity_ecmwf_phase_signals(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        model_plugin=model_plugin,
        ctx=ctx,
        hints=hints,
        expected_shape=total_step.shape,
        use_warped=use_warped,
        target_region=target_region,
        target_grid_id=target_grid_id,
        resampling=resampling,
    )
    _log_fetch_context_memory(
        label="ptype_intensity_ecmwf_after_phase_signals",
        ctx=ctx,
        model_id=model_id,
        var_key="ptype_intensity_ecmwf",
        fh=fh,
        extra=f"shape={total_step.shape}",
    )
    _, rain_rate, snow_rate, ice_rate = _ptype_intensity_family_rates_ecmwf(
        intensity=total_step,
        snow_lwe=snow_step,
        deep_cold=deep_cold,
        surface_cold=surface_cold,
        warm_nose=warm_nose,
    )
    _log_fetch_context_memory(
        label="ptype_intensity_ecmwf_exit",
        ctx=ctx,
        model_id=model_id,
        var_key="ptype_intensity_ecmwf",
        fh=fh,
        extra=f"shape={rain_rate.shape}",
    )
    return rain_rate, snow_rate, ice_rate, src_crs, src_transform


def _ptype_intensity_family_rates(
    *,
    intensity: np.ndarray,
    rain: np.ndarray,
    snow: np.ndarray,
    sleet: np.ndarray,
    frzr: np.ndarray,
    cold_profile: np.ndarray | None = None,
    warm_profile: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Split total precip intensity into display-family intensity planes.

    Uses priority-based selection matching how the competitor renders ptype:
    **ice > snow > rain**.  If the model's categorical mask for a higher-priority
    family is set above a small threshold the pixel belongs to that family
    regardless of what lower-priority masks say.  GFS routinely sets both
    ``crain=1`` and ``csnow=1`` in snow regions, so argmax-of-scores approaches
    systematically misclassify these as rain.

    Thermal profiles are only used as a **fallback** when *all* categorical masks
    are zero but valid precipitation exists (e.g. early GFS hours where ptype
    masks haven't spun up yet).
    """
    base_intensity = np.where(
        np.isfinite(intensity),
        np.maximum(intensity, 0.0),
        np.nan,
    ).astype(np.float32)

    rain_prob = _normalize_ptype_probability(rain)
    snow_prob = _normalize_ptype_probability(snow)
    sleet_prob = _normalize_ptype_probability(sleet)
    frzr_prob = _normalize_ptype_probability(frzr)
    ice_prob = np.maximum(sleet_prob, frzr_prob).astype(np.float32, copy=False)

    # --- Priority-based family assignment (ice > snow > rain) ---------------
    # GFS categorical masks are binary (0 or 1).  Any nonzero mask means the
    # model predicts that ptype.  Use a small threshold so even slightly-
    # interpolated or normalized values are captured.  Priority ordering
    # already prevents lower-priority types from stealing pixels.
    ice_thresh = np.float32(0.01)
    snow_thresh = np.float32(0.01)
    rain_thresh = np.float32(0.01)

    is_ice = ice_prob >= ice_thresh
    is_snow = (~is_ice) & (snow_prob >= snow_thresh)
    is_rain = (~is_ice) & (~is_snow) & (rain_prob >= rain_thresh)

    # --- Thermal fallback for pixels with precip but no categorical signal ---
    positive_precip = np.isfinite(base_intensity) & (base_intensity >= 0.01)
    has_any_mask = (rain_prob >= rain_thresh) | (snow_prob >= snow_thresh) | (ice_prob >= ice_thresh)
    needs_fallback = positive_precip & (~has_any_mask)

    if np.any(needs_fallback):
        cold_signal = np.zeros(base_intensity.shape, dtype=np.float32)
        warm_signal = np.zeros(base_intensity.shape, dtype=np.float32)
        if cold_profile is not None and cold_profile.shape == base_intensity.shape:
            cold_signal = np.clip(np.nan_to_num(cold_profile, nan=0.0), 0.0, 1.0).astype(np.float32, copy=False)
        if warm_profile is not None and warm_profile.shape == base_intensity.shape:
            warm_signal = np.clip(np.nan_to_num(warm_profile, nan=0.0), 0.0, 1.0).astype(np.float32, copy=False)

        fb_cold = cold_signal[needs_fallback]
        fb_warm = warm_signal[needs_fallback]
        # Strong cold → snow, moderate warm with some cold → ice, else rain
        fb_snow = fb_cold >= 0.5
        fb_ice = (~fb_snow) & (fb_warm >= 0.3) & (fb_cold >= 0.2)
        fb_rain = (~fb_snow) & (~fb_ice)

        # Apply fallback assignments
        fallback_indices = np.where(needs_fallback)
        snow_fallback_mask = np.zeros(base_intensity.shape, dtype=bool)
        ice_fallback_mask = np.zeros(base_intensity.shape, dtype=bool)
        rain_fallback_mask = np.zeros(base_intensity.shape, dtype=bool)
        snow_fallback_mask[fallback_indices] = fb_snow
        ice_fallback_mask[fallback_indices] = fb_ice
        rain_fallback_mask[fallback_indices] = fb_rain

        is_snow = is_snow | snow_fallback_mask
        is_ice = is_ice | ice_fallback_mask
        is_rain = is_rain | rain_fallback_mask

    # --- Assign full intensity to the winning family -----------------------
    finite_intensity = np.isfinite(base_intensity)
    rain_rate = np.zeros(base_intensity.shape, dtype=np.float32)
    snow_rate = np.zeros(base_intensity.shape, dtype=np.float32)
    ice_rate = np.zeros(base_intensity.shape, dtype=np.float32)
    valid = finite_intensity & positive_precip
    rain_rate[valid & is_rain] = base_intensity[valid & is_rain]
    snow_rate[valid & is_snow] = base_intensity[valid & is_snow]
    ice_rate[valid & is_ice] = base_intensity[valid & is_ice]
    rain_rate[~finite_intensity] = np.nan
    snow_rate[~finite_intensity] = np.nan
    ice_rate[~finite_intensity] = np.nan
    return base_intensity, rain_rate, snow_rate, ice_rate


def _ptype_intensity_gfs_family_cache_key(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    hints: dict[str, Any],
    use_warped: bool,
    target_region: str,
    target_grid_id: str,
    resampling: str,
) -> tuple[str, str, str, int, str, str, str]:
    run_date_utc = run_date.astimezone(timezone.utc) if run_date.tzinfo else run_date.replace(tzinfo=timezone.utc)
    component_identity = (
        str(hints.get("prate_component") or "prate"),
        str(hints.get("rain_component") or "crain"),
        str(hints.get("snow_component") or "csnow"),
        str(hints.get("sleet_component") or "cicep"),
        str(hints.get("frzr_component") or "cfrzr"),
        str(hints.get("surface_temp_component") or "tmp2m"),
        str(hints.get("mid_temp_component") or "tmp850"),
        str(hints.get("apcp_component") or "apcp_step"),
        str(hints.get("apcp_product") or ""),
    )
    if use_warped:
        scope = f"warped:{target_grid_id}:{resampling}"
    else:
        scope = f"raw:{target_region}"
    return (
        str(model_id),
        str(product),
        run_date_utc.isoformat(),
        int(fh),
        repr(component_identity),
        str(scope),
        "gfs_ptype_family_v1",
    )


def _ptype_intensity_index_from_gfs_family_rates(
    *,
    rain_rate: np.ndarray,
    snow_rate: np.ndarray,
    ice_rate: np.ndarray,
) -> np.ndarray:
    family_rate_by_code = {
        "rain": rain_rate,
        "snow": snow_rate,
        "ice": ice_rate,
    }
    family_stack = np.stack([ice_rate, snow_rate, rain_rate], axis=0).astype(np.float32, copy=False)
    family_idx = np.argmax(np.nan_to_num(family_stack, nan=-1.0), axis=0).astype(np.int32)
    family_codes = np.array(["ice", "snow", "rain"])
    ptype = family_codes[family_idx]
    has_any_ptype = (
        np.isfinite(rain_rate)
        & (
            (np.nan_to_num(rain_rate, nan=0.0) > 0.0)
            | (np.nan_to_num(snow_rate, nan=0.0) > 0.0)
            | (np.nan_to_num(ice_rate, nan=0.0) > 0.0)
        )
    )

    # The competitor's ptype snow shading is slightly amplified relative to the
    # liquid-equivalent step accumulation base. Keep that as a modest display-only
    # bias rather than a large arbitrary boost.
    snow_display_boost = np.float32(2.0)
    type_levels = {
        "rain": np.asarray([0.0, 0.01, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0], dtype=np.float32),
        "snow": np.asarray([0.05, 0.25, 0.50, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0], dtype=np.float32),
        "ice": np.asarray([0.01, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0, 1.25, 1.5, 2.0], dtype=np.float32),
    }
    type_breaks = {
        "rain": {"offset": 0, "count": 16},
        "snow": {"offset": 16, "count": 10},
        "ice": {"offset": 26, "count": 18},
    }
    min_visible = {"rain": 0.01, "snow": 0.01, "ice": 0.01}

    indexed = np.full(rain_rate.shape, np.nan, dtype=np.float32)
    for code in ("rain", "snow", "ice"):
        levels = type_levels[code]
        offset = int(type_breaks[code]["offset"])
        count = int(type_breaks[code]["count"])
        family_rate = family_rate_by_code[code]
        display_rate = family_rate
        if code == "snow":
            display_rate = (snow_display_boost * np.nan_to_num(family_rate, nan=0.0)).astype(np.float32, copy=False)
        selector = (
            (ptype == code)
            & np.isfinite(family_rate)
            & (family_rate >= float(min_visible[code]))
            & has_any_ptype
        )
        if not np.any(selector):
            continue
        local_bin = np.digitize(display_rate[selector], levels, right=False) - 1
        local_bin = np.clip(local_bin, 0, count - 1)
        indexed[selector] = (offset + local_bin).astype(np.float32)
    return indexed


def _derive_ptype_intensity_gfs_family(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    model_plugin: Any,
    ctx: FetchContext | None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> dict[str, Any]:
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    if not isinstance(hints, dict):
        hints = {}
    use_warped, target_region, target_grid_id, resampling = _resolve_warped_state(
        derive_component_target_grid,
        derive_component_resampling,
        model_id,
    )
    cache_key = _ptype_intensity_gfs_family_cache_key(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        hints=hints,
        use_warped=use_warped,
        target_region=target_region,
        target_grid_id=target_grid_id,
        resampling=resampling,
    )
    if ctx is not None:
        cached = ctx.ptype_family_cache.get(cache_key)
        if cached is not None:
            logger.info("ptype_intensity family cache hit: model=%s fh=%03d", model_id, fh)
            return cached

    prate_id = hints.get("prate_component", "prate")
    rain_id = hints.get("rain_component", "crain")
    snow_id = hints.get("snow_component", "csnow")
    sleet_id = hints.get("sleet_component", "cicep")
    frzr_id = hints.get("frzr_component", "cfrzr")

    prate, src_crs, src_transform = _fetch_step_component(
        model_id=model_id,
        product=product,
        run_date=run_date,
        step_fh=fh,
        model_plugin=model_plugin,
        var_key=prate_id,
        use_warped=use_warped,
        target_region=target_region,
        target_grid_id=target_grid_id,
        resampling=resampling,
        ctx=ctx,
    )
    component_fetch_kwargs = {
        "model_id": model_id,
        "product": product,
        "run_date": run_date,
        "step_fh": fh,
        "model_plugin": model_plugin,
        "use_warped": use_warped,
        "target_region": target_region,
        "target_grid_id": target_grid_id,
        "resampling": resampling,
        "ctx": ctx,
    }
    rain, _, _ = _fetch_step_component(**component_fetch_kwargs, var_key=rain_id)
    snow, _, _ = _fetch_step_component(**component_fetch_kwargs, var_key=snow_id)
    sleet, _, _ = _fetch_step_component(**component_fetch_kwargs, var_key=sleet_id)
    frzr, _, _ = _fetch_step_component(**component_fetch_kwargs, var_key=frzr_id)
    cold_profile, warm_profile = _ptype_intensity_thermal_fields(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        model_plugin=model_plugin,
        ctx=ctx,
        hints=hints,
        expected_shape=prate.shape,
        use_warped=use_warped,
        target_region=target_region,
        target_grid_id=target_grid_id,
        resampling=resampling,
    )

    intensity_rate = _ptype_intensity_fetch_step_intensity(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        model_plugin=model_plugin,
        ctx=ctx,
        hints=hints,
        expected_shape=prate.shape,
        use_warped=use_warped,
        target_region=target_region,
        target_grid_id=target_grid_id,
        resampling=resampling,
    )
    if intensity_rate is None:
        # prate is instantaneous (kg/m²/s). Convert to approximate step-
        # equivalent inches so the fallback is in the same units as the
        # APCP-derived path. Assume a 3-hour window.
        step_seconds = np.float32(3.0 * 3600.0)
        inch_scale = np.float32(0.03937007874015748)
        prate_arr = np.asarray(prate, dtype=np.float32)
        intensity_rate = np.where(
            np.isfinite(prate_arr) & (prate_arr >= 0.0),
            prate_arr * step_seconds * inch_scale,
            np.nan,
        ).astype(np.float32, copy=False)

    _, rain_rate, snow_rate, ice_rate = _ptype_intensity_family_rates(
        intensity=intensity_rate,
        rain=rain,
        snow=snow,
        sleet=sleet,
        frzr=frzr,
        cold_profile=cold_profile,
        warm_profile=warm_profile,
    )
    indexed = _ptype_intensity_index_from_gfs_family_rates(
        rain_rate=rain_rate,
        snow_rate=snow_rate,
        ice_rate=ice_rate,
    )
    snow_display = (2.0 * np.nan_to_num(snow_rate, nan=0.0)).astype(np.float32, copy=False)
    snow_display[~np.isfinite(prate)] = np.nan

    family = {
        "indexed": indexed.astype(np.float32, copy=False),
        "rain": rain_rate.astype(np.float32, copy=False),
        "snow": snow_display.astype(np.float32, copy=False),
        "ice": ice_rate.astype(np.float32, copy=False),
        "src_crs": src_crs,
        "src_transform": src_transform,
    }
    if ctx is not None:
        ctx.ptype_family_cache[cache_key] = family
    return family


def _apply_kuchera_ptype_gate(apcp_step: np.ndarray, frozen_frac: np.ndarray) -> np.ndarray:
    if apcp_step.shape != frozen_frac.shape:
        raise ValueError(f"kuchera ptype gate shape mismatch: {apcp_step.shape} != {frozen_frac.shape}")
    frozen = np.clip(np.asarray(frozen_frac, dtype=np.float32), 0.0, 1.0).astype(np.float32, copy=False)
    return (np.asarray(apcp_step, dtype=np.float32) * frozen).astype(np.float32, copy=False)


def _log_kuchera_ptype_gate_warning_once(*, model_id: str, var_key: str, step_fh: int, reason: str) -> None:
    global _KUCHERA_PTYPE_GATE_LAST_WARN_TS
    now = time.monotonic()
    should_log = False
    with _KUCHERA_PTYPE_GATE_WARN_LOCK:
        if now - _KUCHERA_PTYPE_GATE_LAST_WARN_TS >= _KUCHERA_PTYPE_GATE_WARN_INTERVAL_SECONDS:
            _KUCHERA_PTYPE_GATE_LAST_WARN_TS = now
            should_log = True
    if should_log:
        logger.warning(
            "kuchera_ptype_gate fallback=ones model=%s var=%s step_fh=%03d reason=%s",
            model_id,
            var_key,
            int(step_fh),
            reason,
        )


def _kuchera_frozen_fraction_for_step(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    step_fh: int,
    sample_fhs: list[int] | None,
    model_plugin: Any,
    use_warped: bool,
    target_region: str,
    target_grid_id: str,
    resampling: str,
    ctx: FetchContext | None,
    expected_shape: tuple[int, ...],
) -> tuple[np.ndarray, bool, int]:
    component_keys = ("csnow",)
    fetch_count = 0
    resolved_sample_fhs = list(sample_fhs or [int(step_fh)])
    sample_frozen_fracs: list[np.ndarray] = []
    sample_errors: list[str] = []

    for sample_fh in resolved_sample_fhs:
        fetched: dict[str, np.ndarray] = {}
        sample_failed = False
        for key in component_keys:
            try:
                component_data, _, _ = _fetch_step_component(
                    model_id=model_id,
                    product=product,
                    run_date=run_date,
                    step_fh=int(sample_fh),
                    model_plugin=model_plugin,
                    var_key=key,
                    use_warped=use_warped,
                    target_region=target_region,
                    target_grid_id=target_grid_id,
                    resampling=resampling,
                    ctx=ctx,
                )
            except Exception as exc:
                sample_errors.append(f"fh{int(sample_fh):03d}:{exc}")
                sample_failed = True
                break
            fetch_count += 1
            component_clean = np.asarray(component_data, dtype=np.float32)
            if component_clean.shape != expected_shape:
                raise ValueError(
                    f"kuchera ptype component shape mismatch for {key}: "
                    f"{component_clean.shape} != {expected_shape}"
                )
            fetched[key] = component_clean

        if sample_failed:
            continue

        csnow_prob = _normalize_ptype_probability(fetched["csnow"])
        sample_frozen_fracs.append(csnow_prob.astype(np.float32, copy=False))

    if not sample_frozen_fracs:
        reason = sample_errors[0] if sample_errors else "no_valid_samples"
        _log_kuchera_ptype_gate_warning_once(
            model_id=model_id,
            var_key=var_key,
            step_fh=step_fh,
            reason=reason,
        )
        return np.ones(expected_shape, dtype=np.float32), True, fetch_count

    if len(sample_frozen_fracs) == 1:
        return sample_frozen_fracs[0], False, fetch_count

    sample_stack = np.stack(sample_frozen_fracs, axis=0).astype(np.float32, copy=False)
    sample_valid_counts = np.sum(np.isfinite(sample_stack), axis=0).astype(np.int32, copy=False)
    sample_sum = np.nansum(sample_stack, axis=0).astype(np.float32, copy=False)
    frozen_frac = np.full(expected_shape, np.nan, dtype=np.float32)
    np.divide(
        sample_sum,
        sample_valid_counts.astype(np.float32, copy=False),
        out=frozen_frac,
        where=sample_valid_counts > 0,
    )
    frozen_frac = np.clip(frozen_frac, 0.0, 1.0).astype(np.float32, copy=False)
    return frozen_frac, False, fetch_count


@overload
def _fetch_component(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    model_plugin: Any,
    var_key: str,
    ctx: FetchContext | None = ...,
    return_meta: Literal[False] = ...,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]: ...


@overload
def _fetch_component(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    model_plugin: Any,
    var_key: str,
    ctx: FetchContext | None = ...,
    return_meta: Literal[True],
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]]: ...


def _fetch_component(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    model_plugin: Any,
    var_key: str,
    ctx: FetchContext | None = None,
    return_meta: bool = False,
) -> (
    tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]
    | tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]]
):
    normalized_var_key, selectors = _resolve_component_var(model_plugin, var_key)
    run_date_utc = run_date.astimezone(timezone.utc) if run_date.tzinfo else run_date.replace(tzinfo=timezone.utc)
    cache_key = (
        str(model_id),
        str(product),
        run_date_utc.isoformat(),
        int(fh),
        str(normalized_var_key),
        _selector_fingerprint(selectors),
        str(getattr(ctx, "coverage", "") if ctx is not None else ""),
        str(getattr(model_plugin, "coverage", "")),
    )
    if ctx is not None and cache_key in ctx.fetch_cache:
        _record_fetch_stat(ctx, "hits")
        cached = ctx.fetch_cache[cache_key]
        if return_meta:
            cached_meta = dict(ctx.fetch_meta_cache.get(cache_key, {}))
            return cached[0], cached[1], cached[2], cached_meta
        return cached

    search_patterns = model_plugin.search_patterns_for_var(
        var_key=normalized_var_key,
        fh=fh,
        product=product,
        var_spec=getattr(model_plugin, "get_var", lambda _key: None)(normalized_var_key),
    )
    if not search_patterns:
        search_patterns = [str(pattern) for pattern in selectors.search if str(pattern).strip()]

    last_exc: Exception | None = None
    for search_pattern in search_patterns:
        try:
            request = model_plugin.herbie_request(
                product=product,
                var_key=normalized_var_key,
                run_date=run_date,
                fh=fh,
                search_pattern=search_pattern,
            )
            fetch_kwargs: dict[str, Any] = {}
            if ctx is not None and getattr(ctx, "bundle_fetch_cache", None) is not None:
                fetch_kwargs["bundle_fetch_cache"] = getattr(ctx, "bundle_fetch_cache")
            fetch_result = fetch_variable(
                model_id=request.model,
                product=request.product,
                search_pattern=search_pattern,
                run_date=run_date,
                fh=fh,
                herbie_kwargs=getattr(request, "herbie_kwargs", None),
                **fetch_kwargs,
                return_meta=True,
            )
            data, crs, transform, meta = fetch_result
            resolved = data.astype(np.float32, copy=False), crs, transform
            if ctx is not None:
                ctx.fetch_cache[cache_key] = resolved
                ctx.fetch_meta_cache[cache_key] = dict(meta)
                _record_fetch_stat(ctx, "misses")
            if return_meta:
                return resolved[0], resolved[1], resolved[2], dict(meta)
            return resolved
        except (HerbieTransientUnavailableError, RuntimeError) as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    raise ValueError(f"Component var {normalized_var_key!r} has no usable search patterns")


def _fetch_component_warped(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    model_plugin: Any,
    var_key: str,
    target_region: str,
    target_grid_id: str,
    resampling: str,
    ctx: FetchContext | None = None,
    return_meta: bool = False,
) -> (
    tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]
    | tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]]
):
    normalized_var_key, selector_fingerprint = _resolve_component_cache_identity(model_plugin, var_key)
    run_date_utc = run_date.astimezone(timezone.utc) if run_date.tzinfo else run_date.replace(tzinfo=timezone.utc)
    cache_key = (
        str(model_id),
        str(product),
        run_date_utc.isoformat(),
        int(fh),
        str(normalized_var_key),
        str(selector_fingerprint),
        str(target_grid_id),
        str(resampling),
    )
    if ctx is not None and cache_key in ctx.warp_cache:
        _record_warp_stat(ctx, "hits")
        cached = ctx.warp_cache[cache_key]
        if return_meta:
            cached_meta = dict(ctx.warp_meta_cache.get(cache_key, {}))
            return cached[0], cached[1], cached[2], cached_meta
        return cached

    raw_data, raw_crs, raw_transform, raw_meta = _fetch_component(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        model_plugin=model_plugin,
        var_key=normalized_var_key,
        ctx=ctx,
        return_meta=True,
    )
    warped_data, dst_transform = _warp_component_to_target_grid(
        raw_data=raw_data,
        raw_crs=raw_crs,
        raw_transform=raw_transform,
        model_id=model_id,
        target_region=target_region,
        target_grid_id=target_grid_id,
        resampling=resampling,
    )
    resolved = (
        warped_data.astype(np.float32, copy=False),
        rasterio.crs.CRS.from_epsg(3857),
        dst_transform,
    )
    if ctx is not None:
        ctx.warp_cache[cache_key] = resolved
        ctx.warp_meta_cache[cache_key] = dict(raw_meta)
        _record_warp_stat(ctx, "misses")
    if return_meta:
        return resolved[0], resolved[1], resolved[2], dict(raw_meta)
    return resolved


def get_cached_warped_component(
    *,
    ctx: FetchContext | None,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    model_plugin: Any,
    var_key: str,
    target_grid_id: str,
    resampling: str,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine] | None:
    """Return a previously warped component from a shared derive context."""
    if ctx is None:
        return None
    normalized_var_key, selector_fingerprint = _resolve_component_cache_identity(model_plugin, var_key)
    run_date_utc = run_date.astimezone(timezone.utc) if run_date.tzinfo else run_date.replace(tzinfo=timezone.utc)
    cache_key = (
        str(model_id),
        str(product),
        run_date_utc.isoformat(),
        int(fh),
        str(normalized_var_key),
        str(selector_fingerprint),
        str(target_grid_id),
        str(resampling),
    )
    cached = ctx.warp_cache.get(cache_key)
    if cached is None:
        return None
    _record_warp_stat(ctx, "hits")
    return cached


def _warp_component_to_target_grid(
    *,
    raw_data: np.ndarray,
    raw_crs: Any,
    raw_transform: rasterio.transform.Affine,
    model_id: str,
    target_region: str,
    target_grid_id: str,
    resampling: str,
) -> tuple[np.ndarray, rasterio.transform.Affine]:
    target_grid_id_normalized = str(target_grid_id).strip()
    if target_grid_id_normalized.startswith("climatology:"):
        parts = target_grid_id_normalized.split(":", 3)
        if len(parts) == 4:
            _, baseline_source, baseline_region, grid_label = parts
            expected_bbox, expected_grid_m = get_baseline_grid_params(
                baseline_source=baseline_source,
                region=baseline_region,
            )
            expected_label = f"{expected_grid_m:.1f}m"
            if grid_label == expected_label:
                dst_transform, dst_h, dst_w = compute_transform_and_shape(expected_bbox, expected_grid_m)
                dst_crs = rasterio.crs.CRS.from_epsg(3857)
                dst_data = np.full((dst_h, dst_w), float("nan"), dtype=np.float32)
                reproject(
                    source=raw_data.astype(np.float64),
                    destination=dst_data,
                    src_transform=raw_transform,
                    src_crs=raw_crs,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    resampling=Resampling[resampling],
                    src_nodata=None,
                    dst_nodata=float("nan"),
                )
                return dst_data.astype(np.float32, copy=False), dst_transform

    return warp_to_target_grid(
        raw_data,
        raw_crs,
        raw_transform,
        model=model_id,
        region=target_region,
        resampling=resampling,
        src_nodata=None,
        dst_nodata=float("nan"),
    )


def _cadence_hint_suffix(hints: dict[str, Any]) -> str:
    parts: list[str] = []
    step_hours = hints.get("step_hours")
    transition = hints.get("step_transition_fh")
    after = hints.get("step_hours_after_fh")
    if step_hours is not None and str(step_hours).strip():
        parts.append(f"step_hours={step_hours}")
    if transition is not None and str(transition).strip():
        parts.append(f"transition={transition}")
    if after is not None and str(after).strip():
        parts.append(f"after={after}")
    return f" {' '.join(parts)}" if parts else ""


def _derive_uses_warped_components(
    derive_component_target_grid: dict[str, str] | None,
    derive_component_resampling: str | None,
) -> bool:
    if derive_component_target_grid is None:
        return False
    region = str(derive_component_target_grid.get("region", "")).strip()
    if not region:
        return False
    return isinstance(derive_component_resampling, str) and bool(derive_component_resampling.strip())


def _resolve_cumulative_step_fhs(
    *,
    hints: dict[str, Any],
    fh: int,
    run_date: datetime | None = None,
    default_step_hours: int = 6,
) -> list[int]:
    step_hours_raw = hints.get("step_hours", str(default_step_hours))
    step_transition_fh_raw = hints.get("step_transition_fh")
    step_hours_after_fh_raw = hints.get("step_hours_after_fh")

    try:
        step_hours = max(1, int(step_hours_raw))
    except (TypeError, ValueError):
        step_hours = default_step_hours
    try:
        step_transition_fh = int(step_transition_fh_raw) if step_transition_fh_raw is not None else None
    except (TypeError, ValueError):
        step_transition_fh = None
    try:
        step_hours_after_fh = int(step_hours_after_fh_raw) if step_hours_after_fh_raw is not None else None
    except (TypeError, ValueError):
        step_hours_after_fh = None
    if step_hours_after_fh is not None:
        step_hours_after_fh = max(1, step_hours_after_fh)
    align_after_transition_to_cycle = _parse_hint_bool(
        hints.get("step_hours_after_fh_align_to_cycle"),
        default=False,
    )

    if (
        step_transition_fh is not None
        and step_transition_fh > 0
        and step_hours_after_fh is not None
        and step_hours_after_fh > 0
    ):
        before_end = min(fh, step_transition_fh)
        step_fhs = list(range(step_hours, before_end + 1, step_hours))
        if fh > step_transition_fh:
            after_start = step_transition_fh + step_hours_after_fh
            if align_after_transition_to_cycle and run_date is not None:
                cycle_hour = int(run_date.hour)
                transition_mod = int(step_transition_fh) % int(step_hours_after_fh)
                cycle_mod = cycle_hour % int(step_hours_after_fh)
                offset = (cycle_mod - transition_mod) % int(step_hours_after_fh)
                after_start = int(step_transition_fh) + offset
                if after_start <= int(step_transition_fh):
                    after_start += int(step_hours_after_fh)
            step_fhs.extend(range(after_start, fh + 1, step_hours_after_fh))
        return step_fhs

    return list(range(step_hours, fh + 1, step_hours))


# ---------------------------------------------------------------------------
# Shared infrastructure for cumulative APCP strategies
# ---------------------------------------------------------------------------


def _resolve_warped_state(
    derive_component_target_grid: dict[str, str] | None,
    derive_component_resampling: str | None,
    model_id: str,
) -> tuple[bool, str, str, str]:
    """Resolve warped component state.

    Returns ``(use_warped, target_region, target_grid_id, resampling)``.
    """
    use_warped = _derive_uses_warped_components(
        derive_component_target_grid, derive_component_resampling,
    )
    target_region = (
        str((derive_component_target_grid or {}).get("region", "")).strip()
        if use_warped else ""
    )
    target_grid_id = (
        str((derive_component_target_grid or {}).get("id", "")).strip()
        if use_warped else ""
    )
    if use_warped and not target_grid_id:
        target_grid_id = f"{model_id}:{target_region}"
    resampling = str(derive_component_resampling).strip() if use_warped else ""
    return use_warped, target_region, target_grid_id, resampling


@overload
def _fetch_step_component(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    step_fh: int,
    model_plugin: Any,
    var_key: str,
    use_warped: bool,
    target_region: str,
    target_grid_id: str,
    resampling: str,
    ctx: FetchContext | None,
    return_meta: Literal[False] = ...,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]: ...


@overload
def _fetch_step_component(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    step_fh: int,
    model_plugin: Any,
    var_key: str,
    use_warped: bool,
    target_region: str,
    target_grid_id: str,
    resampling: str,
    ctx: FetchContext | None,
    return_meta: Literal[True],
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]]: ...


def _fetch_step_component(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    step_fh: int,
    model_plugin: Any,
    var_key: str,
    use_warped: bool,
    target_region: str,
    target_grid_id: str,
    resampling: str,
    ctx: FetchContext | None,
    return_meta: bool = False,
) -> (
    tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]
    | tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]]
):
    """Fetch a component for a step, branching warped vs raw."""
    if use_warped:
        return _fetch_component_warped(
            model_id=model_id, product=product, run_date=run_date, fh=step_fh,
            model_plugin=model_plugin, var_key=var_key,
            target_region=target_region, target_grid_id=target_grid_id,
            resampling=resampling, ctx=ctx, return_meta=return_meta,
        )
    return _fetch_component(
        model_id=model_id, product=product, run_date=run_date, fh=step_fh,
        model_plugin=model_plugin, var_key=var_key, ctx=ctx,
        return_meta=return_meta,
    )


def _is_valid_apcp_exact_result(data: Any, meta: dict[str, Any] | None) -> bool:
    """Check whether an inventory-selected APCP fetch returned usable data."""
    if not isinstance(data, np.ndarray):
        return False
    if data.size <= 0:
        return False
    if not np.isfinite(data).any():
        return False
    inventory_line = str((meta or {}).get("inventory_line", "")).strip()
    if not inventory_line:
        return False
    if _is_probabilistic_apcp_inventory_line(inventory_line):
        return False
    return True


@dataclass
class _ApcpCumDiffState:
    """Mutable state for cumulative-to-step APCP differencing across the loop."""
    consumed_sum: np.ndarray | None = None
    consumed_sum_valid: np.ndarray | None = None
    consumed_sum_crs: rasterio.crs.CRS | None = None
    consumed_sum_transform: rasterio.transform.Affine | None = None
    consumed_through_fh: int = 0
    bucket_start_fh: int | None = None
    bucket_cumulative_sum: np.ndarray | None = None
    bucket_cumulative_valid: np.ndarray | None = None
    bucket_cumulative_crs: rasterio.crs.CRS | None = None
    bucket_cumulative_transform: rasterio.transform.Affine | None = None
    bucket_through_fh: int = 0
    recent_exact_steps: dict[
        int,
        tuple[np.ndarray, np.ndarray, rasterio.crs.CRS | None, rasterio.transform.Affine | None],
    ] = field(default_factory=dict)


def _reconstruct_overlap_prior_sum(
    *,
    cum_diff_state: _ApcpCumDiffState,
    start_fh: int,
    through_fh: int,
) -> tuple[np.ndarray, np.ndarray, rasterio.crs.CRS | None, rasterio.transform.Affine | None] | None:
    if through_fh <= start_fh:
        return None

    reconstructed_sum: np.ndarray | None = None
    reconstructed_valid: np.ndarray | None = None
    reconstructed_crs: rasterio.crs.CRS | None = None
    reconstructed_transform: rasterio.transform.Affine | None = None

    for prior_fh in range(int(start_fh) + 1, int(through_fh) + 1):
        cached = cum_diff_state.recent_exact_steps.get(int(prior_fh))
        if cached is None:
            return None
        prior_data, prior_valid, prior_crs, prior_transform = cached
        if reconstructed_sum is None:
            reconstructed_sum = prior_data.copy()
            reconstructed_valid = prior_valid.copy()
            reconstructed_crs = prior_crs
            reconstructed_transform = prior_transform
            continue
        if prior_data.shape != reconstructed_sum.shape:
            return None
        if prior_crs != reconstructed_crs or prior_transform != reconstructed_transform:
            return None
        reconstructed_sum = (reconstructed_sum + prior_data).astype(np.float32, copy=False)
        assert reconstructed_valid is not None
        reconstructed_valid = reconstructed_valid & prior_valid

    if reconstructed_sum is None or reconstructed_valid is None:
        return None
    return reconstructed_sum, reconstructed_valid, reconstructed_crs, reconstructed_transform


def _seed_overlap_prior_bucket_window(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    model_plugin: Any,
    ctx: FetchContext | None,
    apcp_component: str,
    apcp_product: str | None,
    use_warped: bool,
    target_region: str,
    target_grid_id: str,
    resampling: str,
    start_fh: int,
    through_fh: int,
    cum_diff_state: _ApcpCumDiffState,
) -> tuple[np.ndarray, np.ndarray, rasterio.crs.CRS | None, rasterio.transform.Affine | None] | None:
    if through_fh <= start_fh:
        return None

    resolved_apcp_product = str(apcp_product or product)
    search_pattern = _apcp_exact_window_pattern(start_fh, through_fh)
    resolved_apcp_cache_key = _resolved_apcp_cache_key(
        model_id=model_id,
        product=resolved_apcp_product,
        run_date=run_date,
        step_fh=through_fh,
        model_plugin=model_plugin,
        apcp_component=apcp_component,
        expected_start_fh=start_fh,
        use_warped=use_warped,
        target_grid_id=target_grid_id,
        resampling=resampling,
        ctx=ctx,
    )

    step_data: np.ndarray | None = None
    step_crs: rasterio.crs.CRS | None = None
    step_transform: rasterio.transform.Affine | None = None
    apcp_meta: dict[str, Any] = {}

    if ctx is not None and resolved_apcp_cache_key is not None:
        resolved_cache = getattr(ctx, "resolved_apcp_cache", None)
        if isinstance(resolved_cache, dict):
            cached = resolved_cache.get(resolved_apcp_cache_key)
            if cached is not None:
                step_data, step_crs, step_transform, cached_meta = cached
                apcp_meta = dict(cached_meta)

    if step_data is None or step_crs is None or step_transform is None:
        try:
            step_data, step_crs, step_transform, apcp_meta = fetch_variable(
                model_id=model_id,
                product=resolved_apcp_product,
                search_pattern=search_pattern,
                run_date=run_date,
                fh=int(through_fh),
                herbie_kwargs={"priority": _kuchera_primary_herbie_priority()},
                return_meta=True,
            )
        except Exception:
            return None

    if use_warped:
        step_data, warped_transform = _warp_component_to_target_grid(
            raw_data=np.asarray(step_data, dtype=np.float32),
            raw_crs=step_crs,
            raw_transform=step_transform,
            model_id=model_id,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
        )
        step_crs = rasterio.crs.CRS.from_epsg(3857)
        step_transform = warped_transform

    inventory_line = str((apcp_meta or {}).get("inventory_line", search_pattern[:-1])).strip()
    window = _parse_apcp_accum_window_hours(inventory_line)
    if window is None or int(window[0]) != int(start_fh) or int(window[1]) != int(through_fh):
        return None

    step_valid = np.isfinite(step_data) & (step_data >= 0.0)
    step_clean = np.where(step_valid, step_data, 0.0).astype(np.float32, copy=False)

    cum_diff_state.bucket_start_fh = int(start_fh)
    cum_diff_state.bucket_cumulative_sum = step_clean.copy()
    cum_diff_state.bucket_cumulative_valid = step_valid.copy()
    cum_diff_state.bucket_cumulative_crs = step_crs
    cum_diff_state.bucket_cumulative_transform = step_transform
    cum_diff_state.bucket_through_fh = int(through_fh)

    if int(through_fh) == int(start_fh) + 1:
        cum_diff_state.recent_exact_steps[int(through_fh)] = (
            step_clean.copy(),
            step_valid.copy(),
            step_crs,
            step_transform,
        )
        prune_before_fh = int(through_fh) - 12
        for prior_fh in list(cum_diff_state.recent_exact_steps.keys()):
            if int(prior_fh) < prune_before_fh:
                cum_diff_state.recent_exact_steps.pop(int(prior_fh), None)

    if ctx is not None and resolved_apcp_cache_key is not None:
        resolved_cache = getattr(ctx, "resolved_apcp_cache", None)
        if not isinstance(resolved_cache, dict):
            resolved_cache = {}
            setattr(ctx, "resolved_apcp_cache", resolved_cache)
        cache_meta = dict(apcp_meta)
        cache_meta["selected_mode"] = "exact_step" if int(through_fh) == int(start_fh) + 1 else "overlap_window"
        cache_meta["selected_window"] = f"{int(start_fh)}-{int(through_fh)}"
        cache_meta["exact_guess_used"] = True
        cache_meta["inventory_selected"] = True
        cache_meta["search_pattern"] = str((cache_meta or {}).get("search_pattern", search_pattern))
        resolved_cache[resolved_apcp_cache_key] = (
            step_clean,
            step_crs,
            step_transform,
            cache_meta,
        )

    return step_clean, step_valid, step_crs, step_transform


def _resolve_apcp_step_data(
    *,
    step_fh: int,
    step_index: int,
    step_fhs: list[int],
    model_id: str,
    product: str,
    run_date: datetime,
    model_plugin: Any,
    ctx: FetchContext | None,
    apcp_component: str,
    apcp_product: str | None,
    use_warped: bool,
    target_region: str,
    target_grid_id: str,
    resampling: str,
    cum_diff_state: _ApcpCumDiffState,
    expected_start_fh_override: int | None = None,
    force_cumulative_from_zero: bool = False,
    skip_inventory_window_selection: bool = False,
) -> tuple[np.ndarray, np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, str]:
    """Resolve per-step APCP data with inventory-driven window selection.

    Tries, in order:
      1. FetchContext cache hit
      2. Exact-guess window in Herbie inventory
      3. Best available window ending at step_fh
      4. Component selector regex fallback

    Detects cumulative (0-N hour) windows and differences against the
    previous step's cumulative value (tracked in *cum_diff_state*).

    Returns ``(step_clean, apcp_valid, crs, transform, apcp_mode)``
    where
    *step_clean* is the cleaned per-step increment (>= 0, invalid → 0)
    and *apcp_valid* is the boolean validity mask.
    """
    if expected_start_fh_override is not None and step_index == 0:
        expected_start_fh = int(expected_start_fh_override)
    else:
        expected_start_fh = 0 if step_index == 0 else int(step_fhs[step_index - 1])
    resolved_apcp_product = str(apcp_product or product)
    apcp_search_pattern = _apcp_exact_window_pattern(expected_start_fh, step_fh)
    apcp_step: np.ndarray | None = None
    step_crs: rasterio.crs.CRS | None = None
    step_transform: rasterio.transform.Affine | None = None
    apcp_meta: dict[str, Any] = {}
    exact_guess_used = False
    inventory_selected = False
    selected_window = "none"
    selector_fallback_used = False
    selector_reason = "none"
    apcp_fetch_resolved = False
    selected_mode = "invalid"
    inventory_choice_mode = "invalid"
    resolved_apcp_cache_key = _resolved_apcp_cache_key(
        model_id=model_id,
        product=resolved_apcp_product,
        run_date=run_date,
        step_fh=step_fh,
        model_plugin=model_plugin,
        apcp_component=apcp_component,
        expected_start_fh=expected_start_fh,
        use_warped=use_warped,
        target_grid_id=target_grid_id,
        resampling=resampling,
        ctx=ctx,
    )

    if ctx is not None and resolved_apcp_cache_key is not None:
        resolved_cache = getattr(ctx, "resolved_apcp_cache", None)
        if isinstance(resolved_cache, dict):
            cached = resolved_cache.get(resolved_apcp_cache_key)
            if cached is not None:
                apcp_step, step_crs, step_transform, cached_meta = cached
                apcp_meta = dict(cached_meta)
                apcp_search_pattern = str((apcp_meta or {}).get("search_pattern", "")).strip() or apcp_search_pattern
                selected_window = str((apcp_meta or {}).get("selected_window", selected_window))
                exact_guess_used = bool((apcp_meta or {}).get("exact_guess_used", False))
                inventory_selected = bool((apcp_meta or {}).get("inventory_selected", False))
                selector_fallback_used = True
                selector_reason = "shared_apcp_cache_hit"
                selected_mode = str((apcp_meta or {}).get("selected_mode", "invalid"))
                apcp_fetch_resolved = True

    # 1. Check FetchContext cache.
    if not apcp_fetch_resolved and ctx is not None:
        run_date_utc = (
            run_date.astimezone(timezone.utc)
            if run_date.tzinfo else run_date.replace(tzinfo=timezone.utc)
        )
        try:
            apcp_cache_var_key, apcp_selector_fingerprint = _resolve_component_cache_identity(
                model_plugin, apcp_component,
            )
        except Exception:
            apcp_cache_var_key = None
            apcp_selector_fingerprint = None

        if apcp_cache_var_key is not None and apcp_selector_fingerprint is not None:
            if use_warped:
                warped_cache_key = (
                    str(model_id),
                    str(resolved_apcp_product),
                    run_date_utc.isoformat(),
                    int(step_fh),
                    str(apcp_cache_var_key),
                    str(apcp_selector_fingerprint),
                    str(target_grid_id),
                    str(resampling),
                )
                cached = ctx.warp_cache.get(warped_cache_key)
                if cached is not None:
                    cached_meta = dict(ctx.warp_meta_cache.get(warped_cache_key, {}))
                    cached_line = str(cached_meta.get("inventory_line", "")).strip()
                    cached_mode = _classify_apcp_mode_for_kuchera(
                        inventory_line=cached_line,
                        step_fh=step_fh,
                        expected_start_fh=expected_start_fh,
                    )
                    if cached_mode == "invalid" and force_cumulative_from_zero:
                        cached_mode = "cumulative_from_zero"
                    if cached_mode != "invalid":
                        _record_warp_stat(ctx, "hits")
                        apcp_step, step_crs, step_transform = cached
                        apcp_meta = cached_meta
                        apcp_search_pattern = str((apcp_meta or {}).get("search_pattern", "")).strip() or apcp_search_pattern
                        selector_fallback_used = True
                        selector_reason = "cache_hit"
                        selected_mode = cached_mode
                        apcp_fetch_resolved = True
            else:
                fetch_cache_key = (
                    str(model_id),
                    str(resolved_apcp_product),
                    run_date_utc.isoformat(),
                    int(step_fh),
                    str(apcp_cache_var_key),
                    str(apcp_selector_fingerprint),
                    str(getattr(ctx, "coverage", "")),
                    str(getattr(model_plugin, "coverage", "")),
                )
                cached = ctx.fetch_cache.get(fetch_cache_key)
                if cached is not None:
                    cached_meta = dict(ctx.fetch_meta_cache.get(fetch_cache_key, {}))
                    cached_line = str(cached_meta.get("inventory_line", "")).strip()
                    cached_mode = _classify_apcp_mode_for_kuchera(
                        inventory_line=cached_line,
                        step_fh=step_fh,
                        expected_start_fh=expected_start_fh,
                    )
                    if cached_mode == "invalid" and force_cumulative_from_zero:
                        cached_mode = "cumulative_from_zero"
                    if cached_mode != "invalid":
                        _record_fetch_stat(ctx, "hits")
                        apcp_step, step_crs, step_transform = cached
                        apcp_meta = cached_meta
                        apcp_search_pattern = str((apcp_meta or {}).get("search_pattern", "")).strip() or apcp_search_pattern
                        selector_fallback_used = True
                        selector_reason = "cache_hit"
                        selected_mode = cached_mode
                        apcp_fetch_resolved = True

    # 2. Inventory-driven APCP selection.
    if not apcp_fetch_resolved and not skip_inventory_window_selection:
        inventory_lines = _kuchera_inventory_lines(
            model_id=model_id,
            product=resolved_apcp_product,
            run_date=run_date,
            fh=step_fh,
            search_pattern=":APCP:surface:",
        )
        if not inventory_lines:
            selector_fallback_used = True
            selector_reason = "inventory_empty"
        else:
            inventory_choice = _kuchera_select_apcp_window_from_inventory(
                inventory_lines=inventory_lines,
                step_fh=step_fh,
                expected_start_fh=expected_start_fh,
            )
            if inventory_choice is not None:
                apcp_search_pattern = str(inventory_choice.get("search_pattern") or apcp_search_pattern)
                selected_window = str(inventory_choice.get("selected_window") or selected_window)
                inventory_choice_mode = str(inventory_choice.get("mode") or inventory_choice_mode)
                exact_guess_used = inventory_choice_mode == "exact_step"
                inventory_selected = inventory_choice_mode != "exact_step"
                selector_reason = (
                    "inventory_exact_match"
                    if inventory_choice_mode == "exact_step"
                    else "inventory_best_window"
                )
            else:
                selector_fallback_used = True
                selector_reason = "inventory_no_matching_window"

        if not apcp_fetch_resolved and (inventory_selected or exact_guess_used):
            try:
                fetch_kwargs: dict[str, Any] = {}
                if ctx is not None and getattr(ctx, "bundle_fetch_cache", None) is not None:
                    fetch_kwargs["bundle_fetch_cache"] = getattr(ctx, "bundle_fetch_cache")
                selected_data, selected_crs, selected_transform, selected_meta = fetch_variable(
                    model_id=model_id,
                    product=resolved_apcp_product,
                    search_pattern=apcp_search_pattern,
                    run_date=run_date,
                    fh=step_fh,
                    **fetch_kwargs,
                    return_meta=True,
                )
                selected_data = selected_data.astype(np.float32, copy=False)
                selected_meta = dict(selected_meta)

                if use_warped:
                    warped_data, warped_transform = _warp_component_to_target_grid(
                        raw_data=selected_data,
                        raw_crs=selected_crs,
                        raw_transform=selected_transform,
                        model_id=model_id,
                        target_region=target_region,
                        target_grid_id=target_grid_id,
                        resampling=resampling,
                    )
                    selected_data = warped_data.astype(np.float32, copy=False)
                    selected_crs = rasterio.crs.CRS.from_epsg(3857)
                    selected_transform = warped_transform

                if _is_valid_apcp_exact_result(selected_data, selected_meta):
                    apcp_step = selected_data
                    step_crs = selected_crs
                    step_transform = selected_transform
                    apcp_meta = selected_meta
                    selected_mode = inventory_choice_mode
                    apcp_fetch_resolved = True
                else:
                    selector_fallback_used = True
                    selector_reason = f"{selector_reason}_invalid_result"
            except Exception as exc:
                selector_fallback_used = True
                selector_reason = f"{selector_reason}_error:{exc.__class__.__name__}"

    # 3. Fallback to component selector regex.
    if not apcp_fetch_resolved:
        apcp_step, step_crs, step_transform, apcp_meta = _fetch_step_component(
            model_id=model_id,
            product=resolved_apcp_product,
            run_date=run_date,
            step_fh=step_fh,
            model_plugin=model_plugin,
            var_key=apcp_component,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            ctx=ctx,
            return_meta=True,
        )
        apcp_meta = dict(apcp_meta)
        apcp_search_pattern = str((apcp_meta or {}).get("search_pattern", "")).strip() or apcp_search_pattern
        selector_fallback_used = True
        if selector_reason == "none":
            selector_reason = "selector_regex_fallback"

    if (
        ctx is not None
        and resolved_apcp_cache_key is not None
        and apcp_fetch_resolved
        and apcp_step is not None
        and step_crs is not None
        and step_transform is not None
    ):
        resolved_cache = getattr(ctx, "resolved_apcp_cache", None)
        if not isinstance(resolved_cache, dict):
            resolved_cache = {}
            setattr(ctx, "resolved_apcp_cache", resolved_cache)
        cache_meta = dict(apcp_meta)
        cache_meta["selected_mode"] = selected_mode
        cache_meta["selected_window"] = selected_window
        cache_meta["exact_guess_used"] = exact_guess_used
        cache_meta["inventory_selected"] = inventory_selected
        resolved_cache[resolved_apcp_cache_key] = (
            apcp_step.astype(np.float32, copy=False),
            step_crs,
            step_transform,
            cache_meta,
        )

    # 4. Classify mode and apply cumulative differencing.
    assert apcp_step is not None  # guaranteed set by steps 1/2/3 above
    apcp_valid_raw = np.isfinite(apcp_step) & (apcp_step >= 0.0)
    apcp_cum_clean = np.where(apcp_valid_raw, apcp_step, 0.0).astype(np.float32, copy=False)

    apcp_inventory_line = str((apcp_meta or {}).get("inventory_line", "")).strip()
    apcp_mode = _classify_apcp_mode_for_kuchera(
        inventory_line=apcp_inventory_line,
        step_fh=step_fh,
        expected_start_fh=expected_start_fh,
    )
    if apcp_mode == "invalid" and selected_mode != "invalid":
        apcp_mode = selected_mode
    if apcp_mode == "invalid" and force_cumulative_from_zero:
        apcp_mode = "cumulative_from_zero"

    step_apcp_data = apcp_cum_clean
    apcp_valid = apcp_valid_raw
    fallback_differencing_applied = False
    window = _parse_apcp_accum_window_hours(apcp_inventory_line)

    if apcp_mode == "cumulative_from_zero" and cum_diff_state.consumed_sum is not None:
        same_shape = apcp_cum_clean.shape == cum_diff_state.consumed_sum.shape
        same_crs = step_crs == cum_diff_state.consumed_sum_crs
        same_transform = step_transform == cum_diff_state.consumed_sum_transform
        if not (same_shape and same_crs and same_transform):
            raise ValueError(
                f"APCP_STEP_RESOLUTION cumulative grid mismatch for fh{step_fh:03d}: "
                f"shape_match={same_shape} crs_match={same_crs} transform_match={same_transform}"
            )
        step_apcp_data = np.clip(
            apcp_cum_clean - cum_diff_state.consumed_sum, 0.0, None,
        ).astype(np.float32, copy=False)
        if cum_diff_state.consumed_sum_valid is not None:
            apcp_valid = apcp_valid_raw & cum_diff_state.consumed_sum_valid
        fallback_differencing_applied = True
        logger.info(
            'APCP_STEP_FALLBACK step_fh=%d prev_fh=%d reason="cumulative 0-%d"',
            step_fh,
            cum_diff_state.consumed_through_fh,
            step_fh,
        )
    elif apcp_mode == "overlap_window":
        assert window is not None
        bucket_state_available = (
            cum_diff_state.bucket_start_fh is not None
            and int(cum_diff_state.bucket_start_fh) == int(window[0])
            and int(cum_diff_state.bucket_through_fh) == int(expected_start_fh)
            and cum_diff_state.bucket_cumulative_sum is not None
        )
        if bucket_state_available:
            same_shape = apcp_cum_clean.shape == cum_diff_state.bucket_cumulative_sum.shape
            same_crs = step_crs == cum_diff_state.bucket_cumulative_crs
            same_transform = step_transform == cum_diff_state.bucket_cumulative_transform
            if not (same_shape and same_crs and same_transform):
                raise ValueError(
                    f"APCP_STEP_RESOLUTION overlap grid mismatch for fh{step_fh:03d}: "
                    f"shape_match={same_shape} crs_match={same_crs} transform_match={same_transform}"
                )
            step_apcp_data = np.clip(
                apcp_cum_clean - cum_diff_state.bucket_cumulative_sum,
                0.0,
                None,
            ).astype(np.float32, copy=False)
            if cum_diff_state.bucket_cumulative_valid is not None:
                apcp_valid = apcp_valid_raw & cum_diff_state.bucket_cumulative_valid
            fallback_differencing_applied = True
            logger.info(
                'APCP_STEP_FALLBACK step_fh=%d prev_fh=%d reason="overlap %s"',
                step_fh,
                cum_diff_state.bucket_through_fh,
                selected_window,
            )
        else:
            reconstructed = _reconstruct_overlap_prior_sum(
                cum_diff_state=cum_diff_state,
                start_fh=int(window[0]),
                through_fh=int(expected_start_fh),
            )
            if (
                reconstructed is None
                and int(expected_start_fh) > int(window[0])
                and int(cum_diff_state.consumed_through_fh) >= int(expected_start_fh)
            ):
                seeded_prior = _seed_overlap_prior_bucket_window(
                    model_id=model_id,
                    product=product,
                    run_date=run_date,
                    model_plugin=model_plugin,
                    ctx=ctx,
                    apcp_component=apcp_component,
                    apcp_product=apcp_product,
                    use_warped=use_warped,
                    target_region=target_region,
                    target_grid_id=target_grid_id,
                    resampling=resampling,
                    start_fh=int(window[0]),
                    through_fh=int(expected_start_fh),
                    cum_diff_state=cum_diff_state,
                )
                if seeded_prior is not None:
                    reconstructed = seeded_prior
            if reconstructed is not None:
                prior_sum, prior_valid, prior_crs, prior_transform = reconstructed
                same_shape = apcp_cum_clean.shape == prior_sum.shape
                same_crs = step_crs == prior_crs
                same_transform = step_transform == prior_transform
                if not (same_shape and same_crs and same_transform):
                    raise ValueError(
                        f"APCP_STEP_RESOLUTION reconstructed overlap grid mismatch for fh{step_fh:03d}: "
                        f"shape_match={same_shape} crs_match={same_crs} transform_match={same_transform}"
                    )
                step_apcp_data = np.clip(
                    apcp_cum_clean - prior_sum,
                    0.0,
                    None,
                ).astype(np.float32, copy=False)
                apcp_valid = apcp_valid_raw & prior_valid
                fallback_differencing_applied = True
                logger.info(
                    'APCP_STEP_FALLBACK step_fh=%d prev_fh=%d reason="overlap_reconstructed %s"',
                    step_fh,
                    expected_start_fh,
                    selected_window,
                )
            elif cum_diff_state.consumed_through_fh <= 0:
                selector_reason = "history_gap_overlap_rebuild"
            else:
                raise ValueError(
                    f"APCP_STEP_RESOLUTION overlap state missing for fh{step_fh:03d}: "
                    f"expected_start={expected_start_fh} selected_window={selected_window}"
                )

    log_mode = {
        "exact_step": "step",
        "cumulative_from_zero": "cumulative",
        "overlap_window": "overlap",
        "invalid": "invalid",
    }.get(apcp_mode, apcp_mode)

    logger.info(
        'APCP_STEP_RESOLUTION step_fh=%d product=%s inv="%s" mode=%s fallback=%s '
        'exact_guess_used=%s inventory_selected=%s selected_window="%s" selector_fallback=%s '
        'reason="%s" pattern="%s"',
        step_fh,
        apcp_product or product,
        apcp_inventory_line.replace('"', "'"),
        log_mode,
        "true" if fallback_differencing_applied else "false",
        "true" if exact_guess_used else "false",
        "true" if inventory_selected else "false",
        selected_window,
        "true" if selector_fallback_used else "false",
        selector_reason.replace('"', "'"),
        apcp_search_pattern.replace('"', "'"),
    )
    _log_fetch_context_memory(
        label="apcp_step_resolved",
        ctx=ctx,
        model_id=model_id,
        var_key=apcp_component,
        fh=step_fh,
        step_fh=step_fh,
        extra=(
            f"mode={apcp_mode} selected_window={selected_window} inventory_selected={'true' if inventory_selected else 'false'} "
            f"selector_fallback={'true' if selector_fallback_used else 'false'}"
        ),
    )

    # 5. Advance consumed-sum tracking for all modes.
    increment_for_sum = np.where(apcp_valid, step_apcp_data, 0.0).astype(np.float32, copy=False)
    if cum_diff_state.consumed_sum is None:
        cum_diff_state.consumed_sum = increment_for_sum.copy()
        cum_diff_state.consumed_sum_valid = apcp_valid.copy()
        cum_diff_state.consumed_sum_crs = step_crs
        cum_diff_state.consumed_sum_transform = step_transform
    else:
        same_shape = increment_for_sum.shape == cum_diff_state.consumed_sum.shape
        same_crs = step_crs == cum_diff_state.consumed_sum_crs
        same_transform = step_transform == cum_diff_state.consumed_sum_transform
        if not (same_shape and same_crs and same_transform):
            raise ValueError(
                f"APCP_STEP_RESOLUTION consumed-sum grid mismatch for fh{step_fh:03d}: "
                f"shape_match={same_shape} crs_match={same_crs} transform_match={same_transform}"
            )
        cum_diff_state.consumed_sum = (
            cum_diff_state.consumed_sum + increment_for_sum
        ).astype(np.float32, copy=False)
        if cum_diff_state.consumed_sum_valid is not None:
            cum_diff_state.consumed_sum_valid = cum_diff_state.consumed_sum_valid & apcp_valid
        else:
            cum_diff_state.consumed_sum_valid = apcp_valid.copy()

    if window is not None and int(window[0]) > 0 and apcp_mode in {"exact_step", "overlap_window"}:
        start_hour = int(window[0])
        cum_diff_state.bucket_start_fh = start_hour
        cum_diff_state.bucket_cumulative_sum = apcp_cum_clean.copy()
        cum_diff_state.bucket_cumulative_valid = apcp_valid_raw.copy()
        cum_diff_state.bucket_cumulative_crs = step_crs
        cum_diff_state.bucket_cumulative_transform = step_transform
        cum_diff_state.bucket_through_fh = int(step_fh)
    elif apcp_mode == "cumulative_from_zero":
        cum_diff_state.bucket_start_fh = None
        cum_diff_state.bucket_cumulative_sum = None
        cum_diff_state.bucket_cumulative_valid = None
        cum_diff_state.bucket_cumulative_crs = None
        cum_diff_state.bucket_cumulative_transform = None
        cum_diff_state.bucket_through_fh = int(step_fh)

    if apcp_mode in {"exact_step", "overlap_window"}:
        cum_diff_state.recent_exact_steps[int(step_fh)] = (
            increment_for_sum.copy(),
            apcp_valid.copy(),
            step_crs,
            step_transform,
        )
        prune_before_fh = int(step_fh) - 12
        for prior_fh in list(cum_diff_state.recent_exact_steps.keys()):
            if int(prior_fh) < prune_before_fh:
                cum_diff_state.recent_exact_steps.pop(int(prior_fh), None)

    cum_diff_state.consumed_through_fh = int(step_fh)
    assert step_crs is not None  # guaranteed set by steps 1/2/3 above
    assert step_transform is not None  # guaranteed set by steps 1/2/3 above
    return step_apcp_data, apcp_valid, step_crs, step_transform, apcp_mode


def _cumulative_apcp_loop(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    step_fhs: list[int],
    model_plugin: Any,
    ctx: FetchContext | None,
    apcp_component: str,
    apcp_product: str | None,
    use_warped: bool,
    target_region: str,
    target_grid_id: str,
    resampling: str,
    use_inventory_resolution: bool,
    process_step: Callable[
        [int, np.ndarray, "np.ndarray | None", rasterio.crs.CRS, rasterio.transform.Affine],
        tuple[np.ndarray, np.ndarray],
    ],
    error_label: str,
    first_step_expected_start_fh: int | None = None,
    initial_apcp_cumulative: tuple[
        np.ndarray,
        np.ndarray,
        rasterio.crs.CRS,
        rasterio.transform.Affine,
        int,
    ] | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, bool]:
    """Shared cumulative APCP accumulation loop.

    For each forecast step:
      1. Fetch APCP via simple fetch or inventory-driven resolution.
      2. Call *process_step(step_fh, step_data, apcp_valid, crs, transform)*
         which returns ``(contribution, step_valid)``.
      3. Accumulate *contribution*, merge *step_valid*.

    *process_step* receives ``apcp_valid=None`` for the simple fetch path
    (the callback determines validity from raw data) and a boolean mask for
    the inventory path (pre-cleaned, post-differencing).

    Returns ``(cumulative, crs, transform, cumulative_fallback_used)``
    with NaN at invalid pixels.
    """
    cum_diff_state = _ApcpCumDiffState() if use_inventory_resolution else None
    if use_inventory_resolution and cum_diff_state is not None and initial_apcp_cumulative is not None:
        (
            seed_data,
            seed_valid,
            seed_crs,
            seed_transform,
            seed_fh,
        ) = initial_apcp_cumulative
        cum_diff_state.consumed_sum = np.asarray(seed_data, dtype=np.float32)
        cum_diff_state.consumed_sum_valid = np.asarray(seed_valid, dtype=bool)
        cum_diff_state.consumed_sum_crs = seed_crs
        cum_diff_state.consumed_sum_transform = seed_transform
        cum_diff_state.consumed_through_fh = int(seed_fh)

    cumulative: np.ndarray | None = None
    valid_mask: np.ndarray | None = None
    src_crs: rasterio.crs.CRS | None = None
    src_transform: rasterio.transform.Affine | None = None
    cumulative_fallback_used = False

    for step_index, step_fh in enumerate(step_fhs):
        if use_inventory_resolution and cum_diff_state is not None:
            step_data, apcp_valid, step_crs, step_transform, step_apcp_mode = _resolve_apcp_step_data(
                step_fh=step_fh,
                step_index=step_index,
                step_fhs=step_fhs,
                model_id=model_id,
                product=product,
                run_date=run_date,
                model_plugin=model_plugin,
                ctx=ctx,
                apcp_component=apcp_component,
                apcp_product=apcp_product,
                use_warped=use_warped,
                target_region=target_region,
                target_grid_id=target_grid_id,
                resampling=resampling,
                cum_diff_state=cum_diff_state,
                expected_start_fh_override=(first_step_expected_start_fh if step_index == 0 else None),
            )
            cumulative_fallback_used = cumulative_fallback_used or step_apcp_mode != "exact_step"
        else:
            step_data, step_crs, step_transform = _fetch_step_component(
                model_id=model_id,
                product=str(apcp_product or product),
                run_date=run_date,
                step_fh=step_fh,
                model_plugin=model_plugin,
                var_key=apcp_component,
                use_warped=use_warped,
                target_region=target_region,
                target_grid_id=target_grid_id,
                resampling=resampling,
                ctx=ctx,
            )
            apcp_valid = None

        contribution, step_valid = process_step(
            step_fh, step_data, apcp_valid, step_crs, step_transform,
        )

        if cumulative is None:
            cumulative = contribution
            valid_mask = step_valid
            src_crs = step_crs
            src_transform = step_transform
            continue

        if contribution.shape != cumulative.shape:
            raise ValueError(
                f"{error_label} shape mismatch at fh{step_fh:03d}: "
                f"{contribution.shape} != {cumulative.shape}"
            )

        cumulative = cumulative + contribution
        valid_mask = np.logical_or(valid_mask, step_valid)  # type: ignore[arg-type]

    if cumulative is None or valid_mask is None or src_crs is None or src_transform is None:
        raise ValueError(error_label)

    cumulative = np.where(valid_mask, cumulative, np.nan).astype(np.float32)
    return cumulative, src_crs, src_transform, cumulative_fallback_used


def _interval_sample_fhs(step_fh: int, step_len: int, *, sample_mode: str = "auto") -> list[int]:
    if step_len <= 0:
        raise ValueError(f"Invalid cumulative step length={step_len} for fh={step_fh}")
    start_fh = step_fh - step_len
    normalized_sample_mode = str(sample_mode).strip().lower()
    if normalized_sample_mode in {"step_endpoints", "endpoints"}:
        candidates = [start_fh, step_fh]
    elif normalized_sample_mode in {"step_end", "end"}:
        candidates = [step_fh]
    elif normalized_sample_mode == "three_point":
        mid_offset = max(1, step_len // 2)
        mid_fh = start_fh + mid_offset
        candidates = [start_fh, mid_fh, step_fh]
    elif step_len == 3:
        candidates = [start_fh, step_fh]
    else:
        mid_fh = step_fh - (step_len // 2)
        candidates = [start_fh, mid_fh, step_fh]

    sample_fhs: list[int] = []
    for sample_fh in candidates:
        if sample_fh in sample_fhs:
            continue
        sample_fhs.append(sample_fh)
    return sample_fhs


def _filter_sample_fhs_to_available_steps(
    sample_fhs: list[int],
    *,
    available_fhs: set[int] | None,
) -> list[int]:
    if not available_fhs:
        return sample_fhs

    filtered: list[int] = []
    for sample_fh in sample_fhs:
        if sample_fh not in available_fhs:
            continue
        if sample_fh in filtered:
            continue
        filtered.append(sample_fh)

    return filtered if filtered else sample_fhs


def _log_missing_csnow_sample(
    *,
    model_id: str,
    var_key: str,
    step_fh: int,
    sample_fh: int,
    exc: Exception,
) -> None:
    global _MISSING_CSNOW_SAMPLE_LOG_COUNT
    _MISSING_CSNOW_SAMPLE_LOG_COUNT += 1
    count = _MISSING_CSNOW_SAMPLE_LOG_COUNT
    if count <= 5 or count % 25 == 0:
        logger.debug(
            "Skipping unavailable csnow sample for %s/%s at step fh%03d sample fh%03d (%s); missing_count=%d",
            model_id,
            var_key,
            step_fh,
            sample_fh,
            exc.__class__.__name__,
            count,
        )


def _log_missing_ptype_sample(
    *,
    model_id: str,
    var_key: str,
    component: str,
    step_fh: int,
    sample_fh: int,
    exc: Exception,
) -> None:
    global _MISSING_PTYPE_SAMPLE_LOG_COUNT
    _MISSING_PTYPE_SAMPLE_LOG_COUNT += 1
    count = _MISSING_PTYPE_SAMPLE_LOG_COUNT
    if count <= 5 or count % 25 == 0:
        logger.debug(
            "Skipping unavailable ptype sample for %s/%s component=%s at step fh%03d sample fh%03d (%s); missing_count=%d",
            model_id,
            var_key,
            component,
            step_fh,
            sample_fh,
            exc.__class__.__name__,
            count,
        )


def _neighbor_count_3x3(mask: np.ndarray) -> np.ndarray:
    """Return count of True values in each 3x3 neighborhood (including center)."""
    padded = np.pad(mask.astype(np.uint8, copy=False), 1, mode="constant", constant_values=0)
    return (
        padded[:-2, :-2]
        + padded[:-2, 1:-1]
        + padded[:-2, 2:]
        + padded[1:-1, :-2]
        + padded[1:-1, 1:-1]
        + padded[1:-1, 2:]
        + padded[2:, :-2]
        + padded[2:, 1:-1]
        + padded[2:, 2:]
    ).astype(np.uint8, copy=False)


def _derive_wspd10m(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    u_component = hints.get("u_component", "10u")
    v_component = hints.get("v_component", "10v")
    speed_component = hints.get("speed_component")
    use_warped = _derive_uses_warped_components(derive_component_target_grid, derive_component_resampling)
    target_region = str((derive_component_target_grid or {}).get("region", "")).strip()
    target_grid_id = str((derive_component_target_grid or {}).get("id", "")).strip()
    resampling = str(derive_component_resampling or "bilinear").strip() or "bilinear"

    # Prefer a direct wind-speed field when available.
    if speed_component:
        try:
            logger.info(
                "wspd10m derive path (model=%s): trying direct speed component=%s",
                model_id,
                speed_component,
            )
            if use_warped:
                speed_data, src_crs, src_transform = _fetch_component_warped(
                    model_id=model_id,
                    product=product,
                    run_date=run_date,
                    fh=fh,
                    model_plugin=model_plugin,
                    var_key=str(speed_component),
                    target_region=target_region,
                    target_grid_id=target_grid_id,
                    resampling=resampling,
                    ctx=ctx,
                )
            else:
                speed_data, src_crs, src_transform = _fetch_component(
                    model_id=model_id,
                    product=product,
                    run_date=run_date,
                    fh=fh,
                    model_plugin=model_plugin,
                    var_key=str(speed_component),
                    ctx=ctx,
                )
            wspd = convert_units(
                speed_data.astype(np.float32, copy=False),
                var_key=var_key,
                model_id=model_id,
                var_capability=var_capability,
            )
            return wspd.astype(np.float32, copy=False), src_crs, src_transform
        except (HerbieTransientUnavailableError, RuntimeError, ValueError):
            # Fall back to vector magnitude from 10u/10v.
            pass

    try:
        if use_warped:
            u_data, src_crs, src_transform = _fetch_component_warped(
                model_id=model_id,
                product=product,
                run_date=run_date,
                fh=fh,
                model_plugin=model_plugin,
                var_key=u_component,
                target_region=target_region,
                target_grid_id=target_grid_id,
                resampling=resampling,
                ctx=ctx,
            )
            v_data, _, _ = _fetch_component_warped(
                model_id=model_id,
                product=product,
                run_date=run_date,
                fh=fh,
                model_plugin=model_plugin,
                var_key=v_component,
                target_region=target_region,
                target_grid_id=target_grid_id,
                resampling=resampling,
                ctx=ctx,
            )
        else:
            u_data, src_crs, src_transform = _fetch_component(
                model_id=model_id,
                product=product,
                run_date=run_date,
                fh=fh,
                model_plugin=model_plugin,
                var_key=u_component,
                ctx=ctx,
            )
            v_data, _, _ = _fetch_component(
                model_id=model_id,
                product=product,
                run_date=run_date,
                fh=fh,
                model_plugin=model_plugin,
                var_key=v_component,
                ctx=ctx,
            )
    except (HerbieTransientUnavailableError, RuntimeError, ValueError):
        if not speed_component:
            raise
        raise

    wspd_ms = np.hypot(u_data, v_data, dtype=np.float32)
    wspd = convert_units(
        wspd_ms,
        var_key=var_key,
        model_id=model_id,
        var_capability=var_capability,
    )
    return wspd.astype(np.float32, copy=False), src_crs, src_transform


def _temperature_to_celsius(data: np.ndarray, units: Any) -> np.ndarray:
    units_norm = str(units or "c").strip().lower()
    values = data.astype(np.float32, copy=True)
    if units_norm in {"c", "degc", "celsius", "degree_celsius", "degrees_c"}:
        return values
    if units_norm in {"k", "kelvin", "degree_kelvin", "degrees_k"}:
        return values - np.float32(273.15)
    if units_norm in {"f", "degf", "fahrenheit", "degree_fahrenheit", "degrees_f"}:
        return (values - np.float32(32.0)) * np.float32(5.0 / 9.0)
    raise ValueError(f"Unsupported temperature units for relative humidity derive: {units!r}")


def _relative_humidity_from_temp_dewpoint_c(
    temp_c: np.ndarray,
    dewpoint_c: np.ndarray,
) -> np.ndarray:
    if temp_c.shape != dewpoint_c.shape:
        raise ValueError(f"relative humidity shape mismatch: {temp_c.shape} != {dewpoint_c.shape}")

    temp = temp_c.astype(np.float32, copy=False)
    dewpoint = dewpoint_c.astype(np.float32, copy=False)
    valid = np.isfinite(temp) & np.isfinite(dewpoint)
    rh = np.full(temp.shape, np.nan, dtype=np.float32)

    # Alduchov and Eskridge Magnus coefficients over water. This is the
    # standard near-surface RH approximation from air temperature and dew point.
    a = np.float32(17.625)
    b = np.float32(243.04)
    denom_temp = b + temp
    denom_dewpoint = b + dewpoint
    valid &= (np.abs(denom_temp) > np.float32(1.0e-6)) & (np.abs(denom_dewpoint) > np.float32(1.0e-6))

    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        exponent = (a * dewpoint / denom_dewpoint) - (a * temp / denom_temp)
        computed = (np.float32(100.0) * np.exp(exponent.astype(np.float64))).astype(np.float32)

    rh[valid] = np.clip(computed[valid], np.float32(0.0), np.float32(100.0))
    return rh.astype(np.float32, copy=False)


def _specific_humidity_to_kgkg(data: np.ndarray, units: Any) -> np.ndarray:
    units_norm = str(units or "kg/kg").strip().lower().replace(" ", "")
    values = data.astype(np.float32, copy=True)
    if units_norm in {"kg/kg", "kgkg-1", "kgkg^-1", "1", "fraction"}:
        return values
    if units_norm in {"g/kg", "gkg-1", "gkg^-1"}:
        return values / np.float32(1000.0)
    raise ValueError(f"Unsupported specific humidity units for relative humidity derive: {units!r}")


def _relative_humidity_from_specific_humidity_temp_pressure(
    specific_humidity_kgkg: np.ndarray,
    temp_c: np.ndarray,
    pressure_hpa: float,
) -> np.ndarray:
    if specific_humidity_kgkg.shape != temp_c.shape:
        raise ValueError(
            "relative humidity shape mismatch: "
            f"{specific_humidity_kgkg.shape} != {temp_c.shape}"
        )

    q = specific_humidity_kgkg.astype(np.float32, copy=False)
    temp = temp_c.astype(np.float32, copy=False)
    valid = np.isfinite(q) & np.isfinite(temp) & (q >= np.float32(0.0))
    rh = np.full(temp.shape, np.nan, dtype=np.float32)

    epsilon = np.float32(0.622)
    pressure = np.float32(pressure_hpa)
    denom = epsilon + (np.float32(1.0) - epsilon) * q
    valid &= np.isfinite(denom) & (denom > np.float32(1.0e-9)) & (pressure > np.float32(0.0))

    a = np.float32(17.625)
    b = np.float32(243.04)
    denom_temp = b + temp
    valid &= np.abs(denom_temp) > np.float32(1.0e-6)

    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        vapor_pressure_hpa = (q * pressure) / denom
        saturation_hpa = np.float32(6.1094) * np.exp((a * temp / denom_temp).astype(np.float64))
        computed = (np.float32(100.0) * vapor_pressure_hpa / saturation_hpa).astype(np.float32)

    rh[valid] = np.clip(computed[valid], np.float32(0.0), np.float32(100.0))
    return rh.astype(np.float32, copy=False)


def _derive_relative_humidity_from_temp_dewpoint(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_key, var_capability
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {}) or {}
    temp_component = str(hints.get("temp_component") or hints.get("temperature_component") or "tmp2m").strip()
    dewpoint_component = str(hints.get("dewpoint_component") or "dp2m").strip()
    temp_units = str(hints.get("temp_units") or hints.get("temperature_units") or "c").strip()
    dewpoint_units = str(hints.get("dewpoint_units") or hints.get("dewpoint_temperature_units") or temp_units or "c").strip()
    if not temp_component or not dewpoint_component:
        raise ValueError("relative humidity derive requires temp_component and dewpoint_component hints")

    use_warped = _derive_uses_warped_components(derive_component_target_grid, derive_component_resampling)
    if use_warped:
        target_region = str((derive_component_target_grid or {}).get("region", "")).strip()
        target_grid_id = str((derive_component_target_grid or {}).get("id", "")).strip()
        resampling = str(derive_component_resampling or "bilinear").strip() or "bilinear"
        temp_data, src_crs, src_transform = _fetch_component_warped(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=temp_component,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            ctx=ctx,
        )
        dewpoint_data, _, _ = _fetch_component_warped(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=dewpoint_component,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            ctx=ctx,
        )
    else:
        temp_data, src_crs, src_transform = _fetch_component(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=temp_component,
            ctx=ctx,
        )
        dewpoint_data, _, _ = _fetch_component(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=dewpoint_component,
            ctx=ctx,
        )

    temp_c = _temperature_to_celsius(temp_data, temp_units)
    dewpoint_c = _temperature_to_celsius(dewpoint_data, dewpoint_units)
    rh = _relative_humidity_from_temp_dewpoint_c(temp_c, dewpoint_c)
    return rh, src_crs, src_transform


def _derive_relative_humidity_from_specific_humidity(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_key, var_capability
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {}) or {}
    humidity_component = str(hints.get("specific_humidity_component") or hints.get("humidity_component") or "q700").strip()
    temp_component = str(hints.get("temp_component") or hints.get("temperature_component") or "tmp700").strip()
    humidity_units = str(hints.get("specific_humidity_units") or hints.get("humidity_units") or "kg/kg").strip()
    temp_units = str(hints.get("temp_units") or hints.get("temperature_units") or "c").strip()
    pressure_hpa = float(hints.get("pressure_hpa") or 700.0)
    if not humidity_component or not temp_component:
        raise ValueError("specific-humidity relative humidity derive requires humidity and temperature component hints")

    use_warped = _derive_uses_warped_components(derive_component_target_grid, derive_component_resampling)
    if use_warped:
        target_region = str((derive_component_target_grid or {}).get("region", "")).strip()
        target_grid_id = str((derive_component_target_grid or {}).get("id", "")).strip()
        resampling = str(derive_component_resampling or "bilinear").strip() or "bilinear"
        humidity_data, src_crs, src_transform = _fetch_component_warped(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=humidity_component,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            ctx=ctx,
        )
        temp_data, _, _ = _fetch_component_warped(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=temp_component,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            ctx=ctx,
        )
    else:
        humidity_data, src_crs, src_transform = _fetch_component(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=humidity_component,
            ctx=ctx,
        )
        temp_data, _, _ = _fetch_component(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=temp_component,
            ctx=ctx,
        )

    q_kgkg = _specific_humidity_to_kgkg(humidity_data, humidity_units)
    temp_c = _temperature_to_celsius(temp_data, temp_units)
    rh = _relative_humidity_from_specific_humidity_temp_pressure(q_kgkg, temp_c, pressure_hpa)
    return rh, src_crs, src_transform


def _grid_center_coordinates_geographic(
    *,
    transform: rasterio.transform.Affine,
    shape: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    if abs(float(transform.b)) > 1.0e-9 or abs(float(transform.d)) > 1.0e-9:
        raise ValueError("vort500 derive requires north-up affine transform")
    height, width = int(shape[0]), int(shape[1])
    if height < 2 or width < 2:
        raise ValueError("vort500 derive requires at least a 2x2 grid")
    col_centers = np.arange(width, dtype=np.float64) + 0.5
    row_centers = np.arange(height, dtype=np.float64) + 0.5
    lons_deg = np.asarray(transform.c + transform.a * col_centers, dtype=np.float64)
    lats_deg = np.asarray(transform.f + transform.e * row_centers, dtype=np.float64)
    return lats_deg, lons_deg


def _reproject_to_geographic_grid(
    *,
    data: np.ndarray,
    src_crs: rasterio.crs.CRS,
    src_transform: rasterio.transform.Affine,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    dst_crs = rasterio.crs.CRS.from_epsg(4326)
    src_h, src_w = data.shape
    src_bounds = rasterio.transform.array_bounds(src_h, src_w, src_transform)
    dst_transform, dst_w, dst_h = calculate_default_transform(
        src_crs,
        dst_crs,
        src_w,
        src_h,
        *src_bounds,
    )
    dst_data = np.full((dst_h, dst_w), np.nan, dtype=np.float32)
    reproject(
        source=np.asarray(data, dtype=np.float32),
        destination=dst_data,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=Resampling.bilinear,
        src_nodata=None,
        dst_nodata=float("nan"),
    )
    return dst_data.astype(np.float32, copy=False), dst_crs, dst_transform


def _derive_vort500_from_uv(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    u_component = str(hints.get("u_component") or "u500")
    v_component = str(hints.get("v_component") or "v500")
    use_warped = _derive_uses_warped_components(derive_component_target_grid, derive_component_resampling)
    target_region = str((derive_component_target_grid or {}).get("region", "")).strip()
    target_grid_id = str((derive_component_target_grid or {}).get("id", "")).strip()
    resampling = str(derive_component_resampling or "bilinear").strip() or "bilinear"

    if use_warped:
        u_data, src_crs, src_transform = _fetch_component_warped(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=u_component,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            ctx=ctx,
        )
        v_data, _, _ = _fetch_component_warped(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=v_component,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            ctx=ctx,
        )
    else:
        u_data, src_crs, src_transform = _fetch_component(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=u_component,
            ctx=ctx,
        )
        v_data, _, _ = _fetch_component(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=fh,
            model_plugin=model_plugin,
            var_key=v_component,
            ctx=ctx,
        )

    if u_data.shape != v_data.shape:
        raise ValueError("vort500 derive requires matching u/v component shapes")
    if src_crs is None:
        raise ValueError("vort500 derive requires a geographic source CRS")
    if not bool(getattr(src_crs, "is_geographic", False)):
        original_src_crs = src_crs
        original_src_transform = src_transform
        u_data, src_crs, src_transform = _reproject_to_geographic_grid(
            data=u_data,
            src_crs=original_src_crs,
            src_transform=original_src_transform,
        )
        v_data, v_crs, v_transform = _reproject_to_geographic_grid(
            data=v_data,
            src_crs=original_src_crs,
            src_transform=original_src_transform,
        )
        if v_data.shape != u_data.shape:
            raise ValueError("vort500 derive reprojected u/v component shapes do not match")
        if v_crs != src_crs or v_transform != src_transform:
            raise ValueError("vort500 derive reprojected u/v grids do not align")

    lats_deg, lons_deg = _grid_center_coordinates_geographic(transform=src_transform, shape=u_data.shape)
    lats_rad = np.deg2rad(lats_deg)
    lons_rad = np.deg2rad(lons_deg)
    lat_matrix = lats_rad[:, np.newaxis]
    cos_lat = np.cos(lat_matrix)
    cos_lat = np.where(np.abs(cos_lat) < _MIN_COS_LAT, np.sign(cos_lat) * _MIN_COS_LAT, cos_lat)
    cos_lat = np.where(cos_lat == 0.0, _MIN_COS_LAT, cos_lat)
    sin_lat = np.sin(lat_matrix)
    tan_lat = np.tan(lat_matrix)

    u = np.asarray(u_data, dtype=np.float64)
    v = np.asarray(v_data, dtype=np.float64)
    valid_mask = np.isfinite(u) & np.isfinite(v)
    edge_order = 2 if min(u.shape) >= 3 else 1

    with np.errstate(invalid="ignore", divide="ignore"):
        dv_dlambda = np.gradient(v, lons_rad, axis=1, edge_order=edge_order)
        du_dphi = np.gradient(u, lats_rad, axis=0, edge_order=edge_order)
        relative_vorticity = (
            dv_dlambda / (_EARTH_RADIUS_M * cos_lat)
            - du_dphi / _EARTH_RADIUS_M
            + (u * tan_lat) / _EARTH_RADIUS_M
        )

    relative_vorticity = np.where(valid_mask, relative_vorticity, np.nan).astype(np.float32, copy=False)
    converted = convert_units(
        relative_vorticity,
        var_key=var_key,
        model_id=model_id,
        var_capability=var_capability,
    )
    return converted.astype(np.float32, copy=False), src_crs, src_transform


def _derive_radar_ptype_combo(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_key, var_capability
    family = _derive_radar_ptype_family(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        var_spec_model=var_spec_model,
        model_plugin=model_plugin,
        ctx=ctx,
        derive_component_target_grid=derive_component_target_grid,
        derive_component_resampling=derive_component_resampling,
    )
    return family["indexed"], family["src_crs"], family["src_transform"]


def _derive_radar_ptype_component(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_key, var_capability
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    if not isinstance(hints, dict):
        hints = {}
    component = str(hints.get("ptype_component") or "").strip().lower()
    family = _derive_radar_ptype_family(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        var_spec_model=var_spec_model,
        model_plugin=model_plugin,
        ctx=ctx,
        derive_component_target_grid=derive_component_target_grid,
        derive_component_resampling=derive_component_resampling,
    )
    values = family.get(component)
    if values is None:
        values = np.zeros(np.asarray(family["indexed"]).shape, dtype=np.float32)
    return values.astype(np.float32, copy=False), family["src_crs"], family["src_transform"]


def _derive_radar_ptype_family(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> dict[str, Any]:
    del derive_component_target_grid, derive_component_resampling
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    if not isinstance(hints, dict):
        hints = {}
    cache_hints = {
        str(k): str(hints.get(k))
        for k in (
            "refl_component",
            "rain_component",
            "snow_component",
            "sleet_component",
            "frzr_component",
            "min_visible_dbz",
            "min_mask_value",
            "despeckle_min_neighbors",
        )
        if k in hints
    }
    cache_key = (
        str(model_id),
        str(product),
        run_date.strftime("%Y%m%d%H"),
        int(fh),
        "radar_ptype",
        repr(sorted(cache_hints.items())),
        "",
    )
    if ctx is not None:
        cached = ctx.ptype_family_cache.get(cache_key)
        if cached is not None:
            logger.info("radar_ptype family cache hit: model=%s fh=%03d", model_id, fh)
            return cached

    min_visible_dbz: float | None = None
    if "min_visible_dbz" in hints:
        try:
            min_visible_dbz = float(hints["min_visible_dbz"])
        except (TypeError, ValueError):
            min_visible_dbz = None
    try:
        min_mask_value = float(hints.get("min_mask_value", "0.0"))
    except (TypeError, ValueError):
        min_mask_value = 0.0
    try:
        despeckle_min_neighbors = int(hints.get("despeckle_min_neighbors", "1"))
    except (TypeError, ValueError):
        despeckle_min_neighbors = 1
    despeckle_min_neighbors = min(max(despeckle_min_neighbors, 1), 9)

    refl_id = hints.get("refl_component", "refc")
    rain_id = hints.get("rain_component", "crain")
    snow_id = hints.get("snow_component", "csnow")
    sleet_id = hints.get("sleet_component", "cicep")
    frzr_id = hints.get("frzr_component", "cfrzr")

    refl, src_crs, src_transform = _fetch_component(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        model_plugin=model_plugin,
        var_key=refl_id,
        ctx=ctx,
    )
    rain, _, _ = _fetch_component(model_id=model_id, product=product, run_date=run_date, fh=fh, model_plugin=model_plugin, var_key=rain_id, ctx=ctx)
    snow, _, _ = _fetch_component(model_id=model_id, product=product, run_date=run_date, fh=fh, model_plugin=model_plugin, var_key=snow_id, ctx=ctx)
    sleet, _, _ = _fetch_component(model_id=model_id, product=product, run_date=run_date, fh=fh, model_plugin=model_plugin, var_key=sleet_id, ctx=ctx)
    frzr, _, _ = _fetch_component(model_id=model_id, product=product, run_date=run_date, fh=fh, model_plugin=model_plugin, var_key=frzr_id, ctx=ctx)

    mask_stack = np.stack([rain, snow, sleet, frzr], axis=0).astype(np.float32, copy=False)
    mask_max = np.nanmax(mask_stack, axis=0)
    ptype_idx = np.argmax(mask_stack, axis=0).astype(np.int32)
    ptype_codes = np.array(RADAR_PTYPE_ORDER)
    ptype = ptype_codes[ptype_idx]

    rain_mask = mask_stack[0]
    snow_mask = mask_stack[1]
    frzr_transition = (ptype == "frzr") & ((rain_mask > 0) | (snow_mask > 0))
    if np.any(frzr_transition):
        prefer_rain = rain_mask >= snow_mask
        ptype[frzr_transition & prefer_rain] = "rain"
        ptype[frzr_transition & ~prefer_rain] = "snow"

    refl_safe = np.where(np.isfinite(refl), np.maximum(refl, 0.0), np.nan)
    refl_filled = np.where(np.isfinite(refl_safe), refl_safe, -1.0).astype(np.float32, copy=False)

    indexed = np.full(refl.shape, np.nan, dtype=np.float32)
    min_visible_by_type: dict[str, float] = {}
    for code in RADAR_PTYPE_ORDER:
        breaks = RADAR_PTYPE_BREAKS[code]
        offset = int(breaks["offset"])
        count = int(breaks["count"])
        # Bin against this type's own palette levels: the per-type ramps span
        # different dBZ ranges, so a shared linear span shifts colors off-scale.
        type_levels = np.asarray(RADAR_PTYPE_LEVELS_BY_TYPE[code], dtype=np.float32)
        type_min_visible = min_visible_dbz if min_visible_dbz is not None else float(type_levels[0])
        min_visible_by_type[str(code)] = type_min_visible
        local_bin = np.clip(
            np.searchsorted(type_levels, refl_filled, side="right") - 1,
            0,
            count - 1,
        ).astype(np.int32)
        selector = (
            (ptype == code)
            & np.isfinite(refl_safe)
            & (mask_max >= min_mask_value)
            & (refl_safe >= type_min_visible)
        )
        indexed[selector] = (offset + local_bin[selector]).astype(np.float32)

    if despeckle_min_neighbors > 1:
        valid = np.isfinite(indexed)
        if np.any(valid):
            neighbor_count = _neighbor_count_3x3(valid)
            indexed = np.where(neighbor_count >= despeckle_min_neighbors, indexed, np.nan).astype(np.float32, copy=False)

    component_values: dict[str, np.ndarray] = {}
    finite_refl = np.isfinite(refl_safe)
    for code in RADAR_PTYPE_ORDER:
        selector = (ptype == code) & finite_refl & (refl_safe >= min_visible_by_type[str(code)]) & np.isfinite(indexed)
        component_values[str(code)] = np.where(selector, refl_safe, 0.0).astype(np.float32, copy=False)

    family: dict[str, Any] = {
        "indexed": indexed.astype(np.float32, copy=False),
        **component_values,
        "src_crs": src_crs,
        "src_transform": src_transform,
    }
    if ctx is not None:
        ctx.ptype_family_cache[cache_key] = family
    return family


def _derive_ptype_intensity_gfs(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_key, var_capability
    family = _derive_ptype_intensity_gfs_family(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        var_spec_model=var_spec_model,
        model_plugin=model_plugin,
        ctx=ctx,
        derive_component_target_grid=derive_component_target_grid,
        derive_component_resampling=derive_component_resampling,
    )
    return family["indexed"], family["src_crs"], family["src_transform"]


def _derive_ptype_intensity_component(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_key, var_capability
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    if not isinstance(hints, dict):
        hints = {}
    component = str(hints.get("ptype_component") or "").strip().lower()
    family = _derive_ptype_intensity_gfs_family(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        var_spec_model=var_spec_model,
        model_plugin=model_plugin,
        ctx=ctx,
        derive_component_target_grid=derive_component_target_grid,
        derive_component_resampling=derive_component_resampling,
    )
    values = family.get(component)
    if values is None:
        values = np.zeros(np.asarray(family["indexed"]).shape, dtype=np.float32)
        values[~np.isfinite(np.asarray(family["indexed"]))] = np.nan
    return values.astype(np.float32, copy=False), family["src_crs"], family["src_transform"]


def _derive_ptype_intensity_ecmwf(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_key, var_capability
    rain_rate, snow_rate, ice_rate, src_crs, src_transform = _derive_ptype_intensity_rates_ecmwf(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        var_spec_model=var_spec_model,
        model_plugin=model_plugin,
        ctx=ctx,
        derive_component_target_grid=derive_component_target_grid,
        derive_component_resampling=derive_component_resampling,
    )
    indexed = _ptype_intensity_index_from_family_rates(
        rain_rate=rain_rate,
        snow_rate=snow_rate,
        ice_rate=ice_rate,
        snow_display_boost=2.0,
    )
    return indexed.astype(np.float32, copy=False), src_crs, src_transform


def _derive_ptype_intensity_component_ecmwf(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_key, var_capability
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    component = str(hints.get("ptype_component") or "").strip().lower()
    rain_rate, snow_rate, ice_rate, src_crs, src_transform = _derive_ptype_intensity_rates_ecmwf(
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        var_spec_model=var_spec_model,
        model_plugin=model_plugin,
        ctx=ctx,
        derive_component_target_grid=derive_component_target_grid,
        derive_component_resampling=derive_component_resampling,
    )
    component_values = {
        "rain": rain_rate,
        "snow": snow_rate,
        "ice": ice_rate,
    }
    values = component_values.get(component)
    if values is None:
        values = np.zeros(rain_rate.shape, dtype=np.float32)
        values[~np.isfinite(rain_rate)] = np.nan
    elif component == "snow":
        values = (2.0 * np.nan_to_num(values, nan=0.0)).astype(np.float32, copy=False)
        values[~np.isfinite(rain_rate)] = np.nan
    return values.astype(np.float32, copy=False), src_crs, src_transform


def _derive_ptype_accumulation_ecmwf(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_capability
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    _log_fetch_context_memory(
        label="ptype_accumulation_ecmwf_entry",
        ctx=ctx,
        model_id=model_id,
        var_key=var_key,
        fh=fh,
        extra=f"product={product}",
    )
    component = str(hints.get("ptype_component") or "ice").strip().lower()
    precip_component = str(hints.get("precip_component") or "precip_total")
    snow_component = str(hints.get("snow_component") or "sf")
    step_fhs = _resolve_cumulative_step_fhs(hints=hints, fh=fh, run_date=run_date, default_step_hours=3)
    use_warped, target_region, target_grid_id, resampling = _resolve_warped_state(
        derive_component_target_grid,
        derive_component_resampling,
        model_id,
    )
    cache_version = str(hints.get("cumulative_cache_version", "")).strip() or None
    cumulative_cache_grid_key = _cumulative_cache_grid_key(
        use_warped=use_warped,
        target_grid_id=target_grid_id,
        resampling=resampling,
        cache_version=cache_version,
    )

    logger.info(
        "derive %s fh%03d ecmwf_ptype_steps=%d component=%s%s",
        var_key,
        fh,
        len(step_fhs),
        component,
        _cadence_hint_suffix(hints),
    )
    logger.debug("derive %s fh%03d ecmwf_ptype_steps=%s", var_key, fh, step_fhs)

    cumulative: np.ndarray | None = None
    valid_mask: np.ndarray | None = None
    src_crs: rasterio.crs.CRS | None = None
    src_transform: rasterio.transform.Affine | None = None
    start_index = 0
    reused_prev_cumulative = False
    base_fh: int | None = None
    if len(step_fhs) >= 2:
        prev_fh = int(step_fhs[-2])
        prior = _kuchera_load_prior_cumulative(
            model_id=model_id,
            run_date=run_date,
            var_key=var_key,
            fh=prev_fh,
            ctx=ctx,
            grid_cache_key=cumulative_cache_grid_key,
        )
        if prior is not None:
            unpacked_prior = _unpack_kuchera_cumulative_cache_entry(prior)
            if unpacked_prior is None:
                prior = None
            else:
                prior_data, prior_crs, prior_transform, _ = unpacked_prior
        if prior is not None:
            cumulative = prior_data.astype(np.float32, copy=False)
            valid_mask = np.isfinite(prior_data)
            src_crs = prior_crs
            src_transform = prior_transform
            start_index = len(step_fhs) - 1
            reused_prev_cumulative = True
            base_fh = prev_fh
    subset_step_fhs = step_fhs[start_index:]
    for step_fh in subset_step_fhs:
        total_step, total_valid, step_crs, step_transform = _ptype_intensity_fetch_direct_cumulative_step(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=int(step_fh),
            model_plugin=model_plugin,
            ctx=ctx,
            hints=hints,
            component_var_key=precip_component,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            normalize_to_3h=False,
        )
        try:
            snow_step, snow_valid, snow_crs, snow_transform = _ptype_intensity_fetch_direct_cumulative_step(
                model_id=model_id,
                product=product,
                run_date=run_date,
                fh=int(step_fh),
                model_plugin=model_plugin,
                ctx=ctx,
                hints=hints,
                component_var_key=snow_component,
                use_warped=use_warped,
                target_region=target_region,
                target_grid_id=target_grid_id,
                resampling=resampling,
                normalize_to_3h=False,
            )
            if snow_step.shape != total_step.shape or snow_crs != step_crs or snow_transform != step_transform:
                raise ValueError(f"ptype accumulation ECMWF snow/precip grid mismatch for fh{int(step_fh):03d}")
        except Exception:
            logger.debug(
                "ptype accumulation ECMWF snow component unavailable: model=%s fh=%03d var=%s",
                model_id,
                int(step_fh),
                snow_component,
                exc_info=True,
            )
            snow_step = np.zeros(total_step.shape, dtype=np.float32)
            snow_step[~np.isfinite(total_step)] = np.nan
            snow_valid = np.isfinite(total_step)

        deep_cold, surface_cold, warm_nose = _ptype_intensity_ecmwf_phase_signals(
            model_id=model_id,
            product=product,
            run_date=run_date,
            fh=int(step_fh),
            model_plugin=model_plugin,
            ctx=ctx,
            hints=hints,
            expected_shape=total_step.shape,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
        )
        _, rain_step, snow_family_step, ice_step = _ptype_intensity_family_rates_ecmwf(
            intensity=total_step,
            snow_lwe=snow_step,
            deep_cold=deep_cold,
            surface_cold=surface_cold,
            warm_nose=warm_nose,
        )
        component_steps = {
            "rain": rain_step,
            "snow": snow_family_step,
            "ice": ice_step,
        }
        step_values = component_steps.get(component)
        if step_values is None:
            step_values = np.zeros(total_step.shape, dtype=np.float32)
            step_values[~np.isfinite(total_step)] = np.nan
        step_valid = np.asarray(total_valid, dtype=bool) & np.asarray(snow_valid, dtype=bool) & np.isfinite(step_values)
        step_clean = np.where(step_valid, np.maximum(step_values, 0.0), 0.0).astype(np.float32, copy=False)

        if cumulative is None:
            cumulative = step_clean
            valid_mask = step_valid
            src_crs = step_crs
            src_transform = step_transform
            continue
        if cumulative.shape != step_clean.shape or src_crs != step_crs or src_transform != step_transform:
            raise ValueError(f"ptype accumulation ECMWF grid mismatch for {model_id}/{var_key} fh{int(step_fh):03d}")
        cumulative = (cumulative + step_clean).astype(np.float32, copy=False)
        valid_mask = np.logical_or(valid_mask, step_valid)  # type: ignore[arg-type]

    if cumulative is None or valid_mask is None or src_crs is None or src_transform is None:
        raise ValueError(f"No cumulative ECMWF ptype source steps resolved for {model_id}/{var_key} fh{fh:03d}")
    result = np.where(valid_mask, cumulative, np.nan).astype(np.float32, copy=False)
    _log_fetch_context_memory(
        label="ptype_accumulation_ecmwf_after_loop",
        ctx=ctx,
        model_id=model_id,
        var_key=var_key,
        fh=fh,
        extra=f"computed_steps={len(subset_step_fhs)} reused_prev_cumulative={'true' if reused_prev_cumulative else 'false'}",
    )
    logger.info(
        "ptype_accumulation_ecmwf incremental model=%s run=%s fh=%03d total_steps=%d computed_steps=%d reused_prev_cumulative=%s base_fh=%s",
        model_id,
        _run_id_from_date(run_date),
        fh,
        len(step_fhs),
        len(subset_step_fhs),
        "true" if reused_prev_cumulative else "false",
        f"{base_fh:03d}" if base_fh is not None else "none",
    )
    _kuchera_store_cumulative_cache(
        model_id=model_id,
        run_date=run_date,
        var_key=var_key,
        fh=fh,
        data=cumulative,
        crs=src_crs,
        transform=src_transform,
        ctx=ctx,
        grid_cache_key=cumulative_cache_grid_key,
        coverage_start_fh=0,
    )
    _log_fetch_context_memory(
        label="ptype_accumulation_ecmwf_exit",
        ctx=ctx,
        model_id=model_id,
        var_key=var_key,
        fh=fh,
        extra=f"result_shape={result.shape}",
    )
    return result, src_crs, src_transform


def _derive_precip_total_cumulative(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    frame_start = time.perf_counter()
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    apcp_component = hints.get("apcp_component", "apcp_step")
    use_inventory_resolution = (
        str(var_key).strip().lower() == "precip_total"
        and str(apcp_component).strip() == "apcp_step"
    )
    step_fhs = _resolve_cumulative_step_fhs(hints=hints, fh=fh, run_date=run_date, default_step_hours=6)
    cadence_hint = _cadence_hint_suffix(hints)
    logger.info("derive %s fh%03d apcp_steps=%d%s", var_key, fh, len(step_fhs), cadence_hint)
    logger.debug("derive %s fh%03d apcp_steps=%s", var_key, fh, step_fhs)

    use_warped, target_region, target_grid_id, resampling = _resolve_warped_state(
        derive_component_target_grid, derive_component_resampling, model_id,
    )
    cumulative_cache_grid_key = _cumulative_cache_grid_key(
        use_warped=use_warped,
        target_grid_id=target_grid_id,
        resampling=resampling,
    )

    active_step_fhs = list(step_fhs)
    reused_prev_cumulative = False
    base_fh: int | None = None
    base_cumulative_kgm2: np.ndarray | None = None
    base_crs: rasterio.crs.CRS | None = None
    base_transform: rasterio.transform.Affine | None = None
    first_step_expected_start_fh: int | None = None
    initial_apcp_cumulative: tuple[
        np.ndarray,
        np.ndarray,
        rasterio.crs.CRS,
        rasterio.transform.Affine,
        int,
    ] | None = None

    if len(step_fhs) >= 2:
        prev_fh = int(step_fhs[-2])
        prior = _kuchera_load_prior_cumulative(
            model_id=model_id,
            run_date=run_date,
            var_key=var_key,
            fh=prev_fh,
            ctx=ctx,
            grid_cache_key=cumulative_cache_grid_key,
            scale_divisor=0.03937007874015748,
        )
        if prior is not None:
            unpacked_prior = _unpack_kuchera_cumulative_cache_entry(prior)
            if unpacked_prior is None:
                prior = None
            else:
                prior_data, prior_crs, prior_transform, _ = unpacked_prior
        if prior is not None:
            active_step_fhs = [int(step_fhs[-1])]
            reused_prev_cumulative = True
            base_fh = prev_fh
            base_cumulative_kgm2 = prior_data.astype(np.float32, copy=False)
            base_crs = prior_crs
            base_transform = prior_transform
            if use_inventory_resolution:
                first_step_expected_start_fh = prev_fh
                initial_apcp_cumulative = (
                    prior_data.astype(np.float32, copy=False),
                    np.isfinite(prior_data),
                    prior_crs,
                    prior_transform,
                    prev_fh,
                )

    if not use_inventory_resolution:
        _prefetch_components_parallel(
            [
                _PrefetchTask(
                    model_id=model_id, product=product, run_date=run_date,
                    fh=sfh, model_plugin=model_plugin, var_key=apcp_component,
                    warped=use_warped, target_region=target_region,
                    target_grid_id=target_grid_id, resampling=resampling,
                )
                for sfh in active_step_fhs
            ],
            ctx,
            label=f"precip_total fh{fh:03d}",
        )

    def _process_step(
        step_fh: int,
        step_data: np.ndarray,
        apcp_valid_hint: np.ndarray | None,
        step_crs: rasterio.crs.CRS,
        step_transform: rasterio.transform.Affine,
    ) -> tuple[np.ndarray, np.ndarray]:
        del step_fh, step_crs, step_transform
        step_clean = np.where(
            np.isfinite(step_data), np.maximum(step_data, 0.0), 0.0,
        ).astype(np.float32)
        if apcp_valid_hint is None:
            step_valid = np.isfinite(step_data)
        else:
            step_valid = np.asarray(apcp_valid_hint, dtype=bool)
        return step_clean, step_valid

    try:
        cumulative_kgm2, src_crs, src_transform, _ = _cumulative_apcp_loop(
            model_id=model_id,
            var_key=var_key,
            product=product,
            run_date=run_date,
            fh=fh,
            step_fhs=active_step_fhs,
            model_plugin=model_plugin,
            ctx=ctx,
            apcp_component=apcp_component,
            apcp_product=None,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            use_inventory_resolution=use_inventory_resolution,
            process_step=_process_step,
            error_label=f"No cumulative APCP source steps resolved for {model_id}/{var_key} fh{fh:03d}",
            first_step_expected_start_fh=first_step_expected_start_fh,
            initial_apcp_cumulative=initial_apcp_cumulative,
        )
    except ValueError as exc:
        if not (reused_prev_cumulative and _is_apcp_incremental_rebuild_retryable_error(exc)):
            raise
        logger.warning(
            "%s incremental APCP state unusable at fh=%03d; retrying full rebuild reason=\"%s\"",
            var_key,
            fh,
            str(exc).replace('"', "'"),
        )
        active_step_fhs = list(step_fhs)
        reused_prev_cumulative = False
        base_fh = None
        base_cumulative_kgm2 = None
        base_crs = None
        base_transform = None
        first_step_expected_start_fh = None
        initial_apcp_cumulative = None
        cumulative_kgm2, src_crs, src_transform, _ = _cumulative_apcp_loop(
            model_id=model_id,
            var_key=var_key,
            product=product,
            run_date=run_date,
            fh=fh,
            step_fhs=active_step_fhs,
            model_plugin=model_plugin,
            ctx=ctx,
            apcp_component=apcp_component,
            apcp_product=None,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            use_inventory_resolution=use_inventory_resolution,
            process_step=_process_step,
            error_label=f"No cumulative APCP source steps resolved for {model_id}/{var_key} fh{fh:03d}",
        )

    if base_cumulative_kgm2 is not None and base_crs is not None and base_transform is not None:
        shape_match = base_cumulative_kgm2.shape == cumulative_kgm2.shape
        crs_match = base_crs == src_crs
        transform_match = base_transform == src_transform
        if not (shape_match and crs_match and transform_match):
            if reused_prev_cumulative:
                logger.warning(
                    "%s incremental base-grid mismatch at fh=%03d; retrying full rebuild "
                    "(shape_match=%s crs_match=%s transform_match=%s)",
                    var_key,
                    fh,
                    shape_match,
                    crs_match,
                    transform_match,
                )
                cumulative_kgm2, src_crs, src_transform, _ = _cumulative_apcp_loop(
                    model_id=model_id,
                    var_key=var_key,
                    product=product,
                    run_date=run_date,
                    fh=fh,
                    step_fhs=list(step_fhs),
                    model_plugin=model_plugin,
                    ctx=ctx,
                    apcp_component=apcp_component,
                    apcp_product=None,
                    use_warped=use_warped,
                    target_region=target_region,
                    target_grid_id=target_grid_id,
                    resampling=resampling,
                    use_inventory_resolution=use_inventory_resolution,
                    process_step=_process_step,
                    error_label=f"No cumulative APCP source steps resolved for {model_id}/{var_key} fh{fh:03d}",
                )
                active_step_fhs = list(step_fhs)
                reused_prev_cumulative = False
                base_fh = None
                base_cumulative_kgm2 = None
                base_crs = None
                base_transform = None
            else:
                raise ValueError(
                    f"Precip incremental base-grid mismatch for {model_id}/{var_key} fh{fh:03d}"
                )
        if base_cumulative_kgm2 is not None and base_crs is not None and base_transform is not None:
            base_valid = np.isfinite(base_cumulative_kgm2)
            base_clean = np.where(base_valid, base_cumulative_kgm2, 0.0).astype(np.float32, copy=False)
            current_valid = np.isfinite(cumulative_kgm2)
            current_clean = np.where(current_valid, cumulative_kgm2, 0.0).astype(np.float32, copy=False)
            cumulative_kgm2 = (base_clean + current_clean).astype(np.float32, copy=False)
            cumulative_kgm2 = np.where(base_valid | current_valid, cumulative_kgm2, np.nan).astype(np.float32, copy=False)

    cumulative_inches = convert_units(
        cumulative_kgm2,
        var_key=var_key,
        model_id=model_id,
        var_capability=var_capability,
    )
    logger.info(
        "%s incremental model=%s run=%s fh=%03d total_steps=%d computed_steps=%d reused_prev_cumulative=%s base_fh=%s compute_ms=%d",
        var_key,
        model_id,
        _run_id_from_date(run_date),
        fh,
        len(step_fhs),
        len(active_step_fhs),
        "true" if reused_prev_cumulative else "false",
        f"{base_fh:03d}" if base_fh is not None else "none",
        int((time.perf_counter() - frame_start) * 1000),
    )
    _kuchera_store_cumulative_cache(
        model_id=model_id,
        run_date=run_date,
        var_key=var_key,
        fh=fh,
        data=cumulative_kgm2,
        crs=src_crs,
        transform=src_transform,
        ctx=ctx,
        grid_cache_key=cumulative_cache_grid_key,
    )
    return cumulative_inches.astype(np.float32, copy=False), src_crs, src_transform


def _derive_snowfall_total_10to1_cumulative(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_capability
    frame_start = time.perf_counter()
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    apcp_component = hints.get("apcp_component", "apcp_step")
    snow_component = hints.get("snow_component", "csnow")
    precip_cumulative_component = str(
        hints.get("precip_cumulative_component")
        or "precip_total"
    ).strip() or "precip_total"
    slr_raw = hints.get("slr", "10")
    snow_mask_threshold_raw = hints.get("snow_mask_threshold")
    snow_interval_sample_mode = str(hints.get("snow_interval_sample_mode", "auto")).strip().lower() or "auto"
    skip_zero_hour_sample = _parse_hint_bool(hints.get("skip_zero_hour_sample"), default=False)
    min_step_lwe_raw = hints.get("min_step_lwe_kgm2", "0.01")

    try:
        slr = float(slr_raw)
    except (TypeError, ValueError):
        slr = 10.0
    if slr <= 0.0:
        slr = 10.0

    snow_mask_threshold: float | None = None
    if snow_mask_threshold_raw is not None:
        try:
            parsed_threshold = float(snow_mask_threshold_raw)
        except (TypeError, ValueError):
            parsed_threshold = 0.5
        snow_mask_threshold = min(max(parsed_threshold, 0.0), 1.0)

    try:
        min_step_lwe = float(min_step_lwe_raw)
    except (TypeError, ValueError):
        min_step_lwe = 0.01
    min_step_lwe = max(min_step_lwe, 0.0)

    use_inventory_resolution = (
        str(model_id).strip().lower() in {"gfs", "nam"}
        and str(apcp_component).strip() == "apcp_step"
    )
    cache_version = str(hints.get("cumulative_cache_version", "")).strip() or None
    cadence_sample_fhs: set[int] | None = None
    snow_inches_scale = 0.03937007874015748 * slr

    step_fhs = _resolve_cumulative_step_fhs(hints=hints, fh=fh, run_date=run_date, default_step_hours=6)
    if str(model_id).strip().lower() == "gfs" and snow_interval_sample_mode == "three_point":
        cadence_sample_fhs = {0, *[int(step_fh) for step_fh in step_fhs]}
    # Build interval plan: step_fh → (step_len, sample_fhs).
    interval_plan: dict[int, tuple[int, list[int]]] = {}
    snow_step_fhs: list[int] = []
    prev_step_fh = 0
    for step_fh in step_fhs:
        step_len = step_fh - prev_step_fh
        prev_step_fh = step_fh
        if step_len <= 0:
            raise ValueError(
                f"Non-increasing cumulative snowfall step sequence for {model_id}/{var_key}: "
                f"step_len={step_len} at fh{step_fh:03d}"
            )
        sample_fhs = [
            sf
            for sf in _interval_sample_fhs(
                step_fh,
                step_len,
                sample_mode=snow_interval_sample_mode,
            )
            if sf >= 0
        ]
        if skip_zero_hour_sample:
            sample_fhs = [sf for sf in sample_fhs if sf != 0]
        sample_fhs = _filter_sample_fhs_to_available_steps(
            sample_fhs,
            available_fhs=cadence_sample_fhs,
        )
        interval_plan[step_fh] = (step_len, sample_fhs)
        for sf in sample_fhs:
            if sf not in snow_step_fhs:
                snow_step_fhs.append(sf)

    active_step_fhs = list(step_fhs)
    active_snow_step_fhs = list(snow_step_fhs)
    reused_prev_cumulative = False
    base_fh: int | None = None
    base_cumulative_kgm2: np.ndarray | None = None
    base_crs: rasterio.crs.CRS | None = None
    base_transform: rasterio.transform.Affine | None = None
    first_step_expected_start_fh: int | None = None
    initial_apcp_cumulative: tuple[
        np.ndarray,
        np.ndarray,
        rasterio.crs.CRS,
        rasterio.transform.Affine,
        int,
    ] | None = None
    current_step_fetch_counts: dict[str, int] = {"apcp": 0, "csnow": 0}

    logger.info("snow_ratio method=10to1 fh=%d", fh)
    logger.info(
        "derive %s fh%03d apcp_steps=%d snow_steps=%d%s",
        var_key, fh, len(step_fhs), len(snow_step_fhs),
        _cadence_hint_suffix(hints),
    )
    logger.debug("derive %s fh%03d apcp_steps=%s snow_steps=%s", var_key, fh, step_fhs, snow_step_fhs)

    use_warped, target_region, target_grid_id, resampling = _resolve_warped_state(
        derive_component_target_grid, derive_component_resampling, model_id,
    )
    cumulative_cache_grid_key = _cumulative_cache_grid_key(
        use_warped=use_warped,
        target_grid_id=target_grid_id,
        resampling=resampling,
        cache_version=cache_version,
    )

    if len(step_fhs) >= 2:
        prev_fh = int(step_fhs[-2])
        prior_snowfall = _kuchera_load_prior_cumulative(
            model_id=model_id,
            run_date=run_date,
            var_key=var_key,
            fh=prev_fh,
            ctx=ctx,
            grid_cache_key=cumulative_cache_grid_key,
            scale_divisor=snow_inches_scale,
        )
        prior_precip = _kuchera_load_prior_cumulative(
            model_id=model_id,
            run_date=run_date,
            var_key=precip_cumulative_component,
            fh=prev_fh,
            ctx=ctx,
            grid_cache_key=cumulative_cache_grid_key,
            scale_divisor=0.03937007874015748,
        )
        if prior_snowfall is not None and prior_precip is not None:
            unpacked_prior_snowfall = _unpack_kuchera_cumulative_cache_entry(prior_snowfall)
            unpacked_prior_precip = _unpack_kuchera_cumulative_cache_entry(prior_precip)
            if unpacked_prior_snowfall is None or unpacked_prior_precip is None:
                prior_snowfall = None
                prior_precip = None
            else:
                prior_snowfall_data, prior_snowfall_crs, prior_snowfall_transform, _ = unpacked_prior_snowfall
                prior_precip_data, prior_precip_crs, prior_precip_transform, _ = unpacked_prior_precip
        if prior_snowfall is not None and prior_precip is not None:
            same_shape = prior_snowfall_data.shape == prior_precip_data.shape
            same_crs = prior_snowfall_crs == prior_precip_crs
            same_transform = prior_snowfall_transform == prior_precip_transform
            if same_shape and same_crs and same_transform:
                active_step_fhs = [int(step_fhs[-1])]
                active_snow_step_fhs = list(interval_plan[int(step_fhs[-1])][1])
                reused_prev_cumulative = True
                base_fh = prev_fh
                base_cumulative_kgm2 = prior_snowfall_data.astype(np.float32, copy=False)
                base_crs = prior_snowfall_crs
                base_transform = prior_snowfall_transform
                first_step_expected_start_fh = prev_fh
                initial_apcp_cumulative = (
                    prior_precip_data.astype(np.float32, copy=False),
                    np.isfinite(prior_precip_data),
                    prior_precip_crs,
                    prior_precip_transform,
                    prev_fh,
                )

    # Prefetch APCP + csnow in parallel.
    _prefetch_tasks: list[_PrefetchTask] = []
    if not use_inventory_resolution:
        for _pf_fh in active_step_fhs:
            _prefetch_tasks.append(_PrefetchTask(
                model_id=model_id, product=product, run_date=run_date,
                fh=_pf_fh, model_plugin=model_plugin, var_key=apcp_component,
                warped=use_warped, target_region=target_region,
                target_grid_id=target_grid_id, resampling=resampling,
            ))
    for _pf_fh in active_snow_step_fhs:
        _prefetch_tasks.append(_PrefetchTask(
            model_id=model_id, product=product, run_date=run_date,
            fh=_pf_fh, model_plugin=model_plugin, var_key=snow_component,
            warped=use_warped, target_region=target_region,
            target_grid_id=target_grid_id, resampling=resampling,
        ))
    _prefetch_components_parallel(_prefetch_tasks, ctx, label=f"snow10to1 fh{fh:03d}")
    del _prefetch_tasks

    def _process_step(
        step_fh: int,
        step_data: np.ndarray,
        apcp_valid_hint: np.ndarray | None,
        step_crs: rasterio.crs.CRS,
        step_transform: rasterio.transform.Affine,
    ) -> tuple[np.ndarray, np.ndarray]:
        if int(step_fh) == int(fh):
            current_step_fetch_counts["apcp"] = int(current_step_fetch_counts.get("apcp", 0)) + 1
        if apcp_valid_hint is None:
            apcp_valid = np.isfinite(step_data) & (step_data >= 0.0)
        else:
            apcp_valid = np.asarray(apcp_valid_hint, dtype=bool)
        step_apcp_clean = np.where(apcp_valid, step_data, 0.0).astype(np.float32, copy=False)
        if min_step_lwe > 0.0:
            step_apcp_clean = np.where(
                step_apcp_clean >= min_step_lwe, step_apcp_clean, 0.0,
            ).astype(np.float32, copy=False)

        _step_len, sample_fhs = interval_plan[step_fh]
        sample_masks: list[np.ndarray] = []
        for sample_fh in sample_fhs:
            if int(step_fh) == int(fh):
                current_step_fetch_counts["csnow"] = int(current_step_fetch_counts.get("csnow", 0)) + 1
            try:
                snow_mask, _, _ = _fetch_step_component(
                    model_id=model_id, product=product, run_date=run_date,
                    step_fh=sample_fh, model_plugin=model_plugin,
                    var_key=snow_component,
                    use_warped=use_warped, target_region=target_region,
                    target_grid_id=target_grid_id, resampling=resampling,
                    ctx=ctx,
                )
            except (HerbieTransientUnavailableError, RuntimeError, ValueError) as exc:
                _log_missing_csnow_sample(
                    model_id=model_id, var_key=var_key,
                    step_fh=step_fh, sample_fh=sample_fh, exc=exc,
                )
                continue

            if snow_mask.shape != step_apcp_clean.shape:
                raise ValueError(
                    f"Snowfall mask shape mismatch for {model_id}/{var_key} at fh{sample_fh:03d}: "
                    f"{snow_mask.shape} != {step_apcp_clean.shape}"
                )
            snow_valid = np.isfinite(snow_mask) & (snow_mask >= 0.0) & (snow_mask <= 1.0)
            sample_masks.append(
                np.where(snow_valid, snow_mask, np.nan).astype(np.float32, copy=False)
            )

        if sample_masks:
            sample_stack = np.stack(sample_masks, axis=0).astype(np.float32, copy=False)
            sample_valid_counts = np.sum(np.isfinite(sample_stack), axis=0).astype(np.int32, copy=False)
            sample_sum = np.nansum(sample_stack, axis=0).astype(np.float32, copy=False)
            interval_mask = np.zeros(step_apcp_clean.shape, dtype=np.float32)
            np.divide(
                sample_sum,
                sample_valid_counts.astype(np.float32, copy=False),
                out=interval_mask,
                where=sample_valid_counts > 0,
            )
            interval_mask = np.clip(interval_mask, 0.0, 1.0).astype(np.float32, copy=False)
            if snow_mask_threshold is not None:
                interval_mask = np.where(
                    interval_mask >= np.float32(snow_mask_threshold),
                    np.float32(1.0),
                    np.float32(0.0),
                ).astype(np.float32, copy=False)
            csnow_valid = sample_valid_counts > 0
        else:
            interval_mask = np.zeros(step_apcp_clean.shape, dtype=np.float32)
            csnow_valid = np.zeros(step_apcp_clean.shape, dtype=bool)

        step_snow_kgm2 = (step_apcp_clean * interval_mask).astype(np.float32, copy=False)
        step_valid = apcp_valid & csnow_valid
        return step_snow_kgm2, step_valid

    try:
        cumulative_kgm2, src_crs, src_transform, _ = _cumulative_apcp_loop(
            model_id=model_id,
            var_key=var_key,
            product=product,
            run_date=run_date,
            fh=fh,
            step_fhs=active_step_fhs,
            model_plugin=model_plugin,
            ctx=ctx,
            apcp_component=apcp_component,
            apcp_product=None,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            use_inventory_resolution=use_inventory_resolution,
            process_step=_process_step,
            error_label=f"No cumulative snowfall source steps resolved for {model_id}/{var_key} fh{fh:03d}",
            first_step_expected_start_fh=first_step_expected_start_fh,
            initial_apcp_cumulative=initial_apcp_cumulative,
        )
    except ValueError as exc:
        if not (reused_prev_cumulative and _is_apcp_incremental_rebuild_retryable_error(exc)):
            raise
        logger.warning(
            "snow10to1_incremental apcp incremental state unusable at fh=%03d; retrying full rebuild "
            'reason="%s"',
            fh,
            str(exc).replace('"', "'"),
        )
        active_step_fhs = list(step_fhs)
        reused_prev_cumulative = False
        base_fh = None
        base_cumulative_kgm2 = None
        base_crs = None
        base_transform = None
        first_step_expected_start_fh = None
        initial_apcp_cumulative = None
        current_step_fetch_counts = {"apcp": 0, "csnow": 0}
        cumulative_kgm2, src_crs, src_transform, _ = _cumulative_apcp_loop(
            model_id=model_id,
            var_key=var_key,
            product=product,
            run_date=run_date,
            fh=fh,
            step_fhs=active_step_fhs,
            model_plugin=model_plugin,
            ctx=ctx,
            apcp_component=apcp_component,
            apcp_product=None,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            use_inventory_resolution=use_inventory_resolution,
            process_step=_process_step,
            error_label=f"No cumulative snowfall source steps resolved for {model_id}/{var_key} fh{fh:03d}",
            first_step_expected_start_fh=None,
            initial_apcp_cumulative=None,
        )

    if base_cumulative_kgm2 is not None and base_crs is not None and base_transform is not None:
        shape_match = base_cumulative_kgm2.shape == cumulative_kgm2.shape
        crs_match = base_crs == src_crs
        transform_match = base_transform == src_transform
        if not (shape_match and crs_match and transform_match):
            raise ValueError(
                f"Snowfall incremental base-grid mismatch for {model_id}/{var_key} fh{fh:03d}: "
                f"shape_match={shape_match} crs_match={crs_match} transform_match={transform_match}"
            )
        base_valid = np.isfinite(base_cumulative_kgm2)
        base_clean = np.where(base_valid, base_cumulative_kgm2, 0.0).astype(np.float32, copy=False)
        current_valid = np.isfinite(cumulative_kgm2)
        current_clean = np.where(current_valid, cumulative_kgm2, 0.0).astype(np.float32, copy=False)
        cumulative_kgm2 = (base_clean + current_clean).astype(np.float32, copy=False)
        cumulative_kgm2 = np.where(base_valid | current_valid, cumulative_kgm2, np.nan).astype(np.float32, copy=False)

    # 1 kg/m^2 == 1 mm LWE. Convert to inches liquid then apply fixed 10:1 SLR.
    cumulative_snow_inches = cumulative_kgm2 * 0.03937007874015748 * slr
    _kuchera_store_cumulative_cache(
        model_id=model_id,
        run_date=run_date,
        var_key=var_key,
        fh=fh,
        data=cumulative_kgm2,
        crs=src_crs,
        transform=src_transform,
        ctx=ctx,
        grid_cache_key=cumulative_cache_grid_key,
    )
    logger.info(
        "snow10to1_incremental model=%s run=%s fh=%03d total_steps=%d computed_steps=%d reused_prev_cumulative=%s "
        "base_fh=%s final_step_samples=%d current_step_fetches=%s compute_ms=%d",
        model_id,
        _run_id_from_date(run_date),
        fh,
        len(step_fhs),
        len(active_step_fhs),
        "true" if reused_prev_cumulative else "false",
        f"{base_fh:03d}" if base_fh is not None else "none",
        len(interval_plan.get(int(fh), (0, []))[1]),
        current_step_fetch_counts,
        int((time.perf_counter() - frame_start) * 1000),
    )
    return cumulative_snow_inches.astype(np.float32, copy=False), src_crs, src_transform


def _derive_snowfall_kuchera_total_cumulative(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_capability
    frame_start = time.perf_counter()
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    _log_fetch_context_memory(
        label="kuchera_entry",
        ctx=ctx,
        model_id=model_id,
        var_key=var_key,
        fh=fh,
        extra=f"product={product}",
    )
    kuchera_lwe_component_raw = str(hints.get("kuchera_lwe_component", "")).strip()
    use_direct_cumulative_lwe = bool(kuchera_lwe_component_raw)
    apcp_component = str(kuchera_lwe_component_raw or hints.get("apcp_component", "apcp_step"))
    kuchera_lwe_component_scale_raw = hints.get("kuchera_lwe_component_scale", "1")
    try:
        kuchera_lwe_component_scale = float(kuchera_lwe_component_scale_raw)
    except (TypeError, ValueError):
        kuchera_lwe_component_scale = 1.0
    if kuchera_lwe_component_scale <= 0.0:
        kuchera_lwe_component_scale = 1.0
    apcp_product_raw = str(hints.get("kuchera_apcp_product", "")).strip()
    apcp_product = apcp_product_raw or None
    profile_product_raw = str(hints.get("kuchera_profile_product", "")).strip()
    profile_product = profile_product_raw or None
    ptype_product_raw = str(hints.get("kuchera_ptype_product", "")).strip()
    ptype_product = ptype_product_raw or apcp_product or product
    configured_levels_hpa = _parse_kuchera_levels_hpa(hints.get("kuchera_levels_hpa"))
    profile_mode_hint = str(hints.get("kuchera_profile_mode", "")).strip().lower()
    use_simplified_profile = (
        profile_mode_hint in {"simplified", "ops", "operational"}
        or (not profile_mode_hint and str(model_id).lower() == "hrrr")
    )
    profile_levels_hpa = _kuchera_select_profile_levels(configured_levels_hpa, simplified=use_simplified_profile)
    if not profile_levels_hpa:
        raise ValueError(f"No Kuchera profile levels configured for {model_id}/{var_key} fh{fh:03d}")
    use_ptype_gate = _parse_hint_bool(
        hints.get("kuchera_use_ptype_gate"),
        default=False,
    )
    ptype_interval_sample_mode = str(
        hints.get("kuchera_ptype_interval_sample_mode", "auto"),
    ).strip().lower() or "auto"
    use_surface_temp_cap = _parse_hint_bool(
        hints.get("kuchera_use_surface_temp_cap"),
        default=False,
    )
    use_sfc_pressure_mask = _parse_hint_bool(
        hints.get("kuchera_use_sfc_pressure_mask"),
        default=False,
    )
    surface_temp_product_raw = str(hints.get("kuchera_surface_temp_product", "")).strip()
    resolved_surface_temp_product = surface_temp_product_raw or product
    surface_temp_cap_cold_f_raw = hints.get("kuchera_surface_temp_cap_cold_f")
    surface_temp_cap_warm_f_raw = hints.get("kuchera_surface_temp_cap_warm_f")
    surface_temp_cap_cold_ratio_raw = hints.get("kuchera_surface_temp_cap_cold_ratio")
    surface_temp_cap_warm_ratio_raw = hints.get("kuchera_surface_temp_cap_warm_ratio")
    try:
        surface_temp_cap_cold_f = np.float32(float(surface_temp_cap_cold_f_raw))
    except (TypeError, ValueError):
        surface_temp_cap_cold_f = _KUCHERA_SURFACE_TEMP_CAP_COLD_F_DEFAULT
    try:
        surface_temp_cap_warm_f = np.float32(float(surface_temp_cap_warm_f_raw))
    except (TypeError, ValueError):
        surface_temp_cap_warm_f = _KUCHERA_SURFACE_TEMP_CAP_WARM_F_DEFAULT
    try:
        surface_temp_cap_cold_ratio = np.float32(float(surface_temp_cap_cold_ratio_raw))
    except (TypeError, ValueError):
        surface_temp_cap_cold_ratio = _KUCHERA_SURFACE_TEMP_CAP_COLD_RATIO_DEFAULT
    try:
        surface_temp_cap_warm_ratio = np.float32(float(surface_temp_cap_warm_ratio_raw))
    except (TypeError, ValueError):
        surface_temp_cap_warm_ratio = _KUCHERA_SURFACE_TEMP_CAP_WARM_RATIO_DEFAULT
    sfc_pressure_product_raw = str(hints.get("kuchera_sfc_pressure_product", "")).strip()
    resolved_sfc_pressure_product = sfc_pressure_product_raw or product
    sfc_pressure_margin_pa_raw = hints.get("kuchera_sfc_pressure_margin_pa")
    try:
        sfc_pressure_margin_pa = np.float32(float(sfc_pressure_margin_pa_raw))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        sfc_pressure_margin_pa = _KUCHERA_SFC_PRESSURE_MARGIN_PA_DEFAULT
    sfc_pressure_margin_pa = np.float32(max(0.0, float(sfc_pressure_margin_pa)))
    min_step_lwe_raw = hints.get("min_step_lwe_kgm2", "0.01")
    try:
        min_step_lwe = float(min_step_lwe_raw)
    except (TypeError, ValueError):
        min_step_lwe = 0.01
    min_step_lwe = max(min_step_lwe, 0.0)
    rebuild_window_steps = _parse_hint_int(
        hints.get("kuchera_incremental_rebuild_window_steps"),
        default=_KUCHERA_INCREMENTAL_WINDOW_DEFAULT,
        minimum=1,
    )

    step_fhs = _resolve_cumulative_step_fhs(hints=hints, fh=fh, run_date=run_date, default_step_hours=6)
    if not step_fhs:
        raise ValueError(f"No cumulative Kuchera source steps resolved for {model_id}/{var_key} fh{fh:03d}")
    ptype_interval_plan: dict[int, tuple[int, list[int]]] = {}
    if use_ptype_gate:
        # Keep ptype interval sampling aligned to the resolved APCP step cadence
        # so midpoint requests do not drift onto forecast hours the model does
        # not actually provide for precip-type fields.
        ptype_cadence_sample_fhs: set[int] | None = {0, *[int(step_fh) for step_fh in step_fhs]}

        prev_step_fh = 0
        for step_fh in step_fhs:
            step_len = int(step_fh) - int(prev_step_fh)
            prev_step_fh = int(step_fh)
            if step_len <= 0:
                raise ValueError(
                    f"Non-increasing cumulative Kuchera step sequence for {model_id}/{var_key}: "
                    f"step_len={step_len} at fh{int(step_fh):03d}"
                )
            sample_fhs = [
                sample_fh
                for sample_fh in _interval_sample_fhs(
                    int(step_fh),
                    step_len,
                    sample_mode=ptype_interval_sample_mode,
                )
                if sample_fh >= 0
            ]
            sample_fhs = _filter_sample_fhs_to_available_steps(
                sample_fhs,
                available_fhs=ptype_cadence_sample_fhs,
            )
            ptype_interval_plan[int(step_fh)] = (step_len, sample_fhs)
    logger.info(
        "derive %s fh%03d apcp_steps=%d profile_levels=%s profile_mode=%s apcp_product=%s profile_product=%s%s",
        var_key,
        fh,
        len(step_fhs),
        profile_levels_hpa,
        "simplified" if use_simplified_profile else "full",
        apcp_product or product,
        profile_product or product,
        _cadence_hint_suffix(hints),
    )
    logger.debug("derive %s fh%03d apcp_steps=%s", var_key, fh, step_fhs)

    if use_simplified_profile:
        logger.info(
            "kuchera_profile_mode=simplified model=%s fh=%03d levels=%s",
            model_id,
            fh,
            profile_levels_hpa,
        )

    use_warped, target_region, target_grid_id, resampling = _resolve_warped_state(
        derive_component_target_grid, derive_component_resampling, model_id,
    )
    cache_version = str(hints.get("cumulative_cache_version", "")).strip() or None
    cumulative_cache_grid_key = _cumulative_cache_grid_key(
        use_warped=use_warped,
        target_grid_id=target_grid_id,
        resampling=resampling,
        cache_version=cache_version,
    )

    resolved_profile_product = str(profile_product or product)
    sfc_pressure_mask_logged = False
    sfc_pressure_fetch_failed_logged = False
    surface_temp_cap_fetch_failed_logged = False
    fallback_used = False
    fallback_profile_logged = False
    missing_level_warning_logged = False
    sparse_level_warning_logged = False
    ptype_stats: dict[str, float] = {
        "frozen_min": float("inf"),
        "frozen_max": float("-inf"),
        "frozen_sum": 0.0,
        "frozen_count": 0.0,
        "apcp_min": float("inf"),
        "apcp_max": float("-inf"),
        "apcp_frozen_min": float("inf"),
        "apcp_frozen_max": float("-inf"),
    }
    ptype_any_precip_pixels = False
    ptype_any_reduced_pixels = False
    kuchera_maxt_stats: dict[str, float] = {
        "max_t_min": float("inf"),
        "max_t_max": float("-inf"),
        "max_t_sum": 0.0,
        "max_t_count": 0.0,
        "ratio_min": float("inf"),
        "ratio_max": float("-inf"),
        "ratio_sum": 0.0,
        "ratio_count": 0.0,
        "ratio_clamp_max_count": 0.0,
    }
    apcp_cumulative_fallback_used = False
    current_step_fetch_counts: dict[str, int] = {"apcp": 0, "profile_temp": 0, "ptype": 0}
    surface_temp_cap_stats: dict[str, float] = {
        "applied_count": 0.0,
        "tmp2m_min": float("inf"),
        "tmp2m_max": float("-inf"),
        "cap_ratio_min": float("inf"),
        "cap_ratio_max": float("-inf"),
    }

    reused_prev_cumulative = False
    base_fh: int | None = None
    base_cumulative: np.ndarray | None = None
    base_crs: rasterio.crs.CRS | None = None
    base_transform: rasterio.transform.Affine | None = None
    first_step_expected_start_fh: int | None = None
    initial_apcp_cumulative: tuple[
        np.ndarray,
        np.ndarray,
        rasterio.crs.CRS,
        rasterio.transform.Affine,
        int,
    ] | None = None
    start_index = max(0, len(step_fhs) - rebuild_window_steps)

    def _load_prior_kuchera_base(
        prior_fh: int,
    ) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]] | None:
        prior = _kuchera_load_prior_cumulative(
            model_id=model_id,
            run_date=run_date,
            var_key=var_key,
            fh=int(prior_fh),
            ctx=ctx,
            grid_cache_key=cumulative_cache_grid_key,
        )
        if prior is None:
            return None
        unpacked_prior = _unpack_kuchera_cumulative_cache_entry(prior)
        if unpacked_prior is None:
            return None
        prior_data, prior_crs, prior_transform, prior_meta = unpacked_prior
        return prior_data, prior_crs, prior_transform, prior_meta

    def _load_prior_apcp_seed(
        seed_fh: int,
        *,
        reference_data: np.ndarray | None,
        reference_crs: rasterio.crs.CRS | None,
        reference_transform: rasterio.transform.Affine | None,
    ) -> tuple[np.ndarray, np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, int] | None:
        prior_precip = _kuchera_load_prior_cumulative(
            model_id=model_id,
            run_date=run_date,
            var_key="precip_total",
            fh=int(seed_fh),
            ctx=ctx,
            grid_cache_key=cumulative_cache_grid_key,
        )
        if prior_precip is None:
            return None
        unpacked_prior_precip = _unpack_kuchera_cumulative_cache_entry(prior_precip)
        if unpacked_prior_precip is None:
            return None
        prior_precip_data, prior_precip_crs, prior_precip_transform, _ = unpacked_prior_precip
        if reference_data is not None:
            same_shape = prior_precip_data.shape == reference_data.shape
            same_crs = prior_precip_crs == reference_crs
            same_transform = prior_precip_transform == reference_transform
            if not (same_shape and same_crs and same_transform):
                return None
        return (
            prior_precip_data.astype(np.float32, copy=False),
            np.isfinite(prior_precip_data),
            prior_precip_crs,
            prior_precip_transform,
            int(seed_fh),
        )

    def _load_prior_direct_lwe_seed(
        seed_fh: int,
        *,
        reference_data: np.ndarray | None,
        reference_crs: rasterio.crs.CRS | None,
        reference_transform: rasterio.transform.Affine | None,
    ) -> tuple[np.ndarray, np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, int] | None:
        try:
            seed_data, seed_crs, seed_transform = _fetch_step_component(
                model_id=model_id,
                product=apcp_product or product,
                run_date=run_date,
                step_fh=int(seed_fh),
                model_plugin=model_plugin,
                var_key=apcp_component,
                use_warped=use_warped,
                target_region=target_region,
                target_grid_id=target_grid_id,
                resampling=resampling,
                ctx=ctx,
            )
        except (HerbieTransientUnavailableError, RuntimeError, ValueError):
            return None
        if reference_data is not None:
            same_shape = seed_data.shape == reference_data.shape
            same_crs = seed_crs == reference_crs
            same_transform = seed_transform == reference_transform
            if not (same_shape and same_crs and same_transform):
                return None
        return (
            seed_data.astype(np.float32, copy=False),
            np.isfinite(seed_data),
            seed_crs,
            seed_transform,
            int(seed_fh),
        )

    def _load_prior_seed(
        seed_fh: int,
        *,
        reference_data: np.ndarray | None,
        reference_crs: rasterio.crs.CRS | None,
        reference_transform: rasterio.transform.Affine | None,
    ) -> tuple[np.ndarray, np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, int] | None:
        if use_direct_cumulative_lwe:
            return _load_prior_direct_lwe_seed(
                seed_fh,
                reference_data=reference_data,
                reference_crs=reference_crs,
                reference_transform=reference_transform,
            )
        return _load_prior_apcp_seed(
            seed_fh,
            reference_data=reference_data,
            reference_crs=reference_crs,
            reference_transform=reference_transform,
        )

    if len(step_fhs) >= 2:
        prev_fh = int(step_fhs[-2])
        prior = _load_prior_kuchera_base(prev_fh)
        if prior is not None:
            prior_data, prior_crs, prior_transform, prior_meta = prior
            prior_seed = _load_prior_seed(
                prev_fh,
                reference_data=prior_data,
                reference_crs=prior_crs,
                reference_transform=prior_transform,
            )
            direct_prior_ok = (
                not use_direct_cumulative_lwe
                or _kuchera_cache_has_full_run_coverage(prior_meta)
            )
            if direct_prior_ok and (prior_seed is not None or not use_direct_cumulative_lwe):
                base_cumulative, base_crs, base_transform = prior_data, prior_crs, prior_transform
                base_fh = prev_fh
                start_index = len(step_fhs) - 1
                reused_prev_cumulative = True
                initial_apcp_cumulative = prior_seed
                if initial_apcp_cumulative is not None:
                    first_step_expected_start_fh = prev_fh

    if base_cumulative is None and start_index > 0:
        anchor_fh = int(step_fhs[start_index - 1])
        prior = _load_prior_kuchera_base(anchor_fh)
        if prior is None:
            start_index = 0
            base_fh = None
        else:
            prior_data, prior_crs, prior_transform, prior_meta = prior
            prior_seed = _load_prior_seed(
                anchor_fh,
                reference_data=prior_data,
                reference_crs=prior_crs,
                reference_transform=prior_transform,
            )
            direct_prior_ok = (
                not use_direct_cumulative_lwe
                or _kuchera_cache_has_full_run_coverage(prior_meta)
            )
            if (prior_seed is None or not direct_prior_ok) and use_direct_cumulative_lwe:
                start_index = 0
                base_fh = None
            else:
                base_cumulative, base_crs, base_transform = prior_data, prior_crs, prior_transform
                base_fh = anchor_fh
                reused_prev_cumulative = True
                initial_apcp_cumulative = prior_seed
                if initial_apcp_cumulative is not None:
                    first_step_expected_start_fh = anchor_fh

    steps_processed = 0
    while True:
        subset_step_fhs = step_fhs[start_index:]
        if not subset_step_fhs:
            raise ValueError(f"No incremental Kuchera steps selected for {model_id}/{var_key} fh{fh:03d}")

        if start_index > 0:
            anchor_fh = int(step_fhs[start_index - 1])
            if base_fh != anchor_fh or base_cumulative is None or (
                use_direct_cumulative_lwe and initial_apcp_cumulative is None
            ):
                prior = _load_prior_kuchera_base(anchor_fh)
                if prior is None:
                    start_index = 0
                    base_cumulative = None
                    base_crs = None
                    base_transform = None
                    base_fh = None
                    first_step_expected_start_fh = None
                    initial_apcp_cumulative = None
                    reused_prev_cumulative = False
                    continue
                prior_data, prior_crs, prior_transform, prior_meta = prior
                prior_seed = _load_prior_seed(
                    anchor_fh,
                    reference_data=prior_data,
                    reference_crs=prior_crs,
                    reference_transform=prior_transform,
                )
                direct_prior_ok = (
                    not use_direct_cumulative_lwe
                    or _kuchera_cache_has_full_run_coverage(prior_meta)
                )
                if (prior_seed is None or not direct_prior_ok) and use_direct_cumulative_lwe:
                    start_index = 0
                    base_cumulative = None
                    base_crs = None
                    base_transform = None
                    base_fh = None
                    first_step_expected_start_fh = None
                    initial_apcp_cumulative = None
                    reused_prev_cumulative = False
                    continue
                base_cumulative, base_crs, base_transform = prior_data, prior_crs, prior_transform
                base_fh = anchor_fh
                reused_prev_cumulative = True
                initial_apcp_cumulative = prior_seed
                first_step_expected_start_fh = anchor_fh if initial_apcp_cumulative is not None else None

        _prefetch_tasks: list[_PrefetchTask] = []
        for _prefetch_step_fh in subset_step_fhs:
            for _level_hpa in profile_levels_hpa:
                _prefetch_tasks.append(_PrefetchTask(
                    model_id=model_id,
                    product=resolved_profile_product,
                    run_date=run_date,
                    fh=int(_prefetch_step_fh),
                    model_plugin=model_plugin,
                    var_key=f"tmp{int(_level_hpa)}",
                    warped=use_warped,
                    target_region=target_region,
                    target_grid_id=target_grid_id,
                    resampling=resampling,
                ))
            if use_surface_temp_cap:
                _prefetch_tasks.append(_PrefetchTask(
                    model_id=model_id,
                    product=resolved_surface_temp_product,
                    run_date=run_date,
                    fh=int(_prefetch_step_fh),
                    model_plugin=model_plugin,
                    var_key="tmp2m",
                    warped=use_warped,
                    target_region=target_region,
                    target_grid_id=target_grid_id,
                    resampling=resampling,
                ))
            if use_sfc_pressure_mask:
                _prefetch_tasks.append(_PrefetchTask(
                    model_id=model_id,
                    product=resolved_sfc_pressure_product,
                    run_date=run_date,
                    fh=int(_prefetch_step_fh),
                    model_plugin=model_plugin,
                    var_key="pres_sfc",
                    warped=use_warped,
                    target_region=target_region,
                    target_grid_id=target_grid_id,
                    resampling=resampling,
                ))
            if use_ptype_gate:
                _ptype_step_len, _ptype_sample_fhs = ptype_interval_plan.get(int(_prefetch_step_fh), (0, [int(_prefetch_step_fh)]))
                del _ptype_step_len
                for _ptype_sample_fh in _ptype_sample_fhs:
                    _prefetch_tasks.append(_PrefetchTask(
                        model_id=model_id,
                        product=str(ptype_product),
                        run_date=run_date,
                        fh=int(_ptype_sample_fh),
                        model_plugin=model_plugin,
                        var_key="csnow",
                        warped=use_warped,
                        target_region=target_region,
                        target_grid_id=target_grid_id,
                        resampling=resampling,
                    ))
        _prefetch_components_parallel(_prefetch_tasks, ctx, label=f"kuchera fh{fh:03d}")
        _log_fetch_context_memory(
            label="kuchera_after_prefetch",
            ctx=ctx,
            model_id=model_id,
            var_key=var_key,
            fh=fh,
            extra=f"subset_steps={len(subset_step_fhs)} start_index={start_index}",
        )
        del _prefetch_tasks

        cum_diff_state = _ApcpCumDiffState()
        if initial_apcp_cumulative is not None:
            (
                seed_data,
                seed_valid,
                seed_crs,
                seed_transform,
                seed_fh,
            ) = initial_apcp_cumulative
            cum_diff_state.consumed_sum = np.asarray(seed_data, dtype=np.float32)
            cum_diff_state.consumed_sum_valid = np.asarray(seed_valid, dtype=bool)
            cum_diff_state.consumed_sum_crs = seed_crs
            cum_diff_state.consumed_sum_transform = seed_transform
            cum_diff_state.consumed_through_fh = int(seed_fh)
        subset_cumulative: np.ndarray | None = None
        subset_valid_mask: np.ndarray | None = None
        subset_crs: rasterio.crs.CRS | None = None
        subset_transform: rasterio.transform.Affine | None = None
        requires_full_history_rebuild = False
        rebuild_trigger_step_fh: int | None = None
        steps_processed = 0

        for local_step_index, step_fh in enumerate(subset_step_fhs):
            try:
                step_apcp_data, apcp_valid, step_crs, step_transform, step_apcp_mode = _resolve_apcp_step_data(
                    step_fh=step_fh,
                    step_index=start_index + local_step_index,
                    step_fhs=step_fhs,
                    model_id=model_id,
                    product=product,
                    run_date=run_date,
                    model_plugin=model_plugin,
                    ctx=ctx,
                    apcp_component=apcp_component,
                    apcp_product=apcp_product,
                    use_warped=use_warped,
                    target_region=target_region,
                    target_grid_id=target_grid_id,
                    resampling=resampling,
                    cum_diff_state=cum_diff_state,
                    force_cumulative_from_zero=use_direct_cumulative_lwe,
                    skip_inventory_window_selection=use_direct_cumulative_lwe,
                )
            except ValueError as exc:
                if start_index > 0 and _is_apcp_incremental_rebuild_retryable_error(exc):
                    logger.warning(
                        "kuchera_incremental apcp incremental state unusable at fh=%03d; retrying full rebuild reason=\"%s\"",
                        fh,
                        str(exc).replace('"', "'"),
                    )
                    requires_full_history_rebuild = True
                    rebuild_trigger_step_fh = int(step_fh)
                    break
                raise
            apcp_cumulative_fallback_used = apcp_cumulative_fallback_used or step_apcp_mode != "exact_step"
            steps_processed += 1
            if int(step_fh) == int(fh):
                current_step_fetch_counts["apcp"] = current_step_fetch_counts.get("apcp", 0) + 1

            if kuchera_lwe_component_scale != 1.0:
                step_apcp_data = (step_apcp_data * np.float32(kuchera_lwe_component_scale)).astype(np.float32, copy=False)

            # Without a carried APCP cumulative baseline, history-dependent
            # APCP windows inside an incremental subset can overcount by
            # subtracting against an empty or stale state.
            if step_apcp_mode != "exact_step" and start_index > 0 and initial_apcp_cumulative is None and not use_direct_cumulative_lwe:
                requires_full_history_rebuild = True
                rebuild_trigger_step_fh = int(step_fh)
                break

            assert apcp_valid is not None
            step_apcp_clean = step_apcp_data
            if min_step_lwe > 0.0:
                step_apcp_clean = np.where(
                    step_apcp_clean >= min_step_lwe,
                    step_apcp_clean,
                    0.0,
                ).astype(np.float32, copy=False)

            step_apcp_for_snow = step_apcp_clean
            if use_ptype_gate:
                _ptype_step_len, ptype_sample_fhs = ptype_interval_plan.get(int(step_fh), (0, [int(step_fh)]))
                frozen_frac, _ptype_fallback_used, ptype_fetch_count = _kuchera_frozen_fraction_for_step(
                    model_id=model_id,
                    var_key=var_key,
                    product=str(ptype_product),
                    run_date=run_date,
                    step_fh=step_fh,
                    sample_fhs=ptype_sample_fhs,
                    model_plugin=model_plugin,
                    use_warped=use_warped,
                    target_region=target_region,
                    target_grid_id=target_grid_id,
                    resampling=resampling,
                    ctx=ctx,
                    expected_shape=step_apcp_clean.shape,
                )
                if int(step_fh) == int(fh):
                    current_step_fetch_counts["ptype"] = current_step_fetch_counts.get("ptype", 0) + int(ptype_fetch_count)
                step_apcp_for_snow = _apply_kuchera_ptype_gate(step_apcp_clean, frozen_frac)

                finite_frozen = np.isfinite(frozen_frac)
                if np.any(finite_frozen):
                    frozen_values = frozen_frac[finite_frozen]
                    ptype_stats["frozen_min"] = min(ptype_stats["frozen_min"], float(np.min(frozen_values)))
                    ptype_stats["frozen_max"] = max(ptype_stats["frozen_max"], float(np.max(frozen_values)))
                    ptype_stats["frozen_sum"] += float(np.sum(frozen_values, dtype=np.float64))
                    ptype_stats["frozen_count"] += float(frozen_values.size)
                finite_apcp = np.isfinite(step_apcp_clean)
                if np.any(finite_apcp):
                    apcp_values = step_apcp_clean[finite_apcp]
                    ptype_stats["apcp_min"] = min(ptype_stats["apcp_min"], float(np.min(apcp_values)))
                    ptype_stats["apcp_max"] = max(ptype_stats["apcp_max"], float(np.max(apcp_values)))
                finite_apcp_frozen = np.isfinite(step_apcp_for_snow)
                if np.any(finite_apcp_frozen):
                    apcp_frozen_values = step_apcp_for_snow[finite_apcp_frozen]
                    ptype_stats["apcp_frozen_min"] = min(ptype_stats["apcp_frozen_min"], float(np.min(apcp_frozen_values)))
                    ptype_stats["apcp_frozen_max"] = max(ptype_stats["apcp_frozen_max"], float(np.max(apcp_frozen_values)))

                precip_mask = apcp_valid & np.isfinite(step_apcp_clean) & (step_apcp_clean > 0.0) & np.isfinite(frozen_frac)
                if np.any(precip_mask):
                    ptype_any_precip_pixels = True
                    if np.any(frozen_frac[precip_mask] < 0.999):
                        ptype_any_reduced_pixels = True

            step_levels: list[int] = []
            step_temps: list[np.ndarray] = []

            step_sfc_pressure: np.ndarray | None = None
            if use_sfc_pressure_mask:
                try:
                    sfc_pres_data, _, _ = _fetch_step_component(
                        model_id=model_id,
                        product=resolved_sfc_pressure_product,
                        run_date=run_date,
                        step_fh=step_fh,
                        model_plugin=model_plugin,
                        var_key="pres_sfc",
                        use_warped=use_warped,
                        target_region=target_region,
                        target_grid_id=target_grid_id,
                        resampling=resampling,
                        ctx=ctx,
                    )
                    step_sfc_pressure = sfc_pres_data.astype(np.float32, copy=False)
                except (HerbieTransientUnavailableError, RuntimeError, ValueError) as exc:
                    if not sfc_pressure_fetch_failed_logged:
                        logger.warning(
                            "kuchera_sfc_pressure_mask fetch_failed step_fh=%03d reason=%s; "
                            "proceeding without below-ground filtering",
                            step_fh,
                            exc,
                        )
                        sfc_pressure_fetch_failed_logged = True

            for level_hpa in profile_levels_hpa:
                try:
                    step_temp, _, _ = _fetch_step_component(
                        model_id=model_id,
                        product=resolved_profile_product,
                        run_date=run_date,
                        step_fh=step_fh,
                        model_plugin=model_plugin,
                        var_key=f"tmp{int(level_hpa)}",
                        use_warped=use_warped,
                        target_region=target_region,
                        target_grid_id=target_grid_id,
                        resampling=resampling,
                        ctx=ctx,
                    )
                    if int(step_fh) == int(fh):
                        current_step_fetch_counts["profile_temp"] = current_step_fetch_counts.get("profile_temp", 0) + 1
                except (HerbieTransientUnavailableError, RuntimeError, ValueError):
                    continue
                if step_temp.shape != step_apcp_clean.shape:
                    raise ValueError(
                        f"Kuchera temp shape mismatch for {model_id}/{var_key} at fh{step_fh:03d} "
                        f"level={level_hpa}: {step_temp.shape} != {step_apcp_clean.shape}"
                    )
                step_levels.append(int(level_hpa))
                step_temp_clean = step_temp.astype(np.float32, copy=False)
                if step_sfc_pressure is not None and step_temp_clean.shape == step_sfc_pressure.shape:
                    level_pa = np.float32(int(level_hpa) * 100)
                    below_ground = np.isfinite(step_sfc_pressure) & (level_pa > step_sfc_pressure + sfc_pressure_margin_pa)
                    masked_count = int(np.count_nonzero(below_ground))
                    if masked_count > 0:
                        step_temp_clean = np.where(below_ground, np.nan, step_temp_clean).astype(np.float32, copy=False)
                        if not sfc_pressure_mask_logged:
                            logger.info(
                                "kuchera_sfc_pressure_mask active step_fh=%03d level=%d "
                                "masked_pixels=%d/%d",
                                step_fh,
                                level_hpa,
                                masked_count,
                                step_temp_clean.size,
                            )
                            sfc_pressure_mask_logged = True
                step_temps.append(step_temp_clean)

            step_max_t_k = np.full(step_apcp_clean.shape, np.nan, dtype=np.float32)
            if not step_levels:
                if not fallback_profile_logged:
                    logger.info(
                        "kuchera_profile insufficient_levels=0/%d fallback=10to1",
                        len(profile_levels_hpa),
                    )
                    fallback_profile_logged = True
                fallback_used = True
                step_slr = np.full(step_apcp_clean.shape, 10.0, dtype=np.float32)
            else:
                if len(step_levels) < len(profile_levels_hpa) and not missing_level_warning_logged:
                    missing_levels = sorted(level for level in profile_levels_hpa if level not in set(step_levels))
                    logger.warning(
                        "kuchera_maxt_low500 missing_levels available=%d/%d step_fh=%03d missing=%s",
                        len(step_levels),
                        len(profile_levels_hpa),
                        step_fh,
                        missing_levels,
                    )
                    missing_level_warning_logged = True
                if len(step_levels) < min(2, len(profile_levels_hpa)) and not sparse_level_warning_logged:
                    logger.warning(
                        "kuchera_maxt_low500 sparse_levels available=%d/%d step_fh=%03d using_warmest_available=true",
                        len(step_levels),
                        len(profile_levels_hpa),
                        step_fh,
                    )
                    sparse_level_warning_logged = True
                step_max_t_k = _kuchera_maxt_low500_from_temp_stack_k(step_temps)
                step_slr = _kuchera_ratio_from_maxt_low500_k(step_max_t_k)
                step_slr = np.where(np.isfinite(step_slr), step_slr, 10.0).astype(np.float32, copy=False)

            if use_surface_temp_cap and np.any(
                apcp_valid & np.isfinite(step_apcp_for_snow) & (step_apcp_for_snow > 0.0)
            ):
                try:
                    step_tmp2m_c, _, _ = _fetch_step_component(
                        model_id=model_id,
                        product=resolved_surface_temp_product,
                        run_date=run_date,
                        step_fh=step_fh,
                        model_plugin=model_plugin,
                        var_key="tmp2m",
                        use_warped=use_warped,
                        target_region=target_region,
                        target_grid_id=target_grid_id,
                        resampling=resampling,
                        ctx=ctx,
                    )
                    if step_tmp2m_c.shape != step_slr.shape:
                        raise ValueError(
                            f"Kuchera surface temp shape mismatch for {model_id}/{var_key} at fh{step_fh:03d}: "
                            f"{step_tmp2m_c.shape} != {step_slr.shape}"
                        )
                    if int(step_fh) == int(fh):
                        current_step_fetch_counts["surface_temp"] = current_step_fetch_counts.get("surface_temp", 0) + 1
                    step_slr, cap_applied_mask, cap_ratio = _apply_kuchera_surface_temp_slr_cap(
                        step_slr,
                        step_tmp2m_c.astype(np.float32, copy=False),
                        cold_threshold_f=float(surface_temp_cap_cold_f),
                        warm_threshold_f=float(surface_temp_cap_warm_f),
                        cold_cap_ratio=float(surface_temp_cap_cold_ratio),
                        warm_cap_ratio=float(surface_temp_cap_warm_ratio),
                    )
                    if np.any(cap_applied_mask):
                        temp_f_values = (
                            step_tmp2m_c[cap_applied_mask] * np.float32(9.0 / 5.0) + np.float32(32.0)
                        ).astype(np.float32, copy=False)
                        cap_values = cap_ratio[cap_applied_mask]
                        surface_temp_cap_stats["applied_count"] += float(np.count_nonzero(cap_applied_mask))
                        surface_temp_cap_stats["tmp2m_min"] = min(
                            surface_temp_cap_stats["tmp2m_min"], float(np.min(temp_f_values))
                        )
                        surface_temp_cap_stats["tmp2m_max"] = max(
                            surface_temp_cap_stats["tmp2m_max"], float(np.max(temp_f_values))
                        )
                        surface_temp_cap_stats["cap_ratio_min"] = min(
                            surface_temp_cap_stats["cap_ratio_min"], float(np.min(cap_values))
                        )
                        surface_temp_cap_stats["cap_ratio_max"] = max(
                            surface_temp_cap_stats["cap_ratio_max"], float(np.max(cap_values))
                        )
                except (HerbieTransientUnavailableError, RuntimeError, ValueError) as exc:
                    if not surface_temp_cap_fetch_failed_logged:
                        logger.warning(
                            "kuchera_surface_temp_cap fetch_failed step_fh=%03d reason=%s; proceeding without surface cap",
                            step_fh,
                            exc,
                        )
                        surface_temp_cap_fetch_failed_logged = True

            valid_precip_ratio = (
                apcp_valid
                & np.isfinite(step_apcp_for_snow)
                & (step_apcp_for_snow > 0.0)
                & np.isfinite(step_slr)
            )
            if np.any(valid_precip_ratio):
                ratio_values = step_slr[valid_precip_ratio]
                kuchera_maxt_stats["ratio_min"] = min(kuchera_maxt_stats["ratio_min"], float(np.min(ratio_values)))
                kuchera_maxt_stats["ratio_max"] = max(kuchera_maxt_stats["ratio_max"], float(np.max(ratio_values)))
                kuchera_maxt_stats["ratio_sum"] += float(np.sum(ratio_values, dtype=np.float64))
                kuchera_maxt_stats["ratio_count"] += float(ratio_values.size)
                kuchera_maxt_stats["ratio_clamp_max_count"] += float(
                    np.count_nonzero(ratio_values >= (_KUCHERA_RATIO_CLAMP_MAX - np.float32(1e-6)))
                )

                valid_max_t = valid_precip_ratio & np.isfinite(step_max_t_k)
                if np.any(valid_max_t):
                    max_t_values = step_max_t_k[valid_max_t]
                    kuchera_maxt_stats["max_t_min"] = min(kuchera_maxt_stats["max_t_min"], float(np.min(max_t_values)))
                    kuchera_maxt_stats["max_t_max"] = max(kuchera_maxt_stats["max_t_max"], float(np.max(max_t_values)))
                    kuchera_maxt_stats["max_t_sum"] += float(np.sum(max_t_values, dtype=np.float64))
                    kuchera_maxt_stats["max_t_count"] += float(max_t_values.size)

            contribution = (step_apcp_for_snow * step_slr).astype(np.float32, copy=False)
            step_valid = apcp_valid & np.isfinite(step_slr)

            if subset_cumulative is None:
                subset_cumulative = contribution
                subset_valid_mask = step_valid
                subset_crs = step_crs
                subset_transform = step_transform
            else:
                if contribution.shape != subset_cumulative.shape:
                    raise ValueError(
                        f"Kuchera contribution shape mismatch at fh{step_fh:03d}: "
                        f"{contribution.shape} != {subset_cumulative.shape}"
                    )
                subset_cumulative = (subset_cumulative + contribution).astype(np.float32, copy=False)
                subset_valid_mask = np.logical_or(subset_valid_mask, step_valid)

        _log_fetch_context_memory(
            label="kuchera_after_step_loop",
            ctx=ctx,
            model_id=model_id,
            var_key=var_key,
            fh=fh,
            extra=(
                f"subset_steps={len(subset_step_fhs)} processed={steps_processed} "
                f"rebuild_required={'true' if requires_full_history_rebuild else 'false'}"
            ),
        )

        if requires_full_history_rebuild and start_index > 0:
            logger.info(
                "kuchera_incremental cumulative_apcp_requires_full_rebuild fh=%03d step_fh=%03d start_index=%d",
                fh,
                rebuild_trigger_step_fh if rebuild_trigger_step_fh is not None else -1,
                start_index,
            )
            start_index = 0
            base_cumulative = None
            base_crs = None
            base_transform = None
            base_fh = None
            reused_prev_cumulative = False
            first_step_expected_start_fh = None
            initial_apcp_cumulative = None
            continue

        if subset_cumulative is None or subset_valid_mask is None or subset_crs is None or subset_transform is None:
            raise ValueError(f"No cumulative Kuchera source steps resolved for {model_id}/{var_key} fh{fh:03d}")

        if base_cumulative is not None and base_crs is not None and base_transform is not None:
            base_data = np.asarray(base_cumulative, dtype=np.float32)
            shape_match = base_data.shape == subset_cumulative.shape
            crs_match = base_crs == subset_crs
            transform_match = base_transform == subset_transform
            if not (shape_match and crs_match and transform_match):
                if start_index > 0:
                    logger.warning(
                        "kuchera_incremental base-grid mismatch at fh=%03d; retrying full rebuild "
                        "(shape_match=%s crs_match=%s transform_match=%s)",
                        fh,
                        shape_match,
                        crs_match,
                        transform_match,
                    )
                    start_index = 0
                    base_cumulative = None
                    base_crs = None
                    base_transform = None
                    base_fh = None
                    reused_prev_cumulative = False
                    continue
                raise ValueError(
                    f"Kuchera incremental base-grid mismatch for {model_id}/{var_key} fh{fh:03d}"
                )

            base_valid = np.isfinite(base_data)
            base_clean = np.where(base_valid, base_data, 0.0).astype(np.float32, copy=False)
            subset_clean = np.where(subset_valid_mask, subset_cumulative, 0.0).astype(np.float32, copy=False)
            cumulative_kgm2 = (base_clean + subset_clean).astype(np.float32, copy=False)
            valid_mask = base_valid | subset_valid_mask
            src_crs = base_crs
            src_transform = base_transform
        else:
            cumulative_kgm2 = subset_cumulative.astype(np.float32, copy=False)
            valid_mask = subset_valid_mask
            src_crs = subset_crs
            src_transform = subset_transform
        break

    cumulative_kgm2 = np.where(valid_mask, cumulative_kgm2, np.nan).astype(np.float32, copy=False)
    cumulative_snow_inches = cumulative_kgm2 * 0.03937007874015748
    if use_ptype_gate and ptype_stats["frozen_count"] > 0:
        frozen_mean = ptype_stats["frozen_sum"] / ptype_stats["frozen_count"]
        logger.info(
            "kuchera_ptype_gate fh=%03d frozen_frac_min=%.3f frozen_frac_max=%.3f "
            "frozen_frac_mean=%.3f apcp_step_min=%.3f apcp_step_max=%.3f "
            "apcp_frozen_min=%.3f apcp_frozen_max=%.3f",
            fh,
            ptype_stats["frozen_min"],
            ptype_stats["frozen_max"],
            frozen_mean,
            ptype_stats["apcp_min"],
            ptype_stats["apcp_max"],
            ptype_stats["apcp_frozen_min"],
            ptype_stats["apcp_frozen_max"],
        )
        if ptype_any_precip_pixels and not ptype_any_reduced_pixels:
            logger.warning("ptype gate ineffective")

    if surface_temp_cap_stats["applied_count"] > 0:
        logger.info(
            "kuchera_surface_temp_cap fh=%03d applied_px=%d tmp2m_f_min=%.2f tmp2m_f_max=%.2f "
            "cap_ratio_min=%.2f cap_ratio_max=%.2f",
            fh,
            int(surface_temp_cap_stats["applied_count"]),
            surface_temp_cap_stats["tmp2m_min"],
            surface_temp_cap_stats["tmp2m_max"],
            surface_temp_cap_stats["cap_ratio_min"],
            surface_temp_cap_stats["cap_ratio_max"],
        )

    ratio_count = kuchera_maxt_stats["ratio_count"]
    ratio_mean = kuchera_maxt_stats["ratio_sum"] / ratio_count if ratio_count > 0 else float("nan")
    max_t_count = kuchera_maxt_stats["max_t_count"]
    max_t_mean = kuchera_maxt_stats["max_t_sum"] / max_t_count if max_t_count > 0 else float("nan")
    logger.info(
        "kuchera_maxt_low500 fh=%03d maxT_k_min=%.2f maxT_k_max=%.2f maxT_k_mean=%.2f "
        "ratio_min=%.2f ratio_max=%.2f ratio_mean=%.2f valid_precip_px=%d",
        fh,
        kuchera_maxt_stats["max_t_min"] if max_t_count > 0 else float("nan"),
        kuchera_maxt_stats["max_t_max"] if max_t_count > 0 else float("nan"),
        max_t_mean,
        kuchera_maxt_stats["ratio_min"] if ratio_count > 0 else float("nan"),
        kuchera_maxt_stats["ratio_max"] if ratio_count > 0 else float("nan"),
        ratio_mean,
        int(ratio_count),
    )
    if ratio_count > 0:
        clamp_max_fraction = kuchera_maxt_stats["ratio_clamp_max_count"] / ratio_count
        if clamp_max_fraction > 0.2:
            logger.warning(
                "kuchera_maxt_low500 high_clamp_max_fraction=%.3f threshold=0.200 valid_precip_px=%d",
                clamp_max_fraction,
                int(ratio_count),
            )

    quality_flags: list[str] = []
    if fallback_used:
        quality_flags.append("slr_fallback_10to1")
    if apcp_cumulative_fallback_used:
        quality_flags.append("apcp_cumulative_fallback")
    _record_derive_quality(
        ctx,
        var_key=var_key,
        fh=fh,
        quality_flags=quality_flags,
    )
    logger.info(
        "snow_ratio method=kuchera fh=%d levels=%s fallback=%s",
        fh, profile_levels_hpa, "10to1" if fallback_used else "none",
    )
    logger.info(
        "kuchera_incremental run=%s fh=%03d total_steps=%d computed_steps=%d reused_prev_cumulative=%s "
        "base_fh=%s current_step_fetches=%s compute_ms=%d",
        _run_id_from_date(run_date),
        fh,
        len(step_fhs),
        steps_processed,
        "true" if reused_prev_cumulative else "false",
        f"{base_fh:03d}" if base_fh is not None else "none",
        current_step_fetch_counts,
        int((time.perf_counter() - frame_start) * 1000),
    )

    _kuchera_store_cumulative_cache(
        model_id=model_id,
        run_date=run_date,
        var_key=var_key,
        fh=fh,
        data=cumulative_kgm2,
        crs=src_crs,
        transform=src_transform,
        ctx=ctx,
        grid_cache_key=cumulative_cache_grid_key,
    )
    _log_fetch_context_memory(
        label="kuchera_exit",
        ctx=ctx,
        model_id=model_id,
        var_key=var_key,
        fh=fh,
        extra=f"result_shape={cumulative_snow_inches.shape}",
    )
    return cumulative_snow_inches.astype(np.float32, copy=False), src_crs, src_transform


def _derive_ptype_accumulation_cumulative(
    *,
    model_id: str,
    var_key: str,
    product: str,
    run_date: datetime,
    fh: int,
    var_spec_model: Any,
    var_capability: Any | None,
    model_plugin: Any,
    ctx: FetchContext | None = None,
    derive_component_target_grid: dict[str, str] | None = None,
    derive_component_resampling: str | None = None,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    del var_capability
    frame_start = time.perf_counter()
    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {})
    apcp_component = str(hints.get("apcp_component", "apcp_step"))
    ptype_component = str(hints.get("ptype_component", "cfrzr"))
    sample_mode = str(hints.get("ptype_interval_sample_mode", "auto")).strip().lower() or "auto"
    threshold_raw = hints.get("ptype_mask_threshold", "0.5")
    min_step_lwe_raw = hints.get("min_step_lwe_kgm2", "0.01")

    try:
        ptype_threshold = min(max(float(threshold_raw), 0.0), 1.0)
    except (TypeError, ValueError):
        ptype_threshold = 0.5
    try:
        min_step_lwe = max(float(min_step_lwe_raw), 0.0)
    except (TypeError, ValueError):
        min_step_lwe = 0.01

    use_inventory_resolution = (
        str(model_id).strip().lower() in {"gfs", "nam"}
        and str(apcp_component).strip() == "apcp_step"
    )
    cache_version = str(hints.get("cumulative_cache_version", "")).strip() or None
    step_fhs = _resolve_cumulative_step_fhs(hints=hints, fh=fh, run_date=run_date, default_step_hours=6)
    cadence_sample_fhs: set[int] | None = None
    if str(model_id).strip().lower() == "gfs" and sample_mode == "three_point":
        cadence_sample_fhs = {0, *[int(step_fh) for step_fh in step_fhs]}

    interval_plan: dict[int, tuple[int, list[int]]] = {}
    ptype_sample_fhs: list[int] = []
    prev_step_fh = 0
    for step_fh in step_fhs:
        step_len = int(step_fh) - int(prev_step_fh)
        prev_step_fh = int(step_fh)
        if step_len <= 0:
            raise ValueError(
                f"Non-increasing cumulative ptype step sequence for {model_id}/{var_key}: "
                f"step_len={step_len} at fh{step_fh:03d}"
            )
        sample_fhs = _filter_sample_fhs_to_available_steps(
            _interval_sample_fhs(int(step_fh), step_len, sample_mode=sample_mode),
            available_fhs=cadence_sample_fhs,
        )
        interval_plan[int(step_fh)] = (step_len, sample_fhs)
        for sample_fh in sample_fhs:
            if sample_fh not in ptype_sample_fhs:
                ptype_sample_fhs.append(sample_fh)

    logger.info(
        "derive %s fh%03d apcp_steps=%d ptype_samples=%d component=%s%s",
        var_key,
        fh,
        len(step_fhs),
        len(ptype_sample_fhs),
        ptype_component,
        _cadence_hint_suffix(hints),
    )
    logger.debug("derive %s fh%03d apcp_steps=%s ptype_samples=%s", var_key, fh, step_fhs, ptype_sample_fhs)

    use_warped, target_region, target_grid_id, resampling = _resolve_warped_state(
        derive_component_target_grid, derive_component_resampling, model_id,
    )
    cumulative_cache_grid_key = _cumulative_cache_grid_key(
        use_warped=use_warped,
        target_grid_id=target_grid_id,
        resampling=resampling,
        cache_version=cache_version,
    )

    active_step_fhs = list(step_fhs)
    reused_prev_cumulative = False
    base_fh: int | None = None
    base_cumulative_kgm2: np.ndarray | None = None
    base_crs: rasterio.crs.CRS | None = None
    base_transform: rasterio.transform.Affine | None = None
    first_step_expected_start_fh: int | None = None
    initial_apcp_cumulative: tuple[
        np.ndarray,
        np.ndarray,
        rasterio.crs.CRS,
        rasterio.transform.Affine,
        int,
    ] | None = None

    if len(step_fhs) >= 2:
        prev_fh = int(step_fhs[-2])
        prior_ptype = _kuchera_load_prior_cumulative(
            model_id=model_id,
            run_date=run_date,
            var_key=var_key,
            fh=prev_fh,
            ctx=ctx,
            grid_cache_key=cumulative_cache_grid_key,
            scale_divisor=0.03937007874015748,
        )
        prior_precip = _kuchera_load_prior_cumulative(
            model_id=model_id,
            run_date=run_date,
            var_key="precip_total",
            fh=prev_fh,
            ctx=ctx,
            grid_cache_key=cumulative_cache_grid_key,
            scale_divisor=0.03937007874015748,
        )
        if prior_ptype is not None and prior_precip is not None:
            prior_ptype_data, prior_ptype_crs, prior_ptype_transform, _ = prior_ptype
            prior_precip_data, prior_precip_crs, prior_precip_transform, _ = prior_precip
            same_shape = prior_ptype_data.shape == prior_precip_data.shape
            same_crs = prior_ptype_crs == prior_precip_crs
            same_transform = prior_ptype_transform == prior_precip_transform
            if same_shape and same_crs and same_transform:
                active_step_fhs = [int(step_fhs[-1])]
                reused_prev_cumulative = True
                base_fh = prev_fh
                base_cumulative_kgm2 = prior_ptype_data.astype(np.float32, copy=False)
                base_crs = prior_ptype_crs
                base_transform = prior_ptype_transform
                first_step_expected_start_fh = prev_fh
                initial_apcp_cumulative = (
                    prior_precip_data.astype(np.float32, copy=False),
                    np.isfinite(prior_precip_data),
                    prior_precip_crs,
                    prior_precip_transform,
                    prev_fh,
                )

    active_sample_fhs: list[int] = []
    for active_step_fh in active_step_fhs:
        for sample_fh in interval_plan[int(active_step_fh)][1]:
            if sample_fh not in active_sample_fhs:
                active_sample_fhs.append(sample_fh)

    _prefetch_components_parallel(
        [
            _PrefetchTask(
                model_id=model_id, product=product, run_date=run_date,
                fh=sample_fh, model_plugin=model_plugin, var_key=ptype_component,
                warped=use_warped, target_region=target_region,
                target_grid_id=target_grid_id, resampling=resampling,
            )
            for sample_fh in active_sample_fhs
        ],
        ctx,
        label=f"{var_key} fh{fh:03d}",
    )

    def _process_step(
        step_fh: int,
        step_data: np.ndarray,
        apcp_valid_hint: np.ndarray | None,
        step_crs: rasterio.crs.CRS,
        step_transform: rasterio.transform.Affine,
    ) -> tuple[np.ndarray, np.ndarray]:
        if apcp_valid_hint is None:
            apcp_valid = np.isfinite(step_data) & (step_data >= 0.0)
        else:
            apcp_valid = np.asarray(apcp_valid_hint, dtype=bool)
        step_apcp_clean = np.where(apcp_valid, step_data, 0.0).astype(np.float32, copy=False)
        if min_step_lwe > 0.0:
            step_apcp_clean = np.where(step_apcp_clean >= min_step_lwe, step_apcp_clean, 0.0).astype(np.float32, copy=False)

        _step_len, sample_fhs = interval_plan[int(step_fh)]
        sample_masks: list[np.ndarray] = []
        for sample_fh in sample_fhs:
            try:
                ptype_mask, _, _ = _fetch_step_component(
                    model_id=model_id, product=product, run_date=run_date,
                    step_fh=sample_fh, model_plugin=model_plugin,
                    var_key=ptype_component,
                    use_warped=use_warped, target_region=target_region,
                    target_grid_id=target_grid_id, resampling=resampling,
                    ctx=ctx,
                )
            except (HerbieTransientUnavailableError, RuntimeError, ValueError) as exc:
                _log_missing_ptype_sample(
                    model_id=model_id, var_key=var_key, component=ptype_component,
                    step_fh=int(step_fh), sample_fh=int(sample_fh), exc=exc,
                )
                continue
            if ptype_mask.shape != step_apcp_clean.shape:
                raise ValueError(
                    f"Ptype mask shape mismatch for {model_id}/{var_key} component={ptype_component} "
                    f"at fh{sample_fh:03d}: {ptype_mask.shape} != {step_apcp_clean.shape}"
                )
            ptype_valid = np.isfinite(ptype_mask) & (ptype_mask >= 0.0) & (ptype_mask <= 1.0)
            sample_masks.append(np.where(ptype_valid, ptype_mask, np.nan).astype(np.float32, copy=False))

        if sample_masks:
            sample_stack = np.stack(sample_masks, axis=0).astype(np.float32, copy=False)
            sample_valid_counts = np.sum(np.isfinite(sample_stack), axis=0).astype(np.int32, copy=False)
            sample_sum = np.nansum(sample_stack, axis=0).astype(np.float32, copy=False)
            interval_mask = np.zeros(step_apcp_clean.shape, dtype=np.float32)
            np.divide(
                sample_sum,
                sample_valid_counts.astype(np.float32, copy=False),
                out=interval_mask,
                where=sample_valid_counts > 0,
            )
            interval_mask = np.where(
                interval_mask >= np.float32(ptype_threshold),
                np.float32(1.0),
                np.float32(0.0),
            ).astype(np.float32, copy=False)
            ptype_valid = sample_valid_counts > 0
        else:
            interval_mask = np.zeros(step_apcp_clean.shape, dtype=np.float32)
            ptype_valid = np.zeros(step_apcp_clean.shape, dtype=bool)

        step_ptype_kgm2 = (step_apcp_clean * interval_mask).astype(np.float32, copy=False)
        step_valid = apcp_valid & ptype_valid
        return step_ptype_kgm2, step_valid

    try:
        cumulative_kgm2, src_crs, src_transform, _ = _cumulative_apcp_loop(
            model_id=model_id,
            var_key=var_key,
            product=product,
            run_date=run_date,
            fh=fh,
            step_fhs=active_step_fhs,
            model_plugin=model_plugin,
            ctx=ctx,
            apcp_component=apcp_component,
            apcp_product=None,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            use_inventory_resolution=use_inventory_resolution,
            process_step=_process_step,
            error_label=f"No cumulative ptype accumulation source steps resolved for {model_id}/{var_key} fh{fh:03d}",
            first_step_expected_start_fh=first_step_expected_start_fh,
            initial_apcp_cumulative=initial_apcp_cumulative,
        )
    except ValueError as exc:
        if not (reused_prev_cumulative and _is_apcp_incremental_rebuild_retryable_error(exc)):
            raise
        logger.warning(
            "%s incremental APCP state unusable at fh=%03d; retrying full rebuild reason=\"%s\"",
            var_key,
            fh,
            str(exc).replace('"', "'"),
        )
        active_step_fhs = list(step_fhs)
        reused_prev_cumulative = False
        base_fh = None
        base_cumulative_kgm2 = None
        base_crs = None
        base_transform = None
        cumulative_kgm2, src_crs, src_transform, _ = _cumulative_apcp_loop(
            model_id=model_id,
            var_key=var_key,
            product=product,
            run_date=run_date,
            fh=fh,
            step_fhs=active_step_fhs,
            model_plugin=model_plugin,
            ctx=ctx,
            apcp_component=apcp_component,
            apcp_product=None,
            use_warped=use_warped,
            target_region=target_region,
            target_grid_id=target_grid_id,
            resampling=resampling,
            use_inventory_resolution=use_inventory_resolution,
            process_step=_process_step,
            error_label=f"No cumulative ptype accumulation source steps resolved for {model_id}/{var_key} fh{fh:03d}",
        )

    if base_cumulative_kgm2 is not None and base_crs is not None and base_transform is not None:
        shape_match = base_cumulative_kgm2.shape == cumulative_kgm2.shape
        crs_match = base_crs == src_crs
        transform_match = base_transform == src_transform
        if not (shape_match and crs_match and transform_match):
            raise ValueError(
                f"Ptype accumulation incremental base-grid mismatch for {model_id}/{var_key} fh{fh:03d}: "
                f"shape_match={shape_match} crs_match={crs_match} transform_match={transform_match}"
            )
        base_valid = np.isfinite(base_cumulative_kgm2)
        base_clean = np.where(base_valid, base_cumulative_kgm2, 0.0).astype(np.float32, copy=False)
        current_valid = np.isfinite(cumulative_kgm2)
        current_clean = np.where(current_valid, cumulative_kgm2, 0.0).astype(np.float32, copy=False)
        cumulative_kgm2 = (base_clean + current_clean).astype(np.float32, copy=False)
        cumulative_kgm2 = np.where(base_valid | current_valid, cumulative_kgm2, np.nan).astype(np.float32, copy=False)

    _kuchera_store_cumulative_cache(
        model_id=model_id,
        run_date=run_date,
        var_key=var_key,
        fh=fh,
        data=cumulative_kgm2,
        crs=src_crs,
        transform=src_transform,
        ctx=ctx,
        grid_cache_key=cumulative_cache_grid_key,
    )
    logger.info(
        "%s incremental model=%s run=%s fh=%03d total_steps=%d computed_steps=%d reused_prev_cumulative=%s "
        "base_fh=%s compute_ms=%d",
        var_key,
        model_id,
        _run_id_from_date(run_date),
        fh,
        len(step_fhs),
        len(active_step_fhs),
        "true" if reused_prev_cumulative else "false",
        f"{base_fh:03d}" if base_fh is not None else "none",
        int((time.perf_counter() - frame_start) * 1000),
    )
    return (cumulative_kgm2 * 0.03937007874015748).astype(np.float32, copy=False), src_crs, src_transform


DERIVE_STRATEGIES: dict[str, DeriveStrategy] = {
    "wspd10m": DeriveStrategy(
        id="wspd10m",
        required_inputs=("10u", "10v"),
        output_var_key="wspd10m",
        execute=_derive_wspd10m,
    ),
    "vort500_from_uv": DeriveStrategy(
        id="vort500_from_uv",
        required_inputs=("u500", "v500"),
        output_var_key="vort500",
        execute=_derive_vort500_from_uv,
    ),
    "relative_humidity_from_temp_dewpoint": DeriveStrategy(
        id="relative_humidity_from_temp_dewpoint",
        required_inputs=("tmp2m", "dp2m"),
        output_var_key="rh2m",
        execute=_derive_relative_humidity_from_temp_dewpoint,
    ),
    "relative_humidity_from_specific_humidity": DeriveStrategy(
        id="relative_humidity_from_specific_humidity",
        required_inputs=("q700", "tmp700"),
        output_var_key=None,
        execute=_derive_relative_humidity_from_specific_humidity,
    ),
    "radar_ptype_combo": DeriveStrategy(
        id="radar_ptype_combo",
        required_inputs=("refc", "crain", "csnow", "cicep", "cfrzr"),
        output_var_key="radar_ptype",
        execute=_derive_radar_ptype_combo,
    ),
    "radar_ptype_component": DeriveStrategy(
        id="radar_ptype_component",
        required_inputs=("refc", "crain", "csnow", "cicep", "cfrzr"),
        output_var_key=None,
        execute=_derive_radar_ptype_component,
    ),
    "ptype_intensity_gfs": DeriveStrategy(
        id="ptype_intensity_gfs",
        required_inputs=("prate", "apcp_step", "crain", "csnow", "cicep", "cfrzr"),
        output_var_key="ptype_intensity",
        execute=_derive_ptype_intensity_gfs,
    ),
    "ptype_intensity_component": DeriveStrategy(
        id="ptype_intensity_component",
        required_inputs=("prate", "apcp_step", "crain", "csnow", "cicep", "cfrzr"),
        output_var_key=None,
        execute=_derive_ptype_intensity_component,
    ),
    "ptype_intensity_ecmwf": DeriveStrategy(
        id="ptype_intensity_ecmwf",
        required_inputs=("precip_total", "sf", "tmp2m", "tmp925", "tmp850", "msl"),
        output_var_key="ptype_intensity",
        execute=_derive_ptype_intensity_ecmwf,
    ),
    "ptype_intensity_component_ecmwf": DeriveStrategy(
        id="ptype_intensity_component_ecmwf",
        required_inputs=("precip_total", "sf", "tmp2m", "tmp925", "tmp850"),
        output_var_key=None,
        execute=_derive_ptype_intensity_component_ecmwf,
    ),
    "precip_total_cumulative": DeriveStrategy(
        id="precip_total_cumulative",
        required_inputs=("apcp_step",),
        output_var_key="precip_total",
        execute=_derive_precip_total_cumulative,
    ),
    "snowfall_total_10to1_cumulative": DeriveStrategy(
        id="snowfall_total_10to1_cumulative",
        required_inputs=("apcp_step", "csnow"),
        output_var_key="snowfall_total",
        execute=_derive_snowfall_total_10to1_cumulative,
    ),
    "snowfall_kuchera_total_cumulative": DeriveStrategy(
        id="snowfall_kuchera_total_cumulative",
        required_inputs=("apcp_step", "tmp850", "tmp700"),
        output_var_key="snowfall_kuchera_total",
        execute=_derive_snowfall_kuchera_total_cumulative,
    ),
    "ptype_accumulation_cumulative": DeriveStrategy(
        id="ptype_accumulation_cumulative",
        required_inputs=("apcp_step",),
        output_var_key=None,
        execute=_derive_ptype_accumulation_cumulative,
    ),
    "ptype_accumulation_ecmwf": DeriveStrategy(
        id="ptype_accumulation_ecmwf",
        required_inputs=("precip_total", "sf", "tmp2m", "tmp925", "tmp850"),
        output_var_key=None,
        execute=_derive_ptype_accumulation_ecmwf,
    ),
    "anomaly_departure": DeriveStrategy(
        id="anomaly_departure",
        required_inputs=(),
        output_var_key=None,
        execute=_derive_anomaly_departure,
    ),
    "precip_accum_anomaly_departure": DeriveStrategy(
        id="precip_accum_anomaly_departure",
        required_inputs=("precip_total",),
        output_var_key=None,
        execute=_derive_precip_accum_anomaly_departure,
    ),
}
