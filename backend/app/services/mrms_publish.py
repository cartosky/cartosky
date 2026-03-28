from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.transform import Affine
from scipy.ndimage import gaussian_filter  # type: ignore[import-untyped]

from app.models.mrms import MRMS_MODEL
from app.services.builder.colorize import float_to_rgba
from app.services.builder.cog_writer import (
    compute_transform_and_shape,
    get_grid_params,
    warp_to_target_grid,
    write_rgba_cog,
    write_value_cog,
)
from app.services.builder.pipeline import build_sidecar_json
from app.services.observed_bundle_health import build_observed_bundle_health
from app.services.publish_utils import (
    DEFAULT_LOOP_WEBP_MAX_DIM,
    DEFAULT_LOOP_WEBP_QUALITY,
    DEFAULT_LOOP_WEBP_TIER1_MAX_DIM,
    DEFAULT_LOOP_WEBP_TIER1_QUALITY,
    pregenerate_loop_webp_for_run,
    promote_run,
    write_json_atomic,
    write_latest_pointer,
    write_run_manifest,
)
from app.services.run_ids import format_run_id

logger = logging.getLogger(__name__)

MRMS_MODEL_ID = "mrms"
MRMS_REGION_ID = "conus"
MRMS_VARIABLE_ID = "reflectivity"
MRMS_COLOR_MAP_ID = "mrms_reflectivity"
MRMS_DISPLAY_SMOOTHING_SIGMA = 0.45


@dataclass(frozen=True)
class MRMSBundleFrame:
    valid_time: datetime
    values: np.ndarray
    source_crs: Any | None = None
    source_transform: Affine | None = None
    quality: str = "full"
    quality_flags: list[str] = field(default_factory=list)
    source_url: str | None = None
    source_filename: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MRMSLoopPublishSettings:
    loop_cache_root: Path
    workers: int = 2
    tier0_quality: int = DEFAULT_LOOP_WEBP_QUALITY
    tier0_max_dim: int = DEFAULT_LOOP_WEBP_MAX_DIM
    tier0_fixed_w: int = 0
    tier1_quality: int = DEFAULT_LOOP_WEBP_TIER1_QUALITY
    tier1_max_dim: int = DEFAULT_LOOP_WEBP_TIER1_MAX_DIM
    tier1_fixed_w: int = 0


@dataclass(frozen=True)
class MRMSPublishResult:
    run_id: str
    published_run_dir: Path
    manifest_path: Path
    frame_count: int


@dataclass(frozen=True)
class MRMSPublishedFrame:
    valid_time: datetime
    rgba_path: Path
    value_path: Path
    sidecar: dict[str, Any]


def load_latest_published_mrms_frames(data_root: Path) -> tuple[str | None, list[MRMSPublishedFrame]]:
    latest_path = data_root / "published" / MRMS_MODEL_ID / "LATEST.json"
    if not latest_path.is_file():
        return None, []
    try:
        latest_payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, []

    run_id = str(latest_payload.get("run_id") or "").strip()
    if not run_id:
        return None, []

    manifest_path = data_root / "manifests" / MRMS_MODEL_ID / f"{run_id}.json"
    if not manifest_path.is_file():
        return run_id, []
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return run_id, []

    var_entry = manifest.get("variables", {}).get(MRMS_VARIABLE_ID)
    if not isinstance(var_entry, dict):
        return run_id, []
    manifest_frames = var_entry.get("frames")
    if not isinstance(manifest_frames, list):
        return run_id, []

    published_run_dir = data_root / "published" / MRMS_MODEL_ID / run_id / MRMS_VARIABLE_ID
    frames: list[MRMSPublishedFrame] = []
    for frame in manifest_frames:
        if not isinstance(frame, dict):
            continue
        fh = frame.get("fh")
        try:
            fh_int = int(fh)
        except (TypeError, ValueError):
            continue
        sidecar_path = published_run_dir / f"fh{fh_int:03d}.json"
        rgba_path = published_run_dir / f"fh{fh_int:03d}.rgba.cog.tif"
        value_path = published_run_dir / f"fh{fh_int:03d}.val.cog.tif"
        if not sidecar_path.is_file() or not rgba_path.is_file() or not value_path.is_file():
            continue
        try:
            sidecar = json.loads(sidecar_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        valid_time_raw = sidecar.get("valid_time") or frame.get("valid_time")
        if not isinstance(valid_time_raw, str) or not valid_time_raw.strip():
            continue
        try:
            valid_time = datetime.strptime(valid_time_raw.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        frames.append(
            MRMSPublishedFrame(
                valid_time=valid_time,
                rgba_path=rgba_path,
                value_path=value_path,
                sidecar=sidecar,
            )
        )

    frames.sort(key=lambda item: item.valid_time.astimezone(timezone.utc))
    return run_id, frames


def publish_mrms_bundle(
    *,
    data_root: Path,
    frames: list[MRMSBundleFrame],
    publish_time: datetime | None = None,
    loop_settings: MRMSLoopPublishSettings | None = None,
    frame_write_workers: int = 1,
    previous_frames: list[MRMSPublishedFrame] | None = None,
    target_frame_count: int | None = None,
    expected_frame_count: int | None = None,
) -> MRMSPublishResult:
    if not frames and not previous_frames:
        raise ValueError("MRMS bundle publish requires at least one frame")

    publish_dt = (publish_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_id = format_run_id(publish_dt, include_minutes=True)

    merged_by_valid_time: dict[datetime, MRMSPublishedFrame | MRMSBundleFrame] = {}
    for frame in sorted(previous_frames or [], key=lambda item: item.valid_time.astimezone(timezone.utc)):
        merged_by_valid_time[frame.valid_time.astimezone(timezone.utc)] = frame
    for frame in sorted(frames, key=lambda item: item.valid_time.astimezone(timezone.utc)):
        merged_by_valid_time[frame.valid_time.astimezone(timezone.utc)] = frame

    ordered_frame_inputs = [
        merged_by_valid_time[key]
        for key in sorted(merged_by_valid_time.keys())
    ]
    if target_frame_count is not None and target_frame_count > 0:
        ordered_frame_inputs = ordered_frame_inputs[-int(target_frame_count):]
    if not ordered_frame_inputs:
        raise ValueError("MRMS bundle publish resolved to an empty rolling window")

    _prepare_stage_run_dir(data_root=data_root, run_id=run_id)

    targets: list[tuple[str, int]] = []
    max_workers = max(1, int(frame_write_workers))
    fresh_jobs: list[tuple[int, MRMSBundleFrame]] = []
    for fh, frame in enumerate(ordered_frame_inputs):
        if isinstance(frame, MRMSPublishedFrame):
            reuse_mrms_frame(
                data_root=data_root,
                run_id=run_id,
                forecast_hour=fh,
                frame=frame,
            )
            targets.append((MRMS_VARIABLE_ID, fh))
        else:
            fresh_jobs.append((fh, frame))

    if max_workers <= 1 or len(fresh_jobs) <= 1:
        for fh, frame in fresh_jobs:
            write_mrms_frame(
                data_root=data_root,
                run_id=run_id,
                forecast_hour=fh,
                frame=frame,
            )
            targets.append((MRMS_VARIABLE_ID, fh))
    elif fresh_jobs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(fresh_jobs))) as pool:
            future_map = {
                pool.submit(
                    write_mrms_frame,
                    data_root=data_root,
                    run_id=run_id,
                    forecast_hour=fh,
                    frame=frame,
                ): fh
                for fh, frame in fresh_jobs
            }
            for future in concurrent.futures.as_completed(future_map):
                fh = future_map[future]
                future.result()
                targets.append((MRMS_VARIABLE_ID, fh))
    targets.sort(key=lambda item: item[1])

    ordered_valid_times = [
        item.valid_time.astimezone(timezone.utc)
        for item in ordered_frame_inputs
    ]
    manifest_target_frame_count = (
        max(1, int(expected_frame_count))
        if expected_frame_count is not None
        else len(ordered_valid_times)
    )

    promote_run(data_root=data_root, model=MRMS_MODEL_ID, run_id=run_id)
    write_run_manifest(
        data_root=data_root,
        model=MRMS_MODEL_ID,
        run_id=run_id,
        targets=targets,
        plugin=MRMS_MODEL,
        metadata=build_observed_bundle_health(
            latest_run=run_id,
            manifest={
                "last_updated": publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "variables": {
                    MRMS_VARIABLE_ID: {
                        "expected_frames": manifest_target_frame_count,
                        "available_frames": len(ordered_valid_times),
                        "frames": [
                            {
                                "fh": fh,
                                "valid_time": valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            }
                            for fh, valid_time in enumerate(ordered_valid_times)
                        ],
                    }
                },
            },
            source=MRMS_MODEL_ID,
            now_utc=publish_dt,
        ),
    )
    write_latest_pointer(data_root=data_root, model=MRMS_MODEL_ID, run_id=run_id, source="mrms_publish_v1")

    if loop_settings is not None:
        pregenerate_loop_webp_for_run(
            data_root=data_root,
            model=MRMS_MODEL_ID,
            run_id=run_id,
            loop_cache_root=loop_settings.loop_cache_root,
            workers=loop_settings.workers,
            tier0_quality=loop_settings.tier0_quality,
            tier0_max_dim=loop_settings.tier0_max_dim,
            tier0_fixed_w=loop_settings.tier0_fixed_w,
            tier1_quality=loop_settings.tier1_quality,
            tier1_max_dim=loop_settings.tier1_max_dim,
            tier1_fixed_w=loop_settings.tier1_fixed_w,
            variables=(MRMS_VARIABLE_ID,),
            forecast_hours=range(len(ordered_valid_times)),
        )

    manifest_path = data_root / "manifests" / MRMS_MODEL_ID / f"{run_id}.json"
    published_run_dir = data_root / "published" / MRMS_MODEL_ID / run_id
    return MRMSPublishResult(
        run_id=run_id,
        published_run_dir=published_run_dir,
        manifest_path=manifest_path,
        frame_count=len(ordered_valid_times),
    )


def write_mrms_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: MRMSBundleFrame,
) -> None:
    values = np.asarray(frame.values, dtype=np.float32)
    values = _warp_frame_to_target_grid(values, frame=frame)
    display_values = _display_values_for_colorize(values)

    fh_str = f"fh{int(forecast_hour):03d}"
    staging_dir = data_root / "staging" / MRMS_MODEL_ID / run_id / MRMS_VARIABLE_ID
    staging_dir.mkdir(parents=True, exist_ok=True)

    rgba_path = staging_dir / f"{fh_str}.rgba.cog.tif"
    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"

    rgba, colorize_meta = float_to_rgba(display_values, MRMS_COLOR_MAP_ID, meta_var_key=MRMS_VARIABLE_ID)
    write_rgba_cog(
        rgba,
        rgba_path,
        model=MRMS_MODEL_ID,
        region=MRMS_REGION_ID,
        kind="discrete",
        color_map_id=MRMS_COLOR_MAP_ID,
    )
    write_value_cog(
        values,
        value_path,
        model=MRMS_MODEL_ID,
        region=MRMS_REGION_ID,
    )

    run_dt = datetime.now(timezone.utc)
    sidecar = build_sidecar_json(
        model=MRMS_MODEL_ID,
        region=MRMS_REGION_ID,
        run_id=run_id,
        var_id=MRMS_VARIABLE_ID,
        fh=int(forecast_hour),
        run_date=run_dt,
        colorize_meta=colorize_meta,
        var_spec={"type": "discrete", "units": "dBZ"},
        var_spec_model=None,
        value_downsample_factor=1,
        quality=frame.quality,
        quality_flags=frame.quality_flags,
        valid_time_override=frame.valid_time.astimezone(timezone.utc),
    )
    if frame.source_url:
        sidecar["source_url"] = frame.source_url
    if frame.source_filename:
        sidecar["source_filename"] = frame.source_filename
    if frame.metadata:
        sidecar["source_metadata"] = dict(frame.metadata)
    write_json_atomic(sidecar_path, sidecar)


def reuse_mrms_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: MRMSPublishedFrame,
) -> None:
    fh_str = f"fh{int(forecast_hour):03d}"
    staging_dir = data_root / "staging" / MRMS_MODEL_ID / run_id / MRMS_VARIABLE_ID
    staging_dir.mkdir(parents=True, exist_ok=True)

    rgba_path = staging_dir / f"{fh_str}.rgba.cog.tif"
    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"

    _link_or_copy(frame.rgba_path, rgba_path)
    _link_or_copy(frame.value_path, value_path)

    sidecar = dict(frame.sidecar)
    sidecar["run"] = run_id
    sidecar["fh"] = int(forecast_hour)
    sidecar["valid_time"] = frame.valid_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json_atomic(sidecar_path, sidecar)


def _prepare_stage_run_dir(*, data_root: Path, run_id: str) -> None:
    stage_run = data_root / "staging" / MRMS_MODEL_ID / run_id
    if stage_run.exists():
        shutil.rmtree(stage_run, ignore_errors=True)
    stage_run.mkdir(parents=True, exist_ok=True)


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _expected_target_shape() -> tuple[int, int]:
    bbox, grid_m = get_grid_params(MRMS_MODEL_ID, MRMS_REGION_ID)
    _, height, width = compute_transform_and_shape(bbox, grid_m)
    return int(height), int(width)


def _warp_frame_to_target_grid(values: np.ndarray, *, frame: MRMSBundleFrame) -> np.ndarray:
    expected_height, expected_width = _expected_target_shape()
    if frame.source_crs is not None and frame.source_transform is not None:
        warped_values, _ = warp_to_target_grid(
            values,
            frame.source_crs,
            frame.source_transform,
            model=MRMS_MODEL_ID,
            region=MRMS_REGION_ID,
            resampling="bilinear",
        )
        return np.asarray(warped_values, dtype=np.float32)

    if values.shape != (expected_height, expected_width):
        raise ValueError(
            "MRMS frame shape does not match the configured CartoSky target grid: "
            f"got={values.shape} expected={(expected_height, expected_width)}"
        )
    return values


def _display_values_for_colorize(values: np.ndarray, *, sigma: float = MRMS_DISPLAY_SMOOTHING_SIGMA) -> np.ndarray:
    if sigma <= 0.0:
        return values

    finite_mask = np.isfinite(values)
    if not finite_mask.any():
        return values

    data_filled = np.where(finite_mask, values, 0.0).astype(np.float32, copy=False)
    weight = np.where(finite_mask, 1.0, 0.0).astype(np.float32, copy=False)

    num = gaussian_filter(data_filled, sigma=sigma, mode="nearest", truncate=3.0)
    den = gaussian_filter(weight, sigma=sigma, mode="nearest", truncate=3.0)

    smoothed = np.full(values.shape, np.nan, dtype=np.float32)
    np.divide(num, den, out=smoothed, where=den > 1e-6)
    smoothed[~finite_mask] = np.nan
    return smoothed
