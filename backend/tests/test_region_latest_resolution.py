from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app import main as main_module


def _write_manifest(manifests_root: Path, model: str, run_id: str, *, region: str | None = None) -> None:
    suffix = "" if not region or region == "conus" else f".{region}"
    path = manifests_root / model / f"{run_id}{suffix}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "contract_version": "3.0",
                "model": model,
                "run": run_id,
                "region": region or "conus",
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


def test_bootstrap_selection_uses_region_specific_latest_pointer(
    isolated_roots: tuple[Path, Path],
) -> None:
    published_root, manifests_root = isolated_roots
    model = "gfs"
    conus_run = "20260406_06z"
    na_run = "20260406_12z"

    (published_root / model / conus_run / "conus" / "tmp2m").mkdir(parents=True, exist_ok=True)
    (published_root / model / na_run / "na" / "tmp2m").mkdir(parents=True, exist_ok=True)
    _write_manifest(manifests_root, model, conus_run)
    _write_manifest(manifests_root, model, na_run, region="na")

    (published_root / model / "LATEST.json").write_text(json.dumps({"run_id": conus_run}))
    (published_root / model / "LATEST.na.json").write_text(json.dumps({"run_id": na_run}))

    selection = main_module._bootstrap_selection_state(
        model=model,
        run="latest",
        var="tmp2m",
        ensemble_view=None,
        region="na",
        capabilities_by_model=main_module.list_model_capabilities(),
    )

    assert selection["selected_region"] == "na"
    assert selection["selected_run"] == na_run
    assert selection["selected_var"] == "tmp2m"


def test_resolve_sidecar_prefers_requested_region_when_both_exist(
    isolated_roots: tuple[Path, Path],
) -> None:
    published_root, manifests_root = isolated_roots
    model = "gfs"
    run_id = "20260406_12z"
    conus_dir = published_root / model / run_id / "conus" / "tmp2m"
    na_dir = published_root / model / run_id / "na" / "tmp2m"
    conus_dir.mkdir(parents=True, exist_ok=True)
    na_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(manifests_root, model, run_id)
    _write_manifest(manifests_root, model, run_id, region="na")

    (conus_dir / "fh000.json").write_text(json.dumps({"fh": 0, "source_region": "conus"}))
    (na_dir / "fh000.json").write_text(json.dumps({"fh": 0, "source_region": "na"}))

    sidecar = main_module._resolve_sidecar(model, run_id, "tmp2m", 0, region="na")

    assert sidecar is not None
    assert sidecar["source_region"] == "na"