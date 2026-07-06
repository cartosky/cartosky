// Shared types + pure helpers for the share modal (share overhaul Phase 2).
// Extracted verbatim from twf-share-modal.tsx — behavior-preserving move only.

import { captureProductAnalyticsEvent, type AnalyticsEventProperties } from "@/lib/analytics";
import type { ShareChannel } from "@/lib/analytics-types";
import type { ScreenshotExportState } from "@/lib/screenshot_export";
import { getSharePrefsTopicCacheEntry, type SharePrefs } from "@/lib/share_prefs";
import { formatObservedCompactTime } from "@/lib/time-axis";

export type SharePayload = {
  permalink: string;
  summary: string;
  detailsSummary?: string;
};

export type TwfStatus =
  | { linked: false }
  | { linked: true; member_id: number; display_name: string; photo_url?: string | null };

export type TwfForum = {
  id: number;
  name: string;
  path?: string;
};

const EXCLUDED_FORUM_IDS = new Set([3, 49]);

export type TwfTopic = {
  id: number;
  title: string;
  url?: string;
  pinned: boolean;
  updated?: string;
  starter?: string;
};

export type ApiErrorInfo = {
  code?: string;
  message: string;
};

export type SharePostResult = {
  postId: number;
  postUrl: string;
  topicId: number;
};

export type ShareTopicResult = {
  topicId: number;
  topicUrl: string;
  title: string;
};

export type ShareMode = "existing" | "new";

export type TopicCacheEntry = {
  topics: TwfTopic[];
  selectedTopicId: number | null;
  selectedTopicTitle: string | null;
};

export const QUICK_FORUMS: Array<{ id: number; label: string }> = [
  { id: 4, label: "West of Rockies" },
  { id: 9, label: "East of Rockies" },
];

export function captureShareCompleted(channel: ShareChannel, extra: AnalyticsEventProperties = {}): void {
  captureProductAnalyticsEvent("share_completed", { success: true, channel, ...extra });
}

export function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function normalizeTwfStatus(value: unknown): TwfStatus {
  if (!isObject(value) || value.linked !== true) {
    return { linked: false };
  }
  const memberId = Number(value.member_id);
  const displayName = typeof value.display_name === "string" ? value.display_name.trim() : "";
  if (!Number.isFinite(memberId) || memberId <= 0 || !displayName) {
    return { linked: false };
  }
  const photoUrl = typeof value.photo_url === "string" && value.photo_url.trim() ? value.photo_url.trim() : undefined;
  return {
    linked: true,
    member_id: memberId,
    display_name: displayName,
    photo_url: photoUrl,
  };
}

export async function readApiError(response: Response): Promise<ApiErrorInfo | null> {
  try {
    const body = (await response.json()) as unknown;
    if (!isObject(body)) {
      return null;
    }
    const err = body.error;
    if (!isObject(err)) {
      return null;
    }
    const message = typeof err.message === "string" ? err.message.trim() : "";
    if (!message) {
      return null;
    }
    const code = typeof err.code === "string" && err.code.trim() ? err.code.trim() : undefined;
    return { code, message };
  } catch {
    return null;
  }
}

function stripForumIdSuffix(label: string): string {
  return label.replace(/\s*\(ID\s+\d+\)\s*$/i, "").trim();
}

export function formatForumLabel(forum: Pick<TwfForum, "name" | "path">): string {
  return stripForumIdSuffix(forum.path ?? forum.name);
}

const FORUM_SORT_PREFIX_ORDER = [
  "The Weather Forums > West of the Rockies",
  "The Weather Forums > East of the Rockies",
  "The Weather Forums > Climate, World Weather, and Earth Sciences",
  "The Weather Forums > The Archive",
  "The Weather Forums > The Storm Wiki",
  "The Weather Forums > Off Topic",
] as const;

function forumSortPriority(forum: Pick<TwfForum, "name" | "path">): number {
  const label = formatForumLabel(forum);
  const directIndex = FORUM_SORT_PREFIX_ORDER.findIndex((prefix) => label === prefix || label.startsWith(`${prefix} > `));
  return directIndex === -1 ? FORUM_SORT_PREFIX_ORDER.length : directIndex;
}

export function normalizeForums(value: unknown): TwfForum[] {
  const list = Array.isArray(value)
    ? value
    : isObject(value) && Array.isArray(value.results)
    ? value.results
    : isObject(value) && Array.isArray(value.forums)
    ? value.forums
    : [];

  const normalized: TwfForum[] = [];
  for (const entry of list) {
    if (!isObject(entry)) {
      continue;
    }
    const id = Number(entry.id);
    if (!Number.isFinite(id) || id <= 0 || EXCLUDED_FORUM_IDS.has(id)) {
      continue;
    }
    const name = typeof entry.name === "string" ? stripForumIdSuffix(entry.name.trim()) : "";
    if (!Number.isFinite(id) || id <= 0 || !name) {
      continue;
    }
    const path = typeof entry.path === "string" && entry.path.trim()
      ? stripForumIdSuffix(entry.path.trim())
      : undefined;
    if (path) {
      normalized.push({ id, name, path });
      continue;
    }
    normalized.push({ id, name });
  }

  normalized.sort((a, b) => {
    const priorityDiff = forumSortPriority(a) - forumSortPriority(b);
    if (priorityDiff !== 0) {
      return priorityDiff;
    }
    return formatForumLabel(a).localeCompare(formatForumLabel(b));
  });
  return normalized;
}

export function normalizeTopics(value: unknown): TwfTopic[] {
  if (!isObject(value) || !Array.isArray(value.results)) {
    return [];
  }
  const normalized: TwfTopic[] = [];
  for (const entry of value.results) {
    if (!isObject(entry)) {
      continue;
    }
    const id = Number(entry.id);
    const title = typeof entry.title === "string" ? entry.title.trim() : "";
    const url = typeof entry.url === "string" ? entry.url.trim() : "";
    if (!Number.isFinite(id) || id <= 0 || !title) {
      continue;
    }
    const updated = typeof entry.updated === "string" && entry.updated.trim() ? entry.updated.trim() : undefined;
    const starter = typeof entry.starter === "string" && entry.starter.trim() ? entry.starter.trim() : undefined;
    const topic: TwfTopic = {
      id,
      title,
      pinned: entry.pinned === true,
    };
    if (url) {
      topic.url = url;
    }
    if (updated) {
      topic.updated = updated;
    }
    if (starter) {
      topic.starter = starter;
    }
    normalized.push(topic);
  }
  return normalized;
}

export function isQuickForumId(forumId: number): boolean {
  return QUICK_FORUMS.some((entry) => entry.id === forumId);
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function resolveMonthlyTopicId(topics: TwfTopic[]): number | null {
  if (topics.length === 0) {
    return null;
  }
  const formatter = new Intl.DateTimeFormat("en-US", { month: "long" });
  const monthsToTry = [0, 1].map((offset) => {
    const date = new Date();
    date.setDate(1);
    date.setMonth(date.getMonth() - offset);
    return {
      monthName: formatter.format(date),
      year: date.getFullYear(),
    };
  });

  for (const candidate of monthsToTry) {
    const rx = new RegExp(`^\\s*${escapeRegex(candidate.monthName)}\\s+${candidate.year}\\b`, "i");
    const match = topics.find((topic) => rx.test(topic.title.replace(/\s+/g, " ")));
    if (match) {
      return match.id;
    }
  }

  const firstPinned = topics.find((topic) => topic.pinned);
  if (firstPinned) {
    return firstPinned.id;
  }
  return topics[0]?.id ?? null;
}

export function topicTitleForId(topics: TwfTopic[], topicId: number | null | undefined): string | null {
  if (!Number.isFinite(topicId) || Number(topicId) <= 0) {
    return null;
  }
  return topics.find((topic) => topic.id === Number(topicId))?.title ?? null;
}

export function resolveTopicSelection(topics: TwfTopic[], preferredTopicId?: number | null): {
  topicId: number | null;
  topicTitle: string | null;
} {
  const preferredTitle = topicTitleForId(topics, preferredTopicId);
  if (preferredTitle) {
    return {
      topicId: Number(preferredTopicId),
      topicTitle: preferredTitle,
    };
  }
  const fallbackTopicId = resolveMonthlyTopicId(topics);
  return {
    topicId: fallbackTopicId,
    topicTitle: topicTitleForId(topics, fallbackTopicId),
  };
}

export function hydratePersistedTopicCacheEntry(forumId: number): TopicCacheEntry | null {
  const persisted = getSharePrefsTopicCacheEntry(forumId);
  if (!persisted) {
    return null;
  }
  return {
    topics: persisted.topics,
    selectedTopicId: persisted.selectedTopicId ?? null,
    selectedTopicTitle: persisted.selectedTopicTitle ?? null,
  };
}

export function forumIdFromPrefs(prefs: SharePrefs): number {
  if (Number.isFinite(prefs.forumId) && Number(prefs.forumId) > 0) {
    return Number(prefs.forumId);
  }
  return prefs.forumMode === "east" ? QUICK_FORUMS[1].id : QUICK_FORUMS[0].id;
}

export function forumModeFromSelection(
  selectedForumId: number,
  showOtherForums: boolean
): SharePrefs["forumMode"] {
  if (showOtherForums || !isQuickForumId(selectedForumId)) {
    return "other";
  }
  return selectedForumId === QUICK_FORUMS[1].id ? "east" : "west";
}

export async function writeClipboard(text: string): Promise<boolean> {
  if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
    return false;
  }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function sanitizeFilenamePart(value: string): string {
  const sanitized = value
    .trim()
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
  return sanitized || "value";
}

export function screenshotFilename(state: ScreenshotExportState): string {
  const observedFramePart =
    state.timeAxisMode === "observed"
      ? sanitizeFilenamePart(formatObservedCompactTime(state.validTimeISO) ?? "observed")
      : null;
  const parts = [
    sanitizeFilenamePart(state.model),
    sanitizeFilenamePart(state.run),
    observedFramePart ?? `fh${Number.isFinite(state.fh) ? Math.max(0, Math.round(state.fh)) : 0}`,
    sanitizeFilenamePart(state.variable.key || state.variable.label),
    sanitizeFilenamePart(state.region?.id ?? "region"),
  ];
  return `cartosky-${parts.join("-")}.png`;
}

export function screenshotUrlForState(permalink: string, state: ScreenshotExportState): string {
  const base = typeof window !== "undefined" ? window.location.origin : "https://cartosky.com";
  const url = new URL(permalink, base);
  const [lng, lat] = state.center;
  if (Number.isFinite(lat)) {
    url.searchParams.set("lat", Number(lat).toFixed(5));
  }
  if (Number.isFinite(lng)) {
    url.searchParams.set("lon", Number(lng).toFixed(5));
  }
  if (Number.isFinite(state.zoom)) {
    url.searchParams.set("z", Number(state.zoom).toFixed(2));
  }
  if (Number.isFinite(state.fh)) {
    url.searchParams.set("fh", String(Math.round(Number(state.fh))));
  }
  return url.toString();
}

export function currentRouteWithSearch(): string {
  if (typeof window === "undefined") {
    return "/viewer";
  }
  return `${window.location.pathname}${window.location.search}` || "/viewer";
}

export function loginRouteForCurrentPage(): string {
  return `/login?${new URLSearchParams({ redirect_url: currentRouteWithSearch() }).toString()}`;
}
