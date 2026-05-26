import { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { AlertCircle } from "lucide-react";

import { BottomForecastControls } from "@/components/bottom-forecast-controls";
import { MapCanvas, buildMapStyle, type BasemapMode, type VectorHazardSelection } from "@/components/map-canvas";
import type { LegendPayload } from "@/components/map-legend";
import type { SharePayload } from "@/components/twf-share-modal";
import SiteHeader from "@/components/SiteHeader";
import type { GridContourLayerConfig } from "@/lib/grid-webgl";
import { ViewerToolbarContext } from "@/lib/viewer-toolbar-context";
import {
  fetchAnchorFeatureCollection,
  type CapabilitiesResponse,
  type CapabilityModel,
  type FrameRow,
  type GridManifestResponse,
  type RegionPreset,
  type RunManifestResponse,
  fetchManifest,
  fetchCapabilities,
  fetchFrames,
  buildContourUrl,
  fetchGridManifest,
  fetchRegionPresets,
  fetchRuns,
  fetchSampleBatch,
  readCapabilityDefaultFrameSelection,
  readCapabilityLatestOnly,
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
} from "@/lib/config";
import { useFeedbackContext } from "@/lib/feedback-context";
import { buildRunOptions, formatRunLabel, latestRunLabel, pickLatestRunId, sortRunIdsDescending } from "@/lib/run-options";
import { type ScreenshotExportState } from "@/lib/screenshot_export";
import {
  deriveObservedSourceStatus,
  frameIssueTime,
  frameValidTime,
  formatIssuedTimeISO,
  observedSourceStatusFromAvailability,
  parseRunId,
  runIdToIso,
} from "@/lib/time-axis";
import { readPermalink } from "@/lib/permalink-read";
import { captureProductAnalyticsEvent } from "@/lib/posthog";
import { trackRumDiagnosticMetric } from "@/lib/rum";
import { selectGridManifestLod } from "@/lib/grid-lod";
import { useSiteLoading } from "@/lib/site-loading";
import { useDisplaySettings } from "@/lib/use-display-settings";
import { useFrameStatusBadge } from "@/lib/use-frame-status-badge";
import { usePageVisibility } from "@/lib/use-page-visibility";
import { usePermalinkSync } from "@/lib/use-permalink-sync";
import { useSampleTooltip } from "@/lib/use-sample-tooltip";

import { useViewerLayoutMode } from "@/lib/viewer-layout";
import {
  // Constants
  AUTOPLAY_TICK_MS,
  AUTOPLAY_READY_AHEAD,
  AUTOPLAY_SKIP_WINDOW,
  AUTOPLAY_STALL_SKIP_MS,
  GRID_PLAY_START_AHEAD_FRAMES,
  GRID_PLAY_STALL_MS,
  VARIABLE_SWITCH_TIMEOUT_MS,
  // Pure helpers
  viewportSignatureFromState,
  areStringArraysEqual,
  withUpdatedLatestRun,
  pickPreferred,
  makeRegionLabel,
  filterRegionOptionsByCoverage,
  filterRegionOptionsForVariable,
  buildFallbackSharePayload,
  toNumberOrNull,
  makeModelOptions,
  normalizeModelRows,
  normalizeCapabilityVarRows,
  capabilityVarsForManifest,
  makeVariableOptions,
  resolveManifestFrames,
  mergeManifestRowsWithPrevious,
  extractLegendMeta,
  nearestFrame,
  mostRecentFrameHourByValidTime,
  selectableFramesForVariable,
  resolveForecastHour,
  resolveForecastHourFromRows,
  buildLegend,
  buildVectorLayerUrl,
  emptyScrubPhase0aSnapshot,
  // Types
  type NewRunNoticeState,
  type GroupedOption,
  type Option,
  type VariableOption,
  type VariableEntry,
  type PendingLoopStartMetric,
  type PendingVariableSwitchMetric,
  type VariableSwitchState,
  type ScrubCommitIntent,
  type ScrubPhase0aSnapshot,
  type ForecastHourChangeReason,
  type AnchorBatchRequestContext,
} from "@/lib/app-utils";

const TwfShareModal = lazy(() =>
  import("@/components/twf-share-modal").then((module) => ({ default: module.TwfShareModal }))
);
const NwsCityModal = lazy(() =>
  import("@/components/nws-city-modal").then((module) => ({ default: module.NwsCityModal }))
);
const NwsHazardModal = lazy(() =>
  import("@/components/nws-hazard-modal").then((module) => ({ default: module.NwsHazardModal }))
);

const NWS_HAZARDS_CONUS_VIEW_BBOX = [-126.0, 24.0, -66.0, 50.0] as [number, number, number, number];
const HIGH_RES_GRID_LOD_PIXEL_THRESHOLD = 6_000_000;
const VERY_HIGH_RES_GRID_LOD_PIXEL_THRESHOLD = 10_000_000;
const HIGH_RES_GRID_PLAY_START_AHEAD_FRAMES = 4;
const VERY_HIGH_RES_GRID_PLAY_START_AHEAD_FRAMES = 3;
const HIGH_RES_AUTOPLAY_READY_AHEAD = 2;
const VERY_HIGH_RES_AUTOPLAY_READY_AHEAD = 2;
const HIGH_RES_AUTOPLAY_LOOKAHEAD_GRACE_MS = 250;
const VERY_HIGH_RES_AUTOPLAY_LOOKAHEAD_GRACE_MS = 250;
const HIGH_RES_AUTOPLAY_STALL_SKIP_MS = 300;
const VERY_HIGH_RES_AUTOPLAY_STALL_SKIP_MS = 200;
const HIGH_RES_GRID_PLAY_STALL_MS = 2400;
const VERY_HIGH_RES_GRID_PLAY_STALL_MS = 2400;
const HIGH_RES_SCRUB_LOD_HOLD_MS = 700;
const RUN_AVAILABILITY_BADGE_EXCLUDED_MODELS = new Set(["nws_hazards", "spc", "cpc"]);
const DEFAULT_VIEWER_MODEL_ID = "mrms";
const DEFAULT_VIEWER_VARIABLE_ID = "reflectivity";

function inferLatestRunTargetMaxForecastHour(modelId: string, runId: string | null | undefined): number | null {
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

function nearestSortedNumber(values: number[], target: number): number | null {
  if (values.length === 0 || !Number.isFinite(target)) {
    return null;
  }

  let low = 0;
  let high = values.length - 1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const current = values[mid];
    if (current === target) {
      return current;
    }
    if (current < target) {
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }

  const right = values[low] ?? null;
  const left = values[high] ?? null;
  if (!Number.isFinite(left)) {
    return Number.isFinite(right) ? Number(right) : null;
  }
  if (!Number.isFinite(right)) {
    return Number(left);
  }
  return Math.abs(Number(left) - target) <= Math.abs(Number(right) - target)
    ? Number(left)
    : Number(right);
}

function defaultEnsembleViewForVariable(
  modelCapability: CapabilityModel | null | undefined,
  varKey: string | null | undefined,
): string {
  const variableEntry = varKey ? modelCapability?.variables?.[varKey] : null;
  const variableEnsemble = (variableEntry?.ensemble ?? {}) as Record<string, unknown>;
  const modelEnsemble = (modelCapability?.ensemble ?? {}) as Record<string, unknown>;
  const variableDefault = String(variableEnsemble.default_view ?? "").trim().toLowerCase();
  if (variableDefault) {
    return variableDefault;
  }
  return String(
    modelCapability?.defaults?.default_ensemble_view
    ?? modelEnsemble.default_view
    ?? ""
  ).trim().toLowerCase();
}

function pickDefaultVariableForModel(
  modelId: string,
  modelCapability: CapabilityModel | null | undefined,
  variableIds: string[],
): string {
  if (modelId === DEFAULT_VIEWER_MODEL_ID && variableIds.includes(DEFAULT_VIEWER_VARIABLE_ID)) {
    return DEFAULT_VIEWER_VARIABLE_ID;
  }
  const defaultVarKey = String(modelCapability?.defaults?.default_var_key ?? "").trim();
  if (defaultVarKey && variableIds.includes(defaultVarKey)) {
    return defaultVarKey;
  }
  return variableIds[0] ?? "";
}

export default function App() {
  const { start: startSiteLoading } = useSiteLoading();
  const { setViewerContext, clearViewerContext } = useFeedbackContext();
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
  const [models, setModels] = useState<GroupedOption[]>([]);
  const [regions, setRegions] = useState<Option[]>([]);
  const [runs, setRuns] = useState<string[]>([]);
  const [variables, setVariables] = useState<VariableOption[]>([]);
  const [frameRows, setFrameRows] = useState<FrameRow[]>([]);
  const [runManifest, setRunManifest] = useState<RunManifestResponse | null>(null);
  const [gridManifest, setGridManifest] = useState<GridManifestResponse | null>(null);
  const [compositeGridManifests, setCompositeGridManifests] = useState<Record<string, GridManifestResponse | null>>({});
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
  const [ensembleView, setEnsembleView] = useState(initialPermalink.ensembleView?.trim() ?? "");
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
  const [isScrubLodHoldActive, setIsScrubLodHoldActive] = useState(false);
  const [scrubRequestedHour, setScrubRequestedHour] = useState<number | null>(null);
  const [scrubCommitIntent, setScrubCommitIntent] = useState<ScrubCommitIntent | null>(null);

  useEffect(() => {
    return () => clearViewerContext();
  }, [clearViewerContext]);

  useEffect(() => {
    setViewerContext({
      modelContext: model || null,
      fhrContext: Number.isFinite(forecastHour) ? Number(forecastHour) : null,
    });
  }, [forecastHour, model, setViewerContext]);

  const {
    basemapMode, setBasemapMode,
    pointLabelsEnabled, setPointLabelsEnabled,
    zoomControlsVisible, setZoomControlsVisible,
    legendVisible, setLegendVisible,
    displayPanelOpen, setDisplayPanelOpen,
    opacity, setOpacity,
  } = useDisplaySettings(viewerLayoutMode, isDesktopViewerLayout);
  const [legendPopoverOpen, setLegendPopoverOpen] = useState(false);
  const [mobileControlsOpen, setMobileControlsOpen] = useState(false);
  const isPageVisible = usePageVisibility();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isShareModalOpen, setIsShareModalOpen] = useState(false);
  const [selectedAnchorCity, setSelectedAnchorCity] = useState<{
    id: string;
    city: string;
    state: string;
    st: string;
  } | null>(null);
  const [selectedVectorHazard, setSelectedVectorHazard] = useState<VectorHazardSelection | null>(null);
  const isCurrentAnalysisSelection = String(model ?? "").trim().toLowerCase() === "current_analysis";
  const [sharePayload, setSharePayload] = useState<SharePayload>({
    permalink: "",
    summary: "CartoSky viewer share",
    detailsSummary: "",
  });
  const { frameStatusMessage, showTransientFrameStatus, clearFrameStatusTimer } = useFrameStatusBadge();
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
  const [firstWeatherFramePainted, setFirstWeatherFramePainted] = useState(false);
  const selectionEpochRef = useRef(selectionEpoch);
  const [loadedFramesKey, setLoadedFramesKey] = useState("");
  const datasetGenerationRef = useRef(0);
  const requestGenerationRef = useRef(0);
  const scrubRafRef = useRef<number | null>(null);
  const scrubLodHoldTimerRef = useRef<number | null>(null);
  const previousIsScrubbingRef = useRef(false);
  const pendingScrubHourRef = useRef<number | null>(null);
  const scrubPhase0aRef = useRef<ScrubPhase0aSnapshot>(emptyScrubPhase0aSnapshot());
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
  const viewerSessionEndedTrackedRef = useRef(false);
  const pendingFirstViewerFrameRef = useRef(false);
  const pendingFirstViewerFrameHourRef = useRef<number | null>(null);
  const pendingLoopStartMetricRef = useRef<PendingLoopStartMetric | null>(null);
  const pendingVariableSwitchRef = useRef<PendingVariableSwitchMetric | null>(null);
  const modelRef = useRef(model);
  const variableRef = useRef(variable);
  const regionRef = useRef(region);
  const telemetryRunIdRef = useRef<string | null>(null);
  const targetForecastHourRef = useRef(targetForecastHour);
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
  const allVariableCatalog = useMemo(() => {
    const byId = new Map<string, VariableEntry>();
    for (const capability of Object.values(modelCatalog)) {
      for (const entry of normalizeCapabilityVarRows(capability)) {
        if (!byId.has(entry.id)) {
          byId.set(entry.id, entry);
        }
      }
    }
    for (const entry of selectedCapabilityVars) {
      byId.set(entry.id, entry);
    }
    for (const option of variables) {
      if (!byId.has(option.value)) {
        byId.set(option.value, {
          id: option.value,
          displayName: option.label,
          group: option.group,
        });
      }
    }
    return makeVariableOptions(Array.from(byId.values()));
  }, [modelCatalog, selectedCapabilityVars, variables]);
  const supportedVariableIds = useMemo(
    () => selectedCapabilityVars.map((entry) => entry.id),
    [selectedCapabilityVars]
  );

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
  const selectedVariableCapability = variable ? selectedModelCapability?.variables?.[variable] : undefined;
  const selectedVariableDefaultFh = selectedCapabilityVarMap.get(variable)?.defaultFh ?? null;
  const selectedModelLatestOnly = readCapabilityLatestOnly(selectedModelCapability);
  const selectedModelSupportsSampling = readCapabilitySupportsSampling(selectedModelCapability);
  const selectedVariableConstraints = (selectedVariableCapability?.constraints ?? {}) as Record<string, unknown>;
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
    const start = toNumberOrNull(selectedVariableConstraints.overlay_fade_out_zoom_start);
    const end = toNumberOrNull(selectedVariableConstraints.overlay_fade_out_zoom_end);
    if (start === null || end === null || end <= start) {
      return null;
    }
    return { start, end };
  }, [selectedVariableConstraints.overlay_fade_out_zoom_start, selectedVariableConstraints.overlay_fade_out_zoom_end]);

  useEffect(() => {
    const nextDefault = defaultEnsembleViewForVariable(selectedModelCapability, variable);
    if (!nextDefault) {
      if (ensembleView) {
        setEnsembleView("");
      }
      return;
    }
    const ensembleMeta = (selectedVariableCapability?.ensemble ?? {}) as Record<string, unknown>;
    const supportedViews = Array.isArray(ensembleMeta.supported_views)
      ? ensembleMeta.supported_views.map((entry) => String(entry).trim().toLowerCase()).filter(Boolean)
      : [];
    if (!ensembleView || (supportedViews.length > 0 && !supportedViews.includes(ensembleView))) {
      setEnsembleView(nextDefault);
    }
  }, [selectedModelCapability, selectedVariableCapability, variable, ensembleView]);

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
      Object.entries(regionPresets).map(([id, preset]) => {
        const isNwsHazardsConusView = model === "nws_hazards" && id === "conus";
        return [
          id,
          {
            center: [preset.defaultCenter[0], preset.defaultCenter[1]] as [number, number],
            zoom: preset.defaultZoom,
            bbox: isNwsHazardsConusView
              ? NWS_HAZARDS_CONUS_VIEW_BBOX
              : id === "na"
                ? [-154, 12, -48, 72] as [number, number, number, number]
                : preset.bbox,
            fitMinZoom: isNwsHazardsConusView ? 3 : undefined,
            fitMinZoomBreakpoint: isNwsHazardsConusView ? 640 : undefined,
            minZoom: preset.minZoom,
            maxZoom: preset.maxZoom,
          },
        ];
      })
    );
  }, [model, regionPresets]);

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
        ensembleView,
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
    [ensembleView]
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
    let newest: string | null = null;
    let newestTimestamp = Number.NEGATIVE_INFINITY;
    for (const validTime of Object.values(frameValidTimesByHour)) {
      const timestamp = Date.parse(validTime);
      if (!Number.isFinite(timestamp)) {
        continue;
      }
      if (timestamp > newestTimestamp) {
        newest = validTime;
        newestTimestamp = timestamp;
      }
    }
    return newest;
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
  const selectionKey = `${model}:${selectionRunKey}:${variable}:${region}:${ensembleView || "-"}`;
  const telemetryRunId = gridOnlySelection && run === "latest"
    ? (resolvedGridLatestRunId ?? latestRunId ?? null)
    : (resolvedRunForRequests ?? (run !== "latest" ? run : latestRunId ?? null));
  const apiRoot = API_ORIGIN.replace(/\/$/, "");

  useEffect(() => {
    if (!gridOnlySelection || run !== "latest") {
      setResolvedGridLatestRunId(null);
      lastResolvedGridRunRef.current = null;
    }
  }, [gridOnlySelection, model, run, variable, ensembleView]);

  const previousRegionRef = useRef<string | null>(null);

  useEffect(() => {
    const previousRegion = previousRegionRef.current;
    previousRegionRef.current = region;
    if (previousRegion === null || previousRegion === region) {
      return;
    }
    setGridManifest(null);
    setCompositeGridManifests({});
  }, [region]);

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
            fetchGridManifest(model, candidateRun, variable, region, ensembleView, { signal: controller.signal })
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
            setCompositeGridManifests({});
            return;
          }
        }
        setResolvedGridLatestRunId(null);
        setGridManifest(null);
        setCompositeGridManifests({});
        return;
      }

      const manifest = await fetchGridManifest(model, resolvedRunForRequests, variable, region, ensembleView, { signal: controller.signal });
      if (controller.signal.aborted) {
        return;
      }
      setGridManifest(manifest);
      setCompositeGridManifests({});
    };

    void resolveManifest().catch(() => {
      if (controller.signal.aborted) {
        return;
      }
      if (gridOnlySelection && run === "latest") {
        setResolvedGridLatestRunId(null);
      }
      setGridManifest(null);
      setCompositeGridManifests({});
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
    ensembleView,
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
        source: availability?.source ?? model,
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
      source: availability?.source ?? model,
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

  useEffect(() => {
    if (!isCurrentAnalysisSelection && selectedAnchorCity) {
      setSelectedAnchorCity(null);
    }
  }, [isCurrentAnalysisSelection, selectedAnchorCity]);

  const runOptions = useMemo<Option[]>(() => {
    return buildRunOptions(runs, latestRunId, selectedTimeAxisMode);
  }, [runs, latestRunId, selectedTimeAxisMode]);

  const isObservedGridSelection = useMemo(() => {
    return String(model ?? "").trim().toLowerCase() === "mrms";
  }, [model]);
  const zoomSelectedGridLod = useMemo(() => {
    if (!gridManifest?.lods?.length) {
      return null;
    }
    return selectGridManifestLod(gridManifest, mapZoom);
  }, [gridManifest, mapZoom]);
  const selectedGridLod = useMemo(() => {
    if (!(isScrubbing || isScrubLodHoldActive) || !isObservedGridSelection || !zoomSelectedGridLod || !gridManifest?.lods?.length) {
      return zoomSelectedGridLod;
    }

    const currentWidth = Number(zoomSelectedGridLod.width);
    const currentHeight = Number(zoomSelectedGridLod.height);
    const currentPixels = Number.isFinite(currentWidth) && Number.isFinite(currentHeight)
      ? Math.max(0, Math.floor(currentWidth) * Math.floor(currentHeight))
      : 0;
    if (currentPixels < HIGH_RES_GRID_LOD_PIXEL_THRESHOLD) {
      return zoomSelectedGridLod;
    }

    const currentLevel = Number(zoomSelectedGridLod.level);
    const nextCoarserLod = [...gridManifest.lods]
      .filter((entry) => {
        const width = Number(entry?.width);
        const height = Number(entry?.height);
        const level = Number(entry?.level);
        if (!Number.isFinite(width) || !Number.isFinite(height) || !Number.isFinite(level)) {
          return false;
        }
        return level !== currentLevel && Math.floor(width) * Math.floor(height) < currentPixels;
      })
      .sort((left, right) => (Number(right.width) * Number(right.height)) - (Number(left.width) * Number(left.height)))[0]
      ?? null;

    return nextCoarserLod ?? zoomSelectedGridLod;
  }, [gridManifest, isObservedGridSelection, isScrubLodHoldActive, isScrubbing, zoomSelectedGridLod]);
  const selectedGridLodPixelCount = useMemo(() => {
    const width = Number(selectedGridLod?.width);
    const height = Number(selectedGridLod?.height);
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
      return null;
    }
    return Math.floor(width) * Math.floor(height);
  }, [selectedGridLod]);
  const isHighResObservedGridPlayback = useMemo(() => {
    return Boolean(
      isObservedGridSelection
      && selectedGridLodPixelCount !== null
      && selectedGridLodPixelCount >= HIGH_RES_GRID_LOD_PIXEL_THRESHOLD
    );
  }, [isObservedGridSelection, selectedGridLodPixelCount]);
  const isVeryHighResObservedGridPlayback = useMemo(() => {
    return Boolean(
      isObservedGridSelection
      && selectedGridLodPixelCount !== null
      && selectedGridLodPixelCount >= VERY_HIGH_RES_GRID_LOD_PIXEL_THRESHOLD
    );
  }, [isObservedGridSelection, selectedGridLodPixelCount]);
  const gridPlayStartAheadFrames = useMemo(() => {
    if (isVeryHighResObservedGridPlayback) {
      return VERY_HIGH_RES_GRID_PLAY_START_AHEAD_FRAMES;
    }
    if (isHighResObservedGridPlayback) {
      return HIGH_RES_GRID_PLAY_START_AHEAD_FRAMES;
    }
    return GRID_PLAY_START_AHEAD_FRAMES;
  }, [isHighResObservedGridPlayback, isVeryHighResObservedGridPlayback]);
  const autoplayReadyAheadFrames = useMemo(() => {
    if (isVeryHighResObservedGridPlayback) {
      return VERY_HIGH_RES_AUTOPLAY_READY_AHEAD;
    }
    if (isHighResObservedGridPlayback) {
      return HIGH_RES_AUTOPLAY_READY_AHEAD;
    }
    return AUTOPLAY_READY_AHEAD;
  }, [isHighResObservedGridPlayback, isVeryHighResObservedGridPlayback]);
  const autoplayLookAheadGraceMs = useMemo(() => {
    if (isVeryHighResObservedGridPlayback) {
      return VERY_HIGH_RES_AUTOPLAY_LOOKAHEAD_GRACE_MS;
    }
    if (isHighResObservedGridPlayback) {
      return HIGH_RES_AUTOPLAY_LOOKAHEAD_GRACE_MS;
    }
    return 80;
  }, [isHighResObservedGridPlayback, isVeryHighResObservedGridPlayback]);
  const autoplayStallSkipMs = useMemo(() => {
    if (isVeryHighResObservedGridPlayback) {
      return VERY_HIGH_RES_AUTOPLAY_STALL_SKIP_MS;
    }
    if (isHighResObservedGridPlayback) {
      return HIGH_RES_AUTOPLAY_STALL_SKIP_MS;
    }
    return AUTOPLAY_STALL_SKIP_MS;
  }, [isHighResObservedGridPlayback, isVeryHighResObservedGridPlayback]);
  const gridPlayStallMs = useMemo(() => {
    if (isVeryHighResObservedGridPlayback) {
      return VERY_HIGH_RES_GRID_PLAY_STALL_MS;
    }
    if (isHighResObservedGridPlayback) {
      return HIGH_RES_GRID_PLAY_STALL_MS;
    }
    return GRID_PLAY_STALL_MS;
  }, [isHighResObservedGridPlayback, isVeryHighResObservedGridPlayback]);
  const compositeLayerSpecs = useMemo(() => {
    return Array.isArray(gridManifest?.composite_layers)
      ? gridManifest.composite_layers.filter((layer) => Boolean(layer?.id) && Boolean(layer?.var))
      : [];
  }, [gridManifest]);
  useEffect(() => {
    if (!model || !resolvedRunForRequests || compositeLayerSpecs.length === 0) {
      setCompositeGridManifests({});
      return;
    }
    const controller = new AbortController();
    void Promise.all(
      compositeLayerSpecs.map(async (layer) => {
        const manifest = await fetchGridManifest(model, resolvedRunForRequests, layer.var, region, ensembleView, { signal: controller.signal });
        return [layer.id, manifest] as const;
      })
    ).then((entries) => {
      if (controller.signal.aborted) {
        return;
      }
      setCompositeGridManifests(Object.fromEntries(entries));
    }).catch(() => {
      if (controller.signal.aborted) {
        return;
      }
      setCompositeGridManifests({});
    });
    return () => {
      controller.abort();
    };
  }, [compositeLayerSpecs, model, resolvedRunForRequests]);
  const gridFrameByHour = useMemo(() => {
    const map = new Map<number, NonNullable<typeof selectedGridLod>["frames"][number]>();
    const frames = Array.isArray(selectedGridLod?.frames) ? selectedGridLod.frames : [];
    for (const frame of frames) {
      const fh = Number(frame?.fh);
      if (!Number.isFinite(fh)) {
        continue;
      }
      map.set(fh, frame);
    }
    return map;
  }, [selectedGridLod]);
  const gridFrameHours = useMemo(() => {
    return Array.from(gridFrameByHour.keys()).sort((a, b) => a - b);
  }, [gridFrameByHour]);
  const gridFrameIndexByHour = useMemo(() => {
    const indexByHour = new Map<number, number>();
    for (let index = 0; index < gridFrameHours.length; index += 1) {
      indexByHour.set(gridFrameHours[index], index);
    }
    return indexByHour;
  }, [gridFrameHours]);
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
    if (gridFrameHours.length === 0) {
      return null;
    }
    if (
      selectedTimeAxisMode === "observed"
      && selectedModelDefaultFrameSelection === "latest"
    ) {
      const mostRecentHour = mostRecentFrameHourByValidTime(Array.from(gridFrameByHour.values()));
      if (mostRecentHour !== null) {
        return mostRecentHour;
      }
    }
    return resolveForecastHour(
      gridFrameHours,
      Number.POSITIVE_INFINITY,
      selectedVariableDefaultFh,
      selectedModelDefaultFrameSelection,
    );
  }, [
    forecastHour,
    gridFrameByHour,
    gridFrameHours,
    isGridPreloadingForPlay,
    isPlaying,
    isScrubbing,
    isVariableSwitching,
    selectedModelDefaultFrameSelection,
    selectedTimeAxisMode,
    selectedVariableDefaultFh,
    targetForecastHour,
  ]);
  const resolvedGridDisplayHour = useMemo(() => {
    if (gridFrameHours.length === 0) {
      return null;
    }
    const requested = Number.isFinite(requestedGridDisplayHour)
      ? Number(requestedGridDisplayHour)
      : resolveForecastHour(
        gridFrameHours,
        Number.POSITIVE_INFINITY,
        selectedVariableDefaultFh,
        selectedModelDefaultFrameSelection,
      );
    return nearestFrame(gridFrameHours, requested);
  }, [gridFrameHours, requestedGridDisplayHour, selectedModelDefaultFrameSelection, selectedVariableDefaultFh]);
  const activeGridFrame = useMemo(() => {
    if (!Number.isFinite(resolvedGridDisplayHour)) {
      return null;
    }
    return gridFrameByHour.get(Number(resolvedGridDisplayHour)) ?? null;
  }, [gridFrameByHour, resolvedGridDisplayHour]);
  const compositeGridLayers = useMemo(() => {
    if (!Number.isFinite(resolvedGridDisplayHour) || compositeLayerSpecs.length === 0) {
      return [] as Array<{
        id: string;
        manifest: GridManifestResponse | null;
        frameUrl: string | null;
        frameHour: number | null;
        legend: LegendPayload | null;
      }>;
    }
    const targetHour = Number(resolvedGridDisplayHour);
    const effectiveGridLodLevel = Number(selectedGridLod?.level);
    return compositeLayerSpecs.map((layer) => {
      const manifest = compositeGridManifests[layer.id] ?? null;
      const selectedLod = Number.isFinite(effectiveGridLodLevel)
        ? manifest?.lods?.find((entry) => Number(entry?.level) === effectiveGridLodLevel) ?? selectGridManifestLod(manifest, mapZoom)
        : selectGridManifestLod(manifest, mapZoom);
      const frames = Array.isArray(selectedLod?.frames) ? selectedLod.frames : [];
      const frameHours = frames
        .map((entry) => Number(entry?.fh))
        .filter(Number.isFinite)
        .sort((a, b) => a - b);
      const resolvedHour = frameHours.length > 0 ? nearestFrame(frameHours, targetHour) : null;
      const frame = Number.isFinite(resolvedHour)
        ? frames.find((entry) => Number(entry?.fh) === Number(resolvedHour)) ?? null
        : null;
      const frameUrl = frame?.url
        ? (/^https?:\/\//i.test(frame.url)
            ? frame.url
            : `${apiRoot}${frame.url.startsWith("/") ? "" : "/"}${frame.url}`)
        : null;
      const legendMeta = manifest
        ? {
            ...(typeof manifest.palette?.kind === "string" ? { kind: manifest.palette.kind } : {}),
            ...(typeof manifest.grid?.units === "string" ? { units: manifest.grid.units } : {}),
            ...(typeof manifest.display_name === "string" ? { display_name: manifest.display_name } : {}),
            ...(manifest.legend ? { legend: manifest.legend } : {}),
            var_key: manifest.var,
          }
        : null;
      return {
        id: layer.id,
        manifest,
        frameUrl,
        frameHour: Number.isFinite(resolvedHour) ? Number(resolvedHour) : null,
        legend: buildLegend(legendMeta, opacity),
      };
    }).filter((layer) => layer.manifest && layer.frameUrl);
  }, [apiRoot, compositeGridManifests, compositeLayerSpecs, mapZoom, opacity, resolvedGridDisplayHour, selectedGridLod]);
  const activeGridFrameUrl = useMemo(() => {
    const frameUrl = activeGridFrame?.url;
    if (!frameUrl) {
      return null;
    }
    return /^https?:\/\//i.test(frameUrl)
      ? frameUrl
      : `${apiRoot}${frameUrl.startsWith("/") ? "" : "/"}${frameUrl}`;
  }, [activeGridFrame, apiRoot]);
  const shouldWaitForInitialGridFrame =
    hasRenderableSelection
    && selectionSupportsGrid
    && Boolean(activeGridFrameUrl)
    && !firstWeatherFramePainted;
  const showInitialMapSkeleton = loading || !isMapReady || shouldWaitForInitialGridFrame;
  const initialMapSkeletonStatus = loading || !bootstrapHydrated || !isMapReady
    ? "Loading viewer"
    : "Preparing first frame";

  useEffect(() => {
    if (!showInitialMapSkeleton) {
      return undefined;
    }
    return startSiteLoading(initialMapSkeletonStatus);
  }, [initialMapSkeletonStatus, showInitialMapSkeleton, startSiteLoading]);
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
  const gridReadyHours = useMemo(() => {
    const readyHours: number[] = [];
    for (const hour of gridFrameHours) {
      const frameUrl = normalizeGridFrameUrl(gridFrameByHour.get(hour)?.url);
      if (frameUrl && gridReadyFrameUrlsRef.current.has(frameUrl)) {
        readyHours.push(hour);
      }
    }
    return readyHours;
  }, [gridFrameByHour, gridFrameHours, gridReadyVersion, normalizeGridFrameUrl]);
  const gridReadyHourSet = useMemo(() => {
    return new Set(gridReadyHours);
  }, [gridReadyHours]);
  const presentedGridDisplayHour = useMemo(() => {
    if (gridFrameHours.length === 0) {
      return null;
    }
    const requestedHourCandidate = Number.isFinite(resolvedGridDisplayHour) ? Number(resolvedGridDisplayHour) : null;
    if (!Number.isFinite(requestedHourCandidate)) {
      return Number.isFinite(visibleGridFrameHour) ? Number(visibleGridFrameHour) : null;
    }
    const requestedHour = Number(requestedHourCandidate);
    if (gridReadyHourSet.has(requestedHour)) {
      return requestedHour;
    }
    if (Number.isFinite(visibleGridFrameHour) && gridFrameByHour.has(Number(visibleGridFrameHour))) {
      return Number(visibleGridFrameHour);
    }

    const nearestReadyHour = nearestSortedNumber(gridReadyHours, requestedHour);
    return nearestReadyHour ?? requestedHour;
  }, [
    gridFrameByHour,
    gridFrameHours,
    gridReadyHourSet,
    gridReadyHours,
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
    const currentIndex = gridFrameIndexByHour.get(currentHour) ?? -1;
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
  }, [gridFrameHours, gridFrameIndexByHour, gridReadyHourSet]);
  const gridReadyCount = useMemo(() => {
    return gridReadyHours.length;
  }, [gridReadyHours]);
  const gridPlaybackStartHour = useMemo(() => {
    if (gridFrameHours.length === 0) {
      return null;
    }
    const requested = Number.isFinite(targetForecastHour)
      ? Number(targetForecastHour)
      : (Number.isFinite(forecastHour)
        ? Number(forecastHour)
        : (selectedTimeAxisMode === "observed"
          && selectedModelDefaultFrameSelection === "latest"
          ? (mostRecentFrameHourByValidTime(Array.from(gridFrameByHour.values())) ?? resolveForecastHour(
            gridFrameHours,
            Number.POSITIVE_INFINITY,
            selectedVariableDefaultFh,
            selectedModelDefaultFrameSelection,
          ))
          : resolveForecastHour(
            gridFrameHours,
            Number.POSITIVE_INFINITY,
            selectedVariableDefaultFh,
            selectedModelDefaultFrameSelection,
          )));
    return nearestFrame(gridFrameHours, requested);
  }, [forecastHour, gridFrameByHour, gridFrameHours, selectedModelDefaultFrameSelection, selectedTimeAxisMode, selectedVariableDefaultFh, targetForecastHour]);
  const gridPlaybackAheadReadyCount = useMemo(() => {
    if (!Number.isFinite(gridPlaybackStartHour)) {
      return 0;
    }
    return countGridAheadReadyFrames(Number(gridPlaybackStartHour), gridPlayStartAheadFrames);
  }, [countGridAheadReadyFrames, gridPlaybackStartHour, gridPlayStartAheadFrames, gridReadyVersion]);
  const isGridPlaybackStartReady = useMemo(() => {
    if (!Number.isFinite(gridPlaybackStartHour)) {
      return false;
    }
    const currentHour = Number(gridPlaybackStartHour);
    if (!gridReadyHourSet.has(currentHour)) {
      return false;
    }
    const currentIndex = gridFrameIndexByHour.get(currentHour) ?? -1;
    if (currentIndex < 0) {
      return false;
    }
    const remainingAhead = Math.max(0, gridFrameHours.length - currentIndex - 1);
    const requiredAhead = Math.min(gridPlayStartAheadFrames, remainingAhead);
    return gridPlaybackAheadReadyCount >= requiredAhead;
  }, [
    gridFrameHours,
    gridFrameIndexByHour,
    gridPlaybackAheadReadyCount,
    gridPlaybackStartHour,
    gridPlayStartAheadFrames,
    gridReadyHourSet,
  ]);
  const isGridLowMidActive = useMemo(() => {
    return Boolean(
      gridManifest
      && selectedGridLod
      && Array.isArray(gridManifest.bbox)
      && gridManifest.bbox.length === 4
      && (presentedGridFrameUrl || compositeGridLayers.length > 0)
    );
  }, [compositeGridLayers.length, gridManifest, presentedGridFrameUrl, selectedGridLod]);
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
    regionRef.current = region;
  }, [region]);

  useEffect(() => {
    telemetryRunIdRef.current = telemetryRunId;
  }, [telemetryRunId]);

  useEffect(() => {
    const captureViewerSessionEnded = (useBeaconTransport = false) => {
      if (!viewerOpenedTrackedRef.current) {
        return;
      }
      if (viewerSessionEndedTrackedRef.current) {
        return;
      }
      viewerSessionEndedTrackedRef.current = true;
      const durationMs = typeof performance === "undefined"
        ? 0
        : Math.max(0, performance.now() - viewerMountedAtRef.current);
      captureProductAnalyticsEvent("viewer_session_ended", {
        model_id: modelRef.current || null,
        variable_id: variableRef.current || null,
        run_id: telemetryRunIdRef.current,
        region_id: regionRef.current || null,
        forecast_hour: Number.isFinite(forecastHourRef.current) ? forecastHourRef.current : null,
        duration_seconds: Math.floor(durationMs / 1000),
      }, useBeaconTransport ? {
        send_instantly: true,
        transport: "sendBeacon",
      } : undefined);
    };

    const handlePageHide = (event: PageTransitionEvent) => {
      if (event.persisted) {
        return;
      }
      captureViewerSessionEnded(true);
    };

    window.addEventListener("pagehide", handlePageHide, { passive: true });

    return () => {
      window.removeEventListener("pagehide", handlePageHide);
      captureViewerSessionEnded();
    };
  }, []);

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
  const visibleOverlayFrame = useMemo(() => {
    if (Number.isFinite(visibleOverlayHour)) {
      return frameByHour.get(Number(visibleOverlayHour)) ?? null;
    }
    return currentFrame;
  }, [currentFrame, frameByHour, visibleOverlayHour]);
  // Keep the legacy GeoJSON contour renderer as the production path for now.
  // The companion-grid shader path regressed line quality and frame availability
  // for GFS-style products, even though the shaded grid playback itself is good.
  const gridContour: GridContourLayerConfig | null = null;

  // During a variable switch the old variable's imagery is still on screen;
  // keep its paint settings in effect until the new variable is promoting.
  const displayedOverlayVariable = isVariableSwitching ? (visualVariable || variable) : variable;
  const contourGeoJsonUrl = useMemo(() => {
    if (!model || !displayedOverlayVariable || !visibleOverlayFrame || !resolvedRunForRequests) {
      return null;
    }
    const frameMeta = extractLegendMeta(visibleOverlayFrame) ?? extractLegendMeta(frameRows[0] ?? null);
    const contours = gridManifest?.contours ?? frameMeta?.contours;
    if (!contours || typeof contours !== "object") {
      return null;
    }
    const contourKey = Object.keys(contours)[0];
    if (!contourKey) {
      return null;
    }
    return buildContourUrl({
      model,
      run: resolvedRunForRequests,
      varKey: displayedOverlayVariable,
      fh: Number(visibleOverlayFrame.fh),
      key: contourKey,
    });
  }, [displayedOverlayVariable, frameRows, gridManifest, model, resolvedRunForRequests, visibleOverlayFrame]);
  const contourPrefetchUrls = useMemo(() => {
    if (!model || !displayedOverlayVariable || frameRows.length <= 1 || !resolvedRunForRequests) {
      return [] as string[];
    }
    const currentHour = Number.isFinite(visibleOverlayHour) ? Number(visibleOverlayHour) : Number(visibleOverlayFrame?.fh);
    const orderedRows = [...frameRows].sort((a, b) => Number(a.fh) - Number(b.fh));
    const pivotIndex = orderedRows.findIndex((row) => Number(row.fh) === currentHour);
    const candidateRows = pivotIndex >= 0
      ? [
          ...orderedRows.slice(pivotIndex + 1, pivotIndex + 9),
          ...orderedRows.slice(Math.max(0, pivotIndex - 2), pivotIndex).reverse(),
        ]
      : orderedRows.slice(1, 11);
    const urls: string[] = [];
    for (const row of candidateRows) {
      const meta = extractLegendMeta(row);
      const contours = gridManifest?.contours ?? meta?.contours;
      if (!contours || typeof contours !== "object") {
        continue;
      }
      const contourKey = Object.keys(contours)[0];
      if (!contourKey) {
        continue;
      }
      const url = buildContourUrl({
        model,
        run: resolvedRunForRequests,
        varKey: displayedOverlayVariable,
        fh: Number(row.fh),
        key: contourKey,
      });
      if (url && url !== contourGeoJsonUrl && !urls.includes(url)) {
        urls.push(url);
      }
    }
    return urls;
  }, [contourGeoJsonUrl, displayedOverlayVariable, frameRows, gridManifest, model, resolvedRunForRequests, visibleOverlayFrame, visibleOverlayHour]);
  const pressureCenters = useMemo(() => {
    const frameMeta = extractLegendMeta(visibleOverlayFrame) ?? extractLegendMeta(frameRows[0] ?? null);
    return Array.isArray(frameMeta?.pressure_centers) ? frameMeta.pressure_centers : [];
  }, [frameRows, visibleOverlayFrame]);
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
  const hoverSamplingVariable = String(variable ?? "").trim().toLowerCase();
  const hoverSamplingDisabled = hoverSamplingVariable === "hgt500_anom";
  const hoverSamplingEnabled = selectedModelSupportsSampling
    && !hoverSamplingDisabled
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
    ensembleView: hoverSamplingEnabled ? ensembleView : "",
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
        const requestedEnsembleView = initialPermalink.ensembleView?.trim().toLowerCase();
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
        const preferredDefaultModel = orderedVisibleModelIds.includes(DEFAULT_VIEWER_MODEL_ID) ? DEFAULT_VIEWER_MODEL_ID : "";
        const availableModelId = orderedVisibleModelIds.find((modelId) => {
          const availability = capabilitiesData.availability?.[modelId];
          return Boolean(availability?.latest_run);
        });
        const nextModel = requestedModel && orderedVisibleModelIds.includes(requestedModel)
          ? requestedModel
          : (preferredDefaultModel || availableModelId || orderedVisibleModelIds[0] || "");
        const modelOptions = makeModelOptions(modelRows);
        setModels(modelOptions);
        setModel(nextModel);

        const modelCapability = nextModel ? capabilitiesData.model_catalog[nextModel] : null;
        const capabilityVars = normalizeCapabilityVarRows(modelCapability);
        const variableOptions = makeVariableOptions(capabilityVars);
        const variableIds = variableOptions.map((opt) => opt.value);
        const defaultVarKey = String(modelCapability?.defaults?.default_var_key ?? "").trim();
        const preferredDefaultVariable = nextModel === DEFAULT_VIEWER_MODEL_ID && variableIds.includes(DEFAULT_VIEWER_VARIABLE_ID)
          ? DEFAULT_VIEWER_VARIABLE_ID
          : "";
        const nextVariable = requestedVariable && variableIds.includes(requestedVariable)
          ? requestedVariable
          : (preferredDefaultVariable || (variableIds.includes(defaultVarKey) ? defaultVarKey : (variableIds[0] ?? "")));
        const nextVariableCapability = nextVariable ? modelCapability?.variables?.[nextVariable] : undefined;
        setVariables(variableOptions);
        setVariable(nextVariable);
        const nextEnsembleView = requestedEnsembleView || defaultEnsembleViewForVariable(modelCapability, nextVariable);
        setEnsembleView(nextEnsembleView);

        setRegionPresets(regionPresetData);
        const canonicalRegion = String(
          modelCapability?.constraints?.canonical_region
          ?? modelCapability?.canonical_region
          ?? MAP_VIEW_DEFAULTS.region
        ).trim();
        const regionOptions = filterRegionOptionsForVariable(
          regionPresetData,
          canonicalRegion,
          nextVariableCapability?.supported_build_regions,
        );
        const allowedRegionIds = regionOptions.map((option) => option.value);
        setRegions(regionOptions);
        const nextRegion = requestedRegion && allowedRegionIds.includes(requestedRegion)
          ? requestedRegion
          : pickPreferred(allowedRegionIds, canonicalRegion || MAP_VIEW_DEFAULTS.region);
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
    const regionIds = Object.keys(regionPresets);
    if (regionIds.length === 0) {
      setRegions([]);
      return;
    }
    const canonicalRegion = String(
      selectedModelCapability?.constraints?.canonical_region
      ?? selectedModelCapability?.canonical_region
      ?? MAP_VIEW_DEFAULTS.region
    ).trim();
    const nextRegionOptions = filterRegionOptionsForVariable(
      regionPresets,
      canonicalRegion,
      selectedVariableCapability?.supported_build_regions,
    );
    setRegions(nextRegionOptions);
    const allowedRegionIds = nextRegionOptions.map((option) => option.value);
    if (allowedRegionIds.length === 0) {
      return;
    }
    setRegion((currentRegion) => (
      allowedRegionIds.includes(currentRegion)
        ? currentRegion
        : pickPreferred(allowedRegionIds, canonicalRegion || MAP_VIEW_DEFAULTS.region)
    ));
  }, [regionPresets, selectedModelCapability, selectedVariableCapability]);

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
          fetchManifest(model, manifestRunKey, region, ensembleView, { signal: controller.signal }).catch(() => null),
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
          manifestData = await fetchManifest(model, nextManifestRunKey, region, ensembleView, { signal: controller.signal }).catch(() => null);
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
        const nextVar = pickDefaultVariableForModel(model, selectedModelCapability, variableIds);
        setVariables(variableOptions);
        setVariable((prev) => (prev && variableIds.includes(prev) ? prev : nextVar));
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
  }, [model, run, runs, selectedCapabilityVars, selectedModelCapability, gridOnlySelection, resolvedGridLatestRunId, ensembleView]);

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
          setLoadedFramesKey(selectionKey);
          setForecastHour((prev) =>
            resolveForecastHourFromRows(
              rows,
              prev,
              selectedVariableDefaultFh,
              selectedModelDefaultFrameSelection,
              selectedTimeAxisMode,
            )
          );
          setTargetForecastHour((prev) =>
            resolveForecastHourFromRows(
              rows,
              prev,
              selectedVariableDefaultFh,
              selectedModelDefaultFrameSelection,
              selectedTimeAxisMode,
            )
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
        const rows = await fetchFrames(model, framesRunKey, variable, region, ensembleView, { signal: controller.signal });
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
        setLoadedFramesKey(selectionKey);
        // Use the merged frame set so resolveForecastHour sees ALL expected
        // hours (including manifest-only rows), not just COG-ready ones.
        // Note: React processes functional updaters synchronously within the
        // same synchronous block, so `mergedRows` is populated by this point.
        setForecastHour((prev) =>
          resolveForecastHourFromRows(
            mergedRows,
            prev,
            selectedVariableDefaultFh,
            selectedModelDefaultFrameSelection,
            selectedTimeAxisMode,
          )
        );
        setTargetForecastHour((prev) =>
          resolveForecastHourFromRows(
            mergedRows,
            prev,
            selectedVariableDefaultFh,
            selectedModelDefaultFrameSelection,
            selectedTimeAxisMode,
          )
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
    selectedTimeAxisMode,
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

    if (model === "mrms" || model === "goes-east" || (variable && resolveAnchorDisplayRule(variable).mode === "hidden")) {
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
            const manifestData = await fetchManifest(model, manifestRunKey, region, ensembleView, { signal: tickController.signal });
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
              const nextVar = pickDefaultVariableForModel(model, selectedModelCapability, variableIds);
              setVariables(variableOptions);
              setVariable((prev) => (prev && variableIds.includes(prev) ? prev : nextVar));
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
              const nextGridManifest = await fetchGridManifest(model, gridRunKey, variable, region, ensembleView, { signal: tickController.signal });
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
                return merged.length === prevRows.length && JSON.stringify(merged) === JSON.stringify(prevRows)
                  ? prevRows
                  : merged;
              });
              setForecastHour((prev) =>
                resolveForecastHourFromRows(
                  rows,
                  prev,
                  selectedVariableDefaultFh,
                  selectedModelDefaultFrameSelection,
                  selectedTimeAxisMode,
                )
              );
              setTargetForecastHour((prev) =>
                resolveForecastHourFromRows(
                  rows,
                  prev,
                  selectedVariableDefaultFh,
                  selectedModelDefaultFrameSelection,
                  selectedTimeAxisMode,
                )
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
          const rows = await fetchFrames(model, framesRunKey, variable, region, ensembleView, { signal: tickController.signal });
          if (cancelled || tickController?.signal.aborted) {
            return;
          }

          // Refresh the grid manifest before updating frameRows so that
          // gridFrameHours is in sync with the slider when the user scrubs.
          if (prefersGridSubstrate && selectionSupportsGrid) {
            const gridRunKey = gridOnlySelection && run === "latest"
              ? (resolvedGridLatestRunId ?? framesRunKey)
              : resolvedRunForRequests;
            const nextGridManifest = await fetchGridManifest(model, gridRunKey, variable, region, ensembleView, { signal: tickController.signal });
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

          setFrameRows((prevRows) => {
            if (rows.length === prevRows.length && JSON.stringify(rows) === JSON.stringify(prevRows)) {
              return prevRows;
            }
            return rows;
          });
          setForecastHour((prev) =>
            resolveForecastHourFromRows(
              rows,
              prev,
              selectedVariableDefaultFh,
              selectedModelDefaultFrameSelection,
              selectedTimeAxisMode,
            )
          );
          setTargetForecastHour((prev) =>
            resolveForecastHourFromRows(
              rows,
              prev,
              selectedVariableDefaultFh,
              selectedModelDefaultFrameSelection,
              selectedTimeAxisMode,
            )
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
  }, [model, run, variable, ensembleView, resolvedRunForRequests, runManifest, isPageVisible, selectedCapabilityVars, selectedModelCapability, selectedVariableDefaultFh, selectedModelDefaultFrameSelection, selectedTimeAxisMode, hasRenderableSelection, loadedFramesKey, selectionKey, selectedModelLatestOnly, gridOnlySelection, resolvedGridLatestRunId]);

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
    const LOOKAHEAD_GRACE_MS = autoplayLookAheadGraceMs;

    const tick = (now: number) => {
      const currentHour = gridPlaybackHourRef.current
        ?? (Number.isFinite(targetForecastHourRef.current) ? targetForecastHourRef.current : forecastHourRef.current);
      const currentIndex = gridFrameIndexByHour.get(currentHour) ?? -1;
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
        if (gridReadyHourSet.has(nextHour)) {
          // Look-ahead: only advance if the next AUTOPLAY_READY_AHEAD frames
          // beyond this one are also ready (or we're near the end).  This
          // prevents advancing into a gap that will immediately stall.
          let aheadReady = true;
          const lookAheadEnd = Math.min(nextIndex + autoplayReadyAheadFrames, gridFrameHours.length - 1);
          for (let li = nextIndex + 1; li <= lookAheadEnd; li++) {
            const laHour = gridFrameHours[li];
            if (!gridReadyHourSet.has(laHour)) {
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
          // Drop playback debt so a buffered frame resumes at the normal cadence
          // instead of racing through the next few frames after a wait.
          lookAheadWaitMs += deltaMs;
          accumulatedMs = 0;
          break;
        }

        // Next frame isn't ready — reset look-ahead wait and accumulate stall time.
        lookAheadWaitMs = 0;
        accumulatedMs = 0;
        if (stalledOnIndex !== nextIndex) {
          stalledOnIndex = nextIndex;
          stallMs = 0;
        }
        stallMs += deltaMs;

        // After stalling long enough, try skipping ahead within a window.
        if (stallMs >= autoplayStallSkipMs) {
          const maxStep = Math.min(AUTOPLAY_SKIP_WINDOW, gridFrameHours.length - 1 - currentIndex);
          for (let step = 2; step <= maxStep; step += 1) {
            const candidateHour = gridFrameHours[currentIndex + step];
            if (gridReadyHourSet.has(candidateHour)) {
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
  }, [
    autoplayLookAheadGraceMs,
    autoplayReadyAheadFrames,
    autoplayStallSkipMs,
    gridFrameHours,
    gridFrameIndexByHour,
    gridReadyHourSet,
    isGridPlayable,
    isPlaying,
  ]);

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
    const allowStallStart = currentReady && stalledMs >= gridPlayStallMs;

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
    gridPlayStallMs,
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
      || (payload.selectionKey !== undefined && payload.selectionKey !== selectionKey && !payload.selectionKey.startsWith(`${selectionKey}:`))
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
    const nextModelCapability = capabilities?.model_catalog?.[nextModel] ?? null;
    const nextVariableOptions = makeVariableOptions(normalizeCapabilityVarRows(nextModelCapability));
    const nextVariableIds = nextVariableOptions.map((option) => option.value);
    const nextSupportedVariableIds = new Set(nextVariableIds);
    const nextVariable = variable && nextSupportedVariableIds.has(variable)
      ? variable
      : pickDefaultVariableForModel(nextModel, nextModelCapability, nextVariableIds);
    setNewRunNotice((current) => (current?.model === nextModel ? current : null));
    setRun("latest");
    setRuns([]);
    setRunManifest(null);
    setFrameRows([]);
    pendingVariableSwitchRef.current = null;
    setVariableSwitchState(null);
    if (nextVariableOptions.length > 0) {
      setVariables(nextVariableOptions);
    }
    if (nextVariable !== variable) {
      setVariable(nextVariable);
      setVisualVariable(nextVariable);
    }
    setModel(nextModel);
    captureProductAnalyticsEvent("model_selected", {
      model_id: nextModel,
      variable_id: nextVariable || null,
      run_id: telemetryRunId,
      region_id: region || null,
      forecast_hour: Number.isFinite(forecastHour) ? forecastHour : null,
    });
  }, [capabilities, variable, telemetryRunId, region, forecastHour]);

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
      expectedSelectionKey: `${model}:${selectionRunKey}:${nextVariable}:${region}:${ensembleView || "-"}`,
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
  }, [model, variable, visualVariable, telemetryRunId, region, forecastHour, selectionRunKey, ensembleView]);

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
    const wasScrubbing = previousIsScrubbingRef.current;
    previousIsScrubbingRef.current = isScrubbing;

    if (isScrubbing) {
      if (scrubLodHoldTimerRef.current !== null) {
        window.clearTimeout(scrubLodHoldTimerRef.current);
        scrubLodHoldTimerRef.current = null;
      }
      setIsScrubLodHoldActive(false);
      return;
    }

    if (!wasScrubbing) {
      return;
    }

    setIsScrubLodHoldActive(true);
    if (scrubLodHoldTimerRef.current !== null) {
      window.clearTimeout(scrubLodHoldTimerRef.current);
    }
    scrubLodHoldTimerRef.current = window.setTimeout(() => {
      scrubLodHoldTimerRef.current = null;
      setIsScrubLodHoldActive(false);
    }, HIGH_RES_SCRUB_LOD_HOLD_MS);
  }, [isScrubbing]);

  useEffect(() => {
    return () => {
      if (scrubLodHoldTimerRef.current !== null) {
        window.clearTimeout(scrubLodHoldTimerRef.current);
      }
    };
  }, []);

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

    const nextTarget = resolveForecastHour(
      selectableFrameHours,
      targetForecastHour,
      selectedVariableDefaultFh,
      selectedModelDefaultFrameSelection,
    );
    if (nextTarget === forecastHour) {
      return;
    }
    setForecastHour(nextTarget);
  }, [targetForecastHour, forecastHour, selectableFrameHours, selectedVariableDefaultFh, selectedModelDefaultFrameSelection]);

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
    const fromOptions = runOptions.find((entry) => entry.value === run)?.label;
    if (fromOptions) {
      return fromOptions;
    }
    if (run === "latest") {
      return latestRunLabel(latestRunId, selectedTimeAxisMode);
    }
    return formatRunLabel(run, selectedTimeAxisMode);
  }, [runOptions, run, latestRunId, selectedTimeAxisMode, currentFrame, frameRows]);
  const latestAvailableRunLabel = useMemo(() => {
    return latestRunId ? formatRunLabel(latestRunId, selectedTimeAxisMode) : null;
  }, [latestRunId, selectedTimeAxisMode]);
  const selectedModelAvailability = useMemo(() => {
    return model ? capabilities?.availability?.[model] ?? null : null;
  }, [capabilities, model]);
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
  const runAvailability = useMemo(() => {
    if (RUN_AVAILABILITY_BADGE_EXCLUDED_MODELS.has(model)) {
      return null;
    }
    if (selectedTimeAxisMode === "observed") {
      return null;
    }
    if (run !== "latest") {
      return null;
    }

    const latestRun = selectedModelAvailability?.latest_run ?? latestRunId ?? null;
    if (!latestRun) {
      return null;
    }

    const latestLabel = formatRunLabel(latestRun, selectedTimeAxisMode);
    const manifestVariableFrames = Array.isArray(runManifest?.variables?.[variable]?.frames)
      ? runManifest.variables?.[variable]?.frames ?? []
      : [];
    const manifestVariableMaxForecastHour = manifestVariableFrames.length > 0
      ? Math.max(...manifestVariableFrames.map((frame) => Number(frame?.fh)).filter(Number.isFinite))
      : null;
    const selectableAvailableForecastHour = selectableFrameHours.length > 0
      ? Math.max(...selectableFrameHours.filter(Number.isFinite))
      : null;
    const declaredVariableMaxForecastHour = toNumberOrNull(selectedVariableConstraints.max_fh);
    const inferredTargetMaxForecastHour = inferLatestRunTargetMaxForecastHour(model, latestRun);

    const targetMaxForecastHour = Number.isFinite(selectedModelAvailability?.latest_run_target_max_fh)
      ? Math.max(0, Number(selectedModelAvailability?.latest_run_target_max_fh))
      : inferredTargetMaxForecastHour !== null
        ? inferredTargetMaxForecastHour
      : null;
    const readyVars = Array.isArray(selectedModelAvailability?.latest_run_ready_vars)
      ? selectedModelAvailability.latest_run_ready_vars
      : [];
    const selectedVariableReady = variable ? readyVars.includes(variable) : true;
    const degradedReason = String(selectedModelAvailability?.degraded_reason ?? "").trim().replace(/_/g, " ");
    const unusable = selectedModelAvailability?.usable === false;
    const stale = selectedModelAvailability?.stale === true;

    const resolvedTotalForecastHour =
      targetMaxForecastHour
      ?? declaredVariableMaxForecastHour
      ?? manifestVariableMaxForecastHour
      ?? null;
    const resolvedAvailableForecastHour = selectableAvailableForecastHour;

    const resolvedTone: "live" | "delayed" | "stale" | "unavailable" =
      unusable
        ? "unavailable"
        : stale
          ? "stale"
          : selectedVariableReady
            ? "live"
            : "delayed";

    if (resolvedTotalForecastHour !== null && resolvedAvailableForecastHour !== null) {
      const cappedAvailable = Math.max(0, Math.min(resolvedAvailableForecastHour, resolvedTotalForecastHour));
      const isComplete = cappedAvailable >= resolvedTotalForecastHour && resolvedTotalForecastHour > 0;
      const description = `${selectedVariableLabel} · latest ${latestLabel} · ${cappedAvailable}/${resolvedTotalForecastHour} forecast hours ${isComplete ? "complete" : "available"}`;
      return {
        label: `${cappedAvailable}/${resolvedTotalForecastHour} forecast hours ${isComplete ? "complete" : "available"}`,
        description,
        tone: isComplete ? (resolvedTone === "live" ? "live" : resolvedTone) : resolvedTone,
      };
    }

    const fallbackLabel = selectedVariableReady ? "Latest ready" : "Latest updating";
    const fallbackDescription = `${selectedVariableLabel} · latest ${latestLabel} · ${fallbackLabel.toLowerCase()}`;
    return {
      label: fallbackLabel,
      description: fallbackDescription,
      tone: resolvedTone,
    };
  }, [
    latestRunId,
    frameRows,
    run,
    runManifest,
    model,
    selectedModelAvailability,
    selectedTimeAxisMode,
    selectedVariableConstraints,
    selectedVariableLabel,
    selectableFrameHours,
    variable,
  ]);
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

  usePermalinkSync({
    bootstrapHydrated,
    mapViewHydratedRef,
    mapViewTick,
    mapViewRef,
    model,
    run,
    variable,
    ensembleView,
    resolvedForecastHourPermalink,
    region,
  });

  const toolbarContextValue = useMemo(() => ({
    region,
    onRegionChange: handleRegionChange,
    model,
    onModelChange: handleModelChange,
    run,
    onRunChange: handleRunChange,
    variable,
    onVariableChange: handleVariableChange,
    regions,
    models,
    runs: runOptions,
    variables,
    variableCatalog: allVariableCatalog,
    supportedVariableIds,
    disabled: loading || models.length === 0,
    runDisplayLabel: selectedRunLabel,
    latestAvailableRunLabel,
    hasNewerRunAvailable,
    onViewLatestRun: hasNewerRunAvailable ? handleViewLatestRun : undefined,
    runSelectionLocked: selectedModelLatestOnly,
    sourceStatusLabel: observedSourceStatus?.label ?? null,
    sourceStatusDescription: observedSourceStatus?.description ?? null,
    sourceStatusTone: observedSourceStatus?.tone ?? null,
    runAvailabilityLabel: runAvailability?.label ?? null,
    runAvailabilityDescription: runAvailability?.description ?? null,
    runAvailabilityTone: runAvailability?.tone ?? null,
    pointLabelsEnabled,
    onPointLabelsEnabledChange: setPointLabelsEnabled,
    legendVisible,
    onLegendVisibleChange: (nextVisible: boolean) => {
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
    },
    basemapMode,
    onBasemapModeChange: setBasemapMode,
    opacity,
    onOpacityChange: setOpacity,
    zoomControlsVisible,
    onZoomControlsVisibleChange: setZoomControlsVisible,
    legendPopoverOpen,
    onLegendPopoverOpenChange: setLegendPopoverOpen,
    displayPanelOpen,
    onDisplayPanelOpenChange: setDisplayPanelOpen,
    legend,
    onShare: handleOpenShareModal,
    mobileControlsOpen,
    onMobileControlsOpenChange: setMobileControlsOpen,
    layoutMode: viewerLayoutMode,
  }), [
    region, handleRegionChange, model, handleModelChange, run, handleRunChange,
    variable, handleVariableChange, regions, models, runOptions, variables,
    allVariableCatalog, supportedVariableIds,
    loading, selectedRunLabel, latestAvailableRunLabel, hasNewerRunAvailable,
    handleViewLatestRun, selectedModelLatestOnly, observedSourceStatus, runAvailability,
    pointLabelsEnabled, legendVisible, basemapMode, opacity, zoomControlsVisible,
    legendPopoverOpen, displayPanelOpen, handleOpenShareModal, viewerLayoutMode, legend,
    telemetryRunId, forecastHour, mobileControlsOpen,
  ]);

  return (
    <ViewerToolbarContext.Provider value={toolbarContextValue}>
    <div className="relative flex min-h-0 flex-1 flex-col">
      <SiteHeader variant="app" />

      <div className="relative flex-1 min-h-0 overflow-hidden pt-14">
        <MapCanvas
          selectionKey={selectionKey}
          selectionEpoch={selectionEpoch}
          gridManifest={isGridLowMidActive ? gridManifest : null}
          compositeGridLayers={isGridLowMidActive ? compositeGridLayers : []}
          gridLodLevel={isGridLowMidActive ? Number(selectedGridLod?.level ?? 0) : null}
          gridFrameUrl={isGridLowMidActive && compositeGridLayers.length === 0 ? activeGridFrameUrl : null}
          gridFrameHour={isGridLowMidActive && Number.isFinite(resolvedGridDisplayHour) ? Number(resolvedGridDisplayHour) : null}
          gridPrefetchPivotHour={isGridLowMidActive && Number.isFinite(resolvedGridDisplayHour) ? Number(resolvedGridDisplayHour) : null}
          gridLegend={isGridLowMidActive ? legend : null}
          gridActive={isGridLowMidActive}
          gridContour={isGridLowMidActive ? gridContour : null}
            contourGeoJsonUrl={contourGeoJsonUrl}
            contourPrefetchUrls={contourPrefetchUrls}
            pressureCenters={pressureCenters}
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
          onAnchorClick={isCurrentAnalysisSelection ? setSelectedAnchorCity : undefined}
          onVectorHazardClick={model === "nws_hazards" ? setSelectedVectorHazard : undefined}
          showZoomControls={zoomControlsVisible}
          legendButtonVisible={!isDesktopViewerLayout && legendVisible}
          legendButtonActive={!isDesktopViewerLayout && legendVisible && legendPopoverOpen}
          onLegendButtonClick={!isDesktopViewerLayout ? () => setLegendPopoverOpen(v => !v) : undefined}
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
              ? (activeTooltip.label?.trim() || `${activeTooltip.value.toFixed(1)} ${activeTooltip.units}`)
              : activeTooltip.label}
          </div>
        )}

        {error && (
          <div className="absolute left-4 top-4 z-40 flex items-center gap-2 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive shadow-lg backdrop-blur-md">
            <AlertCircle className="h-3.5 w-3.5" />
            {error}
          </div>
        )}

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
          modelLabel={selectedModelLabel}
          variableLabel={selectedVariableLabel}
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

      {selectedVectorHazard ? (
        <Suspense fallback={null}>
          <NwsHazardModal
            open={!!selectedVectorHazard}
            onClose={() => setSelectedVectorHazard(null)}
            hazard={selectedVectorHazard}
          />
        </Suspense>
      ) : null}
    </div>
    </ViewerToolbarContext.Provider>
  );
}
