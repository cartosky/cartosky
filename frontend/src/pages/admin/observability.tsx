import { useEffect, useState } from "react";
import { Activity, Database, ExternalLink, Server } from "lucide-react";

import { fetchAdminObservabilitySummary, fetchTwfStatus, type AdminObservabilitySummaryResponse, type TwfStatus } from "@/lib/admin-api";
import { getGrafanaDashboardUrl, getGrafanaEmbedUrl, getGrafanaUrl } from "@/lib/config";

function SummaryCard(props: {
  title: string;
  value: string;
  hint: string;
  accentClassName?: string;
  icon: typeof Activity;
}) {
  const { title, value, hint, accentClassName = "text-white", icon: Icon } = props;
  return (
    <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-white">{title}</div>
          <div className={`mt-3 text-[2.1rem] font-semibold tracking-tight ${accentClassName}`}>{value}</div>
          <div className="mt-2 text-xs uppercase tracking-[0.18em] text-white/40">{hint}</div>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/[0.05] p-3">
          <Icon className={`h-5 w-5 ${accentClassName}`} />
        </div>
      </div>
    </section>
  );
}

export default function AdminObservabilityPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [summary, setSummary] = useState<AdminObservabilitySummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const grafanaUrl = getGrafanaUrl();
  const grafanaDashboardUrl = getGrafanaDashboardUrl();
  const grafanaEmbedUrl = getGrafanaEmbedUrl();

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
        const nextSummary = await fetchAdminObservabilitySummary();
        if (cancelled) return;
        setSummary(nextSummary);
      } catch (nextError) {
        if (cancelled) return;
        setError(nextError instanceof Error ? nextError.message : "Failed to load observability shell");
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!status?.linked || !status.admin) {
    return (
      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        Observability appears here after admin access is available.
      </section>
    );
  }

  const publishedRuns = summary?.published_runs ?? [];
  const oldestPublishedRun = publishedRuns.reduce<number | null>(
    (current, row) => (current === null || row.run_age_hours > current ? row.run_age_hours : current),
    null,
  );
  const lowestCompletion = publishedRuns.reduce<number | null>(
    (current, row) => (current === null || row.completion_ratio < current ? row.completion_ratio : current),
    null,
  );

  return (
    <div className="space-y-6">
      <section className="rounded-[32px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="flex items-start gap-3">
          <div className="rounded-2xl border border-white/12 bg-white/[0.05] p-3">
            <Activity className="h-5 w-5 text-[#9dd5bf]" />
          </div>
          <div>
            <div className="text-2xl font-semibold tracking-tight">Observability</div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-white/62">
              This route is the Grafana and Prometheus launch point inside the CartoSky admin shell. It now exposes native rollout summaries and can host a Grafana dashboard embed when configured.
            </p>
          </div>
        </div>

        {error ? (
          <div className="mt-5 rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        ) : null}

        <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <SummaryCard
            title="API p95"
            value={summary?.http.p95_ms !== null && summary?.http.p95_ms !== undefined ? `${Math.round(summary.http.p95_ms)} ms` : "Awaiting data"}
            hint={summary ? `${summary.http.recent_request_count} recent requests` : "Waiting for summary"}
            accentClassName="text-sky-300"
            icon={Server}
          />
          <SummaryCard
            title="Error Rate"
            value={summary?.http.error_rate !== null && summary?.http.error_rate !== undefined ? `${Math.round(summary.http.error_rate * 100)}%` : "Awaiting data"}
            hint="Recent API requests with 4xx/5xx"
            accentClassName="text-amber-300"
            icon={Activity}
          />
          <SummaryCard
            title="Sample Cache Hit Rate"
            value={summary?.sample_cache.point_hit_rate !== null && summary?.sample_cache.point_hit_rate !== undefined ? `${Math.round(summary.sample_cache.point_hit_rate * 100)}%` : "Awaiting data"}
            hint={summary ? `${summary.sample_cache.entries} active cache entries` : "Waiting for summary"}
            accentClassName="text-[#9dd5bf]"
            icon={Database}
          />
          <SummaryCard
            title="Oldest Published Run"
            value={oldestPublishedRun !== null ? `${oldestPublishedRun.toFixed(1)} h` : "Awaiting data"}
            hint={lowestCompletion !== null ? `Lowest completion ${(lowestCompletion * 100).toFixed(0)}%` : "Waiting for published-run gauges"}
            accentClassName="text-white"
            icon={Activity}
          />
        </div>

        <div className="mt-6 grid gap-4 xl:grid-cols-3">
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <Server className="h-5 w-5 text-sky-300" />
              <div className="text-sm font-semibold text-white">Prometheus Status</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              Prometheus metrics are currently {summary?.metrics_enabled ? "enabled and exposed on the API." : "disabled until CARTOSKY_PROMETHEUS_ENABLED is turned on in production."}
            </p>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <ExternalLink className="h-5 w-5 text-white/76" />
              <div className="text-sm font-semibold text-white">Grafana Dashboard</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              {grafanaDashboardUrl ? "Open the native Grafana dashboard for deeper latency, cache, and run-health analysis." : "Set a Grafana dashboard URL in env to deep-link operators into observability."}
            </p>
            {grafanaDashboardUrl ? (
              <a
                href={grafanaDashboardUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-4 inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2 text-sm text-white transition hover:bg-white/[0.08]"
              >
                Open Grafana Dashboard
                <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <ExternalLink className="h-5 w-5 text-white/76" />
              <div className="text-sm font-semibold text-white">Grafana Project</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              {grafanaUrl ? "Use the Grafana project UI for ad hoc exploration of Prometheus-backed charts." : "Set a Grafana project URL if you want this page to link to the main observability UI."}
            </p>
            {grafanaUrl ? (
              <a
                href={grafanaUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-4 inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2 text-sm text-white transition hover:bg-white/[0.08]"
              >
                Open Grafana
                <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
          </section>
        </div>
      </section>

      {grafanaEmbedUrl ? (
        <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
          <div className="text-lg font-semibold">Embedded dashboard</div>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-white/62">
            This is the main Grafana view inside CartoSky admin. Use it for high-level latency, cache, and run-health trends, then jump to native Grafana for deeper drill-down.
          </p>
          <div className="mt-5 overflow-hidden rounded-[24px] border border-white/10 bg-black/20">
            <iframe
              src={grafanaEmbedUrl}
              title="Grafana observability dashboard"
              className="h-[720px] w-full"
              loading="lazy"
            />
          </div>
        </section>
      ) : null}
    </div>
  );
}
