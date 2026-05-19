import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@clerk/react";
import { CheckCircle2, ChevronDown, Copy, Download, ExternalLink, Loader2, RefreshCw, X } from "lucide-react";
import { Link } from "react-router-dom";

import type { LegendPayload } from "@/components/map-legend";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { clerkJwtTemplate } from "@/lib/admin-api";
import { API_ORIGIN } from "@/lib/config";
import type { ScreenshotExportState } from "@/lib/screenshot_export";
import { uploadShareMedia } from "@/lib/share_media";
import {
  getSharePrefs,
  getSharePrefsTopicCacheEntry,
  setSharePrefs,
  setSharePrefsTopicCacheEntry,
  type SharePrefs,
} from "@/lib/share_prefs";
import { formatObservedCompactTime } from "@/lib/time-axis";

export type SharePayload = {
  permalink: string;
  summary: string;
  detailsSummary?: string;
};

type TwfStatus =
  | { linked: false }
  | { linked: true; member_id: number; display_name: string; photo_url?: string | null };

type TwfForum = {
  id: number;
  name: string;
  path?: string;
};

type TwfTopic = {
  id: number;
  title: string;
  url: string;
  pinned: boolean;
  updated?: string;
  starter?: string;
};

type ApiErrorInfo = {
  code?: string;
  message: string;
};

type SharePostResult = {
  postId: number;
  postUrl: string;
  topicId: number;
};

type ShareTopicResult = {
  topicId: number;
  topicUrl: string;
  forumId: number;
  title: string;
};

type TopicCacheEntry = {
  topics: TwfTopic[];
  selectedTopicId: number | null;
  selectedTopicTitle: string | null;
};

type ShareMode = "existing" | "new";

type TwfShareModalProps = {
  open: boolean;
  onClose: () => void;
  payload: SharePayload;
  buildScreenshotState?: () => ScreenshotExportState | null;
  getLegend?: () => LegendPayload | null;
};

const TWF_PERMALINK_LABEL = "View map on CartoSky";

const QUICK_FORUMS: Array<{ id: number; label: string }> = [
  { id: 4, label: "West" },
  { id: 9, label: "East" },
];

const modalCardClass =
  "glass-overlay my-2 flex max-h-[calc(100dvh-1rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl text-white sm:my-4 sm:max-h-[calc(100dvh-2rem)]";

const sectionCardClass =
  "glass-overlay-section rounded-2xl";

const insetCardClass =
  "rounded-xl border border-cyan-200/8 bg-[#0b182b]/60";

const secondaryButtonClass =
  "inline-flex h-8 items-center rounded-md bg-white/[0.08] px-2.5 text-xs font-medium text-white/86 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-colors hover:bg-white/[0.12]";

const primaryButtonClass =
  "inline-flex h-10 items-center justify-center gap-1.5 rounded-xl border border-cyan-200/30 bg-[linear-gradient(135deg,#102438_0%,#1a4f68_52%,#6ab7d4_100%)] px-4 text-sm font-semibold text-white shadow-[0_14px_34px_rgba(17,68,92,0.34)] transition-all hover:brightness-110 disabled:opacity-60 disabled:hover:brightness-100";

const fieldClass =
  "h-8 w-full rounded-md border border-cyan-200/10 bg-[#091322]/75 px-2 text-xs text-white outline-none transition-colors focus:border-cyan-300/34 focus:bg-[#0c182a]";

const textareaClass =
  "w-full rounded-md border border-cyan-200/10 bg-[#091322]/75 px-2 py-2 text-xs text-white outline-none transition-colors focus:border-cyan-300/34 focus:bg-[#0c182a]";

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeTwfStatus(value: unknown): TwfStatus {
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

async function readApiError(response: Response): Promise<ApiErrorInfo | null> {
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

function normalizeForums(value: unknown): TwfForum[] {
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
    const name = typeof entry.name === "string" ? entry.name.trim() : "";
    if (!Number.isFinite(id) || id <= 0 || !name) {
      continue;
    }
    const path = typeof entry.path === "string" && entry.path.trim() ? entry.path.trim() : undefined;
    if (path) {
      normalized.push({ id, name, path });
      continue;
    }
    normalized.push({ id, name });
  }

  normalized.sort((a, b) => (a.path ?? a.name).localeCompare(b.path ?? b.name));
  return normalized;
}

function normalizeTopics(value: unknown): TwfTopic[] {
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
    if (!Number.isFinite(id) || id <= 0 || !title || !url) {
      continue;
    }
    const updated = typeof entry.updated === "string" && entry.updated.trim() ? entry.updated.trim() : undefined;
    const starter = typeof entry.starter === "string" && entry.starter.trim() ? entry.starter.trim() : undefined;
    const topic: TwfTopic = {
      id,
      title,
      url,
      pinned: entry.pinned === true,
    };
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

function isQuickForumId(forumId: number): boolean {
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

function topicTitleForId(topics: TwfTopic[], topicId: number | null | undefined): string | null {
  if (!Number.isFinite(topicId) || Number(topicId) <= 0) {
    return null;
  }
  return topics.find((topic) => topic.id === Number(topicId))?.title ?? null;
}

function resolveTopicSelection(topics: TwfTopic[], preferredTopicId?: number | null): {
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

function hydratePersistedTopicCacheEntry(forumId: number): TopicCacheEntry | null {
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

function forumIdFromPrefs(prefs: SharePrefs): number {
  if (Number.isFinite(prefs.forumId) && Number(prefs.forumId) > 0) {
    return Number(prefs.forumId);
  }
  return prefs.forumMode === "east" ? QUICK_FORUMS[1].id : QUICK_FORUMS[0].id;
}

function forumModeFromSelection(
  selectedForumId: number,
  showOtherForums: boolean
): SharePrefs["forumMode"] {
  if (showOtherForums || !isQuickForumId(selectedForumId)) {
    return "other";
  }
  return selectedForumId === QUICK_FORUMS[1].id ? "east" : "west";
}

async function writeClipboard(text: string): Promise<boolean> {
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

function screenshotFilename(state: ScreenshotExportState): string {
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

function currentRouteWithSearch(): string {
  if (typeof window === "undefined") {
    return "/viewer";
  }
  return `${window.location.pathname}${window.location.search}` || "/viewer";
}

function loginRouteForCurrentPage(): string {
  return `/login?${new URLSearchParams({ redirect_url: currentRouteWithSearch() }).toString()}`;
}

export function TwfShareModal({
  open,
  onClose,
  payload,
  buildScreenshotState,
  getLegend,
}: TwfShareModalProps) {
  const { getToken, isLoaded: clerkLoaded, isSignedIn } = useAuth();
  const initialSharePrefs = useMemo(() => getSharePrefs(), []);
  const wasOpenRef = useRef(false);
  const destinationSavedTimerRef = useRef<number | null>(null);
  const copyMenuRef = useRef<HTMLDivElement | null>(null);
  const topicCacheRef = useRef<Map<number, TopicCacheEntry>>(new Map());
  const quickForumPrefetchInFlightRef = useRef<Set<number>>(new Set());
  const selectedTopicIdRef = useRef<number | null>(initialSharePrefs.topicId ?? null);
  const [twfStatus, setTwfStatus] = useState<TwfStatus>({ linked: false });
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusResolved, setStatusResolved] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [connectBusy, setConnectBusy] = useState(false);

  const [selectedForumId, setSelectedForumId] = useState<number>(() => forumIdFromPrefs(initialSharePrefs));
  const [showOtherForums, setShowOtherForums] = useState(
    () => initialSharePrefs.forumMode === "other" || !isQuickForumId(forumIdFromPrefs(initialSharePrefs))
  );
  const [forums, setForums] = useState<TwfForum[]>([]);
  const [forumsLoading, setForumsLoading] = useState(false);
  const [forumsError, setForumsError] = useState<string | null>(null);

  const [topics, setTopics] = useState<TwfTopic[]>([]);
  const [topicsForumId, setTopicsForumId] = useState<number | null>(null);
  const [topicsLoading, setTopicsLoading] = useState(false);
  const [topicsError, setTopicsError] = useState<string | null>(null);
  const [selectedTopicId, setSelectedTopicId] = useState<number | null>(initialSharePrefs.topicId ?? null);
  const [selectedTopicTitleFallback, setSelectedTopicTitleFallback] = useState<string | null>(initialSharePrefs.topicTitle ?? null);
  const [shareMode, setShareMode] = useState<ShareMode>("existing");

  const [content, setContent] = useState("");
  const [newTopicTitle, setNewTopicTitle] = useState("");
  const [submitBusy, setSubmitBusy] = useState(false);
  const [submitError, setSubmitError] = useState<ApiErrorInfo | null>(null);
  const [retryAfterSeconds, setRetryAfterSeconds] = useState<number | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState<SharePostResult | null>(null);
  const [submitTopicSuccess, setSubmitTopicSuccess] = useState<ShareTopicResult | null>(null);
  const [submitTopicTitle, setSubmitTopicTitle] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const [textCopied, setTextCopied] = useState(false);
  const [contentDirty, setContentDirty] = useState(false);
  const [screenshotBusy, setScreenshotBusy] = useState(false);
  const [screenshotError, setScreenshotError] = useState<string | null>(null);
  const [screenshotBlob, setScreenshotBlob] = useState<Blob | null>(null);
  const [screenshotBlobUrl, setScreenshotBlobUrl] = useState<string | null>(null);
  const [screenshotStateSnapshot, setScreenshotStateSnapshot] = useState<ScreenshotExportState | null>(null);
  const [screenshotFilenameValue, setScreenshotFilenameValue] = useState("cartosky-map-screenshot.png");
  const [screenshotUploadBusy, setScreenshotUploadBusy] = useState(false);
  const [screenshotUploadError, setScreenshotUploadError] = useState<string | null>(null);
  const [screenshotUrl, setScreenshotUrl] = useState<string | null>(null);
  const [screenshotKey, setScreenshotKey] = useState<string | null>(null);
  const [includeScreenshotInPost, setIncludeScreenshotInPost] = useState(false);
  const [hasAttemptedAutoScreenshot, setHasAttemptedAutoScreenshot] = useState(false);
  const [showDestinationEditor, setShowDestinationEditor] = useState(false);
  const [destinationSaved, setDestinationSaved] = useState(false);
  const [showCopyMenu, setShowCopyMenu] = useState(false);

  const twfFetch = useCallback(
    async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
      if (!clerkLoaded) {
        throw new Error("Checking CartoSky sign-in status.");
      }
      if (!isSignedIn) {
        throw new Error("Sign in to CartoSky before connecting TWF.");
      }
      const token = await getToken({ template: clerkJwtTemplate() });
      if (!token) {
        throw new Error("Unable to load CartoSky auth token.");
      }
      const headers = new Headers(init.headers);
      headers.set("Authorization", `Bearer ${token}`);
      return fetch(input, {
        ...init,
        credentials: init.credentials ?? "omit",
        headers,
      });
    },
    [clerkLoaded, getToken, isSignedIn]
  );

  const handleConnectTwf = useCallback(async () => {
    setSubmitError(null);
    setStatusError(null);
    if (!clerkLoaded) {
      setSubmitError({ message: "Checking CartoSky sign-in status." });
      return;
    }
    if (!isSignedIn) {
      setSubmitError(null);
      setStatusError("Sign in to CartoSky before connecting TWF.");
      return;
    }
    setConnectBusy(true);
    try {
      const returnTo = currentRouteWithSearch();
      const response = await twfFetch(`${API_ORIGIN}/auth/twf/start?${new URLSearchParams({ return_to: returnTo })}`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        const apiError = await readApiError(response);
        throw new Error(apiError?.message || `TWF connection failed (${response.status})`);
      }
      const body = (await response.json()) as unknown;
      if (!isObject(body) || typeof body.authorize_url !== "string" || !body.authorize_url.trim()) {
        throw new Error("TWF authorization URL was not returned.");
      }
      window.location.assign(body.authorize_url);
    } catch (error) {
      setSubmitError({ message: (error as Error).message || "Failed to start TWF connection." });
      setConnectBusy(false);
    }
  }, [clerkLoaded, isSignedIn, twfFetch]);

  const getTopicCacheEntry = (forumId: number): TopicCacheEntry | null => {
    const inMemory = topicCacheRef.current.get(forumId);
    if (inMemory) {
      return inMemory;
    }
    const persisted = hydratePersistedTopicCacheEntry(forumId);
    if (!persisted) {
      return null;
    }
    topicCacheRef.current.set(forumId, persisted);
    return persisted;
  };

  const storeTopicCacheEntry = (forumId: number, entry: TopicCacheEntry) => {
    topicCacheRef.current.set(forumId, entry);
    if (!isQuickForumId(forumId) || entry.topics.length === 0) {
      return;
    }
    setSharePrefsTopicCacheEntry({
      forumId,
      topics: entry.topics,
      selectedTopicId: entry.selectedTopicId ?? undefined,
      selectedTopicTitle: entry.selectedTopicTitle ?? undefined,
      savedAt: Date.now(),
    });
  };


  const defaultContent = useMemo(() => {
    return payload.summary;
  }, [payload.summary]);
  const defaultTopicTitle = useMemo(() => payload.summary.trim().slice(0, 255), [payload.summary]);
  const selectedTopicTitle = useMemo(() => {
    if (!Number.isFinite(selectedTopicId) || Number(selectedTopicId) <= 0) {
      return null;
    }
    if (topicsForumId === selectedForumId) {
      const found = topics.find((topic) => topic.id === Number(selectedTopicId));
      if (found?.title) {
        return found.title;
      }
    }
    return selectedTopicTitleFallback;
  }, [selectedForumId, selectedTopicId, selectedTopicTitleFallback, topics, topicsForumId]);
  const selectedForumLabel = useMemo(() => {
    const quickForum = QUICK_FORUMS.find((forum) => forum.id === selectedForumId);
    if (!showOtherForums && quickForum) {
      return quickForum.label;
    }
    const customForum = forums.find((forum) => forum.id === selectedForumId);
    return customForum?.path ?? customForum?.name ?? `Forum ${selectedForumId}`;
  }, [forums, selectedForumId, showOtherForums]);
  const showTopicsLoadingState =
    topicsLoading ||
    (open &&
      selectedForumId > 0 &&
      topics.length === 0 &&
      (!statusResolved || statusLoading || topicsForumId !== selectedForumId));
  const canPrepareScreenshot = Boolean(buildScreenshotState);
  const postButtonDisabled = submitBusy || screenshotBusy || screenshotUploadBusy;

  useEffect(() => {
    if (!open) {
      wasOpenRef.current = false;
      return;
    }
    if (wasOpenRef.current) {
      return;
    }
    wasOpenRef.current = true;
    const prefs = getSharePrefs();
    const persistedForumId = forumIdFromPrefs(prefs);
    const cachedTopics = getTopicCacheEntry(persistedForumId);
    setSelectedForumId(persistedForumId);
    setShowOtherForums(prefs.forumMode === "other" || !isQuickForumId(persistedForumId));
    const initialTopicId = cachedTopics?.selectedTopicId ?? (prefs.forumId === persistedForumId ? prefs.topicId ?? null : null);
    const initialTopicTitle = cachedTopics?.selectedTopicTitle ?? (prefs.forumId === persistedForumId ? prefs.topicTitle ?? null : null);
    selectedTopicIdRef.current = initialTopicId;
    setTopics(cachedTopics?.topics ?? []);
    setTopicsForumId(cachedTopics ? persistedForumId : null);
    setSelectedTopicId(initialTopicId);
    setSelectedTopicTitleFallback(initialTopicTitle);
    setShareMode("existing");
    setContent("");
    setNewTopicTitle(defaultTopicTitle);
    setContentDirty(false);
    setLinkCopied(false);
    setTextCopied(false);
    setShowCopyMenu(false);
    setStatusResolved(false);
    setSubmitError(null);
    setSubmitSuccess(null);
    setSubmitTopicSuccess(null);
    setSubmitTopicTitle(null);
    setRetryAfterSeconds(null);
    setScreenshotBusy(false);
    setScreenshotError(null);
    setScreenshotBlob(null);
    setScreenshotFilenameValue("cartosky-map-screenshot.png");
    setScreenshotStateSnapshot(null);
    setScreenshotUploadBusy(false);
    setScreenshotUploadError(null);
    setScreenshotUrl(null);
    setScreenshotKey(null);
    setIncludeScreenshotInPost(true);
    setHasAttemptedAutoScreenshot(false);
    setShowDestinationEditor(false);
    setDestinationSaved(false);
    setScreenshotBlobUrl((previous) => {
      if (previous) {
        URL.revokeObjectURL(previous);
      }
      return null;
    });
  }, [open, defaultContent, defaultTopicTitle]);


  useEffect(() => {
    selectedTopicIdRef.current = selectedTopicId;
  }, [selectedTopicId]);

  useEffect(() => {
    if (!open) {
      return;
    }
    setNewTopicTitle((current) => (current.trim() ? current : defaultTopicTitle));
  }, [open, defaultTopicTitle]);

  useEffect(() => {
    return () => {
      if (destinationSavedTimerRef.current !== null) {
        window.clearTimeout(destinationSavedTimerRef.current);
      }
      if (screenshotBlobUrl) {
        URL.revokeObjectURL(screenshotBlobUrl);
      }
    };
  }, [screenshotBlobUrl]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  useEffect(() => {
    if (!showCopyMenu) {
      return;
    }
    const handlePointerDown = (event: PointerEvent) => {
      if (!copyMenuRef.current?.contains(event.target as Node)) {
        setShowCopyMenu(false);
      }
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [showCopyMenu]);

  useEffect(() => {
    if (!open || hasAttemptedAutoScreenshot || !canPrepareScreenshot) {
      return;
    }
    if (screenshotBusy || screenshotUploadBusy || screenshotBlobUrl) {
      return;
    }
    setHasAttemptedAutoScreenshot(true);
    void generateScreenshot();
  }, [
    canPrepareScreenshot,
    hasAttemptedAutoScreenshot,
    open,
    screenshotBlobUrl,
    screenshotBusy,
    screenshotUploadBusy,
  ]);

  useEffect(() => {
    if (!open) {
      return;
    }
    if (!clerkLoaded) {
      setStatusLoading(true);
      setStatusError(null);
      return;
    }
    if (!isSignedIn) {
      setTwfStatus({ linked: false });
      setStatusResolved(true);
      setStatusLoading(false);
      setStatusError("Sign in to CartoSky before connecting TWF.");
      return;
    }

    const controller = new AbortController();
    setStatusLoading(true);
    setStatusError(null);

    twfFetch(`${API_ORIGIN}/auth/twf/status`, {
      method: "GET",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const apiError = await readApiError(response);
          throw new Error(apiError?.message || `Status request failed (${response.status})`);
        }
        return response.json() as Promise<unknown>;
      })
      .then((value) => {
        setTwfStatus(normalizeTwfStatus(value));
        setStatusResolved(true);
      })
      .catch((error: unknown) => {
        if ((error as { name?: string } | undefined)?.name === "AbortError") {
          return;
        }
        setTwfStatus({ linked: false });
        setStatusResolved(true);
        setStatusError((error as Error).message || "Failed to load TWF account status.");
      })
      .finally(() => setStatusLoading(false));

    return () => controller.abort();
  }, [clerkLoaded, isSignedIn, open, twfFetch]);

  useEffect(() => {
    if (!open || topicsLoading || topicsForumId !== selectedForumId) {
      return;
    }
    const validatedTopic = topics.find((topic) => topic.id === selectedTopicId) ?? null;
    setSharePrefs({
      forumMode: forumModeFromSelection(selectedForumId, showOtherForums),
      forumId: selectedForumId > 0 ? selectedForumId : undefined,
      topicId: validatedTopic?.id,
      topicTitle: validatedTopic?.title,
    });
  }, [open, selectedForumId, selectedTopicId, showOtherForums, topics, topicsForumId, topicsLoading]);

  useEffect(() => {
    if (selectedForumId <= 0 || topicsForumId !== selectedForumId) {
      return;
    }
    storeTopicCacheEntry(selectedForumId, {
      topics,
      selectedTopicId,
      selectedTopicTitle,
    });
  }, [selectedForumId, selectedTopicId, selectedTopicTitle, topics, topicsForumId]);

  useEffect(() => {
    if (!open || !statusResolved || twfStatus.linked !== true) {
      return;
    }

    const controllers: AbortController[] = [];
    for (const forum of QUICK_FORUMS) {
      if (forum.id === selectedForumId) {
        continue;
      }
      if (getTopicCacheEntry(forum.id) || quickForumPrefetchInFlightRef.current.has(forum.id)) {
        continue;
      }

      quickForumPrefetchInFlightRef.current.add(forum.id);
      const controller = new AbortController();
      controllers.push(controller);

      const prefs = getSharePrefs();
      const prefsMatchForum = forumIdFromPrefs(prefs) === forum.id;
      const preferredTopicId = prefsMatchForum ? prefs.topicId ?? null : null;
      const params = new URLSearchParams({
        forum_id: String(forum.id),
        limit: "15",
      });

      twfFetch(`${API_ORIGIN}/twf/topics?${params.toString()}`, {
        method: "GET",
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) {
            const apiError = await readApiError(response);
            throw new Error(apiError?.message || `Topics request failed (${response.status})`);
          }
          return response.json() as Promise<unknown>;
        })
        .then((value) => {
          const normalized = normalizeTopics(value);
          const resolvedSelection = resolveTopicSelection(normalized, preferredTopicId);
          storeTopicCacheEntry(forum.id, {
            topics: normalized,
            selectedTopicId: resolvedSelection.topicId,
            selectedTopicTitle: resolvedSelection.topicTitle,
          });
        })
        .catch((error: unknown) => {
          if ((error as { name?: string } | undefined)?.name === "AbortError") {
            return;
          }
        })
        .finally(() => {
          quickForumPrefetchInFlightRef.current.delete(forum.id);
        });
    }

    return () => {
      for (const controller of controllers) {
        controller.abort();
      }
    };
  }, [open, selectedForumId, statusResolved, twfFetch, twfStatus]);

  useEffect(() => {
    if (!open || twfStatus.linked !== true || !showOtherForums) {
      return;
    }

    const controller = new AbortController();
    setForumsLoading(true);
    setForumsError(null);

    twfFetch(`${API_ORIGIN}/twf/forums`, {
      method: "GET",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const apiError = await readApiError(response);
          throw new Error(apiError?.message || `Forum request failed (${response.status})`);
        }
        return response.json() as Promise<unknown>;
      })
      .then((value) => {
        const normalized = normalizeForums(value);
        setForums(normalized);
        if (!isQuickForumId(selectedForumId) && !normalized.some((forum) => forum.id === selectedForumId)) {
          const fallbackId = normalized[0]?.id ?? QUICK_FORUMS[0].id;
          setSelectedForumId(fallbackId);
        }
      })
      .catch((error: unknown) => {
        if ((error as { name?: string } | undefined)?.name === "AbortError") {
          return;
        }
        setForums([]);
        setForumsError((error as Error).message || "Failed to load forums.");
      })
      .finally(() => setForumsLoading(false));

    return () => controller.abort();
  }, [open, twfFetch, twfStatus, showOtherForums, selectedForumId]);

  useEffect(() => {
    if (!open || selectedForumId <= 0) {
      setTopics([]);
      setTopicsForumId(null);
      setSelectedTopicId(null);
      setSelectedTopicTitleFallback(null);
      setTopicsError(null);
      setTopicsLoading(false);
      return;
    }

    if (statusResolved && twfStatus.linked !== true) {
      setTopics([]);
      setTopicsForumId(selectedForumId);
      setSelectedTopicId(null);
      setSelectedTopicTitleFallback(null);
      setTopicsError(null);
      setTopicsLoading(false);
      return;
    }

    const controller = new AbortController();
  const cachedTopics = getTopicCacheEntry(selectedForumId);
    const prefs = getSharePrefs();
    const persistedSelectionMatchesForum = forumIdFromPrefs(prefs) === selectedForumId;
    if (cachedTopics) {
      setTopics(cachedTopics.topics);
      setTopicsForumId(selectedForumId);
      setSelectedTopicId(cachedTopics.selectedTopicId);
      setSelectedTopicTitleFallback(cachedTopics.selectedTopicTitle);
    } else {
      const initialTopicId = persistedSelectionMatchesForum ? prefs.topicId ?? null : null;
      selectedTopicIdRef.current = initialTopicId;
      setTopics([]);
      setTopicsForumId(null);
      setSelectedTopicId(initialTopicId);
      setSelectedTopicTitleFallback(persistedSelectionMatchesForum ? prefs.topicTitle ?? null : null);
    }
    const params = new URLSearchParams({
      forum_id: String(selectedForumId),
      limit: "15",
    });
    setTopicsLoading(true);
    setTopicsError(null);
    setSubmitSuccess(null);
    setSubmitTopicSuccess(null);
    setSubmitTopicTitle(null);

    twfFetch(`${API_ORIGIN}/twf/topics?${params.toString()}`, {
      method: "GET",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const apiError = await readApiError(response);
          throw new Error(apiError?.message || `Topics request failed (${response.status})`);
        }
        return response.json() as Promise<unknown>;
      })
      .then((value) => {
        const normalized = normalizeTopics(value);
        const latestPrefs = getSharePrefs();
        const latestPrefsMatchForum = forumIdFromPrefs(latestPrefs) === selectedForumId;
        const preferredTopicId = topicTitleForId(normalized, selectedTopicIdRef.current)
          ? selectedTopicIdRef.current
          : latestPrefsMatchForum
          ? latestPrefs.topicId ?? null
          : null;
        const resolvedSelection = resolveTopicSelection(normalized, preferredTopicId);
        setTopics(normalized);
        setTopicsForumId(selectedForumId);
        selectedTopicIdRef.current = resolvedSelection.topicId;
        setSelectedTopicId(resolvedSelection.topicId);
        setSelectedTopicTitleFallback(resolvedSelection.topicTitle);
      })
      .catch((error: unknown) => {
        if ((error as { name?: string } | undefined)?.name === "AbortError") {
          return;
        }
        if (!cachedTopics) {
          setTopics([]);
          setTopicsForumId(selectedForumId);
          selectedTopicIdRef.current = null;
          setSelectedTopicId(null);
          setSelectedTopicTitleFallback(null);
        }
        if (statusResolved || twfStatus.linked === true) {
          setTopicsError((error as Error).message || "Failed to load topics.");
        }
      })
      .finally(() => setTopicsLoading(false));

    return () => controller.abort();
  }, [open, selectedForumId, statusResolved, twfFetch, twfStatus]);

  const generateScreenshot = async (): Promise<{
    blob: Blob;
    blobUrl: string;
    filename: string;
    state: ScreenshotExportState;
  } | null> => {
    setScreenshotError(null);
    if (!buildScreenshotState) {
      setScreenshotError("Screenshot export is unavailable right now.");
      return null;
    }

    const state = buildScreenshotState();
    if (!state) {
      setScreenshotError("Map is still loading. Try again in a moment.");
      return null;
    }

    setScreenshotBusy(true);
    try {
      const { exportViewerScreenshotPng } = await import("@/lib/screenshot_export");
      const blob = await exportViewerScreenshotPng(state, {
        legend: getLegend?.() ?? null,
      });
      const objectUrl = URL.createObjectURL(blob);
      const filename = screenshotFilename(state);
      setScreenshotBlob(blob);
      setScreenshotStateSnapshot(state);
      setScreenshotFilenameValue(filename);
      setScreenshotUploadError(null);
      setScreenshotUrl(null);
      setScreenshotKey(null);
      setIncludeScreenshotInPost(true);
      setScreenshotBlobUrl((previous) => {
        if (previous) {
          URL.revokeObjectURL(previous);
        }
        return objectUrl;
      });
      return {
        blob,
        blobUrl: objectUrl,
        filename,
        state,
      };
    } catch (error) {
      const message = error instanceof Error && error.message
        ? error.message
        : "Screenshot generation failed.";
      setScreenshotError(message);
      return null;
    } finally {
      setScreenshotBusy(false);
    }
  };

  const uploadScreenshot = async (options?: {
    blob?: Blob | null;
    filename?: string | null;
    state?: ScreenshotExportState | null;
  }): Promise<string | null> => {
    const blob = options?.blob ?? screenshotBlob;
    const filename = options?.filename ?? screenshotFilenameValue;
    const state = options?.state ?? screenshotStateSnapshot;

    if (!blob) {
      setScreenshotUploadError("Generate a screenshot before uploading.");
      return null;
    }

    setScreenshotUploadBusy(true);
    setScreenshotUploadError(null);
    setScreenshotUrl(null);
    setScreenshotKey(null);

    try {
      const result = await uploadShareMedia({
        blob,
        filename,
        model: state?.model ?? null,
        run: state?.run ?? null,
        fh: state?.fh ?? null,
        variable: state?.variable.key || state?.variable.label || null,
        region: state?.region?.id ?? null,
      });
      setScreenshotUrl(result.url);
      setScreenshotKey(result.key);
      setIncludeScreenshotInPost(true);
      return result.url;
    } catch (error) {
      const message = error instanceof Error && error.message
        ? error.message
        : "Screenshot upload failed.";
      setScreenshotUploadError(message);
      return null;
    } finally {
      setScreenshotUploadBusy(false);
    }
  };

  const handlePrepareScreenshot = async () => {
    if (screenshotBusy || screenshotUploadBusy) {
      return;
    }
    await generateScreenshot();
  };

  const ensurePreparedScreenshot = async (): Promise<string | null> => {
    if (!includeScreenshotInPost) {
      return null;
    }
    if (screenshotUrl) {
      return screenshotUrl;
    }
    if (screenshotBusy || screenshotUploadBusy) {
      return null;
    }
    const generated = screenshotBlob
      ? {
          blob: screenshotBlob,
          filename: screenshotFilenameValue,
          state: screenshotStateSnapshot,
        }
      : await generateScreenshot();
    if (!generated) {
      return null;
    }
    const uploadedUrl = await uploadScreenshot({
      blob: generated.blob,
      filename: generated.filename,
      state: generated.state,
    });
    return uploadedUrl;
  };

  const handleSubmitPost = async () => {
    setSubmitError(null);
    setSubmitSuccess(null);
    setSubmitTopicSuccess(null);
    setSubmitTopicTitle(null);
    setRetryAfterSeconds(null);

    if (twfStatus.linked !== true) {
      setSubmitError({ message: "Connect your TWF account before posting." });
      return;
    }
    const resolvedSummary = content.trim();
    if (!resolvedSummary) {
      setSubmitError({ message: "Summary is required." });
      return;
    }

    setSubmitBusy(true);
    try {
      let resolvedImageUrl: string | null = null;
      if (includeScreenshotInPost) {
        resolvedImageUrl = await ensurePreparedScreenshot();
        if (!resolvedImageUrl) {
          setSubmitError({ message: screenshotUploadError || screenshotError || "Screenshot preparation failed." });
          return;
        }
      }
      let response: Response;
      if (shareMode === "new") {
        const trimmedTitle = newTopicTitle.trim();
        if (!trimmedTitle) {
          setSubmitError({ message: "Topic title is required." });
          return;
        }
        response = await twfFetch(`${API_ORIGIN}/twf/share/topic`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            forum_id: selectedForumId,
            title: trimmedTitle,
            summary: resolvedSummary,
            permalink: payload.permalink,
            image_url: resolvedImageUrl,
          }),
        });
      } else {
        if (!Number.isFinite(selectedTopicId) || Number(selectedTopicId) <= 0) {
          setSubmitError({ message: "Select a topic to post." });
          return;
        }
        response = await twfFetch(`${API_ORIGIN}/twf/share/post`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            topic_id: Number(selectedTopicId),
            summary: resolvedSummary,
            permalink: payload.permalink,
            image_url: resolvedImageUrl,
          }),
        });
      }

      if (!response.ok) {
        const apiError = await readApiError(response);
        if (response.status === 429) {
          const retryAfter = Number(response.headers.get("Retry-After"));
          if (Number.isFinite(retryAfter) && retryAfter > 0) {
            setRetryAfterSeconds(Math.max(1, Math.floor(retryAfter)));
          }
        }
        setSubmitError(apiError ?? { message: "Request failed. Please try again." });
        return;
      }

      if (shareMode === "new") {
        const result = (await response.json()) as ShareTopicResult;
        if (
          !Number.isFinite(Number(result.topicId)) ||
          typeof result.topicUrl !== "string" ||
          typeof result.title !== "string"
        ) {
          setSubmitError({ message: "Unexpected response from server." });
          return;
        }
        setSubmitTopicSuccess(result);
        setSubmitTopicTitle(result.title);
      } else {
        const result = (await response.json()) as SharePostResult;
        if (!Number.isFinite(Number(result.postId)) || typeof result.postUrl !== "string") {
          setSubmitError({ message: "Unexpected response from server." });
          return;
        }
        setSubmitSuccess(result);
        setSubmitTopicTitle(selectedTopicTitle ?? "Selected topic");
      }
    } catch {
      setSubmitError({ message: "Request failed. Please try again." });
    } finally {
      setSubmitBusy(false);
    }
  };

  const handleMessageChange = (nextValue: string) => {
    setContent(nextValue);
    setContentDirty(nextValue !== defaultContent);
  };

  const handleTopicSelectionChange = (nextTopicId: number | null) => {
    selectedTopicIdRef.current = nextTopicId;
    setSelectedTopicId(nextTopicId);
    setSelectedTopicTitleFallback(topicTitleForId(topics, nextTopicId));
  };

  const handleCopyLink = async () => {
    const ok = await writeClipboard(payload.permalink);
    if (ok) {
      setShowCopyMenu(false);
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 1500);
    }
  };

  const handleCopyText = async () => {
    const text = `${content.trim() || payload.summary}\n${payload.permalink}`;
    const ok = await writeClipboard(text);
    if (ok) {
      setShowCopyMenu(false);
      setTextCopied(true);
      setTimeout(() => setTextCopied(false), 1500);
    }
  };

  useEffect(() => {
    if (!submitSuccess && !submitTopicSuccess) {
      return;
    }
    const timer = setTimeout(() => onClose(), 2000);
    return () => clearTimeout(timer);
  }, [submitSuccess, submitTopicSuccess, onClose]);

  if (!open) {
    return null;
  }

  const isPosted = Boolean(submitSuccess || submitTopicSuccess);
  const signedOutLoginUrl = loginRouteForCurrentPage();
  const destinationLabel = selectedTopicTitle
    ? `${selectedForumLabel} › ${selectedTopicTitle}`
    : selectedForumLabel;
  const handleDestinationEditorToggle = () => {
    if (showDestinationEditor) {
      setShowDestinationEditor(false);
      setDestinationSaved(true);
      if (destinationSavedTimerRef.current !== null) {
        window.clearTimeout(destinationSavedTimerRef.current);
      }
      destinationSavedTimerRef.current = window.setTimeout(() => {
        setDestinationSaved(false);
        destinationSavedTimerRef.current = null;
      }, 2000);
      return;
    }
    if (destinationSavedTimerRef.current !== null) {
      window.clearTimeout(destinationSavedTimerRef.current);
      destinationSavedTimerRef.current = null;
    }
    setDestinationSaved(false);
    setShowDestinationEditor(true);
  };

  return (
    <div
      className="fixed inset-0 z-[80] flex items-end justify-center bg-slate-950/60 backdrop-blur-sm backdrop-brightness-[0.62] backdrop-saturate-75 sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Share"
      onClick={onClose}
    >
      <div
        className="glass w-full max-w-[580px] overflow-hidden rounded-t-3xl sm:rounded-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        {/* Drag handle */}
        <div className="flex justify-center pb-1 pt-3">
          <div className="h-1 w-9 rounded-full bg-white/20" />
        </div>

        {/* Title + close */}
        <div className="flex items-center justify-between px-4 pt-2 pb-3">
          <div className="text-base font-semibold text-white">Share this view</div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-white/[0.08] text-white/70 transition-colors hover:bg-white/[0.12]"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Screenshot preview */}
        <TooltipProvider delayDuration={250}>
          <div className="px-4">
            <div className="relative h-[260px] overflow-hidden rounded-2xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)]">
              {screenshotBlobUrl ? (
                <img src={screenshotBlobUrl} alt="Screenshot preview" className="h-full w-full object-contain bg-black/20" />
              ) : screenshotBusy ? (
                <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-[#0d1e35] to-[#0a1628]">
                  <Loader2 className="h-6 w-6 animate-spin text-white/40" />
                </div>
              ) : (
                <div className="h-full w-full bg-gradient-to-br from-[#0d1e35] to-[#0a1628]" />
              )}

              {screenshotBlobUrl && !screenshotBusy && (
                <div className="absolute bottom-2 left-2 flex items-center gap-1.5 rounded-md bg-black/75 px-2 py-1 text-xs font-medium text-white">
                  <div className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                  Screenshot ready
                </div>
              )}

              <div className="absolute top-2 right-2 flex items-center gap-1.5">
                {screenshotBlobUrl && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        onClick={() => {
                          const link = document.createElement("a");
                          link.href = screenshotBlobUrl;
                          link.download = screenshotFilenameValue;
                          link.rel = "noopener";
                          document.body.appendChild(link);
                          link.click();
                          link.remove();
                        }}
                        className="flex items-center justify-center rounded-xl border border-white/20 bg-black/50 p-1.5 text-white backdrop-blur-sm transition-opacity hover:bg-black/65"
                        aria-label="Download screenshot"
                      >
                        <Download className="h-3.5 w-3.5" />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="left" className="border-white/10 bg-[#07111f] text-white">
                      Download screenshot
                    </TooltipContent>
                  </Tooltip>
                )}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() => {
                        setHasAttemptedAutoScreenshot(false);
                        void handlePrepareScreenshot();
                      }}
                      disabled={!canPrepareScreenshot || screenshotBusy}
                      className="flex items-center justify-center rounded-xl border border-white/20 bg-black/50 p-1.5 text-white backdrop-blur-sm transition-opacity hover:bg-black/65 disabled:opacity-50"
                      aria-label="Refresh screenshot"
                    >
                      {screenshotBusy ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <RefreshCw className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="left" className="border-white/10 bg-[#07111f] text-white">
                    Regenerate screenshot
                  </TooltipContent>
                </Tooltip>
              </div>
            </div>
          </div>
        </TooltipProvider>

        {/* Composer card */}
        <div className="px-4 mt-3">
          <div className="glass-overlay-section overflow-hidden rounded-2xl">

            {/* Destination row */}
            <div className="flex items-start justify-between gap-2 px-4 py-3">
              <div className="min-w-0">
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-white/45">Posting to:</div>
                <span className="block text-sm leading-snug text-white/90">{destinationLabel}</span>
              </div>
              <div className="ml-3 flex shrink-0 items-center gap-2">
                {destinationSaved && (
                  <div className="flex items-center gap-1 text-xs font-medium text-emerald-200">
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                    Saved
                  </div>
                )}
                <button
                  type="button"
                  onClick={handleDestinationEditorToggle}
                  className="rounded-lg bg-white/10 px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-white/[0.15]"
                >
                  {showDestinationEditor ? "Done" : "Change"}
                </button>
              </div>
            </div>

            {/* Destination editor */}
            {showDestinationEditor && (
              <div className="space-y-3 border-t border-[rgba(255,255,255,0.08)] px-4 py-3">
                <div>
                  <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-white/50">Share mode</div>
                  <div className="flex items-center gap-2">
                    {(["existing", "new"] as ShareMode[]).map((mode) => (
                      <button
                        key={mode}
                        type="button"
                        onClick={() => setShareMode(mode)}
                        className={[
                          "inline-flex h-7 items-center rounded-md px-2.5 text-xs font-medium transition-colors",
                          shareMode === mode
                            ? "bg-cyan-300/18 text-cyan-50 shadow-[inset_0_0_0_1px_rgba(125,211,252,0.22)]"
                            : "bg-white/[0.07] text-white/70 hover:bg-white/[0.11]",
                        ].join(" ")}
                      >
                        {mode === "existing" ? "Existing topic" : "New topic"}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-white/50">Forum</div>
                  <div className="flex flex-wrap items-center gap-2">
                    {QUICK_FORUMS.map((forum) => (
                      <button
                        key={forum.id}
                        type="button"
                        onClick={() => { setSelectedForumId(forum.id); setShowOtherForums(false); }}
                        className={[
                          "inline-flex h-7 items-center rounded-md px-2.5 text-xs font-medium transition-colors",
                          selectedForumId === forum.id && !showOtherForums
                            ? "bg-cyan-300/18 text-cyan-50 shadow-[inset_0_0_0_1px_rgba(125,211,252,0.22)]"
                            : "bg-white/[0.07] text-white/70 hover:bg-white/[0.11]",
                        ].join(" ")}
                      >
                        {forum.label}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() => setShowOtherForums((current) => !current)}
                      className={[
                        "inline-flex h-7 items-center rounded-md px-2.5 text-xs font-medium transition-colors",
                        showOtherForums
                          ? "bg-cyan-300/18 text-cyan-50 shadow-[inset_0_0_0_1px_rgba(125,211,252,0.22)]"
                          : "bg-white/[0.07] text-white/70 hover:bg-white/[0.11]",
                      ].join(" ")}
                    >
                      Other...
                    </button>
                  </div>
                  {showOtherForums && (
                    <div className="mt-2">
                      {forumsLoading ? (
                        <div className="text-xs text-white/50">Loading forums...</div>
                      ) : forums.length > 0 ? (
                        <select
                          value={String(selectedForumId)}
                          onChange={(event) => setSelectedForumId(Number(event.target.value))}
                          className={fieldClass}
                        >
                          {forums.map((forum) => (
                            <option key={forum.id} value={String(forum.id)}>
                              {(forum.path ?? forum.name) + ` (ID ${forum.id})`}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <div className="text-xs text-white/50">No accessible forums found.</div>
                      )}
                      {forumsError ? <div className="mt-1 text-xs text-red-200">{forumsError}</div> : null}
                    </div>
                  )}
                </div>

                {shareMode === "existing" ? (
                  <div>
                    <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-white/50">Topic</div>
                    {topics.length > 0 ? (
                      <div className="space-y-1.5">
                        <select
                          value={selectedTopicId !== null ? String(selectedTopicId) : ""}
                          onChange={(event) => handleTopicSelectionChange(Number(event.target.value))}
                          className={fieldClass}
                        >
                          {topics.map((topic) => (
                            <option key={topic.id} value={String(topic.id)}>
                              {(topic.pinned ? "[PIN] " : "") + topic.title}
                            </option>
                          ))}
                        </select>
                        {topicsLoading ? (
                          <div className="flex items-center gap-1.5 text-[11px] text-white/45">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            Refreshing topics...
                          </div>
                        ) : null}
                      </div>
                    ) : showTopicsLoadingState ? (
                      <div className="flex items-center gap-1.5 text-xs text-white/50">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        Loading topics...
                      </div>
                    ) : (
                      <div className="text-xs text-white/50">No topics loaded.</div>
                    )}
                    {topicsError ? <div className="mt-1 text-xs text-red-200">{topicsError}</div> : null}
                  </div>
                ) : (
                  <div>
                    <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-white/50">Topic title</div>
                    <input
                      value={newTopicTitle}
                      onChange={(event) => setNewTopicTitle(event.target.value)}
                      maxLength={255}
                      placeholder="Enter a topic title"
                      className={`${fieldClass} placeholder:text-white/40`}
                    />
                  </div>
                )}

                {twfStatus.linked !== true && (
                  <div className="flex flex-col gap-2 rounded-lg border border-cyan-200/10 bg-[#0b182b]/55 px-3 py-2.5 text-xs text-white/70 sm:flex-row sm:items-center sm:justify-between">
                    <span>
                      {isSignedIn
                        ? "Connect your TWF account to post."
                        : "Sign in to CartoSky, then connect your TWF account to post."}
                    </span>
                    {isSignedIn ? (
                      <button
                        type="button"
                        onClick={handleConnectTwf}
                        disabled={connectBusy}
                        className="font-semibold text-cyan-300 hover:text-cyan-200 disabled:cursor-wait disabled:opacity-70"
                      >
                        {connectBusy ? "Connecting..." : "Connect TWF"}
                      </button>
                    ) : (
                      <Link
                        to={signedOutLoginUrl}
                        className="font-semibold text-cyan-300 hover:text-cyan-200"
                        onClick={onClose}
                      >
                        Sign in
                      </Link>
                    )}
                  </div>
                )}

                {submitError && (
                  <div className="rounded-lg border border-red-400/25 bg-red-500/10 px-3 py-2 text-xs text-red-100">
                    {submitError.message}
                    {submitError.code ? <div className="mt-0.5 opacity-90">Code: {submitError.code}</div> : null}
                    {retryAfterSeconds ? <div className="mt-0.5 opacity-90">Try again in {retryAfterSeconds}s.</div> : null}
                  </div>
                )}
              </div>
            )}

            {/* Divider */}
            <div className="h-px bg-[rgba(255,255,255,0.08)]" />

            {/* Textarea */}
            <textarea
              value={content}
              onChange={(event) => handleMessageChange(event.target.value)}
              maxLength={500}
              placeholder="What do you see in this data…"
              className="w-full resize-none bg-transparent px-4 py-3 text-sm text-white outline-none placeholder:text-white/35"
              style={{ minHeight: "92px" }}
              rows={4}
            />

            {/* Divider */}
            <div className="h-px bg-[rgba(255,255,255,0.08)]" />

            {/* Model label row */}
            <div className="flex items-center gap-3 px-4 py-2.5">
              <button
                type="button"
                onClick={() => {
                  setContent(payload.summary);
                  setContentDirty(true);
                }}
                className="flex shrink-0 items-center gap-1.5 rounded-full border border-blue-400/25 bg-blue-500/10 px-3 py-1 text-xs text-blue-200 transition-colors hover:bg-blue-500/20"
              >
                ↩ Use model label
              </button>
            </div>
          </div>
        </div>

        {/* Success banner */}
        {isPosted && (
          <div className="mx-4 mt-2 flex items-center gap-2 rounded-lg border border-emerald-400/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
            {submitTopicSuccess ? "Topic created!" : "Posted!"} Closing…
          </div>
        )}

        {/* Error banner (when destination editor is closed) */}
        {submitError && !showDestinationEditor && (
          <div className="mx-4 mt-2 rounded-lg border border-red-400/25 bg-red-500/10 px-3 py-2 text-xs text-red-100">
            {submitError.message}
          </div>
        )}

        {/* Bottom action row */}
        <div className="flex items-center justify-between px-4 pb-6 pt-3">
          <div className="relative" ref={copyMenuRef}>
            {showCopyMenu && (
              <div
                role="menu"
                className="absolute bottom-[calc(100%+0.5rem)] left-0 z-20 min-w-[180px] overflow-hidden rounded-xl border border-white/10 bg-[#07111f]/95 p-1.5 shadow-[0_18px_36px_rgba(0,0,0,0.35)] backdrop-blur-md"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => { void handleCopyText(); }}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-white/85 transition-colors hover:bg-white/[0.08]"
                >
                  <Copy className="h-3.5 w-3.5 shrink-0" />
                  {textCopied ? "Copied text" : "Copy text"}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => { void handleCopyLink(); }}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-white/85 transition-colors hover:bg-white/[0.08]"
                >
                  <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                  {linkCopied ? "Copied link" : "Copy link"}
                </button>
              </div>
            )}
            <button
              type="button"
              onClick={() => setShowCopyMenu((current) => !current)}
              className="flex items-center gap-1.5 rounded-xl border border-white/15 bg-white/[0.07] px-3 py-2 text-sm font-medium text-white/80 transition-colors hover:bg-white/[0.11]"
              aria-haspopup="menu"
              aria-expanded={showCopyMenu}
            >
              <Copy className="h-3.5 w-3.5" />
              {linkCopied || textCopied ? "Copied!" : "Copy"}
              <ChevronDown className={[
                "h-3.5 w-3.5 transition-transform",
                showCopyMenu ? "rotate-180" : "rotate-0",
              ].join(" ")} />
            </button>
          </div>

          {!clerkLoaded ? (
            <button type="button" disabled className={primaryButtonClass}>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Checking sign-in...
            </button>
          ) : !isSignedIn ? (
            <Link to={signedOutLoginUrl} onClick={onClose} className={primaryButtonClass}>
              Sign in
            </Link>
          ) : twfStatus.linked !== true ? (
            <button
              type="button"
              onClick={handleConnectTwf}
              disabled={connectBusy || isPosted}
              className={primaryButtonClass}
            >
              {connectBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {connectBusy ? "Connecting..." : "Connect TWF"}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => { void handleSubmitPost(); }}
              disabled={postButtonDisabled || isPosted}
              className={primaryButtonClass}
            >
              {submitBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {isPosted && <CheckCircle2 className="h-3.5 w-3.5" />}
              {submitBusy ? "Posting…" : isPosted ? "Posted!" : "Post →"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
