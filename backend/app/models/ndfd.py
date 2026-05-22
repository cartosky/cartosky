from __future__ import annotations

from pathlib import Path

from .base import BaseModelPlugin, ModelCapabilities, RegionSpec, VarSpec, VariableCapability


class NDFDPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return []

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower().replace("-", "_")
        aliases: dict[str, str] = {
            "mint": "mint",
            "min_temp": "mint",
            "minimum_temp": "mint",
            "maxt": "maxt",
            "max_temp": "maxt",
            "maximum_temp": "maxt",
            "qpf6h": "qpf_6h",
            "qpf_6h": "qpf_6h",
            "qpf24h": "qpf_24h",
            "qpf_24h": "qpf_24h",
            "qpf48h": "qpf_48h",
            "qpf_48h": "qpf_48h",
            "snow6h": "snow_6h",
            "snow_6h": "snow_6h",
            "snow24h": "snow_24h",
            "snow_24h": "snow_24h",
            "snow48h": "snow_48h",
            "snow_48h": "snow_48h",
            "ice6h": "ice_6h",
            "ice_6h": "ice_6h",
            "ice24h": "ice_24h",
            "ice_24h": "ice_24h",
            "wgust6h": "wgust_6h_max",
            "wgust_6h": "wgust_6h_max",
            "wgust_6h_max": "wgust_6h_max",
            "wgust24h": "wgust_24h_max",
            "wgust_24h": "wgust_24h_max",
            "wgust_24h_max": "wgust_24h_max",
        }
        return aliases.get(normalized, normalized)

    def select_dataarray(self, ds: object, var_id: str) -> object:
        del ds, var_id
        raise NotImplementedError("select_dataarray is not used in the NDFD publish path")

    def ensure_latest_cycles(self, keep_cycles: int, *, cache_dir: Path | None = None) -> dict[str, int]:
        del keep_cycles, cache_dir
        raise NotImplementedError("ensure_latest_cycles is not used in the NDFD publish path")


NDFD_REGIONS: dict[str, RegionSpec] = {
    "conus": RegionSpec(
        id="conus",
        name="CONUS",
        bbox_wgs84=(-130.5, 20.0, -60.0, 53.5),
        clip=True,
    ),
}


def _capability(
    *,
    var_key: str,
    name: str,
    units: str,
    color_map_id: str,
    group: str,
    order: float,
) -> VariableCapability:
    return VariableCapability(
        var_key=var_key,
        name=name,
        primary=True,
        kind="continuous",
        units=units,
        color_map_id=color_map_id,
        buildable=True,
        order=order,
        group=group,
    )


NDFD_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    "mint": _capability(
        var_key="mint",
        name="Min Temp",
        units="F",
        color_map_id="tmp2m",
        group="Temperature",
        order=0,
    ),
    "maxt": _capability(
        var_key="maxt",
        name="Max Temp",
        units="F",
        color_map_id="tmp2m",
        group="Temperature",
        order=1,
    ),
    "qpf_6h": _capability(
        var_key="qpf_6h",
        name="QPF (6h)",
        units="in",
        color_map_id="qpf6h",
        group="Precipitation",
        order=10,
    ),
    "qpf_24h": _capability(
        var_key="qpf_24h",
        name="QPF (24h)",
        units="in",
        color_map_id="precip_total",
        group="Precipitation",
        order=11,
    ),
    "qpf_48h": _capability(
        var_key="qpf_48h",
        name="QPF (48h)",
        units="in",
        color_map_id="precip_total",
        group="Precipitation",
        order=12,
    ),
    "snow_6h": _capability(
        var_key="snow_6h",
        name="Snowfall (6h)",
        units="in",
        color_map_id="snowfall_total",
        group="Precipitation",
        order=13,
    ),
    "snow_24h": _capability(
        var_key="snow_24h",
        name="Snowfall (24h)",
        units="in",
        color_map_id="snowfall_total",
        group="Precipitation",
        order=14,
    ),
    "snow_48h": _capability(
        var_key="snow_48h",
        name="Snowfall (48h)",
        units="in",
        color_map_id="snowfall_total",
        group="Precipitation",
        order=15,
    ),
    "ice_6h": _capability(
        var_key="ice_6h",
        name="Ice (6h)",
        units="in",
        color_map_id="ice_total",
        group="Precipitation",
        order=16,
    ),
    "ice_24h": _capability(
        var_key="ice_24h",
        name="Ice (24h)",
        units="in",
        color_map_id="ice_total",
        group="Precipitation",
        order=17,
    ),
    "wgust_6h_max": _capability(
        var_key="wgust_6h_max",
        name="Wind Gust (6h Max)",
        units="mph",
        color_map_id="wgst10m",
        group="Wind",
        order=20,
    ),
    "wgust_24h_max": _capability(
        var_key="wgust_24h_max",
        name="Wind Gust (24h Max)",
        units="mph",
        color_map_id="wgst10m",
        group="Wind",
        order=21,
    ),
}


NDFD_VARS: dict[str, VarSpec] = {
    key: capability.to_var_spec()
    for key, capability in NDFD_VARIABLE_CATALOG.items()
}


NDFD_CAPABILITIES = ModelCapabilities(
    model_id="ndfd",
    name="NDFD",
    product="forecast",
    canonical_region="conus",
    grid_meters_by_region={
        "conus": 2_500.0,
    },
    run_discovery={},
    ui_defaults={
        "default_var_key": "mint",
        "default_run": "latest",
        "default_frame_selection": "first",
    },
    ui_constraints={
        "canonical_region": "conus",
        "time_axis_mode": "valid",
        "latest_only": True,
        "supports_sampling": True,
    },
    variable_catalog=NDFD_VARIABLE_CATALOG,
)


NDFD_MODEL = NDFDPlugin(
    id="ndfd",
    name="NDFD",
    regions=NDFD_REGIONS,
    vars=NDFD_VARS,
    product="forecast",
    capabilities=NDFD_CAPABILITIES,
)