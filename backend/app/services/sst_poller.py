"""SST poller — one daily Geo-Polar Blended frame per cycle, both domains.

Cycle shape:

1. **Cheap probe** (:func:`sst_fetch.probe_latest_available_day`) — a few
   hundred bytes off the ERDDAP time axis. Never downloads ~104 MB just to ask
   whether a new day exists.
2. Compare against the newest published ``valid_time`` of **every enabled
   domain**, not just the canonical one. Nothing missing anywhere -> ``noop``.
   Per-domain is what makes a later ``CARTOSKY_GLOBAL_DOMAIN_MODELS=sst`` flip
   self-healing: the new domain has no published run while the canonical one is
   caught up, so the next cycle backfills it to the same window instead of
   reporting ``noop`` forever (and each day is published only to the domains
   that actually need it, so nothing already-correct gets rewritten).
3. Fetch each missing day **oldest first**, and publish one run per day. Daily
   granularity is what makes the first-ever start cheap in memory: a first run
   walks back up to :data:`app.services.sst_publish.SST_BUNDLE_FRAME_COUNT`
   days and publishes them one at a time, each run hardlinking the previous
   run's frames, so only one native 5 km array is resident at a time. Days the
   upstream is missing are skipped, not fatal.
4. Enforce run retention for every published domain.

A fetch miss never breaks the loop — it logs and retries next cycle.

Run as ``python -m app.services.sst_poller`` (``--once`` for a single cycle).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services import sst_fetch
from app.services.publish_utils import enforce_run_artifact_retention
from app.services.sst_publish import (
    SST_BUNDLE_FRAME_COUNT,
    SSTBundleFrame,
    latest_pointer_path,
    manifest_path,
    manifest_root,
    published_model_root,
    publish_domains,
    publish_sst_bundle,
    staging_model_root,
)

logger = logging.getLogger(__name__)

DEFAULT_DATA_ROOT = Path("/opt/cartosky/data")
DEFAULT_POLL_SECONDS = 3_600
DEFAULT_KEEP_RUNS = 6
DEFAULT_TIMEOUT_SECONDS = 600.0
MIN_POLL_SECONDS = 300

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class SSTPollerConfig:
    data_root: Path
    poll_seconds: int
    keep_runs: int
    timeout_seconds: float
    bundle_frame_count: int = SST_BUNDLE_FRAME_COUNT


@dataclass(frozen=True)
class SSTPollerCycleResult:
    action: str
    published_run_ids: tuple[str, ...]
    latest_available_day: str | None
    message: str


def run_once(config: SSTPollerConfig) -> SSTPollerCycleResult:
    timeout = (10.0, float(config.timeout_seconds))
    probe_timeout = (10.0, min(60.0, float(config.timeout_seconds)))

    latest_available = sst_fetch.probe_latest_available_day(timeout=probe_timeout)
    if latest_available is None:
        return SSTPollerCycleResult(
            action="probe_miss",
            published_run_ids=(),
            latest_available_day=None,
            message="No SST availability probe answered; keeping last known good bundle.",
        )

    domains_needing_day = _domains_needing_days(
        data_root=config.data_root,
        latest_available=latest_available,
        bundle_frame_count=config.bundle_frame_count,
    )
    if not domains_needing_day:
        return SSTPollerCycleResult(
            action="noop",
            published_run_ids=(),
            latest_available_day=latest_available.strftime(_TIME_FORMAT),
            message=(
                f"SST upstream newest day {latest_available.date()} is already published "
                f"in every enabled domain ({_published_state_summary(config.data_root)})."
            ),
        )

    published_run_ids: list[str] = []
    skipped: list[str] = []
    download_dir = Path(tempfile.mkdtemp(prefix="cartosky-sst-"))
    try:
        # Oldest first: publish_sst_bundle derives each run id from its newest
        # frame and grows the window off the previous run.
        for day in sorted(domains_needing_day):
            day_domains = domains_needing_day[day]
            try:
                frame = _build_frame_for_day(day, download_dir=download_dir, timeout=timeout)
            except sst_fetch.SSTFetchError as exc:
                logger.warning("SST day %s unavailable; skipping: %s", day.date(), exc)
                skipped.append(day.strftime("%Y%m%d"))
                continue
            except Exception:
                logger.exception("SST day %s failed to read; skipping", day.date())
                skipped.append(day.strftime("%Y%m%d"))
                continue
            result = publish_sst_bundle(
                data_root=config.data_root,
                frames=[frame],
                target_frame_count=config.bundle_frame_count,
                domains=day_domains,
            )
            # Only a run that actually landed in at least one domain counts as
            # published. publish_sst_bundle now raises on total failure, so this
            # is belt-and-braces — but reporting a run id with no artifacts
            # behind it is exactly how a green-but-broken cycle looked.
            if not result.frame_counts:
                logger.error(
                    "SST publish reported no domains for run=%s (requested %s) — not counting it",
                    result.run_id,
                    ",".join(day_domains),
                )
                continue
            published_run_ids.append(result.run_id)
            logger.info(
                "SST published run=%s domains=%s frames=%s",
                result.run_id,
                ",".join(day_domains),
                result.frame_counts,
            )
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)

    if not published_run_ids:
        return SSTPollerCycleResult(
            action="fetch_miss",
            published_run_ids=(),
            latest_available_day=latest_available.strftime(_TIME_FORMAT),
            message=(
                f"No SST day could be fetched this cycle (skipped {','.join(skipped) or '-'}); "
                f"keeping last known good bundle."
            ),
        )

    _enforce_retention(config)
    return SSTPollerCycleResult(
        action="published",
        published_run_ids=tuple(published_run_ids),
        latest_available_day=latest_available.strftime(_TIME_FORMAT),
        message=(
            f"Published {len(published_run_ids)} SST run(s): {','.join(published_run_ids)}"
            + (f" (skipped {','.join(skipped)})" if skipped else "")
        ),
    )


def run_poller(config: SSTPollerConfig, *, once: bool) -> int:
    logger.info(
        "SST poller starting data_root=%s poll=%ss keep_runs=%d timeout=%ss bundle_frames=%d domains=%s",
        config.data_root,
        config.poll_seconds,
        config.keep_runs,
        config.timeout_seconds,
        config.bundle_frame_count,
        ",".join(publish_domains()),
    )
    while True:
        cycle_failed = False
        try:
            result = run_once(config)
            logger.info("SST cycle result action=%s message=%s", result.action, result.message)
        except Exception:
            logger.exception("SST poller cycle failed")
            cycle_failed = True
        if once:
            # --once is an operator smoke test: a raised cycle must be visible in
            # the exit status. probe_miss/fetch_miss stay 0 — they are documented
            # healthy outcomes that preserve the last known good bundle.
            return 1 if cycle_failed else 0
        time.sleep(max(MIN_POLL_SECONDS, int(config.poll_seconds)))


# ---------------------------------------------------------------------------
# Cycle helpers
# ---------------------------------------------------------------------------

def _build_frame_for_day(
    day: datetime, *, download_dir: Path, timeout: tuple[float, float]
) -> SSTBundleFrame:
    source = sst_fetch.fetch_sst_day(day, download_dir=download_dir, timeout=timeout)
    try:
        native = sst_fetch.read_native_sst(source.path, source.variable)
    finally:
        try:
            source.path.unlink(missing_ok=True)
        except OSError:
            pass
    metadata = dict(native.metadata)
    metadata["upstream_path_label"] = source.label
    return SSTBundleFrame(
        valid_time=source.valid_time,
        values=native.values,
        source_transform=native.transform,
        source_crs=native.crs,
        source_url=source.url,
        source_filename=source.path.name,
        metadata=metadata,
    )


def _missing_days(
    *,
    latest_available: datetime,
    newest_published: datetime | None,
    bundle_frame_count: int,
) -> list[datetime]:
    """Days to fetch this cycle, oldest first.

    Nothing published yet -> walk back ``bundle_frame_count`` days from the
    newest available day (the first-ever backfill). Otherwise every day after
    the newest published one, capped at the bundle size so a long outage cannot
    queue an unbounded backfill in a single cycle.
    """
    newest_day = sst_fetch.normalize_day(latest_available)
    span = max(1, int(bundle_frame_count))
    if newest_published is None:
        return [newest_day - timedelta(days=offset) for offset in range(span - 1, -1, -1)]

    published_day = sst_fetch.normalize_day(newest_published)
    if published_day >= newest_day:
        return []
    gap_days = int((newest_day - published_day).days)
    count = min(span, gap_days)
    return [newest_day - timedelta(days=offset) for offset in range(count - 1, -1, -1)]


def _domains_needing_days(
    *,
    data_root: Path,
    latest_available: datetime,
    bundle_frame_count: int,
) -> dict[datetime, tuple[str, ...]]:
    """``{day: domains that still need it}`` across **every enabled domain**.

    Deciding on the canonical domain alone is not enough. A domain switched on
    after SST was already live — the ``CARTOSKY_GLOBAL_DOMAIN_MODELS=sst`` flip
    on a running box — has no published run at all while the canonical domain is
    fully caught up, so a canonical-only check reports ``noop`` forever and the
    new domain never backfills. Asking each domain separately makes the flip
    self-healing on the very next cycle.

    Each day maps to only the domains actually missing it, so a routine
    incremental cycle still publishes both domains for one new day, while a
    fresh-domain backfill rewrites nothing in the domains that are already
    correct. Every per-domain list is bounded to the bundle window, so the union
    is too.
    """
    needed: dict[datetime, list[str]] = {}
    for domain in publish_domains():
        for day in _missing_days(
            latest_available=latest_available,
            newest_published=newest_published_valid_time(data_root, domain),
            bundle_frame_count=bundle_frame_count,
        ):
            needed.setdefault(day, []).append(domain)
    return {day: tuple(domains) for day, domains in needed.items()}


def _published_state_summary(data_root: Path) -> str:
    return ", ".join(
        f"{domain}={newest.date() if (newest := newest_published_valid_time(data_root, domain)) else 'none'}"
        for domain in publish_domains()
    )


def newest_published_valid_time(data_root: Path, domain: str) -> datetime | None:
    """Newest frame ``valid_time`` in the domain's currently published manifest."""
    latest_path = latest_pointer_path(data_root, domain)
    if not latest_path.is_file():
        return None
    try:
        payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return None

    path = manifest_path(data_root, run_id, domain)
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    var_entry = manifest.get("variables", {}).get("sst")
    frames = var_entry.get("frames") if isinstance(var_entry, dict) else None
    if not isinstance(frames, list):
        return None

    newest: datetime | None = None
    for frame in frames:
        raw = frame.get("valid_time") if isinstance(frame, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            parsed = datetime.strptime(raw.strip(), _TIME_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if newest is None or parsed > newest:
            newest = parsed
    return newest


def _enforce_retention(config: SSTPollerConfig) -> None:
    for domain in publish_domains():
        enforce_run_artifact_retention(staging_model_root(config.data_root, domain), config.keep_runs)
        enforce_run_artifact_retention(published_model_root(config.data_root, domain), config.keep_runs)
        enforce_run_artifact_retention(manifest_root(config.data_root, domain), config.keep_runs)


# ---------------------------------------------------------------------------
# Config / entry point
# ---------------------------------------------------------------------------

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


def build_config_from_env() -> SSTPollerConfig:
    data_root = Path(
        _env_value("CARTOSKY_SST_DATA_ROOT", "CARTOSKY_DATA_ROOT", default=str(DEFAULT_DATA_ROOT))
    ).expanduser()
    return SSTPollerConfig(
        data_root=data_root,
        poll_seconds=_int_env("CARTOSKY_SST_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=MIN_POLL_SECONDS),
        keep_runs=_int_env("CARTOSKY_SST_KEEP_RUNS", DEFAULT_KEEP_RUNS, minimum=1),
        timeout_seconds=_float_env("CARTOSKY_SST_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, minimum=30.0),
        bundle_frame_count=_int_env(
            "CARTOSKY_SST_BUNDLE_FRAMES", SST_BUNDLE_FRAME_COUNT, minimum=1
        ),
    )


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CartoSky SST poller.")
    parser.add_argument("--once", action="store_true", help="Run one SST poll cycle and exit.")
    parser.add_argument("--data-root", type=Path, default=None, help="Override CARTOSKY_SST_DATA_ROOT.")
    parser.add_argument(
        "--timeout-seconds", type=float, default=None, help="Override CARTOSKY_SST_TIMEOUT_SECONDS."
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    config = build_config_from_env()
    if args.data_root is not None or args.timeout_seconds is not None:
        config = SSTPollerConfig(
            data_root=args.data_root or config.data_root,
            poll_seconds=config.poll_seconds,
            keep_runs=config.keep_runs,
            timeout_seconds=(
                max(30.0, float(args.timeout_seconds))
                if args.timeout_seconds is not None
                else config.timeout_seconds
            ),
            bundle_frame_count=config.bundle_frame_count,
        )
    return run_poller(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
