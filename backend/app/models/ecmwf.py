"""ECMWF IFS model plugin.

Phase 1 rollout scope:
  - IFS `oper`
    - `tmp2m`, `dp2m`, `wspd10m`
  - realtime publishing only

Herbie wiring:
  - model = "ifs"
  - product = "oper"
"""

from __future__ import annotations

from datetime import datetime

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
            "wspd10m": "wspd10m",
            "wind10m": "wspd10m",
            "10mwind": "wspd10m",
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
}


ECMWF_COLOR_MAP_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "tmp2m",
    "dp2m": "dp2m",
    "wspd10m": "wspd10m",
}

ECMWF_DEFAULT_FH_BY_VAR_KEY: dict[str, int] = {
    "tmp2m": 0,
    "dp2m": 0,
    "wspd10m": 0,
}

ECMWF_ORDER_BY_VAR_KEY: dict[str, int] = {
    "tmp2m": 1,
    "dp2m": 2,
    "wspd10m": 12,
}

ECMWF_GROUP_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "Temperature",
    "dp2m": "Temperature",
    "wspd10m": "Wind",
}

ECMWF_CONVERSION_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "c_to_f",
    "dp2m": "c_to_f",
    "wspd10m": "ms_to_mph",
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