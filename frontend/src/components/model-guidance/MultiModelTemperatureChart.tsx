import { useMemo, useState } from "react";
import uPlot from "uplot";

import { UplotChart } from "@/components/charts/UplotChart";
import {
  buildAlignedData,
  cursorTooltipPlugin,
  dailyHighLow,
  dayBoundaryPlugin,
  nowMarkerPlugin,
  seriesPoints,
  shouldSpanCadenceGap,
  timeXAxis,
  valueYAxis,
} from "@/components/charts/chart-helpers";
import {
  TEMPERATURE_GUIDANCE_MODELS,
  modelLineStroke,
  modelLineWidth,
  modelShortName,
  orderModelsAnchorLast,
} from "@/lib/chart-constants";
import type { MeteogramResponse } from "@/lib/meteogram-types";

const CHART_HEIGHT = 320;

type ViewMode = "hourly" | "daily";

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

export function MultiModelTemperatureChart({ response, visibleModels, timezone, nowMs }: Props) {
  const [viewMode, setViewMode] = useState<ViewMode>("hourly");

  const activeModels = useMemo(
    () =>
      // Anchor model last so it renders on top of the secondary models.
      orderModelsAnchorLast(
        TEMPERATURE_GUIDANCE_MODELS.filter(
          (model) => visibleModels.has(model) && seriesPoints(response, model, "tmp2m"),
        ),
      ),
    [response, visibleModels],
  );

  const units = useMemo(() => {
    for (const model of activeModels) {
      const u = response?.series?.[model]?.variables?.tmp2m?.units;
      if (u) return u;
    }
    return "F";
  }, [response, activeModels]);

  const tz = timezone || "UTC";
  // Stable across re-renders so chart options aren't rebuilt (which destroys and
  // recreates the uPlot instance) on unrelated re-renders such as scroll-spy.
  const nowSec = useMemo(() => Math.floor((nowMs ?? Date.now()) / 1000), [nowMs]);

  // ── Hourly view data (union x-axis, per-model native cadence) ──────────────
  const hourly = useMemo(
    () => buildAlignedData(response, activeModels, "tmp2m"),
    [response, activeModels],
  );

  // ── Daily view data (two series per model: high, low) ──────────────────────
  const daily = useMemo(() => {
    const perModel = activeModels.map((model) => dailyHighLow(response, model, tz));
    const xsSet = new Set<number>();
    for (const series of perModel) for (const d of series) xsSet.add(d.x);
    const xs = [...xsSet].sort((a, b) => a - b);
    const indexByX = new Map(xs.map((x, idx) => [x, idx]));
    const arrays: (number | null)[][] = [];
    for (const series of perModel) {
      const high: (number | null)[] = new Array(xs.length).fill(null);
      const low: (number | null)[] = new Array(xs.length).fill(null);
      for (const d of series) {
        const idx = indexByX.get(d.x);
        if (idx == null) continue;
        high[idx] = d.high;
        low[idx] = d.low;
      }
      arrays.push(high, low);
    }
    const data = [xs, ...arrays] as unknown as uPlot.AlignedData;
    return { data, hasData: xs.length > 0 };
  }, [response, activeModels, tz]);

  const hourlyOptions = useMemo<Omit<uPlot.Options, "width">>(() => {
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
          range: (_u, dataMin, dataMax) => {
            if (dataMin == null || dataMax == null) return [0, 1];
            const pad = Math.max(1, (dataMax - dataMin) * 0.05);
            return [dataMin - pad, dataMax + pad];
          },
        },
      },
      series: [
        {},
        ...activeModels.map((model, modelIndex) => {
          const nativeTimestamps =
            hourly.nativeTimestampsByModel.get(model) ?? new Set<number>();
          return {
            label: modelShortName(model),
            stroke: modelLineStroke(model),
            width: modelLineWidth(model),
            spanGaps: (u: uPlot, seriesIdx: number, idx0: number, idx1: number) => {
              if (seriesIdx - 1 !== modelIndex) return false;
              const xs = u.data[0] as number[];
              return shouldSpanCadenceGap(xs, nativeTimestamps, idx0, idx1);
            },
            points: { show: false },
            value: (_u: uPlot, v: number | null) =>
              v == null ? "—" : `${v} ${unitsLabel(units)}`,
          };
        }),
      ],
      axes: [timeXAxis(tz), valueYAxis((v) => `${Math.round(v)}${unitsLabel(units)}`)],
      plugins: [dayBoundaryPlugin(tz), nowMarkerPlugin(nowSec), cursorTooltipPlugin(tz)],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeModels, units, tz, nowSec, hourly.nativeTimestampsByModel]);

  const dailyOptions = useMemo<Omit<uPlot.Options, "width">>(() => {
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
          range: (_u, dataMin, dataMax) => {
            if (dataMin == null || dataMax == null) return [0, 1];
            const pad = Math.max(1, (dataMax - dataMin) * 0.05);
            return [dataMin - pad, dataMax + pad];
          },
        },
      },
      series: [
        {},
        ...activeModels.flatMap((model) => {
          const color = modelLineStroke(model);
          const width = modelLineWidth(model);
          // High and low are two solid model-colored lines (high above low);
          // the floating tooltip labels each. No markers.
          const high = {
            label: `${modelShortName(model)} High`,
            stroke: color,
            width,
            spanGaps: true,
            points: { show: false },
            value: (_u: uPlot, v: number | null) =>
              v == null ? "—" : `${v} ${unitsLabel(units)}`,
          };
          const low = {
            label: `${modelShortName(model)} Low`,
            stroke: color,
            width,
            spanGaps: true,
            points: { show: false },
            value: (_u: uPlot, v: number | null) =>
              v == null ? "—" : `${v} ${unitsLabel(units)}`,
          };
          return [high, low];
        }),
      ],
      axes: [timeXAxis(tz), valueYAxis((v) => `${Math.round(v)}${unitsLabel(units)}`)],
      plugins: [dayBoundaryPlugin(tz), nowMarkerPlugin(nowSec), cursorTooltipPlugin(tz)],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeModels, units, tz, nowSec]);

  const isHourly = viewMode === "hourly";
  const hasData = isHourly ? hourly.hasData : daily.hasData;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <div
          role="group"
          aria-label="Temperature view"
          className="inline-flex rounded-lg border border-white/10 p-0.5 text-[12px]"
        >
          {(["hourly", "daily"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setViewMode(mode)}
              aria-pressed={viewMode === mode}
              className={`rounded-md px-2.5 py-1 transition-colors ${
                viewMode === mode
                  ? "bg-white/[0.08] text-white/85"
                  : "text-white/45 hover:text-white/70"
              }`}
            >
              {mode === "hourly" ? "Hourly" : "Daily high/low"}
            </button>
          ))}
        </div>
      </div>

      {!hasData ? (
        <div className="flex h-[320px] w-full items-center justify-center text-center text-[13px] text-white/45">
          No temperature guidance available for this location.
        </div>
      ) : isHourly ? (
        <UplotChart options={hourlyOptions} data={hourly.data} className="w-full" />
      ) : (
        <UplotChart options={dailyOptions} data={daily.data} className="w-full" />
      )}
    </div>
  );
}
