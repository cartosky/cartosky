from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from app.services.publish_utils import enforce_run_artifact_retention
from app.services.run_ids import format_run_id
from app.services.wpc_publish import WPC_PUBLISH_SOURCE, publish_wpc_bundle
from app.services.wpc_source import WPC_LISTING_URL, collect_latest_wpc_fields

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_POLL_SECONDS = 3600
DEFAULT_KEEP_RUNS = 8
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_FORECAST_HOURS = 168
DEFAULT_FORECAST_STEP_HOURS = 6


@dataclass(frozen=True)
class WPCPollerConfig:
    data_root: Path
    listing_url: str
    poll_seconds: int
    keep_runs: int
    timeout_seconds: float
    max_forecast_hours: int
    forecast_step_hours: int


@dataclass(frozen=True)
class WPCPollerCycleResult:
    action: str
    published_run_id: str | None
    latest_issue_time: str | None
    frame_count: int
    message: str


def run_once(config: WPCPollerConfig) -> WPCPollerCycleResult:
    issue_time, frames_by_var = collect_latest_wpc_fields(
        timeout_seconds=config.timeout_seconds,
        listing_url=config.listing_url,
        max_forecast_hour=config.max_forecast_hours,
        cadence_hours=config.forecast_step_hours,
    )
    run_id = format_run_id(issue_time.astimezone(), include_minutes=True)
    latest_run_id = _latest_published_run_id(config.data_root)
    if latest_run_id == run_id and _bundle_exists(config.data_root, run_id):
        if (
            _manifest_variable_ids(config.data_root, run_id) == set(frames_by_var.keys())
            and _manifest_source(config.data_root, run_id) == WPC_PUBLISH_SOURCE
        ):
            _enforce_retention(config)
            return WPCPollerCycleResult(
                action="noop",
                published_run_id=run_id,
                latest_issue_time=issue_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                frame_count=sum(len(frames) for frames in frames_by_var.values()),
                message=f"WPC latest bundle {run_id} is already published.",
            )

    result = publish_wpc_bundle(
        data_root=config.data_root,
        issue_time=issue_time,
        frames_by_var=frames_by_var,
    )

    _enforce_retention(config)
    return WPCPollerCycleResult(
        action="published",
        published_run_id=result.run_id,
        latest_issue_time=issue_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        frame_count=result.frame_count,
        message=f"Published WPC bundle {result.run_id} with {result.frame_count} frames.",
    )


def run_poller(config: WPCPollerConfig, *, once: bool) -> int:
    logger.info(
        "WPC poller starting listing_url=%s data_root=%s poll=%ss keep_runs=%d timeout=%ss max_fh=%d step=%dh",
        config.listing_url,
        config.data_root,
        config.poll_seconds,
        config.keep_runs,
        config.timeout_seconds,
        config.max_forecast_hours,
        config.forecast_step_hours,
    )

    while True:
        try:
            result = run_once(config)
            logger.info("WPC cycle result action=%s message=%s", result.action, result.message)
        except Exception:
            logger.exception("WPC poller cycle failed")
        if once:
            return 0
        time.sleep(max(300, int(config.poll_seconds)))


def _latest_published_run_id(data_root: Path) -> str | None:
    latest_path = data_root / "published" / "wpc" / "LATEST.json"
    if not latest_path.is_file():
        return None
    try:
        payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    run_id = payload.get("run_id")
    return str(run_id).strip() if isinstance(run_id, str) and run_id.strip() else None


def _manifest_variable_ids(data_root: Path, run_id: str) -> set[str]:
    manifest_path = data_root / "manifests" / "wpc" / f"{run_id}.json"
    if not manifest_path.is_file():
        return set()
    try:
        payload = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    variables = payload.get("variables")
    if not isinstance(variables, dict):
        return set()
    return {str(key).strip() for key in variables.keys() if str(key).strip()}


def _manifest_source(data_root: Path, run_id: str) -> str | None:
    manifest_path = data_root / "manifests" / "wpc" / f"{run_id}.json"
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    source = metadata.get("source")
    return str(source).strip() if isinstance(source, str) and source.strip() else None


def _bundle_exists(data_root: Path, run_id: str) -> bool:
    published_run_dir = data_root / "published" / "wpc" / run_id
    manifest_path = data_root / "manifests" / "wpc" / f"{run_id}.json"
    return published_run_dir.is_dir() and manifest_path.is_file()


def _enforce_retention(config: WPCPollerConfig) -> None:
    enforce_run_artifact_retention(config.data_root / "staging" / "wpc", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "published" / "wpc", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "manifests" / "wpc", config.keep_runs)


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


def build_config_from_env() -> WPCPollerConfig:
    data_root = Path(_env_value("CARTOSKY_WPC_DATA_ROOT", "CARTOSKY_DATA_ROOT", default=str(DEFAULT_DATA_ROOT))).expanduser()
    listing_url = _env_value("CARTOSKY_WPC_LISTING_URL", default=WPC_LISTING_URL).strip() or WPC_LISTING_URL
    poll_seconds = _int_env("CARTOSKY_WPC_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=300)
    keep_runs = _int_env("CARTOSKY_WPC_KEEP_RUNS", DEFAULT_KEEP_RUNS, minimum=1)
    timeout_seconds = _float_env("CARTOSKY_WPC_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, minimum=5.0)
    max_forecast_hours = _int_env("CARTOSKY_WPC_MAX_FORECAST_HOURS", DEFAULT_MAX_FORECAST_HOURS, minimum=6)
    forecast_step_hours = _int_env("CARTOSKY_WPC_FORECAST_STEP_HOURS", DEFAULT_FORECAST_STEP_HOURS, minimum=1)
    return WPCPollerConfig(
        data_root=data_root,
        listing_url=listing_url,
        poll_seconds=poll_seconds,
        keep_runs=keep_runs,
        timeout_seconds=timeout_seconds,
        max_forecast_hours=max_forecast_hours,
        forecast_step_hours=forecast_step_hours,
    )


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CartoSky WPC poller.")
    parser.add_argument("--once", action="store_true", help="Run one WPC poll cycle and exit.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    config = build_config_from_env()
    return run_poller(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())