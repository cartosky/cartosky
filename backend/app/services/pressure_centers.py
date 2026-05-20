from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from pyproj import Transformer
from rasterio.transform import xy
from scipy import ndimage


CenterType = Literal["H", "L"]


@dataclass(frozen=True)
class PressureCenterConfig:
    source: str
    units: str
    radius_km: float
    min_delta: float
    min_separation_km: float
    max_centers: int = 12
    detect_highs: bool = True
    detect_lows: bool = True
    skip_edge_centers: bool = True


def _pixel_size_km(transform: Any) -> tuple[float, float]:
    pixel_width_m = abs(float(transform.a))
    pixel_height_m = abs(float(transform.e))
    if not np.isfinite(pixel_width_m) or pixel_width_m <= 0.0:
        pixel_width_m = 1.0
    if not np.isfinite(pixel_height_m) or pixel_height_m <= 0.0:
        pixel_height_m = pixel_width_m
    return pixel_width_m / 1000.0, pixel_height_m / 1000.0


def _round_value(value: float) -> float | int:
    if not np.isfinite(value):
        return 0
    rounded = round(float(value), 1)
    if abs(rounded - round(rounded)) < 1.0e-6:
        return int(round(rounded))
    return rounded


def _candidate_rows(
    values: np.ndarray,
    *,
    center_type: CenterType,
    radius_px: int,
    min_delta: float,
) -> list[tuple[int, int, float, float]]:
    finite_mask = np.isfinite(values)
    if not np.any(finite_mask):
        return []

    max_input = np.where(finite_mask, values, -np.inf)
    min_input = np.where(finite_mask, values, np.inf)
    max_values = ndimage.maximum_filter(max_input, size=(radius_px * 2 + 1), mode="constant", cval=-np.inf)
    min_values = ndimage.minimum_filter(min_input, size=(radius_px * 2 + 1), mode="constant", cval=np.inf)

    if center_type == "H":
        strength = values - min_values
        mask = finite_mask & np.isclose(values, max_values, rtol=0.0, atol=1.0e-6) & (strength >= min_delta)
    else:
        strength = max_values - values
        mask = finite_mask & np.isclose(values, min_values, rtol=0.0, atol=1.0e-6) & (strength >= min_delta)

    rows: list[tuple[int, int, float, float]] = []
    for row, col in np.argwhere(mask):
        rows.append((int(row), int(col), float(values[row, col]), float(strength[row, col])))
    return rows


def detect_pressure_centers(
    values: np.ndarray,
    *,
    transform: Any,
    config: PressureCenterConfig,
    projection: str = "EPSG:3857",
) -> list[dict[str, Any]]:
    """Detect synoptic highs/lows from a gridded pressure or height field.

    The algorithm finds local extrema within a configurable neighborhood,
    filters weak extrema by local range, and applies distance-based non-maximum
    suppression so nearby duplicate labels collapse to the strongest center.
    """
    values_array = np.asarray(values, dtype=np.float32)
    if values_array.ndim != 2 or values_array.size == 0:
        return []

    height, width = values_array.shape
    pixel_width_km, pixel_height_km = _pixel_size_km(transform)
    mean_pixel_km = max(0.001, (pixel_width_km + pixel_height_km) / 2.0)
    radius_px = max(1, int(round(float(config.radius_km) / mean_pixel_km)))
    min_separation_px = max(1.0, float(config.min_separation_km) / mean_pixel_km)
    edge_margin_px = radius_px if config.skip_edge_centers else 0

    candidates: list[tuple[CenterType, int, int, float, float]] = []
    if config.detect_highs:
        candidates.extend(("H", row, col, value, strength) for row, col, value, strength in _candidate_rows(
            values_array,
            center_type="H",
            radius_px=radius_px,
            min_delta=float(config.min_delta),
        ))
    if config.detect_lows:
        candidates.extend(("L", row, col, value, strength) for row, col, value, strength in _candidate_rows(
            values_array,
            center_type="L",
            radius_px=radius_px,
            min_delta=float(config.min_delta),
        ))

    if not candidates:
        return []

    candidates.sort(key=lambda item: item[4], reverse=True)
    accepted: list[tuple[CenterType, int, int, float, float]] = []
    for candidate in candidates:
        center_type, row, col, _value, _strength = candidate
        if edge_margin_px > 0 and (
            row < edge_margin_px
            or col < edge_margin_px
            or row >= height - edge_margin_px
            or col >= width - edge_margin_px
        ):
            continue
        too_close = False
        for accepted_type, accepted_row, accepted_col, _accepted_value, _accepted_strength in accepted:
            if accepted_type != center_type:
                continue
            distance_px = float(np.hypot((row - accepted_row), (col - accepted_col)))
            if distance_px < min_separation_px:
                too_close = True
                break
        if too_close:
            continue
        accepted.append(candidate)
        if len(accepted) >= int(config.max_centers):
            break

    if not accepted:
        return []

    transformer = Transformer.from_crs(projection, "EPSG:4326", always_xy=True)
    centers: list[dict[str, Any]] = []
    for center_type, row, col, value, strength in accepted:
        projected_x, projected_y = xy(transform, row, col, offset="center")
        lon, lat = transformer.transform(float(projected_x), float(projected_y))
        if not all(np.isfinite(item) for item in (lon, lat)):
            continue
        centers.append(
            {
                "type": center_type,
                "lat": round(float(lat), 4),
                "lon": round(float(lon), 4),
                "value": _round_value(value),
                "units": config.units,
                "source": config.source,
                "prominence": _round_value(strength),
            }
        )

    centers.sort(key=lambda item: (str(item["type"]), -float(item.get("prominence") or 0.0)))
    return centers