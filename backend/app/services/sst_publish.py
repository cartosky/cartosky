"""SST bundle publish — rolling 14-day daily observed bundles, °C only.

Shape, borrowed from ``mrms_publish``: every publish is a *run* that bundles
the last :data:`SST_BUNDLE_FRAME_COUNT` daily frames as ``fh000`` (oldest) …
``fh013`` (newest), each carrying its own ``valid_time`` in the manifest and
sidecar. A daily republish re-fetches only the new day; the other thirteen
frames are carried forward by hardlinking the previous run's grid artifacts
(``_reuse_grid_artifacts``, the same technique as ``reuse_mrms_frame``), so the
history is never re-fetched, re-warped or re-encoded.

Unlike MRMS this publishes into **two domains** from one source read:

* ``na`` — the canonical domain, 9 km EPSG:3857 (1825x1893);
* ``global`` — a non-canonical domain tree, native 0.25° EPSG:4326 (721x1440),
  written under ``published/sst/domains/global/{run_id}/`` exactly as
  ``scheduler._publish_domain_locked`` lays out a domain publish. It is only
  attempted when ``CARTOSKY_GLOBAL_DOMAIN_MODELS`` names ``sst``
  (``domains.declared_domains_for_var`` is the single gate).

Both domains share one run id, so the two trees stay in lockstep.

Coastlines are handled in two halves, because the grid alone cannot draw one:

* **here**, each frame's ocean edge is dilated inland with
  ``sst_fetch.fill_coastal_fringe`` before the warp, by a per-domain reach
  (:data:`SST_COASTAL_FRINGE_CELLS_BY_DOMAIN`) — a bilinear warp onto a coarser
  grid otherwise *retreats* from the coast by up to a full target cell;
* **in the viewer**, ``display_prep.clip_to_water`` (registered for ``sst`` in
  ``grid_display_prep``) makes the map draw a vector land-polygon mask directly
  above the grid layer, so the visible data edge is the real coastline instead of
  the grid's texel staircase, and the deliberate under-land bleed stays hidden.

Consequences: warped valid fractions run well above the raw source, and sampling
inshore returns a neighbour-weighted nearest-ocean value rather than nothing.
"""

from __future__ import annotations

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
from rasterio.transform import Affine

from ..config import grid_build_enabled
from ..models.sst import (
    SST_CANONICAL_REGION_ID,
    SST_COLOR_MAP_ID,
    SST_GLOBAL_REGION_ID,
    SST_MODEL,
    SST_MODEL_ID,
    SST_VARIABLE_ID,
)
from .builder.colorize import colorize_metadata
from .builder.pipeline import build_sidecar_json, check_pre_encode_value_sanity
from .builder.raster_grid import get_target_grid, warp_to_target_grid
from .colormaps import get_color_map_spec
from .domains import declared_domains_for_var, model_root_for_domain
from .grid import (
    build_grid_manifests_for_run_root,
    grid_dir_for_run_root,
    resolved_grid_dir_for_run_root,
    write_grid_frames_for_run_root,
)
from .observed_bundle_health import build_observed_bundle_health
from .publish_utils import write_json_atomic
from .sst_fetch import fill_coastal_fringe
from .run_ids import format_run_id

logger = logging.getLogger(__name__)

#: How many daily frames one published run bundles (fh000 oldest … fhNNN newest).
SST_BUNDLE_FRAME_COUNT = 14

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class SSTBundleFrame:
    """One freshly fetched day, on its native EPSG:4326 grid in °C."""

    valid_time: datetime
    values: np.ndarray
    source_transform: Affine
    source_crs: Any = "EPSG:4326"
    source_url: str | None = None
    source_filename: str | None = None
    quality: str = "full"
    quality_flags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SSTPublishedFrame:
    """A day already published in a previous run, reusable by hardlink."""

    valid_time: datetime
    sidecar: dict[str, Any]
    sidecar_path: Path


@dataclass(frozen=True)
class SSTPublishResult:
    run_id: str
    frame_counts: dict[str, int]
    manifest_paths: dict[str, Path]
    published_run_dirs: dict[str, Path]

    @property
    def frame_count(self) -> int:
        """Frames published for the canonical domain."""
        return int(self.frame_counts.get(SST_CANONICAL_REGION_ID, 0))


# ---------------------------------------------------------------------------
# Domain-scoped path helpers (mirroring scheduler.py's layout exactly)
# ---------------------------------------------------------------------------

def staging_model_root(data_root: Path, domain: str) -> Path:
    return model_root_for_domain(Path(data_root) / "staging", SST_MODEL_ID, domain)


def staging_run_root(data_root: Path, run_id: str, domain: str) -> Path:
    return staging_model_root(data_root, domain) / run_id


def published_model_root(data_root: Path, domain: str) -> Path:
    return model_root_for_domain(Path(data_root) / "published", SST_MODEL_ID, domain)


def published_run_root(data_root: Path, run_id: str, domain: str) -> Path:
    return published_model_root(data_root, domain) / run_id


def manifest_root(data_root: Path, domain: str) -> Path:
    return model_root_for_domain(Path(data_root) / "manifests", SST_MODEL_ID, domain)


def manifest_path(data_root: Path, run_id: str, domain: str) -> Path:
    return manifest_root(data_root, domain) / f"{run_id}.json"


def latest_pointer_path(data_root: Path, domain: str) -> Path:
    return published_model_root(data_root, domain) / "LATEST.json"


def publish_domains() -> tuple[str, ...]:
    """Domains this publish covers — canonical always, ``global`` when enabled.

    Delegates entirely to :func:`domains.declared_domains_for_var`, so the
    ``CARTOSKY_GLOBAL_DOMAIN_MODELS`` gate is read in exactly one place and a
    deploy without the flag publishes the canonical tree only.
    """
    return declared_domains_for_var(SST_MODEL, SST_VARIABLE_ID)


# ---------------------------------------------------------------------------
# Reading the previous run
# ---------------------------------------------------------------------------

def load_latest_published_sst_frames(
    data_root: Path, domain: str
) -> tuple[str | None, list[SSTPublishedFrame]]:
    """The previous run's reusable frames for one domain, oldest first."""
    latest_path = latest_pointer_path(data_root, domain)
    if not latest_path.is_file():
        return None, []
    try:
        latest_payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, []

    run_id = str(latest_payload.get("run_id") or "").strip()
    if not run_id:
        return None, []

    path = manifest_path(data_root, run_id, domain)
    if not path.is_file():
        return run_id, []
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return run_id, []

    var_entry = manifest.get("variables", {}).get(SST_VARIABLE_ID)
    manifest_frames = var_entry.get("frames") if isinstance(var_entry, dict) else None
    if not isinstance(manifest_frames, list):
        return run_id, []

    run_root = published_run_root(data_root, run_id, domain)
    var_dir = run_root / SST_VARIABLE_ID
    frames: list[SSTPublishedFrame] = []
    for entry in manifest_frames:
        if not isinstance(entry, dict):
            continue
        try:
            fh = int(entry["fh"])
        except (KeyError, TypeError, ValueError):
            continue
        sidecar_path = var_dir / f"fh{fh:03d}.json"
        if not sidecar_path.is_file():
            continue
        if not _published_grid_meta_exists(run_root, SST_VARIABLE_ID, fh):
            continue
        try:
            sidecar = json.loads(sidecar_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        raw_valid_time = sidecar.get("valid_time") or entry.get("valid_time")
        valid_time = _parse_time(raw_valid_time)
        if valid_time is None:
            continue
        frames.append(
            SSTPublishedFrame(valid_time=valid_time, sidecar=sidecar, sidecar_path=sidecar_path)
        )

    frames.sort(key=lambda item: item.valid_time)
    return run_id, frames


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def publish_sst_bundle(
    *,
    data_root: Path,
    frames: list[SSTBundleFrame],
    publish_time: datetime | None = None,
    target_frame_count: int = SST_BUNDLE_FRAME_COUNT,
    domains: tuple[str, ...] | None = None,
) -> SSTPublishResult:
    """Publish one run bundling the newest ``target_frame_count`` daily frames.

    ``frames`` are the freshly fetched days; every other frame in the window is
    carried forward from the previous run of the same domain.

    ``domains`` defaults to every enabled domain. Callers may pass a **subset**,
    including one that omits the canonical domain — that is how the poller
    backfills a domain that was switched on later (e.g. ``global`` after a
    ``CARTOSKY_GLOBAL_DOMAIN_MODELS`` flip) without rewriting canonical runs that
    are already correct. Failure handling follows from what was requested: the
    canonical domain raises when it was asked for, and every non-canonical domain
    is logged-and-skipped, so a global-grid problem can never take SST offline.

    The run id is the **newest fetched day's valid time** (e.g. ``20260804_12z``),
    not the wall clock: SST is a daily product, so that makes a republish of the
    same day idempotent and lets the poller walk a first-ever backfill day by
    day without minting colliding run ids.

    Days must be published **oldest first**: a run's window is the previous
    run's window merged with the fresh frame, so publishing an older day after a
    newer one would mint a run whose id trails its own newest frame.
    """
    if not frames:
        raise ValueError("SST bundle publish requires at least one fresh frame")

    resolved_domains = tuple(domains) if domains is not None else publish_domains()
    if not resolved_domains:
        raise ValueError("SST publish requires at least one target domain")

    publish_dt = (publish_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    newest_valid_time = max(frame.valid_time.astimezone(timezone.utc) for frame in frames)
    run_id = format_run_id(newest_valid_time)
    started_at = time.monotonic()
    logger.info(
        "SST publish phase=start run=%s fresh_frames=%d domains=%s",
        run_id,
        len(frames),
        ",".join(resolved_domains),
    )

    frame_counts: dict[str, int] = {}
    manifest_paths: dict[str, Path] = {}
    published_run_dirs: dict[str, Path] = {}
    failed_domains: list[str] = []

    for domain in resolved_domains:
        try:
            count = _publish_domain(
                data_root=Path(data_root),
                run_id=run_id,
                domain=domain,
                frames=frames,
                publish_dt=publish_dt,
                target_frame_count=target_frame_count,
            )
        except Exception:
            if domain == SST_CANONICAL_REGION_ID:
                raise
            logger.exception(
                "SST domain publish failed run=%s domain=%s — other domains unaffected",
                run_id,
                domain,
            )
            failed_domains.append(domain)
            continue
        frame_counts[domain] = count
        manifest_paths[domain] = manifest_path(data_root, run_id, domain)
        published_run_dirs[domain] = published_run_root(data_root, run_id, domain)

    # Total failure must be loud. Per-domain log-and-skip exists so ONE bad
    # domain cannot take the others offline — but when it swallows every
    # requested domain, nothing reached disk and a normal return would report a
    # published run that does not exist. That is only reachable for a
    # canonical-free domain set (the poller's backfill path); with the canonical
    # domain requested, its own failure has already raised above.
    if not frame_counts:
        raise RuntimeError(
            f"SST publish wrote nothing for run={run_id}: every requested domain failed "
            f"({', '.join(failed_domains) or ', '.join(resolved_domains)})"
        )

    logger.info(
        "SST publish phase=complete run=%s elapsed=%.1fs frames=%s",
        run_id,
        time.monotonic() - started_at,
        frame_counts,
    )
    return SSTPublishResult(
        run_id=run_id,
        frame_counts=frame_counts,
        manifest_paths=manifest_paths,
        published_run_dirs=published_run_dirs,
    )


def _publish_domain(
    *,
    data_root: Path,
    run_id: str,
    domain: str,
    frames: list[SSTBundleFrame],
    publish_dt: datetime,
    target_frame_count: int,
) -> int:
    previous_run_id, previous_frames = load_latest_published_sst_frames(data_root, domain)

    merged: dict[datetime, SSTPublishedFrame | SSTBundleFrame] = {}
    for frame in previous_frames:
        merged[frame.valid_time] = frame
    for frame in frames:
        merged[frame.valid_time.astimezone(timezone.utc)] = frame

    ordered = [merged[key] for key in sorted(merged)]
    if target_frame_count > 0:
        ordered = ordered[-int(target_frame_count):]
    if not ordered:
        raise ValueError("SST bundle publish resolved to an empty rolling window")

    stage_run = staging_run_root(data_root, run_id, domain)
    if stage_run.exists():
        shutil.rmtree(stage_run, ignore_errors=True)
    stage_run.mkdir(parents=True, exist_ok=True)

    build_artifacts = bool(grid_build_enabled())
    published_fhs: list[int] = []
    valid_times_by_fh: dict[int, datetime] = {}
    for fh, item in enumerate(ordered):
        if isinstance(item, SSTPublishedFrame):
            wrote = _reuse_frame(
                data_root=data_root,
                run_id=run_id,
                domain=domain,
                forecast_hour=fh,
                frame=item,
                build_grid_artifacts=build_artifacts,
            )
            valid_time = item.valid_time
        else:
            wrote = _write_frame(
                data_root=data_root,
                run_id=run_id,
                domain=domain,
                forecast_hour=fh,
                frame=item,
                build_grid_artifacts=build_artifacts,
            )
            valid_time = item.valid_time.astimezone(timezone.utc)
        if not wrote:
            continue
        published_fhs.append(fh)
        valid_times_by_fh[fh] = valid_time

    if not published_fhs:
        raise ValueError(f"SST bundle publish wrote no frames for domain={domain}")

    if build_artifacts:
        try:
            manifests_built = build_grid_manifests_for_run_root(
                run_root=stage_run,
                model=SST_MODEL_ID,
                run=run_id,
                variables=(SST_VARIABLE_ID,),
            )
            logger.info(
                "SST grid manifest build run=%s domain=%s manifests=%d",
                run_id,
                domain,
                manifests_built,
            )
        except Exception:
            logger.exception("SST grid manifest build failed run=%s domain=%s", run_id, domain)

    _promote_run(data_root=data_root, run_id=run_id, domain=domain)

    # Units/kind come from a published sidecar so the manifest carries the same
    # display units the frontend reads there ("°C", via pipeline._format_units)
    # instead of a second hardcoded spelling.
    sidecar_units, sidecar_kind = _units_and_kind_from_published_sidecar(
        data_root=data_root, run_id=run_id, domain=domain, fh=published_fhs[0]
    )
    manifest_variables = {
        SST_VARIABLE_ID: {
            "display_name": "Sea Surface Temperature",
            "kind": sidecar_kind,
            "units": sidecar_units,
            # The window's real size, so a frame the pre-encode gate rejected
            # shows up as available < expected instead of being papered over.
            "expected_frames": len(ordered),
            "available_frames": len(published_fhs),
            "frames": [
                {"fh": fh, "valid_time": valid_times_by_fh[fh].strftime(_TIME_FORMAT)}
                for fh in published_fhs
            ],
        }
    }
    last_updated = publish_dt.strftime(_TIME_FORMAT)
    payload = {
        "contract_version": "3.0",
        "model": SST_MODEL_ID,
        "run": run_id,
        "region": domain,
        "variables": manifest_variables,
        "last_updated": last_updated,
        "metadata": build_observed_bundle_health(
            latest_run=run_id,
            manifest={"last_updated": last_updated, "variables": manifest_variables},
            source=SST_MODEL_ID,
            now_utc=publish_dt,
        ),
    }
    write_json_atomic(manifest_path(data_root, run_id, domain), payload)

    write_json_atomic(
        latest_pointer_path(data_root, domain),
        {
            "run_id": run_id,
            "cycle_utc": publish_dt.strftime(_TIME_FORMAT),
            "updated_utc": datetime.now(timezone.utc).strftime(_TIME_FORMAT),
            "source": "sst_publish_v1",
            "region": domain,
        },
    )
    logger.info(
        "SST publish phase=domain_complete run=%s domain=%s frames=%d reused_from=%s",
        run_id,
        domain,
        len(published_fhs),
        previous_run_id or "-",
    )
    return len(published_fhs)


# ---------------------------------------------------------------------------
# Frame writes
# ---------------------------------------------------------------------------

#: Coastal fringe reach in **native 0.05° source cells** (~5.5 km each), per
#: target domain. The dilation has to cover at least one target cell or the
#: bilinear warp retreats from the coast and the data edge renders as a texel
#: staircase; going comfortably past one cell also lets the viewer's vector land
#: mask (``display_prep.clip_to_water``) trim a clean coastline out of it instead
#: of exposing a ragged one.
#:
#:   na     — 9 km target cell  (~1.6 source cells) -> 6 cells ~= 33 km
#:   global — 0.25° target cell (~5   source cells) -> 8 cells ~= 44 km
#:
#: Everything past the coastline is hidden by the mask, so these are sized for
#: coverage, not for restraint — but they are still bounded, so inland NaN and
#: enclosed masked water (the Great Lakes) stay empty.
SST_COASTAL_FRINGE_CELLS_BY_DOMAIN: dict[str, int] = {
    SST_CANONICAL_REGION_ID: 6,
    SST_GLOBAL_REGION_ID: 8,
}


def coastal_fringe_cells_for_domain(domain: str) -> int:
    """Fringe reach for a domain, defaulting to the canonical domain's value."""
    return int(
        SST_COASTAL_FRINGE_CELLS_BY_DOMAIN.get(
            str(domain).strip().lower(),
            SST_COASTAL_FRINGE_CELLS_BY_DOMAIN[SST_CANONICAL_REGION_ID],
        )
    )


def _write_frame(
    *,
    data_root: Path,
    run_id: str,
    domain: str,
    forecast_hour: int,
    frame: SSTBundleFrame,
    build_grid_artifacts: bool,
) -> bool:
    grid = get_target_grid(SST_MODEL_ID, domain)

    # Dilate the ocean edge into the land NaN fringe BEFORE the warp, sized for
    # this domain's target cell. Done per domain (not once at read time) because
    # the two grids need different reaches.
    source_values = np.asarray(frame.values, dtype=np.float32)
    source_valid_fraction = float(np.isfinite(source_values).mean())
    fringe_cells = coastal_fringe_cells_for_domain(domain)
    source_values = fill_coastal_fringe(source_values, radius_cells=fringe_cells)
    filled_valid_fraction = float(np.isfinite(source_values).mean())

    values, _ = warp_to_target_grid(
        source_values,
        frame.source_crs,
        frame.source_transform,
        model=SST_MODEL_ID,
        region=domain,
        resampling="bilinear",
        src_nodata=float("nan"),
        working_dtype=np.float32,
    )
    values = np.asarray(values, dtype=np.float32)
    if values.shape != (grid.height, grid.width):
        raise ValueError(
            f"SST warp produced {values.shape}, expected {(grid.height, grid.width)} "
            f"for domain={domain}"
        )

    if not _pre_encode_gate_allows(values, forecast_hour=forecast_hour, domain=domain):
        return False

    stage_var_dir = staging_run_root(data_root, run_id, domain) / SST_VARIABLE_ID
    stage_var_dir.mkdir(parents=True, exist_ok=True)

    colorize_meta = colorize_metadata(values, SST_COLOR_MAP_ID, meta_var_key=SST_VARIABLE_ID)
    valid_time = frame.valid_time.astimezone(timezone.utc)
    sidecar = build_sidecar_json(
        model=SST_MODEL_ID,
        region=domain,
        run_id=run_id,
        var_id=SST_VARIABLE_ID,
        fh=int(forecast_hour),
        run_date=datetime.now(timezone.utc),
        colorize_meta=colorize_meta,
        var_spec={"type": "continuous", "units": "C"},
        var_spec_model=SST_MODEL.get_var(SST_VARIABLE_ID),
        value_downsample_factor=1,
        quality=frame.quality,
        quality_flags=list(frame.quality_flags),
        valid_time_override=valid_time,
    )
    if frame.source_url:
        sidecar["source_url"] = frame.source_url
    if frame.source_filename:
        sidecar["source_filename"] = frame.source_filename
    source_metadata = dict(frame.metadata) if frame.metadata else {}
    source_metadata["actual_valid_time"] = valid_time.strftime(_TIME_FORMAT)
    # Per-domain coastal dilation, recorded so it is auditable from a published
    # sidecar rather than inferred from the ramp.
    source_metadata["coastal_fringe_source_cells"] = fringe_cells
    source_metadata["source_valid_fraction"] = round(source_valid_fraction, 6)
    source_metadata["fringe_filled_valid_fraction"] = round(filled_valid_fraction, 6)
    source_metadata["warped_valid_fraction"] = round(float(np.isfinite(values).mean()), 6)
    sidecar["source_metadata"] = source_metadata
    write_json_atomic(stage_var_dir / f"fh{int(forecast_hour):03d}.json", sidecar)

    if build_grid_artifacts:
        write_grid_frames_for_run_root(
            run_root=staging_run_root(data_root, run_id, domain),
            model=SST_MODEL_ID,
            var=SST_VARIABLE_ID,
            fh=int(forecast_hour),
            values=values,
            transform=grid.transform,
            projection=grid.crs,
        )
    return True


def _reuse_frame(
    *,
    data_root: Path,
    run_id: str,
    domain: str,
    forecast_hour: int,
    frame: SSTPublishedFrame,
    build_grid_artifacts: bool,
) -> bool:
    """Carry a previously published day forward by hardlinking its artifacts."""
    if build_grid_artifacts and not _reuse_grid_artifacts(
        data_root=data_root,
        run_id=run_id,
        domain=domain,
        forecast_hour=int(forecast_hour),
        source_value_path=frame.sidecar_path,
    ):
        logger.warning(
            "Skipping SST reuse: no grid artifacts domain=%s fh%03d source=%s",
            domain,
            int(forecast_hour),
            frame.sidecar_path,
        )
        return False

    stage_var_dir = staging_run_root(data_root, run_id, domain) / SST_VARIABLE_ID
    stage_var_dir.mkdir(parents=True, exist_ok=True)

    sidecar = dict(frame.sidecar)
    sidecar["run"] = run_id
    sidecar["fh"] = int(forecast_hour)
    sidecar["valid_time"] = frame.valid_time.strftime(_TIME_FORMAT)
    source_metadata = dict(sidecar.get("source_metadata") or {})
    source_metadata["actual_valid_time"] = frame.valid_time.strftime(_TIME_FORMAT)
    sidecar["source_metadata"] = source_metadata
    write_json_atomic(stage_var_dir / f"fh{int(forecast_hour):03d}.json", sidecar)
    return True


def _reuse_grid_artifacts(
    *,
    data_root: Path,
    run_id: str,
    domain: str,
    forecast_hour: int,
    source_value_path: Path,
) -> bool:
    source_fh = _forecast_hour_from_artifact_name(source_value_path)
    if source_fh is None:
        return False

    source_run_root = source_value_path.parent.parent
    source_grid_dir = resolved_grid_dir_for_run_root(source_run_root, SST_VARIABLE_ID)
    if not source_grid_dir.is_dir():
        return False

    target_grid_dir = grid_dir_for_run_root(
        staging_run_root(data_root, run_id, domain), SST_VARIABLE_ID
    )
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
        meta["run"] = run_id
        filename = str(meta.get("file") or "").strip()
        if filename:
            meta["file"] = filename.replace(source_token, target_token, 1)
        retargeted_meta.append((target_meta_path, meta))

    for source_bin in source_bins:
        target_bin = target_grid_dir / source_bin.name.replace(source_token, target_token, 1)
        _link_or_copy(source_bin, target_bin)
        for suffix in (".gz", ".br"):
            sidecar = source_bin.with_name(f"{source_bin.name}{suffix}")
            if sidecar.is_file():
                _link_or_copy(sidecar, target_bin.with_name(f"{target_bin.name}{suffix}"))

    for target_meta_path, meta in retargeted_meta:
        write_json_atomic(target_meta_path, meta)
    return True


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _pre_encode_gate_allows(values: np.ndarray, *, forecast_hour: int, domain: str) -> bool:
    label = f"{SST_MODEL_ID}/{SST_VARIABLE_ID}/{domain}/fh{int(forecast_hour):03d}"
    try:
        gate_ok = check_pre_encode_value_sanity(
            values,
            get_color_map_spec(SST_COLOR_MAP_ID),
            var_spec_model=SST_MODEL.get_var(SST_VARIABLE_ID),
            var_capability=SST_MODEL.get_var_capability(SST_VARIABLE_ID),
            label=label,
        )
    except Exception:
        logger.exception("SST pre-encode sanity gate errored — rejecting frame %s", label)
        return False
    if not gate_ok:
        logger.error("SST pre-encode sanity gate rejected frame %s — frame not published", label)
        return False
    return True


def _promote_run(*, data_root: Path, run_id: str, domain: str) -> None:
    stage_run = staging_run_root(data_root, run_id, domain)
    if not stage_run.is_dir():
        raise ValueError(f"Cannot promote missing SST staging run dir: {stage_run}")

    published_model = published_model_root(data_root, domain)
    published_model.mkdir(parents=True, exist_ok=True)
    published_run = published_model / run_id
    tmp_run = published_model / f".{run_id}.tmp"

    if tmp_run.exists():
        shutil.rmtree(tmp_run, ignore_errors=True)
    shutil.copytree(stage_run, tmp_run, copy_function=os.link)
    if published_run.exists():
        shutil.rmtree(published_run, ignore_errors=True)
    shutil.move(str(tmp_run), str(published_run))


def _published_grid_meta_exists(run_root: Path, var_id: str, fh: int) -> bool:
    grid_dir = resolved_grid_dir_for_run_root(run_root, var_id)
    return (grid_dir / f"fh{int(fh):03d}.l0.meta.json").is_file()


def _forecast_hour_from_artifact_name(path: Path) -> int | None:
    token = Path(path).name.split(".", 1)[0]
    if not token.startswith("fh"):
        return None
    try:
        return int(token.removeprefix("fh"))
    except ValueError:
        return None


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _units_and_kind_from_published_sidecar(
    *, data_root: Path, run_id: str, domain: str, fh: int
) -> tuple[str, str]:
    sidecar_path = (
        published_run_root(data_root, run_id, domain) / SST_VARIABLE_ID / f"fh{int(fh):03d}.json"
    )
    try:
        sidecar = json.loads(sidecar_path.read_text())
    except (OSError, json.JSONDecodeError):
        return "°C", "continuous"
    units = str(sidecar.get("units") or "").strip() or "°C"
    kind = str(sidecar.get("kind") or "").strip() or "continuous"
    return units, kind


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.strptime(raw.strip(), _TIME_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


__all__ = [
    "SST_BUNDLE_FRAME_COUNT",
    "SST_CANONICAL_REGION_ID",
    "SST_GLOBAL_REGION_ID",
    "SSTBundleFrame",
    "SSTPublishResult",
    "SSTPublishedFrame",
    "load_latest_published_sst_frames",
    "publish_domains",
    "publish_sst_bundle",
]
