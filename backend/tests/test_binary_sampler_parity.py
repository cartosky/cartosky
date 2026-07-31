"""Grid-binary sampling, packing, and display-preparation regression tests."""

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
from app.services.builder.raster_grid import compute_transform_and_shape, get_grid_params
from app.services.grid import (
    GRID_DTYPE,
    _PACKING_BY_MODEL_VAR,
    grid_dtype,
    grid_frame_filename,
    write_grid_frame_for_run_root,
)
from app.services.grid_display_prep import (
    grid_display_prep_config,
    prepare_grid_display_values,
    display_prep_shape_group,
)
from app.services.sampling import (
    read_binary_sample_value,
    sample_binary_value,
)

# Default fixture geometry for the original GFS-era tests below.
_DEFAULT_TRANSFORM = from_origin(-101.0, 46.0, 1.0, 1.0)
_DEFAULT_PROJECTION = "EPSG:4326"


def _write_pair(
    tmp_path: Path,
    *,
    model: str,
    var: str,
    values: np.ndarray,
    transform: Affine = _DEFAULT_TRANSFORM,
    projection: str = _DEFAULT_PROJECTION,
) -> tuple[Path, Path]:
    run_root = tmp_path / "published" / model / "20260630_00z"
    var_dir = run_root / var
    write_grid_frame_for_run_root(
        run_root=run_root,
        model=model,
        var=var,
        fh=0,
        values=values,
        transform=transform,
        projection=projection,
    )
    # The binary filename encodes the packing dtype (u16 for everything except
    # MRMS's two uint8 variables).
    packing = _PACKING_BY_MODEL_VAR.get((model, var), {})
    bin_name = grid_frame_filename(0, dtype=grid_dtype(str(packing.get("dtype") or GRID_DTYPE)))
    return (
        var_dir / "grid" / bin_name,
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


def test_binary_sampler_reads_display_prepped_categorical_upscale(tmp_path: Path) -> None:
    values = np.array(
        [
            [10.0, 20.0],
            [30.0, 40.0],
        ],
        dtype=np.float32,
    )
    frame_path, meta_path = _write_pair(
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
    frame_path, meta_path = _write_pair(
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

# Poll-driven standalone publishers (NDFD 2.5km CONUS, WPC 5km CONUS). They do
# NOT follow the scheduler's model-run conventions — minute-stamped run ids,
# dedicated publish modules, NDFD's fh is a per-variable valid-time sequence
# index and WPC's is a real forecast hour over a cumulative transform — but
# none of that reaches the sampler: parity is a per-frame property. Scope is
# derived through the canary intersection like the ensembles (the audit found
# every exclusion bucket empty for both; pinned below), and both models are
# 100% Group 1 (zero display-prep entries).
PHASE_G_PUBLISHER_MODELS = ("ndfd", "wpc")

# Observed-product standalone publishers (current_analysis 2.5km, MRMS 1km,
# GOES-East 4km — all CONUS). Scope via the canary intersection; for
# current_analysis the buckets are genuinely non-empty (pinned below).
PHASE_G_OBSERVED_MODELS = ("current_analysis", "mrms", "goes-east")

# Observed variables whose grid binary stores a TRANSFORMED field, so the
# generic Group 1 source-to-binary assertion does not apply even though
# display_prep_shape_group classifies them Group 1:
# - goes-east ir13/wv9/wv8: a hardcoded K->C special case at the top of
#   prepare_grid_display_values (values - 273.15, not a display-prep config
#   entry) means the binary is Celsius while the source field is Kelvin — a clean
#   constant delta at every pixel, invisible to the classifier; the canary
#   WILL flag it (~273.15 divergence) and that is handled at canary time.
# - mrms reflectivity: display-prep smooth_sigma=0.45 at upscale 1 means the
#   binary is a masked-gaussian-smoothed field vs the raw source field — a known
#   Group-1-classifier blind spot (same category as ECMWF's floor; operator
#   decision not to patch the classifier).
# Each gets a dedicated parity test below instead of the generic one.
OBSERVED_TRANSFORMED_BINARY_VARS = {
    ("goes-east", "ir13"),
    ("goes-east", "wv9"),
    ("goes-east", "wv8"),
    ("mrms", "reflectivity"),
}


def _model_scope(model: str) -> list[str]:
    return sorted(var for (mdl, var) in _PACKING_BY_MODEL_VAR if mdl == model)


def _canary_scope(model: str) -> list[str]:
    """Packed variables intersected with the canary's own scope logic, so
    Layer 2 coverage cannot silently drift from what Layer 3 exercises."""
    from backend.tests.helpers_variable_scope import _scope_for_model

    return list(_scope_for_model(model)[0])


def _tolerance_group(model: str, var: str) -> int:
    return display_prep_shape_group(grid_display_prep_config(model, var))


_PHASE_G_SCOPE_BY_MODEL: dict[str, list[str]] = {
    **{model: _model_scope(model) for model in PHASE_G_MODELS},
    **{model: _canary_scope(model) for model in PHASE_G_ENSEMBLE_MODELS},
    **{model: _canary_scope(model) for model in PHASE_G_PUBLISHER_MODELS},
    **{model: _canary_scope(model) for model in PHASE_G_OBSERVED_MODELS},
}


def _group_params(group: int) -> list[tuple[str, str]]:
    return [
        (model, var)
        for model, scope in _PHASE_G_SCOPE_BY_MODEL.items()
        for var in scope
        if _tolerance_group(model, var) == group
    ]


# Transformed-binary observed variables are excluded from the generic Group 1
# parameterization (see OBSERVED_TRANSFORMED_BINARY_VARS) — dedicated tests
# below cover them; the partition pin asserts nothing else was dropped.
GROUP1_PARAMS = [p for p in _group_params(1) if p not in OBSERVED_TRANSFORMED_BINARY_VARS]
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
    from backend.tests.helpers_variable_scope import _scope_for_model

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


# Expected tolerance-group partition for the standalone publishers, matching
# the NDFD/WPC audit's tolerance-group table: zero display-prep entries for
# either model, so every packed variable is Group 1 (exact within scale/2).
EXPECTED_PUBLISHER_GROUP_PARTITION = {
    "ndfd": {
        var: 1
        for var in (
            "ice_24h", "ice_6h", "maxt", "mint", "qpf_24h", "qpf_48h",
            "qpf_6h", "snow_24h", "snow_48h", "snow_6h", "wgust_24h_max",
            "wgust_6h_max",
        )
    },
    "wpc": {"precip_total": 1},
}


def test_publisher_partition_matches_audit_and_canary_scope() -> None:
    """Pin the publisher parameterization to the audit: every canary exclusion
    bucket is empty (scope IS the full packed list), every variable is
    Group 1, and the parity scope above is exactly the canary scope — so an
    unaudited catalog or display-prep change fails loudly here instead of
    silently reclassifying (or dropping) a variable."""
    from backend.tests.helpers_variable_scope import _scope_for_model

    for model, expected in EXPECTED_PUBLISHER_GROUP_PARTITION.items():
        (
            in_scope,
            excluded_non_buildable,
            excluded_dead_alias,
            excluded_uncataloged,
        ) = _scope_for_model(model)
        assert excluded_non_buildable == []
        assert excluded_dead_alias == []
        assert excluded_uncataloged == []
        assert sorted(in_scope) == _model_scope(model)

        assert set(_PHASE_G_SCOPE_BY_MODEL[model]) == set(in_scope)
        actual = {var: _tolerance_group(model, var) for var in in_scope}
        assert actual == expected, (
            f"{model}: tolerance-group partition diverged from the NDFD/WPC "
            f"audit — re-audit before extending the canary/tests.\n"
            f"actual={actual}\nexpected={expected}"
        )
        # All-Group-1 means these models appear ONLY in the Group 1
        # parameterization below — none in Group 2 or Group 4.
        assert not [var for (mdl, var) in GROUP2_PARAMS if mdl == model]
        assert not [var for (mdl, var) in GROUP4_PARAMS if mdl == model]
        covered = {var for (mdl, var) in GROUP1_PARAMS if mdl == model}
        assert covered == set(in_scope)


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
    quantize losslessly, and one-decimal values round stably, so the source
    float32 and packed binary agree exactly after the
    samplers' shared round-to-1-decimal — letting Group 1 assert strict
    equality instead of a tolerance. Negative-offset packings (e.g. vorticity
    and temperature anomalies) naturally produce negative values here.
    """
    packing = _PACKING_BY_MODEL_VAR[(model, var)]
    scale = float(packing["scale"])
    offset = float(packing["offset"])
    nodata = int(packing["nodata"])
    step_mult = max(1, int(round(0.1 / scale))) if scale < 0.1 else 1
    # Clamp the step so every fixture value stays inside the representable
    # band — vis2's 1/65534 scale (band 0..1) would otherwise overflow it.
    count = shape[0] * shape[1]
    step_mult = min(step_mult, max(1, (nodata - 1) // (100 + count)))
    step = scale * step_mult
    codes = 100.0 + np.arange(count, dtype=np.float64)
    return (offset + codes * step).reshape(shape).astype(np.float32)


# Expected classifier groups per the live display_prep_shape_group() config:
# ALL Group 1 across all three models — including mrms_radar_ptype (it has NO
# categorical display-prep entry, unlike hrrr's radar_ptype which an entry
# makes Group 4; its scale=1.0 lossless packing makes strict integer equality
# hold anyway, asserted in its dedicated test) and the transformed-binary
# variables (K->C constant offset / smoothing are invisible to the
# classifier — the recorded Group-1 blind spot).
EXPECTED_OBSERVED_GROUP_PARTITION = {
    "current_analysis": {var: 1 for var in ("dp2m", "tmp2m", "wgst10m", "wspd10m")},
    "mrms": {
        var: 1
        for var in (
            "mrms_radar_ptype", "mrms_recent_precip_24h", "mrms_recent_precip_6h",
            "mrms_recent_precip_72h", "reflectivity",
        )
    },
    "goes-east": {var: 1 for var in ("ir13", "vis2", "wv8", "wv9")},
}

# (non_buildable, dead_alias, uncataloged) — current_analysis is the first
# product in the migration with non-empty buckets: spres is packed AND
# published (the publisher ignores buildable=False) but canary-excluded;
# mslp is a stray packing key under a normalize-alias with no catalog entry.
EXPECTED_OBSERVED_EXCLUSIONS = {
    "current_analysis": (["spres"], [], ["mslp"]),
    "mrms": ([], [], []),
    "goes-east": ([], [], []),
}


def test_observed_partition_matches_classifier_and_canary_scope() -> None:
    """Pin the observed-model parameterization: canary buckets, classifier
    groups (all Group 1), and that the generic Group 1 coverage is exactly
    the canary scope minus the transformed-binary exclusions — so neither an
    unaudited catalog change nor a silent exclusion drop can slip through."""
    from backend.tests.helpers_variable_scope import _scope_for_model

    for model, expected_groups in EXPECTED_OBSERVED_GROUP_PARTITION.items():
        (
            in_scope,
            excluded_non_buildable,
            excluded_dead_alias,
            excluded_uncataloged,
        ) = _scope_for_model(model)
        exp_nb, exp_da, exp_unc = EXPECTED_OBSERVED_EXCLUSIONS[model]
        assert excluded_non_buildable == exp_nb
        assert excluded_dead_alias == exp_da
        assert excluded_uncataloged == exp_unc
        packed = set(_model_scope(model))
        assert (
            set(in_scope)
            | set(excluded_non_buildable)
            | set(excluded_dead_alias)
            | set(excluded_uncataloged)
        ) == packed

        assert set(_PHASE_G_SCOPE_BY_MODEL[model]) == set(in_scope)
        actual = {var: _tolerance_group(model, var) for var in in_scope}
        assert actual == expected_groups, (
            f"{model}: tolerance-group partition diverged from the observed "
            f"audit — re-audit before extending the canary/tests.\n"
            f"actual={actual}\nexpected={expected_groups}"
        )

        transformed = {v for (mdl, v) in OBSERVED_TRANSFORMED_BINARY_VARS if mdl == model}
        assert transformed <= set(in_scope)
        covered = {var for (mdl, var) in GROUP1_PARAMS if mdl == model}
        assert covered == set(in_scope) - transformed
        assert not [var for (mdl, var) in GROUP2_PARAMS if mdl == model]
        assert not [var for (mdl, var) in GROUP4_PARAMS if mdl == model]


def test_seek_sampler_matches_full_read_on_mrms_uint8_and_uint16(tmp_path: Path) -> None:
    """Pin read_binary_sample_value_seek == read_binary_sample_value on the
    two MRMS substrates the production routes now read through the seek
    primitive: the uint8 1km reflectivity frames (whose full-frame decode was
    ~70ms/sample — the reason for the call-site swap) and the uint16
    recent-precip frames. Bit-for-bit tuple equality across in-bounds points
    (including negative dBZ), a nodata pixel, and out-of-bounds points; a
    truncated frame must raise identically on both paths."""
    from app.services.sampling import read_binary_sample_value_seek

    transform, projection = _model_grid_geometry("mrms")

    cases = {
        # Negative weak echo included per the sentinel/negative-clamp fix.
        "reflectivity": np.array(
            [
                [-9.5, -4.5, 0.0, 12.5],
                [20.0, np.nan, 47.5, 60.0],
                [5.5, 33.0, -0.5, 41.5],
            ],
            dtype=np.float32,
        ),
        "mrms_recent_precip_24h": np.array(
            [
                [0.0, 0.25, 1.5, 3.75],
                [7.5, np.nan, 12.25, 20.0],
                [0.05, 2.4, 9.99, 15.0],
            ],
            dtype=np.float32,
        ),
    }
    expected_bin_names = {
        "reflectivity": "fh000.l0.u8.bin",
        "mrms_recent_precip_24h": "fh000.l0.u16.bin",
    }

    for var, values in cases.items():
        frame_path, meta_path = _write_pair(
            tmp_path,
            model="mrms",
            var=var,
            values=values,
            transform=transform,
            projection=projection,
        )
        assert frame_path.name == expected_bin_names[var]

        height, width = values.shape
        # Every cell center (covers negatives and the nodata pixel), plus
        # fractional interior positions and out-of-bounds points.
        positions = [
            (r + 0.5, c + 0.5) for r in range(height) for c in range(width)
        ] + [(0.25, 3.75), (2.9, 0.1), (-5.0, -5.0), (height + 3.0, width + 3.0)]
        for row_f, col_f in positions:
            lat, lon = _lonlat_at(transform, projection, row_f, col_f)
            full = read_binary_sample_value(
                frame_path, meta_path, model="mrms", var=var, lat=lat, lon=lon
            )
            seek = read_binary_sample_value_seek(
                frame_path, meta_path, model="mrms", var=var, lat=lat, lon=lon
            )
            assert seek == full, (var, row_f, col_f, full, seek)

        # Truncated frame: both paths refuse to mis-address, same error class.
        frame_path.write_bytes(frame_path.read_bytes()[:-3])
        lat, lon = _lonlat_at(transform, projection, 0.5, 0.5)
        with pytest.raises(ValueError, match="size mismatch"):
            read_binary_sample_value(
                frame_path, meta_path, model="mrms", var=var, lat=lat, lon=lon
            )
        with pytest.raises(ValueError, match="size mismatch"):
            read_binary_sample_value_seek(
                frame_path, meta_path, model="mrms", var=var, lat=lat, lon=lon
            )
