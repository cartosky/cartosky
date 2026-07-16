import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ClipboardCheck, Clock3, SearchCheck, X } from "lucide-react";

import { AdminEmpty, AdminHero, AdminPage, AdminSurface } from "@/components/admin-shell";
import {
  fetchAdminAuthStatus,
  fetchAdminStatusRunDetail,
  fetchAdminStatusResults,
  formatStatsIncompleteUnitCause,
  type Frames404Summary,
  type StatusResult,
  type TwfStatus,
} from "@/lib/admin-api";
import { formatObservedValidTime, formatRunLabel } from "@/lib/time-axis";

type WindowValue = "24h" | "7d" | "30d";
type ViewFilter = "issues" | "gaps" | "stats" | "ongoing" | "artifacts" | "stale" | "all";
type StatusTone = "pass" | "info" | "warning" | "fail";

const ADMIN_POLL_INTERVAL_MS = 5 * 60 * 1000;

function formatTimestamp(value: number | null | undefined): string {
  if (!value) return "—";
  return new Date(value * 1000).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatPercent(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `${value.toFixed(1)}%`;
}

function formatForecastHour(value: number | null | undefined): string {
  return Number.isFinite(value) ? `fh${String(Math.trunc(Number(value))).padStart(3, "0")}` : "—";
}

function formatForecastHourRange(minValue: number | null | undefined, maxValue: number | null | undefined): string {
  if (!Number.isFinite(minValue) && !Number.isFinite(maxValue)) return "—";
  if (Number.isFinite(minValue) && Number.isFinite(maxValue) && Number(minValue) !== Number(maxValue)) {
    return `${formatForecastHour(minValue)}-${formatForecastHour(maxValue)}`;
  }
  return formatForecastHour(maxValue ?? minValue);
}

function forecastProgressLabel(result: StatusResult): string {
  const latest = formatForecastHourRange(result.latest_forecast_hour_min, result.latest_forecast_hour_max);
  const target = formatForecastHourRange(result.target_forecast_hour_min, result.target_forecast_hour_max);
  if (latest === "—" && target === "—") return "—";
  return `${latest} / ${target}`;
}

function issueTone(result: StatusResult): StatusTone {
  if (result.status === "error") return "fail";
  if (result.status === "warning") return "warning";
  if (result.status === "info") return "info";
  return "pass";
}

function issueLabel(issueType: string): string {
  if (issueType === "artifact_failure") return "Artifact failure";
  if (issueType === "run_stalled") return "Run stalled";
  if (issueType === "run_ongoing") return "Run ongoing";
  if (issueType === "run_incomplete") return "Run incomplete";
  if (issueType === "accum_step_gap") return "Accumulation step gap";
  if (issueType === "stats_incomplete") return "Stats processing incomplete";
  if (issueType === "stale_run") return "Stale latest run";
  if (issueType === "bundle_unavailable") return "Bundle unavailable";
  if (issueType === "bundle_stalled") return "Bundle stalled";
  if (issueType === "stale_bundle") return "Stale bundle";
  if (issueType === "delayed_bundle") return "Delayed bundle";
  if (issueType === "manifest_missing") return "Missing manifest";
  if (issueType === "manifest_invalid") return "Invalid manifest";
  return "Healthy";
}

function freshnessTone(state: string | null | undefined): StatusTone {
  if (state === "live") return "pass";
  if (state === "delayed") return "warning";
  if (state === "stale" || state === "unavailable") return "fail";
  return "pass";
}

function StatusBadge(props: { tone: StatusTone; label: string }) {
  const className =
    props.tone === "pass"
      ? "border-emerald-400/25 bg-emerald-500/12 text-emerald-100"
      : props.tone === "info"
        ? "border-sky-400/25 bg-sky-500/12 text-sky-100"
      : props.tone === "warning"
        ? "border-amber-400/25 bg-amber-500/12 text-amber-100"
        : "border-rose-400/25 bg-rose-500/12 text-rose-100";
  return <span className={`inline-flex rounded-full border px-3 py-1 text-[11px] font-medium uppercase tracking-[0.18em] ${className}`}>{props.label}</span>;
}

function SummaryCard(props: {
  title: string;
  value: number;
  accent: string;
  icon: typeof ClipboardCheck;
  hint?: string;
  onClick?: () => void;
  active?: boolean;
}) {
  const muted = props.value === 0;
  const Icon = props.icon;
  return (
    <section
      className={[
        "rounded-[1.15rem] border p-4 shadow-[0_12px_30px_rgba(0,0,0,0.18)]",
        props.onClick ? "cursor-pointer transition-colors hover:bg-white/[0.03]" : "",
        muted ? "border-white/8 bg-white/[0.02]" : "border-white/10 bg-white/[0.03]",
        props.active ? "ring-1 ring-cyan-300/30" : "",
      ].join(" ")}
      onClick={props.onClick}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className={`text-sm font-semibold ${muted ? "text-white/72" : "text-white"}`}>{props.title}</div>
          <div className={`mt-3 text-[2.1rem] font-semibold tracking-tight ${muted ? "text-white/68" : props.accent}`}>{props.value}</div>
          {props.hint ? <div className="mt-2 text-xs uppercase tracking-[0.18em] text-white/38">{props.hint}</div> : null}
        </div>
        <div className={`rounded-2xl border p-3 ${muted ? "border-white/8 bg-white/[0.025]" : "border-white/10 bg-white/[0.05]"}`}>
          <Icon className={`h-5 w-5 ${muted ? "text-white/52" : props.accent}`} />
        </div>
      </div>
    </section>
  );
}

function CompactMetric(props: {
  label: string;
  value: string | number;
  hint?: string;
  accentClassName?: string;
  active?: boolean;
  onClick?: () => void;
}) {
  const content = (
    <div
      className={[
        "border-l pl-4 transition-colors",
        props.active ? "border-cyan-300/40 bg-cyan-400/[0.03]" : "border-white/10",
        props.onClick ? "cursor-pointer hover:border-white/20 hover:bg-white/[0.02]" : "",
      ].join(" ")}
    >
      <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">{props.label}</div>
      <div className={`mt-2 text-[1.6rem] font-semibold tracking-tight ${props.accentClassName ?? "text-white"}`}>{props.value}</div>
      {props.hint ? <div className="mt-2 text-sm leading-6 text-white/58">{props.hint}</div> : null}
    </div>
  );

  if (!props.onClick) return content;
  return (
    <button type="button" onClick={props.onClick} className="w-full text-left">
      {content}
    </button>
  );
}

function filterRows(rows: StatusResult[], view: ViewFilter): StatusResult[] {
  if (view === "all") return rows;
  if (view === "issues") return rows.filter((row) => row.status === "warning" || row.status === "error");
  if (view === "gaps") return rows.filter((row) => (row.accum_step_gap_variable_count ?? 0) > 0);
  if (view === "stats") return rows.filter((row) => (row.stats_incomplete_alert_count ?? 0) > 0);
  if (view === "ongoing") return rows.filter((row) => row.issue_type === "run_ongoing");
  if (view === "artifacts") return rows.filter((row) => row.issue_type === "artifact_failure" || row.issue_type === "manifest_missing" || row.issue_type === "manifest_invalid");
  return rows.filter((row) => (
    row.issue_type === "stale_run"
    || row.issue_type === "run_stalled"
    || row.issue_type === "bundle_unavailable"
    || row.issue_type === "bundle_stalled"
    || row.issue_type === "stale_bundle"
    || row.issue_type === "delayed_bundle"
  ));
}

function viewLabel(view: ViewFilter): string {
  if (view === "issues") return "Open pipeline issues";
  if (view === "gaps") return "Accumulation step gaps";
  if (view === "stats") return "Ensemble stats alerts";
  if (view === "ongoing") return "Ongoing runs";
  if (view === "artifacts") return "Artifact and manifest failures";
  if (view === "stale") return "Stale or stalled runs";
  return "All retained runs";
}

function formatIsoTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatSecondsSincePublish(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${Number(value).toFixed(value < 10 ? 2 : 1)}s`;
}

function RecencyBuckets(props: { buckets?: { lt1s: number; lt5s: number; gte5s: number } }) {
  const buckets = props.buckets ?? { lt1s: 0, lt5s: 0, gte5s: 0 };
  return (
    <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-white/58">
      <span className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5">&lt;1s {buckets.lt1s}</span>
      <span className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5">&lt;5s {buckets.lt5s}</span>
      <span className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5">≥5s {buckets.gte5s}</span>
    </div>
  );
}

function Frames404Panel(props: { summary: Frames404Summary | null }) {
  const summary = props.summary;
  if (!summary) return null;
  const totals = summary.totals_by_reason ?? {};
  const reasonTotal = (reason: string) => totals[reason] ?? 0;
  const contextReasons: Array<[string, string]> = [
    ["stale_run", "Stale run (2.2)"],
    ["not_published", "Not published"],
    ["not_supported", "Not supported"],
    ["size_mismatch", "Size mismatch"],
    ["manifest_missing", "Manifest missing"],
  ];
  const recent = summary.recent ?? [];
  return (
    <AdminSurface
      className="mt-4 p-4"
      title="Frame 404 telemetry"
      headerRight={
        <div className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-medium text-white/60">
          since {formatIsoTimestamp(summary.since)}
        </div>
      }
    >
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-2xl border border-amber-400/18 bg-amber-500/[0.06] px-4 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-amber-100/72">Swap gap (2.1)</div>
          <div className="mt-2 text-[1.6rem] font-semibold tracking-tight text-amber-200">{reasonTotal("swap_gap")}</div>
          <RecencyBuckets buckets={summary.recency_buckets?.swap_gap} />
        </div>
        <div className="rounded-2xl border border-amber-400/18 bg-amber-500/[0.06] px-4 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-amber-100/72">Manifest skew (2.1)</div>
          <div className="mt-2 text-[1.6rem] font-semibold tracking-tight text-amber-200">{reasonTotal("manifest_skew")}</div>
          <RecencyBuckets buckets={summary.recency_buckets?.manifest_skew} />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-3">
        {contextReasons.map(([reason, label]) => (
          <div key={reason} className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2">
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-white/44">{label}</div>
            <div className="mt-1 text-lg font-semibold text-white/82">{reasonTotal(reason)}</div>
          </div>
        ))}
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-max min-w-[720px] border-separate border-spacing-y-2 text-left text-sm">
          <thead className="text-white/48">
            <tr>
              <th className="px-3 py-2 font-medium">Time</th>
              <th className="px-3 py-2 font-medium">Endpoint</th>
              <th className="px-3 py-2 font-medium">Model / Run / Var</th>
              <th className="px-3 py-2 font-medium">Reason</th>
              <th className="px-3 py-2 font-medium">s-since-publish</th>
            </tr>
          </thead>
          <tbody>
            {recent.length === 0 ? (
              <tr>
                <td colSpan={5} className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-6 text-center text-white/48">
                  No frame 404s recorded yet.
                </td>
              </tr>
            ) : (
              recent.map((sample, index) => (
                <tr key={`${sample.ts_iso}-${index}`} className="bg-white/[0.03] text-white/82">
                  <td className="rounded-l-2xl border-y border-l border-white/10 px-3 py-2 text-white/62">{formatIsoTimestamp(sample.ts_iso)}</td>
                  <td className="border-y border-white/10 px-3 py-2">{sample.endpoint}</td>
                  <td className="border-y border-white/10 px-3 py-2 text-white/70">
                    {[sample.model, sample.run_resolved ?? sample.run_requested, sample.var].filter(Boolean).join(" / ") || "—"}
                  </td>
                  <td className="border-y border-white/10 px-3 py-2">
                    <StatusBadge
                      tone={sample.reason === "swap_gap" || sample.reason === "manifest_skew" ? "warning" : "info"}
                      label={sample.reason}
                    />
                  </td>
                  <td className="rounded-r-2xl border-y border-r border-white/10 px-3 py-2 text-white/62">{formatSecondsSincePublish(sample.seconds_since_publish)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </AdminSurface>
  );
}

export default function AdminStatusPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [windowValue, setWindowValue] = useState<WindowValue>("30d");
  const [modelFilter, setModelFilter] = useState<string>("all");
  const [viewFilter, setViewFilter] = useState<ViewFilter>("issues");
  const [results, setResults] = useState<StatusResult[]>([]);
  const [frames404, setFrames404] = useState<Frames404Summary | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<StatusResult | null>(null);
  const [selectedDetailLoading, setSelectedDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const topScrollRef = useRef<HTMLDivElement | null>(null);
  const tableScrollRef = useRef<HTMLDivElement | null>(null);
  const [tableScrollWidth, setTableScrollWidth] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let loading = false;

    async function load() {
      if (loading) return;
      loading = true;

      try {
        const authStatus = await fetchAdminAuthStatus();
        if (cancelled) return;
        setStatus(authStatus);
        if (!authStatus.linked || !authStatus.admin) return;

        const response = await fetchAdminStatusResults({
          window: windowValue,
          model: modelFilter,
          limit: 200,
          includeDetails: false,
        });
        if (cancelled) return;
        setResults(response.results);
        setFrames404(response.frames_404 ?? null);
        setError(null);
      } catch (nextError) {
        if (cancelled) return;
        setError(nextError instanceof Error ? nextError.message : "Failed to load pipeline status");
      } finally {
        loading = false;
      }
    }

    void load();
    const intervalId = window.setInterval(() => {
      void load();
    }, ADMIN_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [windowValue, modelFilter]);

  const filteredRows = useMemo(() => filterRows(results, viewFilter), [results, viewFilter]);
  const selectedSummary = filteredRows.find((item) => item.id === selectedId) ?? results.find((item) => item.id === selectedId) ?? null;
  const selected = selectedDetail && selectedDetail.id === selectedId ? selectedDetail : selectedSummary;

  useEffect(() => {
    if (selectedId !== null && !results.some((item) => item.id === selectedId)) {
      setSelectedId(null);
      setSelectedDetail(null);
    }
  }, [results, selectedId]);

  useEffect(() => {
    let cancelled = false;
    let loading = false;

    if (!selectedSummary) {
      setSelectedDetail(null);
      setSelectedDetailLoading(false);
      return;
    }

    const summary = selectedSummary;

    async function loadDetail() {
      if (loading) return;
      loading = true;
      setSelectedDetailLoading(true);

      try {
        const response = await fetchAdminStatusRunDetail({
          model: summary.model_id,
          run: summary.run_id,
        });
        if (cancelled) return;
        setSelectedDetail(response.result);
      } catch {
        if (cancelled) return;
        setSelectedDetail(null);
      } finally {
        if (!cancelled) {
          setSelectedDetailLoading(false);
        }
        loading = false;
      }
    }

    void loadDetail();
    const intervalId = window.setInterval(() => {
      void loadDetail();
    }, ADMIN_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [
    selectedSummary?.id,
    selectedSummary?.model_id,
    selectedSummary?.run_id,
    selectedSummary?.last_updated_at,
  ]);

  useEffect(() => {
    function updateScrollWidth() {
      if (!tableScrollRef.current) return;
      setTableScrollWidth(tableScrollRef.current.scrollWidth);
    }
    updateScrollWidth();
    window.addEventListener("resize", updateScrollWidth);
    return () => window.removeEventListener("resize", updateScrollWidth);
  }, [filteredRows]);

  function syncScroll(source: "top" | "table") {
    if (!topScrollRef.current || !tableScrollRef.current) return;
    if (source === "top") {
      tableScrollRef.current.scrollLeft = topScrollRef.current.scrollLeft;
    } else {
      topScrollRef.current.scrollLeft = tableScrollRef.current.scrollLeft;
    }
  }

  const modelOptions = Array.from(new Set(results.map((item) => item.model_id))).sort();
  const issueRows = results.filter((row) => row.status === "warning" || row.status === "error");
  const gapRows = results.filter((row) => (row.accum_step_gap_variable_count ?? 0) > 0);
  const statsRows = results.filter((row) => (row.stats_incomplete_alert_count ?? 0) > 0);
  const ongoingRows = results.filter((row) => row.issue_type === "run_ongoing");
  const artifactRows = results.filter((row) => row.issue_type === "artifact_failure" || row.issue_type === "manifest_missing" || row.issue_type === "manifest_invalid");
  const staleRows = results.filter((row) => (
    row.issue_type === "stale_run"
    || row.issue_type === "run_stalled"
    || row.issue_type === "bundle_unavailable"
    || row.issue_type === "bundle_stalled"
    || row.issue_type === "stale_bundle"
    || row.issue_type === "delayed_bundle"
  ));
  const healthyRows = results.filter((row) => row.status === "healthy");
  const emptyStateMessage =
    results.length === 0
      ? "No retained published runs were found for the current window."
      : viewFilter === "issues"
        ? "No operational issues were found in the retained published runs."
        : viewFilter === "gaps"
          ? "No cumulative accumulation step gaps were found."
        : viewFilter === "stats"
          ? "No persistent ensemble stats roster gaps were found."
        : viewFilter === "ongoing"
          ? "No retained latest runs are currently building."
        : viewFilter === "artifacts"
          ? "No artifact or manifest failures were found."
          : viewFilter === "stale"
            ? "No stale or stalled latest runs were found."
            : "No rows match the current filters.";

  if (!status?.linked || !status.admin) {
    return (
      <AdminEmpty>
        Admin pipeline status appears here after admin access is available.
      </AdminEmpty>
    );
  }

  return (
    <AdminPage>
      <AdminHero
        eyebrow="Pipeline Status"
        title="Retained run health"
      >
        {error ? (
          <div className="mt-4 rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        ) : null}

        <div className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-7">
            <CompactMetric
              label="Retained runs"
              value={results.length}
              active={viewFilter === "all"}
              onClick={() => setViewFilter("all")}
            />
            <CompactMetric
              label="Open issues"
              value={issueRows.length}
              accentClassName="text-amber-300"
              active={viewFilter === "issues"}
              onClick={() => setViewFilter("issues")}
            />
            <CompactMetric
              label="Accum gaps"
              value={gapRows.length}
              accentClassName="text-amber-300"
              active={viewFilter === "gaps"}
              onClick={() => setViewFilter("gaps")}
            />
            <CompactMetric
              label="Stats alerts"
              value={statsRows.length}
              accentClassName="text-amber-300"
              active={viewFilter === "stats"}
              onClick={() => setViewFilter("stats")}
            />
            <CompactMetric
              label="Ongoing runs"
              value={ongoingRows.length}
              accentClassName="text-sky-300"
              active={viewFilter === "ongoing"}
              onClick={() => setViewFilter("ongoing")}
            />
            <CompactMetric
              label="Artifact failures"
              value={artifactRows.length}
              accentClassName="text-rose-300"
              active={viewFilter === "artifacts"}
              onClick={() => setViewFilter("artifacts")}
            />
            <CompactMetric
              label="Stale or stalled"
              value={staleRows.length}
              accentClassName="text-amber-300"
              active={viewFilter === "stale"}
              onClick={() => setViewFilter("stale")}
            />
          </div>

        </div>

        <div className="mt-6 grid gap-3 md:grid-cols-3">
          <label className="space-y-2 text-sm">
            <span className="text-white/62">Window</span>
            <select
              value={windowValue}
              onChange={(event) => setWindowValue(event.target.value as WindowValue)}
              className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white outline-none"
            >
              <option value="24h">24 hours</option>
              <option value="7d">7 days</option>
              <option value="30d">30 days</option>
            </select>
          </label>
          <label className="space-y-2 text-sm">
            <span className="text-white/62">Model</span>
            <select
              value={modelFilter}
              onChange={(event) => setModelFilter(event.target.value)}
              className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white outline-none"
            >
              <option value="all">All models</option>
              {modelOptions.map((modelId) => (
                <option key={modelId} value={modelId}>
                  {modelId}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-2 text-sm">
            <span className="text-white/62">View</span>
            <select
              value={viewFilter}
              onChange={(event) => setViewFilter(event.target.value as ViewFilter)}
              className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white outline-none"
            >
              <option value="issues">Open issues</option>
              <option value="gaps">Accumulation step gaps</option>
              <option value="stats">Ensemble stats alerts</option>
              <option value="ongoing">Ongoing runs</option>
              <option value="artifacts">Artifact failures</option>
              <option value="stale">Stale or stalled</option>
              <option value="all">All retained runs</option>
            </select>
          </label>
        </div>
      </AdminHero>

      <AdminSurface className="p-4" title="Current View" headerRight={<div className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-medium text-white/60">{filteredRows.length} rows</div>}>
        <div ref={topScrollRef} onScroll={() => syncScroll("top")} className="mb-3 overflow-x-auto">
          <div className="h-2 rounded-full bg-white/[0.04]" style={{ width: tableScrollWidth > 0 ? `${tableScrollWidth}px` : "100%" }} />
        </div>

        <div ref={tableScrollRef} onScroll={() => syncScroll("table")} className="overflow-x-auto pb-2">
          <table className="w-max min-w-[1540px] border-separate border-spacing-y-2 text-left text-sm">
            <thead className="text-white/48">
              <tr>
                <th className="px-3 py-2 font-medium">Model</th>
                <th className="px-3 py-2 font-medium">Run</th>
                <th className="px-3 py-2 font-medium">Freshness</th>
                <th className="px-3 py-2 font-medium">Latest Scan</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Issue type</th>
                <th className="px-3 py-2 font-medium">Summary</th>
                <th className="px-3 py-2 font-medium">Forecast Hour</th>
                <th className="px-3 py-2 font-medium">Frames</th>
                <th className="px-3 py-2 font-medium">Completion</th>
                <th className="px-3 py-2 font-medium">Build Age</th>
                <th className="px-3 py-2 font-medium">Updated</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.length === 0 ? (
                <tr>
                  <td colSpan={12} className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-8 text-center text-white/48">
                    {emptyStateMessage}
                  </td>
                </tr>
              ) : (
                filteredRows.map((item) => (
                  <tr
                    key={item.id}
                    onClick={() => setSelectedId(item.id)}
                    className={[
                      "cursor-pointer rounded-2xl border transition-colors",
                      item.id === selectedId
                        ? "bg-emerald-500/10 text-white"
                        : item.status === "error"
                        ? "border-rose-400/15 bg-rose-500/[0.06] text-white/84 hover:bg-rose-500/[0.1]"
                        : item.status === "warning"
                          ? "border-amber-400/15 bg-amber-500/[0.05] text-white/84 hover:bg-amber-500/[0.08]"
                          : item.status === "info"
                            ? "border-sky-400/15 bg-sky-500/[0.05] text-white/84 hover:bg-sky-500/[0.08]"
                            : "bg-white/[0.03] text-white/84 hover:bg-white/[0.05]",
                    ].join(" ")}
                  >
                    <td className="rounded-l-2xl border-y border-l border-white/10 px-3 py-3 font-semibold">{item.model_id}</td>
                    <td className="border-y border-white/10 px-3 py-3">{formatRunLabel(item.run_id)}</td>
                    <td className="border-y border-white/10 px-3 py-3">
                      {item.time_axis_mode === "observed" && item.freshness_state ? (
                        <StatusBadge tone={freshnessTone(item.freshness_state)} label={item.freshness_state} />
                      ) : (
                        <span className="text-white/40">—</span>
                      )}
                    </td>
                    <td className="border-y border-white/10 px-3 py-3 text-white/68">
                      {item.time_axis_mode === "observed"
                        ? (formatObservedValidTime(item.latest_scan_valid_time ?? null) ?? "—")
                        : "—"}
                    </td>
                    <td className="border-y border-white/10 px-3 py-3">
                      <StatusBadge tone={issueTone(item)} label={item.status} />
                    </td>
                    <td className="border-y border-white/10 px-3 py-3">
                      <StatusBadge tone={issueTone(item)} label={issueLabel(item.issue_type)} />
                    </td>
                    <td className="max-w-[420px] border-y border-white/10 px-3 py-3 text-white/68">
                      <div className="line-clamp-2">{item.summary}</div>
                    </td>
                    <td className="border-y border-white/10 px-3 py-3 font-medium text-white/76">{forecastProgressLabel(item)}</td>
                    <td className="border-y border-white/10 px-3 py-3">{item.available_frames}/{item.expected_frames}</td>
                    <td className="border-y border-white/10 px-3 py-3">{formatPercent(item.completion_pct)}</td>
                    <td className="border-y border-white/10 px-3 py-3">{item.run_age_hours.toFixed(1)}h</td>
                    <td className="rounded-r-2xl border-y border-r border-white/10 px-3 py-3 text-white/58">{formatTimestamp(item.last_updated_at)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </AdminSurface>

      <Frames404Panel summary={frames404} />

      {selected ? (
        <>
          <button type="button" aria-label="Close status details" className="fixed inset-0 z-30 bg-black/45 backdrop-blur-[2px]" onClick={() => setSelectedId(null)} />
          <section className="fixed inset-y-4 right-4 z-40 w-[min(540px,calc(100vw-2rem))] overflow-y-auto rounded-[1.75rem] border border-white/10 bg-[#081120]/96 p-5 text-white shadow-[0_24px_80px_rgba(0,0,0,0.5)] backdrop-blur-xl">
            <div className="space-y-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.26em] text-[#95b1a2]">Run Details</div>
                  <h2 className="mt-2 text-2xl font-semibold tracking-tight">
                    {selected.model_id} · {selected.run_id}
                  </h2>
                  <p className="mt-1 text-sm text-white/58">
                    {selected.latest_for_model ? "Latest retained run" : "Retained historical run"} · updated {formatTimestamp(selected.last_updated_at)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedId(null)}
                  className="rounded-full border border-white/10 bg-white/[0.04] p-2 text-white/72 transition hover:bg-white/[0.08] hover:text-white"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="grid gap-4 border-t border-white/8 pt-5 sm:grid-cols-2">
                <div className="border-l border-white/10 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Status</div>
                  <div className="mt-3"><StatusBadge tone={issueTone(selected)} label={selected.status} /></div>
                </div>
                <div className="border-l border-white/10 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Issue type</div>
                  <div className="mt-3"><StatusBadge tone={issueTone(selected)} label={issueLabel(selected.issue_type)} /></div>
                </div>
              </div>

              {selected.time_axis_mode === "observed" ? (
                <div className="grid gap-4 border-t border-white/8 pt-5 sm:grid-cols-3">
                  <div className="border-l border-white/10 pl-4">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Freshness</div>
                    <div className="mt-3">
                      <StatusBadge
                        tone={freshnessTone(selected.freshness_state)}
                        label={selected.freshness_state ?? "unknown"}
                      />
                    </div>
                  </div>
                  <div className="border-l border-white/10 pl-4">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Latest scan</div>
                    <div className="mt-2 text-sm leading-6 text-white">
                      {formatObservedValidTime(selected.latest_scan_valid_time ?? null) ?? "—"}
                    </div>
                    <div className="mt-1 text-sm text-white/60">
                      {Number.isFinite(selected.latest_scan_age_minutes)
                        ? `${selected.latest_scan_age_minutes} minutes old`
                        : "Age unavailable"}
                    </div>
                  </div>
                  <div className="border-l border-white/10 pl-4">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Bundle publish</div>
                    <div className="mt-2 text-sm leading-6 text-white">
                      {formatObservedValidTime(selected.bundle_published_at ?? null) ?? "—"}
                    </div>
                    <div className="mt-1 text-sm text-white/60">
                      {Number.isFinite(selected.observation_to_publish_latency_seconds)
                        ? `${Math.round((selected.observation_to_publish_latency_seconds ?? 0) / 60)} min obs-to-publish`
                        : "Latency unavailable"}
                    </div>
                  </div>
                </div>
              ) : null}

              <div className="border-t border-white/8 pt-5">
                <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Summary</div>
                <div className="mt-3 text-sm leading-6 text-white/78">{selected.summary}</div>
              </div>

              {(selected.stats_incomplete_units ?? []).length > 0 ? (
                <div className="border-t border-amber-400/18 pt-5">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-amber-100/72">Persistent stats issues</div>
                  <div className="mt-3 space-y-3">
                    {(selected.stats_incomplete_units ?? []).map((unit) => (
                      <div key={`${unit.base_var}-${unit.forecast_hour}`} className="rounded-xl border border-amber-400/15 bg-amber-500/[0.06] px-4 py-3 text-sm">
                        <div className="font-medium text-amber-50">
                          {unit.base_var} · {formatForecastHour(unit.forecast_hour)} · {unit.consecutive_passes} passes
                        </div>
                        <div className="mt-1 text-amber-100/68">
                          {formatStatsIncompleteUnitCause(unit)}
                        </div>
                        <div className="mt-1 text-xs text-white/44">
                          First seen {formatTimestamp(unit.first_seen_at)} · last seen {formatTimestamp(unit.last_seen_at)}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {(selected.accum_step_gap_samples ?? []).length > 0 ? (
                <div className="border-t border-amber-400/18 pt-5">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-amber-100/72">Cumulative accumulation step gaps</div>
                  <div className="mt-2 text-sm text-amber-100/68">
                    {selected.accum_step_gap_variable_count ?? selected.accum_step_gap_samples?.length ?? 0} affected variable(s)
                    {Number.isFinite(selected.accum_step_gap_max_affected_pixel_percentage)
                      ? ` · up to ${formatPercent(selected.accum_step_gap_max_affected_pixel_percentage ?? 0)} of defined pixels`
                      : ""}
                  </div>
                  <div className="mt-3 space-y-3">
                    {(selected.accum_step_gap_samples ?? []).map((sample) => (
                      <div key={`${sample.variable_id}-${sample.forecast_hour}`} className="rounded-xl border border-amber-400/15 bg-amber-500/[0.06] px-4 py-3 text-sm">
                        <div className="font-medium text-amber-50">
                          {sample.variable_id} · {formatForecastHour(sample.forecast_hour)}
                        </div>
                        <div className="mt-1 text-amber-100/68">
                          {formatPercent(sample.affected_pixel_percentage)} of defined pixels had one or more missing accumulation steps
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              <div className="grid gap-4 border-t border-white/8 pt-5 sm:grid-cols-3">
                <div className="border-l border-white/10 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Forecast hour</div>
                  <div className="mt-2 text-2xl font-semibold text-white">{forecastProgressLabel(selected)}</div>
                  <div className="mt-1 text-sm text-white/60">Latest built / target</div>
                </div>
                <div className="border-l border-white/10 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Frames</div>
                  <div className="mt-2 text-2xl font-semibold text-white">{selected.available_frames}/{selected.expected_frames}</div>
                  <div className="mt-1 text-sm text-white/60">{formatPercent(selected.completion_pct)} complete</div>
                </div>
                <div className="border-l border-white/10 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Build age</div>
                  <div className="mt-2 text-2xl font-semibold text-white">{selected.run_age_hours.toFixed(1)}h</div>
                  <div className="mt-1 text-sm text-white/60">
                    {selected.latest_for_model && selected.available_frames < selected.expected_frames
                      ? "Since first artifact"
                      : "First artifact to last update"}
                  </div>
                </div>
              </div>

              {(selected.variable_forecast_progress ?? []).length > 0 ? (
                <div className="border-t border-white/8 pt-5">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Variable forecast progress</div>
                  <div className="mt-3 overflow-x-auto">
                    <table className="w-full min-w-[460px] text-left text-sm">
                      <thead className="text-white/44">
                        <tr>
                          <th className="border-b border-white/8 px-3 py-2 font-medium">Variable</th>
                          <th className="border-b border-white/8 px-3 py-2 font-medium">Forecast hour</th>
                          <th className="border-b border-white/8 px-3 py-2 font-medium">Frames</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(selected.variable_forecast_progress ?? []).map((variable) => (
                          <tr key={variable.variable_id}>
                            <td className="border-b border-white/6 px-3 py-2 text-white/78">{variable.display_name || variable.variable_id}</td>
                            <td className="border-b border-white/6 px-3 py-2 font-medium text-white">
                              {formatForecastHour(variable.latest_forecast_hour)} / {formatForecastHour(variable.target_forecast_hour)}
                            </td>
                            <td className="border-b border-white/6 px-3 py-2 text-white/68">{variable.available_frames}/{variable.expected_frames}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : null}

              <div className="grid gap-4 border-t border-white/8 pt-5 sm:grid-cols-3">
                <div className="border-l border-rose-400/22 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-rose-100/72">Missing artifacts</div>
                  <div className="mt-2 text-2xl font-semibold text-rose-100">{selected.missing_artifact_count}</div>
                </div>
                <div className="border-l border-rose-400/22 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-rose-100/72">Unreadable artifacts</div>
                  <div className="mt-2 text-2xl font-semibold text-rose-100">{selected.unreadable_artifact_count}</div>
                </div>
                <div className="border-l border-amber-400/22 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-amber-100/72">Incomplete vars</div>
                  <div className="mt-2 text-2xl font-semibold text-amber-100">{selected.incomplete_variable_count}</div>
                </div>
              </div>

              {selected.incomplete_variables.length > 0 ? (
                <div className="border-t border-white/8 pt-5">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Incomplete variables</div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {selected.incomplete_variables.map((variableId) => (
                      <StatusBadge key={variableId} tone="warning" label={variableId} />
                    ))}
                  </div>
                </div>
              ) : null}

              {selectedDetailLoading ? (
                <div className="border-t border-white/8 pt-5 text-sm text-white/56">
                  Loading run diagnostics...
                </div>
              ) : null}

              {!selectedDetailLoading && selected.sample_paths.length > 0 ? (
                <div className="border-t border-white/8 pt-5">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Sample failing paths</div>
                  <div className="mt-3 space-y-3 text-sm text-white/78">
                    {selected.sample_paths.map((sample, index) => (
                      <div key={`${sample.variable_id}-${sample.forecast_hour}-${index}`} className="border-l border-white/10 pl-4">
                        <div className="font-medium text-white">
                          {sample.variable_id} · f{sample.forecast_hour} · {sample.issue}
                        </div>
                        {sample.value_grid_path ? <div className="mt-1 break-all text-white/60">{sample.value_grid_path}</div> : null}
                        {sample.sidecar_path ? <div className="mt-1 break-all text-white/60">{sample.sidecar_path}</div> : null}
                        {sample.read_error ? <div className="mt-1 text-rose-100/78">Read error: {sample.read_error}</div> : null}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </section>
        </>
      ) : null}
    </AdminPage>
  );
}
