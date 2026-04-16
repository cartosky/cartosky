import { useEffect, useState } from "react";
import { ExternalLink, Route, TimerReset, Waypoints } from "lucide-react";

import { AdminEmpty, AdminHero, AdminPage, AdminStat, AdminSurface } from "@/components/admin-shell";
import { fetchAdminTracesSummary, fetchTwfStatus, type AdminTracesSummaryResponse, type TwfStatus } from "@/lib/admin-api";
import { getGrafanaTracesUrl, getGrafanaUrl } from "@/lib/config";

export default function AdminTracesPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [summary, setSummary] = useState<AdminTracesSummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const grafanaUrl = getGrafanaUrl();
  const grafanaTracesUrl = getGrafanaTracesUrl();

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [authStatus, tracesSummary] = await Promise.all([
          fetchTwfStatus(),
          fetchAdminTracesSummary(),
        ]);
        if (cancelled) return;
        setStatus(authStatus);
        setSummary(tracesSummary);
      } catch (nextError) {
        if (cancelled) return;
        setError(nextError instanceof Error ? nextError.message : "Failed to load traces shell");
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
        Traces appear here after admin access is available.
      </AdminEmpty>
    );
  }

  return (
    <AdminPage>
      <AdminHero
        eyebrow="Traces"
        title="Trace export and drill-down"
        description="OpenTelemetry and Tempo handle backend trace ownership. This page focuses on sample policy, recent exports, and native search entry points."
      >
        {error ? (
          <div className="rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        ) : null}

        <div className="grid gap-3 xl:grid-cols-3">
          <AdminStat
            label="Tracing Status"
            value={summary?.enabled ? `${Math.round((summary.sample_ratio || 0) * 100)}%` : "Off"}
            hint="Default sample rate"
            accentClassName={summary?.enabled ? "text-cyan-200" : "text-white"}
            icon={<Waypoints className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Slow/Error Priority"
            value={summary ? `${Math.round(summary.slow_request_ms)} ms` : "Awaiting"}
            hint="Slow request threshold"
            accentClassName="text-white"
            icon={<TimerReset className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Trace Search"
            value={grafanaTracesUrl || grafanaUrl ? "Ready" : "Awaiting"}
            hint="Grafana or Tempo handoff"
            accentClassName={grafanaTracesUrl || grafanaUrl ? "text-cyan-200" : "text-white"}
            icon={<ExternalLink className="h-5 w-5 text-cyan-200/80" />}
          />
        </div>
      </AdminHero>

      <AdminSurface title="Tracing surfaces">
        <div className="grid gap-3 xl:grid-cols-3">
          <section className="border-l border-white/8 pl-4">
            <div className="flex items-center gap-3">
              <Waypoints className="h-5 w-5 text-cyan-200/80" />
              <div className="text-sm font-semibold text-white">Tracing Status</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              Backend tracing is currently {summary?.enabled ? "enabled and exporting through OTLP." : "disabled until CARTOSKY_OTEL_ENABLED is turned on in production."}
            </p>
          </section>
          <section className="border-l border-white/8 pl-4">
            <div className="flex items-center gap-3">
              <TimerReset className="h-5 w-5 text-cyan-200/80" />
              <div className="text-sm font-semibold text-white">Slow/Error Priority</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              Slow requests and server errors are always exported, even when the default trace sample rate is lower.
            </p>
          </section>
          <section className="border-l border-white/8 pl-4">
            <div className="flex items-center gap-3">
              <ExternalLink className="h-5 w-5 text-white/76" />
              <div className="text-sm font-semibold text-white">Trace Search</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              {grafanaTracesUrl || grafanaUrl
                ? "Open Grafana trace exploration for sampled requests and drill-down."
                : "Set a Grafana traces URL in env once Tempo and Grafana trace search are configured."}
            </p>
            {grafanaTracesUrl || grafanaUrl ? (
              <a
                href={grafanaTracesUrl ?? grafanaUrl ?? "#"}
                target="_blank"
                rel="noreferrer"
                className="mt-4 inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-2 text-sm font-medium text-white/86 transition hover:bg-white/[0.12]"
              >
                Open Trace Search
                <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
          </section>
        </div>
      </AdminSurface>

      <AdminSurface title="Recent export activity">
        <div className="grid gap-3 xl:grid-cols-3">
          <section className="border-l border-white/8 pl-4">
            <div className="text-sm font-semibold text-white">Recent Exported Traces</div>
            <div className="mt-3 text-3xl font-semibold tracking-tight text-white">
              {summary?.recent.exported_traces ?? 0}
            </div>
          </section>
          <section className="border-l border-white/8 pl-4">
            <div className="text-sm font-semibold text-white">Slow Traces</div>
            <div className="mt-3 text-3xl font-semibold tracking-tight text-white">
              {summary?.recent.slow_traces ?? 0}
            </div>
          </section>
          <section className="border-l border-white/8 pl-4">
            <div className="text-sm font-semibold text-white">Error Traces</div>
            <div className="mt-3 text-3xl font-semibold tracking-tight text-white">
              {summary?.recent.error_traces ?? 0}
            </div>
          </section>
        </div>
      </AdminSurface>

      <AdminSurface title="Collector and correlation">
        <div className="grid gap-3 xl:grid-cols-2">
          <section className="border-l border-white/8 pl-4">
            <div className="flex items-center gap-3">
              <Route className="h-5 w-5 text-cyan-200/80" />
              <div className="text-sm font-semibold text-white">Collector Export</div>
            </div>
            <p className="mt-3 break-all text-sm leading-6 text-white/62">
              {summary?.exporter_endpoint ?? "Not configured"}
            </p>
            {summary?.recent.last_export_error ? (
              <div className="mt-4 rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
                Last export error: {summary.recent.last_export_error}
              </div>
            ) : null}
          </section>
          <section className="border-l border-white/8 pl-4">
            <div className="text-sm font-semibold text-white">Correlation</div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              CartoSky now returns `X-Trace-ID` on traced API responses, which gives us a clean handoff point from frontend sessions into backend drill-down.
            </p>
          </section>
        </div>
      </AdminSurface>

      <AdminSurface title="Recent exported traces">
        <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm text-white/76">
              <thead className="text-white/42">
                <tr>
                  <th className="pb-3 pr-4 font-medium">Trace ID</th>
                  <th className="pb-3 pr-4 font-medium">Route</th>
                  <th className="pb-3 pr-4 font-medium">Export Reason</th>
                  <th className="pb-3 pr-4 font-medium">Duration</th>
                  <th className="pb-3 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {(summary?.traces ?? []).slice(0, 8).map((traceRow) => (
                  <tr key={`${traceRow.trace_id}-${traceRow.ended_at}`} className="border-t border-white/8">
                    <td className="py-3 pr-4 font-mono text-[0.8rem] text-white/86">{traceRow.trace_id}</td>
                    <td className="py-3 pr-4 text-white/68">{traceRow.route ?? traceRow.name}</td>
                    <td className="py-3 pr-4 capitalize text-white/86">{traceRow.decision}</td>
                    <td className="py-3 pr-4 text-white/86">
                      {typeof traceRow.duration_ms === "number" ? `${Math.round(traceRow.duration_ms)} ms` : "n/a"}
                    </td>
                    <td className="py-3 text-white/68">{traceRow.status_code ?? "n/a"}</td>
                  </tr>
                ))}
                {(summary?.traces ?? []).length === 0 ? (
                  <tr>
                    <td colSpan={5} className="py-4 text-white/42">
                      No exported traces yet. Once traced requests cross the sample or slow/error rules, they will appear here.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
        </div>
      </AdminSurface>
    </AdminPage>
  );
}
