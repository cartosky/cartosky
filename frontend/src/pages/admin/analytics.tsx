import { useEffect, useState } from "react";
import { BarChart3, Clapperboard, ExternalLink, Flag, ShieldCheck } from "lucide-react";

import { AdminEmpty, AdminHero, AdminPage, AdminStat, AdminSurface } from "@/components/admin-shell";
import { fetchAdminAuthStatus, type TwfStatus } from "@/lib/admin-api";
import {
  getPostHogDashboardEmbedUrl,
  getPostHogDashboardUrl,
  getPostHogReplayUrl,
  getPostHogUiHost,
  isPostHogEnabled,
  isPostHogReplayEnabled,
} from "@/lib/config";

export default function AdminAnalyticsPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const posthogEnabled = isPostHogEnabled();
  const replayEnabled = isPostHogReplayEnabled();
  const dashboardUrl = getPostHogDashboardUrl();
  const dashboardEmbedUrl = getPostHogDashboardEmbedUrl();
  const replayUrl = getPostHogReplayUrl();
  const uiHost = getPostHogUiHost();

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const authStatus = await fetchAdminAuthStatus();
        if (cancelled) return;
        setStatus(authStatus);
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
      <AdminEmpty>
        Analytics appears here after admin access is available.
      </AdminEmpty>
    );
  }

  return (
    <AdminPage>
      <AdminHero
        eyebrow="Analytics"
        title="Product analytics and replay launch"
        description="PostHog remains the analytics owner inside the CartoSky admin shell. Use this page to confirm enablement, open native surfaces, and embed the main dashboard when configured."
      >
        {error ? (
          <div className="rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        ) : null}

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <AdminStat
            label="PostHog Status"
            value={posthogEnabled ? "Enabled" : "Disabled"}
            hint={posthogEnabled ? "Feature flags armed" : "Needs env configuration"}
            accentClassName={posthogEnabled ? "text-cyan-200" : "text-white"}
            icon={<Flag className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Replay"
            value={replayEnabled ? "Enabled" : "Disabled"}
            hint={replayEnabled ? "Controlled sampling and errors" : "Replay flag is off"}
            accentClassName={replayEnabled ? "text-cyan-200" : "text-white"}
            icon={<Clapperboard className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Event Contract"
            value="Curated"
            hint="Discrete CartoSky events only"
            accentClassName="text-white"
            icon={<ShieldCheck className="h-5 w-5 text-cyan-200/80" />}
          />
        </div>
      </AdminHero>

      <AdminSurface title="Launch surfaces" description="Use these entry points for native PostHog dashboards, replay, and project-level drill-down.">
        <div className="grid gap-3 xl:grid-cols-3">
          <section className="border-l border-white/8 pl-4">
            <div className="flex items-center gap-3">
              <Flag className="h-5 w-5 text-cyan-200/80" />
              <div className="text-sm font-semibold text-white">PostHog Status</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              PostHog analytics are currently {posthogEnabled ? "enabled behind CartoSky feature flags." : "disabled until env configuration is provided."}
              {" "}Autocapture and automatic page navigation capture remain off in favor of a strict CartoSky event contract.
            </p>
          </section>
          <section className="border-l border-white/8 pl-4">
            <div className="flex items-center gap-3">
              <Clapperboard className="h-5 w-5 text-cyan-200/80" />
              <div className="text-sm font-semibold text-white">Replay Surface</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              Session replay remains a native PostHog drill-down flow. Replay is currently {replayEnabled ? "enabled with controlled sampling/error triggers." : "disabled until the replay flag is enabled."}
            </p>
          </section>
          <section className="border-l border-white/8 pl-4">
            <div className="flex items-center gap-3">
              <ShieldCheck className="h-5 w-5 text-cyan-200/80" />
              <div className="text-sm font-semibold text-white">Controlled Defaults</div>
            </div>
            <p className="mt-3 text-sm leading-6 text-white/62">
              This integration only sends discrete CartoSky events like viewer open, model/variable/region selection, animation start, legend open, and share clicks. High-frequency render events stay out of PostHog.
            </p>
          </section>
        </div>
      </AdminSurface>

      <AdminSurface title="Native links">
        <div className="grid gap-3 xl:grid-cols-3">
          <section className="border-l border-white/8 pl-4">
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
                className="mt-4 inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-2 text-sm text-white transition hover:bg-white/[0.08]"
              >
                Open PostHog Dashboard
                <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
          </section>
          <section className="border-l border-white/8 pl-4">
            <div className="flex items-center gap-3">
              <Clapperboard className="h-5 w-5 text-cyan-200/80" />
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
                className="mt-4 inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-2 text-sm text-white transition hover:bg-white/[0.08]"
              >
                Open PostHog Replays
                <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
          </section>
          <section className="border-l border-white/8 pl-4">
            <div className="flex items-center gap-3">
              <BarChart3 className="h-5 w-5 text-cyan-200/80" />
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
                className="mt-4 inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-2 text-sm text-white transition hover:bg-white/[0.08]"
              >
                Open PostHog
                <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
          </section>
        </div>
      </AdminSurface>

      {dashboardEmbedUrl ? (
        <AdminSurface title="Embedded dashboard" description="This embed is the main live analytics surface inside CartoSky admin. Replay and ad hoc drill-down still stay in native PostHog.">
          <div className="overflow-hidden rounded-[1.2rem] border border-white/10 bg-black/20">
            <iframe
              src={dashboardEmbedUrl}
              title="PostHog analytics dashboard"
              className="h-[720px] w-full"
              loading="lazy"
            />
          </div>
        </AdminSurface>
      ) : null}

      <AdminSurface title="CartoSky event contract" description="Phase 3 intentionally keeps the taxonomy small. These are the events currently eligible for PostHog capture from the viewer.">
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          {[
            "viewer_opened",
            "model_selected",
            "variable_selected",
            "region_selected",
            "animation_started",
            "legend_opened",
            "share_clicked",
          ].map((eventName) => (
            <div key={eventName} className="border-l border-white/8 pl-4 py-2">
              <div className="text-sm font-semibold text-white">{eventName}</div>
            </div>
          ))}
        </div>
      </AdminSurface>
    </AdminPage>
  );
}
