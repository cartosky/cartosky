from __future__ import annotations

import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import derive as derive_module
from app.services.builder import fetch as fetch_module


class _FakeResponse:
    def __init__(self, payload: bytes, status_code: int = 206, headers: dict[str, str] | None = None):
        self.content = payload
        self.status_code = status_code
        self.headers = headers if headers is not None else {"Content-Length": str(len(payload))}

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        return None


def _install_fake_herbie(monkeypatch: pytest.MonkeyPatch, herbie_cls: type) -> None:
    fake_core = types.ModuleType("herbie.core")
    fake_core.Herbie = herbie_cls
    fake_pkg = types.ModuleType("herbie")
    fake_pkg.core = fake_core
    monkeypatch.setitem(sys.modules, "herbie", fake_pkg)
    monkeypatch.setitem(sys.modules, "herbie.core", fake_core)


def _install_fake_rasterio_open(monkeypatch: pytest.MonkeyPatch, value: float = 1.0) -> None:
    class _FakeDataset:
        crs = "EPSG:4326"
        transform = fetch_module.rasterio.transform.Affine.identity()
        nodata = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        def read(self, _band: int, masked: bool = True):
            del masked
            return np.ma.array([[value, value], [value, value]], mask=[[False, False], [False, False]], dtype=np.float32)

        def tags(self, *_args):
            return {}

    monkeypatch.setattr(fetch_module.rasterio, "open", lambda _path: _FakeDataset())


def test_fetch_range_cache_hit_store_and_single_http_call(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    cache = fetch_module.BundleFetchCache(max_entries=8, max_bytes=4096, max_cacheable_bytes=1024)
    calls: list[tuple[str, str]] = []

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del timeout
        calls.append((url, str(headers.get("Range", ""))))
        return _FakeResponse(b"ABCD")

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    kwargs = dict(
        source="nomads",
        source_url="https://nomads.example/hrrr.grib2",
        model_id="hrrr",
        run_date=datetime(2026, 3, 5, 18, 0),
        fh=13,
        start_byte=0,
        end_byte=3,
        bundle_fetch_cache=cache,
    )
    first = fetch_module._fetch_range_bytes(**kwargs)
    second = fetch_module._fetch_range_bytes(**kwargs)

    assert first == b"ABCD"
    assert second == b"ABCD"
    assert calls == [("https://nomads.example/hrrr.grib2", "bytes=0-3")]
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("fetch_cache_miss", 0) == 1
    assert metrics["counters"].get("fetch_cache_store", 0) == 1
    assert metrics["counters"].get("fetch_cache_hit", 0) >= 1

def test_download_subset_with_inventory_byte_range_falls_back_to_full_file_when_range_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()

    class _FakeHerbie:
        idx = "https://nomads.example/aigfs.idx"
        grib = "https://nomads.example/aigfs.grib2"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([
                {"search_this": ":UGRD:850 mb:", "start_byte": 4, "end_byte": 35}
            ])

    payload = b"GRIB" + (b"Z" * 28)
    full_bytes = b"JUNK" + payload + b"TAIL"

    def _fake_fetch_range_bytes(**kwargs):
        raise RuntimeError("range blocked")

    def _fake_download_full_grib_to_path(*, source_url: str, out_path: Path) -> Path:
        assert source_url == "https://nomads.example/aigfs.grib2"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(full_bytes)
        return out_path

    monkeypatch.setattr(fetch_module, "_fetch_range_bytes", _fake_fetch_range_bytes)
    monkeypatch.setattr(fetch_module, "_download_full_grib_to_path", _fake_download_full_grib_to_path)

    out_path = tmp_path / "subset.grib2"
    subset_path = fetch_module._download_subset_with_inventory_byte_range(
        _FakeHerbie(),
        search_pattern=":UGRD:850 mb:",
        out_path=out_path,
        model_id="aigfs",
        run_date=datetime(2026, 5, 28, 18, 0),
        product="pres",
        fh=198,
        priority="nomads",
        bundle_fetch_cache=None,
    )

    assert subset_path == out_path
    assert out_path.read_bytes() == payload
    assert not out_path.with_suffix(".grib2.full").exists()


def test_fetch_range_cache_singleflight_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    cache = fetch_module.BundleFetchCache(max_entries=8, max_bytes=4096, max_cacheable_bytes=1024)
    calls = {"count": 0}
    lock = threading.Lock()

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout
        with lock:
            calls["count"] += 1
        time.sleep(0.1)
        return _FakeResponse(b"DATA")

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    kwargs = dict(
        source="nomads",
        source_url="https://nomads.example/gfs.grib2",
        model_id="gfs",
        run_date=datetime(2026, 3, 5, 0, 0),
        fh=6,
        start_byte=10,
        end_byte=13,
        bundle_fetch_cache=cache,
    )

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_module._fetch_range_bytes, **kwargs) for _ in range(6)]
        results = [future.result() for future in futures]

    assert all(payload == b"DATA" for payload in results)
    assert calls["count"] == 1


def test_fetch_range_cache_separates_different_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    cache = fetch_module.BundleFetchCache(max_entries=8, max_bytes=4096, max_cacheable_bytes=1024)
    calls = {"count": 0}

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, timeout
        calls["count"] += 1
        if headers.get("Range") == "bytes=0-3":
            return _FakeResponse(b"ABCD")
        return _FakeResponse(b"EFGH")

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    base = dict(
        source="nomads",
        source_url="https://nomads.example/nam.grib2",
        model_id="nam",
        run_date=datetime(2026, 3, 5, 6, 0),
        fh=3,
        bundle_fetch_cache=cache,
    )
    first = fetch_module._fetch_range_bytes(start_byte=0, end_byte=3, **base)
    second = fetch_module._fetch_range_bytes(start_byte=4, end_byte=7, **base)

    assert first == b"ABCD"
    assert second == b"EFGH"
    assert calls["count"] == 2
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("fetch_cache_miss", 0) >= 2


def test_fetch_range_cache_skips_large_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    cache = fetch_module.BundleFetchCache(max_entries=8, max_bytes=4096, max_cacheable_bytes=2)
    calls = {"count": 0}

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout
        calls["count"] += 1
        return _FakeResponse(b"ABCD")

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    kwargs = dict(
        source="nomads",
        source_url="https://nomads.example/hrrr-large.grib2",
        model_id="hrrr",
        run_date=datetime(2026, 3, 5, 18, 0),
        fh=13,
        start_byte=0,
        end_byte=3,
        bundle_fetch_cache=cache,
    )
    first = fetch_module._fetch_range_bytes(**kwargs)
    second = fetch_module._fetch_range_bytes(**kwargs)

    assert first == b"ABCD"
    assert second == b"ABCD"
    assert calls["count"] == 2
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("fetch_cache_skip_too_large", 0) >= 1
    assert metrics["counters"].get("fetch_cache_hit", 0) == 0


def test_fetch_range_cache_failure_does_not_poison_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    cache = fetch_module.BundleFetchCache(max_entries=8, max_bytes=4096, max_cacheable_bytes=1024)
    calls = {"count": 0}

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary network failure")
        return _FakeResponse(b"ABCD")

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    kwargs = dict(
        source="nomads",
        source_url="https://nomads.example/hrrr-retry.grib2",
        model_id="hrrr",
        run_date=datetime(2026, 3, 5, 18, 0),
        fh=13,
        start_byte=0,
        end_byte=3,
        bundle_fetch_cache=cache,
    )
    with pytest.raises(RuntimeError):
        fetch_module._fetch_range_bytes(**kwargs)
    second = fetch_module._fetch_range_bytes(**kwargs)

    assert second == b"ABCD"
    assert calls["count"] == 2
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("fetch_cache_store", 0) == 1


def test_network_fetch_range_bytes_rejects_full_file_200_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that ignores the Range header returns 200 with the ENTIRE file.
    The body starts with 'GRIB' so payload validation would pass and band 1
    (the wrong message) would be decoded — the guard must reject it instead."""
    fetch_module.reset_herbie_runtime_caches_for_tests()
    full_file = b"GRIB" + (b"\0" * 1020)  # whole-file body, much larger than the slice

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout, stream
        return _FakeResponse(full_file, status_code=200)

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    with pytest.raises(fetch_module._InvalidGribSubsetError, match="Range request not honored"):
        fetch_module._network_fetch_range_bytes(
            "https://nomads.example/gfs.grib2", start_byte=100, end_byte=131
        )
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("range_request_not_honored", 0) == 1


def test_network_fetch_range_bytes_accepts_200_when_body_is_exactly_the_slice(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"GRIB" + (b"\0" * 28)

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout, stream
        return _FakeResponse(payload, status_code=200, headers={"Content-Length": str(len(payload))})

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    result = fetch_module._network_fetch_range_bytes(
        "https://nomads.example/gfs.grib2", start_byte=0, end_byte=31
    )
    assert result == payload


def test_network_fetch_range_bytes_rejects_truncated_206_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout, stream
        return _FakeResponse(b"GRIB" + (b"\0" * 10))  # 14 bytes; 32 requested

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    with pytest.raises(fetch_module._InvalidGribSubsetError, match="size mismatch"):
        fetch_module._network_fetch_range_bytes(
            "https://nomads.example/gfs.grib2", start_byte=0, end_byte=31
        )
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("range_payload_truncated", 0) == 1


def test_fetch_range_cache_does_not_store_empty_payloads() -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    cache = fetch_module.BundleFetchCache(max_entries=8, max_bytes=4096, max_cacheable_bytes=1024)
    calls = {"count": 0}

    def _fetcher() -> bytes:
        calls["count"] += 1
        if calls["count"] == 1:
            return b""
        return b"GRIB" + (b"\0" * 28)

    first, first_event, _first_evicted = cache.get_or_fetch(
        "empty-then-ready",
        fetcher=_fetcher,
        cacheable=True,
        expected_size=None,
    )
    second, second_event, _second_evicted = cache.get_or_fetch(
        "empty-then-ready",
        fetcher=_fetcher,
        cacheable=True,
        expected_size=None,
    )

    assert first == b""
    assert first_event == "miss"
    assert second.startswith(b"GRIB")
    assert second_event == "miss"
    assert calls["count"] == 2


def test_fetch_range_cache_invalid_grib_payload_does_not_poison_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    cache = fetch_module.BundleFetchCache(max_entries=8, max_bytes=4096, max_cacheable_bytes=1024)
    calls = {"count": 0}

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout
        calls["count"] += 1
        if calls["count"] == 1:
            return _FakeResponse(b"<Error>not ready</Error>".ljust(32, b" "))
        return _FakeResponse(b"GRIB" + (b"\0" * 28))

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    kwargs = dict(
        source="nomads",
        source_url="https://nomads.example/gfs-not-ready.grib2",
        model_id="gfs",
        run_date=datetime(2026, 5, 28, 12, 0),
        fh=156,
        start_byte=436043999,
        end_byte=436044030,
        bundle_fetch_cache=cache,
        require_grib_payload=True,
    )
    with pytest.raises(RuntimeError, match="Invalid GRIB range payload"):
        fetch_module._fetch_range_bytes(**kwargs)
    second = fetch_module._fetch_range_bytes(**kwargs)

    assert second.startswith(b"GRIB")
    assert calls["count"] == 2
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("invalid_grib_range_payload", 0) == 1
    assert metrics["counters"].get("fetch_cache_store", 0) == 1


def test_derived_smoke_reports_fetch_cache_hit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    _install_fake_rasterio_open(monkeypatch, value=1.0)

    class _FakeHerbie:
        def __init__(self, date: datetime, **kwargs):
            self.date = date
            self.model = kwargs.get("model")
            self.product = kwargs.get("product")
            self.fxx = kwargs.get("fxx")
            self.priority = kwargs.get("priority")
            self.idx = f"https://{self.priority}.example/{self.model}.{self.product}.idx"
            self.grib = f"https://{self.priority}.example/{self.model}.{self.product}.grib2"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame(
                [
                    {"search_this": ":APCP:surface:", "start_byte": 0, "end_byte": 31},
                    {"search_this": ":CSNOW:surface:", "start_byte": 0, "end_byte": 31},
                ]
            )

        def get_localFilePath(self, search_pattern: str):
            token = "apcp" if "APCP" in str(search_pattern) else "csnow"
            return str(tmp_path / f"{token}.grib2")

        def download(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("grib2 file not found")

    _install_fake_herbie(monkeypatch, _FakeHerbie)

    request_calls = {"count": 0}

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout
        request_calls["count"] += 1
        return _FakeResponse(b"GRIB" + (b"X" * 28))

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "nomads")

    class _Plugin:
        def normalize_var_id(self, var_key: str) -> str:
            return var_key

        def get_var_capability(self, var_key: str):
            del var_key
            return None

        def get_var(self, var_key: str):
            mapping = {
                "apcp_step": [":APCP:surface:"],
                "csnow": [":CSNOW:surface:"],
            }
            search = mapping.get(var_key)
            if search is None:
                return None
            return SimpleNamespace(
                selectors=SimpleNamespace(search=search, filter_by_keys={}, hints={}),
            )

        def search_patterns_for_var(self, *, var_key: str, fh: int, product: str, var_spec):
            del fh, product, var_spec
            spec = self.get_var(var_key)
            selectors = getattr(spec, "selectors", None)
            return list(getattr(selectors, "search", []) or [])

        def herbie_request(self, *, product: str, var_key: str, run_date: datetime, fh: int, search_pattern: str):
            del var_key, run_date, fh, search_pattern
            return SimpleNamespace(model="hrrr", product=product, herbie_kwargs=None)

    ctx = derive_module.FetchContext(bundle_fetch_cache=fetch_module.BundleFetchCache(max_entries=8, max_bytes=4096, max_cacheable_bytes=1024))
    var_spec_model = SimpleNamespace(
        selectors=SimpleNamespace(
            hints={
                "apcp_component": "apcp_step",
                "snow_component": "csnow",
                "step_hours": "1",
                "slr": "10",
                "min_step_lwe_kgm2": "0.01",
            }
        )
    )

    derive_module._derive_snowfall_total_10to1_cumulative(
        model_id="hrrr",
        var_key="snowfall_total",
        product="sfc",
        run_date=datetime(2026, 3, 5, 18, 0),
        fh=1,
        var_spec_model=var_spec_model,
        var_capability=None,
        model_plugin=_Plugin(),
        ctx=ctx,
    )

    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("fetch_cache_hit", 0) > 0
    assert request_calls["count"] >= 1
