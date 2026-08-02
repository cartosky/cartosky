"""Phase 3 — AIGFS global domain on the native 4326 grid, wired but DARK.

The AIGFS mirror of ``test_gfs_global_domain.py``. Only the AIGFS-specific
properties live here; the registry-wide pins (dark payload byte-identity,
every declared build region has a RegionSpec + grid params) are already
parameterized over ``MODEL_REGISTRY`` in the GFS module and pick up ``aigfs``
for free — they are deliberately not duplicated.

* **Dark by default.** With ``CARTOSKY_GLOBAL_DOMAIN_MODELS`` unset, the
  declared ``supported_build_regions`` are invisible: the scheduler builds
  ``na`` only and the domain routes refuse ``global``.
* **Anomalies go global only where a global ERA5 baseline exists.** The three
  instantaneous anomaly fields inherit the GFS specs — including the
  ``baseline_region_by_build_region`` hint that points the global domain at
  the shared global ERA5 baselines — so they declare ``global``; the four
  precip-window anomalies stay canonical-only until Wave 2. Both directions
  are pinned against the real catalog.
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

from app.models.aigfs import (  # noqa: E402
    AIGFS_GLOBAL_ANOMALY_VAR_KEYS,
    AIGFS_GLOBAL_BUILD_REGIONS,
    AIGFS_MODEL,
    AIGFS_VARS,
)
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

MODEL = "aigfs"
OTHER_MODEL = "gfs"
CANONICAL = "na"
GLOBAL = "global"
FLAG = "CARTOSKY_GLOBAL_DOMAIN_MODELS"

#: Declared in aigfs.py; kept as a literal so a catalog change has to be
#: deliberate rather than silently absorbed by the tests.
DECLARING_VAR = "tmp2m"

#: Phase 3A Wave 1: instantaneous anomaly fields with global ERA5 baselines.
GLOBAL_ANOMALY_VARS = ("tmp2m_anom", "tmp850_anom", "hgt500_anom")
#: Precip-window anomalies — NA-only baselines until Wave 2.
CANONICAL_ONLY_ANOMALY_VARS = (
    "precip_5d_anom",
    "precip_7d_anom",
    "precip_10d_anom",
    "precip_16d_anom",
)

#: Every buildable non-anomaly AIGFS variable, spelled out rather than derived
#: from the catalog so that dropping one is a test failure.
NON_ANOMALY_BUILDABLE_VARS = (
    "tmp2m",
    "precip_total",
    "tmp850",
    "wspd850",
    "wspd300",
    "vort500",
    "wspd10m",
)


@pytest.fixture
def dark(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipping state: no model allowlisted."""
    monkeypatch.delenv(FLAG, raising=False)


@pytest.fixture
def aigfs_global_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prod flip: AIGFS allowlisted on scheduler + API."""
    monkeypatch.setenv(FLAG, MODEL)


@pytest.fixture
def gfs_global_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The already-shipped GFS flip — AIGFS must stay dark under it."""
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


def test_allowlisted_model_builds_and_publishes_global(aigfs_global_live: None) -> None:
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
    aigfs_global_live: None,
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


def test_aigfs_flip_leaves_the_rest_of_the_registry_dark(aigfs_global_live: None) -> None:
    for model_id, plugin in MODEL_REGISTRY.items():
        if model_id == MODEL or plugin.capabilities is None:
            continue
        canonical = domains_module.canonical_domain(plugin)
        assert domains_module.declared_domains_for_model(plugin) == (canonical,), model_id
    assert domains_module.validate_requested_domain(OTHER_MODEL, GLOBAL) is None


def test_gfs_flip_leaves_aigfs_dark(gfs_global_live: None) -> None:
    """The already-live GFS allowlist must not drag AIGFS along with it."""
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


def test_aigfs_anomaly_global_declaration_is_a_per_variable_allowlist() -> None:
    """Phase 3A Wave 1 (D2): exactly the three instantaneous anomaly fields
    declare ``global``; the four precip-window anomalies declare nothing.

    Pinned in BOTH directions over the real catalog, and the two sets must
    exhaust the catalog's anomaly variables — a new anomaly variable has to
    make a deliberate choice rather than inherit one.
    """
    catalog = AIGFS_MODEL.capabilities.variable_catalog
    anomaly_keys = {
        var_key
        for var_key, capability in catalog.items()
        if var_key.endswith("_anom")
        or "anomaly" in str(capability.derive_strategy_id or "")
    }
    assert anomaly_keys == set(GLOBAL_ANOMALY_VARS) | set(CANONICAL_ONLY_ANOMALY_VARS)
    assert AIGFS_GLOBAL_ANOMALY_VAR_KEYS == frozenset(GLOBAL_ANOMALY_VARS)

    for var_key in GLOBAL_ANOMALY_VARS:
        assert catalog[var_key].supported_build_regions == [CANONICAL, GLOBAL], var_key
    for var_key in CANONICAL_ONLY_ANOMALY_VARS:
        assert catalog[var_key].supported_build_regions == [], var_key


def test_every_buildable_non_anomaly_aigfs_variable_declares_global() -> None:
    catalog = AIGFS_MODEL.capabilities.variable_catalog
    seen = []
    for var_key, capability in catalog.items():
        if not capability.buildable:
            # AIGFS carries no ptype_intensity companion components, so every
            # non-buildable var (should one appear) declares nothing.
            assert capability.supported_build_regions == [], var_key
            continue
        if var_key.endswith("_anom") or "anomaly" in str(capability.derive_strategy_id or ""):
            continue
        assert capability.supported_build_regions == [CANONICAL, GLOBAL], var_key
        seen.append(var_key)
    assert sorted(seen) == sorted(NON_ANOMALY_BUILDABLE_VARS)
    assert list(AIGFS_GLOBAL_BUILD_REGIONS) == [CANONICAL, GLOBAL]


def test_anomaly_specs_inherit_the_global_baseline_hints() -> None:
    """AIGFS reuses the GFS anomaly specs, two of them through
    ``_with_pres_product``. That wrapper merges into ``hints``, so the Wave 1
    ``baseline_region_by_build_region`` routing survives — pinned here so a
    future refactor cannot silently drop it and quietly depart the global
    domain from the NA baseline.
    """
    for var_key in GLOBAL_ANOMALY_VARS:
        hints = AIGFS_VARS[var_key].selectors.hints or {}
        assert hints.get("baseline_region_by_build_region") == "global=global", var_key
        assert hints.get("baseline_source") == "era5", var_key
        assert hints.get("baseline_region") == CANONICAL, var_key


# ── 5. global region geometry (plan §1) ────────────────────────────────────


def test_global_regionspec_covers_the_whole_globe_unclipped() -> None:
    region = AIGFS_MODEL.get_region(GLOBAL)
    assert region is not None
    assert region.id == GLOBAL
    assert region.name == "Global"
    assert region.bbox_wgs84 == (-180.0, -90.0, 180.0, 90.0)
    assert region.clip is False


def test_global_grid_is_native_4326_at_quarter_degree() -> None:
    """Contract §1: 1440 × 721 EPSG:4326 at 0.25°, both poles as real rows."""
    assert AIGFS_MODEL.capabilities.grid_native_degrees_by_region == {GLOBAL: 0.25}
    assert GLOBAL not in AIGFS_MODEL.capabilities.grid_meters_by_region
    assert GLOBAL not in REGION_BBOX_3857

    grid = get_target_grid(MODEL, GLOBAL)
    assert grid.crs == "EPSG:4326"
    assert grid.resolution == 0.25
    assert (grid.height, grid.width) == (721, 1440)
    assert grid.bbox == pytest.approx((-180.125, -90.125, 179.875, 90.125))

    bbox, resolution = get_grid_params(MODEL, GLOBAL)
    assert resolution == 0.25
    assert bbox == REGION_BBOX_4326[GLOBAL]


def test_canonical_regions_keep_their_mercator_grids() -> None:
    """The native declaration must not leak into the canonical domains."""
    for region in ("na", "conus"):
        grid = get_target_grid(MODEL, region)
        assert grid.crs == "EPSG:3857"
        assert grid.resolution == 25_000.0
        assert grid.bbox == REGION_BBOX_3857[region]
