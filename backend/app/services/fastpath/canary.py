"""Once-per-run cross-source canary for the fast path (design §6 and §8, WP4).

What it is
==========

The prototype's Phase 4 reconciliation, rerun automatically every cycle against
one frame. Phase 4 measured the fast (9 km O1280) path against the 0.25°
reference on 2026-08-04 and found the signature we want — near-zero bias, tight
synoptic agreement, real fine-scale differences. That was *one day* of evidence.
This module turns it into a continuous guarantee: after both sources have
finished a run, recompute bias / MAE / synoptic MAE / correlation for a couple
of fast-owned variables on one frame and alert if any of them drift.

**Alert, do not auto-disable** (design §6, resolved decision). A failing canary
emits a ``fastpath_canary_failed`` structured log and a metric; the kill switch
stays the human-operated ownership flip (``CARTOSKY_FASTPATH_VAR_OVERRIDES``),
because a canary that can disable the fast path by itself is a canary whose own
bugs cause outages.

The reference source
====================

The comparison needs "what the delayed path would have produced". Two ways to
get it:

1. Fetch the delayed source's GRIB through the production Herbie path and warp
   it with the builder's rasterio pipeline.
2. Fetch the **``ecmwf_ifs025`` 0.25° ``.om`` object** for the same valid time
   through the WP1 source modules and warp it with WP1's ``regular025``
   bilinear sampler.

This module does (2), which is also what ``phase4_reconcile.py`` did. The
justification is measured, not assumed: Phase 2 showed ``ifs025`` matches the
delayed 0.25° product to **0.020 °C MAE** (README "Phase 2 verdict"), and the
builder's rasterio warp is bilinear just like WP1's sampler — so the two
references are the same field to well inside every threshold below. What (2)
buys is that the whole canary runs through code the fast path already depends
on: no Herbie session, no GDAL, no idx parsing, no second failure surface in a
component whose entire job is to tell us when something is wrong. A canary that
is itself fragile produces false alarms, and false alarms get muted.

The cost is the same either way (one frame per run) and the trap is the same
either way: **units differ per product for the same variable name**. ``ifs025``
stores ``pressure_msl`` in hPa where the 9 km files store Pa (README "Units
gotcha"). :data:`REFERENCE_VARIABLES` therefore lists the reference variables
explicitly instead of reusing the fast catalog wholesale, so a variable can only
be canaried after someone has audited its units on *both* products.

What is compared
================

* **Instantaneous** variables (``tmp2m``, ``dp2m``, ``wgst10m``): the published
  frame value against the reference object's field at the same valid time.
* **Accumulation** variables (``precip_total``): the published frame's
  *increment* over its own window — cumulative(fh) − cumulative(fh − step) —
  against the reference's per-step values summed over the same interval.
  Production stores run-cumulative totals (design §4), and reconstructing a
  run-cumulative reference would mean fetching every reference step from FH000:
  ~30 objects instead of one, for a comparison whose measured Phase 4 baseline
  ("precip 3 h fh93") was a per-step comparison anyway.

Variables whose packed binary is not the model grid are skipped. Display prep
runs before packing, so a variable configured with an upscale (GFS
``precip_total``, GEFS ``snowfall_total__mean``) stores a *finer* raster than
the regrid target and the two arrays would not align. ECMWF's fast-owned set
happens to be entirely shape-preserving today — its ``snowfall_total`` entry is
``upscale_factor=1`` — but the guard is on the config, not on a list of
variable names, so the day one of them gains an upscale the canary skips it
loudly instead of comparing misaligned grids. See :func:`_display_prep_blocks`.

Failover seams (design §8)
==========================

The canary also audits any recorded failover seam in the run, from the run
manifest's ``source_ranges`` (which ``_summarize_source_ranges`` writes from
per-frame provenance). For each mixed-source ``(var, domain)``:

* **accumulation** variables get a value-continuity check across the seam — the
  run-cumulative series must not step *backwards*. ``enforce_failover_deadline``
  purges an accumulation pair's fast frames precisely so the delayed path
  rebuilds the whole series and no seam exists; this check is what proves that
  invariant held on real data rather than only in the failover tests.
* **instantaneous** variables are recorded but not failed: a 9 km→0.25° seam is
  legitimate there (design §6), it is what the per-variable resolution label
  exists to explain.

Flag gating
===========

Everything here is behind :func:`~.ownership.fastpath_enabled`. With
``CARTOSKY_FASTPATH_MODELS`` unset :func:`maybe_run_canary` returns ``None``
before importing anything, and this module is never imported at all from a
scheduler whose fast path is off.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

import numpy as np

from .ownership import fast_owned_pairs, fastpath_enabled
from .state import FastpathRunState

logger = logging.getLogger(__name__)

#: Fast-owned variables the canary checks each run (design §6: "2–3"). Both
#: kinds are represented deliberately — an instantaneous variable exercises the
#: regrid/units path, an accumulation variable exercises the WP2 ledger, and
#: they are the two ways this pipeline can be wrong. Adding a third
#: instantaneous variable is nearly free (``wgst10m`` rides the same reference
#: object fetch as ``tmp2m``); adding a second accumulation variable costs
#: another fetch.
DEFAULT_CANARY_VARS: tuple[str, ...] = ("tmp2m", "precip_total")

#: Where in the run to sample. Both sources publish on a 3 h ladder at and
#: beyond FH090 (below it the 9 km stream is hourly and ``ifs025`` is not), so a
#: comparison there needs no cadence reconciliation on either side — the same
#: reason ``phase4_reconcile.py`` pinned its precipitation case to FH093. FH096
#: is comfortably inside the 144 h horizon of the short (06z/18z) cycles too, so
#: one constant serves every cycle.
PREFERRED_CANARY_FH = 96

#: Mean-pool factor for the synoptic-scale metric, per artifact domain: ~1° of
#: longitude on that domain's grid. Ported from ``phase4_reconcile.coarsen``,
#: whose ``k=12`` was 12 × 9 km on the NA grid.
COARSEN_FACTOR_BY_DOMAIN: Mapping[str, int] = {
    "na": 12,  # 12 × 9 km
    "global": 4,  # 4 × 0.25°
}


@dataclass(frozen=True)
class CanaryThresholds:
    """Alert thresholds for one variable, **in that variable's storage unit**.

    Design §6 states them as "bias ≈ 0, synoptic MAE ≤ 0.2 °C-equivalent,
    corr ≥ 0.95". Metrics are computed on the values that actually ship, which
    are °F for temperatures and inches for precipitation, so the °C-equivalent
    numbers are translated per variable rather than compared against raw
    Celsius constants — see :data:`THRESHOLDS_BY_VAR` for the arithmetic.
    """

    max_abs_bias: float
    max_synoptic_mae: float
    min_corr: float = 0.95


#: The generic design §6 numbers, for a variable with no entry below. Stated in
#: Celsius-equivalent units, which is only meaningful for a temperature — any
#: variable that reaches this default should get its own audited entry.
DEFAULT_THRESHOLDS = CanaryThresholds(
    max_abs_bias=0.05, max_synoptic_mae=0.2, min_corr=0.95
)

#: Per-variable thresholds. Each is the design §6 number carried into the
#: variable's storage unit, and each is checked against the spike's measured
#: baseline so the alert has real headroom over known-good behaviour but would
#: still catch a drift of the size a units bug or an orientation flip produces.
#:
#: =============  =========  ===================  ======================
#: variable       unit       threshold            measured baseline
#: =============  =========  ===================  ======================
#: tmp2m / dp2m   °F         bias 0.09 (0.05 °C)  bias −0.004 °C  (Ph. 4)
#:                           synoptic 0.36        synoptic 0.079 °C
#: wgst10m        mph        bias 0.11 (0.05 m/s) bias +0.000 m/s (Ph. 4)
#:                           synoptic 0.45        synoptic 0.100 m/s
#: precip_total   in         bias 0.004 (0.1 mm)  bias ≤0.0012 mm (Ph. 3)
#:                           synoptic 0.008       synoptic 0.014 mm (Ph. 4)
#: =============  =========  ===================  ======================
#:
#: One threshold is **not** a straight unit conversion: precipitation's bias
#: limit is 0.1 mm, a deliberate 2× relaxation of the §6 constant (0.05 in the
#: variable's units, i.e. 0.05 mm here). The reason is the compared quantity —
#: unlike the instantaneous variables, precipitation is checked as a difference
#: of two independently quantised cumulative frames, so it carries quantisation
#: noise the §6 constant was never scoped against. The measured bias is
#: ≤0.0012 mm (spike README "Phase 3 verdict", where the per-step convention
#: was reconciled against ``ifs025``), so 0.1 mm still leaves ~80× headroom.
#: Phase 4 did not report a precipitation bias figure in mm; its precipitation
#: line is the synoptic MAE quoted above.
THRESHOLDS_BY_VAR: Mapping[str, CanaryThresholds] = {
    "tmp2m": CanaryThresholds(max_abs_bias=0.09, max_synoptic_mae=0.36, min_corr=0.95),
    "dp2m": CanaryThresholds(max_abs_bias=0.09, max_synoptic_mae=0.36, min_corr=0.95),
    "wgst10m": CanaryThresholds(max_abs_bias=0.11, max_synoptic_mae=0.45, min_corr=0.95),
    # Phase 4 measured corr 0.9695 for a 3 h precipitation step — the lowest of
    # the four cases and the reason 0.95 is the floor rather than 0.99.
    #
    # Precipitation's own packing quantum is 0.01 in, and the compared quantity
    # is a DIFFERENCE of two independently quantised cumulative frames, so a
    # single cell carries up to ±0.02 in of rounding noise. That noise is
    # zero-mean (so it does not move the bias) and the ~1° mean-pool divides it
    # by the pool's side length, leaving ≈0.001 in on the coarsest domain —
    # comfortably under the 0.008 in synoptic threshold, which is itself ~15×
    # the 0.014 mm Phase 4 measured value. It is also why the bias limit is
    # relaxed 2× above the §6 constant (see this table's preamble).
    "precip_total": CanaryThresholds(
        max_abs_bias=0.004, max_synoptic_mae=0.008, min_corr=0.95
    ),
}

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"

#: Skip reasons that will never resolve on their own, so the run's canary can
#: latch on them: they are statements about configuration, not about the state
#: of the world at this moment.
#:
#: Everything NOT listed here is treated as *retryable* and produces
#: :data:`STATUS_ERROR`, which deliberately does not latch. The distinction
#: matters because the latch is one-shot per run: a single upstream hiccup — a
#: reference object that momentarily omits a variable, a frame whose promote is
#: still in the retry queue — must not permanently forgo the run's only canary.
#: Retries are bounded anyway, because the scheduler drives the canary from the
#: run the delayed probe resolved, and that advances every cycle.
TERMINAL_SKIP_REASONS = frozenset(
    {
        "display_prep_shape",  # the packed binary is not the model grid
        "no_om_counterpart",  # the fast catalog cannot produce this variable
        "not_a_window_boundary",  # this fh is not an accumulation frame
        "shape_mismatch",  # grid config drift; re-reading cannot fix it
    }
)

__all__ = [
    "COARSEN_FACTOR_BY_DOMAIN",
    "DEFAULT_CANARY_VARS",
    "DEFAULT_THRESHOLDS",
    "PREFERRED_CANARY_FH",
    "STATUS_ERROR",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "TERMINAL_SKIP_REASONS",
    "THRESHOLDS_BY_VAR",
    "CanaryOutcome",
    "CanaryResult",
    "CanaryRun",
    "CanaryThresholds",
    "SeamCheck",
    "check_failover_seams",
    "coarsen",
    "compare_frame",
    "maybe_run_canary",
    "reference_catalog_for",
    "run_canary_for_run",
    "select_canary_fh",
    "thresholds_for",
]


def thresholds_for(var_id: str) -> CanaryThresholds:
    return THRESHOLDS_BY_VAR.get(str(var_id).strip().lower(), DEFAULT_THRESHOLDS)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanaryResult:
    """Phase 4 reconcile metrics for one ``(var, domain, fh)``."""

    var_id: str
    domain: str
    fh: int
    #: Cells compared (finite on both sides).
    n: int
    bias: float
    mae: float
    synoptic_mae: float
    corr: float
    unit: str = ""
    reference_source: str = ""
    #: Threshold names that tripped. Empty means the frame agrees.
    failures: tuple[str, ...] = ()
    #: Non-fatal observations (a degenerate correlation, a dry precip frame).
    notes: tuple[str, ...] = ()

    @property
    def failed(self) -> bool:
        return bool(self.failures)

    def to_json(self) -> dict[str, Any]:
        return {
            "var": self.var_id,
            "domain": self.domain,
            "fh": int(self.fh),
            "n": int(self.n),
            "bias": _jsonable(self.bias),
            "mae": _jsonable(self.mae),
            "synoptic_mae": _jsonable(self.synoptic_mae),
            "corr": _jsonable(self.corr),
            "unit": self.unit,
            "reference_source": self.reference_source,
            "failed": self.failed,
            "failures": list(self.failures),
            "notes": list(self.notes),
        }


class CanaryOutcome(NamedTuple):
    """What :func:`compare_frame` produced: a result, or why there is none.

    An absent comparison is always a *skip*, never a failure — "we could not
    look" and "we looked and it is wrong" must not page the same way. But the
    two kinds of skip differ in whether the run should try again, which is what
    :attr:`retryable` answers (see :data:`TERMINAL_SKIP_REASONS`).
    """

    result: CanaryResult | None
    #: Empty when :attr:`result` is present; otherwise a reason token, possibly
    #: with a ``:detail`` suffix.
    skip_reason: str = ""

    @property
    def retryable(self) -> bool:
        if self.result is not None or not self.skip_reason:
            return False
        return self.skip_reason.split(":", 1)[0] not in TERMINAL_SKIP_REASONS


@dataclass(frozen=True)
class SeamCheck:
    """Audit of one recorded failover seam (design §8)."""

    var_id: str
    domain: str
    seam_fh: int
    from_source: str
    to_source: str
    #: True for accumulation variables, where a seam is a correctness concern
    #: rather than a labelling one.
    continuity_checked: bool
    #: Fraction of cells whose run-cumulative value went DOWN across the seam.
    negative_jump_fraction: float = 0.0
    #: Worst backwards step, in the variable's storage unit (0 when none).
    max_negative_jump: float = 0.0
    ok: bool = True
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "var": self.var_id,
            "domain": self.domain,
            "seam_fh": int(self.seam_fh),
            "from_source": self.from_source,
            "to_source": self.to_source,
            "continuity_checked": bool(self.continuity_checked),
            "negative_jump_fraction": _jsonable(self.negative_jump_fraction),
            "max_negative_jump": _jsonable(self.max_negative_jump),
            "ok": bool(self.ok),
            "note": self.note,
        }


@dataclass(frozen=True)
class CanaryRun:
    """Everything one run's canary produced."""

    model_id: str
    run_id: str
    status: str
    completed_at: str
    results: tuple[CanaryResult, ...] = ()
    seams: tuple[SeamCheck, ...] = ()
    skip_reason: str = ""

    @property
    def failed_results(self) -> tuple[CanaryResult, ...]:
        return tuple(result for result in self.results if result.failed)

    @property
    def failed_seams(self) -> tuple[SeamCheck, ...]:
        return tuple(seam for seam in self.seams if not seam.ok)

    def to_json(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "run": self.run_id,
            "status": self.status,
            "completed_at": self.completed_at,
            "skip_reason": self.skip_reason,
            "results": [result.to_json() for result in self.results],
            "seams": [seam.to_json() for seam in self.seams],
        }


def _jsonable(value: float) -> float | None:
    number = float(value)
    return None if not np.isfinite(number) else number


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Reference catalog
# ---------------------------------------------------------------------------

#: ``.om`` variables the canary may read from the 0.25° reference product, and
#: the conversion each needs to reach CartoSky storage units.
#:
#: This list is an explicit **units audit**, not a convenience copy of the fast
#: catalog: the same variable name carries different units per product in the
#: same bucket (``pressure_msl`` is Pa in ``ecmwf_ifs`` and hPa in
#: ``ecmwf_ifs025`` — spike README "Units gotcha"). A variable only appears here
#: after someone has confirmed the reference product's unit matches the fast
#: product's, so an un-audited variable simply cannot be canaried.
REFERENCE_OM_VARIABLES: tuple[str, ...] = (
    "temperature_2m",  # °C on both products
    "dew_point_2m",  # °C on both products
    "precipitation",  # mm per step on both products
    "wind_gusts_10m",  # m/s on both products
)


def reference_catalog_for(catalog: Any) -> Any:
    """The 0.25° sibling product used as the canary's delayed-path stand-in.

    Built from the fast catalog so cadence/attribution stay in one place, with
    the three things that genuinely differ overridden: bucket directory, grid
    kind, and point count. Only :data:`REFERENCE_OM_VARIABLES` are carried over
    (see that constant for why).

    ECMWF-specific by data, not by code: a model whose bucket has no 0.25°
    sibling raises, and the canary skips cleanly.
    """
    import dataclasses  # noqa: PLC0415
    from types import MappingProxyType  # noqa: PLC0415

    from ..sources.openmeteo.catalog import GridKind  # noqa: PLC0415
    from ..sources.openmeteo.grids import REGULAR_025_SOURCE  # noqa: PLC0415

    model_id = str(getattr(catalog, "model_id", "") or "").strip().lower()
    bucket_dir = REFERENCE_BUCKET_BY_MODEL.get(model_id)
    if not bucket_dir:
        raise KeyError(
            f"no 0.25° reference product declared for {model_id!r} — the canary "
            f"needs a second product on the same valid times to compare against"
        )
    keep = frozenset(REFERENCE_OM_VARIABLES)
    return dataclasses.replace(
        catalog,
        model_id=f"{model_id}_reference",
        bucket_dir=bucket_dir,
        grid=GridKind.REGULAR_025,
        source_points=REGULAR_025_SOURCE.points,
        source_resolution_label="0.25° source",
        fast_domains=(),
        derived_variables=(),
        # ``dataclasses.replace`` copies field REFERENCES, and
        # ``source_cadence`` is a plain dict on the live catalog — so without
        # this the reference would alias it, and anything that touched the
        # reference's cadence would silently rewrite the ladder the production
        # fast path builds against. Fresh dict, then frozen: the reference is
        # a read-only view by construction, not by convention. (The remaining
        # fields carried over — ``target_cadence``, ``attribution`` — are
        # tuples and strings, already immutable.)
        source_cadence=MappingProxyType(dict(catalog.source_cadence)),
        variables=tuple(
            variable for variable in catalog.variables if variable.om_name in keep
        ),
    )


#: Fast model id → the bucket directory of its 0.25° sibling product.
REFERENCE_BUCKET_BY_MODEL: Mapping[str, str] = {
    "ecmwf": "ecmwf_ifs025",
}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def coarsen(values: np.ndarray, k: int) -> np.ndarray:
    """~1° mean-pool for the synoptic-agreement metric.

    Ported from ``scripts/spikes/openmeteo_9km/phase4_reconcile.coarsen``, with
    ``k`` a parameter because 1° is 12 cells on the NA 9 km grid and 4 cells on
    the global 0.25° grid. ``k <= 1`` is the identity.
    """
    array = np.asarray(values, dtype=np.float64)
    factor = int(k)
    if factor <= 1:
        return array
    height, width = array.shape
    if height < factor or width < factor:
        return array
    trimmed = array[: height // factor * factor, : width // factor * factor]
    return trimmed.reshape(
        height // factor, factor, width // factor, factor
    ).mean(axis=(1, 3))


#: A field whose standard deviation is below this fraction of its own scale is
#: treated as constant for the purpose of the correlation metric.
FLAT_FIELD_RELATIVE_STD = 1e-5


def _is_flat(values: np.ndarray) -> bool:
    scale = abs(float(np.mean(values))) + 1.0
    return float(np.std(values)) <= FLAT_FIELD_RELATIVE_STD * scale


def _metrics(
    fast: np.ndarray, reference: np.ndarray, *, coarsen_factor: int
) -> tuple[int, float, float, float, float, tuple[str, ...]]:
    """``(n, bias, mae, synoptic_mae, corr, notes)`` for two aligned fields.

    Cells that are non-finite on either side are excluded from the pointwise
    metrics (a NaN in one product must not poison the other's statistics) and
    are zero-filled for the mean-pool, which is what makes the synoptic metric
    comparable between a masked and an unmasked frame.
    """
    fast_f = np.asarray(fast, dtype=np.float64)
    reference_f = np.asarray(reference, dtype=np.float64)
    valid = np.isfinite(fast_f) & np.isfinite(reference_f)
    n = int(np.count_nonzero(valid))
    notes: list[str] = []
    if n == 0:
        return 0, float("nan"), float("nan"), float("nan"), float("nan"), (
            "no_overlapping_finite_cells",
        )

    difference = fast_f[valid] - reference_f[valid]
    bias = float(difference.mean())
    mae = float(np.abs(difference).mean())

    zeroed_fast = np.where(valid, fast_f, 0.0)
    zeroed_reference = np.where(valid, reference_f, 0.0)
    synoptic_mae = float(
        np.abs(
            coarsen(zeroed_fast, coarsen_factor) - coarsen(zeroed_reference, coarsen_factor)
        ).mean()
    )

    # A flat field (a fully dry precipitation frame, say) has no variance, so
    # the correlation is undefined rather than bad. Report it as a note; the
    # threshold check skips a non-finite correlation for the same reason.
    #
    # "Flat" is relative, not exactly zero: a resampled constant field carries
    # float round-off from the bilinear weights, and a std of 1e-5 on a field
    # whose mean is 59 is numerically flat — correlating against it produces a
    # meaningless number near zero, which would then read as a total
    # disagreement between the sources. Real weather is nowhere near this
    # threshold (a 2 m temperature field's std/mean is O(0.1)).
    if _is_flat(fast_f[valid]) or _is_flat(reference_f[valid]):
        corr = float("nan")
        notes.append("degenerate_correlation_constant_field")
    else:
        corr = float(np.corrcoef(fast_f[valid], reference_f[valid])[0, 1])

    return n, bias, mae, synoptic_mae, corr, tuple(notes)


def _evaluate(
    *, var_id: str, bias: float, synoptic_mae: float, corr: float
) -> tuple[str, ...]:
    thresholds = thresholds_for(var_id)
    failures: list[str] = []
    if not np.isfinite(bias) or abs(bias) > thresholds.max_abs_bias:
        failures.append("bias")
    if not np.isfinite(synoptic_mae) or synoptic_mae > thresholds.max_synoptic_mae:
        failures.append("synoptic_mae")
    # A non-finite correlation means "undefined" (constant field), not "bad" —
    # failing on it would alert on every dry precipitation frame.
    if np.isfinite(corr) and corr < thresholds.min_corr:
        failures.append("corr")
    return tuple(failures)


# ---------------------------------------------------------------------------
# Reading the published frame
# ---------------------------------------------------------------------------


def _display_prep_blocks(model_id: str, var_id: str) -> str:
    """Why this variable's binary cannot be compared cell-for-cell, or ``""``.

    ``write_grid_frame_for_run_root`` runs ``prepare_grid_display_values``
    before packing, so a variable configured with an upscale stores a *finer*
    raster than the model grid the canary regrids onto. Rather than reimplement
    the inverse — which would compare the canary against its own
    reimplementation of display prep, not against the product — such variables
    are skipped and said so out loud.

    The test is ``display_prep_shape_group``, the production classifier, so
    this cannot drift from the packing: group 1 (no prep, or prep that keeps
    the raster shape) is comparable; groups 2–4 are not.
    """
    from ..grid_display_prep import (  # noqa: PLC0415
        display_prep_shape_group,
        grid_display_prep_config,
    )

    config = grid_display_prep_config(model_id, var_id)
    group = display_prep_shape_group(config)
    if group == 1:
        return ""
    return f"display_prep_group_{group}"


def _read_published_frame(
    *, data_root: Path, model_id: str, run_id: str, var_id: str, domain: str, fh: int
) -> np.ndarray | None:
    """Decode one PUBLISHED frame to physical values, or ``None`` if absent.

    Uses the production decode authority (``grid._decode_values`` via
    ``sampling._read_binary_frame_values``) rather than reimplementing
    ``value = code * scale + offset``, so the canary can never disagree with
    the packing math the viewer sees.
    """
    from ..grid import grid_frame_meta_path, grid_frame_path  # noqa: PLC0415
    from ..sampling import (  # noqa: PLC0415
        _binary_encoded_dtype,
        _load_binary_frame_meta,
        _read_binary_frame_values,
    )

    resolved_dtype, _ = _binary_encoded_dtype(model_id, var_id)
    frame_path = grid_frame_path(
        data_root, model_id, run_id, var_id, fh, domain=domain, dtype=resolved_dtype
    )
    meta_path = grid_frame_meta_path(
        data_root, model_id, run_id, var_id, fh, domain=domain
    )
    if not frame_path.exists() or not meta_path.exists():
        return None
    meta = _load_binary_frame_meta(meta_path)
    return _read_binary_frame_values(frame_path, meta, model=model_id, var=var_id)


def _packing_scale(model_id: str, var_id: str) -> float:
    from ..grid import _packing_config  # noqa: PLC0415

    packing = _packing_config(model_id, var_id)
    return float(packing["scale"]) if packing else 0.0


# ---------------------------------------------------------------------------
# Reading the reference
# ---------------------------------------------------------------------------


def _reference_object_url(
    *, reference_catalog: Any, run_dt: datetime, fh: int, base_url: str | None
) -> str:
    """``{run dir}/{valid time}.om`` for the reference product.

    Built rather than listed: the reference run directory and the object naming
    are the bucket's uniform layout (WP1 ``source`` module docstring), and a
    LIST per instantaneous comparison would be a needless round trip. The
    accumulation path *does* list, because there it needs to know which steps
    exist.
    """
    from ..sources.openmeteo.catalog import BUCKET_BASE_URL  # noqa: PLC0415
    from ..sources.openmeteo.source import object_url, run_prefix  # noqa: PLC0415

    valid_dt = run_dt + timedelta(hours=int(fh))
    key = f"{run_prefix(reference_catalog, run_dt)}{valid_dt:%Y-%m-%dT%H%M}.om"
    return object_url(key, base_url=base_url or BUCKET_BASE_URL)


def _reference_field(
    *,
    reference_catalog: Any,
    om_source: Any,
    om_name: str,
    run_dt: datetime,
    fh: int,
    base_url: str | None = None,
) -> np.ndarray | None:
    """The reference product's field at one valid time, on its own 0.25° grid."""
    from ..sources.openmeteo import grids  # noqa: PLC0415

    url = _reference_object_url(
        reference_catalog=reference_catalog, run_dt=run_dt, fh=fh, base_url=base_url
    )
    fields = om_source.fetch_frame_fields(reference_catalog, url, (om_name,), "global")
    values = fields.get(om_name)
    if values is None:
        return None
    array = np.asarray(values, dtype=np.float32)
    if array.ndim != 2:
        array = array.reshape(
            grids.REGULAR_025_SOURCE.nlat, grids.REGULAR_025_SOURCE.nlon
        )
    return array


def _regrid_reference(
    *, reference_catalog: Any, domain: str, field_2d: np.ndarray
) -> np.ndarray:
    """Warp a 0.25° reference field onto the production target for ``domain``.

    The same ``regular025`` bilinear sampler WP1 already builds — which is also
    what ``phase4_reconcile.regular025_bilinear`` did, and what the builder's
    rasterio bilinear warp of the delayed GRIB does.

    Row order is **detected, never assumed** (spike README gotcha: the 0.25°
    products are south-first while the 9 km files in the same bucket are
    north-first). The sampler expects the source grid's declared order, so a
    product that ever flips is normalised here rather than silently producing a
    mirrored reference — which would read as a catastrophic canary failure
    instead of the upstream layout change it actually is.
    """
    from ..sources.openmeteo import grids  # noqa: PLC0415

    from .subloop import TARGET_BY_DOMAIN  # noqa: PLC0415

    target = TARGET_BY_DOMAIN.get(str(domain))
    if target is None:
        raise KeyError(f"canary: no regrid target for domain {domain!r}")

    array = np.asarray(field_2d, dtype=np.float32)
    source = grids.REGULAR_025_SOURCE
    if source.south_to_north and not grids.detect_south_to_north(array, source):
        logger.warning(
            "fastpath_canary: reference product rows are north-first this run; "
            "flipping to the declared south-first order before sampling"
        )
        array = array[::-1]
    sampler = grids.get_sampler("regular025", target)
    return grids.apply_sampler(sampler, array.ravel())


# ---------------------------------------------------------------------------
# One frame
# ---------------------------------------------------------------------------


def compare_frame(
    *,
    data_root: Path,
    model_id: str,
    run_dt: datetime,
    run_id: str,
    var_id: str,
    domain: str,
    fh: int,
    catalog: Any,
    reference_catalog: Any,
    om_source: Any,
    base_url: str | None = None,
) -> CanaryOutcome:
    """Recompute the Phase 4 metrics for one published frame.

    Returns a :class:`CanaryOutcome`: either a result, or a skip reason.
    An absent comparison is a skip, never a failure, because "we could not
    look" and "we looked and it is wrong" must not page the same way — and the
    reason token tells the caller whether looking again could help.
    """
    blocked = _display_prep_blocks(model_id, var_id)
    if blocked:
        logger.info(
            "fastpath_canary: skipping %s/%s — %s means the packed binary is not "
            "the model grid",
            var_id,
            domain,
            blocked,
        )
        return CanaryOutcome(None, f"display_prep_shape:{blocked}")

    om_variable = _om_variable_for(catalog, var_id)
    if om_variable is None:
        logger.info("fastpath_canary: %s has no .om counterpart — skipping", var_id)
        return CanaryOutcome(None, "no_om_counterpart")

    from ..sources.openmeteo.catalog import VariableKind  # noqa: PLC0415

    is_accumulation = om_variable.kind is VariableKind.ACCUMULATION
    if is_accumulation:
        pair, reason = _accumulation_increment(
            data_root=data_root,
            model_id=model_id,
            run_id=run_id,
            run_dt=run_dt,
            var_id=var_id,
            domain=domain,
            fh=fh,
            catalog=catalog,
            reference_catalog=reference_catalog,
            om_source=om_source,
            om_variable=om_variable,
            base_url=base_url,
        )
    else:
        pair, reason = _instantaneous_pair(
            data_root=data_root,
            model_id=model_id,
            run_id=run_id,
            run_dt=run_dt,
            var_id=var_id,
            domain=domain,
            fh=fh,
            reference_catalog=reference_catalog,
            om_source=om_source,
            om_variable=om_variable,
            base_url=base_url,
        )
    if pair is None:
        return CanaryOutcome(None, reason or "unavailable")
    fast_values, reference_values = pair
    if fast_values.shape != reference_values.shape:
        logger.error(
            "fastpath_canary: %s/%s fh%03d shape mismatch fast=%s reference=%s — "
            "skipping rather than comparing misaligned grids",
            var_id,
            domain,
            fh,
            fast_values.shape,
            reference_values.shape,
        )
        return CanaryOutcome(None, "shape_mismatch")

    factor = int(COARSEN_FACTOR_BY_DOMAIN.get(str(domain), 1))
    n, bias, mae, synoptic_mae, corr, notes = _metrics(
        fast_values, reference_values, coarsen_factor=factor
    )
    if n == 0:
        # Nothing overlapped, so every metric is NaN. Reporting that as a
        # threshold failure would contradict this function's own contract —
        # there was no comparison to fail. It is a skip, and a retryable one:
        # a frame that is entirely nodata this poll (a partial write, a
        # masked-out domain) may well not be on the next.
        logger.warning(
            "fastpath_canary: %s/%s fh%03d has no overlapping finite cells — "
            "skipping rather than reporting NaN metrics as a failure",
            var_id,
            domain,
            fh,
        )
        return CanaryOutcome(None, "no_overlapping_finite_cells")

    return CanaryOutcome(
        CanaryResult(
            var_id=var_id,
            domain=domain,
            fh=int(fh),
            n=n,
            bias=bias,
            mae=mae,
            synoptic_mae=synoptic_mae,
            corr=corr,
            unit=str(om_variable.production_unit or ""),
            reference_source=str(reference_catalog.bucket_dir),
            failures=_evaluate(
                var_id=var_id, bias=bias, synoptic_mae=synoptic_mae, corr=corr
            ),
            notes=notes,
        )
    )


def _om_variable_for(catalog: Any, var_id: str) -> Any | None:
    for variable in catalog.enabled_variables():
        if variable.cartosky_var == var_id and not variable.component_only:
            return variable
    return None


def _instantaneous_pair(
    *,
    data_root: Path,
    model_id: str,
    run_id: str,
    run_dt: datetime,
    var_id: str,
    domain: str,
    fh: int,
    reference_catalog: Any,
    om_source: Any,
    om_variable: Any,
    base_url: str | None,
) -> tuple[tuple[np.ndarray, np.ndarray] | None, str]:
    fast_values = _read_published_frame(
        data_root=data_root,
        model_id=model_id,
        run_id=run_id,
        var_id=var_id,
        domain=domain,
        fh=fh,
    )
    if fast_values is None:
        return None, "frame_not_published"
    field = _reference_field(
        reference_catalog=reference_catalog,
        om_source=om_source,
        om_name=om_variable.om_name,
        run_dt=run_dt,
        fh=int(fh),
        base_url=base_url,
    )
    if field is None:
        # ``fetch_frame_fields`` drops variables the object does not carry
        # rather than raising, so a truncated or still-being-written reference
        # object looks exactly like this. Retryable: the object may be complete
        # on the next poll.
        logger.warning(
            "fastpath_canary: reference object for %s fh%03d carries no %s",
            var_id,
            fh,
            om_variable.om_name,
        )
        return None, "reference_field_missing"
    regridded = _regrid_reference(
        reference_catalog=reference_catalog, domain=domain, field_2d=field
    )
    return (
        fast_values,
        np.asarray(om_variable.convert(regridded), dtype=np.float32),
    ), ""


def _accumulation_increment(
    *,
    data_root: Path,
    model_id: str,
    run_id: str,
    run_dt: datetime,
    var_id: str,
    domain: str,
    fh: int,
    catalog: Any,
    reference_catalog: Any,
    om_source: Any,
    om_variable: Any,
    base_url: str | None,
) -> tuple[tuple[np.ndarray, np.ndarray] | None, str]:
    """Published increment over this frame's window vs the reference's steps.

    The published series is run-cumulative (design §4), so the frame's own
    contribution is ``cumulative(fh) − cumulative(previous frame)``. The
    reference contributes the **sum of its per-step values** over the same
    interval, taken from the objects that actually exist in its run directory —
    liveness rule again, since the reference's step ladder (3-hourly for
    ``ifs025``) is not the fast product's.
    """
    from ..sources.openmeteo.catalog import aggregation_boundaries  # noqa: PLC0415

    windows = {
        window.target_fh: window
        for window in aggregation_boundaries(catalog, int(run_dt.hour))
    }
    window = windows.get(int(fh))
    if window is None:
        logger.info(
            "fastpath_canary: FH%03d is not an accumulation window boundary for %s",
            fh,
            var_id,
        )
        return None, "not_a_window_boundary"
    previous_fh = int(fh) - int(window.step_hours)

    cumulative = _read_published_frame(
        data_root=data_root,
        model_id=model_id,
        run_id=run_id,
        var_id=var_id,
        domain=domain,
        fh=fh,
    )
    if cumulative is None:
        return None, "frame_not_published"
    if previous_fh > 0:
        previous = _read_published_frame(
            data_root=data_root,
            model_id=model_id,
            run_id=run_id,
            var_id=var_id,
            domain=domain,
            fh=previous_fh,
        )
        if previous is None:
            return None, "frame_not_published"
        fast_increment = cumulative - previous
    else:
        # FH000 publishes no accumulation frame, so the first window's
        # cumulative value IS its increment.
        fast_increment = cumulative

    try:
        objects = om_source.list_run_objects(reference_catalog, run_dt)
    except Exception as exc:
        logger.warning("fastpath_canary: reference LIST failed (%s)", exc)
        return None, "reference_list_failed"
    steps = [
        run_object
        for run_object in objects
        if previous_fh < int(run_object.fh) <= int(fh)
    ]
    if not steps:
        logger.info(
            "fastpath_canary: reference product has no steps in (FH%03d, FH%03d]",
            previous_fh,
            fh,
        )
        return None, "reference_steps_missing"

    total: np.ndarray | None = None
    for run_object in steps:
        fields = om_source.fetch_frame_fields(
            reference_catalog, run_object.url, (om_variable.om_name,), "global"
        )
        values = fields.get(om_variable.om_name)
        if values is None:
            logger.warning(
                "fastpath_canary: reference FH%03d carries no %s — cannot sum the "
                "window",
                run_object.fh,
                om_variable.om_name,
            )
            return None, "reference_field_missing"
        array = np.asarray(values, dtype=np.float64)
        total = array if total is None else total + array
    assert total is not None

    from ..sources.openmeteo import grids  # noqa: PLC0415

    reshaped = total.reshape(
        grids.REGULAR_025_SOURCE.nlat, grids.REGULAR_025_SOURCE.nlon
    )
    regridded = _regrid_reference(
        reference_catalog=reference_catalog,
        domain=domain,
        field_2d=reshaped.astype(np.float32),
    )
    return (
        np.asarray(fast_increment, dtype=np.float32),
        np.asarray(om_variable.convert(regridded), dtype=np.float32),
    ), ""


# ---------------------------------------------------------------------------
# Failover seams (design §8)
# ---------------------------------------------------------------------------


def check_failover_seams(
    *,
    data_root: Path,
    model_id: str,
    run_id: str,
    domains: Sequence[str],
    catalog: Any,
) -> tuple[SeamCheck, ...]:
    """Audit every mixed-source ``(var, domain)`` recorded in the run manifest.

    What is actually checked, per design §8 ("the canary explicitly checks value
    continuity across any recorded failover seam"):

    * **Accumulation** variables — the run-cumulative series must not step
      backwards across the seam. A cell whose total *drops* means two sources
      were mixed inside one series, which is exactly what
      ``enforce_failover_deadline``'s purge-then-hand-over exists to prevent;
      this is the on-real-data proof of that invariant. Tolerance is one
      packing quantum, because the two sides are separately quantised.
    * **Instantaneous** variables — recorded, never failed. A 9 km→0.25° seam
      is legitimate there and is what the §8 resolution label explains to the
      user; flagging it would train operators to ignore the metric.
    """
    from ..scheduler import _manifest_path  # noqa: PLC0415

    accumulation_vars = {
        str(variable.cartosky_var)
        for variable in catalog.accumulation_variables()
        if variable.cartosky_var
    }

    seams: list[SeamCheck] = []
    for domain in domains:
        manifest = _read_json(_manifest_path(data_root, model_id, run_id, region=domain))
        variables = (manifest or {}).get("variables")
        if not isinstance(variables, dict):
            continue
        for var_id, entry in sorted(variables.items()):
            if not isinstance(entry, dict):
                continue
            seam = _seam_for_variable(
                data_root=data_root,
                model_id=model_id,
                run_id=run_id,
                var_id=str(var_id),
                domain=str(domain),
                entry=entry,
                is_accumulation=str(var_id) in accumulation_vars,
            )
            if seam is not None:
                seams.append(seam)
    return tuple(seams)


def _seam_for_variable(
    *,
    data_root: Path,
    model_id: str,
    run_id: str,
    var_id: str,
    domain: str,
    entry: Mapping[str, Any],
    is_accumulation: bool,
) -> SeamCheck | None:
    ranges = entry.get("source_ranges")
    if not isinstance(ranges, list) or not ranges:
        return None  # delayed-only variable: no provenance, no seam
    published = sorted(
        int(frame["fh"])
        for frame in entry.get("frames") or ()
        if isinstance(frame, dict) and "fh" in frame
    )
    if not published:
        return None

    covered = {
        fh
        for source_range in ranges
        if isinstance(source_range, dict)
        for fh in range(
            int(source_range.get("from_fh", 0)), int(source_range.get("to_fh", -1)) + 1
        )
        if fh in set(published)
    }
    uncovered = [fh for fh in published if fh not in covered]

    if len(ranges) > 1:
        # Two recorded sources: the seam is the first fh of the second range.
        # Only the first transition is audited — a run with three recorded
        # sources has already failed over twice, which the stall and blocked
        # metrics surface on their own; the value here is catching the *first*
        # place the series could have been spliced.
        first, second = ranges[0], ranges[1]
        seam_fh = int(second.get("from_fh", published[-1]))
        from_source = str(first.get("source_id") or "")
        to_source = str(second.get("source_id") or "")
    elif uncovered and uncovered[-1] > max(covered, default=-1):
        # The common failover shape: fast frames carry provenance, the delayed
        # rebuild that follows carries none.
        seam_fh = int(uncovered[0])
        from_source = str(ranges[0].get("source_id") or "")
        to_source = "delayed"
    else:
        return None  # homogeneous

    if not is_accumulation:
        return SeamCheck(
            var_id=var_id,
            domain=domain,
            seam_fh=seam_fh,
            from_source=from_source,
            to_source=to_source,
            continuity_checked=False,
            note="instantaneous variable — a resolution seam is expected and labelled",
        )

    previous_fh = max((fh for fh in published if fh < seam_fh), default=None)
    if previous_fh is None:
        return SeamCheck(
            var_id=var_id,
            domain=domain,
            seam_fh=seam_fh,
            from_source=from_source,
            to_source=to_source,
            continuity_checked=False,
            note="no published frame before the seam to compare against",
        )

    before = _read_published_frame(
        data_root=data_root,
        model_id=model_id,
        run_id=run_id,
        var_id=var_id,
        domain=domain,
        fh=previous_fh,
    )
    after = _read_published_frame(
        data_root=data_root,
        model_id=model_id,
        run_id=run_id,
        var_id=var_id,
        domain=domain,
        fh=seam_fh,
    )
    if before is None or after is None or before.shape != after.shape:
        return SeamCheck(
            var_id=var_id,
            domain=domain,
            seam_fh=seam_fh,
            from_source=from_source,
            to_source=to_source,
            continuity_checked=False,
            note="seam frames unreadable or mismatched — continuity not verified",
        )

    # Slack for quantisation: each frame rounds independently, so a genuinely
    # flat cumulative value can read one full code lower at the later fh. 1.5
    # codes keeps that from alerting while still catching the real thing (a
    # source switch moves whole millimetres, not hundredths of an inch).
    tolerance = max(_packing_scale(model_id, var_id), 0.0) * 1.5
    delta = np.asarray(after, dtype=np.float64) - np.asarray(before, dtype=np.float64)
    finite = np.isfinite(delta)
    negative = finite & (delta < -tolerance)
    total = int(np.count_nonzero(finite))
    fraction = float(np.count_nonzero(negative) / total) if total else 0.0
    worst = float(-delta[negative].min()) if np.any(negative) else 0.0
    return SeamCheck(
        var_id=var_id,
        domain=domain,
        seam_fh=seam_fh,
        from_source=from_source,
        to_source=to_source,
        continuity_checked=True,
        negative_jump_fraction=fraction,
        max_negative_jump=worst,
        ok=not np.any(negative),
        note=(
            ""
            if not np.any(negative)
            else "run-cumulative total decreases across the seam — sources were mixed"
        ),
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    import json  # noqa: PLC0415

    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# Frame selection and the run-level entry points
# ---------------------------------------------------------------------------


def select_canary_fh(
    *, plugin: Any, var_ids: Sequence[str], cycle_hour: int, ready_through_fh: int
) -> int | None:
    """A mid-run fh on the production ladder of *every* canaried variable.

    Chosen as the candidate nearest :data:`PREFERRED_CANARY_FH` that is inside
    the fast frontier — near enough to FH096 that both products are on their 3 h
    ladder, and never past what was actually published.
    """
    from .subloop import _scheduled_fhs  # noqa: PLC0415

    common: set[int] | None = None
    for var_id in var_ids:
        fhs = set(_scheduled_fhs(plugin, var_id, int(cycle_hour)))
        common = fhs if common is None else (common & fhs)
    candidates = sorted(
        fh for fh in (common or set()) if 0 < fh <= int(ready_through_fh)
    )
    if not candidates:
        return None
    return min(candidates, key=lambda fh: (abs(fh - PREFERRED_CANARY_FH), -fh))


def run_canary_for_run(
    *,
    plugin: Any,
    model_id: str,
    data_root: Path,
    run_dt: datetime,
    run_id: str,
    state: FastpathRunState,
    var_ids: Sequence[str] = DEFAULT_CANARY_VARS,
    source: Any | None = None,
    base_url: str | None = None,
    fh: int | None = None,
) -> CanaryRun:
    """Compare 2–3 fast-owned variables on one frame and audit any seams.

    Never raises: a canary that can take the scheduler down is worse than no
    canary. Failures become :data:`STATUS_ERROR` with the reason logged.
    """
    from .subloop import _catalog_for  # noqa: PLC0415

    completed_at = _utc_now()
    try:
        catalog = _catalog_for(model_id)
        reference_catalog = reference_catalog_for(catalog)
    except Exception as exc:
        logger.warning("fastpath_canary: no reference product for %s (%s)", model_id, exc)
        return CanaryRun(
            model_id=str(model_id),
            run_id=str(run_id),
            status=STATUS_SKIPPED,
            completed_at=completed_at,
            skip_reason=f"no_reference_product: {exc}",
        )

    om_source = source if source is not None else _default_source()

    # Only pairs the fast source still owns are comparable: a revoked pair's
    # frames were rebuilt by (or handed to) the delayed path, so comparing them
    # against the delayed reference would be comparing that source to itself.
    configured = fast_owned_pairs(model_id)
    live = sorted(
        (var_id, domain)
        for var_id, domain in configured
        if var_id in set(var_ids) and not state.is_revoked(var_id, domain)
    )
    if not live:
        return CanaryRun(
            model_id=str(model_id),
            run_id=str(run_id),
            status=STATUS_SKIPPED,
            completed_at=completed_at,
            skip_reason="no_live_fast_owned_pairs",
        )

    domains = sorted({domain for _var, domain in live})
    results: list[CanaryResult] = []
    retryable_skips: list[str] = []
    try:
        for domain in domains:
            vars_here = [var_id for var_id, dom in live if dom == domain]
            frontier = min(
                (state.pair(var_id, domain, create=False).ready_through_fh or 0)
                for var_id in vars_here
            )
            target_fh = (
                int(fh)
                if fh is not None
                else select_canary_fh(
                    plugin=plugin,
                    var_ids=vars_here,
                    cycle_hour=int(run_dt.hour),
                    ready_through_fh=frontier,
                )
            )
            if target_fh is None:
                logger.info(
                    "fastpath_canary: no comparable fh for %s/%s (frontier FH%03d)",
                    model_id,
                    domain,
                    frontier,
                )
                retryable_skips.append(f"{domain}: no_comparable_fh")
                continue
            for var_id in vars_here:
                outcome = compare_frame(
                    data_root=data_root,
                    model_id=model_id,
                    run_dt=run_dt,
                    run_id=run_id,
                    var_id=var_id,
                    domain=domain,
                    fh=target_fh,
                    catalog=catalog,
                    reference_catalog=reference_catalog,
                    om_source=om_source,
                    base_url=base_url,
                )
                if outcome.result is not None:
                    results.append(outcome.result)
                elif outcome.retryable:
                    retryable_skips.append(f"{var_id}|{domain}: {outcome.skip_reason}")
        seams = check_failover_seams(
            data_root=data_root,
            model_id=model_id,
            run_id=run_id,
            domains=domains,
            catalog=catalog,
        )
    except Exception as exc:
        logger.exception("fastpath_canary: comparison failed for %s %s", model_id, run_id)
        return CanaryRun(
            model_id=str(model_id),
            run_id=str(run_id),
            status=STATUS_ERROR,
            completed_at=completed_at,
            results=tuple(results),
            skip_reason=f"error: {exc}",
        )

    # Failure is decided FIRST, and from both evidence sources. A seam whose
    # run-cumulative series steps backwards is a correctness failure in its own
    # right — it does not become less true because no frame comparison happened
    # to be available this run, and an earlier version that checked
    # ``if not results`` first downgraded exactly that case to a silent skip.
    failed_results = [result for result in results if result.failed]
    failed_seams = [seam for seam in seams if not seam.ok]
    if failed_results or failed_seams:
        status = STATUS_FAILED
        skip_reason = ""
    elif results:
        status = STATUS_OK
        skip_reason = ""
    elif retryable_skips:
        # Nothing was comparable, but the reasons might resolve on their own —
        # STATUS_ERROR so the run's canary is not latched away by one hiccup.
        status = STATUS_ERROR
        skip_reason = "retryable: " + "; ".join(sorted(retryable_skips))
    else:
        status = STATUS_SKIPPED
        skip_reason = "no_comparable_frames"

    canary = CanaryRun(
        model_id=str(model_id),
        run_id=str(run_id),
        status=status,
        completed_at=completed_at,
        results=tuple(results),
        seams=tuple(seams),
        skip_reason=skip_reason,
    )
    _log(canary)
    return canary


def _log(canary: CanaryRun) -> None:
    for result in canary.results:
        line = (
            "model=%s run=%s var=%s domain=%s fh=%03d n=%d bias=%+.4f mae=%.4f "
            "synoptic_mae=%.4f corr=%.5f unit=%s reference=%s"
        )
        args = (
            canary.model_id,
            canary.run_id,
            result.var_id,
            result.domain,
            result.fh,
            result.n,
            result.bias,
            result.mae,
            result.synoptic_mae,
            result.corr,
            result.unit,
            result.reference_source,
        )
        if result.failed:
            logger.error("fastpath_canary_failed " + line + " failures=%s", *args, "|".join(result.failures))
        else:
            logger.info("fastpath_canary " + line, *args)
    for seam in canary.seams:
        if seam.ok:
            logger.info(
                "fastpath_canary_seam model=%s run=%s var=%s domain=%s seam_fh=%03d "
                "%s->%s checked=%s",
                canary.model_id,
                canary.run_id,
                seam.var_id,
                seam.domain,
                seam.seam_fh,
                seam.from_source,
                seam.to_source,
                seam.continuity_checked,
            )
        else:
            logger.error(
                "fastpath_canary_failed model=%s run=%s var=%s domain=%s seam_fh=%03d "
                "negative_jump_fraction=%.4f max_negative_jump=%.4f note=%s",
                canary.model_id,
                canary.run_id,
                seam.var_id,
                seam.domain,
                seam.seam_fh,
                seam.negative_jump_fraction,
                seam.max_negative_jump,
                seam.note,
            )


def _default_source() -> Any:
    from ..sources.openmeteo import source as om_source  # noqa: PLC0415

    return om_source


# ---------------------------------------------------------------------------
# The scheduler trigger
# ---------------------------------------------------------------------------


def both_sources_complete(
    *,
    plugin: Any,
    model_id: str,
    data_root: Path,
    run_dt: datetime,
    run_id: str,
    state: FastpathRunState,
    var_ids: Sequence[str] = DEFAULT_CANARY_VARS,
) -> str:
    """``""`` when the run is ready to canary, else why it is not.

    "Both complete" is deliberately answered from artifacts rather than from a
    completion flag anybody sets:

    * the **fast** side is complete when every live fast-owned pair among
      ``var_ids`` has reached its scheduled horizon in the failover state's
      ``published_fhs`` frontier (which only advances on a successful promote);
    * the **delayed** side is complete when the run manifest shows at least one
      *delayed-built* variable — one with no ``source_ranges``, i.e. nothing
      fast ever wrote — at its own ``ready_through_fh`` horizon. That is the
      cheapest honest signal that the delayed build for this cycle has finished,
      and it needs no new bookkeeping.
    """
    from .subloop import _scheduled_fhs  # noqa: PLC0415

    configured = fast_owned_pairs(model_id)
    live = [
        (var_id, domain)
        for var_id, domain in sorted(configured)
        if var_id in set(var_ids) and not state.is_revoked(var_id, domain)
    ]
    if not live:
        return "no_live_fast_owned_pairs"

    cycle_hour = int(run_dt.hour)
    for var_id, domain in live:
        expected = _scheduled_fhs(plugin, var_id, cycle_hour)
        if not expected:
            continue
        frontier = state.pair(var_id, domain, create=False).ready_through_fh
        if frontier is None or int(frontier) < int(expected[-1]):
            return f"fast_incomplete: {var_id}|{domain}"

    domains = sorted({domain for _var, domain in live})
    if not any(
        _delayed_build_finished(
            data_root=data_root, model_id=model_id, run_id=run_id, domain=domain
        )
        for domain in domains
    ):
        return "delayed_incomplete"
    return ""


def _delayed_build_finished(
    *, data_root: Path, model_id: str, run_id: str, domain: str
) -> bool:
    from ..scheduler import _manifest_path  # noqa: PLC0415

    manifest = _read_json(_manifest_path(data_root, model_id, run_id, region=domain))
    variables = (manifest or {}).get("variables")
    if not isinstance(variables, dict):
        return False
    for entry in variables.values():
        if not isinstance(entry, dict) or entry.get("source_ranges"):
            continue  # fast-written, so it says nothing about the delayed build
        ready = entry.get("ready_through_fh")
        expected_max = entry.get("expected_max_fh")
        if ready is None or expected_max is None:
            continue
        try:
            if int(ready) >= int(expected_max):
                return True
        except (TypeError, ValueError):
            continue
    return False


def maybe_run_canary(
    *,
    plugin: Any,
    model_id: str,
    data_root: Path,
    run_dt: datetime,
    var_ids: Sequence[str] = DEFAULT_CANARY_VARS,
    source: Any | None = None,
    base_url: str | None = None,
) -> CanaryRun | None:
    """Run the canary at most once for ``run_dt``, when both sources are done.

    The scheduler calls this every poll with the run the *delayed* probe just
    resolved — which is the right candidate by construction: the fast source
    finished that cycle ~2 h earlier, so "the delayed path has it" is exactly
    the moment both sides exist (design §6, "once per run, after both sources
    are complete").

    Returns ``None`` when nothing ran (flag off, already done, not yet
    complete). Never raises.
    """
    if not fastpath_enabled(model_id):
        return None
    try:
        from ..scheduler import _run_id_from_dt  # noqa: PLC0415

        run_id = _run_id_from_dt(run_dt)
        state = FastpathRunState.load_or_create(
            data_root=data_root, model=model_id, run_id=run_id
        )
        if state.canary_done:
            return None
        blocker = both_sources_complete(
            plugin=plugin,
            model_id=model_id,
            data_root=data_root,
            run_dt=run_dt,
            run_id=run_id,
            state=state,
            var_ids=var_ids,
        )
        if blocker == "no_live_fast_owned_pairs":
            # A fully-revoked (or never-claimed) run has nothing fast to check.
            # Latch it so the check does not repeat every poll for a run whose
            # answer can no longer change.
            canary = CanaryRun(
                model_id=str(model_id),
                run_id=run_id,
                status=STATUS_SKIPPED,
                completed_at=_utc_now(),
                skip_reason=blocker,
            )
            state.mark_canary(canary.to_json())
            state.save()
            logger.info(
                "fastpath_canary: skipped %s %s — %s", model_id, run_id, blocker
            )
            return canary
        if blocker:
            logger.debug(
                "fastpath_canary: %s %s not ready (%s)", model_id, run_id, blocker
            )
            return None

        canary = run_canary_for_run(
            plugin=plugin,
            model_id=model_id,
            data_root=data_root,
            run_dt=run_dt,
            run_id=run_id,
            state=state,
            var_ids=var_ids,
            source=source,
            base_url=base_url,
        )
        # An ERROR is transient by nature (a bucket blip), so it does not latch
        # — the next poll retries. Everything else is a settled answer.
        if canary.status != STATUS_ERROR:
            state.mark_canary(canary.to_json())
            state.save()
        return canary
    except Exception:
        logger.exception(
            "fastpath_canary: canary failed for model=%s; the fast path is unaffected",
            model_id,
        )
        return None


def canary_results_from_state(state: FastpathRunState) -> Iterable[Mapping[str, Any]]:
    """Recorded per-variable results for one run, for the metrics sweep."""
    results = state.canary.get("results")
    return results if isinstance(results, list) else ()
