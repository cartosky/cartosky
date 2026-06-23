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

# Point that falls inside the synthetic raster (origin -101, 46; 1deg cells).
TEST_LAT = 45.5
TEST_LON = -100.5
TEST_VALUE = 1.3  # top-left pixel value 1.34 rounded to 1 dp

# tmp2m frames published per model in the fixture.
FRAME_HOURS = [0, 3]


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

    with main_module._meteogram_lock:
        main_module._meteogram_rate_window.clear()

    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    main_module._sample_transformer.cache_clear()
    main_module.forecast_page_service._meteogram_cache.clear()


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


def _publish_tmp2m(
    published_root: Path,
    manifests_root: Path,
    model: str,
    run_id: str,
    *,
    frame_hours: list[int] = FRAME_HOURS,
    expected_frames: int | None = None,
    set_latest: bool = True,
) -> None:
    # `available_frames` reflects published frames; `expected_frames` is the run
    # target. A run is "complete" for tmp2m when available >= expected.
    expected = expected_frames if expected_frames is not None else len(frame_hours)
    manifest_dir = manifests_root / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "variables": {
                    "tmp2m": {
                        "expected_frames": expected,
                        "available_frames": len(frame_hours),
                        "frames": [{"fh": fh} for fh in frame_hours],
                    }
                }
            }
        )
    )

    model_root = published_root / model
    model_root.mkdir(parents=True, exist_ok=True)
    if set_latest:
        (model_root / "LATEST.json").write_text(json.dumps({"run_id": run_id}))

    var_dir = model_root / run_id / "tmp2m"
    for fh in frame_hours:
        _write_value_raster(var_dir / f"fh{fh:03d}.val.cog.tif")
        (var_dir / f"fh{fh:03d}.json").write_text(
            json.dumps({"units": "F", "valid_time": f"2026-03-06T{fh:02d}:00:00Z"})
        )


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    data_root = tmp_path / "data" / "v3"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"

    run_id = "20260306_00z"
    _publish_tmp2m(published_root, manifests_root, "gfs", run_id)
    _publish_tmp2m(published_root, manifests_root, "ecmwf", run_id)

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)

    _reset_main_caches()

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    _reset_main_caches()


def _body(models: list[str], variables: list[str]) -> dict:
    return {
        "lat": TEST_LAT,
        "lon": TEST_LON,
        "models": models,
        "variables": variables,
        "run_policy": {"type": "latest_per_model"},
    }


async def test_meteogram_multi_model_temperature_shape(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v4/forecast/meteogram",
        json=_body(["gfs", "ecmwf"], ["tmp2m"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert sorted(payload["series"].keys()) == ["ecmwf", "gfs"]
    assert payload["run_policy"] == {"type": "latest_per_model"}

    for model in ("gfs", "ecmwf"):
        entry = payload["series"][model]
        assert entry["status"] == "ok"
        assert entry["run_id"] == "20260306_00z"
        tmp2m = entry["variables"]["tmp2m"]
        assert tmp2m["units"] == "F"
        assert [p["fh"] for p in tmp2m["points"]] == FRAME_HOURS
        assert all(p["value"] == TEST_VALUE for p in tmp2m["points"])
        assert tmp2m["points"][0]["valid_time"] == "2026-03-06T00:00:00Z"


async def test_meteogram_cache_control_header(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v4/forecast/meteogram",
        json=_body(["gfs"], ["tmp2m"]),
    )
    assert response.status_code == 200
    # Responses vary by per-model entitlement -> private, never shared at the CDN.
    assert response.headers["Cache-Control"] == "private, max-age=300"


async def test_meteogram_unavailable_model_does_not_500(client: httpx.AsyncClient) -> None:
    # `nam` has no published artifacts -> unavailable, not a 500 / not omitted.
    response = await client.post(
        "/api/v4/forecast/meteogram",
        json=_body(["gfs", "ecmwf", "nam"], ["tmp2m"]),
    )
    assert response.status_code == 200
    payload = response.json()
    assert sorted(payload["series"].keys()) == ["ecmwf", "gfs", "nam"]
    assert payload["series"]["nam"]["status"] == "unavailable"
    assert payload["series"]["gfs"]["status"] == "ok"


async def test_meteogram_partial_when_variable_missing(client: httpx.AsyncClient) -> None:
    # `wspd10m` is not published for gfs in the fixture -> null points, status partial, no 500.
    response = await client.post(
        "/api/v4/forecast/meteogram",
        json=_body(["gfs"], ["tmp2m", "wspd10m"]),
    )
    assert response.status_code == 200
    payload = response.json()
    gfs = payload["series"]["gfs"]
    assert gfs["status"] == "partial"
    assert gfs["variables"]["tmp2m"]["points"]
    assert gfs["variables"]["wspd10m"]["points"] is None
    assert gfs["variables"]["wspd10m"]["error"] == "artifact_not_found"


async def test_meteogram_unknown_model_does_not_500(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v4/forecast/meteogram",
        json=_body(["foobar"], ["tmp2m"]),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["series"]["foobar"]["status"] == "unavailable"


async def test_meteogram_rate_limit_returns_429(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "METEOGRAM_RATE_LIMIT_MAX_REQUESTS", 1)
    main_module._meteogram_rate_window.clear()

    first = await client.post("/api/v4/forecast/meteogram", json=_body(["gfs"], ["tmp2m"]))
    second = await client.post("/api/v4/forecast/meteogram", json=_body(["gfs"], ["tmp2m"]))

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"] == "rate limit exceeded"


async def test_meteogram_skips_incomplete_latest_run(client: httpx.AsyncClient) -> None:
    # gfs already has a complete 00z run from the fixture. Publish a newer 12z run
    # that is still building (2 of 10 frames) and point LATEST at it.
    _publish_tmp2m(
        main_module.PUBLISHED_ROOT,
        main_module.MANIFESTS_ROOT,
        "gfs",
        "20260306_12z",
        frame_hours=[0, 3],
        expected_frames=10,
        set_latest=True,
    )
    _reset_main_caches()

    response = await client.post(
        "/api/v4/forecast/meteogram",
        json=_body(["gfs"], ["tmp2m"]),
    )
    assert response.status_code == 200
    gfs = response.json()["series"]["gfs"]
    # The building 12z run is skipped; the previous complete 00z run is used.
    assert gfs["run_id"] == "20260306_00z"
    assert gfs["status"] == "ok"


async def test_meteogram_uses_latest_complete_run(client: httpx.AsyncClient) -> None:
    # Publish a newer 12z run that is itself complete.
    _publish_tmp2m(
        main_module.PUBLISHED_ROOT,
        main_module.MANIFESTS_ROOT,
        "gfs",
        "20260306_12z",
        frame_hours=FRAME_HOURS,
        set_latest=True,
    )
    _reset_main_caches()

    response = await client.post(
        "/api/v4/forecast/meteogram",
        json=_body(["gfs"], ["tmp2m"]),
    )
    assert response.status_code == 200
    gfs = response.json()["series"]["gfs"]
    assert gfs["run_id"] == "20260306_12z"
    assert gfs["status"] == "ok"


async def test_meteogram_no_complete_run_is_unavailable(client: httpx.AsyncClient) -> None:
    # nam has only a building run (2 of 10 frames) -> no complete run -> unavailable.
    _publish_tmp2m(
        main_module.PUBLISHED_ROOT,
        main_module.MANIFESTS_ROOT,
        "nam",
        "20260306_00z",
        frame_hours=[0, 3],
        expected_frames=10,
        set_latest=True,
    )
    _reset_main_caches()

    response = await client.post(
        "/api/v4/forecast/meteogram",
        json=_body(["nam"], ["tmp2m"]),
    )
    assert response.status_code == 200
    assert response.json()["series"]["nam"]["status"] == "unavailable"


async def test_meteogram_invalid_body_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v4/forecast/meteogram",
        json={
            "lat": 999.0,
            "lon": TEST_LON,
            "models": ["gfs"],
            "variables": ["tmp2m"],
        },
    )
    assert response.status_code == 422
