from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.config import Config

from app.services.goes_fetch import (
    GOESScanRef,
    discover_recent_scans_s3,
    download_scan,
    freeze_bundle_scans,
)
from app.services.goes_processing import decode_goes_scan
from app.services.goes_publish import (
    GOESBundleFrame,
    GOESPublishResult,
    load_latest_published_goes_frames,
    publish_goes_bundle,
)
from app.services.observed_bundle_health import parse_iso_datetime
from app.services.publish_utils import enforce_run_artifact_retention

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_CACHE_DIR = Path("/opt/cartosky/goes_cache")
DEFAULT_PROVIDER = "noaa"
DEFAULT_SATELLITE = "goes19"
DEFAULT_BUCKET = "noaa-goes19"
DEFAULT_PRODUCT = "ABI-L2-CMIPC"
DEFAULT_SECTOR = "C"
DEFAULT_BANDS = (13,)
DEFAULT_POLL_SECONDS = 300
DEFAULT_KEEP_RUNS = 6
DEFAULT_WINDOW_MINUTES = 180
DEFAULT_FRAME_CADENCE_MINUTES = 15
DEFAULT_LISTING_LOOKBACK_HOURS = 5
DEFAULT_OBJECT_MIN_AGE_SECONDS = 120
DEFAULT_MIN_OBJECT_BYTES = 1_000_000


@dataclass(frozen=True)
class GOESPollerConfig:
    data_root: Path
    cache_dir: Path
    provider: str
    satellite: str
    bucket: str
    product: str
    sector: str
    bands: tuple[int, ...]
    poll_seconds: int
    keep_runs: int
    window_minutes: int
    frame_cadence_minutes: int
    listing_lookback_hours: int
    object_min_age_seconds: int
    min_object_bytes: int


@dataclass(frozen=True)
class GOESPollerCycleResult:
    action: str
    latest_scan_valid_time: str | None
    published_run_id: str | None
    expected_frame_count: int
    decoded_frame_count: int
    failed_scan_count: int
    message: str


def run_once(config: GOESPollerConfig) -> GOESPollerCycleResult:
    target_frame_count = compute_target_frame_count(
        window_minutes=config.window_minutes,
        frame_cadence_minutes=config.frame_cadence_minutes,
    )
    if not config.bands or int(config.bands[0]) != 13:
        raise ValueError("GOES-East v1 supports Band 13 only")

    now = datetime.now(timezone.utc)
    s3_client = boto3.client("s3", config=Config(signature_version=UNSIGNED))
    discovered = discover_recent_scans_s3(
        s3_client=s3_client,
        bucket=config.bucket,
        product=config.product,
        sector=config.sector,
        band=13,
        satellite=config.satellite,
        now_utc=now,
        lookback_hours=config.listing_lookback_hours,
        object_min_age_seconds=config.object_min_age_seconds,
        min_object_bytes=config.min_object_bytes,
        slot_cadence_minutes=config.frame_cadence_minutes,
        limit=max(target_frame_count * 3, target_frame_count),
    )
    frozen = freeze_bundle_scans(
        discovered,
        max_frames=target_frame_count,
        frame_cadence_minutes=config.frame_cadence_minutes,
    )
    logger.info(
        "GOES bundle candidate discovered=%d frozen=%d target=%d min_age=%ss",
        len(discovered),
        len(frozen),
        target_frame_count,
        config.object_min_age_seconds,
    )
    if not frozen:
        return GOESPollerCycleResult(
            action="noop",
            latest_scan_valid_time=None,
            published_run_id=None,
            expected_frame_count=target_frame_count,
            decoded_frame_count=0,
            failed_scan_count=0,
            message="No eligible GOES-East scans discovered from S3.",
        )

    newest_scan_valid_time = frozen[-1].slot_time.astimezone(timezone.utc)
    latest_published_valid_time, latest_bundle_complete = _latest_published_bundle_state(config.data_root)
    latest_run_id, previous_frames = load_latest_published_goes_frames(config.data_root)
    previously_published_valid_times = {
        frame.slot_time.astimezone(timezone.utc)
        for frame in previous_frames
    }
    if (
        latest_published_valid_time is not None
        and newest_scan_valid_time <= latest_published_valid_time
        and latest_bundle_complete
    ):
        return GOESPollerCycleResult(
            action="noop",
            latest_scan_valid_time=_format_iso(newest_scan_valid_time),
            published_run_id=None,
            expected_frame_count=len(frozen),
            decoded_frame_count=0,
            failed_scan_count=0,
            message="No new GOES-East scan beyond the current published latest bundle.",
        )

    frames: list[GOESBundleFrame] = []
    failed_scans: list[str] = []
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cartosky-goes-", dir=config.cache_dir) as tmpdir:
        download_dir = Path(tmpdir)
        scans_to_decode = [
            scan for scan in frozen
            if scan.slot_time.astimezone(timezone.utc) not in previously_published_valid_times
        ]
        logger.info(
            "GOES incremental window previous_run=%s reused=%d decode=%d",
            latest_run_id or "<none>",
            max(0, len(frozen) - len(scans_to_decode)),
            len(scans_to_decode),
        )
        for index, scan in enumerate(scans_to_decode, start=1):
            logger.info(
                "GOES frame %d/%d fetching s3://%s/%s slot=%s size=%d",
                index,
                len(scans_to_decode),
                scan.bucket,
                scan.key,
                _format_iso(scan.slot_time),
                scan.size_bytes,
            )
            try:
                scan_path = download_scan(scan, dest_dir=download_dir, s3_client=s3_client)
                decoded = decode_goes_scan(scan_path)
                frames.append(
                    GOESBundleFrame(
                        valid_time=decoded.valid_time.astimezone(timezone.utc),
                        slot_time=scan.slot_time.astimezone(timezone.utc),
                        values=decoded.values,
                        transform=decoded.transform,
                        projection=decoded.projection,
                        source_bucket=scan.bucket,
                        source_key=scan.key,
                        source_filename=scan.filename,
                        source_size_bytes=scan.size_bytes,
                        source_last_modified=scan.last_modified,
                        source_metadata={
                            **decoded.source_metadata,
                            "abi_midpoint_time": decoded.valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "scan_start_time": scan.scan_start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "scan_end_time": scan.scan_end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "created_time": scan.created_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "slot_time": scan.slot_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "satellite": config.satellite,
                            "product": config.product,
                            "sector": config.sector,
                            "band": scan.band,
                        },
                    )
                )
            except Exception as exc:
                logger.warning("Skipping GOES scan %s after fetch/decode failure: %s", scan.filename, exc)
                failed_scans.append(scan.filename)

    available_slots = {frame.slot_time.astimezone(timezone.utc) for frame in previous_frames}
    available_slots.update(frame.slot_time.astimezone(timezone.utc) for frame in frames)
    frozen_slots = {scan.slot_time.astimezone(timezone.utc) for scan in frozen}
    available_for_window = len(available_slots.intersection(frozen_slots))
    if not frames and available_for_window == 0:
        return GOESPollerCycleResult(
            action="noop",
            latest_scan_valid_time=_format_iso(newest_scan_valid_time),
            published_run_id=None,
            expected_frame_count=len(frozen),
            decoded_frame_count=0,
            failed_scan_count=len(failed_scans),
            message="No publishable GOES-East bundle could be built from the frozen scan window.",
        )

    publish_result = publish_goes_bundle(
        data_root=config.data_root,
        frames=frames,
        publish_time=datetime.now(timezone.utc),
        previous_frames=previous_frames,
        target_frame_count=len(frozen),
        expected_frame_count=len(frozen),
    )
    _enforce_retention(config)
    _cleanup_cache_dir(config.cache_dir)

    message = f"Published GOES-East bundle {publish_result.run_id} with {available_for_window}/{len(frozen)} frames"
    if failed_scans:
        message += f" ({len(failed_scans)} failed scans skipped)"
    return GOESPollerCycleResult(
        action="published",
        latest_scan_valid_time=_format_iso(newest_scan_valid_time),
        published_run_id=publish_result.run_id,
        expected_frame_count=len(frozen),
        decoded_frame_count=available_for_window,
        failed_scan_count=len(failed_scans),
        message=message,
    )


def run_poller(config: GOESPollerConfig, *, once: bool) -> int:
    logger.info(
        "GOES poller starting provider=%s satellite=%s bucket=%s product=%s sector=%s bands=%s data_root=%s poll=%ss keep_runs=%d window=%dm cadence=%dm min_age=%ss",
        config.provider,
        config.satellite,
        config.bucket,
        config.product,
        config.sector,
        ",".join(str(item) for item in config.bands),
        config.data_root,
        config.poll_seconds,
        config.keep_runs,
        config.window_minutes,
        config.frame_cadence_minutes,
        config.object_min_age_seconds,
    )
    while True:
        try:
            result = run_once(config)
            logger.info("GOES poller cycle action=%s message=%s", result.action, result.message)
        except Exception:
            logger.exception("GOES poller cycle failed")
        if once:
            return 0
        time.sleep(max(15, int(config.poll_seconds)))


def compute_target_frame_count(*, window_minutes: int, frame_cadence_minutes: int) -> int:
    safe_window = max(1, int(window_minutes))
    safe_cadence = max(1, int(frame_cadence_minutes))
    return max(1, (safe_window // safe_cadence) + 1)


def _latest_published_bundle_state(data_root: Path) -> tuple[datetime | None, bool]:
    latest_path = data_root / "published" / "goes-east" / "LATEST.json"
    if not latest_path.is_file():
        return None, False
    try:
        latest_payload = json.loads(latest_path.read_text())
    except Exception:
        return None, False
    run_id = str(latest_payload.get("run_id") or "").strip()
    manifest_path = data_root / "manifests" / "goes-east" / f"{run_id}.json"
    if not manifest_path.is_file():
        return None, False
    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception:
        return None, False
    metadata = manifest.get("metadata") if isinstance(manifest, dict) else None
    latest_scan = parse_iso_datetime(metadata.get("latest_scan_valid_time")) if isinstance(metadata, dict) else None
    variables = manifest.get("variables") if isinstance(manifest, dict) else None
    complete = False
    if isinstance(variables, dict):
        complete = all(
            isinstance(entry, dict) and int(entry.get("available_frames") or 0) >= int(entry.get("expected_frames") or 0)
            for entry in variables.values()
        )
    return latest_scan, complete


def _enforce_retention(config: GOESPollerConfig) -> None:
    enforce_run_artifact_retention(config.data_root / "staging" / "goes-east", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "published" / "goes-east", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "manifests" / "goes-east", config.keep_runs)


def _cleanup_cache_dir(cache_dir: Path) -> None:
    if not cache_dir.is_dir():
        return
    for child in cache_dir.iterdir():
        if child.is_dir() and child.name.startswith("cartosky-goes-"):
            shutil.rmtree(child, ignore_errors=True)


def build_config(args: argparse.Namespace) -> GOESPollerConfig:
    return GOESPollerConfig(
        data_root=Path(args.data_root).expanduser().resolve() if args.data_root else Path(_env_value("CARTOSKY_DATA_ROOT", str(DEFAULT_DATA_ROOT))).expanduser().resolve(),
        cache_dir=Path(args.cache_dir).expanduser().resolve() if args.cache_dir else Path(_env_value("CARTOSKY_GOES_CACHE_DIR", str(DEFAULT_CACHE_DIR))).expanduser().resolve(),
        provider=_env_value("CARTOSKY_GOES_PROVIDER", DEFAULT_PROVIDER),
        satellite=_env_value("CARTOSKY_GOES_SATELLITE", DEFAULT_SATELLITE),
        bucket=_env_value("CARTOSKY_GOES_BUCKET", DEFAULT_BUCKET),
        product=_env_value("CARTOSKY_GOES_PRODUCT", DEFAULT_PRODUCT),
        sector=_env_value("CARTOSKY_GOES_SECTOR", DEFAULT_SECTOR),
        bands=_parse_bands(_env_value("CARTOSKY_GOES_BANDS", ",".join(str(item) for item in DEFAULT_BANDS))),
        poll_seconds=_int_env("CARTOSKY_GOES_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=15),
        keep_runs=_int_env("CARTOSKY_GOES_KEEP_RUNS", DEFAULT_KEEP_RUNS, minimum=1),
        window_minutes=_int_env("CARTOSKY_GOES_WINDOW_MINUTES", DEFAULT_WINDOW_MINUTES, minimum=1),
        frame_cadence_minutes=_int_env("CARTOSKY_GOES_FRAME_CADENCE_MINUTES", DEFAULT_FRAME_CADENCE_MINUTES, minimum=1),
        listing_lookback_hours=_int_env("CARTOSKY_GOES_LISTING_LOOKBACK_HOURS", DEFAULT_LISTING_LOOKBACK_HOURS, minimum=1),
        object_min_age_seconds=_int_env("CARTOSKY_GOES_OBJECT_MIN_AGE_SECONDS", DEFAULT_OBJECT_MIN_AGE_SECONDS, minimum=0),
        min_object_bytes=_int_env("CARTOSKY_GOES_MIN_OBJECT_BYTES", DEFAULT_MIN_OBJECT_BYTES, minimum=0),
    )


def _parse_bands(raw: str) -> tuple[int, ...]:
    bands: list[int] = []
    for item in str(raw or "").replace(";", ",").split(","):
        token = item.strip()
        if not token:
            continue
        bands.append(int(token))
    return tuple(bands or DEFAULT_BANDS)


def _env_value(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None else str(raw).strip()


def _int_env(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(minimum, int(str(raw).strip()))
    except ValueError:
        logger.warning("Invalid %s=%r; using fallback=%d", name, raw, default)
        return default


def _format_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Poll and publish GOES-East satellite imagery")
    parser.add_argument("--once", action="store_true", help="Run one poll/publish cycle then exit")
    parser.add_argument("--data-root", default=None, help="Override CARTOSKY_DATA_ROOT")
    parser.add_argument("--cache-dir", default=None, help="Override CARTOSKY_GOES_CACHE_DIR")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run_poller(build_config(args), once=bool(args.once))


if __name__ == "__main__":
    raise SystemExit(main())
