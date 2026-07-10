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
  ensembleProbThresholdStroke,
  ensembleProbVarId,
} from "@/lib/chart-constants";
import type { MeteogramResponse } from "@/lib/meteogram-types";

const CHART_HEIGHT = 320;
const LINE_WIDTH = 2;

type Props = {
  /** Stats meteogram response carrying `{base}__prob_gt_*` series. */
  response: MeteogramResponse | null;
  /** Single ensemble model whose probabilities are drawn (e.g. "gefs"). */
  model: string;
  /** Base variable id (e.g. "precip_total"); prob ids derive from it. */
  variable: string;
  /** Configured thresholds in display units (chart-constants matrix). */
  thresholds: readonly number[];
  /** Unit suffix for the per-line labels (e.g. `"` → `> 0.5"`). */
  thresholdUnitSuffix: string;
  timezone: string | null;
  emptyMessage: string;
  nowMs?: number;
};

/**
 * Exceedance-probability chart (backlog B1): one line per configured
 * threshold on a fixed 0–100% axis, colored cool→hot by threshold severity.
 * Values are the stats grids' probability products sampled by the meteogram
 * (stats design §8) — identical to the probability maps. Thresholds whose
 * product is absent from the served run (e.g. a stats gate skip) are omitted
 * rather than drawn flat, keeping each remaining line's color stable via its
 * position in the CONFIGURED list.
 */
export function EnsembleProbabilityChart({
  response,
  model,
  variable,
  thresholds,
  thresholdUnitSuffix,
  timezone,
  emptyMessage,
  nowMs,
}: Props) {
  const activeThresholds = useMemo(
    () =>
      thresholds
        .map((threshold, configIndex) => ({
          threshold,
          configIndex,
          points: seriesPoints(response, model, ensembleProbVarId(variable, threshold)),
        }))
        .filter((entry) => entry.points != null),
    [response, model, variable, thresholds],
  );

  const { data, hasData } = useMemo(
    () => alignPointSeries(activeThresholds.map((entry) => entry.points)),
    [activeThresholds],
  );

  const nowSec = useMemo(() => Math.floor((nowMs ?? Date.now()) / 1000), [nowMs]);

  const options = useMemo<Omit<uPlot.Options, "width">>(() => {
    const tz = timezone || "UTC";
    const tzDate = (ts: number) => uPlot.tzDate(new Date(ts * 1000), tz);

    return {
      height: CHART_HEIGHT,
      tzDate,
      cursor: { y: false, focus: { prox: 16 } },
      focus: { alpha: 0.3 },
      legend: { show: false },
      scales: {
        x: { time: true },
        // Fixed 0–100%: probabilities are only comparable across charts and
        // hover positions when the axis never rescales to the data.
        y: { auto: false, range: (): [number, number] => [0, 100] },
      },
      series: [
        {},
        ...activeThresholds.map(
          ({ threshold, configIndex }): uPlot.Series => ({
            label: `> ${threshold}${thresholdUnitSuffix}`,
            stroke: ensembleProbThresholdStroke(configIndex),
            width: LINE_WIDTH,
            points: { show: false },
            value: (_u: uPlot, v: number | null) =>
              v == null ? "—" : `${Math.round(v)}%`,
          }),
        ),
      ],
      axes: [timeXAxis(tz), valueYAxis((v) => `${Math.round(v)}%`)],
      plugins: [dayBoundaryPlugin(tz), nowMarkerPlugin(nowSec), cursorTooltipPlugin(tz)],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeThresholds, thresholdUnitSuffix, timezone, nowSec]);

  if (!hasData || activeThresholds.length === 0) {
    return (
      <div className="flex h-[320px] w-full items-center justify-center text-center text-[13px] text-white/45">
        {emptyMessage}
      </div>
    );
  }

  return <UplotChart options={options} data={data} className="w-full" />;
}
