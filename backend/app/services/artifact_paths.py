from __future__ import annotations

from pathlib import Path


LEGACY_REGION_ID = "conus"


def normalize_region_id(region: str | None) -> str | None:
    normalized = str(region or "").strip().lower()
    return normalized or None


def run_root(base_root: Path, model: str, run_id: str, *, region: str | None = None) -> Path:
    root = Path(base_root) / str(model).strip() / str(run_id).strip()
    normalized_region = normalize_region_id(region)
    if normalized_region is None:
        return root
    return root / normalized_region


def run_root_candidates(base_root: Path, model: str, run_id: str, *, region: str | None = None) -> tuple[Path, ...]:
    root = Path(base_root) / str(model).strip() / str(run_id).strip()
    normalized_region = normalize_region_id(region)
    if normalized_region is None:
        return (root / LEGACY_REGION_ID, root)
    if normalized_region == LEGACY_REGION_ID:
        return (root / normalized_region, root)
    return (root / normalized_region,)


def resolve_existing_run_root(base_root: Path, model: str, run_id: str, *, region: str | None = None) -> Path | None:
    for candidate in run_root_candidates(base_root, model, run_id, region=region):
        if candidate.is_dir():
            return candidate
    return None


def var_dir(base_root: Path, model: str, run_id: str, var_id: str, *, region: str | None = None) -> Path:
    return run_root(base_root, model, run_id, region=region) / str(var_id).strip()


def resolve_existing_var_dir(base_root: Path, model: str, run_id: str, var_id: str, *, region: str | None = None) -> Path | None:
    for candidate_root in run_root_candidates(base_root, model, run_id, region=region):
        candidate = candidate_root / str(var_id).strip()
        if candidate.is_dir():
            return candidate
    return None
