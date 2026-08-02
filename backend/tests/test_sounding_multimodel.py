"""Phase 6 multi-model sounding stacks: GFS + ECMWF (Skew-T design §10).

Three things are load-bearing here and each gets an independent check:

1. **HRRR must not move.** The registry refactor turned module-level constants
   into per-model spec rows; a HRRR stack built from identical inputs has to be
   byte-identical to what Phase 1-5 wrote. The guard recomputes the expected
   buffer from the design's own offset formula rather than from the code under
   test.
2. **Td-from-q** (decision §8 #7) is the only new physics. It is checked against
   MetPy computed independently *and* against
   ``dewpoint_from_specific_humidity`` where that helper exists.
3. **Global-grid longitude wrap** in the endpoint's coordinate path — the GFS /
   ECMWF grids run 0 -> 359.75, so a normal western-hemisphere request and the
   Greenwich wrap gap both have to land on the right column.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from pyproj import CRS
from rasterio.transform import Affine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import sounding  # noqa: E402
from app.services import sounding_api  # noqa: E402
from app.services import sounding_indices  # noqa: E402

RUN_ID = "20260802_06z"
RUN_DATE = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Registry shape — the measured inventory, pinned
# ---------------------------------------------------------------------------


def test_registry_covers_exactly_the_three_shipped_models() -> None:
    assert sorted(sounding.MODEL_SPECS) == ["ecmwf", "gfs", "hrrr"]
    assert sounding.SUPPORTED_MODELS == frozenset(sounding.MODEL_SPECS)
    for model_id, spec in sounding.MODEL_SPECS.items():
        assert spec.model_id == model_id
        # Every model stores the same five series in the same order, which is
        # what lets the reader, indices and client stay ladder-agnostic.
        assert [var.id for var in spec.isobaric_variables] == ["t", "td", "u", "v", "w"]
        assert [field.id for field in spec.surface_fields] == [
            "pres_sfc",
            "t2m",
            "td2m",
            "u10m",
            "v10m",
            "cape_sfc",
        ]


def test_hrrr_spec_reproduces_the_phase_1_geometry() -> None:
    spec = sounding.HRRR_SPEC
    assert spec.levels_hpa == tuple(range(1000, 99, -25))
    assert len(spec.levels_hpa) == 37
    assert spec.n_isobaric_planes == 185
    assert spec.n_planes == 191
    assert spec.bytes_per_pixel == 382
    assert spec.stride == 4
    assert spec.td_policy == sounding.TD_NATIVE
    assert spec.isobaric_variables[1].grib_element == "DPT"
    assert [group.product for group in spec.fetch_groups] == ["prs", "sfc"]


def test_gfs_spec_matches_the_measured_inventory() -> None:
    spec = sounding.GFS_SPEC
    assert spec.levels_hpa == (
        1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600,
        550, 500, 450, 400, 350, 300, 250, 200, 150, 100,
    )
    assert len(spec.levels_hpa) == 21
    # 5 vars x 21 levels + 6 surface = 111 planes / 222 B per pixel (§10).
    assert spec.n_planes == 111
    assert spec.bytes_per_pixel == 222
    assert spec.stride == 2
    assert spec.td_policy == sounding.TD_FROM_Q
    # No isobaric DPT exists in pgrb2/pgrb2b: the td slot is fetched as SPFH.
    assert spec.isobaric_variables[1].grib_element == "SPFH"
    # One subset per fh: pgrb2 carries the ladder and the surface block.
    assert [group.product for group in spec.fetch_groups] == ["pgrb2.0p25"]
    assert spec.fetch_groups[0].isobaric and spec.fetch_groups[0].surface


def test_ecmwf_spec_matches_the_measured_inventory() -> None:
    spec = sounding.ECMWF_SPEC
    assert spec.levels_hpa == (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100)
    assert len(spec.levels_hpa) == 12
    assert spec.n_planes == 66
    assert spec.bytes_per_pixel == 132
    assert spec.stride == 2
    assert spec.td_policy == sounding.TD_FROM_Q
    assert spec.herbie_model == "ifs"
    # `scda` is retired (MEASURED 2026-08-02): every cycle is `oper`, and the
    # adapter must carry no scda branch at all.
    assert [group.product for group in spec.fetch_groups] == ["oper"]
    assert "scda" not in repr(spec)


def test_only_the_ecmwf_cape_diagnostic_is_optional() -> None:
    # mucape is an ECMWF *local* parameter, so its GDAL tags are not pinned by a
    # WMO template; failing to identify it must cost the row, not the build.
    assert sounding.ECMWF_SPEC.optional_planes == frozenset({65})
    assert sounding.GFS_SPEC.optional_planes == frozenset()
    assert sounding.HRRR_SPEC.optional_planes == frozenset()


def test_cape_display_labels_are_per_model_and_ecmwf_is_not_surface_based() -> None:
    labels = {
        spec.model_id: spec.surface_fields[-1].display_label
        for spec in sounding.MODEL_SPECS.values()
    }
    assert labels == {"hrrr": "HRRR SBCAPE", "gfs": "GFS SBCAPE", "ecmwf": "MUCAPE"}
    # The response field name is a stable contract even where the quantity is
    # most-unstable rather than surface-based; the id must therefore not fork.
    assert all(spec.surface_fields[-1].id == "cape_sfc" for spec in sounding.MODEL_SPECS.values())


# ---------------------------------------------------------------------------
# Search patterns
# ---------------------------------------------------------------------------


def test_hrrr_search_patterns_are_unchanged() -> None:
    assert sounding.isobaric_search_pattern() == (
        ":(TMP|DPT|UGRD|VGRD|VVEL):("
        + "|".join(str(level) for level in range(1000, 99, -25))
        + ") mb:"
    )
    assert sounding.surface_search_pattern() == (
        ":(PRES:surface|TMP:2 m above ground|DPT:2 m above ground|"
        "UGRD:10 m above ground|VGRD:10 m above ground|CAPE:surface):"
    )


def test_gfs_pattern_selects_spfh_not_dpt_and_never_a_wildcard_level() -> None:
    pattern = sounding.GFS_SPEC.isobaric_search_pattern()
    assert pattern.startswith(":(TMP|SPFH|UGRD|VGRD|VVEL):(")
    assert pattern.endswith(") mb:")
    assert "DPT" not in pattern
    assert r"\d" not in pattern
    # The intermediate 25-hPa levels live in pgrb2b WITHOUT SPFH, so they must
    # not appear: 21 is the usable ladder under decision §8 #7.
    assert "|875|" not in pattern


def test_ecmwf_pattern_uses_the_index_dialect() -> None:
    spec = sounding.ECMWF_SPEC
    assert spec.isobaric_search_pattern() == (
        ":(t|q|u|v|w):(1000|925|850|700|600|500|400|300|250|200|150|100):pl:"
    )
    assert spec.surface_search_pattern() == ":(sp|2t|2d|10u|10v|mucape):(?:nan:)?sfc:"
    # `r` (relative humidity) is present on all 12 levels and deliberately unused.
    assert ":r:" not in spec.isobaric_search_pattern()


@pytest.mark.parametrize("model_id", ["gfs", "ecmwf"])
def test_single_group_models_fetch_isobaric_and_surface_in_one_regex(model_id: str) -> None:
    spec = sounding.model_spec(model_id)
    group = spec.fetch_groups[0]
    pattern = spec.search_pattern_for_group(group)
    assert spec.isobaric_search_pattern() in pattern
    assert spec.surface_search_pattern() in pattern
    assert spec.planes_for_group(group) == set(range(spec.n_planes))
    # It has to compile and it has to match both halves' real idx lines.
    import re

    compiled = re.compile(pattern)
    if model_id == "gfs":
        assert compiled.search(":TMP:850 mb:")
        assert compiled.search(":SPFH:100 mb:")
        assert compiled.search(":CAPE:surface:")
        assert not compiled.search(":RH:850 mb:")
        assert not compiled.search(":CAPE:180-0 mb above ground:")
        assert not compiled.search(":TMP:875 mb:")
    else:
        assert compiled.search(":t:850:pl:od:oper:fc")
        assert compiled.search(":q:100:pl:od:oper:fc")
        # Both index renderings in this codebase must select the surface block:
        # Herbie's eccodes join keeps the empty levelist as a literal "nan",
        # the repo's JSON-lines parser drops it.
        assert compiled.search(":mucape:sfc:od:oper:fc")
        assert compiled.search(":mucape:nan:sfc:nan:g:0001:od:fc:oper:")
        assert compiled.search(":sp:nan:sfc:nan:g:0001:od:fc:oper:")
        assert compiled.search(":2t:nan:sfc:nan:g:0001:od:fc:oper:")
        assert compiled.search(":t:850:pl:nan:g:0001:od:fc:oper:")
        assert not compiled.search(":tp:nan:sfc:nan:g:0001:od:fc:oper:")
        assert not compiled.search(":r:850:pl:od:oper:fc")
        assert not compiled.search(":t:750:pl:od:oper:fc")


# ---------------------------------------------------------------------------
# Band identification
# ---------------------------------------------------------------------------


def _tags(element: str, short_name: str) -> dict[str, str]:
    return {"GRIB_ELEMENT": element, "GRIB_SHORT_NAME": short_name}


def test_band_tags_resolve_per_model_and_do_not_leak_across_models() -> None:
    # 850 hPa is level index 6 for HRRR, 5 for GFS, 2 for ECMWF; the td slot is
    # var index 1 everywhere, and plane = level_index * 5 + var_index.
    assert sounding.HRRR_SPEC.plane_index_for_band_tags(_tags("DPT", "850-ISBL")) == 6 * 5 + 1
    assert sounding.GFS_SPEC.plane_index_for_band_tags(_tags("SPFH", "850-ISBL")) == 5 * 5 + 1
    assert sounding.ECMWF_SPEC.plane_index_for_band_tags(_tags("SPFH", "850-ISBL")) == 2 * 5 + 1

    # GFS has no isobaric DPT, and 875 hPa is not on its ladder.
    assert sounding.GFS_SPEC.plane_index_for_band_tags(_tags("DPT", "850-ISBL")) is None
    assert sounding.GFS_SPEC.plane_index_for_band_tags(_tags("TMP", "875-ISBL")) is None
    # 750 hPa is on GFS's ladder but not ECMWF's.
    assert sounding.GFS_SPEC.plane_index_for_band_tags(_tags("TMP", "750-ISBL")) is not None
    assert sounding.ECMWF_SPEC.plane_index_for_band_tags(_tags("TMP", "750-ISBL")) is None
    # RH is fetched by nobody and must never claim a plane.
    for spec in sounding.MODEL_SPECS.values():
        assert spec.plane_index_for_band_tags(_tags("RH", "850-ISBL")) is None


def test_ecmwf_accepts_lowercase_element_aliases() -> None:
    # Belt and braces: GDAL decodes IFS through the WMO tables, but the spec
    # also accepts the short-name spellings in case a build sees them.
    assert sounding.ECMWF_SPEC.plane_index_for_band_tags(
        _tags("T", "500-ISBL")
    ) == sounding.ECMWF_SPEC.plane_index_for_band_tags(_tags("TMP", "500-ISBL"))


@pytest.mark.parametrize("level_token", ["0-SFC", "0-EATM", "255-UNKNOWN"])
def test_ecmwf_mucape_is_matched_across_plausible_level_tokens(level_token: str) -> None:
    assert (
        sounding.ECMWF_SPEC.plane_index_for_band_tags(_tags("CAPE", level_token))
        == sounding.ECMWF_SPEC.n_planes - 1
    )


def test_surface_pressure_maps_to_plane_zero_of_the_surface_block() -> None:
    for spec in sounding.MODEL_SPECS.values():
        assert (
            spec.plane_index_for_band_tags(_tags("PRES", "0-SFC")) == spec.n_isobaric_planes
        )


# ---------------------------------------------------------------------------
# HRRR byte-identity regression guard
# ---------------------------------------------------------------------------


def _hrrr_planes(height: int, width: int) -> dict[int, np.ndarray]:
    """Deterministic, physically plausible full-resolution planes."""
    rng = np.random.default_rng(20260802)
    spec = sounding.HRRR_SPEC
    planes: dict[int, np.ndarray] = {}
    ramp = np.linspace(0.0, 1.0, height * width, dtype=np.float32).reshape(height, width)
    for level_index, level in enumerate(spec.levels_hpa):
        base_t = -60.0 + 60.0 * (level / 1000.0)
        planes[spec.isobaric_plane_index(level_index, 0)] = base_t + 3.0 * ramp
        planes[spec.isobaric_plane_index(level_index, 1)] = base_t - 5.0 + 2.0 * ramp
        planes[spec.isobaric_plane_index(level_index, 2)] = 10.0 * ramp - 5.0
        planes[spec.isobaric_plane_index(level_index, 3)] = 4.0 - 8.0 * ramp
        planes[spec.isobaric_plane_index(level_index, 4)] = 0.5 - ramp
    surface_values = [980.0 + 20.0 * ramp, 28.0 * ramp, 18.0 * ramp, 3.0 * ramp, -2.0 * ramp, 1200.0 * ramp]
    for field_index, values in enumerate(surface_values):
        planes[spec.surface_plane_index(field_index)] = values.astype(np.float32)
    # One all-NaN plane pixel keeps the nodata path in the comparison.
    planes[0] = planes[0].copy()
    planes[0][0, 0] = np.nan
    del rng
    return planes


def _expected_hrrr_buffer(planes: dict[int, np.ndarray]) -> bytes:
    """Independent implementation of design §3's packing, for comparison.

    Deliberately written from the design document (decimate by 4, quantize with
    ``round((value - offset) / scale)`` clipped to [0, 65534], nodata 65535,
    pixel-major with plane innermost) rather than by calling the packer.
    """
    spec = sounding.HRRR_SPEC
    sample = next(iter(planes.values()))[::4, ::4]
    height, width = sample.shape
    out = np.full((height, width, spec.n_planes), 65535, dtype=np.uint16)
    for plane, values in planes.items():
        pack = spec.pack_for_plane(plane)
        decimated = np.asarray(values, dtype=np.float32)[::4, ::4]
        for row in range(height):
            for col in range(width):
                value = float(decimated[row, col])
                if not math.isfinite(value):
                    continue
                code = round((value - pack.offset) / pack.scale)
                out[row, col, plane] = int(min(max(code, 0), 65534))
    return out.tobytes()


def test_hrrr_stack_bytes_are_unchanged_by_the_registry_refactor() -> None:
    planes = _hrrr_planes(height=16, width=20)
    stack = sounding.assemble_stack(planes)
    assert stack.shape == (4, 5, 191)
    assert np.ascontiguousarray(stack).astype("<u2").tobytes() == _expected_hrrr_buffer(planes)


def test_hrrr_defaults_survive_when_a_spec_is_not_passed() -> None:
    # Every public helper still answers for HRRR without a spec argument, which
    # is what keeps Phase 1-5 callers (and the endpoint) working untouched.
    assert sounding.pack_for_plane(0) is sounding.TEMPERATURE_PACK
    assert sounding.isobaric_plane_index(0, 1) == 1
    assert sounding.surface_plane_index(0) == 185
    assert sounding.plane_index_for_band_tags(_tags("DPT", "1000-ISBL")) == 1


# ---------------------------------------------------------------------------
# Td from specific humidity (decision §8 #7)
# ---------------------------------------------------------------------------


def test_vapor_pressure_inverts_the_specific_humidity_definition() -> None:
    # q = 0.622 e / (p - 0.378 e) must round-trip through the derivation.
    p = 700.0
    for e in (0.01, 1.0, 12.5, 30.0):
        q = 0.622 * e / (p - 0.378 * e)
        assert sounding.vapor_pressure_from_q(np.array([q]), p)[0] == pytest.approx(e, rel=1e-9)


def test_dewpoint_from_q_matches_metpy_computed_independently() -> None:
    mpcalc = pytest.importorskip("metpy.calc")
    from metpy.units import units

    p = 850.0
    q = np.array([[1e-5, 1e-3, 5e-3, 1.2e-2]], dtype=np.float64)
    got = sounding.dewpoint_from_q(q, p)

    e = q * p / (0.622 + 0.378 * q)
    expected = mpcalc.dewpoint(e * units.hPa).to(units.degC).magnitude
    np.testing.assert_allclose(got, expected, atol=1e-4)


def test_dewpoint_from_q_matches_metpys_own_specific_humidity_helper() -> None:
    mpcalc = pytest.importorskip("metpy.calc")
    from metpy.units import units

    helper = getattr(mpcalc, "dewpoint_from_specific_humidity", None)
    if helper is None:  # pragma: no cover - older MetPy
        pytest.skip("MetPy has no dewpoint_from_specific_humidity")

    p = 700.0
    q = np.array([2e-4, 2e-3, 8e-3], dtype=np.float64)
    got = sounding.dewpoint_from_q(q, p)
    try:
        expected = helper(p * units.hPa, q * units("kg/kg")).to(units.degC).magnitude
    except TypeError:  # pragma: no cover - signature variant with temperature
        expected = helper(
            p * units.hPa, np.array([10.0, 10.0, 10.0]) * units.degC, q * units("kg/kg")
        ).to(units.degC).magnitude
    # MetPy's helper goes via relative humidity, so it is the *same* physics but
    # not the same arithmetic; 0.1 degC is far below the 0.05 degC quantization
    # this feeds and far below the ~11 degC RH/ice trap the rule exists to avoid.
    np.testing.assert_allclose(got, expected, atol=0.1)


def test_dewpoint_from_q_is_monotonic_and_never_infinite() -> None:
    q = np.array([1e-9, 1e-7, 1e-5, 1e-3, 1e-2], dtype=np.float64)
    td = sounding.dewpoint_from_q(q, 500.0)
    assert np.all(np.isfinite(td))
    assert np.all(np.diff(td) > 0.0)
    # Never below the packing floor, so an absurdly dry level clips rather than
    # diverging to -inf and poisoning the plane's range check.
    assert td[0] >= -120.0
    assert sounding.dewpoint_from_q(np.array([1e-30]), 500.0)[0] == pytest.approx(-120.0)


def test_dewpoint_from_q_treats_missing_and_nonpositive_q_as_nodata() -> None:
    td = sounding.dewpoint_from_q(np.array([np.nan, 0.0, -1e-6, 5e-3]), 900.0)
    assert np.isnan(td[0]) and np.isnan(td[1]) and np.isnan(td[2])
    assert np.isfinite(td[3])


def test_dewpoint_stays_below_temperature_on_a_realistic_column() -> None:
    spec = sounding.ECMWF_SPEC
    # Roughly a moist tropical sounding: q falling from 18 g/kg to 2 mg/kg.
    q_by_level = np.geomspace(1.8e-2, 2e-6, len(spec.levels_hpa))
    t_by_level = np.linspace(28.0, -75.0, len(spec.levels_hpa))
    for level, q, t in zip(spec.levels_hpa, q_by_level, t_by_level):
        td = float(sounding.dewpoint_from_q(np.array([q]), float(level))[0])
        assert td <= t + 1e-6, f"Td {td} exceeded T {t} at {level} hPa"


def test_derive_td_from_q_planes_uses_each_levels_own_pressure() -> None:
    spec = sounding.GFS_SPEC
    q_value = 4e-3
    planes = {
        spec.isobaric_plane_index(index, 1): np.full((2, 2), q_value, dtype=np.float32)
        for index in range(len(spec.levels_hpa))
    }
    # A non-td plane must be returned untouched.
    planes[spec.isobaric_plane_index(0, 0)] = np.full((2, 2), 21.0, dtype=np.float32)

    sounding.derive_td_from_q_planes(planes, spec)

    assert planes[spec.isobaric_plane_index(0, 0)][0, 0] == pytest.approx(21.0)
    for index, level in enumerate(spec.levels_hpa):
        got = float(planes[spec.isobaric_plane_index(index, 1)][0, 0])
        expected = float(sounding.dewpoint_from_q(np.array([q_value]), float(level))[0])
        assert got == pytest.approx(expected, abs=1e-4)
    # Same q at lower pressure is a lower vapour pressure, hence a lower Td.
    first = float(planes[spec.isobaric_plane_index(0, 1)][0, 0])
    last = float(planes[spec.isobaric_plane_index(len(spec.levels_hpa) - 1, 1)][0, 0])
    assert last < first


def test_derive_td_from_q_planes_is_a_no_op_for_a_native_dpt_model() -> None:
    spec = sounding.HRRR_SPEC
    planes = {spec.isobaric_plane_index(0, 1): np.full((2, 2), 12.5, dtype=np.float32)}
    sounding.derive_td_from_q_planes(planes, spec)
    assert planes[spec.isobaric_plane_index(0, 1)][0, 0] == pytest.approx(12.5)


# ---------------------------------------------------------------------------
# Assembly + sidecar for the new models
# ---------------------------------------------------------------------------


def _full_planes(spec: sounding.SoundingModelSpec, *, height: int, width: int) -> dict:
    planes: dict[int, np.ndarray] = {}
    ramp = np.linspace(0.0, 1.0, height * width, dtype=np.float32).reshape(height, width)
    for level_index, level in enumerate(spec.levels_hpa):
        base_t = -60.0 + 60.0 * (level / 1000.0)
        planes[spec.isobaric_plane_index(level_index, 0)] = base_t + 2.0 * ramp
        planes[spec.isobaric_plane_index(level_index, 1)] = base_t - 6.0 + ramp
        planes[spec.isobaric_plane_index(level_index, 2)] = 8.0 * ramp
        planes[spec.isobaric_plane_index(level_index, 3)] = -8.0 * ramp
        planes[spec.isobaric_plane_index(level_index, 4)] = 0.2 - 0.4 * ramp
    for field_index, values in enumerate(
        [970.0 + 30.0 * ramp, 25.0 * ramp, 15.0 * ramp, 5.0 * ramp, -5.0 * ramp, 900.0 * ramp]
    ):
        planes[spec.surface_plane_index(field_index)] = values.astype(np.float32)
    return planes


@pytest.mark.parametrize("model_id,expected_planes", [("gfs", 111), ("ecmwf", 66)])
def test_stacks_have_the_specs_plane_count_and_global_stride(
    model_id: str, expected_planes: int
) -> None:
    spec = sounding.model_spec(model_id)
    stack = sounding.assemble_stack(_full_planes(spec, height=8, width=10), spec=spec)
    # Global models decimate by 2, not HRRR's 4.
    assert stack.shape == (4, 5, expected_planes)


def test_ecmwf_assembles_without_the_optional_mucape_plane() -> None:
    spec = sounding.ECMWF_SPEC
    planes = _full_planes(spec, height=4, width=4)
    del planes[spec.n_planes - 1]
    stack = sounding.assemble_stack(planes, spec=spec)
    assert stack.shape[2] == spec.n_planes
    assert np.all(stack[:, :, spec.n_planes - 1] == sounding.NODATA_CODE)


def test_a_missing_required_plane_still_fails_the_build() -> None:
    spec = sounding.ECMWF_SPEC
    planes = _full_planes(spec, height=4, width=4)
    del planes[spec.isobaric_plane_index(3, 2)]
    with pytest.raises(ValueError, match="missing 1 plane"):
        sounding.assemble_stack(planes, spec=spec)


@pytest.mark.parametrize("model_id", ["hrrr", "gfs", "ecmwf"])
def test_sidecar_is_self_describing_per_model(model_id: str) -> None:
    spec = sounding.model_spec(model_id)
    sidecar = sounding.build_sidecar(
        model=model_id,
        run_id=RUN_ID,
        fh=12,
        run_date=RUN_DATE,
        width=10,
        height=8,
        transform=Affine(0.5, 0.0, -0.125, 0.0, -0.5, 90.125),
        projection="EPSG:4326",
        source_width=10 * spec.stride,
        source_height=8 * spec.stride,
    )
    assert sidecar["levels_hPa"] == list(spec.levels_hpa)
    assert sidecar["n_planes"] == spec.n_planes
    assert sidecar["n_isobaric_planes"] == spec.n_isobaric_planes
    assert sidecar["bytes_per_pixel"] == spec.bytes_per_pixel
    assert sidecar["decimation_stride"] == spec.stride
    assert sidecar["td_policy"] == spec.td_policy
    assert [entry["id"] for entry in sidecar["variables"]] == ["t", "td", "u", "v", "w"]
    cape_entry = sidecar["surface_fields"][-1]
    assert cape_entry["id"] == "cape_sfc"
    assert cape_entry["display_label"] == spec.surface_fields[-1].display_label


def test_a_written_stack_round_trips_through_the_sidecar_driven_reader(tmp_path: Path) -> None:
    spec = sounding.ECMWF_SPEC
    planes = _full_planes(spec, height=4, width=6)
    stack = sounding.assemble_stack(planes, spec=spec)
    sidecar = sounding.build_sidecar(
        model="ecmwf",
        run_id=RUN_ID,
        fh=0,
        run_date=RUN_DATE,
        width=stack.shape[1],
        height=stack.shape[0],
        transform=Affine(0.5, 0.0, -0.125, 0.0, -0.5, 90.125),
        projection="EPSG:4326",
        source_width=6,
        source_height=4,
    )
    stack_path, _ = sounding.write_stack(
        run_root=tmp_path, fh=0, stack=stack, sidecar=sidecar
    )
    assert stack_path.stat().st_size == stack.shape[0] * stack.shape[1] * 66 * 2

    profile = sounding.read_profile(stack_path, sidecar, row=1, col=2)
    assert profile["levels_hPa"] == list(spec.levels_hpa)
    assert len(profile["t"]) == 12
    assert len(profile["w"]) == 12
    assert set(profile["surface"]) == {
        "pres_sfc", "t2m", "td2m", "u10m", "v10m", "cape_sfc"
    }


# ---------------------------------------------------------------------------
# Global-grid longitude wrap (design §10 work item 5)
# ---------------------------------------------------------------------------

# GFS/ECMWF after the x2 decimation: 0.5 deg cells, corner-referenced origin at
# (-0.125, 90.125), 720 x 361 — the real published geometry.
GLOBAL_WIDTH = 720
GLOBAL_HEIGHT = 361
GLOBAL_TRANSFORM = Affine(0.5, 0.0, -0.125, 0.0, -0.5, 90.125)


def _global_sidecar(projection: str = "EPSG:4326") -> dict:
    return {
        "width": GLOBAL_WIDTH,
        "height": GLOBAL_HEIGHT,
        "transform": list(tuple(GLOBAL_TRANSFORM)[:6]),
        "projection": projection,
    }


@pytest.mark.parametrize(
    "lon,expected_col",
    [
        (0.0, 0),            # Greenwich sits in column 0
        (-0.05, 0),          # inside the wrap gap: folds forward to column 0
        (359.95, 0),         # the same point expressed in the grid's own frame
        (-0.3, 719),         # just west of the gap: the LAST column
        (359.7, 719),
        (0.5, 1),
        (-97.5, 525),        # a normal CONUS request: 262.5 deg east
        (262.5, 525),
        (179.9, 360),        # antimeridian, from the east
        (-179.9, 360),       # ... and from the west: the same column
        (180.0, 360),
    ],
)
def test_global_longitudes_fold_into_the_grid_frame(lon: float, expected_col: int) -> None:
    point = sounding_api.locate_grid_point(_global_sidecar(), lat=38.5, lon=lon)
    assert point["col"] == expected_col


def test_the_antimeridian_is_continuous() -> None:
    # +180 and -180 are the same meridian and must not be a seam: the cell that
    # straddles it is reached identically from either side.
    west = sounding_api.locate_grid_point(_global_sidecar(), lat=0.0, lon=-179.95)
    east = sounding_api.locate_grid_point(_global_sidecar(), lat=0.0, lon=179.95)
    assert west["col"] == east["col"] == 360
    far_west = sounding_api.locate_grid_point(_global_sidecar(), lat=0.0, lon=-179.5)
    assert far_west["col"] == 361


def test_greenwich_neighbours_wrap_around_the_seam() -> None:
    # The seam the wrap exists for: two points 0.5 deg apart must land in
    # adjacent columns even though one of them is column 0 and the other 719.
    east = sounding_api.locate_grid_point(_global_sidecar(), lat=0.0, lon=0.1)
    west = sounding_api.locate_grid_point(_global_sidecar(), lat=0.0, lon=-0.4)
    assert (east["col"], west["col"]) == (0, 719)


def test_snapped_longitude_is_reported_in_the_callers_convention() -> None:
    point = sounding_api.locate_grid_point(_global_sidecar(), lat=38.5, lon=-97.4)
    assert -180.0 <= point["lon"] <= 180.0
    assert point["lon"] == pytest.approx(-97.375, abs=1e-6)
    # Under a 0.5 deg cell's half-diagonal (~39 km). The residual is the
    # decimated affine's half-cell corner convention, which predates Phase 6 and
    # is identical for HRRR; "nearest gridpoint" reports the snap honestly
    # (decision #4).
    assert point["distance_km"] < 39.0


def test_poles_are_inside_a_global_grid() -> None:
    north = sounding_api.locate_grid_point(_global_sidecar(), lat=90.0, lon=10.0)
    south = sounding_api.locate_grid_point(_global_sidecar(), lat=-90.0, lon=10.0)
    assert north["row"] == 0
    assert south["row"] == GLOBAL_HEIGHT - 1


def test_out_of_range_latitude_is_still_a_request_error() -> None:
    # Wrapping is longitude-only: latitude has no seam and must stay bounded.
    with pytest.raises(sounding_api.SoundingRequestError):
        sounding_api.locate_grid_point(_global_sidecar(), lat=91.0, lon=10.0)


def test_the_wrap_also_fires_for_a_wkt_crs_not_only_the_epsg_string() -> None:
    # Real sidecars store ``crs.to_wkt()``, not "EPSG:4326" — the geographic
    # check has to parse it, or the wrap silently never engages on prod.
    wkt = CRS.from_epsg(4326).to_wkt()
    assert sounding_api._sounding_is_geographic(wkt) is True
    point = sounding_api.locate_grid_point(_global_sidecar(wkt), lat=38.5, lon=-97.5)
    assert point["col"] == 525


def test_a_projected_grid_is_never_wrapped() -> None:
    # HRRR's LCC: a lon far outside the domain must remain an error, not fold
    # into some arbitrary column.
    lcc = CRS.from_proj4(
        "+proj=lcc +lat_0=38.5 +lon_0=-97.5 +lat_1=38.5 +lat_2=38.5 "
        "+x_0=0 +y_0=0 +R=6371229 +units=m +no_defs"
    ).to_wkt()
    sidecar = {
        "width": 20,
        "height": 16,
        "transform": list(tuple(Affine(12000.0, 0.0, -120000.0, 0.0, -12000.0, 96000.0))[:6]),
        "projection": lcc,
    }
    assert sounding_api._sounding_is_geographic(lcc) is False
    with pytest.raises(sounding_api.SoundingRequestError):
        sounding_api.locate_grid_point(sidecar, lat=38.5, lon=20.0)


def test_a_regional_lat_lon_grid_is_not_treated_as_global() -> None:
    # Geographic but only 30 deg wide: folding would be wrong, so it must not
    # happen — an out-of-domain longitude stays an error.
    sidecar = {
        "width": 60,
        "height": 40,
        "transform": list(tuple(Affine(0.5, 0.0, -110.0, 0.0, -0.5, 50.0))[:6]),
        "projection": "EPSG:4326",
    }
    with pytest.raises(sounding_api.SoundingRequestError):
        sounding_api.locate_grid_point(sidecar, lat=40.0, lon=20.0)


# ---------------------------------------------------------------------------
# Indices on a 12-level ladder (design §10 work item 7)
# ---------------------------------------------------------------------------


def _ecmwf_column() -> dict:
    """A convectively unstable 12-level ECMWF-shaped column."""
    levels = list(sounding.ECMWF_SPEC.levels_hpa)
    # ~7.5 K/km through the troposphere via a simple pressure-height proxy.
    t = [28.0, 21.0, 14.5, 1.0, -7.5, -18.0, -31.0, -47.0, -55.0, -58.0, -62.0, -66.0]
    td = [23.0, 17.0, 10.5, -6.0, -17.0, -29.0, -44.0, -60.0, -68.0, -72.0, -76.0, -80.0]
    u = [2.0 + 1.4 * i for i in range(len(levels))]
    v = [-3.0 + 0.9 * i for i in range(len(levels))]
    return {
        "levels_hpa": levels,
        "t": t,
        "td": td,
        "u": u,
        "v": v,
        "surface": {
            "pres_sfc": 1002.0,
            "t2m": 29.5,
            "td2m": 23.5,
            "u10m": 1.5,
            "v10m": -3.5,
            "cape_sfc": 1750.0,
        },
    }


def test_indices_are_computed_on_a_twelve_level_ladder() -> None:
    pytest.importorskip("metpy.calc")
    result = sounding_indices.compute_frame_indices(**_ecmwf_column())
    indices = result["indices"]
    assert indices is not None
    # The ML parcel is a 100 hPa mixed layer; on this ladder that is the
    # 1000/925 pair, so it is coarse but well defined.
    assert indices["sbcape"] is not None and indices["sbcape"] > 0
    assert indices["mlcape"] is not None
    assert indices["lcl_hPa"] is not None
    assert indices["pwat_mm"] is not None
    # The diagnostic passes through under its stable field name even though the
    # ECMWF quantity behind it is most-unstable CAPE.
    assert indices["model_sbcape"] == 1750


def test_twelve_level_parcel_and_profiles_stay_level_aligned() -> None:
    pytest.importorskip("metpy.calc")
    column = _ecmwf_column()
    result = sounding_indices.compute_frame_indices(**column)
    parcel = result["parcel"]
    profiles = result["profiles"]
    assert parcel is not None
    # The parcel path is the ANCHORED column (surface points prepended), so it
    # is intentionally longer than the ladder; p and t must still agree.
    assert len(parcel["p"]) == len(parcel["t"]) >= len(column["levels_hpa"])
    assert profiles is not None
    for key in ("tw", "theta_e", "height_m_agl"):
        assert len(profiles[key]) == len(column["levels_hpa"])
    # Heights increase upward and the lowest level is near the ground.
    heights = [h for h in profiles["height_m_agl"] if h is not None]
    assert heights == sorted(heights)


def test_a_high_terrain_ecmwf_column_degrades_to_nulls_rather_than_lying() -> None:
    pytest.importorskip("metpy.calc")
    column = _ecmwf_column()
    # Tibetan-plateau surface pressure masks 1000/925/850/700/600 hPa, leaving
    # 7 usable levels — below MIN_COLUMN_LEVELS. Coarse ladders genuinely run
    # out of column at altitude; the contract is nulls, never a fabricated value.
    column["surface"] = {**column["surface"], "pres_sfc": 560.0, "t2m": -2.0, "td2m": -12.0}
    result = sounding_indices.compute_frame_indices(**column)
    assert result["indices"] is None or result["indices"]["sbcape"] is None


def test_parcel_definition_names_the_model() -> None:
    assert sounding_indices.parcel_definition_for_model("gfs").startswith("SB parcel: GFS")
    assert sounding_indices.parcel_definition_for_model("ecmwf").startswith("SB parcel: ECMWF")
    assert sounding_indices.parcel_definition_for_model("hrrr") == sounding_indices.PARCEL_DEFINITION
    assert "model 2 m" in sounding_indices.parcel_definition_for_model("nam")


# ---------------------------------------------------------------------------
# Fetch source preference
# ---------------------------------------------------------------------------


def test_hrrr_and_gfs_use_the_shared_source_priority() -> None:
    # Empty means "whatever CARTOSKY_HERBIE_PRIORITY says" — unchanged
    # behaviour, and the measured AWS-first path is right for both.
    assert sounding.HRRR_SPEC.source_priority == ()
    assert sounding.GFS_SPEC.source_priority == ()


def test_ecmwf_prefers_google_over_the_eu_central_bucket() -> None:
    # Herbie's IFS `aws` source is eu-central-1 (1.49 MB/s measured from prod);
    # `google` is multi-region GCS. The fallback tail is exactly the raster
    # plugin's existing order, so a Google outage degrades to today's behaviour.
    assert sounding.ECMWF_SPEC.source_priority == ("google", "azure", "aws", "ecmwf")


def test_the_spec_priority_is_what_the_fetch_layer_is_handed(monkeypatch) -> None:
    seen: list[str] = []

    class _Boom(RuntimeError):
        pass

    def _construct(*_args, **_kwargs):
        seen.append(str(_kwargs.get("priority") or _args[-1]))
        raise _Boom("no network in tests")

    from app.services.builder import fetch as fetch_module

    monkeypatch.setattr(fetch_module, "_import_herbie", lambda: object())
    monkeypatch.setattr(
        fetch_module,
        "_construct_herbie",
        lambda _type, _date, kwargs: _construct(priority=kwargs.get("priority")),
    )

    with pytest.raises(RuntimeError, match="exhausted all sources"):
        sounding._fetch_product_planes(
            model_id="ifs",
            product="oper",
            search_pattern=":t:850:pl:",
            run_date=RUN_DATE,
            fh=0,
            wanted_planes={0},
            spec=sounding.ECMWF_SPEC,
        )
    assert seen == ["google", "azure", "aws", "ecmwf"]
