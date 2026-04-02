from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.services.mrms_fetch import (
    MRMS_LISTING_URL,
    MRMS_PRECIP_FLAG_FILE_RE,
    MRMS_PRECIP_FLAG_LISTING_URL,
    MRMSScanRef,
    decode_scan,
    discover_recent_scans_http,
    download_scan,
    freeze_bundle_scans,
)
from app.services.mrms_publish import (
    MRMSBundleFrame,
    MRMSPublishResult,
    load_latest_published_mrms_frames,
    publish_mrms_bundle,
)
from app.services.observed_bundle_health import parse_iso_datetime
from app.services.publish_utils import (
    enforce_run_artifact_retention,
)

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_POLL_SECONDS = 120
DEFAULT_KEEP_RUNS = 6
DEFAULT_WINDOW_MINUTES = 120
DEFAULT_FRAME_CADENCE_MINUTES = 5
DEFAULT_LISTING_TIMEOUT_SECONDS = 15.0
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 30.0
DEFAULT_PREFERRED_DECODER = "wgrib2"
DEFAULT_FALLBACK_DECODER = "pygrib"
DEFAULT_FRAME_WRITE_WORKERS = 2


@dataclass(frozen=True)
class MRMSPollerConfig:
    data_root: Path
    listing_url: str
    precip_flag_listing_url: str
    poll_seconds: int
    keep_runs: int
    window_minutes: int
    frame_cadence_minutes: int
    listing_timeout_seconds: float
    download_timeout_seconds: float
    preferred_decoder: str
    fallback_decoder: str
    frame_write_workers: int


@dataclass(frozen=True)
class MRMSPollerCycleResult:
    action: str
    latest_scan_valid_time: str | None
    published_run_id: str | None
    expected_frame_count: int
    decoded_frame_count: int
    failed_scan_count: int
    message: str


def run_once(config: MRMSPollerConfig) -> MRMSPollerCycleResult:
    target_frame_count = compute_target_frame_count(
        window_minutes=config.window_minutes,
        frame_cadence_minutes=config.frame_cadence_minutes,
    )
    scans = discover_recent_scans_http(
        listing_url=config.listing_url,
        limit=max(target_frame_count * 3, target_frame_count),
        timeout_seconds=config.listing_timeout_seconds,
    )
    frozen = freeze_bundle_scans(
        scans,
        max_frames=target_frame_count,
        frame_cadence_minutes=config.frame_cadence_minutes,
    )
    logger.info(
        "MRMS bundle candidate discovered=%d frozen=%d target=%d",
        len(scans),
        len(frozen),
        target_frame_count,
    )

    # Discover PrecipFlag scans and build a lookup by valid_time
    precip_flag_by_time: dict[datetime, MRMSScanRef] = {}
    if config.precip_flag_listing_url:
        try:
            pf_scans = discover_recent_scans_http(
                listing_url=config.precip_flag_listing_url,
                file_re=MRMS_PRECIP_FLAG_FILE_RE,
                limit=max(target_frame_count * 3, target_frame_count),
                timeout_seconds=config.listing_timeout_seconds,
            )
            for pf_scan in pf_scans:
                precip_flag_by_time[pf_scan.valid_time.astimezone(timezone.utc)] = pf_scan
            logger.info("MRMS PrecipFlag discovered=%d", len(pf_scans))
        except Exception:
            logger.exception("MRMS PrecipFlag listing failed; proceeding without precip type data")

    if not frozen:
        return MRMSPollerCycleResult(
            action="noop",
            latest_scan_valid_time=None,
            published_run_id=None,
            expected_frame_count=0,
            decoded_frame_count=0,
            failed_scan_count=0,
            message="No MRMS scans discovered from upstream listing.",
        )

    newest_scan_valid_time = frozen[-1].valid_time.astimezone(timezone.utc)
    latest_published_valid_time, latest_bundle_complete = _latest_published_bundle_state(config.data_root)
    latest_run_id, previous_frames = load_latest_published_mrms_frames(config.data_root)
    previously_published_valid_times = {
        frame.valid_time.astimezone(timezone.utc)
        for frame in previous_frames
    }
    if (
        latest_published_valid_time is not None
        and newest_scan_valid_time <= latest_published_valid_time
        and latest_bundle_complete
    ):
        return MRMSPollerCycleResult(
            action="noop",
            latest_scan_valid_time=_format_iso(newest_scan_valid_time),
            published_run_id=None,
            expected_frame_count=len(frozen),
            decoded_frame_count=0,
            failed_scan_count=0,
            message="No new MRMS scan beyond the current published latest bundle.",
        )

    scans_to_decode = [
        scan for scan in frozen
        if scan.valid_time.astimezone(timezone.utc) not in previously_published_valid_times
    ]
    logger.info(
        "MRMS incremental window previous_run=%s reused=%d decode=%d",
        latest_run_id or "<none>",
        max(0, len(frozen) - len(scans_to_decode)),
        len(scans_to_decode),
    )

    frames: list[MRMSBundleFrame] = []
    failed_scans: list[str] = []
    with tempfile.TemporaryDirectory(prefix="cartosky-mrms-") as tmpdir:
        download_dir = Path(tmpdir)
        total_scans = len(scans_to_decode)
        for index, scan in enumerate(scans_to_decode, start=1):
            logger.info(
                "MRMS frame %d/%d fetching %s valid=%s",
                index,
                total_scans,
                scan.filename,
                _format_iso(scan.source_valid_time or scan.valid_time),
            )
            try:
                pf_scan = _find_closest_precip_flag_scan(
                    scan.valid_time, precip_flag_by_time,
                )
                decoded_frame = _decode_scan_ref(
                    scan,
                    download_dir=download_dir,
                    config=config,
                    precip_flag_scan=pf_scan,
                )
                frames.append(decoded_frame)
                logger.info(
                    "MRMS frame %d/%d ready decoder=%s shape=%s pf=%s",
                    index,
                    total_scans,
                    str(decoded_frame.metadata.get("decoder", "unknown")),
                    tuple(decoded_frame.values.shape),
                    "yes" if decoded_frame.precip_flag_values is not None else "no",
                )
            except Exception as exc:
                logger.warning("Skipping MRMS scan %s after fetch/decode failure: %s", scan.filename, exc)
                failed_scans.append(scan.filename)

    available_valid_times = {
        frame.valid_time.astimezone(timezone.utc)
        for frame in previous_frames
    }
    available_valid_times.update(frame.valid_time.astimezone(timezone.utc) for frame in frames)
    frozen_valid_times = {
        scan.valid_time.astimezone(timezone.utc)
        for scan in frozen
    }
    available_for_window = len(available_valid_times.intersection(frozen_valid_times))

    if not frames and available_for_window == 0:
        return MRMSPollerCycleResult(
            action="noop",
            latest_scan_valid_time=_format_iso(newest_scan_valid_time),
            published_run_id=None,
            expected_frame_count=len(frozen),
            decoded_frame_count=0,
            failed_scan_count=len(failed_scans),
            message="No publishable MRMS bundle could be built from the frozen scan window.",
        )

    logger.info(
        "MRMS publish starting new=%d reused=%d available=%d failed=%d latest_scan=%s",
        len(frames),
        max(0, available_for_window - len(frames)),
        available_for_window,
        len(failed_scans),
        _format_iso(newest_scan_valid_time),
    )
    publish_result = publish_mrms_bundle(
        data_root=config.data_root,
        frames=frames,
        publish_time=datetime.now(timezone.utc),
        frame_write_workers=config.frame_write_workers,
        previous_frames=previous_frames,
        target_frame_count=len(frozen),
        expected_frame_count=len(frozen),
    )
    _enforce_retention(config)

    message = (
        f"Published MRMS bundle {publish_result.run_id} "
        f"with {available_for_window}/{len(frozen)} frames"
    )
    if failed_scans:
        message += f" ({len(failed_scans)} failed scans skipped)"

    return MRMSPollerCycleResult(
        action="published",
        latest_scan_valid_time=_format_iso(newest_scan_valid_time),
        published_run_id=publish_result.run_id,
        expected_frame_count=len(frozen),
        decoded_frame_count=available_for_window,
        failed_scan_count=len(failed_scans),
        message=message,
    )


def run_poller(config: MRMSPollerConfig, *, once: bool) -> int:
    logger.info(
        "MRMS poller starting listing=%s precip_flag=%s data_root=%s poll=%ss keep_runs=%d window=%dm cadence=%dm decoder=%s/%s",
        config.listing_url,
        config.precip_flag_listing_url,
        config.data_root,
        config.poll_seconds,
        config.keep_runs,
        config.window_minutes,
        config.frame_cadence_minutes,
        config.preferred_decoder,
        config.fallback_decoder,
    )

    while True:
        try:
            result = run_once(config)
            log_level = logging.INFO if result.action == "published" else logging.INFO
            logger.log(log_level, "MRMS cycle result action=%s message=%s", result.action, result.message)
        except Exception:
            logger.exception("MRMS poller cycle failed")
        if once:
            return 0
        time.sleep(max(15, int(config.poll_seconds)))


def compute_target_frame_count(*, window_minutes: int, frame_cadence_minutes: int) -> int:
    safe_window = max(1, int(window_minutes))
    safe_cadence = max(1, int(frame_cadence_minutes))
    return max(1, (safe_window // safe_cadence) + 1)


PRECIP_FLAG_MATCH_TOLERANCE = timedelta(minutes=4)


def _find_closest_precip_flag_scan(
    valid_time: datetime,
    precip_flag_by_time: dict[datetime, MRMSScanRef],
) -> MRMSScanRef | None:
    """Find the PrecipFlag scan closest to the given valid_time within tolerance."""
    if not precip_flag_by_time:
        return None
    target = valid_time.astimezone(timezone.utc)
    best: MRMSScanRef | None = None
    best_delta = PRECIP_FLAG_MATCH_TOLERANCE
    for pf_time, pf_scan in precip_flag_by_time.items():
        delta = abs(pf_time - target)
        if delta < best_delta:
            best_delta = delta
            best = pf_scan
    return best


def _decode_scan_ref(
    scan: MRMSScanRef,
    *,
    download_dir: Path,
    config: MRMSPollerConfig,
    precip_flag_scan: MRMSScanRef | None = None,
) -> MRMSBundleFrame:
    downloaded = download_scan(
        scan,
        dest_dir=download_dir,
        timeout_seconds=config.download_timeout_seconds,
    )
    decoded = decode_scan(
        downloaded,
        valid_time=scan.valid_time,
        preferred_decoder=config.preferred_decoder,
        fallback_decoder=config.fallback_decoder,
    )

    precip_flag_values: np.ndarray | None = None
    if precip_flag_scan is not None:
        try:
            pf_downloaded = download_scan(
                precip_flag_scan,
                dest_dir=download_dir,
                timeout_seconds=config.download_timeout_seconds,
            )
            pf_decoded = decode_scan(
                pf_downloaded,
                valid_time=precip_flag_scan.valid_time,
                file_re=MRMS_PRECIP_FLAG_FILE_RE,
                preferred_decoder=config.preferred_decoder,
                fallback_decoder=config.fallback_decoder,
            )
            precip_flag_values = pf_decoded.values
            logger.info(
                "MRMS PrecipFlag decoded shape=%s for refl scan valid=%s",
                tuple(pf_decoded.values.shape),
                _format_iso(scan.valid_time),
            )
        except Exception:
            logger.warning(
                "MRMS PrecipFlag decode failed for %s; reflectivity-only frame",
                precip_flag_scan.filename,
                exc_info=True,
            )

    return MRMSBundleFrame(
        valid_time=scan.valid_time,
        source_valid_time=decoded.valid_time,
        values=decoded.values,
        source_crs=getattr(decoded, "source_crs", None),
        source_transform=getattr(decoded, "source_transform", None),
        source_url=scan.url,
        source_filename=scan.filename,
        metadata={
            "decoder": decoded.decoder,
            **dict(decoded.metadata),
        },
        precip_flag_values=precip_flag_values,
    )


def _latest_published_bundle_state(data_root: Path) -> tuple[datetime | None, bool]:
    latest_path = data_root / "published" / "mrms" / "LATEST.json"
    if not latest_path.is_file():
        return None, False
    try:
        latest_payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, False
    run_id = latest_payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return None, False

    manifest_path = data_root / "manifests" / "mrms" / f"{run_id}.json"
    if not manifest_path.is_file():
        return None, False
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, False

    metadata = manifest.get("metadata")
    bundle_complete = False
    if isinstance(metadata, dict):
        parsed = parse_iso_datetime(metadata.get("latest_scan_valid_time"))
        available = metadata.get("available_frame_count")
        target = metadata.get("target_frame_count")
        if isinstance(available, int) and isinstance(target, int) and target > 0 and available >= target:
            bundle_complete = True
        if parsed is not None:
            return parsed, bundle_complete

    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return None, False
    newest: datetime | None = None
    total_expected = 0
    total_available = 0
    for entry in variables.values():
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("expected_frames"), int):
            total_expected += max(0, int(entry["expected_frames"]))
        frames = entry.get("frames")
        if isinstance(entry.get("available_frames"), int):
            total_available += max(0, int(entry["available_frames"]))
        elif isinstance(frames, list):
            total_available += len(frames)
        frames = entry.get("frames")
        if not isinstance(frames, list):
            continue
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            parsed = parse_iso_datetime(frame.get("valid_time"))
            if parsed is None:
                continue
            if newest is None or parsed > newest:
                newest = parsed
    if total_expected > 0 and total_available >= total_expected:
        bundle_complete = True
    return newest, bundle_complete


def _enforce_retention(config: MRMSPollerConfig) -> None:
    enforce_run_artifact_retention(config.data_root / "staging" / "mrms", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "published" / "mrms", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "manifests" / "mrms", config.keep_runs)


def _env_value(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and raw != "":
            return raw
    return default


def _int_env(name: str, fallback: int, *, minimum: int) -> int:
    raw = _env_value(name).strip()
    if not raw:
        return fallback
    try:
        parsed = int(raw)
    except ValueError:
        return fallback
    return max(minimum, parsed)


def _float_env(name: str, fallback: float, *, minimum: float) -> float:
    raw = _env_value(name).strip()
    if not raw:
        return fallback
    try:
        parsed = float(raw)
    except ValueError:
        return fallback
    return max(minimum, parsed)


def _bool_env(name: str, fallback: bool) -> bool:
    raw = _env_value(name).strip().lower()
    if not raw:
        return fallback
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return fallback


def _data_root(cli_data_root: str | None) -> Path:
    if cli_data_root:
        return Path(cli_data_root).resolve()
    return Path(_env_value("CARTOSKY_DATA_ROOT", default=str(DEFAULT_DATA_ROOT))).resolve()


def build_config(args: argparse.Namespace) -> MRMSPollerConfig:
    data_root = _data_root(args.data_root)
    return MRMSPollerConfig(
        data_root=data_root,
        listing_url=(
            args.listing_url.strip()
            if isinstance(args.listing_url, str) and args.listing_url.strip()
            else _env_value("CARTOSKY_MRMS_LISTING_URL", default=MRMS_LISTING_URL)
        ),
        precip_flag_listing_url=_env_value(
            "CARTOSKY_MRMS_PRECIP_FLAG_LISTING_URL",
            default=MRMS_PRECIP_FLAG_LISTING_URL,
        ),
        poll_seconds=(
            int(args.poll_seconds)
            if args.poll_seconds is not None
            else _int_env("CARTOSKY_MRMS_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=15)
        ),
        keep_runs=(
            int(args.keep_runs)
            if args.keep_runs is not None
            else _int_env("CARTOSKY_MRMS_KEEP_RUNS", DEFAULT_KEEP_RUNS, minimum=1)
        ),
        window_minutes=(
            int(args.window_minutes)
            if args.window_minutes is not None
            else _int_env("CARTOSKY_MRMS_WINDOW_MINUTES", DEFAULT_WINDOW_MINUTES, minimum=1)
        ),
        frame_cadence_minutes=(
            int(args.frame_cadence_minutes)
            if args.frame_cadence_minutes is not None
            else _int_env("CARTOSKY_MRMS_FRAME_CADENCE_MINUTES", DEFAULT_FRAME_CADENCE_MINUTES, minimum=1)
        ),
        listing_timeout_seconds=_float_env(
            "CARTOSKY_MRMS_LISTING_TIMEOUT_SECONDS",
            DEFAULT_LISTING_TIMEOUT_SECONDS,
            minimum=1.0,
        ),
        download_timeout_seconds=_float_env(
            "CARTOSKY_MRMS_DOWNLOAD_TIMEOUT_SECONDS",
            DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
            minimum=1.0,
        ),
        preferred_decoder=_env_value("CARTOSKY_MRMS_PREFERRED_DECODER", default=DEFAULT_PREFERRED_DECODER),
        fallback_decoder=_env_value("CARTOSKY_MRMS_FALLBACK_DECODER", default=DEFAULT_FALLBACK_DECODER),
        frame_write_workers=_int_env(
            "CARTOSKY_MRMS_FRAME_WRITE_WORKERS",
            DEFAULT_FRAME_WRITE_WORKERS,
            minimum=1,
        ),
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the CartoSky MRMS poller.")
    parser.add_argument("--data-root", default=None, help="Override CARTOSKY_DATA_ROOT")
    parser.add_argument("--listing-url", default=None, help="Override CARTOSKY_MRMS_LISTING_URL")
    parser.add_argument("--poll-seconds", type=int, default=None, help="Poll interval in loop mode")
    parser.add_argument("--keep-runs", type=int, default=None, help="Retention count for MRMS runs")
    parser.add_argument("--window-minutes", type=int, default=None, help="Rolling window length in minutes")
    parser.add_argument("--frame-cadence-minutes", type=int, default=None, help="Expected MRMS scan cadence in minutes")
    parser.add_argument("--once", action="store_true", help="Run one MRMS poll/publish cycle then exit")
    return parser.parse_args(argv)


def _format_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    config = build_config(args)
    return run_poller(config, once=bool(args.once))


if __name__ == "__main__":
    raise SystemExit(main())
