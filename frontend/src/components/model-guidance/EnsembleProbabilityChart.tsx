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
  ensembleProbVarId,
  type EnsembleProbThresholdSpec,
} from "@/lib/chart-constants";
import type { MeteogramResponse } from "@/lib/meteogram-types";

const CHART_HEIGHT = 320;
const LINE_WIDTH = 2;

type Props = {
  /** Exceedance (`prob_gt`) response — null when no gt thresholds exist. */
  gtResponse: MeteogramResponse | null;
  /** Non-exceedance (`prob_lt`) response — null when no lt thresholds exist.
   * Separate responses because each direction is its own meteogram request
   * (the request schema caps `variables` at 6; tmp2m has 7 thresholds). */
  ltResponse: MeteogramResponse | null;
  /** Single ensemble model whose probabilities are drawn (e.g. "gefs"). */
  model: string;
  /** Base variable id (e.g. "precip_total"); prob ids derive from it. */
  variable: string;
  /** Configured threshold specs in display order (chart-constants matrix). */
  thresholds: readonly EnsembleProbThresholdSpec[];
  /** Unit suffix for the per-line labels (e.g. `°F` → `< 32°F`). */
  thresholdUnitSuffix: string;
  timezone: string | null;
  emptyMessage: string;
  nowMs?: number;
};

/**
 * Exceedance/non-exceedance probability chart (backlog B1 + B2): one line per
 * configured threshold on a fixed 0–100% axis. Strokes come from the config
 * spec (B2 D-D: cold `< x` rungs in blue shades, warm `> x` rungs
 * yellow→red). Values are the stats grids' probability products sampled by
 * the meteogram (stats design §8) — identical to the probability maps.
 * Thresholds whose product is absent from the served run (e.g. a stats gate
 * skip) are omitted rather than drawn flat.
 */
export function EnsembleProbabilityChart({
  gtResponse,
  ltResponse,
  model,
  variable,
  thresholds,
  thresholdUnitSuffix,
  timezone,
  emptyMessage,
  nowMs,
}: Props) {
  // Both directions are pinned to the same run upstream, but each request
  // can independently fall back when that run can't serve its vars — never
  // mix two runs' probabilities in one chart.
  const gtRun = gtResponse?.series?.[model]?.run_id ?? null;
  const ltRun = ltResponse?.series?.[model]?.run_id ?? null;
  const runsConsistent = !gtRun || !ltRun || gtRun === ltRun;

  const activeThresholds = useMemo(
    () =>
      runsConsistent
        ? thresholds
            .map((spec) => ({
              spec,
              points: seriesPoints(
                spec.direction === "lt" ? ltResponse : gtResponse,
                model,
                ensembleProbVarId(variable, spec.threshold, spec.direction),
              ),
            }))
            .filter((entry) => entry.points != null)
        : [],
    [runsConsistent, gtResponse, ltResponse, model, variable, thresholds],
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
          ({ spec }): uPlot.Series => ({
            label: `${spec.direction === "lt" ? "<" : ">"} ${spec.threshold}${thresholdUnitSuffix}`,
            stroke: spec.stroke,
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
