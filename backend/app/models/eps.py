"""ECMWF EPS model plugin.

Initial rollout scope:
  - EPS `enfo`
      - `tmp2m` with `ensemble_view=mean`
  - realtime publishing only

Herbie wiring:
  - model = "ifs"
  - product = "enfo"
  - aggregation = pf member mean
"""

from __future__ import annotations

from dataclasses import replace

from .base import HerbieRequest, ModelCapabilities, VariableCapability
from .ecmwf import ECMWFPlugin, ECMWF_REGIONS, ECMWF_VARS


EPS_FHS_SYNOPTIC = list(range(0, 361, 6))
EPS_FHS_OFF_CYCLE = list(range(0, 145, 6))


class EPSPlugin(ECMWFPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        if cycle_hour in {0, 12}:
            return list(EPS_FHS_SYNOPTIC)
        return list(EPS_FHS_OFF_CYCLE)

    def normalize_var_id(self, var_id: str) -> str:
        normalized = str(var_id).strip().lower()
        if normalized == "tmp2m__mean":
            return "tmp2m__mean"
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
        runtime_var = self.resolve_runtime_var_id(var_key or "", ensemble_view)
        herbie_kwargs = dict(base_request.herbie_kwargs)
        if runtime_var == "tmp2m__mean":
            herbie_kwargs["_cartosky_fetch_aggregation"] = "ecmwf_pf_mean"
        return HerbieRequest(
            model="ifs",
            product="enfo",
            herbie_kwargs=herbie_kwargs,
        )


EPS_VARS = {
    "tmp2m": replace(
        ECMWF_VARS["tmp2m"],
        name="Surface Temp (Mean)",
    ),
    "tmp2m__mean": replace(
        ECMWF_VARS["tmp2m"],
        id="tmp2m__mean",
        name="Surface Temp (Mean)",
    ),
}


EPS_VARIABLE_CATALOG = {
    "tmp2m": VariableCapability(
        var_key="tmp2m",
        name=EPS_VARS["tmp2m"].name,
        selectors=EPS_VARS["tmp2m"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="F",
        color_map_id="tmp2m",
        default_fh=0,
        buildable=True,
        order=1,
        group="Temperature",
        conversion="c_to_f",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "tmp2m__mean"},
        },
    ),
    "tmp2m__mean": VariableCapability(
        var_key="tmp2m__mean",
        name=EPS_VARS["tmp2m__mean"].name,
        selectors=EPS_VARS["tmp2m__mean"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="F",
        color_map_id="tmp2m",
        default_fh=0,
        buildable=False,
        order=1,
        group="Temperature",
        conversion="c_to_f",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
}


EPS_CAPABILITIES = ModelCapabilities(
    model_id="eps",
    name="EPS",
    product="enfo",
    canonical_region="conus",
    grid_meters_by_region={
        "conus": 18_000.0,
    },
    run_discovery={
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 6],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 6,
        "source_priority": ["azure", "aws", "ecmwf"],
        "probe_ensemble_view": "mean",
    },
    ui_defaults={
        "default_var_key": "tmp2m",
        "default_run": "latest",
        "default_ensemble_view": "mean",
    },
    ui_constraints={
        "canonical_region": "conus",
        "supports_sampling": True,
        "overlay_fade_out_zoom_start": 6,
        "overlay_fade_out_zoom_end": 7,
    },
    variable_catalog=EPS_VARIABLE_CATALOG,
    ensemble={
        "supported_views": ["mean"],
        "default_view": "mean",
    },
)


EPS_MODEL = EPSPlugin(
    id="eps",
    name="EPS",
    regions=ECMWF_REGIONS,
    vars=EPS_VARS,
    product="enfo",
    capabilities=EPS_CAPABILITIES,
)