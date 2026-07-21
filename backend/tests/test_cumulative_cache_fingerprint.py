"""Regression tests for the fingerprinted cumulative-cache grid key.

Wave 1 item 1: the cumulative-cache key is a stable fingerprint of
(grid identity, strategy identity, explicit algorithm revision, normalized
hints). Hint changes and code-only semantic changes (via the revision bump)
must automatically invalidate prior-cumulative caches, because the on-load
validation exact-string-matches the stored key.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from rasterio.crs import CRS

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import derive as derive_module


def _grid_key(strategy_id: str, hints: dict | None) -> str:
    return derive_module._cumulative_cache_grid_key(
        use_warped=True,
        target_grid_id="hrrr:conus:3000.0m",
        resampling="bilinear",
        strategy_id=strategy_id,
        hints=hints,
    )


def _store(
    tmp_path: Path,
    *,
    grid_cache_key: str,
    fh: int = 34,
    model_id: str = "hrrr",
    var_key: str = "precip_total",
) -> None:
    ctx = derive_module.FetchContext(coverage="conus")
    setattr(ctx, "data_root", str(tmp_path))
    derive_module._kuchera_store_cumulative_cache(
        model_id=model_id,
        run_date=datetime(2026, 3, 25, 12, 0),
        var_key=var_key,
        fh=fh,
        data=np.ones((2, 2), dtype=np.float32),
        crs=CRS.from_epsg(4326),
        transform=derive_module.rasterio.transform.Affine(1.0, 0.0, 3.0, 0.0, -1.0, 4.0),
        ctx=ctx,
        grid_cache_key=grid_cache_key,
    )


def _load(
    tmp_path: Path,
    *,
    grid_cache_key: str,
    fh: int = 34,
    model_id: str = "hrrr",
    var_key: str = "precip_total",
):
    # Fresh ctx WITHOUT the in-memory cache so we exercise the on-disk
    # exact-string validation, not the ctx dict shortcut.
    ctx = derive_module.FetchContext(coverage="conus")
    setattr(ctx, "data_root", str(tmp_path))
    return derive_module._kuchera_load_prior_cumulative(
        model_id=model_id,
        run_date=datetime(2026, 3, 25, 12, 0),
        var_key=var_key,
        fh=fh,
        ctx=ctx,
        grid_cache_key=grid_cache_key,
    )


def test_hints_change_invalidates_cumulative_cache(tmp_path: Path) -> None:
    """Storing under hints A and loading under hints B must miss."""
    key_a = _grid_key("precip_total_cumulative", {"min_step_lwe_kgm2": "0.01"})
    key_b = _grid_key("precip_total_cumulative", {"min_step_lwe_kgm2": "0.05"})
    assert key_a != key_b, "differing hints must produce differing keys"

    _store(tmp_path, grid_cache_key=key_a)
    # Same hints A round-trips.
    assert _load(tmp_path, grid_cache_key=key_a) is not None
    # Hints B is ignored — old-semantics entry not reused.
    assert _load(tmp_path, grid_cache_key=key_b) is None


def test_revision_bump_invalidates_cumulative_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bumping the explicit algorithm revision must invalidate prior caches."""
    hints = {"min_step_lwe_kgm2": "0.01"}
    current_revision = derive_module.CUMULATIVE_ALGORITHM_REVISIONS["precip_total_cumulative"]
    key_current = _grid_key("precip_total_cumulative", hints)
    _store(tmp_path, grid_cache_key=key_current)
    assert _load(tmp_path, grid_cache_key=key_current) is not None

    monkeypatch.setitem(
        derive_module.CUMULATIVE_ALGORITHM_REVISIONS,
        "precip_total_cumulative",
        current_revision + 1,
    )
    key_bumped = _grid_key("precip_total_cumulative", hints)
    assert key_bumped != key_current
    assert f":r={current_revision + 1}:" in key_bumped
    assert _load(tmp_path, grid_cache_key=key_bumped) is None


def test_fingerprint_is_insertion_order_stable() -> None:
    """Identical hints in different dict orders produce identical keys."""
    key_1 = _grid_key(
        "precip_total_cumulative",
        {"slr": "12", "min_step_lwe_kgm2": "0.01", "cadence": "6h"},
    )
    key_2 = _grid_key(
        "precip_total_cumulative",
        {"cadence": "6h", "min_step_lwe_kgm2": "0.01", "slr": "12"},
    )
    assert key_1 == key_2


def test_strategy_id_partitions_the_namespace() -> None:
    """Same hints, different strategy id must yield different keys."""
    hints = {"min_step_lwe_kgm2": "0.01"}
    assert _grid_key("precip_total_cumulative", hints) != _grid_key(
        "snowfall_total_10to1_cumulative", hints
    )


def test_legacy_old_format_entry_is_ignored(tmp_path: Path) -> None:
    """An entry stored under the OLD key format is ignored, without error."""
    # Old format: `warped:{grid}:{resampling}` with no strategy/revision/hash.
    _store(tmp_path, grid_cache_key="warped:hrrr:conus:3000.0m:bilinear")
    new_key = _grid_key("precip_total_cumulative", {"min_step_lwe_kgm2": "0.01"})
    assert _load(tmp_path, grid_cache_key=new_key) is None


def test_manual_cache_version_hint_still_changes_key() -> None:
    """The manual escape hatch (cumulative_cache_version) still bumps the key."""
    base = _grid_key("precip_total_cumulative", {"min_step_lwe_kgm2": "0.01"})
    bumped = _grid_key(
        "precip_total_cumulative",
        {"min_step_lwe_kgm2": "0.01", "cumulative_cache_version": "manual_v2"},
    )
    assert base != bumped


def test_unknown_strategy_id_raises_key_error() -> None:
    """A strategy that forgot to register a revision fails loud."""
    with pytest.raises(KeyError):
        _grid_key("not_a_registered_strategy", {})


def test_cumulative_algorithm_revisions_are_pinned() -> None:
    """Semantic changes must deliberately update the affected revision only."""
    assert derive_module.CUMULATIVE_ALGORITHM_REVISIONS == {
        "precip_total_cumulative": 3,
        "snowfall_total_10to1_cumulative": 2,
        "snowfall_kuchera_total_cumulative": 3,
        "ptype_accumulation_cumulative": 3,
        "ptype_accumulation_ecmwf": 2,
    }


def test_key_format_shape() -> None:
    """Pin the documented key format: {base}:s={id}:r={rev}:h={12 hex}."""
    revision = derive_module.CUMULATIVE_ALGORITHM_REVISIONS["precip_total_cumulative"]
    key = _grid_key("precip_total_cumulative", {"a": "1"})
    assert key.startswith(
        f"warped:hrrr:conus:3000.0m:bilinear:s=precip_total_cumulative:r={revision}:h="
    )
    hints_hash = key.rsplit(":h=", 1)[1]
    assert len(hints_hash) == 12
    int(hints_hash, 16)  # valid hex

    native = derive_module._cumulative_cache_grid_key(
        use_warped=False,
        target_grid_id="ignored",
        resampling="ignored",
        strategy_id="precip_total_cumulative",
        hints=None,
    )
    assert native.startswith(f"native:s=precip_total_cumulative:r={revision}:h=")


# ---------------------------------------------------------------------------
# Kuchera apcp seed key: must be computed with the WRITER's identity.
#
# The apcp seed is a precip_total cumulative entry WRITTEN by the
# precip_total_cumulative strategy under precip_total's OWN identity
# (strategy_id="precip_total_cumulative" + precip_total's var-spec hints). The
# Kuchera reader must recompute that key from precip_total's spec, not its own,
# so the seed correctly misses when precip_total's semantics change.
# ---------------------------------------------------------------------------

# precip_total's own hints (writer identity) — deliberately different from what
# a Kuchera var spec would carry.
_PRECIP_TOTAL_HINTS = {"apcp_component": "apcp_step", "min_step_lwe_kgm2": "0.01"}


class _FakePlugin:
    """Minimal model plugin exposing precip_total's spec via get_var."""

    def __init__(self, *, precip_hints: dict | None, has_precip: bool = True) -> None:
        self._precip_hints = precip_hints
        self._has_precip = has_precip

    def normalize_var_id(self, var_key: str) -> str:
        return str(var_key)

    def get_var(self, var_key: str):
        if str(var_key) == "precip_total" and self._has_precip:
            return SimpleNamespace(selectors=SimpleNamespace(hints=self._precip_hints))
        return None


def _seed_key(plugin: _FakePlugin, precip_var_id: str = "precip_total") -> str | None:
    return derive_module._precip_seed_cache_key(
        model_plugin=plugin,
        precip_var_id=precip_var_id,
        use_warped=True,
        target_grid_id="hrrr:conus:3000.0m",
        resampling="bilinear",
    )


def test_apcp_seed_key_uses_precip_total_writer_identity(tmp_path: Path) -> None:
    """The seed reader key matches the precip_total_cumulative WRITER key and
    an entry stored under the writer key loads back through the seed key."""
    seed_key = _seed_key(_FakePlugin(precip_hints=_PRECIP_TOTAL_HINTS))
    writer_key = _grid_key("precip_total_cumulative", _PRECIP_TOTAL_HINTS)
    assert seed_key == writer_key

    # A precip_total entry written under precip_total's identity is served to
    # the Kuchera seed reader.
    _store(tmp_path, grid_cache_key=writer_key)
    assert _load(tmp_path, grid_cache_key=seed_key) is not None

    # Kuchera's OWN cumulative key (different strategy id) is NOT the seed key —
    # reading precip_total under it is exactly the prod regression being fixed.
    kuchera_key = _grid_key("snowfall_kuchera_total_cumulative", _PRECIP_TOTAL_HINTS)
    assert seed_key != kuchera_key
    assert _load(tmp_path, grid_cache_key=kuchera_key) is None


def test_apcp_seed_key_invalidated_by_precip_total_revision_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bumping precip_total_cumulative's revision changes the seed key, so a
    stale precip_total seed is correctly ignored."""
    plugin = _FakePlugin(precip_hints=_PRECIP_TOTAL_HINTS)
    current_revision = derive_module.CUMULATIVE_ALGORITHM_REVISIONS["precip_total_cumulative"]
    key_current = _seed_key(plugin)
    assert f":s=precip_total_cumulative:r={current_revision}:" in key_current

    monkeypatch.setitem(
        derive_module.CUMULATIVE_ALGORITHM_REVISIONS,
        "precip_total_cumulative",
        current_revision + 1,
    )
    key_bumped = _seed_key(plugin)
    assert key_bumped != key_current
    assert f":s=precip_total_cumulative:r={current_revision + 1}:" in key_bumped


def test_apcp_seed_key_none_when_precip_total_unresolvable() -> None:
    """If precip_total's spec can't be resolved, the seed key is None → the
    reader misses and falls back to recompute (never raises)."""
    assert _seed_key(_FakePlugin(precip_hints=None, has_precip=False)) is None


# ---------------------------------------------------------------------------
# Production-spec regressions: the seed reader key must equal the
# precip_total_cumulative WRITER key for the SAME precip var, using the REAL
# model registry specs — not stubs. The existing stub tests can't catch this
# class because their mocked loaders discard grid_cache_key
# (test_gfs_snowfall_derive.py). These probe the three consumer strategies the
# external review confirmed: GFS 10:1 snowfall, GEFS __mean 10:1 snowfall, and
# GFS ptype accumulation.
# ---------------------------------------------------------------------------

from app.models.gefs import GEFS_MODEL  # noqa: E402
from app.models.gfs import GFS_MODEL  # noqa: E402

# Fixed grid identity shared by reader and writer key computations — the base
# is unchanged by this fix, so it must not be what distinguishes the keys.
_REAL_GRID = {
    "use_warped": True,
    "target_grid_id": "gfs:conus:3000.0m",
    "resampling": "bilinear",
}


def _real_reader_key(plugin, precip_var_id: str) -> str:
    """Key the fixed consumer strategy computes for the precip seed read."""
    return derive_module._precip_seed_cache_key(
        model_plugin=plugin, precip_var_id=precip_var_id, **_REAL_GRID
    )


def _real_writer_key(plugin, precip_var_id: str) -> str:
    """Key precip_total_cumulative WRITES the precip var's cumulative under."""
    spec = plugin.get_var(plugin.normalize_var_id(precip_var_id))
    return derive_module._cumulative_cache_grid_key(
        strategy_id="precip_total_cumulative",
        hints=spec.selectors.hints,
        **_REAL_GRID,
    )


def _real_strategy_own_key(plugin, derived_var_id: str, strategy_id: str) -> str:
    """The PRE-FIX (buggy) reader key: the consumer strategy's own id + the
    DERIVED var's hints. This is the mismatch the review flagged."""
    spec = plugin.get_var(plugin.normalize_var_id(derived_var_id))
    return derive_module._cumulative_cache_grid_key(
        strategy_id=strategy_id,
        hints=spec.selectors.hints,
        **_REAL_GRID,
    )


# (plugin, derived var id, its cumulative strategy id, the precip var it seeds)
_REAL_CASES = [
    (GFS_MODEL, "snowfall_total", "snowfall_total_10to1_cumulative", "precip_total"),
    (GEFS_MODEL, "snowfall_total__mean", "snowfall_total_10to1_cumulative", "precip_total__mean"),
    (GFS_MODEL, "ice_total", "ptype_accumulation_cumulative", "precip_total"),
]


@pytest.mark.parametrize(
    "plugin, derived_var, strategy_id, precip_var",
    _REAL_CASES,
    ids=["gfs_snowfall_10to1", "gefs_snowfall_mean_10to1", "gfs_ptype"],
)
def test_real_spec_precip_seed_reader_key_matches_writer(
    plugin, derived_var, strategy_id, precip_var
) -> None:
    """Reader key (fixed) == writer key for the same precip var, and the
    PRE-FIX key (consumer strategy's own) does NOT match — reproducing the
    reviewer's probe that returned False on all three pairs."""
    reader = _real_reader_key(plugin, precip_var)
    writer = _real_writer_key(plugin, precip_var)
    assert reader == writer, f"{derived_var}: reader seed key must equal writer key"
    assert ":s=precip_total_cumulative:" in reader

    old = _real_strategy_own_key(plugin, derived_var, strategy_id)
    assert old != writer, f"{derived_var}: pre-fix strategy-own key must NOT match writer"
    assert f":s={strategy_id}:" in old


def test_gefs_snowfall_precip_component_resolves_to_mean_spec() -> None:
    """The GEFS 10:1 snowfall spec seeds precip_total__mean, and that __mean
    var resolves to a real spec whose hints are what precip_total_cumulative
    uses when building it — so the seed round-trips on GEFS."""
    snow_hints = GEFS_MODEL.get_var(
        GEFS_MODEL.normalize_var_id("snowfall_total__mean")
    ).selectors.hints
    precip_var = snow_hints["precip_cumulative_component"]
    assert precip_var == "precip_total__mean"

    # The __mean precip var resolves and carries hints (not stripped/empty).
    precip_spec = GEFS_MODEL.get_var(GEFS_MODEL.normalize_var_id(precip_var))
    assert precip_spec is not None
    assert precip_spec.selectors.hints  # non-empty

    assert _real_reader_key(GEFS_MODEL, precip_var) == _real_writer_key(
        GEFS_MODEL, precip_var
    )


def test_real_spec_gefs_snowfall_seed_loads_through_reader_path(tmp_path: Path) -> None:
    """Integration: a precip_total__mean cumulative written under the WRITER
    key is served to the GEFS 10:1 snowfall reader path, and is NOT found under
    the pre-fix snowfall-strategy key."""
    writer = _real_writer_key(GEFS_MODEL, "precip_total__mean")
    reader = _real_reader_key(GEFS_MODEL, "precip_total__mean")
    _store(tmp_path, grid_cache_key=writer, model_id="gefs", var_key="precip_total__mean")

    assert (
        _load(tmp_path, grid_cache_key=reader, model_id="gefs", var_key="precip_total__mean")
        is not None
    )
    old = _real_strategy_own_key(
        GEFS_MODEL, "snowfall_total__mean", "snowfall_total_10to1_cumulative"
    )
    assert (
        _load(tmp_path, grid_cache_key=old, model_id="gefs", var_key="precip_total__mean")
        is None
    )


def test_real_spec_gfs_ptype_seed_loads_through_reader_path(tmp_path: Path) -> None:
    """Integration: a precip_total cumulative written under the WRITER key is
    served to the GFS ptype reader path, and is NOT found under the pre-fix
    ptype-strategy key."""
    writer = _real_writer_key(GFS_MODEL, "precip_total")
    reader = _real_reader_key(GFS_MODEL, "precip_total")
    _store(tmp_path, grid_cache_key=writer, model_id="gfs", var_key="precip_total")

    assert (
        _load(tmp_path, grid_cache_key=reader, model_id="gfs", var_key="precip_total")
        is not None
    )
    old = _real_strategy_own_key(GFS_MODEL, "ice_total", "ptype_accumulation_cumulative")
    assert (
        _load(tmp_path, grid_cache_key=old, model_id="gfs", var_key="precip_total")
        is None
    )
