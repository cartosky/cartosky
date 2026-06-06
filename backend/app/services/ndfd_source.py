from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests
import rasterio

from app.services.process_memory import current_rss_bytes, peak_rss_bytes

NDFD_BASE_URL = "https://tgftp.nws.noaa.gov/SL.us008001/ST.opnl/DF.gr2/DC.ndfd/AR.conus"

logger = logging.getLogger(__name__)

VP_001_003 = "VP.001-003"
VP_004_007 = "VP.004-007"

SECONDS_PER_HOUR = 3600
KGM2_TO_INCHES = np.float32(1.0 / 25.4)
METERS_TO_INCHES = np.float32(39.37007874015748)
MS_TO_MPH = np.float32(2.2369362920544)


@dataclass(frozen=True)
class NDFDSourceField:
    valid_time: datetime
    issue_time: datetime
    values: np.ndarray
    transform: Any
    crs: Any
    source_url: str
    source_filename: str
    source_units: str


def collect_latest_ndfd_fields(*, timeout_seconds: float) -> tuple[datetime, dict[str, list[NDFDSourceField]]]:
    temp_min = _load_fields(variable="mint", periods=(VP_001_003, VP_004_007), timeout_seconds=timeout_seconds)
    temp_max = _load_fields(variable="maxt", periods=(VP_001_003, VP_004_007), timeout_seconds=timeout_seconds)
    qpf_6h = _load_fields(variable="qpf", periods=(VP_001_003,), timeout_seconds=timeout_seconds)
    snow_6h = _load_fields(variable="snow", periods=(VP_001_003,), timeout_seconds=timeout_seconds)
    ice_6h = _load_fields(variable="iceaccum", periods=(VP_001_003,), timeout_seconds=timeout_seconds)
    wind_gust_raw = _load_fields(variable="wgust", periods=(VP_001_003, VP_004_007), timeout_seconds=timeout_seconds)

    issue_time = max(
        [field.issue_time for field in temp_min + temp_max + qpf_6h + snow_6h + ice_6h + wind_gust_raw],
        default=datetime.now(timezone.utc),
    )

    fields_by_var: dict[str, list[NDFDSourceField]] = {
        "mint": _limit_periods(_convert_fields(temp_min, _c_to_f), 7),
        "maxt": _limit_periods(_convert_fields(temp_max, _c_to_f), 7),
        "qpf_6h": _convert_fields(qpf_6h, _kgm2_to_inches),
        "snow_6h": _convert_fields(snow_6h, _meters_to_inches),
        "ice_6h": _convert_fields(ice_6h, _kgm2_to_inches),
    }

    fields_by_var["qpf_24h"] = _rolling_sum(fields_by_var["qpf_6h"], window_size=4)
    fields_by_var["qpf_48h"] = _rolling_sum(fields_by_var["qpf_6h"], window_size=8)
    fields_by_var["snow_24h"] = _rolling_sum(fields_by_var["snow_6h"], window_size=4)
    fields_by_var["snow_48h"] = _rolling_sum(fields_by_var["snow_6h"], window_size=8)
    fields_by_var["ice_24h"] = _rolling_sum(fields_by_var["ice_6h"], window_size=4)

    gust_converted = _convert_fields(wind_gust_raw, _ms_to_mph)
    gust_6h = _derive_window_max(gust_converted, window_hours=6, cadence_hours=6)
    fields_by_var["wgust_6h_max"] = gust_6h
    fields_by_var["wgust_24h_max"] = _rolling_max(gust_6h, window_size=4)

    return issue_time, {var_id: frames for var_id, frames in fields_by_var.items() if frames}


def _load_fields(*, variable: str, periods: tuple[str, ...], timeout_seconds: float) -> list[NDFDSourceField]:
    fields: list[NDFDSourceField] = []
    for period in periods:
        source_url = f"{NDFD_BASE_URL}/{period}/ds.{variable}.bin"
        source_filename = f"ds.{variable}.bin"
        fields.extend(_read_grib_fields(source_url=source_url, source_filename=source_filename, timeout_seconds=timeout_seconds))
    fields.sort(key=lambda item: item.valid_time)
    deduped: list[NDFDSourceField] = []
    seen: set[datetime] = set()
    for field in fields:
        valid_time = field.valid_time.astimezone(timezone.utc)
        if valid_time in seen:
            continue
        seen.add(valid_time)
        deduped.append(field)
    return deduped


def _read_grib_fields(*, source_url: str, source_filename: str, timeout_seconds: float) -> list[NDFDSourceField]:
    response = requests.get(source_url, timeout=timeout_seconds)
    response.raise_for_status()
    _log_ndfd_memory_checkpoint(
        "after_download",
        source_filename=source_filename,
        downloaded_mib=f"{_bytes_to_mib(len(response.content)):.1f}",
    )
    with tempfile.NamedTemporaryFile(prefix="cartosky-ndfd-", suffix=".grb2") as tmp:
        tmp.write(response.content)
        tmp.flush()
        with rasterio.open(tmp.name) as ds:
            if ds.crs is None:
                raise ValueError(f"NDFD field missing CRS: {source_url}")
            fields: list[NDFDSourceField] = []
            for band_index in range(1, ds.count + 1):
                tags = ds.tags(band_index)
                valid_time = _parse_grib_unix_timestamp(tags.get("GRIB_VALID_TIME"))
                issue_time = _parse_grib_unix_timestamp(tags.get("GRIB_REF_TIME"))
                if valid_time is None or issue_time is None:
                    continue
                values = np.asarray(ds.read(band_index, masked=True).filled(np.nan), dtype=np.float32)
                fields.append(
                    NDFDSourceField(
                        valid_time=valid_time,
                        issue_time=issue_time,
                        values=values,
                        transform=ds.transform,
                        crs=ds.crs,
                        source_url=source_url,
                        source_filename=source_filename,
                        source_units=str(tags.get("GRIB_UNIT") or "").strip(),
                    )
                )
    _log_ndfd_memory_checkpoint(
        "after_decode",
        source_filename=source_filename,
        field_count=len(fields),
        field_arrays_mib=f"{_fields_array_mib(fields):.1f}",
    )
    return fields


def _parse_grib_unix_timestamp(raw_value: Any) -> datetime | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _convert_fields(fields: list[NDFDSourceField], converter: Any) -> list[NDFDSourceField]:
    converted: list[NDFDSourceField] = []
    for field in fields:
        converted.append(
            NDFDSourceField(
                valid_time=field.valid_time,
                issue_time=field.issue_time,
                values=converter(field.values),
                transform=field.transform,
                crs=field.crs,
                source_url=field.source_url,
                source_filename=field.source_filename,
                source_units=field.source_units,
            )
        )
    return converted


def _rolling_sum(fields: list[NDFDSourceField], *, window_size: int) -> list[NDFDSourceField]:
    if window_size <= 1:
        return list(fields)
    derived: list[NDFDSourceField] = []
    for end_idx in range(window_size - 1, len(fields)):
        window = fields[end_idx - window_size + 1 : end_idx + 1]
        derived.append(
            NDFDSourceField(
                valid_time=window[-1].valid_time,
                issue_time=max(item.issue_time for item in window),
                values=np.nansum(np.stack([item.values for item in window], axis=0), axis=0).astype(np.float32, copy=False),
                transform=window[-1].transform,
                crs=window[-1].crs,
                source_url=window[-1].source_url,
                source_filename=window[-1].source_filename,
                source_units=window[-1].source_units,
            )
        )
    return derived


def _rolling_max(fields: list[NDFDSourceField], *, window_size: int) -> list[NDFDSourceField]:
    if window_size <= 1:
        return list(fields)
    derived: list[NDFDSourceField] = []
    for end_idx in range(window_size - 1, len(fields)):
        window = fields[end_idx - window_size + 1 : end_idx + 1]
        derived.append(
            NDFDSourceField(
                valid_time=window[-1].valid_time,
                issue_time=max(item.issue_time for item in window),
                values=_nanmax_preserve_nan(np.stack([item.values for item in window], axis=0)),
                transform=window[-1].transform,
                crs=window[-1].crs,
                source_url=window[-1].source_url,
                source_filename=window[-1].source_filename,
                source_units=window[-1].source_units,
            )
        )
    return derived


def _derive_window_max(fields: list[NDFDSourceField], *, window_hours: int, cadence_hours: int) -> list[NDFDSourceField]:
    derived: list[NDFDSourceField] = []
    cadence = max(1, int(cadence_hours))
    window_delta = timedelta(hours=max(1, int(window_hours)))
    for field in fields:
        valid_time = field.valid_time.astimezone(timezone.utc)
        if valid_time.minute != 0 or valid_time.second != 0:
            continue
        if valid_time.hour % cadence != 0:
            continue
        window = [
            candidate
            for candidate in fields
            if valid_time - window_delta < candidate.valid_time.astimezone(timezone.utc) <= valid_time
        ]
        if not window:
            continue
        derived.append(
            NDFDSourceField(
                valid_time=valid_time,
                issue_time=max(item.issue_time for item in window),
                values=_nanmax_preserve_nan(np.stack([item.values for item in window], axis=0)),
                transform=field.transform,
                crs=field.crs,
                source_url=field.source_url,
                source_filename=field.source_filename,
                source_units=field.source_units,
            )
        )
    return derived


def _limit_periods(fields: list[NDFDSourceField], count: int) -> list[NDFDSourceField]:
    return list(fields[: max(0, int(count))])


def _nanmax_preserve_nan(stacked: np.ndarray) -> np.ndarray:
    values = np.asarray(stacked, dtype=np.float32)
    if values.ndim == 0:
        return values.astype(np.float32, copy=False)
    finite_mask = np.isfinite(values)
    safe_values = np.where(finite_mask, values, -np.inf)
    reduced = np.max(safe_values, axis=0)
    any_finite = np.any(finite_mask, axis=0)
    return np.where(any_finite, reduced, np.nan).astype(np.float32, copy=False)


def _c_to_f(values: np.ndarray) -> np.ndarray:
    return (np.asarray(values, dtype=np.float32) * np.float32(9.0 / 5.0) + np.float32(32.0)).astype(np.float32, copy=False)


def _kgm2_to_inches(values: np.ndarray) -> np.ndarray:
    return (np.asarray(values, dtype=np.float32) * KGM2_TO_INCHES).astype(np.float32, copy=False)


def _meters_to_inches(values: np.ndarray) -> np.ndarray:
    return (np.asarray(values, dtype=np.float32) * METERS_TO_INCHES).astype(np.float32, copy=False)


def _ms_to_mph(values: np.ndarray) -> np.ndarray:
    return (np.asarray(values, dtype=np.float32) * MS_TO_MPH).astype(np.float32, copy=False)


def _bytes_to_mib(num_bytes: int) -> float:
    return float(num_bytes) / (1024.0 * 1024.0)


def _fields_array_mib(fields: list[NDFDSourceField]) -> float:
    return _bytes_to_mib(sum(int(field.values.nbytes) for field in fields))


def _log_ndfd_memory_checkpoint(stage: str, **details: Any) -> None:
    detail_tokens = " ".join(
        f"{key}={value}"
        for key, value in sorted(details.items())
    )
    suffix = f" {detail_tokens}" if detail_tokens else ""
    logger.info(
        "NDFD memory checkpoint stage=%s current_rss_mib=%.1f peak_rss_mib=%.1f%s",
        stage,
        _bytes_to_mib(current_rss_bytes()),
        _bytes_to_mib(peak_rss_bytes()),
        suffix,
    )
