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

// CONUS bounding box [west, south, east, north]. NAM/NBM are CONUS-only models;
// outside this box they are omitted from pills and the meteogram request.
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

export function modelShortName(model: string): string {
  return MODEL_SHORT_NAMES[model.toLowerCase()] ?? model.toUpperCase();
}
