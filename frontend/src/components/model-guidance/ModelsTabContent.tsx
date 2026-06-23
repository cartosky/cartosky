import { useEffect, useMemo, useState } from "react";

import { ChartContainer } from "@/components/charts/ChartContainer";
import { ModelPillFilter } from "@/components/charts/ModelPillFilter";
import { MultiModelTemperatureChart } from "@/components/model-guidance/MultiModelTemperatureChart";
import { isInsideConus } from "@/lib/chart-constants";
import { useEntitlements } from "@/lib/entitlements";
import { useMeteogram } from "@/hooks/useMeteogram";

// Phase 1A temperature models in display order.
const TEMPERATURE_MODELS = ["ecmwf", "gfs", "nam", "aifs", "nbm"];
// Models restricted to CONUS coverage (omitted from pills + request outside it).
const CONUS_ONLY_MODELS = new Set(["nam", "nbm"]);

type Props = {
  lat: number;
  lon: number;
  timezone: string | null;
};

export function ModelsTabContent({ lat, lon, timezone }: Props) {
  const { canAccessProduct } = useEntitlements();

  const eligibleModels = useMemo(() => {
    const insideConus = isInsideConus(lat, lon);
    return TEMPERATURE_MODELS.filter((model) => {
      if (CONUS_ONLY_MODELS.has(model) && !insideConus) return false;
      if (!canAccessProduct(model)) return false;
      return true;
    });
  }, [lat, lon, canAccessProduct]);

  const eligibleKey = eligibleModels.join(",");

  const [activeModels, setActiveModels] = useState<Set<string>>(() => new Set(eligibleModels));

  // Reset active pills to the eligible set whenever coverage/entitlement changes.
  useEffect(() => {
    setActiveModels(new Set(eligibleModels));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eligibleKey]);

  const { data, loading, error, reload } = useMeteogram({
    lat,
    lon,
    models: eligibleModels,
    variables: ["tmp2m"],
  });

  const subtitle = useMemo(() => {
    if (!data) return undefined;
    const degraded = eligibleModels.some((model) => {
      const status = data.series?.[model]?.status;
      return status === "partial" || status === "unavailable";
    });
    return degraded ? "Some models unavailable" : undefined;
  }, [data, eligibleModels]);

  return (
    <div className="flex flex-col gap-6">
      <section id="temperature">
        <ChartContainer
          title="Temperature"
          subtitle={subtitle}
          isLoading={loading}
          error={error}
          onRetry={reload}
          filterSlot={
            <ModelPillFilter
              models={eligibleModels}
              activeModels={activeModels}
              onChange={setActiveModels}
            />
          }
        >
          <MultiModelTemperatureChart
            response={data}
            visibleModels={activeModels}
            timezone={timezone}
          />
        </ChartContainer>
      </section>
    </div>
  );
}
