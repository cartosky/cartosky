from __future__ import annotations

from pathlib import Path

from .base import BaseModelPlugin, ModelCapabilities, RegionSpec, VarSpec, VariableCapability


class SPCPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return []

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        aliases: dict[str, str] = {
            "convective": "convective",
            "categorical": "convective",
            "convective_outlook": "convective",
            "spc_convective": "convective",
            "day1_3_convective": "convective",
        }
        return aliases.get(normalized, normalized)

    def select_dataarray(self, ds: object, var_id: str) -> object:
        del ds, var_id
        raise NotImplementedError("select_dataarray is not used in the SPC publish path")

    def ensure_latest_cycles(self, keep_cycles: int, *, cache_dir: Path | None = None) -> dict[str, int]:
        del keep_cycles, cache_dir
        raise NotImplementedError("ensure_latest_cycles is not used in the SPC publish path")


SPC_REGIONS: dict[str, RegionSpec] = {
    "conus": RegionSpec(
        id="conus",
        name="CONUS",
        bbox_wgs84=(-134.0, 24.0, -60.0, 55.0),
        clip=True,
    ),
}


SPC_VARS: dict[str, VarSpec] = {
    "convective": VarSpec(
        id="convective",
        name="SPC Convective Outlook",
        primary=True,
        kind="categorical",
    ),
}


SPC_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    "convective": VariableCapability(
        var_key="convective",
        name="SPC Convective Outlook",
        primary=True,
        kind="categorical",
        buildable=True,
        order=0,
        group="Outlooks",
        legend_title="Severe Storm Outlook",
        render_substrates=["vector"],
    ),
}


SPC_CAPABILITIES = ModelCapabilities(
    model_id="spc",
    name="SPC",
    product="outlook",
    canonical_region="conus",
    run_discovery={},
    ui_defaults={
        "default_var_key": "convective",
        "default_run": "latest",
        "default_frame_selection": "first",
    },
    ui_constraints={
        "canonical_region": "conus",
        "time_axis_mode": "valid",
        "latest_only": True,
        "supports_sampling": False,
    },
    variable_catalog=SPC_VARIABLE_CATALOG,
)


SPC_MODEL = SPCPlugin(
    id="spc",
    name="SPC",
    regions=SPC_REGIONS,
    vars=SPC_VARS,
    product="outlook",
    capabilities=SPC_CAPABILITIES,
)
