from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GOES_L1B_FILE_RE = re.compile(
    r"^(?:OR_)?ABI-L1b-Rad(?P<sector>[CFM])-M(?P<mode>\d+)C(?P<band>\d{2})_"
    r"G(?P<satellite>\d{2})_s(?P<start>\d{14})_"
    r"e(?P<end>\d{14})_c(?P<created>\d{14})\.nc$",
    re.IGNORECASE,
)

CONUS_SOLAR_SAMPLE_POINTS: list[tuple[float, float]] = [
    (48.0, -122.0),  # NW
    (47.0, -100.0),  # N-central
    (45.0, -68.0),  # NE
    (38.0, -120.0),  # W-central
    (38.0, -97.0),  # Center
    (37.0, -77.0),  # E-central
    (29.0, -117.0),  # SW
    (30.0, -95.0),  # S-central
    (25.0, -80.0),  # SE
]

DAYTIME_MIN_SOLAR_ELEVATION_DEG: float = 5.0
DAYTIME_MIN_POINT_FRACTION: float = 0.56


class GOESl1bFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class GOESl1bScanRef:
    bucket: str
    key: str
    filename: str
    sector: str
    band: int
    satellite: str
    scan_start_time: datetime
    scan_end_time: datetime
    created_time: datetime
    slot_time: datetime
    size_bytes: int
    last_modified: datetime
    etag: str | None = None


@dataclass(frozen=True)
class GOESl1bTripletRef:
    slot_time: datetime
    band1: GOESl1bScanRef
    band2: GOESl1bScanRef
    band3: GOESl1bScanRef


def parse_goes_l1b_filename(filename: str) -> dict[str, Any] | None:
    match = GOES_L1B_FILE_RE.match(Path(str(filename)).name)
    if match is None:
        return None
    try:
        band = int(match.group("band"))
    except ValueError:
        return None
    sector = match.group("sector").upper()
    return {
        "product": f"ABI-L1b-Rad{sector}",
        "sector": sector,
        "band": band,
        "satellite": f"goes{int(match.group('satellite'))}",
        "scan_start_time": _parse_abi_stamp(match.group("start")),
        "scan_end_time": _parse_abi_stamp(match.group("end")),
        "created_time": _parse_abi_stamp(match.group("created")),
    }


def discover_recent_l1b_scans_s3(
    *,
    s3_client: Any,
    bucket: str,
    sector: str,
    band: int,
    satellite: str,
    now_utc: datetime | None = None,
    lookback_hours: int = 3,
    object_min_age_seconds: int = 120,
    min_object_bytes: int = 5_000_000,
    slot_cadence_minutes: int = 5,
    limit: int | None = None,
) -> list[GOESl1bScanRef]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    product = f"ABI-L1b-Rad{str(sector).strip().upper()}"
    prefixes = [
        object_key_prefix(product=product, when=now - timedelta(hours=hour_offset))
        for hour_offset in range(max(1, int(lookback_hours)))
    ]
    refs_by_key: dict[str, GOESl1bScanRef] = {}
    for prefix in prefixes:
        for obj in _iter_s3_objects(s3_client=s3_client, bucket=bucket, prefix=prefix):
            key = str(obj.get("Key") or "")
            filename = Path(key).name
            parsed = parse_goes_l1b_filename(filename)
            if parsed is None:
                continue
            if str(parsed["product"]).upper() != product.upper():
                continue
            if str(parsed["sector"]).upper() != str(sector).strip().upper():
                continue
            if int(parsed["band"]) != int(band):
                continue
            if _normalize_satellite(parsed["satellite"]) != _normalize_satellite(satellite):
                continue
            size_bytes = int(obj.get("Size") or 0)
            if size_bytes < int(min_object_bytes):
                logger.debug("Skipping GOES L1b object below size floor: key=%s size=%s", key, size_bytes)
                continue
            last_modified = _coerce_utc_datetime(obj.get("LastModified"))
            if last_modified is None:
                continue
            object_age_seconds = (now - last_modified).total_seconds()
            if object_age_seconds < max(0, int(object_min_age_seconds)):
                logger.debug(
                    "Skipping young GOES L1b object: key=%s age=%.1fs min_age=%ss",
                    key,
                    object_age_seconds,
                    object_min_age_seconds,
                )
                continue
            scan_start = parsed["scan_start_time"].astimezone(timezone.utc)
            refs_by_key[key] = GOESl1bScanRef(
                bucket=str(bucket),
                key=key,
                filename=filename,
                sector=str(parsed["sector"]),
                band=int(parsed["band"]),
                satellite=str(parsed["satellite"]),
                scan_start_time=scan_start,
                scan_end_time=parsed["scan_end_time"].astimezone(timezone.utc),
                created_time=parsed["created_time"].astimezone(timezone.utc),
                slot_time=_floor_to_cadence(scan_start, slot_cadence_minutes),
                size_bytes=size_bytes,
                last_modified=last_modified,
                etag=str(obj.get("ETag") or "").strip('"') or None,
            )

    ordered = sorted(refs_by_key.values(), key=lambda item: item.scan_start_time, reverse=True)
    if limit is not None:
        return ordered[: max(0, int(limit))]
    return ordered


def object_key_prefix(*, product: str, when: datetime) -> str:
    dt = when.astimezone(timezone.utc)
    return f"{str(product).strip().strip('/')}/{dt:%Y}/{dt:%j}/{dt:%H}/"


def freeze_l1b_bundle_scans(
    scans: list[GOESl1bScanRef],
    *,
    max_frames: int,
    frame_cadence_minutes: int = 5,
) -> list[GOESl1bScanRef]:
    if max_frames < 1:
        raise ValueError("max_frames must be >= 1")
    if frame_cadence_minutes < 1:
        raise ValueError("frame_cadence_minutes must be >= 1")

    selected_by_slot: dict[datetime, GOESl1bScanRef] = {}
    for scan in sorted(scans, key=lambda item: item.created_time, reverse=True):
        slot = _floor_to_cadence(scan.scan_start_time, frame_cadence_minutes)
        current = selected_by_slot.get(slot)
        if current is None or scan.created_time > current.created_time:
            selected_by_slot[slot] = scan

    if not selected_by_slot:
        return []
    anchor = max(selected_by_slot)
    oldest_slot = anchor - timedelta(minutes=(max_frames - 1) * max(1, int(frame_cadence_minutes)))
    selected = [scan for slot, scan in selected_by_slot.items() if oldest_slot <= slot <= anchor]
    selected.sort(key=lambda item: item.slot_time)
    if len(selected) > max_frames:
        selected = selected[-max_frames:]
    return selected


def discover_l1b_triplets(
    *,
    s3_client: Any,
    bucket: str,
    sector: str,
    satellite: str,
    now_utc: datetime | None = None,
    lookback_hours: int = 3,
    object_min_age_seconds: int = 120,
    slot_cadence_minutes: int = 5,
    max_frames: int,
) -> list[GOESl1bTripletRef]:
    band1_scans = discover_recent_l1b_scans_s3(
        s3_client=s3_client,
        bucket=bucket,
        sector=sector,
        band=1,
        satellite=satellite,
        now_utc=now_utc,
        lookback_hours=lookback_hours,
        object_min_age_seconds=object_min_age_seconds,
        min_object_bytes=5_000_000,
        slot_cadence_minutes=slot_cadence_minutes,
    )
    band2_scans = discover_recent_l1b_scans_s3(
        s3_client=s3_client,
        bucket=bucket,
        sector=sector,
        band=2,
        satellite=satellite,
        now_utc=now_utc,
        lookback_hours=lookback_hours,
        object_min_age_seconds=object_min_age_seconds,
        min_object_bytes=5_000_000,
        slot_cadence_minutes=slot_cadence_minutes,
    )
    band3_scans = discover_recent_l1b_scans_s3(
        s3_client=s3_client,
        bucket=bucket,
        sector=sector,
        band=3,
        satellite=satellite,
        now_utc=now_utc,
        lookback_hours=lookback_hours,
        object_min_age_seconds=object_min_age_seconds,
        min_object_bytes=5_000_000,
        slot_cadence_minutes=slot_cadence_minutes,
    )

    band1_by_slot = _most_recent_scan_by_slot(band1_scans, slot_cadence_minutes)
    band2_by_slot = _most_recent_scan_by_slot(band2_scans, slot_cadence_minutes)
    band3_by_slot = _most_recent_scan_by_slot(band3_scans, slot_cadence_minutes)
    complete_slots = sorted(
        set(band1_by_slot).intersection(band2_by_slot, band3_by_slot),
        reverse=True,
    )
    if not complete_slots:
        return []

    selected_slots = complete_slots[: max(0, int(max_frames))]
    selected_slots.sort()
    return [
        GOESl1bTripletRef(
            slot_time=slot,
            band1=band1_by_slot[slot],
            band2=band2_by_slot[slot],
            band3=band3_by_slot[slot],
        )
        for slot in selected_slots
    ]


def download_l1b_triplet(
    triplet: GOESl1bTripletRef,
    *,
    dest_dir: Path,
    s3_client: Any,
) -> tuple[Path, Path, Path]:
    try:
        return (
            _download_l1b_scan(triplet.band1, dest_dir=dest_dir, s3_client=s3_client),
            _download_l1b_scan(triplet.band2, dest_dir=dest_dir, s3_client=s3_client),
            _download_l1b_scan(triplet.band3, dest_dir=dest_dir, s3_client=s3_client),
        )
    except GOESl1bFetchError:
        raise
    except Exception as exc:
        raise GOESl1bFetchError(f"Failed to download GOES L1b triplet for {triplet.slot_time.isoformat()}") from exc


def is_conus_daytime(scan_time: datetime) -> bool:
    try:
        from pyorbital.astronomy import sun_earth_distance_correction, sun_zenith_angle
    except ImportError:
        logger.warning("pyorbital is not installed; treating GOES L1b CONUS scan as daytime")
        return True

    utc_time = _coerce_utc_datetime(scan_time)
    if utc_time is None:
        utc_time = scan_time.replace(tzinfo=timezone.utc)
    pyorbital_time = utc_time.replace(tzinfo=None)
    try:
        sun_earth_distance_correction(pyorbital_time)
        daylit_points = 0
        for lat, lon in CONUS_SOLAR_SAMPLE_POINTS:
            solar_elevation = 90.0 - float(sun_zenith_angle(pyorbital_time, lon, lat))
            if solar_elevation >= DAYTIME_MIN_SOLAR_ELEVATION_DEG:
                daylit_points += 1
    except Exception as exc:
        logger.warning("Failed to compute GOES L1b CONUS solar elevation; treating scan as daytime: %s", exc)
        return True

    required_points = max(1, int(len(CONUS_SOLAR_SAMPLE_POINTS) * DAYTIME_MIN_POINT_FRACTION))
    return daylit_points >= required_points


def _download_l1b_scan(
    scan: GOESl1bScanRef,
    *,
    dest_dir: Path,
    s3_client: Any,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    output_path = dest_dir / scan.filename
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        s3_client.download_file(scan.bucket, scan.key, str(tmp_path))
        actual_size = tmp_path.stat().st_size
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise GOESl1bFetchError(f"Failed to download GOES L1b object {scan.key}") from exc
    if actual_size != int(scan.size_bytes):
        tmp_path.unlink(missing_ok=True)
        raise GOESl1bFetchError(
            f"Downloaded GOES L1b object size mismatch for {scan.key}: "
            f"actual={actual_size} expected={scan.size_bytes}"
        )
    tmp_path.replace(output_path)
    return output_path


def _most_recent_scan_by_slot(
    scans: list[GOESl1bScanRef],
    slot_cadence_minutes: int,
) -> dict[datetime, GOESl1bScanRef]:
    selected_by_slot: dict[datetime, GOESl1bScanRef] = {}
    for scan in sorted(scans, key=lambda item: item.created_time, reverse=True):
        slot = _floor_to_cadence(scan.scan_start_time, slot_cadence_minutes)
        current = selected_by_slot.get(slot)
        if current is None or scan.created_time > current.created_time:
            selected_by_slot[slot] = scan
    return selected_by_slot


def _iter_s3_objects(*, s3_client: Any, bucket: str, prefix: str) -> list[dict[str, Any]]:
    paginator = s3_client.get_paginator("list_objects_v2")
    objects: list[dict[str, Any]] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        contents = page.get("Contents")
        if isinstance(contents, list):
            objects.extend(item for item in contents if isinstance(item, dict))
    return objects


def _parse_abi_stamp(value: str) -> datetime:
    year = int(value[0:4])
    day_of_year = int(value[4:7])
    hour = int(value[7:9])
    minute = int(value[9:11])
    second = int(value[11:13])
    tenth = int(value[13:14])
    base = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_of_year - 1)
    return base.replace(hour=hour, minute=minute, second=second, microsecond=tenth * 100_000)


def _floor_to_cadence(value: datetime, cadence_minutes: int) -> datetime:
    dt = value.astimezone(timezone.utc)
    cadence = max(1, int(cadence_minutes))
    minute = (dt.minute // cadence) * cadence
    return dt.replace(minute=minute, second=0, microsecond=0)


def _normalize_satellite(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "")
    if normalized.startswith("noaagoes"):
        normalized = normalized.removeprefix("noaa")
    if normalized.startswith("g") and normalized[1:].isdigit():
        normalized = f"goes{int(normalized[1:])}"
    return normalized


def _coerce_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None
