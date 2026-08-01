import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronLeft, ChevronRight, X } from "lucide-react";

import { MapLegend } from "@/components/map-legend";
import { ModelPicker } from "@/components/ModelPicker";
import { SpeedButton } from "@/components/SpeedButton";
import { CoverageControl } from "@/components/CoverageControl";
import { StatisticPicker } from "@/components/StatisticPicker";
import { VariablePicker } from "@/components/VariablePicker";
import { DisplayRow, NavbarSelect, RegionUtilitySelect } from "@/components/ViewerSiteHeader";
import { Slider } from "@/components/ui/slider";
import { supportsNwsWarningsOverlay } from "@/lib/app-utils";
import { cn } from "@/lib/utils";
import {
  railAvailabilityLine,
  railFreshnessLabel,
  railFreshnessShortLabel,
} from "@/lib/viewer-rail";
import {
  MOBILE_PEEK_PX,
  mobileSheetHeightPx,
  nextMobileSnap,
  type MobileSheetSnap,
} from "@/lib/viewer-mobile";
import { useViewerToolbar } from "@/lib/viewer-toolbar-context";

/**
 * Phase 8 mobile sheet (§8 State A peek / State C expanded).
 *
 * Snaps are peek (84) → half (50vh) → full (62vh, clamped so ≥180 px of map
 * survives). There is no "closed" snap on the viewer: §8 State A *includes*
 * the peek, and `mobileControlsOpen` maps open → half.
 *
 * §8 "No duplication": the peek owns RUN STATE ONLY — run label, the §5.4
 * `railFreshnessLabel` / `railAvailabilityLine` adapters, and the same
 * newest-first `◀ ▶` run walk the desktop rail uses. Source identity belongs
 * to the map badge. The retired mobile freshness strip's "X/Y hrs available"
 * string is exactly what those adapters replace (§9 truthfulness grep).
 *
 * Tabs are gone (decision 4): the expanded body is scrollable sections in rail
 * order — Source → View → Legend — and the pickers keep their existing inline
 * panel implementations untouched (§2.3).
 *
 * The sheet is an OVERLAY. The map element's box is fixed by
 * `--viewer-map-bottom-inset`, so no snap can resize the map (§9).
 */
const SECTION_HEADING_CLASSNAME =
  "px-1 text-[14px] font-semibold tracking-[0.02em] text-cyan-200/85";
const FIELD_LABEL_CLASSNAME =
  "pl-1 text-[12px] font-medium tracking-[0.02em] text-white/48";
/** Same threshold family the pre-Phase-8 sheet used for its drag gesture. */
const DRAG_THRESHOLD_PX = 40;

function spcVariableLabel(option: { value: string; label: string }): string {
  switch (option.value) {
    case "convective": return "Categorical";
    case "tornado_prob": return "Tornado";
    case "wind_prob": return "Wind";
    case "hail_prob": return "Hail";
    default: return option.label;
  }
}

function useViewportHeight(): number {
  const [height, setHeight] = useState(() => (typeof window === "undefined" ? 844 : window.innerHeight));
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const update = () => setHeight(window.innerHeight);
    update();
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
    };
  }, []);
  return height;
}

/** Re-render every 30 s so `Updated N min ago` cannot go stale in place. */
function useMinuteTick(active: boolean): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => setTick((value) => value + 1), 30_000);
    return () => window.clearInterval(timer);
  }, [active]);
}

export function ViewerMobileSheet({ peekPx = MOBILE_PEEK_PX }: { peekPx?: number }) {
  const toolbar = useViewerToolbar();
  const viewportHeight = useViewportHeight();
  const sourceRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<HTMLDivElement | null>(null);
  const legendRef = useRef<HTMLDivElement | null>(null);
  const dragStartY = useRef<number | null>(null);
  const pickerReturnSnap = useRef<MobileSheetSnap | null>(null);
  const [regionOpenSignal, setRegionOpenSignal] = useState(0);
  /** ⌕ (decision 2) lands at HALF, not full: the search panel fits, and half
      keeps the map in view while the user picks a place. */
  const searchRequestedRef = useRef(false);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [variablePickerOpen, setVariablePickerOpen] = useState(false);
  const [regionPickerOpen, setRegionPickerOpen] = useState(false);

  const snap: MobileSheetSnap = toolbar?.mobileSheetSnap ?? "peek";
  const setSnap = toolbar?.onMobileSheetSnapChange;
  const sheetRequest = toolbar?.mobileSheetRequest ?? null;
  const onRequestHandled = toolbar?.onMobileSheetRequestHandled;
  const requestToken = sheetRequest?.token ?? 0;
  const requestSection = sheetRequest?.section ?? null;
  const requestFocusSearch = sheetRequest?.focusSearch === true;

  useMinuteTick(Boolean(toolbar?.runLastUpdatedISO));

  // Section requests (⌕ / tour). Depends on PRIMITIVES only — a memo-object
  // dependency here would re-fire on every identity change and loop the
  // scroll/clear cycle (the same trap documented on App.tsx's rail tour
  // effect).
  useEffect(() => {
    if (!requestToken || !requestSection) return undefined;
    const frame = window.requestAnimationFrame(() => {
      const target = requestSection === "legend"
        ? legendRef.current
        : requestSection === "view"
          ? viewRef.current
          : sourceRef.current;
      target?.scrollIntoView({ block: "start" });
      if (requestFocusSearch) {
        searchRequestedRef.current = true;
        setRegionOpenSignal((value) => value + 1);
      }
      onRequestHandled?.();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [onRequestHandled, requestFocusSearch, requestSection, requestToken]);

  const expanded = snap !== "peek";

  useEffect(() => {
    if (!expanded) {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [expanded]);

  const goTo = useCallback((next: MobileSheetSnap) => {
    setSnap?.(next);
  }, [setSnap]);

  const handleDragStart = (event: React.TouchEvent) => {
    dragStartY.current = event.touches[0]?.clientY ?? null;
  };
  const handleDragEnd = (event: React.TouchEvent) => {
    const start = dragStartY.current;
    dragStartY.current = null;
    if (start == null) return;
    const deltaY = (event.changedTouches[0]?.clientY ?? 0) - start;
    if (deltaY < -DRAG_THRESHOLD_PX) {
      goTo(snap === "peek" ? "half" : "full");
    } else if (deltaY > DRAG_THRESHOLD_PX) {
      goTo(snap === "full" ? "half" : "peek");
    }
  };

  if (!toolbar) return null;

  const {
    model, onModelChange, models,
    variable, onVariableChange, variables, supportedVariableIds,
    coverageBadgeVariableIds, coverageBadgeLabel,
    ensembleProducts, product, onProductChange, productAvailability,
    run, onRunChange, runs, runDisplayLabel, runSelectionLocked,
    hasNewerRunAvailable, latestAvailableRunLabel, onViewLatestRun,
    region, onRegionChange, onLocationJump, regions,
    disabled, legend, compositeLegendLayers,
    pointLabelsEnabled, onPointLabelsEnabledChange,
    nwsWarningsEnabled, onNwsWarningsEnabledChange,
    zoomControlsVisible, onZoomControlsVisibleChange,
    globeProjectionEnabled, onGlobeProjectionEnabledChange,
    basemapMode, onBasemapModeChange, opacity, onOpacityChange,
    animationDelayMs, onSpeedChange,
  } = toolbar;

  const availableProductOptions = (ensembleProducts ?? [])
    .filter((entry) => entry.key === "mean" || productAvailability?.[entry.key])
    .map((entry) => ({ value: entry.key, label: entry.label ?? entry.key, longLabel: entry.long_label ?? entry.label ?? entry.key }));
  const showProductSelect = availableProductOptions.length > 1;

  const displayVariableCatalog = model === "spc"
    ? variables.map((option) => ({ ...option, label: spcVariableLabel(option) }))
    : variables;
  const selectedRegionLabel = regions.find((option) => option.value === region)?.label ?? "Region";
  const runMenuOptions = hasNewerRunAvailable ? runs.filter((option) => option.value !== "latest") : runs;

  // Run stepper — the same newest-first walk as the desktop rail (§8 peek).
  const runValues = runs.map((option) => option.value);
  const runIndex = Math.max(0, runValues.indexOf(run));
  const olderRun = runValues[Math.min(runValues.length - 1, runIndex + 1)];
  const newerRun = runValues[Math.max(0, runIndex - 1)];
  const stepDisabled = disabled || runSelectionLocked || runValues.length === 0;

  const freshnessLabel = railFreshnessLabel(toolbar.runLastUpdatedISO);
  const freshnessShortLabel = railFreshnessShortLabel(toolbar.runLastUpdatedISO);
  const availabilityLine = railAvailabilityLine({
    mode: toolbar.timeAxisMode ?? "forecast",
    frames: toolbar.availableFrameHours ?? [],
    readyThroughFh: toolbar.readyThroughFh,
    expectedMaxFh: toolbar.expectedMaxFh,
  });

  const pickerOpen = modelPickerOpen || variablePickerOpen || regionPickerOpen;
  const rememberPickerReturnSnap = () => {
    if (!pickerOpen && pickerReturnSnap.current == null) {
      pickerReturnSnap.current = snap === "peek" ? "half" : snap;
    }
  };
  const restorePickerReturnSnap = () => {
    const returnSnap = pickerReturnSnap.current;
    pickerReturnSnap.current = null;
    if (returnSnap) goTo(returnSnap);
  };

  const sheetHeight = mobileSheetHeightPx(snap, viewportHeight, peekPx);

  const grabBar = (edge = false) => (
    <span
      data-testid="mobile-sheet-grabber"
      aria-hidden="true"
      className={cn(
        "pointer-events-none block h-1.5 w-12 shrink-0 rounded-full bg-white/45 shadow-[0_0_0_1px_rgba(255,255,255,0.08),0_1px_8px_rgba(0,0,0,0.35)]",
        edge && "absolute left-1/2 top-2 -translate-x-1/2",
      )}
    />
  );

  const peekBody = (
    <div
      data-testid="mobile-sheet-peek"
      data-tour-target="mobile-sheet-peek"
      className="relative flex h-full items-center gap-1.5 px-3"
    >
      {grabBar(true)}
      <button
        type="button"
        data-testid="mobile-sheet-handle"
        onClick={() => goTo(nextMobileSnap(snap))}
        onTouchStart={handleDragStart}
        onTouchEnd={handleDragEnd}
        aria-label="Open controls"
        className="relative flex h-full min-w-0 flex-1 touch-none select-none flex-col justify-center gap-0.5 rounded-lg px-1 pt-2 text-left transition-colors active:bg-white/[0.04]"
      >
        {/* Two lines, not one. Run + freshness + availability on a single row
            overflowed a 390 px peek and truncated the availability string
            mid-number ("ready through FH 3 of…"), which is the one part of the
            §5.4 vocabulary that carries the actual number. Line 1 takes the
            compact freshness so line 2 can hold the availability string whole. */}
        <span className="flex min-w-0 items-baseline gap-1.5">
          <span data-testid="mobile-peek-run" className="min-w-0 truncate text-[13px] font-semibold text-white/92">
            {runDisplayLabel || "Run"}
          </span>
          {freshnessShortLabel ? (
            <>
              <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 self-center rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(110,231,183,0.45)]" />
              <span data-testid="mobile-peek-freshness" className="shrink-0 text-[12px] font-medium text-white/58">
                {freshnessShortLabel}
              </span>
            </>
          ) : null}
        </span>
        {availabilityLine ? (
          <span
            data-testid="mobile-peek-availability"
            className="block min-w-0 truncate text-[12px] font-medium text-white/58"
          >
            {availabilityLine}
          </span>
        ) : null}
      </button>

      <button
        type="button"
        data-testid="mobile-run-step-older"
        aria-label="Previous run"
        title="Previous run"
        disabled={stepDisabled || olderRun === run}
        onClick={() => { if (olderRun && olderRun !== run) onRunChange(olderRun); }}
        className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.05] text-white/70 transition-colors hover:border-white/18 hover:bg-white/[0.09] hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
      >
        <ChevronLeft className="h-4 w-4" />
      </button>
      <button
        type="button"
        data-testid="mobile-run-step-newer"
        aria-label="Next run"
        title="Next run"
        disabled={stepDisabled || newerRun === run}
        onClick={() => { if (newerRun && newerRun !== run) onRunChange(newerRun); }}
        className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.05] text-white/70 transition-colors hover:border-white/18 hover:bg-white/[0.09] hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );

  const expandedBody = (
    <>
      <button
        type="button"
        data-testid="mobile-sheet-handle"
        onClick={() => goTo(nextMobileSnap(snap))}
        onTouchStart={handleDragStart}
        onTouchEnd={handleDragEnd}
        aria-label={snap === "full" ? "Collapse controls" : "Expand controls"}
        className="flex h-11 w-full shrink-0 touch-none select-none items-center justify-center active:opacity-70"
      >
        {grabBar()}
      </button>

      {/* §8 State C: the timeline hides, so its valid time moves here. */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-white/[0.08] px-4 pb-2">
        <span
          data-testid="mobile-sheet-valid-time"
          className="min-w-0 truncate font-['IBM_Plex_Mono',monospace] text-[12px] font-medium tracking-[0.04em] text-white/72"
        >
          {toolbar.mobileValidTimeLabel ?? "Valid time unavailable"}
        </span>
        <button
          type="button"
          onClick={() => goTo("peek")}
          className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/5 text-white/60 hover:text-white"
          aria-label="Close controls"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div
        className={cn(
          "picker-scroll min-h-0 flex-1 px-4 pb-6 pt-3",
          pickerOpen ? "flex flex-col overflow-hidden" : "overflow-y-auto",
        )}
      >
        {/* ── Source ──────────────────────────────────────────────────────── */}
        <div
          ref={sourceRef}
          data-testid="mobile-sheet-source"
          data-tour-target="mobile-product-variable-run"
          className={cn("flex flex-col gap-2.5", regionPickerOpen && "hidden")}
        >
          <span data-testid="mobile-section-heading" className={cn(SECTION_HEADING_CLASSNAME, pickerOpen && "hidden")}>
            Source
          </span>

          <div className={cn("flex flex-col gap-1", modelPickerOpen && "min-h-0 flex-1", variablePickerOpen && "hidden")}>
            <span className={cn(FIELD_LABEL_CLASSNAME, pickerOpen && "hidden")}>Model</span>
            <ModelPicker
              value={model}
              onChange={onModelChange}
              options={models}
              disabled={models.length === 0}
              placeholder="Model"
              minWidth="w-full"
              inlinePanel
              inlinePanelClassName="min-h-0 flex-1"
              onOpenChange={(open) => {
                setModelPickerOpen(open);
                if (open) {
                  rememberPickerReturnSnap();
                  setVariablePickerOpen(false);
                  setRegionPickerOpen(false);
                  goTo("full");
                } else if (!variablePickerOpen && !regionPickerOpen) {
                  restorePickerReturnSnap();
                }
              }}
            />
          </div>

          {/* Global go-live §5: same Coverage control as the desktop rail,
              directly under Model; hidden while a picker owns the sheet. */}
          <CoverageControl labelClassName={FIELD_LABEL_CLASSNAME} className={cn(pickerOpen && "hidden")} />

          <div className={cn("flex flex-col gap-1", variablePickerOpen && "min-h-0 flex-1", modelPickerOpen && "hidden")}>
            <span className={cn(FIELD_LABEL_CLASSNAME, pickerOpen && "hidden")}>Variable</span>
            <VariablePicker
              modelId={model}
              value={variable}
              onChange={onVariableChange}
              variableCatalog={displayVariableCatalog}
              supportedVariableIds={supportedVariableIds}
              coverageBadgeVariableIds={coverageBadgeVariableIds}
              coverageBadgeLabel={coverageBadgeLabel}
              disabled={disabled}
              placeholder="Variable"
              legend={legend}
              minWidth="w-full"
              inlinePanel
              inlinePanelClassName="min-h-0 flex-1"
              onOpenChange={(open) => {
                setVariablePickerOpen(open);
                if (open) {
                  rememberPickerReturnSnap();
                  setModelPickerOpen(false);
                  setRegionPickerOpen(false);
                  goTo("full");
                } else if (!modelPickerOpen && !regionPickerOpen) {
                  restorePickerReturnSnap();
                }
              }}
            />
          </div>

          {showProductSelect ? (
            <div className={cn("flex flex-col gap-1", pickerOpen && "hidden")}>
              <span className={FIELD_LABEL_CLASSNAME}>Statistic</span>
              <StatisticPicker
                value={product ?? "mean"}
                onValueChange={(value) => onProductChange?.(value)}
                options={availableProductOptions}
                disabled={disabled}
                minWidth="w-full"
              />
            </div>
          ) : null}

          <div className={cn("flex flex-col gap-1", pickerOpen && "hidden")}>
            <span className={FIELD_LABEL_CLASSNAME}>Run</span>
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
              minWidth="w-full"
              contentOffset={8}
            />
          </div>

          {/* Decision 4: the expanded Source section speaks "Run + freshness /
              availability", the same pair the desktop rail shows. The peek's
              compact freshness is a peek-only abbreviation — here there is room
              for the full §5.4 strings, so the full ones are what render. */}
          {freshnessLabel || availabilityLine ? (
            <div
              data-testid="mobile-sheet-run-state"
              className={cn("flex flex-col gap-1 px-1 pt-0.5", pickerOpen && "hidden")}
            >
              {freshnessLabel ? (
                <span className="flex items-center gap-1.5 text-[12px] font-medium text-white/58">
                  <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(110,231,183,0.45)]" />
                  <span data-testid="mobile-sheet-freshness">{freshnessLabel}</span>
                </span>
              ) : null}
              {availabilityLine ? (
                <span
                  data-testid="mobile-sheet-availability"
                  className="pl-3 text-[12px] font-medium text-white/58"
                >
                  {availabilityLine}
                </span>
              ) : null}
            </div>
          ) : null}

          {/* The 64 px timeline row has no horizontal room for a second 44 px
              control beside play + track + readout on a 390 px viewport, so
              the speed control lands here (plan Task 2 audit). */}
          {onSpeedChange && typeof animationDelayMs === "number" ? (
            <div className={cn("flex items-center justify-between gap-3 px-1 pt-1", pickerOpen && "hidden")}>
              <span className="text-[13px] font-medium text-white/82">Animation speed</span>
              <SpeedButton animationDelayMs={animationDelayMs} onSpeedChange={onSpeedChange} touch />
            </div>
          ) : null}
        </div>

        {/* ── View ────────────────────────────────────────────────────────── */}
        <div
          ref={viewRef}
          data-testid="mobile-sheet-view"
          className={cn("mt-4 flex flex-col gap-2", regionPickerOpen && "mt-0 min-h-0 flex-1", pickerOpen && !regionPickerOpen && "hidden")}
        >
          <span
            data-testid="mobile-section-heading"
            data-tour-target="mobile-view-section"
            className={cn(SECTION_HEADING_CLASSNAME, regionPickerOpen && "hidden")}
          >
            View
          </span>

          <div data-testid="mobile-region-row" data-tour-target="mobile-region-row" className={cn(regionPickerOpen && "flex min-h-0 flex-1 flex-col")}>
            <RegionUtilitySelect
              value={region}
              onValueChange={onRegionChange}
              onLocationJump={onLocationJump}
              options={regions}
              disabled={disabled}
              currentRegionLabel={selectedRegionLabel}
              variant="field"
              fieldPrefix
              inlinePanel
              inlinePanelClassName="min-h-0 flex-1"
              openSignal={regionOpenSignal}
              valueTestId="mobile-region-value"
              onOpenChange={(open) => {
                setRegionPickerOpen(open);
                if (open) {
                  rememberPickerReturnSnap();
                  setModelPickerOpen(false);
                  setVariablePickerOpen(false);
                  goTo(searchRequestedRef.current ? "half" : "full");
                  searchRequestedRef.current = false;
                } else if (!modelPickerOpen && !variablePickerOpen) {
                  searchRequestedRef.current = false;
                  restorePickerReturnSnap();
                }
              }}
            />
          </div>

          <div className={cn("flex flex-col gap-2", regionPickerOpen && "hidden")}>
            {supportsNwsWarningsOverlay(model, variable) ? (
              <DisplayRow
                label="NWS Warnings"
                checked={nwsWarningsEnabled}
                onToggle={() => onNwsWarningsEnabledChange(!nwsWarningsEnabled)}
                variant="flat"
              />
            ) : null}
            <DisplayRow
              label="City labels"
              checked={pointLabelsEnabled}
              onToggle={() => onPointLabelsEnabledChange(!pointLabelsEnabled)}
              variant="flat"
            />
            <DisplayRow
              label="Dark basemap"
              checked={basemapMode === "dark"}
              onToggle={() => onBasemapModeChange(basemapMode === "dark" ? "light" : "dark")}
              variant="flat"
            />
            <DisplayRow
              label="Zoom controls"
              checked={zoomControlsVisible}
              onToggle={() => onZoomControlsVisibleChange(!zoomControlsVisible)}
              variant="flat"
            />
            {/* Globe v1 — same row as the desktop rail's VIEW section, and
                only when the runtime projection setter exists. */}
            {onGlobeProjectionEnabledChange ? (
              <div data-testid="mobile-toggle-globe-view" data-tour-target="globe-toggle">
                <DisplayRow
                  label="Globe view"
                  checked={Boolean(globeProjectionEnabled)}
                  onToggle={() => onGlobeProjectionEnabledChange(!globeProjectionEnabled)}
                  variant="flat"
                />
              </div>
            ) : null}
            <DisplayRow
              label="Legend chip"
              checked={toolbar.legendVisible}
              onToggle={() => toolbar.onLegendVisibleChange(!toolbar.legendVisible)}
              variant="flat"
            />

            <div className="px-1 py-2">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-white">Opacity</span>
                <span className="font-['IBM_Plex_Mono',monospace] text-[12px] font-medium text-cyan-300/80">
                  {Math.round(opacity * 100)}%
                </span>
              </div>
              <Slider
                value={[Math.round(opacity * 100)]}
                onValueChange={([v]) => onOpacityChange((v ?? 100) / 100)}
                min={0}
                max={100}
                step={1}
                data-audit-exception="opacity-slider"
                className="w-full transition-opacity duration-150 [&>*:first-child]:h-1.5 [&>*:first-child]:bg-white/[0.12] [&>*:first-child>*:first-child]:bg-gradient-to-r [&>*:first-child>*:first-child]:from-cyan-400 [&>*:first-child>*:first-child]:via-sky-300 [&>*:first-child>*:first-child]:to-slate-200"
              />
            </div>
          </div>
        </div>

        {/* ── Legend ──────────────────────────────────────────────────────── */}
        {/* Decision 4: the full inline legend replaces BOTH the old mobile
            legend popover and the Display-tab Legend toggle. The §7 chip is
            the at-rest surface; this section is the full one. */}
        <div
          ref={legendRef}
          data-testid="mobile-sheet-legend"
          data-legend-mount="mobile-sheet"
          data-tour-target="mobile-legend-section"
          className={cn("mt-4 flex flex-col gap-2", pickerOpen && "hidden")}
        >
          <span data-testid="mobile-section-heading" className={SECTION_HEADING_CLASSNAME}>
            Legend
          </span>
          <div className="rounded-xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.72] px-1 py-1">
            <MapLegend
              legend={legend}
              compositeLayers={compositeLegendLayers}
              defaultExpanded={true}
              inline={true}
            />
          </div>
        </div>
      </div>
    </>
  );

  return createPortal(
    <>
      {expanded ? (
        <div
          className="fixed inset-0 z-[65] bg-black/30 transition-[background-color] duration-300"
          onClick={() => goTo("peek")}
          aria-hidden="true"
        />
      ) : null}

      <div
        data-testid="mobile-sheet"
        data-snap={snap}
        data-tour-target="mobile-bottom-sheet"
        role="region"
        aria-label="Viewer controls"
        style={{
          height: `${sheetHeight}px`,
          transition: "height 0.32s cubic-bezier(0.32, 0.72, 0, 1)",
        }}
        className="viewer-mobile-surface fixed bottom-0 left-0 right-0 z-[66] flex max-w-full flex-col overflow-hidden rounded-t-[1.25rem] [border-bottom:none] [border-left:none] [border-right:none]"
      >
        {expanded ? expandedBody : peekBody}
      </div>
    </>,
    document.body,
  );
}

export default ViewerMobileSheet;
