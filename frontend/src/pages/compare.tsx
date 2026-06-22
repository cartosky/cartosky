import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, ArrowLeftRight, Layers, Moon, Settings, Share2, Sun, X } from "lucide-react";
import type { Map as MapLibreMap } from "maplibre-gl";

import { Link } from "react-router-dom";
import ComparePanel from "@/components/compare/ComparePanel";
import CompareScrubber from "@/components/compare/CompareScrubber";
import { CompareTooltip } from "@/components/compare/CompareTooltip";
import { TwfShareModal, type SharePayload } from "@/components/twf-share-modal";
import type { BasemapMode } from "@/components/map-canvas";
import type { ScreenshotExportState } from "@/lib/screenshot_export";
import { ModelPicker } from "@/components/ModelPicker";
import { VariablePicker } from "@/components/VariablePicker";
import {
  readCapabilityRenderSubstrates,
  type CapabilitiesResponse,
  type RegionPreset,
} from "@/lib/api";
import { useCapabilities } from "@/lib/capabilities-context";
import { buildComparePermalinkSearch, readComparePermalink } from "@/lib/compare-permalink";
import { MAP_VIEW_DEFAULTS } from "@/lib/config";
import { buildPermalinkSearch, replaceUrlQuery } from "@/lib/permalink";
import { useModelLoader } from "@/lib/use-model-loader";
import { useSampleTooltip } from "@/lib/use-sample-tooltip";
import {
  makeModelOptions,
  makeVariableOptions,
  normalizeCapabilityVarRows,
  normalizeModelRows,
  readBasemapModePreference,
  readLegendVisibilityPreference,
  writeBasemapModePreference,
  writeLegendVisibilityPreference,
  type GroupedOption,
  type VariableOption,
} from "@/lib/app-utils";
import { buildRunOptions, pickLatestRunId } from "@/lib/run-options";
import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const DEFAULT_MODEL = "gfs";
const DEFAULT_VARIABLE = "tmp2m";
const DEFAULT_RUN = "latest";

const SPLIT_MIN = 20;
const SPLIT_MAX = 80;
const DEFAULT_SPLIT = 50;

/** Viewport URL writes are throttled by this delay (matches the viewer). */
const URL_SYNC_DEBOUNCE_MS = 200;

const EMPTY_CAPABILITIES: CapabilitiesResponse = {
  contract_version: "",
  supported_models: [],
  model_catalog: {},
  availability: {},
};

function clampSplit(value: number): number {
  if (!Number.isFinite(value)) {
    return DEFAULT_SPLIT;
  }
  return Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, value));
}

/** Default grid variable for a model: its declared default if it is grid-backed, else the first grid variable. */
function defaultGridVariableForModel(capabilities: CapabilitiesResponse, modelId: string): string {
  const modelCapability = capabilities.model_catalog?.[modelId] ?? null;
  const gridVariableIds = normalizeCapabilityVarRows(modelCapability)
    .filter((entry) =>
      readCapabilityRenderSubstrates(modelCapability?.variables?.[entry.id]).includes("grid"),
    )
    .map((entry) => entry.id);
  const defaultVarKey = String(modelCapability?.defaults?.default_var_key ?? "").trim();
  if (defaultVarKey && gridVariableIds.includes(defaultVarKey)) {
    return defaultVarKey;
  }
  return gridVariableIds[0] ?? "";
}

type CompareSelectOption = { value: string; label: string };

function CompareSelect({
  label,
  value,
  onValueChange,
  options,
  placeholder,
  minWidth = "min-w-[128px]",
}: {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  options: CompareSelectOption[];
  placeholder: string;
  minWidth?: string;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="px-1 text-[9px] font-semibold uppercase tracking-[0.18em] text-white/42">
        {label}
      </span>
      <Select value={value} onValueChange={onValueChange} disabled={options.length === 0}>
        <SelectTrigger
          className={`h-8 gap-2 rounded-xl border-white/[0.09] bg-white/[0.05] px-3 text-[12px] font-medium text-white/82 shadow-none transition-all duration-150 hover:border-white/18 hover:bg-white/[0.09] hover:text-white focus:ring-0 ${minWidth}`}
        >
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent className="max-h-72">
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value} className="text-xs">
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );
}

function ComparePanelControls({
  model,
  variable,
  run,
  groupedModelOptions,
  variableCatalog,
  supportedVariableIds,
  runOptions,
  capabilities,
  onModelChange,
  onVariableChange,
  onRunChange,
}: {
  model: string;
  variable: string;
  run: string;
  groupedModelOptions: GroupedOption[];
  variableCatalog: VariableOption[];
  supportedVariableIds: string[];
  runOptions: CompareSelectOption[];
  capabilities: CapabilitiesResponse;
  onModelChange: (value: string) => void;
  onVariableChange: (value: string) => void;
  onRunChange: (value: string) => void;
}) {
  const handleModelChange = (nextModel: string) => {
    if (nextModel === model) {
      return;
    }
    onModelChange(nextModel);
    onVariableChange(defaultGridVariableForModel(capabilities, nextModel));
    onRunChange("latest");
  };

  return (
    <div className="flex min-w-0 flex-wrap items-end gap-1.5">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="px-1 text-[9px] font-semibold uppercase tracking-[0.18em] text-white/42">Product</span>
          <ModelPicker
            value={model}
            onChange={handleModelChange}
            options={groupedModelOptions}
            minWidth="min-w-[130px] max-w-[160px]"
          />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <span className="px-1 text-[9px] font-semibold uppercase tracking-[0.18em] text-white/42">Variable</span>
          <VariablePicker
            modelId={model}
            value={variable}
            onChange={onVariableChange}
            variableCatalog={variableCatalog}
            supportedVariableIds={supportedVariableIds}
            minWidth="min-w-[160px] max-w-[220px]"
          />
        </div>
        <CompareSelect
          label="Run Time"
          value={run}
          onValueChange={onRunChange}
          options={runOptions}
          placeholder="Run"
          minWidth="min-w-[132px]"
        />
    </div>
  );
}

export default function Compare() {
  const { capabilities, regionPresets, error } = useCapabilities();

  // Read the permalink exactly once on mount.
  const initialStateRef = useRef<ReturnType<typeof readComparePermalink> | null>(null);
  if (initialStateRef.current === null) {
    initialStateRef.current = readComparePermalink();
  }
  const initial = initialStateRef.current;

  // Left panel selection.
  const [lModel, setLModel] = useState(initial.lm ?? DEFAULT_MODEL);
  const [lVariable, setLVariable] = useState(initial.lv ?? DEFAULT_VARIABLE);
  const [lRun, setLRun] = useState(initial.lr ?? DEFAULT_RUN);

  // Right panel selection.
  const [rModel, setRModel] = useState(initial.rm ?? DEFAULT_MODEL);
  const [rVariable, setRVariable] = useState(initial.rv ?? DEFAULT_VARIABLE);
  const [rRun, setRRun] = useState(initial.rr ?? DEFAULT_RUN);

  // Shared forecast hour + map viewport.
  const [forecastHour, setForecastHour] = useState(initial.fh ?? 0);
  const [lat, setLat] = useState(initial.lat ?? MAP_VIEW_DEFAULTS.center[0]);
  const [lon, setLon] = useState(initial.lon ?? MAP_VIEW_DEFAULTS.center[1]);
  const [z, setZ] = useState(initial.z ?? MAP_VIEW_DEFAULTS.zoom);

  // Shared region + basemap (not persisted to the URL).
  const [region] = useState("conus");
  const [basemapMode, setBasemapMode] = useState<BasemapMode>(() => readBasemapModePreference());
  const [showLegends, setShowLegends] = useState<boolean>(() => {
    const stored = readLegendVisibilityPreference();
    return stored !== null ? stored : true;
  });

  useEffect(() => { writeBasemapModePreference(basemapMode); }, [basemapMode]);
  useEffect(() => { writeLegendVisibilityPreference(showLegends); }, [showLegends]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsPanelTop, setSettingsPanelTop] = useState(0);
  const settingsRef = useRef<HTMLDivElement>(null);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!settingsOpen) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      const inPanel = settingsRef.current?.contains(target) ?? false;
      const inButton = settingsButtonRef.current?.contains(target) ?? false;
      if (!inPanel && !inButton) setSettingsOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [settingsOpen]);

  const handleSettingsClick = useCallback(() => {
    if (!settingsOpen && settingsButtonRef.current) {
      const rect = settingsButtonRef.current.getBoundingClientRect();
      setSettingsPanelTop(rect.bottom + 8);
    }
    setSettingsOpen(v => !v);
  }, [settingsOpen]);

  // Desktop split position (not persisted to the URL).
  const [splitPercent, setSplitPercent] = useState(() => clampSplit(DEFAULT_SPLIT));
  const [dragPreviewPercent, setDragPreviewPercent] = useState<number | null>(null);
  const dragPreviewPercentRef = useRef<number | null>(null);

  const loaderCapabilities = capabilities ?? EMPTY_CAPABILITIES;
  const loaderRegionPresets = regionPresets ?? {};
  const leftLoader = useModelLoader({
    model: capabilities ? lModel : "",
    run: lRun,
    variable: lVariable,
    region,
    capabilities: loaderCapabilities,
    regionPresets: loaderRegionPresets,
  });
  const rightLoader = useModelLoader({
    model: capabilities ? rModel : "",
    run: rRun,
    variable: rVariable,
    region,
    capabilities: loaderCapabilities,
    regionPresets: loaderRegionPresets,
  });

  const modelOptions = useMemo(() => {
    if (!capabilities) {
      return [];
    }
    const ids = Array.isArray(capabilities.supported_models) && capabilities.supported_models.length > 0
      ? capabilities.supported_models
      : Object.keys(capabilities.model_catalog ?? {});
    return makeModelOptions(normalizeModelRows(capabilities, ids));
  }, [capabilities]);
  const leftRunOptions = useMemo(
    () => buildRunOptions(leftLoader.runs, pickLatestRunId(leftLoader.runs)),
    [leftLoader.runs],
  );
  const rightRunOptions = useMemo(
    () => buildRunOptions(rightLoader.runs, pickLatestRunId(rightLoader.runs)),
    [rightLoader.runs],
  );

  const viewerHref = useMemo(() => {
    const search = buildPermalinkSearch({
      model: lModel,
      var: lVariable,
      run: lRun,
      fh: forecastHour,
      lat,
      lon,
      z,
    });
    return `/viewer${search}`;
  }, [lModel, lVariable, lRun, forecastHour, lat, lon, z]);

  const variableCatalog = useMemo((): VariableOption[] => {
    if (!capabilities) return [];
    const seen = new Set<string>();
    return Object.entries(capabilities.model_catalog ?? {}).flatMap(([modelId, modelCap]) =>
      makeVariableOptions(normalizeCapabilityVarRows(modelCap), modelId)
    ).filter(v => {
      if (seen.has(v.value)) return false;
      seen.add(v.value);
      return true;
    });
  }, [capabilities]);

  // Force the full desktop layout when the page is rendered for a server-side
  // screenshot (?screenshot=1), regardless of the headless viewport width.
  const isScreenshotMode = useMemo(() =>
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("screenshot") === "1"
  , []);

  const leftFrameReadyRef = useRef(false);
  const rightFrameReadyRef = useRef(false);

  const clearCompareReadySignal = useCallback(() => {
    leftFrameReadyRef.current = false;
    rightFrameReadyRef.current = false;
    if (typeof document !== "undefined") {
      document.documentElement.removeAttribute("data-compare-ready");
    }
  }, []);

  const maybeSignalCompareReady = useCallback(() => {
    if (!isScreenshotMode) {
      return;
    }
    if (leftFrameReadyRef.current && rightFrameReadyRef.current) {
      document.documentElement.setAttribute("data-compare-ready", "1");
    }
  }, [isScreenshotMode]);

  const handleLeftFirstFrameReady = useCallback(() => {
    leftFrameReadyRef.current = true;
    maybeSignalCompareReady();
  }, [maybeSignalCompareReady]);

  const handleRightFirstFrameReady = useCallback(() => {
    rightFrameReadyRef.current = true;
    maybeSignalCompareReady();
  }, [maybeSignalCompareReady]);

  useEffect(() => {
    clearCompareReadySignal();
  }, [
    lModel,
    lVariable,
    lRun,
    rModel,
    rVariable,
    rRun,
    forecastHour,
    clearCompareReadySignal,
  ]);

  // Track desktop vs mobile so the split width / divider only apply >= 768px.
  const [isDesktop, setIsDesktop] = useState(() => {
    if (typeof window === "undefined") return true;
    // Force desktop layout when rendering for server-side screenshot
    if (new URLSearchParams(window.location.search).get("screenshot") === "1") return true;
    return window.matchMedia("(min-width: 768px)").matches;
  });
  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const mql = window.matchMedia("(min-width: 768px)");
    const onChange = () => setIsDesktop(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  // Live map instances, captured via onMapReady, used to sync + re-measure.
  const leftMapRef = useRef<MapLibreMap | null>(null);
  const rightMapRef = useRef<MapLibreMap | null>(null);
  const leftMapSyncCleanupRef = useRef<(() => void) | null>(null);
  const rightMapSyncCleanupRef = useRef<(() => void) | null>(null);
  // True while we are programmatically driving one map from the other, so the
  // driven map's move/moveend events don't bounce back and cause a sync loop.
  const isSyncingRef = useRef(false);

  // Mirror the latest selection + forecast hour into a ref so the map event
  // listeners (attached once, on map-ready) can build a fresh permalink
  // without capturing stale state in their closures.
  const selectionStateRef = useRef({ lModel, lVariable, lRun, rModel, rVariable, rRun, forecastHour });
  selectionStateRef.current = { lModel, lVariable, lRun, rModel, rVariable, rRun, forecastHour };

  // Commit the viewport (from whichever map emitted moveend) to state + URL.
  const handleMapMoveEnd = useCallback((map: MapLibreMap) => {
    const center = map.getCenter();
    const nextLat = center.lat;
    const nextLon = center.lng;
    const nextZ = map.getZoom();
    setLat(nextLat);
    setLon(nextLon);
    setZ(nextZ);
    const selection = selectionStateRef.current;
    replaceUrlQuery(
      buildComparePermalinkSearch({
        lm: selection.lModel,
        lv: selection.lVariable,
        lr: selection.lRun,
        rm: selection.rModel,
        rv: selection.rVariable,
        rr: selection.rRun,
        fh: selection.forecastHour,
        lat: nextLat,
        lon: nextLon,
        z: nextZ,
      }),
    );
  }, []);

  // Attach the two-way sync listeners for one map. "move" mirrors this map's
  // camera onto the other (guarded so the mirror doesn't echo back); "moveend"
  // commits the viewport, but only for genuine user gestures (skipped while a
  // programmatic sync is in flight).
  const attachSyncedMapListeners = useCallback(
    (map: MapLibreMap, getOtherMap: () => MapLibreMap | null) => {
      const handleMove = () => {
        if (isSyncingRef.current) {
          return;
        }
        const other = getOtherMap();
        if (!other) {
          return;
        }
        isSyncingRef.current = true;
        other.jumpTo({
          center: map.getCenter(),
          zoom: map.getZoom(),
          bearing: map.getBearing(),
          pitch: map.getPitch(),
        });
        isSyncingRef.current = false;
      };
      const handleMoveEnd = () => {
        if (isSyncingRef.current) {
          return;
        }
        handleMapMoveEnd(map);
      };
      map.on("move", handleMove);
      map.on("moveend", handleMoveEnd);
      return () => {
        map.off("move", handleMove);
        map.off("moveend", handleMoveEnd);
      };
    },
    [handleMapMoveEnd],
  );

  const handleLeftMapReady = useCallback(
    (map: MapLibreMap) => {
      leftMapSyncCleanupRef.current?.();
      leftMapRef.current = map;
      leftMapSyncCleanupRef.current = attachSyncedMapListeners(map, () => rightMapRef.current);
    },
    [attachSyncedMapListeners],
  );
  const handleRightMapReady = useCallback(
    (map: MapLibreMap) => {
      rightMapSyncCleanupRef.current?.();
      rightMapRef.current = map;
      rightMapSyncCleanupRef.current = attachSyncedMapListeners(map, () => leftMapRef.current);
    },
    [attachSyncedMapListeners],
  );

  useEffect(() => {
    return () => {
      leftMapSyncCleanupRef.current?.();
      leftMapSyncCleanupRef.current = null;
      rightMapSyncCleanupRef.current?.();
      rightMapSyncCleanupRef.current = null;
    };
  }, []);

  const handleSwap = useCallback(() => {
    setLModel(rModel);
    setLVariable(rVariable);
    setLRun(rRun);
    setRModel(lModel);
    setRVariable(lVariable);
    setRRun(lRun);
  }, [lModel, lVariable, lRun, rModel, rVariable, rRun]);

  // ── Share to TWF ───────────────────────────────────────────────────────
  // The screenshot is produced server-side: TwfShareModal POSTs the /compare
  // permalink to the share-screenshot endpoint (VITE_SERVER_SCREENSHOT=true),
  // which renders both panels natively. We only supply the permalink, a
  // summary, and a minimal ScreenshotExportState for the URL params + overlay.
  const [shareOpen, setShareOpen] = useState(false);

  const sharePermalink = useMemo(() => {
    const search = buildComparePermalinkSearch({
      lm: lModel, lv: lVariable, lr: lRun,
      rm: rModel, rv: rVariable, rr: rRun,
      fh: forecastHour, lat, lon, z,
    });
    return `${window.location.origin}/compare${search}`;
  }, [lModel, lVariable, lRun, rModel, rVariable, rRun, forecastHour, lat, lon, z]);

  const sharePayload = useMemo<SharePayload>(() => {
    const leftVarLabel = variableCatalog.find(v => v.value === lVariable)?.label ?? lVariable;
    const rightVarLabel = variableCatalog.find(v => v.value === rVariable)?.label ?? rVariable;
    return {
      permalink: sharePermalink,
      summary: `${lModel.toUpperCase()} ${leftVarLabel} vs ${rModel.toUpperCase()} ${rightVarLabel} • FH ${Math.round(forecastHour)}`,
    };
  }, [sharePermalink, lModel, lVariable, rModel, rVariable, forecastHour, variableCatalog]);

  const buildShareScreenshotState = useCallback((): ScreenshotExportState | null => {
    const leftVarLabel = variableCatalog.find(v => v.value === lVariable)?.label ?? lVariable;
    const rightVarLabel = variableCatalog.find(v => v.value === rVariable)?.label ?? rVariable;
    return {
      style: {},
      center: [lon, lat],
      zoom: z,
      basemapMode,
      isMobile: false,
      model: `${lModel.toUpperCase()} vs ${rModel.toUpperCase()}`,
      run: leftLoader.resolvedRun,
      variable: { key: lVariable, label: `${leftVarLabel} vs ${rightVarLabel}` },
      fh: forecastHour,
      gridReady: true,
      region: { id: region, label: region },
      animationEnabled: false,
    };
  }, [lon, lat, z, basemapMode, lModel, rModel, lVariable, rVariable, leftLoader.resolvedRun, forecastHour, region, variableCatalog]);

  const handleShare = useCallback(() => {
    setShareOpen(true);
  }, []);

  // ── Side-by-side hover value sampling ──────────────────────────────────
  const [hoverSide, setHoverSide] = useState<"left" | "right" | null>(null);
  const [hoverX, setHoverX] = useState(0);
  const [hoverY, setHoverY] = useState(0);
  const [hoverContainerWidth, setHoverContainerWidth] = useState(0);

  const leftPanelRef = useRef<HTMLDivElement | null>(null);
  const rightPanelRef = useRef<HTMLDivElement | null>(null);

  const { tooltip: leftTooltip, onHover: onLeftHover, onHoverEnd: onLeftHoverEnd } = useSampleTooltip({
    model: lModel,
    run: leftLoader.resolvedRun,
    varId: lVariable,
    fh: forecastHour,
  });

  const { tooltip: rightTooltip, onHover: onRightHover, onHoverEnd: onRightHoverEnd } = useSampleTooltip({
    model: rModel,
    run: rightLoader.resolvedRun,
    varId: rVariable,
    fh: forecastHour,
  });

  const handleLeftHover = useCallback((lat: number, lon: number, x: number, y: number) => {
    setHoverSide("left");
    setHoverX(x);
    setHoverY(y);
    setHoverContainerWidth(leftPanelRef.current?.offsetWidth ?? 0);
    onLeftHover(lat, lon, x, y);
    // Also sample the right panel at the same lat/lon
    onRightHover(lat, lon, x, y);
  }, [onLeftHover, onRightHover]);

  const handleRightHover = useCallback((lat: number, lon: number, x: number, y: number) => {
    setHoverSide("right");
    setHoverX(x);
    setHoverY(y);
    setHoverContainerWidth(rightPanelRef.current?.offsetWidth ?? 0);
    onRightHover(lat, lon, x, y);
    // Also sample the left panel at the same lat/lon
    onLeftHover(lat, lon, x, y);
  }, [onLeftHover, onRightHover]);

  const handleHoverEnd = useCallback(() => {
    setHoverSide(null);
    onLeftHoverEnd();
    onRightHoverEnd();
  }, [onLeftHoverEnd, onRightHoverEnd]);

  // Persist selection + forecast hour to the URL (debounced). Viewport changes
  // are written immediately by handleMapMoveEnd, so they are not tracked here;
  // the current lat/lon/z are still included so a selection change preserves
  // the viewport. splitPercent is intentionally excluded from the permalink.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      const search = buildComparePermalinkSearch({
        lm: lModel,
        lv: lVariable,
        lr: lRun,
        rm: rModel,
        rv: rVariable,
        rr: rRun,
        fh: forecastHour,
        lat,
        lon,
        z,
      });
      replaceUrlQuery(search);
    }, URL_SYNC_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [lModel, lVariable, lRun, rModel, rVariable, rRun, forecastHour]);

  const containerRef = useRef<HTMLDivElement | null>(null);

  const handleDividerMouseDown = useCallback((event: ReactMouseEvent) => {
    event.preventDefault();
    dragPreviewPercentRef.current = splitPercent;
    setDragPreviewPercent(splitPercent);
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const handleMouseMove = (moveEvent: globalThis.MouseEvent) => {
      const container = containerRef.current;
      if (!container) {
        return;
      }
      const rect = container.getBoundingClientRect();
      if (rect.width <= 0) {
        return;
      }
      const ratio = ((moveEvent.clientX - rect.left) / rect.width) * 100;
      const nextPreview = clampSplit(ratio);
      dragPreviewPercentRef.current = nextPreview;
      setDragPreviewPercent(nextPreview);
    };

    const handleMouseUp = () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      const nextSplit = dragPreviewPercentRef.current ?? splitPercent;
      dragPreviewPercentRef.current = null;
      setDragPreviewPercent(null);
      setSplitPercent(nextSplit);
      window.requestAnimationFrame(() => {
        leftMapRef.current?.resize();
        rightMapRef.current?.resize();
        window.requestAnimationFrame(() => {
          leftMapRef.current?.resize();
          rightMapRef.current?.resize();
        });
      });
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
  }, [splitPercent]);

  if (error) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-background text-foreground">
        <div className="text-center text-muted-foreground px-6">
          <p className="text-sm font-medium text-foreground">Failed to load comparison data</p>
          <p className="text-xs mt-1">{error.message}</p>
        </div>
      </div>
    );
  }

  if (!capabilities || !regionPresets) {
    return <div className="w-full h-full flex items-center justify-center text-muted-foreground text-sm">Loading…</div>;
  }

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      <div
        className="relative z-10 shrink-0 border-b border-white/[0.08] bg-[#04101e]/[0.92] shadow-[0_10px_28px_rgba(0,0,0,0.24)] backdrop-blur-md"
        style={{ paddingTop: "4rem" }}
      >
        {/* Desktop: three columns tracking the map split exactly */}
        <div
          className={isScreenshotMode ? "grid" : "hidden xl:grid"}
          style={{ gridTemplateColumns: `${splitPercent}% 12px 1fr` }}
        >
          {/* Left panel controls */}
          <div className="flex flex-col px-4 pb-2">
            <div className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-cyan-200/70">
              Left Panel
            </div>
            <ComparePanelControls
              model={lModel}
              variable={lVariable}
              run={lRun}
              groupedModelOptions={modelOptions}
              variableCatalog={variableCatalog}
              supportedVariableIds={leftLoader.variables.map(v => v.value)}
              runOptions={leftRunOptions}
              capabilities={capabilities}
              onModelChange={setLModel}
              onVariableChange={setLVariable}
              onRunChange={setLRun}
            />
          </div>

          {/* Center column — spacer aligning with the map divider */}
          <div />

          {/* Right panel controls + action buttons */}
          <div className="flex flex-col px-4 pb-2">
            <div className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-cyan-200/70">
              Right Panel
            </div>
            <div className="flex items-end gap-1.5">
            <ComparePanelControls
              model={rModel}
              variable={rVariable}
              run={rRun}
              groupedModelOptions={modelOptions}
              variableCatalog={variableCatalog}
              supportedVariableIds={rightLoader.variables.map(v => v.value)}
              runOptions={rightRunOptions}
              capabilities={capabilities}
              onModelChange={setRModel}
              onVariableChange={setRVariable}
              onRunChange={setRRun}
            />
            <div className="ml-auto flex shrink-0 items-end gap-2 pb-0.5">
              <Link
                to={viewerHref}
                className="flex h-8 items-center gap-1.5 rounded-lg border border-white/[0.09] bg-white/[0.05] px-3 text-[11px] font-medium text-white/60 transition-all hover:border-white/18 hover:bg-white/[0.09] hover:text-white"
                aria-label="Open current view in Viewer"
                title="Open in Viewer"
              >
                <ArrowLeft className="h-3 w-3 shrink-0" />
                <span>Viewer</span>
              </Link>
              <button
                type="button"
                onClick={handleShare}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.05] text-white/50 transition-all hover:border-white/20 hover:bg-white/[0.09] hover:text-white"
                aria-label="Share to TWF"
                title="Share to TWF"
              >
                <Share2 className="h-3.5 w-3.5" />
              </button>
              <button
                ref={settingsButtonRef}
                type="button"
                onClick={handleSettingsClick}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.05] text-white/50 transition-all hover:border-white/20 hover:bg-white/[0.09] hover:text-white"
                aria-label="Display settings"
                title="Display settings"
              >
                <Settings className="h-3.5 w-3.5" />
              </button>
            </div>
            </div>
          </div>
        </div>

        {/* Mobile fallback: stacked controls */}
        <div className="flex xl:hidden flex-col gap-3 px-4 pb-2">
          <ComparePanelControls
            model={lModel}
            variable={lVariable}
            run={lRun}
            groupedModelOptions={modelOptions}
            variableCatalog={variableCatalog}
            supportedVariableIds={leftLoader.variables.map(v => v.value)}
            runOptions={leftRunOptions}
            capabilities={capabilities}
            onModelChange={setLModel}
            onVariableChange={setLVariable}
            onRunChange={setLRun}
          />
          <ComparePanelControls
            model={rModel}
            variable={rVariable}
            run={rRun}
            groupedModelOptions={modelOptions}
            variableCatalog={variableCatalog}
            supportedVariableIds={rightLoader.variables.map(v => v.value)}
            runOptions={rightRunOptions}
            capabilities={capabilities}
            onModelChange={setRModel}
            onVariableChange={setRVariable}
            onRunChange={setRRun}
          />
        </div>
      </div>

      <div
        ref={containerRef}
        className={`relative min-h-0 flex-1 flex ${isDesktop ? "flex-row" : "flex-col"} overflow-hidden`}
      >
        <div
          ref={leftPanelRef}
          className={`relative min-h-0 min-w-0 ${isDesktop ? "shrink-0" : "flex-1"}`}
          style={isDesktop ? { width: `${splitPercent}%` } : undefined}
        >
          <ComparePanel
            side="left"
            model={lModel}
            variable={lVariable}
            region={region}
            basemapMode={basemapMode}
            showLegend={showLegends}
            onMapReady={handleLeftMapReady}
            onFirstFrameReady={handleLeftFirstFrameReady}
            onMapHover={handleLeftHover}
            onMapHoverEnd={handleHoverEnd}
            resolvedRun={leftLoader.resolvedRun}
            gridManifest={leftLoader.gridManifest}
            gridFrameHours={leftLoader.gridFrameHours}
            gridFrameByHour={leftLoader.gridFrameByHour}
            frameRows={leftLoader.frameRows}
            frameHours={leftLoader.frameHours}
            prefersGridSubstrate={leftLoader.prefersGridSubstrate}
            forecastHour={forecastHour}
            loading={leftLoader.loading}
            error={leftLoader.error}
          />
          {hoverSide === "left" && (
            <CompareTooltip
              leftTooltip={leftTooltip}
              rightTooltip={rightTooltip}
              x={hoverX}
              y={hoverY}
              containerWidth={hoverContainerWidth}
              side="left"
            />
          )}
        </div>

        {isDesktop ? (
          <div
            role="separator"
            aria-orientation="vertical"
            onMouseDown={handleDividerMouseDown}
            className="group relative z-20 w-3 shrink-0 cursor-col-resize bg-[#07111f] before:absolute before:inset-y-0 before:left-1/2 before:w-px before:-translate-x-1/2 before:bg-white/18 before:transition-colors hover:before:bg-cyan-300/70"
          >
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); handleSwap(); }}
              onMouseDown={(e) => e.stopPropagation()}
              className="absolute top-3 left-1/2 -translate-x-1/2 z-30 flex h-7 w-7 items-center justify-center rounded-lg border border-white/[0.14] bg-[#07111f] text-white/50 transition-all hover:border-white/30 hover:text-white shadow-[0_2px_8px_rgba(0,0,0,0.5)]"
              aria-label="Swap left and right panels"
              title="Swap panels"
            >
              <ArrowLeftRight className="h-3.5 w-3.5" />
            </button>
            <div className="pointer-events-none absolute top-1/2 left-1/2 z-10 -translate-x-1/2 -translate-y-1/2 flex flex-col items-center gap-[3px]">
              <div className="h-4 w-[3px] rounded-full bg-white/20 transition-colors group-hover:bg-cyan-300/90" />
              <div className="h-4 w-[3px] rounded-full bg-white/20 transition-colors group-hover:bg-cyan-300/90" />
            </div>
          </div>
        ) : null}

        {isDesktop && dragPreviewPercent !== null ? (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 z-40 w-1 -translate-x-1/2 rounded-full bg-cyan-300 shadow-[0_0_16px_rgba(103,232,249,0.7)]"
            style={{ left: `${dragPreviewPercent}%` }}
          />
        ) : null}

        <div ref={rightPanelRef} className="relative min-h-0 min-w-0 flex-1">
          <ComparePanel
            side="right"
            model={rModel}
            variable={rVariable}
            region={region}
            basemapMode={basemapMode}
            showLegend={showLegends}
            onMapReady={handleRightMapReady}
            onFirstFrameReady={handleRightFirstFrameReady}
            onMapHover={handleRightHover}
            onMapHoverEnd={handleHoverEnd}
            resolvedRun={rightLoader.resolvedRun}
            gridManifest={rightLoader.gridManifest}
            gridFrameHours={rightLoader.gridFrameHours}
            gridFrameByHour={rightLoader.gridFrameByHour}
            frameRows={rightLoader.frameRows}
            frameHours={rightLoader.frameHours}
            prefersGridSubstrate={rightLoader.prefersGridSubstrate}
            forecastHour={forecastHour}
            loading={rightLoader.loading}
            error={rightLoader.error}
          />
          {hoverSide === "right" && (
            <CompareTooltip
              leftTooltip={leftTooltip}
              rightTooltip={rightTooltip}
              x={hoverX}
              y={hoverY}
              containerWidth={hoverContainerWidth}
              side="right"
            />
          )}
        </div>
        <CompareScrubber
          leftFrameHours={leftLoader.frameHours}
          rightFrameHours={rightLoader.frameHours}
          forecastHour={forecastHour}
          onForecastHourChange={setForecastHour}
          leftResolvedRun={leftLoader.resolvedRun}
          rightResolvedRun={rightLoader.resolvedRun}
        />
      </div>

      {settingsOpen ? createPortal(
        <div
          ref={settingsRef}
          className="fixed right-4 z-[70] w-[232px] overflow-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md"
          style={{ top: settingsPanelTop }}
        >
          <div className="flex items-center justify-between border-b border-[#1a3a5c]/50 px-4 py-3">
            <div>
              <div className="font-['IBM_Plex_Mono',monospace] text-[9px] font-medium uppercase tracking-[0.22em] text-cyan-300/60">
                Display
              </div>
              <div className="mt-0.5 text-[11px] text-white/52">Map overlays &amp; reference aids</div>
            </div>
            <button
              type="button"
              onClick={() => setSettingsOpen(false)}
              className="inline-flex h-6 w-6 items-center justify-center rounded-md text-white/32 transition-colors hover:text-white/72"
              aria-label="Close display panel"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>

          <div className="space-y-1.5 px-3 py-3">
            <button
              type="button"
              onClick={() => setBasemapMode(prev => prev === "dark" ? "light" : "dark")}
              className="flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-left transition-all duration-150 hover:bg-white/[0.07]"
            >
              <div className="flex items-center gap-2 text-sm font-semibold text-white">
                {basemapMode === "dark"
                  ? <Moon className="h-4 w-4 text-white/60" />
                  : <Sun className="h-4 w-4 text-white/60" />}
                Basemap
              </div>
              <span className="font-['IBM_Plex_Mono',monospace] text-[10px] font-medium text-cyan-300/80">
                {basemapMode === "dark" ? "Dark" : "Light"}
              </span>
            </button>

            <button
              type="button"
              onClick={() => setShowLegends(v => !v)}
              aria-pressed={showLegends}
              className={cn(
                "flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition-all duration-150",
                showLegends
                  ? "border-cyan-300/20 bg-cyan-300/[0.07] hover:bg-cyan-300/[0.11]"
                  : "border-white/10 bg-white/[0.04] hover:bg-white/[0.07]"
              )}
            >
              <div className="flex items-center gap-2 text-sm font-semibold text-white">
                <Layers className="h-4 w-4 text-white/72" />
                Legends
              </div>
              <span className={cn("font-['IBM_Plex_Mono',monospace] text-[10px] font-medium", showLegends ? "text-cyan-300/90" : "text-white/38")}>
                {showLegends ? "On" : "Off"}
              </span>
            </button>

          </div>
        </div>
      , document.body) : null}

      {shareOpen ? (
        <TwfShareModal
          open={shareOpen}
          onClose={() => setShareOpen(false)}
          payload={sharePayload}
          buildScreenshotState={buildShareScreenshotState}
        />
      ) : null}
    </div>
  );
}
