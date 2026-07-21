import { OVERLAY_DEFAULT_OPACITY } from "@/lib/config";
import type { LegendEntry, LegendPayload } from "@/components/map-legend";
import type { DiffScale } from "@/lib/compare-diff-scale-values";

export {
  COMPARE_DIFF_SCALES,
  DEFAULT_DIFF_SCALE,
  getDiffScale,
  PRECIP_ANOM_VAR_KEY_PATTERN,
} from "@/lib/compare-diff-scale-values";
export type { DiffScale } from "@/lib/compare-diff-scale-values";

/**
 * Diverging-legend construction for compare difference mode. Pure scale lookup
 * lives in compare-diff-scale-values so Node-side regressions avoid Vite env state.
 */

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
