from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import numpy as np
import pandas as pd
import rasterio.crs
import rasterio.transform

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder.fetch import fetch_variable
from app.services.builder import fetch as fetch_module
from app.services.builder import derive as derive_module
from app.services.builder.pipeline import _build_contour_metadata_for_variable, build_frame
from app.services.builder.derive import FetchContext
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


def test_fetch_variable_uses_direct_ecmwf_eps_mean_before_pf_members(tmp_path: Path) -> None:
    class _FakeHerbieDirectMean(_FakeHerbie):
        def get_localFilePath(self, search_pattern: str) -> str:
            return str(tmp_path / f"direct-{search_pattern.strip(':').replace(':', '-')}.grib2")

        @property
        def index_as_dataframe(self):
            return pd.DataFrame(
                [
                    {"search_this": ":gh:500:pl:em:enfo", "type": "em", "start_byte": 0, "end_byte": 9},
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
        assert list(inventory["type"].astype(str)) == ["em"]
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

    contours_meta, contour_dir = _build_contour_metadata_for_variable(
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
    assert contour_dir is not None


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


def test_fetch_variable_reuses_cached_eps_full_grib(monkeypatch, tmp_path: Path) -> None:
    pattern = ":TMP:2 m above ground:"
    full_payload = b"0123456789abcdefghij"
    expected_subset = full_payload[4:10]

    class _FakeHerbieFullCache:
        def __init__(self, *_args, **kwargs) -> None:
            self.priority = kwargs.get("priority")
            self.grib = "https://example.invalid/2026041900-000h-enfo-ef.grib2"
            self.idx = "https://example.invalid/2026041900-000h-enfo-ef.grib2.idx"

        @property
        def index_as_dataframe(self):
            return pd.DataFrame([
                {"search_this": pattern, "start_byte": 4, "end_byte": 9},
            ])

        def get_localFilePath(self, search_pattern: str) -> str:
            assert search_pattern == pattern
            return str(tmp_path / "subset.grib2")

    class _FakeResponse:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload
            self.headers = {"Content-Length": str(len(payload))}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int = 1024 * 1024):
            del chunk_size
            yield self._payload

        def close(self) -> None:
            return None

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

    def _fake_requests_get(url: str, stream: bool = False, timeout: int = 90):
        assert stream is True
        assert timeout == 90
        request_calls.append(url)
        return _FakeResponse(full_payload)

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
        monkeypatch.setattr(fetch_module.requests, "get", _fake_requests_get)
        monkeypatch.setattr(fetch_module.rasterio, "open", _fake_rasterio_open)

        for _ in range(2):
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

    assert request_calls == ["https://example.invalid/2026041900-000h-enfo-ef.grib2"]
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("eps_full_file_cache_store", 0) == 1
    assert metrics["counters"].get("eps_full_file_cache_hit", 0) == 1
