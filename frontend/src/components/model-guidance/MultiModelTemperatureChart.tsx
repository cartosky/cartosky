import { useMemo } from "react";
import uPlot from "uplot";

import { UplotChart } from "@/components/charts/UplotChart";
import { CHART_THEME, modelColor, modelShortName } from "@/lib/chart-constants";
import type { MeteogramResponse } from "@/hooks/useMeteogram";

// Fixed draw order for temperature lines.
const MODEL_ORDER = ["ecmwf", "gfs", "nam", "aifs", "nbm"];
const CHART_HEIGHT = 320;

type Props = {
  response: MeteogramResponse | null;
  visibleModels: Set<string>;
  timezone: string | null;
  nowMs?: number;
};

function unitsLabel(units: string | null | undefined): string {
  if (!units) return "°F";
  if (units === "F" || units === "C") return `°${units}`;
  return units;
}

function tmp2mPoints(response: MeteogramResponse | null, model: string) {
  const series = response?.series?.[model];
  if (!series || (series.status !== "ok" && series.status !== "partial")) return null;
  const points = series.variables?.tmp2m?.points;
  return Array.isArray(points) && points.length > 0 ? points : null;
}

// uPlot plugin: dashed vertical "Now" marker at the current instant.
function nowMarkerPlugin(nowSec: number): uPlot.Plugin {
  return {
    hooks: {
      draw: (u: uPlot) => {
        const { ctx } = u;
        const [min, max] = u.scales.x.min != null && u.scales.x.max != null
          ? [u.scales.x.min, u.scales.x.max]
          : [nowSec, nowSec];
        if (nowSec < min || nowSec > max) return;
        const x = Math.round(u.valToPos(nowSec, "x", true));
        const top = Math.round(u.bbox.top);
        const bottom = Math.round(u.bbox.top + u.bbox.height);
        ctx.save();
        ctx.strokeStyle = CHART_THEME.nowMarker;
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(x, top);
        ctx.lineTo(x, bottom);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = CHART_THEME.nowMarker;
        ctx.font = `${CHART_THEME.tickFontSize}px ui-sans-serif, system-ui, sans-serif`;
        ctx.textAlign = "left";
        ctx.fillText("Now", x + 4, top + 12);
        ctx.restore();
      },
    },
  };
}

// ── Timezone helpers (DST-safe via Intl; no fixed-offset assumptions) ──────

// Offset (ms) such that local_wall_clock_ms = utcMs + offset, for `tz` at the
// given instant. Derived by formatting the instant in `tz` and diffing.
function tzOffsetMs(utcMs: number, tz: string): number {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const map: Record<string, string> = {};
  for (const part of dtf.formatToParts(new Date(utcMs))) {
    if (part.type !== "literal") map[part.type] = part.value;
  }
  const asUTC = Date.UTC(
    Number(map.year),
    Number(map.month) - 1,
    Number(map.day),
    Number(map.hour),
    Number(map.minute),
    Number(map.second),
  );
  return asUTC - utcMs;
}

// UTC instant (ms) of 00:00 local time on the given calendar date in `tz`.
// Two-pass refine handles DST transition days.
function localMidnightMs(year: number, month: number, day: number, tz: string): number {
  const naive = Date.UTC(year, month - 1, day, 0, 0, 0);
  let t = naive - tzOffsetMs(naive, tz);
  t = naive - tzOffsetMs(t, tz);
  return t;
}

function localYMD(utcMs: number, tz: string): { year: number; month: number; day: number } {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const map: Record<string, string> = {};
  for (const part of dtf.formatToParts(new Date(utcMs))) {
    if (part.type !== "literal") map[part.type] = part.value;
  }
  return { year: Number(map.year), month: Number(map.month), day: Number(map.day) };
}

// Unix seconds of each 00:00-local day boundary within [minSec, maxSec].
function localDayBoundaries(minSec: number, maxSec: number, tz: string): number[] {
  if (!Number.isFinite(minSec) || !Number.isFinite(maxSec) || maxSec <= minSec) return [];
  const out: number[] = [];
  const start = localYMD(minSec * 1000, tz);
  let cur = localMidnightMs(start.year, start.month, start.day, tz);
  let guard = 0;
  while (cur <= maxSec * 1000 && guard < 400) {
    const sec = Math.floor(cur / 1000);
    if (sec >= minSec && sec <= maxSec) out.push(sec);
    // Advance to the next calendar day. +26h lands inside the next day even
    // across a spring-forward; snap back to that day's local midnight.
    const next = localYMD(cur + 26 * 3600 * 1000, tz);
    cur = localMidnightMs(next.year, next.month, next.day, tz);
    guard += 1;
  }
  return out;
}

// X tick label per plan: `EEE h a` (<48 h span) else `MMM d`, in `tz`.
function formatXTick(sec: number, tz: string, spanSec: number): string {
  const date = new Date(sec * 1000);
  if (spanSec < 48 * 3600) {
    const dtf = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      weekday: "short",
      hour: "numeric",
      hour12: true,
    });
    const map: Record<string, string> = {};
    for (const part of dtf.formatToParts(date)) {
      if (part.type !== "literal") map[part.type] = part.value;
    }
    return `${map.weekday} ${map.hour} ${map.dayPeriod}`;
  }
  return new Intl.DateTimeFormat("en-US", { timeZone: tz, month: "short", day: "numeric" }).format(date);
}

// uPlot plugin: vertical gridlines at 00:00 local day boundaries.
function dayBoundaryPlugin(tz: string): uPlot.Plugin {
  return {
    hooks: {
      draw: (u: uPlot) => {
        const min = u.scales.x.min;
        const max = u.scales.x.max;
        if (min == null || max == null) return;
        const boundaries = localDayBoundaries(min, max, tz);
        if (boundaries.length === 0) return;
        const { ctx } = u;
        const top = Math.round(u.bbox.top);
        const bottom = Math.round(u.bbox.top + u.bbox.height);
        ctx.save();
        ctx.strokeStyle = CHART_THEME.dayBoundary;
        ctx.lineWidth = 1;
        for (const sec of boundaries) {
          const x = Math.round(u.valToPos(sec, "x", true));
          ctx.beginPath();
          ctx.moveTo(x, top);
          ctx.lineTo(x, bottom);
          ctx.stroke();
        }
        ctx.restore();
      },
    },
  };
}

export function MultiModelTemperatureChart({ response, visibleModels, timezone, nowMs }: Props) {
  const activeModels = useMemo(
    () => MODEL_ORDER.filter((model) => visibleModels.has(model) && tmp2mPoints(response, model)),
    [response, visibleModels],
  );

  const units = useMemo(() => {
    for (const model of activeModels) {
      const u = response?.series?.[model]?.variables?.tmp2m?.units;
      if (u) return u;
    }
    return "F";
  }, [response, activeModels]);

  const { data, hasData } = useMemo(() => {
    const xsSet = new Set<number>();
    for (const model of activeModels) {
      const points = tmp2mPoints(response, model);
      if (!points) continue;
      for (const point of points) {
        if (!point.valid_time) continue;
        const ts = Math.floor(new Date(point.valid_time).getTime() / 1000);
        if (Number.isFinite(ts)) xsSet.add(ts);
      }
    }
    const xs = [...xsSet].sort((a, b) => a - b);
    const indexByTs = new Map(xs.map((ts, idx) => [ts, idx]));

    const seriesArrays: (number | null)[][] = activeModels.map((model) => {
      const arr: (number | null)[] = new Array(xs.length).fill(null);
      const points = tmp2mPoints(response, model);
      if (points) {
        for (const point of points) {
          if (!point.valid_time) continue;
          const ts = Math.floor(new Date(point.valid_time).getTime() / 1000);
          const idx = indexByTs.get(ts);
          if (idx != null) arr[idx] = point.value;
        }
      }
      return arr;
    });

    const aligned = [xs, ...seriesArrays] as unknown as uPlot.AlignedData;
    return { data: aligned, hasData: xs.length > 0 };
  }, [response, activeModels]);

  const nowSec = Math.floor((nowMs ?? Date.now()) / 1000);

  const options = useMemo<Omit<uPlot.Options, "width">>(() => {
    const tz = timezone || "UTC";
    const tzDate = (ts: number) => uPlot.tzDate(new Date(ts * 1000), tz);

    return {
      height: CHART_HEIGHT,
      tzDate,
      cursor: { focus: { prox: 24 } },
      legend: { live: true },
      scales: { x: { time: true }, y: { auto: true, range: (_u, dataMin, dataMax) => {
        if (dataMin == null || dataMax == null) return [0, 1];
        const pad = Math.max(1, (dataMax - dataMin) * 0.05);
        return [dataMin - pad, dataMax + pad];
      } } },
      series: [
        {},
        ...activeModels.map((model) => ({
          label: modelShortName(model),
          stroke: modelColor(model),
          width: 2,
          spanGaps: false,
          points: { show: false },
          value: (_u: uPlot, v: number | null) => (v == null ? "—" : `${v} ${unitsLabel(units)}`),
        })),
      ],
      axes: [
        {
          stroke: CHART_THEME.axisLabel,
          grid: { stroke: CHART_THEME.gridline, width: 1 },
          ticks: { stroke: CHART_THEME.gridline, width: 1 },
          font: `${CHART_THEME.tickFontSize}px ui-sans-serif, system-ui, sans-serif`,
          values: (u, splits) => {
            const min = u.scales.x.min ?? splits[0] ?? 0;
            const max = u.scales.x.max ?? splits[splits.length - 1] ?? 0;
            const spanSec = max - min;
            return splits.map((s) => formatXTick(s, tz, spanSec));
          },
        },
        {
          stroke: CHART_THEME.axisLabel,
          grid: { stroke: CHART_THEME.gridline, width: 1 },
          ticks: { stroke: CHART_THEME.gridline, width: 1 },
          font: `${CHART_THEME.tickFontSize}px ui-sans-serif, system-ui, sans-serif`,
          size: 48,
          values: (_u, splits) => splits.map((v) => `${Math.round(v)}${unitsLabel(units)}`),
        },
      ],
      plugins: [dayBoundaryPlugin(tz), nowMarkerPlugin(nowSec)],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeModels, units, timezone, nowSec]);

  if (!hasData) {
    return (
      <div className="flex h-[320px] w-full items-center justify-center text-center text-[13px] text-white/45">
        No temperature guidance available for this location.
      </div>
    );
  }

  return <UplotChart options={options} data={data} className="w-full" />;
}
