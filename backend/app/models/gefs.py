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
from .gfs import (
    GFS_VARS,
    PRECIP_ANOM_384_STATIC_TARGET_FH_BY_VAR_KEY,
    PRECIP_ANOM_384_TARGET_FH_BY_VAR_KEY,
    _precip_anomaly_var_spec,
)


GEFS_REGIONS: dict[str, RegionSpec] = {
    "na": RegionSpec(
        id="na",
        name="North America",
        bbox_wgs84=(-178.0, 5.0, -25.0, 82.0),
        clip=True,
    ),
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
            "rh2m": "rh2m",
            "rh2m__mean": "rh2m__mean",
            "r2m": "rh2m",
            "2m_rh": "rh2m",
            "surface_rh": "rh2m",
            "surface_relative_humidity": "rh2m",
            "relative_humidity": "rh2m",
            "rh700": "rh700",
            "rh700__mean": "rh700__mean",
            "r700": "rh700",
            "700rh": "rh700",
            "700mb_rh": "rh700",
            "rh_700mb": "rh700",
            "700mb_relative_humidity": "rh700",
            "tmp2m_anom": "tmp2m_anom",
            "tmp2m_anom__mean": "tmp2m_anom__mean",
            "hgt500": "hgt500__mean",
            "hgt500__mean": "hgt500__mean",
            "hgt500_anom": "hgt500_anom",
            "hgt500_anom__mean": "hgt500_anom__mean",
            "tmp850": "tmp850",
            "tmp850__mean": "tmp850__mean",
            "tmp850_anom": "tmp850_anom",
            "tmp850_anom__mean": "tmp850_anom__mean",
            "t850_anom": "tmp850_anom",
            "850mb_temp_anom": "tmp850_anom",
            "temp850_anom": "tmp850_anom",
            "temp850mb_anom": "tmp850_anom",
            "t850": "tmp850",
            "t850mb": "tmp850",
            "temp850": "tmp850",
            "temp850mb": "tmp850",
            "wspd850": "wspd850",
            "wspd850__mean": "wspd850__mean",
            "wind850": "wspd850",
            "850wind": "wspd850",
            "850mbwind": "wspd850",
            "850mbwinds": "wspd850",
            "850_wind": "wspd850",
            "850_winds": "wspd850",
            "850mb_heights_winds": "wspd850",
            "850_heights_winds": "wspd850",
            "wspd300": "wspd300",
            "wspd300__mean": "wspd300__mean",
            "wind300": "wspd300",
            "300wind": "wspd300",
            "300mbwind": "wspd300",
            "300mbwinds": "wspd300",
            "300_wind": "wspd300",
            "300_winds": "wspd300",
            "300mb_heights_winds": "wspd300",
            "300_heights_winds": "wspd300",
            "sbcape": "sbcape",
            "sbcape__mean": "sbcape__mean",
            "snowfall_total": "snowfall_total",
            "snowfall_total__mean": "snowfall_total__mean",
            "asnow": "snowfall_total",
            "snow10": "snowfall_total",
            "snow_10to1": "snowfall_total",
            "total_snow": "snowfall_total",
            "totalsnow": "snowfall_total",
            "wspd10m": "wspd10m",
            "wspd10m__mean": "wspd10m__mean",
            "wind10m": "wspd10m",
            "10mwind": "wspd10m",
            "10u": "10u__mean",
            "u10": "10u__mean",
            "10v": "10v__mean",
            "v10": "10v__mean",
            "csnow": "csnow__mean",
            "pwat": "pwat",
            "pwat__mean": "pwat__mean",
            "precipitable_water": "pwat",
            "precipitablewater": "pwat",
            "precip_total": "precip_total",
            "precip_total__mean": "precip_total__mean",
            "total_precip": "precip_total",
            "apcp": "precip_total",
            "qpf": "precip_total",
            "precip_5d_anom": "precip_5d_anom",
            "precip_5d_anom__mean": "precip_5d_anom__mean",
            "precip_7d_anom": "precip_7d_anom",
            "precip_7d_anom__mean": "precip_7d_anom__mean",
            "precip_10d_anom": "precip_10d_anom",
            "precip_10d_anom__mean": "precip_10d_anom__mean",
            "precip_16d_anom": "precip_16d_anom",
            "precip_16d_anom__mean": "precip_16d_anom__mean",
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
        if runtime_var in {
            "tmp2m__mean",
            "rh2m__mean",
            "rh700__mean",
            "tmp850__mean",
            "hgt850__mean",
            "u850__mean",
            "v850__mean",
            "wspd850__mean",
            "hgt300__mean",
            "u300__mean",
            "v300__mean",
            "wspd300__mean",
            "sbcape__mean",
            "10u__mean",
            "10v__mean",
            "wspd10m__mean",
            "csnow__mean",
            "snowfall_total__mean",
            "pwat__mean",
            "apcp_step__mean",
            "precip_total__mean",
            "precip_5d_anom__mean",
            "precip_7d_anom__mean",
            "precip_10d_anom__mean",
            "precip_16d_anom__mean",
            "tmp2m_anom__mean",
            "tmp850_anom__mean",
            "hgt500__mean",
            "hgt500_anom__mean",
        }:
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
    "rh2m": replace(
        GFS_VARS["rh2m"],
        name="Surface Relative Humidity (Mean)",
    ),
    "rh2m__mean": replace(
        GFS_VARS["rh2m"],
        id="rh2m__mean",
        name="Surface Relative Humidity (Mean)",
    ),
    "rh700": replace(
        GFS_VARS["rh700"],
        name="700mb Relative Humidity (Mean)",
    ),
    "rh700__mean": replace(
        GFS_VARS["rh700"],
        id="rh700__mean",
        name="700mb Relative Humidity (Mean)",
    ),
    "tmp2m_anom": VarSpec(
        id="tmp2m_anom",
        name="Surface Temperature Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "tmp2m",
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
    "tmp2m_anom__mean": VarSpec(
        id="tmp2m_anom__mean",
        name="Surface Temperature Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "tmp2m",
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
        GFS_VARS["hgt500"],
        id="hgt500__mean",
        name="500mb Height (Mean)",
    ),
    "hgt500_anom": VarSpec(
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
                "anomaly_conversion": "dam_to_m",
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
        units="m",
    ),
    "hgt500_anom__mean": VarSpec(
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
                "anomaly_conversion": "dam_to_m",
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
        units="m",
    ),
    "tmp850": replace(
        GFS_VARS["tmp850"],
        name="850mb Temp (Mean)",
    ),
    "tmp850__mean": replace(
        GFS_VARS["tmp850"],
        id="tmp850__mean",
        name="850mb Temp (Mean)",
    ),
    "tmp850_anom": VarSpec(
        id="tmp850_anom",
        name="850mb Temperature Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "tmp850__mean",
                # ERA5 tmp850 baseline assets are stored in °F, so the
                # forecast is compared in °F and the delta rescaled to °C.
                "base_conversion": "c_to_f",
                "anomaly_conversion": "f_to_c_delta",
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
        units="C",
    ),
    "tmp850_anom__mean": VarSpec(
        id="tmp850_anom__mean",
        name="850mb Temperature Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "tmp850__mean",
                # ERA5 tmp850 baseline assets are stored in °F, so the
                # forecast is compared in °F and the delta rescaled to °C.
                "base_conversion": "c_to_f",
                "anomaly_conversion": "f_to_c_delta",
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
        units="C",
    ),
    "hgt850__mean": replace(
        GFS_VARS["hgt850"],
        id="hgt850__mean",
        name="850mb Height (Mean)",
    ),
    "u850__mean": replace(
        GFS_VARS["u850"],
        id="u850__mean",
        name="850mb U Wind (Mean)",
    ),
    "v850__mean": replace(
        GFS_VARS["v850"],
        id="v850__mean",
        name="850mb V Wind (Mean)",
    ),
    "wspd850": replace(
        GFS_VARS["wspd850"],
        name="850mb Heights + Winds (Mean)",
        selectors=VarSelectors(
            hints={
                "u_component": "u850__mean",
                "v_component": "v850__mean",
                "contour_component": "hgt850__mean",
                "contour_interval": "30",
                "contour_start": "900",
                "contour_end": "1800",
                "contour_key": "height_850mb",
                "contour_label": "850 mb Height",
            }
        ),
    ),
    "wspd850__mean": replace(
        GFS_VARS["wspd850"],
        id="wspd850__mean",
        name="850mb Heights + Winds (Mean)",
        selectors=VarSelectors(
            hints={
                "u_component": "u850__mean",
                "v_component": "v850__mean",
                "contour_component": "hgt850__mean",
                "contour_interval": "30",
                "contour_start": "900",
                "contour_end": "1800",
                "contour_key": "height_850mb",
                "contour_label": "850 mb Height",
            }
        ),
    ),
    "hgt300__mean": replace(
        GFS_VARS["hgt300"],
        id="hgt300__mean",
        name="300mb Height (Mean)",
    ),
    "u300__mean": replace(
        GFS_VARS["u300"],
        id="u300__mean",
        name="300mb U Wind (Mean)",
    ),
    "v300__mean": replace(
        GFS_VARS["v300"],
        id="v300__mean",
        name="300mb V Wind (Mean)",
    ),
    "wspd300": replace(
        GFS_VARS["wspd300"],
        name="300mb Heights + Winds (Mean)",
        selectors=VarSelectors(
            hints={
                "u_component": "u300__mean",
                "v_component": "v300__mean",
                "contour_component": "hgt300__mean",
                "contour_interval": "120",
                "contour_key": "height_300mb",
                "contour_label": "300 mb Height",
            }
        ),
    ),
    "wspd300__mean": replace(
        GFS_VARS["wspd300"],
        id="wspd300__mean",
        name="300mb Heights + Winds (Mean)",
        selectors=VarSelectors(
            hints={
                "u_component": "u300__mean",
                "v_component": "v300__mean",
                "contour_component": "hgt300__mean",
                "contour_interval": "120",
                "contour_key": "height_300mb",
                "contour_label": "300 mb Height",
            }
        ),
    ),
    "sbcape": replace(
        GFS_VARS["sbcape"],
        name="Surface-Based CAPE (Mean)",
        selectors=VarSelectors(
            search=[":CAPE:180-0 mb above ground:"],
            filter_by_keys={
                "shortName": "cape",
                "typeOfLevel": "pressureFromGroundLayer",
                "topLevel": "0",
                "bottomLevel": "180",
            },
            hints={
                "upstream_var": "sbcape",
                "cf_var": "cape",
                "short_name": "cape",
                "cape_layer": "180-0 mb above ground",
                "gefs_mean_mapping": "mapped_from_180-0_mb_above_ground",
            },
        ),
    ),
    "sbcape__mean": replace(
        GFS_VARS["sbcape"],
        id="sbcape__mean",
        name="Surface-Based CAPE (Mean)",
        selectors=VarSelectors(
            search=[":CAPE:180-0 mb above ground:"],
            filter_by_keys={
                "shortName": "cape",
                "typeOfLevel": "pressureFromGroundLayer",
                "topLevel": "0",
                "bottomLevel": "180",
            },
            hints={
                "upstream_var": "sbcape",
                "cf_var": "cape",
                "short_name": "cape",
                "cape_layer": "180-0 mb above ground",
                "gefs_mean_mapping": "mapped_from_180-0_mb_above_ground",
            },
        ),
    ),
    "10u__mean": replace(
        GFS_VARS["10u"],
        id="10u__mean",
        name="10m U Wind (Mean)",
    ),
    "10v__mean": replace(
        GFS_VARS["10v"],
        id="10v__mean",
        name="10m V Wind (Mean)",
    ),
    "csnow__mean": replace(
        GFS_VARS["csnow"],
        id="csnow__mean",
        name="Categorical Snow (Mean)",
    ),
    "wspd10m": replace(
        GFS_VARS["wspd10m"],
        name="10m Wind Speed (Mean)",
        selectors=VarSelectors(
            hints={
                "u_component": "10u__mean",
                "v_component": "10v__mean",
            }
        ),
    ),
    "wspd10m__mean": replace(
        GFS_VARS["wspd10m"],
        id="wspd10m__mean",
        name="10m Wind Speed (Mean)",
        selectors=VarSelectors(
            hints={
                "u_component": "10u__mean",
                "v_component": "10v__mean",
            }
        ),
    ),
    "snowfall_total": replace(
        GFS_VARS["snowfall_total"],
        name="Total Snowfall (10:1) (Mean)",
        selectors=VarSelectors(
            hints={
                "apcp_component": "apcp_step__mean",
                "precip_cumulative_component": "precip_total__mean",
                "snow_component": "csnow__mean",
                "step_hours": "6",
                "snow_interval_sample_mode": "step_endpoints",
                "skip_zero_hour_sample": "true",
                "slr": "10",
                "min_step_lwe_kgm2": "0.01",
            }
        ),
    ),
    "snowfall_total__mean": replace(
        GFS_VARS["snowfall_total"],
        id="snowfall_total__mean",
        name="Total Snowfall (10:1) (Mean)",
        selectors=VarSelectors(
            hints={
                "apcp_component": "apcp_step__mean",
                "precip_cumulative_component": "precip_total__mean",
                "snow_component": "csnow__mean",
                "step_hours": "6",
                "snow_interval_sample_mode": "step_endpoints",
                "skip_zero_hour_sample": "true",
                "slr": "10",
                "min_step_lwe_kgm2": "0.01",
            }
        ),
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

for _precip_anom_key, _precip_anom_fh in PRECIP_ANOM_384_TARGET_FH_BY_VAR_KEY.items():
    _days = int(_precip_anom_key.split("_", 2)[1].removesuffix("d"))
    GEFS_VARS[_precip_anom_key] = _precip_anomaly_var_spec(
        _precip_anom_key,
        _days,
        PRECIP_ANOM_384_STATIC_TARGET_FH_BY_VAR_KEY.get(_precip_anom_key),
        base_component="precip_total__mean",
    )
    GEFS_VARS[f"{_precip_anom_key}__mean"] = replace(
        GEFS_VARS[_precip_anom_key],
        id=f"{_precip_anom_key}__mean",
    )


def _gefs_precip_anomaly_capability(var_key: str, *, internal: bool = False) -> VariableCapability:
    public_key = var_key.removesuffix("__mean")
    target_fh = PRECIP_ANOM_384_TARGET_FH_BY_VAR_KEY[public_key]
    days = int(public_key.split("_", 2)[1].removesuffix("d"))
    constraints = {"min_fh": target_fh}
    if public_key in PRECIP_ANOM_384_STATIC_TARGET_FH_BY_VAR_KEY:
        constraints["max_fh"] = target_fh
    return VariableCapability(
        var_key=var_key,
        name="Precip Anomaly",
        selectors=GEFS_VARS[var_key].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="precip_accum_anomaly_departure",
        kind="continuous",
        units="in",
        color_map_id="precip_anom",
        default_fh=target_fh,
        buildable=not internal,
        order=10.0 + (days / 100.0),
        group="Anomalies",
        constraints=constraints,
        frontend={"internal_only": True} if internal else {},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            **({} if internal else {"artifact_map": {"mean": f"{public_key}__mean"}}),
        },
    )


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
            # Per-member slim publish (member pipeline plan Phase 3, design
            # R7): members are registered as metadata under the canonical var
            # — tmp2m__m01..m30 + tmp2m__control — never as catalog entries.
            # The scheduler member pass and the meteogram members probe both
            # enumerate from this descriptor. Publishing itself additionally
            # requires the model on CARTOSKY_MEMBER_PUBLISH_MODELS.
            "members": {"count": 30, "control": True, "prefix": "m", "enabled": True},
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
    "tmp2m_anom": VariableCapability(
        var_key="tmp2m_anom",
        name=GEFS_VARS["tmp2m_anom"].name,
        selectors=GEFS_VARS["tmp2m_anom"].selectors,
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
        name=GEFS_VARS["tmp2m_anom__mean"].name,
        selectors=GEFS_VARS["tmp2m_anom__mean"].selectors,
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
    "rh2m": VariableCapability(
        var_key="rh2m",
        name=GEFS_VARS["rh2m"].name,
        selectors=GEFS_VARS["rh2m"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="%",
        color_map_id="rh",
        default_fh=0,
        buildable=True,
        order=2.5,
        group="Moisture",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "rh2m__mean"},
        },
    ),
    "rh2m__mean": VariableCapability(
        var_key="rh2m__mean",
        name=GEFS_VARS["rh2m__mean"].name,
        selectors=GEFS_VARS["rh2m__mean"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="%",
        color_map_id="rh",
        default_fh=0,
        buildable=False,
        order=2.5,
        group="Moisture",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "rh700": VariableCapability(
        var_key="rh700",
        name=GEFS_VARS["rh700"].name,
        selectors=GEFS_VARS["rh700"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="%",
        color_map_id="rh",
        default_fh=0,
        buildable=True,
        order=3.75,
        group="Moisture",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "rh700__mean"},
        },
    ),
    "rh700__mean": VariableCapability(
        var_key="rh700__mean",
        name=GEFS_VARS["rh700__mean"].name,
        selectors=GEFS_VARS["rh700__mean"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="%",
        color_map_id="rh",
        default_fh=0,
        buildable=False,
        order=3.75,
        group="Moisture",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "hgt500__mean": VariableCapability(
        var_key="hgt500__mean",
        name=GEFS_VARS["hgt500__mean"].name,
        selectors=GEFS_VARS["hgt500__mean"].selectors,
        primary=True,
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
    "hgt500_anom": VariableCapability(
        var_key="hgt500_anom",
        name=GEFS_VARS["hgt500_anom"].name,
        selectors=GEFS_VARS["hgt500_anom"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="m",
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
        name=GEFS_VARS["hgt500_anom__mean"].name,
        selectors=GEFS_VARS["hgt500_anom__mean"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="m",
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
    "tmp850": VariableCapability(
        var_key="tmp850",
        name=GEFS_VARS["tmp850"].name,
        selectors=GEFS_VARS["tmp850"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="C",
        color_map_id="tmp850",
        default_fh=0,
        buildable=True,
        order=3,
        group="Temperature",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "tmp850__mean"},
        },
    ),
    "tmp850__mean": VariableCapability(
        var_key="tmp850__mean",
        name=GEFS_VARS["tmp850__mean"].name,
        selectors=GEFS_VARS["tmp850__mean"].selectors,
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
        name=GEFS_VARS["tmp850_anom"].name,
        selectors=GEFS_VARS["tmp850_anom"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="C",
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
        name=GEFS_VARS["tmp850_anom__mean"].name,
        selectors=GEFS_VARS["tmp850_anom__mean"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="C",
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
    "wspd850": VariableCapability(
        var_key="wspd850",
        name=GEFS_VARS["wspd850"].name,
        selectors=GEFS_VARS["wspd850"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="wspd10m",
        kind="continuous",
        units="kt",
        color_map_id="wspd850",
        default_fh=0,
        buildable=True,
        order=4,
        group="Wind",
        conversion="ms_to_kt",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "wspd850__mean"},
        },
    ),
    "wspd850__mean": VariableCapability(
        var_key="wspd850__mean",
        name=GEFS_VARS["wspd850__mean"].name,
        selectors=GEFS_VARS["wspd850__mean"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="wspd10m",
        kind="continuous",
        units="kt",
        color_map_id="wspd850",
        default_fh=0,
        buildable=False,
        order=4,
        group="Wind",
        conversion="ms_to_kt",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "wspd300": VariableCapability(
        var_key="wspd300",
        name=GEFS_VARS["wspd300"].name,
        selectors=GEFS_VARS["wspd300"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="wspd10m",
        kind="continuous",
        units="kt",
        color_map_id="wspd300",
        default_fh=0,
        buildable=True,
        order=999,
        group="Wind",
        conversion="ms_to_kt",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "wspd300__mean"},
        },
    ),
    "wspd300__mean": VariableCapability(
        var_key="wspd300__mean",
        name=GEFS_VARS["wspd300__mean"].name,
        selectors=GEFS_VARS["wspd300__mean"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="wspd10m",
        kind="continuous",
        units="kt",
        color_map_id="wspd300",
        default_fh=0,
        buildable=False,
        order=999,
        group="Wind",
        conversion="ms_to_kt",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "sbcape": VariableCapability(
        var_key="sbcape",
        name=GEFS_VARS["sbcape"].name,
        selectors=GEFS_VARS["sbcape"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="J/kg",
        color_map_id="mlcape",
        default_fh=0,
        buildable=True,
        order=6,
        group="Instability",
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "sbcape__mean"},
        },
    ),
    "sbcape__mean": VariableCapability(
        var_key="sbcape__mean",
        name=GEFS_VARS["sbcape__mean"].name,
        selectors=GEFS_VARS["sbcape__mean"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="J/kg",
        color_map_id="mlcape",
        default_fh=0,
        buildable=False,
        order=6,
        group="Instability",
        frontend={"internal_only": True},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
        },
    ),
    "wspd10m": VariableCapability(
        var_key="wspd10m",
        name=GEFS_VARS["wspd10m"].name,
        selectors=GEFS_VARS["wspd10m"].selectors,
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
        name=GEFS_VARS["wspd10m__mean"].name,
        selectors=GEFS_VARS["wspd10m__mean"].selectors,
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
    "snowfall_total": VariableCapability(
        var_key="snowfall_total",
        name=GEFS_VARS["snowfall_total"].name,
        selectors=GEFS_VARS["snowfall_total"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="snowfall_total_10to1_cumulative",
        kind="continuous",
        units="in",
        color_map_id="snowfall_total",
        default_fh=6,
        buildable=True,
        order=11,
        group="Precipitation",
        constraints={"min_fh": 6},
        ensemble={
            "supported_views": ["mean"],
            "default_view": "mean",
            "artifact_map": {"mean": "snowfall_total__mean"},
            # Per-member slim publish (design §12/D6): built by the member
            # pass's sequential cumulative loop from bundled APCP+CSNOW.
            "members": {"count": 30, "control": True, "prefix": "m", "enabled": True},
            # Tier 2 stats products (stats design §1/§3). Ships DISABLED:
            # rollout stage 6B — flip after the gefs precip_total first-run
            # acceptance budget (§10.7) is green. Thresholds are LOCKED plan
            # §4.2 values (display units, inches).
            "stats": {
                "percentiles": [10, 25, 50, 75, 90],
                "prob_thresholds": [1, 3, 6, 12],
                "label_noun": "snowfall",
                "enabled": False,
            },
        },
    ),
    "snowfall_total__mean": VariableCapability(
        var_key="snowfall_total__mean",
        name=GEFS_VARS["snowfall_total__mean"].name,
        selectors=GEFS_VARS["snowfall_total__mean"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="snowfall_total_10to1_cumulative",
        kind="continuous",
        units="in",
        color_map_id="snowfall_total",
        default_fh=6,
        buildable=False,
        order=11,
        group="Precipitation",
        constraints={"min_fh": 6},
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
            # Per-member slim publish (design §12/D6): built by the member
            # pass's sequential cumulative loop from bundled APCP steps.
            "members": {"count": 30, "control": True, "prefix": "m", "enabled": True},
            # Tier 2 stats products (stats design §1/§3): rollout stage 6A —
            # the first-enabled product set; the §10.7 acceptance budget runs
            # against this variable. Thresholds are LOCKED plan §4.2 values
            # (display units, inches). Publishing also requires the model on
            # CARTOSKY_STATS_PUBLISH_MODELS.
            "stats": {
                "percentiles": [10, 25, 50, 75, 90],
                "prob_thresholds": [0.10, 0.25, 0.50, 1.00, 1.50, 2.00],
                "label_noun": "precipitation",
                "enabled": True,
            },
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

for _precip_anom_key in PRECIP_ANOM_384_TARGET_FH_BY_VAR_KEY:
    GEFS_VARIABLE_CATALOG[_precip_anom_key] = _gefs_precip_anomaly_capability(_precip_anom_key)
    GEFS_VARIABLE_CATALOG[f"{_precip_anom_key}__mean"] = _gefs_precip_anomaly_capability(
        f"{_precip_anom_key}__mean",
        internal=True,
    )
GEFS_CAPABILITIES = ModelCapabilities(
    model_id="gefs",
    name="GEFS",
    product="atmos.5",
    canonical_region="na",
    grid_meters_by_region={
        "conus": 25_000.0,
        "na": 25_000.0,
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
        "canonical_region": "na",
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
