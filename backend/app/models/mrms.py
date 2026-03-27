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


class MRMSPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        # MRMS uses rolling observed bundles rather than forecast-hour schedules.
        return []

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        aliases: dict[str, str] = {
            "reflectivity": "reflectivity",
            "base_reflectivity": "reflectivity",
            "merged_base_reflectivity_qc": "reflectivity",
            "mrms_reflectivity": "reflectivity",
            "dbz": "reflectivity",
        }
        return aliases.get(normalized, normalized)

    def select_dataarray(self, ds: object, var_id: str) -> object:
        del ds, var_id
        raise NotImplementedError("select_dataarray is not used in the MRMS V1 publish path")

    def ensure_latest_cycles(self, keep_cycles: int, *, cache_dir: Path | None = None) -> dict[str, int]:
        del keep_cycles, cache_dir
        raise NotImplementedError("ensure_latest_cycles is not used in the MRMS V1 publish path")


MRMS_REGIONS: dict[str, RegionSpec] = {
    "conus": RegionSpec(
        id="conus",
        name="CONUS",
        bbox_wgs84=(-134.0, 24.0, -60.0, 55.0),
        clip=True,
    ),
}


MRMS_VARS: dict[str, VarSpec] = {
    "reflectivity": VarSpec(
        id="reflectivity",
        name="Merged Base Reflectivity QC",
        selectors=VarSelectors(
            hints={
                "upstream_product": "MRMS Merged Base Reflectivity QC",
                "upstream_transport": "noaa_ncep_http_grib2",
                "decoder_preferred": "wgrib2",
                "decoder_fallback": "pygrib",
            }
        ),
        primary=True,
        kind="discrete",
        units="dBZ",
    ),
}


def _capability_from_var_spec(var_key: str, var_spec: VarSpec) -> VariableCapability:
    return VariableCapability(
        var_key=var_key,
        name=var_spec.name,
        selectors=var_spec.selectors,
        primary=var_spec.primary,
        derived=var_spec.derived,
        derive_strategy_id=var_spec.derive,
        kind=var_spec.kind,
        units=var_spec.units,
        normalize_units=var_spec.normalize_units,
        scale=var_spec.scale,
        color_map_id="mrms_reflectivity" if var_key == "reflectivity" else None,
        buildable=bool(var_spec.primary or var_spec.derived),
        order=0 if var_key == "reflectivity" else None,
        group="Radar" if var_key == "reflectivity" else None,
    )


MRMS_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    var_key: _capability_from_var_spec(var_key, var_spec)
    for var_key, var_spec in MRMS_VARS.items()
}


MRMS_CAPABILITIES = ModelCapabilities(
    model_id="mrms",
    name="MRMS Radar",
    product="obs",
    canonical_region="conus",
    grid_meters_by_region={
        "conus": 1000.0,
    },
    run_discovery={},
    ui_defaults={
        "default_var_key": "reflectivity",
        "default_run": "latest",
        "default_frame_selection": "latest",
    },
    ui_constraints={
        "canonical_region": "conus",
        "time_axis_mode": "observed",
        "latest_only": True,
        "supports_sampling": True,
    },
    variable_catalog=MRMS_VARIABLE_CATALOG,
)


MRMS_MODEL = MRMSPlugin(
    id="mrms",
    name="MRMS Radar",
    regions=MRMS_REGIONS,
    vars=MRMS_VARS,
    product="obs",
    capabilities=MRMS_CAPABILITIES,
)
