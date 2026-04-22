from __future__ import annotations

from pathlib import Path

from app.services.artifact_paths import resolve_existing_run_root, resolve_existing_var_dir, run_root, var_dir


def test_regioned_run_and_var_paths_round_trip() -> None:
    base_root = Path("/tmp/cartosky-artifacts")

    assert run_root(base_root, "gfs", "20260421_12z", region="na") == base_root / "gfs" / "20260421_12z" / "na"
    assert var_dir(base_root, "gfs", "20260421_12z", "tmp2m", region="na") == (
        base_root / "gfs" / "20260421_12z" / "na" / "tmp2m"
    )


def test_resolvers_support_legacy_and_regioned_layouts(tmp_path: Path) -> None:
    published_root = tmp_path / "published"
    legacy_var = published_root / "gfs" / "20260421_12z" / "tmp2m"
    legacy_var.mkdir(parents=True, exist_ok=True)

    regioned_var = published_root / "gfs" / "20260422_12z" / "conus" / "tmp2m"
    regioned_var.mkdir(parents=True, exist_ok=True)

    assert resolve_existing_run_root(published_root, "gfs", "20260421_12z") == published_root / "gfs" / "20260421_12z"
    assert resolve_existing_var_dir(published_root, "gfs", "20260421_12z", "tmp2m") == legacy_var

    assert resolve_existing_run_root(published_root, "gfs", "20260422_12z") == published_root / "gfs" / "20260422_12z" / "conus"
    assert resolve_existing_var_dir(published_root, "gfs", "20260422_12z", "tmp2m") == regioned_var
