// Gate-state event log for diagnosing headless screenshot readiness (share
// modal overhaul, Phase 0). Active only when the page runs in screenshot mode
// (?screenshot=1). The server render (screenshot_service.py) reads
// window.__cartoskyGateLog after the ready wait — including on timeout — and
// emits it alongside the phase-timing telemetry.

export type ScreenshotGateEvent = {
  event: string;
  tMs: number;
  [key: string]: string | number | boolean | null | undefined;
};

declare global {
  interface Window {
    __cartoskyGateLog?: ScreenshotGateEvent[];
  }
}

const MAX_GATE_LOG_EVENTS = 200;

const screenshotMode =
  typeof window !== "undefined" &&
  new URLSearchParams(window.location.search).get("screenshot") === "1";

export function logScreenshotGateEvent(
  event: string,
  detail: Record<string, string | number | boolean | null | undefined> = {},
): void {
  if (!screenshotMode) {
    return;
  }
  const log = (window.__cartoskyGateLog ??= []);
  if (log.length >= MAX_GATE_LOG_EVENTS) {
    return;
  }
  log.push({ event, tMs: Math.round(performance.now()), ...detail });
}
