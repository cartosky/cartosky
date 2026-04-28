import { useEffect, useRef, useState, useMemo } from "react";
import { createPortal } from "react-dom";
import { NavLink, useLocation } from "react-router-dom";
import {
  Boxes,
  CalendarClock,
  Globe,
  Layers,
  MapPin,
  Moon,
  Palette,
  Send,
  Settings,
  Sun,
  X,
  ZoomIn,
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
import { MapLegend } from "@/components/map-legend";
import type { ObservedSourceStatusTone } from "@/lib/time-axis";
import type { GroupedOption } from "@/lib/app-utils";

// ─── Shared types ────────────────────────────────────────────────────────────
type Option = { value: string; label: string };
type VariableOption = Option & { group: string | null };

function AvailabilityReadout({
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
        "inline-flex items-center gap-1.5 rounded-xl border px-2.5 py-1.5 font-['IBM_Plex_Mono',monospace] text-[10px] font-medium tracking-[0.06em] shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]",
        tone === "unavailable"
          ? "border-rose-300/24 bg-rose-300/[0.08] text-rose-50/94"
          : tone === "stale"
            ? "border-orange-300/24 bg-orange-300/[0.08] text-orange-50/94"
            : tone === "delayed"
              ? "border-cyan-300/20 bg-cyan-300/[0.10] text-cyan-50/96"
              : "border-emerald-300/24 bg-emerald-300/[0.12] text-emerald-50/96"
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          tone === "unavailable"
            ? "bg-rose-300/90"
            : tone === "stale"
              ? "bg-orange-300/90"
              : tone === "delayed"
                ? "bg-cyan-300/90"
                : "bg-emerald-300/90"
        )}
      />
      {label}
    </div>
  );
}


const GROUP_ORDER = ["MODELS", "ENSEMBLES", "OBSERVATIONS", "SURFACE", "PRECIPITATION", "SEVERE", "UPPER AIR"];

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
          <span className="text-xs font-semibold text-cyan-100">{menuActionLabel}</span>
          {menuActionDescription ? (
            <span className="mt-0.5 text-[11px] text-cyan-100/60">{menuActionDescription}</span>
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
          "h-8 w-auto gap-2 rounded-xl border-white/[0.09] bg-white/[0.05] px-3 text-[12px] font-medium text-white/82 shadow-none transition-all duration-150 hover:border-white/18 hover:bg-white/[0.09] hover:text-white focus:ring-0 [&>span]:line-clamp-none",
          minWidth,
          highlightState
            ? "border-cyan-300/25 bg-cyan-300/[0.08] text-cyan-100 hover:bg-cyan-300/[0.12]"
            : ""
        )}
      >
        <span className="whitespace-nowrap">{selectedLabel}</span>
      </SelectTrigger>
      <SelectContent>{resolvedContent}</SelectContent>
    </Select>
  );
}

function HeaderSelectField({
  label,
  icon: Icon,
  children,
}: {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="flex items-center gap-1.5 pl-1 text-[9px] font-medium uppercase tracking-[0.18em] text-white/44">
        <Icon className="h-3 w-3" />
        {label}
      </span>
      {children}
    </div>
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
          ? "border-cyan-300/20 bg-cyan-300/[0.07] text-white hover:bg-cyan-300/[0.11]"
          : "border-white/10 bg-white/[0.04] text-white/82 hover:bg-white/[0.07]"
      )}
    >
      <div className="flex items-center gap-2 text-sm font-semibold text-white">
        <Icon className="h-4 w-4 text-white/72" />
        {label}
      </div>
      <span className={cn("font-['IBM_Plex_Mono',monospace] text-[10px] font-medium", checked ? "text-cyan-300/90" : "text-white/38")}>
        {checked ? "On" : "Off"}
      </span>
    </button>
  );
}

function RegionUtilitySelect({
  value,
  onValueChange,
  options,
  disabled,
  currentRegionLabel,
}: {
  value: string;
  onValueChange: (value: string) => void;
  options: Option[];
  disabled?: boolean;
  currentRegionLabel: string;
}) {
  return (
    <Select
      value={value}
      onValueChange={onValueChange}
      disabled={disabled || options.length === 0}
    >
      <SelectTrigger
        title={`Region: ${currentRegionLabel}`}
        aria-label={`Region: ${currentRegionLabel}`}
        hideChevron
        className="h-8 w-8 items-center justify-center rounded-xl border-white/10 bg-white/[0.05] px-0 text-white/60 shadow-none transition-all duration-150 hover:border-cyan-300/25 hover:bg-cyan-300/[0.08] hover:text-cyan-100 focus:ring-0"
      >
        <span className="flex h-full w-full items-center justify-center">
          <Globe className="h-3.5 w-3.5" />
        </span>
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectLabel className="px-2 pt-1.5 pb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/52">
            Region
          </SelectLabel>
          {options.map((opt) => (
            <SelectItem key={opt.value} value={opt.value} className="text-xs font-medium">
              {opt.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

// ─── Viewer toolbar inline (desktop) ─────────────────────────────────────────
function ViewerNavDesktop() {
  const toolbar = useViewerToolbar();
  const settingsRef = useRef<HTMLDivElement>(null);
  const settingsPanelRef = useRef<HTMLDivElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const legendPanelRef = useRef<HTMLDivElement>(null);
  const [legendPanelOpen, setLegendPanelOpen] = useState(false);

  useEffect(() => {
    if (!toolbar?.displayPanelOpen) return;
    function onPointerDown(e: MouseEvent | TouchEvent) {
      if (!(e.target instanceof Node)) return;
      if (settingsRef.current?.contains(e.target)) return;
      if (settingsPanelRef.current?.contains(e.target)) return;
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

  useEffect(() => {
    if (!legendPanelOpen) return;
    function onPointerDown(e: MouseEvent | TouchEvent) {
      if (!(e.target instanceof Node)) return;
      if (legendRef.current?.contains(e.target)) return;
      if (legendPanelRef.current?.contains(e.target)) return;
      setLegendPanelOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setLegendPanelOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [legendPanelOpen]);

  if (!toolbar) return null;

  const {
    variable, onVariableChange, variables, model, onModelChange, models,
    run, onRunChange, runs, region, onRegionChange, regions,
    disabled, runDisplayLabel, hasNewerRunAvailable, latestAvailableRunLabel,
    onViewLatestRun, runSelectionLocked, sourceStatusLabel, sourceStatusDescription,
    sourceStatusTone, runAvailabilityLabel, runAvailabilityDescription, runAvailabilityTone,
    onShare, displayPanelOpen, onDisplayPanelOpenChange,
    pointLabelsEnabled, onPointLabelsEnabledChange,
    basemapMode, onBasemapModeChange, opacity, onOpacityChange,
    zoomControlsVisible, onZoomControlsVisibleChange, legend,
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
  const selectedRegionLabel = regions.find((option) => option.value === region)?.label ?? "Region";

  return (
    /* flex-1 so this fills all space after the logo; spacer pushes controls to the right */
    <div className="flex h-full flex-1 items-end">
      {/* Flex spacer — pushes everything to the right */}
      <div className="flex-1" />

      {/* Controls group: selectors + divider + icons — all right-aligned */}
      <div className="flex shrink-0 items-end gap-1.5">
        {/* Primary selectors */}
        <HeaderSelectField label="Product" icon={Boxes}>
          <NavbarSelect
            value={model}
            onValueChange={onModelChange}
            options={models}
            disabled={disabled}
            placeholder="Model"
            grouped
            minWidth="min-w-[90px] max-w-[140px]"
          />
        </HeaderSelectField>
        <HeaderSelectField label="Variable" icon={Layers}>
          <NavbarSelect
            value={variable}
            onValueChange={onVariableChange}
            options={displayVariables}
            disabled={disabled}
            placeholder="Variable"
            grouped
            minWidth="min-w-[180px] max-w-[320px]"
          />
        </HeaderSelectField>
        <HeaderSelectField label="Run Time" icon={CalendarClock}>
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
            minWidth="min-w-[148px] max-w-[220px]"
          />
        </HeaderSelectField>

        {runAvailabilityLabel ? (
          <AvailabilityReadout
            label={runAvailabilityLabel}
            description={runAvailabilityDescription}
            tone={runAvailabilityTone}
          />
        ) : sourceStatusLabel ? (
          <AvailabilityReadout
            label={sourceStatusLabel}
            description={sourceStatusDescription}
            tone={sourceStatusTone}
          />
        ) : null}

        <RegionUtilitySelect
          value={region}
          onValueChange={onRegionChange}
          options={regions}
          disabled={disabled}
          currentRegionLabel={selectedRegionLabel}
        />

        {/* Legend button */}
        <div className="relative shrink-0" ref={legendRef}>
          <button
            type="button"
            onClick={() => setLegendPanelOpen((v) => !v)}
            aria-expanded={legendPanelOpen}
            title="Legend"
            aria-label="Legend"
            className={cn(
              "inline-flex h-8 w-8 items-center justify-center rounded-xl border transition-all duration-150",
              legendPanelOpen
                ? "border-cyan-300/30 bg-cyan-300/[0.10] text-cyan-100"
                : "border-white/10 bg-white/[0.05] text-white/60 hover:border-cyan-300/25 hover:bg-cyan-300/[0.08] hover:text-cyan-100"
            )}
          >
            <Palette className="h-3.5 w-3.5" />
          </button>

          {legendPanelOpen ? createPortal(
            <div
              ref={legendPanelRef}
              className="fixed right-[3.25rem] top-[3.5rem] z-[70] w-auto min-w-[148px] max-w-[240px] max-h-[calc(100vh-5rem)] overflow-y-auto overflow-x-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md"
            >
              <MapLegend
                legend={legend}
                onOpacityChange={onOpacityChange}
                showOpacityControl={false}
                displayPanelOpen={displayPanelOpen}
                defaultExpanded={true}
                inline={true}
              />
            </div>
          , document.body) : null}
        </div>

        {onShare ? (
          <button
            type="button"
            onClick={onShare}
            title="Share"
            aria-label="Share"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.05] text-white/60 transition-all duration-150 hover:border-cyan-300/25 hover:bg-cyan-300/[0.08] hover:text-cyan-100"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        ) : null}

        {/* Settings / Display panel */}
        <div className="relative shrink-0" ref={settingsRef}>
          <button
            type="button"
            onClick={() => onDisplayPanelOpenChange(!displayPanelOpen)}
            aria-expanded={displayPanelOpen}
            title="Display settings"
            aria-label="Display settings"
            className={cn(
              "inline-flex h-8 w-8 items-center justify-center rounded-xl border transition-all duration-150",
              displayPanelOpen
                ? "border-cyan-300/30 bg-cyan-300/[0.10] text-cyan-100"
                : "border-white/10 bg-white/[0.05] text-white/60 hover:border-cyan-300/25 hover:bg-cyan-300/[0.08] hover:text-cyan-100"
            )}
          >
            <Settings className="h-3.5 w-3.5" />
          </button>

          {displayPanelOpen ? createPortal(
            <div
              ref={settingsPanelRef}
              className="fixed right-4 top-[3.5rem] z-[70] w-[232px] overflow-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md"
            >
              {/* Panel header */}
              <div className="flex items-center justify-between border-b border-[#1a3a5c]/50 px-4 py-3">
                <div>
                  <div className="font-['IBM_Plex_Mono',monospace] text-[9px] font-medium uppercase tracking-[0.22em] text-cyan-300/60">
                    Display
                  </div>
                  <div className="mt-0.5 text-[11px] text-white/52">Map overlays &amp; reference aids</div>
                </div>
                <button
                  type="button"
                  onClick={() => onDisplayPanelOpenChange(false)}
                  className="inline-flex h-6 w-6 items-center justify-center rounded-md text-white/32 transition-colors hover:text-white/72"
                  aria-label="Close display panel"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>

              <div className="space-y-1.5 px-3 py-3">
                <DisplayRow
                  label="City Labels"
                  icon={MapPin}
                  checked={pointLabelsEnabled}
                  onToggle={() => onPointLabelsEnabledChange(!pointLabelsEnabled)}
                />
                <DisplayRow
                  label="Zoom Controls"
                  icon={ZoomIn}
                  checked={zoomControlsVisible}
                  onToggle={() => onZoomControlsVisibleChange(!zoomControlsVisible)}
                />
                <button
                  type="button"
                  onClick={() => onBasemapModeChange(basemapMode === "dark" ? "light" : "dark")}
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

                <div className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-sm font-semibold text-white">Opacity</span>
                    <span className="font-['IBM_Plex_Mono',monospace] text-[10px] font-medium text-cyan-300/80">
                      {Math.round(opacity * 100)}%
                    </span>
                  </div>
                  <Slider
                    value={[Math.round(opacity * 100)]}
                    onValueChange={([v]) => onOpacityChange((v ?? 100) / 100)}
                    min={0}
                    max={100}
                    step={1}
                    className="w-full transition-opacity duration-150 [&>*:first-child]:h-1.5 [&>*:first-child]:bg-white/[0.12] [&>*:first-child>*:first-child]:bg-gradient-to-r [&>*:first-child>*:first-child]:from-cyan-400 [&>*:first-child>*:first-child]:via-sky-300 [&>*:first-child>*:first-child]:to-slate-200"
                  />
                </div>

                <div className="border-t border-white/8 pt-2 text-[10px] leading-relaxed text-white/32">
                  Maps:{" "}
                  <a href="https://www.maplibre.org/" target="_blank" rel="noreferrer" className="underline underline-offset-2 transition-colors hover:text-white/60">MapLibre</a>
                  {" "}·{" "}
                  <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer" className="underline underline-offset-2 transition-colors hover:text-white/60">OSM</a>
                  {" "}·{" "}
                  <a href="https://carto.com/attributions" target="_blank" rel="noreferrer" className="underline underline-offset-2 transition-colors hover:text-white/60">CARTO</a>
                </div>
              </div>
            </div>
          , document.body) : null}
        </div>
      </div>
    </div>
  );
}

// ─── Viewer toolbar mobile/tablet (slide-up sheet) ───────────────────────────
function ViewerNavMobile() {
  const toolbar = useViewerToolbar();
  const [sheetSnap, setSheetSnap] = useState<"closed" | "peek" | "full">("closed");
  const [activeTab, setActiveTab] = useState<"selection" | "display">("selection");
  const dragStartY = useRef<number | null>(null);

  if (!toolbar) return null;

  const {
    variable, onVariableChange, variables, model, onModelChange, models,
    run, onRunChange, runs, region, onRegionChange, regions, disabled,
    runDisplayLabel, hasNewerRunAvailable, latestAvailableRunLabel, onViewLatestRun,
    runSelectionLocked, sourceStatusLabel, sourceStatusDescription, sourceStatusTone,
    runAvailabilityLabel, runAvailabilityDescription, runAvailabilityTone,
    onShare, pointLabelsEnabled, onPointLabelsEnabledChange, legendVisible,
    onLegendVisibleChange, basemapMode, onBasemapModeChange, opacity, onOpacityChange,
    zoomControlsVisible, onZoomControlsVisibleChange, legendPopoverOpen, onLegendPopoverOpenChange,
    layoutMode, legend,
  } = toolbar;

  const isTabletTouchLayout = layoutMode === "tablet-touch";
  const isPhoneLayout = !isTabletTouchLayout;
  const sheetOpen = sheetSnap !== "closed";

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

  useEffect(() => {
    if (!sheetOpen) {
      document.body.style.removeProperty("overflow");
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [sheetOpen]);

  const closeSheet = () => setSheetSnap("closed");

  // Drag-to-snap gesture handlers (phone only)
  const handleDragStart = (e: React.TouchEvent) => {
    dragStartY.current = e.touches[0]?.clientY ?? null;
  };
  const handleDragEnd = (e: React.TouchEvent) => {
    if (dragStartY.current == null) return;
    const deltaY = (e.changedTouches[0]?.clientY ?? 0) - dragStartY.current;
    dragStartY.current = null;
    if (sheetSnap === "peek") {
      if (deltaY < -40) setSheetSnap("full");
      else if (deltaY > 40) closeSheet();
    } else if (sheetSnap === "full") {
      if (deltaY > 60) setSheetSnap("peek");
    }
  };
  const handleHandleClick = () => {
    if (sheetSnap === "peek") setSheetSnap("full");
    else if (sheetSnap === "full") setSheetSnap("peek");
  };

  const summaryPillClass = "rounded-full border border-white/10 bg-white/[0.06] px-2.5 py-1 font-medium whitespace-nowrap";

  const statusBadge = runAvailabilityLabel ? (
    <AvailabilityReadout label={runAvailabilityLabel} description={runAvailabilityDescription} tone={runAvailabilityTone} />
  ) : sourceStatusLabel ? (
    <AvailabilityReadout label={sourceStatusLabel} description={sourceStatusDescription} tone={sourceStatusTone} />
  ) : null;

  const selectionContent = (
    <>
      {statusBadge ? (
        <div className="mb-4 flex items-center">{statusBadge}</div>
      ) : null}
      <div className="grid grid-cols-1 gap-3">
        <div className="space-y-1.5">
          <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-white/44">
            <Boxes className="h-3 w-3" /> Product
          </span>
          <NavbarSelect
            value={model}
            onValueChange={(v) => { onModelChange(v); closeSheet(); }}
            options={models}
            disabled={disabled}
            placeholder="Product"
            grouped
            minWidth="w-full"
          />
        </div>

        <div className="space-y-1.5">
          <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-white/44">
            <Layers className="h-3 w-3" /> Variable
          </span>
          <NavbarSelect
            value={variable}
            onValueChange={(v) => { onVariableChange(v); closeSheet(); }}
            options={displayVariables}
            disabled={disabled}
            placeholder="Variable"
            grouped
            minWidth="w-full"
          />
        </div>

        <div className="space-y-1.5">
          <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-white/44">
            <CalendarClock className="h-3 w-3" /> Run Time
          </span>
          <NavbarSelect
            value={run}
            onValueChange={(v) => { onRunChange(v); closeSheet(); }}
            options={runMenuOptions}
            disabled={disabled || runSelectionLocked}
            placeholder="Run Time"
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
                ? () => { onViewLatestRun?.(); closeSheet(); }
                : undefined
            }
            minWidth="w-full"
          />
        </div>

        <div className="space-y-1.5">
          <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-white/44">
            <MapPin className="h-3 w-3" /> Region
          </span>
          <NavbarSelect
            value={region}
            onValueChange={(v) => { onRegionChange(v); closeSheet(); }}
            options={regions}
            disabled={disabled}
            placeholder="Region"
            minWidth="w-full"
          />
        </div>
      </div>
    </>
  );

  const displayContent = (
    <>
      <div className="grid grid-cols-1 gap-2">
        <DisplayRow
          label="City Labels"
          icon={MapPin}
          checked={pointLabelsEnabled}
          onToggle={() => onPointLabelsEnabledChange(!pointLabelsEnabled)}
        />
        <DisplayRow
          label="Legend"
          icon={Palette}
          checked={legendVisible}
          onToggle={() => onLegendVisibleChange(!legendVisible)}
        />
        <DisplayRow
          label="Zoom Controls"
          icon={ZoomIn}
          checked={zoomControlsVisible}
          onToggle={() => onZoomControlsVisibleChange(!zoomControlsVisible)}
        />
        <button
          type="button"
          onClick={() => onBasemapModeChange(basemapMode === "dark" ? "light" : "dark")}
          className="flex w-full items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-left transition-colors hover:bg-white/[0.07]"
        >
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            {basemapMode === "dark" ? <Moon className="h-4 w-4 text-white/72" /> : <Sun className="h-4 w-4 text-white/72" />}
            Basemap
          </div>
          <span className="text-xs font-semibold text-[#98c9b2]">
            {basemapMode === "dark" ? "Dark" : "Light"}
          </span>
        </button>
      </div>

      <div className="mt-3 rounded-2xl border border-white/10 bg-white/[0.04] px-3.5 py-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-semibold text-white">Opacity</span>
          <span className="font-mono text-[10px] text-white/62">{Math.round(opacity * 100)}%</span>
        </div>
        <Slider
          value={[Math.round(opacity * 100)]}
          onValueChange={([v]) => onOpacityChange((v ?? 100) / 100)}
          min={0}
          max={100}
          step={1}
          className="w-full transition-opacity duration-150 [&>*:first-child]:h-1.5 [&>*:first-child]:bg-white/[0.12] [&>*:first-child>*:first-child]:bg-gradient-to-r [&>*:first-child>*:first-child]:from-cyan-400 [&>*:first-child>*:first-child]:via-sky-300 [&>*:first-child>*:first-child]:to-slate-200"
        />
      </div>
    </>
  );

  return (
    <>
      {/* Compact summary + share icon — controls button is now a floating FAB */}
      <div className="flex flex-1 items-center justify-end gap-2">
        <div className="flex min-w-0 items-center gap-1.5 overflow-x-auto text-[11px] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          <span className={cn(summaryPillClass, "text-white/82")}>
            {selectedVariableLabel}
          </span>
          <span className={cn(summaryPillClass, "text-white/68")}>
            {selectedModelLabel}
          </span>
          <span className={cn(summaryPillClass, "text-white/60")}>
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
      </div>

      {/* Floating controls button — always in top-right, never pushed off-screen */}
      {createPortal(
        <button
          type="button"
          onClick={() => setSheetSnap(sheetOpen ? "closed" : "peek")}
          aria-label={sheetOpen ? "Close controls" : "Open controls"}
          className={cn(
            "glass fixed right-3 top-[calc(3.5rem+0.5rem)] z-[62] inline-flex h-9 w-9 items-center justify-center rounded-xl transition-all duration-150",
            sheetOpen
              ? "bg-white/[0.12] text-white"
              : "text-white/70 hover:bg-white/[0.07] hover:text-white"
          )}
        >
          <Settings className="h-3.5 w-3.5" />
        </button>
      , document.body)}

      {/* Slide-up sheet */}
      {sheetOpen ? createPortal(
        <>
          {/* Backdrop — subtler in peek, full blur when expanded */}
          <div
            className={cn(
              "fixed inset-0 z-[65] transition-[background-color,backdrop-filter] duration-300",
              sheetSnap === "full"
                ? "bg-black/42 backdrop-blur-[6px]"
                : "bg-black/20"
            )}
            onClick={closeSheet}
            aria-hidden="true"
          />

          {/* Sheet panel */}
          <div
            style={isPhoneLayout ? {
              maxHeight: sheetSnap === "full" ? "88svh" : "60svh",
              transition: "max-height 0.35s cubic-bezier(0.32, 0.72, 0, 1)",
            } : undefined}
            className={cn(
              "glass-navy fixed z-[66] flex flex-col overflow-hidden",
              isTabletTouchLayout
                ? "right-3 top-[4.5rem] max-h-[calc(100svh-5.5rem)] w-[min(19rem,56vw)] rounded-[1.4rem]"
                : "bottom-0 left-0 right-0 rounded-t-[1.5rem] [border-left:none] [border-right:none] [border-bottom:none] pb-[env(safe-area-inset-bottom)]"
            )}
          >
            {/* Drag handle — phone only, tap to toggle peek/full */}
            {isPhoneLayout ? (
              <div
                className="flex touch-none select-none justify-center pt-3 pb-1 active:opacity-70"
                onTouchStart={handleDragStart}
                onTouchEnd={handleDragEnd}
                onClick={handleHandleClick}
                aria-label={sheetSnap === "peek" ? "Expand controls" : "Collapse controls"}
                role="button"
              >
                <div className="h-1 w-10 rounded-full bg-white/25" />
              </div>
            ) : null}

            {/* Header: underline tabs + close button */}
            <div className={cn(
              "flex shrink-0 items-center justify-between border-b border-white/[0.08]",
              isTabletTouchLayout ? "px-5 pt-4" : "px-4 pt-2"
            )}>
              <div className="flex">
                <button
                  type="button"
                  onClick={() => setActiveTab("selection")}
                  className={cn(
                    "relative pb-2.5 pr-5 text-sm font-semibold transition-colors duration-150",
                    activeTab === "selection" ? "text-white" : "text-white/40 hover:text-white/65"
                  )}
                >
                  Selection
                  {activeTab === "selection" && (
                    <span className="absolute bottom-0 left-0 right-5 h-[2px] rounded-full bg-cyan-400" />
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("display")}
                  className={cn(
                    "relative pb-2.5 pr-5 text-sm font-semibold transition-colors duration-150",
                    activeTab === "display" ? "text-white" : "text-white/40 hover:text-white/65"
                  )}
                >
                  Display
                  {activeTab === "display" && (
                    <span className="absolute bottom-0 left-0 right-5 h-[2px] rounded-full bg-cyan-400" />
                  )}
                </button>
              </div>

              <button
                type="button"
                onClick={closeSheet}
                className="mb-2 inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-white/5 text-white/60 hover:text-white"
                aria-label="Close controls"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Scrollable content — explicit max-height keeps container content-sized (no dead space) */}
            <div
              style={isPhoneLayout ? {
                maxHeight: sheetSnap === "full" ? "calc(88svh - 5.5rem)" : "calc(60svh - 5.5rem)",
              } : undefined}
              className={cn(
                "overflow-y-auto",
                isTabletTouchLayout ? "max-h-[calc(100svh-10rem)] px-5 pb-5 pt-3" : "px-4 pb-6 pt-3"
              )}
            >
              {activeTab === "selection" ? selectionContent : displayContent}
            </div>
          </div>
        </>
      , document.body) : null}

      {/* Legend popover — opens to the right of the zoom control cluster */}
      {legendVisible && legendPopoverOpen && legend ? createPortal(
        <>
          <div
            className="fixed inset-0 z-[54]"
            onClick={() => onLegendPopoverOpenChange(false)}
            aria-hidden="true"
          />
          <div className="fixed left-[58px] top-[calc(3.5rem+1rem)] z-[55] w-auto min-w-[140px] max-w-[200px] max-h-[calc(100svh-6rem)] overflow-y-auto overflow-x-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md">
            <MapLegend
              legend={legend}
              onOpacityChange={onOpacityChange}
              showOpacityControl={false}
              displayPanelOpen={false}
              defaultExpanded={true}
              inline={true}
            />
          </div>
        </>
      , document.body) : null}
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
    <header className="fixed inset-x-0 top-0 z-[60]">
      {/* Isolated blur layer — own compositor layer, never repaints from map activity */}
      <div
        aria-hidden="true"
        className="absolute inset-0 border-b border-[#1a3a5c]/60 bg-[#030e1a]/[0.85] shadow-[0_2px_16px_rgba(0,0,0,0.4),inset_0_-1px_0_rgba(100,180,255,0.06)] backdrop-blur-md"
        style={{ willChange: "transform" }}
      />
      <div
        className={cn(
          "relative z-10",
          isAppVariant
            ? isViewerDesktop
              ? "flex h-[4.5rem] items-end gap-3 px-4 pb-2 md:px-5"
              : "flex h-14 items-center gap-3 px-4 md:px-5"
            : "mx-auto flex h-16 max-w-6xl items-center gap-3 px-5 md:gap-6 md:px-8"
        )}
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
