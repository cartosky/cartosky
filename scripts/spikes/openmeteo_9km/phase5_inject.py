"""Phase 5 — visual gate: inject an Open-Meteo 9 km (O1280) field into the REAL
CartoSky artifact pipeline.

Grid VALUES come from the fast path (`ecmwf_ifs` 9 km `.om` on Open-Meteo's S3
bucket, regridded with the Phase 2 reduced-Gaussian bilinear sampler). Every
byte of packing/encoding/sidecar/manifest writing goes through backend code:

  app.services.grid.write_grid_frame_for_run_root      -> grid/fh012.l0.u16.bin + .meta.json
  app.services.builder.pipeline.build_sidecar_json     -> tmp2m/fh012.json (legend/units/valid_time)
  app.services.grid.build_grid_manifests_for_run_root  -> tmp2m/grid/manifest.json
  app.services.scheduler._write_run_manifest           -> manifests/ecmwf/20260804_12z.json
  app.services.scheduler._promote_run                  -> staging -> published

Units: the production ECMWF tmp2m artifact stores DEGREES FAHRENHEIT
(`app/services/grid.py:295-300` packing declares units "F"; the ECMWF plugin's
VarSpec declares units="F" at `app/models/ecmwf.py:274-292`; the builder applies
`ECMWF_CONVERSION_BY_VAR_KEY["tmp2m"] == "c_to_f"`, `app/models/ecmwf.py:1150-1151`,
implemented as `data * 9.0 / 5.0 + 32.0` in
`app/services/builder/fetch.py:5251-5258` — GDAL's GRIB driver already
normalized Kelvin to Celsius, so the builder's input is °C, exactly like the
`.om` source). We therefore apply the identical C->F conversion.

Run from backend/ with CARTOSKY_DATA_ROOT set BEFORE python starts
(app.main reads it at import time, app/main.py:112).

  cd backend && CARTOSKY_DATA_ROOT=/path/to/tree python ../scripts/spikes/openmeteo_9km/phase5_inject.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SPIKE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SPIKE_DIR.parents[2] / "backend"
sys.path.insert(0, str(SPIKE_DIR))
sys.path.insert(0, str(BACKEND_DIR))  # so `app.*` resolves regardless of cwd

MODEL = "ecmwf"
VAR = "tmp2m"
FH = 12
RUN_ID = "20260804_12z"
RUN_DATE = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

# Open-Meteo 9 km IFS, 2026-08-04 12z, valid 2026-08-05T00Z == fh12.
OM_URL = (
    "https://openmeteo.s3.amazonaws.com/data_spatial/ecmwf_ifs/"
    "2026/08/04/1200Z/2026-08-05T0000.om"
)
OM_VARIABLE = "temperature_2m"

# NA 9 km EPSG:3857 TAP-snapped bbox / shape (raster_grid.py rules, replicated
# in phase2_regrid.na_target_grid_9km).
NA_BBOX = (-19818000.0, 549000.0, -2781000.0, 16974000.0)
NA_SHAPE = (1825, 1893)


def fetch_and_regrid_celsius(cache: Path | None) -> np.ndarray:
    """NA-grid float32 field in DEGREES CELSIUS (the `.om` native unit)."""
    if cache is not None and cache.is_file():
        values = np.load(cache)
        print(f"[regrid] loaded cached NA field from {cache}")
        return values.astype(np.float32, copy=False)

    from omfiles import OmFileReader  # GPLv2, isolated to the spike venv

    from o1280 import TOTAL
    from omblock import BlockCachedHTTPFS
    from phase2_regrid import apply_sampler, build_bilinear_sampler, na_target_grid_9km

    fs = BlockCachedHTTPFS()
    reader = OmFileReader.from_fsspec(fs, OM_URL)
    src = np.asarray(
        reader.get_child_by_name(OM_VARIABLE).read_array((slice(0, 1), slice(0, TOTAL)))
    ).ravel()
    print(f"[regrid] read {fs.bytes / 1e6:.1f} MB in {fs.requests} range requests")

    lat, lon, shape, snapped = na_target_grid_9km()
    if shape != NA_SHAPE or tuple(snapped) != NA_BBOX:
        raise SystemExit(f"NA grid drift: shape={shape} bbox={snapped}")
    idx, weights = build_bilinear_sampler(lat, lon)
    values = apply_sampler(idx, weights, src).reshape(shape).astype(np.float32)
    if cache is not None:
        np.save(cache, values)
    return values


def main() -> int:
    data_root = Path(os.environ.get("CARTOSKY_DATA_ROOT", "")).expanduser()
    if not str(data_root):
        raise SystemExit("CARTOSKY_DATA_ROOT must be set before running")

    cache_env = os.environ.get("PHASE5_REGRID_CACHE")
    cache = Path(cache_env) if cache_env else None

    from app.models.registry import MODEL_REGISTRY
    from app.services.builder.pipeline import (
        HOVER_VALUE_DOWNSAMPLE_FACTOR,
        _resolve_model_var_capability,
        _resolve_model_var_spec,
        _write_json_atomic,
        build_sidecar_json,
    )
    from app.services.builder.colorize import float_to_rgba
    from app.services.colormaps import get_color_map_spec
    from app.services.grid import build_grid_manifests_for_run_root, write_grid_frame_for_run_root
    from app.services.scheduler import (
        _frame_sidecar_path,
        _manifest_path,
        _promote_run,
        _staging_run_root,
        _write_run_manifest,
    )

    plugin = MODEL_REGISTRY.get(MODEL)
    if plugin is None:
        raise SystemExit(f"No model plugin registered for {MODEL!r}")
    var_key = plugin.normalize_var_id(VAR)

    # --- Fast-path values, converted to the production storage unit ---------
    celsius = fetch_and_regrid_celsius(cache)
    fahrenheit = (celsius * 9.0 / 5.0 + 32.0).astype(np.float32)
    print(
        f"[values] degC min={celsius.min():.2f} max={celsius.max():.2f} | "
        f"degF min={fahrenheit.min():.2f} max={fahrenheit.max():.2f}"
    )
    np.save(data_root.parent / "phase5_pre_pack_degF.npy", fahrenheit)

    staging_run_root = _staging_run_root(data_root, MODEL, RUN_ID, None)
    staging_run_root.mkdir(parents=True, exist_ok=True)

    # --- 1. Grid frame: .bin + .meta.json (real packer) ---------------------
    frame_meta = write_grid_frame_for_run_root(
        run_root=staging_run_root,
        model=MODEL,
        var=var_key,
        fh=FH,
        values=fahrenheit,
        bbox=list(NA_BBOX),
    )
    print(f"[frame] {frame_meta['file']} {frame_meta['width']}x{frame_meta['height']}")

    # --- 2. Per-fh sidecar (real legend/units/valid_time builder) -----------
    var_spec_model = _resolve_model_var_spec(MODEL, var_key, plugin)
    var_capability = _resolve_model_var_capability(MODEL, var_key, plugin)
    color_map_id = str(var_capability.color_map_id).strip()
    var_spec_colormap = get_color_map_spec(color_map_id)
    _, colorize_meta = float_to_rgba(fahrenheit, color_map_id, meta_var_key=var_key)

    sidecar = build_sidecar_json(
        model=MODEL,
        run_id=RUN_ID,
        var_id=var_key,
        fh=FH,
        run_date=RUN_DATE,
        colorize_meta=colorize_meta,
        var_spec=var_spec_colormap,
        var_spec_model=var_spec_model,
        value_downsample_factor=HOVER_VALUE_DOWNSAMPLE_FACTOR,
        quality="full",
        quality_flags=[],
    )
    sidecar_path = _frame_sidecar_path(data_root, MODEL, RUN_ID, var_key, FH)
    _write_json_atomic(sidecar_path, sidecar)
    print(f"[sidecar] {sidecar_path.name} units={sidecar['units']} valid_time={sidecar['valid_time']}")

    # --- 3. Grid manifest (real builder, scans the staging run root) --------
    built = build_grid_manifests_for_run_root(
        run_root=staging_run_root, model=MODEL, run=RUN_ID, variables=(var_key,)
    )
    if built != 1:
        raise SystemExit(f"grid manifest build wrote {built} manifests, expected 1")

    # --- 4. Run manifest (real scheduler writer) ----------------------------
    _write_run_manifest(
        data_root=data_root,
        model=MODEL,
        run_id=RUN_ID,
        targets=[(var_key, FH)],
        plugin=plugin,
    )
    print(f"[run manifest] {_manifest_path(data_root, MODEL, RUN_ID)}")

    # --- 5. Promote staging -> published (real scheduler promotion) ---------
    _promote_run(data_root, MODEL, RUN_ID)
    print(f"[published] {data_root / 'published' / MODEL / RUN_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
