from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import publish_utils


def _make_staging_run(data_root: Path, model: str, run_id: str, files: dict[str, str]) -> Path:
    run_dir = data_root / "staging" / model / run_id
    for rel, content in files.items():
        path = run_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return run_dir


def test_promote_run_first_publish(tmp_path: Path) -> None:
    _make_staging_run(tmp_path, "mrms", "20260707_12z", {"reflectivity/fh000.json": "a"})

    publish_utils.promote_run(data_root=tmp_path, model="mrms", run_id="20260707_12z")

    published_run = tmp_path / "published" / "mrms" / "20260707_12z"
    assert (published_run / "reflectivity" / "fh000.json").read_text() == "a"
    leftovers = [p.name for p in published_run.parent.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_promote_run_replaces_existing_run_via_rename_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observed republish must rename the live run aside, never rmtree it in place.

    An in-place rmtree deletes the only published copy before the replacement is
    visible, so a crash or failed move leaves the run unpublished and opens a
    404 window for every reader during the delete.
    """
    model, run_id = "mrms", "20260707_12z"
    _make_staging_run(tmp_path, model, run_id, {"reflectivity/fh000.json": "new"})
    published_run = tmp_path / "published" / model / run_id
    (published_run / "reflectivity").mkdir(parents=True)
    (published_run / "reflectivity" / "fh000.json").write_text("old")

    rmtree_targets: list[Path] = []
    real_rmtree = publish_utils.shutil.rmtree

    def _recording_rmtree(path, *args, **kwargs):
        rmtree_targets.append(Path(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(publish_utils.shutil, "rmtree", _recording_rmtree)

    publish_utils.promote_run(data_root=tmp_path, model=model, run_id=run_id)

    assert (published_run / "reflectivity" / "fh000.json").read_text() == "new"
    assert published_run not in rmtree_targets
    leftovers = [p.name for p in published_run.parent.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_promote_run_restores_previous_run_when_swap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, run_id = "mrms", "20260707_12z"
    _make_staging_run(tmp_path, model, run_id, {"reflectivity/fh000.json": "new"})
    published_run = tmp_path / "published" / model / run_id
    (published_run / "reflectivity").mkdir(parents=True)
    (published_run / "reflectivity" / "fh000.json").write_text("old")

    real_rename = os.rename

    def _failing_rename(src, dst):
        if Path(src).name == f".{run_id}.tmp":
            raise OSError("simulated rename failure")
        return real_rename(src, dst)

    monkeypatch.setattr(publish_utils.os, "rename", _failing_rename)

    with pytest.raises(OSError, match="simulated rename failure"):
        publish_utils.promote_run(data_root=tmp_path, model=model, run_id=run_id)

    assert (published_run / "reflectivity" / "fh000.json").read_text() == "old"


def test_promote_run_recovers_trash_from_interrupted_prior_swap(tmp_path: Path) -> None:
    model, run_id = "mrms", "20260707_12z"
    _make_staging_run(tmp_path, model, run_id, {"reflectivity/fh000.json": "new"})
    published_model = tmp_path / "published" / model
    trash_run = published_model / f".{run_id}.trash"
    (trash_run / "reflectivity").mkdir(parents=True)
    (trash_run / "reflectivity" / "fh000.json").write_text("old-from-trash")

    publish_utils.promote_run(data_root=tmp_path, model=model, run_id=run_id)

    published_run = published_model / run_id
    assert (published_run / "reflectivity" / "fh000.json").read_text() == "new"
    leftovers = [p.name for p in published_model.iterdir() if p.name.startswith(".")]
    assert leftovers == []
