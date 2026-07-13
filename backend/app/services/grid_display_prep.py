from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.ndimage import gaussian_filter, zoom  # type: ignore[import-untyped]


@dataclass(frozen=True)
class GridDisplayPrepConfig:
    id: str
    upscale_factor: int = 1
    smooth_sigma: float | None = None
    preserve_zero_support: bool = False
    support_min_value: float | None = None
    support_coverage_threshold: float = 1e-3
    categorical_nearest: bool = False
    render_categorical_nearest: bool | None = None
    # Most prepped variables are physically non-negative (precip/snow totals,
    # palette indices), so negatives are numeric noise and get zeroed. Set
    # False for variables where negative values are real signal (dBZ).
    clamp_negative: bool = True


_GRID_DISPLAY_PREP_BY_MODEL_VAR: dict[tuple[str, str], GridDisplayPrepConfig] = {
    ("gefs", "snowfall_total__mean"): GridDisplayPrepConfig(
        id="gefs_snowfall_total_display_v1",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
    ),
    ("gefs", "precip_total__mean"): GridDisplayPrepConfig(
        id="gefs_precip_total_display_v1",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
        support_min_value=0.01,
        support_coverage_threshold=0.5,
    ),
    ("gfs", "precip_total"): GridDisplayPrepConfig(
        id="gfs_precip_total_display_v2",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
        support_min_value=0.01,
        support_coverage_threshold=0.5,
    ),
    ("gfs", "snowfall_total"): GridDisplayPrepConfig(
        id="gfs_snowfall_total_display_v1",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
    ),
    ("gfs", "snowfall_kuchera_total"): GridDisplayPrepConfig(
        id="gfs_snowfall_total_display_v1",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
    ),
    ("ecmwf", "snowfall_total"): GridDisplayPrepConfig(
        id="ecmwf_snowfall_total_display_v2",
        upscale_factor=1,
        smooth_sigma=None,
        preserve_zero_support=True,
    ),
    ("ecmwf", "snowfall_kuchera_total"): GridDisplayPrepConfig(
        id="ecmwf_snowfall_total_display_v2",
        upscale_factor=1,
        smooth_sigma=None,
        preserve_zero_support=True,
    ),
    ("nbm", "precip_total"): GridDisplayPrepConfig(
        id="nbm_precip_total_display_v2",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
        support_min_value=0.01,
        support_coverage_threshold=0.5,
    ),
    ("nbm", "snowfall_total"): GridDisplayPrepConfig(
        id="nbm_snowfall_total_display_v1",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
    ),
    ("mrms", "reflectivity"): GridDisplayPrepConfig(
        id="mrms_reflectivity_display_v2",
        upscale_factor=1,
        smooth_sigma=0.45,
        preserve_zero_support=False,
        # Real echo can be negative dBZ (observed down to ~-18); include it in
        # the smoothing support and keep it out of the negative-noise clamp.
        # Sentinels (-999/-99) arrive here already masked to NaN upstream.
        support_min_value=-35.0,
        clamp_negative=False,
    ),
    ("hrrr", "radar_ptype"): GridDisplayPrepConfig(
        id="hrrr_radar_ptype_display_v3",
        upscale_factor=1,
        categorical_nearest=True,
        render_categorical_nearest=False,
    ),
    ("hrrr", "radar_ptype_rain"): GridDisplayPrepConfig(
        id="hrrr_radar_ptype_component_display_v1",
        upscale_factor=3,
        preserve_zero_support=True,
        support_min_value=10.0,
        support_coverage_threshold=0.15,
    ),
    ("hrrr", "radar_ptype_snow"): GridDisplayPrepConfig(
        id="hrrr_radar_ptype_component_display_v1",
        upscale_factor=3,
        preserve_zero_support=True,
        support_min_value=10.0,
        support_coverage_threshold=0.15,
    ),
    ("hrrr", "radar_ptype_sleet"): GridDisplayPrepConfig(
        id="hrrr_radar_ptype_component_display_v1",
        upscale_factor=3,
        preserve_zero_support=True,
        support_min_value=10.0,
        support_coverage_threshold=0.15,
    ),
    ("hrrr", "radar_ptype_frzr"): GridDisplayPrepConfig(
        id="hrrr_radar_ptype_component_display_v1",
        upscale_factor=3,
        preserve_zero_support=True,
        support_min_value=10.0,
        support_coverage_threshold=0.15,
    ),
    ("nam", "radar_ptype"): GridDisplayPrepConfig(
        id="nam_radar_ptype_display_v3",
        upscale_factor=1,
        categorical_nearest=True,
        render_categorical_nearest=False,
    ),
    ("nam", "radar_ptype_rain"): GridDisplayPrepConfig(
        id="nam_radar_ptype_component_display_v1",
        upscale_factor=3,
        preserve_zero_support=True,
        support_min_value=10.0,
        support_coverage_threshold=0.15,
    ),
    ("nam", "radar_ptype_snow"): GridDisplayPrepConfig(
        id="nam_radar_ptype_component_display_v1",
        upscale_factor=3,
        preserve_zero_support=True,
        support_min_value=10.0,
        support_coverage_threshold=0.15,
    ),
    ("nam", "radar_ptype_sleet"): GridDisplayPrepConfig(
        id="nam_radar_ptype_component_display_v1",
        upscale_factor=3,
        preserve_zero_support=True,
        support_min_value=10.0,
        support_coverage_threshold=0.15,
    ),
    ("nam", "radar_ptype_frzr"): GridDisplayPrepConfig(
        id="nam_radar_ptype_component_display_v1",
        upscale_factor=3,
        preserve_zero_support=True,
        support_min_value=10.0,
        support_coverage_threshold=0.15,
    ),
    ("gfs", "ptype_intensity"): GridDisplayPrepConfig(
        id="gfs_ptype_intensity_display_v1",
        upscale_factor=3,
        categorical_nearest=True,
    ),
    ("gfs", "ptype_intensity_rain"): GridDisplayPrepConfig(
        id="gfs_ptype_intensity_component_display_v1",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
        support_min_value=0.01,
        support_coverage_threshold=0.25,
    ),
    ("gfs", "ptype_intensity_snow"): GridDisplayPrepConfig(
        id="gfs_ptype_intensity_component_display_v1",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
        support_min_value=0.01,
        support_coverage_threshold=0.1,
    ),
    ("gfs", "ptype_intensity_ice"): GridDisplayPrepConfig(
        id="gfs_ptype_intensity_component_display_v1",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
        support_min_value=0.01,
        support_coverage_threshold=0.1,
    ),
    ("ecmwf", "ptype_intensity"): GridDisplayPrepConfig(
        id="ecmwf_ptype_intensity_display_v2",
        upscale_factor=1,
        categorical_nearest=True,
    ),
    ("ecmwf", "ptype_intensity_rain"): GridDisplayPrepConfig(
        id="ecmwf_ptype_intensity_component_display_v2",
        upscale_factor=1,
        smooth_sigma=None,
        preserve_zero_support=True,
        support_min_value=0.01,
        support_coverage_threshold=0.25,
    ),
    ("ecmwf", "ptype_intensity_snow"): GridDisplayPrepConfig(
        id="ecmwf_ptype_intensity_component_display_v2",
        upscale_factor=1,
        smooth_sigma=None,
        preserve_zero_support=True,
        support_min_value=0.01,
        support_coverage_threshold=0.1,
    ),
    ("ecmwf", "ptype_intensity_ice"): GridDisplayPrepConfig(
        id="ecmwf_ptype_intensity_component_display_v2",
        upscale_factor=1,
        smooth_sigma=None,
        preserve_zero_support=True,
        support_min_value=0.01,
        support_coverage_threshold=0.1,
    ),
}


def grid_display_prep_config(model: str, var: str) -> GridDisplayPrepConfig | None:
    return _GRID_DISPLAY_PREP_BY_MODEL_VAR.get((str(model).strip().lower(), str(var).strip().lower()))


def sampling_tolerance_group(config: GridDisplayPrepConfig | None) -> int:
    """Tolerance group for COG-vs-binary sampling comparisons (migration plan
    Section 3 Layer 2 / Phase G), derived from the display-prep config rather
    than per-model variable lists:

      Group 1 — no display prep (or no upscale, non-categorical): the COG and
                binary describe the same pixel grid; agreement within scale/2.
      Group 2 — continuous upscale (``upscale_factor > 1``): the binary is a
                finer grid; bounded numeric tolerance.
      Group 3 — categorical upscale (``categorical_nearest`` with upscale):
                integer-category comparison, boundary divergence tolerated.
      Group 4 — categorical without upscale (``categorical_nearest`` at
                ``upscale_factor == 1``): same resolution on both sides, so
                strict integer-category equality with zero tolerance.
    """
    if config is None:
        return 1
    upscale_factor = max(1, int(config.upscale_factor or 1))
    categorical = bool(config.categorical_nearest)
    if categorical and upscale_factor > 1:
        return 3
    if categorical:
        return 4
    if upscale_factor > 1:
        return 2
    return 1


def _masked_gaussian(data: np.ndarray, mask: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0.0:
        return data.astype(np.float32, copy=False)

    masked = np.asarray(mask, dtype=bool)
    if not np.any(masked):
        return np.zeros_like(data, dtype=np.float32)

    data_filled = np.where(masked, data, 0.0).astype(np.float32, copy=False)
    weight = np.where(masked, 1.0, 0.0).astype(np.float32, copy=False)
    num = gaussian_filter(data_filled, sigma=sigma, mode="nearest", truncate=3.0)
    den = gaussian_filter(weight, sigma=sigma, mode="nearest", truncate=3.0)

    out = np.zeros_like(data_filled, dtype=np.float32)
    np.divide(num, den, out=out, where=den > 1e-6)
    out[~masked] = 0.0
    return out


def prepare_grid_display_values(
    *,
    model: str,
    var: str,
    values: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any] | None]:
    model_norm = str(model).strip().lower()
    var_norm = str(var).strip().lower()
    values_f32 = np.asarray(values, dtype=np.float32)
    if model_norm == "goes-east" and var_norm in {"ir13", "wv9", "wv8"}:
        return values_f32 - np.float32(273.15), {"id": f"goes_{var_norm}_display_celsius_v1", "unit_conversion": "K_to_C"}

    config = grid_display_prep_config(model_norm, var_norm)
    if config is None:
        return values_f32, None

    prepared = values_f32
    finite_mask = np.isfinite(prepared)
    support_min_value = config.support_min_value
    if support_min_value is None:
        support_mask = finite_mask & (prepared > 0.0)
    else:
        support_mask = finite_mask & (prepared >= float(support_min_value))

    factor = max(1, int(config.upscale_factor))
    if factor > 1:
        value_order = 0 if config.categorical_nearest else 1
        prepared = zoom(
            np.where(finite_mask, prepared, 0.0).astype(np.float32, copy=False),
            zoom=(factor, factor),
            order=value_order,
            mode="nearest",
            prefilter=False,
        ).astype(np.float32, copy=False)
        finite_mask = zoom(
            finite_mask.astype(np.float32, copy=False),
            zoom=(factor, factor),
            order=0,
            mode="nearest",
            prefilter=False,
        ) > 0.5
        support_coverage = zoom(
            support_mask.astype(np.float32, copy=False),
            zoom=(factor, factor),
            order=1,
            mode="nearest",
            prefilter=False,
        ).astype(np.float32, copy=False)
        positive_support = support_coverage >= float(config.support_coverage_threshold)
    else:
        positive_support = support_mask

    prepared = np.where(finite_mask, prepared, np.nan).astype(np.float32, copy=False)

    sigma = float(config.smooth_sigma or 0.0)
    if sigma > 0.0:
        prepared = _masked_gaussian(prepared, positive_support, sigma)

    if config.preserve_zero_support:
        prepared = np.where(positive_support, prepared, 0.0).astype(np.float32, copy=False)

    prepared[~finite_mask] = np.nan
    if config.clamp_negative:
        prepared[np.isfinite(prepared) & (prepared < 0.0)] = 0.0

    prep_meta = {
        "id": config.id,
        "upscale_factor": factor,
        "smooth_sigma": sigma,
    }
    if config.preserve_zero_support:
        prep_meta["preserve_zero_support"] = True
    if support_min_value is not None:
        prep_meta["support_min_value"] = float(support_min_value)
    if factor > 1 and config.preserve_zero_support:
        prep_meta["support_coverage_threshold"] = float(config.support_coverage_threshold)
    render_categorical_nearest = (
        config.categorical_nearest
        if config.render_categorical_nearest is None
        else config.render_categorical_nearest
    )
    if render_categorical_nearest:
        prep_meta["categorical_nearest"] = True
    return prepared, prep_meta
