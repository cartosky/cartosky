import { useState, type ComponentType, type ReactNode } from "react";
import {
  Boxes,
  CalendarClock,
  ChevronDown,
  Layers,
  MapPin,
  Send,
  SlidersHorizontal,
} from "lucide-react";

import type { BasemapMode } from "@/components/map-canvas";
import type { ViewerLayoutMode } from "@/lib/viewer-layout";
import { cn } from "@/lib/utils";
import { Slider } from "@/components/ui/slider";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
} from "@/components/ui/select";

type Option = {
  value: string;
  label: string;
};

type VariableOption = Option & {
  group: string | null;
};

type WeatherToolbarProps = {
  region: string;
  onRegionChange: (value: string) => void;
  model: string;
  onModelChange: (value: string) => void;
  run: string;
  onRunChange: (value: string) => void;
  variable: string;
  onVariableChange: (value: string) => void;
  regions: Option[];
  models: Option[];
  runs: Option[];
  variables: VariableOption[];
  disabled?: boolean;
  pointLabelsEnabled: boolean;
  onPointLabelsEnabledChange: (next: boolean) => void;
  legendVisible: boolean;
  onLegendVisibleChange: (next: boolean) => void;
  basemapMode: BasemapMode;
  onBasemapModeChange: (next: BasemapMode) => void;
  opacity: number;
  onOpacityChange: (next: number) => void;
  onPostToTwf?: () => void;
  layoutMode?: ViewerLayoutMode;
  runDisplayLabel?: string;
  latestAvailableRunLabel?: string | null;
  hasNewerRunAvailable?: boolean;
  onViewLatestRun?: () => void;
};

function ToolbarSelect(props: {
  label: string;
  icon: ComponentType<{ className?: string }>;
  value: string;
  onValueChange: (value: string) => void;
  options: (Option | VariableOption)[];
  disabled?: boolean;
  placeholder: string;
  grouped?: boolean;
  triggerClassName?: string;
  hideLabel?: boolean;
  selectedLabelOverride?: string;
  highlightState?: boolean;
  triggerBadgeLabel?: string | null;
  menuActionLabel?: string | null;
  onMenuAction?: () => void;
}) {
  const {
    label,
    icon: Icon,
    value,
    onValueChange,
    options,
    disabled,
    placeholder,
    grouped,
    triggerClassName,
    hideLabel = false,
    selectedLabelOverride,
    highlightState = false,
    triggerBadgeLabel,
    menuActionLabel,
    onMenuAction,
  } = props;
  const selectedLabel = selectedLabelOverride ?? options.find((opt) => opt.value === value)?.label ?? placeholder;

  let content: ReactNode;
  if (grouped) {
    const GROUP_ORDER = ["Radar & Precipitation Type", "Temperature", "Precipitation", "Wind"];
    const groups = new Map<string, Option[]>();
    const ungrouped: Option[] = [];
    for (const opt of options) {
      const g = "group" in opt && typeof opt.group === "string" ? opt.group : null;
      if (g) {
        let list = groups.get(g);
        if (!list) {
          list = [];
          groups.set(g, list);
        }
        list.push(opt);
      } else {
        ungrouped.push(opt);
      }
    }
    const orderedGroups = GROUP_ORDER.filter((g) => groups.has(g));
    for (const g of groups.keys()) {
      if (!orderedGroups.includes(g)) {
        orderedGroups.push(g);
      }
    }
    content = (
      <>
        {orderedGroups.map((g) => (
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

  const resolvedContent = menuActionLabel && onMenuAction ? (
    <>
      <button
        type="button"
        onClick={onMenuAction}
        className="flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-xs font-semibold text-emerald-50 transition-colors duration-150 hover:bg-white/10"
      >
        <span>{menuActionLabel}</span>
        <span className="rounded-full border border-emerald-300/24 bg-emerald-300/12 px-1.5 py-0.5 text-[10px] uppercase tracking-[0.16em] text-emerald-100">
          Latest
        </span>
      </button>
      <SelectSeparator className="my-1 bg-white/10" />
      {content}
    </>
  ) : content;

  return (
    <div className="flex flex-col gap-1">
      {!hideLabel ? (
        <span className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/80">
          <Icon className="h-3 w-3 opacity-70" />
          {label}
        </span>
      ) : null}
      <Select value={value} onValueChange={onValueChange} disabled={disabled || options.length === 0}>
        <SelectTrigger
          className={cn(
            "h-[30px] w-full border-border/50 bg-secondary/40 px-2.5 text-[12px] font-medium text-foreground shadow-sm transition-all duration-150 hover:border-border hover:bg-secondary/60 focus:border-primary/50 focus:ring-1 focus:ring-primary/30 [&>span]:line-clamp-none",
            highlightState ? "border-emerald-300/25 bg-emerald-300/10 text-white hover:bg-emerald-300/14" : "",
            triggerClassName
          )}
        >
          <span className="flex min-w-0 items-center gap-2 pr-1">
            <span className="truncate whitespace-nowrap">{selectedLabel}</span>
            {triggerBadgeLabel ? (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-emerald-300/24 bg-emerald-300/12 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.16em] text-emerald-100">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-300" />
                {triggerBadgeLabel}
              </span>
            ) : null}
          </span>
        </SelectTrigger>
        <SelectContent>{resolvedContent}</SelectContent>
      </Select>
    </div>
  );
}

function DisplayToggle(props: {
  label: string;
  description?: string;
  checked: boolean;
  onToggle: () => void;
}) {
  const { label, description, checked, onToggle } = props;

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={checked}
      className={cn(
        "flex w-full items-center justify-between gap-3 rounded-lg border border-white/10 px-3 py-2 text-left transition-all duration-150",
        checked
          ? "bg-white/12 text-white hover:bg-white/18"
          : "bg-black/18 text-white/88 hover:bg-black/28"
      )}
    >
      <div className="min-w-0">
        <div className="text-sm font-semibold">{label}</div>
        {description ? <div className="text-[11px] text-white/58">{description}</div> : null}
      </div>
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "text-[10px] font-semibold uppercase tracking-[0.18em] transition-colors duration-150",
            checked ? "text-[#98c9b2]" : "text-white/62"
          )}
        >
          {checked ? "On" : "Off"}
        </span>
        <div
          className={cn(
            "relative h-5 w-9 shrink-0 rounded-full border transition-all duration-150",
            checked ? "border-[#4f7264] bg-[#354d42]" : "border-white/35 bg-white/12"
          )}
        >
          <span
            className={cn(
              "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-[0_1px_4px_rgba(0,0,0,0.38)] transition-transform duration-150",
              checked ? "translate-x-[18px]" : "translate-x-0.5"
            )}
          />
        </div>
      </div>
    </button>
  );
}

function ShareButton({ onClick, compact = false }: { onClick: () => void; compact?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 border border-emerald-300/25 bg-[linear-gradient(to_top_right,#1f342f_0%,#526d5c_100%)] font-semibold text-emerald-50 shadow-sm transition-all duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-300/45",
        compact ? "h-9 rounded-full px-3.5 text-xs" : "h-8 rounded-md px-2.5 text-xs"
      )}
      title="Share"
      aria-label="Share"
    >
      <Send className={cn(compact ? "h-4 w-4" : "h-3.5 w-3.5")} />
      Share
    </button>
  );
}

export function WeatherToolbar(props: WeatherToolbarProps) {
  const {
    region,
    onRegionChange,
    model,
    onModelChange,
    run,
    onRunChange,
    variable,
    onVariableChange,
    regions,
    models,
    runs,
    variables,
    disabled = false,
    pointLabelsEnabled,
    onPointLabelsEnabledChange,
    legendVisible,
    onLegendVisibleChange,
    basemapMode,
    onBasemapModeChange,
    opacity,
    onOpacityChange,
    onPostToTwf,
    layoutMode = "desktop",
    runDisplayLabel,
    latestAvailableRunLabel,
    hasNewerRunAvailable = false,
    onViewLatestRun,
  } = props;
  const [mobilePanelOpen, setMobilePanelOpen] = useState(false);
  const isDesktopLayout = layoutMode === "desktop";
  const isTabletTouchLayout = layoutMode === "tablet-touch";
  const isCompactTouchLayout = !isDesktopLayout;

  const selectedModelLabel = models.find((opt) => opt.value === model)?.label ?? "Model";
  const selectedVariableLabel = variables.find((opt) => opt.value === variable)?.label ?? "Variable";
  const selectedRunLabel = (runDisplayLabel ?? runs.find((opt) => opt.value === run)?.label ?? "Run").replace(
    /^Latest\s*\((.*)\)$/,
    "$1"
  );

  return (
    <header role="toolbar" aria-label="Weather model controls" className="fixed top-[4.35rem] z-50 w-full px-3 sm:px-4">
      <div className={isDesktopLayout ? "block" : "hidden"}>
        <div className="flex items-start">
          <div className="glass-strong inline-flex items-center gap-2 rounded-full border border-white/12 px-3 py-2.5 shadow-[0_18px_40px_rgba(0,0,0,0.34)]">
            <ToolbarSelect
              label="Region"
              icon={MapPin}
              value={region}
              onValueChange={onRegionChange}
              options={regions}
              disabled={disabled}
              placeholder="Region"
              hideLabel
              triggerClassName="min-w-[132px] max-w-[132px] rounded-full border-white/10 bg-white/8 px-3"
            />

            <ToolbarSelect
              label="Model"
              icon={Boxes}
              value={model}
              onValueChange={onModelChange}
              options={models}
              disabled={disabled}
              placeholder="Model"
              hideLabel
              triggerClassName="min-w-[118px] max-w-[118px] rounded-full border-white/10 bg-white/8 px-3"
            />

            <ToolbarSelect
              label="Run"
              icon={CalendarClock}
              value={run}
              onValueChange={onRunChange}
              options={runs}
              disabled={disabled}
              placeholder="Run"
              hideLabel
              selectedLabelOverride={runDisplayLabel}
              highlightState={hasNewerRunAvailable}
              triggerBadgeLabel={hasNewerRunAvailable ? "New" : null}
              menuActionLabel={
                hasNewerRunAvailable && latestAvailableRunLabel
                  ? `Switch to latest (${latestAvailableRunLabel})`
                  : null
              }
              onMenuAction={hasNewerRunAvailable ? onViewLatestRun : undefined}
              triggerClassName="min-w-[142px] max-w-[142px] rounded-full border-white/10 bg-white/8 px-3"
            />

            <ToolbarSelect
              label="Variable"
              icon={Layers}
              value={variable}
              onValueChange={onVariableChange}
              options={variables}
              disabled={disabled}
              placeholder="Variable"
              grouped
              hideLabel
              triggerClassName="min-w-[248px] max-w-[248px] rounded-full border-white/10 bg-white/8 px-3"
            />
          </div>

        </div>
      </div>

      <div
        className={cn(
          "glass-overlay relative rounded-2xl px-3 py-2.5 pb-2",
          isDesktopLayout ? "hidden" : "block",
          isTabletTouchLayout ? "mr-auto ml-0 w-fit max-w-[min(90vw,620px)]" : ""
        )}
      >
        <div className="flex items-center justify-start gap-2">
          <button
            type="button"
            onClick={() => setMobilePanelOpen((value) => !value)}
            className={cn(
              "inline-flex h-9 items-center gap-2 rounded-md border px-3 text-xs font-semibold transition-all duration-150",
              mobilePanelOpen
                ? "border-white/20 bg-white/14 text-white"
                : "border-white/10 bg-black/25 text-white/88 hover:bg-black/35"
            )}
            aria-expanded={mobilePanelOpen}
            aria-controls="mobile-layers-panel"
          >
            <SlidersHorizontal className="h-4 w-4" />
            Layers
            <ChevronDown className={cn("h-4 w-4 transition-transform duration-150", mobilePanelOpen ? "rotate-180" : "")} />
          </button>

          {isCompactTouchLayout ? (
            <div className="min-w-0 flex flex-1 items-center gap-1 overflow-x-auto text-[11px] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
              <span className="rounded-full border border-white/10 bg-white/8 px-2 py-1 font-medium text-white/68">
                {selectedRunLabel}
              </span>
              <span className="rounded-full border border-white/10 bg-white/8 px-2 py-1 font-medium text-white/82">
                {selectedModelLabel}
              </span>
              <span className="max-w-[180px] truncate rounded-full border border-white/10 bg-white/8 px-2 py-1 font-medium text-white/74">
                {selectedVariableLabel}
              </span>
            </div>
          ) : null}
        </div>

        {mobilePanelOpen ? (
          <div
            id="mobile-layers-panel"
            className="glass-overlay-section mt-3 mb-3 rounded-2xl px-3 py-3"
          >
            <div className="grid grid-cols-1 gap-3">
              <ToolbarSelect
                label="Region"
                icon={MapPin}
                value={region}
                onValueChange={onRegionChange}
                options={regions}
                disabled={disabled}
                placeholder="Region"
              />

              <ToolbarSelect
                label="Model"
                icon={Boxes}
                value={model}
                onValueChange={onModelChange}
                options={models}
                disabled={disabled}
                placeholder="Model"
              />

              <ToolbarSelect
                label="Run"
                icon={CalendarClock}
                value={run}
                onValueChange={onRunChange}
                options={runs}
                disabled={disabled}
                placeholder="Run"
                selectedLabelOverride={runDisplayLabel}
                highlightState={hasNewerRunAvailable}
                triggerBadgeLabel={hasNewerRunAvailable ? "New" : null}
                menuActionLabel={
                  hasNewerRunAvailable && latestAvailableRunLabel
                    ? `Switch to latest (${latestAvailableRunLabel})`
                    : null
                }
                onMenuAction={hasNewerRunAvailable ? onViewLatestRun : undefined}
              />

              <ToolbarSelect
                label="Variable"
                icon={Layers}
                value={variable}
                onValueChange={onVariableChange}
                options={variables}
                disabled={disabled}
                placeholder="Variable"
                grouped
              />
            </div>

            <div className="mt-4 border-t border-white/10 pt-3">
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/48">
                Map Display
              </div>
              <div className="space-y-2">
                <DisplayToggle
                  label="City Labels"
                  checked={pointLabelsEnabled}
                  onToggle={() => onPointLabelsEnabledChange(!pointLabelsEnabled)}
                />
                <DisplayToggle
                  label="Legend"
                  checked={legendVisible}
                  onToggle={() => onLegendVisibleChange(!legendVisible)}
                />
                <DisplayToggle
                  label="Dark Mode"
                  checked={basemapMode === "dark"}
                  onToggle={() => onBasemapModeChange(basemapMode === "dark" ? "light" : "dark")}
                />
                <div className="rounded-lg border border-white/10 bg-black/18 px-3 py-2">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-[11px] font-semibold text-white">Opacity</span>
                    <span className="font-mono text-[10px] text-white/62">{Math.round(opacity * 100)}%</span>
                  </div>
                  <Slider
                    value={[Math.round(opacity * 100)]}
                    onValueChange={([value]) => onOpacityChange((value ?? 100) / 100)}
                    min={0}
                    max={100}
                    step={1}
                    className="w-full [&>*:first-child]:h-2 [&>*:first-child]:bg-secondary/55 [&>*:nth-child(2)]:h-4 [&>*:nth-child(2)]:w-4"
                  />
                </div>
              </div>
            </div>

            {onPostToTwf && isCompactTouchLayout ? (
              <div className="mt-4 border-t border-white/10 pt-3">
                <ShareButton onClick={onPostToTwf} compact />
              </div>
            ) : null}

            <div className="mt-4 border-t border-white/10 pt-3 text-[10px] leading-relaxed text-white/52">
              Maps:{" "}
              <a href="https://www.maplibre.org/" target="_blank" rel="noreferrer" className="underline underline-offset-2">
                MapLibre
              </a>
              {" "}|
              {" "}
              <a
                href="https://www.openstreetmap.org/copyright"
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-2"
              >
                OSM
              </a>
              {" "}|
              {" "}
              <a href="https://carto.com/attributions" target="_blank" rel="noreferrer" className="underline underline-offset-2">
                CARTO
              </a>
            </div>
          </div>
        ) : null}
      </div>
    </header>
  );
}
