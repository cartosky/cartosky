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
  RegionPreset,
  RunManifestResponse,
} from "@/lib/api";
import { readCapabilityRenderSubstrates } from "@/lib/api";
import type { LegendPayload } from "@/components/map-legend";
import type { SharePayload } from "@/components/twf-share-modal";
import type { BasemapMode } from "@/components/map-canvas";
import type { WeatherSubstrate } from "@/lib/config";
import {
  formatObservedCompactTime,
  formatValidTime,
  validDayLabel,
  type TimeAxisMode,
} from "@/lib/time-axis";

// ── Constants ─────────────────────────────────────────────────────────

export const AUTOPLAY_TICK_MS = 250;
export const AUTOPLAY_READY_AHEAD = 2;
export const AUTOPLAY_SKIP_WINDOW = 8;
/** Stall time before the loop attempts to skip ahead to a ready frame. */
export const AUTOPLAY_STALL_SKIP_MS = 500;
export const GRID_PLAY_START_AHEAD_FRAMES = 2;
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
export const VARIABLE_SWITCH_TIMEOUT_MS = 2500;
export const PERMALINK_SYNC_DEBOUNCE_MS = 200;

export const BASEMAP_MODE_STORAGE_KEY = "twf.map.basemap_mode";
export const LEGEND_VISIBILITY_STORAGE_KEY = "twf.map.legend_visible";
export const POINT_LABELS_STORAGE_KEY = "twf.map.point_labels_enabled";
export const ZOOM_CONTROLS_STORAGE_KEY = "twf.map.zoom_controls_visible";
export const MODEL_ORDER_BY_ID: Record<string, number> = {
  hrrr: 0,
  nam: 1,
  nbm: 2,
  gfs: 3,
  spc: 4,
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
};

export type VariableOption = GroupedOption;

export type VariableEntry = {
  id: string;
  displayName?: string;
  order?: number | null;
  defaultFh?: number | null;
  buildable?: boolean;
  kind?: string | null;
  displayResamplingOverride?: string | null;
  group?: string | null;
  renderSubstrates?: WeatherSubstrate[];
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
  expectedSelectionKey: string;
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

export function readZoomControlsPreference(): boolean {
  return readBooleanPreference(ZOOM_CONTROLS_STORAGE_KEY, false);
}

export function writeZoomControlsPreference(visible: boolean): void {
  writeBooleanPreference(ZOOM_CONTROLS_STORAGE_KEY, visible);
}

export function pickPreferred(values: string[], preferred: string): string {
  if (values.includes(preferred)) {
    return preferred;
  }
  return values[0] ?? "";
}

export function makeRegionLabel(id: string, preset?: RegionPreset): string {
  return preset?.label ?? id.toUpperCase();
}

const VARIABLE_UI_OVERRIDES: Record<string, VariableUiOverride> = {
  tmp2m: { label: "Surface Temp", group: "SURFACE", order: 0 },
  dp2m: { label: "Surface Dew Point", group: "SURFACE", order: 1 },
  td2m: { label: "Surface Dew Point", group: "SURFACE", order: 1 },
  tmp850: { group: "UPPER AIR", order: 30 },
  wspd10m: { label: "10m Wind Speed", group: "SURFACE", order: 2 },
  wgst10m: { label: "10m Wind Gusts", group: "SURFACE", order: 3 },
  precip_ptype: { label: "Precip Type & Intensity", group: "PRECIPITATION", order: 10 },
  radar_ptype: { label: "Composite Reflectivity + Ptype", group: "PRECIPITATION", order: 11 },
  qpf: { label: "Total Precip (QPF)", group: "PRECIPITATION", order: 12 },
  snow10to1: { label: "Total Snowfall (10:1)", group: "PRECIPITATION", order: 13 },
  snowkuchera: { label: "Total Snowfall (Kuchera)", group: "PRECIPITATION", order: 14 },
  pwat: { label: "Precipitable Water", group: "PRECIPITATION", order: 9999 },
  mucape: { label: "Most-Unstable CAPE", group: "SEVERE", order: 20 },
  mlcape: { label: "Mixed-Layer CAPE", group: "SEVERE", order: 21 },
  sbcape: { label: "Surface-Based CAPE", group: "SEVERE", order: 22 },
  vort500: { label: "500mb Heights + Vorticity", group: "UPPER AIR", order: 31 },
};

const MODEL_UI_OVERRIDES: Record<string, ModelUiOverride> = {
  hrrr: { label: "HRRR", group: "MODELS", order: 0 },
  nam: { label: "NAM", group: "MODELS", order: 1 },
  gfs: { label: "GFS", group: "MODELS", order: 2 },
  nbm: { label: "NBM", group: "MODELS", order: 3 },
  mrms: { label: "Radar", group: "OBSERVATIONS", order: 10 },
  nws_hazards: { label: "NWS Hazards", group: "OBSERVATIONS", order: 11 },
  spc: { label: "SPC Outlooks", group: "OBSERVATIONS", order: 12 },
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
    case "surface":
      return "SURFACE";
    case "temperature":
    case "wind":
      return "SURFACE";
    case "precipitation":
    case "radar & precipitation type":
    case "moisture":
    case "radar":
      return "PRECIPITATION";
    case "severe":
    case "instability":
      return "SEVERE";
    case "upper air":
    case "dynamics":
      return "UPPER AIR";
    default:
      return null;
  }
}

export function makeVariableLabel(id: string, preferredLabel?: string | null): string {
  const override = variableUiOverride(id);
  if (override?.label) {
    return override.label;
  }
  if (preferredLabel && preferredLabel.trim()) {
    return preferredLabel.trim();
  }
  return id;
}

export function buildFallbackSharePayload(params: {
  modelLabel: string;
  runLabel: string;
  variableLabel: string;
  forecastHour: number;
  timeAxisMode: TimeAxisMode;
  validTimeISO?: string | null;
  permalink: string;
}): SharePayload {
  const timeLabel = params.timeAxisMode === "observed"
    ? (params.validTimeISO ? `Observed ${formatObservedCompactTime(params.validTimeISO) ?? params.validTimeISO}` : "Observed time n/a")
    : params.timeAxisMode === "valid"
      ? (params.validTimeISO ? `${validDayLabel(params.forecastHour)} • ${formatValidTime(params.validTimeISO) ?? params.validTimeISO}` : validDayLabel(params.forecastHour))
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

export function normalizeCapabilityVarRows(modelCapability: CapabilityModel | null | undefined): VariableEntry[] {
  if (!modelCapability?.variables) {
    return [];
  }
  const normalized: VariableEntry[] = Object.entries(modelCapability.variables)
    .map(([id, entry]) => ({
      id: String(id).trim(),
      displayName: entry.display_name?.trim() || undefined,
      order: toNumberOrNull(entry.order),
      defaultFh: variableDefaultFh(entry),
      buildable: entry.buildable !== false,
      kind: typeof entry.kind === "string" ? entry.kind : null,
      displayResamplingOverride:
        typeof entry.display_resampling_override === "string" ? entry.display_resampling_override : null,
      group: typeof entry.group === "string" ? entry.group : null,
      renderSubstrates: readCapabilityRenderSubstrates(entry),
    }))
    .filter((entry) => Boolean(entry.id) && entry.buildable);

  return normalized.sort((a, b) => {
    const aOrder = Number.isFinite(a.order) ? Number(a.order) : Number.POSITIVE_INFINITY;
    const bOrder = Number.isFinite(b.order) ? Number(b.order) : Number.POSITIVE_INFINITY;
    if (aOrder !== bOrder) {
      return aOrder - bOrder;
    }
    return a.id.localeCompare(b.id);
  });
}

export function capabilityVarsForManifest(
  manifestVars: RunManifestResponse["variables"] | null | undefined,
  capabilityVars: VariableEntry[]
): VariableEntry[] {
  if (!manifestVars) {
    return capabilityVars;
  }
  const manifestKeys = Object.keys(manifestVars);
  if (manifestKeys.length === 0) {
    return [];
  }
  const manifestSet = new Set(manifestKeys);
  const known = capabilityVars.filter((entry) => manifestSet.has(entry.id));
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
    const displayName = entry?.display_name ?? entry?.name ?? entry?.label;
    normalized.push({ id: normalizedId, displayName: displayName?.trim() || undefined });
  }
  return normalized;
}

export function makeVariableOptions(entries: VariableEntry[]): VariableOption[] {
  return entries
    .map((entry, index) => {
      const override = variableUiOverride(entry.id);
      return {
        value: entry.id,
        label: makeVariableLabel(entry.id, entry.displayName),
        group: canonicalVariableGroup(entry.id, entry.group),
        sortOrder: typeof override?.order === "number" ? override.order : (1000 + index),
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
  for (const frame of varEntry.frames) {
    const fh = Number(frame?.fh);
    if (!Number.isFinite(fh)) {
      continue;
    }
    rows.push({
      fh,
      has_cog: false,
      run: manifest.run,
      valid_time: typeof frame?.valid_time === "string" && frame.valid_time.trim() ? frame.valid_time.trim() : undefined,
      meta:
        typeof frame?.valid_time === "string" && frame.valid_time.trim()
          ? { meta: { valid_time: frame.valid_time.trim() } }
          : undefined,
    });
  }
  rows.sort((a, b) => Number(a.fh) - Number(b.fh));
  return { rows, hasFrameList: true };
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
      meta: row.meta ?? previous.meta,
    };
  });
}

export function extractLegendMeta(row: FrameRow | null | undefined): LegendMeta | null {
  const rawMeta = row?.meta?.meta ?? null;
  if (!rawMeta) return null;
  const nested = (rawMeta as { meta?: LegendMeta | null }).meta;
  return nested ?? (rawMeta as LegendMeta);
}

export function nearestFrame(frames: number[], current: number): number {
  if (frames.length === 0) return 0;
  if (frames.includes(current)) return current;
  return frames.reduce((nearest, value) => {
    const nearestDelta = Math.abs(nearest - current);
    const valueDelta = Math.abs(value - current);
    return valueDelta < nearestDelta ? value : nearest;
  }, frames[0]);
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

export function getEffectiveZoom(zoom: number): number {
  const dpr = typeof window === "undefined" ? 1 : Math.max(1, window.devicePixelRatio || 1);
  return zoom + Math.log2(dpr);
}

export function isPrecipPtypeLegendMeta(
  meta: LegendMeta & { var_key?: string; spec_key?: string; id?: string; var?: string }
): boolean {
  const kind = String(meta.kind ?? "").toLowerCase();
  const id = String(meta.var_key ?? meta.spec_key ?? meta.id ?? meta.var ?? "").toLowerCase();
  return kind.includes("precip_ptype") || id === "precip_ptype";
}

export function withPrecipRateUnits(title: string, units?: string): string {
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

export function buildLegend(meta: LegendMeta | null | undefined, opacity: number): LegendPayload | null {
  if (!meta) {
    return null;
  }
  const metaWithIds = meta as LegendMeta & { var_key?: string; spec_key?: string; id?: string; var?: string };
  const isPrecipPtype = isPrecipPtypeLegendMeta(metaWithIds);
  const rawTitle = meta.legend_title ?? meta.display_name ?? "Legend";
  const baseTitle = meta.vector_layers && rawTitle.trim().toLowerCase() === "severe storm outlook"
    ? "Legend"
    : rawTitle;
  const title = isPrecipPtype ? withPrecipRateUnits(baseTitle, meta.units) : baseTitle;
  const units = normalizeLegendUnits(meta.units, metaWithIds);
  const legendMetadata = {
    kind: metaWithIds.kind,
    id: metaWithIds.var_key ?? metaWithIds.spec_key ?? metaWithIds.id ?? metaWithIds.var,
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
}): string | null {
  const resolvedRun = String(params.run ?? "").trim();
  const layerKey = String(params.layerKey ?? "primary").trim();
  const fh = Number(params.frame?.fh);
  if (!resolvedRun || !Number.isFinite(fh) || !layerKey) {
    return null;
  }
  return `${params.apiRoot}/api/v4/${encodeURIComponent(params.model)}/${encodeURIComponent(resolvedRun)}/${encodeURIComponent(params.variable)}/${Math.round(fh)}/vectors/${encodeURIComponent(layerKey)}`;
}
