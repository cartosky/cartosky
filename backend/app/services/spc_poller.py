from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.publish_utils import enforce_run_artifact_retention
from app.services.spc_publish import (
    SPC_LAYER_BASE_URL,
    build_spc_products_fingerprint,
    collect_latest_spc_products,
    publish_spc_products_bundle,
)

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
    fingerprint = build_spc_products_fingerprint(products)
    if (
        _latest_published_run_id(config.data_root) == run_id
        and _bundle_exists(config.data_root, run_id)
        and _manifest_variable_ids(config.data_root, run_id) == set(products.keys())
        and _published_products_fingerprint(config.data_root, run_id) == fingerprint
    ):
        return SPCPollerCycleResult(
            action="noop",
            published_run_id=run_id,
            latest_issue_time=issue_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            message=f"SPC latest bundle {run_id} is already published.",
        )

    result = publish_spc_products_bundle(
        data_root=config.data_root,
        products=products,
        issue_time=issue_time,
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


def _manifest_source_fingerprint(data_root: Path, run_id: str) -> str | None:
    manifest_path = data_root / "manifests" / "spc" / f"{run_id}.json"
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


def _fingerprint_from_published_sidecars(data_root: Path, run_id: str) -> str | None:
    run_dir = data_root / "published" / "spc" / run_id
    if not run_dir.is_dir():
        return None

    parts: list[str] = []
    for var_dir in sorted(path for path in run_dir.iterdir() if path.is_dir()):
        var_id = var_dir.name
        for sidecar_path in sorted(var_dir.glob("fh*.json")):
            try:
                sidecar = json.loads(sidecar_path.read_text())
            except (OSError, json.JSONDecodeError):
                return None
            fh = sidecar.get("fh")
            issue_time = sidecar.get("issue_time")
            if not isinstance(fh, int) or not isinstance(issue_time, str) or not issue_time.strip():
                return None
            vector_path = var_dir / "vectors" / f"fh{int(fh):03d}.geojson"
            feature_count = 0
            if vector_path.is_file():
                try:
                    vector_payload = json.loads(vector_path.read_text())
                except (OSError, json.JSONDecodeError):
                    return None
                features = vector_payload.get("features")
                if isinstance(features, list):
                    feature_count = len(features)
            parts.append(f"{var_id}:{int(fh)}:{issue_time.strip()}:{feature_count}")

    if not parts:
        return None
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _published_products_fingerprint(data_root: Path, run_id: str) -> str | None:
    return _manifest_source_fingerprint(data_root, run_id) or _fingerprint_from_published_sidecars(data_root, run_id)


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