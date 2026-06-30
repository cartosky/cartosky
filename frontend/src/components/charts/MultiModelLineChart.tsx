import { useMemo } from "react";
import uPlot from "uplot";

import { UplotChart } from "@/components/charts/UplotChart";
import {
  buildAlignedData,
  cursorTooltipPlugin,
  dayBoundaryPlugin,
  nowMarkerPlugin,
  resolveUnits,
  shouldSpanCadenceGap,
  timeXAxis,
  valueYAxis,
} from "@/components/charts/chart-helpers";
import {
  modelLineStroke,
  modelLineWidth,
  modelShortName,
  orderModelsAnchorLast,
} from "@/lib/chart-constants";
import type { MeteogramResponse } from "@/lib/meteogram-types";

const CHART_HEIGHT = 320;

type Props = {
  response: MeteogramResponse | null;
  /** Models considered for this chart, in draw order. */
  models: readonly string[];
  visibleModels: Set<string>;
  variable: string;
  /** Units label appended to tooltip + y-axis ticks (e.g. "in", "mph"). */
  unitsFallback: string;
  /** Value formatter for tooltip + y-axis (receives the resolved units). */
  formatValue: (value: number, units: string) => string;
  timezone: string | null;
  emptyMessage: string;
  nowMs?: number;
  /** Lower-bound the y-range at 0 (cumulative precip never goes negative). */
  clampZero?: boolean;
  /** Per-model stroke override (defaults to the anchor-aware model stroke). */
  strokeFor?: (model: string) => string;
  /** Per-model line width override (defaults to the anchor-aware width). */
  widthFor?: (model: string) => number;
  /** Show per-point markers along each line (defaults to uPlot's behavior). */
  showPoints?: boolean;
};

/**
 * Generic multi-model hourly line chart on a shared union time axis. Used by the
 * cumulative-precip and wind charts. Mirrors the temperature chart's cadence-gap
 * handling so denser models don't break sparser models' lines.
 */
export function MultiModelLineChart({
  response,
  models,
  visibleModels,
  variable,
  unitsFallback,
  formatValue,
  timezone,
  emptyMessage,
  nowMs,
  clampZero = false,
  strokeFor = modelLineStroke,
  widthFor = modelLineWidth,
  showPoints,
}: Props) {
  const activeModels = useMemo(
    () =>
      // Anchor model last so it renders on top of the secondary models.
      orderModelsAnchorLast(
        models.filter(
          (model) =>
            visibleModels.has(model) &&
            (response?.series?.[model]?.variables?.[variable]?.points?.length ?? 0) > 0 &&
            (response?.series?.[model]?.status === "ok" ||
              response?.series?.[model]?.status === "partial"),
        ),
      ),
    [models, response, variable, visibleModels],
  );

  const units = useMemo(
    () => resolveUnits(response, activeModels, variable, unitsFallback),
    [response, activeModels, variable, unitsFallback],
  );

  const { data, hasData, nativeTimestampsByModel } = useMemo(
    () => buildAlignedData(response, activeModels, variable),
    [response, activeModels, variable],
  );

  // Stable across re-renders so chart options aren't rebuilt (recreating the
  // uPlot instance) on unrelated re-renders such as scroll-spy.
  const nowSec = useMemo(() => Math.floor((nowMs ?? Date.now()) / 1000), [nowMs]);

  const options = useMemo<Omit<uPlot.Options, "width">>(() => {
    const tz = timezone || "UTC";
    const tzDate = (ts: number) => uPlot.tzDate(new Date(ts * 1000), tz);

    return {
      height: CHART_HEIGHT,
      tzDate,
      // Vertical crosshair only (no horizontal). Hovering dims the other series
      // so the focused/active model reads on top.
      cursor: { y: false, focus: { prox: 24 } },
      focus: { alpha: 0.3 },
      legend: { show: false },
      scales: {
        x: { time: true },
        y: {
          auto: true,
          range: (_u, dataMin, dataMax): [number, number] => {
            if (dataMin == null || dataMax == null) return [0, 1];
            const span = dataMax - dataMin;
            const pad = span > 0 ? span * 0.05 : Math.max(Math.abs(dataMax) * 0.05, 0.1);
            return [clampZero ? 0 : dataMin - pad, dataMax + pad];
          },
        },
      },
      series: [
        {},
        ...activeModels.map((model, modelIndex) => {
          const nativeTimestamps = nativeTimestampsByModel.get(model) ?? new Set<number>();
          return {
            label: modelShortName(model),
            stroke: strokeFor(model),
            width: widthFor(model),
            spanGaps: (u: uPlot, seriesIdx: number, idx0: number, idx1: number) => {
              if (seriesIdx - 1 !== modelIndex) return false;
              const xs = u.data[0] as number[];
              return shouldSpanCadenceGap(xs, nativeTimestamps, idx0, idx1);
            },
            ...(showPoints === false ? { points: { show: false } } : {}),
            value: (_u: uPlot, v: number | null) =>
              v == null ? "—" : formatValue(v, units),
          };
        }),
      ],
      axes: [timeXAxis(tz), valueYAxis((v) => formatValue(v, units))],
      plugins: [dayBoundaryPlugin(tz), nowMarkerPlugin(nowSec), cursorTooltipPlugin(tz)],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeModels, units, timezone, nowSec, nativeTimestampsByModel, clampZero, formatValue, strokeFor, widthFor, showPoints]);

  if (!hasData) {
    return (
      <div className="flex h-[320px] w-full items-center justify-center text-center text-[13px] text-white/45">
        {emptyMessage}
      </div>
    );
  }

  return <UplotChart options={options} data={data} className="w-full" />;
}
