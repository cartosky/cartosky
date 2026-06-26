import { modelShortName } from "@/lib/chart-constants";
import type { MeteogramResponse } from "@/lib/meteogram-types";

/** "12z"-style init label parsed from a run_id like "20260306_12z". */
export function parseRunInitLabel(runId: string | null | undefined): string | null {
  if (!runId) return null;
  const match = /_(\d{2})z$/i.exec(runId);
  return match ? `${match[1]}z` : null;
}

/**
 * Per-model run init subtitle for visible models, e.g. "12z ECMWF · 18z NBM".
 * `models` order is preserved; only `activeModels` with usable series are included.
 */
export function buildRunInitSubtitle(
  data: MeteogramResponse | null,
  models: readonly string[],
  activeModels: Set<string>,
  variable?: string,
): string | undefined {
  if (!data) return undefined;

  const parts = models
    .filter((model) => activeModels.has(model))
    .map((model) => {
      const series = data.series?.[model];
      if (!series || (series.status !== "ok" && series.status !== "partial")) return null;
      if (variable) {
        const points = series.variables?.[variable]?.points;
        if (!Array.isArray(points) || points.length === 0) return null;
      }
      const init = parseRunInitLabel(series.run_id);
      return init ? `${init} ${modelShortName(model)}` : null;
    })
    .filter((entry): entry is string => entry != null);

  return parts.length > 0 ? parts.join(" · ") : undefined;
}

/** Join subtitle segments with the chart section's middle-dot separator. */
export function joinSubtitleParts(...parts: (string | undefined)[]): string | undefined {
  const filtered = parts.filter((part): part is string => Boolean(part));
  return filtered.length > 0 ? filtered.join(" · ") : undefined;
}
