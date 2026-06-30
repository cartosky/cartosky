import { ChartContainer } from "@/components/charts/ChartContainer";

/**
 * Temperature spread (P10/P90 envelope) placeholder. Spread cannot be derived
 * from the mean field alone — it requires per-member ensemble data (Phase 3).
 * Renders the message in the ChartContainer error slot; never fabricates a band.
 */
export function EnsembleTemperatureSpreadChart() {
  return (
    <ChartContainer
      title="Temperature spread"
      isLoading={false}
      error="Temperature spread requires per-member ensemble data. Coming soon."
    />
  );
}
