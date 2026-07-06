"""Phase 3 member publish tests: packing fallback (R3), slim writer (R1),
member pass (R2/R4), registration + meteogram probe (R7/D1), allowlist (R8).

Synthetic arrays only — no network. See ENSEMBLE_MEMBER_SCHEDULER_DESIGN.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_bounds

from backend.app.config import member_publish_models
from backend.app.models.base import ensemble_member_descriptors, ensemble_member_ids
from backend.app.models.registry import MODEL_REGISTRY
from backend.app.services import grid
from backend.app.services.builder import members
from backend.app.services.grid import (
    _PACKING_BY_MODEL_VAR,
    _iter_grid_variable_run_roots,
    _packing_config,
    build_grid_manifests_for_run_root,
    grid_supported,
    normalize_grid_pack_var_id,
    write_grid_frame_for_run_root,
    write_slim_grid_frame_for_run_root,
)

HEIGHT, WIDTH = 60, 50
BOUNDS = (-19_820_000.0, 550_000.0, -2_780_000.0, 17_000_000.0)


@pytest.fixture()
def transform():
    return from_bounds(*BOUNDS, WIDTH, HEIGHT)


@pytest.fixture()
def values():
    data = np.linspace(0.0, 80.0, HEIGHT * WIDTH, dtype=np.float32).reshape(HEIGHT, WIDTH)
    data[:2, :] = np.nan
    return data


# ── R3: packing suffix fallback ──────────────────────────────────────
def test_normalize_grid_pack_var_id() -> None:
    assert normalize_grid_pack_var_id("tmp2m__m01") == "tmp2m__mean"
    assert normalize_grid_pack_var_id("tmp2m__m30") == "tmp2m__mean"
    assert normalize_grid_pack_var_id("tmp2m__control") == "tmp2m__mean"
    assert normalize_grid_pack_var_id("TMP2M__M05 ") == "tmp2m__mean"
    # Non-member ids pass through unchanged.
    assert normalize_grid_pack_var_id("tmp2m__mean") == "tmp2m__mean"
    assert normalize_grid_pack_var_id("tmp2m") == "tmp2m"
    assert normalize_grid_pack_var_id("precip_total__prob_gt_0p50") == "precip_total__prob_gt_0p50"
    # Only zero-padded 2-digit member suffixes qualify.
    assert normalize_grid_pack_var_id("tmp2m__m1") == "tmp2m__m1"
    assert normalize_grid_pack_var_id("tmp2m__m001") == "tmp2m__m001"


def test_packing_config_member_fallback_is_mean_entry() -> None:
    mean_packing = _PACKING_BY_MODEL_VAR[("gefs", "tmp2m__mean")]
    assert _packing_config("gefs", "tmp2m__m17") is mean_packing
    assert _packing_config("gefs", "tmp2m__control") is mean_packing
    # Exact matches still win and non-members never fall through.
    assert _packing_config("gefs", "tmp2m__mean") is mean_packing
    assert _packing_config("gefs", "nonexistent__m01") is None
    assert _packing_config("gefs", "nonexistent") is None


def test_grid_supported_flows_through_fallback() -> None:
    assert grid_supported("gefs", "tmp2m__m01")
    assert grid_supported("gefs", "tmp2m__control")
    assert not grid_supported("gefs", "bogus__m01")


def test_canary_scope_unchanged_by_fallback() -> None:
    """The fallback adds no packing keys, so canary scope derivation and
    member ids never leak into it."""
    from backend.scripts import canary_binary_sampler as canary

    scope, _, _, _ = canary._scope_for_model("gefs")
    assert "tmp2m__mean" in scope
    assert not any("__m0" in var or var.endswith("__control") for var in scope)


def test_manifest_iteration_discovers_member_dirs(tmp_path, transform, values) -> None:
    write_slim_grid_frame_for_run_root(
        run_root=tmp_path, model="gefs", var="tmp2m__m01",
        fh=0, values=values, transform=transform,
    )
    discovered = _iter_grid_variable_run_roots(tmp_path, "gefs")
    assert (tmp_path, "tmp2m__m01") in discovered
    built = build_grid_manifests_for_run_root(
        run_root=tmp_path, model="gefs", run="20260706_00z", variables=("tmp2m__m01",),
    )
    assert built == 1
    assert (tmp_path / "tmp2m__m01" / "grid" / "manifest.json").is_file()


# ── R1: slim writer vs default writer ────────────────────────────────
def test_slim_write_matches_full_writer_bin_bytes(tmp_path, transform, values) -> None:
    """For a no-display-prep variable the slim and full profiles must produce
    identical .bin bytes and core meta (the full profile only adds sidecars)."""
    full_root = tmp_path / "full"
    slim_root = tmp_path / "slim"
    full_meta = write_grid_frame_for_run_root(
        run_root=full_root, model="gefs", var="tmp2m__mean",
        fh=6, values=values, transform=transform,
    )
    slim_meta = write_slim_grid_frame_for_run_root(
        run_root=slim_root, model="gefs", var="tmp2m__m01",
        fh=6, values=values, transform=transform,
    )
    full_bin = (full_root / "tmp2m__mean" / "grid" / "fh006.l0.u16.bin").read_bytes()
    slim_bin = (slim_root / "tmp2m__m01" / "grid" / "fh006.l0.u16.bin").read_bytes()
    assert full_bin == slim_bin
    for key in ("format_version", "fh", "level", "width", "height", "bbox", "transform", "projection"):
        assert full_meta[key] == slim_meta[key], key
    assert "display_prep" not in slim_meta


def test_slim_write_never_writes_sidecars(tmp_path, transform, values, monkeypatch) -> None:
    monkeypatch.setattr(grid, "GRID_GZIP_SIDECARS_ENABLED", True)
    monkeypatch.setattr(grid, "GRID_BROTLI_SIDECARS_ENABLED", True)
    write_slim_grid_frame_for_run_root(
        run_root=tmp_path, model="gefs", var="tmp2m__m02",
        fh=0, values=values, transform=transform,
    )
    grid_dir = tmp_path / "tmp2m__m02" / "grid"
    assert (grid_dir / "fh000.l0.u16.bin").is_file()
    assert not list(grid_dir.glob("*.gz"))
    assert not list(grid_dir.glob("*.br"))
    assert not list(grid_dir.glob("*.tmp"))


def test_full_writer_still_applies_display_prep(tmp_path, transform, values) -> None:
    """Default-path behavior unchanged: display-prepped variables upscale and
    record display_prep meta exactly as before the extraction."""
    meta = write_grid_frame_for_run_root(
        run_root=tmp_path, model="gefs", var="precip_total__mean",
        fh=6, values=np.abs(values), transform=transform,
    )
    assert meta["width"] == WIDTH * 3
    assert meta["height"] == HEIGHT * 3
    assert meta["display_prep"]["upscale_factor"] == 3
    assert meta["bbox"] == pytest.approx(list(BOUNDS))


def test_member_frames_sample_through_production_sampler(tmp_path, transform, values) -> None:
    from backend.app.services.sampling import read_binary_sample_value

    write_slim_grid_frame_for_run_root(
        run_root=tmp_path, model="gefs", var="tmp2m__control",
        fh=0, values=values, transform=transform,
    )
    frame = tmp_path / "tmp2m__control" / "grid" / "fh000.l0.u16.bin"
    meta = tmp_path / "tmp2m__control" / "grid" / "fh000.l0.meta.json"
    # No caller-side normalization: the sampler resolves member packing itself.
    value, no_data = read_binary_sample_value(
        frame, meta, model="gefs", var="tmp2m__control", lat=43.5, lon=-101.5,
    )
    assert not no_data and value is not None
    value_out, no_data_out = read_binary_sample_value(
        frame, meta, model="gefs", var="tmp2m__control", lat=43.5, lon=5.0,
    )
    assert no_data_out and value_out is None


# ── R7: descriptor + member id enumeration ───────────────────────────
def test_gefs_tmp2m_descriptor_enumeration() -> None:
    plugin = MODEL_REGISTRY["gefs"]
    descriptors = ensemble_member_descriptors(plugin)
    assert set(descriptors) == {"tmp2m"}
    ids = ensemble_member_ids(descriptors["tmp2m"])
    assert len(ids) == 31
    assert ids[0] == "m01"
    assert ids[29] == "m30"
    assert ids[-1] == "control"


def test_descriptor_requires_enabled_and_count() -> None:
    class _Cap:
        def __init__(self, buildable, ensemble):
            self.buildable = buildable
            self.ensemble = ensemble

    class _Caps:
        def __init__(self, catalog):
            self.variable_catalog = catalog

    class _Plugin:
        def __init__(self, catalog):
            self.capabilities = _Caps(catalog)

    catalog = {
        "a": _Cap(True, {"members": {"count": 5, "enabled": True}}),
        "b": _Cap(True, {"members": {"count": 5, "enabled": False}}),
        "c": _Cap(True, {"members": {"count": 0, "enabled": True}}),
        "d": _Cap(False, {"members": {"count": 5, "enabled": True}}),
        "e": _Cap(True, {}),
    }
    assert set(ensemble_member_descriptors(_Plugin(catalog))) == {"a"}


def test_member_var_id_and_herbie_kwarg() -> None:
    assert members.member_var_id("tmp2m", "m07") == "tmp2m__m07"
    assert members.member_var_id("tmp2m", "control") == "tmp2m__control"
    assert members.member_herbie_kwarg("m07") == 7
    assert members.member_herbie_kwarg("m30") == 30
    assert members.member_herbie_kwarg("control") == 0


# ── R2/R4: member pass semantics (no network — build monkeypatched) ──
def _write_member_frame(staging_root: Path, transform, values, var_id: str, fh: int) -> None:
    write_slim_grid_frame_for_run_root(
        run_root=staging_root, model="gefs", var=var_id,
        fh=fh, values=values, transform=transform,
    )


@pytest.fixture()
def small_roster_plugin(monkeypatch):
    """GEFS plugin with a 2-member roster and a 3-fh schedule for fast tests."""
    plugin = MODEL_REGISTRY["gefs"]
    descriptor = {"count": 2, "control": True, "prefix": "m", "enabled": True}
    monkeypatch.setattr(
        members, "ensemble_member_descriptors", lambda _plugin: {"tmp2m": descriptor},
    )

    class _PluginProxy:
        def __getattr__(self, name):
            return getattr(plugin, name)

        def scheduled_fhs_for_var(self, var_key, cycle_hour):
            return [0, 6, 12]

    return _PluginProxy()


def test_member_pass_resume_and_pending(tmp_path, transform, values, small_roster_plugin, monkeypatch) -> None:
    data_root = tmp_path
    run_id = "20260706_00z"
    staging_root = data_root / "staging" / "gefs" / run_id

    built: list[tuple[str, int]] = []

    def _fake_build(*, plugin, model_id, region, ctx, member, fh, run_date, staging_run_root, should_stop):
        built.append((member, fh))
        _write_member_frame(staging_run_root, transform, values, members.member_var_id(ctx.base_var, member), fh)
        return members.STATUS_WRITTEN

    monkeypatch.setattr(members, "build_member_frame", _fake_build)

    assert members.member_pass_pending(
        plugin=small_roster_plugin, model_id="gefs", run_id=run_id, data_root=data_root,
    )
    summary = members.run_member_pass(
        plugin=small_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=data_root, region="na", workers=1,
    )
    assert summary.counts == {members.STATUS_WRITTEN: 9}  # 3 members × 3 fhs
    assert summary.complete
    assert len(built) == 9
    assert not members.member_pass_pending(
        plugin=small_roster_plugin, model_id="gefs", run_id=run_id, data_root=data_root,
    )

    # Second pass resumes everything, builds nothing.
    built.clear()
    summary2 = members.run_member_pass(
        plugin=small_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=data_root, region="na", workers=1,
    )
    assert summary2.counts == {members.STATUS_RESUMED: 9}
    assert built == []


def test_member_pass_preemption_stops_promptly(tmp_path, transform, values, small_roster_plugin, monkeypatch) -> None:
    calls = {"count": 0}

    def _fake_build(*, plugin, model_id, region, ctx, member, fh, run_date, staging_run_root, should_stop):
        calls["count"] += 1
        _write_member_frame(staging_run_root, transform, values, members.member_var_id(ctx.base_var, member), fh)
        return members.STATUS_WRITTEN

    monkeypatch.setattr(members, "build_member_frame", _fake_build)

    def _stop_after_two() -> bool:
        return calls["count"] >= 2

    summary = members.run_member_pass(
        plugin=small_roster_plugin, model_id="gefs", run_id="20260706_00z",
        data_root=tmp_path, region="na", workers=1, should_stop=_stop_after_two,
    )
    assert summary.preempted
    assert summary.counts[members.STATUS_WRITTEN] == 2
    assert summary.counts[members.STATUS_PREEMPTED] == 7
    assert not summary.complete


def test_member_pass_gate_failure_writes_nothing(tmp_path, small_roster_plugin, monkeypatch) -> None:
    def _fake_build(*, plugin, model_id, region, ctx, member, fh, run_date, staging_run_root, should_stop):
        return members.STATUS_GATE_FAILED

    monkeypatch.setattr(members, "build_member_frame", _fake_build)
    summary = members.run_member_pass(
        plugin=small_roster_plugin, model_id="gefs", run_id="20260706_00z",
        data_root=tmp_path, region="na", workers=1,
    )
    assert summary.counts == {members.STATUS_GATE_FAILED: 9}
    assert not summary.complete
    staging_root = tmp_path / "staging" / "gefs" / "20260706_00z"
    assert not list(staging_root.rglob("*.bin")) if staging_root.exists() else True
    # Gate-failed frames stay pending (re-attempted, loudly, on later passes).
    assert members.member_pass_pending(
        plugin=small_roster_plugin, model_id="gefs", run_id="20260706_00z", data_root=tmp_path,
    )


def test_member_promote_pending_detects_staged_but_unpublished(
    tmp_path, transform, values, small_roster_plugin,
) -> None:
    """Crash window between pass completion and promote: staged member frames
    must register as promote-pending until they reach the published tree."""
    run_id = "20260706_00z"
    staging = tmp_path / "staging" / "gefs" / run_id
    published = tmp_path / "published" / "gefs" / run_id
    for member in ("m01", "m02", "control"):
        for fh in (0, 6, 12):
            _write_member_frame(staging, transform, values, members.member_var_id("tmp2m", member), fh)

    assert not members.member_pass_pending(
        plugin=small_roster_plugin, model_id="gefs", run_id=run_id, data_root=tmp_path,
    )
    assert members.member_promote_pending(
        plugin=small_roster_plugin, model_id="gefs", run_id=run_id, data_root=tmp_path,
    )

    # Simulate the promote (hardlink-merge copy) — pending clears.
    import shutil

    shutil.copytree(staging, published)
    assert not members.member_promote_pending(
        plugin=small_roster_plugin, model_id="gefs", run_id=run_id, data_root=tmp_path,
    )


def test_member_frame_is_complete_checks_size(tmp_path, transform, values) -> None:
    _write_member_frame(tmp_path, transform, values, "tmp2m__m01", 0)
    assert members.member_frame_is_complete(tmp_path, "gefs", "tmp2m__m01", 0)
    assert not members.member_frame_is_complete(tmp_path, "gefs", "tmp2m__m01", 6)
    frame = tmp_path / "tmp2m__m01" / "grid" / "fh000.l0.u16.bin"
    frame.write_bytes(frame.read_bytes()[:-8])
    assert not members.member_frame_is_complete(tmp_path, "gefs", "tmp2m__m01", 0)


def test_member_fetch_workers_env(monkeypatch) -> None:
    monkeypatch.delenv(members.ENV_MEMBER_FETCH_WORKERS, raising=False)
    assert members.member_fetch_workers() == 2
    monkeypatch.setenv(members.ENV_MEMBER_FETCH_WORKERS, "3")
    assert members.member_fetch_workers() == 3
    monkeypatch.setenv(members.ENV_MEMBER_FETCH_WORKERS, "9")
    assert members.member_fetch_workers() == 4
    monkeypatch.setenv(members.ENV_MEMBER_FETCH_WORKERS, "0")
    assert members.member_fetch_workers() == 1
    monkeypatch.setenv(members.ENV_MEMBER_FETCH_WORKERS, "junk")
    assert members.member_fetch_workers() == 2


# ── R8: allowlist ────────────────────────────────────────────────────
def test_member_publish_models_env(monkeypatch) -> None:
    monkeypatch.delenv("CARTOSKY_MEMBER_PUBLISH_MODELS", raising=False)
    assert member_publish_models() == frozenset()
    monkeypatch.setenv("CARTOSKY_MEMBER_PUBLISH_MODELS", "gefs")
    assert member_publish_models() == frozenset({"gefs"})
    monkeypatch.setenv("CARTOSKY_MEMBER_PUBLISH_MODELS", " GEFS , eps ")
    assert member_publish_models() == frozenset({"gefs", "eps"})


# ── D1: meteogram members probe ──────────────────────────────────────
def test_meteogram_probe_gated_until_phase5(monkeypatch) -> None:
    from backend.app.services import forecast_page

    # Payload support off (current state): probe is False even though the
    # GEFS descriptor exists — include_members stays honestly rejected.
    assert forecast_page._MEMBER_SERIES_PAYLOAD_SUPPORTED is False
    assert forecast_page._model_supports_members("gefs") is False

    # Phase 5 flips the constant: descriptor-bearing models become supported,
    # descriptor-less models stay unsupported. No probe rework needed.
    monkeypatch.setattr(forecast_page, "_MEMBER_SERIES_PAYLOAD_SUPPORTED", True)
    assert forecast_page._model_supports_members("gefs") is True
    assert forecast_page._model_supports_members("gfs") is False
    assert forecast_page._model_supports_members("hrrr") is False
