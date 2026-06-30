#!/usr/bin/env python3
"""Phase D canary: shadow-compare COG vs binary sampling for GFS.

Runs against real published data, samples anchor city points across all
GFS variables and forecast hours, logs divergences with full context,
and records performance metrics per the migration plan Section 3 / Layer 3.

Usage::

    python backend/scripts/canary_binary_sampler.py \\
        --data-root /opt/cartosky/data \\
        --log-divergences divergences.jsonl \\
        --summary summary.json \\
        --workers 8

The script discovers all retained GFS runs under the published root,
enumerates every variable in ``_PACKING_BY_MODEL_VAR`` for GFS (including
the four loop-registered precip-anomaly variables), samples anchor-city
coordinates through both the COG path and the binary path, and writes a
JSON-lines divergence log plus a summary JSON.

Tolerance groups::

  Group 1 – no display-prep upscale → divergence when
            |cog - binary| > scale/2 + epsilon.
  Group 2 – continuous 3× upscale → divergence expected near gradients;
            pass condition is "spatially explainable and bounded."
  Group 3 – categorical-nearest 3× upscale → divergences only at genuine
            class-boundary pixels (rounded integer comparison).

Performance benchmarks are run after the core comparison: single-point,
100-point batch, 1000-point batch, and a full-meteogram simulation
(85–105 sequential frames), recording latency for both substrates.

Exit codes::
  0 – clean pass (no blocking divergences, comparisons were performed)
  1 – usage / data-root missing
  2 – requested run not found
  3 – no comparisons performed (no usable frames)
  4 – blocking: binary frame/meta file missing, Group 1 divergence,
      or Group 3 categorical divergence
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

from app.services.grid import (
    GRID_DTYPE,
    _PACKING_BY_MODEL_VAR,
    grid_dtype,
    grid_frame_filename,
)
from app.services.grid_display_prep import grid_display_prep_config
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
MODEL = "gfs"
RUN_ID_RE = re.compile(r"^\d{8}_\d{2}z$")

SCALE_HALF_EPSILON = 1e-4

# Group 2 bounded-tolerance constant (matches parity-test threshold).
GROUP2_TOLERANCE = 0.55

# GFS variable scope (read from packing table — includes
# loop-registered precip-anomaly vars).
GFS_SCOPE = sorted(
    var for (mdl, var) in _PACKING_BY_MODEL_VAR if mdl == MODEL
)


# ── Helper: per-variable packing ────────────────────────────────────


def _packing(var: str) -> dict[str, Any]:
    return _PACKING_BY_MODEL_VAR[(MODEL, var)]


def _packing_dtype(var: str) -> str:
    return grid_dtype(str(_packing(var).get("dtype") or GRID_DTYPE))


def _binary_frame_filename(var: str, fh: int) -> str:
    """Resolve the binary frame filename from the variable's packing dtype."""
    return grid_frame_filename(fh, dtype=_packing_dtype(var))


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


def _classify_variable(var: str) -> int:
    """Return tolerance group (1, 2, or 3) for a GFS variable."""
    config = grid_display_prep_config(MODEL, var)
    if config is None:
        return 1
    if config.categorical_nearest:
        return 3
    if config.upscale_factor > 1:
        return 2
    return 1


def _build_group_index() -> dict[str, int]:
    return {var: _classify_variable(var) for var in GFS_SCOPE}


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


def _discover_runs(published_root: Path) -> list[str]:
    """Return sorted list of GFS run ids with published data."""
    gfs_dir = published_root / MODEL
    if not gfs_dir.is_dir():
        return []
    runs: list[str] = []
    for entry in sorted(gfs_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not RUN_ID_RE.match(entry.name):
            continue
        runs.append(entry.name)
    return runs


def _discover_frames_for_run_var(
    published_root: Path,
    run: str,
    var: str,
) -> list[int]:
    """Return forecast hours where the value COG exists.

    Binary frame existence is verified at sample time rather than here,
    so frame-discovery can report missing-binary-frame files separately.
    """
    var_dir = published_root / MODEL / run / var
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
    run: str,
    var: str,
    fh: int,
    point: AnchorPoint,
) -> SampleResult:
    """Sample one point through both COG and binary paths, timing each.

    Distinguishes file-missing from no-value samples so the caller can
    treat infrastructure problems separately from legitimate nodata.
    """
    var_dir = published_root / MODEL / run / var
    cog_path = var_dir / f"fh{fh:03d}.val.cog.tif"
    grid_dir = var_dir / "grid"
    bin_name = _binary_frame_filename(var, fh)
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
                model=MODEL, var=var,
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


def _is_divergent(result: SampleResult, group: int, var: str) -> bool:
    """Does this result constitute a reportable divergence?

    Only called when neither substrate has a missing file.
    Both-values-None (matching nodata) is *not* a divergence.

    Group 1 (no upscale):  |cog - binary| > scale/2 + epsilon.
    Group 2 (continuous 3×): |cog - binary| > GROUP2_TOLERANCE.
    Group 3 (categorical 3×): int(round(cog)) != int(round(binary)).
    """
    # Both absent (matching nodata / out-of-bounds) — agreed.
    if result.cog_value is None and result.binary_value is None:
        return False

    # One present, one absent — structural disagreement.
    if result.cog_value is None or result.binary_value is None:
        return True

    if group == 1:
        scale = float(_packing(var)["scale"])
        threshold = scale / 2 + SCALE_HALF_EPSILON
        return abs(result.cog_value - result.binary_value) > threshold

    if group == 3:
        return int(round(result.cog_value)) != int(round(result.binary_value))

    # Group 2 — continuous 3× upscale: bounded tolerance.
    return abs(result.cog_value - result.binary_value) > GROUP2_TOLERANCE


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
    runs_to_process: list[str],
    anchors: list[AnchorPoint],
    group_index: dict[str, int],
    log_path: Path | None,
    *,
    sample_limit: int | None = None,
) -> dict[str, Any]:
    """Core comparison loop.  Returns summary statistics dict."""
    logger.info("Processing %d GFS run(s): %s", len(runs_to_process),
                ", ".join(runs_to_process))

    divergence_fh = None
    if log_path:
        divergence_fh = open(log_path, "w", encoding="utf-8")

    stats: dict[str, Any] = {
        "model": MODEL,
        "runs_found": len(runs_to_process),
        "runs": runs_to_process,
    }

    total = 0
    missing_cog_files = 0
    missing_binary_frame_files = 0
    missing_binary_meta_files = 0
    cog_no_value = 0
    binary_no_value = 0
    total_cog_latencies: list[float] = []
    total_binary_latencies: list[float] = []
    group_counts: dict[int, int] = defaultdict(int)
    divergences = DivergenceAccumulator()
    vars_with_frames: set[str] = set()

    try:
        for run in runs_to_process:
            logger.info("Processing run: %s", run)
            for var in GFS_SCOPE:
                fh_values = _discover_frames_for_run_var(published_root, run, var)
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

                        result = _sample_one(published_root, run, var, fh, point)
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

                        # Divergence check: only when neither side has a missing
                        # file.  (Both-values-None is already handled inside
                        # _is_divergent.)
                        if not result.any_file_missing:
                            if _is_divergent(result, group, var):
                                divergences.record(run, var, group)
                                div = Divergence(
                                    model=MODEL,
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

    stats.update({
        "total_comparisons": total,
        "vars_with_frames": len(vars_with_frames),
        "missing_cog_files": missing_cog_files,
        "missing_binary_frame_files": missing_binary_frame_files,
        "missing_binary_meta_files": missing_binary_meta_files,
        "cog_no_value_samples": cog_no_value,
        "binary_no_value_samples": binary_no_value,
        "divergences": divergences.asdict(),
        "comparisons_by_group": dict(group_counts),
        "cog_latency_s": _latency_summary(total_cog_latencies),
        "binary_latency_s": _latency_summary(total_binary_latencies),
    })

    logger.info(
        "Comparison complete: %d samples, %d divergences "
        "(COG missing=%d bin-frame-missing=%d bin-meta-missing=%d "
        "COG-noval=%d bin-noval=%d)",
        total, divergences.total,
        missing_cog_files, missing_binary_frame_files, missing_binary_meta_files,
        cog_no_value, binary_no_value,
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
    anchors: list[AnchorPoint],
    target_run: str,
    workers: int,
) -> list[BenchmarkResult]:
    """Run the four Phase D performance benchmarks."""
    bench_var = "tmp2m"
    fh_values = _discover_frames_for_run_var(published_root, target_run, bench_var)
    if not fh_values:
        logger.warning("No frames for benchmark var %s in run %s", bench_var, target_run)
        return []

    fh0 = fh_values[0]
    anchor = anchors[0]
    var_dir = published_root / MODEL / target_run / bench_var
    cog_path = var_dir / f"fh{fh0:03d}.val.cog.tif"
    grid_dir = var_dir / "grid"
    bin_name = _binary_frame_filename(bench_var, fh0)
    frame_path = grid_dir / bin_name
    meta_path = grid_dir / f"fh{fh0:03d}.l0.meta.json"

    results: list[BenchmarkResult] = []

    # 1. Single-point
    results.append(_bench_single(cog_path, frame_path, meta_path, anchor, bench_var))

    # 2. 100-point batch
    points_100 = _scatter_points(anchors, 100, published_root, target_run, bench_var)
    results.append(_bench_batch(cog_path, frame_path, meta_path, points_100,
                                bench_var, label="100-point batch"))

    # 3. 1000-point batch
    points_1000 = _scatter_points(anchors, 1000, published_root, target_run, bench_var)
    results.append(_bench_batch(cog_path, frame_path, meta_path, points_1000,
                                bench_var, label="1000-point batch"))

    # 4. Meteogram simulation: 85–105 sequential frames, single point
    results.append(_bench_meteogram(published_root, target_run, bench_var, fh_values,
                                    anchor, workers))

    return results


def _bench_single(
    cog_path: Path,
    frame_path: Path,
    meta_path: Path,
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
                model=MODEL, var=var,
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
                model=MODEL, var=var,
                lat=pt.lat, lon=pt.lon,
            )
        except Exception:
            pass
        bin_times.append(time.perf_counter() - t0)

    return _build_benchmark(label, cog_times, bin_times)


def _bench_meteogram(
    published_root: Path,
    run: str,
    var: str,
    fh_values: list[int],
    anchor: AnchorPoint,
    workers: int,
) -> BenchmarkResult:
    """Sample 85–105 sequential frames (one point) via both paths."""
    fh_subset = fh_values[:105] if len(fh_values) >= 105 else fh_values
    if len(fh_subset) < 85:
        logger.warning("Only %d frames for meteogram benchmark (need 85+)",
                       len(fh_subset))
    label = f"meteogram ({len(fh_subset)} frames)"

    cog_times: list[float] = []
    bin_times: list[float] = []

    var_dir = published_root / MODEL / run / var
    grid_dir = var_dir / "grid"

    for fh in fh_subset:
        c_path = var_dir / f"fh{fh:03d}.val.cog.tif"
        bin_name = _binary_frame_filename(var, fh)
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
                    model=MODEL, var=var,
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
    run: str,
    var: str,
) -> list[AnchorPoint]:
    """Generate ``count`` points scattered across the grid domain."""
    if len(anchors) >= count:
        return anchors[:count]

    var_dir = published_root / MODEL / run / var
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
    runs: list[str],
    anchors: list[AnchorPoint],
) -> int:
    """Estimate total comparisons without sampling."""
    estimate = 0
    for run in runs:
        for var in GFS_SCOPE:
            fh_values = _discover_frames_for_run_var(published_root, run, var)
            if fh_values:
                estimate += len(fh_values) * len(anchors)
    return estimate


# ── Pass / fail logic ───────────────────────────────────────────────


def _exit_code(
    stats: dict[str, Any],
    missing_run: bool,
) -> int:
    """Determine exit code from canary results.

    Returns:
        0 – clean pass
        2 – requested run missing
        3 – no comparisons performed
        4 – blocking: binary frame/meta file missing, Group 1 divergence,
            or Group 3 categorical divergence
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
            "for ptype_intensity (after rounding)",
            group3_div,
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
        "model": MODEL,
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
        "divergences": stats.get("divergences", {}),
        "latency_cog_s": stats.get("cog_latency_s", {}),
        "latency_binary_s": stats.get("binary_latency_s", {}),
        "benchmarks": [b.asdict() for b in benchmarks],
    }
    return report


# ── Entry point ─────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase D canary: shadow-compare COG vs binary sampling for GFS.",
        parents=[_early_parser],
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

    data_root = Path(args.data_root).expanduser().resolve()
    published_root = data_root / "published"
    if not published_root.is_dir():
        logger.error("Published root not found: %s", published_root)
        sys.exit(1)

    # ── Run selection ───────────────────────────────────────────────
    missing_run = False
    if args.run:
        run_dir = published_root / MODEL / args.run
        if not run_dir.is_dir() or not RUN_ID_RE.match(args.run):
            logger.error("Requested run not found or invalid: %s", args.run)
            sys.exit(2)
        all_runs = [args.run]
        logger.info("Processing single run: %s", args.run)
    else:
        all_runs = _discover_runs(published_root)
        if not all_runs:
            logger.error("No published GFS runs found under %s", published_root)
            sys.exit(3)

    # ── Safety warning for full-run mode ────────────────────────────
    if not args.run and not args.sample_limit:
        logger.warning(
            "No --run or --sample-limit provided — will process ALL %d retained "
            "GFS run(s). This may run for a long time. Use --sample-limit for "
            "a quick sanity check or --run to target a single run.",
            len(all_runs),
        )

    # ── Anchors ─────────────────────────────────────────────────────
    anchors = _load_anchor_points(data_root)
    logger.info("Loaded %d anchor points", len(anchors))
    if not anchors:
        logger.error("No anchor points available")
        sys.exit(1)

    # ── Tolerance groups ────────────────────────────────────────────
    group_index = _build_group_index()
    group1 = [v for v, g in group_index.items() if g == 1]
    group2 = [v for v, g in group_index.items() if g == 2]
    group3 = [v for v, g in group_index.items() if g == 3]
    logger.info("Tolerance groups: Group-1=%d vars, Group-2=%d vars, Group-3=%d vars",
                len(group1), len(group2), len(group3))
    logger.debug("Group 1: %s", ", ".join(group1))
    logger.debug("Group 2: %s", ", ".join(group2))
    logger.debug("Group 3: %s", ", ".join(group3))

    # ── Estimate ────────────────────────────────────────────────────
    estimate = _estimate_comparison_count(published_root, all_runs, anchors)
    logger.info(
        "Estimated comparison count: ~%d (runs=%d × max vars=%d × frames × anchors=%d)",
        estimate, len(all_runs), len(GFS_SCOPE), len(anchors),
    )

    # ── Core comparison ────────────────────────────────────────────
    stats = _run_comparison(
        published_root,
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
        benchmarks = _run_benchmarks(published_root, anchors, bench_run, args.workers)
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
    report["scope_variable_count"] = len(GFS_SCOPE)
    report["scope_variables"] = GFS_SCOPE
    report["tolerance_groups"] = {
        "group_1": group1,
        "group_2": group2,
        "group_3": group3,
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
