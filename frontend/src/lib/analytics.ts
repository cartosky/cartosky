import type { TwfStatus } from "@/lib/admin-api";
import type { AnalyticsEventName, AnalyticsEventProperties } from "@/lib/analytics-types";
import {
  capturePostHogPageview,
  capturePostHogProductEvent,
  initPostHogAnalytics,
  syncPostHogAuthStatus,
} from "@/lib/posthog";
import {
  captureMixpanelEvent,
  captureMixpanelPageview,
  initMixpanelAnalytics,
  syncMixpanelAuthStatus,
} from "@/lib/mixpanel";

export type { AnalyticsEventName, AnalyticsEventProperties };

export function initAnalytics(): void {
  initPostHogAnalytics();
  initMixpanelAnalytics();
}

export function syncAnalyticsAuthStatus(
  clerkUserId: string | null,
  status: TwfStatus,
  profile?: { email: string | null; name: string | null },
): void {
  syncPostHogAuthStatus(status);
  syncMixpanelAuthStatus(clerkUserId, status, profile);
}

export function captureAnalyticsPageview(pathname: string, search = ""): void {
  capturePostHogPageview(pathname, search);
  captureMixpanelPageview(pathname, search);
}

export function captureProductAnalyticsEvent(
  eventName: AnalyticsEventName,
  properties: AnalyticsEventProperties = {},
): void {
  capturePostHogProductEvent(eventName, properties);
  captureMixpanelEvent(eventName, properties);
}