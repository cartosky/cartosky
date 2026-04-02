from __future__ import annotations

import concurrent.futures
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.transform import Affine, array_bounds

from ..config import grid_supported_pair
from .colormaps import get_color_map_spec
from .grid_display_prep import prepare_grid_display_values
from .render_resampling import variable_color_map_id

logger = logging.getLogger(__name__)

GRID_MANIFEST_VERSION = 1
GRID_SUBTYPE = "grid"
GRID_PROJECTION = "EPSG:3857"
GRID_DTYPE = "uint16"
GRID_ENDIANNESS = "little"
GRID_LEVEL = 0

_PACKING_BY_MODEL_VAR: dict[tuple[str, str], dict[str, Any]] = {
    ("hrrr", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("hrrr", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("hrrr", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("hrrr", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("hrrr", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("hrrr", "radar_ptype"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("hrrr", "precip_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("hrrr", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("hrrr", "snowfall_kuchera_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gfs", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("gfs", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("gfs", "precip_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gfs", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nam", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nam", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nam", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nam", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("nam", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("nam", "radar_ptype"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("nam", "precip_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nam", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nbm", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nbm", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("nbm", "precip_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nbm", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("mrms", "reflectivity"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "dBZ",
    },
}


def grid_code_supported(model_id: str, var_key: str) -> bool:
    normalized_model = str(model_id or "").strip().lower()
    normalized_var = str(var_key or "").strip().lower()
    return _packing_config(normalized_model, normalized_var) is not None


def grid_supported(model_id: str, var_key: str) -> bool:
    return grid_supported_pair(model_id, var_key)


def grid_dir_for_run_root(run_root: Path, var: str) -> Path:
    return Path(run_root) / var / "grid_v1"


def grid_dir(data_root: Path, model: str, run: str, var: str) -> Path:
    return grid_dir_for_run_root(data_root / "published" / model / run, var)


def grid_manifest_path(data_root: Path, model: str, run: str, var: str) -> Path:
    return grid_dir(data_root, model, run, var) / "manifest.json"


def grid_manifest_path_for_run_root(run_root: Path, var: str) -> Path:
    return grid_dir_for_run_root(run_root, var) / "manifest.json"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    tmp_path.replace(path)


def grid_frame_filename(fh: int, *, level: int = GRID_LEVEL) -> str:
    return f"fh{int(fh):03d}.l{int(level)}.u16.bin"


def grid_frame_path(data_root: Path, model: str, run: str, var: str, fh: int, *, level: int = GRID_LEVEL) -> Path:
    return grid_dir(data_root, model, run, var) / grid_frame_filename(fh, level=level)


def grid_frame_path_for_run_root(run_root: Path, var: str, fh: int, *, level: int = GRID_LEVEL) -> Path:
    return grid_dir_for_run_root(run_root, var) / grid_frame_filename(fh, level=level)


def grid_frame_meta_filename(fh: int, *, level: int = GRID_LEVEL) -> str:
    return f"fh{int(fh):03d}.l{int(level)}.meta.json"


def grid_frame_meta_path(data_root: Path, model: str, run: str, var: str, fh: int, *, level: int = GRID_LEVEL) -> Path:
    return grid_dir(data_root, model, run, var) / grid_frame_meta_filename(fh, level=level)


def grid_frame_meta_path_for_run_root(run_root: Path, var: str, fh: int, *, level: int = GRID_LEVEL) -> Path:
    return grid_dir_for_run_root(run_root, var) / grid_frame_meta_filename(fh, level=level)


def expected_grid_frame_size_bytes(*, width: int, height: int) -> int:
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


def write_grid_frame_for_run_root(
    *,
    run_root: Path,
    model: str,
    var: str,
    fh: int,
    values: np.ndarray,
    transform: Affine | None = None,
    bbox: list[float] | tuple[float, float, float, float] | None = None,
    projection: str = GRID_PROJECTION,
) -> dict[str, Any]:
    packing = _packing_config(model, var)
    if packing is None:
        raise ValueError(f"Unsupported grid pack target: {model}/{var}")

    values_array = np.asarray(values, dtype=np.float32)
    display_values, prep_meta = prepare_grid_display_values(model=model, var=var, values=values_array)
    encoded = _encode_values(
        display_values,
        scale=float(packing["scale"]),
        offset=float(packing["offset"]),
        nodata=int(packing["nodata"]),
    )
    height, width = encoded.shape
    if bbox is None:
        if transform is None:
            raise ValueError(f"Missing transform/bbox for grid frame: {model}/{var}/fh{int(fh):03d}")
        left, bottom, right, top = array_bounds(height, width, transform)
        bounds = [float(left), float(bottom), float(right), float(top)]
    else:
        bounds = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
    crs_text = str(projection or GRID_PROJECTION)

    out_path = grid_frame_path_for_run_root(run_root, var, fh)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_bytes(encoded.astype("<u2", copy=False).tobytes(order="C"))
    tmp_path.replace(out_path)

    frame_meta = {
        "fh": int(fh),
        "file": out_path.name,
        "width": width,
        "height": height,
        "bbox": bounds,
        "projection": crs_text,
    }
    if prep_meta:
        frame_meta["display_prep"] = prep_meta
    write_json_atomic(grid_frame_meta_path_for_run_root(run_root, var, fh), frame_meta)
    return frame_meta


def write_grid_frame_from_value_cog_for_run_root(
    *,
    run_root: Path,
    model: str,
    var: str,
    fh: int,
    value_cog_path: Path,
) -> dict[str, Any]:
    if not value_cog_path.is_file():
        raise FileNotFoundError(f"Missing grid source value COG: {value_cog_path}")
    try:
        with rasterio.open(value_cog_path) as ds:
            return write_grid_frame_for_run_root(
                run_root=run_root,
                model=model,
                var=var,
                fh=fh,
                values=ds.read(1).astype(np.float32, copy=False),
                transform=ds.transform,
                projection=ds.crs.to_string() if ds.crs is not None else GRID_PROJECTION,
            )
    except RasterioIOError as exc:
        raise FileNotFoundError(f"Unreadable grid source value COG: {value_cog_path}") from exc


def _build_palette_block(model: str, var: str) -> dict[str, Any]:
    color_map_id = variable_color_map_id(model, var)
    palette: dict[str, Any] = {"color_map_id": color_map_id}
    if color_map_id:
        try:
            spec = get_color_map_spec(color_map_id)
        except KeyError:
            spec = {}
        spec_type = str(spec.get("type") or "").strip()
        if spec_type:
            palette["kind"] = spec_type
        gamma = spec.get("power_norm_gamma")
        if gamma is not None:
            palette["power_norm_gamma"] = float(gamma)
        transparent_below_min = spec.get("transparent_below_min")
        if isinstance(transparent_below_min, (int, float)) and not isinstance(transparent_below_min, bool):
            palette["transparent_below_min"] = float(transparent_below_min)
        elif transparent_below_min is True and spec_type == "discrete":
            levels = spec.get("levels")
            if isinstance(levels, list) and levels:
                first_level = levels[0]
                if isinstance(first_level, (int, float)) and not isinstance(first_level, bool):
                    palette["transparent_below_min"] = float(first_level)
        transparent_zero = spec.get("transparent_zero")
        if isinstance(transparent_zero, bool):
            palette["transparent_zero"] = transparent_zero
    return palette


def _build_manifest_for_var_from_run_root(
    *,
    run_root: Path,
    model: str,
    run: str,
    var: str,
) -> bool:
    packing = _packing_config(model, var)
    if packing is None:
        return False

    var_dir = Path(run_root) / var
    if not var_dir.is_dir():
        return False

    frame_entries: list[dict[str, Any]] = []
    width: int | None = None
    height: int | None = None
    bbox: list[float] | None = None
    projection = GRID_PROJECTION
    units = str(packing.get("units") or "")
    display_prep: dict[str, Any] | None = None

    for sidecar_path in sorted(var_dir.glob("fh*.json")):
        fh_token = sidecar_path.stem
        if not fh_token.startswith("fh"):
            continue
        try:
            fh = int(fh_token.removeprefix("fh"))
        except ValueError:
            continue
        frame_path = grid_frame_path_for_run_root(run_root, var, fh)
        frame_meta_path = grid_frame_meta_path_for_run_root(run_root, var, fh)
        value_cog_path = var_dir / f"{fh_token}.val.cog.tif"
        if not frame_path.is_file():
            continue
        try:
            sidecar = json.loads(sidecar_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not units:
            units = str(sidecar.get("units") or units or "")
        frame_meta: dict[str, Any] | None = None
        if frame_meta_path.is_file():
            try:
                frame_meta = json.loads(frame_meta_path.read_text())
            except (OSError, json.JSONDecodeError):
                frame_meta = None
        if frame_meta is not None:
            frame_width = int(frame_meta.get("width") or 0)
            frame_height = int(frame_meta.get("height") or 0)
            frame_bbox = frame_meta.get("bbox")
            frame_projection = str(frame_meta.get("projection") or GRID_PROJECTION)
            actual_size_bytes = frame_path.stat().st_size if frame_path.is_file() else -1
            expected_size_bytes = expected_grid_frame_size_bytes(width=frame_width, height=frame_height)
            if actual_size_bytes != expected_size_bytes:
                logger.warning(
                    "Skipping invalid grid frame in manifest: model=%s run=%s var=%s fh=%s actual_bytes=%s expected_bytes=%s",
                    model,
                    run,
                    var,
                    fh,
                    actual_size_bytes,
                    expected_size_bytes,
                )
                continue
            if width is None:
                width = frame_width
                height = frame_height
                if isinstance(frame_bbox, list) and len(frame_bbox) == 4:
                    bbox = [float(frame_bbox[0]), float(frame_bbox[1]), float(frame_bbox[2]), float(frame_bbox[3])]
                projection = frame_projection
            if display_prep is None and isinstance(frame_meta.get("display_prep"), dict):
                display_prep = dict(frame_meta["display_prep"])
        else:
            if not value_cog_path.is_file():
                continue
            with rasterio.open(value_cog_path) as ds:
                expected_size_bytes = expected_grid_frame_size_bytes(width=int(ds.width), height=int(ds.height))
                actual_size_bytes = frame_path.stat().st_size if frame_path.is_file() else -1
                if actual_size_bytes != expected_size_bytes:
                    logger.warning(
                        "Skipping invalid grid frame in manifest: model=%s run=%s var=%s fh=%s actual_bytes=%s expected_bytes=%s",
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
                    projection = ds.crs.to_string() if ds.crs is not None else GRID_PROJECTION
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
        "manifest_version": GRID_MANIFEST_VERSION,
        "subtype": GRID_SUBTYPE,
        "model": model,
        "run": run,
        "var": var,
        "projection": projection,
        "bbox": bbox,
        "grid": {
            "width": int(width),
            "height": int(height),
            "dtype": GRID_DTYPE,
            "endianness": GRID_ENDIANNESS,
            "scale": float(packing["scale"]),
            "offset": float(packing["offset"]),
            "nodata": int(packing["nodata"]),
            "units": units,
        },
        "palette": _build_palette_block(model, var),
        "lods": [
            {
                "level": GRID_LEVEL,
                "width": int(width),
                "height": int(height),
                "frames": frame_entries,
            }
        ],
    }
    if display_prep:
        manifest["display_prep"] = display_prep
    write_json_atomic(grid_manifest_path_for_run_root(run_root, var), manifest)
    return True


def build_grid_manifests_for_run_root(
    *,
    run_root: Path,
    model: str,
    run: str,
    variables: tuple[str, ...] | None = None,
) -> int:
    run_root_path = Path(run_root)
    if not run_root_path.is_dir():
        return 0

    requested_vars = {str(item).strip().lower() for item in (variables or ()) if str(item).strip()}
    manifest_ok = 0
    for var_dir in sorted(path for path in run_root_path.iterdir() if path.is_dir()):
        var = var_dir.name.strip().lower()
        if requested_vars and var not in requested_vars:
            continue
        if not grid_supported(model, var):
            continue
        try:
            if _build_manifest_for_var_from_run_root(run_root=run_root_path, model=model, run=run, var=var):
                manifest_ok += 1
        except Exception:
            logger.exception("grid manifest build failed: model=%s run=%s var=%s", model, run, var)
    return manifest_ok


def build_grid_for_run(
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
    jobs: list[tuple[str, int, Path]] = []
    manifest_vars: set[str] = set()

    for var_dir in sorted(path for path in published_run.iterdir() if path.is_dir()):
        var = var_dir.name.strip().lower()
        if requested_vars and var not in requested_vars:
            continue
        if not grid_supported(model, var):
            continue
        manifest_vars.add(var)
        for value_cog_path in sorted(var_dir.glob("fh*.val.cog.tif")):
            if not value_cog_path.is_file():
                logger.warning(
                    "Skipping missing grid source value COG: model=%s run=%s var=%s path=%s",
                    model,
                    run,
                    var,
                    value_cog_path,
                )
                continue
            fh_token = value_cog_path.name.split(".")[0]
            try:
                fh = int(fh_token.removeprefix("fh"))
            except ValueError:
                continue
            sidecar_path = var_dir / f"{fh_token}.json"
            if not sidecar_path.is_file():
                continue
            jobs.append((var, fh, value_cog_path))

    if not jobs:
        return 0, 0, 0

    ok = 0
    fail = 0
    max_workers = max(1, int(workers))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(
                write_grid_frame_from_value_cog_for_run_root,
                run_root=published_run,
                model=model,
                var=var,
                fh=fh,
                value_cog_path=value_cog_path,
            )
            for var, fh, value_cog_path in jobs
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception:
                logger.exception("grid frame build failed for model=%s run=%s", model, run)
                fail += 1
                continue
            ok += 1

    manifest_ok = build_grid_manifests_for_run_root(
        run_root=published_run,
        model=model,
        run=run,
        variables=tuple(sorted(manifest_vars)),
    )

    return ok, fail, manifest_ok
