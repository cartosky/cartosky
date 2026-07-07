import { useMemo } from "react";
import uPlot from "uplot";

import { UplotChart } from "@/components/charts/UplotChart";
import {
  dayBoundaryPlugin,
  nowMarkerPlugin,
  timeXAxis,
  toTimestampSec,
  valueYAxis,
} from "@/components/charts/chart-helpers";
import {
  ensembleControlStroke,
  ensembleMeanStroke,
  ensembleMeanWidth,
  ensembleMemberStroke,
  modelShortName,
} from "@/lib/chart-constants";
import type {
  MeteogramMemberSeries,
  MeteogramPoint,
  MeteogramResponse,
} from "@/lib/meteogram-types";

const CHART_HEIGHT = 320;
const MEMBER_LINE_WIDTH = 1;
const CONTROL_LINE_WIDTH = 1.5;

type Props = {
  response: MeteogramResponse | null;
  /** Single model whose member plume is drawn (e.g. "gefs"). */
  model: string;
  variable: string;
  unitsFallback: string;
  formatValue: (value: number, units: string) => string;
  timezone: string | null;
  emptyMessage: string;
  nowMs?: number;
  /** Lower-bound the y-range at 0 (cumulative precip never goes negative). */
  clampZero?: boolean;
};

type PlumeSeries = {
  key: string; // "m01".."mNN" | "control" | "mean"
  points: MeteogramPoint[];
};

/** Perturbation members first, then control, then mean — draw order puts the
 * mean on top, control above the member cloud. */
function orderedPlumeSeries(
  members: Record<string, MeteogramMemberSeries>,
): PlumeSeries[] {
  const perturbation: PlumeSeries[] = [];
  let control: PlumeSeries | null = null;
  let mean: PlumeSeries | null = null;
  for (const [key, entry] of Object.entries(members)) {
    if (!entry.points || entry.points.length === 0) continue;
    const series = { key, points: entry.points };
    if (key === "mean") mean = series;
    else if (key === "control") control = series;
    else perturbation.push(series);
  }
  perturbation.sort((a, b) => a.key.localeCompare(b.key));
  return [...perturbation, ...(control ? [control] : []), ...(mean ? [mean] : [])];
}

function alignPlumeData(seriesList: PlumeSeries[]): {
  data: uPlot.AlignedData;
  hasData: boolean;
} {
  const xsSet = new Set<number>();
  for (const series of seriesList) {
    for (const point of series.points) {
      if (!point.valid_time) continue;
      const ts = toTimestampSec(point.valid_time);
      if (ts != null) xsSet.add(ts);
    }
  }
  const xs = [...xsSet].sort((a, b) => a - b);
  const indexByTs = new Map(xs.map((ts, idx) => [ts, idx]));
  const arrays: (number | null)[][] = seriesList.map((series) => {
    const arr: (number | null)[] = new Array(xs.length).fill(null);
    for (const point of series.points) {
      if (!point.valid_time) continue;
      const ts = toTimestampSec(point.valid_time);
      if (ts == null) continue;
      const idx = indexByTs.get(ts);
      if (idx != null) arr[idx] = point.value;
    }
    return arr;
  });
  return {
    data: [xs, ...arrays] as unknown as uPlot.AlignedData,
    hasData: xs.length > 0 && seriesList.length > 0,
  };
}

/**
 * Single-model ensemble member spaghetti (Model Guidance §7): thin member
 * lines, bold mean, dashed white control. Data comes from the meteogram's
 * `include_members` payload (member pipeline Phase 5) — the mean entry
 * mirrors the variable's main series.
 *
 * The per-series point counts (~65) are far below the spec's 2000-point
 * downsampling threshold, so no downsampling is applied. The busy 30-line
 * cloud makes a 32-row hover tooltip unreadable — the crosshair + day
 * boundaries + now marker are kept, per-series tooltips are not.
 */
export function EnsemblePlumeChart({
  response,
  model,
  variable,
  unitsFallback,
  formatValue,
  timezone,
  emptyMessage,
  nowMs,
  clampZero = false,
}: Props) {
  const variableEntry = response?.series?.[model]?.variables?.[variable];
  const members = variableEntry?.members;
  const units =
    typeof variableEntry?.units === "string" && variableEntry.units.trim()
      ? variableEntry.units
      : unitsFallback;

  const seriesList = useMemo(
    () => (members ? orderedPlumeSeries(members) : []),
    [members],
  );
  const { data, hasData } = useMemo(() => alignPlumeData(seriesList), [seriesList]);

  const nowSec = useMemo(() => Math.floor((nowMs ?? Date.now()) / 1000), [nowMs]);

  const options = useMemo<Omit<uPlot.Options, "width">>(() => {
    const tz = timezone || "UTC";
    const tzDate = (ts: number) => uPlot.tzDate(new Date(ts * 1000), tz);

    return {
      height: CHART_HEIGHT,
      tzDate,
      cursor: { y: false },
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
        ...seriesList.map((series) => {
          if (series.key === "mean") {
            return {
              label: `${modelShortName(model)} mean`,
              stroke: ensembleMeanStroke(model),
              width: ensembleMeanWidth(),
              points: { show: false },
            };
          }
          if (series.key === "control") {
            return {
              label: `${modelShortName(model)} control`,
              stroke: ensembleControlStroke(model),
              width: CONTROL_LINE_WIDTH,
              dash: [6, 6],
              points: { show: false },
            };
          }
          return {
            label: series.key,
            stroke: ensembleMemberStroke(model),
            width: MEMBER_LINE_WIDTH,
            points: { show: false },
          };
        }),
      ],
      axes: [timeXAxis(tz), valueYAxis((v) => formatValue(v, units))],
      plugins: [dayBoundaryPlugin(tz), nowMarkerPlugin(nowSec)],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesList, units, timezone, nowSec, clampZero, formatValue, model]);

  if (!hasData) {
    return (
      <div className="flex h-[320px] w-full items-center justify-center text-center text-[13px] text-white/45">
        {emptyMessage}
      </div>
    );
  }

  return <UplotChart options={options} data={data} className="w-full" />;
}
