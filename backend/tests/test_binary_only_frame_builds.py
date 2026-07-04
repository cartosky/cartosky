"""Phase F cutover: value-COG writes stop for binary-sampling models.

Covers the three load-bearing behaviors of the cutover:

1. For an allowlisted (binary-only) model, ``check_pre_encode_value_sanity``
   is the ENFORCED gate — a deliberately bad array rejects the frame instead
   of publishing ungated (the single most important test here: without it,
   binary-only models would have zero quality gating).
2. For an allowlisted model with good data, the frame builds successfully
   with NO value COG written and no COG gates run — grid binary + sidecar
   are the complete artifact set.
3. For a non-allowlisted model, nothing changes: value COG written,
   ``validate_cog``/``check_value_sanity`` run, and the pre-encode gate
   remains shadow/log-only (a failure does not reject).

Also covers the two value-COG consumers found during the pre-change sweep
that had to become substrate-aware: the scheduler's frame-completion marker
(``_frame_artifacts_exist``) and the frames API's ``has_cog`` flag.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

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
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_binary_only_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app.services.builder import pipeline as pipeline_module
from app.services.grid import write_grid_frame_for_run_root


class _Plugin:
    id = "gfs"

    def normalize_var_id(self, var_key: str) -> str:
        return str(var_key)

    def get_region(self, region: str):
        return region

    def search_patterns_for_var(self, *, var_key: str, fh: int, product: str, var_spec) -> list[str]:
        del var_key, fh, product
        selectors = getattr(var_spec, "selectors", None)
        search = getattr(selectors, "search", None) if selectors is not None else None
        return list(search or [])

    def herbie_request(
        self,
        *,
        product: str,
        var_key: str,
        ensemble_view=None,
        run_date=None,
        fh: int,
        search_pattern: str | None = None,
    ):
        del var_key, ensemble_view, run_date, fh, search_pattern
        return SimpleNamespace(model=self.id, product=product, herbie_kwargs=None)


def _fail_if_called(name: str):
    def _spy(*args, **kwargs):
        raise AssertionError(f"{name} must not be called for a binary-only model")

    return _spy


def _harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fetched: np.ndarray,
) -> None:
    """Mock everything upstream of the gates the same way the existing Phase C
    pipeline test does, with `fetched` as the fetched/warped array."""
    var_spec_model = SimpleNamespace(
        id="tmp2m",
        derived=False,
        selectors=SimpleNamespace(hints={}, search=[":TMP:2 m above ground:"]),
        kind="continuous",
        units="F",
    )
    var_capability = SimpleNamespace(color_map_id="tmp2m", kind="continuous", units="F")

    monkeypatch.setattr(pipeline_module, "_ensure_products_ready", lambda **kwargs: None)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_spec", lambda *a, **k: var_spec_model)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_capability", lambda *a, **k: var_capability)
    monkeypatch.setattr(
        pipeline_module,
        "get_color_map_spec",
        lambda color_map_id: {
            "id": color_map_id,
            "type": "continuous",
            "units": "F",
            "range": [-100.0, 140.0],
            "colors": ["#000000", "#ffffff"],
        },
    )
    monkeypatch.setattr(
        pipeline_module,
        "fetch_variable",
        lambda **kwargs: (fetched, "EPSG:4326", from_origin(-101.0, 46.0, 1.0, 1.0)),
    )
    monkeypatch.setattr(pipeline_module, "convert_units", lambda data, **kwargs: data)
    monkeypatch.setattr(
        pipeline_module,
        "warp_to_target_grid",
        lambda data, src_crs, src_transform, **kwargs: (data, src_transform),
    )
    monkeypatch.setattr(
        pipeline_module,
        "float_to_rgba",
        lambda data, color_map_id, meta_var_key=None: (
            np.zeros((4, data.shape[0], data.shape[1]), dtype=np.uint8),
            {"kind": "continuous", "units": "F", "min": 0.0, "max": 100.0},
        ),
    )
    monkeypatch.setattr(pipeline_module, "grid_build_enabled", lambda: True)
    monkeypatch.setattr(pipeline_module, "_build_contour_metadata_for_variable", lambda **kwargs: ({}, None))


def _build(tmp_path: Path):
    return pipeline_module.build_frame(
        model="gfs",
        region="conus",
        var_id="tmp2m",
        fh=0,
        run_date=datetime(2026, 6, 30, 0, 0),
        data_root=tmp_path,
        product="pgrb2.0p25",
        model_plugin=_Plugin(),
        return_status=True,
    )


def test_binary_only_model_rejects_bad_frame(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A flat constant field is genuinely bad input the REAL pre-encode gate
    # rejects (min == max). With gfs allowlisted, that rejection must fail the
    # frame build — not publish it ungated — and the COG write/gates must
    # never even be reached.
    monkeypatch.setenv("CARTOSKY_BINARY_SAMPLING_MODELS", "gfs")
    _harness(monkeypatch, fetched=np.full((2, 2), 32.0, dtype=np.float32))
    monkeypatch.setattr(pipeline_module, "write_value_cog", _fail_if_called("write_value_cog"))
    monkeypatch.setattr(pipeline_module, "validate_cog", _fail_if_called("validate_cog"))
    monkeypatch.setattr(pipeline_module, "check_value_sanity", _fail_if_called("check_value_sanity"))

    path, status = _build(tmp_path)

    assert path is None
    assert status == "failed"
    staging_var = tmp_path / "staging" / "gfs" / "20260630_00z" / "tmp2m"
    assert not (staging_var / "fh000.val.cog.tif").exists()
    assert not (staging_var / "fh000.json").exists()
    assert not (staging_var / "grid").exists()


def test_binary_only_model_builds_good_frame_without_value_cog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CARTOSKY_BINARY_SAMPLING_MODELS", "gfs")
    _harness(monkeypatch, fetched=np.array([[32.0, 33.0], [34.0, 35.0]], dtype=np.float32))
    monkeypatch.setattr(pipeline_module, "write_value_cog", _fail_if_called("write_value_cog"))
    monkeypatch.setattr(pipeline_module, "validate_cog", _fail_if_called("validate_cog"))
    monkeypatch.setattr(pipeline_module, "check_value_sanity", _fail_if_called("check_value_sanity"))

    path, status = _build(tmp_path)

    assert status == "ok"
    assert path is not None
    staging_var = tmp_path / "staging" / "gfs" / "20260630_00z" / "tmp2m"
    # No value COG — the grid binary + sidecar are the complete artifact set.
    assert not (staging_var / "fh000.val.cog.tif").exists()
    assert (staging_var / "fh000.json").is_file()
    assert (staging_var / "grid" / "fh000.l0.u16.bin").is_file()
    assert (staging_var / "grid" / "fh000.l0.meta.json").is_file()


def test_non_allowlisted_model_gate_behavior_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Empty allowlist (default): the COG write and both COG gates all run, and
    # a FAILING pre-encode gate stays shadow-only — the build still succeeds.
    monkeypatch.delenv("CARTOSKY_BINARY_SAMPLING_MODELS", raising=False)
    _harness(monkeypatch, fetched=np.array([[32.0, 33.0], [34.0, 35.0]], dtype=np.float32))

    calls: list[str] = []
    monkeypatch.setattr(
        pipeline_module,
        "write_value_cog",
        lambda data, path, **kwargs: (calls.append("write_value_cog"), path.write_bytes(b"value"))[1],
    )
    monkeypatch.setattr(
        pipeline_module, "validate_cog", lambda *a, **k: (calls.append("validate_cog"), True)[1]
    )
    monkeypatch.setattr(
        pipeline_module, "check_value_sanity", lambda *a, **k: (calls.append("check_value_sanity"), True)[1]
    )
    monkeypatch.setattr(
        pipeline_module,
        "check_pre_encode_value_sanity",
        lambda *a, **k: (calls.append("pre_encode"), False)[1],
    )

    path, status = _build(tmp_path)

    assert status == "ok"
    assert path is not None
    assert calls == ["pre_encode", "write_value_cog", "validate_cog", "check_value_sanity"]
    staging_var = tmp_path / "staging" / "gfs" / "20260630_00z" / "tmp2m"
    assert (staging_var / "fh000.val.cog.tif").is_file()


# ── HRRR coverage ────────────────────────────────────────────────────────────
#
# Same enforced-gate guarantee as the GFS tests above, for the next model in
# the migration (Phase G). Two materially different rejection paths:
# tmp2m goes through the generic continuous branch of
# _check_value_array_sanity (flat field, max_nodata_ratio=0.95), while
# radar_ptype takes the is_categorical_ptype branch (real colormap spec is
# type="indexed" with ptype_breaks), whose threshold is max_nodata_ratio=0.998
# and whose only flat/dry allowance requires finite_count == 0. The GFS
# fixtures above are untouched; HRRR gets its own plugin/harness with each
# variable's real spec shape from HRRR_VARIABLE_CATALOG and the REAL colormap
# specs (get_color_map_spec is deliberately not mocked here — the categorical
# branch keys off the real radar_ptype spec's type + ptype_breaks).


class _HrrrPlugin(_Plugin):
    id = "hrrr"


def _hrrr_var_specs(var: str):
    """(var_spec_model, var_capability) mirroring the real HRRR catalog entry
    for `var` — HRRR_VARIABLE_CATALOG has tmp2m as a primary continuous fetch
    (units F, color_map_id "tmp2m") and radar_ptype as a derived discrete
    composite (derive="radar_ptype_combo", units dBZ, color_map_id
    "radar_ptype", frontend WITHOUT allow_dry_frame — that flag belongs to the
    radar_ptype_* components only)."""
    if var == "tmp2m":
        return (
            SimpleNamespace(
                id="tmp2m",
                derived=False,
                selectors=SimpleNamespace(hints={}, search=[":TMP:2 m above ground:"]),
                kind="continuous",
                units="F",
            ),
            SimpleNamespace(color_map_id="tmp2m", kind="continuous", units="F", frontend={}),
        )
    if var == "radar_ptype":
        return (
            SimpleNamespace(
                id="radar_ptype",
                derived=True,
                selectors=SimpleNamespace(hints={}, search=[]),
                kind="discrete",
                units="dBZ",
            ),
            SimpleNamespace(color_map_id="radar_ptype", kind="discrete", units="dBZ", frontend={}),
        )
    raise AssertionError(f"unexpected HRRR test var: {var}")


def _hrrr_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fetched: np.ndarray,
    var: str,
) -> None:
    """HRRR twin of `_harness`. Differences: per-variable var_spec/capability
    from `_hrrr_var_specs`, `derive_variable` mocked for the derived
    radar_ptype path (build_frame skips fetch/convert for derived vars), and
    the real `get_color_map_spec` left in place."""
    var_spec_model, var_capability = _hrrr_var_specs(var)

    monkeypatch.setattr(pipeline_module, "_ensure_products_ready", lambda **kwargs: None)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_spec", lambda *a, **k: var_spec_model)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_capability", lambda *a, **k: var_capability)
    monkeypatch.setattr(
        pipeline_module,
        "fetch_variable",
        lambda **kwargs: (fetched, "EPSG:4326", from_origin(-101.0, 46.0, 1.0, 1.0)),
    )
    monkeypatch.setattr(
        pipeline_module,
        "derive_variable",
        lambda **kwargs: (fetched, "EPSG:4326", from_origin(-101.0, 46.0, 1.0, 1.0)),
    )
    monkeypatch.setattr(pipeline_module, "convert_units", lambda data, **kwargs: data)
    monkeypatch.setattr(
        pipeline_module,
        "warp_to_target_grid",
        lambda data, src_crs, src_transform, **kwargs: (data, src_transform),
    )
    monkeypatch.setattr(
        pipeline_module,
        "float_to_rgba",
        lambda data, color_map_id, meta_var_key=None: (
            np.zeros((4, data.shape[0], data.shape[1]), dtype=np.uint8),
            {"kind": "continuous", "units": "F", "min": 0.0, "max": 100.0},
        ),
    )
    monkeypatch.setattr(pipeline_module, "grid_build_enabled", lambda: True)
    monkeypatch.setattr(pipeline_module, "_build_contour_metadata_for_variable", lambda **kwargs: ({}, None))


def _build_hrrr(tmp_path: Path, var: str):
    return pipeline_module.build_frame(
        model="hrrr",
        region="conus",
        var_id=var,
        fh=0,
        run_date=datetime(2026, 7, 2, 10, 0),
        data_root=tmp_path,
        product="sfc",
        model_plugin=_HrrrPlugin(),
        return_status=True,
    )


def test_binary_only_model_rejects_bad_frame_hrrr_tmp2m(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Same guarantee as the GFS test above, for hrrr: a flat constant field
    # fails the REAL pre-encode gate (min == max, no dry-frame allowance for
    # tmp2m) and must reject the frame with the COG write/gates never reached.
    # 32.0 F sits inside HRRR's real tmp2m packing band
    # (_PACKING_BY_MODEL_VAR[("hrrr", "tmp2m")]: scale=0.1, offset=-100.0) —
    # the rejection is the gate's doing, not an out-of-band encode artifact.
    monkeypatch.setenv("CARTOSKY_BINARY_SAMPLING_MODELS", "hrrr")
    _hrrr_harness(monkeypatch, fetched=np.full((2, 2), 32.0, dtype=np.float32), var="tmp2m")
    monkeypatch.setattr(pipeline_module, "write_value_cog", _fail_if_called("write_value_cog"))
    monkeypatch.setattr(pipeline_module, "validate_cog", _fail_if_called("validate_cog"))
    monkeypatch.setattr(pipeline_module, "check_value_sanity", _fail_if_called("check_value_sanity"))

    path, status = _build_hrrr(tmp_path, "tmp2m")

    assert path is None
    assert status == "failed"
    staging_var = tmp_path / "staging" / "hrrr" / "20260702_10z" / "tmp2m"
    assert not (staging_var / "fh000.val.cog.tif").exists()
    assert not (staging_var / "fh000.json").exists()
    assert not (staging_var / "grid").exists()


def test_binary_only_model_rejects_bad_frame_hrrr_radar_ptype(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # radar_ptype exercises the is_categorical_ptype branch of
    # _check_value_array_sanity, not the continuous one: the real "radar_ptype"
    # colormap spec is type="indexed" with ptype_breaks, so the nodata
    # threshold is the relaxed 0.998 (not 0.95) and the only fully-dry
    # allowance requires finite_count == 0. The bad array here is genuinely
    # bad under THAT branch: 1 finite pixel out of 2000 (nodata ratio 0.9995 >
    # 0.998) while NOT fully dry, so the dry-frame carve-out does not apply.
    from app.services.colormaps import RADAR_PTYPE_BREAKS

    rain = RADAR_PTYPE_BREAKS["rain"]
    rain_lo = float(rain["offset"])
    rain_hi = float(rain["offset"] + rain["count"] - 1)

    var_spec_model, var_capability = _hrrr_var_specs("radar_ptype")
    real_spec = pipeline_module.get_color_map_spec("radar_ptype")

    # Fixture-sharpness guard: a sparse-but-valid scene at 0.97 nodata (two
    # distinct in-range palette indices) PASSES the real gate — it would fail
    # the generic 0.95 threshold, so this pins the categorical branch (and its
    # 0.998 threshold) as the logic actually in play for this var_spec. If a
    # spec/branch change ever re-routes radar_ptype to the continuous branch,
    # this assertion fails loudly instead of the test below passing for the
    # wrong reason.
    sparse_valid = np.full((40, 50), np.nan, dtype=np.float32)
    sparse_valid.flat[:30] = rain_lo
    sparse_valid.flat[30:60] = rain_hi  # 60/2000 finite -> nodata ratio 0.97
    assert (
        pipeline_module.check_pre_encode_value_sanity(
            sparse_valid,
            real_spec,
            var_spec_model=var_spec_model,
            var_capability=var_capability,
            label="hrrr/radar_ptype categorical-branch pin",
        )
        is True
    )

    bad = np.full((40, 50), np.nan, dtype=np.float32)
    bad.flat[0] = rain_lo  # 1/2000 finite -> nodata ratio 0.9995, not fully dry

    monkeypatch.setenv("CARTOSKY_BINARY_SAMPLING_MODELS", "hrrr")
    _hrrr_harness(monkeypatch, fetched=bad, var="radar_ptype")
    monkeypatch.setattr(pipeline_module, "write_value_cog", _fail_if_called("write_value_cog"))
    monkeypatch.setattr(pipeline_module, "validate_cog", _fail_if_called("validate_cog"))
    monkeypatch.setattr(pipeline_module, "check_value_sanity", _fail_if_called("check_value_sanity"))

    path, status = _build_hrrr(tmp_path, "radar_ptype")

    assert path is None
    assert status == "failed"
    staging_var = tmp_path / "staging" / "hrrr" / "20260702_10z" / "radar_ptype"
    assert not (staging_var / "fh000.val.cog.tif").exists()
    assert not (staging_var / "fh000.json").exists()
    assert not (staging_var / "grid").exists()


# ── NBM coverage ─────────────────────────────────────────────────────────────
#
# Same enforced-gate guarantee for the third migration model (Phase G). NBM
# has no categorical/indexed variable in scope — all five packed variables
# are continuous — so only the generic branch of _check_value_array_sanity
# applies (flat field, max_nodata_ratio=0.95, no ptype carve-out) and one
# tmp2m rejection test proves the mechanism, matching the GFS precedent.
# NBM's tmp2m is a direct fetch (primary=True, derived=False in
# NBM_VARIABLE_CATALOG — unlike HRRR's derived radar_ptype), with
# color_map_id="tmp2m", kind="continuous", units="F".


class _NbmPlugin(_Plugin):
    id = "nbm"


def _nbm_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fetched: np.ndarray,
) -> None:
    """NBM twin of `_hrrr_harness`, tmp2m only: fetch-path mocks (NBM derives
    nothing in scope), var_spec/capability mirroring NBM_VARIABLE_CATALOG's
    real tmp2m entry, and the real `get_color_map_spec` left in place."""
    var_spec_model = SimpleNamespace(
        id="tmp2m",
        derived=False,
        selectors=SimpleNamespace(hints={}, search=[":TMP:2 m above ground:"]),
        kind="continuous",
        units="F",
    )
    var_capability = SimpleNamespace(color_map_id="tmp2m", kind="continuous", units="F", frontend={})

    monkeypatch.setattr(pipeline_module, "_ensure_products_ready", lambda **kwargs: None)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_spec", lambda *a, **k: var_spec_model)
    monkeypatch.setattr(pipeline_module, "_resolve_model_var_capability", lambda *a, **k: var_capability)
    monkeypatch.setattr(
        pipeline_module,
        "fetch_variable",
        lambda **kwargs: (fetched, "EPSG:4326", from_origin(-101.0, 46.0, 1.0, 1.0)),
    )
    monkeypatch.setattr(pipeline_module, "convert_units", lambda data, **kwargs: data)
    monkeypatch.setattr(
        pipeline_module,
        "warp_to_target_grid",
        lambda data, src_crs, src_transform, **kwargs: (data, src_transform),
    )
    monkeypatch.setattr(
        pipeline_module,
        "float_to_rgba",
        lambda data, color_map_id, meta_var_key=None: (
            np.zeros((4, data.shape[0], data.shape[1]), dtype=np.uint8),
            {"kind": "continuous", "units": "F", "min": 0.0, "max": 100.0},
        ),
    )
    monkeypatch.setattr(pipeline_module, "grid_build_enabled", lambda: True)
    monkeypatch.setattr(pipeline_module, "_build_contour_metadata_for_variable", lambda **kwargs: ({}, None))


def test_binary_only_model_rejects_bad_frame_nbm_tmp2m(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Same guarantee as the GFS/HRRR tests above, for nbm: a flat constant
    # field fails the REAL pre-encode gate (min == max, no dry-frame allowance
    # for tmp2m) and must reject the frame with the COG write/gates never
    # reached. 32.0 F sits inside NBM's real tmp2m packing band
    # (_PACKING_BY_MODEL_VAR[("nbm", "tmp2m")]: scale=0.1, offset=-100.0) —
    # the rejection is the gate's doing, not an out-of-band encode artifact.
    monkeypatch.setenv("CARTOSKY_BINARY_SAMPLING_MODELS", "nbm")
    _nbm_harness(monkeypatch, fetched=np.full((2, 2), 32.0, dtype=np.float32))
    monkeypatch.setattr(pipeline_module, "write_value_cog", _fail_if_called("write_value_cog"))
    monkeypatch.setattr(pipeline_module, "validate_cog", _fail_if_called("validate_cog"))
    monkeypatch.setattr(pipeline_module, "check_value_sanity", _fail_if_called("check_value_sanity"))

    path, status = pipeline_module.build_frame(
        model="nbm",
        region="conus",
        var_id="tmp2m",
        fh=0,
        run_date=datetime(2026, 7, 2, 0, 0),
        data_root=tmp_path,
        product="co",
        model_plugin=_NbmPlugin(),
        return_status=True,
    )

    assert path is None
    assert status == "failed"
    staging_var = tmp_path / "staging" / "nbm" / "20260702_00z" / "tmp2m"
    assert not (staging_var / "fh000.val.cog.tif").exists()
    assert not (staging_var / "fh000.json").exists()
    assert not (staging_var / "grid").exists()


def test_scheduler_frame_marker_is_grid_meta_for_binary_only_models(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The scheduler's "frame already built" marker (build frontier, available
    # counts, promotion readiness) is the staging value COG today. For a
    # binary-only model it must be the grid frame meta instead, or every frame
    # would look forever-missing after the cutover.
    from app.services import scheduler as scheduler_module

    run_id = "20260630_00z"
    staging_var = tmp_path / "staging" / "gfs" / run_id / "tmp2m"
    (staging_var / "grid").mkdir(parents=True)
    (staging_var / "fh000.json").write_text("{}")

    # Sidecar present but no grid meta and no COG: not built either way.
    monkeypatch.setenv("CARTOSKY_BINARY_SAMPLING_MODELS", "gfs")
    assert scheduler_module._frame_artifacts_exist(tmp_path, "gfs", run_id, "tmp2m", 0) is False

    # Grid meta appears: built for the binary-only model...
    (staging_var / "grid" / "fh000.l0.meta.json").write_text("{}")
    assert scheduler_module._frame_artifacts_exist(tmp_path, "gfs", run_id, "tmp2m", 0) is True

    # ...but with the allowlist empty the marker is still the value COG.
    monkeypatch.delenv("CARTOSKY_BINARY_SAMPLING_MODELS")
    assert scheduler_module._frame_artifacts_exist(tmp_path, "gfs", run_id, "tmp2m", 0) is False
    (staging_var / "fh000.val.cog.tif").write_bytes(b"value")
    assert scheduler_module._frame_artifacts_exist(tmp_path, "gfs", run_id, "tmp2m", 0) is True


def test_frame_has_cog_reports_binary_frame_for_allowlisted_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The frames API's `has_cog` flag means "a hover-samplable frame exists".
    # For a binary-only model that must reflect the published grid binary.
    from app import main as main_module

    run_id = "20260630_00z"
    run_root = tmp_path / "published" / "gfs" / run_id
    write_grid_frame_for_run_root(
        run_root=run_root,
        model="gfs",
        var="tmp2m",
        fh=0,
        values=np.array([[32.0, 33.0], [34.0, 35.0]], dtype=np.float32),
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        projection="EPSG:4326",
    )
    manifests_root = tmp_path / "manifests" / "gfs"
    manifests_root.mkdir(parents=True)
    (manifests_root / f"{run_id}.json").write_text("{}")
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", tmp_path / "published")
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", tmp_path / "manifests")
    main_module._manifest_cache.clear()

    # Only the grid binary is published (no value COG, as post-cutover).
    monkeypatch.setenv("CARTOSKY_BINARY_SAMPLING_MODELS", "gfs")
    assert main_module._frame_has_cog("gfs", run_id, "tmp2m", 0) is True
    monkeypatch.delenv("CARTOSKY_BINARY_SAMPLING_MODELS")
    assert main_module._frame_has_cog("gfs", run_id, "tmp2m", 0) is False
