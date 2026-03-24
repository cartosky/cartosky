import { useEffect, useState } from "react";
import { ExternalLink, Route, TimerReset, Waypoints } from "lucide-react";

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
      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        Traces appear here after admin access is available.
      </section>
    );
  }

  return (
    <div className="space-y-6">
      <section className="rounded-[32px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="flex items-start gap-3">
          <div className="rounded-2xl border border-white/12 bg-white/[0.05] p-3">
            <Route className="h-5 w-5 text-[#9dd5bf]" />
          </div>
          <div>
            <div className="text-2xl font-semibold tracking-tight">Traces</div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-white/62">
              This route is the tracing launch point inside the CartoSky admin shell. It now shows backend trace rollout health, recent high-signal exports, and native Grafana trace entrypoints.
            </p>
          </div>
        </div>

        {error ? (
          <div className="mt-5 rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        ) : null}

        <div className="mt-6 grid gap-4 xl:grid-cols-3">
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <Waypoints className="h-5 w-5 text-[#9dd5bf]" />
              <div className="text-sm font-semibold text-white">Tracing Status</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              Backend tracing is currently {summary?.enabled ? "enabled and exporting through OTLP." : "disabled until CARTOSKY_OTEL_ENABLED is turned on in production."}
            </p>
            <div className="mt-4 text-3xl font-semibold tracking-tight text-white">
              {summary?.enabled ? `${Math.round((summary.sample_ratio || 0) * 100)}%` : "Off"}
            </div>
            <div className="mt-2 text-[0.68rem] uppercase tracking-[0.28em] text-white/42">
              Default sample rate
            </div>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <TimerReset className="h-5 w-5 text-amber-300" />
              <div className="text-sm font-semibold text-white">Slow/Error Priority</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              Slow requests and server errors are always exported, even when the default trace sample rate is lower.
            </p>
            <div className="mt-4 text-3xl font-semibold tracking-tight text-white">
              {summary ? `${Math.round(summary.slow_request_ms)} ms` : "Awaiting"}
            </div>
            <div className="mt-2 text-[0.68rem] uppercase tracking-[0.28em] text-white/42">
              Slow request threshold
            </div>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
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
                className="mt-4 inline-flex items-center gap-2 rounded-2xl border border-white/12 bg-white/[0.06] px-4 py-2 text-sm font-medium text-white/86 transition hover:bg-white/[0.12]"
              >
                Open Trace Search
                <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
          </section>
        </div>

        <div className="mt-6 grid gap-4 xl:grid-cols-3">
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="text-sm font-semibold text-white">Recent Exported Traces</div>
            <div className="mt-3 text-3xl font-semibold tracking-tight text-white">
              {summary?.recent.exported_traces ?? 0}
            </div>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="text-sm font-semibold text-white">Slow Traces</div>
            <div className="mt-3 text-3xl font-semibold tracking-tight text-white">
              {summary?.recent.slow_traces ?? 0}
            </div>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="text-sm font-semibold text-white">Error Traces</div>
            <div className="mt-3 text-3xl font-semibold tracking-tight text-white">
              {summary?.recent.error_traces ?? 0}
            </div>
          </section>
        </div>

        <div className="mt-6 grid gap-4 xl:grid-cols-2">
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <Route className="h-5 w-5 text-sky-300" />
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
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="text-sm font-semibold text-white">Correlation</div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              CartoSky now returns `X-Trace-ID` on traced API responses, which gives us a clean handoff point from frontend sessions into backend drill-down.
            </p>
          </section>
        </div>

        <section className="mt-6 rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
          <div className="text-sm font-semibold text-white">Recent Exported Traces</div>
          <div className="mt-4 overflow-x-auto">
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
        </section>
      </section>
    </div>
  );
}
