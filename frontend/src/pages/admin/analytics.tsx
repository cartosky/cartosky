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
        title="Product analytics"
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
            hint={posthogEnabled ? "Feature flags on" : "Needs env config"}
            accentClassName={posthogEnabled ? "text-cyan-200" : "text-white"}
            topAccentClass={posthogEnabled ? "border-t-emerald-400/50" : ""}
            icon={<Flag className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Replay"
            value={replayEnabled ? "Enabled" : "Disabled"}
            hint={replayEnabled ? "Sampling by env" : "Replay off"}
            accentClassName={replayEnabled ? "text-cyan-200" : "text-white"}
            topAccentClass={replayEnabled ? "border-t-emerald-400/50" : ""}
            icon={<Clapperboard className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Event Contract"
            value="Curated"
            hint="Discrete events only"
            accentClassName="text-white"
            icon={<ShieldCheck className="h-5 w-5 text-cyan-200/80" />}
          />
        </div>
      </AdminHero>

      {dashboardEmbedUrl ? (
        <AdminSurface title="Embedded dashboard">
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

      <AdminSurface title="Links &amp; surfaces">
        <div className="grid gap-3 sm:grid-cols-3">
          <section className="flex items-start gap-3 rounded-xl border border-white/8 p-3">
            <Flag className="mt-0.5 h-4 w-4 flex-shrink-0 text-cyan-200/80" />
            <div>
              <div className="text-sm font-semibold text-white">PostHog Status</div>
              <div className="mt-1 text-xs text-white/55">{posthogEnabled ? "Enabled · feature flags active" : "Disabled · needs env config"}</div>
            </div>
          </section>
          <section className="flex items-start gap-3 rounded-xl border border-white/8 p-3">
            <Clapperboard className="mt-0.5 h-4 w-4 flex-shrink-0 text-cyan-200/80" />
            <div>
              <div className="text-sm font-semibold text-white">Replay</div>
              <div className="mt-1 text-xs text-white/55">{replayEnabled ? "Enabled · sampling by env" : "Disabled · replay flag off"}</div>
              {replayUrl ? (
                <a href={replayUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-cyan-300 hover:text-cyan-200">
                  Open replays <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
            </div>
          </section>
          <section className="flex items-start gap-3 rounded-xl border border-white/8 p-3">
            <BarChart3 className="mt-0.5 h-4 w-4 flex-shrink-0 text-cyan-200/80" />
            <div>
              <div className="text-sm font-semibold text-white">Dashboard</div>
              <div className="mt-1 text-xs text-white/55">{dashboardUrl ? "Native PostHog dashboard" : "Set dashboard URL in env"}</div>
              {dashboardUrl ? (
                <a href={dashboardUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-cyan-300 hover:text-cyan-200">
                  Open PostHog <ExternalLink className="h-3 w-3" />
                </a>
              ) : uiHost ? (
                <a href={uiHost} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-cyan-300 hover:text-cyan-200">
                  Open project <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
            </div>
          </section>
        </div>
      </AdminSurface>

      <AdminSurface title="CartoSky event contract" description="Phase 3 intentionally keeps the taxonomy small. These are the events currently eligible for PostHog capture from the viewer and forecast surfaces.">
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          {[
            "viewer_opened",
            "viewer_session_ended",
            "forecast_page_viewed",
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
