"""Phase 3A Waves 1 + 2 — global EPSG:4326 ERA5 baselines for anomaly variables.

Four properties, all pinned against the real catalog and the real registry:

* **Registry.** ``era5``/``global`` resolves the global domain contract grid
  (``docs/GLOBAL_DOMAIN_4326_CONTRACT.md`` §1) and the loader validates it
  exactly — CRS, shape and transform — while metre regions keep their
  byte-identical EPSG:3857 validation.
* **Resolution.** Every anomaly variable departs from the NA baseline on
  canonical builds and from the global baseline on global builds — the three
  Wave-1 instantaneous fields since 2026-07-30, the four precip-window
  accumulation fields since the Wave 2 flip (2026-08-03).
* **Graceful skip.** A global anomaly build whose baseline assets are not
  installed yet skips the frame with one log line; canonical builds are
  untouched by that path. Both the instantaneous (hourly ``doy_NNN_hHH.tif``)
  and the accumulation (daily ``doy_NNN.tif``) pre-checks are covered.
  Deploy-ordering insurance: code and assets may land in either order.
* **Generation script.** ``--region global`` computes the contract transform
  and takes the identity fast path when the staged raster is already on it.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.gfs import GFS_MODEL  # noqa: E402
from app.services import climatology  # noqa: E402
from app.services.builder import pipeline as pipeline_module  # noqa: E402
from app.services.builder.raster_grid import (  # noqa: E402
    REGION_BBOX_3857,
    compute_transform_and_shape,
)
from app.services.builder.derive import (  # noqa: E402
    derive_variable as REAL_DERIVE_VARIABLE,
)
from scripts import build_climatology_baseline_assets as build_script  # noqa: E402

MODEL = "gfs"
CANONICAL = "na"
GLOBAL = "global"
SOURCE = "era5"

#: The contract grid, written as literals so a drift has to be deliberate.
CONTRACT_TRANSFORM = Affine(0.25, 0.0, -180.125, 0.0, -0.25, 90.125)
CONTRACT_HEIGHT = 721
CONTRACT_WIDTH = 1440

WAVE1_VARS = ("tmp2m_anom", "tmp850_anom", "hgt500_anom")
WAVE1_BASELINE_FIELD = {
    "tmp2m_anom": "tmp2m",
    "tmp850_anom": "tmp850",
    "hgt500_anom": "hgt500",
}
PRECIP_ANOM_VARS = (
    "precip_5d_anom",
    "precip_7d_anom",
    "precip_10d_anom",
    "precip_16d_anom",
)
#: GFS precip anomaly windows: 5/7/10/16 days (15 d is the AIFS/ECMWF window).
#: The value is both the accumulation window and the earliest buildable fh, so
#: at exactly this fh the window opens at the run itself.
PRECIP_ANOM_TARGET_FH = {
    "precip_5d_anom": 120,
    "precip_7d_anom": 168,
    "precip_10d_anom": 240,
    "precip_16d_anom": 384,
}
PRECIP_ANOM_BASELINE_FIELD = {
    "precip_5d_anom": "precip_5d",
    "precip_7d_anom": "precip_7d",
    "precip_10d_anom": "precip_10d",
    "precip_16d_anom": "precip_16d",
}


def _hints(var_key: str) -> dict[str, str]:
    spec = GFS_MODEL.get_var(var_key)
    return dict(getattr(getattr(spec, "selectors", None), "hints", {}) or {})


def _write_raster(
    path: Path,
    values: np.ndarray,
    *,
    crs: str,
    transform,
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
        crs=crs,
        transform=transform,
        nodata=float("nan"),
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def _global_values() -> np.ndarray:
    rows = np.arange(CONTRACT_HEIGHT, dtype=np.float32)[:, None]
    cols = np.arange(CONTRACT_WIDTH, dtype=np.float32)[None, :]
    return (rows * 0.5 + cols * 0.25).astype(np.float32)


# ── 1. baseline registry ───────────────────────────────────────────────────


def test_global_baseline_grid_is_the_contract_grid() -> None:
    grid = climatology.get_baseline_grid(baseline_source=SOURCE, region=GLOBAL)
    assert grid.crs == "EPSG:4326"
    assert grid.is_native_geographic
    assert grid.resolution == 0.25
    assert (grid.height, grid.width) == (CONTRACT_HEIGHT, CONTRACT_WIDTH)
    assert grid.transform == CONTRACT_TRANSFORM
    assert grid.bbox == (-180.125, -90.125, 179.875, 90.125)
    assert grid.grid_id == "climatology:era5:global:0.25deg"


def test_metre_baseline_grid_is_unchanged() -> None:
    grid = climatology.get_baseline_grid(baseline_source=SOURCE, region=CANONICAL)
    transform, height, width = compute_transform_and_shape(
        REGION_BBOX_3857[CANONICAL], 25_000.0
    )
    assert grid.crs == "EPSG:3857"
    assert not grid.is_native_geographic
    assert (grid.height, grid.width) == (height, width)
    assert grid.transform == transform
    assert grid.grid_id == "climatology:era5:na:25000.0m"

    # The metre helper is untouched and still refuses the native-geographic
    # region — the two registries are disjoint by construction.
    assert climatology.get_baseline_grid_params(
        baseline_source=SOURCE, region=CANONICAL
    ) == (REGION_BBOX_3857[CANONICAL], 25_000.0)
    with pytest.raises(KeyError):
        climatology.get_baseline_grid_params(baseline_source=SOURCE, region=GLOBAL)
    assert climatology.get_baseline_grid_degrees(
        baseline_source=SOURCE, region=CANONICAL
    ) is None
    assert climatology.get_baseline_grid_degrees(
        baseline_source=SOURCE, region=GLOBAL
    ) == 0.25


def _install_global_baseline(
    tmp_path: Path,
    values: np.ndarray,
    *,
    crs: str = "EPSG:4326",
    transform=CONTRACT_TRANSFORM,
    valid_time: datetime,
    field: str = "hgt500",
) -> Path:
    path = climatology.climatology_baseline_path(
        data_root=tmp_path,
        version="v1",
        baseline_source=SOURCE,
        field=field,
        region=GLOBAL,
        reference_period="1991-2020",
        valid_time=valid_time,
    )
    _write_raster(path, values, crs=crs, transform=transform)
    return path


def test_global_baseline_asset_loads_and_validates(tmp_path: Path) -> None:
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    values = _global_values()
    _install_global_baseline(tmp_path, values, valid_time=valid_time)

    loaded, crs, transform, meta = climatology.load_climatology_baseline(
        version="v1",
        baseline_source=SOURCE,
        field="hgt500",
        valid_time=valid_time,
        region=GLOBAL,
        reference_period="1991-2020",
    )
    assert loaded.shape == (CONTRACT_HEIGHT, CONTRACT_WIDTH)
    assert np.allclose(loaded, values)
    assert crs.to_epsg() == 4326
    assert transform == CONTRACT_TRANSFORM
    assert meta["baseline_region"] == GLOBAL


def test_global_baseline_asset_with_wrong_crs_is_rejected(tmp_path: Path) -> None:
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    _install_global_baseline(
        tmp_path,
        _global_values(),
        crs="EPSG:3857",
        valid_time=valid_time,
    )
    with pytest.raises(ValueError, match="must use EPSG:4326"):
        climatology.load_climatology_baseline(
            version="v1",
            baseline_source=SOURCE,
            field="hgt500",
            valid_time=valid_time,
            region=GLOBAL,
            reference_period="1991-2020",
        )


def test_global_baseline_asset_with_wrong_shape_is_rejected(tmp_path: Path) -> None:
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    _install_global_baseline(
        tmp_path,
        np.zeros((180, 360), dtype=np.float32),
        transform=from_origin(-180.5, 90.5, 1.0, 1.0),
        valid_time=valid_time,
    )
    with pytest.raises(ValueError, match="grid shape mismatch"):
        climatology.load_climatology_baseline(
            version="v1",
            baseline_source=SOURCE,
            field="hgt500",
            valid_time=valid_time,
            region=GLOBAL,
            reference_period="1991-2020",
        )


def test_global_baseline_asset_with_wrong_transform_is_rejected(tmp_path: Path) -> None:
    climatology.configure_data_root(tmp_path)
    valid_time = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    # Right CRS, right shape, cell-centre registered instead of cell-edge —
    # the half-cell offset the contract exists to prevent.
    _install_global_baseline(
        tmp_path,
        _global_values(),
        transform=Affine(0.25, 0.0, -180.0, 0.0, -0.25, 90.0),
        valid_time=valid_time,
    )
    with pytest.raises(ValueError, match="transform mismatch"):
        climatology.load_climatology_baseline(
            version="v1",
            baseline_source=SOURCE,
            field="hgt500",
            valid_time=valid_time,
            region=GLOBAL,
            reference_period="1991-2020",
        )


# ── 2. baseline_region resolution ──────────────────────────────────────────


@pytest.mark.parametrize("var_key", WAVE1_VARS)
def test_wave1_anomaly_resolves_na_on_canonical_and_global_on_global(
    var_key: str,
) -> None:
    hints = _hints(var_key)
    assert hints["baseline_region"] == CANONICAL
    assert (
        climatology.resolve_baseline_region(
            model=MODEL, build_region=CANONICAL, hints=hints
        )
        == CANONICAL
    )
    assert (
        climatology.resolve_baseline_region(
            model=MODEL, build_region=GLOBAL, hints=hints
        )
        == GLOBAL
    )


@pytest.mark.parametrize("var_key", PRECIP_ANOM_VARS)
def test_precip_anomaly_resolves_na_on_canonical_and_global_on_global(
    var_key: str,
) -> None:
    """Wave 2 flip: the precip-window anomalies now carry the override too.

    Before the flip these resolved ``None`` on global (no declaration ⇒ a
    native-geographic build resolves nothing ⇒ skip). This is that assertion
    inverted; the canonical direction is unchanged.
    """
    hints = _hints(var_key)
    assert hints["baseline_region"] == CANONICAL
    assert hints[climatology.BASELINE_REGION_BY_BUILD_REGION_HINT] == f"{GLOBAL}={GLOBAL}"
    assert hints["baseline_field"] == PRECIP_ANOM_BASELINE_FIELD[var_key]
    assert (
        climatology.resolve_baseline_region(
            model=MODEL, build_region=CANONICAL, hints=hints
        )
        == CANONICAL
    )
    assert (
        climatology.resolve_baseline_region(
            model=MODEL, build_region=GLOBAL, hints=hints
        )
        == GLOBAL
    )


def test_override_mapping_a_global_build_to_a_metre_baseline_raises() -> None:
    """Confidently-wrong-values guard: a 4326 domain must never subtract a
    3857 baseline. Nothing downstream would catch it — the baseline loads
    fine, it is just the wrong grid."""
    hints = {
        "baseline_region": CANONICAL,
        climatology.BASELINE_REGION_BY_BUILD_REGION_HINT: f"{GLOBAL}={CANONICAL}",
    }
    with pytest.raises(ValueError, match="same grid family"):
        climatology.resolve_baseline_region(
            model=MODEL, build_region=GLOBAL, hints=hints
        )


def test_override_mapping_a_metre_build_to_a_global_baseline_raises() -> None:
    """…and the reverse direction is just as wrong."""
    hints = {
        "baseline_region": CANONICAL,
        climatology.BASELINE_REGION_BY_BUILD_REGION_HINT: f"{CANONICAL}={GLOBAL}",
    }
    with pytest.raises(ValueError, match="same grid family"):
        climatology.resolve_baseline_region(
            model=MODEL, build_region=CANONICAL, hints=hints
        )


def test_same_family_overrides_are_accepted() -> None:
    """The guard rejects grid-family crossings, not overrides as such."""
    assert (
        climatology.resolve_baseline_region(
            model=MODEL,
            build_region=CANONICAL,
            hints={
                "baseline_region": CANONICAL,
                climatology.BASELINE_REGION_BY_BUILD_REGION_HINT: f"{CANONICAL}=conus",
            },
        )
        == "conus"
    )


def test_variable_without_a_baseline_hint_resolves_nothing() -> None:
    assert (
        climatology.resolve_baseline_region(
            model=MODEL, build_region=CANONICAL, hints={}
        )
        is None
    )


# ── 3. derive target grid ──────────────────────────────────────────────────


@pytest.mark.parametrize("var_key", WAVE1_VARS)
def test_derive_target_grid_canonical_is_the_na_baseline(var_key: str) -> None:
    grid, matches = pipeline_module._resolve_derive_target_grid(
        model=MODEL,
        region=CANONICAL,
        hints=_hints(var_key),
        derive_component_warp_cache=True,
    )
    assert grid == {"region": CANONICAL, "id": "climatology:era5:na:25000.0m"}
    assert matches is True


@pytest.mark.parametrize("var_key", WAVE1_VARS)
def test_derive_target_grid_global_is_the_global_baseline(var_key: str) -> None:
    grid, matches = pipeline_module._resolve_derive_target_grid(
        model=MODEL,
        region=GLOBAL,
        hints=_hints(var_key),
        derive_component_warp_cache=True,
    )
    assert grid == {"region": GLOBAL, "id": "climatology:era5:global:0.25deg"}
    # The global baseline grid IS the global domain grid, so component warps
    # are reusable as the frame output (contract §1/§5).
    assert matches is True


@pytest.mark.parametrize("var_key", PRECIP_ANOM_VARS)
def test_derive_target_grid_canonical_is_the_na_baseline_for_precip(
    var_key: str,
) -> None:
    """Canonical precip anomalies are byte-identical across the Wave 2 flip."""
    assert pipeline_module._resolve_derive_target_grid(
        model=MODEL,
        region=CANONICAL,
        hints=_hints(var_key),
        derive_component_warp_cache=True,
    ) == ({"region": CANONICAL, "id": "climatology:era5:na:25000.0m"}, True)


@pytest.mark.parametrize("var_key", PRECIP_ANOM_VARS)
def test_derive_target_grid_global_is_the_global_baseline_for_precip(
    var_key: str,
) -> None:
    """Wave 2 flip: was ``(None, False)`` (degrade) before the override hint."""
    grid, matches = pipeline_module._resolve_derive_target_grid(
        model=MODEL,
        region=GLOBAL,
        hints=_hints(var_key),
        derive_component_warp_cache=True,
    )
    assert grid == {"region": GLOBAL, "id": "climatology:era5:global:0.25deg"}
    assert matches is True


def test_non_baseline_derived_var_keeps_its_own_grid() -> None:
    """Variables with no baseline hint are untouched by the new branch."""
    hints = _hints("wspd850")
    assert "baseline_region" not in hints
    grid, matches = pipeline_module._resolve_derive_target_grid(
        model=MODEL,
        region=CANONICAL,
        hints=hints,
        derive_component_warp_cache=True,
    )
    assert grid == {"region": CANONICAL, "id": "climatology:era5:na:25000.0m"}
    assert matches is True
    # …and native-geographic output still degrades (no baseline metre grid).
    assert pipeline_module._resolve_derive_target_grid(
        model=MODEL,
        region=GLOBAL,
        hints=hints,
        derive_component_warp_cache=True,
    ) == (None, False)


# ── 4. build-region baseline binding + graceful skip ───────────────────────


RUN_DATE = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)


def _bind(var_key: str, region: str, fh: int = 12):
    spec = GFS_MODEL.get_var(var_key)
    return pipeline_module._resolve_build_region_baseline(
        model=MODEL,
        region=region,
        var_key=var_key,
        fh=fh,
        run_date=RUN_DATE,
        var_spec_model=spec,
        hints=_hints(var_key),
    )


def _install_global_accumulation_baseline(
    tmp_path: Path,
    *,
    field: str,
    reference_date: datetime,
    values: np.ndarray | None = None,
) -> Path:
    path = climatology.climatology_accumulation_baseline_path(
        data_root=tmp_path,
        version="v1",
        baseline_source=SOURCE,
        field=field,
        region=GLOBAL,
        reference_period="1991-2020",
        reference_date=reference_date,
    )
    if values is None:
        values = np.zeros((CONTRACT_HEIGHT, CONTRACT_WIDTH), dtype=np.float32)
    _write_raster(path, values, crs="EPSG:4326", transform=CONTRACT_TRANSFORM)
    return path


@pytest.mark.parametrize("var_key", WAVE1_VARS)
def test_canonical_build_is_untouched_by_the_binding(
    var_key: str, tmp_path: Path
) -> None:
    """No spec rewrite, no skip — even with no baseline installed anywhere."""
    climatology.configure_data_root(tmp_path)
    spec, hints, skip = _bind(var_key, CANONICAL)
    assert skip is None
    assert spec is GFS_MODEL.get_var(var_key)
    assert hints["baseline_region"] == CANONICAL


@pytest.mark.parametrize("var_key", WAVE1_VARS)
def test_global_build_skips_when_baselines_are_absent(
    var_key: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    climatology.configure_data_root(tmp_path)
    with caplog.at_level(logging.WARNING, logger=pipeline_module.logger.name):
        spec, hints, skip = _bind(var_key, GLOBAL)
    assert skip == "missing_baseline_assets"
    # The rewrite still happened — the skip is about the assets, not the wiring.
    assert hints["baseline_region"] == GLOBAL
    assert spec.selectors.hints["baseline_region"] == GLOBAL
    # …and the registry spec was copied, never mutated.
    assert GFS_MODEL.get_var(var_key).selectors.hints["baseline_region"] == CANONICAL

    messages = [record.getMessage() for record in caplog.records]
    assert any("climatology_baseline_missing" in message for message in messages)
    assert any(f"baseline_region={GLOBAL}" in message for message in messages)


@pytest.mark.parametrize("var_key", WAVE1_VARS)
def test_global_build_binds_the_global_baseline_when_installed(
    var_key: str, tmp_path: Path
) -> None:
    climatology.configure_data_root(tmp_path)
    _install_global_baseline(
        tmp_path,
        np.zeros((CONTRACT_HEIGHT, CONTRACT_WIDTH), dtype=np.float32),
        valid_time=RUN_DATE.replace(hour=12),
        field=WAVE1_BASELINE_FIELD[var_key],
    )
    spec, hints, skip = _bind(var_key, GLOBAL)
    assert skip is None
    assert hints["baseline_region"] == GLOBAL
    assert spec.selectors.hints["baseline_region"] == GLOBAL


# ── 4b. accumulation (precip window) baselines — Wave 2 ────────────────────


@pytest.mark.parametrize("var_key", PRECIP_ANOM_VARS)
def test_canonical_precip_build_is_untouched_by_the_binding(
    var_key: str, tmp_path: Path
) -> None:
    """The NA precip anomalies never reach the new accumulation pre-check.

    They resolve ``na``, which equals the declared region, so the binding
    returns the registry spec by identity and no skip — byte-identical to the
    pre-Wave-2 behaviour, including a missing NA baseline still *failing* the
    frame downstream rather than skipping it.
    """
    climatology.configure_data_root(tmp_path)
    spec, hints, skip = _bind(var_key, CANONICAL, fh=PRECIP_ANOM_TARGET_FH[var_key])
    assert skip is None
    assert spec is GFS_MODEL.get_var(var_key)
    assert hints["baseline_region"] == CANONICAL


@pytest.mark.parametrize("var_key", PRECIP_ANOM_VARS)
def test_global_precip_build_skips_when_accumulation_baselines_are_absent(
    var_key: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Was ``no_baseline_declared_for_build_region`` before the Wave 2 flip;
    now the override resolves and the *asset* pre-check produces the skip."""
    climatology.configure_data_root(tmp_path)
    with caplog.at_level(logging.WARNING, logger=pipeline_module.logger.name):
        spec, hints, skip = _bind(var_key, GLOBAL, fh=PRECIP_ANOM_TARGET_FH[var_key])
    assert skip == "missing_baseline_assets"
    assert hints["baseline_region"] == GLOBAL
    assert spec.selectors.hints["baseline_region"] == GLOBAL
    assert GFS_MODEL.get_var(var_key).selectors.hints["baseline_region"] == CANONICAL

    messages = [record.getMessage() for record in caplog.records]
    assert any("climatology_baseline_missing" in message for message in messages)
    assert any(
        f"baseline_field={PRECIP_ANOM_BASELINE_FIELD[var_key]}" in message
        for message in messages
    )


@pytest.mark.parametrize("var_key", PRECIP_ANOM_VARS)
def test_global_precip_build_binds_when_the_accumulation_baseline_is_installed(
    var_key: str, tmp_path: Path
) -> None:
    climatology.configure_data_root(tmp_path)
    # At the earliest buildable fh the window opens at the run itself, so the
    # frame needs exactly the run-date day-of-year asset.
    _install_global_accumulation_baseline(
        tmp_path,
        field=PRECIP_ANOM_BASELINE_FIELD[var_key],
        reference_date=RUN_DATE,
    )
    spec, hints, skip = _bind(var_key, GLOBAL, fh=PRECIP_ANOM_TARGET_FH[var_key])
    assert skip is None
    assert hints["baseline_region"] == GLOBAL
    assert spec.selectors.hints["baseline_region"] == GLOBAL


def test_rolling_precip_window_checks_the_window_start_date(tmp_path: Path) -> None:
    """The pre-check must follow the *window start*, not the run date.

    ``precip_5d_anom`` at fh240 accumulates fh120→fh240, so it subtracts the
    baseline for run+5 d. Installing only the run-date asset must still skip.
    """
    climatology.configure_data_root(tmp_path)
    _install_global_accumulation_baseline(
        tmp_path, field="precip_5d", reference_date=RUN_DATE
    )
    assert _bind("precip_5d_anom", GLOBAL, fh=240)[2] == "missing_baseline_assets"

    _install_global_accumulation_baseline(
        tmp_path,
        field="precip_5d",
        reference_date=RUN_DATE + timedelta(hours=120),
    )
    assert _bind("precip_5d_anom", GLOBAL, fh=240)[2] is None


def test_partial_baseline_install_skips_only_the_missing_window(
    tmp_path: Path,
) -> None:
    """One window directory missing ⇒ exactly one variable skips.

    This is the realistic half-installed prod state, and the property that
    makes deploy ordering survivable.
    """
    climatology.configure_data_root(tmp_path)
    for var_key in PRECIP_ANOM_VARS:
        if var_key == "precip_10d_anom":
            continue
        _install_global_accumulation_baseline(
            tmp_path,
            field=PRECIP_ANOM_BASELINE_FIELD[var_key],
            reference_date=RUN_DATE,
        )

    skips = {
        var_key: _bind(var_key, GLOBAL, fh=PRECIP_ANOM_TARGET_FH[var_key])[2]
        for var_key in PRECIP_ANOM_VARS
    }
    assert skips == {
        "precip_5d_anom": None,
        "precip_7d_anom": None,
        "precip_10d_anom": "missing_baseline_assets",
        "precip_16d_anom": None,
    }


def test_impossible_window_is_not_laundered_into_a_skip(tmp_path: Path) -> None:
    """fh below the accumulation window is a declaration bug, not a missing
    asset — the binding must stay out of the way and let the derive raise."""
    climatology.configure_data_root(tmp_path)
    assert _bind("precip_5d_anom", GLOBAL, fh=12)[2] is None
    with pytest.raises(ValueError, match="shorter than accumulation window"):
        climatology.resolve_accumulation_baseline_window(
            hints=_hints("precip_5d_anom"),
            var_key="precip_5d_anom",
            fh=12,
            run_date=RUN_DATE,
        )


#: Wave 2 covers every deterministic model with a global domain.
WAVE2_FLIPPED_MODELS = ("gfs", "aigfs", "aifs", "ecmwf")
#: …and no others. The ensembles have no global domain at all, so their precip
#: anomalies must carry no global routing hint.
WAVE2_EXCLUDED_MODELS = ("gefs", "eps")
#: Each model's TRUE window set, from its own catalog. GFS/AIGFS are the 16 d
#: family; AIFS/ECMWF are 15 d (ECMWF's 16 d entry was removed as dead in
#: 9a76a1c3 and must not come back).
WAVE2_WINDOWS_BY_MODEL = {
    "gfs": ("precip_5d_anom", "precip_7d_anom", "precip_10d_anom", "precip_16d_anom"),
    "aigfs": ("precip_5d_anom", "precip_7d_anom", "precip_10d_anom", "precip_16d_anom"),
    "aifs": ("precip_5d_anom", "precip_7d_anom", "precip_10d_anom", "precip_15d_anom"),
    "ecmwf": ("precip_5d_anom", "precip_7d_anom", "precip_10d_anom", "precip_15d_anom"),
}


def _precip_anom_keys(plugin) -> set[str]:
    return {
        var_key
        for var_key in plugin.capabilities.variable_catalog
        if var_key.startswith("precip_") and var_key.endswith("_anom")
    }


@pytest.mark.parametrize("model_id", WAVE2_FLIPPED_MODELS)
def test_wave2_flip_covers_each_models_true_window_set(model_id: str) -> None:
    """Per-model windows must match what that model's catalog actually derives.

    The shared helper takes the window from the spec, so 15 vs 16 needs no
    special-casing in the code — but a model declaring a window it does not
    derive (or missing one it does) would resolve a baseline field with no
    asset behind it, which the pre-check would then silently skip forever.
    """
    from app.models.registry import MODEL_REGISTRY

    plugin = MODEL_REGISTRY[model_id]
    assert _precip_anom_keys(plugin) == set(WAVE2_WINDOWS_BY_MODEL[model_id])

    catalog = plugin.capabilities.variable_catalog
    for var_key in WAVE2_WINDOWS_BY_MODEL[model_id]:
        spec = plugin.get_var(var_key)
        hints = dict(getattr(getattr(spec, "selectors", None), "hints", {}) or {})
        assert hints[climatology.BASELINE_REGION_BY_BUILD_REGION_HINT] == f"{GLOBAL}={GLOBAL}"
        assert hints["baseline_region"] == CANONICAL
        # The declared window must round-trip to the field name whose global
        # baseline directory the operator installed (5/7/10/15/16 d on prod).
        days = int(var_key.split("_", 2)[1].removesuffix("d"))
        assert hints["baseline_field"] == f"precip_{days}d"
        assert hints["accumulation_window_hours"] == str(days * 24)
        assert catalog[var_key].supported_build_regions == [CANONICAL, GLOBAL]


def test_ecmwf_family_is_15_day_and_gfs_family_is_16_day() -> None:
    """The one asymmetry that a copy-paste flip would get wrong."""
    assert "precip_16d_anom" in WAVE2_WINDOWS_BY_MODEL["gfs"]
    assert "precip_16d_anom" in WAVE2_WINDOWS_BY_MODEL["aigfs"]
    assert "precip_15d_anom" in WAVE2_WINDOWS_BY_MODEL["aifs"]
    assert "precip_15d_anom" in WAVE2_WINDOWS_BY_MODEL["ecmwf"]
    for model_id in ("aifs", "ecmwf"):
        assert "precip_16d_anom" not in WAVE2_WINDOWS_BY_MODEL[model_id]
    for model_id in ("gfs", "aigfs"):
        assert "precip_15d_anom" not in WAVE2_WINDOWS_BY_MODEL[model_id]


@pytest.mark.parametrize("model_id", WAVE2_EXCLUDED_MODELS)
def test_wave2_flip_did_not_leak_into_the_ensembles(model_id: str) -> None:
    """``_precip_anomaly_var_spec`` is shared by every model with precip
    anomalies, including the ensembles — which have NO global domain.

    The Wave 2 override is a per-caller keyword argument precisely so this set
    stays out of it. If it were baked into the factory (or given a truthy
    default), gefs and eps would start carrying a global routing hint for a
    domain they never build — this is the assertion that kills that mutation.
    """
    from app.models.registry import MODEL_REGISTRY

    plugin = MODEL_REGISTRY[model_id]
    hint = climatology.BASELINE_REGION_BY_BUILD_REGION_HINT
    catalog = plugin.capabilities.variable_catalog

    precip_keys = _precip_anom_keys(plugin)
    assert precip_keys, model_id
    for var_key in precip_keys:
        spec = plugin.get_var(var_key)
        hints = dict(getattr(getattr(spec, "selectors", None), "hints", {}) or {})
        assert hint not in hints, f"{model_id}/{var_key}"
        assert catalog[var_key].supported_build_regions == [], f"{model_id}/{var_key}"


def test_the_factory_default_is_no_global_routing() -> None:
    """Belt and braces on the same mutation, at the source."""
    import inspect

    from app.models.gfs import _precip_anomaly_var_spec

    default = inspect.signature(_precip_anomaly_var_spec).parameters[
        "baseline_region_by_build_region"
    ].default
    assert default is None
    assert climatology.BASELINE_REGION_BY_BUILD_REGION_HINT not in (
        _precip_anomaly_var_spec("precip_5d_anom", 5).selectors.hints or {}
    )


@pytest.mark.parametrize("var_key", PRECIP_ANOM_VARS)
def test_window_helper_matches_the_catalog_declaration(var_key: str) -> None:
    """The shared arithmetic, pinned against the real hints — this is the
    function both ``derive.py`` and the pipeline pre-check call, so a drift
    here would silently desynchronise the check from the load."""
    target_fh = PRECIP_ANOM_TARGET_FH[var_key]
    window = climatology.resolve_accumulation_baseline_window(
        hints=_hints(var_key), var_key=var_key, fh=target_fh, run_date=RUN_DATE
    )
    assert window.target_fh == target_fh
    assert window.accumulation_window_hours == target_fh
    assert window.window_start_fh == 0
    assert window.reference_date == RUN_DATE


@pytest.mark.parametrize(
    ("var_key", "fh", "expected_alignment_key"),
    (
        # Instantaneous: the frame is aligned to its own valid time.
        ("hgt500_anom", 12, "valid_time"),
        # Accumulation: aligned to the date the window OPENS, which is a
        # different quantity and is logged under a different key.
        ("precip_5d_anom", 120, "reference_date"),
        ("precip_16d_anom", 384, "reference_date"),
    ),
)
def test_build_frame_returns_the_skip_status_without_fetching(
    var_key: str,
    fh: int,
    expected_alignment_key: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End to end: no exception, no failed frame, no network — for BOTH
    baseline kinds. The precip arms are what prove the Wave 2 pre-check is
    wired into ``build_frame``, not just into the binding helper."""
    climatology.configure_data_root(tmp_path)
    with caplog.at_level(logging.WARNING, logger=pipeline_module.logger.name):
        result = pipeline_module.build_frame(
            model=MODEL,
            region=GLOBAL,
            var_id=var_key,
            fh=fh,
            run_date=RUN_DATE,
            data_root=tmp_path / "data",
            return_status=True,
        )
    assert result == (None, pipeline_module.BASELINE_SKIP_STATUS)
    assert pipeline_module.BASELINE_SKIP_STATUS == "skipped_missing_baseline"

    messages = [record.getMessage() for record in caplog.records]
    missing = [m for m in messages if "climatology_baseline_missing" in m]
    assert missing
    # The runbook greps this exact line; the alignment key must name the
    # quantity honestly rather than calling a window-start date a valid time.
    assert any(f"{expected_alignment_key}=" in m for m in missing)
    if expected_alignment_key == "reference_date":
        assert not any("valid_time=" in m for m in missing)

    # Nothing was staged.
    assert not (tmp_path / "data" / "staging").exists()


# ── 5. generation script ───────────────────────────────────────────────────


def test_build_script_global_target_grid_is_the_contract_grid() -> None:
    target = build_script.resolve_target_grid(baseline_source=SOURCE, region=GLOBAL)
    assert target.crs == "EPSG:4326"
    assert target.is_native_geographic
    assert target.resolution == 0.25
    assert (target.height, target.width) == (CONTRACT_HEIGHT, CONTRACT_WIDTH)
    assert target.transform == CONTRACT_TRANSFORM


def test_build_script_metre_target_grid_is_unchanged() -> None:
    target = build_script.resolve_target_grid(baseline_source=SOURCE, region=CANONICAL)
    transform, height, width = compute_transform_and_shape(
        REGION_BBOX_3857[CANONICAL], 25_000.0
    )
    assert target.crs == "EPSG:3857"
    assert target.resolution == 25_000.0
    assert (target.height, target.width) == (height, width)
    assert target.transform == transform


def test_build_script_global_warp_takes_the_identity_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staged ERA5 raster already sits on the contract grid, so the build
    must take the identity branch and not call ``reproject`` at all.

    NOTE on what this does and does not prove. The value assertion below is
    NOT what discriminates: a same-grid ``reproject`` on this fixture is
    *also* bit-identical, NaNs included (the source carries nodata=NaN, so
    nothing bleeds). Measured both ways — identical bytes either path. So the
    load-bearing assertion is the branch itself: the identity path is
    justified by skipping 43 830 pointless warps per field, and by being a
    guaranteed no-op rather than one that happens to be a no-op for this
    alignment. Sizing doc R6's "may perturb float bits" premise did not
    reproduce; the doc has been corrected.
    """
    target = build_script.resolve_target_grid(baseline_source=SOURCE, region=GLOBAL)
    values_k = np.full(
        (CONTRACT_HEIGHT, CONTRACT_WIDTH), 273.15, dtype=np.float32
    ) + _global_values()
    values_k[100:110, 200:210] = np.nan
    source_path = tmp_path / "1991042112_tmp2m.tif"
    _write_raster(source_path, values_k, crs="EPSG:4326", transform=CONTRACT_TRANSFORM)

    calls = {"same_grid": 0, "reproject": 0}
    real_is_same_grid = build_script._is_same_grid

    def _spy_is_same_grid(**kwargs):
        result = real_is_same_grid(**kwargs)
        calls["same_grid"] += 1
        calls["same_grid_result"] = result
        return result

    def _spy_reproject(*args, **kwargs):  # pragma: no cover - must not run
        calls["reproject"] += 1
        raise AssertionError("reproject must not run on the identity path")

    monkeypatch.setattr(build_script, "_is_same_grid", _spy_is_same_grid)
    monkeypatch.setattr(build_script, "reproject", _spy_reproject)

    warped = build_script._load_and_warp_source(
        build_script.SourceRaster(
            path=source_path,
            valid_time=datetime(1991, 4, 21, 12, tzinfo=timezone.utc),
        ),
        target=target,
        field="tmp2m",
        units_in="K",
        resampling="bilinear",
    )

    # The branch — this is what fails if the fast path is removed.
    assert calls["same_grid"] == 1
    assert calls["same_grid_result"] is True
    assert calls["reproject"] == 0

    expected = build_script._convert_values(values_k, field="tmp2m", units_in="K")
    assert warped.shape == (CONTRACT_HEIGHT, CONTRACT_WIDTH)
    assert warped.tobytes() == expected.tobytes()
    assert int(np.isnan(warped).sum()) == 100


def test_build_script_metre_region_still_reprojects(tmp_path: Path) -> None:
    """The identity branch is global-only: a metre target still warps."""
    target = build_script.resolve_target_grid(baseline_source=SOURCE, region=CANONICAL)
    source_path = tmp_path / "1991042112_tmp2m.tif"
    _write_raster(
        source_path,
        np.full((32, 32), 273.15, dtype=np.float32),
        crs="EPSG:4326",
        transform=from_origin(-140.0, 60.0, 1.0, 1.0),
    )
    warped = build_script._load_and_warp_source(
        build_script.SourceRaster(
            path=source_path,
            valid_time=datetime(1991, 4, 21, 12, tzinfo=timezone.utc),
        ),
        target=target,
        field="tmp2m",
        units_in="K",
        resampling="bilinear",
    )
    assert warped.shape == (target.height, target.width)


def test_build_script_writes_global_assets_on_the_contract_grid(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    data_root = tmp_path / "data"
    climatology.configure_data_root(data_root)
    values_k = np.full((CONTRACT_HEIGHT, CONTRACT_WIDTH), 273.15, dtype=np.float32)
    for year in (1991, 1992):
        _write_raster(
            source_root / f"{year}010112_tmp2m.tif",
            values_k,
            crs="EPSG:4326",
            transform=CONTRACT_TRANSFORM,
        )

    files_written, missing_buckets = build_script.build_climatology_assets(
        source_root=source_root,
        data_root=data_root,
        version="v1",
        baseline_source=SOURCE,
        field="tmp2m",
        region=GLOBAL,
        reference_period="1991-2020",
        units_in="K",
        smoothing_window_days=1,
        resampling="bilinear",
        start_year=1991,
        end_year=1992,
        require_complete=False,
    )
    assert files_written == 1
    assert missing_buckets == 1463

    path = climatology.climatology_baseline_path(
        data_root=data_root,
        version="v1",
        baseline_source=SOURCE,
        field="tmp2m",
        region=GLOBAL,
        reference_period="1991-2020",
        valid_time=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
    )
    assert path.name == "doy_001_h12.tif"
    assert path.is_file()
    with rasterio.open(path) as ds:
        assert ds.crs.to_epsg() == 4326
        assert (ds.height, ds.width) == (CONTRACT_HEIGHT, CONTRACT_WIDTH)
        assert ds.transform == CONTRACT_TRANSFORM
        # Baselines store °F for both temperature fields, same as NA.
        assert np.allclose(ds.read(1), 32.0)
        assert ds.tags(1)["reference_period"] == "1991-2020"


# ── 6. component warp onto the global baseline grid ────────────────────────


def test_global_climatology_grid_id_warps_by_index_roll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native-geographic baseline grid IS the domain grid, so components
    take the shared roll path — exact values, no interpolation, no lossy
    0…360 → −180…180 reprojection (contract §5).

    The NaN patch is what makes this discriminate. On a NaN-free fixture a
    plain ``reproject`` also equals ``np.roll`` exactly, so the value
    assertion alone would pass with the roll deleted. Here the component
    arrives with ``src_nodata=None`` (the derive path never declares one), so
    bilinear interpolation bleeds the 10x10 NaN block out to 11x11: 100 NaN
    cells on the roll path, 121 on the reproject path.
    """
    from app.services.builder import derive as derive_module
    from app.services.builder import raster_grid as raster_grid_module

    raw = np.arange(CONTRACT_HEIGHT * CONTRACT_WIDTH, dtype=np.float32).reshape(
        CONTRACT_HEIGHT, CONTRACT_WIDTH
    )
    raw[100:110, 200:210] = np.nan

    calls = {"roll": 0, "reproject": 0}
    real_roll = raster_grid_module._index_roll_to_target_grid

    def _spy_roll(*args, **kwargs):
        result = real_roll(*args, **kwargs)
        if result is not None:
            calls["roll"] += 1
        return result

    def _spy_reproject(*args, **kwargs):  # pragma: no cover - must not run
        calls["reproject"] += 1
        raise AssertionError("reproject must not run on the index-roll path")

    monkeypatch.setattr(
        raster_grid_module, "_index_roll_to_target_grid", _spy_roll
    )
    monkeypatch.setattr(raster_grid_module, "reproject", _spy_reproject)

    grid_id = "climatology:era5:global:0.25deg"
    warped, transform = derive_module._warp_component_to_target_grid(
        raw_data=raw,
        raw_crs=rasterio.crs.CRS.from_epsg(4326),
        # GFS source layout: lon centres 0 … 359.75, north-up.
        raw_transform=from_origin(-0.125, 90.125, 0.25, 0.25),
        model_id=MODEL,
        target_region=GLOBAL,
        target_grid_id=grid_id,
        resampling="bilinear",
    )
    assert calls == {"roll": 1, "reproject": 0}
    assert transform == CONTRACT_TRANSFORM
    assert np.array_equal(
        warped, np.roll(raw, CONTRACT_WIDTH // 2, axis=1), equal_nan=True
    )
    # Exactly the source NaNs, none smeared into neighbours.
    assert int(np.isnan(warped).sum()) == 100
    assert derive_module._warped_component_crs(MODEL, GLOBAL, grid_id).to_epsg() == 4326


def test_metre_climatology_grid_id_still_reprojects_to_3857() -> None:
    from app.services.builder import derive as derive_module

    grid_id = "climatology:era5:na:25000.0m"
    transform, height, width = compute_transform_and_shape(
        REGION_BBOX_3857[CANONICAL], 25_000.0
    )
    raw = np.ones((32, 32), dtype=np.float32)
    warped, dst_transform = derive_module._warp_component_to_target_grid(
        raw_data=raw,
        raw_crs=rasterio.crs.CRS.from_epsg(4326),
        raw_transform=from_origin(-140.0, 60.0, 1.0, 1.0),
        model_id=MODEL,
        target_region=CANONICAL,
        target_grid_id=grid_id,
        resampling="bilinear",
    )
    assert warped.shape == (height, width)
    assert dst_transform == transform
    assert derive_module._warped_component_crs(MODEL, CANONICAL, grid_id).to_epsg() == 3857


# ── 7. end-to-end anomaly derive on the global grid ────────────────────────


def test_hgt500_anomaly_derives_against_the_global_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole Wave-1 chain: bound spec → global baseline → 4326 output."""
    from app.services.builder.derive import FetchContext, derive_variable

    climatology.configure_data_root(tmp_path)
    run_date = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)

    # Baseline is stored in decametres; the anomaly converts dam → m.
    baseline_dam = np.full((CONTRACT_HEIGHT, CONTRACT_WIDTH), 552.0, dtype=np.float32)
    _install_global_baseline(
        tmp_path, baseline_dam, valid_time=run_date, field="hgt500"
    )

    # The component arrives in geopotential metres; convert_units turns it
    # into decametres before the departure is taken.
    forecast_m = np.full((CONTRACT_HEIGHT, CONTRACT_WIDTH), 5580.0, dtype=np.float32)

    def _fake_fetch_component_warped(**kwargs):
        assert kwargs["target_region"] == GLOBAL
        assert kwargs["target_grid_id"] == "climatology:era5:global:0.25deg"
        return (
            forecast_m,
            rasterio.crs.CRS.from_epsg(4326),
            CONTRACT_TRANSFORM,
        )

    monkeypatch.setattr(
        "app.services.builder.derive._fetch_component_warped",
        _fake_fetch_component_warped,
    )

    spec, _hints_out, skip = _bind("hgt500_anom", GLOBAL)
    assert skip is None

    grid, _matches = pipeline_module._resolve_derive_target_grid(
        model=MODEL,
        region=GLOBAL,
        hints=_hints_out,
        derive_component_warp_cache=True,
    )

    ctx = FetchContext(coverage=GLOBAL)
    anomaly, crs, transform = derive_variable(
        model_id=MODEL,
        var_key="hgt500_anom",
        product="pgrb2.0p25",
        run_date=run_date,
        fh=0,
        var_spec_model=spec,
        var_capability=GFS_MODEL.get_var_capability("hgt500_anom"),
        model_plugin=GFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid=grid,
        derive_component_resampling="bilinear",
    )

    assert anomaly.shape == (CONTRACT_HEIGHT, CONTRACT_WIDTH)
    assert crs.to_epsg() == 4326
    assert transform == CONTRACT_TRANSFORM
    # (558 − 552) dam → 60 m
    assert np.allclose(anomaly, 60.0, atol=1.0e-4)

    sidecar = ctx.derive_quality[("hgt500_anom", 0)]["sidecar_metadata"]
    assert sidecar["baseline_region"] == GLOBAL
    assert sidecar["reference_period"] == "1991-2020"


def _derive_global_precip_anomaly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    baseline_inches: float,
    run_date: datetime,
    fh: int,
    var_key: str = "precip_5d_anom",
):
    """Drive the full Wave-2 chain for one precip anomaly frame.

    Returns ``(anomaly, crs, transform, sidecar)``.

    The BASE component (``precip_total``, a 100+ step cumulative at these
    forecast hours) is stubbed to a constant rate rather than driven for real:
    at contract-grid size the real walk costs minutes per call and it is not
    what this test is about. Everything the Wave 2 flip touches — the binding,
    the target-grid resolution, the window arithmetic, the baseline load, the
    subtraction and the sidecar — runs unmodified. Note ``derive_variable`` is
    bound locally BEFORE the patch, so the top-level anomaly call is the real
    function and only the nested base lookup is stubbed.
    """
    from app.services.builder import derive as derive_module
    from app.services.builder.derive import FetchContext

    # NOT `derive_module.derive_variable`: a caller may invoke this helper
    # twice inside one test, and monkeypatch is only undone at teardown — the
    # second call would otherwise re-import the stub as its top-level entry
    # point and test nothing at all.
    derive_variable = REAL_DERIVE_VARIABLE

    climatology.configure_data_root(tmp_path)
    window = climatology.resolve_accumulation_baseline_window(
        hints=_hints(var_key), var_key=var_key, fh=fh, run_date=run_date
    )
    _install_global_accumulation_baseline(
        tmp_path,
        field=PRECIP_ANOM_BASELINE_FIELD[var_key],
        reference_date=window.reference_date,
        values=np.full(
            (CONTRACT_HEIGHT, CONTRACT_WIDTH), baseline_inches, dtype=np.float32
        ),
    )

    # Cumulative inches at forecast hour N, at a constant 0.01 in/h.
    seen_grids: list[tuple[str, str]] = []

    def _fake_base_derive(**kwargs):
        assert kwargs["var_key"] == "precip_total", kwargs["var_key"]
        grid_in = kwargs.get("derive_component_target_grid") or {}
        seen_grids.append((grid_in.get("region", ""), grid_in.get("id", "")))
        cumulative = np.full(
            (CONTRACT_HEIGHT, CONTRACT_WIDTH),
            int(kwargs["fh"]) * 0.01,
            dtype=np.float32,
        )
        return cumulative, rasterio.crs.CRS.from_epsg(4326), CONTRACT_TRANSFORM

    monkeypatch.setattr(derive_module, "derive_variable", _fake_base_derive)

    spec, hints_out, skip = _bind(var_key, GLOBAL, fh=fh)
    assert skip is None
    grid, _matches = pipeline_module._resolve_derive_target_grid(
        model=MODEL,
        region=GLOBAL,
        hints=hints_out,
        derive_component_warp_cache=True,
    )

    ctx = FetchContext(coverage=GLOBAL)
    anomaly, crs, transform = derive_variable(
        model_id=MODEL,
        var_key=var_key,
        product="pgrb2.0p25",
        run_date=run_date,
        fh=fh,
        var_spec_model=spec,
        var_capability=GFS_MODEL.get_var_capability(var_key),
        model_plugin=GFS_MODEL,
        fetch_ctx=ctx,
        derive_component_target_grid=grid,
        derive_component_resampling="bilinear",
    )
    # Every base warp must be aimed at the global baseline grid — if the
    # binding had leaked the NA grid, this is where it would show.
    assert seen_grids
    assert set(seen_grids) == {(GLOBAL, "climatology:era5:global:0.25deg")}

    sidecar = ctx.derive_quality[(var_key, fh)]["sidecar_metadata"]
    return anomaly, crs, transform, sidecar


def test_precip_anomaly_derives_against_the_global_accumulation_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole Wave-2 chain: binding → global target grid → derive → frame.

    Mirrors ``test_hgt500_anomaly_derives_against_the_global_baseline`` for the
    accumulation path, at a ROLLING fh (240 for a 5 d window) so the
    window-start arithmetic is exercised rather than the degenerate
    ``window_start_fh == 0`` case.
    """
    run_date = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
    anomaly, crs, transform, sidecar = _derive_global_precip_anomaly(
        tmp_path, monkeypatch, baseline_inches=0.0, run_date=run_date, fh=240
    )

    assert anomaly.shape == (CONTRACT_HEIGHT, CONTRACT_WIDTH)
    assert crs.to_epsg() == 4326
    assert transform == CONTRACT_TRANSFORM
    assert np.all(np.isfinite(anomaly))
    # Baseline is zero, so the anomaly IS the window accumulation. At the
    # stubbed 0.01 in/h that is fh240 − fh120 = 2.40 − 1.20 = 1.20 in — i.e.
    # the rolling difference was taken, not the run-to-fh240 total.
    assert np.allclose(anomaly, 1.20, atol=1.0e-5)

    assert sidecar["baseline_region"] == GLOBAL
    assert sidecar["baseline_kind"] == "climatology"
    assert sidecar["reference_period"] == "1991-2020"
    assert sidecar["anomaly_kind"] == "accumulated_precip_departure"
    assert sidecar["baseline_temporal_resolution"] == "daily_accumulation_window"
    # fh240 with a 120 h window ⇒ the window opens at fh120, five days in.
    assert sidecar["target_fh"] == 240
    assert sidecar["window_start_fh"] == 120
    assert sidecar["window_end_fh"] == 240
    assert sidecar["accumulation_window_hours"] == 120
    assert sidecar["baseline_reference_fh"] == 120
    assert sidecar["baseline_alignment"] == "window_start_date"
    assert sidecar["baseline_reference_doy"] == (
        run_date + timedelta(hours=120)
    ).timetuple().tm_yday


def test_precip_anomaly_subtracts_exactly_the_global_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two runs differing only in the baseline must differ by exactly that.

    This is what proves the global baseline is actually subtracted — the test
    above would still pass if the baseline were ignored entirely.
    """
    run_date = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
    zero, _c0, _t0, _s0 = _derive_global_precip_anomaly(
        tmp_path / "a", monkeypatch, baseline_inches=0.0, run_date=run_date, fh=240
    )
    one, _c1, _t1, _s1 = _derive_global_precip_anomaly(
        tmp_path / "b", monkeypatch, baseline_inches=1.25, run_date=run_date, fh=240
    )
    assert np.allclose(zero - one, 1.25, atol=1.0e-5)


def test_static_window_precip_anomaly_aligns_to_the_run_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``precip_16d_anom`` is fh384-only with a 384 h window, so its window
    opens at the run itself — the ``init_date`` alignment arm."""
    run_date = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
    anomaly, _crs, _transform, sidecar = _derive_global_precip_anomaly(
        tmp_path,
        monkeypatch,
        baseline_inches=0.0,
        run_date=run_date,
        fh=384,
        var_key="precip_16d_anom",
    )
    # Window opens at the run, so the whole run-to-fh384 total is the window.
    assert np.allclose(anomaly, 3.84, atol=1.0e-5)
    assert sidecar["window_start_fh"] == 0
    assert sidecar["baseline_alignment"] == "init_date"
    assert sidecar["baseline_reference_doy"] == run_date.timetuple().tm_yday


# ── 8. accumulation baseline serving path (Wave 2) ─────────────────────────
#
# `load_accumulation_climatology_baseline` was EPSG:3857-only by deliberate
# Wave-1 deferral. It now resolves through the same `get_baseline_grid`
# registry as the instantaneous loader, so the global precip windows validate
# against the contract grid instead of being rejected outright.


def _install_accumulation_baseline(
    tmp_path: Path,
    values: np.ndarray,
    *,
    region: str,
    crs: str,
    transform,
    reference_date: datetime,
    field: str = "precip_5d",
) -> Path:
    path = climatology.climatology_accumulation_baseline_path(
        data_root=tmp_path,
        version="v1",
        baseline_source=SOURCE,
        field=field,
        region=region,
        reference_period="1991-2020",
        reference_date=reference_date,
    )
    _write_raster(path, values, crs=crs, transform=transform)
    return path


def test_global_accumulation_baseline_loads_and_validates(tmp_path: Path) -> None:
    climatology.configure_data_root(tmp_path)
    reference_date = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
    values = _global_values()
    _install_accumulation_baseline(
        tmp_path,
        values,
        region=GLOBAL,
        crs="EPSG:4326",
        transform=CONTRACT_TRANSFORM,
        reference_date=reference_date,
    )

    loaded, crs, transform, meta = climatology.load_accumulation_climatology_baseline(
        version="v1",
        baseline_source=SOURCE,
        field="precip_5d",
        reference_date=reference_date,
        region=GLOBAL,
        reference_period="1991-2020",
    )
    assert loaded.shape == (CONTRACT_HEIGHT, CONTRACT_WIDTH)
    assert np.allclose(loaded, values)
    assert crs.to_epsg() == 4326
    assert transform == CONTRACT_TRANSFORM
    assert meta["baseline_region"] == GLOBAL
    assert meta["baseline_temporal_resolution"] == "daily_accumulation_window"
    assert meta["baseline_reference_doy"] == reference_date.timetuple().tm_yday


def test_metre_accumulation_baseline_is_unchanged(tmp_path: Path) -> None:
    climatology.configure_data_root(tmp_path)
    reference_date = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
    transform, height, width = compute_transform_and_shape(
        REGION_BBOX_3857[CANONICAL], 25_000.0
    )
    values = np.full((height, width), 1.25, dtype=np.float32)
    _install_accumulation_baseline(
        tmp_path,
        values,
        region=CANONICAL,
        crs="EPSG:3857",
        transform=transform,
        reference_date=reference_date,
    )

    loaded, crs, loaded_transform, meta = climatology.load_accumulation_climatology_baseline(
        version="v1",
        baseline_source=SOURCE,
        field="precip_5d",
        reference_date=reference_date,
        region=CANONICAL,
        reference_period="1991-2020",
    )
    assert loaded.shape == (height, width)
    assert crs.to_epsg() == 3857
    assert loaded_transform == transform
    assert meta["baseline_region"] == CANONICAL


@pytest.mark.parametrize(
    ("region", "crs", "transform", "expected_message"),
    [
        (GLOBAL, "EPSG:3857", CONTRACT_TRANSFORM, "must use EPSG:4326"),
        (CANONICAL, "EPSG:4326", CONTRACT_TRANSFORM, "must use EPSG:3857"),
    ],
)
def test_accumulation_baseline_rejects_the_wrong_crs(
    tmp_path: Path,
    region: str,
    crs: str,
    transform,
    expected_message: str,
) -> None:
    climatology.configure_data_root(tmp_path)
    reference_date = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
    _install_accumulation_baseline(
        tmp_path,
        _global_values(),
        region=region,
        crs=crs,
        transform=transform,
        reference_date=reference_date,
    )
    with pytest.raises(ValueError, match=expected_message):
        climatology.load_accumulation_climatology_baseline(
            version="v1",
            baseline_source=SOURCE,
            field="precip_5d",
            reference_date=reference_date,
            region=region,
            reference_period="1991-2020",
        )


def test_global_accumulation_baseline_rejects_the_wrong_shape(tmp_path: Path) -> None:
    climatology.configure_data_root(tmp_path)
    reference_date = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
    _install_accumulation_baseline(
        tmp_path,
        np.zeros((180, 360), dtype=np.float32),
        region=GLOBAL,
        crs="EPSG:4326",
        transform=from_origin(-180.5, 90.5, 1.0, 1.0),
        reference_date=reference_date,
    )
    with pytest.raises(ValueError, match="grid shape mismatch"):
        climatology.load_accumulation_climatology_baseline(
            version="v1",
            baseline_source=SOURCE,
            field="precip_5d",
            reference_date=reference_date,
            region=GLOBAL,
            reference_period="1991-2020",
        )


def test_global_accumulation_baseline_rejects_a_half_cell_offset(tmp_path: Path) -> None:
    climatology.configure_data_root(tmp_path)
    reference_date = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
    _install_accumulation_baseline(
        tmp_path,
        _global_values(),
        region=GLOBAL,
        crs="EPSG:4326",
        transform=Affine(0.25, 0.0, -180.0, 0.0, -0.25, 90.0),
        reference_date=reference_date,
    )
    with pytest.raises(ValueError, match="transform mismatch"):
        climatology.load_accumulation_climatology_baseline(
            version="v1",
            baseline_source=SOURCE,
            field="precip_5d",
            reference_date=reference_date,
            region=GLOBAL,
            reference_period="1991-2020",
        )


def test_accumulation_baseline_for_an_unknown_region_raises(tmp_path: Path) -> None:
    climatology.configure_data_root(tmp_path)
    reference_date = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
    _install_accumulation_baseline(
        tmp_path,
        _global_values(),
        region="atlantis",
        crs="EPSG:4326",
        transform=CONTRACT_TRANSFORM,
        reference_date=reference_date,
    )
    with pytest.raises(KeyError):
        climatology.load_accumulation_climatology_baseline(
            version="v1",
            baseline_source=SOURCE,
            field="precip_5d",
            reference_date=reference_date,
            region="atlantis",
            reference_period="1991-2020",
        )
