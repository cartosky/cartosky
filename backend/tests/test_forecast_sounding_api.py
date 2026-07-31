"""POST /api/v4/forecast/sounding tests (Skew-T design 2026-07-30, Phase 2).

The parity gate (design §7 Phase 2) is asserted at unit level: every served
value must equal ``sounding.read_profile``'s decode of the same pixel exactly.
Fixtures write real stacks with the Phase 1 writer, so the packing/layout math
is exercised end to end rather than mocked.
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
from pyproj import CRS
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
from app.services import sounding_indices  # noqa: E402

pytestmark = pytest.mark.anyio

MODEL = "hrrr"
RUN_ID = "20260730_12z"
OLDER_RUN_ID = "20260730_11z"
RUN_DATE = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
FHS = [0, 1, 2]

# A miniature HRRR-like grid: Lambert conformal, 12 km cells (the shipped
# decimated resolution), 20 cols x 16 rows, north-up.
CELL_M = 12000.0
WIDTH = 20
HEIGHT = 16
ORIGIN_X = -120000.0
ORIGIN_Y = 96000.0
TRANSFORM = Affine(CELL_M, 0.0, ORIGIN_X, 0.0, -CELL_M, ORIGIN_Y)
PROJECTION = CRS.from_proj4(
    "+proj=lcc +lat_0=38.5 +lon_0=-97.5 +lat_1=38.5 +lat_2=38.5 "
    "+x_0=0 +y_0=0 +R=6371229 +units=m +no_defs"
).to_wkt()

# One pixel is deliberately all-nodata so the null path is covered.
NODATA_ROW, NODATA_COL = 3, 4


def _stack_codes(fh: int) -> np.ndarray:
    """Deterministic, distinct-per-(row, col, plane) uint16 codes."""
    stack = np.zeros((HEIGHT, WIDTH, sounding_service.N_PLANES), dtype=np.uint16)
    planes = np.arange(sounding_service.N_PLANES, dtype=np.int64)
    for row in range(HEIGHT):
        for col in range(WIDTH):
            stack[row, col, :] = (
                (row * 1301 + col * 37 + int(fh) * 7919 + planes * 11) % 60000
            ).astype(np.uint16)
    stack[NODATA_ROW, NODATA_COL, :] = sounding_service.NODATA_CODE
    return stack


def _write_run(
    published_root: Path,
    manifests_root: Path,
    run_id: str,
    *,
    fhs: list[int] = FHS,
    with_sounding_section: bool = True,
) -> None:
    run_root = published_root / MODEL / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    for fh in fhs:
        sidecar = sounding_service.build_sidecar(
            model=MODEL,
            run_id=run_id,
            fh=fh,
            run_date=RUN_DATE,
            width=WIDTH,
            height=HEIGHT,
            transform=TRANSFORM,
            projection=PROJECTION,
            source_width=WIDTH * sounding_service.DECIMATION_STRIDE,
            source_height=HEIGHT * sounding_service.DECIMATION_STRIDE,
        )
        sounding_service.write_stack(
            run_root=run_root, fh=fh, stack=_stack_codes(fh), sidecar=sidecar
        )

    manifest: dict = {"variables": {"tmp2m": {"expected_frames": len(fhs)}}}
    if with_sounding_section:
        manifest["sounding"] = sounding_service.manifest_section(
            run_root=run_root, expected_fhs=fhs
        )
    manifest_dir = manifests_root / MODEL
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(json.dumps(manifest))


def _write_decoy_tree(published_root: Path, manifests_root: Path, name: str = "evil") -> Path:
    """A complete, readable sounding run planted OUTSIDE the model root.

    ``run="../evil"`` resolves to ``{manifests}/evil.json`` + ``{published}/evil``
    — i.e. a sibling of the ``hrrr/`` model root. Its codes are all 60123, which
    ``_stack_codes`` (mod 60000) can never produce, so any decoy value that
    reaches the response is unmistakable.
    """
    run_root = published_root / name
    run_root.mkdir(parents=True, exist_ok=True)
    for fh in FHS:
        sidecar = sounding_service.build_sidecar(
            model=MODEL,
            run_id=RUN_ID,
            fh=fh,
            run_date=RUN_DATE,
            width=WIDTH,
            height=HEIGHT,
            transform=TRANSFORM,
            projection=PROJECTION,
            source_width=WIDTH * sounding_service.DECIMATION_STRIDE,
            source_height=HEIGHT * sounding_service.DECIMATION_STRIDE,
        )
        stack = np.full((HEIGHT, WIDTH, sounding_service.N_PLANES), 60123, dtype=np.uint16)
        sounding_service.write_stack(run_root=run_root, fh=fh, stack=stack, sidecar=sidecar)

    manifest = {
        "variables": {"tmp2m": {"expected_frames": len(FHS)}},
        "sounding": sounding_service.manifest_section(run_root=run_root, expected_fhs=FHS),
    }
    (manifests_root / f"{name}.json").write_text(json.dumps(manifest))
    return run_root


def _reset_caches() -> None:
    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    sounding_api_service._sounding_transformer.cache_clear()


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    data_root = tmp_path / "data" / "v3"
    manifests_root = data_root / "manifests"
    published_root = data_root / "published"
    manifests_root.mkdir(parents=True, exist_ok=True)
    published_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", MODEL)
    _reset_caches()
    yield published_root, manifests_root
    _reset_caches()


@pytest.fixture(autouse=True)
def _reset_sounding_rate_window():
    """The Phase 3 limiter is a module-level sliding window shared by every test."""
    main_module._sounding_rate_window.clear()
    yield
    main_module._sounding_rate_window.clear()


@pytest.fixture
async def client(roots: tuple[Path, Path]) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


def _cell_center_lonlat(row: int, col: int) -> tuple[float, float]:
    x, y = TRANSFORM * (col + 0.5, row + 0.5)
    lon, lat = sounding_api_service._sounding_transformer(PROJECTION).transform(
        x, y, direction="INVERSE"
    )
    return float(lon), float(lat)


def _body(lat: float, lon: float, **extra) -> dict:
    return {"model": MODEL, "lat": lat, "lon": lon, **extra}


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


async def test_flag_off_is_404(client: httpx.AsyncClient, roots, monkeypatch) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", "")
    lon, lat = _cell_center_lonlat(8, 10)

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.status_code == 404
    assert "not available" in response.json()["error"]


async def test_flagged_but_unsupported_model_is_404(
    client: httpx.AsyncClient, roots, monkeypatch
) -> None:
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", "gfs")
    assert "gfs" not in sounding_service.SUPPORTED_MODELS

    response = await client.post(
        "/api/v4/forecast/sounding", json={"model": "gfs", "lat": 38.5, "lon": -97.5}
    )
    assert response.status_code == 404


async def test_no_eligible_run_is_404(client: httpx.AsyncClient, roots) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID, with_sounding_section=False)
    lon, lat = _cell_center_lonlat(8, 10)

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Happy path + parity gate
# ---------------------------------------------------------------------------


async def test_happy_path_matches_read_profile_exactly(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    row, col = 6, 9
    lon, lat = _cell_center_lonlat(row, col)

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=300"
    payload = response.json()

    assert payload["model"] == MODEL
    assert payload["run"] == RUN_ID
    assert "requested_run" not in payload
    assert payload["location"] == {"lat": lat, "lon": lon}
    assert payload["levels_hPa"] == list(sounding_service.LEVELS_HPA)
    assert payload["units"]["t"] == "degC"
    assert payload["units"]["u"] == "m s-1"
    assert payload["units"]["w"] == "Pa s-1"
    assert payload["units"]["pres_sfc"] == "hPa"
    assert payload["grid_point"]["row"] == row
    assert payload["grid_point"]["col"] == col
    assert [frame["fh"] for frame in payload["frames"]] == FHS

    run_root = published_root / MODEL / RUN_ID
    for frame in payload["frames"]:
        fh = frame["fh"]
        stack_path, _ = sounding_service.stack_paths(run_root, fh)
        sidecar = sounding_service.read_sidecar(run_root, fh)
        expected = sounding_service.read_profile(stack_path, sidecar, row=row, col=col)
        for var_id in ("t", "td", "u", "v", "w"):
            assert frame[var_id] == expected[var_id]
            assert len(frame[var_id]) == 37
        assert frame["surface"] == expected["surface"]
        assert frame["valid_time"] == sidecar["valid_time"]


async def test_nodata_pixel_serves_nulls(client: httpx.AsyncClient, roots) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    lon, lat = _cell_center_lonlat(NODATA_ROW, NODATA_COL)

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.status_code == 200
    payload = response.json()
    assert payload["grid_point"]["row"] == NODATA_ROW
    assert payload["grid_point"]["col"] == NODATA_COL
    for frame in payload["frames"]:
        assert frame["t"] == [None] * 37
        assert frame["w"] == [None] * 37
        assert all(value is None for value in frame["surface"].values())


async def test_missing_stack_file_skips_only_that_fh(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    stack_path, _ = sounding_service.stack_paths(published_root / MODEL / RUN_ID, 1)
    stack_path.unlink()
    lon, lat = _cell_center_lonlat(6, 9)

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.status_code == 200
    assert [frame["fh"] for frame in response.json()["frames"]] == [0, 2]


# ---------------------------------------------------------------------------
# Row convention (design §5 warning: row 0 is NORTH)
# ---------------------------------------------------------------------------


async def test_northern_point_maps_to_smaller_row(client: httpx.AsyncClient, roots) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    north_lon, north_lat = _cell_center_lonlat(2, 10)
    south_lon, south_lat = _cell_center_lonlat(12, 10)
    assert north_lat > south_lat

    north = await client.post("/api/v4/forecast/sounding", json=_body(north_lat, north_lon))
    south = await client.post("/api/v4/forecast/sounding", json=_body(south_lat, south_lon))
    north_point = north.json()["grid_point"]
    south_point = south.json()["grid_point"]

    assert north_point["row"] == 2
    assert south_point["row"] == 12
    assert north_point["row"] < south_point["row"]

    # And the served column really is the planted one for that row: a flipped
    # row convention would silently return a different (valid-looking) profile.
    run_root = published_root / MODEL / RUN_ID
    stack_path, _ = sounding_service.stack_paths(run_root, 0)
    sidecar = sounding_service.read_sidecar(run_root, 0)
    expected = sounding_service.read_profile(stack_path, sidecar, row=2, col=10)
    assert north.json()["frames"][0]["t"] == expected["t"]
    assert north.json()["frames"][0]["t"] != south.json()["frames"][0]["t"]


# ---------------------------------------------------------------------------
# Run resolution
# ---------------------------------------------------------------------------


async def test_pinned_run_is_served_when_eligible(client: httpx.AsyncClient, roots) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, OLDER_RUN_ID)
    _write_run(published_root, manifests_root, RUN_ID)
    lon, lat = _cell_center_lonlat(6, 9)

    response = await client.post(
        "/api/v4/forecast/sounding", json=_body(lat, lon, run=OLDER_RUN_ID)
    )
    payload = response.json()
    assert payload["run"] == OLDER_RUN_ID
    assert payload["requested_run"] == OLDER_RUN_ID


async def test_ineligible_pin_falls_back_to_latest_eligible(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    _write_run(published_root, manifests_root, "20260730_13z", with_sounding_section=False)
    lon, lat = _cell_center_lonlat(6, 9)

    response = await client.post(
        "/api/v4/forecast/sounding", json=_body(lat, lon, run="20260730_13z")
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["run"] == RUN_ID
    assert payload["requested_run"] == "20260730_13z"


async def test_unknown_pin_falls_back_to_latest_eligible(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    lon, lat = _cell_center_lonlat(6, 9)

    response = await client.post(
        "/api/v4/forecast/sounding", json=_body(lat, lon, run="20260101_00z")
    )
    assert response.status_code == 200
    assert response.json()["run"] == RUN_ID


# ---------------------------------------------------------------------------
# Run pin is a path segment: traversal guard (RUN_ID_RE, as main._resolve_run)
# ---------------------------------------------------------------------------

# Every one of these would escape `{published}/hrrr/` or `{manifests}/hrrr/`
# if the pin reached the filesystem unvalidated.
HOSTILE_PINS = [
    "../evil",
    "../../etc/passwd",
    "/etc/passwd",
    "20260730_12z/../../evil",
    "hrrr/../../evil",
    "..",
    "./evil",
    r"..\evil",
]


async def test_traversal_pin_serves_latest_run_not_the_decoy(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    decoy_root = _write_decoy_tree(published_root, manifests_root)
    row, col = 6, 9
    lon, lat = _cell_center_lonlat(row, col)

    # Sanity: the decoy really is readable and really is outside the model root,
    # so a pass here means the guard held — not that the exploit was unbuildable.
    assert decoy_root.resolve() == (published_root / MODEL / RUN_ID / ".." / ".." / "evil").resolve()
    decoy_stack, _ = sounding_service.stack_paths(decoy_root, 0)
    decoy_profile = sounding_service.read_profile(
        decoy_stack, sounding_service.read_sidecar(decoy_root, 0), row=row, col=col
    )
    assert decoy_profile["t"][0] is not None

    response = await client.post(
        "/api/v4/forecast/sounding", json=_body(lat, lon, run="../evil")
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"] == RUN_ID
    assert payload["requested_run"] == "../evil"

    run_root = published_root / MODEL / RUN_ID
    for frame in payload["frames"]:
        fh = frame["fh"]
        stack_path, _ = sounding_service.stack_paths(run_root, fh)
        expected = sounding_service.read_profile(
            stack_path, sounding_service.read_sidecar(run_root, fh), row=row, col=col
        )
        for var_id in ("t", "td", "u", "v", "w"):
            assert frame[var_id] == expected[var_id]
            assert frame[var_id] != decoy_profile[var_id]
        assert frame["surface"] == expected["surface"]
        assert frame["surface"] != decoy_profile["surface"]


async def test_hostile_pin_never_reaches_the_filesystem(
    client: httpx.AsyncClient, roots, monkeypatch
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    _write_decoy_tree(published_root, manifests_root)
    lon, lat = _cell_center_lonlat(6, 9)

    loaded_runs: list[str] = []
    real_load_manifest = main_module._load_manifest

    def _recording_load_manifest(model: str, run: str, **kwargs):
        loaded_runs.append(run)
        return real_load_manifest(model, run, **kwargs)

    monkeypatch.setattr(main_module, "_load_manifest", _recording_load_manifest)

    for pin in HOSTILE_PINS:
        loaded_runs.clear()
        response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon, run=pin))

        assert response.status_code == 200, pin
        payload = response.json()
        # Fell back silently, echoing the request — the unknown-pin contract.
        assert payload["run"] == RUN_ID, pin
        assert payload["requested_run"] == pin
        # The pin was rejected BEFORE any manifest/filesystem use: only
        # scanner-produced run ids were ever looked up.
        assert pin not in loaded_runs, pin
        assert loaded_runs == [RUN_ID], pin


async def test_valid_format_pin_that_does_not_exist_still_falls_back(
    client: httpx.AsyncClient, roots, monkeypatch
) -> None:
    """The guard must reject only malformed ids, not well-formed unknown ones."""
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    lon, lat = _cell_center_lonlat(6, 9)

    loaded_runs: list[str] = []
    real_load_manifest = main_module._load_manifest

    def _recording_load_manifest(model: str, run: str, **kwargs):
        loaded_runs.append(run)
        return real_load_manifest(model, run, **kwargs)

    monkeypatch.setattr(main_module, "_load_manifest", _recording_load_manifest)

    response = await client.post(
        "/api/v4/forecast/sounding", json=_body(lat, lon, run="20260101_00z")
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"] == RUN_ID
    assert payload["requested_run"] == "20260101_00z"
    # Well-formed pins are still tried against the manifest store first.
    assert loaded_runs == ["20260101_00z", RUN_ID]


# ---------------------------------------------------------------------------
# Snap geometry
# ---------------------------------------------------------------------------


async def test_out_of_domain_point_is_400(client: httpx.AsyncClient, roots) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)

    response = await client.post("/api/v4/forecast/sounding", json=_body(10.0, -60.0))
    assert response.status_code == 400
    assert "outside the sounding grid" in response.json()["error"]


async def test_unprojectable_point_is_400_not_500(
    client: httpx.AsyncClient, roots
) -> None:
    # The south pole projects to inf/NaN under HRRR's LCC; must be a clean 400.
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)

    response = await client.post("/api/v4/forecast/sounding", json=_body(-90.0, 0.0))
    assert response.status_code == 400


async def test_snap_distance_is_zero_at_a_cell_center(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    lon, lat = _cell_center_lonlat(8, 10)

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.json()["grid_point"]["distance_km"] == pytest.approx(0.0, abs=0.01)


async def test_snap_distance_bounded_by_half_cell_diagonal(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    row, col = 8, 10
    # 0.49 of a cell in both axes: still inside the same cell, close enough to
    # the corner to pin the worst case (12 km grid -> ~8.5 km half-diagonal).
    x, y = TRANSFORM * (col + 0.99, row + 0.99)
    lon, lat = sounding_api_service._sounding_transformer(PROJECTION).transform(
        x, y, direction="INVERSE"
    )

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    payload = response.json()
    assert payload["grid_point"]["row"] == row
    assert payload["grid_point"]["col"] == col
    assert 8.0 < payload["grid_point"]["distance_km"] <= 8.5


# ---------------------------------------------------------------------------
# Consistency guard
# ---------------------------------------------------------------------------


async def test_level_ladder_mismatch_between_fhs_is_500(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    run_root = published_root / MODEL / RUN_ID
    _stack_path, sidecar_path = sounding_service.stack_paths(run_root, 2)
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["levels_hPa"] = [level - 5 for level in sidecar["levels_hPa"]]
    sidecar_path.write_text(json.dumps(sidecar))
    lon, lat = _cell_center_lonlat(6, 9)

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.status_code == 500
    assert "disagree" in response.json()["error"]


# ---------------------------------------------------------------------------
# Rate limiting (design §7 Phase 3 — deferred from Phase 2)
# ---------------------------------------------------------------------------


async def test_sounding_rate_limit_returns_429(
    client: httpx.AsyncClient, roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    lon, lat = _cell_center_lonlat(6, 9)

    monkeypatch.setattr(main_module, "SOUNDING_RATE_LIMIT_MAX_REQUESTS", 1)
    main_module._sounding_rate_window.clear()

    first = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    second = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"] == "rate limit exceeded"
    assert int(second.headers["retry-after"]) >= 1
    # Its own bucket: the meteogram window is untouched by sounding traffic.
    assert main_module._meteogram_rate_window == {}


# ---------------------------------------------------------------------------
# Phase 4 — indices, parcel path, format_version 1 vs 2
# ---------------------------------------------------------------------------


def _physical_column() -> tuple[dict[int, float], dict[str, float]]:
    """A convectively UNSTABLE warm-sector column, as {plane: value} + surface.

    Every pixel of the run gets the same column, so the indices assertions do
    not depend on which cell the test picks.

    The environmental lapse below 200 hPa is ~8 K/km — deliberately steeper
    than the moist adiabat, so the SB parcel has real positive area and the
    ``sbcape > 0`` assertions test convection rather than a rounding knife-edge
    (the original ~6.1 K/km column computed SBCAPE = 0.496 J/kg, which rounds
    to 0 — caught by verification 2026-07-31).
    """
    values: dict[int, float] = {}
    for level_index, level in enumerate(sounding_service.LEVELS_HPA):
        # ~8 K/km below 200 hPa via a crude pressure proxy, sharply stable above.
        if level >= 200:
            temperature = 30.0 - (1000.0 - level) * 0.075
        else:
            temperature = -56.0 + (200.0 - level) * 0.05
        dewpoint = temperature - (3.0 + (1000.0 - level) * 0.022)
        values[sounding_service.isobaric_plane_index(level_index, 0)] = temperature
        values[sounding_service.isobaric_plane_index(level_index, 1)] = dewpoint
        values[sounding_service.isobaric_plane_index(level_index, 2)] = 5.0 + level_index * 0.4
        values[sounding_service.isobaric_plane_index(level_index, 3)] = -3.0 + level_index * 0.2
        values[sounding_service.isobaric_plane_index(level_index, 4)] = -0.1
    surface = {"pres_sfc": 968.5, "t2m": 32.0, "td2m": 22.0, "u10m": 3.0, "v10m": -2.0}
    values[sounding_service.surface_plane_index(0)] = surface["pres_sfc"]
    values[sounding_service.surface_plane_index(1)] = surface["t2m"]
    values[sounding_service.surface_plane_index(2)] = surface["td2m"]
    values[sounding_service.surface_plane_index(3)] = surface["u10m"]
    values[sounding_service.surface_plane_index(4)] = surface["v10m"]
    return values, surface


#: HRRR's own surface CAPE, planted in the v2 stacks below.
MODEL_CAPE = 1512.0


def _write_physical_run(
    published_root: Path,
    manifests_root: Path,
    *,
    run_id: str = RUN_ID,
    format_version: int = 2,
    fhs: list[int] | None = None,
) -> Path:
    """Write a run of physically sane stacks in v1 or v2 layout."""
    fhs = list(FHS if fhs is None else fhs)
    values, _surface = _physical_column()
    n_surface = 6 if format_version >= 2 else 5
    if format_version >= 2:
        values[sounding_service.surface_plane_index(5)] = MODEL_CAPE
    n_planes = sounding_service.N_ISOBARIC_PLANES + n_surface

    run_root = published_root / MODEL / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    for fh in fhs:
        codes = np.zeros((HEIGHT, WIDTH, n_planes), dtype=np.uint16)
        for plane in range(n_planes):
            pack = sounding_service.pack_for_plane(plane)
            codes[:, :, plane] = sounding_service.encode_plane(
                np.full((HEIGHT, WIDTH), values[plane], dtype=np.float32), pack
            )
        sidecar = sounding_service.build_sidecar(
            model=MODEL,
            run_id=run_id,
            fh=fh,
            run_date=RUN_DATE,
            width=WIDTH,
            height=HEIGHT,
            transform=TRANSFORM,
            projection=PROJECTION,
            source_width=WIDTH * sounding_service.DECIMATION_STRIDE,
            source_height=HEIGHT * sounding_service.DECIMATION_STRIDE,
        )
        if format_version < 2:
            # Hand-rolled v1: the writer only emits the current layout.
            sidecar["format_version"] = 1
            sidecar["surface_fields"] = sidecar["surface_fields"][:5]
            sidecar["n_planes"] = n_planes
            sidecar["bytes_per_pixel"] = n_planes * sounding_service.BYTES_PER_SAMPLE
            stack_path, sidecar_path = sounding_service.stack_paths(run_root, fh)
            stack_path.parent.mkdir(parents=True, exist_ok=True)
            stack_path.write_bytes(
                np.ascontiguousarray(codes).astype(sounding_service.STACK_DTYPE).tobytes()
            )
            sidecar_path.write_text(json.dumps(sidecar))
        else:
            sounding_service.write_stack(
                run_root=run_root, fh=fh, stack=codes, sidecar=sidecar
            )

    manifest = {
        "variables": {"tmp2m": {"expected_frames": len(fhs)}},
        "sounding": sounding_service.manifest_section(run_root=run_root, expected_fhs=fhs),
    }
    manifest_dir = manifests_root / MODEL
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(json.dumps(manifest))
    return run_root


async def test_every_frame_carries_indices_and_a_parcel_path(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_physical_run(published_root, manifests_root)
    lon, lat = _cell_center_lonlat(6, 9)

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.status_code == 200
    payload = response.json()

    assert payload["parcel_definition"] == sounding_indices.PARCEL_DEFINITION
    assert "virtual" in payload["parcel_definition"].lower()

    expected_keys = {
        "sbcape",
        "sbcin",
        "mlcape",
        "mlcin",
        "lcl_hPa",
        "lcl_C",
        "lfc_hPa",
        "el_hPa",
        "pwat_mm",
        "model_sbcape",
    }
    for frame in payload["frames"]:
        indices = frame["indices"]
        assert indices is not None
        assert set(indices) == expected_keys
        assert isinstance(indices["sbcape"], int)
        assert indices["sbcape"] > 0
        assert indices["pwat_mm"] > 0
        assert indices["lcl_hPa"] is not None and indices["lcl_hPa"] < 968.5

        parcel = frame["parcel"]
        assert parcel is not None
        assert len(parcel["p"]) == len(parcel["t"]) >= 30
        assert parcel["p"][0] == pytest.approx(968.5, abs=0.1)
        assert all(
            parcel["p"][i] >= parcel["p"][i + 1] for i in range(len(parcel["p"]) - 1)
        )


async def test_v2_stack_reports_the_model_sbcape_diagnostic(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_physical_run(published_root, manifests_root, format_version=2)
    lon, lat = _cell_center_lonlat(6, 9)

    payload = (
        await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    ).json()
    frame = payload["frames"][0]
    assert frame["surface"]["cape_sfc"] == pytest.approx(MODEL_CAPE, abs=0.1)
    assert frame["indices"]["model_sbcape"] == int(round(MODEL_CAPE))
    # It is a side-by-side diagnostic, never our parcel (decision #5).
    assert frame["indices"]["sbcape"] != frame["indices"]["model_sbcape"]
    assert payload["units"]["cape_sfc"] == "J kg-1"


async def test_v1_stack_still_serves_with_a_null_model_sbcape(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_physical_run(published_root, manifests_root, format_version=1)
    lon, lat = _cell_center_lonlat(6, 9)

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.status_code == 200
    payload = response.json()
    assert "cape_sfc" not in payload["units"]
    for frame in payload["frames"]:
        assert "cape_sfc" not in frame["surface"]
        assert frame["indices"] is not None
        assert frame["indices"]["model_sbcape"] is None
        # Everything else is unchanged by the format bump.
        assert frame["indices"]["sbcape"] > 0
        assert frame["parcel"] is not None


async def test_mixed_v1_and_v2_stacks_in_one_run_still_serve(
    client: httpx.AsyncClient, roots
) -> None:
    """A scheduler restart mid-run can leave both formats side by side.

    The fh series must not 500 over a surface field the isobaric ladder does not
    depend on.
    """
    published_root, manifests_root = roots
    _write_physical_run(published_root, manifests_root, format_version=1)
    # Overwrite the last fh with a v2 stack, leaving the earlier ones at v1.
    run_root = published_root / MODEL / RUN_ID
    values, _surface = _physical_column()
    values[sounding_service.surface_plane_index(5)] = MODEL_CAPE
    codes = np.zeros((HEIGHT, WIDTH, sounding_service.N_PLANES), dtype=np.uint16)
    for plane in range(sounding_service.N_PLANES):
        pack = sounding_service.pack_for_plane(plane)
        codes[:, :, plane] = sounding_service.encode_plane(
            np.full((HEIGHT, WIDTH), values[plane], dtype=np.float32), pack
        )
    sidecar = sounding_service.build_sidecar(
        model=MODEL,
        run_id=RUN_ID,
        fh=FHS[-1],
        run_date=RUN_DATE,
        width=WIDTH,
        height=HEIGHT,
        transform=TRANSFORM,
        projection=PROJECTION,
        source_width=WIDTH * sounding_service.DECIMATION_STRIDE,
        source_height=HEIGHT * sounding_service.DECIMATION_STRIDE,
    )
    sounding_service.write_stack(run_root=run_root, fh=FHS[-1], stack=codes, sidecar=sidecar)
    lon, lat = _cell_center_lonlat(6, 9)

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.status_code == 200
    frames = response.json()["frames"]
    assert [frame["fh"] for frame in frames] == FHS
    assert frames[0]["indices"]["model_sbcape"] is None
    assert frames[-1]["indices"]["model_sbcape"] == int(round(MODEL_CAPE))


async def test_one_frames_index_failure_does_not_poison_the_response(
    client: httpx.AsyncClient, roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    published_root, manifests_root = roots
    _write_physical_run(published_root, manifests_root)
    real = sounding_indices.compute_frame_indices

    def _explode_on_one(**kwargs):
        surface = kwargs.get("surface") or {}
        if surface.get("_boom"):
            raise RuntimeError("MetPy blew up")
        return real(**kwargs)

    monkeypatch.setattr(sounding_indices, "compute_frame_indices", _explode_on_one)

    real_frame_for_fh = sounding_api_service._frame_for_fh

    def _mark_second_frame(**kwargs):
        frame = real_frame_for_fh(**kwargs)
        if frame is not None and frame["fh"] == FHS[1]:
            frame["surface"]["_boom"] = True
        return frame

    monkeypatch.setattr(sounding_api_service, "_frame_for_fh", _mark_second_frame)

    lon, lat = _cell_center_lonlat(6, 9)
    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.status_code == 200
    frames = {frame["fh"]: frame for frame in response.json()["frames"]}
    assert frames[FHS[1]]["indices"] is None
    assert frames[FHS[1]]["parcel"] is None
    assert frames[FHS[0]]["indices"] is not None
    assert frames[FHS[2]]["indices"] is not None


async def test_nodata_pixel_yields_null_indices_not_an_error(
    client: httpx.AsyncClient, roots
) -> None:
    published_root, manifests_root = roots
    _write_run(published_root, manifests_root, RUN_ID)
    lon, lat = _cell_center_lonlat(NODATA_ROW, NODATA_COL)

    response = await client.post("/api/v4/forecast/sounding", json=_body(lat, lon))
    assert response.status_code == 200
    for frame in response.json()["frames"]:
        assert frame["indices"] is None
        assert frame["parcel"] is None
