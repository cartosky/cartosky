from __future__ import annotations

from pathlib import Path

from .base import (
    BaseModelPlugin,
    ModelCapabilities,
    RegionSpec,
    VarSelectors,
    VarSpec,
    VariableCapability,
)


class GOESEastPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return []

    def normalize_var_id(self, var_id: str) -> str:
        normalized = str(var_id or "").strip().lower()
        aliases: dict[str, str] = {
            "ir": "ir13",
            "ir13": "ir13",
            "clean_ir": "ir13",
            "cleanir": "ir13",
            "band13": "ir13",
            "c13": "ir13",
        }
        return aliases.get(normalized, normalized)

    def select_dataarray(self, ds: object, var_id: str) -> object:
        del ds, var_id
        raise NotImplementedError("select_dataarray is not used in the GOES-East publish path")

    def ensure_latest_cycles(self, keep_cycles: int, *, cache_dir: Path | None = None) -> dict[str, int]:
        del keep_cycles, cache_dir
        raise NotImplementedError("ensure_latest_cycles is not used in the GOES-East publish path")


GOES_EAST_MODEL_ID = "goes-east"
GOES_EAST_REGION_ID = "conus"
GOES_EAST_IR13_VARIABLE_ID = "ir13"
GOES_EAST_IR13_COLOR_MAP_ID = "goes_ir13_enhanced"


GOES_EAST_REGIONS: dict[str, RegionSpec] = {
    GOES_EAST_REGION_ID: RegionSpec(
        id=GOES_EAST_REGION_ID,
        name="CONUS",
        bbox_wgs84=(-134.0, 24.0, -60.0, 55.0),
        clip=True,
    ),
}


GOES_EAST_VARS: dict[str, VarSpec] = {
    GOES_EAST_IR13_VARIABLE_ID: VarSpec(
        id=GOES_EAST_IR13_VARIABLE_ID,
        name="Clean IR",
        selectors=VarSelectors(
            hints={
                "upstream_provider": "noaa_aws_s3",
                "upstream_satellite": "goes19",
                "upstream_bucket": "noaa-goes19",
                "upstream_product": "ABI-L2-CMIPC",
                "upstream_sector": "C",
                "upstream_band": "13",
                "upstream_variable": "CMI",
                "quality_variable": "DQF",
            }
        ),
        primary=True,
        kind="continuous",
        units="K",
    ),
}


GOES_EAST_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    GOES_EAST_IR13_VARIABLE_ID: VariableCapability(
        var_key=GOES_EAST_IR13_VARIABLE_ID,
        name=GOES_EAST_VARS[GOES_EAST_IR13_VARIABLE_ID].name,
        selectors=GOES_EAST_VARS[GOES_EAST_IR13_VARIABLE_ID].selectors,
        primary=True,
        kind="continuous",
        units="K",
        color_map_id=GOES_EAST_IR13_COLOR_MAP_ID,
        buildable=True,
        order=0,
        group="Satellite",
        legend_title="Brightness Temperature (K)",
        render_substrates=["grid"],
    ),
}


GOES_EAST_CAPABILITIES = ModelCapabilities(
    model_id=GOES_EAST_MODEL_ID,
    name="Satellite",
    product="obs",
    canonical_region=GOES_EAST_REGION_ID,
    grid_meters_by_region={
        GOES_EAST_REGION_ID: 4_000.0,
    },
    run_discovery={},
    ui_defaults={
        "default_var_key": GOES_EAST_IR13_VARIABLE_ID,
        "default_run": "latest",
        "default_frame_selection": "latest",
    },
    ui_constraints={
        "canonical_region": GOES_EAST_REGION_ID,
        "time_axis_mode": "observed",
        "latest_only": True,
        "supports_sampling": True,
    },
    variable_catalog=GOES_EAST_VARIABLE_CATALOG,
)


GOES_EAST_MODEL = GOESEastPlugin(
    id=GOES_EAST_MODEL_ID,
    name="Satellite",
    regions=GOES_EAST_REGIONS,
    vars=GOES_EAST_VARS,
    product="obs",
    capabilities=GOES_EAST_CAPABILITIES,
)
