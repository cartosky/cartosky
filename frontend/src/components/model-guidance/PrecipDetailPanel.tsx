import { useMemo } from "react";
import uPlot from "uplot";
import { ChevronDown } from "lucide-react";

import { UplotChart } from "@/components/charts/UplotChart";
import {
  cursorTooltipPlugin,
  dayBoundaryPlugin,
  nowMarkerPlugin,
  seriesPoints,
  timeXAxis,
  toTimestampSec,
  valueYAxis,
} from "@/components/charts/chart-helpers";
import { PRECIP_GUIDANCE_MODELS, modelColor, modelShortName } from "@/lib/chart-constants";
import type { MeteogramPoint, MeteogramResponse } from "@/lib/meteogram-types";

const SUBCHART_HEIGHT = 160;

type PanelProps = {
  response: MeteogramResponse | null;
  visibleModels: Set<string>;
  timezone: string | null;
  expanded: boolean;
  onToggle: () => void;
  nowMs?: number;
};

export type SixHourSteps = {
  xs: number[];
  step: (number | null)[];
  cumul: (number | null)[];
};

/**
 * Non-overlapping 6-hour precip buckets + the cumulative curve, from a model's
 * cumulative `precip_total` points. Pure + exported so it can be unit-tested.
 *
 * Bucket selection walks the model's native cadence in +6 h steps so bars never
 * overlap (no rolling 6 h window at every native fh for 3-hourly / hourly models):
 *  - First bucket endpoint: the first available fh ≥ 6. Its prior cumulative is
 *    the `fh − 6` value if that point exists, else 0 (model starts after init) —
 *    first bucket only.
 *  - Later buckets: the endpoint is exactly `previous endpoint + 6` and requires
 *    the previous selected endpoint's cumulative value.
 *  - Off-cycle cadence (e.g. 39/45/51): when `previous + 6` is absent, re-anchor
 *    to the next fh whose `fh − 6` point exists and does not overlap the prior
 *    bucket, then resume +6 stepping — preserving the native cadence once shifted.
 *  - No interpolation; differences clamped to ≥ 0.
 *
 * The cumulative overlay is emitted at every native fh (smooth line); bar values
 * are null at any fh that is not a selected bucket endpoint.
 */
export function sixHourSteps(points: MeteogramPoint[] | null): SixHourSteps {
  const xs: number[] = [];
  const step: (number | null)[] = [];
  const cumul: (number | null)[] = [];
  if (!points || points.length === 0) return { xs, step, cumul };

  const byFh = new Map<number, { value: number | null; ts: number | null }>();
  for (const point of points) {
    byFh.set(point.fh, {
      value: point.value,
      ts: point.valid_time ? toTimestampSec(point.valid_time) : null,
    });
  }

  // Forecast hours that can be placed on the time axis, ascending.
  const sortedFhs = [...byFh.keys()]
    .filter((fh) => byFh.get(fh)!.ts != null)
    .sort((a, b) => a - b);
  if (sortedFhs.length === 0) return { xs, step, cumul };

  /** Cumulative value at `fh`, or null when the point is absent/missing. */
  const cumulOf = (fh: number): number | null => {
    const entry = byFh.get(fh);
    return entry && entry.value != null ? entry.value : null;
  };

  // Select non-overlapping 6-hour bucket endpoints -> their bar value.
  const stepByFh = new Map<number, number>();

  const firstEndpoint = sortedFhs.find((fh) => fh >= 6 && cumulOf(fh) != null) ?? null;
  if (firstEndpoint != null) {
    // First bucket: prior cumulative is the fh-6 value if present, else 0.
    const priorCumul = cumulOf(firstEndpoint - 6) ?? 0;
    stepByFh.set(firstEndpoint, Math.max(0, cumulOf(firstEndpoint)! - priorCumul));

    let last = firstEndpoint;
    let guard = 0;
    while (guard++ < 1000) {
      const target = last + 6;
      const targetCumul = cumulOf(target);
      if (targetCumul != null) {
        // Native cadence continues exactly 6 h after the previous endpoint.
        stepByFh.set(target, Math.max(0, targetCumul - cumulOf(last)!));
        last = target;
        continue;
      }
      // Gap / cadence shift: re-anchor to the next fh whose fh-6 point exists
      // and starts at/after the previous endpoint (so buckets never overlap).
      const reanchor = sortedFhs.find(
        (fh) => fh > last && fh - 6 >= last && cumulOf(fh) != null && cumulOf(fh - 6) != null,
      );
      if (reanchor == null) break;
      stepByFh.set(reanchor, Math.max(0, cumulOf(reanchor)! - cumulOf(reanchor - 6)!));
      last = reanchor;
    }
  }

  // Emit cumulative at every native fh (smooth overlay); bars only at endpoints.
  for (const fh of sortedFhs) {
    xs.push(byFh.get(fh)!.ts!);
    cumul.push(byFh.get(fh)!.value);
    step.push(stepByFh.has(fh) ? stepByFh.get(fh)! : null);
  }

  return { xs, step, cumul };
}

function PrecipDetailSubChart({
  steps,
  model,
  timezone,
  qpfAxisMax,
  cumulAxisMax,
  nowMs,
}: {
  steps: SixHourSteps;
  model: string;
  timezone: string | null;
  // Shared y-axis maxima (across all rendered models) so sub-charts compare 1:1.
  qpfAxisMax: number;
  cumulAxisMax: number;
  nowMs?: number;
}) {
  const tz = timezone || "UTC";
  // Stable across re-renders so chart options aren't rebuilt (recreating the
  // uPlot instance) on unrelated re-renders such as scroll-spy.
  const nowSec = useMemo(() => Math.floor((nowMs ?? Date.now()) / 1000), [nowMs]);

  const { data, hasData } = useMemo(() => {
    const aligned = [steps.xs, steps.step, steps.cumul] as unknown as uPlot.AlignedData;
    return { data: aligned, hasData: steps.xs.length > 0 };
  }, [steps]);

  const options = useMemo<Omit<uPlot.Options, "width">>(() => {
    const tzDate = (ts: number) => uPlot.tzDate(new Date(ts * 1000), tz);
    const color = modelColor(model);
    return {
      height: SUBCHART_HEIGHT,
      tzDate,
      // Vertical crosshair only (no horizontal).
      cursor: { y: false, focus: { prox: 24 } },
      legend: { show: false },
      scales: {
        x: { time: true },
        // Fixed ranges from the global maxima so every 6-hr chart shares a scale.
        qpf: { range: [0, qpfAxisMax] },
        cumul: { range: [0, cumulAxisMax] },
      },
      series: [
        {},
        {
          label: "6-hr",
          scale: "qpf",
          stroke: color,
          fill: `${color}66`,
          paths: uPlot.paths.bars!({ size: [0.6, 28] }),
          points: { show: false },
          value: (_u: uPlot, v: number | null) => (v == null ? "—" : `${v.toFixed(2)} in`),
        },
        {
          label: "Cumulative",
          scale: "cumul",
          stroke: color,
          width: 2,
          spanGaps: true,
          points: { show: false },
          value: (_u: uPlot, v: number | null) => (v == null ? "—" : `${v.toFixed(2)} in`),
        },
      ],
      axes: [
        timeXAxis(tz),
        valueYAxis((v) => `${v.toFixed(2)}`, { scale: "qpf", side: 3, size: 44 }),
        valueYAxis((v) => `${v.toFixed(1)}`, { scale: "cumul", side: 1, size: 40 }),
      ],
      plugins: [dayBoundaryPlugin(tz), nowMarkerPlugin(nowSec), cursorTooltipPlugin(tz)],
    };
  }, [model, tz, nowSec, qpfAxisMax, cumulAxisMax]);

  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 text-[12px] text-white/70">
        <span
          className="h-2 w-2 rounded-full"
          style={{ backgroundColor: modelColor(model) }}
        />
        {modelShortName(model)}
      </div>
      {hasData ? (
        <UplotChart options={options} data={data} className="w-full" />
      ) : (
        <div className="flex h-[160px] w-full items-center justify-center text-[12px] text-white/35">
          No precipitation data
        </div>
      )}
    </div>
  );
}

/**
 * Collapsible per-model 6-hr precipitation detail (bars) + cumulative overlay.
 * Default collapsed. Uses the existing meteogram data — no extra fetch.
 */
export function PrecipDetailPanel({
  response,
  visibleModels,
  timezone,
  expanded,
  onToggle,
  nowMs,
}: PanelProps) {
  const models = PRECIP_GUIDANCE_MODELS.filter(
    (model) => visibleModels.has(model) && seriesPoints(response, model, "precip_total"),
  );
  const modelsKey = models.join(",");

  // Derive each model's buckets once and a shared y-axis max across all of them,
  // so every 6-hr sub-chart uses the same scale and bars are directly comparable.
  const { stepsByModel, qpfAxisMax, cumulAxisMax } = useMemo(() => {
    const stepsByModel = new Map<string, SixHourSteps>();
    let qpfMax = 0;
    let cumulMax = 0;
    for (const model of models) {
      const steps = sixHourSteps(seriesPoints(response, model, "precip_total"));
      stepsByModel.set(model, steps);
      for (const v of steps.step) if (v != null && v > qpfMax) qpfMax = v;
      for (const v of steps.cumul) if (v != null && v > cumulMax) cumulMax = v;
    }
    // 10% headroom; fall back to 1 when there is no precip so the axis isn't flat.
    return {
      stepsByModel,
      qpfAxisMax: qpfMax > 0 ? qpfMax * 1.1 : 1,
      cumulAxisMax: cumulMax > 0 ? cumulMax * 1.1 : 1,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [response, modelsKey]);

  return (
    <div className="mt-3 rounded-lg border border-white/10 [overflow-anchor:none]">
      <button
        type="button"
        onClick={onToggle}
        onMouseDown={(e) => e.preventDefault()}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between px-3 py-2 text-[13px] text-white/75 transition-colors hover:text-white/90"
      >
        <span>6-hour precipitation detail</span>
        <ChevronDown
          className={`h-4 w-4 transition-transform ${expanded ? "rotate-180" : ""}`}
        />
      </button>
      {expanded && (
        <div className="border-t border-white/10 p-3">
          {models.length === 0 ? (
            <div className="py-6 text-center text-[12px] text-white/35">
              No precipitation guidance available for this location.
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {models.map((model) => (
                <PrecipDetailSubChart
                  key={model}
                  steps={stepsByModel.get(model)!}
                  model={model}
                  timezone={timezone}
                  qpfAxisMax={qpfAxisMax}
                  cumulAxisMax={cumulAxisMax}
                  nowMs={nowMs}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
