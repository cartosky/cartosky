"""Pre-encode gate for the observed-product publishers (current_analysis,
GOES-East, MRMS) — dual mode, mirroring test_publish_pre_encode_shadow_gate.py.

Same invariants as the NDFD/WPC template:
- ``check_pre_encode_value_sanity`` runs UNCONDITIONALLY on every fresh frame
  write; the allowlist decides only what a failure means.
- Enforced (model in CARTOSKY_BINARY_SAMPLING_MODELS): failure or a gate error
  rejects BEFORE any artifact write, and passing frames skip only the value
  COG (grid binary + sidecar still written).
- Shadow (default): log-only, full publish proceeds.
- Rejected frames drop cleanly out of targets/frame_count/manifest entries;
  the bundle continues past them.
- Reuse/hardlink paths are deliberately NOT gated (byte-identical to frames
  gated at their original fresh write) — not covered here by design.

Publisher-specific coverage: current_analysis gates per (var, fh) inside the
frame's variable loop (a rejected variable drops alone, not the whole frame);
GOES gates per band-publish invocation (two-bands test); MRMS gates all four
fresh-write sites, including the finalize/deferred supplemental path that
writes directly into the published run dir, and radar_ptype is gated with its
own indexed (ptype_breaks) spec, never reflectivity's.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import goes_publish, mrms_publish, rtma_ru_publish


def _set_allowlist(monkeypatch: pytest.MonkeyPatch, allowlist: str) -> None:
    if allowlist:
        monkeypatch.setenv("CARTOSKY_BINARY_SAMPLING_MODELS", allowlist)
    else:
        monkeypatch.delenv("CARTOSKY_BINARY_SAMPLING_MODELS", raising=False)


def _fail_if_called(name: str):
    def _spy(*args, **kwargs):
        raise AssertionError(f"{name} must not be called")

    return _spy


def _grid_spies(monkeypatch: pytest.MonkeyPatch, module) -> list[tuple[str, int]]:
    """Enable grid builds with recorders so tests can assert which (var, fh)
    pairs got a grid binary write."""
    grid_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(module, "grid_build_enabled", lambda: True)
    monkeypatch.setattr(
        module,
        "write_grid_frames_for_run_root",
        lambda **kwargs: grid_calls.append((kwargs["var"], int(kwargs["fh"]))),
    )
    monkeypatch.setattr(module, "build_grid_manifests_for_run_root", lambda **kwargs: 0)
    return grid_calls


def _cog_recorder(monkeypatch: pytest.MonkeyPatch, module) -> list[str]:
    """Record value-COG writes by the var-directory name they land in."""
    cog_calls: list[str] = []
    monkeypatch.setattr(
        module,
        "write_value_cog",
        lambda values, output_path, **kwargs: (
            cog_calls.append(Path(output_path).parent.name),
            Path(output_path).write_bytes(b"value"),
        )[1],
    )
    return cog_calls


def _mock_sidecar(monkeypatch: pytest.MonkeyPatch, module, attr: str) -> None:
    monkeypatch.setattr(
        module,
        attr,
        lambda **kwargs: {"model": kwargs["model"], "run": kwargs["run_id"], "var": kwargs["var_id"], "fh": kwargs["fh"]},
    )


def _gate_spy(monkeypatch: pytest.MonkeyPatch, module, *, result: bool = True) -> list[str]:
    labels: list[str] = []
    monkeypatch.setattr(
        module,
        "check_pre_encode_value_sanity",
        lambda *a, **k: (labels.append(k.get("label", "")), result)[1],
    )
    return labels


# ── current_analysis ─────────────────────────────────────────────────

_CA_TIME = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
_GOOD_TEMPS = np.array([[70.0, 71.0], [72.0, 73.0]], dtype=np.float32)
_FLAT_TEMPS = np.full((2, 2), 32.0, dtype=np.float32)


def _ca_frame(values_by_var: dict[str, np.ndarray]) -> rtma_ru_publish.CurrentAnalysisBundleFrame:
    return rtma_ru_publish.CurrentAnalysisBundleFrame(
        valid_time=_CA_TIME,
        values_by_var=values_by_var,
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
    )


def _ca_harness(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(rtma_ru_publish, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(
        rtma_ru_publish,
        "float_to_rgba",
        lambda values, color_map_id, meta_var_key=None: (
            np.zeros((4, values.shape[0], values.shape[1]), dtype=np.uint8),
            {"kind": "continuous", "min": 0.0, "max": 100.0},
        ),
    )
    _mock_sidecar(monkeypatch, rtma_ru_publish, "_build_sidecar_json")
    return _cog_recorder(monkeypatch, rtma_ru_publish)


def _publish_ca(tmp_path: Path, values_by_var: dict[str, np.ndarray]):
    return rtma_ru_publish.publish_current_analysis_bundle(
        data_root=tmp_path,
        frames=[_ca_frame(values_by_var)],
        publish_time=_CA_TIME + timedelta(minutes=5),
    )


@pytest.mark.parametrize("allowlist", ["", "current_analysis"])
def test_current_analysis_gate_runs_regardless_of_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, allowlist: str
) -> None:
    _set_allowlist(monkeypatch, allowlist)
    cog_calls = _ca_harness(monkeypatch)
    labels = _gate_spy(monkeypatch, rtma_ru_publish, result=True)

    result = _publish_ca(tmp_path, {"tmp2m": _GOOD_TEMPS})

    assert result.frame_count == 1
    assert labels == ["current_analysis/tmp2m/fh000"]
    assert cog_calls == ([] if allowlist else ["tmp2m"])


def test_current_analysis_shadow_gate_failure_never_rejects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_allowlist(monkeypatch, "")
    cog_calls = _ca_harness(monkeypatch)
    monkeypatch.setattr(rtma_ru_publish, "check_pre_encode_value_sanity", lambda *a, **k: False)

    with caplog.at_level("WARNING"):
        result = _publish_ca(tmp_path, {"tmp2m": _GOOD_TEMPS})

    assert result.frame_count == 1
    assert cog_calls == ["tmp2m"]
    assert (result.published_run_dir / "tmp2m" / "fh000.val.cog.tif").is_file()
    assert any("Phase C shadow gate failed" in r.getMessage() for r in caplog.records)


def test_current_analysis_enforced_gate_rejects_variable_not_whole_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A flat constant field fails the REAL gate for dp2m's "dp2m" spec
    # (min == max, no dry-frame allowance); tmp2m's varied field passes. The
    # gate is per-variable: dp2m drops alone, the frame's other variable still
    # publishes, and neither the COG nor the grid write is reached for dp2m.
    assert (
        rtma_ru_publish.check_pre_encode_value_sanity(
            _FLAT_TEMPS,
            rtma_ru_publish.get_color_map_spec("dp2m"),
            var_spec_model=None,
            var_capability=None,
            label="current_analysis/dp2m flat-field pin",
        )
        is False
    )

    _set_allowlist(monkeypatch, "current_analysis")
    _ca_harness(monkeypatch)
    monkeypatch.setattr(rtma_ru_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    grid_calls = _grid_spies(monkeypatch, rtma_ru_publish)

    with caplog.at_level("INFO"):
        result = _publish_ca(tmp_path, {"tmp2m": _GOOD_TEMPS, "dp2m": _FLAT_TEMPS})

    assert result.frame_count == 1
    assert grid_calls == [("tmp2m", 0)]
    assert any(
        "Pre-encode sanity gate rejected frame" in r.getMessage()
        and "model=current_analysis var=dp2m fh000" in r.getMessage()
        and r.levelname == "ERROR"
        for r in caplog.records
    )
    assert (result.published_run_dir / "tmp2m" / "fh000.json").is_file()
    assert not (result.published_run_dir / "dp2m").exists()
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["metadata"]["variables_published"] == ["tmp2m"]


def test_current_analysis_binary_only_good_frame_skips_cog_but_writes_grid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_allowlist(monkeypatch, "current_analysis")
    _ca_harness(monkeypatch)
    monkeypatch.setattr(rtma_ru_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    grid_calls = _grid_spies(monkeypatch, rtma_ru_publish)

    with caplog.at_level("INFO"):
        result = _publish_ca(tmp_path, {"tmp2m": _GOOD_TEMPS})

    assert result.frame_count == 1
    assert grid_calls == [("tmp2m", 0)]
    assert (result.published_run_dir / "tmp2m" / "fh000.json").is_file()
    assert not (result.published_run_dir / "tmp2m" / "fh000.val.cog.tif").exists()
    assert any(
        "Value COG write skipped (model=current_analysis is binary-only)" in r.getMessage()
        for r in caplog.records
    )


# ── GOES-East ────────────────────────────────────────────────────────

_GOES_TIME = datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc)
# Brightness temps in Kelvin, matching the published CMI values.
_GOOD_BT = np.array([[220.0, 240.0], [260.0, 280.0]], dtype=np.float32)
_FLAT_BT = np.full((2, 2), 250.0, dtype=np.float32)


def _goes_frame(values: np.ndarray, *, slot_offset_minutes: int = 0) -> goes_publish.GOESBundleFrame:
    slot = _GOES_TIME + timedelta(minutes=slot_offset_minutes)
    return goes_publish.GOESBundleFrame(
        valid_time=slot,
        slot_time=slot,
        values=values,
        transform=from_origin(0.0, 2.0, 1.0, 1.0),
    )


def _goes_harness(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(goes_publish, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(
        goes_publish,
        "float_to_rgba",
        lambda values, color_map_id, meta_var_key=None: (
            np.zeros((4, values.shape[0], values.shape[1]), dtype=np.uint8),
            {"kind": "continuous", "min": 0.0, "max": 1.0},
        ),
    )
    _mock_sidecar(monkeypatch, goes_publish, "build_sidecar_json")
    return _cog_recorder(monkeypatch, goes_publish)


def _publish_goes(tmp_path: Path, frames, *, band_config=None, publish_offset_minutes: int = 5):
    return goes_publish.publish_goes_bundle(
        data_root=tmp_path,
        frames=frames,
        publish_time=_GOES_TIME + timedelta(minutes=publish_offset_minutes),
        band_config=band_config,
    )


@pytest.mark.parametrize("allowlist", ["", "goes-east"])
def test_goes_gate_runs_regardless_of_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, allowlist: str
) -> None:
    _set_allowlist(monkeypatch, allowlist)
    cog_calls = _goes_harness(monkeypatch)
    labels = _gate_spy(monkeypatch, goes_publish, result=True)

    result = _publish_goes(tmp_path, [_goes_frame(_GOOD_BT)])

    assert result.frame_count == 1
    assert labels == ["goes-east/ir13/fh000"]
    assert cog_calls == ([] if allowlist else ["ir13"])


def test_goes_shadow_gate_failure_never_rejects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_allowlist(monkeypatch, "")
    cog_calls = _goes_harness(monkeypatch)
    monkeypatch.setattr(goes_publish, "check_pre_encode_value_sanity", lambda *a, **k: False)

    with caplog.at_level("WARNING"):
        result = _publish_goes(tmp_path, [_goes_frame(_GOOD_BT)])

    assert result.frame_count == 1
    assert cog_calls == ["ir13"]
    assert (result.published_run_dir / "ir13" / "fh000.val.cog.tif").is_file()
    assert any("Phase C shadow gate failed" in r.getMessage() for r in caplog.records)


def test_goes_enforced_gate_rejects_bad_frame_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A flat field fails the REAL gate for ir13's "goes_ir13_enhanced" spec
    # (min == max, no dry-frame allowance).
    assert (
        goes_publish.check_pre_encode_value_sanity(
            _FLAT_BT,
            goes_publish.get_color_map_spec("goes_ir13_enhanced"),
            var_spec_model=goes_publish.GOES_EAST_MODEL.get_var("ir13"),
            var_capability=goes_publish.GOES_EAST_MODEL.get_var_capability("ir13"),
            label="goes-east/ir13 flat-field pin",
        )
        is False
    )

    _set_allowlist(monkeypatch, "goes-east")
    _goes_harness(monkeypatch)
    monkeypatch.setattr(goes_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    grid_calls = _grid_spies(monkeypatch, goes_publish)

    good = _goes_frame(_GOOD_BT, slot_offset_minutes=0)
    bad = _goes_frame(_FLAT_BT, slot_offset_minutes=10)
    with caplog.at_level("INFO"):
        result = _publish_goes(tmp_path, [good, bad], publish_offset_minutes=15)

    assert result.frame_count == 1
    assert grid_calls == [("ir13", 0)]
    assert any(
        "Pre-encode sanity gate rejected frame" in r.getMessage()
        and "model=goes-east var=ir13 fh001" in r.getMessage()
        and r.levelname == "ERROR"
        for r in caplog.records
    )
    assert (result.published_run_dir / "ir13" / "fh000.json").is_file()
    assert not (result.published_run_dir / "ir13" / "fh001.json").exists()
    assert not (result.published_run_dir / "ir13" / "fh001.val.cog.tif").exists()
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["variables"]["ir13"]["available_frames"] == 1
    assert [f["fh"] for f in manifest["variables"]["ir13"]["frames"]] == [0]


def test_goes_binary_only_good_frame_skips_cog_but_writes_grid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_allowlist(monkeypatch, "goes-east")
    _goes_harness(monkeypatch)
    monkeypatch.setattr(goes_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    grid_calls = _grid_spies(monkeypatch, goes_publish)

    with caplog.at_level("INFO"):
        result = _publish_goes(tmp_path, [_goes_frame(_GOOD_BT)])

    assert result.frame_count == 1
    assert grid_calls == [("ir13", 0)]
    assert (result.published_run_dir / "ir13" / "fh000.json").is_file()
    assert not (result.published_run_dir / "ir13" / "fh000.val.cog.tif").exists()
    assert any(
        "Value COG write skipped (model=goes-east is binary-only)" in r.getMessage()
        for r in caplog.records
    )


def test_goes_two_bands_gated_independently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # This publisher runs once per band (band_config), each invocation minting
    # its own run — the gate must fire for every band's publish with that
    # band's own var id, not just the first band ever published.
    _set_allowlist(monkeypatch, "")
    _goes_harness(monkeypatch)
    labels = _gate_spy(monkeypatch, goes_publish, result=True)

    _publish_goes(tmp_path, [_goes_frame(_GOOD_BT)], publish_offset_minutes=5)
    _publish_goes(
        tmp_path,
        [_goes_frame(_GOOD_BT)],
        band_config=goes_publish.BAND_CONFIG_WV9,
        publish_offset_minutes=6,
    )

    assert labels == ["goes-east/ir13/fh000", "goes-east/wv9/fh000"]


# ── MRMS ─────────────────────────────────────────────────────────────

_MRMS_TIME = datetime(2026, 7, 12, 18, 0, tzinfo=timezone.utc)
_GOOD_REFL = np.array([[5.0, 15.0], [30.0, 50.0]], dtype=np.float32)
_FLAT_REFL = np.full((2, 2), 20.0, dtype=np.float32)


def _mrms_frame(
    values: np.ndarray,
    *,
    valid_offset_minutes: int = 0,
    precip_flag_values: np.ndarray | None = None,
) -> mrms_publish.MRMSBundleFrame:
    return mrms_publish.MRMSBundleFrame(
        valid_time=_MRMS_TIME + timedelta(minutes=valid_offset_minutes),
        values=values,
        precip_flag_values=precip_flag_values,
    )


def _mrms_supplemental_frame(values: np.ndarray) -> mrms_publish.MRMSSupplementalFrame:
    return mrms_publish.MRMSSupplementalFrame(
        valid_time=_MRMS_TIME,
        values=values,
    )


def _mrms_harness(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(mrms_publish, "grid_build_enabled", lambda: False)
    # Frames in these tests carry no source CRS; pass each frame's own values
    # through unchanged instead of enforcing the real CONUS target shape.
    monkeypatch.setattr(
        mrms_publish,
        "_warp_frame_to_target_grid",
        lambda values, *, frame, resampling="bilinear": np.asarray(values, dtype=np.float32),
    )
    monkeypatch.setattr(mrms_publish, "_target_grid_transform", lambda: from_origin(0.0, 2.0, 1.0, 1.0))
    monkeypatch.setattr(
        mrms_publish,
        "colorize_metadata",
        lambda values, color_map_id, meta_var_key=None: {"kind": "discrete", "min": 0.0, "max": 70.0},
    )
    _mock_sidecar(monkeypatch, mrms_publish, "build_sidecar_json")
    return _cog_recorder(monkeypatch, mrms_publish)


def _publish_mrms(tmp_path: Path, frames, **kwargs):
    return mrms_publish.publish_mrms_bundle(
        data_root=tmp_path,
        frames=frames,
        publish_time=_MRMS_TIME + timedelta(minutes=4),
        **kwargs,
    )


@pytest.mark.parametrize("allowlist", ["", "mrms"])
def test_mrms_gate_runs_regardless_of_allowlist_for_refl_and_ptype(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, allowlist: str
) -> None:
    # A frame with PrecipFlag exercises two of the four gated sites in one
    # publish: the reflectivity write and the composited radar_ptype write,
    # each gated under its own var id.
    _set_allowlist(monkeypatch, allowlist)
    cog_calls = _mrms_harness(monkeypatch)
    labels = _gate_spy(monkeypatch, mrms_publish, result=True)

    flags = np.ones_like(_GOOD_REFL)  # rain everywhere
    result = _publish_mrms(tmp_path, [_mrms_frame(_GOOD_REFL, precip_flag_values=flags)])

    assert result.frame_count == 1
    assert labels == ["mrms/reflectivity/fh000", "mrms/mrms_radar_ptype/fh000"]
    assert cog_calls == ([] if allowlist else ["reflectivity", "mrms_radar_ptype"])


def test_mrms_shadow_gate_failure_never_rejects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_allowlist(monkeypatch, "")
    cog_calls = _mrms_harness(monkeypatch)
    monkeypatch.setattr(mrms_publish, "check_pre_encode_value_sanity", lambda *a, **k: False)

    with caplog.at_level("WARNING"):
        result = _publish_mrms(tmp_path, [_mrms_frame(_GOOD_REFL)])

    assert result.frame_count == 1
    assert cog_calls == ["reflectivity"]
    assert (result.published_run_dir / "reflectivity" / "fh000.val.cog.tif").is_file()
    assert any("Phase C shadow gate failed" in r.getMessage() for r in caplog.records)


def test_mrms_enforced_gate_rejects_bad_frame_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A flat field fails the REAL gate for the "mrms_reflectivity" spec
    # (min == max, no dry-frame allowance on the discrete branch).
    assert (
        mrms_publish.check_pre_encode_value_sanity(
            _FLAT_REFL,
            mrms_publish.get_color_map_spec("mrms_reflectivity"),
            var_spec_model=mrms_publish.MRMS_MODEL.get_var("reflectivity"),
            var_capability=mrms_publish.MRMS_MODEL.get_var_capability("reflectivity"),
            label="mrms/reflectivity flat-field pin",
        )
        is False
    )

    _set_allowlist(monkeypatch, "mrms")
    _mrms_harness(monkeypatch)
    monkeypatch.setattr(mrms_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    grid_calls = _grid_spies(monkeypatch, mrms_publish)

    good = _mrms_frame(_GOOD_REFL, valid_offset_minutes=0)
    bad = _mrms_frame(_FLAT_REFL, valid_offset_minutes=2)
    with caplog.at_level("INFO"):
        result = _publish_mrms(tmp_path, [good, bad])

    assert result.frame_count == 1
    assert grid_calls == [("reflectivity", 0)]
    assert any(
        "Pre-encode sanity gate rejected frame" in r.getMessage()
        and "model=mrms var=reflectivity fh001" in r.getMessage()
        and r.levelname == "ERROR"
        for r in caplog.records
    )
    assert (result.published_run_dir / "reflectivity" / "fh000.json").is_file()
    assert not (result.published_run_dir / "reflectivity" / "fh001.json").exists()
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["variables"]["reflectivity"]["available_frames"] == 1
    assert [f["fh"] for f in manifest["variables"]["reflectivity"]["frames"]] == [0]


def test_mrms_binary_only_good_frame_skips_cog_but_writes_grid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_allowlist(monkeypatch, "mrms")
    _mrms_harness(monkeypatch)
    monkeypatch.setattr(mrms_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    grid_calls = _grid_spies(monkeypatch, mrms_publish)

    with caplog.at_level("INFO"):
        result = _publish_mrms(tmp_path, [_mrms_frame(_GOOD_REFL)])

    assert result.frame_count == 1
    assert grid_calls == [("reflectivity", 0)]
    assert (result.published_run_dir / "reflectivity" / "fh000.json").is_file()
    assert not (result.published_run_dir / "reflectivity" / "fh000.val.cog.tif").exists()
    assert any(
        "Value COG write skipped (model=mrms is binary-only)" in r.getMessage()
        for r in caplog.records
    )


def test_mrms_ptype_rejection_uses_indexed_spec_and_degrades_to_reflectivity_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # radar_ptype is gated on the COMPOSITED indexed array with the real
    # "mrms_radar_ptype" spec (type=indexed with ptype_breaks -> categorical
    # branch, 0.998 nodata threshold, dry carve-out only when fully empty).
    # Full-coverage reflectivity passes its own gate, but a composite with a
    # single rain pixel out of 2000 (nodata 0.9995, not fully dry) fails —
    # the frame degrades to reflectivity-only, exactly like the existing
    # ptype-write-failure path.
    refl = np.linspace(10.0, 60.0, 2000, dtype=np.float32).reshape(40, 50)
    flags = np.zeros((40, 50), dtype=np.float32)
    flags.flat[0] = 1.0  # one rain pixel; everything else "no precipitation"

    composed = mrms_publish.compose_mrms_radar_ptype(refl, flags)
    assert int(np.isfinite(composed).sum()) == 1
    assert (
        mrms_publish.check_pre_encode_value_sanity(
            composed,
            mrms_publish.get_color_map_spec("mrms_radar_ptype"),
            var_spec_model=mrms_publish.MRMS_MODEL.get_var("mrms_radar_ptype"),
            var_capability=mrms_publish.MRMS_MODEL.get_var_capability("mrms_radar_ptype"),
            label="mrms/mrms_radar_ptype sparse pin",
        )
        is False
    )

    _set_allowlist(monkeypatch, "mrms")
    _mrms_harness(monkeypatch)
    monkeypatch.setattr(mrms_publish, "write_value_cog", _fail_if_called("write_value_cog"))
    grid_calls = _grid_spies(monkeypatch, mrms_publish)

    with caplog.at_level("INFO"):
        result = _publish_mrms(tmp_path, [_mrms_frame(refl, precip_flag_values=flags)])

    assert result.frame_count == 1
    # Reflectivity's grid binary written; the rejected ptype composite's never.
    assert grid_calls == [("reflectivity", 0)]
    assert any(
        "Pre-encode sanity gate rejected frame" in r.getMessage()
        and "model=mrms var=mrms_radar_ptype fh000" in r.getMessage()
        and r.levelname == "ERROR"
        for r in caplog.records
    )
    manifest = json.loads(result.manifest_path.read_text())
    assert "mrms_radar_ptype" not in manifest["variables"]
    assert not (result.published_run_dir / "mrms_radar_ptype").exists()


def test_mrms_finalize_deferred_supplemental_path_is_gated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # finalize_mrms_published_run writes supplemental frames DIRECTLY into the
    # published run dir (not staging) — the easy-to-miss fourth write site.
    # The real "mrms_recent_precip_6h" spec has allow_dry_frame=True, so a
    # flat field passes; its genuinely-bad input is >95% nodata with finite
    # pixels present. Enforced mode must reject that frame with nothing
    # written to the published dir and the manifest listing only the good one.
    bad = np.full((40, 50), np.nan, dtype=np.float32)
    bad.flat[0] = 0.2
    bad.flat[1] = 0.6
    good = np.array([[0.1, 0.2], [0.4, 0.6]], dtype=np.float32)
    assert (
        mrms_publish.check_pre_encode_value_sanity(
            bad,
            mrms_publish.get_color_map_spec("mrms_recent_precip_6h"),
            var_spec_model=mrms_publish.MRMS_MODEL.get_var("mrms_recent_precip_6h"),
            var_capability=mrms_publish.MRMS_MODEL.get_var_capability("mrms_recent_precip_6h"),
            label="mrms/mrms_recent_precip_6h sparse pin",
        )
        is False
    )

    _set_allowlist(monkeypatch, "mrms")
    _mrms_harness(monkeypatch)
    monkeypatch.setattr(mrms_publish, "write_value_cog", _fail_if_called("write_value_cog"))

    run_id = "20260712_1804z"
    published_run_root = tmp_path / "published" / "mrms" / run_id / "reflectivity"
    published_run_root.mkdir(parents=True)
    manifest_path = tmp_path / "manifests" / "mrms" / f"{run_id}.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({
        "last_updated": "2026-07-12T18:04:00Z",
        "variables": {},
    }))

    # The bad frame sorts first (earlier valid time via identical times is
    # fine — order by list position after the sort key ties); give the good
    # frame a later valid time explicitly to pin fh assignment.
    bad_frame = mrms_publish.MRMSSupplementalFrame(valid_time=_MRMS_TIME, values=bad)
    good_frame = mrms_publish.MRMSSupplementalFrame(
        valid_time=_MRMS_TIME + timedelta(hours=1), values=good
    )

    with caplog.at_level("INFO"):
        mrms_publish.finalize_mrms_published_run(
            data_root=tmp_path,
            run_id=run_id,
            supplemental_variable_frames={"mrms_recent_precip_6h": [bad_frame, good_frame]},
            build_grid_artifacts=False,
        )

    var_dir = tmp_path / "published" / "mrms" / run_id / "mrms_recent_precip_6h"
    # fh000 (bad) rejected: no artifacts; fh001 (good) written with sidecar
    # but no value COG (binary-only skip applies on this path too).
    assert not (var_dir / "fh000.json").exists()
    assert not (var_dir / "fh000.val.cog.tif").exists()
    assert (var_dir / "fh001.json").is_file()
    assert not (var_dir / "fh001.val.cog.tif").exists()
    assert any(
        "Pre-encode sanity gate rejected frame" in r.getMessage()
        and "model=mrms var=mrms_recent_precip_6h fh000" in r.getMessage()
        and r.levelname == "ERROR"
        for r in caplog.records
    )
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["variables"]["mrms_recent_precip_6h"]
    assert entry["available_frames"] == 1
    assert [f["fh"] for f in entry["frames"]] == [1]


def test_mrms_sparse_masked_reflectivity_passes_real_gate() -> None:
    # With the NSSL sentinels (-999/-99) masked to NaN at decode, a typical
    # reflectivity frame is legitimately >95% nodata; allow_sparse_frame on
    # the mrms_reflectivity spec keeps the gate green while real echo exists.
    sparse = np.full((10, 10), np.nan, dtype=np.float32)
    sparse[0, 0] = 12.5
    sparse[5, 5] = 30.0
    assert (
        mrms_publish.check_pre_encode_value_sanity(
            sparse,
            mrms_publish.get_color_map_spec("mrms_reflectivity"),
            var_spec_model=mrms_publish.MRMS_MODEL.get_var("reflectivity"),
            var_capability=mrms_publish.MRMS_MODEL.get_var_capability("reflectivity"),
            label="mrms/reflectivity sparse pin",
        )
        is True
    )


def test_mrms_fully_empty_reflectivity_still_fails_real_gate() -> None:
    # allow_sparse_frame must not swallow an entirely empty frame — that is
    # still the empty-fetch/misalignment signal the nodata gate exists for.
    empty = np.full((10, 10), np.nan, dtype=np.float32)
    assert (
        mrms_publish.check_pre_encode_value_sanity(
            empty,
            mrms_publish.get_color_map_spec("mrms_reflectivity"),
            var_spec_model=mrms_publish.MRMS_MODEL.get_var("reflectivity"),
            var_capability=mrms_publish.MRMS_MODEL.get_var_capability("reflectivity"),
            label="mrms/reflectivity empty pin",
        )
        is False
    )
