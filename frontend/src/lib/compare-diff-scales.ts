import { OVERLAY_DEFAULT_OPACITY } from "@/lib/config";
import type { LegendEntry, LegendPayload } from "@/components/map-legend";

/**
 * Symmetric per-variable display scales and diverging-legend construction for
 * compare difference mode. All scale values are named constants here — never
 * inline literals in components (see design doc, Architectural Decisions #4).
 */

export type DiffScale = {
  /** Symmetric half-range: the diff color ramp spans [-maxAbs, +maxAbs]. */
  maxAbs: number;
  units: string;
};

export const COMPARE_DIFF_SCALES: Record<string, DiffScale> = {
  tmp2m:          { maxAbs: 15,   units: "°F" },
  dp2m:           { maxAbs: 15,   units: "°F" },
  tmp850:         { maxAbs: 5,    units: "°C" },
  tmp2m_anom:     { maxAbs: 10,   units: "°F" },
  tmp850_anom:    { maxAbs: 6,    units: "°C" },
  wspd10m:        { maxAbs: 15,   units: "mph" },
  wgst10m:        { maxAbs: 15,   units: "mph" },
  wspd850:        { maxAbs: 20,   units: "kt" },
  wspd300:        { maxAbs: 20,   units: "kt" },
  hgt500:         { maxAbs: 20,   units: "dam" },
  hgt500_anom:    { maxAbs: 400,  units: "m" },
  vort500:        { maxAbs: 4,    units: "×10⁻⁵/s" },
  pwat:           { maxAbs: 0.5,  units: "in" },
  apcp:           { maxAbs: 0.25, units: "in/hr" },
  precip_total:   { maxAbs: 2,    units: "in" },
  snowfall_total: { maxAbs: 6,    units: "in" },
  mlcape:         { maxAbs: 500,  units: "J/kg" },
};

/**
 * Rolling precip anomaly keys (`precip_5d_anom` … `precip_16d_anom`, plus any
 * future window). Shared with diff eligibility so a key can never be
 * diff-eligible without a real scale (the ±10 unitless default renders
 * near-white).
 */
export const PRECIP_ANOM_VAR_KEY_PATTERN = /^precip_\d+d_anom$/;

/** Design doc "Per-variable symmetric scales": precip anomalies at ±2 in. */
const PRECIP_ANOM_DIFF_SCALE: DiffScale = { maxAbs: 2, units: "in" };

/**
 * Ensemble stats runtime ids (stats design §4.1 naming): percentile grids
 * diff in the base variable's physical units and inherit its scale;
 * probability grids diff in percentage points — ±50 pp (ratified
 * 2026-07-09: run-to-run probability shifts rarely exceed that, and a full
 * ±100 range wastes contrast on the common case).
 */
const ENSEMBLE_PERCENTILE_VAR_ID = /^(?<base>.+)__p\d{2}$/;
const ENSEMBLE_PROB_VAR_ID = /^.+__prob_(?:gt|lt)_\d+p\d+$/;
const PROBABILITY_DIFF_SCALE: DiffScale = { maxAbs: 50, units: "%" };

/** Fallback for any var_key not in {@link COMPARE_DIFF_SCALES} — never throws. */
export const DEFAULT_DIFF_SCALE: DiffScale = { maxAbs: 10, units: "" };

export function getDiffScale(varKey: string | null | undefined): DiffScale {
  const key = String(varKey ?? "").trim();
  const exact = COMPARE_DIFF_SCALES[key];
  if (exact) {
    return exact;
  }
  if (PRECIP_ANOM_VAR_KEY_PATTERN.test(key)) {
    return PRECIP_ANOM_DIFF_SCALE;
  }
  const percentile = ENSEMBLE_PERCENTILE_VAR_ID.exec(key);
  if (percentile?.groups?.base) {
    return getDiffScale(percentile.groups.base);
  }
  if (ENSEMBLE_PROB_VAR_ID.test(key)) {
    return PROBABILITY_DIFF_SCALE;
  }
  return DEFAULT_DIFF_SCALE;
}

/**
 * Symmetric legend ticks at [-max, -2/3·max, -1/3·max, 0, +1/3·max, +2/3·max,
 * +max]. Rounded to clean values: integers when the step is ≥1 (e.g. ±15 →
 * -15, -10, -5, 0, 5, 10, 15), otherwise to 1–2 decimals so sub-unit scales
 * (pwat, apcp) keep distinct, readable ticks instead of collapsing to zero.
 */
export function deriveDiffLegendTicks(maxAbs: number): number[] {
  const fractions = [-1, -2 / 3, -1 / 3, 0, 1 / 3, 2 / 3, 1];
  const step = Math.abs(maxAbs) / 3;
  const decimals = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
  const factor = 10 ** decimals;
  return fractions.map((fraction) => Math.round(fraction * maxAbs * factor) / factor);
}

/** Symmetric blue → white → red diverging color stops anchored at ±maxAbs. */
function buildDiffStops(maxAbs: number): LegendEntry[] {
  return [
    { value: -maxAbs, color: "#2166ac" },
    { value: -(2 / 3) * maxAbs, color: "#92c5de" },
    { value: 0, color: "#f7f7f7" },
    { value: (2 / 3) * maxAbs, color: "#f4a582" },
    { value: maxAbs, color: "#d6604d" },
  ];
}

/**
 * Build the client-side diverging legend payload for a diff. Title encodes the
 * Left − Right sign convention; entries form the blue→white→red ramp consumed
 * by both the WebGL LUT (via MapCanvas `gridLegend`) and `CompareDiffLegend`.
 */
export function buildDiffLegend(
  leftModel: string,
  rightModel: string,
  varKey: string,
  scale: DiffScale,
): LegendPayload {
  const units = scale.units ? `Δ${scale.units}` : "Δ";
  return {
    title: `Difference: ${leftModel.toUpperCase()} − ${rightModel.toUpperCase()}`,
    units,
    kind: "continuous",
    id: `compare-diff:${varKey}`,
    entries: buildDiffStops(scale.maxAbs),
    opacity: OVERLAY_DEFAULT_OPACITY,
  };
}
