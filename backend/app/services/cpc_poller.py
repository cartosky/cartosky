from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path

from app.services.cpc_outlook import (
    CPCOutlookError,
    build_cpc_products_fingerprint,
    collect_latest_cpc_outlooks,
    publish_latest_cpc_outlooks,
)
from app.services.publish_utils import enforce_run_artifact_retention

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_POLL_SECONDS = 21_600
DEFAULT_KEEP_RUNS = 10
DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class CPCPollerConfig:
    data_root: Path
    poll_seconds: int
    keep_runs: int
    timeout_seconds: float


@dataclass(frozen=True)
class CPCPollerCycleResult:
    action: str
    published_run_id: str | None
    latest_issue_time: str | None
    message: str


def run_once(config: CPCPollerConfig) -> CPCPollerCycleResult:
    products, issue_time = collect_latest_cpc_outlooks(timeout_seconds=config.timeout_seconds)
    run_id = issue_time.astimezone(timezone.utc).strftime("%Y%m%d_%H%Mz").lower()
    fingerprint = build_cpc_products_fingerprint(products)
    if (
        _latest_published_run_id(config.data_root) == run_id
        and _bundle_exists(config.data_root, run_id)
        and _manifest_variable_ids(config.data_root, run_id) == set(products.keys())
        and _manifest_source_fingerprint(config.data_root, run_id) == fingerprint
    ):
        return CPCPollerCycleResult(
            action="noop",
            published_run_id=run_id,
            latest_issue_time=issue_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            message=f"CPC latest bundle {run_id} is already published.",
        )

    result = publish_latest_cpc_outlooks(data_root=config.data_root, timeout_seconds=config.timeout_seconds)
    _enforce_retention(config)
    return CPCPollerCycleResult(
        action="published",
        published_run_id=result.run_id,
        latest_issue_time=issue_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        message=f"Published CPC bundle {result.run_id} with {result.frame_count} products.",
    )


def run_poller(config: CPCPollerConfig, *, once: bool) -> int:
    logger.info(
        "CPC poller starting data_root=%s poll=%ss keep_runs=%d timeout=%ss",
        config.data_root,
        config.poll_seconds,
        config.keep_runs,
        config.timeout_seconds,
    )

    while True:
        try:
            result = run_once(config)
            logger.info("CPC cycle result action=%s message=%s", result.action, result.message)
        except CPCOutlookError as exc:
            logger.warning("CPC poller cycle failed; preserving last known good data if present: %s", exc)
        except Exception:
            logger.exception("CPC poller cycle failed")
        if once:
            return 0
        time.sleep(max(300, int(config.poll_seconds)))


def _latest_published_run_id(data_root: Path) -> str | None:
    latest_path = data_root / "published" / "cpc" / "LATEST.json"
    if not latest_path.is_file():
        return None
    try:
        payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    run_id = payload.get("run_id")
    return str(run_id).strip() if isinstance(run_id, str) and run_id.strip() else None


def _manifest_variable_ids(data_root: Path, run_id: str) -> set[str]:
    manifest_path = data_root / "manifests" / "cpc" / f"{run_id}.json"
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


def _manifest_source_fingerprint(data_root: Path, run_id: str) -> str | None:
    manifest_path = data_root / "manifests" / "cpc" / f"{run_id}.json"
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    fingerprint = metadata.get("source_fingerprint")
    return str(fingerprint).strip() if isinstance(fingerprint, str) and fingerprint.strip() else None


def _bundle_exists(data_root: Path, run_id: str) -> bool:
    published_run_dir = data_root / "published" / "cpc" / run_id
    manifest_path = data_root / "manifests" / "cpc" / f"{run_id}.json"
    return published_run_dir.is_dir() and manifest_path.is_file()


def _enforce_retention(config: CPCPollerConfig) -> None:
    enforce_run_artifact_retention(config.data_root / "staging" / "cpc", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "published" / "cpc", config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "manifests" / "cpc", config.keep_runs)


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


def build_config_from_env() -> CPCPollerConfig:
    data_root = Path(_env_value("CARTOSKY_CPC_DATA_ROOT", "CARTOSKY_DATA_ROOT", default=str(DEFAULT_DATA_ROOT))).expanduser()
    poll_seconds = _int_env("CARTOSKY_CPC_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=300)
    keep_runs = _int_env("CARTOSKY_CPC_KEEP_RUNS", DEFAULT_KEEP_RUNS, minimum=1)
    timeout_seconds = _float_env("CARTOSKY_CPC_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, minimum=5.0)
    return CPCPollerConfig(
        data_root=data_root,
        poll_seconds=poll_seconds,
        keep_runs=keep_runs,
        timeout_seconds=timeout_seconds,
    )


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CartoSky CPC outlook poller.")
    parser.add_argument("--once", action="store_true", help="Run one CPC poll cycle and exit.")
    parser.add_argument("--data-root", type=Path, default=None, help="Override CARTOSKY_CPC_DATA_ROOT.")
    parser.add_argument("--timeout-seconds", type=float, default=None, help="Override CARTOSKY_CPC_TIMEOUT_SECONDS.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    config = build_config_from_env()
    if args.data_root is not None or args.timeout_seconds is not None:
        config = CPCPollerConfig(
            data_root=args.data_root or config.data_root,
            poll_seconds=config.poll_seconds,
            keep_runs=config.keep_runs,
            timeout_seconds=max(5.0, float(args.timeout_seconds)) if args.timeout_seconds is not None else config.timeout_seconds,
        )
    return run_poller(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
