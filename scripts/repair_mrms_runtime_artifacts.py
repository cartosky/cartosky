from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.grid import grid_manifest_path, grid_supported
from app.services.mrms_publish import MRMS_MODEL_ID, finalize_mrms_published_run


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair missing MRMS runtime grid artifacts for published runs.")
    parser.add_argument(
        "--data-root",
        default=str(REPO_ROOT / "data"),
        help="CartoSky data root containing published/ and manifests/",
    )
    parser.add_argument(
        "--run-id",
        action="append",
        default=[],
        help="Specific MRMS run ID to repair. Repeat to repair multiple runs.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan all published MRMS runs and repair only those with pending or missing runtime artifacts.",
    )
    return parser.parse_args(argv)


def _candidate_run_ids(data_root: Path, requested_run_ids: list[str], scan_all: bool) -> list[str]:
    if requested_run_ids:
        return sorted({run_id.strip() for run_id in requested_run_ids if run_id.strip()})
    if not scan_all:
        raise SystemExit("Specify at least one --run-id or pass --all.")

    manifests_dir = data_root / "manifests" / MRMS_MODEL_ID
    if not manifests_dir.is_dir():
        return []
    return sorted(path.stem for path in manifests_dir.glob("*.json"))


def _run_needs_repair(data_root: Path, run_id: str) -> bool:
    manifest_path = data_root / "manifests" / MRMS_MODEL_ID / f"{run_id}.json"
    if not manifest_path.is_file():
        return False

    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False

    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    if metadata.get("runtime_artifacts_pending") is True:
        return True

    variables = manifest.get("variables") if isinstance(manifest.get("variables"), dict) else {}
    for variable_id in variables.keys():
        if not grid_supported(MRMS_MODEL_ID, str(variable_id)):
            continue
        manifest_file = grid_manifest_path(data_root, MRMS_MODEL_ID, run_id, str(variable_id))
        if not manifest_file.is_file():
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    data_root = Path(args.data_root).resolve()
    run_ids = _candidate_run_ids(data_root, list(args.run_id), bool(args.all))

    repaired = 0
    skipped = 0
    for run_id in run_ids:
        if not _run_needs_repair(data_root, run_id):
            skipped += 1
            continue
        finalize_mrms_published_run(data_root=data_root, run_id=run_id, build_grid_artifacts=True)
        repaired += 1
        print(f"repaired {run_id}")

    print(f"mrms runtime artifact repair complete repaired={repaired} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())