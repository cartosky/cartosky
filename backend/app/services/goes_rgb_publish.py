from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.models.goes_east import GOES_EAST_MODEL, GOES_EAST_RGB_LATEST_FILENAME
from app.services.goes_l1b_processing import encode_rgba_webp
from app.services.goes_publish import (
    GOES_EAST_MODEL_ID,
    GOES_EAST_REGION_ID,
    _link_or_copy,
    _merge_preserved_manifest_variables,
    _parse_iso_datetime,
    _patch_run_manifest_frame_counts,
    _prepare_stage_run_dir,
    _preserved_manifest_variables,
)
from app.services.observed_bundle_health import build_observed_bundle_health
from app.services.publish_utils import (
    promote_run,
    write_json_atomic,
    write_latest_pointer,
    write_run_manifest,
)
from app.services.run_ids import format_run_id

TRUE_COLOR_VARIABLE_ID = "true_color"
TRUE_COLOR_WEBP_QUALITY = 92


@dataclass(frozen=True)
class GOESRGBBundleFrame:
    valid_time: datetime
    slot_time: datetime
    rgba: np.ndarray
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GOESRGBPublishedFrame:
    valid_time: datetime
    slot_time: datetime
    webp_path: Path
    filename: str


@dataclass(frozen=True)
class GOESRGBPublishResult:
    run_id: str
    published_run_dir: Path
    manifest_path: Path
    frame_count: int


def load_latest_published_rgb_frames(
    data_root: Path,
) -> tuple[str | None, list[GOESRGBPublishedFrame]]:
    latest_path = data_root / "published" / GOES_EAST_MODEL_ID / GOES_EAST_RGB_LATEST_FILENAME
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

    variables = manifest.get("variables") if isinstance(manifest, dict) else None
    var_entry = variables.get(TRUE_COLOR_VARIABLE_ID) if isinstance(variables, dict) else None
    frames_payload = var_entry.get("frames") if isinstance(var_entry, dict) else None
    if not isinstance(frames_payload, list):
        return run_id, []

    var_dir = data_root / "published" / GOES_EAST_MODEL_ID / run_id / TRUE_COLOR_VARIABLE_ID
    frames: list[GOESRGBPublishedFrame] = []
    for frame_entry in frames_payload:
        if not isinstance(frame_entry, dict):
            continue
        filename = str(frame_entry.get("filename") or "").strip()
        if not filename:
            try:
                filename = f"fh{int(frame_entry['fh']):03d}.webp"
            except (KeyError, TypeError, ValueError):
                continue
        webp_path = var_dir / filename
        if not webp_path.is_file():
            continue
        valid_time = _parse_iso_datetime(frame_entry.get("valid_time"))
        if valid_time is None:
            continue
        slot_time = _parse_iso_datetime(frame_entry.get("slot_time")) or valid_time
        frames.append(
            GOESRGBPublishedFrame(
                valid_time=valid_time,
                slot_time=slot_time,
                webp_path=webp_path,
                filename=filename,
            )
        )
    frames.sort(key=lambda item: item.slot_time)
    return run_id, frames


def publish_goes_rgb_bundle(
    *,
    data_root: Path,
    frames: list[GOESRGBBundleFrame],
    publish_time: datetime | None = None,
    previous_frames: list[GOESRGBPublishedFrame] | None = None,
    target_frame_count: int | None = None,
    expected_frame_count: int | None = None,
    write_latest: bool = True,
) -> GOESRGBPublishResult:
    if not frames and not previous_frames:
        raise ValueError("GOES RGB bundle publish requires at least one frame")

    publish_dt = (publish_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_id = format_run_id(publish_dt, include_minutes=True)
    preserved_manifest_variables = _preserved_manifest_variables(
        data_root=data_root,
        run_id=run_id,
        exclude_var_id=TRUE_COLOR_VARIABLE_ID,
    )
    _prepare_stage_run_dir(
        data_root=data_root,
        run_id=run_id,
        replace_var_id=TRUE_COLOR_VARIABLE_ID,
    )

    merged_by_slot_time: dict[datetime, GOESRGBPublishedFrame | GOESRGBBundleFrame] = {}
    for frame in sorted(previous_frames or [], key=lambda item: item.slot_time):
        merged_by_slot_time[_as_utc_datetime(frame.slot_time)] = frame
    for frame in sorted(frames, key=lambda item: item.slot_time):
        merged_by_slot_time[_as_utc_datetime(frame.slot_time)] = frame

    ordered_frames = [merged_by_slot_time[key] for key in sorted(merged_by_slot_time)]
    if target_frame_count is not None and target_frame_count > 0:
        ordered_frames = ordered_frames[-int(target_frame_count):]
    if not ordered_frames:
        raise ValueError("GOES RGB bundle publish resolved to an empty rolling window")

    targets: list[tuple[str, int]] = []
    for fh, frame in enumerate(ordered_frames):
        if isinstance(frame, GOESRGBPublishedFrame):
            _reuse_rgb_frame(
                data_root=data_root,
                run_id=run_id,
                forecast_hour=fh,
                frame=frame,
            )
        else:
            write_rgb_frame(
                data_root=data_root,
                run_id=run_id,
                forecast_hour=fh,
                frame=frame,
            )
        targets.append((TRUE_COLOR_VARIABLE_ID, fh))

    promote_run(data_root=data_root, model=GOES_EAST_MODEL_ID, run_id=run_id)

    manifest_target_frame_count = (
        max(1, int(expected_frame_count))
        if expected_frame_count is not None
        else len(ordered_frames)
    )
    manifest_variables = {
        **preserved_manifest_variables,
        TRUE_COLOR_VARIABLE_ID: _true_color_manifest_entry(
            ordered_frames=ordered_frames,
            expected_frames=manifest_target_frame_count,
        ),
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
    metadata["variable"] = TRUE_COLOR_VARIABLE_ID
    write_run_manifest(
        data_root=data_root,
        model=GOES_EAST_MODEL_ID,
        run_id=run_id,
        targets=targets,
        plugin=GOES_EAST_MODEL,
        metadata=metadata,
    )
    _write_true_color_manifest_entry(
        data_root=data_root,
        run_id=run_id,
        entry=manifest_variables[TRUE_COLOR_VARIABLE_ID],
    )
    _patch_run_manifest_frame_counts(
        data_root=data_root,
        run_id=run_id,
        expected_frames=manifest_target_frame_count,
        available_frames=len(ordered_frames),
        var_id=TRUE_COLOR_VARIABLE_ID,
    )
    _merge_preserved_manifest_variables(
        data_root=data_root,
        run_id=run_id,
        preserved_variables=preserved_manifest_variables,
    )
    if write_latest:
        write_latest_pointer(
            data_root=data_root,
            model=GOES_EAST_MODEL_ID,
            run_id=run_id,
            source="goes_rgb_publish_v1",
        )
    rgb_latest_path = (
        data_root / "published" / GOES_EAST_MODEL_ID / GOES_EAST_RGB_LATEST_FILENAME
    )
    write_json_atomic(
        rgb_latest_path,
        {
            "run_id": run_id,
            "cycle_utc": publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_utc": publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "goes_rgb_publish_v1",
        },
    )

    manifest_path = data_root / "manifests" / GOES_EAST_MODEL_ID / f"{run_id}.json"
    published_run_dir = data_root / "published" / GOES_EAST_MODEL_ID / run_id
    return GOESRGBPublishResult(
        run_id=run_id,
        published_run_dir=published_run_dir,
        manifest_path=manifest_path,
        frame_count=len(ordered_frames),
    )


def write_rgb_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: GOESRGBBundleFrame,
) -> Path:
    fh = int(forecast_hour)
    fh_str = f"fh{fh:03d}"
    staging_dir = data_root / "staging" / GOES_EAST_MODEL_ID / run_id / TRUE_COLOR_VARIABLE_ID
    staging_dir.mkdir(parents=True, exist_ok=True)

    webp_filename = f"{fh_str}.webp"
    webp_path = staging_dir / webp_filename
    sidecar_path = staging_dir / f"{fh_str}.json"
    webp_path.write_bytes(encode_rgba_webp(frame.rgba, quality=TRUE_COLOR_WEBP_QUALITY))
    sidecar = _rgb_sidecar(
        run_id=run_id,
        forecast_hour=fh,
        valid_time=frame.valid_time,
        filename=webp_filename,
        source_metadata=frame.source_metadata,
    )
    write_json_atomic(sidecar_path, sidecar)
    return webp_path


def _reuse_rgb_frame(
    *,
    data_root: Path,
    run_id: str,
    forecast_hour: int,
    frame: GOESRGBPublishedFrame,
) -> Path:
    fh = int(forecast_hour)
    fh_str = f"fh{fh:03d}"
    staging_dir = data_root / "staging" / GOES_EAST_MODEL_ID / run_id / TRUE_COLOR_VARIABLE_ID
    staging_dir.mkdir(parents=True, exist_ok=True)

    webp_filename = f"{fh_str}.webp"
    webp_path = staging_dir / webp_filename
    sidecar_path = staging_dir / f"{fh_str}.json"
    _link_or_copy(frame.webp_path, webp_path)
    sidecar = _rgb_sidecar(
        run_id=run_id,
        forecast_hour=fh,
        valid_time=frame.valid_time,
        filename=webp_filename,
        source_metadata={},
    )
    write_json_atomic(sidecar_path, sidecar)
    return webp_path


def _rgb_sidecar(
    *,
    run_id: str,
    forecast_hour: int,
    valid_time: datetime,
    filename: str,
    source_metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "contract_version": "3.0",
        "model": GOES_EAST_MODEL_ID,
        "region": GOES_EAST_REGION_ID,
        "run": run_id,
        "var": TRUE_COLOR_VARIABLE_ID,
        "fh": int(forecast_hour),
        "valid_time": _as_utc_datetime(valid_time).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "raster_rgb",
        "render_substrate": "image",
        "filename": filename,
        "source_metadata": dict(source_metadata or {}),
    }


def _true_color_manifest_entry(
    *,
    ordered_frames: list[GOESRGBPublishedFrame | GOESRGBBundleFrame],
    expected_frames: int,
) -> dict[str, Any]:
    return {
        "display_name": "True Color",
        "kind": "raster_rgb",
        "render_substrate": "image",
        "supports_colormap": False,
        "supports_sampling": False,
        "expected_frames": max(1, int(expected_frames)),
        "available_frames": len(ordered_frames),
        "frames": [
            {
                "fh": fh,
                "valid_time": _as_utc_datetime(frame.valid_time).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "slot_time": _as_utc_datetime(frame.slot_time).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "filename": f"fh{fh:03d}.webp",
            }
            for fh, frame in enumerate(ordered_frames)
        ],
    }


def _write_true_color_manifest_entry(
    *,
    data_root: Path,
    run_id: str,
    entry: dict[str, Any],
) -> None:
    manifest_path = data_root / "manifests" / GOES_EAST_MODEL_ID / f"{run_id}.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        variables = {}
        manifest["variables"] = variables
    variables[TRUE_COLOR_VARIABLE_ID] = dict(entry)
    write_json_atomic(manifest_path, manifest)


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
