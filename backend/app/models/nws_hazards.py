from __future__ import annotations

from pathlib import Path

from .base import BaseModelPlugin, ModelCapabilities, RegionSpec, VarSpec, VariableCapability


class NWSHazardsPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return []

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        aliases: dict[str, str] = {
            "active": "active",
            "hazards": "active",
            "hazard": "active",
            "alerts": "active",
            "active_hazards": "active",
            "active_alerts": "active",
            "nws_hazards": "active",
        }
        return aliases.get(normalized, normalized)

    def select_dataarray(self, ds: object, var_id: str) -> object:
        del ds, var_id
        raise NotImplementedError("select_dataarray is not used in the NWS Hazards publish path")

    def ensure_latest_cycles(self, keep_cycles: int, *, cache_dir: Path | None = None) -> dict[str, int]:
        del keep_cycles, cache_dir
        raise NotImplementedError("ensure_latest_cycles is not used in the NWS Hazards publish path")


NWS_HAZARDS_REGIONS: dict[str, RegionSpec] = {
    "conus": RegionSpec(
        id="conus",
        name="CONUS",
        bbox_wgs84=(-134.0, 24.0, -60.0, 55.0),
        clip=True,
    ),
}


NWS_HAZARDS_VARS: dict[str, VarSpec] = {
    "active": VarSpec(
        id="active",
        name="Active Hazards",
        primary=True,
        kind="categorical",
    ),
}


NWS_HAZARDS_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    "active": VariableCapability(
        var_key="active",
        name="Active Hazards",
        primary=True,
        kind="categorical",
        buildable=True,
        order=0,
        group="Hazards",
        legend_title="NWS Hazards",
        render_substrates=["vector"],
    ),
}


NWS_HAZARDS_CAPABILITIES = ModelCapabilities(
    model_id="nws_hazards",
    name="NWS Hazards",
    product="hazard",
    canonical_region="conus",
    run_discovery={},
    ui_defaults={
        "default_var_key": "active",
        "default_run": "latest",
        "default_frame_selection": "first",
    },
    ui_constraints={
        "canonical_region": "conus",
        "time_axis_mode": "valid",
        "latest_only": True,
        "supports_sampling": False,
    },
    variable_catalog=NWS_HAZARDS_VARIABLE_CATALOG,
)


NWS_HAZARDS_MODEL = NWSHazardsPlugin(
    id="nws_hazards",
    name="NWS Hazards",
    regions=NWS_HAZARDS_REGIONS,
    vars=NWS_HAZARDS_VARS,
    product="hazard",
    capabilities=NWS_HAZARDS_CAPABILITIES,
)