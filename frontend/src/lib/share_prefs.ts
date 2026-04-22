export type SharePrefs = {
  forumMode: "west" | "east" | "other";
  forumId?: number;
  topicId?: number;
  topicTitle?: string;
  topicCache?: SharePrefsTopicCacheEntry[];
};

export type SharePrefsTopicSnapshot = {
  id: number;
  title: string;
  url: string;
  pinned: boolean;
  updated?: string;
  starter?: string;
};

export type SharePrefsTopicCacheEntry = {
  forumId: number;
  topics: SharePrefsTopicSnapshot[];
  selectedTopicId?: number;
  selectedTopicTitle?: string;
  savedAt?: number;
};

const SHARE_PREFS_STORAGE_KEY = "twm.share_prefs.v1";
const MAX_CACHED_TOPIC_LISTS = 4;
const MAX_CACHED_TOPICS_PER_FORUM = 15;

function sanitizePositiveInt(value: unknown): number | undefined {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return undefined;
  }
  return Math.floor(parsed);
}

function sanitizeForumMode(value: unknown): SharePrefs["forumMode"] {
  if (value === "east" || value === "other") {
    return value;
  }
  return "west";
}

function sanitizeTopicTitle(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }
  return trimmed.slice(0, 255);
}

function sanitizeShortText(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }
  return trimmed.slice(0, maxLength);
}

function sanitizeUrl(value: unknown): string {
  if (typeof value !== "string") {
    return "";
  }
  return value.trim().slice(0, 2000);
}

function sanitizeSavedAt(value: unknown): number | undefined {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return undefined;
  }
  return Math.floor(parsed);
}

function sanitizeTopicSnapshot(value: unknown): SharePrefsTopicSnapshot | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const id = sanitizePositiveInt(record.id);
  const title = sanitizeTopicTitle(record.title);
  if (id === undefined || title === undefined) {
    return null;
  }
  return {
    id,
    title,
    url: sanitizeUrl(record.url),
    pinned: record.pinned === true,
    updated: sanitizeShortText(record.updated, 255),
    starter: sanitizeShortText(record.starter, 255),
  };
}

function sanitizeTopicCacheEntry(value: unknown): SharePrefsTopicCacheEntry | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const forumId = sanitizePositiveInt(record.forumId);
  const topics = (Array.isArray(record.topics) ? record.topics : [])
    .map((topic) => sanitizeTopicSnapshot(topic))
    .filter((topic): topic is SharePrefsTopicSnapshot => topic !== null)
    .slice(0, MAX_CACHED_TOPICS_PER_FORUM);
  if (forumId === undefined || topics.length === 0) {
    return null;
  }
  return {
    forumId,
    topics,
    selectedTopicId: sanitizePositiveInt(record.selectedTopicId),
    selectedTopicTitle: sanitizeTopicTitle(record.selectedTopicTitle),
    savedAt: sanitizeSavedAt(record.savedAt),
  };
}

function sanitizeTopicCache(value: unknown): SharePrefsTopicCacheEntry[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const deduped = new Map<number, SharePrefsTopicCacheEntry>();
  for (const entry of value) {
    const sanitized = sanitizeTopicCacheEntry(entry);
    if (sanitized) {
      deduped.set(sanitized.forumId, sanitized);
    }
  }
  if (deduped.size === 0) {
    return undefined;
  }
  return Array.from(deduped.values())
    .sort((left, right) => (right.savedAt ?? 0) - (left.savedAt ?? 0))
    .slice(0, MAX_CACHED_TOPIC_LISTS);
}

function sanitizeSharePrefs(value: unknown): SharePrefs {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { forumMode: "west" };
  }
  const record = value as Record<string, unknown>;
  const prefs: SharePrefs = {
    forumMode: sanitizeForumMode(record.forumMode),
  };
  const forumId = sanitizePositiveInt(record.forumId);
  if (forumId !== undefined) {
    prefs.forumId = forumId;
  }
  const topicId = sanitizePositiveInt(record.topicId);
  if (topicId !== undefined) {
    prefs.topicId = topicId;
  }
  const topicTitle = sanitizeTopicTitle(record.topicTitle);
  if (topicTitle !== undefined) {
    prefs.topicTitle = topicTitle;
  }
  const topicCache = sanitizeTopicCache(record.topicCache);
  if (topicCache !== undefined) {
    prefs.topicCache = topicCache;
  }
  return prefs;
}

export function getSharePrefs(): SharePrefs {
  if (typeof window === "undefined") {
    return { forumMode: "west" };
  }
  try {
    const raw = window.localStorage.getItem(SHARE_PREFS_STORAGE_KEY);
    if (!raw) {
      return { forumMode: "west" };
    }
    const parsed = JSON.parse(raw) as unknown;
    return sanitizeSharePrefs(parsed);
  } catch {
    return { forumMode: "west" };
  }
}

export function setSharePrefs(next: SharePrefs): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    const sanitized = sanitizeSharePrefs({
      ...next,
      topicCache: next.topicCache ?? getSharePrefs().topicCache,
    });
    // TODO: Back this abstraction with server-side TWM account prefs for cross-device sync;
    // keep consumers (modal UI) unchanged.
    window.localStorage.setItem(SHARE_PREFS_STORAGE_KEY, JSON.stringify(sanitized));
  } catch {
    // Ignore storage write errors.
  }
}

export function getSharePrefsTopicCacheEntry(forumId: number): SharePrefsTopicCacheEntry | null {
  if (!Number.isFinite(forumId) || forumId <= 0) {
    return null;
  }
  return getSharePrefs().topicCache?.find((entry) => entry.forumId === Math.floor(forumId)) ?? null;
}

export function setSharePrefsTopicCacheEntry(nextEntry: SharePrefsTopicCacheEntry): void {
  const sanitizedEntry = sanitizeTopicCacheEntry(nextEntry);
  if (!sanitizedEntry) {
    return;
  }
  const prefs = getSharePrefs();
  const nextCache = [sanitizedEntry, ...(prefs.topicCache ?? []).filter((entry) => entry.forumId !== sanitizedEntry.forumId)]
    .sort((left, right) => (right.savedAt ?? 0) - (left.savedAt ?? 0))
    .slice(0, MAX_CACHED_TOPIC_LISTS);
  setSharePrefs({
    ...prefs,
    topicCache: nextCache,
  });
}
