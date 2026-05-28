import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, BarChart3, ClipboardCheck, Database, Gauge, Waypoints } from "lucide-react";
import { Link } from "react-router-dom";

import { AdminEmpty, AdminHero, AdminPage, AdminSurface } from "@/components/admin-shell";
import {
  fetchAdminAuthStatus,
  fetchAdminNetworkDiagnostics,
  fetchAdminObservabilitySummary,
  fetchAdminOverviewSummary,
  fetchAdminStatusResults,
  fetchAdminStatusQaSummary,
  fetchAdminTracesSummary,
  type AdminNetworkDiagnosticsResponse,
  type AdminObservabilitySummaryResponse,
  type NetworkDiagnosticBreakdown,
  type NetworkDiagnosticMetricName,
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
      <AdminEmpty>
        {loadingLabel}
      </AdminEmpty>
    );
  }

  if (!status.linked || !status.admin) {
    return (
      <AdminEmpty>
        Admin access appears here after a linked admin session is available.
      </AdminEmpty>
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
  topBorderClass?: string;
}) {
  const {
    title,
    value,
    hint,
    icon: Icon,
    accentClassName = "text-white",
    topBorderClass,
  } = props;
  return (
    <section className={`rounded-[1.15rem] border border-white/8 bg-white/[0.025] p-4 shadow-[0_12px_30px_rgba(0,0,0,0.16)] ${topBorderClass ? `border-t-2 ${topBorderClass}` : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-white">{title}</div>
          <div className={`mt-3 text-[2.1rem] font-semibold tracking-tight ${accentClassName}`}>{value}</div>
          <div className="mt-1 text-xs uppercase tracking-[0.16em] text-white/40">{hint}</div>
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

const NETWORK_P95_TARGETS: Partial<Record<NetworkDiagnosticMetricName, number>> = {
  bootstrap_fetch_duration: 800,
  capabilities_fetch_duration: 350,
  regions_fetch_duration: 350,
  manifest_fetch_duration: 600,
  frames_fetch_duration: 650,
  grid_manifest_fetch_duration: 500,
  grid_binary_fetch_duration: 1200,
  grid_binary_array_buffer_duration: 250,
  grid_texture_prepare_duration: 120,
  grid_texture_upload_duration: 80,
  grid_webgl1_expand_duration: 60,
  sample_request_duration: 450,
  sample_batch_request_duration: 700,
  contour_fetch_duration: 500,
};

const NETWORK_CARD_METRICS: NetworkDiagnosticMetricName[] = [
  "bootstrap_fetch_duration",
  "grid_binary_fetch_duration",
  "frames_fetch_duration",
  "sample_request_duration",
];

function formatNetworkBreakdowns(items: NetworkDiagnosticBreakdown[] | null | undefined): string {
  if (!items || items.length === 0) {
    return "Awaiting data";
  }
  return items
    .filter((item) => item.count > 0)
    .slice(0, 3)
    .map((item) => {
      const p95 = Number.isFinite(item.p95) ? `${Math.round(Number(item.p95))} ms` : "n/a";
      return `${item.key}: ${p95} (${item.count})`;
    })
    .join(" · ");
}

function getNetworkStatus(summary: OverviewMetricSummary | null, targetMs: number | null): {
  label: string;
  className: string;
  accentClassName: string;
} {
  if (!summary || !summary.count || !Number.isFinite(summary.p95)) {
    return {
      label: "Awaiting Data",
      className: "border-white/10 bg-white/[0.05] text-white/72",
      accentClassName: "text-white",
    };
  }
  const p95 = Number(summary.p95);
  if (!targetMs || p95 <= targetMs) {
    return {
      label: "Healthy",
      className: "border-emerald-400/25 bg-emerald-500/10 text-emerald-200",
      accentClassName: "text-[#9dd5bf]",
    };
  }
  if (p95 <= targetMs * 1.5) {
    return {
      label: "Watch",
      className: "border-amber-400/25 bg-amber-500/10 text-amber-200",
      accentClassName: "text-amber-300",
    };
  }
  return {
    label: "Over Target",
    className: "border-rose-400/25 bg-rose-500/10 text-rose-200",
    accentClassName: "text-rose-300",
  };
}

function getNetworkActionLabel(params: {
  summary: OverviewMetricSummary;
  by_cf_cache_status: NetworkDiagnosticBreakdown[];
  targetMs: number | null;
}): string {
  const { summary, by_cf_cache_status: cacheBreakdown, targetMs } = params;
  if (!summary.count) {
    return "Awaiting samples";
  }
  const hit = cacheBreakdown.find((item) => item.key === "HIT");
  const miss = cacheBreakdown.find((item) => item.key === "MISS");
  const bypass = cacheBreakdown.find((item) => item.key === "BYPASS");

  if (bypass?.count && Number.isFinite(bypass.p95) && Number.isFinite(summary.p95) && Number(bypass.p95) > Number(summary.p95) * 1.35) {
    return "Origin path is notably slower on BYPASS";
  }
  if (miss?.count && hit?.count && Number.isFinite(miss.p95) && Number.isFinite(hit.p95) && Number(miss.p95) > Number(hit.p95) * 1.5) {
    return "MISS path is materially slower than HIT";
  }
  if (hit?.count && targetMs && Number.isFinite(hit.p95) && Number(hit.p95) > targetMs) {
    return "Slow even on HIT; check edge or frontend path";
  }
  if (!hit && !miss && !bypass) {
    return "No cache-status split yet";
  }
  return "Monitor cache and device mix";
}

export default function AdminOverviewPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [results, setResults] = useState<StatusResult[]>([]);
  const [overview, setOverview] = useState<AdminOverviewSummaryResponse | null>(null);
  const [networkDiagnostics, setNetworkDiagnostics] = useState<AdminNetworkDiagnosticsResponse | null>(null);
  const [observability, setObservability] = useState<AdminObservabilitySummaryResponse | null>(null);
  const [traces, setTraces] = useState<AdminTracesSummaryResponse | null>(null);
  const [qaSummary, setQaSummary] = useState<StatusQaSummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const authStatus = await fetchAdminAuthStatus();
        if (cancelled) return;
        setStatus(authStatus);
        if (!authStatus.linked || !authStatus.admin) {
          return;
        }

        void fetchAdminStatusResults({ window: "30d", limit: 200 })
          .then((statusResponse) => {
            if (cancelled) return;
            setResults(statusResponse.results);
          })
          .catch((nextError) => {
            if (cancelled) return;
            setError(nextError instanceof Error ? nextError.message : "Failed to load admin overview");
          });

        const [overviewResponse, networkDiagnosticsResponse, observabilityResponse, tracesResponse, qaSummaryResponse] = await Promise.all([
          fetchAdminOverviewSummary("7d"),
          fetchAdminNetworkDiagnostics("7d"),
          fetchAdminObservabilitySummary(),
          fetchAdminTracesSummary(),
          fetchAdminStatusQaSummary(),
        ]);
        if (cancelled) return;
        setOverview(overviewResponse);
        setNetworkDiagnostics(networkDiagnosticsResponse);
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
  const networkMetrics = networkDiagnostics?.metrics ?? [];
  const networkMetricByName = useMemo(
    () => new Map(networkMetrics.map((metric) => [metric.metric_name, metric])),
    [networkMetrics],
  );
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
      <AdminPage>
        <AdminHero
          eyebrow="Overview"
          title="System health"
        >
          {error ? (
            <div className="rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
              {error}
            </div>
          ) : null}

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <SummaryCard title="Open Issues" value={String(issueRows.length)} hint="Run warnings &amp; errors" icon={AlertTriangle} accentClassName="text-amber-300" topBorderClass="border-t-amber-400/60" />
            <SummaryCard title="Artifact Failures" value={String(artifactRows.length)} hint="Missing or unreadable" icon={ClipboardCheck} accentClassName="text-rose-300" topBorderClass="border-t-rose-400/60" />
            <SummaryCard title="Stale / Stalled" value={String(staleRows.length)} hint="Runs needing attention" icon={Activity} accentClassName="text-[#9dd5bf]" topBorderClass={staleRows.length > 0 ? "border-t-amber-400/60" : "border-t-emerald-400/40"} />
            <SummaryCard
              title="QA Reviews"
              value={new Intl.NumberFormat("en-US").format(qaSummary?.total_reviews ?? 0)}
              hint={qaSummary ? `${qaSummary.warning_reviews} warnings · ${qaStoreMode.toLowerCase()}` : "Awaiting summary"}
              icon={Database}
              accentClassName="text-sky-300"
              topBorderClass="border-t-sky-400/40"
            />
          </div>
        </AdminHero>

        <AdminSurface
          title="Experience Signals"
          description="Core frontend health and manifest fetch timing."
        >
          <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
            <div className="space-y-4">
              {[
                {
                  title: "LCP p75",
                  value: formatMetricValue(webVitals?.lcp ?? null),
                  hint: webVitals?.lcp?.count
                    ? `${webVitals.lcp.count} samples in last 7d · last seen ${formatRelativeTimestamp(telemetryHealth?.web_vitals_last_seen_at ?? null)}`
                    : "Waiting for Web Vitals data",
                  target: "Measures how long the main visible content takes to appear after navigation.",
                  tone: getVitalTone(webVitals?.lcp ?? null),
                  statusLabel: lcpStatus.label,
                  statusClassName: lcpStatus.className,
                },
                {
                  title: "INP p75",
                  value: formatMetricValue(webVitals?.inp ?? null),
                  hint: webVitals?.inp?.count
                    ? `${webVitals.inp.count} samples in last 7d · ${formatTarget(webVitals.inp)}`
                    : "Waiting for Web Vitals data",
                  target: "Measures how responsive the page feels when a user clicks, taps, or types.",
                  tone: getVitalTone(webVitals?.inp ?? null),
                  statusLabel: inpStatus.label,
                  statusClassName: inpStatus.className,
                },
                {
                  title: "CLS p75",
                  value: formatMetricValue(webVitals?.cls ?? null),
                  hint: webVitals?.cls?.count
                    ? `${webVitals.cls.count} samples in last 7d · ${formatTarget(webVitals.cls)}`
                    : "Waiting for Web Vitals data",
                  target: "Measures unexpected layout movement while the page is loading and settling.",
                  tone: getVitalTone(webVitals?.cls ?? null),
                  statusLabel: clsStatus.label,
                  statusClassName: clsStatus.className,
                },
              ].map((metric) => (
                <div key={metric.title} className="border-l border-white/8 pl-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="text-sm font-semibold text-white">{metric.title}</div>
                    <div className={`rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] ${metric.statusClassName}`}>
                      {metric.statusLabel}
                    </div>
                  </div>
                  <div className={`mt-2 text-2xl font-semibold tracking-tight ${metric.tone}`}>{metric.value}</div>
                  <div className="mt-1 text-xs uppercase tracking-[0.16em] text-white/40">{metric.hint}</div>
                  <div className="mt-2 text-sm leading-6 text-white/58">{metric.target}</div>
                </div>
              ))}
            </div>

            <div className="border-l border-white/8 pl-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-white">
                <Activity className="h-4 w-4 text-cyan-200/80" />
                Manifest Fetch p95
              </div>
              <div className="mt-3 text-3xl font-semibold tracking-tight text-cyan-200">
                {formatMetricValue(rumDiagnostics?.manifest_fetch_duration ?? null, "p95")}
              </div>
              <div className="mt-2 text-xs uppercase tracking-[0.16em] text-white/40">
                {rumDiagnostics?.manifest_fetch_duration?.count
                  ? `${rumDiagnostics.manifest_fetch_duration.count} sampled diagnostics · last seen ${formatRelativeTimestamp(telemetryHealth?.rum_last_seen_at ?? null)}`
                  : "Waiting for sampled RUM diagnostics"}
              </div>
              <div className="mt-4 border-t border-white/8 pt-4">
                <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.22em] text-white/44">Ownership</div>
                <div className="flex flex-wrap gap-2">
                  <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${posthogEnabled ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200" : "border-white/10 bg-white/[0.05] text-white/60"}`}>
                    <span className={`h-1.5 w-1.5 rounded-full ${posthogEnabled ? "bg-emerald-400" : "bg-white/30"}`} />
                    PostHog {posthogEnabled ? "on" : "off"}
                  </span>
                  <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${observabilityLive ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200" : observability?.metrics_enabled ? "border-amber-400/25 bg-amber-500/10 text-amber-200" : "border-white/10 bg-white/[0.05] text-white/60"}`}>
                    <span className={`h-1.5 w-1.5 rounded-full ${observabilityLive ? "bg-emerald-400" : observability?.metrics_enabled ? "bg-amber-400" : "bg-white/30"}`} />
                    Prometheus {observabilityLive ? "live" : observability?.metrics_enabled ? "armed" : "off"}
                  </span>
                  <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${tracesLive ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200" : traces?.enabled ? "border-amber-400/25 bg-amber-500/10 text-amber-200" : "border-white/10 bg-white/[0.05] text-white/60"}`}>
                    <span className={`h-1.5 w-1.5 rounded-full ${tracesLive ? "bg-emerald-400" : traces?.enabled ? "bg-amber-400" : "bg-white/30"}`} />
                    Tempo {tracesLive ? "live" : traces?.enabled ? "armed" : "off"}
                  </span>
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-sky-400/25 bg-sky-500/10 px-2.5 py-1 text-[11px] font-semibold text-sky-200">
                    <span className="h-1.5 w-1.5 rounded-full bg-sky-400" />
                    QA {qaStoreMode}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </AdminSurface>

        <AdminSurface
          title="Network Diagnostics"
          headerRight={
            <div className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-white/62">
              Last seen {formatRelativeTimestamp(telemetryHealth?.rum_last_seen_at ?? null)}
            </div>
          }
        >
          <div className="overflow-hidden rounded-[1.2rem] border border-white/10 bg-white/[0.03]">
            <div className="grid grid-cols-[minmax(160px,1.5fr)_90px_80px_minmax(80px,0.8fr)_minmax(0,1fr)] gap-4 border-b border-white/10 px-4 py-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-white/45">
              <div>Metric</div>
              <div>p95</div>
              <div>Samples</div>
              <div>Cache</div>
              <div>Action</div>
            </div>
            <div>
              {[...networkMetrics]
                .sort((a, b) => {
                  const targetA = NETWORK_P95_TARGETS[a.metric_name] ?? null;
                  const targetB = NETWORK_P95_TARGETS[b.metric_name] ?? null;
                  const statusA = getNetworkStatus(a.summary, targetA);
                  const statusB = getNetworkStatus(b.summary, targetB);
                  const order = { "Over Target": 0, Watch: 1, Healthy: 2, "Awaiting Data": 3 };
                  return (order[statusA.label as keyof typeof order] ?? 3) - (order[statusB.label as keyof typeof order] ?? 3);
                })
                .map((metric) => {
                const targetMs = NETWORK_P95_TARGETS[metric.metric_name] ?? null;
                const statusTone = getNetworkStatus(metric.summary, targetMs);
                const dominantCache = [...metric.by_cf_cache_status].sort((a, b) => b.count - a.count)[0];
                return (
                  <div
                    key={metric.metric_name}
                    className={`grid grid-cols-[minmax(160px,1.5fr)_90px_80px_minmax(80px,0.8fr)_minmax(0,1fr)] gap-4 border-b border-white/6 px-4 py-3 text-sm last:border-b-0 ${
                      statusTone.label === "Over Target" ? "bg-rose-500/[0.05]" : statusTone.label === "Watch" ? "bg-amber-500/[0.04]" : ""
                    }`}
                  >
                    <div>
                      <div className="font-semibold text-white">{metric.label}</div>
                      <div className="mt-0.5 text-xs text-white/42">{metric.metric_name}</div>
                    </div>
                    <div className={`font-semibold ${statusTone.accentClassName}`}>{formatMetricValue(metric.summary, "p95")}</div>
                    <div className="text-white/68">{new Intl.NumberFormat("en-US").format(metric.summary.count)}</div>
                    <div>
                      {dominantCache?.key ? (
                        <span className="inline-flex rounded-full border border-white/10 bg-white/[0.05] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/68">
                          {dominantCache.key}
                        </span>
                      ) : <span className="text-white/30">—</span>}
                    </div>
                    <div className="text-white/72 text-xs leading-5">{getNetworkActionLabel({
                      summary: metric.summary,
                      by_cf_cache_status: metric.by_cf_cache_status,
                      targetMs,
                    })}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </AdminSurface>

        <AdminSurface title="Ownership Map">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {[
              { to: "/admin/analytics", icon: BarChart3, label: "Analytics", sub: "PostHog · events, replay" },
              { to: "/admin/observability", icon: Activity, label: "Observability", sub: "Prometheus · Grafana" },
              { to: "/admin/traces", icon: Waypoints, label: "Traces", sub: "Tempo · OTLP drill-down" },
              { to: "/admin/status", icon: ClipboardCheck, label: "Pipeline Status", sub: "Retained runs · artifacts" },
            ].map(({ to, icon: Icon, label, sub }) => (
              <Link key={to} to={to} className="flex items-center gap-3 rounded-xl border border-white/8 px-3 py-3 text-sm transition hover:border-white/14 hover:bg-white/[0.03]">
                <Icon className="h-4 w-4 flex-shrink-0 text-cyan-200/80" />
                <div>
                  <div className="font-semibold text-white">{label}</div>
                  <div className="text-xs text-white/50">{sub}</div>
                </div>
              </Link>
            ))}
          </div>
        </AdminSurface>
      </AdminPage>
    </AdminGate>
  );
}
