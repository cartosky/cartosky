"""NOAA GEFS model plugin.

Initial rollout scope:
    - GEFS `atmos.5`
    - `tmp2m` with `ensemble_view=mean`
    - `pwat` with `ensemble_view=mean`
    - `precip_total` with `ensemble_view=mean`
  - realtime publishing only

Herbie wiring:
  - model = "gefs"
    - product = "atmos.5"
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


GEFS_FHS = tuple(range(0, 361, 6))


class GEFSPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return list(GEFS_FHS)

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        aliases = {
            "tmp2m": "tmp2m",
            "tmp2m__mean": "tmp2m__mean",
            "pwat": "pwat",
            "pwat__mean": "pwat__mean",
            "precipitable_water": "pwat",
            "precipitablewater": "pwat",
            "precip_total": "precip_total",
            "precip_total__mean": "precip_total__mean",
            "total_precip": "precip_total",
            "apcp": "precip_total",
            "qpf": "precip_total",
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
        resolved_product = "atmos.5"
        herbie_kwargs = dict(base_request.herbie_kwargs)
        if runtime_var in {"tmp2m__mean", "pwat__mean", "apcp_step__mean", "precip_total__mean"}:
            herbie_kwargs["member"] = "mean"
        return HerbieRequest(
            model="gefs",
            product=resolved_product,
            herbie_kwargs=herbie_kwargs,
        )


GEFS_VARS: dict[str, VarSpec] = {
    "tmp2m": replace(
        GFS_VARS["tmp2m"],
        name="Surface Temp (Mean)",
    ),
    "tmp2m__mean": replace(
        GFS_VARS["tmp2m"],
        id="tmp2m__mean",
        name="Surface Temp (Mean)",
    ),
    "pwat": replace(
        GFS_VARS["pwat"],
        name="Precipitable Water (Mean)",
    ),
    "pwat__mean": replace(
        GFS_VARS["pwat"],
        id="pwat__mean",
        name="Precipitable Water (Mean)",
    ),
    "apcp_step__mean": VarSpec(
        id="apcp_step__mean",
        name="APCP Step (Mean)",
        selectors=VarSelectors(
            search=[
                r":APCP:surface:[0-9]+-[0-9]+ hour acc[^:]*:ens mean:",
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
    "precip_total": replace(
        GFS_VARS["precip_total"],
        selectors=VarSelectors(
            hints={
                "apcp_component": "apcp_step__mean",
                "step_hours": "6",
            },
        ),
        name="Total Precip (Mean)",
    ),
    "precip_total__mean": replace(
        GFS_VARS["precip_total"],
        id="precip_total__mean",
        selectors=VarSelectors(
            hints={
                "apcp_component": "apcp_step__mean",
                "step_hours": "6",
            },
        ),
        name="Total Precip (Mean)",
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
    "pwat": VariableCapability(
        var_key="pwat",
        name=GEFS_VARS["pwat"].name,
        selectors=GEFS_VARS["pwat"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="in",
        color_map_id="pwat",
        default_fh=0,
        buildable=True,
        order=9,
        group="Moisture",
        conversion="kgm2_to_in",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "pwat__mean"},
        },
    ),
    "pwat__mean": VariableCapability(
        var_key="pwat__mean",
        name=GEFS_VARS["pwat__mean"].name,
        selectors=GEFS_VARS["pwat__mean"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="in",
        color_map_id="pwat",
        default_fh=0,
        buildable=False,
        order=9,
        group="Moisture",
        conversion="kgm2_to_in",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "precip_total": VariableCapability(
        var_key="precip_total",
        name=GEFS_VARS["precip_total"].name,
        selectors=GEFS_VARS["precip_total"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="precip_total_cumulative",
        kind="continuous",
        units="in",
        color_map_id="precip_total",
        default_fh=6,
        buildable=True,
        order=10,
        group="Precipitation",
        conversion="kgm2_to_in",
        constraints={"min_fh": 6},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "precip_total__mean"},
        },
    ),
    "precip_total__mean": VariableCapability(
        var_key="precip_total__mean",
        name=GEFS_VARS["precip_total__mean"].name,
        selectors=GEFS_VARS["precip_total__mean"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="precip_total_cumulative",
        kind="continuous",
        units="in",
        color_map_id="precip_total",
        default_fh=6,
        buildable=False,
        order=10,
        group="Precipitation",
        conversion="kgm2_to_in",
        constraints={"min_fh": 6},
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
    product="atmos.5",
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
    product="atmos.5",
    capabilities=GEFS_CAPABILITIES,
)
