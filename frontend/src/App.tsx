import { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { AlertCircle, Eye, MapPin, Moon, Send, SlidersHorizontal, Sun } from "lucide-react";

import { BottomForecastControls } from "@/components/bottom-forecast-controls";
import { MapCanvas, buildMapStyle, type BasemapMode, type TileReadyMeta, type TileReadySource } from "@/components/map-canvas";
import type { LegendPayload } from "@/components/map-legend";
import type { SharePayload } from "@/components/twf-share-modal";
import { WeatherToolbar } from "@/components/weather-toolbar";
import {
  buildContourUrl,
  fetchAnchorFeatureCollection,
  type CapabilitiesResponse,
  type CapabilityModel,
  type CapabilityVariable,
  type FrameRow,
  type GridManifestResponse,
  type LegendMeta,
  type LoopManifestResponse,
  type ModelDefaultFrameSelection,
  type RegionPreset,
  type RunManifestResponse,
  fetchManifest,
  fetchCapabilities,
  fetchFrames,
  fetchGridManifest,
  fetchLoopManifest,
  fetchRegionPresets,
  fetchRuns,
  fetchSampleBatch,
  readCapabilityDefaultRenderSubstrate,
  readCapabilityDefaultFrameSelection,
  readCapabilityLatestOnly,
  readCapabilityRenderSubstrates,
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
  getCanonicalSingleWebpTierMode,
  getLoopPlaybackPolicy,
  getPlaybackBufferPolicy,
  isGridV1DefaultEnabled,
  isGridV1Enabled,
  isDeferredNonCriticalBootstrapEnabled,
  isDeferredPrefetchUntilFirstPaintEnabled,
  isTileFirstInitialPaintEnabled,
  isViewportAwareTileReadinessEnabled,
  isWebpDefaultRenderEnabled,
  MAP_VIEW_DEFAULTS,
  OVERLAY_DEFAULT_OPACITY,
  WEBP_RENDER_MODE_THRESHOLDS,
  type WeatherSubstrate,
} from "@/lib/config";
import { selectPrefetchFrameHours } from "@/lib/render-scheduler";
import { buildRunOptions, formatRunLabel, pickLatestRunId, sortRunIdsDescending } from "@/lib/run-options";
import { type ScreenshotExportState } from "@/lib/screenshot_export";
import {
  deriveObservedSourceStatus,
  frameValidTime,
  formatObservedCompactTime,
  observedSourceStatusFromAvailability,
  runIdToIso,
  type TimeAxisMode,
} from "@/lib/time-axis";
import { buildTileUrlFromFrame } from "@/lib/tiles";
import { readPermalink } from "@/lib/permalink-read";
import { captureProductAnalyticsEvent } from "@/lib/posthog";
import { trackRumDiagnosticMetric } from "@/lib/rum";
import { trackPerfEvent, trackUsageEvent } from "@/lib/telemetry";
import { useSampleTooltip } from "@/lib/use-sample-tooltip";

import { detectViewerLayoutMode, useViewerLayoutMode } from "@/lib/viewer-layout";

const TwfShareModal = lazy(() =>
  import("@/components/twf-share-modal").then((module) => ({ default: module.TwfShareModal }))
);
const MapLegend = lazy(() =>
  import("@/components/map-legend").then((module) => ({ default: module.MapLegend }))
);

const AUTOPLAY_TICK_MS = 250;
const AUTOPLAY_READY_AHEAD = 2;
const AUTOPLAY_SKIP_WINDOW = 3;
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
const ANCHOR_BATCH_SUPERSEDE_MS = 120;
const WEBP_DECODE_CACHE_BUDGET_DESKTOP_BYTES = 256 * 1024 * 1024;
const WEBP_DECODE_CACHE_BUDGET_MOBILE_BYTES = 128 * 1024 * 1024;
const EMPTY_TILE_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";
const PERMALINK_SYNC_DEBOUNCE_MS = 200;

function viewportSignatureFromState(view: { lat: number; lon: number; z: number }): string {
  const zoomBucket = Math.round(view.z * 2) / 2;
  const latBucket = Math.round(view.lat * 4) / 4;
  const lonBucket = Math.round(view.lon * 4) / 4;
  return `${zoomBucket}|${latBucket}|${lonBucket}`;
}

function recentMedianSample(samples: readonly number[], maxSamples = 12): number | null {
  if (samples.length === 0 || maxSamples <= 0) {
    return null;
  }
  const recent = samples.slice(-Math.min(maxSamples, samples.length)).filter((value) => Number.isFinite(value) && value > 0);
  if (recent.length === 0) {
    return null;
  }
  recent.sort((left, right) => left - right);
  const middle = Math.floor(recent.length / 2);
  if (recent.length % 2 === 1) {
    return recent[middle];
  }
  return (recent[middle - 1] + recent[middle]) / 2;
}

type RenderModeState = "webp_tier0" | "tiles";
const SINGLE_TIER_WEBP_MODE: RenderModeState = getCanonicalSingleWebpTierMode();

type BufferSnapshot = {
  totalFrames: number;
  bufferedCount: number;
  bufferedAheadCount: number;
  terminalCount: number;
  terminalAheadCount: number;
  failedCount: number;
  inFlightCount: number;
  queueDepth: number;
  statusText: string;
  version: number;
};

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

type PendingViewerPerfMetric = {
  eventName: "frame_change" | "scrub_latency";
  startedAt: number;
  renderTarget: "tiles" | "loop";
  expectedTileUrl: string | null;
  expectedLoopHour: number | null;
  modelId: string | null;
  variableId: string | null;
  runId: string | null;
  regionId: string | null;
  forecastHour: number | null;
  traceMeta: Record<string, unknown> | null;
  requestStartedAt: number | null;
  firstTileReadyAt: number | null;
  firstVisibleAt: number | null;
  readySource: TileReadySource | null;
  warmAtStart: boolean | null;
  warmSourceAtStart: TileReadySource | null;
};

type PendingLoopStartMetric = {
  startedAt: number;
  modelId: string | null;
  variableId: string | null;
  runId: string | null;
  regionId: string | null;
  forecastHour: number | null;
};

type LoopDisplayCommitMetric = {
  token: number;
  displayHour: number;
  renderMode: RenderModeState;
  committedAt: number;
  decodedAt: number | null;
  presentationPath: "canvas" | "image-url";
};

type PendingVariableSwitchMetric = {
  startedAt: number;
  fromVariableId: string | null;
  toVariableId: string;
  expectedSelectionKey: string;
  modelId: string | null;
  runId: string | null;
  regionId: string | null;
  manifestResolvedAt: number | null;
  framesResolvedAt: number | null;
  firstTargetRequestAt: number | null;
  firstTargetReadyAt: number | null;
  firstVisibleAt: number | null;
  loopDecodeRequestedAt: number | null;
  expectedTileUrl: string | null;
  warmAtVisible: boolean | null;
  warmSourceAtVisible: TileReadySource | null;
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

function buildVariableSwitchPhase0aMeta(
  pending: PendingVariableSwitchMetric,
  renderTarget: "tiles" | "loop"
): Record<string, unknown> {
  const offsetMs = (at: number | null): number | null => {
    if (!Number.isFinite(at)) {
      return null;
    }
    return Math.max(0, Math.round((at as number) - pending.startedAt));
  };

  return {
    from_variable: pending.fromVariableId,
    render_target: renderTarget,
    phase0a_trace_version: 1,
    expected_selection_key: pending.expectedSelectionKey,
    stage_manifest_resolved_ms: offsetMs(pending.manifestResolvedAt),
    stage_frames_resolved_ms: offsetMs(pending.framesResolvedAt),
    stage_first_target_request_ms: offsetMs(pending.firstTargetRequestAt),
    stage_first_target_ready_ms: offsetMs(pending.firstTargetReadyAt),
    stage_first_visible_ms: offsetMs(pending.firstVisibleAt),
    stage_loop_decode_requested_ms: offsetMs(pending.loopDecodeRequestedAt),
    expected_tile_url: pending.expectedTileUrl,
    warm_at_visible: pending.warmAtVisible,
    warm_source_at_visible: pending.warmSourceAtVisible,
  };
}

type ForecastHourChangeReason = "standard" | "scrub-live" | "scrub-commit";

const BASEMAP_MODE_STORAGE_KEY = "twf.map.basemap_mode";
const MODEL_ORDER_BY_ID: Record<string, number> = {
  hrrr: 0,
  nam: 1,
  nbm: 2,
  gfs: 3,
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
      has_cog: true,
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
      tile_url_template: row.tile_url_template ?? previous.tile_url_template,
      loop_webp_url: row.loop_webp_url ?? previous.loop_webp_url,
      loop_webp_tier0_url: row.loop_webp_tier0_url ?? previous.loop_webp_tier0_url,
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

function isLikelyMobileLoopDevice(): boolean {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return false;
  }
  const coarsePointer = typeof window.matchMedia === "function"
    ? window.matchMedia("(pointer: coarse)").matches
    : false;
  return coarsePointer || /android|iphone|ipad|ipod|mobile/i.test(navigator.userAgent);
}

function getRenderModeThresholds() {
  return WEBP_RENDER_MODE_THRESHOLDS;
}

function nextRenderModeByHysteresis(current: RenderModeState, effectiveZoom: number): RenderModeState {
  const { tier0Max, hysteresis } = getRenderModeThresholds();

  if (current === "tiles") {
    return effectiveZoom <= tier0Max - hysteresis ? SINGLE_TIER_WEBP_MODE : "tiles";
  }

  return effectiveZoom > tier0Max + hysteresis ? "tiles" : SINGLE_TIER_WEBP_MODE;
}

async function preloadLoopFrame(
  url: string,
  signal?: AbortSignal
): Promise<{ ok: boolean; bitmap: ImageBitmap | null; bytes: number; readyMs: number; fetchMs: number; decodeMs: number }> {
  const startedAt = performance.now();
  try {
    const fetchStart = performance.now();
    const response = await fetch(url, {
      credentials: "omit",
      signal,
      cache: "force-cache",
    });
    const fetchEnd = performance.now();
    if (!response.ok) {
      return { ok: false, bitmap: null, bytes: 0, readyMs: 0, fetchMs: 0, decodeMs: 0 };
    }
    const blob = await response.blob();
    if (typeof createImageBitmap !== "function") {
      const readyEnd = performance.now();
      return {
        ok: true,
        bitmap: null,
        bytes: 0,
        readyMs: Math.max(0, Math.round(readyEnd - startedAt)),
        fetchMs: Math.max(0, Math.round(fetchEnd - fetchStart)),
        decodeMs: 0,
      };
    }
    const decodeStart = performance.now();
    const bitmap = await createImageBitmap(blob);
    const decodeEnd = performance.now();
    return {
      ok: true,
      bitmap,
      bytes: bitmap.width * bitmap.height * 4,
      readyMs: Math.max(0, Math.round(decodeEnd - startedAt)),
      fetchMs: Math.max(0, Math.round(fetchEnd - fetchStart)),
      decodeMs: Math.max(0, Math.round(decodeEnd - decodeStart)),
    };
  } catch {
    return { ok: false, bitmap: null, bytes: 0, readyMs: 0, fetchMs: 0, decodeMs: 0 };
  }
}

async function warmLoopImageUrl(url: string, signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch(url, {
      credentials: "omit",
      signal,
      cache: "force-cache",
    });
    if (!response.ok) {
      return false;
    }
    await response.blob();
    return true;
  } catch {
    return false;
  }
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
  if (id === "radar_ptype") {
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
  const baseTitle = meta.legend_title ?? meta.display_name ?? "Legend";
  const title = isPrecipPtype ? withPrecipRateUnits(baseTitle, meta.units) : baseTitle;
  const units = normalizeLegendUnits(meta.units, metaWithIds);
  const legendMetadata = {
    kind: metaWithIds.kind,
    id: metaWithIds.var_key ?? metaWithIds.spec_key ?? metaWithIds.id ?? metaWithIds.var,
    ptype_breaks: metaWithIds.ptype_breaks,
    ptype_order: metaWithIds.ptype_order,
    bins_per_ptype: metaWithIds.bins_per_ptype,
  };

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

export default function App() {
  const webpDefaultEnabled = isWebpDefaultRenderEnabled();
  const gridV1Enabled = isGridV1Enabled();
  const gridV1DefaultEnabled = isGridV1DefaultEnabled();
  const tileFirstInitialPaintEnabled = isTileFirstInitialPaintEnabled();
  const deferNonCriticalBootstrapEnabled = isDeferredNonCriticalBootstrapEnabled();
  const deferPrefetchUntilFirstPaintEnabled = isDeferredPrefetchUntilFirstPaintEnabled();
  const viewportAwareTileReadinessEnabled = isViewportAwareTileReadinessEnabled();
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
  const [loopManifest, setLoopManifest] = useState<LoopManifestResponse | null>(null);
  const [gridManifest, setGridManifest] = useState<GridManifestResponse | null>(null);
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
  const [renderMode, setRenderMode] = useState<RenderModeState>(webpDefaultEnabled ? SINGLE_TIER_WEBP_MODE : "tiles");
  const [visibleRenderMode, setVisibleRenderMode] = useState<RenderModeState>(webpDefaultEnabled ? SINGLE_TIER_WEBP_MODE : "tiles");
  const [weatherSubstrateOverride] = useState<WeatherSubstrate | null>(initialPermalink.weatherSubstrate ?? null);
  const [loopDisplayHour, setLoopDisplayHour] = useState<number | null>(null);
  const [loopDisplayBitmap, setLoopDisplayBitmap] = useState<ImageBitmap | null>(null);
  const [isLoopPreloading, setIsLoopPreloading] = useState(false);
  const [isLoopAutoplayBuffering, setIsLoopAutoplayBuffering] = useState(false);
  const [loopProgress, setLoopProgress] = useState({ total: 0, ready: 0, failed: 0 });
  const [isPreloadingForPlay, setIsPreloadingForPlay] = useState(false);
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
  const [sharePayload, setSharePayload] = useState<SharePayload>({
    permalink: "",
    summary: "CartoSky viewer share",
    detailsSummary: "",
  });
  const [settledTileUrl, setSettledTileUrl] = useState<string | null>(null);
  const [mapLoadingTileUrl, setMapLoadingTileUrl] = useState<string | null>(null);
  const [frameStatusMessage, setFrameStatusMessage] = useState<string | null>(null);
  const [mapViewTick, setMapViewTick] = useState(0);
  const [isMapReady, setIsMapReady] = useState(false);
  const [selectionEpoch, setSelectionEpoch] = useState(0);
  const [gridReadyVersion, setGridReadyVersion] = useState(0);
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
  const [bufferSnapshot, setBufferSnapshot] = useState<BufferSnapshot>({
    totalFrames: 0,
    bufferedCount: 0,
    bufferedAheadCount: 0,
    terminalCount: 0,
    terminalAheadCount: 0,
    failedCount: 0,
    inFlightCount: 0,
    queueDepth: 0,
    statusText: "Buffered 0/0",
    version: 0,
  });
  const latestTileUrlRef = useRef<string>("");
  const selectionEpochRef = useRef(selectionEpoch);
  const readyTileUrlsRef = useRef<Map<string, number>>(new Map());
  const tileReadySourceRef = useRef<Map<string, TileReadySource>>(new Map());
  const tileReadyViewportSignatureRef = useRef<Map<string, string>>(new Map());
  const readyFramesRef = useRef<Set<number>>(new Set());
  const inFlightFramesRef = useRef<Set<number>>(new Set());
  const failedFramesRef = useRef<Set<number>>(new Set());
  const frameRetryCountRef = useRef<Map<number, number>>(new Map());
  const frameCycleStartedAtRef = useRef<Map<number, number>>(new Map());
  const frameNextRetryAtRef = useRef<Map<number, number>>(new Map());
  const inFlightStartedAtRef = useRef<Map<number, number>>(new Map());
  const readyLatencyStatsRef = useRef({ totalMs: 0, count: 0 });
  const bufferVersionRef = useRef(0);
  const [loadedFramesKey, setLoadedFramesKey] = useState("");
  // Tracks a pending RAF for coalescing bufferSnapshot updates (see markFrameReady).
  const bufferSnapshotRafRef = useRef<number | null>(null);
  // Stores the last committed snapshot stats so unchanged updates are skipped entirely.
  const lastSnapshotStatsRef = useRef({ bufferedCount: -1, failedCount: -1, inFlightCount: -1, queueDepth: -1 });
  const datasetGenerationRef = useRef(0);
  const requestGenerationRef = useRef(0);
  const scrubRafRef = useRef<number | null>(null);
  const pendingScrubHourRef = useRef<number | null>(null);
  const scrubPhase0aRef = useRef<ScrubPhase0aSnapshot>(emptyScrubPhase0aSnapshot());
  const autoplayPrimedRef = useRef(false);
  const frameStatusTimerRef = useRef<number | null>(null);
  const preloadProgressRef = useRef({
    lastBufferedCount: 0,
    lastProgressAt: 0,
  });
  const loopPreloadTokenRef = useRef(0);
  const loopUrlWarmTokenRef = useRef(0);
  const warmedLoopSelectionKeyRef = useRef("");
  const loopReadyHoursRef = useRef<Set<number>>(new Set());
  const loopFailedHoursRef = useRef<Set<number>>(new Set());
  const forecastHourRef = useRef(forecastHour);
  const loopFrameHoursRef = useRef<number[]>([]);
  const visibleRenderModeRef = useRef<RenderModeState>("tiles");
  const countAheadReadyLoopFramesRef = useRef<
    (currentHour: number, mode: RenderModeState, maxAhead: number, presentationPath: "image-url" | "canvas") => number
  >(() => 0);
  const isLoopFrameReadyForPresentationRef = useRef<
    (fh: number, mode: RenderModeState, presentationPath: "image-url" | "canvas") => boolean
  >(() => false);
  const loopMinAheadWhilePlayingRef = useRef(0);
  const mapZoomRef = useRef(MAP_VIEW_DEFAULTS.zoom);
  const renderModeDwellTimerRef = useRef<number | null>(null);
  const transitionTokenRef = useRef(0);
  const lastTileViewportCommitUrlRef = useRef<string | null>(null);
  const loopDisplayDecodeTokenRef = useRef(0);
  const loopDisplayDecodeAbortRef = useRef<AbortController | null>(null);
  // The forecast hour currently being decoded by startForegroundLoopFrameDecode.
  // Used to avoid aborting and re-issuing an identical in-flight fetch when the
  // effect-based scrub path fires for the same hour the RAF path already started.
  const foregroundDecodeHourRef = useRef<number | null>(null);
  const loopDecodedCacheRef = useRef<Map<string, { bitmap: ImageBitmap; bytes: number; lastUsedAt: number }>>(new Map());
  const loopDecodedCacheBytesRef = useRef(0);
  const loopDecodedCacheHighWaterRef = useRef(0);
  const loopDecodeReadySamplesRef = useRef<number[]>([]);
  const loopDecodeFetchSamplesRef = useRef<number[]>([]);
  const loopDecodeOnlySamplesRef = useRef<number[]>([]);
  const loopDecodeCompletedAtRef = useRef<Map<string, number>>(new Map());
  const loopDisplayCommitRef = useRef<LoopDisplayCommitMetric | null>(null);
  const loopDisplayCommitTokenRef = useRef(0);
  const loopDisplayPaintedTokenRef = useRef(0);
  const longTaskSampleCounterRef = useRef(0);
  const loopVisiblePaintTokenRef = useRef(0);
  // Holdover refs: snapshot of loop visuals from the outgoing variable so the
  // map keeps showing the old frame during a variable switch instead of
  // flashing stale tile data while the new variable's imagery loads.
  const holdoverLoopBitmapRef = useRef<ImageBitmap | null>(null);
  const holdoverLoopUrlRef = useRef<string | null>(null);
  const holdoverLoopBboxRef = useRef<[number, number, number, number] | null>(null);
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
  const pendingInitialLoopRef = useRef<boolean | undefined>(initialPermalink.loop);
  const viewerMountedAtRef = useRef(typeof performance === "undefined" ? 0 : performance.now());
  const firstViewerFrameTrackedRef = useRef(false);
  const firstMapRenderTrackedRef = useRef(false);
  const viewerOpenedTrackedRef = useRef(false);
  const pendingFirstViewerFrameRef = useRef(false);
  const pendingFirstViewerFrameHourRef = useRef<number | null>(null);
  const pendingFrameMetricRef = useRef<PendingViewerPerfMetric | null>(null);
  const pendingLoopStartMetricRef = useRef<PendingLoopStartMetric | null>(null);
  const pendingVariableSwitchRef = useRef<PendingVariableSwitchMetric | null>(null);
  const failedRumCountRef = useRef(0);
  const modelRef = useRef(model);
  const variableRef = useRef(variable);
  const targetForecastHourRef = useRef(targetForecastHour);
  const lastLoopAdvanceRef = useRef<number | null>(null);
  const loopFrameDropSampleCounterRef = useRef(0);
  // -- Imperative playback fast-path refs --
  // Stable handle to MapCanvas's drawToLoopCanvas; set via onDrawLoopFrameRef callback.
  const drawLoopFrameImperativeRef = useRef<((bitmap: ImageBitmap) => boolean) | null>(null);
  // Pre-built contiguous sequence of { hour, bitmap } for the current playback session.
  // Built when playback starts, refreshed as new frames decode during playback.
  const playbackBitmapMapRef = useRef<Map<number, ImageBitmap> | null>(null);
  // The forecast hour that was last imperatively drawn (not yet synced to React state).
  const imperativePlaybackHourRef = useRef<number | null>(null);
  const tileFetchSampleCounterRef = useRef(0);
  const permalinkHydratedRef = useRef(false);
  const lastSyncedPermalinkSearchRef = useRef("");
  const suppressNextUrlSyncRef = useRef(true);
  const gridReadyFrameUrlsRef = useRef<Set<string>>(new Set());
  const gridPlaybackHourRef = useRef<number | null>(null);
  const anchorSelectionKeyRef = useRef("");
  const anchorBatchAbortRef = useRef<AbortController | null>(null);
  const anchorBatchInFlightHourRef = useRef<number | null>(null);
  const anchorBatchInFlightStartedAtRef = useRef(0);
  const anchorBatchInFlightSelectionKeyRef = useRef("");
  const anchorBatchPendingHourRef = useRef<number | null>(null);
  const anchorBatchLastAppliedHourRef = useRef<number | null>(null);
  const anchorBatchLastAppliedSelectionKeyRef = useRef("");
  const anchorBatchContextRef = useRef<AnchorBatchRequestContext | null>(null);
  const wasCompactViewportRef = useRef<boolean>(viewerLayoutMode !== "desktop");
  // Pre-built Set of valid forecast hours, kept in sync with frameHours.
  // updateBufferSnapshot reads from this ref instead of constructing a new Set
  // on every tile event (which fired 20-40×/sec during animation).
  const frameSetRef = useRef<Set<number>>(new Set());

  const resetLoopPresentationToTiles = useCallback(() => {
    transitionTokenRef.current += 1;
    loopDisplayDecodeTokenRef.current += 1;
    loopDisplayCommitTokenRef.current += 1;
    loopVisiblePaintTokenRef.current += 1;
    loopDisplayCommitRef.current = null;
    setLoopDisplayHour(null);
    setLoopDisplayBitmap(null);
    setVisibleRenderMode("tiles");
    lastTileViewportCommitUrlRef.current = null;
  }, []);

  const clearLoopHoldover = useCallback(() => {
    holdoverLoopBitmapRef.current = null;
    holdoverLoopUrlRef.current = null;
    holdoverLoopBboxRef.current = null;
  }, []);

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
  const selectedVariableKind = selectedCapabilityVarMap.get(variable)?.kind ?? null;
  const selectedVariableDisplayResamplingOverride =
    selectedCapabilityVarMap.get(variable)?.displayResamplingOverride ?? null;
  const visualVariableKind = selectedCapabilityVarMap.get(visualVariable)?.kind ?? selectedVariableKind;
  const visualVariableDisplayResamplingOverride =
    selectedCapabilityVarMap.get(visualVariable)?.displayResamplingOverride
    ?? selectedVariableDisplayResamplingOverride;
  const selectedModelLatestOnly = readCapabilityLatestOnly(selectedModelCapability);
  const selectedModelConstraints = (selectedModelCapability?.constraints ?? {}) as Record<string, unknown>;
  const selectedModelDefaultFrameSelection = readCapabilityDefaultFrameSelection(selectedModelCapability);
  const selectedModelDefaultRenderSubstrate = readCapabilityDefaultRenderSubstrate(selectedModelCapability);
  const selectedTimeAxisMode = readCapabilityTimeAxisMode(selectedModelCapability);
  const selectedVariableRenderSubstrates = selectedCapabilityVarMap.get(variable)?.renderSubstrates ?? ["legacy"];
  const selectionSupportsGridV1 = gridV1Enabled && selectedVariableRenderSubstrates.includes("grid_webgl_v1");
  const selectedWeatherSubstrate = useMemo<WeatherSubstrate>(() => {
    if (weatherSubstrateOverride === "legacy") {
      return "legacy";
    }
    if (weatherSubstrateOverride === "grid_webgl_v1") {
      return selectionSupportsGridV1 ? "grid_webgl_v1" : "legacy";
    }
    if (gridV1DefaultEnabled && selectionSupportsGridV1) {
      return "grid_webgl_v1";
    }
    if (selectedModelDefaultRenderSubstrate === "grid_webgl_v1" && selectionSupportsGridV1) {
      return "grid_webgl_v1";
    }
    return "legacy";
  }, [
    gridV1DefaultEnabled,
    selectedModelDefaultRenderSubstrate,
    selectionSupportsGridV1,
    weatherSubstrateOverride,
  ]);
  const prefersGridSubstrate = selectedWeatherSubstrate === "grid_webgl_v1";
  const overlayFadeOutZoom = useMemo(() => {
    // When the render mode is "tiles" (high-zoom detail), disable the overlay
    // fade-out zoom expression.  GFS defines overlay_fade_out_zoom_start: 6
    // and overlay_fade_out_zoom_end: 7, which fades overlay opacity to 0 at
    // exactly the zoom levels where tiles activate.  Suppressing the fade-out
    // prevents a transparent tile layer at high zoom.
    if (renderMode === "tiles") {
      return null;
    }
    const start = toNumberOrNull(selectedModelConstraints.overlay_fade_out_zoom_start);
    const end = toNumberOrNull(selectedModelConstraints.overlay_fade_out_zoom_end);
    if (start === null || end === null || end <= start) {
      return null;
    }
    return { start, end };
  }, [selectedModelConstraints.overlay_fade_out_zoom_start, selectedModelConstraints.overlay_fade_out_zoom_end, renderMode]);

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

  // Keep frameSetRef in sync so updateBufferSnapshot never allocates a one-off Set.
  useEffect(() => {
    frameSetRef.current = new Set(frameHours);
  }, [frameHours]);

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
    anchorBatchInFlightStartedAtRef.current = 0;
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
      anchorBatchInFlightStartedAtRef.current = performance.now();
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
            anchorBatchInFlightStartedAtRef.current = 0;
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
    const candidates = [manifestLatest, runsLatest, availabilityLatest, fallbackRun].filter((value): value is string => Boolean(value));
    return candidates[0] ?? null;
  }, [run, runManifest, model, capabilities, runs, currentFrame, frameRows]);
  const resolvedRunForRequests = run === "latest" ? (latestRunId ?? "latest") : run;
  const selectionKey = `${model}:${resolvedRunForRequests}:${variable}`;
  const telemetryRunId = resolvedRunForRequests ?? (run !== "latest" ? run : latestRunId ?? null);
  const apiRoot = API_ORIGIN.replace(/\/$/, "");

  useEffect(() => {
    if (!prefersGridSubstrate || !hasRenderableSelection || !selectionSupportsGridV1) {
      setGridManifest(null);
      return;
    }

    const controller = new AbortController();
    const startedAt = performance.now();
    setGridManifest(null);

    fetchGridManifest(model, resolvedRunForRequests, variable, { signal: controller.signal })
      .then((manifest) => {
        if (controller.signal.aborted) {
          return;
        }
        setGridManifest(manifest);
        trackPerfEvent({
          event_name: "grid_manifest_resolve",
          duration_ms: Math.max(0, performance.now() - startedAt),
          model_id: model || null,
          variable_id: variable || null,
          run_id: telemetryRunId,
          region_id: region || null,
          meta: {
            substrate: "grid_webgl_v1",
            success: Boolean(manifest),
          },
        });
      })
      .catch(() => {
        if (controller.signal.aborted) {
          return;
        }
        setGridManifest(null);
      });

    return () => {
      controller.abort();
    };
  }, [
    hasRenderableSelection,
    model,
    prefersGridSubstrate,
    region,
    resolvedRunForRequests,
    selectionSupportsGridV1,
    telemetryRunId,
    variable,
  ]);
  const observedSourceStatus = useMemo(() => {
    if (selectedTimeAxisMode !== "observed") {
      return null;
    }
    const availability = model ? capabilities?.availability?.[model] : null;
    const authoritativeStatus = observedSourceStatusFromAvailability(availability);
    if (
      authoritativeStatus &&
      !(
        authoritativeStatus.tone === "unavailable" &&
        newestFrameValidTimeISO &&
        frameRows.length > 0
      )
    ) {
      return authoritativeStatus;
    }
    return deriveObservedSourceStatus({
      latestRunAvailable: Boolean(availability?.latest_run),
      latestRunReady: availability?.latest_run_ready,
      newestValidTimeISO: newestFrameValidTimeISO,
      availableFrameCount: frameRows.length,
    });
  }, [selectedTimeAxisMode, model, capabilities, newestFrameValidTimeISO, frameRows.length]);
  const buildObservedTelemetryMeta = useCallback(
    (frameHour?: number | null, extraMeta?: Record<string, unknown> | null): Record<string, unknown> | undefined => {
      const base = extraMeta ? { ...extraMeta } : {};
      if (selectedTimeAxisMode !== "observed") {
        return Object.keys(base).length > 0 ? base : undefined;
      }
      const availability = model ? capabilities?.availability?.[model] : null;
      const hour = Number.isFinite(frameHour) ? Number(frameHour) : forecastHour;
      const frameValidTimeISO = Number.isFinite(hour)
        ? (frameValidTimesByHour[hour] ?? currentFrameValidTimeISO ?? null)
        : (currentFrameValidTimeISO ?? null);
      const parsedValidTime = frameValidTimeISO ? new Date(frameValidTimeISO) : null;
      const observationAgeMs = parsedValidTime && Number.isFinite(parsedValidTime.getTime())
        ? Math.max(0, Date.now() - parsedValidTime.getTime())
        : null;
      return {
        ...base,
        time_axis_mode: "observed",
        source_status: observedSourceStatus?.tone ?? null,
        source_status_label: observedSourceStatus?.label ?? null,
        latest_scan_valid_time:
          (typeof availability?.latest_scan_valid_time === "string" && availability.latest_scan_valid_time)
          || newestFrameValidTimeISO
          || null,
        latest_scan_age_minutes: observedSourceStatus?.ageMinutes ?? null,
        frame_valid_time: frameValidTimeISO,
        observation_age_ms: observationAgeMs,
      };
    },
    [
      capabilities,
      currentFrameValidTimeISO,
      forecastHour,
      frameValidTimesByHour,
      model,
      newestFrameValidTimeISO,
      observedSourceStatus,
      selectedTimeAxisMode,
    ]
  );

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

  const loopFrameTier0FallbackByHour = useMemo(() => {
    const map = new Map<number, string>();
    for (const row of frameRows) {
      const fh = Number(row?.fh);
      const loopUrl = row?.loop_webp_tier0_url ?? row?.loop_webp_url;
      if (!Number.isFinite(fh) || !loopUrl) {
        continue;
      }
      const absolute = /^https?:\/\//i.test(loopUrl)
        ? loopUrl
        : `${apiRoot}${loopUrl.startsWith("/") ? "" : "/"}${loopUrl}`;
      map.set(fh, absolute);
    }
    return map;
  }, [apiRoot, frameRows]);

  const loopTier0UrlByHour = useMemo(() => {
    const map = new Map<number, string>(loopFrameTier0FallbackByHour);
    const tier0 = loopManifest?.loop_tiers.find((entry) => Number(entry?.tier) === 0);
    const frames = Array.isArray(tier0?.frames) ? tier0.frames : [];
    for (const frame of frames) {
      const fh = Number(frame?.fh);
      const loopUrl = frame?.url;
      if (!Number.isFinite(fh) || !loopUrl) {
        continue;
      }
      const absolute = /^https?:\/\//i.test(loopUrl)
        ? loopUrl
        : `${apiRoot}${loopUrl.startsWith("/") ? "" : "/"}${loopUrl}`;
      map.set(fh, absolute);
    }
    return map;
  }, [apiRoot, loopFrameTier0FallbackByHour, loopManifest]);

  const loopUrlByHour = useMemo(() => new Map(loopTier0UrlByHour), [loopTier0UrlByHour]);

  const loopFrameHours = useMemo(() => {
    return Array.from(loopTier0UrlByHour.keys()).sort((a, b) => a - b);
  }, [loopTier0UrlByHour]);

  const resolvedLoopTargetForecastHour = useMemo(() => {
    if (loopFrameHours.length === 0) {
      return targetForecastHour;
    }
    return nearestFrame(loopFrameHours, targetForecastHour);
  }, [loopFrameHours, targetForecastHour]);

  const resolveLoopUrlForHour = useCallback(
    (fh: number, _preferredMode: RenderModeState): string | null => {
      return loopTier0UrlByHour.get(fh) ?? loopUrlByHour.get(fh) ?? null;
    },
    [loopTier0UrlByHour, loopUrlByHour]
  );
  const isCurrentSelectionLoaded = loadedFramesKey === selectionKey;
  const loopSelectionReady = isCurrentSelectionLoaded && loopFrameHours.length > 0;
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
  const resolvedWeatherSubstrate = useMemo<WeatherSubstrate>(() => {
    if (
      prefersGridSubstrate
      && gridManifest
      && gridLod0
      && gridFrameHours.length > 0
    ) {
      return "grid_webgl_v1";
    }
    return "legacy";
  }, [gridFrameHours.length, gridLod0, gridManifest, prefersGridSubstrate]);
  const canUseGridPlayback = useMemo(() => {
    if (resolvedWeatherSubstrate !== "grid_webgl_v1" || gridFrameHours.length <= 1) {
      return false;
    }
    return gridFrameHours.every((fh) => Boolean(gridFrameByHour.get(fh)?.url));
  }, [gridFrameByHour, gridFrameHours, resolvedWeatherSubstrate]);
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
  const isGridFrameReady = useCallback((frameUrl: string | null | undefined): boolean => {
    const normalized = normalizeGridFrameUrl(frameUrl);
    if (!normalized) {
      return false;
    }
    return gridReadyFrameUrlsRef.current.has(normalized);
  }, [normalizeGridFrameUrl]);
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
  const webpDecodeCacheBudgetBytes = useMemo(() => {
    if (typeof navigator === "undefined") {
      return WEBP_DECODE_CACHE_BUDGET_DESKTOP_BYTES;
    }
    const isMobile = /android|iphone|ipad|ipod|mobile/i.test(navigator.userAgent);
    return isMobile ? WEBP_DECODE_CACHE_BUDGET_MOBILE_BYTES : WEBP_DECODE_CACHE_BUDGET_DESKTOP_BYTES;
  }, []);

  const loopCacheKey = useCallback(
    (fh: number, mode: RenderModeState) => {
      return `${model}:${resolvedRunForRequests}:${variable}:${mode}:${fh}`;
    },
    [model, resolvedRunForRequests, variable]
  );

  const upsertLoopDecodedCache = useCallback(
    (key: string, bitmap: ImageBitmap, bytes: number) => {
      const now = Date.now();
      const cache = loopDecodedCacheRef.current;
      const previous = cache.get(key);
      if (previous) {
        loopDecodedCacheBytesRef.current -= previous.bytes;
        previous.bitmap.close();
      }
      cache.set(key, { bitmap, bytes, lastUsedAt: now });
      loopDecodedCacheBytesRef.current += bytes;
      if (loopDecodedCacheBytesRef.current > loopDecodedCacheHighWaterRef.current) {
        loopDecodedCacheHighWaterRef.current = loopDecodedCacheBytesRef.current;
      }

      while (loopDecodedCacheBytesRef.current > webpDecodeCacheBudgetBytes && cache.size > 1) {
        let lruKey: string | null = null;
        let oldest = Number.POSITIVE_INFINITY;
        for (const [candidateKey, candidate] of cache.entries()) {
          if (candidate.lastUsedAt < oldest) {
            oldest = candidate.lastUsedAt;
            lruKey = candidateKey;
          }
        }
        if (!lruKey || lruKey === key) {
          break;
        }
        const evicted = cache.get(lruKey);
        if (!evicted) {
          break;
        }
        evicted.bitmap.close();
        loopDecodedCacheBytesRef.current -= evicted.bytes;
        cache.delete(lruKey);
      }
    },
    [webpDecodeCacheBudgetBytes]
  );

  const ensureLoopFrameDecoded = useCallback(
    async (fh: number, mode: RenderModeState, signal?: AbortSignal): Promise<boolean> => {
      if (mode === "tiles") {
        return false;
      }
      const key = loopCacheKey(fh, mode);
      const cached = loopDecodedCacheRef.current.get(key);
      if (cached) {
        cached.lastUsedAt = Date.now();
        loopDecodeCompletedAtRef.current.set(key, performance.now());
        trackPerfEvent({
          event_name: "loop_decode_ready",
          duration_ms: 0,
          model_id: model || null,
          variable_id: variable || null,
          run_id: telemetryRunId,
          region_id: region || null,
          forecast_hour: Number.isFinite(fh) ? fh : null,
          meta: buildObservedTelemetryMeta(fh, {
            render_mode: mode,
            cache_hit: true,
          }),
        });
        loopReadyHoursRef.current.add(fh);
        return true;
      }

      const url = resolveLoopUrlForHour(fh, mode);
      if (!url) {
        return false;
      }

      const decodeStartedAt = performance.now();
      const decoded = await preloadLoopFrame(url, signal);
      if (!decoded.ok) {
        return false;
      }
      if (decoded.readyMs > 0) {
        const readySamples = loopDecodeReadySamplesRef.current;
        readySamples.push(decoded.readyMs);
        if (readySamples.length > 256) {
          readySamples.splice(0, readySamples.length - 256);
        }
      }
      if (decoded.fetchMs > 0) {
        const fetchSamples = loopDecodeFetchSamplesRef.current;
        fetchSamples.push(decoded.fetchMs);
        if (fetchSamples.length > 256) {
          fetchSamples.splice(0, fetchSamples.length - 256);
        }
      }
      if (decoded.decodeMs > 0) {
        const decodeSamples = loopDecodeOnlySamplesRef.current;
        decodeSamples.push(decoded.decodeMs);
        if (decodeSamples.length > 256) {
          decodeSamples.splice(0, decodeSamples.length - 256);
        }
      }
      if (decoded.bitmap) {
        upsertLoopDecodedCache(key, decoded.bitmap, decoded.bytes);
      }
      loopDecodeCompletedAtRef.current.set(key, performance.now());
      trackPerfEvent({
        event_name: "loop_decode_ready",
        duration_ms: decoded.readyMs > 0 ? decoded.readyMs : Math.max(0, performance.now() - decodeStartedAt),
        model_id: model || null,
        variable_id: variable || null,
        run_id: telemetryRunId,
        region_id: region || null,
        forecast_hour: Number.isFinite(fh) ? fh : null,
        meta: buildObservedTelemetryMeta(fh, {
          render_mode: mode,
          cache_hit: false,
          fetch_ms: decoded.fetchMs,
          decode_ms: decoded.decodeMs,
        }),
      });
      loopReadyHoursRef.current.add(fh);
      return true;
    },
    [buildObservedTelemetryMeta, loopCacheKey, resolveLoopUrlForHour, upsertLoopDecodedCache, model, variable, telemetryRunId, region]
  );

  const hasDecodedLoopFrame = useCallback(
    (fh: number, mode: RenderModeState): boolean => {
      if (mode === "tiles") {
        return false;
      }
      return loopDecodedCacheRef.current.has(loopCacheKey(fh, mode));
    },
    [loopCacheKey]
  );

  const isLoopFrameReadyForPresentation = useCallback(
    (fh: number, mode: RenderModeState, presentationPath: "image-url" | "canvas"): boolean => {
      if (mode === "tiles") {
        return false;
      }
      if (presentationPath === "image-url") {
        return Boolean(resolveLoopUrlForHour(fh, mode));
      }
      return hasDecodedLoopFrame(fh, mode);
    },
    [resolveLoopUrlForHour, hasDecodedLoopFrame]
  );

  const getDecodedLoopBitmap = useCallback(
    (fh: number, mode: RenderModeState): ImageBitmap | null => {
      if (mode === "tiles") {
        return null;
      }
      const cached = loopDecodedCacheRef.current.get(loopCacheKey(fh, mode));
      if (!cached) {
        return null;
      }
      // Guard against detached bitmaps whose backing store was freed.
      if (cached.bitmap.width === 0) {
        return null;
      }
      cached.lastUsedAt = Date.now();
      return cached.bitmap;
    },
    [loopCacheKey]
  );

  /** Build / refresh the bitmap map used by the imperative playback fast-path.
   *  Only includes frames that are already decoded.  Returns a Map<hour, bitmap>
   *  for O(1) lookups in the RAF tick. */
  const buildPlaybackBitmapMap = useCallback(
    (frameHours: number[], mode: RenderModeState): Map<number, ImageBitmap> => {
      const map = new Map<number, ImageBitmap>();
      for (const fh of frameHours) {
        const key = loopCacheKey(fh, mode);
        const cached = loopDecodedCacheRef.current.get(key);
        // Skip detached bitmaps — their backing store was freed by
        // LRU eviction or dataset-change cache clears.
        if (cached && cached.bitmap.width > 0) {
          map.set(fh, cached.bitmap);
        }
      }
      return map;
    },
    [loopCacheKey]
  );

  const startForegroundLoopFrameDecode = useCallback(
    (fh: number, mode: RenderModeState, onReady?: () => void) => {
      if (mode === "tiles") {
        return;
      }
      // If a decode for the exact same hour is already in-flight, skip the
      // abort-and-refetch cycle.  This prevents the RAF-initiated scrub decode
      // from being cancelled and re-issued identically by the effect-based path.
      if (foregroundDecodeHourRef.current === fh && loopDisplayDecodeAbortRef.current) {
        return;
      }
      loopDisplayDecodeAbortRef.current?.abort();
      const controller = new AbortController();
      loopDisplayDecodeAbortRef.current = controller;
      foregroundDecodeHourRef.current = fh;
      ensureLoopFrameDecoded(fh, mode, controller.signal)
        .then((ready) => {
          // Only promote if this decode hasn't been superseded by a newer scrub.
          if (ready && loopDisplayDecodeAbortRef.current === controller) {
            onReady?.();
          }
        })
        .catch(() => {
          // Foreground interaction decode is best-effort warming for the exact frame.
        })
        .finally(() => {
          if (loopDisplayDecodeAbortRef.current === controller) {
            loopDisplayDecodeAbortRef.current = null;
            foregroundDecodeHourRef.current = null;
          }
        });
    },
    [ensureLoopFrameDecoded]
  );

  const countAheadReadyLoopFrames = useCallback(
    (
      currentHour: number,
      mode: RenderModeState,
      maxAhead: number,
      presentationPath: "image-url" | "canvas" = "canvas"
    ): number => {
      if (mode === "tiles" || loopFrameHours.length === 0 || maxAhead <= 0) {
        return 0;
      }
      const currentIndex = loopFrameHours.indexOf(currentHour);
      if (currentIndex < 0) {
        return 0;
      }

      let ready = 0;
      const endIndex = Math.min(loopFrameHours.length - 1, currentIndex + maxAhead);
      for (let index = currentIndex + 1; index <= endIndex; index += 1) {
        const fh = loopFrameHours[index];
        if (!isLoopFrameReadyForPresentation(fh, mode, presentationPath)) {
          break;
        }
        ready += 1;
      }
      return ready;
    },
    [loopFrameHours, isLoopFrameReadyForPresentation]
  );

  const canUseLoopPlayback = useMemo(() => {
    if (resolvedWeatherSubstrate === "grid_webgl_v1") {
      return false;
    }
    if (loopFrameHours.length <= 1) {
      return false;
    }
    return loopFrameHours.every((fh) => Boolean(loopTier0UrlByHour.get(fh) ?? loopUrlByHour.get(fh)));
  }, [loopFrameHours, loopTier0UrlByHour, loopUrlByHour, resolvedWeatherSubstrate]);

  const isHighDetailZoom = useMemo(() => {
    const effectiveZoom = getEffectiveZoom(mapZoom);
    const thresholds = getRenderModeThresholds();
    const highDetailCutoff = thresholds.tier0Max + thresholds.hysteresis;
    return effectiveZoom > highDetailCutoff;
  }, [mapZoom]);
  const isGridLowMidActive = useMemo(() => {
    return Boolean(
      resolvedWeatherSubstrate === "grid_webgl_v1"
      && !isHighDetailZoom
      && gridManifest
      && gridLod0
      && Array.isArray(gridManifest.bbox)
      && gridManifest.bbox.length === 4
      && activeGridFrameUrl
    );
  }, [activeGridFrameUrl, gridLod0, gridManifest, isHighDetailZoom, resolvedWeatherSubstrate]);
  const isGridPlayable = useMemo(() => {
    return resolvedWeatherSubstrate === "grid_webgl_v1" && canUseGridPlayback && !isHighDetailZoom;
  }, [canUseGridPlayback, isHighDetailZoom, resolvedWeatherSubstrate]);

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

  // Observe individual weather tile fetch durations via the Performance resource
  // timing API. Sampled at 1:8 to avoid flooding the telemetry pipeline.
  useEffect(() => {
    if (typeof PerformanceObserver === "undefined") {
      return;
    }
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!(entry instanceof PerformanceResourceTiming)) continue;
        const url = entry.name;
        // Only track our own weather tile PNG requests.
        if (!url.includes("/tiles/v3/") || !url.endsWith(".png")) continue;
        tileFetchSampleCounterRef.current += 1;
        if (tileFetchSampleCounterRef.current % 8 !== 0) continue;
        const durationMs = entry.duration;
        if (!Number.isFinite(durationMs) || durationMs <= 0) continue;
        // Extract model and variable from the path: /tiles/v3/{model}/{run}/{varKey}/{fh}/...
        let modelId: string | null = modelRef.current || null;
        let variableId: string | null = variableRef.current || null;
        try {
          const pathMatch = url.match(/\/tiles\/v3\/([^/]+)\/[^/]+\/([^/]+)\//);
          if (pathMatch) {
            modelId = decodeURIComponent(pathMatch[1]);
            variableId = decodeURIComponent(pathMatch[2]);
          }
        } catch {
          // best-effort URL parse; fall through to use ref values
        }
        trackPerfEvent({
          event_name: "tile_fetch",
          duration_ms: durationMs,
          model_id: modelId,
          variable_id: variableId,
        });
      }
    });
    try {
      observer.observe({ type: "resource", buffered: false });
    } catch {
      return;
    }
    return () => observer.disconnect();
  }, []);

  // Observe long tasks to capture main-thread blocking that can delay frame
  // presentation even when network requests are already warm.
  useEffect(() => {
    if (typeof PerformanceObserver === "undefined") {
      return;
    }

    let observer: PerformanceObserver;
    try {
      observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          const durationMs = Number(entry.duration);
          if (!Number.isFinite(durationMs) || durationMs <= 0) {
            continue;
          }
          if (durationMs < 50) {
            continue;
          }
          longTaskSampleCounterRef.current += 1;
          if (longTaskSampleCounterRef.current % 4 !== 0) {
            continue;
          }
          trackPerfEvent({
            event_name: "long_task_blocking",
            duration_ms: durationMs,
            model_id: modelRef.current || null,
            variable_id: variableRef.current || null,
            meta: {
              entry_name: entry.name || "longtask",
            },
          });
        }
      });
      observer.observe({ type: "longtask", buffered: false } as PerformanceObserverInit);
    } catch {
      return;
    }

    return () => {
      observer.disconnect();
    };
  }, []);

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

  useEffect(() => {
    if (renderMode !== "tiles" && renderMode !== SINGLE_TIER_WEBP_MODE) {
      setRenderMode(SINGLE_TIER_WEBP_MODE);
    }
    if (visibleRenderMode !== "tiles" && visibleRenderMode !== SINGLE_TIER_WEBP_MODE) {
      setVisibleRenderMode(SINGLE_TIER_WEBP_MODE);
    }
  }, [renderMode, visibleRenderMode]);

  useEffect(() => {
    const clearDwellTimer = () => {
      if (renderModeDwellTimerRef.current !== null) {
        window.clearTimeout(renderModeDwellTimerRef.current);
        renderModeDwellTimerRef.current = null;
      }
    };

    if (!webpDefaultEnabled || !canUseLoopPlayback) {
      clearDwellTimer();
      if (renderMode !== "tiles") {
        setRenderMode("tiles");
      }
      return clearDwellTimer;
    }

    // At high zoom levels, switch to tiles for detail.  At lower zooms, use
    // the canonical WebP loop mode for smooth playback/scrubbing.
    if (zoomGestureActive) {
      // While the user is still pinching/scrolling, don't switch modes — wait
      // for the gesture to settle to avoid flicker.
      return clearDwellTimer;
    }

    const desired = nextRenderModeByHysteresis(renderMode, getEffectiveZoom(mapZoom));
    if (desired === renderMode) {
      clearDwellTimer();
      return clearDwellTimer;
    }

    // Use the dwell timer to avoid jitter when the zoom is right at the
    // threshold boundary.
    clearDwellTimer();
    renderModeDwellTimerRef.current = window.setTimeout(() => {
      renderModeDwellTimerRef.current = null;
      setRenderMode(desired);
    }, WEBP_RENDER_MODE_THRESHOLDS.dwellMs);

    return clearDwellTimer;
  }, [renderMode, webpDefaultEnabled, canUseLoopPlayback, mapZoom, zoomGestureActive]);

  useEffect(() => {
    transitionTokenRef.current += 1;

    if (!canUseLoopPlayback || !loopSelectionReady) {
      setVisibleRenderMode("tiles");
      setLoopDisplayHour(null);
      return;
    }

    if (renderMode === visibleRenderMode) {
      return;
    }

    if (renderMode === "tiles") {
      setVisibleRenderMode("tiles");
      setLoopDisplayHour(null);
      setLoopDisplayBitmap(null);
      return;
    }

    if (visibleRenderMode !== renderMode) {
      setVisibleRenderMode(renderMode);
    }

    const commitLoopHour = resolvedLoopTargetForecastHour;

    // No signal passed to ensureLoopFrameDecoded: the decode always runs to
    // completion so its result is stored in the LRU cache for immediate reuse.
    // The token gates whether we actually commit the visible mode change,
    // preventing stale results from being applied.
    const token = transitionTokenRef.current;
    ensureLoopFrameDecoded(commitLoopHour, renderMode)
      .then((ready) => {
        if (token !== transitionTokenRef.current) {
          return;
        }
        if (ready) {
          setLoopDisplayHour(commitLoopHour);
        }
      })
      .catch(() => {
        // Decode failed for this attempt; keep current visible mode and allow
        // subsequent scheduler/interaction passes to retry.
      });
  }, [
    renderMode,
    visibleRenderMode,
    canUseLoopPlayback,
    loopSelectionReady,
    targetForecastHour,
    resolvedLoopTargetForecastHour,
    resolveLoopUrlForHour,
    ensureLoopFrameDecoded,
    isPlaying,
    isLoopPreloading,
    isLoopAutoplayBuffering,
    isScrubbing,
  ]);

  const loopPlaybackRenderMode: RenderModeState =
    visibleRenderMode === "tiles" ? SINGLE_TIER_WEBP_MODE : visibleRenderMode;
  const isLoopDisplayActive =
    renderMode !== "tiles"
    && canUseLoopPlayback
    && loopSelectionReady;
  const stagedLoopWarmupMode: RenderModeState = renderMode === "tiles" ? SINGLE_TIER_WEBP_MODE : renderMode;
  const shouldEagerlyDecodeLoopFrames = isPlaying || isLoopPreloading || isLoopAutoplayBuffering;
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
  const mapForecastHour = isLoopDisplayActive
    ? targetForecastHour
    : (Number.isFinite(visibleGridOverlayHour) ? Number(visibleGridOverlayHour) : forecastHour);
  const visibleLoopOverlayHour = (isPlaying || isLoopPreloading || isLoopAutoplayBuffering)
    ? resolvedLoopTargetForecastHour
    : (loopDisplayHour ?? resolvedLoopTargetForecastHour);
  const visibleOverlayHour = isLoopDisplayActive
    ? visibleLoopOverlayHour
    : (Number.isFinite(visibleGridOverlayHour) ? Number(visibleGridOverlayHour) : forecastHour);

  const tileUrlForHour = useCallback(
    (fh: number): string => {
      if (!hasRenderableSelection) {
        return EMPTY_TILE_DATA_URL;
      }
      const fallbackFh = frameHours[0] ?? 0;
      const resolvedFh = Number.isFinite(fh) ? fh : fallbackFh;
      return buildTileUrlFromFrame({
        model,
        run: resolvedRunForRequests,
        varKey: variable,
        fh: resolvedFh,
        frameRow: frameByHour.get(resolvedFh) ?? frameRows[0] ?? null,
      });
    },
    [hasRenderableSelection, model, resolvedRunForRequests, variable, frameHours, frameByHour, frameRows]
  );

  const tileUrl = useMemo(() => {
    return tileUrlForHour(mapForecastHour);
  }, [tileUrlForHour, mapForecastHour]);

  const tileUrlToHour = useMemo(() => {
    const map = new Map<string, number>();
    for (const fh of frameHours) {
      map.set(tileUrlForHour(fh), fh);
    }
    return map;
  }, [frameHours, tileUrlForHour]);

  const playbackPolicy = useMemo(
    () =>
      getPlaybackBufferPolicy({
        totalFrames: frameHours.length,
        autoplayTickMs: AUTOPLAY_TICK_MS,
      }),
    [frameHours.length]
  );
  const loopPlaybackPolicy = useMemo(
    () =>
      getLoopPlaybackPolicy({
        totalFrames: loopFrameHours.length,
        autoplayTickMs: AUTOPLAY_TICK_MS,
      }),
    [loopFrameHours.length]
  );

  useEffect(() => {
    loopFrameHoursRef.current = loopFrameHours;
    visibleRenderModeRef.current = loopPlaybackRenderMode;
    countAheadReadyLoopFramesRef.current = countAheadReadyLoopFrames;
    isLoopFrameReadyForPresentationRef.current = isLoopFrameReadyForPresentation;
    loopMinAheadWhilePlayingRef.current = loopPlaybackPolicy.minAheadWhilePlaying;
  }, [
    loopFrameHours,
    loopPlaybackRenderMode,
    countAheadReadyLoopFrames,
    isLoopFrameReadyForPresentation,
    loopPlaybackPolicy.minAheadWhilePlaying,
  ]);

  const updateBufferSnapshot = useCallback(() => {
    const totalFrames = frameHours.length;
    const ready = readyFramesRef.current;
    const inFlight = inFlightFramesRef.current;
    const failed = failedFramesRef.current;
    const now = Date.now();

    if (totalFrames === 0) {
      const version = ++bufferVersionRef.current;
      setBufferSnapshot({
        totalFrames: 0,
        bufferedCount: 0,
        bufferedAheadCount: 0,
        terminalCount: 0,
        terminalAheadCount: 0,
        failedCount: 0,
        inFlightCount: 0,
        queueDepth: 0,
        statusText: "Buffered 0/0",
        version,
      });
      return;
    }

    const frameSet = frameSetRef.current;
    for (const fh of ready) {
      if (!frameSet.has(fh)) {
        ready.delete(fh);
      }
    }
    for (const fh of failed) {
      if (!frameSet.has(fh)) {
        failed.delete(fh);
      }
    }
    for (const fh of inFlight) {
      if (!frameSet.has(fh) || ready.has(fh)) {
        inFlight.delete(fh);
        inFlightStartedAtRef.current.delete(fh);
        continue;
      }
      const startedAt = inFlightStartedAtRef.current.get(fh);
      if (Number.isFinite(startedAt) && now - (startedAt as number) > INFLIGHT_FRAME_TTL_MS) {
        const nextRetry = (frameRetryCountRef.current.get(fh) ?? 0) + 1;
        frameRetryCountRef.current.set(fh, nextRetry);
        const cycleStartedAt = frameCycleStartedAtRef.current.get(fh) ?? now;
        frameCycleStartedAtRef.current.set(fh, cycleStartedAt);
        const ageMs = now - cycleStartedAt;

        inFlight.delete(fh);
        inFlightStartedAtRef.current.delete(fh);
        if (nextRetry >= FRAME_MAX_RETRIES || ageMs >= FRAME_HARD_DEADLINE_MS) {
          failed.add(fh);
          frameNextRetryAtRef.current.delete(fh);
        } else {
          const retryDelayMs = FRAME_RETRY_BASE_MS * 2 ** (nextRetry - 1);
          frameNextRetryAtRef.current.set(fh, now + retryDelayMs);
          void retryDelayMs;
        }
      }
    }

    const currentIndex = frameHours.indexOf(forecastHour);
    let bufferedAheadCount = 0;
    let terminalAheadCount = 0;
    if (currentIndex >= 0) {
      for (let i = currentIndex + 1; i < frameHours.length; i += 1) {
        const hour = frameHours[i];
        if (ready.has(hour)) {
          bufferedAheadCount += 1;
        }
        if (ready.has(hour) || failed.has(hour)) {
          terminalAheadCount += 1;
        }
      }
    }

    const bufferedCount = ready.size;
    const failedCount = failed.size;
    if (failedCount > failedRumCountRef.current) {
      trackRumDiagnosticMetric({
        metric_name: "tile_request_failure_count",
        metric_value: failedCount - failedRumCountRef.current,
        metric_unit: "count",
        model_id: modelRef.current || null,
        variable_id: variableRef.current || null,
        run_id: telemetryRunId,
        region_id: region || null,
        forecast_hour: forecastHour,
      });
    }
    failedRumCountRef.current = failedCount;
    const terminalCount = Math.min(totalFrames, bufferedCount + failedCount);
    const queueDepth = Math.max(0, totalFrames - terminalCount - inFlight.size);

    // Skip the React state update when the counts that drive UI and prefetchHours
    // are identical to the last committed snapshot. Tile events from prefetch sources
    // can fire 20-40×/sec during animation even when nothing meaningful has changed.
    const prev = lastSnapshotStatsRef.current;
    if (
      prev.bufferedCount === bufferedCount &&
      prev.failedCount === failedCount &&
      prev.inFlightCount === inFlight.size &&
      prev.queueDepth === queueDepth
    ) {
      return;
    }
    lastSnapshotStatsRef.current = { bufferedCount, failedCount, inFlightCount: inFlight.size, queueDepth };

    const version = ++bufferVersionRef.current;
    const snapshot = {
      totalFrames,
      bufferedCount,
      bufferedAheadCount,
      terminalCount,
      terminalAheadCount,
      failedCount,
      inFlightCount: inFlight.size,
      queueDepth,
      statusText: `Loaded ${terminalCount}/${totalFrames} (${bufferedCount} ready)`,
      version,
    };
    setBufferSnapshot(snapshot);
  }, [frameHours, forecastHour, telemetryRunId, region]);

  // During a variable switch the old variable's imagery is still on screen;
  // keep its paint settings in effect until the new variable is promoting.
  const displayedOverlayVariable = (isLoopDisplayActive || isVariableSwitching) ? (visualVariable || variable) : variable;
  const displayedOverlayVariableKind = (isLoopDisplayActive || isVariableSwitching) ? visualVariableKind : selectedVariableKind;
  const displayedOverlayVariableDisplayResamplingOverride =
    (isLoopDisplayActive || isVariableSwitching)
      ? visualVariableDisplayResamplingOverride
      : selectedVariableDisplayResamplingOverride;

  const contourGeoJsonUrl = useMemo(() => {
    if (!firstWeatherFramePainted) {
      return null;
    }
    if (!hasRenderableSelection || displayedOverlayVariable !== "tmp2m") {
      return null;
    }
    const contourFrame = frameByHour.get(mapForecastHour) ?? currentFrame;
    const frameMeta = extractLegendMeta(contourFrame);
    const contourSpec = frameMeta?.contours?.iso32f;
    if (!contourSpec) {
      return null;
    }
    return buildContourUrl({
      model,
      run: resolvedRunForRequests,
      varKey: displayedOverlayVariable,
      fh: mapForecastHour,
      key: "iso32f",
    });
  }, [
    currentFrame,
    displayedOverlayVariable,
    firstWeatherFramePainted,
    frameByHour,
    hasRenderableSelection,
    mapForecastHour,
    model,
    resolvedRunForRequests,
  ]);

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

  const prefetchHours = useMemo(() => {
    if (!hasRenderableSelection || isLoopDisplayActive || frameHours.length === 0) {
      return [] as number[];
    }

    const ready = readyFramesRef.current;
    const failed = failedFramesRef.current;
    const inFlight = inFlightFramesRef.current;
    const bootstrapPrefetchBudget = deferPrefetchUntilFirstPaintEnabled && !firstWeatherFramePainted;
    const maxRequests = isPreloadingForPlay
      ? (bootstrapPrefetchBudget ? 4 : 8)
      : (bootstrapPrefetchBudget ? 2 : 4);
    const targetReady = isPreloadingForPlay
      ? frameHours.length
      : Math.min(frameHours.length, bootstrapPrefetchBudget ? 2 : playbackPolicy.bufferTarget);
    const activeInFlight = frameHours.filter((fh) => inFlight.has(fh)).slice(0, maxRequests);
    if (ready.size + inFlight.size >= targetReady) {
      return activeInFlight;
    }

    return selectPrefetchFrameHours({
      frameHours,
      forecastHour,
      maxRequests,
      targetReady,
      readyHours: ready,
      failedHours: failed,
      inFlightHours: inFlight,
      isPreloadingForPlay,
      isScrubbing,
      scrubCommitIntent,
      commitIntentTtlMs: INFLIGHT_FRAME_TTL_MS,
      neighborWindow: SCRUB_COMMIT_NEIGHBOR_WINDOW,
      nowMs: Date.now(),
      retryAtByHour: frameNextRetryAtRef.current,
    });
  }, [
    frameHours,
    forecastHour,
    bufferSnapshot.version,
    playbackPolicy.bufferTarget,
    isPreloadingForPlay,
    firstWeatherFramePainted,
    deferPrefetchUntilFirstPaintEnabled,
    isScrubbing,
    scrubRequestedHour,
    scrubCommitIntent,
    isLoopDisplayActive,
    hasRenderableSelection,
  ]);

  const prefetchTileUrls = useMemo(() => {
    return prefetchHours.map((fh) => tileUrlForHour(fh));
  }, [prefetchHours, tileUrlForHour]);

  const effectiveRunId = currentFrame?.run ?? (run !== "latest" ? run : latestRunId);
  const runDateTimeISO = runIdToIso(effectiveRunId);

  // ── Hover-for-data tooltip ──────────────────────────────────────────
  const { tooltip, onHover, onHoverEnd } = useSampleTooltip({
    model,
    run: resolvedRunForRequests,
    varId: variable,
    fh: forecastHour,
  });

  const markTileReady = useCallback((readyUrl: string) => {
    const now = Date.now();
    const ready = readyTileUrlsRef.current;
    ready.set(readyUrl, now);
    tileReadyViewportSignatureRef.current.set(readyUrl, viewportSignatureRef.current);

    // Only pay the eviction cost when the map is actually over budget.
    // The previous code iterated all 160 entries + spread them into an array on
    // every tile event regardless of map size.
    if (ready.size > READY_URL_LIMIT) {
      // First pass: evict TTL-expired entries.
      for (const [url, ts] of ready) {
        if (now - ts > READY_URL_TTL_MS) {
          ready.delete(url);
          tileReadySourceRef.current.delete(url);
          tileReadyViewportSignatureRef.current.delete(url);
        }
      }
      // If still over limit, find and remove the single oldest entry per iteration.
      // Excess is typically 1-2 entries, so a linear-scan minimum is cheaper than
      // spreading the whole map into a temporary array and sorting it.
      while (ready.size > READY_URL_LIMIT) {
        let oldestUrl: string | null = null;
        let oldestTs = Number.POSITIVE_INFINITY;
        for (const [url, ts] of ready) {
          if (ts < oldestTs) {
            oldestTs = ts;
            oldestUrl = url;
          }
        }
        if (oldestUrl !== null) {
          ready.delete(oldestUrl);
          tileReadySourceRef.current.delete(oldestUrl);
          tileReadyViewportSignatureRef.current.delete(oldestUrl);
        } else {
          break;
        }
      }
    }
  }, []);

  const markFrameReady = useCallback((readyUrl: string) => {
    const frameHour = tileUrlToHour.get(readyUrl);
    if (!Number.isFinite(frameHour)) {
      return;
    }
    readyFramesRef.current.add(frameHour as number);
    inFlightFramesRef.current.delete(frameHour as number);
    failedFramesRef.current.delete(frameHour as number);
    frameRetryCountRef.current.delete(frameHour as number);
    frameCycleStartedAtRef.current.delete(frameHour as number);
    frameNextRetryAtRef.current.delete(frameHour as number);

    const startedAt = inFlightStartedAtRef.current.get(frameHour as number);
    if (Number.isFinite(startedAt)) {
      const deltaMs = Date.now() - (startedAt as number);
      if (deltaMs >= 0) {
        readyLatencyStatsRef.current.totalMs += deltaMs;
        readyLatencyStatsRef.current.count += 1;
      }
      inFlightStartedAtRef.current.delete(frameHour as number);
    }
    // Coalesce snapshot updates to at most once per animation frame. Tile events
    // from 8 prefetch sources flood this path during animation — scheduling via
    // RAF prevents each tile from triggering a full React re-render cascade.
    if (bufferSnapshotRafRef.current === null) {
      bufferSnapshotRafRef.current = window.requestAnimationFrame(() => {
        bufferSnapshotRafRef.current = null;
        updateBufferSnapshot();
      });
    }
  }, [tileUrlToHour, updateBufferSnapshot]);

  const isTileReady = useCallback((url: string): boolean => {
    const ts = readyTileUrlsRef.current.get(url);
    if (!ts) return false;
    if (Date.now() - ts > READY_URL_TTL_MS) {
      readyTileUrlsRef.current.delete(url);
      tileReadySourceRef.current.delete(url);
      tileReadyViewportSignatureRef.current.delete(url);
      return false;
    }
    if (viewportAwareTileReadinessEnabled) {
      const readyViewport = tileReadyViewportSignatureRef.current.get(url);
      if (readyViewport && readyViewport !== viewportSignatureRef.current) {
        return false;
      }
    }
    return true;
  }, [viewportAwareTileReadinessEnabled]);

  useEffect(() => {
    latestTileUrlRef.current = tileUrl;
    setSettledTileUrl(isTileReady(tileUrl) ? tileUrl : null);
  }, [tileUrl, isTileReady]);

  useEffect(() => {
    const pendingVarSwitch = pendingVariableSwitchRef.current;
    if (!pendingVarSwitch) {
      return;
    }
    if (pendingVarSwitch.toVariableId !== variable) {
      return;
    }
    if (pendingVarSwitch.expectedSelectionKey !== loadedFramesKey) {
      return;
    }
    if (!tileUrl || tileUrl === EMPTY_TILE_DATA_URL) {
      return;
    }
    if (pendingVarSwitch.expectedTileUrl === tileUrl) {
      return;
    }
    pendingVarSwitch.expectedTileUrl = tileUrl;
    if (!Number.isFinite(pendingVarSwitch.firstTargetRequestAt)) {
      pendingVarSwitch.firstTargetRequestAt = performance.now();
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
  }, [loadedFramesKey, tileUrl, variable]);

  const isScrubLoading = useMemo(() => {
    if (isPlaying || isScrubbing) {
      return false;
    }
    return Boolean(mapLoadingTileUrl && mapLoadingTileUrl === tileUrl && settledTileUrl !== tileUrl);
  }, [isPlaying, isScrubbing, mapLoadingTileUrl, tileUrl, settledTileUrl]);

  const findNearestReadyTileScrubHour = useCallback(
    (requestedHour: number): number | null => {
      if (frameHours.length === 0) {
        return null;
      }
      const snappedHour = nearestFrame(frameHours, requestedHour);
      if (isTileReady(tileUrlForHour(snappedHour))) {
        return snappedHour;
      }

      const requestedIndex = frameHours.indexOf(snappedHour);
      if (requestedIndex < 0) {
        return null;
      }

      const movingForward = snappedHour >= forecastHour;
      const checkIndex = (index: number): number | null => {
        if (index < 0 || index >= frameHours.length) {
          return null;
        }
        const candidateHour = frameHours[index];
        if (!isTileReady(tileUrlForHour(candidateHour))) {
          return null;
        }
        return candidateHour;
      };

      for (let step = 1; step <= AUTOPLAY_SKIP_WINDOW; step += 1) {
        const primaryIndex = movingForward ? requestedIndex + step : requestedIndex - step;
        const primaryCandidate = checkIndex(primaryIndex);
        if (Number.isFinite(primaryCandidate)) {
          return primaryCandidate as number;
        }

        const secondaryIndex = movingForward ? requestedIndex - step : requestedIndex + step;
        const secondaryCandidate = checkIndex(secondaryIndex);
        if (Number.isFinite(secondaryCandidate)) {
          return secondaryCandidate as number;
        }
      }

      const currentCandidate = checkIndex(frameHours.indexOf(forecastHour));
      if (Number.isFinite(currentCandidate)) {
        return currentCandidate as number;
      }

      return null;
    },
    [frameHours, forecastHour, isTileReady, tileUrlForHour]
  );

  const findNearestDecodedLoopScrubHour = useCallback(
    (requestedHour: number, mode: RenderModeState): number | null => {
      if (mode === "tiles" || loopFrameHours.length === 0) {
        return null;
      }
      const snappedHour = nearestFrame(loopFrameHours, requestedHour);
      if (hasDecodedLoopFrame(snappedHour, mode)) {
        return snappedHour;
      }

      const pivotIndex = loopFrameHours.indexOf(snappedHour);
      if (pivotIndex < 0) {
        return null;
      }

      const movingForward = snappedHour >= forecastHour;
      for (let step = 1; step < loopFrameHours.length; step += 1) {
        const primaryIndex = movingForward ? pivotIndex + step : pivotIndex - step;
        if (primaryIndex >= 0 && primaryIndex < loopFrameHours.length) {
          const primaryHour = loopFrameHours[primaryIndex];
          if (hasDecodedLoopFrame(primaryHour, mode)) {
            return primaryHour;
          }
        }

        const secondaryIndex = movingForward ? pivotIndex - step : pivotIndex + step;
        if (secondaryIndex >= 0 && secondaryIndex < loopFrameHours.length) {
          const secondaryHour = loopFrameHours[secondaryIndex];
          if (hasDecodedLoopFrame(secondaryHour, mode)) {
            return secondaryHour;
          }
        }
      }

      return null;
    },
    [loopFrameHours, hasDecodedLoopFrame, forecastHour]
  );

  const handleFrameSettled = useCallback((
    loadedTileUrl: string,
    meta?: { selectionEpoch?: number; selectionKey?: string }
  ) => {
    if (
      (meta?.selectionEpoch !== undefined && meta.selectionEpoch !== selectionEpochRef.current)
      || (meta?.selectionKey !== undefined && meta.selectionKey !== selectionKey)
    ) {
      return;
    }
    markTileReady(loadedTileUrl);
    markFrameReady(loadedTileUrl);
    const pending = pendingFrameMetricRef.current;
    if (pending?.renderTarget === "tiles" && pending.expectedTileUrl === loadedTileUrl) {
      if (!Number.isFinite(pending.firstTileReadyAt)) {
        pending.firstTileReadyAt = performance.now();
      }
    }

    const pendingVarSwitch = pendingVariableSwitchRef.current;
    if (
      pendingVarSwitch
      && pendingVarSwitch.toVariableId === variable
      && pendingVarSwitch.expectedSelectionKey === loadedFramesKey
      && loadedTileUrl === tileUrl
      && !Number.isFinite(pendingVarSwitch.firstTargetReadyAt)
    ) {
      pendingVarSwitch.expectedTileUrl = loadedTileUrl;
      pendingVarSwitch.firstTargetReadyAt = performance.now();
    }

    if (loadedTileUrl === latestTileUrlRef.current) {
      setSettledTileUrl(loadedTileUrl);
    }
  }, [loadedFramesKey, markTileReady, markFrameReady, selectionKey]);

  const handleTileReady = useCallback((loadedTileUrl: string, meta?: TileReadyMeta) => {
    if (
      (meta?.selectionEpoch !== undefined && meta.selectionEpoch !== selectionEpochRef.current)
      || (meta?.selectionKey !== undefined && meta.selectionKey !== selectionKey)
    ) {
      return;
    }
    if (meta?.source) {
      tileReadySourceRef.current.set(loadedTileUrl, meta.source);
    }
    markTileReady(loadedTileUrl);
    markFrameReady(loadedTileUrl);
    const pending = pendingFrameMetricRef.current;
    if (pending?.renderTarget === "tiles" && pending.expectedTileUrl === loadedTileUrl) {
      if (!Number.isFinite(pending.firstTileReadyAt)) {
        pending.firstTileReadyAt = performance.now();
      }
      if (meta?.source) {
        pending.readySource = meta.source;
      }
    }

    const pendingVarSwitch = pendingVariableSwitchRef.current;
    if (
      pendingVarSwitch
      && pendingVarSwitch.toVariableId === variable
      && pendingVarSwitch.expectedSelectionKey === loadedFramesKey
      && loadedTileUrl === tileUrl
      && !Number.isFinite(pendingVarSwitch.firstTargetReadyAt)
    ) {
      pendingVarSwitch.expectedTileUrl = loadedTileUrl;
      pendingVarSwitch.firstTargetReadyAt = performance.now();
    }

    if (loadedTileUrl === latestTileUrlRef.current) {
      setSettledTileUrl(loadedTileUrl);
    }
  }, [loadedFramesKey, markTileReady, markFrameReady, selectionKey]);

  const handleFrameLoadingChange = useCallback((
    loadingTileUrl: string,
    isLoadingValue: boolean,
    meta?: { selectionEpoch?: number; selectionKey?: string }
  ) => {
    if (
      (meta?.selectionEpoch !== undefined && meta.selectionEpoch !== selectionEpochRef.current)
      || (meta?.selectionKey !== undefined && meta.selectionKey !== selectionKey)
    ) {
      return;
    }
    if (isLoadingValue) {
      const pending = pendingFrameMetricRef.current;
      if (
        pending
        && pending.renderTarget === "tiles"
        && pending.expectedTileUrl === loadingTileUrl
        && !Number.isFinite(pending.requestStartedAt)
      ) {
        pending.requestStartedAt = performance.now();
      }

      const pendingVarSwitch = pendingVariableSwitchRef.current;
      if (
        pendingVarSwitch
        && pendingVarSwitch.toVariableId === variable
        && pendingVarSwitch.expectedSelectionKey === loadedFramesKey
        && loadingTileUrl === tileUrl
        && !Number.isFinite(pendingVarSwitch.firstTargetRequestAt)
      ) {
        pendingVarSwitch.firstTargetRequestAt = performance.now();
        pendingVarSwitch.expectedTileUrl = loadingTileUrl;
        setVariableSwitchState((current) => {
          if (!current || current.toVariable !== variable) {
            return current;
          }
          return {
            ...current,
            visualState: "warming_new",
          };
        });
      }

      setMapLoadingTileUrl(loadingTileUrl);
      return;
    }
    setMapLoadingTileUrl((current) => (current === loadingTileUrl ? null : current));
  }, [loadedFramesKey, tileUrl, variable, selectionKey]);

  const cancelPendingVariableSwitch = useCallback((
    reason: "selection-mismatch" | "timeout",
    options?: { forceTiles?: boolean }
  ): boolean => {
    const pendingVarSwitch = pendingVariableSwitchRef.current;
    if (!pendingVarSwitch) {
      return false;
    }
    pendingVariableSwitchRef.current = null;
    clearLoopHoldover();
    if (options?.forceTiles) {
      resetLoopPresentationToTiles();
    }
    setVariableSwitchState(null);
    setVisualVariable(variable);
    return true;
  }, [clearLoopHoldover, loadedFramesKey, resetLoopPresentationToTiles, selectionKey, variable]);

  const finalizePendingVariableSwitch = useCallback((
    renderTarget: "tiles" | "loop",
    visibleAt: number,
    options?: {
      readyTileUrl?: string | null;
      loopHour?: number | null;
      loopRenderMode?: RenderModeState;
    }
  ): boolean => {
    const pendingVarSwitch = pendingVariableSwitchRef.current;
    if (
      !pendingVarSwitch
      || pendingVarSwitch.toVariableId !== variable
      || pendingVarSwitch.expectedSelectionKey !== loadedFramesKey
    ) {
      return false;
    }

    pendingVariableSwitchRef.current = null;
    clearLoopHoldover();
    if (renderTarget === "tiles") {
      resetLoopPresentationToTiles();
    }
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

    if (!Number.isFinite(pendingVarSwitch.firstTargetReadyAt)) {
      pendingVarSwitch.firstTargetReadyAt = visibleAt;
    }
    pendingVarSwitch.firstVisibleAt = visibleAt;

    if (renderTarget === "tiles") {
      const readyTileUrl = options?.readyTileUrl ?? null;
      const readyTs = readyTileUrl ? (readyTileUrlsRef.current.get(readyTileUrl) ?? null) : null;
      pendingVarSwitch.warmAtVisible = Number.isFinite(readyTs)
        ? Date.now() - (readyTs as number) <= READY_URL_TTL_MS
        : false;
      pendingVarSwitch.warmSourceAtVisible = readyTileUrl
        ? (tileReadySourceRef.current.get(readyTileUrl) ?? null)
        : null;
    } else {
      const loopHour = options?.loopHour ?? null;
      const loopRenderMode = options?.loopRenderMode ?? loopPlaybackRenderMode;
      pendingVarSwitch.warmAtVisible = Number.isFinite(loopHour)
        ? hasDecodedLoopFrame(loopHour as number, loopRenderMode)
        : null;
      pendingVarSwitch.warmSourceAtVisible = null;
    }

    const durationMs = visibleAt - pendingVarSwitch.startedAt;
    if (Number.isFinite(durationMs) && durationMs >= 0) {
      trackPerfEvent({
        event_name: "variable_switch",
        duration_ms: durationMs,
        model_id: pendingVarSwitch.modelId,
        variable_id: pendingVarSwitch.toVariableId,
        run_id: pendingVarSwitch.runId,
        region_id: pendingVarSwitch.regionId,
        meta: buildVariableSwitchPhase0aMeta(pendingVarSwitch, renderTarget),
      });
    }

    setVariableSwitchState(null);
    return true;
  }, [
    clearLoopHoldover,
    hasDecodedLoopFrame,
    loadedFramesKey,
    resetLoopPresentationToTiles,
    variable,
    loopPlaybackRenderMode,
  ]);

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

  const finalizePendingFrameMetric = useCallback((reason: "tile" | "loop") => {
    const pending = pendingFrameMetricRef.current;
    if (!pending) {
      return;
    }
    const durationMs = performance.now() - pending.startedAt;
    pendingFrameMetricRef.current = null;
    if (!Number.isFinite(durationMs) || durationMs < 0) {
      return;
    }
    const stageOffsetMs = (at: number | null): number | null => {
      if (!Number.isFinite(at)) {
        return null;
      }
      return Math.max(0, Math.round((at as number) - pending.startedAt));
    };
    const phase0aMeta: Record<string, unknown> = {
      phase0a_trace_version: 1,
      stage_request_start_ms: stageOffsetMs(pending.requestStartedAt),
      stage_tile_ready_ms: stageOffsetMs(pending.firstTileReadyAt),
      stage_first_visible_ms: stageOffsetMs(pending.firstVisibleAt),
      warm_at_start: pending.warmAtStart,
      warm_source_at_start: pending.warmSourceAtStart,
      ready_source: pending.readySource,
    };
    trackPerfEvent({
      event_name: pending.eventName,
      duration_ms: durationMs,
      model_id: pending.modelId,
      variable_id: pending.variableId,
      run_id: pending.runId,
      region_id: pending.regionId,
      forecast_hour: pending.forecastHour,
      meta: buildObservedTelemetryMeta(pending.forecastHour, {
        render_target: pending.renderTarget,
        completion: reason,
        ...(pending.traceMeta ?? {}),
        ...phase0aMeta,
      }),
    });
  }, [buildObservedTelemetryMeta]);

  const startPendingFrameMetric = useCallback(
    (args: {
      eventName: "frame_change" | "scrub_latency";
      renderTarget: "tiles" | "loop";
      expectedTileUrl?: string | null;
      expectedLoopHour?: number | null;
      forecastHour?: number | null;
      traceMeta?: Record<string, unknown> | null;
    }) => {
      const expectedTileUrl = args.expectedTileUrl ?? null;
      const now = Date.now();
      const readyTs = expectedTileUrl ? readyTileUrlsRef.current.get(expectedTileUrl) ?? null : null;
      const warmAtStart = Number.isFinite(readyTs) && now - (readyTs as number) <= READY_URL_TTL_MS;
      if (expectedTileUrl && Number.isFinite(readyTs) && !warmAtStart) {
        readyTileUrlsRef.current.delete(expectedTileUrl);
        tileReadySourceRef.current.delete(expectedTileUrl);
        tileReadyViewportSignatureRef.current.delete(expectedTileUrl);
      }
      pendingFrameMetricRef.current = {
        eventName: args.eventName,
        startedAt: performance.now(),
        renderTarget: args.renderTarget,
        expectedTileUrl,
        expectedLoopHour: args.expectedLoopHour ?? null,
        modelId: model || null,
        variableId: variable || null,
        runId: telemetryRunId,
        regionId: region || null,
        forecastHour: Number.isFinite(args.forecastHour) ? Number(args.forecastHour) : null,
        traceMeta: args.traceMeta ?? null,
        requestStartedAt: null,
        firstTileReadyAt: null,
        firstVisibleAt: null,
        readySource: null,
        warmAtStart: expectedTileUrl ? warmAtStart : null,
        warmSourceAtStart: expectedTileUrl
          ? (tileReadySourceRef.current.get(expectedTileUrl) ?? null)
          : null,
      };
    },
    [model, variable, telemetryRunId, region]
  );

  const startPendingLoopStartMetric = useCallback(() => {
    pendingLoopStartMetricRef.current = {
      startedAt: performance.now(),
      modelId: model || null,
      variableId: variable || null,
      runId: telemetryRunId,
      regionId: region || null,
      forecastHour: Number.isFinite(forecastHour) ? forecastHour : null,
    };
  }, [model, variable, telemetryRunId, region, forecastHour]);

  useEffect(() => {
    datasetGenerationRef.current += 1;
    pendingFrameMetricRef.current = null;
    pendingLoopStartMetricRef.current = null;
    readyFramesRef.current.clear();
    inFlightFramesRef.current.clear();
    failedFramesRef.current.clear();
    frameRetryCountRef.current.clear();
    frameCycleStartedAtRef.current.clear();
    frameNextRetryAtRef.current.clear();
    inFlightStartedAtRef.current.clear();
    readyLatencyStatsRef.current = { totalMs: 0, count: 0 };
    autoplayPrimedRef.current = false;
    loopDisplayDecodeAbortRef.current?.abort();
    loopDisplayDecodeAbortRef.current = null;
    foregroundDecodeHourRef.current = null;
    // Cancel any pending coalesced snapshot RAF and reset the equality baseline so
    // the first update after reset is never incorrectly skipped.
    if (bufferSnapshotRafRef.current !== null) {
      window.cancelAnimationFrame(bufferSnapshotRafRef.current);
      bufferSnapshotRafRef.current = null;
    }
    lastSnapshotStatsRef.current = { bufferedCount: -1, failedCount: -1, inFlightCount: -1, queueDepth: -1 };
    setIsLoopPreloading(false);
    setIsLoopAutoplayBuffering(false);
    setLoopProgress({ total: loopFrameHours.length, ready: 0, failed: 0 });
    setLoopDisplayHour(null);
    loopPreloadTokenRef.current += 1;
    loopReadyHoursRef.current.clear();
    loopFailedHoursRef.current.clear();
    for (const cached of loopDecodedCacheRef.current.values()) {
      cached.bitmap.close();
    }
    loopDecodedCacheRef.current.clear();
    loopDecodeCompletedAtRef.current.clear();
    loopDecodedCacheBytesRef.current = 0;
    // Invalidate the imperative playback bitmap map — the bitmaps it
    // referenced were just closed above and are now detached.
    playbackBitmapMapRef.current = null;
    imperativePlaybackHourRef.current = null;
    setIsPreloadingForPlay(false);
    lastTileViewportCommitUrlRef.current = null;
    preloadProgressRef.current = {
      lastBufferedCount: 0,
      lastProgressAt: Date.now(),
    };
    setScrubRequestedHour(null);
    setLoopDisplayBitmap(null);
    const version = ++bufferVersionRef.current;
    setBufferSnapshot({
      totalFrames: frameHours.length,
      bufferedCount: 0,
      bufferedAheadCount: 0,
      terminalCount: 0,
      terminalAheadCount: 0,
      failedCount: 0,
      inFlightCount: 0,
      queueDepth: frameHours.length,
      statusText: `Buffered 0/${frameHours.length}`,
      version,
    });
  }, [
    // Only the three selector values that uniquely identify a dataset change.
    // frameHours.length and loopFrameHours.length are derived state — including
    // them caused a second reset firing when frames were cleared then re-populated,
    // which wiped newly-decoded bitmaps and reset the whole buffer mid-load.
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

  useEffect(() => {
    if (!isLoopPreloading) {
      return;
    }
    if (!canUseLoopPlayback || loopFrameHours.length === 0) {
      setIsLoopPreloading(false);
      setRenderMode("tiles");
      return;
    }

    const token = ++loopPreloadTokenRef.current;
    const readySet = new Set<number>();
    const failedSet = new Set<number>();
    loopReadyHoursRef.current = readySet;
    loopFailedHoursRef.current = failedSet;
    setLoopProgress({ total: loopFrameHours.length, ready: 0, failed: 0 });

    // Reorder frames so decoding starts at the nearest frame to the current
    // forecast hour, proceeds forward to the end, then wraps to the beginning.
    // This prioritises frames the user will see first, enabling early start and
    // smooth playback well before all frames are decoded.
    let nearestIdx = 0;
    let nearestDist = Infinity;
    for (let i = 0; i < loopFrameHours.length; i++) {
      const dist = Math.abs(loopFrameHours[i] - forecastHour);
      if (dist < nearestDist) {
        nearestDist = dist;
        nearestIdx = i;
      }
    }
    const orderedFrames: number[] = [
      ...loopFrameHours.slice(nearestIdx),
      ...loopFrameHours.slice(0, nearestIdx),
    ];

    // RAF-coalesced progress updates: with PRELOAD_CONCURRENCY=4, multiple decodes
    // can complete within the same 16ms frame. Batching them into a single setState
    // call eliminates N intermediate re-renders while frames are loading.
    let progressRafId: number | null = null;
    const flushProgress = () => {
      if (token !== loopPreloadTokenRef.current) return;
      setLoopProgress({ total: loopFrameHours.length, ready: readySet.size, failed: failedSet.size });
    };
    const scheduleProgress = () => {
      if (progressRafId !== null) return;
      progressRafId = window.requestAnimationFrame(() => {
        progressRafId = null;
        flushProgress();
      });
    };

    // Attempt to start playback early once the loop playback policy's minimum
    // decoded frames exist ahead of the current position. Starting autoplay on
    // URL presence alone puts the UI into a "playing" state while frame advance
    // is still blocked on decode, which reads as broken animation.
    let earlyStarted = false;
    const tryEarlyStart = (): boolean => {
      if (earlyStarted) return false;
      const currentIdx = loopFrameHours.indexOf(forecastHour);
      if (currentIdx < 0) return false;
      const remainingAhead = loopFrameHours.length - 1 - currentIdx;
      const neededAhead = Math.min(loopPlaybackPolicy.minStartBuffer, remainingAhead);
      if (neededAhead <= 0) return false;
      let consecutiveAhead = 0;
      for (let i = currentIdx + 1; i < loopFrameHours.length && consecutiveAhead < neededAhead; i++) {
        if (isLoopFrameReadyForPresentation(loopFrameHours[i], renderMode, "canvas")) {
          consecutiveAhead++;
        } else {
          break;
        }
      }
      if (consecutiveAhead < neededAhead) return false;
      earlyStarted = true;
      if (progressRafId !== null) {
        window.cancelAnimationFrame(progressRafId);
        progressRafId = null;
      }
      flushProgress();
      setIsLoopPreloading(false);
      if (renderMode !== "tiles") {
        setVisibleRenderMode(renderMode);
      }
      setLoopDisplayHour(forecastHour);
      setIsPlaying(true);
      return true;
    };

    if (tryEarlyStart()) {
      return () => {
        loopPreloadTokenRef.current += 1;
        if (progressRafId !== null) {
          window.cancelAnimationFrame(progressRafId);
          progressRafId = null;
        }
      };
    }

    const mark = (fh: number, ok: boolean) => {
      if (token !== loopPreloadTokenRef.current) {
        return;
      }
      if (ok) {
        readySet.add(fh);
      } else {
        failedSet.add(fh);
      }

      if (readySet.size + failedSet.size < loopFrameHours.length) {
        // Not all frames accounted for yet. Attempt an early start if enough
        // consecutive frames are ready ahead of the current position — remaining
        // decodes continue in background via processNext() to warm the LRU cache.
        if (ok && tryEarlyStart()) return;
        scheduleProgress();
        return;
      }

      // All frames accounted for — flush progress synchronously then transition.
      if (progressRafId !== null) {
        window.cancelAnimationFrame(progressRafId);
        progressRafId = null;
      }
      flushProgress();
      if (earlyStarted) return;
      setIsLoopPreloading(false);
      const minReady = Math.min(loopPlaybackPolicy.minStartBuffer, loopFrameHours.length);
      if (readySet.size >= minReady) {
        if (renderMode !== "tiles") {
          setVisibleRenderMode(renderMode);
        }
        setLoopDisplayHour(forecastHour);
        setIsPlaying(true);
        return;
      }
      setRenderMode("tiles");
      setIsPlaying(false);
      showTransientFrameStatus("Loop preload failed");
    };

    // Process frames in priority order (starting at current forecast hour) with
    // bounded concurrency to stay within the browser's HTTP/2 stream budget.
    let inFlight = 0;
    let nextIndex = 0;
    let stopped = false;

    const processNext = () => {
      // Stop launching new decodes once the effect is cleaned up (early start
      // or unmount). Already-in-flight fetches complete but won't chain further,
      // preventing runaway cache filling that evicts the frames playback needs.
      if (stopped) return;
      while (inFlight < loopPlaybackPolicy.maxCriticalInFlight && nextIndex < orderedFrames.length) {
        const fh = orderedFrames[nextIndex];
        nextIndex += 1;
        if (!resolveLoopUrlForHour(fh, renderMode)) {
          mark(fh, false);
          continue;
        }
        inFlight += 1;
        ensureLoopFrameDecoded(fh, renderMode)
          .then((ready) => mark(fh, ready))
          .catch(() => mark(fh, false))
          .finally(() => {
            inFlight -= 1;
            processNext();
          });
      }
    };
    processNext();

    return () => {
      stopped = true;
      loopPreloadTokenRef.current += 1;
      if (progressRafId !== null) {
        window.cancelAnimationFrame(progressRafId);
        progressRafId = null;
      }
    };
  }, [
    isLoopPreloading,
    canUseLoopPlayback,
    loopFrameHours,
    resolveLoopUrlForHour,
    showTransientFrameStatus,
    renderMode,
    forecastHour,
    ensureLoopFrameDecoded,
    isLoopFrameReadyForPresentation,
    loopPlaybackPolicy.maxCriticalInFlight,
    loopPlaybackPolicy.minStartBuffer,
  ]);

  useEffect(() => {
    if (!isLoopDisplayActive || !Number.isFinite(loopDisplayHour)) {
      loopDisplayCommitRef.current = null;
      return;
    }
    const displayHour = loopDisplayHour as number;
    const cacheKey = loopCacheKey(displayHour, loopPlaybackRenderMode);
    const decodedAt = loopDecodeCompletedAtRef.current.get(cacheKey) ?? null;
    loopDisplayCommitRef.current = {
      token: ++loopDisplayCommitTokenRef.current,
      displayHour,
      renderMode: loopPlaybackRenderMode,
      committedAt: performance.now(),
      decodedAt: Number.isFinite(decodedAt) ? (decodedAt as number) : null,
      presentationPath: "canvas",
    };
  }, [isLoopDisplayActive, loopDisplayHour, loopPlaybackRenderMode, loopCacheKey, getDecodedLoopBitmap]);

  // loopDisplayBitmap is cleared at selection/reset boundaries below;
  // per-frame sync is handled directly by activeLoopBitmap reading the LRU
  // cache, so no separate effect is needed here.

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
    trackPerfEvent({
      event_name: "viewer_first_frame",
      duration_ms: durationMs,
      model_id: modelRef.current || null,
      variable_id: variableRef.current || null,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(frameHour) ? frameHour : null,
      meta: buildObservedTelemetryMeta(frameHour),
    });
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
  }, [buildObservedTelemetryMeta, telemetryRunId, region, hasRenderableSelection, loadedFramesKey]);

  useEffect(() => {
    if (!isLoopDisplayActive || !Number.isFinite(loopDisplayHour)) {
      return;
    }
    const commit = loopDisplayCommitRef.current;
    if (!commit || commit.displayHour !== loopDisplayHour || commit.renderMode !== loopPlaybackRenderMode) {
      return;
    }
    if (loopDisplayPaintedTokenRef.current === commit.token) {
      return;
    }

    if (Number.isFinite(commit.decodedAt)) {
      const decodedToCommitMs = commit.committedAt - (commit.decodedAt as number);
      if (Number.isFinite(decodedToCommitMs) && decodedToCommitMs >= 0) {
        trackPerfEvent({
          event_name: "loop_decode_to_commit",
          duration_ms: decodedToCommitMs,
          model_id: modelRef.current || null,
          variable_id: variableRef.current || null,
          run_id: telemetryRunId,
          region_id: region || null,
          forecast_hour: commit.displayHour,
          meta: buildObservedTelemetryMeta(commit.displayHour, {
            render_mode: commit.renderMode,
            presentation_path: commit.presentationPath,
          }),
        });
      }
    }

    const paintToken = ++loopVisiblePaintTokenRef.current;
    let rafBId: number | null = null;
    const rafA = window.requestAnimationFrame(() => {
      rafBId = window.requestAnimationFrame(() => {
        if (paintToken !== loopVisiblePaintTokenRef.current) {
          return;
        }
        if (loopDisplayCommitRef.current?.token !== commit.token) {
          return;
        }
        loopDisplayPaintedTokenRef.current = commit.token;
        const visibleAt = performance.now();
        const durationMs = visibleAt - commit.committedAt;
        if (!Number.isFinite(durationMs) || durationMs < 0) {
          return;
        }
        trackPerfEvent({
          event_name: "loop_commit_to_visible",
          duration_ms: durationMs,
          model_id: modelRef.current || null,
          variable_id: variableRef.current || null,
          run_id: telemetryRunId,
          region_id: region || null,
          forecast_hour: commit.displayHour,
          meta: buildObservedTelemetryMeta(commit.displayHour, {
            render_mode: commit.renderMode,
            presentation_path: commit.presentationPath,
          }),
        });

        const pending = pendingFrameMetricRef.current;
        if (pending && pending.renderTarget === "loop" && pending.expectedLoopHour === commit.displayHour) {
          if (!Number.isFinite(pending.firstVisibleAt)) {
            pending.firstVisibleAt = visibleAt;
          }
          finalizePendingFrameMetric("loop");
        }

        finalizePendingVariableSwitch("loop", visibleAt, {
          loopHour: commit.displayHour,
          loopRenderMode: commit.renderMode,
        });

        const pendingLoopStart = pendingLoopStartMetricRef.current;
        if (isPlaying && pendingLoopStart && commit.displayHour !== pendingLoopStart.forecastHour) {
          pendingLoopStartMetricRef.current = null;
          const loopStartMs = visibleAt - pendingLoopStart.startedAt;
          if (Number.isFinite(loopStartMs) && loopStartMs >= 0) {
            trackPerfEvent({
              event_name: "loop_start",
              duration_ms: loopStartMs,
              model_id: pendingLoopStart.modelId,
              variable_id: pendingLoopStart.variableId,
              run_id: pendingLoopStart.runId,
              region_id: pendingLoopStart.regionId,
              forecast_hour: commit.displayHour,
              meta: buildObservedTelemetryMeta(commit.displayHour),
            });
          }
        }

        trackFirstViewerFrame(commit.displayHour);
      });
    });

    return () => {
      window.cancelAnimationFrame(rafA);
      if (rafBId !== null) {
        window.cancelAnimationFrame(rafBId);
      }
    };
  }, [
    isLoopDisplayActive,
    loopDisplayHour,
    loopPlaybackRenderMode,
    loadedFramesKey,
    telemetryRunId,
    region,
    buildObservedTelemetryMeta,
    finalizePendingFrameMetric,
    finalizePendingVariableSwitch,
    isPlaying,
    trackFirstViewerFrame,
    variable,
  ]);

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

  useEffect(() => {
    if (!loopSelectionReady || !canUseLoopPlayback || loopFrameHours.length === 0) {
      return;
    }

    const warmSelectionKey = `${selectionKey}:${stagedLoopWarmupMode}`;
    if (warmedLoopSelectionKeyRef.current === warmSelectionKey) {
      return;
    }
    warmedLoopSelectionKeyRef.current = warmSelectionKey;

    const token = ++loopUrlWarmTokenRef.current;
    const controller = new AbortController();
    const urls = Array.from(
      new Set(
        loopFrameHours
          .map((fh) => resolveLoopUrlForHour(fh, stagedLoopWarmupMode))
          .filter((url): url is string => Boolean(url))
      )
    );

    let nextIndex = 0;
    let inFlight = 0;
    const maxConcurrency = 4;

    const launchNext = () => {
      if (controller.signal.aborted || token !== loopUrlWarmTokenRef.current) {
        return;
      }
      while (inFlight < maxConcurrency && nextIndex < urls.length) {
        const url = urls[nextIndex];
        nextIndex += 1;
        inFlight += 1;
        warmLoopImageUrl(url, controller.signal)
          .catch(() => false)
          .finally(() => {
            inFlight -= 1;
            launchNext();
          });
      }
    };

    launchNext();

    return () => {
      controller.abort();
    };
  }, [
    loopSelectionReady,
    canUseLoopPlayback,
    stagedLoopWarmupMode,
    loopFrameHours,
    resolveLoopUrlForHour,
    selectionKey,
  ]);

  useEffect(() => {
    const decodeMode = isLoopDisplayActive ? loopPlaybackRenderMode : stagedLoopWarmupMode;
    const shouldWarmLoopFrames =
      loopSelectionReady
      && canUseLoopPlayback
      && loopFrameHours.length > 0;
    if (!shouldWarmLoopFrames) {
      return;
    }

    let cancelled = false;
    let scheduleTimer: number | null = null;
    const inFlight = new Set<number>();
    const inFlightLane = new Map<number, "critical" | "idle">();
    const controllers = new Map<number, AbortController>();

    const countInFlightForLane = (lane: "critical" | "idle"): number => {
      let count = 0;
      for (const activeLane of inFlightLane.values()) {
        if (activeLane === lane) {
          count += 1;
        }
      }
      return count;
    };

    const abortIdleDecodes = () => {
      for (const [fh, lane] of inFlightLane.entries()) {
        if (lane !== "idle") {
          continue;
        }
        controllers.get(fh)?.abort();
      }
    };

    const queueSchedulePrefetch = (delayMs: number) => {
      if (cancelled || scheduleTimer !== null) {
        return;
      }
      scheduleTimer = window.setTimeout(() => {
        scheduleTimer = null;
        schedulePrefetch();
      }, delayMs);
    };

    const launchDecode = (fh: number, lane: "critical" | "idle") => {
      if (cancelled || inFlight.has(fh)) {
        return;
      }
      const controller = new AbortController();
      inFlight.add(fh);
      inFlightLane.set(fh, lane);
      controllers.set(fh, controller);
      ensureLoopFrameDecoded(fh, decodeMode, controller.signal)
        .catch(() => {
          // best-effort prefetch; decode failures are handled by fallback path.
        })
        .finally(() => {
          inFlight.delete(fh);
          inFlightLane.delete(fh);
          controllers.delete(fh);
          queueSchedulePrefetch(0);
        });
    };

    const schedulePrefetch = () => {
      if (cancelled) {
        return;
      }
      // Read from ref so this closure always sees the latest playback position
      // without causing the effect to restart (which would abort in-flight decodes).
      const currentHour = forecastHourRef.current;
      const currentIndex = loopFrameHours.indexOf(currentHour);
      if (currentIndex < 0) {
        return;
      }

      const remainingAhead = Math.max(0, loopFrameHours.length - 1 - currentIndex);
      const targetAhead = (isPlaying || isLoopPreloading || isLoopAutoplayBuffering)
        ? Math.min(loopPlaybackPolicy.targetWarmAhead, remainingAhead)
        : remainingAhead;
      if (targetAhead <= 0) {
        return;
      }

      const recentReadyMs = recentMedianSample(loopDecodeReadySamplesRef.current);
      const recentDecodeMs = recentMedianSample(loopDecodeOnlySamplesRef.current);
      const cachePressure = webpDecodeCacheBudgetBytes > 0
        ? loopDecodedCacheBytesRef.current / webpDecodeCacheBudgetBytes
        : 0;
      const highCachePressure = cachePressure >= 0.82;
      const slowCriticalDecode =
        (Number.isFinite(recentReadyMs) && (recentReadyMs as number) >= AUTOPLAY_TICK_MS * 0.8)
        || (Number.isFinite(recentDecodeMs) && (recentDecodeMs as number) >= AUTOPLAY_TICK_MS * 0.45);
      const criticalConcurrencyCap = Math.max(
        2,
        Math.min(
          6,
          loopPlaybackPolicy.maxCriticalInFlight + (!highCachePressure && (isLoopAutoplayBuffering || slowCriticalDecode) ? 1 : 0),
        ),
      );
      const idleConcurrencyCap = highCachePressure
        ? 0
        : Math.min(loopPlaybackPolicy.maxIdleInFlight, Math.max(1, criticalConcurrencyCap - 3));

      const shortAheadTarget = (isPlaying || isLoopPreloading || isLoopAutoplayBuffering)
        ? Math.min(loopPlaybackPolicy.shortAheadTarget, targetAhead)
        : Math.min(Math.max(loopPlaybackPolicy.shortAheadTarget, 4), targetAhead);
      const criticalCandidates: number[] = [];
      const idleCandidates: number[] = [];
      const criticalEndIndex = Math.min(loopFrameHours.length - 1, currentIndex + shortAheadTarget);
      const idleEndIndex = Math.min(loopFrameHours.length - 1, currentIndex + targetAhead);

      for (let index = currentIndex + 1; index <= criticalEndIndex; index += 1) {
        const fh = loopFrameHours[index];
        if (hasDecodedLoopFrame(fh, decodeMode)) {
          continue;
        }
        if (inFlight.has(fh)) {
          continue;
        }
        criticalCandidates.push(fh);
      }

      for (let index = criticalEndIndex + 1; index <= idleEndIndex; index += 1) {
        const fh = loopFrameHours[index];
        if (hasDecodedLoopFrame(fh, decodeMode)) {
          continue;
        }
        if (inFlight.has(fh)) {
          continue;
        }
        idleCandidates.push(fh);
      }

      const suspendIdleLane = isLoopAutoplayBuffering
        || isScrubbing
        || highCachePressure
        || Boolean(
          variableSwitchState
          && variableSwitchState.toVariable === variable
          && variableSwitchState.visualState !== "promoting_new"
        );

      if (suspendIdleLane || criticalCandidates.length > 0) {
        abortIdleDecodes();
      }

      const availableCriticalSlots = Math.max(
        0,
        criticalConcurrencyCap - countInFlightForLane("critical"),
      );
      if (availableCriticalSlots > 0) {
        for (const fh of criticalCandidates.slice(0, availableCriticalSlots)) {
          launchDecode(fh, "critical");
        }
      }

      if (suspendIdleLane || criticalCandidates.length > 0) {
        return;
      }

      const availableIdleSlots = Math.max(
        0,
        idleConcurrencyCap - countInFlightForLane("idle"),
      );
      if (availableIdleSlots <= 0) {
        return;
      }

      for (const fh of idleCandidates.slice(0, availableIdleSlots)) {
        launchDecode(fh, "idle");
      }
    };

    schedulePrefetch();
    const interval = window.setInterval(
      schedulePrefetch,
      isPlaying || isLoopAutoplayBuffering ? 180 : 450,
    );

    return () => {
      cancelled = true;
      window.clearInterval(interval);
      if (scheduleTimer !== null) {
        window.clearTimeout(scheduleTimer);
        scheduleTimer = null;
      }
      for (const controller of controllers.values()) {
        controller.abort();
      }
      controllers.clear();
      inFlight.clear();
    };
  }, [
    isLoopDisplayActive,
    loopSelectionReady,
    canUseLoopPlayback,
    stagedLoopWarmupMode,
    isPlaying,
    loopPlaybackRenderMode,
    loopFrameHours,
    ensureLoopFrameDecoded,
    hasDecodedLoopFrame,
    isLoopAutoplayBuffering,
    loopPlaybackPolicy.maxCriticalInFlight,
    loopPlaybackPolicy.maxIdleInFlight,
    loopPlaybackPolicy.shortAheadTarget,
    loopPlaybackPolicy.targetWarmAhead,
    isScrubbing,
    variableSwitchState,
    variable,
    webpDecodeCacheBudgetBytes,
  ]);

  // Playback ticker. Uses requestAnimationFrame plus an accumulator so cadence
  // tracks elapsed time without interval drift or teardown/rebuild churn.
  // Canvas-backed playback advances only when the next decoded bitmap is ready.
  //
  // FAST PATH: When the imperative draw handle is available, the ticker draws
  // decoded bitmaps directly to the MapLibre canvas source without triggering
  // React re-renders.  React state (forecastHour, targetForecastHour) is synced
  // at a lower cadence (~100 ms) so the timeline slider still tracks the
  // playhead.  This eliminates 4-16 ms of per-frame React reconciliation from
  // the hot path.
  useEffect(() => {
    if (!isPlaying || renderMode === "tiles" || loopFrameHours.length === 0) {
      return;
    }

    // Build the initial bitmap map for the fast path.
    const mode = visibleRenderModeRef.current;
    playbackBitmapMapRef.current = buildPlaybackBitmapMap(loopFrameHoursRef.current, mode);
    imperativePlaybackHourRef.current = null;

    // Interval (ms) at which we flush the imperative playhead position back
    // into React state so the timeline slider, valid-time label, etc. update.
    const STATE_SYNC_INTERVAL_MS = 100;
    let lastStateSyncTs = performance.now();

    lastLoopAdvanceRef.current = Date.now();
    let rafId: number | null = null;
    let previousTs = performance.now();
    let accumulatedMs = 0;

    /** Flush the imperatively-tracked playhead into React state. */
    const syncStateNow = () => {
      const hour = imperativePlaybackHourRef.current;
      if (hour !== null) {
        setTargetForecastHour(hour);
        setForecastHour(hour);
        imperativePlaybackHourRef.current = null;
      }
    };

    const tick = (now: number) => {
      const frameHours = loopFrameHoursRef.current;
      const tickMode = visibleRenderModeRef.current;
      const currentHour = imperativePlaybackHourRef.current ?? forecastHourRef.current;
      const currentIndex = frameHours.indexOf(currentHour);
      if (currentIndex < 0) {
        previousTs = now;
        rafId = window.requestAnimationFrame(tick);
        return;
      }

      const deltaMs = Math.max(0, now - previousTs);
      previousTs = now;
      accumulatedMs = Math.min(accumulatedMs + deltaMs, AUTOPLAY_TICK_MS * 4);

      const nextIndex = currentIndex + 1;
      if (nextIndex >= frameHours.length) {
        // End of sequence — sync final state before stopping.
        syncStateNow();
        lastLoopAdvanceRef.current = null;
        setIsPlaying(false);
        setIsLoopAutoplayBuffering(false);
        return;
      }

      const nextHour = frameHours[nextIndex];
      const remainingAhead = Math.max(0, frameHours.length - 1 - currentIndex);
      const minAheadRequired = Math.min(loopMinAheadWhilePlayingRef.current, remainingAhead);
      const readyAhead = countAheadReadyLoopFramesRef.current(currentHour, tickMode, minAheadRequired, "canvas");
      const shouldBuffer = minAheadRequired > 0 && readyAhead < minAheadRequired;
      setIsLoopAutoplayBuffering((current) => (current === shouldBuffer ? current : shouldBuffer));

      if (accumulatedMs >= AUTOPLAY_TICK_MS) {
        if (isLoopFrameReadyForPresentationRef.current(nextHour, tickMode, "canvas")) {
          accumulatedMs -= AUTOPLAY_TICK_MS;
          lastLoopAdvanceRef.current = Date.now();

          // --- FAST PATH: imperative draw bypassing React ---
          const drawFn = drawLoopFrameImperativeRef.current;
          const bitmapMap = playbackBitmapMapRef.current;
          const bitmap = bitmapMap?.get(nextHour);
          // Guard against detached bitmaps — LRU eviction or dataset-change
          // cache clears call `.close()` which zeros width/height.
          const bitmapValid = bitmap && bitmap.width > 0;
          if (drawFn && bitmapValid) {
            const drawn = drawFn(bitmap);
            if (drawn) {
              forecastHourRef.current = nextHour;
              imperativePlaybackHourRef.current = nextHour;

              // Throttled React state sync.
              if (now - lastStateSyncTs >= STATE_SYNC_INTERVAL_MS) {
                lastStateSyncTs = now;
                syncStateNow();
              }
            } else {
              // Draw failed (e.g. bitmap detached mid-draw) — refresh map.
              playbackBitmapMapRef.current = buildPlaybackBitmapMap(frameHours, tickMode);
              forecastHourRef.current = nextHour;
              imperativePlaybackHourRef.current = nextHour;
              setTargetForecastHour(nextHour);
            }
          } else {
            // --- FALLBACK: go through React state (e.g. frame decoded after
            //     map was built, or draw handle not yet available). ---
            // Refresh the bitmap map so the next tick can use the fast path.
            playbackBitmapMapRef.current = buildPlaybackBitmapMap(frameHours, tickMode);
            // Advance the imperative playhead so subsequent ticks don't re-try
            // the same hour and stall — the frame will be drawn via React's
            // prop-based path (or the next tick will pick up the new bitmap).
            forecastHourRef.current = nextHour;
            imperativePlaybackHourRef.current = nextHour;
            setTargetForecastHour(nextHour);
          }
        } else {
          // Frame not ready — refresh map in case new decodes completed.
          playbackBitmapMapRef.current = buildPlaybackBitmapMap(frameHours, tickMode);

          const wallClockNow = Date.now();
          const lastAdvance = lastLoopAdvanceRef.current;
          if (lastAdvance !== null) {
            const gapMs = wallClockNow - lastAdvance;
            if (gapMs > AUTOPLAY_TICK_MS) {
              loopFrameDropSampleCounterRef.current += 1;
              if (loopFrameDropSampleCounterRef.current % 4 === 0) {
                trackPerfEvent({
                  event_name: "loop_frame_drop_gap",
                  duration_ms: gapMs,
                  model_id: modelRef.current || null,
                  variable_id: variableRef.current || null,
                  forecast_hour: nextHour,
                  meta: {
                    render_mode: tickMode,
                  },
                });
                trackRumDiagnosticMetric({
                  metric_name: "frame_drop_bucket",
                  metric_value: 1,
                  metric_unit: "count",
                  model_id: modelRef.current || null,
                  variable_id: variableRef.current || null,
                  forecast_hour: nextHour,
                  meta: {
                    render_mode: tickMode,
                    bucket:
                      gapMs >= 1000
                        ? "1000ms_plus"
                        : gapMs >= 500
                          ? "500ms_to_999ms"
                          : "250ms_to_499ms",
                  },
                });
              }
            }
            if (gapMs > AUTOPLAY_TICK_MS * 2) {
              lastLoopAdvanceRef.current = wallClockNow;
              trackPerfEvent({
                event_name: "animation_stall",
                duration_ms: gapMs,
                model_id: modelRef.current || null,
                variable_id: variableRef.current || null,
              });
              trackRumDiagnosticMetric({
                metric_name: "animation_stall_count",
                metric_value: 1,
                metric_unit: "count",
                model_id: modelRef.current || null,
                variable_id: variableRef.current || null,
                forecast_hour: nextHour,
                meta: {
                  render_mode: tickMode,
                  stall_ms: gapMs,
                },
              });
            }
          }
        }
      }

      rafId = window.requestAnimationFrame(tick);
    };

    rafId = window.requestAnimationFrame(tick);

    return () => {
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId);
      }
      // Flush any outstanding imperative playhead position into React state
      // so the UI is consistent after the effect tears down.
      syncStateNow();
      playbackBitmapMapRef.current = null;
      lastLoopAdvanceRef.current = null;
    };
  }, [
    isPlaying,
    renderMode,
    loopFrameHours,
    buildPlaybackBitmapMap,
  ]);

  useEffect(() => {
    updateBufferSnapshot();
  }, [updateBufferSnapshot]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      updateBufferSnapshot();
    }, 1000);
    return () => {
      window.clearInterval(interval);
    };
  }, [updateBufferSnapshot]);

  useEffect(() => {
    const inFlight = inFlightFramesRef.current;
    const ready = readyFramesRef.current;
    const failed = failedFramesRef.current;
    const requested = new Set(prefetchHours);
    let changed = false;

    for (const fh of inFlight) {
      if (!requested.has(fh) || ready.has(fh)) {
        inFlight.delete(fh);
        inFlightStartedAtRef.current.delete(fh);
        changed = true;
      }
    }

    for (const fh of prefetchHours) {
      if (!ready.has(fh) && !inFlight.has(fh)) {
        if (failed.has(fh)) {
          failed.delete(fh);
        }
        inFlight.add(fh);
        if (!frameCycleStartedAtRef.current.has(fh)) {
          frameCycleStartedAtRef.current.set(fh, Date.now());
        }
        inFlightStartedAtRef.current.set(fh, Date.now());
        changed = true;
      }
    }
    if (changed) {
      updateBufferSnapshot();
    }
  }, [prefetchHours, updateBufferSnapshot]);

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

      if (reason === "standard") {
        setScrubRequestedHour(null);
        setScrubCommitIntent(null);
        pendingScrubHourRef.current = null;
        scrubPhase0aRef.current = emptyScrubPhase0aSnapshot();
        if (isGridPlayable) {
          const nextGridHour = gridFrameHours.length > 0 ? nearestFrame(gridFrameHours, requestedHour) : requestedHour;
          setTargetForecastHour(nextGridHour);
          return;
        }
        const snappedHour = frameHours.length > 0 ? nearestFrame(frameHours, requestedHour) : requestedHour;
        const nextLoopHour = loopFrameHours.length > 0 ? nearestFrame(loopFrameHours, requestedHour) : snappedHour;
        startPendingFrameMetric({
          eventName: "frame_change",
          renderTarget: isLoopDisplayActive ? "loop" : "tiles",
          expectedTileUrl: isLoopDisplayActive ? null : tileUrlForHour(snappedHour),
          expectedLoopHour: isLoopDisplayActive ? nextLoopHour : null,
          forecastHour: isLoopDisplayActive ? nextLoopHour : snappedHour,
        });
        setTargetForecastHour(requestedHour);
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

        if (isGridPlayable) {
          const nextGridHour = gridFrameHours.length > 0
            ? nearestFrame(gridFrameHours, requestedHour)
            : requestedHour;
          setScrubCommitIntent({
            hour: nextGridHour,
            direction: inferDirection(nextGridHour),
            startedAt: Date.now(),
          });
          setTargetForecastHour(nextGridHour);
          return;
        }

        if (!isLoopDisplayActive) {
          if (frameHours.length === 0) {
            return;
          }
          const snappedTileHour = nearestFrame(frameHours, requestedHour);
          setScrubCommitIntent({
            hour: snappedTileHour,
            direction: inferDirection(snappedTileHour),
            startedAt: Date.now(),
          });
          startPendingFrameMetric({
            eventName: treatCommitAsFrameChange ? "frame_change" : "scrub_latency",
            renderTarget: "tiles",
            expectedTileUrl: tileUrlForHour(snappedTileHour),
            expectedLoopHour: null,
            forecastHour: snappedTileHour,
            traceMeta: scrubTraceMeta,
          });
          setTargetForecastHour(snappedTileHour);
          return;
        }

        const nextHour = loopFrameHours.length > 0
          ? nearestFrame(loopFrameHours, requestedHour)
          : requestedHour;
        setScrubCommitIntent({
          hour: nextHour,
          direction: inferDirection(nextHour),
          startedAt: Date.now(),
        });
        startPendingFrameMetric({
          eventName: treatCommitAsFrameChange ? "frame_change" : "scrub_latency",
          renderTarget: "loop",
          expectedTileUrl: null,
          expectedLoopHour: nextHour,
          forecastHour: nextHour,
          traceMeta: scrubTraceMeta,
        });

        setTargetForecastHour(nextHour);
        if (hasDecodedLoopFrame(nextHour, loopPlaybackRenderMode)) {
          setLoopDisplayHour(nextHour);
        } else {
          startForegroundLoopFrameDecode(nextHour, loopPlaybackRenderMode, () => {
            setLoopDisplayHour(nextHour);
          });
        }
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
      if (scrubRafRef.current !== null) {
        return;
      }

      scrubRafRef.current = window.requestAnimationFrame(() => {
        scrubRafRef.current = null;
        const latestRequestedHour = pendingScrubHourRef.current;
        if (!Number.isFinite(latestRequestedHour)) {
          return;
        }
        const requested = latestRequestedHour as number;
        if (isGridPlayable) {
          const nextGridHour = gridFrameHours.length > 0
            ? nearestFrame(gridFrameHours, requested)
            : requested;
          setTargetForecastHour(nextGridHour);
          return;
        }
        if (!isLoopDisplayActive) {
          // Tile mode is static-only. Live scrub updates are disabled so the
          // overlay only changes on scrub commit.
          return;
        }

        const nextHour = loopFrameHours.length > 0
          ? nearestFrame(loopFrameHours, requested)
          : requested;
        setTargetForecastHour(nextHour);
        if (hasDecodedLoopFrame(nextHour, loopPlaybackRenderMode)) {
          setLoopDisplayHour(nextHour);
        } else {
          // Show the nearest already-decoded frame immediately so the user sees
          // something while the exact frame decodes in the background.
          const nearbyReady = findNearestDecodedLoopScrubHour(nextHour, loopPlaybackRenderMode);
          if (Number.isFinite(nearbyReady)) {
            setLoopDisplayHour(nearbyReady as number);
          }
          startForegroundLoopFrameDecode(nextHour, loopPlaybackRenderMode, () => {
            setLoopDisplayHour(nextHour);
          });
        }
      });
    },
    [
      isLoopDisplayActive,
      isGridPlayable,
      gridFrameHours,
      loopFrameHours,
      frameHours,
      model,
      tileUrlForHour,
      ensureLoopFrameDecoded,
      loopPlaybackRenderMode,
      hasDecodedLoopFrame,
      resolveLoopUrlForHour,
      findNearestReadyTileScrubHour,
      findNearestDecodedLoopScrubHour,
      startForegroundLoopFrameDecode,
      startPendingFrameMetric,
      shouldEagerlyDecodeLoopFrames,
    ]
  );

  useEffect(() => {
    if (!isLoopDisplayActive || !loopSelectionReady) {
      setLoopDisplayHour(null);
      return;
    }

    const pendingVarSwitch = pendingVariableSwitchRef.current;
    if (
      pendingVarSwitch
      && pendingVarSwitch.toVariableId === variable
      && !Number.isFinite(pendingVarSwitch.loopDecodeRequestedAt)
    ) {
      pendingVarSwitch.loopDecodeRequestedAt = performance.now();
    }

    const commitLoopHour = resolvedLoopTargetForecastHour;

    if (
      pendingVarSwitch
      && pendingVarSwitch.toVariableId === variable
      && !Number.isFinite(pendingVarSwitch.firstTargetRequestAt)
    ) {
      pendingVarSwitch.firstTargetRequestAt = performance.now();
    }

    if (isScrubbing) {
      if (hasDecodedLoopFrame(commitLoopHour, loopPlaybackRenderMode)) {
        loopDisplayDecodeTokenRef.current += 1;
        setLoopDisplayHour(commitLoopHour);
      } else {
        // Pass the commit callback so that when the foreground decode finishes
        // the displayed hour advances. Without this, the effect's call to
        // startForegroundLoopFrameDecode (which aborts any prior RAF-initiated
        // decode) would leave loopDisplayHour stuck at the previous value.
        const hourToCommit = commitLoopHour;
        startForegroundLoopFrameDecode(commitLoopHour, loopPlaybackRenderMode, () => {
          setLoopDisplayHour(hourToCommit);
        });
      }
      return;
    }

    if (hasDecodedLoopFrame(commitLoopHour, loopPlaybackRenderMode)) {
      loopDisplayDecodeTokenRef.current += 1;
      setLoopDisplayHour(commitLoopHour);
      return;
    }

    loopDisplayDecodeTokenRef.current += 1;
    const decodeToken = loopDisplayDecodeTokenRef.current;

    // No signal: the decode always completes and its result is stored in the LRU
    // cache. The token guards the commit; scrubbing to a new frame only invalidates
    // the commit, not the inflight fetch — keeping every touched frame warm.
    ensureLoopFrameDecoded(commitLoopHour, loopPlaybackRenderMode)
      .then((ready) => {
        if (!ready) {
          return;
        }
        if (decodeToken !== loopDisplayDecodeTokenRef.current) {
          return;
        }
        setLoopDisplayHour(commitLoopHour);
      })
      .catch(() => {
        // keep previous display hour when decode fails.
      });
  }, [
    isLoopDisplayActive,
    loopSelectionReady,
    targetForecastHour,
    resolvedLoopTargetForecastHour,
    loopPlaybackRenderMode,
    ensureLoopFrameDecoded,
    variable,
    shouldEagerlyDecodeLoopFrames,
    isPlaying,
    isLoopPreloading,
    isLoopAutoplayBuffering,
    isScrubbing,
    resolveLoopUrlForHour,
    startForegroundLoopFrameDecode,
    resetLoopPresentationToTiles,
  ]);

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
        setLoopManifest(null);
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
        const [runDataRaw, requestedManifest] = await Promise.all([
          runDataPromise,
          fetchManifest(model, run, { signal: controller.signal }).catch(() => null),
        ]);
        if (controller.signal.aborted || generation !== requestGenerationRef.current) {
          return;
        }

        const runData = sortRunIdsDescending(runDataRaw);
        const nextRun = run !== "latest" && runData.includes(run) ? run : "latest";
        let manifestData = requestedManifest;
        if (!manifestData && nextRun !== run) {
          manifestData = await fetchManifest(model, nextRun, { signal: controller.signal }).catch(() => null);
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
        const pendingVarSwitch = pendingVariableSwitchRef.current;
        if (pendingVarSwitch && !Number.isFinite(pendingVarSwitch.manifestResolvedAt)) {
          pendingVarSwitch.manifestResolvedAt = performance.now();
        }
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
  }, [model, run, runs, selectedCapabilityVars, selectedModelCapability]);

  useEffect(() => {
    setFrameRows([]);
    setLoopManifest(null);
    setForecastHour(Number.POSITIVE_INFINITY);
    setTargetForecastHour(Number.POSITIVE_INFINITY);
    setLoopDisplayHour(null);
    setLoadedFramesKey("");
    setVariableSwitchState(null);
    setVisualVariable(variable);
  }, [model, run]);

  useEffect(() => {
    setFrameRows([]);
    setLoopManifest(null);
    setVisibleRenderMode("tiles");
    setLoopDisplayHour(null);
    setLoopDisplayBitmap(null);
    setLoadedFramesKey("");
    setSettledTileUrl(null);
    setMapLoadingTileUrl(null);
    failedRumCountRef.current = 0;
  }, [selectionKey]);

  useEffect(() => {
    if (!model || !variable || !hasRenderableSelection) {
      setLoopManifest(null);
      return;
    }
    const controller = new AbortController();
    const generation = requestGenerationRef.current;

    async function loadLoopManifest() {
      const startedAt = performance.now();
      const manifest = await fetchLoopManifest(model, resolvedRunForRequests, variable, { signal: controller.signal });
      if (controller.signal.aborted || generation !== requestGenerationRef.current) {
        return;
      }
      const durationMs = performance.now() - startedAt;
      if (Number.isFinite(durationMs) && durationMs >= 0) {
        trackPerfEvent({
          event_name: "loop_manifest_resolve",
          duration_ms: durationMs,
          model_id: model || null,
          variable_id: variable || null,
          run_id: telemetryRunId,
          region_id: region || null,
          meta: {
            resolved: Boolean(manifest),
          },
        });
        trackRumDiagnosticMetric({
          metric_name: "manifest_fetch_duration",
          metric_value: durationMs,
          metric_unit: "ms",
          model_id: model || null,
          variable_id: variable || null,
          run_id: telemetryRunId,
          region_id: region || null,
        });
      }
      setLoopManifest(manifest);
    }

    loadLoopManifest().catch(() => {
      if (controller.signal.aborted || generation !== requestGenerationRef.current) {
        return;
      }
      setLoopManifest(null);
    });

    return () => {
      controller.abort();
    };
  }, [
    model,
    variable,
    resolvedRunForRequests,
    hasRenderableSelection,
    telemetryRunId,
    region,
  ]);

  useEffect(() => {
    if (!model || !variable || !hasRenderableSelection) return;
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
          const pendingVarSwitch = pendingVariableSwitchRef.current;
          if (pendingVarSwitch && !Number.isFinite(pendingVarSwitch.framesResolvedAt)) {
            pendingVarSwitch.framesResolvedAt = performance.now();
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
        const framesRunKey = run === "latest" ? "latest" : resolvedRunForRequests;
        const rows = await fetchFrames(model, framesRunKey, variable, { signal: controller.signal });
        if (controller.signal.aborted || generation !== requestGenerationRef.current) return;
        const pendingVarSwitch = pendingVariableSwitchRef.current;
        if (pendingVarSwitch && !Number.isFinite(pendingVarSwitch.framesResolvedAt)) {
          pendingVarSwitch.framesResolvedAt = performance.now();
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
        setFrameRows(rows);
        setLoadedFramesKey(`${model}:${resolvedRunForRequests}:${variable}`);
        const frames = rows.map((row) => Number(row.fh)).filter(Number.isFinite);
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
    loadedFramesKey,
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
      const inFlightAgeMs = Math.max(0, performance.now() - anchorBatchInFlightStartedAtRef.current);
      if (context.deferToLatest && inFlightAgeMs >= ANCHOR_BATCH_SUPERSEDE_MS) {
        resetAnchorBatchQueue(true);
        anchorBatchContextRef.current = context;
        anchorBatchPendingHourRef.current = null;
        startAnchorBatchRequest(visibleOverlayHour, context);
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
            const manifestData = await fetchManifest(model, run, { signal: tickController.signal });
            if (cancelled || tickController?.signal.aborted) {
              return;
            }
            setRunManifest(manifestData);
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

          const rows = await fetchFrames(model, run, variable, { signal: tickController.signal });
          if (cancelled || tickController?.signal.aborted) {
            return;
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
  }, [model, run, variable, resolvedRunForRequests, runManifest, isPageVisible, selectedCapabilityVars, selectedModelCapability, selectedVariableDefaultFh, selectedModelDefaultFrameSelection, hasRenderableSelection, loadedFramesKey, selectionKey, selectedModelLatestOnly]);

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
    if (!isPlaying || renderMode !== "tiles" || frameHours.length === 0 || isGridPlayable) return;

    const interval = window.setInterval(() => {
      const currentIndex = frameHours.indexOf(forecastHour);
      if (currentIndex < 0) return;

      const remainingAheadFrames = Math.max(0, frameHours.length - currentIndex - 1);
      const minAheadRequired = Math.min(playbackPolicy.minAheadWhilePlaying, remainingAheadFrames);
      if (bufferSnapshot.bufferedAheadCount < minAheadRequired) {
        setIsPlaying(false);
        showTransientFrameStatus("Buffering frames");
        autoplayPrimedRef.current = false;
        return;
      }

      const nextIndex = currentIndex + 1;
      if (nextIndex >= frameHours.length) {
        setIsPlaying(false);
        return;
      }

      if (!autoplayPrimedRef.current) {
        let primed = true;
        const readyAheadEnd = Math.min(frameHours.length - 1, currentIndex + AUTOPLAY_READY_AHEAD);
        for (let idx = currentIndex + 1; idx <= readyAheadEnd; idx += 1) {
          const aheadHour = frameHours[idx];
          if (!isTileReady(tileUrlForHour(aheadHour))) {
            primed = false;
            break;
          }
        }
        if (!primed) {
          return;
        }
        autoplayPrimedRef.current = true;
      }

      let chosenHour: number | null = null;
      let chosenStep = 0;
      const maxStep = Math.min(AUTOPLAY_SKIP_WINDOW, frameHours.length - 1 - currentIndex);
      for (let step = 1; step <= maxStep; step += 1) {
        const candidateHour = frameHours[currentIndex + step];
        const candidateUrl = tileUrlForHour(candidateHour);
        if (isTileReady(candidateUrl)) {
          chosenHour = candidateHour;
          chosenStep = step;
          break;
        }
      }

      if (chosenHour !== null) {
        if (chosenStep > 1) {
          const skippedHour = frameHours[nextIndex];
          const skippedLabel = selectedTimeAxisMode === "observed"
            ? (formatObservedCompactTime(frameValidTimesByHour[skippedHour]) ?? "observed frame")
            : `FH ${skippedHour}`;
          showTransientFrameStatus(`Frame unavailable (${skippedLabel})`);
        }
        setTargetForecastHour(chosenHour);
        return;
      }

      autoplayPrimedRef.current = false;
    }, AUTOPLAY_TICK_MS);

    return () => window.clearInterval(interval);
  }, [
    isPlaying,
    frameHours,
    forecastHour,
    isTileReady,
    tileUrlForHour,
    showTransientFrameStatus,
    selectedTimeAxisMode,
    frameValidTimesByHour,
    bufferSnapshot.bufferedAheadCount,
    playbackPolicy.minAheadWhilePlaying,
    renderMode,
    isGridPlayable,
  ]);

  useEffect(() => {
    if (!isPlaying || !isGridPlayable || gridFrameHours.length === 0) {
      gridPlaybackHourRef.current = null;
      return;
    }

    let rafId: number | null = null;
    let previousTs = performance.now();
    let accumulatedMs = 0;

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
        if (!isGridFrameReady(nextUrl)) {
          break;
        }
        accumulatedMs -= AUTOPLAY_TICK_MS;
        gridPlaybackHourRef.current = nextHour;
        setTargetForecastHour(nextHour);
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
    if (!isPreloadingForPlay) {
      return;
    }
    if (frameHours.length === 0) {
      setIsPreloadingForPlay(false);
      return;
    }

    const bufferedCount = Math.max(0, Math.min(bufferSnapshot.bufferedCount, frameHours.length));
    const progress = preloadProgressRef.current;
    const now = Date.now();

    if (progress.lastProgressAt <= 0) {
      progress.lastProgressAt = now;
    }
    if (bufferedCount > progress.lastBufferedCount) {
      progress.lastBufferedCount = bufferedCount;
      progress.lastProgressAt = now;
    }

    const remainingAheadFrames = Math.max(0, frameHours.length - forecastHour - 1);
    const minAheadReady = Math.min(playbackPolicy.minAheadWhilePlaying, remainingAheadFrames);
    const canStartByAheadReady = bufferSnapshot.bufferedAheadCount >= minAheadReady;
    const preloadStartThreshold = Math.min(
      frameHours.length,
      Math.max(playbackPolicy.minStartBuffer, Math.ceil(frameHours.length * PRELOAD_START_RATIO))
    );
    const stalledMs = now - progress.lastProgressAt;
    const canStartByThreshold = bufferedCount >= preloadStartThreshold && canStartByAheadReady;
    const canStartByStall =
      bufferedCount >= playbackPolicy.minStartBuffer &&
      canStartByAheadReady &&
      stalledMs >= PRELOAD_STALL_MS;

    if (!canStartByThreshold && !canStartByStall) {
      return;
    }

    setIsPreloadingForPlay(false);
    autoplayPrimedRef.current = false;
    if (canStartByStall && !canStartByThreshold) {
      showTransientFrameStatus("Starting with partial buffer");
    }
    setIsPlaying(true);
  }, [
    isPreloadingForPlay,
    bufferSnapshot.bufferedCount,
    bufferSnapshot.bufferedAheadCount,
    frameHours.length,
    forecastHour,
    playbackPolicy.minAheadWhilePlaying,
    playbackPolicy.minStartBuffer,
    showTransientFrameStatus,
  ]);

  useEffect(() => {
    if (frameHours.length === 0 && isPlaying) {
      setIsPlaying(false);
    }
  }, [frameHours, isPlaying]);

  useEffect(() => {
    if (!isPlaying) {
      autoplayPrimedRef.current = false;
      clearFrameStatusTimer();
    }
  }, [isPlaying, clearFrameStatusTimer]);

  const handleSetIsPlaying = useCallback((value: boolean) => {
    if (!value) {
      pendingLoopStartMetricRef.current = null;
      gridPlaybackHourRef.current = null;
      setIsPlaying(false);
      setIsLoopAutoplayBuffering(false);
      setIsLoopPreloading(false);
      setIsPreloadingForPlay(false);
      setIsGridPreloadingForPlay(false);
      return;
    }
    if (loading || frameHours.length === 0) {
      pendingLoopStartMetricRef.current = null;
      return;
    }

    if (renderMode === "tiles") {
      if (canUseGridPlayback && isHighDetailZoom) {
        pendingLoopStartMetricRef.current = null;
        setIsPlaying(false);
        setIsLoopAutoplayBuffering(false);
        setIsLoopPreloading(false);
        setIsPreloadingForPlay(false);
        setIsGridPreloadingForPlay(false);
        showTransientFrameStatus("High detail mode — zoom out for animation playback");
        return;
      }
      if (canUseLoopPlayback && isHighDetailZoom) {
        pendingLoopStartMetricRef.current = null;
        setIsPlaying(false);
        setIsLoopAutoplayBuffering(false);
        setIsLoopPreloading(false);
        setIsPreloadingForPlay(false);
        setIsGridPreloadingForPlay(false);
        showTransientFrameStatus("High detail mode — zoom out for animation playback");
        return;
      }
      if (!canUseLoopPlayback && !canUseGridPlayback) {
        pendingLoopStartMetricRef.current = null;
        setIsPlaying(false);
        setIsLoopAutoplayBuffering(false);
        setIsLoopPreloading(false);
        setIsPreloadingForPlay(false);
        setIsGridPreloadingForPlay(false);
        showTransientFrameStatus("Loop unavailable for this variable/run — showing tiles");
        return;
      }
    }

    startPendingLoopStartMetric();
    trackUsageEvent({
      event_name: "animation_play",
      model_id: model || null,
      variable_id: variable || null,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
      meta: buildObservedTelemetryMeta(forecastHour),
    });
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
      setIsLoopAutoplayBuffering(false);
      setIsLoopPreloading(false);
      setIsPreloadingForPlay(false);
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

    if (!canUseLoopPlayback || !webpDefaultEnabled) {
      pendingLoopStartMetricRef.current = null;
      setIsPlaying(false);
      setIsLoopAutoplayBuffering(false);
      setIsLoopPreloading(false);
      setIsPreloadingForPlay(false);
      setIsGridPreloadingForPlay(false);
      showTransientFrameStatus("Animation unavailable for this selection");
      return;
    }

    setIsPlaying(false);
    setIsPreloadingForPlay(false);
    setIsGridPreloadingForPlay(false);
    setIsLoopPreloading(true);
    showTransientFrameStatus("Loading loop frames");
  }, [
    loading,
    frameHours.length,
    canUseGridPlayback,
    gridPlaybackStartHour,
    canUseLoopPlayback,
    isHighDetailZoom,
    isGridPlaybackStartReady,
    webpDefaultEnabled,
    renderMode,
    showTransientFrameStatus,
    startPendingLoopStartMetric,
    model,
    variable,
    telemetryRunId,
    region,
    buildObservedTelemetryMeta,
    targetForecastHour,
    forecastHour,
  ]);

  useEffect(() => {
    if (isPlaying && renderMode === "tiles" && !canUseGridPlayback) {
      setIsPlaying(false);
      setIsLoopAutoplayBuffering(false);
      setIsGridPreloadingForPlay(false);
      showTransientFrameStatus("High detail mode — zoom out for animation playback");
    }
  }, [canUseGridPlayback, isPlaying, renderMode, showTransientFrameStatus]);

  useEffect(() => {
    const pendingLoop = pendingInitialLoopRef.current;
    if (typeof pendingLoop === "undefined") {
      return;
    }

    if (!pendingLoop) {
      handleSetIsPlaying(false);
      pendingInitialLoopRef.current = undefined;
      return;
    }

    if (!bootstrapHydrated || loading || selectableFrameHours.length === 0) {
      return;
    }

    handleSetIsPlaying(true);
    pendingInitialLoopRef.current = undefined;
  }, [bootstrapHydrated, loading, selectableFrameHours.length, handleSetIsPlaying]);

  const handleZoomRoutingSignal = useCallback((payload: { zoom: number; gestureActive: boolean }) => {
    setMapZoom(payload.zoom);
    setZoomGestureActive(payload.gestureActive);
  }, []);

  // Receives the imperative draw handle from MapCanvas. Stored in a ref so the
  // RAF playback ticker can call it without going through React props/state.
  const handleDrawLoopFrameRef = useCallback(
    (draw: ((bitmap: ImageBitmap) => boolean) | null) => {
      drawLoopFrameImperativeRef.current = draw;
    },
    []
  );

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

  const handleTileViewportReady = useCallback((
    readyTileUrl: string,
    meta?: { selectionEpoch?: number; selectionKey?: string }
  ) => {
    if (
      (meta?.selectionEpoch !== undefined && meta.selectionEpoch !== selectionEpochRef.current)
      || (meta?.selectionKey !== undefined && meta.selectionKey !== selectionKey)
    ) {
      return;
    }
    if (renderMode !== "tiles") {
      // Loop/canvas playback can emit synthetic tile-ready events to warm caches.
      // Never treat those as visible tile presentation commits.
      return;
    }
    if (readyTileUrl === tileUrl) {
      trackFirstViewerFrame(forecastHour);
    }
    // Finalize variable_switch: fires once the first tile for the new variable is viewport-ready.
    if (readyTileUrl === tileUrl) {
      finalizePendingVariableSwitch("tiles", performance.now(), { readyTileUrl });
    }
    const pending = pendingFrameMetricRef.current;
    if (pending?.renderTarget === "tiles" && pending.expectedTileUrl === readyTileUrl) {
      if (!Number.isFinite(pending.firstVisibleAt)) {
        pending.firstVisibleAt = performance.now();
      }
      finalizePendingFrameMetric("tile");
    }
    if (readyTileUrl !== tileUrl) {
      return;
    }
    if (visibleRenderMode === "tiles" && lastTileViewportCommitUrlRef.current === readyTileUrl) {
      return;
    }
    lastTileViewportCommitUrlRef.current = readyTileUrl;
    setVisibleRenderMode("tiles");
  }, [
    renderMode,
    tileUrl,
    visibleRenderMode,
    variable,
    loadedFramesKey,
    visualVariable,
    region,
    forecastHour,
    loopDisplayHour,
    finalizePendingFrameMetric,
    finalizePendingVariableSwitch,
    telemetryRunId,
    trackFirstViewerFrame,
    selectionKey,
  ]);

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
    trackFirstViewerFrame(Number.isFinite(payload.frameHour) ? payload.frameHour : forecastHour);
  }, [forecastHour, selectionKey, trackFirstViewerFrame]);
  const handleGridFrameReady = useCallback((frameUrl: string) => {
    const normalized = normalizeGridFrameUrl(frameUrl);
    if (!normalized) {
      return;
    }
    if (gridReadyFrameUrlsRef.current.has(normalized)) {
      return;
    }
    gridReadyFrameUrlsRef.current.add(normalized);
    setGridReadyVersion((current) => current + 1);
  }, [normalizeGridFrameUrl]);

  const handleRegionChange = useCallback((nextRegion: string) => {
    setRegion(nextRegion);
    trackUsageEvent({
      event_name: "region_selected",
      model_id: model || null,
      variable_id: variable || null,
      run_id: telemetryRunId,
      region_id: nextRegion,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
      meta: buildObservedTelemetryMeta(forecastHour),
    });
    captureProductAnalyticsEvent("region_selected", {
      model_id: model || null,
      variable_id: variable || null,
      run_id: telemetryRunId,
      region_id: nextRegion,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
    });
  }, [model, variable, telemetryRunId, forecastHour, buildObservedTelemetryMeta]);

  const handleModelChange = useCallback((nextModel: string) => {
    setNewRunNotice((current) => (current?.model === nextModel ? current : null));
    setRun("latest");
    setRuns([]);
    setRunManifest(null);
    setFrameRows([]);
    setLoopManifest(null);
    setModel(nextModel);
    trackUsageEvent({
      event_name: "model_selected",
      model_id: nextModel,
      variable_id: variable || null,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
      meta: buildObservedTelemetryMeta(forecastHour),
    });
    captureProductAnalyticsEvent("model_selected", {
      model_id: nextModel,
      variable_id: variable || null,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
    });
  }, [variable, telemetryRunId, region, forecastHour, buildObservedTelemetryMeta]);

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
      startedAt: performance.now(),
      fromVariableId: fromVariable || null,
      toVariableId: nextVariable,
      expectedSelectionKey: `${model}:${resolvedRunForRequests}:${nextVariable}`,
      modelId: model || null,
      runId: telemetryRunId,
      regionId: region || null,
      manifestResolvedAt: null,
      framesResolvedAt: null,
      firstTargetRequestAt: null,
      firstTargetReadyAt: null,
      firstVisibleAt: null,
      loopDecodeRequestedAt: null,
      expectedTileUrl: null,
      warmAtVisible: null,
      warmSourceAtVisible: null,
    };
    setVariableSwitchState({
      fromVariable,
      toVariable: nextVariable,
      startedAt: performance.now(),
      visualState: "holding_old",
    });
    // Snapshot the current loop visuals before tearing down loop presentation.
    // This lets the map hold the old frame during the transition instead of
    // flashing stale tile imagery while the new variable loads.
    if (isLoopDisplayActive && Number.isFinite(loopDisplayHour)) {
      const snapshotHour = loopDisplayHour as number;
      holdoverLoopBitmapRef.current = getDecodedLoopBitmap(snapshotHour, loopPlaybackRenderMode);
      holdoverLoopUrlRef.current = resolveLoopUrlForHour(snapshotHour, loopPlaybackRenderMode);
      holdoverLoopBboxRef.current = loopManifest?.bbox ?? null;
    } else {
      holdoverLoopBitmapRef.current = null;
      holdoverLoopUrlRef.current = null;
      holdoverLoopBboxRef.current = null;
    }
    resetLoopPresentationToTiles();
    setVariable(nextVariable);
    trackUsageEvent({
      event_name: "variable_selected",
      model_id: model || null,
      variable_id: nextVariable,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
      meta: buildObservedTelemetryMeta(forecastHour),
    });
    captureProductAnalyticsEvent("variable_selected", {
      model_id: model || null,
      variable_id: nextVariable,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
    });
  }, [model, variable, visualVariable, telemetryRunId, region, forecastHour, resolvedRunForRequests, targetForecastHour, renderMode, visibleRenderMode, loopDisplayHour, isLoopDisplayActive, loopPlaybackRenderMode, loopManifest, getDecodedLoopBitmap, resolveLoopUrlForHour, resetLoopPresentationToTiles, buildObservedTelemetryMeta]);

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
    if (isScrubbing) {
      setIsLoopAutoplayBuffering(false);
      setIsLoopPreloading(false);
      return;
    }
    setScrubRequestedHour(null);
  }, [isScrubbing]);

  useEffect(() => {
    return () => {
      clearFrameStatusTimer();
      mapInstanceRef.current = null;
      if (scrubRafRef.current !== null) {
        window.cancelAnimationFrame(scrubRafRef.current);
      }
      resetAnchorBatchQueue(true);
      if (bufferSnapshotRafRef.current !== null) {
        window.cancelAnimationFrame(bufferSnapshotRafRef.current);
      }
      loopDisplayDecodeAbortRef.current?.abort();
      foregroundDecodeHourRef.current = null;
      for (const cached of loopDecodedCacheRef.current.values()) {
        cached.bitmap.close();
      }
      loopDecodedCacheRef.current.clear();
      loopDecodeCompletedAtRef.current.clear();
      loopDecodedCacheBytesRef.current = 0;
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

  const controlsIsPlaying = isPlaying || isPreloadingForPlay || isGridPreloadingForPlay || isLoopPreloading;
  const substrateDebugLabel = useMemo(() => {
    if (resolvedWeatherSubstrate === "grid_webgl_v1") {
      return isHighDetailZoom ? "grid_webgl_v1 (tile fallback)" : "grid_webgl_v1";
    }
    if (prefersGridSubstrate && !gridManifest) {
      return "legacy (grid fallback)";
    }
    return "legacy";
  }, [gridManifest, isHighDetailZoom, prefersGridSubstrate, resolvedWeatherSubstrate]);
  const substrateDebugDetail = useMemo(() => {
    if (resolvedWeatherSubstrate === "grid_webgl_v1") {
      const frameLabel = Number.isFinite(resolvedGridDisplayHour) ? `FH ${resolvedGridDisplayHour}` : "no frame";
      return `${frameLabel} · z ${mapZoom.toFixed(1)}`;
    }
    return `z ${mapZoom.toFixed(1)}`;
  }, [mapZoom, resolvedGridDisplayHour, resolvedWeatherSubstrate]);
  const preloadBufferedCount = isLoopPreloading
    ? Math.max(0, Math.min(loopProgress.ready + loopProgress.failed, loopProgress.total))
    : isGridPreloadingForPlay
      ? Math.max(0, Math.min(gridReadyCount, gridFrameHours.length))
      : Math.max(0, Math.min(bufferSnapshot.terminalCount, bufferSnapshot.totalFrames));
  const preloadTotal = isLoopPreloading
    ? loopProgress.total
    : isGridPreloadingForPlay
      ? gridFrameHours.length
      : bufferSnapshot.totalFrames;
  const preloadPercent = preloadTotal > 0
    ? Math.round((preloadBufferedCount / preloadTotal) * 100)
    : 0;
  const showBufferStatus =
    isScrubLoading
    || (isGridPreloadingForPlay && gridFrameHours.length > 0)
    || (isPreloadingForPlay && bufferSnapshot.totalFrames > 0)
    || (isLoopPreloading && loopProgress.total > 0);
  const bufferStatusText = isScrubLoading
    ? "Loading frame"
    : isGridPreloadingForPlay
      ? `Buffering grid ${preloadBufferedCount}/${preloadTotal}`
      : `Loading frames ${preloadBufferedCount}/${preloadTotal}`;
  const activeLoopHour = visibleLoopOverlayHour;
  const committedLoopHour = Number.isFinite(loopDisplayHour) ? (loopDisplayHour as number) : null;
  // Loop presentation is bitmap-backed. During a variable switch, fall back to
  // the holdover bitmap from the outgoing selection until the new selection's
  // first frame is ready.  When the render mode has switched to tiles (high-zoom
  // detail), suppress all loop bitmaps so the tile layers can take over.
  const targetLoopBitmap = renderMode !== "tiles" && hasDecodedLoopFrame(activeLoopHour, loopPlaybackRenderMode)
    ? getDecodedLoopBitmap(activeLoopHour, loopPlaybackRenderMode)
    : null;
  const committedLoopBitmap = renderMode !== "tiles" && committedLoopHour !== null && hasDecodedLoopFrame(committedLoopHour, loopPlaybackRenderMode)
    ? getDecodedLoopBitmap(committedLoopHour, loopPlaybackRenderMode)
    : null;
  const newLoopBitmap = targetLoopBitmap ?? committedLoopBitmap;
  // Guard against detached holdover bitmaps — the cache-clear on dataset
  // change calls `.close()` on every cached bitmap, which invalidates any
  // holdover ref that was snapshotted before the switch.
  const holdoverBitmap = holdoverLoopBitmapRef.current;
  const safeHoldover = isVariableSwitching && holdoverBitmap && holdoverBitmap.width > 0
    ? holdoverBitmap
    : null;
  const activeLoopBitmap = newLoopBitmap
    ?? loopDisplayBitmap
    ?? safeHoldover;
  // Canvas/bitmap-only loop presentation: avoid MapLibre image-source path,
  // which is currently causing decode instability and stale loop visuals.
  const activeLoopUrl = null;
  const activeLoopBbox = loopManifest?.bbox
    ?? (isVariableSwitching ? holdoverLoopBboxRef.current : null);
  // Keep tiles fully disabled for the entire loop playback session.
  // Even if a specific decoded frame is briefly unavailable, stay in loop mode
  // rather than falling back to tile-layer swaps.  However, when the render
  // mode has explicitly switched to tiles (e.g. high-zoom detail), respect
  // that and allow the tile layers to take over even if a cached bitmap exists.
  const effectiveLoopActive =
    renderMode !== "tiles"
    && (Boolean(activeLoopBitmap)
      || (isLoopDisplayActive && Number.isFinite(loopDisplayHour)));

  useEffect(() => {
    if (!newLoopBitmap) {
      return;
    }
    if (loopDisplayBitmap === newLoopBitmap) {
      return;
    }
    setLoopDisplayBitmap(newLoopBitmap);
  }, [newLoopBitmap, loopDisplayBitmap]);

  const permalinkLoopActive = controlsIsPlaying || isLoopAutoplayBuffering;
  const resolvedLoopPermalink = typeof pendingInitialLoopRef.current === "boolean"
    ? pendingInitialLoopRef.current
    : permalinkLoopActive;
  const resolvedForecastHourPermalink = Number.isFinite(forecastHour)
    ? forecastHour
    : pendingInitialForecastHourRef.current;
  const selectedModelLabel = useMemo(() => {
    const fromOptions = models.find((entry) => entry.value === model)?.label;
    return fromOptions ?? model;
  }, [models, model]);
  const selectedRunLabel = useMemo(() => {
    const fromOptions = runOptions.find((entry) => entry.value === run)?.label;
    if (fromOptions) {
      return fromOptions;
    }
    if (run === "latest") {
      return latestRunId ? `Latest (${formatRunLabel(latestRunId, selectedTimeAxisMode)})` : "Latest";
    }
    return formatRunLabel(run, selectedTimeAxisMode);
  }, [runOptions, run, latestRunId, selectedTimeAxisMode]);
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

    const loopCoordinates = loopManifest?.bbox
      ? ([
          [loopManifest.bbox[0], loopManifest.bbox[3]],
          [loopManifest.bbox[2], loopManifest.bbox[3]],
          [loopManifest.bbox[2], loopManifest.bbox[1]],
          [loopManifest.bbox[0], loopManifest.bbox[1]],
        ] as [[number, number], [number, number], [number, number], [number, number]])
      : undefined;
    const style = buildMapStyle(
      tileUrl,
      opacity,
      variable,
      displayedOverlayVariableKind,
      displayedOverlayVariableDisplayResamplingOverride,
      overlayFadeOutZoom,
      contourGeoJsonUrl,
      loopCoordinates,
      basemapMode,
      { includeRuntimeLoopCanvas: false }
    );

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
      loopEnabled: isLoopDisplayActive,
      capturedMapDataUrl,
      anchors,
    };
  }, [
    selectedModelLabel,
    model,
    selectedRunLabel,
    run,
    tileUrl,
    opacity,
    variable,
      displayedOverlayVariableKind,
      displayedOverlayVariableDisplayResamplingOverride,
    overlayFadeOutZoom,
    contourGeoJsonUrl,
    loopManifest,
    basemapMode,
    anchorDisplayGeoJson,
    selectedVariableLabel,
    forecastHour,
    selectedTimeAxisMode,
    currentFrameValidTimeISO,
    observedSourceStatus,
    region,
    selectedRegionLabel,
    isLoopDisplayActive,
  ]);

  const handleOpenShareModal = useCallback(() => {
    const permalink = typeof window !== "undefined" ? window.location.href : "";
    const runForSummary = run === "latest" ? (latestRunId ?? "latest") : run;
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
          loopEnabled: resolvedLoopPermalink,
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
    latestRunId,
    model,
    region,
    regionPresets,
    resolvedLoopPermalink,
    run,
    runManifest,
    selectedCapabilityVarMap,
    selectedModelLabel,
    selectedRunLabel,
    selectedTimeAxisMode,
    selectedVariableLabel,
    variable,
    currentFrameValidTimeISO,
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
          loop: resolvedLoopPermalink,
          weatherSubstrate: weatherSubstrateOverride ?? undefined,
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
    resolvedLoopPermalink,
    weatherSubstrateOverride,
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
          tileUrl={tileUrl}
          selectionKey={selectionKey}
          selectionEpoch={selectionEpoch}
          gridManifest={isGridLowMidActive ? gridManifest : null}
          gridFrameUrl={isGridLowMidActive ? activeGridFrameUrl : null}
          gridFrameHour={isGridLowMidActive && Number.isFinite(resolvedGridDisplayHour) ? Number(resolvedGridDisplayHour) : null}
          gridLegend={isGridLowMidActive ? legend : null}
          gridActive={isGridLowMidActive}
          contourGeoJsonUrl={contourGeoJsonUrl}
          anchorGeoJson={anchorDisplayGeoJson}
          pointLabelsEnabled={pointLabelsEnabled}
          region={region}
          regionViews={regionViews}
          opacity={opacity}
          mode={(isPlaying || isGridPreloadingForPlay) ? "autoplay" : (isVariableSwitching ? "variable-switch" : "scrub")}
          variable={displayedOverlayVariable}
          variableKind={displayedOverlayVariableKind}
          displayResamplingOverride={displayedOverlayVariableDisplayResamplingOverride}
          overlayFadeOutZoom={overlayFadeOutZoom}
          basemapMode={basemapMode}
          prefetchTileUrls={isLoopDisplayActive || isGridLowMidActive ? [] : prefetchTileUrls}
          crossfade={isVariableSwitching}
          loopImageUrl={activeLoopUrl}
          loopFrameBitmap={activeLoopBitmap}
          loopImageBbox={activeLoopBbox}
          loopActive={effectiveLoopActive}
          onFrameSettled={handleFrameSettled}
          onTileReady={handleTileReady}
          onFrameLoadingChange={handleFrameLoadingChange}
          onTileViewportReady={handleTileViewportReady}
          onGridFrameVisible={handleGridFrameVisible}
          onGridFrameReady={handleGridFrameReady}
          onZoomBucketChange={setZoomBucket}
          onZoomRoutingSignal={handleZoomRoutingSignal}
          onViewportChange={handleViewportChange}
          onMapReady={handleMapReady}
          onMapHover={onHover}
          onMapHoverEnd={onHoverEnd}
          onDrawLoopFrameRef={handleDrawLoopFrameRef}
          loopImperativePlaybackActive={isPlaying && renderMode !== "tiles"}
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

        <div className="pointer-events-none absolute right-3 top-3 z-20 rounded-full border border-white/12 bg-black/45 px-3 py-1.5 text-[11px] font-medium text-white/88 shadow-[0_10px_24px_rgba(0,0,0,0.28)] backdrop-blur-sm">
          <span className="uppercase tracking-[0.16em] text-white/58">Substrate</span>
          <span className="ml-2">{substrateDebugLabel}</span>
          <span className="ml-2 text-white/56">{substrateDebugDetail}</span>
        </div>

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

        {tooltip && (
          <div
            className="pointer-events-none absolute z-50 rounded-xl glass px-2.5 py-1.5 text-xs font-medium shadow-xl"
            style={{
              left: tooltip.x + 14,
              top: tooltip.y - 32,
            }}
          >
            {tooltip.value.toFixed(1)} {tooltip.units}
          </div>
        )}

        {error && (
          <div className="absolute left-4 top-4 z-40 flex items-center gap-2 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive shadow-lg backdrop-blur-md">
            <AlertCircle className="h-3.5 w-3.5" />
            {error}
          </div>
        )}

        {renderMode === "tiles" && canUseLoopPlayback && isHighDetailZoom && (
          <div className="glass fixed bottom-[6.5rem] left-1/2 z-40 flex -translate-x-1/2 items-center gap-2 rounded-xl px-3 py-2 text-xs">
            <AlertCircle className="h-3.5 w-3.5" />
            High detail mode — zoom out for animation playback
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
    </div>
  );
}
