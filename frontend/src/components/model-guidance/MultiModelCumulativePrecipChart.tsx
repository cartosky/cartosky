import { useCallback } from "react";

import { MultiModelLineChart } from "@/components/charts/MultiModelLineChart";
import { PRECIP_GUIDANCE_MODELS } from "@/lib/chart-constants";
import type { MeteogramResponse } from "@/lib/meteogram-types";

type Props = {
  response: MeteogramResponse | null;
  visibleModels: Set<string>;
  timezone: string | null;
  nowMs?: number;
};

/** Multi-model cumulative precipitation (inches from run init). Phase 1B. */
export function MultiModelCumulativePrecipChart({
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
      models={PRECIP_GUIDANCE_MODELS}
      visibleModels={visibleModels}
      variable="precip_total"
      unitsFallback="in"
      formatValue={formatValue}
      timezone={timezone}
      nowMs={nowMs}
      clampZero
      emptyMessage="No precipitation guidance available for this location."
    />
  );
}
