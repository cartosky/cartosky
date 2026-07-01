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
