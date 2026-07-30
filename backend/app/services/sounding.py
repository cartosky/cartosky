"""Sounding "profile stack" artifacts (Skew-T design 2026-07-30, Phase 1).

A sounding stack is one binary file per (run, forecast hour) holding an entire
vertical profile for every point of a decimated model grid, so a Skew-T panel
can be served with a **single seek and a single small read** per point.

This is deliberately *not* a raster variable: it is never registered in a
model plugin's ``vars``, never appears in the capabilities API, and is never
warped to Web Mercator. It is a point-sampling-only artifact and this module
owns its whole lifecycle — fetch spec, packing table, writer, sidecar and
reader.

Layout (design §3) — pixel-major uint16::

    offset(row, col, plane) = ((row * width + col) * n_planes + plane) * 2
    plane = level_index * 5 + var_index          # 0 .. 184  (isobaric)
    plane = 185 + surface_field_index            # 185 .. 189 (surface block)

Levels run 1000 -> 100 hPa every 25 hPa (37 of them, the extrapolated
"1013.2 mb" level deliberately excluded); isobaric variables are ordered
TMP, DPT, UGRD, VGRD, VVEL. One pixel therefore owns a contiguous 380 B run.

The sidecar written next to each stack is fully self-describing: a reader
needs the JSON and nothing else (no import of this module, no model
registry lookup) to decode a profile.

Manual spot-check entry point::

    python -m app.services.sounding --run 20260730_12z --fh 6 --out /tmp/sndg
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------

#: Bumped whenever the on-disk layout, level ladder or variable set changes.
#: Old runs are never migrated — they age out with run retention (design §3).
SOUNDING_FORMAT_VERSION = 1

#: 1000 -> 100 hPa every 25 hPa. "1013.2 mb" is excluded on purpose: it is a
#: sub-surface extrapolated level (design §1).
LEVELS_HPA: tuple[int, ...] = tuple(range(1000, 99, -25))

#: Native-grid decimation stride, both axes (HRRR 1799x1059 -> 450x265).
DECIMATION_STRIDE = 4

#: Only model currently wired for stack builds (design §7 Phase 1).
SUPPORTED_MODELS = frozenset({"hrrr"})

NODATA_CODE = 65535
BYTES_PER_SAMPLE = 2
STACK_DTYPE = "<u2"


@dataclass(frozen=True)
class PackSpec:
    """uint16 packing constants: ``value = code * scale + offset``."""

    units: str
    scale: float
    offset: float
    nodata: int = NODATA_CODE
    # Physical sanity window. A plane whose finite values fall outside this is
    # a units surprise (K vs degC, Pa vs hPa) and must fail the build loudly
    # rather than quietly writing clipped garbage.
    valid_min: float = -math.inf
    valid_max: float = math.inf

    @property
    def max_code(self) -> int:
        return int(self.nodata) - 1

    @property
    def precision(self) -> float:
        """Worst-case round-trip error introduced by quantization."""
        return float(self.scale) / 2.0


# Precision targets (orchestrator spec): T/Td <= 0.05 degC over -120..+60;
# winds/VVEL <= 0.05 over +/-163; surface pressure <= 0.05 hPa over 300..1100.
# Every entry below quantizes at least 2x finer than its target.
TEMPERATURE_PACK = PackSpec(
    units="degC", scale=0.01, offset=-120.0, valid_min=-150.0, valid_max=80.0
)
WIND_PACK = PackSpec(
    units="m s-1", scale=0.01, offset=-163.0, valid_min=-200.0, valid_max=200.0
)
VVEL_PACK = PackSpec(
    units="Pa s-1", scale=0.01, offset=-163.0, valid_min=-200.0, valid_max=200.0
)
# 300..1100 hPa needs 80000 codes at 0.01; 0.02 fits uint16 with 0.01 hPa error.
PRESSURE_PACK = PackSpec(
    units="hPa", scale=0.02, offset=300.0, valid_min=200.0, valid_max=1200.0
)


def _identity(values: np.ndarray) -> np.ndarray:
    return values


def _pa_to_hpa(values: np.ndarray) -> np.ndarray:
    return (values / np.float32(100.0)).astype(np.float32, copy=False)


@dataclass(frozen=True)
class SoundingVariable:
    """One isobaric variable, present at every level of :data:`LEVELS_HPA`."""

    id: str
    grib_element: str
    pack: PackSpec
    convert: Callable[[np.ndarray], np.ndarray] = _identity


@dataclass(frozen=True)
class SurfaceField:
    """One near-surface field, stored as a trailing plane (design §2)."""

    id: str
    grib_element: str
    # GDAL ``GRIB_SHORT_NAME`` level token: "0-SFC", "2-HTGL", "10-HTGL".
    level_token: str
    level_label: str
    pack: PackSpec
    convert: Callable[[np.ndarray], np.ndarray] = _identity


ISOBARIC_VARIABLES: tuple[SoundingVariable, ...] = (
    SoundingVariable("t", "TMP", TEMPERATURE_PACK),
    SoundingVariable("td", "DPT", TEMPERATURE_PACK),
    SoundingVariable("u", "UGRD", WIND_PACK),
    SoundingVariable("v", "VGRD", WIND_PACK),
    SoundingVariable("w", "VVEL", VVEL_PACK),
)

SURFACE_FIELDS: tuple[SurfaceField, ...] = (
    SurfaceField("pres_sfc", "PRES", "0-SFC", "surface", PRESSURE_PACK, _pa_to_hpa),
    SurfaceField("t2m", "TMP", "2-HTGL", "2 m above ground", TEMPERATURE_PACK),
    SurfaceField("td2m", "DPT", "2-HTGL", "2 m above ground", TEMPERATURE_PACK),
    SurfaceField("u10m", "UGRD", "10-HTGL", "10 m above ground", WIND_PACK),
    SurfaceField("v10m", "VGRD", "10-HTGL", "10 m above ground", WIND_PACK),
)

N_ISOBARIC_PLANES = len(LEVELS_HPA) * len(ISOBARIC_VARIABLES)
N_PLANES = N_ISOBARIC_PLANES + len(SURFACE_FIELDS)
BYTES_PER_PIXEL = N_PLANES * BYTES_PER_SAMPLE

ISOBARIC_PRODUCT = "prs"
SURFACE_PRODUCT = "sfc"


def isobaric_plane_index(level_index: int, var_index: int) -> int:
    """Plane number for the *var_index*-th variable at the *level_index*-th level."""
    if not 0 <= level_index < len(LEVELS_HPA):
        raise ValueError(f"level_index out of range: {level_index}")
    if not 0 <= var_index < len(ISOBARIC_VARIABLES):
        raise ValueError(f"var_index out of range: {var_index}")
    return level_index * len(ISOBARIC_VARIABLES) + var_index


def surface_plane_index(field_index: int) -> int:
    if not 0 <= field_index < len(SURFACE_FIELDS):
        raise ValueError(f"field_index out of range: {field_index}")
    return N_ISOBARIC_PLANES + field_index


def pixel_byte_offset(row: int, col: int, *, width: int, plane: int = 0) -> int:
    """Byte offset of ``plane`` within pixel ``(row, col)`` (design §3)."""
    return ((int(row) * int(width) + int(col)) * N_PLANES + int(plane)) * BYTES_PER_SAMPLE


def expected_stack_size_bytes(*, width: int, height: int) -> int:
    return int(width) * int(height) * BYTES_PER_PIXEL


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------


def encode_plane(values: np.ndarray, pack: PackSpec) -> np.ndarray:
    """Quantize physical values to uint16 codes; non-finite -> nodata sentinel.

    Mirrors the house ``code * scale + offset`` convention (grid.py
    ``_encode_values``); values are clipped into the representable window so a
    freak outlier saturates instead of wrapping.
    """
    array = np.asarray(values, dtype=np.float32)
    encoded = np.full(array.shape, int(pack.nodata), dtype=np.uint16)
    valid = np.isfinite(array)
    if not np.any(valid):
        return encoded
    scaled = np.rint((array[valid] - float(pack.offset)) / float(pack.scale))
    encoded[valid] = np.clip(scaled, 0, pack.max_code).astype(np.uint16, copy=False)
    return encoded


def decode_plane(codes: np.ndarray, pack: PackSpec) -> np.ndarray:
    """Inverse of :func:`encode_plane`; nodata codes decode to NaN."""
    array = np.asarray(codes)
    values = array.astype(np.float32) * np.float32(pack.scale) + np.float32(pack.offset)
    return np.where(array == int(pack.nodata), np.float32(np.nan), values).astype(
        np.float32, copy=False
    )


def pack_spec_from_sidecar_entry(entry: dict[str, Any]) -> PackSpec:
    """Rebuild a :class:`PackSpec` from a sidecar variable/surface entry."""
    return PackSpec(
        units=str(entry.get("units", "")),
        scale=float(entry["scale"]),
        offset=float(entry["offset"]),
        nodata=int(entry.get("nodata", NODATA_CODE)),
    )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def sounding_dir(run_root: Path) -> Path:
    """Stacks live inside the run directory so retention is free (design §3)."""
    return Path(run_root) / "sounding"


def stack_paths(run_root: Path, fh: int) -> tuple[Path, Path]:
    base = sounding_dir(run_root)
    return base / f"fh{int(fh):03d}.stack.bin", base / f"fh{int(fh):03d}.stack.json"


def stack_exists(run_root: Path, fh: int) -> bool:
    stack_path, sidecar_path = stack_paths(run_root, fh)
    try:
        return stack_path.is_file() and stack_path.stat().st_size > 0 and sidecar_path.is_file()
    except OSError:
        return False


def available_stack_fhs(run_root: Path) -> list[int]:
    base = sounding_dir(run_root)
    if not base.is_dir():
        return []
    fhs: list[int] = []
    for child in sorted(base.glob("fh*.stack.bin")):
        match = re.match(r"^fh(\d+)\.stack\.bin$", child.name)
        if match is None:
            continue
        fh = int(match.group(1))
        if stack_exists(run_root, fh):
            fhs.append(fh)
    return sorted(fhs)


# ---------------------------------------------------------------------------
# GRIB search patterns (design §4 — never anchor a field name with "^")
# ---------------------------------------------------------------------------


def isobaric_search_pattern() -> str:
    """One regex covering all 5 variables x 37 levels of the ``prs`` product.

    The level list is enumerated explicitly rather than using a ``\\d+ mb``
    wildcard, which would also drag in 1013.2/75/50 mb.
    """
    elements = "|".join(var.grib_element for var in ISOBARIC_VARIABLES)
    levels = "|".join(str(level) for level in LEVELS_HPA)
    return f":({elements}):({levels}) mb:"


def surface_search_pattern() -> str:
    clauses = "|".join(
        f"{field.grib_element}:{field.level_label}" for field in SURFACE_FIELDS
    )
    return f":({clauses}):"


# ---------------------------------------------------------------------------
# Band identification
# ---------------------------------------------------------------------------

_ISOBARIC_LEVEL_RE = re.compile(r"^(\d+(?:\.\d+)?)-ISBL$")
_LEVEL_INDEX = {level: index for index, level in enumerate(LEVELS_HPA)}
_ISOBARIC_VAR_INDEX = {var.grib_element: index for index, var in enumerate(ISOBARIC_VARIABLES)}
_SURFACE_INDEX = {
    (field.grib_element, field.level_token): index
    for index, field in enumerate(SURFACE_FIELDS)
}


def plane_index_for_band_tags(tags: dict[str, Any]) -> int | None:
    """Map GDAL GRIB band tags to a stack plane, or ``None`` if not wanted.

    Band order in a Herbie subset follows the upstream idx order, which is not
    guaranteed and differs between products, so planes are addressed by tag
    identity rather than by position.
    """
    element = str(tags.get("GRIB_ELEMENT", "") or "").strip().upper()
    short_name = str(tags.get("GRIB_SHORT_NAME", "") or "").strip().upper()
    if not element or not short_name:
        return None

    surface_index = _SURFACE_INDEX.get((element, short_name))
    if surface_index is not None:
        return surface_plane_index(surface_index)

    match = _ISOBARIC_LEVEL_RE.match(short_name)
    if match is None:
        return None
    var_index = _ISOBARIC_VAR_INDEX.get(element)
    if var_index is None:
        return None

    raw_level = float(match.group(1))
    # GDAL reports ISBL levels in hPa for HRRR, but normalize defensively:
    # anything above the top of the troposphere-scale ladder is Pa.
    level_hpa = raw_level / 100.0 if raw_level > 1100.0 else raw_level
    if not float(level_hpa).is_integer():
        return None
    level_index = _LEVEL_INDEX.get(int(level_hpa))
    if level_index is None:
        return None
    return isobaric_plane_index(level_index, var_index)


def pack_for_plane(plane: int) -> PackSpec:
    if plane < N_ISOBARIC_PLANES:
        return ISOBARIC_VARIABLES[plane % len(ISOBARIC_VARIABLES)].pack
    return SURFACE_FIELDS[plane - N_ISOBARIC_PLANES].pack


def _converter_for_plane(plane: int) -> Callable[[np.ndarray], np.ndarray]:
    if plane < N_ISOBARIC_PLANES:
        return ISOBARIC_VARIABLES[plane % len(ISOBARIC_VARIABLES)].convert
    return SURFACE_FIELDS[plane - N_ISOBARIC_PLANES].convert


def _plane_label(plane: int) -> str:
    if plane < N_ISOBARIC_PLANES:
        var = ISOBARIC_VARIABLES[plane % len(ISOBARIC_VARIABLES)]
        level = LEVELS_HPA[plane // len(ISOBARIC_VARIABLES)]
        return f"{var.id}@{level}hPa"
    return SURFACE_FIELDS[plane - N_ISOBARIC_PLANES].id


# ---------------------------------------------------------------------------
# Stack assembly
# ---------------------------------------------------------------------------


def decimate(values: np.ndarray, stride: int = DECIMATION_STRIDE) -> np.ndarray:
    """Take every *stride*-th point in both axes (design §3)."""
    return np.asarray(values)[:: int(stride), :: int(stride)]


def decimated_transform(transform: Any, stride: int = DECIMATION_STRIDE) -> Any:
    """Affine for the decimated grid: ``T'(c, r) == T(stride*c, stride*r)``."""
    from rasterio.transform import Affine

    return transform * Affine.scale(float(stride), float(stride))


def _check_physical_range(values: np.ndarray, *, pack: PackSpec, label: str) -> None:
    finite = np.isfinite(values)
    if not np.any(finite):
        return
    lo = float(np.min(values[finite]))
    hi = float(np.max(values[finite]))
    if lo < pack.valid_min or hi > pack.valid_max:
        raise ValueError(
            f"Sounding plane {label} outside its physical window "
            f"[{pack.valid_min}, {pack.valid_max}] ({pack.units}): min={lo} max={hi} — "
            "likely a units mismatch in the GRIB decode"
        )


def assemble_stack(
    planes: dict[int, np.ndarray],
    *,
    stride: int = DECIMATION_STRIDE,
    require_complete: bool = True,
) -> np.ndarray:
    """Decimate, range-check, quantize and interleave planes into the stack.

    Parameters
    ----------
    planes
        ``{plane_index: full-resolution 2-D float array}`` in *physical* units.
    Returns
    -------
    np.ndarray
        uint16 array shaped ``(height, width, N_PLANES)``; its C-order buffer
        is exactly the pixel-major on-disk layout.
    """
    if not planes:
        raise ValueError("Cannot assemble a sounding stack from zero planes")
    missing = sorted(set(range(N_PLANES)) - set(planes))
    if missing and require_complete:
        raise ValueError(
            f"Sounding stack missing {len(missing)} plane(s), e.g. "
            f"{[_plane_label(plane) for plane in missing[:5]]}"
        )

    shapes = {np.asarray(plane).shape for plane in planes.values()}
    if len(shapes) != 1:
        raise ValueError(f"Sounding planes disagree on shape: {sorted(shapes)}")

    sample = decimate(np.asarray(next(iter(planes.values()))), stride)
    height, width = sample.shape
    stack = np.full((height, width, N_PLANES), NODATA_CODE, dtype=np.uint16)

    for plane, values in planes.items():
        pack = pack_for_plane(plane)
        decimated = decimate(np.asarray(values, dtype=np.float32), stride)
        _check_physical_range(decimated, pack=pack, label=_plane_label(plane))
        stack[:, :, plane] = encode_plane(decimated, pack)
    return stack


def build_sidecar(
    *,
    model: str,
    run_id: str,
    fh: int,
    run_date: datetime,
    width: int,
    height: int,
    transform: Any,
    projection: str,
    source_width: int,
    source_height: int,
    stride: int = DECIMATION_STRIDE,
) -> dict[str, Any]:
    """Self-describing sidecar — a reader must need nothing beyond this JSON."""
    valid_time = run_date + timedelta(hours=int(fh))
    if valid_time.tzinfo is None:
        valid_time = valid_time.replace(tzinfo=timezone.utc)
    return {
        "format_version": SOUNDING_FORMAT_VERSION,
        "artifact": "sounding_stack",
        "model": str(model),
        "run": str(run_id),
        "fh": int(fh),
        "valid_time": valid_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "width": int(width),
        "height": int(height),
        "transform": [float(value) for value in tuple(transform)[:6]],
        "projection": str(projection),
        "source_width": int(source_width),
        "source_height": int(source_height),
        "decimation_stride": int(stride),
        "levels_hPa": [int(level) for level in LEVELS_HPA],
        "variables": [
            {
                "id": var.id,
                "grib_element": var.grib_element,
                "units": var.pack.units,
                "scale": float(var.pack.scale),
                "offset": float(var.pack.offset),
                "nodata": int(var.pack.nodata),
            }
            for var in ISOBARIC_VARIABLES
        ],
        "surface_fields": [
            {
                "id": field.id,
                "grib_element": field.grib_element,
                "level": field.level_label,
                "units": field.pack.units,
                "scale": float(field.pack.scale),
                "offset": float(field.pack.offset),
                "nodata": int(field.pack.nodata),
            }
            for field in SURFACE_FIELDS
        ],
        "layout": "pixel_major",
        "n_planes": int(N_PLANES),
        "n_isobaric_planes": int(N_ISOBARIC_PLANES),
        "bytes_per_pixel": int(BYTES_PER_PIXEL),
        "bytes_per_sample": int(BYTES_PER_SAMPLE),
        "dtype": "uint16",
        "byte_order": "little",
        "plane_formula": "((row * width + col) * n_planes + plane) * bytes_per_sample",
        "isobaric_plane_formula": "level_index * n_variables + var_index",
        "surface_plane_offset": int(N_ISOBARIC_PLANES),
    }


def write_stack(
    *,
    run_root: Path,
    fh: int,
    stack: np.ndarray,
    sidecar: dict[str, Any],
) -> tuple[Path, Path]:
    """Write ``fhNNN.stack.bin`` + ``.json`` atomically (binary first)."""
    if stack.dtype != np.uint16:
        raise ValueError(f"Sounding stack must be uint16, got {stack.dtype}")
    if stack.ndim != 3 or stack.shape[2] != N_PLANES:
        raise ValueError(f"Sounding stack has wrong shape: {stack.shape}")

    stack_path, sidecar_path = stack_paths(run_root, fh)
    stack_path.parent.mkdir(parents=True, exist_ok=True)

    stack_tmp = stack_path.with_suffix(stack_path.suffix + ".tmp")
    stack_tmp.write_bytes(np.ascontiguousarray(stack).astype(STACK_DTYPE, copy=False).tobytes())
    stack_tmp.replace(stack_path)

    sidecar_tmp = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
    sidecar_tmp.write_text(json.dumps(sidecar, indent=2) + "\n")
    sidecar_tmp.replace(sidecar_path)
    return stack_path, sidecar_path


# ---------------------------------------------------------------------------
# Reader (used by the tests now, by the Phase 2 endpoint later)
# ---------------------------------------------------------------------------


def read_pixel_codes(stack_path: Path, *, width: int, row: int, col: int) -> np.ndarray:
    """One seek + one 380 B read -> the raw uint16 codes for a grid point."""
    offset = pixel_byte_offset(row, col, width=width)
    with open(stack_path, "rb") as handle:
        handle.seek(offset)
        payload = handle.read(BYTES_PER_PIXEL)
    if len(payload) != BYTES_PER_PIXEL:
        raise ValueError(
            f"Short read at offset {offset} in sounding stack: {stack_path} "
            f"({len(payload)} of {BYTES_PER_PIXEL} bytes)"
        )
    return np.frombuffer(payload, dtype=STACK_DTYPE)


def _finite_or_none(value: float) -> float | None:
    return None if not math.isfinite(value) else float(value)


def read_profile(stack_path: Path, sidecar: dict[str, Any], *, row: int, col: int) -> dict[str, Any]:
    """Decode one column into physical units, driven entirely by *sidecar*.

    Below-ground levels are returned as stored: masking against the surface
    pressure is a display concern (design §6), never a storage one.
    """
    width = int(sidecar["width"])
    height = int(sidecar["height"])
    if not (0 <= int(row) < height and 0 <= int(col) < width):
        raise IndexError(f"({row}, {col}) outside sounding grid {height}x{width}")

    n_planes = int(sidecar["n_planes"])
    codes = read_pixel_codes(stack_path, width=width, row=row, col=col)
    if codes.size != n_planes:
        raise ValueError(f"Sounding pixel has {codes.size} planes, sidecar declares {n_planes}")

    levels = [int(level) for level in sidecar["levels_hPa"]]
    variables = list(sidecar["variables"])
    surface_entries = list(sidecar["surface_fields"])
    n_vars = len(variables)

    profile: dict[str, Any] = {"levels_hPa": levels}
    for var_index, entry in enumerate(variables):
        pack = pack_spec_from_sidecar_entry(entry)
        plane_indices = [level_index * n_vars + var_index for level_index in range(len(levels))]
        decoded = decode_plane(codes[plane_indices], pack)
        profile[str(entry["id"])] = [_finite_or_none(value) for value in decoded]

    surface_offset = int(sidecar.get("surface_plane_offset", len(levels) * n_vars))
    surface: dict[str, float | None] = {}
    for field_index, entry in enumerate(surface_entries):
        pack = pack_spec_from_sidecar_entry(entry)
        decoded = decode_plane(codes[surface_offset + field_index : surface_offset + field_index + 1], pack)
        surface[str(entry["id"])] = _finite_or_none(float(decoded[0]))
    profile["surface"] = surface
    return profile


def read_sidecar(run_root: Path, fh: int) -> dict[str, Any]:
    _stack_path, sidecar_path = stack_paths(run_root, fh)
    return json.loads(Path(sidecar_path).read_text())


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _fetch_product_planes(
    *,
    model_id: str,
    product: str,
    search_pattern: str,
    run_date: datetime,
    fh: int,
    wanted_planes: set[int],
) -> tuple[dict[int, np.ndarray], Any, Any]:
    """Download ONE Herbie subset for *product* and split it into planes.

    Sequential and AWS-first: the priority sequence comes from the shared
    fetch layer (``CARTOSKY_HERBIE_PRIORITY`` / ``DEFAULT_HERBIE_PRIORITY``),
    and each priority is tried once before falling through.
    """
    from app.services.builder import fetch as fetch_module

    herbie_type = fetch_module._import_herbie()
    herbie_date = run_date.replace(tzinfo=None) if run_date.tzinfo else run_date
    priorities = [
        fetch_module._priority_normalized(item)
        for item in fetch_module._priority_candidates(None)
        if str(item).strip()
    ]

    work_dir = Path(tempfile.mkdtemp(prefix=f"cartosky-sounding-{product}-"))
    last_error: Exception | None = None
    try:
        for priority in priorities:
            subset_hint = work_dir / f"{model_id}.{product}.fh{int(fh):03d}.{priority}.grib2"
            try:
                herbie = fetch_module._construct_herbie(
                    herbie_type,
                    herbie_date,
                    {
                        "model": model_id,
                        "product": product,
                        "fxx": int(fh),
                        "priority": priority,
                        "verbose": False,
                    },
                )
                # Asserts the file exists and is non-empty: Herbie can return a
                # path it never wrote (design §4 gotcha).
                grib_path = fetch_module._download_herbie_subset_isolated(
                    herbie, search_pattern=search_pattern, subset_hint=subset_hint
                )
            except Exception as exc:  # noqa: BLE001 - try the next source
                last_error = exc
                logger.warning(
                    "Sounding fetch failed: model=%s product=%s fh%03d priority=%s: %s",
                    model_id,
                    product,
                    fh,
                    priority,
                    exc,
                )
                continue

            planes, crs, transform = _planes_from_grib(grib_path, wanted_planes=wanted_planes)
            logger.info(
                "Sounding fetch ok: model=%s product=%s fh%03d priority=%s planes=%d bytes=%d",
                model_id,
                product,
                fh,
                priority,
                len(planes),
                grib_path.stat().st_size,
            )
            return planes, crs, transform
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    raise RuntimeError(
        f"Sounding fetch exhausted all sources: model={model_id} product={product} "
        f"fh={fh} priorities={priorities}"
    ) from last_error


def _planes_from_grib(
    grib_path: Path, *, wanted_planes: set[int]
) -> tuple[dict[int, np.ndarray], Any, Any]:
    """Read every band of a multi-message GRIB and index it by plane."""
    import rasterio

    from app.services.builder.fetch import _read_rasterio_band

    planes: dict[int, np.ndarray] = {}
    with rasterio.open(grib_path) as src:
        band_count = int(getattr(src, "count", 0))
        if band_count <= 0:
            raise RuntimeError(f"Sounding subset contains no GRIB bands: {grib_path}")
        for band_index in range(1, band_count + 1):
            tags = src.tags(band_index)
            plane = plane_index_for_band_tags(tags)
            if plane is None or plane not in wanted_planes:
                continue
            if plane in planes:
                raise RuntimeError(
                    f"Duplicate GRIB message for sounding plane {_plane_label(plane)} in {grib_path}"
                )
            # ``_read_rasterio_band`` already masks nodata and converts K -> degC.
            planes[plane] = _converter_for_plane(plane)(
                _read_rasterio_band(src, band_index=band_index)
            )
        crs = src.crs
        transform = src.transform
    return planes, crs, transform


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_stack_for_fh(
    *,
    model_id: str,
    run_id: str,
    run_date: datetime,
    fh: int,
    run_root: Path,
    overwrite: bool = False,
) -> Path | None:
    """Fetch, pack and write the sounding stack for one forecast hour.

    Returns the stack path, or ``None`` when a usable stack already exists
    (resume idempotence). Raises on failure; the scheduler wrapper swallows.
    """
    model_norm = str(model_id).strip().lower()
    if model_norm not in SUPPORTED_MODELS:
        logger.info("Sounding build skipped: model=%s is not supported in Phase 1", model_norm)
        return None
    if not overwrite and stack_exists(run_root, fh):
        logger.debug("Sounding stack already present: run=%s fh%03d", run_id, fh)
        return None

    isobaric_planes = set(range(N_ISOBARIC_PLANES))
    surface_planes = set(range(N_ISOBARIC_PLANES, N_PLANES))

    planes, crs, transform = _fetch_product_planes(
        model_id=model_norm,
        product=ISOBARIC_PRODUCT,
        search_pattern=isobaric_search_pattern(),
        run_date=run_date,
        fh=fh,
        wanted_planes=isobaric_planes,
    )
    surface, _sfc_crs, _sfc_transform = _fetch_product_planes(
        model_id=model_norm,
        product=SURFACE_PRODUCT,
        search_pattern=surface_search_pattern(),
        run_date=run_date,
        fh=fh,
        wanted_planes=surface_planes,
    )
    planes.update(surface)

    source_height, source_width = np.asarray(next(iter(planes.values()))).shape
    stack = assemble_stack(planes)
    height, width = stack.shape[0], stack.shape[1]
    sidecar = build_sidecar(
        model=model_norm,
        run_id=run_id,
        fh=fh,
        run_date=run_date,
        width=width,
        height=height,
        transform=decimated_transform(transform),
        projection=crs.to_wkt() if hasattr(crs, "to_wkt") else str(crs),
        source_width=source_width,
        source_height=source_height,
    )
    stack_path, _sidecar_path = write_stack(run_root=run_root, fh=fh, stack=stack, sidecar=sidecar)
    logger.info(
        "Sounding stack written: run=%s fh%03d path=%s bytes=%d grid=%dx%d",
        run_id,
        fh,
        stack_path,
        stack_path.stat().st_size,
        width,
        height,
    )
    return stack_path


def build_stacks_for_run(
    *,
    model_id: str,
    run_id: str,
    run_date: datetime,
    run_root: Path,
    fhs: Iterable[int],
) -> tuple[int, int]:
    """Build every missing stack in *fhs*; never raises.

    Returns ``(built, failed)``. A stack failure must never fail the run
    publish — the raster pipeline is entirely independent of this artifact.
    """
    model_norm = str(model_id).strip().lower()
    if model_norm not in SUPPORTED_MODELS:
        logger.info(
            "Sounding pass skipped: model=%s enabled by flag but unsupported in Phase 1",
            model_norm,
        )
        return 0, 0

    built = 0
    failed = 0
    for fh in sorted({int(value) for value in fhs}):
        try:
            if build_stack_for_fh(
                model_id=model_norm,
                run_id=run_id,
                run_date=run_date,
                fh=fh,
                run_root=run_root,
            ) is not None:
                built += 1
        except Exception:
            failed += 1
            logger.exception(
                "Sounding stack build failed (run publish unaffected): model=%s run=%s fh%03d",
                model_norm,
                run_id,
                fh,
            )
    return built, failed


def manifest_section(
    *,
    run_root: Path,
    expected_fhs: Sequence[int],
) -> dict[str, Any]:
    """Top-level ``sounding`` block for the run manifest (additive)."""
    return {
        "format_version": SOUNDING_FORMAT_VERSION,
        "path_template": "sounding/fh{fh:03d}.stack.bin",
        "sidecar_template": "sounding/fh{fh:03d}.stack.json",
        "expected_fhs": [int(fh) for fh in sorted(set(int(v) for v in expected_fhs))],
        "available_fhs": available_stack_fhs(run_root),
    }


# ---------------------------------------------------------------------------
# Manual dry run
# ---------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - operator tool
    import argparse

    parser = argparse.ArgumentParser(description="Build one sounding stack for spot checks.")
    parser.add_argument("--model", default="hrrr")
    parser.add_argument("--run", required=True, help="Run id, e.g. 20260730_12z")
    parser.add_argument("--fh", type=int, required=True)
    parser.add_argument("--out", required=True, help="Run root directory to write into")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--row", type=int, default=None, help="Decimated-grid row to dump a profile for"
    )
    parser.add_argument("--col", type=int, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    run_date = datetime.strptime(args.run.replace("z", ""), "%Y%m%d_%H").replace(tzinfo=timezone.utc)
    run_root = Path(args.out)
    build_stack_for_fh(
        model_id=args.model,
        run_id=args.run,
        run_date=run_date,
        fh=args.fh,
        run_root=run_root,
        overwrite=bool(args.overwrite),
    )
    sidecar = read_sidecar(run_root, args.fh)
    print(json.dumps(sidecar, indent=2))
    if args.row is not None and args.col is not None:
        stack_path, _ = stack_paths(run_root, args.fh)
        profile = read_profile(stack_path, sidecar, row=args.row, col=args.col)
        print(json.dumps(profile, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
