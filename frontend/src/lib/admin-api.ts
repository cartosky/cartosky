import { API_ORIGIN } from "@/lib/config";

export type TwfStatus = {
  linked: boolean;
  admin: boolean;
  member_id?: number;
  display_name?: string;
  photo_url?: string | null;
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
    | "bootstrap_fetch_duration"
    | "capabilities_fetch_duration"
    | "regions_fetch_duration"
    | "frames_fetch_duration"
    | "grid_manifest_fetch_duration"
    | "grid_binary_fetch_duration"
    | "grid_binary_array_buffer_duration"
    | "grid_texture_prepare_duration"
    | "grid_texture_upload_duration"
    | "grid_webgl1_expand_duration"
    | "sample_request_duration"
    | "sample_batch_request_duration"
    | "contour_fetch_duration"
    | "vector_fetch_duration"
    | "first_map_render_duration"
    | "first_overlay_visible_duration"
    | "tile_request_failure_count"
    | "animation_stall_count"
    | "frame_drop_bucket",
    OverviewMetricSummary
  >;
  telemetry_health: {
    web_vitals_last_seen_at: number | null;
    rum_last_seen_at: number | null;
    web_vitals_sample_count: number;
    rum_sample_count: number;
  };
};

export type NetworkDiagnosticMetricName =
  | "bootstrap_fetch_duration"
  | "capabilities_fetch_duration"
  | "regions_fetch_duration"
  | "manifest_fetch_duration"
  | "frames_fetch_duration"
  | "grid_manifest_fetch_duration"
  | "grid_binary_fetch_duration"
  | "grid_binary_array_buffer_duration"
  | "grid_texture_prepare_duration"
  | "grid_texture_upload_duration"
  | "grid_webgl1_expand_duration"
  | "sample_request_duration"
  | "sample_batch_request_duration"
  | "contour_fetch_duration"
  | "vector_fetch_duration";

export type NetworkDiagnosticBreakdown = OverviewMetricSummary & {
  key: string;
};

export type AdminNetworkDiagnosticsResponse = {
  window: string;
  metrics: Array<{
    metric_name: NetworkDiagnosticMetricName;
    label: string;
    summary: OverviewMetricSummary;
    last_seen_at: number | null;
    by_cf_cache_status: NetworkDiagnosticBreakdown[];
    by_model_id: NetworkDiagnosticBreakdown[];
    by_device_type: NetworkDiagnosticBreakdown[];
    by_webgl_backend: NetworkDiagnosticBreakdown[];
    by_content_encoding: NetworkDiagnosticBreakdown[];
    by_payload_size_bucket: NetworkDiagnosticBreakdown[];
  }>;
};

export type StatusResult = {
  id: string;
  model_id: string;
  run_id: string;
  time_axis_mode?: "forecast" | "observed" | "valid";
  status: "healthy" | "warning" | "error";
  issue_type: string;
  summary: string;
  latest_for_model: boolean;
  run_timestamp?: number | null;
  run_age_hours: number;
  last_updated_at?: number | null;
  latest_scan_valid_time?: string | null;
  latest_scan_age_minutes?: number | null;
  bundle_published_at?: string | null;
  bundle_age_seconds?: number | null;
  freshness_state?: "live" | "delayed" | "stale" | "unavailable" | null;
  usable?: boolean | null;
  degraded_reason?: string | null;
  observation_to_publish_latency_seconds?: number | null;
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
    artifact_path?: string;
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

export type StatusRunDetailResponse = {
  result: StatusResult;
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
    freshness_state?: "live" | "delayed" | "stale" | "unavailable" | null;
    latest_scan_age_minutes?: number | null;
    usable?: boolean | null;
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

export type FeedbackCategory = "bug" | "performance" | "feature" | "data_accuracy" | "ui_ux";

export type AdminFeedbackItem = {
  id: number;
  submitted_at: string;
  category: FeedbackCategory;
  message: string;
  member_id: number;
  forums_display_name: string;
  page_context: string;
  model_context: string | null;
  fhr_context: number | null;
  user_agent: string;
  app_version: string | null;
};

export type AdminFeedbackResponse = {
  items: AdminFeedbackItem[];
  page: number;
  page_size: number;
  total: number;
  summary: {
    total: number;
    last_24h: number;
    last_7d: number;
    by_category: Record<FeedbackCategory, number>;
  };
  daily_volume: Array<{
    date: string;
    count: number;
  }>;
  filters: {
    category: FeedbackCategory | null;
    since: string | null;
    until: string | null;
    display_name: string | null;
  };
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

export async function fetchAdminOverviewSummary(window: string): Promise<AdminOverviewSummaryResponse> {
  const search = new URLSearchParams();
  search.set("window", window);
  return fetchJson<AdminOverviewSummaryResponse>(`${API_ORIGIN}/api/v4/admin/overview/summary?${search.toString()}`);
}

export async function fetchAdminNetworkDiagnostics(window: string): Promise<AdminNetworkDiagnosticsResponse> {
  const search = new URLSearchParams();
  search.set("window", window);
  return fetchJson<AdminNetworkDiagnosticsResponse>(`${API_ORIGIN}/api/v4/admin/overview/network-diagnostics?${search.toString()}`);
}

export async function fetchAdminObservabilitySummary(): Promise<AdminObservabilitySummaryResponse> {
  return fetchJson<AdminObservabilitySummaryResponse>(`${API_ORIGIN}/api/v4/admin/observability/summary`);
}

export async function fetchAdminTracesSummary(): Promise<AdminTracesSummaryResponse> {
  return fetchJson<AdminTracesSummaryResponse>(`${API_ORIGIN}/api/v4/admin/traces/summary`);
}

export async function fetchAdminFeedback(params: {
  page: number;
  pageSize: number;
  category?: FeedbackCategory | "all";
  since?: string;
  until?: string;
  displayName?: string;
}): Promise<AdminFeedbackResponse> {
  const search = new URLSearchParams();
  search.set("page", String(params.page));
  search.set("page_size", String(params.pageSize));
  if (params.category && params.category !== "all") search.set("category", params.category);
  if (params.since?.trim()) search.set("since", params.since.trim());
  if (params.until?.trim()) search.set("until", params.until.trim());
  if (params.displayName?.trim()) search.set("display_name", params.displayName.trim());
  return fetchJson<AdminFeedbackResponse>(`${API_ORIGIN}/api/v4/admin/feedback?${search.toString()}`);
}

export async function fetchAdminStatusResults(params: {
  window: string;
  model?: string;
  status?: string;
  limit?: number;
  includeDetails?: boolean;
}): Promise<StatusResultsResponse> {
  const search = new URLSearchParams();
  search.set("window", params.window);
  if (params.limit) search.set("limit", String(params.limit));
  if (params.model && params.model !== "all") search.set("model", params.model);
  if (params.status && params.status !== "all") search.set("status", params.status);
  if (params.includeDetails) search.set("include_details", "true");
  return fetchJson<StatusResultsResponse>(`${API_ORIGIN}/api/v4/admin/status/results?${search.toString()}`);
}

export async function fetchAdminStatusRunDetail(params: {
  model: string;
  run: string;
}): Promise<StatusRunDetailResponse> {
  const search = new URLSearchParams();
  search.set("model", params.model);
  search.set("run", params.run);
  return fetchJson<StatusRunDetailResponse>(`${API_ORIGIN}/api/v4/admin/status/run?${search.toString()}`);
}

export async function fetchAdminStatusQaSummary(): Promise<StatusQaSummaryResponse> {
  return fetchJson<StatusQaSummaryResponse>(`${API_ORIGIN}/api/v4/admin/status/qa-summary`);
}
