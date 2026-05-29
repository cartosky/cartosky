"""ECMWF EPS model plugin.

Initial rollout scope:
    - EPS `enfo`
            - `tmp2m` with `ensemble_view=mean`
            - `wspd10m` with `ensemble_view=mean`
  - realtime publishing only

Herbie wiring:
  - model = "ifs"
  - product = "enfo"
  - aggregation = pf member mean
"""

from __future__ import annotations

from dataclasses import replace

from .base import HerbieRequest, ModelCapabilities, VarSelectors, VariableCapability
from .ecmwf import ECMWFPlugin, ECMWF_REGIONS, ECMWF_VARS
from .gfs import PRECIP_ANOM_360_STATIC_TARGET_FH_BY_VAR_KEY, _precip_anomaly_var_spec


EPS_FHS_SYNOPTIC = list(range(0, 361, 6))
EPS_FHS_OFF_CYCLE = list(range(0, 145, 6))


class EPSPlugin(ECMWFPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        if cycle_hour in {0, 12}:
            return list(EPS_FHS_SYNOPTIC)
        return list(EPS_FHS_OFF_CYCLE)

    def normalize_var_id(self, var_id: str) -> str:
        normalized = str(var_id).strip().lower()
        aliases = {
            "tmp2m__mean": "tmp2m__mean",
            "rh700": "rh700",
            "rh700__mean": "rh700__mean",
            "r700": "rh700",
            "700rh": "rh700",
            "700mb_rh": "rh700",
            "rh_700mb": "rh700",
            "700mb_relative_humidity": "rh700",
            "tmp2m_anom": "tmp2m_anom",
            "t2m_anom": "tmp2m_anom",
            "2m_temp_anom": "tmp2m_anom",
            "surface_temp_anom": "tmp2m_anom",
            "temperature_anomaly": "tmp2m_anom",
            "tmp2m_anom__mean": "tmp2m_anom__mean",
            "tmp850": "tmp850",
            "tmp850__mean": "tmp850__mean",
            "t850": "tmp850",
            "t850mb": "tmp850",
            "temp850": "tmp850",
            "temp850mb": "tmp850",
            "tmp850_anom": "tmp850_anom",
            "tmp850_anom__mean": "tmp850_anom__mean",
            "t850_anom": "tmp850_anom",
            "850mb_temp_anom": "tmp850_anom",
            "temp850_anom": "tmp850_anom",
            "temp850mb_anom": "tmp850_anom",
            "hgt500": "hgt500__mean",
            "z500": "hgt500__mean",
            "500hgt": "hgt500__mean",
            "500_height": "hgt500__mean",
            "500mb_height": "hgt500__mean",
            "500mb_heights": "hgt500__mean",
            "500_heights": "hgt500__mean",
            "hgt500__mean": "hgt500__mean",
            "hgt500_anom": "hgt500_anom",
            "hgt500_anom__mean": "hgt500_anom__mean",
            "precip_15d_anom": "precip_15d_anom",
            "precip_15d_anom__mean": "precip_15d_anom__mean",
            "wspd10m": "wspd10m",
            "wspd10m__mean": "wspd10m__mean",
            "wind10m": "wspd10m",
            "10mwind": "wspd10m",
            "10u": "10u__mean",
            "u10": "10u__mean",
            "10v": "10v__mean",
            "v10": "10v__mean",
        }
        if normalized in aliases:
            return aliases[normalized]
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
        if runtime_var in {"hgt500__mean"}:
            herbie_kwargs["_cartosky_fetch_aggregation"] = "ecmwf_direct_mean_or_pf_mean"
        elif runtime_var in {"tmp2m__mean", "tmp850__mean", "tmp850_anom__mean", "rh700__mean", "10u__mean", "10v__mean", "precip_total__mean"}:
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
    "rh700": replace(
        ECMWF_VARS["rh700"],
        name="700mb Relative Humidity (Mean)",
    ),
    "rh700__mean": replace(
        ECMWF_VARS["rh700"],
        id="rh700__mean",
        name="700mb Relative Humidity (Mean)",
    ),
    "tmp2m_anom": replace(
        ECMWF_VARS["tmp2m_anom"],
        id="tmp2m_anom",
        name="Surface Temperature Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "tmp2m__mean",
                "baseline_field": "tmp2m",
                "baseline_source": "era5",
                "legacy_baseline_model_family": "gefs",
                "baseline_region": "na",
                "baseline_version": "v1",
                "reference_period": "1991-2020",
            }
        ),
        primary=True,
        derived=True,
        derive="anomaly_departure",
        kind="continuous",
        units="F",
    ),
    "tmp2m_anom__mean": replace(
        ECMWF_VARS["tmp2m_anom"],
        id="tmp2m_anom__mean",
        name="Surface Temperature Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "tmp2m__mean",
                "baseline_field": "tmp2m",
                "baseline_source": "era5",
                "legacy_baseline_model_family": "gefs",
                "baseline_region": "na",
                "baseline_version": "v1",
                "reference_period": "1991-2020",
            }
        ),
        primary=True,
        derived=True,
        derive="anomaly_departure",
        kind="continuous",
        units="F",
    ),
    "hgt500__mean": replace(
        ECMWF_VARS["hgt500"],
        id="hgt500__mean",
        name="500mb Height (Mean)",
    ),
    "hgt500_anom": replace(
        ECMWF_VARS["hgt500"],
        id="hgt500_anom",
        name="500mb Height Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "hgt500__mean",
                "baseline_field": "hgt500",
                "baseline_source": "era5",
                "legacy_baseline_model_family": "gefs",
                "baseline_region": "na",
                "baseline_version": "v1",
                "reference_period": "1991-2020",
                "contour_component": "hgt500__mean",
                "contour_conversion": "m_to_dam",
                "contour_interval": "6",
                "contour_start": "480",
                "contour_end": "624",
                "contour_key": "height_500mb",
                "contour_label": "500 mb Height",
            }
        ),
        primary=True,
        derived=True,
        derive="anomaly_departure",
        kind="continuous",
        units="dam",
    ),
    "hgt500_anom__mean": replace(
        ECMWF_VARS["hgt500"],
        id="hgt500_anom__mean",
        name="500mb Height Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "hgt500__mean",
                "baseline_field": "hgt500",
                "baseline_source": "era5",
                "legacy_baseline_model_family": "gefs",
                "baseline_region": "na",
                "baseline_version": "v1",
                "reference_period": "1991-2020",
                "contour_component": "hgt500__mean",
                "contour_conversion": "m_to_dam",
                "contour_interval": "6",
                "contour_start": "480",
                "contour_end": "624",
                "contour_key": "height_500mb",
                "contour_label": "500 mb Height",
            }
        ),
        primary=True,
        derived=True,
        derive="anomaly_departure",
        kind="continuous",
        units="dam",
    ),
    "tmp850": replace(
        ECMWF_VARS["tmp850"],
        name="850mb Temp (Mean)",
        primary=False,
    ),
    "tmp850__mean": replace(
        ECMWF_VARS["tmp850"],
        id="tmp850__mean",
        name="850mb Temp (Mean)",
    ),
    "tmp850_anom": replace(
        ECMWF_VARS["tmp850_anom"],
        id="tmp850_anom",
        name="850mb Temperature Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "tmp850__mean",
                "base_conversion": "c_to_f",
                "baseline_field": "tmp850",
                "baseline_source": "era5",
                "legacy_baseline_model_family": "gefs",
                "baseline_region": "na",
                "baseline_version": "v1",
                "reference_period": "1991-2020",
            }
        ),
        primary=True,
        derived=True,
        derive="anomaly_departure",
        kind="continuous",
        units="F",
    ),
    "tmp850_anom__mean": replace(
        ECMWF_VARS["tmp850_anom"],
        id="tmp850_anom__mean",
        name="850mb Temperature Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "tmp850__mean",
                "base_conversion": "c_to_f",
                "baseline_field": "tmp850",
                "baseline_source": "era5",
                "legacy_baseline_model_family": "gefs",
                "baseline_region": "na",
                "baseline_version": "v1",
                "reference_period": "1991-2020",
            }
        ),
        primary=True,
        derived=True,
        derive="anomaly_departure",
        kind="continuous",
        units="F",
    ),
    "precip_total__mean": replace(
        ECMWF_VARS["precip_total"],
        id="precip_total__mean",
        name="Total Precip (Mean)",
    ),
    "10u__mean": replace(
        ECMWF_VARS["10u"],
        id="10u__mean",
        name="10m U Wind (Mean)",
    ),
    "10v__mean": replace(
        ECMWF_VARS["10v"],
        id="10v__mean",
        name="10m V Wind (Mean)",
    ),
    "wspd10m": replace(
        ECMWF_VARS["wspd10m"],
        name="10m Wind Speed (Mean)",
        selectors=VarSelectors(
            hints={
                "u_component": "10u__mean",
                "v_component": "10v__mean",
            }
        ),
    ),
    "wspd10m__mean": replace(
        ECMWF_VARS["wspd10m"],
        id="wspd10m__mean",
        name="10m Wind Speed (Mean)",
        selectors=VarSelectors(
            hints={
                "u_component": "10u__mean",
                "v_component": "10v__mean",
            }
        ),
    ),
    "precip_15d_anom": replace(
        _precip_anomaly_var_spec(
            "precip_15d_anom",
            15,
            PRECIP_ANOM_360_STATIC_TARGET_FH_BY_VAR_KEY.get("precip_15d_anom"),
            base_component="precip_total__mean",
        ),
        name="15-Day Precip Anomaly",
    ),
    "precip_15d_anom__mean": replace(
        _precip_anomaly_var_spec(
            "precip_15d_anom__mean",
            15,
            PRECIP_ANOM_360_STATIC_TARGET_FH_BY_VAR_KEY.get("precip_15d_anom"),
            base_component="precip_total__mean",
        ),
        id="precip_15d_anom__mean",
        name="15-Day Precip Anomaly",
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
    "tmp2m_anom": VariableCapability(
        var_key="tmp2m_anom",
        name=EPS_VARS["tmp2m_anom"].name,
        selectors=EPS_VARS["tmp2m_anom"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="F",
        color_map_id="tmp2m_anom",
        default_fh=0,
        buildable=True,
        order=2,
        group="Temperature",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "tmp2m_anom__mean"},
        },
    ),
    "tmp2m_anom__mean": VariableCapability(
        var_key="tmp2m_anom__mean",
        name=EPS_VARS["tmp2m_anom__mean"].name,
        selectors=EPS_VARS["tmp2m_anom__mean"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="F",
        color_map_id="tmp2m_anom",
        default_fh=0,
        buildable=False,
        order=2,
        group="Temperature",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "rh700": VariableCapability(
        var_key="rh700",
        name=EPS_VARS["rh700"].name,
        selectors=EPS_VARS["rh700"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="%",
        color_map_id="rh",
        default_fh=0,
        buildable=True,
        order=2.75,
        group="Moisture",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "rh700__mean"},
        },
    ),
    "rh700__mean": VariableCapability(
        var_key="rh700__mean",
        name=EPS_VARS["rh700__mean"].name,
        selectors=EPS_VARS["rh700__mean"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="%",
        color_map_id="rh",
        default_fh=0,
        buildable=False,
        order=2.75,
        group="Moisture",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "hgt500_anom": VariableCapability(
        var_key="hgt500_anom",
        name=EPS_VARS["hgt500_anom"].name,
        selectors=EPS_VARS["hgt500_anom"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="dam",
        color_map_id="hgt500_anom",
        default_fh=0,
        buildable=True,
        order=3,
        group="Dynamics",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "hgt500_anom__mean"},
        },
    ),
    "hgt500_anom__mean": VariableCapability(
        var_key="hgt500_anom__mean",
        name=EPS_VARS["hgt500_anom__mean"].name,
        selectors=EPS_VARS["hgt500_anom__mean"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="dam",
        color_map_id="hgt500_anom",
        default_fh=0,
        buildable=False,
        order=3,
        group="Dynamics",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "hgt500__mean": VariableCapability(
        var_key="hgt500__mean",
        name=EPS_VARS["hgt500__mean"].name,
        selectors=EPS_VARS["hgt500__mean"].selectors,
        primary=False,
        derived=False,
        kind="continuous",
        units="dam",
        color_map_id="hgt500_anom",
        default_fh=0,
        buildable=False,
        order=3,
        group="Dynamics",
        conversion="m_to_dam",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "tmp850__mean": VariableCapability(
        var_key="tmp850__mean",
        name=EPS_VARS["tmp850__mean"].name,
        selectors=EPS_VARS["tmp850__mean"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="C",
        color_map_id="tmp850",
        default_fh=0,
        buildable=False,
        order=3,
        group="Temperature",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "tmp850_anom": VariableCapability(
        var_key="tmp850_anom",
        name=EPS_VARS["tmp850_anom"].name,
        selectors=EPS_VARS["tmp850_anom"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="F",
        color_map_id="tmp850_anom",
        default_fh=0,
        buildable=True,
        order=3.5,
        group="Temperature",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "tmp850_anom__mean"},
        },
    ),
    "tmp850_anom__mean": VariableCapability(
        var_key="tmp850_anom__mean",
        name=EPS_VARS["tmp850_anom__mean"].name,
        selectors=EPS_VARS["tmp850_anom__mean"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="F",
        color_map_id="tmp850_anom",
        default_fh=0,
        buildable=False,
        order=3.5,
        group="Temperature",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "precip_total__mean": VariableCapability(
        var_key="precip_total__mean",
        name=EPS_VARS["precip_total__mean"].name,
        selectors=EPS_VARS["precip_total__mean"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="in",
        color_map_id="precip_total",
        default_fh=6,
        buildable=False,
        order=10,
        group="Precipitation",
        conversion="m_to_in",
        frontend={"internal_only": True},
        constraints={"min_fh": 6},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "precip_15d_anom": VariableCapability(
        var_key="precip_15d_anom",
        name=EPS_VARS["precip_15d_anom"].name,
        selectors=EPS_VARS["precip_15d_anom"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="precip_accum_anomaly_departure",
        kind="continuous",
        units="in",
        color_map_id="precip_anom",
        default_fh=360,
        buildable=True,
        order=10.4,
        group="Anomalies",
        constraints={"min_fh": 360, "max_fh": 360},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "precip_15d_anom__mean"},
        },
    ),
    "precip_15d_anom__mean": VariableCapability(
        var_key="precip_15d_anom__mean",
        name=EPS_VARS["precip_15d_anom__mean"].name,
        selectors=EPS_VARS["precip_15d_anom__mean"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="precip_accum_anomaly_departure",
        kind="continuous",
        units="in",
        color_map_id="precip_anom",
        default_fh=360,
        buildable=False,
        order=10.4,
        group="Anomalies",
        frontend={"internal_only": True},
        constraints={"min_fh": 360, "max_fh": 360},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "wspd10m": VariableCapability(
        var_key="wspd10m",
        name=EPS_VARS["wspd10m"].name,
        selectors=EPS_VARS["wspd10m"].selectors,
        primary=False,
        derived=True,
        derive_strategy_id="wspd10m",
        kind="continuous",
        units="mph",
        color_map_id="wspd10m",
        default_fh=0,
        buildable=True,
        order=12,
        group="Wind",
        conversion="ms_to_mph",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "wspd10m__mean"},
        },
    ),
    "wspd10m__mean": VariableCapability(
        var_key="wspd10m__mean",
        name=EPS_VARS["wspd10m__mean"].name,
        selectors=EPS_VARS["wspd10m__mean"].selectors,
        primary=False,
        derived=True,
        derive_strategy_id="wspd10m",
        kind="continuous",
        units="mph",
        color_map_id="wspd10m",
        default_fh=0,
        buildable=False,
        order=12,
        group="Wind",
        conversion="ms_to_mph",
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
    canonical_region="na",
    grid_meters_by_region={
        "conus": 18_000.0,
        "na": 18_000.0,
    },
    run_discovery={
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 6],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 6,
        "stale_cycle_release_minutes_by_hour": {0: 150, 6: 90, 12: 150, 18: 90},
        "source_priority": ["azure", "aws", "ecmwf"],
        "probe_ensemble_view": "mean",
    },
    ui_defaults={
        "default_var_key": "tmp2m",
        "default_run": "latest",
        "default_ensemble_view": "mean",
    },
    ui_constraints={
        "canonical_region": "na",
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
