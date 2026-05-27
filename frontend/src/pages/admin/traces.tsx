import { useEffect, useState } from "react";
import { ExternalLink, Route, TimerReset, Waypoints } from "lucide-react";

import { AdminEmpty, AdminHero, AdminPage, AdminStat, AdminSurface } from "@/components/admin-shell";
import { fetchAdminAuthStatus, fetchAdminTracesSummary, type AdminTracesSummaryResponse, type TwfStatus } from "@/lib/admin-api";
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
          fetchAdminAuthStatus(),
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
        title="Trace export"
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
          <section className="flex items-start gap-3 rounded-xl border border-white/8 p-3">
            <Waypoints className="mt-0.5 h-4 w-4 flex-shrink-0 text-cyan-200/80" />
            <div>
              <div className="flex items-center gap-2">
                <div className="text-sm font-semibold text-white">Tracing Status</div>
                <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] ${
                  summary?.enabled ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200" : "border-white/10 bg-white/[0.05] text-white/60"
                }`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${summary?.enabled ? "bg-emerald-400" : "bg-white/30"}`} />
                  {summary?.enabled ? "Exporting via OTLP" : "Disabled"}
                </span>
              </div>
              <div className="mt-1 text-xs text-white/55">{summary?.enabled ? `${Math.round((summary.sample_ratio || 0) * 100)}% sample rate` : "Needs CARTOSKY_OTEL_ENABLED"}</div>
            </div>
          </section>
          <section className="flex items-start gap-3 rounded-xl border border-white/8 p-3">
            <TimerReset className="mt-0.5 h-4 w-4 flex-shrink-0 text-cyan-200/80" />
            <div>
              <div className="text-sm font-semibold text-white">Slow / Error Priority</div>
              <div className="mt-1 text-xs text-white/55">Always exported regardless of sample rate</div>
              <div className="mt-2 rounded-lg border border-sky-400/20 bg-sky-500/[0.08] px-2.5 py-1.5 text-[11px] text-sky-200/90">
                Slow requests (&gt;{summary ? `${Math.round(summary.slow_request_ms)} ms` : "threshold"}) and server errors always export.
              </div>
            </div>
          </section>
          <section className="flex items-start gap-3 rounded-xl border border-white/8 p-3">
            <ExternalLink className="mt-0.5 h-4 w-4 flex-shrink-0 text-white/76" />
            <div>
              <div className="text-sm font-semibold text-white">Trace Search</div>
              <div className="mt-1 text-xs text-white/55">{grafanaTracesUrl || grafanaUrl ? "Grafana trace exploration" : "Set Grafana traces URL in env"}</div>
              {grafanaTracesUrl || grafanaUrl ? (
                <a href={grafanaTracesUrl ?? grafanaUrl ?? "#"} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-cyan-300 hover:text-cyan-200">
                  Open trace search <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
            </div>
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
