import { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { AlertCircle, Eye, MapPin, Moon, Send, SlidersHorizontal, Sun } from "lucide-react";

import { BottomForecastControls } from "@/components/bottom-forecast-controls";
import { MapCanvas, buildMapStyle, type BasemapMode } from "@/components/map-canvas";
import type { LegendPayload } from "@/components/map-legend";
import type { SharePayload } from "@/components/twf-share-modal";
import { WeatherToolbar } from "@/components/weather-toolbar";
import {
  fetchAnchorFeatureCollection,
  type CapabilitiesResponse,
  type CapabilityModel,
  type CapabilityVariable,
  type FrameRow,
  type GridManifestResponse,
  type LegendMeta,
  type ModelDefaultFrameSelection,
  type RegionPreset,
  type RunManifestResponse,
  fetchManifest,
  fetchCapabilities,
  fetchFrames,
  fetchGridManifest,
  fetchRegionPresets,
  fetchRuns,
  fetchSampleBatch,
  readCapabilityDefaultFrameSelection,
  readCapabilityLatestOnly,
  readCapabilityRenderSubstrates,
  readCapabilitySupportsSampling,
  readCapabilityTimeAxisMode,
} from "@/lib/api";
import {
  anchorBatchPointsFromGeoJson,
  buildAnchorDisplayGeoJson,
  buildInactiveAnchorFeatureCollection,
  getActiveAnchorLabels,
  resolveAnchorDisplayRule,
  type AnchorFeatureCollection,
} from "@/lib/anchor-labels";
import {
  API_ORIGIN,
  isDeferredNonCriticalBootstrapEnabled,
  MAP_VIEW_DEFAULTS,
  OVERLAY_DEFAULT_OPACITY,
  type WeatherSubstrate,
} from "@/lib/config";
import { buildRunOptions, formatRunLabel, latestRunLabel, pickLatestRunId, sortRunIdsDescending } from "@/lib/run-options";
import { type ScreenshotExportState } from "@/lib/screenshot_export";
import {
  deriveObservedSourceStatus,
  frameIssueTime,
  frameValidTime,
  formatIssuedTimeISO,
  formatObservedCompactTime,
  formatValidTime,
  observedSourceStatusFromAvailability,
  runIdToIso,
  validDayLabel,
  type TimeAxisMode,
} from "@/lib/time-axis";
import { readPermalink } from "@/lib/permalink-read";
import { captureProductAnalyticsEvent } from "@/lib/posthog";
import { trackRumDiagnosticMetric } from "@/lib/rum";
import { useSampleTooltip } from "@/lib/use-sample-tooltip";

import { detectViewerLayoutMode, useViewerLayoutMode } from "@/lib/viewer-layout";

const TwfShareModal = lazy(() =>
  import("@/components/twf-share-modal").then((module) => ({ default: module.TwfShareModal }))
);
const NwsCityModal = lazy(() =>
  import("@/components/nws-city-modal").then((module) => ({ default: module.NwsCityModal }))
);
const MapLegend = lazy(() =>
  import("@/components/map-legend").then((module) => ({ default: module.MapLegend }))
);

const AUTOPLAY_TICK_MS = 250;
const AUTOPLAY_READY_AHEAD = 2;
const AUTOPLAY_SKIP_WINDOW = 8;
/** Stall time before the loop attempts to skip ahead to a ready frame. */
const AUTOPLAY_STALL_SKIP_MS = 500;
const GRID_PLAY_START_AHEAD_FRAMES = 2;
const GRID_PLAY_STALL_MS = 1500;
const FRAME_STATUS_BADGE_MS = 900;
const READY_URL_TTL_MS = 30_000;
const READY_URL_LIMIT = 160;
const INFLIGHT_FRAME_TTL_MS = 12_000;
const PRELOAD_START_RATIO = 0.7;
const PRELOAD_STALL_MS = 8000;
const FRAME_MAX_RETRIES = 3;
const FRAME_HARD_DEADLINE_MS = 30_000;
const FRAME_RETRY_BASE_MS = 1200;
const SCRUB_COMMIT_NEIGHBOR_WINDOW = 2;
const VARIABLE_SWITCH_TIMEOUT_MS = 2500;
const PERMALINK_SYNC_DEBOUNCE_MS = 200;

function viewportSignatureFromState(view: { lat: number; lon: number; z: number }): string {
  const zoomBucket = Math.round(view.z * 2) / 2;
  const latBucket = Math.round(view.lat * 4) / 4;
  const lonBucket = Math.round(view.lon * 4) / 4;
  return `${zoomBucket}|${latBucket}|${lonBucket}`;
}

type NewRunNoticeState = {
  model: string;
  previousRunId: string;
  latestRunId: string;
};

function areStringArraysEqual(left: readonly string[], right: readonly string[]): boolean {
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

function withUpdatedLatestRun(
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

type AnchorBatchRequestContext = {
  selectionKey: string;
  generation: number;
  model: string;
  run: string;
  variable: string;
  baseCollection: AnchorFeatureCollection;
  points: Array<{ id: string; lat: number; lon: number }>;
  deferToLatest: boolean;
};

type Option = {
  value: string;
  label: string;
};

type VariableOption = Option & {
  group: string | null;
};

type VariableEntry = {
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

type ModelEntry = {
  id: string;
  displayName?: string;
  order?: number | null;
};

type PendingLoopStartMetric = {
  startedAt: number;
};

type PendingVariableSwitchMetric = {
  toVariableId: string;
  expectedSelectionKey: string;
};

type VariableSwitchState = {
  fromVariable: string;
  toVariable: string;
  startedAt: number;
  visualState: "holding_old" | "warming_new" | "promoting_new";
};

type ScrubCommitIntent = {
  hour: number;
  direction: 1 | -1 | 0;
  startedAt: number;
};

type ScrubPhase0aSnapshot = {
  liveStartedAt: number | null;
  liveEventCount: number;
  supersededCount: number;
  lastRequestedHour: number | null;
};

function emptyScrubPhase0aSnapshot(): ScrubPhase0aSnapshot {
  return {
    liveStartedAt: null,
    liveEventCount: 0,
    supersededCount: 0,
    lastRequestedHour: null,
  };
}

type ForecastHourChangeReason = "standard" | "scrub-live" | "scrub-commit";

const BASEMAP_MODE_STORAGE_KEY = "twf.map.basemap_mode";
const MODEL_ORDER_BY_ID: Record<string, number> = {
  hrrr: 0,
  nam: 1,
  nbm: 2,
  gfs: 3,
  spc: 4,
};

function readBasemapModePreference(): BasemapMode {
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

function writeBasemapModePreference(mode: BasemapMode): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(BASEMAP_MODE_STORAGE_KEY, mode);
  } catch {
    // Ignore storage errors.
  }
}

function pickPreferred(values: string[], preferred: string): string {
  if (values.includes(preferred)) {
    return preferred;
  }
  return values[0] ?? "";
}

function makeRegionLabel(id: string, preset?: RegionPreset): string {
  return preset?.label ?? id.toUpperCase();
}

function makeVariableLabel(id: string, preferredLabel?: string | null): string {
  if (preferredLabel && preferredLabel.trim()) {
    return preferredLabel.trim();
  }
  return id;
}

function buildFallbackSharePayload(params: {
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

function toNumberOrNull(value: unknown): number | null {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function variableDefaultFh(entry?: CapabilityVariable | null): number | null {
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

function modelOrderById(id: string): number | null {
  const normalized = id.trim().toLowerCase();
  return Number.isFinite(MODEL_ORDER_BY_ID[normalized]) ? MODEL_ORDER_BY_ID[normalized] : null;
}

function normalizeModelRows(
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
    const aOrder = Number.isFinite(a.order) ? Number(a.order) : Number.POSITIVE_INFINITY;
    const bOrder = Number.isFinite(b.order) ? Number(b.order) : Number.POSITIVE_INFINITY;
    if (aOrder !== bOrder) {
      return aOrder - bOrder;
    }
    return a.id.localeCompare(b.id);
  });
}

function normalizeCapabilityVarRows(modelCapability: CapabilityModel | null | undefined): VariableEntry[] {
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

function capabilityVarsForManifest(
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

function normalizeManifestVarRows(
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

function makeVariableOptions(entries: VariableEntry[]): VariableOption[] {
  return entries.map((entry) => ({
    value: entry.id,
    label: makeVariableLabel(entry.id, entry.displayName),
    group: entry.group ?? null,
  }));
}

function resolveManifestFrames(
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

function mergeManifestRowsWithPrevious(
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

function extractLegendMeta(row: FrameRow | null | undefined): LegendMeta | null {
  const rawMeta = row?.meta?.meta ?? null;
  if (!rawMeta) return null;
  const nested = (rawMeta as { meta?: LegendMeta | null }).meta;
  return nested ?? (rawMeta as LegendMeta);
}

function nearestFrame(frames: number[], current: number): number {
  if (frames.length === 0) return 0;
  if (frames.includes(current)) return current;
  return frames.reduce((nearest, value) => {
    const nearestDelta = Math.abs(nearest - current);
    const valueDelta = Math.abs(value - current);
    return valueDelta < nearestDelta ? value : nearest;
  }, frames[0]);
}

function selectableFramesForVariable(frames: number[], preferredFh: number | null | undefined): number[] {
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

function preferredInitialFrame(
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

function resolveForecastHour(
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

function getEffectiveZoom(zoom: number): number {
  const dpr = typeof window === "undefined" ? 1 : Math.max(1, window.devicePixelRatio || 1);
  return zoom + Math.log2(dpr);
}

function isPrecipPtypeLegendMeta(
  meta: LegendMeta & { var_key?: string; spec_key?: string; id?: string; var?: string }
): boolean {
  const kind = String(meta.kind ?? "").toLowerCase();
  const id = String(meta.var_key ?? meta.spec_key ?? meta.id ?? meta.var ?? "").toLowerCase();
  return kind.includes("precip_ptype") || id === "precip_ptype";
}

function withPrecipRateUnits(title: string, units?: string): string {
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

function normalizeLegendUnits(
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

function buildLegend(meta: LegendMeta | null | undefined, opacity: number): LegendPayload | null {
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

function buildVectorLayerUrl(params: {
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

export default function App() {
  const deferNonCriticalBootstrapEnabled = isDeferredNonCriticalBootstrapEnabled();
  const viewerLayoutMode = useViewerLayoutMode();
  const isDesktopViewerLayout = viewerLayoutMode === "desktop";
  const initialPermalink = useMemo(() => readPermalink(), []);
  const initialPermalinkMapView = useMemo(() => {
    if (
      Number.isFinite(initialPermalink.lat)
      && Number.isFinite(initialPermalink.lon)
      && Number.isFinite(initialPermalink.z)
    ) {
      return {
        lat: Number(initialPermalink.lat),
        lon: Number(initialPermalink.lon),
        z: Number(initialPermalink.z),
      };
    }
    return null;
  }, [initialPermalink]);
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);
  const [models, setModels] = useState<Option[]>([]);
  const [regions, setRegions] = useState<Option[]>([]);
  const [runs, setRuns] = useState<string[]>([]);
  const [variables, setVariables] = useState<VariableOption[]>([]);
  const [frameRows, setFrameRows] = useState<FrameRow[]>([]);
  const [runManifest, setRunManifest] = useState<RunManifestResponse | null>(null);
  const [gridManifest, setGridManifest] = useState<GridManifestResponse | null>(null);
  const [resolvedGridLatestRunId, setResolvedGridLatestRunId] = useState<string | null>(null);
  // Keep the last non-null resolved grid run so that selectionKey stays stable
  // while the next manifest probe is in-flight ("pending-grid" → real-id
  // transitions used to cause a double cache wipe).
  const lastResolvedGridRunRef = useRef<string | null>(null);
  if (resolvedGridLatestRunId) {
    lastResolvedGridRunRef.current = resolvedGridLatestRunId;
  }
  const [regionPresets, setRegionPresets] = useState<Record<string, RegionPreset>>({});
  const [anchorBaseGeoJson, setAnchorBaseGeoJson] = useState<AnchorFeatureCollection | null>(null);
  const [anchorDisplayGeoJson, setAnchorDisplayGeoJson] = useState<AnchorFeatureCollection | null>(null);

  const [model, setModel] = useState("");
  const [region, setRegion] = useState(MAP_VIEW_DEFAULTS.region);
  const [run, setRun] = useState("latest");
  const [newRunNotice, setNewRunNotice] = useState<NewRunNoticeState | null>(null);
  const [variable, setVariable] = useState("");
  const [visualVariable, setVisualVariable] = useState("");
  const [variableSwitchState, setVariableSwitchState] = useState<VariableSwitchState | null>(null);
  const [forecastHour, setForecastHour] = useState(Number.POSITIVE_INFINITY);
  const [targetForecastHour, setTargetForecastHour] = useState(Number.POSITIVE_INFINITY);
  const [, setZoomBucket] = useState(Math.round(MAP_VIEW_DEFAULTS.zoom));
  const [mapZoom, setMapZoom] = useState(MAP_VIEW_DEFAULTS.zoom);
  const [zoomGestureActive, setZoomGestureActive] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isGridPreloadingForPlay, setIsGridPreloadingForPlay] = useState(false);
  const [isScrubbing, setIsScrubbing] = useState(false);
  const [scrubRequestedHour, setScrubRequestedHour] = useState<number | null>(null);
  const [scrubCommitIntent, setScrubCommitIntent] = useState<ScrubCommitIntent | null>(null);
  const [opacity, setOpacity] = useState(OVERLAY_DEFAULT_OPACITY);
  const [basemapMode, setBasemapMode] = useState<BasemapMode>(() => readBasemapModePreference());
  const [pointLabelsEnabled, setPointLabelsEnabled] = useState(true);
  const [zoomControlsVisible, setZoomControlsVisible] = useState(false);
  const [legendVisible, setLegendVisible] = useState(() =>
    typeof window === "undefined" ? true : detectViewerLayoutMode() === "desktop"
  );
  const [displayPanelOpen, setDisplayPanelOpen] = useState(false);
  const [isPageVisible, setIsPageVisible] = useState(() =>
    typeof document === "undefined" ? true : !document.hidden
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isShareModalOpen, setIsShareModalOpen] = useState(false);
  const [selectedAnchorCity, setSelectedAnchorCity] = useState<{
    id: string;
    city: string;
    state: string;
    st: string;
  } | null>(null);
  const [sharePayload, setSharePayload] = useState<SharePayload>({
    permalink: "",
    summary: "CartoSky viewer share",
    detailsSummary: "",
  });
  const [frameStatusMessage, setFrameStatusMessage] = useState<string | null>(null);
  const [mapViewTick, setMapViewTick] = useState(0);
  const [isMapReady, setIsMapReady] = useState(false);
  const [selectionEpoch, setSelectionEpoch] = useState(0);
  const [gridReadyVersion, setGridReadyVersion] = useState(0);
  // Coalesce rapid gridReadyVersion bumps into a single state update per
  // microtask.  This prevents O(n) recomputations when many frames become
  // ready (or are evicted) within the same event-loop tick.
  const gridReadyVersionPendingRef = useRef(false);
  const bumpGridReadyVersion = useCallback(() => {
    if (gridReadyVersionPendingRef.current) {
      return;
    }
    gridReadyVersionPendingRef.current = true;
    queueMicrotask(() => {
      gridReadyVersionPendingRef.current = false;
      setGridReadyVersion((c) => c + 1);
    });
  }, []);
  const [visibleGridFrameHour, setVisibleGridFrameHour] = useState<number | null>(null);

  const isVariableSwitching = useMemo(() => {
    if (!variableSwitchState) {
      return false;
    }
    if (variableSwitchState.toVariable !== variable) {
      return false;
    }
    return variableSwitchState.visualState !== "promoting_new";
  }, [variableSwitchState, variable]);
  const [bootstrapHydrated, setBootstrapHydrated] = useState(false);
  const [permalinkHydrated, setPermalinkHydrated] = useState(false);
  const [firstWeatherFramePainted, setFirstWeatherFramePainted] = useState(false);
  const selectionEpochRef = useRef(selectionEpoch);
  const [loadedFramesKey, setLoadedFramesKey] = useState("");
  const datasetGenerationRef = useRef(0);
  const requestGenerationRef = useRef(0);
  const scrubRafRef = useRef<number | null>(null);
  const pendingScrubHourRef = useRef<number | null>(null);
  const scrubPhase0aRef = useRef<ScrubPhase0aSnapshot>(emptyScrubPhase0aSnapshot());
  const frameStatusTimerRef = useRef<number | null>(null);
  const forecastHourRef = useRef(forecastHour);
  const mapZoomRef = useRef(MAP_VIEW_DEFAULTS.zoom);
  const runsLoadedForModelRef = useRef<string>("");
  const mapInstanceRef = useRef<MapLibreMap | null>(null);
  const mapViewRef = useRef({
    lat: MAP_VIEW_DEFAULTS.center[0],
    lon: MAP_VIEW_DEFAULTS.center[1],
    z: MAP_VIEW_DEFAULTS.zoom,
  });
  const viewportSignatureRef = useRef(viewportSignatureFromState(mapViewRef.current));
  const pendingMapViewRef = useRef(initialPermalinkMapView);
  const mapViewHydratedRef = useRef(initialPermalinkMapView === null);
  const pendingInitialForecastHourRef = useRef(
    Number.isFinite(initialPermalink.fh) ? Number(initialPermalink.fh) : null
  );
  const viewerMountedAtRef = useRef(typeof performance === "undefined" ? 0 : performance.now());
  const firstViewerFrameTrackedRef = useRef(false);
  const firstMapRenderTrackedRef = useRef(false);
  const viewerOpenedTrackedRef = useRef(false);
  const pendingFirstViewerFrameRef = useRef(false);
  const pendingFirstViewerFrameHourRef = useRef<number | null>(null);
  const pendingLoopStartMetricRef = useRef<PendingLoopStartMetric | null>(null);
  const pendingVariableSwitchRef = useRef<PendingVariableSwitchMetric | null>(null);
  const modelRef = useRef(model);
  const variableRef = useRef(variable);
  const targetForecastHourRef = useRef(targetForecastHour);
  const permalinkHydratedRef = useRef(false);
  const lastSyncedPermalinkSearchRef = useRef("");
  const suppressNextUrlSyncRef = useRef(true);
  const gridReadyFrameUrlsRef = useRef<Set<string>>(new Set());
  const gridPlaybackHourRef = useRef<number | null>(null);
  const anchorSelectionKeyRef = useRef("");
  const anchorBatchAbortRef = useRef<AbortController | null>(null);
  const anchorBatchInFlightHourRef = useRef<number | null>(null);
  const anchorBatchInFlightSelectionKeyRef = useRef("");
  const anchorBatchPendingHourRef = useRef<number | null>(null);
  const anchorBatchLastAppliedHourRef = useRef<number | null>(null);
  const anchorBatchLastAppliedSelectionKeyRef = useRef("");
  const anchorBatchContextRef = useRef<AnchorBatchRequestContext | null>(null);
  const wasCompactViewportRef = useRef<boolean>(viewerLayoutMode !== "desktop");

  useEffect(() => {
    writeBasemapModePreference(basemapMode);
  }, [basemapMode]);

  useEffect(() => {
    setLegendVisible((current) => {
      if (viewerLayoutMode !== "desktop") {
        wasCompactViewportRef.current = true;
        return false;
      }

      const next = wasCompactViewportRef.current ? true : current;
      wasCompactViewportRef.current = false;
      return next;
    });
  }, [viewerLayoutMode]);

  useEffect(() => {
    if (isDesktopViewerLayout || !displayPanelOpen) {
      return;
    }
    setDisplayPanelOpen(false);
  }, [displayPanelOpen, isDesktopViewerLayout]);

  const modelCatalog = capabilities?.model_catalog ?? {};
  const selectedModelCapability: CapabilityModel | null = model ? modelCatalog[model] ?? null : null;
  const selectedCapabilityVars = useMemo(
    () => normalizeCapabilityVarRows(selectedModelCapability),
    [selectedModelCapability]
  );
  const selectedCapabilityVarMap = useMemo(() => {
    const map = new Map<string, VariableEntry>();
    for (const entry of selectedCapabilityVars) {
      map.set(entry.id, entry);
    }
    return map;
  }, [selectedCapabilityVars]);

  const manifestVarIds = useMemo(() => {
    const vars = runManifest?.variables;
    if (!vars) {
      return new Set<string>();
    }
    return new Set(Object.keys(vars));
  }, [runManifest]);

  const hasRenderableSelection = Boolean(
    model
    && variable
    && (selectedCapabilityVarMap.has(variable) || manifestVarIds.has(variable))
  );
  const selectedVariableDefaultFh = selectedCapabilityVarMap.get(variable)?.defaultFh ?? null;
  const selectedModelLatestOnly = readCapabilityLatestOnly(selectedModelCapability);
  const selectedModelSupportsSampling = readCapabilitySupportsSampling(selectedModelCapability);
  const selectedModelConstraints = (selectedModelCapability?.constraints ?? {}) as Record<string, unknown>;
  const selectedModelDefaultFrameSelection = readCapabilityDefaultFrameSelection(selectedModelCapability);
  const selectedTimeAxisMode = readCapabilityTimeAxisMode(selectedModelCapability);
  const selectionCapabilitiesResolved = Boolean(variable) && selectedCapabilityVarMap.has(variable);
  const selectedVariableRenderSubstrates = selectionCapabilitiesResolved
    ? (selectedCapabilityVarMap.get(variable)?.renderSubstrates ?? ["grid"])
    : [];
  const selectionSupportsVector = selectionCapabilitiesResolved
    && selectedVariableRenderSubstrates.includes("vector");
  const selectionSupportsGrid = selectionCapabilitiesResolved
    && selectedVariableRenderSubstrates.includes("grid");
  const gridOnlySelection = selectionSupportsGrid;
  const prefersGridSubstrate = selectionSupportsGrid;
  const overlayFadeOutZoom = useMemo(() => {
    const start = toNumberOrNull(selectedModelConstraints.overlay_fade_out_zoom_start);
    const end = toNumberOrNull(selectedModelConstraints.overlay_fade_out_zoom_end);
    if (start === null || end === null || end <= start) {
      return null;
    }
    return { start, end };
  }, [selectedModelConstraints.overlay_fade_out_zoom_start, selectedModelConstraints.overlay_fade_out_zoom_end]);

  const frameHours = useMemo(() => {
    const hours = frameRows.map((row) => Number(row.fh)).filter(Number.isFinite);
    return Array.from(new Set(hours)).sort((a, b) => a - b);
  }, [frameRows]);

  const selectableFrameHours = useMemo(
    () => selectableFramesForVariable(frameHours, selectedVariableDefaultFh),
    [frameHours, selectedVariableDefaultFh]
  );

  useEffect(() => {
    const pendingForecastHour = pendingInitialForecastHourRef.current;
    if (!Number.isFinite(pendingForecastHour) || frameHours.length === 0) {
      return;
    }
    const resolved = resolveForecastHour(
      frameHours,
      Number(pendingForecastHour),
      selectedVariableDefaultFh,
      selectedModelDefaultFrameSelection
    );
    setForecastHour(resolved);
    setTargetForecastHour(resolved);
    pendingInitialForecastHourRef.current = null;
  }, [frameHours, selectedVariableDefaultFh, selectedModelDefaultFrameSelection]);

  const frameByHour = useMemo(() => {
    return new Map(frameRows.map((row) => [Number(row.fh), row]));
  }, [frameRows]);

  const regionViews = useMemo(() => {
    return Object.fromEntries(
      Object.entries(regionPresets).map(([id, preset]) => [
        id,
        {
          center: [preset.defaultCenter[0], preset.defaultCenter[1]] as [number, number],
          zoom: preset.defaultZoom,
          bbox: preset.bbox,
          minZoom: preset.minZoom,
          maxZoom: preset.maxZoom,
        },
      ])
    );
  }, [regionPresets]);

  const anchorBatchPoints = useMemo(
    () => anchorBatchPointsFromGeoJson(anchorBaseGeoJson),
    [anchorBaseGeoJson]
  );

  const resetAnchorBatchQueue = useCallback((abortInFlight = false) => {
    anchorBatchPendingHourRef.current = null;
    anchorBatchContextRef.current = null;
    if (abortInFlight && anchorBatchAbortRef.current) {
      anchorBatchAbortRef.current.abort();
    }
    anchorBatchAbortRef.current = null;
    anchorBatchInFlightHourRef.current = null;
    anchorBatchInFlightSelectionKeyRef.current = "";
  }, []);

  const startAnchorBatchRequest = useCallback(
    (requestedHour: number, context: AnchorBatchRequestContext) => {
      if (!Number.isFinite(requestedHour)) {
        return;
      }

      const controller = new AbortController();
      anchorBatchAbortRef.current = controller;
      anchorBatchInFlightHourRef.current = requestedHour;
      anchorBatchInFlightSelectionKeyRef.current = context.selectionKey;

      fetchSampleBatch({
        model: context.model,
        run: context.run,
        variable: context.variable,
        forecastHour: requestedHour,
        points: context.points,
        signal: controller.signal,
      })
        .then((payload) => {
          if (controller.signal.aborted || context.generation !== requestGenerationRef.current) {
            return;
          }
          const latestContext = anchorBatchContextRef.current;
          if (!latestContext || latestContext.selectionKey !== context.selectionKey) {
            return;
          }
          anchorBatchLastAppliedHourRef.current = requestedHour;
          anchorBatchLastAppliedSelectionKeyRef.current = context.selectionKey;
          setAnchorDisplayGeoJson(
            buildAnchorDisplayGeoJson({
      baseCollection: context.baseCollection,
      varKey: context.variable,
      values: payload?.values ?? {},
      units: payload?.units ?? "",
            })
          );
        })
        .catch((error) => {
          if (error instanceof DOMException && error.name === "AbortError") {
            return;
          }
          if (context.generation !== requestGenerationRef.current) {
            return;
          }
          const latestContext = anchorBatchContextRef.current;
          if (!latestContext || latestContext.selectionKey !== context.selectionKey) {
            return;
          }
          console.warn("[anchors] batch sample request failed", {
            model: context.model,
            run: context.run,
            variable: context.variable,
            forecastHour: requestedHour,
            error,
          });
        })
        .finally(() => {
          if (anchorBatchAbortRef.current === controller) {
            anchorBatchAbortRef.current = null;
            anchorBatchInFlightHourRef.current = null;
            anchorBatchInFlightSelectionKeyRef.current = "";
          }

          const latestContext = anchorBatchContextRef.current;
          if (!latestContext || latestContext.selectionKey !== context.selectionKey) {
            return;
          }
          if (latestContext.generation !== requestGenerationRef.current) {
            return;
          }
          if (!latestContext.deferToLatest) {
            anchorBatchPendingHourRef.current = null;
            return;
          }

          const pendingHour = anchorBatchPendingHourRef.current;
          if (!Number.isFinite(pendingHour) || pendingHour === requestedHour) {
            anchorBatchPendingHourRef.current = null;
            return;
          }

          anchorBatchPendingHourRef.current = null;
          startAnchorBatchRequest(pendingHour as number, latestContext);
        });
    },
    []
  );

  const currentFrame = frameByHour.get(forecastHour) ?? frameRows[0] ?? null;
  const frameValidTimesByHour = useMemo(() => {
    const map: Record<number, string> = {};
    for (const row of frameRows) {
      const fh = Number(row?.fh);
      const validTime = frameValidTime(row);
      if (!Number.isFinite(fh) || !validTime) {
        continue;
      }
      map[fh] = validTime;
    }
    const manifestFrames = runManifest?.variables?.[variable]?.frames;
    if (Array.isArray(manifestFrames)) {
      for (const frame of manifestFrames) {
        const fh = Number(frame?.fh);
        const validTime = typeof frame?.valid_time === "string" && frame.valid_time.trim() ? frame.valid_time.trim() : null;
        if (!Number.isFinite(fh) || !validTime || map[fh]) {
          continue;
        }
        map[fh] = validTime;
      }
    }
    return map;
  }, [frameRows, runManifest, variable]);
  const currentFrameValidTimeISO = useMemo(() => {
    return frameValidTimesByHour[forecastHour] ?? frameValidTime(currentFrame) ?? null;
  }, [frameValidTimesByHour, forecastHour, currentFrame]);
  const newestFrameValidTimeISO = useMemo(() => {
    const orderedHours = Object.keys(frameValidTimesByHour)
      .map((key) => Number(key))
      .filter(Number.isFinite)
      .sort((a, b) => a - b);
    if (orderedHours.length === 0) {
      return null;
    }
    return frameValidTimesByHour[orderedHours[orderedHours.length - 1]] ?? null;
  }, [frameValidTimesByHour]);
  const latestRunId = useMemo(() => {
    const manifestLatest =
      run === "latest" && runManifest?.model === model ? (runManifest.run ?? null) : null;
    const runsLatest = pickLatestRunId(runs);
    const availabilityLatest =
      model && capabilities?.availability?.[model]
        ? (capabilities.availability[model].latest_run ?? null)
        : null;
    const fallbackRun = currentFrame?.run ?? frameRows[0]?.run ?? null;
    const candidates = [runsLatest, availabilityLatest, manifestLatest, fallbackRun].filter((value): value is string => Boolean(value));
    return candidates[0] ?? null;
  }, [run, runManifest, model, capabilities, runs, currentFrame, frameRows]);
  const latestGridRunCandidates = useMemo(() => {
    if (!gridOnlySelection || run !== "latest") {
      return [] as string[];
    }
    return Array.from(new Set([latestRunId, ...runs].filter((value): value is string => Boolean(value))));
  }, [gridOnlySelection, latestRunId, run, runs]);
  const resolvedRunForRequests = useMemo(() => {
    if (gridOnlySelection && run === "latest") {
      return resolvedGridLatestRunId ?? (latestRunId ?? "latest");
    }
    return run === "latest" ? (latestRunId ?? "latest") : run;
  }, [gridOnlySelection, latestRunId, resolvedGridLatestRunId, run]);
  const selectionRunKey = gridOnlySelection && run === "latest"
    ? (resolvedGridLatestRunId ?? lastResolvedGridRunRef.current ?? "pending-grid")
    : resolvedRunForRequests;
  const selectionKey = `${model}:${selectionRunKey}:${variable}`;
  const telemetryRunId = gridOnlySelection && run === "latest"
    ? (resolvedGridLatestRunId ?? latestRunId ?? null)
    : (resolvedRunForRequests ?? (run !== "latest" ? run : latestRunId ?? null));
  const apiRoot = API_ORIGIN.replace(/\/$/, "");

  useEffect(() => {
    if (!gridOnlySelection || run !== "latest") {
      setResolvedGridLatestRunId(null);
      lastResolvedGridRunRef.current = null;
    }
  }, [gridOnlySelection, model, run, variable]);

  useEffect(() => {
    if (!prefersGridSubstrate || !hasRenderableSelection || !selectionSupportsGrid) {
      setGridManifest(null);
      return;
    }

    const controller = new AbortController();
    const resolveManifest = async () => {
      if (gridOnlySelection && run === "latest") {
        // Probe all candidate runs in parallel; pick the first (by priority
        // order) that returns a valid manifest.
        const results = await Promise.allSettled(
          latestGridRunCandidates.map((candidateRun) =>
            fetchGridManifest(model, candidateRun, variable, { signal: controller.signal })
              .then((manifest) => ({ candidateRun, manifest })),
          ),
        );
        if (controller.signal.aborted) {
          return;
        }
        for (let i = 0; i < results.length; i++) {
          const result = results[i];
          if (result.status === "fulfilled" && result.value.manifest) {
            setResolvedGridLatestRunId(result.value.candidateRun);
            setGridManifest(result.value.manifest);
            return;
          }
        }
        setResolvedGridLatestRunId(null);
        setGridManifest(null);
        return;
      }

      const manifest = await fetchGridManifest(model, resolvedRunForRequests, variable, { signal: controller.signal });
      if (controller.signal.aborted) {
        return;
      }
      setGridManifest(manifest);
    };

    void resolveManifest().catch(() => {
      if (controller.signal.aborted) {
        return;
      }
      if (gridOnlySelection && run === "latest") {
        setResolvedGridLatestRunId(null);
      }
      setGridManifest(null);
    });

    return () => {
      controller.abort();
    };
  }, [
    hasRenderableSelection,
    latestGridRunCandidates,
    model,
    prefersGridSubstrate,
    region,
    resolvedRunForRequests,
    run,
    gridOnlySelection,
    selectionSupportsGrid,
    telemetryRunId,
    variable,
  ]);
  // Clock tick (30s) so the observed-source freshness badge re-evaluates as
  // real time passes, rather than staying frozen on the initial server value.
  const [freshnessTickMs, setFreshnessTickMs] = useState(() => Date.now());
  useEffect(() => {
    if (selectedTimeAxisMode !== "observed") return;
    const id = window.setInterval(() => setFreshnessTickMs(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, [selectedTimeAxisMode]);

  const observedSourceStatus = useMemo(() => {
    if (selectedTimeAxisMode !== "observed") {
      return null;
    }
    const availability = model ? capabilities?.availability?.[model] : null;

    // When we have live frame data, derive freshness client-side so the badge
    // stays current between capabilities re-fetches (the server value is only
    // fetched once at page load and goes stale).
    if (newestFrameValidTimeISO && frameRows.length > 0) {
      return deriveObservedSourceStatus({
        latestRunAvailable: Boolean(availability?.latest_run),
        latestRunReady: availability?.latest_run_ready,
        newestValidTimeISO: newestFrameValidTimeISO,
        availableFrameCount: frameRows.length,
        nowMs: freshnessTickMs,
      });
    }

    // No frame data yet — fall back to the server-authoritative status from
    // the initial capabilities fetch, or derive from what we have.
    const authoritativeStatus = observedSourceStatusFromAvailability(availability);
    if (authoritativeStatus) {
      return authoritativeStatus;
    }
    return deriveObservedSourceStatus({
      latestRunAvailable: Boolean(availability?.latest_run),
      latestRunReady: availability?.latest_run_ready,
      newestValidTimeISO: newestFrameValidTimeISO,
      availableFrameCount: frameRows.length,
      nowMs: freshnessTickMs,
    });
  }, [selectedTimeAxisMode, model, capabilities, newestFrameValidTimeISO, frameRows.length, freshnessTickMs]);
  useEffect(() => {
    selectionEpochRef.current = selectionEpoch;
  }, [selectionEpoch]);

  useEffect(() => {
    gridReadyFrameUrlsRef.current = new Set();
    gridPlaybackHourRef.current = null;
    setGridReadyVersion(0);
    setIsGridPreloadingForPlay(false);
    setVisibleGridFrameHour(null);
  }, [selectionKey]);

  useEffect(() => {
    setSelectionEpoch((current) => current + 1);
  }, [selectionKey]);

  const runOptions = useMemo<Option[]>(() => {
    return buildRunOptions(runs, latestRunId, selectedTimeAxisMode);
  }, [runs, latestRunId, selectedTimeAxisMode]);

  const gridLod0 = useMemo(() => {
    if (!gridManifest?.lods?.length) {
      return null;
    }
    return gridManifest.lods.find((entry) => Number(entry?.level) === 0) ?? gridManifest.lods[0] ?? null;
  }, [gridManifest]);
  const gridFrameByHour = useMemo(() => {
    const map = new Map<number, NonNullable<typeof gridLod0>["frames"][number]>();
    const frames = Array.isArray(gridLod0?.frames) ? gridLod0.frames : [];
    for (const frame of frames) {
      const fh = Number(frame?.fh);
      if (!Number.isFinite(fh)) {
        continue;
      }
      map.set(fh, frame);
    }
    return map;
  }, [gridLod0]);
  const gridFrameHours = useMemo(() => {
    return Array.from(gridFrameByHour.keys()).sort((a, b) => a - b);
  }, [gridFrameByHour]);
  const canUseGridPlayback = useMemo(() => {
    if (gridFrameHours.length <= 1) {
      return false;
    }
    return gridFrameHours.every((fh) => Boolean(gridFrameByHour.get(fh)?.url));
  }, [gridFrameByHour, gridFrameHours]);
  const canAnimateTimeline = useMemo(() => {
    if (canUseGridPlayback) {
      return true;
    }
    return selectableFrameHours.length > 1;
  }, [canUseGridPlayback, selectableFrameHours]);
  const requestedGridDisplayHour = useMemo(() => {
    const requested = (isPlaying || isGridPreloadingForPlay || isScrubbing || isVariableSwitching)
      ? targetForecastHour
      : forecastHour;
    if (Number.isFinite(requested)) {
      return Number(requested);
    }
    return gridFrameHours[0] ?? null;
  }, [forecastHour, gridFrameHours, isGridPreloadingForPlay, isPlaying, isScrubbing, isVariableSwitching, targetForecastHour]);
  const resolvedGridDisplayHour = useMemo(() => {
    if (gridFrameHours.length === 0) {
      return null;
    }
    const requested = Number.isFinite(requestedGridDisplayHour) ? Number(requestedGridDisplayHour) : gridFrameHours[0];
    return nearestFrame(gridFrameHours, requested);
  }, [gridFrameHours, requestedGridDisplayHour]);
  const activeGridFrame = useMemo(() => {
    if (!Number.isFinite(resolvedGridDisplayHour)) {
      return null;
    }
    return gridFrameByHour.get(Number(resolvedGridDisplayHour)) ?? null;
  }, [gridFrameByHour, resolvedGridDisplayHour]);
  const activeGridFrameUrl = useMemo(() => {
    const frameUrl = activeGridFrame?.url;
    if (!frameUrl) {
      return null;
    }
    return /^https?:\/\//i.test(frameUrl)
      ? frameUrl
      : `${apiRoot}${frameUrl.startsWith("/") ? "" : "/"}${frameUrl}`;
  }, [activeGridFrame, apiRoot]);
  const normalizeGridFrameUrl = useCallback((frameUrl: string | null | undefined): string => {
    const normalized = String(frameUrl ?? "").trim();
    if (!normalized) {
      return "";
    }
    return /^https?:\/\//i.test(normalized)
      ? normalized
      : `${apiRoot}${normalized.startsWith("/") ? "" : "/"}${normalized}`;
  }, [apiRoot]);
  const gridFrameUrlForHour = useCallback((hour: number | null | undefined): string | null => {
    if (!Number.isFinite(hour)) {
      return null;
    }
    const frameUrl = gridFrameByHour.get(Number(hour))?.url;
    if (!frameUrl) {
      return null;
    }
    return normalizeGridFrameUrl(frameUrl);
  }, [gridFrameByHour, normalizeGridFrameUrl]);
  const isGridFrameReady = useCallback((frameUrl: string | null | undefined): boolean => {
    const normalized = normalizeGridFrameUrl(frameUrl);
    if (!normalized) {
      return false;
    }
    return gridReadyFrameUrlsRef.current.has(normalized);
  }, [normalizeGridFrameUrl]);
  const presentedGridDisplayHour = useMemo(() => {
    if (gridFrameHours.length === 0) {
      return null;
    }
    const requestedHourCandidate = Number.isFinite(resolvedGridDisplayHour) ? Number(resolvedGridDisplayHour) : null;
    if (!Number.isFinite(requestedHourCandidate)) {
      return Number.isFinite(visibleGridFrameHour) ? Number(visibleGridFrameHour) : null;
    }
    const requestedHour = Number(requestedHourCandidate);
    if (isGridFrameReady(gridFrameUrlForHour(requestedHour))) {
      return requestedHour;
    }
    if (Number.isFinite(visibleGridFrameHour) && gridFrameByHour.has(Number(visibleGridFrameHour))) {
      return Number(visibleGridFrameHour);
    }

    let nearestReadyHour: number | null = null;
    let nearestDistance = Number.POSITIVE_INFINITY;
    for (const hour of gridFrameHours) {
      if (!isGridFrameReady(gridFrameUrlForHour(hour))) {
        continue;
      }
      const distance = Math.abs(hour - requestedHour);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestReadyHour = hour;
      }
    }
    return nearestReadyHour ?? requestedHour;
  }, [
    gridFrameByHour,
    gridFrameHours,
    gridFrameUrlForHour,
    gridReadyVersion,
    isGridFrameReady,
    resolvedGridDisplayHour,
    visibleGridFrameHour,
  ]);
  const presentedGridFrameUrl = useMemo(() => {
    return gridFrameUrlForHour(presentedGridDisplayHour);
  }, [gridFrameUrlForHour, presentedGridDisplayHour]);
  const countGridAheadReadyFrames = useCallback((currentHour: number, maxAhead: number): number => {
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
      const frameUrl = normalizeGridFrameUrl(gridFrameByHour.get(gridFrameHours[index])?.url);
      if (!isGridFrameReady(frameUrl)) {
        break;
      }
      ready += 1;
    }
    return ready;
  }, [gridFrameByHour, gridFrameHours, isGridFrameReady, normalizeGridFrameUrl]);
  const gridReadyCount = useMemo(() => {
    return gridFrameHours.reduce((count, fh) => {
      const frameUrl = normalizeGridFrameUrl(gridFrameByHour.get(fh)?.url);
      return count + (isGridFrameReady(frameUrl) ? 1 : 0);
    }, 0);
  }, [gridFrameByHour, gridFrameHours, gridReadyVersion, isGridFrameReady, normalizeGridFrameUrl]);
  const gridPlaybackStartHour = useMemo(() => {
    if (gridFrameHours.length === 0) {
      return null;
    }
    const requested = Number.isFinite(targetForecastHour)
      ? Number(targetForecastHour)
      : (Number.isFinite(forecastHour) ? Number(forecastHour) : gridFrameHours[0]);
    return nearestFrame(gridFrameHours, requested);
  }, [forecastHour, gridFrameHours, targetForecastHour]);
  const gridPlaybackAheadReadyCount = useMemo(() => {
    if (!Number.isFinite(gridPlaybackStartHour)) {
      return 0;
    }
    return countGridAheadReadyFrames(Number(gridPlaybackStartHour), GRID_PLAY_START_AHEAD_FRAMES);
  }, [countGridAheadReadyFrames, gridPlaybackStartHour, gridReadyVersion]);
  const isGridPlaybackStartReady = useMemo(() => {
    if (!Number.isFinite(gridPlaybackStartHour)) {
      return false;
    }
    const currentHour = Number(gridPlaybackStartHour);
    const currentUrl = normalizeGridFrameUrl(gridFrameByHour.get(currentHour)?.url);
    if (!isGridFrameReady(currentUrl)) {
      return false;
    }
    const currentIndex = gridFrameHours.indexOf(currentHour);
    if (currentIndex < 0) {
      return false;
    }
    const remainingAhead = Math.max(0, gridFrameHours.length - currentIndex - 1);
    const requiredAhead = Math.min(GRID_PLAY_START_AHEAD_FRAMES, remainingAhead);
    return gridPlaybackAheadReadyCount >= requiredAhead;
  }, [
    gridFrameByHour,
    gridFrameHours,
    gridPlaybackAheadReadyCount,
    gridPlaybackStartHour,
    gridReadyVersion,
    isGridFrameReady,
    normalizeGridFrameUrl,
  ]);
  const isGridLowMidActive = useMemo(() => {
    return Boolean(
      gridManifest
      && gridLod0
      && Array.isArray(gridManifest.bbox)
      && gridManifest.bbox.length === 4
      && presentedGridFrameUrl
    );
  }, [gridLod0, gridManifest, presentedGridFrameUrl]);
  const isGridPlayable = useMemo(() => {
    return canUseGridPlayback;
  }, [canUseGridPlayback]);

  useEffect(() => {
    forecastHourRef.current = forecastHour;
  }, [forecastHour]);

  useEffect(() => {
    targetForecastHourRef.current = targetForecastHour;
  }, [targetForecastHour]);

  useLayoutEffect(() => {
    modelRef.current = model;
    variableRef.current = variable;
  }, [model, variable]);

  useEffect(() => {
    mapZoomRef.current = mapZoom;
  }, [mapZoom]);

  useEffect(() => {
    const targetView = pendingMapViewRef.current;
    const map = mapInstanceRef.current;
    if (!targetView || !map || !isMapReady || mapViewHydratedRef.current) {
      return;
    }

    let cancelled = false;
    const applyHydratedView = () => {
      if (cancelled || mapViewHydratedRef.current) {
        return;
      }
      map.jumpTo({
        center: [targetView.lon, targetView.lat],
        zoom: targetView.z,
      });
      const center = map.getCenter();
      mapViewRef.current = {
        lat: center.lat,
        lon: center.lng,
        z: map.getZoom(),
      };
      viewportSignatureRef.current = viewportSignatureFromState(mapViewRef.current);
      mapViewHydratedRef.current = true;
      pendingMapViewRef.current = null;
      setMapViewTick((current) => current + 1);
    };

    const fallbackTimer = window.setTimeout(applyHydratedView, 800);
    map.once("idle", applyHydratedView);

    return () => {
      cancelled = true;
      window.clearTimeout(fallbackTimer);
      map.off("idle", applyHydratedView);
    };
  }, [isMapReady, region, regionPresets]);

  useEffect(() => {
    if (permalinkHydratedRef.current || !bootstrapHydrated || !mapViewHydratedRef.current) {
      return;
    }
    permalinkHydratedRef.current = true;
    suppressNextUrlSyncRef.current = true;
    setPermalinkHydrated(true);
    if (typeof window !== "undefined") {
      lastSyncedPermalinkSearchRef.current = window.location.search;
    }
  }, [bootstrapHydrated, mapViewTick]);

  const visibleGridOverlayHour = useMemo(() => {
    if (!isGridLowMidActive) {
      return null;
    }
    if (Number.isFinite(visibleGridFrameHour)) {
      return Number(visibleGridFrameHour);
    }
    if (Number.isFinite(resolvedGridDisplayHour)) {
      return Number(resolvedGridDisplayHour);
    }
    return Number.isFinite(forecastHour) ? forecastHour : null;
  }, [forecastHour, isGridLowMidActive, resolvedGridDisplayHour, visibleGridFrameHour]);
  const mapForecastHour = Number.isFinite(visibleGridOverlayHour) ? Number(visibleGridOverlayHour) : forecastHour;
  const visibleOverlayHour = Number.isFinite(visibleGridOverlayHour) ? Number(visibleGridOverlayHour) : forecastHour;

  // During a variable switch the old variable's imagery is still on screen;
  // keep its paint settings in effect until the new variable is promoting.
  const displayedOverlayVariable = isVariableSwitching ? (visualVariable || variable) : variable;
  const contourGeoJsonUrl = useMemo(() => {
    return null;
  }, []);
  const vectorGeoJsonUrl = useMemo(() => {
    if (!selectionSupportsVector || !model || !variable) {
      return null;
    }
    return buildVectorLayerUrl({
      apiRoot,
      model,
      run: resolvedRunForRequests,
      variable,
      frame: currentFrame,
      layerKey: "primary",
    });
  }, [apiRoot, currentFrame, model, resolvedRunForRequests, selectionSupportsVector, variable]);
  const vectorPrefetchUrls = useMemo(() => {
    if (!selectionSupportsVector || !model || !variable || frameRows.length <= 1) {
      return [] as string[];
    }
    if (model === "spc") {
      return [] as string[];
    }
    const currentHour = Number.isFinite(forecastHour) ? Number(forecastHour) : Number(currentFrame?.fh);
    const orderedRows = [...frameRows].sort((a, b) => Number(a.fh) - Number(b.fh));
    const pivotIndex = orderedRows.findIndex((row) => Number(row.fh) === currentHour);
    const candidateRows = pivotIndex >= 0
      ? orderedRows.filter((_, index) => Math.abs(index - pivotIndex) === 1)
      : orderedRows.slice(1, 3);
    const urls: string[] = [];
    for (const row of candidateRows) {
      const url = buildVectorLayerUrl({
        apiRoot,
        model,
        run: resolvedRunForRequests,
        variable,
        frame: row,
        layerKey: "primary",
      });
      if (url && url !== vectorGeoJsonUrl && !urls.includes(url)) {
        urls.push(url);
      }
    }
    return urls;
  }, [apiRoot, currentFrame, forecastHour, frameRows, model, resolvedRunForRequests, selectionSupportsVector, variable, vectorGeoJsonUrl]);

  const rawLegend = useMemo(() => {
    const normalizedMeta = extractLegendMeta(currentFrame) ?? extractLegendMeta(frameRows[0] ?? null);
    return buildLegend(normalizedMeta, opacity);
  }, [currentFrame, frameRows, opacity]);
  const legendHoldKey = useMemo(
    () => `${selectedTimeAxisMode}:${model}:${variable}`,
    [selectedTimeAxisMode, model, variable]
  );
  const [stableObservedLegend, setStableObservedLegend] = useState<LegendPayload | null>(null);
  const stableObservedLegendKeyRef = useRef("");

  useEffect(() => {
    if (stableObservedLegendKeyRef.current !== legendHoldKey) {
      stableObservedLegendKeyRef.current = legendHoldKey;
      setStableObservedLegend(rawLegend);
      return;
    }
    if (rawLegend) {
      setStableObservedLegend(rawLegend);
    } else if (selectedTimeAxisMode !== "observed") {
      setStableObservedLegend(null);
    }
  }, [legendHoldKey, rawLegend, selectedTimeAxisMode]);

  const legend = useMemo(() => {
    if (rawLegend) {
      return rawLegend;
    }
    if (selectedTimeAxisMode === "observed" && stableObservedLegend) {
      return { ...stableObservedLegend, opacity };
    }
    return null;
  }, [opacity, rawLegend, selectedTimeAxisMode, stableObservedLegend]);

  const effectiveRunId = currentFrame?.run ?? resolvedRunForRequests;
  const runDateTimeISO = runIdToIso(effectiveRunId);
  const hoverSampleFrame = currentFrame ?? frameRows[0] ?? null;
  const hoverSampleHour = selectedModelSupportsSampling && selectionSupportsGrid
    ? (Number.isFinite(presentedGridDisplayHour) ? Number(presentedGridDisplayHour) : Number.NaN)
    : (Number.isFinite(hoverSampleFrame?.fh) ? Number(hoverSampleFrame?.fh) : Number.NaN);
  const hoverSamplingEnabled = selectedModelSupportsSampling
    && Boolean(variable)
    && Number.isFinite(hoverSampleHour)
    && Boolean((effectiveRunId ?? "").trim())
    && (
      selectionSupportsGrid
        ? Boolean(presentedGridFrameUrl)
        : Boolean(hoverSampleFrame?.has_cog)
    );
  const hoverSampleRun = (effectiveRunId ?? "").trim();

  // ── Hover-for-data tooltip ──────────────────────────────────────────
  const { tooltip, onHover, onHoverEnd } = useSampleTooltip({
    model: hoverSamplingEnabled ? model : "",
    run: hoverSamplingEnabled ? hoverSampleRun : "",
    varId: hoverSamplingEnabled ? variable : "",
    fh: hoverSamplingEnabled ? hoverSampleHour : Number.NaN,
  });
  const [vectorHoverTooltip, setVectorHoverTooltip] = useState<Exclude<typeof tooltip, null> | null>(null);
  const handleMapHover = useCallback((lat: number, lon: number, x: number, y: number, hoverTooltip?: Exclude<typeof tooltip, null>) => {
    if (hoverTooltip?.kind === "label") {
      setVectorHoverTooltip(hoverTooltip);
      onHoverEnd();
      return;
    }
    setVectorHoverTooltip(null);
    onHover(lat, lon, x, y);
  }, [onHover, onHoverEnd]);
  const handleMapHoverEnd = useCallback(() => {
    setVectorHoverTooltip(null);
    onHoverEnd();
  }, [onHoverEnd]);
  const activeTooltip = vectorHoverTooltip ?? tooltip;

  useEffect(() => {
    const pendingVarSwitch = pendingVariableSwitchRef.current;
    if (
      !pendingVarSwitch
      || pendingVarSwitch.toVariableId !== variable
      || pendingVarSwitch.expectedSelectionKey !== loadedFramesKey
      || !activeGridFrameUrl
    ) {
      return;
    }
    setVariableSwitchState((current) => {
      if (!current || current.toVariable !== variable) {
        return current;
      }
      return {
        ...current,
        visualState: "warming_new",
      };
    });
  }, [activeGridFrameUrl, loadedFramesKey, variable]);

  const isScrubLoading = false;

  const cancelPendingVariableSwitch = useCallback((
    reason: "selection-mismatch" | "timeout",
    options?: { forceTiles?: boolean }
  ): boolean => {
    void reason;
    void options;
    const pendingVarSwitch = pendingVariableSwitchRef.current;
    if (!pendingVarSwitch) {
      return false;
    }
    pendingVariableSwitchRef.current = null;
    setVariableSwitchState(null);
    setVisualVariable(variable);
    return true;
  }, [variable]);

  const finalizePendingVariableSwitch = useCallback((
    visibleAt: number,
    options?: Record<string, never>
  ): boolean => {
    void options;
    const pendingVarSwitch = pendingVariableSwitchRef.current;
    if (
      !pendingVarSwitch
      || pendingVarSwitch.toVariableId !== variable
      || pendingVarSwitch.expectedSelectionKey !== loadedFramesKey
    ) {
      return false;
    }

    pendingVariableSwitchRef.current = null;
    setVariableSwitchState((current) => {
      if (!current || current.toVariable !== variable) {
        return null;
      }
      return {
        ...current,
        visualState: "promoting_new",
      };
    });
    setVisualVariable(variable);

    setVariableSwitchState(null);
    return true;
  }, [loadedFramesKey, variable]);

  const clearFrameStatusTimer = useCallback(() => {
    if (frameStatusTimerRef.current !== null) {
      window.clearTimeout(frameStatusTimerRef.current);
      frameStatusTimerRef.current = null;
    }
    setFrameStatusMessage(null);
  }, []);

  const showTransientFrameStatus = useCallback((message: string) => {
    setFrameStatusMessage(message);
    if (frameStatusTimerRef.current !== null) {
      window.clearTimeout(frameStatusTimerRef.current);
    }
    frameStatusTimerRef.current = window.setTimeout(() => {
      frameStatusTimerRef.current = null;
      setFrameStatusMessage(null);
    }, FRAME_STATUS_BADGE_MS);
  }, []);

  useEffect(() => {
    requestGenerationRef.current += 1;
  }, [model, run, variable]);

  useEffect(() => {
    if (!variableSwitchState) {
      if (visualVariable !== variable) {
        setVisualVariable(variable);
      }
      return;
    }

    if (variableSwitchState.toVariable !== variable) {
      cancelPendingVariableSwitch("selection-mismatch", { forceTiles: true });
    }
  }, [cancelPendingVariableSwitch, variable, visualVariable, variableSwitchState]);

  useEffect(() => {
    if (
      !variableSwitchState
      || variableSwitchState.toVariable !== variable
      || variableSwitchState.visualState === "promoting_new"
    ) {
      return;
    }

    const elapsedMs = performance.now() - variableSwitchState.startedAt;
    const remainingMs = VARIABLE_SWITCH_TIMEOUT_MS - elapsedMs;
    if (remainingMs <= 0) {
      cancelPendingVariableSwitch("timeout", { forceTiles: true });
      return;
    }

    const timeoutId = window.setTimeout(() => {
      cancelPendingVariableSwitch("timeout", { forceTiles: true });
    }, remainingMs);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [cancelPendingVariableSwitch, variable, variableSwitchState]);

  const startPendingLoopStartMetric = useCallback(() => {
    pendingLoopStartMetricRef.current = {
      startedAt: performance.now(),
    };
  }, []);

  useEffect(() => {
    datasetGenerationRef.current += 1;
    pendingLoopStartMetricRef.current = null;
    setScrubRequestedHour(null);
  }, [
    // Only the three selector values that uniquely identify a dataset change.
    // frameHours.length is derived state, and including it would cause a second
    // reset firing when frames were cleared then re-populated.
    model,
    resolvedRunForRequests,
    variable,
  ]);

  // Clear any pending variable_switch metric when the model or run changes —
  // those are full dataset resets where the switch context is no longer valid.
  // We do NOT clear on variable change because that's what starts the metric.
  useEffect(() => {
    pendingVariableSwitchRef.current = null;
    setVariableSwitchState(null);
    setVisualVariable(variable);
  }, [model, resolvedRunForRequests]);

  const trackFirstViewerFrame = useCallback((frameHour: number | null) => {
    if (firstViewerFrameTrackedRef.current) {
      return;
    }

    const hasSelectionIdentity =
      hasRenderableSelection
      && loadedFramesKey.length > 0
      && Boolean(modelRef.current)
      && Boolean(variableRef.current);

    if (!hasSelectionIdentity) {
      pendingFirstViewerFrameRef.current = true;
      pendingFirstViewerFrameHourRef.current = Number.isFinite(frameHour) ? Number(frameHour) : null;
      return;
    }

    firstViewerFrameTrackedRef.current = true;
    pendingFirstViewerFrameRef.current = false;
    pendingFirstViewerFrameHourRef.current = null;
    setFirstWeatherFramePainted(true);
    const durationMs = performance.now() - viewerMountedAtRef.current;
    if (!Number.isFinite(durationMs) || durationMs < 0) {
      return;
    }
    trackRumDiagnosticMetric({
      metric_name: "first_overlay_visible_duration",
      metric_value: durationMs,
      metric_unit: "ms",
      model_id: modelRef.current || null,
      variable_id: variableRef.current || null,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(frameHour) ? frameHour : null,
    });
  }, [telemetryRunId, region, hasRenderableSelection, loadedFramesKey]);

  useEffect(() => {
    if (firstViewerFrameTrackedRef.current) {
      return;
    }
    if (!pendingFirstViewerFrameRef.current) {
      return;
    }
    if (!hasRenderableSelection || loadedFramesKey.length === 0) {
      return;
    }
    trackFirstViewerFrame(pendingFirstViewerFrameHourRef.current);
  }, [hasRenderableSelection, loadedFramesKey, trackFirstViewerFrame]);

  const requestForecastHour = useCallback(
    (requestedHour: number, reason: ForecastHourChangeReason = "standard") => {
      const inferDirection = (nextHour: number): 1 | -1 | 0 => {
        const currentHour = forecastHourRef.current;
        if (!Number.isFinite(currentHour)) {
          return 0;
        }
        if (nextHour > currentHour) {
          return 1;
        }
        if (nextHour < currentHour) {
          return -1;
        }
        return 0;
      };

      // Snap the requested hour to the nearest grid frame hour.  If the
      // requested hour is visible on the slider (in selectableFrameHours)
      // but the grid manifest hasn't caught up yet (not in gridFrameHours),
      // honour the slider value so the user isn't bounced back to an older
      // hour while the grid manifest refreshes.
      const snapHour = (hour: number): number => {
        if (gridFrameHours.length > 0) {
          const nearest = nearestFrame(gridFrameHours, hour);
          if (nearest === hour) {
            return hour;
          }
          // The grid manifest doesn't have this exact hour.  If the slider
          // does, trust the slider — the grid manifest will catch up on the
          // next refresh cycle and the texture will load shortly.
          if (selectableFrameHours.includes(hour)) {
            return hour;
          }
          return nearest;
        }
        return hour;
      };

      if (reason === "standard") {
        setScrubRequestedHour(null);
        setScrubCommitIntent(null);
        pendingScrubHourRef.current = null;
        scrubPhase0aRef.current = emptyScrubPhase0aSnapshot();
        const nextGridHour = snapHour(requestedHour);
        setTargetForecastHour(nextGridHour);
        return;
      }

      if (reason === "scrub-commit") {
        const scrubSnapshot = scrubPhase0aRef.current;
        const commitStartedAt = performance.now();
        const treatCommitAsFrameChange = scrubSnapshot.liveEventCount <= 1;
        const scrubTraceMeta: Record<string, unknown> = {
          trace_phase: "scrub_commit",
          scrub_live_event_count: scrubSnapshot.liveEventCount,
          scrub_live_superseded_count: scrubSnapshot.supersededCount,
          scrub_classification: treatCommitAsFrameChange ? "single_seek" : "drag_commit",
          scrub_live_to_commit_ms: Number.isFinite(scrubSnapshot.liveStartedAt)
            ? Math.max(0, Math.round(commitStartedAt - (scrubSnapshot.liveStartedAt as number)))
            : null,
        };

        setScrubRequestedHour(null);
        pendingScrubHourRef.current = null;
        scrubPhase0aRef.current = emptyScrubPhase0aSnapshot();

        const snappedGridHour = snapHour(requestedHour);
        setScrubCommitIntent({
          hour: snappedGridHour,
          direction: inferDirection(snappedGridHour),
          startedAt: Date.now(),
        });
        void scrubTraceMeta;
        void treatCommitAsFrameChange;
        setTargetForecastHour(snappedGridHour);
        return;
      }

      const previousRequestedHour = pendingScrubHourRef.current;
      setScrubCommitIntent(null);
      const now = performance.now();
      const scrubTrace = scrubPhase0aRef.current;
      if (!Number.isFinite(scrubTrace.liveStartedAt)) {
        scrubTrace.liveStartedAt = now;
      }
      scrubTrace.liveEventCount += 1;
      if (Number.isFinite(previousRequestedHour) && previousRequestedHour !== requestedHour) {
        scrubTrace.supersededCount += 1;
      }
      scrubTrace.lastRequestedHour = requestedHour;

      setScrubRequestedHour(requestedHour);
      pendingScrubHourRef.current = requestedHour;

      // Apply the scrub immediately — the slider already throttles at ~48ms,
      // so an additional rAF coalesce just adds latency.
      const nextGridHour = snapHour(requestedHour);
      setTargetForecastHour(nextGridHour);
    },
    [gridFrameHours, selectableFrameHours]
  );

  useEffect(() => {
    const controller = new AbortController();
    const generation = requestGenerationRef.current;

    async function bootstrap() {
      setLoading(true);
      setError(null);
      try {
        const requestedModel = initialPermalink.model?.trim();
        const requestedVariable = initialPermalink.var?.trim();
        const requestedRegion = initialPermalink.region?.trim();
        const requestedRun = initialPermalink.run?.trim();

        const [capabilitiesData, regionPresetData] = await Promise.all([
          fetchCapabilities({ signal: controller.signal }),
          fetchRegionPresets({ signal: controller.signal }),
        ]);
        if (controller.signal.aborted || generation !== requestGenerationRef.current) {
          return;
        }

        setCapabilities(capabilitiesData);
        setAnchorBaseGeoJson(null);
        setAnchorDisplayGeoJson(null);

        const supportedModelIds = capabilitiesData.supported_models.filter(
          (modelId) => Boolean(capabilitiesData.model_catalog?.[modelId])
        );
        const visibleModelIds = supportedModelIds;
        const modelRows = normalizeModelRows(capabilitiesData, visibleModelIds);
        const orderedVisibleModelIds = modelRows.map((entry) => entry.id);
        const preferredDefaultModel = orderedVisibleModelIds.includes("hrrr") ? "hrrr" : "";
        const availableModelId = orderedVisibleModelIds.find((modelId) => {
          const availability = capabilitiesData.availability?.[modelId];
          return Boolean(availability?.latest_run);
        });
        const nextModel = requestedModel && orderedVisibleModelIds.includes(requestedModel)
          ? requestedModel
          : (preferredDefaultModel || availableModelId || orderedVisibleModelIds[0] || "");
        const modelOptions = modelRows.map((entry) => ({
          value: entry.id,
          label: entry.displayName || entry.id,
        }));
        setModels(modelOptions);
        setModel(nextModel);

        const modelCapability = nextModel ? capabilitiesData.model_catalog[nextModel] : null;
        const capabilityVars = normalizeCapabilityVarRows(modelCapability);
        const variableOptions = makeVariableOptions(capabilityVars);
        const variableIds = variableOptions.map((opt) => opt.value);
        const defaultVarKey = String(modelCapability?.defaults?.default_var_key ?? "").trim();
        const nextVariable = requestedVariable && variableIds.includes(requestedVariable)
          ? requestedVariable
          : (variableIds.includes(defaultVarKey) ? defaultVarKey : (variableIds[0] ?? ""));
        setVariables(variableOptions);
        setVariable(nextVariable);

        setRegionPresets(regionPresetData);
        const regionIds = Object.keys(regionPresetData);
        const regionOptions = regionIds.map((id) => ({
          value: id,
          label: makeRegionLabel(id, regionPresetData[id]),
        }));
        setRegions(regionOptions);
        const canonicalRegion = String(
          modelCapability?.constraints?.canonical_region
          ?? modelCapability?.canonical_region
          ?? MAP_VIEW_DEFAULTS.region
        ).trim();
        const nextRegion = requestedRegion && regionIds.includes(requestedRegion)
          ? requestedRegion
          : pickPreferred(regionIds, canonicalRegion || MAP_VIEW_DEFAULTS.region);
        setRegion(nextRegion);

        setRun(requestedRun || "latest");
        setRuns([]);
        setRunManifest(null);
        setFrameRows([]);
      } catch (err) {
        if (controller.signal.aborted || generation !== requestGenerationRef.current) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load capabilities");
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          setBootstrapHydrated(true);
        }
      }
    }

    bootstrap();
    return () => {
      controller.abort();
    };
  }, [initialPermalink]);

  useEffect(() => {
    const anchorsReadyToLoad = deferNonCriticalBootstrapEnabled
      ? (bootstrapHydrated && firstWeatherFramePainted)
      : bootstrapHydrated;
    if (!anchorsReadyToLoad) {
      return;
    }
    if (anchorBaseGeoJson) {
      return;
    }

    const controller = new AbortController();
    fetchAnchorFeatureCollection({ signal: controller.signal })
      .then((anchorData) => {
        if (controller.signal.aborted) {
          return;
        }
        setAnchorBaseGeoJson(anchorData);
        setAnchorDisplayGeoJson(anchorData ? buildInactiveAnchorFeatureCollection(anchorData) : null);
      })
      .catch((error) => {
        if (controller.signal.aborted) {
          return;
        }
        console.warn("[anchors] deferred bootstrap fetch failed", error);
      });

    return () => {
      controller.abort();
    };
  }, [deferNonCriticalBootstrapEnabled, bootstrapHydrated, firstWeatherFramePainted, anchorBaseGeoJson]);

  useEffect(() => {
    if (!model) return;
    const controller = new AbortController();
    const generation = requestGenerationRef.current;

    async function loadRunsAndVars() {
      setError(null);
      try {
        const shouldFetchRuns = runsLoadedForModelRef.current !== model;
        const runDataPromise = shouldFetchRuns
          ? fetchRuns(model, { signal: controller.signal })
          : Promise.resolve(runs);
        const manifestRunKey = run === "latest"
          ? ((gridOnlySelection && resolvedGridLatestRunId) ? resolvedGridLatestRunId : run)
          : run;
        const [runDataRaw, requestedManifest] = await Promise.all([
          runDataPromise,
          fetchManifest(model, manifestRunKey, { signal: controller.signal }).catch(() => null),
        ]);
        if (controller.signal.aborted || generation !== requestGenerationRef.current) {
          return;
        }

        const runData = sortRunIdsDescending(runDataRaw);
        const nextRun = run !== "latest" && runData.includes(run) ? run : "latest";
        let manifestData = requestedManifest;
        const nextManifestRunKey = nextRun === "latest"
          ? ((gridOnlySelection && resolvedGridLatestRunId) ? resolvedGridLatestRunId : nextRun)
          : nextRun;
        if (!manifestData && nextManifestRunKey !== manifestRunKey) {
          manifestData = await fetchManifest(model, nextManifestRunKey, { signal: controller.signal }).catch(() => null);
          if (controller.signal.aborted || generation !== requestGenerationRef.current) {
            return;
          }
        }

        if (shouldFetchRuns) {
          runsLoadedForModelRef.current = model;
          setRuns(runData);
          setCapabilities((current) => withUpdatedLatestRun(current, model, pickLatestRunId(runData), runData));
        }
        setRun(nextRun);

        setRunManifest(manifestData);
        const baseCapabilityVars = selectedCapabilityVars;
        const resolvedVars = manifestData
          ? capabilityVarsForManifest(manifestData.variables, baseCapabilityVars)
          : baseCapabilityVars;
        const variableOptions = makeVariableOptions(resolvedVars);
        const variableIds = variableOptions.map((opt) => opt.value);
        const defaultVarKey = String(selectedModelCapability?.defaults?.default_var_key ?? "").trim();
        const nextVar = variableIds.includes(defaultVarKey)
          ? defaultVarKey
          : (variableIds[0] ?? "");
        setVariables(variableOptions);
        setVariable((prev) => (variableIds.includes(prev) ? prev : nextVar));
      } catch (err) {
        if (controller.signal.aborted || generation !== requestGenerationRef.current) return;
        setRunManifest(null);
        setError(err instanceof Error ? err.message : "Failed to load run manifest");
      }
    }

    loadRunsAndVars();
    return () => {
      controller.abort();
    };
  }, [model, run, runs, selectedCapabilityVars, selectedModelCapability, gridOnlySelection, resolvedGridLatestRunId]);

  useEffect(() => {
    setFrameRows([]);
    setForecastHour(Number.POSITIVE_INFINITY);
    setTargetForecastHour(Number.POSITIVE_INFINITY);
    setLoadedFramesKey("");
    setVariableSwitchState(null);
    setVisualVariable(variable);
  }, [model, run]);

  useEffect(() => {
    setFrameRows([]);
    setLoadedFramesKey("");
  }, [selectionKey]);

  // NOTE: gridManifest is NOT eagerly nullified on [model, run, variable]
  // changes. The fetch effect (above, at the useEffect that depends on
  // variable/model/run) will atomically swap the manifest once the new one
  // arrives, keeping the grid WebGL layer visible with the previous
  // variable's data during the fetch. This eliminates the blank-map flash
  // that occurred when the manifest was cleared before the new one loaded.

  useEffect(() => {
    if (!model || !variable || !hasRenderableSelection) return;
    if (gridOnlySelection && run === "latest" && !resolvedGridLatestRunId) {
      setFrameRows([]);
      setLoadedFramesKey("");
      return;
    }
    const controller = new AbortController();
    const generation = requestGenerationRef.current;

    async function loadFrames() {
      setError(null);
      let hydratedFromManifest = false;
      const manifestMatchesSelection =
        Boolean(runManifest) &&
        runManifest?.model === model &&
        (run === "latest" || runManifest?.run === run || runManifest?.run === resolvedRunForRequests);
      if (manifestMatchesSelection) {
        const { rows, hasFrameList } = resolveManifestFrames(runManifest, variable);
        if (hasFrameList) {
          setVariableSwitchState((current) => {
            if (!current || current.toVariable !== variable) {
              return current;
            }
            return {
              ...current,
              visualState: "warming_new",
            };
          });
          setFrameRows((prevRows) => mergeManifestRowsWithPrevious(rows, prevRows, loadedFramesKey === selectionKey));
          setLoadedFramesKey(`${model}:${resolvedRunForRequests}:${variable}`);
          const frames = rows.map((row) => Number(row.fh)).filter(Number.isFinite);
          setForecastHour((prev) =>
            resolveForecastHour(frames, prev, selectedVariableDefaultFh, selectedModelDefaultFrameSelection)
          );
          setTargetForecastHour((prev) =>
            resolveForecastHour(frames, prev, selectedVariableDefaultFh, selectedModelDefaultFrameSelection)
          );
          hydratedFromManifest = true;
        }
      }

      try {
        const framesRunKey = gridOnlySelection && run === "latest"
          ? resolvedGridLatestRunId
          : (run === "latest" ? "latest" : resolvedRunForRequests);
        if (!framesRunKey) {
          return;
        }
        const rows = await fetchFrames(model, framesRunKey, variable, { signal: controller.signal });
        if (controller.signal.aborted || generation !== requestGenerationRef.current) return;
        setVariableSwitchState((current) => {
          if (!current || current.toVariable !== variable) {
            return current;
          }
          return {
            ...current,
            visualState: "warming_new",
          };
        });
        // Merge with existing frame rows rather than hard-replacing.  The
        // manifest hydration path may have already populated a full set of
        // expected forecast hours, while fetchFrames only returns hours that
        // have COGs ready.  A hard replace would contract the slider, causing
        // it to snap to a high hour on still-populating runs.
        //
        // We use a functional updater so we can access the previous rows AND
        // capture the merged result for resolveForecastHour below.
        let mergedRows: FrameRow[] = rows;
        setFrameRows((prevRows) => {
          if (prevRows.length === 0) {
            mergedRows = rows;
            return rows;
          }
          // Build a map from the new rows for quick lookup.
          const newByHour = new Map<number, FrameRow>();
          for (const row of rows) {
            const fh = Number(row.fh);
            if (Number.isFinite(fh)) {
              newByHour.set(fh, row);
            }
          }
          // Keep any previous rows that aren't in the fetch response (they
          // came from the manifest and represent expected-but-not-yet-ready
          // hours).  Prefer the fetched version when both exist.
          const merged = new Map<number, FrameRow>();
          for (const row of prevRows) {
            const fh = Number(row.fh);
            if (Number.isFinite(fh)) {
              merged.set(fh, row);
            }
          }
          for (const [fh, row] of newByHour) {
            merged.set(fh, row);
          }
          const result = Array.from(merged.values()).sort(
            (a, b) => Number(a.fh) - Number(b.fh),
          );
          mergedRows = result;
          return result;
        });
        setLoadedFramesKey(`${model}:${resolvedRunForRequests}:${variable}`);
        // Use the merged frame set so resolveForecastHour sees ALL expected
        // hours (including manifest-only rows), not just COG-ready ones.
        // Note: React processes functional updaters synchronously within the
        // same synchronous block, so `mergedRows` is populated by this point.
        const frames = mergedRows.map((row) => Number(row.fh)).filter(Number.isFinite);
        setForecastHour((prev) =>
          resolveForecastHour(frames, prev, selectedVariableDefaultFh, selectedModelDefaultFrameSelection)
        );
        setTargetForecastHour((prev) =>
          resolveForecastHour(frames, prev, selectedVariableDefaultFh, selectedModelDefaultFrameSelection)
        );
      } catch (err) {
        if (controller.signal.aborted || generation !== requestGenerationRef.current) return;
        if (!hydratedFromManifest) {
          setLoadedFramesKey("");
          setError(err instanceof Error ? err.message : "Failed to load frames");
          setFrameRows([]);
          setVariableSwitchState(null);
        }
      }
    }

    loadFrames();
    return () => {
      controller.abort();
    };
  }, [
    model,
    run,
    variable,
    resolvedRunForRequests,
    runManifest,
    selectedVariableDefaultFh,
    selectedModelDefaultFrameSelection,
    hasRenderableSelection,
    gridOnlySelection,
    loadedFramesKey,
    resolvedGridLatestRunId,
    selectionKey,
  ]);

  useEffect(() => {
    const generation = requestGenerationRef.current;

    if (!anchorBaseGeoJson) {
      anchorSelectionKeyRef.current = selectionKey;
      anchorBatchLastAppliedHourRef.current = null;
      anchorBatchLastAppliedSelectionKeyRef.current = "";
      resetAnchorBatchQueue(true);
      setAnchorDisplayGeoJson(null);
      return;
    }

    if (anchorSelectionKeyRef.current !== selectionKey) {
      anchorSelectionKeyRef.current = selectionKey;
      anchorBatchLastAppliedHourRef.current = null;
      anchorBatchLastAppliedSelectionKeyRef.current = "";
      resetAnchorBatchQueue(true);
      setAnchorDisplayGeoJson(buildInactiveAnchorFeatureCollection(anchorBaseGeoJson));
    }

    if (model === "mrms" || (variable && resolveAnchorDisplayRule(variable).mode === "hidden")) {
      anchorBatchLastAppliedHourRef.current = null;
      anchorBatchLastAppliedSelectionKeyRef.current = "";
      resetAnchorBatchQueue(true);
      setAnchorDisplayGeoJson(buildInactiveAnchorFeatureCollection(anchorBaseGeoJson));
      return;
    }

    if (
      !hasRenderableSelection
      || !model
      || !variable
      || !Number.isFinite(visibleOverlayHour)
      || anchorBatchPoints.length === 0
      || loadedFramesKey !== selectionKey
    ) {
      anchorBatchContextRef.current = null;
      return;
    }

    const context: AnchorBatchRequestContext = {
      selectionKey,
      generation,
      model,
      run: resolvedRunForRequests,
      variable,
      baseCollection: anchorBaseGeoJson,
      points: anchorBatchPoints,
      deferToLatest: isScrubbing || isPlaying || isGridPreloadingForPlay,
    };

    anchorBatchContextRef.current = context;

    if (!context.deferToLatest) {
      anchorBatchPendingHourRef.current = null;
      if (
        anchorBatchLastAppliedSelectionKeyRef.current === selectionKey
        && anchorBatchLastAppliedHourRef.current === visibleOverlayHour
        && anchorBatchInFlightHourRef.current === null
      ) {
        return;
      }
      if (
        anchorBatchAbortRef.current
        && anchorBatchInFlightSelectionKeyRef.current === selectionKey
        && anchorBatchInFlightHourRef.current === visibleOverlayHour
      ) {
        return;
      }
      if (anchorBatchAbortRef.current) {
        resetAnchorBatchQueue(true);
        anchorBatchContextRef.current = context;
      }
      startAnchorBatchRequest(visibleOverlayHour, context);
      return;
    }

    if (anchorBatchAbortRef.current && anchorBatchInFlightSelectionKeyRef.current === selectionKey) {
      if (anchorBatchInFlightHourRef.current === visibleOverlayHour) {
        anchorBatchPendingHourRef.current = null;
        return;
      }
      anchorBatchPendingHourRef.current = visibleOverlayHour;
      return;
    }

    if (
      anchorBatchLastAppliedSelectionKeyRef.current === selectionKey
      && anchorBatchLastAppliedHourRef.current === visibleOverlayHour
    ) {
      anchorBatchPendingHourRef.current = null;
      return;
    }

    anchorBatchPendingHourRef.current = null;
    startAnchorBatchRequest(visibleOverlayHour, context);
  }, [
    anchorBaseGeoJson,
    anchorBatchPoints,
    hasRenderableSelection,
    isGridPreloadingForPlay,
    isPlaying,
    isScrubbing,
    loadedFramesKey,
    model,
    resetAnchorBatchQueue,
    resolvedRunForRequests,
    selectionKey,
    startAnchorBatchRequest,
    variable,
    visibleOverlayHour,
  ]);

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsPageVisible(!document.hidden);
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  useEffect(() => {
    if (!selectedModelLatestOnly || run === "latest") {
      return;
    }
    setRun("latest");
    setNewRunNotice(null);
  }, [selectedModelLatestOnly, run]);

  useEffect(() => {
    if (!model || !variable || !hasRenderableSelection || run !== "latest" || !isPageVisible) {
      return;
    }

    let cancelled = false;
    let tickController: AbortController | null = null;

    const interval = window.setInterval(() => {
      tickController?.abort();
      tickController = new AbortController();
      void (async () => {
        try {
          const nextRuns = sortRunIdsDescending(await fetchRuns(model, { signal: tickController?.signal }));
          if (cancelled || tickController?.signal.aborted) {
            return;
          }
          const nextLatestRunId = pickLatestRunId(nextRuns);
          setRuns((prevRuns) => (areStringArraysEqual(prevRuns, nextRuns) ? prevRuns : nextRuns));
          setCapabilities((current) => withUpdatedLatestRun(current, model, nextLatestRunId, nextRuns));

          const currentlyViewedRun = resolvedRunForRequests;
          if (
            !selectedModelLatestOnly
            && !gridOnlySelection
            && currentlyViewedRun
            && nextLatestRunId
            && nextLatestRunId !== currentlyViewedRun
          ) {
            setRun(currentlyViewedRun);
            setNewRunNotice({
              model,
              previousRunId: currentlyViewedRun,
              latestRunId: nextLatestRunId,
            });
            return;
          }

          const manifestMatchesSelection =
            Boolean(runManifest) &&
            runManifest?.model === model &&
            (runManifest?.run === "latest" || runManifest?.run === currentlyViewedRun || runManifest?.run === nextLatestRunId);

          if (manifestMatchesSelection) {
            const manifestRunKey = gridOnlySelection && run === "latest"
              ? (resolvedGridLatestRunId ?? run)
              : run;
            const manifestData = await fetchManifest(model, manifestRunKey, { signal: tickController.signal });
            if (cancelled || tickController?.signal.aborted) {
              return;
            }
            setRunManifest((prev) => {
              if (prev && JSON.stringify(prev) === JSON.stringify(manifestData)) {
                return prev;
              }
              return manifestData;
            });
            const capabilityVars = capabilityVarsForManifest(manifestData.variables, selectedCapabilityVars);
            if (capabilityVars.length > 0) {
              const variableOptions = makeVariableOptions(capabilityVars);
              const variableIds = variableOptions.map((opt) => opt.value);
              const defaultVarKey = String(selectedModelCapability?.defaults?.default_var_key ?? "").trim();
              const nextVar = variableIds.includes(defaultVarKey)
                ? defaultVarKey
                : (variableIds[0] ?? "");
              setVariables(variableOptions);
              setVariable((prev) => (variableIds.includes(prev) ? prev : nextVar));
            }
            const { rows, hasFrameList } = resolveManifestFrames(manifestData, variable);

            // Refresh the grid manifest BEFORE extending frameRows / the
            // slider.  gridFrameHours (derived from gridManifest) is used by
            // the scrub handler to snap the requested hour.  If we update the
            // slider first, the user can see new hours and try to scrub to
            // them while gridFrameHours still lacks them, causing a snap-back
            // to the nearest old hour.
            if (prefersGridSubstrate && selectionSupportsGrid) {
              const gridRunKey = gridOnlySelection && run === "latest"
                ? (resolvedGridLatestRunId ?? manifestRunKey)
                : resolvedRunForRequests;
              const nextGridManifest = await fetchGridManifest(model, gridRunKey, variable, { signal: tickController.signal });
              if (cancelled || tickController?.signal.aborted) {
                return;
              }
              if (nextGridManifest) {
                setGridManifest((prev) => {
                  if (prev && JSON.stringify(prev) === JSON.stringify(nextGridManifest)) {
                    return prev;
                  }
                  return nextGridManifest;
                });
              }
            }

            if (hasFrameList) {
              setFrameRows((prevRows) => {
                const merged = mergeManifestRowsWithPrevious(rows, prevRows, loadedFramesKey === selectionKey);
                return merged.length === prevRows.length ? prevRows : merged;
              });
              const frames = rows.map((row) => Number(row.fh)).filter(Number.isFinite);
              setForecastHour((prev) =>
                resolveForecastHour(frames, prev, selectedVariableDefaultFh, selectedModelDefaultFrameSelection)
              );
              setTargetForecastHour((prev) =>
                resolveForecastHour(frames, prev, selectedVariableDefaultFh, selectedModelDefaultFrameSelection)
              );
            }
            return;
          }

          const framesRunKey = gridOnlySelection && run === "latest"
            ? resolvedGridLatestRunId
            : run;
          if (!framesRunKey) {
            return;
          }
          const rows = await fetchFrames(model, framesRunKey, variable, { signal: tickController.signal });
          if (cancelled || tickController?.signal.aborted) {
            return;
          }

          // Refresh the grid manifest before updating frameRows so that
          // gridFrameHours is in sync with the slider when the user scrubs.
          if (prefersGridSubstrate && selectionSupportsGrid) {
            const gridRunKey = gridOnlySelection && run === "latest"
              ? (resolvedGridLatestRunId ?? framesRunKey)
              : resolvedRunForRequests;
            const nextGridManifest = await fetchGridManifest(model, gridRunKey, variable, { signal: tickController.signal });
            if (cancelled || tickController?.signal.aborted) {
              return;
            }
            if (nextGridManifest) {
              setGridManifest((prev) => {
                if (prev && JSON.stringify(prev) === JSON.stringify(nextGridManifest)) {
                  return prev;
                }
                return nextGridManifest;
              });
            }
          }

          setFrameRows((prevRows) => (rows.length === prevRows.length ? prevRows : rows));
          const frames = rows.map((row) => Number(row.fh)).filter(Number.isFinite);
          setForecastHour((prev) =>
            resolveForecastHour(frames, prev, selectedVariableDefaultFh, selectedModelDefaultFrameSelection)
          );
          setTargetForecastHour((prev) =>
            resolveForecastHour(frames, prev, selectedVariableDefaultFh, selectedModelDefaultFrameSelection)
          );
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") {
            return;
          }
          // Background refresh should not interrupt active UI.
        }
      })();
    }, 30000);

    return () => {
      cancelled = true;
      tickController?.abort();
      window.clearInterval(interval);
    };
  }, [model, run, variable, resolvedRunForRequests, runManifest, isPageVisible, selectedCapabilityVars, selectedModelCapability, selectedVariableDefaultFh, selectedModelDefaultFrameSelection, hasRenderableSelection, loadedFramesKey, selectionKey, selectedModelLatestOnly, gridOnlySelection, resolvedGridLatestRunId]);

  useEffect(() => {
    if (!model || run === "latest" || !isPageVisible) {
      return;
    }

    let cancelled = false;
    let tickController: AbortController | null = null;

    const interval = window.setInterval(() => {
      tickController?.abort();
      tickController = new AbortController();
      void fetchRuns(model, { signal: tickController.signal })
        .then((nextRunsRaw) => {
          if (cancelled || tickController?.signal.aborted) {
            return;
          }
          const nextRuns = sortRunIdsDescending(nextRunsRaw);
          const nextLatestRunId = pickLatestRunId(nextRuns);
          setRuns((prevRuns) => (areStringArraysEqual(prevRuns, nextRuns) ? prevRuns : nextRuns));
          setCapabilities((current) => withUpdatedLatestRun(current, model, nextLatestRunId, nextRuns));
          setNewRunNotice((current) => {
            if (!current || current.model !== model || !nextLatestRunId) {
              return current;
            }
            if (current.latestRunId === nextLatestRunId) {
              return current;
            }
            return {
              ...current,
              latestRunId: nextLatestRunId,
            };
          });
        })
        .catch((err) => {
          if (err instanceof DOMException && err.name === "AbortError") {
            return;
          }
          // Background refresh should not interrupt active UI.
        });
    }, 30000);

    return () => {
      cancelled = true;
      tickController?.abort();
      window.clearInterval(interval);
    };
  }, [model, run, isPageVisible]);

  useEffect(() => {
    if (!isPlaying || !isGridPlayable || gridFrameHours.length === 0) {
      gridPlaybackHourRef.current = null;
      return;
    }

    let rafId: number | null = null;
    let previousTs = performance.now();
    let accumulatedMs = 0;
    /** Tracks continuous stall time (ms) on a single unready frame. */
    let stallMs = 0;
    /** Index of the frame we're stalled on, to reset the counter on advance. */
    let stalledOnIndex = -1;
    /** Tracks time spent waiting for look-ahead frames when the next frame IS ready. */
    let lookAheadWaitMs = 0;
    /** Maximum time (ms) to wait for look-ahead frames before advancing anyway. */
    const LOOKAHEAD_GRACE_MS = 80;

    const tick = (now: number) => {
      const currentHour = gridPlaybackHourRef.current
        ?? (Number.isFinite(targetForecastHourRef.current) ? targetForecastHourRef.current : forecastHourRef.current);
      const currentIndex = gridFrameHours.indexOf(currentHour);
      if (currentIndex < 0) {
        const firstHour = gridFrameHours[0];
        if (Number.isFinite(firstHour)) {
          gridPlaybackHourRef.current = firstHour;
          setTargetForecastHour(firstHour);
        }
        previousTs = now;
        rafId = window.requestAnimationFrame(tick);
        return;
      }
      const deltaMs = Math.max(0, now - previousTs);
      previousTs = now;
      accumulatedMs = Math.min(accumulatedMs + deltaMs, AUTOPLAY_TICK_MS * 4);

      while (accumulatedMs >= AUTOPLAY_TICK_MS) {
        const nextIndex = currentIndex + 1;
        if (nextIndex >= gridFrameHours.length) {
          gridPlaybackHourRef.current = null;
          setIsPlaying(false);
          return;
        }
        const nextHour = gridFrameHours[nextIndex];
        const nextUrl = String(gridFrameByHour.get(nextHour)?.url ?? "").trim();
        if (isGridFrameReady(nextUrl)) {
          // Look-ahead: only advance if the next AUTOPLAY_READY_AHEAD frames
          // beyond this one are also ready (or we're near the end).  This
          // prevents advancing into a gap that will immediately stall.
          let aheadReady = true;
          const lookAheadEnd = Math.min(nextIndex + AUTOPLAY_READY_AHEAD, gridFrameHours.length - 1);
          for (let li = nextIndex + 1; li <= lookAheadEnd; li++) {
            const laHour = gridFrameHours[li];
            const laUrl = String(gridFrameByHour.get(laHour)?.url ?? "").trim();
            if (!isGridFrameReady(laUrl)) {
              aheadReady = false;
              break;
            }
          }

          if (aheadReady || stallMs > 0 || lookAheadWaitMs >= LOOKAHEAD_GRACE_MS) {
            // Advance: the look-ahead is satisfied, we already stalled on an
            // unready frame, or we've waited long enough for look-ahead.
            accumulatedMs -= AUTOPLAY_TICK_MS;
            gridPlaybackHourRef.current = nextHour;
            setTargetForecastHour(nextHour);
            // Reset stall trackers on successful advance.
            stallMs = 0;
            stalledOnIndex = -1;
            lookAheadWaitMs = 0;
            break;
          }
          // Look-ahead not satisfied — accumulate wait time but don't block
          // indefinitely.  After LOOKAHEAD_GRACE_MS the next tick will advance.
          lookAheadWaitMs += deltaMs;
          break;
        }

        // Next frame isn't ready — reset look-ahead wait and accumulate stall time.
        lookAheadWaitMs = 0;
        if (stalledOnIndex !== nextIndex) {
          stalledOnIndex = nextIndex;
          stallMs = 0;
        }
        stallMs += deltaMs;

        // After stalling long enough, try skipping ahead within a window.
        if (stallMs >= AUTOPLAY_STALL_SKIP_MS) {
          const maxStep = Math.min(AUTOPLAY_SKIP_WINDOW, gridFrameHours.length - 1 - currentIndex);
          for (let step = 2; step <= maxStep; step += 1) {
            const candidateHour = gridFrameHours[currentIndex + step];
            const candidateUrl = String(gridFrameByHour.get(candidateHour)?.url ?? "").trim();
            if (isGridFrameReady(candidateUrl)) {
              accumulatedMs -= AUTOPLAY_TICK_MS;
              gridPlaybackHourRef.current = candidateHour;
              setTargetForecastHour(candidateHour);
              stallMs = 0;
              stalledOnIndex = -1;
              break;
            }
          }
        }
        break;
      }

      rafId = window.requestAnimationFrame(tick);
    };

    rafId = window.requestAnimationFrame(tick);
    return () => {
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId);
      }
      gridPlaybackHourRef.current = null;
    };
  }, [gridFrameByHour, gridFrameHours, isGridFrameReady, isGridPlayable, isPlaying]);

  useEffect(() => {
    if (!isGridPreloadingForPlay) {
      return;
    }
    if (!isGridPlayable || gridFrameHours.length === 0 || !Number.isFinite(gridPlaybackStartHour)) {
      setIsGridPreloadingForPlay(false);
      return;
    }

    const currentHour = Number(gridPlaybackStartHour);
    const currentUrl = normalizeGridFrameUrl(gridFrameByHour.get(currentHour)?.url);
    if (!currentUrl) {
      return;
    }

    const currentReady = isGridFrameReady(currentUrl);
    const stalledMs = pendingLoopStartMetricRef.current
      ? Math.max(0, performance.now() - pendingLoopStartMetricRef.current.startedAt)
      : 0;
    const allowStallStart = currentReady && stalledMs >= GRID_PLAY_STALL_MS;

    if (!isGridPlaybackStartReady && !allowStallStart) {
      return;
    }

    setIsGridPreloadingForPlay(false);
    gridPlaybackHourRef.current = currentHour;
    if (allowStallStart && !isGridPlaybackStartReady) {
      showTransientFrameStatus("Starting grid playback");
    }
    setIsPlaying(true);
  }, [
    gridFrameByHour,
    gridFrameHours,
    gridPlaybackStartHour,
    gridReadyVersion,
    isGridFrameReady,
    isGridPlayable,
    isGridPlaybackStartReady,
    isGridPreloadingForPlay,
    normalizeGridFrameUrl,
    showTransientFrameStatus,
  ]);

  useEffect(() => {
    if (!isPlaying || canUseGridPlayback || selectableFrameHours.length <= 1) {
      return;
    }

    const timer = window.setInterval(() => {
      const currentHour = Number.isFinite(forecastHourRef.current)
        ? Number(forecastHourRef.current)
        : selectableFrameHours[0];
      const currentIndex = selectableFrameHours.indexOf(currentHour);
      if (currentIndex < 0) {
        setTargetForecastHour(selectableFrameHours[0]);
        return;
      }
      const nextIndex = currentIndex + 1;
      if (nextIndex >= selectableFrameHours.length) {
        setIsPlaying(false);
        return;
      }
      setTargetForecastHour(selectableFrameHours[nextIndex]);
    }, AUTOPLAY_TICK_MS);

    return () => {
      window.clearInterval(timer);
    };
  }, [canUseGridPlayback, isPlaying, selectableFrameHours]);

  useEffect(() => {
    if (selectableFrameHours.length === 0 && isPlaying) {
      setIsPlaying(false);
    }
  }, [selectableFrameHours, isPlaying]);

  useEffect(() => {
    if (!isPlaying) {
      clearFrameStatusTimer();
    }
  }, [isPlaying, clearFrameStatusTimer]);

  const handleSetIsPlaying = useCallback((value: boolean) => {
    if (!value) {
      pendingLoopStartMetricRef.current = null;
      gridPlaybackHourRef.current = null;
      setIsPlaying(false);
      setIsGridPreloadingForPlay(false);
      return;
    }
    if (loading || selectableFrameHours.length === 0) {
      pendingLoopStartMetricRef.current = null;
      return;
    }
    if (!canAnimateTimeline) {
      pendingLoopStartMetricRef.current = null;
      setIsPlaying(false);
      setIsGridPreloadingForPlay(false);
      showTransientFrameStatus("Animation unavailable for this selection");
      return;
    }

    startPendingLoopStartMetric();
    captureProductAnalyticsEvent("animation_started", {
      model_id: model || null,
      variable_id: variable || null,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
    });

    if (canUseGridPlayback) {
      const startHour = Number.isFinite(gridPlaybackStartHour)
        ? Number(gridPlaybackStartHour)
        : (Number.isFinite(targetForecastHour)
          ? targetForecastHour
          : (Number.isFinite(forecastHour) ? forecastHour : null));
      gridPlaybackHourRef.current = startHour;
      if (Number.isFinite(startHour) && isGridPlaybackStartReady) {
        setIsGridPreloadingForPlay(false);
        setIsPlaying(true);
        showTransientFrameStatus("Starting grid playback");
        return;
      }
      setIsPlaying(false);
      setIsGridPreloadingForPlay(true);
      showTransientFrameStatus("Buffering grid frames");
      return;
    }
    setIsGridPreloadingForPlay(false);
    setIsPlaying(true);
    showTransientFrameStatus("Starting playback");
  }, [
    loading,
    selectableFrameHours.length,
    canAnimateTimeline,
    canUseGridPlayback,
    gridPlaybackStartHour,
    isGridPlaybackStartReady,
    showTransientFrameStatus,
    startPendingLoopStartMetric,
    model,
    variable,
    telemetryRunId,
    region,
    targetForecastHour,
    forecastHour,
    selectableFrameHours.length,
  ]);

  useEffect(() => {
    if (isPlaying && !canAnimateTimeline) {
      setIsPlaying(false);
      setIsGridPreloadingForPlay(false);
      showTransientFrameStatus("Animation unavailable for this selection");
    }
  }, [canAnimateTimeline, isPlaying, showTransientFrameStatus]);

  const handleZoomRoutingSignal = useCallback((payload: { zoom: number; gestureActive: boolean }) => {
    setMapZoom(payload.zoom);
    setZoomGestureActive(payload.gestureActive);
  }, []);

  const handleMapReady = useCallback((map: MapLibreMap) => {
    mapInstanceRef.current = map;
    const center = map.getCenter();
    mapViewRef.current = {
      lat: center.lat,
      lon: center.lng,
      z: map.getZoom(),
    };
    viewportSignatureRef.current = viewportSignatureFromState(mapViewRef.current);
    setMapViewTick((current) => current + 1);
    setIsMapReady(true);
    if (!firstMapRenderTrackedRef.current) {
      firstMapRenderTrackedRef.current = true;
      const durationMs = performance.now() - viewerMountedAtRef.current;
      if (Number.isFinite(durationMs) && durationMs >= 0) {
        trackRumDiagnosticMetric({
          metric_name: "first_map_render_duration",
          metric_value: durationMs,
          metric_unit: "ms",
          model_id: modelRef.current || null,
          variable_id: variableRef.current || null,
          run_id: telemetryRunId,
          region_id: region || null,
          forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
        });
      }
    }
  }, [telemetryRunId, region, forecastHour]);

  const handleViewportChange = useCallback((payload: { lat: number; lon: number; z: number }) => {
    if (!Number.isFinite(payload.lat) || !Number.isFinite(payload.lon) || !Number.isFinite(payload.z)) {
      return;
    }
    mapViewRef.current = {
      lat: payload.lat,
      lon: payload.lon,
      z: payload.z,
    };
    viewportSignatureRef.current = viewportSignatureFromState(mapViewRef.current);
    setMapViewTick((current) => current + 1);
  }, []);

  const handleGridFrameVisible = useCallback((payload: {
    frameHour: number;
    selectionEpoch?: number;
    selectionKey?: string;
  }) => {
    if (
      (payload.selectionEpoch !== undefined && payload.selectionEpoch !== selectionEpochRef.current)
      || (payload.selectionKey !== undefined && payload.selectionKey !== selectionKey)
    ) {
      return;
    }
    if (Number.isFinite(payload.frameHour)) {
      setVisibleGridFrameHour(payload.frameHour);
    }
    finalizePendingVariableSwitch(performance.now());
    trackFirstViewerFrame(Number.isFinite(payload.frameHour) ? payload.frameHour : forecastHour);
  }, [finalizePendingVariableSwitch, forecastHour, selectionKey, trackFirstViewerFrame]);
  const handleGridFrameReady = useCallback((frameUrl: string) => {
    const normalized = normalizeGridFrameUrl(frameUrl);
    if (!normalized) {
      return;
    }
    if (gridReadyFrameUrlsRef.current.has(normalized)) {
      return;
    }
    gridReadyFrameUrlsRef.current.add(normalized);
    bumpGridReadyVersion();
  }, [bumpGridReadyVersion, normalizeGridFrameUrl]);
  const handleGridFrameEvicted = useCallback((frameUrl: string) => {
    const normalized = normalizeGridFrameUrl(frameUrl);
    if (!normalized) {
      return;
    }
    if (!gridReadyFrameUrlsRef.current.has(normalized)) {
      return;
    }
    gridReadyFrameUrlsRef.current.delete(normalized);
    bumpGridReadyVersion();
  }, [bumpGridReadyVersion, normalizeGridFrameUrl]);

  const handleRegionChange = useCallback((nextRegion: string) => {
    setRegion(nextRegion);
    captureProductAnalyticsEvent("region_selected", {
      model_id: model || null,
      variable_id: variable || null,
      run_id: telemetryRunId,
      region_id: nextRegion,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
    });
  }, [model, variable, telemetryRunId, forecastHour]);

  const handleModelChange = useCallback((nextModel: string) => {
    setNewRunNotice((current) => (current?.model === nextModel ? current : null));
    setRun("latest");
    setRuns([]);
    setRunManifest(null);
    setFrameRows([]);
    setModel(nextModel);
    captureProductAnalyticsEvent("model_selected", {
      model_id: nextModel,
      variable_id: variable || null,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
    });
  }, [variable, telemetryRunId, region, forecastHour]);

  const handleRunChange = useCallback((nextRun: string) => {
    setRun(nextRun);
    setNewRunNotice((current) => {
      if (!current || current.model !== model) {
        return current;
      }
      if (nextRun === "latest" || nextRun === current.latestRunId) {
        return null;
      }
      return current.previousRunId === nextRun ? current : null;
    });
  }, [model]);

  const handleViewLatestRun = useCallback(() => {
    setRun("latest");
    setNewRunNotice(null);
  }, []);

  const handleVariableChange = useCallback((nextVariable: string) => {
    if (!nextVariable || nextVariable === variable) {
      return;
    }
    const fromVariable = visualVariable || variable;
    pendingVariableSwitchRef.current = {
      toVariableId: nextVariable,
      expectedSelectionKey: `${model}:${resolvedRunForRequests}:${nextVariable}`,
    };
    setVariableSwitchState({
      fromVariable,
      toVariable: nextVariable,
      startedAt: performance.now(),
      visualState: "holding_old",
    });
    setVariable(nextVariable);
    captureProductAnalyticsEvent("variable_selected", {
      model_id: model || null,
      variable_id: nextVariable,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
    });
  }, [model, variable, visualVariable, telemetryRunId, region, forecastHour]);

  useEffect(() => {
    if (
      viewerOpenedTrackedRef.current
      || !firstWeatherFramePainted
      || !hasRenderableSelection
      || !model
      || !variable
    ) {
      return;
    }
    viewerOpenedTrackedRef.current = true;
    captureProductAnalyticsEvent("viewer_opened", {
      model_id: model,
      variable_id: variable,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
    });
  }, [firstWeatherFramePainted, hasRenderableSelection, model, variable, telemetryRunId, region, forecastHour]);

  useEffect(() => {
    if (isPlaying && isScrubbing) {
      setIsScrubbing(false);
    }
  }, [isPlaying, isScrubbing]);

  useEffect(() => {
    if (!scrubCommitIntent || !Number.isFinite(forecastHour)) {
      return;
    }
    if (forecastHour !== scrubCommitIntent.hour) {
      return;
    }
    setScrubCommitIntent(null);
  }, [forecastHour, scrubCommitIntent]);

  // When the user starts scrubbing, cancel any pending buffering-recovery auto-restart
  // so it cannot preempt the in-progress scrub and re-lock the slider.
  useEffect(() => {
    if (!isScrubbing) {
      setScrubRequestedHour(null);
    }
  }, [isScrubbing]);

  useEffect(() => {
    return () => {
      clearFrameStatusTimer();
      mapInstanceRef.current = null;
      if (scrubRafRef.current !== null) {
        window.cancelAnimationFrame(scrubRafRef.current);
      }
      resetAnchorBatchQueue(true);
    };
  }, [clearFrameStatusTimer, resetAnchorBatchQueue]);

  useEffect(() => {
    if (selectableFrameHours.length === 0) {
      return;
    }

    const nextTarget = nearestFrame(selectableFrameHours, targetForecastHour);
    if (nextTarget === forecastHour) {
      return;
    }
    setForecastHour(nextTarget);
  }, [targetForecastHour, forecastHour, selectableFrameHours]);

  const controlsIsPlaying = isPlaying || isGridPreloadingForPlay;
  const preloadBufferedCount = Math.max(0, Math.min(gridReadyCount, gridFrameHours.length));
  const preloadTotal = gridFrameHours.length;
  const preloadPercent = preloadTotal > 0
    ? Math.round((preloadBufferedCount / preloadTotal) * 100)
    : 0;
  const showBufferStatus = isGridPreloadingForPlay && gridFrameHours.length > 0;
  const bufferStatusText = `Buffering grid ${preloadBufferedCount}/${preloadTotal}`;

  const resolvedForecastHourPermalink = Number.isFinite(forecastHour)
    ? forecastHour
    : pendingInitialForecastHourRef.current;
  const selectedModelLabel = useMemo(() => {
    const fromOptions = models.find((entry) => entry.value === model)?.label;
    return fromOptions ?? model;
  }, [models, model]);
  const selectedRunLabel = useMemo(() => {
    if (selectedTimeAxisMode === "valid") {
      const issuedAtLabel = formatIssuedTimeISO(frameIssueTime(currentFrame) ?? frameIssueTime(frameRows[0] ?? null));
      if (issuedAtLabel) {
        return `Issued ${issuedAtLabel}`;
      }
    }
    if (
      run === "latest"
      && gridOnlySelection
      && resolvedGridLatestRunId
      && latestRunId
      && resolvedGridLatestRunId !== latestRunId
    ) {
      return `Latest grid-ready (${formatRunLabel(resolvedGridLatestRunId, selectedTimeAxisMode)})`;
    }
    const fromOptions = runOptions.find((entry) => entry.value === run)?.label;
    if (fromOptions) {
      return fromOptions;
    }
    if (run === "latest") {
      return latestRunLabel(latestRunId, selectedTimeAxisMode);
    }
    return formatRunLabel(run, selectedTimeAxisMode);
  }, [runOptions, run, latestRunId, selectedTimeAxisMode, gridOnlySelection, resolvedGridLatestRunId, currentFrame, frameRows]);
  const latestAvailableRunLabel = useMemo(() => {
    return latestRunId ? formatRunLabel(latestRunId, selectedTimeAxisMode) : null;
  }, [latestRunId, selectedTimeAxisMode]);
  const hasNewerRunAvailable = Boolean(
    !selectedModelLatestOnly
    && 
    latestRunId
    && run !== "latest"
    && run !== latestRunId
  );
  const runNoticeForCurrentModel = newRunNotice?.model === model ? newRunNotice : null;
  const showNewRunNotice = Boolean(
    runNoticeForCurrentModel
    && latestRunId
    && latestRunId === runNoticeForCurrentModel.latestRunId
    && run === runNoticeForCurrentModel.previousRunId
  );

  useEffect(() => {
    setNewRunNotice((current) => {
      if (!current) {
        return current;
      }
      if (current.model !== model) {
        return null;
      }
      if (run === "latest" || !latestRunId || run === latestRunId) {
        return null;
      }
      if (current.previousRunId !== run) {
        return current.latestRunId === latestRunId ? current : { ...current, latestRunId };
      }
      return current.latestRunId === latestRunId ? current : { ...current, latestRunId };
    });
  }, [model, run, latestRunId]);

  const selectedVariableLabel = useMemo(() => {
    const fromOptions = variables.find((entry) => entry.value === variable)?.label;
    if (fromOptions) {
      return fromOptions;
    }
    const fromCapabilities = selectedCapabilityVarMap.get(variable)?.displayName;
    if (fromCapabilities) {
      return fromCapabilities;
    }
    const manifestVariable = runManifest?.variables?.[variable];
    return manifestVariable?.display_name ?? manifestVariable?.name ?? manifestVariable?.label ?? variable;
  }, [variables, variable, selectedCapabilityVarMap, runManifest]);
  const selectedRegionLabel = useMemo(() => {
    const fromOptions = regions.find((entry) => entry.value === region)?.label;
    return fromOptions ?? regionPresets[region]?.label ?? region;
  }, [regions, regionPresets, region]);
  const buildScreenshotExportState = useCallback((): ScreenshotExportState | null => {
    const map = mapInstanceRef.current;
    if (!map) {
      return null;
    }
    const center = map.getCenter();
    const zoom = map.getZoom();
    const container = map.getContainer();
    const viewportWidth = container.clientWidth;
    const viewportHeight = container.clientHeight;
    if (!Number.isFinite(center.lng) || !Number.isFinite(center.lat) || !Number.isFinite(zoom)) {
      return null;
    }
    let capturedMapDataUrl: string | undefined;
    try {
      capturedMapDataUrl = map.getCanvas().toDataURL("image/png");
    } catch (error) {
      console.warn("[screenshot] Failed to snapshot live map canvas; falling back to offscreen export.", error);
    }
    const anchors = getActiveAnchorLabels(anchorDisplayGeoJson, zoom)
      .map((anchor) => {
        const projected = map.project(anchor.lngLat);
        return {
          x: Math.round(projected.x),
          y: Math.round(projected.y),
          label: anchor.label,
          cityName: anchor.cityName,
        };
      })
      .filter((anchor) => Number.isFinite(anchor.x) && Number.isFinite(anchor.y));

    const style = buildMapStyle(contourGeoJsonUrl, vectorGeoJsonUrl, basemapMode);

    return {
      style,
      center: [center.lng, center.lat],
      zoom,
      bearing: map.getBearing(),
      pitch: map.getPitch(),
      viewportWidth,
      viewportHeight,
      model: selectedModelLabel || model || "Model",
      run: selectedRunLabel || run || "Run",
      variable: {
        key: variable || "variable",
        label: selectedVariableLabel || variable || "Variable",
      },
      fh: Number.isFinite(forecastHour) ? Math.round(forecastHour) : 0,
      timeAxisMode: selectedTimeAxisMode,
      validTimeISO: currentFrameValidTimeISO,
      sourceStatusLabel: observedSourceStatus?.label ?? null,
      region: {
        id: region || "region",
        label: selectedRegionLabel || region || "Region",
      },
      animationEnabled: false,
      capturedMapDataUrl,
      anchors,
    };
  }, [
    selectedModelLabel,
    model,
    selectedRunLabel,
    run,
    opacity,
    variable,
    overlayFadeOutZoom,
    contourGeoJsonUrl,
    basemapMode,
    anchorDisplayGeoJson,
    selectedVariableLabel,
    forecastHour,
    selectedTimeAxisMode,
    currentFrameValidTimeISO,
    observedSourceStatus,
    region,
    selectedRegionLabel,
  ]);

  const handleOpenShareModal = useCallback(() => {
    const permalink = typeof window !== "undefined" ? window.location.href : "";
    const runForSummary = gridOnlySelection && run === "latest"
      ? resolvedRunForRequests
      : (run === "latest" ? (latestRunId ?? "latest") : run);
    const mapView = mapViewRef.current;
    const capabilityVariableLabel = selectedCapabilityVarMap.get(variable)?.displayName ?? null;
    const manifestVariable = runManifest?.variables?.[variable];
    const manifestVariableLabel = manifestVariable?.display_name ?? manifestVariable?.name ?? manifestVariable?.label ?? null;
    const preferredVariableLabel = capabilityVariableLabel ?? manifestVariableLabel;
    const fallbackPayload = buildFallbackSharePayload({
      modelLabel: selectedModelLabel || model || "Model",
      runLabel: selectedRunLabel || runForSummary || "Run",
      variableLabel: selectedVariableLabel || variable || "Variable",
      forecastHour,
      timeAxisMode: selectedTimeAxisMode,
      validTimeISO: currentFrameValidTimeISO,
      permalink,
    });

    setSharePayload(fallbackPayload);
    setIsShareModalOpen(true);
    captureProductAnalyticsEvent("share_clicked", {
      model_id: model || null,
      variable_id: variable || null,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
    });

    void import("@/lib/share-summary")
      .then(({ buildShareSummary }) => {
        const summaries = buildShareSummary({
          modelId: model || "model",
          runId: runForSummary || "latest",
          variableId: variable || "var",
          variableDisplayName: preferredVariableLabel,
          regionId: region || "region",
          regionLabel: regionPresets[region]?.label ?? null,
          forecastHour: Number.isFinite(forecastHour) ? forecastHour : null,
          timeAxisMode: selectedTimeAxisMode,
          validTimeISO: currentFrameValidTimeISO,
          centerLat: Number.isFinite(mapView.lat) ? mapView.lat : null,
          centerLon: Number.isFinite(mapView.lon) ? mapView.lon : null,
          zoom: Number.isFinite(mapView.z) ? mapView.z : null,
          animationEnabled: controlsIsPlaying,
        });
        setSharePayload({
          permalink,
          summary: summaries.shortSummary,
          detailsSummary: summaries.detailsSummary,
        });
      })
      .catch(() => {
        // Leave the fallback payload in place on import/build errors.
      });
  }, [
    forecastHour,
    gridOnlySelection,
    latestRunId,
    model,
    region,
    regionPresets,
    resolvedRunForRequests,
    run,
    runManifest,
    selectedCapabilityVarMap,
    selectedModelLabel,
    selectedRunLabel,
    selectedTimeAxisMode,
    selectedVariableLabel,
    variable,
    currentFrameValidTimeISO,
    controlsIsPlaying,
  ]);

  useEffect(() => {
    if (!permalinkHydrated || typeof window === "undefined") {
      return;
    }
    if (suppressNextUrlSyncRef.current) {
      suppressNextUrlSyncRef.current = false;
      lastSyncedPermalinkSearchRef.current = window.location.search;
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void import("@/lib/permalink").then(({ buildPermalinkSearch, replaceUrlQuery }) => {
        if (cancelled) {
          return;
        }
        const mapView = mapViewRef.current;
        const search = buildPermalinkSearch({
          model: model || undefined,
          run: run || undefined,
          var: variable || undefined,
          fh: Number.isFinite(resolvedForecastHourPermalink)
            ? Number(resolvedForecastHourPermalink)
            : undefined,
          region: region || undefined,
          lat: mapView.lat,
          lon: mapView.lon,
          z: mapView.z,
        });
        if (search === lastSyncedPermalinkSearchRef.current || search === window.location.search) {
          lastSyncedPermalinkSearchRef.current = search;
          return;
        }
        replaceUrlQuery(search);
        lastSyncedPermalinkSearchRef.current = search;
      });
    }, PERMALINK_SYNC_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [
    permalinkHydrated,
    model,
    run,
    variable,
    resolvedForecastHourPermalink,
    region,
    mapViewTick,
  ]);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <WeatherToolbar
        region={region}
        onRegionChange={handleRegionChange}
        model={model}
        onModelChange={handleModelChange}
        run={run}
        onRunChange={handleRunChange}
        variable={variable}
        onVariableChange={handleVariableChange}
        regions={regions}
        models={models}
        runs={runOptions}
        variables={variables}
        disabled={loading || models.length === 0}
        pointLabelsEnabled={pointLabelsEnabled}
        onPointLabelsEnabledChange={setPointLabelsEnabled}
        legendVisible={legendVisible}
        onLegendVisibleChange={(nextVisible) => {
          setLegendVisible(nextVisible);
          if (nextVisible) {
            captureProductAnalyticsEvent("legend_opened", {
              model_id: model || null,
              variable_id: variable || null,
              run_id: telemetryRunId,
              region_id: region || null,
              forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
            });
          }
        }}
        basemapMode={basemapMode}
        onBasemapModeChange={setBasemapMode}
        opacity={opacity}
        onOpacityChange={setOpacity}
        onPostToTwf={handleOpenShareModal}
        layoutMode={viewerLayoutMode}
        runDisplayLabel={selectedRunLabel}
        latestAvailableRunLabel={latestAvailableRunLabel}
        hasNewerRunAvailable={hasNewerRunAvailable}
        onViewLatestRun={hasNewerRunAvailable ? handleViewLatestRun : undefined}
        sourceStatusLabel={observedSourceStatus?.label ?? null}
        sourceStatusDescription={observedSourceStatus?.description ?? null}
        sourceStatusTone={observedSourceStatus?.tone ?? null}
        runSelectionLocked={selectedModelLatestOnly}
      />

      <div className="relative flex-1 min-h-0 overflow-hidden">
        <MapCanvas
          selectionKey={selectionKey}
          selectionEpoch={selectionEpoch}
          gridManifest={isGridLowMidActive ? gridManifest : null}
          gridFrameUrl={isGridLowMidActive ? presentedGridFrameUrl : null}
          gridFrameHour={isGridLowMidActive && Number.isFinite(presentedGridDisplayHour) ? Number(presentedGridDisplayHour) : null}
          gridLegend={isGridLowMidActive ? legend : null}
          gridActive={isGridLowMidActive}
          contourGeoJsonUrl={contourGeoJsonUrl}
          vectorGeoJsonUrl={vectorGeoJsonUrl}
          vectorPrefetchUrls={vectorPrefetchUrls}
          anchorGeoJson={anchorDisplayGeoJson}
          pointLabelsEnabled={pointLabelsEnabled}
          region={region}
          regionViews={regionViews}
          opacity={opacity}
          mode={(isPlaying || isGridPreloadingForPlay) ? "autoplay" : (isVariableSwitching ? "variable-switch" : "scrub")}
          variable={displayedOverlayVariable}
          overlayFadeOutZoom={overlayFadeOutZoom}
          basemapMode={basemapMode}
          onGridFrameVisible={handleGridFrameVisible}
          onGridFrameReady={handleGridFrameReady}
          onGridFrameEvicted={handleGridFrameEvicted}
          isAnimating={isPlaying || isScrubbing}
          onZoomBucketChange={setZoomBucket}
          onZoomRoutingSignal={handleZoomRoutingSignal}
          onViewportChange={handleViewportChange}
          onMapReady={handleMapReady}
          onMapHover={handleMapHover}
          onMapHoverEnd={handleMapHoverEnd}
          onAnchorClick={setSelectedAnchorCity}
          showZoomControls={isDesktopViewerLayout && zoomControlsVisible}
        />

        {/* Subtle radial vignette — darkens map edges for depth; never blocks interaction */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 z-10"
          style={{
            background:
              "radial-gradient(ellipse at center, transparent 40%, rgba(0,0,0,0.28) 100%)",
          }}
        />

        {showBufferStatus && (
          <div className="glass fixed bottom-28 left-1/2 z-40 flex w-[min(92vw,420px)] -translate-x-1/2 flex-col gap-1.5 rounded-xl px-3 py-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 font-medium">
                <AlertCircle className="h-3.5 w-3.5" />
                {bufferStatusText}
              </span>
              {!isScrubLoading ? <span className="font-mono tabular-nums">{preloadPercent}%</span> : null}
            </div>
            {!isScrubLoading ? (
              <div className="h-1.5 overflow-hidden rounded-full bg-muted/70">
                <div
                  className="h-full rounded-full bg-primary transition-[width] duration-200 ease-out"
                  style={{ width: `${preloadPercent}%` }}
                />
              </div>
            ) : null}
          </div>
        )}

        {activeTooltip && (
          <div
            className="pointer-events-none absolute z-50 rounded-xl glass px-2.5 py-1.5 text-xs font-medium shadow-xl"
            style={{
              left: activeTooltip.x + 14,
              top: activeTooltip.y - 32,
            }}
          >
            {activeTooltip.kind === "sample"
              ? `${activeTooltip.value.toFixed(1)} ${activeTooltip.units}`
              : activeTooltip.label}
          </div>
        )}

        {error && (
          <div className="absolute left-4 top-4 z-40 flex items-center gap-2 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive shadow-lg backdrop-blur-md">
            <AlertCircle className="h-3.5 w-3.5" />
            {error}
          </div>
        )}

        {isDesktopViewerLayout ? (
          <div className="fixed right-4 bottom-6 z-40 flex items-end gap-3">
          {handleOpenShareModal ? (
            <button
              type="button"
              onClick={handleOpenShareModal}
              className="inline-flex h-11 items-center gap-2 rounded-full border border-emerald-300/25 bg-[linear-gradient(to_top_right,#1f342f_0%,#526d5c_100%)] px-4 text-sm font-semibold text-emerald-50 shadow-[0_12px_30px_rgba(0,0,0,0.35)] transition-all duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-300/45"
              aria-label="Share"
              title="Share"
            >
              <Send className="h-4 w-4" />
              Share
            </button>
          ) : null}

          <div className="relative flex flex-col items-end">
            {displayPanelOpen ? (
              <div className="glass absolute right-0 bottom-full mb-3 w-[220px] rounded-2xl px-3 py-3 shadow-[0_12px_30px_rgba(0,0,0,0.35)]">
              <div className="mb-3">
                <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-white/48">Display</div>
                <div className="pt-1 text-xs text-white/62">Map overlays and reference aids.</div>
              </div>

              <div className="space-y-2">
                <button
                  type="button"
                  onClick={() => setPointLabelsEnabled((current) => !current)}
                  aria-pressed={pointLabelsEnabled}
                  className={
                    pointLabelsEnabled
                      ? "flex w-full items-center justify-between gap-3 rounded-lg border border-[#354d42] bg-[rgba(53,77,66,0.22)] px-3 py-2 text-left transition-all duration-150 hover:bg-[rgba(53,77,66,0.3)]"
                      : "flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-black/18 px-3 py-2 text-left transition-all duration-150 hover:bg-black/28"
                  }
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm font-semibold text-white">
                      <MapPin className="h-4 w-4 text-white/72" />
                      City Labels
                    </div>
                  </div>
                  <div className={pointLabelsEnabled ? "text-xs font-semibold text-[#354d42]" : "text-xs font-semibold text-white/42"}>
                    {pointLabelsEnabled ? "On" : "Off"}
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setLegendVisible((current) => {
                      const nextVisible = !current;
                      if (nextVisible) {
                        captureProductAnalyticsEvent("legend_opened", {
                          model_id: model || null,
                          variable_id: variable || null,
                          run_id: telemetryRunId,
                          region_id: region || null,
                          forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
                        });
                      }
                      return nextVisible;
                    });
                  }}
                  aria-pressed={legendVisible}
                  className={
                    legendVisible
                      ? "flex w-full items-center justify-between gap-3 rounded-lg border border-[#354d42] bg-[rgba(53,77,66,0.22)] px-3 py-2 text-left transition-all duration-150 hover:bg-[rgba(53,77,66,0.3)]"
                      : "flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-black/18 px-3 py-2 text-left transition-all duration-150 hover:bg-black/28"
                  }
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm font-semibold text-white">
                      <Eye className="h-4 w-4 text-white/72" />
                      Legend
                    </div>
                  </div>
                  <div className={legendVisible ? "text-xs font-semibold text-[#354d42]" : "text-xs font-semibold text-white/42"}>
                    {legendVisible ? "On" : "Off"}
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => setZoomControlsVisible((current) => !current)}
                  aria-pressed={zoomControlsVisible}
                  className={
                    zoomControlsVisible
                      ? "flex w-full items-center justify-between gap-3 rounded-lg border border-[#354d42] bg-[rgba(53,77,66,0.22)] px-3 py-2 text-left transition-all duration-150 hover:bg-[rgba(53,77,66,0.3)]"
                      : "flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-black/18 px-3 py-2 text-left transition-all duration-150 hover:bg-black/28"
                  }
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm font-semibold text-white">
                      <SlidersHorizontal className="h-4 w-4 text-white/72" />
                      Zoom Controls
                    </div>
                  </div>
                  <div className={zoomControlsVisible ? "text-xs font-semibold text-[#354d42]" : "text-xs font-semibold text-white/42"}>
                    {zoomControlsVisible ? "On" : "Off"}
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => setBasemapMode(basemapMode === "dark" ? "light" : "dark")}
                  className="flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-black/18 px-3 py-2 text-left transition-all duration-150 hover:bg-black/28"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm font-semibold text-white">
                      {basemapMode === "dark" ? <Moon className="h-4 w-4 text-white/72" /> : <Sun className="h-4 w-4 text-white/72" />}
                      Basemap
                    </div>
                  </div>
                  <div className="text-xs font-semibold text-[#354d42]">
                    {basemapMode === "dark" ? "Dark" : "Light"}
                  </div>
                </button>

                <div className="rounded-lg border border-white/10 bg-black/18 px-3 py-2">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-sm font-semibold text-white">Opacity</span>
                    <span className="font-mono text-[10px] text-white/62">{Math.round(opacity * 100)}%</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    step={1}
                    value={Math.round(opacity * 100)}
                    onChange={(event) => setOpacity(Number(event.target.value) / 100)}
                    className="h-2 w-full cursor-pointer accent-[#354d42]"
                    aria-label="Overlay opacity"
                  />
                </div>

                <div className="border-t border-white/8 pt-2 text-[10px] leading-relaxed text-white/42">
                  Maps:{" "}
                  <a href="https://www.maplibre.org/" target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-white/70">
                    MapLibre
                  </a>
                  {" "}|
                  {" "}
                  <a
                    href="https://www.openstreetmap.org/copyright"
                    target="_blank"
                    rel="noreferrer"
                    className="underline underline-offset-2 hover:text-white/70"
                  >
                    OSM
                  </a>
                  {" "}|
                  {" "}
                  <a href="https://carto.com/attributions" target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-white/70">
                    CARTO
                  </a>
                </div>
              </div>
              </div>
            ) : null}

            <button
              type="button"
              onClick={() => setDisplayPanelOpen((current) => !current)}
              aria-expanded={displayPanelOpen}
              className={displayPanelOpen
                ? "glass inline-flex h-11 items-center gap-2 rounded-full border border-white/20 px-4 text-sm font-semibold text-white"
                : "glass inline-flex h-11 items-center gap-2 rounded-full border border-white/12 px-4 text-sm font-semibold text-white/88 hover:bg-white/10"
              }
            >
              <SlidersHorizontal className="h-4 w-4" />
              Display
            </button>
          </div>
          </div>
        ) : null}

        {legendVisible ? (
          <Suspense fallback={null}>
            <MapLegend
              legend={legend}
              onOpacityChange={setOpacity}
              showOpacityControl={false}
              displayPanelOpen={displayPanelOpen}
            />
          </Suspense>
        ) : null}

        <BottomForecastControls
          forecastHour={forecastHour}
          availableFrames={selectableFrameHours}
          onForecastHourChange={requestForecastHour}
          onScrubStateChange={setIsScrubbing}
          isPlaying={controlsIsPlaying}
          setIsPlaying={handleSetIsPlaying}
          runDateTimeISO={runDateTimeISO}
          timeAxisMode={selectedTimeAxisMode}
          validTimeISO={currentFrameValidTimeISO}
          frameValidTimesByHour={frameValidTimesByHour}
          sourceStatusLabel={observedSourceStatus?.label ?? null}
          sourceStatusTone={observedSourceStatus?.tone ?? null}
          disabled={loading}
          playDisabled={loading || selectableFrameHours.length === 0}
          transientStatus={frameStatusMessage}
          layoutMode={viewerLayoutMode}
        />
      </div>

      {isShareModalOpen ? (
        <Suspense fallback={null}>
          <TwfShareModal
            open={isShareModalOpen}
            onClose={() => setIsShareModalOpen(false)}
            payload={sharePayload}
            buildScreenshotState={buildScreenshotExportState}
            getLegend={() => legend}
          />
        </Suspense>
      ) : null}

      {selectedAnchorCity ? (
        <Suspense fallback={null}>
          <NwsCityModal
            open={!!selectedAnchorCity}
            onClose={() => setSelectedAnchorCity(null)}
            anchor={selectedAnchorCity}
          />
        </Suspense>
      ) : null}
    </div>
  );
}
