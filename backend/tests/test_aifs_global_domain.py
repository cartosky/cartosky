"""Phase 3 — AIFS global domain on the native 4326 grid, wired but DARK.

The AIFS mirror of ``test_aigfs_global_domain.py``. Only the AIFS-specific
properties live here; the registry-wide pins (dark payload byte-identity,
every declared build region has a RegionSpec + grid params) are already
parameterized over ``MODEL_REGISTRY`` in the GFS module and pick up ``aifs``
for free — they are deliberately not duplicated.

* **Dark by default.** With ``CARTOSKY_GLOBAL_DOMAIN_MODELS`` unset, the
  declared ``supported_build_regions`` are invisible: the scheduler builds
  ``na`` only and the domain routes refuse ``global``.
* **Global domain ONLY.** The AIFS canonical domains stay on their 9 km
  EPSG:3857 grids, byte-identical (handoff brief, "the one framing rule").
* **Anomalies go global only where a global ERA5 baseline exists.** The three
  instantaneous anomaly fields carry the ``baseline_region_by_build_region``
  hint that points the global domain at the shared global ERA5 baselines — a
  hint AIFS has to add itself, since its specs come from ``ECMWF_VARS`` rather
  than ``GFS_VARS`` — so they declare ``global``; the four precip-window
  anomalies stay canonical-only until Wave 2. Both directions are pinned
  against the real catalog.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.aifs import (  # noqa: E402
    AIFS_GLOBAL_ANOMALY_VAR_KEYS,
    AIFS_GLOBAL_BUILD_REGIONS,
    AIFS_MODEL,
    AIFS_OPER_FHS,
    AIFS_VARS,
)
from app.models.ecmwf import ECMWF_REGIONS  # noqa: E402
from app.models.registry import MODEL_REGISTRY  # noqa: E402
from app.models.serialization import serialize_model_capability  # noqa: E402
from app.services import domains as domains_module  # noqa: E402
from app.services import scheduler as scheduler_module  # noqa: E402
from app.services.builder.raster_grid import (  # noqa: E402
    REGION_BBOX_3857,
    REGION_BBOX_4326,
    get_grid_params,
    get_target_grid,
)

MODEL = "aifs"
OTHER_MODEL = "gfs"
CANONICAL = "na"
GLOBAL = "global"
FLAG = "CARTOSKY_GLOBAL_DOMAIN_MODELS"

#: Declared in aifs.py; kept as a literal so a catalog change has to be
#: deliberate rather than silently absorbed by the tests.
DECLARING_VAR = "tmp2m"

#: Phase 3A Wave 1 (instantaneous) + Wave 2 (precip windows): every anomaly
#: field now has a global ERA5 baseline. AIFS's precip windows come off the
#: 360 h schedule, so the long window is 15 d, not GFS's 16 d.
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

#: Every buildable non-anomaly AIFS variable, spelled out rather than derived
#: from the catalog so that dropping one is a test failure.
NON_ANOMALY_BUILDABLE_VARS = (
    "tmp2m",
    "dp2m",
    "rh2m",
    "rh700",
    "tmp850",
    "wspd850",
    "wspd300",
    "precip_total",
    "pwat",
    "snowfall_total",
    "wspd10m",
)


@pytest.fixture
def dark(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipping state: no model allowlisted."""
    monkeypatch.delenv(FLAG, raising=False)


@pytest.fixture
def aifs_global_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prod flip: AIFS allowlisted on scheduler + API."""
    monkeypatch.setenv(FLAG, MODEL)


@pytest.fixture
def gfs_global_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The already-shipped GFS flip — AIFS must stay dark under it."""
    monkeypatch.setenv(FLAG, OTHER_MODEL)


def _serialized_build_regions(model_id: str) -> dict[str, list[str]]:
    plugin = MODEL_REGISTRY[model_id]
    payload = serialize_model_capability(model_id, plugin.capabilities)
    return {
        var_key: variable["supported_build_regions"]
        for var_key, variable in payload["variables"].items()
    }


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


def test_allowlisted_model_builds_and_publishes_global(aifs_global_live: None) -> None:
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
    aifs_global_live: None,
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


def test_aifs_flip_leaves_the_rest_of_the_registry_dark(aifs_global_live: None) -> None:
    for model_id, plugin in MODEL_REGISTRY.items():
        if model_id == MODEL or plugin.capabilities is None:
            continue
        canonical = domains_module.canonical_domain(plugin)
        assert domains_module.declared_domains_for_model(plugin) == (canonical,), model_id
    assert domains_module.validate_requested_domain(OTHER_MODEL, GLOBAL) is None


def test_gfs_flip_leaves_aifs_dark(gfs_global_live: None) -> None:
    """The already-live GFS allowlist must not drag AIFS along with it."""
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


def test_aifs_anomaly_global_declaration_is_a_per_variable_allowlist() -> None:
    """Phase 3A Wave 1 (D2): exactly the three instantaneous anomaly fields
    declare ``global``; the four precip-window anomalies declare nothing.

    Pinned in BOTH directions over the real catalog, and the two sets must
    exhaust the catalog's anomaly variables — a new anomaly variable has to
    make a deliberate choice rather than inherit one.
    """
    catalog = AIFS_MODEL.capabilities.variable_catalog
    anomaly_keys = {
        var_key
        for var_key, capability in catalog.items()
        if var_key.endswith("_anom")
        or "anomaly" in str(capability.derive_strategy_id or "")
    }
    assert anomaly_keys == set(GLOBAL_ANOMALY_VARS) | set(CANONICAL_ONLY_ANOMALY_VARS)
    assert AIFS_GLOBAL_ANOMALY_VAR_KEYS == frozenset(GLOBAL_ANOMALY_VARS)

    for var_key in GLOBAL_ANOMALY_VARS:
        assert catalog[var_key].supported_build_regions == [CANONICAL, GLOBAL], var_key
    for var_key in CANONICAL_ONLY_ANOMALY_VARS:
        assert catalog[var_key].supported_build_regions == [], var_key


def test_every_buildable_non_anomaly_aifs_variable_declares_global() -> None:
    catalog = AIFS_MODEL.capabilities.variable_catalog
    seen = []
    for var_key, capability in catalog.items():
        if not capability.buildable:
            # AIFS carries no composite/companion component variables; the one
            # non-buildable var (``hgt500``, an internal contour component)
            # declares nothing.
            assert capability.supported_build_regions == [], var_key
            continue
        if var_key.endswith("_anom") or "anomaly" in str(capability.derive_strategy_id or ""):
            continue
        assert capability.supported_build_regions == [CANONICAL, GLOBAL], var_key
        seen.append(var_key)
    assert sorted(seen) == sorted(NON_ANOMALY_BUILDABLE_VARS)
    assert list(AIFS_GLOBAL_BUILD_REGIONS) == [CANONICAL, GLOBAL]


def test_anomaly_specs_carry_the_global_baseline_hints() -> None:
    """AIFS's anomaly specs come from ``ECMWF_VARS``, which carry the NA
    baseline hints only. AIFS adds the Wave 1
    ``baseline_region_by_build_region`` routing itself so the global domain
    departs from the shared global ERA5 baseline rather than the NA one —
    pinned here so a future refactor cannot silently drop it.
    """
    for var_key in GLOBAL_ANOMALY_VARS:
        hints = AIFS_VARS[var_key].selectors.hints or {}
        assert hints.get("baseline_region_by_build_region") == "global=global", var_key
        assert hints.get("baseline_source") == "era5", var_key
        assert hints.get("baseline_region") == CANONICAL, var_key

        # …and the hint survives into the served capability, which is what the
        # builder actually reads.
        catalog_hints = (
            AIFS_MODEL.capabilities.variable_catalog[var_key].selectors.hints or {}
        )
        assert catalog_hints.get("baseline_region_by_build_region") == "global=global", var_key


def test_global_baseline_hint_wrapper_does_not_mutate_the_shared_ecmwf_specs() -> None:
    """AIFS's wrapper must COPY, never mutate, the ``ECMWF_VARS`` spec objects
    it borrows.

    ECMWF now declares ``baseline_region_by_build_region`` itself (its own
    global rollout, and it is the origin of these specs), so its absence is no
    longer the thing worth pinning — what matters is that ``replace()`` copy
    semantics hold, so a future divergence in either model's hint cannot leak
    into the other. The wrapper merging an identical hint must also stay
    idempotent.
    """
    from app.models.ecmwf import ECMWF_VARS

    for var_key in GLOBAL_ANOMALY_VARS:
        ecmwf_spec = ECMWF_VARS[var_key]
        aifs_spec = AIFS_VARS[var_key]

        # Distinct spec and hint-mapping objects: mutating one cannot reach
        # the other.
        assert aifs_spec is not ecmwf_spec, var_key
        assert aifs_spec.selectors is not ecmwf_spec.selectors, var_key
        assert aifs_spec.selectors.hints is not ecmwf_spec.selectors.hints, var_key

        # Both models route the global domain at the same shared baseline —
        # they must not disagree about which climatology a global anomaly
        # departs from. (Other hints legitimately differ: AIFS overrides a few
        # of the borrowed specs downstream.)
        hint_key = "baseline_region_by_build_region"
        assert (ecmwf_spec.selectors.hints or {}).get(hint_key) == "global=global", var_key
        assert (aifs_spec.selectors.hints or {}).get(hint_key) == "global=global", var_key

        # …and re-applying it over a spec that already carries the hint is a
        # no-op on the hint mapping.
        from app.models.aifs import _with_global_baseline_hint

        assert (
            _with_global_baseline_hint(aifs_spec).selectors.hints
            == aifs_spec.selectors.hints
        ), var_key


# ── 5. global region geometry (plan §1) ────────────────────────────────────


def test_global_regionspec_covers_the_whole_globe_unclipped() -> None:
    region = AIFS_MODEL.get_region(GLOBAL)
    assert region is not None
    assert region.id == GLOBAL
    assert region.name == "Global"
    assert region.bbox_wgs84 == (-180.0, -90.0, 180.0, 90.0)
    assert region.clip is False


def test_canonical_regionspecs_are_the_ecmwf_ones_verbatim() -> None:
    """The global entry is additive: ``na``/``conus`` are the same objects the
    model carried before, so the canonical domains cannot drift.

    ECMWF now carries its own ``global`` RegionSpec (its rollout landed after
    AIFS's); ``AIFS_REGIONS`` still declares AIFS's own, so identity is pinned
    over the canonical regions only, with equality of the two global specs
    checked separately.
    """
    canonical_region_ids = set(ECMWF_REGIONS) - {GLOBAL}
    assert canonical_region_ids == {"na", "conus"}
    for region_id in canonical_region_ids:
        assert AIFS_MODEL.get_region(region_id) is ECMWF_REGIONS[region_id], region_id
    assert set(AIFS_MODEL.regions) == set(ECMWF_REGIONS) | {GLOBAL}
    assert AIFS_MODEL.get_region(GLOBAL) == ECMWF_REGIONS[GLOBAL]


def test_global_grid_is_native_4326_at_quarter_degree() -> None:
    """Contract §1: 1440 × 721 EPSG:4326 at 0.25°, both poles as real rows."""
    assert AIFS_MODEL.capabilities.grid_native_degrees_by_region == {GLOBAL: 0.25}
    assert GLOBAL not in AIFS_MODEL.capabilities.grid_meters_by_region
    assert GLOBAL not in REGION_BBOX_3857

    grid = get_target_grid(MODEL, GLOBAL)
    assert grid.crs == "EPSG:4326"
    assert grid.resolution == 0.25
    assert (grid.height, grid.width) == (721, 1440)
    assert grid.bbox == pytest.approx((-180.125, -90.125, 179.875, 90.125))

    bbox, resolution = get_grid_params(MODEL, GLOBAL)
    assert resolution == 0.25
    assert bbox == REGION_BBOX_4326[GLOBAL]


def test_canonical_regions_keep_their_9km_mercator_grids() -> None:
    """The framing rule: the native declaration must not leak into the
    canonical domains, which stay 9 km EPSG:3857."""
    for region in ("na", "conus"):
        grid = get_target_grid(MODEL, region)
        assert grid.crs == "EPSG:3857"
        assert grid.resolution == 9_000.0
        assert grid.bbox == REGION_BBOX_3857[region]
    assert AIFS_MODEL.capabilities.grid_meters_by_region == {
        "conus": 9_000.0,
        "na": 9_000.0,
    }


# ── 6. the model's own schedule (checklist: not assumed from GFS) ──────────


def test_global_frames_follow_the_aifs_schedule_not_gfs() -> None:
    """AIFS runs 0–360 h at 6 h — 61 frames, not GFS's horizon. The disk
    projection for the global tree is derived from this count, so it is pinned
    rather than assumed."""
    assert AIFS_OPER_FHS == list(range(0, 361, 6))
    assert len(AIFS_OPER_FHS) == 61
    assert AIFS_MODEL.target_fhs(0) == AIFS_OPER_FHS
    assert AIFS_MODEL.target_fhs(12) == AIFS_OPER_FHS
    assert AIFS_MODEL.scheduled_fhs_for_var(DECLARING_VAR, 0) == AIFS_OPER_FHS

    catalog = AIFS_MODEL.capabilities.variable_catalog
    total_global_frames = sum(
        len(AIFS_MODEL.scheduled_fhs_for_var(var_key, 0))
        for var_key, capability in catalog.items()
        if GLOBAL in (capability.supported_build_regions or [])
    )
    # Wave 2 (2026-08-03) added the four precip-window anomalies to the global
    # domain: 41 + 33 + 21 + 1 = 96 more frames per run (852 → 948).
    assert total_global_frames == 948
    assert (
        sum(
            len(AIFS_MODEL.scheduled_fhs_for_var(var_key, 0))
            for var_key in WAVE2_PRECIP_ANOMALY_VARS
        )
        == 96
    )
