"""Sea Surface Temperature — standalone daily observational layer (Phase 1).

Modeled on the MRMS plugin: ``product="obs"``,
``ui_constraints.time_axis_mode="observed"``, no forecast-hour schedule (the
publisher writes rolling observed bundles instead). Two differences from MRMS:

* the canonical domain is ``na`` (the 9 km EPSG:3857 grid, same geometry
  ECMWF/AIFS publish), not ``conus``;
* the single variable declares ``global`` as an extra build region, so the
  0.25° native-EPSG:4326 domain tree can be published once
  ``CARTOSKY_GLOBAL_DOMAIN_MODELS`` names ``sst`` (dark until then — see
  ``services/domains.non_canonical_domains_enabled``).

Units are **°C only** everywhere in the SST path — packing, sidecar, legend and
sampling. There is deliberately no °F conversion hook, and that applies to the
anomaly too: ``sst_anom`` carries °C *deltas* and is never Kelvin-converted.
"""

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

SST_MODEL_ID = "sst"
SST_VARIABLE_ID = "sst"
SST_ANOM_VARIABLE_ID = "sst_anom"
SST_COLOR_MAP_ID = "sst"
SST_ANOM_COLOR_MAP_ID = "sst_anom"
SST_CANONICAL_REGION_ID = "na"
SST_GLOBAL_REGION_ID = "global"

#: Domains the ``sst`` variable is declared buildable for. The extra beyond the
#: canonical region is gated by ``CARTOSKY_GLOBAL_DOMAIN_MODELS``.
SST_BUILD_REGIONS: tuple[str, ...] = (SST_CANONICAL_REGION_ID, SST_GLOBAL_REGION_ID)


class SSTPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        # SST uses rolling observed daily bundles, not forecast-hour schedules.
        return []

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        aliases: dict[str, str] = {
            "sst": "sst",
            "sea_surface_temp": "sst",
            "sea_surface_temperature": "sst",
            "analysed_sst": "sst",
            "sst_anom": "sst_anom",
            "sst_anomaly": "sst_anom",
            "sea_surface_temperature_anomaly": "sst_anom",
            "ssta": "sst_anom",
        }
        return aliases.get(normalized, normalized)

    def select_dataarray(self, ds: object, var_id: str) -> object:
        del ds, var_id
        raise NotImplementedError("select_dataarray is not used in the SST publish path")

    def ensure_latest_cycles(self, keep_cycles: int, *, cache_dir: Path | None = None) -> dict[str, int]:
        del keep_cycles, cache_dir
        raise NotImplementedError("ensure_latest_cycles is not used in the SST publish path")


SST_REGIONS: dict[str, RegionSpec] = {
    SST_CANONICAL_REGION_ID: RegionSpec(
        id=SST_CANONICAL_REGION_ID,
        name="North America",
        bbox_wgs84=(-178.0, 5.0, -25.0, 82.0),
        clip=True,
    ),
    # Native 0.25° EPSG:4326 whole-globe domain; no source clipping (the 5 km
    # Geo-Polar grid already covers it).
    SST_GLOBAL_REGION_ID: RegionSpec(
        id=SST_GLOBAL_REGION_ID,
        name="Global",
        bbox_wgs84=(-180.0, -90.0, 180.0, 90.0),
        clip=False,
    ),
}


SST_VARS: dict[str, VarSpec] = {
    SST_VARIABLE_ID: VarSpec(
        id=SST_VARIABLE_ID,
        name="Sea Surface Temperature",
        selectors=VarSelectors(
            hints={
                "upstream_product": "NOAA Geo-Polar Blended 5km daily L4 GHRSST (day+night)",
                "upstream_transport": "coastwatch_erddap_griddap_netcdf",
                "upstream_fallback": "ncei_ghrsst_archive_netcdf",
                "display_units": "C",
            }
        ),
        primary=True,
        kind="continuous",
        units="C",
    ),
    # NOAA Coral Reef Watch 5 km daily anomaly — NOAA's own matched-climatology
    # product on the Geo-Polar family, so no home-built climatology and no
    # cross-dataset bias artifact (spike §1). Two accepted caveats: CRW masks ice
    # zones, so anomaly coverage is lower than absolute SST (NA ~44% vs ~64%), and
    # the anomaly is night-lineage against our day+night absolute field (measured
    # pairing mean |Δ| 0.071 °C, p99 0.64 °C).
    SST_ANOM_VARIABLE_ID: VarSpec(
        id=SST_ANOM_VARIABLE_ID,
        name="Sea Surface Temperature Anomaly",
        selectors=VarSelectors(
            hints={
                "upstream_product": "NOAA Coral Reef Watch 5km daily SST anomaly v3.1",
                "upstream_transport": "noaa_star_archive_netcdf",
                "upstream_fallback": "coastwatch_erddap_griddap_netcdf",
                "display_units": "C",
                "anomaly_baseline": "NOAA CRW matched climatology (upstream-supplied)",
            }
        ),
        primary=True,
        kind="continuous",
        units="C",
    ),
}


_SST_VAR_COLOR_MAPS: dict[str, str] = {
    SST_VARIABLE_ID: SST_COLOR_MAP_ID,
    SST_ANOM_VARIABLE_ID: SST_ANOM_COLOR_MAP_ID,
}

_SST_VAR_GROUPS: dict[str, str] = {
    SST_VARIABLE_ID: "Ocean",
    SST_ANOM_VARIABLE_ID: "Ocean",
}

_SST_VAR_BUILD_REGIONS: dict[str, tuple[str, ...]] = {
    SST_VARIABLE_ID: SST_BUILD_REGIONS,
    SST_ANOM_VARIABLE_ID: SST_BUILD_REGIONS,
}


def _capability_from_var_spec(var_key: str, var_spec: VarSpec) -> VariableCapability:
    return VariableCapability(
        var_key=var_key,
        name=var_spec.name,
        selectors=var_spec.selectors,
        supported_build_regions=list(_SST_VAR_BUILD_REGIONS.get(var_key, ())),
        primary=var_spec.primary,
        derived=var_spec.derived,
        derive_strategy_id=var_spec.derive,
        kind=var_spec.kind,
        units=var_spec.units,
        normalize_units=var_spec.normalize_units,
        scale=var_spec.scale,
        color_map_id=_SST_VAR_COLOR_MAPS.get(var_key),
        buildable=bool(var_spec.primary or var_spec.derived),
        group=_SST_VAR_GROUPS.get(var_key, "Ocean"),
    )


SST_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    var_key: _capability_from_var_spec(var_key, var_spec)
    for var_key, var_spec in SST_VARS.items()
}


SST_CAPABILITIES = ModelCapabilities(
    model_id=SST_MODEL_ID,
    name="Sea Surface Temperature",
    product="obs",
    canonical_region=SST_CANONICAL_REGION_ID,
    grid_meters_by_region={
        SST_CANONICAL_REGION_ID: 9_000.0,
    },
    grid_native_degrees_by_region={
        SST_GLOBAL_REGION_ID: 0.25,
    },
    run_discovery={},
    ui_defaults={
        "default_var_key": SST_VARIABLE_ID,
        "default_run": "latest",
        "default_frame_selection": "latest",
    },
    ui_constraints={
        "canonical_region": SST_CANONICAL_REGION_ID,
        "time_axis_mode": "observed",
        "latest_only": True,
        "supports_sampling": True,
    },
    variable_catalog=SST_VARIABLE_CATALOG,
)


SST_MODEL = SSTPlugin(
    id=SST_MODEL_ID,
    name="Sea Surface Temperature",
    regions=SST_REGIONS,
    vars=SST_VARS,
    product="obs",
    capabilities=SST_CAPABILITIES,
)
