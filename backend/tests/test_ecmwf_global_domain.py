"""Phase 3 — ECMWF global domain on the native 4326 grid, wired but DARK.

The ECMWF mirror of ``test_aifs_global_domain.py`` — fourth and last model in
the GFS → AIGFS → AIFS → ECMWF rollout. Only the ECMWF-specific properties
live here; the registry-wide pins (dark payload byte-identity, every declared
build region has a RegionSpec + grid params) are already parameterized over
``MODEL_REGISTRY`` in the GFS module and pick up ``ecmwf`` for free — they are
deliberately not duplicated.

* **Dark by default.** With ``CARTOSKY_GLOBAL_DOMAIN_MODELS`` unset, the
  declared ``supported_build_regions`` are invisible: the scheduler builds
  ``na`` only and the domain routes refuse ``global``.
* **Global domain ONLY.** ECMWF's canonical domains stay on their 9 km
  EPSG:3857 grids, byte-identical (Change A framing, which applies to ECMWF
  exactly as it did to AIFS).
* **Cycle asymmetry is the gate here** (handoff brief §1). 00/12z run
  ``ECMWF_OPER_FHS``; 06/18z run the shorter ``ECMWF_SCDA_FHS``. Everything
  downstream — build targets, manifest ``expected_frames``, the readiness
  boundary — must be derived per cycle type from the plugin's own schedule.
  A manifest built from long-cycle expectations would permanently show a short
  cycle as incomplete; §7 below pins that it does not happen.
* **Model-id boundary.** ``ecmwf`` is an internal id; the Herbie model is
  ``ifs``. The 2026-07 18z outage was this class of bug, so §8 pins it.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.ecmwf import (  # noqa: E402
    ECMWF_GLOBAL_ANOMALY_VAR_KEYS,
    ECMWF_GLOBAL_BUILD_REGIONS,
    ECMWF_MODEL,
    ECMWF_OPER_FHS,
    ECMWF_SCDA_FHS,
    ECMWF_SHORT_CUTOFF_CYCLE_HOURS,
    ECMWF_VARS,
)
from app.models.registry import MODEL_REGISTRY  # noqa: E402
from app.models.serialization import serialize_model_capability  # noqa: E402
from app.services import domains as domains_module  # noqa: E402
from app.services import scheduler as scheduler_module  # noqa: E402
from app.services.builder.fetch import INTERNAL_ONLY_MODEL_IDS  # noqa: E402
from app.services.builder.raster_grid import (  # noqa: E402
    REGION_BBOX_3857,
    REGION_BBOX_4326,
    get_grid_params,
    get_target_grid,
)

MODEL = "ecmwf"
OTHER_MODEL = "gfs"
CANONICAL = "na"
GLOBAL = "global"
FLAG = "CARTOSKY_GLOBAL_DOMAIN_MODELS"

#: Declared in ecmwf.py; kept as a literal so a catalog change has to be
#: deliberate rather than silently absorbed by the tests.
DECLARING_VAR = "tmp2m"

#: Phase 3A Wave 1 (instantaneous) + Wave 2 (precip windows): every anomaly
#: field now has a global ERA5 baseline. ECMWF's long window is 15 d (the 16 d
#: input alias maps onto it and is absent from this catalog; see 9a76a1c3).
WAVE1_ANOMALY_VARS = ("tmp2m_anom", "tmp850_anom", "hgt500_anom")
WAVE2_PRECIP_ANOMALY_VARS = (
    "precip_5d_anom",
    "precip_7d_anom",
    "precip_10d_anom",
    "precip_15d_anom",
)
GLOBAL_ANOMALY_VARS = WAVE1_ANOMALY_VARS + WAVE2_PRECIP_ANOMALY_VARS
#: Empty since Wave 2. Kept as a named set so the exhaustion assertion still
#: forces an explicit decision for any anomaly variable added later.
CANONICAL_ONLY_ANOMALY_VARS: tuple[str, ...] = ()

#: Every buildable non-anomaly ECMWF variable, spelled out rather than derived
#: from the catalog so that dropping one is a test failure.
NON_ANOMALY_BUILDABLE_VARS = (
    "tmp2m",
    "dp2m",
    "rh2m",
    "rh700",
    "tmp850",
    "wspd850",
    "wspd300",
    "vort500",
    "precip_total",
    "ptype_intensity",
    "snowfall_total",
    "snowfall_kuchera_total",
    "ice_total",
    "pwat",
    "mucape",
    "wspd10m",
    "wgst10m",
)

#: Non-buildable composite components that inherit ``ptype_intensity``'s
#: declaration (see §5).
COMPOSITE_COMPONENT_VARS = (
    "ptype_intensity_rain",
    "ptype_intensity_snow",
    "ptype_intensity_ice",
)

#: The two cycle types, as (cycle_hour, expected target_fhs).
LONG_CYCLE_HOURS = (0, 12)
SHORT_CYCLE_HOURS = (6, 18)


@pytest.fixture
def dark(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipping state: no model allowlisted."""
    monkeypatch.delenv(FLAG, raising=False)


@pytest.fixture
def ecmwf_global_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prod flip: ECMWF allowlisted on scheduler + API."""
    monkeypatch.setenv(FLAG, MODEL)


@pytest.fixture
def gfs_global_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The already-shipped GFS flip — ECMWF must stay dark under it."""
    monkeypatch.setenv(FLAG, OTHER_MODEL)


def _serialized_build_regions(model_id: str) -> dict[str, list[str]]:
    plugin = MODEL_REGISTRY[model_id]
    payload = serialize_model_capability(model_id, plugin.capabilities)
    return {
        var_key: variable["supported_build_regions"]
        for var_key, variable in payload["variables"].items()
    }


def _declaring_vars() -> list[str]:
    catalog = ECMWF_MODEL.capabilities.variable_catalog
    return [
        var_key
        for var_key, capability in catalog.items()
        if GLOBAL in (capability.supported_build_regions or [])
    ]


# ── 1. dark by default ─────────────────────────────────────────────────────


def test_dark_build_targets_are_canonical_only(dark: None) -> None:
    """Flag unset ⇒ the scheduler sees exactly the canonical domain."""
    plugin = MODEL_REGISTRY[MODEL]
    catalog = plugin.capabilities.variable_catalog
    assert catalog[DECLARING_VAR].supported_build_regions == [CANONICAL, GLOBAL]

    for var_key in catalog:
        assert scheduler_module._build_regions_for_var(plugin, var_key) == [CANONICAL], var_key
        assert domains_module.declared_domains_for_var(plugin, var_key) == (CANONICAL,), var_key
    assert domains_module.declared_domains_for_model(MODEL) == (CANONICAL,)
    assert domains_module.validate_requested_domain(MODEL, GLOBAL) is None


def test_dark_capabilities_payload_hides_the_declaration(dark: None) -> None:
    by_var = _serialized_build_regions(MODEL)
    assert by_var
    for var_key, regions in by_var.items():
        assert regions == [], f"{MODEL}/{var_key} leaks build regions while dark"


# ── 2. activation ──────────────────────────────────────────────────────────


def test_allowlisted_model_builds_and_publishes_global(ecmwf_global_live: None) -> None:
    plugin = MODEL_REGISTRY[MODEL]
    assert scheduler_module._build_regions_for_var(plugin, DECLARING_VAR) == [CANONICAL, GLOBAL]
    assert domains_module.declared_domains_for_var(plugin, DECLARING_VAR) == (CANONICAL, GLOBAL)
    assert domains_module.declared_domains_for_model(MODEL) == (CANONICAL, GLOBAL)
    assert domains_module.validate_requested_domain(MODEL, GLOBAL) == GLOBAL

    # …Wave-1 anomalies come along, precip-window anomalies stay canonical-only.
    for var_key in GLOBAL_ANOMALY_VARS:
        assert scheduler_module._build_regions_for_var(plugin, var_key) == [
            CANONICAL,
            GLOBAL,
        ], var_key
    for var_key in CANONICAL_ONLY_ANOMALY_VARS:
        assert scheduler_module._build_regions_for_var(plugin, var_key) == [
            CANONICAL
        ], var_key


def test_live_capabilities_expose_global_on_declaring_vars_only(
    ecmwf_global_live: None,
) -> None:
    by_var = _serialized_build_regions(MODEL)
    assert by_var[DECLARING_VAR] == [CANONICAL, GLOBAL]
    for var_key in GLOBAL_ANOMALY_VARS:
        assert by_var[var_key] == [CANONICAL, GLOBAL], var_key
    for var_key in CANONICAL_ONLY_ANOMALY_VARS:
        assert by_var[var_key] == [], var_key
    declaring = {var_key for var_key, regions in by_var.items() if GLOBAL in regions}
    assert declaring
    assert all(by_var[var_key] == [CANONICAL, GLOBAL] for var_key in declaring)


# ── 3. per-model isolation, both directions ────────────────────────────────


def test_ecmwf_flip_leaves_the_rest_of_the_registry_dark(ecmwf_global_live: None) -> None:
    for model_id, plugin in MODEL_REGISTRY.items():
        if model_id == MODEL or plugin.capabilities is None:
            continue
        canonical = domains_module.canonical_domain(plugin)
        assert domains_module.declared_domains_for_model(plugin) == (canonical,), model_id
    assert domains_module.validate_requested_domain(OTHER_MODEL, GLOBAL) is None


def test_ecmwf_flip_does_not_drag_eps_along(ecmwf_global_live: None) -> None:
    """EPS shares ``ECMWF_REGIONS`` and the ECMWF plugin class, so the shared
    ``global`` RegionSpec reaches it — but ensembles are explicitly out of
    scope, and a RegionSpec alone must not make anything build or serve."""
    eps = MODEL_REGISTRY["eps"]
    assert eps.get_region(GLOBAL) is not None, "fixture assumption: EPS shares the RegionSpec"
    assert domains_module.declared_domains_for_model("eps") == (
        domains_module.canonical_domain(eps),
    )
    assert domains_module.validate_requested_domain("eps", GLOBAL) is None
    for var_key, capability in (eps.capabilities.variable_catalog or {}).items():
        assert GLOBAL not in (capability.supported_build_regions or []), var_key


def test_gfs_flip_leaves_ecmwf_dark(gfs_global_live: None) -> None:
    """The already-live GFS allowlist must not drag ECMWF along with it."""
    plugin = MODEL_REGISTRY[MODEL]
    assert domains_module.declared_domains_for_model(MODEL) == (CANONICAL,)
    assert domains_module.validate_requested_domain(MODEL, GLOBAL) is None
    for var_key in plugin.capabilities.variable_catalog:
        assert scheduler_module._build_regions_for_var(plugin, var_key) == [CANONICAL], var_key
    for var_key, regions in _serialized_build_regions(MODEL).items():
        assert regions == [], var_key

    # …and GFS itself is live, so the fixture is not vacuous.
    assert domains_module.declared_domains_for_model(OTHER_MODEL) == (CANONICAL, GLOBAL)


# ── 4. anomaly pin (plan §2 / G6) ──────────────────────────────────────────


def test_ecmwf_anomaly_global_declaration_is_a_per_variable_allowlist() -> None:
    """Phase 3A Wave 1 (D2): exactly the three instantaneous anomaly fields
    declare ``global``; the four precip-window anomalies declare nothing.

    Pinned in BOTH directions over the real catalog, and the two sets must
    exhaust the catalog's anomaly variables — a new anomaly variable has to
    make a deliberate choice rather than inherit one.
    """
    catalog = ECMWF_MODEL.capabilities.variable_catalog
    anomaly_keys = {
        var_key
        for var_key, capability in catalog.items()
        if var_key.endswith("_anom")
        or "anomaly" in str(capability.derive_strategy_id or "")
    }
    assert anomaly_keys == set(GLOBAL_ANOMALY_VARS) | set(CANONICAL_ONLY_ANOMALY_VARS)
    assert ECMWF_GLOBAL_ANOMALY_VAR_KEYS == frozenset(GLOBAL_ANOMALY_VARS)

    for var_key in GLOBAL_ANOMALY_VARS:
        assert catalog[var_key].supported_build_regions == [CANONICAL, GLOBAL], var_key
    for var_key in CANONICAL_ONLY_ANOMALY_VARS:
        assert catalog[var_key].supported_build_regions == [], var_key


def test_every_buildable_non_anomaly_ecmwf_variable_declares_global() -> None:
    catalog = ECMWF_MODEL.capabilities.variable_catalog
    seen = []
    for var_key, capability in catalog.items():
        if not capability.buildable:
            # Composite components are non-buildable but inherit their parent's
            # declaration (§5); every other non-buildable var declares nothing.
            if var_key in COMPOSITE_COMPONENT_VARS:
                assert capability.supported_build_regions == [CANONICAL, GLOBAL], var_key
            else:
                assert capability.supported_build_regions == [], var_key
            continue
        if var_key.endswith("_anom") or "anomaly" in str(capability.derive_strategy_id or ""):
            continue
        assert capability.supported_build_regions == [CANONICAL, GLOBAL], var_key
        seen.append(var_key)
    assert sorted(seen) == sorted(NON_ANOMALY_BUILDABLE_VARS)
    assert list(ECMWF_GLOBAL_BUILD_REGIONS) == [CANONICAL, GLOBAL]


def test_anomaly_specs_declare_the_global_baseline_hints_inline() -> None:
    """ECMWF is the ORIGIN of these anomaly specs (AIFS borrows them), so the
    Wave 1 ``baseline_region_by_build_region`` routing is declared inline on
    the spec, the way ``gfs.py`` declares it — not wrapped onto a copy."""
    for var_key in GLOBAL_ANOMALY_VARS:
        hints = ECMWF_VARS[var_key].selectors.hints or {}
        assert hints.get("baseline_region_by_build_region") == "global=global", var_key
        assert hints.get("baseline_source") == "era5", var_key
        assert hints.get("baseline_region") == CANONICAL, var_key

        # …and the hint survives into the served capability, which is what the
        # builder actually reads.
        catalog_hints = (
            ECMWF_MODEL.capabilities.variable_catalog[var_key].selectors.hints or {}
        )
        assert catalog_hints.get("baseline_region_by_build_region") == "global=global", var_key


def test_precip_window_anomaly_specs_carry_the_global_baseline_hint() -> None:
    """Wave 2 flip (2026-08-03): inverted from the pre-flip assertion that the
    routing hint was absent. ECMWF is the origin of these specs, so the hint
    is passed at construction.

    The window set is load-bearing: ECMWF is 5/7/10/**15** d. ``precip_16d_anom``
    is an input alias deliberately absent from this catalog (9a76a1c3) and must
    stay absent — a 16 d entry here would be a resurrection, not a flip.
    """
    for var_key in WAVE2_PRECIP_ANOMALY_VARS:
        hints = ECMWF_VARS[var_key].selectors.hints or {}
        assert hints.get("baseline_region_by_build_region") == "global=global", var_key
        assert hints.get("baseline_region") == CANONICAL, var_key

    assert "precip_16d_anom" not in ECMWF_VARS
    assert "precip_16d_anom" not in ECMWF_MODEL.capabilities.variable_catalog
    assert "precip_15d_anom" in WAVE2_PRECIP_ANOMALY_VARS


# ── 5. composite components inherit the parent's declaration ───────────────


def test_composite_components_declare_the_same_regions_as_their_parent() -> None:
    """A global ``ptype_intensity`` manifest must never advertise component
    layers whose frames were not built in the global domain (GFS verifier
    finding, 2026-07-29). ECMWF carries the same composite, so it inherits the
    same rule."""
    catalog = ECMWF_MODEL.capabilities.variable_catalog
    parent = catalog["ptype_intensity"]
    components = [key for key in catalog if key.startswith("ptype_intensity_")]
    assert sorted(components) == sorted(COMPOSITE_COMPONENT_VARS)
    assert parent.supported_build_regions == [CANONICAL, GLOBAL]
    for var_key in components:
        assert catalog[var_key].buildable is False, var_key
        assert catalog[var_key].supported_build_regions == parent.supported_build_regions, var_key


def test_composite_components_are_scheduled_into_the_global_domain(
    ecmwf_global_live: None,
) -> None:
    """The declaration is not decorative: the component vars come back from the
    scheduler's own companion expansion with the global domain attached."""
    plugin = MODEL_REGISTRY[MODEL]
    companions = set(scheduler_module._companion_vars_for_var(plugin, "ptype_intensity"))
    assert companions == set(COMPOSITE_COMPONENT_VARS)
    for var_key in companions:
        assert scheduler_module._build_regions_for_var(plugin, var_key) == [
            CANONICAL,
            GLOBAL,
        ], var_key

    targets = scheduler_module._scheduled_targets_for_cycle(plugin, ["ptype_intensity"], 0)
    for var_key in {"ptype_intensity", *COMPOSITE_COMPONENT_VARS}:
        assert any(
            target[0] == GLOBAL and target[1] == var_key for target in targets
        ), var_key


# ── 6. global region geometry (plan §1) ────────────────────────────────────


def test_global_regionspec_covers_the_whole_globe_unclipped() -> None:
    region = ECMWF_MODEL.get_region(GLOBAL)
    assert region is not None
    assert region.id == GLOBAL
    assert region.name == "Global"
    assert region.bbox_wgs84 == (-180.0, -90.0, 180.0, 90.0)
    assert region.clip is False


def test_global_grid_is_native_4326_at_quarter_degree() -> None:
    """Contract §1: 1440 × 721 EPSG:4326 at 0.25°, both poles as real rows."""
    assert ECMWF_MODEL.capabilities.grid_native_degrees_by_region == {GLOBAL: 0.25}
    assert GLOBAL not in ECMWF_MODEL.capabilities.grid_meters_by_region
    assert GLOBAL not in REGION_BBOX_3857

    grid = get_target_grid(MODEL, GLOBAL)
    assert grid.crs == "EPSG:4326"
    assert grid.resolution == 0.25
    assert (grid.height, grid.width) == (721, 1440)
    assert tuple(grid.transform)[:6] == pytest.approx((0.25, 0.0, -180.125, 0.0, -0.25, 90.125))
    assert grid.bbox == pytest.approx((-180.125, -90.125, 179.875, 90.125))

    bbox, resolution = get_grid_params(MODEL, GLOBAL)
    assert resolution == 0.25
    assert bbox == REGION_BBOX_4326[GLOBAL]


def test_canonical_regions_keep_their_9km_mercator_grids() -> None:
    """Change A applies to ECMWF too: the native declaration must not leak into
    the canonical domains, which stay 9 km EPSG:3857."""
    for region in ("na", "conus"):
        grid = get_target_grid(MODEL, region)
        assert grid.crs == "EPSG:3857"
        assert grid.resolution == 9_000.0
        assert grid.bbox == REGION_BBOX_3857[region]
    assert ECMWF_MODEL.capabilities.grid_meters_by_region == {
        "conus": 9_000.0,
        "na": 9_000.0,
    }
    assert ECMWF_MODEL.capabilities.canonical_region == CANONICAL


# ── 7. cycle asymmetry (handoff §1 — the critical G6 arm) ──────────────────


def test_the_two_cycle_types_have_different_horizons() -> None:
    """The premise the rest of this section rests on, pinned against the
    plugin's own schedule rather than any NA/GFS assumption."""
    assert ECMWF_SHORT_CUTOFF_CYCLE_HOURS == {6, 18}
    assert ECMWF_OPER_FHS == list(range(0, 145, 3)) + list(range(150, 361, 6))
    assert ECMWF_SCDA_FHS == list(range(0, 145, 3))
    assert len(ECMWF_OPER_FHS) == 85
    assert len(ECMWF_SCDA_FHS) == 49
    assert ECMWF_OPER_FHS[-1] == 360
    assert ECMWF_SCDA_FHS[-1] == 144

    for cycle_hour in LONG_CYCLE_HOURS:
        assert ECMWF_MODEL.target_fhs(cycle_hour) == ECMWF_OPER_FHS, cycle_hour
    for cycle_hour in SHORT_CYCLE_HOURS:
        assert ECMWF_MODEL.target_fhs(cycle_hour) == ECMWF_SCDA_FHS, cycle_hour


@pytest.mark.parametrize("cycle_hour", (0, 12, 6, 18))
def test_global_domain_inherits_the_cycle_schedule_per_var(
    cycle_hour: int, ecmwf_global_live: None
) -> None:
    """The binding: ``_scheduled_targets_for_cycle`` fans each var's
    ``scheduled_fhs_for_var(var, cycle_hour)`` across that var's declared build
    regions — so the global domain's fh set for a cycle IS the model's own
    schedule for that cycle, and is identical to the canonical domain's.

    Derived from the plugin, never from a hard-coded horizon: this is the
    assertion that would fail if global builds were pinned to the long cycle.
    """
    plugin = MODEL_REGISTRY[MODEL]
    declaring = _declaring_vars()
    targets = scheduler_module._scheduled_targets_for_cycle(plugin, declaring, cycle_hour)

    global_fhs: dict[str, set[int]] = {}
    canonical_fhs: dict[str, set[int]] = {}
    for region, var_key, fh in targets:
        bucket = global_fhs if region == GLOBAL else canonical_fhs
        bucket.setdefault(var_key, set()).add(fh)

    # Wave 2 put the precip-window anomalies on the global domain, and their
    # min_fh (168/240/360) exceeds the SHORT cycle's 144 h horizon — so on
    # 06/18z they schedule nothing at all, in BOTH domains. Deriving the
    # expected key set the same way (rather than asserting every declaring var
    # appears) keeps this a real per-var binding assertion instead of one that
    # silently depends on the horizon.
    scheduled = {
        var_key
        for var_key in declaring
        if ECMWF_MODEL.scheduled_fhs_for_var(var_key, cycle_hour)
    }
    assert set(global_fhs) == scheduled
    assert set(canonical_fhs) == scheduled
    for var_key in declaring:
        expected = set(ECMWF_MODEL.scheduled_fhs_for_var(var_key, cycle_hour))
        assert global_fhs.get(var_key, set()) == expected, var_key
        assert canonical_fhs.get(var_key, set()) == expected, var_key

    # The asymmetry is real and load-bearing, so pin its direction explicitly.
    if cycle_hour in (6, 18):
        assert "precip_15d_anom" not in scheduled
        assert "precip_5d_anom" in scheduled  # min_fh 120 ≤ 144
    else:
        assert {"precip_5d_anom", "precip_15d_anom"} <= scheduled


@pytest.mark.parametrize(
    ("cycle_hour", "expected_base_fhs"),
    ((0, 85), (12, 85), (6, 49), (18, 49)),
)
def test_per_var_min_max_fh_interacts_with_both_cycle_types(
    cycle_hour: int, expected_base_fhs: int
) -> None:
    """``min_fh``/``max_fh`` are applied on top of the CYCLE's fh list, so the
    accumulation variables lose their fh 0 frame on both cycle types — and the
    static-target precip anomalies drop out of the short cycle entirely."""
    assert len(ECMWF_MODEL.scheduled_fhs_for_var(DECLARING_VAR, cycle_hour)) == expected_base_fhs

    # min_fh = 3 accumulation vars: exactly one fewer frame, and fh 0 is gone.
    for var_key in ("precip_total", "snowfall_total", "snowfall_kuchera_total",
                    "ice_total", "wgst10m", "ptype_intensity"):
        fhs = ECMWF_MODEL.scheduled_fhs_for_var(var_key, cycle_hour)
        assert fhs[0] == 3, var_key
        assert len(fhs) == expected_base_fhs - 1, var_key

    # The composite COMPONENTS carry no min_fh of their own — a real asymmetry
    # with their parent, pinned as observed rather than assumed away.
    for var_key in COMPOSITE_COMPONENT_VARS:
        component_fhs = ECMWF_MODEL.scheduled_fhs_for_var(var_key, cycle_hour)
        assert component_fhs[0] == 0, var_key
        assert len(component_fhs) == expected_base_fhs, var_key

    # Precip-window anomalies are min_fh-gated at their window end. Three of the
    # four windows (168/240/360 h) lie beyond the short cycle's 144 h horizon,
    # so on 06/18z runs they vanish from the VARIABLE SET entirely — a
    # cycle-type difference in *which* variables exist, not just how many
    # frames. Only the 5 d window survives, truncated at 144 h. (All four stay
    # canonical-only until Wave 2 either way; this pins the schedule side.)
    long_cycle = cycle_hour in LONG_CYCLE_HOURS
    horizon = ECMWF_OPER_FHS[-1] if long_cycle else ECMWF_SCDA_FHS[-1]
    for var_key, window_fh in (
        ("precip_5d_anom", 120),
        ("precip_7d_anom", 168),
        ("precip_10d_anom", 240),
        ("precip_15d_anom", 360),
    ):
        fhs = ECMWF_MODEL.scheduled_fhs_for_var(var_key, cycle_hour)
        if window_fh > horizon:
            assert fhs == [], var_key
            continue
        assert fhs[0] == window_fh, var_key
        assert fhs[-1] <= horizon, var_key
    assert ECMWF_MODEL.scheduled_fhs_for_var("precip_5d_anom", 6) == list(range(120, 145, 3))
    # …and the 15 d window is additionally max_fh-pinned to a single frame.
    assert ECMWF_MODEL.scheduled_fhs_for_var("precip_15d_anom", cycle_hour) == (
        [360] if long_cycle else []
    )


@pytest.mark.parametrize("cycle_hour", (0, 6))
def test_global_manifest_expectations_follow_the_cycle_that_built_them(
    cycle_hour: int, ecmwf_global_live: None, tmp_path: Path
) -> None:
    """A short-cycle run whose frames are all published must read COMPLETE.

    ``_write_run_manifest`` derives ``expected_frames`` from the build targets
    it is handed, so this exercises the whole chain: cycle hour →
    ``scheduled_fhs_for_var`` → targets → domain-filtered manifest. The
    negative control below is what makes it non-vacuous.
    """
    plugin = MODEL_REGISTRY[MODEL]
    run_id = f"20260803_{cycle_hour:02d}z"
    targets = scheduler_module._scheduled_targets_for_cycle(plugin, [DECLARING_VAR], cycle_hour)
    scheduled = ECMWF_MODEL.scheduled_fhs_for_var(DECLARING_VAR, cycle_hour)

    _write_sidecars(tmp_path, run_id, scheduled)
    scheduler_module._write_run_manifest(
        data_root=tmp_path, model=MODEL, run_id=run_id, targets=targets,
        plugin=plugin, region=GLOBAL,
    )

    entry = _manifest_entry(tmp_path, run_id)
    assert entry["expected_frames"] == len(scheduled)
    assert entry["available_frames"] == len(scheduled)
    assert entry["expected_max_fh"] == scheduled[-1]
    assert entry["ready_through_fh"] == scheduled[-1]


def test_a_short_cycle_manifest_built_from_long_cycle_targets_would_read_incomplete(
    ecmwf_global_live: None, tmp_path: Path
) -> None:
    """The negative control for the test above, and the exact failure the
    handoff brief warns about: a manifest whose expectations came from the long
    cycle marks a fully-published short cycle as permanently incomplete."""
    plugin = MODEL_REGISTRY[MODEL]
    run_id = "20260803_06z"
    short_fhs = ECMWF_MODEL.scheduled_fhs_for_var(DECLARING_VAR, 6)
    wrong_targets = scheduler_module._scheduled_targets_for_cycle(plugin, [DECLARING_VAR], 0)

    _write_sidecars(tmp_path, run_id, short_fhs)
    scheduler_module._write_run_manifest(
        data_root=tmp_path, model=MODEL, run_id=run_id, targets=wrong_targets,
        plugin=plugin, region=GLOBAL,
    )

    entry = _manifest_entry(tmp_path, run_id)
    assert entry["expected_frames"] == len(ECMWF_MODEL.scheduled_fhs_for_var(DECLARING_VAR, 0))
    assert entry["available_frames"] == len(short_fhs)
    assert entry["available_frames"] < entry["expected_frames"]


def _write_sidecars(data_root: Path, run_id: str, fhs: list[int]) -> None:
    for fh in fhs:
        sidecar = scheduler_module._frame_sidecar_path(
            data_root, MODEL, run_id, DECLARING_VAR, fh, region=GLOBAL
        )
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"fh": fh, "units": "F", "kind": "continuous",
                        "valid_time": "2026-08-03T00:00:00Z"})
        )


def _manifest_entry(data_root: Path, run_id: str) -> dict:
    manifest_path = scheduler_module._manifest_path(data_root, MODEL, run_id, region=GLOBAL)
    payload = json.loads(manifest_path.read_text())
    assert payload["region"] == GLOBAL
    return payload["variables"][DECLARING_VAR]


def test_global_frame_counts_are_derived_per_cycle_type() -> None:
    """The disk projection: long and short cycles are counted separately (the
    old single-number row would be wrong for half of ECMWF's runs)."""
    catalog = ECMWF_MODEL.capabilities.variable_catalog

    def _total(cycle_hour: int) -> int:
        return sum(
            len(ECMWF_MODEL.scheduled_fhs_for_var(var_key, cycle_hour))
            for var_key, capability in catalog.items()
            if GLOBAL in (capability.supported_build_regions or [])
        )

    # Wave 2 (2026-08-03) added the four precip-window anomalies to the global
    # domain: +100 frames on a long cycle (45 + 33 + 21 + 1) and +9 on a short
    # one (only precip_5d_anom clears the 144 h horizon).
    assert _total(0) == _total(12) == 2049
    assert _total(6) == _total(18) == 1130

    precip_long = sum(
        len(ECMWF_MODEL.scheduled_fhs_for_var(var_key, 0))
        for var_key in WAVE2_PRECIP_ANOMALY_VARS
    )
    precip_short = sum(
        len(ECMWF_MODEL.scheduled_fhs_for_var(var_key, 6))
        for var_key in WAVE2_PRECIP_ANOMALY_VARS
    )
    assert (precip_long, precip_short) == (100, 9)


# ── 8. model-id boundary (the 18z-outage class) ────────────────────────────


def test_herbie_request_resolves_ecmwf_to_the_ifs_model_id() -> None:
    """``ecmwf`` is an internal CartoSky id; Herbie only knows ``ifs``. The
    global build path resolves it through the plugin exactly like the canonical
    one does, so this is pinned for BOTH products."""
    oper_run = datetime(2026, 8, 3, 0, tzinfo=timezone.utc)
    scda_run = datetime(2026, 1, 1, 6, tzinfo=timezone.utc)

    oper = ECMWF_MODEL.herbie_request(product="oper", run_date=oper_run)
    assert oper.model == "ifs"
    assert oper.product == "oper"

    scda = ECMWF_MODEL.herbie_request(product="scda", run_date=scda_run)
    assert scda.model == "ifs"
    assert scda.product == "scda"


def test_the_raw_ecmwf_id_is_rejected_by_the_fetch_layer() -> None:
    """The fail-closed half of the same guard: passing the internal id straight
    through must be loud, never a silent probe against a nonexistent model."""
    from app.services.builder import fetch as fetch_module

    assert MODEL in INTERNAL_ONLY_MODEL_IDS
    with pytest.raises(ValueError, match="internal CartoSky model id"):
        fetch_module._reject_internal_model_id(MODEL)
    fetch_module._reject_internal_model_id("ifs")
