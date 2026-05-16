import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CalendarDays, ChevronLeft, ChevronRight, Clock3, MessageSquareText, RefreshCw, Search, Tags } from "lucide-react";

import { AdminEmpty, AdminHero, AdminPage, AdminStat, AdminSurface } from "@/components/admin-shell";
import {
  fetchAdminFeedback,
  fetchTwfStatus,
  type AdminFeedbackItem,
  type AdminFeedbackResponse,
  type FeedbackCategory,
  type TwfStatus,
} from "@/lib/admin-api";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

const CATEGORY_LABELS: Record<FeedbackCategory, string> = {
  bug: "Bug",
  performance: "Performance",
  feature: "Feature",
  data_accuracy: "Data Accuracy",
  ui_ux: "UI / UX",
};

const CATEGORY_OPTIONS: Array<{ value: FeedbackCategory | "all"; label: string }> = [
  { value: "all", label: "All categories" },
  { value: "bug", label: "Bug" },
  { value: "performance", label: "Performance" },
  { value: "feature", label: "Feature" },
  { value: "data_accuracy", label: "Data Accuracy" },
  { value: "ui_ux", label: "UI / UX" },
];

const CATEGORY_ORDER: FeedbackCategory[] = ["bug", "performance", "feature", "data_accuracy", "ui_ux"];

type FeedbackFilters = {
  category: FeedbackCategory | "all";
  since: string;
  until: string;
  displayName: string;
};

const emptyFilters: FeedbackFilters = {
  category: "all",
  since: "",
  until: "",
  displayName: "",
};

function toIsoStartOfDay(value: string): string | undefined {
  return value ? `${value}T00:00:00Z` : undefined;
}

function toIsoEndOfDay(value: string): string | undefined {
  return value ? `${value}T23:59:59Z` : undefined;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function formatChartDate(value: string): string {
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(date);
}

function truncateMessage(message: string): string {
  const compact = message.replace(/\s+/g, " ").trim();
  return compact.length > 120 ? `${compact.slice(0, 120)}...` : compact;
}

function modelHourLabel(item: AdminFeedbackItem): string {
  const model = item.model_context?.trim();
  const hour = item.fhr_context;
  if (model && typeof hour === "number") {
    return `${model} f${hour}`;
  }
  if (model) {
    return model;
  }
  if (typeof hour === "number") {
    return `f${hour}`;
  }
  return "n/a";
}

function buildRequestParams(filters: FeedbackFilters, page: number) {
  return {
    page,
    pageSize: PAGE_SIZE,
    category: filters.category,
    since: toIsoStartOfDay(filters.since),
    until: toIsoEndOfDay(filters.until),
    displayName: filters.displayName,
  };
}

export default function AdminFeedbackPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [feedback, setFeedback] = useState<AdminFeedbackResponse | null>(null);
  const [filters, setFilters] = useState<FeedbackFilters>(emptyFilters);
  const [appliedFilters, setAppliedFilters] = useState<FeedbackFilters>(emptyFilters);
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadStatus() {
      try {
        const authStatus = await fetchTwfStatus();
        if (cancelled) return;
        setStatus(authStatus);
      } catch (nextError) {
        if (cancelled) return;
        setError(nextError instanceof Error ? nextError.message : "Failed to load admin status");
        setStatus({ linked: false, admin: false });
      }
    }

    void loadStatus();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!status?.linked || !status.admin) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    async function loadFeedback() {
      try {
        const response = await fetchAdminFeedback(buildRequestParams(appliedFilters, page));
        if (cancelled) return;
        setFeedback(response);
        setExpandedId(null);
      } catch (nextError) {
        if (cancelled) return;
        setError(nextError instanceof Error ? nextError.message : "Failed to load feedback");
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadFeedback();
    return () => {
      cancelled = true;
    };
  }, [appliedFilters, page, status]);

  const pageCount = Math.max(1, Math.ceil((feedback?.total ?? 0) / PAGE_SIZE));
  const chartData = useMemo(() => (
    (feedback?.daily_volume ?? []).map((point) => ({
      ...point,
      label: formatChartDate(point.date),
    }))
  ), [feedback]);
  const categoryBreakdown = feedback?.summary.by_category ?? {
    bug: 0,
    performance: 0,
    feature: 0,
    data_accuracy: 0,
    ui_ux: 0,
  };
  const topCategory = CATEGORY_ORDER.reduce((best, next) => (
    categoryBreakdown[next] > categoryBreakdown[best] ? next : best
  ), "bug");

  function applyFilters() {
    setAppliedFilters(filters);
    setPage(1);
  }

  function resetFilters() {
    setFilters(emptyFilters);
    setAppliedFilters(emptyFilters);
    setPage(1);
  }

  function refreshFeedback() {
    setAppliedFilters({ ...appliedFilters });
  }

  if (status === null && loading) {
    return <AdminEmpty>Feedback appears here after admin access is available.</AdminEmpty>;
  }

  if (!status?.linked || !status.admin) {
    return <AdminEmpty>Feedback appears here after admin access is available.</AdminEmpty>;
  }

  return (
    <AdminPage>
      <AdminHero
        eyebrow="Beta Feedback"
        title="Feedback triage"
        description="Review public beta submissions, monitor category patterns, and inspect context captured from the viewer and public pages."
        actions={(
          <button
            type="button"
            onClick={refreshFeedback}
            className="inline-flex h-10 items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-4 text-sm font-semibold text-white/82 transition hover:bg-white/[0.08]"
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </button>
        )}
      >
        {error ? (
          <div className="rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        ) : null}

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <AdminStat
            label="Total Feedback"
            value={formatNumber(feedback?.summary.total ?? 0)}
            hint="Matching current filters"
            accentClassName="text-cyan-200"
            icon={<MessageSquareText className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Last 24h"
            value={formatNumber(feedback?.summary.last_24h ?? 0)}
            hint="Recent submissions"
            accentClassName="text-white"
            icon={<Clock3 className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Last 7d"
            value={formatNumber(feedback?.summary.last_7d ?? 0)}
            hint="Beta window signal"
            accentClassName="text-white"
            icon={<CalendarDays className="h-5 w-5 text-cyan-200/80" />}
          />
          <AdminStat
            label="Top Category"
            value={(feedback?.summary.total ?? 0) > 0 ? CATEGORY_LABELS[topCategory] : "No Data"}
            hint="Highest count"
            accentClassName="text-white"
            icon={<Tags className="h-5 w-5 text-cyan-200/80" />}
          />
        </div>
      </AdminHero>

      <AdminSurface title="Filters" description="Filter by category, submitted date, or Weather Forums display name. Summary and chart values update from backend aggregate data.">
        <div className="grid gap-3 lg:grid-cols-[minmax(160px,0.9fr)_minmax(150px,0.8fr)_minmax(150px,0.8fr)_minmax(220px,1.2fr)_auto] lg:items-end">
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-[0.16em] text-white/40">Category</span>
            <select
              value={filters.category}
              onChange={(event) => setFilters((prev) => ({ ...prev, category: event.target.value as FeedbackCategory | "all" }))}
              className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-[#091322] px-3 text-sm text-white outline-none focus:border-cyan-300/34"
            >
              {CATEGORY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-[0.16em] text-white/40">Since</span>
            <input
              type="date"
              value={filters.since}
              onChange={(event) => setFilters((prev) => ({ ...prev, since: event.target.value }))}
              className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-[#091322] px-3 text-sm text-white outline-none focus:border-cyan-300/34"
            />
          </label>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-[0.16em] text-white/40">Until</span>
            <input
              type="date"
              value={filters.until}
              onChange={(event) => setFilters((prev) => ({ ...prev, until: event.target.value }))}
              className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-[#091322] px-3 text-sm text-white outline-none focus:border-cyan-300/34"
            />
          </label>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-[0.16em] text-white/40">Display Name</span>
            <div className="relative mt-2">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-white/36" />
              <input
                type="search"
                value={filters.displayName}
                onChange={(event) => setFilters((prev) => ({ ...prev, displayName: event.target.value }))}
                className="h-10 w-full rounded-lg border border-white/10 bg-[#091322] pl-9 pr-3 text-sm text-white outline-none placeholder:text-white/34 focus:border-cyan-300/34"
                placeholder="Search tester"
              />
            </div>
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={applyFilters}
              className="inline-flex h-10 items-center justify-center rounded-lg border border-cyan-200/28 bg-cyan-300/12 px-4 text-sm font-semibold text-cyan-50 transition hover:bg-cyan-300/16"
            >
              Apply
            </button>
            <button
              type="button"
              onClick={resetFilters}
              className="inline-flex h-10 items-center justify-center rounded-lg border border-white/10 bg-white/[0.04] px-4 text-sm font-semibold text-white/72 transition hover:bg-white/[0.07]"
            >
              Reset
            </button>
          </div>
        </div>
      </AdminSurface>

      <div className="grid gap-5 xl:grid-cols-[1.35fr_0.65fr]">
        <AdminSurface title="Submission volume" description="Daily feedback count for the current filters.">
          <div className="h-[280px]">
            {chartData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}>
                  <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                  <XAxis dataKey="label" stroke="rgba(255,255,255,0.42)" tickLine={false} axisLine={false} fontSize={12} />
                  <YAxis allowDecimals={false} stroke="rgba(255,255,255,0.42)" tickLine={false} axisLine={false} fontSize={12} />
                  <Tooltip
                    cursor={{ fill: "rgba(255,255,255,0.05)" }}
                    contentStyle={{
                      background: "rgba(8,18,32,0.96)",
                      border: "1px solid rgba(255,255,255,0.12)",
                      borderRadius: 12,
                      color: "#fff",
                    }}
                    labelStyle={{ color: "rgba(255,255,255,0.7)" }}
                  />
                  <Bar dataKey="count" name="Submissions" fill="#67d4f5" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center rounded-xl border border-white/8 bg-white/[0.025] text-sm text-white/48">
                No feedback volume for the selected filters.
              </div>
            )}
          </div>
        </AdminSurface>

        <AdminSurface title="Category breakdown">
          <div className="space-y-3">
            {CATEGORY_ORDER.map((categoryKey) => {
              const count = categoryBreakdown[categoryKey] ?? 0;
              const pct = feedback?.summary.total ? Math.round((count / feedback.summary.total) * 100) : 0;
              return (
                <div key={categoryKey}>
                  <div className="flex items-center justify-between gap-3 text-sm">
                    <span className="font-semibold text-white/82">{CATEGORY_LABELS[categoryKey]}</span>
                    <span className="text-white/52">{formatNumber(count)}</span>
                  </div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/[0.06]">
                    <div className="h-full rounded-full bg-cyan-300/70" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </AdminSurface>
      </div>

      <AdminSurface
        title="Submissions"
        description="Expand a row to read the full message and captured browser context."
        headerRight={(
          <div className="text-sm text-white/52">
            Page {page} of {pageCount}
          </div>
        )}
      >
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm text-white/76">
            <thead className="text-white/42">
              <tr>
                <th className="pb-3 pr-4 font-medium">Submitted</th>
                <th className="pb-3 pr-4 font-medium">Category</th>
                <th className="pb-3 pr-4 font-medium">Tester</th>
                <th className="pb-3 pr-4 font-medium">Page</th>
                <th className="pb-3 pr-4 font-medium">Model / Hour</th>
                <th className="pb-3 font-medium">Message</th>
              </tr>
            </thead>
            <tbody>
              {(feedback?.items ?? []).map((item) => {
                const expanded = expandedId === item.id;
                return (
                  <tr
                    key={item.id}
                    className="cursor-pointer border-t border-white/8 align-top transition hover:bg-white/[0.025]"
                    onClick={() => setExpandedId(expanded ? null : item.id)}
                  >
                    <td className="py-3 pr-4 text-white/68">{formatDateTime(item.submitted_at)}</td>
                    <td className="py-3 pr-4">
                      <span className="inline-flex rounded-md border border-cyan-200/16 bg-cyan-300/8 px-2 py-1 text-xs font-semibold text-cyan-100">
                        {CATEGORY_LABELS[item.category]}
                      </span>
                    </td>
                    <td className="py-3 pr-4 text-white/86">{item.forums_display_name}</td>
                    <td className="py-3 pr-4 font-mono text-xs text-white/58">{item.page_context}</td>
                    <td className="py-3 pr-4 text-white/68">{modelHourLabel(item)}</td>
                    <td className="py-3 text-white/80">
                      <div>{expanded ? item.message : truncateMessage(item.message)}</div>
                      {expanded ? (
                        <div className="mt-3 space-y-1 rounded-lg border border-white/8 bg-white/[0.025] px-3 py-2 text-xs text-white/48">
                          <div>App version: {item.app_version ?? "n/a"}</div>
                          <div className="break-all">User agent: {item.user_agent || "n/a"}</div>
                          <div>Member ID: {item.member_id}</div>
                        </div>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
              {!loading && (feedback?.items ?? []).length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-white/42">
                    No feedback matches the selected filters.
                  </td>
                </tr>
              ) : null}
              {loading ? (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-white/42">
                    Loading feedback...
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <div className="mt-4 flex flex-col gap-3 border-t border-white/8 pt-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-sm text-white/48">
            {formatNumber(feedback?.total ?? 0)} matching submissions
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPage((prev) => Math.max(1, prev - 1))}
              disabled={page <= 1 || loading}
              className={cn(
                "inline-flex h-9 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-3 text-sm font-semibold text-white/72 transition hover:bg-white/[0.07]",
                (page <= 1 || loading) && "cursor-not-allowed opacity-45"
              )}
            >
              <ChevronLeft className="h-4 w-4" />
              Previous
            </button>
            <button
              type="button"
              onClick={() => setPage((prev) => Math.min(pageCount, prev + 1))}
              disabled={page >= pageCount || loading}
              className={cn(
                "inline-flex h-9 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-3 text-sm font-semibold text-white/72 transition hover:bg-white/[0.07]",
                (page >= pageCount || loading) && "cursor-not-allowed opacity-45"
              )}
            >
              Next
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </AdminSurface>
    </AdminPage>
  );
}
