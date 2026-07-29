"""Phase 2A — backend artifact-domain contract.

Covers the design's §5 test list (items 1–30). Two properties dominate:

* **Canonical byte-for-byte.** With no ``domain=`` supplied and no model
  declaring extra ``supported_build_regions`` (the 2A shipping state), every
  path, URL and payload is identical to pre-change. The golden path tests
  assert hardcoded literal strings, never helper composition.
* **No crossing.** LATEST pointers, manifests, retention, sampling, caches and
  edge-served artifacts can never leak between domains.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pytest
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "test-client")
os.environ.setdefault("TWF_CLIENT_SECRET", "test-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_domains_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import main as main_module  # noqa: E402
from app.models.base import RegionSpec  # noqa: E402
from app.models.hrrr import HRRR_CAPABILITIES, HRRR_REGIONS  # noqa: E402
from app.models.registry import MODEL_REGISTRY  # noqa: E402
from app.services import admin_telemetry, domains as domains_module, frames_404_telemetry  # noqa: E402
from app.services import sampling as sampling_module  # noqa: E402
from app.services import scheduler as scheduler_module  # noqa: E402
from app.services.builder import raster_grid  # noqa: E402
from app.services.grid import(  # noqa: E402
    build_grid_manifests_for_run_root,
    grid_dir,
    grid_frame_meta_path,
    grid_frame_path,
    grid_manifest_path,
    write_grid_frames_for_run_root,
)
from app.services.run_ids import RUN_ID_RE  # noqa: E402

pytestmark = pytest.mark.anyio

MODEL = "hrrr"
RUN_ID = "20260330_12z"
VAR = "tmp2m"
GLOBAL = "global"
TRANSFORM = from_origin(-14920000.0, 7362000.0, 3000.0, 3000.0)


# ── fixtures / helpers ─────────────────────────────────────────────────────


def _clear_main_caches() -> None:
    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    main_module._grid_manifest_cache.clear()
    sampling_module._FRAME_META_CACHE.clear()


def _published_model_root(data_root: Path, model: str, domain: str | None) -> Path:
    """Physical published model root, written out longhand on purpose."""
    if domain is None or domain == domains_module.canonical_domain(model):
        return data_root / "published" / model
    return data_root / "published" / model / "domains" / domain


def _manifests_model_root(data_root: Path, model: str, domain: str | None) -> Path:
    if domain is None or domain == domains_module.canonical_domain(model):
        return data_root / "manifests" / model
    return data_root / "manifests" / model / "domains" / domain


def _publish_fixture(
    data_root: Path,
    *,
    model: str = MODEL,
    run_id: str = RUN_ID,
    var: str = VAR,
    domain: str | None = None,
    value: float = 32.0,
    write_latest: bool = True,
) -> Path:
    """Write one published frame (+ grid manifest, run manifest, LATEST)."""
    run_root = _published_model_root(data_root, model, domain) / run_id
    var_dir = run_root / var
    var_dir.mkdir(parents=True, exist_ok=True)
    write_grid_frames_for_run_root(
        run_root=run_root,
        model=model,
        var=var,
        fh=0,
        values=np.full((2, 2), value, dtype=np.float32),
        transform=TRANSFORM,
    )
    (var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "fh": 0,
                "units": "F",
                "valid_time": "2026-03-30T12:00:00Z",
                "domain_probe": domain or "canonical",
                "contours": {"freezing": {"format": "geojson", "path": "contours/fh000_freezing.geojson"}},
                "vector_layers": {"barbs": {"format": "geojson", "path": "vectors/fh000_barbs.geojson"}},
            }
        )
    )
    (var_dir / "contours").mkdir(parents=True, exist_ok=True)
    (var_dir / "contours" / "fh000_freezing.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "domain_probe": domain or "canonical", "features": []})
    )
    (var_dir / "vectors").mkdir(parents=True, exist_ok=True)
    (var_dir / "vectors" / "fh000_barbs.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "domain_probe": domain or "canonical", "features": []})
    )
    build_grid_manifests_for_run_root(run_root=run_root, model=model, run=run_id, variables=(var,))

    if write_latest:
        (run_root.parent / "LATEST.json").write_text(json.dumps({"run_id": run_id}))

    manifest_dir = _manifests_model_root(data_root, model, domain)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "contract_version": "3.0",
                "model": model,
                "run": run_id,
                "region": domain or domains_module.canonical_domain(model),
                "variables": {
                    var: {
                        "display_name": "Surface Temp",
                        "kind": "continuous",
                        "units": "F",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [{"fh": 0, "valid_time": "2026-03-30T12:00:00Z"}],
                    }
                },
            }
        )
    )
    return run_root


@pytest.fixture
def api_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_root = tmp_path / "data"
    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", data_root / "published")
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", data_root / "manifests")
    _clear_main_caches()
    yield data_root
    _clear_main_caches()


@pytest.fixture
async def client(api_roots: Path) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


@pytest.fixture
def declared_global(monkeypatch: pytest.MonkeyPatch) -> None:
    """Declare a `global` domain for HRRR ``tmp2m`` — capability data only.

    This is exactly the Phase 3 activation switch: a RegionSpec, grid params,
    ``supported_build_regions`` on the variable, and the model named in
    ``CARTOSKY_GLOBAL_DOMAIN_MODELS`` (Phase 3 §3 — the dark-rollout
    allowlist, which drops declared extras everywhere while unset). No code
    changes.
    """
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", MODEL)
    monkeypatch.setitem(HRRR_REGIONS, GLOBAL, RegionSpec(id=GLOBAL, name="Global"))
    monkeypatch.setitem(
        raster_grid.REGION_BBOX_3857,
        GLOBAL,
        (-20037508.34, -20037508.34, 20037508.34, 20037508.34),
    )
    monkeypatch.setitem(raster_grid.TARGET_GRID_METERS[MODEL], GLOBAL, 9_000.0)
    capability = HRRR_CAPABILITIES.variable_catalog[VAR]
    monkeypatch.setitem(
        HRRR_CAPABILITIES.variable_catalog,
        VAR,
        replace(capability, supported_build_regions=[GLOBAL]),
    )


class _FakeGlobalPlugin:
    """Scheduler plugin declaring canonical `conus` plus `global` for tmp2m."""

    id = MODEL
    capabilities = None

    def normalize_var_id(self, var_id: str) -> str:
        return str(var_id).strip().lower()

    def scheduled_fhs_for_var(self, var_key: str, cycle_hour: int) -> list[int]:
        del var_key, cycle_hour
        return [0, 1]

    def get_region(self, region_id: str):
        return RegionSpec(id=region_id, name=region_id) if region_id in {"conus", GLOBAL} else None

    def get_var_capability(self, var_key: str):
        if var_key != VAR:
            return None
        return HRRR_CAPABILITIES.variable_catalog[VAR]


# ── A. canonical byte-for-byte preservation (§5.1–4, §5.30) ────────────────


@pytest.mark.parametrize("model", sorted(MODEL_REGISTRY))
@pytest.mark.parametrize("explicit_canonical", [False, True])
def test_golden_path_literals_are_unchanged(model: str, explicit_canonical: bool) -> None:
    """§5.1 — every path helper returns the exact pre-change literal string."""
    data_root = Path("/opt/cartosky/data")
    run = "20260330_12z"
    canonical = domains_module.canonical_domain(model)
    domain = canonical if explicit_canonical else None

    plugin = MODEL_REGISTRY[model]
    runtime_var = scheduler_module._runtime_var_id(
        plugin, VAR, scheduler_module._var_default_ensemble_view(plugin, VAR)
    )

    assert str(
        scheduler_module._frame_sidecar_path(data_root, model, run, VAR, 0, region=domain)
    ) == f"/opt/cartosky/data/staging/{model}/{run}/{runtime_var}/fh000.json"
    assert str(
        scheduler_module._frame_primary_artifact_path(data_root, model, run, VAR, 0, region=domain)
    ) == f"/opt/cartosky/data/staging/{model}/{run}/{runtime_var}/grid/fh000.l0.meta.json"
    assert str(
        scheduler_module._latest_pointer_path(data_root, model, region=domain)
    ) == f"/opt/cartosky/data/published/{model}/LATEST.json"
    assert str(
        scheduler_module._manifest_path(data_root, model, run, region=domain)
    ) == f"/opt/cartosky/data/manifests/{model}/{run}.json"

    assert str(
        grid_dir(data_root, model, run, VAR, domain=domain)
    ) == f"/opt/cartosky/data/published/{model}/{run}/{VAR}/grid"
    assert str(
        grid_manifest_path(data_root, model, run, VAR, domain=domain)
    ) == f"/opt/cartosky/data/published/{model}/{run}/{VAR}/grid/manifest.json"
    assert str(
        grid_frame_path(data_root, model, run, VAR, 0, domain=domain)
    ) == f"/opt/cartosky/data/published/{model}/{run}/{VAR}/grid/fh000.l0.u16.bin"
    assert str(
        grid_frame_meta_path(data_root, model, run, VAR, 0, domain=domain)
    ) == f"/opt/cartosky/data/published/{model}/{run}/{VAR}/grid/fh000.l0.meta.json"

    assert "domains" not in scheduler_module._staging_run_root(data_root, model, run, domain).parts


@pytest.mark.parametrize("model", sorted(MODEL_REGISTRY))
@pytest.mark.parametrize("explicit_canonical", [False, True])
def test_golden_api_path_literals_are_unchanged(
    model: str, explicit_canonical: bool, api_roots: Path
) -> None:
    """§5.1 — the API-side path helpers, same literals."""
    run = "20260330_12z"
    canonical = domains_module.canonical_domain(model)
    domain = canonical if explicit_canonical else None
    root = str(api_roots)

    assert str(main_module._manifest_path(model, run, domain=domain)) == f"{root}/manifests/{model}/{run}.json"
    assert str(main_module._latest_pointer_path(model, domain=domain)) == f"{root}/published/{model}/LATEST.json"
    assert str(
        main_module._published_var_dir(model, run, VAR, domain=domain)
    ) == f"{root}/published/{model}/{run}/{VAR}"


@pytest.mark.parametrize("model", sorted(MODEL_REGISTRY))
def test_canonical_artifact_urls_carry_no_domain_marker(model: str) -> None:
    """§5.30 — canonical grid URLs contain neither `domains/` nor `domain=`."""
    canonical = domains_module.canonical_domain(model)
    for domain in (None, canonical):
        url = main_module._grid_file_url(
            model, RUN_ID, VAR, "fh000.l0.u16.bin", version_token="tok", domain=domain,
        )
        assert url == f"/api/v4/grid/{model}/{RUN_ID}/{VAR}/fh000.l0.u16.bin?v=tok"
        assert "domains/" not in url
        assert "domain=" not in url
    assert domains_module.domain_url_prefix(model, None) == ""
    assert domains_module.domain_url_prefix(model, canonical) == ""


@pytest.mark.parametrize("model", sorted(MODEL_REGISTRY))
def test_normalize_domain_identities_and_reserved_words(model: str) -> None:
    """§5.3 — absent == canonical; reserved and run-id-shaped ids rejected."""
    canonical = domains_module.canonical_domain(model)
    assert domains_module.normalize_domain(model, None) == canonical
    assert domains_module.normalize_domain(model, "") == canonical
    assert domains_module.normalize_domain(model, canonical.upper()) == canonical
    assert domains_module.normalize_domain(model, canonical) == canonical
    assert domains_module.is_canonical(model, None)
    assert domains_module.is_canonical(model, canonical)

    assert domains_module.is_reserved_domain_id("")
    assert domains_module.validate_requested_domain(model, "") == canonical
    assert domains_module.validate_requested_domain(model, None) == canonical
    for bad in ("domains", "DOMAINS", "20260330_12z", "20260330_1200z", "..", "../etc", "a/b"):
        assert domains_module.is_reserved_domain_id(bad), bad
        assert domains_module.validate_requested_domain(model, bad) is None


def test_reserved_words_hold_for_every_declared_region_id() -> None:
    """§5.28 — no RegionSpec id in any plugin is `domains` or run-id-shaped."""
    seen = 0
    for model_id, plugin in MODEL_REGISTRY.items():
        for region_id in getattr(plugin, "regions", {}) or {}:
            seen += 1
            token = str(region_id).strip().lower()
            assert token != "domains", f"{model_id} declares the reserved region id 'domains'"
            assert RUN_ID_RE.match(token) is None, f"{model_id} region {token!r} parses as a run id"
            assert not domains_module.is_reserved_domain_id(token), f"{model_id}/{token}"
        assert model_id != "domains"
    assert seen > 0


def test_publish_helpers_write_the_canonical_literal_tree(tmp_path: Path) -> None:
    """§5.4 — promote + manifest + LATEST land on today's literal paths."""
    data_root = tmp_path
    staging_var = data_root / "staging" / MODEL / RUN_ID / VAR
    staging_var.mkdir(parents=True, exist_ok=True)
    (staging_var / "fh000.json").write_text(json.dumps({"fh": 0, "units": "F"}))

    scheduler_module._promote_run(data_root, MODEL, RUN_ID)
    scheduler_module._write_run_manifest(
        data_root=data_root,
        model=MODEL,
        run_id=RUN_ID,
        targets=[("conus", VAR, 0)],
        plugin=MODEL_REGISTRY[MODEL],
        region="conus",
    )
    scheduler_module._write_latest_pointer(data_root, MODEL, RUN_ID, region="conus")

    assert (data_root / "published" / MODEL / RUN_ID / VAR / "fh000.json").is_file()
    assert (data_root / "manifests" / MODEL / f"{RUN_ID}.json").is_file()
    assert (data_root / "published" / MODEL / "LATEST.json").is_file()
    assert not (data_root / "published" / MODEL / "domains").exists()
    assert not (data_root / "manifests" / MODEL / "domains").exists()

    latest = json.loads((data_root / "published" / MODEL / "LATEST.json").read_text())
    assert latest["run_id"] == RUN_ID
    assert latest["region"] == "conus"


def test_write_latest_pointer_records_the_models_own_canonical_domain(tmp_path: Path) -> None:
    """§4.2 M5 — the retired "conus" fallback mislabelled `na` models."""
    scheduler_module._write_latest_pointer(tmp_path, "gfs", RUN_ID)
    payload = json.loads((tmp_path / "published" / "gfs" / "LATEST.json").read_text())
    assert payload["region"] == "na"


# ── B. coexistence without collision (§5.5–7) ──────────────────────────────


def test_domain_and_canonical_artifacts_never_share_a_path(tmp_path: Path) -> None:
    """§5.5 — same model/run/var, distinct trees; canonical bytes untouched."""
    data_root = tmp_path
    canonical_root = _publish_fixture(data_root, value=32.0)
    canonical_frame = canonical_root / VAR / "grid" / "fh000.l0.u16.bin"
    before = hashlib.sha256(canonical_frame.read_bytes()).hexdigest()

    global_root = _publish_fixture(data_root, domain=GLOBAL, value=99.0)

    assert canonical_root != global_root
    assert global_root == data_root / "published" / MODEL / "domains" / GLOBAL / RUN_ID
    assert hashlib.sha256(canonical_frame.read_bytes()).hexdigest() == before

    global_frame = global_root / VAR / "grid" / "fh000.l0.u16.bin"
    assert global_frame.is_file()
    assert global_frame.read_bytes() != canonical_frame.read_bytes()

    # Manifests and LATEST pointers are siblings, never the same file.
    assert (
        _manifests_model_root(data_root, MODEL, None) / f"{RUN_ID}.json"
        != _manifests_model_root(data_root, MODEL, GLOBAL) / f"{RUN_ID}.json"
    )


def test_build_regions_for_var_reads_declared_supported_build_regions(
    declared_global: None,
) -> None:
    """§5.6 — declaration is capability data; absent it, exactly [canonical]."""
    plugin = MODEL_REGISTRY[MODEL]
    assert scheduler_module._build_regions_for_var(plugin, VAR) == ["conus", GLOBAL]
    assert scheduler_module._build_regions_for_var(plugin, "dp2m") == ["conus"]
    assert domains_module.declared_domains_for_var(plugin, VAR) == ("conus", GLOBAL)


def test_build_regions_for_var_without_declaration_is_canonical_only() -> None:
    """§5.6 (negative) — the 2A shipping state for every registered model."""
    for model_id, plugin in MODEL_REGISTRY.items():
        canonical = domains_module.canonical_domain(plugin)
        catalog = getattr(getattr(plugin, "capabilities", None), "variable_catalog", {}) or {}
        for var_key in catalog:
            assert domains_module.declared_domains_for_var(plugin, var_key) == (canonical,), (
                f"{model_id}/{var_key} declares extra build regions in the 2A shipping state"
            )


def test_grid_manifest_build_over_a_domain_root_stays_inside_that_tree(tmp_path: Path) -> None:
    """§5.7 — building manifests for a domain root writes only under it."""
    data_root = tmp_path
    _publish_fixture(data_root, value=32.0)
    canonical_manifest = grid_manifest_path(data_root, MODEL, RUN_ID, VAR)
    canonical_before = canonical_manifest.read_bytes()

    global_root = _published_model_root(data_root, MODEL, GLOBAL) / RUN_ID
    (global_root / VAR).mkdir(parents=True, exist_ok=True)
    write_grid_frames_for_run_root(
        run_root=global_root, model=MODEL, var=VAR, fh=0,
        values=np.full((2, 2), 99.0, dtype=np.float32), transform=TRANSFORM,
    )
    (global_root / VAR / "fh000.json").write_text(json.dumps({"fh": 0, "units": "F"}))

    assert build_grid_manifests_for_run_root(run_root=global_root, model=MODEL, run=RUN_ID) == 1
    assert (global_root / VAR / "grid" / "manifest.json").is_file()
    assert canonical_manifest.read_bytes() == canonical_before


# ── C. LATEST / manifests / retention / sampling cannot cross (§5.8–13) ────


def test_latest_pointer_never_falls_back_across_domains(api_roots: Path) -> None:
    """§5.8 — a domain with no LATEST resolves to nothing, not to canonical."""
    _publish_fixture(api_roots)
    assert main_module._latest_run_from_pointer_for_region(MODEL, domain=None) == RUN_ID
    assert main_module._latest_run_from_pointer_for_region(MODEL, domain=GLOBAL) is None

    _publish_fixture(api_roots, domain=GLOBAL, run_id="20260330_13z", value=99.0)
    assert main_module._latest_run_from_pointer_for_region(MODEL, domain=None) == RUN_ID
    assert main_module._latest_run_from_pointer_for_region(MODEL, domain=GLOBAL) == "20260330_13z"


def test_scan_manifest_runs_reroots_manifest_dir_and_published_cross_check(
    api_roots: Path,
) -> None:
    """§5.9 / §5.25 — both the manifest dir AND the published cross-check."""
    _publish_fixture(api_roots)
    _publish_fixture(api_roots, domain=GLOBAL, run_id="20260330_13z", value=99.0)

    assert main_module._scan_manifest_runs(MODEL) == [RUN_ID]
    assert main_module._scan_manifest_runs(MODEL, domain=GLOBAL) == ["20260330_13z"]

    # A domain manifest whose domain-published run dir is missing must not be
    # rescued by a same-named canonical published run dir.
    (_manifests_model_root(api_roots, MODEL, GLOBAL) / f"{RUN_ID}.json").write_text(
        json.dumps({"variables": {}})
    )
    assert main_module._scan_manifest_runs(MODEL, domain=GLOBAL) == ["20260330_13z"]


def test_retention_never_crosses_domains(tmp_path: Path) -> None:
    """§5.10 — `domains/` survives canonical retention, and vice versa."""
    data_root = tmp_path
    published_model = data_root / "published" / MODEL
    for run in ("20260330_10z", "20260330_11z", "20260330_12z"):
        (published_model / run / VAR).mkdir(parents=True, exist_ok=True)
        (published_model / "domains" / GLOBAL / run / VAR).mkdir(parents=True, exist_ok=True)

    manifests_model = data_root / "manifests" / MODEL
    manifests_model.mkdir(parents=True, exist_ok=True)
    (manifests_model / "domains" / GLOBAL).mkdir(parents=True, exist_ok=True)
    for run in ("20260330_10z", "20260330_11z", "20260330_12z"):
        (manifests_model / f"{run}.json").write_text("{}")
        (manifests_model / "domains" / GLOBAL / f"{run}.json").write_text("{}")

    scheduler_module._enforce_run_retention(published_model, 1)
    scheduler_module._enforce_manifest_retention(manifests_model, 1)

    assert (published_model / "20260330_12z").is_dir()
    assert not (published_model / "20260330_10z").exists()
    assert (published_model / "domains" / GLOBAL).is_dir()
    for run in ("20260330_10z", "20260330_11z", "20260330_12z"):
        assert (published_model / "domains" / GLOBAL / run).is_dir()
        assert (manifests_model / "domains" / GLOBAL / f"{run}.json").is_file()
    assert (manifests_model / "domains").is_dir()

    # …and the reverse direction.
    scheduler_module._enforce_run_retention(published_model / "domains" / GLOBAL, 1)
    assert (published_model / "domains" / GLOBAL / "20260330_12z").is_dir()
    assert not (published_model / "domains" / GLOBAL / "20260330_10z").exists()
    assert (published_model / "20260330_12z").is_dir()


def test_run_scanners_neither_crash_on_nor_list_the_domains_namespace(
    tmp_path: Path,
) -> None:
    """§5.11 — member backfill scan, stats-health retention, admin listing."""
    data_root = tmp_path
    published_model = data_root / "published" / MODEL
    (published_model / RUN_ID / VAR).mkdir(parents=True, exist_ok=True)
    (published_model / "domains" / GLOBAL / RUN_ID / VAR).mkdir(parents=True, exist_ok=True)

    assert admin_telemetry._published_run_ids(data_root, MODEL, keep_runs=10) == [RUN_ID]

    health_root = data_root / "status" / "ensemble_stats" / MODEL
    health_root.mkdir(parents=True, exist_ok=True)
    (health_root / f"{RUN_ID}.json").write_text("{}")
    (health_root / "20260101_00z.json").write_text("{}")
    scheduler_module._enforce_ensemble_stats_health_retention(health_root, published_model)
    assert (health_root / f"{RUN_ID}.json").is_file()
    assert not (health_root / "20260101_00z.json").exists()

    # The published scan the member backfill runs skips non-run-id children.
    scanned = [
        child.name
        for child in published_model.iterdir()
        if child.is_dir()
        and not child.name.startswith(".")
        and scheduler_module._parse_run_id_datetime(child.name) is not None
    ]
    assert scanned == [RUN_ID]


def test_sampling_and_cache_keys_are_domain_isolated(api_roots: Path) -> None:
    """§5.12 — resolver picks the right tree; cache keys differ per domain."""
    _publish_fixture(api_roots, value=32.0)
    _publish_fixture(api_roots, domain=GLOBAL, value=99.0)

    canonical = sampling_module._resolve_binary_grid_frame(MODEL, RUN_ID, VAR, 0)
    scoped = sampling_module._resolve_binary_grid_frame(MODEL, RUN_ID, VAR, 0, domain=GLOBAL)
    assert canonical is not None and scoped is not None
    assert "domains" not in canonical[0].parts
    assert scoped[0].parts[-6:-4] == ("domains", GLOBAL)
    assert canonical[0].read_bytes() != scoped[0].read_bytes()

    sidecar_canonical = sampling_module._resolve_sidecar(MODEL, RUN_ID, VAR, 0)
    sidecar_global = sampling_module._resolve_sidecar(MODEL, RUN_ID, VAR, 0, domain=GLOBAL)
    assert sidecar_canonical["domain_probe"] == "canonical"
    assert sidecar_global["domain_probe"] == GLOBAL

    key_canonical = main_module._sample_cache_key(MODEL, RUN_ID, VAR, 0, 1, 1, None, None)
    key_explicit = main_module._sample_cache_key(MODEL, RUN_ID, VAR, 0, 1, 1, None, "conus")
    key_global = main_module._sample_cache_key(MODEL, RUN_ID, VAR, 0, 1, 1, None, GLOBAL)
    assert key_canonical == key_explicit == f"{MODEL}:{RUN_ID}:{VAR}:-:0:1:1"
    assert key_global != key_canonical
    batch_canonical = main_module._sample_batch_cache_key(MODEL, RUN_ID, VAR, 0, "h", None, None)
    assert batch_canonical == f"batch:{MODEL}:{RUN_ID}:{VAR}:-:0:h"
    assert main_module._sample_batch_cache_key(MODEL, RUN_ID, VAR, 0, "h", None, GLOBAL) != batch_canonical


async def test_api_routes_serve_only_the_requested_domain(
    client: httpx.AsyncClient, api_roots: Path, declared_global: None
) -> None:
    """§5.13 / §5.26 — frames, grid-manifest and grid files stay in-domain."""
    _publish_fixture(api_roots, value=32.0)
    _publish_fixture(api_roots, domain=GLOBAL, value=99.0)
    _clear_main_caches()

    canonical_frames = await client.get(f"/api/v4/{MODEL}/{RUN_ID}/{VAR}/frames")
    global_frames = await client.get(f"/api/v4/{MODEL}/{RUN_ID}/{VAR}/frames?domain={GLOBAL}")
    assert canonical_frames.status_code == 200
    assert global_frames.status_code == 200
    assert canonical_frames.json()[0]["meta"]["meta"]["domain_probe"] == "canonical"
    assert global_frames.json()[0]["meta"]["meta"]["domain_probe"] == GLOBAL

    canonical_gm = await client.get(f"/api/v4/{MODEL}/{RUN_ID}/{VAR}/grid-manifest")
    global_gm = await client.get(f"/api/v4/{MODEL}/{RUN_ID}/{VAR}/grid-manifest?domain={GLOBAL}")
    canonical_url = canonical_gm.json()["lods"][0]["frames"][0]["url"]
    global_url = global_gm.json()["lods"][0]["frames"][0]["url"]
    assert canonical_url.startswith(f"/api/v4/grid/{MODEL}/{RUN_ID}/{VAR}/")
    assert "domains/" not in canonical_url
    assert global_url.startswith(f"/api/v4/grid/domains/{GLOBAL}/{MODEL}/{RUN_ID}/{VAR}/")
    assert canonical_url != global_url

    canonical_bytes = await client.get(canonical_url)
    global_bytes = await client.get(global_url)
    assert canonical_bytes.status_code == 200
    assert global_bytes.status_code == 200
    assert canonical_bytes.content != global_bytes.content

    compat = await client.get(
        f"/api/v4/grid/v1/domains/{GLOBAL}/{MODEL}/{RUN_ID}/{VAR}/fh000.l0.u16.bin"
    )
    assert compat.status_code == 200
    assert compat.content == global_bytes.content


async def test_domain_grid_file_uses_accel_redirect_under_published_root(
    client: httpx.AsyncClient, api_roots: Path, declared_global: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§5.13 — X-Accel-Redirect keeps working for domain artifacts."""
    _publish_fixture(api_roots, domain=GLOBAL, value=99.0)
    _clear_main_caches()
    monkeypatch.setattr(main_module, "GRID_ACCEL_REDIRECT_ENABLED", True)
    monkeypatch.setattr(main_module, "GRID_ACCEL_REDIRECT_PREFIX", "/_grid/")

    response = await client.get(
        f"/api/v4/grid/domains/{GLOBAL}/{MODEL}/{RUN_ID}/{VAR}/fh000.l0.u16.bin"
    )
    assert response.status_code == 200
    assert response.headers["x-accel-redirect"] == (
        f"/_grid/{MODEL}/domains/{GLOBAL}/{RUN_ID}/{VAR}/grid/fh000.l0.u16.bin"
    )


async def test_domain_contour_and_vector_routes_are_isolated(
    client: httpx.AsyncClient, api_roots: Path, declared_global: None
) -> None:
    """§5.23 / §5.26 — contour and vector families, both domains."""
    _publish_fixture(api_roots, value=32.0)
    _publish_fixture(api_roots, domain=GLOBAL, value=99.0)
    _clear_main_caches()

    for family in ("contours/freezing", "vectors/barbs"):
        canonical = await client.get(f"/api/v4/{MODEL}/{RUN_ID}/{VAR}/0/{family}")
        scoped = await client.get(f"/api/v4/domains/{GLOBAL}/{MODEL}/{RUN_ID}/{VAR}/0/{family}")
        assert canonical.status_code == 200, family
        assert scoped.status_code == 200, family
        assert canonical.json()["domain_probe"] == "canonical"
        assert scoped.json()["domain_probe"] == GLOBAL

        # An undeclared domain 404s rather than falling back cross-domain.
        missing = await client.get(f"/api/v4/domains/atlantis/{MODEL}/{RUN_ID}/{VAR}/0/{family}")
        assert missing.status_code == 404


async def test_canonical_artifact_routes_ignore_a_domain_query(
    client: httpx.AsyncClient, api_roots: Path, declared_global: None
) -> None:
    """§5.23 / locked decision #5 — `domain=` never isolates artifact bodies.

    Canonical grid/contour/vector routes serve immutable bodies under
    `max-age=31536000, immutable` (and a 24h edge s-maxage for vectors).
    Isolation there is structural — the `domains/`-prefixed path routes — so
    the canonical routes must not accept a `domain` query at all: a stray one
    is an unrecognized param with no effect on the response.
    """
    _publish_fixture(api_roots, value=32.0)
    _publish_fixture(api_roots, domain=GLOBAL, value=99.0)
    _clear_main_caches()

    canonical_paths = (
        f"/api/v4/grid/{MODEL}/{RUN_ID}/{VAR}/fh000.l0.u16.bin",
        f"/api/v4/grid/v1/{MODEL}/{RUN_ID}/{VAR}/fh000.l0.u16.bin",
        f"/api/v4/{MODEL}/{RUN_ID}/{VAR}/0/contours/freezing",
        f"/api/v4/{MODEL}/{RUN_ID}/{VAR}/0/vectors/barbs",
    )
    for path in canonical_paths:
        plain = await client.get(path)
        with_domain = await client.get(f"{path}?domain={GLOBAL}")
        assert plain.status_code == 200, path
        assert with_domain.status_code == 200, path
        assert with_domain.content == plain.content, path

    # The bytes served are the CANONICAL ones, not the global tree's.
    canonical_frame = (
        _published_model_root(api_roots, MODEL, None) / RUN_ID / VAR / "grid" / "fh000.l0.u16.bin"
    )
    global_frame = (
        _published_model_root(api_roots, MODEL, GLOBAL) / RUN_ID / VAR / "grid" / "fh000.l0.u16.bin"
    )
    assert canonical_frame.read_bytes() != global_frame.read_bytes()
    served = await client.get(f"{canonical_paths[0]}?domain={GLOBAL}")
    assert served.content == canonical_frame.read_bytes()
    assert "immutable" in served.headers.get("cache-control", "")

    for family in ("contours/freezing", "vectors/barbs"):
        response = await client.get(f"/api/v4/{MODEL}/{RUN_ID}/{VAR}/0/{family}?domain={GLOBAL}")
        assert response.json()["domain_probe"] == "canonical", family


def test_canonical_artifact_routes_declare_no_domain_query_param() -> None:
    """Locked decision #5 — enforced in the signature, not just in behaviour."""
    artifact_routes = {
        "/api/v4/grid/{model}/{run}/{var}/{filename}",
        "/api/v4/grid/v1/{model}/{run}/{var}/{filename}",
        "/api/v4/{model}/{run}/{var}/{fh:int}/contours/{key}",
        "/api/v4/{model}/{run}/{var}/{fh:int}/vectors/{key}",
    }
    seen: set[str] = set()
    for route in main_module.app.routes:
        path = getattr(route, "path", "")
        if path not in artifact_routes:
            continue
        seen.add(path)
        params = set(getattr(route, "endpoint").__code__.co_varnames)
        assert "domain" not in params, f"{path} exposes a domain query param"
    assert seen == artifact_routes


# ── D. promotion atomicity (§5.14–16) ──────────────────────────────────────


def test_domain_promote_keeps_tmp_trash_and_final_as_siblings(tmp_path: Path) -> None:
    """§5.14 — the two-rename swap stays inside one directory per domain."""
    data_root = tmp_path
    stage_var = data_root / "staging" / MODEL / "domains" / GLOBAL / RUN_ID / VAR
    stage_var.mkdir(parents=True, exist_ok=True)
    (stage_var / "fh000.json").write_text("{}")

    observed: list[tuple[Path, Path]] = []
    real_rename = os.rename

    def _spy(src, dst):
        observed.append((Path(src), Path(dst)))
        return real_rename(src, dst)

    scheduler_module.os.rename = _spy
    try:
        scheduler_module._promote_run(data_root, MODEL, RUN_ID, domain=GLOBAL)
    finally:
        scheduler_module.os.rename = real_rename

    published_domain_root = data_root / "published" / MODEL / "domains" / GLOBAL
    assert (published_domain_root / RUN_ID / VAR / "fh000.json").is_file()
    assert not (data_root / "published" / MODEL / RUN_ID).exists()
    for src, dst in observed:
        assert src.parent == dst.parent == published_domain_root


def test_domain_promote_restores_previous_published_run_on_rename_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§5.14 — a failed swap leaves the previous published run in place."""
    data_root = tmp_path
    stage_var = data_root / "staging" / MODEL / "domains" / GLOBAL / RUN_ID / VAR
    stage_var.mkdir(parents=True, exist_ok=True)
    (stage_var / "fh000.json").write_text('{"gen": 2}')

    published_run = data_root / "published" / MODEL / "domains" / GLOBAL / RUN_ID
    (published_run / VAR).mkdir(parents=True, exist_ok=True)
    (published_run / VAR / "fh000.json").write_text('{"gen": 1}')

    real_rename = os.rename

    def _failing(src, dst):
        if Path(src).name.endswith(".tmp"):
            raise OSError("simulated swap failure")
        return real_rename(src, dst)

    monkeypatch.setattr(scheduler_module.os, "rename", _failing)
    with pytest.raises(OSError):
        scheduler_module._promote_run(data_root, MODEL, RUN_ID, domain=GLOBAL)

    assert json.loads((published_run / VAR / "fh000.json").read_text()) == {"gen": 1}


def test_promoted_frames_share_an_inode_with_staging(tmp_path: Path) -> None:
    """§5.15 — the hardlink path executed (same filesystem, no copy)."""
    data_root = tmp_path
    stage_var = data_root / "staging" / MODEL / "domains" / GLOBAL / RUN_ID / VAR
    stage_var.mkdir(parents=True, exist_ok=True)
    staged = stage_var / "fh000.json"
    staged.write_text("{}")

    scheduler_module._promote_run(data_root, MODEL, RUN_ID, domain=GLOBAL)

    promoted = data_root / "published" / MODEL / "domains" / GLOBAL / RUN_ID / VAR / "fh000.json"
    assert promoted.stat().st_ino == staged.stat().st_ino


def test_canonical_published_run_survives_a_concurrent_domain_promote(
    tmp_path: Path,
) -> None:
    """§5.16 — canonical stays stat-able throughout a domain swap window."""
    data_root = tmp_path
    canonical_run = data_root / "published" / MODEL / RUN_ID
    (canonical_run / VAR).mkdir(parents=True, exist_ok=True)
    (canonical_run / VAR / "fh000.json").write_text("{}")

    stage_var = data_root / "staging" / MODEL / "domains" / GLOBAL / RUN_ID / VAR
    stage_var.mkdir(parents=True, exist_ok=True)
    (stage_var / "fh000.json").write_text("{}")

    real_rename = os.rename
    canonical_visible: list[bool] = []

    def _spy(src, dst):
        canonical_visible.append(canonical_run.is_dir())
        return real_rename(src, dst)

    scheduler_module.os.rename = _spy
    try:
        scheduler_module._promote_run(data_root, MODEL, RUN_ID, domain=GLOBAL)
    finally:
        scheduler_module.os.rename = real_rename

    assert canonical_visible and all(canonical_visible)
    assert canonical_run.is_dir()


# ── E. absent domain= resolves exactly as today (§5.17–19) ─────────────────


def test_meteogram_sampling_without_domain_reads_canonical_frames(
    api_roots: Path,
) -> None:
    """§5.17 — both domains published, no domain= ⇒ canonical values."""
    _publish_fixture(api_roots, value=32.0)
    _publish_fixture(api_roots, domain=GLOBAL, value=99.0)
    _clear_main_caches()

    # A point inside the 2x2 fixture grid (EPSG:3857 origin above).
    lat, lon = 54.985, -134.0017

    present, value = sampling_module.sample_binary_value(
        MODEL, RUN_ID, VAR, 0, lat=lat, lon=lon,
    )
    assert present and value == pytest.approx(32.0, abs=0.2)

    present_global, value_global = sampling_module.sample_binary_value(
        MODEL, RUN_ID, VAR, 0, lat=lat, lon=lon, domain=GLOBAL,
    )
    assert present_global and value_global == pytest.approx(99.0, abs=0.2)


@pytest.mark.parametrize("preset", ["midwest", "pnw", "na", "conus"])
async def test_legacy_region_param_behaves_exactly_like_no_param(
    client: httpx.AsyncClient, api_roots: Path, preset: str
) -> None:
    """§5.18 — `region=` is a viewport preset and never selects artifacts."""
    _publish_fixture(api_roots, value=32.0)
    _publish_fixture(api_roots, domain=GLOBAL, value=99.0)
    _clear_main_caches()

    for path in (
        f"/api/v4/{MODEL}/{RUN_ID}/{VAR}/frames",
        f"/api/v4/{MODEL}/{RUN_ID}/manifest",
        f"/api/v4/{MODEL}/{RUN_ID}/{VAR}/grid-manifest",
        f"/api/v4/grid/{MODEL}/{RUN_ID}/{VAR}/fh000.l0.u16.bin",
    ):
        plain = await client.get(path)
        with_region = await client.get(f"{path}?region={preset}")
        assert plain.status_code == with_region.status_code, path
        assert plain.content == with_region.content, path

    plain_sample = await client.get(
        f"/api/v4/sample?model={MODEL}&run={RUN_ID}&var={VAR}&fh=0&lat=40&lon=-100"
    )
    region_sample = await client.get(
        f"/api/v4/sample?model={MODEL}&run={RUN_ID}&var={VAR}&fh=0&lat=40&lon=-100&region={preset}"
    )
    assert plain_sample.status_code == region_sample.status_code
    assert plain_sample.json() == region_sample.json()


async def test_unknown_domain_returns_the_existing_404_bodies(
    client: httpx.AsyncClient, api_roots: Path
) -> None:
    """§5.19 — unknown domains reuse today's bodies verbatim."""
    _publish_fixture(api_roots)
    _clear_main_caches()

    frames = await client.get(f"/api/v4/{MODEL}/{RUN_ID}/{VAR}/frames?domain=atlantis")
    assert frames.status_code == 404
    assert frames.text == '{"error": "run not found"}'

    manifest = await client.get(f"/api/v4/{MODEL}/{RUN_ID}/manifest?domain=atlantis")
    assert manifest.status_code == 404
    assert manifest.text == '{"error": "run not found"}'

    runs = await client.get(f"/api/v4/{MODEL}/runs?domain=atlantis")
    assert runs.status_code == 404
    assert runs.text == '{"error": "run not found"}'

    sample = await client.get(
        f"/api/v4/sample?model={MODEL}&run={RUN_ID}&var={VAR}&fh=0&lat=40&lon=-100&domain=atlantis"
    )
    assert sample.status_code == 404
    assert sample.text == '{"error": "val.cog.tif not found"}'

    batch = await client.post(
        "/api/v4/sample/batch",
        json={
            "model": MODEL, "run": RUN_ID, "variable": VAR, "forecast_hour": 0,
            "domain": "atlantis", "points": [{"id": "a", "lat": 40.0, "lon": -100.0}],
        },
    )
    assert batch.status_code == 404
    assert batch.text == '{"error": "val.cog.tif not found"}'

    grid_file = await client.get(
        f"/api/v4/grid/domains/atlantis/{MODEL}/{RUN_ID}/{VAR}/fh000.l0.u16.bin"
    )
    assert grid_file.status_code == 404
    assert grid_file.json() == {"detail": "Run not found"}


# ── F. review-mandated additions (§5.20–22, 24, 27, 29) ────────────────────


@pytest.mark.parametrize("preset", ["midwest", "pnw"])
async def test_bootstrap_ignores_the_viewport_preset_for_artifact_selection(
    client: httpx.AsyncClient, api_roots: Path, preset: str
) -> None:
    """§5.20 (review blocker B3) — `region=` must not reach any resolver."""
    _publish_fixture(api_roots, value=32.0)
    _publish_fixture(api_roots, domain=GLOBAL, value=99.0)
    _clear_main_caches()

    plain = await client.get(f"/api/v4/bootstrap?model={MODEL}&run={RUN_ID}&var={VAR}")
    scoped = await client.get(
        f"/api/v4/bootstrap?model={MODEL}&run={RUN_ID}&var={VAR}&region={preset}"
    )
    assert plain.status_code == 200 and scoped.status_code == 200

    plain_payload = plain.json()
    scoped_payload = scoped.json()
    assert plain_payload["selection"]["region"] != scoped_payload["selection"]["region"]
    for key in ("model", "run", "variable", "ensemble_view"):
        assert plain_payload["selection"][key] == scoped_payload["selection"][key]
    assert plain_payload["frames"] == scoped_payload["frames"]
    assert plain_payload["manifest"] == scoped_payload["manifest"]


def test_should_promote_is_false_when_only_a_non_canonical_domain_is_ready(
    tmp_path: Path, declared_global: None
) -> None:
    """§5.21 (review blocker B1) — canonical gating is domain-specific."""
    data_root = tmp_path
    global_var = (
        data_root / "staging" / MODEL / "domains" / GLOBAL / RUN_ID / VAR
    )
    (global_var / "grid").mkdir(parents=True, exist_ok=True)
    (global_var / "fh000.json").write_text("{}")
    (global_var / "grid" / "fh000.l0.meta.json").write_text("{}")

    ready = scheduler_module._promotion_ready_regions(data_root, MODEL, RUN_ID, [VAR], [0])
    assert ready == [GLOBAL]
    assert scheduler_module._should_promote(data_root, MODEL, RUN_ID, [VAR], [0]) is False
    assert not (data_root / "published" / MODEL / "LATEST.json").exists()


def test_a_failing_domain_publish_does_not_abort_canonical_or_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared_global: None
) -> None:
    """§5.22 (review blocker B2) — per-domain failure isolation."""
    data_root = tmp_path
    run_dt = datetime(2026, 3, 30, 12, tzinfo=timezone.utc)
    plugin = _FakeGlobalPlugin()

    built: set[tuple[str, str, int]] = set()

    def fake_build_one(*, model_id, region, var_id, fh, run_dt, data_root, plugin, **kwargs):
        del model_id, run_dt, plugin, kwargs
        sidecar = scheduler_module._frame_sidecar_path(
            data_root, MODEL, RUN_ID, var_id, fh, region=region,
        )
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({"fh": fh, "units": "F"}))
        marker = scheduler_module._frame_primary_artifact_path(
            data_root, MODEL, RUN_ID, var_id, fh, region=region,
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}")
        built.add((region, var_id, fh))
        return region, var_id, fh, True

    promoted: list[str] = []
    real_promote = scheduler_module._promote_run

    def flaky_promote(root, model, run_id, *, domain=None):
        resolved = domain or "conus"
        promoted.append(resolved)
        if resolved == GLOBAL:
            raise OSError("simulated ENOSPC on the global tree")
        return real_promote(root, model, run_id, domain=domain)

    retention_calls: list[Path] = []
    real_retention = scheduler_module._enforce_run_retention

    def spy_retention(root, keep_runs):
        retention_calls.append(Path(root))
        return real_retention(root, keep_runs)

    monkeypatch.setattr(scheduler_module, "_build_one", fake_build_one)
    monkeypatch.setattr(scheduler_module, "_promote_run", flaky_promote)
    monkeypatch.setattr(scheduler_module, "_enforce_run_retention", spy_retention)
    monkeypatch.setattr(scheduler_module, "grid_build_enabled", lambda: False)

    scheduler_module._process_run(
        plugin=plugin,
        model_id=MODEL,
        vars_to_build=[VAR],
        primary_vars=[VAR],
        run_dt=run_dt,
        data_root=data_root,
        workers=1,
        keep_runs=2,
        loop_pregenerate_enabled=False,
        loop_cache_root=data_root / "loop-cache",
        loop_workers=1,
        loop_tier0_quality=82,
        loop_tier0_max_dim=2300,
        loop_tier0_fixed_w=2300,
        loop_tier1_quality=86,
        loop_tier1_max_dim=2400,
        loop_tier1_fixed_w=2400,
        rebuild_existing=False,
    )

    assert ("conus", VAR, 0) in built and (GLOBAL, VAR, 0) in built
    # Canonical published first, and the global failure was swallowed.
    assert promoted[0] == "conus"
    assert GLOBAL in promoted
    assert (data_root / "published" / MODEL / RUN_ID / VAR / "fh000.json").is_file()
    assert (data_root / "published" / MODEL / "LATEST.json").is_file()
    assert (data_root / "manifests" / MODEL / f"{RUN_ID}.json").is_file()
    # …and the retention tail still ran.
    assert (data_root / "published" / MODEL) in retention_calls
    assert (data_root / "staging" / MODEL) in retention_calls


def test_domain_run_manifest_lists_only_the_declaring_variable_subset(
    tmp_path: Path,
) -> None:
    """§5.24 (review M3) — targets are filtered by domain."""
    data_root = tmp_path
    targets = [("conus", VAR, 0), ("conus", "dp2m", 0), (GLOBAL, VAR, 0)]
    for region, var_id, fh in targets:
        sidecar = scheduler_module._frame_sidecar_path(
            data_root, MODEL, RUN_ID, var_id, fh, region=region,
        )
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({"fh": fh, "units": "F"}))

    plugin = MODEL_REGISTRY[MODEL]
    scheduler_module._write_run_manifest(
        data_root=data_root, model=MODEL, run_id=RUN_ID, targets=targets,
        plugin=plugin, region="conus",
    )
    scheduler_module._write_run_manifest(
        data_root=data_root, model=MODEL, run_id=RUN_ID, targets=targets,
        plugin=plugin, region=GLOBAL,
    )

    canonical = json.loads((data_root / "manifests" / MODEL / f"{RUN_ID}.json").read_text())
    scoped = json.loads(
        (data_root / "manifests" / MODEL / "domains" / GLOBAL / f"{RUN_ID}.json").read_text()
    )
    assert sorted(canonical["variables"]) == ["dp2m", VAR]
    assert sorted(scoped["variables"]) == [VAR]
    assert canonical["region"] == "conus"
    assert scoped["region"] == GLOBAL
    assert scoped["variables"][VAR]["expected_frames"] == 1


async def test_frames_404_telemetry_persists_the_domain_end_to_end(
    client: httpx.AsyncClient, api_roots: Path, declared_global: None
) -> None:
    """§5.27 — record parameter, `_recent` field, admin section, stale_run."""
    _publish_fixture(api_roots, domain=GLOBAL, value=99.0)
    _clear_main_caches()
    frames_404_telemetry.reset()

    # stale_run on the grid-file route (main.py's formerly domain-less site).
    stale = await client.get(
        f"/api/v4/grid/domains/{GLOBAL}/{MODEL}/20260330_23z/{VAR}/fh000.l0.u16.bin"
    )
    assert stale.status_code == 404

    section = admin_telemetry.build_frames_404_section(data_root=api_roots)
    recent = section["recent"]
    assert recent, "expected a recorded 404 sample"
    stale_entries = [item for item in recent if item.get("reason") == "stale_run"]
    assert stale_entries, recent
    assert stale_entries[0]["domain"] == GLOBAL

    frames_404_telemetry.reset()
    canonical_stale = await client.get(f"/api/v4/{MODEL}/20260330_23z/{VAR}/frames")
    assert canonical_stale.status_code == 404
    canonical_recent = admin_telemetry.build_frames_404_section(data_root=api_roots)["recent"]
    assert canonical_recent[0]["domain"] == "conus"
    frames_404_telemetry.reset()


async def test_rgb_surfaces_stay_canonical_only(
    client: httpx.AsyncClient, api_roots: Path
) -> None:
    """§5.29 — the RGB routes take no domain and keep today's behaviour."""
    baseline = await client.get("/api/v4/goes_east/20260330_1200z/true_color/rgb-manifest")
    with_domain = await client.get(
        f"/api/v4/goes_east/20260330_1200z/true_color/rgb-manifest?domain={GLOBAL}"
    )
    assert baseline.status_code == with_domain.status_code
    assert baseline.content == with_domain.content

    routes = {getattr(route, "path", "") for route in main_module.app.routes}
    assert "/api/v4/rgb/domains/{domain}/{model}/{run}/{var}/{filename}" not in routes
    assert main_module._rgb_latest_pointer_path("goes_east") == (
        api_roots / "published" / "goes_east" / "LATEST_RGB.json"
    )


def test_domain_routes_are_declared_before_the_parameterized_canonical_routes() -> None:
    """§2 implementation note — FastAPI must never bind model="domains"."""
    paths = [getattr(route, "path", "") for route in main_module.app.routes]

    def _index(path: str) -> int:
        assert path in paths, path
        return paths.index(path)

    assert _index("/api/v4/grid/domains/{domain}/{model}/{run}/{var}/{filename}") < _index(
        "/api/v4/grid/{model}/{run}/{var}/{filename}"
    )
    assert _index("/api/v4/grid/v1/domains/{domain}/{model}/{run}/{var}/{filename}") < _index(
        "/api/v4/grid/v1/{model}/{run}/{var}/{filename}"
    )
    assert _index("/api/v4/domains/{domain}/{model}/{run}/{var}/{fh:int}/contours/{key}") < _index(
        "/api/v4/{model}/{run}/{var}/{fh:int}/contours/{key}"
    )
    assert _index("/api/v4/domains/{domain}/{model}/{run}/{var}/{fh:int}/vectors/{key}") < _index(
        "/api/v4/{model}/{run}/{var}/{fh:int}/vectors/{key}"
    )


def test_build_frame_refuses_a_reserved_artifact_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reserved/traversing region id must never reach the staging path."""
    from app.services.builder import pipeline as pipeline_module

    plugin = MODEL_REGISTRY[MODEL]
    monkeypatch.setattr(
        pipeline_module, "_resolve_model_plugin", lambda model: plugin,
    )

    class _AnyRegionPlugin:
        """Accepts every region id, so only the reserved-word guard can stop it."""

        id = MODEL
        capabilities = plugin.capabilities

        def get_region(self, region_id: str):
            return RegionSpec(id=region_id, name=region_id)

        def normalize_var_id(self, var_id: str) -> str:
            return str(var_id)

        def get_var(self, var_id: str):
            return plugin.get_var(var_id)

        def get_var_capability(self, var_key: str):
            return plugin.get_var_capability(var_key)

    for bad in ("domains", "20260330_12z", "../../etc"):
        with pytest.raises(ValueError, match="reserved artifact domain"):
            pipeline_module.build_frame(
                model=MODEL,
                region=bad,
                var_id=VAR,
                fh=0,
                run_date=datetime(2026, 3, 30, 12),
                data_root=tmp_path,
                product="sfc",
                model_plugin=_AnyRegionPlugin(),
            )
    assert not (tmp_path / "staging" / MODEL / "domains").exists()
