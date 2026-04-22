"""GFS model plugin — V3 clean implementation.

Provides VarSpec definitions, region specs, and forecast-hour schedule
for the Global Forecast System (GFS) model.

V3 design: this module is import-safe with zero external service dependencies.
All runtime logic (xarray selection, cfgrib parsing, cycle management) that
lived here in V2 has been removed — the V3 builder never calls those paths.
Selection happens via Herbie search patterns (VarSpec.selectors.search).
Derivation dispatch happens in builder/derive.py (Phase 2).
"""

from __future__ import annotations

from .base import (
    BaseModelPlugin,
    ModelCapabilities,
    RegionSpec,
    VarSelectors,
    VarSpec,
    VariableCapability,
)
from .kuchera import kuchera_hint_overrides


class GFSPlugin(BaseModelPlugin):
    """V3-clean GFS plugin.

    Inherits get_var() / get_region() from BaseModelPlugin.
    Only overrides target_fhs() and normalize_var_id() — both
    are dependency-free.
    """

    def target_fhs(self, cycle_hour: int) -> list[int]:
        """GFS forecast hours to build.

        GFS runs 4×/day at 00/06/12/18z.  Initial V3 rollout builds
        fh000–fh240 in 3-hour steps, then fh246–fh384 in 6-hour steps
        (all cycles use the same schedule).
        """
        del cycle_hour  # all GFS cycles use the same FH set for now
        return list(GFS_INITIAL_FHS)

    def normalize_var_id(self, var_id: str) -> str:
        """Normalise common GFS variable aliases to canonical V3 IDs."""
        normalized = var_id.strip().lower()
        _aliases: dict[str, str] = {
            "t2m": "tmp2m",
            "2t": "tmp2m",
            "tmp2m": "tmp2m",
            "dp2m": "dp2m",
            "d2m": "dp2m",
            "2d": "dp2m",
            "dpt2m": "dp2m",
            "dewpoint2m": "dp2m",
            "dewpoint": "dp2m",
            "tmp850": "tmp850",
            "t850": "tmp850",
            "t850mb": "tmp850",
            "temp850": "tmp850",
            "temp850mb": "tmp850",
            "wspd850": "wspd850",
            "wind850": "wspd850",
            "850wind": "wspd850",
            "850mbwind": "wspd850",
            "850mbwinds": "wspd850",
            "850_wind": "wspd850",
            "850_winds": "wspd850",
            "850mb_heights_winds": "wspd850",
            "850_heights_winds": "wspd850",
            "wspd300": "wspd300",
            "wind300": "wspd300",
            "300wind": "wspd300",
            "300mbwind": "wspd300",
            "300mbwinds": "wspd300",
            "300_wind": "wspd300",
            "300_winds": "wspd300",
            "300mb_heights_winds": "wspd300",
            "300_heights_winds": "wspd300",
            "vort500": "vort500",
            "500vort": "vort500",
            "500_vort": "vort500",
            "500mb_vort": "vort500",
            "500mb_vorticity": "vort500",
            "500_vorticity": "vort500",
            "absv500": "vort500",
            "avor500": "vort500",
            "hgt500": "hgt500",
            "z500": "hgt500",
            "500hgt": "hgt500",
            "500_height": "hgt500",
            "500mb_height": "hgt500",
            "500mb_heights": "hgt500",
            "500_heights": "hgt500",
            "sbcape": "sbcape",
            "mucape": "mucape",
            "mlcape": "mlcape",
            "pwat": "pwat",
            "precipitable_water": "pwat",
            "precipitablewater": "pwat",
            "refc": "refc",
            "cref": "refc",
            "wspd10m": "wspd10m",
            "wgst10m": "wgst10m",
            "gust": "wgst10m",
            "gust10m": "wgst10m",
            "10m_gust": "wgst10m",
            "wind_gust": "wgst10m",
            "10u": "10u",
            "u10": "10u",
            "10v": "10v",
            "v10": "10v",
            "precip_total": "precip_total",
            "total_precip": "precip_total",
            "apcp": "precip_total",
            "qpf": "precip_total",
            "total_qpf": "precip_total",
            "qpf6h": "qpf6h",
            "snowfall_total": "snowfall_total",
            "snowfall_kuchera_total": "snowfall_kuchera_total",
            "asnow": "snowfall_total",
            "snow10": "snowfall_total",
            "total_snow": "snowfall_total",
            "totalsnow": "snowfall_total",
            "crain": "crain",
            "csnow": "csnow",
            "cicep": "cicep",
            "cfrzr": "cfrzr",
        }
        return _aliases.get(normalized, normalized)


# ---------------------------------------------------------------------------
# Region definitions
# ---------------------------------------------------------------------------

GFS_REGIONS: dict[str, RegionSpec] = {
    "na": RegionSpec(
        id="na",
        name="North America",
        bbox_wgs84=(-170.0, 5.0, -50.0, 75.0),
        clip=True,
    ),
    "pnw": RegionSpec(
        id="pnw",
        name="Pacific Northwest",
        bbox_wgs84=(-125.5, 41.5, -111.0, 49.5),
        clip=True,
    ),
    "conus": RegionSpec(
        id="conus",
        name="CONUS",
        bbox_wgs84=(-134.0, 24.0, -60.0, 55.0),
        clip=True,
    ),
}

# ---------------------------------------------------------------------------
# Forecast-hour schedule
# ---------------------------------------------------------------------------

# Mixed-resolution forecast-hour schedule:
#   - 3-hour steps: fh000–fh240 (inclusive)
#   - 6-hour steps: fh246–fh384 (inclusive)
# Boundary fh240 appears once to avoid duplicate builds.
GFS_INITIAL_FHS: tuple[int, ...] = tuple(range(0, 241, 3)) + tuple(range(246, 385, 6))

# Initial rollout: PNW only.  CONUS added in Phase 3 after scale validation.
GFS_INITIAL_ROLLOUT_REGIONS: tuple[str, ...] = ("pnw",)

# ---------------------------------------------------------------------------
# Variable definitions
# ---------------------------------------------------------------------------


def _gfs_tmp_level_component(level_hpa: int) -> VarSpec:
    level = int(level_hpa)
    return VarSpec(
        id=f"tmp{level}",
        name=f"{level}mb Temp",
        selectors=VarSelectors(
            search=[f":TMP:{level} mb:"],
            filter_by_keys={
                "shortName": "t",
                "typeOfLevel": "isobaricInhPa",
                "level": str(level),
            },
            hints={
                "upstream_var": f"t{level}",
                "cf_var": "t",
                "short_name": "t",
            },
        ),
    )


def _gfs_rh_level_component(level_hpa: int) -> VarSpec:
    level = int(level_hpa)
    return VarSpec(
        id=f"rh{level}",
        name=f"{level}mb RH",
        selectors=VarSelectors(
            search=[f":RH:{level} mb:"],
            filter_by_keys={
                "shortName": "r",
                "typeOfLevel": "isobaricInhPa",
                "level": str(level),
            },
            hints={
                "upstream_var": f"r{level}",
                "short_name": "r",
            },
        ),
    )


def _gfs_hgt_level_component(level_hpa: int) -> VarSpec:
    level = int(level_hpa)
    return VarSpec(
        id=f"hgt{level}",
        name=f"{level}mb Height",
        selectors=VarSelectors(
            search=[f":HGT:{level} mb:"],
            filter_by_keys={
                "shortName": "gh",
                "typeOfLevel": "isobaricInhPa",
                "level": str(level),
            },
            hints={
                "upstream_var": f"gh{level}",
                "cf_var": "gh",
                "short_name": "gh",
            },
        ),
    )


def _gfs_wind_level_component(axis: str, level_hpa: int) -> VarSpec:
    level = int(level_hpa)
    axis_norm = axis.strip().lower()
    if axis_norm not in {"u", "v"}:
        raise ValueError(f"Unsupported wind axis: {axis!r}")
    short_name = "ugrd" if axis_norm == "u" else "vgrd"
    grib_name = "UGRD" if axis_norm == "u" else "VGRD"
    return VarSpec(
        id=f"{axis_norm}{level}",
        name=f"{level}mb {'U' if axis_norm == 'u' else 'V'} Wind",
        selectors=VarSelectors(
            search=[f":{grib_name}:{level} mb:"],
            filter_by_keys={
                "shortName": short_name,
                "typeOfLevel": "isobaricInhPa",
                "level": str(level),
            },
            hints={
                "upstream_var": f"{axis_norm}{level}",
                "cf_var": axis_norm,
                "short_name": short_name,
            },
        ),
    )


def _gfs_absv_level_component(level_hpa: int) -> VarSpec:
    level = int(level_hpa)
    return VarSpec(
        id=f"vort{level}",
        name=f"{level}mb Absolute Vorticity",
        selectors=VarSelectors(
            search=[f":ABSV:{level} mb:"],
            filter_by_keys={
                "shortName": "absv",
                "typeOfLevel": "isobaricInhPa",
                "level": str(level),
            },
            hints={
                "upstream_var": f"absv{level}",
                "cf_var": "absv",
                "short_name": "absv",
                "contour_component": f"hgt{level}",
                "contour_interval": "60",
                "contour_start": "4800",
                "contour_end": "6240",
                "contour_key": "height_500mb",
                "contour_label": f"{level} mb Height",
            },
        ),
        primary=True,
        kind="continuous",
        units="10^-5 s^-1",
    )


GFS_VARS: dict[str, VarSpec] = {
    # ── Simple variables (Phase 1+) ─────────────────────────────────────────
    "tmp2m": VarSpec(
        id="tmp2m",
        name="Surface Temp",
        selectors=VarSelectors(
            search=[":TMP:2 m above ground:"],
            filter_by_keys={
                "typeOfLevel": "heightAboveGround",
                "level": "2",
            },
            hints={
                "upstream_var": "t2m",
                "cf_var": "t2m",
                "short_name": "2t",
            },
        ),
        primary=True,
        kind="continuous",
        units="F",
    ),
    "dp2m": VarSpec(
        id="dp2m",
        name="Surface Dew Point",
        selectors=VarSelectors(
            search=[":DPT:2 m above ground:"],
            filter_by_keys={
                "typeOfLevel": "heightAboveGround",
                "level": "2",
            },
            hints={
                "upstream_var": "d2m",
                "cf_var": "d2m",
                "short_name": "2d",
            },
        ),
        primary=True,
        kind="continuous",
        units="F",
    ),
    "tmp850": VarSpec(
        id="tmp850",
        name="850mb Temp",
        selectors=VarSelectors(
            search=[":TMP:850 mb:"],
            filter_by_keys={
                "shortName": "t",
                "typeOfLevel": "isobaricInhPa",
                "level": "850",
            },
            hints={
                "upstream_var": "t850",
                "cf_var": "t",
                "short_name": "t",
            },
        ),
        primary=True,
        kind="continuous",
        units="C",
    ),
    "wspd850": VarSpec(
        id="wspd850",
        name="850mb Heights + Winds",
        selectors=VarSelectors(
            hints={
                "u_component": "u850",
                "v_component": "v850",
                "contour_component": "hgt850",
                "contour_interval": "30",
                "contour_start": "900",
                "contour_end": "1800",
                "contour_key": "height_850mb",
                "contour_label": "850 mb Height",
            }
        ),
        primary=True,
        derived=True,
        derive="wspd10m",
        kind="continuous",
        units="kt",
    ),
    "wspd300": VarSpec(
        id="wspd300",
        name="300mb Heights + Winds",
        selectors=VarSelectors(
            hints={
                "u_component": "u300",
                "v_component": "v300",
                "contour_component": "hgt300",
                "contour_interval": "120",
                "contour_key": "height_300mb",
                "contour_label": "300 mb Height",
            }
        ),
        primary=True,
        derived=True,
        derive="wspd10m",
        kind="continuous",
        units="kt",
    ),
    "vort500": _gfs_absv_level_component(500),
    "sbcape": VarSpec(
        id="sbcape",
        name="Surface-Based CAPE",
        selectors=VarSelectors(
            search=[":CAPE:surface:"],
            filter_by_keys={
                "shortName": "cape",
                "typeOfLevel": "surface",
            },
            hints={
                "upstream_var": "sbcape",
                "cf_var": "cape",
                "short_name": "cape",
                "cape_layer": "surface",
            },
        ),
        primary=True,
        kind="continuous",
        units="J/kg",
    ),
    "mlcape": VarSpec(
        id="mlcape",
        name="Mixed-Layer CAPE",
        selectors=VarSelectors(
            search=[":CAPE:90-0 mb above ground:"],
            filter_by_keys={
                "shortName": "cape",
                "typeOfLevel": "pressureFromGroundLayer",
                "topLevel": "0",
                "bottomLevel": "90",
            },
            hints={
                "upstream_var": "mlcape",
                "cf_var": "cape",
                "short_name": "cape",
                "cape_layer": "90-0 mb above ground",
            },
        ),
        primary=True,
        kind="continuous",
        units="J/kg",
    ),
    "mucape": VarSpec(
        id="mucape",
        name="Most-Unstable CAPE",
        selectors=VarSelectors(
            search=[":CAPE:255-0 mb above ground:"],
            filter_by_keys={
                "shortName": "cape",
                "typeOfLevel": "pressureFromGroundLayer",
                "topLevel": "0",
                "bottomLevel": "255",
            },
            hints={
                "upstream_var": "mucape",
                "cf_var": "cape",
                "short_name": "cape",
                "cape_layer": "255-0 mb above ground",
            },
        ),
        primary=True,
        kind="continuous",
        units="J/kg",
    ),
    "pwat": VarSpec(
        id="pwat",
        name="Precipitable Water",
        selectors=VarSelectors(
            search=[":PWAT:entire atmosphere (considered as a single layer):"],
            filter_by_keys={
                "shortName": "pwat",
                "typeOfLevel": "atmosphereSingleLayer",
            },
            hints={
                "upstream_var": "pwat",
                "cf_var": "pwat",
                "short_name": "pwat",
            },
        ),
        primary=True,
        kind="continuous",
        units="in",
    ),
    **{
        f"hgt{level}": _gfs_hgt_level_component(level)
        for level in (300, 500, 850)
    },
    **{
        f"{axis}{level}": _gfs_wind_level_component(axis, level)
        for axis in ("u", "v")
        for level in (300, 850)
    },
    **{
        f"tmp{level}": _gfs_tmp_level_component(level)
        for level in (925, 700, 600, 500)
    },
    **{
        f"rh{level}": _gfs_rh_level_component(level)
        for level in (925, 850, 700, 600, 500)
    },
    # ── Wind components (fetched separately for wspd10m derivation) ─────────
    "10u": VarSpec(
        id="10u",
        name="10m U Wind",
        selectors=VarSelectors(
            search=[":UGRD:10 m above ground:"],
            filter_by_keys={
                "typeOfLevel": "heightAboveGround",
                "level": "10",
            },
            hints={
                "upstream_var": "10u",
                "cf_var": "u10",
                "short_name": "10u",
            },
        ),
    ),
    "10v": VarSpec(
        id="10v",
        name="10m V Wind",
        selectors=VarSelectors(
            search=[":VGRD:10 m above ground:"],
            filter_by_keys={
                "typeOfLevel": "heightAboveGround",
                "level": "10",
            },
            hints={
                "upstream_var": "10v",
                "cf_var": "v10",
                "short_name": "10v",
            },
        ),
    ),
    # ── Derived: wind speed (Phase 2) ────────────────────────────────────────
    "wspd10m": VarSpec(
        id="wspd10m",
        name="10m Wind Speed",
        selectors=VarSelectors(
            hints={
                "u_component": "10u",
                "v_component": "10v",
            }
        ),
        derived=True,
        derive="wspd10m",
        kind="continuous",
        units="mph",
    ),
    "wgst10m": VarSpec(
        id="wgst10m",
        name="10m Wind Gust",
        selectors=VarSelectors(
            search=[":GUST:surface:"],
            filter_by_keys={
                "typeOfLevel": "surface",
                "shortName": "gust",
            },
            hints={
                "upstream_var": "gust",
                "cf_var": "gust",
                "short_name": "gust",
            },
        ),
        primary=True,
        kind="continuous",
        units="mph",
    ),
    "refc": VarSpec(
        id="refc",
        name="Composite Reflectivity",
        selectors=VarSelectors(
            search=[":REFC:"],
            filter_by_keys={
                "shortName": "refc",
            },
            hints={
                "upstream_var": "refc",
                "cf_var": "refc",
                "short_name": "refc",
            },
        ),
    ),
    "prate": VarSpec(
        id="prate",
        name="Precipitation Rate",
        selectors=VarSelectors(
            search=[":PRATE:surface:"],
            filter_by_keys={
                "typeOfLevel": "surface",
                "shortName": "prate",
            },
            hints={
                "upstream_var": "prate",
                "cf_var": "prate",
                "short_name": "prate",
            },
        ),
    ),
    "ptype_intensity": VarSpec(
        id="ptype_intensity",
        name="Precipitation Type & Intensity",
        selectors=VarSelectors(
            hints={
                "display_kind": "ptype_intensity",
                "prate_component": "prate",
                "rain_component": "crain",
                "snow_component": "csnow",
                "sleet_component": "cicep",
                "frzr_component": "cfrzr",
                "apcp_component": "apcp_step",
                "step_hours": "3",
                "step_transition_fh": "240",
                "step_hours_after_fh": "6",
                "contour_component": "prmsl",
                "contour_interval": "4",
                "contour_start": "960",
                "contour_end": "1048",
                "contour_key": "mslp",
                "contour_label": "Mean Sea-Level Pressure",
                "contour_conversion": "pressure_pa_to_hpa",
                "companion_vars": "ptype_intensity_rain,ptype_intensity_snow,ptype_intensity_ice",
                "composite_mode": "max_alpha_stack",
                "composite_layers": "ice:ptype_intensity_ice;snow:ptype_intensity_snow;rain:ptype_intensity_rain",
            },
        ),
        primary=True,
        derived=True,
        derive="ptype_intensity_gfs",
        kind="indexed",
        units="in/hr",
        normalize_units="in/hr",
    ),
    "ptype_intensity_rain": VarSpec(
        id="ptype_intensity_rain",
        name="Precipitation Type Intensity Rain",
        selectors=VarSelectors(
            hints={
                "ptype_component": "rain",
                "prate_component": "prate",
                "rain_component": "crain",
                "snow_component": "csnow",
                "sleet_component": "cicep",
                "frzr_component": "cfrzr",
                "apcp_component": "apcp_step",
                "step_hours": "3",
                "step_transition_fh": "240",
                "step_hours_after_fh": "6",
            },
        ),
        derived=True,
        derive="ptype_intensity_component",
        kind="continuous",
        units="in/hr",
        normalize_units="in/hr",
    ),
    "ptype_intensity_snow": VarSpec(
        id="ptype_intensity_snow",
        name="Precipitation Type Intensity Snow",
        selectors=VarSelectors(
            hints={
                "ptype_component": "snow",
                "prate_component": "prate",
                "rain_component": "crain",
                "snow_component": "csnow",
                "sleet_component": "cicep",
                "frzr_component": "cfrzr",
                "apcp_component": "apcp_step",
                "step_hours": "3",
                "step_transition_fh": "240",
                "step_hours_after_fh": "6",
            },
        ),
        derived=True,
        derive="ptype_intensity_component",
        kind="continuous",
        units="in/hr",
        normalize_units="in/hr",
    ),
    "ptype_intensity_ice": VarSpec(
        id="ptype_intensity_ice",
        name="Precipitation Type Intensity Ice",
        selectors=VarSelectors(
            hints={
                "ptype_component": "ice",
                "prate_component": "prate",
                "rain_component": "crain",
                "snow_component": "csnow",
                "sleet_component": "cicep",
                "frzr_component": "cfrzr",
                "apcp_component": "apcp_step",
                "step_hours": "3",
                "step_transition_fh": "240",
                "step_hours_after_fh": "6",
            },
        ),
        derived=True,
        derive="ptype_intensity_component",
        kind="continuous",
        units="in/hr",
        normalize_units="in/hr",
    ),
    "crain": VarSpec(
        id="crain",
        name="Categorical Rain",
        selectors=VarSelectors(
            search=[":CRAIN:surface:"],
            filter_by_keys={
                "typeOfLevel": "surface",
                "shortName": "crain",
            },
            hints={
                "upstream_var": "crain",
                "short_name": "crain",
            },
        ),
    ),
    "csnow": VarSpec(
        id="csnow",
        name="Categorical Snow",
        selectors=VarSelectors(
            search=[":CSNOW:surface:"],
            filter_by_keys={
                "typeOfLevel": "surface",
                "shortName": "csnow",
            },
            hints={
                "upstream_var": "csnow",
                "short_name": "csnow",
            },
        ),
    ),
    "cicep": VarSpec(
        id="cicep",
        name="Categorical Sleet",
        selectors=VarSelectors(
            search=[":CICEP:surface:"],
            filter_by_keys={
                "typeOfLevel": "surface",
                "shortName": "cicep",
            },
            hints={
                "upstream_var": "cicep",
                "short_name": "cicep",
            },
        ),
    ),
    "cfrzr": VarSpec(
        id="cfrzr",
        name="Categorical Freezing Rain",
        selectors=VarSelectors(
            search=[":CFRZR:surface:"],
            filter_by_keys={
                "typeOfLevel": "surface",
                "shortName": "cfrzr",
            },
            hints={
                "upstream_var": "cfrzr",
                "short_name": "cfrzr",
            },
        ),
    ),
    "prmsl": VarSpec(
        id="prmsl",
        name="Mean Sea-Level Pressure",
        selectors=VarSelectors(
            search=[r":PRMSL:mean sea level:"],
            filter_by_keys={
                "typeOfLevel": "meanSea",
                "shortName": "prmsl",
            },
            hints={
                "upstream_var": "prmsl",
                "short_name": "prmsl",
            },
        ),
    ),
    # ── QPF (Phase 3 candidate) ──────────────────────────────────────────────
    "apcp_step": VarSpec(
        id="apcp_step",
        name="APCP Step",
        selectors=VarSelectors(
            search=[
                r":APCP:surface:[0-9]+-[0-9]+ hour acc[^:]*:$",
            ],
            filter_by_keys={
                "shortName": "apcp",
                "typeOfLevel": "surface",
            },
            hints={
                "upstream_var": "apcp",
            },
        ),
    ),
    "precip_total": VarSpec(
        id="precip_total",
        name="Total Precip",
        selectors=VarSelectors(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "3",
                "step_transition_fh": "240",
                "step_hours_after_fh": "6",
            },
        ),
        derived=True,
        derive="precip_total_cumulative",
        kind="continuous",
        units="in",
    ),
    "snowfall_total": VarSpec(
        id="snowfall_total",
        name="Total Snowfall (10:1)",
        selectors=VarSelectors(
            hints={
                "apcp_component": "apcp_step",
                "snow_component": "csnow",
                "step_hours": "3",
                "step_transition_fh": "240",
                "step_hours_after_fh": "6",
                "snow_interval_sample_mode": "three_point",
                "slr": "10",
                "snow_mask_threshold": "0.5",
                "min_step_lwe_kgm2": "0.01",
            },
        ),
        primary=True,
        derived=True,
        derive="snowfall_total_10to1_cumulative",
        kind="continuous",
        units="in",
    ),
    "snowfall_kuchera_total": VarSpec(
        id="snowfall_kuchera_total",
        name="Total Snowfall (Kuchera)",
        selectors=VarSelectors(
            hints={
                "apcp_component": "apcp_step",
                "step_hours": "3",
                "step_transition_fh": "240",
                "step_hours_after_fh": "6",
                "kuchera_use_ptype_gate": "true",
                "kuchera_profile_mode": "simplified",
                **kuchera_hint_overrides(levels_hpa=(925, 850, 700, 600)),
            },
        ),
        primary=True,
        derived=True,
        derive="snowfall_kuchera_total_cumulative",
        kind="continuous",
        units="in",
    ),
    "qpf6h": VarSpec(
        id="qpf6h",
        name="6-hr Precip",
        selectors=VarSelectors(
            search=[":APCP:surface:"],
            hints={
                "kind": "apcp_rolling_6h",
                "apcp_window_hours": "6",
            }
        ),
        kind="continuous",
        units="in",
    ),
}

GFS_COLOR_MAP_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "tmp2m",
    "dp2m": "dp2m",
    "tmp850": "tmp850",
    "wspd850": "wspd850",
    "wspd300": "wspd300",
    "vort500": "vort500",
    "sbcape": "mlcape",
    "mlcape": "mlcape",
    "mucape": "mlcape",
    "pwat": "pwat",
    "wspd10m": "wspd10m",
    "wgst10m": "wgst10m",
    "refc": "refc",
    "ptype_intensity": "ptype_intensity",
    "ptype_intensity_rain": "ptype_intensity_rain",
    "ptype_intensity_snow": "ptype_intensity_snow",
    "ptype_intensity_ice": "ptype_intensity_ice",
    "precip_total": "precip_total",
    "snowfall_total": "snowfall_total",
    "snowfall_kuchera_total": "snowfall_total",
    "qpf6h": "qpf6h",
}

GFS_DEFAULT_FH_BY_VAR_KEY: dict[str, int] = {
    "ptype_intensity": 6,
    "ptype_intensity_rain": 6,
    "ptype_intensity_snow": 6,
    "ptype_intensity_ice": 6,
    "precip_total": 6,
    "snowfall_total": 6,
    "snowfall_kuchera_total": 6,
    "qpf6h": 6,
}

GFS_ORDER_BY_VAR_KEY: dict[str, int] = {
    "tmp2m": 1,
    "dp2m": 2,
    "tmp850": 3,
    "wspd850": 4,
    "wspd300": 18,
    "vort500": 5,
    "sbcape": 6,
    "mlcape": 7,
    "mucape": 8,
    "pwat": 9,
    "precip_total": 10,
    "snowfall_total": 11,
    "wspd10m": 12,
    "wgst10m": 13,
    "ptype_intensity": 15,
    "refc": 15,
    "qpf6h": 16,
    "snowfall_kuchera_total": 17,
}

GFS_GROUP_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "Temperature",
    "dp2m": "Temperature",
    "tmp850": "Temperature",
    "wspd850": "Wind",
    "wspd300": "Wind",
    "vort500": "Dynamics",
    "sbcape": "Instability",
    "mlcape": "Instability",
    "mucape": "Instability",
    "pwat": "Moisture",
    "precip_total": "Precipitation",
    "snowfall_total": "Precipitation",
    "snowfall_kuchera_total": "Precipitation",
    "qpf6h": "Precipitation",
    "ptype_intensity": "Radar & Precipitation Type",
    "wspd10m": "Wind",
    "wgst10m": "Wind",
    "refc": "Radar & Precipitation Type",
}

GFS_CONVERSION_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "c_to_f",
    "dp2m": "c_to_f",
    "vort500": "s-1_to_1e5s-1",
    "wspd850": "ms_to_kt",
    "wspd300": "ms_to_kt",
    "pwat": "kgm2_to_in",
    "wspd10m": "ms_to_mph",
    "wgst10m": "ms_to_mph",
    "precip_total": "kgm2_to_in",
    "qpf6h": "kgm2_to_in",
}

GFS_CONSTRAINTS_BY_VAR_KEY: dict[str, dict[str, int]] = {
    "precip_total": {
        "min_fh": 3,
    },
    "snowfall_total": {
        "min_fh": 3,
    },
    "snowfall_kuchera_total": {
        "min_fh": 3,
    },
    "qpf6h": {
        "min_fh": 6,
    },
}


def _capability_from_var_spec(var_key: str, var_spec: VarSpec) -> VariableCapability:
    is_buildable = bool(var_spec.primary or var_spec.derived)
    hints = getattr(getattr(var_spec, "selectors", None), "hints", {}) or {}
    frontend: dict[str, object] = {}
    constraints = dict(GFS_CONSTRAINTS_BY_VAR_KEY.get(var_key, {}))
    if str(hints.get("companion_vars") or "").strip():
        frontend["companion_vars"] = [
            item.strip()
            for item in str(hints.get("companion_vars") or "").split(",")
            if item.strip()
        ]
    if str(hints.get("composite_mode") or "").strip():
        frontend["composite_mode"] = str(hints.get("composite_mode") or "").strip()
    if str(hints.get("composite_layers") or "").strip():
        frontend["composite_layers"] = str(hints.get("composite_layers") or "").strip()
    if str(var_key).startswith("ptype_intensity_"):
        frontend["internal_only"] = True
        frontend["allow_dry_frame"] = True
        is_buildable = False
        constraints["internal_only"] = 1
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
        color_map_id=GFS_COLOR_MAP_BY_VAR_KEY.get(var_key),
        default_fh=GFS_DEFAULT_FH_BY_VAR_KEY.get(var_key),
        buildable=is_buildable,
        order=GFS_ORDER_BY_VAR_KEY.get(var_key),
        group=GFS_GROUP_BY_VAR_KEY.get(var_key),
        conversion=GFS_CONVERSION_BY_VAR_KEY.get(var_key),
        constraints=constraints,
        frontend=frontend,
    )


GFS_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    var_key: _capability_from_var_spec(var_key, var_spec)
    for var_key, var_spec in GFS_VARS.items()
}
GFS_CAPABILITIES = ModelCapabilities(
    model_id="gfs",
    name="GFS",
    product="pgrb2.0p25",
    canonical_region="na",
    grid_meters_by_region={
        "conus": 25_000.0,
        "na": 25_000.0,
        "pnw": 25_000.0,
    },
    run_discovery={
        "probe_var_key": "tmp2m",
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 5,
    },
    ui_defaults={
        "default_var_key": "tmp2m",
        "default_run": "latest",
    },
    ui_constraints={
        "canonical_region": "na",
        "supports_sampling": True,
        "overlay_fade_out_zoom_start": 6,
        "overlay_fade_out_zoom_end": 7,
    },
    variable_catalog=GFS_VARIABLE_CATALOG,
)

# ---------------------------------------------------------------------------
# Plugin instance — imported by the model registry
# ---------------------------------------------------------------------------

GFS_MODEL = GFSPlugin(
    id="gfs",
    name="GFS",
    regions=GFS_REGIONS,
    vars=GFS_VARS,
    product="pgrb2.0p25",
    capabilities=GFS_CAPABILITIES,
)
