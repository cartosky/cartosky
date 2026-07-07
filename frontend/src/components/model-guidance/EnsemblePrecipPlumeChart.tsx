import { useCallback } from "react";

import { EnsemblePlumeChart } from "@/components/model-guidance/EnsemblePlumeChart";
import type { MeteogramResponse } from "@/lib/meteogram-types";

type Props = {
  response: MeteogramResponse | null;
  model: string;
  timezone: string | null;
  nowMs?: number;
};

/** Cumulative precipitation member plume for one ensemble model. §7 Phase 3. */
export function EnsemblePrecipPlumeChart({ response, model, timezone, nowMs }: Props) {
  const formatValue = useCallback(
    (value: number, units: string) => `${value.toFixed(2)} ${units}`,
    [],
  );

  return (
    <EnsemblePlumeChart
      response={response}
      model={model}
      variable="precip_total"
      unitsFallback="in"
      formatValue={formatValue}
      timezone={timezone}
      nowMs={nowMs}
      clampZero
      emptyMessage="No member precipitation data available for this location."
    />
  );
}
