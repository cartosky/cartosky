import { useEffect, useState } from "react";
import { BarChart3, Clapperboard, ExternalLink, Flag, ShieldCheck } from "lucide-react";

import { fetchAdminUsageSummary, fetchTwfStatus, type TwfStatus } from "@/lib/admin-api";
import {
  getPostHogDashboardEmbedUrl,
  getPostHogDashboardUrl,
  getPostHogReplayUrl,
  getPostHogUiHost,
  isLegacyUsageTelemetryEnabled,
  isPostHogEnabled,
  isPostHogReplayEnabled,
} from "@/lib/config";

export default function AdminAnalyticsPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [events, setEvents] = useState<Array<{ event_name: string; count: number }>>([]);
  const [error, setError] = useState<string | null>(null);
  const posthogEnabled = isPostHogEnabled();
  const replayEnabled = isPostHogReplayEnabled();
  const dashboardUrl = getPostHogDashboardUrl();
  const dashboardEmbedUrl = getPostHogDashboardEmbedUrl();
  const replayUrl = getPostHogReplayUrl();
  const uiHost = getPostHogUiHost();
  const legacyUsageEnabled = isLegacyUsageTelemetryEnabled();

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
        if (legacyUsageEnabled) {
          const summary = await fetchAdminUsageSummary("30d");
          if (cancelled) return;
          setEvents(summary.events);
        }
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
              This route is the PostHog launch point inside the CartoSky admin shell. It now provides native status and launch surfaces,
              plus an embedded dashboard when a PostHog embed URL is configured.
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
              <div className="text-sm font-semibold text-white">PostHog Status</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              PostHog analytics are currently {posthogEnabled ? "enabled behind CartoSky feature flags." : "disabled until env configuration is provided."}
              {" "}Autocapture and automatic page navigation capture remain off in favor of a strict CartoSky event contract.
            </p>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <Clapperboard className="h-5 w-5 text-amber-300" />
              <div className="text-sm font-semibold text-white">Replay Surface</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              Session replay remains a native PostHog drill-down flow. Replay is currently {replayEnabled ? "enabled with controlled sampling/error triggers." : "disabled until the replay flag is enabled."}
            </p>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <ShieldCheck className="h-5 w-5 text-[#9dd5bf]" />
              <div className="text-sm font-semibold text-white">Controlled Defaults</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              This integration only sends discrete CartoSky events like viewer open, model/variable/region selection, animation start, legend open, and share clicks. High-frequency render events stay out of PostHog.
            </p>
          </section>
        </div>

        <div className="mt-6 grid gap-4 xl:grid-cols-3">
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <ExternalLink className="h-5 w-5 text-white/76" />
              <div className="text-sm font-semibold text-white">Dashboard Link</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              {dashboardUrl ? "Open the native PostHog dashboard for funnels, retention, and deeper product analytics." : "Add a PostHog dashboard URL in env to enable native deep links from this page."}
            </p>
            {dashboardUrl ? (
              <a
                href={dashboardUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-4 inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2 text-sm text-white transition hover:bg-white/[0.08]"
              >
                Open PostHog Dashboard
                <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <Clapperboard className="h-5 w-5 text-amber-300" />
              <div className="text-sm font-semibold text-white">Replay Link</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              {replayUrl ? "Launch directly into PostHog session replay for sampled or error-triggered viewer sessions." : "Add a replay URL in env when you are ready to link operators into native replay."}
            </p>
            {replayUrl ? (
              <a
                href={replayUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-4 inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2 text-sm text-white transition hover:bg-white/[0.08]"
              >
                Open PostHog Replays
                <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="flex items-center gap-3">
              <BarChart3 className="h-5 w-5 text-sky-300" />
              <div className="text-sm font-semibold text-white">Project Entry</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              {uiHost ? "Use the PostHog project UI for ad hoc exploration and schema management." : "Set a PostHog UI host if you want this page to link to the project root."}
            </p>
            {uiHost ? (
              <a
                href={uiHost}
                target="_blank"
                rel="noreferrer"
                className="mt-4 inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2 text-sm text-white transition hover:bg-white/[0.08]"
              >
                Open PostHog
                <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
          </section>
        </div>
      </section>

      {dashboardEmbedUrl ? (
        <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
          <div className="text-lg font-semibold">Embedded dashboard</div>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-white/62">
            This embed is the main live analytics surface inside CartoSky admin. Replay and ad hoc drill-down still stay in native PostHog.
          </p>
          <div className="mt-5 overflow-hidden rounded-[24px] border border-white/10 bg-black/20">
            <iframe
              src={dashboardEmbedUrl}
              title="PostHog analytics dashboard"
              className="h-[720px] w-full"
              loading="lazy"
            />
          </div>
        </section>
      ) : null}

      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="text-lg font-semibold">Legacy comparison counts</div>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-white/62">
          {legacyUsageEnabled
            ? "Keep these existing first-party counts around for a short validation window only. They are still useful for migration confidence, but PostHog is now the source of truth for product analytics and this section should be removed after the cutoff release."
            : "The legacy first-party usage comparison has been retired for cutoff. PostHog is now the production source of truth for product analytics."}
        </p>

        {legacyUsageEnabled ? (
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
        ) : (
          <div className="mt-6 rounded-2xl border border-emerald-300/20 bg-emerald-500/10 px-4 py-5 text-sm leading-6 text-emerald-100">
            Legacy custom usage writes are disabled. Keep using PostHog dashboards and replay for product analytics workflows.
          </div>
        )}
      </section>

      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="text-lg font-semibold">CartoSky event contract</div>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-white/62">
          Phase 3 intentionally keeps the taxonomy small. These are the events currently eligible for PostHog capture from the viewer.
        </p>
        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {[
            "viewer_opened",
            "model_selected",
            "variable_selected",
            "region_selected",
            "animation_started",
            "legend_opened",
            "share_clicked",
          ].map((eventName) => (
            <div key={eventName} className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4">
              <div className="text-sm font-semibold text-white">{eventName}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
