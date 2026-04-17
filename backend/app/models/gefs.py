"""NOAA GEFS model plugin.

Initial rollout scope:
  - GEFS `atmos.25`
      - `tmp2m` with `ensemble_view=mean`
  - realtime publishing only

Herbie wiring:
  - model = "gefs"
  - product = "atmos.25"
  - member = "mean"
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .base import BaseModelPlugin, HerbieRequest, ModelCapabilities, RegionSpec, VarSelectors, VarSpec, VariableCapability
from .gfs import GFS_VARS


GEFS_REGIONS: dict[str, RegionSpec] = {
    "conus": RegionSpec(
        id="conus",
        name="CONUS",
        bbox_wgs84=(-134.0, 24.0, -60.0, 55.0),
        clip=True,
    ),
}


GEFS_FHS = tuple(range(0, 385, 6))


class GEFSPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return list(GEFS_FHS)

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        aliases = {
            "tmp2m": "tmp2m",
            "tmp2m__mean": "tmp2m__mean",
            "t2m": "tmp2m",
            "2t": "tmp2m",
        }
        return aliases.get(normalized, normalized)

    def herbie_request(
        self,
        *,
        product: str | None = None,
        var_key: str | None = None,
        ensemble_view: str | None = None,
        run_date: datetime | None = None,
        fh: int | None = None,
        search_pattern: str | None = None,
    ) -> HerbieRequest:
        base_request = super().herbie_request(
            product=product,
            var_key=var_key,
            ensemble_view=ensemble_view,
            run_date=run_date,
            fh=fh,
            search_pattern=search_pattern,
        )
        runtime_var = self.resolve_runtime_var_id(var_key or "", ensemble_view)
        resolved_product = "atmos.25"
        herbie_kwargs = dict(base_request.herbie_kwargs)
        if runtime_var == "tmp2m__mean":
            herbie_kwargs["member"] = "mean"
        return HerbieRequest(
            model="gefs",
            product=resolved_product,
            herbie_kwargs=herbie_kwargs,
        )


GEFS_VARS: dict[str, VarSpec] = {
    "tmp2m": replace(
        GFS_VARS["tmp2m"],
        name="Surface Temp (Ensemble Mean)",
    ),
    "tmp2m__mean": replace(
        GFS_VARS["tmp2m"],
        id="tmp2m__mean",
        name="Surface Temp (Ensemble Mean)",
    ),
}


GEFS_VARIABLE_CATALOG = {
    "tmp2m": VariableCapability(
        var_key="tmp2m",
        name=GEFS_VARS["tmp2m"].name,
        selectors=GEFS_VARS["tmp2m"].selectors,
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
        name=GEFS_VARS["tmp2m__mean"].name,
        selectors=GEFS_VARS["tmp2m__mean"].selectors,
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


GEFS_CAPABILITIES = ModelCapabilities(
    model_id="gefs",
    name="GEFS",
    product="atmos.25",
    canonical_region="conus",
    grid_meters_by_region={
        "conus": 25_000.0,
    },
    run_discovery={
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 6],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 6,
        "source_priority": ["aws", "nomads", "google", "azure"],
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
    variable_catalog=GEFS_VARIABLE_CATALOG,
    ensemble={
        "supported_views": ["mean"],
        "default_view": "mean",
    },
)


GEFS_MODEL = GEFSPlugin(
    id="gefs",
    name="GEFS",
    regions=GEFS_REGIONS,
    vars=GEFS_VARS,
    product="atmos.25",
    capabilities=GEFS_CAPABILITIES,
)
