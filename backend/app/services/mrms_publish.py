from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import Affine
from scipy.ndimage import gaussian_filter  # type: ignore[import-untyped]

from ..config import binary_sampling_models, grid_build_enabled
from ..models.mrms import MRMS_MODEL
from .builder.colorize import colorize_metadata
from .builder.cog_writer import (
    compute_transform_and_shape,
    get_grid_params,
    warp_to_target_grid,
    write_value_cog,
)
from .builder.pipeline import build_sidecar_json, check_pre_encode_value_sanity
from .colormaps import MRMS_RADAR_PTYPE_BREAKS, MRMS_RADAR_PTYPE_ORDER, get_color_map_spec
from .observed_bundle_health import build_observed_bundle_health
from .process_memory import current_rss_bytes, peak_rss_bytes
from .publish_utils import (
    promote_run,
    write_json_atomic,
    write_latest_pointer,
    write_run_manifest,
)
from .grid import (
    build_grid_manifests_for_run_root,
    grid_dir_for_run_root,
    resolved_grid_dir_for_run_root,
    write_grid_frames_for_run_root,
)
from .run_ids import format_run_id

logger = logging.getLogger(__name__)

MRMS_MODEL_ID = "mrms"
MRMS_REGION_ID = "conus"
MRMS_VARIABLE_ID = "reflectivity"
MRMS_COLOR_MAP_ID = "mrms_reflectivity"
MRMS_DISPLAY_SMOOTHING_SIGMA = 0.45

MRMS_RADAR_PTYPE_VARIABLE_ID = "mrms_radar_ptype"
MRMS_RADAR_PTYPE_COLOR_MAP_ID = "mrms_radar_ptype"
MRMS_RECENT_PRECIP_VARIABLE_IDS = (
    "mrms_recent_precip_6h",
    "mrms_recent_precip_24h",
    "mrms_recent_precip_72h",
)
MRMS_RECENT_PRECIP_COLOR_MAP_IDS: dict[str, str] = {
    "mrms_recent_precip_6h": "mrms_recent_precip_6h",
    "mrms_recent_precip_24h": "mrms_recent_precip_24h",
    "mrms_recent_precip_72h": "mrms_recent_precip_72h",
}
MRMS_RUNTIME_ARTIFACTS_PENDING_KEY = "runtime_artifacts_pending"


def _pre_encode_gate_allows(
    values: np.ndarray,
    *,
    var_id: str,
    color_map_id: str,
    forecast_hour: int,
    binary_only: bool,
) -> bool:
    """Dual-mode pre-encode gate (COG->binary sampling migration), shared by
    all four MRMS fresh-write sites. The check itself runs unconditionally on
    every fresh frame write; ``binary_only`` decides only what a failure
    means. Enforced (model allowlisted): failure or a gate error rejects the
    frame before ANY artifact is written, matching pipeline.py's binary_only
    branch. Shadow (default): log-only, frame governed by the COG path."""
    try:
        gate_ok = check_pre_encode_value_sanity(
            values,
            get_color_map_spec(color_map_id),
            var_spec_model=MRMS_MODEL.get_var(var_id),
            var_capability=MRMS_MODEL.get_var_capability(var_id),
            label=f"{MRMS_MODEL_ID}/{var_id}/fh{int(forecast_hour):03d}",
        )
    except Exception:
        if binary_only:
            logger.exception(
                "Pre-encode sanity gate errored — rejecting frame "
                "model=%s var=%s fh%03d — frame not published",
                MRMS_MODEL_ID,
                var_id,
                int(forecast_hour),
            )
            return False
        logger.exception(
            "Phase C shadow gate errored: pre-encode value sanity "
            "model=%s var=%s fh%03d; frame remains governed by existing COG gates",
            MRMS_MODEL_ID,
            var_id,
            int(forecast_hour),
        )
        return True
    if not gate_ok:
        if binary_only:
            logger.error(
                "Pre-encode sanity gate rejected frame model=%s var=%s "
                "fh%03d — frame not published",
                MRMS_MODEL_ID,
                var_id,
                int(forecast_hour),
            )
            return False
        logger.warning(
            "Phase C shadow gate failed: pre-encode value sanity "
            "model=%s var=%s fh%03d; frame remains governed by existing COG gates",
            MRMS_MODEL_ID,
            var_id,
            int(forecast_hour),
        )
    return True


def _bytes_to_mib(num_bytes: int) -> float:
    return float(num_bytes) / (1024.0 * 1024.0)


def _array_mib(value: Any) -> float:
    if isinstance(value, np.ndarray):
        return _bytes_to_mib(int(value.nbytes))
    return 0.0


def _log_mrms_publish_memory(stage: str, **details: Any) -> None:
    detail_tokens = " ".join(
        f"{key}={value}"
        for key, value in sorted(details.items())
    )
    suffix = f" {detail_tokens}" if detail_tokens else ""
    logger.info(
        "MRMS memory checkpoint stage=%s current_rss_mib=%.1f peak_rss_mib=%.1f%s",
        stage,
        _bytes_to_mib(current_rss_bytes()),
        _bytes_to_mib(peak_rss_bytes()),
        suffix,
    )

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

MRMS_PTYPE_CATEGORY_INDEX: dict[str, int] = {
    code: idx for idx, code in enumerate(MRMS_RADAR_PTYPE_ORDER)
}


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
class MRMSSupplementalFrame:
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
    supplemental_variable_frames: dict[str, list[MRMSSupplementalFrame]] | None = None,
    supplemental_expected_frame_counts: dict[str, int] | None = None,
    build_grid_artifacts: bool = True,
) -> MRMSPublishResult:
    if not frames and not previous_frames:
        raise ValueError("MRMS bundle publish requires at least one frame")

    publish_dt = (publish_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_id = format_run_id(publish_dt, include_minutes=True)
    started_at = time.monotonic()
    logger.info(
        "MRMS publish phase=start run=%s frames=%d previous_frames=%d supplemental_vars=%d workers=%d",
        run_id,
        len(frames),
        len(previous_frames or []),
        len(supplemental_variable_frames or {}),
        int(frame_write_workers),
    )

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

    build_primary_grid_artifacts = bool(grid_build_enabled())
    build_supplemental_grid_artifacts = bool(build_grid_artifacts and grid_build_enabled())

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
                build_grid_artifacts=build_primary_grid_artifacts,
            )
            targets.append((MRMS_VARIABLE_ID, fh))
            if reused_ptype:
                ptype_targets.append((MRMS_RADAR_PTYPE_VARIABLE_ID, fh))
        else:
            fresh_jobs.append((fh, frame))

    def _write_fresh_frame(fh: int, frame: MRMSBundleFrame) -> tuple[int, bool, bool]:
        """Write reflectivity frame, and radar_ptype frame if precip_flag available.

        Returns (fh, wrote_reflectivity, has_ptype). A frame the enforced
        pre-encode gate rejected reports wrote_reflectivity=False and drops
        out of the bundle; a rejected ptype composite degrades the frame to
        reflectivity-only, matching the existing ptype-failure path.
        """
        if not write_mrms_frame(
            data_root=data_root,
            run_id=run_id,
            forecast_hour=fh,
            frame=frame,
            build_grid_artifacts=build_primary_grid_artifacts,
        ):
            return fh, False, False
        has_ptype = frame.precip_flag_values is not None
        if has_ptype:
            try:
                has_ptype = write_mrms_radar_ptype_frame(
                    data_root=data_root,
                    run_id=run_id,
                    forecast_hour=fh,
                    frame=frame,
                    build_grid_artifacts=build_primary_grid_artifacts,
                )
            except Exception:
                logger.warning(
                    "MRMS radar_ptype frame write failed fh=%d; reflectivity-only",
                    fh,
                    exc_info=True,
                )
                has_ptype = False
        return fh, True, has_ptype

    if max_workers <= 1 or len(fresh_jobs) <= 1:
        for fh, frame in fresh_jobs:
            fh_result, wrote_refl, has_ptype = _write_fresh_frame(fh, frame)
            if not wrote_refl:
                continue
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
                fh_result, wrote_refl, has_ptype = future.result()
                if not wrote_refl:
                    continue
                targets.append((MRMS_VARIABLE_ID, fh_result))
                if has_ptype:
                    ptype_targets.append((MRMS_RADAR_PTYPE_VARIABLE_ID, fh_result))
    targets.sort(key=lambda item: item[1])
    ptype_targets.sort(key=lambda item: item[1])
    if not targets:
        raise ValueError("MRMS bundle publish requires at least one frame")
    supplemental_targets: dict[str, list[tuple[str, int]]] = {}
    for var_id, supplemental_frames in sorted((supplemental_variable_frames or {}).items()):
        if var_id not in MRMS_RECENT_PRECIP_COLOR_MAP_IDS:
            raise ValueError(f"Unsupported MRMS supplemental variable: {var_id}")
        ordered_supplemental_frames = sorted(
            supplemental_frames,
            key=lambda item: item.valid_time.astimezone(timezone.utc),
        )
        for fh, supplemental_frame in enumerate(ordered_supplemental_frames):
            if not write_mrms_supplemental_frame(
                data_root=data_root,
                run_id=run_id,
                var_id=var_id,
                forecast_hour=fh,
                frame=supplemental_frame,
                build_grid_artifacts=build_supplemental_grid_artifacts,
            ):
                continue
            supplemental_targets.setdefault(var_id, []).append((var_id, fh))
    all_targets = targets + ptype_targets + [
        target
        for var_targets in supplemental_targets.values()
        for target in var_targets
    ]

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
    runtime_artifacts_pending = False

    if build_primary_grid_artifacts:
        grid_variables = [MRMS_VARIABLE_ID]
        if ptype_targets:
            grid_variables.append(MRMS_RADAR_PTYPE_VARIABLE_ID)
        if build_supplemental_grid_artifacts:
            grid_variables.extend(var_id for var_id, var_targets in supplemental_targets.items() if var_targets)
        try:
            manifest_ok = build_grid_manifests_for_run_root(
                run_root=data_root / "staging" / MRMS_MODEL_ID / run_id,
                model=MRMS_MODEL_ID,
                run=run_id,
                variables=tuple(dict.fromkeys(grid_variables)),
            )
            logger.info("MRMS grid manifest build: run=%s manifests=%d", run_id, manifest_ok)
        except Exception:
            logger.exception("MRMS grid manifest build failed: run=%s", run_id)

    promote_run(data_root=data_root, model=MRMS_MODEL_ID, run_id=run_id)

    reflectivity_fhs = sorted(fh for _, fh in targets)
    manifest_variables: dict[str, Any] = {
        MRMS_VARIABLE_ID: {
            "expected_frames": manifest_target_frame_count,
            "available_frames": len(reflectivity_fhs),
            "frames": [
                {
                    "fh": fh,
                    "valid_time": ordered_source_valid_times[fh].strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                for fh in reflectivity_fhs
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
    for var_id, var_targets in supplemental_targets.items():
        ordered_frames = sorted(
            supplemental_variable_frames.get(var_id, []),
            key=lambda item: item.valid_time.astimezone(timezone.utc),
        ) if supplemental_variable_frames else []
        expected_frames_for_var = (
            max(0, int((supplemental_expected_frame_counts or {}).get(var_id, len(ordered_frames))))
        )
        manifest_variables[var_id] = {
            "expected_frames": expected_frames_for_var,
            "available_frames": len(var_targets),
            "frames": [
                {
                    "fh": fh,
                    "valid_time": (
                        (ordered_frames[fh].source_valid_time or ordered_frames[fh].valid_time)
                        .astimezone(timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ")
                    ),
                }
                for _, fh in var_targets
                if fh < len(ordered_frames)
            ],
        }

    write_run_manifest(
        data_root=data_root,
        model=MRMS_MODEL_ID,
        run_id=run_id,
        targets=all_targets,
        plugin=MRMS_MODEL,
        metadata=_mrms_manifest_metadata(
            run_id=run_id,
            manifest_variables=manifest_variables,
            manifest_last_updated=publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            now_utc=publish_dt,
            runtime_artifacts_pending=runtime_artifacts_pending,
        ),
    )
    write_latest_pointer(data_root=data_root, model=MRMS_MODEL_ID, run_id=run_id, source="mrms_publish_v1")

    manifest_path = data_root / "manifests" / MRMS_MODEL_ID / f"{run_id}.json"
    published_run_dir = data_root / "published" / MRMS_MODEL_ID / run_id
    logger.info(
        "MRMS publish phase=complete run=%s elapsed=%.1fs frame_count=%d",
        run_id,
        time.monotonic() - started_at,
        len(reflectivity_fhs),
    )
    return MRMSPublishResult(
        run_id=run_id,
        published_run_dir=published_run_dir,
        manifest_path=manifest_path,
        frame_count=len(reflectivity_fhs),
    )


def write_mrms_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: MRMSBundleFrame,
    build_grid_artifacts: bool = True,
) -> bool:
    """Write one reflectivity frame's artifacts. Returns False when the
    enforced pre-encode gate rejected the frame (nothing written), mirroring
    how build_frame signals a failed frame via status rather than raising."""
    phase_started_at = time.monotonic()
    logger.info(
        "MRMS publish phase=start run=%s var=%s fh=%03d",
        run_id,
        MRMS_VARIABLE_ID,
        int(forecast_hour),
    )

    values = np.asarray(frame.values, dtype=np.float32)
    logger.info(
        "MRMS publish phase=frame_prepare run=%s var=%s fh=%03d elapsed=%.1fs",
        run_id,
        MRMS_VARIABLE_ID,
        int(forecast_hour),
        time.monotonic() - phase_started_at,
    )

    values = _warp_frame_to_target_grid(values, frame=frame)
    _log_mrms_publish_memory(
        "after_warp",
        run_id=run_id,
        var=MRMS_VARIABLE_ID,
        fh=f"{int(forecast_hour):03d}",
        source_values_mib=f"{_array_mib(frame.values):.1f}",
        warped_values_mib=f"{_array_mib(values):.1f}",
    )
    logger.info(
        "MRMS publish phase=reproject run=%s var=%s fh=%03d elapsed=%.1fs",
        run_id,
        MRMS_VARIABLE_ID,
        int(forecast_hour),
        time.monotonic() - phase_started_at,
    )

    # Gate the warped array — the one both the COG and grid writes receive —
    # not the colorize-only smoothed display copy computed below.
    binary_only = MRMS_MODEL_ID in binary_sampling_models()
    if not _pre_encode_gate_allows(
        values,
        var_id=MRMS_VARIABLE_ID,
        color_map_id=MRMS_COLOR_MAP_ID,
        forecast_hour=forecast_hour,
        binary_only=binary_only,
    ):
        return False

    display_values = _display_values_for_colorize(values)

    fh_str = f"fh{int(forecast_hour):03d}"
    staging_dir = data_root / "staging" / MRMS_MODEL_ID / run_id / MRMS_VARIABLE_ID
    staging_dir.mkdir(parents=True, exist_ok=True)

    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"

    colorize_meta = colorize_metadata(display_values, MRMS_COLOR_MAP_ID, meta_var_key=MRMS_VARIABLE_ID)
    _log_mrms_publish_memory(
        "after_colorization",
        run_id=run_id,
        var=MRMS_VARIABLE_ID,
        fh=f"{int(forecast_hour):03d}",
        values_mib=f"{_array_mib(values):.1f}",
        display_values_mib=f"{_array_mib(display_values):.1f}",
        metadata_only="true",
        rgba_mib="0.0",
    )
    if binary_only:
        # Value COG retired for binary-sampling models: the grid binary
        # (written below) serves rendering and sampling, and the enforced
        # gate above already applied the value-quality gate.
        logger.info(
            "Value COG write skipped (model=%s is binary-only)",
            MRMS_MODEL_ID,
        )
    else:
        write_value_cog(
            values,
            value_path,
            model=MRMS_MODEL_ID,
            region=MRMS_REGION_ID,
        )
        logger.info(
            "MRMS publish phase=cog_write run=%s var=%s fh=%03d elapsed=%.1fs",
            run_id,
            MRMS_VARIABLE_ID,
            int(forecast_hour),
            time.monotonic() - phase_started_at,
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
    if build_grid_artifacts and grid_build_enabled():
        write_grid_frames_for_run_root(
            run_root=data_root / "staging" / MRMS_MODEL_ID / run_id,
            model=MRMS_MODEL_ID,
            var=MRMS_VARIABLE_ID,
            fh=int(forecast_hour),
            values=values,
            transform=_target_grid_transform(),
        )
        logger.info(
            "MRMS publish phase=grid_build run=%s var=%s fh=%03d elapsed=%.1fs",
            run_id,
            MRMS_VARIABLE_ID,
            int(forecast_hour),
            time.monotonic() - phase_started_at,
        )

    logger.info(
        "MRMS publish phase=complete run=%s var=%s fh=%03d elapsed=%.1fs",
        run_id,
        MRMS_VARIABLE_ID,
        int(forecast_hour),
        time.monotonic() - phase_started_at,
    )
    return True


def reuse_mrms_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: MRMSPublishedFrame,
    build_grid_artifacts: bool = True,
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
    if build_grid_artifacts and grid_build_enabled():
        if not _reuse_mrms_grid_artifacts(
            data_root=data_root,
            run_id=run_id,
            var=MRMS_VARIABLE_ID,
            forecast_hour=int(forecast_hour),
            source_value_path=frame.value_path,
        ):
            with rasterio.open(value_path) as ds:
                write_grid_frames_for_run_root(
                    run_root=data_root / "staging" / MRMS_MODEL_ID / run_id,
                    model=MRMS_MODEL_ID,
                    var=MRMS_VARIABLE_ID,
                    fh=int(forecast_hour),
                    values=ds.read(1).astype(np.float32, copy=False),
                    transform=ds.transform,
                    projection=ds.crs.to_string() if ds.crs is not None else "EPSG:3857",
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
            if build_grid_artifacts and grid_build_enabled():
                if not _reuse_mrms_grid_artifacts(
                    data_root=data_root,
                    run_id=run_id,
                    var=MRMS_RADAR_PTYPE_VARIABLE_ID,
                    forecast_hour=int(forecast_hour),
                    source_value_path=frame.ptype_value_path,
                ):
                    with rasterio.open(ptype_value_path) as ds:
                        write_grid_frames_for_run_root(
                            run_root=data_root / "staging" / MRMS_MODEL_ID / run_id,
                            model=MRMS_MODEL_ID,
                            var=MRMS_RADAR_PTYPE_VARIABLE_ID,
                            fh=int(forecast_hour),
                            values=ds.read(1).astype(np.float32, copy=False),
                            transform=ds.transform,
                            projection=ds.crs.to_string() if ds.crs is not None else "EPSG:3857",
                        )
            has_ptype = True
        except Exception:
            logger.warning(
                "MRMS radar_ptype reuse failed fh=%d; reflectivity-only reuse",
                forecast_hour,
                exc_info=True,
            )
    return has_ptype


def _reuse_mrms_grid_artifacts(
    *,
    data_root: Path,
    run_id: str,
    var: str,
    forecast_hour: int,
    source_value_path: Path,
) -> bool:
    source_fh = _forecast_hour_from_artifact_name(source_value_path)
    if source_fh is None:
        return False

    source_run_root = source_value_path.parent.parent
    source_grid_dir = resolved_grid_dir_for_run_root(source_run_root, var)
    if not source_grid_dir.is_dir():
        return False

    target_run_root = data_root / "staging" / MRMS_MODEL_ID / run_id
    target_grid_dir = grid_dir_for_run_root(target_run_root, var)
    target_grid_dir.mkdir(parents=True, exist_ok=True)

    source_token = f"fh{source_fh:03d}"
    target_token = f"fh{int(forecast_hour):03d}"
    source_bins = sorted(source_grid_dir.glob(f"{source_token}.l*.u*.bin"))
    source_meta_paths = sorted(source_grid_dir.glob(f"{source_token}.l*.meta.json"))
    if not source_bins or not source_meta_paths:
        return False

    retargeted_meta: list[tuple[Path, dict[str, Any]]] = []
    for source_meta_path in source_meta_paths:
        target_meta_path = target_grid_dir / source_meta_path.name.replace(source_token, target_token, 1)
        try:
            meta = json.loads(source_meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        meta["fh"] = int(forecast_hour)
        filename = str(meta.get("file") or "").strip()
        if filename:
            meta["file"] = filename.replace(source_token, target_token, 1)
        retargeted_meta.append((target_meta_path, meta))

    for source_bin in source_bins:
        target_bin = target_grid_dir / source_bin.name.replace(source_token, target_token, 1)
        _link_or_copy(source_bin, target_bin)
        for suffix in (".gz", ".br"):
            source_sidecar = source_bin.with_name(f"{source_bin.name}{suffix}")
            if source_sidecar.is_file():
                _link_or_copy(source_sidecar, target_bin.with_name(f"{target_bin.name}{suffix}"))

    for target_meta_path, meta in retargeted_meta:
        write_json_atomic(target_meta_path, meta)

    return True


def _forecast_hour_from_artifact_name(path: Path) -> int | None:
    token = Path(path).name.split(".", 1)[0]
    if not token.startswith("fh"):
        return None
    try:
        return int(token.removeprefix("fh"))
    except ValueError:
        return None


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


def _warp_frame_to_target_grid(
    values: np.ndarray,
    *,
    frame: MRMSBundleFrame,
    resampling: str = "bilinear",
) -> np.ndarray:
    expected_height, expected_width = _expected_target_shape()
    if frame.source_crs is not None and frame.source_transform is not None:
        # src_nodata=NaN keeps masked sentinels (and any other nodata) out of
        # the resampling kernel instead of blending them into real values.
        warped_values, _ = warp_to_target_grid(
            values,
            frame.source_crs,
            frame.source_transform,
            model=MRMS_MODEL_ID,
            region=MRMS_REGION_ID,
            resampling=resampling,
            src_nodata=float("nan"),
            working_dtype=np.float32,
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
    (matching MRMS_RADAR_PTYPE_BREAKS offsets) and invalid pixels are NaN.
    This mirrors the forecast ``_derive_radar_ptype_combo`` output format.
    """
    refl = np.asarray(reflectivity, dtype=np.float32)
    flags = np.asarray(precip_flag, dtype=np.float32)

    if refl.shape != flags.shape:
        raise ValueError(
            f"Reflectivity and PrecipFlag shape mismatch: "
            f"refl={refl.shape} flags={flags.shape}"
        )

    # Map finite integer flag codes to compact category indices. Using
    # integers avoids expensive large unicode arrays and suppresses NaN cast
    # warnings from upstream missing-data cells.
    rounded_flags = np.zeros(flags.shape, dtype=np.float32)
    finite_flag_mask = np.isfinite(flags)
    np.rint(flags, out=rounded_flags, where=finite_flag_mask)
    flag_int = np.zeros(flags.shape, dtype=np.int16)
    flag_int[finite_flag_mask] = rounded_flags[finite_flag_mask].astype(np.int16, copy=False)

    category_idx = np.full(refl.shape, -1, dtype=np.int8)
    for flag_code, ptype_name in MRMS_PRECIP_FLAG_TO_PTYPE.items():
        if ptype_name is None:
            continue
        category_idx[flag_int == flag_code] = MRMS_PTYPE_CATEGORY_INDEX[ptype_name]

    # Normalise reflectivity to [0, 1] for binning (same as forecast)
    refl_safe = np.where(np.isfinite(refl), np.maximum(refl, 0.0), np.nan)
    normalized = np.clip(refl_safe / 70.0, 0.0, 1.0)
    visible_refl_mask = np.isfinite(refl_safe) & (refl_safe >= min_visible_dbz)

    indexed = np.full(refl.shape, np.nan, dtype=np.float32)
    for idx, code in enumerate(MRMS_RADAR_PTYPE_ORDER):
        breaks = MRMS_RADAR_PTYPE_BREAKS[code]
        offset = int(breaks["offset"])
        count = int(breaks["count"])
        local_bin = np.zeros(refl.shape, dtype=np.int32)
        if visible_refl_mask.any():
            scaled_visible = np.clip(
                np.rint(normalized[visible_refl_mask] * (count - 1)),
                0,
                count - 1,
            )
            local_bin[visible_refl_mask] = scaled_visible.astype(np.int32, copy=False)
        selector = (
            (category_idx == idx)
            & visible_refl_mask
        )
        indexed[selector] = (offset + local_bin[selector]).astype(np.float32)

    return indexed


def write_mrms_radar_ptype_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: MRMSBundleFrame,
    build_grid_artifacts: bool = True,
) -> bool:
    """Write an mrms_radar_ptype frame by compositing reflectivity + PrecipFlag.

    Returns False when the enforced pre-encode gate rejected the frame
    (nothing written) — the caller degrades to a reflectivity-only frame."""
    if frame.precip_flag_values is None:
        raise ValueError("Cannot write mrms_radar_ptype frame without precip_flag_values")

    phase_started_at = time.monotonic()
    logger.info(
        "MRMS publish phase=start run=%s var=%s fh=%03d",
        run_id,
        MRMS_RADAR_PTYPE_VARIABLE_ID,
        int(forecast_hour),
    )

    reflectivity = np.asarray(frame.values, dtype=np.float32)
    logger.info(
        "MRMS publish phase=frame_prepare run=%s var=%s fh=%03d elapsed=%.1fs",
        run_id,
        MRMS_RADAR_PTYPE_VARIABLE_ID,
        int(forecast_hour),
        time.monotonic() - phase_started_at,
    )

    reflectivity = _warp_frame_to_target_grid(reflectivity, frame=frame)

    precip_flag = np.asarray(frame.precip_flag_values, dtype=np.float32)
    # PrecipFlag shares the same native grid as reflectivity, but it is a
    # categorical flag field: bilinear blends at category boundaries round
    # into wrong (or unmapped) flag codes, so it must warp nearest-neighbor.
    pf_frame = MRMSBundleFrame(
        valid_time=frame.valid_time,
        values=precip_flag,
        source_crs=frame.source_crs,
        source_transform=frame.source_transform,
    )
    precip_flag = _warp_frame_to_target_grid(precip_flag, frame=pf_frame, resampling="nearest")
    _log_mrms_publish_memory(
        "after_warp",
        run_id=run_id,
        var=MRMS_RADAR_PTYPE_VARIABLE_ID,
        fh=f"{int(forecast_hour):03d}",
        source_reflectivity_mib=f"{_array_mib(frame.values):.1f}",
        source_precip_flag_mib=f"{_array_mib(frame.precip_flag_values):.1f}",
        warped_reflectivity_mib=f"{_array_mib(reflectivity):.1f}",
        warped_precip_flag_mib=f"{_array_mib(precip_flag):.1f}",
    )
    logger.info(
        "MRMS publish phase=reproject run=%s var=%s fh=%03d elapsed=%.1fs",
        run_id,
        MRMS_RADAR_PTYPE_VARIABLE_ID,
        int(forecast_hour),
        time.monotonic() - phase_started_at,
    )

    indexed = compose_mrms_radar_ptype(reflectivity, precip_flag)
    _log_mrms_publish_memory(
        "after_ptype_compose",
        run_id=run_id,
        var=MRMS_RADAR_PTYPE_VARIABLE_ID,
        fh=f"{int(forecast_hour):03d}",
        indexed_mib=f"{_array_mib(indexed):.1f}",
        reflectivity_mib=f"{_array_mib(reflectivity):.1f}",
        precip_flag_mib=f"{_array_mib(precip_flag):.1f}",
    )

    # Gate the composited indexed array with its own indexed spec (the real
    # "mrms_radar_ptype" colormap carries ptype_breaks, so the categorical
    # branch of the gate applies) — never reflectivity's continuous spec.
    binary_only = MRMS_MODEL_ID in binary_sampling_models()
    if not _pre_encode_gate_allows(
        indexed,
        var_id=MRMS_RADAR_PTYPE_VARIABLE_ID,
        color_map_id=MRMS_RADAR_PTYPE_COLOR_MAP_ID,
        forecast_hour=forecast_hour,
        binary_only=binary_only,
    ):
        return False

    fh_str = f"fh{int(forecast_hour):03d}"
    staging_dir = data_root / "staging" / MRMS_MODEL_ID / run_id / MRMS_RADAR_PTYPE_VARIABLE_ID
    staging_dir.mkdir(parents=True, exist_ok=True)

    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"

    colorize_meta = colorize_metadata(indexed, MRMS_RADAR_PTYPE_COLOR_MAP_ID, meta_var_key=MRMS_RADAR_PTYPE_VARIABLE_ID)
    _log_mrms_publish_memory(
        "after_colorization",
        run_id=run_id,
        var=MRMS_RADAR_PTYPE_VARIABLE_ID,
        fh=f"{int(forecast_hour):03d}",
        indexed_mib=f"{_array_mib(indexed):.1f}",
        metadata_only="true",
        rgba_mib="0.0",
    )
    if binary_only:
        logger.info(
            "Value COG write skipped (model=%s is binary-only)",
            MRMS_MODEL_ID,
        )
    else:
        write_value_cog(
            indexed,
            value_path,
            model=MRMS_MODEL_ID,
            region=MRMS_REGION_ID,
        )
        logger.info(
            "MRMS publish phase=cog_write run=%s var=%s fh=%03d elapsed=%.1fs",
            run_id,
            MRMS_RADAR_PTYPE_VARIABLE_ID,
            int(forecast_hour),
            time.monotonic() - phase_started_at,
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
    if build_grid_artifacts and grid_build_enabled():
        write_grid_frames_for_run_root(
            run_root=data_root / "staging" / MRMS_MODEL_ID / run_id,
            model=MRMS_MODEL_ID,
            var=MRMS_RADAR_PTYPE_VARIABLE_ID,
            fh=int(forecast_hour),
            values=indexed,
            transform=_target_grid_transform(),
        )
        logger.info(
            "MRMS publish phase=grid_build run=%s var=%s fh=%03d elapsed=%.1fs",
            run_id,
            MRMS_RADAR_PTYPE_VARIABLE_ID,
            int(forecast_hour),
            time.monotonic() - phase_started_at,
        )

    logger.info(
        "MRMS publish phase=complete run=%s var=%s fh=%03d elapsed=%.1fs",
        run_id,
        MRMS_RADAR_PTYPE_VARIABLE_ID,
        int(forecast_hour),
        time.monotonic() - phase_started_at,
    )
    return True


def write_mrms_supplemental_frame(
    *,
    data_root: Path,
    run_id: str,
    var_id: str,
    forecast_hour: int,
    frame: MRMSSupplementalFrame,
    build_grid_artifacts: bool = True,
) -> bool:
    return _write_mrms_supplemental_frame_to_run_root(
        run_root=data_root / "staging" / MRMS_MODEL_ID / run_id,
        run_id=run_id,
        var_id=var_id,
        forecast_hour=forecast_hour,
        frame=frame,
        build_grid_artifacts=build_grid_artifacts,
    )


def _write_mrms_supplemental_frame_to_run_root(
    *,
    run_root: Path,
    run_id: str,
    var_id: str,
    forecast_hour: int,
    frame: MRMSSupplementalFrame,
    build_grid_artifacts: bool,
) -> bool:
    """Write one supplemental frame's artifacts into ``run_root`` — the shared
    body for BOTH the staging bundle path and finalize_mrms_published_run's
    deferred path (which writes directly into the published run dir), so the
    pre-encode gate below covers both. Returns False when the enforced gate
    rejected the frame (nothing written)."""
    color_map_id = MRMS_RECENT_PRECIP_COLOR_MAP_IDS.get(var_id)
    if not color_map_id:
        raise ValueError(f"Unsupported MRMS supplemental variable: {var_id}")

    phase_started_at = time.monotonic()
    logger.info(
        "MRMS publish phase=start run=%s var=%s fh=%03d",
        run_id,
        var_id,
        int(forecast_hour),
    )

    warped_values = _warp_supplemental_values(frame.values, frame=frame)
    _log_mrms_publish_memory(
        "after_warp",
        run_id=run_id,
        var=var_id,
        fh=f"{int(forecast_hour):03d}",
        source_values_mib=f"{_array_mib(frame.values):.1f}",
        warped_values_mib=f"{_array_mib(warped_values):.1f}",
    )
    logger.info(
        "MRMS publish phase=reproject run=%s var=%s fh=%03d elapsed=%.1fs",
        run_id,
        var_id,
        int(forecast_hour),
        time.monotonic() - phase_started_at,
    )

    binary_only = MRMS_MODEL_ID in binary_sampling_models()
    if not _pre_encode_gate_allows(
        warped_values,
        var_id=var_id,
        color_map_id=color_map_id,
        forecast_hour=forecast_hour,
        binary_only=binary_only,
    ):
        return False

    fh_str = f"fh{int(forecast_hour):03d}"
    staging_dir = run_root / var_id
    staging_dir.mkdir(parents=True, exist_ok=True)

    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"

    colorize_meta = colorize_metadata(warped_values, color_map_id, meta_var_key=var_id)
    _log_mrms_publish_memory(
        "after_colorization",
        run_id=run_id,
        var=var_id,
        fh=f"{int(forecast_hour):03d}",
        warped_values_mib=f"{_array_mib(warped_values):.1f}",
        metadata_only="true",
        rgba_mib="0.0",
    )
    if binary_only:
        logger.info(
            "Value COG write skipped (model=%s is binary-only)",
            MRMS_MODEL_ID,
        )
    else:
        write_value_cog(
            warped_values,
            value_path,
            model=MRMS_MODEL_ID,
            region=MRMS_REGION_ID,
        )
        logger.info(
            "MRMS publish phase=cog_write run=%s var=%s fh=%03d elapsed=%.1fs",
            run_id,
            var_id,
            int(forecast_hour),
            time.monotonic() - phase_started_at,
        )

    run_dt = datetime.now(timezone.utc)
    sidecar = build_sidecar_json(
        model=MRMS_MODEL_ID,
        region=MRMS_REGION_ID,
        run_id=run_id,
        var_id=var_id,
        fh=int(forecast_hour),
        run_date=run_dt,
        colorize_meta=colorize_meta,
        var_spec={"type": "continuous", "units": "in"},
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
    if build_grid_artifacts and grid_build_enabled():
        write_grid_frames_for_run_root(
            run_root=run_root,
            model=MRMS_MODEL_ID,
            var=var_id,
            fh=int(forecast_hour),
            values=warped_values,
            transform=_target_grid_transform(),
        )
        logger.info(
            "MRMS publish phase=grid_build run=%s var=%s fh=%03d elapsed=%.1fs",
            run_id,
            var_id,
            int(forecast_hour),
            time.monotonic() - phase_started_at,
        )

    logger.info(
        "MRMS publish phase=complete run=%s var=%s fh=%03d elapsed=%.1fs",
        run_id,
        var_id,
        int(forecast_hour),
        time.monotonic() - phase_started_at,
    )
    return True


def finalize_mrms_published_run(
    *,
    data_root: Path,
    run_id: str,
    reused_supplemental_from_run_id: str | None = None,
    reused_supplemental_manifest_entries: dict[str, dict[str, Any]] | None = None,
    supplemental_variable_frames: dict[str, list[MRMSSupplementalFrame]] | None = None,
    supplemental_expected_frame_counts: dict[str, int] | None = None,
    build_grid_artifacts: bool = True,
) -> None:
    published_run_root = data_root / "published" / MRMS_MODEL_ID / run_id
    if not published_run_root.is_dir():
        raise ValueError(f"Cannot finalize missing published MRMS run: {published_run_root}")

    manifest_path = data_root / "manifests" / MRMS_MODEL_ID / f"{run_id}.json"
    if not manifest_path.is_file():
        raise ValueError(f"Cannot finalize MRMS run without manifest: {manifest_path}")

    manifest = json.loads(manifest_path.read_text())
    manifest_variables = dict(manifest.get("variables") or {})
    expected_counts = dict(supplemental_expected_frame_counts or {})
    changed_supplemental_vars: set[str] = set()

    for var_id, previous_entry in sorted((reused_supplemental_manifest_entries or {}).items()):
        if reused_supplemental_from_run_id:
            _copy_published_variable_artifacts(
                data_root=data_root,
                source_run_id=reused_supplemental_from_run_id,
                target_run_id=run_id,
                var_id=var_id,
            )
        manifest_variables[var_id] = json.loads(json.dumps(previous_entry))
        changed_supplemental_vars.add(var_id)

    for var_id, supplemental_frames in sorted((supplemental_variable_frames or {}).items()):
        ordered_frames = sorted(
            supplemental_frames,
            key=lambda item: item.valid_time.astimezone(timezone.utc),
        )
        written_items: list[tuple[int, MRMSSupplementalFrame]] = []
        for fh, supplemental_frame in enumerate(ordered_frames):
            if _write_mrms_supplemental_frame_to_run_root(
                run_root=published_run_root,
                run_id=run_id,
                var_id=var_id,
                forecast_hour=fh,
                frame=supplemental_frame,
                build_grid_artifacts=False,
            ):
                written_items.append((fh, supplemental_frame))
        manifest_variables[var_id] = _supplemental_manifest_entry(
            frame_items=written_items,
            expected_frame_count=max(0, int(expected_counts.get(var_id, len(ordered_frames)))),
        )
        changed_supplemental_vars.add(var_id)

    manifest_last_updated = manifest.get("last_updated")
    metadata_before = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    should_rewrite_manifest = manifest_variables != manifest.get("variables") or bool(metadata_before.get(MRMS_RUNTIME_ARTIFACTS_PENDING_KEY))

    if should_rewrite_manifest:
        manifest["variables"] = manifest_variables
        manifest["metadata"] = _mrms_manifest_metadata(
            run_id=run_id,
            manifest_variables=manifest_variables,
            manifest_last_updated=manifest_last_updated,
            now_utc=datetime.now(timezone.utc),
            runtime_artifacts_pending=False,
        )
        write_json_atomic(manifest_path, manifest)

    if build_grid_artifacts and grid_build_enabled():
        if changed_supplemental_vars:
            grid_variables = [
                var_id
                for var_id in MRMS_RECENT_PRECIP_VARIABLE_IDS
                if var_id in changed_supplemental_vars and (published_run_root / var_id).is_dir()
            ]
        else:
            grid_variables = [MRMS_VARIABLE_ID]
            if (published_run_root / MRMS_RADAR_PTYPE_VARIABLE_ID).is_dir():
                grid_variables.append(MRMS_RADAR_PTYPE_VARIABLE_ID)
            grid_variables.extend(
                var_id
                for var_id in MRMS_RECENT_PRECIP_VARIABLE_IDS
                if (published_run_root / var_id).is_dir()
            )
        _build_published_run_grid_artifacts(
            data_root=data_root,
            run_id=run_id,
            variables=tuple(dict.fromkeys(grid_variables)),
        )
def _supplemental_manifest_entry(
    *,
    frame_items: list[tuple[int, MRMSSupplementalFrame]],
    expected_frame_count: int,
) -> dict[str, Any]:
    return {
        "expected_frames": max(0, int(expected_frame_count)),
        "available_frames": len(frame_items),
        "frames": [
            {
                "fh": fh,
                "valid_time": (
                    (frame.source_valid_time or frame.valid_time)
                    .astimezone(timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ")
                ),
            }
            for fh, frame in frame_items
        ],
    }


def _mrms_manifest_metadata(
    *,
    run_id: str,
    manifest_variables: dict[str, Any],
    manifest_last_updated: str | None,
    now_utc: datetime,
    runtime_artifacts_pending: bool,
) -> dict[str, Any]:
    last_updated = str(manifest_last_updated or now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")).strip()
    metadata = build_observed_bundle_health(
        latest_run=run_id,
        manifest={
            "last_updated": last_updated,
            "variables": manifest_variables,
        },
        source=MRMS_MODEL_ID,
        now_utc=now_utc,
    )
    if runtime_artifacts_pending:
        metadata[MRMS_RUNTIME_ARTIFACTS_PENDING_KEY] = True
    return metadata


def _copy_published_variable_artifacts(
    *,
    data_root: Path,
    source_run_id: str,
    target_run_id: str,
    var_id: str,
) -> None:
    source_dir = data_root / "published" / MRMS_MODEL_ID / source_run_id / var_id
    if not source_dir.is_dir():
        raise ValueError(f"Cannot reuse missing MRMS supplemental directory: {source_dir}")

    target_dir = data_root / "published" / MRMS_MODEL_ID / target_run_id / var_id
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    for source_path in sorted(source_dir.glob("fh*")):
        if source_path.is_dir() or source_path.name.startswith("grid"):
            continue
        _link_or_copy(source_path, target_dir / source_path.name)


def _build_published_run_grid_artifacts(
    *,
    data_root: Path,
    run_id: str,
    variables: tuple[str, ...],
) -> None:
    run_root = data_root / "published" / MRMS_MODEL_ID / run_id
    built_variables: list[str] = []

    for var_id in variables:
        var_dir = run_root / var_id
        if not var_dir.is_dir():
            continue

        grid_dir = var_dir / "grid"
        if grid_dir.exists():
            shutil.rmtree(grid_dir, ignore_errors=True)

        wrote_any = False
        for sidecar_path in sorted(var_dir.glob("fh*.json")):
            fh = _forecast_hour_from_artifact_name(sidecar_path)
            if fh is None:
                continue
            value_path = var_dir / f"fh{fh:03d}.val.cog.tif"
            if not value_path.is_file():
                continue
            with rasterio.open(value_path) as ds:
                write_grid_frames_for_run_root(
                    run_root=run_root,
                    model=MRMS_MODEL_ID,
                    var=var_id,
                    fh=fh,
                    values=ds.read(1).astype(np.float32, copy=False),
                    transform=ds.transform,
                    projection=ds.crs.to_string() if ds.crs is not None else "EPSG:3857",
                )
            wrote_any = True

        if wrote_any:
            built_variables.append(var_id)

    if built_variables:
        build_grid_manifests_for_run_root(
            run_root=run_root,
            model=MRMS_MODEL_ID,
            run=run_id,
            variables=tuple(dict.fromkeys(built_variables)),
        )


def _warp_supplemental_values(values: np.ndarray, *, frame: MRMSSupplementalFrame) -> np.ndarray:
    temp_frame = MRMSBundleFrame(
        valid_time=frame.valid_time,
        values=np.asarray(values, dtype=np.float32),
        source_valid_time=frame.source_valid_time,
        source_crs=frame.source_crs,
        source_transform=frame.source_transform,
        quality=frame.quality,
        quality_flags=list(frame.quality_flags),
        source_url=frame.source_url,
        source_filename=frame.source_filename,
        metadata=dict(frame.metadata),
    )
    return _warp_frame_to_target_grid(np.asarray(values, dtype=np.float32), frame=temp_frame)
