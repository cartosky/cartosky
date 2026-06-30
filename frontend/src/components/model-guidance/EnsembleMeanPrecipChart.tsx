import { useCallback } from "react";

import { MultiModelLineChart } from "@/components/charts/MultiModelLineChart";
import {
  ENSEMBLE_GUIDANCE_MODELS,
  ensembleMeanStroke,
  ensembleMeanWidth,
} from "@/lib/chart-constants";
import type { MeteogramResponse } from "@/lib/meteogram-types";

type Props = {
  response: MeteogramResponse | null;
  visibleModels: Set<string>;
  timezone: string | null;
  nowMs?: number;
};

/** Ensemble mean cumulative precipitation (EPS mean + GEFS mean). Phase 2. */
export function EnsembleMeanPrecipChart({
  response,
  visibleModels,
  timezone,
  nowMs,
}: Props) {
  const formatValue = useCallback(
    (value: number, units: string) => `${value.toFixed(2)} ${units}`,
    [],
  );

  return (
    <MultiModelLineChart
      response={response}
      models={ENSEMBLE_GUIDANCE_MODELS}
      visibleModels={visibleModels}
      variable="precip_total"
      unitsFallback="in"
      formatValue={formatValue}
      timezone={timezone}
      nowMs={nowMs}
      clampZero
      strokeFor={ensembleMeanStroke}
      widthFor={ensembleMeanWidth}
      showPoints={false}
      emptyMessage="No ensemble precipitation guidance available for this location."
    />
  );
}
