"""NOAA AIGFS model plugin.

Initial rollout scope:
  - AIGFS `sfc`
      - `tmp2m`
      - `wspd10m`
      - `precip_total`
  - AIGFS `pres`
      - `tmp850`
            - `tmp850_anom`
      - `wspd850`
      - `wspd300`
            - `vort500`
  - realtime publishing only

Upstream verification:
  - Herbie model = "aigfs"
  - Herbie products = "sfc", "pres"
    - Surface tmp2m inventory entry is `TMP:2 m above ground`
    - Surface 10m wind components inventory entries are `UGRD:10 m above ground` and `VGRD:10 m above ground`
    - Surface precipitation inventory includes cumulative `APCP:surface:0-6 hour acc`, `0-12 hour acc`, and `0-1 day acc` messages alongside step windows
    - Pressure temperature inventory includes `TMP:850 mb`
    - Pressure 850mb height and wind components inventory entries are `HGT:850 mb`, `UGRD:850 mb`, and `VGRD:850 mb`
    - Pressure 300mb height and wind components inventory entries are `HGT:300 mb`, `UGRD:300 mb`, and `VGRD:300 mb`
        - Pressure 500mb height inventory includes `HGT:500 mb`; `ABSV:500 mb` is not published, so AIGFS `vort500` is derived as relative vorticity from `UGRD:500 mb` and `VGRD:500 mb`
  - NOAA product inventory exposes 00/06/12/18z cycles with f000 and f006-f384

References:
  - https://herbie.readthedocs.io/en/stable/gallery/noaa_models/aigfs.html
  - https://www.nco.ncep.noaa.gov/pmb/products/aigfs
"""

from __future__ import annotations

from dataclasses import replace

from .base import HerbieRequest, ModelCapabilities, RegionSpec, VarSelectors, VarSpec, VariableCapability
from .gfs import (
    GFSPlugin,
    GFS_VARS,
    PRECIP_ANOM_384_STATIC_TARGET_FH_BY_VAR_KEY,
    PRECIP_ANOM_384_TARGET_FH_BY_VAR_KEY,
    _precip_anomaly_var_spec,
)


class AIGFSPlugin(GFSPlugin):
    _PRES_VAR_KEYS = frozenset({
        "tmp850", "tmp850_anom", "u850", "v850", "hgt850", "wspd850",
        "u300", "v300", "hgt300", "wspd300",
        "u500", "v500", "hgt500", "vort500",
    })

    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return list(AIGFS_SFC_FHS)

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        if normalized in {"wind10m", "10mwind"}:
            return "wspd10m"
        if normalized in {"z850", "gh850", "850height", "850mbheight", "850mbheights", "850_heights"}:
            return "hgt850"
        if normalized in {"z300", "gh300", "300height", "300mbheight", "300mbheights", "300_heights"}:
            return "hgt300"
        if normalized in {"hgt500_anom", "500hgt_anom", "500_hgt_anom", "500mb_height_anom", "height500_anom"}:
            return "hgt500_anom"
        if normalized == "gh500":
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
        base_request = super().herbie_request(
            product=product,
            var_key=var_key,
            ensemble_view=ensemble_view,
            run_date=run_date,
            fh=fh,
            search_pattern=search_pattern,
        )
        normalized_var = self.normalize_var_id(var_key or "") if isinstance(var_key, str) else ""
        resolved_product = "pres" if normalized_var in self._PRES_VAR_KEYS else base_request.product
        return HerbieRequest(
            model="aigfs",
            product=resolved_product,
            herbie_kwargs=dict(base_request.herbie_kwargs),
        )


AIGFS_REGIONS: dict[str, RegionSpec] = {
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


AIGFS_SFC_FHS = tuple(range(0, 385, 6))


def _with_pres_product(var_spec):
    return replace(
        var_spec,
        selectors=replace(
            var_spec.selectors,
            hints={
                **(var_spec.selectors.hints or {}),
                "product": "pres",
            },
        ),
    )


def _aigfs_pres_wind_component(axis: str, level_hpa: int) -> VarSpec:
    axis_norm = axis.strip().lower()
    level = int(level_hpa)
    if axis_norm not in {"u", "v"}:
        raise ValueError(f"Unsupported wind axis: {axis!r}")
    short_name = "ugrd" if axis_norm == "u" else "vgrd"
    grib_name = "UGRD" if axis_norm == "u" else "VGRD"
    return VarSpec(
        id=f"{axis_norm}{level}",
        name=f"{level}mb {'U' if axis_norm == 'u' else 'V'} Wind",
        selectors=VarSelectors(
            search=[f":{grib_name}:{level} mb:"],
            filter_by_keys={
                "shortName": short_name,
                "typeOfLevel": "isobaricInhPa",
                "level": str(level),
            },
            hints={
                "upstream_var": f"{axis_norm}{level}",
                "cf_var": axis_norm,
                "short_name": short_name,
                "product": "pres",
            },
        ),
    )


def _aigfs_vort500_spec() -> VarSpec:
    return VarSpec(
        id="vort500",
        name="500mb Heights + Vorticity",
        selectors=VarSelectors(
            hints={
                "u_component": "u500",
                "v_component": "v500",
                "contour_component": "hgt500",
                "contour_interval": "60",
                "contour_start": "4800",
                "contour_end": "6240",
                "contour_key": "height_500mb",
                "contour_label": "500 mb Height",
                "product": "pres",
            },
        ),
        primary=True,
        derived=True,
        derive="vort500_from_uv",
        kind="continuous",
        units="10^-5 s^-1",
    )


AIGFS_VARS = {
    "tmp2m": GFS_VARS["tmp2m"],
    "tmp2m_anom": GFS_VARS["tmp2m_anom"],
    "precip_total": VarSpec(
        id="precip_total",
        name="Total Precip",
        selectors=VarSelectors(
            search=[
                r":APCP:surface:0-[0-9]+ hour acc[^:]*:$",
                r":APCP:surface:0-[0-9]+ day acc[^:]*:$",
            ],
            filter_by_keys={
                "shortName": "apcp",
                "typeOfLevel": "surface",
            },
            hints={
                "upstream_var": "apcp",
            },
        ),
        primary=True,
        kind="continuous",
        units="in",
    ),
    "tmp850": _with_pres_product(GFS_VARS["tmp850"]),
    "tmp850_anom": _with_pres_product(GFS_VARS["tmp850_anom"]),
    "10u": GFS_VARS["10u"],
    "10v": GFS_VARS["10v"],
    "wspd10m": GFS_VARS["wspd10m"],
    "u850": _with_pres_product(GFS_VARS["u850"]),
    "v850": _with_pres_product(GFS_VARS["v850"]),
    "hgt850": _with_pres_product(GFS_VARS["hgt850"]),
    "wspd850": _with_pres_product(GFS_VARS["wspd850"]),
    "u300": _with_pres_product(GFS_VARS["u300"]),
    "v300": _with_pres_product(GFS_VARS["v300"]),
    "hgt300": _with_pres_product(GFS_VARS["hgt300"]),
    "wspd300": _with_pres_product(GFS_VARS["wspd300"]),
    "u500": _aigfs_pres_wind_component("u", 500),
    "v500": _aigfs_pres_wind_component("v", 500),
    "hgt500": _with_pres_product(GFS_VARS["hgt500"]),
    "hgt500_anom": _with_pres_product(GFS_VARS["hgt500_anom"]),
    "vort500": _aigfs_vort500_spec(),
}

for _precip_anom_key, _precip_anom_fh in PRECIP_ANOM_384_TARGET_FH_BY_VAR_KEY.items():
    _days = int(_precip_anom_key.split("_", 2)[1].removesuffix("d"))
    AIGFS_VARS[_precip_anom_key] = _precip_anomaly_var_spec(
        _precip_anom_key,
        _days,
        PRECIP_ANOM_384_STATIC_TARGET_FH_BY_VAR_KEY.get(_precip_anom_key),
    )


AIGFS_VARIABLE_CATALOG = {
    "tmp2m": VariableCapability(
        var_key="tmp2m",
        name=AIGFS_VARS["tmp2m"].name,
        selectors=AIGFS_VARS["tmp2m"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="F",
        color_map_id="tmp2m",
        default_fh=0,
        buildable=True,
        group="Temperature",
        conversion="c_to_f",
    ),
    "precip_total": VariableCapability(
        var_key="precip_total",
        name=AIGFS_VARS["precip_total"].name,
        selectors=AIGFS_VARS["precip_total"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="in",
        color_map_id="precip_total",
        default_fh=6,
        buildable=True,
        group="Precipitation",
        conversion="kgm2_to_in",
        constraints={"min_fh": 6},
    ),
    "tmp2m_anom": VariableCapability(
        var_key="tmp2m_anom",
        name=AIGFS_VARS["tmp2m_anom"].name,
        selectors=AIGFS_VARS["tmp2m_anom"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="F",
        color_map_id="tmp2m_anom",
        default_fh=0,
        buildable=True,
        group="Temperature",
    ),
    "tmp850": VariableCapability(
        var_key="tmp850",
        name=AIGFS_VARS["tmp850"].name,
        selectors=AIGFS_VARS["tmp850"].selectors,
        primary=True,
        derived=False,
        kind="continuous",
        units="C",
        color_map_id="tmp850",
        default_fh=0,
        buildable=True,
        group="Temperature",
    ),
    "tmp850_anom": VariableCapability(
        var_key="tmp850_anom",
        name=AIGFS_VARS["tmp850_anom"].name,
        selectors=AIGFS_VARS["tmp850_anom"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="C",
        color_map_id="tmp850_anom",
        default_fh=0,
        buildable=True,
        group="Temperature",
    ),
    "wspd850": VariableCapability(
        var_key="wspd850",
        name=AIGFS_VARS["wspd850"].name,
        selectors=AIGFS_VARS["wspd850"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="wspd10m",
        kind="continuous",
        units="kt",
        color_map_id="wspd850",
        default_fh=0,
        buildable=True,
        group="Wind",
        conversion="ms_to_kt",
    ),
    "wspd300": VariableCapability(
        var_key="wspd300",
        name=AIGFS_VARS["wspd300"].name,
        selectors=AIGFS_VARS["wspd300"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="wspd10m",
        kind="continuous",
        units="kt",
        color_map_id="wspd300",
        default_fh=0,
        buildable=True,
        group="Wind",
        conversion="ms_to_kt",
    ),
    "hgt500_anom": VariableCapability(
        var_key="hgt500_anom",
        name=AIGFS_VARS["hgt500_anom"].name,
        selectors=AIGFS_VARS["hgt500_anom"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="anomaly_departure",
        kind="continuous",
        units="m",
        color_map_id="hgt500_anom",
        default_fh=0,
        buildable=True,
        group="Dynamics",
    ),
    "vort500": VariableCapability(
        var_key="vort500",
        name=AIGFS_VARS["vort500"].name,
        selectors=AIGFS_VARS["vort500"].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="vort500_from_uv",
        kind="continuous",
        units="10^-5 s^-1",
        color_map_id="vort500",
        default_fh=0,
        buildable=True,
        group="Dynamics",
        conversion="s-1_to_1e5s-1",
    ),
    "wspd10m": VariableCapability(
        var_key="wspd10m",
        name=AIGFS_VARS["wspd10m"].name,
        selectors=AIGFS_VARS["wspd10m"].selectors,
        primary=False,
        derived=True,
        derive_strategy_id="wspd10m",
        kind="continuous",
        units="mph",
        color_map_id="wspd10m",
        default_fh=0,
        buildable=True,
        group="Wind",
        conversion="ms_to_mph",
    ),
}

for _precip_anom_key, _precip_anom_fh in PRECIP_ANOM_384_TARGET_FH_BY_VAR_KEY.items():
    _days = int(_precip_anom_key.split("_", 2)[1].removesuffix("d"))
    _precip_anom_constraint = {"min_fh": _precip_anom_fh}
    if _precip_anom_key in PRECIP_ANOM_384_STATIC_TARGET_FH_BY_VAR_KEY:
        _precip_anom_constraint["max_fh"] = _precip_anom_fh
    AIGFS_VARIABLE_CATALOG[_precip_anom_key] = VariableCapability(
        var_key=_precip_anom_key,
        name="Precip Anomaly",
        selectors=AIGFS_VARS[_precip_anom_key].selectors,
        primary=True,
        derived=True,
        derive_strategy_id="precip_accum_anomaly_departure",
        kind="continuous",
        units="in",
        color_map_id="precip_anom",
        default_fh=_precip_anom_fh,
        buildable=True,
        group="Anomalies",
        constraints=_precip_anom_constraint,
    )
AIGFS_CAPABILITIES = ModelCapabilities(
    model_id="aigfs",
    name="AIGFS",
    product="sfc",
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
        # EAGLE mirror (fetch.py injects the "aws" source into Herbie's
        # aigfs template); it lags NOMADS by hours, so realtime run tails
        # still resolve via the nomads fallback.
        "source_priority": ["aws", "nomads"],
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
    variable_catalog=AIGFS_VARIABLE_CATALOG,
)


AIGFS_MODEL = AIGFSPlugin(
    id="aigfs",
    name="AIGFS",
    regions=AIGFS_REGIONS,
    vars=AIGFS_VARS,
    product="sfc",
    capabilities=AIGFS_CAPABILITIES,
)
