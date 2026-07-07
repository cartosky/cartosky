from __future__ import annotations

import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import fetch as fetch_module


def _install_fake_herbie(monkeypatch: pytest.MonkeyPatch, herbie_cls: type) -> None:
    fake_core = types.ModuleType("herbie.core")
    fake_core.Herbie = herbie_cls
    fake_pkg = types.ModuleType("herbie")
    fake_pkg.core = fake_core
    monkeypatch.setitem(sys.modules, "herbie", fake_pkg)
    monkeypatch.setitem(sys.modules, "herbie.core", fake_core)


def _install_fake_rasterio_open(monkeypatch: pytest.MonkeyPatch) -> None:
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
            return np.ma.array([[1.0]], mask=[[False]], dtype=np.float32)

        def tags(self, *_args):
            return {}

    monkeypatch.setattr(fetch_module.rasterio, "open", lambda _path: _FakeDataset())


def test_inventory_cache_cap_uses_recent_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_CACHE_MAX_ENTRIES", "2")

    clock = {"now": 1000.0}
    monkeypatch.setattr(fetch_module.time, "monotonic", lambda: float(clock["now"]))

    fetch_module._inventory_cache_set("old-hit", object(), 600)
    clock["now"] += 1.0
    fetch_module._inventory_cache_set("old-unused", object(), 600)
    clock["now"] += 1.0

    assert fetch_module._inventory_cache_get("old-hit") is not None
    clock["now"] += 1.0
    fetch_module._inventory_cache_set("new-entry", object(), 600)

    assert "old-hit" in fetch_module._INVENTORY_CACHE
    assert "new-entry" in fetch_module._INVENTORY_CACHE
    assert "old-unused" not in fetch_module._INVENTORY_CACHE
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("inventory_cache_pruned", 0) == 1


def test_idx_negative_suppress_cap_preserves_new_suppression(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_IDX_NEGATIVE_CACHE_MAX_ENTRIES", "2")

    clock = {"now": 1000.0}
    monkeypatch.setattr(fetch_module.time, "monotonic", lambda: float(clock["now"]))

    run_a = datetime(2026, 3, 5, 0, 0)
    run_b = datetime(2026, 3, 5, 6, 0)
    run_new = datetime(2026, 3, 5, 12, 0)
    for run_date in (run_a, run_b):
        fetch_module._log_idx_missing_once(
            model_id="eps",
            run_date=run_date,
            product="enfo",
            fh=6,
            priority="aws",
            search_pattern=":TMP:",
            ttl_seconds=300,
            source="test",
        )

    caplog.clear()
    with caplog.at_level("WARNING", logger=fetch_module.logger.name):
        fetch_module._log_idx_missing_once(
            model_id="eps",
            run_date=run_new,
            product="enfo",
            fh=6,
            priority="aws",
            search_pattern=":TMP:",
            ttl_seconds=10,
            source="test",
        )
        fetch_module._log_idx_missing_once(
            model_id="eps",
            run_date=run_new,
            product="enfo",
            fh=6,
            priority="aws",
            search_pattern=":TMP:",
            ttl_seconds=10,
            source="test",
        )

    new_key = fetch_module._idx_negative_log_key(
        model_id="eps",
        run_date=run_new,
        product="enfo",
        fh=6,
    )
    assert new_key in fetch_module._IDX_NEGATIVE_LOG_SUPPRESS
    assert len(fetch_module._IDX_NEGATIVE_LOG_SUPPRESS) == 2
    assert sum(1 for record in caplog.records if "Herbie precheck unavailable" in record.message) == 1
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("idx_negative_log_suppress_pruned", 0) == 1


def test_no_idx_negative_cache_skips_repeated_herbie_calls_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeHerbie:
        calls = 0

        def __init__(self, *args, **kwargs):
            del args, kwargs
            type(self).calls += 1
            self.idx = None

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()

    clock = {"now": 1000.0}
    monkeypatch.setattr(fetch_module.time, "monotonic", lambda: float(clock["now"]))
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "2")
    monkeypatch.setenv("TWF_HERBIE_IDX_NEGATIVE_CACHE_INITIAL_TTL_SECONDS", "60")
    monkeypatch.setenv("TWF_HERBIE_IDX_NEGATIVE_CACHE_MAX_TTL_SECONDS", "300")

    kwargs = dict(
        model_id="hrrr",
        product="sfc",
        search_pattern=":TMP:2 m above ground:",
        run_date=datetime(2026, 3, 5, 17, 0),
        fh=13,
        herbie_kwargs={"priority": "aws"},
    )

    with pytest.raises(fetch_module.HerbieTransientUnavailableError):
        fetch_module.fetch_variable(**kwargs)
    assert _FakeHerbie.calls == 1

    with pytest.raises(fetch_module.HerbieTransientUnavailableError):
        fetch_module.fetch_variable(**kwargs)
    assert _FakeHerbie.calls == 1

    clock["now"] += 61.0
    with pytest.raises(fetch_module.HerbieTransientUnavailableError):
        fetch_module.fetch_variable(**kwargs)
    assert _FakeHerbie.calls == 2


def test_no_idx_negative_cache_isolated_by_minute_granularity(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeHerbie:
        calls = 0

        def __init__(self, *args, **kwargs):
            del args, kwargs
            type(self).calls += 1
            self.idx = None

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()

    clock = {"now": 1000.0}
    monkeypatch.setattr(fetch_module.time, "monotonic", lambda: float(clock["now"]))
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "2")
    monkeypatch.setenv("TWF_HERBIE_IDX_NEGATIVE_CACHE_INITIAL_TTL_SECONDS", "60")
    monkeypatch.setenv("TWF_HERBIE_IDX_NEGATIVE_CACHE_MAX_TTL_SECONDS", "300")

    base_kwargs = dict(
        model_id="rtma_ru",
        product="anl",
        search_pattern=":TMP:2 m above ground:",
        fh=0,
        herbie_kwargs={"priority": "aws"},
    )

    with pytest.raises(fetch_module.HerbieTransientUnavailableError):
        fetch_module.fetch_variable(
            run_date=datetime(2026, 5, 22, 13, 0),
            **base_kwargs,
        )
    assert _FakeHerbie.calls == 1

    with pytest.raises(fetch_module.HerbieTransientUnavailableError):
        fetch_module.fetch_variable(
            run_date=datetime(2026, 5, 22, 13, 15),
            **base_kwargs,
        )
    assert _FakeHerbie.calls == 2


def test_inventory_filter_matches_literal_parentheses_before_regex_fallback() -> None:
    index_df = pd.DataFrame(
        [
            {
                "search_this": ":PWAT:entire atmosphere (considered as a single layer):anl:",
                "start_byte": 0,
                "end_byte": 100,
            }
        ]
    )

    subset = fetch_module._inventory_filter(
        index_df,
        ":PWAT:entire atmosphere (considered as a single layer):",
    )

    assert subset is not None
    assert len(subset) == 1
    assert str(subset.iloc[0]["search_this"]).startswith(":PWAT:entire atmosphere")


def test_inventory_filter_preserves_regex_matching_for_windowed_patterns() -> None:
    index_df = pd.DataFrame(
        [
            {
                "search_this": ":APCP:surface:0-1 hour acc fcst:",
                "start_byte": 0,
                "end_byte": 100,
            }
        ]
    )

    subset = fetch_module._inventory_filter(
        index_df,
        r":APCP:surface:[0-9]+-[0-9]+ hour acc[^:]*:$",
    )

    assert subset is not None
    assert len(subset) == 1
    assert str(subset.iloc[0]["search_this"]) == ":APCP:surface:0-1 hour acc fcst:"


def test_prs_idx_missing_switches_to_nomads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pattern = ":TMP:850 mb:"

    class _FakeHerbie:
        init_priorities: list[str] = []
        download_priorities: list[str] = []
        idx_df_calls: dict[str, int] = {}

        def __init__(self, date: datetime, **kwargs):
            self.date = date
            self.model = kwargs.get("model")
            self.product = kwargs.get("product")
            self.fxx = kwargs.get("fxx")
            self.priority = kwargs.get("priority")
            type(self).init_priorities.append(str(self.priority))
            self.idx = None if self.priority == "aws" else f"https://{self.priority}.example/file.idx"
            self.grib = f"https://{self.priority}.example/file.grib2"

        @property
        def index_as_dataframe(self):
            current = str(self.priority)
            type(self).idx_df_calls[current] = int(type(self).idx_df_calls.get(current, 0)) + 1
            if self.priority == "aws":
                raise AssertionError("aws idx dataframe should not be requested when idx is missing")
            return pd.DataFrame([{"search_this": pattern, "start_byte": 0, "end_byte": 100}])

        def download(self, search_pattern: str, errors: str = "raise", overwrite: bool = True):
            del errors, overwrite
            assert search_pattern == pattern
            type(self).download_priorities.append(str(self.priority))
            out_path = tmp_path / f"{self.priority}.grib2"
            out_path.write_bytes(b"grib")
            return str(out_path)

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    _install_fake_rasterio_open(monkeypatch)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws,google,azure")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "4")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")

    fetch_module.fetch_variable(
        model_id="hrrr",
        product="prs",
        search_pattern=pattern,
        run_date=datetime(2026, 3, 5, 17, 0),
        fh=13,
    )

    assert _FakeHerbie.init_priorities == ["aws", "nomads"]
    assert _FakeHerbie.download_priorities == ["nomads"]
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("source_switch_count", 0) == 1
    assert metrics["counters"].get("prs_idx_lag_count", 0) == 1


def test_prs_idx_missing_pattern_switches_to_nomads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    requested_pattern = ":TMP:850 mb:"

    class _FakeHerbie:
        init_priorities: list[str] = []
        download_priorities: list[str] = []
        idx_df_calls: dict[str, int] = {}

        def __init__(self, date: datetime, **kwargs):
            self.date = date
            self.model = kwargs.get("model")
            self.product = kwargs.get("product")
            self.fxx = kwargs.get("fxx")
            self.priority = kwargs.get("priority")
            type(self).init_priorities.append(str(self.priority))
            self.idx = f"https://{self.priority}.example/file.idx"
            self.grib = f"https://{self.priority}.example/file.grib2"

        @property
        def index_as_dataframe(self):
            current = str(self.priority)
            type(self).idx_df_calls[current] = int(type(self).idx_df_calls.get(current, 0)) + 1
            if self.priority == "aws":
                return pd.DataFrame([{"search_this": ":RH:850 mb:", "start_byte": 0, "end_byte": 100}])
            return pd.DataFrame([{"search_this": requested_pattern, "start_byte": 0, "end_byte": 100}])

        def download(self, search_pattern: str, errors: str = "raise", overwrite: bool = True):
            del errors, overwrite
            assert search_pattern == requested_pattern
            type(self).download_priorities.append(str(self.priority))
            out_path = tmp_path / f"{self.priority}.grib2"
            out_path.write_bytes(b"grib")
            return str(out_path)

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    _install_fake_rasterio_open(monkeypatch)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws,google,azure")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "4")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")

    fetch_module.fetch_variable(
        model_id="hrrr",
        product="prs",
        search_pattern=requested_pattern,
        run_date=datetime(2026, 3, 5, 17, 0),
        fh=13,
    )

    assert _FakeHerbie.init_priorities == ["aws", "nomads"]
    assert _FakeHerbie.download_priorities == ["nomads"]


def test_prs_empty_idx_switches_to_nomads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pattern = ":TMP:850 mb:"

    class _FakeHerbie:
        init_priorities: list[str] = []
        download_priorities: list[str] = []

        def __init__(self, date: datetime, **kwargs):
            self.date = date
            self.model = kwargs.get("model")
            self.product = kwargs.get("product")
            self.fxx = kwargs.get("fxx")
            self.priority = kwargs.get("priority")
            type(self).init_priorities.append(str(self.priority))
            self.idx = f"https://{self.priority}.example/file.idx"
            self.grib = f"https://{self.priority}.example/file.grib2"

        @property
        def index_as_dataframe(self):
            if self.priority == "aws":
                return pd.DataFrame(columns=["search_this", "start_byte", "end_byte"])
            return pd.DataFrame([{"search_this": pattern, "start_byte": 0, "end_byte": 100}])

        def download(self, search_pattern: str, errors: str = "raise", overwrite: bool = True):
            del errors, overwrite
            assert search_pattern == pattern
            type(self).download_priorities.append(str(self.priority))
            out_path = tmp_path / f"{self.priority}.grib2"
            out_path.write_bytes(b"grib")
            return str(out_path)

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    _install_fake_rasterio_open(monkeypatch)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws,google,azure")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "3")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")

    fetch_module.fetch_variable(
        model_id="hrrr",
        product="prs",
        search_pattern=pattern,
        run_date=datetime(2026, 3, 5, 17, 0),
        fh=13,
    )

    assert _FakeHerbie.init_priorities == ["aws", "nomads"]
    assert _FakeHerbie.download_priorities == ["nomads"]


def test_prs_idx_match_uses_prs_without_switch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pattern = ":TMP:850 mb:"

    class _FakeHerbie:
        init_priorities: list[str] = []
        download_priorities: list[str] = []

        def __init__(self, date: datetime, **kwargs):
            self.date = date
            self.model = kwargs.get("model")
            self.product = kwargs.get("product")
            self.fxx = kwargs.get("fxx")
            self.priority = kwargs.get("priority")
            type(self).init_priorities.append(str(self.priority))
            self.idx = f"https://{self.priority}.example/file.idx"
            self.grib = f"https://{self.priority}.example/file.grib2"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([{"search_this": pattern, "start_byte": 0, "end_byte": 100}])

        def download(self, search_pattern: str, errors: str = "raise", overwrite: bool = True):
            del errors, overwrite
            assert search_pattern == pattern
            type(self).download_priorities.append(str(self.priority))
            out_path = tmp_path / f"{self.priority}.grib2"
            out_path.write_bytes(b"grib")
            return str(out_path)

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    _install_fake_rasterio_open(monkeypatch)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws,nomads")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "4")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")

    fetch_module.fetch_variable(
        model_id="hrrr",
        product="prs",
        search_pattern=pattern,
        run_date=datetime(2026, 3, 5, 17, 0),
        fh=13,
    )

    assert _FakeHerbie.init_priorities == ["aws"]
    assert _FakeHerbie.download_priorities == ["aws"]
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("source_switch_count", 0) == 0


def test_prs_idx_lag_does_not_retry_or_fan_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pattern = ":TMP:850 mb:"

    class _FakeHerbie:
        init_priorities: list[str] = []
        idx_df_calls: dict[str, int] = {}
        download_priorities: list[str] = []

        def __init__(self, date: datetime, **kwargs):
            self.date = date
            self.model = kwargs.get("model")
            self.product = kwargs.get("product")
            self.fxx = kwargs.get("fxx")
            self.priority = kwargs.get("priority")
            type(self).init_priorities.append(str(self.priority))
            self.idx = f"https://{self.priority}.example/file.idx"
            self.grib = f"https://{self.priority}.example/file.grib2"

        @property
        def index_as_dataframe(self):
            current = str(self.priority)
            type(self).idx_df_calls[current] = int(type(self).idx_df_calls.get(current, 0)) + 1
            if self.priority == "aws":
                raise RuntimeError("404 idx not found")
            return pd.DataFrame([{"search_this": pattern, "start_byte": 0, "end_byte": 100}])

        def download(self, search_pattern: str, errors: str = "raise", overwrite: bool = True):
            del errors, overwrite
            assert search_pattern == pattern
            type(self).download_priorities.append(str(self.priority))
            out_path = tmp_path / f"{self.priority}.grib2"
            out_path.write_bytes(b"grib")
            return str(out_path)

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    _install_fake_rasterio_open(monkeypatch)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws,google,azure,pando")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "5")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")

    fetch_module.fetch_variable(
        model_id="hrrr",
        product="prs",
        search_pattern=pattern,
        run_date=datetime(2026, 3, 5, 17, 0),
        fh=13,
    )

    assert _FakeHerbie.idx_df_calls.get("aws", 0) == 1
    assert _FakeHerbie.init_priorities == ["aws", "nomads"]
    assert _FakeHerbie.download_priorities == ["nomads"]


def test_inventory_cache_reuses_idx_for_multiple_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    index_df = pd.DataFrame(
        [
            {"search_this": ":TMP:850 mb:", "start_byte": 0, "end_byte": 100},
            {"search_this": ":RH:850 mb:", "start_byte": 101, "end_byte": 200},
        ]
    )

    class _FakeHerbie:
        idx_df_calls = 0

        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.idx = "https://aws.example/hrrr.t17z.wrfprsf13.grib2.idx"

        @property
        def index_as_dataframe(self):
            type(self).idx_df_calls += 1
            return index_df

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws")
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_CACHE_TTL_SECONDS", "600")

    common_kwargs = dict(
        model_id="hrrr",
        product="prs",
        run_date=datetime(2026, 3, 5, 17, 0),
        fh=13,
        herbie_kwargs={"priority": "aws"},
    )
    tmp_lines = fetch_module.inventory_lines_for_pattern(
        search_pattern=":TMP:850 mb:",
        **common_kwargs,
    )
    rh_lines = fetch_module.inventory_lines_for_pattern(
        search_pattern=":RH:850 mb:",
        **common_kwargs,
    )

    assert tmp_lines == [":TMP:850 mb:"]
    assert rh_lines == [":RH:850 mb:"]
    assert _FakeHerbie.idx_df_calls == 1
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("idx_cache_store", 0) == 1
    assert metrics["counters"].get("idx_cache_hit", 0) >= 1
    assert metrics["timers_ms"].get("idx_fetch_ms", {}).get("count", 0) == 1
    assert metrics["timers_ms"].get("idx_parse_ms", {}).get("count", 0) >= 2


def test_herbie_construction_defaults_to_quiet_verbose_false(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_verbose: list[object] = []

    class _FakeHerbie:
        def __init__(self, *args, **kwargs):
            del args
            seen_verbose.append(kwargs.get("verbose"))
            self.idx = None

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws")

    with pytest.raises(fetch_module.HerbieTransientUnavailableError):
        fetch_module.fetch_variable(
            model_id="hrrr",
            product="sfc",
            search_pattern=":TMP:2 m above ground:",
            run_date=datetime(2026, 3, 5, 17, 0),
            fh=13,
            herbie_kwargs={"priority": "aws"},
        )

    assert seen_verbose == [False]


def test_empty_inventory_dataframe_error_is_transient(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _FakeHerbie:
        idx = "https://nomads.example/aigfs.idx"
        grib = "https://nomads.example/aigfs.grib2"

        def __init__(self, *args, **kwargs):
            del args, kwargs

        @property
        def index_as_dataframe(self):
            return pd.DataFrame(
                [
                    {
                        "search_this": ":APCP:surface:0-1 day acc:",
                        "start_byte": 0,
                        "end_byte": 31,
                    }
                ]
            )

        def get_localFilePath(self, search_pattern: str):
            del search_pattern
            return str(tmp_path / "aigfs-empty-inventory.grib2")

        def download(self, *args, **kwargs):
            del args, kwargs
            raise ValueError("Cannot set a DataFrame without columns to the column search_this")

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "nomads")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    monkeypatch.setattr(fetch_module, "_download_subset_with_inventory_byte_range", lambda *args, **kwargs: None)

    with pytest.raises(fetch_module.HerbieTransientUnavailableError):
        fetch_module.fetch_variable(
            model_id="aigfs",
            product="sfc",
            search_pattern=":APCP:surface:0-[0-9]+ day acc[^:]*:$",
            run_date=datetime(2026, 5, 29, 18, 0),
            fh=168,
            herbie_kwargs={"priority": "nomads"},
        )


def test_readiness_probe_rejects_empty_inventory_for_probe_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeHerbie:
        idx = "https://nomads.example/aigfs.idx"
        grib = "https://nomads.example/aigfs.grib2"

        def __init__(self, *args, **kwargs):
            del args, kwargs

        @property
        def index_as_dataframe(self):
            return pd.DataFrame()

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()

    assert fetch_module.product_hour_has_any_idx(
        model_id="aigfs",
        product="sfc",
        search_pattern=":TMP:2 m above ground:",
        run_date=datetime(2026, 5, 30, 12, 0),
        fh=0,
        herbie_kwargs={"priority": "nomads"},
    ) is False


def test_precheck_empty_idx_fails_open_to_herbie_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_rasterio_open(monkeypatch)

    class _FakeHerbie:
        idx = "https://nomads.example/aigfs.idx"
        grib = "https://nomads.example/aigfs.grib2"

        def __init__(self, *args, **kwargs):
            del args, kwargs

        @property
        def index_as_dataframe(self):
            return pd.DataFrame()

        def get_localFilePath(self, search_pattern: str):
            del search_pattern
            return str(tmp_path / "aigfs-herbie-download.grib2")

        def download(self, search_pattern: str, **kwargs):
            del search_pattern, kwargs
            out_path = tmp_path / "aigfs-herbie-download.grib2"
            out_path.write_bytes(b"GRIB" + (b"\0" * 28))
            return out_path

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "nomads")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")

    data, _crs, _transform = fetch_module.fetch_variable(
        model_id="aigfs",
        product="sfc",
        search_pattern=":TMP:2 m above ground:",
        run_date=datetime(2026, 5, 30, 12, 0),
        fh=0,
        herbie_kwargs={"priority": "nomads"},
    )

    assert data.shape == (1, 1)


def test_no_space_left_on_device_is_transient(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _FakeHerbie:
        idx = "https://aws.example/hrrr.idx"
        grib = "https://aws.example/hrrr.grib2"

        def __init__(self, *args, **kwargs):
            del args, kwargs

        @property
        def index_as_dataframe(self):
            return pd.DataFrame(
                [
                    {
                        "search_this": ":CAPE:90-0 mb above ground:",
                        "start_byte": 0,
                        "end_byte": 31,
                    }
                ]
            )

        def get_localFilePath(self, search_pattern: str):
            del search_pattern
            raise OSError(28, "No space left on device", str(tmp_path))

        def download(self, search_pattern: str, **kwargs):
            del search_pattern, kwargs
            raise OSError(28, "No space left on device", str(tmp_path))

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    monkeypatch.setattr(fetch_module, "_download_subset_with_inventory_byte_range", lambda *args, **kwargs: None)

    with pytest.raises(fetch_module.HerbieTransientUnavailableError):
        fetch_module.fetch_variable(
            model_id="hrrr",
            product="sfc",
            search_pattern=":CAPE:90-0 mb above ground:",
            run_date=datetime(2026, 5, 30, 12, 0),
            fh=0,
            herbie_kwargs={"priority": "aws"},
        )


def test_herbie_index_unavailable_is_transient(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pattern = ":REFC:"

    class _FakeHerbie:
        idx = "https://nomads.example/hrrr.idx"
        grib = "https://nomads.example/hrrr.grib2"

        def __init__(self, *args, **kwargs):
            del args, kwargs

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([{"search_this": pattern, "start_byte": 0, "end_byte": 31}])

        def get_localFilePath(self, search_pattern: str):
            del search_pattern
            return str(tmp_path / "hrrr-refc.grib2")

        def download(self, search_pattern: str, **kwargs):
            del search_pattern, kwargs
            raise RuntimeError(
                "Cant open index file https://nomads.example/hrrr.idx\n"
                "Download the full file first (with `H.download()`)."
            )

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "nomads")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    monkeypatch.setattr(fetch_module, "_download_subset_with_inventory_byte_range", lambda *args, **kwargs: None)

    with pytest.raises(fetch_module.HerbieTransientUnavailableError):
        fetch_module.fetch_variable(
            model_id="hrrr",
            product="sfc",
            search_pattern=pattern,
            run_date=datetime(2026, 5, 30, 12, 0),
            fh=0,
            herbie_kwargs={"priority": "nomads"},
        )


def test_herbie_download_runs_before_direct_byte_range_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_rasterio_open(monkeypatch)
    pattern = ":TMP:2 m above ground:"
    events: list[str] = []

    class _FakeHerbie:
        idx = "https://aws.example/hrrr.idx"
        grib = "https://aws.example/hrrr.grib2"

        def __init__(self, *args, **kwargs):
            del args, kwargs

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([{"search_this": pattern, "start_byte": 0, "end_byte": 31}])

        def get_localFilePath(self, search_pattern: str):
            del search_pattern
            return str(tmp_path / "hrrr-tmp2m.grib2")

        def download(self, search_pattern: str, **kwargs):
            del search_pattern, kwargs
            events.append("herbie")
            raise RuntimeError("grib2 file not found")

    def _fake_direct(*args, **kwargs):
        del args
        events.append("direct")
        out_path = Path(kwargs["out_path"])
        out_path.write_bytes(b"GRIB" + (b"\0" * 28))
        return out_path

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    monkeypatch.setattr(fetch_module, "_download_subset_with_inventory_byte_range", _fake_direct)

    data, _crs, _transform = fetch_module.fetch_variable(
        model_id="hrrr",
        product="sfc",
        search_pattern=pattern,
        run_date=datetime(2026, 5, 30, 12, 0),
        fh=0,
        herbie_kwargs={"priority": "aws"},
    )

    assert data.shape == (1, 1)
    assert events == ["herbie", "direct"]


def test_inventory_cache_dedupes_inflight_idx_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    index_df = pd.DataFrame(
        [
            {
                "search_this": ":TMP:850 mb:",
                "start_byte": 0,
                "end_byte": 100,
            }
        ]
    )

    class _FakeHerbie:
        init_calls = 0
        idx_df_calls = 0
        _lock = threading.Lock()

        def __init__(self, *args, **kwargs):
            del args, kwargs
            type(self).init_calls += 1
            self.idx = "https://nomads.example/hrrr.t17z.wrfprsf13.grib2.idx"

        @property
        def index_as_dataframe(self):
            with type(self)._lock:
                type(self).idx_df_calls += 1
            time.sleep(0.1)
            return index_df

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws")
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_CACHE_TTL_SECONDS", "600")

    def _fetch_lines() -> list[str]:
        return fetch_module.inventory_lines_for_pattern(
            model_id="hrrr",
            product="prs",
            run_date=datetime(2026, 3, 5, 17, 0),
            fh=13,
            search_pattern=":TMP:850 mb:",
            herbie_kwargs={"priority": "aws"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        left_future = pool.submit(_fetch_lines)
        right_future = pool.submit(_fetch_lines)
        left = left_future.result()
        right = right_future.result()

    assert left == [":TMP:850 mb:"]
    assert right == [":TMP:850 mb:"]
    assert _FakeHerbie.init_calls == 2
    assert _FakeHerbie.idx_df_calls == 1


def test_inventory_cache_fetch_error_does_not_poison_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    index_df = pd.DataFrame([{"search_this": ":TMP:850 mb:", "start_byte": 0, "end_byte": 100}])

    class _FakeHerbie:
        idx_df_calls = 0

        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.idx = "https://aws.example/hrrr.t17z.wrfsfcf13.grib2.idx"

        @property
        def index_as_dataframe(self):
            type(self).idx_df_calls += 1
            if type(self).idx_df_calls == 1:
                raise RuntimeError("temporary idx parse failure")
            return index_df

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws")
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_CACHE_TTL_SECONDS", "600")

    kwargs = dict(
        model_id="hrrr",
        product="sfc",
        run_date=datetime(2026, 3, 5, 17, 0),
        fh=13,
        search_pattern=":TMP:850 mb:",
        herbie_kwargs={"priority": "aws"},
    )
    first = fetch_module.inventory_lines_for_pattern(**kwargs)
    second = fetch_module.inventory_lines_for_pattern(**kwargs)

    assert first == []
    assert second == [":TMP:850 mb:"]
    assert _FakeHerbie.idx_df_calls == 2
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("idx_cache_error", 0) >= 1
    assert metrics["counters"].get("idx_cache_store", 0) == 1


def test_inventory_search_refreshes_remote_idx_once_on_pattern_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    first_df = pd.DataFrame([
        {"search_this": ":TMP:850 mb:", "start_byte": 0, "end_byte": 100}
    ])
    refreshed_df = pd.DataFrame([
        {"search_this": ":TMP:850 mb:", "start_byte": 0, "end_byte": 100},
        {"search_this": ":UGRD:850 mb:", "start_byte": 101, "end_byte": 200},
    ])

    class _FakeHerbie:
        idx_df_calls = 0

        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.idx = "https://nomads.example/aigfs.t18z.pres.f198.grib2.idx"
            self.grib = "https://nomads.example/aigfs.t18z.pres.f198.grib2"
            self.priority = "nomads"
            self.model = "aigfs"
            self.product = "pres"
            self.fxx = 198

        @property
        def index_as_dataframe(self):
            type(self).idx_df_calls += 1
            if type(self).idx_df_calls == 1:
                return first_df
            return refreshed_df

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "nomads")
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_CACHE_TTL_SECONDS", "600")

    lines = fetch_module.inventory_lines_for_pattern(
        model_id="aigfs",
        product="pres",
        run_date=datetime(2026, 5, 28, 18, 0),
        fh=198,
        search_pattern=":UGRD:850 mb:",
        herbie_kwargs={"priority": "nomads"},
    )

    assert lines == [":UGRD:850 mb:"]
    assert _FakeHerbie.idx_df_calls == 2
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("idx_cache_pattern_refresh", 0) == 1


def test_inventory_search_refreshes_remote_idx_once_on_empty_idx(monkeypatch: pytest.MonkeyPatch) -> None:
    empty_df = pd.DataFrame(columns=["search_this", "start_byte", "end_byte"])
    refreshed_df = pd.DataFrame([
        {"search_this": ":UGRD:850 mb:", "start_byte": 101, "end_byte": 200},
    ])

    class _FakeHerbie:
        idx_df_calls = 0

        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.idx = "https://nomads.example/aigfs.t18z.pres.f198.grib2.idx"
            self.grib = "https://nomads.example/aigfs.t18z.pres.f198.grib2"
            self.priority = "nomads"
            self.model = "aigfs"
            self.product = "pres"
            self.fxx = 198

        @property
        def index_as_dataframe(self):
            cached = self.__dict__.get("index_as_dataframe")
            if cached is not None:
                return cached
            type(self).idx_df_calls += 1
            result = empty_df if type(self).idx_df_calls == 1 else refreshed_df
            self.__dict__["index_as_dataframe"] = result
            return result

    def _fake_idx_text_parser(idx_ref):
        assert idx_ref == "https://nomads.example/aigfs.t18z.pres.f198.grib2.idx"
        return refreshed_df

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "nomads")
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_CACHE_TTL_SECONDS", "600")
    monkeypatch.setattr(fetch_module, "_inventory_index_dataframe_from_idx_text", _fake_idx_text_parser)

    lines = fetch_module.inventory_lines_for_pattern(
        model_id="aigfs",
        product="pres",
        run_date=datetime(2026, 5, 28, 18, 0),
        fh=198,
        search_pattern=":UGRD:850 mb:",
        herbie_kwargs={"priority": "nomads"},
    )

    assert lines == [":UGRD:850 mb:"]
    assert _FakeHerbie.idx_df_calls == 1
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("idx_cache_empty_refresh", 0) == 1


def test_inventory_search_falls_back_to_raw_wgrib2_idx_when_herbie_df_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    idx_path = tmp_path / "nam_fh016.idx"
    idx_path.write_text(
        "1:0:d=2026052818:TMP:2 m above ground:16 hour fcst:\n"
        "2:120:d=2026052818:DPT:2 m above ground:16 hour fcst:\n",
        encoding="utf-8",
    )

    class _FakeHerbie:
        idx_df_calls = 0

        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.idx = str(idx_path)
            self.grib = "https://example.test/nam.t18z.conusnest.hiresf16.tm00.grib2"
            self.priority = "aws"
            self.model = "nam"
            self.product = "conusnest.hiresf"
            self.fxx = 16

        @property
        def index_as_dataframe(self):
            type(self).idx_df_calls += 1
            return pd.DataFrame(columns=["search_this", "start_byte", "end_byte"])

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws")
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_CACHE_TTL_SECONDS", "600")

    lines = fetch_module.inventory_lines_for_pattern(
        model_id="nam",
        product="conusnest.hiresf",
        run_date=datetime(2026, 5, 28, 18, 0),
        fh=16,
        search_pattern=":TMP:2 m above ground:",
        herbie_kwargs={"priority": "aws"},
    )

    assert lines == ["1:0:d=2026052818:TMP:2 m above ground:16 hour fcst:"]
    assert _FakeHerbie.idx_df_calls == 1


def test_inventory_search_falls_back_from_empty_local_idx_file_to_remote_idx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_idx = tmp_path / "cached-empty.idx"
    local_idx.write_text("", encoding="utf-8")
    remote_idx = "https://example.test/nam.t18z.conusnest.hiresf16.tm00.grib2.idx"

    class _FakeHerbie:
        idx_df_calls = 0

        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.idx = str(local_idx)
            self.grib = "https://example.test/nam.t18z.conusnest.hiresf16.tm00.grib2"
            self.priority = "aws"
            self.model = "nam"
            self.product = "conusnest.hiresf"
            self.fxx = 16

        @property
        def index_as_dataframe(self):
            type(self).idx_df_calls += 1
            return pd.DataFrame(columns=["search_this", "start_byte", "end_byte"])

    def _fake_idx_text_parser(idx_ref):
        if idx_ref == remote_idx:
            return pd.DataFrame([
                {"search_this": ":TMP:2 m above ground:", "start_byte": 0, "end_byte": 100}
            ])
        return fetch_module._inventory_index_dataframe_from_wgrib2_lines(idx_ref)

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws")
    monkeypatch.setenv("TWF_HERBIE_INVENTORY_CACHE_TTL_SECONDS", "600")
    monkeypatch.setattr(fetch_module, "_inventory_index_dataframe_from_idx_text", _fake_idx_text_parser)

    lines = fetch_module.inventory_lines_for_pattern(
        model_id="nam",
        product="conusnest.hiresf",
        run_date=datetime(2026, 5, 28, 18, 0),
        fh=16,
        search_pattern=":TMP:2 m above ground:",
        herbie_kwargs={"priority": "aws"},
    )

    assert lines == [":TMP:2 m above ground:"]
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("idx_cache_alt_source_refresh", 0) == 1


def test_grib_not_found_falls_back_to_manual_byte_range_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pattern = ":UGRD:850 mb:"
    subset_path = tmp_path / "manual_refresh.grib2"

    class _FakeHerbie:
        download_calls = 0

        def __init__(self, date: datetime, **kwargs):
            del date, kwargs
            self.idx = "https://nomads.example/aigfs.t18z.pres.f198.grib2.idx"
            self.grib = "https://nomads.example/aigfs.t18z.pres.f198.grib2"
            self.priority = "nomads"
            self.model = "aigfs"
            self.product = "pres"
            self.fxx = 198

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([
                {"search_this": pattern, "start_byte": 0, "end_byte": 99}
            ])

        def get_localFilePath(self, search_pattern: str) -> str:
            assert search_pattern == pattern
            return str(subset_path)

        def download(self, search_pattern: str, errors: str = "raise", overwrite: bool = True):
            del errors, overwrite
            assert search_pattern == pattern
            type(self).download_calls += 1
            raise RuntimeError("grib2 file not found")

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
            return np.ma.array([[1.0]], mask=[[False]], dtype=np.float32)

        def tags(self, *_args):
            return {}

    def _fake_download_subset(*args, **kwargs):
        force_inventory_refresh = bool(kwargs.get("force_inventory_refresh"))
        out_path = Path(kwargs["out_path"])
        if not force_inventory_refresh:
            return None
        out_path.write_bytes(b"grib")
        return out_path

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    _install_fake_rasterio_open(monkeypatch)
    monkeypatch.setattr(fetch_module, "_download_subset_with_inventory_byte_range", _fake_download_subset)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "nomads")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")

    data, crs, transform = fetch_module.fetch_variable(
        model_id="aigfs",
        product="pres",
        search_pattern=pattern,
        run_date=datetime(2026, 5, 28, 18, 0),
        fh=198,
        herbie_kwargs={"priority": ["nomads"]},
    )

    assert np.allclose(data, np.array([[1.0]], dtype=np.float32))
    assert crs == "EPSG:4326"
    assert transform == fetch_module.rasterio.transform.Affine.identity()
    assert _FakeHerbie.download_calls == 1
    assert subset_path.read_bytes() == b"grib"


@pytest.mark.parametrize(
    "open_error_message",
    [
        "not recognized as being in a supported file format.",
        "is a grib file, but no raster dataset was successfully identified.",
    ],
)
def test_invalid_cached_subset_is_deleted_and_refetched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    open_error_message: str,
) -> None:
    pattern = ":TMP:2 m above ground:"
    cached_subset = tmp_path / "cached_invalid.grib2"
    cached_subset.write_bytes(b"not-grib")

    class _FakeHerbie:
        download_calls = 0

        def __init__(self, date: datetime, **kwargs):
            del date, kwargs
            self.idx = "https://aws.example/gfs.idx"
            self.grib = "https://aws.example/gfs.grib2"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([{"search_this": pattern, "start_byte": 0, "end_byte": 99}])

        def get_localFilePath(self, search_pattern: str) -> str:
            assert search_pattern == pattern
            return str(cached_subset)

        def download(self, search_pattern: str, errors: str = "raise", overwrite: bool = False):
            del errors, overwrite
            assert search_pattern == pattern
            type(self).download_calls += 1
            cached_subset.write_bytes(b"grib")
            return str(cached_subset)

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
            return np.ma.array([[1.0]], mask=[[False]], dtype=np.float32)

        def tags(self, *_args):
            return {}

    def _fake_rasterio_open(path: str | Path):
        payload = Path(path).read_bytes()
        if payload != b"grib":
            raise fetch_module.rasterio.errors.RasterioIOError(
                f"{path!s} {open_error_message}"
            )
        return _FakeDataset()

    # Single-priority refetch now prefers the inventory byte-range path, so the
    # fake must actually produce a valid subset instead of returning None.
    byte_range_calls: list[str] = []

    def _fake_byte_range_download(H, *, search_pattern: str, out_path: Path, **kwargs):
        del H, kwargs
        assert search_pattern == pattern
        byte_range_calls.append(str(out_path))
        Path(out_path).write_bytes(b"grib")
        return Path(out_path)

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    monkeypatch.setattr(fetch_module, "_download_subset_with_inventory_byte_range", _fake_byte_range_download)
    monkeypatch.setattr(fetch_module.rasterio, "open", _fake_rasterio_open)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")
    monkeypatch.setenv("TWF_V3_GRIB_DISK_CACHE_LOCK", "1")

    data, crs, transform = fetch_module.fetch_variable(
        model_id="gfs",
        product="pgrb2.0p25",
        search_pattern=pattern,
        run_date=datetime(2026, 3, 10, 12, 0),
        fh=57,
    )

    assert np.allclose(data, np.array([[1.0]], dtype=np.float32))
    assert crs == "EPSG:4326"
    assert transform == fetch_module.rasterio.transform.Affine.identity()
    assert _FakeHerbie.download_calls == 0
    assert byte_range_calls == [str(cached_subset)]
    assert cached_subset.read_bytes() == b"grib"


def test_invalid_subset_falls_through_to_next_priority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pattern = ":ABSV:500 mb:"
    aws_subset = tmp_path / "aws_invalid.grib2"
    nomads_subset = tmp_path / "nomads_valid.grib2"
    download_calls: list[str] = []

    class _FakeHerbie:
        def __init__(self, date: datetime, **kwargs):
            del date
            self.priority = str(kwargs.get("priority"))
            self.idx = f"https://{self.priority}.example/nam.idx"
            self.grib = f"https://{self.priority}.example/nam.grib2"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([{"search_this": pattern, "start_byte": 0, "end_byte": 99}])

        def get_localFilePath(self, search_pattern: str) -> str:
            assert search_pattern == pattern
            if self.priority == "aws":
                return str(aws_subset)
            if self.priority == "nomads":
                return str(nomads_subset)
            raise AssertionError(f"unexpected priority: {self.priority}")

        def download(self, search_pattern: str, errors: str = "raise", overwrite: bool = False):
            del errors, overwrite
            assert search_pattern == pattern
            download_calls.append(self.priority)
            if self.priority == "aws":
                aws_subset.write_bytes(b"not-grib")
                return str(aws_subset)
            if self.priority == "nomads":
                nomads_subset.write_bytes(b"grib")
                return str(nomads_subset)
            raise AssertionError(f"unexpected priority: {self.priority}")

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
            return np.ma.array([[1.0]], mask=[[False]], dtype=np.float32)

        def tags(self, *_args):
            return {}

    def _fake_rasterio_open(path: str | Path):
        payload = Path(path).read_bytes()
        if payload != b"grib":
            raise fetch_module.rasterio.errors.RasterioIOError(
                f"{path!s} not recognized as being in a supported file format."
            )
        return _FakeDataset()

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    monkeypatch.setattr(fetch_module, "_download_subset_with_inventory_byte_range", lambda *args, **kwargs: None)
    monkeypatch.setattr(fetch_module.rasterio, "open", _fake_rasterio_open)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "aws,nomads")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")
    monkeypatch.setenv("TWF_V3_GRIB_DISK_CACHE_LOCK", "1")

    data, crs, transform = fetch_module.fetch_variable(
        model_id="nam",
        product="conusnest",
        search_pattern=pattern,
        run_date=datetime(2026, 5, 31, 0, 0),
        fh=44,
        herbie_kwargs={"priority": ["aws", "nomads"]},
    )

    assert np.allclose(data, np.array([[1.0]], dtype=np.float32))
    assert crs == "EPSG:4326"
    assert transform == fetch_module.rasterio.transform.Affine.identity()
    assert download_calls == ["aws", "nomads"]
    assert not aws_subset.exists()
    assert nomads_subset.read_bytes() == b"grib"


def test_invalid_subset_retries_same_single_priority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pattern = ":TMP:850 mb:"
    subset_path = tmp_path / "nomads_retry.grib2"
    download_calls: list[str] = []

    class _FakeHerbie:
        def __init__(self, date: datetime, **kwargs):
            del date
            self.priority = str(kwargs.get("priority"))
            self.idx = f"https://{self.priority}.example/aigfs.idx"
            self.grib = f"https://{self.priority}.example/aigfs.grib2"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([{"search_this": pattern, "start_byte": 0, "end_byte": 99}])

        def get_localFilePath(self, search_pattern: str) -> str:
            assert search_pattern == pattern
            return str(subset_path)

        def download(self, search_pattern: str, errors: str = "raise", overwrite: bool = False):
            del errors, overwrite
            assert search_pattern == pattern
            download_calls.append(self.priority)
            if len(download_calls) == 1:
                subset_path.write_bytes(b"not-grib")
            else:
                subset_path.write_bytes(b"grib")
            return str(subset_path)

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
            return np.ma.array([[1.0]], mask=[[False]], dtype=np.float32)

        def tags(self, *_args):
            return {}

    def _fake_rasterio_open(path: str | Path):
        payload = Path(path).read_bytes()
        if payload != b"grib":
            raise fetch_module.rasterio.errors.RasterioIOError(
                f"{path!s} not recognized as being in a supported file format."
            )
        return _FakeDataset()

    def _fake_range_download(*args, **kwargs):
        del args
        out_path = Path(kwargs["out_path"])
        out_path.write_bytes(b"grib")
        return out_path

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    monkeypatch.setattr(fetch_module, "_download_subset_with_inventory_byte_range", _fake_range_download)
    monkeypatch.setattr(fetch_module.rasterio, "open", _fake_rasterio_open)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "nomads")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")
    monkeypatch.setenv("TWF_V3_GRIB_DISK_CACHE_LOCK", "1")

    data, crs, transform = fetch_module.fetch_variable(
        model_id="aigfs",
        product="pres",
        search_pattern=pattern,
        run_date=datetime(2026, 6, 2, 12, 0),
        fh=240,
        herbie_kwargs={"priority": ["nomads"]},
    )

    assert np.allclose(data, np.array([[1.0]], dtype=np.float32))
    assert crs == "EPSG:4326"
    assert transform == fetch_module.rasterio.transform.Affine.identity()
    assert download_calls == ["nomads"]
    assert subset_path.read_bytes() == b"grib"


def test_invalid_subset_refresh_uses_inventory_byte_range(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pattern = ":TMP:850 mb:"
    subset_path = tmp_path / "aigfs_invalid_then_range.grib2"
    download_calls = {"herbie": 0, "range": 0}

    class _FakeHerbie:
        def __init__(self, date: datetime, **kwargs):
            del date, kwargs
            self.idx = "https://nomads.example/aigfs.idx"
            self.grib = "https://nomads.example/aigfs.grib2"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([{"search_this": pattern, "start_byte": 0, "end_byte": 99}])

        def get_localFilePath(self, search_pattern: str) -> str:
            assert search_pattern == pattern
            return str(subset_path)

        def download(self, search_pattern: str, errors: str = "raise", overwrite: bool = False):
            del errors, overwrite
            assert search_pattern == pattern
            download_calls["herbie"] += 1
            subset_path.write_bytes(b"not-grib")
            return str(subset_path)

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
            return np.ma.array([[1.0]], mask=[[False]], dtype=np.float32)

        def tags(self, *_args):
            return {}

    def _fake_rasterio_open(path: str | Path):
        payload = Path(path).read_bytes()
        if payload != b"grib":
            raise fetch_module.rasterio.errors.RasterioIOError(
                f"{path!s} not recognized as being in a supported file format."
            )
        return _FakeDataset()

    def _fake_range_download(*args, **kwargs):
        del args
        download_calls["range"] += 1
        out_path = Path(kwargs["out_path"])
        out_path.write_bytes(b"grib")
        return out_path

    _install_fake_herbie(monkeypatch, _FakeHerbie)
    monkeypatch.setattr(fetch_module.rasterio, "open", _fake_rasterio_open)
    monkeypatch.setattr(fetch_module, "_download_subset_with_inventory_byte_range", _fake_range_download)
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "nomads")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")
    monkeypatch.setenv("TWF_V3_GRIB_DISK_CACHE_LOCK", "1")

    data, crs, transform = fetch_module.fetch_variable(
        model_id="aigfs",
        product="pres",
        search_pattern=pattern,
        run_date=datetime(2026, 6, 2, 12, 0),
        fh=240,
        herbie_kwargs={"priority": ["nomads"]},
    )

    assert np.allclose(data, np.array([[1.0]], dtype=np.float32))
    assert crs == "EPSG:4326"
    assert transform == fetch_module.rasterio.transform.Affine.identity()
    assert download_calls == {"herbie": 1, "range": 1}
    assert subset_path.read_bytes() == b"grib"
