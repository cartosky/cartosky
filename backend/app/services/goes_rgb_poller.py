from __future__ import annotations

import argparse
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

from app.services.goes_l1b_fetch import (
    GOESl1bTripletRef,
    discover_l1b_triplets,
    download_l1b_triplet,
    freeze_l1b_bundle_scans,
    is_conus_daytime,
)
from app.services.goes_l1b_processing import decode_goes_l1b_triplet
from app.services import goes_rgb_publish
from app.services.goes_rgb_publish import (
    GOESRGBBundleFrame,
    GOESRGBPublishedFrame,
    GOESRGBPublishResult,
    load_latest_published_rgb_frames,
    publish_goes_rgb_bundle,
)
from app.services.publish_utils import enforce_run_artifact_retention

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_CACHE_DIR = Path("/opt/cartosky/goes_rgb_cache")
DEFAULT_SATELLITE = "goes19"
DEFAULT_BUCKET = "noaa-goes19"
DEFAULT_SECTOR = "C"
DEFAULT_POLL_SECONDS = 120
DEFAULT_KEEP_RUNS = 6
DEFAULT_WINDOW_MINUTES = 90
DEFAULT_FRAME_CADENCE_MINUTES = 5
DEFAULT_LISTING_LOOKBACK_HOURS = 3
DEFAULT_OBJECT_MIN_AGE_SECONDS = 120
DEFAULT_COMPOSITE_NAME = "cimss_true_color_sunz_rayleigh"
DEFAULT_WEBP_QUALITY = 85


@dataclass(frozen=True)
class GOESRGBPollerConfig:
    data_root: Path
    cache_dir: Path
    satellite: str
    bucket: str
    sector: str
    poll_seconds: int
    keep_runs: int
    window_minutes: int
    frame_cadence_minutes: int
    listing_lookback_hours: int
    object_min_age_seconds: int
    composite_name: str
    webp_quality: int


@dataclass(frozen=True)
class GOESRGBPollerCycleResult:
    action: str
    latest_scan_valid_time: str | None
    published_run_id: str | None
    expected_frame_count: int
    decoded_frame_count: int
    failed_scan_count: int
    message: str


def run_once(config: GOESRGBPollerConfig) -> GOESRGBPollerCycleResult:
    target_frame_count = compute_target_frame_count(
        window_minutes=config.window_minutes,
        frame_cadence_minutes=config.frame_cadence_minutes,
    )
    now = datetime.now(timezone.utc)
    s3_client = boto3.client("s3", config=Config(signature_version=UNSIGNED))

    triplets = discover_l1b_triplets(
        s3_client=s3_client,
        bucket=config.bucket,
        sector=config.sector,
        satellite=config.satellite,
        now_utc=now,
        lookback_hours=config.listing_lookback_hours,
        object_min_age_seconds=config.object_min_age_seconds,
        slot_cadence_minutes=config.frame_cadence_minutes,
        max_frames=target_frame_count * 3,
    )
    daytime_triplets = [triplet for triplet in triplets if is_conus_daytime(triplet.slot_time)]
    logger.info(
        "RGB triplet discovery: total=%d daytime=%d nighttime_skipped=%d",
        len(triplets),
        len(daytime_triplets),
        len(triplets) - len(daytime_triplets),
    )
    if not daytime_triplets:
        return GOESRGBPollerCycleResult(
            action="noop",
            latest_scan_valid_time=None,
            published_run_id=None,
            expected_frame_count=target_frame_count,
            decoded_frame_count=0,
            failed_scan_count=0,
            message="No daytime GOES-East RGB L1b triplets discovered from S3.",
        )

    frozen = _freeze_l1b_triplets(
        daytime_triplets,
        max_frames=target_frame_count,
        frame_cadence_minutes=config.frame_cadence_minutes,
    )
    if not frozen:
        return GOESRGBPollerCycleResult(
            action="noop",
            latest_scan_valid_time=None,
            published_run_id=None,
            expected_frame_count=target_frame_count,
            decoded_frame_count=0,
            failed_scan_count=0,
            message="No eligible GOES-East RGB triplets remained after freezing the bundle window.",
        )

    latest_run_id, previous_frames = load_latest_published_rgb_frames(config.data_root)
    newest_slot = frozen[-1].slot_time.astimezone(timezone.utc)
    previously_published_slots = {
        frame.slot_time.astimezone(timezone.utc)
        for frame in previous_frames
    }
    if newest_slot in previously_published_slots and len(frozen) <= len(previous_frames):
        return GOESRGBPollerCycleResult(
            action="noop",
            latest_scan_valid_time=_format_iso(newest_slot),
            published_run_id=None,
            expected_frame_count=len(frozen),
            decoded_frame_count=0,
            failed_scan_count=0,
            message="No new GOES-East RGB triplet beyond the current published latest bundle.",
        )

    scans_to_decode = [
        triplet
        for triplet in frozen
        if triplet.slot_time.astimezone(timezone.utc) not in previously_published_slots
    ]
    logger.info(
        "RGB incremental window previous_run=%s reused=%d decode=%d",
        latest_run_id or "<none>",
        max(0, len(frozen) - len(scans_to_decode)),
        len(scans_to_decode),
    )

    frames: list[GOESRGBBundleFrame] = []
    failed_scans: list[str] = []
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    for index, triplet in enumerate(scans_to_decode, start=1):
        logger.info(
            "RGB frame %d/%d fetching band1=%s band2=%s band3=%s slot=%s sizes=%d+%d+%d bytes",
            index,
            len(scans_to_decode),
            triplet.band1.filename,
            triplet.band2.filename,
            triplet.band3.filename,
            _format_iso(triplet.slot_time),
            triplet.band1.size_bytes,
            triplet.band2.size_bytes,
            triplet.band3.size_bytes,
        )
        try:
            with tempfile.TemporaryDirectory(prefix="cartosky-goes-rgb-", dir=config.cache_dir) as tmpdir:
                band1_path, band2_path, band3_path = download_l1b_triplet(
                    triplet,
                    dest_dir=Path(tmpdir),
                    s3_client=s3_client,
                )
                decoded = decode_goes_l1b_triplet(
                    band1_path,
                    band2_path,
                    band3_path,
                    slot_time=triplet.slot_time,
                    composite_name=config.composite_name,
                )
                frames.append(
                    GOESRGBBundleFrame(
                        valid_time=decoded.valid_time.astimezone(timezone.utc),
                        slot_time=triplet.slot_time.astimezone(timezone.utc),
                        rgba=decoded.rgba,
                        source_metadata={
                            **decoded.source_metadata,
                            "abi_midpoint_time": decoded.valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "slot_time": triplet.slot_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "satellite": config.satellite,
                            "product": f"ABI-L1b-Rad{config.sector}",
                            "sector": config.sector,
                            "band1_filename": triplet.band1.filename,
                            "band2_filename": triplet.band2.filename,
                            "band3_filename": triplet.band3.filename,
                            "band1_size_bytes": triplet.band1.size_bytes,
                            "band2_size_bytes": triplet.band2.size_bytes,
                            "band3_size_bytes": triplet.band3.size_bytes,
                            "band1_key": triplet.band1.key,
                            "band2_key": triplet.band2.key,
                            "band3_key": triplet.band3.key,
                        },
                    )
                )
        except Exception as exc:
            logger.warning("Skipping GOES RGB triplet slot=%s after fetch/decode failure: %s", _format_iso(triplet.slot_time), exc)
            failed_scans.append(_format_iso(triplet.slot_time) or triplet.band2.filename)

    available_slots = {frame.slot_time.astimezone(timezone.utc) for frame in previous_frames}
    available_slots.update(frame.slot_time.astimezone(timezone.utc) for frame in frames)
    frozen_slots = {triplet.slot_time.astimezone(timezone.utc) for triplet in frozen}
    available_for_window = len(available_slots.intersection(frozen_slots))
    if not frames and available_for_window == 0:
        return GOESRGBPollerCycleResult(
            action="noop",
            latest_scan_valid_time=_format_iso(newest_slot),
            published_run_id=None,
            expected_frame_count=len(frozen),
            decoded_frame_count=0,
            failed_scan_count=len(failed_scans),
            message="No publishable GOES-East RGB bundle could be built from the frozen triplet window.",
        )

    publish_result = _publish_goes_rgb_bundle_with_quality(
        data_root=config.data_root,
        frames=frames,
        publish_time=now,
        previous_frames=previous_frames,
        target_frame_count=len(frozen),
        expected_frame_count=len(frozen),
        webp_quality=config.webp_quality,
    )
    _enforce_retention(config)
    _cleanup_cache_dir(config.cache_dir)

    message = f"Published GOES-East RGB bundle {publish_result.run_id} with {available_for_window}/{len(frozen)} frames"
    if failed_scans:
        message += f" ({len(failed_scans)} failed triplets skipped)"
    return GOESRGBPollerCycleResult(
        action="published",
        latest_scan_valid_time=_format_iso(newest_slot),
        published_run_id=publish_result.run_id,
        expected_frame_count=len(frozen),
        decoded_frame_count=len(frames),
        failed_scan_count=len(failed_scans),
        message=message,
    )


def run_poller(config: GOESRGBPollerConfig, *, once: bool) -> int:
    logger.info(
        "GOES RGB poller starting satellite=%s bucket=%s sector=%s composite=%s data_root=%s poll=%ss keep_runs=%d window=%dm cadence=%dm min_age=%ss",
        config.satellite,
        config.bucket,
        config.sector,
        config.composite_name,
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
            logger.info("GOES RGB poller cycle action=%s message=%s", result.action, result.message)
        except Exception:
            logger.exception("GOES RGB poller cycle failed")
        if once:
            return 0
        time.sleep(max(15, int(config.poll_seconds)))


def compute_target_frame_count(*, window_minutes: int, frame_cadence_minutes: int) -> int:
    safe_window = max(1, int(window_minutes))
    safe_cadence = max(1, int(frame_cadence_minutes))
    return max(1, (safe_window // safe_cadence) + 1)


def _freeze_l1b_triplets(
    triplets: list[GOESl1bTripletRef],
    *,
    max_frames: int,
    frame_cadence_minutes: int,
) -> list[GOESl1bTripletRef]:
    triplets_by_slot = {
        triplet.slot_time.astimezone(timezone.utc): triplet
        for triplet in triplets
    }
    frozen_scans = freeze_l1b_bundle_scans(
        [triplet.band2 for triplet in triplets],
        max_frames=max_frames,
        frame_cadence_minutes=frame_cadence_minutes,
    )
    frozen = [
        triplets_by_slot[scan.slot_time.astimezone(timezone.utc)]
        for scan in frozen_scans
        if scan.slot_time.astimezone(timezone.utc) in triplets_by_slot
    ]
    frozen.sort(key=lambda item: item.slot_time)
    return frozen


def _publish_goes_rgb_bundle_with_quality(
    *,
    data_root: Path,
    frames: list[GOESRGBBundleFrame],
    publish_time: datetime,
    previous_frames: list[GOESRGBPublishedFrame],
    target_frame_count: int,
    expected_frame_count: int,
    webp_quality: int,
) -> GOESRGBPublishResult:
    original_quality = goes_rgb_publish.TRUE_COLOR_WEBP_QUALITY
    goes_rgb_publish.TRUE_COLOR_WEBP_QUALITY = max(1, int(webp_quality))
    try:
        return publish_goes_rgb_bundle(
            data_root=data_root,
            frames=frames,
            publish_time=publish_time,
            previous_frames=previous_frames,
            target_frame_count=target_frame_count,
            expected_frame_count=expected_frame_count,
        )
    finally:
        goes_rgb_publish.TRUE_COLOR_WEBP_QUALITY = original_quality


def _enforce_retention(config: GOESRGBPollerConfig) -> None:
    enforce_run_artifact_retention(config.data_root / "staging" / "goes-east", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "published" / "goes-east", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "manifests" / "goes-east", config.keep_runs)


def _cleanup_cache_dir(cache_dir: Path) -> None:
    if not cache_dir.is_dir():
        return
    for child in cache_dir.iterdir():
        if child.is_dir() and child.name.startswith("cartosky-goes-rgb-"):
            shutil.rmtree(child, ignore_errors=True)


def build_config(args: argparse.Namespace) -> GOESRGBPollerConfig:
    data_root_raw = args.data_root or _env_value(
        "CARTOSKY_GOES_RGB_DATA_ROOT",
        _env_value("CARTOSKY_DATA_ROOT", str(DEFAULT_DATA_ROOT)),
    )
    cache_dir_raw = args.cache_dir or _env_value("CARTOSKY_GOES_RGB_CACHE_DIR", str(DEFAULT_CACHE_DIR))
    return GOESRGBPollerConfig(
        data_root=Path(data_root_raw).expanduser().resolve(),
        cache_dir=Path(cache_dir_raw).expanduser().resolve(),
        satellite=_env_value("CARTOSKY_GOES_RGB_SATELLITE", DEFAULT_SATELLITE),
        bucket=_env_value("CARTOSKY_GOES_RGB_BUCKET", DEFAULT_BUCKET),
        sector=_env_value("CARTOSKY_GOES_RGB_SECTOR", DEFAULT_SECTOR),
        poll_seconds=_int_env("CARTOSKY_GOES_RGB_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=15),
        keep_runs=_int_env("CARTOSKY_GOES_RGB_KEEP_RUNS", DEFAULT_KEEP_RUNS, minimum=1),
        window_minutes=_int_env("CARTOSKY_GOES_RGB_WINDOW_MINUTES", DEFAULT_WINDOW_MINUTES, minimum=1),
        frame_cadence_minutes=_int_env("CARTOSKY_GOES_RGB_FRAME_CADENCE_MINUTES", DEFAULT_FRAME_CADENCE_MINUTES, minimum=1),
        listing_lookback_hours=_int_env("CARTOSKY_GOES_RGB_LISTING_LOOKBACK_HOURS", DEFAULT_LISTING_LOOKBACK_HOURS, minimum=1),
        object_min_age_seconds=_int_env("CARTOSKY_GOES_RGB_OBJECT_MIN_AGE_SECONDS", DEFAULT_OBJECT_MIN_AGE_SECONDS, minimum=0),
        composite_name=_env_value("CARTOSKY_GOES_RGB_COMPOSITE", DEFAULT_COMPOSITE_NAME),
        webp_quality=_int_env("CARTOSKY_GOES_RGB_WEBP_QUALITY", DEFAULT_WEBP_QUALITY, minimum=1),
    )


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
    parser = argparse.ArgumentParser(description="Poll and publish GOES-East true color RGB imagery")
    parser.add_argument("--once", action="store_true", help="Run one poll/publish cycle then exit")
    parser.add_argument("--data-root", default=None, help="Override CARTOSKY_GOES_RGB_DATA_ROOT or CARTOSKY_DATA_ROOT")
    parser.add_argument("--cache-dir", default=None, help="Override CARTOSKY_GOES_RGB_CACHE_DIR")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run_poller(build_config(args), once=bool(args.once))


if __name__ == "__main__":
    raise SystemExit(main())
