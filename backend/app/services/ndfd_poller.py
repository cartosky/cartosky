from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

from app.services.ndfd_publish import publish_ndfd_bundle
from app.services.ndfd_source import prepare_latest_ndfd_field_stream
from app.services.process_memory import current_rss_bytes, peak_rss_bytes
from app.services.publish_utils import enforce_run_artifact_retention
from app.services.run_ids import format_run_id

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_POLL_SECONDS = 1800
DEFAULT_KEEP_RUNS = 8
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class NDFDPollerConfig:
    data_root: Path
    poll_seconds: int
    keep_runs: int
    timeout_seconds: float


@dataclass(frozen=True)
class NDFDPollerCycleResult:
    action: str
    published_run_id: str | None
    latest_issue_time: str | None
    variable_count: int
    message: str


def run_once(config: NDFDPollerConfig) -> NDFDPollerCycleResult:
    _log_ndfd_memory_checkpoint("before_build")
    with prepare_latest_ndfd_field_stream(timeout_seconds=config.timeout_seconds) as field_stream:
        issue_time = field_stream.issue_time
        variable_ids = set(field_stream.variable_ids)
        run_id = format_run_id(issue_time.astimezone(timezone.utc), include_minutes=True)
        latest_run_id = _latest_published_run_id(config.data_root)
        if latest_run_id == run_id and _bundle_exists(config.data_root, run_id):
            if _manifest_variable_ids(config.data_root, run_id) == variable_ids:
                return NDFDPollerCycleResult(
                    action="noop",
                    published_run_id=run_id,
                    latest_issue_time=issue_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    variable_count=len(variable_ids),
                    message=f"NDFD latest bundle {run_id} is already published.",
                )

        result = publish_ndfd_bundle(
            data_root=config.data_root,
            issue_time=issue_time,
            frame_batches=field_stream.iter_variable_frames(),
            variable_ids=field_stream.variable_ids,
        )
        _log_ndfd_memory_checkpoint("after_publish", run=result.run_id)
        _enforce_retention(config)
        return NDFDPollerCycleResult(
            action="published",
            published_run_id=result.run_id,
            latest_issue_time=issue_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            variable_count=len(variable_ids),
            message=(
                f"Published NDFD bundle {result.run_id} with {result.frame_count} "
                f"frames across {len(variable_ids)} variables."
            ),
        )


def run_poller(config: NDFDPollerConfig, *, once: bool) -> int:
    logger.info(
        "NDFD poller starting data_root=%s poll=%ss keep_runs=%d timeout=%ss",
        config.data_root,
        config.poll_seconds,
        config.keep_runs,
        config.timeout_seconds,
    )

    while True:
        try:
            result = run_once(config)
            _log_ndfd_memory_checkpoint("after_cleanup", action=result.action, run=result.published_run_id or "none")
            logger.info("NDFD cycle result action=%s message=%s", result.action, result.message)
        except Exception:
            logger.exception("NDFD poller cycle failed")
        if once:
            return 0
        time.sleep(max(300, int(config.poll_seconds)))


def _latest_published_run_id(data_root: Path) -> str | None:
    latest_path = data_root / "published" / "ndfd" / "LATEST.json"
    if not latest_path.is_file():
        return None
    try:
        payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    run_id = payload.get("run_id")
    return str(run_id).strip() if isinstance(run_id, str) and run_id.strip() else None


def _manifest_variable_ids(data_root: Path, run_id: str) -> set[str]:
    manifest_path = data_root / "manifests" / "ndfd" / f"{run_id}.json"
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


def _bundle_exists(data_root: Path, run_id: str) -> bool:
    published_run_dir = data_root / "published" / "ndfd" / run_id
    manifest_path = data_root / "manifests" / "ndfd" / f"{run_id}.json"
    return published_run_dir.is_dir() and manifest_path.is_file()


def _enforce_retention(config: NDFDPollerConfig) -> None:
    enforce_run_artifact_retention(config.data_root / "staging" / "ndfd", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "published" / "ndfd", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "manifests" / "ndfd", config.keep_runs)


def _bytes_to_mib(num_bytes: int) -> float:
    return float(num_bytes) / (1024.0 * 1024.0)


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


def build_config_from_env() -> NDFDPollerConfig:
    data_root = Path(_env_value("CARTOSKY_NDFD_DATA_ROOT", "CARTOSKY_DATA_ROOT", default=str(DEFAULT_DATA_ROOT))).expanduser()
    poll_seconds = _int_env("CARTOSKY_NDFD_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=300)
    keep_runs = _int_env("CARTOSKY_NDFD_KEEP_RUNS", DEFAULT_KEEP_RUNS, minimum=1)
    timeout_seconds = _float_env("CARTOSKY_NDFD_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, minimum=5.0)
    return NDFDPollerConfig(
        data_root=data_root,
        poll_seconds=poll_seconds,
        keep_runs=keep_runs,
        timeout_seconds=timeout_seconds,
    )


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CartoSky NDFD poller.")
    parser.add_argument("--once", action="store_true", help="Run one NDFD poll cycle and exit.")
    parser.add_argument("--data-root", type=Path, default=None, help="Override CARTOSKY_NDFD_DATA_ROOT.")
    parser.add_argument("--timeout-seconds", type=float, default=None, help="Override CARTOSKY_NDFD_TIMEOUT_SECONDS.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    config = build_config_from_env()
    if args.data_root is not None or args.timeout_seconds is not None:
        config = NDFDPollerConfig(
            data_root=args.data_root or config.data_root,
            poll_seconds=config.poll_seconds,
            keep_runs=config.keep_runs,
            timeout_seconds=max(5.0, float(args.timeout_seconds)) if args.timeout_seconds is not None else config.timeout_seconds,
        )
    return run_poller(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
