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


_GRID_DISPLAY_PREP_BY_MODEL_VAR: dict[tuple[str, str], GridDisplayPrepConfig] = {
    ("gfs", "precip_total"): GridDisplayPrepConfig(
        id="gfs_precip_total_display_v1",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
    ),
    ("gfs", "snowfall_total"): GridDisplayPrepConfig(
        id="gfs_snowfall_total_display_v1",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
    ),
    ("nbm", "precip_total"): GridDisplayPrepConfig(
        id="nbm_precip_total_display_v1",
        upscale_factor=3,
        smooth_sigma=None,
        preserve_zero_support=True,
    ),
}


def grid_display_prep_config(model: str, var: str) -> GridDisplayPrepConfig | None:
    return _GRID_DISPLAY_PREP_BY_MODEL_VAR.get((str(model).strip().lower(), str(var).strip().lower()))


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
    config = grid_display_prep_config(model, var)
    values_f32 = np.asarray(values, dtype=np.float32)
    if config is None:
        return values_f32, None

    prepared = values_f32
    finite_mask = np.isfinite(prepared)
    positive_mask = finite_mask & (prepared > 0.0)

    factor = max(1, int(config.upscale_factor))
    if factor > 1:
        prepared = zoom(
            np.where(finite_mask, prepared, 0.0).astype(np.float32, copy=False),
            zoom=(factor, factor),
            order=1,
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
        positive_support = zoom(
            positive_mask.astype(np.float32, copy=False),
            zoom=(factor, factor),
            order=1,
            mode="nearest",
            prefilter=False,
        ) > 1e-3
    else:
        positive_support = positive_mask

    prepared = np.where(finite_mask, prepared, np.nan).astype(np.float32, copy=False)

    sigma = float(config.smooth_sigma or 0.0)
    if sigma > 0.0:
        prepared = _masked_gaussian(prepared, positive_support, sigma)

    if config.preserve_zero_support:
        prepared = np.where(positive_support, prepared, 0.0).astype(np.float32, copy=False)

    prepared[~finite_mask] = np.nan
    prepared[np.isfinite(prepared) & (prepared < 0.0)] = 0.0

    prep_meta = {
        "id": config.id,
        "upscale_factor": factor,
        "smooth_sigma": sigma,
    }
    return prepared, prep_meta
