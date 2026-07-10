import { useMemo } from "react";
import uPlot from "uplot";

import { UplotChart } from "@/components/charts/UplotChart";
import {
  alignPointSeries,
  cursorTooltipPlugin,
  dayBoundaryPlugin,
  nowMarkerPlugin,
  seriesPoints,
  timeXAxis,
  valueYAxis,
} from "@/components/charts/chart-helpers";
import {
  ENSEMBLE_STATS_PERCENTILES,
  PLUME_MEAN_STROKE,
  ensemblePercentileBandFill,
  ensemblePercentileEdgeStroke,
  ensemblePercentileVarId,
} from "@/lib/chart-constants";
import type { MeteogramResponse } from "@/lib/meteogram-types";

const CHART_HEIGHT = 320;
const EDGE_LINE_WIDTH = 1;
const MEDIAN_LINE_WIDTH = 3;
const MEAN_LINE_WIDTH = 2;

type Props = {
  /** Stats meteogram response: base variable + `{base}__pNN` series. */
  response: MeteogramResponse | null;
  /** Single ensemble model whose stats are drawn (e.g. "gefs"). */
  model: string;
  /** Base variable id (e.g. "precip_total"); percentile ids derive from it. */
  variable: string;
  unitsFallback: string;
  formatValue: (value: number, units: string) => string;
  timezone: string | null;
  emptyMessage: string;
  nowMs?: number;
  /** Lower-bound the y-range at 0 (cumulative precip never goes negative). */
  clampZero?: boolean;
};

/**
 * Ensemble percentile band chart (backlog B1): shaded 10–90th percentile band
 * with a darker 25–75th band inside it, a bold median (P50) line, and the
 * ensemble mean dashed on top. Data comes from the stats grids sampled by the
 * meteogram as ordinary variables (stats design §8) — the values match the
 * percentile maps exactly. The base variable's own series IS the ensemble
 * mean, so no member payload is needed.
 */
export function EnsemblePercentileBandChart({
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
  // Descending so the outer band's upper edge (p90) is series 1 and its lower
  // edge (p10) is series 5 — the bands option references these positions.
  const percentilesDesc = useMemo(
    () => [...ENSEMBLE_STATS_PERCENTILES].sort((a, b) => b - a),
    [],
  );

  const percentilePoints = useMemo(
    () =>
      percentilesDesc.map((q) =>
        seriesPoints(response, model, ensemblePercentileVarId(variable, q)),
      ),
    [response, model, variable, percentilesDesc],
  );
  const meanPoints = useMemo(
    () => seriesPoints(response, model, variable),
    [response, model, variable],
  );

  // Bands need every edge: a run whose stats are partially published would
  // draw a misleading half-band, so require the full percentile set.
  const percentilesComplete = percentilePoints.every((points) => points != null);

  const { data, hasData } = useMemo(
    () => alignPointSeries([...percentilePoints, meanPoints]),
    [percentilePoints, meanPoints],
  );

  const units =
    (percentilesComplete
      ? response?.series?.[model]?.variables?.[
          ensemblePercentileVarId(variable, 50)
        ]?.units
      : null) || unitsFallback;

  const nowSec = useMemo(() => Math.floor((nowMs ?? Date.now()) / 1000), [nowMs]);

  const options = useMemo<Omit<uPlot.Options, "width">>(() => {
    const tz = timezone || "UTC";
    const tzDate = (ts: number) => uPlot.tzDate(new Date(ts * 1000), tz);
    const value = (_u: uPlot, v: number | null) =>
      v == null ? "—" : formatValue(v, units);

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
        ...percentilesDesc.map((q): uPlot.Series => {
          if (q === 50) {
            return {
              label: "Median (P50)",
              stroke: PLUME_MEAN_STROKE,
              width: MEDIAN_LINE_WIDTH,
              points: { show: false },
              value,
            };
          }
          return {
            label: `${q}th percentile`,
            stroke: ensemblePercentileEdgeStroke(model),
            width: EDGE_LINE_WIDTH,
            points: { show: false },
            value,
          };
        }),
        {
          label: "Mean",
          stroke: PLUME_MEAN_STROKE,
          width: MEAN_LINE_WIDTH,
          dash: [6, 6],
          points: { show: false },
          value,
        },
      ],
      // Series order is [x, p90, p75, p50, p25, p10, mean]: the outer band
      // fills p90→p10, the inner band overdraws p75→p25 darker.
      bands: [
        { series: [1, 5], fill: ensemblePercentileBandFill(model, "outer") },
        { series: [2, 4], fill: ensemblePercentileBandFill(model, "inner") },
      ],
      axes: [timeXAxis(tz), valueYAxis((v) => formatValue(v, units))],
      plugins: [dayBoundaryPlugin(tz), nowMarkerPlugin(nowSec), cursorTooltipPlugin(tz)],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [percentilesDesc, units, timezone, nowSec, clampZero, formatValue, model]);

  if (!hasData || !percentilesComplete) {
    return (
      <div className="flex h-[320px] w-full items-center justify-center text-center text-[13px] text-white/45">
        {emptyMessage}
      </div>
    );
  }

  return <UplotChart options={options} data={data} className="w-full" />;
}
