"""Phase 3 — GFS global domain at 25 km, wired but DARK.

Two properties dominate, mirroring the Phase 2A test file:

* **Dark by default.** With ``CARTOSKY_GLOBAL_DOMAIN_MODELS`` unset, the
  declared ``supported_build_regions`` are invisible everywhere: the scheduler
  builds ``na`` only and the capability payload is the literal pre-Phase-3
  shape (``supported_build_regions: []`` for every variable of every model).
* **Anomalies never go global.** ERA5 baselines are North-America-only, so
  anomaly variables are excluded by omission in the declaration — pinned here
  against the real catalog, not a fixture.

The G1 synthetic warp/contour oracles live in their own module.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from rasterio.warp import transform_bounds

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.gfs import GFS_MODEL  # noqa: E402
from app.models.registry import MODEL_REGISTRY  # noqa: E402
from app.models.serialization import serialize_model_capability  # noqa: E402
from app.services import domains as domains_module  # noqa: E402
from app.services import scheduler as scheduler_module  # noqa: E402
from app.services.builder import pipeline as pipeline_module  # noqa: E402
from app.services.builder.raster_grid import (  # noqa: E402
    REGION_BBOX_3857,
    REGION_BBOX_4326,
    compute_transform_and_shape,
    get_grid_crs,
    get_grid_params,
    get_target_grid,
)
from app.services.colormaps import get_color_map_spec  # noqa: E402

MODEL = "gfs"
CANONICAL = "na"
GLOBAL = "global"
FLAG = "CARTOSKY_GLOBAL_DOMAIN_MODELS"

#: Declared in gfs.py; kept as a literal so a catalog change has to be
#: deliberate rather than silently absorbed by the tests.
DECLARING_VAR = "tmp2m"
ANOMALY_VAR = "tmp2m_anom"


@pytest.fixture
def dark(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipping state: no model allowlisted."""
    monkeypatch.delenv(FLAG, raising=False)


@pytest.fixture
def gfs_global_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prod flip: GFS allowlisted on scheduler + API."""
    monkeypatch.setenv(FLAG, MODEL)


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


@pytest.mark.parametrize("model_id", sorted(MODEL_REGISTRY))
def test_dark_capabilities_payload_is_byte_identical(model_id: str, dark: None) -> None:
    """Flag unset ⇒ every variable of every model reports no build regions.

    ``[]`` is the literal pre-Phase-3 value for the whole registry, so this is
    the byte-identical regression pin for the API payload.
    """
    plugin = MODEL_REGISTRY[model_id]
    if plugin.capabilities is None:
        pytest.skip(f"{model_id} has no capabilities")
    by_var = _serialized_build_regions(model_id)
    assert by_var, model_id
    for var_key, regions in by_var.items():
        assert regions == [], f"{model_id}/{var_key} leaks build regions while dark"


# ── 2. activation ──────────────────────────────────────────────────────────


def test_allowlisted_model_builds_and_publishes_global(gfs_global_live: None) -> None:
    plugin = MODEL_REGISTRY[MODEL]
    assert scheduler_module._build_regions_for_var(plugin, DECLARING_VAR) == [CANONICAL, GLOBAL]
    assert domains_module.declared_domains_for_var(plugin, DECLARING_VAR) == (CANONICAL, GLOBAL)
    assert domains_module.declared_domains_for_model(MODEL) == (CANONICAL, GLOBAL)
    assert domains_module.validate_requested_domain(MODEL, GLOBAL) == GLOBAL

    # …and anomalies stay canonical-only even with the flag on.
    assert scheduler_module._build_regions_for_var(plugin, ANOMALY_VAR) == [CANONICAL]


def test_allowlist_is_per_model(gfs_global_live: None) -> None:
    """Only the named model activates; the rest of the registry stays dark."""
    for model_id, plugin in MODEL_REGISTRY.items():
        if model_id == MODEL or plugin.capabilities is None:
            continue
        canonical = domains_module.canonical_domain(plugin)
        assert domains_module.declared_domains_for_model(plugin) == (canonical,), model_id


def test_live_capabilities_expose_global_on_declaring_vars_only(
    gfs_global_live: None,
) -> None:
    by_var = _serialized_build_regions(MODEL)
    assert by_var[DECLARING_VAR] == [CANONICAL, GLOBAL]
    assert by_var[ANOMALY_VAR] == []
    declaring = {var_key for var_key, regions in by_var.items() if GLOBAL in regions}
    assert declaring
    assert all(by_var[var_key] == [CANONICAL, GLOBAL] for var_key in declaring)


# ── 3. anomaly pin (plan §2 / G6) ──────────────────────────────────────────


def test_no_gfs_anomaly_variable_declares_global() -> None:
    """Exclusion is by omission — asserted over the real catalog."""
    catalog = GFS_MODEL.capabilities.variable_catalog
    anomaly_keys = {
        var_key
        for var_key, capability in catalog.items()
        if var_key.endswith("_anom")
        or "anomaly" in str(capability.derive_strategy_id or "")
    }
    assert anomaly_keys, "expected GFS to have anomaly variables"
    for var_key in anomaly_keys:
        assert catalog[var_key].supported_build_regions == [], var_key


def test_every_buildable_non_anomaly_gfs_variable_declares_global() -> None:
    catalog = GFS_MODEL.capabilities.variable_catalog
    for var_key, capability in catalog.items():
        if not capability.buildable:
            # Companion component vars (ptype_intensity_*) are non-buildable
            # but scheduled through their own _build_regions_for_var call, so
            # they MUST carry their composite parent's declaration; every
            # other non-buildable var declares nothing.
            if var_key.startswith("ptype_intensity_"):
                assert capability.supported_build_regions == [CANONICAL, GLOBAL], var_key
            else:
                assert capability.supported_build_regions == [], var_key
            continue
        if var_key.endswith("_anom") or "anomaly" in str(capability.derive_strategy_id or ""):
            continue
        assert capability.supported_build_regions == [CANONICAL, GLOBAL], var_key


def test_composite_components_declare_the_same_regions_as_their_parent() -> None:
    """A global ptype_intensity manifest must never advertise component
    layers whose frames were not built in the global domain (verifier
    finding, 2026-07-29): components inherit the parent's declaration."""
    catalog = GFS_MODEL.capabilities.variable_catalog
    parent = catalog["ptype_intensity"]
    components = [key for key in catalog if key.startswith("ptype_intensity_")]
    assert components, "expected ptype_intensity component vars"
    for var_key in components:
        assert catalog[var_key].supported_build_regions == parent.supported_build_regions, var_key


# ── 4. declaration typos cannot silently no-op ─────────────────────────────


@pytest.mark.parametrize("model_id", sorted(MODEL_REGISTRY))
def test_every_declared_build_region_has_a_regionspec_and_grid_params(
    model_id: str,
) -> None:
    plugin = MODEL_REGISTRY[model_id]
    catalog = getattr(plugin.capabilities, "variable_catalog", {}) or {}
    for var_key, capability in catalog.items():
        for region in getattr(capability, "supported_build_regions", []) or []:
            assert plugin.get_region(region) is not None, f"{model_id}/{var_key}/{region}"
            assert domains_module._grid_params_available(model_id, region), (
                f"{model_id}/{var_key}/{region} declares a region with no grid params"
            )
            assert not domains_module.is_reserved_domain_id(region)


# ── 5. global-aware sanity ranges (plan §4) ────────────────────────────────


def _range_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "outside spec range" in record.getMessage()
    ]


@pytest.fixture
def antarctic_tmp2m() -> tuple[object, object, object]:
    """A tmp2m field with Vostok-class values (°F), plus its specs."""
    import numpy as np

    values = np.array(
        [[-125.0, -118.0, -96.0, -80.0], [-60.0, -20.0, 10.0, 40.0]],
        dtype=np.float32,
    )
    return values, get_color_map_spec("tmp2m"), GFS_MODEL.get_var("tmp2m")


def test_antarctic_field_warns_under_na_range(
    antarctic_tmp2m, caplog: pytest.LogCaptureFixture
) -> None:
    values, spec, var_spec_model = antarctic_tmp2m
    with caplog.at_level(logging.WARNING, logger=pipeline_module.logger.name):
        assert pipeline_module.check_pre_encode_value_sanity(
            values, spec, var_spec_model=var_spec_model, label="na probe", region=CANONICAL,
        )
    assert _range_warnings(caplog), "expected the NA envelope to flag −125 °F"


def test_antarctic_field_is_clean_under_the_global_range(
    antarctic_tmp2m, caplog: pytest.LogCaptureFixture
) -> None:
    values, spec, var_spec_model = antarctic_tmp2m
    with caplog.at_level(logging.WARNING, logger=pipeline_module.logger.name):
        assert pipeline_module.check_pre_encode_value_sanity(
            values, spec, var_spec_model=var_spec_model, label="global probe", region=GLOBAL,
        )
    assert _range_warnings(caplog) == []


def test_absent_region_keeps_the_default_envelope(
    antarctic_tmp2m, caplog: pytest.LogCaptureFixture
) -> None:
    """Callers that pass no region (observed publishers) are unchanged."""
    values, spec, var_spec_model = antarctic_tmp2m
    with caplog.at_level(logging.WARNING, logger=pipeline_module.logger.name):
        assert pipeline_module.check_pre_encode_value_sanity(
            values, spec, var_spec_model=var_spec_model, label="no-region probe",
        )
    assert _range_warnings(caplog)


def test_spec_range_for_region_falls_back_to_range() -> None:
    spec = {"range": (0.0, 10.0), "range_by_region": {GLOBAL: (0.0, 99.0)}}
    assert pipeline_module._spec_range_for_region(spec, None) == (0.0, 10.0)
    assert pipeline_module._spec_range_for_region(spec, CANONICAL) == (0.0, 10.0)
    assert pipeline_module._spec_range_for_region(spec, GLOBAL) == (0.0, 99.0)
    assert pipeline_module._spec_range_for_region({"range": (0.0, 1.0)}, GLOBAL) == (0.0, 1.0)


def test_global_envelopes_only_widen_the_declared_ranges() -> None:
    """A global entry may never be tighter than its NA counterpart."""
    from app.services.colormaps import COLOR_MAP_SPECS

    seen = 0
    for color_map_id, spec in COLOR_MAP_SPECS.items():
        by_region = spec.get("range_by_region")
        if not isinstance(by_region, dict) or GLOBAL not in by_region:
            continue
        seen += 1
        base_min, base_max = spec["range"]
        global_min, global_max = by_region[GLOBAL]
        assert global_min <= base_min, color_map_id
        assert global_max >= base_max, color_map_id
    assert seen >= 9, "expected the GFS global variables to carry global envelopes"


# ── 6. global region geometry (plan §1) ────────────────────────────────────


def test_global_grid_is_native_4326_at_quarter_degree() -> None:
    """Contract §1: 1440 × 721 EPSG:4326 at 0.25°, both poles as real rows."""
    grid = get_target_grid(MODEL, GLOBAL)
    assert grid.crs == "EPSG:4326"
    assert grid.resolution == 0.25
    assert (grid.height, grid.width) == (721, 1440)
    assert tuple(grid.transform)[:6] == pytest.approx((0.25, 0.0, -180.125, 0.0, -0.25, 90.125))
    assert grid.bbox == pytest.approx((-180.125, -90.125, 179.875, 90.125))

    # Column/row centres, stated the way the contract states them.
    first_lon, first_lat = grid.transform * (0.5, 0.5)
    last_lon, last_lat = grid.transform * (grid.width - 0.5, grid.height - 0.5)
    assert (first_lon, first_lat) == pytest.approx((-180.0, 90.0))
    assert (last_lon, last_lat) == pytest.approx((179.75, -90.0))


def test_mercator_global_grid_is_no_longer_producible() -> None:
    """The retired 25 km 3857 global grid must not be reachable."""
    assert GLOBAL not in REGION_BBOX_3857
    assert GLOBAL not in GFS_MODEL.capabilities.grid_meters_by_region
    assert GFS_MODEL.capabilities.grid_native_degrees_by_region == {GLOBAL: 0.25}
    bbox, resolution = get_grid_params(MODEL, GLOBAL)
    assert resolution == 0.25
    assert bbox == REGION_BBOX_4326[GLOBAL]


def test_metre_grid_helper_rejects_a_native_geographic_pair() -> None:
    """A degree pair fed to the metre snap rule must raise, not silently warp.

    `get_grid_params(model, "global")` returns degrees now, so any legacy
    caller that still pairs it with `compute_transform_and_shape` would get a
    plausible-looking 722 × 1441 grid with a half-cell-shifted origin. Fail
    loudly and name the correct entry point instead.
    """
    bbox, resolution = get_grid_params(MODEL, GLOBAL)
    with pytest.raises(ValueError, match="get_target_grid"):
        compute_transform_and_shape(bbox, resolution)

    # The metre path is untouched.
    transform, height, width = compute_transform_and_shape(REGION_BBOX_3857["conus"], 25_000.0)
    assert (height, width) > (0, 0)
    assert transform.a == 25_000.0


def test_canonical_regions_keep_their_mercator_grids() -> None:
    """The native declaration must not leak into the canonical domains."""
    for region in ("na", "conus", "pnw"):
        assert get_grid_crs(MODEL, region) == "EPSG:3857"
        grid = get_target_grid(MODEL, region)
        assert grid.resolution == 25_000.0
        assert grid.bbox == REGION_BBOX_3857[region]

    projected = transform_bounds("EPSG:4326", "EPSG:3857", *REGION_BBOX_4326["na"])
    for value, expected in zip(projected, REGION_BBOX_3857["na"]):
        assert value == pytest.approx(expected, abs=1.0)


def test_written_frames_and_manifest_declare_epsg_4326(tmp_path: Path) -> None:
    """Contract §4: the projection is carried, never inferred."""
    import json

    import numpy as np

    from app.services.grid import (
        build_grid_manifests_for_run_root,
        write_grid_frames_for_run_root,
    )

    grid = get_target_grid(MODEL, GLOBAL)
    run_root = tmp_path / "published" / MODEL / "domains" / GLOBAL / "20260730_00z"
    (run_root / DECLARING_VAR).mkdir(parents=True)
    values = np.zeros((grid.height, grid.width), dtype=np.float32)

    write_grid_frames_for_run_root(
        run_root=run_root,
        model=MODEL,
        var=DECLARING_VAR,
        fh=0,
        values=values,
        transform=grid.transform,
        projection=get_grid_crs(MODEL, GLOBAL),
    )
    assert build_grid_manifests_for_run_root(
        run_root=run_root, model=MODEL, run="20260730_00z", variables=(DECLARING_VAR,)
    )

    frame_meta = json.loads(
        (run_root / DECLARING_VAR / "grid" / "fh000.l0.meta.json").read_text()
    )
    assert frame_meta["projection"] == "EPSG:4326"
    assert (frame_meta["height"], frame_meta["width"]) == (721, 1440)
    assert frame_meta["bbox"] == pytest.approx([-180.125, -90.125, 179.875, 90.125])

    manifest = json.loads((run_root / DECLARING_VAR / "grid" / "manifest.json").read_text())
    assert manifest["projection"] == "EPSG:4326"
    assert (manifest["grid"]["height"], manifest["grid"]["width"]) == (721, 1440)


def test_global_lod_chain_is_not_degenerate() -> None:
    """The global grid reuses GFS's region-independent LOD config (§1)."""
    from app.services.grid import grid_lod_specs

    grid = get_target_grid(MODEL, GLOBAL)
    lods = grid_lod_specs(MODEL, DECLARING_VAR)
    assert lods
    for lod in lods:
        step = int(getattr(lod, "step", 1) or 1)
        assert grid.height // step >= 2 and grid.width // step >= 2, lod
