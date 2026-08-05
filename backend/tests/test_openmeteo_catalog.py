"""Catalog / units-audit / cadence tests for the Open-Meteo fast path (WP1).

The units assertions are deliberately cross-checked against the *production*
tables (``ECMWF_VARS``, ``ECMWF_CONVERSION_BY_VAR_KEY``,
``_PACKING_BY_MODEL_VAR``) rather than against restated constants: the fast
path's whole contract is that it hands the packer the same value the delayed
path would, so a production units change must fail here.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.models.ecmwf import (
    ECMWF_CONVERSION_BY_VAR_KEY,
    ECMWF_VARS,
)
from app.services.builder.fetch import UNIT_CONVERTERS
from app.services.grid import _PACKING_BY_MODEL_VAR
from app.services.sources.openmeteo import catalog as om_catalog
from app.services.sources.openmeteo.catalog import (
    ATTRIBUTION_ECMWF_IFS,
    ECMWF_IFS_CATALOG,
    GridKind,
    OmConversion,
    VariableKind,
    aggregation_boundaries,
    catalog_for,
    expected_file_count,
    expected_fhs,
)

#: The ten variables the prototype validated (design Decision 4).
VALIDATED_TEN = (
    "temperature_2m",
    "dew_point_2m",
    "precipitation",
    "showers",
    "snowfall_water_equivalent",
    "snow_depth",
    "pressure_msl",
    "wind_gusts_10m",
    "wind_u_component_10m",
    "wind_v_component_10m",
)


# ---------------------------------------------------------------------------
# Shape of the catalog
# ---------------------------------------------------------------------------


def test_catalog_covers_exactly_the_validated_ten() -> None:
    assert {var.om_name for var in ECMWF_IFS_CATALOG.variables} == set(VALIDATED_TEN)


def test_catalog_identity_and_registry() -> None:
    assert ECMWF_IFS_CATALOG.model_id == "ecmwf"
    assert ECMWF_IFS_CATALOG.bucket_dir == "ecmwf_ifs"
    assert ECMWF_IFS_CATALOG.grid is GridKind.O1280
    assert ECMWF_IFS_CATALOG.source_points == 6_599_680
    assert catalog_for("ECMWF ") is ECMWF_IFS_CATALOG
    with pytest.raises(KeyError):
        catalog_for("icon")


def test_attribution_is_the_launch_gate_string() -> None:
    assert ATTRIBUTION_ECMWF_IFS == "ECMWF IFS data © ECMWF, via Open-Meteo (CC-BY-4.0)"
    assert ECMWF_IFS_CATALOG.attribution == ATTRIBUTION_ECMWF_IFS


def test_disabled_variables_are_exactly_the_two_without_counterparts() -> None:
    disabled = {var.om_name for var in ECMWF_IFS_CATALOG.variables if not var.enabled}

    assert disabled == {"showers", "snow_depth"}
    for name in disabled:
        variable = ECMWF_IFS_CATALOG.variable(name)
        assert variable.cartosky_var is None
        assert variable.note  # every disabled entry documents why


def test_enabled_variables_all_map_to_a_real_ecmwf_variable() -> None:
    for variable in ECMWF_IFS_CATALOG.enabled_variables():
        assert variable.cartosky_var in ECMWF_VARS, variable.om_name


def test_component_only_variables_are_not_published_artifacts() -> None:
    components = {
        var.cartosky_var for var in ECMWF_IFS_CATALOG.variables if var.component_only
    }

    assert components == {"msl", "10u", "10v"}
    for var_key in components:
        # Components have no packing entry — they feed derives/overlays only.
        assert ("ecmwf", var_key) not in _PACKING_BY_MODEL_VAR

    publishable = {var.cartosky_var for var in ECMWF_IFS_CATALOG.publishable_variables()}
    assert publishable == {"tmp2m", "dp2m", "precip_total", "snowfall_total", "wgst10m"}


def test_accumulation_kind_matches_the_per_step_prototype_finding() -> None:
    accumulating = {var.om_name for var in ECMWF_IFS_CATALOG.accumulation_variables()}

    assert accumulating == {"precipitation", "snowfall_water_equivalent"}
    for name in accumulating:
        assert ECMWF_IFS_CATALOG.variable(name).om_unit.endswith("/step")


# ---------------------------------------------------------------------------
# Units audit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("om_name", "cartosky_var", "om_unit", "production_unit"),
    [
        ("temperature_2m", "tmp2m", "degC", "F"),
        ("dew_point_2m", "dp2m", "degC", "F"),
        ("precipitation", "precip_total", "mm/step", "in"),
        ("snowfall_water_equivalent", "snowfall_total", "mm SWE/step", "in"),
        ("pressure_msl", "msl", "Pa", "Pa"),
        ("wind_gusts_10m", "wgst10m", "m/s", "mph"),
        ("wind_u_component_10m", "10u", "m/s", "m/s"),
        ("wind_v_component_10m", "10v", "m/s", "m/s"),
    ],
)
def test_units_audit_rows(
    om_name: str, cartosky_var: str, om_unit: str, production_unit: str
) -> None:
    variable = ECMWF_IFS_CATALOG.variable(om_name)

    assert variable.enabled
    assert variable.cartosky_var == cartosky_var
    assert variable.om_unit == om_unit
    assert variable.production_unit == production_unit


def test_published_production_units_match_the_packing_registry() -> None:
    """The audit's "production stored unit" is what the packer declares."""
    for variable in ECMWF_IFS_CATALOG.publishable_variables():
        packing = _PACKING_BY_MODEL_VAR[("ecmwf", variable.cartosky_var)]
        assert packing["units"] == variable.production_unit, variable.om_name


def test_component_production_units_match_the_var_spec_declarations() -> None:
    # msl declares Pa (converted to hPa only at contour time); 10u/10v are bare
    # component specs with no units, i.e. native m/s.
    assert ECMWF_VARS["msl"].units == "Pa"
    assert ECMWF_IFS_CATALOG.variable("pressure_msl").production_unit == "Pa"
    for var_key in ("10u", "10v"):
        assert getattr(ECMWF_VARS[var_key], "units", None) in (None, "", "m/s")


def test_temperature_conversion_matches_production_c_to_f() -> None:
    variable = ECMWF_IFS_CATALOG.variable("temperature_2m")
    values = np.array([-40.0, 0.0, 22.45, 100.0], dtype=np.float32)

    assert variable.conversion is OmConversion.C_TO_F
    assert ECMWF_CONVERSION_BY_VAR_KEY["tmp2m"] == "c_to_f"
    assert np.allclose(variable.convert(values), UNIT_CONVERTERS["c_to_f"](values))
    assert np.allclose(variable.convert(np.float32(0.0)), 32.0)


def test_dewpoint_conversion_matches_production_c_to_f() -> None:
    variable = ECMWF_IFS_CATALOG.variable("dew_point_2m")
    values = np.array([-10.0, 19.6], dtype=np.float32)

    assert ECMWF_CONVERSION_BY_VAR_KEY["dp2m"] == "c_to_f"
    assert np.allclose(variable.convert(values), UNIT_CONVERTERS["c_to_f"](values))


def test_precip_mm_conversion_equals_production_metres_conversion() -> None:
    """production: metres → inches. fast path: mm → inches. Same destination."""
    variable = ECMWF_IFS_CATALOG.variable("precipitation")
    millimetres = np.array([0.0, 1.0, 25.4, 137.3], dtype=np.float64)

    assert ECMWF_CONVERSION_BY_VAR_KEY["precip_total"] == "m_to_in"
    production = UNIT_CONVERTERS["m_to_in"](millimetres / 1000.0)

    assert np.allclose(variable.convert(millimetres), production, rtol=1e-9)
    assert variable.convert(np.array([25.4]))[0] == pytest.approx(1.0, rel=1e-9)


def test_snowfall_mm_swe_conversion_equals_production_metres_swe_conversion() -> None:
    variable = ECMWF_IFS_CATALOG.variable("snowfall_water_equivalent")
    millimetres = np.array([0.0, 2.54, 10.0], dtype=np.float64)

    assert ECMWF_CONVERSION_BY_VAR_KEY["snowfall_total"] == "m_swe_to_in_10to1"
    production = UNIT_CONVERTERS["m_swe_to_in_10to1"](millimetres / 1000.0)

    assert np.allclose(variable.convert(millimetres), production, rtol=1e-9)
    # 2.54 mm SWE == 0.1 in liquid == 1.0 in of 10:1 snow.
    assert variable.convert(np.array([2.54]))[0] == pytest.approx(1.0, rel=1e-9)


def test_gust_conversion_matches_production_ms_to_mph() -> None:
    variable = ECMWF_IFS_CATALOG.variable("wind_gusts_10m")
    values = np.array([0.0, 5.3, 30.0], dtype=np.float64)

    assert ECMWF_CONVERSION_BY_VAR_KEY["wgst10m"] == "ms_to_mph"
    assert np.allclose(variable.convert(values), UNIT_CONVERTERS["ms_to_mph"](values))


def test_component_variables_are_stored_unconverted() -> None:
    values = np.array([101560.0, 0.3, 2.55], dtype=np.float64)

    for om_name in ("pressure_msl", "wind_u_component_10m", "wind_v_component_10m"):
        variable = ECMWF_IFS_CATALOG.variable(om_name)
        assert variable.conversion is OmConversion.NONE
        assert variable.converter is None
        assert np.array_equal(variable.convert(values), values)


def test_wind_components_feed_the_existing_wspd10m_derive() -> None:
    """u/v stay m/s because the derive's own conversion does the mph step."""
    assert ECMWF_VARS["wspd10m"].derive == "wspd10m"
    assert ECMWF_CONVERSION_BY_VAR_KEY["wspd10m"] == "ms_to_mph"
    assert _PACKING_BY_MODEL_VAR[("ecmwf", "wspd10m")]["units"] == "mph"
    for om_name in ("wind_u_component_10m", "wind_v_component_10m"):
        assert ECMWF_IFS_CATALOG.variable(om_name).component_only


def test_every_conversion_id_has_a_registered_callable() -> None:
    for conversion in OmConversion:
        assert conversion in om_catalog.OM_UNIT_CONVERTERS
    assert om_catalog.OM_UNIT_CONVERTERS[OmConversion.NONE] is None


# ---------------------------------------------------------------------------
# Cadence ladders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("cycle_hour", "count"), [(0, 145), (6, 109), (12, 145), (18, 109)])
def test_expected_file_counts_per_cycle(cycle_hour: int, count: int) -> None:
    assert expected_file_count(ECMWF_IFS_CATALOG, cycle_hour) == count


@pytest.mark.parametrize(("cycle_hour", "horizon"), [(0, 360), (6, 144), (12, 360), (18, 144)])
def test_horizons_per_cycle(cycle_hour: int, horizon: int) -> None:
    fhs = expected_fhs(ECMWF_IFS_CATALOG, cycle_hour)

    assert ECMWF_IFS_CATALOG.horizon(cycle_hour) == horizon
    assert fhs[0] == 0
    assert fhs[-1] == horizon
    assert list(fhs) == sorted(set(fhs))


def test_source_ladder_step_lengths_and_transitions() -> None:
    fhs = expected_fhs(ECMWF_IFS_CATALOG, 0)
    steps = np.diff(np.array(fhs))

    assert set(steps[: 90 - 0].tolist()) == {1}
    assert fhs[90] == 90
    assert set(steps[90:108].tolist()) == {3}  # FH93 .. FH144
    assert fhs[108] == 144
    assert set(steps[108:].tolist()) == {6}  # FH150 .. FH360
    assert len(fhs) == 91 + 18 + 36 == 145


def test_unknown_cycle_hour_is_rejected() -> None:
    with pytest.raises(KeyError, match="no cadence ladder"):
        expected_fhs(ECMWF_IFS_CATALOG, 3)


# ---------------------------------------------------------------------------
# Aggregation boundaries
# ---------------------------------------------------------------------------


def test_aggregation_windows_partition_every_source_step_for_00z() -> None:
    windows = aggregation_boundaries(ECMWF_IFS_CATALOG, 0)
    source = [fh for fh in expected_fhs(ECMWF_IFS_CATALOG, 0) if fh > 0]

    covered: list[int] = []
    for window in windows:
        covered.extend(window.source_fhs)

    assert covered == sorted(covered)  # ordered
    assert covered == source  # no gaps, no overlaps, FH000 excluded
    assert len(covered) == len(set(covered))
    assert covered[0] == 1 and covered[-1] == 360


def test_aggregation_windows_match_the_production_3h_then_6h_ladder() -> None:
    windows = aggregation_boundaries(ECMWF_IFS_CATALOG, 0)
    targets = [window.target_fh for window in windows]

    assert targets == list(range(3, 145, 3)) + list(range(150, 361, 6))
    assert len(windows) == 48 + 36 == 84
    for window in windows:
        assert window.step_hours == (3 if window.target_fh <= 144 else 6)
        assert window.source_fhs[-1] == window.target_fh


def test_aggregation_respects_the_fh90_and_fh144_transitions() -> None:
    by_target = {w.target_fh: w for w in aggregation_boundaries(ECMWF_IFS_CATALOG, 0)}

    # Hourly regime: three .om steps sum into one production frame.
    assert by_target[3].source_fhs == (1, 2, 3)
    assert by_target[90].source_fhs == (88, 89, 90)
    # FH90 transition: the .om cadence becomes 3 h and the ladders coincide.
    assert by_target[93].source_fhs == (93,)
    assert by_target[144].source_fhs == (144,)
    # FH144 transition: both ladders step to 6 h.
    assert by_target[150].source_fhs == (150,)
    assert by_target[360].source_fhs == (360,)


def test_aggregation_truncates_at_the_short_cycle_horizon() -> None:
    windows = aggregation_boundaries(ECMWF_IFS_CATALOG, 18)

    assert windows[-1].target_fh == 144
    assert len(windows) == 48
    covered = [fh for window in windows for fh in window.source_fhs]
    assert covered == [fh for fh in expected_fhs(ECMWF_IFS_CATALOG, 18) if fh > 0]


def test_aggregation_rejects_a_target_ladder_the_source_cannot_serve() -> None:
    from dataclasses import replace

    from app.services.sources.openmeteo.catalog import CadenceSegment

    # Hourly production frames past FH90, where the .om ladder is already 3 h:
    # FH091 has no source step at all.
    broken = replace(
        ECMWF_IFS_CATALOG, target_cadence=(CadenceSegment(step_hours=1, through_fh=144),)
    )

    with pytest.raises(ValueError, match="cadence ladders have diverged"):
        aggregation_boundaries(broken, 6)


def test_variable_lookup_error_names_the_variable() -> None:
    with pytest.raises(KeyError, match="cloud_cover"):
        ECMWF_IFS_CATALOG.variable("cloud_cover")
