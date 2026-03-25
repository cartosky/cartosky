import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ClipboardCheck, Clock3, SearchCheck, X } from "lucide-react";

import {
  fetchAdminStatusQaSummary,
  fetchAdminStatusResults,
  fetchTwfStatus,
  type StatusQaSummaryResponse,
  type StatusResult,
  type TwfStatus,
} from "@/lib/admin-api";

type WindowValue = "24h" | "7d" | "30d";
type ViewFilter = "issues" | "artifacts" | "stale" | "all";

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

function issueTone(result: StatusResult): "pass" | "warning" | "fail" {
  if (result.status === "error") return "fail";
  if (result.status === "warning") return "warning";
  return "pass";
}

function issueLabel(issueType: string): string {
  if (issueType === "artifact_failure") return "Artifact failure";
  if (issueType === "run_stalled") return "Run stalled";
  if (issueType === "run_incomplete") return "Run incomplete";
  if (issueType === "stale_run") return "Stale latest run";
  if (issueType === "manifest_missing") return "Missing manifest";
  if (issueType === "manifest_invalid") return "Invalid manifest";
  return "Healthy";
}

function StatusBadge(props: { tone: "pass" | "warning" | "fail"; label: string }) {
  const className =
    props.tone === "pass"
      ? "border-emerald-400/25 bg-emerald-500/12 text-emerald-100"
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
        "rounded-[24px] border p-5 shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl",
        props.onClick ? "cursor-pointer transition-colors hover:bg-white/[0.03]" : "",
        muted ? "border-white/8 bg-black/18" : "border-white/12 bg-black/28",
        props.active ? "ring-1 ring-emerald-300/30" : "",
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

function filterRows(rows: StatusResult[], view: ViewFilter): StatusResult[] {
  if (view === "all") return rows;
  if (view === "issues") return rows.filter((row) => row.status !== "healthy");
  if (view === "artifacts") return rows.filter((row) => row.issue_type === "artifact_failure" || row.issue_type === "manifest_missing" || row.issue_type === "manifest_invalid");
  return rows.filter((row) => row.issue_type === "stale_run" || row.issue_type === "run_stalled");
}

function viewLabel(view: ViewFilter): string {
  if (view === "issues") return "Open pipeline issues";
  if (view === "artifacts") return "Artifact and manifest failures";
  if (view === "stale") return "Stale or stalled runs";
  return "All retained runs";
}

export default function AdminStatusPage() {
  const [status, setStatus] = useState<TwfStatus | null>(null);
  const [qaSummary, setQaSummary] = useState<StatusQaSummaryResponse | null>(null);
  const [windowValue, setWindowValue] = useState<WindowValue>("30d");
  const [modelFilter, setModelFilter] = useState<string>("all");
  const [viewFilter, setViewFilter] = useState<ViewFilter>("issues");
  const [results, setResults] = useState<StatusResult[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const topScrollRef = useRef<HTMLDivElement | null>(null);
  const tableScrollRef = useRef<HTMLDivElement | null>(null);
  const [tableScrollWidth, setTableScrollWidth] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const authStatus = await fetchTwfStatus();
        if (cancelled) return;
        setStatus(authStatus);
        if (!authStatus.linked || !authStatus.admin) return;

        const [response, nextQaSummary] = await Promise.all([
          fetchAdminStatusResults({
            window: windowValue,
            model: modelFilter,
            limit: 200,
          }),
          fetchAdminStatusQaSummary(),
        ]);
        if (cancelled) return;
        setResults(response.results);
        setQaSummary(nextQaSummary);
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
  const selected = filteredRows.find((item) => item.id === selectedId) ?? results.find((item) => item.id === selectedId) ?? null;

  useEffect(() => {
    if (selectedId !== null && !results.some((item) => item.id === selectedId)) {
      setSelectedId(null);
    }
  }, [results, selectedId]);

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
  const issueRows = results.filter((row) => row.status !== "healthy");
  const artifactRows = results.filter((row) => row.issue_type === "artifact_failure" || row.issue_type === "manifest_missing" || row.issue_type === "manifest_invalid");
  const staleRows = results.filter((row) => row.issue_type === "stale_run" || row.issue_type === "run_stalled");
  const healthyRows = results.filter((row) => row.status === "healthy");
  const emptyStateMessage =
    results.length === 0
      ? "No retained published runs were found for the current window."
      : viewFilter === "issues"
        ? "No operational issues were found in the retained published runs."
        : viewFilter === "artifacts"
          ? "No artifact or manifest failures were found."
          : viewFilter === "stale"
            ? "No stale or stalled latest runs were found."
            : "No rows match the current filters.";

  if (!status?.linked || !status.admin) {
    return (
      <section className="rounded-[28px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        Admin pipeline status appears here after admin access is available.
      </section>
    );
  }

  return (
    <div className="space-y-6">
      <section className="rounded-[32px] border border-white/12 bg-black/28 p-6 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="flex items-start gap-3">
          <div className="rounded-2xl border border-white/12 bg-white/[0.05] p-3">
            <ClipboardCheck className="h-5 w-5 text-[#9dd5bf]" />
          </div>
          <div>
            <div className="text-2xl font-semibold tracking-tight">Pipeline Status</div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-white/62">
              Operational health for retained published runs. This page tracks stale runs, incomplete manifests, and artifact failures from the current pipeline output. It no longer reports map-verification or parity warnings.
            </p>
          </div>
        </div>

        {error ? (
          <div className="mt-5 rounded-2xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        ) : null}

        <div className="mt-6 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <SummaryCard
            title="Retained runs"
            value={results.length}
            accent="text-white"
            icon={SearchCheck}
            hint="click for all"
            onClick={() => setViewFilter("all")}
            active={viewFilter === "all"}
          />
          <SummaryCard
            title="Open issues"
            value={issueRows.length}
            accent="text-amber-300"
            icon={AlertTriangle}
            hint="click to inspect"
            onClick={() => setViewFilter("issues")}
            active={viewFilter === "issues"}
          />
          <SummaryCard
            title="Artifact failures"
            value={artifactRows.length}
            accent="text-rose-300"
            icon={ClipboardCheck}
            hint="manifest or files"
            onClick={() => setViewFilter("artifacts")}
            active={viewFilter === "artifacts"}
          />
          <SummaryCard
            title="Stale or stalled"
            value={staleRows.length}
            accent="text-amber-300"
            icon={Clock3}
            hint="latest run cadence"
            onClick={() => setViewFilter("stale")}
            active={viewFilter === "stale"}
          />
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="text-sm font-semibold text-white">QA Store</div>
            <div className="mt-3 text-[1.9rem] font-semibold tracking-tight text-white">
              {qaSummary?.store_mode === "separate" ? "Separate" : "Shared"}
            </div>
            <div className="mt-2 text-xs uppercase tracking-[0.18em] text-white/42">
              {qaSummary?.store_mode === "separate" ? "Ready for final DB retirement" : "Still sharing legacy telemetry DB"}
            </div>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="text-sm font-semibold text-white">QA Reviews</div>
            <div className="mt-3 text-[1.9rem] font-semibold tracking-tight text-white">
              {qaSummary?.total_reviews ?? 0}
            </div>
            <div className="mt-2 text-xs uppercase tracking-[0.18em] text-white/42">
              {qaSummary?.warning_reviews ?? 0} warning rows
            </div>
          </section>
          <section className="rounded-[24px] border border-white/12 bg-white/[0.04] p-5">
            <div className="text-sm font-semibold text-white">Latest QA Check</div>
            <div className="mt-3 text-[1.15rem] font-semibold tracking-tight text-white">
              {formatTimestamp(qaSummary?.latest_checked_at)}
            </div>
            <div className="mt-2 text-xs uppercase tracking-[0.18em] text-white/42">
              {qaSummary?.distinct_runs ?? 0} distinct runs tracked
            </div>
          </section>
        </div>

        <div className="mt-6 grid gap-3 md:grid-cols-3">
          <label className="space-y-2 text-sm">
            <span className="text-white/62">Window</span>
            <select
              value={windowValue}
              onChange={(event) => setWindowValue(event.target.value as WindowValue)}
              className="w-full rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3 text-white outline-none"
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
              className="w-full rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3 text-white outline-none"
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
              className="w-full rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3 text-white outline-none"
            >
              <option value="issues">Open issues</option>
              <option value="artifacts">Artifact failures</option>
              <option value="stale">Stale or stalled</option>
              <option value="all">All retained runs</option>
            </select>
          </label>
        </div>

        <div className="mt-4 text-sm text-white/48">
          Healthy runs: <span className="text-white/72">{healthyRows.length}</span>. The page scans the latest four retained published runs per model directly from disk.
        </div>
      </section>

      <section className="rounded-[32px] border border-white/12 bg-black/28 p-4 text-white shadow-[0_16px_42px_rgba(0,0,0,0.3)] backdrop-blur-xl">
        <div className="mb-3 flex items-center justify-between gap-3 px-2">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-[#95b1a2]">Current View</div>
            <div className="mt-1 text-sm text-white/58">
              Showing <span className="text-white">{viewLabel(viewFilter)}</span> for retained published runs matching the current filters.
            </div>
          </div>
          <div className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-medium text-white/60">
            {filteredRows.length} rows loaded
          </div>
        </div>

        <div className="mb-3 px-2 text-xs text-white/42">
          Click a row to inspect missing files, incomplete variables, and run timing. Use the top scrollbar to reach the right-side columns while staying at the top of the table.
        </div>

        <div ref={topScrollRef} onScroll={() => syncScroll("top")} className="mb-3 overflow-x-auto px-2">
          <div className="h-3 rounded-full bg-white/[0.04]" style={{ width: tableScrollWidth > 0 ? `${tableScrollWidth}px` : "100%" }} />
        </div>

        <div ref={tableScrollRef} onScroll={() => syncScroll("table")} className="overflow-x-auto pb-2">
          <table className="w-max min-w-[1220px] border-separate border-spacing-y-2 text-left text-sm">
            <thead className="text-white/48">
              <tr>
                <th className="px-3 py-2 font-medium">Model</th>
                <th className="px-3 py-2 font-medium">Run</th>
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
                  <td colSpan={9} className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-8 text-center text-white/48">
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
                      item.id === selectedId ? "bg-emerald-500/10 text-white" : "bg-white/[0.03] text-white/84 hover:bg-white/[0.05]",
                    ].join(" ")}
                  >
                    <td className="rounded-l-2xl border-y border-l border-white/10 px-3 py-3 font-semibold">{item.model_id}</td>
                    <td className="border-y border-white/10 px-3 py-3">{item.run_id}</td>
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
      </section>

      {selected ? (
        <>
          <button type="button" aria-label="Close status details" className="fixed inset-0 z-30 bg-black/45 backdrop-blur-[2px]" onClick={() => setSelectedId(null)} />
          <section className="fixed inset-y-4 right-4 z-40 w-[min(540px,calc(100vw-2rem))] overflow-y-auto rounded-[32px] border border-white/12 bg-[#030711]/95 p-5 text-white shadow-[0_24px_80px_rgba(0,0,0,0.5)] backdrop-blur-xl">
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

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/42">Status</div>
                  <div className="mt-3"><StatusBadge tone={issueTone(selected)} label={selected.status} /></div>
                </div>
                <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/42">Issue type</div>
                  <div className="mt-3"><StatusBadge tone={issueTone(selected)} label={issueLabel(selected.issue_type)} /></div>
                </div>
              </div>

              <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                <div className="text-xs uppercase tracking-[0.22em] text-white/42">Summary</div>
                <div className="mt-3 text-sm leading-6 text-white/78">{selected.summary}</div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/42">Frames</div>
                  <div className="mt-2 text-2xl font-semibold text-white">{selected.available_frames}/{selected.expected_frames}</div>
                  <div className="mt-1 text-sm text-white/60">{formatPercent(selected.completion_pct)} complete</div>
                </div>
                <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/42">Run age</div>
                  <div className="mt-2 text-2xl font-semibold text-white">{selected.run_age_hours.toFixed(1)}h</div>
                  <div className="mt-1 text-sm text-white/60">{selected.latest_for_model ? "Latest retained cycle" : "Historical retained cycle"}</div>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-2xl border border-rose-400/18 bg-rose-500/8 p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-rose-100/70">Missing artifacts</div>
                  <div className="mt-2 text-2xl font-semibold text-rose-100">{selected.missing_artifact_count}</div>
                </div>
                <div className="rounded-2xl border border-rose-400/18 bg-rose-500/8 p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-rose-100/70">Unreadable grids</div>
                  <div className="mt-2 text-2xl font-semibold text-rose-100">{selected.unreadable_artifact_count}</div>
                </div>
                <div className="rounded-2xl border border-amber-400/18 bg-amber-500/8 p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-amber-100/70">Incomplete vars</div>
                  <div className="mt-2 text-2xl font-semibold text-amber-100">{selected.incomplete_variable_count}</div>
                </div>
              </div>

              {selected.incomplete_variables.length > 0 ? (
                <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/42">Incomplete variables</div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {selected.incomplete_variables.map((variableId) => (
                      <StatusBadge key={variableId} tone="warning" label={variableId} />
                    ))}
                  </div>
                </div>
              ) : null}

              {selected.sample_paths.length > 0 ? (
                <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/42">Sample failing paths</div>
                  <div className="mt-3 space-y-3 text-sm text-white/78">
                    {selected.sample_paths.map((sample, index) => (
                      <div key={`${sample.variable_id}-${sample.forecast_hour}-${index}`} className="rounded-xl border border-white/10 bg-black/20 p-3">
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
    </div>
  );
}
