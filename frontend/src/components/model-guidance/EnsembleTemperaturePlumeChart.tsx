import { useCallback } from "react";

import { EnsemblePlumeChart } from "@/components/model-guidance/EnsemblePlumeChart";
import type { MeteogramResponse } from "@/lib/meteogram-types";

type Props = {
  response: MeteogramResponse | null;
  model: string;
  variable?: "tmp2m" | "tmp850";
  unitsFallback?: "F" | "C";
  timezone: string | null;
  nowMs?: number;
};

function unitsLabel(units: string): string {
  if (units === "F" || units === "C") return `°${units}`;
  return units;
}

/** Temperature member plume (spaghetti) for one ensemble model. §7 Phase 3. */
export function EnsembleTemperaturePlumeChart({
  response,
  model,
  variable = "tmp2m",
  unitsFallback = "F",
  timezone,
  nowMs,
}: Props) {
  const formatValue = useCallback(
    (value: number, units: string) => `${Math.round(value)} ${unitsLabel(units)}`,
    [],
  );

  return (
    <EnsemblePlumeChart
      response={response}
      model={model}
      variable={variable}
      unitsFallback={unitsFallback}
      formatValue={formatValue}
      timezone={timezone}
      nowMs={nowMs}
      emptyMessage={`No member ${variable === "tmp850" ? "850 mb " : ""}temperature data available for this location.`}
    />
  );
}
