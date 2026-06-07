from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

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


@dataclass(frozen=True)
class _NDFDSourceFieldMeta:
    valid_time: datetime
    issue_time: datetime
    transform: Any
    crs: Any
    source_url: str
    source_filename: str
    source_units: str


@dataclass(frozen=True)
class _NDFDDownloadedSource:
    variable: str
    period: str
    source_url: str
    source_filename: str
    path: Path
    fields: tuple[_NDFDSourceFieldMeta, ...]


class NDFDFieldStream:
    def __init__(
        self,
        *,
        issue_time: datetime,
        variable_ids: tuple[str, ...],
        sources: tuple[_NDFDDownloadedSource, ...],
        temp_dir: tempfile.TemporaryDirectory[str],
    ) -> None:
        self.issue_time = issue_time
        self.variable_ids = variable_ids
        self._sources = sources
        self._temp_dir = temp_dir
        self._closed = False

    def __enter__(self) -> "NDFDFieldStream":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._temp_dir.cleanup()
        self._closed = True

    def iter_variable_frames(self) -> Iterator[tuple[str, list[NDFDSourceField]]]:
        if self._closed:
            raise RuntimeError("NDFD field stream is closed")
        yield from _iter_variable_frames(self._sources)


def collect_latest_ndfd_fields(*, timeout_seconds: float) -> tuple[datetime, dict[str, list[NDFDSourceField]]]:
    with prepare_latest_ndfd_field_stream(timeout_seconds=timeout_seconds) as stream:
        return stream.issue_time, {
            var_id: frames
            for var_id, frames in stream.iter_variable_frames()
            if frames
        }


def prepare_latest_ndfd_field_stream(*, timeout_seconds: float) -> NDFDFieldStream:
    temp_dir = tempfile.TemporaryDirectory(prefix="cartosky-ndfd-cycle-")
    try:
        sources = _download_source_files(
            root=Path(temp_dir.name),
            timeout_seconds=timeout_seconds,
        )
        all_fields = [field for source in sources for field in source.fields]
        issue_time = max(
            [field.issue_time for field in all_fields],
            default=datetime.now(timezone.utc),
        )
        return NDFDFieldStream(
            issue_time=issue_time,
            variable_ids=_available_variable_ids(sources),
            sources=sources,
            temp_dir=temp_dir,
        )
    except Exception:
        temp_dir.cleanup()
        raise


def _download_source_files(*, root: Path, timeout_seconds: float) -> tuple[_NDFDDownloadedSource, ...]:
    requests_to_make = (
        ("mint", VP_001_003),
        ("mint", VP_004_007),
        ("maxt", VP_001_003),
        ("maxt", VP_004_007),
        ("qpf", VP_001_003),
        ("snow", VP_001_003),
        ("iceaccum", VP_001_003),
        ("wgust", VP_001_003),
        ("wgust", VP_004_007),
    )
    sources: list[_NDFDDownloadedSource] = []
    for variable, period in requests_to_make:
        source_url = f"{NDFD_BASE_URL}/{period}/ds.{variable}.bin"
        source_filename = f"ds.{variable}.bin"
        response = requests.get(source_url, timeout=timeout_seconds)
        response.raise_for_status()
        _log_ndfd_memory_checkpoint(
            "after_download",
            source_filename=source_filename,
            downloaded_mib=f"{_bytes_to_mib(len(response.content)):.1f}",
        )
        path = root / f"{period}-{source_filename}"
        path.write_bytes(response.content)
        fields = _read_grib_field_metadata(
            path=path,
            source_url=source_url,
            source_filename=source_filename,
        )
        sources.append(
            _NDFDDownloadedSource(
                variable=variable,
                period=period,
                source_url=source_url,
                source_filename=source_filename,
                path=path,
                fields=tuple(fields),
            )
        )
    return tuple(sources)


def _available_variable_ids(sources: tuple[_NDFDDownloadedSource, ...]) -> tuple[str, ...]:
    metas_by_var = {
        variable: _dedupe_field_metadata(_source_metadata_for_variable(sources, variable))
        for variable in ("mint", "maxt", "qpf", "snow", "iceaccum", "wgust")
    }
    available: list[str] = []
    if metas_by_var["mint"]:
        available.append("mint")
    if metas_by_var["maxt"]:
        available.append("maxt")
    qpf_count = len(metas_by_var["qpf"])
    if qpf_count:
        available.append("qpf_6h")
    if qpf_count >= 4:
        available.append("qpf_24h")
    if qpf_count >= 8:
        available.append("qpf_48h")
    snow_count = len(metas_by_var["snow"])
    if snow_count:
        available.append("snow_6h")
    if snow_count >= 4:
        available.append("snow_24h")
    if snow_count >= 8:
        available.append("snow_48h")
    ice_count = len(metas_by_var["iceaccum"])
    if ice_count:
        available.append("ice_6h")
    if ice_count >= 4:
        available.append("ice_24h")
    gust_6h_count = _derive_window_max_count(metas_by_var["wgust"], window_hours=6, cadence_hours=6)
    if gust_6h_count:
        available.append("wgust_6h_max")
    if gust_6h_count >= 4:
        available.append("wgust_24h_max")
    return tuple(sorted(available))


def _iter_variable_frames(sources: tuple[_NDFDDownloadedSource, ...]) -> Iterator[tuple[str, list[NDFDSourceField]]]:
    temp_min = _load_fields_from_sources(sources, variable="mint")
    if temp_min:
        yield "mint", _limit_periods(_convert_fields(temp_min, _c_to_f), 7)
    del temp_min

    temp_max = _load_fields_from_sources(sources, variable="maxt")
    if temp_max:
        yield "maxt", _limit_periods(_convert_fields(temp_max, _c_to_f), 7)
    del temp_max

    qpf_6h = _convert_fields(_load_fields_from_sources(sources, variable="qpf"), _kgm2_to_inches)
    if qpf_6h:
        yield "qpf_6h", qpf_6h
        qpf_24h = _rolling_sum(qpf_6h, window_size=4)
        if qpf_24h:
            yield "qpf_24h", qpf_24h
        qpf_48h = _rolling_sum(qpf_6h, window_size=8)
        if qpf_48h:
            yield "qpf_48h", qpf_48h
    del qpf_6h

    snow_6h = _convert_fields(_load_fields_from_sources(sources, variable="snow"), _meters_to_inches)
    if snow_6h:
        yield "snow_6h", snow_6h
        snow_24h = _rolling_sum(snow_6h, window_size=4)
        if snow_24h:
            yield "snow_24h", snow_24h
        snow_48h = _rolling_sum(snow_6h, window_size=8)
        if snow_48h:
            yield "snow_48h", snow_48h
    del snow_6h

    ice_6h = _convert_fields(_load_fields_from_sources(sources, variable="iceaccum"), _kgm2_to_inches)
    if ice_6h:
        yield "ice_6h", ice_6h
        ice_24h = _rolling_sum(ice_6h, window_size=4)
        if ice_24h:
            yield "ice_24h", ice_24h
    del ice_6h

    wind_gust_raw = _load_fields_from_sources(sources, variable="wgust")
    gust_converted = _convert_fields(wind_gust_raw, _ms_to_mph)
    del wind_gust_raw
    gust_6h = _derive_window_max(gust_converted, window_hours=6, cadence_hours=6)
    del gust_converted
    if gust_6h:
        yield "wgust_6h_max", gust_6h
        gust_24h = _rolling_max(gust_6h, window_size=4)
        if gust_24h:
            yield "wgust_24h_max", gust_24h


def _source_metadata_for_variable(
    sources: tuple[_NDFDDownloadedSource, ...],
    variable: str,
) -> list[_NDFDSourceFieldMeta]:
    fields: list[_NDFDSourceFieldMeta] = []
    for source in sources:
        if source.variable == variable:
            fields.extend(source.fields)
    return fields


def _dedupe_field_metadata(fields: list[_NDFDSourceFieldMeta]) -> list[_NDFDSourceFieldMeta]:
    fields.sort(key=lambda item: item.valid_time)
    deduped: list[_NDFDSourceFieldMeta] = []
    seen: set[datetime] = set()
    for field in fields:
        valid_time = field.valid_time.astimezone(timezone.utc)
        if valid_time in seen:
            continue
        seen.add(valid_time)
        deduped.append(field)
    return deduped


def _derive_window_max_count(fields: list[_NDFDSourceFieldMeta], *, window_hours: int, cadence_hours: int) -> int:
    cadence = max(1, int(cadence_hours))
    window_delta = timedelta(hours=max(1, int(window_hours)))
    count = 0
    for field in fields:
        valid_time = field.valid_time.astimezone(timezone.utc)
        if valid_time.minute != 0 or valid_time.second != 0:
            continue
        if valid_time.hour % cadence != 0:
            continue
        if any(valid_time - window_delta < candidate.valid_time.astimezone(timezone.utc) <= valid_time for candidate in fields):
            count += 1
    return count


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


def _load_fields_from_sources(
    sources: tuple[_NDFDDownloadedSource, ...],
    *,
    variable: str,
) -> list[NDFDSourceField]:
    fields: list[NDFDSourceField] = []
    for source in sources:
        if source.variable == variable:
            fields.extend(
                _read_grib_fields_from_file(
                    path=source.path,
                    source_url=source.source_url,
                    source_filename=source.source_filename,
                )
            )
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
        fields = _read_grib_fields_from_file(path=Path(tmp.name), source_url=source_url, source_filename=source_filename)
    return fields


def _read_grib_field_metadata(
    *,
    path: Path,
    source_url: str,
    source_filename: str,
) -> list[_NDFDSourceFieldMeta]:
    with rasterio.open(path) as ds:
        if ds.crs is None:
            raise ValueError(f"NDFD field missing CRS: {source_url}")
        fields: list[_NDFDSourceFieldMeta] = []
        for band_index in range(1, ds.count + 1):
            tags = ds.tags(band_index)
            valid_time = _parse_grib_unix_timestamp(tags.get("GRIB_VALID_TIME"))
            issue_time = _parse_grib_unix_timestamp(tags.get("GRIB_REF_TIME"))
            if valid_time is None or issue_time is None:
                continue
            fields.append(
                _NDFDSourceFieldMeta(
                    valid_time=valid_time,
                    issue_time=issue_time,
                    transform=ds.transform,
                    crs=ds.crs,
                    source_url=source_url,
                    source_filename=source_filename,
                    source_units=str(tags.get("GRIB_UNIT") or "").strip(),
                )
            )
    return fields


def _read_grib_fields_from_file(
    *,
    path: Path,
    source_url: str,
    source_filename: str,
) -> list[NDFDSourceField]:
    with rasterio.open(path) as ds:
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
