#!/usr/bin/env python3
"""Probe GOES-16 ABI imagery via goes2go.

This is an investigation helper, not production pipeline code. It downloads one
CONUS scan for each requested band, prints the dataset shape/metadata, and
saves compact sample arrays for manual inspection.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr


DEFAULT_ATTIME = "2024-05-21T18:00:00"
DEFAULT_BANDS = (13, 9)


def _parse_attime(raw: str) -> datetime:
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1]
    # goes2go 2025.10.0 compares against tz-naive pandas timestamps.
    return datetime.fromisoformat(value).replace(tzinfo=None)


def _scalar_to_text(value: Any) -> str | None:
    if value is None:
        return None
    arr = np.asarray(value)
    if arr.shape == ():
        item = arr.item()
        if isinstance(item, bytes):
            return item.decode("utf-8", errors="replace")
        return str(item)
    return None


def _array_stats(values: np.ndarray) -> dict[str, Any]:
    item: dict[str, Any] = {
        "shape": list(values.shape),
        "dtype": str(values.dtype),
    }
    if values.size and np.issubdtype(values.dtype, np.number):
        finite = values[np.isfinite(values)]
        item.update(
            {
                "min": float(np.nanmin(finite)) if finite.size else None,
                "max": float(np.nanmax(finite)) if finite.size else None,
                "mean": float(np.nanmean(finite)) if finite.size else None,
                "finite_fraction": float(finite.size / values.size),
            }
        )
    else:
        scalar = _scalar_to_text(values)
        if scalar is not None:
            item["value"] = scalar
    return item


def _summarize_dataset(ds: xr.Dataset, band: int) -> dict[str, Any]:
    cmi = np.asarray(ds["CMI"].values)
    dqf = np.asarray(ds["DQF"].values) if "DQF" in ds else None
    projection_attrs = dict(ds["goes_imager_projection"].attrs) if "goes_imager_projection" in ds else {}
    summary: dict[str, Any] = {
        "band": band,
        "sizes": {key: int(value) for key, value in ds.sizes.items()},
        "data_vars": list(ds.data_vars),
        "coords": list(ds.coords),
        "dataset_attrs": {key: str(value) for key, value in ds.attrs.items()},
        "CMI": {
            **_array_stats(cmi),
            "attrs": {key: str(value) for key, value in ds["CMI"].attrs.items()},
        },
        "goes_imager_projection": {key: str(value) for key, value in projection_attrs.items()},
    }
    if dqf is not None:
        summary["DQF"] = {
            **_array_stats(dqf),
            "attrs": {key: str(value) for key, value in ds["DQF"].attrs.items()},
        }
    for coord in ("t", "band_id", "band_wavelength", "time_coverage_start", "time_coverage_end", "date_created"):
        if coord in ds.coords:
            da = ds.coords[coord]
            summary[coord] = {
                **_array_stats(np.asarray(da.values)),
                "attrs": {key: str(value) for key, value in da.attrs.items()},
            }
    sources = []
    for var_name in ds.data_vars:
        source = ds[var_name].encoding.get("source")
        if source:
            sources.append(str(source))
    if sources:
        summary["encoding_sources"] = sorted(set(sources))
    return summary


def _save_sample(ds: xr.Dataset, band: int, output_dir: Path, sample_size: int) -> Path:
    cmi = np.asarray(ds["CMI"].values, dtype=np.float32)
    dqf = np.asarray(ds["DQF"].values, dtype=np.float32) if "DQF" in ds else None
    height, width = cmi.shape
    sample_h = min(sample_size, height)
    sample_w = min(sample_size, width)
    y0 = max(0, (height - sample_h) // 2)
    x0 = max(0, (width - sample_w) // 2)
    payload: dict[str, Any] = {
        "CMI": cmi[y0 : y0 + sample_h, x0 : x0 + sample_w],
        "window": np.asarray([y0, x0, sample_h, sample_w], dtype=np.int32),
    }
    if dqf is not None:
        payload["DQF"] = dqf[y0 : y0 + sample_h, x0 : x0 + sample_w]
    out_path = output_dir / f"goes16_band{band}_sample_{sample_h}x{sample_w}.npz"
    np.savez_compressed(out_path, **payload)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe GOES-16 CONUS ABI L2 CMIP imagery with goes2go.")
    parser.add_argument("--attime", default=DEFAULT_ATTIME, help="Naive UTC scan time, e.g. 2024-05-21T18:00:00")
    parser.add_argument("--bands", nargs="+", type=int, default=list(DEFAULT_BANDS), help="ABI bands to fetch")
    parser.add_argument("--save-dir", type=Path, default=Path("/private/tmp/cartosky-goes2go-probe/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("/private/tmp/cartosky-goes2go-probe"))
    parser.add_argument("--sample-size", type=int, default=256)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("MPLCONFIGDIR", str(args.output_dir / "mplconfig"))
    os.environ.setdefault("GOES2GO_CONFIG_PATH", str(args.output_dir / "goes2go-config"))

    from goes2go.data import goes_nearesttime

    args.save_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    attime = _parse_attime(args.attime)

    all_summaries: dict[str, Any] = {}
    for band in args.bands:
        print(f"Fetching GOES-16 ABI-L2-CMIPC Band {band} nearest {attime.isoformat()} UTC")
        ds = goes_nearesttime(
            attime,
            satellite="noaa-goes16",
            product="ABI-L2-CMIP",
            domain="C",
            bands=[band],
            return_as="xarray",
            download=True,
            overwrite=args.overwrite,
            save_dir=args.save_dir,
            verbose=True,
        )
        if isinstance(ds, list):
            ds = ds[0]
        summary = _summarize_dataset(ds, band)
        sample_path = _save_sample(ds, band, args.output_dir, max(1, int(args.sample_size)))
        summary["sample_path"] = str(sample_path)
        all_summaries[str(band)] = summary
        print(json.dumps(summary, indent=2))

    summary_path = args.output_dir / "goes2go_probe_summary.json"
    summary_path.write_text(json.dumps(all_summaries, indent=2) + "\n")
    print(f"Wrote summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
