from __future__ import annotations

import json
import logging
import shutil
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from app.config import grid_build_enabled
from app.models.goes_east import GOES_EAST_MODEL
from app.services.builder.colorize import float_to_rgba
from app.services.builder.cog_writer import write_value_cog
from app.services.builder.pipeline import build_sidecar_json
from app.services.colormaps import get_color_map_spec
from app.services.grid import (
    build_grid_manifests_for_run_root,
    grid_dir_for_run_root,
    resolved_grid_dir_for_run_root,
    write_grid_frames_for_run_root,
)
from app.services.observed_bundle_health import build_observed_bundle_health
from app.services.publish_utils import (
    promote_run,
    write_json_atomic,
    write_latest_pointer,
    write_run_manifest,
)
from app.services.run_ids import format_run_id

logger = logging.getLogger(__name__)

GOES_EAST_MODEL_ID = "goes-east"
GOES_EAST_REGION_ID = "conus"


@dataclass(frozen=True)
class GOESBandConfig:
    variable_id: str
    color_map_id: str


BAND_CONFIG_IR13 = GOESBandConfig(
    variable_id="ir13",
    color_map_id="goes_ir13_enhanced",
)
BAND_CONFIG_WV9 = GOESBandConfig(
    variable_id="wv9",
    color_map_id="goes_wv9_enhanced",
)

GOES_EAST_VARIABLE_ID = BAND_CONFIG_IR13.variable_id
GOES_EAST_COLOR_MAP_ID = BAND_CONFIG_IR13.color_map_id


@dataclass(frozen=True)
class GOESBundleFrame:
    valid_time: datetime
    slot_time: datetime
    values: np.ndarray
    transform: Any
    projection: str = "EPSG:3857"
    quality: str = "full"
    quality_flags: list[str] = field(default_factory=list)
    source_bucket: str | None = None
    source_key: str | None = None
    source_filename: str | None = None
    source_size_bytes: int | None = None
    source_last_modified: datetime | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GOESPublishedFrame:
    valid_time: datetime
    slot_time: datetime
    value_path: Path
    sidecar: dict[str, Any]


@dataclass(frozen=True)
class GOESPublishResult:
    run_id: str
    published_run_dir: Path
    manifest_path: Path
    frame_count: int


def _resolve_band_config(band_config: GOESBandConfig | None) -> GOESBandConfig:
    return band_config or BAND_CONFIG_IR13


def _preserved_manifest_variables(
    *,
    data_root: Path,
    run_id: str,
    exclude_var_id: str,
) -> dict[str, dict[str, Any]]:
    manifest_path = data_root / "manifests" / GOES_EAST_MODEL_ID / f"{run_id}.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    variables = manifest.get("variables") if isinstance(manifest, dict) else None
    if not isinstance(variables, dict):
        return {}
    return {
        str(var_id): deepcopy(entry)
        for var_id, entry in variables.items()
        if str(var_id) != exclude_var_id and isinstance(entry, dict)
    }


def _merge_preserved_manifest_variables(
    *,
    data_root: Path,
    run_id: str,
    preserved_variables: dict[str, dict[str, Any]],
) -> None:
    if not preserved_variables:
        return
    manifest_path = data_root / "manifests" / GOES_EAST_MODEL_ID / f"{run_id}.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        variables = {}
        manifest["variables"] = variables
    for var_id, entry in preserved_variables.items():
        variables.setdefault(var_id, deepcopy(entry))
    write_json_atomic(manifest_path, manifest)


def load_latest_published_goes_frames(
    data_root: Path,
    band_config: GOESBandConfig | None = None,
) -> tuple[str | None, list[GOESPublishedFrame]]:
    band_config = _resolve_band_config(band_config)
    var_id = band_config.variable_id
    latest_path = data_root / "published" / GOES_EAST_MODEL_ID / "LATEST.json"
    if not latest_path.is_file():
        return None, []
    try:
        latest_payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, []
    run_id = str(latest_payload.get("run_id") or "").strip()
    if not run_id:
        return None, []
    manifest_path = data_root / "manifests" / GOES_EAST_MODEL_ID / f"{run_id}.json"
    if not manifest_path.is_file():
        return run_id, []
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return run_id, []
    var_entry = manifest.get("variables", {}).get(var_id)
    frames_payload = var_entry.get("frames") if isinstance(var_entry, dict) else None
    if not isinstance(frames_payload, list):
        return run_id, []
    published_run_dir = data_root / "published" / GOES_EAST_MODEL_ID / run_id
    var_dir = published_run_dir / var_id
    frames: list[GOESPublishedFrame] = []
    for frame in frames_payload:
        if not isinstance(frame, dict):
            continue
        try:
            fh = int(frame["fh"])
        except (KeyError, TypeError, ValueError):
            continue
        value_path = var_dir / f"fh{fh:03d}.val.cog.tif"
        sidecar_path = var_dir / f"fh{fh:03d}.json"
        if not value_path.is_file() or not sidecar_path.is_file():
            continue
        try:
            sidecar = json.loads(sidecar_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        valid_time_raw = sidecar.get("valid_time") or frame.get("valid_time")
        valid_time = _parse_iso_datetime(valid_time_raw)
        if valid_time is None:
            continue
        source_metadata = sidecar.get("source_metadata")
        slot_time = None
        if isinstance(source_metadata, dict):
            slot_time = _parse_iso_datetime(source_metadata.get("slot_time"))
        frames.append(GOESPublishedFrame(valid_time=valid_time, slot_time=slot_time or valid_time, value_path=value_path, sidecar=sidecar))
    frames.sort(key=lambda item: item.slot_time)
    return run_id, frames


def publish_goes_bundle(
    *,
    data_root: Path,
    frames: list[GOESBundleFrame],
    publish_time: datetime | None = None,
    previous_frames: list[GOESPublishedFrame] | None = None,
    target_frame_count: int | None = None,
    expected_frame_count: int | None = None,
    band_config: GOESBandConfig | None = None,
) -> GOESPublishResult:
    if not frames and not previous_frames:
        raise ValueError("GOES bundle publish requires at least one frame")

    band_config = _resolve_band_config(band_config)
    var_id = band_config.variable_id
    publish_dt = (publish_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_id = format_run_id(publish_dt, include_minutes=True)
    preserved_manifest_variables = _preserved_manifest_variables(
        data_root=data_root,
        run_id=run_id,
        exclude_var_id=var_id,
    )
    _prepare_stage_run_dir(data_root=data_root, run_id=run_id)

    merged_by_slot_time: dict[datetime, GOESPublishedFrame | GOESBundleFrame] = {}
    for frame in sorted(previous_frames or [], key=lambda item: item.slot_time):
        merged_by_slot_time[frame.slot_time.astimezone(timezone.utc)] = frame
    for frame in sorted(frames, key=lambda item: item.slot_time):
        merged_by_slot_time[frame.slot_time.astimezone(timezone.utc)] = frame

    ordered_inputs = [merged_by_slot_time[key] for key in sorted(merged_by_slot_time)]
    if target_frame_count is not None and target_frame_count > 0:
        ordered_inputs = ordered_inputs[-int(target_frame_count):]
    if not ordered_inputs:
        raise ValueError("GOES bundle publish resolved to an empty rolling window")

    targets: list[tuple[str, int]] = []
    for fh, frame in enumerate(ordered_inputs):
        if isinstance(frame, GOESPublishedFrame):
            reuse_goes_frame(
                data_root=data_root,
                run_id=run_id,
                forecast_hour=fh,
                frame=frame,
                band_config=band_config,
            )
        else:
            write_goes_frame(
                data_root=data_root,
                run_id=run_id,
                forecast_hour=fh,
                frame=frame,
                band_config=band_config,
            )
        targets.append((var_id, fh))

    if grid_build_enabled():
        manifest_count = build_grid_manifests_for_run_root(
            run_root=data_root / "staging" / GOES_EAST_MODEL_ID / run_id,
            model=GOES_EAST_MODEL_ID,
            run=run_id,
            variables=(var_id,),
        )
        logger.info("GOES grid manifest build: run=%s manifests=%d", run_id, manifest_count)

    promote_run(data_root=data_root, model=GOES_EAST_MODEL_ID, run_id=run_id)

    ordered_valid_times = [item.valid_time.astimezone(timezone.utc) for item in ordered_inputs]
    manifest_target_frame_count = (
        max(1, int(expected_frame_count))
        if expected_frame_count is not None
        else len(ordered_valid_times)
    )
    manifest_variables = {
        **preserved_manifest_variables,
        var_id: {
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
    }
    manifest_stub = {
        "last_updated": publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "variables": manifest_variables,
    }
    metadata = build_observed_bundle_health(
        latest_run=run_id,
        manifest=manifest_stub,
        source=GOES_EAST_MODEL_ID,
        now_utc=publish_dt,
        delayed_threshold_minutes=30,
        stale_threshold_minutes=45,
    )
    newest_input = ordered_inputs[-1] if ordered_inputs else None
    newest_source_metadata = getattr(newest_input, "source_metadata", None)
    if isinstance(newest_source_metadata, dict):
        for key in ("satellite", "product", "sector", "band"):
            if newest_source_metadata.get(key) is not None:
                metadata[key] = newest_source_metadata[key]
    write_run_manifest(
        data_root=data_root,
        model=GOES_EAST_MODEL_ID,
        run_id=run_id,
        targets=targets,
        plugin=GOES_EAST_MODEL,
        metadata=metadata,
    )
    _patch_run_manifest_frame_counts(
        data_root=data_root,
        run_id=run_id,
        expected_frames=manifest_target_frame_count,
        available_frames=len(ordered_valid_times),
        var_id=var_id,
    )
    _merge_preserved_manifest_variables(
        data_root=data_root,
        run_id=run_id,
        preserved_variables=preserved_manifest_variables,
    )
    write_latest_pointer(data_root=data_root, model=GOES_EAST_MODEL_ID, run_id=run_id, source="goes_publish_v1")

    manifest_path = data_root / "manifests" / GOES_EAST_MODEL_ID / f"{run_id}.json"
    published_run_dir = data_root / "published" / GOES_EAST_MODEL_ID / run_id
    return GOESPublishResult(
        run_id=run_id,
        published_run_dir=published_run_dir,
        manifest_path=manifest_path,
        frame_count=len(ordered_valid_times),
    )


def write_goes_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: GOESBundleFrame,
    band_config: GOESBandConfig | None = None,
) -> None:
    band_config = _resolve_band_config(band_config)
    var_id = band_config.variable_id
    color_map_id = band_config.color_map_id
    fh_str = f"fh{int(forecast_hour):03d}"
    staging_dir = data_root / "staging" / GOES_EAST_MODEL_ID / run_id / var_id
    staging_dir.mkdir(parents=True, exist_ok=True)

    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"
    values = np.asarray(frame.values, dtype=np.float32)
    _, colorize_meta = float_to_rgba(values, color_map_id, meta_var_key=var_id)
    write_value_cog(values, value_path, model=GOES_EAST_MODEL_ID, region=GOES_EAST_REGION_ID)

    source_metadata = dict(frame.source_metadata or {})
    if frame.source_bucket:
        source_metadata["bucket"] = frame.source_bucket
    if frame.source_key:
        source_metadata["key"] = frame.source_key
    if frame.source_size_bytes is not None:
        source_metadata["size_bytes"] = int(frame.source_size_bytes)
    if frame.source_last_modified is not None:
        source_metadata["last_modified"] = frame.source_last_modified.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    sidecar = build_sidecar_json(
        model=GOES_EAST_MODEL_ID,
        region=GOES_EAST_REGION_ID,
        run_id=run_id,
        var_id=var_id,
        fh=int(forecast_hour),
        run_date=datetime.now(timezone.utc),
        colorize_meta=colorize_meta,
        var_spec=get_color_map_spec(color_map_id),
        var_spec_model=GOES_EAST_MODEL.get_var(var_id),
        value_downsample_factor=1,
        quality=frame.quality,
        quality_flags=frame.quality_flags,
        valid_time_override=frame.valid_time.astimezone(timezone.utc),
        extra_metadata={"source_metadata": source_metadata},
    )
    if frame.source_filename:
        sidecar["source_filename"] = frame.source_filename
    write_json_atomic(sidecar_path, sidecar)

    if grid_build_enabled():
        write_grid_frames_for_run_root(
            run_root=data_root / "staging" / GOES_EAST_MODEL_ID / run_id,
            model=GOES_EAST_MODEL_ID,
            var=var_id,
            fh=int(forecast_hour),
            values=values,
            transform=frame.transform,
            projection=frame.projection,
        )


def reuse_goes_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: GOESPublishedFrame,
    band_config: GOESBandConfig | None = None,
) -> None:
    band_config = _resolve_band_config(band_config)
    var_id = band_config.variable_id
    fh_str = f"fh{int(forecast_hour):03d}"
    staging_dir = data_root / "staging" / GOES_EAST_MODEL_ID / run_id / var_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"
    _link_or_copy(frame.value_path, value_path)

    sidecar = dict(frame.sidecar)
    sidecar["run"] = run_id
    sidecar["fh"] = int(forecast_hour)
    sidecar["valid_time"] = frame.valid_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json_atomic(sidecar_path, sidecar)

    if grid_build_enabled():
        if not _reuse_goes_grid_artifacts(
            data_root=data_root,
            run_id=run_id,
            forecast_hour=int(forecast_hour),
            source_value_path=frame.value_path,
            source_var_id=var_id,
        ):
            with rasterio.open(value_path) as ds:
                write_grid_frames_for_run_root(
                    run_root=data_root / "staging" / GOES_EAST_MODEL_ID / run_id,
                    model=GOES_EAST_MODEL_ID,
                    var=var_id,
                    fh=int(forecast_hour),
                    values=ds.read(1).astype(np.float32, copy=False),
                    transform=ds.transform,
                    projection=ds.crs.to_string() if ds.crs is not None else "EPSG:3857",
                )


def _reuse_goes_grid_artifacts(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    source_value_path: Path,
    source_var_id: str | None = None,
) -> bool:
    source_var_id = str(source_var_id or GOES_EAST_VARIABLE_ID).strip() or GOES_EAST_VARIABLE_ID
    source_fh = _forecast_hour_from_artifact_name(source_value_path)
    if source_fh is None:
        return False
    source_run_root = source_value_path.parent.parent
    source_grid_dir = resolved_grid_dir_for_run_root(source_run_root, source_var_id)
    if not source_grid_dir.is_dir():
        return False

    target_run_root = data_root / "staging" / GOES_EAST_MODEL_ID / run_id
    target_grid_dir = grid_dir_for_run_root(target_run_root, source_var_id)
    target_grid_dir.mkdir(parents=True, exist_ok=True)
    source_token = f"fh{source_fh:03d}"
    target_token = f"fh{int(forecast_hour):03d}"
    source_bins = sorted(source_grid_dir.glob(f"{source_token}.l*.u*.bin"))
    source_meta_paths = sorted(source_grid_dir.glob(f"{source_token}.l*.meta.json"))
    if not source_bins or not source_meta_paths:
        return False

    retargeted_meta: list[tuple[Path, dict[str, Any]]] = []
    for source_meta_path in source_meta_paths:
        try:
            meta = json.loads(source_meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        target_meta_path = target_grid_dir / source_meta_path.name.replace(source_token, target_token, 1)
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
    stage_run = data_root / "staging" / GOES_EAST_MODEL_ID / run_id
    if stage_run.exists():
        shutil.rmtree(stage_run, ignore_errors=True)
    stage_run.mkdir(parents=True, exist_ok=True)


def _link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    try:
        target.hardlink_to(source)
    except OSError:
        shutil.copy2(source, target)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _patch_run_manifest_frame_counts(
    *,
    data_root: Path,
    run_id: str,
    expected_frames: int,
    available_frames: int,
    var_id: str | None = None,
) -> None:
    var_id = str(var_id or GOES_EAST_VARIABLE_ID).strip() or GOES_EAST_VARIABLE_ID
    manifest_path = data_root / "manifests" / GOES_EAST_MODEL_ID / f"{run_id}.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    variables = manifest.get("variables")
    var_entry = variables.get(var_id) if isinstance(variables, dict) else None
    if not isinstance(var_entry, dict):
        return
    var_entry["expected_frames"] = max(1, int(expected_frames))
    var_entry["available_frames"] = max(0, int(available_frames))
    write_json_atomic(manifest_path, manifest)
