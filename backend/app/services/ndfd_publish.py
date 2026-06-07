from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from app.config import grid_build_enabled
from app.models.ndfd import NDFD_MODEL
from app.services.builder.colorize import colorize_metadata
from app.services.builder.cog_writer import warp_to_target_grid, write_value_cog
from app.services.colormaps import get_color_map_spec
from app.services.process_memory import current_rss_bytes, peak_rss_bytes
from app.services.publish_utils import promote_run, write_json_atomic, write_latest_pointer, write_run_manifest
from app.services.run_ids import format_run_id
from app.services.ndfd_source import NDFDSourceField

try:
    from app.services.builder.pipeline import build_sidecar_json as _shared_build_sidecar_json
except ModuleNotFoundError as exc:
    if exc.name != "brotli":
        raise
    _shared_build_sidecar_json = None

try:
    from app.services.grid import build_grid_manifests_for_run_root, write_grid_frames_for_run_root
except ModuleNotFoundError as exc:
    if exc.name != "brotli":
        raise
    build_grid_manifests_for_run_root = None
    write_grid_frames_for_run_root = None

logger = logging.getLogger(__name__)

NDFD_MODEL_ID = "ndfd"
NDFD_REGION_ID = "conus"


@dataclass(frozen=True)
class NDFDPublishResult:
    run_id: str
    published_run_dir: Path
    manifest_path: Path
    frame_count: int


def publish_ndfd_bundle(
    *,
    data_root: Path,
    issue_time: datetime,
    frames_by_var: dict[str, list[NDFDSourceField]] | None = None,
    frame_batches: Iterable[tuple[str, list[NDFDSourceField]]] | None = None,
    variable_ids: Iterable[str] | None = None,
) -> NDFDPublishResult:
    if frames_by_var is None and frame_batches is None:
        raise ValueError("NDFD publish requires at least one variable with frames")

    if grid_build_enabled():
        _require_grid_support()

    run_id = format_run_id(issue_time.astimezone(timezone.utc), include_minutes=True)
    _prepare_stage_run_dir(data_root=data_root, run_id=run_id)

    targets: list[tuple[str, int]] = []
    published_vars: set[str] = set()
    frame_count = 0
    latest_valid_time: datetime | None = None
    for var_id, frames in _iter_frame_batches(frames_by_var=frames_by_var, frame_batches=frame_batches):
        if not frames:
            continue
        published_vars.add(var_id)
        for fh, frame in enumerate(sorted(frames, key=lambda item: item.valid_time.astimezone(timezone.utc))):
            _write_ndfd_frame(
                data_root=data_root,
                run_id=run_id,
                var_id=var_id,
                forecast_hour=fh,
                issue_time=issue_time,
                frame=frame,
            )
            targets.append((var_id, fh))
            frame_count += 1
            frame_valid_time = frame.valid_time.astimezone(timezone.utc)
            latest_valid_time = frame_valid_time if latest_valid_time is None else max(latest_valid_time, frame_valid_time)

    if not targets:
        raise ValueError("NDFD publish requires at least one frame")

    if grid_build_enabled():
        _require_grid_support()
        build_grid_manifests_for_run_root(
            run_root=data_root / "staging" / NDFD_MODEL_ID / run_id,
            model=NDFD_MODEL_ID,
            run=run_id,
            variables=tuple(
                sorted({
                    str(item).strip()
                    for item in (variable_ids or published_vars)
                    if str(item).strip()
                })
            ),
        )

    promote_run(data_root=data_root, model=NDFD_MODEL_ID, run_id=run_id)
    metadata = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issue_time": issue_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_valid_time": latest_valid_time.strftime("%Y-%m-%dT%H:%M:%SZ") if latest_valid_time is not None else None,
        "source": "ndfd_tgftp_grib2",
    }
    write_run_manifest(
        data_root=data_root,
        model=NDFD_MODEL_ID,
        run_id=run_id,
        targets=targets,
        plugin=NDFD_MODEL,
        metadata=metadata,
    )
    write_latest_pointer(data_root=data_root, model=NDFD_MODEL_ID, run_id=run_id, source="ndfd_publish_v1")

    manifest_path = data_root / "manifests" / NDFD_MODEL_ID / f"{run_id}.json"
    published_run_dir = data_root / "published" / NDFD_MODEL_ID / run_id
    return NDFDPublishResult(
        run_id=run_id,
        published_run_dir=published_run_dir,
        manifest_path=manifest_path,
        frame_count=frame_count,
    )


def _iter_frame_batches(
    *,
    frames_by_var: dict[str, list[NDFDSourceField]] | None,
    frame_batches: Iterable[tuple[str, list[NDFDSourceField]]] | None,
) -> Iterable[tuple[str, list[NDFDSourceField]]]:
    if frames_by_var is not None:
        yield from sorted(frames_by_var.items(), key=lambda item: item[0])
        return
    if frame_batches is None:
        return
    yield from frame_batches


def _prepare_stage_run_dir(*, data_root: Path, run_id: str) -> None:
    stage_run_dir = data_root / "staging" / NDFD_MODEL_ID / run_id
    if stage_run_dir.exists():
        shutil.rmtree(stage_run_dir)
    stage_run_dir.mkdir(parents=True, exist_ok=True)


def _write_ndfd_frame(
    *,
    data_root: Path,
    run_id: str,
    var_id: str,
    forecast_hour: int,
    issue_time: datetime,
    frame: NDFDSourceField,
) -> None:
    fh_str = f"fh{int(forecast_hour):03d}"
    staging_dir = data_root / "staging" / NDFD_MODEL_ID / run_id / var_id
    staging_dir.mkdir(parents=True, exist_ok=True)

    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"
    values, dst_transform = _warp_frame_to_target_grid(frame)
    _log_ndfd_publish_memory(
        "after_reprojection",
        array_mib=f"{_array_mib(values):.1f}",
        fh=forecast_hour,
        shape=f"{values.shape[0]}x{values.shape[1]}",
        var=var_id,
    )
    var_capability = NDFD_MODEL.get_var_capability(var_id)
    if var_capability is None or not var_capability.color_map_id:
        raise ValueError(f"Missing NDFD color map registration for {var_id}")
    colorize_meta = colorize_metadata(values, str(var_capability.color_map_id), meta_var_key=var_id)
    write_value_cog(values, value_path, model=NDFD_MODEL_ID, region=NDFD_REGION_ID)

    sidecar = _build_sidecar_json(
        model=NDFD_MODEL_ID,
        region=NDFD_REGION_ID,
        run_id=run_id,
        var_id=var_id,
        fh=int(forecast_hour),
        run_date=issue_time.astimezone(timezone.utc),
        colorize_meta=colorize_meta,
        var_spec=get_color_map_spec(str(var_capability.color_map_id)),
        var_spec_model=NDFD_MODEL.get_var(var_id),
        value_downsample_factor=1,
        valid_time_override=frame.valid_time.astimezone(timezone.utc),
        extra_metadata={
            "source_filename": frame.source_filename,
            "source_metadata": {
                "url": frame.source_url,
                "issue_time": frame.issue_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "upstream_units": frame.source_units,
            },
        },
    )
    write_json_atomic(sidecar_path, sidecar)

    if grid_build_enabled():
        _require_grid_support()
        write_grid_frames_for_run_root(
            run_root=data_root / "staging" / NDFD_MODEL_ID / run_id,
            model=NDFD_MODEL_ID,
            var=var_id,
            fh=int(forecast_hour),
            values=values,
            transform=dst_transform,
            projection="EPSG:3857",
        )


def _warp_frame_to_target_grid(frame: NDFDSourceField) -> tuple[np.ndarray, Any]:
    values = np.asarray(frame.values, dtype=np.float32)
    warped_values, dst_transform = warp_to_target_grid(
        values,
        frame.crs,
        frame.transform,
        model=NDFD_MODEL_ID,
        region=NDFD_REGION_ID,
        resampling="bilinear",
        src_nodata=None,
        dst_nodata=float("nan"),
        working_dtype=np.float32,
    )
    return np.asarray(warped_values, dtype=np.float32), dst_transform


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


def _require_grid_support() -> None:
    if build_grid_manifests_for_run_root is None or write_grid_frames_for_run_root is None:
        raise RuntimeError("Grid publishing requires optional brotli-backed grid dependencies")


def _bytes_to_mib(num_bytes: int) -> float:
    return float(num_bytes) / (1024.0 * 1024.0)


def _array_mib(value: Any) -> float:
    if isinstance(value, np.ndarray):
        return _bytes_to_mib(int(value.nbytes))
    return 0.0


def _log_ndfd_publish_memory(stage: str, **details: Any) -> None:
    detail_tokens = " ".join(
        f"{key}={value}"
        for key, value in sorted(details.items())
    )
    suffix = f" {detail_tokens}" if detail_tokens else ""
    logger.info(
        "NDFD memory checkpoint stage=%s current_rss_mib=%.1f peak_rss_mib=%.1f%s",
        stage,
        _bytes_to_mib(current_rss_bytes()),
        _bytes_to_mib(peak_rss_bytes()),
        suffix,
    )
