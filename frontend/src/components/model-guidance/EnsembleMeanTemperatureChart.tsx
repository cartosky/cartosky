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

function unitsLabel(units: string): string {
  if (units === "F" || units === "C") return `°${units}`;
  return units;
}

/** Ensemble mean temperature (EPS mean + GEFS mean on one chart). Phase 2. */
export function EnsembleMeanTemperatureChart({
  response,
  visibleModels,
  timezone,
  nowMs,
}: Props) {
  const formatValue = useCallback(
    (value: number, units: string) => `${Math.round(value)} ${unitsLabel(units)}`,
    [],
  );

  return (
    <MultiModelLineChart
      response={response}
      models={ENSEMBLE_GUIDANCE_MODELS}
      visibleModels={visibleModels}
      variable="tmp2m"
      unitsFallback="F"
      formatValue={formatValue}
      timezone={timezone}
      nowMs={nowMs}
      strokeFor={ensembleMeanStroke}
      widthFor={ensembleMeanWidth}
      showPoints={false}
      emptyMessage="No ensemble temperature guidance available for this location."
    />
  );
}
