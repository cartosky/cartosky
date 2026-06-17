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
    def __init__(
        self,
        id: str | None = None,
        name: str | None = None,
        regions: dict[str, RegionSpec] | None = None,
        vars: dict[str, VarSpec] | None = None,
        product: str | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        object.__setattr__(self, "id", id or "goes-east")
        object.__setattr__(self, "name", name or "Satellite")
        object.__setattr__(self, "regions", regions if regions is not None else globals().get("GOES_EAST_REGIONS", {}))
        object.__setattr__(self, "vars", vars if vars is not None else globals().get("GOES_EAST_VARS", {}))
        object.__setattr__(self, "product", product or "obs")
        object.__setattr__(self, "capabilities", capabilities if capabilities is not None else globals().get("GOES_EAST_CAPABILITIES"))
        BaseModelPlugin.__post_init__(self)

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
            "wv9": "wv9",
            "wv": "wv9",
            "water_vapor": "wv9",
            "band9": "wv9",
            "c09": "wv9",
            "wv8": "wv8",
            "upper_water_vapor": "wv8",
            "band8": "wv8",
            "c08": "wv8",
            "true_color": "true_color",
            "truecolor": "true_color",
            "rgb": "true_color",
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
GOES_EAST_RGB_LATEST_FILENAME = "LATEST_RGB.json"
GOES_EAST_IR13_VARIABLE_ID = "ir13"
GOES_EAST_IR13_COLOR_MAP_ID = "goes_ir13_enhanced"
GOES_EAST_WV9_VARIABLE_ID = "wv9"
GOES_EAST_WV9_COLOR_MAP_ID = "goes_wv9_enhanced"
GOES_EAST_WV8_VARIABLE_ID = "wv8"
GOES_EAST_WV8_COLOR_MAP_ID = "goes_wv8_enhanced"
GOES_EAST_TRUE_COLOR_VARIABLE_ID = "true_color"


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
        units="C",
    ),
    GOES_EAST_WV9_VARIABLE_ID: VarSpec(
        id=GOES_EAST_WV9_VARIABLE_ID,
        name="Mid-Level Water Vapor",
        selectors=VarSelectors(
            hints={
                "upstream_provider": "noaa_aws_s3",
                "upstream_satellite": "goes19",
                "upstream_bucket": "noaa-goes19",
                "upstream_product": "ABI-L2-CMIPC",
                "upstream_sector": "C",
                "upstream_band": "9",
                "upstream_variable": "CMI",
                "quality_variable": "DQF",
            }
        ),
        primary=False,
        kind="continuous",
        units="C",
    ),
    "wv8": VarSpec(
        id="wv8",
        name="Upper-Level Water Vapor",
        selectors=VarSelectors(
            hints={
                "upstream_provider": "noaa_aws_s3",
                "upstream_satellite": "goes19",
                "upstream_bucket": "noaa-goes19",
                "upstream_product": "ABI-L2-CMIPC",
                "upstream_sector": "C",
                "upstream_band": "8",
                "upstream_variable": "CMI",
                "quality_variable": "DQF",
            }
        ),
        primary=False,
        kind="continuous",
        units="C",
    ),
    GOES_EAST_TRUE_COLOR_VARIABLE_ID: VarSpec(
        id=GOES_EAST_TRUE_COLOR_VARIABLE_ID,
        name="True Color",
        selectors=VarSelectors(
            hints={
                "upstream_provider": "noaa_aws_s3",
                "upstream_satellite": "goes19",
                "upstream_bucket": "noaa-goes19",
                "upstream_product": "ABI-L1b-RadC",
                "upstream_sector": "C",
                "upstream_variable": "Rad",
            }
        ),
        primary=False,
        kind="continuous",
        units="",
    ),
}


GOES_EAST_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    GOES_EAST_IR13_VARIABLE_ID: VariableCapability(
        var_key=GOES_EAST_IR13_VARIABLE_ID,
        name=GOES_EAST_VARS[GOES_EAST_IR13_VARIABLE_ID].name,
        selectors=GOES_EAST_VARS[GOES_EAST_IR13_VARIABLE_ID].selectors,
        primary=True,
        kind="continuous",
        units="C",
        color_map_id=GOES_EAST_IR13_COLOR_MAP_ID,
        buildable=True,
        order=0,
        group="Satellite",
        legend_title="Brightness Temperature",
        render_substrates=["grid"],
    ),
    GOES_EAST_WV9_VARIABLE_ID: VariableCapability(
        var_key=GOES_EAST_WV9_VARIABLE_ID,
        name="Mid-Level Water Vapor",
        selectors=GOES_EAST_VARS[GOES_EAST_WV9_VARIABLE_ID].selectors,
        primary=False,
        kind="continuous",
        units="C",
        color_map_id=GOES_EAST_WV9_COLOR_MAP_ID,
        buildable=True,
        order=1,
        group="Satellite",
        legend_title="Brightness Temperature",
        render_substrates=["grid"],
    ),
    "wv8": VariableCapability(
        var_key="wv8",
        name="Upper-Level Water Vapor",
        selectors=GOES_EAST_VARS["wv8"].selectors,
        primary=False,
        kind="continuous",
        units="C",
        color_map_id=GOES_EAST_WV8_COLOR_MAP_ID,
        buildable=True,
        order=2,
        group="Satellite",
        legend_title="Brightness Temperature",
        render_substrates=["grid"],
    ),
    GOES_EAST_TRUE_COLOR_VARIABLE_ID: VariableCapability(
        var_key=GOES_EAST_TRUE_COLOR_VARIABLE_ID,
        name="True Color",
        selectors=GOES_EAST_VARS[GOES_EAST_TRUE_COLOR_VARIABLE_ID].selectors,
        primary=False,
        kind="raster_rgb",
        units="",
        color_map_id=None,
        buildable=False,  # temporarily disabled — re-enable when server resources allow
        order=3,
        group="Satellite",
        legend_title=None,
        render_substrates=["image"],
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
        "supports_sampling": False,
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
