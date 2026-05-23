import type { PostHog } from "posthog-js";

import type { TwfStatus } from "@/lib/admin-api";
import {
  getPostHogApiKey,
  getPostHogHost,
  getPostHogReplaySampleRate,
  getReleaseSha,
  isPostHogEnabled,
} from "@/lib/config";
const POSTHOG_EVENT_BUDGET_KEY = "cartosky.posthog.event_count";
const POSTHOG_EVENT_BUDGET = 75;

const ALLOWED_EVENT_NAMES = new Set([
  "$pageview",
  "viewer_opened",
  "viewer_session_ended",
  "forecast_page_viewed",
  "model_selected",
  "variable_selected",
  "region_selected",
  "animation_started",
  "legend_opened",
  "share_clicked",
]);

type ProductAnalyticsEventName =
  | "viewer_opened"
  | "viewer_session_ended"
  | "forecast_page_viewed"
  | "model_selected"
  | "variable_selected"
  | "region_selected"
  | "animation_started"
  | "legend_opened"
  | "share_clicked";

type ProductAnalyticsProperties = {
  model_id?: string | null;
  variable_id?: string | null;
  run_id?: string | null;
  region_id?: string | null;
  forecast_hour?: number | null;
  device_class?: string;
  viewport_bucket?: string;
  release_sha?: string | null;
  is_logged_in?: boolean;
  [key: string]: unknown;
};

/**
 * Lazily-resolved PostHog client instance.
 * `null` until `initPostHogAnalytics` successfully loads and inits `posthog-js`.
 * All public functions guard on this being non-null so callers never need to
 * know whether PostHog has been loaded yet.
 */
let ph: PostHog | null = null;
let initStarted = false;
let lastPageviewKey: string | null = null;

function getDeviceClass(): "mobile" | "desktop" {
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

function readEventCount(): number {
  if (typeof window === "undefined") {
    return 0;
  }
  try {
    return Math.max(0, Number(window.sessionStorage.getItem(POSTHOG_EVENT_BUDGET_KEY) ?? 0) || 0);
  } catch {
    return 0;
  }
}

function writeEventCount(value: number): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.setItem(POSTHOG_EVENT_BUDGET_KEY, String(Math.max(0, value)));
  } catch {
    // Ignore storage failures.
  }
}

function canCaptureEvent(): boolean {
  const nextCount = readEventCount() + 1;
  if (nextCount > POSTHOG_EVENT_BUDGET) {
    return false;
  }
  writeEventCount(nextCount);
  return true;
}

function buildCommonProperties(): ProductAnalyticsProperties {
  return {
    device_class: getDeviceClass(),
    viewport_bucket: getViewportBucket(),
    release_sha: getReleaseSha(),
  };
}

/**
 * Lazily load and initialize PostHog analytics.
 *
 * When `isPostHogEnabled()` is `false` this returns immediately without
 * importing `posthog-js`, keeping the library out of the critical path
 * entirely for disabled sessions.
 */
export function initPostHogAnalytics(): void {
  if (initStarted || !isPostHogEnabled()) {
    return;
  }
  initStarted = true;

  void import("posthog-js").then(({ default: posthog }) => {
    posthog.init(getPostHogApiKey(), {
      api_host: getPostHogHost(),
      autocapture: false,
      capture_pageview: false,
      capture_pageleave: false,
      session_recording: {
        sampleRate: getPostHogReplaySampleRate(),
      },
      person_profiles: "identified_only",
      before_send: (event) => {
        if (!event) {
          return event;
        }
        const eventName = String(event.event ?? "");
        if (
          eventName === "$identify"
          || eventName === "$set"
          || eventName === "$groupidentify"
          || ALLOWED_EVENT_NAMES.has(eventName)
        ) {
          return event;
        }
        return null;
      },
    });

    ph = posthog;
    posthog.register(buildCommonProperties());
  });
}

export function syncPostHogAuthStatus(status: TwfStatus): void {
  if (!ph || !isPostHogEnabled()) {
    return;
  }

  const common = buildCommonProperties();
  ph.register({
    ...common,
    is_logged_in: status.linked === true,
    admin_session: status.admin === true,
  });

  if (status.linked !== true) {
    return;
  }

  const distinctId = `twf:${status.member_id}`;
  const personProps = {
    is_logged_in: true,
    device_class: common.device_class,
    viewport_bucket: common.viewport_bucket,
    admin_session: status.admin === true,
  };
  const personPropsOnce = {
    first_seen_release_sha: common.release_sha,
  };

  if (ph.get_distinct_id() !== distinctId) {
    ph.identify(distinctId, personProps, personPropsOnce);
    return;
  }
  ph.setPersonProperties(personProps, personPropsOnce);
}

export function capturePostHogPageview(pathname: string, search = ""): void {
  if (!ph || !isPostHogEnabled()) {
    return;
  }
  const pageviewKey = `${pathname}${search}`;
  if (pageviewKey === lastPageviewKey) {
    return;
  }
  lastPageviewKey = pageviewKey;
  if (!canCaptureEvent()) {
    return;
  }
  ph.capture("$pageview", {
    ...buildCommonProperties(),
    path: pathname,
    search,
    title: typeof document !== "undefined" ? document.title : undefined,
    current_url: typeof window !== "undefined" ? window.location.href : undefined,
  });
}

export function captureProductAnalyticsEvent(
  eventName: ProductAnalyticsEventName,
  properties: ProductAnalyticsProperties = {},
): void {
  if (!ph || !isPostHogEnabled() || !ALLOWED_EVENT_NAMES.has(eventName)) {
    return;
  }
  if (!canCaptureEvent()) {
    return;
  }
  ph.capture(eventName, {
    ...buildCommonProperties(),
    ...properties,
  });
}
