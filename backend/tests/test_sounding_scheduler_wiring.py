"""Scheduler wiring for sounding stacks (Skew-T design §7 Phase 1).

The point of these tests is the *gate*: with CARTOSKY_SOUNDING_MODELS unset,
the scheduler must behave exactly as it did before Phase 1 — no stack builds,
no new manifest key.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import scheduler as scheduler_module
from app.services import sounding

MODEL = "hrrr"
RUN_ID = "20260730_12z"
RUN_DT = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
VAR_ID = "tmp2m"


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARTOSKY_SOUNDING_MODELS", raising=False)


def _publish_frames(data_root: Path, fhs: list[int], *, var_id: str = VAR_ID) -> None:
    for fh in fhs:
        for path in (
            scheduler_module._frame_sidecar_path(data_root, MODEL, RUN_ID, var_id, fh),
            scheduler_module._frame_primary_artifact_path(data_root, MODEL, RUN_ID, var_id, fh),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"fh": fh, "units": "F", "kind": "continuous"}))


def _write_stack(run_root: Path, fh: int) -> None:
    planes = {
        plane: np.zeros((2, 2), dtype=np.float32) for plane in range(sounding.N_PLANES)
    }
    planes[sounding.surface_plane_index(0)] = np.full((2, 2), 1000.0, dtype=np.float32)
    sounding.write_stack(
        run_root=run_root,
        fh=fh,
        stack=sounding.assemble_stack(planes, stride=1),
        sidecar=sounding.build_sidecar(
            model=MODEL,
            run_id=RUN_ID,
            fh=fh,
            run_date=RUN_DT,
            width=2,
            height=2,
            transform=(1.0, 0.0, 0.0, 0.0, -1.0, 0.0),
            projection="EPSG:4326",
            source_width=2,
            source_height=2,
            stride=1,
        ),
    )


# ---------------------------------------------------------------------------
# Flag gate
# ---------------------------------------------------------------------------


def test_sounding_is_disabled_by_default() -> None:
    assert scheduler_module._sounding_enabled(MODEL) is False


def test_sounding_enabled_when_the_flag_lists_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", "hrrr")
    assert scheduler_module._sounding_enabled(MODEL) is True
    assert scheduler_module._sounding_enabled("HRRR") is True


def test_flagged_but_unsupported_model_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", "gfs,nam")
    assert scheduler_module._sounding_enabled("gfs") is False
    assert scheduler_module._sounding_enabled("nam") is False


def test_sounding_pass_is_a_no_op_when_the_flag_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _explode(**_kwargs):  # pragma: no cover - must never run
        raise AssertionError("sounding build attempted with the flag off")

    monkeypatch.setattr(sounding, "build_stacks_for_run", _explode)
    _publish_frames(tmp_path, [0, 1])

    assert scheduler_module._run_sounding_pass(
        data_root=tmp_path,
        model_id=MODEL,
        run_id=RUN_ID,
        run_dt=RUN_DT,
        region="conus",
        primary_vars=[VAR_ID],
        candidate_fhs=[0, 1],
        reason="test",
    ) == (0, 0)


# ---------------------------------------------------------------------------
# Frontier gating / idempotence
# ---------------------------------------------------------------------------


def test_complete_fhs_only_counts_hours_whose_primary_frames_all_exist(tmp_path: Path) -> None:
    _publish_frames(tmp_path, [0, 1, 2], var_id="tmp2m")
    _publish_frames(tmp_path, [0, 1], var_id="refc")

    assert scheduler_module._sounding_complete_fhs(
        data_root=tmp_path,
        model_id=MODEL,
        run_id=RUN_ID,
        region="conus",
        primary_vars=["tmp2m", "refc"],
        candidate_fhs=[0, 1, 2, 3],
    ) == [0, 1]


def test_sounding_pass_builds_only_complete_and_missing_fhs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", "hrrr")
    _publish_frames(tmp_path, [0, 1, 2])

    run_root = scheduler_module._staging_run_root(tmp_path, MODEL, RUN_ID, "conus")
    _write_stack(run_root, 0)  # already built; must be skipped

    requested: list[list[int]] = []

    def _fake_build(*, model_id, run_id, run_date, run_root, fhs):
        fhs = sorted(fhs)
        requested.append(fhs)
        for fh in fhs:
            _write_stack(run_root, fh)
        return len(fhs), 0

    monkeypatch.setattr(sounding, "build_stacks_for_run", _fake_build)

    built, failed = scheduler_module._run_sounding_pass(
        data_root=tmp_path,
        model_id=MODEL,
        run_id=RUN_ID,
        run_dt=RUN_DT,
        region="conus",
        primary_vars=[VAR_ID],
        candidate_fhs=[0, 1, 2, 5],
        reason="test",
    )

    assert requested == [[1, 2]]
    assert (built, failed) == (2, 0)

    # Second pass has nothing left to do.
    requested.clear()
    assert scheduler_module._run_sounding_pass(
        data_root=tmp_path,
        model_id=MODEL,
        run_id=RUN_ID,
        run_dt=RUN_DT,
        region="conus",
        primary_vars=[VAR_ID],
        candidate_fhs=[0, 1, 2, 5],
        reason="test",
    ) == (0, 0)
    assert requested == []


def test_sounding_pass_swallows_builder_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", "hrrr")
    _publish_frames(tmp_path, [0])

    def _boom(**_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(sounding, "build_stacks_for_run", _boom)

    assert scheduler_module._run_sounding_pass(
        data_root=tmp_path,
        model_id=MODEL,
        run_id=RUN_ID,
        run_dt=RUN_DT,
        region="conus",
        primary_vars=[VAR_ID],
        candidate_fhs=[0],
        reason="test",
    ) == (0, 0)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _manifest(tmp_path: Path) -> dict:
    scheduler_module._write_run_manifest(
        data_root=tmp_path,
        model=MODEL,
        run_id=RUN_ID,
        targets=[(VAR_ID, fh) for fh in (0, 1, 2)],
        plugin=None,
    )
    manifest_path = tmp_path / "manifests" / MODEL / f"{RUN_ID}.json"
    return json.loads(manifest_path.read_text())


def test_manifest_has_no_sounding_key_when_the_flag_is_off(tmp_path: Path) -> None:
    _publish_frames(tmp_path, [0, 1, 2])
    payload = _manifest(tmp_path)
    assert "sounding" not in payload
    assert set(payload) == {
        "contract_version",
        "model",
        "run",
        "region",
        "variables",
        "last_updated",
    }


def test_manifest_sounding_section_is_top_level_and_additive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", "hrrr")
    _publish_frames(tmp_path, [0, 1, 2])
    run_root = scheduler_module._staging_run_root(tmp_path, MODEL, RUN_ID, "conus")
    _write_stack(run_root, 0)
    _write_stack(run_root, 2)

    payload = _manifest(tmp_path)

    assert payload["contract_version"] == "3.0"
    assert VAR_ID in payload["variables"]
    # Never inside `variables` — raster machinery must not see this artifact.
    assert "sounding" not in payload["variables"]

    section = payload["sounding"]
    assert section["format_version"] == sounding.SOUNDING_FORMAT_VERSION
    assert section["expected_fhs"] == [0, 1, 2]
    assert section["available_fhs"] == [0, 2]
    assert section["path_template"] == "sounding/fh{fh:03d}.stack.bin"
