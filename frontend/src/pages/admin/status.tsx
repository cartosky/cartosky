import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ClipboardCheck, Clock3, SearchCheck, X } from "lucide-react";

import { AdminEmpty, AdminHero, AdminPage, AdminSurface } from "@/components/admin-shell";
import {
  fetchAdminAuthStatus,
  fetchAdminStatusRunDetail,
  fetchAdminStatusResults,
  type StatusResult,
  type TwfStatus,
} from "@/lib/admin-api";
import { formatObservedValidTime, formatRunLabel } from "@/lib/time-axis";

type WindowValue = "24h" | "7d" | "30d";
type ViewFilter = "issues" | "ongoing" | "artifacts" | "stale" | "all";
type StatusTone = "pass" | "info" | "warning" | "fail";

function formatTimestamp(value: number | null | undefined): string {
  if (!value) return "—";
  return new Date(value * 1000).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatPercent(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `${value.toFixed(1)}%`;
}

function issueTone(result: StatusResult): StatusTone {
  if (result.status === "error") return "fail";
  if (result.status === "warning") return "warning";
  if (result.status === "info") return "info";
  return "pass";
}

function issueLabel(issueType: string): string {
  if (issueType === "artifact_failure") return "Artifact failure";
  if (issueType === "run_stalled") return "Run stalled";
  if (issueType === "run_ongoing") return "Run ongoing";
  if (issueType === "run_incomplete") return "Run incomplete";
  if (issueType === "stale_run") return "Stale latest run";
  if (issueType === "bundle_unavailable") return "Bundle unavailable";
  if (issueType === "bundle_stalled") return "Bundle stalled";
  if (issueType === "stale_bundle") return "Stale bundle";
  if (issueType === "delayed_bundle") return "Delayed bundle";
  if (issueType === "manifest_missing") return "Missing manifest";
  if (issueType === "manifest_invalid") return "Invalid manifest";
  return "Healthy";
}

function freshnessTone(state: string | null | undefined): StatusTone {
  if (state === "live") return "pass";
  if (state === "delayed") return "warning";
  if (state === "stale" || state === "unavailable") return "fail";
  return "pass";
}

function StatusBadge(props: { tone: StatusTone; label: string }) {
  const className =
    props.tone === "pass"
      ? "border-emerald-400/25 bg-emerald-500/12 text-emerald-100"
      : props.tone === "info"
        ? "border-sky-400/25 bg-sky-500/12 text-sky-100"
      : props.tone === "warning"
        ? "border-amber-400/25 bg-amber-500/12 text-amber-100"
        : "border-rose-400/25 bg-rose-500/12 text-rose-100";
  return <span className={`inline-flex rounded-full border px-3 py-1 text-[11px] font-medium uppercase tracking-[0.18em] ${className}`}>{props.label}</span>;
}

function SummaryCard(props: {
  title: string;
  value: number;
  accent: string;
  icon: typeof ClipboardCheck;
  hint?: string;
  onClick?: () => void;
  active?: boolean;
}) {
  const muted = props.value === 0;
  const Icon = props.icon;
  return (
    <section
      className={[
        "rounded-[1.15rem] border p-4 shadow-[0_12px_30px_rgba(0,0,0,0.18)]",
        props.onClick ? "cursor-pointer transition-colors hover:bg-white/[0.03]" : "",
        muted ? "border-white/8 bg-white/[0.02]" : "border-white/10 bg-white/[0.03]",
        props.active ? "ring-1 ring-cyan-300/30" : "",
      ].join(" ")}
      onClick={props.onClick}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className={`text-sm font-semibold ${muted ? "text-white/72" : "text-white"}`}>{props.title}</div>
          <div className={`mt-3 text-[2.1rem] font-semibold tracking-tight ${muted ? "text-white/68" : props.accent}`}>{props.value}</div>
          {props.hint ? <div className="mt-2 text-xs uppercase tracking-[0.18em] text-white/38">{props.hint}</div> : null}
        </div>
        <div className={`rounded-2xl border p-3 ${muted ? "border-white/8 bg-white/[0.025]" : "border-white/10 bg-white/[0.05]"}`}>
          <Icon className={`h-5 w-5 ${muted ? "text-white/52" : props.accent}`} />
        </div>
      </div>
    </section>
  );
}

function CompactMetric(props: {
  label: string;
  value: string | number;
  hint?: string;
  accentClassName?: string;
  active?: boolean;
  onClick?: () => void;
}) {
  const content = (
    <div
      className={[
        "border-l pl-4 transition-colors",
        props.active ? "border-cyan-300/40 bg-cyan-400/[0.03]" : "border-white/10",
        props.onClick ? "cursor-pointer hover:border-white/20 hover:bg-white/[0.02]" : "",
      ].join(" ")}
    >
      <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">{props.label}</div>
      <div className={`mt-2 text-[1.6rem] font-semibold tracking-tight ${props.accentClassName ?? "text-white"}`}>{props.value}</div>
      {props.hint ? <div className="mt-2 text-sm leading-6 text-white/58">{props.hint}</div> : null}
    </div>
  );

  if (!props.onClick) return content;
  return (
    <button type="button" onClick={props.onClick} className="w-full text-left">
      {content}
    </button>
  );
}

function filterRows(rows: StatusResult[], view: ViewFilter): StatusResult[] {
  if (view === "all") return rows;
  if (view === "issues") return rows.filter((row) => row.status === "warning" || row.status === "error");
  if (view === "ongoing") return rows.filter((row) => row.issue_type === "run_ongoing");
  if (view === "artifacts") return rows.filter((row) => row.issue_type === "artifact_failure" || row.issue_type === "manifest_missing" || row.issue_type === "manifest_invalid");
  return rows.filter((row) => (
    row.issue_type === "stale_run"
    || row.issue_type === "run_stalled"
    || row.issue_type === "bundle_unavailable"
    || row.issue_type === "bundle_stalled"
    || row.issue_type === "stale_bundle"
    || row.issue_type === "delayed_bundle"
  ));
}

function viewLabel(view: ViewFilter): string {
  if (view === "issues") return "Open pipeline issues";
  if (view === "ongoing") return "Ongoing runs";
  if (view === "artifacts") return "Artifact and manifest failures";
  if (view === "stale") return "Stale or stalled runs";
  return "All retained runs";
}

export default function AdminStatusPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [windowValue, setWindowValue] = useState<WindowValue>("30d");
  const [modelFilter, setModelFilter] = useState<string>("all");
  const [viewFilter, setViewFilter] = useState<ViewFilter>("issues");
  const [results, setResults] = useState<StatusResult[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<StatusResult | null>(null);
  const [selectedDetailLoading, setSelectedDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const topScrollRef = useRef<HTMLDivElement | null>(null);
  const tableScrollRef = useRef<HTMLDivElement | null>(null);
  const [tableScrollWidth, setTableScrollWidth] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const authStatus = await fetchAdminAuthStatus();
        if (cancelled) return;
        setStatus(authStatus);
        if (!authStatus.linked || !authStatus.admin) return;

        const response = await fetchAdminStatusResults({
          window: windowValue,
          model: modelFilter,
          limit: 200,
          includeDetails: false,
        });
        if (cancelled) return;
        setResults(response.results);
        setSelectedDetail(null);
        setError(null);
      } catch (nextError) {
        if (cancelled) return;
        setError(nextError instanceof Error ? nextError.message : "Failed to load pipeline status");
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [windowValue, modelFilter]);

  const filteredRows = useMemo(() => filterRows(results, viewFilter), [results, viewFilter]);
  const selectedSummary = filteredRows.find((item) => item.id === selectedId) ?? results.find((item) => item.id === selectedId) ?? null;
  const selected = selectedDetail && selectedDetail.id === selectedId ? selectedDetail : selectedSummary;

  useEffect(() => {
    if (selectedId !== null && !results.some((item) => item.id === selectedId)) {
      setSelectedId(null);
      setSelectedDetail(null);
    }
  }, [results, selectedId]);

  useEffect(() => {
    let cancelled = false;

    if (!selectedSummary) {
      setSelectedDetail(null);
      setSelectedDetailLoading(false);
      return;
    }

    setSelectedDetail(null);
    setSelectedDetailLoading(true);

    void fetchAdminStatusRunDetail({
      model: selectedSummary.model_id,
      run: selectedSummary.run_id,
    })
      .then((response) => {
        if (cancelled) return;
        setSelectedDetail(response.result);
      })
      .catch(() => {
        if (cancelled) return;
        setSelectedDetail(null);
      })
      .finally(() => {
        if (cancelled) return;
        setSelectedDetailLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedSummary?.id, selectedSummary?.model_id, selectedSummary?.run_id]);

  useEffect(() => {
    function updateScrollWidth() {
      if (!tableScrollRef.current) return;
      setTableScrollWidth(tableScrollRef.current.scrollWidth);
    }
    updateScrollWidth();
    window.addEventListener("resize", updateScrollWidth);
    return () => window.removeEventListener("resize", updateScrollWidth);
  }, [filteredRows]);

  function syncScroll(source: "top" | "table") {
    if (!topScrollRef.current || !tableScrollRef.current) return;
    if (source === "top") {
      tableScrollRef.current.scrollLeft = topScrollRef.current.scrollLeft;
    } else {
      topScrollRef.current.scrollLeft = tableScrollRef.current.scrollLeft;
    }
  }

  const modelOptions = Array.from(new Set(results.map((item) => item.model_id))).sort();
  const issueRows = results.filter((row) => row.status === "warning" || row.status === "error");
  const ongoingRows = results.filter((row) => row.issue_type === "run_ongoing");
  const artifactRows = results.filter((row) => row.issue_type === "artifact_failure" || row.issue_type === "manifest_missing" || row.issue_type === "manifest_invalid");
  const staleRows = results.filter((row) => (
    row.issue_type === "stale_run"
    || row.issue_type === "run_stalled"
    || row.issue_type === "bundle_unavailable"
    || row.issue_type === "bundle_stalled"
    || row.issue_type === "stale_bundle"
    || row.issue_type === "delayed_bundle"
  ));
  const healthyRows = results.filter((row) => row.status === "healthy");
  const emptyStateMessage =
    results.length === 0
      ? "No retained published runs were found for the current window."
      : viewFilter === "issues"
        ? "No operational issues were found in the retained published runs."
        : viewFilter === "ongoing"
          ? "No retained latest runs are currently building."
        : viewFilter === "artifacts"
          ? "No artifact or manifest failures were found."
          : viewFilter === "stale"
            ? "No stale or stalled latest runs were found."
            : "No rows match the current filters.";

  if (!status?.linked || !status.admin) {
    return (
      <AdminEmpty>
        Admin pipeline status appears here after admin access is available.
      </AdminEmpty>
    );
  }

  return (
    <AdminPage>
      <AdminHero
        eyebrow="Pipeline Status"
        title="Retained run health"
      >
        {error ? (
          <div className="mt-4 rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        ) : null}

        <div className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <CompactMetric
              label="Retained runs"
              value={results.length}
              active={viewFilter === "all"}
              onClick={() => setViewFilter("all")}
            />
            <CompactMetric
              label="Open issues"
              value={issueRows.length}
              accentClassName="text-amber-300"
              active={viewFilter === "issues"}
              onClick={() => setViewFilter("issues")}
            />
            <CompactMetric
              label="Ongoing runs"
              value={ongoingRows.length}
              accentClassName="text-sky-300"
              active={viewFilter === "ongoing"}
              onClick={() => setViewFilter("ongoing")}
            />
            <CompactMetric
              label="Artifact failures"
              value={artifactRows.length}
              accentClassName="text-rose-300"
              active={viewFilter === "artifacts"}
              onClick={() => setViewFilter("artifacts")}
            />
            <CompactMetric
              label="Stale or stalled"
              value={staleRows.length}
              accentClassName="text-amber-300"
              active={viewFilter === "stale"}
              onClick={() => setViewFilter("stale")}
            />
          </div>

        </div>

        <div className="mt-6 grid gap-3 md:grid-cols-3">
          <label className="space-y-2 text-sm">
            <span className="text-white/62">Window</span>
            <select
              value={windowValue}
              onChange={(event) => setWindowValue(event.target.value as WindowValue)}
              className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white outline-none"
            >
              <option value="24h">24 hours</option>
              <option value="7d">7 days</option>
              <option value="30d">30 days</option>
            </select>
          </label>
          <label className="space-y-2 text-sm">
            <span className="text-white/62">Model</span>
            <select
              value={modelFilter}
              onChange={(event) => setModelFilter(event.target.value)}
              className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white outline-none"
            >
              <option value="all">All models</option>
              {modelOptions.map((modelId) => (
                <option key={modelId} value={modelId}>
                  {modelId}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-2 text-sm">
            <span className="text-white/62">View</span>
            <select
              value={viewFilter}
              onChange={(event) => setViewFilter(event.target.value as ViewFilter)}
              className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-white outline-none"
            >
              <option value="issues">Open issues</option>
              <option value="ongoing">Ongoing runs</option>
              <option value="artifacts">Artifact failures</option>
              <option value="stale">Stale or stalled</option>
              <option value="all">All retained runs</option>
            </select>
          </label>
        </div>
      </AdminHero>

      <AdminSurface className="p-4" title="Current View" headerRight={<div className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-medium text-white/60">{filteredRows.length} rows</div>}>
        <div ref={topScrollRef} onScroll={() => syncScroll("top")} className="mb-3 overflow-x-auto">
          <div className="h-2 rounded-full bg-white/[0.04]" style={{ width: tableScrollWidth > 0 ? `${tableScrollWidth}px` : "100%" }} />
        </div>

        <div ref={tableScrollRef} onScroll={() => syncScroll("table")} className="overflow-x-auto pb-2">
          <table className="w-max min-w-[1420px] border-separate border-spacing-y-2 text-left text-sm">
            <thead className="text-white/48">
              <tr>
                <th className="px-3 py-2 font-medium">Model</th>
                <th className="px-3 py-2 font-medium">Run</th>
                <th className="px-3 py-2 font-medium">Freshness</th>
                <th className="px-3 py-2 font-medium">Latest Scan</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Issue type</th>
                <th className="px-3 py-2 font-medium">Summary</th>
                <th className="px-3 py-2 font-medium">Frames</th>
                <th className="px-3 py-2 font-medium">Completion</th>
                <th className="px-3 py-2 font-medium">Age</th>
                <th className="px-3 py-2 font-medium">Updated</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.length === 0 ? (
                <tr>
                  <td colSpan={11} className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-8 text-center text-white/48">
                    {emptyStateMessage}
                  </td>
                </tr>
              ) : (
                filteredRows.map((item) => (
                  <tr
                    key={item.id}
                    onClick={() => setSelectedId(item.id)}
                    className={[
                      "cursor-pointer rounded-2xl border transition-colors",
                      item.id === selectedId
                        ? "bg-emerald-500/10 text-white"
                        : item.status === "error"
                        ? "border-rose-400/15 bg-rose-500/[0.06] text-white/84 hover:bg-rose-500/[0.1]"
                        : item.status === "warning"
                          ? "border-amber-400/15 bg-amber-500/[0.05] text-white/84 hover:bg-amber-500/[0.08]"
                          : item.status === "info"
                            ? "border-sky-400/15 bg-sky-500/[0.05] text-white/84 hover:bg-sky-500/[0.08]"
                            : "bg-white/[0.03] text-white/84 hover:bg-white/[0.05]",
                    ].join(" ")}
                  >
                    <td className="rounded-l-2xl border-y border-l border-white/10 px-3 py-3 font-semibold">{item.model_id}</td>
                    <td className="border-y border-white/10 px-3 py-3">{formatRunLabel(item.run_id)}</td>
                    <td className="border-y border-white/10 px-3 py-3">
                      {item.time_axis_mode === "observed" && item.freshness_state ? (
                        <StatusBadge tone={freshnessTone(item.freshness_state)} label={item.freshness_state} />
                      ) : (
                        <span className="text-white/40">—</span>
                      )}
                    </td>
                    <td className="border-y border-white/10 px-3 py-3 text-white/68">
                      {item.time_axis_mode === "observed"
                        ? (formatObservedValidTime(item.latest_scan_valid_time ?? null) ?? "—")
                        : "—"}
                    </td>
                    <td className="border-y border-white/10 px-3 py-3">
                      <StatusBadge tone={issueTone(item)} label={item.status} />
                    </td>
                    <td className="border-y border-white/10 px-3 py-3">
                      <StatusBadge tone={issueTone(item)} label={issueLabel(item.issue_type)} />
                    </td>
                    <td className="max-w-[420px] border-y border-white/10 px-3 py-3 text-white/68">
                      <div className="line-clamp-2">{item.summary}</div>
                    </td>
                    <td className="border-y border-white/10 px-3 py-3">{item.available_frames}/{item.expected_frames}</td>
                    <td className="border-y border-white/10 px-3 py-3">{formatPercent(item.completion_pct)}</td>
                    <td className="border-y border-white/10 px-3 py-3">{item.run_age_hours.toFixed(1)}h</td>
                    <td className="rounded-r-2xl border-y border-r border-white/10 px-3 py-3 text-white/58">{formatTimestamp(item.last_updated_at)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </AdminSurface>

      {selected ? (
        <>
          <button type="button" aria-label="Close status details" className="fixed inset-0 z-30 bg-black/45 backdrop-blur-[2px]" onClick={() => setSelectedId(null)} />
          <section className="fixed inset-y-4 right-4 z-40 w-[min(540px,calc(100vw-2rem))] overflow-y-auto rounded-[1.75rem] border border-white/10 bg-[#081120]/96 p-5 text-white shadow-[0_24px_80px_rgba(0,0,0,0.5)] backdrop-blur-xl">
            <div className="space-y-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.26em] text-[#95b1a2]">Run Details</div>
                  <h2 className="mt-2 text-2xl font-semibold tracking-tight">
                    {selected.model_id} · {selected.run_id}
                  </h2>
                  <p className="mt-1 text-sm text-white/58">
                    {selected.latest_for_model ? "Latest retained run" : "Retained historical run"} · updated {formatTimestamp(selected.last_updated_at)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedId(null)}
                  className="rounded-full border border-white/10 bg-white/[0.04] p-2 text-white/72 transition hover:bg-white/[0.08] hover:text-white"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="grid gap-4 border-t border-white/8 pt-5 sm:grid-cols-2">
                <div className="border-l border-white/10 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Status</div>
                  <div className="mt-3"><StatusBadge tone={issueTone(selected)} label={selected.status} /></div>
                </div>
                <div className="border-l border-white/10 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Issue type</div>
                  <div className="mt-3"><StatusBadge tone={issueTone(selected)} label={issueLabel(selected.issue_type)} /></div>
                </div>
              </div>

              {selected.time_axis_mode === "observed" ? (
                <div className="grid gap-4 border-t border-white/8 pt-5 sm:grid-cols-3">
                  <div className="border-l border-white/10 pl-4">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Freshness</div>
                    <div className="mt-3">
                      <StatusBadge
                        tone={freshnessTone(selected.freshness_state)}
                        label={selected.freshness_state ?? "unknown"}
                      />
                    </div>
                  </div>
                  <div className="border-l border-white/10 pl-4">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Latest scan</div>
                    <div className="mt-2 text-sm leading-6 text-white">
                      {formatObservedValidTime(selected.latest_scan_valid_time ?? null) ?? "—"}
                    </div>
                    <div className="mt-1 text-sm text-white/60">
                      {Number.isFinite(selected.latest_scan_age_minutes)
                        ? `${selected.latest_scan_age_minutes} minutes old`
                        : "Age unavailable"}
                    </div>
                  </div>
                  <div className="border-l border-white/10 pl-4">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Bundle publish</div>
                    <div className="mt-2 text-sm leading-6 text-white">
                      {formatObservedValidTime(selected.bundle_published_at ?? null) ?? "—"}
                    </div>
                    <div className="mt-1 text-sm text-white/60">
                      {Number.isFinite(selected.observation_to_publish_latency_seconds)
                        ? `${Math.round((selected.observation_to_publish_latency_seconds ?? 0) / 60)} min obs-to-publish`
                        : "Latency unavailable"}
                    </div>
                  </div>
                </div>
              ) : null}

              <div className="border-t border-white/8 pt-5">
                <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Summary</div>
                <div className="mt-3 text-sm leading-6 text-white/78">{selected.summary}</div>
              </div>

              <div className="grid gap-4 border-t border-white/8 pt-5 sm:grid-cols-2">
                <div className="border-l border-white/10 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Frames</div>
                  <div className="mt-2 text-2xl font-semibold text-white">{selected.available_frames}/{selected.expected_frames}</div>
                  <div className="mt-1 text-sm text-white/60">{formatPercent(selected.completion_pct)} complete</div>
                </div>
                <div className="border-l border-white/10 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Run age</div>
                  <div className="mt-2 text-2xl font-semibold text-white">{selected.run_age_hours.toFixed(1)}h</div>
                  <div className="mt-1 text-sm text-white/60">{selected.latest_for_model ? "Latest retained cycle" : "Historical retained cycle"}</div>
                </div>
              </div>

              <div className="grid gap-4 border-t border-white/8 pt-5 sm:grid-cols-3">
                <div className="border-l border-rose-400/22 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-rose-100/72">Missing artifacts</div>
                  <div className="mt-2 text-2xl font-semibold text-rose-100">{selected.missing_artifact_count}</div>
                </div>
                <div className="border-l border-rose-400/22 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-rose-100/72">Unreadable artifacts</div>
                  <div className="mt-2 text-2xl font-semibold text-rose-100">{selected.unreadable_artifact_count}</div>
                </div>
                <div className="border-l border-amber-400/22 pl-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-amber-100/72">Incomplete vars</div>
                  <div className="mt-2 text-2xl font-semibold text-amber-100">{selected.incomplete_variable_count}</div>
                </div>
              </div>

              {selected.incomplete_variables.length > 0 ? (
                <div className="border-t border-white/8 pt-5">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Incomplete variables</div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {selected.incomplete_variables.map((variableId) => (
                      <StatusBadge key={variableId} tone="warning" label={variableId} />
                    ))}
                  </div>
                </div>
              ) : null}

              {selectedDetailLoading ? (
                <div className="border-t border-white/8 pt-5 text-sm text-white/56">
                  Loading run diagnostics...
                </div>
              ) : null}

              {!selectedDetailLoading && selected.sample_paths.length > 0 ? (
                <div className="border-t border-white/8 pt-5">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/44">Sample failing paths</div>
                  <div className="mt-3 space-y-3 text-sm text-white/78">
                    {selected.sample_paths.map((sample, index) => (
                      <div key={`${sample.variable_id}-${sample.forecast_hour}-${index}`} className="border-l border-white/10 pl-4">
                        <div className="font-medium text-white">
                          {sample.variable_id} · f{sample.forecast_hour} · {sample.issue}
                        </div>
                        {sample.value_grid_path ? <div className="mt-1 break-all text-white/60">{sample.value_grid_path}</div> : null}
                        {sample.sidecar_path ? <div className="mt-1 break-all text-white/60">{sample.sidecar_path}</div> : null}
                        {sample.read_error ? <div className="mt-1 text-rose-100/78">Read error: {sample.read_error}</div> : null}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </section>
        </>
      ) : null}
    </AdminPage>
  );
}
