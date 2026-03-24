import { useEffect, useState } from "react";
import { ExternalLink, Route, TimerReset } from "lucide-react";

import { fetchTwfStatus, type TwfStatus } from "@/lib/admin-api";
import { isAdminEmbedsEnabled } from "@/lib/config";

export default function AdminTracesPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const embedsEnabled = isAdminEmbedsEnabled();

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const authStatus = await fetchTwfStatus();
        if (cancelled) return;
        setStatus(authStatus);
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
              This route is the Phase 5 entrypoint for tracing. The CartoSky admin shell will launch operators into Tempo or Grafana trace views instead of rebuilding a custom trace explorer.
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
              <TimerReset className="h-5 w-5 text-amber-300" />
              <div className="text-sm font-semibold text-white">Slow Request Drill-down</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              Backend traces will connect slow viewer actions to manifest lookup, cache, raster decode, tile generation, and serialization spans.
            </p>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <Route className="h-5 w-5 text-sky-300" />
              <div className="text-sm font-semibold text-white">Correlation</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              This page will later connect slow sessions and admin incidents back to sampled traces through IDs and deep links.
            </p>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <ExternalLink className="h-5 w-5 text-white/76" />
              <div className="text-sm font-semibold text-white">Embed Surface</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              Trace embeds are currently {embedsEnabled ? "enabled by flag for future phases." : "disabled until tracing infrastructure is deployed."}
            </p>
          </section>
        </div>
      </section>
    </div>
  );
}
