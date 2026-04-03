import {
  formatValidRunIssuedLabel,
  formatObservedRunLabel,
  formatRunLabel as formatRunLabelFromTimeAxis,
  parseRunId,
  type TimeAxisMode,
} from "@/lib/time-axis";

export type RunOption = {
  value: string;
  label: string;
};

export function formatRunLabel(runId: string, timeAxisMode: TimeAxisMode = "forecast"): string {
  if (timeAxisMode === "valid") {
    return formatValidRunIssuedLabel(runId);
  }
  return timeAxisMode === "observed" ? formatObservedRunLabel(runId) : formatRunLabelFromTimeAxis(runId);
}

export function latestRunLabel(runId: string | null, timeAxisMode: TimeAxisMode = "forecast"): string {
  if (!runId) {
    return "Latest";
  }
  if (timeAxisMode === "valid") {
    return `Issued ${formatValidRunIssuedLabel(runId)}`;
  }
  return `Latest (${formatRunLabel(runId, timeAxisMode)})`;
}

export function sortRunIdsDescending(runs: string[]): string[] {
  return Array.from(new Set(runs.filter(Boolean))).sort((left, right) => {
    const leftTime = parseRunId(left)?.getTime() ?? Number.NEGATIVE_INFINITY;
    const rightTime = parseRunId(right)?.getTime() ?? Number.NEGATIVE_INFINITY;
    if (leftTime !== rightTime) {
      return rightTime - leftTime;
    }
    return right.localeCompare(left);
  });
}

export function pickLatestRunId(runs: string[]): string | null {
  return sortRunIdsDescending(runs)[0] ?? null;
}

export function buildRunOptions(
  runs: string[],
  latestRunId: string | null,
  timeAxisMode: TimeAxisMode = "forecast"
): RunOption[] {
  const concrete = sortRunIdsDescending(runs)
    .filter((runId) => runId !== latestRunId);

  return [
    { value: "latest", label: latestRunLabel(latestRunId, timeAxisMode) },
    ...concrete.map((runId) => ({ value: runId, label: formatRunLabel(runId, timeAxisMode) })),
  ];
}
