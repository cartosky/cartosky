"""Ensemble stats math (member pipeline Phase 6 — Tier 2).

Pure array math for percentile and probability-of-exceedance grids computed
over a member stack (axis 0 = members). No I/O, no model knowledge — the
stats pass owns decode/gate/publish; this module owns the numbers.

The percentile implementation exists because ``np.nanpercentile`` degrades
to a pixel-bound Python fallback in the presence of NaNs: the sizing spike
measured 13.7 s/fh and the design benchmark 17.1 s/fh on a GEFS-shaped
stack, vs 0.25 s/fh here for all five percentiles at once (67×). One
``np.sort`` (NaNs sort last) serves every requested percentile, and the
same valid-count array serves every probability threshold.

Parity contract (stats design §4): result-identical to
``np.nanpercentile(stack, qs, axis=0, method="linear")`` — the method is
named explicitly so a numpy default change cannot shift published products
silently. Pinned by tests over NaN fringes, scattered gaps, all-NaN pixels,
and single-valid-member pixels.
"""

from __future__ import annotations

import numpy as np

__all__ = ["sorted_nanpercentile", "prob_exceedance"]


def sorted_nanpercentile(stack: np.ndarray, percentiles: list[int]) -> np.ndarray:
    """Nan-aware percentiles over axis 0 via one sort.

    ``stack``: (members, H, W) float array; NaN = member missing at pixel.
    Returns (len(percentiles), H, W) float32 with NaN where NO member is
    valid. Linear interpolation at fractional ranks over the per-pixel
    valid member count — ``np.nanpercentile(..., method="linear")``
    semantics exactly.
    """
    if stack.ndim != 3:
        raise ValueError(f"Expected (members, H, W) stack, got shape {stack.shape}")
    if not percentiles:
        return np.empty((0,) + stack.shape[1:], dtype=np.float32)

    ordered = np.sort(stack, axis=0)  # NaNs sort to the end of the member axis
    valid = np.sum(~np.isnan(stack), axis=0)
    out = np.full((len(percentiles),) + stack.shape[1:], np.nan, dtype=np.float32)

    has = valid > 0
    idx = np.flatnonzero(has.ravel())
    if idx.size == 0:
        return out
    valid_flat = valid.ravel()[idx]
    flat = ordered.reshape(ordered.shape[0], -1)

    for i, q in enumerate(percentiles):
        rank = (float(q) / 100.0) * (valid_flat - 1)
        lo = np.floor(rank).astype(np.int64)
        hi = np.minimum(lo + 1, valid_flat - 1)
        frac = (rank - lo).astype(np.float32)
        v_lo = flat[lo, idx]
        v_hi = flat[hi, idx]
        out[i].ravel()[idx] = v_lo + (v_hi - v_lo) * frac
    return out


def prob_exceedance(stack: np.ndarray, thresholds: list[float]) -> np.ndarray:
    """Probability (%) that a member exceeds each threshold, per pixel.

    ``100 * count(member > threshold) / valid_members``; NaN where no member
    is valid (matches the percentile NaN pattern — a pixel outside every
    member's coverage carries no probability). Strict ``>`` per the plan's
    "probability of exceedance" product definition.
    """
    if stack.ndim != 3:
        raise ValueError(f"Expected (members, H, W) stack, got shape {stack.shape}")
    if not thresholds:
        return np.empty((0,) + stack.shape[1:], dtype=np.float32)

    valid = np.sum(~np.isnan(stack), axis=0)
    has = valid > 0
    out = np.full((len(thresholds),) + stack.shape[1:], np.nan, dtype=np.float32)
    safe_valid = np.maximum(valid, 1).astype(np.float32)
    for i, threshold in enumerate(thresholds):
        # NaN > x is False, so nansum semantics fall out of the comparison.
        count = np.sum(stack > np.float32(threshold), axis=0)
        values = (100.0 * count / safe_valid).astype(np.float32)
        out[i] = np.where(has, values, np.nan)
    return out
