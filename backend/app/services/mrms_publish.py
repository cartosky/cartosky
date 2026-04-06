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

from app.config import grid_build_enabled
from app.models.mrms import MRMS_MODEL
from app.services.builder.colorize import float_to_rgba
from app.services.builder.cog_writer import (
    compute_transform_and_shape,
    get_grid_params,
    warp_to_target_grid,
    write_value_cog,
)
from app.services.builder.pipeline import build_sidecar_json
from app.services.colormaps import RADAR_PTYPE_BREAKS, RADAR_PTYPE_ORDER
from app.services.observed_bundle_health import build_observed_bundle_health
from app.services.publish_utils import (
    promote_run,
    write_json_atomic,
    write_latest_pointer,
    write_run_manifest,
)
from app.services.grid import (
    build_grid_manifests_for_run_root,
    write_grid_frames_for_run_root,
    write_grid_frame_from_value_cog_for_run_root,
)
from app.services.run_ids import format_run_id

logger = logging.getLogger(__name__)

MRMS_MODEL_ID = "mrms"
MRMS_REGION_ID = "conus"
MRMS_VARIABLE_ID = "reflectivity"
MRMS_COLOR_MAP_ID = "mrms_reflectivity"
MRMS_DISPLAY_SMOOTHING_SIGMA = 0.45

MRMS_RADAR_PTYPE_VARIABLE_ID = "mrms_radar_ptype"
MRMS_RADAR_PTYPE_COLOR_MAP_ID = "mrms_radar_ptype"

# ---------------------------------------------------------------------------
# PrecipFlag → ptype mapping
# ---------------------------------------------------------------------------
MRMS_PRECIP_FLAG_TO_PTYPE: dict[int, str | None] = {
    -3: None,      # no coverage
     0: None,      # no precipitation
     1: "rain",    # warm stratiform rain
     3: "snow",    # snow
     6: "rain",    # convective rain
     7: "rain",    # rain mixed with hail
    10: "rain",    # cold stratiform rain
    91: "rain",    # tropical/stratiform rain mix
    96: "rain",    # tropical/convective rain mix
}
# Any unknown flag values → None (transparent)


@dataclass(frozen=True)
class MRMSBundleFrame:
    valid_time: datetime
    values: np.ndarray
    source_valid_time: datetime | None = None
    source_crs: Any | None = None
    source_transform: Affine | None = None
    quality: str = "full"
    quality_flags: list[str] = field(default_factory=list)
    source_url: str | None = None
    source_filename: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    precip_flag_values: np.ndarray | None = None


@dataclass(frozen=True)
class MRMSPublishResult:
    run_id: str
    published_run_dir: Path
    manifest_path: Path
    frame_count: int


@dataclass(frozen=True)
class MRMSPublishedFrame:
    valid_time: datetime
    source_valid_time: datetime | None
    value_path: Path
    sidecar: dict[str, Any]
    ptype_value_path: Path | None = None
    ptype_sidecar: dict[str, Any] | None = None


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

    # Build a set of fh values that have published mrms_radar_ptype frames
    ptype_fh_set: set[int] = set()
    ptype_var_entry = manifest.get("variables", {}).get(MRMS_RADAR_PTYPE_VARIABLE_ID)
    if isinstance(ptype_var_entry, dict):
        ptype_manifest_frames = ptype_var_entry.get("frames")
        if isinstance(ptype_manifest_frames, list):
            for pf in ptype_manifest_frames:
                if isinstance(pf, dict):
                    try:
                        ptype_fh_set.add(int(pf["fh"]))
                    except (KeyError, TypeError, ValueError):
                        pass

    published_run_dir = data_root / "published" / MRMS_MODEL_ID / run_id
    refl_dir = published_run_dir / MRMS_VARIABLE_ID
    ptype_dir = published_run_dir / MRMS_RADAR_PTYPE_VARIABLE_ID
    frames: list[MRMSPublishedFrame] = []
    for frame in manifest_frames:
        if not isinstance(frame, dict):
            continue
        fh = frame.get("fh")
        try:
            fh_int = int(fh)
        except (TypeError, ValueError):
            continue
        sidecar_path = refl_dir / f"fh{fh_int:03d}.json"
        value_path = refl_dir / f"fh{fh_int:03d}.val.cog.tif"
        if not sidecar_path.is_file() or not value_path.is_file():
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

        # Check for paired mrms_radar_ptype artifacts
        ptype_value_path: Path | None = None
        ptype_sidecar_data: dict[str, Any] | None = None
        if fh_int in ptype_fh_set:
            ptype_val = ptype_dir / f"fh{fh_int:03d}.val.cog.tif"
            ptype_sc = ptype_dir / f"fh{fh_int:03d}.json"
            if ptype_val.is_file() and ptype_sc.is_file():
                try:
                    ptype_sidecar_data = json.loads(ptype_sc.read_text())
                    ptype_value_path = ptype_val
                except (OSError, json.JSONDecodeError):
                    pass

        frames.append(
            MRMSPublishedFrame(
                valid_time=valid_time,
                source_valid_time=_source_valid_time_from_sidecar(sidecar, fallback=valid_time),
                value_path=value_path,
                sidecar=sidecar,
                ptype_value_path=ptype_value_path,
                ptype_sidecar=ptype_sidecar_data,
            )
        )

    frames.sort(key=lambda item: item.valid_time.astimezone(timezone.utc))
    return run_id, frames


def publish_mrms_bundle(
    *,
    data_root: Path,
    frames: list[MRMSBundleFrame],
    publish_time: datetime | None = None,
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
    ptype_targets: list[tuple[str, int]] = []
    max_workers = max(1, int(frame_write_workers))
    fresh_jobs: list[tuple[int, MRMSBundleFrame]] = []
    for fh, frame in enumerate(ordered_frame_inputs):
        if isinstance(frame, MRMSPublishedFrame):
            reused_ptype = reuse_mrms_frame(
                data_root=data_root,
                run_id=run_id,
                forecast_hour=fh,
                frame=frame,
            )
            targets.append((MRMS_VARIABLE_ID, fh))
            if reused_ptype:
                ptype_targets.append((MRMS_RADAR_PTYPE_VARIABLE_ID, fh))
        else:
            fresh_jobs.append((fh, frame))

    def _write_fresh_frame(fh: int, frame: MRMSBundleFrame) -> tuple[int, bool]:
        """Write reflectivity frame, and radar_ptype frame if precip_flag available.

        Returns (fh, has_ptype).
        """
        write_mrms_frame(
            data_root=data_root,
            run_id=run_id,
            forecast_hour=fh,
            frame=frame,
        )
        has_ptype = frame.precip_flag_values is not None
        if has_ptype:
            try:
                write_mrms_radar_ptype_frame(
                    data_root=data_root,
                    run_id=run_id,
                    forecast_hour=fh,
                    frame=frame,
                )
            except Exception:
                logger.warning(
                    "MRMS radar_ptype frame write failed fh=%d; reflectivity-only",
                    fh,
                    exc_info=True,
                )
                has_ptype = False
        return fh, has_ptype

    if max_workers <= 1 or len(fresh_jobs) <= 1:
        for fh, frame in fresh_jobs:
            fh_result, has_ptype = _write_fresh_frame(fh, frame)
            targets.append((MRMS_VARIABLE_ID, fh_result))
            if has_ptype:
                ptype_targets.append((MRMS_RADAR_PTYPE_VARIABLE_ID, fh_result))
    elif fresh_jobs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(fresh_jobs))) as pool:
            future_map = {
                pool.submit(_write_fresh_frame, fh, frame): fh
                for fh, frame in fresh_jobs
            }
            for future in concurrent.futures.as_completed(future_map):
                fh = future_map[future]
                fh_result, has_ptype = future.result()
                targets.append((MRMS_VARIABLE_ID, fh_result))
                if has_ptype:
                    ptype_targets.append((MRMS_RADAR_PTYPE_VARIABLE_ID, fh_result))
    targets.sort(key=lambda item: item[1])
    ptype_targets.sort(key=lambda item: item[1])
    all_targets = targets + ptype_targets

    ordered_valid_times = [
        item.valid_time.astimezone(timezone.utc)
        for item in ordered_frame_inputs
    ]
    ordered_source_valid_times = [
        (item.source_valid_time or item.valid_time).astimezone(timezone.utc)
        for item in ordered_frame_inputs
    ]
    manifest_target_frame_count = (
        max(1, int(expected_frame_count))
        if expected_frame_count is not None
        else len(ordered_valid_times)
    )

    if grid_build_enabled():
        grid_variables = [MRMS_VARIABLE_ID]
        if ptype_targets:
            grid_variables.append(MRMS_RADAR_PTYPE_VARIABLE_ID)
        try:
            manifest_ok = build_grid_manifests_for_run_root(
                run_root=data_root / "staging" / MRMS_MODEL_ID / run_id,
                model=MRMS_MODEL_ID,
                run=run_id,
                variables=tuple(grid_variables),
            )
            logger.info("MRMS grid manifest build: run=%s manifests=%d", run_id, manifest_ok)
        except Exception:
            logger.exception("MRMS grid manifest build failed: run=%s", run_id)

    promote_run(data_root=data_root, model=MRMS_MODEL_ID, run_id=run_id)

    manifest_variables: dict[str, Any] = {
        MRMS_VARIABLE_ID: {
            "expected_frames": manifest_target_frame_count,
            "available_frames": len(ordered_valid_times),
            "frames": [
                {
                    "fh": fh,
                    "valid_time": source_valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                for fh, source_valid_time in enumerate(ordered_source_valid_times)
            ],
        },
    }
    if ptype_targets:
        ptype_fhs = sorted(fh for _, fh in ptype_targets)
        manifest_variables[MRMS_RADAR_PTYPE_VARIABLE_ID] = {
            "expected_frames": manifest_target_frame_count,
            "available_frames": len(ptype_fhs),
            "frames": [
                {
                    "fh": fh,
                    "valid_time": ordered_source_valid_times[fh].strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                for fh in ptype_fhs
                if fh < len(ordered_source_valid_times)
            ],
        }

    write_run_manifest(
        data_root=data_root,
        model=MRMS_MODEL_ID,
        run_id=run_id,
        targets=all_targets,
        plugin=MRMS_MODEL,
        metadata=build_observed_bundle_health(
            latest_run=run_id,
            manifest={
                "last_updated": publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "variables": manifest_variables,
            },
            source=MRMS_MODEL_ID,
            now_utc=publish_dt,
        ),
    )
    write_latest_pointer(data_root=data_root, model=MRMS_MODEL_ID, run_id=run_id, source="mrms_publish_v1")

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

    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"

    _, colorize_meta = float_to_rgba(display_values, MRMS_COLOR_MAP_ID, meta_var_key=MRMS_VARIABLE_ID)
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
    source_metadata = dict(frame.metadata) if frame.metadata else {}
    if frame.source_valid_time is not None:
        source_metadata["actual_valid_time"] = frame.source_valid_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if source_metadata:
        sidecar["source_metadata"] = source_metadata
    write_json_atomic(sidecar_path, sidecar)
    if grid_build_enabled():
        write_grid_frames_for_run_root(
            run_root=data_root / "staging" / MRMS_MODEL_ID / run_id,
            model=MRMS_MODEL_ID,
            var=MRMS_VARIABLE_ID,
            fh=int(forecast_hour),
            values=values,
            transform=_target_grid_transform(),
        )


def reuse_mrms_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: MRMSPublishedFrame,
) -> bool:
    """Reuse a previously published reflectivity frame (and its ptype frame if available).

    Returns True if a paired mrms_radar_ptype frame was also reused.
    """
    fh_str = f"fh{int(forecast_hour):03d}"
    staging_dir = data_root / "staging" / MRMS_MODEL_ID / run_id / MRMS_VARIABLE_ID
    staging_dir.mkdir(parents=True, exist_ok=True)

    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"

    _link_or_copy(frame.value_path, value_path)

    sidecar = dict(frame.sidecar)
    sidecar["run"] = run_id
    sidecar["fh"] = int(forecast_hour)
    sidecar["valid_time"] = frame.valid_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if frame.source_valid_time is not None:
        source_metadata = dict(sidecar.get("source_metadata") or {})
        source_metadata["actual_valid_time"] = frame.source_valid_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sidecar["source_metadata"] = source_metadata
    write_json_atomic(sidecar_path, sidecar)
    if grid_build_enabled():
        write_grid_frame_from_value_cog_for_run_root(
            run_root=data_root / "staging" / MRMS_MODEL_ID / run_id,
            model=MRMS_MODEL_ID,
            var=MRMS_VARIABLE_ID,
            fh=int(forecast_hour),
            value_cog_path=value_path,
        )

    # Reuse paired mrms_radar_ptype artifacts if they exist
    has_ptype = False
    if frame.ptype_value_path is not None and frame.ptype_sidecar is not None:
        try:
            ptype_staging_dir = data_root / "staging" / MRMS_MODEL_ID / run_id / MRMS_RADAR_PTYPE_VARIABLE_ID
            ptype_staging_dir.mkdir(parents=True, exist_ok=True)

            ptype_value_path = ptype_staging_dir / f"{fh_str}.val.cog.tif"
            ptype_sidecar_path = ptype_staging_dir / f"{fh_str}.json"

            _link_or_copy(frame.ptype_value_path, ptype_value_path)

            ptype_sc = dict(frame.ptype_sidecar)
            ptype_sc["run"] = run_id
            ptype_sc["fh"] = int(forecast_hour)
            ptype_sc["valid_time"] = frame.valid_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if frame.source_valid_time is not None:
                ptype_source_metadata = dict(ptype_sc.get("source_metadata") or {})
                ptype_source_metadata["actual_valid_time"] = frame.source_valid_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                ptype_sc["source_metadata"] = ptype_source_metadata
            write_json_atomic(ptype_sidecar_path, ptype_sc)
            if grid_build_enabled():
                write_grid_frame_from_value_cog_for_run_root(
                    run_root=data_root / "staging" / MRMS_MODEL_ID / run_id,
                    model=MRMS_MODEL_ID,
                    var=MRMS_RADAR_PTYPE_VARIABLE_ID,
                    fh=int(forecast_hour),
                    value_cog_path=ptype_value_path,
                )
            has_ptype = True
        except Exception:
            logger.warning(
                "MRMS radar_ptype reuse failed fh=%d; reflectivity-only reuse",
                forecast_hour,
                exc_info=True,
            )
    return has_ptype


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


def _source_valid_time_from_sidecar(sidecar: dict[str, Any], *, fallback: datetime) -> datetime:
    source_meta = sidecar.get("source_metadata")
    if isinstance(source_meta, dict):
        raw = source_meta.get("actual_valid_time")
        if isinstance(raw, str) and raw.strip():
            try:
                return datetime.strptime(raw.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return fallback.astimezone(timezone.utc)


def _expected_target_shape() -> tuple[int, int]:
    bbox, grid_m = get_grid_params(MRMS_MODEL_ID, MRMS_REGION_ID)
    _, height, width = compute_transform_and_shape(bbox, grid_m)
    return int(height), int(width)


def _target_grid_transform() -> Affine:
    bbox, grid_m = get_grid_params(MRMS_MODEL_ID, MRMS_REGION_ID)
    transform, _, _ = compute_transform_and_shape(bbox, grid_m)
    return transform


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


# ---------------------------------------------------------------------------
# MRMS radar_ptype composition (reflectivity + PrecipFlag → indexed palette)
# ---------------------------------------------------------------------------

def compose_mrms_radar_ptype(
    reflectivity: np.ndarray,
    precip_flag: np.ndarray,
    *,
    min_visible_dbz: float = 10.0,
) -> np.ndarray:
    """Compose reflectivity + PrecipFlag into an indexed flat palette array.

    Returns a float32 array where valid pixels contain palette index values
    (matching RADAR_PTYPE_BREAKS offsets) and invalid pixels are NaN.
    This mirrors the forecast ``_derive_radar_ptype_combo`` output format.
    """
    refl = np.asarray(reflectivity, dtype=np.float32)
    flags = np.asarray(precip_flag, dtype=np.float32)

    if refl.shape != flags.shape:
        raise ValueError(
            f"Reflectivity and PrecipFlag shape mismatch: "
            f"refl={refl.shape} flags={flags.shape}"
        )

    # Map integer flag codes to ptype strings (vectorised)
    ptype = np.empty(refl.shape, dtype="U5")  # max len "sleet"
    ptype[:] = ""
    flag_int = np.rint(flags).astype(np.int32)
    for flag_code, ptype_name in MRMS_PRECIP_FLAG_TO_PTYPE.items():
        if ptype_name is not None:
            ptype[flag_int == flag_code] = ptype_name

    # Normalise reflectivity to [0, 1] for binning (same as forecast)
    refl_safe = np.where(np.isfinite(refl), np.maximum(refl, 0.0), np.nan)
    normalized = np.clip(refl_safe / 70.0, 0.0, 1.0)

    indexed = np.full(refl.shape, np.nan, dtype=np.float32)
    for code in RADAR_PTYPE_ORDER:
        breaks = RADAR_PTYPE_BREAKS[code]
        offset = int(breaks["offset"])
        count = int(breaks["count"])
        local_bin = np.clip(
            np.rint(normalized * (count - 1)), 0, count - 1,
        ).astype(np.int32)
        selector = (
            (ptype == code)
            & np.isfinite(refl_safe)
            & (refl_safe >= min_visible_dbz)
        )
        indexed[selector] = (offset + local_bin[selector]).astype(np.float32)

    return indexed


def write_mrms_radar_ptype_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: MRMSBundleFrame,
) -> None:
    """Write an mrms_radar_ptype frame by compositing reflectivity + PrecipFlag."""
    if frame.precip_flag_values is None:
        raise ValueError("Cannot write mrms_radar_ptype frame without precip_flag_values")

    reflectivity = np.asarray(frame.values, dtype=np.float32)
    reflectivity = _warp_frame_to_target_grid(reflectivity, frame=frame)

    precip_flag = np.asarray(frame.precip_flag_values, dtype=np.float32)
    # PrecipFlag shares the same native grid as reflectivity, so warp the same way
    pf_frame = MRMSBundleFrame(
        valid_time=frame.valid_time,
        values=precip_flag,
        source_crs=frame.source_crs,
        source_transform=frame.source_transform,
    )
    precip_flag = _warp_frame_to_target_grid(precip_flag, frame=pf_frame)

    indexed = compose_mrms_radar_ptype(reflectivity, precip_flag)

    fh_str = f"fh{int(forecast_hour):03d}"
    staging_dir = data_root / "staging" / MRMS_MODEL_ID / run_id / MRMS_RADAR_PTYPE_VARIABLE_ID
    staging_dir.mkdir(parents=True, exist_ok=True)

    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"

    _, colorize_meta = float_to_rgba(indexed, MRMS_RADAR_PTYPE_COLOR_MAP_ID, meta_var_key=MRMS_RADAR_PTYPE_VARIABLE_ID)
    write_value_cog(
        indexed,
        value_path,
        model=MRMS_MODEL_ID,
        region=MRMS_REGION_ID,
    )

    run_dt = datetime.now(timezone.utc)
    sidecar = build_sidecar_json(
        model=MRMS_MODEL_ID,
        region=MRMS_REGION_ID,
        run_id=run_id,
        var_id=MRMS_RADAR_PTYPE_VARIABLE_ID,
        fh=int(forecast_hour),
        run_date=run_dt,
        colorize_meta=colorize_meta,
        var_spec={"type": "indexed", "units": "index"},
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
    source_metadata = dict(frame.metadata) if frame.metadata else {}
    if frame.source_valid_time is not None:
        source_metadata["actual_valid_time"] = frame.source_valid_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if source_metadata:
        sidecar["source_metadata"] = source_metadata
    write_json_atomic(sidecar_path, sidecar)
    if grid_build_enabled():
        write_grid_frames_for_run_root(
            run_root=data_root / "staging" / MRMS_MODEL_ID / run_id,
            model=MRMS_MODEL_ID,
            var=MRMS_RADAR_PTYPE_VARIABLE_ID,
            fh=int(forecast_hour),
            values=indexed,
            transform=_target_grid_transform(),
        )
