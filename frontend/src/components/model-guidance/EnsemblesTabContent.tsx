import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { fetchRuns } from "@/lib/api";
import { ChartContainer } from "@/components/charts/ChartContainer";
import { ModelPillFilter } from "@/components/charts/ModelPillFilter";
import { EnsembleMeanTemperatureChart } from "@/components/model-guidance/EnsembleMeanTemperatureChart";
import { EnsembleMeanPrecipChart } from "@/components/model-guidance/EnsembleMeanPrecipChart";
import { EnsemblePercentileBandChart } from "@/components/model-guidance/EnsemblePercentileBandChart";
import { EnsemblePrecipPlumeChart } from "@/components/model-guidance/EnsemblePrecipPlumeChart";
import { EnsembleProbabilityChart } from "@/components/model-guidance/EnsembleProbabilityChart";
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
  ENSEMBLE_MEAN_VARIABLES,
  ENSEMBLES_TAB_VARIABLES,
  ENSEMBLE_STATS_CHARTS,
  ENSEMBLE_STATS_PERCENTILES,
  MEMBER_PLUME_MODELS,
  ensemblePercentileVarId,
  ensembleProbabilityRequestVariables,
  modelShortName,
  resolveEnsembleStatsRun,
} from "@/lib/chart-constants";
import { eligibleEnsembleModels } from "@/lib/eligible-ensemble-models";
import { useEntitlements } from "@/lib/entitlements";
import { buildRunOptions, formatRunLabel, sortRunIdsDescending } from "@/lib/run-options";

type Props = {
  lat: number;
  lon: number;
  timezone: string | null;
  /** Location line passed to the chart cards' image export. */
  locationText: string;
};

type EnsembleVariable = (typeof ENSEMBLES_TAB_VARIABLES)[number];

/** "means" = multi-model mean comparison; a model id = that model's members. */
type EnsembleView = "means" | (typeof MEMBER_PLUME_MODELS)[number];

const VARIABLE_LABELS: Record<EnsembleVariable, string> = {
  tmp2m: "Temperature",
  tmp850: "850 mb Temperature",
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
export function EnsemblesTabContent({ lat, lon, timezone, locationText }: Props) {
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
  const memberModel = view === "means" ? null : view;
  const selectableVariables =
    view === "means" ? ENSEMBLE_MEAN_VARIABLES : ENSEMBLES_TAB_VARIABLES;
  const varParam = searchParams.get("ensemble_var");
  const variable: EnsembleVariable = (selectableVariables as readonly string[]).includes(
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

  const handleViewChange = useCallback(
    (nextView: string) => {
      const next = new URLSearchParams(searchParams);
      if (nextView === "means") {
        next.delete("ensemble_view");
        if (!(ENSEMBLE_MEAN_VARIABLES as readonly string[]).includes(variable)) {
          next.delete("ensemble_var");
        }
      } else {
        next.set("ensemble_view", nextView);
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams, variable],
  );

  const variableOptions = useMemo(
    () =>
      selectableVariables.map((value) => ({
        value,
        label: VARIABLE_LABELS[value],
      })),
    [selectableVariables],
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
    variables: [...ENSEMBLE_MEAN_VARIABLES],
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
    variables: [...ENSEMBLE_MEAN_VARIABLES],
    pinnedRuns: plumePinnedRuns,
    includeMembers: true,
    enabled: plumeModels.length > 0,
  });

  // tmp850 is member-only for now and fetched lazily for the selected model.
  // Keeping it out of the always-warmed two-model member request avoids a
  // roughly 50% payload/sampling increase on every Ensembles-tab visit.
  const tmp850PinnedRuns = useMemo(
    () =>
      memberModel && pinnedRuns[memberModel]
        ? { [memberModel]: pinnedRuns[memberModel] }
        : {},
    [memberModel, pinnedRuns],
  );
  const {
    data: tmp850MemberData,
    loading: tmp850MembersLoading,
    error: tmp850MembersError,
    reload: reloadTmp850Members,
  } = useMeteogram({
    lat,
    lon,
    models: memberModel ? [memberModel] : [],
    variables: ["tmp850"],
    pinnedRuns: tmp850PinnedRuns,
    includeMembers: true,
    enabled: Boolean(memberModel && variable === "tmp850"),
  });
  const activeMemberData = variable === "tmp850" ? tmp850MemberData : memberData;
  const activeMembersLoading =
    variable === "tmp850" ? tmp850MembersLoading : membersLoading;
  const activeMembersError =
    variable === "tmp850" ? tmp850MembersError : membersError;
  const reloadActiveMembers =
    variable === "tmp850" ? reloadTmp850Members : reloadMembers;

  // Newest complete run per model — the selector ceiling. The backend reports
  // it directly as `latest_complete_run` (independent of pins), because
  // inferring it from the SERVED run breaks under a pin: a fresh page load
  // with a pinned URL only ever sees responses serving the pin, so a
  // served-run ceiling freezes at the pinned cycle and hides every newer run
  // (observed 2026-07-08 with a stale gefs pin in a shared link). The served
  // run_id remains the max-ratchet FALLBACK for older cached payloads that
  // predate the field.
  const [latestCompleteRun, setLatestCompleteRun] = useState<
    Record<string, string>
  >({});

  useEffect(() => {
    if (!data) return;
    setLatestCompleteRun((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const model of eligibleModels) {
        const entry = data.series?.[model];
        const reported = entry?.latest_complete_run ?? entry?.run_id;
        if (reported && (!next[model] || reported > next[model])) {
          next[model] = reported;
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
    ? activeMemberData?.series?.[memberModel]?.run_id ?? null
    : null;

  // ── Stats charts data (backlog B1 + B2) ─────────────────────────────────
  // Percentile band + probability charts render below the plume when the
  // selected variable has an ENSEMBLE_STATS_CHARTS entry. Temperature keeps
  // its probability products for maps/future visualizations but suppresses
  // the legacy multi-line meteogram and its otherwise-unused requests.
  // Requests are
  // chunked because the meteogram request schema caps `variables` at 6
  // (main.py MeteogramRequest): the band chart's vars (base + 5 percentiles)
  // exactly fit one request, and each enabled probability DIRECTION (<= 6
  // thresholds by config contract) is its own request — precip has 6 gt
  // rungs; tmp2m retains 3 lt + 4 gt rungs for future presentation. All are
  // pinned to the run the MEMBER payload serves so every
  // chart on the page describes the same run: unpinned, "latest
  // members-ready" and "latest stats-ready" can briefly diverge while a
  // fresh run's stats pass finishes (~2 min after members promote). The base
  // variable rides along only for the band chart's mean overlay — an ensemble
  // base series IS the mean. Probability chunks remain probability-only so
  // precip's six thresholds still fit the API's six-variable limit; backend
  // pin validation derives the base anchor without adding it to this request.
  const statsConfig = ENSEMBLE_STATS_CHARTS[variable];
  const hasStatsCharts = Boolean(statsConfig);
  const probabilityChartConfig =
    statsConfig?.showProbabilityChart === false ? undefined : statsConfig;
  const statsRun = memberModel
    ? resolveEnsembleStatsRun(pinnedRuns[memberModel], memberServedRun)
    : null;
  const statsPinnedRuns = useMemo(
    () => (memberModel && statsRun ? { [memberModel]: statsRun } : {}),
    [memberModel, statsRun],
  );
  const statsEnabled = Boolean(memberModel && hasStatsCharts && statsRun);
  const percentileVariables = useMemo(
    () =>
      hasStatsCharts
        ? [
            variable,
            ...ENSEMBLE_STATS_PERCENTILES.map((q) => ensemblePercentileVarId(variable, q)),
          ]
        : [],
    [variable, hasStatsCharts],
  );
  const probGtVariables = useMemo(
    () =>
      probabilityChartConfig
        ? ensembleProbabilityRequestVariables(variable, "gt")
        : [],
    [variable, probabilityChartConfig],
  );
  const probLtVariables = useMemo(
    () =>
      probabilityChartConfig
        ? ensembleProbabilityRequestVariables(variable, "lt")
        : [],
    [variable, probabilityChartConfig],
  );
  const {
    data: percentileData,
    loading: percentileLoading,
    error: percentileError,
    reload: reloadPercentiles,
  } = useMeteogram({
    lat,
    lon,
    models: memberModel ? [memberModel] : [],
    variables: percentileVariables,
    pinnedRuns: statsPinnedRuns,
    enabled: statsEnabled,
  });
  const {
    data: probGtData,
    loading: probGtLoading,
    error: probGtError,
    reload: reloadProbGt,
  } = useMeteogram({
    lat,
    lon,
    models: memberModel ? [memberModel] : [],
    variables: probGtVariables,
    pinnedRuns: statsPinnedRuns,
    enabled: statsEnabled && probGtVariables.length > 0,
  });
  const {
    data: probLtData,
    loading: probLtLoading,
    error: probLtError,
    reload: reloadProbLt,
  } = useMeteogram({
    lat,
    lon,
    models: memberModel ? [memberModel] : [],
    variables: probLtVariables,
    pinnedRuns: statsPinnedRuns,
    enabled: statsEnabled && probLtVariables.length > 0,
  });
  const probLoading =
    (probGtVariables.length > 0 && probGtLoading) ||
    (probLtVariables.length > 0 && probLtLoading);
  const probHasData = Boolean(probGtData || probLtData);
  const probError = probGtError ?? probLtError;
  const reloadProbs = useCallback(() => {
    reloadProbGt();
    reloadProbLt();
  }, [reloadProbGt, reloadProbLt]);

  const statsRunSubtitle = useCallback(
    (served: string | null | undefined) => {
      const runId = served ?? statsRun;
      return runId ? `Run ${formatRunLabel(runId)}` : null;
    },
    [statsRun],
  );

  const statsNoun = VARIABLE_LABELS[variable].toLowerCase();
  const statsBandTitle = memberModel
    ? `${modelShortName(memberModel)} ${statsNoun} percentiles`
    : "";
  const statsBandSubtitle = [
    memberModel ? statsRunSubtitle(percentileData?.series?.[memberModel]?.run_id) : null,
    "Bands span the 10–90th (light) and 25–75th (dark) percentiles · solid white is the median · dashed is the mean",
  ]
    .filter(Boolean)
    .join(" · ");
  const statsProbTitle = memberModel
    ? `${modelShortName(memberModel)} ${statsNoun} probabilities`
    : "";
  const statsProbSubtitle = [
    memberModel
      ? statsRunSubtitle(
          (probGtData ?? probLtData)?.series?.[memberModel]?.run_id,
        )
      : null,
    probabilityChartConfig?.probSubtitle,
  ]
    .filter(Boolean)
    .join(" · ");

  const memberChartTitle = memberModel
    ? `${modelShortName(memberModel)} ${VARIABLE_LABELS[variable].toLowerCase()} members`
    : "";
  // Only mention the control line when the served payload has one — EPS
  // publishes 50 pf members with no upstream control.
  const memberHasControl = memberModel
    ? Boolean(activeMemberData?.series?.[memberModel]?.variables?.[variable]?.members?.control)
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
            onChange={handleViewChange}
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
            exportImage={{
              headerText:
                variable === "tmp2m" ? "Mean temperature" : "Mean cumulative precipitation",
              locationText,
              filenameSlug:
                variable === "tmp2m" ? "ensemble-mean-temperature" : "ensemble-mean-precip",
            }}
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
        <>
          <section id={`ensemble-${memberModel}-members`}>
            <ChartContainer
              title={memberChartTitle}
              subtitle={memberChartSubtitle}
              isLoading={activeMembersLoading && !activeMemberData}
              error={activeMembersError}
              onRetry={reloadActiveMembers}
              exportImage={{
                headerText: memberChartTitle,
                locationText,
                filenameSlug: `${memberModel}-${variable}-members`,
              }}
            >
              {variable !== "precip_total" ? (
                <EnsembleTemperaturePlumeChart
                  response={activeMemberData}
                  model={memberModel!}
                  variable={variable}
                  unitsFallback={variable === "tmp850" ? "C" : "F"}
                  timezone={timezone}
                />
              ) : (
                <EnsemblePrecipPlumeChart
                  response={activeMemberData}
                  model={memberModel!}
                  timezone={timezone}
                />
              )}
            </ChartContainer>
          </section>
          {statsConfig ? (
            <section id={`ensemble-${memberModel}-percentiles`}>
              <ChartContainer
                title={statsBandTitle}
                subtitle={statsBandSubtitle}
                isLoading={percentileLoading && !percentileData}
                error={percentileError}
                onRetry={reloadPercentiles}
                exportImage={{
                  headerText: statsBandTitle,
                  locationText,
                  filenameSlug: `${memberModel}-${variable}-percentiles`,
                }}
              >
                <EnsemblePercentileBandChart
                  response={percentileData}
                  model={memberModel!}
                  variable={variable}
                  unitsFallback={statsConfig.unitsFallback}
                  formatValue={statsConfig.formatValue}
                  timezone={timezone}
                  clampZero={statsConfig.clampZero}
                  emptyMessage="No percentile data available for this run yet."
                />
              </ChartContainer>
            </section>
          ) : null}
          {probabilityChartConfig ? (
            <section id={`ensemble-${memberModel}-probabilities`}>
              <ChartContainer
                title={statsProbTitle}
                subtitle={statsProbSubtitle}
                isLoading={probLoading && !probHasData}
                error={probError}
                onRetry={reloadProbs}
                exportImage={{
                  headerText: statsProbTitle,
                  locationText,
                  filenameSlug: `${memberModel}-${variable}-probabilities`,
                }}
              >
                <EnsembleProbabilityChart
                  gtResponse={probGtData}
                  ltResponse={probLtData}
                  model={memberModel!}
                  variable={variable}
                  expectedRun={statsRun}
                  thresholds={probabilityChartConfig.probThresholds}
                  thresholdUnitSuffix={probabilityChartConfig.thresholdUnitSuffix}
                  timezone={timezone}
                  emptyMessage="No probability data available for this run yet."
                />
              </ChartContainer>
            </section>
          ) : null}
        </>
      )}
    </div>
  );
}
