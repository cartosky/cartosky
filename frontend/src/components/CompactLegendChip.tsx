import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

import { LegendPresentationBody, splitLegendTitle } from "@/components/map-legend";
import { resolveLegendPresentation, type CompositeLegendLayer, type LegendPayload } from "@/lib/legend-model";
import { cn } from "@/lib/utils";

/**
 * Compact map-corner legend chip — map viewer redesign Phase 7 (§7.2 / §7.3).
 *
 * Product-aware and truthful: it renders the same `resolveLegendPresentation`
 * model the full legend does, so a precipitation-type product always shows
 * EVERY applicable ramp — never "dominant ramp + N more". Under width pressure
 * the repeated per-ramp break labels are dropped before any ramp is (§7.2);
 * `data-compact-density="tight"` is that state.
 *
 * A tap expands the chip in place into the full inline legend; a second tap,
 * Escape, or an outside click returns it to compact.
 *
 * §7.3: the chip is DOM-only and the share capture is canvas-only, so it can
 * never reach an export. `data-export-exclude="legend-chip"` is the explicit
 * marker the Phase 7 contract suite asserts on, so a future capture change
 * that started rasterizing DOM fails loudly instead of shipping two legends.
 *
 * Phase 7 mounts this in the desktop collapsed-rail state only; the mobile
 * mount is Phase 8's (§8 State A).
 */

/** Below this the per-ramp break labels no longer fit beside the ramp label. */
const TIGHT_WIDTH_PX = 200;
const EXPANDED_WIDTH_PX = 232;
/** Compact width = clamp(MIN, (map width) × FRACTION, MAX) — see `chipWidthCss`. */
const CHIP_MIN_WIDTH_PX = 160;
const CHIP_MAX_WIDTH_PX = 240;
const CHIP_WIDTH_FRACTION = 0.22;
const RAIL_COLLAPSED_FALLBACK_PX = 72;

const chipWidthCss = `clamp(${CHIP_MIN_WIDTH_PX}px, calc((100vw - var(--viewer-rail-width, ${RAIL_COLLAPSED_FALLBACK_PX}px)) * ${CHIP_WIDTH_FRACTION}), ${CHIP_MAX_WIDTH_PX}px)`;

/**
 * The same clamp, evaluated from viewport metrics rather than from layout.
 * Reading `innerWidth` (and a custom property) cannot return a stale value the
 * way a `getBoundingClientRect()` inside a resize handler can, so the density
 * state can never latch on a pre-resize measurement.
 */
function computeChipWidth(): number {
  if (typeof window === "undefined") return CHIP_MAX_WIDTH_PX;
  const rawRail = getComputedStyle(document.documentElement).getPropertyValue("--viewer-rail-width");
  const rail = Number.parseFloat(rawRail);
  const railPx = Number.isFinite(rail) ? rail : RAIL_COLLAPSED_FALLBACK_PX;
  const fluid = (window.innerWidth - railPx) * CHIP_WIDTH_FRACTION;
  return Math.min(CHIP_MAX_WIDTH_PX, Math.max(CHIP_MIN_WIDTH_PX, fluid));
}

type CompactLegendChipProps = {
  legend: LegendPayload | null;
  compositeLayers?: CompositeLegendLayer[] | null;
  /**
   * Phase 8 (§8 State A): the mobile mount. POSITION ONLY — the rendering, the
   * density model and `data-export-exclude` are frozen. `desktop` keeps the
   * collapsed-rail corner offset byte-for-byte.
   */
  position?: "desktop" | "mobile";
};

export function CompactLegendChip({ legend, compositeLayers = null, position = "desktop" }: CompactLegendChipProps) {
  const [expanded, setExpanded] = useState(false);
  const [tight, setTight] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const collapse = useCallback(() => setExpanded(false), []);

  const presentation = resolveLegendPresentation(legend, compositeLayers);
  // The chip renders nothing until the legend arrives, so the measurement
  // effect must re-run when it does — with `[]` deps it would attach to a
  // node that did not exist yet and never measure again.
  const rendered = Boolean(legend && presentation);

  useLayoutEffect(() => {
    const element = rootRef.current;
    if (!element) return undefined;
    const measure = () => setTight(computeChipWidth() < TIGHT_WIDTH_PX);
    measure();
    // `resize` covers viewport changes; the observer covers a rail-width
    // change, which fires no window event.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [rendered]);

  useEffect(() => {
    if (!expanded) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        collapse();
      }
    };
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        collapse();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [collapse, expanded]);

  if (!legend || !presentation) {
    return null;
  }

  const { title, unitsSuffix } = splitLegendTitle(legend.title, legend.units);

  return (
    <div
      ref={rootRef}
      data-testid="compact-legend-chip"
      data-export-exclude="legend-chip"
      data-compact-density={tight ? "tight" : "comfortable"}
      data-expanded={expanded ? "true" : "false"}
      role="complementary"
      aria-label="Map legend"
      className={cn(
        "fixed right-4 z-[55] flex flex-col overflow-hidden rounded-xl",
        "border border-[#1a3a5c]/60 bg-[#04101e]/[0.82] shadow-[0_8px_32px_rgba(0,0,0,0.5),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md",
        position === "mobile" && "right-3",
      )}
      style={{
        width: expanded ? `${EXPANDED_WIDTH_PX}px` : chipWidthCss,
        ...(position === "mobile"
          ? // §8 State A: top-right of the MAP, mirroring the desktop corner.
            { top: "calc(var(--viewer-map-top-inset, 0px) + 0.75rem)", maxHeight: "calc(100vh - var(--viewer-map-top-inset, 0px) - var(--viewer-map-bottom-inset, 0px) - 1.5rem)" }
          : // Tucked under the top bar, same seam offset as the rail edge
            // toggle — never a hardcoded height from a previous chrome.
            { top: "calc(var(--viewer-topbar-height, 3rem) + var(--viewer-header-extra, 0px) + 0.75rem)" }),
      }}
    >
      <button
        type="button"
        data-testid="compact-legend-chip-toggle"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-controls="compact-legend-chip-panel"
        title={expanded ? "Collapse legend" : "Expand legend"}
        className="flex min-h-8 w-full items-center justify-between gap-1.5 px-2 py-1 text-left transition-colors duration-150 hover:bg-secondary/25 active:bg-secondary/45 pointer-coarse:min-h-11"
      >
        <span className="block min-w-0 truncate text-[12px] font-semibold tracking-tight text-foreground/95">
          <span>{title}</span>
          {unitsSuffix ? <span className="ml-1 text-foreground/60">{unitsSuffix}</span> : null}
        </span>
        {expanded ? (
          <ChevronUp className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
      </button>

      {expanded ? (
        /* The full body renders directly — nesting <MapLegend> here would
           duplicate both the title row (the chip header already shows it) and
           the "Map legend" complementary landmark. */
        <div id="compact-legend-chip-panel" data-testid="compact-legend-chip-panel" className="border-t border-border/25">
          <div className="flex max-h-[60vh] flex-col gap-1.5 overflow-y-auto px-1.5 py-1.5">
            <LegendPresentationBody presentation={presentation} variant="full" />
            {legend.note ? (
              <p className="border-t border-border/25 pt-1 text-[12px] font-medium leading-snug text-foreground/68">
                {legend.note}
              </p>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="px-2 pb-2 pt-0.5">
          <LegendPresentationBody presentation={presentation} variant="compact" tight={tight} />
        </div>
      )}
    </div>
  );
}

export default CompactLegendChip;
