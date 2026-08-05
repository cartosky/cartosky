"""Poll / list / fetch API for the Open-Meteo relay bucket.

Deliberately **free of scheduler coupling** (that is WP3): nothing here holds
locks, writes artifacts, or knows about runs on disk. It answers three
questions — what objects exist for a run, what does the upstream manifest
*hint*, and what are the field values in one object — and exposes the pure
cadence helpers WP2 needs.

Liveness rule (design §4): **object existence is truth.** ``in-progress.json``
/ ``meta.json`` / ``latest.json`` lag reality and are hints only
(``docs/ECMWF_OPENMETEO_9KM_PROTOTYPE_2026-08-04.md`` §4 gotcha 4). The two
manifest readers below say so in their own docstrings; never gate a build on
them.

Bucket layout (uniform across ECMWF/ICON/GEM/UKMO/… — this is what makes the
source model-agnostic)::

    data_spatial/{bucket_dir}/in-progress.json      # newest run, hint only
    data_spatial/{bucket_dir}/latest.json           # newest complete, hint only
    data_spatial/{bucket_dir}/{Y}/{M}/{D}/{HHMM}Z/meta.json
    data_spatial/{bucket_dir}/{Y}/{M}/{D}/{HHMM}Z/{YYYY-MM-DD}T{HHMM}.om
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterable, Literal, NamedTuple

import numpy as np
import requests

from . import grids
from .catalog import (
    BUCKET_BASE_URL,
    AggregationWindow,
    GridKind,
    OmModelCatalog,
    aggregation_boundaries,
    expected_fhs,
    expected_file_count,
)
from .reader import BlockCachedHTTPFS, open_om

logger = logging.getLogger(__name__)

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

#: Which slice of the source grid to read for a frame.
#:
#: ``global`` is the normal call (design Decision 1): one full-globe pass per
#: file feeds *both* the NA 9 km and the global 0.25° targets. ``na_band``
#: exists for NA-only operation and for bandwidth experiments — it reads only
#: the O1280 rows inside 4.5–82.5°N (~44% of the bytes).
FetchWindow = Literal["global", "na_band"]

__all__ = [
    "AggregationWindow",
    "FetchWindow",
    "RunObject",
    "aggregation_boundaries",
    "expected_fhs",
    "expected_file_count",
    "fetch_frame_fields",
    "list_run_objects",
    "object_url",
    "read_in_progress",
    "read_run_meta",
    "run_prefix",
]


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class RunObject(NamedTuple):
    """One published timestep object.

    Unpacks as ``(valid_dt, fh, url, last_modified, etag)``.

    ``etag`` is the listing's ``<ETag>`` with its quoting stripped, empty when
    the listing omits it. WP3 records it (with :attr:`key`) as per-frame
    provenance so a published frame can be traced back to the exact upstream
    object it was decoded from (design §8).
    """

    valid_dt: datetime
    fh: int
    url: str
    last_modified: datetime
    etag: str = ""

    @property
    def key(self) -> str:
        return self.url.split("/", 3)[-1] if "://" in self.url else self.url


def run_prefix(catalog: OmModelCatalog, run_dt: datetime) -> str:
    """S3 key prefix for one run directory (trailing slash included)."""
    run = _as_utc(run_dt)
    return f"data_spatial/{catalog.bucket_dir}/{run:%Y/%m/%d}/{run:%H%M}Z/"


def object_url(key: str, *, base_url: str = BUCKET_BASE_URL) -> str:
    return f"{base_url.rstrip('/')}/{key.lstrip('/')}"


def list_run_objects(
    catalog: OmModelCatalog,
    run_dt: datetime,
    *,
    base_url: str = BUCKET_BASE_URL,
    session: requests.Session | None = None,
    timeout: float = 60.0,
) -> list[RunObject]:
    """List the ``.om`` timestep objects present for a run, fh-ordered.

    This is the **liveness** call: an object in this list exists and can be
    fetched, regardless of what any manifest says. Paginated; non-``.om`` keys
    (``meta.json``) are dropped.
    """
    prefix = run_prefix(catalog, run_dt)
    run = _as_utc(run_dt)
    http = session if session is not None else requests
    objects: list[RunObject] = []
    token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        response = http.get(base_url, params=params, timeout=timeout)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for entry in root.findall(f"{_S3_NS}Contents"):
            key = entry.findtext(f"{_S3_NS}Key") or ""
            if not key.endswith(".om"):
                continue
            valid_dt = _valid_time_from_key(key)
            if valid_dt is None:
                logger.warning("openmeteo: unparseable object key %s", key)
                continue
            last_modified = datetime.fromisoformat(
                (entry.findtext(f"{_S3_NS}LastModified") or "").replace("Z", "+00:00")
            )
            fh = int((valid_dt - run).total_seconds() // 3600)
            objects.append(
                RunObject(
                    valid_dt=valid_dt,
                    fh=fh,
                    url=object_url(key, base_url=base_url),
                    last_modified=last_modified,
                    etag=(entry.findtext(f"{_S3_NS}ETag") or "").strip().strip('"'),
                )
            )
        if root.findtext(f"{_S3_NS}IsTruncated") != "true":
            break
        token = root.findtext(f"{_S3_NS}NextContinuationToken")
        if not token:
            break
    objects.sort(key=lambda item: item.fh)
    return objects


def _valid_time_from_key(key: str) -> datetime | None:
    stem = key.rsplit("/", 1)[-1]
    if not stem.endswith(".om"):
        return None
    try:
        return datetime.strptime(stem[: -len(".om")], "%Y-%m-%dT%H%M").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(
        tzinfo=timezone.utc
    )


# ---------------------------------------------------------------------------
# Manifest hints
# ---------------------------------------------------------------------------


class ManifestHint(NamedTuple):
    """Parsed upstream manifest. **Hints only — never used for liveness.**"""

    reference_time: datetime | None
    completed: bool
    last_modified_time: datetime | None
    valid_times: tuple[datetime, ...]
    variables: tuple[str, ...]
    crs_wkt: str


def read_in_progress(
    catalog: OmModelCatalog,
    *,
    base_url: str = BUCKET_BASE_URL,
    session: requests.Session | None = None,
    timeout: float = 30.0,
) -> ManifestHint | None:
    """Read ``data_spatial/{dir}/in-progress.json``.

    **Hint only.** Useful to cheaply learn *which* run is currently
    disseminating so the poller can tighten its interval — the object list
    (:func:`list_run_objects`) remains the sole authority on what exists. The
    upstream ``completed`` flag in particular lags reality (prototype §4
    gotcha 4). Returns ``None`` on 404.
    """
    return _read_manifest(
        f"data_spatial/{catalog.bucket_dir}/in-progress.json",
        base_url=base_url,
        session=session,
        timeout=timeout,
    )


def read_run_meta(
    catalog: OmModelCatalog,
    run_dt: datetime,
    *,
    base_url: str = BUCKET_BASE_URL,
    session: requests.Session | None = None,
    timeout: float = 30.0,
) -> ManifestHint | None:
    """Read a run's ``meta.json``.

    **Hint only**, same caveat as :func:`read_in_progress`. Its ``valid_times``
    are useful for *bookkeeping* reconciliation against
    :func:`expected_file_count` after a cycle finishes (design §4 "Complete"),
    never as a build gate. Returns ``None`` on 404.
    """
    return _read_manifest(
        f"{run_prefix(catalog, run_dt)}meta.json",
        base_url=base_url,
        session=session,
        timeout=timeout,
    )


def _read_manifest(
    key: str,
    *,
    base_url: str,
    session: requests.Session | None,
    timeout: float,
) -> ManifestHint | None:
    http = session if session is not None else requests
    response = http.get(object_url(key, base_url=base_url), timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    valid_times = []
    for text in payload.get("valid_times") or ():
        parsed = _parse_iso(text)
        if parsed is not None:
            valid_times.append(parsed)
    return ManifestHint(
        reference_time=_parse_iso(payload.get("reference_time")),
        completed=bool(payload.get("completed")),
        last_modified_time=_parse_iso(payload.get("last_modified_time")),
        valid_times=tuple(valid_times),
        variables=tuple(payload.get("variables") or ()),
        crs_wkt=str(payload.get("crs_wkt") or ""),
    )


def _parse_iso(text: object) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Frame fetch
# ---------------------------------------------------------------------------


def fetch_frame_fields(
    catalog: OmModelCatalog,
    url: str,
    var_names: Iterable[str],
    window: FetchWindow = "global",
    *,
    block_bytes: int = 512 * 1024,
    session: requests.Session | None = None,
) -> dict[str, np.ndarray]:
    """Read the requested fields from one ``.om`` object in a single pass.

    One :class:`~.reader.BlockCachedHTTPFS` is created per call — per-file
    instances by design, so the block cache is scoped to this frame's working
    set and released with it.

    ``window='global'`` (the default and normal call, design Decision 1)
    returns full fields: 1D ``(N,)`` for :attr:`GridKind.O1280`, 2D
    ``(nlat, nlon)`` for :attr:`GridKind.REGULAR_025`. ``window='na_band'``
    returns the O1280 rows inside 4.5–82.5°N only — the returned arrays start
    at global index ``grids.na_band_index_range()[0]``, which is the ``offset``
    to hand :func:`grids.apply_sampler`.

    Variables absent from the object are **omitted from the result**, not
    errors: FH000 files legitimately carry no accumulation or gust fields
    (prototype §4 gotcha 4). Callers decide whether a missing field matters.
    """
    requested = list(var_names)
    for name in requested:
        variable = catalog.variable(name)  # raises on an uncatalogued name
        if not variable.enabled:
            raise ValueError(
                f"{catalog.model_id}: .om variable {name!r} is disabled in the catalog "
                f"({variable.note or 'no CartoSky counterpart'})"
            )

    if window not in ("global", "na_band"):
        raise ValueError(f"unknown fetch window {window!r}")
    if window == "na_band" and catalog.grid is not GridKind.O1280:
        raise ValueError(
            f"{catalog.model_id}: window='na_band' is only defined for O1280 grids, "
            f"not {catalog.grid.value}"
        )
    if not requested:
        return {}

    fields: dict[str, np.ndarray] = {}
    fs = BlockCachedHTTPFS(block_bytes=block_bytes, session=session)
    try:
        om = open_om(url, fs=fs)
    except Exception:
        fs.close()
        raise
    with om:
        available = set(om.list_variables())
        for name in requested:
            if name not in available:
                logger.debug("openmeteo: %s has no variable %s (skipping)", url, name)
                continue
            if catalog.grid is GridKind.O1280:
                if window == "na_band":
                    start, stop = grids.na_band_index_range()
                    fields[name] = om.read_band_1d(
                        name, start, stop, expected_points=catalog.source_points
                    )
                else:
                    fields[name] = om.read_global_1d(
                        name, expected_points=catalog.source_points
                    )
            else:
                source = grids.REGULAR_025_SOURCE
                fields[name] = om.read_grid_2d(name, source.nlat, source.nlon)
        logger.info(
            "openmeteo: read %d/%d fields from %s (%.1f MB, %d requests)",
            len(fields),
            len(requested),
            url,
            om.bytes_transferred / 1e6,
            om.requests_made,
        )
    return fields
