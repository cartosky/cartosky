from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import sys

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import fetch as fetch_module


def test_herbie_internal_call_has_a_hard_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    started = threading.Event()

    def _blocked_call() -> None:
        started.set()
        release.wait(timeout=2.0)

    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_INTERNAL_CALL_DEADLINE_SECONDS", "0.05")

    before = time.monotonic()
    try:
        with pytest.raises(fetch_module.HerbieCallTimeoutError, match="index_as_dataframe"):
            fetch_module._run_herbie_call_with_deadline(
                _blocked_call,
                operation="index_as_dataframe",
            )
        assert started.wait(timeout=0.25)
        assert time.monotonic() - before < 0.5
    finally:
        release.set()

    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("herbie_call_timeout", 0) == 1
    assert metrics["counters"].get("herbie_index_as_dataframe_timeout", 0) == 1


def test_inventory_follower_wait_is_independent_of_cache_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnusedHerbie:
        @property
        def index_as_dataframe(self):
            raise AssertionError("A follower must not issue the leader's inventory request")

    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_FOLLOWER_WAIT_SECONDS", "0.05")
    with fetch_module._INVENTORY_CACHE_LOCK:
        fetch_module._INVENTORY_INFLIGHT["blocked-inventory"] = threading.Event()

    before = time.monotonic()
    try:
        with pytest.raises(fetch_module.HerbieCallTimeoutError, match="follower wait"):
            fetch_module._inventory_index_dataframe(
                _UnusedHerbie(),
                idx_key="blocked-inventory",
            )
        assert time.monotonic() - before < 0.5
    finally:
        with fetch_module._INVENTORY_CACHE_LOCK:
            fetch_module._INVENTORY_INFLIGHT.pop("blocked-inventory", None)

    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("idx_cache_follower_timeout", 0) == 1


def test_inventory_follower_timeout_is_not_a_definitive_empty_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out follower must not look like idx_empty to callers.

    Concurrent EPS builds share one inventory download. If followers treat a
    still-in-flight leader as an empty index, direct-mean negative caching
    poisons the whole statistics file for minutes even after the leader lands.
    """
    class _UnusedHerbie:
        @property
        def index_as_dataframe(self):
            raise AssertionError("A follower must not issue the leader's inventory request")

    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_FOLLOWER_WAIT_SECONDS", "0.05")
    with fetch_module._INVENTORY_CACHE_LOCK:
        fetch_module._INVENTORY_INFLIGHT["shared-inventory"] = threading.Event()

    try:
        with pytest.raises(fetch_module.HerbieCallTimeoutError, match="follower wait"):
            fetch_module._inventory_index_dataframe(
                _UnusedHerbie(),
                idx_key="shared-inventory",
            )
        # Leader is still marked in-flight — this was congestion, not absence.
        with fetch_module._INVENTORY_CACHE_LOCK:
            assert "shared-inventory" in fetch_module._INVENTORY_INFLIGHT
    finally:
        with fetch_module._INVENTORY_CACHE_LOCK:
            fetch_module._INVENTORY_INFLIGHT.pop("shared-inventory", None)


def test_direct_mean_inventory_follower_timeout_does_not_poison_negative_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Follower congestion must not record a terminal statistics miss.

    Concrete trigger: concurrent EPS direct-mean builds share one inventory
    download. A follower that times out while the leader is still in flight
    used to surface as ``idx_empty``, which recorded a run-wide negative cache
    entry and forced every later hour onto PF-mean for minutes.
    """
    class _CongestedHerbie:
        def __init__(self, *_args, **kwargs) -> None:
            self.priority = kwargs.get("priority")
            self.fxx = int(kwargs.get("fxx"))
            self.grib = f"https://example.invalid/run-{self.fxx}h-enfo-ef.grib2"
            self.idx = f"https://example.invalid/run-{self.fxx}h-enfo-ef.index"

        @property
        def index_as_dataframe(self):
            raise AssertionError("follower must wait on the shared in-flight download")

    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _CongestedHerbie
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_FOLLOWER_WAIT_SECONDS", "0.05")
    monkeypatch.setattr(fetch_module, "_fetch_ecmwf_pf_mean_variable", lambda **_kwargs: "pf")

    run_date = datetime(2026, 4, 19, 0, 0)
    # _search_eps_statistics_inventory keys the shared inventory cache by the
    # *requested* forecast hour (fh=6), even though Herbie is pointed at the
    # terminal statistics file. Remap turns "...-240h-enfo-ef.index" into
    # "...-240h-enfo-ep.index" (not .grib2.index).
    idx_key = fetch_module._inventory_cache_key_from_idx(
        "https://example.invalid/run-240h-enfo-ep.index",
        priority="azure",
        model_id="ifs",
        run_date=run_date,
        product="enfo",
        fh=6,
        grib_ref="https://example.invalid/run-240h-enfo-ep.grib2",
    )
    with fetch_module._INVENTORY_CACHE_LOCK:
        fetch_module._INVENTORY_INFLIGHT[idx_key] = threading.Event()

    try:
        with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
            result = fetch_module._fetch_ecmwf_direct_mean_variable(
                model_id="ifs",
                product="enfo",
                search_pattern=":gh:500:",
                run_date=run_date,
                fh=6,
                herbie_kwargs={"priority": ["azure"]},
                bundle_fetch_cache=None,
                return_meta=False,
                fallback_to_pf_mean=True,
            )
    finally:
        with fetch_module._INVENTORY_CACHE_LOCK:
            fetch_module._INVENTORY_INFLIGHT.pop(idx_key, None)

    assert result == "pf"
    assert not fetch_module._EPS_DIRECT_MEAN_NEGATIVE_CACHE
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("eps_direct_mean_negative_cache_store", 0) == 0
    assert metrics["counters"].get("idx_cache_follower_timeout", 0) >= 1


def test_timed_out_herbie_download_cannot_overwrite_canonical_subset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    release = threading.Event()
    download_started = threading.Event()
    download_finished = threading.Event()
    attempt_paths: list[Path] = []

    class _BlockedDownloadHerbie:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def download(self, _search_pattern: str, **kwargs) -> Path:
            attempt_root = Path(kwargs["save_dir"])
            attempt_path = attempt_root / "isolated-subset.grib2"
            attempt_path.parent.mkdir(parents=True, exist_ok=True)
            attempt_path.write_bytes(b"GRIBpartial")
            attempt_paths.append(attempt_path)
            download_started.set()
            release.wait(timeout=2.0)
            attempt_path.write_bytes(b"GRIBlate-writer")
            download_finished.set()
            return attempt_path

    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_INTERNAL_CALL_DEADLINE_SECONDS", "0.05")
    canonical = tmp_path / "canonical-subset.grib2"

    try:
        with pytest.raises(fetch_module.HerbieCallTimeoutError, match="download"):
            fetch_module._download_herbie_subset_isolated(
                _BlockedDownloadHerbie(),
                search_pattern=":TMP:2 m above ground:",
                subset_hint=canonical,
            )
        assert download_started.wait(timeout=0.25)
        canonical.write_bytes(b"GRIBfallback")
    finally:
        release.set()

    assert download_finished.wait(timeout=0.5)
    assert canonical.read_bytes() == b"GRIBfallback"
    assert attempt_paths
    deadline = time.monotonic() + 0.5
    while attempt_paths[0].exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not attempt_paths[0].exists()
