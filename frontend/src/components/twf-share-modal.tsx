import { useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Copy, ExternalLink, Loader2, RefreshCw, X } from "lucide-react";

import type { LegendPayload } from "@/components/map-legend";
import { API_ORIGIN } from "@/lib/config";
import type { ScreenshotExportState } from "@/lib/screenshot_export";
import { uploadShareMedia } from "@/lib/share_media";
import { getSharePrefs, setSharePrefs, type SharePrefs } from "@/lib/share_prefs";
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

export function TwfShareModal({
  open,
  onClose,
  payload,
  buildScreenshotState,
  getLegend,
}: TwfShareModalProps) {
  const initialSharePrefs = useMemo(() => getSharePrefs(), []);
  const wasOpenRef = useRef(false);
  const [twfStatus, setTwfStatus] = useState<TwfStatus>({ linked: false });
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [selectedForumId, setSelectedForumId] = useState<number>(() => forumIdFromPrefs(initialSharePrefs));
  const [showOtherForums, setShowOtherForums] = useState(
    () => initialSharePrefs.forumMode === "other" || !isQuickForumId(forumIdFromPrefs(initialSharePrefs))
  );
  const [forums, setForums] = useState<TwfForum[]>([]);
  const [forumsLoading, setForumsLoading] = useState(false);
  const [forumsError, setForumsError] = useState<string | null>(null);

  const [topics, setTopics] = useState<TwfTopic[]>([]);
  const [topicsLoading, setTopicsLoading] = useState(false);
  const [topicsError, setTopicsError] = useState<string | null>(null);
  const [selectedTopicId, setSelectedTopicId] = useState<number | null>(initialSharePrefs.topicId ?? null);
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


  const defaultContent = useMemo(() => {
    return payload.summary;
  }, [payload.summary]);
  const defaultTopicTitle = useMemo(() => payload.summary.trim().slice(0, 255), [payload.summary]);
  const selectedTopicTitle = useMemo(() => {
    if (!Number.isFinite(selectedTopicId) || Number(selectedTopicId) <= 0) {
      return null;
    }
    const found = topics.find((topic) => topic.id === Number(selectedTopicId));
    if (found?.title) {
      return found.title;
    }
    return null;
  }, [selectedTopicId, topics]);
  const selectedForumLabel = useMemo(() => {
    const quickForum = QUICK_FORUMS.find((forum) => forum.id === selectedForumId);
    if (!showOtherForums && quickForum) {
      return quickForum.label;
    }
    const customForum = forums.find((forum) => forum.id === selectedForumId);
    return customForum?.path ?? customForum?.name ?? `Forum ${selectedForumId}`;
  }, [forums, selectedForumId, showOtherForums]);
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
    setSelectedForumId(persistedForumId);
    setShowOtherForums(prefs.forumMode === "other" || !isQuickForumId(persistedForumId));
    setSelectedTopicId(prefs.topicId ?? null);
    setShareMode("existing");
    setContent(defaultContent);
    setNewTopicTitle(defaultTopicTitle);
    setContentDirty(false);
    setLinkCopied(false);
    setTextCopied(false);
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
    setScreenshotBlobUrl((previous) => {
      if (previous) {
        URL.revokeObjectURL(previous);
      }
      return null;
    });
  }, [open, defaultContent, defaultTopicTitle]);

  useEffect(() => {
    if (!open || contentDirty) {
      return;
    }
    setContent(defaultContent);
  }, [open, defaultContent, contentDirty]);

  useEffect(() => {
    if (!open) {
      return;
    }
    setNewTopicTitle((current) => (current.trim() ? current : defaultTopicTitle));
  }, [open, defaultTopicTitle]);

  useEffect(() => {
    return () => {
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

    const controller = new AbortController();
    setStatusLoading(true);
    setStatusError(null);

    fetch(`${API_ORIGIN}/auth/twf/status`, {
      method: "GET",
      credentials: "include",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const apiError = await readApiError(response);
          throw new Error(apiError?.message || `Status request failed (${response.status})`);
        }
        return response.json() as Promise<unknown>;
      })
      .then((value) => setTwfStatus(normalizeTwfStatus(value)))
      .catch((error: unknown) => {
        if ((error as { name?: string } | undefined)?.name === "AbortError") {
          return;
        }
        setTwfStatus({ linked: false });
        setStatusError((error as Error).message || "Failed to load TWF account status.");
      })
      .finally(() => setStatusLoading(false));

    return () => controller.abort();
  }, [open]);

  useEffect(() => {
    setSharePrefs({
      forumMode: forumModeFromSelection(selectedForumId, showOtherForums),
      forumId: selectedForumId > 0 ? selectedForumId : undefined,
      topicId: selectedTopicId ?? undefined,
    });
  }, [selectedForumId, showOtherForums, selectedTopicId]);

  useEffect(() => {
    if (!open || twfStatus.linked !== true || !showOtherForums) {
      return;
    }

    const controller = new AbortController();
    setForumsLoading(true);
    setForumsError(null);

    fetch(`${API_ORIGIN}/twf/forums`, {
      method: "GET",
      credentials: "include",
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
  }, [open, twfStatus, showOtherForums, selectedForumId]);

  useEffect(() => {
    if (!open || twfStatus.linked !== true || selectedForumId <= 0) {
      setTopics([]);
      setSelectedTopicId(null);
      setTopicsError(null);
      setTopicsLoading(false);
      return;
    }

    const controller = new AbortController();
    const params = new URLSearchParams({
      forum_id: String(selectedForumId),
      limit: "15",
    });
    setTopicsLoading(true);
    setTopicsError(null);
    setSubmitSuccess(null);
    setSubmitTopicSuccess(null);
    setSubmitTopicTitle(null);

    fetch(`${API_ORIGIN}/twf/topics?${params.toString()}`, {
      method: "GET",
      credentials: "include",
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
        setTopics(normalized);
        const savedTopicId = getSharePrefs().topicId;
        if (savedTopicId && normalized.some((topic) => topic.id === savedTopicId)) {
          setSelectedTopicId(savedTopicId);
          return;
        }
        setSelectedTopicId(resolveMonthlyTopicId(normalized));
      })
      .catch((error: unknown) => {
        if ((error as { name?: string } | undefined)?.name === "AbortError") {
          return;
        }
        setTopics([]);
        setSelectedTopicId(null);
        setTopicsError((error as Error).message || "Failed to load topics.");
      })
      .finally(() => setTopicsLoading(false));

    return () => controller.abort();
  }, [open, twfStatus, selectedForumId]);

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
        response = await fetch(`${API_ORIGIN}/twf/share/topic`, {
          method: "POST",
          credentials: "include",
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
        response = await fetch(`${API_ORIGIN}/twf/share/post`, {
          method: "POST",
          credentials: "include",
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

  const handleCopyLink = async () => {
    const ok = await writeClipboard(payload.permalink);
    if (ok) {
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 1500);
    }
  };

  const handleCopyText = async () => {
    const text = `${payload.summary}\n${payload.permalink}`;
    const ok = await writeClipboard(text);
    if (ok) {
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
  const destinationLabel = selectedTopicTitle
    ? `${selectedForumLabel} › ${selectedTopicTitle}`
    : selectedForumLabel;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-end justify-center bg-slate-950/60 backdrop-blur-sm backdrop-brightness-[0.62] backdrop-saturate-75 sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Share"
      onClick={onClose}
    >
      <div
        className="w-full max-w-[420px] overflow-hidden rounded-t-3xl bg-[#0f1923] sm:rounded-2xl"
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
        <div className="px-4">
          <div className="relative h-[168px] overflow-hidden rounded-2xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)]">
            {screenshotBlobUrl ? (
              <img src={screenshotBlobUrl} alt="Screenshot preview" className="h-full w-full object-cover" />
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

            <button
              type="button"
              onClick={() => {
                setHasAttemptedAutoScreenshot(false);
                void handlePrepareScreenshot();
              }}
              disabled={!canPrepareScreenshot || screenshotBusy}
              className="absolute top-2 right-2 flex items-center gap-1.5 rounded-xl border border-white/20 bg-black/50 px-3 py-1.5 text-sm font-medium text-white backdrop-blur-sm transition-opacity hover:bg-black/65 disabled:opacity-50"
            >
              {screenshotBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              Refresh
            </button>
          </div>
        </div>

        {/* Composer card */}
        <div className="px-4 mt-3">
          <div className="overflow-hidden rounded-2xl border border-[rgba(255,255,255,0.1)] bg-[rgba(255,255,255,0.04)]">

            {/* Destination row */}
            <div className="flex items-center justify-between px-4 py-3">
              <div className="flex min-w-0 items-center gap-2.5">
                <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-blue-400/30 bg-blue-500/15 text-blue-300">
                  <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                    <path d="M5 2v6M2 5h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                  </svg>
                </div>
                <span className="truncate text-sm text-white/90">{destinationLabel}</span>
              </div>
              <button
                type="button"
                onClick={() => setShowDestinationEditor((current) => !current)}
                className="ml-3 shrink-0 rounded-lg bg-white/10 px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-white/[0.15]"
              >
                {showDestinationEditor ? "Done" : "Change"}
              </button>
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
                    {topicsLoading ? (
                      <div className="flex items-center gap-1.5 text-xs text-white/50">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        Loading topics...
                      </div>
                    ) : topics.length > 0 ? (
                      <select
                        value={selectedTopicId !== null ? String(selectedTopicId) : ""}
                        onChange={(event) => setSelectedTopicId(Number(event.target.value))}
                        className={fieldClass}
                      >
                        {topics.map((topic) => (
                          <option key={topic.id} value={String(topic.id)}>
                            {(topic.pinned ? "[PIN] " : "") + topic.title}
                          </option>
                        ))}
                      </select>
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
                  <div className="rounded-lg border border-cyan-200/10 bg-[#0b182b]/55 px-3 py-2.5 text-xs text-white/70">
                    Connect your TWF account to post.{" "}
                    <a href={`${API_ORIGIN}/auth/twf/start`} className="font-semibold text-cyan-300 hover:text-cyan-200">
                      Connect TWF →
                    </a>
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
              // eslint-disable-next-line jsx-a11y/no-autofocus
              autoFocus
              value={content}
              onChange={(event) => handleMessageChange(event.target.value)}
              maxLength={500}
              placeholder="What do you see in this data…"
              className="w-full resize-none bg-transparent px-4 py-3 text-sm text-white outline-none placeholder:text-white/35"
              style={{ minHeight: "72px" }}
              rows={3}
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
              <span className="min-w-0 truncate text-xs text-white/35">
                {payload.detailsSummary || payload.summary}
              </span>
            </div>
          </div>
        </div>

        {/* Character count */}
        <div className="mt-1.5 px-4 text-right">
          <span className="text-xs text-white/35">{content.length} / 500</span>
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
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => { void handleCopyLink(); }}
              className="flex items-center gap-1.5 rounded-xl border border-white/15 bg-white/[0.07] px-3 py-2 text-sm font-medium text-white/80 transition-colors hover:bg-white/[0.11]"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              {linkCopied ? "Copied!" : "Link"}
            </button>
            <button
              type="button"
              onClick={() => { void handleCopyText(); }}
              className="flex items-center gap-1.5 rounded-xl border border-white/15 bg-white/[0.07] px-3 py-2 text-sm font-medium text-white/80 transition-colors hover:bg-white/[0.11]"
            >
              <Copy className="h-3.5 w-3.5" />
              {textCopied ? "Copied!" : "Text"}
            </button>
          </div>

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
        </div>
      </div>
    </div>
  );
}
