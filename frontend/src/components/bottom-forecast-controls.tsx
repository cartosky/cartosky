import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Clock, Pause, Play } from "lucide-react";

import type { ViewerLayoutMode } from "@/lib/viewer-layout";
import type { ObservedSourceStatusTone, TimeAxisMode } from "@/lib/time-axis";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import {
  formatObservedCompactTime,
  formatObservedValidTime,
  formatValidTime,
  validDayLabel,
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
  runDateTimeISO: string | null;
  timeAxisMode?: TimeAxisMode;
  validTimeISO?: string | null;
  frameValidTimesByHour?: Record<number, string>;
  sourceStatusLabel?: string | null;
  sourceStatusTone?: ObservedSourceStatusTone | null;
  disabled: boolean;
  playDisabled?: boolean;
  transientStatus?: string | null;
  layoutMode?: ViewerLayoutMode;
  modelLabel?: string | null;
  variableLabel?: string | null;
};

function formatTimelineDisplay(params: {
  runDateISO: string | null;
  forecastHour: number;
  timeAxisMode: TimeAxisMode;
  validTimeISO?: string | null;
}): {
  primary: string;
  secondary: string;
  compactValue: string;
  axisLabel: string;
} | null {
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
      axisLabel: "Observed Time",
    };
  }

  if (params.timeAxisMode === "valid") {
    const primary = formatValidTime(params.validTimeISO);
    if (!primary) {
      return null;
    }
    return {
      primary,
      secondary: validDayLabel(params.forecastHour),
      compactValue: validDayLabel(params.forecastHour),
      axisLabel: "Valid Day",
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

    const secondary = `FH ${params.forecastHour}`;

    return {
      primary,
      secondary,
      compactValue: `${params.forecastHour}h`,
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
  runDateTimeISO,
  timeAxisMode = "forecast",
  validTimeISO = null,
  frameValidTimesByHour,
  sourceStatusLabel = null,
  sourceStatusTone = null,
  disabled,
  playDisabled = false,
  transientStatus,
  layoutMode = "desktop",
  modelLabel = null,
  variableLabel = null,
}: BottomForecastControlsProps) {
  const DRAG_UPDATE_MS = 48;
  const [previewHour, setPreviewHour] = useState<number | null>(null);
  const [isScrubbing, setIsScrubbing] = useState(false);
  const lastDragEmitAtRef = useRef(0);
  const lastSentHourRef = useRef<number | null>(null);
  const trailingRafRef = useRef<number | null>(null);
  const pendingEmitRef = useRef<number | null>(null);

  const validTime = useMemo(
    () => formatTimelineDisplay({
      runDateISO: runDateTimeISO,
      forecastHour: previewHour ?? forecastHour,
      timeAxisMode,
      validTimeISO:
        timeAxisMode === "observed"
          ? frameValidTimesByHour?.[previewHour ?? forecastHour] ?? validTimeISO
          : validTimeISO,
    }),
    [runDateTimeISO, forecastHour, previewHour, timeAxisMode, validTimeISO, frameValidTimesByHour]
  );

  const hasFrames = availableFrames.length > 0;
  const isDesktopLayout = layoutMode === "desktop";
  const isTabletTouchLayout = layoutMode === "tablet-touch";
  const effectiveHour = previewHour ?? forecastHour;
  const sliderIndex = Math.max(0, availableFrames.indexOf(effectiveHour));

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
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex items-end justify-center px-2 pb-3 sm:px-4 sm:pb-5">
        <div
          className={cn(
            "pointer-events-auto relative flex flex-col",
            isDesktopLayout
              ? "w-full max-w-[42rem] gap-2 rounded-2xl px-5 py-3.5"
              : isTabletTouchLayout
                ? "w-[min(90vw,560px)] gap-1.5 rounded-3xl p-4"
                : "w-full max-w-3xl gap-2 rounded-[1.6rem] p-5"
          )}
        >
          {/* Blur layer isolated on its own compositor layer — never repaints during slider drag */}
          <div
            aria-hidden="true"
            className={cn(
              "pointer-events-none absolute inset-0 border border-[#1a3a5c]/60 bg-[#04101e]/[0.82] shadow-[0_8px_40px_rgba(0,0,0,0.55),0_2px_12px_rgba(0,0,0,0.35),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md",
              isDesktopLayout ? "rounded-2xl" : isTabletTouchLayout ? "rounded-3xl" : "rounded-[1.6rem]"
            )}
            style={{ willChange: "transform" }}
          />
          {/* Content sits above the blur layer */}
          <div className={cn("relative z-10", isDesktopLayout ? "hidden" : "block")}>
            {(modelLabel || variableLabel) ? (
              <div className={cn("flex items-center gap-2 px-1", isTabletTouchLayout ? "mb-2" : "mb-2.5")}>
                {modelLabel ? (
                  <span className="shrink-0 rounded-full border border-cyan-200/18 bg-cyan-300/[0.08] px-2.5 py-1 text-[11px] font-semibold text-cyan-50/92">
                    {modelLabel}
                  </span>
                ) : null}
                {variableLabel ? (
                  <span className="min-w-0 truncate rounded-full border border-white/10 bg-white/[0.06] px-2.5 py-1 text-[11px] font-semibold text-white/84">
                    {variableLabel}
                  </span>
                ) : null}
              </div>
            ) : null}
            <div className={cn("flex items-start justify-between gap-2 px-1", isTabletTouchLayout ? "mb-1.5" : "mb-2")}>
              <div className="min-w-0">
                {validTime ? (
                  <div className="truncate text-xs font-semibold text-white">{validTime.primary}</div>
                ) : (
                  <div className="text-[10px] text-white/50">
                    {timeAxisMode === "observed" ? "Observed time unavailable" : "Valid time unavailable"}
                  </div>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {sourceStatusLabel ? (
                  <div
                    className={cn(
                      "rounded-md border px-2 py-1 font-['IBM_Plex_Mono',monospace] text-[9px] font-medium uppercase tracking-[0.2em]",
                      statusBadgeClass(sourceStatusTone)
                    )}
                  >
                    {sourceStatusLabel}
                  </div>
                ) : null}
                {transientStatus ? (
                  <div className="flex items-center gap-1 rounded-md border border-amber-300/25 bg-amber-300/[0.08] px-2 py-1 text-[9px] text-amber-100">
                    <AlertCircle className="h-3 w-3" />
                    {transientStatus}
                  </div>
                ) : null}
              </div>
            </div>

            <div className={cn("flex items-center", isTabletTouchLayout ? "gap-2.5" : "gap-3")}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => setIsPlaying(!isPlaying)}
                    disabled={disabled || !hasFrames || playDisabled}
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

              <div className="min-w-0 flex-1">
                <Slider
                  value={[sliderIndex]}
                  onValueChange={([value]) => {
                    const next = availableFrames[Math.round(value ?? 0)];
                    if (Number.isFinite(next)) {
                      if (!isScrubbing) {
                        setIsScrubbing(true);
                      }
                      setPreviewHour(next);
                      emitForecastHour(next, false);
                    }
                  }}
                  onValueCommit={([value]) => {
                    const next = availableFrames[Math.round(value ?? 0)];
                    if (Number.isFinite(next)) {
                      setPreviewHour(null);
                      setIsScrubbing(false);
                      emitForecastHour(next, true);
                    }
                  }}
                  min={0}
                  max={Math.max(0, availableFrames.length - 1)}
                  step={1}
                  disabled={disabled || isPlaying || !hasFrames}
                  className="w-full transition-opacity duration-150 [&>*:first-child]:h-1.5 [&>*:first-child]:bg-white/[0.12] [&>*:first-child>*:first-child]:bg-gradient-to-r [&>*:first-child>*:first-child]:from-cyan-400 [&>*:first-child>*:first-child]:via-sky-300 [&>*:first-child>*:first-child]:to-slate-200"
                />
                {validTime ? (
                  <div className="pt-1 text-right font-['IBM_Plex_Mono',monospace] text-[9px] font-medium uppercase tracking-[0.2em] text-white/50">
                    {validTime.secondary}
                  </div>
                ) : null}
              </div>
            </div>
          </div>

            <div className={isDesktopLayout ? "relative z-10 flex items-center gap-3" : "hidden"}>
              <div className="flex shrink-0 items-center gap-2">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() => setIsPlaying(!isPlaying)}
                      disabled={disabled || !hasFrames || playDisabled}
                      aria-label={isPlaying ? "Pause animation" : "Play animation"}
                      className={cn(
                        "flex h-8 w-8 items-center justify-center rounded-xl border transition-all duration-150 disabled:opacity-50",
                        isPlaying
                          ? "bg-cyan-300/10 text-cyan-200 border-white/12 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]"
                          : "bg-white/[0.06] text-white/70 border-white/12 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] hover:text-white hover:bg-white/[0.1]"
                      )}
                    >
                      {isPlaying ? (
                        <Pause className="h-4 w-4" />
                      ) : (
                        <Play className="h-4 w-4 translate-x-[1px]" />
                      )}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="bg-[#07111f] border-white/10 text-white">
                    {isPlaying ? "Pause" : "Play"} animation
                  </TooltipContent>
                </Tooltip>
              </div>

              <div className="flex flex-1 flex-col gap-1">
                <div className="flex items-center justify-between px-0.5">
                  <span className="flex items-center gap-1.5 text-[9px] font-semibold uppercase tracking-[0.26em] text-white/40">
                    <Clock className="h-2.5 w-2.5" />
                    {validTime?.axisLabel ?? (timeAxisMode === "observed" ? "Observed Time" : timeAxisMode === "valid" ? "Valid Day" : "Forecast Hour")}
                  </span>
                  <span className="font-['IBM_Plex_Mono',monospace] text-[10px] font-medium tracking-[0.1em] text-white/80 transition-all duration-150">
                    {validTime?.compactValue ?? (timeAxisMode === "observed" ? "--" : timeAxisMode === "valid" ? validDayLabel(forecastHour) : `${forecastHour}h`)}
                  </span>
                </div>
                <div className="px-0.5">
                  <Slider
                    value={[sliderIndex]}
                    onValueChange={([value]) => {
                      const next = availableFrames[Math.round(value ?? 0)];
                      if (Number.isFinite(next)) {
                        if (!isScrubbing) {
                          setIsScrubbing(true);
                        }
                        setPreviewHour(next);
                        emitForecastHour(next, false);
                      }
                    }}
                    onValueCommit={([value]) => {
                      const next = availableFrames[Math.round(value ?? 0)];
                      if (Number.isFinite(next)) {
                        setPreviewHour(null);
                        setIsScrubbing(false);
                        emitForecastHour(next, true);
                      }
                    }}
                    min={0}
                    max={Math.max(0, availableFrames.length - 1)}
                    step={1}
                    disabled={disabled || isPlaying || !hasFrames}
                    className="w-full transition-opacity duration-150 [&>*:first-child]:h-1 [&>*:first-child]:bg-white/[0.12] [&>*:first-child>*:first-child]:bg-gradient-to-r [&>*:first-child>*:first-child]:from-cyan-400 [&>*:first-child>*:first-child]:via-sky-300 [&>*:first-child>*:first-child]:to-slate-200"
                  />
                </div>
              </div>

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
                    <span className="text-[10px] font-medium text-cyan-200/80 transition-all duration-200">
                      {validTime.secondary}
                    </span>
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
