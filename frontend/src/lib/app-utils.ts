/**
 * Pure utility functions and types extracted from App.tsx.
 *
 * These have no React dependencies — they are data-transformation helpers,
 * type definitions, and constants that support the viewer's selection,
 * legend, frame-resolution, and variable-normalisation logic.
 */

import type {
  CapabilitiesResponse,
  CapabilityModel,
  CapabilityVariable,
  FrameRow,
  GridManifestResponse,
  LegendMeta,
  ModelDefaultFrameSelection,
  ModelTimeAxisMode,
  RegionPreset,
  RunManifestResponse,
} from "@/lib/api";
import { readCapabilityRenderSubstrates } from "@/lib/api";
import type { LegendPayload } from "@/components/map-legend";
import type { SharePayload } from "@/components/share/share-utils";
import type { BasemapMode } from "@/components/map-canvas";
import { API_ORIGIN, type WeatherSubstrate } from "@/lib/config";
import {
  formatObservedCompactTime,
  formatValidTime,
  parseRunId,
  validAxisLabel,
  type TimeAxisMode,
} from "@/lib/time-axis";

// ── Constants ─────────────────────────────────────────────────────────

export const ANIMATION_SPEEDS = [
  { label: "1×",   delayMs: 200 },
  { label: "2×",   delayMs: 100 },
  { label: "4×",   delayMs:  50 },
  { label: "0.5×", delayMs: 400 },
] as const;

export type AnimationSpeed = typeof ANIMATION_SPEEDS[number];
export const DEFAULT_ANIMATION_DELAY_MS = 200;

export const AUTOPLAY_UI_SYNC_MS = 120;
export const AUTOPLAY_READY_AHEAD = 3;
export const AUTOPLAY_SKIP_WINDOW = 8;
/** Stall time before the loop attempts to skip ahead to a ready frame. */
export const AUTOPLAY_STALL_SKIP_MS = 500;
export const GRID_PLAY_START_AHEAD_FRAMES = 4;
export const GRID_PLAY_STALL_MS = 1500;
export const FRAME_STATUS_BADGE_MS = 900;
export const READY_URL_TTL_MS = 30_000;
export const READY_URL_LIMIT = 160;
export const INFLIGHT_FRAME_TTL_MS = 12_000;
export const PRELOAD_START_RATIO = 0.7;
export const PRELOAD_STALL_MS = 8000;
export const FRAME_MAX_RETRIES = 3;
export const FRAME_HARD_DEADLINE_MS = 30_000;
export const FRAME_RETRY_BASE_MS = 1200;
export const SCRUB_COMMIT_NEIGHBOR_WINDOW = 2;
/** Minimum lag (forecast hours) between scrub target and displayed ready frame to enter burst prefetch. */
export const SCRUB_LAG_BURST_LAG_HOURS = 36;
/** Mobile/tablet: enter burst prefetch sooner when display trails the thumb. */
export const SCRUB_LAG_BURST_LAG_HOURS_MOBILE = 24;
/** Texture warm queue size during lag burst (forecast grid, desktop/high tier). */
export const SCRUB_LAG_BURST_WARM_LIMIT = 28;
/** Texture warm queue size during lag burst on mobile/low tier. */
export const SCRUB_LAG_BURST_WARM_LIMIT_MOBILE = 20;
/** Prefetch URL budget during lag burst / far-end forward scrub (desktop). */
export const SCRUB_LAG_BURST_PREFETCH_BUDGET = 28;
/** Prefetch URL budget during lag burst on mobile/tablet. */
export const SCRUB_LAG_BURST_PREFETCH_BUDGET_MOBILE = 20;
/** Forward-scrub ahead-only bias on long timelines past this forecast hour (desktop). */
export const SCRUB_FAR_END_FORWARD_FH = 168;
/** Forward-scrub ahead-only bias on mobile/tablet long timelines. */
export const SCRUB_FAR_END_FORWARD_FH_MOBILE = 120;
/** Minimum manifest frame count treated as a long timeline for far-end prefetch tuning. */
export const SCRUB_LONG_TIMELINE_FRAMES = 72;
/** Lower bar on mobile so burst prefetch can engage on medium-length runs (e.g. HRRR). */
export const SCRUB_LONG_TIMELINE_FRAMES_MOBILE = 48;
export const VARIABLE_SWITCH_TIMEOUT_MS = 2500;

/** Forecast-hour distance between scrub target and the displayed ready frame. */
export function resolveScrubDisplayLagHours(
  targetHour: number | null | undefined,
  displayedHour: number | null | undefined,
): number {
  if (!Number.isFinite(targetHour) || !Number.isFinite(displayedHour)) {
    return 0;
  }
  return Math.abs(Number(targetHour) - Number(displayedHour));
}

export type GridFrameCoverageIssue =
  | { kind: "slider_missing_grid"; hours: number[] }
  | { kind: "grid_missing_url"; hours: number[] };

export type GridFrameCoverageReport = {
  issues: GridFrameCoverageIssue[];
  sliderHours: number[];
  gridHours: number[];
};

/** Compare slider-visible hours against grid-manifest hours for coverage gaps. */
export function auditGridFrameCoverage(params: {
  selectableFrameHours: number[];
  gridFrameHours: number[];
  gridFrameByHour: ReadonlyMap<number, { url?: string | null | undefined }>;
}): GridFrameCoverageReport {
  const sliderHours = Array.from(new Set(
    params.selectableFrameHours.filter(Number.isFinite).map((hour) => Number(hour)),
  )).sort((a, b) => a - b);
  const gridHours = Array.from(new Set(
    params.gridFrameHours.filter(Number.isFinite).map((hour) => Number(hour)),
  )).sort((a, b) => a - b);
  const gridHourSet = new Set(gridHours);

  const sliderMissingGrid = sliderHours.filter((hour) => !gridHourSet.has(hour));
  const gridMissingUrl = gridHours.filter((hour) => {
    const url = String(params.gridFrameByHour.get(hour)?.url ?? "").trim();
    return !url;
  });

  const issues: GridFrameCoverageIssue[] = [];
  if (sliderMissingGrid.length > 0) {
    issues.push({ kind: "slider_missing_grid", hours: sliderMissingGrid });
  }
  if (gridMissingUrl.length > 0) {
    issues.push({ kind: "grid_missing_url", hours: gridMissingUrl });
  }

  return { issues, sliderHours, gridHours };
}
export const PERMALINK_SYNC_DEBOUNCE_MS = 200;

export const BASEMAP_MODE_STORAGE_KEY = "twf.map.basemap_mode";
export const LEGEND_VISIBILITY_STORAGE_KEY = "twf.map.legend_visible";
export const POINT_LABELS_STORAGE_KEY = "twf.map.point_labels_enabled";
export const NWS_WARNINGS_STORAGE_KEY = "twf.map.nws_warnings_enabled";
export const ZOOM_CONTROLS_STORAGE_KEY = "twf.map.zoom_controls_visible";
/**
 * Phase 6 rail width override, keyed by breakpoint class (§6.4) so a laptop
 * preference never follows the user to an external monitor. Mirrored by the
 * inline boot-shell script in index.html.
 */
export const RAIL_MODE_STORAGE_KEY_WIDE = "twf.rail.mode.wide";
export const RAIL_MODE_STORAGE_KEY_NARROW = "twf.rail.mode.narrow";
export const ANIMATION_DELAY_STORAGE_KEY = "cartosky_animation_delay_ms";
export const MODEL_ORDER_BY_ID: Record<string, number> = {
  hrrr: 0,
  nam: 1,
  nbm: 2,
  gfs: 3,
  ecmwf: 4,
  aifs: 5,
  aigfs: 6,
  gefs: 7,
  eps: 8,
  ndfd: 9,
  spc: 10,
  cpc: 11,
  wpc: 12,
};

// ── Types ─────────────────────────────────────────────────────────────

export type NewRunNoticeState = {
  model: string;
  previousRunId: string;
  latestRunId: string;
};

export type Option = {
  value: string;
  label: string;
};

export type GroupedOption = Option & {
  group: string | null;
  hasStats?: boolean;
};

export type VariableOption = GroupedOption;

export type VariableEntry = {
  id: string;
  name?: string;
  displayName?: string;
  defaultFh?: number | null;
  buildable?: boolean;
  kind?: string | null;
  units?: string;
  displayResamplingOverride?: string | null;
  group?: string | null;
  renderSubstrates?: WeatherSubstrate[];
  supportedBuildRegions?: string[];
  hasStats?: boolean;
};

type VariableUiOverride = {
  label?: string;
  group?: string | null;
  order?: number;
};

type ModelUiOverride = {
  label?: string;
  group?: string | null;
  order?: number;
};

export type ModelEntry = {
  id: string;
  displayName?: string;
  order?: number | null;
};

export type PendingLoopStartMetric = {
  startedAt: number;
};

export type PendingVariableSwitchMetric = {
  toVariableId: string;
};

export type VariableSwitchState = {
  fromVariable: string;
  toVariable: string;
  startedAt: number;
  visualState: "holding_old" | "warming_new" | "promoting_new";
};

export type ScrubCommitIntent = {
  hour: number;
  direction: 1 | -1 | 0;
  startedAt: number;
};

export type ScrubPhase0aSnapshot = {
  liveStartedAt: number | null;
  liveEventCount: number;
  supersededCount: number;
  lastRequestedHour: number | null;
};

export type ForecastHourChangeReason = "standard" | "scrub-live" | "scrub-commit";

export type AnchorBatchRequestContext = {
  selectionKey: string;
  generation: number;
  model: string;
  run: string;
  variable: string;
  baseCollection: import("@/lib/anchor-labels").AnchorFeatureCollection;
  points: Array<{ id: string; lat: number; lon: number }>;
  deferToLatest: boolean;
};

// ── Pure helpers ──────────────────────────────────────────────────────

export function emptyScrubPhase0aSnapshot(): ScrubPhase0aSnapshot {
  return {
    liveStartedAt: null,
    liveEventCount: 0,
    supersededCount: 0,
    lastRequestedHour: null,
  };
}

export function viewportSignatureFromState(view: { lat: number; lon: number; z: number }): string {
  const zoomBucket = Math.round(view.z * 2) / 2;
  const latBucket = Math.round(view.lat * 4) / 4;
  const lonBucket = Math.round(view.lon * 4) / 4;
  return `${zoomBucket}|${latBucket}|${lonBucket}`;
}

export function areStringArraysEqual(left: readonly string[], right: readonly string[]): boolean {
  if (left.length !== right.length) {
    return false;
  }
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) {
      return false;
    }
  }
  return true;
}

export function withUpdatedLatestRun(
  capabilities: CapabilitiesResponse | null,
  modelId: string,
  latestRunId: string | null,
  publishedRuns?: string[]
): CapabilitiesResponse | null {
  if (!capabilities) {
    return capabilities;
  }
  const currentAvailability = capabilities.availability?.[modelId];
  if (!currentAvailability) {
    return capabilities;
  }
  const nextPublishedRuns = publishedRuns ?? currentAvailability.published_runs ?? [];
  const latestUnchanged = currentAvailability.latest_run === latestRunId;
  const runsUnchanged = areStringArraysEqual(currentAvailability.published_runs ?? [], nextPublishedRuns);
  if (latestUnchanged && runsUnchanged) {
    return capabilities;
  }
  return {
    ...capabilities,
    availability: {
      ...capabilities.availability,
      [modelId]: {
        ...currentAvailability,
        latest_run: latestRunId,
        published_runs: [...nextPublishedRuns],
      },
    },
  };
}

export function readBasemapModePreference(): BasemapMode {
  if (typeof window === "undefined") {
    return "light";
  }
  try {
    const stored = window.localStorage.getItem(BASEMAP_MODE_STORAGE_KEY);
    return stored === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function writeBasemapModePreference(mode: BasemapMode): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(BASEMAP_MODE_STORAGE_KEY, mode);
  } catch {
    // Ignore storage errors.
  }
}

export const MRMS_DARK_BASEMAP_VARIABLES = new Set(["reflectivity", "mrms_radar_ptype"]);

export function defaultBasemapModeForSelection(model: string, variable: string): BasemapMode {
  if (model === "mrms" && MRMS_DARK_BASEMAP_VARIABLES.has(variable)) {
    return "dark";
  }
  return "light";
}

// The NWS warnings overlay only applies to radar-derived MRMS variables, not the precip ones.
const MRMS_RADAR_VARIABLES = new Set(["reflectivity", "mrms_radar_ptype"]);

export function supportsNwsWarningsOverlay(model: string, variable: string): boolean {
  return model === "mrms" && MRMS_RADAR_VARIABLES.has(variable);
}

export function readLegendVisibilityPreference(): boolean | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const stored = window.localStorage.getItem(LEGEND_VISIBILITY_STORAGE_KEY);
    if (stored === "true") {
      return true;
    }
    if (stored === "false") {
      return false;
    }
    return null;
  } catch {
    return null;
  }
}

export function writeLegendVisibilityPreference(visible: boolean): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(LEGEND_VISIBILITY_STORAGE_KEY, String(visible));
  } catch {
    // Ignore storage errors.
  }
}

function readBooleanPreference(key: string, fallback: boolean): boolean {
  if (typeof window === "undefined") {
    return fallback;
  }
  try {
    const stored = window.localStorage.getItem(key);
    if (stored === "true") {
      return true;
    }
    if (stored === "false") {
      return false;
    }
    return fallback;
  } catch {
    return fallback;
  }
}

function writeBooleanPreference(key: string, value: boolean): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Ignore storage errors.
  }
}

export function readPointLabelsPreference(): boolean {
  return readBooleanPreference(POINT_LABELS_STORAGE_KEY, true);
}

export function writePointLabelsPreference(enabled: boolean): void {
  writeBooleanPreference(POINT_LABELS_STORAGE_KEY, enabled);
}

export function readNwsWarningsPreference(): boolean {
  return readBooleanPreference(NWS_WARNINGS_STORAGE_KEY, true);
}

export function writeNwsWarningsPreference(enabled: boolean): void {
  writeBooleanPreference(NWS_WARNINGS_STORAGE_KEY, enabled);
}

export function buildNwsActiveWarningsUrl(apiRoot: string, versionToken: string): string {
  const baseUrl = `${apiRoot}/api/v4/nws-hazards/active/warnings`;
  const token = String(versionToken ?? "").trim();
  if (!token) {
    return baseUrl;
  }
  return `${baseUrl}?v=${encodeURIComponent(token)}`;
}

export function readZoomControlsPreference(): boolean | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(ZOOM_CONTROLS_STORAGE_KEY);
    if (stored === "true") return true;
    if (stored === "false") return false;
    return null; // never explicitly set
  } catch {
    return null;
  }
}

export function writeZoomControlsPreference(visible: boolean): void {
  writeBooleanPreference(ZOOM_CONTROLS_STORAGE_KEY, visible);
}

export function readRailModePreference(
  breakpointClass: "wide" | "narrow",
): "expanded" | "collapsed" | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(
      breakpointClass === "wide" ? RAIL_MODE_STORAGE_KEY_WIDE : RAIL_MODE_STORAGE_KEY_NARROW,
    );
    if (stored === "expanded" || stored === "collapsed") return stored;
    return null; // never explicitly set
  } catch {
    return null;
  }
}

export function writeRailModePreference(
  breakpointClass: "wide" | "narrow",
  state: "expanded" | "collapsed",
): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      breakpointClass === "wide" ? RAIL_MODE_STORAGE_KEY_WIDE : RAIL_MODE_STORAGE_KEY_NARROW,
      state,
    );
  } catch {
    // Ignore storage errors.
  }
}

export function readAnimationDelayPreference(): number {
  if (typeof window === "undefined") {
    return DEFAULT_ANIMATION_DELAY_MS;
  }
  try {
    const stored = Number(window.localStorage.getItem(ANIMATION_DELAY_STORAGE_KEY));
    return ANIMATION_SPEEDS.some((speed) => speed.delayMs === stored)
      ? stored
      : DEFAULT_ANIMATION_DELAY_MS;
  } catch {
    return DEFAULT_ANIMATION_DELAY_MS;
  }
}

export function writeAnimationDelayPreference(delayMs: number): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(ANIMATION_DELAY_STORAGE_KEY, String(delayMs));
  } catch {
    // Ignore storage errors.
  }
}

function regionPriority(regionId: string): number {
  switch (regionId) {
    case "na":
      return 0;
    case "conus":
      return 1;
    default:
      return 2;
  }
}

function sortRegionIds(regionIds: readonly string[]): string[] {
  return [...regionIds].sort((left, right) => {
    const priorityDelta = regionPriority(left) - regionPriority(right);
    if (priorityDelta !== 0) {
      return priorityDelta;
    }
    return 0;
  });
}

export function pickPreferred(values: string[], preferred: string): string {
  const preferredRegionIds = sortRegionIds(values);
  if (preferredRegionIds.length === 0) {
    return "";
  }
  const normalizedPreferred = String(preferred ?? "").trim().toLowerCase();
  if (normalizedPreferred && preferredRegionIds.includes(normalizedPreferred)) {
    const preferredPriority = regionPriority(normalizedPreferred);
    if (preferredPriority <= regionPriority(preferredRegionIds[0])) {
      return normalizedPreferred;
    }
  }
  return preferredRegionIds[0] ?? "";
}

export function makeRegionLabel(id: string, preset?: RegionPreset): string {
  return preset?.label ?? id.toUpperCase();
}

// Single source of truth for variable dropdown ordering (and label/group overrides).
// Ids not listed here fall back to backend name/group and sort alphabetically after ordered entries.
const VARIABLE_UI_OVERRIDES: Record<string, VariableUiOverride> = {
  tmp2m: { label: "Surface Temp", group: "SURFACE", order: 0 },
  tmp2m_anom: { label: "Surface Temp Anomaly", group: "SURFACE", order: 0.5 },
  dp2m: { label: "Surface Dew Point", group: "SURFACE", order: 1 },
  tmp850: { label: "850mb Temp", group: "UPPER AIR", order: 30 },
  tmp850_anom: { label: "850mb Temp Anomaly", group: "UPPER AIR", order: 30.5 },
  wspd850: { label: "850mb Heights + Winds", group: "UPPER AIR", order: 31 },
  wspd300: { label: "300mb Heights + Winds", group: "UPPER AIR", order: 999 },
  wspd10m: { label: "10m Wind Speed", group: "SURFACE", order: 2 },
  wgst10m: { label: "10m Wind Gusts", group: "SURFACE", order: 3 },
  maxt: { label: "Max Temp", group: "SURFACE", order: 4 },
  mint: { label: "Min Temp", group: "SURFACE", order: 5 },
  wgust_6h_max: { label: "Wind Gust Max (6h)", group: "SURFACE", order: 6 },
  wgust_24h_max: { label: "Wind Gust Max (24h)", group: "SURFACE", order: 7 },
  ptype_intensity: { label: "Precip Type & Intensity", group: "PRECIPITATION", order: 10 },
  radar_ptype: { label: "Composite Reflectivity + Ptype", group: "PRECIPITATION", order: 11 },
  precip_total: { label: "Total Precip", group: "PRECIPITATION", order: 12 },
  qpf_6h: { label: "QPF (6h)", group: "PRECIPITATION", order: 12.1 },
  qpf_24h: { label: "QPF (24h)", group: "PRECIPITATION", order: 12.2 },
  qpf_48h: { label: "QPF (48h)", group: "PRECIPITATION", order: 12.3 },
  precip_5d_anom: { label: "5-day Precip Anomaly", group: "PRECIPITATION", order: 40 },
  precip_7d_anom: { label: "7-day Precip Anomaly", group: "PRECIPITATION", order: 41 },
  precip_10d_anom: { label: "10-day Precip Anomaly", group: "PRECIPITATION", order: 42 },
  precip_15d_anom: { label: "15-day Precip Anomaly", group: "PRECIPITATION", order: 43 },
  precip_16d_anom: { label: "16-day Precip Anomaly", group: "PRECIPITATION", order: 44 },
  snowfall_total: { label: "Total Snowfall (10:1)", group: "PRECIPITATION", order: 13 },
  snow_6h: { label: "Snowfall (6h)", group: "PRECIPITATION", order: 13.1 },
  snow_24h: { label: "Snowfall (24h)", group: "PRECIPITATION", order: 13.2 },
  snow_48h: { label: "Snowfall (48h)", group: "PRECIPITATION", order: 13.3 },
  snowfall_kuchera_total: { label: "Total Snowfall (Kuchera)", group: "PRECIPITATION", order: 14 },
  ice_total: { label: "Total Ice", group: "PRECIPITATION", order: 15 },
  ice_6h: { label: "Ice (6h)", group: "PRECIPITATION", order: 15.1 },
  ice_24h: { label: "Ice (24h)", group: "PRECIPITATION", order: 15.2 },
  pwat: { label: "Precipitable Water", group: "PRECIPITATION", order: 16 },
  mucape: { label: "Most-Unstable CAPE", group: "SEVERE", order: 20 },
  mlcape: { label: "Mixed-Layer CAPE", group: "SEVERE", order: 21 },
  sbcape: { label: "Surface-Based CAPE", group: "SEVERE", order: 22 },
  vort500: { label: "500mb Heights + Vorticity", group: "UPPER AIR", order: 33 },
  hgt500_anom: { label: "500mb Height Anomaly", group: "UPPER AIR", order: 33.5 },
  ir13: { label: "Clean IR", group: "SATELLITE", order: 0 },
  wv9: { label: "Mid-Level Water Vapor", group: "SATELLITE", order: 1 },
  wv8: { label: "Upper-Level Water Vapor", group: "SATELLITE", order: 2 },
  vis2: { label: "Visible", group: "SATELLITE", order: 4 },
  true_color: { label: "True Color", group: "SATELLITE", order: 3 },
  rh2m: { label: "Surface Relative Humidity", group: "PRECIPITATION", order: 16.1 },
  rh700: { label: "700mb Relative Humidity", group: "PRECIPITATION", order: 16.2 },
  qpf6h: { order: 16.3 },
  convective: { label: "SPC Convective Outlook", group: "OUTLOOKS", order: 0 },
  tornado_prob: { label: "SPC Tornado Probability", group: "OUTLOOKS", order: 1 },
  wind_prob: { label: "SPC Wind Probability", group: "OUTLOOKS", order: 2 },
  hail_prob: { label: "SPC Hail Probability", group: "OUTLOOKS", order: 3 },
  extended: { order: 4 },
  cpc_610_temp: { label: "6-10 Day Temp Outlook", group: "FORECASTS", order: 0 },
  cpc_610_precip: { label: "6-10 Day Precip Outlook", group: "FORECASTS", order: 1 },
  cpc_814_temp: { label: "8-14 Day Temp Outlook", group: "FORECASTS", order: 2 },
  cpc_814_precip: { label: "8-14 Day Precip Outlook", group: "FORECASTS", order: 3 },
  cpc_w34_temp: { order: 4 },
  cpc_w34_precip: { order: 5 },
  cpc_1m_temp: { order: 6 },
  cpc_1m_precip: { order: 7 },
  cpc_3m_temp: { order: 8 },
  cpc_3m_precip: { order: 9 },
  mrms_recent_precip_6h: { label: "Recent Precip (6h)", group: "PRECIPITATION", order: 17 },
  mrms_recent_precip_24h: { label: "Recent Precip (24h)", group: "PRECIPITATION", order: 18 },
  mrms_recent_precip_72h: { label: "Recent Precip (72h)", group: "PRECIPITATION", order: 19 },
  reflectivity: { label: "Base Reflectivity", group: "RADAR", order: 0 },
  mrms_radar_ptype: { label: "Reflectivity + Ptype", group: "RADAR", order: 1 },
  active: { label: "Active Hazards", group: "OBSERVATIONS", order: 0 },
};

const FIXED_LEGEND_TITLE_IDS = new Set([
  "precip_5d_anom",
  "precip_7d_anom",
  "precip_10d_anom",
  "precip_15d_anom",
  "precip_16d_anom",
]);

const ENSEMBLE_MODEL_IDS = new Set(["gefs", "eps"]);

function isEnsembleModel(modelId?: string | null): boolean {
  return ENSEMBLE_MODEL_IDS.has(String(modelId ?? "").trim().toLowerCase());
}

function hasMeanSuffix(label: string): boolean {
  return /\(\s*mean\s*\)/i.test(label);
}

function withMeanSuffix(label: string): string {
  const trimmed = label.trim();
  if (!trimmed || hasMeanSuffix(trimmed)) {
    return trimmed;
  }
  return `${trimmed} (Mean)`;
}

const MODEL_UI_OVERRIDES: Record<string, ModelUiOverride> = {
  hrrr: { label: "HRRR", group: "MODELS", order: 0 },
  nam: { label: "NAM", group: "MODELS", order: 1 },
  gfs: { label: "GFS", group: "MODELS", order: 2 },
  nbm: { label: "NBM", group: "MODELS", order: 3 },
  ecmwf: { label: "ECMWF", group: "MODELS", order: 4 },
  aifs: { label: "AIFS", group: "MODELS", order: 5 },
  aigfs: { label: "AIGFS", group: "MODELS", order: 6 },
  gefs: { label: "GEFS", group: "ENSEMBLES", order: 7 },
  eps: { label: "EPS", group: "ENSEMBLES", order: 8 },
  mrms: { label: "MRMS", group: "OBSERVATIONS", order: 10 },
  "goes-east": { label: "Satellite", group: "OBSERVATIONS", order: 11 },
  current_analysis: { label: "Current Analysis", group: "OBSERVATIONS", order: 12 },
  nws_hazards: { label: "NWS Hazards", group: "OBSERVATIONS", order: 13 },
  ndfd: { label: "NDFD", group: "FORECASTS", order: 14 },
  spc: { label: "SPC Outlooks", group: "FORECASTS", order: 15 },
  cpc: { label: "CPC Outlooks", group: "FORECASTS", order: 16 },
  wpc: { label: "WPC", group: "FORECASTS", order: 17 },
};

function variableUiOverride(id: string): VariableUiOverride | null {
  return VARIABLE_UI_OVERRIDES[id] ?? null;
}

function modelUiOverride(id: string): ModelUiOverride | null {
  return MODEL_UI_OVERRIDES[id] ?? null;
}

function canonicalVariableGroup(id: string, group?: string | null): string | null {
  const override = variableUiOverride(id);
  if (override?.group !== undefined) {
    return override.group;
  }

  const normalizedGroup = group?.trim().toLowerCase();
  switch (normalizedGroup) {
    case "anomalies":
    case "precip anomalies":
      return "PRECIP ANOMALIES";
    case "surface":
      return "SURFACE";
    case "temperature":
    case "wind":
      return "SURFACE";
    case "precipitation":
    case "moisture":
      return "PRECIPITATION";
    case "radar & precipitation type":
    case "radar":
      return "RADAR";
    case "severe":
    case "instability":
      return "SEVERE";
    case "upper air":
    case "dynamics":
      return "UPPER AIR";
    case "outlooks":
      return "OUTLOOKS";
    case "forecasts":
      return "FORECASTS";
    case "satellite":
      return "SATELLITE";
    case "hazards":
      return "OBSERVATIONS";
    case "ensemble":
    case "ensembles":
      return "ENSEMBLE";
    default:
      return null;
  }
}

export function viewerVariableGroup(id: string, backendGroup?: string | null): string {
  return canonicalVariableGroup(id, backendGroup) ?? "OBSERVATIONS";
}

export function viewerModelGroup(modelId: string): string {
  return modelUiOverride(modelId)?.group ?? "MODELS";
}

export function variableCatalogOrder(id: string, backendOrder?: number | null): number {
  const override = variableUiOverride(id);
  if (typeof override?.order === "number") {
    return override.order;
  }
  return 999;
}

export function makeVariableLabel(
  id: string,
  preferredLabel?: string | null,
  modelId?: string | null,
  options?: { appendEnsembleMeanSuffix?: boolean },
): string {
  const appendSuffix = options?.appendEnsembleMeanSuffix ?? true;
  const override = variableUiOverride(id);
  const apiLabel = preferredLabel?.trim() ?? "";

  if (appendSuffix && isEnsembleModel(modelId)) {
    if (apiLabel && hasMeanSuffix(apiLabel)) {
      return apiLabel;
    }
    if (override?.label) {
      return withMeanSuffix(override.label);
    }
    if (apiLabel) {
      return withMeanSuffix(apiLabel);
    }
    return id;
  }

  if (override?.label) {
    return override.label;
  }
  if (apiLabel) {
    return apiLabel;
  }
  return id;
}

function makeLegendTitle(id: string, preferredTitle?: string | null, modelId?: string | null): string {
  if (FIXED_LEGEND_TITLE_IDS.has(id) && preferredTitle && preferredTitle.trim()) {
    if (isEnsembleModel(modelId)) {
      return makeVariableLabel(id, preferredTitle, modelId);
    }
    return preferredTitle.trim();
  }
  return makeVariableLabel(id, preferredTitle, modelId);
}

export function buildFallbackSharePayload(params: {
  modelLabel: string;
  runLabel: string;
  variableId?: string | null;
  variableLabel: string;
  forecastHour: number;
  timeAxisMode: TimeAxisMode;
  runTimeISO?: string | null;
  validTimeISO?: string | null;
  permalink: string;
}): SharePayload {
  const timeLabel = params.timeAxisMode === "observed"
    ? (params.validTimeISO ? `Observed ${formatObservedCompactTime(params.validTimeISO) ?? params.validTimeISO}` : "Observed time n/a")
    : params.timeAxisMode === "valid"
      ? (
        params.validTimeISO
          ? `${validAxisLabel(params.forecastHour, params.variableId, params.runTimeISO, params.validTimeISO)} • ${formatValidTime(params.validTimeISO, params.variableId) ?? params.validTimeISO}`
          : validAxisLabel(params.forecastHour, params.variableId, params.runTimeISO, params.validTimeISO)
      )
      : (Number.isFinite(params.forecastHour)
        ? `FH ${Math.max(0, Math.round(params.forecastHour))}`
        : "FH n/a");
  const summary = [params.modelLabel, params.runLabel, timeLabel, params.variableLabel]
    .map((part) => part.trim())
    .filter(Boolean)
    .join(" • ");
  return {
    permalink: params.permalink,
    summary: summary || "CartoSky viewer share",
    detailsSummary: "",
  };
}

export function toNumberOrNull(value: unknown): number | null {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

export function variableDefaultFh(entry?: CapabilityVariable | null): number | null {
  const defaultFh = toNumberOrNull(entry?.default_fh);
  if (defaultFh !== null) {
    return defaultFh;
  }
  const minFh = toNumberOrNull(entry?.constraints?.min_fh);
  if (minFh !== null) {
    return minFh;
  }
  return null;
}

export function modelOrderById(id: string): number | null {
  const normalized = id.trim().toLowerCase();
  return Number.isFinite(MODEL_ORDER_BY_ID[normalized]) ? MODEL_ORDER_BY_ID[normalized] : null;
}

export function normalizeModelRows(
  capabilities: CapabilitiesResponse | null | undefined,
  modelIds: string[]
): ModelEntry[] {
  if (!capabilities?.model_catalog || modelIds.length === 0) {
    return [];
  }

  const normalized: ModelEntry[] = [];
  for (const id of modelIds) {
    const normalizedId = String(id).trim();
    const capability = capabilities.model_catalog[normalizedId];
    if (!normalizedId || !capability) {
      continue;
    }
    normalized.push({
      id: normalizedId,
      displayName: capability.name?.trim() || undefined,
      order: modelOrderById(normalizedId),
    });
  }

  return normalized.sort((a, b) => {
    const aOverride = modelUiOverride(a.id);
    const bOverride = modelUiOverride(b.id);
    const aOrder = typeof aOverride?.order === "number"
      ? aOverride.order
      : (Number.isFinite(a.order) ? Number(a.order) : Number.POSITIVE_INFINITY);
    const bOrder = typeof bOverride?.order === "number"
      ? bOverride.order
      : (Number.isFinite(b.order) ? Number(b.order) : Number.POSITIVE_INFINITY);
    if (aOrder !== bOrder) {
      return aOrder - bOrder;
    }
    return a.id.localeCompare(b.id);
  });
}

export function makeModelOptions(entries: ModelEntry[]): GroupedOption[] {
  return entries.map((entry) => {
    const override = modelUiOverride(entry.id);
    return {
      value: entry.id,
      label: override?.label ?? entry.displayName ?? entry.id,
      group: override?.group ?? null,
    };
  });
}

function bboxWithin(parent: RegionPreset | undefined, child: RegionPreset | undefined): boolean {
  if (!parent || !child) {
    return true;
  }
  const [parentWest, parentSouth, parentEast, parentNorth] = parent.bbox;
  const [childWest, childSouth, childEast, childNorth] = child.bbox;
  return (
    childWest >= parentWest
    && childSouth >= parentSouth
    && childEast <= parentEast
    && childNorth <= parentNorth
  );
}

export function filterRegionOptionsByCoverage(
  regionPresets: Record<string, RegionPreset>,
  canonicalRegionId: string | null | undefined,
): Option[] {
  const regionIds = Object.keys(regionPresets);
  const canonicalRegion = String(canonicalRegionId ?? "").trim().toLowerCase();
  const canonicalPreset = canonicalRegion ? regionPresets[canonicalRegion] : undefined;
  const allowedRegionIds = canonicalPreset
    ? regionIds.filter((regionId) => bboxWithin(canonicalPreset, regionPresets[regionId]))
    : regionIds;
  return sortRegionIds(allowedRegionIds).map((id) => ({
    value: id,
    label: makeRegionLabel(id, regionPresets[id]),
  }));
}

/**
 * Camera-preset options for the ACTIVE data domain (Phase 3, §5).
 *
 * A non-canonical effective data domain (e.g. `global`) covers every camera
 * preset, so no coverage filtering applies. Canonical selections (dataDomain
 * `null`) keep the canonical-region coverage filter. Build-region declarations
 * (`supported_build_regions`) never constrain the camera list — they describe
 * which domains can be *requested*, not where the camera may point.
 */
export function filterRegionOptionsForDataDomain(
  regionPresets: Record<string, RegionPreset>,
  canonicalRegionId: string | null | undefined,
  dataDomain: string | null | undefined,
): Option[] {
  const domain = String(dataDomain ?? "").trim().toLowerCase();
  return filterRegionOptionsByCoverage(regionPresets, domain ? null : canonicalRegionId);
}

/**
 * Resolve the effective data domain for a selection (Phase 2B, max-week plan).
 *
 * Returns the requested published build-region ID when the variable declares
 * it in `supported_build_regions`, and `null` — meaning "canonical, send no
 * `domain=`" — in every other case. Degrading is silent by design: an
 * unsupported sticky `domain=` in the URL keeps issuing canonical requests
 * rather than erroring, mirroring variable-stickiness behavior.
 */
export function resolveDataDomain(
  requestedDomain: string | null | undefined,
  modelCapability: CapabilityModel | null | undefined,
  variableCapability: CapabilityVariable | null | undefined,
): string | null {
  const requested = String(requestedDomain ?? "").trim().toLowerCase();
  if (!requested) {
    return null;
  }
  const canonicalRegion = String(
    modelCapability?.constraints?.canonical_region
    ?? modelCapability?.canonical_region
    ?? "",
  ).trim().toLowerCase();
  if (requested === canonicalRegion) {
    return null;
  }
  const supported = Array.isArray(variableCapability?.supported_build_regions)
    ? variableCapability.supported_build_regions.map((regionId) => String(regionId ?? "").trim().toLowerCase())
    : [];
  return supported.includes(requested) ? requested : null;
}

/**
 * Outcome of probing the DOMAIN-SCOPED run manifest for the selected variable.
 *
 * - `disabled` — no non-canonical domain is in play, so nothing was probed.
 * - `pending`  — the probe is in flight; the answer is not yet known.
 * - `present`  — the domain manifest carries the variable with a non-empty
 *                frame list.
 * - `absent`   — DEFINITIVE negative: the manifest loaded and the variable is
 *                missing, or it is declared with NOTHING built, or the domain
 *                manifest 404s for this run. See
 *                `domainRunProbeStatusForManifest` for the exact rule.
 * - `error`    — transient failure (network down, 5xx, unparseable payload).
 *                NOT a negative: an unreachable API is not "unpublished".
 */
export type DomainRunProbeStatus = "disabled" | "pending" | "present" | "absent" | "error";

export type RefinedDataDomain = {
  /** The domain that REQUESTS must use. `null` = canonical (send no `domain=`). */
  domain: string | null;
  /**
   * The answer is not known yet. Callers must HOLD (stay in loading) rather
   * than issue requests — degrading preemptively would flash canonical data
   * and then swap, which is exactly the flicker this layer exists to avoid.
   */
  indeterminate: boolean;
  /** The run-scoped degrade fired — drives the third `coverageDegradedNote` variant. */
  runDegraded: boolean;
};

/**
 * Run-scoped coverage degradation — the third layer, applied DOWNSTREAM of the
 * capability layer (`resolveDataDomain`).
 *
 * Capabilities answer "may this domain be requested for this variable"; they
 * cannot answer "does this RUN actually have it". A capability flip that lands
 * before the artifacts do (Wave-1 anomaly baselines), every pre-baseline run,
 * and every mid-cycle window before a variable's frames publish all produce a
 * capability-supported domain with nothing behind it — a blank map with no
 * explanation. This selector turns that into the same silent degrade the other
 * two layers already perform, plus a run-scoped note.
 *
 * Decision table (`effectiveDomain` is `resolveDataDomain`'s output):
 *
 *   effectiveDomain | status    | domain | indeterminate | runDegraded
 *   ----------------+-----------+--------+---------------+------------
 *   null            | (any)     | null   | false         | false
 *   "global"        | disabled  | global | false         | false
 *   "global"        | pending   | global | TRUE          | false
 *   "global"        | present   | global | false         | false
 *   "global"        | absent    | null   | false         | TRUE
 *   "global"        | error     | global | false         | false
 *
 * `null` in never degrades further: a capability-unsupported domain already
 * shows the "this variable"/"this model" note and must not be re-attributed to
 * the run.
 */
/**
 * Classify one variable inside an already-loaded DOMAIN-SCOPED run manifest.
 *
 * Prod (verified against api.cartosky.com) publishes a Wave-1 anomaly that is
 * declared-but-unbuilt in exactly this shape:
 *
 *   { expected_frames: 105, available_frames: 0, ready_through_fh: null,
 *     expected_max_fh: 384, frames: [] }
 *
 * — present in the variables map, zero artifacts behind it. That is the
 * operator's actual repro, and it must degrade.
 *
 * The rule is deliberately asymmetric: ANY positive signal means present, and
 * `absent` requires every signal that exists to read zero. So a mid-cycle
 * variable with SOME frames (`available_frames > 0`, a `ready_through_fh`) is
 * a normal in-progress build — the viewer shows partial global data with the
 * readiness hatch, exactly as it does canonically, and must NOT be yanked to
 * canonical halfway through a cycle. Likewise a legacy manifest entry carrying
 * neither counter nor frame list is unknown, not negative, so it stays
 * `present`.
 */
export function domainRunProbeStatusForManifest(
  manifest: RunManifestResponse | null | undefined,
  requestVariable: string,
): "present" | "absent" {
  const varKey = String(requestVariable ?? "").trim();
  const entry = varKey ? manifest?.variables?.[varKey] : undefined;
  if (!entry) {
    // Absent from the variables map entirely — a domain manifest that lists
    // only what it built (today's prod shape for non-declared variables).
    return "absent";
  }
  const availableFrames = Number(entry.available_frames);
  if (Number.isFinite(availableFrames) && availableFrames > 0) {
    return "present";
  }
  if (Array.isArray(entry.frames) && entry.frames.length > 0) {
    return "present";
  }
  const sawZeroCounter = Number.isFinite(availableFrames) && availableFrames === 0;
  const sawEmptyFrameList = Array.isArray(entry.frames) && entry.frames.length === 0;
  return sawZeroCounter || sawEmptyFrameList ? "absent" : "present";
}

export function refineDataDomainForRun(
  effectiveDomain: string | null | undefined,
  probeStatus: DomainRunProbeStatus,
): RefinedDataDomain {
  const domain = String(effectiveDomain ?? "").trim().toLowerCase() || null;
  if (!domain) {
    return { domain: null, indeterminate: false, runDegraded: false };
  }
  if (probeStatus === "pending") {
    return { domain, indeterminate: true, runDegraded: false };
  }
  if (probeStatus === "absent") {
    return { domain: null, indeterminate: false, runDegraded: true };
  }
  // "present", "error" and "disabled" all keep the requested domain: only a
  // definitive negative may lock a selection into canonical.
  return { domain, indeterminate: false, runDegraded: false };
}

/**
 * THE single flip point for global-by-default (global go-live design §7).
 *
 * Returns the data domain a selection uses when the URL carries no `domain=`
 * param — i.e. what ABSENCE means. It returns `null` (canonical) today;
 * flipping global-by-default is changing THIS function and nothing else. No
 * other call site may hardcode the "absent param → canonical" assumption for
 * DEFAULTING purposes — they must route through here so the flip stays a
 * one-line change.
 *
 * Because absence means "the default", an explicit selection that differs from
 * the default has to be spelled out in the URL — including a canonical one
 * (`domain=na`). `normalizeRequestedDomain` below owns that collapse rule, and
 * is the only other function that has to be correct for the flip to be safe.
 *
 * (`resolveDataDomain` is the separate, unrelated question of whether an
 * already-chosen domain is *supported* by a selection.)
 */
export function defaultDataDomainForSelection(
  modelCapability: CapabilityModel | null | undefined,
  variableCapability: CapabilityVariable | null | undefined,
): string | null {
  void modelCapability;
  void variableCapability;
  return null;
}

/**
 * Canonical form of a requested domain: `null` means "the model's canonical
 * domain". A canonical region id and `null` are the same request.
 */
function canonicalizeRequestedDomain(
  requestedDomain: string | null | undefined,
  canonicalRegionId: string,
): string | null {
  const requested = String(requestedDomain ?? "").trim().toLowerCase();
  if (!requested || requested === canonicalRegionId) {
    return null;
  }
  return requested;
}

/**
 * Collapse an EXPLICIT coverage selection to the domain state that should be
 * stored (and therefore serialized into `domain=`) — design §7.
 *
 * `null` state means "unset, use the default", so a selection is storable as
 * `null` only when it *is* the default. A selection that differs must be kept
 * verbatim, and a canonical selection that differs from a non-canonical
 * default must be spelled out as the canonical region id — otherwise absence
 * would re-resolve to the default and silently revert the user's choice on
 * every reload or shared link.
 *
 * Today (default `null`): canonical selections collapse to `null`, so the URL
 * is byte-identical to the pre-control behavior. After the flip (default
 * `"global"`): a canonical selection serializes as `domain=na` and survives a
 * reload; an explicit global selection collapses to no param.
 *
 * `domain=na` is safe on the wire — the backend's `normalize_domain` treats a
 * canonical id as no domain, and `resolveDataDomain` returns `null` for it, so
 * requests stay byte-identical canonical ones.
 */
export function normalizeRequestedDomain(
  requestedDomain: string | null | undefined,
  modelCapability: CapabilityModel | null | undefined,
  variableCapability: CapabilityVariable | null | undefined,
): string | null {
  return normalizeRequestedDomainAgainstDefault(
    requestedDomain,
    canonicalRegionIdForModel(modelCapability),
    defaultDataDomainForSelection(modelCapability, variableCapability),
  );
}

/**
 * The collapse rule itself, with the default injected. Exported so the flip
 * can be PROVEN safe: the unit suite runs the full serialization matrix in
 * both the `null` (today) and `"global"` (post-flip) worlds without having to
 * mutate the flip point.
 */
export function normalizeRequestedDomainAgainstDefault(
  requestedDomain: string | null | undefined,
  canonicalRegionId: string,
  defaultDomain: string | null | undefined,
): string | null {
  const canonical = String(canonicalRegionId ?? "").trim().toLowerCase();
  const selection = canonicalizeRequestedDomain(requestedDomain, canonical);
  const fallback = canonicalizeRequestedDomain(defaultDomain, canonical);
  if (selection === fallback) {
    // Representable by absence.
    return null;
  }
  // Differs from the default, so it has to be explicit. A canonical selection
  // is spelled out with the canonical region id.
  return selection ?? (canonical || null);
}

/**
 * The coverage segment a given domain state should show as selected — design
 * §1/§7. `null` state resolves through the default, so the toggle reflects
 * what the user is actually getting, both before and after the flip.
 *
 * Returns a concrete region id (never ""), so a sticky-but-unknown domain
 * (`?domain=mars`) matches NO segment and leaves the toggle unchecked while
 * the degraded note explains it.
 */
export function coverageSelectionValue(
  requestedDomain: string | null | undefined,
  modelCapability: CapabilityModel | null | undefined,
  variableCapability: CapabilityVariable | null | undefined,
): string {
  return coverageSelectionValueAgainstDefault(
    requestedDomain,
    canonicalRegionIdForModel(modelCapability),
    defaultDataDomainForSelection(modelCapability, variableCapability),
  );
}

/** `coverageSelectionValue` with the default injected — see the sibling above. */
export function coverageSelectionValueAgainstDefault(
  requestedDomain: string | null | undefined,
  canonicalRegionId: string,
  defaultDomain: string | null | undefined,
): string {
  const requested = String(requestedDomain ?? "").trim().toLowerCase();
  if (requested) {
    return requested;
  }
  return String(defaultDomain ?? "").trim().toLowerCase()
    || String(canonicalRegionId ?? "").trim().toLowerCase();
}

export function canonicalRegionIdForModel(modelCapability: CapabilityModel | null | undefined): string {
  return String(
    modelCapability?.constraints?.canonical_region
    ?? modelCapability?.canonical_region
    ?? "",
  ).trim().toLowerCase();
}

/**
 * Human label for a data-domain segment. Camera presets carry labels
 * (`na` → "North America"); build regions without a preset (`global`) fall
 * back to a title-cased id, so a future domain needs no UI change.
 */
export function coverageSegmentLabel(
  regionId: string,
  regionPresets?: Record<string, RegionPreset> | null,
): string {
  const id = String(regionId ?? "").trim().toLowerCase();
  if (!id) {
    return "";
  }
  const presetLabel = regionPresets?.[id]?.label;
  if (presetLabel) {
    return presetLabel;
  }
  return id.charAt(0).toUpperCase() + id.slice(1);
}

export type CoverageSegment = {
  /**
   * A concrete build-region id — the canonical segment carries the canonical
   * region id (`na`), NOT "". Absence of `domain=` means "the default", which
   * is not necessarily canonical after the §7 flip, so the segment set cannot
   * use "" to stand for canonical.
   */
  value: string;
  label: string;
};

/**
 * Coverage (data-domain) segments for a model — global go-live design §1.
 *
 * The non-canonical segment set is the UNION of every buildable variable's
 * `supported_build_regions`, minus the canonical region, so a model that gains
 * a new domain needs no UI change. Returns `[]` when the model declares no
 * non-canonical build region at all: canonical-only models render no control.
 */
export function coverageSegmentsForModel(
  modelCapability: CapabilityModel | null | undefined,
  regionPresets?: Record<string, RegionPreset> | null,
): CoverageSegment[] {
  const canonical = canonicalRegionIdForModel(modelCapability);
  const extras: string[] = [];
  for (const entry of normalizeCapabilityVarRows(modelCapability)) {
    for (const regionId of entry.supportedBuildRegions ?? []) {
      if (regionId && regionId !== canonical && !extras.includes(regionId)) {
        extras.push(regionId);
      }
    }
  }
  if (extras.length === 0) {
    return [];
  }
  extras.sort();
  return [
    { value: canonical, label: coverageSegmentLabel(canonical, regionPresets) || "Canonical" },
    ...extras.map((regionId) => ({ value: regionId, label: coverageSegmentLabel(regionId, regionPresets) })),
  ];
}

/**
 * Degraded-coverage note (design §2 / decision U2). The toggle stays on the
 * requested segment (mirroring URL stickiness) and this note states what is
 * actually being shown — exactly when a domain was requested but
 * `resolveDataDomain` degraded it to canonical.
 *
 * Three causes, three sentences: the MODEL may not offer the requested
 * coverage at all (a sticky `?domain=mars`, or a domain carried across a model
 * switch); the model offers it and the selected VARIABLE does not; or both
 * declare it and THIS RUN has no artifacts for it (`runDegraded`, from
 * `refineDataDomainForRun`). Attributing any of these to the others would be a
 * lie — "this variable" on a capability-supported variable would send the
 * operator hunting for a variable problem that does not exist.
 */
export function coverageDegradedNote(
  requestedDomain: string | null | undefined,
  modelCapability: CapabilityModel | null | undefined,
  variableCapability: CapabilityVariable | null | undefined,
  regionPresets?: Record<string, RegionPreset> | null,
  runDegraded: boolean = false,
): string | null {
  const requested = String(requestedDomain ?? "").trim().toLowerCase();
  if (!requested) {
    return null;
  }
  const canonical = canonicalRegionIdForModel(modelCapability);
  if (requested === canonical) {
    return null;
  }
  const canonicalLabel = coverageSegmentLabel(canonical, regionPresets) || "the canonical coverage";
  if (resolveDataDomain(requested, modelCapability, variableCapability) !== null) {
    // Capability-supported: the only remaining degrade cause is the run.
    return runDegraded ? `Not available for this run — showing ${canonicalLabel}` : null;
  }
  const modelOffersRequested = coverageSegmentsForModel(modelCapability, regionPresets)
    .some((segment) => segment.value === requested);
  const subject = modelOffersRequested ? "this variable" : "this model";
  return `Not available for ${subject} — showing ${canonicalLabel}`;
}

/** Short chip label for the canonical domain, used on variable rows (§3). */
export function coverageBadgeLabel(modelCapability: CapabilityModel | null | undefined): string {
  return canonicalRegionIdForModel(modelCapability).toUpperCase();
}

/**
 * Variable ids that do NOT declare the requested (non-canonical) domain —
 * design §3. Empty whenever coverage is canonical: no badges exist then.
 * Rows are badged, never hidden or disabled; selecting one degrades per §2.
 */
export function variableIdsMissingDomain(
  entries: readonly VariableEntry[],
  requestedDomain: string | null | undefined,
  modelCapability: CapabilityModel | null | undefined,
): string[] {
  const requested = String(requestedDomain ?? "").trim().toLowerCase();
  if (!requested || requested === canonicalRegionIdForModel(modelCapability)) {
    return [];
  }
  return entries
    .filter((entry) => !(entry.supportedBuildRegions ?? []).includes(requested))
    .map((entry) => entry.id);
}

export function normalizeCapabilityVarRows(modelCapability: CapabilityModel | null | undefined): VariableEntry[] {
  if (!modelCapability?.variables) {
    return [];
  }
  const normalized: VariableEntry[] = Object.entries(modelCapability.variables)
    .map(([id, entry]) => ({
      id: String(id).trim(),
      displayName: entry.display_name?.trim() || undefined,
      defaultFh: variableDefaultFh(entry),
      buildable: entry.buildable !== false,
      kind: typeof entry.kind === "string" ? entry.kind : null,
      displayResamplingOverride:
        typeof entry.display_resampling_override === "string" ? entry.display_resampling_override : null,
      group: typeof entry.group === "string" ? entry.group : null,
      renderSubstrates: readCapabilityRenderSubstrates(entry),
      supportedBuildRegions: Array.isArray(entry.supported_build_regions)
        ? entry.supported_build_regions
          .map((regionId) => String(regionId ?? "").trim().toLowerCase())
          .filter((regionId, index, items) => Boolean(regionId) && items.indexOf(regionId) === index)
        : undefined,
      hasStats: (() => {
        const products = (entry as { ensemble?: Record<string, unknown> }).ensemble?.products;
        return Array.isArray(products) && products.length > 1;
      })(),
    }))
    .filter((entry) => Boolean(entry.id) && entry.buildable);

  return normalized.sort((a, b) => a.id.localeCompare(b.id));
}

export function capabilityVarsForManifest(
  manifestVars: RunManifestResponse["variables"] | null | undefined,
  capabilityVars: VariableEntry[],
  options?: { modelId?: string | null; domainScoped?: boolean },
): VariableEntry[] {
  // A DOMAIN-SCOPED manifest lists only what that domain built (prod's
  // `?domain=global` manifest carries 22 variables and omits every
  // canonical-only one). It answers "what does this DOMAIN have", never "what
  // does this MODEL have", so it must not prune the picker: design U2 says
  // rows are badged, never hidden or disabled. Selecting a canonical-only row
  // under Global degrades through the existing capability path.
  if (options?.domainScoped) {
    const capabilityIds = new Set(capabilityVars.map((entry) => entry.id));
    const extras = normalizeManifestVarRows(manifestVars).filter((entry) => !capabilityIds.has(entry.id));
    return [...capabilityVars, ...extras];
  }
  // MRMS advances LATEST as soon as the fast radar phase publishes, while
  // hourly recent-precip products refresh asynchronously. Keep its stable
  // capability catalog visible during that transition; the grid loader can
  // resolve a carried-forward/previous usable run for the selected variable.
  if (options?.modelId === "goes-east" || options?.modelId === "mrms") {
    return capabilityVars;
  }
  if (!manifestVars) {
    return capabilityVars;
  }
  const manifestKeys = Object.keys(manifestVars);
  if (manifestKeys.length === 0) {
    return [];
  }
  const manifestSet = new Set(manifestKeys);
  // Non-grid variables (e.g. raster_rgb / image substrate) have their own
  // manifest path and are never present in the scalar run manifest. Always
  // preserve them so they aren't filtered out when the scalar scheduler
  // publishes a run that lacks their variable key.
  const isNonGridVar = (entry: VariableEntry): boolean =>
    Array.isArray(entry.renderSubstrates)
    && entry.renderSubstrates.length > 0
    && !entry.renderSubstrates.includes("grid")
    && !entry.renderSubstrates.includes("vector");
  const known = capabilityVars.filter(
    (entry) => manifestSet.has(entry.id) || isNonGridVar(entry)
  );
  const knownSet = new Set(known.map((entry) => entry.id));
  const extras = normalizeManifestVarRows(manifestVars).filter((entry) => !knownSet.has(entry.id));
  return [...known, ...extras];
}

export function normalizeManifestVarRows(
  variables: RunManifestResponse["variables"] | null | undefined
): VariableEntry[] {
  if (!variables) {
    return [];
  }
  const normalized: VariableEntry[] = [];
  for (const [id, entry] of Object.entries(variables)) {
    const normalizedId = String(id ?? "").trim();
    if (!normalizedId) {
      continue;
    }
    if (normalizedId === "precip_ptype") {
      continue;
    }
    const displayName = entry?.display_name ?? entry?.name ?? entry?.label;
    normalized.push({ id: normalizedId, displayName: displayName?.trim() || undefined });
  }
  return normalized;
}

export function makeVariableOptions(entries: VariableEntry[], modelId?: string | null): VariableOption[] {
  return entries
    .map((entry, index) => {
      const override = variableUiOverride(entry.id);
      return {
        value: entry.id,
        label: makeVariableLabel(entry.id, entry.displayName, modelId, { appendEnsembleMeanSuffix: false }),
        group: canonicalVariableGroup(entry.id, entry.group),
        sortOrder: typeof override?.order === "number" ? override.order : (1000 + index),
        hasStats: Boolean(entry.hasStats),
      };
    })
    .sort((a, b) => {
      if (a.sortOrder !== b.sortOrder) {
        return a.sortOrder - b.sortOrder;
      }
      return a.label.localeCompare(b.label);
    })
    .map(({ sortOrder: _sortOrder, ...option }) => option);
}

export function resolveManifestFrames(
  manifest: RunManifestResponse | null | undefined,
  varKey: string
): { rows: FrameRow[]; hasFrameList: boolean } {
  if (!manifest || !varKey) {
    return { rows: [], hasFrameList: false };
  }
  const varEntry = manifest.variables?.[varKey];
  if (!varEntry || !Array.isArray(varEntry.frames)) {
    return { rows: [], hasFrameList: false };
  }

  const rows: FrameRow[] = [];
  const manifestGeneratedAt = typeof manifest.last_updated === "string" && manifest.last_updated.trim() ? manifest.last_updated.trim() : undefined;
  for (const frame of varEntry.frames) {
    const fh = Number(frame?.fh);
    if (!Number.isFinite(fh)) {
      continue;
    }
    const validTime = typeof frame?.valid_time === "string" && frame.valid_time.trim() ? frame.valid_time.trim() : undefined;
    const generatedAt = typeof frame?.generated_at === "string" && frame.generated_at.trim()
      ? frame.generated_at.trim()
      : manifestGeneratedAt;
    rows.push({
      fh,
      has_cog: false,
      run: manifest.run,
      valid_time: validTime,
      meta: validTime || generatedAt ? { meta: { valid_time: validTime, generated_at: generatedAt } } : undefined,
    });
  }
  rows.sort((a, b) => Number(a.fh) - Number(b.fh));
  return { rows, hasFrameList: true };
}

export function inferRunTargetMaxForecastHour(
  modelId: string,
  runId: string | null | undefined
): number | null {
  const parsedRun = parseRunId(runId);
  const cycleHour = parsedRun?.getUTCHours() ?? null;

  switch (modelId) {
    case "aigfs":
      return 384;
    case "gefs":
    case "eps":
    case "aifs":
    case "ecmwf":
      return 360;
    case "gfs":
      return 384;
    case "nam":
      return 60;
    case "hrrr":
      return cycleHour !== null && [0, 6, 12, 18].includes(cycleHour) ? 48 : 18;
    case "nbm":
      return cycleHour !== null && [0, 6, 12, 18].includes(cycleHour) ? 264 : 261;
    default:
      return null;
  }
}

function manifestVariableFrameCounts(varEntry: RunManifestResponse["variables"][string] | undefined): {
  expected: number;
  available: number;
} | null {
  if (!varEntry || typeof varEntry !== "object") {
    return null;
  }

  const expectedRaw = varEntry.expected_frames;
  const availableRaw = varEntry.available_frames;
  let expected: number | null = typeof expectedRaw === "number" && Number.isInteger(expectedRaw) ? expectedRaw : null;
  let available: number | null = typeof availableRaw === "number" && Number.isInteger(availableRaw) ? availableRaw : null;

  if (expected === null) {
    const frames = varEntry.frames;
    if (Array.isArray(frames)) {
      expected = frames.length;
      available = frames.length;
    } else {
      return null;
    }
  }

  if (available === null) {
    const frames = varEntry.frames;
    if (Array.isArray(frames)) {
      available = frames.length;
    } else {
      return null;
    }
  }

  return { expected, available };
}

export function isManifestRunComplete(manifest: RunManifestResponse | null | undefined): boolean {
  const variables = manifest?.variables;
  if (!variables || typeof variables !== "object") {
    return false;
  }

  let sawExpected = false;
  for (const varEntry of Object.values(variables)) {
    const counts = manifestVariableFrameCounts(varEntry);
    if (!counts) {
      return false;
    }
    sawExpected = sawExpected || counts.expected > 0;
    if (counts.available < counts.expected) {
      return false;
    }
  }

  return sawExpected;
}

export type HistoricalRunIncompleteStatus = {
  label: string;
  description: string;
  tone: "delayed";
};

export function resolveHistoricalRunIncompleteStatus(params: {
  manifest: RunManifestResponse | null | undefined;
  modelId: string;
  runId: string;
  variableId: string;
  variableLabel: string;
  variableMaxFh: number | null;
  selectableMaxForecastHour: number | null;
  runLabel: string;
}): HistoricalRunIncompleteStatus | null {
  const {
    manifest,
    modelId,
    runId,
    variableId,
    variableLabel,
    variableMaxFh,
    selectableMaxForecastHour,
    runLabel,
  } = params;

  if (!manifest || isManifestRunComplete(manifest)) {
    return null;
  }

  const varEntry = manifest.variables?.[variableId];
  const manifestFrames = Array.isArray(varEntry?.frames) ? varEntry.frames : [];
  const manifestMaxForecastHour = manifestFrames.length > 0
    ? Math.max(...manifestFrames.map((frame) => Number(frame?.fh)).filter(Number.isFinite))
    : null;
  const targetMaxForecastHour =
    inferRunTargetMaxForecastHour(modelId, runId)
    ?? variableMaxFh
    ?? manifestMaxForecastHour;
  const availableMaxForecastHour = selectableMaxForecastHour ?? manifestMaxForecastHour;

  if (
    targetMaxForecastHour !== null
    && Number.isFinite(targetMaxForecastHour)
    && availableMaxForecastHour !== null
    && Number.isFinite(availableMaxForecastHour)
  ) {
    const cappedAvailable = Math.max(
      0,
      Math.min(availableMaxForecastHour, targetMaxForecastHour)
    );
    return {
      label: "Not complete",
      description: `${variableLabel} · ${runLabel} · ${cappedAvailable}/${targetMaxForecastHour} forecast hours published`,
      tone: "delayed",
    };
  }

  return {
    label: "Not complete",
    description: `${variableLabel} · ${runLabel} · run build did not finish`,
    tone: "delayed",
  };
}

export function mergeManifestRowsWithPrevious(
  manifestRows: FrameRow[],
  previousRows: FrameRow[],
  allowCarryForward = true
): FrameRow[] {
  if (!allowCarryForward || manifestRows.length === 0 || previousRows.length === 0) {
    return manifestRows;
  }

  const previousByHour = new Map<number, FrameRow>();
  for (const row of previousRows) {
    const fh = Number(row.fh);
    if (Number.isFinite(fh)) {
      previousByHour.set(fh, row);
    }
  }

  return manifestRows.map((row) => {
    const previous = previousByHour.get(Number(row.fh));
    if (!previous) {
      return row;
    }
    return {
      ...row,
      meta: row.meta && previous.meta
        ? { meta: { ...(previous.meta.meta ?? {}), ...(row.meta.meta ?? {}) } }
        : row.meta ?? previous.meta,
    };
  });
}

export function extractLegendMeta(row: FrameRow | null | undefined): LegendMeta | null {
  const rawMeta = row?.meta?.meta ?? null;
  if (!rawMeta) return null;
  const nested = (rawMeta as { meta?: LegendMeta | null }).meta;
  return nested ?? (rawMeta as LegendMeta);
}

/**
 * Resolve a (possibly relative) grid frame URL to an absolute one against the
 * API origin. Shared by ComparePanel (split mode) and compare.tsx's diff
 * pipeline: both must produce byte-identical strings because the diff
 * GridFrameCache and in-flight dedup are keyed on the exact URL. `API_ORIGIN`
 * is already trailing-slash-stripped at its source in config.
 */
export function toAbsoluteGridFrameUrl(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `${API_ORIGIN}${url.startsWith("/") ? "" : "/"}${url}`;
}

export function nearestFrame(frames: number[], current: number): number {
  if (frames.length === 0) return 0;
  if (frames.includes(current)) return current;
  return frames.reduce((nearest, value) => {
    const nearestDelta = Math.abs(nearest - current);
    const valueDelta = Math.abs(value - current);
    return valueDelta < nearestDelta || (valueDelta === nearestDelta && value > nearest) ? value : nearest;
  }, frames[0]);
}

/** First frame >= current; clamps to max when current is beyond the schedule. */
export function ceilingFrame(frames: number[], current: number): number {
  if (frames.length === 0) return 0;
  if (frames.includes(current)) return current;
  for (const value of frames) {
    if (value >= current) {
      return value;
    }
  }
  return frames[frames.length - 1];
}

export function resolveLoopPlaybackStartHour(
  frameHours: number[],
  currentHour: number | null | undefined,
): number | null {
  if (frameHours.length === 0) {
    return Number.isFinite(currentHour) ? Number(currentHour) : null;
  }
  if (frameHours.length === 1) {
    return frameHours[0];
  }
  const resolvedHour = Number.isFinite(currentHour)
    ? nearestFrame(frameHours, Number(currentHour))
    : frameHours[0];
  const currentIndex = frameHours.indexOf(resolvedHour);
  if (currentIndex >= frameHours.length - 1) {
    return frameHours[0] ?? null;
  }
  return resolvedHour;
}

export function resolveLoopPlaybackNextHour(
  frameHours: number[],
  currentHour: number,
): number | null {
  if (frameHours.length === 0) {
    return null;
  }
  const resolvedHour = nearestFrame(frameHours, currentHour);
  const currentIndex = frameHours.indexOf(resolvedHour);
  if (currentIndex < 0) {
    return frameHours[0] ?? null;
  }
  const nextIndex = currentIndex + 1;
  return nextIndex >= frameHours.length ? (frameHours[0] ?? null) : frameHours[nextIndex];
}

export function countGridAheadReadyFramesForHour(
  gridFrameHours: number[],
  gridReadyHourSet: ReadonlySet<number>,
  currentHour: number,
  maxAhead: number,
): number {
  if (gridFrameHours.length === 0 || maxAhead <= 0) {
    return 0;
  }
  const currentIndex = gridFrameHours.indexOf(currentHour);
  if (currentIndex < 0) {
    return 0;
  }

  let ready = 0;
  const endIndex = Math.min(gridFrameHours.length - 1, currentIndex + maxAhead);
  for (let index = currentIndex + 1; index <= endIndex; index += 1) {
    if (!gridReadyHourSet.has(gridFrameHours[index])) {
      break;
    }
    ready += 1;
  }
  return ready;
}

export function isGridPlaybackStartReadyForHour(
  gridFrameHours: number[],
  gridReadyHourSet: ReadonlySet<number>,
  startHour: number,
  startAheadFrames: number,
): boolean {
  if (!Number.isFinite(startHour) || !gridReadyHourSet.has(startHour)) {
    return false;
  }
  const currentIndex = gridFrameHours.indexOf(startHour);
  if (currentIndex < 0) {
    return false;
  }
  const remainingAhead = Math.max(0, gridFrameHours.length - currentIndex - 1);
  const requiredAhead = Math.min(startAheadFrames, remainingAhead);
  const aheadReadyCount = countGridAheadReadyFramesForHour(
    gridFrameHours,
    gridReadyHourSet,
    startHour,
    startAheadFrames,
  );
  return aheadReadyCount >= requiredAhead;
}

export function selectableFramesForVariable(frames: number[], preferredFh: number | null | undefined): number[] {
  if (frames.length === 0) {
    return frames;
  }
  if (!Number.isFinite(preferredFh)) {
    return frames;
  }
  const minimumFh = Number(preferredFh);
  const filtered = frames.filter((fh) => fh >= minimumFh);
  return filtered.length > 0 ? filtered : frames;
}

export function preferredInitialFrame(
  frames: number[],
  preferredFh: number | null | undefined,
  defaultFrameSelection: ModelDefaultFrameSelection = "first"
): number {
  if (frames.length === 0) {
    return 0;
  }
  if (!Number.isFinite(preferredFh)) {
    return defaultFrameSelection === "latest" ? frames[frames.length - 1] : frames[0];
  }
  return nearestFrame(frames, Number(preferredFh));
}

type ValidTimeFrame = {
  fh: number;
  valid_time?: string | null;
  meta?: { meta?: { valid_time?: string | null } | null } | null;
};

function validTimeForFrame(frame: ValidTimeFrame): string | null {
  const direct = typeof frame.valid_time === "string" && frame.valid_time.trim()
    ? frame.valid_time.trim()
    : null;
  if (direct) {
    return direct;
  }
  const nested = frame.meta?.meta?.valid_time;
  return typeof nested === "string" && nested.trim() ? nested.trim() : null;
}

export function mostRecentFrameHourByValidTime(frames: ValidTimeFrame[]): number | null {
  let bestHour: number | null = null;
  let bestTimestamp = Number.NEGATIVE_INFINITY;

  for (const frame of frames) {
    const fh = Number(frame.fh);
    const validTime = validTimeForFrame(frame);
    const timestamp = validTime ? Date.parse(validTime) : Number.NaN;
    if (!Number.isFinite(fh) || !Number.isFinite(timestamp)) {
      continue;
    }
    if (
      timestamp > bestTimestamp
      || (timestamp === bestTimestamp && (bestHour === null || fh > bestHour))
    ) {
      bestHour = fh;
      bestTimestamp = timestamp;
    }
  }

  return bestHour;
}

export function resolveForecastHourFromRows(
  rows: ValidTimeFrame[],
  current: number,
  preferredFh: number | null | undefined,
  defaultFrameSelection: ModelDefaultFrameSelection = "first",
  timeAxisMode: ModelTimeAxisMode = "forecast"
): number {
  const frames = rows
    .map((row) => Number(row.fh))
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  const selectableFrames = selectableFramesForVariable(Array.from(new Set(frames)), preferredFh);
  if (selectableFrames.length === 0) {
    return 0;
  }
  if (Number.isFinite(current)) {
    return nearestFrame(selectableFrames, current);
  }
  if (
    timeAxisMode === "observed"
    && defaultFrameSelection === "latest"
  ) {
    const selectableFrameSet = new Set(selectableFrames);
    const mostRecentHour = mostRecentFrameHourByValidTime(
      rows.filter((row) => selectableFrameSet.has(Number(row.fh)))
    );
    if (mostRecentHour !== null) {
      return mostRecentHour;
    }
  }
  return preferredInitialFrame(selectableFrames, preferredFh, defaultFrameSelection);
}

export function resolveForecastHour(
  frames: number[],
  current: number,
  preferredFh: number | null | undefined,
  defaultFrameSelection: ModelDefaultFrameSelection = "first"
): number {
  const selectableFrames = selectableFramesForVariable(frames, preferredFh);
  if (selectableFrames.length === 0) {
    return 0;
  }
  if (Number.isFinite(current)) {
    return nearestFrame(selectableFrames, current);
  }
  return preferredInitialFrame(selectableFrames, preferredFh, defaultFrameSelection);
}

export type ForecastHourTransitionResult = {
  resolvedHour: number;
  requestedHour: number | null;
  didFallback: boolean;
};

export function shouldNotifyForecastHourFallback(
  intentHour: number,
  resolvedHour: number,
  rows: ValidTimeFrame[],
  preferredFh: number | null | undefined,
): boolean {
  if (!Number.isFinite(intentHour) || resolvedHour === Number(intentHour)) {
    return false;
  }
  const frames = rows
    .map((row) => Number(row.fh))
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  const selectableFrames = selectableFramesForVariable(Array.from(new Set(frames)), preferredFh);
  return !selectableFrames.includes(Number(intentHour));
}

export function resolveForecastHourTransition(
  rows: ValidTimeFrame[],
  requestedHour: number,
  preferredFh: number | null | undefined,
  defaultFrameSelection: ModelDefaultFrameSelection = "first",
  timeAxisMode: ModelTimeAxisMode = "forecast",
): ForecastHourTransitionResult {
  const frames = rows
    .map((row) => Number(row.fh))
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  const selectableFrames = selectableFramesForVariable(Array.from(new Set(frames)), preferredFh);
  const resolvedHour = selectableFrames.length === 0
    ? 0
    : Number.isFinite(requestedHour)
      ? ceilingFrame(selectableFrames, requestedHour)
      : resolveForecastHourFromRows(
        rows,
        requestedHour,
        preferredFh,
        defaultFrameSelection,
        timeAxisMode,
      );
  return {
    resolvedHour,
    requestedHour: Number.isFinite(requestedHour) ? requestedHour : null,
    didFallback: shouldNotifyForecastHourFallback(requestedHour, resolvedHour, rows, preferredFh),
  };
}

export function resolveForecastHourTransitionFromFrames(
  frames: number[],
  requestedHour: number,
  preferredFh: number | null | undefined,
  defaultFrameSelection: ModelDefaultFrameSelection = "first",
): ForecastHourTransitionResult {
  const resolvedHour = resolveForecastHour(
    frames,
    requestedHour,
    preferredFh,
    defaultFrameSelection,
  );
  const rows = frames.map((fh) => ({ fh }));
  return {
    resolvedHour,
    requestedHour: Number.isFinite(requestedHour) ? requestedHour : null,
    didFallback: shouldNotifyForecastHourFallback(requestedHour, resolvedHour, rows, preferredFh),
  };
}

export function getEffectiveZoom(zoom: number): number {
  const dpr = typeof window === "undefined" ? 1 : Math.max(1, window.devicePixelRatio || 1);
  return zoom + Math.log2(dpr);
}

export function isPtypeIntensityLegendMeta(
  meta: LegendMeta & { var_key?: string; spec_key?: string; id?: string; var?: string }
): boolean {
  const id = String(meta.var_key ?? meta.spec_key ?? meta.id ?? meta.var ?? "").toLowerCase();
  return id === "ptype_intensity";
}

export function withPrecipRateUnits(title: string, units?: string): string {
  const normalizedTitle = title.trim().toLowerCase();
  if (normalizedTitle === "legend") {
    return "Precip Type & Intensity";
  }
  const resolvedUnits = (units ?? "").trim();
  if (!resolvedUnits) {
    return title;
  }
  const lowerTitle = title.toLowerCase();
  const lowerUnits = resolvedUnits.toLowerCase();
  if (lowerTitle.includes(`(${lowerUnits})`)) {
    return title;
  }
  return `${title} (${resolvedUnits})`;
}

export function normalizeLegendUnits(
  units: string | undefined,
  meta: LegendMeta & { var_key?: string; spec_key?: string; id?: string; var?: string }
): string | undefined {
  const resolved = (units ?? "").trim();
  if (resolved.toLowerCase() !== "index") {
    return units;
  }
  const id = String(meta.var_key ?? meta.spec_key ?? meta.id ?? meta.var ?? "").toLowerCase();
  if (id === "radar_ptype" || id === "mrms_radar_ptype") {
    return "dBZ";
  }
  return units;
}

export function buildLegend(
  meta: LegendMeta | null | undefined,
  opacity: number,
  modelId?: string | null,
): LegendPayload | null {
  if (!meta) {
    return null;
  }
  const metaWithIds = meta as LegendMeta & { var_key?: string; spec_key?: string; id?: string; var?: string };
  const legendId = String(metaWithIds.var_key ?? metaWithIds.spec_key ?? metaWithIds.id ?? metaWithIds.var ?? "").toLowerCase();
  const isPtypeIntensity = isPtypeIntensityLegendMeta(metaWithIds);
  const rawTitle = meta.legend_title ?? meta.display_name ?? "Legend";
  const baseTitle = meta.vector_layers && rawTitle.trim().toLowerCase() === "severe storm outlook"
    ? "Legend"
    : makeLegendTitle(legendId, rawTitle, modelId);
  const title = isPtypeIntensity ? withPrecipRateUnits(baseTitle, meta.units) : baseTitle;
  const units = normalizeLegendUnits(meta.units, metaWithIds);
  const legendMetadata = {
    kind: metaWithIds.kind,
    id: legendId || (metaWithIds.var_key ?? metaWithIds.spec_key ?? metaWithIds.id ?? metaWithIds.var),
    note: typeof meta.legend_note === "string" ? meta.legend_note.trim() || undefined : undefined,
    ptype_breaks: metaWithIds.ptype_breaks,
    ptype_order: metaWithIds.ptype_order,
    bins_per_ptype: metaWithIds.bins_per_ptype,
  };

  if (Array.isArray(meta.legend_entries) && meta.legend_entries.length > 0) {
    const entries = meta.legend_entries
      .map((entry) => ({
        value: Number(entry.value),
        color: String(entry.color ?? "").trim(),
        label: typeof entry.label === "string" ? entry.label.trim() : undefined,
      }))
      .filter((entry) => Number.isFinite(entry.value) && entry.color);
    if (entries.length > 0) {
      return {
        title,
        units,
        entries,
        opacity,
        ...legendMetadata,
      };
    }
  }

  // V3 sidecar format: meta.legend.stops = [[value, color], ...]
  const resolvedStops = meta.legend_stops ?? meta.legend?.stops;
  if (Array.isArray(resolvedStops) && resolvedStops.length > 0) {
    const entries = resolvedStops
      .map(([value, color]) => ({ value: Number(value), color }))
      .filter((entry) => Number.isFinite(entry.value));
    if (entries.length === 0) {
      return null;
    }
    return {
      title,
      units,
      entries,
      opacity,
      ...legendMetadata,
    };
  }

  const hasPtypeSegments =
    Array.isArray(meta.ptype_order) && Boolean(meta.ptype_breaks) && Boolean(meta.ptype_levels);

  if (
    Array.isArray(meta.colors) &&
    meta.colors.length > 1 &&
    Array.isArray(meta.range) &&
    meta.range.length === 2 &&
    !hasPtypeSegments
  ) {
    const [min, max] = meta.range;
    const entries = meta.colors.map((color, index) => {
      const denom = Math.max(1, meta.colors!.length - 1);
      const value = min + ((max - min) * index) / denom;
      return { value, color };
    });
    return {
      title,
      units,
      entries,
      opacity,
      ...legendMetadata,
    };
  }

  if (Array.isArray(meta.colors) && meta.colors.length > 0) {
    const entries: Array<{ value: number; color: string }> = [];

    if (Array.isArray(meta.ptype_order) && meta.ptype_breaks && meta.ptype_levels) {
      for (const ptype of meta.ptype_order) {
        const ptypeBreak = meta.ptype_breaks[ptype];
        const ptypeLevels = meta.ptype_levels[ptype];
        if (!ptypeBreak || !Array.isArray(ptypeLevels)) {
          continue;
        }
        const offset = Number(ptypeBreak.offset);
        const count = Number(ptypeBreak.count);
        if (!Number.isFinite(offset) || !Number.isFinite(count) || offset < 0 || count <= 0) {
          continue;
        }
        const maxItems = Math.min(count, ptypeLevels.length, meta.colors.length - offset);
        for (let index = 0; index < maxItems; index += 1) {
          const value = Number(ptypeLevels[index]);
          const color = meta.colors[offset + index];
          if (!Number.isFinite(value) || !color) {
            continue;
          }
          entries.push({ value, color });
        }
      }
    }

    if (entries.length === 0 && Array.isArray(meta.levels) && meta.levels.length > 0) {
      const maxItems = Math.min(meta.levels.length, meta.colors.length);
      for (let index = 0; index < maxItems; index += 1) {
        const value = Number(meta.levels[index]);
        const color = meta.colors[index];
        if (!Number.isFinite(value) || !color) {
          continue;
        }
        entries.push({ value, color });
      }
    }

    if (entries.length > 0) {
      return {
        title,
        units,
        entries,
        opacity,
        ...legendMetadata,
      };
    }
  }

  return null;
}

export function buildVectorLayerUrl(params: {
  apiRoot: string;
  model: string;
  run: string | null | undefined;
  variable: string;
  frame: FrameRow | null | undefined;
  layerKey?: string;
  domain?: string | null;
}): string | null {
  const resolvedRun = String(params.run ?? "").trim();
  const layerKey = String(params.layerKey ?? "primary").trim();
  const fh = Number(params.frame?.fh);
  if (!resolvedRun || !Number.isFinite(fh) || !layerKey) {
    return null;
  }
  // Non-canonical domains are path-scoped (Phase 2A locked decision #5).
  const domainPrefix = params.domain ? `domains/${encodeURIComponent(params.domain)}/` : "";
  const baseUrl = `${params.apiRoot}/api/v4/${domainPrefix}${encodeURIComponent(params.model)}/${encodeURIComponent(resolvedRun)}/${encodeURIComponent(params.variable)}/${Math.round(fh)}/vectors/${encodeURIComponent(layerKey)}`;
  const meta = params.frame?.meta?.meta;
  const versionToken =
    typeof meta?.generated_at === "string" && meta.generated_at.trim()
      ? meta.generated_at.trim()
      : typeof meta?.issue_time === "string" && meta.issue_time.trim()
        ? meta.issue_time.trim()
        : typeof params.frame?.valid_time === "string" && params.frame.valid_time.trim()
          ? params.frame.valid_time.trim()
          : "";
  if (versionToken) {
    return `${baseUrl}?v=${encodeURIComponent(versionToken)}`;
  }
  return baseUrl;
}
