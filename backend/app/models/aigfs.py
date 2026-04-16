"""NOAA AIGFS model plugin.

Initial rollout scope:
  - AIGFS `sfc`
      - `tmp2m`
  - realtime publishing only

Upstream verification:
  - Herbie model = "aigfs"
  - Herbie products = "sfc", "pres"
  - Surface tmp2m inventory entry is `TMP:2 m above ground`
  - NOAA product inventory exposes 00/06/12/18z cycles with f000 and f006-f384

References:
  - https://herbie.readthedocs.io/en/stable/gallery/noaa_models/aigfs.html
  - https://www.nco.ncep.noaa.gov/pmb/products/aigfs
"""

from __future__ import annotations

from .base import ModelCapabilities, RegionSpec, VariableCapability
from .gfs import GFSPlugin, GFS_VARS


class AIGFSPlugin(GFSPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return list(AIGFS_SFC_FHS)


AIGFS_REGIONS: dict[str, RegionSpec] = {
    "conus": RegionSpec(
        id="conus",
        name="CONUS",
        bbox_wgs84=(-134.0, 24.0, -60.0, 55.0),
        clip=True,
    ),
}


AIGFS_SFC_FHS = tuple(range(0, 385, 6))


AIGFS_VARS = {
    "tmp2m": GFS_VARS["tmp2m"],
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