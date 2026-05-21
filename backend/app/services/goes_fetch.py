from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GOES_FILE_RE = re.compile(
    r"^(?P<prefix>OR_)?(?P<product>ABI-L2-CMIP(?P<sector>[CFM]))-M(?P<mode>\d+)C(?P<band>\d{2})_"
    r"G(?P<satellite>\d{2})_s(?P<start>\d{4}\d{3}\d{6}\d)_"
    r"e(?P<end>\d{4}\d{3}\d{6}\d)_c(?P<created>\d{4}\d{3}\d{6}\d)\.nc$",
    re.IGNORECASE,
)


class GOESFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class GOESScanRef:
    bucket: str
    key: str
    filename: str
    product: str
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


def parse_goes_filename(filename: str) -> dict[str, Any] | None:
    match = GOES_FILE_RE.match(Path(str(filename)).name)
    if match is None:
        return None
    try:
        band = int(match.group("band"))
    except ValueError:
        return None
    sector = match.group("sector").upper()
    return {
        "product": f"ABI-L2-CMIP{sector}",
        "sector": sector,
        "band": band,
        "satellite": f"goes{int(match.group('satellite'))}",
        "scan_start_time": _parse_abi_stamp(match.group("start")),
        "scan_end_time": _parse_abi_stamp(match.group("end")),
        "created_time": _parse_abi_stamp(match.group("created")),
    }


def object_key_prefix(*, product: str, when: datetime) -> str:
    dt = when.astimezone(timezone.utc)
    return f"{str(product).strip().strip('/')}/{dt:%Y}/{dt:%j}/{dt:%H}/"


def discover_recent_scans_s3(
    *,
    s3_client: Any,
    bucket: str,
    product: str,
    sector: str,
    band: int,
    satellite: str,
    now_utc: datetime | None = None,
    lookback_hours: int = 5,
    object_min_age_seconds: int = 120,
    min_object_bytes: int = 1_000_000,
    limit: int | None = None,
) -> list[GOESScanRef]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    prefixes = [
        object_key_prefix(product=product, when=now - timedelta(hours=hour_offset))
        for hour_offset in range(max(1, int(lookback_hours)))
    ]
    refs_by_key: dict[str, GOESScanRef] = {}
    for prefix in prefixes:
        for obj in _iter_s3_objects(s3_client=s3_client, bucket=bucket, prefix=prefix):
            key = str(obj.get("Key") or "")
            filename = Path(key).name
            parsed = parse_goes_filename(filename)
            if parsed is None:
                continue
            if str(parsed["product"]).upper() != str(product).strip().upper():
                continue
            if str(parsed["sector"]).upper() != str(sector).strip().upper():
                continue
            if int(parsed["band"]) != int(band):
                continue
            if _normalize_satellite(parsed["satellite"]) != _normalize_satellite(satellite):
                continue
            size_bytes = int(obj.get("Size") or 0)
            if size_bytes < int(min_object_bytes):
                logger.debug("Skipping GOES object below size floor: key=%s size=%s", key, size_bytes)
                continue
            last_modified = _coerce_utc_datetime(obj.get("LastModified"))
            if last_modified is None:
                continue
            object_age_seconds = (now - last_modified).total_seconds()
            if object_age_seconds < max(0, int(object_min_age_seconds)):
                logger.debug(
                    "Skipping young GOES object: key=%s age=%.1fs min_age=%ss",
                    key,
                    object_age_seconds,
                    object_min_age_seconds,
                )
                continue
            scan_start = parsed["scan_start_time"].astimezone(timezone.utc)
            refs_by_key[key] = GOESScanRef(
                bucket=str(bucket),
                key=key,
                filename=filename,
                product=str(parsed["product"]),
                sector=str(parsed["sector"]),
                band=int(parsed["band"]),
                satellite=str(parsed["satellite"]),
                scan_start_time=scan_start,
                scan_end_time=parsed["scan_end_time"].astimezone(timezone.utc),
                created_time=parsed["created_time"].astimezone(timezone.utc),
                slot_time=_floor_to_cadence(scan_start, 15),
                size_bytes=size_bytes,
                last_modified=last_modified,
                etag=str(obj.get("ETag") or "").strip('"') or None,
            )

    ordered = sorted(refs_by_key.values(), key=lambda item: item.scan_start_time, reverse=True)
    if limit is not None:
        return ordered[: max(0, int(limit))]
    return ordered


def freeze_bundle_scans(
    scans: list[GOESScanRef],
    *,
    max_frames: int,
    frame_cadence_minutes: int = 15,
) -> list[GOESScanRef]:
    if max_frames < 1:
        raise ValueError("max_frames must be >= 1")
    if frame_cadence_minutes < 1:
        raise ValueError("frame_cadence_minutes must be >= 1")

    selected_by_slot: dict[datetime, GOESScanRef] = {}
    for scan in sorted(scans, key=lambda item: item.created_time, reverse=True):
        slot = _floor_to_cadence(scan.scan_start_time, frame_cadence_minutes)
        current = selected_by_slot.get(slot)
        if current is None or scan.created_time > current.created_time:
            selected_by_slot[slot] = scan

    if not selected_by_slot:
        return []
    anchor = max(selected_by_slot)
    oldest_slot = anchor - timedelta(minutes=(max_frames - 1) * max(1, int(frame_cadence_minutes)))
    selected = [
        scan
        for slot, scan in selected_by_slot.items()
        if oldest_slot <= slot <= anchor
    ]
    selected.sort(key=lambda item: item.slot_time)
    if len(selected) > max_frames:
        selected = selected[-max_frames:]
    return selected


def download_scan(
    scan: GOESScanRef,
    *,
    dest_dir: Path,
    s3_client: Any,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    output_path = dest_dir / scan.filename
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    s3_client.download_file(scan.bucket, scan.key, str(tmp_path))
    actual_size = tmp_path.stat().st_size
    if actual_size != int(scan.size_bytes):
        tmp_path.unlink(missing_ok=True)
        raise GOESFetchError(
            f"Downloaded GOES object size mismatch for {scan.key}: "
            f"actual={actual_size} expected={scan.size_bytes}"
        )
    tmp_path.replace(output_path)
    return output_path


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
