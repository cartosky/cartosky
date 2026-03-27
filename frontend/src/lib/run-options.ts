import { formatRunLabel as formatRunLabelFromTimeAxis, parseRunId } from "@/lib/time-axis";

export type RunOption = {
  value: string;
  label: string;
};

export function formatRunLabel(runId: string): string {
  return formatRunLabelFromTimeAxis(runId);
}

export function latestRunLabel(runId: string | null): string {
  if (!runId) {
    return "Latest";
  }
  return `Latest (${formatRunLabel(runId)})`;
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

export function buildRunOptions(runs: string[], latestRunId: string | null): RunOption[] {
  const concrete = sortRunIdsDescending(runs)
    .filter((runId) => runId !== latestRunId);

  return [
    { value: "latest", label: latestRunLabel(latestRunId) },
    ...concrete.map((runId) => ({ value: runId, label: formatRunLabel(runId) })),
  ];
}
