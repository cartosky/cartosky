from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from app.services.nws import NWS_API_BASE, NWS_REQUEST_TIMEOUT
from app.services.nws_hazards import (
    NWS_HAZARDS_MODEL_ID,
    _build_alert_fingerprint,
    default_county_reference_path,
    fetch_active_alerts_geojson,
    publish_active_hazards,
)
from app.services.publish_utils import enforce_run_artifact_retention

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_POLL_SECONDS = 90
DEFAULT_KEEP_RUNS = 20


@dataclass(frozen=True)
class NWSHazardsPollerConfig:
    data_root: Path
    county_reference_path: Path
    poll_seconds: int
    keep_runs: int
    timeout_seconds: float
    api_base: str


@dataclass(frozen=True)
class NWSHazardsPollerCycleResult:
    action: str
    published_run_id: str | None
    fingerprint: str
    message: str


def _latest_published_run_id(data_root: Path) -> str | None:
    latest_path = data_root / "published" / NWS_HAZARDS_MODEL_ID / "LATEST.json"
    if not latest_path.is_file():
        return None
    try:
        payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    run_id = payload.get("run_id")
    return str(run_id).strip() if isinstance(run_id, str) and run_id.strip() else None


def _manifest_fingerprint(data_root: Path, run_id: str) -> str | None:
    manifest_path = data_root / "manifests" / NWS_HAZARDS_MODEL_ID / f"{run_id}.json"
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


def _enforce_retention(config: NWSHazardsPollerConfig) -> None:
    enforce_run_artifact_retention(config.data_root / "staging" / NWS_HAZARDS_MODEL_ID, config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "published" / NWS_HAZARDS_MODEL_ID, config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "manifests" / NWS_HAZARDS_MODEL_ID, config.keep_runs)


def run_once(config: NWSHazardsPollerConfig) -> NWSHazardsPollerCycleResult:
    payload = fetch_active_alerts_geojson(timeout_seconds=config.timeout_seconds, api_base=config.api_base)
    fingerprint = _build_alert_fingerprint(payload)
    latest_run_id = _latest_published_run_id(config.data_root)
    if latest_run_id and _manifest_fingerprint(config.data_root, latest_run_id) == fingerprint:
        return NWSHazardsPollerCycleResult(
            action="noop",
            published_run_id=latest_run_id,
            fingerprint=fingerprint,
            message=f"NWS Hazards active bundle {latest_run_id} already matches upstream fingerprint.",
        )

    result = publish_active_hazards(
        data_root=config.data_root,
        county_reference_path=config.county_reference_path,
        timeout_seconds=config.timeout_seconds,
        api_base=config.api_base,
    )
    _enforce_retention(config)
    return NWSHazardsPollerCycleResult(
        action="published",
        published_run_id=result.run_id,
        fingerprint=result.fingerprint,
        message=f"Published NWS Hazards bundle {result.run_id}.",
    )


def run_poller(config: NWSHazardsPollerConfig, *, once: bool) -> int:
    logger.info(
        "NWS Hazards poller starting data_root=%s county_reference=%s poll=%ss keep_runs=%d timeout=%ss",
        config.data_root,
        config.county_reference_path,
        config.poll_seconds,
        config.keep_runs,
        config.timeout_seconds,
    )
    while True:
        try:
            result = run_once(config)
            logger.info("NWS Hazards cycle result action=%s message=%s", result.action, result.message)
        except Exception:
            logger.exception("NWS Hazards poller cycle failed")
        if once:
            return 0
        time.sleep(max(60, int(config.poll_seconds)))


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


def build_config_from_env() -> NWSHazardsPollerConfig:
    data_root = Path(_env_value("CARTOSKY_NWS_HAZARDS_DATA_ROOT", "CARTOSKY_DATA_ROOT", default=str(DEFAULT_DATA_ROOT))).expanduser()
    county_reference = Path(
        _env_value(
            "CARTOSKY_NWS_HAZARDS_COUNTY_REFERENCE",
            default=str(default_county_reference_path(data_root)),
        )
    ).expanduser()
    poll_seconds = _int_env("CARTOSKY_NWS_HAZARDS_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=60)
    keep_runs = _int_env("CARTOSKY_NWS_HAZARDS_KEEP_RUNS", DEFAULT_KEEP_RUNS, minimum=1)
    timeout_seconds = _float_env("CARTOSKY_NWS_HAZARDS_TIMEOUT_SECONDS", NWS_REQUEST_TIMEOUT, minimum=5.0)
    api_base = _env_value("CARTOSKY_NWS_HAZARDS_API_BASE", default=NWS_API_BASE).strip() or NWS_API_BASE
    return NWSHazardsPollerConfig(
        data_root=data_root,
        county_reference_path=county_reference,
        poll_seconds=poll_seconds,
        keep_runs=keep_runs,
        timeout_seconds=timeout_seconds,
        api_base=api_base,
    )


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CartoSky NWS Hazards poller.")
    parser.add_argument("--once", action="store_true", help="Run one NWS Hazards poll cycle and exit.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    config = build_config_from_env()
    return run_poller(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())