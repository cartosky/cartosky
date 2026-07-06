// TWF posting state for the share modal (share overhaul Phase 2).
// Extracted verbatim from twf-share-modal.tsx — behavior-preserving move only.
// Owns TWF account status, forum/topic loading + cache, share prefs
// persistence, the composer state, and the post/create-topic submit flow.
// The effect dependency arrays here are load-bearing; do not "clean them up".

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { captureProductAnalyticsEvent } from "@/lib/analytics";
import { API_ORIGIN } from "@/lib/config";
import type { ScreenshotExportState } from "@/lib/screenshot_export";
import {
  getSharePrefs,
  setSharePrefs,
  setSharePrefsTopicCacheEntry,
} from "@/lib/share_prefs";
import {
  QUICK_FORUMS,
  captureShareCompleted,
  currentRouteWithSearch,
  forumIdFromPrefs,
  forumModeFromSelection,
  formatForumLabel,
  hydratePersistedTopicCacheEntry,
  isObject,
  isQuickForumId,
  normalizeForums,
  normalizeTopics,
  normalizeTwfStatus,
  readApiError,
  resolveTopicSelection,
  topicTitleForId,
  type ApiErrorInfo,
  type SharePayload,
  type ShareMode,
  type SharePostResult,
  type ShareTopicResult,
  type TopicCacheEntry,
  type TwfForum,
  type TwfStatus,
  type TwfTopic,
} from "@/components/share/share-utils";

export type UseTwfPostingParams = {
  open: boolean;
  onClose: () => void;
  payload: SharePayload;
  clerkLoaded: boolean;
  isSignedIn: boolean | undefined;
  twfFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  /** Screenshot pieces the submit flow depends on (owned by useScreenshotCapture). */
  includeScreenshotInPost: boolean;
  ensurePreparedScreenshot: () => Promise<string | null>;
  screenshotUploadError: string | null;
  screenshotError: string | null;
};

export function useTwfPosting({
  open,
  onClose,
  payload,
  clerkLoaded,
  isSignedIn,
  twfFetch,
  includeScreenshotInPost,
  ensurePreparedScreenshot,
  screenshotUploadError,
  screenshotError,
}: UseTwfPostingParams) {
  const initialSharePrefs = useMemo(() => getSharePrefs(), []);
  const wasOpenRef = useRef(false);
  const destinationSavedTimerRef = useRef<number | null>(null);
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
  const [contentDirty, setContentDirty] = useState(false);
  const [showDestinationEditor, setShowDestinationEditor] = useState(false);
  const [destinationSaved, setDestinationSaved] = useState(false);

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
      topics: entry.topics.map((topic) => ({
        ...topic,
        url: topic.url ?? "",
      })),
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
    return customForum ? formatForumLabel(customForum) : `Forum ${selectedForumId}`;
  }, [forums, selectedForumId, showOtherForums]);
  const showTopicsLoadingState =
    topicsLoading ||
    (open &&
      selectedForumId > 0 &&
      topics.length === 0 &&
      (!statusResolved || statusLoading || topicsForumId !== selectedForumId));

  // Reset composer state each time the modal opens (split out of the old
  // single open-reset effect; same open-transition guard semantics).
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
    setStatusResolved(false);
    setSubmitError(null);
    setSubmitSuccess(null);
    setSubmitTopicSuccess(null);
    setSubmitTopicTitle(null);
    setRetryAfterSeconds(null);
    setShowDestinationEditor(false);
    setDestinationSaved(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    };
  }, []);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, selectedForumId, statusResolved, twfFetch, twfStatus]);

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
        captureShareCompleted("twf_post", { share_mode: shareMode });
        setSubmitTopicSuccess(result);
        setSubmitTopicTitle(result.title);
      } else {
        const result = (await response.json()) as SharePostResult;
        if (!Number.isFinite(Number(result.postId)) || typeof result.postUrl !== "string") {
          setSubmitError({ message: "Unexpected response from server." });
          return;
        }
        captureShareCompleted("twf_post", { share_mode: shareMode });
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

  useEffect(() => {
    if (!submitSuccess && !submitTopicSuccess) {
      return;
    }
    const timer = setTimeout(() => onClose(), 2000);
    return () => clearTimeout(timer);
  }, [submitSuccess, submitTopicSuccess, onClose]);

  // share_initiated fires once per modal open with the linked state known at
  // that moment (same semantics as the old single open-reset effect).
  const shareInitiatedTrackedRef = useRef(false);
  useEffect(() => {
    if (!open) {
      shareInitiatedTrackedRef.current = false;
      return;
    }
    if (shareInitiatedTrackedRef.current) {
      return;
    }
    shareInitiatedTrackedRef.current = true;
    captureProductAnalyticsEvent("share_initiated", {
      user_type: twfStatus.linked ? "twf" : "anonymous",
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return {
    twfStatus,
    statusResolved,
    statusError,
    connectBusy,
    selectedForumId,
    setSelectedForumId,
    showOtherForums,
    setShowOtherForums,
    forums,
    forumsLoading,
    forumsError,
    topics,
    topicsLoading,
    topicsError,
    selectedTopicId,
    shareMode,
    setShareMode,
    content,
    setContent,
    setContentDirty,
    newTopicTitle,
    setNewTopicTitle,
    submitBusy,
    submitError,
    retryAfterSeconds,
    submitSuccess,
    submitTopicSuccess,
    selectedTopicTitle,
    selectedForumLabel,
    showTopicsLoadingState,
    showDestinationEditor,
    destinationSaved,
    handleConnectTwf,
    handleSubmitPost,
    handleMessageChange,
    handleTopicSelectionChange,
    handleDestinationEditorToggle,
  };
}
