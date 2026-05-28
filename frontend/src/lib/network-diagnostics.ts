import { trackRumDiagnosticMetric } from "@/lib/rum";

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
  | "contour_fetch_duration";

function nowMs(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

function trimHeaderValue(value: string | null): string | null {
  const normalized = String(value ?? "").trim();
  return normalized.length > 0 ? normalized : null;
}

function parseHeaderInteger(value: string | null): number | null {
  const normalized = trimHeaderValue(value);
  if (!normalized) {
    return null;
  }
  const parsed = Number.parseInt(normalized, 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function safeUrlPath(rawUrl: string): string | null {
  const normalized = String(rawUrl ?? "").trim();
  if (!normalized) {
    return null;
  }
  try {
    return new URL(normalized, typeof window !== "undefined" ? window.location.origin : "https://cartosky.local").pathname;
  } catch {
    return null;
  }
}

export function startNetworkTimer(): number {
  return nowMs();
}

export function trackNetworkFetchDuration(params: {
  metric_name: NetworkDiagnosticMetricName;
  started_at_ms: number;
  response: Response;
  model_id?: string | null;
  variable_id?: string | null;
  run_id?: string | null;
  region_id?: string | null;
  forecast_hour?: number | null;
  meta?: Record<string, unknown> | null;
}): void {
  const durationMs = nowMs() - params.started_at_ms;
  if (!Number.isFinite(durationMs) || durationMs < 0) {
    return;
  }

  const responseMeta: Record<string, unknown> = {
    url_path: safeUrlPath(params.response.url),
    http_status: params.response.status,
    cf_cache_status: trimHeaderValue(params.response.headers.get("CF-Cache-Status")),
    server_timing: trimHeaderValue(params.response.headers.get("Server-Timing")),
    request_id: trimHeaderValue(params.response.headers.get("X-Request-ID")),
    trace_id: trimHeaderValue(params.response.headers.get("X-Trace-ID")),
    cache_control: trimHeaderValue(params.response.headers.get("Cache-Control")),
    age: trimHeaderValue(params.response.headers.get("Age")),
    content_encoding: trimHeaderValue(params.response.headers.get("Content-Encoding")),
    content_length_bytes: parseHeaderInteger(params.response.headers.get("Content-Length")),
  };

  trackRumDiagnosticMetric({
    metric_name: params.metric_name,
    metric_value: durationMs,
    metric_unit: "ms",
    model_id: params.model_id ?? null,
    variable_id: params.variable_id ?? null,
    run_id: params.run_id ?? null,
    region_id: params.region_id ?? null,
    forecast_hour: Number.isFinite(params.forecast_hour) ? Number(params.forecast_hour) : null,
    meta: {
      ...responseMeta,
      ...(params.meta ?? {}),
    },
  });
}

export function trackClientProcessingDuration(params: {
  metric_name: NetworkDiagnosticMetricName;
  duration_ms: number;
  model_id?: string | null;
  variable_id?: string | null;
  run_id?: string | null;
  region_id?: string | null;
  forecast_hour?: number | null;
  meta?: Record<string, unknown> | null;
}): void {
  const durationMs = Number(params.duration_ms);
  if (!Number.isFinite(durationMs) || durationMs < 0) {
    return;
  }

  trackRumDiagnosticMetric({
    metric_name: params.metric_name,
    metric_value: durationMs,
    metric_unit: "ms",
    model_id: params.model_id ?? null,
    variable_id: params.variable_id ?? null,
    run_id: params.run_id ?? null,
    region_id: params.region_id ?? null,
    forecast_hour: Number.isFinite(params.forecast_hour) ? Number(params.forecast_hour) : null,
    meta: params.meta ?? null,
  });
}
