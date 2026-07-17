from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
import rasterio.crs
import rasterio.transform

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder.fetch import fetch_variable
from app.services.builder import fetch as fetch_module
from app.services.builder import derive as derive_module
from app.services.builder.pipeline import (
    _build_contour_metadata_for_variable,
    _cleanup_artifacts,
    build_frame,
)
from app.services.builder.derive import FetchContext
from app.models.aifs import AIFS_MODEL
from app.models.eps import EPS_MODEL


class _FakeHerbie:
    def __init__(self, *_args, **_kwargs) -> None:
        self.priority = _kwargs.get("priority")
        self.grib = "https://example.invalid/eps-enfo.grib2"
        self.idx = "https://example.invalid/eps-enfo.index"

    def get_localFilePath(self, search_pattern: str) -> str:
        return f"/tmp/fake-{search_pattern.strip(':') or 'subset'}.grib2"

    @property
    def index_as_dataframe(self):
        return pd.DataFrame(
            [
                {"search_this": ":2t:sfc:g:0001:od:cf:enfo", "type": "cf", "number": np.nan, "start_byte": 0, "end_byte": 9},
                {"search_this": ":2t:sfc:1:g:0001:od:pf:enfo", "type": "pf", "number": 1, "start_byte": 10, "end_byte": 19},
                {"search_this": ":2t:sfc:2:g:0001:od:pf:enfo", "type": "pf", "number": 2, "start_byte": 20, "end_byte": 29},
            ]
        )


class _FakeHerbieBrokenIndex(_FakeHerbie):
    @property
    def index_as_dataframe(self):
        raise RuntimeError("idx parser failed")


def test_fetch_variable_aggregates_ecmwf_eps_pf_members() -> None:
    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _FakeHerbie

    def _fake_download_subset(_herbie, **kwargs):
        inventory = kwargs["inventory"]
        assert list(inventory["type"].astype(str)) == ["pf", "pf"]
        assert list(inventory["number"].astype(int)) == [1, 2]
        return kwargs["out_path"]

    def _fake_aggregate_subset(_path):
        data = np.array([[3.0, 4.0], [5.0, 6.0]], dtype=np.float32)
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0)
        return data, crs, transform, 2

    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        with patch("app.services.builder.fetch._download_subset_with_inventory_rows", side_effect=_fake_download_subset), patch(
            "app.services.builder.fetch._aggregate_grib_subset_mean", side_effect=_fake_aggregate_subset
        ):
            data, crs, transform, meta = fetch_variable(
                model_id="ifs",
                product="enfo",
                search_pattern=":2t:",
                run_date=datetime(2026, 4, 19, 0, 0),
                fh=0,
                herbie_kwargs={"_cartosky_fetch_aggregation": "ecmwf_pf_mean", "priority": ["azure"]},
                return_meta=True,
            )

    assert np.array_equal(data, np.array([[3.0, 4.0], [5.0, 6.0]], dtype=np.float32))
    assert crs.to_epsg() == 4326
    assert transform.c == -101.0
    assert transform.f == 46.0
    assert meta["inventory_line"] == ":2t:sfc:1:g:0001:od:pf:enfo"
    assert meta["member_count"] == 2


def test_fetch_variable_sorts_eps_pf_inventory_by_numeric_member(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class _UnsortedMemberHerbie(_FakeHerbie):
        def __init__(self, *_args, **kwargs) -> None:
            super().__init__(*_args, **kwargs)
            self.grib = "https://example.invalid/numeric-member-sort.grib2"
            self.idx = "https://example.invalid/numeric-member-sort.index"

        def get_localFilePath(self, search_pattern: str) -> str:
            del search_pattern
            return str(tmp_path / "numeric-member-sort.grib2")

        @property
        def index_as_dataframe(self):
            return pd.DataFrame(
                [
                    {"search_this": ":2t:sfc:10:g:0001:od:pf:enfo", "type": "pf", "number": "10", "start_byte": 10, "end_byte": 19},
                    {"search_this": ":2t:sfc:2:g:0001:od:pf:enfo", "type": "pf", "number": "2", "start_byte": 20, "end_byte": 29},
                    {"search_this": ":2t:sfc:1:g:0001:od:pf:enfo", "type": "pf", "number": "1", "start_byte": 30, "end_byte": 39},
                ]
            )

    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _UnsortedMemberHerbie
    seen_member_orders: list[list[str]] = []

    def _fake_download_subset(_herbie, **kwargs):
        inventory = kwargs["inventory"]
        seen_member_orders.append([str(value) for value in inventory["number"]])
        return kwargs["out_path"]

    def _fake_aggregate_subset(_path):
        data = np.array([[3.0]], dtype=np.float32)
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0)
        return data, crs, transform, 3

    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setattr(fetch_module, "_retry_sleep_seconds", lambda: 0.0)
    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        with patch(
            "app.services.builder.fetch._download_subset_with_inventory_rows",
            side_effect=_fake_download_subset,
        ), patch(
            "app.services.builder.fetch._aggregate_grib_subset_mean",
            side_effect=_fake_aggregate_subset,
        ):
            _data, _crs, _transform, meta = fetch_variable(
                model_id="ifs",
                product="enfo",
                search_pattern=":2t:",
                run_date=datetime(2026, 7, 16, 0, 0),
                fh=0,
                herbie_kwargs={
                    "_cartosky_fetch_aggregation": "ecmwf_pf_mean",
                    "priority": ["azure"],
                },
                return_meta=True,
            )

    assert seen_member_orders == [["1", "2", "10"]]
    assert meta["inventory_line"] == ":2t:sfc:1:g:0001:od:pf:enfo"


def test_fetch_variable_uses_raw_json_index_fallback_for_eps_pf_mean() -> None:
    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _FakeHerbieBrokenIndex

    index_lines = "\n".join(
        [
            json.dumps({"param": "2t", "levtype": "sfc", "type": "cf", "domain": "g", "expver": "0001", "class": "od", "stream": "enfo", "_offset": 0, "_length": 10}),
            json.dumps({"param": "2t", "levtype": "sfc", "type": "pf", "number": 1, "domain": "g", "expver": "0001", "class": "od", "stream": "enfo", "_offset": 10, "_length": 10}),
            json.dumps({"param": "2t", "levtype": "sfc", "type": "pf", "number": 2, "domain": "g", "expver": "0001", "class": "od", "stream": "enfo", "_offset": 20, "_length": 10}),
        ]
    )

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

        def close(self) -> None:
            return None

    def _fake_download_subset(_herbie, **kwargs):
        inventory = kwargs["inventory"]
        assert list(inventory["type"].astype(str)) == ["pf", "pf"]
        assert list(inventory["search_this"].astype(str)) == [
            ":2t:sfc:1:g:0001:od:pf:enfo",
            ":2t:sfc:2:g:0001:od:pf:enfo",
        ]
        return kwargs["out_path"]

    def _fake_aggregate_subset(_path):
        data = np.array([[3.0, 4.0], [5.0, 6.0]], dtype=np.float32)
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0)
        return data, crs, transform, 2

    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        with patch("app.services.builder.fetch.requests.get", return_value=_FakeResponse(index_lines)), patch(
            "app.services.builder.fetch._download_subset_with_inventory_rows", side_effect=_fake_download_subset
        ), patch("app.services.builder.fetch._aggregate_grib_subset_mean", side_effect=_fake_aggregate_subset):
            data, crs, transform, meta = fetch_variable(
                model_id="ifs",
                product="enfo",
                search_pattern=":2t:",
                run_date=datetime(2026, 4, 20, 0, 0),
                fh=150,
                herbie_kwargs={"_cartosky_fetch_aggregation": "ecmwf_pf_mean", "priority": ["azure"]},
                return_meta=True,
            )

    assert np.array_equal(data, np.array([[3.0, 4.0], [5.0, 6.0]], dtype=np.float32))
    assert crs.to_epsg() == 4326
    assert transform.c == -101.0
    assert transform.f == 46.0
    assert meta["inventory_line"] == ":2t:sfc:1:g:0001:od:pf:enfo"
    assert meta["member_count"] == 2


def test_fetch_variable_recovers_from_local_eps_idx_cache_using_signed_remote_index() -> None:
    fake_herbie_core = ModuleType("herbie.core")

    class _FakeHerbieLocalIdxCache(_FakeHerbie):
        def __init__(self, *_args, **kwargs) -> None:
            super().__init__(*_args, **kwargs)
            self.grib = "https://example.invalid/20260528120000-186h-enfo-ef.grib2?sig=abc123"
            self.idx = "/tmp/corrupt-local.index"

        @property
        def index_as_dataframe(self):
            raise RuntimeError("idx parser failed on cached local file")

    fake_herbie_core.Herbie = _FakeHerbieLocalIdxCache

    index_lines = "\n".join(
        [
            json.dumps({"param": "2t", "levtype": "sfc", "type": "cf", "domain": "g", "expver": "0001", "class": "od", "stream": "enfo", "_offset": 0, "_length": 10}),
            json.dumps({"param": "2t", "levtype": "sfc", "type": "pf", "number": 1, "domain": "g", "expver": "0001", "class": "od", "stream": "enfo", "_offset": 10, "_length": 10}),
            json.dumps({"param": "2t", "levtype": "sfc", "type": "pf", "number": 2, "domain": "g", "expver": "0001", "class": "od", "stream": "enfo", "_offset": 20, "_length": 10}),
        ]
    )
    requested_urls: list[str] = []

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

        def close(self) -> None:
            return None

    def _fake_requests_get(url: str, timeout: int = 45):
        requested_urls.append(url)
        assert url == "https://example.invalid/20260528120000-186h-enfo-ef.index?sig=abc123"
        assert timeout == 45
        return _FakeResponse(index_lines)

    def _fake_download_subset(_herbie, **kwargs):
        inventory = kwargs["inventory"]
        assert list(inventory["type"].astype(str)) == ["pf", "pf"]
        return kwargs["out_path"]

    def _fake_aggregate_subset(_path):
        data = np.array([[3.0, 4.0], [5.0, 6.0]], dtype=np.float32)
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0)
        return data, crs, transform, 2

    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        with patch("app.services.builder.fetch.requests.get", side_effect=_fake_requests_get), patch(
            "app.services.builder.fetch._download_subset_with_inventory_rows", side_effect=_fake_download_subset
        ), patch("app.services.builder.fetch._aggregate_grib_subset_mean", side_effect=_fake_aggregate_subset):
            data, crs, transform, meta = fetch_variable(
                model_id="ifs",
                product="enfo",
                search_pattern=":2t:",
                run_date=datetime(2026, 4, 20, 0, 0),
                fh=186,
                herbie_kwargs={"_cartosky_fetch_aggregation": "ecmwf_pf_mean", "priority": ["azure"]},
                return_meta=True,
            )

    assert requested_urls
    assert set(requested_urls) == {"https://example.invalid/20260528120000-186h-enfo-ef.index?sig=abc123"}
    assert np.array_equal(data, np.array([[3.0, 4.0], [5.0, 6.0]], dtype=np.float32))
    assert crs.to_epsg() == 4326
    assert transform.c == -101.0
    assert meta["member_count"] == 2


def test_ecmwf_eps_statistics_url_rewrites_grib_and_index_forms() -> None:
    base = "https://example.invalid/20260424120000-42h-enfo-ef"
    assert fetch_module._ecmwf_eps_statistics_url(
        f"{base}.grib2",
        requested_fh=42,
        statistics_fh=240,
    ).endswith("-240h-enfo-ep.grib2")
    assert fetch_module._ecmwf_eps_statistics_url(
        f"{base}.index",
        requested_fh=42,
        statistics_fh=240,
    ).endswith("-240h-enfo-ep.index")
    assert fetch_module._ecmwf_eps_statistics_url(
        f"{base}.grib2.index",
        requested_fh=42,
        statistics_fh=240,
    ).endswith("-240h-enfo-ep.grib2.index")


def test_eps_subset_fallback_path_uses_writable_temp_root(monkeypatch) -> None:
    monkeypatch.delenv("CARTOSKY_HERBIE_SAVE_DIR", raising=False)
    monkeypatch.delenv("HERBIE_SAVE_DIR", raising=False)

    path = fetch_module._eps_subset_fallback_path(prefix="eps_direct_mean", token="abc123")

    assert path == Path(tempfile.gettempdir()) / "cartosky-eps-subsets" / "eps_direct_mean_abc123.grib2"


@pytest.mark.parametrize(
    "aggregation",
    ["ecmwf_direct_mean", "ecmwf_pf_mean"],
)
def test_eps_subset_fallback_cache_is_isolated_by_model_run(
    monkeypatch,
    tmp_path: Path,
    aggregation: str,
) -> None:
    class _FakeHerbieFallback(_FakeHerbie):
        def __init__(self, run_date, *_args, **kwargs) -> None:
            super().__init__(run_date, *_args, **kwargs)
            run_token = run_date.strftime("%Y%m%d%H")
            self.grib = f"https://example.invalid/{run_token}-240h-enfo-ef.grib2"
            self.idx = f"https://example.invalid/{run_token}-240h-enfo-ef.index"

        def get_localFilePath(self, _search_pattern: str) -> str:
            raise RuntimeError("Herbie local subset path unavailable")

        @property
        def index_as_dataframe(self):
            return pd.DataFrame(
                [
                    {
                        "search_this": ":gh:500:pl:em:enfo",
                        "type": "em",
                        "step": "0",
                        "start_byte": 0,
                        "end_byte": 9,
                    },
                    {
                        "search_this": ":gh:500:pl:1:pf:enfo",
                        "type": "pf",
                        "number": 1,
                        "start_byte": 10,
                        "end_byte": 19,
                    },
                    {
                        "search_this": ":gh:500:pl:2:pf:enfo",
                        "type": "pf",
                        "number": 2,
                        "start_byte": 20,
                        "end_byte": 29,
                    },
                ]
            )

    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _FakeHerbieFallback
    downloaded_paths: list[Path] = []

    def _fake_download_subset(_herbie, **kwargs):
        downloaded_paths.append(kwargs["out_path"])
        return kwargs["out_path"]

    data = np.array([[5580.0, 5520.0]], dtype=np.float32)
    crs = rasterio.crs.CRS.from_epsg(4326)
    transform = rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0)

    monkeypatch.setenv("CARTOSKY_HERBIE_SAVE_DIR", str(tmp_path))
    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}), patch(
        "app.services.builder.fetch._subset_file_status", return_value=(False, 0)
    ), patch(
        "app.services.builder.fetch._download_subset_with_inventory_rows",
        side_effect=_fake_download_subset,
    ), patch(
        "app.services.builder.fetch._read_grib_raster",
        return_value=(data, crs, transform),
    ), patch(
        "app.services.builder.fetch._aggregate_grib_subset_mean",
        return_value=(data, crs, transform, 2),
    ):
        for run_date in (datetime(2026, 7, 10, 0), datetime(2026, 7, 10, 12)):
            fetch_variable(
                model_id="ifs",
                product="enfo",
                search_pattern=":gh:500:",
                run_date=run_date,
                fh=0,
                herbie_kwargs={
                    "_cartosky_fetch_aggregation": aggregation,
                    "priority": ["azure"],
                },
            )

    assert len(downloaded_paths) == 2
    assert downloaded_paths[0] != downloaded_paths[1]


def test_ecmwf_eps_step_filter_handles_herbie_timedelta_steps() -> None:
    inventory = pd.DataFrame(
        [
            {"type": "em", "step": pd.Timedelta(hours=36), "search_this": ":gh:500:pl:g:em:enfo:"},
            {"type": "em", "step": pd.Timedelta(hours=42), "search_this": ":gh:500:pl:g:em:enfo:"},
        ]
    )

    filtered = fetch_module._filter_inventory_step(inventory, fh=42)

    assert len(filtered) == 1
    assert filtered.iloc[0]["step"] == pd.Timedelta(hours=42)


def test_ecmwf_eps_step_filter_handles_numeric_and_string_steps() -> None:
    inventory = pd.DataFrame(
        [
            {"type": "em", "step": "210", "search_this": ":gh:500:pl:g:0001:od:em:enfo", "start_byte": 0},
            {"type": "em", "step": "24", "search_this": ":gh:500:pl:g:0001:od:em:enfo", "start_byte": 24},
            {"type": "em", "step": 216, "search_this": ":gh:500:pl:g:0001:od:em:enfo", "start_byte": 216},
        ]
    )

    filtered_24 = fetch_module._filter_inventory_step(inventory, fh=24)
    filtered_216 = fetch_module._filter_inventory_step(inventory, fh=216)

    assert len(filtered_24) == 1
    assert int(filtered_24.iloc[0]["start_byte"]) == 24
    assert len(filtered_216) == 1
    assert int(filtered_216.iloc[0]["start_byte"]) == 216


def test_fetch_variable_uses_direct_ecmwf_eps_mean_before_pf_members(tmp_path: Path) -> None:
    class _FakeHerbieDirectMean(_FakeHerbie):
        def __init__(self, *_args, **kwargs) -> None:
            self.priority = kwargs.get("priority")
            self.fxx = int(kwargs.get("fxx"))
            self.grib = f"https://example.invalid/2026041900-{self.fxx}h-enfo-ef.grib2"
            self.idx = f"https://example.invalid/2026041900-{self.fxx}h-enfo-ef.index"

        def get_localFilePath(self, search_pattern: str) -> str:
            return str(tmp_path / f"direct-{search_pattern.strip(':').replace(':', '-')}.grib2")

        @property
        def index_as_dataframe(self):
            return pd.DataFrame(
                [
                    {"search_this": ":gh:500:pl:em:enfo", "type": "em", "step": "210", "start_byte": 0, "end_byte": 9},
                    {"search_this": ":gh:500:pl:em:enfo", "type": "em", "step": "6", "start_byte": 30, "end_byte": 39},
                    {"search_this": ":gh:500:pl:1:pf:enfo", "type": "pf", "number": 1, "start_byte": 10, "end_byte": 19},
                    {"search_this": ":gh:500:pl:2:pf:enfo", "type": "pf", "number": 2, "start_byte": 20, "end_byte": 29},
                ]
            )

    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _FakeHerbieDirectMean
    calls: dict[str, int] = {"download": 0, "aggregate": 0, "read": 0}

    def _fake_download_subset(_herbie, **kwargs):
        calls["download"] += 1
        inventory = kwargs["inventory"]
        assert _herbie.fxx == 240
        assert str(_herbie.grib).endswith("-240h-enfo-ep.grib2")
        assert str(_herbie.idx).endswith("-240h-enfo-ep.index")
        assert list(inventory["type"].astype(str)) == ["em"]
        assert list(inventory["step"]) == ["6"]
        assert list(inventory["start_byte"].astype(int)) == [30]
        return kwargs["out_path"]

    def _fake_read_grib_raster(_path):
        calls["read"] += 1
        data = np.array([[5580.0, 5520.0]], dtype=np.float32)
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0)
        return data, crs, transform

    def _fake_aggregate_subset(_path):
        calls["aggregate"] += 1
        raise AssertionError("PF aggregation should not run when direct em exists")

    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        with patch("app.services.builder.fetch._download_subset_with_inventory_rows", side_effect=_fake_download_subset), patch(
            "app.services.builder.fetch._read_grib_raster", side_effect=_fake_read_grib_raster
        ), patch("app.services.builder.fetch._aggregate_grib_subset_mean", side_effect=_fake_aggregate_subset):
            data, crs, transform, meta = fetch_variable(
                model_id="ifs",
                product="enfo",
                search_pattern=":gh:500:",
                run_date=datetime(2026, 4, 19, 0, 0),
                fh=6,
                herbie_kwargs={"_cartosky_fetch_aggregation": "ecmwf_direct_mean_or_pf_mean", "priority": ["azure"]},
                return_meta=True,
            )

    assert np.array_equal(data, np.array([[5580.0, 5520.0]], dtype=np.float32))
    assert crs.to_epsg() == 4326
    assert transform.c == -101.0
    assert meta["aggregation"] == "ecmwf_direct_mean"
    assert meta["member_count"] == 1
    assert calls == {"download": 1, "aggregate": 0, "read": 1}


def test_direct_mean_missing_terminal_inventory_is_cached_until_late_frontier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _MissingStatisticsHerbie:
        init_calls = 0

        def __init__(self, *_args, **kwargs) -> None:
            type(self).init_calls += 1
            self.priority = kwargs.get("priority")
            self.fxx = int(kwargs.get("fxx"))
            self.grib = f"https://example.invalid/run-{self.fxx}h-enfo-ef.grib2"
            self.idx = f"https://example.invalid/run-{self.fxx}h-enfo-ef.index"

    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _MissingStatisticsHerbie
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "2")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")
    monkeypatch.setattr(
        fetch_module,
        "_inventory_search",
        lambda *_args, **_kwargs: fetch_module._InventorySearchResult(
            inventory=None,
            reason="idx_empty",
        ),
    )
    pf_calls: list[int] = []

    def _fake_pf_mean(**kwargs):
        pf_calls.append(int(kwargs["fh"]))
        return ("pf", int(kwargs["fh"]))

    monkeypatch.setattr(fetch_module, "_fetch_ecmwf_pf_mean_variable", _fake_pf_mean)

    kwargs = {
        "model_id": "ifs",
        "product": "enfo",
        "search_pattern": ":gh:500:",
        "run_date": datetime(2026, 4, 19, 0, 0),
        "herbie_kwargs": {"priority": ["azure"]},
        "bundle_fetch_cache": None,
        "return_meta": False,
        "fallback_to_pf_mean": True,
    }
    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        first = fetch_module._fetch_ecmwf_direct_mean_variable(fh=6, **kwargs)
        second = fetch_module._fetch_ecmwf_direct_mean_variable(fh=12, **kwargs)

    assert first == ("pf", 6)
    assert second == ("pf", 12)
    assert pf_calls == [6, 12]
    assert _MissingStatisticsHerbie.init_calls == 1
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("eps_direct_mean_negative_cache_store", 0) == 1
    assert metrics["counters"].get("eps_direct_mean_negative_cache_hit", 0) == 1


def test_direct_mean_exhausts_source_priorities_before_run_negative_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    valid_inventory = pd.DataFrame(
        [
            {
                "search_this": ":gh:500:pl:em:enfo",
                "type": "em",
                "step": 6,
                "start_byte": 0,
                "end_byte": 9,
            }
        ]
    )

    class _MirrorHerbie:
        priorities: list[str] = []

        def __init__(self, *_args, **kwargs) -> None:
            self.priority = str(kwargs.get("priority"))
            type(self).priorities.append(self.priority)
            self.fxx = int(kwargs.get("fxx"))
            self.grib = f"https://example.invalid/{self.priority}-{self.fxx}h-enfo-ef.grib2"
            self.idx = f"https://example.invalid/{self.priority}-{self.fxx}h-enfo-ef.index"

        def get_localFilePath(self, _search_pattern: str) -> str:
            return str(tmp_path / f"mirror-{self.priority}.grib2")

    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _MirrorHerbie
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "2")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")

    def _fake_inventory_search(herbie, **_kwargs):
        if herbie.priority == "azure":
            return fetch_module._InventorySearchResult(inventory=None, reason="idx_empty")
        return fetch_module._InventorySearchResult(inventory=valid_inventory, reason="ok")

    monkeypatch.setattr(fetch_module, "_inventory_search", _fake_inventory_search)
    monkeypatch.setattr(
        fetch_module,
        "_download_subset_with_inventory_rows",
        lambda _herbie, **kwargs: kwargs["out_path"],
    )
    direct_result = (
        np.array([[1.0]], dtype=np.float32),
        rasterio.crs.CRS.from_epsg(4326),
        rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0),
    )
    monkeypatch.setattr(fetch_module, "_read_grib_raster", lambda _path: direct_result)
    monkeypatch.setattr(
        fetch_module,
        "_fetch_ecmwf_pf_mean_variable",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("AWS direct mean should win")),
    )

    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        result = fetch_module._fetch_ecmwf_direct_mean_variable(
            model_id="ifs",
            product="enfo",
            search_pattern=":gh:500:",
            run_date=datetime(2026, 4, 19, 0, 0),
            fh=6,
            herbie_kwargs={"priority": ["azure", "aws", "ecmwf"]},
            bundle_fetch_cache=None,
            return_meta=False,
            fallback_to_pf_mean=True,
        )

    assert np.array_equal(result[0], direct_result[0])
    assert _MirrorHerbie.priorities == ["azure", "aws"]
    assert not fetch_module._EPS_DIRECT_MEAN_NEGATIVE_CACHE


def test_direct_mean_negative_cache_reprobes_at_late_frontier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inventory = pd.DataFrame(
        [
            {
                "search_this": ":gh:500:pl:em:enfo",
                "type": "em",
                "step": 216,
                "start_byte": 0,
                "end_byte": 9,
            }
        ]
    )

    class _LateStatisticsHerbie:
        init_calls = 0

        def __init__(self, *_args, **kwargs) -> None:
            type(self).init_calls += 1
            self.priority = kwargs.get("priority")
            self.fxx = int(kwargs.get("fxx"))
            self.grib = f"https://example.invalid/run-{self.fxx}h-enfo-ef.grib2"
            self.idx = f"https://example.invalid/run-{self.fxx}h-enfo-ef.index"

        def get_localFilePath(self, _search_pattern: str) -> str:
            return str(tmp_path / f"late-{self.fxx}.grib2")

    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _LateStatisticsHerbie
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    search_calls = {"count": 0}

    def _fake_inventory_search(*_args, **_kwargs):
        search_calls["count"] += 1
        if search_calls["count"] == 1:
            return fetch_module._InventorySearchResult(inventory=None, reason="idx_empty")
        return fetch_module._InventorySearchResult(inventory=inventory, reason="ok")

    monkeypatch.setattr(fetch_module, "_inventory_search", _fake_inventory_search)
    monkeypatch.setattr(
        fetch_module,
        "_fetch_ecmwf_pf_mean_variable",
        lambda **kwargs: ("pf", int(kwargs["fh"])),
    )
    monkeypatch.setattr(
        fetch_module,
        "_download_subset_with_inventory_rows",
        lambda _herbie, **kwargs: kwargs["out_path"],
    )
    direct_result = (
        np.array([[1.0]], dtype=np.float32),
        rasterio.crs.CRS.from_epsg(4326),
        rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0),
    )
    monkeypatch.setattr(fetch_module, "_read_grib_raster", lambda _path: direct_result)

    kwargs = {
        "model_id": "ifs",
        "product": "enfo",
        "search_pattern": ":gh:500:",
        "run_date": datetime(2026, 4, 19, 0, 0),
        "herbie_kwargs": {"priority": ["azure"]},
        "bundle_fetch_cache": None,
        "return_meta": False,
        "fallback_to_pf_mean": True,
    }
    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        assert fetch_module._fetch_ecmwf_direct_mean_variable(fh=6, **kwargs) == ("pf", 6)
        assert fetch_module._fetch_ecmwf_direct_mean_variable(fh=12, **kwargs) == ("pf", 12)
        result = fetch_module._fetch_ecmwf_direct_mean_variable(fh=216, **kwargs)

    assert np.array_equal(result[0], direct_result[0])
    assert _LateStatisticsHerbie.init_calls == 2
    assert search_calls["count"] == 2


def test_direct_mean_deterministic_inventory_miss_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _WrongFieldHerbie:
        init_calls = 0

        def __init__(self, *_args, **kwargs) -> None:
            type(self).init_calls += 1
            self.priority = kwargs.get("priority")
            self.fxx = int(kwargs.get("fxx"))
            self.grib = "https://example.invalid/run-enfo-ef.grib2"
            self.idx = "https://example.invalid/run-enfo-ef.index"

    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _WrongFieldHerbie
    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "3")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")
    monkeypatch.setattr(
        fetch_module,
        "_inventory_search",
        lambda *_args, **_kwargs: fetch_module._InventorySearchResult(
            inventory=pd.DataFrame(),
            reason="pattern_missing",
        ),
    )
    monkeypatch.setattr(fetch_module, "_fetch_ecmwf_pf_mean_variable", lambda **_kwargs: "pf")

    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        result = fetch_module._fetch_ecmwf_direct_mean_variable(
            model_id="ifs",
            product="enfo",
            search_pattern=":gh:500:",
            run_date=datetime(2026, 4, 19, 0, 0),
            fh=216,
            herbie_kwargs={"priority": ["azure"]},
            bundle_fetch_cache=None,
            return_meta=False,
            fallback_to_pf_mean=True,
        )

    assert result == "pf"
    assert _WrongFieldHerbie.init_calls == 1


def test_direct_mean_terminal_inventory_is_reused_for_the_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inventory = pd.DataFrame(
        [
            {"search_this": ":gh:500:pl:em:enfo", "type": "em", "step": 216, "start_byte": 0, "end_byte": 9},
            {"search_this": ":gh:500:pl:em:enfo", "type": "em", "step": 222, "start_byte": 10, "end_byte": 19},
        ]
    )

    class _StatisticsHerbie:
        index_calls = 0

        def __init__(self, *_args, **kwargs) -> None:
            self.priority = kwargs.get("priority")
            self.fxx = int(kwargs.get("fxx"))
            self.grib = f"https://example.invalid/run-{self.fxx}h-enfo-ef.grib2"
            self.idx = f"https://example.invalid/run-{self.fxx}h-enfo-ef.index"

        @property
        def index_as_dataframe(self):
            type(self).index_calls += 1
            return inventory

        def get_localFilePath(self, _search_pattern: str) -> str:
            return str(tmp_path / f"statistics-{self.fxx}.grib2")

    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _StatisticsHerbie
    fetch_module.reset_herbie_runtime_caches_for_tests()
    clock = {"now": 1000.0}
    monkeypatch.setattr(fetch_module.time, "monotonic", lambda: float(clock["now"]))
    monkeypatch.setattr(
        fetch_module,
        "_download_subset_with_inventory_rows",
        lambda _herbie, **kwargs: kwargs["out_path"],
    )
    direct_result = (
        np.array([[1.0]], dtype=np.float32),
        rasterio.crs.CRS.from_epsg(4326),
        rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0),
    )
    monkeypatch.setattr(fetch_module, "_read_grib_raster", lambda _path: direct_result)

    kwargs = {
        "model_id": "ifs",
        "product": "enfo",
        "search_pattern": ":gh:500:",
        "run_date": datetime(2026, 4, 19, 0, 0),
        "herbie_kwargs": {"priority": ["azure"]},
        "bundle_fetch_cache": None,
        "return_meta": False,
    }
    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        first = fetch_module._fetch_ecmwf_direct_mean_variable(fh=216, **kwargs)
        clock["now"] += 601.0
        second = fetch_module._fetch_ecmwf_direct_mean_variable(fh=222, **kwargs)

    assert np.array_equal(first[0], direct_result[0])
    assert np.array_equal(second[0], direct_result[0])
    assert _StatisticsHerbie.index_calls == 1


def test_fetch_variable_rebuilds_unreadable_cached_eps_pf_subset(tmp_path: Path) -> None:
    fake_herbie_core = ModuleType("herbie.core")

    class _FakeHerbieUnreadableSubset(_FakeHerbie):
        def get_localFilePath(self, search_pattern: str) -> str:
            return str(tmp_path / f"cached-{search_pattern.strip(':') or 'subset'}.grib2")

    fake_herbie_core.Herbie = _FakeHerbieUnreadableSubset
    subset_downloads = {"count": 0}

    def _fake_download_subset(_herbie, **kwargs):
        subset_downloads["count"] += 1
        out_path = kwargs["out_path"]
        out_path.write_bytes(b"GRIB")
        return out_path

    subset_path = tmp_path / "cached-2t.cartosky_pf.grib2"
    subset_path.write_bytes(b"corrupt")
    aggregate_calls = {"count": 0}

    def _fake_aggregate_subset(_path):
        aggregate_calls["count"] += 1
        if aggregate_calls["count"] == 1:
            raise rasterio.errors.RasterioIOError("no raster dataset was successfully identified")
        data = np.array([[7.0, 8.0]], dtype=np.float32)
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0)
        return data, crs, transform, 2

    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        with patch("app.services.builder.fetch._download_subset_with_inventory_rows", side_effect=_fake_download_subset), patch(
            "app.services.builder.fetch._aggregate_grib_subset_mean", side_effect=_fake_aggregate_subset
        ):
            data, crs, transform, meta = fetch_variable(
                model_id="ifs",
                product="enfo",
                search_pattern=":2t:",
                run_date=datetime(2026, 4, 19, 0, 0),
                fh=0,
                herbie_kwargs={"_cartosky_fetch_aggregation": "ecmwf_pf_mean", "priority": ["azure"]},
                return_meta=True,
            )

    assert subset_downloads["count"] == 1
    assert aggregate_calls["count"] == 2
    assert np.array_equal(data, np.array([[7.0, 8.0]], dtype=np.float32))
    assert crs.to_epsg() == 4326
    assert transform.c == -101.0
    assert meta["member_count"] == 2


def test_build_frame_uses_underlying_herbie_model_for_eps(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_fetch_variable(*, model_id: str, product: str, search_pattern: str, run_date: datetime, fh: int, herbie_kwargs=None, bundle_fetch_cache=None, return_meta=False):
        del search_pattern, run_date, fh, herbie_kwargs, bundle_fetch_cache
        captured["model_id"] = model_id
        captured["product"] = product
        data = np.array([[273.15, 273.15], [273.15, 273.15]], dtype=np.float32)
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0)
        if return_meta:
            return data, crs, transform, {"inventory_line": "fake"}
        return data, crs, transform

    monkeypatch.setattr("app.services.builder.pipeline.fetch_variable", _fake_fetch_variable)

    build_frame(
        model="eps",
        region="conus",
        var_id="tmp2m__mean",
        fh=0,
        run_date=datetime(2026, 4, 19, 0, 0),
        data_root=tmp_path,
        product="enfo",
        model_plugin=EPS_MODEL,
        ensemble_view="mean",
        fetch_ctx=FetchContext(coverage="conus"),
        log_fetch_cache_stats=False,
    )

    assert captured["model_id"] == "ifs"
    assert captured["product"] == "enfo"


def test_build_contour_metadata_uses_underlying_herbie_model_for_eps_anomaly(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def _fake_fetch_variable(*, model_id: str, product: str, search_pattern: str, run_date: datetime, fh: int, herbie_kwargs=None, bundle_fetch_cache=None, return_meta=False):
        del run_date, fh, bundle_fetch_cache, return_meta
        captured["model_id"] = model_id
        captured["product"] = product
        captured["search_pattern"] = search_pattern
        captured["fetch_aggregation"] = dict(herbie_kwargs or {}).get("_cartosky_fetch_aggregation")
        data = np.array([[5580.0, 5520.0], [5460.0, 5400.0]], dtype=np.float32)
        crs = rasterio.crs.CRS.from_epsg(3857)
        transform = rasterio.transform.from_origin(0.0, 20.0, 10.0, 10.0)
        return data, crs, transform

    def _fake_build_iso_contour_geojson(*, value_data, value_transform, value_crs, out_geojson_path, levels):
        del value_data, value_transform, value_crs, levels
        out_geojson_path.parent.mkdir(parents=True, exist_ok=True)
        out_geojson_path.write_text('{"type":"FeatureCollection","features":[]}')

    def _fake_warp_to_target_grid(data, src_crs, src_transform, *, model, region, resampling="bilinear", src_nodata=None, dst_nodata=float("nan")):
        del src_crs, model, region, resampling, src_nodata, dst_nodata
        return data, src_transform

    monkeypatch.setattr("app.services.builder.pipeline.fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr("app.services.builder.pipeline.build_iso_contour_geojson", _fake_build_iso_contour_geojson)
    monkeypatch.setattr("app.services.builder.pipeline.warp_to_target_grid", _fake_warp_to_target_grid)

    var_spec = EPS_MODEL.get_var("hgt500_anom")
    assert var_spec is not None

    prior_contour = tmp_path / "contours" / "fh006_height_500mb.geojson"
    prior_contour.parent.mkdir(parents=True)
    prior_contour.write_text('{"type":"FeatureCollection","features":[]}')

    contours_meta, contour_path = _build_contour_metadata_for_variable(
        model="eps",
        run_date=datetime(2026, 4, 24, 6, 0),
        fh=0,
        product="enfo",
        var_key="hgt500_anom",
        region="na",
        model_plugin=EPS_MODEL,
        var_spec_model=var_spec,
        dst_transform=rasterio.transform.from_origin(0.0, 20.0, 10.0, 10.0),
        staging_dir=tmp_path,
        fetch_ctx=None,
        ensemble_view="mean",
    )

    assert captured["model_id"] == "ifs"
    assert captured["product"] == "enfo"
    assert captured["search_pattern"] == ":gh:500:"
    assert captured["fetch_aggregation"] == "ecmwf_direct_mean_or_pf_mean"
    assert isinstance(contours_meta, dict)
    assert contour_path == tmp_path / "contours" / "fh000_height_500mb.geojson"

    _cleanup_artifacts(contour_path)

    assert not contour_path.exists()
    assert prior_contour.is_file()


def test_build_contour_metadata_reuses_cached_warped_eps_anomaly_component(
    monkeypatch, tmp_path: Path
) -> None:
    fetch_calls = {"count": 0}

    def _fake_fetch_variable(**_kwargs):
        fetch_calls["count"] += 1
        raise AssertionError("contour component should come from derive warp cache")

    def _fake_build_iso_contour_geojson(*, value_data, value_transform, value_crs, out_geojson_path, levels):
        assert np.nanmax(value_data) == 558.0
        del value_transform, value_crs, levels
        out_geojson_path.parent.mkdir(parents=True, exist_ok=True)
        out_geojson_path.write_text('{"type":"FeatureCollection","features":[]}')

    monkeypatch.setattr("app.services.builder.pipeline.fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr("app.services.builder.pipeline.build_iso_contour_geojson", _fake_build_iso_contour_geojson)

    var_spec = EPS_MODEL.get_var("hgt500_anom")
    assert var_spec is not None
    component_spec = EPS_MODEL.get_var("hgt500__mean")
    assert component_spec is not None
    selectors = component_spec.selectors
    target_grid_id = "climatology:era5:na:25000.0m"
    run_date = datetime(2026, 4, 24, 6, 0, tzinfo=timezone.utc)
    ctx = FetchContext(coverage="na")
    cache_key = (
        "eps",
        "enfo",
        run_date.isoformat(),
        6,
        "hgt500__mean",
        derive_module._selector_fingerprint(selectors),
        target_grid_id,
        "bilinear",
    )
    ctx.warp_cache[cache_key] = (
        np.array([[5580.0, 5520.0], [5460.0, 5400.0]], dtype=np.float32),
        rasterio.crs.CRS.from_epsg(3857),
        rasterio.transform.from_origin(0.0, 20.0, 10.0, 10.0),
    )

    contours_meta, contour_dir = _build_contour_metadata_for_variable(
        model="eps",
        run_date=run_date,
        fh=6,
        product="enfo",
        var_key="hgt500_anom__mean",
        region="na",
        model_plugin=EPS_MODEL,
        var_spec_model=var_spec,
        dst_transform=rasterio.transform.from_origin(0.0, 20.0, 10.0, 10.0),
        staging_dir=tmp_path,
        fetch_ctx=ctx,
        ensemble_view="mean",
    )

    assert fetch_calls["count"] == 0
    assert isinstance(contours_meta, dict)
    assert contour_dir is not None


def test_build_contour_metadata_reuses_cached_warped_aifs_anomaly_component_with_dam_levels(
    monkeypatch, tmp_path: Path
) -> None:
    fetch_calls = {"count": 0}
    captured = {}

    def _fake_fetch_variable(**_kwargs):
        fetch_calls["count"] += 1
        raise AssertionError("contour component should come from derive warp cache")

    def _fake_build_iso_contour_geojson(*, value_data, value_transform, value_crs, out_geojson_path, levels):
        captured["max"] = float(np.nanmax(value_data))
        captured["levels"] = list(levels)
        del value_transform, value_crs
        out_geojson_path.parent.mkdir(parents=True, exist_ok=True)
        out_geojson_path.write_text('{"type":"FeatureCollection","features":[]}')

    monkeypatch.setattr("app.services.builder.pipeline.fetch_variable", _fake_fetch_variable)
    monkeypatch.setattr("app.services.builder.pipeline.build_iso_contour_geojson", _fake_build_iso_contour_geojson)

    var_spec = AIFS_MODEL.get_var("hgt500_anom")
    assert var_spec is not None
    component_spec = AIFS_MODEL.get_var("hgt500")
    assert component_spec is not None
    selectors = component_spec.selectors
    target_grid_id = "climatology:era5:na:25000.0m"
    run_date = datetime(2026, 4, 24, 6, 0, tzinfo=timezone.utc)
    ctx = FetchContext(coverage="na")
    cache_key = (
        "aifs",
        "oper",
        run_date.isoformat(),
        6,
        "hgt500",
        derive_module._selector_fingerprint(selectors),
        target_grid_id,
        "bilinear",
    )
    ctx.warp_cache[cache_key] = (
        np.array([[54730.987, 54142.087], [53553.187, 52964.287]], dtype=np.float32),
        rasterio.crs.CRS.from_epsg(3857),
        rasterio.transform.from_origin(0.0, 20.0, 10.0, 10.0),
    )

    contours_meta, contour_dir = _build_contour_metadata_for_variable(
        model="aifs",
        run_date=run_date,
        fh=6,
        product="oper",
        var_key="hgt500_anom",
        region="na",
        model_plugin=AIFS_MODEL,
        var_spec_model=var_spec,
        dst_transform=rasterio.transform.from_origin(0.0, 20.0, 10.0, 10.0),
        staging_dir=tmp_path,
        fetch_ctx=ctx,
    )

    assert fetch_calls["count"] == 0
    assert captured["max"] < 624.0
    assert captured["max"] > 500.0
    assert 480.0 in captured["levels"]
    assert 552.0 in captured["levels"]
    assert 624.0 in captured["levels"]
    assert isinstance(contours_meta, dict)
    assert contour_dir is not None


def test_fetch_variable_reuses_cached_eps_full_grib(monkeypatch, tmp_path: Path) -> None:
    pattern = ":TMP:2 m above ground:"
    full_payload = b"GRIB0123456789abcdefghij"
    expected_subset = full_payload[:20]

    class _FakeHerbieFullCache:
        def __init__(self, *_args, **kwargs) -> None:
            self.priority = kwargs.get("priority")
            self.grib = "https://example.invalid/2026041900-000h-enfo-ef.grib2"
            self.idx = "https://example.invalid/2026041900-000h-enfo-ef.grib2.idx"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([
                {"search_this": pattern, "start_byte": 0, "end_byte": 19},
            ])

        def get_localFilePath(self, search_pattern: str) -> str:
            assert search_pattern == pattern
            return str(tmp_path / "subset.grib2")

    class _FakeDataset:
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = rasterio.transform.Affine.identity()
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
        assert Path(path).read_bytes() == expected_subset
        return _FakeDataset()

    class _FakeFullGribResponse:
        status_code = 200
        headers = {"Content-Length": str(len(full_payload))}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self, chunk_size: int):
            del chunk_size
            yield full_payload

    class _FakeFullGribClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

        def stream(self, method: str, url: str):
            assert method == "GET"
            request_calls.append(url)
            return _FakeFullGribResponse()

    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _FakeHerbieFullCache
    request_calls: list[str] = []

    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "azure")
    monkeypatch.setenv("TWF_HERBIE_SUBSET_RETRIES", "1")
    monkeypatch.setenv("TWF_HERBIE_RETRY_SLEEP_SECONDS", "0")
    monkeypatch.setenv("TWF_EPS_FULL_FILE_CACHE_ENABLE", "1")
    monkeypatch.setenv("TWF_EPS_FULL_FILE_CACHE_ROOT", str(tmp_path / "full-cache"))

    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        monkeypatch.setattr(
            fetch_module,
            "_full_grib_http_client",
            lambda **_kwargs: _FakeFullGribClient(),
        )
        monkeypatch.setattr(fetch_module.rasterio, "open", _fake_rasterio_open)

        for call_index in range(2):
            data, crs, transform = fetch_variable(
                model_id="ifs",
                product="enfo",
                search_pattern=pattern,
                run_date=datetime(2026, 4, 19, 0, 0),
                fh=0,
                herbie_kwargs={"priority": ["azure"]},
            )
            assert np.allclose(data, np.array([[1.0]], dtype=np.float32))
            assert crs.to_epsg() == 4326
            assert transform == rasterio.transform.Affine.identity()
            if call_index == 0:
                # This test isolates full-file-cache reuse.  Standard fetches
                # now correctly reuse their derived subset before reaching the
                # full-file fallback, so remove that subset between calls.
                (tmp_path / "subset.grib2").unlink()

    assert request_calls == ["https://example.invalid/2026041900-000h-enfo-ef.grib2"]
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("eps_full_file_cache_store", 0) == 1
    assert metrics["counters"].get("eps_full_file_cache_hit", 0) == 1


def test_fetch_variable_rejects_partial_eps_pf_mean_aggregation(monkeypatch, tmp_path: Path) -> None:
    """Audit 4.3: a subset covering fewer bands than the pf inventory must fail
    loudly (and evict the cached partial subset) instead of shipping a mean
    silently computed over fewer members."""

    class _TmpPathHerbie(_FakeHerbie):
        def get_localFilePath(self, search_pattern: str) -> str:
            return str(tmp_path / "eps-subset.grib2")

    fake_herbie_core = ModuleType("herbie.core")
    fake_herbie_core.Herbie = _TmpPathHerbie

    subset_paths: list[Path] = []

    def _fake_download_subset(_herbie, **kwargs):
        out_path = Path(kwargs["out_path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"GRIB-partial-subset")
        subset_paths.append(out_path)
        return out_path

    def _fake_aggregate_subset(_path):
        data = np.array([[3.0]], dtype=np.float32)
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = rasterio.transform.from_origin(-101.0, 46.0, 1.0, 1.0)
        # The _FakeHerbie inventory lists 2 pf members; only 1 band made it.
        return data, crs, transform, 1

    monkeypatch.setattr(fetch_module, "_retry_sleep_seconds", lambda: 0.0)

    with patch.dict(sys.modules, {"herbie.core": fake_herbie_core}):
        with patch(
            "app.services.builder.fetch._download_subset_with_inventory_rows",
            side_effect=_fake_download_subset,
        ), patch(
            "app.services.builder.fetch._aggregate_grib_subset_mean",
            side_effect=_fake_aggregate_subset,
        ):
            with pytest.raises(RuntimeError, match="pf-mean") as excinfo:
                fetch_variable(
                    model_id="ifs",
                    product="enfo",
                    search_pattern=":2t:",
                    run_date=datetime(2026, 4, 19, 0, 0),
                    fh=0,
                    herbie_kwargs={"_cartosky_fetch_aggregation": "ecmwf_pf_mean", "priority": ["azure"]},
                    return_meta=True,
                )

    assert "covered 1 of 2 perturbed members" in str(excinfo.value.__cause__)
    # The partial subset must not be left behind as a cached poison file that
    # every retry would silently reuse.
    assert subset_paths
    assert all(not path.exists() for path in subset_paths)
