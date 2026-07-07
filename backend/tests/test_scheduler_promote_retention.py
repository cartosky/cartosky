from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import scheduler as scheduler_module


def _make_staging_run(data_root: Path, model: str, run_id: str, files: dict[str, str]) -> Path:
    run_dir = data_root / "staging" / model / run_id
    for rel, content in files.items():
        path = run_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return run_dir


def test_promote_run_first_publish(tmp_path: Path) -> None:
    _make_staging_run(tmp_path, "gfs", "20260707_12z", {"tmp2m/fh001.json": "a"})

    scheduler_module._promote_run(tmp_path, "gfs", "20260707_12z")

    published_run = tmp_path / "published" / "gfs" / "20260707_12z"
    assert (published_run / "tmp2m" / "fh001.json").read_text() == "a"
    leftovers = [p.name for p in published_run.parent.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_promote_run_replaces_existing_run_via_rename_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The live published run dir must be renamed aside, never rmtree'd in
    place — an in-place rmtree opened a 404 window on every progress publish."""
    model, run_id = "gfs", "20260707_12z"
    _make_staging_run(tmp_path, model, run_id, {"tmp2m/fh001.json": "new"})
    published_run = tmp_path / "published" / model / run_id
    (published_run / "tmp2m").mkdir(parents=True)
    (published_run / "tmp2m" / "fh000.json").write_text("old-only")
    (published_run / "tmp2m" / "fh001.json").write_text("old")

    rmtree_targets: list[Path] = []
    real_rmtree = scheduler_module.shutil.rmtree

    def _recording_rmtree(path, *args, **kwargs):
        rmtree_targets.append(Path(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(scheduler_module.shutil, "rmtree", _recording_rmtree)

    scheduler_module._promote_run(tmp_path, model, run_id)

    # Staging content overlays; frames present only in the prior publish survive.
    assert (published_run / "tmp2m" / "fh001.json").read_text() == "new"
    assert (published_run / "tmp2m" / "fh000.json").read_text() == "old-only"
    assert published_run not in rmtree_targets
    leftovers = [p.name for p in published_run.parent.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_promote_run_restores_previous_run_when_swap_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model, run_id = "gfs", "20260707_12z"
    _make_staging_run(tmp_path, model, run_id, {"tmp2m/fh001.json": "new"})
    published_run = tmp_path / "published" / model / run_id
    (published_run / "tmp2m").mkdir(parents=True)
    (published_run / "tmp2m" / "fh001.json").write_text("old")

    real_rename = scheduler_module.os.rename

    def _failing_rename(src, dst):
        if Path(src).name == f".{run_id}.tmp":
            raise OSError("simulated rename failure")
        return real_rename(src, dst)

    monkeypatch.setattr(scheduler_module.os, "rename", _failing_rename)

    with pytest.raises(OSError, match="simulated rename failure"):
        scheduler_module._promote_run(tmp_path, model, run_id)

    assert (published_run / "tmp2m" / "fh001.json").read_text() == "old"


def test_enforce_manifest_retention_prunes_only_old_run_manifests(tmp_path: Path) -> None:
    root = tmp_path / "manifests" / "gfs"
    root.mkdir(parents=True)
    for run in ("20260707_00z", "20260707_06z", "20260707_12z"):
        (root / f"{run}.json").write_text("{}")
    (root / "LATEST.json").write_text("{}")
    (root / "notes.txt").write_text("keep")

    scheduler_module._enforce_manifest_retention(root, keep_runs=2)

    remaining = sorted(p.name for p in root.iterdir())
    assert remaining == ["20260707_06z.json", "20260707_12z.json", "LATEST.json", "notes.txt"]


def test_enforce_manifest_retention_noops_below_keep_count(tmp_path: Path) -> None:
    root = tmp_path / "manifests" / "gfs"
    root.mkdir(parents=True)
    (root / "20260707_12z.json").write_text("{}")

    scheduler_module._enforce_manifest_retention(root, keep_runs=3)
    scheduler_module._enforce_manifest_retention(tmp_path / "manifests" / "missing-model", keep_runs=3)

    assert sorted(p.name for p in root.iterdir()) == ["20260707_12z.json"]
