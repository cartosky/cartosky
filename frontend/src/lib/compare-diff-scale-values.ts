/** Pure scale lookup for compare difference mode; kept Vite-runtime agnostic for regression tests. */
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
  hgt500_anom:    { maxAbs: 100,  units: "m" },
  vort500:        { maxAbs: 4,    units: "×10⁻⁵/s" },
  pwat:           { maxAbs: 0.5,  units: "in" },
  apcp:           { maxAbs: 0.25, units: "in/hr" },
  precip_total:   { maxAbs: 2,    units: "in" },
  snowfall_total: { maxAbs: 6,    units: "in" },
  mlcape:         { maxAbs: 500,  units: "J/kg" },
};

/** Rolling precipitation anomaly keys inherit the shared ±2 inch diff scale. */
export const PRECIP_ANOM_VAR_KEY_PATTERN = /^precip_\d+d_anom$/;

const PRECIP_ANOM_DIFF_SCALE: DiffScale = { maxAbs: 2, units: "in" };
const ENSEMBLE_PERCENTILE_VAR_ID = /^(?<base>.+)__p\d{2}$/;
const ENSEMBLE_PROB_VAR_ID = /^.+__prob_(?:gt|lt)_\d+p\d+$/;
const PROBABILITY_DIFF_SCALE: DiffScale = { maxAbs: 50, units: "%" };

export const DEFAULT_DIFF_SCALE: DiffScale = { maxAbs: 10, units: "" };

/** Resolve exact, rolling-anomaly, ensemble-percentile, and probability scales. */
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
