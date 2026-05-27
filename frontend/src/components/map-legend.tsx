import { useEffect, useRef, useState, type Ref } from "react";
import { AlertCircle, ChevronDown, ChevronUp } from "lucide-react";

import { cn } from "@/lib/utils";

export type LegendEntry = {
  value: number;
  color: string;
  label?: string;
};

export type LegendPayload = {
  title: string;
  units?: string;
  kind?: string;
  id?: string;
  note?: string;
  ptype_breaks?: Record<string, { offset: number; count: number }>;
  ptype_order?: string[];
  bins_per_ptype?: number;
  entries: LegendEntry[];
  opacity: number;
};

function formatValue(value: number): string {
  if (Number.isInteger(value)) return value.toString();
  if (Math.abs(value) < 0.1) return value.toFixed(2);
  return value.toFixed(1);
}

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

function UnavailablePlaceholder() {
  return (
    <div className="flex items-center gap-1.5 rounded-xl glass px-2.5 py-2">
      <AlertCircle className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
      <span className="text-xs font-medium text-muted-foreground/80">Legend unavailable</span>
    </div>
  );
}

const RADAR_GROUP_LABELS = ["Rain", "Snow", "Sleet", "Freezing Rain"];
const DEFAULT_PTYPE_ORDER = ["rain", "snow", "sleet", "frzr"];
const LEGEND_COLLAPSED_STORAGE_KEY = "twf.legend.collapsed";

type RadarLegendGroup = {
  label: string;
  entries: LegendEntry[];
};

type PtypeIntensityLegendRow = {
  label: string;
  min: number;
  max: number;
  colors: string[];
};

type RadarLegendRow = {
  label: string;
  colors: string[];
};

function radarGroupLabelForCode(code: string, index: number): string {
  const normalized = code.toLowerCase();
  if (normalized === "rain") return "Rain";
  if (normalized === "snow") return "Snow";
  if (normalized === "sleet") return "Sleet";
  if (normalized === "ice") return "Ice";
  if (normalized === "frzr") return "Freezing Rain";
  return RADAR_GROUP_LABELS[index] ?? `Type ${index + 1}`;
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

function isRadarPtypeLegend(legend: LegendPayload): boolean {
  const kind = legend.kind?.toLowerCase() ?? "";
  const id = legend.id?.toLowerCase() ?? "";
  return (
    kind.includes("radar_ptype") ||
    kind.includes("radar_ptype_combo") ||
    id.includes("radar") ||
    id === "radar_ptype"
  );
}

function isPtypeIntensityLegend(legend: LegendPayload): boolean {
  const id = legend.id?.toLowerCase() ?? "";
  return id === "ptype_intensity";
}

function isCategoricalLegend(legend: LegendPayload): boolean {
  const kind = legend.kind?.toLowerCase() ?? "";
  if (kind === "categorical") {
    return true;
  }
  return legend.entries.length > 0 && legend.entries.every((entry) => typeof entry.label === "string" && entry.label.trim().length > 0);
}

function buildDenseLegendTicks(entries: LegendEntry[], targetCount = 6): LegendEntry[] {
  const displayed = entries.slice().reverse();
  if (displayed.length === 0) return [];

  const lastIndex = displayed.length - 1;
  const indices = Array.from({ length: targetCount }, (_, index) => {
    const ratio = targetCount === 1 ? 0 : index / (targetCount - 1);
    return Math.round(ratio * lastIndex);
  }).filter((value, index, arr) => index === 0 || value !== arr[index - 1]);

  return indices.map((index) => displayed[index]);
}

function formatDenseTickValue(value: number): string {
  const absValue = Math.abs(value);
  // For small values, preserve actual precision rather than collapsing to "0"
  if (absValue < 1) {
    return formatValue(value);
  }
  if (absValue >= 20) {
    return formatValue(Math.round(value / 10) * 10);
  }
  if (absValue >= 5) {
    return formatValue(Math.round(value / 5) * 5);
  }
  return formatValue(Math.round(value));
}

function splitLegendTitle(title: string, units?: string): { title: string; unitsSuffix: string | null } {
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

function HorizontalGradientLegend({ entries }: { entries: LegendEntry[] }) {
  // entries arrive high-to-low; reverse so index 0 = lowest value = left side
  const displayed = entries.slice().reverse();
  // Build ticks from the low-to-high displayed array
  const ticks = buildDenseLegendTicks(displayed.slice().reverse(), 6)
    .map((tick) => displayed.findIndex((e) => e === tick))
    .filter((i) => i !== -1)
    .map((i) => ({ entry: displayed[i]!, displayedIndex: i }));
  const stopCount = Math.max(displayed.length - 1, 1);
  const gradientStops = displayed
    .map((entry, index) => `${entry.color} ${(index / stopCount) * 100}%`)
    .join(", ");

  return (
    <div className="px-0.5 py-1.5">
      <div className="rounded-[14px] bg-black/14 p-[3px] ring-1 ring-inset ring-white/12 shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_6px_16px_rgba(0,0,0,0.18)]">
        <div
          className="h-3 rounded-[7px] shadow-[inset_0_1px_0_rgba(255,255,255,0.16)]"
          style={{
            backgroundImage:
              displayed.length === 1 ? undefined : `linear-gradient(to right, ${gradientStops})`,
            backgroundColor: displayed.length === 1 ? displayed[0]?.color : undefined,
          }}
        />
      </div>
      {/* Tick marks */}
      <div className="relative mt-[3px] h-[5px]">
        {ticks.map(({ entry, displayedIndex }, index) => {
          const offset = stopCount === 0 ? 0 : (displayedIndex / stopCount) * 100;
          const isFirst = index === 0;
          const isLast = index === ticks.length - 1;
          return (
            <div
              key={`tick-${entry.value}-${index}`}
              className="absolute top-0 h-full w-px bg-white/40"
              style={{
                left: `${offset}%`,
                transform: isFirst ? "none" : isLast ? "translateX(-100%)" : "translateX(-50%)",
              }}
            />
          );
        })}
      </div>
      {/* Tick labels */}
      <div className="relative mt-0.5 h-4">
        {ticks.map(({ entry, displayedIndex }, index) => {
          const offset = stopCount === 0 ? 0 : (displayedIndex / stopCount) * 100;
          const isFirst = index === 0;
          const isLast = index === ticks.length - 1;
          return (
            <div
              key={`label-${entry.value}-${index}`}
              className="absolute top-0"
              style={{
                left: `${offset}%`,
                transform: isFirst ? "none" : isLast ? "translateX(-100%)" : "translateX(-50%)",
              }}
            >
              <span className="font-mono text-[10px] font-semibold leading-none tabular-nums tracking-tight text-foreground/95 whitespace-nowrap">
                {formatDenseTickValue(entry.value)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CategoricalLegendEntries({ entries }: { entries: LegendEntry[] }) {
  return (
    <div className="legend-scroll max-h-[45vh] space-y-px overflow-y-auto scroll-smooth">
      {entries.slice().reverse().map((entry, index) => (
        <div
          key={`${entry.value}-${entry.color}-${index}`}
          className="flex items-center gap-1.5 rounded-[2px] px-0.5 py-0.5 transition-colors duration-150"
        >
          <div
            className="h-3 w-3 shrink-0 rounded-[2px] border border-border/30 shadow-sm"
            style={{ backgroundColor: entry.color }}
          />
          <span
            className={
              entry.label
                ? "text-[10px] font-medium leading-none tracking-tight text-foreground/95"
                : "font-mono text-[10px] font-medium leading-none tabular-nums tracking-tight text-foreground/95"
            }
          >
            {entry.label?.trim() || formatValue(entry.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

function groupRadarEntries(
  entries: LegendEntry[],
  ptypeBreaks?: Record<string, { offset: number; count: number }>,
  ptypeOrder?: string[]
): RadarLegendGroup[] {
  const isZero = (value: number) => Math.abs(value) < 1e-9;

  if (ptypeBreaks) {
    const orderedTypes = (Array.isArray(ptypeOrder) && ptypeOrder.length > 0 ? ptypeOrder : DEFAULT_PTYPE_ORDER).filter(
      (ptype) => ptypeBreaks[ptype]
    );
    const groupedByMeta: RadarLegendGroup[] = [];

    for (let index = 0; index < orderedTypes.length; index += 1) {
      const ptype = orderedTypes[index];
      const boundary = ptypeBreaks[ptype];
      if (!boundary) continue;
      const offset = Number(boundary.offset);
      const count = Number(boundary.count);
      if (!Number.isFinite(offset) || !Number.isFinite(count) || offset < 0 || count <= 0) {
        continue;
      }
      const slice = entries.slice(offset, offset + count);
      if (slice.length === 0) continue;
      groupedByMeta.push({
        label: radarGroupLabelForCode(ptype, index),
        entries: slice,
      });
    }

    if (groupedByMeta.length > 0) {
      return groupedByMeta;
    }
  }

  // Fallback: split sequence on zero-value delimiters in native order.
  // Reversing here flips group labels (rain↔frzr, snow↔sleet) when
  // sidecars don't provide ptype metadata.
  const displayed = entries.slice();
  const fallbackGroups: RadarLegendGroup[] = [];
  let current: LegendEntry[] = [];

  for (const entry of displayed) {
    if (isZero(entry.value)) {
      if (current.length > 0) {
        fallbackGroups.push({
          label: RADAR_GROUP_LABELS[fallbackGroups.length] ?? `Type ${fallbackGroups.length + 1}`,
          entries: current,
        });
        current = [];
      }
      continue;
    }
    current.push(entry);
  }

  if (current.length > 0) {
    fallbackGroups.push({
      label: RADAR_GROUP_LABELS[fallbackGroups.length] ?? `Type ${fallbackGroups.length + 1}`,
      entries: current,
    });
  }

  return fallbackGroups;
}

function groupPtypeIntensityRows(
  entries: LegendEntry[],
  ptypeBreaks?: Record<string, { offset: number; count: number }>,
  ptypeOrder?: string[]
): PtypeIntensityLegendRow[] {
  if (!ptypeBreaks) return [];
  const orderedTypes = (Array.isArray(ptypeOrder) && ptypeOrder.length > 0 ? ptypeOrder : []).filter(
    (ptype) => ptypeBreaks[ptype]
  );
  if (orderedTypes.length === 0) return [];

  const rows: PtypeIntensityLegendRow[] = [];
  for (let index = 0; index < orderedTypes.length; index += 1) {
    const ptype = orderedTypes[index];
    const boundary = ptypeBreaks[ptype];
    if (!boundary) continue;
    const offset = Number(boundary.offset);
    const count = Number(boundary.count);
    if (!Number.isFinite(offset) || !Number.isFinite(count) || offset < 0 || count <= 0) {
      continue;
    }
    const segment = entries.slice(offset, offset + count);
    if (segment.length === 0) continue;
    const colors = segment.map((entry) => entry.color).filter(Boolean);
    if (colors.length === 0) continue;
    const min = Number(segment[0]?.value);
    const max = Number(segment[segment.length - 1]?.value);
    if (!Number.isFinite(min) || !Number.isFinite(max)) continue;
    rows.push({
      label: radarGroupLabelForCode(ptype, index),
      min,
      max,
      colors,
    });
  }

  return rows;
}

function buildRadarLegendRows(groups: RadarLegendGroup[]): RadarLegendRow[] {
  return groups
    .map((group) => ({
      label: group.label,
      colors: group.entries.map((entry) => entry.color).filter(Boolean),
    }))
    .filter((row) => row.colors.length > 0);
}

function RadarGradientRows({ groups }: { groups: RadarLegendGroup[] }) {
  const rows = buildRadarLegendRows(groups);

  return (
    <div className="space-y-2">
      {rows.map((row, index) => (
        <div key={`${row.label}-${index}`} className={cn(index > 0 ? "border-t border-border/18 pt-2" : "")}>
          <div className="mb-1 px-0.5 text-[9px] font-semibold uppercase tracking-[0.12em] text-foreground/78">
            {row.label}
          </div>
          <div className="rounded-lg bg-black/16 p-[3px] ring-1 ring-inset ring-white/10 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
            <div
              className="h-3 rounded-[5px]"
              style={{
                backgroundImage:
                  row.colors.length === 1 ? undefined : `linear-gradient(to right, ${row.colors.join(", ")})`,
                backgroundColor: row.colors.length === 1 ? row.colors[0] : undefined,
              }}
            />
          </div>
          <div className="mt-1 flex items-center justify-between px-0.5 font-mono text-[8px] font-medium uppercase tracking-[0.08em] text-foreground/58">
            <span>Light</span>
            <span>Heavy</span>
          </div>
        </div>
      ))}
    </div>
  );
}

type MapLegendProps = {
  legend: LegendPayload | null;
  containerRef?: Ref<HTMLDivElement>;
  defaultExpanded?: boolean;
  /** When true, renders inline (no fixed positioning). Use inside popovers/portals. */
  inline?: boolean;
};

export function MapLegend({
  legend,
  containerRef,
  defaultExpanded = false,
  inline = false,
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

  if (!legend) {
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
  const ptypeIntensityRows = isPtypeIntensityLegend(legend)
    ? groupPtypeIntensityRows(legend.entries, legend.ptype_breaks, legend.ptype_order)
    : [];
  const showPtypeIntensityRows = ptypeIntensityRows.length > 0;
  const groupedRadarEntries = isRadarPtypeLegend(legend)
    ? groupRadarEntries(legend.entries, legend.ptype_breaks, legend.ptype_order)
    : [];
  const showGroupedRadar = groupedRadarEntries.length > 0;
  const showCategoricalLegend = isCategoricalLegend(legend);

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
          "flex w-full items-center justify-between gap-1.5 px-1.5 py-1 text-left transition-all duration-150 hover:bg-secondary/25 active:bg-secondary/45",
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
            <div>
              {showPtypeIntensityRows
                ? ptypeIntensityRows.map((row, rowIndex) => (
                    <div
                      key={`precip-row-${row.label}-${rowIndex}`}
                      className={cn(rowIndex > 0 ? "mt-2 border-t border-border/20 pt-2" : "")}
                    >
                      <div className="mb-1 flex items-center justify-between gap-2 px-0.5">
                        <span className="text-[9px] font-medium uppercase tracking-wide text-foreground/62">
                          {row.label}
                        </span>
                        <span className="font-mono text-[9px] font-medium tabular-nums text-foreground/90">
                          {formatValue(row.min)}-{formatValue(row.max)} {legend.units ?? ""}
                        </span>
                      </div>
                      <div
                        className="h-3 rounded-[2px] border border-border/40 shadow-sm"
                        style={{ backgroundImage: `linear-gradient(to right, ${row.colors.join(", ")})` }}
                      />
                    </div>
                  ))
                : showGroupedRadar
                ? <RadarGradientRows groups={groupedRadarEntries} />
                : showCategoricalLegend
                ? <CategoricalLegendEntries entries={legend.entries} />
                : <HorizontalGradientLegend entries={legend.entries} />}
            </div>

            {legend.note ? (
              <p className="border-t border-border/25 pt-1 text-[9px] font-medium leading-snug text-foreground/68">
                {legend.note}
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
