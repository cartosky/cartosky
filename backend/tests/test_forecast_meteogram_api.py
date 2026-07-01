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


def _publish_variable(
    published_root: Path,
    manifests_root: Path,
    model: str,
    run_id: str,
    var: str,
    units: str,
    *,
    frame_hours: list[int] = FRAME_HOURS,
) -> None:
    # Append an additional variable to an existing run manifest + publish its COGs.
    manifest_path = manifests_root / model / f"{run_id}.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["variables"][var] = {
        "expected_frames": len(frame_hours),
        "available_frames": len(frame_hours),
        "frames": [{"fh": fh} for fh in frame_hours],
    }
    manifest_path.write_text(json.dumps(manifest))

    var_dir = published_root / model / run_id / var
    for fh in frame_hours:
        _write_value_raster(var_dir / f"fh{fh:03d}.val.cog.tif")
        (var_dir / f"fh{fh:03d}.json").write_text(
            json.dumps({"units": units, "valid_time": f"2026-03-06T{fh:02d}:00:00Z"})
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


async def test_meteogram_honors_complete_pinned_run(client: httpx.AsyncClient) -> None:
    # A newer 12z run is complete, so latest_per_model would pick it; pinning the
    # older (still complete) 00z run must override that.
    _publish_tmp2m(
        main_module.PUBLISHED_ROOT,
        main_module.MANIFESTS_ROOT,
        "gfs",
        "20260306_12z",
        frame_hours=FRAME_HOURS,
        set_latest=True,
    )
    _reset_main_caches()

    body = _body(["gfs"], ["tmp2m"])
    body["pinned_runs"] = {"gfs": "20260306_00z"}
    response = await client.post("/api/v4/forecast/meteogram", json=body)
    assert response.status_code == 200
    gfs = response.json()["series"]["gfs"]
    assert gfs["run_id"] == "20260306_00z"
    assert gfs["status"] == "ok"


async def test_meteogram_pinned_incomplete_run_falls_back(client: httpx.AsyncClient) -> None:
    # Pinning a still-building run is not honored; the latest complete run is used.
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

    body = _body(["gfs"], ["tmp2m"])
    body["pinned_runs"] = {"gfs": "20260306_12z"}
    response = await client.post("/api/v4/forecast/meteogram", json=body)
    assert response.status_code == 200
    gfs = response.json()["series"]["gfs"]
    assert gfs["run_id"] == "20260306_00z"
    assert gfs["status"] == "ok"


async def test_meteogram_pinned_unknown_run_falls_back(client: httpx.AsyncClient) -> None:
    # An unknown/nonexistent pinned run id falls back to the latest complete run.
    body = _body(["gfs"], ["tmp2m"])
    body["pinned_runs"] = {"gfs": "20991231_18z"}
    response = await client.post("/api/v4/forecast/meteogram", json=body)
    assert response.status_code == 200
    gfs = response.json()["series"]["gfs"]
    assert gfs["run_id"] == "20260306_00z"
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


async def test_meteogram_multi_variable_returns_all_three(client: httpx.AsyncClient) -> None:
    # Phase 1B: the Models tab requests tmp2m + precip_total + wspd10m together.
    _publish_variable(
        main_module.PUBLISHED_ROOT,
        main_module.MANIFESTS_ROOT,
        "gfs",
        "20260306_00z",
        "precip_total",
        "in",
    )
    _publish_variable(
        main_module.PUBLISHED_ROOT,
        main_module.MANIFESTS_ROOT,
        "gfs",
        "20260306_00z",
        "wspd10m",
        "mph",
    )
    _reset_main_caches()

    response = await client.post(
        "/api/v4/forecast/meteogram",
        json=_body(["gfs"], ["tmp2m", "precip_total", "wspd10m"]),
    )
    assert response.status_code == 200
    gfs = response.json()["series"]["gfs"]
    assert gfs["status"] == "ok"
    assert gfs["variables"]["tmp2m"]["units"] == "F"
    assert gfs["variables"]["precip_total"]["units"] == "in"
    assert gfs["variables"]["wspd10m"]["units"] == "mph"
    for var in ("tmp2m", "precip_total", "wspd10m"):
        points = gfs["variables"][var]["points"]
        assert points and all(p["value"] == TEST_VALUE for p in points)


async def test_meteogram_prefers_manifest_valid_time_and_units(client: httpx.AsyncClient) -> None:
    # Production manifests carry per-frame valid_time + the variable's units, so
    # the meteogram sources both from the manifest (one read) and skips per-frame
    # sidecar reads. Inject sentinel manifest values that differ from the sidecars
    # and assert the manifest values win.
    manifest_path = main_module.MANIFESTS_ROOT / "gfs" / "20260306_00z.json"
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["variables"]["tmp2m"]
    entry["units"] = "ZZ"
    entry["frames"] = [{"fh": fh, "valid_time": "2099-01-01T00:00:00Z"} for fh in FRAME_HOURS]
    manifest_path.write_text(json.dumps(manifest))
    _reset_main_caches()

    response = await client.post("/api/v4/forecast/meteogram", json=_body(["gfs"], ["tmp2m"]))
    assert response.status_code == 200
    tmp2m = response.json()["series"]["gfs"]["variables"]["tmp2m"]
    assert tmp2m["units"] == "ZZ"
    assert all(p["valid_time"] == "2099-01-01T00:00:00Z" for p in tmp2m["points"])


async def test_model_guidance_v4_returns_410(client: httpx.AsyncClient) -> None:
    # Retired after Phase 1B; clients must use POST /api/v4/forecast/meteogram.
    response = await client.get("/api/v4/model-guidance?lat=45.5&lon=-100.5")
    assert response.status_code == 410
    assert response.json()["error"] == "gone"


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


async def test_meteogram_binary_allowlist_switches_substrate_and_cache_key(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Phase F Step 2: CARTOSKY_BINARY_SAMPLING_MODELS routes allowlisted models
    # to the grid-binary sampler. Publish gfs tmp2m binaries whose value (5.0)
    # differs from the COGs (1.3) so the substrate actually serving the payload
    # is observable, then prove: empty allowlist -> COG values; allowlist=gfs ->
    # binary values for gfs only, WITHOUT clearing the meteogram cache in
    # between — if the cache key did not vary by substrate, the second request
    # would return the stale COG payload cached by the first.
    from app.services import grid as grid_module

    run_root = main_module.PUBLISHED_ROOT / "gfs" / "20260306_00z"
    for fh in FRAME_HOURS:
        grid_module.write_grid_frames_for_run_root(
            run_root=run_root,
            model="gfs",
            var="tmp2m",
            fh=fh,
            values=np.full((3, 3), 5.0, dtype=np.float32),
            transform=from_origin(-101.0, 46.0, 1.0, 1.0),
            projection="EPSG:4326",
        )

    body = _body(["gfs", "ecmwf"], ["tmp2m"])

    # Default (empty allowlist): both models sample COGs; payload gets cached.
    first = await client.post("/api/v4/forecast/meteogram", json=body)
    assert first.status_code == 200
    first_payload = first.json()
    for model in ("gfs", "ecmwf"):
        assert first_payload["series"][model]["status"] == "ok"
        points = first_payload["series"][model]["variables"]["tmp2m"]["points"]
        assert all(p["value"] == TEST_VALUE for p in points)

    # Allowlist gfs (test-local only): gfs flips to the binary substrate, ecmwf
    # in the same request stays on the COG path.
    monkeypatch.setenv("CARTOSKY_BINARY_SAMPLING_MODELS", "gfs")
    second = await client.post("/api/v4/forecast/meteogram", json=body)
    assert second.status_code == 200
    second_payload = second.json()
    gfs = second_payload["series"]["gfs"]
    assert gfs["status"] == "ok"
    assert [p["fh"] for p in gfs["variables"]["tmp2m"]["points"]] == FRAME_HOURS
    assert all(p["value"] == 5.0 for p in gfs["variables"]["tmp2m"]["points"])
    ecmwf_points = second_payload["series"]["ecmwf"]["variables"]["tmp2m"]["points"]
    assert all(p["value"] == TEST_VALUE for p in ecmwf_points)

    # Back to empty: the original "cog" cache key must be unchanged by all of
    # the above — this request is a cache hit on the first payload, verbatim.
    monkeypatch.delenv("CARTOSKY_BINARY_SAMPLING_MODELS")
    third = await client.post("/api/v4/forecast/meteogram", json=body)
    assert third.status_code == 200
    assert third.json() == first_payload


# ── Phase E: COG vs grid-binary meteogram batch-loop comparison ─────────────
#
# Compares the meteogram's COG sampling loop against the allowlist-gated
# binary counterpart `forecast_page._sample_variable_series_binary` across a
# full run for one variable from each canary tolerance group. The fixture is
# synthesized (this test file has no real published runs), but the geometry is
# deliberate: the sampled point is Bridgeport, CT — the location that produced
# real Group 2 divergences in the canary runs — placed 5% into a coarse cell in
# the grid's registration-drift zone, so the binary path's display-prep
# behaviors (3x bilinear upscale, zero-support clamping, categorical
# nearest-neighbor registration drift) genuinely fire when a field edge abuts
# the sampled cell, exactly like the display-prep boundaries seen in the
# canary window.

BINCMP_MODEL = "gfs"
BINCMP_RUN = "20260306_00z"
BINCMP_LAT = 40.6501  # Bridgeport, CT (canary Group 2 divergence anchor)
BINCMP_LON = -73.5966
BINCMP_CELL = 0.25  # GFS-like grid spacing, degrees
BINCMP_W, BINCMP_H = 20, 16
# Sampled coarse cell + the point's fractional position inside it. col 17 of 20
# puts the fine (3x) grid in the zoom registration-drift zone; 0.05 leans the
# point against the cell's left edge so the binary path reads the col-16 side.
BINCMP_COL, BINCMP_ROW = 17, 7
BINCMP_ORIGIN_LON = BINCMP_LON - (BINCMP_COL + 0.05) * BINCMP_CELL
BINCMP_ORIGIN_LAT = BINCMP_LAT + (BINCMP_ROW + 0.5) * BINCMP_CELL

BINCMP_FRAME_HOURS = list(range(0, 385, 6))

# Forecast hours where each variable's field edge is placed at the sampled
# column (divergence expected); everywhere else the edge is far away and the
# sampled neighborhood is flat (exact match expected).
BINCMP_EDGE_FHS = {
    "precip_total": {180, 360},
    "ptype_intensity_rain": {90, 270},
    "ptype_intensity": {60, 240},
}
# COG published but binary frame deliberately not: one real missing-frame
# asymmetry for the counter to catch.
BINCMP_SKIP_BINARY = {("precip_total", 384)}

BINCMP_UNITS = {
    "tmp2m": "F",
    "precip_total": "in",
    "ptype_intensity_rain": "in/hr",
    "ptype_intensity": "index",
}


def _bincmp_field(var: str, fh: int) -> np.ndarray:
    """Deterministic synthetic field for (var, fh) on the comparison grid.

    Values sit on each variable's packing lattice away from 1-dp rounding
    midpoints, so in locally-flat neighborhoods the COG and binary paths must
    agree exactly and any divergence is attributable to display-prep
    boundaries, not quantization noise.
    """
    rows, cols = np.mgrid[0:BINCMP_H, 0:BINCMP_W]
    step = fh // 6
    edge_here = fh in BINCMP_EDGE_FHS.get(var, set())
    if var == "tmp2m":
        return np.round(30.0 + 0.5 * rows + 0.9 * cols + 0.05 * fh, 1).astype(np.float32)
    if var == "precip_total":
        value = [0.62, 0.38, 0.81, 0.24, 1.13][step % 5]
        edge_col = BINCMP_COL if edge_here else 3
        return np.where(cols >= edge_col, value, 0.0).astype(np.float32)
    if var == "ptype_intensity_rain":
        value = [0.22, 0.38, 0.14, 0.31][step % 4]
        edge_col = BINCMP_COL if edge_here else 3
        return np.where(cols >= edge_col, value, 0.0).astype(np.float32)
    if var == "ptype_intensity":
        rain_index = float([2, 4, 7, 3][step % 4])
        field = np.full((BINCMP_H, BINCMP_W), rain_index, dtype=np.float32)
        if edge_here:
            # Adjacent rain bin from the sampled column rightward: the binary
            # path's drift reads the col-16 side -> same-type bin swap.
            field[:, BINCMP_COL:] = rain_index + 1.0
        # Snow block far from the sampled cell: a type boundary exists in the
        # frame, but never inside the sampled neighborhood, so any type
        # crossing at the point would be a real bug.
        field[:, :3] = float(16 + step % 6)
        return field
    raise AssertionError(f"unexpected comparison var: {var}")


def _bincmp_valid_time(fh: int) -> str:
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 3, 6, 0, 0, tzinfo=timezone.utc)
    return (start + timedelta(hours=fh)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def bincmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import grid as grid_module

    data_root = tmp_path / "data" / "v3"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"

    manifest_vars: dict[str, dict] = {}
    run_root = published_root / BINCMP_MODEL / BINCMP_RUN
    for var in BINCMP_UNITS:
        manifest_vars[var] = {
            "units": BINCMP_UNITS[var],
            "expected_frames": len(BINCMP_FRAME_HOURS),
            "available_frames": len(BINCMP_FRAME_HOURS),
            "frames": [
                {"fh": fh, "valid_time": _bincmp_valid_time(fh)} for fh in BINCMP_FRAME_HOURS
            ],
        }
        for fh in BINCMP_FRAME_HOURS:
            cog_path = run_root / var / f"fh{fh:03d}.val.cog.tif"
            cog_path.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(
                cog_path,
                "w",
                driver="GTiff",
                height=BINCMP_H,
                width=BINCMP_W,
                count=1,
                dtype="float32",
                crs="EPSG:4326",
                transform=from_origin(BINCMP_ORIGIN_LON, BINCMP_ORIGIN_LAT, BINCMP_CELL, BINCMP_CELL),
            ) as ds:
                ds.write(_bincmp_field(var, fh), 1)
            if (var, fh) in BINCMP_SKIP_BINARY:
                continue
            grid_module.write_grid_frame_from_value_cog_for_run_root(
                run_root=run_root,
                model=BINCMP_MODEL,
                var=var,
                fh=fh,
                value_cog_path=cog_path,
            )

    manifest_dir = manifests_root / BINCMP_MODEL
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{BINCMP_RUN}.json").write_text(json.dumps({"variables": manifest_vars}))
    (published_root / BINCMP_MODEL / "LATEST.json").write_text(
        json.dumps({"run_id": BINCMP_RUN})
    )

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    _reset_main_caches()
    yield
    _reset_main_caches()


def _bincmp_cog_series(var: str) -> dict:
    """COG twin of `_sample_variable_series_binary`: the same per-variable loop
    `get_forecast_meteogram` runs inline (manifest frames -> `sample_value` per
    fh -> absent frames omitted), so the diff isolates the sampler swap.
    """
    from app.services import sampling

    frames, _units = sampling.manifest_frame_entries(BINCMP_MODEL, BINCMP_RUN, var)
    points = []
    for fh, valid_time in frames:
        present, value = sampling.sample_value(
            BINCMP_MODEL, BINCMP_RUN, var, fh, lat=BINCMP_LAT, lon=BINCMP_LON
        )
        if not present:
            continue
        points.append({"fh": fh, "valid_time": valid_time, "value": value})
    points.sort(key=lambda item: item["fh"])
    return {"points": points}


def _bincmp_neighborhood(var: str, fh: int) -> np.ndarray:
    field = _bincmp_field(var, fh)
    r0, r1 = max(0, BINCMP_ROW - 1), min(BINCMP_H, BINCMP_ROW + 2)
    c0, c1 = max(0, BINCMP_COL - 1), min(BINCMP_W, BINCMP_COL + 2)
    return field[r0:r1, c0:c1]


def _bincmp_scale(var: str) -> float:
    from app.services.grid import _PACKING_BY_MODEL_VAR

    return float(_PACKING_BY_MODEL_VAR[(BINCMP_MODEL, var)]["scale"])


def _bincmp_ptype_type(index: int) -> str | None:
    from app.services.colormaps import GFS_PTYPE_INTENSITY_BREAKS

    for ptype, span in GFS_PTYPE_INTENSITY_BREAKS.items():
        offset = int(span["offset"])
        if offset <= index < offset + int(span["count"]):
            return ptype
    return None


async def test_meteogram_binary_loop_matches_cog_loop(bincmp_env: None) -> None:
    from app.services import forecast_page as forecast_page_service

    report_lines: list[str] = []
    diverged_by_var: dict[str, set[int]] = {}

    for var in BINCMP_UNITS:
        cog = _bincmp_cog_series(var)
        binary = forecast_page_service._sample_variable_series_binary(
            BINCMP_MODEL, BINCMP_RUN, var, lat=BINCMP_LAT, lon=BINCMP_LON
        )
        cog_by_fh = {p["fh"]: p["value"] for p in cog["points"]}
        bin_by_fh = {p["fh"]: p["value"] for p in (binary["points"] or [])}

        # Missing/None asymmetry between the two paths: counted and reported,
        # never silently ignored. Low bound (<=2) matches the canary-observed
        # no-value-delta frequency; the fixture's one deliberate gap
        # (precip_total fh384 has no binary frame) must land here.
        missing_fhs = sorted(set(cog_by_fh) ^ set(bin_by_fh))
        common_fhs = sorted(set(cog_by_fh) & set(bin_by_fh))
        none_asym_fhs = sorted(
            fh for fh in common_fhs if (cog_by_fh[fh] is None) != (bin_by_fh[fh] is None)
        )
        report_lines.append(
            f"{var}: frames cog={len(cog_by_fh)} binary={len(bin_by_fh)} "
            f"present-asymmetry={missing_fhs} none-asymmetry={none_asym_fhs}"
        )
        assert len(missing_fhs) + len(none_asym_fhs) <= 2, report_lines[-1]
        expected_cog_frames = len(BINCMP_FRAME_HOURS)
        assert len(cog_by_fh) == expected_cog_frames

        scale = _bincmp_scale(var)
        valued_fhs = [
            fh
            for fh in common_fhs
            if cog_by_fh[fh] is not None and bin_by_fh[fh] is not None
        ]
        diverged = [
            fh for fh in valued_fhs if abs(cog_by_fh[fh] - bin_by_fh[fh]) > scale / 2.0
        ]
        diverged_by_var[var] = set(diverged)
        for fh in diverged:
            nbhd = _bincmp_neighborhood(var, fh)
            report_lines.append(
                f"  {var} fh{fh:03d}: cog={cog_by_fh[fh]} binary={bin_by_fh[fh]} "
                f"neighborhood=[{np.nanmin(nbhd):.3f}, {np.nanmax(nbhd):.3f}]"
            )

        if var == "tmp2m":
            # Group 1: exact match within scale/2, zero exceptions.
            assert not diverged, "\n".join(report_lines)
        elif var in ("precip_total", "ptype_intensity_rain"):
            # Group 2: >=95% exact within scale/2; every divergence must be
            # boundary-explainable — the binary value must lie inside the COG
            # neighborhood's value range (what bilinear mixing / zero-support
            # clamping at a display-prep boundary can produce), never outside
            # it. A real decode/registration regression lands outside.
            assert valued_fhs, report_lines[-1]
            match_ratio = 1.0 - len(diverged) / len(valued_fhs)
            assert match_ratio >= 0.95, "\n".join(report_lines)
            for fh in diverged:
                nbhd = _bincmp_neighborhood(var, fh)
                low = float(np.nanmin(nbhd)) - scale / 2.0
                high = float(np.nanmax(nbhd)) + scale / 2.0
                assert low <= bin_by_fh[fh] <= high, "\n".join(report_lines)
        else:
            # Group 3 (ptype_intensity): categories must match exactly, or the
            # mismatch must be an adjacent-cell swap that stays within the same
            # physical type per GFS_PTYPE_INTENSITY_BREAKS — a rain->snow
            # crossing is a real bug regardless of index distance.
            for fh in valued_fhs:
                cog_idx = int(round(cog_by_fh[fh]))
                bin_idx = int(round(bin_by_fh[fh]))
                if cog_idx == bin_idx:
                    continue
                cog_type = _bincmp_ptype_type(cog_idx)
                bin_type = _bincmp_ptype_type(bin_idx)
                context = "\n".join(
                    report_lines
                    + [f"  ptype fh{fh:03d}: cog index {cog_idx} ({cog_type}) vs binary {bin_idx} ({bin_type})"]
                )
                assert cog_type is not None and cog_type == bin_type, context
                nbhd_indices = {int(round(v)) for v in _bincmp_neighborhood(var, fh).ravel()}
                assert bin_idx in nbhd_indices, context

    # Prove the comparison exercised real display-prep boundaries: the fixture
    # plants field edges at the sampled cell for known hours, and at least one
    # planted hour per variable must actually diverge — otherwise the
    # boundary-tolerance assertions above are vacuously comparing flat fields.
    for var, planted in BINCMP_EDGE_FHS.items():
        assert planted & diverged_by_var[var], (
            f"{var}: no divergence at planted boundary hours {sorted(planted)}\n"
            + "\n".join(report_lines)
        )

    print("\n".join(report_lines))
