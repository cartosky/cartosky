import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { fetchRuns } from "@/lib/api";
import { ChartContainer } from "@/components/charts/ChartContainer";
import { ModelPillFilter } from "@/components/charts/ModelPillFilter";
import { EnsembleMeanTemperatureChart } from "@/components/model-guidance/EnsembleMeanTemperatureChart";
import { EnsembleMeanPrecipChart } from "@/components/model-guidance/EnsembleMeanPrecipChart";
import { EnsemblePrecipPlumeChart } from "@/components/model-guidance/EnsemblePrecipPlumeChart";
import { EnsemblePrecipProbabilityCard } from "@/components/model-guidance/EnsemblePrecipProbabilityCard";
import { EnsembleTemperaturePlumeChart } from "@/components/model-guidance/EnsembleTemperaturePlumeChart";
import { EnsembleTemperatureSpreadChart } from "@/components/model-guidance/EnsembleTemperatureSpreadChart";
import { useMeteogram } from "@/hooks/useMeteogram";
import {
  ENSEMBLES_TAB_VARIABLES,
  MEMBER_PLUME_MODELS,
  modelShortName,
} from "@/lib/chart-constants";
import { eligibleEnsembleModels } from "@/lib/eligible-ensemble-models";
import { useEntitlements } from "@/lib/entitlements";

type Props = {
  lat: number;
  lon: number;
  timezone: string | null;
};

/** Ensembles top-level tab — mean-only ensemble guidance (EPS, GEFS). Phase 2. */
export function EnsemblesTabContent({ lat, lon, timezone }: Props) {
  const { canAccessProduct } = useEntitlements();
  const [searchParams, setSearchParams] = useSearchParams();

  const eligibleModels = useMemo(
    () => eligibleEnsembleModels(canAccessProduct),
    [canAccessProduct],
  );

  const eligibleKey = eligibleModels.join(",");

  // Pill filter state lives here and is shared by both mean charts. Defaults to
  // all eligible ensemble models active. Model selection is kept local (not
  // URL-synced); only run pinning is persisted to the URL below.
  const [activeModels, setActiveModels] = useState<Set<string>>(
    () => new Set(eligibleModels),
  );

  // Active selection intersected with the eligible set. An empty selection
  // (user deselected every pill) renders empty charts, mirroring the Models tab.
  const visibleModels = useMemo(() => {
    const next = new Set<string>();
    for (const model of eligibleModels) {
      if (activeModels.has(model)) next.add(model);
    }
    return next;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeModels, eligibleKey]);

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

  // Run pins live in the URL under a dedicated param so Models-tab and
  // Ensembles-tab pinning never collide in a shared link.
  const pinnedRunsParam = searchParams.get("ensemble_pinned_runs");

  const pinnedRuns = useMemo(() => {
    if (!pinnedRunsParam) return {} as Record<string, string>;
    const result: Record<string, string> = {};
    for (const pair of pinnedRunsParam.split(",")) {
      const [model, runId] = pair.split(":");
      if (model && runId) result[model] = runId;
    }
    return result;
  }, [pinnedRunsParam]);

  const handleRunChange = useCallback(
    (model: string, runId: string | null) => {
      const next = new URLSearchParams(searchParams);
      const current: Record<string, string> = {};
      for (const pair of (searchParams.get("ensemble_pinned_runs") ?? "").split(",")) {
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
        next.delete("ensemble_pinned_runs");
      } else {
        next.set("ensemble_pinned_runs", entries.map(([m, r]) => `${m}:${r}`).join(","));
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const { data, loading, isUpdating, error, reload } = useMeteogram({
    lat,
    lon,
    models: eligibleModels,
    variables: [...ENSEMBLES_TAB_VARIABLES],
    pinnedRuns,
  });

  // Member plumes (member pipeline Phase 5): a SEPARATE meteogram call scoped
  // to member-publishing models only — include_members 400s the whole request
  // if any requested model lacks member support (EPS joins at pipeline
  // Phase 4). Restricting the pinned runs to the same models keeps the plume
  // in lockstep with the pill run selector.
  const plumeModels = useMemo(
    () => MEMBER_PLUME_MODELS.filter((model) => eligibleModels.includes(model)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [eligibleKey],
  );
  const plumePinnedRuns = useMemo(() => {
    const result: Record<string, string> = {};
    for (const model of plumeModels) {
      if (pinnedRuns[model]) result[model] = pinnedRuns[model];
    }
    return result;
  }, [plumeModels, pinnedRuns]);
  const {
    data: memberData,
    loading: membersLoading,
    error: membersError,
    reload: reloadMembers,
  } = useMeteogram({
    lat,
    lon,
    models: plumeModels,
    variables: [...ENSEMBLES_TAB_VARIABLES],
    pinnedRuns: plumePinnedRuns,
    includeMembers: true,
    enabled: plumeModels.length > 0,
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

  const servedRuns = useMemo(() => {
    if (!data) return undefined;
    const result: Record<string, string> = {};
    for (const model of eligibleModels) {
      const runId = data.series?.[model]?.run_id;
      if (typeof runId === "string" && runId.trim()) {
        result[model] = runId;
      }
    }
    return Object.keys(result).length > 0 ? result : undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, eligibleKey]);

  const showSkeleton = loading && !data;

  const handleFilterChange = useCallback((next: Set<string>) => {
    setActiveModels(next);
  }, []);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, eligibleKey, isUpdating]);

  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
          <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-white/40">
            Filter:
          </span>
          <ModelPillFilter
            models={eligibleModels}
            activeModels={visibleModels}
            onChange={handleFilterChange}
            availableRuns={availableRuns}
            pinnedRuns={pinnedRuns}
            servedRuns={servedRuns}
            onRunChange={handleRunChange}
          />
        </div>
      </div>

      <section id="ensemble-temperature">
        <ChartContainer
          title="Mean temperature"
          subtitle={degradedSubtitle}
          isLoading={showSkeleton}
          error={error}
          onRetry={reload}
        >
          <EnsembleMeanTemperatureChart
            response={data}
            visibleModels={visibleModels}
            timezone={timezone}
          />
        </ChartContainer>
      </section>

      {plumeModels.map((model) => (
        <section key={`${model}-temp-plume`} id={`ensemble-${model}-temperature-plume`}>
          <ChartContainer
            title={`${modelShortName(model)} temperature members`}
            subtitle={`${modelShortName(model)} member plume — thin lines are individual members, bold is the mean, dashed white is the control`}
            isLoading={membersLoading && !memberData}
            error={membersError}
            onRetry={reloadMembers}
          >
            <EnsembleTemperaturePlumeChart
              response={memberData}
              model={model}
              timezone={timezone}
            />
          </ChartContainer>
        </section>
      ))}

      <section id="ensemble-precip-probability">
        <EnsemblePrecipProbabilityCard />
      </section>

      <section id="ensemble-temperature-spread">
        <EnsembleTemperatureSpreadChart />
      </section>

      <section id="ensemble-precipitation">
        <ChartContainer
          title="Mean cumulative precipitation"
          subtitle={degradedSubtitle}
          isLoading={showSkeleton}
          error={error}
          onRetry={reload}
        >
          <EnsembleMeanPrecipChart
            response={data}
            visibleModels={visibleModels}
            timezone={timezone}
          />
        </ChartContainer>
      </section>

      {plumeModels.map((model) => (
        <section key={`${model}-precip-plume`} id={`ensemble-${model}-precipitation-plume`}>
          <ChartContainer
            title={`${modelShortName(model)} precipitation members`}
            subtitle={`Cumulative precipitation per ${modelShortName(model)} member — thin lines are individual members, bold is the mean, dashed white is the control`}
            isLoading={membersLoading && !memberData}
            error={membersError}
            onRetry={reloadMembers}
          >
            <EnsemblePrecipPlumeChart
              response={memberData}
              model={model}
              timezone={timezone}
            />
          </ChartContainer>
        </section>
      ))}
    </div>
  );
}
