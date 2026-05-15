"""ECMWF AIFS model plugin.

Initial rollout scope:
  - AIFS `oper`
      - `tmp2m`
      - `dp2m`
            - `tmp850`
    - `wspd850`
            - `wspd300`
      - `precip_total`
            - `pwat`
            - `snowfall_total`
      - `wspd10m`
  - realtime publishing only

Herbie wiring:
    - model = "aifs"
    - product = "oper"
"""

from __future__ import annotations

from dataclasses import replace

from .base import HerbieRequest, ModelCapabilities, VarSelectors
from .ecmwf import ECMWFPlugin, ECMWF_REGIONS, ECMWF_VARS, _capability_from_var_spec
from .gfs import (
    PRECIP_ANOM_STATIC_TARGET_FH_BY_VAR_KEY,
    PRECIP_ANOM_TARGET_FH_BY_VAR_KEY,
)


class AIFSPlugin(ECMWFPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return list(AIFS_OPER_FHS)

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        if normalized in {"tcw", "total_column_water"}:
            return "pwat"
        if normalized in {"z850", "gh850", "z"}:
            return "hgt850"
        if normalized in {"z300", "gh300"}:
            return "hgt300"
        if normalized in {"z500", "gh500"}:
            return "hgt500"
        return super().normalize_var_id(var_id)

    def herbie_request(
        self,
        *,
        product: str | None = None,
        var_key: str | None = None,
        ensemble_view: str | None = None,
        run_date=None,
        fh: int | None = None,
        search_pattern: str | None = None,
    ) -> HerbieRequest:
        base_request = super(ECMWFPlugin, self).herbie_request(
            product=product,
            var_key=var_key,
            ensemble_view=ensemble_view,
            run_date=run_date,
            fh=fh,
            search_pattern=search_pattern,
        )
        return HerbieRequest(
            model="aifs",
            product=base_request.product,
            herbie_kwargs=dict(base_request.herbie_kwargs),
        )


AIFS_VARS = {
    "tmp2m": ECMWF_VARS["tmp2m"],
    "tmp2m_anom": ECMWF_VARS["tmp2m_anom"],
    "dp2m": ECMWF_VARS["dp2m"],
    "tmp850": ECMWF_VARS["tmp850"],
    "tmp850_anom": ECMWF_VARS["tmp850_anom"],
    "u850": ECMWF_VARS["u850"],
    "v850": ECMWF_VARS["v850"],
    "hgt850": ECMWF_VARS["hgt850"],
    "wspd850": ECMWF_VARS["wspd850"],
    "u300": ECMWF_VARS["u300"],
    "v300": ECMWF_VARS["v300"],
    "hgt300": ECMWF_VARS["hgt300"],
    "wspd300": ECMWF_VARS["wspd300"],
    "hgt500": ECMWF_VARS["hgt500"],
    "hgt500_anom": ECMWF_VARS["hgt500_anom"],
    "precip_total": ECMWF_VARS["precip_total"],
    "precip_5d_anom": ECMWF_VARS["precip_5d_anom"],
    "precip_7d_anom": ECMWF_VARS["precip_7d_anom"],
    "precip_10d_anom": ECMWF_VARS["precip_10d_anom"],
    "precip_15d_anom": ECMWF_VARS["precip_15d_anom"],
    "pwat": ECMWF_VARS["pwat"],
    "snowfall_total": ECMWF_VARS["snowfall_total"],
    "10u": ECMWF_VARS["10u"],
    "10v": ECMWF_VARS["10v"],
    "wspd10m": ECMWF_VARS["wspd10m"],
}


AIFS_OPER_FHS = list(range(0, 361, 6))


AIFS_VARIABLE_CATALOG = {
    var_key: _capability_from_var_spec(var_key, var_spec)
    for var_key, var_spec in AIFS_VARS.items()
    if var_key not in {"10u", "10v", "u850", "v850", "hgt850", "u300", "v300", "hgt300"}
}

AIFS_VARIABLE_CATALOG["precip_total"] = replace(
    AIFS_VARIABLE_CATALOG["precip_total"],
    conversion="kgm2_to_in",
)

for _precip_anom_key, _precip_anom_fh in PRECIP_ANOM_TARGET_FH_BY_VAR_KEY.items():
    if _precip_anom_key in AIFS_VARIABLE_CATALOG:
        _precip_anom_constraint = {"min_fh": _precip_anom_fh}
        if _precip_anom_key in PRECIP_ANOM_STATIC_TARGET_FH_BY_VAR_KEY:
            _precip_anom_constraint["max_fh"] = _precip_anom_fh
        AIFS_VARIABLE_CATALOG[_precip_anom_key] = replace(
            AIFS_VARIABLE_CATALOG[_precip_anom_key],
            default_fh=_precip_anom_fh,
            constraints=_precip_anom_constraint,
            group="Anomalies",
            color_map_id="precip_anom",
        )

AIFS_VARIABLE_CATALOG["tmp2m_anom"] = replace(
    AIFS_VARIABLE_CATALOG["tmp2m_anom"],
    color_map_id="tmp2m_anom",
    default_fh=0,
    order=2,
    group="Temperature",
)

AIFS_VARS["pwat"] = replace(
    AIFS_VARS["pwat"],
    selectors=VarSelectors(
        search=[":tcw:", ":tcw:sfc:"],
        filter_by_keys={
            "shortName": "tcw",
            "typeOfLevel": "surface",
        },
        hints={
            "upstream_var": "tcw",
            "cf_var": "tcw",
            "short_name": "tcw",
        },
    ),
)

AIFS_VARS["hgt850"] = replace(
    AIFS_VARS["hgt850"],
    selectors=VarSelectors(
        search=[":z:850:pl:", ":z:850:"],
        filter_by_keys={
            "shortName": "z",
            "typeOfLevel": "isobaricInhPa",
            "level": "850",
        },
        hints={
            "upstream_var": "z850",
            "cf_var": "z",
            "short_name": "z",
        },
    ),
)

AIFS_VARS["wspd850"] = replace(
    AIFS_VARS["wspd850"],
    selectors=replace(
        AIFS_VARS["wspd850"].selectors,
        hints={
            **AIFS_VARS["wspd850"].selectors.hints,
            "contour_conversion": "geopotential_to_height_m",
        },
    ),
)

AIFS_VARS["hgt300"] = replace(
    AIFS_VARS["hgt300"],
    selectors=VarSelectors(
        search=[":z:300:pl:", ":z:300:"],
        filter_by_keys={
            "shortName": "z",
            "typeOfLevel": "isobaricInhPa",
            "level": "300",
        },
        hints={
            "upstream_var": "z300",
            "cf_var": "z",
            "short_name": "z",
        },
    ),
)

AIFS_VARS["wspd300"] = replace(
    AIFS_VARS["wspd300"],
    selectors=replace(
        AIFS_VARS["wspd300"].selectors,
        hints={
            **AIFS_VARS["wspd300"].selectors.hints,
            "contour_conversion": "geopotential_to_height_m",
        },
    ),
)

AIFS_VARS["hgt500"] = replace(
    AIFS_VARS["hgt500"],
    selectors=VarSelectors(
        search=[":z:500:pl:", ":z:500:"],
        filter_by_keys={
            "shortName": "z",
            "typeOfLevel": "isobaricInhPa",
            "level": "500",
        },
        hints={
            "upstream_var": "z500",
            "cf_var": "z",
            "short_name": "z",
        },
    ),
)

AIFS_VARS["hgt500_anom"] = replace(
    AIFS_VARS["hgt500_anom"],
    selectors=replace(
        AIFS_VARS["hgt500_anom"].selectors,
        hints={
            **AIFS_VARS["hgt500_anom"].selectors.hints,
            "contour_conversion": "geopotential_to_height_dam",
        },
    ),
)

AIFS_VARIABLE_CATALOG["pwat"] = replace(
    AIFS_VARIABLE_CATALOG["pwat"],
    selectors=AIFS_VARS["pwat"].selectors,
)

AIFS_VARIABLE_CATALOG["wspd850"] = replace(
    AIFS_VARIABLE_CATALOG["wspd850"],
    selectors=AIFS_VARS["wspd850"].selectors,
)

AIFS_VARIABLE_CATALOG["wspd300"] = replace(
    AIFS_VARIABLE_CATALOG["wspd300"],
    selectors=AIFS_VARS["wspd300"].selectors,
)

AIFS_VARIABLE_CATALOG["hgt500"] = replace(
    _capability_from_var_spec("hgt500", AIFS_VARS["hgt500"]),
    buildable=False,
    frontend={"internal_only": True},
)

AIFS_VARIABLE_CATALOG["hgt500_anom"] = replace(
    _capability_from_var_spec("hgt500_anom", AIFS_VARS["hgt500_anom"]),
    selectors=AIFS_VARS["hgt500_anom"].selectors,
)

AIFS_VARIABLE_CATALOG["snowfall_total"] = replace(
    AIFS_VARIABLE_CATALOG["snowfall_total"],
    conversion="kgm2_swe_to_in_10to1",
)


AIFS_CAPABILITIES = ModelCapabilities(
    model_id="aifs",
    name="AIFS",
    product="oper",
    canonical_region="na",
    grid_meters_by_region={
        "conus": 9_000.0,
        "na": 9_000.0,
    },
    run_discovery={
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 6],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 6,
        "source_priority": ["azure", "aws", "ecmwf"],
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
    variable_catalog=AIFS_VARIABLE_CATALOG,
)


AIFS_MODEL = AIFSPlugin(
    id="aifs",
    name="AIFS",
    regions=ECMWF_REGIONS,
    vars=AIFS_VARS,
    product="oper",
    capabilities=AIFS_CAPABILITIES,
)
