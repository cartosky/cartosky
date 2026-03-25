import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, BarChart3, ClipboardCheck, Database, Gauge, Waypoints } from "lucide-react";
import { Link } from "react-router-dom";

import {
  fetchAdminObservabilitySummary,
  fetchAdminOverviewSummary,
  fetchAdminStatusResults,
  fetchAdminStatusQaSummary,
  fetchAdminTracesSummary,
  fetchTwfStatus,
  type AdminObservabilitySummaryResponse,
  type AdminOverviewSummaryResponse,
  type AdminTracesSummaryResponse,
  type OverviewMetricSummary,
  type StatusResult,
  type StatusQaSummaryResponse,
  type TwfStatus,
} from "@/lib/admin-api";
import { isPostHogEnabled, isPostHogReplayEnabled } from "@/lib/config";

function AdminGate(props: {
  status: TwfStatus | null;
  children: React.ReactNode;
  loadingLabel: string;
}) {
  const { status, children, loadingLabel } = props;

  if (status === null) {
    return (
      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        {loadingLabel}
      </section>
    );
  }

  if (!status.linked || !status.admin) {
    return (
      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        Admin access appears here after a linked admin session is available.
      </section>
    );
  }

  return <>{children}</>;
}

function SummaryCard(props: {
  title: string;
  value: string;
  hint: string;
  icon: typeof Gauge;
  accentClassName?: string;
  description?: string;
  statusLabel?: string;
  statusClassName?: string;
}) {
  const {
    title,
    value,
    hint,
    icon: Icon,
    accentClassName = "text-white",
    description,
    statusLabel,
    statusClassName = "border-white/10 bg-white/[0.05] text-white/72",
  } = props;
  return (
    <section className="rounded-[24px] border border-white/12 bg-black/28 p-5 shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-sm font-semibold text-white">{title}</div>
            {statusLabel ? (
              <div className={`rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] ${statusClassName}`}>
                {statusLabel}
              </div>
            ) : null}
          </div>
          <div className={`mt-3 text-[2.1rem] font-semibold tracking-tight ${accentClassName}`}>{value}</div>
          <div className="mt-2 text-xs uppercase tracking-[0.18em] text-white/40">{hint}</div>
          {description ? <p className="mt-3 max-w-xs text-sm leading-6 text-white/62">{description}</p> : null}
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/[0.05] p-3">
          <Icon className={`h-5 w-5 ${accentClassName}`} />
        </div>
      </div>
    </section>
  );
}

function formatMetricValue(summary: OverviewMetricSummary | null, percentile: "p75" | "p95" = "p75"): string {
  if (!summary) {
    return "Awaiting data";
  }
  const value = summary[percentile];
  if (!Number.isFinite(value)) {
    return "Awaiting data";
  }
  if (summary.unit === "score") {
    return Number(value).toFixed(3);
  }
  if (summary.unit === "count") {
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(Number(value));
  }
  return `${Math.round(Number(value))} ms`;
}

function getVitalTone(summary: OverviewMetricSummary | null): string {
  if (!summary || !Number.isFinite(summary.p75)) {
    return "text-white";
  }
  const value = Number(summary.p75);
  if (summary.good_threshold !== null && value <= summary.good_threshold) {
    return "text-[#9dd5bf]";
  }
  if (summary.needs_improvement_threshold !== null && value <= summary.needs_improvement_threshold) {
    return "text-amber-300";
  }
  return "text-rose-300";
}

function getVitalStatus(summary: OverviewMetricSummary | null): {
  label: string;
  className: string;
} {
  if (!summary || !Number.isFinite(summary.p75)) {
    return {
      label: "Awaiting Data",
      className: "border-white/10 bg-white/[0.05] text-white/72",
    };
  }
  const value = Number(summary.p75);
  if (summary.good_threshold !== null && value <= summary.good_threshold) {
    return {
      label: "Good",
      className: "border-emerald-400/25 bg-emerald-500/10 text-emerald-200",
    };
  }
  if (summary.needs_improvement_threshold !== null && value <= summary.needs_improvement_threshold) {
    return {
      label: "Watch",
      className: "border-amber-400/25 bg-amber-500/10 text-amber-200",
    };
  }
  return {
    label: "Over Target",
    className: "border-rose-400/25 bg-rose-500/10 text-rose-200",
  };
}

function formatTarget(summary: OverviewMetricSummary | null): string {
  if (!summary || summary.good_threshold === null) {
    return "Target unavailable";
  }
  if (summary.unit === "score") {
    return `Target <= ${summary.good_threshold.toFixed(1)}`;
  }
  if (summary.unit === "count") {
    return `Target <= ${Math.round(summary.good_threshold)}`;
  }
  return `Target <= ${Math.round(summary.good_threshold)} ms`;
}

function formatRelativeTimestamp(timestampSeconds: number | null): string {
  if (!timestampSeconds || !Number.isFinite(timestampSeconds)) {
    return "Awaiting data";
  }
  const deltaSeconds = Math.max(0, Math.round(Date.now() / 1000 - timestampSeconds));
  if (deltaSeconds < 60) {
    return "moments ago";
  }
  if (deltaSeconds < 3600) {
    return `${Math.round(deltaSeconds / 60)}m ago`;
  }
  if (deltaSeconds < 86_400) {
    return `${Math.round(deltaSeconds / 3600)}h ago`;
  }
  return `${Math.round(deltaSeconds / 86_400)}d ago`;
}

export default function AdminOverviewPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [results, setResults] = useState<StatusResult[]>([]);
  const [overview, setOverview] = useState<AdminOverviewSummaryResponse | null>(null);
  const [observability, setObservability] = useState<AdminObservabilitySummaryResponse | null>(null);
  const [traces, setTraces] = useState<AdminTracesSummaryResponse | null>(null);
  const [qaSummary, setQaSummary] = useState<StatusQaSummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const authStatus = await fetchTwfStatus();
        if (cancelled) return;
        setStatus(authStatus);
        if (!authStatus.linked || !authStatus.admin) {
          return;
        }

        const [statusResponse, overviewResponse, observabilityResponse, tracesResponse, qaSummaryResponse] = await Promise.all([
          fetchAdminStatusResults({ window: "30d", limit: 200 }),
          fetchAdminOverviewSummary("7d"),
          fetchAdminObservabilitySummary(),
          fetchAdminTracesSummary(),
          fetchAdminStatusQaSummary(),
        ]);
        if (cancelled) return;
        setResults(statusResponse.results);
        setOverview(overviewResponse);
        setObservability(observabilityResponse);
        setTraces(tracesResponse);
        setQaSummary(qaSummaryResponse);
        setError(null);
      } catch (nextError) {
        if (cancelled) return;
        setError(nextError instanceof Error ? nextError.message : "Failed to load admin overview");
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const issueRows = useMemo(() => results.filter((row) => row.status !== "healthy"), [results]);
  const artifactRows = useMemo(
    () => results.filter((row) => row.issue_type === "artifact_failure" || row.issue_type === "manifest_missing" || row.issue_type === "manifest_invalid"),
    [results],
  );
  const staleRows = useMemo(
    () => results.filter((row) => row.issue_type === "stale_run" || row.issue_type === "run_stalled"),
    [results],
  );
  const webVitals = overview?.web_vitals ?? null;
  const rumDiagnostics = overview?.rum_diagnostics ?? null;
  const telemetryHealth = overview?.telemetry_health ?? null;
  const lcpStatus = getVitalStatus(webVitals?.lcp ?? null);
  const inpStatus = getVitalStatus(webVitals?.inp ?? null);
  const clsStatus = getVitalStatus(webVitals?.cls ?? null);
  const observabilityLive = Boolean(observability?.metrics_enabled && (observability.http.recent_request_count ?? 0) > 0);
  const tracesLive = Boolean(traces?.enabled && traces.recent.last_trace_at);
  const posthogEnabled = isPostHogEnabled();
  const posthogReplayEnabled = isPostHogReplayEnabled();
  const qaStoreMode = qaSummary?.store_mode === "separate" ? "Separate" : qaSummary?.store_mode === "shared" ? "Shared" : "Awaiting data";

  return (
    <AdminGate status={status} loadingLabel="Loading admin overview...">
      <div className="space-y-6">
        <section className="rounded-[32px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
          <div className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[#95b1a2]">Overview</div>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">Telemetry trust center</h2>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-white/62">
            Phase 8 turns the admin shell into the rollout checkpoint for telemetry ownership. Use this page to confirm which
            system owns each signal class, whether the live emitters are healthy, and where to drill down when a rollout looks off.
          </p>

          {error ? (
            <div className="mt-5 rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
              {error}
            </div>
          ) : null}

          <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <SummaryCard title="Open Issues" value={String(issueRows.length)} hint="Retained run warnings and errors" icon={AlertTriangle} accentClassName="text-amber-300" />
            <SummaryCard title="Artifact Failures" value={String(artifactRows.length)} hint="Missing or unreadable artifacts" icon={ClipboardCheck} accentClassName="text-rose-300" />
            <SummaryCard title="Stale / Stalled" value={String(staleRows.length)} hint="Latest runs needing attention" icon={Activity} accentClassName="text-[#9dd5bf]" />
            <SummaryCard
              title="QA Reviews"
              value={new Intl.NumberFormat("en-US").format(qaSummary?.total_reviews ?? 0)}
              hint={
                qaSummary
                  ? `${new Intl.NumberFormat("en-US").format(qaSummary.warning_reviews)} warning reviews · ${qaStoreMode.toLowerCase()} store`
                  : "Awaiting QA store summary"
              }
              icon={Database}
              accentClassName="text-sky-300"
            />
          </div>
        </section>

        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <SummaryCard
            title="LCP p75"
            value={formatMetricValue(webVitals?.lcp ?? null)}
            hint={
              webVitals?.lcp?.count
                ? `${webVitals.lcp.count} samples in last 7d · last seen ${formatRelativeTimestamp(telemetryHealth?.web_vitals_last_seen_at ?? null)}`
                : "Waiting for Web Vitals data"
            }
            icon={Gauge}
            accentClassName={getVitalTone(webVitals?.lcp ?? null)}
            description="Measures how long the main visible content takes to appear after navigation."
            statusLabel={lcpStatus.label}
            statusClassName={lcpStatus.className}
          />
          <SummaryCard
            title="INP p75"
            value={formatMetricValue(webVitals?.inp ?? null)}
            hint={
              webVitals?.inp?.count
                ? `${webVitals.inp.count} samples in last 7d · ${formatTarget(webVitals.inp)}`
                : "Waiting for Web Vitals data"
            }
            icon={Gauge}
            accentClassName={getVitalTone(webVitals?.inp ?? null)}
            description="Measures how responsive the page feels when a user clicks, taps, or types."
            statusLabel={inpStatus.label}
            statusClassName={inpStatus.className}
          />
          <SummaryCard
            title="CLS p75"
            value={formatMetricValue(webVitals?.cls ?? null)}
            hint={
              webVitals?.cls?.count
                ? `${webVitals.cls.count} samples in last 7d · ${formatTarget(webVitals.cls)}`
                : "Waiting for Web Vitals data"
            }
            icon={Gauge}
            accentClassName={getVitalTone(webVitals?.cls ?? null)}
            description="Measures unexpected layout movement while the page is loading and settling."
            statusLabel={clsStatus.label}
            statusClassName={clsStatus.className}
          />
          <SummaryCard
            title="Manifest Fetch p95"
            value={formatMetricValue(rumDiagnostics?.manifest_fetch_duration ?? null, "p95")}
            hint={
              rumDiagnostics?.manifest_fetch_duration?.count
                ? `${rumDiagnostics.manifest_fetch_duration.count} sampled diagnostics · last seen ${formatRelativeTimestamp(telemetryHealth?.rum_last_seen_at ?? null)}`
                : "Waiting for sampled RUM diagnostics"
            }
            icon={Activity}
            accentClassName="text-sky-300"
          />
        </section>

        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <SummaryCard
            title="Product Analytics"
            value={posthogEnabled ? "Enabled" : "Disabled"}
            hint={`Owner: PostHog · replay ${posthogReplayEnabled ? "on" : "off"}`}
            icon={BarChart3}
            accentClassName={posthogEnabled ? "text-[#9dd5bf]" : "text-white"}
            description="Usage events, funnels, and replay are owned in PostHog. Validate ingestion and drill-down under Analytics."
            statusLabel={posthogEnabled ? "External Owner" : "Needs Config"}
            statusClassName={posthogEnabled ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200" : "border-white/10 bg-white/[0.05] text-white/72"}
          />
          <SummaryCard
            title="Service Metrics"
            value={observabilityLive ? "Live" : observability?.metrics_enabled ? "Armed" : "Disabled"}
            hint={
              observability
                ? `${observability.http.recent_request_count} recent requests · p95 ${observability.http.p95_ms !== null ? `${Math.round(observability.http.p95_ms)} ms` : "n/a"}`
                : "Awaiting Prometheus summary"
            }
            icon={Activity}
            accentClassName={observabilityLive ? "text-[#9dd5bf]" : "text-white"}
            description="API latency, error rate, cache health, and run freshness are owned by Prometheus and Grafana."
            statusLabel={observabilityLive ? "Healthy" : observability?.metrics_enabled ? "Needs Traffic" : "Disabled"}
            statusClassName={observabilityLive ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200" : "border-white/10 bg-white/[0.05] text-white/72"}
          />
          <SummaryCard
            title="Trace Drill-down"
            value={tracesLive ? "Live" : traces?.enabled ? "Armed" : "Disabled"}
            hint={
              traces
                ? `${traces.recent.exported_traces} exported traces · last seen ${formatRelativeTimestamp(traces.recent.last_trace_at)}`
                : "Awaiting trace summary"
            }
            icon={Waypoints}
            accentClassName={tracesLive ? "text-sky-300" : "text-white"}
            description="Slow-request correlation and backend drill-down are owned by OpenTelemetry plus Tempo."
            statusLabel={tracesLive ? "Healthy" : traces?.enabled ? "Waiting" : "Disabled"}
            statusClassName={tracesLive ? "border-sky-400/25 bg-sky-500/10 text-sky-200" : "border-white/10 bg-white/[0.05] text-white/72"}
          />
          <SummaryCard
            title="QA Store"
            value={qaStoreMode}
            hint={
              qaSummary
                ? `${new Intl.NumberFormat("en-US").format(qaSummary.distinct_runs)} tracked runs · last checked ${formatRelativeTimestamp(qaSummary.latest_checked_at)}`
                : "Awaiting QA store summary"
            }
            icon={Database}
            accentClassName={qaSummary?.store_mode === "separate" ? "text-[#9dd5bf]" : "text-white"}
            description="Pipeline and QA health remain first-party CartoSky ownership under Status."
            statusLabel={qaSummary?.store_mode === "separate" ? "Separated" : qaSummary?.store_mode === "shared" ? "Shared Store" : "Awaiting Data"}
            statusClassName={qaSummary?.store_mode === "separate" ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200" : "border-white/10 bg-white/[0.05] text-white/72"}
          />
        </section>

        <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
          <div className="text-lg font-semibold">Ownership Map</div>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-white/62">
            Each admin route now fronts a specific telemetry owner. Use these as the release-level handoff points instead of the
            retired custom frontend perf stack.
          </p>
          <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <Link
              to="/admin/analytics"
              className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4 text-sm text-white transition hover:bg-white/[0.08]"
            >
              <div className="font-semibold">Analytics</div>
              <div className="mt-2 text-white/60">PostHog owns product analytics, dashboards, event ingestion validation, and replay launch points.</div>
            </Link>
            <Link
              to="/admin/observability"
              className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4 text-sm text-white transition hover:bg-white/[0.08]"
            >
              <div className="font-semibold">Observability</div>
              <div className="mt-2 text-white/60">Prometheus and Grafana own API latency, errors, cache health, and published-run freshness.</div>
            </Link>
            <Link
              to="/admin/traces"
              className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4 text-sm text-white transition hover:bg-white/[0.08]"
            >
              <div className="font-semibold">Traces</div>
              <div className="mt-2 text-white/60">Tempo-backed traces own slow-request drill-down, correlation, and request-path debugging.</div>
            </Link>
            <Link
              to="/admin/status"
              className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4 text-sm text-white transition hover:bg-white/[0.08]"
            >
              <div className="font-semibold">Pipeline Status</div>
              <div className="mt-2 text-white/60">CartoSky keeps ownership here for retained-run health, artifact checks, QA warnings, and domain-specific pipeline issues.</div>
            </Link>
          </div>
        </section>
      </div>
    </AdminGate>
  );
}
