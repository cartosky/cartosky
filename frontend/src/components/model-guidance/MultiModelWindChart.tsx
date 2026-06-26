import { useCallback } from "react";

import { MultiModelLineChart } from "@/components/charts/MultiModelLineChart";
import { WIND_GUIDANCE_MODELS } from "@/lib/chart-constants";
import type { MeteogramResponse } from "@/lib/meteogram-types";

type Props = {
  response: MeteogramResponse | null;
  visibleModels: Set<string>;
  timezone: string | null;
  nowMs?: number;
};

/** Multi-model 10 m wind speed (mph) at native temporal resolution. Phase 1B. */
export function MultiModelWindChart({ response, visibleModels, timezone, nowMs }: Props) {
  const formatValue = useCallback(
    (value: number, units: string) => `${Math.round(value)} ${units}`,
    [],
  );

  return (
    <MultiModelLineChart
      response={response}
      models={WIND_GUIDANCE_MODELS}
      visibleModels={visibleModels}
      variable="wspd10m"
      unitsFallback="mph"
      formatValue={formatValue}
      timezone={timezone}
      nowMs={nowMs}
      clampZero
      emptyMessage="No wind guidance available for this location."
    />
  );
}
