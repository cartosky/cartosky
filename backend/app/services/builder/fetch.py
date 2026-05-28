"""GRIB acquisition via Herbie.

Downloads GRIB data for a given model/variable/forecast-hour and returns
the raw numpy array along with its source CRS and affine transform.

Phase 1 scope: single-variable "simple" fetch (e.g. tmp2m, refc).
Phase 2 adds multi-component fetch for derived variables (wspd, radar_ptype).

Usage
-----
    from app.services.builder.fetch import fetch_variable

    data, crs, transform = fetch_variable(
        model_id="hrrr", product="sfc",
        search_pattern=":TMP:2 m above ground:",
        run_date=datetime(2026, 2, 17, 6),
        fh=0,
    )
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from collections import OrderedDict
import hashlib
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, overload

import numpy as np
import rasterio
import rasterio.crs
import rasterio.errors
import rasterio.io
import rasterio.transform
import requests

logger = logging.getLogger(__name__)

DEFAULT_HERBIE_PRIORITY = ["aws", "nomads", "google", "azure", "pando", "pando2"]
ENV_HERBIE_PRIORITY = ("CARTOSKY_HERBIE_PRIORITY", "TWF_HERBIE_PRIORITY")
ENV_HERBIE_RETRIES = ("CARTOSKY_HERBIE_SUBSET_RETRIES", "TWF_HERBIE_SUBSET_RETRIES")
ENV_HERBIE_RETRY_SLEEP = ("CARTOSKY_HERBIE_RETRY_SLEEP_SECONDS", "TWF_HERBIE_RETRY_SLEEP_SECONDS")
ENV_HERBIE_IDX_NEGATIVE_CACHE_INITIAL_TTL = (
    "CARTOSKY_HERBIE_IDX_NEGATIVE_CACHE_INITIAL_TTL_SECONDS",
    "TWF_HERBIE_IDX_NEGATIVE_CACHE_INITIAL_TTL_SECONDS",
)
ENV_HERBIE_IDX_NEGATIVE_CACHE_MAX_TTL = (
    "CARTOSKY_HERBIE_IDX_NEGATIVE_CACHE_MAX_TTL_SECONDS",
    "TWF_HERBIE_IDX_NEGATIVE_CACHE_MAX_TTL_SECONDS",
)
ENV_HERBIE_INVENTORY_CACHE_TTL = (
    "CARTOSKY_HERBIE_INVENTORY_CACHE_TTL_SECONDS",
    "TWF_HERBIE_INVENTORY_CACHE_TTL_SECONDS",
)
ENV_HERBIE_FETCH_CACHE_MAX_ENTRIES = (
    "CARTOSKY_HERBIE_FETCH_CACHE_MAX_ENTRIES",
    "TWF_HERBIE_FETCH_CACHE_MAX_ENTRIES",
)
ENV_HERBIE_FETCH_CACHE_MAX_BYTES = ("CARTOSKY_HERBIE_FETCH_CACHE_MAX_BYTES", "TWF_HERBIE_FETCH_CACHE_MAX_BYTES")
ENV_HERBIE_FETCH_CACHE_MAX_CACHEABLE_BYTES = (
    "CARTOSKY_HERBIE_FETCH_CACHE_MAX_CACHEABLE_BYTES",
    "TWF_HERBIE_FETCH_CACHE_MAX_CACHEABLE_BYTES",
)
ENV_HERBIE_RANGE_FETCH_WORKERS = (
    "CARTOSKY_HERBIE_RANGE_FETCH_WORKERS",
    "TWF_HERBIE_RANGE_FETCH_WORKERS",
)
ENV_EPS_FULL_FILE_CACHE_ENABLE = (
    "CARTOSKY_EPS_FULL_FILE_CACHE_ENABLE",
    "TWF_EPS_FULL_FILE_CACHE_ENABLE",
)
ENV_EPS_FULL_FILE_CACHE_ROOT = (
    "CARTOSKY_EPS_FULL_FILE_CACHE_ROOT",
    "TWF_EPS_FULL_FILE_CACHE_ROOT",
)
ENV_EPS_FULL_FILE_CACHE_MAX_BYTES = (
    "CARTOSKY_EPS_FULL_FILE_CACHE_MAX_BYTES",
    "TWF_EPS_FULL_FILE_CACHE_MAX_BYTES",
)
ENV_EPS_FULL_FILE_CACHE_TTL_SECONDS = (
    "CARTOSKY_EPS_FULL_FILE_CACHE_TTL_SECONDS",
    "TWF_EPS_FULL_FILE_CACHE_TTL_SECONDS",
)
ENV_GRIB_DISK_CACHE_LOCK = (
    "CARTOSKY_GRIB_DISK_CACHE_LOCK",
    "CARTOSKY_V3_GRIB_DISK_CACHE_LOCK",
    "TWF_V3_GRIB_DISK_CACHE_LOCK",
    "TWF_V3_DISK_CACHE_LOCK",
)
DEFAULT_GRIB_DISK_LOCK_TIMEOUT_SECONDS = 8.0
DEFAULT_GRIB_DISK_LOCK_POLL_SECONDS = 0.1
DEFAULT_IDX_NEGATIVE_INITIAL_TTL_SECONDS = 20.0
DEFAULT_IDX_NEGATIVE_MAX_TTL_SECONDS = 90.0
DEFAULT_INVENTORY_CACHE_TTL_SECONDS = 600.0
DEFAULT_FETCH_CACHE_MAX_ENTRIES = 256
DEFAULT_FETCH_CACHE_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_FETCH_CACHE_MAX_CACHEABLE_BYTES = 4 * 1024 * 1024
DEFAULT_RANGE_FETCH_WORKERS = 8
DEFAULT_EPS_FULL_FILE_CACHE_MAX_BYTES = 200 * 1024 * 1024 * 1024
DEFAULT_EPS_FULL_FILE_CACHE_TTL_SECONDS = 2 * 60 * 60
DEFAULT_EPS_FULL_FILE_CACHE_CLEANUP_INTERVAL_SECONDS = 60.0
_GRIB_DISK_CACHE_LOCK_WAITS = 0
_EPS_FULL_FILE_CACHE_CLEANUP_LOCK = threading.Lock()
_EPS_FULL_FILE_CACHE_LAST_CLEANUP_TS = 0.0

_MISSING_VALUE_TAG_KEYS = (
    "missing_value",
    "_FillValue",
    "GRIB_missingValue",
    "GRIB_NODATA",
    "GRIB_noDataValue",
    "NODATA",
)

_INVENTORY_SEARCH_COLUMNS = (
    "search_this",
    "line",
    "inventory_line",
    "grib_message",
    "message",
)


class HerbieTransientUnavailableError(RuntimeError):
    """Raised when all Herbie attempts fail due to transient source/index availability."""


class _InvalidGribSubsetError(RuntimeError):
    """Raised when an upstream byte-range response is not a GRIB payload."""


@dataclass
class _IdxNegativeCacheEntry:
    expires_at: float
    ttl_seconds: float
    updated_at: float


@dataclass
class _InventoryCacheEntry:
    data: Any
    expires_at: float
    updated_at: float


@dataclass
class _TimerAggregate:
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0


@dataclass
class _InventorySearchResult:
    inventory: Any | None
    reason: str
    idx_key: str = ""


@dataclass
class _RangeFetchInflight:
    event: threading.Event
    waiters: int = 1
    data: bytes | None = None
    error: Exception | None = None


class BundleFetchCache:
    """Per-bundle cache for GRIB byte-range fetches."""

    def __init__(
        self,
        *,
        max_entries: int,
        max_bytes: int,
        max_cacheable_bytes: int,
    ) -> None:
        self.max_entries = max(1, int(max_entries))
        self.max_bytes = max(1, int(max_bytes))
        self.max_cacheable_bytes = max(1, int(max_cacheable_bytes))
        self._entries: OrderedDict[str, bytes] = OrderedDict()
        self._entries_bytes = 0
        self._inflight: dict[str, _RangeFetchInflight] = {}
        self._lock = threading.Lock()

    def _evict_if_needed_locked(self, incoming_size: int) -> int:
        evicted = 0
        while self._entries and (
            len(self._entries) >= self.max_entries
            or self._entries_bytes + incoming_size > self.max_bytes
        ):
            _, removed = self._entries.popitem(last=False)
            self._entries_bytes = max(0, self._entries_bytes - len(removed))
            evicted += 1
        return evicted

    def evict(self, key: str) -> bool:
        with self._lock:
            removed = self._entries.pop(key, None)
            if removed is None:
                return False
            self._entries_bytes = max(0, self._entries_bytes - len(removed))
            return True

    def get_or_fetch(
        self,
        key: str,
        *,
        fetcher: Any,
        cacheable: bool,
        expected_size: int | None = None,
    ) -> tuple[bytes, str, int]:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached, "hit", 0
            inflight = self._inflight.get(key)
            if inflight is None:
                inflight = _RangeFetchInflight(event=threading.Event(), waiters=1)
                self._inflight[key] = inflight
                leader = True
            else:
                inflight.waiters += 1
                leader = False

        if not leader:
            inflight.event.wait()
            with self._lock:
                current = self._inflight.get(key)
                if current is None:
                    raise RuntimeError("range fetch inflight state missing")
                current.waiters -= 1
                payload = current.data
                error = current.error
                if current.waiters <= 0:
                    self._inflight.pop(key, None)
            if error is not None:
                raise error
            if payload is None:
                raise RuntimeError("range fetch finished without payload")
            return payload, "wait", 0

        payload: bytes | None = None
        error: Exception | None = None
        evicted = 0
        try:
            payload = bytes(fetcher())
            complete = expected_size is None or len(payload) == int(expected_size)
            if (
                cacheable
                and complete
                and len(payload) <= self.max_cacheable_bytes
                and len(payload) <= self.max_bytes
            ):
                with self._lock:
                    evicted = self._evict_if_needed_locked(len(payload))
                    self._entries[key] = payload
                    self._entries.move_to_end(key)
                    self._entries_bytes += len(payload)
                    while len(self._entries) > self.max_entries:
                        _, removed = self._entries.popitem(last=False)
                        self._entries_bytes = max(0, self._entries_bytes - len(removed))
                        evicted += 1
            return payload, "miss", evicted
        except Exception as exc:  # pragma: no cover - surfaced to callers/tests
            error = exc
            raise
        finally:
            with self._lock:
                current = self._inflight.get(key)
                if current is not None:
                    current.data = payload
                    current.error = error
                    current.event.set()
                    current.waiters -= 1
                    if current.waiters <= 0:
                        self._inflight.pop(key, None)


_IDX_NEGATIVE_CACHE: dict[tuple[str, str, str, int, str], _IdxNegativeCacheEntry] = {}
_IDX_NEGATIVE_CACHE_LOCK = threading.Lock()
_IDX_NEGATIVE_LOG_SUPPRESS: dict[tuple[str, str, str, int], float] = {}

_INVENTORY_CACHE: dict[str, _InventoryCacheEntry] = {}
_INVENTORY_CACHE_LOCK = threading.Lock()
_INVENTORY_INFLIGHT: dict[str, threading.Event] = {}

_FETCH_RUNTIME_COUNTERS: dict[str, int] = {}
_FETCH_RUNTIME_TIMERS_MS: dict[str, _TimerAggregate] = {}
_FETCH_RUNTIME_METRICS_LOCK = threading.Lock()


def _env_value(name: str | tuple[str, ...], default: str = "") -> str:
    names = (name,) if isinstance(name, str) else name
    for env_name in names:
        raw = os.getenv(env_name, "").strip()
        if raw:
            return raw
    return default


def _priority_candidates(herbie_kwargs: dict[str, Any] | None) -> list[str]:
    if herbie_kwargs and herbie_kwargs.get("priority"):
        raw_priority = herbie_kwargs["priority"]
        if isinstance(raw_priority, (list, tuple)):
            parsed = [str(item).strip().lower() for item in raw_priority if str(item).strip()]
            if parsed:
                return parsed
        return [str(raw_priority).strip().lower()]

    raw = _env_value(ENV_HERBIE_PRIORITY)
    if raw:
        parsed = [item.strip().lower() for item in raw.split(",") if item.strip()]
        if parsed:
            return parsed
    return list(DEFAULT_HERBIE_PRIORITY)


def _priority_normalized(priority: str) -> str:
    return str(priority).strip().lower()


def _quiet_herbie_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    quiet_kwargs = dict(kwargs)
    quiet_kwargs.setdefault("verbose", False)
    return quiet_kwargs


def _is_prs_aws_priority(*, priority: str, product: str) -> bool:
    return _priority_normalized(priority) == "aws" and str(product).strip().lower() == "prs"


def _is_idx_lag_reason(reason: str) -> bool:
    return str(reason).strip().lower() in {
        "idx_missing",
        "idx_missing_cached",
        "idx_empty",
        "idx_unparseable",
        "pattern_missing",
        "no_inventory",
    }


def _fallback_to_nomads_sequence(priority_sequence: list[str], *, current_index: int) -> list[str]:
    if current_index < 0:
        return ["nomads"]
    return list(priority_sequence[: current_index + 1]) + ["nomads"]


def _log_source_fallback(
    *,
    from_source: str,
    to_source: str,
    reason: str,
    model_id: str,
    run_date: datetime,
    fh: int,
    var_pattern: str,
) -> None:
    logger.warning(
        "SOURCE_FALLBACK from=%s to=%s reason=%s model=%s run=%s fh=%03d var=%s",
        from_source,
        to_source,
        reason,
        model_id,
        _run_id_from_date(run_date),
        int(fh),
        var_pattern,
    )


def _retry_count() -> int:
    raw = _env_value(ENV_HERBIE_RETRIES, "2")
    try:
        count = int(raw)
    except ValueError:
        return 2
    return max(1, count)


def _retry_sleep_seconds() -> float:
    raw = _env_value(ENV_HERBIE_RETRY_SLEEP, "0.6")
    try:
        value = float(raw)
    except ValueError:
        return 0.6
    return max(0.0, value)


def _float_from_env(name: str | tuple[str, ...], default: float, *, minimum: float = 0.0) -> float:
    raw = _env_value(name)
    if not raw:
        return max(minimum, default)
    try:
        parsed = float(raw)
    except ValueError:
        return max(minimum, default)
    return max(minimum, parsed)


def _int_from_env(name: str | tuple[str, ...], default: int, *, minimum: int = 1) -> int:
    raw = _env_value(name)
    if not raw:
        return max(minimum, int(default))
    try:
        parsed = int(raw)
    except ValueError:
        return max(minimum, int(default))
    return max(minimum, parsed)


def _idx_negative_initial_ttl_seconds() -> float:
    return _float_from_env(
        ENV_HERBIE_IDX_NEGATIVE_CACHE_INITIAL_TTL,
        DEFAULT_IDX_NEGATIVE_INITIAL_TTL_SECONDS,
        minimum=1.0,
    )


def _idx_negative_max_ttl_seconds() -> float:
    default_max = max(DEFAULT_IDX_NEGATIVE_MAX_TTL_SECONDS, _idx_negative_initial_ttl_seconds())
    return _float_from_env(
        ENV_HERBIE_IDX_NEGATIVE_CACHE_MAX_TTL,
        default_max,
        minimum=_idx_negative_initial_ttl_seconds(),
    )


def _inventory_cache_ttl_seconds() -> float:
    return _float_from_env(
        ENV_HERBIE_INVENTORY_CACHE_TTL,
        DEFAULT_INVENTORY_CACHE_TTL_SECONDS,
        minimum=1.0,
    )


def _fetch_cache_max_entries() -> int:
    return _int_from_env(
        ENV_HERBIE_FETCH_CACHE_MAX_ENTRIES,
        DEFAULT_FETCH_CACHE_MAX_ENTRIES,
        minimum=1,
    )


def _fetch_cache_max_bytes() -> int:
    return _int_from_env(
        ENV_HERBIE_FETCH_CACHE_MAX_BYTES,
        DEFAULT_FETCH_CACHE_MAX_BYTES,
        minimum=1024,
    )


def _fetch_cache_max_cacheable_bytes() -> int:
    max_bytes = _fetch_cache_max_bytes()
    return _int_from_env(
        ENV_HERBIE_FETCH_CACHE_MAX_CACHEABLE_BYTES,
        min(DEFAULT_FETCH_CACHE_MAX_CACHEABLE_BYTES, max_bytes),
        minimum=1024,
    )


def new_bundle_fetch_cache() -> BundleFetchCache:
    """Create a byte-range cache intended for one bundle/build context."""
    return BundleFetchCache(
        max_entries=_fetch_cache_max_entries(),
        max_bytes=_fetch_cache_max_bytes(),
        max_cacheable_bytes=min(_fetch_cache_max_cacheable_bytes(), _fetch_cache_max_bytes()),
    )


def _metric_increment(name: str, amount: int = 1) -> None:
    metric_name = str(name).strip()
    if not metric_name:
        return
    with _FETCH_RUNTIME_METRICS_LOCK:
        _FETCH_RUNTIME_COUNTERS[metric_name] = int(_FETCH_RUNTIME_COUNTERS.get(metric_name, 0)) + int(amount)


def _metric_observe_ms(name: str, elapsed_ms: float) -> None:
    metric_name = str(name).strip()
    if not metric_name:
        return
    elapsed = max(0.0, float(elapsed_ms))
    with _FETCH_RUNTIME_METRICS_LOCK:
        aggregate = _FETCH_RUNTIME_TIMERS_MS.get(metric_name)
        if aggregate is None:
            aggregate = _TimerAggregate()
            _FETCH_RUNTIME_TIMERS_MS[metric_name] = aggregate
        aggregate.count += 1
        aggregate.total_ms += elapsed
        aggregate.max_ms = max(aggregate.max_ms, elapsed)


def get_herbie_runtime_metrics_for_tests() -> dict[str, Any]:
    """Return process-local Herbie fetch metrics (tests only)."""
    with _FETCH_RUNTIME_METRICS_LOCK:
        counters = {key: int(value) for key, value in _FETCH_RUNTIME_COUNTERS.items()}
        timers = {
            key: {
                "count": int(value.count),
                "sum_ms": float(value.total_ms),
                "avg_ms": float(value.total_ms / value.count) if value.count > 0 else 0.0,
                "max_ms": float(value.max_ms),
            }
            for key, value in _FETCH_RUNTIME_TIMERS_MS.items()
        }
    return {"counters": counters, "timers_ms": timers}


def _run_id_from_date(run_date: datetime) -> str:
    if run_date.minute or run_date.second or run_date.microsecond:
        return run_date.strftime("%Y%m%d_%H%Mz")
    return run_date.strftime("%Y%m%d_%Hz")


def _url_hash(url: str) -> str:
    return hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:12]


def _env_int_setting(names: tuple[str, ...], default: int, *, minimum: int = 1) -> int:
    for name in names:
        raw_value = os.getenv(name)
        if raw_value is None:
            continue
        try:
            parsed = int(str(raw_value).strip())
        except (TypeError, ValueError):
            continue
        return max(minimum, parsed)
    return max(minimum, int(default))


def _range_fetch_workers() -> int:
    return _env_int_setting(ENV_HERBIE_RANGE_FETCH_WORKERS, DEFAULT_RANGE_FETCH_WORKERS, minimum=1)


def _eps_full_file_cache_enabled(*, model_id: str, product: str) -> bool:
    normalized_model = str(model_id).strip().lower()
    normalized_product = str(product).strip().lower()
    if normalized_model not in {"ifs", "eps"}:
        return False
    if normalized_product != "enfo":
        return False
    return _bool_from_env(ENV_EPS_FULL_FILE_CACHE_ENABLE, False)


def _eps_full_file_cache_root() -> Path:
    configured = _env_value(ENV_EPS_FULL_FILE_CACHE_ROOT)
    if configured:
        return Path(configured).expanduser()
    herbie_save_dir = _env_value(("CARTOSKY_HERBIE_SAVE_DIR", "HERBIE_SAVE_DIR"))
    if herbie_save_dir:
        return Path(herbie_save_dir).expanduser() / "eps_full_files"
    return Path("/tmp/cartosky-eps-full-files")


def _eps_full_file_cache_max_bytes() -> int:
    return _int_from_env(
        ENV_EPS_FULL_FILE_CACHE_MAX_BYTES,
        DEFAULT_EPS_FULL_FILE_CACHE_MAX_BYTES,
        minimum=1024 * 1024,
    )


def _eps_full_file_cache_ttl_seconds() -> float:
    return _float_from_env(
        ENV_EPS_FULL_FILE_CACHE_TTL_SECONDS,
        float(DEFAULT_EPS_FULL_FILE_CACHE_TTL_SECONDS),
        minimum=60.0,
    )


def _eps_full_file_cache_path(*, source_url: str, run_date: datetime, fh: int) -> Path:
    root = _eps_full_file_cache_root()
    file_name = Path(str(source_url).split("?", 1)[0]).name or f"eps-fh{int(fh):03d}.grib2"
    return root / _run_id_from_date(run_date) / f"fh{int(fh):03d}" / f"{_url_hash(source_url)}-{file_name}"


def _iter_cache_files(root: Path) -> list[tuple[Path, int, float]]:
    files: list[tuple[Path, int, float]] = []
    try:
        if not root.exists():
            return files
    except OSError:
        return files

    for path in root.rglob("*"):
        try:
            if not path.is_file() or path.name.endswith(".lock") or path.name.endswith(".part"):
                continue
            stat = path.stat()
        except OSError:
            continue
        files.append((path, int(stat.st_size), float(stat.st_mtime)))
    return files


def _remove_file_quietly(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _remove_empty_parent_dirs(path: Path, *, stop_at: Path) -> None:
    current = path.parent
    while True:
        try:
            if current == stop_at or current == current.parent:
                return
            current.rmdir()
            current = current.parent
        except OSError:
            return


def _cleanup_eps_full_file_cache(*, keep_paths: set[Path] | None = None, force: bool = False) -> None:
    global _EPS_FULL_FILE_CACHE_LAST_CLEANUP_TS
    root = _eps_full_file_cache_root()
    keep = {path.resolve() for path in (keep_paths or set())}
    now_wall = time.time()
    with _EPS_FULL_FILE_CACHE_CLEANUP_LOCK:
        if not force and (now_wall - _EPS_FULL_FILE_CACHE_LAST_CLEANUP_TS) < DEFAULT_EPS_FULL_FILE_CACHE_CLEANUP_INTERVAL_SECONDS:
            return
        _EPS_FULL_FILE_CACHE_LAST_CLEANUP_TS = now_wall

        files = _iter_cache_files(root)
        if not files:
            return

        ttl_seconds = _eps_full_file_cache_ttl_seconds()
        max_bytes = _eps_full_file_cache_max_bytes()
        total_bytes = sum(size for _, size, _ in files)

        for path, size, modified_at in sorted(files, key=lambda item: item[2]):
            resolved = path.resolve()
            if resolved in keep:
                continue
            if (now_wall - modified_at) <= ttl_seconds:
                continue
            if _remove_file_quietly(path):
                total_bytes = max(0, total_bytes - size)
                _metric_increment("eps_full_file_cache_expired")
                _remove_empty_parent_dirs(path, stop_at=root)

        if total_bytes <= max_bytes:
            return

        for path, size, _modified_at in sorted(_iter_cache_files(root), key=lambda item: item[2]):
            resolved = path.resolve()
            if resolved in keep:
                continue
            if total_bytes <= max_bytes:
                break
            if _remove_file_quietly(path):
                total_bytes = max(0, total_bytes - size)
                _metric_increment("eps_full_file_cache_evict")
                _remove_empty_parent_dirs(path, stop_at=root)


def _download_full_grib_to_path(*, source_url: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(f"{out_path.suffix}.part")
    response = requests.get(source_url, stream=True, timeout=90)
    try:
        response.raise_for_status()
        expected_size = _parse_float_tag(response.headers.get("Content-Length"))
        with open(tmp_path, "wb") as dst:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                dst.write(chunk)
    finally:
        response.close()

    file_ok, file_size = _subset_file_status(tmp_path)
    if not file_ok:
        _remove_file_quietly(tmp_path)
        raise RuntimeError(f"EPS full GRIB download produced no file bytes: {source_url}")
    if expected_size is not None and int(expected_size) > 0 and int(file_size) != int(expected_size):
        _remove_file_quietly(tmp_path)
        raise RuntimeError(
            f"EPS full GRIB download size mismatch for {source_url}: got {file_size}, expected {int(expected_size)}"
        )
    tmp_path.replace(out_path)
    return out_path


def _maybe_get_eps_full_grib_path(
    H: Any,
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    priority: str,
) -> Path | None:
    source_url = str(getattr(H, "grib", "") or "").strip()
    if not source_url.startswith(("http://", "https://")):
        return None
    if not _eps_full_file_cache_enabled(model_id=model_id, product=product):
        return None

    cache_path = _eps_full_file_cache_path(source_url=source_url, run_date=run_date, fh=fh)
    try:
        with _path_download_lock(cache_path):
            cached_ok, cached_size = _subset_file_status(cache_path)
            if cached_ok:
                cache_path.touch()
                logger.info(
                    "FULL_GRIB_CACHE event=hit source=%s model=%s run=%s fh=%03d file=%s size=%d",
                    priority,
                    model_id,
                    _run_id_from_date(run_date),
                    int(fh),
                    cache_path.name,
                    cached_size,
                )
                _metric_increment("eps_full_file_cache_hit")
            else:
                _cleanup_eps_full_file_cache(force=True)
                downloaded_path = _download_full_grib_to_path(source_url=source_url, out_path=cache_path)
                downloaded_path.touch()
                logger.info(
                    "FULL_GRIB_CACHE event=store source=%s model=%s run=%s fh=%03d file=%s size=%d",
                    priority,
                    model_id,
                    _run_id_from_date(run_date),
                    int(fh),
                    downloaded_path.name,
                    downloaded_path.stat().st_size,
                )
                _metric_increment("eps_full_file_cache_miss")
                _metric_increment("eps_full_file_cache_store")
    except Exception as exc:
        logger.warning(
            "FULL_GRIB_CACHE event=error source=%s model=%s run=%s fh=%03d url_hash=%s error=%s",
            priority,
            model_id,
            _run_id_from_date(run_date),
            int(fh),
            _url_hash(source_url),
            exc,
        )
        _metric_increment("eps_full_file_cache_error")
        return None

    _cleanup_eps_full_file_cache(keep_paths={cache_path})
    return cache_path


def _range_cache_key(
    *,
    source: str,
    model_id: str,
    run_date: datetime,
    fh: int,
    url: str,
    start_byte: int,
    end_byte: int,
) -> str:
    return "|".join(
        [
            str(source).strip().lower() or "-",
            str(model_id).strip().lower() or "-",
            _run_id_from_date(run_date),
            f"{int(fh):03d}",
            str(url).strip(),
            f"{int(start_byte)}-{int(end_byte)}",
        ]
    )


def _idx_negative_key(
    *,
    model_id: str,
    run_date: datetime,
    product: str,
    fh: int,
    priority: str,
) -> tuple[str, str, str, int, str]:
    run_id = _run_id_from_date(run_date)
    return (
        str(model_id).strip().lower(),
        run_id,
        str(product).strip().lower(),
        int(fh),
        str(priority).strip().lower(),
    )


def _idx_negative_log_key(
    *,
    model_id: str,
    run_date: datetime,
    product: str,
    fh: int,
) -> tuple[str, str, str, int]:
    run_id = _run_id_from_date(run_date)
    return (
        str(model_id).strip().lower(),
        run_id,
        str(product).strip().lower(),
        int(fh),
    )


def _idx_negative_cache_remaining(cache_key: tuple[str, str, str, int, str]) -> float:
    now = time.monotonic()
    with _IDX_NEGATIVE_CACHE_LOCK:
        entry = _IDX_NEGATIVE_CACHE.get(cache_key)
        if entry is None:
            return 0.0
        if now >= entry.expires_at:
            _IDX_NEGATIVE_CACHE.pop(cache_key, None)
            return 0.0
        return max(0.0, entry.expires_at - now)


def _record_idx_negative_cache(cache_key: tuple[str, str, str, int, str]) -> float:
    now = time.monotonic()
    initial_ttl = _idx_negative_initial_ttl_seconds()
    max_ttl = _idx_negative_max_ttl_seconds()
    with _IDX_NEGATIVE_CACHE_LOCK:
        previous = _IDX_NEGATIVE_CACHE.get(cache_key)
        if previous is not None and now < previous.expires_at:
            ttl = min(max_ttl, max(initial_ttl, previous.ttl_seconds * 2.0))
        else:
            ttl = initial_ttl
        _IDX_NEGATIVE_CACHE[cache_key] = _IdxNegativeCacheEntry(
            expires_at=now + ttl,
            ttl_seconds=ttl,
            updated_at=now,
        )
    return ttl


def _log_idx_missing_once(
    *,
    model_id: str,
    run_date: datetime,
    product: str,
    fh: int,
    priority: str,
    search_pattern: str,
    ttl_seconds: float,
    source: str,
) -> None:
    now = time.monotonic()
    log_key = _idx_negative_log_key(
        model_id=model_id,
        run_date=run_date,
        product=product,
        fh=fh,
    )
    should_log = False
    with _IDX_NEGATIVE_CACHE_LOCK:
        suppress_until = _IDX_NEGATIVE_LOG_SUPPRESS.get(log_key, 0.0)
        if now >= suppress_until:
            _IDX_NEGATIVE_LOG_SUPPRESS[log_key] = now + max(1.0, ttl_seconds)
            should_log = True
    if should_log:
        logger.warning(
            "Herbie precheck unavailable (%s %s %s fh%03d; priority=%s; pattern=%s): no idx (%s; suppress=%ds)",
            model_id,
            _run_id_from_date(run_date),
            product,
            int(fh),
            priority,
            search_pattern,
            source,
            int(max(1.0, ttl_seconds)),
        )


def _record_and_log_idx_missing(
    *,
    model_id: str,
    run_date: datetime,
    product: str,
    fh: int,
    priority: str,
    search_pattern: str,
    source: str,
) -> float:
    cache_key = _idx_negative_key(
        model_id=model_id,
        run_date=run_date,
        product=product,
        fh=fh,
        priority=priority,
    )
    ttl = _record_idx_negative_cache(cache_key)
    _log_idx_missing_once(
        model_id=model_id,
        run_date=run_date,
        product=product,
        fh=fh,
        priority=priority,
        search_pattern=search_pattern,
        ttl_seconds=ttl,
        source=source,
    )
    return ttl


def _inventory_cache_key_from_idx(
    idx_ref: Any,
    *,
    priority: str = "",
    model_id: str = "",
    run_date: datetime | None = None,
    product: str = "",
    fh: int | None = None,
    grib_ref: Any = None,
) -> str:
    idx_url = str(idx_ref).strip()
    if not idx_url:
        return ""
    run_id = "-"
    if isinstance(run_date, datetime):
        run_id = _run_id_from_date(run_date)
    fh_token = "-"
    if fh is not None:
        try:
            fh_token = f"{int(fh):03d}"
        except Exception:
            fh_token = str(fh).strip() or "-"
    grib_token = str(grib_ref).strip() if grib_ref is not None else ""
    return "|".join(
        [
            _priority_normalized(priority) or "-",
            str(model_id).strip().lower() or "-",
            run_id,
            str(product).strip().lower() or "-",
            fh_token,
            idx_url,
            grib_token or "-",
        ]
    )


def _inventory_cache_get(key: str) -> Any | None:
    now = time.monotonic()
    with _INVENTORY_CACHE_LOCK:
        entry = _INVENTORY_CACHE.get(key)
        if entry is None:
            return None
        if now >= entry.expires_at:
            _INVENTORY_CACHE.pop(key, None)
            return None
        return entry.data


def _inventory_cache_set(key: str, data: Any, ttl_seconds: float) -> None:
    now = time.monotonic()
    with _INVENTORY_CACHE_LOCK:
        _INVENTORY_CACHE[key] = _InventoryCacheEntry(
            data=data,
            expires_at=now + max(1.0, ttl_seconds),
            updated_at=now,
        )


def _inventory_cache_delete(key: str) -> None:
    if not key:
        return
    with _INVENTORY_CACHE_LOCK:
        _INVENTORY_CACHE.pop(key, None)


def _ecmwf_search_this_from_record(record: dict[str, Any]) -> str:
    param = str(record.get("param") or "").strip()
    if not param:
        return ""

    tokens: list[str] = [param]
    levelist = str(record.get("levelist") or "").strip()
    levtype = str(record.get("levtype") or "").strip()
    number = str(record.get("number") or "").strip()

    if levelist:
        tokens.append(levelist)
    if levtype:
        tokens.append(levtype)
    if number and number.lower() != "nan":
        tokens.append(number)

    for key in ("domain", "expver", "class", "type", "stream"):
        value = str(record.get(key) or "").strip()
        if value:
            tokens.append(value)

    return ":" + ":".join(tokens)


def _fetch_inventory_index_text(idx_ref: Any) -> str:
    idx_text = str(idx_ref or "").strip()
    if not idx_text:
        return ""
    if idx_text.startswith(("http://", "https://")):
        response = requests.get(idx_text, timeout=45)
        response.raise_for_status()
        text = str(response.text)
        response.close()
        return text
    return Path(idx_text).read_text(encoding="utf-8")


def _inventory_index_dataframe_from_json_lines(idx_ref: Any) -> Any | None:
    try:
        import pandas as pd
    except Exception:
        return None

    text = _fetch_inventory_index_text(idx_ref)
    if not text.strip():
        return pd.DataFrame()

    records: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = str(raw_line).strip()
        if not line:
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            continue
        record = dict(parsed)

        offset = record.get("_offset")
        length = record.get("_length")
        try:
            if offset is not None and np.isfinite(offset):
                record.setdefault("start_byte", int(offset))
        except Exception:
            pass
        try:
            if offset is not None and length is not None and np.isfinite(offset) and np.isfinite(length):
                record.setdefault("end_byte", int(offset) + int(length) - 1)
        except Exception:
            pass

        search_this = _ecmwf_search_this_from_record(record)
        if search_this:
            record.setdefault("search_this", search_this)
            record.setdefault("inventory_line", search_this)
            record.setdefault("line", search_this)

        records.append(record)

    return pd.DataFrame.from_records(records)


def _inventory_index_dataframe(
    H: Any,
    *,
    idx_key: str,
    force_refresh: bool = False,
) -> Any | None:
    if not force_refresh:
        cached = _inventory_cache_get(idx_key)
        if cached is not None:
            _metric_increment("idx_cache_hit")
            return cached
    else:
        _inventory_cache_delete(idx_key)

    downloader = False
    inflight_event: threading.Event
    now = time.monotonic()
    with _INVENTORY_CACHE_LOCK:
        entry = _INVENTORY_CACHE.get(idx_key)
        if entry is not None and now < entry.expires_at:
            _metric_increment("idx_cache_hit")
            return entry.data
        if entry is not None and now >= entry.expires_at:
            _INVENTORY_CACHE.pop(idx_key, None)
        existing = _INVENTORY_INFLIGHT.get(idx_key)
        if existing is None:
            inflight_event = threading.Event()
            _INVENTORY_INFLIGHT[idx_key] = inflight_event
            downloader = True
            _metric_increment("idx_cache_miss")
        else:
            inflight_event = existing

    if not downloader:
        inflight_event.wait(timeout=max(5.0, _inventory_cache_ttl_seconds()))
        reused = _inventory_cache_get(idx_key)
        if reused is not None:
            _metric_increment("idx_cache_hit")
            return reused
        _metric_increment("idx_cache_miss")
        return None

    fetch_start = time.monotonic()
    try:
        try:
            dataframe = H.index_as_dataframe
        except Exception:
            dataframe = _inventory_index_dataframe_from_json_lines(getattr(H, "idx", None))
        _metric_observe_ms("idx_fetch_ms", (time.monotonic() - fetch_start) * 1000.0)
        if dataframe is None:
            _metric_increment("idx_cache_error")
            return None
        try:
            dataframe_len = len(dataframe)
        except Exception:
            _metric_increment("idx_cache_error")
            raise
        if dataframe_len > 0:
            _inventory_cache_set(idx_key, dataframe, _inventory_cache_ttl_seconds())
            _metric_increment("idx_cache_store")
        return dataframe
    except Exception:
        _metric_observe_ms("idx_fetch_ms", (time.monotonic() - fetch_start) * 1000.0)
        _metric_increment("idx_cache_error")
        raise
    finally:
        with _INVENTORY_CACHE_LOCK:
            event = _INVENTORY_INFLIGHT.pop(idx_key, None)
            if event is not None:
                event.set()


def _inventory_filter(index_df: Any, search_pattern: str) -> Any | None:
    if index_df is None:
        return None
    try:
        if len(index_df) == 0:
            return index_df
    except Exception:
        return None

    pattern = str(search_pattern)
    compiled_pattern: re.Pattern[str] | None = None
    try:
        compiled_pattern = re.compile(pattern)
    except re.error:
        compiled_pattern = None

    try:
        for col in _INVENTORY_SEARCH_COLUMNS:
            if col in index_df.columns:
                series = index_df[col].astype(str)
                # Treat inventory selectors as literal strings first so GRIB labels
                # containing regex metacharacters like parentheses still match.
                mask = series.str.contains(pattern, regex=False, na=False)
                subset = index_df.loc[mask]
                if len(subset) > 0:
                    return subset

                if compiled_pattern is None:
                    continue

                mask = series.str.contains(compiled_pattern.pattern, regex=True, na=False)
                subset = index_df.loc[mask]
                if len(subset) > 0:
                    return subset
        return index_df.iloc[0:0]
    except Exception:
        return None


def _inventory_search(
    H: Any,
    *,
    search_pattern: str,
    priority: str = "",
    model_id: str = "",
    run_date: datetime | None = None,
    product: str = "",
    fh: int | None = None,
    force_inventory_refresh: bool = False,
) -> _InventorySearchResult:
    idx_ref: Any
    try:
        idx_ref = getattr(H, "idx", None)
    except Exception as exc:
        if _is_missing_index_error(exc):
            return _InventorySearchResult(inventory=None, reason="idx_missing")
        return _InventorySearchResult(inventory=None, reason="idx_unparseable")
    try:
        grib_ref = getattr(H, "grib", None)
    except Exception:
        grib_ref = None
    priority_token = str(priority).strip() or str(getattr(H, "priority", "") or "")
    model_token = str(model_id).strip() or str(getattr(H, "model", "") or "")
    run_token = run_date if isinstance(run_date, datetime) else getattr(H, "date", None)
    product_token = str(product).strip() or str(getattr(H, "product", "") or "")
    fh_token = fh if fh is not None else getattr(H, "fxx", None)
    idx_key = _inventory_cache_key_from_idx(
        idx_ref,
        priority=priority_token,
        model_id=model_token,
        run_date=run_token,
        product=product_token,
        fh=fh_token,
        grib_ref=grib_ref,
    )
    if not idx_key:
        return _InventorySearchResult(inventory=None, reason="idx_missing")

    try:
        index_df = _inventory_index_dataframe(H, idx_key=idx_key, force_refresh=force_inventory_refresh)
    except Exception:
        return _InventorySearchResult(inventory=None, reason="idx_unparseable", idx_key=idx_key)
    if index_df is None:
        return _InventorySearchResult(inventory=None, reason="idx_empty", idx_key=idx_key)

    try:
        if len(index_df) == 0:
            return _InventorySearchResult(inventory=index_df, reason="idx_empty", idx_key=idx_key)
    except Exception:
        return _InventorySearchResult(inventory=None, reason="idx_unparseable", idx_key=idx_key)

    parse_start = time.monotonic()
    filtered = _inventory_filter(index_df, search_pattern)
    _metric_observe_ms("idx_parse_ms", (time.monotonic() - parse_start) * 1000.0)
    if filtered is None:
        return _InventorySearchResult(inventory=None, reason="idx_unparseable", idx_key=idx_key)

    try:
        if len(filtered) == 0:
            idx_ref_text = str(idx_ref or "").strip().lower()
            if idx_ref_text.startswith(("http://", "https://")):
                refreshed_df = _inventory_index_dataframe(H, idx_key=idx_key, force_refresh=True)
                if refreshed_df is not None:
                    parse_start = time.monotonic()
                    refreshed_filtered = _inventory_filter(refreshed_df, search_pattern)
                    _metric_observe_ms("idx_parse_ms", (time.monotonic() - parse_start) * 1000.0)
                    if refreshed_filtered is None:
                        return _InventorySearchResult(inventory=None, reason="idx_unparseable", idx_key=idx_key)
                    try:
                        if len(refreshed_filtered) > 0:
                            _metric_increment("idx_cache_pattern_refresh")
                            return _InventorySearchResult(inventory=refreshed_filtered, reason="ok", idx_key=idx_key)
                    except Exception:
                        return _InventorySearchResult(inventory=None, reason="idx_unparseable", idx_key=idx_key)
            return _InventorySearchResult(inventory=filtered, reason="pattern_missing", idx_key=idx_key)
    except Exception:
        return _InventorySearchResult(inventory=None, reason="idx_unparseable", idx_key=idx_key)
    return _InventorySearchResult(inventory=filtered, reason="ok", idx_key=idx_key)


def _inventory_lines_from_rows(inventory: Any) -> list[str]:
    if inventory is None:
        return []
    try:
        if len(inventory) == 0:
            return []
    except Exception:
        return []

    lines: list[str] = []
    for row_index in range(len(inventory)):
        try:
            row = inventory.iloc[row_index]
        except Exception:
            continue
        line = _inventory_line_from_row(row)
        if line:
            lines.append(line)
    return lines


def _regular_latlon_affine(longitude: np.ndarray, latitude: np.ndarray) -> rasterio.transform.Affine:
    lon = np.asarray(longitude, dtype=np.float64).reshape(-1)
    lat = np.asarray(latitude, dtype=np.float64).reshape(-1)
    if lon.size < 2 or lat.size < 2:
        raise ValueError("Regular lat/lon grid requires at least two points per axis")

    xres = float(np.median(np.diff(lon)))
    yres = float(np.median(np.diff(lat)))
    if not np.isfinite(xres) or not np.isfinite(yres) or xres == 0.0 or yres == 0.0:
        raise ValueError("Unable to derive regular lat/lon resolution from coordinates")

    west = float(lon[0] - (xres / 2.0))
    north = float(lat[0] - (yres / 2.0))
    return rasterio.transform.Affine(xres, 0.0, west, 0.0, yres, north)


def _normalize_temperature_units_for_xarray(data: np.ndarray, units: str | None) -> np.ndarray:
    normalized_units = str(units or "").strip().lower()
    if normalized_units in {"k", "kelvin", "degrees_k", "degree_kelvin"}:
        return data - np.float32(273.15)
    return data


def _inventory_row_byte_range(row: Any) -> tuple[int, int] | None:
    start_byte: int | None = None
    end_byte: int | None = None

    try:
        raw_start = row.get("start_byte")
    except Exception:
        raw_start = None
    try:
        raw_offset = row.get("_offset")
    except Exception:
        raw_offset = None

    for candidate in (raw_start, raw_offset):
        try:
            if candidate is not None and np.isfinite(candidate):
                start_byte = int(candidate)
                break
        except Exception:
            continue

    if start_byte is None:
        return None

    try:
        raw_end = row.get("end_byte")
        if raw_end is not None and np.isfinite(raw_end):
            end_byte = int(raw_end)
    except Exception:
        end_byte = None

    if end_byte is None:
        try:
            raw_length = row.get("_length")
            if raw_length is not None and np.isfinite(raw_length):
                parsed_length = int(raw_length)
                if parsed_length > 0:
                    end_byte = start_byte + parsed_length - 1
        except Exception:
            end_byte = None

    if end_byte is None or end_byte < start_byte:
        return None
    return start_byte, end_byte


def _normalize_temperature_units_for_grib(data: np.ndarray, tags: dict[str, Any]) -> np.ndarray:
    unit_text = " ".join(
        str(tags.get(key) or "").strip().lower()
        for key in ("GRIB_UNIT", "units", "GRIB_COMMENT")
    )
    if "[k]" in unit_text or unit_text in {"k", "kelvin"}:
        return data - np.float32(273.15)
    return data


def _read_rasterio_band(src: Any, *, band_index: int) -> np.ndarray:
    band_data = src.read(band_index, masked=True)
    data = np.asarray(np.ma.filled(band_data, np.nan), dtype=np.float32)
    band_mask = np.ma.getmaskarray(band_data)
    if band_mask is not np.ma.nomask:
        data = np.where(band_mask, np.nan, data).astype(np.float32, copy=False)

    nodata_val = _parse_float_tag(getattr(src, "nodata", None))
    if nodata_val is not None:
        atol = max(1e-6, abs(nodata_val) * 1e-6)
        data = np.where(np.isclose(data, nodata_val, rtol=0.0, atol=atol), np.nan, data).astype(np.float32, copy=False)

    tag_values: list[float] = []
    band_tags = src.tags(band_index)
    for tags in (src.tags(), band_tags):
        for key in _MISSING_VALUE_TAG_KEYS:
            parsed = _parse_float_tag(tags.get(key))
            if parsed is not None:
                tag_values.append(parsed)
    for missing_val in set(tag_values):
        atol = max(1e-6, abs(missing_val) * 1e-6)
        data = np.where(np.isclose(data, missing_val, rtol=0.0, atol=atol), np.nan, data).astype(np.float32, copy=False)

    data = np.where(np.abs(data) > 1e12, np.nan, data).astype(np.float32, copy=False)
    return _normalize_temperature_units_for_grib(data, band_tags)


def _read_rasterio_dataset(src: Any) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    data = _read_rasterio_band(src, band_index=1)
    return data, src.crs, src.transform


def _read_grib_raster(source: Path | str | bytes) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    if isinstance(source, bytes):
        with rasterio.io.MemoryFile(source) as memfile:
            with memfile.open() as src:
                return _read_rasterio_dataset(src)
    with rasterio.open(source) as src:
        return _read_rasterio_dataset(src)


def _aggregation_subset_path(base_path: Path, token: str) -> Path:
    suffix = base_path.suffix or ".grib2"
    return base_path.with_name(f"{base_path.stem}.{token}{suffix}")


def _download_subset_with_inventory_rows(
    H: Any,
    *,
    inventory: Any,
    out_path: Path,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    priority: str,
    bundle_fetch_cache: BundleFetchCache | None,
) -> Path | None:
    source_url = getattr(H, "grib", None)
    if source_url is None:
        return None
    cached_full_path = _maybe_get_eps_full_grib_path(
        H,
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        priority=priority,
    )
    if cached_full_path is not None:
        source_url = str(cached_full_path)

    row_ranges: list[tuple[int, int]] = []
    for _, row in inventory.iterrows():
        byte_range = _inventory_row_byte_range(row)
        if byte_range is None:
            continue
        row_ranges.append(byte_range)
    if not row_ranges:
        return None

    ordered_ranges: list[tuple[int, int]] = []
    seen_ranges: set[tuple[int, int]] = set()
    for start_byte, end_byte in sorted(row_ranges, key=lambda item: (item[0], item[1])):
        range_key = (int(start_byte), int(end_byte))
        if range_key in seen_ranges:
            continue
        seen_ranges.add(range_key)
        ordered_ranges.append(range_key)

    is_remote = str(source_url).startswith(("http://", "https://"))

    def _read_remote_payload(range_key: tuple[int, int]) -> bytes:
        start_byte, end_byte = range_key
        return _fetch_range_bytes(
            source=priority,
            source_url=str(source_url),
            model_id=model_id,
            run_date=run_date,
            fh=fh,
            start_byte=start_byte,
            end_byte=end_byte,
            bundle_fetch_cache=bundle_fetch_cache,
            require_grib_payload=True,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wrote_bytes = False
    remote_payloads: dict[tuple[int, int], bytes] = {}
    max_workers = min(len(ordered_ranges), _range_fetch_workers())
    if is_remote and max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="eps-range") as executor:
            future_map = {
                executor.submit(_read_remote_payload, range_key): range_key
                for range_key in ordered_ranges
            }
            for future in as_completed(future_map):
                range_key = future_map[future]
                remote_payloads[range_key] = future.result()

    with open(out_path, "wb") as dst:
        src = open(source_url, "rb") if not is_remote else None
        try:
            for start_byte, end_byte in ordered_ranges:
                range_key = (int(start_byte), int(end_byte))
                if is_remote:
                    if remote_payloads:
                        payload = remote_payloads.get(range_key, b"")
                    else:
                        payload = _read_remote_payload(range_key)
                else:
                    assert src is not None
                    src.seek(start_byte)
                    payload = src.read(end_byte - start_byte + 1)
                if not payload:
                    continue
                _validate_grib_range_payload(
                    payload,
                    source=priority,
                    source_url=str(source_url),
                    model_id=model_id,
                    run_date=run_date,
                    fh=fh,
                    start_byte=start_byte,
                    end_byte=end_byte,
                )
                dst.write(payload)
                wrote_bytes = True
        finally:
            if src is not None:
                src.close()

    if not wrote_bytes:
        return None
    subset_ok, _subset_size = _subset_file_status(out_path)
    if not subset_ok:
        return None
    return out_path


def _aggregate_grib_subset_mean(grib_path: Path) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, int]:
    with rasterio.open(grib_path) as src:
        if int(getattr(src, "count", 0)) <= 0:
            raise RuntimeError(f"EPS pf-mean subset contains no GRIB bands: {grib_path}")

        aggregate_sum: np.ndarray | None = None
        aggregate_count: np.ndarray | None = None
        member_count = 0

        for band_index in range(1, int(src.count) + 1):
            member_data = _read_rasterio_band(src, band_index=band_index)
            if aggregate_sum is None:
                aggregate_sum = np.zeros(member_data.shape, dtype=np.float64)
                aggregate_count = np.zeros(member_data.shape, dtype=np.uint16)
            elif member_data.shape != aggregate_sum.shape:
                raise RuntimeError(f"EPS pf-mean subset band shape mismatch in {grib_path}")

            finite_mask = np.isfinite(member_data)
            aggregate_sum[finite_mask] += member_data[finite_mask].astype(np.float64, copy=False)
            aggregate_count[finite_mask] += 1
            member_count += 1

        if aggregate_sum is None or aggregate_count is None or member_count == 0:
            raise RuntimeError(f"EPS pf-mean subset produced no usable member data: {grib_path}")

        data = np.full(aggregate_sum.shape, np.nan, dtype=np.float32)
        valid_mask = aggregate_count > 0
        data[valid_mask] = (aggregate_sum[valid_mask] / aggregate_count[valid_mask]).astype(np.float32, copy=False)
        return data, src.crs, src.transform, member_count


def _ecmwf_pf_mean_from_xarray_result(result: Any) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]:
    datasets = result if isinstance(result, list) else [result]
    selected = None
    selected_name = ""

    for dataset in datasets:
        if dataset is None:
            continue
        data_vars = getattr(dataset, "data_vars", None)
        if not data_vars:
            continue
        data_var_names = list(data_vars)
        if not data_var_names:
            continue
        candidate = dataset[data_var_names[0]]
        if "number" not in getattr(candidate, "dims", ()):  # control dataset lacks a member dimension
            continue
        number_coord = getattr(candidate, "coords", {}).get("number")
        if number_coord is None:
            continue
        try:
            number_values = np.asarray(number_coord.values, dtype=np.int64)
        except Exception:
            continue
        if number_values.ndim != 1 or number_values.size < 2:
            continue
        member_indexes = np.where(number_values > 0)[0]
        if member_indexes.size == 0:
            continue
        selected = candidate.isel(number=member_indexes).mean(dim="number", skipna=True)
        selected_name = str(data_var_names[0])
        break

    if selected is None:
        raise RuntimeError("Unable to locate ECMWF EPS perturbed-member dataset for mean aggregation")

    latitude = getattr(selected, "coords", {}).get("latitude")
    longitude = getattr(selected, "coords", {}).get("longitude")
    if latitude is None or longitude is None:
        raise RuntimeError("ECMWF EPS mean aggregation requires latitude/longitude coordinates")

    data = np.asarray(selected.values, dtype=np.float32)
    if data.ndim != 2:
        raise RuntimeError(f"ECMWF EPS aggregated field must be 2-D, got {data.ndim}-D for {selected_name}")

    data = _normalize_temperature_units_for_xarray(data, getattr(selected, "attrs", {}).get("units"))

    lat_values = np.asarray(latitude.values, dtype=np.float64)
    if lat_values.ndim == 1 and lat_values.size >= 2 and lat_values[1] > lat_values[0]:
        data = np.flipud(data)
        lat_values = lat_values[::-1]

    transform = _regular_latlon_affine(np.asarray(longitude.values, dtype=np.float64), lat_values)
    return data, rasterio.crs.CRS.from_epsg(4326), transform


def _fetch_ecmwf_pf_mean_variable(
    *,
    model_id: str,
    product: str,
    search_pattern: str,
    run_date: datetime,
    fh: int,
    herbie_kwargs: dict[str, Any] | None,
    bundle_fetch_cache: BundleFetchCache | None,
    return_meta: bool,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine] | tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]]:
    from herbie.core import Herbie

    kwargs: dict[str, Any] = {
        "model": model_id,
        "product": product,
        "fxx": fh,
    }
    if herbie_kwargs:
        kwargs.update(herbie_kwargs)

    herbie_date = run_date.replace(tzinfo=None) if run_date.tzinfo else run_date
    priority_list = [_priority_normalized(item) for item in _priority_candidates(herbie_kwargs) if str(item).strip()]
    retries = _retry_count()
    sleep_s = _retry_sleep_seconds()
    last_exc: Exception | None = None

    for priority in priority_list:
        for attempt_idx in range(1, retries + 1):
            try:
                run_kwargs = _quiet_herbie_kwargs(kwargs)
                run_kwargs["priority"] = priority
                H = Herbie(herbie_date, **run_kwargs)
                inv_result = _inventory_search(
                    H,
                    search_pattern=search_pattern,
                    priority=priority,
                    model_id=model_id,
                    run_date=run_date,
                    product=product,
                    fh=fh,
                )
                inventory = inv_result.inventory
                if inv_result.reason != "ok" or inventory is None or len(inventory) == 0:
                    raise RuntimeError(
                        f"ECMWF EPS pf-mean inventory unavailable for {model_id} fh{fh:03d} pattern={search_pattern!r}: {inv_result.reason}"
                    )

                if "type" in inventory.columns:
                    type_series = inventory["type"].astype(str).str.strip().str.lower()
                    pf_inventory = inventory.loc[type_series == "pf"]
                else:
                    pf_inventory = inventory

                if len(pf_inventory) == 0:
                    raise RuntimeError(
                        f"ECMWF EPS pf-mean inventory contained no perturbed members for {model_id} fh{fh:03d} pattern={search_pattern!r}"
                    )

                if "number" in pf_inventory.columns:
                    try:
                        pf_inventory = pf_inventory.assign(
                            _cartosky_member_number=np.to_numeric(pf_inventory["number"], errors="coerce")
                        ).sort_values("_cartosky_member_number", kind="stable")
                    except Exception:
                        pass
                first_inventory_line = ""
                try:
                    first_inventory_line = _inventory_line_from_row(pf_inventory.iloc[0])
                except Exception:
                    first_inventory_line = ""

                subset_hint: Path | None = None
                try:
                    subset_hint = Path(H.get_localFilePath(search_pattern))
                except Exception:
                    subset_hint = None
                if subset_hint is None:
                    fallback_name = hashlib.sha1(
                        f"{model_id}|{product}|{fh}|{search_pattern}|{priority}".encode("utf-8")
                    ).hexdigest()[:16]
                    subset_hint = Path(os.getcwd()) / f"eps_pf_mean_{fallback_name}.grib2"

                subset_path = _aggregation_subset_path(subset_hint, "cartosky_pf")
                with _subset_download_lock(subset_path):
                    cached_ok, _cached_size = _subset_file_status(subset_path)
                    if not cached_ok:
                        downloaded_subset = _download_subset_with_inventory_rows(
                            H,
                            inventory=pf_inventory,
                            out_path=subset_path,
                            model_id=model_id,
                            product=product,
                            run_date=run_date,
                            fh=fh,
                            priority=priority,
                            bundle_fetch_cache=bundle_fetch_cache,
                        )
                        if downloaded_subset is None:
                            raise RuntimeError(
                                f"ECMWF EPS pf-mean subset download failed for {model_id} fh{fh:03d} pattern={search_pattern!r}"
                            )
                    data, crs, transform, member_count = _aggregate_grib_subset_mean(subset_path)

                meta = {
                    "inventory_line": first_inventory_line or f"aggregate:{search_pattern}:pf_mean",
                    "search_pattern": str(search_pattern),
                    "fh": int(fh),
                    "product": str(product),
                    "priority": str(priority),
                    "aggregation": "ecmwf_pf_mean",
                    "member_count": int(member_count),
                }
                if return_meta:
                    return data, crs, transform, meta
                return data, crs, transform
            except Exception as exc:
                last_exc = exc
                if sleep_s > 0 and attempt_idx < retries:
                    time.sleep(sleep_s)

    if last_exc is not None:
        raise RuntimeError(
            f"ECMWF EPS pf-mean aggregation failed for {model_id} fh{fh:03d} pattern={search_pattern!r}"
        ) from last_exc
    raise RuntimeError(
        f"ECMWF EPS pf-mean aggregation failed without a captured exception for {model_id} fh{fh:03d} pattern={search_pattern!r}"
    )


def _ecmwf_eps_statistics_file_fh(fh: int) -> int:
    return 240 if int(fh) <= 240 else 360


def _ecmwf_eps_statistics_url(url: Any, *, requested_fh: int, statistics_fh: int) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    replacements = (
        (f"-{int(requested_fh)}h-enfo-ef.grib2.index", f"-{int(statistics_fh)}h-enfo-ep.grib2.index"),
        (f"-{int(requested_fh)}h-enfo-ef.grib2", f"-{int(statistics_fh)}h-enfo-ep.grib2"),
        (f"-{int(requested_fh)}h-enfo-ef.index", f"-{int(statistics_fh)}h-enfo-ep.index"),
    )
    for suffix, replacement in replacements:
        if suffix in text:
            return text.replace(suffix, replacement, 1)
    return re.sub(
        r"-\d+h-enfo-ef(\.grib2\.index|\.grib2|\.index)",
        lambda match: f"-{int(statistics_fh)}h-enfo-ep{match.group(1)}",
        text,
        count=1,
    )


def _point_herbie_at_ecmwf_eps_statistics_file(H: Any, *, requested_fh: int, statistics_fh: int) -> None:
    try:
        grib_url = _ecmwf_eps_statistics_url(
            getattr(H, "grib", ""),
            requested_fh=requested_fh,
            statistics_fh=statistics_fh,
        )
        if grib_url:
            setattr(H, "grib", grib_url)
    except Exception:
        pass
    try:
        idx_url = _ecmwf_eps_statistics_url(
            getattr(H, "idx", ""),
            requested_fh=requested_fh,
            statistics_fh=statistics_fh,
        )
        if idx_url:
            setattr(H, "idx", idx_url)
    except Exception:
        pass


def _filter_inventory_step(inventory: Any, *, fh: int) -> Any:
    try:
        if "step" not in inventory.columns:
            return inventory
        step_series = inventory["step"]
        step_values = None
        try:
            import pandas as pd

            if pd.api.types.is_timedelta64_dtype(step_series):
                step_values = step_series.dt.total_seconds() / 3600.0
        except Exception:
            step_values = None
        if step_values is None:
            step_values = np.to_numeric(step_series, errors="coerce")
        return inventory.loc[step_values == int(fh)]
    except Exception:
        return inventory


def _fetch_ecmwf_direct_mean_variable(
    *,
    model_id: str,
    product: str,
    search_pattern: str,
    run_date: datetime,
    fh: int,
    herbie_kwargs: dict[str, Any] | None,
    bundle_fetch_cache: BundleFetchCache | None,
    return_meta: bool,
    fallback_to_pf_mean: bool = False,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine] | tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]]:
    from herbie.core import Herbie

    kwargs: dict[str, Any] = {
        "model": model_id,
        "product": product,
        "fxx": fh,
    }
    if herbie_kwargs:
        kwargs.update(herbie_kwargs)

    herbie_date = run_date.replace(tzinfo=None) if run_date.tzinfo else run_date
    priority_list = [_priority_normalized(item) for item in _priority_candidates(herbie_kwargs) if str(item).strip()]
    retries = _retry_count()
    sleep_s = _retry_sleep_seconds()
    last_exc: Exception | None = None

    for priority in priority_list:
        for attempt_idx in range(1, retries + 1):
            try:
                direct_fh = _ecmwf_eps_statistics_file_fh(fh)
                run_kwargs = _quiet_herbie_kwargs(kwargs)
                run_kwargs["priority"] = priority
                run_kwargs["fxx"] = direct_fh
                H = Herbie(herbie_date, **run_kwargs)
                _point_herbie_at_ecmwf_eps_statistics_file(
                    H,
                    requested_fh=fh,
                    statistics_fh=direct_fh,
                )
                inv_result = _inventory_search(
                    H,
                    search_pattern=search_pattern,
                    priority=priority,
                    model_id=model_id,
                    run_date=run_date,
                    product=product,
                    fh=fh,
                )
                inventory = inv_result.inventory
                if inv_result.reason != "ok" or inventory is None or len(inventory) == 0:
                    raise RuntimeError(
                        f"ECMWF EPS direct mean inventory unavailable for {model_id} fh{fh:03d} pattern={search_pattern!r}: {inv_result.reason}"
                    )

                if "type" in inventory.columns:
                    type_series = inventory["type"].astype(str).str.strip().str.lower()
                    direct_inventory = inventory.loc[type_series == "em"]
                else:
                    direct_inventory = inventory.iloc[0:0]
                direct_inventory = _filter_inventory_step(direct_inventory, fh=fh)

                if len(direct_inventory) == 0:
                    raise RuntimeError(
                        f"ECMWF EPS direct mean inventory contained no em record for {model_id} fh{fh:03d} pattern={search_pattern!r}"
                    )

                first_inventory_line = ""
                try:
                    first_inventory_line = _inventory_line_from_row(direct_inventory.iloc[0])
                except Exception:
                    first_inventory_line = ""

                subset_hint: Path | None = None
                try:
                    subset_hint = Path(H.get_localFilePath(search_pattern))
                except Exception:
                    subset_hint = None
                if subset_hint is None:
                    fallback_name = hashlib.sha1(
                        f"{model_id}|{product}|{fh}|{search_pattern}|{priority}|em".encode("utf-8")
                    ).hexdigest()[:16]
                    subset_hint = Path(os.getcwd()) / f"eps_direct_mean_{fallback_name}.grib2"

                subset_path = _aggregation_subset_path(subset_hint, "cartosky_em")
                with _subset_download_lock(subset_path):
                    cached_ok, _cached_size = _subset_file_status(subset_path)
                    if not cached_ok:
                        downloaded_subset = _download_subset_with_inventory_rows(
                            H,
                            inventory=direct_inventory.iloc[0:1],
                            out_path=subset_path,
                            model_id=model_id,
                            product=product,
                            run_date=run_date,
                            fh=fh,
                            priority=priority,
                            bundle_fetch_cache=bundle_fetch_cache,
                        )
                        if downloaded_subset is None:
                            raise RuntimeError(
                                f"ECMWF EPS direct mean subset download failed for {model_id} fh{fh:03d} pattern={search_pattern!r}"
                            )
                    data, crs, transform = _read_grib_raster(subset_path)

                meta = {
                    "inventory_line": first_inventory_line or f"direct:{search_pattern}:em",
                    "search_pattern": str(search_pattern),
                    "fh": int(fh),
                    "product": str(product),
                    "priority": str(priority),
                    "aggregation": "ecmwf_direct_mean",
                    "member_count": 1,
                }
                if return_meta:
                    return data, crs, transform, meta
                return data, crs, transform
            except Exception as exc:
                last_exc = exc
                if sleep_s > 0 and attempt_idx < retries:
                    time.sleep(sleep_s)

    if fallback_to_pf_mean:
        reason = f"{type(last_exc).__name__}: {last_exc}" if last_exc is not None else "unknown"
        logger.warning(
            "ECMWF EPS direct mean unavailable; falling back to PF mean aggregation for %s fh%03d pattern=%s reason=%s",
            model_id,
            int(fh),
            search_pattern,
            reason,
        )
        return _fetch_ecmwf_pf_mean_variable(
            model_id=model_id,
            product=product,
            search_pattern=search_pattern,
            run_date=run_date,
            fh=fh,
            herbie_kwargs=herbie_kwargs,
            bundle_fetch_cache=bundle_fetch_cache,
            return_meta=return_meta,
        )

    if last_exc is not None:
        raise RuntimeError(
            f"ECMWF EPS direct mean fetch failed for {model_id} fh{fh:03d} pattern={search_pattern!r}"
        ) from last_exc
    raise RuntimeError(
        f"ECMWF EPS direct mean fetch failed without a captured exception for {model_id} fh{fh:03d} pattern={search_pattern!r}"
    )


def reset_herbie_runtime_caches_for_tests() -> None:
    """Reset process-local Herbie availability caches (tests only)."""
    global _EPS_FULL_FILE_CACHE_LAST_CLEANUP_TS
    with _IDX_NEGATIVE_CACHE_LOCK:
        _IDX_NEGATIVE_CACHE.clear()
        _IDX_NEGATIVE_LOG_SUPPRESS.clear()
    with _INVENTORY_CACHE_LOCK:
        _INVENTORY_CACHE.clear()
        _INVENTORY_INFLIGHT.clear()
    with _FETCH_RUNTIME_METRICS_LOCK:
        _FETCH_RUNTIME_COUNTERS.clear()
        _FETCH_RUNTIME_TIMERS_MS.clear()
    with _EPS_FULL_FILE_CACHE_CLEANUP_LOCK:
        _EPS_FULL_FILE_CACHE_LAST_CLEANUP_TS = 0.0


def inventory_lines_for_pattern(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    search_pattern: str,
    herbie_kwargs: dict[str, Any] | None = None,
) -> list[str]:
    """Return inventory lines for a pattern with process-local cache/dedupe."""
    from herbie.core import Herbie

    kwargs = {
        "model": model_id,
        "product": product,
        "fxx": fh,
    }
    if herbie_kwargs:
        kwargs.update(herbie_kwargs)

    priority_list = [_priority_normalized(item) for item in _priority_candidates(herbie_kwargs) if str(item).strip()]
    herbie_date = run_date.replace(tzinfo=None) if run_date.tzinfo else run_date
    priority_sequence = list(priority_list)
    priority_idx = 0
    while priority_idx < len(priority_sequence):
        priority = priority_sequence[priority_idx]
        cache_key = _idx_negative_key(
            model_id=model_id,
            run_date=run_date,
            product=product,
            fh=fh,
            priority=priority,
        )
        if _idx_negative_cache_remaining(cache_key) > 0:
            priority_idx += 1
            continue
        run_kwargs = _quiet_herbie_kwargs(kwargs)
        run_kwargs["priority"] = priority
        inv_reason = "unknown"
        try:
            H = Herbie(herbie_date, **run_kwargs)
            idx_ref = getattr(H, "idx", None)
            if not idx_ref:
                inv_reason = "idx_missing"
                _record_and_log_idx_missing(
                    model_id=model_id,
                    run_date=run_date,
                    product=product,
                    fh=fh,
                    priority=priority,
                    search_pattern=search_pattern,
                    source="inventory_lines",
                )
            else:
                inv_result = _inventory_search(
                    H,
                    search_pattern=search_pattern,
                    priority=priority,
                    model_id=model_id,
                    run_date=run_date,
                    product=product,
                    fh=fh,
                )
                inventory = inv_result.inventory
                inv_reason = inv_result.reason
                lines = _inventory_lines_from_rows(inventory)
                if lines:
                    return lines
        except Exception as exc:
            if _is_missing_index_error(exc):
                inv_reason = "idx_missing"
                _record_and_log_idx_missing(
                    model_id=model_id,
                    run_date=run_date,
                    product=product,
                    fh=fh,
                    priority=priority,
                    search_pattern=search_pattern,
                    source="inventory_lines_exception",
                )
            else:
                inv_reason = "idx_unparseable"

        if _is_prs_aws_priority(priority=priority, product=product) and _is_idx_lag_reason(inv_reason):
            _metric_increment("prs_idx_lag_count")
            _metric_increment("source_switch_count")
            _log_source_fallback(
                from_source="prs",
                to_source="nomads",
                reason="idx_lag",
                model_id=model_id,
                run_date=run_date,
                fh=fh,
                var_pattern=search_pattern,
            )
            priority_sequence = _fallback_to_nomads_sequence(priority_sequence, current_index=priority_idx)

        priority_idx += 1
    return []


def product_hour_has_any_idx(
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    fh: int,
    herbie_kwargs: dict[str, Any] | None = None,
    allow_grib_without_idx: bool = False,
) -> bool:
    """Cheap run-hour readiness probe using IDX, with optional GRIB fallback."""
    from herbie.core import Herbie

    kwargs = {
        "model": model_id,
        "product": product,
        "fxx": fh,
    }
    if herbie_kwargs:
        kwargs.update(herbie_kwargs)

    priority_list = _priority_candidates(herbie_kwargs)
    herbie_date = run_date.replace(tzinfo=None) if run_date.tzinfo else run_date
    all_cached_missing = True
    for priority in priority_list:
        H = None
        if not allow_grib_without_idx:
            cache_key = _idx_negative_key(
                model_id=model_id,
                run_date=run_date,
                product=product,
                fh=fh,
                priority=priority,
            )
            if _idx_negative_cache_remaining(cache_key) > 0:
                continue
        all_cached_missing = False
        run_kwargs = _quiet_herbie_kwargs(kwargs)
        run_kwargs["priority"] = priority
        try:
            H = Herbie(herbie_date, **run_kwargs)
            idx_ref = getattr(H, "idx", None)
        except Exception as exc:
            if _is_missing_index_error(exc):
                if allow_grib_without_idx and getattr(H, "grib", None):
                    logger.info(
                        "Herbie readiness probe using GRIB fallback (%s %s fh%03d; priority=%s): idx exception but GRIB exists",
                        model_id,
                        product,
                        int(fh),
                        priority,
                    )
                    return True
                _record_and_log_idx_missing(
                    model_id=model_id,
                    run_date=run_date,
                    product=product,
                    fh=fh,
                    priority=priority,
                    search_pattern="(readiness_probe)",
                    source="readiness_probe_exception",
                )
                continue
            logger.debug(
                "Herbie readiness probe failed (%s %s fh%03d; priority=%s): %s",
                model_id,
                product,
                int(fh),
                priority,
                exc,
            )
            return True
        if not idx_ref:
            if allow_grib_without_idx and getattr(H, "grib", None):
                logger.info(
                    "Herbie readiness probe using GRIB fallback (%s %s fh%03d; priority=%s): idx missing but GRIB exists",
                    model_id,
                    product,
                    int(fh),
                    priority,
                )
                return True
            _record_and_log_idx_missing(
                model_id=model_id,
                run_date=run_date,
                product=product,
                fh=fh,
                priority=priority,
                search_pattern="(readiness_probe)",
                source="readiness_probe",
            )
            continue
        return True
    if all_cached_missing:
        logger.warning(
            "Herbie readiness probe short-circuited (%s %s fh%03d): all priorities cached idx-missing",
            model_id,
            product,
            int(fh),
        )
    return False


def _is_missing_index_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "no index file was found for none" in text


def _is_missing_file_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "no such file or directory" in text


def _is_unsupported_file_format_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "not recognized as being in a supported file format" in text
        or "no raster dataset was successfully identified" in text
    )


def _is_grib_not_found_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "grib2 file not found" in text


def _default_subset_target_path(
    H: Any,
    *,
    model_id: str,
    product: str,
    run_date: datetime,
    priority: str,
    fh: int,
    search_pattern: str,
) -> Path:
    try:
        return Path(H.get_localFilePath(search_pattern))
    except Exception:
        return Path("/tmp") / (
            "twf_subset_"
            + hashlib.sha1(
                f"{model_id}|{product}|{_run_id_from_date(run_date)}|{priority}|{fh}|{search_pattern}".encode("utf-8")
            ).hexdigest()
            + ".grib2"
        )


def _parse_float_tag(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed):
        return None
    return parsed


def _bool_from_env(name: str | tuple[str, ...], default: bool = False) -> bool:
    raw = _env_value(name).lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _grib_disk_cache_lock_enabled() -> bool:
    return _bool_from_env(ENV_GRIB_DISK_CACHE_LOCK, False)


def _log_disk_lock_wait_event() -> None:
    global _GRIB_DISK_CACHE_LOCK_WAITS
    _GRIB_DISK_CACHE_LOCK_WAITS += 1
    waits = _GRIB_DISK_CACHE_LOCK_WAITS
    if waits <= 5 or waits % 25 == 0:
        logger.info("grib_disk_cache lock_waits=%d", waits)


def _subset_file_status(path: Path) -> tuple[bool, int]:
    size = 0
    try:
        if path.is_file():
            size = int(path.stat().st_size)
            return size > 0, size
    except OSError:
        pass
    return False, size


@contextmanager
def _path_download_lock(path: Path):
    if not _grib_disk_cache_lock_enabled():
        yield
        return

    try:
        import fcntl
    except ImportError:
        logger.warning("GRIB disk-cache lock requested but fcntl is unavailable; proceeding unlocked")
        yield
        return

    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a+")
    waited = False
    deadline = time.monotonic() + DEFAULT_GRIB_DISK_LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                if waited:
                    _log_disk_lock_wait_event()
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for GRIB subset lock: {lock_path}")
                waited = True
                time.sleep(DEFAULT_GRIB_DISK_LOCK_POLL_SECONDS)
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_file.close()


@contextmanager
def _subset_download_lock(path: Path):
    with _path_download_lock(path):
        yield


def _precheck_subset_available(
    H: Any,
    *,
    model_id: str,
    run_date: datetime,
    product: str,
    fh: int,
    search_pattern: str,
    priority: str,
    attempt_idx: int,
    retries: int,
) -> tuple[bool, str]:
    cache_key = _idx_negative_key(
        model_id=model_id,
        run_date=run_date,
        product=product,
        fh=fh,
        priority=priority,
    )
    cache_remaining = _idx_negative_cache_remaining(cache_key)
    if cache_remaining > 0.0:
        return False, "idx_missing_cached"

    try:
        idx_ref = getattr(H, "idx", None)
    except Exception as exc:
        if _is_missing_index_error(exc):
            _record_and_log_idx_missing(
                model_id=model_id,
                run_date=run_date,
                product=product,
                fh=fh,
                priority=priority,
                search_pattern=search_pattern,
                source="precheck_idx_exception",
            )
            return False, "idx_missing"
        logger.debug(
            "Herbie precheck idx introspection failed (%s fh%03d %s; priority=%s): %s",
            model_id,
            fh,
            search_pattern,
            priority,
            exc,
        )
        return True, "ok"

    if not idx_ref:
        _record_and_log_idx_missing(
            model_id=model_id,
            run_date=run_date,
            product=product,
            fh=fh,
            priority=priority,
            search_pattern=search_pattern,
            source="precheck_no_idx",
        )
        return False, "idx_missing"

    try:
        inv_result = _inventory_search(
            H,
            search_pattern=search_pattern,
            priority=priority,
            model_id=model_id,
            run_date=run_date,
            product=product,
            fh=fh,
        )
        if inv_result.reason == "ok":
            return True, "ok"
        if inv_result.reason == "idx_missing":
            _record_and_log_idx_missing(
                model_id=model_id,
                run_date=run_date,
                product=product,
                fh=fh,
                priority=priority,
                search_pattern=search_pattern,
                source="precheck_inventory_idx_missing",
            )
            return False, "idx_missing"
        if inv_result.reason == "pattern_missing":
            logger.warning(
                "Herbie precheck unavailable (%s fh%03d %s; priority=%s; attempt=%d/%d): inventory missing pattern",
                model_id,
                fh,
                search_pattern,
                priority,
                attempt_idx,
                retries,
            )
            return False, "pattern_missing"
        if inv_result.reason == "idx_empty":
            logger.warning(
                "Herbie precheck unavailable (%s fh%03d %s; priority=%s; attempt=%d/%d): empty idx",
                model_id,
                fh,
                search_pattern,
                priority,
                attempt_idx,
                retries,
            )
            return False, "idx_empty"
        if inv_result.reason == "idx_unparseable":
            logger.warning(
                "Herbie precheck unavailable (%s fh%03d %s; priority=%s; attempt=%d/%d): idx unparseable",
                model_id,
                fh,
                search_pattern,
                priority,
                attempt_idx,
                retries,
            )
            return False, "idx_unparseable"
        logger.warning(
            "Herbie precheck unavailable (%s fh%03d %s; priority=%s; attempt=%d/%d): no inventory match",
            model_id,
            fh,
            search_pattern,
            priority,
            attempt_idx,
            retries,
        )
        return False, "no_inventory"
    except Exception as exc:
        if _is_missing_index_error(exc):
            _record_and_log_idx_missing(
                model_id=model_id,
                run_date=run_date,
                product=product,
                fh=fh,
                priority=priority,
                search_pattern=search_pattern,
                source="precheck_inventory_exception",
            )
            return False, "idx_missing"
        logger.debug(
            "Herbie precheck inventory check failed; continuing with subset download (%s fh%03d %s; priority=%s): %s",
            model_id,
            fh,
            search_pattern,
            priority,
            exc,
        )
        return True, "ok"


def _inventory_line_from_row(row: Any) -> str:
    preferred_keys = (
        "search_this",
        "line",
        "inventory_line",
        "grib_message",
        "message",
    )
    for key in preferred_keys:
        try:
            value = row.get(key)
        except Exception:
            value = None
        if value is None:
            continue
        text = " ".join(str(value).split()).strip()
        if text:
            return text

    try:
        if hasattr(row, "to_dict"):
            row_dict = row.to_dict()
            pieces = [
                " ".join(str(value).split()).strip()
                for value in row_dict.values()
                if str(value).strip()
            ]
            joined = " | ".join(piece for piece in pieces if piece)
            if joined:
                return joined
    except Exception:
        pass
    return ""


def _inventory_meta_from_herbie(
    H: Any,
    *,
    search_pattern: str,
    fh: int,
    product: str,
    model_id: str = "",
    run_date: datetime | None = None,
    priority: str = "",
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "inventory_line": "",
        "search_pattern": str(search_pattern),
        "fh": int(fh),
        "product": str(product),
    }
    inv_result = _inventory_search(
        H,
        search_pattern=search_pattern,
        priority=priority,
        model_id=model_id,
        run_date=run_date,
        product=product,
        fh=fh,
    )
    inventory = inv_result.inventory
    if inv_result.reason != "ok" or inventory is None or len(inventory) == 0:
        return meta

    try:
        row = inventory.iloc[0]
    except Exception:
        return meta

    meta["inventory_line"] = _inventory_line_from_row(row)
    return meta


def _inventory_primary_byte_range(
    H: Any,
    *,
    search_pattern: str,
    model_id: str,
    run_date: datetime,
    product: str,
    fh: int,
    priority: str,
    force_inventory_refresh: bool = False,
) -> tuple[str, int, int] | None:
    try:
        inv_result = _inventory_search(
            H,
            search_pattern=search_pattern,
            priority=priority,
            model_id=model_id,
            run_date=run_date,
            product=product,
            fh=fh,
            force_inventory_refresh=force_inventory_refresh,
        )
        inv = inv_result.inventory
    except Exception:
        inv = None
    if inv is None or len(inv) == 0:
        return None

    row = inv.iloc[0]
    try:
        start_byte = int(row["start_byte"])
    except Exception:
        return None

    end_byte: int | None = None
    try:
        raw_end = row.get("end_byte")
        if raw_end is not None and np.isfinite(raw_end):
            parsed_end = int(raw_end)
            if parsed_end >= start_byte:
                end_byte = parsed_end
    except Exception:
        end_byte = None

    if end_byte is None:
        try:
            idx_ref = getattr(H, "idx", None)
            idx_key = _inventory_cache_key_from_idx(
                idx_ref,
                priority=priority,
                model_id=model_id,
                run_date=run_date,
                product=product,
                fh=fh,
                grib_ref=getattr(H, "grib", None),
            )
            full_idx = _inventory_index_dataframe(H, idx_key=idx_key) if idx_key else None
            if full_idx is None:
                return None
            starts = full_idx["start_byte"].dropna().astype(int)
            higher = starts[starts > start_byte]
            if len(higher) > 0:
                candidate_end = int(higher.min() - 1)
                if candidate_end >= start_byte:
                    end_byte = candidate_end
        except Exception:
            end_byte = None

    if end_byte is None or end_byte < start_byte:
        return None

    source = getattr(H, "grib", None)
    if source is None:
        return None
    return str(source), start_byte, end_byte


def _network_fetch_range_bytes(source_url: str, *, start_byte: int, end_byte: int) -> bytes:
    headers = {"Range": f"bytes={start_byte}-{end_byte}"}
    response = requests.get(source_url, headers=headers, timeout=45)
    response.raise_for_status()
    data = bytes(response.content)
    response.close()
    return data


def _grib_payload_invalid_reason(payload: bytes) -> str | None:
    if not payload:
        return "empty"
    if len(payload) < 16:
        return f"too_small:{len(payload)}"
    header = bytes(payload[:64])
    if b"GRIB" in header:
        return None
    stripped = header.lstrip()
    lowered = stripped[:32].lower()
    if lowered.startswith((b"<!doctype", b"<html", b"<?xml", b"<error", b"{", b"[")):
        return "looks_like_text_error"
    return "missing_grib_signature"


def _validate_grib_range_payload(
    payload: bytes,
    *,
    source: str,
    source_url: str,
    model_id: str,
    run_date: datetime,
    fh: int,
    start_byte: int,
    end_byte: int,
) -> None:
    reason = _grib_payload_invalid_reason(payload)
    if reason is None:
        return
    _metric_increment("invalid_grib_range_payload")
    raise _InvalidGribSubsetError(
        f"Invalid GRIB range payload source={source} model={model_id} "
        f"run={_run_id_from_date(run_date)} fh{int(fh):03d} "
        f"range={int(start_byte)}-{int(end_byte)} url_hash={_url_hash(source_url)} "
        f"size={len(payload)} reason={reason}"
    )


def _fetch_range_bytes(
    *,
    source: str,
    source_url: str,
    model_id: str,
    run_date: datetime,
    fh: int,
    start_byte: int,
    end_byte: int,
    bundle_fetch_cache: BundleFetchCache | None,
    require_grib_payload: bool = False,
) -> bytes:
    total_start = time.monotonic()
    lookup_start = time.monotonic()
    cache_key = _range_cache_key(
        source=source,
        model_id=model_id,
        run_date=run_date,
        fh=fh,
        url=source_url,
        start_byte=start_byte,
        end_byte=end_byte,
    )
    _metric_observe_ms("cache_lookup_ms", (time.monotonic() - lookup_start) * 1000.0)

    def _fetch_from_network() -> bytes:
        http_start = time.monotonic()
        payload = _network_fetch_range_bytes(
            source_url,
            start_byte=start_byte,
            end_byte=end_byte,
        )
        if require_grib_payload:
            _validate_grib_range_payload(
                payload,
                source=source,
                source_url=source_url,
                model_id=model_id,
                run_date=run_date,
                fh=fh,
                start_byte=start_byte,
                end_byte=end_byte,
            )
        _metric_observe_ms("fetch_http_ms", (time.monotonic() - http_start) * 1000.0)
        return payload

    if bundle_fetch_cache is None:
        _metric_increment("fetch_cache_miss")
        logger.info(
            "FETCH_CACHE event=miss source=%s model=%s run=%s fh=%03d range=%d-%d url_hash=%s reason=no_bundle_cache",
            source,
            model_id,
            _run_id_from_date(run_date),
            int(fh),
            int(start_byte),
            int(end_byte),
            _url_hash(source_url),
        )
        payload = _fetch_from_network()
        _metric_observe_ms("fetch_total_ms", (time.monotonic() - total_start) * 1000.0)
        return payload

    expected_size = max(0, int(end_byte) - int(start_byte) + 1)
    cacheable = expected_size <= max(1, int(bundle_fetch_cache.max_cacheable_bytes))
    if not cacheable:
        _metric_increment("fetch_cache_skip_too_large")
    payload, event, evicted = bundle_fetch_cache.get_or_fetch(
        cache_key,
        fetcher=_fetch_from_network,
        cacheable=cacheable,
        expected_size=expected_size if expected_size > 0 else None,
    )
    if require_grib_payload:
        try:
            _validate_grib_range_payload(
                payload,
                source=source,
                source_url=source_url,
                model_id=model_id,
                run_date=run_date,
                fh=fh,
                start_byte=start_byte,
                end_byte=end_byte,
            )
        except _InvalidGribSubsetError:
            if bundle_fetch_cache.evict(cache_key):
                _metric_increment("fetch_cache_evict_invalid_grib")
            raise
    if event in {"hit", "wait"}:
        _metric_increment("fetch_cache_hit")
        logger.info(
            "FETCH_CACHE event=hit source=%s model=%s run=%s fh=%03d range=%d-%d url_hash=%s mode=%s",
            source,
            model_id,
            _run_id_from_date(run_date),
            int(fh),
            int(start_byte),
            int(end_byte),
            _url_hash(source_url),
            event,
        )
    else:
        _metric_increment("fetch_cache_miss")
        logger.info(
            "FETCH_CACHE event=miss source=%s model=%s run=%s fh=%03d range=%d-%d url_hash=%s cacheable=%s",
            source,
            model_id,
            _run_id_from_date(run_date),
            int(fh),
            int(start_byte),
            int(end_byte),
            _url_hash(source_url),
            "true" if cacheable else "false",
        )
        if cacheable:
            if (
                len(payload) == expected_size
                and len(payload) <= int(bundle_fetch_cache.max_cacheable_bytes)
                and len(payload) <= int(bundle_fetch_cache.max_bytes)
            ):
                _metric_increment("fetch_cache_store")
            else:
                _metric_increment("fetch_cache_skip_too_large")
        if evicted > 0:
            _metric_increment("fetch_cache_evict", evicted)
    _metric_observe_ms("fetch_total_ms", (time.monotonic() - total_start) * 1000.0)
    return payload


def _download_subset_with_inventory_byte_range(
    H: Any,
    *,
    search_pattern: str,
    out_path: Path,
    model_id: str,
    run_date: datetime,
    product: str,
    fh: int,
    priority: str,
    bundle_fetch_cache: BundleFetchCache | None,
    force_inventory_refresh: bool = False,
) -> Path | None:
    source_url = str(getattr(H, "grib", "") or "")
    cached_full_path = _maybe_get_eps_full_grib_path(
        H,
        model_id=model_id,
        product=product,
        run_date=run_date,
        fh=fh,
        priority=priority,
    )
    if cached_full_path is not None:
        source_url = str(cached_full_path)
    primary_range = _inventory_primary_byte_range(
        H,
        search_pattern=search_pattern,
        model_id=model_id,
        run_date=run_date,
        product=product,
        fh=fh,
        priority=priority,
        force_inventory_refresh=force_inventory_refresh,
    )
    if primary_range is None:
        return None

    primary_source_url, start_byte, end_byte = primary_range
    if not source_url:
        source_url = primary_source_url
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if source_url.startswith(("http://", "https://")):
            payload = _fetch_range_bytes(
                source=priority,
                source_url=source_url,
                model_id=model_id,
                run_date=run_date,
                fh=fh,
                start_byte=start_byte,
                end_byte=end_byte,
                bundle_fetch_cache=bundle_fetch_cache,
                require_grib_payload=True,
            )
        else:
            with open(source_url, "rb") as src:
                src.seek(start_byte)
                payload = src.read(end_byte - start_byte + 1)

        if not payload:
            return None
        _validate_grib_range_payload(
            payload,
            source=priority,
            source_url=source_url,
            model_id=model_id,
            run_date=run_date,
            fh=fh,
            start_byte=start_byte,
            end_byte=end_byte,
        )
        out_path.write_bytes(payload)
        subset_ok, _subset_size = _subset_file_status(out_path)
        if not subset_ok:
            return None
        return out_path
    except _InvalidGribSubsetError:
        try:
            if out_path.exists():
                out_path.unlink()
        except OSError:
            pass
        raise
    except Exception:
        return None


def _manual_subset_download_with_corrected_range(
    H: Any,
    *,
    search_pattern: str,
    out_path: Path,
    model_id: str,
    run_date: datetime,
    product: str,
    fh: int,
    priority: str,
    bundle_fetch_cache: BundleFetchCache | None = None,
    force_inventory_refresh: bool = False,
) -> Path | None:
    """Fallback subset fetch for edge-case index rows with duplicate start bytes.

    Some upstream IDX inventories contain duplicate `start_byte` rows (for example
    NAM 10m vector components). In those cases the first row can end up with an
    invalid computed range in Herbie's subset path and produce 0-byte output.
    This fallback computes `end_byte` from the next distinct start byte.
    """
    subset_path = _download_subset_with_inventory_byte_range(
        H,
        search_pattern=search_pattern,
        out_path=out_path,
        model_id=model_id,
        run_date=run_date,
        product=product,
        fh=fh,
        priority=priority,
        bundle_fetch_cache=bundle_fetch_cache,
        force_inventory_refresh=force_inventory_refresh,
    )
    if subset_path is None:
        logger.warning(
            "Manual subset fallback download failed (%s fh%03d %s; priority=%s): inventory byte-range unavailable",
            model_id,
            fh,
            search_pattern,
            priority,
        )
        return None
    logger.info(
        "Downloaded GRIB via manual byte-range fallback: %s (%s fh%03d %s; priority=%s)",
        subset_path.name,
        model_id,
        fh,
        search_pattern,
        priority,
    )
    return subset_path


@overload
def fetch_variable(
    model_id: str,
    product: str,
    search_pattern: str,
    run_date: datetime,
    fh: int,
    *,
    herbie_kwargs: dict[str, Any] | None = ...,
    bundle_fetch_cache: BundleFetchCache | None = ...,
    return_meta: Literal[False] = ...,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine]: ...


@overload
def fetch_variable(
    model_id: str,
    product: str,
    search_pattern: str,
    run_date: datetime,
    fh: int,
    *,
    herbie_kwargs: dict[str, Any] | None = ...,
    bundle_fetch_cache: BundleFetchCache | None = ...,
    return_meta: Literal[True],
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]]: ...


def fetch_variable(
    model_id: str,
    product: str,
    search_pattern: str,
    run_date: datetime,
    fh: int,
    *,
    herbie_kwargs: dict[str, Any] | None = None,
    bundle_fetch_cache: BundleFetchCache | None = None,
    return_meta: bool = False,
    _retry_on_invalid_subset: bool = True,
) -> tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine] | tuple[np.ndarray, rasterio.crs.CRS, rasterio.transform.Affine, dict[str, Any]]:
    """Fetch a single GRIB variable via Herbie and return its data.

    Downloads the GRIB subset matching *search_pattern*, then opens it
    with rasterio to extract the data array, CRS, and affine transform
    in the GRIB's native projection.

    Parameters
    ----------
    model_id : str
        Model name for Herbie (e.g. "hrrr", "gfs").
    product : str
        Herbie product string (e.g. "sfc").
    search_pattern : str
        Herbie search/regex for the GRIB message
        (e.g. ":TMP:2 m above ground:").
    run_date : datetime
        Model run initialization time (UTC).
    fh : int
        Forecast hour.
    herbie_kwargs : dict, optional
        Extra keyword arguments for the Herbie constructor
        (e.g. priority, save_dir, overwrite).

    Returns
    -------
    data : np.ndarray
        2-D float32 array in the GRIB's native projection.
    crs : rasterio.crs.CRS
        Source coordinate reference system.
    transform : rasterio.transform.Affine
        Source affine transform.

    Raises
    ------
    RuntimeError
        If the GRIB download fails or produces no data.
    """
    from herbie.core import Herbie  # lazy — not always installed

    raw_herbie_kwargs = dict(herbie_kwargs or {})
    fetch_aggregation = str(raw_herbie_kwargs.pop("_cartosky_fetch_aggregation", "")).strip().lower()
    if fetch_aggregation == "ecmwf_direct_mean_or_pf_mean":
        return _fetch_ecmwf_direct_mean_variable(
            model_id=model_id,
            product=product,
            search_pattern=search_pattern,
            run_date=run_date,
            fh=fh,
            herbie_kwargs=raw_herbie_kwargs,
            bundle_fetch_cache=bundle_fetch_cache,
            return_meta=return_meta,
            fallback_to_pf_mean=True,
        )
    if fetch_aggregation == "ecmwf_direct_mean":
        return _fetch_ecmwf_direct_mean_variable(
            model_id=model_id,
            product=product,
            search_pattern=search_pattern,
            run_date=run_date,
            fh=fh,
            herbie_kwargs=raw_herbie_kwargs,
            bundle_fetch_cache=bundle_fetch_cache,
            return_meta=return_meta,
        )
    if fetch_aggregation == "ecmwf_pf_mean":
        return _fetch_ecmwf_pf_mean_variable(
            model_id=model_id,
            product=product,
            search_pattern=search_pattern,
            run_date=run_date,
            fh=fh,
            herbie_kwargs=raw_herbie_kwargs,
            bundle_fetch_cache=bundle_fetch_cache,
            return_meta=return_meta,
        )

    kwargs: dict[str, Any] = {
        "model": model_id,
        "product": product,
        "fxx": fh,
    }
    if raw_herbie_kwargs:
        kwargs.update(raw_herbie_kwargs)

    # Herbie expects a tz-naive datetime (assumes UTC internally).
    # Strip tzinfo to avoid pandas tz-naive vs tz-aware comparison errors.
    herbie_date = run_date.replace(tzinfo=None) if run_date.tzinfo else run_date

    priority_list = [_priority_normalized(item) for item in _priority_candidates(raw_herbie_kwargs) if str(item).strip()]
    retries = _retry_count()
    sleep_s = _retry_sleep_seconds()
    lock_enabled = _grib_disk_cache_lock_enabled()

    last_exc: Exception | None = None
    saw_missing_index = False
    saw_missing_subset_file = False
    saw_non_transient_failure = False
    grib_path: Path | None = None
    selected_meta: dict[str, Any] = {
        "inventory_line": "",
        "search_pattern": str(search_pattern),
        "fh": int(fh),
        "product": str(product),
    }
    prs_idx_lag_reason: str | None = None
    prs_fallback_triggered = False
    skipped_cached_priorities: list[tuple[str, float]] = []
    priority_sequence = list(priority_list)
    priority_idx = 0
    while priority_idx < len(priority_sequence):
        priority = priority_sequence[priority_idx]
        priority_cache_key = _idx_negative_key(
            model_id=model_id,
            run_date=run_date,
            product=product,
            fh=fh,
            priority=priority,
        )
        remaining_ttl = _idx_negative_cache_remaining(priority_cache_key)
        if remaining_ttl > 0.0:
            skipped_cached_priorities.append((priority, remaining_ttl))
            priority_idx += 1
            continue

        is_prs_aws = _is_prs_aws_priority(priority=priority, product=product)
        attempts_for_priority = 1 if is_prs_aws else retries
        force_nomads_after_prs_idx_lag = False

        for attempt_idx in range(1, attempts_for_priority + 1):
            run_kwargs = _quiet_herbie_kwargs(kwargs)
            run_kwargs["priority"] = priority
            subset_target: Path | None = None
            try:
                H = Herbie(herbie_date, **run_kwargs)
                precheck_ok, precheck_reason = _precheck_subset_available(
                    H,
                    model_id=model_id,
                    run_date=run_date,
                    product=product,
                    fh=fh,
                    search_pattern=search_pattern,
                    priority=priority,
                    attempt_idx=attempt_idx,
                    retries=attempts_for_priority,
                )
                if not precheck_ok:
                    if precheck_reason in {"idx_missing", "idx_missing_cached"}:
                        saw_missing_index = True
                        if is_prs_aws:
                            prs_idx_lag_reason = precheck_reason
                            force_nomads_after_prs_idx_lag = True
                        break
                    if is_prs_aws and _is_idx_lag_reason(precheck_reason):
                        saw_missing_index = True
                        prs_idx_lag_reason = precheck_reason
                        force_nomads_after_prs_idx_lag = True
                        break
                    saw_missing_subset_file = True
                    if sleep_s > 0 and attempt_idx < attempts_for_priority:
                        time.sleep(sleep_s)
                    continue
                attempt_meta = _inventory_meta_from_herbie(
                    H,
                    search_pattern=search_pattern,
                    fh=fh,
                    product=product,
                    model_id=model_id,
                    run_date=run_date,
                    priority=priority,
                )
                subset_target = _default_subset_target_path(
                    H,
                    model_id=model_id,
                    product=product,
                    run_date=run_date,
                    priority=priority,
                    fh=fh,
                    search_pattern=search_pattern,
                )
                subset_hint: Path | None = None
                if lock_enabled:
                    subset_hint = subset_target

                if lock_enabled and subset_hint is not None:
                    with _subset_download_lock(subset_hint):
                        cached_ok, cached_size = _subset_file_status(subset_hint)
                        if cached_ok:
                            grib_path = subset_hint
                            logger.info(
                                "Reusing cached GRIB: %s (%s fh%03d %s; priority=%s; attempt=%d/%d; size=%d)",
                                grib_path.name,
                                model_id,
                                fh,
                                search_pattern,
                                priority,
                                attempt_idx,
                                attempts_for_priority,
                                cached_size,
                            )
                            selected_meta = attempt_meta
                            break

                        subset_path = _download_subset_with_inventory_byte_range(
                            H,
                            search_pattern=search_pattern,
                            out_path=subset_hint,
                            model_id=model_id,
                            run_date=run_date,
                            product=product,
                            fh=fh,
                            priority=priority,
                            bundle_fetch_cache=bundle_fetch_cache,
                        )
                        if subset_path is None:
                            subset_path = H.download(search_pattern, errors="raise", overwrite=False)
                        if subset_path is None:
                            saw_missing_subset_file = True
                            logger.warning(
                                "Herbie subset unavailable: download returned None (%s fh%03d %s; priority=%s; attempt=%d/%d)",
                                model_id,
                                fh,
                                search_pattern,
                                priority,
                                attempt_idx,
                                attempts_for_priority,
                            )
                            if sleep_s > 0 and attempt_idx < attempts_for_priority:
                                time.sleep(sleep_s)
                            continue
                        subset_candidate = Path(subset_path)
                        subset_ok, subset_size = _subset_file_status(subset_candidate)
                        if not subset_ok:
                            saw_missing_subset_file = True
                            logger.warning(
                                "Herbie subset file missing/empty after download (%s fh%03d %s; priority=%s; attempt=%d/%d): %s (size=%d)",
                                model_id,
                                fh,
                                search_pattern,
                                priority,
                                attempt_idx,
                                attempts_for_priority,
                                subset_candidate,
                                subset_size,
                            )
                            manual_subset = _manual_subset_download_with_corrected_range(
                                H,
                                search_pattern=search_pattern,
                                out_path=subset_candidate,
                                model_id=model_id,
                                run_date=run_date,
                                product=product,
                                fh=fh,
                                priority=priority,
                                bundle_fetch_cache=bundle_fetch_cache,
                            )
                            if manual_subset is not None:
                                grib_path = manual_subset
                                selected_meta = attempt_meta
                                break
                            try:
                                if subset_candidate.exists():
                                    subset_candidate.unlink()
                            except OSError:
                                pass
                            if sleep_s > 0 and attempt_idx < attempts_for_priority:
                                time.sleep(sleep_s)
                            continue

                        grib_path = subset_candidate
                        logger.info(
                            "Downloaded GRIB: %s (%s fh%03d %s; priority=%s; attempt=%d/%d)",
                            grib_path.name,
                            model_id,
                            fh,
                            search_pattern,
                            priority,
                            attempt_idx,
                            attempts_for_priority,
                        )
                        selected_meta = attempt_meta
                        break
                else:
                    subset_path = _download_subset_with_inventory_byte_range(
                        H,
                        search_pattern=search_pattern,
                        out_path=subset_target,
                        model_id=model_id,
                        run_date=run_date,
                        product=product,
                        fh=fh,
                        priority=priority,
                        bundle_fetch_cache=bundle_fetch_cache,
                    )
                    if subset_path is None:
                        subset_path = H.download(search_pattern, errors="raise", overwrite=True)
                    if subset_path is None:
                        saw_missing_subset_file = True
                        logger.warning(
                            "Herbie subset unavailable: download returned None (%s fh%03d %s; priority=%s; attempt=%d/%d)",
                            model_id,
                            fh,
                            search_pattern,
                            priority,
                            attempt_idx,
                            attempts_for_priority,
                        )
                        if sleep_s > 0 and attempt_idx < attempts_for_priority:
                            time.sleep(sleep_s)
                        continue
                    subset_candidate = Path(subset_path)
                    subset_ok, subset_size = _subset_file_status(subset_candidate)

                    if not subset_ok:
                        saw_missing_subset_file = True
                        logger.warning(
                            "Herbie subset file missing/empty after download (%s fh%03d %s; priority=%s; attempt=%d/%d): %s (size=%d)",
                            model_id,
                            fh,
                            search_pattern,
                            priority,
                            attempt_idx,
                            attempts_for_priority,
                            subset_candidate,
                            subset_size,
                        )
                        manual_subset = _manual_subset_download_with_corrected_range(
                            H,
                            search_pattern=search_pattern,
                            out_path=subset_candidate,
                            model_id=model_id,
                            run_date=run_date,
                            product=product,
                            fh=fh,
                            priority=priority,
                            bundle_fetch_cache=bundle_fetch_cache,
                        )
                        if manual_subset is not None:
                            grib_path = manual_subset
                            selected_meta = attempt_meta
                            break
                        try:
                            if subset_candidate.exists():
                                subset_candidate.unlink()
                        except OSError:
                            pass
                        if sleep_s > 0 and attempt_idx < attempts_for_priority:
                            time.sleep(sleep_s)
                        continue

                    grib_path = subset_candidate
                    logger.info(
                        "Downloaded GRIB: %s (%s fh%03d %s; priority=%s; attempt=%d/%d)",
                        grib_path.name,
                        model_id,
                        fh,
                        search_pattern,
                        priority,
                        attempt_idx,
                        attempts_for_priority,
                    )
                    selected_meta = attempt_meta
                    break
            except Exception as exc:
                last_exc = exc
                if isinstance(exc, _InvalidGribSubsetError):
                    saw_missing_subset_file = True
                    logger.warning(
                        "Herbie subset unavailable: invalid GRIB byte-range payload (%s fh%03d %s; priority=%s; attempt=%d/%d): %s",
                        model_id,
                        fh,
                        search_pattern,
                        priority,
                        attempt_idx,
                        attempts_for_priority,
                        exc,
                    )
                    if sleep_s > 0 and attempt_idx < attempts_for_priority:
                        time.sleep(sleep_s)
                    continue
                if _is_missing_index_error(exc):
                    saw_missing_index = True
                    _record_and_log_idx_missing(
                        model_id=model_id,
                        run_date=run_date,
                        product=product,
                        fh=fh,
                        priority=priority,
                        search_pattern=search_pattern,
                        source="subset_exception_missing_idx",
                    )
                    if is_prs_aws:
                        prs_idx_lag_reason = "idx_missing_exception"
                        force_nomads_after_prs_idx_lag = True
                    break
                if _is_grib_not_found_error(exc):
                    manual_subset = None
                    if 'H' in locals():
                        manual_out_path = subset_target
                        if manual_out_path is None:
                            manual_out_path = _default_subset_target_path(
                                H,
                                model_id=model_id,
                                product=product,
                                run_date=run_date,
                                priority=priority,
                                fh=fh,
                                search_pattern=search_pattern,
                            )
                        try:
                            manual_subset = _manual_subset_download_with_corrected_range(
                                H,
                                search_pattern=search_pattern,
                                out_path=manual_out_path,
                                model_id=model_id,
                                run_date=run_date,
                                product=product,
                                fh=fh,
                                priority=priority,
                                bundle_fetch_cache=bundle_fetch_cache,
                                force_inventory_refresh=True,
                            )
                        except _InvalidGribSubsetError:
                            raise
                        except Exception:
                            manual_subset = None
                    if manual_subset is not None:
                        grib_path = manual_subset
                        try:
                            selected_meta = attempt_meta
                        except Exception:
                            pass
                        logger.info(
                            "Recovered GRIB after Herbie download miss via manual byte-range fallback (%s fh%03d %s; priority=%s; attempt=%d/%d)",
                            model_id,
                            fh,
                            search_pattern,
                            priority,
                            attempt_idx,
                            attempts_for_priority,
                        )
                        break
                    saw_missing_subset_file = True
                    logger.warning(
                        "Herbie subset unavailable (%s fh%03d %s; priority=%s; attempt=%d/%d): grib not found",
                        model_id,
                        fh,
                        search_pattern,
                        priority,
                        attempt_idx,
                        attempts_for_priority,
                    )
                    if sleep_s > 0 and attempt_idx < attempts_for_priority:
                        time.sleep(sleep_s)
                    continue
                saw_non_transient_failure = True
                logger.warning(
                    "Herbie subset fetch failed (%s fh%03d %s; priority=%s; attempt=%d/%d): %s",
                    model_id,
                    fh,
                    search_pattern,
                    priority,
                    attempt_idx,
                    attempts_for_priority,
                    exc,
                )
                if sleep_s > 0 and attempt_idx < attempts_for_priority:
                    time.sleep(sleep_s)
        if grib_path is not None:
            break
        if force_nomads_after_prs_idx_lag:
            prs_fallback_triggered = True
            _metric_increment("prs_idx_lag_count")
            _metric_increment("source_switch_count")
            _log_source_fallback(
                from_source="prs",
                to_source="nomads",
                reason="idx_lag",
                model_id=model_id,
                run_date=run_date,
                fh=fh,
                var_pattern=search_pattern,
            )
            priority_sequence = _fallback_to_nomads_sequence(priority_sequence, current_index=priority_idx)
        priority_idx += 1

    if grib_path is None:
        if prs_fallback_triggered:
            nomads_error = str(last_exc) if last_exc is not None else "unavailable"
            raise HerbieTransientUnavailableError(
                f"Herbie PRS idx-lag fallback failed (aws->nomads) for {model_id} "
                f"run={_run_id_from_date(run_date)} product={product} fh{fh:03d} "
                f"pattern={search_pattern!r}; aws_reason={prs_idx_lag_reason or 'idx_lag'}; "
                f"nomads_error={nomads_error}"
            ) from last_exc
        if len(skipped_cached_priorities) == len(priority_list) and priority_list:
            suppress_ttl = min(ttl for _, ttl in skipped_cached_priorities)
            _log_idx_missing_once(
                model_id=model_id,
                run_date=run_date,
                product=product,
                fh=fh,
                priority="all",
                search_pattern=search_pattern,
                ttl_seconds=suppress_ttl,
                source="cached_short_circuit",
            )
            raise HerbieTransientUnavailableError(
                f"Herbie idx transiently unavailable (cached) after priorities={priority_list} "
                f"for {model_id} fh{fh:03d} pattern={search_pattern!r}"
            ) from last_exc
        if (saw_missing_index or saw_missing_subset_file) and not saw_non_transient_failure:
            raise HerbieTransientUnavailableError(
                f"Herbie subset transiently unavailable after priorities={priority_list} "
                f"for {model_id} fh{fh:03d} pattern={search_pattern!r}"
            ) from last_exc
        raise RuntimeError(
            f"Herbie subset download failed after trying priorities={priority_list} "
            f"for {model_id} fh{fh:03d} pattern={search_pattern!r}"
        ) from last_exc

    try:
        data, crs, transform = _read_grib_raster(grib_path)
    except rasterio.errors.RasterioIOError as exc:
        if _is_missing_file_error(exc):
            raise HerbieTransientUnavailableError(
                f"Herbie subset file disappeared before open for {model_id} fh{fh:03d} "
                f"pattern={search_pattern!r} path={grib_path}"
            ) from exc
        if _retry_on_invalid_subset and _is_unsupported_file_format_error(exc):
            subset_path = Path(grib_path)
            try:
                if subset_path.exists():
                    subset_path.unlink()
                    logger.warning(
                        "Deleted unreadable cached GRIB subset and retrying (%s fh%03d %s): %s",
                        model_id,
                        fh,
                        search_pattern,
                        subset_path,
                    )
                    return fetch_variable(
                        model_id=model_id,
                        product=product,
                        search_pattern=search_pattern,
                        run_date=run_date,
                        fh=fh,
                        herbie_kwargs=herbie_kwargs,
                        bundle_fetch_cache=None,
                        return_meta=return_meta,
                        _retry_on_invalid_subset=False,
                    )
            except OSError:
                pass
        if _is_unsupported_file_format_error(exc):
            subset_path = Path(grib_path)
            try:
                if subset_path.exists():
                    subset_path.unlink()
            except OSError:
                pass
            _metric_increment("invalid_grib_subset_open")
            raise HerbieTransientUnavailableError(
                f"Herbie subset unreadable after refresh for {model_id} fh{fh:03d} "
                f"pattern={search_pattern!r} path={grib_path}"
            ) from exc
        raise

    logger.debug(
        "GRIB data: shape=%s, CRS=%s, dtype=%s",
        data.shape, crs, data.dtype,
    )

    if return_meta:
        return data, crs, transform, selected_meta
    return data, crs, transform


# ---------------------------------------------------------------------------
# Unit conversions
# Keyed by conversion id, (model_id, var_key), or legacy var_key.
# Each converter takes a float32 array (in-place safe) and returns float32.
# ---------------------------------------------------------------------------

def _celsius_to_fahrenheit(data: np.ndarray) -> np.ndarray:
    """Convert Celsius → Fahrenheit, preserving NaN.

    GDAL's GRIB driver normalizes temperatures to °C by default
    (GRIB_NORMALIZE_UNITS=YES since GDAL 2.0), so GRIB TMP fields
    arrive as °C, not Kelvin.
    """
    return data * 9.0 / 5.0 + 32.0


def _ms_to_mph(data: np.ndarray) -> np.ndarray:
    """Convert m/s → mph, preserving NaN."""
    return data * 2.23694


def _ms_to_kt(data: np.ndarray) -> np.ndarray:
    """Convert m/s → knots, preserving NaN."""
    return data * 1.943844


def _meters_to_inches(data: np.ndarray) -> np.ndarray:
    """Convert meters → inches, preserving NaN."""
    return data * 39.37007874015748


def _kgm2_to_inches(data: np.ndarray) -> np.ndarray:
    """Convert kg/m^2 liquid water equivalent → inches.

    For water, 1 kg/m^2 == 1 mm depth.
    """
    return data * 0.03937007874015748


def _meters_swe_to_10to1_snow_inches(data: np.ndarray) -> np.ndarray:
    """Convert meters of snow water equivalent to 10:1 snowfall inches."""
    return data * 39.37007874015748 * 10.0


def _kgm2_swe_to_10to1_snow_inches(data: np.ndarray) -> np.ndarray:
    """Convert kg/m^2 SWE to 10:1 snowfall inches.

    For water equivalent, 1 kg/m^2 == 1 mm depth.
    """
    return data * 0.03937007874015748 * 10.0


def _per_second_to_1e5_per_second(data: np.ndarray) -> np.ndarray:
    """Convert s^-1 to 10^-5 s^-1, preserving NaN."""
    return data * 100000.0


def _pa_to_hpa(data: np.ndarray) -> np.ndarray:
    """Convert Pascals to hectopascals, preserving NaN."""
    return data / 100.0


def _geopotential_to_height_m(data: np.ndarray) -> np.ndarray:
    """Convert geopotential (m^2/s^2) to geopotential height in meters."""
    return data / 9.80665


def _geopotential_to_height_dam(data: np.ndarray) -> np.ndarray:
    """Convert geopotential (m^2/s^2) to geopotential height in decameters."""
    return _meters_to_decameters(_geopotential_to_height_m(data))


def _aifs_geopotential_to_height_dam(data: np.ndarray) -> np.ndarray:
    """Convert AIFS geopotential at 500 mb to geopotential height in dam."""
    return _meters_to_decameters(_geopotential_to_height_m(data))


def _meters_to_decameters(data: np.ndarray) -> np.ndarray:
    """Convert meters to decameters, preserving NaN."""
    return data / 10.0


# Registry: conversion-key -> converter function.
# Variables not listed here need no conversion (GRIB units match spec units).
# NOTE: GDAL's GRIB driver applies GRIB_NORMALIZE_UNITS=YES by default,
# so temperatures arrive in °C (not K) and wind speeds in m/s.
UNIT_CONVERTERS: dict[tuple[str, str] | str, Any] = {
    # Conversion IDs for capability metadata
    "c_to_f": _celsius_to_fahrenheit,
    "ms_to_mph": _ms_to_mph,
    "ms_to_kt": _ms_to_kt,
    "m_to_in": _meters_to_inches,
    "m_swe_to_in_10to1": _meters_swe_to_10to1_snow_inches,
    "kgm2_to_in": _kgm2_to_inches,
    "kgm2_swe_to_in_10to1": _kgm2_swe_to_10to1_snow_inches,
    "s-1_to_1e5s-1": _per_second_to_1e5_per_second,
    "pressure_pa_to_hpa": _pa_to_hpa,
    "geopotential_to_height_m": _geopotential_to_height_m,
    "geopotential_to_height_dam": _geopotential_to_height_dam,
    "m_to_dam": _meters_to_decameters,
    ("aifs", "hgt500"): _aifs_geopotential_to_height_dam,
    # Legacy var-key fallback path
    "tmp2m": _celsius_to_fahrenheit,
    "dp2m": _celsius_to_fahrenheit,
    "hgt500": _meters_to_decameters,
    "hgt500__mean": _meters_to_decameters,
    "pwat": _kgm2_to_inches,
    "wspd10m": _ms_to_mph,
    "wgst10m": _ms_to_mph,
    "snowfall_total": _meters_to_inches,
    "precip_total": _kgm2_to_inches,
    "vort500": _per_second_to_1e5_per_second,
}


def convert_units(
    data: np.ndarray,
    var_key: str,
    *,
    model_id: str | None = None,
    var_capability: Any | None = None,
) -> np.ndarray:
    """Apply unit conversion for a variable if one is registered.

    Returns a new array (or the original if no conversion needed).
    """
    converter = None

    # Authoritative path: conversion id set in model capability metadata.
    conversion_id = getattr(var_capability, "conversion", None) if var_capability is not None else None
    if isinstance(conversion_id, str) and conversion_id:
        converter = UNIT_CONVERTERS.get(conversion_id)

    # Optional model-specific override fallback.
    if converter is None and isinstance(model_id, str) and model_id:
        converter = UNIT_CONVERTERS.get((model_id, var_key))

    # Legacy fallback for existing callers/vars.
    if converter is None:
        converter = UNIT_CONVERTERS.get(var_key)

    if converter is None:
        return data
    result = converter(data.astype(np.float32, copy=True))
    logger.debug("Unit conversion applied for model=%s var=%s", model_id, var_key)
    return result
