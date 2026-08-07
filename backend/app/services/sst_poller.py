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
from typing import Any

from app.services import sst_fetch
from app.services.publish_utils import enforce_run_artifact_retention
from app.services.sst_publish import (
    SST_ANOM_VARIABLE_ID,
    SST_BUNDLE_FRAME_COUNT,
    SST_SUPPLEMENTAL_VARIABLES,
    SST_TREND_VARIABLE_ID,
    SST_CANONICAL_REGION_ID,
    SST_VARIABLE_ID,
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
    # A caught-up SST is NOT on its own a noop: a supplemental variable may still
    # be pending for days whose SST already shipped, which is the normal state for
    # the newest day or two. This is only a probe for "is there any work"; the
    # authoritative catch-up lists are recomputed after pass 1, against fresh state.
    supplemental_work_pending = any(
        _supplemental_catch_up_days(
            var_id,
            data_root=config.data_root,
            latest_available=latest_available,
            bundle_frame_count=config.bundle_frame_count,
            exclude={},
        )
        for var_id in SST_SUPPLEMENTAL_VARIABLES
    )
    if not domains_needing_day and not supplemental_work_pending:
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
    #: var_id -> pending day tokens, for the cycle message.
    supplemental_skipped: dict[str, list[str]] = {}
    download_dir = Path(tempfile.mkdtemp(prefix="cartosky-sst-"))
    try:
        # ── Pass 1: advance the absolute SST, oldest first. This is the layer's
        # reason to exist and is never gated on any supplemental. Each day also
        # picks up every supplemental opportunistically when upstream already has
        # it, so the common case costs one publish per day.
        #
        # Per (VARIABLE, DOMAIN), not a flat set of days: pass 1 publishes a day to
        # only the domains that needed its SST, so "na already got this day's
        # anomaly" says nothing about global — and nothing at all about the trend.
        supplemental_done: dict[str, dict[str, set[datetime]]] = {
            var_id: {} for var_id in SST_SUPPLEMENTAL_VARIABLES
        }
        # Per variable but domain-INDEPENDENT, because it records an upstream fact
        # ("CRW has not published this day for this product"), not a per-domain
        # publish state. Lets pass 2 skip re-probing a day pass 1 already found
        # missing, so each pending (variable, day) costs at most one HEAD per cycle.
        probe_missed: dict[str, set[datetime]] = {
            var_id: set() for var_id in SST_SUPPLEMENTAL_VARIABLES
        }
        for day in sorted(domains_needing_day):
            day_domains = domains_needing_day[day]
            try:
                frame = _build_frame_for_day(
                    day,
                    download_dir=download_dir,
                    timeout=timeout,
                    probe_timeout=probe_timeout,
                    with_supplementals=SST_SUPPLEMENTAL_VARIABLES,
                )
            except sst_fetch.SSTFetchError as exc:
                logger.warning("SST day %s unavailable; skipping: %s", day.date(), exc)
                skipped.append(day.strftime("%Y%m%d"))
                continue
            except Exception:
                logger.exception("SST day %s failed to read; skipping", day.date())
                skipped.append(day.strftime("%Y%m%d"))
                continue
            for var_id in SST_SUPPLEMENTAL_VARIABLES:
                if frame.field_for(var_id) is not None:
                    for domain in day_domains:
                        supplemental_done[var_id].setdefault(domain, set()).add(day)
                else:
                    probe_missed[var_id].add(day)
                    supplemental_skipped.setdefault(var_id, []).append(day.strftime("%Y%m%d"))
            run_id = _publish_day(
                config=config, frame=frame, day_domains=day_domains, run_valid_time=None
            )
            if run_id is not None:
                published_run_ids.append(run_id)

        # ── Pass 2: catch-up for days whose SST is already published but whose
        # supplemental was missing at the time (each CRW product runs on its own
        # schedule). These frames carry ONLY the supplemental, so the day's
        # absolute SST is neither re-downloaded nor re-encoded — it is carried
        # forward by hardlink. They amend the newest published run rather than
        # minting a run id that trails the window.
        for var_id in SST_SUPPLEMENTAL_VARIABLES:
            catch_up = _supplemental_catch_up_days(
                var_id,
                data_root=config.data_root,
                latest_available=latest_available,
                bundle_frame_count=config.bundle_frame_count,
                exclude=supplemental_done[var_id],
            )
            for run_valid_time, days_by_domain in sorted(catch_up.items()):
                catch_up_frames: list[SSTBundleFrame] = []
                catch_up_domains: tuple[str, ...] = ()
                for day, day_domains in sorted(days_by_domain.items()):
                    if day in probe_missed[var_id]:
                        # Already HEAD-probed this cycle and upstream did not have
                        # it. Re-probing cannot change the answer within one cycle.
                        continue
                    try:
                        supplemental_frame = _build_supplemental_frame_for_day(
                            var_id,
                            day,
                            download_dir=download_dir,
                            timeout=timeout,
                            probe_timeout=probe_timeout,
                        )
                    except sst_fetch.SSTFetchError as exc:
                        logger.info(
                            "SST %s for %s not available yet; retrying next cycle: %s",
                            var_id,
                            day.date(),
                            exc,
                        )
                        supplemental_skipped.setdefault(var_id, []).append(
                            day.strftime("%Y%m%d")
                        )
                        continue
                    except Exception:
                        logger.exception(
                            "SST %s %s failed to read; skipping", var_id, day.date()
                        )
                        supplemental_skipped.setdefault(var_id, []).append(
                            day.strftime("%Y%m%d")
                        )
                        continue
                    if supplemental_frame is None:
                        supplemental_skipped.setdefault(var_id, []).append(
                            day.strftime("%Y%m%d")
                        )
                        continue
                    catch_up_frames.append(supplemental_frame)
                    catch_up_domains = tuple(dict.fromkeys(catch_up_domains + day_domains))
                if not catch_up_frames:
                    continue
                run_id = _publish_day(
                    config=config,
                    frame=catch_up_frames,
                    day_domains=catch_up_domains,
                    run_valid_time=run_valid_time,
                )
                if run_id is not None and run_id not in published_run_ids:
                    published_run_ids.append(run_id)
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)

    anomaly_note = _supplemental_pending_note(supplemental_skipped)
    if not published_run_ids:
        if skipped:
            return SSTPollerCycleResult(
                action="fetch_miss",
                published_run_ids=(),
                latest_available_day=latest_available.strftime(_TIME_FORMAT),
                message=(
                    f"No SST day could be fetched this cycle (skipped {','.join(skipped)}); "
                    f"keeping last known good bundle.{anomaly_note}"
                ),
            )
        # SST is fully caught up and only the anomaly is outstanding. That is the
        # normal steady state for the newest day or two, so it reads as a noop
        # rather than a fetch failure — the pending days are named in the message.
        return SSTPollerCycleResult(
            action="noop",
            published_run_ids=(),
            latest_available_day=latest_available.strftime(_TIME_FORMAT),
            message=(
                f"SST upstream newest day {latest_available.date()} is already published "
                f"in every enabled domain ({_published_state_summary(config.data_root)})."
                + anomaly_note
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
            + anomaly_note
        ),
    )


def _publish_day(
    *,
    config: SSTPollerConfig,
    frame: SSTBundleFrame | list[SSTBundleFrame],
    day_domains: tuple[str, ...],
    run_valid_time: datetime | None,
) -> str | None:
    """Publish one run; return its id, or None when nothing landed on disk."""
    frames = frame if isinstance(frame, list) else [frame]
    result = publish_sst_bundle(
        data_root=config.data_root,
        frames=frames,
        target_frame_count=config.bundle_frame_count,
        domains=day_domains,
        run_valid_time=run_valid_time,
    )
    # Only a run that actually landed in at least one domain counts as published.
    # publish_sst_bundle raises on total failure, so this is belt-and-braces — but
    # reporting a run id with no artifacts behind it is exactly how a
    # green-but-broken cycle looked.
    if not result.frame_counts:
        logger.error(
            "SST publish reported no domains for run=%s (requested %s) — not counting it",
            result.run_id,
            ",".join(day_domains),
        )
        return None
    logger.info(
        "SST published run=%s domains=%s frames=%s",
        result.run_id,
        ",".join(day_domains),
        result.variable_frame_counts,
    )
    return result.run_id


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

def _supplemental_pending_note(skipped_by_var: dict[str, list[str]]) -> str:
    """Human-readable "(x pending …)" tail for the cycle message."""
    parts = [
        f"{var_id} pending {','.join(sorted(set(days)))}"
        for var_id, days in sorted(skipped_by_var.items())
        if days
    ]
    return f" ({'; '.join(parts)})" if parts else ""


#: SSTBundleFrame kwargs carrying each supplemental variable's payload.
_SUPPLEMENTAL_FRAME_KWARGS: dict[str, tuple[str, str, str, str]] = {
    SST_ANOM_VARIABLE_ID: (
        "anomaly_values",
        "anomaly_source_url",
        "anomaly_source_filename",
        "anomaly_metadata",
    ),
    SST_TREND_VARIABLE_ID: (
        "trend_values",
        "trend_source_url",
        "trend_source_filename",
        "trend_metadata",
    ),
}


def _build_frame_for_day(
    day: datetime,
    *,
    download_dir: Path,
    timeout: tuple[float, float],
    probe_timeout: tuple[float, float],
    with_supplementals: tuple[str, ...] = (),
) -> SSTBundleFrame:
    """Fetch a day's absolute SST, plus any supplemental upstream already has.

    A supplemental miss degrades the frame (SST-only, or SST plus whichever
    supplementals were ready) and is never allowed to fail the day: each CRW
    product publishes on its own schedule, so "not out yet" is the normal case for
    the newest day, and the catch-up pass picks it up on a later cycle.
    """
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

    extra: dict[str, Any] = {}
    for var_id in with_supplementals:
        values_kw, url_kw, filename_kw, metadata_kw = _SUPPLEMENTAL_FRAME_KWARGS[var_id]
        try:
            supplemental = _build_supplemental_frame_for_day(
                var_id,
                day,
                download_dir=download_dir,
                timeout=timeout,
                probe_timeout=probe_timeout,
            )
        except sst_fetch.SSTFetchError as exc:
            logger.info(
                "SST %s for %s not available with the SST; will retry: %s",
                var_id,
                day.date(),
                exc,
            )
            continue
        except Exception:
            logger.exception(
                "SST %s %s failed to read; publishing without it", var_id, day.date()
            )
            continue
        if supplemental is None:
            continue
        extra[values_kw] = getattr(supplemental, values_kw)
        extra[url_kw] = getattr(supplemental, url_kw)
        extra[filename_kw] = getattr(supplemental, filename_kw)
        extra[metadata_kw] = dict(getattr(supplemental, metadata_kw))

    return SSTBundleFrame(
        valid_time=source.valid_time,
        values=native.values,
        source_transform=native.transform,
        source_crs=native.crs,
        source_url=source.url,
        source_filename=source.path.name,
        metadata=metadata,
        **extra,
    )


def _build_supplemental_frame_for_day(
    var_id: str,
    day: datetime,
    *,
    download_dir: Path,
    timeout: tuple[float, float],
    probe_timeout: tuple[float, float],
) -> SSTBundleFrame | None:
    """Fetch one day of one supplemental as a supplemental-only frame, or None.

    HEAD-probed first, which is the payoff for making the STAR archive primary:
    asking "is this product published for this day?" costs one header instead of a
    download — 11 MB for the anomaly, 57 MB for the trend.
    """
    if not sst_fetch.probe_supplemental_available(var_id, day, timeout=probe_timeout):
        return None
    source = sst_fetch.fetch_supplemental_day(
        var_id, day, download_dir=download_dir, timeout=timeout
    )
    try:
        native = sst_fetch.read_native_supplemental(var_id, source.path, source.variable)
    finally:
        try:
            source.path.unlink(missing_ok=True)
        except OSError:
            pass
    metadata = dict(native.metadata)
    metadata["upstream_path_label"] = source.label
    values_kw, url_kw, filename_kw, metadata_kw = _SUPPLEMENTAL_FRAME_KWARGS[var_id]
    return SSTBundleFrame(
        valid_time=source.valid_time,
        source_transform=native.transform,
        source_crs=native.crs,
        **{
            values_kw: native.values,
            url_kw: source.url,
            filename_kw: source.path.name,
            metadata_kw: metadata,
        },
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


def _supplemental_catch_up_days(
    var_id: str,
    *,
    data_root: Path,
    latest_available: datetime,
    bundle_frame_count: int,
    exclude: dict[str, set[datetime]],
) -> dict[datetime, dict[datetime, tuple[str, ...]]]:
    """Days whose SST is published but whose ``var_id`` is not, per domain.

    Returns ``{run_valid_time: {day: domains}}``. ``run_valid_time`` is the newest
    published SST day, i.e. the run these anomaly frames should *amend* — passing
    it explicitly is what keeps an older catch-up day from minting a run id that
    trails the window's own newest frame.

    ``exclude`` is ``{domain: days}`` — days this cycle already published this
    variable for, **per domain**. It must stay per-domain: pass 1 publishes a day
    only to the domains that needed its SST, so excluding a day globally because
    one domain picked it up leaves the others a cycle behind. The caller keeps one
    such map per supplemental variable, so the anomaly's progress never masks the
    trend's.

    Bounded to the bundle window, so a cycle never re-fetches the same anomaly
    twice and never queues unbounded work.
    """
    canonical_newest = newest_published_valid_time(data_root, SST_CANONICAL_REGION_ID)
    if canonical_newest is None:
        return {}

    window_floor = sst_fetch.normalize_day(canonical_newest) - timedelta(
        days=max(1, int(bundle_frame_count)) - 1
    )
    newest_upstream = sst_fetch.normalize_day(latest_available)
    needed: dict[datetime, list[str]] = {}
    for domain in publish_domains():
        already_done = exclude.get(domain, frozenset())
        sst_days = published_valid_times(data_root, domain, SST_VARIABLE_ID)
        have_days = published_valid_times(data_root, domain, var_id)
        for day in sorted(sst_days - have_days):
            if day in already_done or day < window_floor or day > newest_upstream:
                continue
            needed.setdefault(day, []).append(domain)
    if not needed:
        return {}
    return {
        sst_fetch.normalize_day(canonical_newest): {
            day: tuple(domains) for day, domains in needed.items()
        }
    }


def published_valid_times(data_root: Path, domain: str, var_id: str) -> set[datetime]:
    """Frame ``valid_time`` values published for one variable in the LATEST run."""
    manifest = _latest_manifest(data_root, domain)
    if manifest is None:
        return set()
    var_entry = manifest.get("variables", {}).get(var_id)
    frames = var_entry.get("frames") if isinstance(var_entry, dict) else None
    if not isinstance(frames, list):
        return set()
    out: set[datetime] = set()
    for frame in frames:
        raw = frame.get("valid_time") if isinstance(frame, dict) else None
        parsed = _parse_manifest_time(raw)
        if parsed is not None:
            out.add(parsed)
    return out


def _parse_manifest_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.strptime(raw.strip(), _TIME_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _latest_manifest(data_root: Path, domain: str) -> dict | None:
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
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def newest_published_valid_time(
    data_root: Path, domain: str, var_id: str = SST_VARIABLE_ID
) -> datetime | None:
    """Newest frame ``valid_time`` published for a variable in the LATEST run.

    Defaults to the absolute SST: the cycle decision is driven by that variable,
    never by the anomaly, so a lagging anomaly can never hold SST back.
    """
    valid_times = published_valid_times(data_root, domain, var_id)
    return max(valid_times) if valid_times else None


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
