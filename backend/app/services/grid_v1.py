from __future__ import annotations

import concurrent.futures
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from ..config import grid_v1_allowlist
from .render_resampling import variable_color_map_id

logger = logging.getLogger(__name__)

GRID_V1_MANIFEST_VERSION = 1
GRID_V1_SUBTYPE = "grid_webgl_v1"
GRID_V1_PROJECTION = "EPSG:3857"
GRID_V1_DTYPE = "uint16"
GRID_V1_ENDIANNESS = "little"
GRID_V1_LEVEL = 0

_PACKING_BY_MODEL_VAR: dict[tuple[str, str], dict[str, Any]] = {
    ("hrrr", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
}


def grid_v1_supported(model_id: str, var_key: str) -> bool:
    normalized_model = str(model_id or "").strip().lower()
    normalized_var = str(var_key or "").strip().lower()
    return (normalized_model, normalized_var) in grid_v1_allowlist()


def grid_v1_dir(data_root: Path, model: str, run: str, var: str) -> Path:
    return data_root / "published" / model / run / var / "grid_v1"


def grid_v1_manifest_path(data_root: Path, model: str, run: str, var: str) -> Path:
    return grid_v1_dir(data_root, model, run, var) / "manifest.json"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    tmp_path.replace(path)


def grid_v1_frame_filename(fh: int, *, level: int = GRID_V1_LEVEL) -> str:
    return f"fh{int(fh):03d}.l{int(level)}.u16.bin"


def grid_v1_frame_path(data_root: Path, model: str, run: str, var: str, fh: int, *, level: int = GRID_V1_LEVEL) -> Path:
    return grid_v1_dir(data_root, model, run, var) / grid_v1_frame_filename(fh, level=level)


def expected_grid_v1_frame_size_bytes(*, width: int, height: int) -> int:
    return max(0, int(width) * int(height) * 2)


def _packing_config(model: str, var: str) -> dict[str, Any] | None:
    return _PACKING_BY_MODEL_VAR.get((str(model).strip().lower(), str(var).strip().lower()))


def _encode_values(values: np.ndarray, *, scale: float, offset: float, nodata: int) -> np.ndarray:
    encoded = np.full(values.shape, int(nodata), dtype=np.uint16)
    valid_mask = np.isfinite(values)
    if not np.any(valid_mask):
        return encoded

    scaled = np.rint((values[valid_mask] - float(offset)) / float(scale))
    clipped = np.clip(scaled, 0, int(nodata) - 1).astype(np.uint16, copy=False)
    encoded[valid_mask] = clipped
    return encoded


def _write_frame_from_value_cog(
    *,
    model: str,
    var: str,
    fh: int,
    value_cog_path: Path,
    out_path: Path,
) -> dict[str, Any]:
    packing = _packing_config(model, var)
    if packing is None:
        raise ValueError(f"Unsupported grid_v1 pack target: {model}/{var}")

    with rasterio.open(value_cog_path) as ds:
        values = ds.read(1).astype(np.float32, copy=False)
        encoded = _encode_values(
            values,
            scale=float(packing["scale"]),
            offset=float(packing["offset"]),
            nodata=int(packing["nodata"]),
        )
        width = int(ds.width)
        height = int(ds.height)
        bounds = [float(ds.bounds.left), float(ds.bounds.bottom), float(ds.bounds.right), float(ds.bounds.top)]
        crs_text = ds.crs.to_string() if ds.crs is not None else GRID_V1_PROJECTION

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_bytes(encoded.astype("<u2", copy=False).tobytes(order="C"))
    tmp_path.replace(out_path)

    return {
        "fh": int(fh),
        "file": out_path.name,
        "width": width,
        "height": height,
        "bbox": bounds,
        "projection": crs_text,
    }


def _build_manifest_for_var(
    *,
    data_root: Path,
    model: str,
    run: str,
    var: str,
) -> bool:
    packing = _packing_config(model, var)
    if packing is None:
        return False

    var_dir = data_root / "published" / model / run / var
    if not var_dir.is_dir():
        return False

    frame_entries: list[dict[str, Any]] = []
    width: int | None = None
    height: int | None = None
    bbox: list[float] | None = None
    projection = GRID_V1_PROJECTION
    units = str(packing.get("units") or "")

    for sidecar_path in sorted(var_dir.glob("fh*.json")):
        fh_token = sidecar_path.stem
        if not fh_token.startswith("fh"):
            continue
        try:
            fh = int(fh_token.removeprefix("fh"))
        except ValueError:
            continue
        frame_path = grid_v1_frame_path(data_root, model, run, var, fh)
        value_cog_path = var_dir / f"{fh_token}.val.cog.tif"
        if not frame_path.is_file() or not value_cog_path.is_file():
            continue
        try:
            sidecar = json.loads(sidecar_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not units:
            units = str(sidecar.get("units") or units or "")
        with rasterio.open(value_cog_path) as ds:
            expected_size_bytes = expected_grid_v1_frame_size_bytes(width=int(ds.width), height=int(ds.height))
            actual_size_bytes = frame_path.stat().st_size if frame_path.is_file() else -1
            if actual_size_bytes != expected_size_bytes:
                logger.warning(
                    "Skipping invalid grid_v1 frame in manifest: model=%s run=%s var=%s fh=%s actual_bytes=%s expected_bytes=%s",
                    model,
                    run,
                    var,
                    fh,
                    actual_size_bytes,
                    expected_size_bytes,
                )
                continue
            if width is None:
                width = int(ds.width)
                height = int(ds.height)
                bbox = [float(ds.bounds.left), float(ds.bounds.bottom), float(ds.bounds.right), float(ds.bounds.top)]
                projection = ds.crs.to_string() if ds.crs is not None else GRID_V1_PROJECTION
        frame_entry: dict[str, Any] = {
            "fh": fh,
            "file": frame_path.name,
        }
        valid_time = sidecar.get("valid_time")
        if isinstance(valid_time, str) and valid_time.strip():
            frame_entry["valid_time"] = valid_time.strip()
        frame_entries.append(frame_entry)

    if width is None or height is None or bbox is None:
        return False

    frame_entries.sort(key=lambda item: int(item["fh"]))
    manifest = {
        "manifest_version": GRID_V1_MANIFEST_VERSION,
        "subtype": GRID_V1_SUBTYPE,
        "model": model,
        "run": run,
        "var": var,
        "projection": projection,
        "bbox": bbox,
        "grid": {
            "width": int(width),
            "height": int(height),
            "dtype": GRID_V1_DTYPE,
            "endianness": GRID_V1_ENDIANNESS,
            "scale": float(packing["scale"]),
            "offset": float(packing["offset"]),
            "nodata": int(packing["nodata"]),
            "units": units,
        },
        "palette": {
            "color_map_id": variable_color_map_id(model, var),
        },
        "lods": [
            {
                "level": GRID_V1_LEVEL,
                "width": int(width),
                "height": int(height),
                "frames": frame_entries,
            }
        ],
    }
    write_json_atomic(grid_v1_manifest_path(data_root, model, run, var), manifest)
    return True


def build_grid_v1_for_run(
    *,
    data_root: Path,
    model: str,
    run: str,
    workers: int,
    variables: tuple[str, ...] | None = None,
) -> tuple[int, int, int]:
    published_run = data_root / "published" / model / run
    if not published_run.is_dir():
        return 0, 0, 0

    requested_vars = {str(item).strip().lower() for item in (variables or ()) if str(item).strip()}
    jobs: list[tuple[str, int, Path, Path]] = []
    manifest_vars: set[str] = set()

    for var_dir in sorted(path for path in published_run.iterdir() if path.is_dir()):
        var = var_dir.name.strip().lower()
        if requested_vars and var not in requested_vars:
            continue
        if not grid_v1_supported(model, var):
            continue
        manifest_vars.add(var)
        for value_cog_path in sorted(var_dir.glob("fh*.val.cog.tif")):
            fh_token = value_cog_path.name.split(".")[0]
            try:
                fh = int(fh_token.removeprefix("fh"))
            except ValueError:
                continue
            sidecar_path = var_dir / f"{fh_token}.json"
            if not sidecar_path.is_file():
                continue
            out_path = grid_v1_frame_path(data_root, model, run, var, fh)
            jobs.append((var, fh, value_cog_path, out_path))

    if not jobs:
        return 0, 0, 0

    ok = 0
    fail = 0
    max_workers = max(1, int(workers))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(
                _write_frame_from_value_cog,
                model=model,
                var=var,
                fh=fh,
                value_cog_path=value_cog_path,
                out_path=out_path,
            )
            for var, fh, value_cog_path, out_path in jobs
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception:
                logger.exception("grid_v1 frame build failed for model=%s run=%s", model, run)
                fail += 1
                continue
            ok += 1

    manifest_ok = 0
    for var in sorted(manifest_vars):
        try:
            if _build_manifest_for_var(data_root=data_root, model=model, run=run, var=var):
                manifest_ok += 1
        except Exception:
            logger.exception("grid_v1 manifest build failed for model=%s run=%s var=%s", model, run, var)

    return ok, fail, manifest_ok
