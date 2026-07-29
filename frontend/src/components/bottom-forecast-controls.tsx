import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, GitCompareArrows, MessageSquareText, Pause, Play, Share2, Settings } from "lucide-react";

import type { ViewerLayoutMode } from "@/lib/viewer-layout";
import type { ObservedSourceStatusTone, TimeAxisMode } from "@/lib/time-axis";
import { Button } from "@/components/ui/button";
import { TimelineTrack } from "@/components/timeline/TimelineTrack";
import { cn } from "@/lib/utils";
import { resolveRunBuildProgress } from "@/lib/viewer-loading-status";
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
  /** Frame hours whose data is already cached locally (drives the buffered track). */
  bufferedFrameHours?: number[];
  /**
   * Every PUBLISHED frame hour (unclamped). The timeline renders a marker per
   * published frame and a ghost when the user drags past the ready boundary,
   * so it needs the full list — `availableFrames` is the committable subset.
   */
  publishedFrameHours?: number[];
  onForecastHourChange: (fh: number, reason?: "standard" | "scrub-live" | "scrub-commit") => void;
  onScrubStateChange?: (isScrubbing: boolean) => void;
  isPlaying: boolean;
  setIsPlaying: (value: boolean) => void;
  animationDelayMs: number;
  onSpeedChange: (delayMs: number) => void;
  runDateTimeISO: string | null;
  timeAxisMode?: TimeAxisMode;
  validTimeISO?: string | null;
  cpcValidSeas?: string | null;
  cpcValidStart?: string | null;
  cpcValidEnd?: string | null;
  frameDayLabel?: string | null;
  frameValidTimesByHour?: Record<number, string>;
  sourceStatusLabel?: string | null;
  sourceStatusDescription?: string | null;
  sourceStatusTone?: ObservedSourceStatusTone | null;
  disabled: boolean;
  playDisabled?: boolean;
  transientStatus?: string | null;
  forecastHourFallbackNotice?: string | null;
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
  /** Manifest readiness boundary; `undefined` = pre-Phase-5 manifest. */
  readyThroughFh?: number | null;
  /** Authoritative run horizon (hatch target). */
  expectedMaxFh?: number | null;
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

const CPC_SEASON_CODES: Record<string, string> = {
  DJF: "Dec-Jan-Feb",
  JFM: "Jan-Feb-Mar",
  FMA: "Feb-Mar-Apr",
  MAM: "Mar-Apr-May",
  AMJ: "Apr-May-Jun",
  MJJ: "May-Jun-Jul",
  JJA: "Jun-Jul-Aug",
  JAS: "Jul-Aug-Sep",
  ASO: "Aug-Sep-Oct",
  SON: "Sep-Oct-Nov",
  OND: "Oct-Nov-Dec",
  NDJ: "Nov-Dec-Jan",
};

function expandSeasonalShorthand(code: string): string {
  return CPC_SEASON_CODES[code.trim().toUpperCase()] ?? code;
}

function formatCpcValidSeasDisplay(
  validSeas: string | null | undefined,
  validStart: string | null | undefined,
  validEnd: string | null | undefined,
): string | null {
  const seas = (validSeas ?? "").trim();
  if (seas) {
    const expanded = seas.replace(/^([A-Z]{3,})(\s+\d{4})$/i, (_, codes, year) =>
      expandSeasonalShorthand(codes) + year
    );
    return `VALID: ${expanded}`;
  }

  const start = validStart ? new Date(validStart) : null;
  const end = validEnd ? new Date(validEnd) : null;
  if (!start || !end || Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return null;
  }

  const fmt = (date: Date) =>
    new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" }).format(date);
  const startStr = fmt(start);
  const endStr = fmt(end);
  if (start.getUTCFullYear() === end.getUTCFullYear()) {
    if (start.getUTCMonth() === end.getUTCMonth()) {
      const month = new Intl.DateTimeFormat("en-US", { month: "long", timeZone: "UTC" }).format(start);
      const year = start.getUTCFullYear();
      return `VALID: ${month} ${start.getUTCDate()}-${end.getUTCDate()}, ${year}`;
    }
    const startCompact = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", timeZone: "UTC" }).format(start);
    return `VALID: ${startCompact} - ${endStr}`;
  }

  return `VALID: ${startStr} - ${endStr}`;
}

function formatTimelineDisplay(params: {
  modelId?: string | null;
  runDateISO: string | null;
  forecastHour: number;
  timeAxisMode: TimeAxisMode;
  variableId?: string | null;
  validTimeISO?: string | null;
  frameDayLabel?: string | null;
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
    const secondary = validAxisLabel(
      params.forecastHour,
      params.variableId,
      params.runDateISO,
      params.validTimeISO,
      params.frameDayLabel,
    );
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

export const BottomForecastControls = memo(function BottomForecastControls({
  forecastHour,
  availableFrames,
  bufferedFrameHours,
  publishedFrameHours,
  onForecastHourChange,
  onScrubStateChange,
  isPlaying,
  setIsPlaying,
  animationDelayMs,
  onSpeedChange,
  runDateTimeISO,
  timeAxisMode = "forecast",
  validTimeISO = null,
  cpcValidSeas = null,
  cpcValidStart = null,
  cpcValidEnd = null,
  frameDayLabel = null,
  frameValidTimesByHour,
  sourceStatusLabel = null,
  sourceStatusDescription = null,
  sourceStatusTone = null,
  disabled,
  playDisabled = false,
  transientStatus,
  forecastHourFallbackNotice = null,
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
  readyThroughFh,
  expectedMaxFh = null,
}: BottomForecastControlsProps) {
  const toolbar = useViewerToolbar();
  const onShare = toolbar?.onShare;
  const compareHref = toolbar?.compareHref;
  const onFeedback = toolbar?.onFeedback;
  const onOpenControls = toolbar?.onMobileControlsOpenChange;
  const DRAG_UPDATE_MS = 48;
  const [previewHour, setPreviewHour] = useState<number | null>(null);
  const [isScrubbing, setIsScrubbing] = useState(false);
  const staticSnapshotLabel: string | null = (() => {
    if (modelId === "cpc") {
      return formatCpcValidSeasDisplay(cpcValidSeas, cpcValidStart, cpcValidEnd) ?? "Latest forecast";
    }
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
      frameDayLabel,
      validTimeISO:
        timeAxisMode === "observed"
          ? frameValidTimesByHour?.[previewHour ?? forecastHour] ?? validTimeISO
          : validTimeISO,
    }),
    [modelId, runDateTimeISO, forecastHour, previewHour, timeAxisMode, variableId, frameDayLabel, validTimeISO, frameValidTimesByHour]
  );

  const hasFrames = availableFrames.length > 0;
  const isDesktopLayout = layoutMode === "desktop" || layoutMode === "tablet-touch";
  const isTabletTouchLayout = layoutMode === "tablet-touch";
  const controlsLayerClassName = isDesktopLayout || isTabletTouchLayout ? "z-[70]" : "z-[60]";
  const effectiveHour = previewHour ?? forecastHour;
  const jumpHours = useMemo(
    () => Array.from(new Set(availableFrames.filter(Number.isFinite))).sort((a, b) => a - b),
    [availableFrames],
  );
  const desktopRunLabel = useMemo(() => {
    if (timeAxisMode !== "forecast" || !modelLabel || !runDateTimeISO) {
      return null;
    }
    const runDate = new Date(runDateTimeISO);
    if (Number.isNaN(runDate.getTime())) {
      return null;
    }
    return `${modelLabel} ${String(runDate.getUTCHours()).padStart(2, "0")}Z`;
  }, [modelLabel, runDateTimeISO, timeAxisMode]);
  // Shared with the initial map scrim via resolveRunBuildProgress so both
  // surfaces render identical `Building available/total hrs` values.
  const { freshnessTotal, cappedAvailableForecastHours } = useMemo(
    () => resolveRunBuildProgress(availableFrames, totalForecastHours),
    [availableFrames, totalForecastHours],
  );
  const hasFreshnessTotal = freshnessTotal !== null && freshnessTotal > 0;
  const showFreshnessStrip = !isDesktopLayout && timeAxisMode !== "observed" && hasFreshnessTotal;
  const freshnessProgressPercent = hasFreshnessTotal
    ? Math.max(0, Math.min(100, (cappedAvailableForecastHours / freshnessTotal) * 100))
    : 0;
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

  const desktopTransportStart = isDesktopLayout ? (
    <div className="flex shrink-0 items-center gap-2">
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            data-testid="timeline-play-button"
            onClick={() => setIsPlaying(!isPlaying)}
            disabled={disabled || !hasFrames || playDisabled || staticSnapshotLabel !== null}
            aria-label={isPlaying ? "Pause animation" : "Play animation"}
            className={cn(
              "flex h-8 w-10 items-center justify-center rounded-lg border transition-all duration-150 disabled:opacity-50 pointer-coarse:h-11 pointer-coarse:w-11",
              isPlaying
                ? "border-cyan-200/45 bg-cyan-300/25 text-cyan-100 shadow-[0_0_16px_rgba(34,211,238,0.12)]"
                : "border-cyan-300/55 bg-cyan-400/85 text-[#03111c] shadow-[0_0_18px_rgba(34,211,238,0.18)] hover:bg-cyan-300",
            )}
          >
            {isPlaying ? (
              <Pause className="h-4 w-4" />
            ) : (
              <Play className="h-4 w-4 translate-x-px" />
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="border-white/10 bg-[#07111f] text-white">
          {isPlaying ? "Pause" : "Play"} animation
        </TooltipContent>
      </Tooltip>
      <SpeedButton
        animationDelayMs={animationDelayMs}
        onSpeedChange={onSpeedChange}
        expanded
      />
      {timeAxisMode === "forecast" && staticSnapshotLabel === null ? (
        <label
          data-testid="timeline-jump-control"
          className="flex h-8 shrink-0 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.035] pl-3 pr-2 text-[12px] font-medium text-white/55 transition-colors hover:border-white/18 hover:bg-white/[0.06] pointer-coarse:h-11"
        >
          <span className="whitespace-nowrap">Jump to FH</span>
          <select
            data-testid="timeline-jump-select"
            aria-label="Jump to forecast hour"
            value={jumpHours.includes(effectiveHour) ? String(effectiveHour) : ""}
            onChange={(event) => {
              const next = Number(event.target.value);
              if (!Number.isFinite(next)) return;
              setPreviewHour(null);
              setIsPlaying(false);
              onForecastHourChange(next, "scrub-commit");
            }}
            disabled={disabled || jumpHours.length === 0}
            className="h-8 min-w-[54px] cursor-pointer bg-transparent text-right font-sans text-[12px] font-semibold tabular-nums text-cyan-200 outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/80 focus-visible:ring-offset-1 focus-visible:ring-offset-[#071522] disabled:cursor-not-allowed pointer-coarse:h-11"
          >
            {!jumpHours.includes(effectiveHour) ? <option value="">—</option> : null}
            {jumpHours.map((hour) => (
              <option key={hour} value={hour} className="bg-[#071522] text-white">
                {hour}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {hasFreshnessTotal
        && freshnessTotal !== null
        && timeAxisMode !== "observed"
        && !runIsComplete
        && staticSnapshotLabel === null ? (
          <div
            data-testid="timeline-building-status"
            className="flex shrink-0 items-center gap-1.5 rounded-md border border-amber-300/20 bg-amber-300/[0.07] px-2 py-1 text-[11px] font-medium leading-none text-amber-100/75"
          >
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-amber-300" />
            <span>Building</span>
            <span className="tabular-nums text-white/55">
              {cappedAvailableForecastHours}/{freshnessTotal} hrs
            </span>
          </div>
        ) : null}
    </div>
  ) : undefined;

  const desktopTransportEnd = isDesktopLayout ? (
    <div className="flex shrink-0 items-center gap-2">
      {sourceStatusLabel ? (
        <div
          data-tour-target="freshness-indicator"
          title={sourceStatusDescription ?? sourceStatusLabel}
          className={cn(
            "flex h-8 shrink-0 items-center rounded-lg border px-2.5 font-['IBM_Plex_Mono',monospace] text-[11px] font-medium uppercase tracking-[0.14em]",
            statusBadgeClass(sourceStatusTone),
          )}
        >
          {sourceStatusLabel}
        </div>
      ) : runIncompleteLabel ? (
        <div
          data-tour-target="freshness-indicator"
          title={runIncompleteDescription ?? runIncompleteLabel}
          className={cn(
            "flex h-8 shrink-0 items-center rounded-lg border px-2.5 font-['IBM_Plex_Mono',monospace] text-[11px] font-medium uppercase tracking-[0.14em]",
            statusBadgeClass(runIncompleteTone),
          )}
        >
          {runIncompleteLabel}
        </div>
      ) : null}
      {transientStatus ? (
        <div className="flex items-center gap-1.5 rounded-md border border-amber-300/25 bg-amber-300/[0.08] px-2 py-1 text-[11px] text-amber-100">
          <AlertCircle className="h-3 w-3" />
          {transientStatus}
        </div>
      ) : null}
      <div className="h-5 w-px bg-white/[0.09]" />
      <div className="flex min-w-[220px] flex-col items-end leading-none">
        {validTime ? (
          <>
            <span className="text-[12px] font-semibold tracking-[-0.01em] text-white/95">
              {validTime.primary}
            </span>
            {validTime.secondary ? (
              <span
                data-testid={desktopRunLabel ? "timeline-run-readout" : undefined}
                className="mt-1 flex items-center gap-1.5 text-[11px] font-medium"
              >
                {desktopRunLabel ? (
                  <>
                    <span className="text-cyan-200/85">{desktopRunLabel}</span>
                    <span aria-hidden="true" className="text-white/25">·</span>
                  </>
                ) : null}
                <span className="text-white/62">{validTime.secondary}</span>
              </span>
            ) : null}
          </>
        ) : (
          <span className="text-[11px] text-white/50">
            {timeAxisMode === "observed" ? "Observed time unavailable" : "Valid time unavailable"}
          </span>
        )}
      </div>
    </div>
  ) : undefined;

  // One purpose-built valid-time track replaces both Radix timeline sliders.
  // Exactly one instance is mounted (the hidden branch would otherwise
  // duplicate the single-focusable-thumb contract).
  const timeline = (
    <TimelineTrack
      mode={timeAxisMode}
      frames={publishedFrameHours && publishedFrameHours.length > 0 ? publishedFrameHours : availableFrames}
      bufferedFrameHours={bufferedFrameHours}
      validTimeByHour={frameValidTimesByHour}
      runDateTimeISO={runDateTimeISO}
      forecastHour={effectiveHour}
      readyThroughFh={readyThroughFh}
      expectedMaxFh={expectedMaxFh}
      validPeriodLabel={staticSnapshotLabel}
      issuedLabel={modelId === "cpc" ? formatCpcIssuedDisplay(runDateTimeISO) : null}
      availabilityOverride={
        staticSnapshotLabel
          ? [staticSnapshotLabel, modelId === "cpc" ? formatCpcIssuedDisplay(runDateTimeISO) : null]
            .filter(Boolean)
            .join(" · ")
          : null
      }
      layoutMode={layoutMode}
      desktopTransportStart={desktopTransportStart}
      desktopTransportEnd={desktopTransportEnd}
      disabled={disabled || isPlaying || !hasFrames || staticSnapshotLabel !== null}
      onScrubLive={(next) => {
        setPreviewHour(next);
        emitForecastHour(next, false);
      }}
      onScrubCommit={(next) => {
        setPreviewHour(null);
        emitForecastHour(next, true);
      }}
      onScrubStateChange={setIsScrubbing}
    />
  );

  return (
    <TooltipProvider delayDuration={300}>
      <div
        className={cn(
          "pointer-events-none fixed inset-x-0 bottom-0 flex max-w-[100vw] flex-col items-center justify-end",
          isDesktopLayout ? "overflow-visible px-0 pb-0" : "overflow-x-hidden px-2 pb-3 sm:px-4 sm:pb-5",
          controlsLayerClassName,
        )}
        // Phase 6: center within the map area, not the viewport. The variable
        // is unset outside the viewer, where this resolves to today's 0.
        style={{ left: "var(--viewer-rail-width, 0px)" }}
      >
        {forecastHourFallbackNotice ? (
          <div
            data-testid="forecast-hour-fallback-notice"
            className="pointer-events-none mb-2 flex max-w-[min(92vw,36rem)] items-center gap-2 rounded-md border border-amber-300/40 bg-slate-950/85 px-3 py-2 text-xs text-amber-100 shadow-lg backdrop-blur-md"
          >
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />
            <span>{forecastHourFallbackNotice}</span>
          </div>
        ) : null}
        <div
          data-testid={isDesktopLayout ? "timeline-panel" : undefined}
          data-tour-target={isDesktopLayout ? "forecast-scrubber" : undefined}
          className={cn(
            "pointer-events-auto relative flex w-full max-w-full flex-col",
            isDesktopLayout
              ? "w-full max-w-none overflow-visible px-4 py-0"
              : isTabletTouchLayout
                ? "w-[min(90vw,560px)] gap-1.5 overflow-x-hidden rounded-3xl p-4"
                : "w-full max-w-3xl gap-2 overflow-x-hidden rounded-[1.6rem] p-5"
          )}
        >
          {/* Blur layer isolated on its own compositor layer — never repaints during slider drag */}
          <div
            aria-hidden="true"
            className={cn(
              "viewer-mobile-control-surface pointer-events-none absolute inset-0",
              isDesktopLayout ? "border-x-0 border-b-0" : isTabletTouchLayout ? "rounded-3xl" : "rounded-[1.6rem]"
            )}
            style={{ willChange: "transform" }}
          />
          {/* Content sits above the blur layer. Rendered conditionally, not
              CSS-hidden: a display:none duplicate of the transport controls
              leaves two elements sharing every aria-label. */}
          {!isDesktopLayout ? (
          <div className="relative z-10">
            {/* Row 1: context (model/variable) + action buttons */}
            <div className={cn("flex items-center justify-between gap-2 px-1", isTabletTouchLayout ? "mb-1.5" : "mb-2")}>
              <div className="min-w-0 flex-1">
                {(modelLabel || variableLabel) ? (
                    <div>
                      <div className="flex items-center gap-1.5">
                        {runDateTimeISO ? (
                          <span className="shrink-0 font-['IBM_Plex_Mono',monospace] text-[12px] font-semibold uppercase tracking-[0.18em] text-cyan-300/55">
                            {`${new Date(runDateTimeISO).getUTCHours()}z`}
                          </span>
                        ) : null}
                        {runDateTimeISO && modelLabel ? (
                          <span className="text-[12px] text-cyan-300/30">·</span>
                        ) : null}
                        {modelLabel ? (
                          <span className="shrink-0 font-['IBM_Plex_Mono',monospace] text-[12px] font-semibold uppercase tracking-[0.18em] text-cyan-300/80">
                            {modelLabel}
                          </span>
                        ) : null}
                      </div>
                      {variableLabel ? (
                        <span className="block min-w-0 truncate text-[11px] font-medium text-cyan-200/70 mt-0.5">
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
                      "rounded-md border px-2 py-1 font-['IBM_Plex_Mono',monospace] text-[12px] font-medium uppercase tracking-[0.2em]",
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
                      "rounded-md border px-2 py-1 font-['IBM_Plex_Mono',monospace] text-[12px] font-medium uppercase tracking-[0.2em]",
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
                    className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-white/10 bg-white/[0.05] text-white/60 transition-colors hover:bg-white/[0.09] hover:text-white"
                  >
                    <Share2 className="h-3.5 w-3.5" />
                  </button>
                ) : null}
                {compareHref ? (
                  <Link
                    to={compareHref}
                    aria-label="Compare"
                    className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-white/10 bg-white/[0.05] text-white/60 transition-colors hover:bg-white/[0.09] hover:text-white"
                  >
                    <GitCompareArrows className="h-3.5 w-3.5" />
                  </Link>
                ) : null}
                {onFeedback ? (
                  <button
                    type="button"
                    onClick={onFeedback}
                    aria-label="Send feedback"
                    data-tour-target="feedback-button"
                    className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-white/10 bg-white/[0.05] text-white/60 transition-colors hover:bg-white/[0.09] hover:text-white"
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
                    className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-white/10 bg-white/[0.05] text-white/60 transition-colors hover:bg-white/[0.09] hover:text-white"
                  >
                    <Settings className="h-3.5 w-3.5" />
                  </button>
                ) : null}
              </div>
            </div>

            {transientStatus ? (
              <div className="mb-2 flex items-center gap-1 rounded-md border border-amber-300/25 bg-amber-300/[0.08] px-2 py-1 text-[12px] text-amber-100">
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
                      "h-11 w-11 rounded-xl",
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

              <SpeedButton animationDelayMs={animationDelayMs} onSpeedChange={onSpeedChange} touch />

              <div className="min-w-0 flex-1">
                {!isDesktopLayout ? timeline : null}
                {validTime ? (
                  <div className="-mt-0.5 text-right font-['IBM_Plex_Mono',monospace] text-[12px] font-medium tracking-[0.06em] text-white/50">
                    {validTime.shortDate}
                    {showInlineSecondary && validTime.secondary && validTime.secondary !== validTime.shortDate ? (
                      <span className="ml-1.5 text-white/32">· {validTime.secondary}</span>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>

            {showFreshnessStrip && freshnessTotal !== null ? (
              <>
                <div className="mt-2 border-t border-white/[0.08]" />
                <div className="flex items-center gap-2 px-0.5 pt-2 font-['IBM_Plex_Mono',monospace] text-[12px] font-medium text-white/55">
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
          ) : null}

          {isDesktopLayout ? (
            <div className="relative z-10 w-full">
              {timeline}
            </div>
          ) : null}

        </div>
      </div>
    </TooltipProvider>
  );
});
