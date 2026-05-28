import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import { Show, UserButton, useAuth } from "@clerk/react";
import { createPortal } from "react-dom";
import { NavLink, useLocation } from "react-router-dom";
import {
  Boxes,
  CalendarClock,
  Globe,
  Layers,
  MapPin,
  MessageSquareText,
  Moon,
  Palette,
  Send,
  Settings,
  Sun,
  X,
  ZoomIn,
} from "lucide-react";

import { BRAND_LOGO_SRC } from "@/lib/branding";
import { API_ORIGIN } from "@/lib/config";
import { clerkUserButtonProps } from "@/lib/clerk-appearance";
import { cn } from "@/lib/utils";
import { useFeedbackContext } from "@/lib/feedback-context";
import { useViewerToolbar } from "@/lib/viewer-toolbar-context";
import { clerkJwtTemplate } from "@/lib/admin-api";
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
import { ModelPicker } from "@/components/ModelPicker";
import { VariablePicker } from "@/components/VariablePicker";
import { MapLegend } from "@/components/map-legend";
import type { ObservedSourceStatusTone } from "@/lib/time-axis";
import type { GroupedOption } from "@/lib/app-utils";

// ─── Shared types ────────────────────────────────────────────────────────────
type Option = { value: string; label: string };
type VariableOption = Option & { group: string | null };

const DESKTOP_TOPBAR_POPOVER_OFFSET = 10;
const DESKTOP_TOPBAR_POPOVER_FALLBACK_TOP = 74;
const DESKTOP_TOPBAR_SELECT_CONTENT_CLASSNAME = "data-[side=bottom]:translate-y-0";

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


const GROUP_ORDER = ["MODELS", "ENSEMBLES", "FORECASTS", "OBSERVATIONS", "SURFACE", "PRECIPITATION", "PRECIP ANOMALIES", "SEVERE", "UPPER AIR", "OUTLOOKS", "ENSEMBLE"];

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
  contentOffset?: number;
  contentClassName?: string;
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
    contentOffset,
    contentClassName,
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
      <SelectContent sideOffset={contentOffset} className={contentClassName}>{resolvedContent}</SelectContent>
    </Select>
  );
}

function HeaderSelectField({
  label,
  icon: Icon,
  children,
  tourTarget,
}: {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  tourTarget?: string;
}) {
  return (
    <div className="flex flex-col gap-1" {...(tourTarget ? { "data-tour-target": tourTarget } : {})}>
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
  contentOffset,
  contentClassName,
  tourTarget,
}: {
  value: string;
  onValueChange: (value: string) => void;
  options: Option[];
  disabled?: boolean;
  currentRegionLabel: string;
  contentOffset?: number;
  contentClassName?: string;
  tourTarget?: string;
}) {
  return (
    <div className="shrink-0" {...(tourTarget ? { "data-tour-target": tourTarget } : {})}>
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
      <SelectContent sideOffset={contentOffset} className={contentClassName}>
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
    </div>
  );
}

// ─── Viewer toolbar inline (desktop) ─────────────────────────────────────────
function ViewerNavDesktop({ onFeedback }: { onFeedback?: () => void }) {
  const toolbar = useViewerToolbar();
  const settingsRef = useRef<HTMLDivElement>(null);
  const settingsPanelRef = useRef<HTMLDivElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const legendPanelRef = useRef<HTMLDivElement>(null);
  const [legendPanelOpen, setLegendPanelOpen] = useState(false);
  const [settingsPanelTop, setSettingsPanelTop] = useState<number>(DESKTOP_TOPBAR_POPOVER_FALLBACK_TOP);
  const [legendPanelTop, setLegendPanelTop] = useState<number>(DESKTOP_TOPBAR_POPOVER_FALLBACK_TOP);

  const updateSettingsPanelTop = useCallback(() => {
    const rect = settingsRef.current?.getBoundingClientRect();
    if (!rect) return;
    setSettingsPanelTop(rect.bottom + DESKTOP_TOPBAR_POPOVER_OFFSET);
  }, []);

  const updateLegendPanelTop = useCallback(() => {
    const rect = legendRef.current?.getBoundingClientRect();
    if (!rect) return;
    setLegendPanelTop(rect.bottom + DESKTOP_TOPBAR_POPOVER_OFFSET);
  }, []);

  useEffect(() => {
    if (!toolbar?.displayPanelOpen) return;
    updateSettingsPanelTop();
    function onPointerDown(e: MouseEvent | TouchEvent) {
      if (!(e.target instanceof Node)) return;
      if (settingsRef.current?.contains(e.target)) return;
      if (settingsPanelRef.current?.contains(e.target)) return;
      toolbar?.onDisplayPanelOpenChange(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") toolbar?.onDisplayPanelOpenChange(false);
    }
    window.addEventListener("resize", updateSettingsPanelTop);
    window.addEventListener("scroll", updateSettingsPanelTop, true);
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", updateSettingsPanelTop);
      window.removeEventListener("scroll", updateSettingsPanelTop, true);
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [toolbar?.displayPanelOpen, updateSettingsPanelTop]);

  useEffect(() => {
    if (!legendPanelOpen) return;
    updateLegendPanelTop();
    function onPointerDown(e: MouseEvent | TouchEvent) {
      if (!(e.target instanceof Node)) return;
      if (legendRef.current?.contains(e.target)) return;
      if (legendPanelRef.current?.contains(e.target)) return;
      setLegendPanelOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setLegendPanelOpen(false);
    }
    window.addEventListener("resize", updateLegendPanelTop);
    window.addEventListener("scroll", updateLegendPanelTop, true);
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", updateLegendPanelTop);
      window.removeEventListener("scroll", updateLegendPanelTop, true);
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [legendPanelOpen, updateLegendPanelTop]);

  if (!toolbar) return null;

  const {
    variable, onVariableChange, variables, variableCatalog, supportedVariableIds, model, onModelChange, models,
    run, onRunChange, runs, region, onRegionChange, regions,
    disabled, runDisplayLabel, hasNewerRunAvailable, latestAvailableRunLabel,
    onViewLatestRun, runSelectionLocked, sourceStatusLabel, sourceStatusDescription,
    sourceStatusTone, runAvailabilityLabel, runAvailabilityDescription, runAvailabilityTone,
    onShare, displayPanelOpen, onDisplayPanelOpenChange,
    pointLabelsEnabled, onPointLabelsEnabledChange,
    basemapMode, onBasemapModeChange, opacity, onOpacityChange,
    zoomControlsVisible, onZoomControlsVisibleChange, legend,
  } = toolbar;

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
        <HeaderSelectField label="Product" icon={Boxes} tourTarget="product-selector">
          <ModelPicker
            value={model}
            onChange={onModelChange}
            options={models}
            disabled={disabled}
            placeholder="Model"
            minWidth="min-w-[180px] max-w-[220px]"
            panelOffset={DESKTOP_TOPBAR_POPOVER_OFFSET}
          />
        </HeaderSelectField>
        <HeaderSelectField label="Variable" icon={Layers} tourTarget="variable-picker">
          <VariablePicker
            modelId={model}
            value={variable}
            onChange={onVariableChange}
            variableCatalog={model === "spc" ? variableCatalog.map((o) => ({ ...o, label: spcVariableLabel(o) })) : variableCatalog}
            supportedVariableIds={supportedVariableIds}
            disabled={disabled}
            placeholder="Variable"
            legend={legend}
            minWidth="min-w-[180px] max-w-[320px]"
            panelOffset={DESKTOP_TOPBAR_POPOVER_OFFSET}
          />
        </HeaderSelectField>
        <HeaderSelectField label="Run Time" icon={CalendarClock} tourTarget="run-selector">
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
            contentOffset={DESKTOP_TOPBAR_POPOVER_OFFSET}
            contentClassName={DESKTOP_TOPBAR_SELECT_CONTENT_CLASSNAME}
          />
        </HeaderSelectField>

        <div data-tour-target="freshness-indicator" className="flex items-center">
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
        </div>

        <RegionUtilitySelect
          value={region}
          onValueChange={onRegionChange}
          options={regions}
          disabled={disabled}
          currentRegionLabel={selectedRegionLabel}
          contentOffset={DESKTOP_TOPBAR_POPOVER_OFFSET}
          contentClassName={DESKTOP_TOPBAR_SELECT_CONTENT_CLASSNAME}
          tourTarget="region-selector"
        />

        {/* Legend button */}
        <div className="relative shrink-0" ref={legendRef} data-tour-target="legend-button">
          <button
            type="button"
            onClick={() => {
              updateLegendPanelTop();
              setLegendPanelOpen((v) => !v);
            }}
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
              className="fixed right-[3.25rem] z-[70] w-[220px] max-h-[calc(100vh-5rem)] overflow-y-auto overflow-x-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md"
              style={{ top: legendPanelTop }}
            >
              <MapLegend
                legend={legend}
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
            data-tour-target="share-button"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.05] text-white/60 transition-all duration-150 hover:border-cyan-300/25 hover:bg-cyan-300/[0.08] hover:text-cyan-100"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        ) : null}

        {onFeedback ? (
          <button
            type="button"
            onClick={onFeedback}
            title="Send feedback"
            aria-label="Send feedback"
            data-tour-target="feedback-button"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.05] text-white/60 transition-all duration-150 hover:border-cyan-300/25 hover:bg-cyan-300/[0.08] hover:text-cyan-100"
          >
            <MessageSquareText className="h-3.5 w-3.5" />
          </button>
        ) : null}

        {/* Settings / Display panel */}
        <div className="relative shrink-0" ref={settingsRef} data-tour-target="display-settings-button">
          <button
            type="button"
            onClick={() => {
              updateSettingsPanelTop();
              onDisplayPanelOpenChange(!displayPanelOpen);
            }}
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
              className="fixed right-4 z-[70] w-[232px] overflow-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md"
              style={{ top: settingsPanelTop }}
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

                {toolbar.onReplayTour ? (
                  <button
                    type="button"
                    onClick={() => {
                      toolbar.onReplayTour?.();
                      onDisplayPanelOpenChange(false);
                    }}
                    className="flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-left transition-all duration-150 hover:bg-white/[0.07]"
                  >
                    <span className="text-sm font-semibold text-white">Replay Tour</span>
                    <span className="font-['IBM_Plex_Mono',monospace] text-[10px] font-medium text-cyan-300/70">?</span>
                  </button>
                ) : null}

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
function ViewerNavMobile({ onFeedback }: { onFeedback?: () => void }) {
  const toolbar = useViewerToolbar();
  const [sheetSnap, setSheetSnap] = useState<"closed" | "peek" | "full">("closed");
  const [activeTab, setActiveTab] = useState<"selection" | "display">("selection");
  const [mobileModelPickerOpen, setMobileModelPickerOpen] = useState(false);
  const [mobileVariablePickerOpen, setMobileVariablePickerOpen] = useState(false);
  const dragStartY = useRef<number | null>(null);
  const pickerReturnSnap = useRef<"peek" | "full" | null>(null);

  if (!toolbar) return null;

  const {
    variable, onVariableChange, variables, variableCatalog, supportedVariableIds, model, onModelChange, models,
    run, onRunChange, runs, region, onRegionChange, regions, disabled,
    runDisplayLabel, hasNewerRunAvailable, latestAvailableRunLabel, onViewLatestRun,
    runSelectionLocked, onShare, pointLabelsEnabled, onPointLabelsEnabledChange, legendVisible,
    onLegendVisibleChange, basemapMode, onBasemapModeChange, opacity, onOpacityChange,
    zoomControlsVisible, onZoomControlsVisibleChange, legendPopoverOpen, onLegendPopoverOpenChange,
    layoutMode, legend, mobileControlsOpen, onMobileControlsOpenChange,
  } = toolbar;

  const isTabletTouchLayout = layoutMode === "tablet-touch";
  const isPhoneLayout = !isTabletTouchLayout;
  const sheetOpen = sheetSnap !== "closed";
  const mobilePickerOpen = mobileModelPickerOpen || mobileVariablePickerOpen;

  // Sync external open requests (e.g. from the bottom bar) into local sheetSnap
  useEffect(() => {
    if (mobileControlsOpen && sheetSnap === "closed") {
      setSheetSnap("peek");
    } else if (!mobileControlsOpen && sheetSnap !== "closed") {
      setSheetSnap("closed");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mobileControlsOpen]);

  const displayVariables = model === "spc"
    ? variables.map((o) => ({ ...o, label: spcVariableLabel(o) }))
    : variables;

  const runMenuOptions = hasNewerRunAvailable
    ? runs.filter((o) => o.value !== "latest")
    : runs;

  const selectedVariableLabel = displayVariables.find((o) => o.value === variable)?.label ?? "Variable";
  const selectedModelLabel = models.find((o) => o.value === model)?.label ?? "Model";

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

  const closeSheet = () => {
    setSheetSnap("closed");
    pickerReturnSnap.current = null;
    setMobileModelPickerOpen(false);
    setMobileVariablePickerOpen(false);
    onMobileControlsOpenChange?.(false);
  };

  const restorePickerReturnSnap = () => {
    const returnSnap = pickerReturnSnap.current;
    pickerReturnSnap.current = null;
    if (returnSnap) {
      setSheetSnap(returnSnap);
    }
  };

  const rememberPickerReturnSnap = () => {
    if (!mobilePickerOpen && pickerReturnSnap.current == null) {
      pickerReturnSnap.current = sheetSnap === "closed" ? "peek" : sheetSnap;
    }
  };

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

  const selectionContent = (
    <>
      <div className="flex h-full min-h-0 flex-col gap-3">
        <div data-tour-target="mobile-product-row" className={cn("space-y-1.5", mobileModelPickerOpen ? "min-h-0 flex-1" : "") }>
          <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-white/44">
            <Boxes className="h-3 w-3" /> Product
          </span>
          <ModelPicker
            value={model}
            onChange={(nextModel) => { onModelChange(nextModel); closeSheet(); }}
            options={models}
            disabled={disabled}
            placeholder="Product"
            minWidth="w-full"
            inlinePanel={isPhoneLayout}
            inlinePanelClassName="max-h-[calc(90dvh-12rem)]"
            onOpenChange={(nextOpen) => {
              if (!isPhoneLayout) {
                setMobileModelPickerOpen(false);
                return;
              }
              setMobileModelPickerOpen(nextOpen);
              if (nextOpen) {
                rememberPickerReturnSnap();
                setMobileVariablePickerOpen(false);
                setSheetSnap("full");
              } else if (!mobileVariablePickerOpen) {
                restorePickerReturnSnap();
              }
            }}
          />
        </div>

        <div data-tour-target="mobile-variable-row" className={cn("space-y-1.5", mobileModelPickerOpen ? "hidden" : mobileVariablePickerOpen ? "min-h-0 flex-1" : "") }>
          <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-white/44">
            <Layers className="h-3 w-3" /> Variable
          </span>
          <VariablePicker
            modelId={model}
            value={variable}
            onChange={(v) => { onVariableChange(v); closeSheet(); }}
            variableCatalog={model === "spc" ? variableCatalog.map((o) => ({ ...o, label: spcVariableLabel(o) })) : variableCatalog}
            supportedVariableIds={supportedVariableIds}
            disabled={disabled}
            placeholder="Variable"
            selectedLabelOverride={selectedVariableLabel}
            legend={legend}
            minWidth="w-full"
            inlinePanel={isPhoneLayout}
            inlinePanelClassName="max-h-[calc(90dvh-17rem)]"
            onOpenChange={(open) => {
              if (!isPhoneLayout) {
                setMobileVariablePickerOpen(false);
                return;
              }
              setMobileVariablePickerOpen(open);
              if (open) {
                rememberPickerReturnSnap();
                setMobileModelPickerOpen(false);
                setSheetSnap("full");
              } else if (!mobileModelPickerOpen) {
                restorePickerReturnSnap();
              }
            }}
          />
        </div>

        <div data-tour-target="mobile-run-row" className={cn("space-y-1.5", mobilePickerOpen ? "hidden" : "") }>
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

      {toolbar.onReplayTour ? (
        <button
          type="button"
          onClick={() => {
            toolbar.onReplayTour?.();
            closeSheet();
          }}
          className="mt-2 flex w-full items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-left transition-colors hover:bg-white/[0.07]"
        >
          <span className="text-sm font-semibold text-white">Replay Tour</span>
          <span className="text-xs font-medium text-cyan-300/70">?</span>
        </button>
      ) : null}
    </>
  );

  return (
    <>
      {/* Spacer so logo stays left-aligned with nothing on the right */}
      <div className="flex-1" />

      {onFeedback ? (
        <button
          type="button"
          onClick={onFeedback}
          title="Send feedback"
          aria-label="Send feedback"
          data-tour-target="feedback-button"
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.05] text-white/60 transition-all duration-150 hover:border-cyan-300/25 hover:bg-cyan-300/[0.08] hover:text-cyan-100"
        >
          <MessageSquareText className="h-3.5 w-3.5" />
        </button>
      ) : null}

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
            data-tour-target="mobile-bottom-sheet"
            style={isPhoneLayout ? {
              maxHeight: sheetSnap === "full" ? "90dvh" : "60dvh",
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
                maxHeight: sheetSnap === "full" ? "calc(90dvh - 5.5rem)" : "calc(60dvh - 5.5rem)",
              } : undefined}
              className={cn(
                "min-h-0",
                mobilePickerOpen ? "flex flex-1 flex-col overflow-hidden" : "overflow-y-auto",
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
          <div className="fixed left-[58px] top-[calc(3.5rem+1rem)] z-[55] w-[220px] max-h-[calc(100svh-6rem)] overflow-y-auto overflow-x-hidden rounded-2xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.88] shadow-[0_16px_48px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md">
            <MapLegend
              legend={legend}
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
  const [adminEnabled, setAdminEnabled] = useState(false);
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const location = useLocation();
  const menuRef = useRef<HTMLDivElement | null>(null);
  const toolbar = useViewerToolbar();
  const { openFeedback } = useFeedbackContext();

  const isAppVariant = variant === "app";
  const isMarketingVariant = variant === "marketing";
  const isViewerRoute = location.pathname === "/viewer";
  const showAppNav = isAppVariant && !isViewerRoute;
  const isViewerDesktop = isViewerRoute && (toolbar?.layoutMode === "desktop" || toolbar?.layoutMode === undefined);
  const isViewerMobile = isViewerRoute && !isViewerDesktop;

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function loadAdminStatus() {
      if (!isLoaded || !isSignedIn) {
        setAdminEnabled(false);
        return;
      }

      try {
        const token = await getToken({ template: clerkJwtTemplate() });
        if (!token) {
          if (!cancelled) setAdminEnabled(false);
          return;
        }

        const response = await fetch(`${API_ORIGIN}/api/v4/auth/me`, {
          method: "GET",
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Admin auth check failed (${response.status})`);
        }
        const body = (await response.json()) as { is_admin?: boolean };
        if (!cancelled) setAdminEnabled(body.is_admin === true);
      } catch (error: unknown) {
        if ((error as { name?: string } | undefined)?.name === "AbortError") return;
        if (!cancelled) setAdminEnabled(false);
      }
    }

    void loadAdminStatus();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [getToken, isLoaded, isSignedIn]);

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
    <header className="fixed inset-x-0 top-0 z-[80]">
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
          <ViewerNavDesktop onFeedback={openFeedback} />
        ) : null}

        {/* Viewer route — mobile compact controls */}
        {isViewerRoute && isViewerMobile ? (
          <ViewerNavMobile onFeedback={openFeedback} />
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
            <button
              type="button"
              onClick={openFeedback}
              title="Send feedback"
              aria-label="Send feedback"
              className="rounded-md px-3 py-1.5 text-sm font-medium text-white/70 transition hover:bg-white/10 hover:text-white"
            >
              Feedback
            </button>
            <Show when="signed-out">
              <NavLink
                to="/login"
                className="ml-3 rounded-lg px-2 py-2 text-sm text-white/62 transition duration-150 hover:text-white/88"
              >
                Login
              </NavLink>
            </Show>
            <Show when="signed-in">
              <div className="ml-3 flex h-9 items-center">
                <UserButton {...clerkUserButtonProps} />
              </div>
            </Show>
          </nav>
        ) : null}

        {/* App nav (non-viewer app routes) */}
        {showAppNav ? (
          <nav className="ml-auto hidden items-center gap-1 md:flex">
            <NavItem to="/viewer" label="Viewer" />
            {adminEnabled ? <NavItem to="/admin" label="Admin" /> : null}
            <button
              type="button"
              onClick={openFeedback}
              title="Send feedback"
              aria-label="Send feedback"
              className="rounded-md px-3 py-1.5 text-sm font-medium text-white/70 transition hover:bg-white/10 hover:text-white"
            >
              Feedback
            </button>
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
                  <button
                    type="button"
                    onClick={() => { setMobileMenuOpen(false); openFeedback(); }}
                    className="flex w-full items-center rounded-md px-3 py-1.5 text-left text-sm font-medium text-white/90 transition hover:bg-white/10 hover:text-white"
                  >
                    Feedback
                  </button>
                  <Show when="signed-out">
                    <NavItem to="/login" label="Login" onClick={() => setMobileMenuOpen(false)} className="text-white/90 hover:text-white" />
                  </Show>
                  <Show when="signed-in">
                    <div className="flex items-center justify-between rounded-md px-3 py-2">
                      <span className="text-sm font-medium text-white/90">Account</span>
                      <UserButton {...clerkUserButtonProps} />
                    </div>
                  </Show>
                </div>
              </nav>
            ) : null}
          </div>
        ) : null}
      </div>
    </header>
  );
}
