import { useEffect, useRef, useState, useMemo } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  Boxes,
  CalendarClock,
  ChevronDown,
  Eye,
  Layers,
  MapPin,
  Moon,
  Send,
  Settings,
  SlidersHorizontal,
  Sun,
  X,
} from "lucide-react";

import { BRAND_LOGO_SRC } from "@/lib/branding";
import { cn } from "@/lib/utils";
import { useViewerToolbar } from "@/lib/viewer-toolbar-context";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import type { ObservedSourceStatusTone } from "@/lib/time-axis";
import type { GroupedOption } from "@/lib/app-utils";

// ─── Shared types ────────────────────────────────────────────────────────────
type Option = { value: string; label: string };
type VariableOption = Option & { group: string | null };

// ─── Source status badge ─────────────────────────────────────────────────────
function sourceStatusBadgeClass(tone: ObservedSourceStatusTone | null | undefined): string {
  switch (tone) {
    case "live":
      return "border-emerald-300/24 bg-emerald-300/[0.08] text-emerald-50";
    case "delayed":
      return "border-amber-300/24 bg-amber-300/[0.08] text-amber-50";
    case "stale":
      return "border-orange-300/24 bg-orange-300/[0.1] text-orange-50";
    case "unavailable":
      return "border-rose-300/24 bg-rose-300/[0.08] text-rose-50";
    default:
      return "border-white/10 bg-white/8 text-white/78";
  }
}

function SourceStatusBadge({
  label,
  description,
  tone,
}: {
  label: string;
  description?: string | null;
  tone?: ObservedSourceStatusTone | null;
}) {
  return (
    <div
      title={description ?? label}
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]",
        sourceStatusBadgeClass(tone)
      )}
    >
      {label}
    </div>
  );
}

// ─── Compact toolbar select ───────────────────────────────────────────────────
const GROUP_ORDER = ["MODELS", "OBSERVATIONS", "SURFACE", "PRECIPITATION", "SEVERE", "UPPER AIR"];

function spcVariableLabel(option: VariableOption): string {
  switch (option.value) {
    case "convective": return "Categorical";
    case "tornado_prob": return "Tornado";
    case "wind_prob": return "Wind";
    case "hail_prob": return "Hail";
    default: return option.label;
  }
}

function NavbarSelect(props: {
  value: string;
  onValueChange: (value: string) => void;
  options: (Option | VariableOption | GroupedOption)[];
  disabled?: boolean;
  placeholder: string;
  grouped?: boolean;
  selectedLabelOverride?: string;
  highlightState?: boolean;
  menuActionLabel?: string | null;
  menuActionDescription?: string | null;
  onMenuAction?: () => void;
  minWidth?: string;
}) {
  const [open, setOpen] = useState(false);
  const {
    value,
    onValueChange,
    options,
    disabled,
    placeholder,
    grouped,
    selectedLabelOverride,
    highlightState = false,
    menuActionLabel,
    menuActionDescription,
    onMenuAction,
    minWidth = "min-w-[120px]",
  } = props;

  const selectedLabel = selectedLabelOverride ?? options.find((o) => o.value === value)?.label ?? placeholder;

  let content: React.ReactNode;
  if (grouped) {
    const groups = new Map<string, Option[]>();
    const ungrouped: Option[] = [];
    for (const opt of options) {
      const g = "group" in opt && typeof opt.group === "string" ? opt.group : null;
      if (g) {
        let list = groups.get(g);
        if (!list) { list = []; groups.set(g, list); }
        list.push(opt);
      } else {
        ungrouped.push(opt);
      }
    }
    const ordered = GROUP_ORDER.filter((g) => groups.has(g));
    for (const g of groups.keys()) {
      if (!ordered.includes(g)) ordered.push(g);
    }
    content = (
      <>
        {ordered.map((g) => (
          <SelectGroup key={g}>
            <SelectLabel className="px-2 pt-1.5 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-white/60">
              {g}
            </SelectLabel>
            {groups.get(g)!.map((opt) => (
              <SelectItem key={opt.value} value={opt.value} className="text-xs font-medium">
                {opt.label}
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
        {ungrouped.map((opt) => (
          <SelectItem key={opt.value} value={opt.value} className="text-xs font-medium">
            {opt.label}
          </SelectItem>
        ))}
      </>
    );
  } else {
    content = options.map((opt) => (
      <SelectItem key={opt.value} value={opt.value} className="text-xs font-medium">
        {opt.label}
      </SelectItem>
    ));
  }

  const resolvedContent =
    menuActionLabel && onMenuAction ? (
      <>
        <button
          type="button"
          onClick={() => { setOpen(false); onMenuAction(); }}
          className="flex w-full flex-col items-start rounded-md px-3 py-2 text-left transition-colors duration-150 hover:bg-white/10"
        >
          <span className="text-xs font-semibold text-emerald-50">{menuActionLabel}</span>
          {menuActionDescription ? (
            <span className="mt-0.5 text-[11px] text-emerald-100/72">{menuActionDescription}</span>
          ) : null}
        </button>
        <SelectSeparator className="my-1 bg-white/10" />
        {content}
      </>
    ) : content;

  return (
    <Select
      value={value}
      onValueChange={(v) => { setOpen(false); onValueChange(v); }}
      open={open}
      onOpenChange={setOpen}
      disabled={disabled || options.length === 0}
    >
      <SelectTrigger
        className={cn(
          "h-8 border-white/10 bg-white/[0.06] px-2.5 text-[12px] font-medium text-white shadow-none transition-all duration-150 hover:border-white/20 hover:bg-white/[0.10] focus:ring-0 [&>span]:line-clamp-none",
          minWidth,
          highlightState
            ? "border-emerald-300/20 bg-emerald-300/[0.07] text-emerald-50 hover:bg-emerald-300/[0.11]"
            : ""
        )}
      >
        <span className="truncate whitespace-nowrap pr-1">{selectedLabel}</span>
      </SelectTrigger>
      <SelectContent>{resolvedContent}</SelectContent>
    </Select>
  );
}

// ─── Display toggle row ───────────────────────────────────────────────────────
function DisplayRow({
  label,
  icon: Icon,
  checked,
  onToggle,
}: {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={checked}
      className={cn(
        "flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition-all duration-150",
        checked
          ? "border-[#354d42] bg-[rgba(53,77,66,0.22)] text-white hover:bg-[rgba(53,77,66,0.3)]"
          : "border-white/10 bg-black/18 text-white/88 hover:bg-black/28"
      )}
    >
      <div className="flex items-center gap-2 text-sm font-semibold text-white">
        <Icon className="h-4 w-4 text-white/72" />
        {label}
      </div>
      <span className={cn("text-xs font-semibold", checked ? "text-[#98c9b2]" : "text-white/42")}>
        {checked ? "On" : "Off"}
      </span>
    </button>
  );
}

// ─── Viewer toolbar inline (desktop) ─────────────────────────────────────────
function ViewerNavDesktop() {
  const toolbar = useViewerToolbar();
  const settingsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!toolbar?.displayPanelOpen) return;
    function onPointerDown(e: MouseEvent | TouchEvent) {
      if (!(e.target instanceof Node)) return;
      if (settingsRef.current?.contains(e.target)) return;
      toolbar?.onDisplayPanelOpenChange(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") toolbar?.onDisplayPanelOpenChange(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [toolbar?.displayPanelOpen]);

  if (!toolbar) return null;

  const {
    variable, onVariableChange, variables, model, onModelChange, models,
    run, onRunChange, runs, region, onRegionChange, regions,
    disabled, runDisplayLabel, hasNewerRunAvailable, latestAvailableRunLabel,
    onViewLatestRun, runSelectionLocked, sourceStatusLabel, sourceStatusDescription,
    sourceStatusTone, onShare, displayPanelOpen, onDisplayPanelOpenChange,
    pointLabelsEnabled, onPointLabelsEnabledChange, legendVisible, onLegendVisibleChange,
    basemapMode, onBasemapModeChange, opacity, onOpacityChange,
    zoomControlsVisible, onZoomControlsVisibleChange,
  } = toolbar;

  const displayVariables = useMemo(
    () =>
      model === "spc"
        ? variables.map((o) => ({ ...o, label: spcVariableLabel(o) }))
        : variables,
    [model, variables]
  );

  const runMenuOptions = useMemo(() => {
    if (!hasNewerRunAvailable) return runs;
    return runs.filter((o) => o.value !== "latest");
  }, [hasNewerRunAvailable, runs]);

  return (
    <div className="flex flex-1 items-center gap-2 overflow-hidden">
      {/* Primary selectors */}
      <div className="flex min-w-0 flex-1 items-center gap-1.5">
        <NavbarSelect
          value={variable}
          onValueChange={onVariableChange}
          options={displayVariables}
          disabled={disabled}
          placeholder="Variable"
          grouped
          minWidth="min-w-[148px] max-w-[210px]"
        />
        <NavbarSelect
          value={model}
          onValueChange={onModelChange}
          options={models}
          disabled={disabled}
          placeholder="Model"
          grouped
          minWidth="min-w-[80px] max-w-[120px]"
        />
        <NavbarSelect
          value={run}
          onValueChange={onRunChange}
          options={runMenuOptions}
          disabled={disabled || runSelectionLocked}
          placeholder="Run"
          selectedLabelOverride={runDisplayLabel}
          highlightState={!runSelectionLocked && hasNewerRunAvailable}
          menuActionLabel={!runSelectionLocked && hasNewerRunAvailable ? "View latest run" : null}
          menuActionDescription={
            !runSelectionLocked && hasNewerRunAvailable && latestAvailableRunLabel
              ? `${latestAvailableRunLabel} available`
              : null
          }
          onMenuAction={!runSelectionLocked && hasNewerRunAvailable ? onViewLatestRun : undefined}
          minWidth="min-w-[110px] max-w-[170px]"
        />
        <NavbarSelect
          value={region}
          onValueChange={onRegionChange}
          options={regions}
          disabled={disabled}
          placeholder="Region"
          minWidth="min-w-[80px] max-w-[130px]"
        />
      </div>

      {/* Right group: source status + share + settings */}
      <div className="flex shrink-0 items-center gap-1.5 border-l border-white/8 pl-2">
        {sourceStatusLabel ? (
          <SourceStatusBadge
            label={sourceStatusLabel}
            description={sourceStatusDescription}
            tone={sourceStatusTone}
          />
        ) : null}

        {onShare ? (
          <button
            type="button"
            onClick={onShare}
            title="Share"
            aria-label="Share"
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.05] text-white/72 transition-all duration-150 hover:border-white/20 hover:bg-white/[0.10] hover:text-white"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        ) : null}

        {/* Settings / Display panel */}
        <div className="relative" ref={settingsRef}>
          <button
            type="button"
            onClick={() => onDisplayPanelOpenChange(!displayPanelOpen)}
            aria-expanded={displayPanelOpen}
            title="Display settings"
            aria-label="Display settings"
            className={cn(
              "inline-flex h-8 w-8 items-center justify-center rounded-lg border transition-all duration-150",
              displayPanelOpen
                ? "border-white/20 bg-white/12 text-white"
                : "border-white/10 bg-white/[0.05] text-white/72 hover:border-white/20 hover:bg-white/[0.10] hover:text-white"
            )}
          >
            <Settings className="h-3.5 w-3.5" />
          </button>

          {displayPanelOpen ? (
            <div className="glass absolute right-0 top-[calc(100%+0.5rem)] z-[70] w-[220px] rounded-2xl px-3 py-3 shadow-[0_12px_30px_rgba(0,0,0,0.3)]">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-white/48">Display</div>
                  <div className="pt-0.5 text-xs text-white/62">Map overlays and reference aids.</div>
                </div>
                <button
                  type="button"
                  onClick={() => onDisplayPanelOpenChange(false)}
                  className="ml-2 inline-flex h-6 w-6 items-center justify-center rounded-md text-white/40 hover:text-white/80"
                  aria-label="Close display panel"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>

              <div className="space-y-2">
                <DisplayRow
                  label="City Labels"
                  icon={MapPin}
                  checked={pointLabelsEnabled}
                  onToggle={() => onPointLabelsEnabledChange(!pointLabelsEnabled)}
                />
                <DisplayRow
                  label="Legend"
                  icon={Eye}
                  checked={legendVisible}
                  onToggle={() => onLegendVisibleChange(!legendVisible)}
                />
                <DisplayRow
                  label="Zoom Controls"
                  icon={SlidersHorizontal}
                  checked={zoomControlsVisible}
                  onToggle={() => onZoomControlsVisibleChange(!zoomControlsVisible)}
                />
                <button
                  type="button"
                  onClick={() => onBasemapModeChange(basemapMode === "dark" ? "light" : "dark")}
                  className="flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-black/18 px-3 py-2 text-left transition-all duration-150 hover:bg-black/28"
                >
                  <div className="flex items-center gap-2 text-sm font-semibold text-white">
                    {basemapMode === "dark"
                      ? <Moon className="h-4 w-4 text-white/72" />
                      : <Sun className="h-4 w-4 text-white/72" />}
                    Basemap
                  </div>
                  <span className="text-xs font-semibold text-[#98c9b2]">
                    {basemapMode === "dark" ? "Dark" : "Light"}
                  </span>
                </button>

                <div className="rounded-lg border border-white/10 bg-black/18 px-3 py-2">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-sm font-semibold text-white">Opacity</span>
                    <span className="font-mono text-[10px] text-white/62">{Math.round(opacity * 100)}%</span>
                  </div>
                  <Slider
                    value={[Math.round(opacity * 100)]}
                    onValueChange={([v]) => onOpacityChange((v ?? 100) / 100)}
                    min={0}
                    max={100}
                    step={1}
                    className="w-full [&>*:first-child]:h-2 [&>*:first-child]:bg-secondary/55 [&>*:nth-child(2)]:h-4 [&>*:nth-child(2)]:w-4"
                  />
                </div>

                <div className="border-t border-white/8 pt-2 text-[10px] leading-relaxed text-white/42">
                  Maps:{" "}
                  <a href="https://www.maplibre.org/" target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-white/70">MapLibre</a>
                  {" "}|{" "}
                  <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-white/70">OSM</a>
                  {" "}|{" "}
                  <a href="https://carto.com/attributions" target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-white/70">CARTO</a>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

// ─── Viewer toolbar mobile/tablet (slide-up sheet) ───────────────────────────
function ViewerNavMobile() {
  const toolbar = useViewerToolbar();
  const [sheetOpen, setSheetOpen] = useState(false);

  if (!toolbar) return null;

  const {
    variable, onVariableChange, variables, model, onModelChange, models,
    run, onRunChange, runs, region, onRegionChange, regions, disabled,
    runDisplayLabel, hasNewerRunAvailable, latestAvailableRunLabel, onViewLatestRun,
    runSelectionLocked, sourceStatusLabel, sourceStatusDescription, sourceStatusTone,
    onShare, pointLabelsEnabled, onPointLabelsEnabledChange, legendVisible,
    onLegendVisibleChange, basemapMode, onBasemapModeChange, opacity, onOpacityChange,
  } = toolbar;

  const displayVariables = model === "spc"
    ? variables.map((o) => ({ ...o, label: spcVariableLabel(o) }))
    : variables;

  const runMenuOptions = hasNewerRunAvailable
    ? runs.filter((o) => o.value !== "latest")
    : runs;

  const selectedVariableLabel = displayVariables.find((o) => o.value === variable)?.label ?? "Variable";
  const selectedModelLabel = models.find((o) => o.value === model)?.label ?? "Model";
  const selectedRunLabel = (runDisplayLabel ?? runs.find((o) => o.value === run)?.label ?? "Run")
    .replace(/^Latest\s*\((.*)\)$/, "$1");

  return (
    <>
      {/* Compact summary + controls icon */}
      <div className="flex flex-1 items-center justify-end gap-2">
        <div className="flex min-w-0 items-center gap-1 overflow-x-auto text-[11px] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {sourceStatusLabel ? (
            <SourceStatusBadge label={sourceStatusLabel} description={sourceStatusDescription} tone={sourceStatusTone} />
          ) : null}
          <span className="rounded-full border border-white/10 bg-white/8 px-2 py-1 font-medium text-white/82 whitespace-nowrap">
            {selectedVariableLabel}
          </span>
          <span className="rounded-full border border-white/10 bg-white/8 px-2 py-1 font-medium text-white/68 whitespace-nowrap">
            {selectedModelLabel}
          </span>
          <span className="rounded-full border border-white/10 bg-white/8 px-2 py-1 font-medium text-white/60 whitespace-nowrap">
            {selectedRunLabel}
          </span>
        </div>

        {onShare ? (
          <button
            type="button"
            onClick={onShare}
            title="Share"
            aria-label="Share"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.05] text-white/72"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        ) : null}

        <button
          type="button"
          onClick={() => setSheetOpen(true)}
          aria-label="Open controls"
          className={cn(
            "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.05] text-white/72",
            sheetOpen && "border-white/20 bg-white/12 text-white"
          )}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Slide-up sheet */}
      {sheetOpen ? (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-[65] bg-black/50 backdrop-blur-sm"
            onClick={() => setSheetOpen(false)}
            aria-hidden="true"
          />
          {/* Sheet */}
          <div className="fixed bottom-0 left-0 right-0 z-[66] rounded-t-[1.5rem] border-t border-white/12 bg-[#0d1a2f] pb-[env(safe-area-inset-bottom)] shadow-[0_-12px_40px_rgba(0,0,0,0.5)]">
            {/* Handle */}
            <div className="flex justify-center pt-3 pb-1">
              <div className="h-1 w-10 rounded-full bg-white/20" />
            </div>
            <div className="flex items-center justify-between px-4 pb-3 pt-1">
              <span className="text-sm font-semibold text-white">Controls</span>
              <button
                type="button"
                onClick={() => setSheetOpen(false)}
                className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-white/10 bg-white/5 text-white/60 hover:text-white"
                aria-label="Close controls"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="max-h-[70svh] overflow-y-auto px-4 pb-6">
              <div className="grid grid-cols-1 gap-3">
                <div className="flex flex-col gap-1">
                  <span className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-white/50">
                    <Layers className="h-3 w-3" /> Product
                  </span>
                  <NavbarSelect
                    value={variable}
                    onValueChange={(v) => { onVariableChange(v); setSheetOpen(false); }}
                    options={displayVariables}
                    disabled={disabled}
                    placeholder="Variable"
                    grouped
                    minWidth="w-full"
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <span className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-white/50">
                    <Boxes className="h-3 w-3" /> Model
                  </span>
                  <NavbarSelect
                    value={model}
                    onValueChange={(v) => { onModelChange(v); setSheetOpen(false); }}
                    options={models}
                    disabled={disabled}
                    placeholder="Model"
                    grouped
                    minWidth="w-full"
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <span className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-white/50">
                    <CalendarClock className="h-3 w-3" /> Run
                  </span>
                  <NavbarSelect
                    value={run}
                    onValueChange={(v) => { onRunChange(v); setSheetOpen(false); }}
                    options={runMenuOptions}
                    disabled={disabled || runSelectionLocked}
                    placeholder="Run"
                    selectedLabelOverride={runDisplayLabel}
                    highlightState={!runSelectionLocked && hasNewerRunAvailable}
                    menuActionLabel={!runSelectionLocked && hasNewerRunAvailable ? "View latest run" : null}
                    menuActionDescription={
                      !runSelectionLocked && hasNewerRunAvailable && latestAvailableRunLabel
                        ? `${latestAvailableRunLabel} available`
                        : null
                    }
                    onMenuAction={
                      !runSelectionLocked && hasNewerRunAvailable
                        ? () => { onViewLatestRun?.(); setSheetOpen(false); }
                        : undefined
                    }
                    minWidth="w-full"
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <span className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-white/50">
                    <MapPin className="h-3 w-3" /> Region
                  </span>
                  <NavbarSelect
                    value={region}
                    onValueChange={(v) => { onRegionChange(v); setSheetOpen(false); }}
                    options={regions}
                    disabled={disabled}
                    placeholder="Region"
                    minWidth="w-full"
                  />
                </div>
              </div>

              <div className="mt-4 border-t border-white/10 pt-3">
                <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/48">
                  Map Display
                </div>
                <div className="space-y-2">
                  <DisplayRow
                    label="City Labels"
                    icon={MapPin}
                    checked={pointLabelsEnabled}
                    onToggle={() => onPointLabelsEnabledChange(!pointLabelsEnabled)}
                  />
                  <DisplayRow
                    label="Legend"
                    icon={Eye}
                    checked={legendVisible}
                    onToggle={() => onLegendVisibleChange(!legendVisible)}
                  />
                  <button
                    type="button"
                    onClick={() => onBasemapModeChange(basemapMode === "dark" ? "light" : "dark")}
                    className="flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-black/18 px-3 py-2 text-left hover:bg-black/28"
                  >
                    <div className="flex items-center gap-2 text-sm font-semibold text-white">
                      {basemapMode === "dark" ? <Moon className="h-4 w-4 text-white/72" /> : <Sun className="h-4 w-4 text-white/72" />}
                      Basemap
                    </div>
                    <span className="text-xs font-semibold text-[#98c9b2]">
                      {basemapMode === "dark" ? "Dark" : "Light"}
                    </span>
                  </button>

                  <div className="rounded-lg border border-white/10 bg-black/18 px-3 py-2">
                    <div className="mb-1 flex items-center justify-between">
                      <span className="text-sm font-semibold text-white">Opacity</span>
                      <span className="font-mono text-[10px] text-white/62">{Math.round(opacity * 100)}%</span>
                    </div>
                    <Slider
                      value={[Math.round(opacity * 100)]}
                      onValueChange={([v]) => onOpacityChange((v ?? 100) / 100)}
                      min={0}
                      max={100}
                      step={1}
                      className="w-full [&>*:first-child]:h-2 [&>*:first-child]:bg-secondary/55 [&>*:nth-child(2)]:h-4 [&>*:nth-child(2)]:w-4"
                    />
                  </div>
                </div>
              </div>

              <div className="mt-4 border-t border-white/10 pt-3 text-[10px] leading-relaxed text-white/42">
                Maps:{" "}
                <a href="https://www.maplibre.org/" target="_blank" rel="noreferrer" className="underline underline-offset-2">MapLibre</a>
                {" "}|{" "}
                <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer" className="underline underline-offset-2">OSM</a>
                {" "}|{" "}
                <a href="https://carto.com/attributions" target="_blank" rel="noreferrer" className="underline underline-offset-2">CARTO</a>
              </div>
            </div>
          </div>
        </>
      ) : null}
    </>
  );
}

// ─── Marketing nav item ───────────────────────────────────────────────────────
type NavItemProps = {
  to: string;
  label: string;
  onClick?: () => void;
  className?: string;
};

type TwfStatus =
  | { linked: false; admin?: boolean }
  | { linked: true; admin?: boolean; member_id: number; display_name: string; photo_url?: string | null };

function getApiBase(): string {
  const fromEnv = (import.meta as any)?.env?.VITE_API_BASE as string | undefined;
  return ((fromEnv ?? "https://api.cartosky.com").trim()).replace(/\/$/, "");
}

function NavItem({ to, label, onClick, className }: NavItemProps) {
  return (
    <NavLink
      to={to}
      onClick={onClick}
      className={({ isActive }) =>
        [
          "text-sm font-medium transition px-3 py-1.5 rounded-md",
          isActive ? "text-white bg-white/10" : "text-white/70 hover:text-white hover:bg-white/10",
          className ?? "",
        ].join(" ")
      }
    >
      {label}
    </NavLink>
  );
}

// ─── Main SiteHeader ──────────────────────────────────────────────────────────
export default function SiteHeader({ variant }: { variant: "marketing" | "app" }) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [twfStatus, setTwfStatus] = useState<TwfStatus>({ linked: false });
  const location = useLocation();
  const menuRef = useRef<HTMLDivElement | null>(null);
  const toolbar = useViewerToolbar();

  const isAppVariant = variant === "app";
  const isMarketingVariant = variant === "marketing";
  const isViewerRoute = location.pathname === "/viewer";
  const showAppNav = isAppVariant && !isViewerRoute;
  const isViewerDesktop = isViewerRoute && (toolbar?.layoutMode === "desktop" || toolbar?.layoutMode === undefined);
  const isViewerMobile = isViewerRoute && !isViewerDesktop;

  const accountLabel = twfStatus.linked ? twfStatus.display_name : "Login";
  const accountPhotoUrl = twfStatus.linked ? twfStatus.photo_url : null;
  const adminEnabled = twfStatus.admin === true;

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${getApiBase()}/auth/twf/status`, {
      method: "GET",
      credentials: "include",
      signal: controller.signal,
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`Status request failed (${r.status})`);
        return (await r.json()) as TwfStatus;
      })
      .then((status) => setTwfStatus(status))
      .catch((e: unknown) => {
        if ((e as any)?.name === "AbortError") return;
        setTwfStatus({ linked: false });
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    setMobileMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileMenuOpen) return;
    function onPointerDown(event: MouseEvent | TouchEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (menuRef.current?.contains(target)) return;
      setMobileMenuOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setMobileMenuOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [mobileMenuOpen]);

  return (
    <header className="sticky top-0 z-[60] border-b border-white/8 bg-[#08111f]/78 backdrop-blur-2xl">
      <div
        className={
          isAppVariant
            ? "flex h-14 items-center gap-3 px-4 md:px-5"
            : "mx-auto flex h-16 max-w-6xl items-center gap-3 px-5 md:gap-6 md:px-8"
        }
      >
        {/* Logo */}
        <NavLink to="/" className="flex shrink-0 items-center font-semibold tracking-tight text-white">
          <img
            src={BRAND_LOGO_SRC}
            alt="CartoSky"
            className="block h-12 w-auto max-w-none"
          />
        </NavLink>

        {/* Viewer route — desktop inline toolbar */}
        {isViewerRoute && isViewerDesktop ? (
          <ViewerNavDesktop />
        ) : null}

        {/* Viewer route — mobile compact controls */}
        {isViewerRoute && isViewerMobile ? (
          <ViewerNavMobile />
        ) : null}

        {/* Marketing nav — desktop */}
        {isMarketingVariant ? (
          <nav className="ml-auto hidden items-center gap-1 md:flex">
            <NavLink
              to="/viewer"
              className="inline-flex items-center rounded-lg border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-3.5 py-2 text-sm font-semibold text-slate-950 shadow-[0_14px_30px_rgba(35,196,255,0.14)] transition duration-150 hover:brightness-105"
            >
              Viewer
            </NavLink>
            <NavItem to="/forecast" label="Forecast" className="ml-2 text-white/72 hover:text-white" />
            {adminEnabled ? <NavItem to="/admin" label="Admin" /> : null}
            <NavLink
              to="/login"
              className="ml-3 rounded-lg px-2 py-2 text-sm text-white/62 transition duration-150 hover:text-white/88"
            >
              <span className="inline-flex items-center gap-2">
                {accountPhotoUrl ? (
                  <img src={accountPhotoUrl} alt="" className="h-5 w-5 rounded-full object-cover" />
                ) : null}
                <span>{accountLabel}</span>
              </span>
            </NavLink>
          </nav>
        ) : null}

        {/* App nav (non-viewer app routes) */}
        {showAppNav ? (
          <nav className="ml-auto hidden items-center gap-1 md:flex">
            <NavItem to="/viewer" label="Viewer" />
            {adminEnabled ? <NavItem to="/admin" label="Admin" /> : null}
          </nav>
        ) : null}

        {/* Marketing nav — mobile hamburger */}
        {isMarketingVariant ? (
          <div className="ml-auto flex items-center gap-2 md:hidden" ref={menuRef}>
            <NavLink
              to="/viewer"
              className="inline-flex items-center rounded-lg border border-cyan-200/35 bg-[linear-gradient(180deg,#97e7ff_0%,#76d5fb_100%)] px-3 py-2 text-sm font-semibold text-slate-950 shadow-[0_14px_30px_rgba(35,196,255,0.14)]"
            >
              Viewer
            </NavLink>
            <button
              type="button"
              className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-white/15 bg-white/5 text-white hover:bg-white/10"
              aria-label="Open menu"
              aria-expanded={mobileMenuOpen}
              aria-controls="mobile-site-nav"
              onClick={() => setMobileMenuOpen((open) => !open)}
            >
              <span className="sr-only">{mobileMenuOpen ? "Close menu" : "Open menu"}</span>
              <span className="flex w-4 flex-col gap-1.5">
                <span className="block h-0.5 w-4 rounded bg-current" />
                <span className="block h-0.5 w-4 rounded bg-current" />
                <span className="block h-0.5 w-4 rounded bg-current" />
              </span>
            </button>

            {mobileMenuOpen ? (
              <nav
                id="mobile-site-nav"
                className="absolute right-0 top-[calc(100%+0.5rem)] z-[70] w-[min(92vw,360px)] rounded-2xl border border-white/15 bg-black/90 p-2.5 text-white shadow-[0_20px_52px_rgba(0,0,0,0.72)] backdrop-blur-xl"
                aria-label="Site navigation"
              >
                <div className="flex flex-col gap-1">
                  <NavItem to="/viewer" label="Viewer" onClick={() => setMobileMenuOpen(false)} className="text-white/90 hover:text-white" />
                  <NavItem to="/forecast" label="Forecast" onClick={() => setMobileMenuOpen(false)} className="text-white/90 hover:text-white" />
                  {adminEnabled ? (
                    <NavItem to="/admin" label="Admin" onClick={() => setMobileMenuOpen(false)} className="text-white/90 hover:text-white" />
                  ) : null}
                  <div className="my-1 h-px bg-white/10" />
                  <NavItem to="/login" label={accountLabel} onClick={() => setMobileMenuOpen(false)} className="text-white/90 hover:text-white" />
                </div>
              </nav>
            ) : null}
          </div>
        ) : null}
      </div>
    </header>
  );
}
