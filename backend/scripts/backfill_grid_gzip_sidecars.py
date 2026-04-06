#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import os
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.grid import ensure_grid_brotli_sidecar, ensure_grid_gzip_sidecar


def _iter_grid_frames(data_root: Path, *, model: str | None, run: str | None, var: str | None) -> list[Path]:
    published_root = data_root / "published"
    if not published_root.is_dir():
        return []

    model_dirs = [published_root / model] if model else [path for path in published_root.iterdir() if path.is_dir()]
    frame_paths: list[Path] = []
    for model_dir in sorted(model_dirs):
        if not model_dir.is_dir():
            continue
        run_dirs = [model_dir / run] if run else [path for path in model_dir.iterdir() if path.is_dir()]
        for run_dir in sorted(run_dirs):
            if not run_dir.is_dir():
                continue
            var_dirs = [run_dir / var] if var else [path for path in run_dir.iterdir() if path.is_dir()]
            for var_dir in sorted(var_dirs):
                grid_dir = var_dir / "grid"
                if not grid_dir.is_dir():
                    continue
                frame_paths.extend(sorted(path for path in grid_dir.glob("fh*.bin") if path.is_file()))
    return frame_paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill .gz and .br sidecars for published grid artifacts.")
    parser.add_argument("--data-root", default=os.getenv("CARTOSKY_DATA_ROOT", "./data"), help="CartoSky data root")
    parser.add_argument("--model", default=None, help="Optional model filter")
    parser.add_argument("--run", default=None, help="Optional run filter")
    parser.add_argument("--var", default=None, help="Optional variable filter")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="Compression worker count")
    parser.add_argument("--force", action="store_true", help="Rewrite sidecars even if they already exist")
    args = parser.parse_args()

    data_root = Path(args.data_root).expanduser().resolve()
    frame_paths = _iter_grid_frames(data_root, model=args.model, run=args.run, var=args.var)
    if not frame_paths:
        print("No grid frame artifacts found.")
        return 0

    completed = 0
    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = {}
        for frame_path in frame_paths:
            futures[pool.submit(ensure_grid_gzip_sidecar, frame_path, force=bool(args.force))] = frame_path
            futures[pool.submit(ensure_grid_brotli_sidecar, frame_path, force=bool(args.force))] = frame_path
        for future in concurrent.futures.as_completed(futures):
            frame_path = futures[future]
            try:
                future.result()
            except Exception as exc:
                failed += 1
                print(f"FAILED {frame_path}: {exc}", file=sys.stderr)
                continue
            completed += 1

    print(f"Completed {completed} sidecar writes across {len(frame_paths)} grid frames.")
    if failed:
        print(f"Failures: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
