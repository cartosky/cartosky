"""ECMWF AIFS model plugin.

Initial rollout scope:
  - AIFS `oper`
      - `tmp2m`
      - `dp2m`
      - `precip_total`
      - `wspd10m`
  - realtime publishing only

Herbie wiring:
    - model = "aifs"
    - product = "oper"
"""

from __future__ import annotations

from dataclasses import replace

from .base import HerbieRequest, ModelCapabilities
from .ecmwf import ECMWFPlugin, ECMWF_REGIONS, ECMWF_VARS, _capability_from_var_spec


class AIFSPlugin(ECMWFPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return list(AIFS_OPER_FHS)

    def herbie_request(
        self,
        *,
        product: str | None = None,
        var_key: str | None = None,
        run_date=None,
        fh: int | None = None,
        search_pattern: str | None = None,
    ) -> HerbieRequest:
        base_request = super(ECMWFPlugin, self).herbie_request(
            product=product,
            var_key=var_key,
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
    "dp2m": ECMWF_VARS["dp2m"],
    "precip_total": ECMWF_VARS["precip_total"],
    "snowfall_total": ECMWF_VARS["snowfall_total"],
    "10u": ECMWF_VARS["10u"],
    "10v": ECMWF_VARS["10v"],
    "wspd10m": ECMWF_VARS["wspd10m"],
}


AIFS_OPER_FHS = list(range(0, 361, 6))


AIFS_VARIABLE_CATALOG = {
    var_key: _capability_from_var_spec(var_key, var_spec)
    for var_key, var_spec in AIFS_VARS.items()
    if var_key not in {"10u", "10v"}
}

AIFS_VARIABLE_CATALOG["precip_total"] = replace(
    AIFS_VARIABLE_CATALOG["precip_total"],
    conversion="kgm2_to_in",
)


AIFS_CAPABILITIES = ModelCapabilities(
    model_id="aifs",
    name="AIFS",
    product="oper",
    canonical_region="conus",
    grid_meters_by_region={
        "conus": 9_000.0,
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
        "canonical_region": "conus",
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