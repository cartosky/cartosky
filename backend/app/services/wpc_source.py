from __future__ import annotations

import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import numpy as np
import rasterio

WPC_LISTING_URL = "https://ftp.wpc.ncep.noaa.gov/5km_qpf/"
WPC_FILE_RE = re.compile(r"p06m_(?P<run>\d{10})f(?P<fh>\d{3})\.grb", re.IGNORECASE)
KGM2_TO_INCHES = np.float32(1.0 / 25.4)


@dataclass(frozen=True)
class WPCSourceRef:
    filename: str
    source_url: str
    run_time: datetime
    forecast_hour: int


@dataclass(frozen=True)
class WPCSourceField:
    forecast_hour: int
    valid_time: datetime
    issue_time: datetime
    values: np.ndarray
    transform: Any
    crs: Any
    source_url: str
    source_filename: str
    source_units: str


def collect_latest_wpc_fields(
    *,
    timeout_seconds: float,
    listing_url: str = WPC_LISTING_URL,
    max_forecast_hour: int = 168,
    cadence_hours: int = 6,
) -> tuple[datetime, dict[str, list[WPCSourceField]]]:
    refs = discover_wpc_source_refs(listing_url=listing_url, timeout_seconds=timeout_seconds)
    run_time, selected_refs = select_latest_complete_run(
        refs,
        max_forecast_hour=max_forecast_hour,
        cadence_hours=cadence_hours,
    )
    fields = [
        _read_grib_field(ref, timeout_seconds=timeout_seconds)
        for ref in selected_refs
    ]
    fields.sort(key=lambda item: item.forecast_hour)
    issue_time = max((field.issue_time for field in fields), default=run_time)
    return issue_time, {"precip_total": fields}


def discover_wpc_source_refs(*, listing_url: str, timeout_seconds: float) -> list[WPCSourceRef]:
    request = Request(
        listing_url,
        headers={
            "User-Agent": "CartoSky-WPC/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=float(timeout_seconds)) as response:
        listing_html = response.read().decode("utf-8", errors="replace")
    return discover_wpc_source_refs_from_listing(listing_html, listing_url=listing_url)


def discover_wpc_source_refs_from_listing(listing_html: str, *, listing_url: str) -> list[WPCSourceRef]:
    refs_by_key: dict[tuple[datetime, int], WPCSourceRef] = {}
    for match in WPC_FILE_RE.finditer(unescape(listing_html or "")):
        filename = match.group(0)
        run_time = datetime.strptime(match.group("run"), "%Y%m%d%H").replace(tzinfo=timezone.utc)
        forecast_hour = int(match.group("fh"))
        refs_by_key[(run_time, forecast_hour)] = WPCSourceRef(
            filename=filename,
            source_url=urljoin(listing_url, filename),
            run_time=run_time,
            forecast_hour=forecast_hour,
        )
    return sorted(refs_by_key.values(), key=lambda item: (item.run_time, item.forecast_hour))


def select_latest_complete_run(
    refs: list[WPCSourceRef],
    *,
    max_forecast_hour: int,
    cadence_hours: int,
) -> tuple[datetime, list[WPCSourceRef]]:
    cadence = max(1, int(cadence_hours))
    max_fh = max(cadence, int(max_forecast_hour))
    expected_fhs = list(range(cadence, max_fh + cadence, cadence))

    refs_by_run: dict[datetime, dict[int, WPCSourceRef]] = defaultdict(dict)
    for ref in refs:
        if ref.forecast_hour <= 0 or ref.forecast_hour % cadence != 0 or ref.forecast_hour > max_fh:
            continue
        refs_by_run[ref.run_time][ref.forecast_hour] = ref

    for run_time in sorted(refs_by_run.keys(), reverse=True):
        run_refs = refs_by_run[run_time]
        if all(fh in run_refs for fh in expected_fhs):
            return run_time, [run_refs[fh] for fh in expected_fhs]

    raise ValueError(
        f"No complete WPC run available for cadence={cadence}h through forecast hour {max_fh}"
    )


def _read_grib_field(ref: WPCSourceRef, *, timeout_seconds: float) -> WPCSourceField:
    request = Request(
        ref.source_url,
        headers={
            "User-Agent": "CartoSky-WPC/1.0",
            "Accept": "application/octet-stream,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=float(timeout_seconds)) as response:
        payload = response.read()

    with tempfile.NamedTemporaryFile(prefix="cartosky-wpc-", suffix=".grb") as tmp:
        tmp.write(payload)
        tmp.flush()
        with rasterio.open(tmp.name) as ds:
            if ds.crs is None:
                raise ValueError(f"WPC field missing CRS: {ref.source_url}")
            tags = ds.tags(1)
            values = np.asarray(ds.read(1, masked=True).filled(np.nan), dtype=np.float32)
            issue_time = _parse_grib_unix_timestamp(tags.get("GRIB_REF_TIME")) or ref.run_time
            valid_time = _parse_grib_unix_timestamp(tags.get("GRIB_VALID_TIME")) or (
                ref.run_time + timedelta(hours=int(ref.forecast_hour))
            )
            units = str(tags.get("GRIB_UNIT") or "").strip()
            return WPCSourceField(
                forecast_hour=ref.forecast_hour,
                valid_time=valid_time,
                issue_time=issue_time,
                values=_convert_precip_to_inches(values, units_in=units),
                transform=ds.transform,
                crs=ds.crs,
                source_url=ref.source_url,
                source_filename=ref.filename,
                source_units=units,
            )


def _parse_grib_unix_timestamp(raw_value: Any) -> datetime | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _convert_precip_to_inches(values: np.ndarray, *, units_in: str) -> np.ndarray:
    normalized_units = str(units_in).strip().lower()
    output = np.asarray(values, dtype=np.float32)
    if normalized_units in {"[kg/(m^2)]", "kg/(m^2)", "kg m-2", "kg/m^2", "mm", "millimeter", "millimeters"}:
        return output * KGM2_TO_INCHES
    if normalized_units in {"in", "inch", "inches", "[in]"}:
        return output
    if not normalized_units:
        return output
    raise ValueError(f"Unsupported WPC precip units: {units_in}")