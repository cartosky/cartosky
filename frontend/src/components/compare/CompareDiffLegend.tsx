import { useMemo } from "react";

import type { LegendPayload } from "@/components/map-legend";
import { deriveDiffLegendTicks } from "@/lib/compare-diff-scales";

type CompareDiffLegendProps = {
  legend: LegendPayload;
};

function formatTick(value: number): string {
  const rounded = Number.isInteger(value) ? value : Number(value.toFixed(2));
  if (rounded > 0) {
    return `+${rounded}`;
  }
  return String(rounded);
}

/**
 * Bottom-center diverging legend for difference mode. Renders the blue→white→red
 * gradient from the legend stops with symmetric tick marks. Render-only.
 */
export function CompareDiffLegend({ legend }: CompareDiffLegendProps) {
  const entries = useMemo(
    () => [...legend.entries].sort((a, b) => a.value - b.value),
    [legend.entries],
  );

  const maxAbs = useMemo(() => {
    const values = entries.map((entry) => Math.abs(entry.value));
    return values.length > 0 ? Math.max(...values) : 0;
  }, [entries]);

  const gradient = useMemo(() => {
    if (maxAbs <= 0 || entries.length === 0) {
      return "linear-gradient(to right, #2166ac, #f7f7f7, #d6604d)";
    }
    const stops = entries.map((entry) => {
      const position = ((entry.value + maxAbs) / (2 * maxAbs)) * 100;
      return `${entry.color} ${position.toFixed(1)}%`;
    });
    return `linear-gradient(to right, ${stops.join(", ")})`;
  }, [entries, maxAbs]);

  const ticks = useMemo(() => (maxAbs > 0 ? deriveDiffLegendTicks(maxAbs) : []), [maxAbs]);

  return (
    <div className="pointer-events-none absolute bottom-24 left-1/2 z-30 w-[min(360px,calc(100vw-2rem))] -translate-x-1/2">
      <div className="rounded-xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.85] px-3 py-2 shadow-[0_8px_32px_rgba(0,0,0,0.5),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md">
        <div className="mb-1.5 flex items-baseline justify-between gap-2">
          <span className="truncate text-[11px] font-semibold text-white/85">{legend.title}</span>
          {legend.units ? (
            <span className="shrink-0 font-['IBM_Plex_Mono',monospace] text-[10px] font-medium text-cyan-200/75">
              {legend.units}
            </span>
          ) : null}
        </div>
        <div
          className="h-2.5 w-full rounded-full"
          style={{ backgroundImage: gradient }}
        />
        <div className="relative mt-1 h-3.5">
          {ticks.map((tick) => {
            const position = maxAbs > 0 ? ((tick + maxAbs) / (2 * maxAbs)) * 100 : 50;
            return (
              <span
                key={tick}
                className="absolute -translate-x-1/2 font-['IBM_Plex_Mono',monospace] text-[9px] font-medium text-white/55"
                style={{ left: `${position}%` }}
              >
                {formatTick(tick)}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default CompareDiffLegend;
