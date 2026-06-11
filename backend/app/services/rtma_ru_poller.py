from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.models.rtma_ru import CURRENT_ANALYSIS_MODEL
from app.services.builder.cog_writer import warp_to_target_grid
from app.services.builder.fetch import (
    HerbieTransientUnavailableError,
    convert_units,
    fetch_variable,
    new_bundle_fetch_cache,
    product_hour_has_any_idx,
)
from app.services.rtma_ru_publish import (
    CurrentAnalysisBundleFrame,
    CurrentAnalysisPublishResult,
    CurrentAnalysisPublishedFrame,
    load_latest_published_current_analysis_frames,
    publish_current_analysis_bundle,
)
from app.services.observed_bundle_health import parse_iso_datetime
from app.services.publish_utils import enforce_herbie_cache_retention, enforce_run_artifact_retention

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_CACHE_DIR = Path("/opt/cartosky/herbie_cache/current_analysis")
DEFAULT_PRODUCT = "anl"
DEFAULT_POLL_SECONDS = 300
DEFAULT_KEEP_RUNS = 6
DEFAULT_WINDOW_MINUTES = 120
DEFAULT_FRAME_CADENCE_MINUTES = 15
DEFAULT_LOOKBACK_MINUTES = 360
DEFAULT_ALLOW_GRIB_WITHOUT_IDX = False

CURRENT_ANALYSIS_MODEL_ID = "current_analysis"
CURRENT_ANALYSIS_REGION_ID = "conus"
CURRENT_ANALYSIS_HERBIE_MODEL = "rtma_ru"
CURRENT_ANALYSIS_FH = 0

CURRENT_ANALYSIS_DIRECT_PATTERNS: dict[str, str] = {
    "tmp2m": ":TMP:2 m above ground:",
    "dp2m": ":DPT:2 m above ground:",
    "wgst10m": ":GUST:10 m above ground:",
    "spres": ":PRES:surface:",
}
CURRENT_ANALYSIS_WIND_COMPONENT_PATTERNS = {
    "u": ":UGRD:10 m above ground:",
    "v": ":VGRD:10 m above ground:",
}


@dataclass(frozen=True)
class CurrentAnalysisPollerConfig:
    data_root: Path
    cache_dir: Path
    product: str
    poll_seconds: int
    keep_runs: int
    window_minutes: int
    frame_cadence_minutes: int
    lookback_minutes: int
    allow_grib_without_idx: bool
    source_priority: tuple[str, ...]


@dataclass(frozen=True)
class CurrentAnalysisPollerCycleResult:
    action: str
    latest_scan_valid_time: str | None
    published_run_id: str | None
    expected_frame_count: int
    decoded_frame_count: int
    failed_scan_count: int
    message: str


def run_once(config: CurrentAnalysisPollerConfig) -> CurrentAnalysisPollerCycleResult:
    target_frame_count = compute_target_frame_count(
        window_minutes=config.window_minutes,
        frame_cadence_minutes=config.frame_cadence_minutes,
    )
    discovered = discover_recent_run_times(
        config,
        limit=max(target_frame_count * 3, target_frame_count),
    )
    frozen = freeze_bundle_run_times(
        discovered,
        max_frames=target_frame_count,
        frame_cadence_minutes=config.frame_cadence_minutes,
    )
    logger.info(
        "Current Analysis bundle candidate discovered=%d frozen=%d target=%d",
        len(discovered),
        len(frozen),
        target_frame_count,
    )
    if not frozen:
        return CurrentAnalysisPollerCycleResult(
            action="noop",
            latest_scan_valid_time=None,
            published_run_id=None,
            expected_frame_count=target_frame_count,
            decoded_frame_count=0,
            failed_scan_count=0,
            message="No RTMA Current Analysis cycles discovered from upstream listing.",
        )

    newest_valid_time = frozen[-1].astimezone(timezone.utc)
    latest_published_valid_time, latest_bundle_complete = _latest_published_bundle_state(config.data_root)
    latest_run_id, previous_frames = load_latest_published_current_analysis_frames(config.data_root)
    previously_published_valid_times = {
        frame.valid_time.astimezone(timezone.utc) for frame in previous_frames
    }
    if (
        latest_published_valid_time is not None
        and newest_valid_time <= latest_published_valid_time
        and latest_bundle_complete
    ):
        return CurrentAnalysisPollerCycleResult(
            action="noop",
            latest_scan_valid_time=_format_iso(newest_valid_time),
            published_run_id=None,
            expected_frame_count=len(frozen),
            decoded_frame_count=0,
            failed_scan_count=0,
            message="No new Current Analysis cycle beyond the current published latest bundle.",
        )

    runs_to_fetch = [run_time for run_time in frozen if run_time.astimezone(timezone.utc) not in previously_published_valid_times]
    logger.info(
        "Current Analysis incremental window previous_run=%s reused=%d fetch=%d",
        latest_run_id or "<none>",
        max(0, len(frozen) - len(runs_to_fetch)),
        len(runs_to_fetch),
    )

    bundle_fetch_cache = new_bundle_fetch_cache()
    frames: list[CurrentAnalysisBundleFrame] = []
    failed_runs: list[str] = []
    for index, run_time in enumerate(runs_to_fetch, start=1):
        logger.info(
            "Current Analysis frame %d/%d fetching run=%s",
            index,
            len(runs_to_fetch),
            _format_iso(run_time),
        )
        try:
            frame = build_bundle_frame(run_time=run_time, config=config, bundle_fetch_cache=bundle_fetch_cache)
            frames.append(frame)
        except HerbieTransientUnavailableError as exc:
            logger.warning("Skipping Current Analysis cycle %s after transient fetch failure: %s", _format_iso(run_time), exc)
            failed_runs.append(_format_iso(run_time) or str(run_time))
        except Exception as exc:
            logger.warning("Skipping Current Analysis cycle %s after fetch/decode failure: %s", _format_iso(run_time), exc)
            failed_runs.append(_format_iso(run_time) or str(run_time))

    available_valid_times = {frame.valid_time.astimezone(timezone.utc) for frame in previous_frames}
    available_valid_times.update(frame.valid_time.astimezone(timezone.utc) for frame in frames)
    frozen_valid_times = {run_time.astimezone(timezone.utc) for run_time in frozen}
    available_for_window = len(available_valid_times.intersection(frozen_valid_times))
    if not frames and available_for_window == 0:
        return CurrentAnalysisPollerCycleResult(
            action="noop",
            latest_scan_valid_time=_format_iso(newest_valid_time),
            published_run_id=None,
            expected_frame_count=len(frozen),
            decoded_frame_count=0,
            failed_scan_count=len(failed_runs),
            message="No publishable Current Analysis bundle could be built from the frozen cycle window.",
        )

    publish_result = publish_current_analysis_bundle(
        data_root=config.data_root,
        frames=frames,
        publish_time=datetime.now(timezone.utc),
        previous_frames=previous_frames,
        target_frame_count=len(frozen),
        expected_frame_count=len(frozen),
    )
    _enforce_retention(config)

    message = (
        f"Published Current Analysis bundle {publish_result.run_id} "
        f"with {available_for_window}/{len(frozen)} frames"
    )
    if failed_runs:
        message += f" ({len(failed_runs)} failed cycles skipped)"
    return CurrentAnalysisPollerCycleResult(
        action="published",
        latest_scan_valid_time=_format_iso(newest_valid_time),
        published_run_id=publish_result.run_id,
        expected_frame_count=len(frozen),
        decoded_frame_count=available_for_window,
        failed_scan_count=len(failed_runs),
        message=message,
    )


def build_bundle_frame(
    *,
    run_time: datetime,
    config: CurrentAnalysisPollerConfig,
    bundle_fetch_cache: Any,
) -> CurrentAnalysisBundleFrame:
    values_by_var: dict[str, np.ndarray] = {}
    source_metadata_by_var: dict[str, dict[str, Any]] = {}
    frame_transform = None

    for var_id, pattern in CURRENT_ANALYSIS_DIRECT_PATTERNS.items():
        warped, warped_transform, meta = _fetch_direct_variable(
            run_time=run_time,
            config=config,
            var_id=var_id,
            search_pattern=pattern,
            bundle_fetch_cache=bundle_fetch_cache,
        )
        values_by_var[var_id] = warped
        source_metadata_by_var[var_id] = meta
        if warped_transform is not None:
            if frame_transform is None:
                frame_transform = warped_transform
            elif warped_transform != frame_transform:
                raise RuntimeError("Current Analysis direct variables returned mismatched target grids")

    wspd_values, wspd_transform, wspd_meta = _fetch_derived_wind_speed(
        run_time=run_time,
        config=config,
        bundle_fetch_cache=bundle_fetch_cache,
    )
    values_by_var["wspd10m"] = wspd_values
    source_metadata_by_var["wspd10m"] = wspd_meta
    if wspd_transform is not None:
        if frame_transform is None:
            frame_transform = wspd_transform
        elif wspd_transform != frame_transform:
            raise RuntimeError("Current Analysis wind speed returned a mismatched target grid")

    return CurrentAnalysisBundleFrame(
        valid_time=run_time.astimezone(timezone.utc),
        values_by_var=values_by_var,
        transform=frame_transform,
        projection="EPSG:3857",
        source_metadata={
            "source_model": CURRENT_ANALYSIS_HERBIE_MODEL,
            "source_product": config.product,
            "source_valid_time": _format_iso(run_time),
        },
        source_metadata_by_var=source_metadata_by_var,
    )


def discover_recent_run_times(
    config: CurrentAnalysisPollerConfig,
    *,
    limit: int,
    now_utc: datetime | None = None,
) -> list[datetime]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cadence = max(1, int(config.frame_cadence_minutes))
    max_frames = max(1, int(limit))
    max_steps = max(max_frames, (max(1, int(config.lookback_minutes)) // cadence) + 1)
    candidate = _floor_to_cadence(now, cadence)
    discovered: list[datetime] = []
    for step in range(max_steps):
        run_time = candidate - timedelta(minutes=step * cadence)
        if product_hour_has_any_idx(
            model_id=CURRENT_ANALYSIS_HERBIE_MODEL,
            product=config.product,
            run_date=run_time,
            fh=CURRENT_ANALYSIS_FH,
            herbie_kwargs=_herbie_kwargs(config),
            allow_grib_without_idx=config.allow_grib_without_idx,
        ):
            discovered.append(run_time)
            if len(discovered) >= max_frames:
                break
    discovered.sort()
    return discovered


def freeze_bundle_run_times(
    run_times: list[datetime],
    *,
    max_frames: int,
    frame_cadence_minutes: int,
) -> list[datetime]:
    del frame_cadence_minutes
    unique = sorted({run_time.astimezone(timezone.utc) for run_time in run_times})
    if max_frames > 0:
        unique = unique[-int(max_frames):]
    return unique


def compute_target_frame_count(*, window_minutes: int, frame_cadence_minutes: int) -> int:
    safe_window = max(1, int(window_minutes))
    safe_cadence = max(1, int(frame_cadence_minutes))
    return max(1, (safe_window // safe_cadence) + 1)


def run_poller(config: CurrentAnalysisPollerConfig, *, once: bool) -> int:
    logger.info(
        "Current Analysis poller starting data_root=%s cache_dir=%s product=%s poll=%ss keep_runs=%d window=%dm cadence=%dm lookback=%dm priorities=%s",
        config.data_root,
        config.cache_dir,
        config.product,
        config.poll_seconds,
        config.keep_runs,
        config.window_minutes,
        config.frame_cadence_minutes,
        config.lookback_minutes,
        ",".join(config.source_priority),
    )
    while True:
        try:
            result = run_once(config)
            logger.info("Current Analysis poller cycle action=%s message=%s", result.action, result.message)
        except Exception:
            logger.exception("Current Analysis poller cycle failed")
        if once:
            return 0
        time.sleep(max(15, int(config.poll_seconds)))


def _fetch_direct_variable(
    *,
    run_time: datetime,
    config: CurrentAnalysisPollerConfig,
    var_id: str,
    search_pattern: str,
    bundle_fetch_cache: Any,
) -> tuple[np.ndarray, Any, dict[str, Any]]:
    capability = CURRENT_ANALYSIS_MODEL.get_var_capability(var_id)
    if capability is None:
        raise KeyError(f"Unknown Current Analysis variable: {var_id}")
    data, crs, transform, meta = fetch_variable(
        model_id=CURRENT_ANALYSIS_HERBIE_MODEL,
        product=config.product,
        search_pattern=search_pattern,
        run_date=run_time,
        fh=CURRENT_ANALYSIS_FH,
        herbie_kwargs=_herbie_kwargs(config),
        bundle_fetch_cache=bundle_fetch_cache,
        return_meta=True,
    )
    converted = convert_units(data, var_id, model_id=CURRENT_ANALYSIS_MODEL_ID, var_capability=capability)
    warped, dst_transform = warp_to_target_grid(
        converted,
        crs,
        transform,
        model=CURRENT_ANALYSIS_MODEL_ID,
        region=CURRENT_ANALYSIS_REGION_ID,
        resampling="bilinear",
    )
    return warped, dst_transform, {
        "inventory_line": str(meta.get("inventory_line") or ""),
        "search_pattern": str(meta.get("search_pattern") or search_pattern),
        "source_product": str(meta.get("product") or config.product),
    }


def _fetch_derived_wind_speed(
    *,
    run_time: datetime,
    config: CurrentAnalysisPollerConfig,
    bundle_fetch_cache: Any,
) -> tuple[np.ndarray, Any, dict[str, Any]]:
    u_data, u_crs, u_transform, u_meta = fetch_variable(
        model_id=CURRENT_ANALYSIS_HERBIE_MODEL,
        product=config.product,
        search_pattern=CURRENT_ANALYSIS_WIND_COMPONENT_PATTERNS["u"],
        run_date=run_time,
        fh=CURRENT_ANALYSIS_FH,
        herbie_kwargs=_herbie_kwargs(config),
        bundle_fetch_cache=bundle_fetch_cache,
        return_meta=True,
    )
    v_data, v_crs, v_transform, v_meta = fetch_variable(
        model_id=CURRENT_ANALYSIS_HERBIE_MODEL,
        product=config.product,
        search_pattern=CURRENT_ANALYSIS_WIND_COMPONENT_PATTERNS["v"],
        run_date=run_time,
        fh=CURRENT_ANALYSIS_FH,
        herbie_kwargs=_herbie_kwargs(config),
        bundle_fetch_cache=bundle_fetch_cache,
        return_meta=True,
    )
    if np.asarray(u_data).shape != np.asarray(v_data).shape:
        raise RuntimeError("Current Analysis wind components returned mismatched shapes")
    if str(u_crs) != str(v_crs) or u_transform != v_transform:
        raise RuntimeError("Current Analysis wind components returned mismatched source grids")
    native_speed = np.hypot(np.asarray(u_data, dtype=np.float32), np.asarray(v_data, dtype=np.float32)).astype(np.float32)
    capability = CURRENT_ANALYSIS_MODEL.get_var_capability("wspd10m")
    converted_speed = convert_units(
        native_speed,
        "wspd10m",
        model_id=CURRENT_ANALYSIS_MODEL_ID,
        var_capability=capability,
    )
    warped, dst_transform = warp_to_target_grid(
        converted_speed,
        u_crs,
        u_transform,
        model=CURRENT_ANALYSIS_MODEL_ID,
        region=CURRENT_ANALYSIS_REGION_ID,
        resampling="bilinear",
    )
    return warped, dst_transform, {
        "inventory_lines": [
            str(u_meta.get("inventory_line") or ""),
            str(v_meta.get("inventory_line") or ""),
        ],
        "search_pattern": "derive:wspd10m",
        "source_product": config.product,
    }


def _latest_published_bundle_state(data_root: Path) -> tuple[datetime | None, bool]:
    latest_path = data_root / "published" / CURRENT_ANALYSIS_MODEL_ID / "LATEST.json"
    if not latest_path.is_file():
        return None, False
    try:
        latest_payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, False
    run_id = str(latest_payload.get("run_id") or "").strip()
    manifest_path = data_root / "manifests" / CURRENT_ANALYSIS_MODEL_ID / f"{run_id}.json"
    if not manifest_path.is_file():
        return None, False
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
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


def _enforce_retention(config: CurrentAnalysisPollerConfig) -> None:
    enforce_run_artifact_retention(config.data_root / "staging" / CURRENT_ANALYSIS_MODEL_ID, config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "published" / CURRENT_ANALYSIS_MODEL_ID, config.keep_runs)
    enforce_run_artifact_retention(config.data_root / "manifests" / CURRENT_ANALYSIS_MODEL_ID, config.keep_runs)
    enforce_herbie_cache_retention(config.cache_dir, CURRENT_ANALYSIS_HERBIE_MODEL, config.keep_runs)


def _floor_to_cadence(value: datetime, cadence_minutes: int) -> datetime:
    safe_cadence = max(1, int(cadence_minutes))
    value_utc = value.astimezone(timezone.utc)
    floored_minute = value_utc.minute - (value_utc.minute % safe_cadence)
    return value_utc.replace(minute=floored_minute, second=0, microsecond=0)


def _herbie_kwargs(config: CurrentAnalysisPollerConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "save_dir": str(config.cache_dir),
    }
    if config.source_priority:
        kwargs["priority"] = list(config.source_priority)
    return kwargs


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


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def build_config(args: argparse.Namespace) -> CurrentAnalysisPollerConfig:
    raw_priorities = _env_value("CARTOSKY_CURRENT_ANALYSIS_SOURCE_PRIORITY", "aws,nomads")
    priorities = tuple(item.strip().lower() for item in raw_priorities.split(",") if item.strip())
    return CurrentAnalysisPollerConfig(
        data_root=Path(args.data_root).expanduser().resolve() if args.data_root else Path(_env_value("CARTOSKY_DATA_ROOT", str(DEFAULT_DATA_ROOT))).expanduser().resolve(),
        cache_dir=Path(args.cache_dir).expanduser().resolve() if args.cache_dir else Path(_env_value("CARTOSKY_CURRENT_ANALYSIS_CACHE_DIR", str(DEFAULT_CACHE_DIR))).expanduser().resolve(),
        product=_env_value("CARTOSKY_CURRENT_ANALYSIS_PRODUCT", DEFAULT_PRODUCT) or DEFAULT_PRODUCT,
        poll_seconds=_int_env("CARTOSKY_CURRENT_ANALYSIS_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=15),
        keep_runs=_int_env("CARTOSKY_CURRENT_ANALYSIS_KEEP_RUNS", DEFAULT_KEEP_RUNS, minimum=1),
        window_minutes=_int_env("CARTOSKY_CURRENT_ANALYSIS_WINDOW_MINUTES", DEFAULT_WINDOW_MINUTES, minimum=1),
        frame_cadence_minutes=_int_env("CARTOSKY_CURRENT_ANALYSIS_FRAME_CADENCE_MINUTES", DEFAULT_FRAME_CADENCE_MINUTES, minimum=1),
        lookback_minutes=_int_env("CARTOSKY_CURRENT_ANALYSIS_LOOKBACK_MINUTES", DEFAULT_LOOKBACK_MINUTES, minimum=15),
        allow_grib_without_idx=_bool_env("CARTOSKY_CURRENT_ANALYSIS_ALLOW_GRIB_WITHOUT_IDX", DEFAULT_ALLOW_GRIB_WITHOUT_IDX),
        source_priority=priorities or ("aws", "nomads"),
    )


def _format_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Poll and publish Current Analysis RTMA bundles")
    parser.add_argument("--once", action="store_true", help="Run one poll/publish cycle then exit")
    parser.add_argument("--data-root", default=None, help="Override CARTOSKY_DATA_ROOT")
    parser.add_argument("--cache-dir", default=None, help="Override CARTOSKY_CURRENT_ANALYSIS_CACHE_DIR")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run_poller(build_config(args), once=bool(args.once))


if __name__ == "__main__":
    raise SystemExit(main())