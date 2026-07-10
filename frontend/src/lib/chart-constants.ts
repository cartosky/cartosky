// Shared design system for Model Guidance charts (Models + Ensembles tabs).
// Colors MUST be imported from this file in every chart component.
// Never hardcode hex in chart components.

export const MODEL_COLORS = {
  ecmwf: "#E85002",   // ECMWF orange
  gfs: "#1E6BB8",     // NOAA blue
  nam: "#2CA02C",     // green
  aifs: "#9467BD",    // purple (ECMWF AI)
  aigfs: "#17BECF",   // cyan — reserved; omitted from Phase 1A/1B/2/3 charts (NOMADS reliability)
  nbm: "#BCBD22",     // olive
  hrrr: "#FF7F0E",    // amber — reserved; no chart work Phase 1–3 (short-range; future separate section)
  eps: "#E85002",     // same family as ECMWF
  gefs: "#1E6BB8",    // same family as GFS
} as const;

export const ENSEMBLE_COLORS = {
  eps_member: "rgba(232, 80, 2, 0.12)",
  eps_member_stroke: "rgba(232, 80, 2, 0.35)",
  eps_mean: "#E85002",
  eps_control: "#FFFFFF",
  eps_spread_fill: "rgba(232, 80, 2, 0.18)",
  gefs_member: "rgba(30, 107, 184, 0.12)",
  gefs_member_stroke: "rgba(30, 107, 184, 0.35)",
  gefs_mean: "#1E6BB8",
  gefs_control: "#FFFFFF",
  gefs_spread_fill: "rgba(30, 107, 184, 0.18)",
} as const;

export const CHART_THEME = {
  background: "hsl(222 22% 8%)",        // matches .dark --background
  cardBackground: "hsl(222 22% 11%)",   // matches .dark --card
  axisLabel: "hsl(215 14% 55%)",        // matches --muted-foreground
  gridline: "hsla(0, 0%, 100%, 0.08)",
  tickFontSize: 11,
  titleColor: "hsl(210 20% 92%)",
  nowMarker: "#F59E0B",
  dayBoundary: "hsla(0, 0%, 100%, 0.15)",
} as const;

/**
 * Semantic colors used in the Model Detail single-model card.
 * These are independent of model identity — they encode meteorological meaning.
 */
export const DETAIL_COLORS = {
  /** Daily high temperature bars — warm red */
  tempHigh: "#E05252",
  /** Daily low temperature bars — cool blue */
  tempLow: "#5B9BD5",
  /** Temperature value labels and bar strokes */
  tempStroke: "#E8E8E8",
  /** Precipitation bars fill base (hex, no alpha — alpha appended in canvas) */
  precipBar: "#4CAF82",
  /** Precipitation bars stroke */
  precipStroke: "#4CAF82",
  /** Cumulative precipitation line */
  precipCumul: "#4CAF82",
  /** Wind speed line */
  wind: "#94A3B8",
} as const;

// Long-range temperature guidance models (Models tab Phase 1A). NAM omitted — discontinued.
export const TEMPERATURE_GUIDANCE_MODELS = ["ecmwf", "gfs", "aifs", "nbm"] as const;

export type TemperatureGuidanceModel = (typeof TEMPERATURE_GUIDANCE_MODELS)[number];

// Cumulative precipitation guidance models (Models tab Phase 1B). NAM omitted — discontinued.
export const PRECIP_GUIDANCE_MODELS = ["ecmwf", "gfs", "nbm", "aifs"] as const;

// 10 m wind speed guidance models (Models tab Phase 1B).
export const WIND_GUIDANCE_MODELS = ["ecmwf", "gfs", "nbm"] as const;

// Variables the Models tab requests in one meteogram call (Phase 1B). Shared by
// the tab and the Forecast page prefetch so the warm cache key matches.
export const MODELS_TAB_VARIABLES = ["tmp2m", "precip_total", "wspd10m"] as const;

// Ensemble guidance models (Ensembles tab, Phase 2). Mean-only products.
export const ENSEMBLE_GUIDANCE_MODELS = ["eps", "gefs"] as const;

// Variables the Ensembles tab requests in one meteogram call (Phase 2).
export const ENSEMBLES_TAB_VARIABLES = ["tmp2m", "precip_total"] as const;

// Models omitted from pills and meteogram requests outside CONUS.
export const CONUS_ONLY_GUIDANCE_MODELS = new Set<string>(["nbm"]);

// Anchor (primary) model. Drawn heavier, at full opacity, and on top of the
// others; secondary models are slightly de-emphasized.
export const ANCHOR_GUIDANCE_MODEL = "ecmwf";

export function isAnchorModel(model: string): boolean {
  return model.toLowerCase() === ANCHOR_GUIDANCE_MODEL;
}

// Model display short names, used by pills and tooltips.
export const MODEL_SHORT_NAMES: Record<string, string> = {
  ecmwf: "ECMWF",
  gfs: "GFS",
  nam: "NAM",
  aifs: "AIFS",
  aigfs: "AIGFS",
  nbm: "NBM",
  hrrr: "HRRR",
  eps: "EPS",
  gefs: "GEFS",
};

// CONUS bounding box [west, south, east, north]. NBM is CONUS-only for guidance charts;
// outside this box it is omitted from pills and the meteogram request.
export const CONUS_BBOX = { west: -134, south: 24, east: -60, north: 55 } as const;

export function isInsideConus(lat: number, lon: number): boolean {
  return (
    lon >= CONUS_BBOX.west &&
    lon <= CONUS_BBOX.east &&
    lat >= CONUS_BBOX.south &&
    lat <= CONUS_BBOX.north
  );
}

export function modelColor(model: string): string {
  return (MODEL_COLORS as Record<string, string>)[model.toLowerCase()] ?? "#9CA3AF";
}

/**
 * Solid stroke color for a model line. The anchor model renders at full opacity;
 * secondary models are slightly de-emphasized so the anchor reads as primary.
 * Pass `alpha` to override (e.g. equal weighting in single-model sub-charts).
 */
export function modelLineStroke(model: string, alpha?: number): string {
  const hex = modelColor(model);
  const a = alpha ?? (isAnchorModel(model) ? 1 : 0.7);
  if (!hex.startsWith("#") || hex.length < 7) return hex;
  const r = Number.parseInt(hex.slice(1, 3), 16);
  const g = Number.parseInt(hex.slice(3, 5), 16);
  const b = Number.parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}

/** Line width (px): anchor model slightly heavier than the secondary models. */
export function modelLineWidth(model: string): number {
  return isAnchorModel(model) ? 2.5 : 1.75;
}

/**
 * Draw order with the anchor model last so it renders on top of the others.
 * Stable: preserves the relative order of the non-anchor models.
 */
export function orderModelsAnchorLast<T extends string>(models: readonly T[]): T[] {
  return [...models].sort((a, b) => Number(isAnchorModel(a)) - Number(isAnchorModel(b)));
}

export function modelShortName(model: string): string {
  return MODEL_SHORT_NAMES[model.toLowerCase()] ?? model.toUpperCase();
}

/**
 * Solid stroke color for an ensemble mean line (Ensembles tab, Phase 2).
 * EPS shares the ECMWF family, GEFS the GFS family.
 */
export function ensembleMeanStroke(model: string): string {
  return model.toLowerCase() === "gefs"
    ? ENSEMBLE_COLORS.gefs_mean
    : ENSEMBLE_COLORS.eps_mean;
}

/** Line width (px) for ensemble mean lines — both read as primary. */
export function ensembleMeanWidth(): number {
  return 2.25;
}

// Models whose per-member plume data the meteogram can serve (member pipeline
// Phase 5; EPS members landed with pipeline Phase 4). Deploy-ordering: a model
// listed here must ALSO be on the API's CARTOSKY_BINARY_SAMPLING_MODELS env,
// or its include_members requests 400.
export const MEMBER_PLUME_MODELS = ["gefs", "eps"] as const;

/**
 * Distinct per-member stroke for the plume charts. Golden-angle hue spacing
 * makes adjacent member indices maximally distinguishable; fixed saturation /
 * lightness tuned for the dark chart background. The mean (bold white) and
 * control (dashed model color) can never be confused with a member hue at
 * these widths/styles.
 */
export function plumeMemberStroke(memberIndex: number): string {
  const hue = Math.round((memberIndex * 137.508) % 360);
  return `hsla(${hue}, 70%, 62%, 0.8)`;
}

/** Bold mean line on plume charts — white reads on top of any member hue. */
export const PLUME_MEAN_STROKE = "#FFFFFF";

// ── Ensemble stats meteogram charts (backlog B1) ───────────────────────────
// MUST mirror the backend stats descriptors (`ensemble.stats` in
// models/gefs.py + models/eps.py); var ids are derived with the same grammar
// as models/base.py `ensemble_stats_product_ids`. Only variables listed in
// ENSEMBLE_STATS_PROB_THRESHOLDS get band + probability charts under the
// member views — to add one (e.g. snowfall_total: [1, 3, 6, 12] when a
// snowfall variable joins the tab, or tmp2m with backlog B2), add its
// threshold entry here.

export const ENSEMBLE_STATS_PERCENTILES = [10, 25, 50, 75, 90] as const;

export const ENSEMBLE_STATS_PROB_THRESHOLDS: Partial<Record<string, readonly number[]>> = {
  precip_total: [0.1, 0.25, 0.5, 1, 1.5, 2],
};

/** `0.5 -> "0p5"`, `1 -> "1p0"` — mirrors backend `format_prob_threshold`. */
function formatProbThresholdToken(value: number): string {
  const text = String(value);
  return text.includes(".") ? text.replace(".", "p") : `${text}p0`;
}

/** `("precip_total", 10) -> "precip_total__p10"` */
export function ensemblePercentileVarId(baseVar: string, percentile: number): string {
  return `${baseVar}__p${String(percentile).padStart(2, "0")}`;
}

/** `("precip_total", 0.5) -> "precip_total__prob_gt_0p5"` */
export function ensembleProbVarId(baseVar: string, threshold: number): string {
  return `${baseVar}__prob_gt_${formatProbThresholdToken(threshold)}`;
}

/**
 * Percentile band fills: outer = 10–90th, inner = 25–75th drawn over it (the
 * overlap reads darker). Model family hue matches the plume/mean charts.
 */
export function ensemblePercentileBandFill(model: string, band: "outer" | "inner"): string {
  const rgb = model.toLowerCase() === "gefs" ? "30, 107, 184" : "232, 80, 2";
  return `rgba(${rgb}, ${band === "outer" ? 0.16 : 0.2})`;
}

/** Thin band-edge stroke (p10/p25/p75/p90) in the model family hue. */
export function ensemblePercentileEdgeStroke(model: string): string {
  const rgb = model.toLowerCase() === "gefs" ? "30, 107, 184" : "232, 80, 2";
  return `rgba(${rgb}, 0.5)`;
}

/**
 * Cool→hot stroke ramp for exceedance-probability lines, indexed by the
 * threshold's position in the configured list (lowest threshold = cool blue,
 * highest = magenta). Six stops cover the largest configured list.
 */
export const ENSEMBLE_PROB_THRESHOLD_STROKES = [
  "#60A5FA",
  "#34D399",
  "#FBBF24",
  "#FB923C",
  "#F87171",
  "#E879F9",
] as const;

export function ensembleProbThresholdStroke(index: number): string {
  const clamped = Math.max(0, Math.min(index, ENSEMBLE_PROB_THRESHOLD_STROKES.length - 1));
  return ENSEMBLE_PROB_THRESHOLD_STROKES[clamped]!;
}

/** Control-member stroke (drawn dashed by the plume chart). White like the
 * mean — a model-colored dash got lost among the member hues; the dash
 * pattern alone separates it from the solid mean line. */
export function plumeControlStroke(model: string): string {
  void model;
  return PLUME_MEAN_STROKE;
}

