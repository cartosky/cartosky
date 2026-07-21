from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.config import binary_sampling_enabled, grid_build_enabled
from app.models.wpc import WPC_MODEL
from app.services.builder.colorize import float_to_rgba
from app.services.builder.cog_writer import warp_to_target_grid, write_value_cog
from app.services.colormaps import get_color_map_spec
from app.services.publish_utils import promote_run, write_json_atomic, write_latest_pointer, write_run_manifest
from app.services.run_ids import format_run_id
from app.services.wpc_source import WPCSourceField

try:
    from app.services.builder.pipeline import build_sidecar_json as _shared_build_sidecar_json
    from app.services.builder.pipeline import check_pre_encode_value_sanity
except ModuleNotFoundError as exc:
    if exc.name != "brotli":
        raise
    _shared_build_sidecar_json = None
    check_pre_encode_value_sanity = None

try:
    from app.services.grid import build_grid_manifests_for_run_root, write_grid_frames_for_run_root
except ModuleNotFoundError as exc:
    if exc.name != "brotli":
        raise
    build_grid_manifests_for_run_root = None
    write_grid_frames_for_run_root = None

logger = logging.getLogger(__name__)

WPC_MODEL_ID = "wpc"
WPC_REGION_ID = "conus"
WPC_PUBLISH_SOURCE = "wpc_5km_qpf_cumulative_v2"


@dataclass(frozen=True)
class WPCPublishResult:
    run_id: str
    published_run_dir: Path
    manifest_path: Path
    frame_count: int


def publish_wpc_bundle(
    *,
    data_root: Path,
    issue_time: datetime,
    frames_by_var: dict[str, list[WPCSourceField]],
) -> WPCPublishResult:
    if not frames_by_var:
        raise ValueError("WPC publish requires at least one variable with frames")

    if grid_build_enabled():
        _require_grid_support()

    run_id = format_run_id(issue_time.astimezone(timezone.utc), include_minutes=True)
    _prepare_stage_run_dir(data_root=data_root, run_id=run_id)

    targets: list[tuple[str, int]] = []
    latest_valid_time: datetime | None = None
    for var_id, frames in sorted(frames_by_var.items(), key=lambda item: item[0]):
        for frame in sorted(frames, key=lambda item: item.forecast_hour):
            if not _write_wpc_frame(
                data_root=data_root,
                run_id=run_id,
                var_id=var_id,
                frame=frame,
            ):
                continue
            targets.append((var_id, int(frame.forecast_hour)))
            frame_valid_time = frame.valid_time.astimezone(timezone.utc)
            latest_valid_time = frame_valid_time if latest_valid_time is None else max(latest_valid_time, frame_valid_time)

    if not targets:
        raise ValueError("WPC publish requires at least one frame")

    if grid_build_enabled():
        _require_grid_support()
        build_grid_manifests_for_run_root(
            run_root=data_root / "staging" / WPC_MODEL_ID / run_id,
            model=WPC_MODEL_ID,
            run=run_id,
            variables=tuple(sorted(frames_by_var.keys())),
        )

    promote_run(data_root=data_root, model=WPC_MODEL_ID, run_id=run_id)
    metadata = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issue_time": issue_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_valid_time": latest_valid_time.strftime("%Y-%m-%dT%H:%M:%SZ") if latest_valid_time is not None else None,
        "source": WPC_PUBLISH_SOURCE,
        "accumulation_mode": "cumulative",
    }
    write_run_manifest(
        data_root=data_root,
        model=WPC_MODEL_ID,
        run_id=run_id,
        targets=targets,
        plugin=WPC_MODEL,
        metadata=metadata,
    )
    write_latest_pointer(data_root=data_root, model=WPC_MODEL_ID, run_id=run_id, source="wpc_publish_v1")

    manifest_path = data_root / "manifests" / WPC_MODEL_ID / f"{run_id}.json"
    published_run_dir = data_root / "published" / WPC_MODEL_ID / run_id
    return WPCPublishResult(
        run_id=run_id,
        published_run_dir=published_run_dir,
        manifest_path=manifest_path,
        frame_count=len(targets),
    )


def _prepare_stage_run_dir(*, data_root: Path, run_id: str) -> None:
    stage_run_dir = data_root / "staging" / WPC_MODEL_ID / run_id
    if stage_run_dir.exists():
        shutil.rmtree(stage_run_dir)
    stage_run_dir.mkdir(parents=True, exist_ok=True)


def _write_wpc_frame(
    *,
    data_root: Path,
    run_id: str,
    var_id: str,
    frame: WPCSourceField,
) -> bool:
    """Write one frame's artifacts. Returns False when the enforced pre-encode
    gate rejected the frame (nothing written), mirroring how build_frame
    signals a failed frame to the scheduler via status rather than raising."""
    fh = int(frame.forecast_hour)
    fh_str = f"fh{fh:03d}"
    staging_dir = data_root / "staging" / WPC_MODEL_ID / run_id / var_id
    staging_dir.mkdir(parents=True, exist_ok=True)

    value_path = staging_dir / f"{fh_str}.val.cog.tif"
    sidecar_path = staging_dir / f"{fh_str}.json"
    values, dst_transform = _warp_frame_to_target_grid(frame)
    var_capability = WPC_MODEL.get_var_capability(var_id)
    if var_capability is None or not var_capability.color_map_id:
        raise ValueError(f"Missing WPC color map registration for {var_id}")
    var_spec_colormap = get_color_map_spec(str(var_capability.color_map_id))
    var_spec_model = WPC_MODEL.get_var(var_id)
    # Pre-encode gate (COG->binary sampling migration): the check itself runs
    # on every frame. For a binary-sampling model (the default; a
    # CARTOSKY_COG_SAMPLING_MODELS opt-out disables it) it is ENFORCED —
    # failure (or a gate error) rejects the frame before ANY artifact is
    # written, matching pipeline.py's binary_only branch. Otherwise it stays
    # the Phase C shadow gate: log-only, frame governed by the COG path.
    binary_only = binary_sampling_enabled(WPC_MODEL_ID)
    if check_pre_encode_value_sanity is not None:
        try:
            gate_ok = check_pre_encode_value_sanity(
                values,
                var_spec_colormap,
                var_spec_model=var_spec_model,
                var_capability=var_capability,
                label=f"{WPC_MODEL_ID}/{var_id}/fh{fh:03d}",
            )
        except Exception:
            if binary_only:
                logger.exception(
                    "Pre-encode sanity gate errored — rejecting frame "
                    "model=%s var=%s fh%03d — frame not published",
                    WPC_MODEL_ID,
                    var_id,
                    fh,
                )
                return False
            logger.exception(
                "Phase C shadow gate errored: pre-encode value sanity "
                "model=%s var=%s fh%03d; frame remains governed by existing COG gates",
                WPC_MODEL_ID,
                var_id,
                fh,
            )
            gate_ok = True
        if not gate_ok:
            if binary_only:
                logger.error(
                    "Pre-encode sanity gate rejected frame model=%s var=%s "
                    "fh%03d — frame not published",
                    WPC_MODEL_ID,
                    var_id,
                    fh,
                )
                return False
            logger.warning(
                "Phase C shadow gate failed: pre-encode value sanity "
                "model=%s var=%s fh%03d; frame remains governed by existing COG gates",
                WPC_MODEL_ID,
                var_id,
                fh,
            )
    _, colorize_meta = float_to_rgba(values, str(var_capability.color_map_id), meta_var_key=var_id)
    if binary_only:
        # Value COG retired for binary-sampling models: the grid binary
        # (written below) serves rendering and sampling, and the enforced
        # gate above already applied the value-quality gate.
        logger.info(
            "Value COG write skipped (model=%s is binary-only)",
            WPC_MODEL_ID,
        )
    else:
        write_value_cog(values, value_path, model=WPC_MODEL_ID, region=WPC_REGION_ID)

    sidecar = _build_sidecar_json(
        model=WPC_MODEL_ID,
        region=WPC_REGION_ID,
        run_id=run_id,
        var_id=var_id,
        fh=fh,
        run_date=frame.issue_time.astimezone(timezone.utc),
        colorize_meta=colorize_meta,
        var_spec=var_spec_colormap,
        var_spec_model=var_spec_model,
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
            run_root=data_root / "staging" / WPC_MODEL_ID / run_id,
            model=WPC_MODEL_ID,
            var=var_id,
            fh=fh,
            values=values,
            transform=dst_transform,
            projection="EPSG:3857",
        )
    return True


def _warp_frame_to_target_grid(frame: WPCSourceField) -> tuple[np.ndarray, Any]:
    values = np.asarray(frame.values, dtype=np.float32)
    warped_values, dst_transform = warp_to_target_grid(
        values,
        frame.crs,
        frame.transform,
        model=WPC_MODEL_ID,
        region=WPC_REGION_ID,
        resampling="bilinear",
        src_nodata=None,
        dst_nodata=float("nan"),
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