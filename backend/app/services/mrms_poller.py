from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.mrms_fetch import (
    MRMS_LISTING_URL,
    MRMSScanRef,
    decode_scan,
    discover_recent_scans_http,
    download_scan,
    freeze_bundle_scans,
)
from app.services.mrms_publish import (
    MRMSBundleFrame,
    MRMSLoopPublishSettings,
    MRMSPublishResult,
    publish_mrms_bundle,
)
from app.services.observed_bundle_health import parse_iso_datetime
from app.services.publish_utils import (
    DEFAULT_LOOP_WEBP_MAX_DIM,
    DEFAULT_LOOP_WEBP_QUALITY,
    DEFAULT_LOOP_WEBP_TIER0_FIXED_W,
    DEFAULT_LOOP_WEBP_TIER1_FIXED_W,
    DEFAULT_LOOP_WEBP_TIER1_MAX_DIM,
    DEFAULT_LOOP_WEBP_TIER1_QUALITY,
    enforce_run_artifact_retention,
)

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_POLL_SECONDS = 120
DEFAULT_KEEP_RUNS = 6
DEFAULT_WINDOW_MINUTES = 120
DEFAULT_FRAME_CADENCE_MINUTES = 2
DEFAULT_LISTING_TIMEOUT_SECONDS = 15.0
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 30.0
DEFAULT_PREFERRED_DECODER = "wgrib2"
DEFAULT_FALLBACK_DECODER = "pygrib"
DEFAULT_LOOP_PREGENERATE_ENABLED = True
DEFAULT_LOOP_CACHE_ROOT = Path("/opt/cartosky/data/loop_cache")
DEFAULT_LOOP_PREGENERATE_WORKERS = 2


@dataclass(frozen=True)
class MRMSPollerConfig:
    data_root: Path
    listing_url: str
    poll_seconds: int
    keep_runs: int
    window_minutes: int
    frame_cadence_minutes: int
    listing_timeout_seconds: float
    download_timeout_seconds: float
    preferred_decoder: str
    fallback_decoder: str
    loop_pregenerate_enabled: bool
    loop_cache_root: Path
    loop_workers: int
    loop_tier0_quality: int
    loop_tier0_max_dim: int
    loop_tier0_fixed_w: int
    loop_tier1_quality: int
    loop_tier1_max_dim: int
    loop_tier1_fixed_w: int


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
    frozen = freeze_bundle_scans(scans, max_frames=target_frame_count)
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

    frames: list[MRMSBundleFrame] = []
    failed_scans: list[str] = []
    with tempfile.TemporaryDirectory(prefix="cartosky-mrms-") as tmpdir:
        download_dir = Path(tmpdir)
        for scan in frozen:
            try:
                frames.append(_decode_scan_ref(scan, download_dir=download_dir, config=config))
            except Exception as exc:
                logger.warning("Skipping MRMS scan %s after fetch/decode failure: %s", scan.filename, exc)
                failed_scans.append(scan.filename)

    if not frames:
        return MRMSPollerCycleResult(
            action="noop",
            latest_scan_valid_time=_format_iso(newest_scan_valid_time),
            published_run_id=None,
            expected_frame_count=len(frozen),
            decoded_frame_count=0,
            failed_scan_count=len(failed_scans),
            message="No publishable MRMS bundle could be built from the frozen scan window.",
        )

    publish_result = publish_mrms_bundle(
        data_root=config.data_root,
        frames=frames,
        publish_time=datetime.now(timezone.utc),
        loop_settings=_loop_settings(config) if config.loop_pregenerate_enabled else None,
    )
    _enforce_retention(config)

    message = (
        f"Published MRMS bundle {publish_result.run_id} "
        f"with {len(frames)}/{len(frozen)} frames"
    )
    if failed_scans:
        message += f" ({len(failed_scans)} failed scans skipped)"

    return MRMSPollerCycleResult(
        action="published",
        latest_scan_valid_time=_format_iso(newest_scan_valid_time),
        published_run_id=publish_result.run_id,
        expected_frame_count=len(frozen),
        decoded_frame_count=len(frames),
        failed_scan_count=len(failed_scans),
        message=message,
    )


def run_poller(config: MRMSPollerConfig, *, once: bool) -> int:
    logger.info(
        "MRMS poller starting listing=%s data_root=%s poll=%ss keep_runs=%d window=%dm cadence=%dm decoder=%s/%s loop_pregen=%s",
        config.listing_url,
        config.data_root,
        config.poll_seconds,
        config.keep_runs,
        config.window_minutes,
        config.frame_cadence_minutes,
        config.preferred_decoder,
        config.fallback_decoder,
        config.loop_pregenerate_enabled,
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


def _decode_scan_ref(
    scan: MRMSScanRef,
    *,
    download_dir: Path,
    config: MRMSPollerConfig,
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
    return MRMSBundleFrame(
        valid_time=decoded.valid_time,
        values=decoded.values,
        source_url=scan.url,
        source_filename=scan.filename,
        metadata={
            "decoder": decoded.decoder,
            **dict(decoded.metadata),
        },
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
    enforce_run_artifact_retention(config.loop_cache_root / "mrms", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "manifests" / "mrms", config.keep_runs)


def _loop_settings(config: MRMSPollerConfig) -> MRMSLoopPublishSettings:
    return MRMSLoopPublishSettings(
        loop_cache_root=config.loop_cache_root,
        workers=config.loop_workers,
        tier0_quality=config.loop_tier0_quality,
        tier0_max_dim=config.loop_tier0_max_dim,
        tier0_fixed_w=config.loop_tier0_fixed_w,
        tier1_quality=config.loop_tier1_quality,
        tier1_max_dim=config.loop_tier1_max_dim,
        tier1_fixed_w=config.loop_tier1_fixed_w,
    )


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
        loop_pregenerate_enabled=_bool_env(
            "CARTOSKY_MRMS_LOOP_PREGENERATE_ENABLED",
            DEFAULT_LOOP_PREGENERATE_ENABLED,
        ),
        loop_cache_root=Path(
            _env_value("CARTOSKY_LOOP_CACHE_ROOT", default=str(DEFAULT_LOOP_CACHE_ROOT))
        ).resolve(),
        loop_workers=_int_env("CARTOSKY_LOOP_PREGENERATE_WORKERS", DEFAULT_LOOP_PREGENERATE_WORKERS, minimum=1),
        loop_tier0_quality=_int_env("CARTOSKY_LOOP_WEBP_QUALITY", DEFAULT_LOOP_WEBP_QUALITY, minimum=1),
        loop_tier0_max_dim=_int_env("CARTOSKY_LOOP_WEBP_MAX_DIM", DEFAULT_LOOP_WEBP_MAX_DIM, minimum=64),
        loop_tier0_fixed_w=_int_env("CARTOSKY_LOOP_WEBP_TIER0_FIXED_W", DEFAULT_LOOP_WEBP_TIER0_FIXED_W, minimum=64),
        loop_tier1_quality=_int_env("CARTOSKY_LOOP_WEBP_TIER1_QUALITY", DEFAULT_LOOP_WEBP_TIER1_QUALITY, minimum=1),
        loop_tier1_max_dim=_int_env("CARTOSKY_LOOP_WEBP_TIER1_MAX_DIM", DEFAULT_LOOP_WEBP_TIER1_MAX_DIM, minimum=64),
        loop_tier1_fixed_w=_int_env("CARTOSKY_LOOP_WEBP_TIER1_FIXED_W", DEFAULT_LOOP_WEBP_TIER1_FIXED_W, minimum=64),
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
