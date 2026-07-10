"""Decoupled ensemble-member publish pass (member pipeline plan Phase 3).

Publishes slim member grid binaries (``{var}__m{NN}`` / ``{var}__control``)
into the STAGING run root, strictly after the run's mean catchup completes.
The scheduler owns scheduling and promotion; this module owns the per-frame
pipeline and the pass loop.

Members are processed **sequentially per member, ascending fh** (Phase 2
design Section 12 / D6): the derived member variables (``precip_total``,
``snowfall_total``) are cumulative, so a member's fh-N frame depends on that
member's fields at every prior step. Per (member, fh) the pass downloads ONE
bundled subset (TMP + APCP + CSNOW as enabled — D5's member-bundled fetch,
keeping the request count at members × fhs regardless of variable count),
then:

  * ``tmp2m``: convert -> warp -> gate -> slim write (the spike-validated
    direct-field shape);
  * ``precip_total`` / ``snowfall_total``: warp the step fields and fold them
    into per-member running cumulative state (target-grid space — bilinear
    warping is linear, so accumulating warped steps equals warping the
    accumulated sum up to nodata-edge effects), then gate + slim-write the
    cumulative snapshot at each scheduled fh.

The cumulative step math (``precip_step_contribution`` /
``snowfall_step_contribution`` / ``merge_cumulative_step``) mirrors the
production derive strategies exactly and is pinned by a parity test against
them; its parameters (SLR, min step LWE, csnow sample mode) are read from the
SAME var-spec hints the mean path reads, so a hint change flows to both.

Pass semantics (design R4):
  * resumable/idempotent — completed frames are skipped; cumulative state is
    rebased from the decode of the member's own last complete cumulative
    frame (mirroring production, which reloads prior cumulative bases from
    staged artifacts; packing quantization is a one-time base offset);
  * preemptible — ``should_stop`` is consulted between frames and during
    backoff waits, so a newer run's mean build always wins;
  * fetch failures are recorded and retried on the next pass, never fatal.
    A bundle failure aborts the member's remaining fhs for this pass (the
    cumulative chain cannot continue past a missing step).

GEFS member identity -> Herbie kwarg (spike-confirmed): ``member=1..30`` ->
gepNN, ``member=0`` -> gec00.

EPS (member pipeline Phase 4, design Section 13 / D7) runs through the SAME
pass in ``pf_subset`` mode: ECMWF publishes all 50 perturbed members in one
file per fh with no control, and both EPS member variables (``tmp2m``,
``precip_total`` — ECMWF ``tp`` is natively run-cumulative) are direct
fields. The unit of work is ``(var, fh)``, not ``(member, fh)``: the pass
resolves the SAME ``*.cartosky_pf.grib2`` subset the mean build downloaded
(reused from the Herbie cache at its deterministic path, or re-downloaded
via the same production range-fetch primitive on a miss), derives the
band->member mapping from the .index (bands are written in ascending byte
order, so pf inventory rows sorted by start byte give each band's member
``number`` — validated for count and uniqueness per subset), then per band:
read -> convert -> warp -> gate -> slim write. No derive chain, no bundle
patterns, no scheduler changes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import rasterio
from rasterio.transform import Affine

from ...models.base import ensemble_member_descriptors, ensemble_member_ids
from ..colormaps import get_color_map_spec
from ..grid import (
    _decode_values,
    _packing_config,
    expected_grid_frame_size_bytes,
    grid_dtype,
    grid_frame_meta_path_for_run_root,
    grid_frame_path_for_run_root,
    write_slim_grid_frame_for_run_root,
)
from .cog_writer import warp_to_target_grid
from .fetch import (
    _aggregation_subset_path,
    _download_subset_with_inventory_rows,
    _eps_subset_fallback_path,
    _eps_subset_fallback_token,
    _inventory_row_byte_range,
    _inventory_search,
    _priority_candidates,
    _priority_normalized,
    _quiet_herbie_kwargs,
    _read_rasterio_band,
    _subset_download_lock,
    _subset_file_status,
    convert_units,
)
from .pipeline import (
    _get_search_patterns,
    _resolve_model_var_capability,
    _resolve_model_var_spec,
    _warp_resampling_for_variable,
    check_pre_encode_value_sanity,
)

logger = logging.getLogger(__name__)

# Script-level backoff between bundle-fetch attempts, after the per-priority
# sweep has failed (spike-validated schedule; zero retries were needed across
# 2,009 requests in the spike).
FETCH_BACKOFF_SCHEDULE: tuple[float, ...] = (5.0, 15.0, 45.0)

DEFAULT_MEMBER_FETCH_WORKERS = 2
MAX_MEMBER_FETCH_WORKERS = 4
ENV_MEMBER_FETCH_WORKERS = "CARTOSKY_MEMBER_FETCH_WORKERS"

STATUS_WRITTEN = "written"
STATUS_RESUMED = "resumed"
STATUS_GATE_FAILED = "gate_failed"
STATUS_FETCH_FAILED = "fetch_failed"
STATUS_ERROR = "error"
STATUS_PREEMPTED = "preempted"

# Build-plan modes (design §13/D7). member_files: one upstream file per
# member (GEFS gepNN/gec00) — per-member sequential fh loop, bundled fetch.
# pf_subset: all members share one upstream file per fh (ECMWF enfo) — the
# unit of work is (var, fh) and members are bands of the mean's pf subset.
MODE_MEMBER_FILES = "member_files"
MODE_PF_SUBSET = "pf_subset"

# Member-file component patterns. These intentionally differ from BOTH the
# mean specs (whose patterns carry ":ens mean:", absent from gepNN files) and
# the GFS deterministic apcp_step spec (whose pattern is ":$"-anchored, which
# member idx lines — carrying an ENS suffix — would not match).
MEMBER_APCP_PATTERN = r":APCP:surface:[0-9]+-[0-9]+ hour acc"
MEMBER_CSNOW_PATTERN = r":CSNOW:surface:"

# GRIB band element -> bundle field key (rasterio GRIB driver band tags).
_BUNDLE_ELEMENT_TO_FIELD = {"TMP": "tmp2m", "APCP": "apcp", "CSNOW": "csnow"}


def _normalize_grib_element(element: str) -> str:
    """GDAL's GRIB driver suffixes accumulation elements with their window
    (APCP over 6 h -> ``APCP06``, prod-observed 2026-07-06); strip trailing
    digits to the base element name."""
    return re.sub(r"\d+$", "", str(element or "").strip().upper())

_KGM2_TO_INCHES = 0.03937007874015748  # 1 kg/m^2 == 1 mm LWE


def member_fetch_workers() -> int:
    raw = str(os.getenv(ENV_MEMBER_FETCH_WORKERS, "") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MEMBER_FETCH_WORKERS
    except ValueError:
        value = DEFAULT_MEMBER_FETCH_WORKERS
    return max(1, min(MAX_MEMBER_FETCH_WORKERS, value))


def member_var_id(base_var: str, member: str) -> str:
    """``tmp2m`` + ``m07`` -> ``tmp2m__m07``; ``control`` -> ``tmp2m__control``."""
    return f"{str(base_var).strip().lower()}__{str(member).strip().lower()}"


def member_herbie_kwarg(member: str) -> int:
    """Herbie ``member`` kwarg: perturbation number, 0 for control (gec00)."""
    normalized = str(member).strip().lower()
    if normalized == "control":
        return 0
    return int(normalized.lstrip("mp"))


def member_frame_is_complete(run_root: Path, model: str, var_id: str, fh: int) -> bool:
    """Resume check: slim ``.bin`` exists with the size its meta promises."""
    packing = _packing_config(model, var_id)
    if packing is None:
        return False
    packing_dtype = grid_dtype(str(packing.get("dtype") or ""))
    frame_path = grid_frame_path_for_run_root(run_root, var_id, fh, dtype=packing_dtype)
    meta_path = grid_frame_meta_path_for_run_root(run_root, var_id, fh)
    if not frame_path.is_file() or not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text())
        expected = expected_grid_frame_size_bytes(
            width=int(meta["width"]), height=int(meta["height"]), dtype=packing_dtype,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return expected > 0 and frame_path.stat().st_size == expected


# ── Pure cumulative step math (parity-pinned to the production derive) ──────
def precip_step_contribution(step_data: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mirror of the production precip cumulative ``_process_step``:
    contribution = max(step, 0) where finite else 0; valid = finite(step)."""
    step_clean = np.where(
        np.isfinite(step_data), np.maximum(step_data, 0.0), 0.0,
    ).astype(np.float32)
    return step_clean, np.isfinite(step_data)


def snowfall_step_contribution(
    step_data: np.ndarray,
    csnow_samples: list[np.ndarray],
    *,
    min_step_lwe: float,
    snow_mask_threshold: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Mirror of the production snowfall cumulative ``_process_step``.

    APCP: valid where finite & >= 0, else 0; zero below ``min_step_lwe``.
    CSNOW: each sample valid where finite & in [0, 1]; the interval mask is
    the MEAN of the valid samples (clipped [0, 1]); optional hard threshold.
    Contribution (kg/m^2 LWE) = clean APCP × mask; valid = apcp_valid & any
    valid csnow sample.
    """
    apcp_valid = np.isfinite(step_data) & (step_data >= 0.0)
    step_apcp_clean = np.where(apcp_valid, step_data, 0.0).astype(np.float32, copy=False)
    if min_step_lwe > 0.0:
        step_apcp_clean = np.where(
            step_apcp_clean >= min_step_lwe, step_apcp_clean, 0.0,
        ).astype(np.float32, copy=False)

    sample_masks: list[np.ndarray] = []
    for snow_mask in csnow_samples:
        snow_valid = np.isfinite(snow_mask) & (snow_mask >= 0.0) & (snow_mask <= 1.0)
        sample_masks.append(
            np.where(snow_valid, snow_mask, np.nan).astype(np.float32, copy=False)
        )

    if sample_masks:
        sample_stack = np.stack(sample_masks, axis=0).astype(np.float32, copy=False)
        sample_valid_counts = np.sum(np.isfinite(sample_stack), axis=0).astype(np.int32, copy=False)
        sample_sum = np.nansum(sample_stack, axis=0).astype(np.float32, copy=False)
        interval_mask = np.zeros(step_apcp_clean.shape, dtype=np.float32)
        np.divide(
            sample_sum,
            sample_valid_counts.astype(np.float32, copy=False),
            out=interval_mask,
            where=sample_valid_counts > 0,
        )
        interval_mask = np.clip(interval_mask, 0.0, 1.0).astype(np.float32, copy=False)
        if snow_mask_threshold is not None:
            interval_mask = np.where(
                interval_mask >= np.float32(snow_mask_threshold),
                np.float32(1.0),
                np.float32(0.0),
            ).astype(np.float32, copy=False)
        csnow_valid = sample_valid_counts > 0
    else:
        interval_mask = np.zeros(step_apcp_clean.shape, dtype=np.float32)
        csnow_valid = np.zeros(step_apcp_clean.shape, dtype=bool)

    contribution = (step_apcp_clean * interval_mask).astype(np.float32, copy=False)
    return contribution, apcp_valid & csnow_valid


def merge_cumulative_step(
    cumulative: np.ndarray | None,
    valid_mask: np.ndarray | None,
    contribution: np.ndarray,
    step_valid: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Mirror of the production ``_cumulative_apcp_loop`` accumulation:
    sum contributions, OR the valid masks."""
    if cumulative is None or valid_mask is None:
        return contribution.astype(np.float32, copy=False), np.asarray(step_valid, dtype=bool)
    return (
        (cumulative + contribution).astype(np.float32, copy=False),
        np.logical_or(valid_mask, step_valid),
    )


def cumulative_field(cumulative: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    """NaN at never-valid pixels — the production loop's final step."""
    return np.where(valid_mask, cumulative, np.nan).astype(np.float32)


# ── Var contexts and the per-model build plan ───────────────────────────────
@dataclass
class _MemberVarContext:
    """Resolved per-variable build inputs, mirroring build_frame's resolution."""

    base_var: str
    var_spec: Any
    capability: Any
    colormap_spec: dict[str, Any]
    resampling: str
    search_patterns: list[str]
    product: str
    derived: bool


def _resolve_member_var_context(plugin: Any, model_id: str, base_var: str) -> _MemberVarContext:
    var_spec = _resolve_model_var_spec(model_id, base_var, plugin)
    capability = _resolve_model_var_capability(model_id, base_var, plugin)
    color_map_id = str(getattr(capability, "color_map_id", "") or "").strip()
    colormap_spec = get_color_map_spec(color_map_id)
    kind = str(getattr(capability, "kind", None) or getattr(var_spec, "kind", "") or "continuous")
    resampling = _warp_resampling_for_variable(model_id=model_id, var_key=base_var, kind=kind)
    selectors = getattr(var_spec, "selectors", None)
    hints = getattr(selectors, "hints", {}) if selectors is not None else {}
    default_product = str(getattr(plugin, "product", "") or "sfc")
    product = str((hints or {}).get("product") or default_product).strip() or default_product
    derived = bool(getattr(var_spec, "derived", False))
    search_patterns: list[str] = []
    if not derived:
        search_patterns = _get_search_patterns(
            var_spec, model_plugin=plugin, var_key=base_var, fh=None, product=product,
        )
    return _MemberVarContext(
        base_var=base_var,
        var_spec=var_spec,
        capability=capability,
        colormap_spec=colormap_spec,
        resampling=resampling,
        search_patterns=search_patterns,
        product=product,
        derived=derived,
    )


@dataclass
class _CumulativeParams:
    """Snowfall/precip step parameters, read from the SAME var-spec hints the
    mean derive path reads (Section 12: a hint change flows to both)."""

    step_hours: int = 6
    slr: float = 10.0
    min_step_lwe: float = 0.01
    snow_mask_threshold: float | None = None
    skip_zero_hour_sample: bool = True


def _parse_cumulative_params(snow_ctx: _MemberVarContext | None, precip_ctx: _MemberVarContext | None) -> _CumulativeParams:
    params = _CumulativeParams()
    hint_source = snow_ctx or precip_ctx
    if hint_source is None:
        return params
    hints = getattr(getattr(hint_source.var_spec, "selectors", None), "hints", {}) or {}
    try:
        params.step_hours = max(1, int(str(hints.get("step_hours", "6"))))
    except (TypeError, ValueError):
        params.step_hours = 6
    if snow_ctx is not None:
        snow_hints = getattr(getattr(snow_ctx.var_spec, "selectors", None), "hints", {}) or {}
        try:
            slr = float(snow_hints.get("slr", "10"))
        except (TypeError, ValueError):
            slr = 10.0
        params.slr = slr if slr > 0.0 else 10.0
        try:
            params.min_step_lwe = max(0.0, float(snow_hints.get("min_step_lwe_kgm2", "0.01")))
        except (TypeError, ValueError):
            params.min_step_lwe = 0.01
        raw_threshold = snow_hints.get("snow_mask_threshold")
        if raw_threshold is not None:
            try:
                params.snow_mask_threshold = min(max(float(raw_threshold), 0.0), 1.0)
            except (TypeError, ValueError):
                params.snow_mask_threshold = 0.5
        params.skip_zero_hour_sample = str(
            snow_hints.get("skip_zero_hour_sample", "true")
        ).strip().lower() in {"1", "true", "yes", "on"}
        sample_mode = str(snow_hints.get("snow_interval_sample_mode", "step_endpoints")).strip().lower()
        if sample_mode not in {"step_endpoints", "endpoints"}:
            # The member loop implements the GEFS endpoint-sampling case only;
            # any other mode must be scoped deliberately, not silently approximated.
            raise ValueError(
                f"Unsupported member snowfall sample mode {sample_mode!r} "
                "(member pass implements step_endpoints only)"
            )
    return params


@dataclass
class _MemberBuildPlan:
    model_id: str
    region: str
    run_id: str
    run_date: datetime
    member_ids: list[str]
    contexts: dict[str, _MemberVarContext]  # base_var -> ctx, enabled vars only
    fhs_by_var: dict[str, list[int]]
    step_fhs: list[int]  # cumulative step sequence (empty when no derived vars)
    all_fhs: list[int]
    cumulative: _CumulativeParams
    plugin: Any = None
    mode: str = MODE_MEMBER_FILES

    @property
    def member_var_ids(self) -> list[str]:
        return [
            member_var_id(base_var, member)
            for base_var in self.contexts
            for member in self.member_ids
        ]

    @property
    def has_cumulative(self) -> bool:
        return bool(self.step_fhs)


def _cycle_hour_from_run_id(run_id: str) -> int:
    return int(str(run_id)[9:11])


def _run_date_from_run_id(run_id: str) -> datetime:
    return datetime.strptime(str(run_id), "%Y%m%d_%Hz")


def _detect_plan_mode(
    plugin: Any,
    contexts: dict[str, _MemberVarContext],
    run_date: datetime,
    fhs_by_var: dict[str, list[int]],
) -> str:
    """``pf_subset`` when the mean path fetches via ECMWF pf aggregation.

    Read from the SAME plugin request the mean build uses (the
    ``_cartosky_fetch_aggregation`` kwarg), so a plugin fetch-path change
    flows to the member pass automatically. Mixing pf and non-pf member vars
    in one plan has no build path and fails loudly.
    """
    aggregations: dict[str, str] = {}
    for base_var, ctx in contexts.items():
        if ctx.derived or not ctx.search_patterns:
            aggregations[base_var] = ""
            continue
        fhs = fhs_by_var.get(base_var) or [0]
        try:
            request = plugin.herbie_request(
                product=ctx.product,
                var_key=base_var,
                ensemble_view="mean",
                run_date=run_date,
                fh=fhs[0],
                search_pattern=ctx.search_patterns[0],
            )
            aggregations[base_var] = str(
                request.herbie_kwargs.get("_cartosky_fetch_aggregation") or ""
            ).strip().lower()
        except Exception:  # noqa: BLE001 — no aggregation signal -> member files
            aggregations[base_var] = ""
    pf_vars = sorted(var for var, agg in aggregations.items() if "pf_mean" in agg)
    if not pf_vars:
        return MODE_MEMBER_FILES
    non_pf = sorted(set(contexts) - set(pf_vars))
    if non_pf:
        raise ValueError(
            f"Member plan mixes pf-subset vars {pf_vars} with non-pf vars "
            f"{non_pf} — no build path for a mixed-mode plan (design §13)"
        )
    return MODE_PF_SUBSET


def _mean_var_id_for(base_var: str, capability: Any) -> str:
    """The runtime var id of this variable's published ensemble MEAN."""
    ensemble = getattr(capability, "ensemble", None)
    if isinstance(ensemble, dict):
        artifact = (ensemble.get("artifact_map") or {}).get("mean")
        if artifact:
            return str(artifact).strip().lower()
    return f"{str(base_var).strip().lower()}__mean"


def _mean_frame_available(
    data_root: Path, model_id: str, run_id: str, mean_var_id: str, fh: int,
) -> bool:
    """Does the run's MEAN frame exist (staging or published) for this fh?"""
    return any(
        member_frame_is_complete(
            data_root / tree / model_id / run_id, model_id, mean_var_id, fh,
        )
        for tree in ("staging", "published")
    )


def build_member_plan(
    plugin: Any,
    model_id: str,
    run_id: str,
    region: str,
    *,
    data_root: Path | None = None,
    mean_coverage_only: bool = False,
) -> _MemberBuildPlan | None:
    """Resolve the member build plan for one run.

    ``mean_coverage_only`` (backfill semantics — requires ``data_root``) caps
    each variable's fhs to the frames whose MEAN artifact actually exists:
    a superseded run whose mean catchup never completed (e.g. an upstream
    data defect blocked one variable) still gets members exactly where the
    plume can display them, without chasing frames the mean never built.
    The normal post-catchup pass leaves this off — there, coverage equals
    the schedule by construction.
    """
    descriptors = ensemble_member_descriptors(plugin)
    if not descriptors:
        return None

    rosters = {var: ensemble_member_ids(desc) for var, desc in descriptors.items()}
    roster_values = list(rosters.values())
    if any(r != roster_values[0] for r in roster_values[1:]):
        raise ValueError(
            f"Member descriptors disagree on the roster for {model_id}: "
            + ", ".join(f"{var}={len(r)}" for var, r in rosters.items())
        )
    if mean_coverage_only and data_root is None:
        raise ValueError("mean_coverage_only requires data_root")

    cycle_hour = _cycle_hour_from_run_id(run_id)
    run_date = _run_date_from_run_id(run_id)
    contexts: dict[str, _MemberVarContext] = {}
    fhs_by_var: dict[str, list[int]] = {}
    for base_var in descriptors:
        ctx = _resolve_member_var_context(plugin, model_id, base_var)
        contexts[base_var] = ctx
        fhs = sorted({int(fh) for fh in plugin.scheduled_fhs_for_var(base_var, cycle_hour)})
        if ctx.derived:
            # Cumulative vars have no fh-0 product (min_fh constraints); a
            # schedule that claims one would leave the pass forever-pending.
            fhs = [fh for fh in fhs if fh > 0]
        if mean_coverage_only and data_root is not None:
            mean_var_id = _mean_var_id_for(base_var, ctx.capability)
            fhs = [
                fh for fh in fhs
                if _mean_frame_available(data_root, model_id, run_id, mean_var_id, fh)
            ]
        fhs_by_var[base_var] = fhs

    mode = _detect_plan_mode(plugin, contexts, run_date, fhs_by_var)

    derived_vars = [var for var, ctx in contexts.items() if ctx.derived]
    snow_ctx = contexts.get("snowfall_total")
    precip_ctx = contexts.get("precip_total")
    unsupported = [v for v in derived_vars if v not in {"precip_total", "snowfall_total"}]
    if unsupported:
        raise ValueError(
            f"Member pass has no build path for derived vars: {unsupported} "
            "(only the cumulative precip/snow pair is implemented — design §12)"
        )
    cumulative = _parse_cumulative_params(snow_ctx, precip_ctx)

    step_fhs: list[int] = []
    derived_with_fhs = [v for v in derived_vars if fhs_by_var[v]]
    if derived_with_fhs:
        max_step_fh = max(max(fhs_by_var[v]) for v in derived_with_fhs)
        step_fhs = list(range(cumulative.step_hours, max_step_fh + 1, cumulative.step_hours))

    all_fhs = sorted({fh for fhs in fhs_by_var.values() for fh in fhs} | set(step_fhs))
    return _MemberBuildPlan(
        model_id=model_id,
        region=region,
        run_id=run_id,
        run_date=run_date,
        member_ids=roster_values[0],
        contexts=contexts,
        fhs_by_var=fhs_by_var,
        step_fhs=step_fhs,
        all_fhs=all_fhs,
        cumulative=cumulative,
        plugin=plugin,
        mode=mode,
    )


# ── Bundled fetch (D5): one subset per (member, fh) ─────────────────────────
class MemberFetchError(RuntimeError):
    pass


def _bundle_fields_for_fh(plan: _MemberBuildPlan, fh: int) -> dict[str, str]:
    """Field key -> search pattern for everything needed at this fh."""
    fields: dict[str, str] = {}
    tmp_ctx = plan.contexts.get("tmp2m")
    if tmp_ctx is not None and fh in plan.fhs_by_var["tmp2m"]:
        fields["tmp2m"] = tmp_ctx.search_patterns[0]
    if plan.has_cumulative and fh in plan.step_fhs:
        fields["apcp"] = MEMBER_APCP_PATTERN
        if "snowfall_total" in plan.contexts:
            fields["csnow"] = MEMBER_CSNOW_PATTERN
    return fields


def _map_bundle_bands(
    band_elements: list[str],
    expected_fields: dict[str, str],
) -> dict[str, int]:
    """GRIB band element names -> bundle field keys -> 1-based band index."""
    mapping: dict[str, int] = {}
    for band_index, element in enumerate(band_elements, start=1):
        key = _BUNDLE_ELEMENT_TO_FIELD.get(_normalize_grib_element(element))
        if key is None or key not in expected_fields:
            continue
        if key in mapping:
            raise MemberFetchError(f"Duplicate GRIB element for bundle field {key!r}")
        mapping[key] = band_index
    missing = sorted(set(expected_fields) - set(mapping))
    if missing:
        raise MemberFetchError(
            f"Bundle subset is missing fields {missing} (bands: {band_elements})"
        )
    return mapping


def _read_bundle_subset(
    subset_path: Path,
    expected_fields: dict[str, str],
) -> dict[str, tuple[np.ndarray, Any, Any]]:
    with rasterio.open(subset_path) as src:
        band_elements = [
            str(src.tags(band_index).get("GRIB_ELEMENT", "") or "")
            for band_index in range(1, int(src.count) + 1)
        ]
        mapping = _map_bundle_bands(band_elements, expected_fields)
        return {
            key: (_read_rasterio_band(src, band_index=band_index), src.crs, src.transform)
            for key, band_index in mapping.items()
        }


def _fetch_member_bundle(
    *,
    plan: _MemberBuildPlan,
    member: str,
    fh: int,
    fields: dict[str, str],
    should_stop: Callable[[], bool],
) -> dict[str, tuple[np.ndarray, Any, Any]]:
    """Download one subset with all requested fields for (member, fh).

    Reuses the production inventory-search + byte-range subset primitives
    (the EPS pf-subset pattern) with a combined regex pattern; band -> field
    mapping comes from the GRIB band element tags.
    """
    from herbie.core import Herbie  # lazy — matches production fetch style

    plugin_ctx = next(iter(plan.contexts.values()))
    request_ctx = plan.contexts.get("tmp2m") or plugin_ctx
    combined_pattern = "|".join(f"(?:{pattern})" for pattern in fields.values())
    last_exc: Exception | None = None

    delays = (0.0, *FETCH_BACKOFF_SCHEDULE)
    for attempt_idx, delay in enumerate(delays, start=1):
        if delay > 0:
            logger.info(
                "Member bundle backoff %.0fs before retry %d: %s member=%s fh%03d",
                delay, attempt_idx, plan.model_id, member, fh,
            )
            if _stop_aware_wait(delay, should_stop):
                raise MemberFetchError(f"preempted during backoff (member={member} fh{fh:03d})")
        if should_stop():
            raise MemberFetchError(f"preempted before fetch (member={member} fh{fh:03d})")
        try:
            request = _member_herbie_request(plan, request_ctx, member, fh)
            base_kwargs: dict[str, Any] = {
                "model": request.model,
                "product": request.product,
                "fxx": int(fh),
                **request.herbie_kwargs,
            }
            priorities = [
                _priority_normalized(item)
                for item in _priority_candidates(request.herbie_kwargs)
                if str(item).strip()
            ] or ["aws"]
            for priority in priorities:
                try:
                    run_kwargs = _quiet_herbie_kwargs(base_kwargs)
                    run_kwargs["priority"] = priority
                    herbie_date = (
                        plan.run_date.replace(tzinfo=None)
                        if plan.run_date.tzinfo else plan.run_date
                    )
                    H = Herbie(herbie_date, **run_kwargs)
                    inv_result = _inventory_search(
                        H,
                        search_pattern=combined_pattern,
                        priority=priority,
                        model_id=plan.model_id,
                        run_date=plan.run_date,
                        product=request.product,
                        fh=fh,
                    )
                    inventory = inv_result.inventory
                    if inv_result.reason != "ok" or inventory is None or len(inventory) == 0:
                        raise MemberFetchError(
                            f"bundle inventory unavailable ({inv_result.reason}) "
                            f"member={member} fh{fh:03d} priority={priority}"
                        )
                    subset_hint = Path(H.get_localFilePath(combined_pattern))
                    subset_path = _aggregation_subset_path(subset_hint, "cartosky_mbr")
                    with _subset_download_lock(subset_path):
                        cached_ok, _size = _subset_file_status(subset_path)
                        if not cached_ok:
                            downloaded = _download_subset_with_inventory_rows(
                                H,
                                inventory=inventory,
                                out_path=subset_path,
                                model_id=plan.model_id,
                                product=request.product,
                                run_date=plan.run_date,
                                fh=fh,
                                priority=priority,
                                bundle_fetch_cache=None,
                            )
                            if downloaded is None:
                                raise MemberFetchError(
                                    f"bundle subset download failed member={member} "
                                    f"fh{fh:03d} priority={priority}"
                                )
                        return _read_bundle_subset(subset_path, fields)
                except Exception as exc:  # noqa: BLE001 — recorded; next priority
                    last_exc = exc
                    logger.warning(
                        "Member bundle fetch failed: %s member=%s fh%03d priority=%s: %s",
                        plan.model_id, member, fh, priority,
                        f"{type(exc).__name__}: {exc}"[:300],
                    )
            raise MemberFetchError(
                f"bundle fetch failed on all priorities (member={member} fh{fh:03d})"
            )
        except MemberFetchError as exc:
            last_exc = exc
            if "preempted" in str(exc):
                raise
            continue
    raise MemberFetchError(
        f"bundle fetch failed after {len(delays)} attempt(s) (member={member} fh{fh:03d})"
    ) from last_exc


def _member_herbie_request(plan: _MemberBuildPlan, ctx: _MemberVarContext, member: str, fh: int) -> Any:
    """Plugin herbie request with the member kwarg overriding the mean's."""
    plugin = plan.plugin
    request = plugin.herbie_request(
        product=ctx.product,
        var_key=ctx.base_var,
        ensemble_view="mean",
        run_date=plan.run_date,
        fh=fh,
        search_pattern=ctx.search_patterns[0] if ctx.search_patterns else None,
    )
    herbie_kwargs = dict(request.herbie_kwargs)
    herbie_kwargs["member"] = member_herbie_kwarg(member)
    return type(request)(
        model=request.model, product=request.product, herbie_kwargs=herbie_kwargs,
    )


def _stop_aware_wait(delay_s: float, should_stop: Callable[[], bool]) -> bool:
    """Sleep up to ``delay_s``, polling ``should_stop``. True when stopped."""
    deadline = time.monotonic() + delay_s
    while time.monotonic() < deadline:
        if should_stop():
            return True
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    return should_stop()


# ── pf-subset mode (EPS — design §13/D7) ────────────────────────────────────
def _pf_band_member_numbers(pf_inventory: Any) -> list[int]:
    """Subset band index -> ECMWF member ``number``, derived from the .index.

    ``_download_subset_with_inventory_rows`` writes UNIQUE byte ranges sorted
    by (start, end) — regardless of the row order it is handed — so the
    subset's k-th band is the k-th range in that order, and each pf row's
    ``number`` labels its range's member. Any ambiguity (missing range or
    number, duplicate ranges or numbers) makes the mapping unsafe and fails
    the unit loudly rather than risk mislabeling members.
    """
    entries: list[tuple[int, int, int]] = []
    for _, row in pf_inventory.iterrows():
        byte_range = _inventory_row_byte_range(row)
        if byte_range is None:
            raise MemberFetchError("pf inventory row is missing its byte range")
        try:
            number = int(float(row.get("number")))
        except (TypeError, ValueError) as exc:
            raise MemberFetchError(f"pf inventory row has no member number ({exc})")
        entries.append((int(byte_range[0]), int(byte_range[1]), number))
    if not entries:
        raise MemberFetchError("pf inventory produced no byte ranges")
    ranges = [(start, end) for start, end, _ in entries]
    if len(set(ranges)) != len(ranges):
        raise MemberFetchError("pf inventory contains duplicate byte ranges")
    entries.sort(key=lambda item: (item[0], item[1]))
    numbers = [number for _, _, number in entries]
    if len(set(numbers)) != len(numbers):
        raise MemberFetchError(f"pf inventory contains duplicate member numbers: {numbers}")
    return numbers


def _resolve_pf_subset(
    *,
    plan: _MemberBuildPlan,
    ctx: _MemberVarContext,
    fh: int,
    should_stop: Callable[[], bool],
) -> tuple[Path, list[int]]:
    """Resolve the pf subset for (var, fh) plus its band->member mapping.

    Mirrors the mean's ``_fetch_ecmwf_pf_mean_variable`` path resolution
    exactly (same Herbie request, same pf-row filter, same deterministic
    ``*.cartosky_pf.grib2`` path with the same fallback token), so in the
    normal case this REUSES the subset the mean build just downloaded and
    only re-downloads the same byte ranges on a cache miss.
    """
    from herbie.core import Herbie  # lazy — matches production fetch style

    last_exc: Exception | None = None
    delays = (0.0, *FETCH_BACKOFF_SCHEDULE)
    for attempt_idx, delay in enumerate(delays, start=1):
        if delay > 0:
            logger.info(
                "pf subset backoff %.0fs before retry %d: %s %s fh%03d",
                delay, attempt_idx, plan.model_id, ctx.base_var, fh,
            )
            if _stop_aware_wait(delay, should_stop):
                raise MemberFetchError(f"preempted during backoff ({ctx.base_var} fh{fh:03d})")
        if should_stop():
            raise MemberFetchError(f"preempted before fetch ({ctx.base_var} fh{fh:03d})")
        # Patterns in declared order, priorities inner — the mean pipeline's
        # iteration order, so the first pattern that resolves here is the one
        # whose subset the mean build wrote.
        for pattern in ctx.search_patterns:
            request = plan.plugin.herbie_request(
                product=ctx.product,
                var_key=ctx.base_var,
                ensemble_view="mean",
                run_date=plan.run_date,
                fh=fh,
                search_pattern=pattern,
            )
            herbie_kwargs = dict(request.herbie_kwargs)
            herbie_kwargs.pop("_cartosky_fetch_aggregation", None)
            # The fetch layer keys caches/paths on the Herbie model id
            # ("ifs"), not the plugin id ("eps") — match it or the reuse
            # never hits.
            fetch_model_id = str(request.model)
            base_kwargs: dict[str, Any] = {
                "model": request.model,
                "product": request.product,
                "fxx": int(fh),
                **herbie_kwargs,
            }
            priorities = [
                _priority_normalized(item)
                for item in _priority_candidates(herbie_kwargs)
                if str(item).strip()
            ] or ["aws"]
            for priority in priorities:
                try:
                    run_kwargs = _quiet_herbie_kwargs(base_kwargs)
                    run_kwargs["priority"] = priority
                    herbie_date = (
                        plan.run_date.replace(tzinfo=None)
                        if plan.run_date.tzinfo else plan.run_date
                    )
                    H = Herbie(herbie_date, **run_kwargs)
                    inv_result = _inventory_search(
                        H,
                        search_pattern=pattern,
                        priority=priority,
                        model_id=fetch_model_id,
                        run_date=plan.run_date,
                        product=request.product,
                        fh=fh,
                    )
                    inventory = inv_result.inventory
                    if inv_result.reason != "ok" or inventory is None or len(inventory) == 0:
                        raise MemberFetchError(
                            f"pf inventory unavailable ({inv_result.reason}) "
                            f"{ctx.base_var} fh{fh:03d} priority={priority}"
                        )
                    if "type" in inventory.columns:
                        type_series = inventory["type"].astype(str).str.strip().str.lower()
                        pf_inventory = inventory.loc[type_series == "pf"]
                    else:
                        pf_inventory = inventory
                    if len(pf_inventory) == 0:
                        raise MemberFetchError(
                            f"pf inventory contained no perturbed members "
                            f"{ctx.base_var} fh{fh:03d} priority={priority}"
                        )
                    numbers = _pf_band_member_numbers(pf_inventory)

                    subset_hint: Path | None = None
                    try:
                        subset_hint = Path(H.get_localFilePath(pattern))
                    except Exception:  # noqa: BLE001 — mirror the mean's fallback
                        subset_hint = None
                    if subset_hint is None:
                        fallback_name = _eps_subset_fallback_token(
                            model_id=fetch_model_id,
                            product=request.product,
                            run_date=plan.run_date,
                            fh=fh,
                            search_pattern=pattern,
                            priority=priority,
                        )
                        subset_hint = _eps_subset_fallback_path(
                            prefix="eps_pf_mean", token=fallback_name,
                        )
                    subset_path = _aggregation_subset_path(subset_hint, "cartosky_pf")
                    with _subset_download_lock(subset_path):
                        cached_ok, _size = _subset_file_status(subset_path)
                        if not cached_ok:
                            downloaded = _download_subset_with_inventory_rows(
                                H,
                                inventory=pf_inventory,
                                out_path=subset_path,
                                model_id=fetch_model_id,
                                product=request.product,
                                run_date=plan.run_date,
                                fh=fh,
                                priority=priority,
                                bundle_fetch_cache=None,
                            )
                            if downloaded is None:
                                raise MemberFetchError(
                                    f"pf subset download failed {ctx.base_var} "
                                    f"fh{fh:03d} priority={priority}"
                                )
                    return subset_path, numbers
                except Exception as exc:  # noqa: BLE001 — recorded; next priority
                    if isinstance(exc, MemberFetchError) and "preempted" in str(exc):
                        raise
                    last_exc = exc
                    logger.warning(
                        "pf subset resolve failed: %s %s fh%03d priority=%s: %s",
                        plan.model_id, ctx.base_var, fh, priority,
                        f"{type(exc).__name__}: {exc}"[:300],
                    )
    raise MemberFetchError(
        f"pf subset resolve failed after {len(delays)} attempt(s) "
        f"({ctx.base_var} fh{fh:03d})"
    ) from last_exc


def _process_pf_unit(
    *,
    plan: _MemberBuildPlan,
    base_var: str,
    fh: int,
    staging_run_root: Path,
    should_stop: Callable[[], bool],
    record: Callable[[str, float | None], None],
) -> None:
    """Build every missing member frame of (var, fh) from one pf subset.

    Direct fields only (validated at plan time): per band, read -> convert ->
    warp -> gate -> slim write, holding one member's field at a time.
    """
    ctx = plan.contexts[base_var]
    missing = {
        member for member in plan.member_ids
        if not member_frame_is_complete(
            staging_run_root, plan.model_id, member_var_id(base_var, member), fh,
        )
    }
    for _ in range(len(plan.member_ids) - len(missing)):
        record(STATUS_RESUMED, None)
    if not missing:
        return

    def _mark_missing(status: str) -> None:
        for _ in range(len(missing)):
            record(status, None)

    if should_stop():
        _mark_missing(STATUS_PREEMPTED)
        return
    try:
        subset_path, numbers = _resolve_pf_subset(
            plan=plan, ctx=ctx, fh=fh, should_stop=should_stop,
        )
    except MemberFetchError as exc:
        if "preempted" in str(exc):
            _mark_missing(STATUS_PREEMPTED)
            return
        logger.warning(
            "pf unit fetch failed: %s %s fh%03d: %s", plan.model_id, base_var, fh, exc,
        )
        _mark_missing(STATUS_FETCH_FAILED)
        return
    except Exception:
        logger.exception("pf unit unexpected fetch error: %s %s fh%03d", plan.model_id, base_var, fh)
        _mark_missing(STATUS_ERROR)
        return

    expected = len(plan.member_ids)
    if sorted(numbers) != list(range(1, expected + 1)):
        logger.error(
            "pf roster mismatch — refusing to label bands: %s %s fh%03d "
            "numbers=%s expected 1..%d",
            plan.model_id, base_var, fh, numbers, expected,
        )
        _mark_missing(STATUS_ERROR)
        return

    try:
        with rasterio.open(subset_path) as src:
            if int(src.count) != expected:
                logger.error(
                    "pf subset band count mismatch: %s %s fh%03d bands=%d expected=%d (%s)",
                    plan.model_id, base_var, fh, int(src.count), expected, subset_path,
                )
                _mark_missing(STATUS_ERROR)
                return
            for band_index, number in enumerate(numbers, start=1):
                member = plan.member_ids[number - 1]
                if member not in missing:
                    continue
                if should_stop():
                    _mark_missing(STATUS_PREEMPTED)
                    return
                band_started = time.perf_counter()
                raw = _read_rasterio_band(src, band_index=band_index)
                converted = convert_units(
                    raw, var_key=base_var, model_id=plan.model_id,
                    var_capability=ctx.capability,
                )
                warped, dst_transform = warp_to_target_grid(
                    converted, src.crs, src.transform,
                    model=plan.model_id, region=plan.region,
                    resampling=ctx.resampling, src_nodata=None,
                    dst_nodata=float("nan"),
                )
                status = _gate_and_write(
                    plan=plan, ctx=ctx, var_id=member_var_id(base_var, member),
                    fh=fh, values=warped, dst_transform=dst_transform,
                    staging_run_root=staging_run_root,
                )
                record(
                    status,
                    time.perf_counter() - band_started if status == STATUS_WRITTEN else None,
                )
                missing.discard(member)
    except Exception:
        logger.exception("pf unit unexpected build error: %s %s fh%03d", plan.model_id, base_var, fh)
        _mark_missing(STATUS_ERROR)


# ── Per-member sequential processing ────────────────────────────────────────
@dataclass
class _CumulativeState:
    """Per-member running cumulative state in target-grid (warped) space."""

    precip_kgm2: np.ndarray | None = None
    precip_valid: np.ndarray | None = None
    snow_kgm2: np.ndarray | None = None
    snow_valid: np.ndarray | None = None
    prev_csnow: np.ndarray | None = None
    dst_transform: Any = None


def _decode_member_frame(
    run_root: Path, model: str, var_id: str, fh: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    packing = _packing_config(model, var_id)
    packing_dtype = grid_dtype(str(packing.get("dtype") or ""))
    frame_path = grid_frame_path_for_run_root(run_root, var_id, fh, dtype=packing_dtype)
    meta_path = grid_frame_meta_path_for_run_root(run_root, var_id, fh)
    meta = json.loads(meta_path.read_text())
    encoded = np.frombuffer(frame_path.read_bytes(), dtype="<u2").reshape(
        int(meta["height"]), int(meta["width"]))
    return _decode_values(encoded, model=model, var=var_id), meta


def _rebase_cumulative_state(
    *,
    plan: _MemberBuildPlan,
    member: str,
    staging_run_root: Path,
    base_fh: int,
    should_stop: Callable[[], bool],
) -> _CumulativeState:
    """Rebuild running state from the member's own frames at ``base_fh``.

    Mirrors production's prior-cumulative reload from staged artifacts; the
    packing quantization (0.01 in precip / 0.1 in snow) is a one-time base
    offset. The csnow endpoint at the base step is re-fetched (one bundle).
    """
    state = _CumulativeState()
    if "precip_total" in plan.contexts:
        decoded_in, meta = _decode_member_frame(
            staging_run_root, plan.model_id, member_var_id("precip_total", member), base_fh,
        )
        valid = np.isfinite(decoded_in)
        state.precip_kgm2 = np.where(valid, decoded_in / _KGM2_TO_INCHES, 0.0).astype(np.float32)
        state.precip_valid = valid
        state.dst_transform = Affine(*[float(v) for v in meta["transform"]])
    if "snowfall_total" in plan.contexts:
        decoded_in, meta = _decode_member_frame(
            staging_run_root, plan.model_id, member_var_id("snowfall_total", member), base_fh,
        )
        valid = np.isfinite(decoded_in)
        snow_scale = _KGM2_TO_INCHES * plan.cumulative.slr
        state.snow_kgm2 = np.where(valid, decoded_in / snow_scale, 0.0).astype(np.float32)
        state.snow_valid = valid
        if state.dst_transform is None:
            state.dst_transform = Affine(*[float(v) for v in meta["transform"]])
        # The next step's endpoint sampling needs csnow at base_fh.
        bundle = _fetch_member_bundle(
            plan=plan, member=member, fh=base_fh,
            fields={"csnow": MEMBER_CSNOW_PATTERN}, should_stop=should_stop,
        )
        csnow_native, crs, transform = bundle["csnow"]
        snow_ctx = plan.contexts["snowfall_total"]
        state.prev_csnow, _ = warp_to_target_grid(
            csnow_native, crs, transform,
            model=plan.model_id, region=plan.region,
            resampling=snow_ctx.resampling, src_nodata=None, dst_nodata=float("nan"),
        )
    return state


def _gate_and_write(
    *,
    plan: _MemberBuildPlan,
    ctx: _MemberVarContext,
    var_id: str,
    fh: int,
    values: np.ndarray,
    dst_transform: Any,
    staging_run_root: Path,
) -> str:
    if not check_pre_encode_value_sanity(
        values,
        ctx.colormap_spec,
        var_spec_model=ctx.var_spec,
        var_capability=ctx.capability,
        label=f"{plan.model_id}/{var_id}/fh{fh:03d} (member pass)",
    ):
        logger.error(
            "PRE-ENCODE SANITY GATE FAILED — member frame NOT written: %s/%s/fh%03d "
            "(a silently-bad member poisons downstream member products; the gate is never bypassed)",
            plan.model_id, var_id, fh,
        )
        return STATUS_GATE_FAILED
    write_slim_grid_frame_for_run_root(
        run_root=staging_run_root,
        model=plan.model_id,
        var=var_id,
        fh=fh,
        values=values,
        transform=dst_transform,
    )
    return STATUS_WRITTEN


def _process_member(
    *,
    plan: _MemberBuildPlan,
    member: str,
    staging_run_root: Path,
    should_stop: Callable[[], bool],
    record: Callable[[str, float | None], None],
) -> None:
    """Sequential ascending-fh processing of one member (cumulative-safe)."""
    missing_by_var = {
        base_var: [
            fh for fh in plan.fhs_by_var[base_var]
            if not member_frame_is_complete(
                staging_run_root, plan.model_id, member_var_id(base_var, member), fh,
            )
        ]
        for base_var in plan.contexts
    }
    total_missing = sum(len(v) for v in missing_by_var.values())

    # Every expected (var, fh) frame gets exactly one status per pass.
    recorded: set[tuple[str, int]] = set()

    def _rec(base_var: str, fh: int, status: str, frame_time_s: float | None = None) -> None:
        key = (base_var, fh)
        if key in recorded:
            return
        recorded.add(key)
        record(status, frame_time_s)

    for base_var in plan.contexts:
        missing = set(missing_by_var[base_var])
        for fh in plan.fhs_by_var[base_var]:
            if fh not in missing:
                _rec(base_var, fh, STATUS_RESUMED)
    if total_missing == 0:
        return

    resume_fh = min(min(fhs) for fhs in missing_by_var.values() if fhs)
    work_fhs = [fh for fh in plan.all_fhs if fh >= resume_fh]

    def _mark_remaining(status: str, from_fh: int) -> None:
        for base_var, fhs in missing_by_var.items():
            for fh in fhs:
                if fh >= from_fh:
                    _rec(base_var, fh, status)

    state = _CumulativeState()
    if plan.has_cumulative:
        prior_steps = [s for s in plan.step_fhs if s < resume_fh]
        if prior_steps:
            try:
                state = _rebase_cumulative_state(
                    plan=plan, member=member, staging_run_root=staging_run_root,
                    base_fh=prior_steps[-1], should_stop=should_stop,
                )
            except MemberFetchError as exc:
                if "preempted" in str(exc):
                    _mark_remaining(STATUS_PREEMPTED, resume_fh)
                    return
                logger.warning(
                    "Member %s rebase fetch failed at fh%03d: %s",
                    member, prior_steps[-1], exc,
                )
                _mark_remaining(STATUS_FETCH_FAILED, resume_fh)
                return
            except Exception:
                logger.exception("Member %s cumulative rebase failed", member)
                _mark_remaining(STATUS_ERROR, resume_fh)
                return

    for fh in work_fhs:
        if should_stop():
            _mark_remaining(STATUS_PREEMPTED, fh)
            return
        fields = _bundle_fields_for_fh(plan, fh)
        if not fields:
            continue
        frame_started = time.perf_counter()
        try:
            bundle = _fetch_member_bundle(
                plan=plan, member=member, fh=fh, fields=fields, should_stop=should_stop,
            )
        except MemberFetchError as exc:
            if "preempted" in str(exc):
                _mark_remaining(STATUS_PREEMPTED, fh)
                return
            logger.warning("Member %s bundle failed at fh%03d: %s", member, fh, exc)
            if plan.has_cumulative and fh in plan.step_fhs:
                # The cumulative chain cannot continue past a missing step —
                # abort the member for this pass; retried on the next pass.
                _mark_remaining(STATUS_FETCH_FAILED, fh)
                return
            for base_var in plan.contexts:
                if fh in missing_by_var[base_var] and not plan.contexts[base_var].derived:
                    _rec(base_var, fh, STATUS_FETCH_FAILED)
            continue
        except Exception:
            logger.exception("Member %s unexpected bundle error at fh%03d", member, fh)
            _mark_remaining(STATUS_ERROR, fh)
            return

        try:
            # tmp2m — direct field.
            tmp_ctx = plan.contexts.get("tmp2m")
            if tmp_ctx is not None and "tmp2m" in fields and fh in plan.fhs_by_var["tmp2m"]:
                if fh in missing_by_var["tmp2m"]:
                    raw, crs, transform = bundle["tmp2m"]
                    converted = convert_units(
                        raw, var_key="tmp2m", model_id=plan.model_id,
                        var_capability=tmp_ctx.capability,
                    )
                    warped, dst_transform = warp_to_target_grid(
                        converted, crs, transform,
                        model=plan.model_id, region=plan.region,
                        resampling=tmp_ctx.resampling, src_nodata=None,
                        dst_nodata=float("nan"),
                    )
                    status = _gate_and_write(
                        plan=plan, ctx=tmp_ctx, var_id=member_var_id("tmp2m", member),
                        fh=fh, values=warped, dst_transform=dst_transform,
                        staging_run_root=staging_run_root,
                    )
                    _rec("tmp2m", fh, status,
                         time.perf_counter() - frame_started if status == STATUS_WRITTEN else None)

            # Cumulative step — precip/snow.
            if plan.has_cumulative and fh in plan.step_fhs:
                apcp_native, apcp_crs, apcp_transform = bundle["apcp"]
                any_cum_ctx = plan.contexts.get("precip_total") or plan.contexts["snowfall_total"]
                apcp_warped, dst_transform = warp_to_target_grid(
                    apcp_native, apcp_crs, apcp_transform,
                    model=plan.model_id, region=plan.region,
                    resampling=any_cum_ctx.resampling, src_nodata=None,
                    dst_nodata=float("nan"),
                )
                state.dst_transform = dst_transform

                if "precip_total" in plan.contexts:
                    contribution, step_valid = precip_step_contribution(apcp_warped)
                    state.precip_kgm2, state.precip_valid = merge_cumulative_step(
                        state.precip_kgm2, state.precip_valid, contribution, step_valid,
                    )
                if "snowfall_total" in plan.contexts:
                    csnow_native, csnow_crs, csnow_transform = bundle["csnow"]
                    snow_ctx = plan.contexts["snowfall_total"]
                    csnow_warped, _ = warp_to_target_grid(
                        csnow_native, csnow_crs, csnow_transform,
                        model=plan.model_id, region=plan.region,
                        resampling=snow_ctx.resampling, src_nodata=None,
                        dst_nodata=float("nan"),
                    )
                    # step_endpoints sampling: csnow at [step start, step end];
                    # the fh-0 endpoint is skipped per skip_zero_hour_sample.
                    samples = []
                    start_fh = fh - plan.cumulative.step_hours
                    if state.prev_csnow is not None and (
                        start_fh > 0 or not plan.cumulative.skip_zero_hour_sample
                    ):
                        samples.append(state.prev_csnow)
                    samples.append(csnow_warped)
                    contribution, step_valid = snowfall_step_contribution(
                        apcp_warped, samples,
                        min_step_lwe=plan.cumulative.min_step_lwe,
                        snow_mask_threshold=plan.cumulative.snow_mask_threshold,
                    )
                    state.snow_kgm2, state.snow_valid = merge_cumulative_step(
                        state.snow_kgm2, state.snow_valid, contribution, step_valid,
                    )
                    state.prev_csnow = csnow_warped

                for base_var, scale in (
                    ("precip_total", _KGM2_TO_INCHES),
                    ("snowfall_total", _KGM2_TO_INCHES * plan.cumulative.slr),
                ):
                    ctx = plan.contexts.get(base_var)
                    if ctx is None or fh not in plan.fhs_by_var[base_var]:
                        continue
                    if fh not in missing_by_var[base_var]:
                        continue
                    cum = state.precip_kgm2 if base_var == "precip_total" else state.snow_kgm2
                    valid = state.precip_valid if base_var == "precip_total" else state.snow_valid
                    values_in = (cumulative_field(cum, valid) * np.float32(scale)).astype(np.float32)
                    status = _gate_and_write(
                        plan=plan, ctx=ctx, var_id=member_var_id(base_var, member),
                        fh=fh, values=values_in, dst_transform=state.dst_transform,
                        staging_run_root=staging_run_root,
                    )
                    _rec(base_var, fh, status,
                         time.perf_counter() - frame_started if status == STATUS_WRITTEN else None)
        except Exception:
            logger.exception("Member %s unexpected build error at fh%03d", member, fh)
            _mark_remaining(STATUS_ERROR, fh)
            return


# ── Pass pending / promote pending scans ────────────────────────────────────
def _iter_expected_member_frames(
    plugin: Any,
    model_id: str,
    run_id: str,
    *,
    data_root: Path | None = None,
    mean_coverage_only: bool = False,
):
    descriptors = ensemble_member_descriptors(plugin)
    cycle_hour = _cycle_hour_from_run_id(run_id)
    for base_var, descriptor in descriptors.items():
        fhs = sorted({int(fh) for fh in plugin.scheduled_fhs_for_var(base_var, cycle_hour)})
        derived = bool(getattr(
            _resolve_model_var_spec(model_id, base_var, plugin), "derived", False,
        ))
        if derived:
            fhs = [fh for fh in fhs if fh > 0]
        if mean_coverage_only and data_root is not None:
            capability = _resolve_model_var_capability(model_id, base_var, plugin)
            mean_var_id = _mean_var_id_for(base_var, capability)
            fhs = [
                fh for fh in fhs
                if _mean_frame_available(data_root, model_id, run_id, mean_var_id, fh)
            ]
        for member in ensemble_member_ids(descriptor):
            var_id = member_var_id(base_var, member)
            for fh in fhs:
                yield var_id, fh


def member_pass_pending(
    *,
    plugin: Any,
    model_id: str,
    run_id: str,
    data_root: Path,
    mean_coverage_only: bool = False,
) -> bool:
    """Cheap presence scan: does any enabled (member, fh) frame remain unwritten?

    Gate-failed frames stay "pending" by this definition and are re-attempted
    on later passes; a persistent gate failure is loudly logged each pass
    rather than silently forgotten. ``mean_coverage_only`` mirrors the plan
    builder's backfill cap so a mean-incomplete run stops reading as pending
    once its mean-covered member frames exist.
    """
    staging_run_root = data_root / "staging" / model_id / run_id
    for var_id, fh in _iter_expected_member_frames(
        plugin, model_id, run_id,
        data_root=data_root, mean_coverage_only=mean_coverage_only,
    ):
        if not member_frame_is_complete(staging_run_root, model_id, var_id, fh):
            return True
    return False


def member_promote_pending(
    *,
    plugin: Any,
    model_id: str,
    run_id: str,
    data_root: Path,
    mean_coverage_only: bool = False,
) -> bool:
    """Any member frame complete in STAGING but not in PUBLISHED?

    Covers the crash window between a completed pass and its promote: the
    next pass would find no build work, but the staged frames still need to
    ride a promote or they'd be stranded until the run ages out.
    """
    staging_run_root = data_root / "staging" / model_id / run_id
    published_run_root = data_root / "published" / model_id / run_id
    for var_id, fh in _iter_expected_member_frames(
        plugin, model_id, run_id,
        data_root=data_root, mean_coverage_only=mean_coverage_only,
    ):
        if member_frame_is_complete(
            staging_run_root, model_id, var_id, fh,
        ) and not member_frame_is_complete(published_run_root, model_id, var_id, fh):
            return True
    return False


# ── The pass ────────────────────────────────────────────────────────────────
@dataclass
class MemberPassSummary:
    run_id: str
    model_id: str
    counts: dict[str, int] = field(default_factory=dict)
    member_var_ids: list[str] = field(default_factory=list)
    frame_times_s: list[float] = field(default_factory=list)
    wall_s: float = 0.0
    preempted: bool = False

    @property
    def complete(self) -> bool:
        pending = (
            self.counts.get(STATUS_FETCH_FAILED, 0)
            + self.counts.get(STATUS_GATE_FAILED, 0)
            + self.counts.get(STATUS_ERROR, 0)
            + self.counts.get(STATUS_PREEMPTED, 0)
        )
        return pending == 0

    def asdict(self) -> dict[str, Any]:
        return {
            "run": self.run_id,
            "model": self.model_id,
            "counts": dict(self.counts),
            "member_var_count": len(self.member_var_ids),
            "frame_time_mean_s": (
                round(statistics.mean(self.frame_times_s), 3) if self.frame_times_s else None
            ),
            "wall_s": round(self.wall_s, 2),
            "preempted": self.preempted,
            "complete": self.complete,
        }


def run_member_pass(
    *,
    plugin: Any,
    model_id: str,
    run_id: str,
    data_root: Path,
    region: str,
    workers: int | None = None,
    should_stop: Callable[[], bool] | None = None,
    mean_coverage_only: bool = False,
) -> MemberPassSummary:
    """Run the member publish pass for one run; idempotent and preemptible.

    Writes slim frames into the STAGING run root only — the caller (the
    scheduler) is responsible for member manifest builds and promotion.
    Parallelism follows the plan mode: per-MEMBER in member_files mode (a
    member's fh sequence is order-dependent), per-(var, fh) UNIT in
    pf_subset mode (units are independent — one subset per unit).
    """
    resolved_workers = workers if workers is not None else member_fetch_workers()
    resolved_workers = max(1, min(MAX_MEMBER_FETCH_WORKERS, int(resolved_workers)))
    stop = should_stop or (lambda: False)
    summary = MemberPassSummary(run_id=run_id, model_id=model_id)

    try:
        plan = build_member_plan(
            plugin, model_id, run_id, region,
            data_root=data_root, mean_coverage_only=mean_coverage_only,
        )
    except Exception:
        logger.exception("Member pass: failed building plan model=%s run=%s", model_id, run_id)
        summary.counts[STATUS_ERROR] = summary.counts.get(STATUS_ERROR, 0) + 1
        return summary
    if plan is None:
        logger.info("Member pass: no enabled member descriptors for model=%s", model_id)
        return summary
    summary.member_var_ids = plan.member_var_ids

    staging_run_root = data_root / "staging" / model_id / run_id
    started = time.perf_counter()
    counts_lock = threading.Lock()

    def _record(status: str, frame_time_s: float | None = None) -> None:
        with counts_lock:
            summary.counts[status] = summary.counts.get(status, 0) + 1
            if frame_time_s is not None:
                summary.frame_times_s.append(frame_time_s)

    if plan.mode == MODE_PF_SUBSET:
        work_items: list[Any] = [
            (base_var, fh)
            for base_var in plan.contexts
            for fh in plan.fhs_by_var[base_var]
        ]

        def _one(item: Any) -> None:
            base_var, fh = item
            try:
                _process_pf_unit(
                    plan=plan, base_var=base_var, fh=fh,
                    staging_run_root=staging_run_root,
                    should_stop=stop, record=_record,
                )
            except Exception:  # noqa: BLE001 — recorded; retried next pass
                logger.exception(
                    "Member pass: unexpected error processing pf unit %s fh%03d",
                    base_var, fh,
                )
                _record(STATUS_ERROR)
    else:
        work_items = list(plan.member_ids)

        def _one(item: Any) -> None:
            try:
                _process_member(
                    plan=plan, member=item, staging_run_root=staging_run_root,
                    should_stop=stop, record=_record,
                )
            except Exception:  # noqa: BLE001 — recorded; retried next pass
                logger.exception("Member pass: unexpected error processing member=%s", item)
                _record(STATUS_ERROR)

    if resolved_workers <= 1:
        for item in work_items:
            _one(item)
    else:
        with ThreadPoolExecutor(
            max_workers=resolved_workers, thread_name_prefix="member-pass",
        ) as pool:
            futures = [pool.submit(_one, item) for item in work_items]
            for future in as_completed(futures):
                future.result()

    summary.wall_s = time.perf_counter() - started
    if summary.counts.get(STATUS_PREEMPTED, 0) > 0:
        summary.preempted = True
    logger.info(
        "Member pass summary: run=%s model=%s %s wall=%.1fs workers=%d complete=%s preempted=%s",
        run_id,
        model_id,
        " ".join(f"{key}={value}" for key, value in sorted(summary.counts.items())) or "no_work",
        summary.wall_s,
        resolved_workers,
        summary.complete,
        summary.preempted,
    )
    return summary
