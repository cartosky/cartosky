"""ECMWF AIFS model plugin.

Initial rollout scope:
  - AIFS `oper`
      - `tmp2m`
  - realtime publishing only

Herbie wiring:
  - model = "aifs"
  - product = "oper"
"""

from __future__ import annotations

from .base import HerbieRequest, ModelCapabilities, VariableCapability
from .ecmwf import ECMWFPlugin, ECMWF_OPER_FHS, ECMWF_REGIONS, ECMWF_VARS


class AIFSPlugin(ECMWFPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return list(ECMWF_OPER_FHS)

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
}


AIFS_VARIABLE_CATALOG = {
    "tmp2m": VariableCapability(
        var_key="tmp2m",
        name="Surface Temp",
        selectors=AIFS_VARS["tmp2m"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="F",
        color_map_id="tmp2m",
        default_fh=0,
        buildable=True,
        order=1,
        group="Temperature",
    ),
}


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