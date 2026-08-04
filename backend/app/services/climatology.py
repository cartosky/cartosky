from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import rasterio
from rasterio.crs import CRS

from .builder.raster_grid import (
    LEGACY_GRID_CRS,
    NATIVE_GRID_CRS,
    REGION_BBOX_3857,
    REGION_BBOX_4326,
    compute_native_transform_and_shape,
    compute_transform_and_shape,
    get_native_grid_degrees,
)

_configured_data_root: Path | None = None
DEFAULT_BASELINE_SOURCE = "era5"
_BASELINE_SOURCE_ALIASES = {
    "shared": DEFAULT_BASELINE_SOURCE,
}
_BASELINE_SOURCE_GRID_METERS: dict[str, dict[str, float]] = {
    DEFAULT_BASELINE_SOURCE: {
        "conus": 25_000.0,
        "na": 25_000.0,
    },
}
#: Native-geographic (EPSG:4326) baseline grids, in *degrees*. These are the
#: Phase 3A global ERA5 baselines: the staged ERA5 rasters already land on the
#: exact grid declared by ``GLOBAL_DOMAIN_4326_CONTRACT.md`` §1, so the build
#: warp is an identity transform. A region appearing here is EPSG:4326 and is
#: deliberately absent from ``_BASELINE_SOURCE_GRID_METERS``: the two maps are
#: disjoint, so ``get_baseline_grid_params`` (metre-only, unchanged) still
#: raises ``KeyError`` for them.
_BASELINE_SOURCE_GRID_DEGREES: dict[str, dict[str, float]] = {
    DEFAULT_BASELINE_SOURCE: {
        "global": 0.25,
    },
}

#: Hint key declaring, per *build* region, which baseline region to resolve.
#: Value grammar: ``"<build_region>=<baseline_region>[,<build>=<baseline>...]"``
#: — a flat string so ``VarSelectors.hints`` stays ``dict[str, str]``.
BASELINE_REGION_BY_BUILD_REGION_HINT = "baseline_region_by_build_region"


def configure_data_root(data_root: Path) -> None:
    global _configured_data_root
    _configured_data_root = Path(data_root).resolve()


def _resolve_data_root() -> Path:
    if _configured_data_root is not None:
        return _configured_data_root
    raw = (
        os.environ.get("CARTOSKY_DATA_ROOT")
        or os.environ.get("CARTOSKY_V3_DATA_ROOT")
        or os.environ.get("TWF_V3_DATA_ROOT")
        or "./data"
    )
    return Path(raw).resolve()


def normalize_baseline_source(baseline_source: str | None) -> str:
    normalized = str(baseline_source or DEFAULT_BASELINE_SOURCE).strip().lower()
    if not normalized:
        return DEFAULT_BASELINE_SOURCE
    return _BASELINE_SOURCE_ALIASES.get(normalized, normalized)


def get_baseline_grid_params(
    *,
    baseline_source: str,
    region: str,
) -> tuple[tuple[float, float, float, float], float]:
    source_key = normalize_baseline_source(baseline_source)
    region_key = str(region).strip().lower()

    bbox = REGION_BBOX_3857.get(region_key)
    if bbox is None:
        raise KeyError(f"Unknown climatology baseline region: {region!r}")

    source_grids = _BASELINE_SOURCE_GRID_METERS.get(source_key)
    if source_grids is None:
        raise KeyError(f"Unknown climatology baseline source: {baseline_source!r}")
    grid_m = source_grids.get(region_key)
    if grid_m is None:
        raise KeyError(
            f"No climatology baseline grid configured for source={source_key!r} region={region_key!r}"
        )
    return bbox, float(grid_m)


def get_baseline_grid_degrees(
    *,
    baseline_source: str,
    region: str,
) -> float | None:
    """Resolution in degrees when a baseline region is native-geographic.

    ``None`` means the pair is a legacy EPSG:3857 metre grid (or unknown);
    presence of a value is the only signal that a baseline follows the native
    4326 contract. Mirrors ``raster_grid.get_native_grid_degrees``.
    """
    source_key = normalize_baseline_source(baseline_source)
    region_key = str(region).strip().lower()
    source_grids = _BASELINE_SOURCE_GRID_DEGREES.get(source_key)
    if source_grids is None:
        return None
    value = source_grids.get(region_key)
    if value is None:
        return None
    return float(value)


@dataclass(frozen=True)
class BaselineGrid:
    """Full grid description of a climatology baseline region."""

    baseline_source: str
    region: str
    crs: str
    bbox: tuple[float, float, float, float]
    #: metres for EPSG:3857 regions, degrees for EPSG:4326 regions.
    resolution: float
    transform: Any
    height: int
    width: int
    grid_id: str

    @property
    def is_native_geographic(self) -> bool:
        return self.crs == NATIVE_GRID_CRS


def get_baseline_grid(
    *,
    baseline_source: str,
    region: str,
) -> BaselineGrid:
    """Resolve CRS + bbox + transform + shape for a baseline region.

    The single entry point correct for both grid families. ``KeyError`` for an
    unknown (source, region) pair, exactly like
    :func:`get_baseline_grid_params`.
    """
    source_key = normalize_baseline_source(baseline_source)
    region_key = str(region).strip().lower()

    degrees = get_baseline_grid_degrees(baseline_source=source_key, region=region_key)
    if degrees is not None:
        bbox = REGION_BBOX_4326.get(region_key)
        if bbox is None:
            raise KeyError(
                f"No EPSG:4326 bbox for native-geographic baseline region: {region!r}"
            )
        transform, height, width = compute_native_transform_and_shape(bbox, degrees)
        return BaselineGrid(
            baseline_source=source_key,
            region=region_key,
            crs=NATIVE_GRID_CRS,
            bbox=tuple(float(value) for value in bbox),  # type: ignore[arg-type]
            resolution=float(degrees),
            transform=transform,
            height=int(height),
            width=int(width),
            grid_id=f"climatology:{source_key}:{region_key}:{degrees:g}deg",
        )

    bbox_3857, grid_m = get_baseline_grid_params(
        baseline_source=source_key,
        region=region_key,
    )
    transform, height, width = compute_transform_and_shape(bbox_3857, grid_m)
    return BaselineGrid(
        baseline_source=source_key,
        region=region_key,
        crs=LEGACY_GRID_CRS,
        bbox=tuple(float(value) for value in bbox_3857),  # type: ignore[arg-type]
        resolution=float(grid_m),
        transform=transform,
        height=int(height),
        width=int(width),
        grid_id=f"climatology:{source_key}:{region_key}:{grid_m:.1f}m",
    )


def get_baseline_target_grid(
    *,
    baseline_source: str,
    region: str,
) -> dict[str, str]:
    grid = get_baseline_grid(baseline_source=baseline_source, region=region)
    return {
        "region": grid.region,
        "id": grid.grid_id,
    }


def _parse_baseline_region_overrides(raw: Any) -> dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {}
    overrides: dict[str, str] = {}
    for item in text.split(","):
        entry = item.strip()
        if not entry:
            continue
        build_region, _, baseline_region = entry.partition("=")
        build_key = build_region.strip().lower()
        baseline_key = baseline_region.strip().lower()
        if not build_key or not baseline_key:
            raise ValueError(
                f"Malformed {BASELINE_REGION_BY_BUILD_REGION_HINT} entry: {entry!r}"
            )
        overrides[build_key] = baseline_key
    return overrides


def resolve_baseline_region(
    *,
    model: str,
    build_region: str,
    hints: Mapping[str, Any],
) -> str | None:
    """Which baseline region a variable departs from for *this* build region.

    Resolution order:

    1. An explicit per-build-region declaration
       (``baseline_region_by_build_region``) always wins.
    2. Otherwise the plain ``baseline_region`` hint applies — but only to
       legacy metre build regions. A native-geographic build region (the
       global domain) with no explicit declaration resolves to ``None``:
       the NA baseline is not a valid climatology for it, and silently
       falling back to it is exactly the failure Phase 3A exists to prevent.

    ``None`` means "this variable has no baseline for this build region";
    callers degrade or skip rather than load the wrong one.

    An override is rejected outright when it crosses grid families — a
    native-geographic build region declaring a metre baseline, or the reverse.
    Nothing downstream would catch it: the derive path would load a
    well-formed baseline on the wrong grid and publish confidently wrong
    anomalies. Fail loud at resolution time instead.
    """
    build_key = str(build_region).strip().lower()
    overrides = _parse_baseline_region_overrides(
        hints.get(BASELINE_REGION_BY_BUILD_REGION_HINT)
    )
    if build_key in overrides:
        baseline_key = overrides[build_key]
        build_is_native = get_native_grid_degrees(model, build_key) is not None
        baseline_is_native = (
            get_baseline_grid_degrees(
                baseline_source=str(hints.get("baseline_source") or DEFAULT_BASELINE_SOURCE),
                region=baseline_key,
            )
            is not None
        )
        if build_is_native != baseline_is_native:
            raise ValueError(
                f"{BASELINE_REGION_BY_BUILD_REGION_HINT} maps build region "
                f"{build_key!r} ("
                f"{'native-geographic EPSG:4326' if build_is_native else 'EPSG:3857 metre'}"
                f") to baseline region {baseline_key!r} ("
                f"{'native-geographic EPSG:4326' if baseline_is_native else 'EPSG:3857 metre'}"
                f") for model {str(model).strip().lower()!r}: a baseline must "
                f"be on the same grid family as the domain it is subtracted "
                f"from."
            )
        return baseline_key

    declared = str(hints.get("baseline_region") or "").strip().lower()
    if not declared:
        return None
    if get_native_grid_degrees(model, build_key) is not None:
        return None
    return declared


def instantaneous_baseline_assets_present(
    *,
    data_root: Path | None = None,
    version: str,
    baseline_source: str,
    field: str,
    region: str,
    reference_period: str,
    valid_time,
) -> bool:
    """Whether the baseline asset backing one frame is installed on disk.

    Mirrors the lookup :func:`load_climatology_baseline` performs (exact hour,
    then the synoptic round-down bucket), minus the legacy per-model fallback.
    Used by the build pipeline to skip a frame instead of failing it when a
    baseline set has not been installed yet.
    """
    for candidate_time in (valid_time, _synoptic_bucket_valid_time(valid_time)):
        path = climatology_baseline_path(
            data_root=data_root,
            version=version,
            baseline_source=baseline_source,
            field=field,
            region=region,
            reference_period=reference_period,
            valid_time=candidate_time,
        )
        if path.is_file():
            return True
    return False


def climatology_baseline_root(
    *,
    data_root: Path | None = None,
    version: str,
    baseline_source: str,
    field: str,
    region: str,
    reference_period: str,
) -> Path:
    root = Path(data_root).resolve() if data_root is not None else _resolve_data_root()
    return (
        root
        / "climatology"
        / str(version).strip()
        / normalize_baseline_source(baseline_source)
        / "baseline"
        / str(field).strip().lower()
        / str(region).strip().lower()
        / str(reference_period).strip()
    )


def legacy_climatology_baseline_path(
    *,
    version: str,
    model_family: str,
    field: str,
    valid_time,
) -> Path:
    doy = int(valid_time.timetuple().tm_yday)
    hour = int(valid_time.hour)
    return (
        _resolve_data_root()
        / "climatology"
        / str(version).strip()
        / str(model_family).strip().lower()
        / "baseline"
        / str(field).strip().lower()
        / f"doy_{doy:03d}_h{hour:02d}.tif"
    )


def climatology_baseline_path(
    *,
    data_root: Path | None = None,
    version: str,
    baseline_source: str,
    field: str,
    region: str,
    reference_period: str,
    valid_time,
) -> Path:
    doy = int(valid_time.timetuple().tm_yday)
    hour = int(valid_time.hour)
    root = climatology_baseline_root(
        data_root=data_root,
        version=version,
        baseline_source=baseline_source,
        field=field,
        region=region,
        reference_period=reference_period,
    )
    return root / f"doy_{doy:03d}_h{hour:02d}.tif"


def climatology_accumulation_baseline_path(
    *,
    data_root: Path | None = None,
    version: str,
    baseline_source: str,
    field: str,
    region: str,
    reference_period: str,
    reference_date,
) -> Path:
    doy = int(reference_date.timetuple().tm_yday)
    root = climatology_baseline_root(
        data_root=data_root,
        version=version,
        baseline_source=baseline_source,
        field=field,
        region=region,
        reference_period=reference_period,
    )
    return root / f"doy_{doy:03d}.tif"


class AccumulationBaselineWindow(NamedTuple):
    """The window arithmetic behind one accumulation-anomaly frame.

    ``window_start_fh`` is the forecast hour the accumulation window opens at
    (``0`` for a run-anchored window), and ``reference_date`` is the single
    date whose ``doy_NNN.tif`` baseline asset that frame subtracts.
    """

    target_fh: int
    accumulation_window_hours: int
    window_start_fh: int
    reference_date: datetime


def resolve_accumulation_baseline_window(
    *,
    hints: Mapping[str, Any],
    var_key: str,
    fh: int,
    run_date: datetime,
    baseline_field: str = "",
) -> AccumulationBaselineWindow:
    """Resolve which baseline reference date one accumulation frame needs.

    Single source of truth for this arithmetic. ``derive.py`` uses it to pick
    the cumulative forecast hours and to load the baseline; the build pipeline
    uses it to pre-check that the baseline asset for the *same* reference date
    is installed before the frame is attempted. Duplicating the arithmetic
    would let the pre-check silently drift from what the derive actually
    loads, which is exactly the failure mode the pre-check exists to prevent.

    Raises ``ValueError`` when the declared window is longer than the target
    forecast hour — the same condition, and message, the derive raises on.
    """
    # NOTE: this `field` is used only by the `precip_(\d+)d` regex fallback
    # below, and that fallback is unreachable for every shipping catalog —
    # all of them set `accumulation_window_hours` explicitly. So the fact that
    # the fallback chain here differs slightly from pre-extraction HEAD (which
    # regexed the caller's already-resolved `baseline_field` and had no
    # var_key fallback) cannot change any resolved window. Documented, not
    # fixed: normalising it would be churn on a dead path.
    field = str(baseline_field or hints.get("baseline_field") or "").strip().lower()
    if not field:
        field = str(var_key).removesuffix("_anom").strip().lower()

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
        match = re.match(r"^precip_(\d+)d$", field)
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

    init_date = (
        run_date.astimezone(timezone.utc)
        if run_date.tzinfo
        else run_date.replace(tzinfo=timezone.utc)
    )
    return AccumulationBaselineWindow(
        target_fh=target_fh,
        accumulation_window_hours=accumulation_window_hours,
        window_start_fh=window_start_fh,
        reference_date=init_date + timedelta(hours=window_start_fh),
    )


def accumulation_baseline_assets_present(
    *,
    data_root: Path | None = None,
    version: str,
    baseline_source: str,
    field: str,
    region: str,
    reference_period: str,
    reference_date,
) -> bool:
    """Whether the accumulation baseline asset backing one frame is on disk.

    Accumulation baselines are day-of-year only (no hour bucket) and one frame
    subtracts exactly one of them, so this is an exact-path check against the
    same path :func:`load_accumulation_climatology_baseline` opens — not an
    approximation. The pipeline uses it to skip a frame instead of failing it
    when a baseline set has not been installed yet.
    """
    return climatology_accumulation_baseline_path(
        data_root=data_root,
        version=version,
        baseline_source=baseline_source,
        field=field,
        region=region,
        reference_period=reference_period,
        reference_date=reference_date,
    ).is_file()


def _synoptic_bucket_valid_time(valid_time):
    hour = int(valid_time.hour)
    bucket_hour = (hour // 6) * 6
    if bucket_hour == hour:
        return valid_time
    return valid_time.replace(hour=bucket_hour, minute=0, second=0, microsecond=0)


def load_climatology_baseline(
    *,
    version: str,
    baseline_source: str,
    field: str,
    valid_time,
    region: str,
    reference_period: str,
    legacy_model_family_fallback: str | None = None,
) -> tuple[np.ndarray, CRS, rasterio.transform.Affine, dict[str, Any]]:
    source_key = normalize_baseline_source(baseline_source)
    region_key = str(region).strip().lower()
    path = climatology_baseline_path(
        version=version,
        baseline_source=source_key,
        field=field,
        region=region_key,
        reference_period=reference_period,
        valid_time=valid_time,
    )
    requested_hour = int(valid_time.hour)
    resolved_valid_time = valid_time
    used_legacy_fallback = False
    if not path.is_file():
        synoptic_valid_time = _synoptic_bucket_valid_time(valid_time)
        if int(synoptic_valid_time.hour) != requested_hour:
            synoptic_path = climatology_baseline_path(
                version=version,
                baseline_source=source_key,
                field=field,
                region=region_key,
                reference_period=reference_period,
                valid_time=synoptic_valid_time,
            )
            if synoptic_path.is_file():
                path = synoptic_path
                resolved_valid_time = synoptic_valid_time
        fallback_family = str(legacy_model_family_fallback or "").strip().lower()
        if fallback_family:
            fallback_path = legacy_climatology_baseline_path(
                version=version,
                model_family=fallback_family,
                field=field,
                valid_time=resolved_valid_time,
            )
            if fallback_path.is_file():
                path = fallback_path
                used_legacy_fallback = True
        if not path.is_file():
            raise FileNotFoundError(
                f"Missing climatology baseline asset: {path}"
            )

    grid = get_baseline_grid(baseline_source=source_key, region=region_key)
    expected_crs = CRS.from_user_input(grid.crs)
    expected_transform = grid.transform
    expected_height = grid.height
    expected_width = grid.width

    with rasterio.open(path) as ds:
        data = ds.read(1).astype(np.float32, copy=False)
        crs = ds.crs
        transform = ds.transform
        width = int(ds.width)
        height = int(ds.height)

    if crs is None:
        raise ValueError(f"Climatology baseline asset missing CRS: {path}")
    if CRS.from_user_input(crs) != expected_crs:
        raise ValueError(
            f"Climatology baseline asset must use {grid.crs}: {path}"
        )
    if height != expected_height or width != expected_width:
        raise ValueError(
            "Climatology baseline asset grid shape mismatch: "
            f"expected={(expected_height, expected_width)} actual={(height, width)} path={path}"
        )
    if any(
        abs(float(actual) - float(expected)) > 1.0e-6
        for actual, expected in zip(transform[:6], expected_transform[:6])
    ):
        raise ValueError(
            "Climatology baseline asset transform mismatch: "
            f"expected={expected_transform} actual={transform} path={path}"
        )

    metadata = {
        "baseline_kind": "climatology",
        "baseline_version": str(version).strip(),
        "baseline_source": source_key,
        "baseline_field": str(field).strip().lower(),
        "baseline_region": region_key,
        "baseline_alignment": "valid_time",
        "reference_period": str(reference_period).strip(),
        "baseline_legacy_fallback": used_legacy_fallback,
        "baseline_requested_hour": requested_hour,
        "baseline_resolved_hour": int(resolved_valid_time.hour),
    }
    return data, expected_crs, transform, metadata


def load_accumulation_climatology_baseline(
    *,
    version: str,
    baseline_source: str,
    field: str,
    reference_date,
    region: str,
    reference_period: str,
) -> tuple[np.ndarray, CRS, rasterio.transform.Affine, dict[str, Any]]:
    source_key = normalize_baseline_source(baseline_source)
    region_key = str(region).strip().lower()
    path = climatology_accumulation_baseline_path(
        version=version,
        baseline_source=source_key,
        field=field,
        region=region_key,
        reference_period=reference_period,
        reference_date=reference_date,
    )
    if not path.is_file():
        raise FileNotFoundError(f"Missing accumulation climatology baseline asset: {path}")

    grid = get_baseline_grid(baseline_source=source_key, region=region_key)
    expected_crs = CRS.from_user_input(grid.crs)
    expected_transform = grid.transform
    expected_height = grid.height
    expected_width = grid.width

    with rasterio.open(path) as ds:
        data = ds.read(1).astype(np.float32, copy=False)
        crs = ds.crs
        transform = ds.transform
        width = int(ds.width)
        height = int(ds.height)

    if crs is None:
        raise ValueError(f"Accumulation climatology baseline asset missing CRS: {path}")
    if CRS.from_user_input(crs) != expected_crs:
        raise ValueError(
            f"Accumulation climatology baseline asset must use {grid.crs}: {path}"
        )
    if height != expected_height or width != expected_width:
        raise ValueError(
            "Accumulation climatology baseline asset grid shape mismatch: "
            f"expected={(expected_height, expected_width)} actual={(height, width)} path={path}"
        )
    if any(
        abs(float(actual) - float(expected)) > 1.0e-6
        for actual, expected in zip(transform[:6], expected_transform[:6])
    ):
        raise ValueError(
            "Accumulation climatology baseline asset transform mismatch: "
            f"expected={expected_transform} actual={transform} path={path}"
        )

    metadata = {
        "baseline_kind": "climatology",
        "baseline_version": str(version).strip(),
        "baseline_source": source_key,
        "baseline_field": str(field).strip().lower(),
        "baseline_region": region_key,
        "baseline_alignment": "init_date",
        "baseline_temporal_resolution": "daily_accumulation_window",
        "reference_period": str(reference_period).strip(),
        "baseline_reference_doy": int(reference_date.timetuple().tm_yday),
    }
    return data, expected_crs, transform, metadata
