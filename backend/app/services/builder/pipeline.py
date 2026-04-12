"""Build pipeline: orchestrates fetch → warp → write → validate.

This is the single entry-point for producing V3 artifacts. For a given
model/region/var/fh it produces the published numeric/value metadata and, when
enabled, packed grid frames in the staging directory:

    fh{NNN}.val.cog.tif    — 1-band float32 value COG
    fh{NNN}.json           — sidecar metadata (per artifact contract)
    grid/fh{NNN}.l0.*      — packed grid frame + metadata

Published value outputs pass structural and sanity validation before being accepted.

Phase 1 scope: "simple" derivation path only (tmp2m, refc — single GRIB fetch).
Phase 2 adds wspd (vector magnitude) and radar_ptype (categorical combo).

CLI usage:
    python -m backend.app.services.builder.pipeline \\
        --model hrrr --region pnw --var tmp2m --fh 0 \\
        --data-root ./data/v3
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from app.config import grid_build_enabled
from app.services.builder.cog_writer import (
    _gdal,
    compute_transform_and_shape,
    get_grid_params,
    write_value_cog,
    warp_to_target_grid,
)
from app.services.builder.colorize import float_to_rgba
from app.services.builder.derive import FetchContext, derive_variable
from app.services.builder.fetch import (
    HerbieTransientUnavailableError,
    convert_units,
    fetch_variable,
    new_bundle_fetch_cache,
    product_hour_has_any_idx,
)
from app.services.colormaps import get_color_map_spec
from app.services.grid import (
    grid_frame_meta_path_for_run_root,
    grid_frame_path_for_run_root,
    write_grid_frame_for_run_root,
)
from app.services.render_resampling import resampling_name_for_kind

logger = logging.getLogger(__name__)

CONTRACT_VERSION = "3.0"
# Value COG base grid must match RGBA COG grid for render-time parity.
VALUE_HOVER_DOWNSAMPLE_FACTOR = 1
CANONICAL_COVERAGE = "conus"


def _warp_resampling_for_variable(*, model_id: str | None, var_key: str | None, kind: str | None) -> str:
    """Return warp resampling method for a variable.

    Uses the shared display-resampling policy so build-time warping stays
    aligned with tile extraction and frontend raster display.
    """
    return resampling_name_for_kind(
        model_id=str(model_id or ""),
        var_key=str(var_key or ""),
        kind=kind,
    )


def _prepare_display_data_for_colorize(
    warped_data: np.ndarray,
    var_spec: dict[str, Any],
    *,
    model_id: str | None = None,
    var_key: str | None = None,
) -> np.ndarray:
    _ = (var_spec, model_id, var_key)
    return warped_data


def _safe_float_hint(hints: dict[str, Any], key: str) -> float | None:
    raw = hints.get(key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _build_contour_metadata_for_variable(
    *,
    model: str,
    run_date: datetime,
    fh: int,
    product: str,
    var_key: str,
    region: str,
    model_plugin: Any,
    var_spec_model: Any,
    dst_transform: Any,
    staging_dir: Path,
    fetch_ctx: FetchContext | None,
) -> tuple[dict[str, Any] | None, Path | None]:
    selectors = getattr(var_spec_model, "selectors", None)
    hints = getattr(selectors, "hints", {}) if selectors is not None else {}
    if not isinstance(hints, dict):
        return None, None

    contour_component = str(hints.get("contour_component") or "").strip()
    if not contour_component:
        return None, None

    contour_interval = _safe_float_hint(hints, "contour_interval")
    if contour_interval is None or contour_interval <= 0.0:
        return None, None

    contour_key = str(hints.get("contour_key") or "contour").strip() or "contour"
    contour_label = str(hints.get("contour_label") or contour_key).strip() or contour_key
    contour_start = _safe_float_hint(hints, "contour_start")
    contour_end = _safe_float_hint(hints, "contour_end")
    contour_product = str(hints.get("contour_product") or product).strip() or product
    contour_conversion = str(hints.get("contour_conversion") or "").strip()

    component_spec = _resolve_model_var_spec(model, contour_component, model_plugin)
    component_patterns = _get_search_patterns(
        component_spec,
        model_plugin=model_plugin,
        var_key=contour_component,
        fh=fh,
        product=contour_product,
    )
    component_data = None
    src_crs = None
    src_transform = None
    last_exc: Exception | None = None
    for search_pattern in component_patterns:
        try:
            contour_request = model_plugin.herbie_request(
                product=contour_product,
                var_key=contour_component,
                run_date=run_date,
                fh=fh,
                search_pattern=search_pattern,
            )
            component_data, src_crs, src_transform = fetch_variable(
                model_id=model,
                product=contour_request.product,
                search_pattern=search_pattern,
                run_date=run_date,
                fh=fh,
                herbie_kwargs=getattr(contour_request, "herbie_kwargs", None),
                bundle_fetch_cache=getattr(fetch_ctx, "bundle_fetch_cache", None),
            )
            break
        except (HerbieTransientUnavailableError, RuntimeError) as exc:
            last_exc = exc
            continue
    if component_data is None or src_crs is None or src_transform is None:
        if last_exc is not None:
            raise last_exc
        return None, None
    warped_component, _ = warp_to_target_grid(
        component_data,
        src_crs,
        src_transform,
        model=model,
        region=region,
        resampling="bilinear",
        src_nodata=None,
        dst_nodata=float("nan"),
    )
    if contour_conversion:
        contour_capability = type("_ContourCapability", (), {"conversion": contour_conversion})()
        warped_component = convert_units(
            warped_component,
            contour_component,
            model_id=model,
            var_capability=contour_capability,
        )
    finite = warped_component[np.isfinite(warped_component)]
    if finite.size == 0:
        return None, None

    data_min = float(np.nanmin(finite))
    data_max = float(np.nanmax(finite))
    logger.info(
        "Contour source range: model=%s var=%s key=%s min=%.3f max=%.3f",
        model,
        var_key,
        contour_key,
        data_min,
        data_max,
    )
    level_min = contour_start if contour_start is not None else np.ceil(data_min / contour_interval) * contour_interval
    level_max = contour_end if contour_end is not None else np.floor(data_max / contour_interval) * contour_interval
    if not np.isfinite(level_min) or not np.isfinite(level_max) or level_max < level_min:
        return None, None

    levels: list[float] = []
    level = level_min
    while level <= level_max + (contour_interval * 0.25):
        levels.append(float(round(level, 6)))
        level += contour_interval
    if not levels:
        return None, None

    contours_dir = staging_dir / "contours"
    contour_path = contours_dir / f"fh{fh:03d}_{contour_key}.geojson"
    build_iso_contour_geojson(
        value_data=warped_component,
        value_transform=dst_transform,
        value_crs="EPSG:3857",
        out_geojson_path=contour_path,
        levels=levels,
    )
    metadata = {
        contour_key: {
            "format": "geojson",
            "path": str(contour_path.relative_to(staging_dir)).replace("\\", "/"),
            "srs": "EPSG:4326",
            "level": contour_interval,
            "levels": levels,
            "label": contour_label,
        }
    }
    return metadata, contours_dir


# ---------------------------------------------------------------------------
# Gate 1: structural validation (in-process via rasterio)
# ---------------------------------------------------------------------------

# Map GDAL type names (used by callers) to numpy/rasterio dtype strings.
_GDAL_DTYPE_TO_RASTERIO: dict[str, str] = {
    "Byte": "uint8",
    "UInt16": "uint16",
    "Int16": "int16",
    "UInt32": "uint32",
    "Int32": "int32",
    "Float32": "float32",
    "Float64": "float64",
}


def validate_cog(
    path: Path,
    *,
    expected_bands: int,
    expected_dtype: str,
    region: str,
    grid_meters: float,
) -> bool:
    """Validate a COG's structure using rasterio (in-process).

    Checks band count, band type, CRS, internal tiling, overview presence,
    pixel size, and COG layout metadata.  Returns True if all checks pass.
    """
    try:
        ds = rasterio.open(path)
    except Exception as exc:
        logger.error("Cannot open %s: %s", path, exc)
        return False

    ok = True

    try:
        # Band count
        if ds.count != expected_bands:
            logger.error("Band count: expected %d, got %d (%s)", expected_bands, ds.count, path)
            ok = False

        # Band dtype
        expected_rio_dtype = _GDAL_DTYPE_TO_RASTERIO.get(expected_dtype, expected_dtype.lower())
        if ds.dtypes[0] != expected_rio_dtype:
            logger.error("Band type: expected %s, got %s (%s)", expected_dtype, ds.dtypes[0], path)
            ok = False

        # CRS — must be EPSG:3857
        if ds.crs is None or ds.crs.to_epsg() != 3857:
            logger.error("CRS does not match EPSG:3857 (%s)", path)
            ok = False

        # Internal tiling (512×512)
        block_shapes = ds.block_shapes
        if block_shapes and block_shapes[0] != (512, 512):
            logger.error("Block size: expected (512, 512), got %s (%s)", block_shapes[0], path)
            ok = False

        # Overviews present
        if not ds.overviews(1):
            logger.error("No overviews found (%s)", path)
            ok = False

        # Pixel size matches grid_meters (±0.1m tolerance)
        pixel_x = abs(ds.transform.a)
        pixel_y = abs(ds.transform.e)
        if abs(pixel_x - grid_meters) > 0.1 or abs(pixel_y - grid_meters) > 0.1:
            logger.error(
                "Pixel size: expected %.1fm, got (%.1f, %.1f) (%s)",
                grid_meters, pixel_x, pixel_y, path,
            )
            ok = False

        # COG layout metadata
        image_structure = ds.tags(ns="IMAGE_STRUCTURE")
        layout = image_structure.get("LAYOUT", "")
        if layout != "COG":
            logger.error("Layout: expected 'COG', got %r (%s)", layout, path)
            ok = False
    finally:
        ds.close()

    if ok:
        logger.info("Gate 1 PASS: %s", path.name)
    return ok


# ---------------------------------------------------------------------------
# Gate 2: value sanity check
# ---------------------------------------------------------------------------


def check_value_sanity(
    val_path: Path,
    var_spec: dict[str, Any],
    var_spec_model: Any | None = None,
    var_capability: Any | None = None,
) -> bool:
    """Sanity-check pixel statistics of the produced value artifact."""
    ok = True
    spec_type = str(var_spec.get("type", "")).lower()
    model_kind = str(getattr(var_spec_model, "kind", "") or "").lower()
    model_units = getattr(var_spec_model, "units", None) if var_spec_model is not None else None
    is_non_physical_kind = spec_type in {"indexed", "categorical", "discrete"} or model_kind in {
        "indexed",
        "categorical",
        "discrete",
    }
    is_non_physical_units = model_units is None or var_spec.get("units") is None
    is_non_physical_flag = var_spec.get("physical") is False
    capability_frontend = getattr(var_capability, "frontend", {}) if var_capability is not None else {}
    allow_dry_frame = bool(var_spec.get("allow_dry_frame", False)) or bool(
        capability_frontend.get("allow_dry_frame") if isinstance(capability_frontend, dict) else False
    )
    skip_physical_range_checks = is_non_physical_kind or is_non_physical_units or is_non_physical_flag
    is_categorical_ptype = spec_type in {"discrete", "indexed"} and bool(var_spec.get("ptype_breaks"))

    max_nodata_ratio = 0.95

    # Categorical ptype products can legitimately be very sparse (near-dry scenes).
    # Keep guardrails, but relax thresholds enough to avoid rejecting valid frames.
    if is_categorical_ptype:
        max_nodata_ratio = 0.998    # 99.8%

    min_discrete_level = None
    levels = var_spec.get("levels")
    if isinstance(levels, list) and levels:
        try:
            min_discrete_level = float(levels[0])
        except (TypeError, ValueError):
            min_discrete_level = None

    # --- Value COG checks ---
    with rasterio.open(val_path) as src:
        values = src.read(1)
        finite_mask = np.isfinite(values)
        finite_count = int(np.count_nonzero(finite_mask))
        total_pixels = values.size

        # Nodata ratio sanity threshold
        nodata_ratio = 1.0 - (finite_count / total_pixels)
        if nodata_ratio > max_nodata_ratio:
            if is_categorical_ptype and finite_count == 0:
                logger.warning(
                    "Dry categorical ptype frame allowed: nodata ratio %.1f%% (%s)",
                    nodata_ratio * 100,
                    val_path,
                )
            else:
                logger.error(
                    "Value COG nodata ratio too high: %.1f%% (>%.1f%%) — "
                    "likely grid misalignment or empty fetch (%s)",
                    nodata_ratio * 100,
                    max_nodata_ratio * 100,
                    val_path,
                )
                ok = False

        # Value range: min ≠ max
        if finite_count > 0:
            vmin = float(np.nanmin(values[finite_mask]))
            vmax = float(np.nanmax(values[finite_mask]))
            if vmin == vmax:
                if allow_dry_frame and (min_discrete_level is None or vmin <= min_discrete_level):
                    logger.warning(
                        "Dry frame allowed: flat value field at %.2f (%s)",
                        vmin,
                        val_path,
                    )
                else:
                    logger.error(
                        "Value COG is flat (min==max==%.2f) — "
                        "likely constant input or unit conversion error (%s)",
                        vmin, val_path,
                    )
                    ok = False

            # Value range within VarSpec.range ± 20% (for physical continuous vars)
            spec_range = var_spec.get("range")
            if not skip_physical_range_checks and spec_range and len(spec_range) == 2:
                spec_min, spec_max = float(spec_range[0]), float(spec_range[1])
                span = spec_max - spec_min
                margin = span * 0.2
                if vmin < spec_min - margin or vmax > spec_max + margin:
                    logger.warning(
                        "Value range [%.1f, %.1f] outside spec range "
                        "[%.1f, %.1f] ± 20%% — may indicate unit error (%s)",
                        vmin, vmax, spec_min, spec_max, val_path,
                    )
                    # Warning only, not a hard fail

    if ok:
        logger.info("Value sanity PASS: %s", val_path.name)
    return ok


# ---------------------------------------------------------------------------
# Sidecar JSON metadata
# ---------------------------------------------------------------------------


def build_sidecar_json(
    *,
    model: str,
    region: str | None = None,
    run_id: str,
    var_id: str,
    fh: int,
    run_date: datetime,
    colorize_meta: dict[str, Any],
    var_spec: dict[str, Any],
    var_spec_model: Any | None = None,
    contours: dict[str, Any] | None = None,
    value_downsample_factor: int = 1,
    quality: str = "full",
    quality_flags: list[str] | None = None,
    valid_time_override: datetime | None = None,
) -> dict[str, Any]:
    """Build the sidecar metadata dict per the artifact contract.

    The sidecar JSON is written alongside each frame's COGs and provides
    the frontend with all information needed to render legends and tooltips.
    """
    valid_time = valid_time_override or (run_date + timedelta(hours=fh))

    model_kind = getattr(var_spec_model, "kind", None) if var_spec_model is not None else None
    model_units = getattr(var_spec_model, "units", None) if var_spec_model is not None else None

    kind = colorize_meta.get("kind") or model_kind or var_spec.get("type", "continuous")
    display_kind = var_spec.get("display_palette_kind") or kind
    units = model_units or colorize_meta.get("units") or var_spec.get("units", "")

    # Build legend
    legend = _build_legend(str(display_kind), var_spec, colorize_meta)

    sidecar: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "model": model,
        "run": run_id,
        "var": var_id,
        "fh": fh,
        "valid_time": valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "units": _format_units(units),
        "kind": display_kind,
        "min": colorize_meta.get("min"),
        "max": colorize_meta.get("max"),
        "legend": legend,
        "quality": "degraded" if str(quality).strip().lower() == "degraded" else "full",
        "quality_flags": [
            item for item in dict.fromkeys(str(flag).strip() for flag in (quality_flags or []))
            if item
        ],
    }
    display_name = colorize_meta.get("display_name") or var_spec.get("display_name") or getattr(var_spec_model, "name", None)
    if isinstance(display_name, str) and display_name.strip():
        sidecar["display_name"] = display_name.strip()

    if region:
        sidecar["region"] = region

    if value_downsample_factor > 1:
        sidecar["hover_value_downsample_factor"] = int(value_downsample_factor)

    # Preserve optional legend-grouping metadata for categorical ptype variables.
    for key in ("ptype_order", "ptype_breaks", "ptype_levels", "bins_per_ptype"):
        value = colorize_meta.get(key)
        if value is None:
            value = var_spec.get(key)
        if value is not None:
            sidecar[key] = value

    selectors = getattr(var_spec_model, "selectors", None) if var_spec_model is not None else None
    hints = getattr(selectors, "hints", {}) if selectors is not None else {}
    if isinstance(hints, dict):
        composite_layers_raw = str(hints.get("composite_layers") or "").strip()
        composite_mode = str(hints.get("composite_mode") or "").strip()
        if composite_layers_raw:
            composite_layers: list[dict[str, str]] = []
            for item in composite_layers_raw.split(";"):
                token = item.strip()
                if not token or ":" not in token:
                    continue
                layer_id, component_var = token.split(":", 1)
                layer_id = layer_id.strip()
                component_var = component_var.strip()
                if not layer_id or not component_var:
                    continue
                composite_layers.append({"id": layer_id, "var": component_var})
            if composite_layers:
                sidecar["composite_layers"] = composite_layers
                if composite_mode:
                    sidecar["composite_mode"] = composite_mode

    if contours:
        sidecar["contours"] = contours

    return sidecar


def build_iso_contour_geojson(
    *,
    value_data: np.ndarray,
    value_transform: Any,
    value_crs: str = "EPSG:3857",
    out_geojson_path: Path,
    level: float | None = None,
    levels: list[float] | tuple[float, ...] | None = None,
    srs: str = "EPSG:4326",
) -> None:
    """Generate iso-contour GeoJSON from a full-resolution value grid.

    Writes a temporary in-memory-source GTiff from the provided
    array/transform, then warps/contours via GDAL CLI. This avoids depending
    on the on-disk hover value COG resolution.
    """
    out_geojson_path.parent.mkdir(parents=True, exist_ok=True)

    gdalwarp_bin = _gdal("gdalwarp")
    gdal_contour_bin = _gdal("gdal_contour")

    tmp_path: Path | None = None
    src_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as src_file:
            src_path = Path(src_file.name)
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)

        value_f32 = value_data.astype(np.float32, copy=False)
        with rasterio.open(
            src_path,
            "w",
            driver="GTiff",
            height=value_f32.shape[0],
            width=value_f32.shape[1],
            count=1,
            dtype="float32",
            crs=value_crs,
            transform=value_transform,
            nodata=float("nan"),
        ) as src_ds:
            src_ds.write(value_f32, 1)

        subprocess.run(
            [
                gdalwarp_bin,
                "-t_srs",
                srs,
                "-r",
                "bilinear",
                "-of",
                "GTiff",
                str(src_path),
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        contour_levels = [float(item) for item in (levels or []) if np.isfinite(float(item))]
        if not contour_levels:
            if level is None:
                raise ValueError("build_iso_contour_geojson requires level or levels")
            contour_levels = [float(level)]

        contour_cmd = [
            gdal_contour_bin,
            "-a",
            "value",
            "-f",
            "GeoJSON",
        ]
        for contour_level in contour_levels:
            contour_cmd.extend(["-fl", str(contour_level)])
        contour_cmd.extend([str(tmp_path), str(out_geojson_path)])
        subprocess.run(
            contour_cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            payload = json.loads(out_geojson_path.read_text())
            features = payload.get("features") if isinstance(payload, dict) else None
            feature_count = len(features) if isinstance(features, list) else 0
            logger.info(
                "Contour GeoJSON generated: path=%s features=%d levels=%s",
                out_geojson_path,
                feature_count,
                contour_levels,
            )
            if feature_count == 0:
                logger.warning(
                    "Contour GeoJSON empty: path=%s levels=%s",
                    out_geojson_path,
                    contour_levels,
                )
        except Exception:
            logger.warning("Unable to inspect contour GeoJSON output: %s", out_geojson_path)
    finally:
        if src_path is not None:
            try:
                if src_path.exists():
                    src_path.unlink()
            except Exception:
                pass
        if tmp_path is not None:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass


def _build_legend(
    kind: str,
    var_spec: dict[str, Any],
    colorize_meta: dict[str, Any],
) -> dict[str, Any]:
    """Build the legend block for the sidecar JSON.

    For continuous vars: gradient with evenly-spaced or explicit stops.
    For discrete/indexed: discrete with level/color stops.
    """
    if kind == "continuous":
        # Check for explicit legend_stops first
        legend_stops = var_spec.get("legend_stops") or colorize_meta.get("legend_stops")
        if legend_stops:
            # legend_stops is a list of (value, hex_color) tuples
            stops = [[float(v), c] for v, c in legend_stops]
        else:
            anchors = (
                var_spec.get("color_anchors")
                or var_spec.get("anchors")
                or colorize_meta.get("color_anchors")
                or colorize_meta.get("anchors")
            )
            if anchors:
                # Anchors are already value→color stops.
                stops = [[float(v), c] for v, c in anchors]
            else:
                # Generate stops from range + colors
                spec_range = var_spec.get("range", colorize_meta.get("range", [0, 1]))
                colors = var_spec.get("colors", colorize_meta.get("colors", []))
                if not colors:
                    raise ValueError(
                        f"Continuous var spec requires 'colors' but got none "
                        f"(var_spec keys: {sorted(var_spec.keys())})"
                    )
                rmin, rmax = float(spec_range[0]), float(spec_range[1])
                n = len(colors)
                stops = []
                for i, color in enumerate(colors):
                    val = rmin + (rmax - rmin) * i / max(n - 1, 1)
                    stops.append([round(val, 1), color])

        return {"type": "gradient", "stops": stops}

    else:  # discrete / indexed
        legend_stops = var_spec.get("legend_stops") or colorize_meta.get("legend_stops")
        if legend_stops:
            stops = [[float(v), c] for v, c in legend_stops]
            return {"type": "discrete", "stops": stops}

        levels = var_spec.get("levels", colorize_meta.get("levels", []))
        colors = var_spec.get("colors", colorize_meta.get("colors", []))

        ptype_order = colorize_meta.get("ptype_order") or var_spec.get("ptype_order")
        ptype_breaks = colorize_meta.get("ptype_breaks") or var_spec.get("ptype_breaks")
        ptype_levels = colorize_meta.get("ptype_levels") or var_spec.get("ptype_levels")

        if (
            isinstance(ptype_order, list)
            and isinstance(ptype_breaks, dict)
            and isinstance(ptype_levels, dict)
        ):
            stops: list[list[Any]] = []
            for ptype in ptype_order:
                boundary = ptype_breaks.get(ptype)
                type_levels = ptype_levels.get(ptype)
                if not isinstance(boundary, dict) or not isinstance(type_levels, list):
                    continue
                offset = int(boundary.get("offset", -1))
                count = int(boundary.get("count", 0))
                if offset < 0 or count <= 0:
                    continue
                max_items = min(count, len(type_levels), len(colors) - offset)
                if max_items <= 0:
                    continue
                for idx in range(max_items):
                    stops.append([float(type_levels[idx]), colors[offset + idx]])
            if stops:
                return {"type": "discrete", "stops": stops}

        # Pair levels with colors (take min length)
        n = min(len(levels), len(colors))
        stops = [[float(levels[i]), colors[i]] for i in range(n)]

        return {"type": "discrete", "stops": stops}


def _format_units(units: str) -> str:
    """Normalize unit strings for display (e.g. 'F' → '°F')."""
    mapping = {
        "F": "°F",
        "C": "°C",
        "K": "K",
        "mph": "mph",
        "m/s": "m/s",
        "dBZ": "dBZ",
        "mm/hr": "mm/hr",
        "in/hr": "in/hr",
        "in": "in",
    }
    return mapping.get(units, units)


# ---------------------------------------------------------------------------
# GRIB search pattern lookup
# ---------------------------------------------------------------------------


def _get_search_patterns(
    var_spec_model: Any,
    *,
    model_plugin: Any | None = None,
    var_key: str | None = None,
    fh: int | None = None,
    product: str | None = None,
) -> list[str]:
    """Extract Herbie search patterns from a model VarSpec.

    The VarSpec.selectors.search list contains GRIB index patterns.
    Patterns are tried in order.
    """
    if model_plugin is not None and isinstance(var_key, str) and var_key.strip():
        resolved = model_plugin.search_patterns_for_var(
            var_key=var_key,
            fh=fh,
            product=product,
            var_spec=var_spec_model,
        )
        if resolved:
            return [str(pattern) for pattern in resolved if str(pattern).strip()]
    selectors = getattr(var_spec_model, "selectors", None)
    if selectors is None:
        raise ValueError("VarSpec has no selectors")
    search_list = getattr(selectors, "search", [])
    if not search_list:
        raise ValueError(
            f"VarSpec for {getattr(var_spec_model, 'id', '?')!r} has no "
            f"search patterns — cannot determine GRIB message to fetch"
        )
    return [str(pattern) for pattern in search_list if str(pattern).strip()]


def _derive_strategy_id(var_spec_model: Any, var_capability: Any | None) -> str:
    derive_kind = (
        getattr(var_capability, "derive_strategy_id", None)
        or getattr(var_spec_model, "derive", None)
        or ""
    )
    return str(derive_kind).strip()


def _required_products_for_var(
    *,
    default_product: str,
    var_spec_model: Any,
    var_capability: Any | None,
) -> list[str]:
    default_norm = str(default_product).strip() or "sfc"
    required: list[str] = []

    def _push(product_name: str) -> None:
        normalized = str(product_name).strip()
        if normalized and normalized not in required:
            required.append(normalized)

    derive_kind = _derive_strategy_id(var_spec_model, var_capability)
    if not derive_kind:
        _push(default_norm)
        return required

    hints = getattr(getattr(var_spec_model, "selectors", None), "hints", {}) or {}
    if derive_kind == "snowfall_kuchera_total_cumulative":
        apcp_product = str(hints.get("kuchera_apcp_product", "")).strip() or default_norm
        profile_product = str(hints.get("kuchera_profile_product", "")).strip() or default_norm
        _push(apcp_product)
        _push(profile_product)
        return required

    _push(default_norm)
    return required


def _ensure_products_ready(
    *,
    model: str,
    model_plugin: Any,
    run_date: datetime,
    fh: int,
    var_key: str,
    required_products: list[str],
    readiness_cache: dict[str, bool] | None = None,
) -> None:
    missing_products: list[str] = []
    for product_name in required_products:
        request = model_plugin.herbie_request(
            product=product_name,
            var_key=var_key,
            run_date=run_date,
            fh=fh,
        )
        readiness_key = f"{request.model}|{request.product}"
        if readiness_cache is not None and readiness_key in readiness_cache:
            ready = bool(readiness_cache[readiness_key])
        elif readiness_cache is not None and product_name in readiness_cache:
            ready = bool(readiness_cache[product_name])
        else:
            ready = product_hour_has_any_idx(
                model_id=model,
                product=request.product,
                run_date=run_date,
                fh=fh,
                herbie_kwargs=getattr(request, "herbie_kwargs", None),
            )
            if readiness_cache is not None:
                readiness_cache[product_name] = bool(ready)
                readiness_cache[readiness_key] = bool(ready)
        if not ready:
            missing_products.append(request.product)

    if missing_products:
        run_id = _run_id_from_date(run_date)
        raise HerbieTransientUnavailableError(
            f"Herbie hour not ready for {model}/{run_id}/{var_key}/fh{fh:03d}; "
            f"missing_idx_products={missing_products}"
        )


# ---------------------------------------------------------------------------
# Frame builder — the main orchestration function
# ---------------------------------------------------------------------------


def build_frame(
    *,
    model: str,
    region: str,
    var_id: str,
    fh: int,
    run_date: datetime,
    data_root: Path,
    product: str = "sfc",
    model_plugin: Any = None,
    fetch_ctx: FetchContext | None = None,
    readiness_cache: dict[str, bool] | None = None,
    log_fetch_cache_stats: bool = True,
    derive_component_warp_cache: bool = False,
) -> Path | None:
    """Build one frame's artifacts: RGBA COG + value COG + sidecar JSON.

    This is the core orchestration function implementing the pipeline:
        fetch → unit convert → warp → colorize → write COGs → validate → sidecar

    Parameters
    ----------
    model : str
        Model identifier (e.g. "hrrr").
    region : str
        Region identifier (e.g. "pnw", "conus").
    var_id : str
        Variable identifier (e.g. "tmp2m").
    fh : int
        Forecast hour.
    run_date : datetime
        Model run initialization time (UTC).
    data_root : Path
        Root of the data directory (e.g. ./data/v3).
    product : str
        Herbie product string (default "sfc").
    model_plugin : ModelPlugin, optional
        Model plugin instance for VarSpec lookup.
        If None, uses the model registry.

    Returns
    -------
    Path to the staging directory with the three artifacts,
    or None if validation failed and the frame was rejected.
    """
    run_id = _run_id_from_date(run_date)
    fh_str = f"fh{fh:03d}"
    local_fetch_ctx = fetch_ctx or FetchContext(coverage=region)
    setattr(local_fetch_ctx, "data_root", str(data_root))
    setattr(local_fetch_ctx, "run_id", run_id)
    if getattr(local_fetch_ctx, "bundle_fetch_cache", None) is None:
        local_fetch_ctx.bundle_fetch_cache = new_bundle_fetch_cache()
    fetch_stats_logged = False

    def _log_fetch_cache_stats_once() -> None:
        nonlocal fetch_stats_logged
        if not log_fetch_cache_stats:
            return
        if fetch_stats_logged:
            return
        fetch_stats_logged = True
        hits = int(local_fetch_ctx.stats.get("hits", 0))
        misses = int(local_fetch_ctx.stats.get("misses", 0))
        logger.info("fetch_cache hits=%d misses=%d", hits, misses)

    if region != CANONICAL_COVERAGE:
        logger.error("Rejected non-canonical coverage for build_frame: %s (expected %s)", region, CANONICAL_COVERAGE)
        _log_fetch_cache_stats_once()
        return None

    logger.info("Building frame: %s/%s/%s/%s (coverage=%s)", model, run_id, var_id, fh_str, region)

    # --- Resolve specs ---
    resolved_plugin = model_plugin or _resolve_model_plugin(model)
    var_key = resolved_plugin.normalize_var_id(var_id)
    var_spec_model = _resolve_model_var_spec(model, var_key, resolved_plugin)
    var_capability = _resolve_model_var_capability(model, var_key, resolved_plugin)
    color_map_id = getattr(var_capability, "color_map_id", None)
    if not isinstance(color_map_id, str) or not color_map_id.strip():
        logger.error(
            "Missing color_map_id in model capability for model=%s var_key=%s; build aborted",
            model,
            var_key,
        )
        _log_fetch_cache_stats_once()
        return None
    color_map_id = color_map_id.strip()
    try:
        var_spec_colormap = get_color_map_spec(color_map_id)
    except KeyError:
        logger.error("No colormap spec for model=%s var_key=%s color_map_id=%s", model, var_key, color_map_id)
        _log_fetch_cache_stats_once()
        return None

    kind = (
        getattr(var_capability, "kind", None)
        or getattr(var_spec_model, "kind", None)
        or var_spec_colormap.get("type", "continuous")
    )
    kind_normalized = str(kind).strip().lower() or "continuous"
    selectors = getattr(var_spec_model, "selectors", None)
    hints = getattr(selectors, "hints", {}) if selectors is not None else {}
    if not isinstance(hints, dict):
        hints = {}
    source_product = str(hints.get("product") or product).strip() or product
    warp_resampling = _warp_resampling_for_variable(
        model_id=model,
        var_key=var_key,
        kind=kind_normalized,
    )
    search_patterns = None if getattr(var_spec_model, "derived", False) else _get_search_patterns(
        var_spec_model,
        model_plugin=resolved_plugin,
        var_key=var_key,
        fh=fh,
        product=source_product,
    )
    required_products = _required_products_for_var(
        default_product=product,
        var_spec_model=var_spec_model,
        var_capability=var_capability,
    )

    # --- Staging directory ---
    staging_dir = data_root / "staging" / model / run_id / var_key
    staging_dir.mkdir(parents=True, exist_ok=True)

    val_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"
    contour_geojson_path: Path | None = None
    grid_frame_path: Path | None = None
    grid_frame_meta_path: Path | None = None
    frame_quality = "full"
    frame_quality_flags: list[str] = []

    try:
        _ensure_products_ready(
            model=model,
            model_plugin=resolved_plugin,
            run_date=run_date,
            fh=fh,
            var_key=var_key,
            required_products=[source_product],
            readiness_cache=readiness_cache,
        )
        if getattr(var_spec_model, "derived", False):
            # --- Step 1/2: Derive from component GRIB fields ---
            logger.info("Step 1/6: Deriving variable components")
            converted_data, src_crs, src_transform = derive_variable(
                model_id=model,
                var_key=var_key,
                product=product,
                run_date=run_date,
                fh=fh,
                var_spec_model=var_spec_model,
                var_capability=var_capability,
                model_plugin=resolved_plugin,
                fetch_ctx=local_fetch_ctx,
                derive_component_target_grid=(
                    {
                        "region": region,
                        "id": f"{model}:{region}:{get_grid_params(model, region)[1]:.1f}m",
                    }
                    if derive_component_warp_cache
                    else None
                ),
                derive_component_resampling=warp_resampling if derive_component_warp_cache else None,
            )
            quality_meta = local_fetch_ctx.derive_quality.get((var_key, int(fh)), {})
            frame_quality = (
                "degraded"
                if str(quality_meta.get("quality", "full")).strip().lower() == "degraded"
                else "full"
            )
            flags_raw = quality_meta.get("quality_flags", [])
            if isinstance(flags_raw, list):
                frame_quality_flags = [
                    item for item in dict.fromkeys(str(flag).strip() for flag in flags_raw)
                    if item
                ]
        else:
            # --- Step 1: Fetch GRIB data ---
            logger.info("Step 1/6: Fetching GRIB data")
            if search_patterns is None or not search_patterns:
                raise ValueError(
                    f"No search patterns resolved for non-derived var {var_id!r}"
                )
            last_exc: Exception | None = None
            raw_data: np.ndarray | None = None
            src_crs = None
            src_transform = None
            for pattern_idx, search_pattern in enumerate(search_patterns, start=1):
                try:
                    source_request = resolved_plugin.herbie_request(
                        product=source_product,
                        var_key=var_key,
                        run_date=run_date,
                        fh=fh,
                        search_pattern=search_pattern,
                    )
                    raw_data, src_crs, src_transform = fetch_variable(  # type: ignore[misc]
                        model_id=model,
                        product=source_request.product,
                        search_pattern=search_pattern,
                        run_date=run_date,
                        fh=fh,
                        herbie_kwargs=getattr(source_request, "herbie_kwargs", None),
                        bundle_fetch_cache=getattr(local_fetch_ctx, "bundle_fetch_cache", None),
                    )
                    if pattern_idx > 1:
                        logger.info(
                            "Fetched via fallback search pattern %d/%d for %s: %s",
                            pattern_idx,
                            len(search_patterns),
                            var_key,
                            search_pattern,
                        )
                    break
                except (HerbieTransientUnavailableError, RuntimeError) as exc:
                    last_exc = exc
                    if pattern_idx < len(search_patterns):
                        logger.warning(
                            "Search pattern %d/%d unavailable for %s fh%03d (%s); trying next pattern",
                            pattern_idx,
                            len(search_patterns),
                            var_key,
                            fh,
                            search_pattern,
                        )
                        continue
                    raise
            if raw_data is None or src_crs is None or src_transform is None:
                if last_exc is not None:
                    raise last_exc
                raise RuntimeError(
                    f"Unable to fetch non-derived var {var_key!r} for fh{fh:03d}; no usable search pattern"
                )

            # --- Step 2: Unit conversion ---
            logger.info("Step 2/6: Unit conversion")
            converted_data = convert_units(
                raw_data,
                var_key=var_key,
                model_id=model,
                var_capability=var_capability,
            )

        # --- Step 3: Warp to target grid ---
        if getattr(var_spec_model, "derived", False) and derive_component_warp_cache:
            logger.info("Step 3/6: Warping to target grid (reused cached component warps)")
            warped_data = converted_data.astype(np.float32, copy=False)
            dst_transform = src_transform
        else:
            logger.info("Step 3/6: Warping to target grid (resampling=%s)", warp_resampling)
            warped_data, dst_transform = warp_to_target_grid(
                converted_data,
                src_crs,
                src_transform,
                model=model,
                region=region,
                resampling=warp_resampling,
                src_nodata=None,
                dst_nodata=float("nan"),
            )

        # --- Step 4: Colorize ---
        logger.info("Step 4/6: Colorizing")
        display_data = _prepare_display_data_for_colorize(
            warped_data,
            var_spec_colormap,
            model_id=model,
            var_key=var_key,
        )
        _, colorize_meta = float_to_rgba(
            display_data,
            color_map_id,
            meta_var_key=var_key,
        )

        # --- Step 5: Write artifacts ---
        logger.info("Step 5/6: Writing artifacts")
        write_value_cog(
            warped_data, val_path,
            model=model, region=region,
            downsample_factor=VALUE_HOVER_DOWNSAMPLE_FACTOR,
        )

        # --- Step 6: Validate (Gates 1 & 2) ---
        logger.info("Step 6/6: Validating artifacts")
        _, grid_m = get_grid_params(model, region)

        # Gate 1: structural validation
        if not validate_cog(
            val_path,
            expected_bands=1,
            expected_dtype="Float32",
            region=region,
            grid_meters=grid_m * VALUE_HOVER_DOWNSAMPLE_FACTOR,
        ):
            logger.error("Value COG validation failed — rejecting frame")
            _cleanup_artifacts(val_path, sidecar_path, contour_geojson_path, grid_frame_path, grid_frame_meta_path)
            return None

        if not check_value_sanity(
            val_path,
            var_spec_colormap,
            var_spec_model=var_spec_model,
            var_capability=var_capability,
        ):
            logger.error("Value sanity failed — rejecting frame")
            _cleanup_artifacts(val_path, sidecar_path, contour_geojson_path, grid_frame_path, grid_frame_meta_path)
            return None

        contours_meta, contour_geojson_path = _build_contour_metadata_for_variable(
            model=model,
            run_date=run_date,
            fh=fh,
            product=product,
            var_key=var_key,
            region=region,
            model_plugin=resolved_plugin,
            var_spec_model=var_spec_model,
            dst_transform=dst_transform,
            staging_dir=staging_dir,
            fetch_ctx=local_fetch_ctx,
        )

        # --- Write sidecar JSON ---
        sidecar = build_sidecar_json(
            model=model,
            run_id=run_id,
            var_id=var_key,
            fh=fh,
            run_date=run_date,
            colorize_meta=colorize_meta,
            var_spec=var_spec_colormap,
            var_spec_model=var_spec_model,
            contours=contours_meta,
            value_downsample_factor=VALUE_HOVER_DOWNSAMPLE_FACTOR,
            quality=frame_quality,
            quality_flags=frame_quality_flags,
        )
        _write_json_atomic(sidecar_path, sidecar)

        if grid_build_enabled():
            run_root = data_root / "staging" / model / run_id
            grid_frame_path = grid_frame_path_for_run_root(run_root, var_key, fh)
            grid_frame_meta_path = grid_frame_meta_path_for_run_root(run_root, var_key, fh)
            write_grid_frame_for_run_root(
                run_root=run_root,
                model=model,
                var=var_key,
                fh=fh,
                values=warped_data,
                transform=dst_transform,
            )

        logger.info(
            "Frame complete: %s/%s/%s/%s/%s "
            "(Val: %s, JSON: %s%s)",
            model, region, run_id, var_key, fh_str,
            _file_size_str(val_path),
            _file_size_str(sidecar_path),
            f", Grid: {_file_size_str(grid_frame_path)}" if grid_frame_path is not None else "",
        )
        return staging_dir

    except HerbieTransientUnavailableError as exc:
        logger.warning(
            "Build transiently unavailable for %s/%s/%s/%s/%s: %s",
            model,
            region,
            run_id,
            var_key,
            fh_str,
            exc,
        )
        _cleanup_artifacts(val_path, sidecar_path, contour_geojson_path, grid_frame_path, grid_frame_meta_path)
        return None

    except Exception:
        logger.exception(
            "Build failed for %s/%s/%s/%s/%s",
            model, region, run_id, var_key, fh_str,
        )
        _cleanup_artifacts(val_path, sidecar_path, contour_geojson_path, grid_frame_path, grid_frame_meta_path)
        return None
    finally:
        _log_fetch_cache_stats_once()


def build_frame_bundle(
    *,
    model: str,
    region: str,
    var_keys: list[str],
    fh: int,
    run_date: datetime,
    data_root: Path,
    product: str = "sfc",
    model_plugin: Any = None,
    include_timings: bool = False,
) -> dict[str, Path | None] | tuple[dict[str, Path | None], dict[str, int]]:
    """Build multiple variables for one fh with shared fetch/warp caches."""
    resolved_plugin = model_plugin or _resolve_model_plugin(model)
    shared_ctx = FetchContext(coverage=region)
    readiness_cache: dict[str, bool] = {}

    ordered_vars: list[str] = []
    seen: set[str] = set()
    for raw_var in var_keys:
        normalized = resolved_plugin.normalize_var_id(raw_var)
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered_vars.append(normalized)

    results: dict[str, Path | None] = {}
    timings_ms: dict[str, int] = {}
    for var_key in ordered_vars:
        started_at = time.perf_counter()
        results[var_key] = build_frame(
            model=model,
            region=region,
            var_id=var_key,
            fh=fh,
            run_date=run_date,
            data_root=data_root,
            product=product,
            model_plugin=resolved_plugin,
            fetch_ctx=shared_ctx,
            readiness_cache=readiness_cache,
            log_fetch_cache_stats=False,
            derive_component_warp_cache=True,
        )
        timings_ms[var_key] = int((time.perf_counter() - started_at) * 1000)

    fetch_hits = int(shared_ctx.stats.get("hits", 0))
    fetch_misses = int(shared_ctx.stats.get("misses", 0))
    warp_hits = int(shared_ctx.warp_stats.get("hits", 0))
    warp_misses = int(shared_ctx.warp_stats.get("misses", 0))
    logger.info(
        "derive_bundle fh%03d vars=%s fetch_cache hits=%d misses=%d warp_cache hits=%d misses=%d",
        fh,
        ordered_vars,
        fetch_hits,
        fetch_misses,
        warp_hits,
        warp_misses,
    )
    if include_timings:
        return results, timings_ms
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_model_var_spec(
    model: str,
    var_key: str,
    model_plugin: Any = None,
) -> Any:
    """Resolve the VarSpec from model plugin or registry."""
    plugin = model_plugin or _resolve_model_plugin(model)
    normalized = plugin.normalize_var_id(var_key)
    spec = plugin.get_var(normalized)
    if spec is None:
        raise ValueError(
            f"Variable {normalized!r} not found in {model!r} model plugin"
        )
    return spec


def _resolve_model_var_capability(
    model: str,
    var_key: str,
    model_plugin: Any = None,
) -> Any:
    plugin = model_plugin or _resolve_model_plugin(model)
    normalized = plugin.normalize_var_id(var_key)
    capability = plugin.get_var_capability(normalized)
    if capability is not None:
        return capability
    raise ValueError(
        f"Variable capability missing for {model!r}/{normalized!r}; "
        "plugin capabilities are required for all buildable variables"
    )


def _resolve_model_plugin(model: str) -> Any:
    """Resolve a model plugin by id."""
    from app.models.registry import MODEL_REGISTRY

    plugin = MODEL_REGISTRY.get(model)
    if plugin is None:
        raise ValueError(f"Unknown model: {model!r}")
    return plugin


def _run_id_from_date(run_date: datetime) -> str:
    """Format a run date as the canonical run_id string.

    Example: datetime(2026, 2, 17, 6) → "20260217_06z"
    """
    return run_date.strftime("%Y%m%d_%Hz")


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON to a file atomically via tmp → rename."""
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.write("\n")
    tmp_path.rename(path)
    logger.debug("Wrote sidecar JSON: %s", path)


def _cleanup_artifacts(*paths: Path | None) -> None:
    """Remove artifact files that failed validation."""
    for p in paths:
        if p is None or not p.exists():
            continue
        if p.is_dir():
            for child in sorted(p.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            p.rmdir()
        else:
            p.unlink()
        logger.debug("Cleaned up: %s", p)


def _file_size_str(path: Path) -> str:
    """Human-readable file size."""
    if not path.exists():
        return "??"
    size = path.stat().st_size
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for building a single frame."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Build V3 artifacts for a single frame",
        prog="python -m backend.app.services.builder.pipeline",
    )
    parser.add_argument("--model", required=True, help="Model id (e.g. hrrr)")
    parser.add_argument("--region", required=True, help="Region id (e.g. pnw, conus)")
    parser.add_argument("--var", required=True, dest="var_id", help="Variable id (e.g. tmp2m)")
    parser.add_argument("--fh", required=True, type=int, help="Forecast hour")
    parser.add_argument("--data-root", required=True, type=Path, help="Data root directory")
    parser.add_argument(
        "--run",
        default=None,
        help="Run id (e.g. 20260217_06z). Defaults to latest available.",
    )
    parser.add_argument("--product", default="sfc", help="Herbie product (default: sfc)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    # Parse or determine run date
    if args.run:
        run_date = _parse_run_id(args.run)
    else:
        run_date = _latest_run_date(args.model)
        logger.info("Using latest run: %s", _run_id_from_date(run_date))

    result = build_frame(
        model=args.model,
        region=args.region,
        var_id=args.var_id,
        fh=args.fh,
        run_date=run_date,
        data_root=args.data_root,
        product=args.product,
    )

    if result is None:
        logger.error("Build FAILED — frame rejected")
        raise SystemExit(1)

    logger.info("Build SUCCESS — artifacts in %s", result)


def _parse_run_id(run_id: str) -> datetime:
    """Parse a run_id string like '20260217_06z' into a datetime."""
    # Strip trailing 'z' if present
    clean = run_id.rstrip("zZ")
    try:
        return datetime.strptime(clean, "%Y%m%d_%H").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.strptime(clean, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(
            f"Cannot parse run_id {run_id!r}. "
            f"Expected format: YYYYMMDD_HHz (e.g. 20260217_06z)"
        )


def _latest_run_date(model: str) -> datetime:
    """Determine the latest available run date for a model.

    Uses a simple heuristic: round the current UTC time down to the
    nearest synoptic cycle, then step back one cycle to ensure data
    availability (GRIB data typically has ~2h latency).

    HRRR: hourly cycles (round back 2 hours)
    GFS:  6-hourly cycles (round back to last 00/06/12/18, minus 4 hours)
    """
    now = datetime.now(timezone.utc)
    plugin = _resolve_model_plugin(model)
    run_discovery = plugin.run_discovery_config() if hasattr(plugin, "run_discovery_config") else {}
    fallback_lag_hours = 3
    cadence_hours = 1
    try:
        fallback_lag_hours = max(0, int(run_discovery.get("fallback_lag_hours", fallback_lag_hours)))
    except (TypeError, ValueError):
        fallback_lag_hours = 3
    try:
        cadence_hours = max(1, int(run_discovery.get("cycle_cadence_hours", cadence_hours)))
    except (TypeError, ValueError):
        cadence_hours = 1

    target = now - timedelta(hours=fallback_lag_hours)
    aligned_hour = (target.hour // cadence_hours) * cadence_hours
    return target.replace(hour=aligned_hour, minute=0, second=0, microsecond=0)


if __name__ == "__main__":
    main()
