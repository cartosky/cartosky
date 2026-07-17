// Share modal shell (share overhaul Phase 2, plan §3.3): Image | GIF | Link
// tabs with TWF posting as a destination *inside* the Image tab — never a gate
// in front of the image. State lives in useScreenshotCapture / useTwfPosting /
// useGifExport; this component is presentation + small copy-action state.

import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";
import { useAuth } from "@clerk/react";
import { CheckCircle2, Copy, Download, ExternalLink, Film, Link2, Loader2, Play, RefreshCw, Share2, X } from "lucide-react";
import { Link } from "react-router-dom";

import { HexSignalRing } from "@/components/HexSignalRing";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { clerkJwtTemplate } from "@/lib/admin-api";
import { SERVER_SCREENSHOT_ENABLED } from "@/lib/config";
import { uploadShareMedia } from "@/lib/share_media";
import type { LegendPayload } from "@/components/map-legend";
import type { ScreenshotExportState } from "@/lib/screenshot_export";
import {
  QUICK_FORUMS,
  captureShareCompleted,
  formatForumLabel,
  loginRouteForCurrentPage,
  screenshotFilename,
  writeClipboard,
  type SharePayload,
  type ShareMode,
} from "@/components/share/share-utils";
import {
  GIF_SPEED_PRESETS,
  GIF_TREND_SPEED_PRESETS,
  useGifExport,
  type GifExportMode,
  type GifFrameDriver,
} from "@/components/share/useGifExport";
import { useScreenshotCapture } from "@/components/share/useScreenshotCapture";
import { useTwfPosting } from "@/components/share/useTwfPosting";

export type { SharePayload } from "@/components/share/share-utils";

type ShareTab = "image" | "gif" | "link";

type ShareModalProps = {
  open: boolean;
  onClose: () => void;
  payload: SharePayload;
  buildScreenshotState?: () => ScreenshotExportState | null;
  getLegend?: () => LegendPayload | null;
  getDraftDataUrl?: () => Promise<string | null>;
  /** Repaint-then-read PNG capture of the live map canvas (WYSIWYG local share). */
  captureMapPng?: () => Promise<string | null>;
  /** Compare-mode GIF is out of scope for v1 — the compare page hides the tab. */
  gifTabEnabled?: boolean;
  /** Viewer frame driver for GIF export; absent → GIF tab shows unavailable. */
  gifFrameDriver?: GifFrameDriver;
};

type DialogPosition = { x: number; y: number };

const DESKTOP_DIALOG_MEDIA = "(min-width: 640px)";
const DIALOG_VIEWPORT_PADDING = 16;

const secondaryButtonClass =
  "inline-flex h-8 items-center rounded-md bg-white/[0.08] px-2.5 text-xs font-medium text-white/86 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-colors hover:bg-white/[0.12]";

const primaryButtonClass =
  "inline-flex h-10 items-center justify-center gap-1.5 rounded-xl border border-cyan-200/30 bg-[linear-gradient(135deg,#102438_0%,#1a4f68_52%,#6ab7d4_100%)] px-4 text-sm font-semibold text-white shadow-[0_14px_34px_rgba(17,68,92,0.34)] transition-all hover:brightness-110 disabled:opacity-60 disabled:hover:brightness-100";

const fieldClass =
  "viewer-mobile-field h-8 w-full rounded-md px-2 text-xs text-white";

const previewActionButtonClass =
  "flex items-center justify-center rounded-xl border border-white/20 bg-black/50 p-1.5 text-white backdrop-blur-sm transition-opacity hover:bg-black/65";

export function ShareModal({
  open,
  onClose,
  payload,
  buildScreenshotState,
  getLegend,
  getDraftDataUrl,
  captureMapPng,
  gifTabEnabled = true,
  gifFrameDriver,
}: ShareModalProps) {
  const { getToken, isLoaded: clerkLoaded, isSignedIn } = useAuth();

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

  const screenshot = useScreenshotCapture({
    open,
    permalink: payload.permalink,
    buildScreenshotState,
    getLegend,
    getDraftDataUrl,
    captureMapPng,
    clerkLoaded,
    isSignedIn,
    getToken,
    twfFetch,
  });

  const gif = useGifExport({
    open,
    frameDriver: gifFrameDriver,
    buildScreenshotState,
    getLegend,
  });

  const [activeTab, setActiveTab] = useState<ShareTab>("image");

  const gifFilename = (): string => {
    const state = buildScreenshotState?.();
    const base = state ? screenshotFilename(state).replace(/\.png$/, "") : "cartosky-map";
    return `${base}.gif`;
  };

  // TWF posts from the GIF tab upload the encoded GIF instead of the still
  // (§3.3: TWF is a destination inside the Image AND GIF tabs). Cached per
  // blob so post retries don't re-upload.
  const gifUploadCacheRef = useRef<{ blob: Blob; url: string } | null>(null);
  const ensurePreparedGifUrl = async (): Promise<string | null> => {
    const blob = gif.gifBlob;
    if (!blob) {
      return null;
    }
    if (gifUploadCacheRef.current?.blob === blob) {
      return gifUploadCacheRef.current.url;
    }
    try {
      if (!clerkLoaded || !isSignedIn) {
        return null;
      }
      const token = await getToken({ template: clerkJwtTemplate() });
      if (!token) {
        return null;
      }
      const state = buildScreenshotState?.() ?? null;
      const result = await uploadShareMedia({
        blob,
        filename: gifFilename(),
        authToken: token,
        model: state?.model ?? null,
        run: state?.run ?? null,
        fh: state?.fh ?? null,
        variable: state?.variable.key || state?.variable.label || null,
        region: state?.region?.id ?? null,
      });
      gifUploadCacheRef.current = { blob, url: result.url };
      return result.url;
    } catch {
      return null;
    }
  };

  // Mode/run-count breakdown for gif share events. Settings can't change
  // while a generated GIF is showing (the ready view replaces the controls),
  // so they still describe the artifact being shared.
  const gifShareAnalytics = () => ({
    gif_mode: gif.settings.mode,
    gif_frame_count: gif.gifFrameCount,
    ...(gif.settings.mode === "trend"
      ? { trend_run_count: Math.min(gif.settings.trendRunCount, Math.max(2, gif.trendRunsAvailable)) }
      : {}),
  });

  const posting = useTwfPosting({
    open,
    onClose,
    payload,
    clerkLoaded,
    isSignedIn,
    twfFetch,
    includeScreenshotInPost: activeTab === "gif" ? true : screenshot.includeScreenshotInPost,
    ensurePreparedScreenshot:
      activeTab === "gif" ? ensurePreparedGifUrl : screenshot.ensurePreparedScreenshot,
    screenshotUploadError: screenshot.screenshotUploadError,
    screenshotError:
      activeTab === "gif" ? "Generate a GIF before posting." : screenshot.screenshotError,
    postArtifact: activeTab === "gif" ? "gif" : "image",
    postAnalytics: activeTab === "gif" ? gifShareAnalytics() : {},
  });
  const [linkCopied, setLinkCopied] = useState(false);
  const [textCopied, setTextCopied] = useState(false);
  const [imageCopied, setImageCopied] = useState(false);
  const [dialogPosition, setDialogPosition] = useState<DialogPosition>({ x: 0, y: 0 });
  const dialogPanelRef = useRef<HTMLDivElement | null>(null);
  const dialogDragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    origin: DialogPosition;
  } | null>(null);
  const wasOpenRef = useRef(false);

  useEffect(() => {
    if (!open) {
      wasOpenRef.current = false;
      return;
    }
    if (wasOpenRef.current) {
      return;
    }
    wasOpenRef.current = true;
    setActiveTab("image");
    setLinkCopied(false);
    setTextCopied(false);
    setImageCopied(false);
    setDialogPosition({ x: 0, y: 0 });
  }, [open]);

  const clampDialogPosition = useCallback((position: DialogPosition): DialogPosition => {
    if (typeof window === "undefined" || !window.matchMedia(DESKTOP_DIALOG_MEDIA).matches) {
      return { x: 0, y: 0 };
    }
    const panel = dialogPanelRef.current;
    if (!panel) {
      return position;
    }
    const bounds = panel.getBoundingClientRect();
    const maxX = Math.max(0, (window.innerWidth - bounds.width) / 2 - DIALOG_VIEWPORT_PADDING);
    const maxY = Math.max(0, (window.innerHeight - bounds.height) / 2 - DIALOG_VIEWPORT_PADDING);
    return {
      x: Math.max(-maxX, Math.min(maxX, position.x)),
      y: Math.max(-maxY, Math.min(maxY, position.y)),
    };
  }, []);

  const handleDialogPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (
      event.button !== 0
      || typeof window === "undefined"
      || !window.matchMedia(DESKTOP_DIALOG_MEDIA).matches
      || (event.target as HTMLElement).closest("button, a, input, select, textarea, [role='button']")
    ) {
      return;
    }
    dialogDragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin: dialogPosition,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  };

  const handleDialogPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dialogDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) {
      return;
    }
    setDialogPosition(clampDialogPosition({
      x: drag.origin.x + event.clientX - drag.startX,
      y: drag.origin.y + event.clientY - drag.startY,
    }));
  };

  const finishDialogDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dialogDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) {
      return;
    }
    dialogDragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  useEffect(() => {
    if (!open) {
      return;
    }
    const keepDialogInViewport = () => {
      setDialogPosition((current) => {
        const next = clampDialogPosition(current);
        return next.x === current.x && next.y === current.y ? current : next;
      });
    };
    window.addEventListener("resize", keepDialogInViewport);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(keepDialogInViewport);
    if (dialogPanelRef.current) {
      observer?.observe(dialogPanelRef.current);
    }
    return () => {
      window.removeEventListener("resize", keepDialogInViewport);
      observer?.disconnect();
    };
  }, [clampDialogPosition, open]);

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

  const handleCopyLink = async () => {
    const ok = await writeClipboard(payload.permalink);
    if (ok) {
      captureShareCompleted("copy", { copy_variant: "link" });
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 1500);
    }
  };

  const handleCopyText = async () => {
    const text = `${posting.content.trim() || payload.summary}\n${payload.permalink}`;
    const ok = await writeClipboard(text);
    if (ok) {
      captureShareCompleted("copy", { copy_variant: "text_link" });
      setTextCopied(true);
      setTimeout(() => setTextCopied(false), 1500);
    }
  };

  const canCopyImage =
    typeof ClipboardItem !== "undefined" &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.clipboard) &&
    "write" in navigator.clipboard;
  const canNativeShare = typeof navigator !== "undefined" && typeof navigator.share === "function";

  const handleCopyImage = async () => {
    if (!screenshot.screenshotBlob) {
      return;
    }
    try {
      await navigator.clipboard.write([
        new ClipboardItem({ "image/png": screenshot.screenshotBlob }),
      ]);
      captureShareCompleted("copy", { copy_variant: "image" });
      setImageCopied(true);
      setTimeout(() => setImageCopied(false), 1500);
    } catch {
      // Clipboard write denied or unsupported at call time — no state change.
    }
  };

  const handleNativeShare = async () => {
    if (!screenshot.screenshotBlob) {
      return;
    }
    const file = new File(
      [screenshot.screenshotBlob],
      screenshot.screenshotFilenameValue || "cartosky-map-screenshot.png",
      { type: "image/png" },
    );
    const fileShare: ShareData = { files: [file] };
    try {
      if (typeof navigator.canShare !== "function" || navigator.canShare(fileShare)) {
        await navigator.share(fileShare);
        captureShareCompleted("native_share", { share_payload: "image" });
        return;
      }
      // File sharing unsupported — share the permalink instead.
      await navigator.share({ url: payload.permalink, text: payload.summary });
      captureShareCompleted("native_share", { share_payload: "link" });
    } catch {
      // AbortError (user dismissed the share sheet) or unsupported — no event.
    }
  };

  const handleGifDownload = () => {
    if (!gif.gifBlobUrl) {
      return;
    }
    const link = document.createElement("a");
    link.href = gif.gifBlobUrl;
    link.download = gifFilename();
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
    captureShareCompleted("gif", { gif_action: "download", ...gifShareAnalytics() });
  };

  const handleGifNativeShare = async () => {
    if (!gif.gifBlob) {
      return;
    }
    const file = new File([gif.gifBlob], gifFilename(), { type: "image/gif" });
    const fileShare: ShareData = { files: [file] };
    try {
      if (typeof navigator.canShare !== "function" || navigator.canShare(fileShare)) {
        await navigator.share(fileShare);
        captureShareCompleted("gif", { gif_action: "native_share", ...gifShareAnalytics() });
      }
    } catch {
      // AbortError (user dismissed the share sheet) or unsupported — no event.
    }
  };

  // Seed the range-preview thumbnail as soon as the GIF tab opens so the
  // slider has visual context before the first drag.
  useEffect(() => {
    if (!open || activeTab !== "gif" || !gif.available || gif.rangePreview) {
      return;
    }
    if (gif.status !== "idle" && gif.status !== "error" && gif.status !== "cancelled") {
      return;
    }
    // Seed at the hour the user was viewing (falls back to the first frame):
    // it keeps the thumbnail on their context and is the natural trend anchor.
    const seedHour = gif.settings.startHour ?? buildScreenshotState?.()?.fh ?? gif.availableHours[0];
    if (Number.isFinite(seedHour)) {
      gif.previewFrame(Number(seedHour));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeTab, gif.available, gif.status, gif.rangePreview]);

  const rangePreviewTimerRef = useRef<number | null>(null);

  const handleGifModeChange = (mode: GifExportMode) => {
    if (mode === gif.settings.mode) {
      return;
    }
    // Each mode gets its own speed scale (3 trend frames want ~1s holds).
    gif.updateSettings({
      mode,
      delayMs: (mode === "trend" ? GIF_TREND_SPEED_PRESETS : GIF_SPEED_PRESETS)[1].delayMs,
    });
  };

  const handleGifTrendHourChange = (value: number[]) => {
    const hours = gif.availableHours;
    const index = value[0];
    if (!Number.isFinite(index) || !Number.isFinite(hours[index])) {
      return;
    }
    gif.updateSettings({ trendHour: hours[index] });
    if (rangePreviewTimerRef.current !== null) {
      window.clearTimeout(rangePreviewTimerRef.current);
    }
    rangePreviewTimerRef.current = window.setTimeout(() => {
      rangePreviewTimerRef.current = null;
      gif.previewFrame(hours[index]);
    }, 140);
  };

  const handleGifRangeChange = (value: number[]) => {
    const hours = gif.availableHours;
    if (hours.length < 2 || value.length < 2) {
      return;
    }
    const startIdx = Math.min(value[0], value[1]);
    const endIdx = Math.max(value[0], value[1]);
    // Which handle moved? Compare against the pre-change settings (state
    // hasn't updated yet inside this handler).
    const prevStartIdx = gif.settings.startHour === null
      ? 0
      : Math.max(0, hours.indexOf(gif.settings.startHour));
    const movedIdx = startIdx !== prevStartIdx ? startIdx : endIdx;
    gif.updateSettings({ startHour: hours[startIdx], endHour: hours[endIdx] });
    // Refresh the in-modal thumbnail to the dragged handle's frame (debounced).
    if (rangePreviewTimerRef.current !== null) {
      window.clearTimeout(rangePreviewTimerRef.current);
    }
    rangePreviewTimerRef.current = window.setTimeout(() => {
      rangePreviewTimerRef.current = null;
      gif.previewFrame(hours[movedIdx]);
    }, 140);
  };

  if (!open) {
    return null;
  }

  const gifHours = gif.availableHours;
  const gifStartIdx = (() => {
    if (gif.settings.startHour === null) return 0;
    const index = gifHours.indexOf(gif.settings.startHour);
    return index === -1 ? 0 : index;
  })();
  const gifEndIdx = (() => {
    if (gif.settings.endHour === null) return Math.max(0, gifHours.length - 1);
    const index = gifHours.indexOf(gif.settings.endHour);
    return index === -1 ? Math.max(0, gifHours.length - 1) : index;
  })();
  const gifTrendIdx = (() => {
    if (gifHours.length === 0) {
      return 0;
    }
    const anchor = gif.settings.trendHour ?? buildScreenshotState?.()?.fh ?? gifHours[0];
    let bestIndex = 0;
    let bestDiff = Number.POSITIVE_INFINITY;
    gifHours.forEach((hour, index) => {
      const diff = Math.abs(hour - Number(anchor));
      if (diff < bestDiff) {
        bestDiff = diff;
        bestIndex = index;
      }
    });
    return bestIndex;
  })();
  const gifSpeedPresets = gif.settings.mode === "trend" ? GIF_TREND_SPEED_PRESETS : GIF_SPEED_PRESETS;
  // 2..N chips where N = runs actually retained right now; the effective
  // selection clamps to availability so eviction mid-open can't strand it.
  const gifTrendRunOptions = Array.from(
    { length: Math.max(0, gif.trendRunsAvailable - 1) },
    (_, index) => index + 2,
  );
  const gifTrendRunCount = Math.min(gif.settings.trendRunCount, Math.max(2, gif.trendRunsAvailable));

  const isPosted = Boolean(posting.submitSuccess || posting.submitTopicSuccess);
  const signedOutLoginUrl = loginRouteForCurrentPage();
  const checkingShareAccess = !clerkLoaded || (isSignedIn && !posting.statusResolved);
  const destinationLabel = posting.selectedTopicTitle
    ? `${posting.selectedForumLabel} › ${posting.selectedTopicTitle}`
    : posting.selectedForumLabel;
  const postButtonDisabled =
    posting.submitBusy || screenshot.screenshotBusy || screenshot.screenshotUploadBusy;

  const tabs: Array<{ id: ShareTab; label: string; icon: typeof Copy }> = [
    { id: "image", label: "Image", icon: Download },
    // Play, not Film: Film's perforation grid anti-aliases into a blur at the
    // 14px tab size; the tab-body Film at 32px is unaffected.
    ...(gifTabEnabled ? [{ id: "gif" as ShareTab, label: "GIF", icon: Play }] : []),
    { id: "link", label: "Link", icon: Link2 },
  ];

  // Rendered on both the Image and GIF tabs (§3.3: TWF is a destination
  // inside each shareable-artifact tab, never a gate).
  const twfSection = (
    <>
              {/* TWF destination section — composer when linked, quiet connect row otherwise */}
              {posting.twfStatus.linked === true ? (
                <div className="mt-3 px-4">
                  <div className="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-white/45">
                    Post to The Weather Forums
                  </div>
                  <div className="viewer-mobile-inset overflow-hidden rounded-2xl">
                    {/* Destination row */}
                    <div className="flex items-start justify-between gap-2 px-4 py-3">
                      <div className="min-w-0">
                        <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-white/45">Posting to:</div>
                        <span className="block text-sm leading-snug text-white/90">{destinationLabel}</span>
                      </div>
                      <div className="ml-3 flex shrink-0 items-center gap-2">
                        {posting.destinationSaved && (
                          <div className="flex items-center gap-1 text-xs font-medium text-emerald-200">
                            <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                            Saved
                          </div>
                        )}
                        <button
                          type="button"
                          onClick={posting.handleDestinationEditorToggle}
                          className="rounded-lg bg-white/10 px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-white/[0.15]"
                        >
                          {posting.showDestinationEditor ? "Done" : "Change"}
                        </button>
                      </div>
                    </div>

                    {/* Destination editor */}
                    {posting.showDestinationEditor && (
                      <div className="space-y-3 border-t border-[rgba(255,255,255,0.08)] px-4 py-3">
                        <div>
                          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-white/50">Share mode</div>
                          <div className="flex items-center gap-2">
                            {(["existing", "new"] as ShareMode[]).map((mode) => (
                              <button
                                key={mode}
                                type="button"
                                onClick={() => posting.setShareMode(mode)}
                                className={[
                                  "inline-flex h-7 items-center rounded-md px-2.5 text-xs font-medium transition-colors",
                                  posting.shareMode === mode
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
                                onClick={() => { posting.setSelectedForumId(forum.id); posting.setShowOtherForums(false); }}
                                className={[
                                  "inline-flex h-7 items-center rounded-md px-2.5 text-xs font-medium transition-colors",
                                  posting.selectedForumId === forum.id && !posting.showOtherForums
                                    ? "bg-cyan-300/18 text-cyan-50 shadow-[inset_0_0_0_1px_rgba(125,211,252,0.22)]"
                                    : "bg-white/[0.07] text-white/70 hover:bg-white/[0.11]",
                                ].join(" ")}
                              >
                                {forum.label}
                              </button>
                            ))}
                            <button
                              type="button"
                              onClick={() => posting.setShowOtherForums(!posting.showOtherForums)}
                              className={[
                                "inline-flex h-7 items-center rounded-md px-2.5 text-xs font-medium transition-colors",
                                posting.showOtherForums
                                  ? "bg-cyan-300/18 text-cyan-50 shadow-[inset_0_0_0_1px_rgba(125,211,252,0.22)]"
                                  : "bg-white/[0.07] text-white/70 hover:bg-white/[0.11]",
                              ].join(" ")}
                            >
                              Other...
                            </button>
                          </div>
                          {posting.showOtherForums && (
                            <div className="mt-2">
                              {posting.forumsLoading ? (
                                <div className="text-xs text-white/50">Loading forums...</div>
                              ) : posting.forums.length > 0 ? (
                                <select
                                  value={String(posting.selectedForumId)}
                                  onChange={(event) => posting.setSelectedForumId(Number(event.target.value))}
                                  className={fieldClass}
                                >
                                  {posting.forums.map((forum) => (
                                    <option key={forum.id} value={String(forum.id)}>
                                      {formatForumLabel(forum)}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <div className="text-xs text-white/50">No accessible forums found.</div>
                              )}
                              {posting.forumsError ? <div className="mt-1 text-xs text-red-200">{posting.forumsError}</div> : null}
                            </div>
                          )}
                        </div>

                        {posting.shareMode === "existing" ? (
                          <div>
                            <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-white/50">Topic</div>
                            {posting.topics.length > 0 ? (
                              <div className="space-y-1.5">
                                <select
                                  value={posting.selectedTopicId !== null ? String(posting.selectedTopicId) : ""}
                                  onChange={(event) => posting.handleTopicSelectionChange(Number(event.target.value))}
                                  className={fieldClass}
                                >
                                  {posting.topics.map((topic) => (
                                    <option key={topic.id} value={String(topic.id)}>
                                      {(topic.pinned ? "[PIN] " : "") + topic.title}
                                    </option>
                                  ))}
                                </select>
                                {posting.topicsLoading ? (
                                  <div className="flex items-center gap-1.5 text-[11px] text-white/45">
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                    Refreshing topics...
                                  </div>
                                ) : null}
                              </div>
                            ) : posting.showTopicsLoadingState ? (
                              <div className="flex items-center gap-1.5 text-xs text-white/50">
                                <Loader2 className="h-3 w-3 animate-spin" />
                                Loading topics...
                              </div>
                            ) : (
                              <div className="text-xs text-white/50">No topics loaded.</div>
                            )}
                            {posting.topicsError ? <div className="mt-1 text-xs text-red-200">{posting.topicsError}</div> : null}
                          </div>
                        ) : (
                          <div>
                            <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-white/50">Topic title</div>
                            <input
                              value={posting.newTopicTitle}
                              onChange={(event) => posting.setNewTopicTitle(event.target.value)}
                              maxLength={255}
                              placeholder="Enter a topic title"
                              className={`${fieldClass} placeholder:text-white/40`}
                            />
                          </div>
                        )}

                        {posting.submitError && (
                          <div className="rounded-lg border border-red-400/25 bg-red-500/10 px-3 py-2 text-xs text-red-100">
                            {posting.submitError.message}
                            {posting.submitError.code ? <div className="mt-0.5 opacity-90">Code: {posting.submitError.code}</div> : null}
                            {posting.retryAfterSeconds ? <div className="mt-0.5 opacity-90">Try again in {posting.retryAfterSeconds}s.</div> : null}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Divider */}
                    <div className="h-px bg-[rgba(255,255,255,0.08)]" />

                    {/* Textarea */}
                    <textarea
                      value={posting.content}
                      onChange={(event) => posting.handleMessageChange(event.target.value)}
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
                          posting.setContent(payload.summary);
                          posting.setContentDirty(true);
                        }}
                        className="flex shrink-0 items-center gap-1.5 rounded-full border border-blue-400/25 bg-blue-500/10 px-3 py-1 text-xs text-blue-200 transition-colors hover:bg-blue-500/20"
                      >
                        ↩ Use model label
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                !isPosted && (
                  <div className="mt-3 px-4">
                    <div className="viewer-mobile-inset flex flex-col gap-2 rounded-2xl px-3.5 py-3 text-sm text-white/78 sm:flex-row sm:items-center sm:justify-between">
                      <div className="flex min-w-0 items-center gap-2">
                        {checkingShareAccess ? (
                          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-cyan-200" />
                        ) : (
                          <ExternalLink className="h-4 w-4 shrink-0 text-cyan-200" />
                        )}
                        <span className="min-w-0 leading-snug">
                          {!clerkLoaded
                            ? "Checking CartoSky sign-in status..."
                            : !isSignedIn
                              ? "Post directly to TWF threads — sign in to connect your account."
                              : !posting.statusResolved
                                ? "Checking your TWF connection..."
                                : "Post directly to TWF threads — connect your account."}
                        </span>
                      </div>
                      {!clerkLoaded || (isSignedIn && !posting.statusResolved) ? null : !isSignedIn ? (
                        <Link
                          to={signedOutLoginUrl}
                          className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-cyan-200/30 bg-cyan-300/12 px-3 text-xs font-semibold text-cyan-100 transition-colors hover:bg-cyan-300/18"
                          onClick={onClose}
                        >
                          Sign in
                        </Link>
                      ) : (
                        <button
                          type="button"
                          onClick={posting.handleConnectTwf}
                          disabled={posting.connectBusy}
                          className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg border border-cyan-200/30 bg-cyan-300/12 px-3 text-xs font-semibold text-cyan-100 transition-colors hover:bg-cyan-300/18 disabled:cursor-wait disabled:opacity-70"
                        >
                          {posting.connectBusy ? "Connecting..." : "Connect TWF"}
                        </button>
                      )}
                    </div>
                    {isSignedIn && (posting.statusError || posting.submitError) && (
                      <div className="mt-2 rounded-lg border border-red-400/25 bg-red-500/10 px-3 py-2 text-xs text-red-100">
                        {posting.statusError ?? posting.submitError?.message}
                      </div>
                    )}
                  </div>
                )
              )}

              {/* Success banner */}
              {isPosted && (
                <div className="mx-4 mt-2 flex items-center gap-2 rounded-lg border border-emerald-400/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                  {posting.submitTopicSuccess ? "Topic created!" : "Posted!"} Closing…
                </div>
              )}

              {/* Error banners (when destination editor is closed) */}
              {posting.twfStatus.linked === true && posting.submitError && !posting.showDestinationEditor && (
                <div className="mx-4 mt-2 rounded-lg border border-red-400/25 bg-red-500/10 px-3 py-2 text-xs text-red-100">
                  {posting.submitError.message}
                </div>
              )}
              {posting.twfStatus.linked === true && isSignedIn && posting.statusError && !posting.showDestinationEditor && (
                <div className="mx-4 mt-2 rounded-lg border border-red-400/25 bg-red-500/10 px-3 py-2 text-xs text-red-100">
                  {posting.statusError}
                </div>
              )}
    </>
  );

  return (
    <div
      className="viewer-mobile-backdrop fixed inset-0 z-[80] flex items-end justify-center sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Share"
      onClick={onClose}
    >
      <div
        ref={dialogPanelRef}
        className="viewer-mobile-surface w-full max-w-[580px] flex flex-col overflow-hidden rounded-t-3xl sm:max-h-[calc(100dvh-2rem)] sm:rounded-2xl"
        style={{
          maxHeight: "calc(100dvh - env(safe-area-inset-top, 0px))",
          transform: `translate3d(${dialogPosition.x}px, ${dialogPosition.y}px, 0)`,
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <div
          className="sm:cursor-grab sm:select-none sm:touch-none sm:active:cursor-grabbing"
          onPointerDown={handleDialogPointerDown}
          onPointerMove={handleDialogPointerMove}
          onPointerUp={finishDialogDrag}
          onPointerCancel={finishDialogDrag}
          onLostPointerCapture={() => { dialogDragRef.current = null; }}
        >
          {/* Drag handle */}
          <div className="flex justify-center pb-1 pt-3">
            <div className="h-1 w-9 rounded-full bg-white/20" />
          </div>

          {/* Title + close */}
          <div className="flex items-center justify-between px-4 pt-2 pb-2">
            <div className="text-base font-semibold text-white">Share this view</div>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md bg-white/[0.08] text-white/70 transition-colors hover:bg-white/[0.12]"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="px-4 pb-3" role="tablist" aria-label="Share format">
          <div className="flex items-center gap-1 rounded-xl bg-white/[0.06] p-1">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={[
                  "flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors",
                  activeTab === tab.id
                    ? "bg-cyan-300/18 text-cyan-50 shadow-[inset_0_0_0_1px_rgba(125,211,252,0.22)]"
                    : "text-white/60 hover:bg-white/[0.07] hover:text-white/85",
                ].join(" ")}
              >
                <tab.icon className="h-3.5 w-3.5" />
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {activeTab === "image" && (
            <>
              {/* Screenshot preview */}
              <TooltipProvider delayDuration={250}>
                <div className="px-4">
                  <div className="relative aspect-[16/9] max-h-[160px] w-full overflow-hidden rounded-2xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)] sm:max-h-none">
                    {screenshot.screenshotBlobUrl ? (
                      <img
                        src={screenshot.screenshotBlobUrl}
                        alt="Screenshot preview"
                        className="h-full w-full object-contain"
                      />
                    ) : screenshot.screenshotBusy && screenshot.draftDataUrl ? (
                      <>
                        <img
                          src={screenshot.draftDataUrl}
                          alt="Draft preview"
                          className="h-full w-full object-contain"
                        />
                        <div className="absolute bottom-2 left-2 rounded-md bg-black/70 px-2 py-1 text-xs text-white/80 backdrop-blur-sm">
                          Generating forum image…
                        </div>
                      </>
                    ) : screenshot.screenshotBusy ? (
                      <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-[#0d1e35] to-[#0a1628]">
                        <div
                          role="status"
                          aria-live="polite"
                          aria-label="Generating screenshot"
                          className="glass-overlay flex min-w-36 flex-col items-center gap-3 rounded-2xl px-5 py-4 shadow-[0_22px_64px_rgba(0,0,0,0.26)]"
                        >
                          <HexSignalRing />
                          <div className="text-center text-xs font-medium text-white/76">
                            Generating screenshot
                          </div>
                        </div>
                      </div>
                    ) : screenshot.screenshotError ? (
                      <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-[#0d1e35] to-[#0a1628]">
                        <div
                          role="alert"
                          className="glass-overlay flex min-w-36 flex-col items-center gap-3 rounded-2xl px-5 py-4 shadow-[0_22px_64px_rgba(0,0,0,0.26)]"
                        >
                          <div className="text-center text-xs font-medium text-white/76">
                            {screenshot.screenshotError.length > 80
                              ? `${screenshot.screenshotError.slice(0, 80)}…`
                              : screenshot.screenshotError}
                          </div>
                          <button
                            type="button"
                            onClick={() => void screenshot.handlePrepareScreenshot()}
                            disabled={!screenshot.canPrepareScreenshot}
                            className={`${secondaryButtonClass} disabled:opacity-50`}
                          >
                            Retry
                          </button>
                          {SERVER_SCREENSHOT_ENABLED && (
                            <div className="text-center text-xs text-white/50">
                              Forum image unavailable — retry or post without image.
                            </div>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div className="h-full w-full bg-gradient-to-br from-[#0d1e35] to-[#0a1628]" />
                    )}
                  </div>

                  <div className="mt-1.5 flex items-center justify-between gap-3 px-1">
                    {screenshot.screenshotBlobUrl ? (
                      <div className="flex items-center gap-1.5 rounded-md bg-black/75 px-2 py-1 text-xs font-medium text-white">
                        <div className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                        Screenshot ready
                      </div>
                    ) : (
                      <div />
                    )}
                    <div className="flex items-center gap-1.5">
                      {screenshot.screenshotBlobUrl && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              onClick={() => {
                                const link = document.createElement("a");
                                link.href = screenshot.screenshotBlobUrl!;
                                link.download = screenshot.screenshotFilenameValue;
                                link.rel = "noopener";
                                document.body.appendChild(link);
                                link.click();
                                link.remove();
                                captureShareCompleted("download");
                              }}
                              className={previewActionButtonClass}
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
                      {screenshot.screenshotBlob && canCopyImage && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              onClick={() => void handleCopyImage()}
                              className={previewActionButtonClass}
                              aria-label="Copy image to clipboard"
                            >
                              {imageCopied
                                ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" />
                                : <Copy className="h-3.5 w-3.5" />}
                            </button>
                          </TooltipTrigger>
                          <TooltipContent side="left" className="border-white/10 bg-[#07111f] text-white">
                            {imageCopied ? "Copied" : "Copy image"}
                          </TooltipContent>
                        </Tooltip>
                      )}
                      {screenshot.screenshotBlob && canNativeShare && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              onClick={() => void handleNativeShare()}
                              className={previewActionButtonClass}
                              aria-label="Share image"
                            >
                              <Share2 className="h-3.5 w-3.5" />
                            </button>
                          </TooltipTrigger>
                          <TooltipContent side="left" className="border-white/10 bg-[#07111f] text-white">
                            Share image
                          </TooltipContent>
                        </Tooltip>
                      )}
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            onClick={() => {
                              screenshot.setHasAttemptedAutoScreenshot(false);
                              void screenshot.handlePrepareScreenshot();
                            }}
                            disabled={!screenshot.canPrepareScreenshot || screenshot.screenshotBusy}
                            className={`${previewActionButtonClass} disabled:opacity-50`}
                            aria-label="Refresh screenshot"
                          >
                            <RefreshCw className="h-3.5 w-3.5" />
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

              {twfSection}
            </>
          )}

          {activeTab === "gif" && (
            <>
            <div className="px-4">
              {gif.status === "ready" && gif.gifBlobUrl ? (
                <>
                  {/* Preview-as-artifact: this <img> plays the exact encoded
                      GIF the user downloads/shares. The height clamp lives on
                      the img (a %-based max-height against the auto-height
                      wrapper clipped portrait GIFs) so the full frame is always
                      visible and the actions stay above the fold on phones. */}
                  <div className="flex w-full items-center justify-center overflow-hidden rounded-2xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)]">
                    <img
                      src={gif.gifBlobUrl}
                      alt="Animated GIF preview"
                      className="h-auto max-h-[38dvh] w-auto max-w-full sm:max-h-[420px]"
                    />
                  </div>
                  <div className="mt-1.5 flex items-center justify-between gap-3 px-1">
                    <div className="flex items-center gap-1.5 rounded-md bg-black/75 px-2 py-1 text-xs font-medium text-white">
                      <div className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                      {gif.gifFrameCount} frames · {(gif.gifBlob!.size / (1024 * 1024)).toFixed(1)} MB
                    </div>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={handleGifDownload}
                        className={previewActionButtonClass}
                        aria-label="Download GIF"
                      >
                        <Download className="h-3.5 w-3.5" />
                      </button>
                      {canNativeShare && (
                        <button
                          type="button"
                          onClick={() => void handleGifNativeShare()}
                          className={previewActionButtonClass}
                          aria-label="Share GIF"
                        >
                          <Share2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => { gif.reset(); }}
                        className={previewActionButtonClass}
                        aria-label="Discard GIF and start over"
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                </>
              ) : gif.status === "capturing" || gif.status === "encoding" ? (
                <div className="flex aspect-[16/9] max-h-[220px] w-full flex-col items-center justify-center gap-3 rounded-2xl border border-[rgba(255,255,255,0.08)] bg-gradient-to-br from-[#0d1e35] to-[#0a1628] px-6 text-center">
                  <HexSignalRing size="sm" />
                  {gif.status === "capturing" ? (
                    <>
                      <div className="text-sm font-semibold text-white/90">
                        Capturing frames… {gif.progress.done}/{gif.progress.total}
                      </div>
                      <div className="h-1.5 w-full max-w-[260px] overflow-hidden rounded-full bg-white/10">
                        <div
                          className="h-full rounded-full bg-cyan-300/80 transition-[width]"
                          style={{ width: `${gif.progress.total > 0 ? Math.round((gif.progress.done / gif.progress.total) * 100) : 0}%` }}
                        />
                      </div>
                      <button
                        type="button"
                        onClick={() => gif.cancel()}
                        className={secondaryButtonClass}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <div className="text-sm font-semibold text-white/90">Encoding GIF…</div>
                  )}
                </div>
              ) : !gif.available ? (
                <div className="flex w-full flex-col items-center justify-center gap-3 rounded-2xl border border-[rgba(255,255,255,0.08)] bg-gradient-to-br from-[#0d1e35] to-[#0a1628] px-6 py-6 text-center">
                  <Film className="h-8 w-8 text-cyan-200/70" />
                  <div className="text-sm font-semibold text-white/90">GIF isn't available for this view</div>
                  <div className="max-w-[320px] text-xs leading-relaxed text-white/55">
                    Pick a product with an animatable forecast timeline, then come back here.
                  </div>
                </div>
              ) : (
                <>
                  {/* Preview gets the full modal width (mirrors the ready
                      state) so the selected frame's data is readable on
                      desktop; controls live in their own section below. */}
                  <div className="flex w-full items-center justify-center overflow-hidden rounded-2xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)]">
                    {gif.rangePreview ? (
                      <img
                        src={gif.rangePreview.url}
                        alt={`Frame preview for FH ${gif.rangePreview.hour}`}
                        className="h-auto max-h-[34dvh] w-auto max-w-full sm:max-h-[380px]"
                      />
                    ) : (
                      <div className="flex aspect-[16/9] max-h-[220px] w-full items-center justify-center">
                        <Film className="h-8 w-8 text-cyan-200/70" />
                      </div>
                    )}
                  </div>
                  {gif.rangePreview && (
                    <div className="mt-1 text-center text-[11px] text-white/50">
                      Previewing FH {gif.rangePreview.hour}
                    </div>
                  )}
                  <div className="mt-2 flex w-full flex-col items-center justify-center gap-3 rounded-2xl border border-[rgba(255,255,255,0.08)] bg-gradient-to-br from-[#0d1e35] to-[#0a1628] px-6 py-4 text-center">
                    <>
                      {(gif.status === "error" || gif.status === "cancelled") && (
                        <div className="max-w-[320px] text-xs leading-relaxed text-red-200/90">
                          {gif.status === "cancelled" ? "GIF generation cancelled." : gif.error}
                        </div>
                      )}
                      <div className="flex w-full max-w-[420px] flex-col gap-2">
                        {gif.trendAvailable && (
                          <div className="flex items-center gap-1.5">
                            {([
                              { id: "hours" as GifExportMode, label: "Forecast loop" },
                              { id: "trend" as GifExportMode, label: "Run trend" },
                            ]).map((mode) => (
                              <button
                                key={mode.id}
                                type="button"
                                onClick={() => handleGifModeChange(mode.id)}
                                className={[
                                  "inline-flex h-7 flex-1 items-center justify-center rounded-md px-2.5 text-xs font-medium transition-colors",
                                  gif.settings.mode === mode.id
                                    ? "bg-cyan-300/18 text-cyan-50 shadow-[inset_0_0_0_1px_rgba(125,211,252,0.22)]"
                                    : "bg-white/[0.07] text-white/70 hover:bg-white/[0.11]",
                                ].join(" ")}
                              >
                                {mode.label}
                              </button>
                            ))}
                          </div>
                        )}
                        {gif.settings.mode === "trend" ? (
                          <>
                            <div className="flex items-center justify-between">
                              <span className="text-[10px] font-semibold uppercase tracking-wider text-white/45">Valid time</span>
                              <span className="text-xs font-medium text-white/70">
                                FH {gifHours[gifTrendIdx] ?? "—"} on the latest run
                              </span>
                            </div>
                            <SliderPrimitive.Root
                              min={0}
                              max={Math.max(1, gifHours.length - 1)}
                              step={1}
                              value={[gifTrendIdx]}
                              onValueChange={handleGifTrendHourChange}
                              className="relative flex h-5 w-full touch-none select-none items-center"
                            >
                              <SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-white/10">
                                <SliderPrimitive.Range className="absolute h-full bg-gradient-to-r from-cyan-700 to-cyan-500" />
                              </SliderPrimitive.Track>
                              <SliderPrimitive.Thumb
                                aria-label="Trend valid time"
                                className="block h-4 w-4 rounded-full border-2 border-cyan-900 bg-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.35)] focus:outline-none"
                              />
                            </SliderPrimitive.Root>
                            {gifTrendRunOptions.length > 1 && (
                              <div className="flex items-center gap-1.5">
                                <span className="text-[10px] font-semibold uppercase tracking-wider text-white/45">Runs</span>
                                {gifTrendRunOptions.map((count) => (
                                  <button
                                    key={count}
                                    type="button"
                                    onClick={() => gif.updateSettings({ trendRunCount: count })}
                                    className={[
                                      "inline-flex h-7 flex-1 items-center justify-center rounded-md px-2 text-xs font-medium transition-colors",
                                      gifTrendRunCount === count
                                        ? "bg-cyan-300/18 text-cyan-50 shadow-[inset_0_0_0_1px_rgba(125,211,252,0.22)]"
                                        : "bg-white/[0.07] text-white/70 hover:bg-white/[0.11]",
                                    ].join(" ")}
                                  >
                                    {count}
                                  </button>
                                ))}
                              </div>
                            )}
                          </>
                        ) : (
                          <>
                            <div className="flex items-center justify-between">
                              <span className="text-[10px] font-semibold uppercase tracking-wider text-white/45">Range</span>
                              <span className="text-xs font-medium text-white/70">
                                FH {gifHours[gifStartIdx] ?? "—"} – FH {gifHours[gifEndIdx] ?? "—"}
                              </span>
                            </div>
                            {/* Dragging a handle steps the in-modal thumbnail to
                                that frame, so the range is picked visually. */}
                            <SliderPrimitive.Root
                              min={0}
                              max={Math.max(1, gifHours.length - 1)}
                              step={1}
                              minStepsBetweenThumbs={1}
                              value={[gifStartIdx, gifEndIdx]}
                              onValueChange={handleGifRangeChange}
                              className="relative flex h-5 w-full touch-none select-none items-center"
                            >
                              <SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-white/10">
                                <SliderPrimitive.Range className="absolute h-full bg-gradient-to-r from-cyan-700 to-cyan-500" />
                              </SliderPrimitive.Track>
                              <SliderPrimitive.Thumb
                                aria-label="First forecast hour"
                                className="block h-4 w-4 rounded-full border-2 border-cyan-900 bg-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.35)] focus:outline-none"
                              />
                              <SliderPrimitive.Thumb
                                aria-label="Last forecast hour"
                                className="block h-4 w-4 rounded-full border-2 border-cyan-900 bg-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.35)] focus:outline-none"
                              />
                            </SliderPrimitive.Root>
                          </>
                        )}
                        <div className="flex items-center justify-center gap-1.5">
                          {gifSpeedPresets.map((preset) => (
                            <button
                              key={preset.id}
                              type="button"
                              onClick={() => gif.updateSettings({ delayMs: preset.delayMs })}
                              className={[
                                "inline-flex h-7 flex-1 items-center justify-center rounded-md px-2.5 text-xs font-medium transition-colors",
                                gif.settings.delayMs === preset.delayMs
                                  ? "bg-cyan-300/18 text-cyan-50 shadow-[inset_0_0_0_1px_rgba(125,211,252,0.22)]"
                                  : "bg-white/[0.07] text-white/70 hover:bg-white/[0.11]",
                              ].join(" ")}
                            >
                              {preset.label}
                            </button>
                          ))}
                        </div>
                      </div>
                      {!gif.buildPlan() && (
                        <div className="max-w-[320px] text-xs leading-relaxed text-red-200/80">
                          {gif.settings.mode === "trend"
                            ? "Run trend needs at least two runs."
                            : "Pick a range with at least two frames."}
                        </div>
                      )}
                      <button
                        type="button"
                        onClick={() => { void gif.generate(); }}
                        disabled={!gif.buildPlan()}
                        className={primaryButtonClass}
                      >
                        <Film className="h-4 w-4" />
                        Generate GIF
                      </button>
                    </>
                  </div>
                </>
              )}
            </div>
            {twfSection}
            </>
          )}

          {activeTab === "link" && (
            <div className="space-y-3 px-4">
              <div className="viewer-mobile-inset rounded-2xl px-4 py-3">
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-white/45">Link to this view</div>
                <div className="break-all text-xs leading-relaxed text-white/75">{payload.permalink}</div>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <button
                  type="button"
                  onClick={() => { void handleCopyLink(); }}
                  className="flex flex-1 items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/[0.07] px-3 py-2.5 text-sm font-medium text-white/85 transition-colors hover:bg-white/[0.11]"
                >
                  {linkCopied ? <CheckCircle2 className="h-4 w-4 text-emerald-300" /> : <ExternalLink className="h-4 w-4" />}
                  {linkCopied ? "Link copied" : "Copy link"}
                </button>
                <button
                  type="button"
                  onClick={() => { void handleCopyText(); }}
                  className="flex flex-1 items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/[0.07] px-3 py-2.5 text-sm font-medium text-white/85 transition-colors hover:bg-white/[0.11]"
                >
                  {textCopied ? <CheckCircle2 className="h-4 w-4 text-emerald-300" /> : <Copy className="h-4 w-4" />}
                  {textCopied ? "Text copied" : "Copy text + link"}
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Bottom action row — Post button lives with the Image tab's TWF section */}
        <div
          className="flex items-center justify-end px-4 pt-3"
          style={{ paddingBottom: "max(1.5rem, env(safe-area-inset-bottom))" }}
        >
          {(activeTab === "image" || activeTab === "gif") && posting.twfStatus.linked === true ? (
            <button
              type="button"
              onClick={() => { void posting.handleSubmitPost(); }}
              disabled={postButtonDisabled || isPosted || (activeTab === "gif" && gif.status !== "ready")}
              className={primaryButtonClass}
            >
              {posting.submitBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {isPosted && <CheckCircle2 className="h-3.5 w-3.5" />}
              {posting.submitBusy
                ? "Posting…"
                : isPosted
                  ? "Posted!"
                  : activeTab === "gif"
                    ? "Post GIF →"
                    : "Post →"}
            </button>
          ) : (
            <button type="button" onClick={onClose} className={secondaryButtonClass}>
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// Back-compat alias: mounts predating the Phase 2 restructure imported
// TwfShareModal; TWF is now a destination inside the Image tab, not the modal.
export { ShareModal as TwfShareModal };
