import { useEffect, useState } from "react";
import { BarChart3, Clapperboard, ExternalLink, Flag } from "lucide-react";

import { fetchAdminUsageSummary, fetchTwfStatus, type TwfStatus } from "@/lib/admin-api";
import { isAdminEmbedsEnabled } from "@/lib/config";

export default function AdminAnalyticsPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [events, setEvents] = useState<Array<{ event_name: string; count: number }>>([]);
  const [error, setError] = useState<string | null>(null);
  const embedsEnabled = isAdminEmbedsEnabled();

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
        const summary = await fetchAdminUsageSummary("30d");
        if (cancelled) return;
        setEvents(summary.events);
      } catch (nextError) {
        if (cancelled) return;
        setError(nextError instanceof Error ? nextError.message : "Failed to load analytics");
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
        Analytics appears here after admin access is available.
      </section>
    );
  }

  return (
    <div className="space-y-6">
      <section className="rounded-[32px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="flex items-start gap-3">
          <div className="rounded-2xl border border-white/12 bg-white/[0.05] p-3">
            <BarChart3 className="h-5 w-5 text-[#9dd5bf]" />
          </div>
          <div>
            <div className="text-2xl font-semibold tracking-tight">Analytics</div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-white/62">
              This route is the future PostHog launch point. For Phase 1 it keeps the admin shell in place and shows the current first-party
              usage counts that seed the v1 event taxonomy.
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
              <Flag className="h-5 w-5 text-sky-300" />
              <div className="text-sm font-semibold text-white">Phase 3 Contract</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              PostHog will own product analytics and replay. This page will later host native summary cards, dashboard embeds, and replay deep links.
            </p>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <Clapperboard className="h-5 w-5 text-amber-300" />
              <div className="text-sm font-semibold text-white">Replay Surface</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              Session replay remains a native PostHog drill-down flow. The CartoSky admin shell will link into it rather than recreate it.
            </p>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <ExternalLink className="h-5 w-5 text-white/76" />
              <div className="text-sm font-semibold text-white">Embeds</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              Admin embeds are currently {embedsEnabled ? "enabled by flag for future phases." : "disabled until future phases are ready."}
            </p>
          </section>
        </div>
      </section>

      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="text-lg font-semibold">Current first-party usage counts</div>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-white/62">
          These existing counts are temporary migration scaffolding and should inform the first PostHog taxonomy, not become the long-term analytics backend.
        </p>

        <div className="mt-6 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {events.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-8 text-sm text-white/48">
              No usage events recorded yet.
            </div>
          ) : (
            events.map((event) => (
              <div key={event.event_name} className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
                <div className="text-sm font-semibold text-white">{event.event_name}</div>
                <div className="mt-3 text-3xl font-semibold tracking-tight text-[#9dd5bf]">{event.count}</div>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
