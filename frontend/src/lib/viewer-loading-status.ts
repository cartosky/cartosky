/**
 * Shared run-build progress clamping (map viewer redesign Phase 2, Task 4).
 *
 * Single source of the timeline's `Building available/total hrs` rule so the
 * initial map scrim and BottomForecastControls cannot disagree: available is
 * the maximum finite published frame hour, capped into [0, total].
 */
export type RunBuildProgress = {
  availableForecastHours: number;
  freshnessTotal: number | null;
  cappedAvailableForecastHours: number;
  hasFreshnessTotal: boolean;
};

export function resolveRunBuildProgress(
  availableFrames: number[],
  totalForecastHours: number | null | undefined,
): RunBuildProgress {
  const finiteFrames = availableFrames.filter(Number.isFinite);
  const availableForecastHours = finiteFrames.length > 0 ? Math.max(...finiteFrames) : 0;
  const freshnessTotal = Number.isFinite(totalForecastHours ?? Number.NaN)
    ? Math.max(0, Number(totalForecastHours))
    : null;
  const cappedAvailableForecastHours = freshnessTotal !== null
    ? Math.max(0, Math.min(availableForecastHours, freshnessTotal))
    : availableForecastHours;
  return {
    availableForecastHours,
    freshnessTotal,
    cappedAvailableForecastHours,
    hasFreshnessTotal: freshnessTotal !== null && freshnessTotal > 0,
  };
}
