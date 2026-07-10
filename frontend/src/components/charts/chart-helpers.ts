import uPlot from "uplot";

import { CHART_THEME } from "@/lib/chart-constants";
import {
  formatTooltipTime,
  formatXTick,
  localDayBoundaries,
  localNoonSec,
  localYMD,
  toTimestampSec,
} from "@/lib/chart-time";
import type { MeteogramPoint, MeteogramResponse } from "@/lib/meteogram-types";

export { toTimestampSec };

/** Native forecast points for one model+variable, or null when none are usable. */
export function seriesPoints(
  response: MeteogramResponse | null,
  model: string,
  variable: string,
): MeteogramPoint[] | null {
  const series = response?.series?.[model];
  if (!series || (series.status !== "ok" && series.status !== "partial")) return null;
  const points = series.variables?.[variable]?.points;
  return Array.isArray(points) && points.length > 0 ? points : null;
}

/**
 * Daily high/low per local calendar day for one model, plotted at local noon.
 *
 * A trailing day whose forecast coverage stops before midday is dropped: such a
 * day has only early-morning samples, so its high/low collapses toward a single
 * value (e.g. a model whose data ends at +360h/06z shows high == low for that
 * day). A day is kept only when it has samples on BOTH sides of local noon, so
 * both the morning low and the afternoon high are represented. Interior days
 * always have full diurnal coverage, so in practice only the trailing partial
 * day is trimmed — the final bar is always a true daily high/low. Display-only;
 * the underlying forecast is untouched.
 */
export function dailyHighLow(
  response: MeteogramResponse | null,
  model: string,
  tz: string,
): { x: number; high: number; low: number }[] {
  const points = seriesPoints(response, model, "tmp2m");
  if (!points) return [];
  const byDay = new Map<
    string,
    { noon: number; vals: number[]; hasMorning: boolean; hasAfternoon: boolean }
  >();
  for (const point of points) {
    if (!point.valid_time || point.value == null) continue;
    const ms = new Date(point.valid_time).getTime();
    if (!Number.isFinite(ms)) continue;
    const { year, month, day } = localYMD(ms, tz);
    const key = `${year}-${month}-${day}`;
    let entry = byDay.get(key);
    if (!entry) {
      entry = { noon: localNoonSec(ms, tz), vals: [], hasMorning: false, hasAfternoon: false };
      byDay.set(key, entry);
    }
    entry.vals.push(point.value);
    if (Math.floor(ms / 1000) < entry.noon) entry.hasMorning = true;
    else entry.hasAfternoon = true;
  }
  const rows = [...byDay.values()]
    .map((e) => ({
      x: e.noon,
      high: Math.max(...e.vals),
      low: Math.min(...e.vals),
      complete: e.hasMorning && e.hasAfternoon,
    }))
    .sort((a, b) => a.x - b.x);
  // Trim trailing incomplete day(s) so the final bar is a true daily high/low.
  while (rows.length > 0 && !rows[rows.length - 1]!.complete) rows.pop();
  return rows.map(({ x, high, low }) => ({ x, high, low }));
}

/** Timestamps (sec) where this model has a native frame (value may still be null). */
export function nativeTimestampsForModel(
  response: MeteogramResponse | null,
  model: string,
  variable: string,
): Set<number> {
  const points = seriesPoints(response, model, variable);
  const timestamps = new Set<number>();
  if (!points) return timestamps;
  for (const point of points) {
    if (!point.valid_time) continue;
    const ts = toTimestampSec(point.valid_time);
    if (ts != null) timestamps.add(ts);
  }
  return timestamps;
}

export type AlignedSeries = {
  data: uPlot.AlignedData;
  hasData: boolean;
  nativeTimestampsByModel: Map<string, Set<number>>;
};

/**
 * Build a shared union x-axis (all models' native timestamps) plus one aligned
 * value array per model. Slots without a native frame are null.
 */
export function buildAlignedData(
  response: MeteogramResponse | null,
  models: readonly string[],
  variable: string,
): AlignedSeries {
  const xsSet = new Set<number>();
  const nativeTimestampsByModel = new Map<string, Set<number>>();

  for (const model of models) {
    const nativeTs = nativeTimestampsForModel(response, model, variable);
    nativeTimestampsByModel.set(model, nativeTs);
    for (const ts of nativeTs) xsSet.add(ts);
  }

  const xs = [...xsSet].sort((a, b) => a - b);
  const indexByTs = new Map(xs.map((ts, idx) => [ts, idx]));

  const seriesArrays: (number | null)[][] = models.map((model) => {
    const arr: (number | null)[] = new Array(xs.length).fill(null);
    const points = seriesPoints(response, model, variable);
    if (points) {
      for (const point of points) {
        if (!point.valid_time) continue;
        const ts = toTimestampSec(point.valid_time);
        if (ts == null) continue;
        const idx = indexByTs.get(ts);
        if (idx != null) arr[idx] = point.value;
      }
    }
    return arr;
  });

  const data = [xs, ...seriesArrays] as unknown as uPlot.AlignedData;
  return { data, hasData: xs.length > 0, nativeTimestampsByModel };
}

/**
 * Build a shared union x-axis across several point series of ONE model (the
 * ensemble stats charts align p10…p90 + mean, or one line per probability
 * threshold). Null entries in `seriesList` produce all-null value arrays so
 * callers can keep positional series indices stable.
 */
export function alignPointSeries(seriesList: readonly (MeteogramPoint[] | null)[]): {
  data: uPlot.AlignedData;
  hasData: boolean;
} {
  const xsSet = new Set<number>();
  for (const points of seriesList) {
    if (!points) continue;
    for (const point of points) {
      if (!point.valid_time) continue;
      const ts = toTimestampSec(point.valid_time);
      if (ts != null) xsSet.add(ts);
    }
  }
  const xs = [...xsSet].sort((a, b) => a - b);
  const indexByTs = new Map(xs.map((ts, idx) => [ts, idx]));
  const arrays: (number | null)[][] = seriesList.map((points) => {
    const arr: (number | null)[] = new Array(xs.length).fill(null);
    if (points) {
      for (const point of points) {
        if (!point.valid_time) continue;
        const ts = toTimestampSec(point.valid_time);
        if (ts == null) continue;
        const idx = indexByTs.get(ts);
        if (idx != null) arr[idx] = point.value;
      }
    }
    return arr;
  });
  return {
    data: [xs, ...arrays] as unknown as uPlot.AlignedData,
    hasData: xs.length > 0,
  };
}

/**
 * Span null slots on the shared union x-axis only when the gap is due to another
 * model's denser cadence — not when this model has a native frame with a missing
 * value at an intermediate timestamp.
 */
export function shouldSpanCadenceGap(
  xs: number[],
  nativeTimestamps: Set<number>,
  idx0: number,
  idx1: number,
): boolean {
  for (let i = idx0 + 1; i < idx1; i++) {
    if (nativeTimestamps.has(xs[i]!)) return false;
  }
  return true;
}

/** First non-empty units string for the variable across the given models. */
export function resolveUnits(
  response: MeteogramResponse | null,
  models: readonly string[],
  variable: string,
  fallback: string,
): string {
  for (const model of models) {
    const u = response?.series?.[model]?.variables?.[variable]?.units;
    if (u) return u;
  }
  return fallback;
}

// ── uPlot plugins ──────────────────────────────────────────────────────────

/** Dashed vertical "Now" marker at the current instant. */
export function nowMarkerPlugin(nowSec: number): uPlot.Plugin {
  return {
    hooks: {
      draw: (u: uPlot) => {
        const { ctx } = u;
        const [min, max] =
          u.scales.x.min != null && u.scales.x.max != null
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

/** Vertical gridlines at 00:00 local day boundaries. */
export function dayBoundaryPlugin(tz: string): uPlot.Plugin {
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

/**
 * Floating HTML tooltip that follows the cursor's X position and lists ALL
 * series values at that time (not just the nearest line). Flips to the cursor's
 * left when past the chart midpoint so it never clips. Replaces uPlot's built-in
 * legend — set `legend: { show: false }` on charts that use it. Series labels +
 * value formatters are reused verbatim, so the legend content moves here.
 */
export function cursorTooltipPlugin(tz: string): uPlot.Plugin {
  let tip: HTMLDivElement | null = null;
  const hide = () => {
    if (tip) tip.style.display = "none";
  };

  return {
    hooks: {
      init: (u: uPlot) => {
        tip = document.createElement("div");
        Object.assign(tip.style, {
          position: "absolute",
          top: "0",
          left: "0",
          display: "none",
          pointerEvents: "none",
          zIndex: "10",
          padding: "6px 8px",
          borderRadius: "8px",
          border: `1px solid ${CHART_THEME.gridline}`,
          background: CHART_THEME.cardBackground,
          boxShadow: "0 4px 16px rgba(0, 0, 0, 0.45)",
          font: `${CHART_THEME.tickFontSize}px ui-sans-serif, system-ui, sans-serif`,
          whiteSpace: "nowrap",
        });
        u.over.appendChild(tip);
      },
      setCursor: (u: uPlot) => {
        if (!tip) return;
        const idx = u.cursor.idx;
        const left = u.cursor.left;
        const top = u.cursor.top;
        if (idx == null || left == null || left < 0) {
          hide();
          return;
        }

        const xs = u.data[0] as number[];
        let rows = "";
        for (let i = 1; i < u.series.length; i++) {
          const s = u.series[i];
          if (s.show === false) continue;
          const raw = (u.data[i] as (number | null)[])[idx] ?? null;
          const valStr =
            typeof s.value === "function"
              ? (s.value as (u: uPlot, v: number | null, si: number, di: number) => string)(
                  u,
                  raw,
                  i,
                  idx,
                )
              : raw == null
                ? "—"
                : String(raw);
          const color =
            typeof s.stroke === "function"
              ? (s.stroke as (u: uPlot, si: number) => string)(u, i)
              : ((s.stroke as string | undefined) ?? "#fff");
          rows +=
            `<div style="display:flex;align-items:center;gap:6px;margin-top:3px;">` +
            `<span style="width:8px;height:8px;border-radius:9999px;background:${color};flex:none;"></span>` +
            `<span style="color:rgba(255,255,255,0.55);">${s.label ?? ""}</span>` +
            `<span style="margin-left:auto;padding-left:14px;color:${CHART_THEME.titleColor};">${valStr}</span>` +
            `</div>`;
        }
        if (!rows) {
          hide();
          return;
        }

        tip.innerHTML =
          `<div style="color:rgba(255,255,255,0.85);font-weight:500;">${formatTooltipTime(xs[idx]!, tz)}</div>` +
          rows;
        tip.style.display = "block";

        const w = u.over.clientWidth;
        const h = u.over.clientHeight;
        const tw = tip.offsetWidth;
        const th = tip.offsetHeight;
        const pad = 12;
        // Flip to the cursor's left when past the chart midpoint; clamp in-bounds.
        let tl = left > w / 2 ? left - tw - pad : left + pad;
        tl = Math.max(0, Math.min(tl, Math.max(0, w - tw)));
        const baseTop = top != null && top >= 0 ? top : 0;
        const tt = Math.max(0, Math.min(baseTop + pad, Math.max(0, h - th)));
        tip.style.transform = `translate(${tl}px, ${tt}px)`;
      },
      destroy: () => {
        tip?.remove();
        tip = null;
      },
    },
  };
}

// ── Shared axes ─────────────────────────────────────────────────────────────

const AXIS_FONT = `${CHART_THEME.tickFontSize}px ui-sans-serif, system-ui, sans-serif`;

/** Time x-axis with `EEE h a` / `MMM d` tick labels in `tz`. */
export function timeXAxis(tz: string): uPlot.Axis {
  return {
    stroke: CHART_THEME.axisLabel,
    grid: { stroke: CHART_THEME.gridline, width: 1 },
    ticks: { stroke: CHART_THEME.gridline, width: 1 },
    font: AXIS_FONT,
    values: (u, splits) => {
      const min = u.scales.x.min ?? splits[0] ?? 0;
      const max = u.scales.x.max ?? splits[splits.length - 1] ?? 0;
      const spanSec = max - min;
      return splits.map((s) => formatXTick(s, tz, spanSec));
    },
  };
}

/** Value y-axis; `format` renders each tick (e.g. with a units suffix). */
export function valueYAxis(
  format: (v: number) => string,
  opts: { size?: number; scale?: string; side?: 0 | 1 | 2 | 3; space?: number } = {},
): uPlot.Axis {
  const minSize = opts.size ?? 48;

  return {
    stroke: CHART_THEME.axisLabel,
    grid: { stroke: CHART_THEME.gridline, width: 1 },
    ticks: { stroke: CHART_THEME.gridline, width: 1 },
    font: AXIS_FONT,
    ...(opts.scale ? { scale: opts.scale } : {}),
    ...(opts.side != null ? { side: opts.side } : {}),
    // Min px between ticks fed to uPlot's increment finder. Smaller → more ticks
    // → finer increments; compact strips set this so they show several nice
    // round increments (e.g. 0/5/10 mph) instead of just two coarse ones.
    ...(opts.space != null ? { space: opts.space } : {}),
    // uPlot passes filter() output — null slots must stay null (Math.round(null) === 0).
    values: (u, splits) =>
      splits.map((v) => (v == null || !Number.isFinite(Number(v)) ? null : format(Number(v)))),
    // Size to the widest tick label so double-digit values + units are not clipped
    // (fixed 48px truncates "10 mph" → "0 mph", "15 mph" → "5 mph").
    size: (u, values) => {
      if (!values || values.length === 0) return minSize;
      const ctx = u.ctx;
      const prevFont = ctx.font;
      ctx.font = AXIS_FONT;
      let maxW = 0;
      for (const label of values) {
        if (label == null) continue;
        maxW = Math.max(maxW, ctx.measureText(String(label)).width);
      }
      ctx.font = prevFont;
      return Math.max(minSize, Math.ceil(maxW) + 14);
    },
  };
}

/** Auto y-range with 5% headroom; used by the shared line charts. */
export function paddedRange(dataMin: number | null, dataMax: number | null): [number, number] {
  if (dataMin == null || dataMax == null) return [0, 1];
  const pad = Math.max(1, (dataMax - dataMin) * 0.05);
  return [dataMin - pad, dataMax + pad];
}
