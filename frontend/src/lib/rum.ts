import { isRumEnabled, isWebVitalsEnabled } from "@/lib/config";
import { getTelemetrySessionId, isSampledSession, trackRumMetric } from "@/lib/telemetry";

const RUM_SAMPLE_RATE = 0.1;

let initialized = false;

function finalizeOnce(fn: () => void): () => void {
  let finalized = false;
  return () => {
    if (finalized) {
      return;
    }
    finalized = true;
    fn();
  };
}

function observeLargestContentfulPaint(onValue: (value: number) => void): PerformanceObserver | null {
  if (typeof PerformanceObserver === "undefined") {
    return null;
  }
  try {
    const observer = new PerformanceObserver((list) => {
      const entries = list.getEntries();
      const last = entries[entries.length - 1];
      if (!last) {
        return;
      }
      onValue(last.startTime);
    });
    observer.observe({ type: "largest-contentful-paint", buffered: true } as PerformanceObserverInit);
    return observer;
  } catch {
    return null;
  }
}

function observeCumulativeLayoutShift(onValue: (value: number) => void): PerformanceObserver | null {
  if (typeof PerformanceObserver === "undefined") {
    return null;
  }
  try {
    let cls = 0;
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const layoutShift = entry as PerformanceEntry & { value?: number; hadRecentInput?: boolean };
        if (layoutShift.hadRecentInput) {
          continue;
        }
        cls += Number(layoutShift.value ?? 0);
        onValue(cls);
      }
    });
    observer.observe({ type: "layout-shift", buffered: true } as PerformanceObserverInit);
    return observer;
  } catch {
    return null;
  }
}

function observeInteractionToNextPaint(onValue: (value: number) => void): PerformanceObserver | null {
  if (typeof PerformanceObserver === "undefined") {
    return null;
  }
  try {
    let inp = 0;
    const interactionMax = new Map<number, number>();
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const eventTiming = entry as PerformanceEntry & { duration?: number; interactionId?: number };
        const interactionId = Number(eventTiming.interactionId ?? 0);
        const duration = Number(eventTiming.duration ?? 0);
        if (!Number.isFinite(duration) || duration <= 0 || interactionId <= 0) {
          continue;
        }
        const current = interactionMax.get(interactionId) ?? 0;
        if (duration <= current) {
          continue;
        }
        interactionMax.set(interactionId, duration);
        if (duration > inp) {
          inp = duration;
          onValue(inp);
        }
      }
    });
    observer.observe({ type: "event", buffered: true, durationThreshold: 40 } as PerformanceObserverInit);
    return observer;
  } catch {
    return null;
  }
}

export function initRumTelemetry(): void {
  if (initialized || typeof window === "undefined" || typeof document === "undefined") {
    return;
  }
  initialized = true;

  const sessionId = getTelemetrySessionId();
  void sessionId;

  if (!isWebVitalsEnabled()) {
    return;
  }

  let lcpValue: number | null = null;
  let inpValue: number | null = null;
  let clsValue: number | null = null;

  const lcpObserver = observeLargestContentfulPaint((value) => {
    lcpValue = value;
  });
  const inpObserver = observeInteractionToNextPaint((value) => {
    inpValue = value;
  });
  const clsObserver = observeCumulativeLayoutShift((value) => {
    clsValue = value;
  });

  const finalize = finalizeOnce(() => {
    lcpObserver?.disconnect();
    inpObserver?.disconnect();
    clsObserver?.disconnect();

    if (Number.isFinite(lcpValue) && (lcpValue as number) >= 0) {
      trackRumMetric({
        metric_name: "lcp",
        metric_value: Number(lcpValue),
        metric_unit: "ms",
        sample_rate: 1,
      });
    }
    if (Number.isFinite(inpValue) && (inpValue as number) >= 0) {
      trackRumMetric({
        metric_name: "inp",
        metric_value: Number(inpValue),
        metric_unit: "ms",
        sample_rate: 1,
      });
    }
    if (Number.isFinite(clsValue) && (clsValue as number) >= 0) {
      trackRumMetric({
        metric_name: "cls",
        metric_value: Number(clsValue),
        metric_unit: "score",
        sample_rate: 1,
      });
    }
  });

  document.addEventListener(
    "visibilitychange",
    () => {
      if (document.visibilityState === "hidden") {
        finalize();
      }
    },
    { passive: true },
  );
  window.addEventListener("pagehide", finalize, { passive: true });
}

export function shouldTrackRumDiagnostics(): boolean {
  return isRumEnabled() && isSampledSession(RUM_SAMPLE_RATE);
}

export function trackRumDiagnosticMetric(params: {
  metric_name:
    | "manifest_fetch_duration"
    | "first_map_render_duration"
    | "first_overlay_visible_duration"
    | "tile_request_failure_count"
    | "animation_stall_count"
    | "frame_drop_bucket";
  metric_value: number;
  metric_unit: "ms" | "count";
  sample_rate?: number;
  model_id?: string | null;
  variable_id?: string | null;
  run_id?: string | null;
  region_id?: string | null;
  forecast_hour?: number | null;
  meta?: Record<string, unknown> | null;
}): void {
  if (!shouldTrackRumDiagnostics()) {
    return;
  }
  trackRumMetric({
    ...params,
    sample_rate: params.sample_rate ?? RUM_SAMPLE_RATE,
  });
}
