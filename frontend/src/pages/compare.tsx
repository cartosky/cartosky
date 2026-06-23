import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, ArrowLeftRight, Layers, Moon, Settings, Share2, Sun, X } from "lucide-react";
import type { Map as MapLibreMap } from "maplibre-gl";

import { Link } from "react-router-dom";
import ComparePanel from "@/components/compare/ComparePanel";
import CompareScrubber, { deriveValidTime } from "@/components/compare/CompareScrubber";
import CompareDiffPanel from "@/components/compare/CompareDiffPanel";
import CompareModeToggle, { type CompareMode } from "@/components/compare/CompareModeToggle";
import CompareMobileDiffBar from "@/components/compare/CompareMobileDiffBar";
import CompareMobileDrawer from "@/components/compare/CompareMobileDrawer";
import { CompareTooltip } from "@/components/compare/CompareTooltip";
import { TwfShareModal, type SharePayload } from "@/components/twf-share-modal";
import type { BasemapMode } from "@/components/map-canvas";
import type { ScreenshotExportState } from "@/lib/screenshot_export";
import { ModelPicker } from "@/components/ModelPicker";
import { VariablePicker } from "@/components/VariablePicker";
import {
  readCapabilityRenderSubstrates,
  type CapabilitiesResponse,
  type GridManifestResponse,
  type RegionPreset,
} from "@/lib/api";
import { useCapabilities } from "@/lib/capabilities-context";
import { buildComparePermalinkSearch, readComparePermalink } from "@/lib/compare-permalink";
import { mutualDiffEligibleVariables } from "@/lib/compare-diff-eligibility";
import { useCompareDiff } from "@/lib/use-compare-diff";
import type { GridMeta } from "@/lib/compare-diff";
import { selectGridManifestLod } from "@/lib/grid-lod";
import { API_ORIGIN, MAP_VIEW_DEFAULTS } from "@/lib/config";
import { buildPermalinkSearch, replaceUrlQuery } from "@/lib/permalink";
import { useModelLoader, type UseModelLoaderResult } from "@/lib/use-model-loader";
import { useSampleTooltip } from "@/lib/use-sample-tooltip";
import { useViewerLayoutMode } from "@/lib/viewer-layout";
import {
  makeModelOptions,
  makeVariableOptions,
  nearestFrame,
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

/** sessionStorage flag so the diff auto-correction notice shows once per session. */
const DIFF_AUTOCORRECT_NOTICE_FLAG = "compare-diff-autocorrect-notice";

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

/** Same base-URL resolution ComparePanel applies to grid frame URLs. */
const API_ROOT = API_ORIGIN.replace(/\/$/, "");

function clampSplit(value: number): number {
  if (!Number.isFinite(value)) {
    return DEFAULT_SPLIT;
  }
  return Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, value));
}

/** Apply ComparePanel's base-URL resolution to a (possibly relative) grid frame URL. */
function toAbsoluteFrameUrl(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `${API_ROOT}${url.startsWith("/") ? "" : "/"}${url}`;
}

/** Resolve the active grid frame URL for a loader at a forecast hour (mirrors ComparePanel). */
function resolveActiveGridFrameUrl(loader: UseModelLoaderResult, forecastHour: number): string | null {
  const hours = loader.gridFrameHours;
  if (hours.length === 0) {
    return null;
  }
  const hour = nearestFrame(hours, forecastHour);
  const url = loader.gridFrameByHour.get(hour)?.url;
  if (!url) {
    return null;
  }
  return toAbsoluteFrameUrl(url);
}

/**
 * Resolve the grid frame URLs for the forecast hours immediately adjacent to the
 * active one (previous + next), for adjacent-frame prefetch. Returns absolute
 * URLs matching {@link resolveActiveGridFrameUrl}, so the prefetched bytes hit
 * the same GridFrameCache key the compute later reads.
 */
function resolveAdjacentGridFrameUrls(loader: UseModelLoaderResult, forecastHour: number): string[] {
  const hours = loader.gridFrameHours;
  if (hours.length === 0) {
    return [];
  }
  const activeIndex = hours.indexOf(nearestFrame(hours, forecastHour));
  if (activeIndex < 0) {
    return [];
  }
  const urls: string[] = [];
  for (const neighborIndex of [activeIndex - 1, activeIndex + 1]) {
    if (neighborIndex < 0 || neighborIndex >= hours.length) {
      continue;
    }
    const url = loader.gridFrameByHour.get(hours[neighborIndex])?.url;
    if (url) {
      urls.push(toAbsoluteFrameUrl(url));
    }
  }
  return urls;
}

/** Build the diff GridMeta from a loader's grid manifest (level-zero LOD). bbox is EPSG:3857 meters. */
function resolveGridMeta(manifest: GridManifestResponse | null): GridMeta | null {
  if (!manifest || !Array.isArray(manifest.bbox) || manifest.bbox.length !== 4) {
    return null;
  }
  const lod = selectGridManifestLod(manifest, null);
  if (!lod) {
    return null;
  }
  const grid = manifest.grid;
  return {
    width: Math.max(1, Math.floor(Number(lod.width) || 1)),
    height: Math.max(1, Math.floor(Number(lod.height) || 1)),
    bbox: manifest.bbox as [number, number, number, number],
    dtype: String(grid?.dtype ?? "").trim().toLowerCase() === "uint8" ? "uint8" : "uint16",
    scale: Number(grid?.scale) || 1,
    offset: Number(grid?.offset) || 0,
    nodata: Number(grid?.nodata) || 65535,
    units: typeof grid?.units === "string" ? grid.units : undefined,
  };
}

/** Parse a resolved run id ("YYYYMMDD_HHz") into compact display parts. */
function parseRunParts(resolvedRun: string): { hour: string; ymd: string; date: string } | null {
  const match = resolvedRun.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})z$/i);
  if (!match) {
    return null;
  }
  const [, year, month, day, hour] = match;
  return { hour: `${hour}Z`, ymd: `${year}${month}${day}`, date: `${Number(month)}/${Number(day)}` };
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

function ControlLabel({ children }: { children: ReactNode }) {
  return (
    <span className="px-1 text-[9px] font-semibold uppercase tracking-[0.18em] text-white/42">
      {children}
    </span>
  );
}

/**
 * Difference-mode control bar: collapses the split layout to a single row of
 * LEFT MODEL · RIGHT MODEL · VARIABLE (shared) · L-RUN · R-RUN, plus the mode
 * toggle and action buttons. Wraps on narrow viewports. The shared variable
 * picker is restricted to mutually diff-eligible variables.
 */
function DiffControlBar({
  lModel,
  rModel,
  sharedVariable,
  lRun,
  rRun,
  mode,
  modelOptions,
  variableCatalog,
  diffMutualVariables,
  leftRunOptions,
  rightRunOptions,
  viewerHref,
  diffNotice,
  settingsButtonRef,
  onModeChange,
  onLeftModelChange,
  onRightModelChange,
  onSharedVariableChange,
  onLeftRunChange,
  onRightRunChange,
  onSwap,
  onShare,
  onSettingsClick,
  onDismissNotice,
}: {
  lModel: string;
  rModel: string;
  sharedVariable: string;
  lRun: string;
  rRun: string;
  mode: CompareMode;
  modelOptions: GroupedOption[];
  variableCatalog: VariableOption[];
  diffMutualVariables: string[];
  leftRunOptions: CompareSelectOption[];
  rightRunOptions: CompareSelectOption[];
  viewerHref: string;
  diffNotice: string | null;
  settingsButtonRef: RefObject<HTMLButtonElement | null>;
  onModeChange: (mode: CompareMode) => void;
  onLeftModelChange: (value: string) => void;
  onRightModelChange: (value: string) => void;
  onSharedVariableChange: (value: string) => void;
  onLeftRunChange: (value: string) => void;
  onRightRunChange: (value: string) => void;
  onSwap: () => void;
  onShare: () => void;
  onSettingsClick: () => void;
  onDismissNotice: () => void;
}) {
  const variablesDisabled = diffMutualVariables.length === 0;
  return (
    <div className="px-4 pb-2">
      <div className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-cyan-200/70">
        Difference
      </div>
      <div className="flex flex-wrap items-end gap-x-2 gap-y-2 sm:gap-x-4">
        <div className="flex min-w-0 flex-col gap-1">
          <ControlLabel>Left Model</ControlLabel>
          <ModelPicker
            value={lModel}
            onChange={onLeftModelChange}
            options={modelOptions}
            minWidth="min-w-[130px] max-w-[160px]"
          />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <ControlLabel>Right Model</ControlLabel>
          <ModelPicker
            value={rModel}
            onChange={onRightModelChange}
            options={modelOptions}
            minWidth="min-w-[130px] max-w-[160px]"
          />
        </div>
        <button
          type="button"
          onClick={onSwap}
          className="mb-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-white/[0.14] bg-[#07111f] text-white/50 shadow-[0_2px_8px_rgba(0,0,0,0.5)] transition-all hover:border-white/30 hover:text-white"
          aria-label="Swap left and right panels"
          title="Swap panels"
        >
          <ArrowLeftRight className="h-3.5 w-3.5" />
        </button>
        <div className="flex min-w-0 flex-col gap-1">
          <ControlLabel>Variable (shared)</ControlLabel>
          <VariablePicker
            modelId={lModel}
            value={sharedVariable}
            onChange={onSharedVariableChange}
            variableCatalog={variableCatalog}
            supportedVariableIds={diffMutualVariables}
            disabled={variablesDisabled}
            placeholder={variablesDisabled ? "No shared variable" : "Variable"}
            minWidth="min-w-[160px] max-w-[220px]"
          />
        </div>
        <CompareSelect
          label="L Run"
          value={lRun}
          onValueChange={onLeftRunChange}
          options={leftRunOptions}
          placeholder="Run"
          minWidth="min-w-[132px]"
        />
        <CompareSelect
          label="R Run"
          value={rRun}
          onValueChange={onRightRunChange}
          options={rightRunOptions}
          placeholder="Run"
          minWidth="min-w-[132px]"
        />
        <div className="flex flex-wrap items-center gap-2 pb-0.5 sm:ml-auto sm:flex-nowrap">
          <CompareModeToggle mode={mode} onChange={onModeChange} />
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
            onClick={onShare}
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.05] text-white/50 transition-all hover:border-white/20 hover:bg-white/[0.09] hover:text-white"
            aria-label="Share to TWF"
            title="Share to TWF"
          >
            <Share2 className="h-3.5 w-3.5" />
          </button>
          <button
            ref={settingsButtonRef}
            type="button"
            onClick={onSettingsClick}
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.05] text-white/50 transition-all hover:border-white/20 hover:bg-white/[0.09] hover:text-white"
            aria-label="Display settings"
            title="Display settings"
          >
            <Settings className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      {diffNotice ? (
        <div className="mt-2 flex items-start gap-2 rounded-lg border border-cyan-300/20 bg-cyan-300/[0.06] px-3 py-2 text-[11px] font-medium text-cyan-100/90">
          <span className="min-w-0 flex-1">{diffNotice}</span>
          <button
            type="button"
            onClick={onDismissNotice}
            className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-cyan-100/50 transition-colors hover:bg-white/[0.08] hover:text-cyan-50"
            aria-label="Dismiss notice"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : null}
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

  // Compare mode (split = side-by-side, diff = difference). Driven by permalink.
  const [mode, setMode] = useState<CompareMode>(initial.mode === "diff" ? "diff" : "split");
  // Inline notice shown once per session when entering diff auto-corrects the variable.
  const [diffNotice, setDiffNotice] = useState<string | null>(null);
  const diffNoticeShownRef = useRef<boolean>(
    typeof window !== "undefined" &&
      window.sessionStorage.getItem(DIFF_AUTOCORRECT_NOTICE_FLAG) === "1",
  );

  // Phone layout (≤639px) swaps the diff control rows for a summary bar + drawer.
  const layoutMode = useViewerLayoutMode();
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [mobileDrawerTab, setMobileDrawerTab] = useState<"comparison" | "display">("comparison");

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

  // ── Difference mode: shared eligible variable ──────────────────────────
  // var_keys usable in diff mode for the current model pair (intersection of
  // both models' grid variables ∩ the v1 diff-eligible allowlist).
  const diffMutualVariables = useMemo(
    () => (capabilities ? mutualDiffEligibleVariables(lModel, rModel, capabilities) : []),
    [capabilities, lModel, rModel],
  );

  const showDiffAutocorrectNotice = useCallback((message: string) => {
    if (diffNoticeShownRef.current) {
      return;
    }
    diffNoticeShownRef.current = true;
    try {
      window.sessionStorage.setItem(DIFF_AUTOCORRECT_NOTICE_FLAG, "1");
    } catch {
      // sessionStorage may be unavailable (private mode); the ref still gates it.
    }
    setDiffNotice(message);
  }, []);

  // Keep the shared variable valid + in sync while in diff mode (covers initial
  // permalink load and model changes). Silent — the once-per-session notice is
  // raised only by the explicit split→diff toggle in handleModeChange.
  useEffect(() => {
    if (mode !== "diff" || !capabilities) {
      return;
    }
    if (diffMutualVariables.length === 0) {
      return; // No mutual eligible variable — diff panel shows the blocking state.
    }
    const next = diffMutualVariables.includes(lVariable) ? lVariable : diffMutualVariables[0];
    if (next !== lVariable) {
      setLVariable(next);
    }
    if (next !== rVariable) {
      setRVariable(next);
    }
  }, [mode, capabilities, diffMutualVariables, lVariable, rVariable]);

  const handleModeChange = useCallback((nextMode: CompareMode) => {
    if (nextMode === mode) {
      return;
    }
    if (nextMode === "split") {
      setDiffNotice(null);
      setMode("split");
      return;
    }
    // Entering diff mode: auto-correct the variable if it is not mutually
    // eligible, and surface the one-per-session inline notice. The actual
    // variable assignment is handled by the reconcile effect above.
    const mutual = capabilities ? mutualDiffEligibleVariables(lModel, rModel, capabilities) : [];
    if (mutual.length > 0 && !mutual.includes(lVariable)) {
      const next = mutual[0];
      const label = variableCatalog.find((v) => v.value === next)?.label ?? next;
      showDiffAutocorrectNotice(
        `Variable changed to "${label}" — difference mode only compares continuous fields.`,
      );
    }
    setMode("diff");
  }, [mode, capabilities, lModel, rModel, lVariable, variableCatalog, showDiffAutocorrectNotice]);

  // In diff mode the variable picker is shared: changing it sets both sides.
  const handleSharedVariableChange = useCallback((value: string) => {
    setLVariable(value);
    setRVariable(value);
  }, []);

  // Diff-mode model changes reset that side's run; the reconcile effect keeps
  // the shared variable valid for the new pair (no notice — silent).
  const handleDiffLeftModelChange = useCallback((nextModel: string) => {
    if (nextModel === lModel) {
      return;
    }
    setLModel(nextModel);
    setLRun("latest");
  }, [lModel]);

  const handleDiffRightModelChange = useCallback((nextModel: string) => {
    if (nextModel === rModel) {
      return;
    }
    setRModel(nextModel);
    setRRun("latest");
  }, [rModel]);

  // ── Difference pipeline (orchestrated by useCompareDiff) ───────────────
  const leftDiffFrameUrl = useMemo(
    () => resolveActiveGridFrameUrl(leftLoader, forecastHour),
    [leftLoader.gridFrameHours, leftLoader.gridFrameByHour, forecastHour],
  );
  const rightDiffFrameUrl = useMemo(
    () => resolveActiveGridFrameUrl(rightLoader, forecastHour),
    [rightLoader.gridFrameHours, rightLoader.gridFrameByHour, forecastHour],
  );
  const leftGridMeta = useMemo(() => resolveGridMeta(leftLoader.gridManifest), [leftLoader.gridManifest]);
  const rightGridMeta = useMemo(() => resolveGridMeta(rightLoader.gridManifest), [rightLoader.gridManifest]);

  // Adjacent-hour frame URLs warmed into GridFrameCache after each diff settles,
  // so sequential scrubbing finds bytes already cached (no loading flash).
  const leftPrefetchUrls = useMemo(
    () => resolveAdjacentGridFrameUrls(leftLoader, forecastHour),
    [leftLoader.gridFrameHours, leftLoader.gridFrameByHour, forecastHour],
  );
  const rightPrefetchUrls = useMemo(
    () => resolveAdjacentGridFrameUrls(rightLoader, forecastHour),
    [rightLoader.gridFrameHours, rightLoader.gridFrameByHour, forecastHour],
  );

  const diff = useCompareDiff({
    leftFrameUrl: leftDiffFrameUrl,
    rightFrameUrl: rightDiffFrameUrl,
    leftGridMeta,
    rightGridMeta,
    leftModel: lModel,
    rightModel: rModel,
    varKey: mode === "diff" ? lVariable : null,
    enabled: mode === "diff",
    leftPrefetchUrls,
    rightPrefetchUrls,
  });

  // Readiness gate step 4: the diff MapCanvas has rendered + gone idle.
  const [diffMapReady, setDiffMapReady] = useState(false);

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
    setDiffMapReady(false);
    if (typeof document !== "undefined") {
      document.documentElement.removeAttribute("data-compare-ready");
    }
  }, []);

  // Split-mode gate: both panels' first frames ready. (No-op in diff mode — the
  // diff four-step gate is handled by the effect below.)
  const maybeSignalCompareReady = useCallback(() => {
    if (!isScreenshotMode || mode === "diff") {
      return;
    }
    if (leftFrameReadyRef.current && rightFrameReadyRef.current) {
      document.documentElement.setAttribute("data-compare-ready", "1");
    }
  }, [isScreenshotMode, mode]);

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
    mode,
    clearCompareReadySignal,
  ]);

  // Diff-mode four-step readiness gate: left fetched, right fetched, compute
  // done (all from useCompareDiff), plus the diff MapCanvas rendered + idle.
  // Fail closed — only set when all four are simultaneously true.
  useEffect(() => {
    if (!isScreenshotMode || mode !== "diff") {
      return;
    }
    if (
      diff.readySteps.leftFetched
      && diff.readySteps.rightFetched
      && diff.readySteps.computeDone
      && diffMapReady
    ) {
      document.documentElement.setAttribute("data-compare-ready", "1");
    }
  }, [isScreenshotMode, mode, diff.readySteps, diffMapReady]);

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
  const selectionStateRef = useRef({ lModel, lVariable, lRun, rModel, rVariable, rRun, forecastHour, mode });
  selectionStateRef.current = { lModel, lVariable, lRun, rModel, rVariable, rRun, forecastHour, mode };

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
        mode: selection.mode,
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

  // Diff mode has a single map. Reuse the same listener attach (no peer to sync)
  // so panning still commits the viewport to the permalink.
  const handleDiffMapReady = useCallback(
    (map: MapLibreMap) => {
      leftMapRef.current = map;
      attachSyncedMapListeners(map, () => null);
    },
    [attachSyncedMapListeners],
  );
  const handleDiffMapRenderReady = useCallback(() => {
    setDiffMapReady(true);
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
      fh: forecastHour, lat, lon, z, mode,
    });
    return `${window.location.origin}/compare${search}`;
  }, [lModel, lVariable, lRun, rModel, rVariable, rRun, forecastHour, lat, lon, z, mode]);

  // Valid time for the displayed (nearest shared) forecast hour — same run +
  // format the scrubber shows. Used in the diff-mode share summary.
  const diffValidTime = useMemo(() => {
    const run = leftLoader.resolvedRun || rightLoader.resolvedRun;
    if (!run) {
      return null;
    }
    const rightSet = new Set(rightLoader.frameHours);
    const validHours = leftLoader.frameHours.filter((h) => rightSet.has(h));
    const hour = validHours.length > 0 ? nearestFrame(validHours, forecastHour) : forecastHour;
    return deriveValidTime(run, hour);
  }, [leftLoader.resolvedRun, rightLoader.resolvedRun, leftLoader.frameHours, rightLoader.frameHours, forecastHour]);

  // Single-line summary for the mobile diff bar, e.g.
  // "6/23 06Z GFS - 6/23 00Z GFS * Surface Temp"
  const diffSummaryLine = useMemo(() => {
    const lParts = parseRunParts(leftLoader.resolvedRun);
    const rParts = parseRunParts(rightLoader.resolvedRun);
    const lModelDisp = modelOptions.find((o) => o.value === lModel)?.label ?? lModel.toUpperCase();
    const rModelDisp = modelOptions.find((o) => o.value === rModel)?.label ?? rModel.toUpperCase();
    const varDisp = variableCatalog.find((v) => v.value === lVariable)?.label ?? lVariable;
    const formatSide = (parts: ReturnType<typeof parseRunParts>, modelDisp: string) =>
      parts ? `${parts.date} ${parts.hour} ${modelDisp}` : modelDisp;
    return `${formatSide(lParts, lModelDisp)} - ${formatSide(rParts, rModelDisp)} * ${varDisp}`;
  }, [leftLoader.resolvedRun, rightLoader.resolvedRun, modelOptions, variableCatalog, lModel, rModel, lVariable]);

  // The mobile drawer only exists in mobile diff mode — close it if either changes.
  useEffect(() => {
    if (mobileDrawerOpen && (layoutMode !== "mobile" || mode !== "diff")) {
      setMobileDrawerOpen(false);
    }
  }, [mobileDrawerOpen, layoutMode, mode]);

  const sharePayload = useMemo<SharePayload>(() => {
    const leftVarLabel = variableCatalog.find(v => v.value === lVariable)?.label ?? lVariable;
    const rightVarLabel = variableCatalog.find(v => v.value === rVariable)?.label ?? rVariable;
    const summary = mode === "diff"
      ? `Difference: ${lModel.toUpperCase()} − ${rModel.toUpperCase()} | ${leftVarLabel}${diffValidTime ? ` | Valid ${diffValidTime}` : ` | F+${Math.round(forecastHour)}`}`
      : `${lModel.toUpperCase()} ${leftVarLabel} vs ${rModel.toUpperCase()} ${rightVarLabel} • FH ${Math.round(forecastHour)}`;
    return {
      permalink: sharePermalink,
      summary,
    };
  }, [sharePermalink, lModel, lVariable, rModel, rVariable, forecastHour, variableCatalog, mode, diffValidTime]);

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
  const diffPanelRef = useRef<HTMLDivElement | null>(null);

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

  // Diff-mode hover: single map, sample both models at the same lat/lon so the
  // tooltip can show Δ plus the L/R breakdown.
  const handleDiffHover = useCallback((lat: number, lon: number, x: number, y: number) => {
    setHoverSide("left");
    setHoverX(x);
    setHoverY(y);
    setHoverContainerWidth(diffPanelRef.current?.offsetWidth ?? 0);
    onLeftHover(lat, lon, x, y);
    onRightHover(lat, lon, x, y);
  }, [onLeftHover, onRightHover]);

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
        mode,
      });
      replaceUrlQuery(search);
    }, URL_SYNC_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [lModel, lVariable, lRun, rModel, rVariable, rRun, forecastHour, mode]);

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
        {mode === "split" ? (
        <>
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
            <div className="ml-auto flex shrink-0 items-center gap-2 pb-0.5">
              <CompareModeToggle mode={mode} onChange={handleModeChange} />
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
          <CompareModeToggle mode={mode} onChange={handleModeChange} />
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
        </>
        ) : (layoutMode === "mobile" && !isScreenshotMode) ? (
          // Mobile diff: keep the mode toggle + utility row, collapse the picker
          // rows into a summary bar that opens the bottom drawer.
          <div className="px-4 pb-2">
            <div className="flex items-center gap-2">
              <CompareModeToggle mode={mode} onChange={handleModeChange} compact />
              <div className="ml-auto flex shrink-0 items-center gap-2">
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
                  type="button"
                  onClick={() => setMobileDrawerOpen(true)}
                  className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.05] text-white/50 transition-all hover:border-white/20 hover:bg-white/[0.09] hover:text-white"
                  aria-label="Comparison settings"
                  title="Comparison settings"
                >
                  <Settings className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            <div className="mt-2">
              <CompareMobileDiffBar summaryLine={diffSummaryLine} />
            </div>
            {diffNotice ? (
              <div className="mt-2 flex items-start gap-2 rounded-lg border border-cyan-300/20 bg-cyan-300/[0.06] px-3 py-2 text-[11px] font-medium text-cyan-100/90">
                <span className="min-w-0 flex-1">{diffNotice}</span>
                <button
                  type="button"
                  onClick={() => setDiffNotice(null)}
                  className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-cyan-100/50 transition-colors hover:bg-white/[0.08] hover:text-cyan-50"
                  aria-label="Dismiss notice"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : null}
          </div>
        ) : (
          <DiffControlBar
            lModel={lModel}
            rModel={rModel}
            sharedVariable={lVariable}
            lRun={lRun}
            rRun={rRun}
            mode={mode}
            modelOptions={modelOptions}
            variableCatalog={variableCatalog}
            diffMutualVariables={diffMutualVariables}
            leftRunOptions={leftRunOptions}
            rightRunOptions={rightRunOptions}
            viewerHref={viewerHref}
            diffNotice={diffNotice}
            settingsButtonRef={settingsButtonRef}
            onModeChange={handleModeChange}
            onLeftModelChange={handleDiffLeftModelChange}
            onRightModelChange={handleDiffRightModelChange}
            onSharedVariableChange={handleSharedVariableChange}
            onLeftRunChange={setLRun}
            onRightRunChange={setRRun}
            onSwap={handleSwap}
            onShare={handleShare}
            onSettingsClick={handleSettingsClick}
            onDismissNotice={() => setDiffNotice(null)}
          />
        )}
      </div>

      <div
        ref={containerRef}
        className={`relative min-h-0 flex-1 overflow-hidden ${
          mode === "split" ? `flex ${isDesktop ? "flex-row" : "flex-col"}` : ""
        }`}
      >
        {mode === "split" ? (
        <>
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
        </>
        ) : (
          <div ref={diffPanelRef} className="relative h-full w-full">
            <CompareDiffPanel
              hasMutualEligibleVariables={diffMutualVariables.length > 0}
              leftModel={lModel}
              rightModel={rModel}
              variable={lVariable}
              region={region}
              basemapMode={basemapMode}
              showLegend={showLegends}
              diffManifest={diff.diffManifest}
              diffFrameUrl={diff.diffFrameUrl}
              diffLegend={diff.diffLegend}
              isLoading={diff.isLoading}
              error={diff.error}
              onMapReady={handleDiffMapReady}
              onDiffMapReady={handleDiffMapRenderReady}
              onMapHover={handleDiffHover}
              onMapHoverEnd={handleHoverEnd}
            />
            {hoverSide !== null ? (
              <CompareTooltip
                mode="diff"
                varKey={lVariable}
                leftModel={lModel}
                rightModel={rModel}
                leftTooltip={leftTooltip}
                rightTooltip={rightTooltip}
                x={hoverX}
                y={hoverY}
                containerWidth={hoverContainerWidth}
                side="left"
              />
            ) : null}
          </div>
        )}
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

      <CompareMobileDrawer
        open={mobileDrawerOpen && layoutMode === "mobile" && mode === "diff" && !isScreenshotMode}
        onClose={() => setMobileDrawerOpen(false)}
        activeTab={mobileDrawerTab}
        onTabChange={setMobileDrawerTab}
        lModel={lModel}
        rModel={rModel}
        sharedVariable={lVariable}
        lRun={lRun}
        rRun={rRun}
        modelOptions={modelOptions}
        variableCatalog={variableCatalog}
        diffMutualVariables={diffMutualVariables}
        leftRunOptions={leftRunOptions}
        rightRunOptions={rightRunOptions}
        onLeftModelChange={handleDiffLeftModelChange}
        onRightModelChange={handleDiffRightModelChange}
        onSharedVariableChange={handleSharedVariableChange}
        onLeftRunChange={setLRun}
        onRightRunChange={setRRun}
        onSwap={handleSwap}
        basemapMode={basemapMode}
        onToggleBasemap={() => setBasemapMode((prev) => (prev === "dark" ? "light" : "dark"))}
        showLegends={showLegends}
        onToggleLegends={() => setShowLegends((v) => !v)}
      />
    </div>
  );
}
