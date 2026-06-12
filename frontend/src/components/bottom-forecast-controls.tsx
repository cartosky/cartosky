import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, MessageSquareText, Pause, Play, Share2, Settings } from "lucide-react";

import type { ViewerLayoutMode } from "@/lib/viewer-layout";
import type { ObservedSourceStatusTone, TimeAxisMode } from "@/lib/time-axis";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { useViewerToolbar } from "@/lib/viewer-toolbar-context";
import { SpeedButton } from "@/components/SpeedButton";
import {
  formatObservedCompactTime,
  formatObservedValidTime,
  formatValidTime,
  validAxisLabel,
} from "@/lib/time-axis";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type BottomForecastControlsProps = {
  forecastHour: number;
  availableFrames: number[];
  onForecastHourChange: (fh: number, reason?: "standard" | "scrub-live" | "scrub-commit") => void;
  onScrubStateChange?: (isScrubbing: boolean) => void;
  isPlaying: boolean;
  setIsPlaying: (value: boolean) => void;
  animationDelayMs: number;
  onSpeedChange: (delayMs: number) => void;
  runDateTimeISO: string | null;
  timeAxisMode?: TimeAxisMode;
  validTimeISO?: string | null;
  frameValidTimesByHour?: Record<number, string>;
  sourceStatusLabel?: string | null;
  sourceStatusDescription?: string | null;
  sourceStatusTone?: ObservedSourceStatusTone | null;
  disabled: boolean;
  playDisabled?: boolean;
  transientStatus?: string | null;
  layoutMode?: ViewerLayoutMode;
  modelLabel?: string | null;
  modelId?: string | null;
  variableId?: string | null;
  variableLabel?: string | null;
  totalForecastHours?: number | null;
  runIsComplete?: boolean;
  runIncompleteLabel?: string | null;
  runIncompleteDescription?: string | null;
  runIncompleteTone?: ObservedSourceStatusTone | null;
};

function formatCpcIssuedDisplay(iso: string | null | undefined): string | null {
  if (!iso) {
    return null;
  }

  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }

  const parts = new Intl.DateTimeFormat("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).formatToParts(parsed);

  const lookup = (type: Intl.DateTimeFormatPartTypes): string => parts.find((part) => part.type === type)?.value ?? "";
  const month = lookup("month");
  const day = lookup("day");
  const year = lookup("year");
  const hour = lookup("hour");
  const minute = lookup("minute");
  const dayPeriod = lookup("dayPeriod").toUpperCase();
  const timeZoneName = lookup("timeZoneName");

  if (!month || !day || !year || !hour || !minute || !dayPeriod) {
    return null;
  }

  return `ISSUED: ${month} ${day}, ${year}, ${hour}:${minute}${dayPeriod}${timeZoneName ? ` ${timeZoneName}` : ""}`;
}

function formatTimelineDisplay(params: {
  modelId?: string | null;
  runDateISO: string | null;
  forecastHour: number;
  timeAxisMode: TimeAxisMode;
  variableId?: string | null;
  validTimeISO?: string | null;
}): {
  primary: string;
  secondary: string;
  compactValue: string;
  shortDate: string;
  axisLabel: string;
} | null {
  if (params.modelId === "cpc") {
    const issuedAt = formatCpcIssuedDisplay(params.runDateISO);
    if (issuedAt) {
      return {
        primary: issuedAt,
        secondary: "",
        compactValue: issuedAt,
        shortDate: issuedAt,
        axisLabel: "Issued",
      };
    }
  }

  if (params.timeAxisMode === "observed") {
    const primary = formatObservedValidTime(params.validTimeISO);
    const compactValue = formatObservedCompactTime(params.validTimeISO);
    if (!primary || !compactValue) {
      return null;
    }
    return {
      primary,
      secondary: "Observed",
      compactValue,
      shortDate: compactValue,
      axisLabel: "Observed Time",
    };
  }

  if (params.timeAxisMode === "valid") {
    const primary = formatValidTime(params.validTimeISO, params.variableId);
    if (!primary) {
      return null;
    }
    const secondary = validAxisLabel(params.forecastHour, params.variableId, params.runDateISO, params.validTimeISO);
    return {
      primary,
      secondary,
      compactValue: secondary,
      shortDate: primary,
      axisLabel: "Valid Time",
    };
  }

  if (!params.runDateISO) return null;

  try {
    const runDate = new Date(params.runDateISO);
    if (Number.isNaN(runDate.getTime())) return null;

    const validDate = new Date(runDate.getTime() + params.forecastHour * 60 * 60 * 1000);

    const primary = new Intl.DateTimeFormat("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(validDate);

    const rawShort = new Intl.DateTimeFormat("en-US", {
      weekday: "short",
      month: "numeric",
      day: "numeric",
      year: "2-digit",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    }).format(validDate);
    // Trim spaces before AM/PM: "3:00 PM" → "3:00PM"
    const shortDate = rawShort.replace(/(\d)\s+(AM|PM)/i, "$1$2");

    const secondary = `FH ${params.forecastHour}`;

    return {
      primary,
      secondary,
      compactValue: `${params.forecastHour}h`,
      shortDate,
      axisLabel: "Forecast Hour",
    };
  } catch {
    return null;
  }
}

function statusBadgeClass(tone: ObservedSourceStatusTone | null | undefined): string {
  switch (tone) {
    case "live":
      return "border-emerald-300/35 bg-emerald-300/12 text-emerald-50";
    case "delayed":
      return "border-amber-300/35 bg-amber-300/12 text-amber-50";
    case "stale":
      return "border-orange-300/35 bg-orange-300/14 text-orange-50";
    case "unavailable":
      return "border-rose-300/35 bg-rose-300/12 text-rose-50";
    default:
      return "border-border/35 bg-background/35 text-foreground/90";
  }
}

export function BottomForecastControls({
  forecastHour,
  availableFrames,
  onForecastHourChange,
  onScrubStateChange,
  isPlaying,
  setIsPlaying,
  animationDelayMs,
  onSpeedChange,
  runDateTimeISO,
  timeAxisMode = "forecast",
  validTimeISO = null,
  frameValidTimesByHour,
  sourceStatusLabel = null,
  sourceStatusDescription = null,
  sourceStatusTone = null,
  disabled,
  playDisabled = false,
  transientStatus,
  layoutMode = "desktop",
  modelLabel = null,
  modelId = null,
  variableId = null,
  variableLabel = null,
  totalForecastHours = null,
  runIsComplete = false,
  runIncompleteLabel = null,
  runIncompleteDescription = null,
  runIncompleteTone = null,
}: BottomForecastControlsProps) {
  const toolbar = useViewerToolbar();
  const onShare = toolbar?.onShare;
  const onFeedback = toolbar?.onFeedback;
  const onOpenControls = toolbar?.onMobileControlsOpenChange;
  const DRAG_UPDATE_MS = 48;
  const [previewHour, setPreviewHour] = useState<number | null>(null);
  const [isScrubbing, setIsScrubbing] = useState(false);
  const staticSnapshotLabel: string | null = (() => {
    if (modelId === "cpc") return "Latest forecast";
    if (modelId === "nws_hazards") return "Latest hazards";
    if (modelId === "mrms" && variableId === "mrms_recent_precip_72h") return "Latest observations";
    return null;
  })();
  const lastDragEmitAtRef = useRef(0);
  const lastSentHourRef = useRef<number | null>(null);
  const trailingRafRef = useRef<number | null>(null);
  const pendingEmitRef = useRef<number | null>(null);
  const showInlineSecondary = !(timeAxisMode === "valid" && (variableId === "wgust_6h_max" || variableId === "wgust_24h_max"));

  const validTime = useMemo(
    () => formatTimelineDisplay({
      modelId,
      runDateISO: runDateTimeISO,
      forecastHour: previewHour ?? forecastHour,
      timeAxisMode,
      variableId,
      validTimeISO:
        timeAxisMode === "observed"
          ? frameValidTimesByHour?.[previewHour ?? forecastHour] ?? validTimeISO
          : validTimeISO,
    }),
    [modelId, runDateTimeISO, forecastHour, previewHour, timeAxisMode, variableId, validTimeISO, frameValidTimesByHour]
  );

  const hasFrames = availableFrames.length > 0;
  const isDesktopLayout = layoutMode === "desktop" || layoutMode === "tablet-touch";
  const isTabletTouchLayout = layoutMode === "tablet-touch";
  const controlsLayerClassName = isDesktopLayout || isTabletTouchLayout ? "z-[70]" : "z-[60]";
  const effectiveHour = previewHour ?? forecastHour;
  const sliderIndex = Math.max(0, availableFrames.indexOf(effectiveHour));
  const availableForecastHours = useMemo(() => {
    const finiteFrames = availableFrames.filter(Number.isFinite);
    return finiteFrames.length > 0 ? Math.max(...finiteFrames) : 0;
  }, [availableFrames]);
  const freshnessTotal = Number.isFinite(totalForecastHours) ? Math.max(0, Number(totalForecastHours)) : null;
  const cappedAvailableForecastHours = freshnessTotal !== null
    ? Math.max(0, Math.min(availableForecastHours, freshnessTotal))
    : availableForecastHours;
  const hasFreshnessTotal = freshnessTotal !== null && freshnessTotal > 0;
  const showFreshnessStrip = !isDesktopLayout && timeAxisMode !== "observed" && hasFreshnessTotal;
  const freshnessProgressPercent = hasFreshnessTotal
    ? Math.max(0, Math.min(100, (cappedAvailableForecastHours / freshnessTotal) * 100))
    : 0;
  const enhancedAvailabilityTrack = hasFreshnessTotal && timeAxisMode !== "observed";
  const desktopEnhancedTrack = isDesktopLayout && hasFreshnessTotal && timeAxisMode !== "observed";
  const publishedFrames = useMemo(() => {
    if (!enhancedAvailabilityTrack) {
      return availableFrames;
    }
    const published = availableFrames.filter((frame) => Number.isFinite(frame) && frame <= cappedAvailableForecastHours);
    return published.length > 0 ? published : availableFrames.slice(0, 1);
  }, [availableFrames, cappedAvailableForecastHours, enhancedAvailabilityTrack]);
  const publishedSliderIndex = Math.max(0, publishedFrames.indexOf(effectiveHour));
  const desktopInteractiveTrackPercent = desktopEnhancedTrack
    ? runIsComplete
      ? 100
      : Math.max(0.5, freshnessProgressPercent)
    : 100;
  const mobileEnhancedTrack = !isDesktopLayout && enhancedAvailabilityTrack;
  const mobileInteractiveTrackPercent = mobileEnhancedTrack
    ? runIsComplete
      ? 100
      : Math.max(0.5, freshnessProgressPercent)
    : 100;
  const trackHatchStyle = {
    backgroundImage:
      "repeating-linear-gradient(135deg, rgba(148,163,184,0.18) 0px, rgba(148,163,184,0.18) 3px, rgba(15,23,42,0.2) 3px, rgba(15,23,42,0.2) 7px)",
  };
  const desktopSliderClassName = cn(
    "absolute inset-x-0 top-1/2 w-full -translate-y-1/2 transition-opacity duration-150 [&>*:first-child]:h-2 [&>*:nth-child(2)]:h-4 [&>*:nth-child(2)]:w-4",
    desktopEnhancedTrack
      ? "[&>*:first-child]:bg-transparent [&>*:first-child>*:first-child]:bg-transparent"
      : "[&>*:first-child]:bg-white/[0.12] [&>*:first-child>*:first-child]:bg-gradient-to-r [&>*:first-child>*:first-child]:from-cyan-400 [&>*:first-child>*:first-child]:via-sky-300 [&>*:first-child>*:first-child]:to-slate-200"
  );

  useEffect(() => {
    setPreviewHour(null);
  }, [forecastHour]);

  useEffect(() => {
    onScrubStateChange?.(isScrubbing);
  }, [isScrubbing, onScrubStateChange]);

  useEffect(() => {
    if (isPlaying && isScrubbing) {
      setIsScrubbing(false);
    }
  }, [isPlaying, isScrubbing]);

  useEffect(() => {
    lastSentHourRef.current = forecastHour;
  }, [forecastHour]);

  // Clean up any pending trailing rAF on unmount.
  useEffect(() => {
    return () => {
      if (trailingRafRef.current !== null) {
        cancelAnimationFrame(trailingRafRef.current);
      }
    };
  }, []);

  const emitForecastHour = (next: number, force: boolean) => {
    const now = Date.now();
    const shouldEmit =
      force ||
      (lastSentHourRef.current !== next && now - lastDragEmitAtRef.current >= DRAG_UPDATE_MS);
    if (shouldEmit) {
      // Cancel any pending trailing emission since we're emitting now.
      if (trailingRafRef.current !== null) {
        cancelAnimationFrame(trailingRafRef.current);
        trailingRafRef.current = null;
      }
      pendingEmitRef.current = null;
      lastDragEmitAtRef.current = now;
      lastSentHourRef.current = next;
      onForecastHourChange(next, force ? "scrub-commit" : "scrub-live");
      return;
    }
    // Schedule a trailing emission so the final scrub position is always
    // delivered, even if the throttle window hasn't elapsed yet.
    if (lastSentHourRef.current !== next) {
      pendingEmitRef.current = next;
      if (trailingRafRef.current === null) {
        trailingRafRef.current = requestAnimationFrame(() => {
          trailingRafRef.current = null;
          const pending = pendingEmitRef.current;
          if (pending !== null && lastSentHourRef.current !== pending) {
            pendingEmitRef.current = null;
            lastDragEmitAtRef.current = Date.now();
            lastSentHourRef.current = pending;
            onForecastHourChange(pending, "scrub-live");
          }
        });
      }
    }
  };

  return (
    <TooltipProvider delayDuration={300}>
      <div className={cn("pointer-events-none fixed inset-x-0 bottom-0 flex items-end justify-center px-2 pb-3 sm:px-4 sm:pb-5", controlsLayerClassName)}>
        <div
          className={cn(
            "pointer-events-auto relative flex flex-col",
            isDesktopLayout
              ? "w-full max-w-[45rem] gap-2 rounded-2xl px-5 py-[15px]"
              : isTabletTouchLayout
                ? "w-[min(90vw,560px)] gap-1.5 rounded-3xl p-4"
                : "w-full max-w-3xl gap-2 rounded-[1.6rem] p-5"
          )}
        >
          {/* Blur layer isolated on its own compositor layer — never repaints during slider drag */}
          <div
            aria-hidden="true"
            className={cn(
              "viewer-mobile-surface pointer-events-none absolute inset-0",
              isDesktopLayout ? "rounded-2xl" : isTabletTouchLayout ? "rounded-3xl" : "rounded-[1.6rem]"
            )}
            style={{ willChange: "transform" }}
          />
          {/* Content sits above the blur layer */}
          <div className={cn("relative z-10", isDesktopLayout ? "hidden" : "block")}>
            {/* Row 1: context (model/variable) + action buttons */}
            <div className={cn("flex items-center justify-between gap-2 px-1", isTabletTouchLayout ? "mb-1.5" : "mb-2")}>
              <div className="min-w-0 flex-1">
                {(modelLabel || variableLabel) ? (
                    <div>
                      <div className="flex items-center gap-1.5">
                        {runDateTimeISO ? (
                          <span className="shrink-0 font-['IBM_Plex_Mono',monospace] text-[9px] font-semibold uppercase tracking-[0.18em] text-cyan-300/55">
                            {`${new Date(runDateTimeISO).getUTCHours()}z`}
                          </span>
                        ) : null}
                        {runDateTimeISO && modelLabel ? (
                          <span className="text-[9px] text-cyan-300/30">·</span>
                        ) : null}
                        {modelLabel ? (
                          <span className="shrink-0 font-['IBM_Plex_Mono',monospace] text-[9px] font-semibold uppercase tracking-[0.18em] text-cyan-300/80">
                            {modelLabel}
                          </span>
                        ) : null}
                      </div>
                      {variableLabel ? (
                        <span className="block min-w-0 truncate text-[10px] font-medium text-cyan-200/70 mt-0.5">
                          {variableLabel}
                        </span>
                      ) : null}
                  </div>
                ) : null}
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                {sourceStatusLabel ? (
                  <div
                    data-tour-target={!isDesktopLayout ? "freshness-indicator" : undefined}
                    title={sourceStatusDescription ?? sourceStatusLabel}
                    className={cn(
                      "rounded-md border px-2 py-1 font-['IBM_Plex_Mono',monospace] text-[9px] font-medium uppercase tracking-[0.2em]",
                      statusBadgeClass(sourceStatusTone)
                    )}
                  >
                    {sourceStatusLabel}
                  </div>
                ) : runIncompleteLabel ? (
                  <div
                    data-tour-target={!isDesktopLayout ? "freshness-indicator" : undefined}
                    title={runIncompleteDescription ?? runIncompleteLabel}
                    className={cn(
                      "rounded-md border px-2 py-1 font-['IBM_Plex_Mono',monospace] text-[9px] font-medium uppercase tracking-[0.2em]",
                      statusBadgeClass(runIncompleteTone)
                    )}
                  >
                    {runIncompleteLabel}
                  </div>
                ) : null}
                {onShare ? (
                  <button
                    type="button"
                    onClick={onShare}
                    aria-label="Share"
                    data-tour-target="share-button"
                    className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-white/10 bg-white/[0.05] text-white/60 transition-colors hover:bg-white/[0.09] hover:text-white"
                  >
                    <Share2 className="h-3.5 w-3.5" />
                  </button>
                ) : null}
                {onFeedback ? (
                  <button
                    type="button"
                    onClick={onFeedback}
                    aria-label="Send feedback"
                    data-tour-target="feedback-button"
                    className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-white/10 bg-white/[0.05] text-white/60 transition-colors hover:bg-white/[0.09] hover:text-white"
                  >
                    <MessageSquareText className="h-3.5 w-3.5" />
                  </button>
                ) : null}
                {onOpenControls ? (
                  <button
                    type="button"
                    onClick={() => onOpenControls(true)}
                    aria-label="Open controls"
                    data-tour-target="mobile-controls-button"
                    className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-white/10 bg-white/[0.05] text-white/60 transition-colors hover:bg-white/[0.09] hover:text-white"
                  >
                    <Settings className="h-3.5 w-3.5" />
                  </button>
                ) : null}
              </div>
            </div>

            {transientStatus ? (
              <div className="mb-2 flex items-center gap-1 rounded-md border border-amber-300/25 bg-amber-300/[0.08] px-2 py-1 text-[9px] text-amber-100">
                <AlertCircle className="h-3 w-3" />
                {transientStatus}
              </div>
            ) : null}

            {/* Row 2: play + slider + compact time/FH below */}
            <div className={cn("flex items-center", isTabletTouchLayout ? "gap-2.5" : "gap-3")}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => setIsPlaying(!isPlaying)}
                    disabled={disabled || !hasFrames || playDisabled || staticSnapshotLabel !== null}
                    aria-label={isPlaying ? "Pause animation" : "Play animation"}
                    className={cn(
                      "flex shrink-0 items-center justify-center border transition-all duration-150 disabled:opacity-50 disabled:hover:scale-100",
                      isTabletTouchLayout ? "h-9 w-9 rounded-lg" : "h-10 w-10 rounded-xl",
                      isPlaying
                        ? "bg-cyan-300/[0.12] text-cyan-200 border-cyan-300/30"
                        : "bg-white/[0.05] text-white/80 border-white/10 hover:bg-white/[0.09] hover:text-white"
                    )}
                  >
                    {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4 translate-x-[1px]" />}
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" className="bg-[#07111f] border-white/10 text-white">
                  {isPlaying ? "Pause" : "Play"} animation
                </TooltipContent>
              </Tooltip>

              <SpeedButton animationDelayMs={animationDelayMs} onSpeedChange={onSpeedChange} />

              {staticSnapshotLabel ? (
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 py-1">
                    <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full bg-cyan-400" />
                    <span className="font-['IBM_Plex_Mono',monospace] text-[11px] font-medium uppercase tracking-[0.18em] text-white/40">
                      {staticSnapshotLabel}
                    </span>
                  </div>
                  {validTime ? (
                    <div className="-mt-0.5 text-right font-['IBM_Plex_Mono',monospace] text-[9px] font-medium tracking-[0.06em] text-white/50">
                      {validTime.shortDate}
                      {showInlineSecondary && validTime.secondary && validTime.secondary !== validTime.shortDate ? (
                        <span className="ml-1.5 text-white/32">· {validTime.secondary}</span>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="min-w-0 flex-1">
                  <div className="relative h-8">
                    {mobileEnhancedTrack ? (
                      <>
                        <div
                          aria-hidden="true"
                          className="pointer-events-none absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 overflow-hidden rounded-full bg-white/[0.08]"
                        >
                          <div
                            className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-cyan-400 via-sky-300 to-slate-200"
                            style={{ width: `${freshnessProgressPercent}%` }}
                          />
                          {!runIsComplete ? (
                            <div
                              className="absolute inset-y-0 right-0 bg-slate-500/[0.12]"
                              style={{ left: `${freshnessProgressPercent}%`, ...trackHatchStyle }}
                            />
                          ) : null}
                        </div>
                        {!runIsComplete ? (
                          <div
                            aria-hidden="true"
                            className="pointer-events-none absolute top-1/2 z-20 h-3.5 w-px -translate-x-1/2 -translate-y-1/2 rounded-full bg-emerald-300 shadow-[0_0_8px_rgba(110,231,183,0.65)]"
                            style={{ left: `${freshnessProgressPercent}%` }}
                          />
                        ) : null}
                      </>
                    ) : null}
                    <div
                      className="absolute inset-y-0 left-0"
                      style={{ width: mobileEnhancedTrack ? `${mobileInteractiveTrackPercent}%` : "100%" }}
                    >
                      <Slider
                        value={[mobileEnhancedTrack ? publishedSliderIndex : sliderIndex]}
                        onValueChange={([value]) => {
                          const frames = mobileEnhancedTrack ? publishedFrames : availableFrames;
                          const next = frames[Math.round(value ?? 0)];
                          if (Number.isFinite(next)) {
                            if (!isScrubbing) {
                              setIsScrubbing(true);
                            }
                            setPreviewHour(next);
                            emitForecastHour(next, false);
                          }
                        }}
                        onValueCommit={([value]) => {
                          const frames = mobileEnhancedTrack ? publishedFrames : availableFrames;
                          const next = frames[Math.round(value ?? 0)];
                          if (Number.isFinite(next)) {
                            setPreviewHour(null);
                            setIsScrubbing(false);
                            emitForecastHour(next, true);
                          }
                        }}
                        min={0}
                        max={Math.max(0, (mobileEnhancedTrack ? publishedFrames : availableFrames).length - 1)}
                        step={1}
                        disabled={disabled || isPlaying || !hasFrames || (mobileEnhancedTrack && publishedFrames.length === 0)}
                        className={cn(
                          "absolute inset-x-0 top-1/2 w-full -translate-y-1/2 transition-opacity duration-150 [&>*:first-child]:h-1.5",
                          mobileEnhancedTrack
                            ? "[&>*:first-child]:bg-transparent [&>*:first-child>*:first-child]:bg-transparent"
                            : "[&>*:first-child]:bg-white/[0.12] [&>*:first-child>*:first-child]:bg-gradient-to-r [&>*:first-child>*:first-child]:from-cyan-400 [&>*:first-child>*:first-child]:via-sky-300 [&>*:first-child>*:first-child]:to-slate-200"
                        )}
                      />
                    </div>
                  </div>
                  {validTime ? (
                    <div className="-mt-0.5 text-right font-['IBM_Plex_Mono',monospace] text-[9px] font-medium tracking-[0.06em] text-white/50">
                      {validTime.shortDate}
                      {showInlineSecondary && validTime.secondary && validTime.secondary !== validTime.shortDate ? (
                        <span className="ml-1.5 text-white/32">· {validTime.secondary}</span>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              )}
            </div>

            {showFreshnessStrip && freshnessTotal !== null ? (
              <>
                <div className="mt-2 border-t border-white/[0.08]" />
                <div className="flex items-center gap-2 px-0.5 pt-2 font-['IBM_Plex_Mono',monospace] text-[9px] font-medium text-white/55">
                  <span
                    aria-hidden="true"
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      runIsComplete ? "bg-emerald-300 shadow-[0_0_8px_rgba(110,231,183,0.45)]" : "bg-emerald-400"
                    )}
                  />
                  <span className="shrink-0 tabular-nums text-emerald-100/80">
                    {runIsComplete
                      ? `${cappedAvailableForecastHours}/${freshnessTotal} hrs complete`
                      : `${cappedAvailableForecastHours}/${freshnessTotal} hrs available`}
                  </span>
                  <div className="h-px min-w-[3rem] flex-1 overflow-hidden rounded-full bg-white/[0.12]">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-emerald-500/55 via-emerald-400 to-emerald-300"
                      style={{ width: `${freshnessProgressPercent}%` }}
                    />
                  </div>
                  {!runIsComplete ? (
                    <span className="shrink-0 text-emerald-300/75">building...</span>
                  ) : null}
                </div>
              </>
            ) : null}
          </div>

            <div data-tour-target="forecast-scrubber" className={isDesktopLayout ? "relative z-10 flex items-center gap-3" : "hidden"}>
              <div className="flex shrink-0 items-center gap-2">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() => setIsPlaying(!isPlaying)}
                      disabled={disabled || !hasFrames || playDisabled || staticSnapshotLabel !== null}
                      aria-label={isPlaying ? "Pause animation" : "Play animation"}
                      className={cn(
                        "flex h-9 w-9 items-center justify-center rounded-xl border transition-all duration-150 disabled:opacity-50",
                        isPlaying
                          ? "bg-cyan-300/10 text-cyan-200 border-white/12 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]"
                          : "bg-white/[0.06] text-white/70 border-white/12 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] hover:text-white hover:bg-white/[0.1]"
                      )}
                    >
                      {isPlaying ? (
                        <Pause className="h-[17px] w-[17px]" />
                      ) : (
                        <Play className="h-[17px] w-[17px] translate-x-[1px]" />
                      )}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="bg-[#07111f] border-white/10 text-white">
                    {isPlaying ? "Pause" : "Play"} animation
                  </TooltipContent>
                </Tooltip>
                <SpeedButton animationDelayMs={animationDelayMs} onSpeedChange={onSpeedChange} />
              </div>

              {staticSnapshotLabel ? (
                <div className="flex flex-1 items-center">
                  <div className="flex items-center gap-2 px-0.5">
                    <span aria-hidden="true" className="h-2 w-2 rounded-full bg-cyan-400 shrink-0" />
                    <span className="font-['IBM_Plex_Mono',monospace] text-[11px] font-medium uppercase tracking-[0.18em] text-white/40">
                      {staticSnapshotLabel}
                    </span>
                  </div>
                </div>
              ) : (
                <div className="flex flex-1 items-center">
                  <div className="relative w-full px-0.5">
                    <div className="relative h-10">
                      {desktopEnhancedTrack ? (
                        <>
                          <div
                            aria-hidden="true"
                            className="pointer-events-none absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 overflow-hidden rounded-full bg-white/[0.08]"
                          >
                            <div
                              className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-cyan-400 via-sky-300 to-slate-200"
                              style={{ width: `${freshnessProgressPercent}%` }}
                            />
                            {!runIsComplete ? (
                              <div
                                className="absolute inset-y-0 right-0 bg-slate-500/[0.12]"
                                style={{ left: `${freshnessProgressPercent}%`, ...trackHatchStyle }}
                              />
                            ) : null}
                          </div>
                          {!runIsComplete ? (
                            <div
                              aria-hidden="true"
                              className="pointer-events-none absolute top-1/2 z-20 h-4 w-px -translate-x-1/2 -translate-y-1/2 rounded-full bg-emerald-300 shadow-[0_0_8px_rgba(110,231,183,0.65)]"
                              style={{ left: `${freshnessProgressPercent}%` }}
                            />
                          ) : null}
                        </>
                      ) : null}
                      <div
                        className="absolute inset-y-0 left-0"
                        style={{ width: desktopEnhancedTrack ? `${desktopInteractiveTrackPercent}%` : "100%" }}
                      >
                        <Slider
                          value={[desktopEnhancedTrack ? publishedSliderIndex : sliderIndex]}
                          onValueChange={([value]) => {
                            const frames = desktopEnhancedTrack ? publishedFrames : availableFrames;
                            const next = frames[Math.round(value ?? 0)];
                            if (Number.isFinite(next)) {
                              if (!isScrubbing) {
                                setIsScrubbing(true);
                              }
                              setPreviewHour(next);
                              emitForecastHour(next, false);
                            }
                          }}
                          onValueCommit={([value]) => {
                            const frames = desktopEnhancedTrack ? publishedFrames : availableFrames;
                            const next = frames[Math.round(value ?? 0)];
                            if (Number.isFinite(next)) {
                              setPreviewHour(null);
                              setIsScrubbing(false);
                              emitForecastHour(next, true);
                            }
                          }}
                          min={0}
                          max={Math.max(0, (desktopEnhancedTrack ? publishedFrames : availableFrames).length - 1)}
                          step={1}
                          disabled={disabled || isPlaying || !hasFrames || (desktopEnhancedTrack && publishedFrames.length === 0)}
                          className={desktopSliderClassName}
                        />
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {hasFreshnessTotal && freshnessTotal !== null ? (
                <>
                  <div className="h-9 w-px shrink-0 bg-white/[0.1]" />
                  <div className="flex shrink-0 flex-col items-center gap-0.5 px-1.5 font-['IBM_Plex_Mono',monospace]">
                    <div className="text-[12px] font-semibold leading-none tracking-tight text-emerald-300 tabular-nums">
                      {cappedAvailableForecastHours}
                      <span className="text-[10px] font-medium text-white/40">/{freshnessTotal}</span>
                    </div>
                    <div className="text-[8px] font-medium uppercase leading-none tracking-[0.18em] text-white/45">
                      {runIsComplete ? "HRS COMPLETE" : "HRS READY"}
                    </div>
                  </div>
                </>
              ) : null}

              {sourceStatusLabel ? (
                <>
                  <div className="h-9 w-px shrink-0 bg-white/[0.08]" />
                  <div
                    data-tour-target={isDesktopLayout ? "freshness-indicator" : undefined}
                    title={sourceStatusDescription ?? sourceStatusLabel}
                    className={cn(
                      "flex h-9 shrink-0 items-center rounded-xl border px-2.5 font-['IBM_Plex_Mono',monospace] text-[10px] font-medium uppercase tracking-[0.16em]",
                      statusBadgeClass(sourceStatusTone)
                    )}
                  >
                    {sourceStatusLabel}
                  </div>
                </>
              ) : runIncompleteLabel ? (
                <>
                  <div className="h-9 w-px shrink-0 bg-white/[0.08]" />
                  <div
                    data-tour-target={isDesktopLayout ? "freshness-indicator" : undefined}
                    title={runIncompleteDescription ?? runIncompleteLabel}
                    className={cn(
                      "flex h-9 shrink-0 items-center rounded-xl border px-2.5 font-['IBM_Plex_Mono',monospace] text-[10px] font-medium uppercase tracking-[0.16em]",
                      statusBadgeClass(runIncompleteTone)
                    )}
                  >
                    {runIncompleteLabel}
                  </div>
                </>
              ) : null}

              {hasFreshnessTotal ? (
                <div className="h-9 w-px shrink-0 bg-white/[0.08]" />
              ) : null}

              <div className="flex shrink-0 flex-col items-end gap-0.5 pl-3 sm:min-w-[160px]">
                {transientStatus ? (
                  <div className="flex items-center gap-1.5 rounded-md border border-amber-300/25 bg-amber-300/[0.08] px-2 py-1 text-[10px] text-amber-100">
                    <AlertCircle className="h-3 w-3" />
                    {transientStatus}
                  </div>
                ) : null}
                {validTime ? (
                  <>
                    <span className="text-[12px] font-semibold tracking-tight text-white transition-all duration-200">
                      {validTime.primary}
                    </span>
                    {validTime.secondary ? (
                      <span className="text-[10px] font-medium text-cyan-200/80 transition-all duration-200">
                        {validTime.secondary}
                      </span>
                    ) : null}
                  </>
                ) : (
                  <div className="flex items-center gap-1.5">
                    <AlertCircle className="h-3 w-3 text-white/50" />
                    <span className="text-[10px] text-white/50">
                      {timeAxisMode === "observed" ? "Observed time unavailable" : "Valid time unavailable"}
                    </span>
                  </div>
                )}
              </div>
          </div>
        </div>
      </div>
    </TooltipProvider>
  );
}
