"""Sounding stack artifact tests (Skew-T design 2026-07-30, Phase 1).

Covers the pieces that a later phase can silently break: packing precision,
the pixel-major offset math, sidecar self-description, flag parsing, the
Herbie search regexes, and an end-to-end build from a synthetic in-memory
dataset (no network, no GDAL).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import sounding_models
from app.services import sounding


def _tol(pack: "sounding.PackSpec") -> float:
    """Quantization error plus float32 representation slack."""
    return pack.precision * 2.0


RUN_DATE = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
RUN_ID = "20260730_12z"


# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------


def test_level_ladder_is_1000_to_100_every_25_excluding_1013() -> None:
    assert sounding.LEVELS_HPA[0] == 1000
    assert sounding.LEVELS_HPA[-1] == 100
    assert len(sounding.LEVELS_HPA) == 37
    assert list(sounding.LEVELS_HPA) == sorted(sounding.LEVELS_HPA, reverse=True)
    assert all(
        sounding.LEVELS_HPA[i] - sounding.LEVELS_HPA[i + 1] == 25
        for i in range(len(sounding.LEVELS_HPA) - 1)
    )
    # 1013.2 is sub-surface/extrapolated and must never enter the ladder.
    assert 1013 not in sounding.LEVELS_HPA


def test_plane_counts_match_the_designed_382_byte_pixel() -> None:
    # format_version 2: 185 isobaric + 6 surface planes.
    assert sounding.N_ISOBARIC_PLANES == 185
    assert sounding.N_PLANES == 191
    assert sounding.BYTES_PER_PIXEL == 382


def test_isobaric_variable_order_is_t_td_u_v_w() -> None:
    assert [var.id for var in sounding.ISOBARIC_VARIABLES] == ["t", "td", "u", "v", "w"]
    assert [var.grib_element for var in sounding.ISOBARIC_VARIABLES] == [
        "TMP",
        "DPT",
        "UGRD",
        "VGRD",
        "VVEL",
    ]


def test_surface_block_order_matches_the_spec() -> None:
    assert [field.id for field in sounding.SURFACE_FIELDS] == [
        "pres_sfc",
        "t2m",
        "td2m",
        "u10m",
        "v10m",
        # v2 append; the v1 five keep their plane indices.
        "cape_sfc",
    ]
    assert [sounding.surface_plane_index(i) for i in range(6)] == [
        185,
        186,
        187,
        188,
        189,
        190,
    ]


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pack, lo, hi, tolerance",
    [
        (sounding.TEMPERATURE_PACK, -120.0, 60.0, 0.05),
        (sounding.WIND_PACK, -163.0, 163.0, 0.05),
        (sounding.VVEL_PACK, -163.0, 163.0, 0.05),
        (sounding.PRESSURE_PACK, 300.0, 1100.0, 0.05),
    ],
)
def test_packing_round_trip_meets_precision_over_its_full_span(
    pack: sounding.PackSpec, lo: float, hi: float, tolerance: float
) -> None:
    values = np.linspace(lo, hi, 4001, dtype=np.float32)
    decoded = sounding.decode_plane(sounding.encode_plane(values, pack), pack)
    assert np.all(np.isfinite(decoded))
    assert float(np.max(np.abs(decoded - values))) <= tolerance
    # The whole span must be representable without saturating the sentinel.
    codes = sounding.encode_plane(values, pack)
    assert int(codes.max()) < pack.nodata
    assert int(codes.min()) >= 0


def test_every_pack_spec_quantizes_finer_than_the_005_target() -> None:
    for pack in (
        sounding.TEMPERATURE_PACK,
        sounding.WIND_PACK,
        sounding.VVEL_PACK,
        sounding.PRESSURE_PACK,
    ):
        assert pack.precision <= 0.05


def test_nan_encodes_to_nodata_and_decodes_back_to_nan() -> None:
    pack = sounding.TEMPERATURE_PACK
    values = np.array([np.nan, 0.0, np.inf, -np.inf, -40.0], dtype=np.float32)
    codes = sounding.encode_plane(values, pack)
    assert int(codes[0]) == sounding.NODATA_CODE
    assert int(codes[2]) == sounding.NODATA_CODE
    assert int(codes[3]) == sounding.NODATA_CODE
    decoded = sounding.decode_plane(codes, pack)
    assert np.isnan(decoded[0]) and np.isnan(decoded[2]) and np.isnan(decoded[3])
    assert decoded[1] == pytest.approx(0.0, abs=0.005)
    assert decoded[4] == pytest.approx(-40.0, abs=0.005)


def test_out_of_window_values_saturate_instead_of_wrapping() -> None:
    pack = sounding.TEMPERATURE_PACK
    codes = sounding.encode_plane(np.array([-1e6, 1e6], dtype=np.float32), pack)
    assert int(codes[0]) == 0
    assert int(codes[1]) == pack.max_code
    assert int(codes[1]) < pack.nodata


def test_pack_spec_round_trips_through_a_sidecar_entry() -> None:
    sidecar = sounding.build_sidecar(
        model="hrrr",
        run_id=RUN_ID,
        fh=6,
        run_date=RUN_DATE,
        width=4,
        height=3,
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 0.0),
        projection="EPSG:4326",
        source_width=16,
        source_height=12,
    )
    entry = sidecar["variables"][0]
    rebuilt = sounding.pack_spec_from_sidecar_entry(entry)
    assert rebuilt.scale == sounding.TEMPERATURE_PACK.scale
    assert rebuilt.offset == sounding.TEMPERATURE_PACK.offset
    assert rebuilt.nodata == sounding.TEMPERATURE_PACK.nodata


# ---------------------------------------------------------------------------
# Layout / offset math
# ---------------------------------------------------------------------------


def test_pixel_byte_offset_matches_the_design_formula() -> None:
    width = 450
    for row, col, plane in [(0, 0, 0), (0, 1, 0), (1, 0, 3), (264, 449, 190)]:
        assert sounding.pixel_byte_offset(row, col, width=width, plane=plane) == (
            ((row * width + col) * 191 + plane) * 2
        )
    # A v1 stack is still addressable from the same helper: the plane count is
    # a parameter, never an assumption (that is what keeps old runs readable).
    assert sounding.pixel_byte_offset(1, 0, width=width, plane=3, n_planes=190) == (
        ((1 * width + 0) * 190 + 3) * 2
    )


def test_isobaric_plane_index_is_level_outer_var_inner() -> None:
    assert sounding.isobaric_plane_index(0, 0) == 0  # t @ 1000 hPa
    assert sounding.isobaric_plane_index(0, 4) == 4  # w @ 1000 hPa
    assert sounding.isobaric_plane_index(1, 0) == 5  # t @ 975 hPa
    assert sounding.isobaric_plane_index(36, 4) == 184  # w @ 100 hPa


def test_planted_column_decodes_from_its_own_pixel_block(tmp_path: Path) -> None:
    """Synthetic small grid: a known pixel's block must decode to its column."""
    height, width = 3, 4
    target_row, target_col = 2, 1

    stride = 1
    planes: dict[int, np.ndarray] = {}
    # Every plane carries a distinctive per-pixel ramp so a mis-addressed read
    # cannot accidentally look correct.
    for plane in range(sounding.N_PLANES):
        pack = sounding.pack_for_plane(plane)
        base = np.arange(height * width, dtype=np.float32).reshape(height, width)
        span = (pack.valid_max - pack.valid_min) * 0.25
        values = pack.valid_min + span + base * (span / (height * width))
        planes[plane] = values
    stack = sounding.assemble_stack(planes, stride=stride)
    sidecar = sounding.build_sidecar(
        model="hrrr",
        run_id=RUN_ID,
        fh=6,
        run_date=RUN_DATE,
        width=width,
        height=height,
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 0.0),
        projection="EPSG:4326",
        source_width=width,
        source_height=height,
        stride=stride,
    )
    stack_path, sidecar_path = sounding.write_stack(
        run_root=tmp_path, fh=6, stack=stack, sidecar=sidecar
    )
    assert stack_path.stat().st_size == sounding.expected_stack_size_bytes(
        width=width, height=height
    )
    assert json.loads(sidecar_path.read_text())["n_planes"] == 191

    codes = sounding.read_pixel_codes(stack_path, width=width, row=target_row, col=target_col)
    assert codes.size == sounding.N_PLANES
    np.testing.assert_array_equal(codes, stack[target_row, target_col, :])

    profile = sounding.read_profile(stack_path, sidecar, row=target_row, col=target_col)
    assert profile["levels_hPa"] == list(sounding.LEVELS_HPA)
    for var_index, var in enumerate(sounding.ISOBARIC_VARIABLES):
        for level_index in range(len(sounding.LEVELS_HPA)):
            plane = sounding.isobaric_plane_index(level_index, var_index)
            expected = float(planes[plane][target_row, target_col])
            assert profile[var.id][level_index] == pytest.approx(
                expected, abs=_tol(var.pack)
            )
    for field_index, field in enumerate(sounding.SURFACE_FIELDS):
        plane = sounding.surface_plane_index(field_index)
        expected = float(planes[plane][target_row, target_col])
        assert profile["surface"][field.id] == pytest.approx(expected, abs=_tol(field.pack))


def test_read_profile_rejects_out_of_grid_indices(tmp_path: Path) -> None:
    height, width = 2, 2
    planes = {
        plane: np.zeros((height, width), dtype=np.float32)
        for plane in range(sounding.N_PLANES)
    }
    # Zero is outside the pressure window; give the pressure plane a real value.
    planes[sounding.surface_plane_index(0)] = np.full((height, width), 980.0, dtype=np.float32)
    stack = sounding.assemble_stack(planes, stride=1)
    sidecar = sounding.build_sidecar(
        model="hrrr",
        run_id=RUN_ID,
        fh=0,
        run_date=RUN_DATE,
        width=width,
        height=height,
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 0.0),
        projection="EPSG:4326",
        source_width=width,
        source_height=height,
        stride=1,
    )
    stack_path, _ = sounding.write_stack(run_root=tmp_path, fh=0, stack=stack, sidecar=sidecar)
    with pytest.raises(IndexError):
        sounding.read_profile(stack_path, sidecar, row=height, col=0)
    with pytest.raises(IndexError):
        sounding.read_profile(stack_path, sidecar, row=0, col=width)


def test_nodata_pixels_read_back_as_none(tmp_path: Path) -> None:
    height, width = 2, 2
    planes: dict[int, np.ndarray] = {}
    for plane in range(sounding.N_PLANES):
        pack = sounding.pack_for_plane(plane)
        values = np.full((height, width), np.nan, dtype=np.float32)
        # Keep the one valid pixel inside that plane's physical window.
        values[0, 0] = 980.0 if pack is sounding.PRESSURE_PACK else 0.0
        planes[plane] = values
    stack = sounding.assemble_stack(planes, stride=1)
    sidecar = sounding.build_sidecar(
        model="hrrr",
        run_id=RUN_ID,
        fh=0,
        run_date=RUN_DATE,
        width=width,
        height=height,
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 0.0),
        projection="EPSG:4326",
        source_width=width,
        source_height=height,
        stride=1,
    )
    stack_path, _ = sounding.write_stack(run_root=tmp_path, fh=0, stack=stack, sidecar=sidecar)

    masked = sounding.read_profile(stack_path, sidecar, row=1, col=1)
    assert all(value is None for value in masked["t"])
    assert all(value is None for value in masked["surface"].values())

    present = sounding.read_profile(stack_path, sidecar, row=0, col=0)
    assert all(value is not None for value in present["t"])
    assert present["surface"]["pres_sfc"] == pytest.approx(980.0, abs=0.01)


def test_below_ground_values_are_stored_as_is_not_masked(tmp_path: Path) -> None:
    """Masking p > surface pressure is a display concern (design §2/§6)."""
    height, width = 1, 1
    surface_pressure = 970.0
    planes: dict[int, np.ndarray] = {}
    for plane in range(sounding.N_PLANES):
        planes[plane] = np.full((height, width), 0.0, dtype=np.float32)
    planes[sounding.surface_plane_index(0)] = np.full(
        (height, width), surface_pressure, dtype=np.float32
    )
    # 1000 hPa is BELOW ground here; plant the fictitious extrapolated value.
    planes[sounding.isobaric_plane_index(0, 0)] = np.full((height, width), 39.0, dtype=np.float32)
    planes[sounding.surface_plane_index(1)] = np.full((height, width), 36.7, dtype=np.float32)

    stack = sounding.assemble_stack(planes, stride=1)
    sidecar = sounding.build_sidecar(
        model="hrrr",
        run_id=RUN_ID,
        fh=6,
        run_date=RUN_DATE,
        width=width,
        height=height,
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 0.0),
        projection="EPSG:4326",
        source_width=width,
        source_height=height,
        stride=1,
    )
    stack_path, _ = sounding.write_stack(run_root=tmp_path, fh=6, stack=stack, sidecar=sidecar)
    profile = sounding.read_profile(stack_path, sidecar, row=0, col=0)

    assert profile["surface"]["pres_sfc"] == pytest.approx(surface_pressure, abs=0.01)
    assert profile["t"][0] == pytest.approx(39.0, abs=0.005)  # 1000 hPa kept
    assert profile["surface"]["t2m"] == pytest.approx(36.7, abs=0.005)


def test_assemble_stack_requires_every_plane() -> None:
    planes = {
        plane: np.zeros((2, 2), dtype=np.float32) for plane in range(sounding.N_PLANES - 1)
    }
    with pytest.raises(ValueError, match="missing"):
        sounding.assemble_stack(planes, stride=1)


def test_assemble_stack_rejects_a_units_mismatch() -> None:
    planes = {
        plane: np.zeros((2, 2), dtype=np.float32) for plane in range(sounding.N_PLANES)
    }
    planes[sounding.surface_plane_index(0)] = np.full((2, 2), 980.0, dtype=np.float32)
    # Kelvin instead of degC on a temperature plane.
    planes[sounding.isobaric_plane_index(0, 0)] = np.full((2, 2), 293.15, dtype=np.float32)
    with pytest.raises(ValueError, match="physical window"):
        sounding.assemble_stack(planes, stride=1)


# ---------------------------------------------------------------------------
# Decimation
# ---------------------------------------------------------------------------


def test_decimation_shrinks_the_hrrr_grid_to_450x265() -> None:
    values = np.zeros((1059, 1799), dtype=np.float32)
    assert sounding.decimate(values).shape == (265, 450)


def test_decimated_affine_matches_the_full_affine_at_stride_multiples() -> None:
    from rasterio.transform import Affine

    full = Affine(3000.0, 0.0, -2699020.0, 0.0, -3000.0, 1588193.0)
    decimated = sounding.decimated_transform(full, sounding.DECIMATION_STRIDE)

    for row, col in [(0, 0), (1, 1), (7, 13), (264, 449)]:
        assert decimated * (col, row) == pytest.approx(
            full * (col * sounding.DECIMATION_STRIDE, row * sounding.DECIMATION_STRIDE)
        )


def test_decimation_and_affine_agree_on_the_sampled_point() -> None:
    from rasterio.transform import Affine

    full = Affine(3000.0, 0.0, -2699020.0, 0.0, -3000.0, 1588193.0)
    values = np.arange(20 * 24, dtype=np.float32).reshape(20, 24)
    decimated = sounding.decimate(values)
    decimated_affine = sounding.decimated_transform(full)

    row, col = 3, 2
    assert decimated[row, col] == values[
        row * sounding.DECIMATION_STRIDE, col * sounding.DECIMATION_STRIDE
    ]
    assert decimated_affine * (col, row) == pytest.approx(
        full * (col * sounding.DECIMATION_STRIDE, row * sounding.DECIMATION_STRIDE)
    )


# ---------------------------------------------------------------------------
# Sidecar completeness
# ---------------------------------------------------------------------------


def test_sidecar_is_fully_self_describing() -> None:
    sidecar = sounding.build_sidecar(
        model="hrrr",
        run_id=RUN_ID,
        fh=6,
        run_date=RUN_DATE,
        width=450,
        height=265,
        transform=(12000.0, 0.0, -2699020.0, 0.0, -12000.0, 1588193.0),
        projection="PROJCS[...]",
        source_width=1799,
        source_height=1059,
    )

    required = {
        "format_version",
        "fh",
        "valid_time",
        "width",
        "height",
        "transform",
        "projection",
        "levels_hPa",
        "variables",
        "surface_fields",
        "n_planes",
        "bytes_per_pixel",
    }
    assert required <= set(sidecar)

    assert sidecar["format_version"] == 2
    assert sidecar["fh"] == 6
    assert sidecar["valid_time"] == "2026-07-30T18:00:00Z"
    assert sidecar["width"] == 450 and sidecar["height"] == 265
    assert len(sidecar["transform"]) == 6
    assert sidecar["levels_hPa"] == list(sounding.LEVELS_HPA)
    assert sidecar["n_planes"] == 191
    assert sidecar["bytes_per_pixel"] == 382
    assert sidecar["dtype"] == "uint16"
    assert sidecar["byte_order"] == "little"
    assert sidecar["decimation_stride"] == 4

    for entry in list(sidecar["variables"]) + list(sidecar["surface_fields"]):
        assert {"id", "units", "scale", "offset", "nodata"} <= set(entry)
        assert entry["units"]
        assert entry["nodata"] == 65535

    assert [entry["id"] for entry in sidecar["variables"]] == ["t", "td", "u", "v", "w"]
    assert [entry["units"] for entry in sidecar["variables"]] == [
        "degC",
        "degC",
        "m s-1",
        "m s-1",
        "Pa s-1",
    ]
    assert sidecar["surface_fields"][0]["units"] == "hPa"


def test_sidecar_is_json_serializable() -> None:
    sidecar = sounding.build_sidecar(
        model="hrrr",
        run_id=RUN_ID,
        fh=0,
        run_date=RUN_DATE,
        width=2,
        height=2,
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 0.0),
        projection="EPSG:4326",
        source_width=2,
        source_height=2,
    )
    assert json.loads(json.dumps(sidecar)) == sidecar


# ---------------------------------------------------------------------------
# Herbie search patterns / band identification
# ---------------------------------------------------------------------------


def test_isobaric_pattern_matches_all_wanted_levels_and_nothing_else() -> None:
    pattern = re.compile(sounding.isobaric_search_pattern())

    for level in sounding.LEVELS_HPA:
        for element in ("TMP", "DPT", "UGRD", "VGRD", "VVEL"):
            line = f"31:12345678:d=2026073012:{element}:{level} mb:6 hour fcst:"
            assert pattern.search(line), line

    # Excluded by design: extrapolated 1013.2, the 75/50 mb tail, RH and HGT.
    for line in [
        "1:0:d=2026073012:TMP:1013.2 mb:6 hour fcst:",
        "2:0:d=2026073012:TMP:75 mb:6 hour fcst:",
        "3:0:d=2026073012:TMP:50 mb:6 hour fcst:",
        "4:0:d=2026073012:RH:500 mb:6 hour fcst:",
        "5:0:d=2026073012:HGT:500 mb:6 hour fcst:",
        "6:0:d=2026073012:TMP:2 m above ground:6 hour fcst:",
    ]:
        assert not pattern.search(line), line


def test_surface_pattern_matches_the_five_surface_fields_only() -> None:
    pattern = re.compile(sounding.surface_search_pattern())

    for line in [
        "1:0:d=2026073012:PRES:surface:6 hour fcst:",
        "2:0:d=2026073012:TMP:2 m above ground:6 hour fcst:",
        "3:0:d=2026073012:DPT:2 m above ground:6 hour fcst:",
        "4:0:d=2026073012:UGRD:10 m above ground:6 hour fcst:",
        "5:0:d=2026073012:VGRD:10 m above ground:6 hour fcst:",
    ]:
        assert pattern.search(line), line

    for line in [
        "6:0:d=2026073012:TMP:500 mb:6 hour fcst:",
        "7:0:d=2026073012:PRES:cloud base:6 hour fcst:",
        "8:0:d=2026073012:UGRD:80 m above ground:6 hour fcst:",
    ]:
        assert not pattern.search(line), line


def test_search_patterns_are_never_anchored() -> None:
    """Herbie matches the whole idx line; a "^" anchor silently matches zero."""
    assert not sounding.isobaric_search_pattern().startswith("^")
    assert not sounding.surface_search_pattern().startswith("^")


@pytest.mark.parametrize(
    "tags, expected",
    [
        ({"GRIB_ELEMENT": "TMP", "GRIB_SHORT_NAME": "1000-ISBL"}, 0),
        ({"GRIB_ELEMENT": "VVEL", "GRIB_SHORT_NAME": "1000-ISBL"}, 4),
        ({"GRIB_ELEMENT": "TMP", "GRIB_SHORT_NAME": "975-ISBL"}, 5),
        ({"GRIB_ELEMENT": "VVEL", "GRIB_SHORT_NAME": "100-ISBL"}, 184),
        ({"GRIB_ELEMENT": "PRES", "GRIB_SHORT_NAME": "0-SFC"}, 185),
        ({"GRIB_ELEMENT": "TMP", "GRIB_SHORT_NAME": "2-HTGL"}, 186),
        ({"GRIB_ELEMENT": "DPT", "GRIB_SHORT_NAME": "2-HTGL"}, 187),
        ({"GRIB_ELEMENT": "UGRD", "GRIB_SHORT_NAME": "10-HTGL"}, 188),
        ({"GRIB_ELEMENT": "VGRD", "GRIB_SHORT_NAME": "10-HTGL"}, 189),
        # Pa-reported ISBL levels are normalized.
        ({"GRIB_ELEMENT": "TMP", "GRIB_SHORT_NAME": "50000-ISBL"}, 100),
        # Not wanted.
        ({"GRIB_ELEMENT": "RH", "GRIB_SHORT_NAME": "500-ISBL"}, None),
        ({"GRIB_ELEMENT": "TMP", "GRIB_SHORT_NAME": "1013.2-ISBL"}, None),
        ({"GRIB_ELEMENT": "TMP", "GRIB_SHORT_NAME": "50-ISBL"}, None),
        ({"GRIB_ELEMENT": "TMP", "GRIB_SHORT_NAME": "80-HTGL"}, None),
        ({}, None),
    ],
)
def test_plane_index_for_band_tags(tags: dict[str, str], expected: int | None) -> None:
    assert sounding.plane_index_for_band_tags(tags) == expected


def test_every_isobaric_plane_is_reachable_from_band_tags() -> None:
    seen = set()
    for level in sounding.LEVELS_HPA:
        for var in sounding.ISOBARIC_VARIABLES:
            plane = sounding.plane_index_for_band_tags(
                {"GRIB_ELEMENT": var.grib_element, "GRIB_SHORT_NAME": f"{level}-ISBL"}
            )
            assert plane is not None
            seen.add(plane)
    assert seen == set(range(sounding.N_ISOBARIC_PLANES))


# ---------------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------------


def test_sounding_models_defaults_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARTOSKY_SOUNDING_MODELS", raising=False)
    assert sounding_models() == frozenset()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("hrrr", {"hrrr"}),
        ("HRRR", {"hrrr"}),
        (" hrrr , gfs ", {"hrrr", "gfs"}),
        ("hrrr,,gfs", {"hrrr", "gfs"}),
        ("   ", set()),
        ("", set()),
    ],
)
def test_sounding_models_parsing(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: set[str]
) -> None:
    monkeypatch.setenv("CARTOSKY_SOUNDING_MODELS", raw)
    assert sounding_models() == frozenset(expected)


def test_supported_models_are_exactly_the_registry() -> None:
    # Phase 6 added GFS + ECMWF; SUPPORTED_MODELS is derived from MODEL_SPECS so
    # a new row cannot be half-wired. The env flag remains the deployment gate.
    assert sounding.SUPPORTED_MODELS == frozenset({"hrrr", "gfs", "ecmwf"})
    assert sounding.SUPPORTED_MODELS == frozenset(sounding.MODEL_SPECS)


def test_build_stacks_for_run_skips_unsupported_models(tmp_path: Path) -> None:
    built, failed = sounding.build_stacks_for_run(
        model_id="nam",
        run_id=RUN_ID,
        run_date=RUN_DATE,
        run_root=tmp_path,
        fhs=[0, 1, 2],
    )
    assert (built, failed) == (0, 0)
    assert not sounding.sounding_dir(tmp_path).exists()


# ---------------------------------------------------------------------------
# Artifact discovery / resume
# ---------------------------------------------------------------------------


def _write_minimal_stack(run_root: Path, fh: int, *, height: int = 2, width: int = 2) -> None:
    planes: dict[int, np.ndarray] = {
        plane: np.zeros((height, width), dtype=np.float32)
        for plane in range(sounding.N_PLANES)
    }
    planes[sounding.surface_plane_index(0)] = np.full((height, width), 1000.0, dtype=np.float32)
    stack = sounding.assemble_stack(planes, stride=1)
    sidecar = sounding.build_sidecar(
        model="hrrr",
        run_id=RUN_ID,
        fh=fh,
        run_date=RUN_DATE,
        width=width,
        height=height,
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 0.0),
        projection="EPSG:4326",
        source_width=width,
        source_height=height,
        stride=1,
    )
    sounding.write_stack(run_root=run_root, fh=fh, stack=stack, sidecar=sidecar)


def test_stack_exists_and_available_fhs(tmp_path: Path) -> None:
    assert sounding.available_stack_fhs(tmp_path) == []
    _write_minimal_stack(tmp_path, 0)
    _write_minimal_stack(tmp_path, 12)
    assert sounding.available_stack_fhs(tmp_path) == [0, 12]
    assert sounding.stack_exists(tmp_path, 0)
    assert not sounding.stack_exists(tmp_path, 6)

    # A stack whose sidecar vanished is not usable and must not be advertised.
    sounding.stack_paths(tmp_path, 12)[1].unlink()
    assert sounding.available_stack_fhs(tmp_path) == [0]


def test_build_stack_for_fh_is_a_no_op_when_the_stack_exists(tmp_path: Path) -> None:
    _write_minimal_stack(tmp_path, 3)
    # No network mock needed: a fetch attempt here would blow up.
    assert (
        sounding.build_stack_for_fh(
            model_id="hrrr",
            run_id=RUN_ID,
            run_date=RUN_DATE,
            fh=3,
            run_root=tmp_path,
        )
        is None
    )


def test_manifest_section_reports_expected_and_available(tmp_path: Path) -> None:
    _write_minimal_stack(tmp_path, 0)
    _write_minimal_stack(tmp_path, 1)
    section = sounding.manifest_section(run_root=tmp_path, expected_fhs=[2, 0, 1, 1])
    assert section == {
        "format_version": 2,
        "path_template": "sounding/fh{fh:03d}.stack.bin",
        "sidecar_template": "sounding/fh{fh:03d}.stack.json",
        "expected_fhs": [0, 1, 2],
        "available_fhs": [0, 1],
    }


# ---------------------------------------------------------------------------
# Integration: whole builder against a synthetic in-memory dataset
# ---------------------------------------------------------------------------


class _FakeCRS:
    def to_wkt(self) -> str:
        return 'PROJCS["fake-lcc"]'


def _synthetic_planes(height: int, width: int) -> dict[int, np.ndarray]:
    """Physically plausible planes with a horizontal gradient per level."""
    rng = np.random.default_rng(20260730)
    xx, yy = np.meshgrid(
        np.linspace(0.0, 1.0, width, dtype=np.float32),
        np.linspace(0.0, 1.0, height, dtype=np.float32),
    )
    planes: dict[int, np.ndarray] = {}
    for level_index, level in enumerate(sounding.LEVELS_HPA):
        # A crude but monotone-ish lapse: warm near the ground, cold at 100 hPa.
        temperature = -60.0 + (level - 100.0) * (90.0 / 900.0) + 4.0 * xx - 3.0 * yy
        planes[sounding.isobaric_plane_index(level_index, 0)] = temperature.astype(np.float32)
        planes[sounding.isobaric_plane_index(level_index, 1)] = (
            temperature - 5.0 - 2.0 * yy
        ).astype(np.float32)
        planes[sounding.isobaric_plane_index(level_index, 2)] = (
            10.0 + 0.02 * (1000.0 - level) + rng.normal(0.0, 0.5, temperature.shape)
        ).astype(np.float32)
        planes[sounding.isobaric_plane_index(level_index, 3)] = (
            (-4.0 + 0.01 * (1000.0 - level)) * np.ones_like(temperature)
        ).astype(np.float32)
        planes[sounding.isobaric_plane_index(level_index, 4)] = (
            -0.3 * float(np.sin(level_index)) * np.ones_like(temperature)
        ).astype(np.float32)

    planes[sounding.surface_plane_index(0)] = (975.0 - 40.0 * yy).astype(np.float32)
    planes[sounding.surface_plane_index(1)] = (30.0 + 4.0 * xx).astype(np.float32)
    planes[sounding.surface_plane_index(2)] = (17.0 + 2.0 * xx).astype(np.float32)
    planes[sounding.surface_plane_index(3)] = (6.0 * np.ones_like(xx)).astype(np.float32)
    planes[sounding.surface_plane_index(4)] = (-2.0 * np.ones_like(xx)).astype(np.float32)
    # v2: surface CAPE, J/kg.
    planes[sounding.surface_plane_index(5)] = (250.0 + 900.0 * xx).astype(np.float32)
    return planes


def test_end_to_end_build_from_a_synthetic_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full builder path with the two product fetches stubbed out."""
    from rasterio.transform import Affine

    source_height, source_width = 40, 48  # -> 10 x 12 after stride 4
    planes = _synthetic_planes(source_height, source_width)
    full_transform = Affine(3000.0, 0.0, -2699020.0, 0.0, -3000.0, 1588193.0)
    calls: list[tuple[str, str]] = []

    def _fake_fetch(*, model_id, product, search_pattern, run_date, fh, wanted_planes, spec=None):
        calls.append((product, search_pattern))
        subset = {plane: planes[plane] for plane in sorted(wanted_planes)}
        return subset, _FakeCRS(), full_transform

    monkeypatch.setattr(sounding, "_fetch_product_planes", _fake_fetch)

    stack_path = sounding.build_stack_for_fh(
        model_id="hrrr",
        run_id=RUN_ID,
        run_date=RUN_DATE,
        fh=6,
        run_root=tmp_path,
    )
    assert stack_path is not None

    # Exactly one download per product per fh (design §4).
    assert [product for product, _ in calls] == ["prs", "sfc"]

    sidecar = sounding.read_sidecar(tmp_path, 6)
    assert sidecar["width"] == 12 and sidecar["height"] == 10
    assert sidecar["source_width"] == source_width
    assert sidecar["source_height"] == source_height
    assert sidecar["projection"] == 'PROJCS["fake-lcc"]'
    assert stack_path.stat().st_size == sounding.expected_stack_size_bytes(width=12, height=10)

    decimated_affine = Affine(*sidecar["transform"])
    assert decimated_affine * (3, 2) == pytest.approx(full_transform * (12, 8))

    row, col = 7, 5
    profile = sounding.read_profile(stack_path, sidecar, row=row, col=col)
    src_row, src_col = row * 4, col * 4
    for var_index, var in enumerate(sounding.ISOBARIC_VARIABLES):
        for level_index in range(len(sounding.LEVELS_HPA)):
            expected = float(planes[sounding.isobaric_plane_index(level_index, var_index)][src_row, src_col])
            assert profile[var.id][level_index] == pytest.approx(expected, abs=_tol(var.pack))
    for field_index, field in enumerate(sounding.SURFACE_FIELDS):
        expected = float(planes[sounding.surface_plane_index(field_index)][src_row, src_col])
        assert profile["surface"][field.id] == pytest.approx(expected, abs=_tol(field.pack))

    # Re-running is a no-op (resume idempotence).
    calls.clear()
    assert (
        sounding.build_stack_for_fh(
            model_id="hrrr", run_id=RUN_ID, run_date=RUN_DATE, fh=6, run_root=tmp_path
        )
        is None
    )
    assert calls == []


def test_build_stacks_for_run_never_raises_on_a_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs):
        raise RuntimeError("upstream is down")

    monkeypatch.setattr(sounding, "_fetch_product_planes", _boom)
    built, failed = sounding.build_stacks_for_run(
        model_id="hrrr",
        run_id=RUN_ID,
        run_date=RUN_DATE,
        run_root=tmp_path,
        fhs=[0, 1],
    )
    assert built == 0
    assert failed == 2
    assert sounding.available_stack_fhs(tmp_path) == []


# ---------------------------------------------------------------------------
# format_version 1 / 2 compatibility (Phase 4)
# ---------------------------------------------------------------------------


def _write_v1_stack(run_root: Path, fh: int, *, height: int, width: int) -> tuple[Path, dict]:
    """Write a stack in the RETIRED v1 layout: 190 planes, no ``cape_sfc``.

    Deliberately hand-rolled rather than produced by the current writer — the
    point of the test is that today's reader can still decode bytes written by
    code that no longer exists.
    """
    v1_surface = sounding.SURFACE_FIELDS[:5]
    n_planes = sounding.N_ISOBARIC_PLANES + len(v1_surface)
    assert n_planes == 190

    values: dict[int, float] = {}
    codes = np.zeros((height, width, n_planes), dtype=np.uint16)
    for plane in range(n_planes):
        pack = sounding.pack_for_plane(plane)
        value = pack.valid_min + (pack.valid_max - pack.valid_min) * 0.4 + plane * pack.scale
        values[plane] = value
        codes[:, :, plane] = sounding.encode_plane(
            np.full((height, width), value, dtype=np.float32), pack
        )

    sidecar = sounding.build_sidecar(
        model="hrrr",
        run_id=RUN_ID,
        fh=fh,
        run_date=RUN_DATE,
        width=width,
        height=height,
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 0.0),
        projection="EPSG:4326",
        source_width=width,
        source_height=height,
        stride=1,
    )
    sidecar["format_version"] = 1
    sidecar["surface_fields"] = sidecar["surface_fields"][:5]
    sidecar["n_planes"] = n_planes
    sidecar["bytes_per_pixel"] = n_planes * sounding.BYTES_PER_SAMPLE

    stack_path, sidecar_path = sounding.stack_paths(run_root, fh)
    stack_path.parent.mkdir(parents=True, exist_ok=True)
    stack_path.write_bytes(np.ascontiguousarray(codes).astype(sounding.STACK_DTYPE).tobytes())
    sidecar_path.write_text(json.dumps(sidecar))
    return stack_path, {"sidecar": sidecar, "values": values}


def test_reader_decodes_a_v1_stack_with_the_v2_module(tmp_path: Path) -> None:
    height, width = 4, 5
    stack_path, planted = _write_v1_stack(tmp_path, 3, height=height, width=width)
    sidecar = planted["sidecar"]

    assert stack_path.stat().st_size == sounding.expected_stack_size_bytes(
        width=width, height=height, n_planes=190
    )
    profile = sounding.read_profile(stack_path, sidecar, row=2, col=3)

    for var_index, var in enumerate(sounding.ISOBARIC_VARIABLES):
        for level_index in range(len(sounding.LEVELS_HPA)):
            plane = sounding.isobaric_plane_index(level_index, var_index)
            assert profile[var.id][level_index] == pytest.approx(
                planted["values"][plane], abs=_tol(var.pack)
            )
    for field_index, field in enumerate(sounding.SURFACE_FIELDS[:5]):
        plane = sounding.surface_plane_index(field_index)
        assert profile["surface"][field.id] == pytest.approx(
            planted["values"][plane], abs=_tol(field.pack)
        )
    # The v2-only field is simply absent — never a fabricated zero.
    assert "cape_sfc" not in profile["surface"]


def test_v2_stack_carries_surface_cape(tmp_path: Path) -> None:
    height, width = 3, 3
    planes = {
        plane: np.zeros((height, width), dtype=np.float32)
        for plane in range(sounding.N_PLANES)
    }
    planes[sounding.surface_plane_index(0)] = np.full((height, width), 968.5, dtype=np.float32)
    planes[sounding.surface_plane_index(5)] = np.full((height, width), 1234.5, dtype=np.float32)
    stack = sounding.assemble_stack(planes, stride=1)
    sidecar = sounding.build_sidecar(
        model="hrrr",
        run_id=RUN_ID,
        fh=0,
        run_date=RUN_DATE,
        width=width,
        height=height,
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 0.0),
        projection="EPSG:4326",
        source_width=width,
        source_height=height,
        stride=1,
    )
    stack_path, _ = sounding.write_stack(run_root=tmp_path, fh=0, stack=stack, sidecar=sidecar)

    assert sidecar["format_version"] == 2
    assert [entry["id"] for entry in sidecar["surface_fields"]][-1] == "cape_sfc"
    profile = sounding.read_profile(stack_path, sidecar, row=1, col=2)
    # 0.15 J/kg quantization -> 0.075 worst case.
    assert profile["surface"]["cape_sfc"] == pytest.approx(1234.5, abs=0.075)


def test_surface_cape_joins_the_grib_search_and_band_mapping() -> None:
    pattern = sounding.surface_search_pattern()
    assert "CAPE:surface" in pattern
    assert not pattern.startswith("^")
    assert sounding.plane_index_for_band_tags(
        {"GRIB_ELEMENT": "CAPE", "GRIB_SHORT_NAME": "0-SFC"}
    ) == sounding.surface_plane_index(5)
    # Surface pressure shares the level token; the element keeps them apart.
    assert sounding.plane_index_for_band_tags(
        {"GRIB_ELEMENT": "PRES", "GRIB_SHORT_NAME": "0-SFC"}
    ) == sounding.surface_plane_index(0)
    # The mixed-layer CAPE messages in the same product must not be captured.
    assert sounding.plane_index_for_band_tags(
        {"GRIB_ELEMENT": "CAPE", "GRIB_SHORT_NAME": "18000-0-SPDL"}
    ) is None
