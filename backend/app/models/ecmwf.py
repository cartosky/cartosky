"""ECMWF IFS model plugin.

Phase 1 rollout scope:
    - IFS `oper` with legacy `scda` support before IFS Cycle 50r1
            - `tmp2m`, `dp2m`, `tmp850`, `wspd10m`, `wspd850`, `wspd300`, `vort500`, `wgst10m`, `precip_total`, `ptype_intensity`, `mucape`, `pwat`, `snowfall_total`
  - realtime publishing only

Herbie wiring:
  - model = "ifs"
        - product = `oper`
        - product = `scda` for 06z/18z runs before 2026-05-12 06 UTC
"""

from __future__ import annotations

from datetime import datetime, timezone

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
from .gfs import (
    PRECIP_ANOM_360_STATIC_TARGET_FH_BY_VAR_KEY,
    PRECIP_ANOM_360_TARGET_FH_BY_VAR_KEY,
    PRECIP_ANOM_384_TARGET_FH_BY_VAR_KEY,
    _precip_anomaly_var_spec,
)


class ECMWFPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        if int(cycle_hour) in ECMWF_SHORT_CUTOFF_CYCLE_HOURS:
            return list(ECMWF_SCDA_FHS)
        return list(ECMWF_OPER_FHS)

    def _default_product_for_run(self, run_date: datetime | None) -> str:
        if run_date is None:
            return str(self.product)
        if self._uses_legacy_scda_stream(run_date):
            return "scda"
        return "oper"

    def _uses_legacy_scda_stream(self, run_date: datetime) -> bool:
        run_date_utc = run_date.astimezone(timezone.utc) if run_date.tzinfo else run_date.replace(tzinfo=timezone.utc)
        if int(run_date_utc.hour) not in ECMWF_SHORT_CUTOFF_CYCLE_HOURS:
            return False
        return run_date_utc < ECMWF_SCDA_RETIREMENT_RUN

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        aliases: dict[str, str] = {
            "tmp2m": "tmp2m",
            "tm2m": "tmp2m",
            "t2m": "tmp2m",
            "2t": "tmp2m",
            "tmp2m_anom": "tmp2m_anom",
            "t2m_anom": "tmp2m_anom",
            "2m_temp_anom": "tmp2m_anom",
            "surface_temp_anom": "tmp2m_anom",
            "temperature_anomaly": "tmp2m_anom",
            "dp2m": "dp2m",
            "d2m": "dp2m",
            "2d": "dp2m",
            "dpt2m": "dp2m",
            "dewpoint2m": "dp2m",
            "dewpoint": "dp2m",
            "rh2m": "rh2m",
            "r2m": "rh2m",
            "2m_rh": "rh2m",
            "surface_rh": "rh2m",
            "surface_relative_humidity": "rh2m",
            "relative_humidity": "rh2m",
            "rh700": "rh700",
            "r700": "rh700",
            "700rh": "rh700",
            "700mb_rh": "rh700",
            "rh_700mb": "rh700",
            "700mb_relative_humidity": "rh700",
            "tmp850": "tmp850",
            "t850": "tmp850",
            "t850mb": "tmp850",
            "temp850": "tmp850",
            "temp850mb": "tmp850",
            "tmp850_anom": "tmp850_anom",
            "t850_anom": "tmp850_anom",
            "850mb_temp_anom": "tmp850_anom",
            "temp850_anom": "tmp850_anom",
            "temp850mb_anom": "tmp850_anom",
            "wspd850": "wspd850",
            "wind850": "wspd850",
            "850wind": "wspd850",
            "850mbwind": "wspd850",
            "850mbwinds": "wspd850",
            "850_wind": "wspd850",
            "850_winds": "wspd850",
            "850mb_heights_winds": "wspd850",
            "850_heights_winds": "wspd850",
            "wspd300": "wspd300",
            "wind300": "wspd300",
            "300wind": "wspd300",
            "300mbwind": "wspd300",
            "300mbwinds": "wspd300",
            "300_wind": "wspd300",
            "300_winds": "wspd300",
            "300mb_heights_winds": "wspd300",
            "300_heights_winds": "wspd300",
            "hgt500": "hgt500",
            "500hgt": "hgt500",
            "500_hgt": "hgt500",
            "500mb_hgt": "hgt500",
            "500mb_height": "hgt500",
            "500_height": "hgt500",
            "hgt500_anom": "hgt500_anom",
            "500hgt_anom": "hgt500_anom",
            "500_hgt_anom": "hgt500_anom",
            "500mb_height_anom": "hgt500_anom",
            "height500_anom": "hgt500_anom",
            "vort500": "vort500",
            "500vort": "vort500",
            "500_vort": "vort500",
            "500mb_vort": "vort500",
            "500mb_vorticity": "vort500",
            "500_vorticity": "vort500",
            "precip_total": "precip_total",
            "total_precip": "precip_total",
            "apcp": "precip_total",
            "qpf": "precip_total",
            "total_qpf": "precip_total",
            "precip_5d_anom": "precip_5d_anom",
            "precip_7d_anom": "precip_7d_anom",
            "precip_10d_anom": "precip_10d_anom",
            "precip_15d_anom": "precip_15d_anom",
            "precip_16d_anom": "precip_15d_anom",
            "snowfall_total": "snowfall_total",
            "asnow": "snowfall_total",
            "snow10": "snowfall_total",
            "snow_10to1": "snowfall_total",
            "total_snow": "snowfall_total",
            "totalsnow": "snowfall_total",
            "snowfall_kuchera_total": "snowfall_kuchera_total",
            "snowkuchera": "snowfall_kuchera_total",
            "ice_total": "ice_total",
            "total_ice": "ice_total",
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
            "ptype_intensity": "ptype_intensity",
            "precip_ptype": "ptype_intensity",
            "ptype": "ptype_intensity",
            "ptypeintensity": "ptype_intensity",
            "10u": "10u",
            "u10": "10u",
            "10v": "10v",
            "v10": "10v",
            "msl": "msl",
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
        resolved_product = str(base_request.product).strip().lower()
        if resolved_product in {"", "oper", "scda"}:
            resolved_product = self._default_product_for_run(run_date)
        return HerbieRequest(
            model="ifs",
            product=resolved_product,
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
ECMWF_SCDA_FHS = list(range(0, 145, 3))
ECMWF_SHORT_CUTOFF_CYCLE_HOURS = {6, 18}
ECMWF_SCDA_RETIREMENT_RUN = datetime(2026, 5, 12, 6, tzinfo=timezone.utc)
ECMWF_PRECIP_ANOM_TARGET_FH_BY_VAR_KEY: dict[str, int] = {
    var_key: fh
    for var_key, fh in PRECIP_ANOM_384_TARGET_FH_BY_VAR_KEY.items()
    if var_key != "precip_16d_anom"
}
ECMWF_PRECIP_ANOM_TARGET_FH_BY_VAR_KEY.update(PRECIP_ANOM_360_TARGET_FH_BY_VAR_KEY)
ECMWF_PRECIP_ANOM_STATIC_TARGET_FH_BY_VAR_KEY: dict[str, int] = dict(PRECIP_ANOM_360_STATIC_TARGET_FH_BY_VAR_KEY)


ECMWF_REGIONS: dict[str, RegionSpec] = {
    "na": RegionSpec(
        id="na",
        name="North America",
        bbox_wgs84=(-178.0, 5.0, -25.0, 82.0),
        tile_matrix="WebMercatorQuad",
        clip=True,
    ),
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
    "rh2m": VarSpec(
        id="rh2m",
        name="Surface Relative Humidity",
        selectors=VarSelectors(
            hints={
                "temp_component": "tmp2m",
                "dewpoint_component": "dp2m",
                "temp_units": "c",
                "dewpoint_units": "c",
            }
        ),
        primary=True,
        derived=True,
        derive="relative_humidity_from_temp_dewpoint",
        kind="continuous",
        units="%",
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
    "ptype_intensity": VarSpec(
        id="ptype_intensity",
        name="Precipitation Type & Intensity",
        selectors=VarSelectors(
            hints={
                "display_kind": "ptype_intensity",
                "precip_component": "precip_total",
                "snow_component": "sf",
                "surface_temp_component": "tmp2m",
                "low_temp_component": "tmp925",
                "mid_temp_component": "tmp850",
                "step_hours": "3",
                "step_transition_fh": "144",
                "step_hours_after_fh": "6",
                "contour_component": "msl",
                "contour_interval": "4",
                "contour_start": "960",
                "contour_end": "1048",
                "contour_key": "mslp",
                "contour_label": "Mean Sea-Level Pressure",
                "contour_conversion": "pressure_pa_to_hpa",
                "center_radius_km": "900",
                "center_min_delta": "8",
                "center_min_separation_km": "1000",
                "center_max_count": "18",
                "center_skip_edge": "true",
                "companion_vars": "ptype_intensity_rain,ptype_intensity_snow,ptype_intensity_ice",
                "composite_mode": "max_alpha_stack",
                "composite_layers": "ice:ptype_intensity_ice;snow:ptype_intensity_snow;rain:ptype_intensity_rain",
            },
        ),
        primary=True,
        derived=True,
        derive="ptype_intensity_ecmwf",
        kind="indexed",
        units="in/hr",
        normalize_units="in/hr",
    ),
    "ptype_intensity_rain": VarSpec(
        id="ptype_intensity_rain",
        name="Precipitation Type Intensity Rain",
        selectors=VarSelectors(
            hints={
                "ptype_component": "rain",
                "precip_component": "precip_total",
                "snow_component": "sf",
                "surface_temp_component": "tmp2m",
                "low_temp_component": "tmp925",
                "mid_temp_component": "tmp850",
                "step_hours": "3",
                "step_transition_fh": "144",
                "step_hours_after_fh": "6",
            },
        ),
        derived=True,
        derive="ptype_intensity_component_ecmwf",
        kind="continuous",
        units="in/hr",
        normalize_units="in/hr",
    ),
    "ptype_intensity_snow": VarSpec(
        id="ptype_intensity_snow",
        name="Precipitation Type Intensity Snow",
        selectors=VarSelectors(
            hints={
                "ptype_component": "snow",
                "precip_component": "precip_total",
                "snow_component": "sf",
                "surface_temp_component": "tmp2m",
                "low_temp_component": "tmp925",
                "mid_temp_component": "tmp850",
                "step_hours": "3",
                "step_transition_fh": "144",
                "step_hours_after_fh": "6",
            },
        ),
        derived=True,
        derive="ptype_intensity_component_ecmwf",
        kind="continuous",
        units="in/hr",
        normalize_units="in/hr",
    ),
    "ptype_intensity_ice": VarSpec(
        id="ptype_intensity_ice",
        name="Precipitation Type Intensity Ice",
        selectors=VarSelectors(
            hints={
                "ptype_component": "ice",
                "precip_component": "precip_total",
                "snow_component": "sf",
                "surface_temp_component": "tmp2m",
                "low_temp_component": "tmp925",
                "mid_temp_component": "tmp850",
                "step_hours": "3",
                "step_transition_fh": "144",
                "step_hours_after_fh": "6",
            },
        ),
        derived=True,
        derive="ptype_intensity_component_ecmwf",
        kind="continuous",
        units="in/hr",
        normalize_units="in/hr",
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
    "ice_total": VarSpec(
        id="ice_total",
        name="Total Ice",
        selectors=VarSelectors(
            hints={
                "ptype_component": "ice",
                "precip_component": "precip_total",
                "snow_component": "sf",
                "surface_temp_component": "tmp2m",
                "low_temp_component": "tmp925",
                "mid_temp_component": "tmp850",
                "step_hours": "3",
                "step_transition_fh": "144",
                "step_hours_after_fh": "6",
            },
        ),
        primary=True,
        derived=True,
        derive="ptype_accumulation_ecmwf",
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
        primary=True,
        kind="continuous",
        units="C",
    ),
    "tmp850_anom": VarSpec(
        id="tmp850_anom",
        name="850mb Temperature Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "tmp850",
                "base_conversion": "c_to_f",
                "baseline_field": "tmp850",
                "baseline_source": "era5",
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
    "wspd850": VarSpec(
        id="wspd850",
        name="850mb Heights + Winds",
        selectors=VarSelectors(
            hints={
                "u_component": "u850",
                "v_component": "v850",
                "contour_component": "hgt850",
                "contour_interval": "30",
                "contour_start": "900",
                "contour_end": "1800",
                "contour_key": "height_850mb",
                "contour_label": "850 mb Height",
            }
        ),
        primary=True,
        derived=True,
        derive="wspd10m",
        kind="continuous",
        units="kt",
    ),
    "wspd300": VarSpec(
        id="wspd300",
        name="300mb Heights + Winds",
        selectors=VarSelectors(
            hints={
                "u_component": "u300",
                "v_component": "v300",
                "contour_component": "hgt300",
                "contour_interval": "120",
                "contour_key": "height_300mb",
                "contour_label": "300 mb Height",
            }
        ),
        primary=True,
        derived=True,
        derive="wspd10m",
        kind="continuous",
        units="kt",
    ),
    "hgt500_anom": VarSpec(
        id="hgt500_anom",
        name="500mb Height Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "hgt500",
                "baseline_field": "hgt500",
                "baseline_source": "era5",
                "baseline_region": "na",
                "baseline_version": "v1",
                "reference_period": "1991-2020",
                "contour_component": "hgt500",
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
    "tmp2m_anom": VarSpec(
        id="tmp2m_anom",
        name="Surface Temperature Anomaly",
        selectors=VarSelectors(
            hints={
                "base_component": "tmp2m",
                "baseline_field": "tmp2m",
                "baseline_source": "era5",
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
    "vort500": VarSpec(
        id="vort500",
        name="500mb Heights + Vorticity",
        selectors=VarSelectors(
            search=[":vo:500:", ":vo:500:pl:"],
            filter_by_keys={
                "shortName": "vo",
                "typeOfLevel": "isobaricInhPa",
                "level": "500",
            },
            hints={
                "upstream_var": "vo500",
                "cf_var": "vo",
                "short_name": "vo",
                "contour_component": "hgt500",
                "contour_interval": "60",
                "contour_start": "4800",
                "contour_end": "6240",
                "contour_key": "height_500mb",
                "contour_label": "500 mb Height",
            },
        ),
        primary=True,
        kind="continuous",
        units="10^-5 s^-1",
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
    "rh700": VarSpec(
        id="rh700",
        name="700mb Relative Humidity",
        selectors=VarSelectors(
            search=[":r:700:pl:"],
            filter_by_keys={
                "shortName": "r",
                "typeOfLevel": "isobaricInhPa",
                "level": "700",
            },
            hints={
                "upstream_var": "r700",
                "cf_var": "r",
                "short_name": "r",
            },
        ),
        primary=True,
        kind="continuous",
        units="%",
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
    "msl": VarSpec(
        id="msl",
        name="Mean Sea-Level Pressure",
        selectors=VarSelectors(
            search=[":msl:"],
            filter_by_keys={
                "shortName": "msl",
                "typeOfLevel": "meanSea",
            },
            hints={
                "upstream_var": "msl",
                "short_name": "msl",
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
    "u850": VarSpec(
        id="u850",
        name="850mb U Wind",
        selectors=VarSelectors(
            search=[":u:850:pl:"],
            filter_by_keys={
                "shortName": "u",
                "typeOfLevel": "isobaricInhPa",
                "level": "850",
            },
            hints={
                "upstream_var": "u850",
                "cf_var": "u",
                "short_name": "u",
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
    "v850": VarSpec(
        id="v850",
        name="850mb V Wind",
        selectors=VarSelectors(
            search=[":v:850:pl:"],
            filter_by_keys={
                "shortName": "v",
                "typeOfLevel": "isobaricInhPa",
                "level": "850",
            },
            hints={
                "upstream_var": "v850",
                "cf_var": "v",
                "short_name": "v",
            },
        ),
    ),
    "hgt850": VarSpec(
        id="hgt850",
        name="850mb Height",
        selectors=VarSelectors(
            search=[":gh:850:"],
            filter_by_keys={
                "shortName": "gh",
                "typeOfLevel": "isobaricInhPa",
                "level": "850",
            },
            hints={
                "upstream_var": "gh850",
                "cf_var": "gh",
                "short_name": "gh",
            },
        ),
    ),
    "u300": VarSpec(
        id="u300",
        name="300mb U Wind",
        selectors=VarSelectors(
            search=[":u:300:"],
            filter_by_keys={
                "shortName": "u",
                "typeOfLevel": "isobaricInhPa",
                "level": "300",
            },
            hints={
                "upstream_var": "u300",
                "cf_var": "u",
                "short_name": "u",
            },
        ),
    ),
    "v300": VarSpec(
        id="v300",
        name="300mb V Wind",
        selectors=VarSelectors(
            search=[":v:300:"],
            filter_by_keys={
                "shortName": "v",
                "typeOfLevel": "isobaricInhPa",
                "level": "300",
            },
            hints={
                "upstream_var": "v300",
                "cf_var": "v",
                "short_name": "v",
            },
        ),
    ),
    "hgt300": VarSpec(
        id="hgt300",
        name="300mb Height",
        selectors=VarSelectors(
            search=[":gh:300:"],
            filter_by_keys={
                "shortName": "gh",
                "typeOfLevel": "isobaricInhPa",
                "level": "300",
            },
            hints={
                "upstream_var": "gh300",
                "cf_var": "gh",
                "short_name": "gh",
            },
        ),
    ),
    "hgt500": VarSpec(
        id="hgt500",
        name="500mb Height",
        selectors=VarSelectors(
            search=[":gh:500:"],
            filter_by_keys={
                "shortName": "gh",
                "typeOfLevel": "isobaricInhPa",
                "level": "500",
            },
            hints={
                "upstream_var": "gh500",
                "cf_var": "gh",
                "short_name": "gh",
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

for _precip_anom_key, _precip_anom_fh in ECMWF_PRECIP_ANOM_TARGET_FH_BY_VAR_KEY.items():
    _days = int(_precip_anom_key.split("_", 2)[1].removesuffix("d"))
    ECMWF_VARS[_precip_anom_key] = _precip_anomaly_var_spec(
        _precip_anom_key,
        _days,
        ECMWF_PRECIP_ANOM_STATIC_TARGET_FH_BY_VAR_KEY.get(_precip_anom_key),
    )


ECMWF_COLOR_MAP_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "tmp2m",
    "tmp2m_anom": "tmp2m_anom",
    "dp2m": "dp2m",
    "rh2m": "rh",
    "rh700": "rh",
    "tmp850": "tmp850",
    "tmp850_anom": "tmp850_anom",
    "wspd850": "wspd850",
    "wspd300": "wspd300",
    "hgt500_anom": "hgt500_anom",
    "vort500": "vort500",
    "precip_total": "precip_total",
    "precip_5d_anom": "precip_anom",
    "precip_7d_anom": "precip_anom",
    "precip_10d_anom": "precip_anom",
    "precip_15d_anom": "precip_anom",
    "ptype_intensity": "ptype_intensity",
    "ptype_intensity_rain": "ptype_intensity_rain",
    "ptype_intensity_snow": "ptype_intensity_snow",
    "ptype_intensity_ice": "ptype_intensity_ice",
    "ice_total": "ice_total",
    "pwat": "pwat",
    "snowfall_total": "snowfall_total",
    "snowfall_kuchera_total": "snowfall_total",
    "wspd10m": "wspd10m",
    "wgst10m": "wgst10m",
    "mucape": "mlcape",
}

ECMWF_DEFAULT_FH_BY_VAR_KEY: dict[str, int] = {
    "tmp2m": 0,
    "tmp2m_anom": 0,
    "dp2m": 0,
    "rh2m": 0,
    "rh700": 0,
    "tmp850": 0,
    "tmp850_anom": 0,
    "wspd850": 0,
    "wspd300": 0,
    "hgt500_anom": 0,
    "vort500": 0,
    "precip_total": 3,
    "precip_5d_anom": 120,
    "precip_7d_anom": 168,
    "precip_10d_anom": 240,
    "precip_15d_anom": 360,
    "ptype_intensity": 6,
    "ptype_intensity_rain": 6,
    "ptype_intensity_snow": 6,
    "ptype_intensity_ice": 6,
    "ice_total": 6,
    "pwat": 0,
    "snowfall_total": 3,
    "snowfall_kuchera_total": 3,
    "wspd10m": 0,
    "wgst10m": 3,
    "mucape": 0,
}

ECMWF_ORDER_BY_VAR_KEY: dict[str, float] = {
    "tmp2m": 1,
    "tmp2m_anom": 2,
    "dp2m": 2,
    "rh2m": 2.5,
    "tmp850": 3,
    "tmp850_anom": 3.5,
    "rh700": 3.75,
    "wspd850": 4,
    "hgt500_anom": 5,
    "vort500": 5,
    "pwat": 9,
    "precip_total": 10,
    "precip_5d_anom": 10.1,
    "precip_7d_anom": 10.2,
    "precip_10d_anom": 10.3,
    "precip_15d_anom": 10.4,
    "snowfall_total": 11,
    "snowfall_kuchera_total": 14,
    "ice_total": 14.5,
    "ptype_intensity": 15,
    "wspd10m": 12,
    "wgst10m": 13,
    "wspd300": 999,
    "mucape": 20,
}

ECMWF_GROUP_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "Temperature",
    "tmp2m_anom": "Temperature",
    "dp2m": "Temperature",
    "rh2m": "Moisture",
    "rh700": "Moisture",
    "tmp850": "Temperature",
    "tmp850_anom": "Temperature",
    "wspd850": "Wind",
    "wspd300": "Wind",
    "hgt500_anom": "Dynamics",
    "vort500": "Dynamics",
    "pwat": "Moisture",
    "precip_total": "Precipitation",
    "precip_5d_anom": "Anomalies",
    "precip_7d_anom": "Anomalies",
    "precip_10d_anom": "Anomalies",
    "precip_15d_anom": "Anomalies",
    "ptype_intensity": "Precipitation",
    "snowfall_total": "Precipitation",
    "snowfall_kuchera_total": "Precipitation",
    "ice_total": "Precipitation",
    "wspd10m": "Wind",
    "wgst10m": "Wind",
    "mucape": "Instability",
}

ECMWF_CONVERSION_BY_VAR_KEY: dict[str, str] = {
    "tmp2m": "c_to_f",
    "dp2m": "c_to_f",
    "wspd850": "ms_to_kt",
    "wspd300": "ms_to_kt",
    "hgt500_anom": "m_to_dam",
    "vort500": "s-1_to_1e5s-1",
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
    "ptype_intensity": {
        "min_fh": 3,
    },
    "snowfall_total": {
        "min_fh": 3,
    },
    "snowfall_kuchera_total": {
        "min_fh": 3,
    },
    "ice_total": {
        "min_fh": 3,
    },
    "wgst10m": {
        "min_fh": 3,
    },
}

for _precip_anom_key, _precip_anom_fh in ECMWF_PRECIP_ANOM_TARGET_FH_BY_VAR_KEY.items():
    _precip_anom_constraint: dict[str, int] = {
        "min_fh": _precip_anom_fh,
    }
    if _precip_anom_key in ECMWF_PRECIP_ANOM_STATIC_TARGET_FH_BY_VAR_KEY:
        _precip_anom_constraint["max_fh"] = _precip_anom_fh
    ECMWF_CONSTRAINTS_BY_VAR_KEY[_precip_anom_key] = _precip_anom_constraint


def _capability_from_var_spec(var_key: str, var_spec: VarSpec) -> VariableCapability:
    is_buildable = bool(var_spec.primary or var_spec.derived)
    hints = getattr(getattr(var_spec, "selectors", None), "hints", {}) or {}
    frontend: dict[str, object] = {}
    if str(hints.get("companion_vars") or "").strip():
        frontend["companion_vars"] = [
            item.strip()
            for item in str(hints.get("companion_vars") or "").split(",")
            if item.strip()
        ]
    if str(hints.get("composite_mode") or "").strip():
        frontend["composite_mode"] = str(hints.get("composite_mode") or "").strip()
    if str(hints.get("composite_layers") or "").strip():
        frontend["composite_layers"] = str(hints.get("composite_layers") or "").strip()
    if str(var_key).startswith("ptype_intensity_"):
        frontend["internal_only"] = True
        frontend["allow_dry_frame"] = True
        is_buildable = False
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
        buildable=is_buildable,
        order=ECMWF_ORDER_BY_VAR_KEY.get(var_key),
        group=ECMWF_GROUP_BY_VAR_KEY.get(var_key),
        conversion=ECMWF_CONVERSION_BY_VAR_KEY.get(var_key),
        constraints=dict(ECMWF_CONSTRAINTS_BY_VAR_KEY.get(var_key, {})),
        frontend=frontend,
    )


ECMWF_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    var_key: _capability_from_var_spec(var_key, var_spec)
    for var_key, var_spec in ECMWF_VARS.items()
}
ECMWF_CAPABILITIES = ModelCapabilities(
    model_id="ecmwf",
    name="ECMWF",
    product="oper",
    canonical_region="na",
    grid_meters_by_region={
        "conus": 9_000.0,
        "na": 9_000.0,
    },
    run_discovery={
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 3],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 6,
        "stale_cycle_release_minutes_by_hour": {0: 450, 6: 390, 12: 450, 18: 390},
        "stalled_run_idle_minutes": 90,
        "allow_grib_without_idx": True,
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
