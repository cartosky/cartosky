"""Phase 3 — G1 antimeridian gates for the AIFS global domain (native 4326).

The AIFS mirror of ``test_aigfs_global_antimeridian.py``. The open AIFS source
is delivered as a 0.25° 0–360° EPSG:4326 global field, so it adopts the
contract — and this suite — verbatim; the clean module-level pieces of the GFS
suite (the analytic field, the synthetic source raster, the GeoJSON walker,
the bump fixture geometry) are imported rather than re-derived, and only the
model-parameterised parts are restated here.

Note this is the *global* domain only: AIFS's canonical grid stays 9 km
EPSG:3857 and no part of this file touches it.

Confirming the *live* AIFS GRIBs are on that grid — every variable of them,
AIFS being an AI model — is prod-gate item G1, not something this file can
assert; what it does assert is that the index-roll branch accepts that
geometry and that a build over it is a pure longitude roll — pinned by
patching ``reproject`` to explode, because value equality is vacuous on an
aligned grid.

Everything runs through the REAL production paths (``warp_to_target_grid``,
the grid binary writer, the ``/api/v4/sample`` route, ``build_iso_contour_geojson``).
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
TESTS_ROOT = Path(__file__).resolve().parent
for _path in (str(BACKEND_ROOT), str(REPO_ROOT), str(TESTS_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "test-client")
os.environ.setdefault("TWF_CLIENT_SECRET", "test-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_antimeridian_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import main as main_module  # noqa: E402
from app.services import sampling as sampling_module  # noqa: E402
from app.services.builder import raster_grid as raster_grid_module  # noqa: E402
from app.services.builder.pipeline import build_iso_contour_geojson  # noqa: E402
from app.services.builder.raster_grid import (  # noqa: E402
    _index_roll_to_target_grid,
    get_grid_crs,
    get_target_grid,
    warp_to_target_grid,
)
from app.services.grid import (  # noqa: E402
    _packing_config,
    build_grid_manifests_for_run_root,
    write_grid_frames_for_run_root,
)
from app.services.grid_display_prep import grid_display_prep_config  # noqa: E402

# Shared, model-independent fixture machinery from the GFS suite. Importing it
# keeps the two suites judged against one oracle: a change to the analytic
# field or the synthetic source cannot drift between models.
from test_gfs_global_antimeridian import (  # noqa: E402
    _BUMP_PEAK,
    _CONTOUR_LEVEL,
    _CONTROL_BUMP_CENTRE,
    _FIELD_BASE,
    _FIELD_LAT_SLOPE,
    _FIELD_LON_AMPLITUDE,
    _SEAM_BUMP_CENTRE,
    LATITUDES,
    SEAM_LONGITUDES,
    SRC_STEP_DEG,
    _analytic,
    _bump,
    _flatten_coordinates,
    _source_raster,
    requires_gdal_cli,
)
from rasterio.transform import from_origin  # noqa: E402

MODEL = "aifs"
CANONICAL = "na"
GLOBAL = "global"
VAR = "tmp2m"
RUN_ID = "20260802_00z"
FLAG = "CARTOSKY_GLOBAL_DOMAIN_MODELS"


# ── tolerance ──────────────────────────────────────────────────────────────


def _gridpoint_tolerance() -> float:
    """Tolerance for a query that lands exactly on a gridpoint.

    Identical derivation to the GFS suite (read the docstring there): uint16
    packing quantisation (≤ scale/2, read from the real packing config) plus
    the API's 1-decimal response rounding. No warp term exists on the native
    grid. Restated rather than imported because it is keyed to this model's
    packing config.
    """
    packing = _packing_config(MODEL, VAR)
    assert packing is not None, f"no packing config for {MODEL}/{VAR}"
    return float(packing["scale"]) / 2.0 + 0.05


def _sampling_tolerance() -> float:
    """Sample-vs-analytic tolerance for arbitrary query points.

    Pixel-centre offset dominates (nearest-pixel sampling, half a cell =
    0.125° in both axes on this geographic grid), plus the gridpoint terms
    above; there is no resampling term because the build is a longitude roll.
    A 15 % margin covers float32 storage and the ignored curvature term. The
    negative-control test below is what keeps it from being vacuous.
    """
    grid = get_target_grid(MODEL, GLOBAL)
    half_cell_deg = grid.resolution / 2.0

    max_df_dlon = _FIELD_LON_AMPLITUDE * math.radians(1.0)  # |cos| ≤ 1
    max_df_dlat = _FIELD_LAT_SLOPE
    pixel_centre_term = half_cell_deg * (max_df_dlon + max_df_dlat)

    return 1.15 * (pixel_centre_term + _gridpoint_tolerance())


TOLERANCE_F = _sampling_tolerance()
GRIDPOINT_TOLERANCE_F = _gridpoint_tolerance()


# ── synthetic source + warp ────────────────────────────────────────────────


_WARP_CACHE: dict[str, tuple[np.ndarray, object]] = {}


def _warped_global_field() -> tuple[np.ndarray, object]:
    """The real build onto the registered native global grid (cached)."""
    if "value" not in _WARP_CACHE:
        values, src_transform = _source_raster()
        _WARP_CACHE["value"] = warp_to_target_grid(
            values,
            "EPSG:4326",
            src_transform,
            model=MODEL,
            region=GLOBAL,
            resampling="bilinear",
        )
    warped, transform = _WARP_CACHE["value"]
    return warped.copy(), transform


# ── domain-scoped published tree ───────────────────────────────────────────


def _clear_caches() -> None:
    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    main_module._grid_manifest_cache.clear()
    sampling_module._FRAME_META_CACHE.clear()


@pytest.fixture
def global_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Phase 3 prod flip: AIFS allowlisted so `domain=global` validates."""
    monkeypatch.setenv(FLAG, MODEL)


@pytest.fixture
def api_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    data_root = tmp_path / "data"
    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", data_root / "published")
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", data_root / "manifests")
    _clear_caches()
    yield data_root
    _clear_caches()


@pytest.fixture
async def client(api_roots: Path) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


def _publish_global_frame(data_root: Path, values: np.ndarray, transform: object) -> Path:
    """Write the warped array into `published/aifs/domains/global/…`.

    Paths are spelled out longhand (matching `test_artifact_domains.py`) so a
    regression in the domain path helpers cannot make this fixture agree with
    itself; the bytes and metadata come from the real writers.
    """
    run_root = data_root / "published" / MODEL / "domains" / GLOBAL / RUN_ID
    var_dir = run_root / VAR
    var_dir.mkdir(parents=True, exist_ok=True)

    write_grid_frames_for_run_root(
        run_root=run_root,
        model=MODEL,
        var=VAR,
        fh=0,
        values=values,
        transform=transform,
        projection=get_grid_crs(MODEL, GLOBAL),
    )
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "F", "valid_time": "2026-08-02T00:00:00Z"})
    )
    build_grid_manifests_for_run_root(
        run_root=run_root, model=MODEL, run=RUN_ID, variables=(VAR,)
    )
    (run_root.parent / "LATEST.json").write_text(json.dumps({"run_id": RUN_ID}))

    manifest_dir = data_root / "manifests" / MODEL / "domains" / GLOBAL
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{RUN_ID}.json").write_text(
        json.dumps(
            {
                "contract_version": "3.0",
                "model": MODEL,
                "run": RUN_ID,
                "region": GLOBAL,
                "variables": {
                    VAR: {
                        "display_name": "Surface Temp",
                        "kind": "continuous",
                        "units": "F",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [{"fh": 0, "valid_time": "2026-08-02T00:00:00Z"}],
                    }
                },
            }
        )
    )
    _clear_caches()
    return run_root


@pytest.fixture
def published_global(api_roots: Path, global_live: None) -> Path:
    warped, transform = _warped_global_field()
    _publish_global_frame(api_roots, warped, transform)
    return api_roots


async def _sample(client: httpx.AsyncClient, *, lon: float, lat: float) -> dict:
    response = await client.get(
        f"/api/v4/sample?model={MODEL}&run={RUN_ID}&var={VAR}&fh=0"
        f"&lat={lat}&lon={lon}&domain={GLOBAL}"
    )
    assert response.status_code == 200, (lon, lat, response.status_code, response.text)
    return response.json()


# ─────────────────────────────────────────────────────────────────────────────
# SUITE A — sampling oracle
# ─────────────────────────────────────────────────────────────────────────────


def test_analytic_field_fits_packing_range() -> None:
    """Guard: no sample may be clipped at a packing endpoint."""
    packing = _packing_config(MODEL, VAR)
    assert packing is not None
    scale = float(packing["scale"])
    offset = float(packing["offset"])
    representable_min = offset
    representable_max = offset + scale * (int(packing["nodata"]) - 1)

    field_min = _FIELD_BASE - _FIELD_LON_AMPLITUDE - _FIELD_LAT_SLOPE * 90.0
    field_max = _FIELD_BASE + _FIELD_LON_AMPLITUDE + _FIELD_LAT_SLOPE * 90.0
    assert representable_min < field_min, (representable_min, field_min)
    assert field_max < representable_max, (field_max, representable_max)


def test_global_grid_geometry_is_the_registered_native_grid() -> None:
    """Pins the grid the oracle is derived against (contract §1)."""
    grid = get_target_grid(MODEL, GLOBAL)
    assert grid.crs == "EPSG:4326"
    assert grid.resolution == SRC_STEP_DEG == 0.25
    assert (grid.height, grid.width) == (721, 1440)
    assert tuple(grid.transform)[:6] == pytest.approx((0.25, 0.0, -180.125, 0.0, -0.25, 90.125))
    assert grid.bbox == pytest.approx((-180.125, -90.125, 179.875, 90.125))


def test_the_roll_path_claims_this_source() -> None:
    """The index-roll branch must accept the AIFS global source geometry.

    The AIFS open source is on the same 0.25° 0–360° global grid as GFS, so the
    same branch applies. Values alone cannot prove this: on a grid-aligned source
    rasterio's reproject is bit-exact, so a frame that matches the roll is
    also what a fallback warp would produce. This pins the branch.
    """
    values, src_transform = _source_raster()
    grid = get_target_grid(MODEL, GLOBAL)
    rolled = _index_roll_to_target_grid(values, "EPSG:4326", src_transform, grid)
    assert rolled is not None, "index-roll branch rejected the native AIFS global source"
    assert rolled.shape == (grid.height, grid.width)


def test_build_is_a_pure_longitude_roll(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exactness proof: output == source rolled by half a world.

    Two independent claims: (1) no reprojection runs — the ``reproject`` the
    warp module actually calls is patched to explode; (2) the roll is the
    right one.
    """
    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("rasterio.warp.reproject ran: the build is not a pure roll")

    monkeypatch.setattr(raster_grid_module, "reproject", _explode)

    source, src_transform = _source_raster()
    built, transform = warp_to_target_grid(
        source,
        "EPSG:4326",
        src_transform,
        model=MODEL,
        region=GLOBAL,
        resampling="bilinear",
    )

    grid = get_target_grid(MODEL, GLOBAL)
    roll = grid.width // 2  # 0…360 → −180…+179.75
    assert np.array_equal(built, np.roll(source, roll, axis=1))
    assert tuple(transform)[:6] == pytest.approx(tuple(grid.transform)[:6])
    assert np.isfinite(built).all(), "nodata found after a pure index roll"


def test_poles_are_real_rows() -> None:
    """Contract §1: row 0 is +90 and row 720 is −90, both carrying data."""
    built, transform = _warped_global_field()
    assert built.shape[0] == 721
    assert np.isfinite(built[0]).all() and np.isfinite(built[720]).all()

    _lon, north = transform * (0.5, 0.5)
    _lon, south = transform * (0.5, 720.5)
    assert north == pytest.approx(90.0)
    assert south == pytest.approx(-90.0)


def test_display_prep_is_absent_so_the_sampled_grid_is_the_warped_grid() -> None:
    """Pins the assumption behind the tolerance derivation: no upscaling
    display-prep config stands between the warp output and the encoded grid."""
    assert grid_display_prep_config(MODEL, VAR) is None


@pytest.mark.anyio
@pytest.mark.parametrize("lon", SEAM_LONGITUDES)
@pytest.mark.parametrize("lat", LATITUDES)
async def test_sample_matches_analytic_reference_within_tolerance(
    client: httpx.AsyncClient, published_global: Path, lon: float, lat: float
) -> None:
    """G1 oracle: every point agrees with f(lon, lat) within packing tolerance.

    Each longitude is judged against the analytic value AT THAT LONGITUDE;
    179°E and 179°W are distinct locations and nothing here asserts equality
    across the seam.
    """
    payload = await _sample(client, lon=lon, lat=lat)
    assert payload["noData"] is False, payload
    sampled = payload["value"]
    assert sampled is not None, payload

    expected = _analytic(lon, lat)
    delta = abs(float(sampled) - expected)
    assert delta <= TOLERANCE_F, (
        f"lon={lon} lat={lat}: sampled={sampled} expected={expected:.4f} "
        f"delta={delta:.4f} > tolerance={TOLERANCE_F:.4f}"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("lat", LATITUDES)
async def test_179e_and_179w_are_distinct_locations(
    client: httpx.AsyncClient, published_global: Path, lat: float
) -> None:
    """The seam gate: each side matches its OWN reference and the two differ
    by more than tolerance — a seam collapse shows up as that gap shrinking."""
    east = await _sample(client, lon=179.0, lat=lat)
    west = await _sample(client, lon=-179.0, lat=lat)

    east_value = float(east["value"])
    west_value = float(west["value"])

    assert abs(east_value - _analytic(179.0, lat)) <= TOLERANCE_F, east
    assert abs(west_value - _analytic(-179.0, lat)) <= TOLERANCE_F, west

    expected_gap = abs(_analytic(179.0, lat) - _analytic(-179.0, lat))
    assert expected_gap > 2 * TOLERANCE_F, "fixture too flat to discriminate"
    assert abs(east_value - west_value) > TOLERANCE_F, (
        f"179E and 179W collapsed: {east_value} vs {west_value}"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("lat", LATITUDES)
async def test_tolerance_is_not_vacuously_wide(
    client: httpx.AsyncClient, published_global: Path, lat: float
) -> None:
    """Negative control at maximum longitudinal gradient: a reference two
    degrees away must NOT pass. If it does, the oracle above is worthless."""
    for lon in (0.0, 180.0):
        payload = await _sample(client, lon=lon, lat=lat)
        sampled = float(payload["value"])

        assert abs(sampled - _analytic(lon, lat)) <= TOLERANCE_F, payload

        wrong_reference = _analytic(lon + 2.0, lat)
        assert abs(sampled - wrong_reference) > TOLERANCE_F, (
            f"lon={lon}: tolerance {TOLERANCE_F:.4f} swallowed a 2° error "
            f"(sampled={sampled}, wrong_reference={wrong_reference:.4f})"
        )


def _frame_meta(published_global: Path) -> dict:
    return json.loads(
        (
            published_global
            / "published"
            / MODEL
            / "domains"
            / GLOBAL
            / RUN_ID
            / VAR
            / "grid"
            / "fh000.l0.meta.json"
        ).read_text()
    )


def test_published_frame_declares_the_native_projection(published_global: Path) -> None:
    meta = _frame_meta(published_global)
    assert meta["projection"] == "EPSG:4326"
    assert (meta["height"], meta["width"]) == (721, 1440)


def test_seam_endpoints_address_the_same_column(published_global: Path) -> None:
    """±180 are the same meridian, pinned at the addressing level: −180 is the
    seam column (col 0) and +180, which is not a column centre, wraps onto it."""
    meta = _frame_meta(published_global)
    width = int(meta["width"])

    _row_e, col_east = sampling_module._sample_binary_frame_index(meta, lon=180.0, lat=0.0)
    _row_w, col_west = sampling_module._sample_binary_frame_index(meta, lon=-180.0, lat=0.0)

    assert col_west == 0
    assert col_east == 0
    # ±179.75 remain the distinct edge columns either side of the seam.
    _row, col_last = sampling_module._sample_binary_frame_index(meta, lon=179.75, lat=0.0)
    assert col_last == width - 1


def test_pole_rows_are_addressable(published_global: Path) -> None:
    meta = _frame_meta(published_global)
    north_row, _col = sampling_module._sample_binary_frame_index(meta, lon=0.0, lat=90.0)
    south_row, _col = sampling_module._sample_binary_frame_index(meta, lon=0.0, lat=-90.0)
    assert north_row == 0
    assert south_row == int(meta["height"]) - 1 == 720


@pytest.mark.anyio
@pytest.mark.parametrize("lon", (-180.0, -150.0, 0.0, 179.75))
@pytest.mark.parametrize("lat", (0.0, 35.25, -35.25))
async def test_sample_at_a_gridpoint_is_exact_to_the_packing_quantum(
    client: httpx.AsyncClient, published_global: Path, lon: float, lat: float
) -> None:
    """No warp error term exists on this path (contract §3): every (lon, lat)
    here is a grid centre, so the only slack is ±scale/2 plus API rounding.
    That the *roll* branch produced the frame is pinned separately above."""
    payload = await _sample(client, lon=lon, lat=lat)
    sampled = float(payload["value"])
    delta = abs(sampled - _analytic(lon, lat))
    assert delta <= GRIDPOINT_TOLERANCE_F, (
        f"lon={lon} lat={lat}: sampled={sampled} expected={_analytic(lon, lat):.4f} "
        f"delta={delta:.4f} > gridpoint tolerance={GRIDPOINT_TOLERANCE_F:.4f}"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("lat", LATITUDES)
async def test_plus_180_wraps_onto_the_minus_180_column(
    client: httpx.AsyncClient, published_global: Path, lat: float
) -> None:
    """Contract §3: +180 is API-legal and is the same physical meridian."""
    east = await _sample(client, lon=180.0, lat=lat)
    west = await _sample(client, lon=-180.0, lat=lat)
    assert east["noData"] is False and west["noData"] is False
    assert float(east["value"]) == float(west["value"])


@pytest.mark.anyio
@pytest.mark.parametrize("lat", (90.0, -90.0))
async def test_sampling_at_the_poles_returns_data(
    client: httpx.AsyncClient, published_global: Path, lat: float
) -> None:
    """The mercator grid could not represent these latitudes at all."""
    payload = await _sample(client, lon=0.0, lat=lat)
    assert payload["noData"] is False, payload
    sampled = float(payload["value"])
    assert abs(sampled - _analytic(0.0, lat)) <= GRIDPOINT_TOLERANCE_F, payload


# ─────────────────────────────────────────────────────────────────────────────
# SUITE B — contour seam behaviour
# ─────────────────────────────────────────────────────────────────────────────


def _warped_bump_field() -> tuple[np.ndarray, object]:
    """The GFS suite's two Gaussian bumps (one straddling the antimeridian,
    one control over North America), warped onto the AIFS global grid."""
    step = SRC_STEP_DEG
    lons = np.arange(0.0, 360.0, step)
    lats = np.arange(80.0, -80.0 - step / 2.0, -step)
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    values = (
        _BUMP_PEAK * _bump(lon_grid, lat_grid, _SEAM_BUMP_CENTRE)
        + _BUMP_PEAK * _bump(lon_grid, lat_grid, _CONTROL_BUMP_CENTRE)
    ).astype(np.float32)
    transform = from_origin(-step / 2.0, 80.0 + step / 2.0, step, step)
    return warp_to_target_grid(
        values, "EPSG:4326", transform, model=MODEL, region=GLOBAL, resampling="bilinear"
    )


@pytest.fixture
def seam_contours(tmp_path: Path) -> dict:
    warped, transform = _warped_bump_field()
    out_path = tmp_path / "contours" / "fh000_seam.geojson"
    build_iso_contour_geojson(
        value_data=warped,
        value_transform=transform,
        value_crs=get_grid_crs(MODEL, GLOBAL),
        out_geojson_path=out_path,
        levels=[_CONTOUR_LEVEL],
    )
    payload = json.loads(out_path.read_text())
    assert payload.get("type") == "FeatureCollection"
    return payload


@requires_gdal_cli
def test_contour_coordinates_stay_inside_the_legal_longitude_range(
    seam_contours: dict,
) -> None:
    for index, feature in enumerate(seam_contours["features"]):
        for lon, lat in _flatten_coordinates(feature["geometry"]):
            assert -180.0 <= lon <= 180.0, f"feature {index}: lon {lon} outside [-180, 180]"
            assert -90.0 <= lat <= 90.0, f"feature {index}: lat {lat} outside [-90, 90]"


@requires_gdal_cli
def test_no_contour_feature_spans_the_globe(seam_contours: dict) -> None:
    """The globe-spanning-artifact detector: both fixture features are ≲30°
    wide, so a ≥350° span can only be a "closed the long way round" artifact."""
    for index, feature in enumerate(seam_contours["features"]):
        lons = [point[0] for point in _flatten_coordinates(feature["geometry"])]
        span = max(lons) - min(lons)
        assert span < 350.0, (
            f"feature {index} spans {span:.2f}° of longitude — globe-spanning artifact"
        )


@requires_gdal_cli
def test_seam_crossing_feature_is_represented(seam_contours: dict) -> None:
    """Absence is a failure: split at ±180 or legally wrapped are both fine,
    the seam bump vanishing is not."""
    seam_lat = _SEAM_BUMP_CENTRE[1]
    touches_east = False
    touches_west = False
    lat_span_ok = False

    for feature in seam_contours["features"]:
        points = _flatten_coordinates(feature["geometry"])
        lons = [point[0] for point in points]
        lats = [point[1] for point in points]
        near_east = any(lon >= 179.5 for lon in lons)
        near_west = any(lon <= -179.5 for lon in lons)
        if not (near_east or near_west):
            continue
        touches_east = touches_east or near_east
        touches_west = touches_west or near_west
        if min(lats) < seam_lat < max(lats):
            lat_span_ok = True

    assert touches_east and touches_west, (
        "the seam-crossing feature is missing from at least one side of the "
        f"dateline (east={touches_east}, west={touches_west})"
    )
    assert lat_span_ok, (
        f"no dateline-touching feature brackets the bump centre latitude {seam_lat}"
    )


@requires_gdal_cli
def test_control_feature_far_from_the_seam_is_closed_and_sane(
    seam_contours: dict,
) -> None:
    """The control bump must come out as one closed, correctly placed ring."""
    control_lon, control_lat = _CONTROL_BUMP_CENTRE
    matches = []
    for feature in seam_contours["features"]:
        points = _flatten_coordinates(feature["geometry"])
        lons = [point[0] for point in points]
        lats = [point[1] for point in points]
        if min(lons) < control_lon < max(lons) and min(lats) < control_lat < max(lats):
            matches.append((feature, points, lons, lats))

    assert len(matches) == 1, f"expected exactly one control feature, got {len(matches)}"
    feature, points, lons, lats = matches[0]

    assert points[0] == points[-1], "control ring is not closed"
    assert float(feature["properties"]["value"]) == pytest.approx(_CONTOUR_LEVEL)
    # Half-peak contour of a σ=12° Gaussian: radius ≈ 14.1°, so a diameter near
    # 28° in each axis. Generous bounds — this only has to catch a grossly
    # mislocated or degenerate ring.
    assert 10.0 < (max(lons) - min(lons)) < 60.0
    assert 10.0 < (max(lats) - min(lats)) < 60.0
    assert all(-180.0 <= lon <= 180.0 for lon in lons)


@requires_gdal_cli
def test_contours_are_produced_at_all(seam_contours: dict) -> None:
    """Cheap canary: an empty FeatureCollection would make the assertions above
    pass vacuously."""
    assert len(seam_contours["features"]) >= 2
