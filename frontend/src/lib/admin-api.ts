import { API_ORIGIN } from "@/lib/config";

export type TwfStatus = {
  linked: boolean;
  admin: boolean;
  member_id?: number;
  display_name?: string;
  photo_url?: string | null;
};

export type PerfMetricSummary = {
  count: number;
  avg_ms: number | null;
  min_ms: number | null;
  max_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  target_ms: number | null;
};

export type PerfSummaryResponse = {
  window: string;
  filters: {
    device: string | null;
    model: string | null;
    variable: string | null;
    latest_runs: number | null;
  };
  metrics: Record<string, PerfMetricSummary>;
};

export type PerfTimeseriesPoint = PerfMetricSummary & {
  bucket_start: string;
};

export type PerfTimeseriesResponse = {
  metric: string;
  window: string;
  bucket: "hour" | "day";
  filters: {
    device: string | null;
    model: string | null;
    variable: string | null;
    latest_runs: number | null;
  };
  points: PerfTimeseriesPoint[];
};

export type PerfBreakdownItem = PerfMetricSummary & {
  key: string;
};

export type PerfBreakdownResponse = {
  metric: string;
  window: string;
  by: string;
  filters: {
    device: string | null;
    model: string | null;
    variable: string | null;
    latest_runs: number | null;
  };
  items: PerfBreakdownItem[];
};

export type UsageSummaryResponse = {
  window: string;
  events: Array<{
    event_name: string;
    count: number;
  }>;
};

export type OverviewMetricSummary = {
  count: number;
  unit: "ms" | "score" | "count";
  avg: number | null;
  min: number | null;
  max: number | null;
  p50: number | null;
  p75: number | null;
  p95: number | null;
  total_value: number;
  good_threshold: number | null;
  needs_improvement_threshold: number | null;
};

export type AdminOverviewSummaryResponse = {
  window: string;
  web_vitals: Record<"lcp" | "inp" | "cls", OverviewMetricSummary>;
  rum_diagnostics: Record<
    | "manifest_fetch_duration"
    | "first_map_render_duration"
    | "first_overlay_visible_duration"
    | "tile_request_failure_count"
    | "animation_stall_count"
    | "frame_drop_bucket",
    OverviewMetricSummary
  >;
};

export type StatusResult = {
  id: string;
  model_id: string;
  run_id: string;
  status: "healthy" | "warning" | "error";
  issue_type: string;
  summary: string;
  latest_for_model: boolean;
  run_timestamp?: number | null;
  run_age_hours: number;
  last_updated_at?: number | null;
  expected_frames: number;
  available_frames: number;
  completion_pct: number;
  missing_artifact_count: number;
  unreadable_artifact_count: number;
  incomplete_variable_count: number;
  incomplete_variables: string[];
  sample_paths: Array<{
    variable_id: string;
    forecast_hour: number;
    issue: string;
    value_grid_path?: string;
    sidecar_path?: string;
    read_error?: string;
  }>;
};

export type StatusResultsResponse = {
  window: string;
  filters: {
    model: string | null;
    status: string | null;
  };
  results: StatusResult[];
};

export type StatusQaSummaryResponse = {
  store_mode: "shared" | "separate";
  db_path: string;
  total_reviews: number;
  warning_reviews: number;
  distinct_runs: number;
  latest_checked_at: number | null;
};

export type AdminObservabilitySummaryResponse = {
  metrics_enabled: boolean;
  http: {
    recent_request_count: number;
    p95_ms: number | null;
    error_rate: number | null;
  };
  sample_cache: {
    point_hit_rate: number | null;
    entries: number;
    hits: number;
    misses: number;
  };
  published_runs: Array<{
    model_id: string;
    run_age_hours: number;
    completion_ratio: number;
  }>;
};

export type AdminTracesSummaryResponse = {
  enabled: boolean;
  service_name: string;
  exporter_endpoint: string;
  sample_ratio: number;
  slow_request_ms: number;
  recent: {
    exported_traces: number;
    slow_traces: number;
    error_traces: number;
    last_trace_at: number | null;
    last_export_error: string | null;
  };
  traces: Array<{
    trace_id: string;
    name: string;
    route: string | null;
    duration_ms: number | null;
    status_code: number | null;
    decision: string;
    ended_at: number;
  }>;
};

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: "include",
    ...init,
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { error?: { message?: string } };
      if (body?.error?.message) {
        message = body.error.message;
      }
    } catch {
      // Ignore parse failures.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export async function fetchTwfStatus(): Promise<TwfStatus> {
  return fetchJson<TwfStatus>(`${API_ORIGIN}/auth/twf/status`);
}

export async function fetchAdminPerfSummary(params: {
  window: string;
  device?: string;
  model?: string;
  variable?: string;
  latestRuns?: number;
}): Promise<PerfSummaryResponse> {
  const search = new URLSearchParams();
  search.set("window", params.window);
  if (params.device && params.device !== "all") search.set("device", params.device);
  if (params.model && params.model !== "all") search.set("model", params.model);
  if (params.variable && params.variable !== "all") search.set("variable", params.variable);
  if (Number.isFinite(params.latestRuns)) search.set("latest_runs", String(params.latestRuns));
  return fetchJson<PerfSummaryResponse>(`${API_ORIGIN}/api/v4/admin/performance/summary?${search.toString()}`);
}

export async function fetchAdminPerfTimeseries(params: {
  metric: string;
  window: string;
  bucket?: string;
  device?: string;
  model?: string;
  variable?: string;
  latestRuns?: number;
}): Promise<PerfTimeseriesResponse> {
  const search = new URLSearchParams();
  search.set("metric", params.metric);
  search.set("window", params.window);
  if (params.bucket) search.set("bucket", params.bucket);
  if (params.device && params.device !== "all") search.set("device", params.device);
  if (params.model && params.model !== "all") search.set("model", params.model);
  if (params.variable && params.variable !== "all") search.set("variable", params.variable);
  if (Number.isFinite(params.latestRuns)) search.set("latest_runs", String(params.latestRuns));
  return fetchJson<PerfTimeseriesResponse>(`${API_ORIGIN}/api/v4/admin/performance/timeseries?${search.toString()}`);
}

export async function fetchAdminPerfBreakdown(params: {
  metric: string;
  by: string;
  window: string;
  device?: string;
  model?: string;
  variable?: string;
  limit?: number;
  latestRuns?: number;
}): Promise<PerfBreakdownResponse> {
  const search = new URLSearchParams();
  search.set("metric", params.metric);
  search.set("by", params.by);
  search.set("window", params.window);
  if (params.limit) search.set("limit", String(params.limit));
  if (params.device && params.device !== "all") search.set("device", params.device);
  if (params.model && params.model !== "all") search.set("model", params.model);
  if (params.variable && params.variable !== "all") search.set("variable", params.variable);
  if (Number.isFinite(params.latestRuns)) search.set("latest_runs", String(params.latestRuns));
  return fetchJson<PerfBreakdownResponse>(`${API_ORIGIN}/api/v4/admin/performance/breakdown?${search.toString()}`);
}

export async function fetchAdminUsageSummary(window: string): Promise<UsageSummaryResponse> {
  const search = new URLSearchParams();
  search.set("window", window);
  return fetchJson<UsageSummaryResponse>(`${API_ORIGIN}/api/v4/admin/usage/summary?${search.toString()}`);
}

export async function fetchAdminOverviewSummary(window: string): Promise<AdminOverviewSummaryResponse> {
  const search = new URLSearchParams();
  search.set("window", window);
  return fetchJson<AdminOverviewSummaryResponse>(`${API_ORIGIN}/api/v4/admin/overview/summary?${search.toString()}`);
}

export async function fetchAdminObservabilitySummary(): Promise<AdminObservabilitySummaryResponse> {
  return fetchJson<AdminObservabilitySummaryResponse>(`${API_ORIGIN}/api/v4/admin/observability/summary`);
}

export async function fetchAdminTracesSummary(): Promise<AdminTracesSummaryResponse> {
  return fetchJson<AdminTracesSummaryResponse>(`${API_ORIGIN}/api/v4/admin/traces/summary`);
}

export async function fetchAdminStatusResults(params: {
  window: string;
  model?: string;
  status?: string;
  limit?: number;
}): Promise<StatusResultsResponse> {
  const search = new URLSearchParams();
  search.set("window", params.window);
  if (params.limit) search.set("limit", String(params.limit));
  if (params.model && params.model !== "all") search.set("model", params.model);
  if (params.status && params.status !== "all") search.set("status", params.status);
  return fetchJson<StatusResultsResponse>(`${API_ORIGIN}/api/v4/admin/status/results?${search.toString()}`);
}

export async function fetchAdminStatusQaSummary(): Promise<StatusQaSummaryResponse> {
  return fetchJson<StatusQaSummaryResponse>(`${API_ORIGIN}/api/v4/admin/status/qa-summary`);
}
