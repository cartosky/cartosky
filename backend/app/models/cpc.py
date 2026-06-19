from __future__ import annotations

from pathlib import Path

from .base import BaseModelPlugin, ModelCapabilities, RegionSpec, VarSpec, VariableCapability


class CPCPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return []

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower().replace("-", "_")
        aliases: dict[str, str] = {
            "610_temp": "cpc_610_temp",
            "cpc_610_temperature": "cpc_610_temp",
            "610_temperature": "cpc_610_temp",
            "610_precip": "cpc_610_precip",
            "cpc_610_precipitation": "cpc_610_precip",
            "610_precipitation": "cpc_610_precip",
            "814_temp": "cpc_814_temp",
            "cpc_814_temperature": "cpc_814_temp",
            "814_temperature": "cpc_814_temp",
            "814_precip": "cpc_814_precip",
            "cpc_814_precipitation": "cpc_814_precip",
            "814_precipitation": "cpc_814_precip",
            "w34_temp": "cpc_w34_temp",
            "w34_temperature": "cpc_w34_temp",
            "cpc_w34_temperature": "cpc_w34_temp",
            "w34_precip": "cpc_w34_precip",
            "w34_precipitation": "cpc_w34_precip",
            "cpc_w34_precipitation": "cpc_w34_precip",
            "1m_temp": "cpc_1m_temp",
            "1m_temperature": "cpc_1m_temp",
            "cpc_1m_temperature": "cpc_1m_temp",
            "1m_precip": "cpc_1m_precip",
            "1m_precipitation": "cpc_1m_precip",
            "cpc_1m_precipitation": "cpc_1m_precip",
            "3m_temp": "cpc_3m_temp",
            "3m_temperature": "cpc_3m_temp",
            "cpc_3m_temperature": "cpc_3m_temp",
            "3m_precip": "cpc_3m_precip",
            "3m_precipitation": "cpc_3m_precip",
            "cpc_3m_precipitation": "cpc_3m_precip",
        }
        return aliases.get(normalized, normalized)

    def select_dataarray(self, ds: object, var_id: str) -> object:
        del ds, var_id
        raise NotImplementedError("select_dataarray is not used in the CPC publish path")

    def ensure_latest_cycles(self, keep_cycles: int, *, cache_dir: Path | None = None) -> dict[str, int]:
        del keep_cycles, cache_dir
        raise NotImplementedError("ensure_latest_cycles is not used in the CPC publish path")


CPC_REGIONS: dict[str, RegionSpec] = {
    "conus": RegionSpec(
        id="conus",
        name="CONUS",
        bbox_wgs84=(-180.0, 18.0, -60.0, 72.0),
        clip=True,
    ),
}


CPC_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    "cpc_610_temp": VariableCapability(
        var_key="cpc_610_temp",
        name="6-10 Day Temp",
        primary=True,
        kind="categorical",
        buildable=True,
        order=0,
        group="Forecasts",
        legend_title="CPC Temperature Outlook",
        render_substrates=["vector"],
    ),
    "cpc_610_precip": VariableCapability(
        var_key="cpc_610_precip",
        name="6-10 Day Precip",
        kind="categorical",
        buildable=True,
        order=1,
        group="Forecasts",
        legend_title="CPC Precipitation Outlook",
        render_substrates=["vector"],
    ),
    "cpc_814_temp": VariableCapability(
        var_key="cpc_814_temp",
        name="8-14 Day Temp",
        kind="categorical",
        buildable=True,
        order=2,
        group="Forecasts",
        legend_title="CPC Temperature Outlook",
        render_substrates=["vector"],
    ),
    "cpc_814_precip": VariableCapability(
        var_key="cpc_814_precip",
        name="8-14 Day Precip",
        kind="categorical",
        buildable=True,
        order=3,
        group="Forecasts",
        legend_title="CPC Precipitation Outlook",
        render_substrates=["vector"],
    ),
    "cpc_w34_temp": VariableCapability(
        var_key="cpc_w34_temp",
        name="W3-4 Temp",
        kind="categorical",
        buildable=True,
        order=4,
        group="Forecasts",
        legend_title="CPC Temperature Outlook",
        render_substrates=["vector"],
    ),
    "cpc_w34_precip": VariableCapability(
        var_key="cpc_w34_precip",
        name="W3-4 Precip",
        kind="categorical",
        buildable=True,
        order=5,
        group="Forecasts",
        legend_title="CPC Precipitation Outlook",
        render_substrates=["vector"],
    ),
    "cpc_1m_temp": VariableCapability(
        var_key="cpc_1m_temp",
        name="1-Month Temp",
        primary=False,
        kind="categorical",
        buildable=True,
        order=6,
        group="Forecasts",
        legend_title="CPC Temperature Outlook",
        render_substrates=["vector"],
    ),
    "cpc_1m_precip": VariableCapability(
        var_key="cpc_1m_precip",
        name="1-Month Precip",
        kind="categorical",
        buildable=True,
        order=7,
        group="Forecasts",
        legend_title="CPC Precipitation Outlook",
        render_substrates=["vector"],
    ),
    "cpc_3m_temp": VariableCapability(
        var_key="cpc_3m_temp",
        name="3-Month Temp",
        kind="categorical",
        buildable=True,
        order=8,
        group="Forecasts",
        legend_title="CPC Temperature Outlook",
        render_substrates=["vector"],
    ),
    "cpc_3m_precip": VariableCapability(
        var_key="cpc_3m_precip",
        name="3-Month Precip",
        kind="categorical",
        buildable=True,
        order=9,
        group="Forecasts",
        legend_title="CPC Precipitation Outlook",
        render_substrates=["vector"],
    ),
}


CPC_VARS: dict[str, VarSpec] = {
    key: capability.to_var_spec()
    for key, capability in CPC_VARIABLE_CATALOG.items()
}


CPC_CAPABILITIES = ModelCapabilities(
    model_id="cpc",
    name="CPC Outlooks",
    product="outlook",
    canonical_region="conus",
    run_discovery={},
    ui_defaults={
        "default_var_key": "cpc_610_temp",
        "default_run": "latest",
        "default_frame_selection": "first",
        "default_render_substrate": "vector",
    },
    ui_constraints={
        "canonical_region": "conus",
        "time_axis_mode": "valid",
        "latest_only": True,
        "supports_sampling": False,
    },
    variable_catalog=CPC_VARIABLE_CATALOG,
)


CPC_MODEL = CPCPlugin(
    id="cpc",
    name="CPC Outlooks",
    regions=CPC_REGIONS,
    vars=CPC_VARS,
    product="outlook",
    capabilities=CPC_CAPABILITIES,
)
