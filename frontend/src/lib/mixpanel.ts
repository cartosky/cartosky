import type { TwfStatus } from "@/lib/admin-api";
import type {
  AnalyticsEventName,
  AnalyticsEventProperties,
} from "@/lib/analytics-types";
import {
  getMixpanelToken,
  getReleaseSha,
  isMixpanelEnabled,
} from "@/lib/config";

type MixpanelModule = typeof import("mixpanel-browser");
type MixpanelInstance = MixpanelModule["default"];

type PendingCapture =
  | {
      type: "pageview";
      pathname: string;
      search: string;
    }
  | {
      type: "event";
      eventName: AnalyticsEventName;
      properties: AnalyticsEventProperties;
    };

let mixpanelClient: MixpanelInstance | null = null;
let initStarted = false;
let initPromise: Promise<void> | null = null;
let lastPageviewKey: string | null = null;
const pendingCaptures: PendingCapture[] = [];

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

function buildCommonProperties(): AnalyticsEventProperties {
  return {
    device_class: getDeviceClass(),
    viewport_bucket: getViewportBucket(),
    release_sha: getReleaseSha(),
  };
}

function enqueueCapture(capture: PendingCapture): void {
  pendingCaptures.push(capture);
}

function sendMixpanelPageview(pathname: string, search: string): void {
  if (!mixpanelClient) {
    return;
  }

  mixpanelClient.track("page_viewed", {
    ...buildCommonProperties(),
    path: pathname,
    search,
    title: typeof document !== "undefined" ? document.title : undefined,
  });
}

function sendMixpanelEvent(
  eventName: AnalyticsEventName,
  properties: AnalyticsEventProperties,
): void {
  if (!mixpanelClient) {
    return;
  }

  mixpanelClient.track(eventName, {
    ...buildCommonProperties(),
    ...properties,
  });
}

function flushPendingCaptures(): void {
  if (!mixpanelClient || pendingCaptures.length === 0) {
    return;
  }

  const queuedCaptures = pendingCaptures.splice(0, pendingCaptures.length);
  for (const capture of queuedCaptures) {
    if (capture.type === "pageview") {
      sendMixpanelPageview(capture.pathname, capture.search);
      continue;
    }
    sendMixpanelEvent(capture.eventName, capture.properties);
  }
}

export function initMixpanelAnalytics(): void {
  if (!isMixpanelEnabled() || initStarted) {
    return;
  }

  initStarted = true;
  initPromise = import("mixpanel-browser")
    .then(({ default: mixpanel }) => {
      mixpanel.init(getMixpanelToken(), {
        persistence: "localStorage",
        autocapture: false,
        track_pageview: false,
      });
      mixpanelClient = mixpanel;
      flushPendingCaptures();
    })
    .catch(() => {
      initStarted = false;
      initPromise = null;
    });
}

export function syncMixpanelAuthStatus(status: TwfStatus): void {
  if (!isMixpanelEnabled()) {
    return;
  }

  if (status.linked !== true || !mixpanelClient || status.member_id == null) {
    return;
  }

  const common = buildCommonProperties();
  mixpanelClient.identify(`twf:${status.member_id}`);
  mixpanelClient.people.set({
    is_logged_in: true,
    device_class: common.device_class,
    viewport_bucket: common.viewport_bucket,
  });
}

export function captureMixpanelPageview(pathname: string, search = ""): void {
  if (!isMixpanelEnabled()) {
    return;
  }

  const pageviewKey = `${pathname}${search}`;
  if (pageviewKey === lastPageviewKey) {
    return;
  }
  lastPageviewKey = pageviewKey;

  if (!mixpanelClient) {
    enqueueCapture({ type: "pageview", pathname, search });
    if (!initStarted && !initPromise) {
      initMixpanelAnalytics();
    }
    return;
  }

  sendMixpanelPageview(pathname, search);
}

export function captureMixpanelEvent(
  eventName: AnalyticsEventName,
  properties: AnalyticsEventProperties = {},
): void {
  if (!isMixpanelEnabled()) {
    return;
  }

  if (!mixpanelClient) {
    enqueueCapture({ type: "event", eventName, properties });
    if (!initStarted && !initPromise) {
      initMixpanelAnalytics();
    }
    return;
  }

  sendMixpanelEvent(eventName, properties);
}