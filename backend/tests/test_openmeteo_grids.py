"""Grid decode / sampler tests for the Open-Meteo fast path (WP1).

No network: every test drives synthetic fields through the real samplers.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.builder.raster_grid import (
    compute_transform_and_shape,
    get_grid_params,
    get_target_grid,
)
from app.services.sources.openmeteo import grids


# ---------------------------------------------------------------------------
# Target grid parity with raster_grid (drift guard)
# ---------------------------------------------------------------------------


def test_na_target_matches_raster_grid_shape_and_snapped_bbox() -> None:
    bbox, resolution = get_grid_params("ecmwf", "na")
    transform, height, width = compute_transform_and_shape(bbox, resolution)

    target = grids.na_target_9km()

    assert (height, width) == grids.NA_9KM_SHAPE == (1825, 1893)
    assert target.shape == (height, width)
    assert target.bbox == grids.NA_9KM_SNAPPED_BBOX
    assert target.bbox == (-19818000.0, 549000.0, -2781000.0, 16974000.0)
    # The pinned bbox is the TAP-snapped extent implied by the transform.
    assert float(transform.c) == target.bbox[0]
    assert float(transform.f) == target.bbox[3]
    assert target.crs == "EPSG:3857"


def test_na_target_edge_cell_centres_sit_outside_a_naive_5_to_82_band() -> None:
    """The reason the fetch band is 4.5–82.5°N, not 5–82 (prototype gotcha 5)."""
    target = grids.na_target_9km()

    assert target.lat.max() == pytest.approx(82.0021, abs=1e-3)
    assert target.lat.min() == pytest.approx(4.9659, abs=1e-3)
    assert not (5.0 <= target.lat.min() and target.lat.max() <= 82.0)
    assert grids.NA_BAND_LAT == (4.5, 82.5)


def test_global_target_matches_native_geographic_contract() -> None:
    production = get_target_grid("ecmwf", "global")
    target = grids.global_target_025()

    assert target.shape == (production.height, production.width) == (721, 1440)
    assert target.crs == "EPSG:4326"
    # Row 0 north, centres 90 .. -90; columns -180 .. 179.75.
    assert target.lat[0, 0] == pytest.approx(90.0)
    assert target.lat[-1, 0] == pytest.approx(-90.0)
    assert target.lon[0, 0] == pytest.approx(-180.0)
    assert target.lon[0, -1] == pytest.approx(179.75)


# ---------------------------------------------------------------------------
# O1280 geometry invariants
# ---------------------------------------------------------------------------


def test_o1280_row_counts_and_total_points() -> None:
    assert grids.O1280_NROWS == 2560
    assert grids.o1280_points_in_row(1) == 20
    assert grids.o1280_points_in_row(1280) == 16 + 4 * 1280
    # Hemispheric symmetry.
    for row in (1, 7, 640, 1280):
        assert grids.o1280_points_in_row(row) == grids.o1280_points_in_row(
            grids.O1280_NROWS + 1 - row
        )
    total = sum(grids.o1280_points_in_row(i) for i in range(1, grids.O1280_NROWS + 1))
    assert total == grids.O1280_TOTAL_POINTS == 6_599_680


def test_o1280_row_starts_are_a_contiguous_partition() -> None:
    assert grids.o1280_row_start(1) == 0
    for row in (1, 2, 500, 1280, 1281, 2559):
        assert grids.o1280_row_start(row + 1) == grids.o1280_row_start(
            row
        ) + grids.o1280_points_in_row(row)
    last = grids.O1280_NROWS
    assert (
        grids.o1280_row_start(last) + grids.o1280_points_in_row(last)
        == grids.O1280_TOTAL_POINTS
    )


def test_o1280_row_latitudes_are_descending_gaussian() -> None:
    lats = grids.o1280_row_latitudes()

    assert lats.shape == (grids.O1280_NROWS,)
    assert np.all(np.diff(lats) < 0)  # north first
    assert lats[0] > 89.0 and lats[-1] < -89.0
    assert lats[0] == pytest.approx(-lats[-1], abs=1e-9)  # symmetric
    # Exact Gaussian latitudes differ from the uniform approximation, which is
    # the whole point (the uniform version shifts rows by up to ~2 km).
    uniform = 90.0 - (np.arange(grids.O1280_NROWS) + 0.5) * (180.0 / grids.O1280_NROWS)
    assert np.max(np.abs(lats - uniform)) > 0.01


def test_na_band_contains_every_index_the_na_sampler_needs() -> None:
    start, stop = grids.na_band_index_range()
    sampler = grids.get_sampler("o1280", "na_9km")
    lo, hi = sampler.index_range

    assert start <= lo and hi <= stop
    # A naive 5–82 band would not contain them.
    naive_start, naive_stop = grids.o1280_band_indices(5.0, 82.0)
    assert not (naive_start <= lo and hi <= naive_stop)


# ---------------------------------------------------------------------------
# Sampler correctness
# ---------------------------------------------------------------------------


def _o1280_point_latitudes() -> np.ndarray:
    lats = grids.o1280_row_latitudes()
    counts = np.array(
        [grids.o1280_points_in_row(i) for i in range(1, grids.O1280_NROWS + 1)]
    )
    return np.repeat(lats, counts).astype(np.float64)


@pytest.fixture(scope="module")
def na_sampler() -> grids.Sampler:
    return grids.get_sampler("o1280", "na_9km")


def test_o1280_sampler_weights_sum_to_one(na_sampler: grids.Sampler) -> None:
    assert na_sampler.idx.shape == na_sampler.weights.shape
    assert na_sampler.idx.shape[0] == 4
    assert na_sampler.idx.dtype == np.int32
    assert np.allclose(na_sampler.weights.sum(axis=0), 1.0, atol=1e-6)
    assert np.all(na_sampler.weights >= 0.0)


def test_o1280_constant_field_maps_to_a_constant_output(na_sampler: grids.Sampler) -> None:
    source = np.full(grids.O1280_TOTAL_POINTS, 7.25, dtype=np.float32)

    out = grids.apply_sampler(na_sampler, source)

    assert out.shape == grids.NA_9KM_SHAPE
    assert out.dtype == np.float32
    assert np.allclose(out, 7.25, atol=1e-5)


def test_o1280_field_linear_in_latitude_reproduces_target_latitudes(
    na_sampler: grids.Sampler,
) -> None:
    """Bilinear interpolation of f(lat) = lat is exact away from the poles."""
    source = _o1280_point_latitudes()
    target = grids.na_target_9km()

    out = grids.apply_sampler(na_sampler, source)

    assert np.max(np.abs(out - target.lat)) < 1e-3


def test_o1280_band_read_with_offset_matches_the_full_globe_read(
    na_sampler: grids.Sampler,
) -> None:
    source = _o1280_point_latitudes()
    start, stop = grids.na_band_index_range()

    full = grids.apply_sampler(na_sampler, source)
    banded = grids.apply_sampler(na_sampler, source[start:stop], offset=start)

    assert np.array_equal(full, banded)


def test_apply_sampler_rejects_a_band_that_does_not_cover_the_sampler(
    na_sampler: grids.Sampler,
) -> None:
    source = _o1280_point_latitudes()
    start, stop = grids.na_band_index_range()

    truncated = start + 10_000  # starts after the first index the sampler needs
    with pytest.raises(ValueError, match="supplied band covers"):
        grids.apply_sampler(na_sampler, source[truncated:stop], offset=truncated)


def test_o1280_global_sampler_wraps_the_antimeridian() -> None:
    """A lon-only ramp must stay continuous across the 180° seam."""
    sampler = grids.get_sampler("o1280", "global_025")
    counts = np.array(
        [grids.o1280_points_in_row(i) for i in range(1, grids.O1280_NROWS + 1)]
    )
    # cos/sin of longitude: smooth and periodic, so wrap errors show up as a
    # discontinuity at the seam rather than as interpolation noise.
    lons = np.concatenate([360.0 * np.arange(n) / n for n in counts])
    source = np.cos(np.radians(lons))

    target = grids.global_target_025()
    out = grids.apply_sampler(sampler, source)
    expected = np.cos(np.radians(target.lon))

    assert out.shape == (721, 1440)
    # Restrict to |lat| <= 60: poleward of that the reduced-Gaussian rows carry
    # as few as 20 points (18° apart), so a lon-varying field is genuinely
    # under-resolved there and interpolation error is physical, not a bug.
    midlat = np.abs(target.lat[:, 0]) <= 60.0
    assert np.max(np.abs(out[midlat] - expected[midlat])) < 1e-4

    # Antimeridian continuity: the step across the seam must be no larger than
    # the largest step anywhere in the interior of the same rows.
    seam_step = np.abs(out[midlat, -1] - out[midlat, 0]).max()
    interior_step = np.abs(np.diff(out[midlat], axis=1)).max()
    assert seam_step <= interior_step * 1.5


def test_regular_sampler_is_exact_for_constant_and_lat_linear_fields() -> None:
    source_grid = grids.REGULAR_025_SOURCE
    lat = np.array([[10.0, 45.0], [-33.0, 0.0]])
    lon = np.array([[-120.0, 5.0], [100.0, 179.9]])

    sampler = grids.build_regular_bilinear_sampler(lat, lon, source_grid)

    assert np.allclose(sampler.weights.sum(axis=0), 1.0, atol=1e-6)

    constant = np.full(source_grid.points, -3.5, dtype=np.float32)
    assert np.allclose(grids.apply_sampler(sampler, constant), -3.5, atol=1e-5)

    row_lats = source_grid.row_latitudes()
    lat_field = np.repeat(row_lats, source_grid.nlon)
    out = grids.apply_sampler(sampler, lat_field)
    assert np.max(np.abs(out - lat)) < 1e-3


def test_regular_sampler_wraps_longitude_across_the_seam() -> None:
    source_grid = grids.REGULAR_025_SOURCE
    # 179.875 sits exactly halfway between the last column (179.75) and the
    # first (-180.0), so a correct sampler blends those two columns.
    sampler = grids.build_regular_bilinear_sampler(
        np.array([[0.0]]), np.array([[179.875]]), source_grid
    )
    columns = sampler.idx[:, 0] % source_grid.nlon

    assert set(columns.tolist()) == {source_grid.nlon - 1, 0}
    assert np.allclose(sampler.weights[:, 0].sum(), 1.0, atol=1e-6)


# ---------------------------------------------------------------------------
# Sampler cache
# ---------------------------------------------------------------------------


def test_sampler_cache_returns_the_same_object_and_precompute_warms_it() -> None:
    first = grids.get_sampler("o1280", "na_9km")
    second = grids.get_sampler("o1280", "na_9km")

    assert first is second

    grids.precompute()
    for source_kind, target in grids.DEFAULT_SAMPLER_PAIRS:
        assert grids.get_sampler(source_kind, target) is not None


def test_sampler_cache_rejects_unknown_pairs() -> None:
    with pytest.raises(KeyError, match="unknown source grid kind"):
        grids.get_sampler("o640", "na_9km")
    with pytest.raises(KeyError, match="unknown target grid"):
        grids.get_sampler("o1280", "europe_9km")


# ---------------------------------------------------------------------------
# Orientation detection and self-check
# ---------------------------------------------------------------------------


def _synthetic_025_field_south_to_north() -> np.ndarray:
    """721×1440 field: smooth in latitude, noisy only in the 45°N row."""
    source_grid = grids.REGULAR_025_SOURCE
    lats = source_grid.row_latitudes()
    field = np.repeat(30.0 - 0.5 * np.abs(lats), source_grid.nlon).reshape(
        source_grid.nlat, source_grid.nlon
    )
    rng = np.random.default_rng(0)
    row_45n = int(round((45.0 - source_grid.lat0) / source_grid.dlat))
    field[row_45n] += rng.normal(0.0, 8.0, source_grid.nlon)
    return field.astype(np.float32)


def test_detect_south_to_north_identifies_both_orientations() -> None:
    s2n = _synthetic_025_field_south_to_north()

    assert grids.detect_south_to_north(s2n) is True
    assert grids.detect_south_to_north(s2n[::-1]) is False


def test_detect_south_to_north_requires_a_2d_field() -> None:
    with pytest.raises(ValueError, match="2D field"):
        grids.detect_south_to_north(np.zeros(721 * 1440, dtype=np.float32))


def test_tropics_warmer_than_arctic_self_check() -> None:
    lats = np.linspace(5.0, 82.0, 60)
    good = np.repeat(30.0 - 0.4 * lats, 10).reshape(60, 10)

    assert grids.tropics_warmer_than_arctic(good, lats) is True
    assert grids.tropics_warmer_than_arctic(good[::-1], lats) is False


def test_tropics_warmer_than_arctic_validates_row_count() -> None:
    with pytest.raises(ValueError, match="row_latitudes"):
        grids.tropics_warmer_than_arctic(np.zeros((4, 3)), np.array([1.0, 2.0]))
