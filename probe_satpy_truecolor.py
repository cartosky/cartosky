#!/usr/bin/env python3
"""Probe SatPy True Color RGB composite from GOES-19 ABI L1b data.

Investigation helper — not production code. Downloads one set of ABI L1b
scans for Bands 1, 2, and 3, runs SatPy's native_color composite (a close
approximation to true color using available bands), and saves the output as
a WebP image for visual inspection.

Run from the repo root:
    python probe_satpy_truecolor.py

Or with custom args:
    python probe_satpy_truecolor.py \
        --attime 2026-06-16T18:00:00 \
        --output-dir /tmp/satpy-probe
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_ATTIME = "2026-06-16T18:00:00"
OUTPUT_DIR = Path("/tmp/cartosky-satpy-probe")


def _fetch_l1b_band(
    *,
    band: int,
    attime: datetime,
    save_dir: Path,
    satellite: str = "goes19",
    bucket: str = "noaa-goes19",
    sector: str = "C",
) -> Path:
    """Fetch the nearest ABI-L1b-RadC file for a given band from S3."""
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))
    product = f"ABI-L1b-Rad{sector}"
    band_str = f"C{band:02d}"

    # Search the hour containing attime and the hour before
    found = None
    for hour_offset in range(3):
        dt = attime - timedelta(hours=hour_offset)
        prefix = f"{product}/{dt:%Y}/{dt:%j}/{dt:%H}/"
        print(f"  Listing s3://{bucket}/{prefix}")
        paginator = s3.get_paginator("list_objects_v2")
        candidates = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                filename = Path(key).name
                if f"-M6{band_str}_" in filename and filename.endswith(".nc"):
                    # Parse scan start time from filename
                    try:
                        # OR_ABI-L1b-RadC-M6C01_G19_s20261671801191_...
                        parts = filename.split("_")
                        s_token = next(p for p in parts if p.startswith("s"))
                        year = int(s_token[1:5])
                        doy = int(s_token[5:8])
                        hour = int(s_token[8:10])
                        minute = int(s_token[10:12])
                        second = int(s_token[12:14])
                        scan_dt = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(
                            days=doy - 1, hours=hour, minutes=minute, seconds=second
                        )
                        age_minutes = abs((attime.replace(tzinfo=timezone.utc) - scan_dt).total_seconds() / 60)
                        candidates.append((age_minutes, key, obj["Size"], filename))
                    except Exception:
                        continue
        if candidates:
            candidates.sort()
            age_minutes, key, size_bytes, filename = candidates[0]
            print(f"  Found Band {band}: {filename} (age={age_minutes:.1f}m, size={size_bytes/1e6:.1f}MB)")
            found = (key, filename, size_bytes)
            break

    if not found:
        raise RuntimeError(f"No L1b file found for Band {band} near {attime.isoformat()}")

    key, filename, size_bytes = found
    save_dir.mkdir(parents=True, exist_ok=True)
    dest = save_dir / filename
    if dest.exists() and dest.stat().st_size == size_bytes:
        print(f"  Band {band}: already cached at {dest}")
        return dest

    print(f"  Downloading Band {band} ({size_bytes/1e6:.1f}MB)...")
    s3.download_file(bucket, key, str(dest))
    print(f"  Band {band}: saved to {dest}")
    return dest


def _run_satpy_composite(
    *,
    band1_path: Path,
    band2_path: Path,
    band3_path: Path,
    output_dir: Path,
    composite_name: str = "true_color",
) -> dict[str, Any]:
    """Run SatPy composite and save output as WebP."""
    from satpy import Scene
    from satpy.writers import get_enhanced_image

    print(f"\nLoading SatPy scene from L1b files...")
    print(f"  Band 1: {band1_path.name}")
    print(f"  Band 2: {band2_path.name}")
    print(f"  Band 3: {band3_path.name}")

    scn = Scene(
        reader="abi_l1b",
        filenames=[str(band1_path), str(band2_path), str(band3_path)],
    )

    # Load the composite
    print(f"\nLoading composite: {composite_name}")
    scn.load([composite_name])

    # Resample BEFORE accessing — required for composites that need it
    print(f"\nResampling to CONUS EPSG:3857 grid...")
    from pyresample.geometry import AreaDefinition

    width = 2061
    height = 1153
    west, south, east, north = -14920000.0, 2752000.0, -6676000.0, 7364000.0

    area_def = AreaDefinition(
        "cartosky_conus",
        "CartoSky CONUS EPSG:3857",
        "cartosky_conus",
        {"proj": "merc", "a": 6378137, "b": 6378137, "lat_ts": 0, "lon_0": 0},
        width,
        height,
        (west, south, east, north),
    )

    resampled = scn.resample(area_def, resampler="nearest")

    # NOW access the composite from the resampled scene
    rgb = resampled[composite_name]

    print(f"Resampled shape: {rgb.shape}")

    # Convert to uint8 numpy array
    print("\nConverting to uint8...")
    rgb_data = rgb.values  # (3, H, W) float32, 0-1 range after enhancement

    # SatPy sometimes returns (C, H, W) and sometimes (H, W, C) depending on version
    if rgb_data.ndim == 3 and rgb_data.shape[0] == 3:
        # (C, H, W) → (H, W, C)
        rgb_data = np.moveaxis(rgb_data, 0, -1)

    print(f"RGB array shape: {rgb_data.shape}")
    print(f"RGB value range: min={np.nanmin(rgb_data):.3f} max={np.nanmax(rgb_data):.3f}")

    # Clip and convert to uint8
    rgb_uint8 = np.clip(rgb_data * 255, 0, 255).astype(np.uint8)

    # Save as WebP using PIL
    print("\nSaving WebP output...")
    from PIL import Image

    img = Image.fromarray(rgb_uint8, mode="RGB")
    webp_path = output_dir / f"true_color_probe_{composite_name}.webp"
    img.save(str(webp_path), "WEBP", quality=85)
    print(f"Saved: {webp_path} ({webp_path.stat().st_size / 1e3:.1f}KB)")

    # Also save a PNG for easier visual inspection
    png_path = output_dir / f"true_color_probe_{composite_name}.png"
    img.save(str(png_path), "PNG")
    print(f"Saved: {png_path} ({png_path.stat().st_size / 1e3:.1f}KB)")

    # Print per-channel stats
    stats: dict[str, Any] = {}
    channel_names = ["red", "green", "blue"]
    for i, ch in enumerate(channel_names):
        ch_data = rgb_uint8[:, :, i]
        stats[ch] = {
            "min": int(ch_data.min()),
            "max": int(ch_data.max()),
            "mean": float(ch_data.mean()),
        }
        print(f"  {ch}: min={stats[ch]['min']} max={stats[ch]['max']} mean={stats[ch]['mean']:.1f}")

    return {
        "webp_path": str(webp_path),
        "png_path": str(png_path),
        "shape": list(rgb_uint8.shape),
        "channel_stats": stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe SatPy True Color RGB from GOES-19 ABI L1b.")
    parser.add_argument(
        "--attime",
        default=DEFAULT_ATTIME,
        help="UTC time to fetch near, e.g. 2026-06-16T18:00:00",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory for probe output files",
    )
    parser.add_argument(
        "--satellite",
        default="goes19",
        help="Satellite identifier (default: goes19)",
    )
    parser.add_argument(
        "--bucket",
        default="noaa-goes19",
        help="S3 bucket (default: noaa-goes19)",
    )
    parser.add_argument(
        "--composite",
        default="true_color",
        help="SatPy composite name (default: true_color). Try 'natural_color' if true_color fails.",
    )
    parser.add_argument(
        "--sector",
        default="C",
        help="ABI sector (default: C for CONUS)",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Redirect matplotlib config to avoid permission issues
    os.environ.setdefault("MPLCONFIGDIR", str(args.output_dir / "mplconfig"))

    attime = datetime.fromisoformat(args.attime.rstrip("Z"))
    l1b_dir = args.output_dir / "l1b"

    print(f"=== SatPy True Color Probe ===")
    print(f"Target time: {attime.isoformat()} UTC")
    print(f"Satellite: {args.satellite} / bucket: {args.bucket}")
    print(f"Sector: ABI-L1b-Rad{args.sector}")
    print(f"Composite: {args.composite}")
    print(f"Output: {args.output_dir}")
    print()

    # Fetch all three bands
    results: dict[str, Any] = {}
    band_paths: dict[int, Path] = {}
    for band in [1, 2, 3]:
        print(f"--- Fetching Band {band} ---")
        try:
            path = _fetch_l1b_band(
                band=band,
                attime=attime,
                save_dir=l1b_dir,
                satellite=args.satellite,
                bucket=args.bucket,
                sector=args.sector,
            )
            band_paths[band] = path
            results[f"band{band}_path"] = str(path)
        except Exception as exc:
            print(f"ERROR fetching Band {band}: {exc}")
            return 1

    print(f"\n--- Running SatPy composite: {args.composite} ---")
    try:
        composite_result = _run_satpy_composite(
            band1_path=band_paths[1],
            band2_path=band_paths[2],
            band3_path=band_paths[3],
            output_dir=args.output_dir,
            composite_name=args.composite,
        )
        results.update(composite_result)
    except Exception as exc:
        import traceback
        print(f"ERROR running composite: {exc}")
        traceback.print_exc()
        # Try fallback composite
        if args.composite == "true_color":
            print("\nRetrying with 'natural_color' composite...")
            try:
                composite_result = _run_satpy_composite(
                    band1_path=band_paths[1],
                    band2_path=band_paths[2],
                    band3_path=band_paths[3],
                    output_dir=args.output_dir,
                    composite_name="natural_color",
                )
                results.update(composite_result)
                results["fallback_composite"] = "natural_color"
            except Exception as exc2:
                print(f"ERROR on fallback composite: {exc2}")
                traceback.print_exc()
                return 1
        else:
            return 1

    summary_path = args.output_dir / "satpy_probe_summary.json"
    summary_path.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nSummary: {summary_path}")
    print("\n=== Probe complete ===")
    print(f"Visual output: {results.get('webp_path') or results.get('png_path')}")
    print("Copy the WebP/PNG to your Mac and open it to verify the composite looks correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())