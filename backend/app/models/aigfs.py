"""NOAA AIGFS model plugin.

Initial rollout scope:
  - AIGFS `sfc`
      - `tmp2m`
      - `wspd10m`
  - AIGFS `pres`
      - `tmp850`
      - `wspd850`
  - realtime publishing only

Upstream verification:
  - Herbie model = "aigfs"
  - Herbie products = "sfc", "pres"
    - Surface tmp2m inventory entry is `TMP:2 m above ground`
    - Surface 10m wind components inventory entries are `UGRD:10 m above ground` and `VGRD:10 m above ground`
    - Pressure temperature inventory includes `TMP:850 mb`
    - Pressure 850mb height and wind components inventory entries are `HGT:850 mb`, `UGRD:850 mb`, and `VGRD:850 mb`
  - NOAA product inventory exposes 00/06/12/18z cycles with f000 and f006-f384

References:
  - https://herbie.readthedocs.io/en/stable/gallery/noaa_models/aigfs.html
  - https://www.nco.ncep.noaa.gov/pmb/products/aigfs
"""

from __future__ import annotations

from dataclasses import replace

from .base import HerbieRequest, ModelCapabilities, RegionSpec, VarSelectors, VariableCapability
from .gfs import GFSPlugin, GFS_VARS


class AIGFSPlugin(GFSPlugin):
    _PRES_VAR_KEYS = frozenset({"tmp850", "u850", "v850", "hgt850", "wspd850"})

    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return list(AIGFS_SFC_FHS)

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        if normalized in {"wind10m", "10mwind"}:
            return "wspd10m"
        if normalized in {"z850", "gh850", "850height", "850mbheight", "850mbheights", "850_heights"}:
            return "hgt850"
        return super().normalize_var_id(var_id)

    def herbie_request(
        self,
        *,
        product: str | None = None,
        var_key: str | None = None,
        run_date=None,
        fh: int | None = None,
        search_pattern: str | None = None,
    ) -> HerbieRequest:
        base_request = super().herbie_request(
            product=product,
            var_key=var_key,
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


AIGFS_VARS = {
    "tmp2m": GFS_VARS["tmp2m"],
    "tmp850": _with_pres_product(GFS_VARS["tmp850"]),
    "10u": GFS_VARS["10u"],
    "10v": GFS_VARS["10v"],
    "wspd10m": GFS_VARS["wspd10m"],
    "u850": _with_pres_product(GFS_VARS["u850"]),
    "v850": _with_pres_product(GFS_VARS["v850"]),
    "hgt850": _with_pres_product(GFS_VARS["hgt850"]),
    "wspd850": _with_pres_product(GFS_VARS["wspd850"]),
}


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
        order=1,
        group="Temperature",
        conversion="c_to_f",
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
        order=3,
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
        order=4,
        group="Wind",
        conversion="ms_to_kt",
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
        order=12,
        group="Wind",
        conversion="ms_to_mph",
    ),
}


AIGFS_CAPABILITIES = ModelCapabilities(
    model_id="aigfs",
    name="AIGFS",
    product="sfc",
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
        "source_priority": ["nomads"],
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