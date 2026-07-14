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
    tmp850_packing = _PACKING_BY_MODEL_VAR[("gefs", "tmp850__mean")]
    assert _packing_config("gefs", "tmp850__m17") is tmp850_packing
    assert _packing_config("eps", "tmp850__m50") is _PACKING_BY_MODEL_VAR[("eps", "tmp850__mean")]
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
def test_gefs_descriptor_enumeration() -> None:
    plugin = MODEL_REGISTRY["gefs"]
    descriptors = ensemble_member_descriptors(plugin)
    assert set(descriptors) == {"tmp2m", "tmp850", "precip_total", "snowfall_total"}
    for descriptor in descriptors.values():
        ids = ensemble_member_ids(descriptor)
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


# ── R2/R4/D6: member pass semantics (no network — bundle monkeypatched) ──
def _write_member_frame(staging_root: Path, transform, values, var_id: str, fh: int) -> None:
    write_slim_grid_frame_for_run_root(
        run_root=staging_root, model="gefs", var=var_id,
        fh=fh, values=values, transform=transform,
    )


# Native (EPSG:4326) synthetic source covering the GEFS na bbox, so warped
# outputs are fully finite and gate-plausible.
NATIVE_H, NATIVE_W = 40, 60
NATIVE_TRANSFORM = from_bounds(-178.0, 5.0, -25.0, 82.0, NATIVE_W, NATIVE_H)
NATIVE_CRS = "EPSG:4326"


def _native_tmp_c(fh: int) -> np.ndarray:
    base = np.linspace(0.0, 30.0, NATIVE_H * NATIVE_W, dtype=np.float32).reshape(NATIVE_H, NATIVE_W)
    return base + np.float32(fh % 7)


def _make_fake_bundle(apcp_value: float = 2.0, csnow_value: float = 1.0):
    """Bundle mock: constant APCP/CSNOW fields, gradient TMP."""
    calls: list[tuple[str, int, tuple[str, ...]]] = []

    def _fake(*, plan, member, fh, fields, should_stop):
        calls.append((member, fh, tuple(sorted(fields))))
        out = {}
        for key in fields:
            if key == "tmp2m":
                data = _native_tmp_c(fh)
            elif key == "apcp":
                data = np.full((NATIVE_H, NATIVE_W), apcp_value, dtype=np.float32)
            else:  # csnow
                data = np.full((NATIVE_H, NATIVE_W), csnow_value, dtype=np.float32)
            out[key] = (data, NATIVE_CRS, NATIVE_TRANSFORM)
        return out

    return _fake, calls


@pytest.fixture()
def small_roster_plugin(monkeypatch):
    """GEFS plugin proxy: 2-member roster, tmp2m-only, 3-fh schedule."""
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


@pytest.fixture()
def direct_temperature_roster_plugin(monkeypatch):
    """GEFS proxy with two direct temperature member variables."""
    plugin = MODEL_REGISTRY["gefs"]
    descriptor = {"count": 2, "control": True, "prefix": "m", "enabled": True}
    monkeypatch.setattr(
        members,
        "ensemble_member_descriptors",
        lambda _plugin: {"tmp2m": descriptor, "tmp850": descriptor},
    )

    class _PluginProxy:
        def __getattr__(self, name):
            return getattr(plugin, name)

        def scheduled_fhs_for_var(self, var_key, cycle_hour):
            return [0, 6]

    return _PluginProxy()


def test_member_pass_writes_all_direct_temperature_fields(
    tmp_path, direct_temperature_roster_plugin, monkeypatch,
) -> None:
    calls: list[tuple[str, int, tuple[str, ...]]] = []

    def _fake_bundle(*, plan, member, fh, fields, should_stop):
        calls.append((member, fh, tuple(sorted(fields))))
        return {
            field: (
                _native_tmp_c(fh)
                if field == "tmp2m"
                else _native_tmp_c(fh) - np.float32(20.0),
                NATIVE_CRS,
                NATIVE_TRANSFORM,
            )
            for field in fields
        }

    monkeypatch.setattr(members, "_fetch_member_bundle", _fake_bundle)
    run_id = "20260706_00z"
    summary = members.run_member_pass(
        plugin=direct_temperature_roster_plugin,
        model_id="gefs",
        run_id=run_id,
        data_root=tmp_path,
        region="na",
        workers=1,
    )

    assert summary.counts == {members.STATUS_WRITTEN: 12}
    assert summary.complete
    assert len(calls) == 6
    assert all(fields == ("tmp2m", "tmp850") for _member, _fh, fields in calls)
    decoded, _ = members._decode_member_frame(
        tmp_path / "staging" / "gefs" / run_id,
        "gefs",
        "tmp850__m01",
        6,
    )
    assert np.nanmin(decoded) < -10.0
    assert np.nanmax(decoded) > 0.0


@pytest.fixture()
def cumulative_roster_plugin(monkeypatch):
    """GEFS plugin proxy: 2-member roster, all three member vars enabled."""
    plugin = MODEL_REGISTRY["gefs"]
    descriptor = {"count": 2, "control": True, "prefix": "m", "enabled": True}
    monkeypatch.setattr(
        members,
        "ensemble_member_descriptors",
        lambda _plugin: {
            "tmp2m": descriptor,
            "precip_total": descriptor,
            "snowfall_total": descriptor,
        },
    )

    class _PluginProxy:
        def __getattr__(self, name):
            return getattr(plugin, name)

        def scheduled_fhs_for_var(self, var_key, cycle_hour):
            # min_fh constraints for the derived pair are enforced by the plan
            # builder's fh>0 filter; hand it fh 0 anyway to exercise that.
            return [0, 6, 12]

    return _PluginProxy()


def test_build_member_plan_rejects_off_cadence_derived_fhs(
    cumulative_roster_plugin,
) -> None:
    class _OffCadenceProxy:
        def __getattr__(self, name):
            return getattr(cumulative_roster_plugin, name)

        def scheduled_fhs_for_var(self, var_key, cycle_hour):
            if var_key in {"precip_total", "snowfall_total"}:
                return [0, 7, 12]
            return [0, 6, 12]

    with pytest.raises(
        ValueError,
        match=r"gefs.*precip_total.*7.*6-hour cumulative step grid",
    ):
        members.build_member_plan(
            _OffCadenceProxy(), "gefs", "20260706_00z", "na",
        )


def test_member_pass_resume_and_pending(tmp_path, small_roster_plugin, monkeypatch) -> None:
    data_root = tmp_path
    run_id = "20260706_00z"
    fake_bundle, calls = _make_fake_bundle()
    monkeypatch.setattr(members, "_fetch_member_bundle", fake_bundle)

    assert members.member_pass_pending(
        plugin=small_roster_plugin, model_id="gefs", run_id=run_id, data_root=data_root,
    )
    summary = members.run_member_pass(
        plugin=small_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=data_root, region="na", workers=1,
    )
    assert summary.counts == {members.STATUS_WRITTEN: 9}  # 3 members × 3 fhs
    assert summary.complete
    assert len(calls) == 9
    assert not members.member_pass_pending(
        plugin=small_roster_plugin, model_id="gefs", run_id=run_id, data_root=data_root,
    )

    # Second pass resumes everything, fetches nothing.
    calls.clear()
    summary2 = members.run_member_pass(
        plugin=small_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=data_root, region="na", workers=1,
    )
    assert summary2.counts == {members.STATUS_RESUMED: 9}
    assert calls == []


def test_member_pass_preemption_stops_promptly(tmp_path, small_roster_plugin, monkeypatch) -> None:
    fake_bundle, calls = _make_fake_bundle()
    monkeypatch.setattr(members, "_fetch_member_bundle", fake_bundle)

    def _stop_after_two() -> bool:
        return len(calls) >= 2

    summary = members.run_member_pass(
        plugin=small_roster_plugin, model_id="gefs", run_id="20260706_00z",
        data_root=tmp_path, region="na", workers=1, should_stop=_stop_after_two,
    )
    assert summary.preempted
    assert summary.counts[members.STATUS_WRITTEN] == 2
    assert summary.counts[members.STATUS_PREEMPTED] == 7
    assert not summary.complete


def test_member_pass_gate_failure_writes_nothing(tmp_path, small_roster_plugin, monkeypatch) -> None:
    fake_bundle, _calls = _make_fake_bundle()
    monkeypatch.setattr(members, "_fetch_member_bundle", fake_bundle)
    monkeypatch.setattr(members, "check_pre_encode_value_sanity", lambda *a, **k: False)

    summary = members.run_member_pass(
        plugin=small_roster_plugin, model_id="gefs", run_id="20260706_00z",
        data_root=tmp_path, region="na", workers=1,
    )
    assert summary.counts == {members.STATUS_GATE_FAILED: 9}
    assert not summary.complete
    staging_root = tmp_path / "staging" / "gefs" / "20260706_00z"
    assert not (staging_root.exists() and list(staging_root.rglob("*.bin")))
    # Gate-failed frames stay pending (re-attempted, loudly, on later passes).
    assert members.member_pass_pending(
        plugin=small_roster_plugin, model_id="gefs", run_id="20260706_00z", data_root=tmp_path,
    )


# ── D6: cumulative member vars end-to-end (mocked bundles) ───────────
def test_cumulative_member_vars_accumulate_and_write(tmp_path, cumulative_roster_plugin, monkeypatch) -> None:
    fake_bundle, calls = _make_fake_bundle(apcp_value=2.0, csnow_value=1.0)
    monkeypatch.setattr(members, "_fetch_member_bundle", fake_bundle)
    run_id = "20260706_00z"

    summary = members.run_member_pass(
        plugin=cumulative_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na", workers=1,
    )
    # Per member: tmp2m fh 0/6/12 + precip fh 6/12 + snow fh 6/12 = 7 frames.
    assert summary.counts == {members.STATUS_WRITTEN: 21}
    assert summary.complete
    # Bundles: one per (member, fh) — fh0 TMP-only, fh6/12 TMP+APCP+CSNOW.
    assert len(calls) == 9
    assert calls[0][2] == ("tmp2m",)
    assert calls[1][2] == ("apcp", "csnow", "tmp2m")

    staging_root = tmp_path / "staging" / "gefs" / run_id
    # 2.0 kg/m² per step: precip fh12 = 4.0 × 0.03937 = 0.157 in (0.01 packing);
    # snow fh12 = 4.0 × 0.3937 = 1.575 in (0.1 packing).
    precip, _ = members._decode_member_frame(staging_root, "gefs", "tmp2m__m01", 12)
    assert np.isfinite(precip).all()
    precip12, _ = members._decode_member_frame(staging_root, "gefs", "precip_total__m01", 12)
    assert np.nanmax(np.abs(precip12 - 0.157)) < 0.011
    snow12, _ = members._decode_member_frame(staging_root, "gefs", "snowfall_total__m01", 12)
    assert np.nanmax(np.abs(snow12 - 1.575)) < 0.06
    precip6, _ = members._decode_member_frame(staging_root, "gefs", "precip_total__control", 6)
    assert np.nanmax(np.abs(precip6 - 0.0787)) < 0.011


def test_cumulative_member_vars_zero_snow_when_csnow_zero(tmp_path, cumulative_roster_plugin, monkeypatch) -> None:
    fake_bundle, _calls = _make_fake_bundle(apcp_value=2.0, csnow_value=0.0)
    monkeypatch.setattr(members, "_fetch_member_bundle", fake_bundle)
    run_id = "20260706_00z"
    members.run_member_pass(
        plugin=cumulative_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na", workers=1,
    )
    staging_root = tmp_path / "staging" / "gefs" / run_id
    snow12, _ = members._decode_member_frame(staging_root, "gefs", "snowfall_total__m01", 12)
    assert np.nanmax(np.abs(snow12)) < 0.06  # all rain, no snow
    precip12, _ = members._decode_member_frame(staging_root, "gefs", "precip_total__m01", 12)
    assert np.nanmax(np.abs(precip12 - 0.157)) < 0.011


def test_cumulative_resume_rebases_from_written_frames(tmp_path, cumulative_roster_plugin, monkeypatch) -> None:
    fake_bundle, calls = _make_fake_bundle(apcp_value=2.0, csnow_value=1.0)
    monkeypatch.setattr(members, "_fetch_member_bundle", fake_bundle)
    run_id = "20260706_00z"
    members.run_member_pass(
        plugin=cumulative_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na", workers=1,
    )
    staging_root = tmp_path / "staging" / "gefs" / run_id

    # Simulate a lost fh12 for m01 across all three vars.
    for var in ("tmp2m__m01", "precip_total__m01", "snowfall_total__m01"):
        for artifact in (staging_root / var / "grid").glob("fh012.*"):
            artifact.unlink()
    calls.clear()

    summary = members.run_member_pass(
        plugin=cumulative_roster_plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na", workers=1,
    )
    assert summary.counts[members.STATUS_WRITTEN] == 3
    assert summary.counts[members.STATUS_RESUMED] == 18
    # Rebase fetched csnow at the base step (fh6) plus the fh12 bundle.
    assert ("m01", 6, ("csnow",)) in calls
    assert ("m01", 12, ("apcp", "csnow", "tmp2m")) in calls

    # Values survive the rebase within packing quantization.
    precip12, _ = members._decode_member_frame(staging_root, "gefs", "precip_total__m01", 12)
    assert np.nanmax(np.abs(precip12 - 0.157)) < 0.021  # one extra quantization step
    snow12, _ = members._decode_member_frame(staging_root, "gefs", "snowfall_total__m01", 12)
    assert np.nanmax(np.abs(snow12 - 1.575)) < 0.11


def test_cumulative_resume_replays_steps_after_last_complete_scheduled_frame(
    tmp_path, cumulative_roster_plugin, monkeypatch,
) -> None:
    class _SparseScheduleProxy:
        def __getattr__(self, name):
            return getattr(cumulative_roster_plugin, name)

        def scheduled_fhs_for_var(self, var_key, cycle_hour):
            return [0, 12, 24]

    plugin = _SparseScheduleProxy()
    fake_bundle, calls = _make_fake_bundle(apcp_value=2.0, csnow_value=1.0)
    monkeypatch.setattr(members, "_fetch_member_bundle", fake_bundle)
    run_id = "20260706_00z"
    members.run_member_pass(
        plugin=plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na", workers=1,
    )
    staging_root = tmp_path / "staging" / "gefs" / run_id

    # Simulate a lost fh24 for m01. The synthetic cumulative grid also has
    # fh18, but sparse schedules never wrote a cumulative checkpoint there.
    for var in ("tmp2m__m01", "precip_total__m01", "snowfall_total__m01"):
        for artifact in (staging_root / var / "grid").glob("fh024.*"):
            artifact.unlink()
    calls.clear()

    summary = members.run_member_pass(
        plugin=plugin, model_id="gefs", run_id=run_id,
        data_root=tmp_path, region="na", workers=1,
    )

    assert summary.counts == {
        members.STATUS_RESUMED: 18,
        members.STATUS_WRITTEN: 3,
    }
    assert summary.complete
    # Rebase at the last complete scheduled checkpoint, then replay every
    # cumulative step after it so fh18 is not silently omitted from fh24.
    assert ("m01", 12, ("csnow",)) in calls
    assert ("m01", 18, ("apcp", "csnow")) in calls
    assert ("m01", 24, ("apcp", "csnow", "tmp2m")) in calls

    precip24, _ = members._decode_member_frame(
        staging_root, "gefs", "precip_total__m01", 24,
    )
    assert np.nanmax(np.abs(precip24 - 0.315)) < 0.021
    snow24, _ = members._decode_member_frame(
        staging_root, "gefs", "snowfall_total__m01", 24,
    )
    assert np.nanmax(np.abs(snow24 - 3.15)) < 0.11


def test_cumulative_bundle_failure_aborts_member_chain(tmp_path, cumulative_roster_plugin, monkeypatch) -> None:
    def _failing_bundle(*, plan, member, fh, fields, should_stop):
        if member == "m01" and fh == 6:
            raise members.MemberFetchError("bundle fetch failed (synthetic)")
        fake, _ = _make_fake_bundle()
        return fake(plan=plan, member=member, fh=fh, fields=fields, should_stop=should_stop)

    monkeypatch.setattr(members, "_fetch_member_bundle", _failing_bundle)
    summary = members.run_member_pass(
        plugin=cumulative_roster_plugin, model_id="gefs", run_id="20260706_00z",
        data_root=tmp_path, region="na", workers=1,
    )
    # m01: fh0 tmp written, then the fh6 step failure aborts its chain
    # (tmp fh6/12 + precip fh6/12 + snow fh6/12 = 6 fetch_failed).
    assert summary.counts[members.STATUS_FETCH_FAILED] == 6
    assert summary.counts[members.STATUS_WRITTEN] == 1 + 7 + 7
    assert not summary.complete
    # The failed frames stay pending for the next pass.
    assert members.member_pass_pending(
        plugin=cumulative_roster_plugin, model_id="gefs", run_id="20260706_00z", data_root=tmp_path,
    )


# ── D6: parity with the production derive strategies ─────────────────
def _synthetic_steps(rng_seed: int = 7) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    rng = np.random.default_rng(rng_seed)
    apcp: dict[int, np.ndarray] = {}
    csnow: dict[int, np.ndarray] = {}
    for fh in (6, 12, 18):
        a = rng.uniform(-0.5, 5.0, size=(30, 40)).astype(np.float32)
        a[rng.uniform(size=a.shape) < 0.1] = np.nan
        apcp[fh] = a
        c = (rng.uniform(size=a.shape) < 0.5).astype(np.float32)
        c[rng.uniform(size=c.shape) < 0.05] = np.nan
        c[rng.uniform(size=c.shape) < 0.03] = 1.7  # out-of-range -> invalid
        csnow[fh] = c
    return apcp, csnow


def _patch_production_derive(monkeypatch, apcp: dict[int, np.ndarray], csnow: dict[int, np.ndarray]):
    import rasterio
    from backend.app.services.builder import derive

    crs = rasterio.crs.CRS.from_epsg(4326)
    transform = from_bounds(-178.0, 5.0, -25.0, 82.0, 40, 30)

    def _fake_step_component(**kwargs):
        var_key = str(kwargs["var_key"])
        step_fh = int(kwargs["step_fh"])
        if "csnow" in var_key:
            return csnow[step_fh].copy(), crs, transform
        return apcp[step_fh].copy(), crs, transform

    monkeypatch.setattr(derive, "_fetch_step_component", _fake_step_component)
    monkeypatch.setattr(derive, "_kuchera_load_prior_cumulative", lambda **k: None)
    monkeypatch.setattr(derive, "_kuchera_store_cumulative_cache", lambda **k: None)
    monkeypatch.setattr(derive, "_prefetch_components_parallel", lambda *a, **k: None)
    return derive


def test_precip_member_math_matches_production_derive(monkeypatch) -> None:
    from datetime import datetime

    from backend.app.models.gefs import GEFS_MODEL, GEFS_VARIABLE_CATALOG, GEFS_VARS

    apcp, csnow = _synthetic_steps()
    derive = _patch_production_derive(monkeypatch, apcp, csnow)

    production, _crs, _transform = derive._derive_precip_total_cumulative(
        model_id="gefs", var_key="precip_total", product="atmos.5",
        run_date=datetime(2026, 7, 6), fh=18,
        var_spec_model=GEFS_VARS["precip_total"],
        var_capability=GEFS_VARIABLE_CATALOG["precip_total"],
        model_plugin=GEFS_MODEL, ctx=None,
    )

    cum = valid = None
    for fh in (6, 12, 18):
        contribution, step_valid = members.precip_step_contribution(apcp[fh])
        cum, valid = members.merge_cumulative_step(cum, valid, contribution, step_valid)
    member_field = members.cumulative_field(cum, valid) * np.float32(members._KGM2_TO_INCHES)

    assert np.allclose(production, member_field, rtol=1e-5, atol=1e-6, equal_nan=True)


def test_snowfall_member_math_matches_production_derive(monkeypatch) -> None:
    from datetime import datetime

    from backend.app.models.gefs import GEFS_MODEL, GEFS_VARIABLE_CATALOG, GEFS_VARS

    apcp, csnow = _synthetic_steps()
    derive = _patch_production_derive(monkeypatch, apcp, csnow)

    production, _crs, _transform = derive._derive_snowfall_total_10to1_cumulative(
        model_id="gefs", var_key="snowfall_total", product="atmos.5",
        run_date=datetime(2026, 7, 6), fh=18,
        var_spec_model=GEFS_VARS["snowfall_total"],
        var_capability=GEFS_VARIABLE_CATALOG["snowfall_total"],
        model_plugin=GEFS_MODEL, ctx=None,
    )

    snow_ctx = members._resolve_member_var_context(GEFS_MODEL, "gefs", "snowfall_total")
    params = members._parse_cumulative_params(snow_ctx, None)
    assert params.slr == 10.0
    assert params.min_step_lwe == 0.01
    assert params.snow_mask_threshold is None
    assert params.skip_zero_hour_sample is True
    assert params.step_hours == 6

    cum = valid = None
    prev_csnow: np.ndarray | None = None
    for fh in (6, 12, 18):
        samples = []
        start_fh = fh - params.step_hours
        if prev_csnow is not None and (start_fh > 0 or not params.skip_zero_hour_sample):
            samples.append(prev_csnow)
        samples.append(csnow[fh])
        contribution, step_valid = members.snowfall_step_contribution(
            apcp[fh], samples,
            min_step_lwe=params.min_step_lwe,
            snow_mask_threshold=params.snow_mask_threshold,
        )
        cum, valid = members.merge_cumulative_step(cum, valid, contribution, step_valid)
        prev_csnow = csnow[fh]
    member_field = members.cumulative_field(cum, valid) * np.float32(
        members._KGM2_TO_INCHES * params.slr,
    )

    assert np.allclose(production, member_field, rtol=1e-5, atol=1e-6, equal_nan=True)


def test_all_zero_snow_member_frame_passes_gate() -> None:
    """A July member with csnow=0 everywhere yields an exactly-flat all-zero
    snowfall cumulative; allow_dry_frame on the snowfall/precip colormaps must
    let it through the gate (else snow members would retry-loop all summer)."""
    from backend.app.services.builder.pipeline import check_pre_encode_value_sanity
    from backend.app.services.colormaps import get_color_map_spec

    plugin = MODEL_REGISTRY["gefs"]
    for base_var in ("snowfall_total", "precip_total"):
        ctx = members._resolve_member_var_context(plugin, "gefs", base_var)
        flat = np.zeros((30, 40), dtype=np.float32)
        assert check_pre_encode_value_sanity(
            flat, get_color_map_spec(base_var),
            var_spec_model=ctx.var_spec, var_capability=ctx.capability,
            label=f"gefs/{base_var}__m01/fh384 (dry-frame test)",
        ), base_var


# ── D6: bundle plumbing ──────────────────────────────────────────────
def test_map_bundle_bands() -> None:
    fields = {"tmp2m": "p1", "apcp": "p2", "csnow": "p3"}
    mapping = members._map_bundle_bands(["TMP", "APCP", "CSNOW"], fields)
    assert mapping == {"tmp2m": 1, "apcp": 2, "csnow": 3}
    # GDAL suffixes accumulation elements with their window (prod-observed
    # 2026-07-06: APCP over 6 h reports as "APCP06").
    mapping = members._map_bundle_bands(["TMP", "APCP06", "CSNOW"], fields)
    assert mapping == {"tmp2m": 1, "apcp": 2, "csnow": 3}
    # Order-independent, unknown elements ignored.
    mapping = members._map_bundle_bands(["UGRD", "CSNOW", "TMP", "APCP"], fields)
    assert mapping == {"csnow": 2, "tmp2m": 3, "apcp": 4}
    with pytest.raises(members.MemberFetchError, match="missing fields"):
        members._map_bundle_bands(["TMP", "APCP"], fields)
    with pytest.raises(members.MemberFetchError, match="Duplicate"):
        members._map_bundle_bands(["TMP", "TMP", "APCP", "CSNOW"], fields)


def test_map_bundle_bands_distinguishes_temperature_levels_from_inventory() -> None:
    fields = {
        "tmp2m": r":TMP:2 m above ground:",
        "tmp850": r":TMP:850 mb:",
        "apcp": members.MEMBER_APCP_PATTERN,
    }
    mapping = members._map_bundle_bands(
        ["TMP", "TMP", "APCP06"],
        fields,
        band_inventory_lines=[
            ":TMP:2 m above ground:anl:ENS=+1",
            ":TMP:850 mb:anl:ENS=+1",
            ":APCP:surface:0-6 hour acc fcst:ENS=+1",
        ],
    )
    assert mapping == {"tmp2m": 1, "tmp850": 2, "apcp": 3}


def test_build_member_plan_real_gefs_schedules() -> None:
    plugin = MODEL_REGISTRY["gefs"]
    plan = members.build_member_plan(plugin, "gefs", "20260706_00z", "na")
    assert plan is not None
    assert set(plan.contexts) == {"tmp2m", "tmp850", "precip_total", "snowfall_total"}
    assert len(plan.member_ids) == 31
    assert len(plan.member_var_ids) == 124
    assert plan.fhs_by_var["tmp2m"] == list(range(0, 385, 6))
    assert plan.fhs_by_var["tmp850"] == list(range(0, 385, 6))
    assert plan.fhs_by_var["precip_total"] == list(range(6, 385, 6))  # min_fh 6
    assert plan.step_fhs == list(range(6, 385, 6))
    assert plan.cumulative.slr == 10.0
    assert members._bundle_fields_for_fh(plan, 0) == {
        "tmp2m": plan.contexts["tmp2m"].search_patterns[0],
        "tmp850": plan.contexts["tmp850"].search_patterns[0],
    }
    assert set(members._bundle_fields_for_fh(plan, 6)) == {
        "tmp2m", "tmp850", "apcp", "csnow",
    }
    # The member APCP pattern must NOT be end-anchored (member idx lines carry
    # an ENS suffix, unlike the deterministic GFS lines).
    assert not members.MEMBER_APCP_PATTERN.endswith("$")


def test_build_member_plan_roster_mismatch_raises(monkeypatch) -> None:
    plugin = MODEL_REGISTRY["gefs"]
    monkeypatch.setattr(
        members,
        "ensemble_member_descriptors",
        lambda _plugin: {
            "tmp2m": {"count": 30, "control": True, "prefix": "m", "enabled": True},
            "precip_total": {"count": 20, "control": False, "prefix": "m", "enabled": True},
        },
    )
    with pytest.raises(ValueError, match="disagree on the roster"):
        members.build_member_plan(plugin, "gefs", "20260706_00z", "na")


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


# ── D1/Phase 5: meteogram members probe ──────────────────────────────
def test_meteogram_probe_requires_allowlist_and_descriptor(monkeypatch) -> None:
    from backend.app.services import forecast_page

    # Phase 5 backend is live: the payload can serve member series.
    assert forecast_page._MEMBER_SERIES_PAYLOAD_SUPPORTED is True

    # Binary sampling is the zero-config default, so a model with a member
    # descriptor is supported out of the box.
    monkeypatch.delenv("CARTOSKY_BINARY_SAMPLING_MODELS", raising=False)
    monkeypatch.delenv("CARTOSKY_COG_SAMPLING_MODELS", raising=False)
    assert forecast_page._model_supports_members("gefs") is True
    assert forecast_page._model_supports_members("gfs") is False   # no descriptor
    assert forecast_page._model_supports_members("hrrr") is False  # no descriptor

    # A COG opt-out removes the binary substrate member frames live on ->
    # unsupported even with a descriptor.
    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", "gefs")
    assert forecast_page._model_supports_members("gefs") is False


# ── Phase 5: seek sampler equality with the full-read sampler ─────────
def test_seek_sampler_matches_full_read(tmp_path, transform, values) -> None:
    from backend.app.services.sampling import (
        read_binary_sample_value,
        read_binary_sample_value_seek,
    )

    spike_var = "tmp2m__m01"
    write_slim_grid_frame_for_run_root(
        run_root=tmp_path, model="gefs", var=spike_var,
        fh=0, values=values, transform=transform,
    )
    frame = tmp_path / spike_var / "grid" / "fh000.l0.u16.bin"
    meta = tmp_path / spike_var / "grid" / "fh000.l0.meta.json"

    # Interior, near-edge, out-of-coverage, and a NaN-fringe (nodata) pixel —
    # the fringe rows sit at the top of the array (north, lat ~82).
    for lat, lon in ((43.5, -101.5), (6.0, -177.0), (43.5, 5.0), (81.5, -101.5)):
        full = read_binary_sample_value(frame, meta, model="gefs", var=spike_var, lat=lat, lon=lon)
        seek = read_binary_sample_value_seek(frame, meta, model="gefs", var=spike_var, lat=lat, lon=lon)
        assert seek == full, (lat, lon)

    # Truncated frame: both paths refuse to mis-address.
    frame.write_bytes(frame.read_bytes()[:-10])
    with pytest.raises(ValueError, match="size mismatch"):
        read_binary_sample_value(frame, meta, model="gefs", var=spike_var, lat=43.5, lon=-101.5)
    with pytest.raises(ValueError, match="size mismatch"):
        read_binary_sample_value_seek(frame, meta, model="gefs", var=spike_var, lat=43.5, lon=-101.5)


# ── Phase 4: pf-subset mode (EPS, design §13/D7) ─────────────────────
def _pf_inventory(rows):
    """Minimal pf inventory frame: (start_byte, end_byte, number) rows."""
    import pandas as pd

    return pd.DataFrame(
        [{"start_byte": s, "end_byte": e, "number": n} for s, e, n in rows]
    )


def test_pf_band_member_numbers_follow_byte_order() -> None:
    # Rows arrive number-ordered (the mean sorts them that way), but the
    # subset writer lays bands out by ascending byte start — the mapping
    # must follow bytes, not row order.
    inventory = _pf_inventory([(300, 399, 1), (100, 199, 2), (200, 299, 3)])
    assert members._pf_band_member_numbers(inventory) == [2, 3, 1]


def test_pf_band_member_numbers_reject_ambiguity() -> None:
    with pytest.raises(members.MemberFetchError, match="duplicate member numbers"):
        members._pf_band_member_numbers(
            _pf_inventory([(100, 199, 1), (200, 299, 1)])
        )
    with pytest.raises(members.MemberFetchError, match="duplicate byte ranges"):
        members._pf_band_member_numbers(
            _pf_inventory([(100, 199, 1), (100, 199, 2)])
        )
    with pytest.raises(members.MemberFetchError, match="missing its byte range"):
        import pandas as pd

        members._pf_band_member_numbers(
            pd.DataFrame([{"start_byte": float("nan"), "end_byte": 199, "number": 1}])
        )
    with pytest.raises(members.MemberFetchError, match="no member number"):
        import pandas as pd

        members._pf_band_member_numbers(
            pd.DataFrame([{"start_byte": 100, "end_byte": 199, "number": None}])
        )
    with pytest.raises(members.MemberFetchError, match="no byte ranges"):
        members._pf_band_member_numbers(_pf_inventory([]))


def test_build_member_plan_real_eps_is_pf_subset_mode() -> None:
    plugin = MODEL_REGISTRY["eps"]
    plan = members.build_member_plan(plugin, "eps", "20260706_00z", "na")
    assert plan is not None
    assert plan.mode == members.MODE_PF_SUBSET
    assert set(plan.contexts) == {"tmp2m", "tmp850", "precip_total"}
    # 50 pf members, NO control (plan §2.2 correction).
    assert len(plan.member_ids) == 50
    assert plan.member_ids[0] == "m01" and plan.member_ids[-1] == "m50"
    assert "control" not in plan.member_ids
    assert len(plan.member_var_ids) == 150
    # ECMWF tp is natively run-cumulative: all member vars are direct, no step chain.
    assert not plan.contexts["tmp2m"].derived
    assert not plan.contexts["tmp850"].derived
    assert not plan.contexts["precip_total"].derived
    assert plan.step_fhs == [] and not plan.has_cumulative
    # min_fh 6 honored by the schedule (no phantom fh-0 precip unit).
    assert plan.fhs_by_var["precip_total"][0] == 6
    assert plan.fhs_by_var["tmp2m"][0] == 0
    assert len(plan.fhs_by_var["tmp850"]) == 61
    # GEFS is untouched by mode detection.
    gefs_plan = members.build_member_plan(MODEL_REGISTRY["gefs"], "gefs", "20260706_00z", "na")
    assert gefs_plan.mode == members.MODE_MEMBER_FILES


def test_detect_plan_mode_mixed_raises(monkeypatch) -> None:
    """A plan mixing pf-subset and non-pf member vars has no build path."""
    plugin = MODEL_REGISTRY["gefs"]
    monkeypatch.setattr(
        members,
        "ensemble_member_descriptors",
        lambda _plugin: {
            "tmp2m": {"count": 2, "control": False, "prefix": "m", "enabled": True},
            "precip_total": {"count": 2, "control": False, "prefix": "m", "enabled": True},
        },
    )

    class _MixedProxy:
        def __getattr__(self, name):
            return getattr(plugin, name)

        def scheduled_fhs_for_var(self, var_key, cycle_hour):
            return [0, 6]

        def herbie_request(self, **kwargs):
            request = plugin.herbie_request(**kwargs)
            if kwargs.get("var_key") == "tmp2m":
                herbie_kwargs = dict(request.herbie_kwargs)
                herbie_kwargs["_cartosky_fetch_aggregation"] = "ecmwf_pf_mean"
                return type(request)(
                    model=request.model, product=request.product,
                    herbie_kwargs=herbie_kwargs,
                )
            return request

    with pytest.raises(ValueError, match="mixes pf-subset vars"):
        members.build_member_plan(_MixedProxy(), "gefs", "20260706_00z", "na")


@pytest.fixture()
def eps_roster_plugin(monkeypatch):
    """EPS plugin proxy: 3-member pf roster, tmp2m-only, 2-fh schedule."""
    plugin = MODEL_REGISTRY["eps"]
    descriptor = {"count": 3, "control": False, "prefix": "m", "enabled": True}
    monkeypatch.setattr(
        members, "ensemble_member_descriptors", lambda _plugin: {"tmp2m": descriptor},
    )

    class _PluginProxy:
        def __getattr__(self, name):
            return getattr(plugin, name)

        def scheduled_fhs_for_var(self, var_key, cycle_hour):
            return [0, 6]

    return _PluginProxy()


def _write_pf_subset_tif(path: Path, fh: int, numbers: list[int]) -> None:
    """Multi-band float32 GeoTIFF standing in for a *.cartosky_pf.grib2:
    band k carries member numbers[k-1]'s field (byte-order layout)."""
    import rasterio

    with rasterio.open(
        path, "w", driver="GTiff", height=NATIVE_H, width=NATIVE_W,
        count=len(numbers), dtype="float32", crs=NATIVE_CRS,
        transform=NATIVE_TRANSFORM,
    ) as dst:
        for band_index, number in enumerate(numbers, start=1):
            dst.write(_native_tmp_c(fh) + np.float32(number), band_index)


def _make_fake_pf_resolver(subset_dir: Path, numbers: list[int]):
    calls: list[tuple[str, int]] = []

    def _fake(*, plan, ctx, fh, should_stop):
        calls.append((ctx.base_var, fh))
        path = subset_dir / f"pf_{ctx.base_var}_fh{fh:03d}.tif"
        if not path.exists():
            _write_pf_subset_tif(path, fh, numbers)
        return path, list(numbers)

    return _fake, calls


def _expected_eps_member_frame(plugin, fh: int, number: int) -> np.ndarray:
    """The member path's expected output: convert -> warp of that band."""
    ctx = members._resolve_member_var_context(plugin, "eps", "tmp2m")
    native = _native_tmp_c(fh) + np.float32(number)
    converted = members.convert_units(
        native, var_key="tmp2m", model_id="eps", var_capability=ctx.capability,
    )
    warped, _ = members.warp_to_target_grid(
        converted, NATIVE_CRS, NATIVE_TRANSFORM, model="eps", region="na",
        resampling=ctx.resampling, src_nodata=None, dst_nodata=float("nan"),
    )
    return warped


def test_pf_pass_writes_correctly_labeled_frames_and_resumes(
    tmp_path, eps_roster_plugin, monkeypatch,
) -> None:
    # Byte order deliberately ≠ member order: band 1 is member 2's field.
    numbers = [2, 1, 3]
    fake_resolver, calls = _make_fake_pf_resolver(tmp_path / "subsets", numbers)
    (tmp_path / "subsets").mkdir()
    monkeypatch.setattr(members, "_resolve_pf_subset", fake_resolver)
    run_id = "20260706_00z"

    summary = members.run_member_pass(
        plugin=eps_roster_plugin, model_id="eps", run_id=run_id,
        data_root=tmp_path, region="na", workers=1,
    )
    assert summary.counts == {members.STATUS_WRITTEN: 6}  # 3 members × 2 fhs
    assert summary.complete
    assert calls == [("tmp2m", 0), ("tmp2m", 6)]  # ONE resolve per (var, fh)

    # Labeling: each member's decoded frame matches ITS band, within packing
    # quantization (EPS tmp2m scale 0.1 F), despite the shuffled byte order.
    staging = tmp_path / "staging" / "eps" / run_id
    for fh in (0, 6):
        for number in (1, 2, 3):
            decoded, _meta = members._decode_member_frame(
                staging, "eps", members.member_var_id("tmp2m", f"m{number:02d}"), fh,
            )
            expected = _expected_eps_member_frame(eps_roster_plugin, fh, number)
            both = np.isfinite(decoded) & np.isfinite(expected)
            assert both.any()
            assert np.allclose(decoded[both], expected[both], atol=0.11)
            # Wrong-label guard: must NOT match a different member's field
            # (members differ by 1 C = 1.8 F, far above quantization).
            other = _expected_eps_member_frame(eps_roster_plugin, fh, number % 3 + 1)
            assert not np.allclose(decoded[both], other[both], atol=0.11)

    assert not members.member_pass_pending(
        plugin=eps_roster_plugin, model_id="eps", run_id=run_id, data_root=tmp_path,
    )
    # Second pass resumes everything and never re-resolves a subset.
    calls.clear()
    summary2 = members.run_member_pass(
        plugin=eps_roster_plugin, model_id="eps", run_id=run_id,
        data_root=tmp_path, region="na", workers=1,
    )
    assert summary2.counts == {members.STATUS_RESUMED: 6}
    assert calls == []


def test_pf_pass_mean_of_members_matches_subset_mean(
    tmp_path, eps_roster_plugin, monkeypatch,
) -> None:
    """Parity with the production mean: averaging the published member frames
    reproduces convert->warp of ``_aggregate_grib_subset_mean`` on the same
    subset (both linear, so they commute) within packing tolerance."""
    from backend.app.services.builder.fetch import _aggregate_grib_subset_mean

    numbers = [3, 1, 2]
    fake_resolver, _calls = _make_fake_pf_resolver(tmp_path / "subsets", numbers)
    (tmp_path / "subsets").mkdir()
    monkeypatch.setattr(members, "_resolve_pf_subset", fake_resolver)
    run_id = "20260706_00z"
    members.run_member_pass(
        plugin=eps_roster_plugin, model_id="eps", run_id=run_id,
        data_root=tmp_path, region="na", workers=1,
    )

    staging = tmp_path / "staging" / "eps" / run_id
    fh = 6
    member_mean = np.mean(
        [
            members._decode_member_frame(
                staging, "eps", members.member_var_id("tmp2m", f"m{n:02d}"), fh,
            )[0]
            for n in (1, 2, 3)
        ],
        axis=0,
    )
    subset_path = tmp_path / "subsets" / f"pf_tmp2m_fh{fh:03d}.tif"
    agg, crs, transform, count = _aggregate_grib_subset_mean(subset_path)
    assert count == 3
    ctx = members._resolve_member_var_context(eps_roster_plugin, "eps", "tmp2m")
    converted = members.convert_units(
        agg, var_key="tmp2m", model_id="eps", var_capability=ctx.capability,
    )
    expected, _ = members.warp_to_target_grid(
        converted, crs, transform, model="eps", region="na",
        resampling=ctx.resampling, src_nodata=None, dst_nodata=float("nan"),
    )
    both = np.isfinite(member_mean) & np.isfinite(expected)
    assert both.any()
    assert np.allclose(member_mean[both], expected[both], atol=0.11)


def test_pf_pass_roster_mismatch_errors_without_writing(
    tmp_path, eps_roster_plugin, monkeypatch,
) -> None:
    """Numbers that don't cover 1..N exactly must never label bands."""
    fake_resolver, _calls = _make_fake_pf_resolver(tmp_path / "subsets", [1, 2])
    (tmp_path / "subsets").mkdir()
    monkeypatch.setattr(members, "_resolve_pf_subset", fake_resolver)

    summary = members.run_member_pass(
        plugin=eps_roster_plugin, model_id="eps", run_id="20260706_00z",
        data_root=tmp_path, region="na", workers=1,
    )
    assert summary.counts == {members.STATUS_ERROR: 6}
    assert not summary.complete
    staging = tmp_path / "staging" / "eps" / "20260706_00z"
    assert not (staging.exists() and list(staging.rglob("*.bin")))


def test_pf_pass_band_count_mismatch_errors(tmp_path, eps_roster_plugin, monkeypatch) -> None:
    subset_dir = tmp_path / "subsets"
    subset_dir.mkdir()

    def _short_resolver(*, plan, ctx, fh, should_stop):
        path = subset_dir / f"pf_{ctx.base_var}_fh{fh:03d}.tif"
        if not path.exists():
            _write_pf_subset_tif(path, fh, [1, 2])  # 2 bands…
        return path, [1, 2, 3]  # …but the index promises 3 members

    monkeypatch.setattr(members, "_resolve_pf_subset", _short_resolver)
    summary = members.run_member_pass(
        plugin=eps_roster_plugin, model_id="eps", run_id="20260706_00z",
        data_root=tmp_path, region="na", workers=1,
    )
    assert summary.counts == {members.STATUS_ERROR: 6}
    staging = tmp_path / "staging" / "eps" / "20260706_00z"
    assert not (staging.exists() and list(staging.rglob("*.bin")))


def test_pf_pass_fetch_failure_and_preemption(tmp_path, eps_roster_plugin, monkeypatch) -> None:
    def _failing_resolver(*, plan, ctx, fh, should_stop):
        raise members.MemberFetchError(f"pf subset resolve failed ({ctx.base_var} fh{fh:03d})")

    monkeypatch.setattr(members, "_resolve_pf_subset", _failing_resolver)
    summary = members.run_member_pass(
        plugin=eps_roster_plugin, model_id="eps", run_id="20260706_00z",
        data_root=tmp_path, region="na", workers=1,
    )
    assert summary.counts == {members.STATUS_FETCH_FAILED: 6}
    assert members.member_pass_pending(
        plugin=eps_roster_plugin, model_id="eps", run_id="20260706_00z", data_root=tmp_path,
    )

    # Preemption between bands: stop after the first unit resolves.
    numbers = [1, 2, 3]
    fake_resolver, calls = _make_fake_pf_resolver(tmp_path / "subsets", numbers)
    (tmp_path / "subsets").mkdir()
    monkeypatch.setattr(members, "_resolve_pf_subset", fake_resolver)
    summary2 = members.run_member_pass(
        plugin=eps_roster_plugin, model_id="eps", run_id="20260706_00z",
        data_root=tmp_path, region="na", workers=1,
        should_stop=lambda: len(calls) >= 1,
    )
    assert summary2.preempted
    assert summary2.counts[members.STATUS_PREEMPTED] > 0
    assert not summary2.complete


def test_pf_pass_gate_failure_writes_nothing(tmp_path, eps_roster_plugin, monkeypatch) -> None:
    fake_resolver, _calls = _make_fake_pf_resolver(tmp_path / "subsets", [1, 2, 3])
    (tmp_path / "subsets").mkdir()
    monkeypatch.setattr(members, "_resolve_pf_subset", fake_resolver)
    monkeypatch.setattr(members, "check_pre_encode_value_sanity", lambda *a, **k: False)

    summary = members.run_member_pass(
        plugin=eps_roster_plugin, model_id="eps", run_id="20260706_00z",
        data_root=tmp_path, region="na", workers=1,
    )
    assert summary.counts == {members.STATUS_GATE_FAILED: 6}
    staging = tmp_path / "staging" / "eps" / "20260706_00z"
    assert not (staging.exists() and list(staging.rglob("*.bin")))


def test_pf_pass_partial_resume_rebuilds_only_missing_members(
    tmp_path, eps_roster_plugin, monkeypatch,
) -> None:
    numbers = [1, 2, 3]
    fake_resolver, calls = _make_fake_pf_resolver(tmp_path / "subsets", numbers)
    (tmp_path / "subsets").mkdir()
    monkeypatch.setattr(members, "_resolve_pf_subset", fake_resolver)
    run_id = "20260706_00z"
    members.run_member_pass(
        plugin=eps_roster_plugin, model_id="eps", run_id=run_id,
        data_root=tmp_path, region="na", workers=1,
    )

    # Knock out one member frame; the next pass resolves ONLY that unit's
    # subset and rewrites only the missing frame.
    staging = tmp_path / "staging" / "eps" / run_id
    victim = staging / "tmp2m__m02" / "grid" / "fh006.l0.u16.bin"
    assert victim.is_file()
    victim.unlink()
    calls.clear()
    summary = members.run_member_pass(
        plugin=eps_roster_plugin, model_id="eps", run_id=run_id,
        data_root=tmp_path, region="na", workers=1,
    )
    assert summary.counts == {members.STATUS_RESUMED: 5, members.STATUS_WRITTEN: 1}
    assert calls == [("tmp2m", 6)]
    decoded, _ = members._decode_member_frame(
        staging, "eps", "tmp2m__m02", 6,
    )
    expected = _expected_eps_member_frame(eps_roster_plugin, 6, 2)
    both = np.isfinite(decoded) & np.isfinite(expected)
    assert np.allclose(decoded[both], expected[both], atol=0.11)


# ── Backfill: mean-coverage cap + idle scan (post-Phase-4 hardening) ──
# Triggered by prod 2026-07-08: EPS 00z stuck at 654/705 (upstream 1-byte
# index defect on rh700) → superseded before complete → member hook never
# fired → mean-only plumes. Backfill builds members for the mean frames a
# stuck run DID publish, during idle, preempting only on new mean work.
def _write_mean_frame(data_root: Path, run_id: str, fh: int) -> None:
    write_slim_grid_frame_for_run_root(
        run_root=data_root / "staging" / "eps" / run_id, model="eps",
        var="tmp2m__mean", fh=fh,
        values=np.full((NATIVE_H, NATIVE_W), 20.0, dtype=np.float32),
        transform=NATIVE_TRANSFORM,
    )


def test_build_member_plan_mean_coverage_cap(tmp_path, eps_roster_plugin) -> None:
    run_id = "20260708_00z"
    _write_mean_frame(tmp_path, run_id, 0)  # fh6 mean deliberately absent

    full = members.build_member_plan(eps_roster_plugin, "eps", run_id, "na")
    assert full.fhs_by_var["tmp2m"] == [0, 6]
    capped = members.build_member_plan(
        eps_roster_plugin, "eps", run_id, "na",
        data_root=tmp_path, mean_coverage_only=True,
    )
    assert capped.fhs_by_var["tmp2m"] == [0]
    with pytest.raises(ValueError, match="requires data_root"):
        members.build_member_plan(
            eps_roster_plugin, "eps", run_id, "na", mean_coverage_only=True,
        )


def test_pf_backfill_pass_builds_only_mean_covered_frames(
    tmp_path, eps_roster_plugin, monkeypatch,
) -> None:
    run_id = "20260708_00z"
    _write_mean_frame(tmp_path, run_id, 0)
    fake_resolver, calls = _make_fake_pf_resolver(tmp_path / "subsets", [1, 2, 3])
    (tmp_path / "subsets").mkdir()
    monkeypatch.setattr(members, "_resolve_pf_subset", fake_resolver)

    summary = members.run_member_pass(
        plugin=eps_roster_plugin, model_id="eps", run_id=run_id,
        data_root=tmp_path, region="na", workers=1, mean_coverage_only=True,
    )
    # 3 members × the ONE mean-covered fh; complete, and the uncovered fh6
    # was never even resolved.
    assert summary.counts == {members.STATUS_WRITTEN: 3}
    assert summary.complete
    assert calls == [("tmp2m", 0)]
    # Coverage-capped pending clears; schedule-based pending still reports
    # the uncovered fh6 (the normal post-complete hook semantics).
    assert not members.member_pass_pending(
        plugin=eps_roster_plugin, model_id="eps", run_id=run_id,
        data_root=tmp_path, mean_coverage_only=True,
    )
    assert members.member_pass_pending(
        plugin=eps_roster_plugin, model_id="eps", run_id=run_id, data_root=tmp_path,
    )


def test_member_backfill_scan_selects_newest_pending_run(tmp_path, monkeypatch) -> None:
    """Scheduler-level scan: newest-first, skips the latest run, one run per
    idle iteration, probe referenced to the LATEST run."""
    from backend.app.services import scheduler as sched
    # The scheduler's lazy member imports resolve in the app.* namespace —
    # patch that module instance (backend.app.* and app.* are distinct).
    import app.services.builder.members as members_app

    for run in ("20260707_12z", "20260708_00z", "20260708_06z"):
        (tmp_path / "published" / "eps" / run).mkdir(parents=True)

    pending_runs = {"20260707_12z"}  # 00z clean, 12z stuck
    monkeypatch.setattr(sched, "member_publish_models", lambda: frozenset({"eps"}))
    monkeypatch.setattr(
        members_app, "member_pass_pending",
        lambda *, plugin, model_id, run_id, data_root, mean_coverage_only=False:
            run_id in pending_runs,
    )
    monkeypatch.setattr(
        members_app, "member_promote_pending",
        lambda *, plugin, model_id, run_id, data_root, mean_coverage_only=False: False,
    )
    passes: list[dict] = []
    monkeypatch.setattr(
        sched, "_maybe_run_member_pass", lambda **kwargs: passes.append(kwargs),
    )

    sched._maybe_run_member_backfill(
        plugin=object(), model_id="eps", latest_run_id="20260708_06z",
        data_root=tmp_path, probe_var="tmp2m",
    )
    assert len(passes) == 1
    call = passes[0]
    assert call["run_id"] == "20260707_12z"
    assert call["probe_reference_run_id"] == "20260708_06z"
    assert call["mean_coverage_only"] is True

    # Latest run is never a backfill target even when "pending".
    passes.clear()
    pending_runs.add("20260708_06z")
    pending_runs.discard("20260707_12z")
    sched._maybe_run_member_backfill(
        plugin=object(), model_id="eps", latest_run_id="20260708_06z",
        data_root=tmp_path, probe_var="tmp2m",
    )
    assert passes == []

    # Model not on the allowlist: no scan at all.
    monkeypatch.setattr(sched, "member_publish_models", lambda: frozenset())
    pending_runs.add("20260707_12z")
    sched._maybe_run_member_backfill(
        plugin=object(), model_id="eps", latest_run_id="20260708_06z",
        data_root=tmp_path, probe_var="tmp2m",
    )
    assert passes == []


def test_batch_member_sampler_matches_per_sample(tmp_path, monkeypatch, transform) -> None:
    """sample_member_values_seek is result-identical to per-sample
    sample_binary_value_seek across present / nodata / missing frames."""
    import backend.app.main as main_mod
    from backend.app.services import sampling

    monkeypatch.setattr(main_mod, "PUBLISHED_ROOT", tmp_path / "published")
    monkeypatch.setattr(main_mod, "MANIFESTS_ROOT", tmp_path / "manifests")

    run_id = "20260706_00z"
    run_root = tmp_path / "published" / "gefs" / run_id
    manifest = tmp_path / "manifests" / "gefs"
    manifest.mkdir(parents=True)
    (manifest / f"{run_id}.json").write_text("{}")

    rng = np.random.default_rng(11)
    member_vars = ["tmp2m__m01", "tmp2m__m02", "tmp2m__control"]
    fhs = [0, 6, 12]
    for var in member_vars:
        for fh in fhs:
            if var == "tmp2m__m02" and fh == 12:
                continue  # missing frame -> present=False
            data = rng.uniform(0.0, 80.0, size=(HEIGHT, WIDTH)).astype(np.float32)
            data[:3, :] = np.nan  # nodata band at the grid's north edge
            write_slim_grid_frame_for_run_root(
                run_root=run_root, model="gefs", var=var,
                fh=fh, values=data, transform=transform,
            )

    # Interior, nodata-band, and out-of-coverage points.
    for lat, lon in ((43.5, -101.5), (81.5, -101.5), (43.5, 5.0)):
        batch = sampling.sample_member_values_seek(
            "gefs", run_id, member_vars, fhs, lat=lat, lon=lon,
        )
        for var in member_vars:
            for fh in fhs:
                single = sampling.sample_binary_value_seek(
                    "gefs", run_id, var, fh, lat=lat, lon=lon,
                )
                assert batch[(var, fh)] == single, (var, fh, lat, lon)
