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

from app.config import binary_sampling_models, grid_build_enabled
from app.models.goes_east import GOES_EAST_MODEL
from app.services.builder.colorize import float_to_rgba
from app.services.builder.cog_writer import write_value_cog
from app.services.builder.pipeline import build_sidecar_json, check_pre_encode_value_sanity
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
BAND_CONFIG_WV8 = GOESBandConfig(
    variable_id="wv8",
    color_map_id="goes_wv8_enhanced",
)
BAND_CONFIG_VIS2 = GOESBandConfig(
    variable_id="vis2",
    color_map_id="goes_vis2_enhanced",
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


def _latest_published_run_id(data_root: Path) -> str | None:
    latest_path = data_root / "published" / GOES_EAST_MODEL_ID / "LATEST.json"
    try:
        latest_payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    run_id = str(latest_payload.get("run_id") or "").strip()
    return run_id or None


def _preservation_source_run_id(data_root: Path, run_id: str) -> str:
    current_manifest = data_root / "manifests" / GOES_EAST_MODEL_ID / f"{run_id}.json"
    if current_manifest.is_file():
        return run_id
    latest_run_id = _latest_published_run_id(data_root)
    if not latest_run_id:
        return run_id
    latest_run = data_root / "published" / GOES_EAST_MODEL_ID / latest_run_id
    latest_manifest = data_root / "manifests" / GOES_EAST_MODEL_ID / f"{latest_run_id}.json"
    if latest_run.is_dir() and latest_manifest.is_file():
        return latest_run_id
    return run_id


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
    write_latest: bool = True,
) -> GOESPublishResult:
    if not frames and not previous_frames:
        raise ValueError("GOES bundle publish requires at least one frame")

    band_config = _resolve_band_config(band_config)
    var_id = band_config.variable_id
    publish_dt = (publish_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_id = format_run_id(publish_dt, include_minutes=True)
    preservation_source_run_id = _preservation_source_run_id(data_root, run_id)
    preserved_manifest_variables = _preserved_manifest_variables(
        data_root=data_root,
        run_id=preservation_source_run_id,
        exclude_var_id=var_id,
    )
    _prepare_stage_run_dir(
        data_root=data_root,
        run_id=run_id,
        replace_var_id=var_id,
        source_run_id=preservation_source_run_id,
    )

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
    written_fhs: list[int] = []
    for fh, frame in enumerate(ordered_inputs):
        if isinstance(frame, GOESPublishedFrame):
            # Reuse is deliberately NOT gated: a reused frame is byte-identical
            # to one that passed the pre-encode gate at its original write.
            reuse_goes_frame(
                data_root=data_root,
                run_id=run_id,
                forecast_hour=fh,
                frame=frame,
                band_config=band_config,
            )
        elif not write_goes_frame(
            data_root=data_root,
            run_id=run_id,
            forecast_hour=fh,
            frame=frame,
            band_config=band_config,
        ):
            continue
        targets.append((var_id, fh))
        written_fhs.append(fh)

    if not targets:
        raise ValueError("GOES bundle publish requires at least one frame")

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
            "available_frames": len(written_fhs),
            "frames": [
                {
                    "fh": fh,
                    "valid_time": ordered_valid_times[fh].strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                for fh in written_fhs
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
        available_frames=len(written_fhs),
        var_id=var_id,
    )
    _merge_preserved_manifest_variables(
        data_root=data_root,
        run_id=run_id,
        preserved_variables=preserved_manifest_variables,
    )
    if write_latest:
        write_latest_pointer(data_root=data_root, model=GOES_EAST_MODEL_ID, run_id=run_id, source="goes_publish_v1")

    manifest_path = data_root / "manifests" / GOES_EAST_MODEL_ID / f"{run_id}.json"
    published_run_dir = data_root / "published" / GOES_EAST_MODEL_ID / run_id
    return GOESPublishResult(
        run_id=run_id,
        published_run_dir=published_run_dir,
        manifest_path=manifest_path,
        frame_count=len(written_fhs),
    )


def write_goes_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: GOESBundleFrame,
    band_config: GOESBandConfig | None = None,
) -> bool:
    """Write one band frame's artifacts. Returns False when the enforced
    pre-encode gate rejected the frame (nothing written), mirroring how
    build_frame signals a failed frame via status rather than raising."""
    band_config = _resolve_band_config(band_config)
    var_id = band_config.variable_id
    color_map_id = band_config.color_map_id
    values = np.asarray(frame.values, dtype=np.float32)
    var_spec_colormap = get_color_map_spec(color_map_id)
    var_spec_model = GOES_EAST_MODEL.get_var(var_id)
    var_capability = GOES_EAST_MODEL.get_var_capability(var_id)
    # Pre-encode gate (COG->binary sampling migration): the check itself runs
    # on every fresh frame write, per band-publish invocation. For a
    # binary-sampling-allowlisted model it is ENFORCED — failure (or a gate
    # error) rejects the frame before ANY artifact is written, matching
    # pipeline.py's binary_only branch. Otherwise it stays the Phase C shadow
    # gate: log-only.
    binary_only = GOES_EAST_MODEL_ID in binary_sampling_models()
    try:
        gate_ok = check_pre_encode_value_sanity(
            values,
            var_spec_colormap,
            var_spec_model=var_spec_model,
            var_capability=var_capability,
            label=f"{GOES_EAST_MODEL_ID}/{var_id}/fh{int(forecast_hour):03d}",
        )
    except Exception:
        if binary_only:
            logger.exception(
                "Pre-encode sanity gate errored — rejecting frame "
                "model=%s var=%s fh%03d — frame not published",
                GOES_EAST_MODEL_ID,
                var_id,
                int(forecast_hour),
            )
            return False
        logger.exception(
            "Phase C shadow gate errored: pre-encode value sanity "
            "model=%s var=%s fh%03d; frame remains governed by existing COG gates",
            GOES_EAST_MODEL_ID,
            var_id,
            int(forecast_hour),
        )
        gate_ok = True
    if not gate_ok:
        if binary_only:
            logger.error(
                "Pre-encode sanity gate rejected frame model=%s var=%s "
                "fh%03d — frame not published",
                GOES_EAST_MODEL_ID,
                var_id,
                int(forecast_hour),
            )
            return False
        logger.warning(
            "Phase C shadow gate failed: pre-encode value sanity "
            "model=%s var=%s fh%03d; frame remains governed by existing COG gates",
            GOES_EAST_MODEL_ID,
            var_id,
            int(forecast_hour),
        )

    fh_str = f"fh{int(forecast_hour):03d}"
    staging_dir = data_root / "staging" / GOES_EAST_MODEL_ID / run_id / var_id
    staging_dir.mkdir(parents=True, exist_ok=True)

    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"
    _, colorize_meta = float_to_rgba(values, color_map_id, meta_var_key=var_id)
    if binary_only:
        # Value COG retired for binary-sampling models: the grid binary
        # (written below) serves rendering and sampling, and the enforced
        # gate above already applied the value-quality gate.
        logger.info(
            "Value COG write skipped (model=%s is binary-only)",
            GOES_EAST_MODEL_ID,
        )
    else:
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
        var_spec=var_spec_colormap,
        var_spec_model=var_spec_model,
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
    return True


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


def _prepare_stage_run_dir(
    *,
    data_root: Path,
    run_id: str,
    replace_var_id: str | None = None,
    source_run_id: str | None = None,
) -> None:
    stage_run = data_root / "staging" / GOES_EAST_MODEL_ID / run_id
    if stage_run.exists():
        shutil.rmtree(stage_run, ignore_errors=True)
    stage_run.mkdir(parents=True, exist_ok=True)

    source_id = str(source_run_id or run_id).strip() or run_id
    published_run = data_root / "published" / GOES_EAST_MODEL_ID / source_id
    if published_run.is_dir():
        for var_dir in published_run.iterdir():
            if not var_dir.is_dir():
                continue
            if replace_var_id and var_dir.name == replace_var_id:
                continue
            target_var_dir = stage_run / var_dir.name
            target_var_dir.mkdir(parents=True, exist_ok=True)
            for src_file in var_dir.rglob("*"):
                if src_file.is_file():
                    rel = src_file.relative_to(var_dir)
                    dst_file = target_var_dir / rel
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    _copy_preserved_run_artifact(src_file, dst_file, run_id=run_id, source_run_id=source_id)


def _copy_preserved_run_artifact(source: Path, target: Path, *, run_id: str, source_run_id: str) -> None:
    if source_run_id == run_id or source.suffix.lower() != ".json":
        _link_or_copy(source, target)
        return
    try:
        payload = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError):
        _link_or_copy(source, target)
        return
    if not isinstance(payload, dict):
        _link_or_copy(source, target)
        return
    if payload.get("run") == source_run_id:
        payload["run"] = run_id
    write_json_atomic(target, payload)


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
