"""ECMWF IFS model plugin.

Phase 1 rollout scope:
  - IFS `oper`
    - `tmp2m`, `dp2m`, `wspd10m`, `wgst10m`, `precip_total`, `mucape`, `pwat`, `snowfall_total`
  - realtime publishing only

Herbie wiring:
  - model = "ifs"
  - product = "oper"
"""

from __future__ import annotations

from datetime import datetime

from .kuchera import kuchera_hint_overrides
from .base import (
    BaseModelPlugin,
    HerbieRequest,
    ModelCapabilities,
    RegionSpec,
    VarSelectors,
    VarSpec,
    VariableCapability,
)


class ECMWFPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return list(ECMWF_OPER_FHS)

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        aliases: dict[str, str] = {
            "tmp2m": "tmp2m",
            "tm2m": "tmp2m",
            "t2m": "tmp2m",
            "2t": "tmp2m",
            "dp2m": "dp2m",
            "d2m": "dp2m",
            "2d": "dp2m",
            "dpt2m": "dp2m",
            "dewpoint2m": "dp2m",
            "dewpoint": "dp2m",
            "precip_total": "precip_total",
            "total_precip": "precip_total",
            "apcp": "precip_total",
            "qpf": "precip_total",
            "total_qpf": "precip_total",
            "snowfall_total": "snowfall_total",
            "asnow": "snowfall_total",
            "snow10": "snowfall_total",
            "snow_10to1": "snowfall_total",
            "total_snow": "snowfall_total",
            "totalsnow": "snowfall_total",
            "snowfall_kuchera_total": "snowfall_kuchera_total",
            "snowkuchera": "snowfall_kuchera_total",
            "wspd10m": "wspd10m",
            "wind10m": "wspd10m",
            "10mwind": "wspd10m",
            "wgst10m": "wgst10m",
            "gust10m": "wgst10m",
            "10m_gust": "wgst10m",
            "gust": "wgst10m",
            "wind_gust": "wgst10m",
            "mucape": "mucape",
            "most_unstable_cape": "mucape",
            "mostunstablecape": "mucape",
            "pwat": "pwat",
            "precipitable_water": "pwat",
            "precipitablewater": "pwat",
            "tcwv": "pwat",
            "total_column_water_vapor": "pwat",
            "10u": "10u",
            "u10": "10u",
            "10v": "10v",
            "v10": "10v",
        }
        return aliases.get(normalized, normalized)

    def herbie_request(
        self,
        *,
        product: str | None = None,
        var_key: str | None = None,
        run_date: datetime | None = None,
        fh: int | None = None,
        search_pattern: str | None = None,
    ) -> HerbieRequest:
        base_request = super().herbie_request(
            product=product,
            var_key=var_key,
            run_date=run_date,
            fh=fh,
            search_pattern=search_pattern,
        )
        return HerbieRequest(
            model="ifs",
            product=base_request.product,
            herbie_kwargs=dict(base_request.herbie_kwargs),
        )

    def search_patterns_for_var(
        self,
        *,
        var_key: str,
        fh: int | None = None,
        product: str | None = None,
        var_spec: VarSpec | None = None,
    ) -> list[str]:
        patterns = super().search_patterns_for_var(
            var_key=var_key,
            fh=fh,
            product=product,
            var_spec=var_spec,
        )
        if var_key != "wgst10m" or fh is None:
            return patterns
        preferred = [":10fg3:", ":10fg:"] if 93 <= int(fh) <= 144 else [":10fg:", ":10fg3:"]
        ordered: list[str] = []
        for pattern in preferred + patterns:
            if pattern and pattern not in ordered:
                ordered.append(pattern)
        return ordered


ECMWF_OPER_FHS = list(range(0, 145, 3)) + list(range(150, 361, 6))


ECMWF_REGIONS: dict[str, RegionSpec] = {
    "conus": RegionSpec(
        id="conus",
        name="CONUS",
        bbox_wgs84=(-134.0, 24.0, -60.0, 55.0),
        tile_matrix="WebMercatorQuad",
        clip=True,
    ),
}


ECMWF_VARS: dict[str, VarSpec] = {
    "tmp2m": VarSpec(
        id="tmp2m",
        name="Surface Temp",
        selectors=VarSelectors(
            search=[":2t:"],
            filter_by_keys={
                "shortName": "2t",
                "typeOfLevel": "surface",
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
            search=[":2d:"],
            filter_by_keys={
                "shortName": "2d",
                "typeOfLevel": "surface",
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
    "precip_total": VarSpec(
        id="precip_total",
        name="Total Precip",
        selectors=VarSelectors(
            search=[":tp:sfc:", ":tp:"],
            filter_by_keys={
                "shortName": "tp",
                "typeOfLevel": "surface",
            },
            hints={
                "upstream_var": "tp",
                "short_name": "tp",
            },
        ),
        primary=True,
        kind="continuous",
        units="in",
    ),
    "snowfall_total": VarSpec(
        id="snowfall_total",
        name="Total Snowfall (10:1)",
        selectors=VarSelectors(
            search=[":sf:sfc:", ":sf:"],
            filter_by_keys={
                "shortName": "sf",
                "typeOfLevel": "surface",
            },
            hints={
                "upstream_var": "sf",
                "short_name": "sf",
            },
        ),
        primary=True,
        kind="continuous",
        units="in",
    ),
    "snowfall_kuchera_total": VarSpec(
        id="snowfall_kuchera_total",
        name="Total Snowfall (Kuchera)",
        selectors=VarSelectors(
            hints={
                "kuchera_lwe_component": "sf",
                "kuchera_lwe_component_scale": "1000",
                "cumulative_cache_version": "ecmwf_sf_v2",
                "step_hours": "3",
                "step_transition_fh": "144",
                "step_hours_after_fh": "6",
                "kuchera_profile_mode": "simplified",
                "kuchera_use_surface_temp_cap": "true",
                "kuchera_surface_temp_cap_cold_f": "30",
                "kuchera_surface_temp_cap_warm_f": "34",
                "kuchera_surface_temp_cap_cold_ratio": "18",
                "kuchera_surface_temp_cap_warm_ratio": "10",
                "kuchera_use_sfc_pressure_mask": "true",
                **kuchera_hint_overrides(levels_hpa=(925, 850, 700, 600), require_rh=False),
            }
        ),
        primary=True,
        derived=True,
        derive="snowfall_kuchera_total_cumulative",
        kind="continuous",
        units="in",
    ),
    "sf": VarSpec(
        id="sf",
        name="Snowfall Water Equivalent",
        selectors=VarSelectors(
            search=[":sf:sfc:", ":sf:"],
            filter_by_keys={
                "shortName": "sf",
                "typeOfLevel": "surface",
            },
            hints={
                "upstream_var": "sf",
                "short_name": "sf",
            },
        ),
        kind="continuous",
        units="m",
    ),
    "tmp925": VarSpec(
        id="tmp925",
        name="925mb Temp",
        selectors=VarSelectors(
            search=[":t:925:pl:"],
            filter_by_keys={
                "shortName": "t",
                "typeOfLevel": "isobaricInhPa",
            },
            hints={
                "upstream_var": "t925",
                "cf_var": "t",
                "short_name": "t",
            },
        ),
        kind="continuous",
        units="C",
    ),
    "tmp850": VarSpec(
        id="tmp850",
        name="850mb Temp",
        selectors=VarSelectors(
            search=[":t:850:pl:"],
            filter_by_keys={
                "shortName": "t",
                "typeOfLevel": "isobaricInhPa",
            },
            hints={
                "upstream_var": "t850",
                "cf_var": "t",
                "short_name": "t",
            },
        ),
        kind="continuous",
        units="C",
    ),
    "tmp700": VarSpec(
        id="tmp700",
        name="700mb Temp",
        selectors=VarSelectors(
            search=[":t:700:pl:"],
            filter_by_keys={
                "shortName": "t",
                "typeOfLevel": "isobaricInhPa",
            },
            hints={
                "upstream_var": "t700",
                "cf_var": "t",
                "short_name": "t",
            },
        ),
        kind="continuous",
        units="C",
    ),
    "tmp600": VarSpec(
        id="tmp600",
        name="600mb Temp",
        selectors=VarSelectors(
            search=[":t:600:pl:"],
            filter_by_keys={
                "shortName": "t",
                "typeOfLevel": "isobaricInhPa",
            },
            hints={
                "upstream_var": "t600",
                "cf_var": "t",
                "short_name": "t",
            },
        ),
        kind="continuous",
        units="C",
    ),
    "tmp500": VarSpec(
        id="tmp500",
        name="500mb Temp",
        selectors=VarSelectors(
            search=[":t:500:pl:"],
            filter_by_keys={
                "shortName": "t",
                "typeOfLevel": "isobaricInhPa",
            },
            hints={
                "upstream_var": "t500",
                "cf_var": "t",
                "short_name": "t",
            },
        ),
        kind="continuous",
        units="C",
    ),
    "pres_sfc": VarSpec(
        id="pres_sfc",
        name="Surface Pressure",
        selectors=VarSelectors(
            search=[":sp:sfc:"],
            filter_by_keys={
                "shortName": "sp",
                "typeOfLevel": "surface",
            },
            hints={
                "upstream_var": "sp",
                "short_name": "sp",
            },
        ),
        kind="continuous",
        units="Pa",
    ),
    "10u": VarSpec(
        id="10u",
        name="10m U Wind",
        selectors=VarSelectors(
            search=[":10u:"],
            filter_by_keys={
                "shortName": "10u",
                "typeOfLevel": "surface",
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
            search=[":10v:"],
            filter_by_keys={
                "shortName": "10v",
                "typeOfLevel": "surface",
            },
            hints={
                "upstream_var": "10v",
                "cf_var": "v10",
                "short_name": "10v",
            },
        ),
    ),
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
            search=[":10fg:", ":10fg3:"],
            filter_by_keys={
                "typeOfLevel": "surface",
            },
            hints={
                "upstream_var": "10fg",
                "short_name": "10fg",
            },
        ),
        primary=True,
        kind="continuous",
        units="mph",
    ),
    "mucape": VarSpec(
        id="mucape",
        name="Most-Unstable CAPE",
        selectors=VarSelectors(
            search=[":mucape:sfc:", ":mucape:"],
            filter_by_keys={
                "shortName": "mucape",
                "typeOfLevel": "mostUnstableParcel",
            },
            hints={
                "upstream_var": "mucape",
                "cf_var": "cape",
                "short_name": "mucape",
                "cape_layer": "most unstable parcel",
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
            search=[":tcwv:"],
            filter_by_keys={
                "shortName": "tcwv",
                "typeOfLevel": "atmosphereSingleLayer",
            },
            hints={
                "upstream_var": "tcwv",
                "cf_var": "tcwv",
                "short_name": "tcwv",
            },
        ),
        primary=True,
        kind="continuous",
        units="in",
    ),
}


ECMWF_COLOR_MAP_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "tmp2m",
    "dp2m": "dp2m",
    "precip_total": "precip_total",
    "pwat": "pwat",
    "snowfall_total": "snowfall_total",
    "snowfall_kuchera_total": "snowfall_total",
    "wspd10m": "wspd10m",
    "wgst10m": "wgst10m",
    "mucape": "mlcape",
}

ECMWF_DEFAULT_FH_BY_VAR_KEY: dict[str, int] = {
    "tmp2m": 0,
    "dp2m": 0,
    "precip_total": 3,
    "pwat": 0,
    "snowfall_total": 3,
    "snowfall_kuchera_total": 3,
    "wspd10m": 0,
    "wgst10m": 3,
    "mucape": 0,
}

ECMWF_ORDER_BY_VAR_KEY: dict[str, int] = {
    "tmp2m": 1,
    "dp2m": 2,
    "pwat": 9,
    "precip_total": 10,
    "snowfall_total": 11,
    "snowfall_kuchera_total": 14,
    "wspd10m": 12,
    "wgst10m": 13,
    "mucape": 20,
}

ECMWF_GROUP_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "Temperature",
    "dp2m": "Temperature",
    "pwat": "Moisture",
    "precip_total": "Precipitation",
    "snowfall_total": "Precipitation",
    "snowfall_kuchera_total": "Precipitation",
    "wspd10m": "Wind",
    "wgst10m": "Wind",
    "mucape": "Instability",
}

ECMWF_CONVERSION_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "c_to_f",
    "dp2m": "c_to_f",
    "precip_total": "m_to_in",
    "pwat": "kgm2_to_in",
    "snowfall_total": "m_swe_to_in_10to1",
    "wspd10m": "ms_to_mph",
    "wgst10m": "ms_to_mph",
}

ECMWF_CONSTRAINTS_BY_VAR_KEY: dict[str, dict[str, int]] = {
    "precip_total": {
        "min_fh": 3,
    },
    "snowfall_total": {
        "min_fh": 3,
    },
    "snowfall_kuchera_total": {
        "min_fh": 3,
    },
    "wgst10m": {
        "min_fh": 3,
    },
}


def _capability_from_var_spec(var_key: str, var_spec: VarSpec) -> VariableCapability:
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
        color_map_id=ECMWF_COLOR_MAP_BY_VAR_KEY.get(var_key),
        default_fh=ECMWF_DEFAULT_FH_BY_VAR_KEY.get(var_key),
        buildable=bool(var_spec.primary or var_spec.derived),
        order=ECMWF_ORDER_BY_VAR_KEY.get(var_key),
        group=ECMWF_GROUP_BY_VAR_KEY.get(var_key),
        conversion=ECMWF_CONVERSION_BY_VAR_KEY.get(var_key),
        constraints=dict(ECMWF_CONSTRAINTS_BY_VAR_KEY.get(var_key, {})),
    )


ECMWF_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    var_key: _capability_from_var_spec(var_key, var_spec)
    for var_key, var_spec in ECMWF_VARS.items()
}


ECMWF_CAPABILITIES = ModelCapabilities(
    model_id="ecmwf",
    name="ECMWF",
    product="oper",
    canonical_region="conus",
    grid_meters_by_region={
        "conus": 9_000.0,
    },
    run_discovery={
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 3],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 12,
        "fallback_lag_hours": 6,
        "source_priority": ["azure", "aws", "ecmwf"],
    },
    ui_defaults={
        "default_var_key": "tmp2m",
        "default_run": "latest",
    },
    ui_constraints={
        "canonical_region": "conus",
        "supports_sampling": True,
        "overlay_fade_out_zoom_start": 6,
        "overlay_fade_out_zoom_end": 7,
    },
    variable_catalog=ECMWF_VARIABLE_CATALOG,
)


ECMWF_MODEL = ECMWFPlugin(
    id="ecmwf",
    name="ECMWF",
    regions=ECMWF_REGIONS,
    vars=ECMWF_VARS,
    product="oper",
    capabilities=ECMWF_CAPABILITIES,
)