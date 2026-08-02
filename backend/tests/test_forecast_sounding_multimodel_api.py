"""POST /api/v4/forecast/sounding for GFS + ECMWF (Skew-T design §10, Phase 6).

Companion to ``test_forecast_sounding_api.py``, which owns HRRR. The point of
this module is that nothing in the serving layer is HRRR-shaped: a 21-level GFS
run and a 12-level ECMWF run go through the same endpoint on a global 0.5 deg
lat/lon grid, and the response states its own ladder, units and CAPE label.

Stacks are written with the real writer at the real published geometry
(720 x 361 would be 38 MB per fh, so the grids here are the same *affine* over a
small window — the wrap arithmetic is what matters and it is affine-driven).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pytest
from rasterio.transform import Affine

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

from app import main as main_module  # noqa: E402
from app.services import sounding as sounding_service  # noqa: E402
from app.services import sounding_api as sounding_api_service  # noqa: E402
from app.services import sounding_thermo  # noqa: E402

pytestmark = pytest.mark.anyio

RUN_ID = "20260802_06z"
RUN_DATE = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)
FHS = [0, 3, 6]

# The real published global geometry (0.5 deg after the x2 decimation), but only
# the first 24 x 16 cells are materialised: the affine is what the coordinate
# path reads, and writing 720 x 361 x 111 planes per fh would be 38 MB a frame.
GLOBAL_TRANSFORM = Affine(0.5, 0.0, -0.125, 0.0, -0.5, 90.125)
WIDTH = 24
HEIGHT = 16
PROJECTION = "EPSG:4326"


def _physical_column(spec: sounding_service.SoundingModelSpec) -> dict[int, float]:
    """A single unstable profile, replicated across the little grid."""
    values: dict[int, float] = {}
    n_levels = len(spec.levels_hpa)
    for index, level in enumerate(spec.levels_hpa):
        fraction = index / max(1, n_levels - 1)
        t = 30.0 - 105.0 * fraction
        values[spec.isobaric_plane_index(index, 0)] = t
        values[spec.isobaric_plane_index(index, 1)] = t - 4.0 - 12.0 * fraction
        values[spec.isobaric_plane_index(index, 2)] = 3.0 + 25.0 * fraction
        values[spec.isobaric_plane_index(index, 3)] = -4.0 + 8.0 * fraction
        values[spec.isobaric_plane_index(index, 4)] = -0.4 + 0.8 * fraction
    for field_index, value in enumerate([1004.0, 31.0, 24.0, 2.5, -3.5, 1875.0]):
        values[spec.surface_plane_index(field_index)] = value
    return values


def _write_run(
    published_root: Path,
    manifests_root: Path,
    model: str,
    *,
    fhs: list[int] = FHS,
) -> Path:
    spec = sounding_service.model_spec(model)
    run_root = published_root / model / RUN_ID
    run_root.mkdir(parents=True, exist_ok=True)
    column = _physical_column(spec)

    for fh in fhs:
        codes = np.full((HEIGHT, WIDTH, spec.n_planes), sounding_service.NODATA_CODE, np.uint16)
        for plane, value in column.items():
            pack = spec.pack_for_plane(plane)
            codes[:, :, plane] = sounding_service.encode_plane(
                np.full((HEIGHT, WIDTH), value, dtype=np.float32), pack
            )
        sidecar = sounding_service.build_sidecar(
            model=model,
            run_id=RUN_ID,
            fh=fh,
            run_date=RUN_DATE,
            width=WIDTH,
            height=HEIGHT,
            transform=GLOBAL_TRANSFORM,
            projection=PROJECTION,
            source_width=WIDTH * spec.stride,
            source_height=HEIGHT * spec.stride,
        )
        sounding_service.write_stack(run_root=run_root, fh=fh, stack=codes, sidecar=sidecar)

    manifest = {
        "variables": {"tmp2m": {"expected_frames": len(fhs)}},
        "sounding": sounding_service.manifest_section(run_root=run_root, expected_fhs=fhs),
    }
    manifest_dir = manifests_root / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{RUN_ID}.json").write_text(json.dumps(manifest))
    return run_root


def _reset_caches() -> None:
    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    sounding_api_service._sounding_transformer.cache_clear()
    sounding_api_service._sounding_is_geographic.cache_clear()
    sounding_api_service.clear_response_cache()


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data" / "v3"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"
    manifests_root.mkdir(parents=True, exist_ok=True)
    published_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", "hrrr,gfs,ecmwf")
    _reset_caches()
    yield published_root, manifests_root
    _reset_caches()


@pytest.fixture(autouse=True)
def _serial_thermo(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(sounding_thermo.WORKERS_ENV_VAR, "0")


@pytest.fixture(autouse=True)
def _reset_sounding_rate_window():
    main_module._sounding_rate_window.clear()
    yield
    main_module._sounding_rate_window.clear()


@pytest.fixture
async def client(roots) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# A point inside the materialised window (cols 0-23 = 0 -> 11.5 E, rows 0-15 =
# 90 -> 82.5 N is polar, so the window is deliberately read at its own cells).
IN_WINDOW_LAT = 86.0
IN_WINDOW_LON = 4.0


async def _post(client: httpx.AsyncClient, model: str, **extra) -> httpx.Response:
    return await client.post(
        "/api/v4/forecast/sounding",
        json={"model": model, "lat": IN_WINDOW_LAT, "lon": IN_WINDOW_LON, **extra},
    )


# ---------------------------------------------------------------------------
# Per-model responses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,n_levels,cape_label,parcel_model",
    [
        ("gfs", 21, "GFS SBCAPE", "GFS"),
        ("ecmwf", 12, "MUCAPE", "ECMWF"),
    ],
)
async def test_response_states_its_own_ladder_and_cape_label(
    client: httpx.AsyncClient, roots, model, n_levels, cape_label, parcel_model
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, model)

    response = await _post(client, model)
    assert response.status_code == 200
    payload = response.json()

    assert payload["model"] == model
    assert payload["run"] == RUN_ID
    assert len(payload["levels_hPa"]) == n_levels
    assert payload["levels_hPa"] == list(sounding_service.model_spec(model).levels_hpa)
    assert payload["levels_hPa"] == sorted(payload["levels_hPa"], reverse=True)
    assert payload["model_cape_label"] == cape_label
    assert payload["parcel_definition"].startswith(f"SB parcel: {parcel_model}")
    assert [frame["fh"] for frame in payload["frames"]] == FHS

    for frame in payload["frames"]:
        for key in ("t", "td", "u", "v", "w"):
            assert len(frame[key]) == n_levels, key
        # Omega rides along for BOTH new models: ECMWF open data does publish
        # `w` on all 12 levels (the earlier "not in open data" was wrong).
        assert all(value is not None for value in frame["w"])
        assert frame["surface"]["pres_sfc"] == pytest.approx(1004.0, abs=0.05)
        assert frame["surface"]["cape_sfc"] == pytest.approx(1875.0, abs=0.1)


@pytest.mark.parametrize("model", ["gfs", "ecmwf"])
async def test_served_values_equal_a_direct_decode_of_the_same_pixel(
    client: httpx.AsyncClient, roots, model
) -> None:
    published_root, manifests_root = roots
    run_root = _write_run(published_root, manifests_root, model)

    response = await _post(client, model)
    payload = response.json()
    grid_point = payload["grid_point"]

    stack_path, _ = sounding_service.stack_paths(run_root, FHS[0])
    sidecar = sounding_service.read_sidecar(run_root, FHS[0])
    expected = sounding_service.read_profile(
        stack_path, sidecar, row=grid_point["row"], col=grid_point["col"]
    )
    frame = payload["frames"][0]
    for key in ("t", "td", "u", "v", "w"):
        assert frame[key] == expected[key]
    assert frame["surface"] == expected["surface"]


@pytest.mark.parametrize("model", ["gfs", "ecmwf"])
async def test_indices_are_computed_on_the_coarse_ladders(
    client: httpx.AsyncClient, roots, model
) -> None:
    pytest.importorskip("metpy.calc")
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, model)

    payload = (await _post(client, model)).json()
    for frame in payload["frames"]:
        indices = frame["indices"]
        assert indices is not None
        assert indices["sbcape"] is not None
        assert indices["pwat_mm"] is not None
        # The diagnostic keeps its stable field name across models; only the
        # label above says whether it is surface-based or most-unstable.
        assert indices["model_sbcape"] == 1875
        assert frame["parcel"] is not None
        assert frame["profiles"] is not None
        assert len(frame["profiles"]["tw"]) == len(payload["levels_hPa"])


# ---------------------------------------------------------------------------
# Global grid coordinate handling
# ---------------------------------------------------------------------------


async def test_a_western_hemisphere_longitude_reaches_a_zero_to_360_grid(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, "gfs")

    # Column 525 of the real grid is 262.5 E == 97.5 W, which is outside the
    # materialised window here — so this asserts the failure MODE is a clean
    # out-of-grid 400 rather than a 500, and that a lon inside the window works
    # from either side of Greenwich.
    outside = await client.post(
        "/api/v4/forecast/sounding", json={"model": "gfs", "lat": 38.5, "lon": -97.5}
    )
    assert outside.status_code == 400

    wrapped = await client.post(
        "/api/v4/forecast/sounding", json={"model": "gfs", "lat": IN_WINDOW_LAT, "lon": -0.05}
    )
    assert wrapped.status_code == 200
    assert wrapped.json()["grid_point"]["col"] == 0


async def test_the_snapped_point_is_echoed_in_signed_longitude(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, "ecmwf")

    payload = (await _post(client, "ecmwf")).json()
    grid_point = payload["grid_point"]
    assert -180.0 <= grid_point["lon"] <= 180.0
    assert grid_point["distance_km"] < 60.0
    assert payload["location"] == {"lat": IN_WINDOW_LAT, "lon": IN_WINDOW_LON}


# ---------------------------------------------------------------------------
# Gating stays per model
# ---------------------------------------------------------------------------


async def test_the_flag_gates_each_model_independently(
    client: httpx.AsyncClient, roots, monkeypatch
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, "gfs")
    _write_run(published_root, manifests_root, "ecmwf")
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", "gfs")

    assert (await _post(client, "gfs")).status_code == 200
    assert (await _post(client, "ecmwf")).status_code == 404


async def test_capabilities_expose_the_same_gate_the_endpoint_enforces(
    client: httpx.AsyncClient, roots, monkeypatch
) -> None:
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", "hrrr,gfs")
    main_module._capabilities_catalog_signature.cache_clear()

    for model_id in ("hrrr", "gfs", "ecmwf", "nam"):
        payload = main_module._serialize_model_capability(model_id, None)
        assert payload["soundings"] is sounding_api_service.sounding_enabled(model_id)

    assert main_module._serialize_model_capability("gfs", None)["soundings"] is True
    assert main_module._serialize_model_capability("ecmwf", None)["soundings"] is False
    main_module._capabilities_catalog_signature.cache_clear()


async def test_two_models_are_served_side_by_side_without_cross_contamination(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, "gfs")
    _write_run(published_root, manifests_root, "ecmwf")

    gfs = (await _post(client, "gfs")).json()
    ecmwf = (await _post(client, "ecmwf")).json()

    # Same run id, same grid cell, same in-process cache: the payloads must not
    # collapse into one another.
    assert len(gfs["levels_hPa"]) == 21
    assert len(ecmwf["levels_hPa"]) == 12
    assert gfs["model_cape_label"] != ecmwf["model_cape_label"]
    assert len(gfs["frames"][0]["t"]) == 21
    assert len(ecmwf["frames"][0]["t"]) == 12
