import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_test_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import main as main_module

pytestmark = pytest.mark.anyio


def _reset_main_caches() -> None:
    with main_module._ds_cache_lock:
        for ds in main_module._ds_cache.values():
            try:
                ds.close()
            except Exception:
                pass
        main_module._ds_cache.clear()

    with main_module._sample_lock:
        main_module._sample_cache.clear()
        main_module._sample_inflight.clear()
        main_module._sample_rate_window.clear()

    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    main_module._sample_transformer.cache_clear()


def _write_value_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.array(
        [
            [1.34, 2.21, 3.09],
            [4.04, -9999.0, np.nan],
            [7.77, 8.88, 9.99],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=-9999.0,
    ) as ds:
        ds.write(data, 1)


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    data_root = tmp_path / "data" / "v3"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"

    model = "hrrr"
    run_id = "20260306_00z"
    variable = "tmp2m"
    fh = 1

    manifest_dir = manifests_root / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "variables": {
                    variable: {
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [{"fh": fh}],
                    }
                }
            }
        )
    )

    model_root = published_root / model
    model_root.mkdir(parents=True, exist_ok=True)
    (model_root / "LATEST.json").write_text(json.dumps({"run_id": run_id}))

    var_dir = model_root / run_id / variable
    _write_value_raster(var_dir / f"fh{fh:03d}.val.cog.tif")
    (var_dir / f"fh{fh:03d}.json").write_text(
        json.dumps({"units": "K", "valid_time": "2026-03-06T01:00:00Z"})
    )

    # The fixture publishes COG-only frames: opt hrrr out of the (now default)
    # binary-only substrate; substrate-flip tests clear this mid-test.
    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "hrrr")
    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)

    _reset_main_caches()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    _reset_main_caches()


async def test_sample_batch_returns_values_for_valid_points(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v4/sample/batch",
        json={
            "model": "hrrr",
            "run": "latest",
            "variable": "tmp2m",
            "forecast_hour": 1,
            "points": [
                {"id": "SD_1", "lat": 45.5, "lon": -100.5},
                {"id": "SD_2", "lat": 44.5, "lon": -100.5},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "units": "K",
        "values": {
            "SD_1": 1.3,
            "SD_2": 4.0,
        },
    }


async def test_sample_batch_returns_null_for_out_of_bounds_and_nodata(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v4/sample/batch",
        json={
            "model": "hrrr",
            "run": "latest",
            "variable": "tmp2m",
            "forecast_hour": 1,
            "points": [
                {"id": "OOB", "lat": 60.0, "lon": -120.0},
                {"id": "NODATA", "lat": 44.5, "lon": -99.5},
                {"id": "NAN", "lat": 44.5, "lon": -98.5},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "units": "K",
        "values": {
            "OOB": None,
            "NODATA": None,
            "NAN": None,
        },
    }


async def test_sample_batch_invalid_payload_returns_422_detail(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v4/sample/batch",
        json={
            "model": "hrrr",
            "run": "latest",
            "variable": "tmp2m",
            "forecast_hour": 1,
            "points": [
                {"id": "BAD", "lat": 999.0, "lon": -100.5},
            ],
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert "detail" in payload
    assert isinstance(payload["detail"], list)


async def test_sample_batch_rejects_products_without_sampling_support(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_capabilities = main_module.list_model_capabilities

    class _Capability:
        ui_constraints = {"supports_sampling": False}

    def fake_list_model_capabilities() -> dict[str, object]:
        capabilities = dict(original_capabilities())
        capabilities["hrrr"] = _Capability()
        return capabilities

    def fail_resolve_val_cog(*args: object, **kwargs: object) -> Path:
        raise AssertionError("sample batch should fast-fail before resolving raster assets")

    monkeypatch.setattr(main_module, "list_model_capabilities", fake_list_model_capabilities)
    monkeypatch.setattr(main_module, "_resolve_val_cog", fail_resolve_val_cog)

    response = await client.post(
        "/api/v4/sample/batch",
        json={
            "model": "hrrr",
            "run": "latest",
            "variable": "tmp2m",
            "forecast_hour": 1,
            "points": [
                {"id": "SD_1", "lat": 45.5, "lon": -100.5},
            ],
        },
    )

    assert response.status_code == 400
    assert response.json() == {"error": "sampling not supported for this product"}


# ── Phase F Step 3: binary-sampling allowlist on the sample endpoints ───────
#
# Same proof shape as the meteogram allowlist test: publish grid binaries
# whose value (5.0) deliberately differs from the COGs (1.3 / 4.0 at the test
# points), flip the CARTOSKY_COG_SAMPLING_MODELS opt-out mid-test WITHOUT clearing the
# sample cache, and require fresh substrate-correct values — a cache key that
# failed to vary by substrate would serve the stale COG payload instead.

BINARY_TEST_VALUE = 5.0


def _publish_binary_frame() -> None:
    from app.services import grid as grid_module

    grid_module.write_grid_frames_for_run_root(
        run_root=main_module.PUBLISHED_ROOT / "hrrr" / "20260306_00z",
        model="hrrr",
        var="tmp2m",
        fh=1,
        values=np.full((3, 3), BINARY_TEST_VALUE, dtype=np.float32),
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        projection="EPSG:4326",
    )


SAMPLE_QUERY = {
    "model": "hrrr",
    "run": "latest",
    "var": "tmp2m",
    "fh": 1,
    "lat": 45.5,
    "lon": -100.5,
}


async def test_sample_binary_allowlist_switches_substrate_and_cache_key(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish_binary_frame()

    # Fixture default (hrrr opted out): COG value, cached under the cog key.
    first = await client.get("/api/v4/sample", params=SAMPLE_QUERY)
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["value"] == 1.3
    assert first_payload["noData"] is False
    assert first_payload["units"] == "K"

    # Clear the opt-out (test-local only): the binary substrate must answer,
    # not the still-cached COG payload.
    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "")
    second = await client.get("/api/v4/sample", params=SAMPLE_QUERY)
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["value"] == BINARY_TEST_VALUE
    assert second_payload["noData"] is False
    assert second_payload["units"] == "K"

    # Back to opted out: the original cog cache key is untouched — verbatim hit.
    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "hrrr")
    third = await client.get("/api/v4/sample", params=SAMPLE_QUERY)
    assert third.status_code == 200
    assert third.json() == first_payload


async def test_sample_batch_binary_allowlist_switches_substrate_and_cache_key(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish_binary_frame()

    body = {
        "model": "hrrr",
        "run": "latest",
        "variable": "tmp2m",
        "forecast_hour": 1,
        "points": [
            {"id": "SD_1", "lat": 45.5, "lon": -100.5},
            {"id": "SD_2", "lat": 44.5, "lon": -100.5},
        ],
    }

    first = await client.post("/api/v4/sample/batch", json=body)
    assert first.status_code == 200
    assert first.json() == {"units": "K", "values": {"SD_1": 1.3, "SD_2": 4.0}}

    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "")
    second = await client.post("/api/v4/sample/batch", json=body)
    assert second.status_code == 200
    assert second.json() == {
        "units": "K",
        "values": {"SD_1": BINARY_TEST_VALUE, "SD_2": BINARY_TEST_VALUE},
    }

    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "hrrr")
    third = await client.post("/api/v4/sample/batch", json=body)
    assert third.status_code == 200
    assert third.json() == first.json()


async def test_sample_binary_missing_frame_404_matches_cog_404(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No binary frames are published in this test. A missing binary frame on
    # an allowlisted model must be indistinguishable from a missing COG on a
    # non-allowlisted one — same status, same body, on both endpoints.
    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "")
    binary_missing = await client.get("/api/v4/sample", params=SAMPLE_QUERY)
    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "hrrr")
    cog_missing = await client.get("/api/v4/sample", params={**SAMPLE_QUERY, "fh": 2})
    assert binary_missing.status_code == cog_missing.status_code == 404
    assert binary_missing.content == cog_missing.content

    batch_body = {
        "model": "hrrr",
        "run": "latest",
        "variable": "tmp2m",
        "forecast_hour": 1,
        "points": [{"id": "SD_1", "lat": 45.5, "lon": -100.5}],
    }
    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "")
    batch_binary_missing = await client.post("/api/v4/sample/batch", json=batch_body)
    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "hrrr")
    batch_cog_missing = await client.post(
        "/api/v4/sample/batch", json={**batch_body, "forecast_hour": 2}
    )
    assert batch_binary_missing.status_code == batch_cog_missing.status_code == 404
    assert batch_binary_missing.content == batch_cog_missing.content


async def test_sample_batch_reuses_cached_payload(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0
    original = main_module._sample_batch_values

    def wrapped(ds: rasterio.DatasetReader, *, points: list[main_module.SampleBatchPointIn]) -> dict[str, float | None]:
        nonlocal call_count
        call_count += 1
        return original(ds, points=points)

    monkeypatch.setattr(main_module, "_sample_batch_values", wrapped)

    payload = {
        "model": "hrrr",
        "run": "latest",
        "variable": "tmp2m",
        "forecast_hour": 1,
        "points": [
            {"id": "SD_1", "lat": 45.5, "lon": -100.5},
            {"id": "SD_2", "lat": 44.5, "lon": -100.5},
        ],
    }

    first = await client.post("/api/v4/sample/batch", json=payload)
    second = await client.post("/api/v4/sample/batch", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert call_count == 1