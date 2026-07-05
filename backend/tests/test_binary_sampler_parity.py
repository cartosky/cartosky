"""Layer 2 binary-sampler tests for the value COG -> grid binary migration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import Affine, from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_binary_sampler_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import main as main_module
from app.models.registry import MODEL_REGISTRY
from app.services.builder.cog_writer import compute_transform_and_shape, get_grid_params
from app.services.grid import _PACKING_BY_MODEL_VAR, write_grid_frame_for_run_root
from app.services.grid_display_prep import (
    grid_display_prep_config,
    prepare_grid_display_values,
    sampling_tolerance_group,
)
from app.services.sampling import (
    read_binary_sample_value,
    sample_binary_value,
    sample_binary_point_value,
    sample_point_value,
)

# Default fixture geometry for the original GFS-era tests below.
_DEFAULT_TRANSFORM = from_origin(-101.0, 46.0, 1.0, 1.0)
_DEFAULT_PROJECTION = "EPSG:4326"


def _write_value_cog(
    path: Path,
    values: np.ndarray,
    *,
    transform: Affine = _DEFAULT_TRANSFORM,
    projection: str = _DEFAULT_PROJECTION,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs=projection,
        transform=transform,
        nodata=np.nan,
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def _write_pair(
    tmp_path: Path,
    *,
    model: str,
    var: str,
    values: np.ndarray,
    transform: Affine = _DEFAULT_TRANSFORM,
    projection: str = _DEFAULT_PROJECTION,
) -> tuple[Path, Path, Path]:
    run_root = tmp_path / "published" / model / "20260630_00z"
    var_dir = run_root / var
    cog_path = var_dir / "fh000.val.cog.tif"
    _write_value_cog(cog_path, values, transform=transform, projection=projection)
    write_grid_frame_for_run_root(
        run_root=run_root,
        model=model,
        var=var,
        fh=0,
        values=values,
        transform=transform,
        projection=projection,
    )
    return (
        cog_path,
        var_dir / "grid" / "fh000.l0.u16.bin",
        var_dir / "grid" / "fh000.l0.meta.json",
    )


def _meta_index(meta_path: Path, *, lon: float, lat: float) -> tuple[int, int]:
    meta = json.loads(meta_path.read_text())
    transform = rasterio.Affine(*meta["transform"])
    projection = str(meta.get("projection") or "EPSG:4326")
    if projection.upper() == "EPSG:4326":
        x, y = float(lon), float(lat)
    else:
        x, y = Transformer.from_crs("EPSG:4326", projection, always_xy=True).transform(lon, lat)
    col_f, row_f = ~transform * (x, y)
    return int(np.floor(row_f)), int(np.floor(col_f))


def test_binary_sampler_matches_cog_for_unscaled_variable_and_oob(tmp_path: Path) -> None:
    values = np.array(
        [
            [1.34, 2.21, 3.09],
            [4.04, np.nan, 6.52],
            [7.77, 8.88, 9.99],
        ],
        dtype=np.float32,
    )
    cog_path, frame_path, meta_path = _write_pair(
        tmp_path,
        model="gfs",
        var="tmp2m",
        values=values,
    )

    assert sample_binary_point_value(
        frame_path,
        meta_path,
        model="gfs",
        var="tmp2m",
        lat=45.5,
        lon=-100.5,
    ) == sample_point_value(cog_path, lat=45.5, lon=-100.5)

    raw, no_data = read_binary_sample_value(
        frame_path,
        meta_path,
        model="gfs",
        var="tmp2m",
        lat=44.5,
        lon=-99.5,
    )
    assert raw is None
    assert no_data is True

    raw, no_data = read_binary_sample_value(
        frame_path,
        meta_path,
        model="gfs",
        var="tmp2m",
        lat=60.0,
        lon=-120.0,
    )
    assert raw is None
    assert no_data is True


def test_binary_sampler_reads_display_prepped_continuous_upscale(tmp_path: Path) -> None:
    values = np.array(
        [
            [0.00, 0.30, 0.60],
            [0.90, 1.20, 1.50],
            [1.80, 2.10, 2.40],
        ],
        dtype=np.float32,
    )
    cog_path, frame_path, meta_path = _write_pair(
        tmp_path,
        model="gfs",
        var="precip_total",
        values=values,
    )

    lat = 45.75
    lon = -100.25
    raw, no_data = read_binary_sample_value(
        frame_path,
        meta_path,
        model="gfs",
        var="precip_total",
        lat=lat,
        lon=lon,
    )

    display_values, prep_meta = prepare_grid_display_values(model="gfs", var="precip_total", values=values)
    row, col = _meta_index(meta_path, lon=lon, lat=lat)
    expected = float(display_values[row, col])
    assert prep_meta is not None
    assert prep_meta["upscale_factor"] == 3
    assert no_data is False
    assert raw == pytest.approx(expected, abs=0.005)

    # The COG still samples the original lower-resolution field during canary.
    # For continuous 3x display-prepped vars, exact equality is not required.
    cog_value = sample_point_value(cog_path, lat=lat, lon=lon)
    assert cog_value is not None
    assert raw is not None
    assert abs(round(raw, 1) - cog_value) <= 0.5


def test_binary_sampler_reads_display_prepped_categorical_upscale(tmp_path: Path) -> None:
    values = np.array(
        [
            [10.0, 20.0],
            [30.0, 40.0],
        ],
        dtype=np.float32,
    )
    _cog_path, frame_path, meta_path = _write_pair(
        tmp_path,
        model="gfs",
        var="ptype_intensity",
        values=values,
    )

    raw, no_data = read_binary_sample_value(
        frame_path,
        meta_path,
        model="gfs",
        var="ptype_intensity",
        lat=44.25,
        lon=-99.25,
    )

    display_values, prep_meta = prepare_grid_display_values(model="gfs", var="ptype_intensity", values=values)
    row, col = _meta_index(meta_path, lon=-99.25, lat=44.25)
    assert prep_meta is not None
    assert prep_meta["upscale_factor"] == 3
    assert prep_meta["categorical_nearest"] is True
    assert no_data is False
    assert raw == float(display_values[row, col])
    assert raw == 40.0


def test_binary_sampler_rejects_unknown_meta_format_version(tmp_path: Path) -> None:
    values = np.array([[32.0]], dtype=np.float32)
    _cog_path, frame_path, meta_path = _write_pair(
        tmp_path,
        model="gfs",
        var="tmp2m",
        values=values,
    )
    meta = json.loads(meta_path.read_text())
    meta["format_version"] = 999
    meta_path.write_text(json.dumps(meta))

    with pytest.raises(ValueError, match="Unsupported grid frame format_version"):
        read_binary_sample_value(
            frame_path,
            meta_path,
            model="gfs",
            var="tmp2m",
            lat=45.5,
            lon=-100.5,
        )


def test_sample_binary_value_resolves_published_grid_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = "gfs"
    run = "20260630_00z"
    var = "tmp2m"
    values = np.array([[32.0, 40.5]], dtype=np.float32)
    _write_pair(tmp_path, model=model, var=var, values=values)

    manifests_root = tmp_path / "manifests"
    manifest_dir = manifests_root / model
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run}.json").write_text(
        json.dumps({"variables": {var: {"expected_frames": 1, "available_frames": 1, "frames": [{"fh": 0}]}}})
    )
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", tmp_path / "published")
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", manifests_root)
    main_module._manifest_cache.clear()

    present, value = sample_binary_value(
        model,
        run,
        var,
        0,
        lat=45.5,
        lon=-100.5,
    )

    assert present is True
    assert value == 32.0


def test_sample_binary_value_decodes_alias_under_runtime_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression test for the requested-vs-runtime variable id split: "t2m" is
    # a real GFS alias for "tmp2m". The frame is published (and packed) under
    # the runtime id, so the decode packing lookup must use that id too — with
    # the requested alias, the packing entry ("gfs", "t2m") does not exist and
    # the sample silently degrades to (True, None) instead of a value.
    model = "gfs"
    run = "20260630_00z"
    values = np.array([[32.0, 40.5]], dtype=np.float32)
    _write_pair(tmp_path, model=model, var="tmp2m", values=values)
    monkeypatch.setattr(main_module, "PUBLISHED_ROOT", tmp_path / "published")
    monkeypatch.setattr(main_module, "MANIFESTS_ROOT", tmp_path / "manifests")
    main_module._manifest_cache.clear()

    canonical = sample_binary_value(model, run, "tmp2m", 0, lat=45.5, lon=-100.5)
    alias = sample_binary_value(model, run, "t2m", 0, lat=45.5, lon=-100.5)

    assert canonical == (True, 32.0)
    assert alias == canonical


# ---------------------------------------------------------------------------
# Phase G — HRRR / NBM parity coverage (migration plan, Phase G checklist
# item 5). Variables and tolerance groups are derived from the packing table
# and display-prep config — never hardcoded — via the shared classifier the
# canary script also uses. Model names appear only in the parameterization
# data below, never in helper logic.
# ---------------------------------------------------------------------------

PHASE_G_MODELS = ("hrrr", "nbm")

# Ensemble models from the "Phase G audit — GEFS and EPS static readiness"
# section. Unlike PHASE_G_MODELS (whose full packed lists are parity-tested),
# their parity scope is the canary's own comparison scope: GEFS/EPS publish
# exclusively under runtime __mean artifact ids, so the packed bare-id dead
# aliases (and EPS's stale hgt500__mean entry) have no on-disk artifact pair
# to compare.
PHASE_G_ENSEMBLE_MODELS = ("gefs", "eps")


def _model_scope(model: str) -> list[str]:
    return sorted(var for (mdl, var) in _PACKING_BY_MODEL_VAR if mdl == model)


def _canary_scope(model: str) -> list[str]:
    """Packed variables intersected with the canary's own scope logic, so
    Layer 2 coverage cannot silently drift from what Layer 3 exercises."""
    from backend.scripts.canary_binary_sampler import _scope_for_model

    return list(_scope_for_model(model)[0])


def _tolerance_group(model: str, var: str) -> int:
    return sampling_tolerance_group(grid_display_prep_config(model, var))


_PHASE_G_SCOPE_BY_MODEL: dict[str, list[str]] = {
    **{model: _model_scope(model) for model in PHASE_G_MODELS},
    **{model: _canary_scope(model) for model in PHASE_G_ENSEMBLE_MODELS},
}


def _group_params(group: int) -> list[tuple[str, str]]:
    return [
        (model, var)
        for model, scope in _PHASE_G_SCOPE_BY_MODEL.items()
        for var in scope
        if _tolerance_group(model, var) == group
    ]


GROUP1_PARAMS = _group_params(1)
GROUP2_PARAMS = _group_params(2)
GROUP4_PARAMS = _group_params(4)


# Expected tolerance-group partition from the migration plan's "Phase G audit —
# HRRR and NBM static readiness" section. This pins the audit tables to the
# live config: if a variable is added to (or reclassified in) either model's
# packing or display-prep config without a re-audit, this test fails loudly
# instead of the new variable silently defaulting into a group.
EXPECTED_GROUP_PARTITION = {
    "hrrr": {
        **{
            var: 1
            for var in (
                "dp2m", "mlcape", "mucape", "precip_total", "pwat", "rh2m",
                "rh700", "sbcape", "snowfall_kuchera_total", "snowfall_total",
                "tmp2m", "tmp850", "tmp850_anom", "vort500", "wgst10m",
                "wspd10m", "wspd300", "wspd850",
            )
        },
        **{
            var: 2
            for var in (
                "radar_ptype_rain", "radar_ptype_snow", "radar_ptype_sleet",
                "radar_ptype_frzr",
            )
        },
        "radar_ptype": 4,
    },
    "nbm": {
        **{var: 1 for var in ("sbcape", "tmp2m", "wspd10m")},
        **{var: 2 for var in ("precip_total", "snowfall_total")},
    },
}


def test_phase_g_audit_tolerance_group_partition_matches_config() -> None:
    for model, expected in EXPECTED_GROUP_PARTITION.items():
        actual = {var: _tolerance_group(model, var) for var in _model_scope(model)}
        assert actual == expected, (
            f"{model}: tolerance-group partition diverged from the Phase G "
            f"audit — re-audit before extending the canary/tests.\n"
            f"actual={actual}\nexpected={expected}"
        )


# Expected tolerance-group partition and dead-alias sets from the migration
# plan's "Phase G audit — GEFS and EPS static readiness" section, keyed by the
# canary comparison scope (the published __mean artifacts). GEFS has exactly
# two Group 2 artifacts (upscale_factor=3 continuous); EPS has zero display-
# prep entries, so every EPS artifact is Group 1.
EXPECTED_ENSEMBLE_GROUP_PARTITION = {
    "gefs": {
        **{
            var: 1
            for var in (
                "hgt500_anom__mean", "precip_10d_anom__mean",
                "precip_16d_anom__mean", "precip_5d_anom__mean",
                "precip_7d_anom__mean", "pwat__mean", "rh2m__mean",
                "rh700__mean", "sbcape__mean", "tmp2m__mean",
                "tmp2m_anom__mean", "tmp850__mean", "tmp850_anom__mean",
                "wspd10m__mean", "wspd300__mean", "wspd850__mean",
            )
        },
        **{var: 2 for var in ("precip_total__mean", "snowfall_total__mean")},
    },
    "eps": {
        var: 1
        for var in (
            "hgt500_anom__mean", "precip_10d_anom__mean",
            "precip_15d_anom__mean", "precip_5d_anom__mean",
            "precip_7d_anom__mean", "precip_total__mean", "pwat__mean",
            "rh2m__mean", "rh700__mean", "tmp2m__mean", "tmp2m_anom__mean",
            "tmp850__mean", "tmp850_anom__mean", "wspd10m__mean",
        )
    },
}

# The audited write-path-dead bare aliases per ensemble model — packed and
# catalog-buildable, but never written under their own ids because runtime
# var-id resolution redirects every build to the __mean twin.
EXPECTED_ENSEMBLE_DEAD_ALIASES = {
    "gefs": {
        "hgt500_anom", "precip_10d_anom", "precip_16d_anom", "precip_5d_anom",
        "precip_7d_anom", "tmp2m_anom", "tmp850_anom",
    },
    "eps": {
        "hgt500_anom", "precip_10d_anom", "precip_15d_anom", "precip_5d_anom",
        "precip_7d_anom", "tmp2m_anom", "tmp850_anom",
    },
}


def test_phase_g_ensemble_partition_matches_audit_and_canary_scope() -> None:
    """Pin the ensemble parameterization to the audit: the canary's scope
    buckets must partition the packing table, the dead-alias bucket must equal
    the audited set, and the parity scope above must be exactly the canary
    scope with the audited tolerance groups — so an unaudited catalog change
    fails loudly here instead of silently narrowing coverage."""
    from backend.scripts.canary_binary_sampler import _scope_for_model

    for model, expected in EXPECTED_ENSEMBLE_GROUP_PARTITION.items():
        (
            in_scope,
            excluded_non_buildable,
            excluded_dead_alias,
            excluded_uncataloged,
        ) = _scope_for_model(model)
        packed = set(_model_scope(model))
        assert (
            set(in_scope)
            | set(excluded_non_buildable)
            | set(excluded_dead_alias)
            | set(excluded_uncataloged)
        ) == packed
        assert excluded_uncataloged == []
        assert set(excluded_dead_alias) == EXPECTED_ENSEMBLE_DEAD_ALIASES[model]
        assert set(in_scope).isdisjoint(excluded_dead_alias)

        assert set(_PHASE_G_SCOPE_BY_MODEL[model]) == set(in_scope)
        actual = {var: _tolerance_group(model, var) for var in in_scope}
        assert actual == expected, (
            f"{model}: tolerance-group partition diverged from the ensemble "
            f"Phase G audit — re-audit before extending the canary/tests.\n"
            f"actual={actual}\nexpected={expected}"
        )


def _model_grid_geometry(model: str) -> tuple[Affine, str]:
    """Fixture grid geometry taken from the model's real build config
    (Phase G checklist item 4): canonical build region, native grid
    resolution, and published CRS, resolved through the same helpers the
    production pipeline uses. The fixture covers a small window anchored at
    the domain's tap-aligned origin — a full-domain array would be needlessly
    large for a parity unit test — but the CRS, pixel size, and grid
    alignment are the model's real ones, not reused from another model's
    fixture.
    """
    plugin = MODEL_REGISTRY[model]
    region = str(plugin.capabilities.canonical_region)
    bbox_3857, grid_meters = get_grid_params(model, region)
    transform, _height, _width = compute_transform_and_shape(bbox_3857, grid_meters)
    return transform, "EPSG:3857"


def _lonlat_at(
    transform: Affine,
    projection: str,
    row_f: float,
    col_f: float,
) -> tuple[float, float]:
    """Lat/lon of a fractional (row, col) position on the fixture grid."""
    x, y = transform * (col_f, row_f)
    if projection.upper() == "EPSG:4326":
        return float(y), float(x)
    lon, lat = Transformer.from_crs(projection, "EPSG:4326", always_xy=True).transform(x, y)
    return float(lat), float(lon)


def _lattice_values(model: str, var: str, shape: tuple[int, int]) -> np.ndarray:
    """In-band fixture values on the packing lattice (offset + k*scale),
    stepped so every value has at most one decimal digit. Lattice values
    quantize losslessly, and one-decimal values round stably, so the COG path
    (raw float32) and the binary path (packed uint) agree exactly after the
    samplers' shared round-to-1-decimal — letting Group 1 assert strict
    equality instead of a tolerance. Negative-offset packings (e.g. vorticity
    and temperature anomalies) naturally produce negative values here.
    """
    packing = _PACKING_BY_MODEL_VAR[(model, var)]
    scale = float(packing["scale"])
    offset = float(packing["offset"])
    step_mult = max(1, int(round(0.1 / scale))) if scale < 0.1 else 1
    step = scale * step_mult
    count = shape[0] * shape[1]
    codes = 100.0 + np.arange(count, dtype=np.float64)
    return (offset + codes * step).reshape(shape).astype(np.float32)


@pytest.mark.parametrize(("model", "var"), GROUP1_PARAMS)
def test_group1_binary_matches_cog_on_model_native_grid(
    model: str, var: str, tmp_path: Path
) -> None:
    """Group 1 (no display prep): COG and binary describe the same pixel grid,
    so the rounded sampled values must be exactly equal at interior points,
    pixel-boundary points, nodata cells, and out-of-bbox points."""
    transform, projection = _model_grid_geometry(model)
    values = _lattice_values(model, var, (5, 5))
    values[1, 1] = np.nan  # nodata cell
    height, width = values.shape

    cog_path, frame_path, meta_path = _write_pair(
        tmp_path,
        model=model,
        var=var,
        values=values,
        transform=transform,
        projection=projection,
    )

    # Group 1 invariant: no resolution change between the artifacts.
    meta = json.loads(meta_path.read_text())
    assert (meta["height"], meta["width"]) == (height, width)

    interior_points = [(0.5, 0.5), (height - 0.5, width - 0.5), (2.5, 3.5)]
    boundary_points = [(2.0, 2.0), (0.5, 3.0), (height - 1.0, 2.5)]
    for row_f, col_f in interior_points + boundary_points:
        lat, lon = _lonlat_at(transform, projection, row_f, col_f)
        cog_value = sample_point_value(cog_path, lat=lat, lon=lon)
        binary_value = sample_binary_point_value(
            frame_path, meta_path, model=model, var=var, lat=lat, lon=lon
        )
        assert cog_value is not None, f"{model}/{var}: COG sample missing at ({row_f}, {col_f})"
        assert binary_value == cog_value, (
            f"{model}/{var}: Group 1 divergence at ({row_f}, {col_f}): "
            f"cog={cog_value} binary={binary_value}"
        )

    # Nodata cell center: both substrates agree it has no value.
    lat, lon = _lonlat_at(transform, projection, 1.5, 1.5)
    assert sample_point_value(cog_path, lat=lat, lon=lon) is None
    raw, no_data = read_binary_sample_value(
        frame_path, meta_path, model=model, var=var, lat=lat, lon=lon
    )
    assert raw is None
    assert no_data is True

    # Point outside the model's bbox (well northwest of the domain origin).
    lat, lon = _lonlat_at(transform, projection, -50.0, -50.0)
    assert sample_point_value(cog_path, lat=lat, lon=lon) is None
    raw, no_data = read_binary_sample_value(
        frame_path, meta_path, model=model, var=var, lat=lat, lon=lon
    )
    assert raw is None
    assert no_data is True


@pytest.mark.parametrize(("model", "var"), GROUP2_PARAMS)
def test_group2_continuous_upscale_parity_on_model_native_grid(
    model: str, var: str, tmp_path: Path
) -> None:
    """Group 2 (continuous upscale): the binary stores a finer, display-prepped
    grid. The authoritative assertion is against the real display-prep output
    at the sampled fine pixel (within packing quantization, scale/2); the
    COG comparison is a bounded sanity check, not equality — see the tolerance
    comment inline."""
    transform, projection = _model_grid_geometry(model)
    packing = _PACKING_BY_MODEL_VAR[(model, var)]
    scale = float(packing["scale"])
    config = grid_display_prep_config(model, var)
    assert config is not None

    # Smooth gradient comfortably above the display-prep support threshold so
    # zero-support masking never engages; adjacent-cell delta is `step`
    # horizontally and `width * step` vertically.
    base = float(config.support_min_value or 0.0) + 10.0 * scale
    step = 25.0 * scale
    height = width = 5
    values = (
        base + np.arange(height * width, dtype=np.float64) * step
    ).reshape(height, width).astype(np.float32)

    cog_path, frame_path, meta_path = _write_pair(
        tmp_path,
        model=model,
        var=var,
        values=values,
        transform=transform,
        projection=projection,
    )

    display_values, prep_meta = prepare_grid_display_values(model=model, var=var, values=values)
    assert prep_meta is not None
    upscale_factor = int(prep_meta["upscale_factor"])
    assert upscale_factor > 1
    meta = json.loads(meta_path.read_text())
    assert (meta["height"], meta["width"]) == (height * upscale_factor, width * upscale_factor)

    for row_f, col_f in [(1.25, 1.75), (2.5, 3.5), (3.9, 0.6)]:
        lat, lon = _lonlat_at(transform, projection, row_f, col_f)
        raw, no_data = read_binary_sample_value(
            frame_path, meta_path, model=model, var=var, lat=lat, lon=lon
        )
        assert no_data is False
        assert raw is not None

        # Authoritative: the binary must reproduce the display-prepped field at
        # the fine pixel the meta transform maps this point to, within packing
        # quantization only.
        fine_row, fine_col = _meta_index(meta_path, lon=lon, lat=lat)
        expected = float(display_values[fine_row, fine_col])
        assert raw == pytest.approx(expected, abs=scale / 2 + 1e-4), (
            f"{model}/{var}: binary sample diverged from display-prepped field "
            f"at ({row_f}, {col_f}): raw={raw} expected={expected}"
        )

        # Bounded COG sanity check. The COG samples the original coarse grid;
        # the binary samples a bilinear-upscaled fine grid, so exact equality
        # is not expected (this is what makes Group 2 a distinct tolerance
        # group). A fine-grid bilinear value stays within the local coarse
        # neighborhood, so the divergence is bounded by the fixture's largest
        # adjacent-cell delta (width * step vertically, plus one horizontal
        # step), plus packing quantization and the samplers' 1-decimal
        # rounding. Anything beyond that bound means the sampler read a pixel
        # from the wrong part of the grid, not an upscale artifact.
        cog_value = sample_point_value(cog_path, lat=lat, lon=lon)
        assert cog_value is not None
        neighborhood_bound = (width + 1) * step + scale + 0.05
        assert abs(raw - cog_value) <= neighborhood_bound, (
            f"{model}/{var}: Group 2 divergence exceeds the coarse-neighborhood "
            f"bound at ({row_f}, {col_f}): raw={raw} cog={cog_value} "
            f"bound={neighborhood_bound}"
        )

    # Point outside the model's bbox.
    lat, lon = _lonlat_at(transform, projection, -50.0, -50.0)
    raw, no_data = read_binary_sample_value(
        frame_path, meta_path, model=model, var=var, lat=lat, lon=lon
    )
    assert raw is None
    assert no_data is True


@pytest.mark.parametrize(("model", "var"), GROUP4_PARAMS)
def test_group4_categorical_no_upscale_requires_exact_integer_equality(
    model: str, var: str, tmp_path: Path
) -> None:
    """Group 4 (categorical_nearest with upscale_factor=1): there is NO
    resolution difference between the value COG and the grid binary, so the
    two samplers must return exactly equal integer categories at every test
    point — including points on and near category boundaries. No tolerance,
    no boundary exception: any divergence is a test failure (and blocking in
    the canary), unlike Group 3 where boundary divergence is expected from
    the resolution mismatch."""
    transform, projection = _model_grid_geometry(model)
    height = width = 6

    # Explicit multi-category quadrants (categories 1, 2, 3, 5) with internal
    # class boundaries between rows/cols 2 and 3.
    values = np.zeros((height, width), dtype=np.float32)
    values[:3, :3] = 1.0
    values[:3, 3:] = 2.0
    values[3:, :3] = 3.0
    values[3:, 3:] = 5.0

    cog_path, frame_path, meta_path = _write_pair(
        tmp_path,
        model=model,
        var=var,
        values=values,
        transform=transform,
        projection=projection,
    )

    # Group 4 invariant: same resolution on both sides.
    meta = json.loads(meta_path.read_text())
    assert (meta["height"], meta["width"]) == (height, width)

    quadrant_centers = [(1.5, 1.5), (1.5, 4.5), (4.5, 1.5), (4.5, 4.5)]
    near_boundary = [
        (2.9, 2.9), (3.1, 3.1), (2.9, 3.1), (3.1, 2.9),
        (1.5, 2.95), (1.5, 3.05), (2.95, 4.5), (3.05, 4.5),
    ]
    on_boundary = [(3.0, 3.0), (3.0, 1.5), (1.5, 3.0)]
    for row_f, col_f in quadrant_centers + near_boundary + on_boundary:
        lat, lon = _lonlat_at(transform, projection, row_f, col_f)
        cog_value = sample_point_value(cog_path, lat=lat, lon=lon)
        binary_value = sample_binary_point_value(
            frame_path, meta_path, model=model, var=var, lat=lat, lon=lon
        )
        assert cog_value is not None
        assert binary_value is not None
        assert float(cog_value).is_integer()
        assert float(binary_value).is_integer()
        assert int(binary_value) == int(cog_value), (
            f"{model}/{var}: Group 4 categorical divergence at ({row_f}, {col_f}): "
            f"cog={cog_value} binary={binary_value} — same-resolution categorical "
            f"artifacts must never disagree, even at class boundaries"
        )

    # Point outside the model's bbox.
    lat, lon = _lonlat_at(transform, projection, -50.0, -50.0)
    assert sample_point_value(cog_path, lat=lat, lon=lon) is None
    raw, no_data = read_binary_sample_value(
        frame_path, meta_path, model=model, var=var, lat=lat, lon=lon
    )
    assert raw is None
    assert no_data is True
