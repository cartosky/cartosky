"""ECMWF AIFS model plugin.

Initial rollout scope:
  - AIFS `oper`
      - `tmp2m`
      - `dp2m`
            - `tmp850`
    - `wspd850`
            - `wspd300`
      - `precip_total`
            - `pwat`
            - `snowfall_total`
      - `wspd10m`
  - realtime publishing only

Herbie wiring:
    - model = "aifs"
    - product = "oper"
"""

from __future__ import annotations

from dataclasses import replace

from .base import (
    HerbieRequest,
    ModelCapabilities,
    RegionSpec,
    VarSelectors,
    VariableCapability,
    VarSpec,
)
from .ecmwf import ECMWFPlugin, ECMWF_REGIONS, ECMWF_VARS, _capability_from_var_spec
from .gfs import (
    PRECIP_ANOM_360_STATIC_TARGET_FH_BY_VAR_KEY,
    PRECIP_ANOM_360_TARGET_FH_BY_VAR_KEY,
    _precip_anomaly_var_spec,
)


class AIFSPlugin(ECMWFPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return list(AIFS_OPER_FHS)

    def normalize_var_id(self, var_id: str) -> str:
        normalized = var_id.strip().lower()
        if normalized in {"tcw", "total_column_water"}:
            return "pwat"
        if normalized in {"z850", "gh850", "z"}:
            return "hgt850"
        if normalized in {"z300", "gh300"}:
            return "hgt300"
        if normalized in {"z500", "gh500"}:
            return "hgt500"
        if normalized in {"q700", "sh700", "specific_humidity_700", "700mb_specific_humidity"}:
            return "q700"
        return super().normalize_var_id(var_id)

    def herbie_request(
        self,
        *,
        product: str | None = None,
        var_key: str | None = None,
        ensemble_view: str | None = None,
        run_date=None,
        fh: int | None = None,
        search_pattern: str | None = None,
    ) -> HerbieRequest:
        base_request = super(ECMWFPlugin, self).herbie_request(
            product=product,
            var_key=var_key,
            ensemble_view=ensemble_view,
            run_date=run_date,
            fh=fh,
            search_pattern=search_pattern,
        )
        return HerbieRequest(
            model="aifs",
            product=base_request.product,
            herbie_kwargs=dict(base_request.herbie_kwargs),
        )


AIFS_REGIONS: dict[str, RegionSpec] = {
    **ECMWF_REGIONS,
    # Phase 3 — the whole globe on the source's native 0.25° EPSG:4326 grid.
    # Data coverage is the full globe including both poles; the mercator
    # viewer's ±85.05° clip is a display limit, not a coverage limit. No
    # source clipping (the AIFS grid already covers the domain). The canonical
    # regions are spread in verbatim so they stay identical by construction.
    "global": RegionSpec(
        id="global",
        name="Global",
        bbox_wgs84=(-180.0, -90.0, 180.0, 90.0),
        clip=False,
    ),
}


def _with_global_baseline_hint(var_spec: VarSpec) -> VarSpec:
    """Point the global domain of an anomaly at the global ERA5 baseline.

    AIFS's anomaly specs come from ``ECMWF_VARS``, which carry only the
    North-America baseline hints. The global EPSG:4326 ERA5 baselines are
    shared — the baseline path has no model segment — so AIFS rides the ones
    built for GFS free; all it needs is the same per-build-region routing hint
    GFS declares (see ``gfs.py``). Declared here rather than in ``ecmwf.py``
    because ECMWF itself is a later step in the rollout.
    """
    return replace(
        var_spec,
        selectors=replace(
            var_spec.selectors,
            hints={
                **(var_spec.selectors.hints or {}),
                "baseline_region_by_build_region": "global=global",
            },
        ),
    )


AIFS_VARS = {
    "tmp2m": ECMWF_VARS["tmp2m"],
    "tmp2m_anom": _with_global_baseline_hint(ECMWF_VARS["tmp2m_anom"]),
    "dp2m": ECMWF_VARS["dp2m"],
    "rh2m": ECMWF_VARS["rh2m"],
    "rh700": VarSpec(
        id="rh700",
        name="700mb Relative Humidity",
        selectors=VarSelectors(
            hints={
                "specific_humidity_component": "q700",
                "specific_humidity_units": "kg/kg",
                "temp_component": "tmp700",
                "temp_units": "c",
                "pressure_hpa": "700",
            }
        ),
        primary=True,
        derived=True,
        derive="relative_humidity_from_specific_humidity",
        kind="continuous",
        units="%",
    ),
    "tmp850": ECMWF_VARS["tmp850"],
    "tmp850_anom": _with_global_baseline_hint(ECMWF_VARS["tmp850_anom"]),
    "tmp700": ECMWF_VARS["tmp700"],
    "q700": VarSpec(
        id="q700",
        name="700mb Specific Humidity",
        selectors=VarSelectors(
            search=[":q:700:pl:", ":q:700:"],
            filter_by_keys={
                "shortName": "q",
                "typeOfLevel": "isobaricInhPa",
                "level": "700",
            },
            hints={
                "upstream_var": "q700",
                "cf_var": "q",
                "short_name": "q",
            },
        ),
        kind="continuous",
        units="kg/kg",
    ),
    "u850": ECMWF_VARS["u850"],
    "v850": ECMWF_VARS["v850"],
    "hgt850": ECMWF_VARS["hgt850"],
    "wspd850": ECMWF_VARS["wspd850"],
    "u300": ECMWF_VARS["u300"],
    "v300": ECMWF_VARS["v300"],
    "hgt300": ECMWF_VARS["hgt300"],
    "wspd300": ECMWF_VARS["wspd300"],
    "hgt500": ECMWF_VARS["hgt500"],
    "hgt500_anom": _with_global_baseline_hint(ECMWF_VARS["hgt500_anom"]),
    "precip_total": ECMWF_VARS["precip_total"],
    "precip_5d_anom": ECMWF_VARS["precip_5d_anom"],
    "precip_7d_anom": ECMWF_VARS["precip_7d_anom"],
    "precip_10d_anom": ECMWF_VARS["precip_10d_anom"],
    "precip_15d_anom": _precip_anomaly_var_spec(
        "precip_15d_anom",
        15,
        PRECIP_ANOM_360_STATIC_TARGET_FH_BY_VAR_KEY.get("precip_15d_anom"),
    ),
    "pwat": ECMWF_VARS["pwat"],
    "snowfall_total": ECMWF_VARS["snowfall_total"],
    "10u": ECMWF_VARS["10u"],
    "10v": ECMWF_VARS["10v"],
    "wspd10m": ECMWF_VARS["wspd10m"],
}


AIFS_OPER_FHS = list(range(0, 361, 6))


# Phase 3 (plan §2): every buildable AIFS grid variable also builds the
# ``global`` domain — the open AIFS source is a 0.25° global field, so it
# adopts the native EPSG:4326 contract verbatim
# (docs/GLOBAL_DOMAIN_4326_CONTRACT.md). This is the global domain ONLY: the
# AIFS canonical grid stays 9 km EPSG:3857, untouched.
#
# Anomaly variables were the one blanket exclusion — their ERA5 baselines were
# North-America-only, so a global anomaly had no climatology to depart from.
# Phase 3A Wave 1 (D2) narrows that to a per-variable allowlist: the three
# *instantaneous* anomaly fields now have global EPSG:4326 ERA5 baselines
# (shared across models — the baseline path has no model segment) and declare
# ``global``; the four precip-window anomalies still do not (their baselines
# need the Wave 2 streaming-memory fix first). The exclusion remains by
# omission below (never a runtime check) and both directions are pinned by
# tests over the real catalog.
AIFS_GLOBAL_BUILD_REGIONS: tuple[str, ...] = ("na", "global")

#: Anomaly variables that have global ERA5 baselines (Wave 1 — instantaneous
#: fields only). Everything else ending in ``_anom`` stays canonical-only.
AIFS_GLOBAL_ANOMALY_VAR_KEYS: frozenset[str] = frozenset(
    {"tmp2m_anom", "tmp850_anom", "hgt500_anom"}
)


def _declares_global_build_region(var_key: str, capability: VariableCapability) -> bool:
    if str(var_key) in AIFS_GLOBAL_ANOMALY_VAR_KEYS:
        return True
    if str(var_key).endswith("_anom"):
        return False
    return "anomaly" not in str(capability.derive_strategy_id or "")


AIFS_VARIABLE_CATALOG = {
    var_key: _capability_from_var_spec(var_key, var_spec)
    for var_key, var_spec in AIFS_VARS.items()
    if var_key not in {"10u", "10v", "tmp700", "q700", "u850", "v850", "hgt850", "u300", "v300", "hgt300"}
}

AIFS_VARIABLE_CATALOG["precip_total"] = replace(
    AIFS_VARIABLE_CATALOG["precip_total"],
    conversion="kgm2_to_in",
)

for _precip_anom_key, _precip_anom_fh in PRECIP_ANOM_360_TARGET_FH_BY_VAR_KEY.items():
    if _precip_anom_key in AIFS_VARIABLE_CATALOG:
        _precip_anom_constraint = {"min_fh": _precip_anom_fh}
        if _precip_anom_key in PRECIP_ANOM_360_STATIC_TARGET_FH_BY_VAR_KEY:
            _precip_anom_constraint["max_fh"] = _precip_anom_fh
        AIFS_VARIABLE_CATALOG[_precip_anom_key] = replace(
            AIFS_VARIABLE_CATALOG[_precip_anom_key],
            default_fh=_precip_anom_fh,
            constraints=_precip_anom_constraint,
            group="Anomalies",
            color_map_id="precip_anom",
        )

AIFS_VARIABLE_CATALOG["tmp2m_anom"] = replace(
    AIFS_VARIABLE_CATALOG["tmp2m_anom"],
    color_map_id="tmp2m_anom",
    default_fh=0,
    group="Temperature",
)

AIFS_VARS["pwat"] = replace(
    AIFS_VARS["pwat"],
    selectors=VarSelectors(
        search=[":tcw:", ":tcw:sfc:"],
        filter_by_keys={
            "shortName": "tcw",
            "typeOfLevel": "surface",
        },
        hints={
            "upstream_var": "tcw",
            "cf_var": "tcw",
            "short_name": "tcw",
        },
    ),
)

AIFS_VARS["hgt850"] = replace(
    AIFS_VARS["hgt850"],
    selectors=VarSelectors(
        search=[":z:850:pl:", ":z:850:"],
        filter_by_keys={
            "shortName": "z",
            "typeOfLevel": "isobaricInhPa",
            "level": "850",
        },
        hints={
            "upstream_var": "z850",
            "cf_var": "z",
            "short_name": "z",
        },
    ),
)

AIFS_VARS["wspd850"] = replace(
    AIFS_VARS["wspd850"],
    selectors=replace(
        AIFS_VARS["wspd850"].selectors,
        hints={
            **AIFS_VARS["wspd850"].selectors.hints,
            "contour_conversion": "geopotential_to_height_m",
        },
    ),
)

AIFS_VARS["hgt300"] = replace(
    AIFS_VARS["hgt300"],
    selectors=VarSelectors(
        search=[":z:300:pl:", ":z:300:"],
        filter_by_keys={
            "shortName": "z",
            "typeOfLevel": "isobaricInhPa",
            "level": "300",
        },
        hints={
            "upstream_var": "z300",
            "cf_var": "z",
            "short_name": "z",
        },
    ),
)

AIFS_VARS["wspd300"] = replace(
    AIFS_VARS["wspd300"],
    selectors=replace(
        AIFS_VARS["wspd300"].selectors,
        hints={
            **AIFS_VARS["wspd300"].selectors.hints,
            "contour_conversion": "geopotential_to_height_m",
        },
    ),
)

AIFS_VARS["hgt500"] = replace(
    AIFS_VARS["hgt500"],
    selectors=VarSelectors(
        search=[":z:500:pl:", ":z:500:"],
        filter_by_keys={
            "shortName": "z",
            "typeOfLevel": "isobaricInhPa",
            "level": "500",
        },
        hints={
            "upstream_var": "z500",
            "cf_var": "z",
            "short_name": "z",
        },
    ),
)

AIFS_VARS["hgt500_anom"] = replace(
    AIFS_VARS["hgt500_anom"],
    selectors=replace(
        AIFS_VARS["hgt500_anom"].selectors,
        hints={
            **AIFS_VARS["hgt500_anom"].selectors.hints,
            "contour_conversion": "geopotential_to_height_dam",
        },
    ),
)

AIFS_VARIABLE_CATALOG["pwat"] = replace(
    AIFS_VARIABLE_CATALOG["pwat"],
    selectors=AIFS_VARS["pwat"].selectors,
)

AIFS_VARIABLE_CATALOG["wspd850"] = replace(
    AIFS_VARIABLE_CATALOG["wspd850"],
    selectors=AIFS_VARS["wspd850"].selectors,
)

AIFS_VARIABLE_CATALOG["wspd300"] = replace(
    AIFS_VARIABLE_CATALOG["wspd300"],
    selectors=AIFS_VARS["wspd300"].selectors,
)

AIFS_VARIABLE_CATALOG["hgt500"] = replace(
    _capability_from_var_spec("hgt500", AIFS_VARS["hgt500"]),
    buildable=False,
    frontend={"internal_only": True},
)

AIFS_VARIABLE_CATALOG["hgt500_anom"] = replace(
    _capability_from_var_spec("hgt500_anom", AIFS_VARS["hgt500_anom"]),
    selectors=AIFS_VARS["hgt500_anom"].selectors,
)

AIFS_VARIABLE_CATALOG["snowfall_total"] = replace(
    AIFS_VARIABLE_CATALOG["snowfall_total"],
    conversion="kgm2_swe_to_in_10to1",
)


# Apply the global-domain declaration in ONE place, after the whole catalog
# (literals, generated precip anomalies and every per-variable ``replace``)
# exists. AIFS carries no composite/companion component variables, so
# buildability is the only gate besides the anomaly allowlist above —
# ``hgt500`` is a non-buildable internal contour component and declares
# nothing.
for _var_key, _capability in list(AIFS_VARIABLE_CATALOG.items()):
    if _capability.buildable and _declares_global_build_region(_var_key, _capability):
        AIFS_VARIABLE_CATALOG[_var_key] = replace(
            _capability,
            supported_build_regions=list(AIFS_GLOBAL_BUILD_REGIONS),
        )


AIFS_CAPABILITIES = ModelCapabilities(
    model_id="aifs",
    name="AIFS",
    product="oper",
    canonical_region="na",
    grid_meters_by_region={
        "conus": 9_000.0,
        "na": 9_000.0,
    },
    # The global domain is published on the source's own 0.25° EPSG:4326 grid
    # (1440 × 721, both poles included) rather than a mercator warp — see
    # docs/GLOBAL_DOMAIN_4326_CONTRACT.md. The canonical 9 km metre grids above
    # are untouched.
    grid_native_degrees_by_region={
        "global": 0.25,
    },
    run_discovery={
        "probe_var_key": "tmp2m",
        "probe_fhs": [0, 6],
        "probe_enabled": True,
        "probe_attempts": 4,
        "cycle_cadence_hours": 6,
        "fallback_lag_hours": 6,
        "source_priority": ["azure", "aws", "ecmwf"],
    },
    ui_defaults={
        "default_var_key": "tmp2m",
        "default_run": "latest",
    },
    ui_constraints={
        "canonical_region": "na",
        "supports_sampling": True,
        "overlay_fade_out_zoom_start": 6,
        "overlay_fade_out_zoom_end": 7,
    },
    variable_catalog=AIFS_VARIABLE_CATALOG,
)


AIFS_MODEL = AIFSPlugin(
    id="aifs",
    name="AIFS",
    regions=AIFS_REGIONS,
    vars=AIFS_VARS,
    product="oper",
    capabilities=AIFS_CAPABILITIES,
)
