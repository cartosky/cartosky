import { API_ORIGIN, isLegacyPerfTelemetryEnabled, isLegacyUsageTelemetryEnabled } from "@/lib/config";

const TELEMETRY_SESSION_STORAGE_KEY = "twm.telemetry.session_id";

type TelemetryBase = {
  session_id?: string;
  model_id?: string | null;
  variable_id?: string | null;
  run_id?: string | null;
  region_id?: string | null;
  forecast_hour?: number | null;
  meta?: Record<string, unknown> | null;
};

type PerfEventInput = TelemetryBase & {
  event_name:
    | "viewer_first_frame"
    | "frame_change"
    | "loop_start"
    | "scrub_latency"
    | "variable_switch"
    | "tile_fetch"
    | "animation_stall"
    | "loop_manifest_resolve"
    | "loop_decode_ready"
    | "loop_decode_to_commit"
    | "loop_commit_to_visible"
    | "loop_queue_to_visible"
    | "loop_first_visible_paint"
    | "long_task_blocking"
    | "loop_frame_drop_gap";
  duration_ms: number;
};

type UsageEventInput = TelemetryBase & {
  event_name: "model_selected" | "variable_selected" | "region_selected" | "animation_play";
};

type RumMetricInput = TelemetryBase & {
  metric_name:
    | "lcp"
    | "inp"
    | "cls"
    | "manifest_fetch_duration"
    | "first_map_render_duration"
    | "first_overlay_visible_duration"
    | "tile_request_failure_count"
    | "animation_stall_count"
    | "frame_drop_bucket";
  metric_value: number;
  metric_unit: "ms" | "score" | "count";
  sample_rate?: number;
};

function randomId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function stableHash(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function getTelemetrySessionId(): string {
  if (typeof window === "undefined") {
    return "server";
  }
  try {
    const existing = window.localStorage.getItem(TELEMETRY_SESSION_STORAGE_KEY);
    if (existing) {
      return existing;
    }
    const next = randomId();
    window.localStorage.setItem(TELEMETRY_SESSION_STORAGE_KEY, next);
    return next;
  } catch {
    return randomId();
  }
}

export function isSampledSession(sampleRate: number): boolean {
  if (!Number.isFinite(sampleRate) || sampleRate <= 0) {
    return false;
  }
  if (sampleRate >= 1) {
    return true;
  }
  const sessionId = getTelemetrySessionId();
  const bucket = stableHash(sessionId) / 0xffffffff;
  return bucket < sampleRate;
}

function getDeviceType(): "mobile" | "desktop" {
  if (typeof window === "undefined") {
    return "desktop";
  }
  return window.innerWidth < 768 ? "mobile" : "desktop";
}

function getViewportBucket(): string {
  if (typeof window === "undefined") {
    return "server";
  }
  const width = window.innerWidth;
  if (width < 640) return "sm";
  if (width < 768) return "md";
  if (width < 1024) return "lg";
  if (width < 1280) return "xl";
  return "2xl";
}

function enrichPayload<T extends TelemetryBase>(payload: T): T & {
  session_id: string;
  device_type: string;
  viewport_bucket: string;
  page: string;
} {
  const page =
    typeof window === "undefined"
      ? "/"
      : `${window.location.pathname}${window.location.search || ""}`;
  return {
    ...payload,
    session_id: payload.session_id || getTelemetrySessionId(),
    device_type: getDeviceType(),
    viewport_bucket: getViewportBucket(),
    page,
  };
}

function postTelemetry(url: string, payload: Record<string, unknown>) {
  const body = JSON.stringify(payload);
  try {
    if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
      const ok = navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
      if (ok) {
        return;
      }
    }
  } catch {
    // Fall through to fetch.
  }

  void fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
    },
    body,
    keepalive: true,
  }).catch(() => {
    // Best-effort telemetry.
  });
}

export function trackPerfEvent(payload: PerfEventInput): void {
  if (!isLegacyPerfTelemetryEnabled()) {
    return;
  }
  const enriched = enrichPayload(payload);
  if (!Number.isFinite(enriched.duration_ms) || enriched.duration_ms < 0) {
    return;
  }
  postTelemetry(`${API_ORIGIN}/api/v4/telemetry/perf`, enriched);
}

export function trackUsageEvent(payload: UsageEventInput): void {
  if (!isLegacyUsageTelemetryEnabled()) {
    return;
  }
  const enriched = enrichPayload(payload);
  postTelemetry(`${API_ORIGIN}/api/v4/telemetry/usage`, enriched);
}

export function trackRumMetric(payload: RumMetricInput): void {
  const enriched = enrichPayload(payload);
  if (!Number.isFinite(enriched.metric_value) || enriched.metric_value < 0) {
    return;
  }
  if (
    enriched.sample_rate !== undefined
    && (!Number.isFinite(enriched.sample_rate) || enriched.sample_rate <= 0 || enriched.sample_rate > 1)
  ) {
    return;
  }
  postTelemetry(`${API_ORIGIN}/api/v4/telemetry/rum`, enriched);
}
