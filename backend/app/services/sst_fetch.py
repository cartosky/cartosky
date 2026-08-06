"""SST upstream fetch — NOAA Geo-Polar Blended 5 km daily L4 (day+night).

Two paths, both unauthenticated single-file netCDF:

* **primary** — CoastWatch ERDDAP griddap subset of ``noaacwBLENDEDsstDNDaily``
  (~100-200 MB per day, ~16-18 s from prod);
* **fallback** — the NCEI GHRSST ``Geo_Polar_Blended`` archive granule.

**ERDDAP is effectively the sole reliable path for recent days.** The NCEI
archive is patchy near the present: HEAD probes on 2026-08-06 found 08-05,
08-04, 08-03 and 07-30..07-28 all ``404`` while 08-02 returned ``200``. Treat the
fallback as coverage for *older* days (and as a units cross-check — it serves the
same field in Kelvin), not as a live substitute for ERDDAP. A day that only the
archive would have served is therefore likely to be skipped, which is why a
per-day miss is never fatal to a poller cycle.

Two upstream gotchas are load-bearing here, both proven on prod 2026-08-06 by
``backend/scripts/measure_sst_sizing.py`` (this module adapts that script's
reference code rather than importing it — the script is a confined spike):

1. **Every request must send a non-default User-Agent.** CoastWatch ERDDAP
   answers ``403`` to ``python-requests/x.y.z``; the same URL returns ``200``
   under a normal UA. Without :data:`HTTP_HEADERS` ERDDAP silently looks down.
2. **Kelvin vs Celsius is per-file, never a static flag.** The ERDDAP DN
   dataset serves ``degree_C`` while the NCEI archive granule for the same day
   serves ``kelvin``, so :func:`detect_kelvin` reads the netCDF ``units``
   attribute per file and only falls back to a magnitude test when the
   attribute is missing or unrecognised.

Values leave this module as float32 **°C** with fill masked to NaN, on an
EPSG:4326 north-up transform. There is no °F path.

The ocean edge is dilated a few source cells inland by
:func:`fill_coastal_fringe` before anything warps it, so the coarser target grids
do not retreat from the coastline. Accepted consequence: hover/sampling just
inshore returns a neighbour-weighted nearest-ocean value instead of "no data".

Availability is probed through the ERDDAP time axis
(``.json?time[last]`` — a few hundred bytes) so the poller never downloads
~104 MB just to learn whether a new day exists. The fallback probe is a HEAD on
the NCEI archive URL.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests

logger = logging.getLogger(__name__)

#: REQUIRED, not cosmetic — see module docstring gotcha 1.
SST_USER_AGENT = "CartoSky-SST/1.0"
HTTP_HEADERS: dict[str, str] = {"User-Agent": SST_USER_AGENT}

ERDDAP_DATASET = "noaacwBLENDEDsstDNDaily"
ERDDAP_VAR = "analysed_sst"
_ERDDAP_GRIDDAP = "https://coastwatch.noaa.gov/erddap/griddap"
#: The dataset's full lat/lon extent at 0.05°; centres, not edges.
_ERDDAP_LAT_LON_SELECTOR = "[(-89.975):(89.975)][(-179.975):(179.975)]"

NCEI_VAR = "analysed_sst"
_NCEI_GEOPOLAR_DN = (
    "https://www.ncei.noaa.gov/data/oceans/ghrsst/L4/GLOB/OSPO/"
    "Geo_Polar_Blended/{Y}/{DDD}/"
    "{ymd}000000-OSPO-L4_GHRSST-SSTfnd-Geo_Polar_Blended-GLOB-v02.0-fv01.0.nc"
)

#: Upstream stamps every daily frame at 12:00Z.
SST_DAILY_VALID_HOUR = 12

#: (connect, read). ERDDAP materialises the subset server-side before
#: streaming, so the read timeout is generous.
DEFAULT_TIMEOUT: tuple[float, float] = (10.0, 600.0)
DEFAULT_PROBE_TIMEOUT: tuple[float, float] = (10.0, 60.0)
DOWNLOAD_ATTEMPTS = 2
#: How far back the HEAD-based fallback probe will look for a newest day.
FALLBACK_PROBE_WALKBACK_DAYS = 5


class SSTFetchError(RuntimeError):
    """No upstream path produced a usable file for the requested day."""


@dataclass(frozen=True)
class SSTSourceFile:
    """A downloaded day of upstream SST."""

    path: Path
    url: str
    label: str
    variable: str
    valid_time: datetime
    bytes_downloaded: int


@dataclass(frozen=True)
class SSTNativeField:
    """One day of SST on its native EPSG:4326 grid, already normalised to °C."""

    values: np.ndarray
    transform: Any
    crs: str = "EPSG:4326"
    source_units: str | None = None
    kelvin_converted: bool = False
    units_decision: str = ""
    valid_fraction: float = 0.0
    rolled_longitude: bool = False
    flipped_northup: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Day / URL helpers
# ---------------------------------------------------------------------------

def normalize_day(value: datetime) -> datetime:
    """The canonical valid time for a day: that UTC date at 12:00:00Z."""
    utc = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return utc.replace(hour=SST_DAILY_VALID_HOUR, minute=0, second=0, microsecond=0)


def erddap_griddap_url(day: datetime) -> str:
    stamp = normalize_day(day).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{_ERDDAP_GRIDDAP}/{ERDDAP_DATASET}.nc?{ERDDAP_VAR}"
        f"[({stamp})]{_ERDDAP_LAT_LON_SELECTOR}"
    )


def erddap_time_probe_url() -> str:
    """Cheap availability probe: the dataset's newest time-axis value.

    Verified live 2026-08-06 with the CartoSky UA — returns a few hundred bytes
    of JSON whose single row is the newest published day, e.g.
    ``2026-08-04T12:00:00Z``.
    """
    return f"{_ERDDAP_GRIDDAP}/{ERDDAP_DATASET}.json?time%5Blast%5D"


def ncei_archive_url(day: datetime) -> str:
    normalized = normalize_day(day)
    return _NCEI_GEOPOLAR_DN.format(
        Y=normalized.strftime("%Y"),
        DDD=normalized.strftime("%j"),
        ymd=normalized.strftime("%Y%m%d"),
    )


def source_paths_for_day(day: datetime) -> list[dict[str, str]]:
    """Ordered fetch paths for one day: ERDDAP primary, NCEI archive fallback."""
    return [
        {"label": f"erddap-{ERDDAP_DATASET}", "url": erddap_griddap_url(day), "variable": ERDDAP_VAR},
        {"label": "ncei-ghrsst-geopolar-dn", "url": ncei_archive_url(day), "variable": NCEI_VAR},
    ]


# ---------------------------------------------------------------------------
# Availability probe
# ---------------------------------------------------------------------------

def _parse_erddap_time_rows(payload: Any) -> datetime | None:
    table = payload.get("table") if isinstance(payload, dict) else None
    rows = table.get("rows") if isinstance(table, dict) else None
    if not isinstance(rows, list):
        return None
    newest: datetime | None = None
    for row in rows:
        if not isinstance(row, list) or not row:
            continue
        raw = row[0]
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        parsed = parsed.astimezone(timezone.utc)
        if newest is None or parsed > newest:
            newest = parsed
    return newest


def probe_latest_available_day(
    *,
    timeout: tuple[float, float] = DEFAULT_PROBE_TIMEOUT,
    now: datetime | None = None,
) -> datetime | None:
    """Newest upstream day, or ``None`` when neither probe answers.

    Cheap by construction: the ERDDAP time-axis query transfers a few hundred
    bytes. When ERDDAP is unreachable this walks the NCEI archive backwards with
    HEAD requests (also cheap — no body) for at most
    :data:`FALLBACK_PROBE_WALKBACK_DAYS` days. Never raises.
    """
    url = erddap_time_probe_url()
    try:
        response = requests.get(url, timeout=timeout, headers=HTTP_HEADERS)
        if response.status_code == 200:
            newest = _parse_erddap_time_rows(json.loads(response.text))
            if newest is not None:
                return normalize_day(newest)
            logger.warning("SST probe: ERDDAP time axis returned no parsable row (%s)", url)
        else:
            logger.warning("SST probe: ERDDAP time axis HTTP %s (%s)", response.status_code, url)
    except (requests.RequestException, json.JSONDecodeError) as exc:
        logger.warning("SST probe: ERDDAP time axis failed (%s): %r", url, exc)

    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for offset in range(1, FALLBACK_PROBE_WALKBACK_DAYS + 1):
        candidate = normalize_day(reference - timedelta(days=offset))
        candidate_url = ncei_archive_url(candidate)
        try:
            response = requests.head(
                candidate_url, timeout=timeout, headers=HTTP_HEADERS, allow_redirects=True
            )
        except requests.RequestException as exc:
            logger.warning("SST probe: NCEI HEAD failed (%s): %r", candidate_url, exc)
            continue
        if response.status_code == 200:
            logger.info("SST probe: newest day via NCEI HEAD fallback = %s", candidate.date())
            return candidate
    return None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _download(url: str, dest: Path, *, timeout: tuple[float, float]) -> tuple[bool, str | None]:
    last_error: str | None = None
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        tmp = dest.with_name(dest.name + ".part")
        try:
            with requests.get(url, stream=True, timeout=timeout, headers=HTTP_HEADERS) as response:
                if response.status_code != 200:
                    last_error = f"HTTP {response.status_code}"
                    logger.warning("SST download attempt %d: %s for %s", attempt, last_error, url)
                    continue
                with tmp.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1 << 20):
                        if chunk:
                            handle.write(chunk)
            tmp.replace(dest)
            return True, None
        except (requests.RequestException, OSError) as exc:
            last_error = repr(exc)
            logger.warning("SST download attempt %d raised for %s: %s", attempt, url, last_error)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        if attempt < DOWNLOAD_ATTEMPTS:
            time.sleep(1.5)
    return False, last_error


def fetch_sst_day(
    day: datetime,
    *,
    download_dir: Path,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
) -> SSTSourceFile:
    """Download one day, trying ERDDAP then the NCEI archive.

    Raises :class:`SSTFetchError` when no path produced a file — the caller
    treats a per-day miss as skippable, never fatal.
    """
    normalized = normalize_day(day)
    ymd = normalized.strftime("%Y%m%d")
    errors: list[str] = []
    for path_spec in source_paths_for_day(normalized):
        dest = Path(download_dir) / f"sst_{ymd}_{path_spec['label']}.nc"
        started_at = time.monotonic()
        ok, error = _download(path_spec["url"], dest, timeout=timeout)
        if not ok:
            errors.append(f"{path_spec['label']}: {error}")
            continue
        size = dest.stat().st_size
        logger.info(
            "SST fetch %s via %s -> %.1f MiB in %.1fs",
            ymd,
            path_spec["label"],
            size / (1024.0 * 1024.0),
            time.monotonic() - started_at,
        )
        return SSTSourceFile(
            path=dest,
            url=path_spec["url"],
            label=path_spec["label"],
            variable=path_spec["variable"],
            valid_time=normalized,
            bytes_downloaded=size,
        )
    raise SSTFetchError(f"No SST upstream path produced a file for {ymd}: {'; '.join(errors)}")


# ---------------------------------------------------------------------------
# netCDF read
# ---------------------------------------------------------------------------

_KELVIN_TOKENS = frozenset({"kelvin", "k", "degrees k", "degree k", "degk"})
_CELSIUS_TOKENS = frozenset({"degree c", "degrees c", "celsius", "degc", "c", "degree celsius"})


def detect_kelvin(units: str | None, values: np.ndarray) -> tuple[bool, str]:
    """Whether an absolute-SST field is Kelvin. Returns ``(is_kelvin, reason)``.

    Per-file by design: ERDDAP's DN dataset serves ``degree_C`` while the NCEI
    archive granule for the same day serves ``kelvin``, so a static flag would
    be wrong for one of them. The magnitude fallback only runs when the units
    attribute is absent or unrecognised.
    """
    token = (units or "").strip().lower().replace("_", " ")
    if token in _KELVIN_TOKENS:
        return True, f"units attribute {units!r}"
    if token in _CELSIUS_TOKENS:
        return False, f"units attribute {units!r}"
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return False, "no finite values; assuming Celsius"
    median = float(np.median(finite))
    return median > 100.0, f"magnitude fallback (median {median:.2f}, units={units!r})"


#: Coastal fringe reach in **source** cells at the native 0.05° grid (~5.5 km per
#: cell), so 3 cells ≈ 16 km. Sized against the target cells the field is warped
#: onto: the na grid is 9 km (~1.6 source cells), so 3 cells covers a full target
#: cell with margin and the coastline can no longer retreat. Deliberately small —
#: this is a coastline cosmetic, not an interpolation product, and every extra
#: cell pushes synthetic ocean values further under land.
SST_COASTAL_FRINGE_SOURCE_CELLS = 3


def fill_coastal_fringe(
    values: np.ndarray, *, radius_cells: int = SST_COASTAL_FRINGE_SOURCE_CELLS
) -> np.ndarray:
    """Dilate valid SST into the NaN fringe next to it by normalized convolution.

    Why this exists: the L4 field is NaN over land, and GDAL's bilinear kernel
    emits nodata whenever it touches NaN. Warping straight to a coarser target
    grid therefore *retreats* coverage from the coast by up to a full target cell
    (9 km na / 0.25° global) and the viewer renders that hard NaN edge as visible
    stair-step blocks. ``mrms_publish._warp_frame_to_target_grid`` solves the
    same problem for radar by warping ``[values, weights]`` together and
    dividing; that recovers erosion *within* the kernel but cannot extend the
    data footprint, which is what a coarsening warp needs. So the same
    normalized-convolution idea is applied here **before** the reproject, on the
    native grid, as an iterative one-cell-at-a-time dilation.

    Each pass replaces NaN cells that have at least one finite 8-neighbour with
    the mean of those neighbours (``sum(valid)/count(valid)`` via a pair of 3x3
    box filters — the shared 1/9 factor cancels), then feeds the result into the
    next pass. Consequences that matter:

    * the fill is strictly a **fringe**: reach is bounded by ``radius_cells``, so
      large interior NaN regions stay NaN — deep inland never acquires a value;
    * filled values are **neighbour-weighted**, not a constant flood, so the
      dilated band carries the local coastal gradient;
    * enclosed water bodies the producer masks out (the Great Lakes) have no
      valid source cell within reach to dilate from, so they stay empty as a
      natural consequence rather than a special case.

    Returns a new array; the input is not modified. A field that is entirely
    finite or entirely NaN is returned unchanged.
    """
    from scipy.ndimage import uniform_filter  # type: ignore[import-untyped]

    reach = max(0, int(radius_cells))
    out = np.array(values, dtype=np.float32, copy=True)
    if reach == 0:
        return out

    for _ in range(reach):
        valid = np.isfinite(out)
        if valid.all() or not valid.any():
            break
        filled = np.where(valid, out, np.float32(0.0))
        weights = valid.astype(np.float32)
        # mode="nearest" replicates the array edge. The lon axis is cyclic, but
        # the antimeridian is open ocean on both sides, so no fill happens there
        # and the difference is unobservable.
        neighbour_sum = uniform_filter(filled, size=3, mode="nearest")
        neighbour_count = uniform_filter(weights, size=3, mode="nearest")
        newly_covered = (~valid) & (neighbour_count > 0)
        if not newly_covered.any():
            break
        out[newly_covered] = (
            neighbour_sum[newly_covered] / neighbour_count[newly_covered]
        )
    return out


def read_native_sst(
    path: Path,
    variable: str = ERDDAP_VAR,
    *,
    fill_fringe: bool = True,
) -> SSTNativeField:
    """Read one SST granule to float32 °C on a north-up EPSG:4326 transform.

    GDAL's netCDF driver applies neither scale/offset nor the fill mask, so this
    applies ``raw * scale + offset``, masks the fill code to NaN, converts from
    Kelvin when :func:`detect_kelvin` says so, flips a south-up array north-up,
    and rolls a 0..360 longitude layout to -180..180.

    Finally :func:`fill_coastal_fringe` dilates the ocean edge by a few source
    cells so the downstream bilinear warp does not retreat from the coastline.
    That happens **once per fetched day**, here rather than in the publish path,
    so both target grids share one fill. ``fill_fringe=False`` returns the raw
    masked field (used by the coastline eyeball comparison).
    """
    import rasterio  # local import: keeps module import cheap for probe-only use
    from rasterio.transform import from_origin

    subdataset = f'NETCDF:"{path}":{variable}'
    with rasterio.open(subdataset) as dataset:
        raw = dataset.read(1).astype("float64")
        scale = dataset.scales[0] if dataset.scales else 1.0
        offset = dataset.offsets[0] if dataset.offsets else 0.0
        nodata = dataset.nodatavals[0] if dataset.nodatavals else None
        transform = dataset.transform
        width, height = dataset.width, dataset.height
        left, right = dataset.bounds.left, dataset.bounds.right
        top, bottom = dataset.bounds.top, dataset.bounds.bottom
        xres = transform.a
        yres = abs(transform.e)
        band_units = dataset.tags(1).get("units")

    values = raw * float(scale) + float(offset)
    if nodata is not None:
        values[raw == nodata] = np.nan

    kelvin, units_reason = detect_kelvin(band_units, values)
    if kelvin:
        values = values - 273.15

    flipped = False
    if transform.e > 0:
        values = values[::-1, :]
        flipped = True
        north = bottom + height * yres
    else:
        north = top

    rolled = False
    if right > 180.5:
        values = np.roll(values, -(width // 2), axis=1)
        west = -180.0
        rolled = True
    else:
        west = left

    values = np.ascontiguousarray(values, dtype=np.float32)
    source_valid_fraction = float(np.isfinite(values).mean())

    fringe_cells = SST_COASTAL_FRINGE_SOURCE_CELLS if fill_fringe else 0
    if fringe_cells:
        values = fill_coastal_fringe(values, radius_cells=fringe_cells)
    valid_fraction = float(np.isfinite(values).mean())

    return SSTNativeField(
        values=values,
        transform=from_origin(west, north, xres, yres),
        source_units=band_units,
        kelvin_converted=kelvin,
        units_decision=units_reason,
        valid_fraction=valid_fraction,
        rolled_longitude=rolled,
        flipped_northup=flipped,
        metadata={
            "native_dims_wxh": [int(width), int(height)],
            "source_units": str(band_units or ""),
            "kelvin_converted": bool(kelvin),
            "units_decision": units_reason,
            # Both fractions are recorded so the coastal dilation is auditable
            # from a published sidecar instead of inferred.
            "source_valid_fraction": round(source_valid_fraction, 6),
            "valid_fraction": round(valid_fraction, 6),
            "coastal_fringe_source_cells": int(fringe_cells),
        },
    )
