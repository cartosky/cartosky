import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { fetchRuns } from "@/lib/api";
import { ChartContainer } from "@/components/charts/ChartContainer";
import { ModelDetailView } from "@/components/model-guidance/ModelDetailView";
import { ModelsTabControlPanel } from "@/components/model-guidance/ModelsTabControlPanel";
import { MultiModelTemperatureChart } from "@/components/model-guidance/MultiModelTemperatureChart";
import { MultiModelCumulativePrecipChart } from "@/components/model-guidance/MultiModelCumulativePrecipChart";
import { MultiModelWindChart } from "@/components/model-guidance/MultiModelWindChart";
import { PrecipDetailPanel } from "@/components/model-guidance/PrecipDetailPanel";
import { useMeteogram } from "@/hooks/useMeteogram";
import {
  MODELS_TAB_VARIABLES,
  PRECIP_GUIDANCE_MODELS,
  TEMPERATURE_GUIDANCE_MODELS,
  WIND_GUIDANCE_MODELS,
} from "@/lib/chart-constants";
import { eligibleTemperatureModels } from "@/lib/eligible-temperature-models";
import { useEntitlements } from "@/lib/entitlements";
import {
  buildRunInitSubtitle,
  joinSubtitleParts,
} from "@/lib/model-guidance-subtitle";

type Props = {
  lat: number;
  lon: number;
  timezone: string | null;
  /** Location line passed to the Model Detail card's image export. */
  locationText: string;
};

type ViewMode = "compare" | "detail";

export function ModelsTabContent({ lat, lon, timezone, locationText }: Props) {
  const { canAccessProduct } = useEntitlements();
  const [searchParams, setSearchParams] = useSearchParams();

  const eligibleModels = useMemo(
    () => eligibleTemperatureModels(lat, lon, canAccessProduct),
    [lat, lon, canAccessProduct],
  );

  const eligibleKey = eligibleModels.join(",");

  // Per-model run lists from /api/v4/{model}/runs — the same source the map
  // viewer uses. Note this includes the newest run even while it is still
  // building its sampling data, which the meteogram cannot serve yet; that is
  // filtered out below against the run the meteogram actually returns.
  const [publishedRuns, setPublishedRuns] = useState<
    Record<string, string[]> | undefined
  >(undefined);

  useEffect(() => {
    if (!eligibleModels.length) return;
    let cancelled = false;

    async function loadRuns() {
      const results: Record<string, string[]> = {};
      await Promise.all(
        eligibleModels.map(async (model) => {
          try {
            const runs = await fetchRuns(model);
            if (!cancelled && runs.length > 0) {
              results[model] = runs;
            }
          } catch {
            // silently skip — pill renders without run selector for this model
          }
        }),
      );
      if (!cancelled) {
        setPublishedRuns(
          Object.keys(results).length > 0 ? results : undefined,
        );
      }
    }

    void loadRuns();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eligibleKey]);

  const [precipDetailExpanded, setPrecipDetailExpanded] = useState(false);

  // View mode, selected detail model, and the Compare multi-select all live in
  // the URL so the page is shareable. Mirror the Forecast page's pattern: clone
  // the current params, mutate, then setSearchParams with { replace: true }.
  const viewMode: ViewMode = searchParams.get("section") === "detail" ? "detail" : "compare";
  const detailModelParam = searchParams.get("detail_model");
  const modelsParam = searchParams.get("models");
  const pinnedRunsParam = searchParams.get("pinned_runs");

  const pinnedRuns = useMemo(() => {
  if (!pinnedRunsParam) return {} as Record<string, string>;
    const result: Record<string, string> = {};
    for (const pair of pinnedRunsParam.split(",")) {
      const [model, runId] = pair.split(":");
      if (model && runId) result[model] = runId;
    }
    return result;
  }, [pinnedRunsParam]);

  // Compare-mode active models, derived from the URL. Absent param → all
  // eligible (default). Present → the requested ids that are still eligible
  // here, so an explicit subset (or empty `models=`) round-trips through a link.
  const activeModels = useMemo(() => {
    if (modelsParam == null) return new Set(eligibleModels);
    const requested = modelsParam
      .split(",")
      .map((id) => id.trim())
      .filter((id) => eligibleModels.includes(id));
    return new Set(requested);
  }, [modelsParam, eligibleModels]);

  const setViewMode = useCallback(
    (mode: ViewMode) => {
      const next = new URLSearchParams(searchParams);
      if (mode === "compare") next.delete("section");
      else next.set("section", "detail");
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const handleRunChange = useCallback(
    (model: string, runId: string | null) => {
      const next = new URLSearchParams(searchParams);
      const current: Record<string, string> = {};
      for (const pair of (searchParams.get("pinned_runs") ?? "").split(",")) {
        const [m, r] = pair.split(":");
        if (m && r) current[m] = r;
      }
      if (runId === null) {
        delete current[model];
      } else {
        current[model] = runId;
      }
      const entries = Object.entries(current);
      if (entries.length === 0) {
        next.delete("pinned_runs");
      } else {
        next.set("pinned_runs", entries.map(([m, r]) => `${m}:${r}`).join(","));
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const togglePrecipDetail = useCallback(() => {
    const scrollY = window.scrollY;
    setPrecipDetailExpanded((prev) => !prev);
    // Charts mount/unmount on expand; restore viewport so layout growth does not jump scroll.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        window.scrollTo(0, scrollY);
      });
    });
  }, []);

  const { data, loading, isUpdating, error, reload } = useMeteogram({
    lat,
    lon,
    models: eligibleModels,
    variables: [...MODELS_TAB_VARIABLES],
    pinnedRuns,
  });

  // Highest complete run seen per model. The backend only ever serves a complete
  // run (a pinned run that is still building falls back to the latest complete
  // one), so the max served run id is the latest complete run. Tracking the max
  // — rather than reading the current response's run_id — keeps the ceiling
  // stable when the user pins an older run, which would otherwise lower the
  // served run_id and hide the newer (still servable) runs from the selector.
  const [latestCompleteRun, setLatestCompleteRun] = useState<
    Record<string, string>
  >({});

  useEffect(() => {
    if (!data) return;
    setLatestCompleteRun((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const model of eligibleModels) {
        const served = data.series?.[model]?.run_id;
        if (served && (!next[model] || served > next[model])) {
          next[model] = served;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, eligibleKey]);

  // Clamp each model's run list to runs at or older than its latest complete
  // run, so the selector only offers runs the meteogram can serve (the newest
  // run is hidden while it is still building). Run ids are fixed-width and
  // zero-padded (YYYYMMDD_HHz), so lexical order matches time order. Models with
  // no complete run yet are omitted entirely.
  const availableRuns = useMemo(() => {
    if (!publishedRuns) return undefined;
    const result: Record<string, string[]> = {};
    for (const [model, runs] of Object.entries(publishedRuns)) {
      const ceiling = latestCompleteRun[model];
      if (!ceiling) continue;
      const servable = runs.filter((run) => run <= ceiling);
      if (servable.length > 0) result[model] = servable;
    }
    return Object.keys(result).length > 0 ? result : undefined;
  }, [publishedRuns, latestCompleteRun]);

  // Default detail model: ECMWF when present & ok, otherwise the first model
  // with status "ok", otherwise the first eligible model.
  const defaultDetailModel = useMemo(() => {
    if (eligibleModels.includes("ecmwf") && data?.series?.ecmwf?.status === "ok") return "ecmwf";
    const firstOk = eligibleModels.find((model) => data?.series?.[model]?.status === "ok");
    return firstOk ?? eligibleModels[0] ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, eligibleKey]);

  const detailModel =
    detailModelParam && eligibleModels.includes(detailModelParam)
      ? detailModelParam
      : defaultDetailModel;

  const handleSelectDetailModel = useCallback(
    (model: string) => {
      const next = new URLSearchParams(searchParams);
      next.set("detail_model", model);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const detailActiveModels = useMemo(
    () => new Set(detailModel ? [detailModel] : []),
    [detailModel],
  );

  const setCompareModels = useCallback(
    (next: Set<string>) => {
      const params = new URLSearchParams(searchParams);
      const selected = eligibleModels.filter((model) => next.has(model));
      // Default (all eligible) → omit for a clean URL; otherwise persist the
      // selection in eligible order (an empty selection writes `models=`).
      if (selected.length === eligibleModels.length) params.delete("models");
      else params.set("models", selected.join(","));
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams, eligibleModels],
  );

  const handleFilterChange = useCallback(
    (next: Set<string>) => {
      if (viewMode === "detail") {
        const model = [...next][0];
        if (model) handleSelectDetailModel(model);
        return;
      }
      setCompareModels(next);
    },
    [viewMode, handleSelectDetailModel, setCompareModels],
  );

  const showSkeleton = loading && !data;

  const degradedSubtitle = useMemo(() => {
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

  const temperatureSubtitle = useMemo(
    () =>
      joinSubtitleParts(
        buildRunInitSubtitle(data, TEMPERATURE_GUIDANCE_MODELS, activeModels, "tmp2m"),
        degradedSubtitle,
      ),
    [data, activeModels, degradedSubtitle],
  );

  const precipSubtitle = useMemo(
    () =>
      buildRunInitSubtitle(data, PRECIP_GUIDANCE_MODELS, activeModels, "precip_total"),
    [data, activeModels],
  );

  const windSubtitle = useMemo(
    () => buildRunInitSubtitle(data, WIND_GUIDANCE_MODELS, activeModels, "wspd10m"),
    [data, activeModels],
  );

  return (
    <div className="flex flex-col gap-6">
      <ModelsTabControlPanel
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        models={eligibleModels}
        activeModels={viewMode === "detail" ? detailActiveModels : activeModels}
        onActiveModelsChange={handleFilterChange}
        filterMode={viewMode === "detail" ? "single" : "multi"}
        availableRuns={availableRuns}
        pinnedRuns={pinnedRuns}
        onRunChange={handleRunChange}
      />

      {viewMode === "detail" ? (
        <ModelDetailView
          response={data}
          loading={loading}
          error={error}
          reload={reload}
          selectedModel={detailModel}
          timezone={timezone}
          locationText={locationText}
        />
      ) : (
        <>
          <section id="temperature">
            <ChartContainer
              title="Temperature"
              subtitle={temperatureSubtitle}
              isLoading={showSkeleton}
              error={error}
              onRetry={reload}
            >
              <MultiModelTemperatureChart
                response={data}
                visibleModels={activeModels}
                timezone={timezone}
              />
            </ChartContainer>
          </section>

          <section id="precipitation">
            <ChartContainer
              title="Precipitation"
              subtitle={precipSubtitle}
              isLoading={showSkeleton}
              error={error}
              onRetry={reload}
            >
              <MultiModelCumulativePrecipChart
                response={data}
                visibleModels={activeModels}
                timezone={timezone}
              />
              <PrecipDetailPanel
                response={data}
                visibleModels={activeModels}
                timezone={timezone}
                expanded={precipDetailExpanded}
                onToggle={togglePrecipDetail}
              />
            </ChartContainer>
          </section>

          <section id="wind">
            <ChartContainer
              title="Wind"
              subtitle={windSubtitle}
              isLoading={showSkeleton}
              error={error}
              onRetry={reload}
            >
              <MultiModelWindChart
                response={data}
                visibleModels={activeModels}
                timezone={timezone}
              />
            </ChartContainer>
          </section>
        </>
      )}
    </div>
  );
}
