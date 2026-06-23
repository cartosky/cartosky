import { useEffect, useMemo, useState } from "react";

import { ChartContainer } from "@/components/charts/ChartContainer";
import { ModelPillFilter } from "@/components/charts/ModelPillFilter";
import { MultiModelTemperatureChart } from "@/components/model-guidance/MultiModelTemperatureChart";
import { useMeteogram } from "@/hooks/useMeteogram";
import { eligibleTemperatureModels } from "@/lib/eligible-temperature-models";
import { useEntitlements } from "@/lib/entitlements";

type Props = {
  lat: number;
  lon: number;
  timezone: string | null;
};

export function ModelsTabContent({ lat, lon, timezone }: Props) {
  const { canAccessProduct } = useEntitlements();

  const eligibleModels = useMemo(
    () => eligibleTemperatureModels(lat, lon, canAccessProduct),
    [lat, lon, canAccessProduct],
  );

  const eligibleKey = eligibleModels.join(",");

  const [activeModels, setActiveModels] = useState<Set<string>>(() => new Set(eligibleModels));

  // Reset active pills to the eligible set whenever coverage/entitlement changes.
  useEffect(() => {
    setActiveModels(new Set(eligibleModels));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eligibleKey]);

  const { data, loading, isUpdating, error, reload } = useMeteogram({
    lat,
    lon,
    models: eligibleModels,
    variables: ["tmp2m"],
  });

  const showSkeleton = loading && !data;

  const subtitle = useMemo(() => {
    const parts: string[] = [];
    if (isUpdating) parts.push("Updating…");
    if (data) {
      const degraded = eligibleModels.some((model) => {
        const status = data.series?.[model]?.status;
        return status === "partial" || status === "unavailable";
      });
      if (degraded) parts.push("Some models unavailable");
    }
    return parts.length > 0 ? parts.join(" · ") : undefined;
  }, [data, eligibleModels, isUpdating]);

  return (
    <div className="flex flex-col gap-6">
      <section id="temperature">
        <ChartContainer
          title="Temperature"
          subtitle={subtitle}
          isLoading={showSkeleton}
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
