"""Phase 3 — G1 antimeridian gates for the GFS global domain (25 km).

Two synthetic oracles, both driven through the REAL production code paths
(`warp_to_target_grid`, the grid binary writer, the `/api/v4/sample` route,
`build_iso_contour_geojson`) rather than reimplementations:

**Suite A — sampling oracle.** A smooth, single-valued analytic field on a
GFS-style 0–360° EPSG:4326 source is warped onto the registered global 25 km
EPSG:3857 grid, written as a real packed grid binary into a domain-scoped
published tree, and sampled through the API at 0°, ±179°, ±179.9°, ±179.99°,
±180° and mid-Pacific. Every sample must agree with the analytic reference
*at that longitude* within a derived tolerance.

  179°E and 179°W are DISTINCT LOCATIONS. Nothing here asserts equality
  across the seam; the seam test asserts the two sides *differ* by more than
  tolerance, which is what proves the seam has not collapsed.

**Suite B — contour seam behaviour.** A closed feature straddling lon=180
plus a control feature far from the seam, contoured through the real GDAL CLI
path. The output must stay inside [−180, 180], must contain no
globe-spanning feature, and must actually represent the seam-crossing
feature (split at ±180 or legally wrapped — absence is a failure).

Tolerance derivation lives in :func:`_sampling_tolerance`; read it before
loosening any number here.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
from collections.abc import AsyncIterator, Iterator
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
os.environ.setdefault("TWF_CLIENT_ID", "test-client")
os.environ.setdefault("TWF_CLIENT_SECRET", "test-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_antimeridian_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import main as main_module  # noqa: E402
from app.services import sampling as sampling_module  # noqa: E402
from app.services.builder.pipeline import build_iso_contour_geojson  # noqa: E402
from app.services.builder.raster_grid import (  # noqa: E402
    compute_transform_and_shape,
    get_grid_params,
)
from app.services.builder.raster_grid import warp_to_target_grid  # noqa: E402
from app.services.grid import (  # noqa: E402
    _packing_config,
    build_grid_manifests_for_run_root,
    write_grid_frames_for_run_root,
)
from app.services.grid_display_prep import grid_display_prep_config  # noqa: E402

MODEL = "gfs"
CANONICAL = "na"
GLOBAL = "global"
VAR = "tmp2m"
RUN_ID = "20260729_00z"
FLAG = "CARTOSKY_GLOBAL_DOMAIN_MODELS"

#: Source grid step in degrees. GFS ships 0.25°; 0.5° keeps the fixture warp
#: fast and is still fine enough that source-side interpolation error is
#: negligible against the packing floor (see `_sampling_tolerance`).
SRC_STEP_DEG = 0.5

#: Analytic field, in the packed units of gfs/tmp2m (°F).
#:
#: Chosen so that it is smooth and SINGLE-VALUED around the seam — the
#: `sin` term is continuous and periodic in longitude, so f(180−ε) → f(180+ε)
#: with no discontinuity for the warp to smear. Coefficients are small enough
#: that the whole range sits comfortably inside the packed representable
#: range (pinned by `test_analytic_field_fits_packing_range`), so no sample
#: can be silently clipped at a packing endpoint.
_FIELD_BASE = 60.0
_FIELD_LON_AMPLITUDE = 50.0
_FIELD_LAT_SLOPE = 0.5

#: WGS84 semi-major axis, as EPSG:3857 uses it.
_EARTH_RADIUS_M = 6378137.0


def _analytic(lon: float, lat: float) -> float:
    """f(lon, lat) — the oracle. Degrees in, °F out."""
    return (
        _FIELD_BASE
        + _FIELD_LON_AMPLITUDE * math.sin(math.radians(lon))
        + _FIELD_LAT_SLOPE * lat
    )


def _analytic_array(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    return (
        _FIELD_BASE
        + _FIELD_LON_AMPLITUDE * np.sin(np.radians(lon))
        + _FIELD_LAT_SLOPE * lat
    )


# ── tolerance ──────────────────────────────────────────────────────────────


def _sampling_tolerance() -> float:
    """Derive the sample-vs-analytic tolerance from first principles.

    The sampled value is the value of the grid *pixel containing* the query
    point, decoded from a packed uint16 and rounded by the API. Four terms,
    each bounded from the real grid/packing parameters rather than guessed:

    1. **Pixel-centre offset** (dominant). Sampling is NEAREST-pixel:
       `_sample_binary_frame_index` floors the inverse-affine result, so the
       returned value describes the pixel centre, which is up to half a pixel
       away from the query point in each axis. Half a 25 km pixel is 12 500 m
       in EPSG:3857.
         · longitude: dλ = 12 500 · 180/(π·R) ≈ 0.1123°, uniform in latitude.
           |∂f/∂λ| = A·π/180·|cos λ| ≤ 0.8727 °F/deg.
         · latitude: for Mercator dφ = dy·cos(φ)/R, so dφ ≤ 0.1123° (worst
           case at the equator). |∂f/∂φ| = 0.5 °F/deg.
       Second-order curvature over half a pixel is < 1e-3 °F and is absorbed
       by the margin below.
    2. **Bilinear resampling slack** from the 0.5° source grid. Linear
       interpolation error is ≤ (1/8)·max|∂²f/∂λ²|·h². f is exactly linear in
       latitude, so that axis contributes nothing.
    3. **uint16 packing quantisation.** `_encode_values` does
       `rint((v − offset)/scale)`, so decode error ≤ scale/2. Read from the
       real packing config, not hardcoded.
    4. **API response rounding.** `_sample_payload` rounds to 1 decimal ⇒
       ≤ 0.05 °F.

    A 15 % margin covers float32 storage, pyproj/GDAL round-off, and the
    ignored curvature term. The result is ~0.29 °F — narrow enough that the
    negative controls in this module fail against a 2°-shifted reference,
    which is what keeps it from being vacuous.
    """
    _bbox, grid_m = get_grid_params(MODEL, GLOBAL)
    half_pixel_m = float(grid_m) / 2.0
    deg_per_meter = 180.0 / (math.pi * _EARTH_RADIUS_M)
    half_pixel_deg = half_pixel_m * deg_per_meter

    max_df_dlon = _FIELD_LON_AMPLITUDE * math.radians(1.0)  # |cos| ≤ 1
    max_df_dlat = _FIELD_LAT_SLOPE
    pixel_centre_term = half_pixel_deg * (max_df_dlon + max_df_dlat)

    max_d2f_dlon2 = _FIELD_LON_AMPLITUDE * math.radians(1.0) ** 2
    bilinear_term = 0.125 * max_d2f_dlon2 * SRC_STEP_DEG**2

    packing = _packing_config(MODEL, VAR)
    assert packing is not None, f"no packing config for {MODEL}/{VAR}"
    packing_term = float(packing["scale"]) / 2.0

    api_rounding_term = 0.05

    return 1.15 * (pixel_centre_term + bilinear_term + packing_term + api_rounding_term)


TOLERANCE_F = _sampling_tolerance()


# ── synthetic source + warp ────────────────────────────────────────────────


def _source_raster() -> tuple[np.ndarray, object]:
    """GFS-style global source: EPSG:4326, longitudes 0…360, north-up."""
    step = SRC_STEP_DEG
    lons = np.arange(0.0, 360.0, step)
    lats = np.arange(90.0, -90.0 - step / 2.0, -step)
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    values = _analytic_array(lon_grid, lat_grid).astype(np.float32)
    # Pixel *centres* sit on the coordinate arrays, so the origin is offset by
    # half a cell — the convention GDAL reports for a GFS GRIB.
    transform = from_origin(-step / 2.0, 90.0 + step / 2.0, step, step)
    return values, transform


_WARP_CACHE: dict[str, tuple[np.ndarray, object]] = {}


def _warped_global_field() -> tuple[np.ndarray, object]:
    """The real warp onto the registered global 25 km grid (cached per run)."""
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
    """The Phase 3 prod flip: GFS allowlisted so `domain=global` validates."""
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
    """Write the warped array into `published/gfs/domains/global/…`.

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
    )
    (var_dir / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "F", "valid_time": "2026-07-29T00:00:00Z"})
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
                        "frames": [{"fh": 0, "valid_time": "2026-07-29T00:00:00Z"}],
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

#: Latitudes exercised at every longitude. All well inside the ±85.05° Mercator
#: pole clip so no assertion depends on the clipped rows.
LATITUDES = (0.0, 35.0, -35.0)

#: The near-seam spread plus reference longitudes. `180.0` and `-180.0` are the
#: two *endpoints of the same meridian* and land in opposite edge columns of
#: the grid — they are deliberately both present and never compared.
SEAM_LONGITUDES = (
    0.0,
    179.0,
    -179.0,
    179.9,
    -179.9,
    179.99,
    -179.99,
    180.0,
    -180.0,
    -150.0,  # mid-Pacific, far from both the seam and the prime meridian
)


def test_analytic_field_fits_packing_range() -> None:
    """Guard: no sample may be clipped at a packing endpoint.

    `_encode_values` clips codes into [0, nodata−1]; a field exceeding that
    window would saturate and make the oracle agree for the wrong reason.
    """
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


def test_global_grid_geometry_is_the_registered_25km_grid() -> None:
    """Pins the grid the oracle is derived against."""
    bbox, grid_m = get_grid_params(MODEL, GLOBAL)
    assert grid_m == 25_000.0
    _transform, height, width = compute_transform_and_shape(bbox, grid_m)
    assert (height, width) == (1604, 1604)


def test_warp_leaves_no_seam_gap() -> None:
    """The warp must not punch a nodata stripe along the antimeridian.

    A 0–360 source reprojected to a −180…180 destination is exactly where a
    seam bug shows up as a column of NaN. Checked on the equatorial band,
    which is fully covered by the source.
    """
    warped, transform = _warped_global_field()
    _bbox, grid_m = get_grid_params(MODEL, GLOBAL)
    equator_row = int((transform.f - 0.0) / grid_m)
    band = warped[equator_row - 2 : equator_row + 3, :]
    assert np.isfinite(band).all(), "nodata found on the equatorial band after warp"


def test_display_prep_is_absent_so_the_sampled_grid_is_the_warped_grid() -> None:
    """Pins the assumption behind the tolerance derivation.

    If gfs/tmp2m ever gains an upscaling display-prep config, the encoded grid
    stops being the warp output and the pixel-centre term above no longer
    bounds the error. Fail loudly rather than silently widening.
    """
    assert grid_display_prep_config(MODEL, VAR) is None


@pytest.mark.anyio
@pytest.mark.parametrize("lon", SEAM_LONGITUDES)
@pytest.mark.parametrize("lat", LATITUDES)
async def test_sample_matches_analytic_reference_within_tolerance(
    client: httpx.AsyncClient, published_global: Path, lon: float, lat: float
) -> None:
    """G1 oracle: every point agrees with f(lon, lat) within packing tolerance.

    Each longitude is judged against the analytic value AT THAT LONGITUDE.
    There is no cross-seam equality assertion anywhere in this test.
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
    """The seam gate, stated the corrected way.

    f(179°) − f(−179°) = 2·A·sin(1°) ≈ 1.745 °F, several multiples of the
    tolerance. Each side must match its OWN reference, and the two must differ
    by more than tolerance — a seam collapse (one side aliasing to the other)
    would show up as the difference shrinking toward zero.
    """
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
    """Negative control at maximum longitudinal gradient.

    At lon=0 (and lon=180) |∂f/∂λ| is at its maximum, so a deliberately wrong
    reference two degrees away differs by ≈1.75 °F. If the tolerance were wide
    enough to swallow that, this test fails and the oracle above is worthless.
    """
    for lon in (0.0, 180.0):
        payload = await _sample(client, lon=lon, lat=lat)
        sampled = float(payload["value"])

        assert abs(sampled - _analytic(lon, lat)) <= TOLERANCE_F, payload

        wrong_reference = _analytic(lon + 2.0, lat)
        assert abs(sampled - wrong_reference) > TOLERANCE_F, (
            f"lon={lon}: tolerance {TOLERANCE_F:.4f} swallowed a 2° error "
            f"(sampled={sampled}, wrong_reference={wrong_reference:.4f})"
        )


def test_seam_endpoints_land_in_opposite_edge_columns(
    published_global: Path,
) -> None:
    """±180 are the same meridian but opposite ends of the grid.

    Asserted on the index math directly so that the "distinct locations"
    property is pinned at the addressing level, not just the value level.
    """
    meta_path = (
        published_global
        / "published"
        / MODEL
        / "domains"
        / GLOBAL
        / RUN_ID
        / VAR
        / "grid"
        / "fh000.l0.meta.json"
    )
    meta = json.loads(meta_path.read_text())
    width = int(meta["width"])

    _row_e, col_east = sampling_module._sample_binary_frame_index(meta, lon=180.0, lat=0.0)
    _row_w, col_west = sampling_module._sample_binary_frame_index(meta, lon=-180.0, lat=0.0)

    assert 0 <= col_west < col_east < width
    assert col_west == 0
    assert col_east == width - 1


# ─────────────────────────────────────────────────────────────────────────────
# SUITE B — contour seam behaviour
# ─────────────────────────────────────────────────────────────────────────────

#: `build_iso_contour_geojson` shells out to gdalwarp + gdal_contour. No other
#: test in this suite exercises the CLIs, so there is no established skip
#: marker to follow — this is the local convention.
_GDAL_CLI_AVAILABLE = all(shutil.which(tool) for tool in ("gdalwarp", "gdal_contour"))
requires_gdal_cli = pytest.mark.skipif(
    not _GDAL_CLI_AVAILABLE,
    reason="gdalwarp/gdal_contour CLIs not on PATH; build_iso_contour_geojson shells out",
)

#: Gaussian bumps: one straddling the antimeridian, one control over North
#: America. σ in degrees; the contour level is half the peak so each bump
#: yields one closed ring in an un-split world.
_BUMP_PEAK = 50.0
_BUMP_SIGMA_DEG = 12.0
_CONTOUR_LEVEL = 25.0
_SEAM_BUMP_CENTRE = (180.0, 10.0)
_CONTROL_BUMP_CENTRE = (-100.0, 40.0)


def _bump(lon: np.ndarray, lat: np.ndarray, centre: tuple[float, float]) -> np.ndarray:
    centre_lon, centre_lat = centre
    # Wrapped longitudinal separation — without this the "bump at 180" would be
    # a fixture artifact rather than a genuinely seam-crossing feature.
    d_lon = (lon - centre_lon + 180.0) % 360.0 - 180.0
    d_lat = lat - centre_lat
    return np.exp(-(d_lon**2 + d_lat**2) / (2.0 * _BUMP_SIGMA_DEG**2))


def _warped_bump_field() -> tuple[np.ndarray, object]:
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


def _flatten_coordinates(geometry: dict) -> list[list[float]]:
    points: list[list[float]] = []

    def walk(node: object) -> None:
        if isinstance(node, (list, tuple)) and node and isinstance(node[0], (int, float)):
            points.append([float(node[0]), float(node[1])])
            return
        assert isinstance(node, (list, tuple))
        for child in node:
            walk(child)

    walk(geometry["coordinates"])
    return points


@pytest.fixture
def seam_contours(tmp_path: Path) -> dict:
    warped, transform = _warped_bump_field()
    out_path = tmp_path / "contours" / "fh000_seam.geojson"
    build_iso_contour_geojson(
        value_data=warped,
        value_transform=transform,
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
    """The globe-spanning-artifact detector.

    The classic seam defect is a ring that "closes the long way round",
    producing a feature whose longitudinal bbox extent is ~360°. Both fixture
    features are ≲30° wide, so a ≥350° span can only be an artifact.
    """
    for index, feature in enumerate(seam_contours["features"]):
        lons = [point[0] for point in _flatten_coordinates(feature["geometry"])]
        span = max(lons) - min(lons)
        assert span < 350.0, (
            f"feature {index} spans {span:.2f}° of longitude — globe-spanning artifact"
        )


@requires_gdal_cli
def test_seam_crossing_feature_is_represented(seam_contours: dict) -> None:
    """Absence is a failure.

    Either representation is acceptable: split into two pieces that terminate
    on ±180, or one legally wrapped piece. What is not acceptable is the
    seam-crossing bump vanishing from the output.
    """
    seam_lat = _SEAM_BUMP_CENTRE[1]
    touches_east = False
    touches_west = False
    lat_span_ok = False

    for feature in seam_contours["features"]:
        points = _flatten_coordinates(feature["geometry"])
        lons = [point[0] for point in points]
        lats = [point[1] for point in points]
        # A piece of the seam bump must reach the dateline, on one side or both.
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
    # Half-peak contour of a σ=12° Gaussian: radius ≈ σ·sqrt(2·ln2) ≈ 14.1°,
    # so a diameter near 28° in each axis. Generous bounds — this only has to
    # catch a grossly mislocated or degenerate ring.
    assert 10.0 < (max(lons) - min(lons)) < 60.0
    assert 10.0 < (max(lats) - min(lats)) < 60.0
    assert all(-180.0 <= lon <= 180.0 for lon in lons)


@requires_gdal_cli
def test_contours_are_produced_at_all(seam_contours: dict) -> None:
    """Cheap canary: an empty FeatureCollection would make the assertions above
    pass vacuously."""
    assert len(seam_contours["features"]) >= 2
