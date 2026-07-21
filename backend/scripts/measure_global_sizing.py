#!/usr/bin/env python3
"""Disk-sizing spike: measure a full GLOBAL-domain run for one model.

Production publishes NA-domain artifacts for gfs/ecmwf/aifs/aigfs. This
standalone script measures what one full run per model would cost at GLOBAL
domain — raw GRIB subsets plus converted grid artifacts — so a block-storage
purchase can be sized.

There is NO "global" region in the codebase. This script injects one
IN-MEMORY at runtime (process-local monkeypatch; nothing on disk or in the
scheduler is altered) and drives the real ``build_frame`` conversion pipeline
with ``region="global"`` for the exact variable/forecast-hour set that
production builds for NA today.

Isolation contract (this script must be INCAPABLE of touching live data):
  * data root  → ``{dev_root}/data``           (NEVER /opt/cartosky/data)
  * herbie dir → ``{dev_root}/herbie/{model}``  (NEVER /opt/cartosky/herbie_cache_ssd)
  * reports    → ``{dev_root}/reports``
  * refuses to run as root.

The env vars that steer the data root and the Herbie cache are set BEFORE any
app (or herbie) module is imported — herbie latches HERBIE_SAVE_DIR when its
config first loads, so ordering matters. All argument parsing happens first so
``--help`` works on any machine with no prod env and no network.

Usage examples::

    # Auto-pick the latest ready gfs cycle (>= 6h old) and measure it:
    python backend/scripts/measure_global_sizing.py --model gfs

    # ECMWF full-horizon (00z/12z) run:
    python backend/scripts/measure_global_sizing.py --model ecmwf --cycle-type full

    # Offline sanity check — resolve plugin, inject region, print the plan, exit:
    python backend/scripts/measure_global_sizing.py --model gfs \\
        --dev-root /tmp/sizing --plan-only

Exit codes::
  0   measurement completed (per-frame failures are recorded, not fatal)
  1   usage / safety-guard / environment error
  2   no ready run found (readiness probe never passed)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Path setup (mirrors canary_binary_sampler.py / ensemble_member_sizing_spike.py) ──
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GLOBAL_REGION_ID = "global"
# Global extent in EPSG:3857 (±180°, ±85.051129°). Square domain.
GLOBAL_BBOX_3857 = (
    -20037508.342789244,
    -20037508.342789244,
    20037508.342789244,
    20037508.342789244,
)
GLOBAL_BBOX_WGS84 = (-180.0, -85.051129, 180.0, 85.051129)

LIVE_DATA_ROOT = Path("/opt/cartosky/data")
LIVE_HERBIE_CACHE = Path("/opt/cartosky/herbie_cache_ssd")

logger = logging.getLogger("measure_global_sizing")


# ---------------------------------------------------------------------------
# Argument parsing (runs BEFORE any heavy/app import so --help works anywhere)
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="measure_global_sizing.py",
        description="Measure one full GLOBAL-domain run's disk cost for a model.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--model",
        required=True,
        choices=["gfs", "ecmwf", "aifs", "aigfs"],
        help="Model to measure.",
    )
    parser.add_argument(
        "--run",
        default=None,
        help="Explicit cycle YYYYMMDDHH (e.g. 2026072100). "
        "If omitted, auto-pick the latest cycle >= --min-age-hours old that "
        "passes an upstream readiness probe.",
    )
    parser.add_argument(
        "--cycle-type",
        choices=["full", "short"],
        default=None,
        help="ECMWF only, required when --run is omitted: full -> latest "
        "00z/12z, short -> latest 06z/18z.",
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=6.0,
        help="When auto-picking a run, only consider cycles at least this old.",
    )
    parser.add_argument(
        "--dev-root",
        default="/opt/cartosky-dev",
        help="Confinement root. data root, herbie dir and reports all live here.",
    )
    parser.add_argument(
        "--limit-vars",
        type=int,
        default=None,
        help="Smoke mode: measure only the first N variables.",
    )
    parser.add_argument(
        "--limit-fhs",
        type=int,
        default=None,
        help="Smoke mode: measure only the first N forecast hours per variable.",
    )
    parser.add_argument(
        "--frame-delay",
        type=float,
        default=0.0,
        help="Seconds to sleep between frame builds. Courtesy pacing for "
        "NOMADS-only models (aigfs) whose anti-abuse throttle trips on the "
        "unpaced measurement loop; leave 0 for AWS/ECMWF-sourced models.",
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep converted staging output after measuring (default: delete).",
    )
    parser.add_argument(
        "--keep-grib",
        action="store_true",
        help="Keep the dev herbie dir for this model after measuring "
        "(default: delete).",
    )
    parser.add_argument(
        "--compare-root",
        default="/opt/cartosky/data",
        help="READ-ONLY: du of {compare-root}/published/{model}/{run_id} for the "
        "NA same-run comparison. Skipped gracefully if that run dir is absent.",
    )
    parser.add_argument(
        "--baseline-root",
        default=None,
        help="READ-ONLY root for ERA5 climatology baselines used by anomaly "
        "vars (e.g. tmp2m_anom, precip_*_anom). climatology is read-only, so "
        "this points baseline reads at the live tree while all writes stay in "
        "--dev-root. Defaults to --compare-root. Anomaly frames fail gracefully "
        "if baselines are absent.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Resolve plugin, inject the global region, print the planned grid "
        "dims / var list / FH list, and exit WITHOUT fetching or building. "
        "Works offline (no readiness probe).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Debug-level logging.",
    )
    return parser


# Parse EARLY. On --help/usage errors argparse calls sys.exit before any heavy
# import executes below, so the command works on any machine.
ARGS = build_parser().parse_args()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)


_setup_logging(ARGS.verbose)


# ---------------------------------------------------------------------------
# Safety guards + env wiring (must precede any app / herbie import)
# ---------------------------------------------------------------------------

def _is_within(path: Path, ancestor: Path) -> bool:
    try:
        path.resolve().relative_to(ancestor.resolve())
        return True
    except ValueError:
        return False


def _fail(message: str) -> None:
    logger.error(message)
    raise SystemExit(1)


def _resolve_paths(dev_root: str, model: str) -> tuple[Path, Path, Path]:
    """Return (data_root, herbie_model_dir, reports_dir), all under dev_root."""
    root = Path(dev_root).expanduser().resolve()
    data_root = root / "data"
    herbie_model_dir = root / "herbie" / model
    reports_dir = root / "reports"
    return data_root, herbie_model_dir, reports_dir


def _enforce_safety(data_root: Path, herbie_model_dir: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        _fail("Refusing to run as root (euid==0). Run as the cartosky user.")

    resolved_data = data_root.resolve()
    if resolved_data == LIVE_DATA_ROOT.resolve() or _is_within(resolved_data, LIVE_DATA_ROOT):
        _fail(
            f"SAFETY: data root {resolved_data} is the live data root "
            f"{LIVE_DATA_ROOT} or inside it. Refusing."
        )

    herbie_base = herbie_model_dir.resolve()
    if herbie_base == LIVE_HERBIE_CACHE.resolve() or _is_within(herbie_base, LIVE_HERBIE_CACHE):
        _fail(
            f"SAFETY: herbie dir {herbie_base} is the live Herbie cache "
            f"{LIVE_HERBIE_CACHE} or inside it. Refusing."
        )


DATA_ROOT, HERBIE_MODEL_DIR, REPORTS_DIR = _resolve_paths(ARGS.dev_root, ARGS.model)
_enforce_safety(DATA_ROOT, HERBIE_MODEL_DIR)

# Create the confined dirs and steer every downstream env-driven path at them.
DATA_ROOT.mkdir(parents=True, exist_ok=True)
HERBIE_MODEL_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

os.environ["CARTOSKY_DATA_ROOT"] = str(DATA_ROOT)
# Both names: the fetch module prefers CARTOSKY_HERBIE_SAVE_DIR, but herbie
# itself latches HERBIE_SAVE_DIR when its config loads.
os.environ["CARTOSKY_HERBIE_SAVE_DIR"] = str(HERBIE_MODEL_DIR)
os.environ["HERBIE_SAVE_DIR"] = str(HERBIE_MODEL_DIR)
os.environ.setdefault("GDAL_CACHEMAX", "256")

# herbie must not have been imported before we set HERBIE_SAVE_DIR.
if any(name == "herbie" or name.startswith("herbie.") for name in sys.modules):
    logger.warning(
        "herbie was imported before cache isolation — HERBIE_SAVE_DIR override "
        "may not take effect."
    )

logger.info("Confined data root : %s", DATA_ROOT)
logger.info("Confined herbie dir: %s", HERBIE_MODEL_DIR)
logger.info("Reports dir        : %s", REPORTS_DIR)


# ---------------------------------------------------------------------------
# App imports (safe now that env is wired; deferred so --help stays import-free)
# ---------------------------------------------------------------------------

from app.models.base import RegionSpec  # noqa: E402
from app.models.registry import MODEL_REGISTRY  # noqa: E402
from app.models.ecmwf import ECMWF_SHORT_CUTOFF_CYCLE_HOURS  # noqa: E402
from app.config import binary_sampling_enabled  # noqa: E402
from app.services import climatology  # noqa: E402
from app.services.builder import cog_writer  # noqa: E402
from app.services.builder.cog_writer import (  # noqa: E402
    compute_transform_and_shape,
    get_grid_params,
)
from app.services.builder.derive import FetchContext, destroy_fetch_context  # noqa: E402
from app.services.builder.fetch import _range_throttle_remaining  # noqa: E402
from app.services.builder.pipeline import build_frame  # noqa: E402
from app.services.grid import grid_dir_for_run_root  # noqa: E402
from app.services.process_memory import peak_rss_bytes  # noqa: E402
from app.services.scheduler import (  # noqa: E402
    _align_to_cycle_hour,
    _companion_vars_for_var,
    _probe_run_exists,
    _resolve_vars_to_schedule,
    _runtime_var_id,
    _var_default_ensemble_view,
)


GIB = 1024.0 ** 3


# ---------------------------------------------------------------------------
# Global-region injection (process-local monkeypatch of in-memory tables)
# ---------------------------------------------------------------------------

def inject_global_region(plugin: Any, model: str) -> float:
    """Register region ``"global"`` in every in-memory table build_frame /
    warp consult, using this model's ``"na"`` grid resolution.

    Patch points (verified against the code, not the scout notes):
      1. ``plugin.regions["global"]`` — build_frame validates the region via
         ``resolved_plugin.get_region(region)`` (pipeline.py:1572), which reads
         this mapping. Frozen dataclass, but the mapping object is mutable.
      2. ``cog_writer.REGION_BBOX_3857["global"]`` — ``get_grid_params`` (used
         by ``warp_to_target_grid`` and the COG/validate paths) looks the bbox
         up here.
      3. ``plugin.capabilities.grid_meters_by_region["global"]`` — the
         authoritative grid-resolution source consulted by
         ``_grid_meters_from_capabilities``; copies the model's "na" value.
         (``cog_writer.TARGET_GRID_METERS`` is only a legacy fallback and is
         also populated for belt-and-suspenders parity.)

    Returns the global grid resolution in meters.
    """
    caps = getattr(plugin, "capabilities", None)
    grid_map = getattr(caps, "grid_meters_by_region", None) if caps is not None else None
    na_grid_m: float | None = None
    if isinstance(grid_map, dict):
        na_grid_m = grid_map.get("na")
    if na_grid_m is None:
        # Legacy fallback table.
        model_grids = cog_writer.TARGET_GRID_METERS.get(model, {})
        na_grid_m = model_grids.get("na")
    if na_grid_m is None:
        raise RuntimeError(
            f"Model {model!r} has no 'na' grid resolution to copy for global."
        )
    na_grid_m = float(na_grid_m)

    # (1) plugin region table
    plugin.regions[GLOBAL_REGION_ID] = RegionSpec(
        id=GLOBAL_REGION_ID,
        name="Global",
        bbox_wgs84=GLOBAL_BBOX_WGS84,
        clip=False,
    )
    # (2) module bbox table (cog_writer)
    cog_writer.REGION_BBOX_3857[GLOBAL_REGION_ID] = GLOBAL_BBOX_3857
    cog_writer.REGION_BBOX_4326[GLOBAL_REGION_ID] = GLOBAL_BBOX_WGS84
    # (3) grid resolution — capabilities (authoritative) + legacy fallback
    if isinstance(grid_map, dict):
        grid_map[GLOBAL_REGION_ID] = na_grid_m
    cog_writer.TARGET_GRID_METERS.setdefault(model, {})[GLOBAL_REGION_ID] = na_grid_m

    # Sanity: the path build_frame/warp actually take must now resolve.
    bbox, grid_m = get_grid_params(model, GLOBAL_REGION_ID)
    if plugin.get_region(GLOBAL_REGION_ID) is None:
        raise RuntimeError("global region injection failed: get_region returned None")
    logger.info(
        "Injected global region for %s: grid=%.1fm bbox_3857=%s",
        model,
        grid_m,
        bbox,
    )
    return grid_m


# ---------------------------------------------------------------------------
# Run resolution
# ---------------------------------------------------------------------------

def _parse_run_arg(run: str) -> datetime:
    run = run.strip()
    if len(run) != 10 or not run.isdigit():
        _fail(f"Invalid --run {run!r}; expected YYYYMMDDHH (10 digits).")
    return datetime(
        int(run[0:4]), int(run[4:6]), int(run[6:8]), int(run[8:10]),
        tzinfo=timezone.utc,
    )


def _cadence_hours(plugin: Any) -> int:
    cfg = plugin.run_discovery_config() if hasattr(plugin, "run_discovery_config") else {}
    try:
        return max(1, int(cfg.get("cycle_cadence_hours", 6)))
    except (TypeError, ValueError):
        return 6


def _cycle_type_hours(model: str, cycle_type: str | None, cadence: int) -> set[int] | None:
    """Allowed cycle hours for the given --cycle-type (ecmwf only)."""
    if model != "ecmwf" or cycle_type is None:
        return None
    short = set(int(h) for h in ECMWF_SHORT_CUTOFF_CYCLE_HOURS)
    all_hours = set(range(0, 24, cadence))
    if cycle_type == "short":
        return short
    return all_hours - short  # "full" == 00z/12z


def _run_id(run_dt: datetime) -> str:
    return run_dt.strftime("%Y%m%d_%Hz")


def resolve_run(
    plugin: Any,
    model: str,
    *,
    run_arg: str | None,
    cycle_type: str | None,
    min_age_hours: float,
    probe: bool,
) -> datetime:
    """Resolve the cycle to measure.

    ``probe=False`` (plan-only) never touches the network — it picks the newest
    aligned candidate matching the age + cycle-type filters. ``probe=True``
    additionally requires an upstream readiness probe to pass.
    """
    if run_arg:
        run_dt = _parse_run_arg(run_arg)
        logger.info("Using explicit run %s", _run_id(run_dt))
        return run_dt

    if model == "ecmwf" and cycle_type is None:
        _fail("--cycle-type {full,short} is required for ecmwf when --run is omitted.")

    cadence = _cadence_hours(plugin)
    allowed = _cycle_type_hours(model, cycle_type, cadence)
    probe_var = None
    if hasattr(plugin, "resolve_probe_var_key"):
        probe_var = plugin.resolve_probe_var_key(None)
    probe_var = probe_var or "tmp2m"

    now = datetime.now(timezone.utc)
    base = _align_to_cycle_hour(now, cadence)
    max_candidates = 24
    for step in range(max_candidates):
        candidate = base - timedelta(hours=step * cadence)
        age_h = (now - candidate).total_seconds() / 3600.0
        if age_h < min_age_hours:
            continue
        if allowed is not None and candidate.hour not in allowed:
            continue
        if not probe:
            logger.info(
                "Plan-only candidate %s (age %.1fh; no readiness probe)",
                _run_id(candidate), age_h,
            )
            return candidate
        logger.info("Probing candidate %s (age %.1fh)...", _run_id(candidate), age_h)
        try:
            ready = _probe_run_exists(plugin=plugin, run_dt=candidate, probe_var=probe_var)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Probe error for %s: %s", _run_id(candidate), exc)
            ready = False
        if ready:
            logger.info("Run %s is ready upstream.", _run_id(candidate))
            return candidate

    logger.error(
        "No ready run found in the last %d candidate cycles (cadence=%dh, "
        "min_age=%.1fh, cycle_type=%s).",
        max_candidates, cadence, min_age_hours, cycle_type,
    )
    raise SystemExit(2)


# ---------------------------------------------------------------------------
# Target (var, fh) enumeration — mirrors scheduler._scheduled_targets_for_cycle
# ---------------------------------------------------------------------------

def enumerate_targets(
    plugin: Any,
    cycle_hour: int,
    *,
    limit_vars: int | None,
    limit_fhs: int | None,
) -> tuple[list[str], list[tuple[str, int]]]:
    """Return (vars_to_build, ordered unique (var_key, fh) targets).

    Mirrors ``scheduler._resolve_vars_to_schedule`` (buildable filter) +
    ``scheduler._scheduled_targets_for_cycle`` (per-var scheduled FHs plus
    companion vars at the same FHs), which is exactly the set production builds
    for NA today. Region is irrelevant to that (var, fh) set. ``--limit-*``
    trims for smoke runs.
    """
    vars_to_build = _resolve_vars_to_schedule(plugin, [])
    if limit_vars is not None:
        vars_to_build = vars_to_build[: max(0, limit_vars)]

    targets: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    def _append(var_key: str, fh: int) -> None:
        key = (var_key, int(fh))
        if key in seen:
            return
        seen.add(key)
        targets.append(key)

    for var_id in vars_to_build:
        fhs = list(plugin.scheduled_fhs_for_var(var_id, cycle_hour))
        if limit_fhs is not None:
            fhs = fhs[: max(0, limit_fhs)]
        normalized_var = plugin.normalize_var_id(var_id)
        for fh in fhs:
            _append(normalized_var, int(fh))
            for companion in _companion_vars_for_var(plugin, normalized_var):
                _append(plugin.normalize_var_id(companion), int(fh))

    return vars_to_build, targets


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

def _dir_size_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            fp = Path(dirpath) / name
            try:
                total += fp.stat().st_size
            except OSError:
                continue
    return total


_SUFFIX_BUCKETS = (".bin", ".meta.json", ".tif", ".geojson", ".json")


def _suffix_bucket(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".meta.json"):
        return ".meta.json"
    for suffix in (".bin", ".tif", ".geojson", ".json"):
        if lower.endswith(suffix):
            return suffix
    return "other"


def measure_staging(run_root: Path) -> dict[str, Any]:
    """Walk the staging run dir: total, per-var totals, suffix breakdown."""
    per_var: dict[str, int] = {}
    per_suffix: dict[str, int] = {k: 0 for k in _SUFFIX_BUCKETS}
    per_suffix["other"] = 0
    total = 0
    file_count = 0
    if run_root.exists():
        for var_dir in sorted(p for p in run_root.iterdir() if p.is_dir()):
            var_total = 0
            for dirpath, _dirnames, filenames in os.walk(var_dir):
                for name in filenames:
                    fp = Path(dirpath) / name
                    try:
                        size = fp.stat().st_size
                    except OSError:
                        continue
                    var_total += size
                    total += size
                    file_count += 1
                    per_suffix[_suffix_bucket(name)] += size
            per_var[var_dir.name] = var_total
    return {
        "total_bytes": total,
        "file_count": file_count,
        "per_var_bytes": per_var,
        "per_suffix_bytes": per_suffix,
    }


def read_produced_grid_dims(run_root: Path) -> tuple[int, int] | None:
    """Read (width, height) from the first grid .meta.json actually produced."""
    if not run_root.exists():
        return None
    for dirpath, _dirnames, filenames in os.walk(run_root):
        for name in sorted(filenames):
            if name.endswith(".meta.json") and "contour" not in name:
                try:
                    meta = json.loads((Path(dirpath) / name).read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                w = meta.get("width")
                h = meta.get("height")
                if isinstance(w, int) and isinstance(h, int):
                    return w, h
    return None


def frame_outputs_exist(run_root: Path, var_key: str, fh: int) -> bool:
    """Idempotent-resume check: a frame is 'done' when its sidecar json plus a
    (non-contour) grid binary and its meta exist.

    Globs the grid dir rather than reconstructing the filename, because the
    grid-binary name embeds a per-var packing dtype token.
    """
    sidecar = run_root / var_key / f"fh{int(fh):03d}.json"
    if not sidecar.exists():
        return False
    grid_dir = grid_dir_for_run_root(run_root, var_key)
    if not grid_dir.is_dir():
        return False
    prefix = f"fh{int(fh):03d}.l"
    has_bin = any(
        p.name.startswith(prefix) and p.name.endswith(".bin")
        for p in grid_dir.iterdir()
    )
    has_meta = any(
        p.name.startswith(prefix) and p.name.endswith(".meta.json")
        for p in grid_dir.iterdir()
    )
    return has_bin and has_meta


def _fmt_gib(num_bytes: int) -> str:
    return f"{num_bytes / GIB:.2f} GiB"


def _fmt_elapsed(seconds: float) -> str:
    mins, secs = divmod(int(seconds), 60)
    if mins >= 60:
        hrs, mins = divmod(mins, 60)
        return f"{hrs}h{mins:02d}m"
    return f"{mins}m{secs:02d}s"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    model = args.model
    plugin = MODEL_REGISTRY.get(model)
    if plugin is None:
        _fail(f"Model {model!r} not found in registry.")

    global_grid_m = inject_global_region(plugin, model)

    # Anomaly vars read ERA5 baselines via the climatology module, which is
    # strictly read-only (no write paths). Point those reads at the live tree
    # so anomaly frames can build; every WRITE still goes to the dev data root
    # (build_frame's data_root param / FetchContext.data_root / Herbie dir).
    baseline_root = Path(args.baseline_root or args.compare_root).expanduser().resolve()
    if baseline_root.exists():
        climatology.configure_data_root(baseline_root)
        logger.info("Climatology baseline root (read-only): %s", baseline_root)
    else:
        logger.warning(
            "Baseline root %s does not exist; anomaly vars will fail to resolve "
            "baselines and be recorded as failures.",
            baseline_root,
        )

    bbox, grid_m = get_grid_params(model, GLOBAL_REGION_ID)
    _transform, planned_h, planned_w = compute_transform_and_shape(bbox, grid_m)

    run_dt = resolve_run(
        plugin,
        model,
        run_arg=args.run,
        cycle_type=args.cycle_type,
        min_age_hours=args.min_age_hours,
        probe=not args.plan_only,
    )
    run_id = _run_id(run_dt)
    cycle_hour = run_dt.hour

    vars_to_build, targets = enumerate_targets(
        plugin,
        cycle_hour,
        limit_vars=args.limit_vars,
        limit_fhs=args.limit_fhs,
    )
    fhs_by_var: dict[str, list[int]] = {}
    for var_key, fh in targets:
        fhs_by_var.setdefault(var_key, []).append(fh)

    binary_only = binary_sampling_enabled(model)

    logger.info("=" * 70)
    logger.info("Model            : %s", model)
    logger.info("Run              : %s (cycle_hour=%02dz)", run_id, cycle_hour)
    if model == "ecmwf":
        logger.info("Cycle type       : %s", args.cycle_type or "explicit-run")
    logger.info("Binary-only model: %s", binary_only)
    logger.info("Global grid      : %.1fm  planned %dx%d px (WxH)", global_grid_m, planned_w, planned_h)
    logger.info("Buildable vars   : %d -> %s", len(vars_to_build), ", ".join(vars_to_build))
    logger.info(
        "Target frames    : %d across %d unique vars (incl. companions)",
        len(targets), len(fhs_by_var),
    )
    for var_key in sorted(fhs_by_var):
        fhs = fhs_by_var[var_key]
        logger.info("    %-16s %3d fhs  (%s..%s)", var_key, len(fhs), fhs[0], fhs[-1])
    logger.info("=" * 70)

    if args.plan_only:
        logger.info("--plan-only: no fetching or building performed. Exiting.")
        return 0

    staging_run_root = DATA_ROOT / "staging" / model / run_id

    # Shared fetch context + readiness cache for the whole run (one region),
    # mirroring the scheduler's per-region sharing.
    shared_fetch_ctx = FetchContext(coverage=GLOBAL_REGION_ID)
    shared_readiness_cache: dict[str, bool] = {}

    built = 0
    skipped = 0
    failed = 0
    failures: list[dict[str, Any]] = []
    per_var_frames: dict[str, dict[str, int]] = {}

    started = time.perf_counter()
    total_frames = len(targets)
    var_order = list(fhs_by_var.keys())
    var_index = {v: i for i, v in enumerate(var_order)}

    try:
        for idx, (var_key, fh) in enumerate(targets, start=1):
            counts = per_var_frames.setdefault(var_key, {"built": 0, "skipped": 0, "failed": 0})

            if frame_outputs_exist(staging_run_root, var_key, fh):
                skipped += 1
                counts["skipped"] += 1
            else:
                # Don't burn a frame's retry attempts inside an active upstream
                # range-throttle cooldown (NOMADS anti-abuse) — sleep it out.
                cooldown = _range_throttle_remaining()
                if cooldown > 0:
                    logger.info(
                        "Upstream range-throttle cooldown active — sleeping %.0fs "
                        "before %s/fh%03d", cooldown + 2.0, var_key, fh,
                    )
                    time.sleep(cooldown + 2.0)
                if args.frame_delay > 0:
                    time.sleep(args.frame_delay)
                ensemble_view = _var_default_ensemble_view(plugin, var_key)
                runtime_var_id = _runtime_var_id(plugin, var_key, ensemble_view)
                try:
                    _path, status = build_frame(
                        model=model,
                        region=GLOBAL_REGION_ID,
                        var_id=runtime_var_id,
                        fh=fh,
                        run_date=run_dt,
                        data_root=DATA_ROOT,
                        product=getattr(plugin, "product", "sfc"),
                        model_plugin=plugin,
                        ensemble_view=ensemble_view,
                        fetch_ctx=shared_fetch_ctx,
                        readiness_cache=shared_readiness_cache,
                        log_fetch_cache_stats=False,
                        return_status=True,
                    )
                except Exception as exc:  # noqa: BLE001 — one bad frame must not abort the run
                    failed += 1
                    counts["failed"] += 1
                    failures.append({"var": var_key, "fh": fh, "status": "exception", "error": repr(exc)})
                    logger.exception("Frame build raised for %s/fh%03d", var_key, fh)
                else:
                    if _path is not None and status == "ok":
                        built += 1
                        counts["built"] += 1
                    else:
                        failed += 1
                        counts["failed"] += 1
                        failures.append({"var": var_key, "fh": fh, "status": status})
                        logger.warning("Frame not built for %s/fh%03d: status=%s", var_key, fh, status)

            # Progress: log at the end of each var's run of frames.
            is_last_of_var = (
                idx == total_frames
                or targets[idx][0] != var_key  # next target is a different var
            )
            if is_last_of_var:
                var_bytes = _dir_size_bytes(staging_run_root / var_key)
                pos = var_index.get(var_key, 0) + 1
                logger.info(
                    "var %d/%d %s: %d/%d frames (built=%d skipped=%d failed=%d), %s, elapsed %s",
                    pos, len(var_order), var_key,
                    counts["built"] + counts["skipped"], len(fhs_by_var[var_key]),
                    counts["built"], counts["skipped"], counts["failed"],
                    _fmt_gib(var_bytes), _fmt_elapsed(time.perf_counter() - started),
                )
    finally:
        try:
            destroy_fetch_context(shared_fetch_ctx)
        except Exception:  # noqa: BLE001
            pass

    wall_seconds = time.perf_counter() - started

    # --- Measure ---
    raw_bytes = _dir_size_bytes(HERBIE_MODEL_DIR)
    staging = measure_staging(staging_run_root)
    converted_bytes = staging["total_bytes"]
    n_measured_frames = built + skipped
    per_fh_mean_bytes = int(converted_bytes / n_measured_frames) if n_measured_frames else 0
    produced_dims = read_produced_grid_dims(staging_run_root)
    peak_rss_mb = peak_rss_bytes() / (1024.0 * 1024.0)

    # NA published comparison (READ-ONLY).
    na_published_root = Path(args.compare_root).expanduser() / "published" / model / run_id
    na_note = None
    na_published_bytes: int | None = None
    if na_published_root.exists():
        na_published_bytes = _dir_size_bytes(na_published_root)
    else:
        na_note = f"NA published run dir not found: {na_published_root}"
        logger.info(na_note)
    converted_multiplier = (
        converted_bytes / na_published_bytes
        if na_published_bytes not in (None, 0)
        else None
    )

    report: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "args": {
            "model": model,
            "run": args.run,
            "cycle_type": args.cycle_type,
            "min_age_hours": args.min_age_hours,
            "dev_root": args.dev_root,
            "limit_vars": args.limit_vars,
            "limit_fhs": args.limit_fhs,
            "keep_artifacts": args.keep_artifacts,
            "keep_grib": args.keep_grib,
            "compare_root": args.compare_root,
            "baseline_root": args.baseline_root,
        },
        "model": model,
        "run_id": run_id,
        "cycle_hour": cycle_hour,
        "binary_only": binary_only,
        "region": GLOBAL_REGION_ID,
        "global_grid_meters": global_grid_m,
        "global_bbox_3857": list(bbox),
        "planned_grid_dims_wxh": [planned_w, planned_h],
        "produced_grid_dims_wxh": list(produced_dims) if produced_dims else None,
        "vars_to_build": vars_to_build,
        "fhs_by_var": {v: fhs_by_var[v] for v in fhs_by_var},
        "frame_counts": {
            "targets": total_frames,
            "built": built,
            "skipped": skipped,
            "failed": failed,
        },
        "per_var_frames": per_var_frames,
        "raw_grib_bytes": raw_bytes,
        "converted": {
            "total_bytes": converted_bytes,
            "file_count": staging["file_count"],
            "per_fh_mean_bytes": per_fh_mean_bytes,
            "per_var_bytes": staging["per_var_bytes"],
            "per_suffix_bytes": staging["per_suffix_bytes"],
        },
        "na_comparison": {
            "published_root": str(na_published_root),
            "published_bytes": na_published_bytes,
            "converted_multiplier": converted_multiplier,
            "note": na_note,
        },
        "peak_rss_mb": round(peak_rss_mb, 1),
        "wall_seconds": round(wall_seconds, 1),
        "failures": failures,
        "paths": {
            "data_root": str(DATA_ROOT),
            "herbie_dir": str(HERBIE_MODEL_DIR),
            "staging_run_root": str(staging_run_root),
        },
    }

    # --- Write report (survives cleanup) ---
    report_name = f"{model}_{run_id}"
    if model == "ecmwf" and args.cycle_type:
        report_name += f"_{args.cycle_type}"
    report_path = REPORTS_DIR / f"{report_name}.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    logger.info("Wrote report: %s", report_path)

    _print_summary(report)

    # --- Cleanup (AFTER measuring + report) ---
    if failed > 0:
        logger.warning(
            "Keeping staging + grib despite cleanup defaults: %d frames failed. "
            "Re-run the same command to resume (completed frames are skipped); "
            "cleanup runs on the pass that finishes with 0 failures.", failed,
        )
    else:
        if not args.keep_artifacts:
            _safe_rmtree(staging_run_root, "converted staging")
        if not args.keep_grib:
            _safe_rmtree(HERBIE_MODEL_DIR, "dev herbie dir")

    return 0


def _safe_rmtree(path: Path, label: str) -> None:
    resolved = path.resolve()
    dev_root = Path(ARGS.dev_root).expanduser().resolve()
    if not (_is_within(resolved, dev_root) or resolved == dev_root):
        logger.error("Refusing to delete %s (%s): outside dev_root %s", resolved, label, dev_root)
        return
    if resolved in (LIVE_DATA_ROOT.resolve(), LIVE_HERBIE_CACHE.resolve()):
        logger.error("Refusing to delete live path %s (%s)", resolved, label)
        return
    shutil.rmtree(resolved, ignore_errors=True)
    logger.info("Deleted %s: %s", label, resolved)


def _print_summary(report: dict[str, Any]) -> None:
    conv = report["converted"]
    fc = report["frame_counts"]
    na = report["na_comparison"]
    dims = report["produced_grid_dims_wxh"] or report["planned_grid_dims_wxh"]
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 68)
    lines.append(f"  GLOBAL SIZING — {report['model'].upper()}  run {report['run_id']}")
    lines.append("=" * 68)
    lines.append(f"  Global grid          : {report['global_grid_meters']:.0f} m   {dims[0]} x {dims[1]} px (WxH)")
    lines.append(f"  Binary-only model    : {report['binary_only']}")
    lines.append(f"  Frames  built/skip/fail: {fc['built']} / {fc['skipped']} / {fc['failed']}   (targets {fc['targets']})")
    lines.append(f"  Wall time            : {_fmt_elapsed(report['wall_seconds'])}")
    lines.append(f"  Peak RSS             : {report['peak_rss_mb']:.0f} MB")
    lines.append("  " + "-" * 64)
    lines.append(f"  RAW GRIB (global)    : {_fmt_gib(report['raw_grib_bytes'])}")
    lines.append(f"  CONVERTED (global)   : {_fmt_gib(conv['total_bytes'])}   ({conv['file_count']} files)")
    lines.append(f"  per-FH mean          : {conv['per_fh_mean_bytes'] / (1024.0 * 1024.0):.2f} MiB")
    lines.append("  Converted by suffix:")
    for suffix, size in sorted(conv["per_suffix_bytes"].items(), key=lambda kv: -kv[1]):
        if size:
            lines.append(f"      {suffix:<12} {_fmt_gib(size)}")
    lines.append("  " + "-" * 64)
    if na["published_bytes"] is not None:
        lines.append(f"  NA published (same run): {_fmt_gib(na['published_bytes'])}")
        if na["converted_multiplier"] is not None:
            lines.append(f"  Global / NA converted  : {na['converted_multiplier']:.2f}x")
    else:
        lines.append(f"  NA published (same run): n/a  ({na['note']})")
    lines.append("  " + "-" * 64)
    lines.append("  Converted per-var (top 12):")
    for var_key, size in sorted(conv["per_var_bytes"].items(), key=lambda kv: -kv[1])[:12]:
        lines.append(f"      {var_key:<18} {_fmt_gib(size)}")
    if report["failures"]:
        lines.append("  " + "-" * 64)
        lines.append(f"  FAILURES: {len(report['failures'])} (see report JSON)")
    lines.append("=" * 68)
    # Emit as one block for easy copy-paste.
    print("\n".join(lines), file=sys.stdout, flush=True)


def main() -> int:
    try:
        return run(ARGS)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        logger.warning("Interrupted. Re-run the same command to resume (completed frames skip).")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
