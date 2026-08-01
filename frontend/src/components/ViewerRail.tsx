import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Boxes,
  ChevronLeft,
  ChevronRight,
  MapPin,
  Palette,
  SlidersHorizontal,
} from "lucide-react";

import { CompactLegendChip } from "@/components/CompactLegendChip";
import { CoverageControl } from "@/components/CoverageControl";
import { MapLegend } from "@/components/map-legend";
import { ModelPicker } from "@/components/ModelPicker";
import { StatisticPicker } from "@/components/StatisticPicker";
import { VariablePicker } from "@/components/VariablePicker";
import { DisplayRow, NavbarSelect, RegionUtilitySelect } from "@/components/ViewerSiteHeader";
import { Slider } from "@/components/ui/slider";
import { supportsNwsWarningsOverlay } from "@/lib/app-utils";
import { cn } from "@/lib/utils";
import { railFreshnessLabel } from "@/lib/viewer-rail";
import { useViewerToolbar } from "@/lib/viewer-toolbar-context";

/**
 * Phase 6 rail (§6.2 / §6.3). Fills the left edge from below the 48 px bar to
 * the viewport bottom and owns three normative sections — SOURCE, VIEW and
 * LEGEND.
 *
 * The rail is a presentational consumer: every value and setter still lives in
 * App.tsx / ViewerToolbarContext / use-display-settings. Expanded and collapsed
 * bodies are both mounted and switched with `display`, so a width toggle never
 * remounts a picker, the legend, or the map (§12: no refetch on chrome moves).
 */
const SECTION_LABEL_CLASSNAME =
  "px-1 text-[14px] font-semibold tracking-[0.02em] text-cyan-200/85";
const FIELD_LABEL_CLASSNAME =
  "pl-1 text-[12px] font-medium tracking-[0.02em] text-white/48";
const COLLAPSED_ITEM_CLASSNAME =
  "flex min-h-[52px] w-full flex-col items-center justify-center gap-1 rounded-xl border border-transparent px-1 py-2 text-white/62 transition-colors hover:border-white/12 hover:bg-white/[0.06] hover:text-white pointer-coarse:min-h-11";

function spcVariableLabel(option: { value: string; label: string }): string {
  switch (option.value) {
    case "convective": return "Categorical";
    case "tornado_prob": return "Tornado";
    case "wind_prob": return "Wind";
    case "hail_prob": return "Hail";
    default: return option.label;
  }
}

function useMinuteTick(active: boolean): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => setTick((value) => value + 1), 30_000);
    return () => window.clearInterval(timer);
  }, [active]);
  return tick;
}

export function ViewerRail() {
  const toolbar = useViewerToolbar();
  const sourceRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<HTMLDivElement | null>(null);
  const legendRef = useRef<HTMLDivElement | null>(null);
  const expanded = toolbar?.railState !== "collapsed";
  const pendingSection = toolbar?.railPendingSection ?? null;
  const onRailPendingSectionHandled = toolbar?.onRailPendingSectionHandled;
  useMinuteTick(Boolean(toolbar?.runLastUpdatedISO));

  // Expand-to-section (§6.3): scroll the requested section into view once the
  // expanded body is laid out, then clear the request.
  useEffect(() => {
    if (!expanded || !pendingSection) return undefined;
    const frame = window.requestAnimationFrame(() => {
      const target = pendingSection === "legend"
        ? legendRef.current
        : pendingSection === "view"
          ? viewRef.current
          : sourceRef.current;
      target?.scrollIntoView({ block: "nearest" });
      onRailPendingSectionHandled?.();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [expanded, onRailPendingSectionHandled, pendingSection]);

  if (!toolbar) return null;

  const {
    model, onModelChange, models,
    variable, onVariableChange, variables, supportedVariableIds,
    coverageBadgeVariableIds, coverageBadgeLabel,
    ensembleProducts, product, onProductChange, productAvailability,
    run, onRunChange, runs, runDisplayLabel, runSelectionLocked,
    hasNewerRunAvailable, latestAvailableRunLabel, onViewLatestRun,
    region, onRegionChange, onLocationJump, regions,
    disabled, legend, legendVisible, onLegendVisibleChange, compositeLegendLayers,
    pointLabelsEnabled, onPointLabelsEnabledChange,
    nwsWarningsEnabled, onNwsWarningsEnabledChange,
    zoomControlsVisible, onZoomControlsVisibleChange,
    globeProjectionEnabled, onGlobeProjectionEnabledChange,
    basemapMode, onBasemapModeChange, opacity, onOpacityChange,
    onRailToggle, onRailExpandTo,
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

  // Run stepper: same newest-first walk as the Phase 5 `[` / `]` keys.
  const runValues = runs.map((option) => option.value);
  const runIndex = Math.max(0, runValues.indexOf(run));
  const olderRun = runValues[Math.min(runValues.length - 1, runIndex + 1)];
  const newerRun = runValues[Math.max(0, runIndex - 1)];
  const stepDisabled = disabled || runSelectionLocked || runValues.length === 0;

  const freshnessLabel = railFreshnessLabel(toolbar.runLastUpdatedISO);
  const edgeToggle = (
    <button
      type="button"
      data-testid={expanded ? "rail-collapse-toggle" : "rail-expand-toggle"}
      onClick={onRailToggle}
      aria-label={expanded ? "Collapse controls" : "Expand controls"}
      title={expanded ? "Collapse controls" : "Expand controls"}
      className="fixed z-[78] flex h-8 w-8 items-center justify-start text-white/58 transition-colors hover:text-white pointer-coarse:h-11 pointer-coarse:w-11"
      style={{
        top: "calc(var(--viewer-topbar-height, 3rem) + var(--viewer-header-extra, 0px) + 0.75rem)",
        left: "calc(var(--viewer-rail-width, 288px) - 1px)",
      }}
    >
      <span
        data-testid="rail-edge-tab"
        aria-hidden="true"
        className="flex h-8 w-5 items-center justify-center rounded-r-lg border border-l-0 border-white/[0.11] bg-[#0a1825]/90 shadow-[2px_2px_9px_rgba(0,0,0,0.28),inset_0_1px_0_rgba(255,255,255,0.05)] backdrop-blur-md transition-colors hover:bg-[#102334]/95"
      >
        {expanded
          ? <ChevronLeft className="h-3.5 w-3.5" />
          : <ChevronRight className="h-3.5 w-3.5" />}
      </span>
    </button>
  );

  const rail = (
    <aside
      data-testid="viewer-rail"
      data-rail-state={expanded ? "expanded" : "collapsed"}
      aria-label="Viewer controls"
      className="fixed bottom-0 left-0 z-[76] flex flex-col overflow-hidden border-r border-[#1a3a5c]/60 bg-[#030e1a]/[0.92] shadow-[2px_0_16px_rgba(0,0,0,0.4),inset_-1px_0_0_rgba(100,180,255,0.06)] backdrop-blur-md"
      style={{
        top: "calc(var(--viewer-topbar-height, 3rem) + var(--viewer-header-extra, 0px))",
        width: "var(--viewer-rail-width, 288px)",
      }}
    >
      {/* ── Collapsed body (§6.3): icon + 11 px caption, never icon-only ── */}
      <div className={cn("flex flex-col gap-1 px-2 py-2", expanded && "hidden")}>
        <button
          type="button"
          data-testid="rail-collapsed-source"
          onClick={() => onRailExpandTo?.("source")}
          className={COLLAPSED_ITEM_CLASSNAME}
        >
          <Boxes className="h-[18px] w-[18px]" />
          <span data-testid="rail-collapsed-caption" className="text-[11px] font-medium leading-none">Source</span>
        </button>
        <button
          type="button"
          data-testid="rail-collapsed-region"
          onClick={() => onRailExpandTo?.("view")}
          className={COLLAPSED_ITEM_CLASSNAME}
        >
          <MapPin className="h-[18px] w-[18px]" />
          <span data-testid="rail-collapsed-caption" className="text-[11px] font-medium leading-none">Region</span>
        </button>
        <button
          type="button"
          data-testid="rail-collapsed-view"
          onClick={() => onRailExpandTo?.("view")}
          className={COLLAPSED_ITEM_CLASSNAME}
        >
          <SlidersHorizontal className="h-[18px] w-[18px]" />
          <span data-testid="rail-collapsed-caption" className="text-[11px] font-medium leading-none">View</span>
        </button>
        {/* §6.3 / §7.2: the Legend item expands the rail to the full legend —
            the compact chip in the map corner is the other path to density. */}
        <button
          type="button"
          data-testid="rail-collapsed-legend"
          onClick={() => onRailExpandTo?.("legend")}
          className={COLLAPSED_ITEM_CLASSNAME}
        >
          <Palette className="h-[18px] w-[18px]" />
          <span data-testid="rail-collapsed-caption" className="text-[11px] font-medium leading-none">Legend</span>
        </button>
      </div>

      {/* ── Expanded body (§6.2) ───────────────────────────────────────────── */}
      <div className={cn("picker-scroll flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-3 py-3", !expanded && "hidden")}>
        <div ref={sourceRef} data-testid="rail-source" data-tour-target="product-variable-run" className="flex flex-col gap-2.5">
          <div className="flex items-center gap-2">
            <span data-testid="rail-source-heading" className={SECTION_LABEL_CLASSNAME}>
              Source
            </span>
          </div>

          <div
            data-testid="rail-source-fields-card"
            className="flex flex-col gap-2.5 rounded-xl border border-white/[0.11] bg-[#0b1b2a]/55 bg-gradient-to-b from-white/[0.075] via-white/[0.035] to-cyan-300/[0.025] p-2.5 shadow-[0_8px_24px_rgba(0,0,0,0.16),inset_0_1px_0_rgba(255,255,255,0.055)] backdrop-blur-xl"
          >
            <div className="flex flex-col gap-1">
              <span data-testid="rail-model-label" className={FIELD_LABEL_CLASSNAME}>Model</span>
              <ModelPicker
                value={model}
                onChange={onModelChange}
                options={models}
                disabled={models.length === 0}
                placeholder="Model"
                minWidth="w-full"
                panelOffset={8}
              />
            </div>

            {/* Global go-live §1: Coverage sits directly under Model — it is a
                data-selection act, never the VIEW section's camera preset. */}
            <CoverageControl labelClassName={FIELD_LABEL_CLASSNAME} />

            <div className="flex flex-col gap-1">
              <span data-testid="rail-variable-label" className={FIELD_LABEL_CLASSNAME}>Variable</span>
              <VariablePicker
                modelId={model}
                value={variable}
                onChange={onVariableChange}
                variableCatalog={displayVariableCatalog}
                supportedVariableIds={supportedVariableIds}
                disabled={disabled}
                placeholder="Variable"
                legend={legend}
                minWidth="w-full"
                panelOffset={8}
                coverageBadgeVariableIds={coverageBadgeVariableIds}
                coverageBadgeLabel={coverageBadgeLabel}
              />
            </div>

            {showProductSelect ? (
              <div className="flex flex-col gap-1">
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

            <div className="flex flex-col gap-1">
              <span data-testid="rail-run-label" className={FIELD_LABEL_CLASSNAME}>Run</span>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  data-testid="rail-run-step-older"
                  aria-label="Previous run"
                  title="Previous run"
                  disabled={stepDisabled || olderRun === run}
                  onClick={() => { if (olderRun && olderRun !== run) onRunChange(olderRun); }}
                  className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.05] text-white/70 transition-colors hover:border-white/18 hover:bg-white/[0.09] hover:text-white disabled:cursor-not-allowed disabled:opacity-40 pointer-coarse:h-11 pointer-coarse:w-11"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <div data-testid="rail-run-trigger" className="min-w-0 flex-1">
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
                <button
                  type="button"
                  data-testid="rail-run-step-newer"
                  aria-label="Next run"
                  title="Next run"
                  disabled={stepDisabled || newerRun === run}
                  onClick={() => { if (newerRun && newerRun !== run) onRunChange(newerRun); }}
                  className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/[0.09] bg-white/[0.05] text-white/70 transition-colors hover:border-white/18 hover:bg-white/[0.09] hover:text-white disabled:cursor-not-allowed disabled:opacity-40 pointer-coarse:h-11 pointer-coarse:w-11"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>

          {freshnessLabel ? (
            <div data-testid="rail-freshness" className="flex items-center gap-1.5 px-1 text-[12px] font-medium text-white/58">
              <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(110,231,183,0.45)]" />
              {freshnessLabel}
            </div>
          ) : null}
        </div>

        <div data-testid="rail-section-divider" aria-hidden="true" className="h-px w-full shrink-0 bg-white/10" />

        <div ref={viewRef} data-testid="rail-view" className="flex flex-col gap-2">
          <span
            data-testid="rail-view-heading"
            className={SECTION_LABEL_CLASSNAME}
            data-tour-target="display-settings-button"
          >
            View
          </span>

          <div data-testid="rail-region-row" data-tour-target="region-selector">
            {/* §6.2: the region value is readable as text, not behind a glyph. */}
            <RegionUtilitySelect
              value={region}
              onValueChange={onRegionChange}
              onLocationJump={onLocationJump}
              options={regions}
              disabled={disabled}
              currentRegionLabel={selectedRegionLabel}
              variant="field"
              fieldPrefix
              panelPlacement="right"
              valueTestId="rail-region-value"
            />
          </div>

          {supportsNwsWarningsOverlay(model, variable) ? (
            <div data-testid="rail-toggle-nws-warnings">
              <DisplayRow
                label="NWS Warnings"
                checked={nwsWarningsEnabled}
                onToggle={() => onNwsWarningsEnabledChange(!nwsWarningsEnabled)}
                variant="flat"
              />
            </div>
          ) : null}
          <div data-testid="rail-toggle-city-labels">
            <DisplayRow
              label="City labels"
              checked={pointLabelsEnabled}
              onToggle={() => onPointLabelsEnabledChange(!pointLabelsEnabled)}
              variant="flat"
            />
          </div>
          <div data-testid="rail-toggle-dark-basemap">
            <DisplayRow
              label="Dark basemap"
              checked={basemapMode === "dark"}
              onToggle={() => onBasemapModeChange(basemapMode === "dark" ? "light" : "dark")}
              variant="flat"
            />
          </div>
          {/* Decision 5: dropping a working control is not authorized, so the
              zoom-controls toggle stays with the other map toggles. */}
          <div data-testid="rail-toggle-zoom-controls">
            <DisplayRow
              label="Zoom controls"
              checked={zoomControlsVisible}
              onToggle={() => onZoomControlsVisibleChange(!zoomControlsVisible)}
              variant="flat"
            />
          </div>
          {/* Globe v1. A camera setting, not a data selection — hence VIEW.
              Rendered only when the runtime projection setter is actually
              available, so a MapLibre that stops supporting globe degrades to
              no button rather than to a dead one.

              NOT gated per model: a canonical (3857) regional artifact costs
              roughly 3x the frame time on the globe (MapLibre's own globe pass,
              measured in G1), but it renders correctly and gating the control
              on the selected model would make the toggle flicker in and out as
              the user browses. Revisit with the LOD/perf work. */}
          {onGlobeProjectionEnabledChange ? (
            <div data-testid="rail-toggle-globe-view" data-tour-target="globe-toggle">
              <DisplayRow
                label="Globe view"
                checked={Boolean(globeProjectionEnabled)}
                onToggle={() => onGlobeProjectionEnabledChange(!globeProjectionEnabled)}
                variant="flat"
              />
            </div>
          ) : null}
          {/* Same shared twf.map.legend_visible pref as the mobile sheet row —
              without this, a pref persisted false left desktop with no way to
              bring the collapsed-state chip back. */}
          <div data-testid="rail-toggle-legend-chip">
            <DisplayRow
              label="Legend chip"
              checked={legendVisible}
              onToggle={() => onLegendVisibleChange(!legendVisible)}
              variant="flat"
            />
          </div>

          <div data-testid="rail-opacity-control" className="px-1 py-2">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-semibold text-white">Opacity</span>
              <span data-testid="rail-opacity-value" className="font-['IBM_Plex_Mono',monospace] text-[11px] font-medium text-cyan-300/80">
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

        <div aria-hidden="true" className="h-px w-full shrink-0 bg-white/10" />

        {/* Phase 7 legend mount (§7.1), now presented as an explicit section
            directly after View instead of an unlabeled bottom card. */}
        <div ref={legendRef} data-testid="rail-legend" className="flex flex-col gap-2">
          <span
            data-testid="rail-legend-heading"
            className={SECTION_LABEL_CLASSNAME}
            data-tour-target="legend-button"
          >
            Legend
          </span>
          <div
            data-legend-mount="rail"
            className="rounded-xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.72] px-1 py-1"
          >
            {expanded ? (
              <MapLegend
                legend={legend}
                compositeLayers={compositeLegendLayers}
                defaultExpanded={true}
                inline={true}
              />
            ) : null}
          </div>
        </div>
      </div>
    </aside>
  );

  return (
    <>
      {rail}
      {edgeToggle}
      {/* §7.2: the collapsed rail is served by the compact map-corner chip,
          portaled out of the rail's backdrop-filter containing block. */}
      {!expanded && legendVisible
        ? createPortal(
            <CompactLegendChip legend={legend} compositeLayers={compositeLegendLayers} />,
            document.body,
          )
        : null}
    </>
  );
}

export default ViewerRail;
