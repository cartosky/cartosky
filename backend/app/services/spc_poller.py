from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.publish_utils import enforce_run_artifact_retention
from app.services.spc_publish import SPC_LAYER_BASE_URL, collect_latest_spc_products, publish_latest_spc_outlooks

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_POLL_SECONDS = 900
DEFAULT_KEEP_RUNS = 10
DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class SPCPollerConfig:
    data_root: Path
    poll_seconds: int
    keep_runs: int
    timeout_seconds: float
    base_url: str


@dataclass(frozen=True)
class SPCPollerCycleResult:
    action: str
    published_run_id: str | None
    latest_issue_time: str | None
    message: str


def run_once(config: SPCPollerConfig) -> SPCPollerCycleResult:
    products, issue_time = collect_latest_spc_products(
        timeout_seconds=config.timeout_seconds,
        base_url=config.base_url,
    )
    run_id = issue_time.astimezone(timezone.utc).strftime("%Y%m%d_%H%Mz").lower()
    if _latest_published_run_id(config.data_root) == run_id and _bundle_exists(config.data_root, run_id):
        if _manifest_variable_ids(config.data_root, run_id) == set(products.keys()):
            return SPCPollerCycleResult(
                action="noop",
                published_run_id=run_id,
                latest_issue_time=issue_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                message=f"SPC latest bundle {run_id} is already published.",
            )

    result = publish_latest_spc_outlooks(
        data_root=config.data_root,
        timeout_seconds=config.timeout_seconds,
        base_url=config.base_url,
    )
    _enforce_retention(config)
    return SPCPollerCycleResult(
        action="published",
        published_run_id=result.run_id,
        latest_issue_time=issue_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        message=f"Published SPC bundle {result.run_id} with {result.frame_count} frames across {len(result.variable_ids)} variables.",
    )


def run_poller(config: SPCPollerConfig, *, once: bool) -> int:
    logger.info(
        "SPC poller starting base_url=%s data_root=%s poll=%ss keep_runs=%d timeout=%ss",
        config.base_url,
        config.data_root,
        config.poll_seconds,
        config.keep_runs,
        config.timeout_seconds,
    )

    while True:
        try:
            result = run_once(config)
            logger.info("SPC cycle result action=%s message=%s", result.action, result.message)
        except Exception:
            logger.exception("SPC poller cycle failed")
        if once:
            return 0
        time.sleep(max(60, int(config.poll_seconds)))


def _latest_published_run_id(data_root: Path) -> str | None:
    latest_path = data_root / "published" / "spc" / "LATEST.json"
    if not latest_path.is_file():
        return None
    try:
        payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    run_id = payload.get("run_id")
    return str(run_id).strip() if isinstance(run_id, str) and run_id.strip() else None


def _manifest_variable_ids(data_root: Path, run_id: str) -> set[str]:
    manifest_path = data_root / "manifests" / "spc" / f"{run_id}.json"
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
    published_run_dir = data_root / "published" / "spc" / run_id
    manifest_path = data_root / "manifests" / "spc" / f"{run_id}.json"
    return published_run_dir.is_dir() and manifest_path.is_file()


def _enforce_retention(config: SPCPollerConfig) -> None:
    enforce_run_artifact_retention(config.data_root / "staging" / "spc", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "published" / "spc", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "manifests" / "spc", config.keep_runs)


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


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def build_config_from_env() -> SPCPollerConfig:
    data_root = Path(_env_value("CARTOSKY_SPC_DATA_ROOT", "CARTOSKY_DATA_ROOT", default=str(DEFAULT_DATA_ROOT))).expanduser()
    poll_seconds = _int_env("CARTOSKY_SPC_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=60)
    keep_runs = _int_env("CARTOSKY_SPC_KEEP_RUNS", DEFAULT_KEEP_RUNS, minimum=1)
    timeout_seconds = _float_env("CARTOSKY_SPC_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, minimum=5.0)
    base_url = _env_value("CARTOSKY_SPC_BASE_URL", default=SPC_LAYER_BASE_URL).strip() or SPC_LAYER_BASE_URL
    return SPCPollerConfig(
        data_root=data_root,
        poll_seconds=poll_seconds,
        keep_runs=keep_runs,
        timeout_seconds=timeout_seconds,
        base_url=base_url,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CartoSky SPC poller.")
    parser.add_argument("--once", action="store_true", help="Run one SPC poll cycle and exit.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    config = build_config_from_env()
    return run_poller(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())