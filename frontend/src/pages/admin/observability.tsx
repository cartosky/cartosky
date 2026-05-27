import { useEffect, useState } from "react";
import { Activity, Database, ExternalLink, Server } from "lucide-react";

import { AdminEmpty, AdminHero, AdminPage, AdminStat, AdminSurface } from "@/components/admin-shell";
import { fetchAdminAuthStatus, fetchAdminObservabilitySummary, type AdminObservabilitySummaryResponse, type TwfStatus } from "@/lib/admin-api";
import { getGrafanaDashboardUrl, getGrafanaEmbedUrl, getGrafanaUrl } from "@/lib/config";

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
        const authStatus = await fetchAdminAuthStatus();
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
      <AdminEmpty>
        Observability appears here after admin access is available.
      </AdminEmpty>
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
    <AdminPage>
      <AdminHero
        eyebrow="Observability"
        title="Service metrics"
      >
        {error ? (
          <div className="rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        ) : null}

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <AdminStat
            label="API p95"
            value={summary?.http.p95_ms !== null && summary?.http.p95_ms !== undefined ? `${Math.round(summary.http.p95_ms)} ms` : "Awaiting data"}
            hint={summary ? `${summary.http.recent_request_count} recent requests` : "Waiting for summary"}
            accentClassName="text-cyan-200"
            icon={<Server className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Error Rate"
            value={summary?.http.error_rate !== null && summary?.http.error_rate !== undefined ? `${Math.round(summary.http.error_rate * 100)}%` : "Awaiting data"}
            hint="Recent API requests with 4xx/5xx"
            accentClassName="text-white"
            icon={<Activity className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Sample Cache Hit Rate"
            value={summary?.sample_cache.point_hit_rate !== null && summary?.sample_cache.point_hit_rate !== undefined ? `${Math.round(summary.sample_cache.point_hit_rate * 100)}%` : "Awaiting data"}
            hint={summary ? `${summary.sample_cache.entries} active cache entries` : "Waiting for summary"}
            accentClassName="text-cyan-200"
            icon={<Database className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Oldest Published Run"
            value={oldestPublishedRun !== null ? `${oldestPublishedRun.toFixed(1)} h` : "Awaiting data"}
            hint={lowestCompletion !== null ? `Lowest completion ${(lowestCompletion * 100).toFixed(0)}%` : "Waiting for published-run gauges"}
            accentClassName="text-white"
            icon={<Activity className="h-5 w-5 text-cyan-200/80" />}
          />
        </div>
      </AdminHero>

      <AdminSurface title="Launch surfaces">
        <div className="grid gap-3 xl:grid-cols-3">
          <section className="flex items-start gap-3 rounded-xl border border-white/8 p-3">
            <Server className="mt-0.5 h-4 w-4 flex-shrink-0 text-cyan-200/80" />
            <div>
              <div className="text-sm font-semibold text-white">Prometheus</div>
              <div className="mt-1 text-xs text-white/55">{summary?.metrics_enabled ? "Enabled · metrics exposed" : "Disabled · needs CARTOSKY_PROMETHEUS_ENABLED"}</div>
            </div>
          </section>
          <section className="flex items-start gap-3 rounded-xl border border-white/8 p-3">
            <ExternalLink className="mt-0.5 h-4 w-4 flex-shrink-0 text-white/76" />
            <div>
              <div className="text-sm font-semibold text-white">Grafana Dashboard</div>
              <div className="mt-1 text-xs text-white/55">{grafanaDashboardUrl ? "Latency, cache, and run-health charts" : "Set Grafana dashboard URL in env"}</div>
              {grafanaDashboardUrl ? (
                <a href={grafanaDashboardUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-cyan-300 hover:text-cyan-200">
                  Open dashboard <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
            </div>
          </section>
          <section className="flex items-start gap-3 rounded-xl border border-white/8 p-3">
            <ExternalLink className="mt-0.5 h-4 w-4 flex-shrink-0 text-white/76" />
            <div>
              <div className="text-sm font-semibold text-white">Grafana Project</div>
              <div className="mt-1 text-xs text-white/55">{grafanaUrl ? "Ad hoc Prometheus chart exploration" : "Set Grafana project URL in env"}</div>
              {grafanaUrl ? (
                <a href={grafanaUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-cyan-300 hover:text-cyan-200">
                  Open Grafana <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
            </div>
          </section>
        </div>
      </AdminSurface>

      {grafanaEmbedUrl ? (
        <AdminSurface title="Embedded dashboard">
          <div className="overflow-hidden rounded-[1.2rem] border border-white/10 bg-black/20">
            <iframe
              src={grafanaEmbedUrl}
              title="Grafana observability dashboard"
              className="h-[720px] w-full"
              loading="lazy"
            />
          </div>
        </AdminSurface>
      ) : null}
    </AdminPage>
  );
}
