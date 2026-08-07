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
    SST_ANOM_COLOR_MAP_ID,
    SST_ANOM_VARIABLE_ID,
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


#: Variables a run can carry, primary first. ``sst`` is the layer's reason to
#: exist; ``sst_anom`` is optional per day because CRW publishes it on its own
#: schedule and may lag the absolute field.
SST_PUBLISH_VARIABLES: tuple[str, ...] = (SST_VARIABLE_ID, SST_ANOM_VARIABLE_ID)

_COLOR_MAP_BY_VARIABLE: dict[str, str] = {
    SST_VARIABLE_ID: SST_COLOR_MAP_ID,
    SST_ANOM_VARIABLE_ID: SST_ANOM_COLOR_MAP_ID,
}

_DISPLAY_NAME_BY_VARIABLE: dict[str, str] = {
    SST_VARIABLE_ID: "Sea Surface Temperature",
    SST_ANOM_VARIABLE_ID: "Sea Surface Temperature Anomaly",
}


@dataclass(frozen=True)
class SSTBundleFrame:
    """One freshly fetched day, on its native EPSG:4326 grid in °C.

    A frame carries whichever variables were newly fetched for that day, so it is
    legal (and normal) for a frame to carry only the anomaly: that is the
    "anomaly arrived late" case, where the day's absolute SST is already published
    and gets carried forward by hardlink instead of being re-fetched.
    """

    valid_time: datetime
    source_transform: Affine
    values: np.ndarray | None = None
    anomaly_values: np.ndarray | None = None
    source_crs: Any = "EPSG:4326"
    source_url: str | None = None
    source_filename: str | None = None
    anomaly_source_url: str | None = None
    anomaly_source_filename: str | None = None
    quality: str = "full"
    quality_flags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    anomaly_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.values is None and self.anomaly_values is None:
            raise ValueError(
                "SSTBundleFrame carries no field: supply values, anomaly_values, or both"
            )

    def field_for(self, var_id: str) -> np.ndarray | None:
        return self.values if var_id == SST_VARIABLE_ID else self.anomaly_values

    def source_url_for(self, var_id: str) -> str | None:
        return self.source_url if var_id == SST_VARIABLE_ID else self.anomaly_source_url

    def source_filename_for(self, var_id: str) -> str | None:
        return (
            self.source_filename if var_id == SST_VARIABLE_ID else self.anomaly_source_filename
        )

    def metadata_for(self, var_id: str) -> dict[str, Any]:
        return dict(self.metadata if var_id == SST_VARIABLE_ID else self.anomaly_metadata)


@dataclass(frozen=True)
class SSTPublishedFrame:
    """A day already published in a previous run, reusable by hardlink.

    Per-variable, because a day can legitimately have `sst` published and
    `sst_anom` still missing.
    """

    valid_time: datetime
    sidecars: dict[str, dict[str, Any]] = field(default_factory=dict)
    sidecar_paths: dict[str, Path] = field(default_factory=dict)

    def has(self, var_id: str) -> bool:
        return var_id in self.sidecar_paths


@dataclass(frozen=True)
class SSTPublishResult:
    run_id: str
    frame_counts: dict[str, int]
    manifest_paths: dict[str, Path]
    published_run_dirs: dict[str, Path]
    #: domain -> {var_id: frames published}. ``frame_counts`` stays the primary
    #: variable's count so the total-failure (D5) check keeps its meaning.
    variable_frame_counts: dict[str, dict[str, int]] = field(default_factory=dict)

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

    run_root = published_run_root(data_root, run_id, domain)
    manifest_variables = manifest.get("variables")
    if not isinstance(manifest_variables, dict):
        return run_id, []

    # Collect per (valid_time, variable): a day is admitted for a variable only
    # when both its sidecar and its level-0 grid meta exist, so a half-written
    # variable is never carried forward as if it were complete.
    by_valid_time: dict[datetime, dict[str, tuple[dict[str, Any], Path]]] = {}
    for var_id in SST_PUBLISH_VARIABLES:
        var_entry = manifest_variables.get(var_id)
        manifest_frames = var_entry.get("frames") if isinstance(var_entry, dict) else None
        if not isinstance(manifest_frames, list):
            continue
        var_dir = run_root / var_id
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
            if not _published_grid_meta_exists(run_root, var_id, fh):
                continue
            try:
                sidecar = json.loads(sidecar_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            valid_time = _parse_time(sidecar.get("valid_time") or entry.get("valid_time"))
            if valid_time is None:
                continue
            by_valid_time.setdefault(valid_time, {})[var_id] = (sidecar, sidecar_path)

    frames = [
        SSTPublishedFrame(
            valid_time=valid_time,
            sidecars={var_id: pair[0] for var_id, pair in per_var.items()},
            sidecar_paths={var_id: pair[1] for var_id, pair in per_var.items()},
        )
        for valid_time, per_var in sorted(by_valid_time.items())
    ]
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
    run_valid_time: datetime | None = None,
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

    The run id defaults to the **newest fetched day's valid time**
    (e.g. ``20260804_12z``), not the wall clock: SST is a daily product, so that
    makes a republish of the same day idempotent and lets the poller walk a
    first-ever backfill day by day without minting colliding run ids.

    ``run_valid_time`` overrides that when the caller is *amending* an existing
    run rather than advancing to a new day — specifically the anomaly catch-up
    path, where the fresh frames are older days whose absolute SST is already
    published. Without the override those frames would mint a run id that trails
    the window's own newest frame.

    Days must be published **oldest first**: a run's window is the previous
    run's window merged with the fresh frames, so publishing an older day after a
    newer one would mint a run whose id trails its own newest frame.
    """
    if not frames:
        raise ValueError("SST bundle publish requires at least one fresh frame")

    resolved_domains = tuple(domains) if domains is not None else publish_domains()
    if not resolved_domains:
        raise ValueError("SST publish requires at least one target domain")

    publish_dt = (publish_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    newest_valid_time = (
        run_valid_time.astimezone(timezone.utc)
        if run_valid_time is not None
        else max(frame.valid_time.astimezone(timezone.utc) for frame in frames)
    )
    run_id = format_run_id(newest_valid_time)
    started_at = time.monotonic()
    logger.info(
        "SST publish phase=start run=%s fresh_frames=%d domains=%s",
        run_id,
        len(frames),
        ",".join(resolved_domains),
    )

    frame_counts: dict[str, int] = {}
    variable_frame_counts: dict[str, dict[str, int]] = {}
    manifest_paths: dict[str, Path] = {}
    published_run_dirs: dict[str, Path] = {}
    failed_domains: list[str] = []

    for domain in resolved_domains:
        try:
            counts_by_var = _publish_domain(
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
        frame_counts[domain] = int(counts_by_var.get(SST_VARIABLE_ID, 0))
        variable_frame_counts[domain] = counts_by_var
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
        variable_frame_counts,
    )
    return SSTPublishResult(
        run_id=run_id,
        frame_counts=frame_counts,
        manifest_paths=manifest_paths,
        published_run_dirs=published_run_dirs,
        variable_frame_counts=variable_frame_counts,
    )


def _publish_domain(
    *,
    data_root: Path,
    run_id: str,
    domain: str,
    frames: list[SSTBundleFrame],
    publish_dt: datetime,
    target_frame_count: int,
) -> dict[str, int]:
    previous_run_id, previous_frames = load_latest_published_sst_frames(data_root, domain)

    previous_by_valid_time = {frame.valid_time: frame for frame in previous_frames}
    fresh_by_valid_time = {
        frame.valid_time.astimezone(timezone.utc): frame for frame in frames
    }

    # The window is the union of days from either side. A day contributes as long
    # as SOME variable can be written or reused for it.
    ordered_valid_times = sorted(set(previous_by_valid_time) | set(fresh_by_valid_time))
    if target_frame_count > 0:
        ordered_valid_times = ordered_valid_times[-int(target_frame_count):]
    if not ordered_valid_times:
        raise ValueError("SST bundle publish resolved to an empty rolling window")

    stage_run = staging_run_root(data_root, run_id, domain)
    if stage_run.exists():
        shutil.rmtree(stage_run, ignore_errors=True)
    stage_run.mkdir(parents=True, exist_ok=True)

    build_artifacts = bool(grid_build_enabled())
    published_fhs_by_var: dict[str, list[int]] = {var_id: [] for var_id in SST_PUBLISH_VARIABLES}
    valid_times_by_fh: dict[int, datetime] = {}

    # Per (day, variable): write it fresh when this publish supplies it, else
    # carry it forward from the previous run, else it is simply absent for that
    # day. This is what lets the anomaly arrive late without re-fetching (or
    # re-encoding) the day's absolute SST.
    for fh, valid_time in enumerate(ordered_valid_times):
        valid_times_by_fh[fh] = valid_time
        fresh = fresh_by_valid_time.get(valid_time)
        previous = previous_by_valid_time.get(valid_time)
        for var_id in SST_PUBLISH_VARIABLES:
            fresh_values = fresh.field_for(var_id) if fresh is not None else None
            if fresh_values is not None:
                wrote = _write_variable_frame(
                    data_root=data_root,
                    run_id=run_id,
                    domain=domain,
                    var_id=var_id,
                    forecast_hour=fh,
                    frame=fresh,
                    values=fresh_values,
                    build_grid_artifacts=build_artifacts,
                )
            elif previous is not None and previous.has(var_id):
                wrote = _reuse_variable_frame(
                    data_root=data_root,
                    run_id=run_id,
                    domain=domain,
                    var_id=var_id,
                    forecast_hour=fh,
                    frame=previous,
                    build_grid_artifacts=build_artifacts,
                )
            else:
                continue
            if wrote:
                published_fhs_by_var[var_id].append(fh)

    if not published_fhs_by_var[SST_VARIABLE_ID]:
        raise ValueError(
            f"SST bundle publish wrote no {SST_VARIABLE_ID} frames for domain={domain}"
        )

    grid_variables = tuple(
        var_id for var_id in SST_PUBLISH_VARIABLES if published_fhs_by_var[var_id]
    )
    if build_artifacts:
        try:
            manifests_built = build_grid_manifests_for_run_root(
                run_root=stage_run,
                model=SST_MODEL_ID,
                run=run_id,
                variables=grid_variables,
            )
            logger.info(
                "SST grid manifest build run=%s domain=%s variables=%s manifests=%d",
                run_id,
                domain,
                ",".join(grid_variables),
                manifests_built,
            )
        except Exception:
            logger.exception("SST grid manifest build failed run=%s domain=%s", run_id, domain)

    _promote_run(data_root=data_root, run_id=run_id, domain=domain)

    manifest_variables: dict[str, Any] = {}
    for var_id in grid_variables:
        fhs = published_fhs_by_var[var_id]
        # Units/kind come from a published sidecar so the manifest carries the
        # same display units the frontend reads there ("°C", via
        # pipeline._format_units) instead of a second hardcoded spelling.
        sidecar_units, sidecar_kind = _units_and_kind_from_published_sidecar(
            data_root=data_root, run_id=run_id, domain=domain, var_id=var_id, fh=fhs[0]
        )
        manifest_variables[var_id] = {
            "display_name": _DISPLAY_NAME_BY_VARIABLE[var_id],
            "kind": sidecar_kind,
            "units": sidecar_units,
            # The window's real size for BOTH variables, so a day whose anomaly
            # upstream has not published yet reads as available < expected rather
            # than being quietly hidden.
            "expected_frames": len(ordered_valid_times),
            "available_frames": len(fhs),
            "frames": [
                {"fh": fh, "valid_time": valid_times_by_fh[fh].strftime(_TIME_FORMAT)}
                for fh in fhs
            ],
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
    counts = {var_id: len(fhs) for var_id, fhs in published_fhs_by_var.items() if fhs}
    logger.info(
        "SST publish phase=domain_complete run=%s domain=%s frames=%s reused_from=%s",
        run_id,
        domain,
        counts,
        previous_run_id or "-",
    )
    return counts


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


def _write_variable_frame(
    *,
    data_root: Path,
    run_id: str,
    domain: str,
    var_id: str,
    forecast_hour: int,
    frame: SSTBundleFrame,
    values: np.ndarray,
    build_grid_artifacts: bool,
) -> bool:
    grid = get_target_grid(SST_MODEL_ID, domain)
    color_map_id = _COLOR_MAP_BY_VARIABLE[var_id]

    # Dilate the ocean edge into the land NaN fringe BEFORE the warp, sized for
    # this domain's target cell. Done per domain (not once at read time) because
    # the two grids need different reaches. Applies to the anomaly identically —
    # it has the same water-only footprint (a slightly smaller one, since CRW
    # masks ice zones) and the same texel-staircase problem.
    source_values = np.asarray(values, dtype=np.float32)
    source_valid_fraction = float(np.isfinite(source_values).mean())
    fringe_cells = coastal_fringe_cells_for_domain(domain)
    source_values = fill_coastal_fringe(source_values, radius_cells=fringe_cells)
    filled_valid_fraction = float(np.isfinite(source_values).mean())

    warped, _ = warp_to_target_grid(
        source_values,
        frame.source_crs,
        frame.source_transform,
        model=SST_MODEL_ID,
        region=domain,
        resampling="bilinear",
        src_nodata=float("nan"),
        working_dtype=np.float32,
    )
    warped = np.asarray(warped, dtype=np.float32)
    if warped.shape != (grid.height, grid.width):
        raise ValueError(
            f"SST warp produced {warped.shape}, expected {(grid.height, grid.width)} "
            f"for domain={domain} var={var_id}"
        )

    if not _pre_encode_gate_allows(
        warped, var_id=var_id, forecast_hour=forecast_hour, domain=domain
    ):
        return False

    stage_var_dir = staging_run_root(data_root, run_id, domain) / var_id
    stage_var_dir.mkdir(parents=True, exist_ok=True)

    colorize_meta = colorize_metadata(warped, color_map_id, meta_var_key=var_id)
    valid_time = frame.valid_time.astimezone(timezone.utc)
    sidecar = build_sidecar_json(
        model=SST_MODEL_ID,
        region=domain,
        run_id=run_id,
        var_id=var_id,
        fh=int(forecast_hour),
        run_date=datetime.now(timezone.utc),
        colorize_meta=colorize_meta,
        var_spec={"type": "continuous", "units": "C"},
        var_spec_model=SST_MODEL.get_var(var_id),
        value_downsample_factor=1,
        quality=frame.quality,
        quality_flags=list(frame.quality_flags),
        valid_time_override=valid_time,
    )
    source_url = frame.source_url_for(var_id)
    source_filename = frame.source_filename_for(var_id)
    if source_url:
        sidecar["source_url"] = source_url
    if source_filename:
        sidecar["source_filename"] = source_filename
    source_metadata = frame.metadata_for(var_id)
    source_metadata["actual_valid_time"] = valid_time.strftime(_TIME_FORMAT)
    # Per-domain coastal dilation, recorded so it is auditable from a published
    # sidecar rather than inferred from the ramp.
    source_metadata["coastal_fringe_source_cells"] = fringe_cells
    source_metadata["source_valid_fraction"] = round(source_valid_fraction, 6)
    source_metadata["fringe_filled_valid_fraction"] = round(filled_valid_fraction, 6)
    source_metadata["warped_valid_fraction"] = round(float(np.isfinite(warped).mean()), 6)
    sidecar["source_metadata"] = source_metadata
    write_json_atomic(stage_var_dir / f"fh{int(forecast_hour):03d}.json", sidecar)

    if build_grid_artifacts:
        write_grid_frames_for_run_root(
            run_root=staging_run_root(data_root, run_id, domain),
            model=SST_MODEL_ID,
            var=var_id,
            fh=int(forecast_hour),
            values=warped,
            transform=grid.transform,
            projection=grid.crs,
        )
    return True


def _reuse_variable_frame(
    *,
    data_root: Path,
    run_id: str,
    domain: str,
    var_id: str,
    forecast_hour: int,
    frame: SSTPublishedFrame,
    build_grid_artifacts: bool,
) -> bool:
    """Carry one previously published (day, variable) forward by hardlink."""
    source_value_path = frame.sidecar_paths[var_id]
    if build_grid_artifacts and not _reuse_grid_artifacts(
        data_root=data_root,
        run_id=run_id,
        domain=domain,
        var_id=var_id,
        forecast_hour=int(forecast_hour),
        source_value_path=source_value_path,
    ):
        logger.warning(
            "Skipping SST reuse: no grid artifacts domain=%s var=%s fh%03d source=%s",
            domain,
            var_id,
            int(forecast_hour),
            source_value_path,
        )
        return False

    stage_var_dir = staging_run_root(data_root, run_id, domain) / var_id
    stage_var_dir.mkdir(parents=True, exist_ok=True)

    sidecar = dict(frame.sidecars[var_id])
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
    var_id: str,
    forecast_hour: int,
    source_value_path: Path,
) -> bool:
    source_fh = _forecast_hour_from_artifact_name(source_value_path)
    if source_fh is None:
        return False

    source_run_root = source_value_path.parent.parent
    source_grid_dir = resolved_grid_dir_for_run_root(source_run_root, var_id)
    if not source_grid_dir.is_dir():
        return False

    target_grid_dir = grid_dir_for_run_root(
        staging_run_root(data_root, run_id, domain), var_id
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

def _pre_encode_gate_allows(
    values: np.ndarray, *, var_id: str, forecast_hour: int, domain: str
) -> bool:
    label = f"{SST_MODEL_ID}/{var_id}/{domain}/fh{int(forecast_hour):03d}"
    try:
        gate_ok = check_pre_encode_value_sanity(
            values,
            get_color_map_spec(_COLOR_MAP_BY_VARIABLE[var_id]),
            var_spec_model=SST_MODEL.get_var(var_id),
            var_capability=SST_MODEL.get_var_capability(var_id),
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
    """Promote a staged SST run into the published tree.

    Uses the same two-rename swap as ``scheduler._promote_run``: the live
    published run dir is renamed aside, never ``rmtree``'d in place. Anomaly
    catch-up amends the *same* ``run_id`` on every late-anomaly cycle, so an
    in-place rmtree would open a multi-second 404 window for every reader and —
    if the subsequent move failed — leave ``LATEST.json`` pointing at a missing
    run with no automatic restore.
    """
    stage_run = staging_run_root(data_root, run_id, domain)
    if not stage_run.is_dir():
        raise ValueError(f"Cannot promote missing SST staging run dir: {stage_run}")

    # tmp / trash / final are siblings inside this one directory, so the
    # two-rename swap stays atomic (single-directory renames) per domain.
    published_model = published_model_root(data_root, domain)
    published_model.mkdir(parents=True, exist_ok=True)
    published_run = published_model / run_id
    tmp_run = published_model / f".{run_id}.tmp"

    if tmp_run.exists():
        shutil.rmtree(tmp_run, ignore_errors=True)
    if tmp_run.exists():
        raise ValueError(f"Cannot clear temporary SST promotion dir: {tmp_run}")

    shutil.copytree(stage_run, tmp_run, copy_function=os.link)

    trash_run = published_model / f".{run_id}.trash"
    if trash_run.exists():
        shutil.rmtree(trash_run, ignore_errors=True)
    if trash_run.exists():
        raise ValueError(f"Cannot clear stale SST promotion trash dir: {trash_run}")

    if published_run.exists():
        os.rename(published_run, trash_run)
    try:
        os.rename(tmp_run, published_run)
    except OSError:
        # Put the previous published run back before surfacing the failure.
        if trash_run.exists() and not published_run.exists():
            os.rename(trash_run, published_run)
        raise
    shutil.rmtree(trash_run, ignore_errors=True)


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
    *, data_root: Path, run_id: str, domain: str, var_id: str, fh: int
) -> tuple[str, str]:
    sidecar_path = (
        published_run_root(data_root, run_id, domain) / var_id / f"fh{int(fh):03d}.json"
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
