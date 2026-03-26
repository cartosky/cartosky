#!/usr/bin/env python3
"""Generate loop playback video assets from prebuilt loop WebP frames.

Usage:
    PYTHONPATH=backend .venv/bin/python backend/scripts/generate_loop_playback.py \
      --model hrrr --run 20260325_15z --var tmp2m --format webm
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def _env_value(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.environ.get(name)
        if raw:
            return raw
    return default


DATA_ROOT = Path(_env_value("CARTOSKY_DATA_ROOT", "CARTOSKY_V3_DATA_ROOT", "TWF_V3_DATA_ROOT", default="./data"))
LOOP_CACHE_ROOT = Path(
    _env_value(
        "CARTOSKY_LOOP_CACHE_ROOT",
        "CARTOSKY_V3_LOOP_CACHE_ROOT",
        "TWF_V3_LOOP_CACHE_ROOT",
        default=str(DATA_ROOT / "loop_cache"),
    )
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate loop playback videos from loop WebP frames")
    parser.add_argument("--model", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--var", dest="variable", required=True)
    parser.add_argument("--format", choices=("webm", "mp4"), default="webm")
    parser.add_argument("--fps", type=int, default=4)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ffmpeg_bin = shutil.which(args.ffmpeg_bin)
    if not ffmpeg_bin:
        raise SystemExit("ffmpeg not found in PATH; install ffmpeg or pass --ffmpeg-bin")

    tier0_dir = LOOP_CACHE_ROOT / args.model / args.run / args.variable / "tier0"
    if not tier0_dir.is_dir():
        raise SystemExit(f"Loop tier0 directory not found: {tier0_dir}")

    frame_pattern = tier0_dir / "fh%03d.loop.webp"
    output_path = LOOP_CACHE_ROOT / args.model / args.run / args.variable / f"playback.{args.format}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not args.overwrite:
        raise SystemExit(f"Output already exists: {output_path} (pass --overwrite to replace)")

    if args.format == "webm":
        codec_args = [
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuva420p",
            "-b:v", "0",
            "-crf", "28",
            "-row-mt", "1",
        ]
    else:
        codec_args = [
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",
            "-crf", "22",
            "-movflags", "+faststart",
        ]

    cmd = [
        ffmpeg_bin,
        "-y" if args.overwrite else "-n",
        "-framerate", str(max(1, args.fps)),
        "-i", str(frame_pattern),
        *codec_args,
        str(output_path),
    ]
    subprocess.run(cmd, check=True)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
