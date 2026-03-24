import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, BarChart3, ClipboardCheck, Gauge, Waypoints } from "lucide-react";
import { Link } from "react-router-dom";

import {
  fetchAdminStatusResults,
  fetchAdminUsageSummary,
  fetchTwfStatus,
  type StatusResult,
  type TwfStatus,
  type UsageSummaryResponse,
} from "@/lib/admin-api";

function AdminGate(props: {
  status: TwfStatus | null;
  children: React.ReactNode;
  loadingLabel: string;
}) {
  const { status, children, loadingLabel } = props;

  if (status === null) {
    return (
      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        {loadingLabel}
      </section>
    );
  }

  if (!status.linked || !status.admin) {
    return (
      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        Admin access appears here after a linked admin session is available.
      </section>
    );
  }

  return <>{children}</>;
}

function SummaryCard(props: {
  title: string;
  value: string;
  hint: string;
  icon: typeof Gauge;
  accentClassName?: string;
}) {
  const { title, value, hint, icon: Icon, accentClassName = "text-white" } = props;
  return (
    <section className="rounded-[24px] border border-white/12 bg-black/28 p-5 shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
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

function FutureSignalCard(props: { title: string; detail: string; phase: string; icon: typeof Gauge }) {
  const { title, detail, phase, icon: Icon } = props;
  return (
    <section className="rounded-[24px] border border-dashed border-white/12 bg-white/[0.03] p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-white">{title}</div>
          <p className="mt-2 text-sm leading-6 text-white/58">{detail}</p>
          <div className="mt-3 text-xs uppercase tracking-[0.18em] text-white/42">{phase}</div>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/[0.05] p-3">
          <Icon className="h-5 w-5 text-white/70" />
        </div>
      </div>
    </section>
  );
}

export default function AdminOverviewPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [results, setResults] = useState<StatusResult[]>([]);
  const [usage, setUsage] = useState<UsageSummaryResponse["events"]>([]);
  const [error, setError] = useState<string | null>(null);

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

        const [statusResponse, usageResponse] = await Promise.all([
          fetchAdminStatusResults({ window: "30d", limit: 200 }),
          fetchAdminUsageSummary("30d"),
        ]);
        if (cancelled) return;
        setResults(statusResponse.results);
        setUsage(usageResponse.events);
        setError(null);
      } catch (nextError) {
        if (cancelled) return;
        setError(nextError instanceof Error ? nextError.message : "Failed to load admin overview");
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const issueRows = useMemo(() => results.filter((row) => row.status !== "healthy"), [results]);
  const artifactRows = useMemo(
    () => results.filter((row) => row.issue_type === "artifact_failure" || row.issue_type === "manifest_missing" || row.issue_type === "manifest_invalid"),
    [results],
  );
  const staleRows = useMemo(
    () => results.filter((row) => row.issue_type === "stale_run" || row.issue_type === "run_stalled"),
    [results],
  );
  const totalUsageEvents = useMemo(() => usage.reduce((sum, event) => sum + event.count, 0), [usage]);
  const topUsageEvent = usage[0]?.event_name ?? "No usage events yet";

  return (
    <AdminGate status={status} loadingLabel="Loading admin overview...">
      <div className="space-y-6">
        <section className="rounded-[32px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
          <div className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[#95b1a2]">Overview</div>
          <h2 className="mt-2 text-4xl font-semibold tracking-tight">Unified admin shell</h2>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-white/62">
            This page is the Phase 1 command center. Native pipeline and incident signals are available now, while analytics,
            observability, and tracing cards will populate as later phases land.
          </p>

          {error ? (
            <div className="mt-5 rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
              {error}
            </div>
          ) : null}

          <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <SummaryCard title="Open Issues" value={String(issueRows.length)} hint="Retained run warnings and errors" icon={AlertTriangle} accentClassName="text-amber-300" />
            <SummaryCard title="Artifact Failures" value={String(artifactRows.length)} hint="Missing or unreadable artifacts" icon={ClipboardCheck} accentClassName="text-rose-300" />
            <SummaryCard title="Stale / Stalled" value={String(staleRows.length)} hint="Latest runs needing attention" icon={Activity} accentClassName="text-[#9dd5bf]" />
            <SummaryCard title="Usage Events" value={new Intl.NumberFormat("en-US").format(totalUsageEvents)} hint={`Top current signal: ${topUsageEvent}`} icon={BarChart3} accentClassName="text-sky-300" />
          </div>
        </section>

        <section className="grid gap-4 xl:grid-cols-2">
          <FutureSignalCard
            title="Web Vitals Health"
            detail="LCP, INP, and CLS cards will populate here once Phase 2 adds the new frontend truth baseline."
            phase="Phase 2"
            icon={Gauge}
          />
          <FutureSignalCard
            title="Product Analytics"
            detail="PostHog-backed usage summaries, funnels, and replay launch points will appear here under the analytics route in Phase 3."
            phase="Phase 3"
            icon={BarChart3}
          />
          <FutureSignalCard
            title="Service Observability"
            detail="Prometheus and Grafana-backed API, tile, cache, scheduler, and freshness signals will populate after Phase 4."
            phase="Phase 4"
            icon={Activity}
          />
          <FutureSignalCard
            title="Trace Drill-down"
            detail="Tempo-backed trace entrypoints and slow-request correlation will populate after backend tracing is added in Phase 5."
            phase="Phase 5"
            icon={Waypoints}
          />
        </section>

        <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
          <div className="text-lg font-semibold">Available now</div>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-white/62">
            Phase 1 keeps the useful first-party operational surfaces online while the new telemetry stack is built out.
          </p>
          <div className="mt-5 grid gap-3 md:grid-cols-2">
            <Link
              to="/admin/status"
              className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4 text-sm text-white transition hover:bg-white/[0.08]"
            >
              <div className="font-semibold">Pipeline Status</div>
              <div className="mt-2 text-white/60">Current retained-run health, artifact failures, stale runs, and QA-oriented operational visibility.</div>
            </Link>
            <Link
              to="/admin/legacy-performance"
              className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-4 text-sm text-white transition hover:bg-white/[0.08]"
            >
              <div className="font-semibold">Legacy Performance</div>
              <div className="mt-2 text-white/60">Comparison-only custom viewer telemetry retained during migration and no longer treated as primary truth.</div>
            </Link>
          </div>
        </section>
      </div>
    </AdminGate>
  );
}
