from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from app.config import grid_build_enabled
from app.models.rtma_ru import CURRENT_ANALYSIS_MODEL
from app.services.builder.colorize import float_to_rgba
from app.services.builder.cog_writer import write_value_cog
from app.services.colormaps import get_color_map_spec
from app.services.observed_bundle_health import build_observed_bundle_health
from app.services.publish_utils import (
    promote_run,
    write_json_atomic,
    write_latest_pointer,
    write_run_manifest,
)
from app.services.run_ids import format_run_id

try:
    from app.services.builder.pipeline import build_sidecar_json as _shared_build_sidecar_json
except ModuleNotFoundError as exc:
    if exc.name != "brotli":
        raise
    _shared_build_sidecar_json = None

try:
    from app.services.grid import (
        build_grid_manifests_for_run_root,
        grid_dir_for_run_root,
        resolved_grid_dir_for_run_root,
        write_grid_frames_for_run_root,
    )
except ModuleNotFoundError as exc:
    if exc.name != "brotli":
        raise
    build_grid_manifests_for_run_root = None
    grid_dir_for_run_root = None
    resolved_grid_dir_for_run_root = None
    write_grid_frames_for_run_root = None

logger = logging.getLogger(__name__)

CURRENT_ANALYSIS_MODEL_ID = "current_analysis"
CURRENT_ANALYSIS_REGION_ID = "conus"
CURRENT_ANALYSIS_REPRESENTATIVE_VAR_ID = "tmp2m"


@dataclass(frozen=True)
class CurrentAnalysisBundleFrame:
    valid_time: datetime
    values_by_var: dict[str, np.ndarray]
    transform: Any
    projection: str = "EPSG:3857"
    quality: str = "full"
    quality_flags: list[str] = field(default_factory=list)
    source_metadata: dict[str, Any] = field(default_factory=dict)
    source_metadata_by_var: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_filename_by_var: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CurrentAnalysisPublishedFrame:
    valid_time: datetime
    value_paths: dict[str, Path]
    sidecars: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class CurrentAnalysisPublishResult:
    run_id: str
    published_run_dir: Path
    manifest_path: Path
    frame_count: int


def load_latest_published_current_analysis_frames(
    data_root: Path,
) -> tuple[str | None, list[CurrentAnalysisPublishedFrame]]:
    latest_path = data_root / "published" / CURRENT_ANALYSIS_MODEL_ID / "LATEST.json"
    if not latest_path.is_file():
        return None, []
    try:
        latest_payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, []
    run_id = str(latest_payload.get("run_id") or "").strip()
    if not run_id:
        return None, []

    manifest_path = data_root / "manifests" / CURRENT_ANALYSIS_MODEL_ID / f"{run_id}.json"
    if not manifest_path.is_file():
        return run_id, []
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return run_id, []

    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return run_id, []
    representative_entry = variables.get(CURRENT_ANALYSIS_REPRESENTATIVE_VAR_ID)
    if not isinstance(representative_entry, dict):
        return run_id, []
    rep_frames = representative_entry.get("frames")
    if not isinstance(rep_frames, list):
        return run_id, []

    published_run_dir = data_root / "published" / CURRENT_ANALYSIS_MODEL_ID / run_id
    variable_ids = sorted(str(var_id) for var_id in variables.keys())
    frames: list[CurrentAnalysisPublishedFrame] = []
    for frame in rep_frames:
        if not isinstance(frame, dict):
            continue
        try:
            fh = int(frame["fh"])
        except (KeyError, TypeError, ValueError):
            continue
        valid_time = _parse_iso_datetime(frame.get("valid_time"))
        if valid_time is None:
            continue
        value_paths: dict[str, Path] = {}
        sidecars: dict[str, dict[str, Any]] = {}
        for var_id in variable_ids:
            var_dir = published_run_dir / var_id
            value_path = var_dir / f"fh{fh:03d}.val.cog.tif"
            sidecar_path = var_dir / f"fh{fh:03d}.json"
            if not value_path.is_file() or not sidecar_path.is_file():
                continue
            try:
                sidecar = json.loads(sidecar_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            value_paths[var_id] = value_path
            sidecars[var_id] = sidecar
        if not value_paths:
            continue
        frames.append(
            CurrentAnalysisPublishedFrame(
                valid_time=valid_time,
                value_paths=value_paths,
                sidecars=sidecars,
            )
        )
    frames.sort(key=lambda item: item.valid_time.astimezone(timezone.utc))
    return run_id, frames


def publish_current_analysis_bundle(
    *,
    data_root: Path,
    frames: list[CurrentAnalysisBundleFrame],
    publish_time: datetime | None = None,
    previous_frames: list[CurrentAnalysisPublishedFrame] | None = None,
    target_frame_count: int | None = None,
    expected_frame_count: int | None = None,
) -> CurrentAnalysisPublishResult:
    if not frames and not previous_frames:
        raise ValueError("Current Analysis publish requires at least one frame")

    publish_dt = (publish_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_id = format_run_id(publish_dt, include_minutes=True)
    _prepare_stage_run_dir(data_root=data_root, run_id=run_id)

    merged_by_valid_time: dict[datetime, CurrentAnalysisPublishedFrame | CurrentAnalysisBundleFrame] = {}
    for frame in sorted(previous_frames or [], key=lambda item: item.valid_time.astimezone(timezone.utc)):
        merged_by_valid_time[frame.valid_time.astimezone(timezone.utc)] = frame
    for frame in sorted(frames, key=lambda item: item.valid_time.astimezone(timezone.utc)):
        merged_by_valid_time[frame.valid_time.astimezone(timezone.utc)] = frame

    ordered_inputs = [merged_by_valid_time[key] for key in sorted(merged_by_valid_time)]
    if target_frame_count is not None and target_frame_count > 0:
        ordered_inputs = ordered_inputs[-int(target_frame_count):]
    if not ordered_inputs:
        raise ValueError("Current Analysis publish resolved to an empty rolling window")

    variable_ids = _collect_variable_ids(ordered_inputs)
    if not variable_ids:
        raise ValueError("Current Analysis publish requires at least one variable")

    for fh, frame in enumerate(ordered_inputs):
        if isinstance(frame, CurrentAnalysisPublishedFrame):
            reuse_current_analysis_frame(
                data_root=data_root,
                run_id=run_id,
                forecast_hour=fh,
                frame=frame,
                variable_ids=variable_ids,
            )
        else:
            write_current_analysis_frame(
                data_root=data_root,
                run_id=run_id,
                forecast_hour=fh,
                frame=frame,
                variable_ids=variable_ids,
            )

    targets = [(var_id, fh) for var_id in variable_ids for fh in range(len(ordered_inputs))]
    if grid_build_enabled():
        _require_grid_support()
        build_grid_manifests_for_run_root(
            run_root=data_root / "staging" / CURRENT_ANALYSIS_MODEL_ID / run_id,
            model=CURRENT_ANALYSIS_MODEL_ID,
            run=run_id,
            variables=tuple(variable_ids),
        )

    promote_run(data_root=data_root, model=CURRENT_ANALYSIS_MODEL_ID, run_id=run_id)

    ordered_valid_times = [item.valid_time.astimezone(timezone.utc) for item in ordered_inputs]
    manifest_target_frame_count = max(1, int(expected_frame_count)) if expected_frame_count is not None else len(ordered_valid_times)
    metadata = build_observed_bundle_health(
        latest_run=run_id,
        manifest={
            "last_updated": publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "variables": {
                CURRENT_ANALYSIS_REPRESENTATIVE_VAR_ID: {
                    "expected_frames": manifest_target_frame_count,
                    "available_frames": len(ordered_valid_times),
                    "frames": [
                        {"fh": fh, "valid_time": valid_time.strftime("%Y-%m-%dT%H:%M:%SZ")}
                        for fh, valid_time in enumerate(ordered_valid_times)
                    ],
                }
            },
        },
        source=CURRENT_ANALYSIS_MODEL_ID,
        now_utc=publish_dt,
    )
    metadata["variables_published"] = list(variable_ids)
    write_run_manifest(
        data_root=data_root,
        model=CURRENT_ANALYSIS_MODEL_ID,
        run_id=run_id,
        targets=targets,
        plugin=CURRENT_ANALYSIS_MODEL,
        metadata=metadata,
    )
    if expected_frame_count is not None:
        _patch_run_manifest_expected_frame_counts(
            data_root=data_root,
            run_id=run_id,
            expected_frames=manifest_target_frame_count,
        )
    write_latest_pointer(
        data_root=data_root,
        model=CURRENT_ANALYSIS_MODEL_ID,
        run_id=run_id,
        source="current_analysis_publish_v1",
    )

    manifest_path = data_root / "manifests" / CURRENT_ANALYSIS_MODEL_ID / f"{run_id}.json"
    published_run_dir = data_root / "published" / CURRENT_ANALYSIS_MODEL_ID / run_id
    return CurrentAnalysisPublishResult(
        run_id=run_id,
        published_run_dir=published_run_dir,
        manifest_path=manifest_path,
        frame_count=len(ordered_valid_times),
    )


def write_current_analysis_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: CurrentAnalysisBundleFrame,
    variable_ids: list[str],
) -> None:
    fh_str = f"fh{int(forecast_hour):03d}"
    shared_source_metadata = dict(frame.source_metadata or {})
    for var_id in variable_ids:
        values_raw = frame.values_by_var.get(var_id)
        if values_raw is None:
            continue
        capability = CURRENT_ANALYSIS_MODEL.get_var_capability(var_id)
        color_map_id = getattr(capability, "color_map_id", None) if capability is not None else None
        if not isinstance(color_map_id, str) or not color_map_id.strip():
            raise ValueError(f"Current Analysis variable {var_id!r} has no configured color map")

        staging_dir = data_root / "staging" / CURRENT_ANALYSIS_MODEL_ID / run_id / var_id
        staging_dir.mkdir(parents=True, exist_ok=True)
        value_path = staging_dir / f"{fh_str}.val.cog.tif"
        sidecar_path = staging_dir / f"{fh_str}.json"
        values = np.asarray(values_raw, dtype=np.float32)
        _, colorize_meta = float_to_rgba(values, color_map_id, meta_var_key=var_id)
        write_value_cog(values, value_path, model=CURRENT_ANALYSIS_MODEL_ID, region=CURRENT_ANALYSIS_REGION_ID)

        source_metadata = dict(shared_source_metadata)
        source_metadata.update(frame.source_metadata_by_var.get(var_id, {}))
        sidecar = _build_sidecar_json(
            model=CURRENT_ANALYSIS_MODEL_ID,
            region=CURRENT_ANALYSIS_REGION_ID,
            run_id=run_id,
            var_id=var_id,
            fh=int(forecast_hour),
            run_date=datetime.now(timezone.utc),
            colorize_meta=colorize_meta,
            var_spec=get_color_map_spec(color_map_id),
            var_spec_model=CURRENT_ANALYSIS_MODEL.get_var(var_id),
            value_downsample_factor=1,
            quality=frame.quality,
            quality_flags=frame.quality_flags,
            valid_time_override=frame.valid_time.astimezone(timezone.utc),
            extra_metadata={"source_metadata": source_metadata},
        )
        source_filename = frame.source_filename_by_var.get(var_id)
        if source_filename:
            sidecar["source_filename"] = source_filename
        write_json_atomic(sidecar_path, sidecar)

        if grid_build_enabled():
            _require_grid_support()
            write_grid_frames_for_run_root(
                run_root=data_root / "staging" / CURRENT_ANALYSIS_MODEL_ID / run_id,
                model=CURRENT_ANALYSIS_MODEL_ID,
                var=var_id,
                fh=int(forecast_hour),
                values=values,
                transform=frame.transform,
                projection=frame.projection,
            )


def reuse_current_analysis_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: CurrentAnalysisPublishedFrame,
    variable_ids: list[str],
) -> None:
    fh_str = f"fh{int(forecast_hour):03d}"
    for var_id in variable_ids:
        source_value_path = frame.value_paths.get(var_id)
        sidecar = frame.sidecars.get(var_id)
        if source_value_path is None or sidecar is None:
            continue
        staging_dir = data_root / "staging" / CURRENT_ANALYSIS_MODEL_ID / run_id / var_id
        staging_dir.mkdir(parents=True, exist_ok=True)
        value_path = staging_dir / f"{fh_str}.val.cog.tif"
        sidecar_path = staging_dir / f"{fh_str}.json"
        _link_or_copy(source_value_path, value_path)

        retargeted_sidecar = dict(sidecar)
        retargeted_sidecar["run"] = run_id
        retargeted_sidecar["fh"] = int(forecast_hour)
        retargeted_sidecar["valid_time"] = frame.valid_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        write_json_atomic(sidecar_path, retargeted_sidecar)

        if grid_build_enabled():
            _require_grid_support()
            if not _reuse_current_analysis_grid_artifacts(
                data_root=data_root,
                run_id=run_id,
                forecast_hour=int(forecast_hour),
                var_id=var_id,
                source_value_path=source_value_path,
            ):
                with rasterio.open(value_path) as ds:
                    write_grid_frames_for_run_root(
                        run_root=data_root / "staging" / CURRENT_ANALYSIS_MODEL_ID / run_id,
                        model=CURRENT_ANALYSIS_MODEL_ID,
                        var=var_id,
                        fh=int(forecast_hour),
                        values=ds.read(1).astype(np.float32, copy=False),
                        transform=ds.transform,
                        projection=ds.crs.to_string() if ds.crs is not None else "EPSG:3857",
                    )


def _reuse_current_analysis_grid_artifacts(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    var_id: str,
    source_value_path: Path,
) -> bool:
    _require_grid_support()
    source_fh = _forecast_hour_from_artifact_name(source_value_path)
    if source_fh is None:
        return False
    source_run_root = source_value_path.parent.parent
    source_grid_dir = resolved_grid_dir_for_run_root(source_run_root, var_id)
    if not source_grid_dir.is_dir():
        return False

    target_run_root = data_root / "staging" / CURRENT_ANALYSIS_MODEL_ID / run_id
    target_grid_dir = grid_dir_for_run_root(target_run_root, var_id)
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


def _collect_variable_ids(
    frames: list[CurrentAnalysisPublishedFrame | CurrentAnalysisBundleFrame],
) -> list[str]:
    variable_ids: set[str] = set()
    for frame in frames:
        if isinstance(frame, CurrentAnalysisPublishedFrame):
            variable_ids.update(str(var_id) for var_id in frame.value_paths.keys())
        else:
            variable_ids.update(str(var_id) for var_id in frame.values_by_var.keys())
    ordered_catalog = tuple(CURRENT_ANALYSIS_MODEL.capabilities.variable_catalog.keys())
    ordered = [var_id for var_id in ordered_catalog if var_id in variable_ids]
    extras = sorted(var_id for var_id in variable_ids if var_id not in ordered_catalog)
    return ordered + extras


def _forecast_hour_from_artifact_name(path: Path) -> int | None:
    token = Path(path).name.split(".", 1)[0]
    if not token.startswith("fh"):
        return None
    try:
        return int(token.removeprefix("fh"))
    except ValueError:
        return None


def _prepare_stage_run_dir(*, data_root: Path, run_id: str) -> None:
    stage_run = data_root / "staging" / CURRENT_ANALYSIS_MODEL_ID / run_id
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


def _patch_run_manifest_expected_frame_counts(
    *,
    data_root: Path,
    run_id: str,
    expected_frames: int,
) -> None:
    manifest_path = data_root / "manifests" / CURRENT_ANALYSIS_MODEL_ID / f"{run_id}.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return
    for entry in variables.values():
        if not isinstance(entry, dict):
            continue
        entry["expected_frames"] = max(1, int(expected_frames))
    write_json_atomic(manifest_path, manifest)


def _require_grid_support() -> None:
    if (
        build_grid_manifests_for_run_root is None
        or grid_dir_for_run_root is None
        or resolved_grid_dir_for_run_root is None
        or write_grid_frames_for_run_root is None
    ):
        raise RuntimeError("Grid publishing requires optional brotli-backed grid dependencies")


def _build_sidecar_json(**kwargs: Any) -> dict[str, Any]:
    if _shared_build_sidecar_json is not None:
        return _shared_build_sidecar_json(**kwargs)
    return _fallback_build_sidecar_json(**kwargs)


def _fallback_build_sidecar_json(
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
    value_downsample_factor: int = 1,
    quality: str = "full",
    quality_flags: list[str] | None = None,
    valid_time_override: datetime | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del run_date
    valid_time = (valid_time_override or datetime.now(timezone.utc)).astimezone(timezone.utc)
    display_name = colorize_meta.get("display_name") or var_spec.get("display_name") or getattr(var_spec_model, "name", None) or var_id
    units = getattr(var_spec_model, "units", None) or colorize_meta.get("units") or var_spec.get("units") or ""
    sidecar: dict[str, Any] = {
        "contract_version": "3.0",
        "model": model,
        "run": run_id,
        "var": var_id,
        "fh": int(fh),
        "valid_time": valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "units": str(units),
        "kind": colorize_meta.get("kind") or var_spec.get("type") or getattr(var_spec_model, "kind", None) or "continuous",
        "min": colorize_meta.get("min"),
        "max": colorize_meta.get("max"),
        "quality": "degraded" if str(quality).strip().lower() == "degraded" else "full",
        "quality_flags": [
            item for item in dict.fromkeys(str(flag).strip() for flag in (quality_flags or [])) if item
        ],
        "display_name": str(display_name),
        "legend": {
            "title": var_spec.get("legend_title") or str(display_name),
        },
    }
    if region:
        sidecar["region"] = region
    if value_downsample_factor > 1:
        sidecar["hover_value_downsample_factor"] = int(value_downsample_factor)
    legend_stops = var_spec.get("legend_stops")
    if isinstance(legend_stops, list):
        sidecar["legend"]["stops"] = [list(item) for item in legend_stops]
    if isinstance(extra_metadata, dict):
        for key, value in extra_metadata.items():
            normalized_key = str(key).strip()
            if normalized_key and normalized_key not in sidecar and value is not None:
                sidecar[normalized_key] = value
    return sidecar