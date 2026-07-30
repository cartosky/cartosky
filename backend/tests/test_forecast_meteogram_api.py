import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import numpy as np
import pytest
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
from app.services import sampling as sampling_module  # noqa: E402
from app.services import grid as grid_module  # noqa: E402

pytestmark = pytest.mark.anyio

# Point that falls inside the synthetic raster (origin -101, 46; 1deg cells).
TEST_LAT = 45.5
TEST_LON = -100.5
TEST_VALUE = 1.3  # top-left pixel value 1.34 rounded to 1 dp

# tmp2m frames published per model in the fixture.
FRAME_HOURS = [0, 3]


def _reset_main_caches() -> None:
    with main_module._sample_lock:
        main_module._sample_cache.clear()
        main_module._sample_inflight.clear()
        main_module._sample_rate_window.clear()

    with main_module._meteogram_lock:
        main_module._meteogram_rate_window.clear()

    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    sampling_module._sample_transformer.cache_clear()
    main_module.forecast_page_service._meteogram_cache.clear()


def _write_value_raster(path: Path) -> None:
    """Write the grid binary frames a published run would contain.

    ``path`` keeps the historical ``<published>/<model>/<run>/<var>/fhNNN...``
    shape so model/var/fh stay derivable from a single argument. The COG
    fixture's -9999.0 nodata sentinel becomes NaN, which is what the binary
    packing treats as nodata.
    """
    var = path.parent.name
    run_root = path.parent.parent
    model = run_root.parent.name
    fh = int(path.name.split(".")[0].removeprefix("fh"))
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.array(
        [
            [1.34, 2.21, 3.09],
            [4.04, np.nan, np.nan],
            [7.77, 8.88, 9.99],
        ],
        dtype=np.float32,
    )
    grid_module.write_grid_frames_for_run_root(
        run_root=run_root,
        model=model,
        var=var,
        fh=fh,
        values=data,
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        projection="EPSG:4326",
    )


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

    # Artifacts live under the runtime variable id (e.g. gefs tmp2m ->
    # tmp2m__mean); the manifest keeps the canonical id. Same mapping the
    # sampling resolver applies.
    runtime_var = main_module._runtime_var_id_for_request(model, "tmp2m", None)
    var_dir = model_root / run_id / runtime_var
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

    runtime_var = main_module._runtime_var_id_for_request(model, var, None)
    var_dir = published_root / model / run_id / runtime_var
    for fh in frame_hours:
        # Probability vars stay sidecar-only: the tests that publish them only
        # assert their points are None on a run without artifacts, and writing
        # real frames would trigger lazy probability-packing registration in
        # grid._PACKING_BY_MODEL_VAR — a module-global whose audited partition
        # test_grid_value_decode.py pins.
        if "__prob_" not in var:
            _write_value_raster(var_dir / f"fh{fh:03d}.val.cog.tif")
        var_dir.mkdir(parents=True, exist_ok=True)
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


def test_pinned_probability_validation_anchors_to_base_variable() -> None:
    helper = getattr(
        main_module.forecast_page_service,
        "_pinned_run_validation_variables",
        None,
    )
    assert helper is not None
    probability_vars = [
        "precip_total__prob_gt_0p1",
        "precip_total__prob_gt_0p25",
        "precip_total__prob_gt_0p5",
        "precip_total__prob_gt_1p0",
        "precip_total__prob_gt_1p5",
        "precip_total__prob_gt_2p0",
    ]
    assert helper(probability_vars) == [*probability_vars, "precip_total"]
    assert helper(["tmp2m"]) == ["tmp2m"]


async def test_pinned_probability_request_stays_on_base_complete_run(
    client: httpx.AsyncClient,
) -> None:
    older_run = "20260306_00z"
    newer_run = "20260306_06z"
    base_var = "precip_total"
    probability_vars = [
        "precip_total__prob_gt_0p1",
        "precip_total__prob_gt_0p25",
        "precip_total__prob_gt_0p5",
        "precip_total__prob_gt_1p0",
        "precip_total__prob_gt_1p5",
        "precip_total__prob_gt_2p0",
    ]
    _publish_tmp2m(
        main_module.PUBLISHED_ROOT,
        main_module.MANIFESTS_ROOT,
        "gefs",
        older_run,
        set_latest=False,
    )
    _publish_variable(
        main_module.PUBLISHED_ROOT,
        main_module.MANIFESTS_ROOT,
        "gefs",
        older_run,
        base_var,
        "in",
    )
    for probability_var in probability_vars:
        _publish_variable(
            main_module.PUBLISHED_ROOT,
            main_module.MANIFESTS_ROOT,
            "gefs",
            older_run,
            probability_var,
            "%",
        )
    _publish_tmp2m(
        main_module.PUBLISHED_ROOT,
        main_module.MANIFESTS_ROOT,
        "gefs",
        newer_run,
        set_latest=True,
    )
    _publish_variable(
        main_module.PUBLISHED_ROOT,
        main_module.MANIFESTS_ROOT,
        "gefs",
        newer_run,
        base_var,
        "in",
    )
    _reset_main_caches()

    body = _body(["gefs"], probability_vars)
    body["pinned_runs"] = {"gefs": newer_run}
    response = await client.post("/api/v4/forecast/meteogram", json=body)

    assert response.status_code == 200
    gefs = response.json()["series"]["gefs"]
    assert gefs["run_id"] == newer_run
    assert gefs["status"] == "partial"
    assert all(gefs["variables"][var]["points"] is None for var in probability_vars)


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
    # precip_total bakes a 3x upscale into the published binary frame
    # (gfs_precip_total_display_v2), so its sampled value comes from the
    # display-prepped grid, not the raw one; tmp2m/wspd10m have no display
    # prep and sample the raw cell (quantized by packing scale).
    expected = {"tmp2m": TEST_VALUE, "precip_total": 1.9, "wspd10m": TEST_VALUE}
    for var in ("tmp2m", "precip_total", "wspd10m"):
        points = gfs["variables"][var]["points"]
        assert points and all(p["value"] == expected[var] for p in points)


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


# ── Member pipeline Phase 5: include_members payload contract ───────────────


async def test_meteogram_include_members_contract(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include_members=true returns the Model Guidance §7 members block for a
    member-publishing binary model: mean (reused main series) + every roster
    id, with frameless members as points=None; include_members=false omits
    the key; unsupported models 400."""
    from app.services import grid as grid_module  # noqa: E402
    from app.services.grid import write_slim_grid_frame_for_run_root

    run_id = "20260306_00z"
    _publish_tmp2m(main_module.PUBLISHED_ROOT, main_module.MANIFESTS_ROOT, "gefs", run_id)
    _publish_variable(
        main_module.PUBLISHED_ROOT,
        main_module.MANIFESTS_ROOT,
        "gefs",
        run_id,
        "tmp850",
        "C",
    )
    run_root = main_module.PUBLISHED_ROOT / "gefs" / run_id
    transform = from_origin(-101.0, 46.0, 1.0, 1.0)
    for fh in FRAME_HOURS:
        grid_module.write_grid_frames_for_run_root(
            run_root=run_root,
            model="gefs",
            var="tmp2m__mean",
            fh=fh,
            values=np.full((3, 3), 5.0, dtype=np.float32),
            transform=transform,
            projection="EPSG:4326",
        )
        grid_module.write_grid_frames_for_run_root(
            run_root=run_root,
            model="gefs",
            var="tmp850__mean",
            fh=fh,
            values=np.full((3, 3), -5.0, dtype=np.float32),
            transform=transform,
            projection="EPSG:4326",
        )
    # Two members published; the other 29 have no frames (points=None).
    for member_var, value in (("tmp2m__m01", 6.0), ("tmp2m__control", 4.0)):
        for fh in FRAME_HOURS:
            write_slim_grid_frame_for_run_root(
                run_root=run_root,
                model="gefs",
                var=member_var,
                fh=fh,
                values=np.full((3, 3), value, dtype=np.float32),
                transform=transform,
                projection="EPSG:4326",
            )
    for member_var, value in (("tmp850__m01", -4.0), ("tmp850__control", -6.0)):
        for fh in FRAME_HOURS:
            write_slim_grid_frame_for_run_root(
                run_root=run_root,
                model="gefs",
                var=member_var,
                fh=fh,
                values=np.full((3, 3), value, dtype=np.float32),
                transform=transform,
                projection="EPSG:4326",
            )


    body = _body(["gefs"], ["tmp2m", "tmp850"])
    body["include_members"] = True
    response = await client.post("/api/v4/forecast/meteogram", json=body)
    assert response.status_code == 200
    var_payload = response.json()["series"]["gefs"]["variables"]["tmp2m"]
    members = var_payload["members"]

    expected_keys = {"mean", "control"} | {f"m{i:02d}" for i in range(1, 31)}
    assert set(members) == expected_keys
    assert members["mean"]["points"] == var_payload["points"]
    assert [p["value"] for p in members["m01"]["points"]] == [6.0] * len(FRAME_HOURS)
    assert [p["fh"] for p in members["m01"]["points"]] == FRAME_HOURS
    assert all(p["valid_time"] for p in members["m01"]["points"])
    assert [p["value"] for p in members["control"]["points"]] == [4.0] * len(FRAME_HOURS)
    assert members["m02"]["points"] is None

    tmp850_payload = response.json()["series"]["gefs"]["variables"]["tmp850"]
    tmp850_members = tmp850_payload["members"]
    assert tmp850_payload["units"] == "C"
    assert tmp850_members["mean"]["points"] == tmp850_payload["points"]
    assert [p["value"] for p in tmp850_members["m01"]["points"]] == [-4.0] * len(FRAME_HOURS)
    assert [p["value"] for p in tmp850_members["control"]["points"]] == [-6.0] * len(FRAME_HOURS)
    assert tmp850_members["m02"]["points"] is None

    # include_members omitted -> no members key, and a distinct cache entry
    # (the cache key already varies by the flag).
    plain = await client.post("/api/v4/forecast/meteogram", json=_body(["gefs"], ["tmp2m"]))
    assert plain.status_code == 200
    assert "members" not in plain.json()["series"]["gefs"]["variables"]["tmp2m"]

    # A model without member publishing rejects include_members with a 400.
    unsupported = _body(["gfs"], ["tmp2m"])
    unsupported["include_members"] = True
    rejected = await client.post("/api/v4/forecast/meteogram", json=unsupported)
    assert rejected.status_code == 400
    assert "include_members" in rejected.json()["error"]


async def test_meteogram_members_prefers_members_ready_run(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include_members prefers the newest run whose member frames are
    PUBLISHED (member manifest present): a fresh run's member pass lags its
    mean catchup, and a one-cycle-older full fan beats a mean-only plume.
    The plain request keeps serving the newest mean-complete run, and an
    explicit pin always wins."""
    from app.services import grid as grid_module  # noqa: E402
    from app.services.grid import write_slim_grid_frame_for_run_root

    older, newer = "20260307_00z", "20260307_06z"
    transform = from_origin(-101.0, 46.0, 1.0, 1.0)
    for run_id in (older, newer):
        _publish_tmp2m(
            main_module.PUBLISHED_ROOT, main_module.MANIFESTS_ROOT, "gefs", run_id,
            set_latest=run_id == newer,
        )
        run_root = main_module.PUBLISHED_ROOT / "gefs" / run_id
        for fh in FRAME_HOURS:
            grid_module.write_grid_frames_for_run_root(
                run_root=run_root, model="gefs", var="tmp2m__mean", fh=fh,
                values=np.full((3, 3), 5.0, dtype=np.float32),
                transform=transform, projection="EPSG:4326",
            )
    # Member frames + member manifest exist only on the OLDER run — the
    # newer run's member pass "hasn't happened yet".
    older_root = main_module.PUBLISHED_ROOT / "gefs" / older
    for fh in FRAME_HOURS:
        write_slim_grid_frame_for_run_root(
            run_root=older_root, model="gefs", var="tmp2m__m01", fh=fh,
            values=np.full((3, 3), 6.0, dtype=np.float32),
            transform=transform, projection="EPSG:4326",
        )
    grid_module.build_grid_manifests_for_run_root(
        run_root=older_root, model="gefs", run=older, variables=("tmp2m__m01",),
    )


    body = _body(["gefs"], ["tmp2m"])
    body["include_members"] = True
    response = await client.post("/api/v4/forecast/meteogram", json=body)
    assert response.status_code == 200
    series = response.json()["series"]["gefs"]
    assert series["run_id"] == older
    # latest_complete_run always reports the true newest complete run, even
    # when the members-ready preference serves an older one — it is the run
    # selector's ceiling, and inferring it from run_id hides newer runs.
    assert series["latest_complete_run"] == newer
    m01 = series["variables"]["tmp2m"]["members"]["m01"]["points"]
    assert [p["value"] for p in m01] == [6.0] * len(FRAME_HOURS)

    # Without include_members the newest mean-complete run still wins.
    plain = await client.post("/api/v4/forecast/meteogram", json=_body(["gefs"], ["tmp2m"]))
    assert plain.status_code == 200
    plain_series = plain.json()["series"]["gefs"]
    assert plain_series["run_id"] == newer
    assert plain_series["latest_complete_run"] == newer

    # An explicit pin beats the members-ready preference (mean-only is what
    # the user asked to see).
    pinned = _body(["gefs"], ["tmp2m"])
    pinned["include_members"] = True
    pinned["pinned_runs"] = {"gefs": newer}
    response = await client.post("/api/v4/forecast/meteogram", json=pinned)
    assert response.status_code == 200
    pinned_series = response.json()["series"]["gefs"]
    assert pinned_series["run_id"] == newer
    assert pinned_series["variables"]["tmp2m"]["members"]["m01"]["points"] is None

    # A pin to the OLDER run must not mask the true latest (the 2026-07-08
    # prod bug: a stale pin in a shared URL froze the run dropdown's ceiling
    # at the pinned cycle and hid every newer run).
    pinned_old = _body(["gefs"], ["tmp2m"])
    pinned_old["pinned_runs"] = {"gefs": older}
    response = await client.post("/api/v4/forecast/meteogram", json=pinned_old)
    assert response.status_code == 200
    pinned_old_series = response.json()["series"]["gefs"]
    assert pinned_old_series["run_id"] == older
    assert pinned_old_series["latest_complete_run"] == newer


def test_meteogram_cache_key_isolates_domain() -> None:
    """Origin cache keys must differ by domain (Phase 2A sample caches do).

    Sampling is already domain-scoped; omitting domain from the meteogram key
    let a domain=global miss poison the canonical entry for five minutes.
    """
    from app.services import forecast_page as forecast_page_module

    common = dict(
        lat=TEST_LAT,
        lon=TEST_LON,
        models=["gfs"],
        variables=["tmp2m"],
        policy_type="latest_per_model",
        include_members=False,
        run_ids={"gfs": "20260306_00z"},
        entitled={"gfs": True},
    )
    canonical = forecast_page_module._meteogram_cache_key(**common)
    explicit_na = forecast_page_module._meteogram_cache_key(**common, domain="na")
    global_key = forecast_page_module._meteogram_cache_key(**common, domain="global")
    absent = forecast_page_module._meteogram_cache_key(**common, domain=None)

    assert canonical == absent
    assert canonical != global_key
    assert explicit_na != global_key
    assert explicit_na != canonical


async def test_meteogram_domain_miss_does_not_poison_canonical_cache(
    client: httpx.AsyncClient,
) -> None:
    """A domain= request that finds no artifacts must not cache-poison
    the next canonical (no-domain) meteogram for the same point/models."""
    miss_body = _body(["gfs"], ["tmp2m"])
    miss_body["domain"] = "global"
    miss = await client.post("/api/v4/forecast/meteogram", json=miss_body)
    assert miss.status_code == 200
    miss_series = miss.json()["series"]["gfs"]
    assert miss_series["status"] in {"unavailable", "partial"}
    assert miss_series.get("run_id") is None or not any(
        (p or {}).get("value") is not None
        for p in (miss_series.get("variables", {}).get("tmp2m", {}) or {}).get("points") or []
    )

    # Do NOT clear the origin cache — the regression is sharing one entry.
    ok = await client.post("/api/v4/forecast/meteogram", json=_body(["gfs"], ["tmp2m"]))
    assert ok.status_code == 200
    ok_series = ok.json()["series"]["gfs"]
    assert ok_series["status"] == "ok"
    assert ok_series["run_id"] == "20260306_00z"
    points = ok_series["variables"]["tmp2m"]["points"]
    assert any(p["value"] is not None for p in points)
