from __future__ import annotations

from pathlib import Path

from .base import (
    BaseModelPlugin,
    ModelCapabilities,
    RegionSpec,
    VarSelectors,
    VarSpec,
    VariableCapability,
)


class CurrentAnalysisPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return []

    def normalize_var_id(self, var_id: str) -> str:
        normalized = str(var_id or "").strip().lower()
        aliases: dict[str, str] = {
            "tmp2m": "tmp2m",
            "temperature": "tmp2m",
            "t2m": "tmp2m",
            "dp2m": "dp2m",
            "td2m": "dp2m",
            "dewpoint": "dp2m",
            "dew_point": "dp2m",
            "dewpoint2m": "dp2m",
            "wspd10m": "wspd10m",
            "wind_speed": "wspd10m",
            "wind10m": "wspd10m",
            "wgst10m": "wgst10m",
            "wind_gust": "wgst10m",
            "gust10m": "wgst10m",
            "spres": "spres",
            "surface_pressure": "spres",
            "pressure": "spres",
            "sfc_pressure": "spres",
            "mslp": "spres",
            "prmsl": "spres",
            "slp": "spres",
        }
        return aliases.get(normalized, normalized)

    def select_dataarray(self, ds: object, var_id: str) -> object:
        del ds, var_id
        raise NotImplementedError("select_dataarray is not used in the Current Analysis publish path")

    def ensure_latest_cycles(self, keep_cycles: int, *, cache_dir: Path | None = None) -> dict[str, int]:
        del keep_cycles, cache_dir
        raise NotImplementedError("ensure_latest_cycles is not used in the Current Analysis publish path")


CURRENT_ANALYSIS_MODEL_ID = "current_analysis"
CURRENT_ANALYSIS_REGION_ID = "conus"


CURRENT_ANALYSIS_REGIONS: dict[str, RegionSpec] = {
    CURRENT_ANALYSIS_REGION_ID: RegionSpec(
        id=CURRENT_ANALYSIS_REGION_ID,
        name="CONUS",
        bbox_wgs84=(-134.0, 24.0, -60.0, 55.0),
        clip=True,
    ),
}


CURRENT_ANALYSIS_VARS: dict[str, VarSpec] = {
    "tmp2m": VarSpec(
        id="tmp2m",
        name="Temperature",
        selectors=VarSelectors(
            search=[":TMP:2 m above ground:"],
            hints={
                "source_family": "rtma_ru",
                "upstream_var": "TMP",
                "upstream_level": "2 m above ground",
                "preferred_units": "F",
            }
        ),
        primary=True,
        kind="continuous",
        units="F",
    ),
    "dp2m": VarSpec(
        id="dp2m",
        name="Dewpoint",
        selectors=VarSelectors(
            search=[":DPT:2 m above ground:"],
            hints={
                "source_family": "rtma_ru",
                "upstream_var": "DPT",
                "upstream_level": "2 m above ground",
                "preferred_units": "F",
            }
        ),
        primary=True,
        kind="continuous",
        units="F",
    ),
    "wspd10m": VarSpec(
        id="wspd10m",
        name="Wind Speed",
        selectors=VarSelectors(
            hints={
                "source_family": "rtma_ru",
                "u_component": "10u",
                "v_component": "10v",
                "upstream_level": "10 m above ground",
                "preferred_units": "mph",
            }
        ),
        primary=True,
        derived=True,
        derive="wspd10m",
        kind="continuous",
        units="mph",
    ),
    "wgst10m": VarSpec(
        id="wgst10m",
        name="Wind Gust",
        selectors=VarSelectors(
            search=[":GUST:10 m above ground:"],
            hints={
                "source_family": "rtma_ru",
                "upstream_var": "GUST",
                "upstream_level": "10 m above ground",
                "preferred_units": "mph",
            }
        ),
        primary=True,
        kind="continuous",
        units="mph",
    ),
    "spres": VarSpec(
        id="spres",
        name="Surface Pressure",
        selectors=VarSelectors(
            search=[":PRES:surface:"],
            hints={
                "source_family": "rtma_ru",
                "upstream_var": "PRES",
                "upstream_level": "surface",
                "preferred_units": "hPa",
            }
        ),
        primary=True,
        kind="continuous",
        units="hPa",
    ),
}


CURRENT_ANALYSIS_COLOR_MAP_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "tmp2m",
    "dp2m": "dp2m",
    "wspd10m": "wspd10m",
    "wgst10m": "wgst10m",
    "spres": "spres",
}

CURRENT_ANALYSIS_CONVERSION_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "c_to_f",
    "dp2m": "c_to_f",
    "wspd10m": "ms_to_mph",
    "wgst10m": "ms_to_mph",
    "spres": "pressure_pa_to_hpa",
}

CURRENT_ANALYSIS_ORDER_BY_VAR_KEY: dict[str, int] = {
    "tmp2m": 0,
    "dp2m": 1,
    "wspd10m": 2,
    "wgst10m": 3,
    "spres": 4,
}

CURRENT_ANALYSIS_GROUP_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "Surface",
    "dp2m": "Surface",
    "wspd10m": "Surface",
    "wgst10m": "Surface",
    "spres": "Surface",
}

# Keep pressure in the backend contract for future contours and overlays, but do
# not expose it as a primary buildable base layer in the public Current Analysis UI.
CURRENT_ANALYSIS_INTERNAL_ONLY_VAR_KEYS = {"spres"}


def _capability_from_var_spec(var_key: str, var_spec: VarSpec) -> VariableCapability:
    is_buildable = bool(var_spec.primary or var_spec.derived) and var_key not in CURRENT_ANALYSIS_INTERNAL_ONLY_VAR_KEYS
    return VariableCapability(
        var_key=var_key,
        name=var_spec.name,
        selectors=var_spec.selectors,
        primary=var_spec.primary,
        derived=var_spec.derived,
        derive_strategy_id=var_spec.derive,
        kind=var_spec.kind,
        units=var_spec.units,
        normalize_units=var_spec.normalize_units,
        scale=var_spec.scale,
        color_map_id=CURRENT_ANALYSIS_COLOR_MAP_BY_VAR_KEY.get(var_key),
        buildable=is_buildable,
        order=CURRENT_ANALYSIS_ORDER_BY_VAR_KEY.get(var_key),
        group=CURRENT_ANALYSIS_GROUP_BY_VAR_KEY.get(var_key),
        conversion=CURRENT_ANALYSIS_CONVERSION_BY_VAR_KEY.get(var_key),
        render_substrates=["grid"],
    )


CURRENT_ANALYSIS_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    var_key: _capability_from_var_spec(var_key, var_spec)
    for var_key, var_spec in CURRENT_ANALYSIS_VARS.items()
}


CURRENT_ANALYSIS_CAPABILITIES = ModelCapabilities(
    model_id=CURRENT_ANALYSIS_MODEL_ID,
    name="Current Analysis",
    product="obs",
    canonical_region=CURRENT_ANALYSIS_REGION_ID,
    grid_meters_by_region={
        CURRENT_ANALYSIS_REGION_ID: 2_500.0,
    },
    run_discovery={
        "probe_var_key": "tmp2m",
        "probe_fhs": [0],
        "source_priority": ["aws", "nomads"],
        "allow_grib_without_idx": False,
    },
    ui_defaults={
        "default_var_key": "tmp2m",
        "default_run": "latest",
        "default_frame_selection": "latest",
    },
    ui_constraints={
        "canonical_region": CURRENT_ANALYSIS_REGION_ID,
        "time_axis_mode": "observed",
        "latest_only": True,
        "supports_sampling": True,
    },
    variable_catalog=CURRENT_ANALYSIS_VARIABLE_CATALOG,
)


CURRENT_ANALYSIS_MODEL = CurrentAnalysisPlugin(
    id=CURRENT_ANALYSIS_MODEL_ID,
    name="Current Analysis",
    regions=CURRENT_ANALYSIS_REGIONS,
    vars=CURRENT_ANALYSIS_VARS,
    product="obs",
    capabilities=CURRENT_ANALYSIS_CAPABILITIES,
)