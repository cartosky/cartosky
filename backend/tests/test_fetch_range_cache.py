from __future__ import annotations

import asyncio
import os
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


class _FakeFullGribResponseContext:
    def __init__(self, response) -> None:
        self._response = response
        self.status_code = response.status_code
        self.headers = response.headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        self._response.close()
        return False

    def raise_for_status(self) -> None:
        self._response.raise_for_status()

    async def aiter_bytes(self, chunk_size: int):
        yield_from = self._response.iter_content(chunk_size=chunk_size)
        for chunk in yield_from:
            yield chunk


class _FakeFullGribClient:
    def __init__(self, response_factory) -> None:
        self._response_factory = response_factory

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False

    def stream(self, method: str, url: str):
        del method, url
        return _FakeFullGribResponseContext(self._response_factory())


def _install_full_grib_response(monkeypatch: pytest.MonkeyPatch, response_factory) -> None:
    monkeypatch.setattr(
        fetch_module,
        "_full_grib_http_client",
        lambda **_kwargs: _FakeFullGribClient(response_factory),
    )


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


def test_subset_writer_orders_and_deduplicates_inventory_ranges(tmp_path: Path) -> None:
    records = [
        b"GRIB" + (b"A" * 16),
        b"GRIB" + (b"B" * 16),
        b"GRIB" + (b"C" * 16),
    ]
    source_path = tmp_path / "source.grib2"
    source_path.write_bytes(b"".join(records))
    inventory = pd.DataFrame(
        [
            {"start_byte": 40, "end_byte": 59},
            {"start_byte": 0, "end_byte": 19},
            {"start_byte": 20, "end_byte": 39},
            {"start_byte": 0, "end_byte": 19},
        ]
    )
    out_path = tmp_path / "subset.grib2"

    written = fetch_module._download_subset_with_inventory_rows(
        SimpleNamespace(grib=str(source_path)),
        inventory=inventory,
        out_path=out_path,
        model_id="ifs",
        product="enfo",
        run_date=datetime(2026, 7, 16, 0, 0),
        fh=6,
        priority="local",
        bundle_fetch_cache=None,
    )

    assert written == out_path
    assert out_path.read_bytes() == b"".join(records)


def test_wgrib2_final_record_uses_an_open_ended_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idx_text = "\n".join(
        [
            "1:0:d=2026071600:TMP:2 m above ground:anl:",
            "2:100:d=2026071600:UGRD:10 m above ground:anl:",
        ]
    )
    monkeypatch.setattr(fetch_module, "_fetch_inventory_index_text", lambda _ref: idx_text)
    inventory = fetch_module._inventory_index_dataframe_from_wgrib2_lines(
        "https://example.invalid/model.idx"
    )

    assert inventory is not None
    assert fetch_module._inventory_row_byte_range(inventory.iloc[-1]) == (100, None)

    class _FakeHerbie:
        grib = "https://example.invalid/model.grib2"
        idx = "https://example.invalid/model.idx"

    monkeypatch.setattr(
        fetch_module,
        "_inventory_search",
        lambda *_args, **_kwargs: fetch_module._InventorySearchResult(
            inventory=inventory.iloc[[-1]],
            reason="ok",
        ),
    )
    assert fetch_module._inventory_primary_byte_range(
        _FakeHerbie(),
        search_pattern=":UGRD:10 m above ground:",
        model_id="gfs",
        run_date=datetime(2026, 7, 16, 0, 0),
        product="pgrb2.0p25",
        fh=0,
        priority="nomads",
    ) == ("https://example.invalid/model.grib2", 100, None)

    payload = b"GRIB" + (b"Z" * 28)
    observed_ranges: list[str] = []

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, timeout, stream
        observed_ranges.append(headers["Range"])
        return _FakeResponse(payload, status_code=206)

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)
    assert fetch_module._network_fetch_range_bytes(
        "https://example.invalid/model.grib2",
        start_byte=100,
        end_byte=None,
    ) == payload
    assert observed_ranges == ["bytes=100-"]


def test_open_ended_range_reuses_bundle_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"GRIB" + (b"K" * 28)
    calls = {"count": 0}
    cache = fetch_module.BundleFetchCache(
        max_entries=4,
        max_bytes=4096,
        max_cacheable_bytes=1024,
    )

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, timeout, stream
        assert headers["Range"] == "bytes=100-"
        calls["count"] += 1
        return _FakeResponse(payload, status_code=206)

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)
    kwargs = dict(
        source="nomads",
        source_url="https://example.invalid/model.grib2",
        model_id="gfs",
        run_date=datetime(2026, 7, 16, 0, 0),
        fh=6,
        start_byte=100,
        end_byte=None,
        bundle_fetch_cache=cache,
        require_grib_payload=True,
    )

    assert fetch_module._fetch_range_bytes(**kwargs) == payload
    assert fetch_module._fetch_range_bytes(**kwargs) == payload
    assert calls["count"] == 1


def test_local_multirow_subset_reads_open_ended_final_record(tmp_path: Path) -> None:
    first = b"GRIB" + (b"A" * 16)
    final = b"GRIB" + (b"B" * 16)
    source_path = tmp_path / "source.grib2"
    source_path.write_bytes(first + final)
    inventory = pd.DataFrame(
        [
            {"start_byte": 0, "end_byte": len(first) - 1},
            {"start_byte": len(first)},
        ]
    )
    out_path = tmp_path / "subset.grib2"

    result = fetch_module._download_subset_with_inventory_rows(
        SimpleNamespace(grib=str(source_path)),
        inventory=inventory,
        out_path=out_path,
        model_id="gfs",
        product="pgrb2.0p25",
        run_date=datetime(2026, 7, 16, 0, 0),
        fh=6,
        priority="local",
        bundle_fetch_cache=None,
    )

    assert result == out_path
    assert out_path.read_bytes() == first + final


def test_fetch_range_retries_transient_failure_before_returning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"count": 0}
    payload = b"GRIB" + (b"R" * 28)

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout, stream
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("temporary range failure")
        return _FakeResponse(payload)

    monkeypatch.setenv("TWF_HERBIE_RANGE_RETRIES", "3")
    monkeypatch.setenv("TWF_HERBIE_RANGE_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    result = fetch_module._fetch_range_bytes(
        source="nomads",
        source_url="https://example.invalid/model.grib2",
        model_id="gfs",
        run_date=datetime(2026, 7, 16, 0, 0),
        fh=6,
        start_byte=0,
        end_byte=31,
        bundle_fetch_cache=None,
        require_grib_payload=True,
    )

    assert result == payload
    assert attempts["count"] == 2


def test_fetch_range_does_not_retry_upstream_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"count": 0}

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout, stream
        attempts["count"] += 1
        # Same declared length as the requested slice: redirects must still
        # be typed refusals rather than accepted as exact-length HTTP 200s.
        return _FakeResponse(b"x" * 32, status_code=302)

    monkeypatch.setenv("TWF_HERBIE_RANGE_RETRIES", "3")
    monkeypatch.setenv("TWF_HERBIE_RANGE_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    with pytest.raises(fetch_module._RangeRequestNotHonoredError):
        fetch_module._fetch_range_bytes(
            source="nomads",
            source_url="https://example.invalid/model.grib2",
            model_id="gfs",
            run_date=datetime(2026, 7, 16, 0, 0),
            fh=6,
            start_byte=0,
            end_byte=31,
            bundle_fetch_cache=None,
        )

    assert attempts["count"] == 1


def test_range_retries_exhaust_before_full_file_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    range_attempts = {"count": 0}
    fallback_calls = {"count": 0}
    payload = b"GRIB" + (b"F" * 28)

    class _FakeHerbie:
        idx = "https://example.invalid/model.idx"
        grib = "https://example.invalid/model.grib2"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([
                {"search_this": ":TMP:2 m above ground:", "start_byte": 4, "end_byte": 35}
            ])

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout, stream
        range_attempts["count"] += 1
        raise OSError("range unavailable")

    def _fake_full_fallback(*_args, **_kwargs) -> bytes:
        fallback_calls["count"] += 1
        return payload

    monkeypatch.setenv("TWF_HERBIE_RANGE_RETRIES", "3")
    monkeypatch.setenv("TWF_HERBIE_RANGE_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)
    monkeypatch.setattr(fetch_module, "_fetch_subset_bytes_from_full_source", _fake_full_fallback)

    out_path = tmp_path / "subset.grib2"
    result = fetch_module._download_subset_with_inventory_byte_range(
        _FakeHerbie(),
        search_pattern=":TMP:2 m above ground:",
        out_path=out_path,
        model_id="gfs",
        run_date=datetime(2026, 7, 16, 0, 0),
        product="pgrb2.0p25",
        fh=6,
        priority="nomads",
        bundle_fetch_cache=None,
    )

    assert result == out_path
    assert range_attempts["count"] == 3
    assert fallback_calls["count"] == 1


def test_full_file_fallback_rejects_declared_size_over_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    body_started = {"value": False}

    class _OversizedResponse:
        status_code = 200
        headers = {"Content-Length": "100"}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            del chunk_size
            body_started["value"] = True
            yield b"GRIB" + (b"X" * 96)

        def close(self) -> None:
            return None

    _install_full_grib_response(monkeypatch, _OversizedResponse)

    with pytest.raises(RuntimeError, match="exceeds.*cap"):
        fetch_module._fetch_subset_bytes_from_full_source(
            "https://example.invalid/model.grib2",
            out_path=tmp_path / "subset.grib2",
            start_byte=0,
            end_byte=31,
            max_bytes=64,
        )

    assert body_started["value"] is False
    assert not list(tmp_path.iterdir())


def test_full_file_fallback_enforces_cap_without_content_length(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _ChunkedOversizedResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            del chunk_size
            yield b"GRIB" + (b"X" * 36)
            yield b"Y" * 40

        def close(self) -> None:
            return None

    _install_full_grib_response(monkeypatch, _ChunkedOversizedResponse)

    with pytest.raises(RuntimeError, match="streamed size exceeds.*cap"):
        fetch_module._fetch_subset_bytes_from_full_source(
            "https://example.invalid/model.grib2",
            out_path=tmp_path / "subset.grib2",
            start_byte=0,
            end_byte=31,
            max_bytes=64,
        )

    assert not list(tmp_path.iterdir())


def test_range_failure_routes_fallback_through_eps_full_file_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = b"GRIB" + (b"C" * 28)
    full_path = tmp_path / "cached-full.grib2"
    full_path.write_bytes(b"JUNK" + payload + b"TAIL")
    range_calls = {"count": 0}
    cache_calls = {"count": 0}

    class _FakeHerbie:
        idx = "https://example.invalid/eps.index"
        grib = "https://example.invalid/eps.grib2"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([
                {"search_this": ":TMP:2 m above ground:", "start_byte": 4, "end_byte": 35}
            ])

    def _failed_range(**_kwargs) -> bytes:
        range_calls["count"] += 1
        raise OSError("range retries exhausted")

    def _cached_full(*_args, **_kwargs) -> Path:
        cache_calls["count"] += 1
        return full_path

    monkeypatch.setattr(fetch_module, "_fetch_range_bytes", _failed_range)
    monkeypatch.setattr(fetch_module, "_maybe_get_eps_full_grib_path", _cached_full)
    monkeypatch.setattr(fetch_module, "_eps_full_file_cache_enabled", lambda **_kwargs: True)

    out_path = tmp_path / "subset.grib2"
    result = fetch_module._download_subset_with_inventory_byte_range(
        _FakeHerbie(),
        search_pattern=":TMP:2 m above ground:",
        out_path=out_path,
        model_id="ifs",
        run_date=datetime(2026, 7, 16, 0, 0),
        product="enfo",
        fh=6,
        priority="azure",
        bundle_fetch_cache=None,
    )

    assert result == out_path
    assert out_path.read_bytes() == payload
    assert range_calls["count"] == 1
    assert cache_calls["count"] == 1


def test_eps_cache_failure_does_not_bypass_to_disposable_full_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_calls = {"count": 0}

    class _FakeHerbie:
        idx = "https://example.invalid/eps.index"
        grib = "https://example.invalid/eps.grib2"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([
                {"search_this": ":TMP:2 m above ground:", "start_byte": 4, "end_byte": 35}
            ])

    def _cache_failed(*_args, **_kwargs):
        cache_calls["count"] += 1
        return None

    def _range_failed(**_kwargs) -> bytes:
        raise OSError("range retries exhausted")

    def _forbid_disposable(*_args, **_kwargs) -> bytes:
        raise AssertionError("must not bypass a failed reusable cache transfer")

    monkeypatch.setattr(fetch_module, "_fetch_range_bytes", _range_failed)
    monkeypatch.setattr(fetch_module, "_eps_full_file_cache_enabled", lambda **_kwargs: True)
    monkeypatch.setattr(fetch_module, "_maybe_get_eps_full_grib_path", _cache_failed)
    monkeypatch.setattr(
        fetch_module,
        "_fetch_subset_bytes_from_full_source",
        _forbid_disposable,
    )

    result = fetch_module._download_subset_with_inventory_byte_range(
        _FakeHerbie(),
        search_pattern=":TMP:2 m above ground:",
        out_path=tmp_path / "subset.grib2",
        model_id="ifs",
        run_date=datetime(2026, 7, 16, 0, 0),
        product="enfo",
        fh=6,
        priority="azure",
        bundle_fetch_cache=None,
    )

    assert result is None
    assert cache_calls["count"] == 1


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

    def _fake_download_full_grib_to_path(
        *,
        source_url: str,
        out_path: Path,
        max_bytes: int | None = None,
    ) -> Path:
        assert source_url == "https://nomads.example/aigfs.grib2"
        assert max_bytes == fetch_module.DEFAULT_FULL_GRIB_FALLBACK_MAX_BYTES
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
        if calls["count"] <= 3:
            raise RuntimeError("temporary network failure")
        return _FakeResponse(b"ABCD")

    monkeypatch.setenv("TWF_HERBIE_RANGE_RETRIES", "3")
    monkeypatch.setenv("TWF_HERBIE_RANGE_RETRY_BACKOFF_SECONDS", "0")
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
    assert calls["count"] == 4
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


def test_network_fetch_range_bytes_302_refusals_trip_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Consecutive unfollowable 3xx responses (NOMADS anti-abuse block) must trip
    a global cooldown so further range fetches fail fast without network hits."""
    fetch_module.reset_herbie_runtime_caches_for_tests()
    calls: list[str] = []

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del headers, timeout, stream
        calls.append(url)
        return _FakeResponse(b"<html>blocked</html>", status_code=302)

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    for _ in range(fetch_module.RANGE_THROTTLE_TRIP_COUNT):
        with pytest.raises(fetch_module._RangeRequestNotHonoredError):
            fetch_module._network_fetch_range_bytes(
                "https://nomads.example/aigfs.grib2", start_byte=100, end_byte=131
            )

    # Cooldown active: no further network call, distinct throttle error.
    with pytest.raises(fetch_module._RangeThrottleActiveError):
        fetch_module._network_fetch_range_bytes(
            "https://nomads.example/aigfs.grib2", start_byte=100, end_byte=131
        )
    assert len(calls) == fetch_module.RANGE_THROTTLE_TRIP_COUNT
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("range_throttle_cooldown_tripped", 0) == 1
    assert metrics["counters"].get("range_throttle_cooldown_skip", 0) == 1
    fetch_module.reset_herbie_runtime_caches_for_tests()


def test_network_fetch_range_bytes_success_resets_throttle_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    payload = b"GRIB" + (b"\0" * 28)
    responses = [
        _FakeResponse(b"x", status_code=302),
        _FakeResponse(b"x", status_code=302),
        _FakeResponse(payload),
        _FakeResponse(b"x", status_code=302),
    ]

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int, stream: bool = False):
        del url, headers, timeout, stream
        return responses.pop(0)

    monkeypatch.setattr(fetch_module.requests, "get", _fake_get)

    for _ in range(2):
        with pytest.raises(fetch_module._RangeRequestNotHonoredError):
            fetch_module._network_fetch_range_bytes(
                "https://nomads.example/aigfs.grib2", start_byte=0, end_byte=31
            )
    assert fetch_module._network_fetch_range_bytes(
        "https://nomads.example/aigfs.grib2", start_byte=0, end_byte=31
    ) == payload
    # Counter reset by the success — one more 302 must not trip the cooldown.
    with pytest.raises(fetch_module._RangeRequestNotHonoredError):
        fetch_module._network_fetch_range_bytes(
            "https://nomads.example/aigfs.grib2", start_byte=0, end_byte=31
        )
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("range_throttle_cooldown_tripped", 0) == 0
    fetch_module.reset_herbie_runtime_caches_for_tests()


def test_download_subset_skips_full_file_fallback_on_302_refusal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A 3xx range refusal means the full-file GET would meet the same refusal —
    the fallback must be skipped and the typed error propagated."""
    fetch_module.reset_herbie_runtime_caches_for_tests()

    class _FakeHerbie:
        idx = "https://nomads.example/aigfs.idx"
        grib = "https://nomads.example/aigfs.grib2"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([
                {"search_this": ":UGRD:850 mb:", "start_byte": 4, "end_byte": 35}
            ])

    def _fake_fetch_range_bytes(**kwargs):
        raise fetch_module._RangeRequestNotHonoredError("Range request not honored: status=302", status_code=302)

    def _fake_download_full_grib_to_path(*, source_url: str, out_path: Path) -> Path:
        raise AssertionError("full-file fallback must not run after a 3xx range refusal")

    monkeypatch.setattr(fetch_module, "_fetch_range_bytes", _fake_fetch_range_bytes)
    monkeypatch.setattr(fetch_module, "_download_full_grib_to_path", _fake_download_full_grib_to_path)

    with pytest.raises(fetch_module._RangeRequestNotHonoredError):
        fetch_module._download_subset_with_inventory_byte_range(
            _FakeHerbie(),
            search_pattern=":UGRD:850 mb:",
            out_path=tmp_path / "subset.grib2",
            model_id="aigfs",
            run_date=datetime(2026, 5, 28, 18, 0),
            product="pres",
            fh=198,
            priority="nomads",
            bundle_fetch_cache=None,
        )


def test_download_full_grib_to_path_rejects_non_200_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """raise_for_status passes 3xx — an unfollowable redirect body must not be
    saved as the 'full file'."""

    class _FakeStreamResponse(_FakeResponse):
        def iter_content(self, chunk_size: int):
            del chunk_size
            yield self.content

    _install_full_grib_response(
        monkeypatch,
        lambda: _FakeStreamResponse(b"<html>blocked</html>", status_code=302),
    )

    with pytest.raises(RuntimeError, match="status 302"):
        fetch_module._download_full_grib_to_path(
            source_url="https://nomads.example/aigfs.grib2",
            out_path=tmp_path / "full.grib2",
        )
    assert not (tmp_path / "full.grib2").exists()


def test_download_full_grib_uses_unique_temp_files_for_concurrent_callers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = b"GRIB" + (b"A" * 64)
    barrier = threading.Barrier(2)
    observed_part_names: set[str] = set()

    class _ConcurrentStreamResponse:
        status_code = 200
        headers = {"Content-Length": str(len(payload))}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            del chunk_size
            barrier.wait(timeout=2.0)
            observed_part_names.update(
                path.name for path in tmp_path.iterdir()
                if path.name.endswith(".part")
            )
            yield payload

        def close(self) -> None:
            return None

    _install_full_grib_response(monkeypatch, _ConcurrentStreamResponse)
    out_path = tmp_path / "full.grib2"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: fetch_module._download_full_grib_to_path(
                    source_url="https://example.invalid/full.grib2",
                    out_path=out_path,
                ),
                range(2),
            )
        )

    assert results == [out_path, out_path]
    assert len(observed_part_names) == 2
    assert out_path.read_bytes() == payload
    assert not [path for path in tmp_path.iterdir() if path.name.endswith(".part")]


def test_download_full_grib_failure_preserves_destination_and_removes_temp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "full.grib2"
    out_path.write_bytes(b"previous-complete-file")

    class _FailingStreamResponse:
        status_code = 200
        headers = {"Content-Length": "100"}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            del chunk_size
            yield b"GRIB-partial"
            raise OSError("stream interrupted")

        def close(self) -> None:
            return None

    _install_full_grib_response(monkeypatch, _FailingStreamResponse)

    with pytest.raises(OSError, match="stream interrupted"):
        fetch_module._download_full_grib_to_path(
            source_url="https://example.invalid/full.grib2",
            out_path=out_path,
        )

    assert out_path.read_bytes() == b"previous-complete-file"
    assert not [path for path in tmp_path.iterdir() if path.name.endswith(".part")]


def test_download_full_grib_enforces_wall_clock_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "full.grib2"
    out_path.write_bytes(b"previous-complete-file")
    clock = {"value": -0.6}

    def _monotonic() -> float:
        clock["value"] += 0.6
        return clock["value"]

    class _SlowStreamResponse:
        status_code = 200
        headers = {"Content-Length": "4"}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            del chunk_size
            yield b"GRIB"

        def close(self) -> None:
            return None

    monkeypatch.setenv("TWF_FULL_GRIB_DOWNLOAD_DEADLINE_SECONDS", "1")
    monkeypatch.setattr(fetch_module.time, "monotonic", _monotonic)
    _install_full_grib_response(monkeypatch, _SlowStreamResponse)

    with pytest.raises(TimeoutError, match="wall-clock deadline"):
        fetch_module._download_full_grib_to_path(
            source_url="https://example.invalid/full.grib2",
            out_path=out_path,
        )

    assert out_path.read_bytes() == b"previous-complete-file"
    assert not [path for path in tmp_path.iterdir() if path.name.endswith(".part")]


def test_download_full_grib_deadline_aborts_a_blocked_stream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream_cancelled = threading.Event()

    class _BlockedStreamContext:
        status_code = 200
        headers = {"Content-Length": "4"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self, chunk_size: int):
            del chunk_size
            try:
                await asyncio.sleep(0.4)
            except asyncio.CancelledError:
                stream_cancelled.set()
                raise
            raise AssertionError("deadline did not cancel the blocked stream")
            yield b""  # pragma: no cover - keeps this an async generator

    class _BlockedStreamClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

        def stream(self, method: str, url: str):
            del method, url
            return _BlockedStreamContext()

    monkeypatch.setattr(
        fetch_module,
        "_full_grib_download_deadline_seconds",
        lambda: 0.05,
    )
    monkeypatch.setattr(
        fetch_module,
        "_full_grib_http_client",
        lambda **_kwargs: _BlockedStreamClient(),
    )

    with pytest.raises(TimeoutError, match="wall-clock deadline"):
        fetch_module._download_full_grib_to_path(
            source_url="https://example.invalid/full.grib2",
            out_path=tmp_path / "full.grib2",
        )

    assert stream_cancelled.is_set()
    assert not [path for path in tmp_path.iterdir() if path.name.endswith(".part")]


def test_download_full_grib_deadline_cancels_response_header_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _SlowStreamContext:
        async def __aenter__(self):
            await asyncio.sleep(0.4)
            raise AssertionError("header acquisition was not cancelled")

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

    class _SlowAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

        def stream(self, method: str, url: str):
            del method, url
            return _SlowStreamContext()

    monkeypatch.setattr(
        fetch_module,
        "_full_grib_download_deadline_seconds",
        lambda: 0.05,
    )
    monkeypatch.setattr(
        fetch_module,
        "_full_grib_http_client",
        lambda **_kwargs: _SlowAsyncClient(),
    )

    started_at = time.monotonic()
    with pytest.raises(TimeoutError, match="wall-clock deadline"):
        fetch_module._download_full_grib_to_path(
            source_url="https://example.invalid/full.grib2",
            out_path=tmp_path / "full.grib2",
        )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.2


def test_full_source_fallback_uses_a_unique_consumable_file_per_caller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = b"GRIB0123456789"
    barrier = threading.Barrier(2)
    observed_full_paths: set[Path] = set()

    def _fake_download(
        *,
        source_url: str,
        out_path: Path,
        max_bytes: int | None = None,
    ) -> Path:
        del source_url
        assert max_bytes is None
        observed_full_paths.add(out_path)
        barrier.wait(timeout=2.0)
        out_path.write_bytes(payload)
        return out_path

    monkeypatch.setattr(fetch_module, "_download_full_grib_to_path", _fake_download)
    subset_out = tmp_path / "subset.grib2"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: fetch_module._fetch_subset_bytes_from_full_source(
                    "https://example.invalid/full.grib2",
                    out_path=subset_out,
                    start_byte=0,
                    end_byte=3,
                ),
                range(2),
            )
        )

    assert results == [b"GRIB", b"GRIB"]
    assert len(observed_full_paths) == 2
    assert not [path for path in tmp_path.iterdir() if path.name.endswith(".full")]


def test_eps_full_file_cache_lock_wait_covers_download_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_url = "https://example.invalid/eps.grib2"
    run_date = datetime(2026, 7, 16, 0, 0)
    download_started = threading.Event()
    download_calls = {"count": 0}

    def _slow_download(*, source_url: str, out_path: Path) -> Path:
        del source_url
        download_calls["count"] += 1
        download_started.set()
        # Stay inside the critical section beyond both the short test timeout
        # and its 100 ms polling interval.  Otherwise the waiter can
        # acquire on its first retry without observing that its deadline
        # expired.
        time.sleep(0.25)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"GRIB-complete")
        return out_path

    monkeypatch.setenv("TWF_EPS_FULL_FILE_CACHE_ENABLE", "1")
    monkeypatch.setenv("TWF_EPS_FULL_FILE_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("TWF_V3_GRIB_DISK_CACHE_LOCK", "1")
    monkeypatch.setenv("TWF_FULL_GRIB_DOWNLOAD_DEADLINE_SECONDS", "1")
    monkeypatch.setattr(fetch_module, "DEFAULT_GRIB_DISK_LOCK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(fetch_module, "_download_full_grib_to_path", _slow_download)
    monkeypatch.setattr(fetch_module, "_cleanup_eps_full_file_cache", lambda **_kwargs: None)
    herbie = SimpleNamespace(grib=source_url)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            fetch_module._maybe_get_eps_full_grib_path,
            herbie,
            model_id="ifs",
            product="enfo",
            run_date=run_date,
            fh=6,
            priority="azure",
        )
        assert download_started.wait(timeout=1.0)
        second = pool.submit(
            fetch_module._maybe_get_eps_full_grib_path,
            herbie,
            model_id="ifs",
            product="enfo",
            run_date=run_date,
            fh=6,
            priority="azure",
        )
        results = [first.result(), second.result()]

    assert results[0] is not None
    assert results[1] == results[0]
    assert download_calls["count"] == 1


def test_eps_full_file_cache_cleanup_reaps_stale_temp_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    monkeypatch.setenv("TWF_EPS_FULL_FILE_CACHE_ROOT", str(cache_root))
    run_dir = cache_root / "2026071600" / "fh006"
    run_dir.mkdir(parents=True)

    fresh_part = run_dir / ".eps.grib2.fresh123.part"
    stale_part = run_dir / ".eps.grib2.stale456.part"
    stale_full = run_dir / ".subset.grib2.stale789.full"
    regular_file = run_dir / "aaaa1111-eps.grib2"
    # A crash during the only fetch for a run+fh leaves the temp as the sole
    # entry in its directory — reaping must survive emptying that directory.
    orphan_dir = cache_root / "2026071600" / "fh012"
    orphan_dir.mkdir(parents=True)
    lone_stale_part = orphan_dir / ".eps.grib2.lone000.part"
    for path in (fresh_part, stale_part, stale_full, regular_file, lone_stale_part):
        path.write_bytes(b"GRIB")

    # Temp files are never visible to TTL expiry or size-based eviction.
    listed = {path for path, _size, _mtime in fetch_module._iter_cache_files(cache_root)}
    assert listed == {regular_file}

    stale_mtime = time.time() - (3.0 * fetch_module._full_grib_download_deadline_seconds())
    for path in (stale_part, stale_full, lone_stale_part):
        os.utime(path, (stale_mtime, stale_mtime))

    fetch_module._cleanup_eps_full_file_cache(force=True)

    assert fresh_part.exists()
    assert regular_file.exists()
    assert not stale_part.exists()
    assert not stale_full.exists()
    assert not lone_stale_part.exists()
    assert not orphan_dir.exists()


def test_pattern_negative_cache_records_and_expires() -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    key = fetch_module._pattern_negative_key(
        model_id="aigfs",
        run_date=datetime(2026, 7, 7, 18, 0),
        product="sfc",
        fh=336,
        priority="nomads",
        search_pattern=r":APCP:surface:0-[0-9]+ hour acc[^:]*:$",
    )
    assert fetch_module._pattern_negative_cache_remaining(key) == 0.0
    ttl = fetch_module._record_pattern_negative_cache(key)
    assert ttl > 0.0
    assert fetch_module._pattern_negative_cache_remaining(key) > 0.0
    # Repeat recording doubles the TTL up to the max.
    ttl_second = fetch_module._record_pattern_negative_cache(key)
    assert ttl_second >= ttl
    # A different pattern for the same frame is unaffected.
    other = fetch_module._pattern_negative_key(
        model_id="aigfs",
        run_date=datetime(2026, 7, 7, 18, 0),
        product="sfc",
        fh=336,
        priority="nomads",
        search_pattern=r":APCP:surface:0-[0-9]+ day acc[^:]*:$",
    )
    assert fetch_module._pattern_negative_cache_remaining(other) == 0.0
    fetch_module.reset_herbie_runtime_caches_for_tests()
    assert fetch_module._pattern_negative_cache_remaining(key) == 0.0


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
        if calls["count"] <= 3:
            return _FakeResponse(b"<Error>not ready</Error>".ljust(32, b" "))
        return _FakeResponse(b"GRIB" + (b"\0" * 28))

    monkeypatch.setenv("TWF_HERBIE_RANGE_RETRIES", "3")
    monkeypatch.setenv("TWF_HERBIE_RANGE_RETRY_BACKOFF_SECONDS", "0")
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
    assert calls["count"] == 4
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("invalid_grib_range_payload", 0) == 3
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
