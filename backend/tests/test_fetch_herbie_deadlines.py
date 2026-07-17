from __future__ import annotations

import threading
import time
from pathlib import Path
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
        result = fetch_module._inventory_index_dataframe(
            _UnusedHerbie(),
            idx_key="blocked-inventory",
        )
    finally:
        with fetch_module._INVENTORY_CACHE_LOCK:
            fetch_module._INVENTORY_INFLIGHT.pop("blocked-inventory", None)

    assert result is None
    assert time.monotonic() - before < 0.5
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("idx_cache_follower_timeout", 0) == 1


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
