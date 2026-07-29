import { useEffect, useRef, useState, type Ref } from "react";
import { AlertCircle, ChevronDown, ChevronUp } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  formatValue,
  rampEndpointsLabel,
  resolveLegendPresentation,
  sharedScaleTicks,
  swatchSummaryLabel,
  type CompositeLegendLayer,
  type LegendPresentation,
  type LegendRamp,
  type LegendSharedScale,
  type LegendSwatchRow,
} from "@/lib/legend-model";

export type { LegendEntry, LegendPayload, CompositeLegendLayer } from "@/lib/legend-model";

import type { LegendPayload } from "@/lib/legend-model";

/**
 * Phase 7 (§7.1 / §7.2): this component is the *rendering* of the legend
 * model; every branch selection, every number and every ramp comes from
 * `resolveLegendPresentation`. The compact chip renders the same model through
 * `LegendPresentationBody` with `variant="compact"`, so the two surfaces can
 * never disagree about what a product's legend says.
 */

const LEGEND_COLLAPSED_STORAGE_KEY = "twf.legend.collapsed";
/** Above this many swatches the compact form summarizes instead of listing. */
const COMPACT_SWATCH_LIST_MAX = 4;

function formatLegendTitle(title: string, units?: string): string {
  const resolvedUnits = (units ?? "").trim();
  if (!resolvedUnits) {
    return title;
  }
  const lowerTitle = title.trim().toLowerCase();
  const lowerUnits = resolvedUnits.toLowerCase();
  if (lowerTitle.includes(`(${lowerUnits})`)) {
    return title;
  }
  return `${title} (${resolvedUnits})`;
}

export function splitLegendTitle(title: string, units?: string): { title: string; unitsSuffix: string | null } {
  const formattedTitle = formatLegendTitle(title, units);
  const resolvedUnits = (units ?? "").trim();
  if (!resolvedUnits) {
    return { title: formattedTitle, unitsSuffix: null };
  }

  const unitsSuffix = `(${resolvedUnits})`;
  if (!formattedTitle.endsWith(unitsSuffix)) {
    return { title: formattedTitle, unitsSuffix: null };
  }

  return {
    title: formattedTitle.slice(0, -unitsSuffix.length).trimEnd(),
    unitsSuffix,
  };
}

function UnavailablePlaceholder() {
  return (
    <div className="flex items-center gap-1.5 rounded-xl glass px-2.5 py-2">
      <AlertCircle className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
      <span className="text-xs font-medium text-muted-foreground/80">Legend unavailable</span>
    </div>
  );
}

function readCollapsedPreference(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const stored = window.localStorage.getItem(LEGEND_COLLAPSED_STORAGE_KEY);
    if (stored === null) return true;
    return stored === "true";
  } catch {
    return true;
  }
}

function writeCollapsedPreference(value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LEGEND_COLLAPSED_STORAGE_KEY, String(value));
  } catch {
    // Ignore storage errors (private mode/quota).
  }
}

function rampBackground(colors: string[]): { backgroundImage?: string; backgroundColor?: string } {
  if (colors.length === 0) return {};
  if (colors.length === 1) return { backgroundColor: colors[0] };
  return { backgroundImage: `linear-gradient(to right, ${colors.join(", ")})` };
}

// ── Continuous ─────────────────────────────────────────────────────────────

function ContinuousBody({
  presentation,
  compact,
}: {
  presentation: Extract<LegendPresentation, { mode: "continuous" }>;
  compact: boolean;
}) {
  const { entries, gradientStops, ticks } = presentation;
  const gradientStyle =
    entries.length === 1
      ? { backgroundColor: entries[0]?.color }
      : { backgroundImage: `linear-gradient(to right, ${gradientStops})` };

  if (compact) {
    const first = entries[0];
    const last = entries[entries.length - 1];
    return (
      <div className="flex flex-col gap-1">
        <div
          data-testid="legend-gradient"
          className="h-2.5 rounded-[5px] ring-1 ring-inset ring-white/12"
          style={gradientStyle}
        />
        <div className="flex items-center justify-between font-mono text-[11px] font-semibold leading-none tabular-nums text-foreground/90">
          <span>{first ? formatValue(first.value) : ""}</span>
          <span>{last ? formatValue(last.value) : ""}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="px-0.5 py-1.5">
      <div className="rounded-[14px] bg-black/14 p-[3px] ring-1 ring-inset ring-white/12 shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_6px_16px_rgba(0,0,0,0.18)]">
        <div
          data-testid="legend-gradient"
          className="h-3 rounded-[7px] shadow-[inset_0_1px_0_rgba(255,255,255,0.16)]"
          style={gradientStyle}
        />
      </div>
      {/* Tick marks */}
      <div className="relative mt-[3px] h-[5px]">
        {ticks.map((tick, index) => {
          const isFirst = index === 0;
          const isLast = index === ticks.length - 1;
          return (
            <div
              key={`tick-${tick.value}-${index}`}
              className="absolute top-0 h-full w-px bg-white/40"
              style={{
                left: `${tick.offsetPercent}%`,
                transform: isFirst ? "none" : isLast ? "translateX(-100%)" : "translateX(-50%)",
              }}
            />
          );
        })}
      </div>
      {/* Tick labels */}
      <div className="relative mt-0.5 h-4">
        {ticks.map((tick, index) => {
          const isFirst = index === 0;
          const isLast = index === ticks.length - 1;
          return (
            <div
              key={`label-${tick.value}-${index}`}
              className="absolute top-0"
              style={{
                left: `${tick.offsetPercent}%`,
                transform: isFirst ? "none" : isLast ? "translateX(-100%)" : "translateX(-50%)",
              }}
            >
              <span
                data-testid="legend-tick"
                className="font-mono text-[11px] font-semibold leading-none tabular-nums tracking-tight text-foreground/95 whitespace-nowrap"
              >
                {tick.label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Swatch kinds (discrete / categorical / indexed) ─────────────────────────

function SwatchRow({ row }: { row: LegendSwatchRow }) {
  return (
    <div
      data-testid="legend-swatch-row"
      className="flex items-center gap-1.5 rounded-[2px] px-0.5 py-0.5 transition-colors duration-150"
    >
      <div
        className="h-3 w-3 shrink-0 rounded-[2px] border border-border/30 shadow-sm"
        style={{ backgroundColor: row.color }}
      />
      <span className="text-[11px] font-medium leading-none tracking-tight text-foreground/95">
        {row.label}
      </span>
      {row.value !== undefined ? (
        <span
          data-testid="legend-swatch-value"
          className="ml-auto font-mono text-[11px] font-medium leading-none tabular-nums text-foreground/72"
        >
          {formatValue(row.value)}
        </span>
      ) : null}
    </div>
  );
}

function SwatchBody({
  presentation,
  compact,
}: {
  presentation: Extract<LegendPresentation, { mode: "discrete" | "categorical" | "indexed" }>;
  compact: boolean;
}) {
  const { rows, mode } = presentation;

  // §7.2: a truthful summary count, never a silent truncation.
  if (compact && rows.length > COMPACT_SWATCH_LIST_MAX) {
    return (
      <div className="flex items-center gap-1.5">
        <div className="flex h-2.5 flex-1 overflow-hidden rounded-[5px] ring-1 ring-inset ring-white/12">
          {rows
            .slice()
            .reverse()
            .map((row, index) => (
              <div key={`${row.color}-${index}`} className="h-full flex-1" style={{ backgroundColor: row.color }} />
            ))}
        </div>
        <span
          data-testid="legend-summary-count"
          className="shrink-0 text-[11px] font-medium leading-none text-foreground/80"
        >
          {swatchSummaryLabel(mode, rows.length)}
        </span>
      </div>
    );
  }

  return (
    <div className={cn("space-y-px", compact ? "" : "legend-scroll max-h-[45vh] overflow-y-auto scroll-smooth")}>
      {rows.map((row, index) => (
        <SwatchRow key={`${row.label}-${row.color}-${index}`} row={row} />
      ))}
    </div>
  );
}

// ── Precipitation-type modes ───────────────────────────────────────────────

function SharedScaleRow({ scale, compact }: { scale: LegendSharedScale; compact: boolean }) {
  const ticks = sharedScaleTicks(scale, compact ? 4 : 6);
  return (
    <div
      data-testid="legend-shared-scale"
      className={cn(
        "flex items-center justify-between gap-1 font-mono text-[11px] font-semibold leading-none tabular-nums text-foreground/88",
        compact ? "mt-1" : "mt-2 border-t border-border/18 pt-1.5",
      )}
    >
      {ticks.map((value, index) => (
        <span key={`shared-${value}-${index}`}>{formatValue(value)}</span>
      ))}
      {scale.units ? <span className="text-foreground/62">{scale.units}</span> : null}
    </div>
  );
}

function RampRow({
  ramp,
  units,
  showEndpoints,
  showAdjuncts,
  compact,
  isFirst,
}: {
  ramp: LegendRamp;
  units?: string;
  showEndpoints: boolean;
  showAdjuncts: boolean;
  compact: boolean;
  isFirst: boolean;
}) {
  const endpoints = showEndpoints ? rampEndpointsLabel(ramp, units) : null;
  return (
    <div
      data-testid="legend-ramp"
      data-ramp-key={ramp.key}
      className={cn(compact ? (isFirst ? "" : "mt-1.5") : isFirst ? "" : "mt-2 border-t border-border/18 pt-2")}
    >
      <div className="mb-1 flex items-center justify-between gap-2 px-0.5">
        <span
          data-testid="legend-ramp-label"
          className={cn(
            "font-medium uppercase tracking-[0.08em] text-foreground/72",
            compact ? "text-[11px] leading-none" : "text-[12px]",
          )}
        >
          {ramp.label}
        </span>
        {endpoints ? (
          <span
            data-testid="legend-ramp-endpoints"
            className="shrink-0 font-mono text-[11px] font-medium leading-none tabular-nums text-foreground/90"
          >
            {endpoints}
          </span>
        ) : null}
      </div>
      <div
        className={cn(
          "rounded-[5px] ring-1 ring-inset ring-white/10",
          compact ? "h-2.5" : "h-3",
        )}
        style={rampBackground(ramp.colors)}
      />
      {showAdjuncts ? (
        <div className="mt-1 flex items-center justify-between px-0.5 font-mono text-[11px] font-medium uppercase tracking-[0.08em] text-foreground/58">
          <span>Light</span>
          <span>Heavy</span>
        </div>
      ) : null}
    </div>
  );
}

function PtypeBody({
  presentation,
  compact,
  tight,
}: {
  presentation: Extract<LegendPresentation, { mode: "ptype-intensity" | "radar-ptype" }>;
  compact: boolean;
  /** §7.2 width pressure: drop the repeated break labels, never a ramp. */
  tight: boolean;
}) {
  const { ramps, sharedScale, units } = presentation;
  // The shared ladder already carries the numbers; repeating them per ramp
  // would be noise, and under width pressure the repetition goes first.
  const showEndpoints = !sharedScale && !tight;
  return (
    <div>
      {ramps.map((ramp, index) => (
        <RampRow
          key={`${ramp.key}-${index}`}
          ramp={ramp}
          units={units}
          showEndpoints={showEndpoints}
          showAdjuncts={!compact && presentation.mode === "radar-ptype"}
          compact={compact}
          isFirst={index === 0}
        />
      ))}
      {sharedScale ? <SharedScaleRow scale={sharedScale} compact={compact} /> : null}
    </div>
  );
}

// ── Composite groups ───────────────────────────────────────────────────────

function CompositeBody({
  presentation,
  compact,
  tight,
}: {
  presentation: Extract<LegendPresentation, { mode: "composite-group" }>;
  compact: boolean;
  tight: boolean;
}) {
  return (
    <div className={compact ? "space-y-1.5" : "space-y-2"}>
      {presentation.groups.map((group, index) => (
        <div
          key={`${group.layerLabel}-${index}`}
          data-testid="legend-group"
          className={cn(index > 0 && !compact ? "border-t border-border/18 pt-2" : "")}
        >
          <div
            data-testid="legend-group-label"
            className={cn(
              "mb-1 px-0.5 font-medium tracking-[0.02em] text-foreground/78",
              compact ? "text-[11px] leading-none" : "text-[12px]",
            )}
          >
            {group.layerLabel}
          </div>
          <LegendPresentationBody
            presentation={group.presentation}
            variant={compact ? "compact" : "full"}
            tight={tight}
          />
        </div>
      ))}
    </div>
  );
}

// ── Shared body renderer ───────────────────────────────────────────────────

export function LegendPresentationBody({
  presentation,
  variant = "full",
  tight = false,
}: {
  presentation: LegendPresentation;
  variant?: "full" | "compact";
  tight?: boolean;
}) {
  const compact = variant === "compact";
  const body = (() => {
    switch (presentation.mode) {
      case "continuous":
        return <ContinuousBody presentation={presentation} compact={compact} />;
      case "ptype-intensity":
      case "radar-ptype":
        return <PtypeBody presentation={presentation} compact={compact} tight={tight} />;
      case "composite-group":
        return <CompositeBody presentation={presentation} compact={compact} tight={tight} />;
      default:
        return <SwatchBody presentation={presentation} compact={compact} />;
    }
  })();
  return (
    <div data-testid="legend-presentation" data-legend-mode={presentation.mode}>
      {body}
    </div>
  );
}

// ── MapLegend ──────────────────────────────────────────────────────────────

type MapLegendProps = {
  legend: LegendPayload | null;
  containerRef?: Ref<HTMLDivElement>;
  defaultExpanded?: boolean;
  /** When true, renders inline (no fixed positioning). Use inside popovers/portals. */
  inline?: boolean;
  /** Component layers of a composite product, already resolved by the App seam. */
  compositeLayers?: CompositeLegendLayer[] | null;
};

export function MapLegend({
  legend,
  containerRef,
  defaultExpanded = false,
  inline = false,
  compositeLayers = null,
}: MapLegendProps) {
  const [collapsed, setCollapsed] = useState<boolean>(() => defaultExpanded ? false : readCollapsedPreference());
  const [isSmallScreen, setIsSmallScreen] = useState(false);
  const [fadeKey, setFadeKey] = useState(0);
  const prevTitleRef = useRef(legend?.title);

  useEffect(() => {
    // Skip media query listener when rendering inline (inside a popover)
    if (inline) return;
    const mq = window.matchMedia("(max-width: 640px)");
    const handler = (query: MediaQueryList | MediaQueryListEvent) => {
      setIsSmallScreen(query.matches);
      if (query.matches) {
        setCollapsed(true);
        writeCollapsedPreference(true);
      }
    };
    handler(mq);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [inline]);

  useEffect(() => {
    if (legend?.title !== prevTitleRef.current) {
      setFadeKey((value) => value + 1);
      prevTitleRef.current = legend?.title;
    }
  }, [legend?.title]);

  const presentation = resolveLegendPresentation(legend, compositeLayers);

  if (!legend || !presentation) {
    // In inline mode (popover), render a simple inline placeholder — not a fixed overlay
    if (inline) {
      return (
        <div ref={containerRef} className="px-2.5 py-2">
          <UnavailablePlaceholder />
        </div>
      );
    }
    return (
      <div
        ref={containerRef}
        className={cn("pointer-events-none fixed z-[55]", isSmallScreen ? "right-3 top-40" : "right-4 top-[4.35rem]")}
      >
        <UnavailablePlaceholder />
      </div>
    );
  }

  const { title: legendTitle, unitsSuffix } = splitLegendTitle(legend.title, legend.units);

  return (
    <div
      ref={containerRef}
      className={cn(
        "flex flex-col overflow-hidden transition-all duration-200",
        inline
          ? "w-[220px]"
          : cn(
              "fixed z-[55] max-h-[70vh] rounded-xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.82] shadow-[0_8px_32px_rgba(0,0,0,0.5),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md",
              "w-[220px]",
              isSmallScreen ? "right-3 top-40 max-w-[min(72vw,220px)]" : "right-4 top-[7.75rem]"
            )
      )}
      role="complementary"
      aria-label="Map legend"
    >
      <button
        type="button"
        onClick={() =>
          setCollapsed((value) => {
            const next = !value;
            writeCollapsedPreference(next);
            return next;
          })
        }
        className={cn(
          "flex min-h-8 w-full items-center justify-between gap-1.5 px-1.5 py-1 text-left transition-all duration-150 hover:bg-secondary/25 active:bg-secondary/45 pointer-coarse:min-h-11",
          collapsed ? "border-b border-transparent" : "border-b border-border/25"
        )}
        aria-expanded={!collapsed}
        aria-controls="legend-body"
      >
        <span className="block min-w-0 text-sm font-semibold tracking-tight text-foreground/95">
          <span>{legendTitle}</span>
          {unitsSuffix ? <span className="ml-1 text-foreground/60">{unitsSuffix}</span> : null}
        </span>
        {collapsed ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-150" />
        ) : (
          <ChevronUp className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-150" />
        )}
      </button>

      <div
        id="legend-body"
        className={cn("grid transition-[grid-template-rows] duration-200 ease-out", collapsed ? "grid-rows-[0fr]" : "grid-rows-[1fr]")}
      >
        <div className="overflow-hidden">
          <div key={fadeKey} className="flex flex-col gap-1.5 px-1.5 py-1.5 animate-in fade-in duration-200">
            <LegendPresentationBody presentation={presentation} variant="full" />

            {legend.note ? (
              <p className="border-t border-border/25 pt-1 text-[12px] font-medium leading-snug text-foreground/68">
                {legend.note}
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
