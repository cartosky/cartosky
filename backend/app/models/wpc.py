from __future__ import annotations

from pathlib import Path

from .base import BaseModelPlugin, ModelCapabilities, RegionSpec, VarSpec, VariableCapability


class WPCPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return []

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower().replace("-", "_")
        aliases: dict[str, str] = {
            "precip_total": "precip_total",
            "total_precip": "precip_total",
            "apcp": "precip_total",
            "qpf": "precip_total",
            "total_qpf": "precip_total",
            "wpc_qpf": "precip_total",
            "wpc_precip_total": "precip_total",
        }
        return aliases.get(normalized, normalized)

    def select_dataarray(self, ds: object, var_id: str) -> object:
        del ds, var_id
        raise NotImplementedError("select_dataarray is not used in the WPC publish path")

    def ensure_latest_cycles(self, keep_cycles: int, *, cache_dir: Path | None = None) -> dict[str, int]:
        del keep_cycles, cache_dir
        raise NotImplementedError("ensure_latest_cycles is not used in the WPC publish path")


WPC_REGIONS: dict[str, RegionSpec] = {
    "conus": RegionSpec(
        id="conus",
        name="CONUS",
        bbox_wgs84=(-130.5, 20.0, -60.0, 53.5),
        clip=True,
    ),
}


WPC_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    "precip_total": VariableCapability(
        var_key="precip_total",
        name="Total Precip",
        primary=True,
        kind="continuous",
        units="in",
        color_map_id="precip_total",
        default_fh=6,
        buildable=True,
        order=0,
        group="Precipitation",
        constraints={
            "min_fh": 6,
            "max_fh": 168,
        },
    ),
}


WPC_VARS: dict[str, VarSpec] = {
    key: capability.to_var_spec()
    for key, capability in WPC_VARIABLE_CATALOG.items()
}


WPC_CAPABILITIES = ModelCapabilities(
    model_id="wpc",
    name="WPC",
    product="forecast",
    canonical_region="conus",
    grid_meters_by_region={
        "conus": 5_000.0,
    },
    run_discovery={},
    ui_defaults={
        "default_var_key": "precip_total",
        "default_run": "latest",
        "default_frame_selection": "first",
    },
    ui_constraints={
        "canonical_region": "conus",
        "time_axis_mode": "valid",
        "latest_only": True,
        "supports_sampling": True,
    },
    variable_catalog=WPC_VARIABLE_CATALOG,
)


WPC_MODEL = WPCPlugin(
    id="wpc",
    name="WPC",
    regions=WPC_REGIONS,
    vars=WPC_VARS,
    product="forecast",
    capabilities=WPC_CAPABILITIES,
)