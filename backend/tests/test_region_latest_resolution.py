from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import main as main_module


def _write_manifest(manifests_root: Path, model: str, run_id: str, *, region: str = "na") -> None:
    path = manifests_root / model / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "contract_version": "3.0",
                "model": model,
                "run": run_id,
                "region": region,
                "variables": {
                    "tmp2m": {
                        "display_name": "Surface Temp",
                        "kind": "continuous",
                        "units": "F",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [{"fh": 0, "valid_time": "2026-04-06T12:00:00Z"}],
                    }
                },
            }
        )
    )


def _clear_main_caches() -> None:
    main_module._manifest_cache.clear()
    main_module._sidecar_cache.clear()
    main_module._grid_manifest_cache.clear()


@pytest.fixture
def isolated_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    published_root = data_root / "published"
    manifests_root = data_root / "manifests"
    monkeypatch.setattr(main_module, "DATA_ROOT", data_root)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", published_root)
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    _clear_main_caches()
    return published_root, manifests_root


def test_bootstrap_selection_uses_single_latest_pointer_with_region_presets(
    isolated_roots: tuple[Path, Path],
) -> None:
    published_root, manifests_root = isolated_roots
    model = "gfs"
    run_id = "20260406_12z"

    (published_root / model / run_id / "tmp2m").mkdir(parents=True, exist_ok=True)
    _write_manifest(manifests_root, model, run_id, region="na")
    (published_root / model / "LATEST.json").write_text(json.dumps({"run_id": run_id}))

    selection = main_module._bootstrap_selection_state(
        model=model,
        run="latest",
        var="tmp2m",
        ensemble_view=None,
        region="conus",
        capabilities_by_model=main_module.list_model_capabilities(),
    )

    assert selection["selected_region"] == "conus"
    assert selection["selected_run"] == run_id
    assert selection["selected_var"] == "tmp2m"


def test_resolve_sidecar_uses_canonical_published_var_dir(
    isolated_roots: tuple[Path, Path],
) -> None:
    published_root, manifests_root = isolated_roots
    model = "gfs"
    run_id = "20260406_12z"
    var_dir = published_root / model / run_id / "tmp2m"
    var_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(manifests_root, model, run_id, region="na")

    (var_dir / "fh000.json").write_text(json.dumps({"fh": 0, "source_region": "na"}))

    sidecar = main_module._resolve_sidecar(model, run_id, "tmp2m", 0, region="conus")

    assert sidecar is not None
    assert sidecar["source_region"] == "na"
