#!/usr/bin/env python3
"""Disk-sizing spike (Phase 3): measure SST candidate sources for CartoSky.

CartoSky is evaluating adding SST (sea-surface temperature) as a new data
source. This standalone script measures, for 1-2 unauthenticated daily SST
products, the raw netCDF download size and the converted binary-grid-artifact
size at the two Web Mercator resolutions used by the model grids measured in
Phase 2 (25000 m -> 1604x1604 px and 9000 m -> 4454x4454 px). The numbers feed
a block-storage sizing decision.

It reuses the CURRENT binary artifact writer
(``app.services.grid.write_grid_frame_for_run_root``) directly — no model
plugin, no ``build_frame``, no Herbie. The SST source is opened via the
rasterio/GDAL netCDF subdataset path, scale/offset/fill are applied, the
0-360 longitude layout (OISST) is rolled to -180..180, and the field is warped
to the global Web Mercator grid with bilinear resampling before being packed
through the same encode -> ``.bin`` + ``.meta.json`` path production uses.

Candidates:
  * OISST v2.1  — NOAA 0.25 deg daily AVHRR OI, netCDF, no auth. Finals lag
    ~2 weeks; a ``_preliminary`` file usually exists within a few days, so the
    walk-back tries both the final and the preliminary filename per day.
  * NOAA Geo-Polar Blended — 0.05 deg / 5 km daily L4 (GHRSST convention),
    no auth. Hosting varies, so an ordered list of URL templates is probed and
    the first that returns a file is used. If none works that is recorded as a
    FINDING ("no working unauthenticated URL found"), not a script failure.
  * MUR (JPL 0.01 deg) — PROBE ONLY, no download. A PO.DAAC HTTPS granule is
    HEADed (no auth) to demonstrate the Earthdata-auth misfit; the AWS mirror
    (s3://mur-sst) is zarr, which does not fit the single-file netCDF flow.

Isolation contract (this script must be INCAPABLE of touching live data):
  * everything it writes lives under ``{dev_root}/sst/``:
      downloads -> ``{dev_root}/sst/downloads``
      converted -> ``{dev_root}/sst/data/staging/sst_{candidate}/{date}/...``
      reports   -> ``{dev_root}/sst/reports``
  * refuses to run as root (euid==0).
  * refuses any resolved write path at or inside ``/opt/cartosky/data`` or
    ``/opt/cartosky/herbie_cache_ssd``; deletions only happen inside the dev
    ``sst`` subtree.

All argument parsing happens BEFORE any app import so ``--help`` and
``--plan-only`` work on any machine with no prod env and no network.

Usage examples::

    # Measure all candidates, auto walk-back to the latest available day:
    python backend/scripts/measure_sst_sizing.py

    # Offline plan (no network): print candidates, URL templates, grids, exit:
    python backend/scripts/measure_sst_sizing.py --dev-root /tmp/sst --plan-only

    # Only OISST, pin the date, keep downloads + converted output:
    python backend/scripts/measure_sst_sizing.py --candidate oisst \\
        --date 20260720 --keep

Exit codes::
  0   at least the OISST candidate produced measurements (per-candidate misses
      are recorded as findings, not fatal)
  1   usage / safety-guard / environment error, OR nothing could be measured
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

# Global extent in EPSG:3857 (±180°, ±85.051129°). Square domain — matches the
# Phase 2 global model grids.
GLOBAL_BBOX_3857 = (
    -20037508.342789244,
    -20037508.342789244,
    20037508.342789244,
    20037508.342789244,
)

# (grid_meters, label). 25000 m -> 1604², 9000 m -> 4454² for the global bbox.
RESOLUTIONS: tuple[tuple[int, str], ...] = ((25000, "25000m"), (9000, "9000m"))

LIVE_DATA_ROOT = Path("/opt/cartosky/data")
LIVE_HERBIE_CACHE = Path("/opt/cartosky/herbie_cache_ssd")

# SST packing target injected at runtime into the grid module's mutable
# packing table (there is no real SST model/var yet). uint16 @ 0.01 °C, offset
# -5 °C -> representable range -5..650 °C, which comfortably covers ocean SST
# (roughly -2..36 °C) at 0.01 °C precision. 2 bytes/pixel, the realistic pack.
SST_MODEL_ID = "sst"
SST_VAR_ID = "sst"
SST_PACKING = {"scale": 0.01, "offset": -5.0, "nodata": 65535, "units": "C"}

# HTTP timeouts: (connect, read).
HTTP_TIMEOUT = (10.0, 120.0)
DOWNLOAD_ATTEMPTS = 2

logger = logging.getLogger("measure_sst_sizing")


# ---------------------------------------------------------------------------
# Candidate definitions
# ---------------------------------------------------------------------------

def _oisst_urls_for_date(d: datetime) -> list[tuple[str, str]]:
    """(label, url) pairs to try for one OISST day: final first, then prelim."""
    base = (
        "https://www.ncei.noaa.gov/data/"
        "sea-surface-temperature-optimum-interpolation/v2.1/access/avhrr/"
        "{ym}/oisst-avhrr-v02r01.{ymd}{suffix}.nc"
    )
    ym = d.strftime("%Y%m")
    ymd = d.strftime("%Y%m%d")
    return [
        ("final", base.format(ym=ym, ymd=ymd, suffix="")),
        ("preliminary", base.format(ym=ym, ymd=ymd, suffix="_preliminary")),
    ]


# Ordered Geo-Polar Blended URL templates. First that yields a file wins.
# {Y}=year, {DDD}=day-of-year (zero-padded), {ymd}=YYYYMMDD.
GEOPOLAR_TEMPLATES: tuple[str, ...] = (
    # NCEI GHRSST archive (daytime blend).
    "https://www.ncei.noaa.gov/data/oceans/ghrsst/L4/GLOB/OSPO/"
    "Geo_Polar_Blended/{Y}/{DDD}/"
    "{ymd}000000-OSPO-L4_GHRSST-SSTfnd-Geo_Polar_Blended-GLOB-v02.0-fv01.0.nc",
    # NCEI GHRSST archive (night blend) — fallback if the day blend is absent.
    "https://www.ncei.noaa.gov/data/oceans/ghrsst/L4/GLOB/OSPO/"
    "Geo_Polar_Blended_Night/{Y}/{DDD}/"
    "{ymd}000000-OSPO-L4_GHRSST-SSTfnd-Geo_Polar_Blended_Night-GLOB-v02.0-fv01.0.nc",
    # CoastWatch THREDDS fileServer (path may drift; recorded as a probe either way).
    "https://coastwatch.noaa.gov/thredds/fileServer/CoastWatch/GEO-POLAR-BLENDED/"
    "{Y}/{DDD}/"
    "{ymd}000000-OSPO-L4_GHRSST-SSTfnd-Geo_Polar_Blended-GLOB-v02.0-fv01.0.nc",
)


def _geopolar_urls_for_date(d: datetime) -> list[tuple[str, str]]:
    y = d.strftime("%Y")
    ddd = d.strftime("%j")
    ymd = d.strftime("%Y%m%d")
    out: list[tuple[str, str]] = []
    for i, tmpl in enumerate(GEOPOLAR_TEMPLATES):
        out.append((f"template{i}", tmpl.format(Y=y, DDD=ddd, ymd=ymd)))
    return out


# MUR probe: a representative PO.DAAC HTTPS granule that requires Earthdata auth.
MUR_PROBE_URL = (
    "https://archive.podaac.earthdata.nasa.gov/podaac-ops-cumulus-protected/"
    "MUR-JPL-L4-GLOB-v4.1/"
    "20260101090000-JPL-L4_GHRSST-SSTfnd-MUR-GLOB-v02.0-fv04.1.nc"
)


CandidateSpec = dict[str, Any]

CANDIDATES: dict[str, CandidateSpec] = {
    "oisst": {
        "id": "oisst",
        "name": "OISST v2.1 (NOAA 0.25 deg daily AVHRR OI)",
        "subdataset_var": "sst",
        "kelvin": False,
        "walkback_days": 14,
        "urls_for_date": _oisst_urls_for_date,
        "url_note": "final lags ~2wk; _preliminary tried per day",
    },
    "geopolar": {
        "id": "geopolar",
        "name": "NOAA Geo-Polar Blended (0.05 deg / 5 km daily L4 GHRSST)",
        "subdataset_var": "analysed_sst",
        "kelvin": True,
        "walkback_days": 7,
        "urls_for_date": _geopolar_urls_for_date,
        "url_note": "ordered template probe; first working wins",
    },
}


# ---------------------------------------------------------------------------
# Argument parsing (runs BEFORE any heavy/app import so --help works anywhere)
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="measure_sst_sizing.py",
        description="Measure raw + converted disk cost of SST candidate sources.",
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
        choices=["oisst", "geopolar", "all"],
        default="all",
        help="Which download candidate(s) to measure. The MUR probe always "
        "runs regardless (it is a free HEAD, no download).",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep downloads + converted output after measuring "
        "(default: delete; reports always persist).",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Offline: print candidates, URL templates, planned grids, exit. "
        "No network, no fetching, no conversion.",
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

    Global SST source grids cover ±90° but Web Mercator only represents
    ±85.05°, so GDAL/PROJ warns about every polar row it (correctly) drops
    during the warp. Only this exact message is filtered; all other GDAL/PROJ
    warnings still surface.
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


def _resolve_paths(dev_root: str) -> tuple[Path, Path, Path, Path]:
    """Return (sst_root, downloads_dir, staging_root, reports_dir), all under dev_root."""
    root = Path(dev_root).expanduser().resolve()
    sst_root = root / "sst"
    downloads_dir = sst_root / "downloads"
    staging_root = sst_root / "data" / "staging"
    reports_dir = sst_root / "reports"
    return sst_root, downloads_dir, staging_root, reports_dir


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


SST_ROOT, DOWNLOADS_DIR, STAGING_ROOT, REPORTS_DIR = _resolve_paths(ARGS.dev_root)
_enforce_safety(SST_ROOT)

SST_ROOT.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
STAGING_ROOT.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("GDAL_CACHEMAX", "256")

logger.info("Confined sst root  : %s", SST_ROOT)
logger.info("Downloads dir      : %s", DOWNLOADS_DIR)
logger.info("Staging root       : %s", STAGING_ROOT)
logger.info("Reports dir        : %s", REPORTS_DIR)


# ---------------------------------------------------------------------------
# App / heavy imports (deferred so --help stays import-free)
# ---------------------------------------------------------------------------

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import requests  # noqa: E402
from rasterio.crs import CRS  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import Resampling, reproject  # noqa: E402

from app.services.builder.cog_writer import compute_transform_and_shape  # noqa: E402
from app.services.grid import (  # noqa: E402
    _PACKING_BY_MODEL_VAR,
    write_grid_frame_for_run_root,
)

MIB = 1024.0 * 1024.0


def inject_sst_packing() -> None:
    """Register the SST pack target in the grid module's mutable packing table.

    There is no real SST model/var, so the writer would raise
    ``Unsupported grid pack target``. The table is a plain module dict; adding
    a process-local entry mirrors the sibling script's in-memory region
    injection and changes nothing on disk or for any other (model, var).
    """
    _PACKING_BY_MODEL_VAR[(SST_MODEL_ID, SST_VAR_ID)] = dict(SST_PACKING)
    logger.info(
        "Injected packing for (%s, %s): %s",
        SST_MODEL_ID, SST_VAR_ID, SST_PACKING,
    )


# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

def _head(url: str, *, allow_redirects: bool = True) -> tuple[int | None, dict[str, str], str | None]:
    """HEAD a URL. Returns (status_code|None, headers, error|None). Never raises."""
    try:
        resp = requests.head(url, timeout=HTTP_TIMEOUT, allow_redirects=allow_redirects)
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
            with requests.get(url, stream=True, timeout=HTTP_TIMEOUT) as resp:
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
# URL resolution (probe / walk-back)
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


def resolve_url(
    spec: CandidateSpec, pinned: str | None
) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    """Walk candidate days/templates, HEAD each, return the first that exists.

    Returns (url|None, date_str|None, probe_log). ``probe_log`` records every
    HEAD outcome so a total miss is a fully-documented finding.
    """
    urls_for_date: Callable[[datetime], list[tuple[str, str]]] = spec["urls_for_date"]
    probe_log: list[dict[str, Any]] = []
    for d in _candidate_dates(spec, pinned):
        for label, url in urls_for_date(d):
            status, headers, err = _head(url)
            entry = {
                "date": d.strftime("%Y%m%d"),
                "variant": label,
                "url": url,
                "status": status,
                "content_length": headers.get("Content-Length"),
                "error": err,
            }
            probe_log.append(entry)
            if status == 200:
                logger.info(
                    "Resolved %s: %s (%s) -> %s",
                    spec["id"], d.strftime("%Y%m%d"), label, url,
                )
                return url, d.strftime("%Y%m%d"), probe_log
    return None, None, probe_log


# ---------------------------------------------------------------------------
# netCDF read + warp + write
# ---------------------------------------------------------------------------

def read_native_sst(path: Path, var: str, kelvin: bool) -> dict[str, Any]:
    """Open the SST subdataset, apply scale/offset/fill, normalize orientation.

    GDAL's netCDF driver already returns the field north-up (top row = north)
    with a valid geotransform, but leaves raw packed integers and does not
    apply scale/offset. This:
      * applies ``value = raw * scale + offset`` and masks the fill code to NaN,
      * converts Kelvin -> °C when ``kelvin`` is set,
      * rolls a 0..360 longitude layout to -180..180 and rebuilds the transform.

    Returns a dict with the float32 values, an EPSG:4326 Affine transform, the
    native dims, valid fraction, and value min/max (°C).
    """
    subdataset = f'NETCDF:"{path}":{var}'
    with rasterio.open(subdataset) as ds:
        raw = ds.read(1).astype("float64")
        scale = ds.scales[0] if ds.scales else 1.0
        offset = ds.offsets[0] if ds.offsets else 0.0
        nodata = ds.nodatavals[0] if ds.nodatavals else None
        tr = ds.transform
        width, height = ds.width, ds.height
        left, right, top = ds.bounds.left, ds.bounds.right, ds.bounds.top
        xres, yres = tr.a, -tr.e

    values = raw * float(scale) + float(offset)
    if nodata is not None:
        values[raw == nodata] = np.nan
    if kelvin:
        values = values - 273.15

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
    transform = from_origin(west, top, xres, yres)
    finite = np.isfinite(values)
    valid_fraction = float(finite.mean())
    vmin = float(np.nanmin(values)) if finite.any() else float("nan")
    vmax = float(np.nanmax(values)) if finite.any() else float("nan")
    return {
        "values": values,
        "transform": transform,
        "dims_wxh": (width, height),
        "valid_fraction": valid_fraction,
        "value_min_c": vmin,
        "value_max_c": vmax,
        "rolled_longitude": rolled,
    }


def warp_and_write(
    native: dict[str, Any],
    grid_meters: int,
    run_root: Path,
) -> dict[str, Any]:
    """Warp the native field to global Web Mercator and pack it to a grid frame.

    Returns per-resolution byte + dimension detail read back off disk.
    """
    dst_transform, dst_h, dst_w = compute_transform_and_shape(GLOBAL_BBOX_3857, grid_meters)
    dst = np.full((dst_h, dst_w), np.nan, dtype="float32")
    reproject(
        source=native["values"],
        destination=dst,
        src_transform=native["transform"],
        src_crs=CRS.from_epsg(4326),
        dst_transform=dst_transform,
        dst_crs=CRS.from_epsg(3857),
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    write_grid_frame_for_run_root(
        run_root=run_root,
        model=SST_MODEL_ID,
        var=SST_VAR_ID,
        fh=0,
        values=dst,
        bbox=list(GLOBAL_BBOX_3857),
    )
    grid_dir = run_root / SST_VAR_ID / "grid"
    bin_bytes = 0
    meta_bytes = 0
    other_bytes = 0
    for p in grid_dir.iterdir():
        try:
            size = p.stat().st_size
        except OSError:
            continue
        name = p.name.lower()
        if name.endswith(".bin"):
            bin_bytes += size
        elif name.endswith(".meta.json"):
            meta_bytes += size
        else:
            other_bytes += size
    return {
        "grid_meters": grid_meters,
        "grid_dims_wxh": [dst_w, dst_h],
        "bin_bytes": bin_bytes,
        "meta_bytes": meta_bytes,
        "sidecar_bytes": other_bytes,
        "total_bytes": bin_bytes + meta_bytes + other_bytes,
    }


# ---------------------------------------------------------------------------
# Candidate driver
# ---------------------------------------------------------------------------

def measure_candidate(spec: CandidateSpec, pinned: str | None) -> dict[str, Any]:
    """Resolve, download, convert and measure one candidate. Never raises."""
    cid = spec["id"]
    result: dict[str, Any] = {
        "id": cid,
        "name": spec["name"],
        "status": "pending",
        "url": None,
        "date": None,
        "url_note": spec.get("url_note"),
        "probe_log": [],
    }
    url, date_str, probe_log = resolve_url(spec, pinned)
    result["probe_log"] = probe_log
    if url is None:
        result["status"] = "no_url"
        result["finding"] = "no working unauthenticated URL found"
        logger.warning("%s: %s (probed %d URLs)", cid, result["finding"], len(probe_log))
        return result
    result["url"] = url
    result["date"] = date_str

    dest = DOWNLOADS_DIR / f"sst_{cid}_{date_str}.nc"
    ok, err = _download(url, dest)
    if not ok:
        result["status"] = "download_failed"
        result["finding"] = f"download failed: {err}"
        logger.warning("%s: download failed: %s", cid, err)
        return result
    result["raw_nc_bytes"] = dest.stat().st_size

    try:
        native = read_native_sst(dest, spec["subdataset_var"], bool(spec["kelvin"]))
    except Exception as exc:  # noqa: BLE001 — an unreadable file is a finding
        result["status"] = "read_failed"
        result["finding"] = f"netCDF read/convert failed: {exc!r}"
        logger.exception("%s: netCDF read failed", cid)
        if not ARGS.keep:
            _safe_unlink(dest)
        return result

    result["native_dims_wxh"] = list(native["dims_wxh"])
    result["native_valid_fraction"] = round(native["valid_fraction"], 4)
    result["value_min_c"] = round(native["value_min_c"], 3)
    result["value_max_c"] = round(native["value_max_c"], 3)
    result["rolled_longitude"] = native["rolled_longitude"]

    converted: dict[str, Any] = {}
    for grid_meters, label in RESOLUTIONS:
        # Separate run_root per resolution: both write var "sst"/fh0 with an
        # identical filename, so they must not share a grid dir. Staging path is
        # data/staging/sst_{candidate}/{date}/{res_label}/ (resolution nesting
        # is a deliberate deviation from the bare {date}/ in the spec, forced by
        # the shared filename).
        run_root = STAGING_ROOT / f"sst_{cid}" / date_str / label
        try:
            converted[label] = warp_and_write(native, grid_meters, run_root)
            logger.info(
                "%s %s: %dx%d, bin %.2f MiB, meta %d B",
                cid, label,
                converted[label]["grid_dims_wxh"][0],
                converted[label]["grid_dims_wxh"][1],
                converted[label]["bin_bytes"] / MIB,
                converted[label]["meta_bytes"],
            )
        except Exception as exc:  # noqa: BLE001
            converted[label] = {"grid_meters": grid_meters, "error": repr(exc)}
            logger.exception("%s %s: warp/write failed", cid, label)

    result["converted"] = converted
    measured_any = any("bin_bytes" in v for v in converted.values())
    result["status"] = "measured" if measured_any else "convert_failed"
    if not measured_any:
        result["finding"] = "all resolutions failed to convert"
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
# Planned-grid helper (shared by --plan-only and the report)
# ---------------------------------------------------------------------------

def planned_grids() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for grid_meters, label in RESOLUTIONS:
        _tr, h, w = compute_transform_and_shape(GLOBAL_BBOX_3857, grid_meters)
        out.append({"grid_meters": grid_meters, "label": label, "dims_wxh": [w, h]})
    return out


# ---------------------------------------------------------------------------
# Plan-only
# ---------------------------------------------------------------------------

def print_plan(selected: list[str]) -> None:
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 70)
    lines.append("  SST SIZING — PLAN ONLY (offline; no fetch, no conversion)")
    lines.append("=" * 70)
    lines.append("  Global bbox (EPSG:3857): ±20037508.342789244")
    lines.append("  Planned grids:")
    for g in planned_grids():
        lines.append(f"      {g['label']:<8} {g['grid_meters']:>6} m  ->  {g['dims_wxh'][0]} x {g['dims_wxh'][1]} px (WxH)")
    lines.append("  " + "-" * 66)
    lines.append("  Download candidates:")
    for cid in selected:
        spec = CANDIDATES[cid]
        lines.append(f"    [{cid}] {spec['name']}")
        lines.append(f"        subdataset var : {spec['subdataset_var']}  (kelvin={spec['kelvin']})")
        lines.append(f"        walk-back      : up to {spec['walkback_days']} days from today-2")
        lines.append(f"        note           : {spec['url_note']}")
        sample = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for label, url in spec["urls_for_date"](sample):
            lines.append(f"        url[{label}] : {url}")
    lines.append("  " + "-" * 66)
    lines.append("  MUR (probe only, always runs):")
    lines.append(f"      {MUR_PROBE_URL}")
    lines.append("=" * 70)
    print("\n".join(lines), file=sys.stdout, flush=True)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _fmt_mib(num_bytes: int) -> str:
    return f"{num_bytes / MIB:.2f} MiB"


def print_summary(report: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 70)
    lines.append("  SST SIZING SPIKE — SUMMARY")
    lines.append("=" * 70)
    lines.append("  Global grids:")
    for g in report["planned_grids"]:
        lines.append(f"      {g['label']:<8} {g['grid_meters']:>6} m  {g['dims_wxh'][0]} x {g['dims_wxh'][1]} px")
    for cand in report["candidates"]:
        lines.append("  " + "-" * 66)
        lines.append(f"  [{cand['id']}] {cand['name']}")
        lines.append(f"      status : {cand['status']}")
        if cand.get("finding"):
            lines.append(f"      FINDING: {cand['finding']}")
        if cand.get("url"):
            lines.append(f"      day    : {cand.get('date')}")
            lines.append(f"      url    : {cand['url']}")
        if cand["status"] == "measured":
            lines.append(
                f"      native : {cand['native_dims_wxh'][0]} x {cand['native_dims_wxh'][1]}  "
                f"valid {cand['native_valid_fraction'] * 100:.1f}%  "
                f"SST {cand['value_min_c']:.2f}..{cand['value_max_c']:.2f} °C"
            )
            lines.append(f"      raw .nc: {_fmt_mib(cand['raw_nc_bytes'])} ({cand['raw_nc_bytes']} B)")
            for label, conv in cand["converted"].items():
                if "bin_bytes" in conv:
                    lines.append(
                        f"      conv {label:<7}: bin {_fmt_mib(conv['bin_bytes'])}  "
                        f"meta {conv['meta_bytes']} B  "
                        f"sidecar {_fmt_mib(conv['sidecar_bytes'])}  "
                        f"total {_fmt_mib(conv['total_bytes'])}  "
                        f"({conv['grid_dims_wxh'][0]}x{conv['grid_dims_wxh'][1]})"
                    )
                else:
                    lines.append(f"      conv {label:<7}: ERROR {conv.get('error')}")
    mur = report["mur_probe"]
    lines.append("  " + "-" * 66)
    lines.append(f"  [mur] {mur['name']}")
    lines.append(f"      HEAD status : {mur['status']}  redirect: {mur.get('redirect_location')}")
    lines.append(f"      finding     : {mur['interpretation']}")
    lines.append("  " + "-" * 66)
    lines.append("  FINDINGS:")
    for note in report["findings"]:
        lines.append(f"      - {note}")
    lines.append("=" * 70)
    lines.append(f"  Report JSON: {report['report_path']}")
    lines.append("=" * 70)
    print("\n".join(lines), file=sys.stdout, flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    if args.candidate == "all":
        selected = ["oisst", "geopolar"]
    else:
        selected = [args.candidate]

    if args.plan_only:
        print_plan(selected)
        logger.info("--plan-only: nothing fetched or converted. Exiting.")
        return 0

    inject_sst_packing()

    candidates: list[dict[str, Any]] = []
    for cid in selected:
        logger.info("=" * 60)
        logger.info("Candidate: %s", cid)
        candidates.append(measure_candidate(CANDIDATES[cid], args.date))

    mur = probe_mur()

    # Findings roll-up.
    findings: list[str] = []
    for cand in candidates:
        if cand["status"] == "measured":
            conv = cand["converted"]
            parts = []
            for label, c in conv.items():
                if "bin_bytes" in c:
                    parts.append(f"{label}={c['total_bytes'] / MIB:.1f} MiB")
            findings.append(
                f"{cand['id']}: OK — raw {cand['raw_nc_bytes'] / MIB:.1f} MiB, "
                f"converted " + ", ".join(parts)
            )
        else:
            findings.append(f"{cand['id']}: {cand['status']} — {cand.get('finding', '')}")
    findings.append(
        f"mur: probe HEAD status={mur['status']} — {mur['interpretation']}"
    )

    date_tag = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")
    report_path = REPORTS_DIR / f"sst_sizing_{date_tag}.json"

    report: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "args": {
            "dev_root": args.dev_root,
            "date": args.date,
            "candidate": args.candidate,
            "keep": args.keep,
        },
        "global_bbox_3857": list(GLOBAL_BBOX_3857),
        "sst_packing": SST_PACKING,
        "planned_grids": planned_grids(),
        "candidates": candidates,
        "mur_probe": mur,
        "findings": findings,
        "report_path": str(report_path),
    }

    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    logger.info("Wrote report: %s", report_path)

    print_summary(report)

    # Cleanup AFTER measuring + report (reports persist regardless).
    if not args.keep:
        for cand in candidates:
            if cand.get("date"):
                dest = DOWNLOADS_DIR / f"sst_{cand['id']}_{cand['date']}.nc"
                _safe_unlink(dest)
                _safe_rmtree(STAGING_ROOT / f"sst_{cand['id']}", "converted staging")

    measured = [c for c in candidates if c["status"] == "measured"]
    oisst_measured = any(c["id"] == "oisst" and c["status"] == "measured" for c in candidates)

    if "oisst" in selected and not oisst_measured:
        logger.error("OISST did not produce measurements; treating as failure.")
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
