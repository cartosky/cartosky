#!/usr/bin/env python3
"""Disk-sizing spike v2 (Phase 3): measure SST candidate sources for CartoSky.

CartoSky is adding a standalone **daily SST layer**. The upstream decision is
already made; this script produces the apples-to-apples numbers that size it:

  * **Geo-Polar Blended 5 km** is the RECOMMENDED absolute-SST upstream.
  * **NOAA Coral Reef Watch 5 km SST anomaly** (``noaacrwsstanomalyDaily``
    lineage) is the anomaly upstream.
  * **OISST v2.1** is the fallback absolute-SST upstream.

**Display units are °C only.** There is no °F support anywhere in this spike —
not in the packing table, not in the reported statistics, not in the eyeball
PNGs. Both packing entries carry ``units: "C"`` and stay uint16 @ 0.01 °C.

What v2 measures that v1 did not:

  1. **The REAL production target grids** instead of synthetic global-3857
     squares. Both are resolved through
     ``builder.raster_grid.get_target_grid`` — the single production
     source of truth — and then hard-asserted against the pinned constants in
     ``sources/openmeteo/grids.py`` so a production grid change fails this
     script loudly instead of quietly sizing the wrong raster:
       * ``na``     — EPSG:3857, 9000 m, snapped bbox
         (-19818000, 549000, -2781000, 16974000), 1825 rows x 1893 cols.
       * ``global`` — EPSG:4326, 0.25°, 721 x 1440 (lat 90 -> -90,
         lon -180 -> 179.75).
     The 4326 grid needs no special writer:
     ``grid.write_grid_frame_for_run_root`` already takes ``transform=`` and
     ``projection=``, which is exactly how the production 4326 publish path
     calls it (``fastpath/subloop.py`` passes ``grid.transform`` /
     ``grid.crs`` straight from ``get_target_grid``). This script mirrors that
     call shape, so the ``.bin`` / ``.meta.json`` bytes match prod.

  2. **Multiple probed fetch paths per lineage**, so the report shows which
     paths actually work *from prod*. CoastWatch ERDDAP griddap is now the
     primary Geo-Polar and CRW path, with the NCEI/THREDDS archive templates
     kept as probed fallbacks.

  3. **Both Geo-Polar lineages for the same date** (day+night and night-only)
     plus a pairing statistic — mean/max absolute difference over commonly
     valid ocean cells, in °C. This tells us whether showing DN absolute SST
     next to the night-lineage CRW anomaly is visually coherent.

  4. **A retention storage projection** (1 run/day, 1 frame per variable per
     grid per day) over 7 / 30 / 90 / 365 day windows, built from measured
     on-disk bytes. Raw ``.nc`` downloads are transient and reported apart.

  5. **Eyeball PNGs** per (candidate x grid) under
     ``{dev_root}/sst/reports/png/``, which persist regardless of ``--keep``.

Units caveat baked in from live inspection (2026-08-06): the two Geo-Polar
ERDDAP datasets disagree on units — ``noaacwBLENDEDsstDNDaily`` serves
``degree_C`` while ``noaacwBLENDEDsstDaily`` (night) serves ``kelvin``. A
static per-candidate "kelvin" flag would therefore be wrong for one of them, so
SST fields auto-detect their units from the netCDF ``units`` attribute (with a
magnitude fallback) and the detection is recorded in the report. Anomaly fields
are already °C deltas and are never converted.

Second caveat from live inspection: CoastWatch ERDDAP returns **403** to the
default ``python-requests`` User-Agent, so every request here sends an explicit
``CartoSky-SST-Sizing/1.0`` UA. Without it ERDDAP looks uniformly unavailable.

Candidates:
  * ``geopolar``  — NOAA Geo-Polar Blended 0.05°/5 km daily L4 GHRSST.
    Lineages: ``dn`` (day+night, primary) and ``night`` (night-only).
  * ``crw_anom``  — NOAA Coral Reef Watch daily global 5 km SST anomaly.
    Primary CoastWatch ERDDAP; fallback the NOAA STAR HTTPS archive.
  * ``oisst``     — OISST v2.1, 0.25° daily AVHRR OI. Fallback upstream.
    Finals lag ~2 weeks so ``_preliminary`` is tried per day.
  * ``mur``       — PROBE ONLY, no download. A PO.DAAC HTTPS granule is HEADed
    to document the Earthdata-auth misfit; the AWS mirror (s3://mur-sst) is
    zarr, which does not fit the single-file netCDF flow.

Isolation contract (this script must be INCAPABLE of touching live data):
  * everything it writes lives under ``{dev_root}/sst/``:
      downloads -> ``{dev_root}/sst/downloads``
      converted -> ``{dev_root}/sst/data/staging/sst_{candidate}/{date}/...``
      reports   -> ``{dev_root}/sst/reports`` (PNGs in ``reports/png``)
  * refuses to run as root (euid==0).
  * refuses any resolved write path at or inside ``/opt/cartosky/data`` or
    ``/opt/cartosky/herbie_cache_ssd``; deletions only happen inside the dev
    ``sst`` subtree.

All argument parsing happens BEFORE any app import so ``--help`` and
``--plan-only`` work on any machine with no prod env and no network.

Usage examples::

    # Measure all candidates, auto walk-back to the latest available day:
    python backend/scripts/measure_sst_sizing.py

    # Offline plan (no network): candidates, URLs, grids, projection model:
    python backend/scripts/measure_sst_sizing.py --dev-root /tmp/sst --plan-only

    # Only the anomaly candidate (smallest scope), pinned date, keep output:
    python backend/scripts/measure_sst_sizing.py --candidate crw_anom \\
        --date 20260803 --keep

Exit codes::
  0   at least one candidate produced measurements (per-candidate misses and
      skipped steps are recorded as findings, not failures)
  1   usage / safety-guard / environment error, OR nothing could be measured,
      OR the recommended candidate (geopolar) was selected but not measured
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
from typing import Any, Callable

# ── Path setup (mirrors measure_global_sizing.py) ──
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LIVE_DATA_ROOT = Path("/opt/cartosky/data")
LIVE_HERBIE_CACHE = Path("/opt/cartosky/herbie_cache_ssd")

# ── Production target grids ──
# (name, model, region) triples fed to builder.raster_grid.get_target_grid.
# ``ecmwf/na`` is the 9 km EPSG:3857 NA grid; ``gfs/global`` is the native
# 0.25° EPSG:4326 grid. Model choice only selects the grid geometry — nothing
# model-specific is read.
TARGET_GRID_KEYS: tuple[tuple[str, str, str], ...] = (
    ("na", "ecmwf", "na"),
    ("global", "gfs", "global"),
)

# Fallback copies of the pinned production geometry, used only if
# ``sources.openmeteo.grids`` cannot be imported. grids.py stays authoritative.
_FALLBACK_NA_9KM_SHAPE = (1825, 1893)
_FALLBACK_NA_9KM_SNAPPED_BBOX = (-19818000.0, 549000.0, -2781000.0, 16974000.0)
_FALLBACK_GLOBAL_025_SHAPE = (721, 1440)

# ── Packing targets injected at runtime into the grid module's mutable packing
# table (there is no real SST model/var yet). Both uint16 @ 0.01 °C, °C only.
SST_MODEL_ID = "sst"
SST_VAR_ID = "sst"
SST_ANOM_VAR_ID = "sst_anom"
# offset -5 °C -> representable -5..650 °C, covering ocean SST (~-2..36 °C).
SST_PACKING = {"scale": 0.01, "offset": -5.0, "nodata": 65535, "units": "C"}
# offset -30 °C -> representable -30..625 °C, covering ±30 °C anomalies.
SST_ANOM_PACKING = {"scale": 0.01, "offset": -30.0, "nodata": 65535, "units": "C"}

# ── Eyeball PNG ramps (°C). NaN renders fully transparent. ──
SST_PNG_RANGE_C = (-2.0, 35.0)
SST_PNG_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (-2.0, (8, 16, 72)),      # dark blue
    (10.0, (0, 110, 190)),
    (20.0, (0, 216, 224)),    # cyan
    (27.0, (250, 238, 120)),  # yellow
    (35.0, (170, 24, 24)),    # red
)
ANOM_PNG_RANGE_C = (-5.0, 5.0)
ANOM_PNG_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (-5.0, (28, 62, 176)),    # blue
    (0.0, (255, 255, 255)),   # white
    (5.0, (176, 28, 28)),     # red
)

# ── Retention windows for the storage projection (days). ──
RETENTION_DAYS: tuple[int, ...] = (7, 30, 90, 365)

# HTTP timeouts: (connect, read). ERDDAP builds the subset server-side before
# streaming, so the read timeout is generous.
HTTP_TIMEOUT = (10.0, 300.0)
DOWNLOAD_ATTEMPTS = 2

#: REQUIRED, not cosmetic: CoastWatch ERDDAP answers **403** to the default
#: ``python-requests/x.y.z`` User-Agent (verified live 2026-08-06 — the same URL
#: returns 200 under a normal UA). Every request this script makes sends this
#: header, matching the ``CartoSky-<service>/1.0`` convention used by
#: ``wpc_source``/``cpc_outlook``/``mrms_fetch``. Any future SST fetcher must
#: carry a UA too or ERDDAP will silently look "down".
HTTP_HEADERS = {"User-Agent": "CartoSky-SST-Sizing/1.0"}

logger = logging.getLogger("measure_sst_sizing")


# ---------------------------------------------------------------------------
# Candidate definitions
# ---------------------------------------------------------------------------
#
# Every fetch path is a dict: {"label", "url", "probe"}.
#   probe="head"  -> cheap HEAD, used to walk back to the newest available day.
#   probe="none"  -> do not probe; ERDDAP griddap materialises the subset
#                    server-side, so a HEAD is as expensive as the download.
#                    The download itself is attempted and its outcome recorded.

_ERDDAP_GRIDDAP = "https://coastwatch.noaa.gov/erddap/griddap"

# Verified live 2026-08-06 against
# https://coastwatch.noaa.gov/erddap/info/<dataset>/index.json:
#   noaacwBLENDEDsstDNDaily      analysed_sst                     units degree_C
#   noaacwBLENDEDsstDaily        analysed_sst                     units kelvin
#   noaacrwsstanomalyDaily       sea_surface_temperature_anomaly  units degree_C
# All three: 0.05°, lat -89.975..89.975, lon -179.975..179.975, time at T12:00:00Z.
_ERDDAP_LAT_LON_SELECTOR = "[(-89.975):(89.975)][(-179.975):(179.975)]"


def _erddap_url(dataset: str, var: str, d: datetime) -> str:
    stamp = d.strftime("%Y-%m-%dT12:00:00Z")
    return (
        f"{_ERDDAP_GRIDDAP}/{dataset}.nc?{var}"
        f"[({stamp})]{_ERDDAP_LAT_LON_SELECTOR}"
    )


def _oisst_paths(d: datetime) -> list[dict[str, str]]:
    """OISST day: final first, then preliminary."""
    base = (
        "https://www.ncei.noaa.gov/data/"
        "sea-surface-temperature-optimum-interpolation/v2.1/access/avhrr/"
        "{ym}/oisst-avhrr-v02r01.{ymd}{suffix}.nc"
    )
    ym, ymd = d.strftime("%Y%m"), d.strftime("%Y%m%d")
    return [
        {"label": "ncei-final", "url": base.format(ym=ym, ymd=ymd, suffix=""), "probe": "head"},
        {
            "label": "ncei-preliminary",
            "url": base.format(ym=ym, ymd=ymd, suffix="_preliminary"),
            "probe": "head",
        },
    ]


# NCEI GHRSST archive templates, kept as probed fallbacks behind ERDDAP.
_NCEI_GEOPOLAR_DN = (
    "https://www.ncei.noaa.gov/data/oceans/ghrsst/L4/GLOB/OSPO/"
    "Geo_Polar_Blended/{Y}/{DDD}/"
    "{ymd}000000-OSPO-L4_GHRSST-SSTfnd-Geo_Polar_Blended-GLOB-v02.0-fv01.0.nc"
)
_NCEI_GEOPOLAR_NIGHT = (
    "https://www.ncei.noaa.gov/data/oceans/ghrsst/L4/GLOB/OSPO/"
    "Geo_Polar_Blended_Night/{Y}/{DDD}/"
    "{ymd}000000-OSPO-L4_GHRSST-SSTfnd-Geo_Polar_Blended_Night-GLOB-v02.0-fv01.0.nc"
)
_THREDDS_GEOPOLAR_DN = (
    "https://coastwatch.noaa.gov/thredds/fileServer/CoastWatch/GEO-POLAR-BLENDED/"
    "{Y}/{DDD}/"
    "{ymd}000000-OSPO-L4_GHRSST-SSTfnd-Geo_Polar_Blended-GLOB-v02.0-fv01.0.nc"
)


def _archive_fmt(tmpl: str, d: datetime) -> str:
    return tmpl.format(Y=d.strftime("%Y"), DDD=d.strftime("%j"), ymd=d.strftime("%Y%m%d"))


def _geopolar_dn_paths(d: datetime) -> list[dict[str, str]]:
    return [
        {
            "label": "erddap-noaacwBLENDEDsstDNDaily",
            "url": _erddap_url("noaacwBLENDEDsstDNDaily", "analysed_sst", d),
            "probe": "none",
        },
        {"label": "ncei-ghrsst-day", "url": _archive_fmt(_NCEI_GEOPOLAR_DN, d), "probe": "head"},
        {"label": "thredds-fileserver", "url": _archive_fmt(_THREDDS_GEOPOLAR_DN, d), "probe": "head"},
    ]


def _geopolar_night_paths(d: datetime) -> list[dict[str, str]]:
    return [
        {
            "label": "erddap-noaacwBLENDEDsstDaily",
            "url": _erddap_url("noaacwBLENDEDsstDaily", "analysed_sst", d),
            "probe": "none",
        },
        {"label": "ncei-ghrsst-night", "url": _archive_fmt(_NCEI_GEOPOLAR_NIGHT, d), "probe": "head"},
    ]


# NOAA STAR CRW archive, verified live 2026-08-06: the ssta/ directory exists
# and is laid out as ssta/{YYYY}/ct5km_ssta_v3.1_{YYYYMMDD}.nc.
_STAR_CRW_SSTA = (
    "https://www.star.nesdis.noaa.gov/pub/socd/mecb/crw/data/5km/v3.1_op/nc/"
    "v1.0/daily/ssta/{Y}/ct5km_ssta_v3.1_{ymd}.nc"
)


def _crw_anom_paths(d: datetime) -> list[dict[str, str]]:
    return [
        {
            "label": "erddap-noaacrwsstanomalyDaily",
            "url": _erddap_url(
                "noaacrwsstanomalyDaily", "sea_surface_temperature_anomaly", d
            ),
            "probe": "none",
        },
        {
            "label": "star-nesdis-archive",
            "url": _STAR_CRW_SSTA.format(Y=d.strftime("%Y"), ymd=d.strftime("%Y%m%d")),
            "probe": "head",
        },
    ]


CandidateSpec = dict[str, Any]

CANDIDATES: dict[str, CandidateSpec] = {
    "geopolar": {
        "id": "geopolar",
        "name": "NOAA Geo-Polar Blended (0.05 deg / 5 km daily L4 GHRSST) — RECOMMENDED SST",
        "var_id": SST_VAR_ID,
        "field_kind": "sst",
        "walkback_days": 7,
        "primary_lineage": "dn",
        "lineages": {
            "dn": {
                "name": "day+night blend",
                "subdataset_var": "analysed_sst",
                "paths_for_date": _geopolar_dn_paths,
                "note": "ERDDAP noaacwBLENDEDsstDNDaily serves degree_C; NCEI archive serves kelvin",
            },
            "night": {
                "name": "night-only blend",
                "subdataset_var": "analysed_sst",
                "paths_for_date": _geopolar_night_paths,
                "note": "ERDDAP noaacwBLENDEDsstDaily serves kelvin; same date as dn (pairing stat)",
            },
        },
    },
    "crw_anom": {
        "id": "crw_anom",
        "name": "NOAA Coral Reef Watch daily global 5 km SST anomaly — ANOMALY SOURCE",
        "var_id": SST_ANOM_VAR_ID,
        "field_kind": "anom",
        "walkback_days": 7,
        "primary_lineage": "default",
        "lineages": {
            "default": {
                "name": "CRW v3.1 SST anomaly",
                "subdataset_var": "sea_surface_temperature_anomaly",
                "paths_for_date": _crw_anom_paths,
                "note": "already degree_C anomalies — never Kelvin-converted",
            },
        },
    },
    "oisst": {
        "id": "oisst",
        "name": "OISST v2.1 (NOAA 0.25 deg daily AVHRR OI) — FALLBACK SST",
        "var_id": SST_VAR_ID,
        "field_kind": "sst",
        "walkback_days": 14,
        "primary_lineage": "default",
        "lineages": {
            "default": {
                "name": "OISST v2.1",
                "subdataset_var": "sst",
                "paths_for_date": _oisst_paths,
                "note": "final lags ~2wk; _preliminary tried per day; 0..360 lon rolled",
            },
        },
    },
}

ALL_CANDIDATES = ["oisst", "geopolar", "crw_anom"]
#: Selecting this candidate and failing to measure it is a hard failure.
REQUIRED_CANDIDATE = "geopolar"

# MUR probe: a representative PO.DAAC HTTPS granule that requires Earthdata auth.
MUR_PROBE_URL = (
    "https://archive.podaac.earthdata.nasa.gov/podaac-ops-cumulus-protected/"
    "MUR-JPL-L4-GLOB-v4.1/"
    "20260101090000-JPL-L4_GHRSST-SSTfnd-MUR-GLOB-v02.0-fv04.1.nc"
)


# ---------------------------------------------------------------------------
# Argument parsing (runs BEFORE any heavy/app import so --help works anywhere)
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="measure_sst_sizing.py",
        description="Measure raw + converted disk cost of SST candidate sources "
        "on the real production grids, and project retention storage.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dev-root",
        default="/opt/cartosky-dev",
        help="Confinement root. Everything is written under {dev-root}/sst/.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Pin a single day YYYYMMDD to attempt (else auto walk-back).",
    )
    parser.add_argument(
        "--candidate",
        choices=[*ALL_CANDIDATES, "all"],
        default="all",
        help="Which download candidate(s) to measure. The MUR probe always "
        "runs regardless (it is a free HEAD, no download).",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep downloads + converted output after measuring "
        "(default: delete; reports and eyeball PNGs always persist).",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Offline: print candidates, fetch paths, target grids, projection "
        "model, exit. No network, no fetching, no conversion.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Debug-level logging.",
    )
    return parser


# Parse EARLY so --help / --plan-only never trigger a heavy or networked import.
ARGS = build_parser().parse_args()


class _DropWebmercLatitudeNoise(logging.Filter):
    """Drop the expected PROJ 'webmerc: Invalid latitude' spam.

    Global SST source grids cover ±90° but the NA target is Web Mercator, which
    only represents ±85.05°, so GDAL/PROJ warns about every polar row it
    (correctly) drops during the warp. Only this exact message is filtered; all
    other GDAL/PROJ warnings still surface.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "webmerc: Invalid latitude" not in record.getMessage()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    handler.addFilter(_DropWebmercLatitudeNoise())
    logging.basicConfig(level=level, handlers=[handler], force=True)


_setup_logging(ARGS.verbose)


# ---------------------------------------------------------------------------
# Safety guards + confined dirs (must precede any app import)
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


def _resolve_paths(dev_root: str) -> tuple[Path, Path, Path, Path, Path]:
    """Return (sst_root, downloads, staging, reports, png_dir), all under dev_root."""
    root = Path(dev_root).expanduser().resolve()
    sst_root = root / "sst"
    downloads_dir = sst_root / "downloads"
    staging_root = sst_root / "data" / "staging"
    reports_dir = sst_root / "reports"
    png_dir = reports_dir / "png"
    return sst_root, downloads_dir, staging_root, reports_dir, png_dir


def _enforce_safety(sst_root: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        _fail("Refusing to run as root (euid==0). Run as the cartosky user.")

    resolved = sst_root.resolve()
    for live in (LIVE_DATA_ROOT, LIVE_HERBIE_CACHE):
        if resolved == live.resolve() or _is_within(resolved, live):
            _fail(
                f"SAFETY: sst root {resolved} is the live path {live} or inside "
                f"it. Refusing."
            )


SST_ROOT, DOWNLOADS_DIR, STAGING_ROOT, REPORTS_DIR, PNG_DIR = _resolve_paths(ARGS.dev_root)
_enforce_safety(SST_ROOT)

SST_ROOT.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
STAGING_ROOT.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
PNG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("GDAL_CACHEMAX", "256")

logger.info("Confined sst root  : %s", SST_ROOT)
logger.info("Downloads dir      : %s", DOWNLOADS_DIR)
logger.info("Staging root       : %s", STAGING_ROOT)
logger.info("Reports dir        : %s", REPORTS_DIR)
logger.info("Eyeball PNG dir    : %s", PNG_DIR)


# ---------------------------------------------------------------------------
# App / heavy imports (deferred so --help stays import-free)
# ---------------------------------------------------------------------------

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import requests  # noqa: E402
from rasterio.crs import CRS  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import Resampling, reproject  # noqa: E402

from app.services.builder.raster_grid import get_target_grid  # noqa: E402
from app.services.grid import (  # noqa: E402
    _PACKING_BY_MODEL_VAR,
    write_grid_frame_for_run_root,
)

MIB = 1024.0 * 1024.0
GIB = 1024.0 * 1024.0 * 1024.0


def _pinned_grid_constants() -> dict[str, Any]:
    """Pinned production geometry, preferring the openmeteo grids.py originals."""
    try:
        from app.services.sources.openmeteo.grids import (  # noqa: PLC0415
            GLOBAL_025_SHAPE,
            NA_9KM_SHAPE,
            NA_9KM_SNAPPED_BBOX,
        )
    except Exception as exc:  # noqa: BLE001 — fall back, but say so
        logger.warning(
            "Could not import pinned grid constants from sources.openmeteo.grids "
            "(%r); using local fallback copies.", exc
        )
        return {
            "source": "fallback-literals",
            "na_shape": _FALLBACK_NA_9KM_SHAPE,
            "na_bbox": _FALLBACK_NA_9KM_SNAPPED_BBOX,
            "global_shape": _FALLBACK_GLOBAL_025_SHAPE,
        }
    return {
        "source": "app.services.sources.openmeteo.grids",
        "na_shape": tuple(NA_9KM_SHAPE),
        "na_bbox": tuple(NA_9KM_SNAPPED_BBOX),
        "global_shape": tuple(GLOBAL_025_SHAPE),
    }


PINNED = _pinned_grid_constants()


def inject_sst_packing() -> None:
    """Register both pack targets in the grid module's mutable packing table.

    There is no real SST model/var, so the writer would raise
    ``Unsupported grid pack target``. The table is a plain module dict; adding
    process-local entries mirrors the sibling script's in-memory region
    injection and changes nothing on disk or for any other (model, var).
    """
    _PACKING_BY_MODEL_VAR[(SST_MODEL_ID, SST_VAR_ID)] = dict(SST_PACKING)
    _PACKING_BY_MODEL_VAR[(SST_MODEL_ID, SST_ANOM_VAR_ID)] = dict(SST_ANOM_PACKING)
    logger.info("Injected packing (%s, %s): %s", SST_MODEL_ID, SST_VAR_ID, SST_PACKING)
    logger.info(
        "Injected packing (%s, %s): %s", SST_MODEL_ID, SST_ANOM_VAR_ID, SST_ANOM_PACKING
    )


# ---------------------------------------------------------------------------
# Production target grids
# ---------------------------------------------------------------------------

def target_grids() -> list[dict[str, Any]]:
    """Resolve both production target grids and assert the pinned geometry.

    ``get_target_grid`` is the production source of truth for CRS + bbox +
    transform + shape (both the legacy metre family and the native-geographic
    family). Nothing here recomputes a grid; the pinned constants only guard
    against drift.
    """
    out: list[dict[str, Any]] = []
    for name, model, region in TARGET_GRID_KEYS:
        try:
            grid = get_target_grid(model, region)
        except Exception as exc:  # noqa: BLE001 — cannot size an unresolvable grid
            _fail(
                f"Could not resolve the production target grid {model}/{region} "
                f"({exc!r}). This usually means the model registry did not load, "
                f"so grid ownership fell back and lost the {name} entry. Refusing "
                f"to guess a grid."
            )
        res = float(grid.resolution)
        xmin, ymax = float(grid.transform.c), float(grid.transform.f)
        snapped = (xmin, ymax - grid.height * res, xmin + grid.width * res, ymax)
        entry: dict[str, Any] = {
            "name": name,
            "model": model,
            "region": region,
            "crs": grid.crs,
            "resolution": res,
            "shape_rows_cols": [int(grid.height), int(grid.width)],
            "snapped_bbox": [round(v, 6) for v in snapped],
            "transform": [float(v) for v in tuple(grid.transform)[:6]],
            "_transform": grid.transform,
        }
        if name == "na":
            expected_shape = PINNED["na_shape"]
            expected_bbox = PINNED["na_bbox"]
            if (grid.height, grid.width) != tuple(expected_shape) or snapped != tuple(
                expected_bbox
            ):
                _fail(
                    f"PRODUCTION GRID DRIFT for {model}/{region}: "
                    f"shape={(grid.height, grid.width)} bbox={snapped}, expected "
                    f"{expected_shape} / {expected_bbox} (pinned in {PINNED['source']}). "
                    f"Refusing to size the wrong raster."
                )
        elif name == "global":
            expected_shape = PINNED["global_shape"]
            if (grid.height, grid.width) != tuple(expected_shape):
                _fail(
                    f"PRODUCTION GRID DRIFT for {model}/{region}: "
                    f"shape={(grid.height, grid.width)}, expected {expected_shape} "
                    f"(pinned in {PINNED['source']}). Refusing to size the wrong raster."
                )
            if grid.crs != "EPSG:4326":
                _fail(
                    f"Expected the global target grid in EPSG:4326, got {grid.crs}. "
                    f"Refusing to fake a 4326 measurement on a 3857 grid."
                )
        out.append(entry)
    return out


def _grids_for_report(grids: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip the non-JSON-serialisable Affine before the report is written."""
    return [{k: v for k, v in g.items() if not k.startswith("_")} for g in grids]


# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

def _head(url: str, *, allow_redirects: bool = True) -> tuple[int | None, dict[str, str], str | None]:
    """HEAD a URL. Returns (status_code|None, headers, error|None). Never raises."""
    try:
        resp = requests.head(
            url,
            timeout=HTTP_TIMEOUT,
            allow_redirects=allow_redirects,
            headers=HTTP_HEADERS,
        )
        return resp.status_code, dict(resp.headers), None
    except Exception as exc:  # noqa: BLE001 — a probe error is a recorded finding
        return None, {}, repr(exc)


def _download(url: str, dest: Path) -> tuple[bool, str | None]:
    """Stream a single file to ``dest`` with retries. No directory crawling.

    Returns (ok, error). ``ok`` means a complete file landed at ``dest``.
    """
    last_err: str | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with requests.get(
                url, stream=True, timeout=HTTP_TIMEOUT, headers=HTTP_HEADERS
            ) as resp:
                if resp.status_code != 200:
                    last_err = f"HTTP {resp.status_code}"
                    logger.warning("Download attempt %d: %s for %s", attempt, last_err, url)
                    continue
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if chunk:
                            fh.write(chunk)
            tmp.replace(dest)
            return True, None
        except Exception as exc:  # noqa: BLE001
            last_err = repr(exc)
            logger.warning("Download attempt %d raised for %s: %s", attempt, url, last_err)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        if attempt < DOWNLOAD_ATTEMPTS:
            time.sleep(1.5)
    return False, last_err


# ---------------------------------------------------------------------------
# Fetch: resolve a lineage's date + path, then download
# ---------------------------------------------------------------------------

def _candidate_dates(spec: CandidateSpec, pinned: str | None) -> list[datetime]:
    if pinned:
        try:
            d = datetime.strptime(pinned, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            _fail(f"Invalid --date {pinned!r}; expected YYYYMMDD.")
        return [d]
    today = datetime.now(timezone.utc)
    start = today - timedelta(days=2)  # finals lag; start at today-2
    return [start - timedelta(days=i) for i in range(int(spec["walkback_days"]))]


def fetch_lineage(
    spec: CandidateSpec,
    lineage_key: str,
    dates: list[datetime],
) -> dict[str, Any]:
    """Walk dates x fetch paths for one lineage; download the first that works.

    ``probe="head"`` paths are HEADed first so a walk-back is cheap.
    ``probe="none"`` paths are downloaded directly and the outcome recorded —
    that is the honest way to test an ERDDAP griddap path, which has no cheap
    existence check. Every attempt lands in ``probe_log``, so a total miss is a
    fully documented finding rather than a silent gap.
    """
    lineage = spec["lineages"][lineage_key]
    paths_for_date: Callable[[datetime], list[dict[str, str]]] = lineage["paths_for_date"]
    cid = spec["id"]
    result: dict[str, Any] = {
        "lineage": lineage_key,
        "lineage_name": lineage["name"],
        "note": lineage.get("note"),
        "subdataset_var": lineage["subdataset_var"],
        "status": "pending",
        "url": None,
        "path_label": None,
        "date": None,
        "local_path": None,
        "probe_log": [],
    }
    for d in dates:
        ymd = d.strftime("%Y%m%d")
        for path in paths_for_date(d):
            label, url, probe_mode = path["label"], path["url"], path["probe"]
            entry: dict[str, Any] = {
                "date": ymd,
                "path_label": label,
                "url": url,
                "probe_mode": probe_mode,
            }
            if probe_mode == "head":
                status, headers, err = _head(url)
                entry.update(
                    {
                        "status": status,
                        "content_length": headers.get("Content-Length"),
                        "error": err,
                    }
                )
                result["probe_log"].append(entry)
                if status != 200:
                    continue
            else:
                result["probe_log"].append(entry)

            dest = DOWNLOADS_DIR / f"sst_{cid}_{lineage_key}_{ymd}_{label}.nc"
            t0 = time.monotonic()
            ok, err = _download(url, dest)
            elapsed = time.monotonic() - t0
            entry["download_ok"] = ok
            entry["download_error"] = err
            entry["download_seconds"] = round(elapsed, 2)
            if not ok:
                logger.warning("%s/%s: %s download failed: %s", cid, lineage_key, label, err)
                continue
            size = dest.stat().st_size
            entry["downloaded_bytes"] = size
            result.update(
                {
                    "status": "downloaded",
                    "url": url,
                    "path_label": label,
                    "date": ymd,
                    "local_path": str(dest),
                    "raw_nc_bytes": size,
                    "download_seconds": round(elapsed, 2),
                }
            )
            logger.info(
                "%s/%s: %s %s -> %.1f MiB in %.1fs",
                cid, lineage_key, ymd, label, size / MIB, elapsed,
            )
            return result

    result["status"] = "no_url"
    result["finding"] = (
        f"no working unauthenticated fetch path for lineage {lineage_key} "
        f"(tried {len(result['probe_log'])} path/date combinations)"
    )
    logger.warning("%s/%s: %s", cid, lineage_key, result["finding"])
    return result


# ---------------------------------------------------------------------------
# netCDF read
# ---------------------------------------------------------------------------

def _detect_kelvin(units: str | None, values: np.ndarray) -> tuple[bool, str]:
    """Decide whether an absolute-SST field is Kelvin. Returns (is_k, reason).

    The ERDDAP Geo-Polar datasets genuinely disagree (DN is degree_C, night is
    kelvin), so this reads the netCDF ``units`` attribute first and only falls
    back to a magnitude test when the attribute is absent or unrecognised.
    """
    token = (units or "").strip().lower().replace("_", " ")
    if token in {"kelvin", "k", "degrees k", "degree k", "degk"}:
        return True, f"units attribute {units!r}"
    if token in {"degree c", "degrees c", "celsius", "degc", "c", "degree celsius"}:
        return False, f"units attribute {units!r}"
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return False, "no finite values; assuming Celsius"
    median = float(np.median(finite))
    is_k = median > 100.0
    return is_k, f"magnitude fallback (median {median:.2f}, units={units!r})"


def read_native_field(path: Path, var: str, field_kind: str) -> dict[str, Any]:
    """Open the subdataset, apply scale/offset/fill, normalize to °C north-up.

    GDAL's netCDF driver applies neither scale/offset nor the fill mask, so this:
      * applies ``value = raw * scale + offset`` and masks the fill code to NaN,
      * for ``field_kind == "sst"``, auto-detects Kelvin and converts to °C,
      * for ``field_kind == "anom"``, leaves the values alone (already °C deltas),
      * flips a south-up array to north-up if the driver reports one,
      * rolls a 0..360 longitude layout to -180..180 and rebuilds the transform.

    Returns the float32 values, an EPSG:4326 Affine, native dims, valid
    fraction, min/max (°C), and the units decision.
    """
    subdataset = f'NETCDF:"{path}":{var}'
    with rasterio.open(subdataset) as ds:
        raw = ds.read(1).astype("float64")
        scale = ds.scales[0] if ds.scales else 1.0
        offset = ds.offsets[0] if ds.offsets else 0.0
        nodata = ds.nodatavals[0] if ds.nodatavals else None
        tr = ds.transform
        width, height = ds.width, ds.height
        left, right = ds.bounds.left, ds.bounds.right
        top, bottom = ds.bounds.top, ds.bounds.bottom
        xres = tr.a
        yres = abs(tr.e)
        band_units = ds.tags(1).get("units")
        global_tags = ds.tags()

    values = raw * float(scale) + float(offset)
    if nodata is not None:
        values[raw == nodata] = np.nan

    if field_kind == "anom":
        kelvin, units_reason = False, "anomaly field — °C deltas, no conversion"
    else:
        kelvin, units_reason = _detect_kelvin(band_units, values)
        if kelvin:
            values = values - 273.15

    # South-up source (positive dy) -> flip to north-up and use the true north.
    flipped = False
    if tr.e > 0:
        values = values[::-1, :]
        flipped = True
        north = bottom + height * yres
    else:
        north = top

    # 0..360 longitude layout -> -180..180. Detect by the eastern edge running
    # well past 180. Roll so the [180,360) half becomes [-180,0).
    rolled = False
    if right > 180.5:
        values = np.roll(values, -(width // 2), axis=1)
        west = -180.0
        rolled = True
    else:
        west = left

    values = values.astype("float32", copy=False)
    transform = from_origin(west, north, xres, yres)
    finite = np.isfinite(values)
    valid_fraction = float(finite.mean())
    vmin = float(np.nanmin(values)) if finite.any() else float("nan")
    vmax = float(np.nanmax(values)) if finite.any() else float("nan")

    # Climatology / provenance attributes, if the producer supplies any.
    clim_keys = ("clima", "baseline", "reference_period", "reference period")
    climatology_attrs = {
        k: str(v)[:300]
        for k, v in global_tags.items()
        if any(token in k.lower() for token in clim_keys)
    }
    for key in ("NC_GLOBAL#source", "NC_GLOBAL#comment", "NC_GLOBAL#history"):
        if key in global_tags:
            climatology_attrs.setdefault(key, str(global_tags[key])[:300])

    return {
        "values": values,
        "transform": transform,
        "dims_wxh": (width, height),
        "valid_fraction": valid_fraction,
        "value_min_c": vmin,
        "value_max_c": vmax,
        "rolled_longitude": rolled,
        "flipped_northup": flipped,
        "source_units": band_units,
        "kelvin_converted": kelvin,
        "units_decision": units_reason,
        "climatology_attrs": climatology_attrs,
    }


# ---------------------------------------------------------------------------
# Warp + write to a production grid
# ---------------------------------------------------------------------------

def _measure_tree(root: Path) -> dict[str, int]:
    """Categorised on-disk bytes for everything the writer produced."""
    bin_bytes = meta_bytes = compressed_bytes = json_bytes = other_bytes = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        name = p.name.lower()
        if name.endswith((".bin.gz", ".bin.br")):
            compressed_bytes += size
        elif name.endswith(".bin"):
            bin_bytes += size
        elif name.endswith(".meta.json"):
            meta_bytes += size
        elif name.endswith(".json"):
            json_bytes += size
        else:
            other_bytes += size
    total = bin_bytes + meta_bytes + compressed_bytes + json_bytes + other_bytes
    return {
        "bin_bytes": bin_bytes,
        "meta_bytes": meta_bytes,
        "compressed_sidecar_bytes": compressed_bytes,
        "json_sidecar_bytes": json_bytes,
        "other_bytes": other_bytes,
        "total_bytes": total,
    }


def warp_and_write(
    native: dict[str, Any],
    var_id: str,
    grid: dict[str, Any],
    run_root: Path,
) -> dict[str, Any]:
    """Warp the native field onto a production grid and pack it to a grid frame.

    Mirrors the production 4326-and-3857 publish call shape from
    ``fastpath/subloop.py``: ``transform=`` + ``projection=`` come straight
    from ``get_target_grid``, so both grid families go through the identical
    writer and the artifact bytes match prod.
    """
    rows, cols = grid["shape_rows_cols"]
    dst = np.full((rows, cols), np.nan, dtype="float32")
    reproject(
        source=native["values"],
        destination=dst,
        src_transform=native["transform"],
        src_crs=CRS.from_epsg(4326),
        dst_transform=grid["_transform"],
        dst_crs=CRS.from_string(grid["crs"]),
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,  # bilinear for SST and for anomaly
    )
    write_grid_frame_for_run_root(
        run_root=run_root,
        model=SST_MODEL_ID,
        var=var_id,
        fh=0,
        values=dst,
        transform=grid["_transform"],
        projection=grid["crs"],
    )
    measured = _measure_tree(run_root)
    finite = np.isfinite(dst)
    return {
        "grid": grid["name"],
        "crs": grid["crs"],
        "resolution": grid["resolution"],
        "shape_rows_cols": [rows, cols],
        "warped_valid_fraction": round(float(finite.mean()), 4),
        **measured,
        "_warped": dst,
    }


# ---------------------------------------------------------------------------
# Eyeball PNGs
# ---------------------------------------------------------------------------

_PNG_BACKEND: str | None = None
_PNG_BACKEND_ERROR: str | None = None


def _resolve_png_backend() -> tuple[str | None, str | None]:
    global _PNG_BACKEND, _PNG_BACKEND_ERROR
    if _PNG_BACKEND is not None or _PNG_BACKEND_ERROR is not None:
        return _PNG_BACKEND, _PNG_BACKEND_ERROR
    try:
        import PIL.Image  # noqa: F401, PLC0415

        _PNG_BACKEND = "pillow"
        return _PNG_BACKEND, None
    except Exception:  # noqa: BLE001
        pass
    try:
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg")
        import matplotlib.image  # noqa: F401, PLC0415

        _PNG_BACKEND = "matplotlib"
        return _PNG_BACKEND, None
    except Exception as exc:  # noqa: BLE001
        _PNG_BACKEND_ERROR = (
            f"neither Pillow nor matplotlib importable ({exc!r}); eyeball PNGs skipped"
        )
        return None, _PNG_BACKEND_ERROR


def _ramp_rgba(
    values: np.ndarray,
    stops: tuple[tuple[float, tuple[int, int, int]], ...],
) -> np.ndarray:
    """Linear-interpolate a colour ramp over ``values``; NaN -> transparent.

    NaN pixels get alpha 0 *and* RGB zeroed. Zeroing matters: plenty of image
    viewers ignore the alpha channel, and leaving the ramp-minimum colour under
    a transparent land/ice mask makes every continent read as an extreme cold
    value — exactly the misreading these eyeball PNGs exist to prevent.
    """
    xs = np.array([s[0] for s in stops], dtype="float64")
    finite = np.isfinite(values)
    v = np.where(finite, values, xs[0]).astype("float64")
    rgba = np.zeros((*values.shape, 4), dtype="uint8")
    for channel in range(3):
        ys = np.array([float(s[1][channel]) for s in stops], dtype="float64")
        ramped = np.clip(np.interp(v, xs, ys), 0, 255)
        rgba[..., channel] = np.where(finite, ramped, 0).astype("uint8")
    rgba[..., 3] = np.where(finite, 255, 0).astype("uint8")
    return rgba


def render_eyeball_png(
    values: np.ndarray,
    field_kind: str,
    out_path: Path,
) -> dict[str, Any]:
    """Render a colourmapped RGBA PNG for visual sanity-checking. Never raises."""
    backend, error = _resolve_png_backend()
    if backend is None:
        return {"status": "skipped", "finding": error, "path": None}

    stops = SST_PNG_STOPS if field_kind == "sst" else ANOM_PNG_STOPS
    ramp_range = SST_PNG_RANGE_C if field_kind == "sst" else ANOM_PNG_RANGE_C
    try:
        rgba = _ramp_rgba(values, stops)
        if backend == "pillow":
            from PIL import Image  # noqa: PLC0415

            Image.fromarray(rgba, mode="RGBA").save(out_path)
        else:
            import matplotlib.image as mpimg  # noqa: PLC0415

            mpimg.imsave(out_path, rgba)
    except Exception as exc:  # noqa: BLE001 — a PNG miss is a finding, not a failure
        logger.warning("Eyeball PNG failed for %s: %r", out_path.name, exc)
        return {"status": "error", "finding": repr(exc), "path": None}

    size = out_path.stat().st_size if out_path.exists() else 0
    logger.info("Eyeball PNG: %s (%.2f MiB)", out_path, size / MIB)
    return {
        "status": "ok",
        "backend": backend,
        "path": str(out_path),
        "png_bytes": size,
        "ramp_range_c": list(ramp_range),
    }


# ---------------------------------------------------------------------------
# Geo-Polar DN vs night pairing statistic
# ---------------------------------------------------------------------------

def pairing_diff(dn: dict[str, Any], night: dict[str, Any]) -> dict[str, Any]:
    """Mean/max |DN - night| in °C over commonly valid ocean cells.

    Both lineages share the native 0.05° grid, so the comparison is a direct
    array difference. A geometry mismatch (e.g. one lineage served from a
    different archive layout) is recorded as a finding, not forced.
    """
    a, b = dn["values"], night["values"]
    if a.shape != b.shape:
        return {
            "status": "skipped",
            "finding": (
                f"lineage geometry mismatch: dn {a.shape} vs night {b.shape}; "
                f"pairing diff not computed"
            ),
        }
    both = np.isfinite(a) & np.isfinite(b)
    n = int(both.sum())
    if n == 0:
        return {"status": "skipped", "finding": "no commonly valid cells"}
    delta = np.abs(a[both].astype("float64") - b[both].astype("float64"))
    stats = {
        "status": "ok",
        "common_valid_cells": n,
        "common_valid_fraction": round(float(both.mean()), 4),
        "mean_abs_diff_c": round(float(delta.mean()), 4),
        "max_abs_diff_c": round(float(delta.max()), 4),
        "p99_abs_diff_c": round(float(np.percentile(delta, 99)), 4),
        "interpretation": (
            "mean |DN - night| is the typical °C offset a viewer would see "
            "between DN absolute SST and the night-lineage anomaly baseline"
        ),
    }
    logger.info(
        "Geo-Polar DN vs night: mean |Δ| %.3f °C, max %.3f °C over %d cells",
        stats["mean_abs_diff_c"], stats["max_abs_diff_c"], n,
    )
    return stats


# ---------------------------------------------------------------------------
# Candidate driver
# ---------------------------------------------------------------------------

def measure_candidate(
    spec: CandidateSpec,
    pinned: str | None,
    grids: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fetch, convert and measure one candidate on both grids. Never raises."""
    cid = spec["id"]
    var_id = spec["var_id"]
    field_kind = spec["field_kind"]
    primary = spec["primary_lineage"]
    result: dict[str, Any] = {
        "id": cid,
        "name": spec["name"],
        "var_id": var_id,
        "field_kind": field_kind,
        "status": "pending",
        "primary_lineage": primary,
        "lineages": {},
        "date": None,
    }

    # Primary lineage first — it fixes the date every other lineage must reuse.
    dates = _candidate_dates(spec, pinned)
    primary_fetch = fetch_lineage(spec, primary, dates)
    result["lineages"][primary] = primary_fetch
    if primary_fetch["status"] != "downloaded":
        result["status"] = "no_url"
        result["finding"] = primary_fetch.get("finding", "primary lineage unavailable")
        return result

    date_str = primary_fetch["date"]
    result["date"] = date_str
    result["url"] = primary_fetch["url"]
    result["path_label"] = primary_fetch["path_label"]
    result["raw_nc_bytes"] = primary_fetch["raw_nc_bytes"]

    # Secondary lineages, pinned to the primary's date so any comparison is
    # same-day and apples-to-apples.
    same_day = [datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)]
    natives: dict[str, dict[str, Any]] = {}
    for lineage_key in spec["lineages"]:
        if lineage_key == primary:
            continue
        result["lineages"][lineage_key] = fetch_lineage(spec, lineage_key, same_day)

    # Read every downloaded lineage natively (secondary lineages are read for
    # their stats + the pairing diff; only the primary is warped and written).
    for lineage_key, fetch in result["lineages"].items():
        if fetch["status"] != "downloaded":
            continue
        try:
            native = read_native_field(
                Path(fetch["local_path"]), fetch["subdataset_var"], field_kind
            )
        except Exception as exc:  # noqa: BLE001 — an unreadable file is a finding
            fetch["status"] = "read_failed"
            fetch["finding"] = f"netCDF read/convert failed: {exc!r}"
            logger.exception("%s/%s: netCDF read failed", cid, lineage_key)
            continue
        natives[lineage_key] = native
        fetch["status"] = "read"
        fetch.update(
            {
                "native_dims_wxh": list(native["dims_wxh"]),
                "native_valid_fraction": round(native["valid_fraction"], 4),
                "value_min_c": round(native["value_min_c"], 3),
                "value_max_c": round(native["value_max_c"], 3),
                "rolled_longitude": native["rolled_longitude"],
                "flipped_northup": native["flipped_northup"],
                "source_units": native["source_units"],
                "kelvin_converted": native["kelvin_converted"],
                "units_decision": native["units_decision"],
                "climatology_attrs": native["climatology_attrs"],
            }
        )

    if primary not in natives:
        result["status"] = "read_failed"
        result["finding"] = result["lineages"][primary].get(
            "finding", "primary lineage unreadable"
        )
        return result

    primary_native = natives[primary]
    for key in (
        "native_dims_wxh", "native_valid_fraction", "value_min_c", "value_max_c",
        "source_units", "kelvin_converted", "units_decision", "climatology_attrs",
    ):
        result[key] = result["lineages"][primary][key]

    # Geo-Polar pairing statistic (skipped gracefully when only one lineage came down).
    if cid == "geopolar":
        if "dn" in natives and "night" in natives:
            result["lineage_pairing"] = pairing_diff(natives["dn"], natives["night"])
        else:
            missing = [k for k in ("dn", "night") if k not in natives]
            result["lineage_pairing"] = {
                "status": "skipped",
                "finding": (
                    f"pairing diff skipped — lineage(s) {missing} not downloaded/readable"
                ),
            }

    # Warp + write the primary lineage onto both production grids.
    converted: dict[str, Any] = {}
    pngs: dict[str, Any] = {}
    for grid in grids:
        name = grid["name"]
        # Separate run_root per grid: both write the same var/fh0 filename, so
        # they must not share a grid dir. Staging path is
        # data/staging/sst_{candidate}/{date}/{grid}/ (grid nesting is a
        # deliberate deviation from a bare {date}/, forced by the shared name).
        run_root = STAGING_ROOT / f"sst_{cid}" / date_str / name
        try:
            conv = warp_and_write(primary_native, var_id, grid, run_root)
        except Exception as exc:  # noqa: BLE001
            converted[name] = {"grid": name, "error": repr(exc)}
            logger.exception("%s %s: warp/write failed", cid, name)
            continue
        warped = conv.pop("_warped")
        converted[name] = conv
        logger.info(
            "%s %s (%s %sx%s): bin %.2f MiB, meta %d B, sidecars %.2f MiB, total %.2f MiB",
            cid, name, conv["crs"], conv["shape_rows_cols"][0], conv["shape_rows_cols"][1],
            conv["bin_bytes"] / MIB, conv["meta_bytes"],
            conv["compressed_sidecar_bytes"] / MIB, conv["total_bytes"] / MIB,
        )
        pngs[name] = render_eyeball_png(
            warped, field_kind, PNG_DIR / f"{cid}_{var_id}_{name}_{date_str}.png"
        )

    result["converted"] = converted
    result["eyeball_png"] = pngs
    measured_any = any("bin_bytes" in v for v in converted.values())
    result["status"] = "measured" if measured_any else "convert_failed"
    if not measured_any:
        result["finding"] = "all target grids failed to convert"
    return result


def probe_mur() -> dict[str, Any]:
    """Free, no-auth HEAD of a PO.DAAC MUR granule to document the auth misfit."""
    status, headers, err = _head(MUR_PROBE_URL, allow_redirects=False)
    location = next(
        (v for k, v in headers.items() if k.lower() == "location"), None
    )
    logger.info("MUR probe: status=%s location=%s err=%s", status, location, err)
    interpretation = (
        "requires Earthdata Login (401/redirect); AWS mirror s3://mur-sst is "
        "zarr, which does not fit the single-file netCDF flow. Not downloaded."
    )
    return {
        "id": "mur",
        "name": "MUR (JPL 0.01 deg) — probe only, no download",
        "url": MUR_PROBE_URL,
        "status": status,
        "redirect_location": location,
        "error": err,
        "interpretation": interpretation,
    }


# ---------------------------------------------------------------------------
# Storage projection
# ---------------------------------------------------------------------------

def _per_day_rows(cand: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for grid_name, conv in (cand.get("converted") or {}).items():
        if "total_bytes" not in conv:
            continue
        rows.append(
            {
                "var": cand["var_id"],
                "grid": grid_name,
                "source": cand["id"],
                "bin_bytes": conv["bin_bytes"],
                "meta_bytes": conv["meta_bytes"],
                "compressed_sidecar_bytes": conv["compressed_sidecar_bytes"],
                "json_sidecar_bytes": conv["json_sidecar_bytes"],
                "other_bytes": conv["other_bytes"],
                "total_bytes": conv["total_bytes"],
            }
        )
    return rows


def storage_projection(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Project retention storage from measured on-disk bytes.

    Model: 1 run/day, 1 frame per variable per grid per day. The recommended
    combination is Geo-Polar SST + CRW anomaly, each on both production grids;
    an OISST-as-SST-source alternative is included when OISST was measured.
    Raw ``.nc`` downloads are transient (deleted after conversion) and are
    reported separately, never inside the retained total.
    """
    by_id = {c["id"]: c for c in candidates if c.get("status") == "measured"}
    anom = by_id.get("crw_anom")

    scenarios: list[dict[str, Any]] = []
    for sst_source in ("geopolar", "oisst"):
        sst = by_id.get(sst_source)
        if sst is None:
            continue
        rows = _per_day_rows(sst) + (_per_day_rows(anom) if anom else [])
        if not rows:
            continue
        per_day = sum(r["total_bytes"] for r in rows)
        scenarios.append(
            {
                "sst_source": sst_source,
                "anomaly_source": anom["id"] if anom else None,
                "recommended": sst_source == "geopolar",
                "complete": anom is not None,
                "note": None
                if anom is not None
                else "anomaly variable NOT measured — SST-only per-day figure",
                "rows": rows,
                "frames_per_day": len(rows),
                "per_day_bytes": per_day,
                "retention": {
                    str(days): {
                        "days": days,
                        "total_bytes": per_day * days,
                        "total_gib": round(per_day * days / GIB, 3),
                    }
                    for days in RETENTION_DAYS
                },
            }
        )

    transient = []
    for cand in candidates:
        for lineage_key, fetch in (cand.get("lineages") or {}).items():
            if fetch.get("raw_nc_bytes"):
                transient.append(
                    {
                        "candidate": cand["id"],
                        "lineage": lineage_key,
                        "path_label": fetch.get("path_label"),
                        "raw_nc_bytes": fetch["raw_nc_bytes"],
                        "download_seconds": fetch.get("download_seconds"),
                    }
                )

    return {
        "model": (
            "1 run/day, 1 frame per variable per grid per day; measured on-disk "
            "bytes (bin + meta + json/gz/br sidecars) as the per-day unit"
        ),
        "retention_days": list(RETENTION_DAYS),
        "scenarios": scenarios,
        "transient_raw_downloads": transient,
        "transient_note": (
            "raw .nc downloads are transient working files — deleted after "
            "conversion unless --keep; they size peak scratch, not retention"
        ),
    }


# ---------------------------------------------------------------------------
# Plan-only
# ---------------------------------------------------------------------------

def print_plan(selected: list[str], grids: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 78)
    lines.append("  SST SIZING v2 — PLAN ONLY (offline; no fetch, no conversion)")
    lines.append("=" * 78)
    lines.append("  Display units: °C ONLY (no °F anywhere in this spike).")
    lines.append(f"  Pinned-grid constants from: {PINNED['source']}")
    lines.append("  Production target grids (resolved via get_target_grid):")
    for g in grids:
        rows, cols = g["shape_rows_cols"]
        lines.append(
            f"      {g['name']:<7} {g['crs']:<10} res {g['resolution']:<9} "
            f"{rows} rows x {cols} cols"
        )
        lines.append(f"              bbox {g['snapped_bbox']}")
    lines.append("  4326 write path: write_grid_frame_for_run_root(transform=…, "
                 "projection='EPSG:4326')")
    lines.append("              — the same call shape production uses in "
                 "fastpath/subloop.py")
    lines.append("  " + "-" * 74)
    lines.append("  Packing targets (both uint16 @ 0.01 °C):")
    lines.append(f"      ({SST_MODEL_ID}, {SST_VAR_ID:<8}) {SST_PACKING}")
    lines.append(f"      ({SST_MODEL_ID}, {SST_ANOM_VAR_ID:<8}) {SST_ANOM_PACKING}")
    lines.append("  " + "-" * 74)
    lines.append(
        f"  HTTP User-Agent: {HTTP_HEADERS['User-Agent']}  "
        f"(required — ERDDAP 403s the default python-requests UA)"
    )
    lines.append("  " + "-" * 74)
    lines.append("  Download candidates:")
    sample = datetime(2026, 8, 3, tzinfo=timezone.utc)
    for cid in selected:
        spec = CANDIDATES[cid]
        lines.append(f"    [{cid}] {spec['name']}")
        lines.append(
            f"        var / field    : {spec['var_id']} / {spec['field_kind']}"
            f"   walk-back up to {spec['walkback_days']} days from today-2"
        )
        for lineage_key, lineage in spec["lineages"].items():
            primary = " (PRIMARY)" if lineage_key == spec["primary_lineage"] else ""
            lines.append(f"        lineage {lineage_key}{primary}: {lineage['name']}")
            lines.append(f"            var  : {lineage['subdataset_var']}")
            lines.append(f"            note : {lineage['note']}")
            for path in lineage["paths_for_date"](sample):
                lines.append(
                    f"            [{path['probe']:<4}] {path['label']}: {path['url']}"
                )
    lines.append("  " + "-" * 74)
    lines.append("  MUR (probe only, always runs):")
    lines.append(f"      {MUR_PROBE_URL}")
    lines.append("  " + "-" * 74)
    lines.append("  Storage projection model:")
    lines.append("      1 run/day, 1 frame per variable per grid per day.")
    lines.append(
        "      Retained per day = measured bytes for {sst@na, sst@global, "
        "sst_anom@na, sst_anom@global}."
    )
    lines.append(
        f"      Retention windows projected: {', '.join(str(d) for d in RETENTION_DAYS)} days."
    )
    lines.append("      Raw .nc downloads are transient and reported separately.")
    lines.append("  " + "-" * 74)
    lines.append(f"  Eyeball PNGs would be written to: {PNG_DIR}")
    lines.append(
        f"      SST ramp {SST_PNG_RANGE_C} °C dark blue→cyan→yellow→red; "
        f"anomaly ramp {ANOM_PNG_RANGE_C} °C blue→white→red; NaN transparent."
    )
    lines.append("=" * 78)
    print("\n".join(lines), file=sys.stdout, flush=True)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _fmt_mib(num_bytes: int) -> str:
    return f"{num_bytes / MIB:.2f} MiB"


def print_summary(report: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 78)
    lines.append("  SST SIZING SPIKE v2 — SUMMARY  (display units: °C only)")
    lines.append("=" * 78)
    lines.append("  Production target grids:")
    for g in report["target_grids"]:
        rows, cols = g["shape_rows_cols"]
        lines.append(
            f"      {g['name']:<7} {g['crs']:<10} res {g['resolution']:<9} "
            f"{rows} x {cols} (rows x cols)"
        )

    for cand in report["candidates"]:
        lines.append("  " + "-" * 74)
        lines.append(f"  [{cand['id']}] {cand['name']}")
        lines.append(f"      status : {cand['status']}   var: {cand['var_id']}")
        if cand.get("finding"):
            lines.append(f"      FINDING: {cand['finding']}")
        for lineage_key, fetch in (cand.get("lineages") or {}).items():
            tag = "PRIMARY" if lineage_key == cand.get("primary_lineage") else "secondary"
            lines.append(f"      lineage {lineage_key} ({tag}): {fetch['status']}")
            if fetch.get("finding"):
                lines.append(f"          FINDING: {fetch['finding']}")
            if fetch.get("url"):
                lines.append(
                    f"          day {fetch['date']} via {fetch['path_label']} "
                    f"— raw {_fmt_mib(fetch['raw_nc_bytes'])} in "
                    f"{fetch.get('download_seconds')}s"
                )
                lines.append(f"          {fetch['url']}")
            if fetch.get("native_dims_wxh"):
                lines.append(
                    f"          native {fetch['native_dims_wxh'][0]} x "
                    f"{fetch['native_dims_wxh'][1]}  valid "
                    f"{fetch['native_valid_fraction'] * 100:.1f}%  "
                    f"{fetch['value_min_c']:.2f}..{fetch['value_max_c']:.2f} °C"
                )
                lines.append(
                    f"          units: {fetch['units_decision']} "
                    f"(kelvin_converted={fetch['kelvin_converted']})"
                )
            probed = len(fetch.get("probe_log") or [])
            failed = [
                e for e in (fetch.get("probe_log") or [])
                if e.get("status") not in (200, None) or e.get("download_ok") is False
            ]
            if failed:
                lines.append(f"          fetch-path attempts: {probed}, non-working:")
                for e in failed[:6]:
                    detail = e.get("download_error") or e.get("error") or f"HTTP {e.get('status')}"
                    lines.append(f"            - {e['path_label']} ({e['date']}): {detail}")

        pairing = cand.get("lineage_pairing")
        if pairing:
            if pairing.get("status") == "ok":
                lines.append(
                    f"      DN vs NIGHT pairing: mean |Δ| {pairing['mean_abs_diff_c']:.3f} °C, "
                    f"p99 {pairing['p99_abs_diff_c']:.3f} °C, max "
                    f"{pairing['max_abs_diff_c']:.3f} °C over "
                    f"{pairing['common_valid_cells']} cells"
                )
            else:
                lines.append(f"      DN vs NIGHT pairing: {pairing.get('finding')}")

        for grid_name, conv in (cand.get("converted") or {}).items():
            if "bin_bytes" in conv:
                lines.append(
                    f"      conv {grid_name:<7}: bin {_fmt_mib(conv['bin_bytes'])}  "
                    f"meta {conv['meta_bytes']} B  "
                    f"gz/br {_fmt_mib(conv['compressed_sidecar_bytes'])}  "
                    f"json {_fmt_mib(conv['json_sidecar_bytes'])}  "
                    f"TOTAL {_fmt_mib(conv['total_bytes'])}  "
                    f"valid {conv['warped_valid_fraction'] * 100:.1f}%"
                )
            else:
                lines.append(f"      conv {grid_name:<7}: ERROR {conv.get('error')}")
        for grid_name, png in (cand.get("eyeball_png") or {}).items():
            if png.get("status") == "ok":
                lines.append(f"      png  {grid_name:<7}: {png['path']} ({_fmt_mib(png['png_bytes'])})")
            else:
                lines.append(f"      png  {grid_name:<7}: {png.get('status')} — {png.get('finding')}")

    mur = report["mur_probe"]
    lines.append("  " + "-" * 74)
    lines.append(f"  [mur] {mur['name']}")
    lines.append(f"      HEAD status : {mur['status']}  redirect: {mur.get('redirect_location')}")
    lines.append(f"      finding     : {mur['interpretation']}")

    # ── Storage projection ──
    proj = report["storage_projection"]
    lines.append("  " + "=" * 74)
    lines.append("  STORAGE PROJECTION")
    lines.append(f"      model: {proj['model']}")
    if not proj["scenarios"]:
        lines.append("      (no scenario measurable — see findings)")
    for sc in proj["scenarios"]:
        tag = "RECOMMENDED" if sc["recommended"] else "alternative"
        lines.append("  " + "-" * 74)
        lines.append(
            f"      [{tag}] SST={sc['sst_source']}  anomaly={sc['anomaly_source']}"
        )
        if sc["note"]:
            lines.append(f"          NOTE: {sc['note']}")
        lines.append(f"          {'var':<10} {'grid':<8} {'source':<10} per-day bytes")
        for r in sc["rows"]:
            lines.append(
                f"          {r['var']:<10} {r['grid']:<8} {r['source']:<10} "
                f"{r['total_bytes']:>12,}  ({_fmt_mib(r['total_bytes'])})"
            )
        lines.append(
            f"          {'PER DAY':<10} {sc['frames_per_day']} frames"
            f"{'':<13}{sc['per_day_bytes']:>12,}  ({_fmt_mib(sc['per_day_bytes'])})"
        )
        lines.append(f"          {'retention':<12} {'total':>16} {'GiB':>10}")
        for days in proj["retention_days"]:
            row = sc["retention"][str(days)]
            lines.append(
                f"          {str(days) + ' days':<12} {row['total_bytes']:>16,} "
                f"{row['total_gib']:>10.3f}"
            )
    lines.append("  " + "-" * 74)
    lines.append(f"      {proj['transient_note']}")
    for t in proj["transient_raw_downloads"]:
        lines.append(
            f"          raw .nc {t['candidate']}/{t['lineage']} via {t['path_label']}: "
            f"{_fmt_mib(t['raw_nc_bytes'])} in {t['download_seconds']}s"
        )

    lines.append("  " + "-" * 74)
    lines.append("  FINDINGS:")
    for note in report["findings"]:
        lines.append(f"      - {note}")
    lines.append("=" * 78)
    lines.append(f"  Report JSON : {report['report_path']}")
    lines.append(f"  Eyeball PNGs: {report['png_dir']}")
    lines.append("=" * 78)
    print("\n".join(lines), file=sys.stdout, flush=True)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _safe_unlink(path: Path) -> None:
    resolved = path.resolve()
    if _is_within(resolved, SST_ROOT):
        try:
            resolved.unlink()
        except OSError:
            pass


def _safe_rmtree(path: Path, label: str) -> None:
    resolved = path.resolve()
    if not (_is_within(resolved, SST_ROOT) or resolved == SST_ROOT.resolve()):
        logger.error("Refusing to delete %s (%s): outside sst root %s", resolved, label, SST_ROOT)
        return
    for live in (LIVE_DATA_ROOT, LIVE_HERBIE_CACHE):
        if resolved == live.resolve():
            logger.error("Refusing to delete live path %s (%s)", resolved, label)
            return
    shutil.rmtree(resolved, ignore_errors=True)
    logger.info("Deleted %s: %s", label, resolved)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    selected = list(ALL_CANDIDATES) if args.candidate == "all" else [args.candidate]

    grids = target_grids()
    for g in grids:
        rows, cols = g["shape_rows_cols"]
        logger.info(
            "Target grid %-7s %s res=%s %dx%d bbox=%s",
            g["name"], g["crs"], g["resolution"], rows, cols, g["snapped_bbox"],
        )

    if args.plan_only:
        print_plan(selected, grids)
        logger.info("--plan-only: nothing fetched or converted. Exiting.")
        return 0

    inject_sst_packing()

    candidates: list[dict[str, Any]] = []
    for cid in selected:
        logger.info("=" * 60)
        logger.info("Candidate: %s", cid)
        candidates.append(measure_candidate(CANDIDATES[cid], args.date, grids))

    mur = probe_mur()
    proj = storage_projection(candidates)

    # Findings roll-up.
    findings: list[str] = []
    for cand in candidates:
        if cand["status"] == "measured":
            parts = [
                f"{name}={c['total_bytes'] / MIB:.1f} MiB"
                for name, c in cand["converted"].items()
                if "total_bytes" in c
            ]
            findings.append(
                f"{cand['id']}: OK via {cand.get('path_label')} ({cand.get('date')}) — "
                f"raw {cand['raw_nc_bytes'] / MIB:.1f} MiB, converted "
                + ", ".join(parts)
            )
        else:
            findings.append(f"{cand['id']}: {cand['status']} — {cand.get('finding', '')}")
        pairing = cand.get("lineage_pairing")
        if pairing and pairing.get("status") == "ok":
            findings.append(
                f"{cand['id']}: DN vs night mean |Δ| {pairing['mean_abs_diff_c']:.3f} °C, "
                f"max {pairing['max_abs_diff_c']:.3f} °C — bears on showing DN "
                f"absolute SST beside the night-lineage anomaly"
            )
        elif pairing:
            findings.append(f"{cand['id']}: {pairing.get('finding')}")
        for name, png in (cand.get("eyeball_png") or {}).items():
            if png.get("status") != "ok":
                findings.append(f"{cand['id']}/{name}: eyeball PNG {png.get('status')} — {png.get('finding')}")
    findings.append(f"mur: probe HEAD status={mur['status']} — {mur['interpretation']}")
    for sc in proj["scenarios"]:
        findings.append(
            f"storage[{'recommended' if sc['recommended'] else 'alternative'}] "
            f"SST={sc['sst_source']}+anom={sc['anomaly_source']}: "
            f"{sc['per_day_bytes'] / MIB:.1f} MiB/day -> "
            f"{sc['retention'][str(RETENTION_DAYS[-1])]['total_gib']:.2f} GiB at "
            f"{RETENTION_DAYS[-1]} days"
            + (f" [{sc['note']}]" if sc["note"] else "")
        )
    if not proj["scenarios"]:
        findings.append("storage: no scenario measurable — no SST candidate converted")

    date_tag = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")
    report_path = REPORTS_DIR / f"sst_sizing_{date_tag}.json"

    report: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "spike_version": 2,
        "display_units": "C",
        "display_units_note": "°C only — no °F support anywhere in this spike",
        "args": {
            "dev_root": args.dev_root,
            "date": args.date,
            "candidate": args.candidate,
            "keep": args.keep,
        },
        "http_user_agent": HTTP_HEADERS["User-Agent"],
        "http_user_agent_note": (
            "CoastWatch ERDDAP 403s the default python-requests User-Agent; an "
            "explicit UA is required, not cosmetic"
        ),
        "pinned_grid_constants_source": PINNED["source"],
        "target_grids": _grids_for_report(grids),
        "grid_writer_note": (
            "both grid families go through write_grid_frame_for_run_root with "
            "transform= + projection= from get_target_grid — the same call shape "
            "production uses for 4326 publishes (fastpath/subloop.py)"
        ),
        "packing": {SST_VAR_ID: SST_PACKING, SST_ANOM_VAR_ID: SST_ANOM_PACKING},
        "candidates": candidates,
        "mur_probe": mur,
        "storage_projection": proj,
        "findings": findings,
        "report_path": str(report_path),
        "png_dir": str(PNG_DIR),
    }

    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    logger.info("Wrote report: %s", report_path)

    print_summary(report)

    # Cleanup AFTER measuring + report (reports and PNGs persist regardless).
    if not args.keep:
        for cand in candidates:
            for fetch in (cand.get("lineages") or {}).values():
                if fetch.get("local_path"):
                    _safe_unlink(Path(fetch["local_path"]))
            if cand.get("date"):
                _safe_rmtree(STAGING_ROOT / f"sst_{cand['id']}", "converted staging")

    measured = [c for c in candidates if c["status"] == "measured"]
    if REQUIRED_CANDIDATE in selected and not any(
        c["id"] == REQUIRED_CANDIDATE and c["status"] == "measured" for c in candidates
    ):
        logger.error(
            "%s (the recommended upstream) did not produce measurements; "
            "treating as failure.", REQUIRED_CANDIDATE,
        )
        return 1
    if not measured:
        logger.error("No candidate could be measured.")
        return 1
    return 0


def main() -> int:
    try:
        return run(ARGS)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
