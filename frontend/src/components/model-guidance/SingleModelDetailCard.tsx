import { useMemo, type ReactNode } from "react";
import uPlot from "uplot";

import { ChartContainer } from "@/components/charts/ChartContainer";
import { UplotChart } from "@/components/charts/UplotChart";
import {
  cursorTooltipPlugin,
  dailyHighLow,
  dayBoundaryPlugin,
  seriesPoints,
  timeXAxis,
  toTimestampSec,
  valueYAxis,
} from "@/components/charts/chart-helpers";
import { sixHourSteps } from "@/components/model-guidance/PrecipDetailPanel";
import { CHART_THEME, DETAIL_COLORS, modelShortName } from "@/lib/chart-constants";
import { localNoonSec } from "@/lib/chart-time";
import { parseRunInitLabel } from "@/lib/model-guidance-subtitle";
import type { MeteogramResponse, MeteogramSeries } from "@/lib/meteogram-types";

// Compact strip heights. Only the bottom strip carries an x-axis, so it is a
// little taller. A fixed left-axis width keeps every strip's plot area aligned
// so day-boundary lines line up vertically across strips.
const TEMP_STRIP_HEIGHT = 240;   // taller so the 0–120° (every 10°) labels breathe
const PRECIP_STRIP_HEIGHT = 124; // was 92,  +32 for label area
const CUMUL_STRIP_HEIGHT = 124;  // was 92,  +32 for label area
const WIND_STRIP_HEIGHT = 116;
const STRIP_Y_SIZE = 56;

type Props = {
  response: MeteogramResponse | null;
  model: string | null;
  timezone: string | null;
  /**
   * Location line for the exported image, e.g. "Nashville, TN · 36.1659°N,
   * 86.7844°W". Optional so existing call sites keep compiling until the parent
   * threads it through; defaults to an empty line.
   */
  locationText?: string;
  isLoading: boolean;
  error?: string | null;
  onRetry?: () => void;
  nowMs?: number;
};

function unitsLabel(units: string | null | undefined): string {
  if (!units) return "°F";
  if (units === "F" || units === "C") return `°${units}`;
  return units;
}

/** "12z Jun 24"-style init label from a model series' run id / run time. */
function formatRunInit(series: MeteogramSeries | undefined): string | undefined {
  if (!series) return undefined;
  const init = parseRunInitLabel(series.run_id);
  let dateLabel: string | null = null;
  const match = /(\d{4})(\d{2})(\d{2})/.exec(series.run_id ?? "");
  const ms = match
    ? Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
    : series.run_time
      ? new Date(series.run_time).getTime()
      : NaN;
  if (Number.isFinite(ms)) {
    dateLabel = new Intl.DateTimeFormat("en-US", {
      timeZone: "UTC",
      month: "short",
      day: "numeric",
    }).format(new Date(ms));
  }
  const parts = [init, dateLabel].filter((p): p is string => Boolean(p));
  return parts.length > 0 ? parts.join(" ") : undefined;
}

/** Native (timestamp, value) pairs for a hourly variable; ascending by time. */
function nativeSeries(
  response: MeteogramResponse | null,
  model: string,
  variable: string,
): { xs: number[]; vals: (number | null)[] } {
  const points = seriesPoints(response, model, variable);
  const xs: number[] = [];
  const vals: (number | null)[] = [];
  if (!points) return { xs, vals };
  for (const point of points) {
    if (!point.valid_time) continue;
    const ts = toTimestampSec(point.valid_time);
    if (ts == null) continue;
    xs.push(ts);
    vals.push(point.value);
  }
  return { xs, vals };
}

// Shared option pieces so every strip uses identical cursor/legend/plugins.
function stripBase(
  tz: string,
  _nowSec: number,
): Pick<uPlot.Options, "tzDate" | "cursor" | "focus" | "legend" | "plugins" | "padding"> {
  return {
    tzDate: (ts: number) => uPlot.tzDate(new Date(ts * 1000), tz),
    cursor: { y: false, focus: { prox: 24 } },
    focus: { alpha: 0.3 },
    legend: { show: false },
    // Fixed right gutter so the plot area's right edge is identical on every
    // strip (the x-axis strip would otherwise auto-reserve extra right space),
    // keeping day-boundary lines aligned vertically. Left/top/bottom stay auto.
    padding: [null, 12, null, null],
    // No "Now" marker in detail mode — the card is meant for a clean, shareable
    // multi-day snapshot rather than a live cursor.
    plugins: [dayBoundaryPlugin(tz), cursorTooltipPlugin(tz)],
  };
}

function xScale(xDomain: [number, number] | null): uPlot.Scale {
  return { time: true, ...(xDomain ? { range: xDomain } : {}) };
}

const zeroFloorRange = (_u: uPlot, _dataMin: number | null, dataMax: number | null): [number, number] =>
  [0, dataMax != null && dataMax > 0 ? dataMax * 1.1 : 1];

/**
 * Returns a uPlot draw hook that renders "Mon\nJun 25" style labels centered
 * at local noon of each day within the visible x range.
 * Used on strips whose x-axis is hidden ({ show: false }).
 */
function dayLabelHook(tz: string): (u: uPlot) => void {
  return (u: uPlot) => {
    const min = u.scales.x.min;
    const max = u.scales.x.max;
    if (min == null || max == null) return;

    const { ctx } = u;
    const bottom = Math.round(u.bbox.top + u.bbox.height);

    ctx.save();
    ctx.textAlign = "center";

    // Walk each local calendar day from the day containing `min` through `max`,
    // labeling at that day's local noon. Stepping from noon (rather than the
    // 00:00 boundary) ensures the leading/partial day — e.g. today — is labeled
    // even when its midnight falls before the visible range.
    let noonSec = localNoonSec(min * 1000, tz);
    let guard = 0;
    while (noonSec <= max && guard < 40) {
      guard += 1;
      // Advance pointer captured before drawing so `continue` can't loop forever.
      const at = noonSec;
      noonSec = localNoonSec((noonSec + 26 * 3600) * 1000, tz);

      if (at < min) continue;
      const xPx = Math.round(u.valToPos(at, "x", true));
      const date = new Date(at * 1000);

      // Day-of-week: "Mon"
      const weekday = new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        weekday: "short",
      }).format(date);

      // Date: "Jun 25"
      const dateStr = new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        month: "short",
        day: "numeric",
      }).format(date);

      // Weekday line
      ctx.font = `500 10px sans-serif`;
      ctx.fillStyle = CHART_THEME.axisLabel;
      ctx.fillText(weekday, xPx, bottom + 13);

      // Date line
      ctx.font = `400 10px sans-serif`;
      ctx.fillStyle = CHART_THEME.axisLabel;
      ctx.fillText(dateStr, xPx, bottom + 24);
    }

    ctx.restore();
  };
}

function StripFrame({
  label,
  summary,
  first,
  children,
}: {
  label: string;
  summary?: string;
  first?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={first ? "" : "mt-3 border-t border-white/[0.06] pt-3"}>
      <div className="mb-1 text-[12px] text-white/70">{label}</div>
      {summary && <div className="mb-1 text-[11px] text-white/45">{summary}</div>}
      {children}
    </div>
  );
}

function StripEmpty({ height, text }: { height: number; text: string }) {
  return (
    <div
      className="flex w-full items-center justify-center text-[12px] text-white/35"
      style={{ height }}
    >
      {text}
    </div>
  );
}

/**
 * Single-model compact meteogram card. All data comes from the existing
 * meteogram response — no extra fetch. The temperature, precipitation and wind
 * strips share one x time-domain so day boundaries align vertically.
 */
export function SingleModelDetailCard({
  response,
  model,
  timezone,
  locationText = "",
  isLoading,
  error,
  onRetry,
  nowMs,
}: Props) {
  const tz = timezone || "UTC";
  const nowSec = useMemo(() => Math.floor((nowMs ?? Date.now()) / 1000), [nowMs]);

  const series = model ? response?.series?.[model] : undefined;
  const title = model ? modelShortName(model) : "Model detail";
  const runSubtitle = formatRunInit(series);

  const tempUnit = unitsLabel(series?.variables?.tmp2m?.units);
  const windUnit = series?.variables?.wspd10m?.units || "mph";
  const precipUnit = series?.variables?.precip_total?.units || "in";

  // ── Derived strip data ─────────────────────────────────────────────────────
  const temp = useMemo(() => {
    if (!model) return { xs: [] as number[], data: [[], [], []] as unknown as uPlot.AlignedData, hasData: false };
    const rows = dailyHighLow(response, model, tz);
    const xs = rows.map((r) => r.x);
    const high = rows.map((r) => r.high);
    const low = rows.map((r) => r.low);
    return { xs, data: [xs, high, low] as unknown as uPlot.AlignedData, hasData: xs.length > 0 };
  }, [response, model, tz]);

  const precip = useMemo(
    () => (model ? sixHourSteps(seriesPoints(response, model, "precip_total")) : { xs: [], step: [], cumul: [] }),
    [response, model],
  );

  const wind = useMemo(() => {
    if (!model) return { xs: [] as number[], data: [[], []] as unknown as uPlot.AlignedData, hasData: false };
    const { xs, vals } = nativeSeries(response, model, "wspd10m");
    return { xs, data: [xs, vals] as unknown as uPlot.AlignedData, hasData: xs.length > 0 };
  }, [response, model]);

  // ── Per-strip summary stat lines (derived from the data above) ──────────────
  const tempSummary = useMemo(() => {
    const highs = (temp.data[1] as (number | null)[]).filter((v): v is number => v != null);
    const lows = (temp.data[2] as (number | null)[]).filter((v): v is number => v != null);
    if (!highs.length) return undefined;
    const peakHigh = Math.round(Math.max(...highs));
    const minLow = Math.round(Math.min(...lows));
    const avgHigh = Math.round(highs.reduce((a, b) => a + b, 0) / highs.length);
    return `Peak: ${peakHigh}${tempUnit} · Low: ${minLow}${tempUnit} · Avg high: ${avgHigh}${tempUnit}`;
  }, [temp.data, tempUnit]);

  const precipBarsSummary = useMemo(() => {
    const steps = precip.step.filter((v): v is number => v != null && v > 0);
    if (!steps.length) return undefined;
    const max6hr = Math.max(...steps);
    return `Largest 6-hr: ${max6hr.toFixed(2)} ${precipUnit}`;
  }, [precip.step, precipUnit]);

  const cumulSummary = useMemo(() => {
    const vals = precip.cumul.filter((v): v is number => v != null);
    if (!vals.length) return undefined;
    const total = Math.max(...vals);
    return `Total: ${total.toFixed(2)} ${precipUnit}`;
  }, [precip.cumul, precipUnit]);

  const windSummary = useMemo(() => {
    const vals = (wind.data[1] as (number | null)[]).filter((v): v is number => v != null);
    if (!vals.length) return undefined;
    const peak = Math.round(Math.max(...vals));
    const avg = Math.round(vals.reduce((a, b) => a + b, 0) / vals.length);
    return `Peak: ${peak} ${windUnit} · Avg: ${avg} ${windUnit}`;
  }, [wind.data, windUnit]);

  // Shared time domain across every strip (union of all strips' timestamps).
  const xDomain = useMemo<[number, number] | null>(() => {
    let min = Infinity;
    let max = -Infinity;
    for (const arr of [temp.xs, precip.xs, wind.xs]) {
      for (const ts of arr) {
        if (ts < min) min = ts;
        if (ts > max) max = ts;
      }
    }
    if (!(Number.isFinite(min) && Number.isFinite(max) && max > min)) return null;
    // The temp strip draws fixed-width bars centered on each day's local noon, so
    // an edge bar spills into the y-axis gutter (left) or past the plot (right)
    // unless the domain extends a half-day beyond the first and last bars. Without
    // the left pad, a run whose precip/wind strips are empty collapses the shared
    // domain onto the temp bars, putting the first bar's center on the left edge.
    // (The right edge also drops the daily strip's trailing partial day.)
    if (temp.xs.length > 0) {
      const firstDayStart = temp.xs[0]! - 12 * 3600;
      if (firstDayStart < min) min = firstDayStart;
      const lastDayEnd = temp.xs[temp.xs.length - 1]! + 12 * 3600;
      if (lastDayEnd > max) max = lastDayEnd;
    }
    return [min, max];
  }, [temp.xs, precip.xs, wind.xs]);

  const lineWidth = 1.75; // fixed weight in detail mode — not model-dependent

  // ── Per-strip uPlot options ────────────────────────────────────────────────
  const tempOptions = useMemo<Omit<uPlot.Options, "width">>(
    () => ({
      ...stripBase(tz, nowSec),
      // Override stripBase bottom padding to reserve room for the day labels.
      padding: [30, 12, 32, null],
      height: TEMP_STRIP_HEIGHT,
      scales: {
        x: xScale(xDomain),
        // Fixed 0–120°F range so gridlines are evenly spaced every 10° and the
        // data always floats above the bottom edge — keeping the day labels
        // (drawn in the bottom padding) clear of the lowest bars and gridline.
        y: { range: (): [number, number] => [0, 120] },
      },
      // Only the x placeholder; floating bars are drawn in hooks.draw.
      series: [{}],
      axes: [
        { show: false },
        {
          ...valueYAxis((v) => `${Math.round(v)}${tempUnit}`, { size: STRIP_Y_SIZE }),
          splits: (_u: uPlot) => {
            // Even 10° axis labels across the fixed 0–120° range.
            return [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120];
          },
          // No horizontal gridlines crossing the plot, but keep short tick marks
          // at each temperature label. Vertical day-boundary lines (drawn by
          // dayBoundaryPlugin) still separate the days.
          grid: { show: false },
          ticks: { stroke: CHART_THEME.gridline, width: 1, size: 5 },
        },
      ],
      hooks: {
        draw: [
          (u: uPlot) => {
            const ctx = u.ctx;
            const xArr = u.data[0] as number[];
            const highArr = u.data[1] as (number | null)[];
            const lowArr = u.data[2] as (number | null)[];
            const barWidthPx = Math.max(4, (u.bbox.width / xArr.length) * 0.72);
            const isMobileChart = window.matchMedia("(max-width: 1023px)").matches;
            const valueLabelFontSize = isMobileChart ? 20 : 15;
            const valueLabelFontWeight = isMobileChart ? 700 : 600;
            const y0 = Math.round(u.valToPos(0, "y", true)); // baseline at 0°F
            for (let i = 0; i < xArr.length; i++) {
              const high = highArr[i];
              const low = lowArr[i];
              if (high == null || low == null) continue;
              const cx = Math.round(u.valToPos(xArr[i], "x", true));
              const yHigh = Math.round(u.valToPos(high, "y", true));
              const yLow = Math.round(u.valToPos(low, "y", true));
              const x = cx - barWidthPx / 2;

              // Blue base — grows up from 0°F to the daily low
              ctx.fillStyle = `${DETAIL_COLORS.tempLow}B3`; // 70% opacity
              ctx.fillRect(x, yLow, barWidthPx, y0 - yLow);

              // Red cap — from the daily low up to the daily high
              ctx.fillStyle = `${DETAIL_COLORS.tempHigh}B3`; // 70% opacity
              ctx.fillRect(x, yHigh, barWidthPx, yLow - yHigh);

              // Outer stroke around the full 0→high bar
              ctx.strokeStyle = `${DETAIL_COLORS.tempStroke}4D`; // 30% opacity
              ctx.lineWidth = 1;
              ctx.strokeRect(x, yHigh, barWidthPx, y0 - yHigh);

              // High label above the bar
              ctx.font = `${valueLabelFontWeight} ${valueLabelFontSize}px ui-sans-serif, system-ui, sans-serif`;
              ctx.textAlign = "center";
              ctx.fillStyle = DETAIL_COLORS.tempHigh;
              ctx.fillText(`${Math.round(high)}°`, cx, yHigh - (isMobileChart ? 8 : 6));

              // Low label inside the blue base, white for contrast. Clamped so a
              // very low value never pushes the label below the baseline.
              ctx.fillStyle = "#FFFFFF";
              ctx.fillText(
                `${Math.round(low)}°`,
                cx,
                Math.min(yLow + valueLabelFontSize + 3, y0 - 5),
              );
            }
          },
          dayLabelHook(tz),
        ],
      },
    }),
    [tz, nowSec, xDomain, tempUnit],
  );

  const precipBarsOptions = useMemo<Omit<uPlot.Options, "width">>(
    () => ({
      ...stripBase(tz, nowSec),
      // Override stripBase bottom padding to reserve room for the day labels.
      padding: [null, 12, 32, null],
      height: PRECIP_STRIP_HEIGHT,
      scales: { x: xScale(xDomain), y: { range: zeroFloorRange } },
      series: [
        {},
        {
          label: "6-hr",
          stroke: DETAIL_COLORS.precipStroke,
          fill: `${DETAIL_COLORS.precipBar}66`,
          paths: uPlot.paths.bars!({ size: [0.6, 28] }),
          points: { show: false },
          value: (_u: uPlot, v: number | null) => (v == null ? "—" : `${v.toFixed(2)} ${precipUnit}`),
        },
      ],
      axes: [
        { show: false },
        valueYAxis((v) => `${v.toFixed(2)} ${precipUnit}`, { size: STRIP_Y_SIZE, space: 16 }),
      ],
      hooks: { draw: [dayLabelHook(tz)] },
    }),
    [tz, nowSec, xDomain, precipUnit],
  );

  const cumulOptions = useMemo<Omit<uPlot.Options, "width">>(
    () => ({
      ...stripBase(tz, nowSec),
      // Override stripBase bottom padding to reserve room for the day labels.
      padding: [null, 12, 32, null],
      height: CUMUL_STRIP_HEIGHT,
      scales: { x: xScale(xDomain), y: { range: zeroFloorRange } },
      series: [
        {},
        {
          label: "Cumulative",
          stroke: DETAIL_COLORS.precipCumul,
          width: lineWidth,
          spanGaps: true,
          points: { show: false },
          value: (_u: uPlot, v: number | null) => (v == null ? "—" : `${v.toFixed(2)} ${precipUnit}`),
        },
      ],
      axes: [
        { show: false },
        valueYAxis((v) => `${v.toFixed(2)} ${precipUnit}`, { size: STRIP_Y_SIZE, space: 16 }),
      ],
      hooks: { draw: [dayLabelHook(tz)] },
    }),
    [tz, nowSec, xDomain, lineWidth, precipUnit],
  );

  const windOptions = useMemo<Omit<uPlot.Options, "width">>(
    () => ({
      ...stripBase(tz, nowSec),
      height: WIND_STRIP_HEIGHT,
      scales: { x: xScale(xDomain), y: { range: zeroFloorRange } },
      series: [
        {},
        {
          label: modelShortName(model ?? ""),
          stroke: DETAIL_COLORS.wind,
          width: lineWidth,
          spanGaps: true,
          points: { show: false },
          value: (_u: uPlot, v: number | null) => (v == null ? "—" : `${Math.round(v)} ${windUnit}`),
        },
      ],
      axes: [
        timeXAxis(tz),
        valueYAxis((v) => `${Math.round(v)} ${windUnit}`, { size: STRIP_Y_SIZE, space: 16 }),
      ],
    }),
    [tz, nowSec, xDomain, lineWidth, windUnit, model],
  );

  // ── Card content / states ──────────────────────────────────────────────────
  const hasAnyData = temp.hasData || precip.xs.length > 0 || wind.hasData;

  let content: ReactNode;
  if (!model) {
    content = <StripEmpty height={200} text="No guidance available for this location." />;
  } else if (series?.status === "not_entitled") {
    content = (
      <StripEmpty height={200} text={`${modelShortName(model)} guidance requires an upgraded plan.`} />
    );
  } else if (series?.status === "unavailable") {
    content = (
      <StripEmpty height={200} text={`${modelShortName(model)} guidance is currently unavailable.`} />
    );
  } else if (!hasAnyData) {
    content = (
      <StripEmpty height={200} text={`No guidance available for ${modelShortName(model)} at this location.`} />
    );
  } else {
    content = (
      <div>
        <p className="mb-2 text-[11px] text-white/45 lg:hidden">
          Swipe horizontally to read each day.
        </p>
        <div
          data-model-detail-charts
          role="region"
          aria-label={`${title} daily model detail charts`}
          tabIndex={0}
          className="overflow-x-auto overscroll-x-contain rounded-sm focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-300/60"
        >
          <div className="flex min-w-[1000px] flex-col lg:min-w-0">
            <StripFrame label="Daily high / low" summary={tempSummary} first>
              {temp.hasData ? (
                <UplotChart options={tempOptions} data={temp.data} className="w-full" />
              ) : (
                <StripEmpty height={TEMP_STRIP_HEIGHT} text="No temperature data" />
              )}
            </StripFrame>
            <StripFrame label="6-hour precipitation" summary={precipBarsSummary}>
              {precip.xs.length > 0 ? (
                <UplotChart
                  options={precipBarsOptions}
                  data={[precip.xs, precip.step] as unknown as uPlot.AlignedData}
                  className="w-full"
                />
              ) : (
                <StripEmpty height={PRECIP_STRIP_HEIGHT} text="No precipitation data" />
              )}
            </StripFrame>
            <StripFrame label="Cumulative precipitation" summary={cumulSummary}>
              {precip.xs.length > 0 ? (
                <UplotChart
                  options={cumulOptions}
                  data={[precip.xs, precip.cumul] as unknown as uPlot.AlignedData}
                  className="w-full"
                />
              ) : (
                <StripEmpty height={CUMUL_STRIP_HEIGHT} text="No precipitation data" />
              )}
            </StripFrame>
            <StripFrame label="Wind speed" summary={windSummary}>
              {wind.hasData ? (
                <UplotChart options={windOptions} data={wind.data} className="w-full" />
              ) : (
                <StripEmpty height={WIND_STRIP_HEIGHT} text="No wind data" />
              )}
            </StripFrame>
          </div>
        </div>
      </div>
    );
  }

  return (
    <ChartContainer
      title={title}
      subtitle={runSubtitle}
      isLoading={isLoading}
      error={error}
      onRetry={onRetry}
      exportImage={
        model
          ? {
              headerText: runSubtitle ? `${title} · ${runSubtitle}` : title,
              locationText,
              filenameSlug: model,
            }
          : undefined
      }
    >
      {content}
    </ChartContainer>
  );
}
