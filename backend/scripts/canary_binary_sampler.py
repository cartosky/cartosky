#!/usr/bin/env python3
"""Phase D/Phase G canary: shadow-compare COG vs binary sampling.

Runs against real published data, samples anchor city points across all
selected-model variables and forecast hours, logs divergences with full
context, and records performance metrics per the migration plan Section 3 /
Layer 3.

Usage::

    python backend/scripts/canary_binary_sampler.py \\
        --data-root /opt/cartosky/data \\
        --log-divergences divergences.jsonl \\
        --summary summary.json \\
        --workers 8

The script discovers retained runs for the selected model under the published
root, enumerates every variable in ``_PACKING_BY_MODEL_VAR`` for that model
(excluding ``buildable=False`` catalog entries that are never independently
published), samples anchor-city coordinates through both the COG path and the
binary path, and writes a JSON-lines divergence log plus a summary JSON.
``--vars var1,var2`` restricts the scope for targeted smoke runs.

Tolerance groups::

  Group 1 – no display-prep upscale → divergence when
            |cog - binary| > scale/2 + epsilon.
  Group 2 – continuous 3× upscale → divergence expected near gradients;
            pass condition is "spatially explainable and bounded."
  Group 3 – categorical-nearest upscale → divergences only at genuine
            class-boundary pixels (rounded integer comparison).
  Group 4 – categorical-nearest without upscale → strict categorical equality
            is expected; any divergence is blocking like Group 1.

Performance benchmarks are run after the core comparison: single-point,
100-point batch, 1000-point batch, and a full-meteogram simulation over the
model's scheduled forecast-hour range, recording latency for both substrates.

Exit codes::
  0 – clean pass (no blocking divergences, comparisons were performed)
  1 – usage / data-root missing / invalid --vars
  2 – requested run not found
  3 – no comparisons performed (no usable frames)
  4 – blocking: binary frame/meta file missing, binary resolution failure on
      a COG-sampled frame (bin_meta_invalid), asymmetric no-value rates
      between substrates, Group 1 divergence, Group 3 categorical divergence,
      or Group 4 categorical divergence
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Path setup ──────────────────────────────────────────────────────
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Parse --data-root early so it is set before any app module reads env.
_early_parser = argparse.ArgumentParser(add_help=False)
_early_parser.add_argument(
    "--data-root",
    default=os.environ.get("CARTOSKY_DATA_ROOT", "./data"),
    help="CartoSky data root (default: $CARTOSKY_DATA_ROOT or ./data)",
)
_early_args, _ = _early_parser.parse_known_args()
os.environ.setdefault(
    "CARTOSKY_DATA_ROOT",
    str(Path(_early_args.data_root).expanduser().resolve()),
)

# Safe to import app modules now.
import numpy as np
import rasterio
import rasterio.warp

from app.models.registry import MODEL_REGISTRY
from app.services.grid import (
    GRID_DTYPE,
    _PACKING_BY_MODEL_VAR,
    grid_dtype,
    grid_frame_filename,
)
from app.services.grid_display_prep import (
    grid_display_prep_config,
    sampling_tolerance_group,
)
from app.services.sampling import (
    _read_sample_value,
    _sample_dataset_index,
    read_binary_sample_value,
)

# ── Logging ──────────────────────────────────────────────────────────
logger = logging.getLogger("canary")


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                          datefmt="%Y-%m-%dT%H:%M:%S"))
    logging.basicConfig(level=level, handlers=[handler], force=True)


# ── Constants ───────────────────────────────────────────────────────
DEFAULT_MODEL = "gfs"
RUN_ID_RE = re.compile(r"^\d{8}_\d{2}z$")

SCALE_HALF_EPSILON = 1e-4

# Group 2 bounded-tolerance constant (matches parity-test threshold).
GROUP2_TOLERANCE = 0.55

# Substrate-asymmetry blocking thresholds: a binary no-value rate above the
# first while the COG no-value rate stays below the second is a substrate
# failure, not legitimate shared no-data.
BIN_NO_VALUE_BLOCKING_RATE = 0.2
COG_NO_VALUE_BENIGN_RATE = 0.05

# ── Helper: per-variable packing ────────────────────────────────────


def _normalize_model(model: str) -> str:
    return str(model or DEFAULT_MODEL).strip().lower()


def _capability_catalog_for_model(model: str) -> dict[str, Any]:
    plugin = MODEL_REGISTRY.get(_normalize_model(model))
    catalog = getattr(getattr(plugin, "capabilities", None), "variable_catalog", None)
    return catalog if isinstance(catalog, dict) else {}


def _companion_published_vars(catalog: dict[str, Any]) -> set[str]:
    """Variables published as ``companion_vars`` of a buildable catalog entry.

    The scheduler appends companions of every buildable variable to its build
    targets, so these are independently published grid frames even when their
    own capability says ``buildable=False`` (e.g. client-composited layers).
    """
    published: set[str] = set()
    for capability in catalog.values():
        if not bool(getattr(capability, "buildable", False)):
            continue
        frontend = getattr(capability, "frontend", None)
        companions = frontend.get("companion_vars") if isinstance(frontend, dict) else None
        if isinstance(companions, list):
            published.update(
                c.strip() for c in companions if isinstance(c, str) and c.strip()
            )
    return published


def _ensemble_artifact_published_vars(catalog: dict[str, Any]) -> set[str]:
    """Runtime artifacts published via a buildable entry's ``artifact_map``.

    Ensemble models (GEFS, EPS) publish a buildable variable's frames under
    the runtime id resolved through ``ensemble.artifact_map`` (e.g. ``tmp2m``
    -> ``tmp2m__mean`` for the "mean" view). Those runtime ids have their own
    capability entry with ``buildable=False``, but the frames are real
    independently published artifacts on both substrates. Only views listed
    in the entry's ``ensemble.supported_views`` are reachable at runtime, so
    mapped values for other views are ignored.
    """
    published: set[str] = set()
    for capability in catalog.values():
        if not bool(getattr(capability, "buildable", False)):
            continue
        ensemble = getattr(capability, "ensemble", None)
        if not isinstance(ensemble, dict):
            continue
        artifact_map = ensemble.get("artifact_map")
        if not isinstance(artifact_map, dict) or not artifact_map:
            continue
        raw_views = ensemble.get("supported_views")
        views = raw_views if isinstance(raw_views, (list, tuple)) else []
        for view in views:
            normalized_view = str(view or "").strip().lower()
            if not normalized_view:
                continue
            resolved = artifact_map.get(normalized_view)
            if isinstance(resolved, str) and resolved.strip():
                published.add(resolved.strip())
    return published


def _ensemble_dead_alias_vars(catalog: dict[str, Any]) -> set[str]:
    """Buildable ids whose runtime resolution redirects to a different artifact.

    The scheduler resolves every build target through ``resolve_runtime_var_id``
    before writing, so a buildable entry whose ``ensemble.artifact_map`` maps
    every reachable view (per its ``supported_views``) to some *other* var id
    is never written under its own name — it is a runtime alias with no frames
    on disk on either substrate (e.g. GEFS/EPS ``tmp2m_anom`` vs the published
    ``tmp2m_anom__mean``). Entries without an ``artifact_map`` never redirect
    and are unaffected.
    """
    dead: set[str] = set()
    for var_key, capability in catalog.items():
        if not bool(getattr(capability, "buildable", False)):
            continue
        ensemble = getattr(capability, "ensemble", None)
        if not isinstance(ensemble, dict):
            continue
        artifact_map = ensemble.get("artifact_map")
        if not isinstance(artifact_map, dict) or not artifact_map:
            continue
        raw_views = ensemble.get("supported_views")
        views = raw_views if isinstance(raw_views, (list, tuple)) else []
        mapped: set[str] = set()
        for view in views:
            normalized_view = str(view or "").strip().lower()
            if not normalized_view:
                continue
            resolved = artifact_map.get(normalized_view)
            if isinstance(resolved, str) and resolved.strip():
                mapped.add(resolved.strip())
        if mapped and str(var_key).strip() not in mapped:
            dead.add(str(var_key).strip())
    return dead


def _split_scope_by_buildable(
    packed_vars: list[str],
    catalog: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Split packed variables into
    (scope, excluded non-buildable, excluded dead-alias, excluded uncataloged).

    A variable is excluded as uncataloged when the model's capability catalog
    has no entry for it at all — checked first because it is the most
    fundamental failure: there is no capability to consult, so no publish
    path can vouch for it. This is distinct from the buckets below (which all
    reason from an existing entry); its typical root cause is a cross-model
    packing loop injecting a key for a model whose own catalog opted out
    (e.g. ecmwf's ``precip_16d_anom`` from the gfs-family precip-anom loop).

    A variable is excluded as non-buildable when its capability says
    ``buildable=False`` and it is neither companion-published nor
    ensemble-artifact-published: such variables are derive-strategy inputs
    consumed in-memory and never written to disk on either substrate.

    A variable is excluded as a dead alias when it is buildable but its own
    ``ensemble.artifact_map`` redirects every reachable view to a different
    artifact id: frames exist only under the redirected id, never this one
    (see ``_ensemble_dead_alias_vars``). The classes are kept separate so
    they stay distinguishable in the summary output.
    """
    published = _companion_published_vars(catalog) | _ensemble_artifact_published_vars(catalog)
    dead_aliases = _ensemble_dead_alias_vars(catalog)
    in_scope: list[str] = []
    excluded_non_buildable: list[str] = []
    excluded_dead_alias: list[str] = []
    excluded_uncataloged: list[str] = []
    for var in packed_vars:
        capability = catalog.get(var)
        if capability is None:
            excluded_uncataloged.append(var)
        elif var in published:
            # Frames exist under this id via another entry's publish path.
            in_scope.append(var)
        elif var in dead_aliases:
            excluded_dead_alias.append(var)
        elif bool(getattr(capability, "buildable", False)):
            in_scope.append(var)
        else:
            excluded_non_buildable.append(var)
    return in_scope, excluded_non_buildable, excluded_dead_alias, excluded_uncataloged


def _scope_for_model(model: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return (scope, excluded non-buildable, excluded dead-alias,
    excluded uncataloged) for a model."""
    model_norm = _normalize_model(model)
    packed = sorted(
        var for (mdl, var) in _PACKING_BY_MODEL_VAR if mdl == model_norm
    )
    (
        in_scope,
        excluded_non_buildable,
        excluded_dead_alias,
        excluded_uncataloged,
    ) = _split_scope_by_buildable(packed, _capability_catalog_for_model(model_norm))
    if excluded_uncataloged:
        logger.info(
            "Excluded %d packed variable(s) with no capability catalog entry "
            "from %s comparison scope: %s",
            len(excluded_uncataloged), model_norm, ", ".join(excluded_uncataloged),
        )
    if excluded_non_buildable:
        logger.info(
            "Excluded %d non-buildable, never-published variable(s) from %s "
            "comparison scope: %s",
            len(excluded_non_buildable), model_norm, ", ".join(excluded_non_buildable),
        )
    if excluded_dead_alias:
        logger.info(
            "Excluded %d buildable dead-alias variable(s) from %s comparison "
            "scope (published only under their artifact_map runtime id): %s",
            len(excluded_dead_alias), model_norm, ", ".join(excluded_dead_alias),
        )
    return in_scope, excluded_non_buildable, excluded_dead_alias, excluded_uncataloged


def _packing(model: str, var: str) -> dict[str, Any]:
    return _PACKING_BY_MODEL_VAR[(_normalize_model(model), var)]


def _packing_dtype(model: str, var: str) -> str:
    return grid_dtype(str(_packing(model, var).get("dtype") or GRID_DTYPE))


def _binary_frame_filename(model: str, var: str, fh: int) -> str:
    """Resolve the binary frame filename from the variable's packing dtype."""
    return grid_frame_filename(fh, dtype=_packing_dtype(model, var))


def _parse_vars_filter(raw: str | None, scope: list[str]) -> list[str]:
    """Restrict scope to the comma-separated ``--vars`` selection.

    Preserves scope order; raises ValueError when a requested variable is
    not in the comparison scope.
    """
    if raw is None:
        return scope
    requested = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not requested:
        raise ValueError("--vars was provided but contained no variable names")
    unknown = sorted(set(requested) - set(scope))
    if unknown:
        raise ValueError(
            f"--vars entries not in comparison scope: {', '.join(unknown)} "
            f"(scope: {', '.join(scope)})"
        )
    requested_set = set(requested)
    return [var for var in scope if var in requested_set]


# ── Dataclasses ─────────────────────────────────────────────────────


@dataclass
class AnchorPoint:
    """A real-world coordinate point to sample."""
    label: str
    lat: float
    lon: float


@dataclass
class Divergence:
    """A single COG-vs-binary divergence record."""
    model: str
    run: str
    var: str
    fh: int
    group: int
    anchor_label: str
    lat: float
    lon: float
    cog_value: float | None
    binary_value: float | None
    distance_to_boundary_px: float | None = None

    def asdict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "run": self.run,
            "var": self.var,
            "fh": self.fh,
            "group": self.group,
            "anchor": self.anchor_label,
            "lat": round(self.lat, 5),
            "lon": round(self.lon, 5),
            "cog_value": (
                round(self.cog_value, 5) if self.cog_value is not None else None
            ),
            "binary_value": (
                round(self.binary_value, 5) if self.binary_value is not None else None
            ),
            "distance_to_boundary_px": (
                round(self.distance_to_boundary_px, 6)
                if self.distance_to_boundary_px is not None
                else None
            ),
        }


@dataclass
class SampleResult:
    """Result from a single (point, frame) comparison.

    File-missing flags distinguish infrastructure problems (blocking)
    from legitimate nodata / out-of-bounds samples (not blocking).
    """
    cog_file_missing: bool
    binary_frame_file_missing: bool
    binary_meta_file_missing: bool
    cog_value: float | None
    binary_value: float | None
    cog_latency_s: float
    binary_latency_s: float
    distance_to_boundary_px: float | None = None

    @property
    def any_file_missing(self) -> bool:
        return self.cog_file_missing or self.binary_frame_file_missing or self.binary_meta_file_missing


@dataclass
class BenchmarkResult:
    """Aggregate latency statistics from a single benchmark run."""
    label: str
    sample_count: int
    cog_mean_s: float
    cog_p50_s: float
    cog_p95_s: float
    cog_p99_s: float
    binary_mean_s: float
    binary_p50_s: float
    binary_p95_s: float
    binary_p99_s: float

    def asdict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "sample_count": self.sample_count,
            "cog_ms": {
                "mean": round(self.cog_mean_s * 1000, 2),
                "p50": round(self.cog_p50_s * 1000, 2),
                "p95": round(self.cog_p95_s * 1000, 2),
                "p99": round(self.cog_p99_s * 1000, 2),
            },
            "binary_ms": {
                "mean": round(self.binary_mean_s * 1000, 2),
                "p50": round(self.binary_p50_s * 1000, 2),
                "p95": round(self.binary_p95_s * 1000, 2),
                "p99": round(self.binary_p99_s * 1000, 2),
            },
        }


# ── Tolerance group classification ─────────────────────────────────


def _classify_variable(model: str, var: str) -> int:
    """Return tolerance group (1, 2, 3, or 4) for a model variable.

    Delegates to the shared config-derived classifier so the canary and the
    Layer 2 parity tests can never drift apart on group assignment.
    """
    return sampling_tolerance_group(grid_display_prep_config(model, var))


def _build_group_index(model: str, scope: list[str]) -> dict[str, int]:
    return {var: _classify_variable(model, var) for var in scope}


# ── Anchor points ───────────────────────────────────────────────────


def _load_anchor_points(data_root: Path) -> list[AnchorPoint]:
    """Load city-label anchor coordinates from ``anchor_index.json``."""
    anchor_path = data_root / "anchor_index.json"
    if not anchor_path.is_file():
        logger.warning("Anchor index not found at %s — using fallback points", anchor_path)
        return _fallback_anchor_points()

    try:
        raw = json.loads(anchor_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load anchor index (%s) — using fallback points", exc)
        return _fallback_anchor_points()

    anchors_raw = raw.get("anchors", {})
    if not isinstance(anchors_raw, dict) or not anchors_raw:
        logger.warning("Anchor index is empty — using fallback points")
        return _fallback_anchor_points()

    points: list[AnchorPoint] = []
    for anchor_id, data in sorted(anchors_raw.items()):
        if not isinstance(data, dict):
            continue
        lat = data.get("lat")
        lon = data.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        city = data.get("city", anchor_id)
        st = data.get("st", "")
        label = f"{city}, {st}" if st else city
        points.append(AnchorPoint(label=label, lat=float(lat), lon=float(lon)))
    return points


def _fallback_anchor_points() -> list[AnchorPoint]:
    """Hardcoded CONUS-wide points covering diverse regions."""
    return [
        AnchorPoint(label="Seattle, WA", lat=47.6062, lon=-122.3321),
        AnchorPoint(label="Portland, OR", lat=45.5152, lon=-122.6784),
        AnchorPoint(label="San Francisco, CA", lat=37.7749, lon=-122.4194),
        AnchorPoint(label="Los Angeles, CA", lat=34.0522, lon=-118.2437),
        AnchorPoint(label="Phoenix, AZ", lat=33.4484, lon=-112.0740),
        AnchorPoint(label="Denver, CO", lat=39.7392, lon=-104.9903),
        AnchorPoint(label="Dallas, TX", lat=32.7767, lon=-96.7970),
        AnchorPoint(label="Houston, TX", lat=29.7604, lon=-95.3698),
        AnchorPoint(label="Minneapolis, MN", lat=44.9778, lon=-93.2650),
        AnchorPoint(label="Chicago, IL", lat=41.8781, lon=-87.6298),
        AnchorPoint(label="St. Louis, MO", lat=38.6270, lon=-90.1994),
        AnchorPoint(label="New Orleans, LA", lat=29.9511, lon=-90.0715),
        AnchorPoint(label="Miami, FL", lat=25.7617, lon=-80.1918),
        AnchorPoint(label="Atlanta, GA", lat=33.7490, lon=-84.3880),
        AnchorPoint(label="Washington, DC", lat=38.9072, lon=-77.0369),
        AnchorPoint(label="New York, NY", lat=40.7128, lon=-74.0060),
        AnchorPoint(label="Boston, MA", lat=42.3601, lon=-71.0589),
        AnchorPoint(label="Detroit, MI", lat=42.3314, lon=-83.0458),
        AnchorPoint(label="Kansas City, MO", lat=39.0997, lon=-94.5786),
        AnchorPoint(label="Oklahoma City, OK", lat=35.4676, lon=-97.5164),
        AnchorPoint(label="Salt Lake City, UT", lat=40.7608, lon=-111.8910),
        AnchorPoint(label="Boise, ID", lat=43.6150, lon=-116.2023),
        AnchorPoint(label="Billings, MT", lat=45.7833, lon=-108.5007),
        AnchorPoint(label="Fargo, ND", lat=46.8772, lon=-96.7898),
        AnchorPoint(label="Caribou, ME", lat=46.8607, lon=-68.0120),
        AnchorPoint(label="San Diego, CA", lat=32.7157, lon=-117.1611),
        AnchorPoint(label="El Paso, TX", lat=31.7619, lon=-106.4850),
        AnchorPoint(label="Burlington, VT", lat=44.4759, lon=-73.2121),
        AnchorPoint(label="Anchorage, AK", lat=61.2181, lon=-149.9003),
        AnchorPoint(label="Honolulu, HI", lat=21.3069, lon=-157.8583),
    ]


# ── Run / frame discovery ───────────────────────────────────────────


def _discover_runs(published_root: Path, model: str) -> list[str]:
    """Return sorted list of run ids with published data for a model."""
    model_dir = published_root / _normalize_model(model)
    if not model_dir.is_dir():
        return []
    runs: list[str] = []
    for entry in sorted(model_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not RUN_ID_RE.match(entry.name):
            continue
        runs.append(entry.name)
    return runs


def _discover_frames_for_run_var(
    published_root: Path,
    model: str,
    run: str,
    var: str,
) -> list[int]:
    """Return forecast hours where the value COG exists.

    Binary frame existence is verified at sample time rather than here,
    so frame-discovery can report missing-binary-frame files separately.
    """
    var_dir = published_root / _normalize_model(model) / run / var
    if not var_dir.is_dir():
        return []
    fh_values: list[int] = []
    for cog_path in sorted(var_dir.glob("fh*.val.cog.tif")):
        fh_str = cog_path.name[2:5]
        try:
            fh = int(fh_str)
        except ValueError:
            continue
        fh_values.append(fh)
    return fh_values


# ── Raw COG sampler (no rounding) ───────────────────────────────────


def _read_cog_raw(cog_path: Path, *, lat: float, lon: float) -> float | None:
    """Read a single pixel from a value COG, returning the raw float32 value.

    Returns None for out-of-bounds, nodata, or NaN pixels.  Does *not*
    round — divergence checks compare raw decoded values on both sides.
    """
    with rasterio.open(cog_path) as ds:
        row, col = _sample_dataset_index(ds, lon=lon, lat=lat)
        value, no_data = _read_sample_value(ds, row=row, col=col, masked=True)
        if no_data or value is None:
            return None
        return float(value)


# ── Pixel-boundary distance ─────────────────────────────────────────


def _distance_to_pixel_boundary(
    cog_path: Path,
    lat: float,
    lon: float,
) -> float | None:
    """Distance (in pixel units) to the nearest pixel edge for a lon/lat point.

    Transforms lon/lat from EPSG:4326 into the dataset's CRS (via
    ``rasterio.warp.transform``) then applies the inverse affine
    transform to get fractional column/row.
    """
    try:
        with rasterio.open(cog_path) as ds:
            src_crs = "EPSG:4326"
            dst_crs = ds.crs
            if dst_crs is None:
                return None
            xs, ys = rasterio.warp.transform(src_crs, dst_crs, [lon], [lat])
            col_f, row_f = ~ds.transform * (xs[0], ys[0])
    except Exception:
        return None
    frac_row = row_f - int(row_f)
    frac_col = col_f - int(col_f)
    return min(
        abs(frac_row),
        abs(1.0 - frac_row),
        abs(frac_col),
        abs(1.0 - frac_col),
    )


# ── Per-frame comparison ────────────────────────────────────────────


def _sample_one(
    published_root: Path,
    model: str,
    run: str,
    var: str,
    fh: int,
    point: AnchorPoint,
) -> SampleResult:
    """Sample one point through both COG and binary paths, timing each.

    Distinguishes file-missing from no-value samples so the caller can
    treat infrastructure problems separately from legitimate nodata.
    """
    model_norm = _normalize_model(model)
    var_dir = published_root / model_norm / run / var
    cog_path = var_dir / f"fh{fh:03d}.val.cog.tif"
    grid_dir = var_dir / "grid"
    bin_name = _binary_frame_filename(model_norm, var, fh)
    frame_path = grid_dir / bin_name
    meta_path = grid_dir / f"fh{fh:03d}.l0.meta.json"

    cog_file_missing = not cog_path.is_file()
    bin_frame_missing = not frame_path.is_file()
    bin_meta_missing = not meta_path.is_file()

    dist_px: float | None = None
    if not cog_file_missing:
        dist_px = _distance_to_pixel_boundary(cog_path, point.lat, point.lon)

    # COG sample
    cog_value: float | None = None
    cog_latency = 0.0
    if not cog_file_missing:
        t0 = time.perf_counter()
        try:
            cog_value = _read_cog_raw(cog_path, lat=point.lat, lon=point.lon)
        except Exception:
            logger.debug("COG sample error: run=%s var=%s fh=%03d", run, var, fh,
                         exc_info=True)
        cog_latency = time.perf_counter() - t0

    # Binary sample
    bin_value: float | None = None
    bin_latency = 0.0
    if not bin_frame_missing and not bin_meta_missing:
        t0 = time.perf_counter()
        try:
            raw, no_data = read_binary_sample_value(
                frame_path, meta_path,
                model=model_norm, var=var,
                lat=point.lat, lon=point.lon,
            )
            bin_value = None if (no_data or raw is None) else float(raw)
        except Exception:
            logger.debug("Binary sample error: run=%s var=%s fh=%03d", run, var, fh,
                         exc_info=True)
        bin_latency = time.perf_counter() - t0

    return SampleResult(
        cog_file_missing=cog_file_missing,
        binary_frame_file_missing=bin_frame_missing,
        binary_meta_file_missing=bin_meta_missing,
        cog_value=cog_value,
        binary_value=bin_value,
        cog_latency_s=cog_latency,
        binary_latency_s=bin_latency,
        distance_to_boundary_px=dist_px,
    )


def _is_divergent(
    result: SampleResult,
    group: int,
    var: str,
    *,
    model: str = DEFAULT_MODEL,
) -> bool:
    """Does this result constitute a reportable divergence?

    Only called when neither substrate has a missing file.
    Both-values-None (matching nodata) is *not* a divergence.

    Group 1 (no upscale):  |cog - binary| > scale/2 + epsilon.
    Group 2 (continuous 3×): |cog - binary| > GROUP2_TOLERANCE.
    Group 3 (categorical upscale): int(round(cog)) != int(round(binary)).
    Group 4 (categorical no-upscale): same integer-category equality as Group 3,
        but blocking like Group 1 because matching-resolution categories should
        not diverge.
    """
    # Both absent (matching nodata / out-of-bounds) — agreed.
    if result.cog_value is None and result.binary_value is None:
        return False

    # One present, one absent — structural disagreement.
    if result.cog_value is None or result.binary_value is None:
        return True

    if group == 1:
        scale = float(_packing(model, var)["scale"])
        threshold = scale / 2 + SCALE_HALF_EPSILON
        return abs(result.cog_value - result.binary_value) > threshold

    if group in {3, 4}:
        return int(round(result.cog_value)) != int(round(result.binary_value))

    # Group 2 — continuous 3× upscale: bounded tolerance.
    return abs(result.cog_value - result.binary_value) > GROUP2_TOLERANCE


def _is_bin_meta_invalid(result: SampleResult) -> bool:
    """Binary substrate failed on a frame the COG side sampled successfully.

    Both binary files exist, the COG produced a value, but binary resolution
    (meta load / decode / index) failed or returned no-value.  This is a
    substrate failure, not a tolerance-group divergence, and blocks the canary
    regardless of the variable's group.
    """
    return (
        not result.cog_file_missing
        and not result.binary_frame_file_missing
        and not result.binary_meta_file_missing
        and result.cog_value is not None
        and result.binary_value is None
    )


# ── Divergence accumulator ──────────────────────────────────────────


class DivergenceAccumulator:
    """Tracks divergences for per-run, per-var, per-group summaries."""

    def __init__(self) -> None:
        self.by_run: dict[str, int] = defaultdict(int)
        self.by_var: dict[str, int] = defaultdict(int)
        self.by_group: dict[int, int] = defaultdict(int)
        self.total: int = 0

    def record(self, run: str, var: str, group: int) -> None:
        self.by_run[run] += 1
        self.by_var[var] += 1
        self.by_group[group] += 1
        self.total += 1

    def top_vars(self, n: int = 20) -> list[dict[str, Any]]:
        return sorted(
            ({"var": v, "divergences": c} for v, c in self.by_var.items()),
            key=lambda x: x["divergences"],
            reverse=True,
        )[:n]

    def asdict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_run": dict(sorted(self.by_run.items())),
            "by_variable": dict(sorted(self.by_var.items())),
            "by_group": {
                str(g): c for g, c in sorted(self.by_group.items())
            },
            "top_variables": self.top_vars(),
        }


# ── Comparison runner ───────────────────────────────────────────────


def _run_comparison(
    published_root: Path,
    model: str,
    scope: list[str],
    runs_to_process: list[str],
    anchors: list[AnchorPoint],
    group_index: dict[str, int],
    log_path: Path | None,
    *,
    sample_limit: int | None = None,
) -> dict[str, Any]:
    """Core comparison loop.  Returns summary statistics dict."""
    model_norm = _normalize_model(model)
    logger.info("Processing %d %s run(s): %s", len(runs_to_process),
                model_norm, ", ".join(runs_to_process))

    divergence_fh = None
    if log_path:
        divergence_fh = open(log_path, "w", encoding="utf-8")

    stats: dict[str, Any] = {
        "model": model_norm,
        "runs_found": len(runs_to_process),
        "runs": runs_to_process,
    }

    total = 0
    missing_cog_files = 0
    missing_binary_frame_files = 0
    missing_binary_meta_files = 0
    cog_no_value = 0
    binary_no_value = 0
    bin_meta_invalid = 0
    comparisons_by_var: dict[str, int] = defaultdict(int)
    total_cog_latencies: list[float] = []
    total_binary_latencies: list[float] = []
    group_counts: dict[int, int] = defaultdict(int)
    divergences = DivergenceAccumulator()
    vars_with_frames: set[str] = set()

    try:
        for run in runs_to_process:
            logger.info("Processing run: %s", run)
            for var in scope:
                fh_values = _discover_frames_for_run_var(published_root, model_norm, run, var)
                if not fh_values:
                    continue
                vars_with_frames.add(var)
                group = group_index[var]

                for fh in fh_values:
                    for point in anchors:
                        if sample_limit is not None and total >= sample_limit:
                            break
                        total += 1
                        group_counts[group] += 1
                        comparisons_by_var[var] += 1

                        result = _sample_one(published_root, model_norm, run, var, fh, point)
                        total_cog_latencies.append(result.cog_latency_s)
                        total_binary_latencies.append(result.binary_latency_s)

                        if result.cog_file_missing:
                            missing_cog_files += 1
                        elif result.cog_value is None:
                            cog_no_value += 1

                        if result.binary_frame_file_missing:
                            missing_binary_frame_files += 1
                        if result.binary_meta_file_missing:
                            missing_binary_meta_files += 1
                        if (not result.binary_frame_file_missing
                                and not result.binary_meta_file_missing
                                and result.binary_value is None):
                            binary_no_value += 1
                        if _is_bin_meta_invalid(result):
                            bin_meta_invalid += 1

                        # Divergence check: only when neither side has a missing
                        # file.  (Both-values-None is already handled inside
                        # _is_divergent.)
                        if not result.any_file_missing:
                            if _is_divergent(result, group, var, model=model_norm):
                                divergences.record(run, var, group)
                                div = Divergence(
                                    model=model_norm,
                                    run=run,
                                    var=var,
                                    fh=fh,
                                    group=group,
                                    anchor_label=point.label,
                                    lat=point.lat,
                                    lon=point.lon,
                                    cog_value=result.cog_value,
                                    binary_value=result.binary_value,
                                    distance_to_boundary_px=result.distance_to_boundary_px,
                                )
                                if divergence_fh:
                                    divergence_fh.write(json.dumps(div.asdict()) + "\n")

                    if sample_limit is not None and total >= sample_limit:
                        break
                if sample_limit is not None and total >= sample_limit:
                    break
            if sample_limit is not None and total >= sample_limit:
                break
    finally:
        if divergence_fh:
            divergence_fh.close()

    vars_with_zero_comparisons = [
        var for var in scope if comparisons_by_var[var] == 0
    ]
    if vars_with_zero_comparisons:
        logger.warning(
            "%d in-scope variable(s) got zero comparisons%s: %s",
            len(vars_with_zero_comparisons),
            (f" (--sample-limit {sample_limit} truncated coverage)"
             if sample_limit is not None else ""),
            ", ".join(vars_with_zero_comparisons),
        )

    stats.update({
        "total_comparisons": total,
        "vars_with_frames": len(vars_with_frames),
        "vars_with_zero_comparisons": vars_with_zero_comparisons,
        "missing_cog_files": missing_cog_files,
        "missing_binary_frame_files": missing_binary_frame_files,
        "missing_binary_meta_files": missing_binary_meta_files,
        "cog_no_value_samples": cog_no_value,
        "binary_no_value_samples": binary_no_value,
        "bin_meta_invalid_count": bin_meta_invalid,
        "divergences": divergences.asdict(),
        "comparisons_by_group": dict(group_counts),
        "cog_latency_s": _latency_summary(total_cog_latencies),
        "binary_latency_s": _latency_summary(total_binary_latencies),
    })

    logger.info(
        "Comparison complete: %d samples, %d divergences "
        "(COG missing=%d bin-frame-missing=%d bin-meta-missing=%d "
        "COG-noval=%d bin-noval=%d bin-meta-invalid=%d)",
        total, divergences.total,
        missing_cog_files, missing_binary_frame_files, missing_binary_meta_files,
        cog_no_value, binary_no_value, bin_meta_invalid,
    )
    return stats


def _latency_summary(latencies: list[float]) -> dict[str, float]:
    if not latencies:
        return {}
    sorted_lat = sorted(latencies)
    return {
        "count": len(sorted_lat),
        "mean": round(statistics.mean(sorted_lat), 6),
        "p50": round(_percentile(sorted_lat, 50), 6),
        "p95": round(_percentile(sorted_lat, 95), 6),
        "p99": round(_percentile(sorted_lat, 99), 6),
        "min": round(min(sorted_lat), 6),
        "max": round(max(sorted_lat), 6),
    }


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    idx = (p / 100.0) * (len(data) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(data) - 1)
    frac = idx - lo
    return data[lo] * (1.0 - frac) + data[hi] * frac


# ── Performance benchmarks ──────────────────────────────────────────


def _run_benchmarks(
    published_root: Path,
    model: str,
    anchors: list[AnchorPoint],
    target_run: str,
    workers: int,
    scope: list[str],
    group_index: dict[str, int],
) -> list[BenchmarkResult]:
    """Run the four Phase D performance benchmarks.

    The benchmark variable is the first Group 1 variable in scope: the
    simplest case (no display-prep), matching what every prior model's
    benchmark measured — and guaranteed to be a variable actually published
    for this model, unlike a hardcoded var id.
    """
    model_norm = _normalize_model(model)
    bench_var = next((var for var in scope if group_index.get(var) == 1), None)
    if bench_var is None:
        logger.warning(
            "No Group 1 variable in %s comparison scope — skipping benchmarks "
            "(the benchmark methodology assumes a no-display-prep variable)",
            model_norm,
        )
        return []
    logger.info("Benchmark variable: %s (first Group 1 variable in scope)", bench_var)
    fh_values = _discover_frames_for_run_var(published_root, model_norm, target_run, bench_var)
    if not fh_values:
        logger.warning("No frames for benchmark var %s in run %s", bench_var, target_run)
        return []

    fh0 = fh_values[0]
    anchor = anchors[0]
    var_dir = published_root / model_norm / target_run / bench_var
    cog_path = var_dir / f"fh{fh0:03d}.val.cog.tif"
    grid_dir = var_dir / "grid"
    bin_name = _binary_frame_filename(model_norm, bench_var, fh0)
    frame_path = grid_dir / bin_name
    meta_path = grid_dir / f"fh{fh0:03d}.l0.meta.json"

    results: list[BenchmarkResult] = []

    # 1. Single-point
    results.append(_bench_single(cog_path, frame_path, meta_path, model_norm, anchor, bench_var))

    # 2. 100-point batch
    points_100 = _scatter_points(anchors, 100, published_root, model_norm, target_run, bench_var)
    results.append(_bench_batch(cog_path, frame_path, meta_path, points_100,
                                model_norm, bench_var, label="100-point batch"))

    # 3. 1000-point batch
    points_1000 = _scatter_points(anchors, 1000, published_root, model_norm, target_run, bench_var)
    results.append(_bench_batch(cog_path, frame_path, meta_path, points_1000,
                                model_norm, bench_var, label="1000-point batch"))

    # 4. Meteogram simulation: 85–105 sequential frames, single point
    results.append(_bench_meteogram(published_root, model_norm, target_run, bench_var, fh_values,
                                    anchor, workers))

    return results


def _bench_single(
    cog_path: Path,
    frame_path: Path,
    meta_path: Path,
    model: str,
    anchor: AnchorPoint,
    var: str,
) -> BenchmarkResult:
    """Single-point latency: sample one point 200 times and aggregate."""
    cog_times: list[float] = []
    bin_times: list[float] = []
    for _ in range(200):
        t0 = time.perf_counter()
        try:
            _read_cog_raw(cog_path, lat=anchor.lat, lon=anchor.lon)
        except Exception:
            pass
        cog_times.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        try:
            read_binary_sample_value(
                frame_path, meta_path,
                model=model, var=var,
                lat=anchor.lat, lon=anchor.lon,
            )
        except Exception:
            pass
        bin_times.append(time.perf_counter() - t0)

    return _build_benchmark("single-point (200 reps)", cog_times, bin_times)


def _bench_batch(
    cog_path: Path,
    frame_path: Path,
    meta_path: Path,
    points: list[AnchorPoint],
    model: str,
    var: str,
    *,
    label: str,
) -> BenchmarkResult:
    """Sample N distinct points from the same frame, timing each call."""
    cog_times: list[float] = []
    bin_times: list[float] = []
    for pt in points:
        t0 = time.perf_counter()
        try:
            _read_cog_raw(cog_path, lat=pt.lat, lon=pt.lon)
        except Exception:
            pass
        cog_times.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        try:
            read_binary_sample_value(
                frame_path, meta_path,
                model=model, var=var,
                lat=pt.lat, lon=pt.lon,
            )
        except Exception:
            pass
        bin_times.append(time.perf_counter() - t0)

    return _build_benchmark(label, cog_times, bin_times)


def _expected_meteogram_frame_count(model: str, var: str, run: str) -> int | None:
    """Frame count the model's schedule publishes for this variable and cycle.

    Returns None when the plugin, its schedule, or the run-id cycle hour
    cannot be resolved.
    """
    plugin = MODEL_REGISTRY.get(_normalize_model(model))
    if plugin is None or not RUN_ID_RE.match(run):
        return None
    cycle_hour = int(run[9:11])
    try:
        fhs = plugin.scheduled_fhs_for_var(var, cycle_hour)
    except Exception:
        return None
    return len(fhs) or None


def _bench_meteogram(
    published_root: Path,
    model: str,
    run: str,
    var: str,
    fh_values: list[int],
    anchor: AnchorPoint,
    workers: int,
) -> BenchmarkResult:
    """Sample the model's scheduled frame range (one point) via both paths."""
    expected = _expected_meteogram_frame_count(model, var, run)
    fh_subset = fh_values if expected is None else fh_values[:expected]
    if expected is not None and len(fh_subset) < expected:
        logger.warning(
            "Only %d frames for meteogram benchmark (model schedule expects %d)",
            len(fh_subset), expected,
        )
    label = f"meteogram ({len(fh_subset)} frames)"

    cog_times: list[float] = []
    bin_times: list[float] = []

    model_norm = _normalize_model(model)
    var_dir = published_root / model_norm / run / var
    grid_dir = var_dir / "grid"

    for fh in fh_subset:
        c_path = var_dir / f"fh{fh:03d}.val.cog.tif"
        bin_name = _binary_frame_filename(model_norm, var, fh)
        f_path = grid_dir / bin_name
        m_path = grid_dir / f"fh{fh:03d}.l0.meta.json"

        t0 = time.perf_counter()
        try:
            if c_path.is_file():
                _read_cog_raw(c_path, lat=anchor.lat, lon=anchor.lon)
        except Exception:
            pass
        cog_times.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        try:
            if f_path.is_file() and m_path.is_file():
                read_binary_sample_value(
                    f_path, m_path,
                    model=model_norm, var=var,
                    lat=anchor.lat, lon=anchor.lon,
                )
        except Exception:
            pass
        bin_times.append(time.perf_counter() - t0)

    return _build_benchmark(label, cog_times, bin_times)


def _scatter_points(
    anchors: list[AnchorPoint],
    count: int,
    published_root: Path,
    model: str,
    run: str,
    var: str,
) -> list[AnchorPoint]:
    """Generate ``count`` points scattered across the grid domain."""
    if len(anchors) >= count:
        return anchors[:count]

    var_dir = published_root / _normalize_model(model) / run / var
    bbox = (-125.0, 24.0, -66.0, 50.0)
    for cog_path in sorted(var_dir.glob("fh*.val.cog.tif")):
        try:
            with rasterio.open(cog_path) as ds:
                bbox = (
                    ds.bounds.left,
                    ds.bounds.bottom,
                    ds.bounds.right,
                    ds.bounds.top,
                )
            break
        except Exception:
            continue

    left, bottom, right, top = bbox
    margin = 0.5
    left += margin
    bottom += margin
    right -= margin
    top -= margin
    if left >= right or bottom >= top:
        return anchors[:count]

    points: list[AnchorPoint] = list(anchors)
    rng = np.random.default_rng(42)
    needed = count - len(points)
    lats = rng.uniform(bottom, top, needed)
    lons = rng.uniform(left, right, needed)
    for i in range(needed):
        points.append(AnchorPoint(
            label=f"scatter_{i:04d}",
            lat=float(lats[i]),
            lon=float(lons[i]),
        ))
    return points


def _build_benchmark(
    label: str,
    cog_times: list[float],
    bin_times: list[float],
) -> BenchmarkResult:
    def _p(data: list[float], pct: int) -> float:
        return _percentile(sorted(data), pct) if data else 0.0

    return BenchmarkResult(
        label=label,
        sample_count=len(cog_times),
        cog_mean_s=statistics.mean(cog_times) if cog_times else 0.0,
        cog_p50_s=_p(cog_times, 50),
        cog_p95_s=_p(cog_times, 95),
        cog_p99_s=_p(cog_times, 99),
        binary_mean_s=statistics.mean(bin_times) if bin_times else 0.0,
        binary_p50_s=_p(bin_times, 50),
        binary_p95_s=_p(bin_times, 95),
        binary_p99_s=_p(bin_times, 99),
    )


# ── Count estimation ────────────────────────────────────────────────


def _estimate_comparison_count(
    published_root: Path,
    model: str,
    scope: list[str],
    runs: list[str],
    anchors: list[AnchorPoint],
) -> int:
    """Estimate total comparisons without sampling."""
    estimate = 0
    for run in runs:
        for var in scope:
            fh_values = _discover_frames_for_run_var(published_root, model, run, var)
            if fh_values:
                estimate += len(fh_values) * len(anchors)
    return estimate


# ── Pass / fail logic ───────────────────────────────────────────────


def _no_value_rate_asymmetric(stats: dict[str, Any]) -> bool:
    """Binary no-value rate exceeds its blocking threshold while the COG
    no-value rate stays benign — a substrate gap, not shared no-data."""
    total = int(stats.get("total_comparisons", 0))
    if total <= 0:
        return False
    cog_rate = int(stats.get("cog_no_value_samples", 0)) / total
    bin_rate = int(stats.get("binary_no_value_samples", 0)) / total
    return bin_rate > BIN_NO_VALUE_BLOCKING_RATE and cog_rate < COG_NO_VALUE_BENIGN_RATE


def _exit_code(
    stats: dict[str, Any],
    missing_run: bool,
) -> int:
    """Determine exit code from canary results.

    Returns:
        0 – clean pass
        2 – requested run missing
        3 – no comparisons performed
        4 – blocking: binary frame/meta file missing, binary resolution
            failure on a COG-sampled frame (bin_meta_invalid), asymmetric
            no-value rates between substrates, Group 1 divergence,
            Group 3 categorical divergence, or Group 4 categorical divergence
    """
    if missing_run:
        return 2

    total = int(stats.get("total_comparisons", 0))
    if total == 0:
        logger.error("No comparisons performed — no usable frames found")
        return 3

    missing_bin_frame = int(stats.get("missing_binary_frame_files", 0))
    missing_bin_meta = int(stats.get("missing_binary_meta_files", 0))
    if missing_bin_frame > 0 or missing_bin_meta > 0:
        logger.error(
            "Binary files unexpectedly missing: %d frame file(s), %d meta file(s)",
            missing_bin_frame, missing_bin_meta,
        )
        return 4

    bin_meta_invalid = int(stats.get("bin_meta_invalid_count", 0))
    if bin_meta_invalid > 0:
        logger.error(
            "%d binary substrate failure(s): binary resolution failed or "
            "returned no-value on frames the COG side sampled successfully",
            bin_meta_invalid,
        )
        return 4

    if _no_value_rate_asymmetric(stats):
        logger.error(
            "No-value rates are asymmetric between substrates: binary=%.4f "
            "(> %.2f) while cog=%.4f (< %.2f) — substrate gap, not shared no-data",
            int(stats.get("binary_no_value_samples", 0)) / total,
            BIN_NO_VALUE_BLOCKING_RATE,
            int(stats.get("cog_no_value_samples", 0)) / total,
            COG_NO_VALUE_BENIGN_RATE,
        )
        return 4

    divergences = stats.get("divergences", {})
    by_group = divergences.get("by_group", {})

    group1_div = int(by_group.get("1", 0))
    if group1_div > 0:
        logger.error(
            "%d Group 1 divergence(s) — scale/2 tolerance exceeded "
            "for non-upscaled variables",
            group1_div,
        )
        return 4

    group3_div = int(by_group.get("3", 0))
    if group3_div > 0:
        logger.error(
            "%d Group 3 divergence(s) — categorical match expected "
            "for upscaled categorical variables (after rounding)",
            group3_div,
        )
        return 4

    group4_div = int(by_group.get("4", 0))
    if group4_div > 0:
        logger.error(
            "%d Group 4 divergence(s) — categorical match expected "
            "for non-upscaled categorical variables (after rounding)",
            group4_div,
        )
        return 4

    group2_div = int(by_group.get("2", 0))
    if group2_div > 0:
        logger.warning(
            "%d Group 2 divergence(s) — require manual spatial review "
            "(continuous upscale, not automatically failing)",
            group2_div,
        )

    return 0


# ── Metrics report ──────────────────────────────────────────────────


def _metrics_report(
    stats: dict[str, Any],
    benchmarks: list[BenchmarkResult],
) -> dict[str, Any]:
    """Produce the final summary report."""
    total = int(stats.get("total_comparisons", 0))
    missing_cog_files = int(stats.get("missing_cog_files", 0))
    missing_bin_frame = int(stats.get("missing_binary_frame_files", 0))
    missing_bin_meta = int(stats.get("missing_binary_meta_files", 0))
    cog_no_value = int(stats.get("cog_no_value_samples", 0))
    bin_no_value = int(stats.get("binary_no_value_samples", 0))

    report: dict[str, Any] = {
        "model": stats.get("model", DEFAULT_MODEL),
        "runs": stats.get("runs", []),
        "runs_found": stats.get("runs_found", 0),
        "total_comparisons": total,
        "vars_with_frames": stats.get("vars_with_frames", 0),
        "comparisons_by_group": stats.get("comparisons_by_group", {}),
        "missing_cog_files": missing_cog_files,
        "missing_binary_frame_files": missing_bin_frame,
        "missing_binary_meta_files": missing_bin_meta,
        "missing_file_rate": {
            "cog": round(missing_cog_files / max(total, 1), 6),
            "binary_frame": round(missing_bin_frame / max(total, 1), 6),
            "binary_meta": round(missing_bin_meta / max(total, 1), 6),
        },
        "cog_no_value_samples": cog_no_value,
        "binary_no_value_samples": bin_no_value,
        "no_value_sample_rate": {
            "cog": round(cog_no_value / max(total, 1), 6),
            "binary": round(bin_no_value / max(total, 1), 6),
        },
        "bin_meta_invalid_count": int(stats.get("bin_meta_invalid_count", 0)),
        "no_value_rate_asymmetric": _no_value_rate_asymmetric(stats),
        "vars_with_zero_comparisons": stats.get("vars_with_zero_comparisons", []),
        "divergences": stats.get("divergences", {}),
        "latency_cog_s": stats.get("cog_latency_s", {}),
        "latency_binary_s": stats.get("binary_latency_s", {}),
        "benchmarks": [b.asdict() for b in benchmarks],
    }
    return report


# ── Entry point ─────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase D/Phase G canary: shadow-compare COG vs binary sampling.",
        parents=[_early_parser],
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model id to compare (default: gfs)",
    )
    parser.add_argument(
        "--log-divergences",
        default=None,
        help="JSON-lines file for per-divergence records",
    )
    parser.add_argument(
        "--summary",
        default=None,
        help="JSON summary output path (default: stdout)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, (os.cpu_count() or 2) // 2),
        help="Thread-pool size for benchmarks (default: half CPU count)",
    )
    parser.add_argument(
        "--run",
        default=None,
        help="Limit comparison to a specific run id (e.g. 20260630_12z)",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=None,
        help="Cap total comparisons for faster dry-runs",
    )
    parser.add_argument(
        "--vars",
        default=None,
        help="Comma-separated variable ids to restrict comparison scope "
             "(e.g. radar_ptype,tmp2m); composable with --sample-limit and --run",
    )
    parser.add_argument(
        "--skip-benchmarks",
        action="store_true",
        help="Skip performance benchmarks",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Debug-level logging",
    )
    args = parser.parse_args()
    _setup_logging(verbose=args.verbose)
    model = _normalize_model(args.model)
    scope, excluded_non_buildable, excluded_dead_alias, excluded_uncataloged = _scope_for_model(model)
    if not scope:
        logger.error("No grid packing scope found for model: %s", model)
        sys.exit(1)

    try:
        scope = _parse_vars_filter(args.vars, scope)
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    if args.vars:
        logger.info("Scope restricted by --vars to %d variable(s): %s",
                    len(scope), ", ".join(scope))

    data_root = Path(args.data_root).expanduser().resolve()
    published_root = data_root / "published"
    if not published_root.is_dir():
        logger.error("Published root not found: %s", published_root)
        sys.exit(1)

    # ── Run selection ───────────────────────────────────────────────
    missing_run = False
    if args.run:
        run_dir = published_root / model / args.run
        if not run_dir.is_dir() or not RUN_ID_RE.match(args.run):
            logger.error("Requested run not found or invalid: %s", args.run)
            sys.exit(2)
        all_runs = [args.run]
        logger.info("Processing single run: %s", args.run)
    else:
        all_runs = _discover_runs(published_root, model)
        if not all_runs:
            logger.error("No published %s runs found under %s", model, published_root)
            sys.exit(3)

    # ── Safety warning for full-run mode ────────────────────────────
    if not args.run and not args.sample_limit:
        logger.warning(
            "No --run or --sample-limit provided — will process ALL %d retained "
            "%s run(s). This may run for a long time. Use --sample-limit for "
            "a quick sanity check or --run to target a single run.",
            len(all_runs), model,
        )

    # ── Anchors ─────────────────────────────────────────────────────
    anchors = _load_anchor_points(data_root)
    logger.info("Loaded %d anchor points", len(anchors))
    if not anchors:
        logger.error("No anchor points available")
        sys.exit(1)

    # ── Tolerance groups ────────────────────────────────────────────
    group_index = _build_group_index(model, scope)
    group1 = [v for v, g in group_index.items() if g == 1]
    group2 = [v for v, g in group_index.items() if g == 2]
    group3 = [v for v, g in group_index.items() if g == 3]
    group4 = [v for v, g in group_index.items() if g == 4]
    logger.info(
        "Tolerance groups: Group-1=%d vars, Group-2=%d vars, Group-3=%d vars, Group-4=%d vars",
        len(group1), len(group2), len(group3), len(group4),
    )
    logger.debug("Group 1: %s", ", ".join(group1))
    logger.debug("Group 2: %s", ", ".join(group2))
    logger.debug("Group 3: %s", ", ".join(group3))
    logger.debug("Group 4: %s", ", ".join(group4))

    # ── Estimate ────────────────────────────────────────────────────
    estimate = _estimate_comparison_count(published_root, model, scope, all_runs, anchors)
    logger.info(
        "Estimated comparison count: ~%d (runs=%d × max vars=%d × frames × anchors=%d)",
        estimate, len(all_runs), len(scope), len(anchors),
    )

    # ── Core comparison ────────────────────────────────────────────
    stats = _run_comparison(
        published_root,
        model,
        scope,
        all_runs,
        anchors,
        group_index,
        log_path=Path(args.log_divergences) if args.log_divergences else None,
        sample_limit=args.sample_limit,
    )

    # ── Benchmarks ─────────────────────────────────────────────────
    benchmarks: list[BenchmarkResult] = []
    if not args.skip_benchmarks:
        logger.info("Running performance benchmarks...")
        bench_run = all_runs[-1]
        benchmarks = _run_benchmarks(
            published_root, model, anchors, bench_run, args.workers,
            scope, group_index,
        )
        for bm in benchmarks:
            logger.info(
                "Bench %s: COG avg=%.2f ms  p95=%.2f ms | "
                "Binary avg=%.2f ms  p95=%.2f ms",
                bm.label,
                bm.cog_mean_s * 1000, bm.cog_p95_s * 1000,
                bm.binary_mean_s * 1000, bm.binary_p95_s * 1000,
            )

    # ── Summary ────────────────────────────────────────────────────
    report = _metrics_report(stats, benchmarks)
    report["anchor_count"] = len(anchors)
    report["scope_variable_count"] = len(scope)
    report["scope_variables"] = scope
    report["excluded_non_buildable_variables"] = excluded_non_buildable
    report["excluded_dead_alias_variables"] = excluded_dead_alias
    report["excluded_uncataloged_variables"] = excluded_uncataloged
    if args.vars:
        report["vars_filter"] = scope
    report["tolerance_groups"] = {
        "group_1": group1,
        "group_2": group2,
        "group_3": group3,
        "group_4": group4,
    }
    report["tolerance_group_descriptions"] = {
        "group_1": "No display-prep entry; continuous; exact within scale/2.",
        "group_2": "Display-prep entry with upscale_factor > 1; continuous; boundary-tolerant numeric comparison.",
        "group_3": "Display-prep entry with upscale_factor > 1 and categorical_nearest=True; boundary-tolerant integer-category comparison.",
        "group_4": "Display-prep entry with categorical_nearest=True and no upscale; strict integer-category equality.",
    }
    if args.sample_limit:
        report["sample_limit"] = args.sample_limit

    if args.summary:
        Path(args.summary).write_text(json.dumps(report, indent=2) + "\n")
        logger.info("Summary written to %s", args.summary)
    else:
        print(json.dumps(report, indent=2))

    # ── Exit code ──────────────────────────────────────────────────
    sys.exit(_exit_code(stats, missing_run))


if __name__ == "__main__":
    main()
