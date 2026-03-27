from __future__ import annotations

import gzip
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import numpy as np

logger = logging.getLogger(__name__)

MRMS_LISTING_URL = "https://mrms.ncep.noaa.gov/2D/MergedBaseReflectivityQC/"
MRMS_FILE_RE = re.compile(
    r"MRMS_MergedBaseReflectivityQC_00\.50_(?P<stamp>\d{8}-\d{6})\.grib2(?:\.gz)?$",
    re.IGNORECASE,
)


class MRMSFetchError(RuntimeError):
    pass


class MRMSDecodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class MRMSScanRef:
    valid_time: datetime
    url: str
    filename: str
    size_bytes: int | None = None


@dataclass(frozen=True)
class MRMSDecodedScan:
    valid_time: datetime
    values: np.ndarray
    decoder: str
    metadata: dict[str, Any] = field(default_factory=dict)


def discover_recent_scans_from_listing_html(
    listing_html: str,
    *,
    base_url: str = MRMS_LISTING_URL,
    limit: int | None = None,
) -> list[MRMSScanRef]:
    scans: dict[datetime, MRMSScanRef] = {}
    href_re = re.compile(r'href=["\'](?P<href>[^"\']+)["\']', re.IGNORECASE)
    for match in href_re.finditer(str(listing_html)):
        href = match.group("href").strip()
        filename = Path(href).name
        parsed = _scan_ref_from_filename(filename, base_url=base_url)
        if parsed is None:
            continue
        scans[parsed.valid_time] = parsed

    ordered = sorted(scans.values(), key=lambda item: item.valid_time, reverse=True)
    if limit is not None:
        return ordered[: max(0, int(limit))]
    return ordered


def fetch_listing_html(*, listing_url: str = MRMS_LISTING_URL, timeout_seconds: float = 15.0) -> str:
    request = Request(
        listing_url,
        headers={
            "User-Agent": "CartoSky-MRMS/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=float(timeout_seconds)) as response:
        return response.read().decode("utf-8", errors="replace")


def discover_recent_scans_http(
    *,
    listing_url: str = MRMS_LISTING_URL,
    limit: int | None = None,
    timeout_seconds: float = 15.0,
) -> list[MRMSScanRef]:
    return discover_recent_scans_from_listing_html(
        fetch_listing_html(listing_url=listing_url, timeout_seconds=timeout_seconds),
        base_url=listing_url,
        limit=limit,
    )


def freeze_bundle_scans(
    scans: list[MRMSScanRef],
    *,
    max_frames: int,
    newest_valid_time: datetime | None = None,
) -> list[MRMSScanRef]:
    if max_frames < 1:
        raise ValueError("max_frames must be >= 1")

    newest_utc = newest_valid_time.astimezone(timezone.utc) if newest_valid_time is not None else None
    deduped: dict[datetime, MRMSScanRef] = {}
    for scan in scans:
        valid_time = scan.valid_time.astimezone(timezone.utc)
        if newest_utc is not None and valid_time > newest_utc:
            continue
        deduped[valid_time] = scan

    ordered = sorted(deduped.values(), key=lambda item: item.valid_time)
    if len(ordered) > max_frames:
        ordered = ordered[-max_frames:]
    return ordered


def download_scan(scan: MRMSScanRef, *, dest_dir: Path, timeout_seconds: float = 30.0) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    output_path = dest_dir / scan.filename
    request = Request(
        scan.url,
        headers={
            "User-Agent": "CartoSky-MRMS/1.0",
            "Accept": "application/octet-stream,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=float(timeout_seconds)) as response, open(output_path, "wb") as f:
        shutil.copyfileobj(response, f)
    return output_path


def decode_scan(
    scan_path: Path,
    *,
    valid_time: datetime | None = None,
    preferred_decoder: str = "wgrib2",
    fallback_decoder: str = "pygrib",
) -> MRMSDecodedScan:
    resolved_valid_time = valid_time or _valid_time_from_filename(scan_path.name)
    if resolved_valid_time is None:
        raise MRMSDecodeError(f"Unable to derive MRMS valid time from filename: {scan_path.name}")

    prepared_path, cleanup_path = _prepare_scan_path(scan_path)
    try:
        decoder_order = [preferred_decoder, fallback_decoder]
        seen: set[str] = set()
        last_error: Exception | None = None
        for decoder_name in decoder_order:
            normalized = str(decoder_name or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            try:
                if normalized == "wgrib2":
                    return _decode_with_wgrib2(prepared_path, valid_time=resolved_valid_time)
                if normalized == "pygrib":
                    return _decode_with_pygrib(prepared_path, valid_time=resolved_valid_time)
                raise MRMSDecodeError(f"Unsupported MRMS decoder: {decoder_name!r}")
            except Exception as exc:
                last_error = exc
                logger.warning("MRMS decode attempt failed via %s for %s: %s", normalized, scan_path, exc)
                continue
        raise MRMSDecodeError(f"Unable to decode MRMS scan {scan_path}") from last_error
    finally:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)


def _scan_ref_from_filename(filename: str, *, base_url: str) -> MRMSScanRef | None:
    valid_time = _valid_time_from_filename(filename)
    if valid_time is None:
        return None
    return MRMSScanRef(
        valid_time=valid_time,
        url=urljoin(base_url, filename),
        filename=filename,
    )


def _valid_time_from_filename(filename: str) -> datetime | None:
    match = MRMS_FILE_RE.match(Path(filename).name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group("stamp"), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _prepare_scan_path(scan_path: Path) -> tuple[Path, Path | None]:
    if scan_path.suffix.lower() != ".gz":
        return scan_path, None
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    with gzip.open(scan_path, "rb") as src, open(tmp_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return tmp_path, tmp_path


def _decode_with_wgrib2(scan_path: Path, *, valid_time: datetime) -> MRMSDecodedScan:
    wgrib2_path = shutil.which("wgrib2")
    if not wgrib2_path:
        raise MRMSDecodeError("wgrib2 is not installed")

    try:
        import xarray as xr
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        raise MRMSDecodeError("xarray is required for the wgrib2 decoder path") from exc

    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp:
        netcdf_path = Path(tmp.name)
    try:
        subprocess.run(
            [wgrib2_path, str(scan_path), "-netcdf", str(netcdf_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        with xr.open_dataset(netcdf_path) as ds:  # pragma: no cover - requires optional deps
            data_vars = list(ds.data_vars)
            if not data_vars:
                raise MRMSDecodeError(f"No data variables found in decoded MRMS NetCDF: {scan_path}")
            values = np.asarray(ds[data_vars[0]].squeeze().values, dtype=np.float32)
        return MRMSDecodedScan(valid_time=valid_time, values=values, decoder="wgrib2")
    finally:
        netcdf_path.unlink(missing_ok=True)


def _decode_with_pygrib(scan_path: Path, *, valid_time: datetime) -> MRMSDecodedScan:
    try:
        import pygrib
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        raise MRMSDecodeError("pygrib is not installed") from exc

    with pygrib.open(str(scan_path)) as grib:  # pragma: no cover - requires optional deps
        messages = list(grib)
        if not messages:
            raise MRMSDecodeError(f"No GRIB messages found in {scan_path}")
        values = np.asarray(messages[0].values, dtype=np.float32)
    return MRMSDecodedScan(valid_time=valid_time, values=values, decoder="pygrib")
