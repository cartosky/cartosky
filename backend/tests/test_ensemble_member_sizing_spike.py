"""Unit tests for the Phase 1 ensemble member sizing spike script.

Synthetic arrays only — no network, no prod paths (spike execution model:
local verification is limited to encode/suffix-resolution/stats-prototype
pieces plus the --selftest plumbing).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_bounds

from backend.scripts import ensemble_member_sizing_spike as spike

# The spike module imports production primitives via the `app.*` namespace
# (it puts backend/ on sys.path like the other canary scripts), while tests
# import via `backend.app.*` — two distinct module objects. Identity-sensitive
# assertions must use the spike module's own imports.
_PACKING_BY_MODEL_VAR = spike._PACKING_BY_MODEL_VAR


# ── Synthetic fixtures ───────────────────────────────────────────────
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


def _write_frames(root: Path, transform, values, members, fhs):
    for offset, member in enumerate(members):
        for fh in fhs:
            spike.write_slim_member_frame(
                run_root=root,
                model=spike.MODEL,
                var_id=spike.member_var_id(member),
                fh=fh,
                values=values + offset,
                transform=transform,
            )


# ── Suffix normalization / packing resolution ────────────────────────
def test_suffix_normalization_maps_members_to_mean_twin() -> None:
    assert spike.resolve_member_packing_var("tmp2m__m01") == "tmp2m__mean"
    assert spike.resolve_member_packing_var("tmp2m__m30") == "tmp2m__mean"
    assert spike.resolve_member_packing_var("tmp2m__control") == "tmp2m__mean"
    # Non-member ids pass through unchanged.
    assert spike.resolve_member_packing_var("tmp2m__mean") == "tmp2m__mean"
    assert spike.resolve_member_packing_var("tmp2m") == "tmp2m"
    # Zero-padded two-digit members only — 3-digit or 1-digit are not member ids.
    assert spike.resolve_member_packing_var("tmp2m__m1") == "tmp2m__m1"
    assert spike.resolve_member_packing_var("tmp2m__m001") == "tmp2m__m001"


def test_member_packing_is_the_mean_entry() -> None:
    packing = spike.member_packing("gefs", "tmp2m__m17")
    assert packing is _PACKING_BY_MODEL_VAR[("gefs", "tmp2m__mean")]
    packing_control = spike.member_packing("gefs", "tmp2m__control")
    assert packing_control is packing


def test_member_packing_unknown_var_raises() -> None:
    with pytest.raises(ValueError, match="No packing entry"):
        spike.member_packing("gefs", "nonexistent__m01")


def test_member_var_id_and_herbie_kwarg() -> None:
    assert spike.member_var_id("m07") == "tmp2m__m07"
    assert spike.member_var_id("control") == "tmp2m__control"
    assert spike.member_herbie_kwarg("m07", control_kwarg=0) == 7
    assert spike.member_herbie_kwarg("m30", control_kwarg=0) == 30
    assert spike.member_herbie_kwarg("control", control_kwarg=0) == 0
    assert spike.member_herbie_kwarg("control", control_kwarg="c00") == "c00"


# ── Slim writer: bytes + meta schema ─────────────────────────────────
def test_slim_frame_write_matches_production_meta_schema(tmp_path, transform, values) -> None:
    meta = spike.write_slim_member_frame(
        run_root=tmp_path, model="gefs", var_id="tmp2m__m01",
        fh=6, values=values, transform=transform,
    )
    frame_path = tmp_path / "tmp2m__m01" / "grid" / "fh006.l0.u16.bin"
    meta_path = tmp_path / "tmp2m__m01" / "grid" / "fh006.l0.meta.json"
    assert frame_path.is_file()
    assert meta_path.is_file()
    assert frame_path.stat().st_size == HEIGHT * WIDTH * 2
    # No tmp file left behind (atomic rename).
    assert not list((tmp_path / "tmp2m__m01" / "grid").glob("*.tmp"))

    on_disk = json.loads(meta_path.read_text())
    assert on_disk == meta
    assert on_disk["format_version"] == 1
    assert on_disk["width"] == WIDTH
    assert on_disk["height"] == HEIGHT
    assert on_disk["fh"] == 6
    assert on_disk["level"] == 0
    assert on_disk["file"] == "fh006.l0.u16.bin"
    assert on_disk["projection"] == "EPSG:3857"
    assert len(on_disk["transform"]) == 6
    assert on_disk["bbox"] == pytest.approx(list(BOUNDS))
    assert "display_prep" not in on_disk  # slim profile: no display prep


def test_slim_frame_values_decode_via_production_sampler(tmp_path, transform, values) -> None:
    from backend.app.services.sampling import read_binary_sample_value

    spike.write_slim_member_frame(
        run_root=tmp_path, model="gefs", var_id="tmp2m__m01",
        fh=0, values=values, transform=transform,
    )
    frame_path = tmp_path / "tmp2m__m01" / "grid" / "fh000.l0.u16.bin"
    meta_path = tmp_path / "tmp2m__m01" / "grid" / "fh000.l0.meta.json"
    # Interior of the grid (central North America).
    value, no_data = read_binary_sample_value(
        frame_path, meta_path, model="gefs",
        var=spike.resolve_member_packing_var("tmp2m__m01"),
        lat=43.5, lon=-101.5,
    )
    assert not no_data
    assert value is not None
    assert 0.0 <= value <= 80.0
    # Out of coverage registers as expected-missing, not an error.
    value_out, no_data_out = read_binary_sample_value(
        frame_path, meta_path, model="gefs",
        var=spike.resolve_member_packing_var("tmp2m__m01"),
        lat=43.5, lon=5.0,
    )
    assert no_data_out
    assert value_out is None


def test_slim_frame_encode_uses_mean_packing_constants(tmp_path, transform, values) -> None:
    """Member frame bytes must equal a frame encoded with the __mean packing."""
    _encode_values = spike._encode_values

    spike.write_slim_member_frame(
        run_root=tmp_path, model="gefs", var_id="tmp2m__m02",
        fh=0, values=values, transform=transform,
    )
    packing = _PACKING_BY_MODEL_VAR[("gefs", "tmp2m__mean")]
    expected = _encode_values(
        values, scale=float(packing["scale"]), offset=float(packing["offset"]),
        nodata=int(packing["nodata"]), dtype="uint16",
    ).astype("<u2").tobytes(order="C")
    written = (tmp_path / "tmp2m__m02" / "grid" / "fh000.l0.u16.bin").read_bytes()
    assert written == expected


def test_upscaled_write_records_display_prep_and_effective_transform(tmp_path, transform, values) -> None:
    upscaled = spike.measurement_upscale(values)
    assert upscaled.shape == (HEIGHT * 3, WIDTH * 3)
    meta = spike.write_slim_member_frame(
        run_root=tmp_path, model="gefs", var_id="tmp2m__m01",
        fh=0, values=upscaled, transform=transform,
        display_prep_meta={"id": "spike_measurement_upscale_3x", "upscale_factor": 3},
        pre_upscale_shape=(HEIGHT, WIDTH),
    )
    # bbox from PRE-upscale dims; width/height post-upscale; transform pixel
    # size is 1/3 of the native transform — the production math.
    assert meta["bbox"] == pytest.approx(list(BOUNDS))
    assert meta["width"] == WIDTH * 3
    assert meta["height"] == HEIGHT * 3
    assert meta["transform"][0] == pytest.approx(transform.a / 3)
    assert meta["display_prep"]["upscale_factor"] == 3


# ── Resume logic ─────────────────────────────────────────────────────
def test_resume_detects_complete_missing_and_truncated(tmp_path, transform, values) -> None:
    spike.write_slim_member_frame(
        run_root=tmp_path, model="gefs", var_id="tmp2m__m01",
        fh=12, values=values, transform=transform,
    )
    assert spike.slim_frame_is_complete(tmp_path, "gefs", "tmp2m__m01", 12)
    assert not spike.slim_frame_is_complete(tmp_path, "gefs", "tmp2m__m01", 18)
    assert not spike.slim_frame_is_complete(tmp_path, "gefs", "tmp2m__m02", 12)

    frame_path = tmp_path / "tmp2m__m01" / "grid" / "fh012.l0.u16.bin"
    frame_path.write_bytes(frame_path.read_bytes()[:-10])
    assert not spike.slim_frame_is_complete(tmp_path, "gefs", "tmp2m__m01", 12)

    meta_path = tmp_path / "tmp2m__m01" / "grid" / "fh012.l0.meta.json"
    meta_path.write_text("not json")
    assert not spike.slim_frame_is_complete(tmp_path, "gefs", "tmp2m__m01", 12)


# ── Measurement-only upscale ─────────────────────────────────────────
def test_measurement_upscale_preserves_nan_mask_and_range(values) -> None:
    upscaled = spike.measurement_upscale(values)
    # NaN fringe (first 2 source rows) stays NaN in the upscaled grid. With
    # order-0 zoom, output row i maps to source row round(i/3), so rows 0–4
    # map onto the NaN fringe and row 10 is well inside finite data.
    assert np.all(np.isnan(upscaled[:5, :]))
    assert np.all(np.isfinite(upscaled[10, :]))
    finite = upscaled[np.isfinite(upscaled)]
    assert finite.min() >= 0.0
    assert finite.max() <= 80.0 + 1e-3


# ── Stats prototype ──────────────────────────────────────────────────
def test_stats_prototype_computes_and_reports(tmp_path, transform, values) -> None:
    members = ["m01", "m02", "m03"]
    _write_frames(tmp_path, transform, values, members, [0])
    result = spike.run_stats_prototype(
        tmp_path, members, [0], threshold=40.0, expected_member_count=3,
    )
    assert not result.get("skipped")
    assert result["fh"] == 0
    assert result["member_count"] == 3
    assert result["grid_shape"] == [HEIGHT, WIDTH]
    assert result["percentiles_computed"] == [10, 25, 50, 75, 90]
    # Members are values+0, +1, +2 — the p50 field is values+1, so the mean of
    # the p50 field tracks the source mean + 1.
    expected_p50_mean = float(np.nanmean(values)) + 1.0
    assert result["p50_field_mean"] == pytest.approx(expected_p50_mean, abs=0.1)
    assert 0.0 <= result["prob_gt_threshold_mean"] <= 1.0


def test_stats_prototype_completeness_gate_skips_partial_member_set(
    tmp_path, transform, values,
) -> None:
    _write_frames(tmp_path, transform, values, ["m01", "m02"], [0])
    result = spike.run_stats_prototype(
        tmp_path, ["m01", "m02", "m03"], [0], threshold=40.0, expected_member_count=3,
    )
    assert result.get("skipped")
    assert "completeness gate" in result["reason"]


def test_stats_prototype_picks_first_complete_fh(tmp_path, transform, values) -> None:
    members = ["m01", "m02"]
    # fh 0 incomplete (m02 missing), fh 6 complete.
    spike.write_slim_member_frame(
        run_root=tmp_path, model="gefs", var_id="tmp2m__m01",
        fh=0, values=values, transform=transform,
    )
    _write_frames(tmp_path, transform, values, members, [6])
    result = spike.run_stats_prototype(
        tmp_path, members, [0, 6], threshold=40.0, expected_member_count=2,
    )
    assert result["fh"] == 6


# ── Tree measurement ─────────────────────────────────────────────────
def test_measure_tree_counts_frames_and_sidecars(tmp_path, transform, values) -> None:
    _write_frames(tmp_path, transform, values, ["m01"], [0, 6])
    measured = spike.measure_tree(tmp_path)
    assert measured["frame_count"] == 2
    assert measured["file_count"] == 4  # 2 bins + 2 metas
    assert measured["by_suffix"][".u16.bin"]["bytes"] == 2 * HEIGHT * WIDTH * 2
    assert measured["bytes_per_frame"] is not None
    empty = spike.measure_tree(tmp_path / "does_not_exist")
    assert empty["total_bytes"] == 0
    assert empty["bytes_per_frame"] is None


# ── Promote / retention sim ──────────────────────────────────────────
def test_promote_retention_sim_round_trips_and_deletes_copy(tmp_path, transform, values) -> None:
    slim_root = tmp_path / "slim"
    _write_frames(slim_root, transform, values, ["m01", "m02"], [0, 6])
    before = spike.measure_tree(slim_root)
    guards = spike.Guards(
        data_root=tmp_path, canary_root=tmp_path,
        disk_floor_bytes=0, rss_limit_bytes=1 << 62,
    )
    result = spike.run_promote_retention_sim(tmp_path, slim_root, guards)
    assert result["retention_sweep_files_deleted"] == before["file_count"]
    assert result["retention_sweep_bytes_deleted"] == before["total_bytes"]
    # Original slim tree untouched after the round trip; copy fully removed.
    after = spike.measure_tree(slim_root)
    assert after["total_bytes"] == before["total_bytes"]
    assert not (tmp_path / "scratch_retention_copy").exists()


# ── Guards ───────────────────────────────────────────────────────────
def test_disk_floor_guard_aborts(tmp_path) -> None:
    guards = spike.Guards(
        data_root=tmp_path, canary_root=tmp_path,
        disk_floor_bytes=1 << 62, rss_limit_bytes=1 << 62,
    )
    with pytest.raises(spike.SpikeAbort, match="below the"):
        guards.check()


def test_rss_guard_aborts_when_over_limit(tmp_path) -> None:
    guards = spike.Guards(
        data_root=tmp_path, canary_root=tmp_path,
        disk_floor_bytes=0, rss_limit_bytes=1,
    )
    if spike._current_rss_bytes() is None:
        pytest.skip("RSS introspection unavailable on this platform")
    with pytest.raises(spike.SpikeAbort, match="RSS"):
        guards.check()


# ── CLI parsing helpers ──────────────────────────────────────────────
def test_parse_members() -> None:
    assert spike._parse_members("all") == spike.ALL_MEMBERS
    assert spike._parse_members("m01,m02") == ["m01", "m02"]
    assert spike._parse_members("m01, control") == ["m01", "control"]
    assert spike._parse_members("m01,m01") == ["m01"]
    with pytest.raises(ValueError):
        spike._parse_members("m31")
    with pytest.raises(ValueError):
        spike._parse_members(" , ")


def test_parse_fhs() -> None:
    scheduled = list(range(0, 385, 6))
    assert spike._parse_fhs("all", scheduled) == scheduled
    assert spike._parse_fhs("12,0,6", scheduled) == [0, 6, 12]
    with pytest.raises(ValueError):
        spike._parse_fhs("7", scheduled)
    with pytest.raises(ValueError):
        spike._parse_fhs("", scheduled)


# ── Fetch bookkeeping ────────────────────────────────────────────────
def test_status_code_extraction() -> None:
    assert spike._status_code_from_error(RuntimeError("HTTP 429 Too Many Requests")) == 429
    assert spike._status_code_from_error(RuntimeError("server returned 503")) == 503
    assert spike._status_code_from_error(RuntimeError("no code here")) is None


def test_target_run_selection_prefers_newest_full_coverage(tmp_path, transform, values, monkeypatch) -> None:
    """Newest run with full mean coverage wins; newer-but-partial is skipped."""
    published = tmp_path / "published"
    scheduled = [0, 6]

    class _FakePlugin:
        def scheduled_fhs_for_var(self, var_key: str, cycle_hour: int) -> list[int]:
            return scheduled

    monkeypatch.setitem(spike.MODEL_REGISTRY, spike.MODEL, _FakePlugin())

    def _publish_mean(run_id: str, fhs: list[int]) -> None:
        run_root = published / spike.MODEL / run_id
        for fh in fhs:
            spike.write_slim_member_frame(
                run_root=run_root, model=spike.MODEL, var_id=spike.MEAN_VAR,
                fh=fh, values=values, transform=transform,
            )

    _publish_mean("20260701_00z", scheduled)      # older, complete
    _publish_mean("20260702_00z", scheduled)      # newest complete
    _publish_mean("20260702_06z", scheduled[:1])  # newest but partial

    run_id, fhs = spike.select_target_run(published, requested_run=None)
    assert run_id == "20260702_00z"
    assert fhs == scheduled
