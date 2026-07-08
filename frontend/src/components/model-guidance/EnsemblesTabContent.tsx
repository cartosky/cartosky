import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { fetchRuns } from "@/lib/api";
import { ChartContainer } from "@/components/charts/ChartContainer";
import { ModelPillFilter } from "@/components/charts/ModelPillFilter";
import { EnsembleMeanTemperatureChart } from "@/components/model-guidance/EnsembleMeanTemperatureChart";
import { EnsembleMeanPrecipChart } from "@/components/model-guidance/EnsembleMeanPrecipChart";
import { EnsemblePrecipPlumeChart } from "@/components/model-guidance/EnsemblePrecipPlumeChart";
import { EnsembleTemperaturePlumeChart } from "@/components/model-guidance/EnsembleTemperaturePlumeChart";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useMeteogram } from "@/hooks/useMeteogram";
import {
  ENSEMBLES_TAB_VARIABLES,
  MEMBER_PLUME_MODELS,
  modelShortName,
} from "@/lib/chart-constants";
import { eligibleEnsembleModels } from "@/lib/eligible-ensemble-models";
import { useEntitlements } from "@/lib/entitlements";
import { buildRunOptions, formatRunLabel, sortRunIdsDescending } from "@/lib/run-options";

type Props = {
  lat: number;
  lon: number;
  timezone: string | null;
};

type EnsembleVariable = (typeof ENSEMBLES_TAB_VARIABLES)[number];

/** "means" = multi-model mean comparison; a model id = that model's members. */
type EnsembleView = "means" | (typeof MEMBER_PLUME_MODELS)[number];

const VARIABLE_LABELS: Record<EnsembleVariable, string> = {
  tmp2m: "Temperature",
  precip_total: "Precipitation",
};

type ControlOption = { value: string; label: string };

/**
 * Labeled themed dropdown for the control bar. Dropdowns (not segmented
 * toggles) by design: additional views/variables flow into them without the
 * bar growing. Uses the design-system Radix select so the open panel is the
 * site's glass/cyan styling everywhere — native <select> pickers fall back to
 * the generic browser control on mobile/tablet.
 */
function ControlSelect({
  label,
  ariaLabel,
  value,
  options,
  onChange,
}: {
  label: string;
  ariaLabel: string;
  value: string;
  options: ControlOption[];
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-white/40">
        {label}
      </span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger
          aria-label={ariaLabel}
          className="h-8 w-auto gap-1.5 rounded-lg border-white/[0.09] bg-white/[0.05] px-2.5 text-[12px] text-white/80 ring-offset-0 hover:bg-white/[0.08] focus:ring-1 focus:ring-cyan-300/40 focus:ring-offset-0"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/**
 * Ensembles top-level tab. Selector-driven (view / variable / run) rather than
 * a stacked list of every chart: "Means" compares the ensemble means across
 * models; a members view shows one model's member plume (member pipeline
 * Phase 5).
 */
export function EnsemblesTabContent({ lat, lon, timezone }: Props) {
  const { canAccessProduct } = useEntitlements();
  const [searchParams, setSearchParams] = useSearchParams();

  const eligibleModels = useMemo(
    () => eligibleEnsembleModels(canAccessProduct),
    [canAccessProduct],
  );

  const eligibleKey = eligibleModels.join(",");

  const plumeModels = useMemo(
    () => MEMBER_PLUME_MODELS.filter((model) => eligibleModels.includes(model)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [eligibleKey],
  );

  // ── View + variable selection (URL-synced, like the Models tab) ──────────
  const viewParam = searchParams.get("ensemble_view");
  const view: EnsembleView =
    viewParam && (plumeModels as readonly string[]).includes(viewParam)
      ? (viewParam as EnsembleView)
      : "means";
  const varParam = searchParams.get("ensemble_var");
  const variable: EnsembleVariable = (ENSEMBLES_TAB_VARIABLES as readonly string[]).includes(
    varParam ?? "",
  )
    ? (varParam as EnsembleVariable)
    : "tmp2m";

  const setUrlParam = useCallback(
    (key: string, value: string | null) => {
      const next = new URLSearchParams(searchParams);
      if (value === null) next.delete(key);
      else next.set(key, value);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const viewOptions = useMemo(
    () => [
      { value: "means" as const, label: "Means" },
      ...plumeModels.map((model) => ({
        value: model,
        label: `${modelShortName(model)} members`,
      })),
    ],
    [plumeModels],
  );

  const variableOptions = useMemo(
    () =>
      ENSEMBLES_TAB_VARIABLES.map((value) => ({
        value,
        label: VARIABLE_LABELS[value],
      })),
    [],
  );

  // ── Pill filter state for the means view ─────────────────────────────────
  const [activeModels, setActiveModels] = useState<Set<string>>(
    () => new Set(eligibleModels),
  );

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

  // ── Data: mean comparison + member payload (separate calls; the members
  // request may only name member-publishing models or the backend 400s) ────
  const { data, loading, isUpdating, error, reload } = useMeteogram({
    lat,
    lon,
    models: eligibleModels,
    variables: [...ENSEMBLES_TAB_VARIABLES],
    pinnedRuns,
  });

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

  // ── Member-view run selector ──────────────────────────────────────────────
  const memberModel = view === "means" ? null : view;
  // Same shape as the pill run popovers: "Latest (18Z 7/06)" first, then only
  // the OLDER runs — the latest run is never listed twice.
  const memberRunOptions = useMemo(() => {
    if (!memberModel) return [];
    const runs = sortRunIdsDescending(availableRuns?.[memberModel] ?? []);
    return buildRunOptions(runs, runs[0] ?? null);
  }, [memberModel, availableRuns]);
  const memberLatestRun = memberModel
    ? sortRunIdsDescending(availableRuns?.[memberModel] ?? [])[0] ?? null
    : null;
  // A pin equal to the latest run reads as "Latest" (that option represents it).
  const memberRunValue =
    memberModel && pinnedRuns[memberModel] && pinnedRuns[memberModel] !== memberLatestRun
      ? pinnedRuns[memberModel]
      : "latest";
  const memberServedRun = memberModel
    ? memberData?.series?.[memberModel]?.run_id ?? null
    : null;

  const memberChartTitle = memberModel
    ? `${modelShortName(memberModel)} ${VARIABLE_LABELS[variable].toLowerCase()} members`
    : "";
  // Only mention the control line when the served payload has one — EPS
  // publishes 50 pf members with no upstream control.
  const memberHasControl = memberModel
    ? Boolean(memberData?.series?.[memberModel]?.variables?.[variable]?.members?.control)
    : false;
  const memberChartSubtitle = memberModel
    ? [
        memberServedRun ? `Run ${formatRunLabel(memberServedRun)}` : null,
        "Colored lines are individual members · bold white is the mean" +
          (memberHasControl ? " · dashed is the control" : ""),
      ]
        .filter(Boolean)
        .join(" · ")
    : undefined;

  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
          <ControlSelect
            label="View"
            ariaLabel="Ensemble view"
            value={view}
            options={viewOptions}
            onChange={(next) => setUrlParam("ensemble_view", next === "means" ? null : next)}
          />
          <ControlSelect
            label="Variable"
            ariaLabel="Ensemble variable"
            value={variable}
            options={variableOptions}
            onChange={(next) => setUrlParam("ensemble_var", next === "tmp2m" ? null : next)}
          />
          {memberModel ? (
            <ControlSelect
              label="Run"
              ariaLabel={`${modelShortName(memberModel)} run`}
              value={memberRunValue}
              options={memberRunOptions}
              onChange={(next) =>
                handleRunChange(memberModel, next === "latest" ? null : next)
              }
            />
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-white/40">
                Models
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
          )}
        </div>
      </div>

      {view === "means" ? (
        <section id="ensemble-means">
          <ChartContainer
            title={
              variable === "tmp2m" ? "Mean temperature" : "Mean cumulative precipitation"
            }
            subtitle={degradedSubtitle}
            isLoading={showSkeleton}
            error={error}
            onRetry={reload}
          >
            {variable === "tmp2m" ? (
              <EnsembleMeanTemperatureChart
                response={data}
                visibleModels={visibleModels}
                timezone={timezone}
              />
            ) : (
              <EnsembleMeanPrecipChart
                response={data}
                visibleModels={visibleModels}
                timezone={timezone}
              />
            )}
          </ChartContainer>
        </section>
      ) : (
        <section id={`ensemble-${memberModel}-members`}>
          <ChartContainer
            title={memberChartTitle}
            subtitle={memberChartSubtitle}
            isLoading={membersLoading && !memberData}
            error={membersError}
            onRetry={reloadMembers}
          >
            {variable === "tmp2m" ? (
              <EnsembleTemperaturePlumeChart
                response={memberData}
                model={memberModel!}
                timezone={timezone}
              />
            ) : (
              <EnsemblePrecipPlumeChart
                response={memberData}
                model={memberModel!}
                timezone={timezone}
              />
            )}
          </ChartContainer>
        </section>
      )}
    </div>
  );
}
