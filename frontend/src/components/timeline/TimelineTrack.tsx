/**
 * Valid-time timeline track (map viewer redesign Phase 5, Task 3).
 *
 * Horizontal position maps linearly to VALID TIME, not to frame index, so a
 * cadence change (GFS 3-hourly → 6-hourly at FH 240) visibly widens the
 * spacing instead of lying about it. The forecast track has exactly two
 * states: solid through `ready_through_fh` and hatched from there to the
 * authoritative `expected_max_fh`. Purpose-built rather than Radix so the
 * thumb meets the §2.1 touch floors natively (closes the Phase 4 exception).
 */
import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

import type { TimeAxisMode } from "@/lib/time-axis";
import type { ViewerLayoutMode } from "@/lib/viewer-layout";
import { cn } from "@/lib/utils";
import {
  buildTimelineViewModel,
  validTimeMsForHour,
  type TimelineViewModel,
} from "@/lib/timeline-availability";

export type TimelineDensity = "standard" | "compact";

export const TIMELINE_DENSITY_STORAGE_KEY = "twf.timeline.density";
const TIMELINE_HEIGHT_PX: Record<TimelineDensity, number> = { standard: 100, compact: 74 };
const MOBILE_TIMELINE_HEIGHT_PX: Record<TimelineDensity, number> = { standard: 88, compact: 64 };
const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;
const SNAP_BACK_NOTE_MS = 2200;

type TimelineTrackProps = {
  mode: TimeAxisMode;
  frames: number[];
  /** Frame hours already cached locally (brightens their markers). */
  bufferedFrameHours?: number[];
  validTimeByHour?: Record<number, string>;
  runDateTimeISO: string | null;
  forecastHour: number;
  readyThroughFh?: number | null;
  expectedMaxFh?: number | null;
  validPeriodLabel?: string | null;
  issuedLabel?: string | null;
  /** Static-snapshot selections (CPC, hazards) speak their own sentence. */
  availabilityOverride?: string | null;
  layoutMode: ViewerLayoutMode;
  /**
   * Phase 8 (§8 State A): the mobile 64 px one-row timeline. Locks the
   * existing compact density (a user-toggled 88 px density would break the
   * constant map inset), retires the density toggle, and moves the
   * availability sentence to the accessibility layer — the sheet peek owns
   * run state on mobile now. The track MODEL is untouched.
   */
  singleRow?: boolean;
  /** Desktop-only transport groups rendered inside the 40 px rail row. */
  desktopTransportStart?: ReactNode;
  desktopTransportEnd?: ReactNode;
  disabled?: boolean;
  onScrubLive: (fh: number) => void;
  onScrubCommit: (fh: number) => void;
  onScrubStateChange?: (isScrubbing: boolean) => void;
};

function readStoredDensity(): TimelineDensity | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const stored = window.localStorage.getItem(TIMELINE_DENSITY_STORAGE_KEY);
    return stored === "standard" || stored === "compact" ? stored : null;
  } catch {
    return null;
  }
}

function formatTickLabel(ms: number, stepHours: number): string {
  const date = new Date(ms);
  if (stepHours >= 24 || date.getUTCHours() === 0) {
    return "00Z";
  }
  return `${String(date.getUTCHours()).padStart(2, "0")}Z`;
}

function formatDayLabel(ms: number, pixelsPerDay: number, index: number): string {
  const date = new Date(ms);
  if (pixelsPerDay >= 80) {
    const weekday = new Intl.DateTimeFormat("en-US", { weekday: "short", timeZone: "UTC" }).format(date);
    return `${weekday} ${date.getUTCMonth() + 1}/${date.getUTCDate()}`;
  }
  if (pixelsPerDay >= 48) {
    const weekday = new Intl.DateTimeFormat("en-US", { weekday: "short", timeZone: "UTC" }).format(date);
    return `${weekday} ${date.getUTCDate()}`;
  }
  if (pixelsPerDay >= 24) {
    return String(date.getUTCDate());
  }
  return index % 2 === 0 ? String(date.getUTCDate()) : "";
}

function formatValueText(fh: number, ms: number | null, mode: TimeAxisMode): string {
  if (ms === null) {
    return mode === "forecast" ? `FH ${fh}` : `Frame ${fh}`;
  }
  const date = new Date(ms);
  const day = new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(date);
  const time = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(date);
  return `FH ${fh}, ${day}, ${time}`;
}

function nearestMarkerFh(model: TimelineViewModel, targetMs: number): number {
  let best = model.markers[0];
  let bestDistance = Math.abs(best.ms - targetMs);
  for (const marker of model.markers) {
    const distance = Math.abs(marker.ms - targetMs);
    if (distance < bestDistance) {
      best = marker;
      bestDistance = distance;
    }
  }
  return best.fh;
}

export const TimelineTrack = memo(function TimelineTrack({
  mode,
  frames,
  validTimeByHour,
  runDateTimeISO,
  forecastHour,
  readyThroughFh,
  expectedMaxFh,
  validPeriodLabel,
  issuedLabel,
  availabilityOverride = null,
  layoutMode,
  singleRow = false,
  desktopTransportStart,
  desktopTransportEnd,
  disabled = false,
  onScrubLive,
  onScrubCommit,
  onScrubStateChange,
}: TimelineTrackProps) {
  const isMobileLayout = layoutMode === "mobile";
  const hasDesktopTransport = !isMobileLayout && (desktopTransportStart !== undefined || desktopTransportEnd !== undefined);
  const [density, setDensity] = useState<TimelineDensity>(
    () => readStoredDensity() ?? (isMobileLayout ? "compact" : "standard"),
  );
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [trackWidth, setTrackWidth] = useState(0);
  const [ghostFh, setGhostFh] = useState<number | null>(null);
  const [snapBackNote, setSnapBackNote] = useState(false);
  const snapBackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draggingRef = useRef(false);
  const availabilityId = "timeline-availability-line";

  const model = useMemo(
    () => buildTimelineViewModel({
      mode,
      frames,
      validTimeByHour,
      runDateTimeISO,
      readyThroughFh,
      expectedMaxFh,
      validPeriodLabel,
      issuedLabel,
    }),
    [mode, frames, validTimeByHour, runDateTimeISO, readyThroughFh, expectedMaxFh, validPeriodLabel, issuedLabel],
  );

  // Callback ref, not a mount effect: the component returns null until the
  // run data loads, so trackRef is unset on mount and a []-dep effect would
  // never observe the element — leaving trackWidth at 0 and the tick row
  // permanently empty (caught by the Phase 5 verification round).
  const trackResizeObserverRef = useRef<ResizeObserver | null>(null);
  const attachTrackRef = useCallback((element: HTMLDivElement | null) => {
    trackRef.current = element;
    trackResizeObserverRef.current?.disconnect();
    trackResizeObserverRef.current = null;
    if (!element || typeof ResizeObserver === "undefined") {
      return;
    }
    setTrackWidth(element.getBoundingClientRect().width);
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      setTrackWidth(width);
    });
    observer.observe(element);
    trackResizeObserverRef.current = observer;
  }, []);

  useEffect(() => {
    return () => {
      if (snapBackTimerRef.current !== null) {
        clearTimeout(snapBackTimerRef.current);
      }
    };
  }, []);

  const handleDensityToggle = useCallback(() => {
    setDensity((current) => {
      const next: TimelineDensity = current === "standard" ? "compact" : "standard";
      try {
        window.localStorage.setItem(TIMELINE_DENSITY_STORAGE_KEY, next);
      } catch {
        // Private-mode storage failures must not break the toggle.
      }
      return next;
    });
  }, []);

  const effectiveDensity: TimelineDensity = singleRow ? "compact" : density;
  const height = (isMobileLayout ? MOBILE_TIMELINE_HEIGHT_PX : TIMELINE_HEIGHT_PX)[effectiveDensity];

  // Frames the user may actually land on. `ready_through_fh: null` means
  // nothing is ready yet — the committed selection still has to resolve
  // somewhere, and policy is the earliest published frame.
  const committable = useMemo(() => {
    if (!model) {
      return [] as number[];
    }
    if (model.committableFhs.length > 0) {
      return model.committableFhs;
    }
    return model.markers.length > 0 ? [model.markers[0].fh] : [];
  }, [model]);

  const fractionForMs = useCallback((ms: number): number => {
    if (!model) {
      return 0;
    }
    const span = model.domainEndMs - model.domainStartMs;
    if (span <= 0) {
      return 0;
    }
    return Math.max(0, Math.min(1, (ms - model.domainStartMs) / span));
  }, [model]);

  const fractionForFh = useCallback((fh: number): number => {
    const ms = validTimeMsForHour(fh, validTimeByHour, runDateTimeISO);
    return ms === null ? 0 : fractionForMs(ms);
  }, [fractionForMs, runDateTimeISO, validTimeByHour]);

  const clampToCommittable = useCallback((fh: number): number => {
    if (committable.length === 0) {
      return fh;
    }
    if (fh <= committable[0]) {
      return committable[0];
    }
    const last = committable[committable.length - 1];
    return fh >= last ? last : fh;
  }, [committable]);

  const daySegments = useMemo(() => {
    if (!model || trackWidth <= 0) {
      return [] as Array<{ startMs: number; endMs: number; label: string }>;
    }
    const spanMs = model.domainEndMs - model.domainStartMs;
    if (spanMs <= 0) {
      return [];
    }
    const pixelsPerDay = trackWidth / (spanMs / DAY_MS);
    const out: Array<{ startMs: number; endMs: number; label: string }> = [];
    let cursor = model.domainStartMs;
    let index = 0;
    while (cursor < model.domainEndMs) {
      const date = new Date(cursor);
      const nextMidnight = Date.UTC(
        date.getUTCFullYear(),
        date.getUTCMonth(),
        date.getUTCDate() + 1,
      );
      const endMs = Math.min(model.domainEndMs, Math.max(cursor + 1, nextMidnight));
      const segmentWidth = ((endMs - cursor) / spanMs) * trackWidth;
      out.push({
        startMs: cursor,
        endMs,
        label: formatDayLabel(cursor, Math.min(pixelsPerDay, segmentWidth), index),
      });
      cursor = endMs;
      index += 1;
    }
    return out;
  }, [model, trackWidth]);

  const ticks = useMemo(() => {
    if (!model || trackWidth <= 0) {
      return [] as Array<{ ms: number; label: string; major: boolean }>;
    }
    const spanMs = model.domainEndMs - model.domainStartMs;
    const spanHours = spanMs / HOUR_MS;
    if (spanHours <= 0) {
      return [];
    }

    const pixelsPerDay = trackWidth / (spanMs / DAY_MS);
    const stepHours = spanHours <= 6
      ? 1
      : spanHours <= 18
        ? 3
        : pixelsPerDay >= 48
          ? 6
          : pixelsPerDay >= 24
            ? 12
            : 24;
    const stepMs = stepHours * HOUR_MS;
    const firstTick = Math.ceil(model.domainStartMs / stepMs) * stepMs;
    const out: Array<{ ms: number; label: string; major: boolean }> = [];
    for (let ms = firstTick; ms <= model.domainEndMs; ms += stepMs) {
      const hour = new Date(ms).getUTCHours();
      const major = hour === 0 || hour === 12;
      out.push({
        ms,
        label: major ? formatTickLabel(ms, stepHours) : "",
        major,
      });
    }
    return out;
  }, [model, trackWidth]);

  const resolveFhFromClientX = useCallback((clientX: number): number | null => {
    const element = trackRef.current;
    if (!element || !model) {
      return null;
    }
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0) {
      return null;
    }
    const fraction = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const targetMs = model.domainStartMs + fraction * (model.domainEndMs - model.domainStartMs);
    return nearestMarkerFh(model, targetMs);
  }, [model]);

  const endDrag = useCallback((committedFh: number, wasBeyondBoundary: boolean) => {
    draggingRef.current = false;
    setGhostFh(null);
    onScrubStateChange?.(false);
    onScrubCommit(committedFh);
    if (wasBeyondBoundary) {
      setSnapBackNote(true);
      if (snapBackTimerRef.current !== null) {
        clearTimeout(snapBackTimerRef.current);
      }
      snapBackTimerRef.current = setTimeout(() => setSnapBackNote(false), SNAP_BACK_NOTE_MS);
    }
  }, [onScrubCommit, onScrubStateChange]);

  const handlePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (disabled || !model || event.button !== 0) {
      return;
    }
    const element = trackRef.current;
    if (!element) {
      return;
    }
    element.setPointerCapture(event.pointerId);
    draggingRef.current = true;
    setSnapBackNote(false);
    onScrubStateChange?.(true);
    const raw = resolveFhFromClientX(event.clientX);
    if (raw !== null) {
      const clamped = clampToCommittable(raw);
      setGhostFh(raw > clamped ? raw : null);
      onScrubLive(clamped);
    }
  }, [clampToCommittable, disabled, model, onScrubLive, onScrubStateChange, resolveFhFromClientX]);

  const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) {
      return;
    }
    const raw = resolveFhFromClientX(event.clientX);
    if (raw === null) {
      return;
    }
    const clamped = clampToCommittable(raw);
    setGhostFh(raw > clamped ? raw : null);
    onScrubLive(clamped);
  }, [clampToCommittable, onScrubLive, resolveFhFromClientX]);

  const handlePointerUp = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) {
      return;
    }
    const element = trackRef.current;
    if (element?.hasPointerCapture(event.pointerId)) {
      element.releasePointerCapture(event.pointerId);
    }
    const raw = resolveFhFromClientX(event.clientX);
    const clamped = raw === null ? forecastHour : clampToCommittable(raw);
    endDrag(clamped, raw !== null && raw > clamped);
  }, [clampToCommittable, endDrag, forecastHour, resolveFhFromClientX]);

  if (!model) {
    return null;
  }

  const committedFh = clampToCommittable(forecastHour);
  const committedMs = validTimeMsForHour(committedFh, validTimeByHour, runDateTimeISO);
  const committedFraction = fractionForFh(committedFh);
  const ghostFraction = ghostFh === null ? 0 : fractionForFh(ghostFh);
  const pct = (value: number) => `${value * 100}%`;

  const renderTrack = (docked: boolean) => (
    <div
      ref={attachTrackRef}
      data-testid="timeline-track"
      className={cn(
        "relative w-full touch-none select-none",
        docked
          ? effectiveDensity === "standard" ? "h-6" : "h-[34px]"
          : effectiveDensity === "standard" ? "h-6" : "h-5",
        disabled ? "cursor-default opacity-60" : "cursor-pointer",
      )}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    >
      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2",
          docked
            ? "h-[18px] rounded-md border border-white/[0.09] bg-white/[0.045] shadow-[inset_0_1px_2px_rgba(0,0,0,0.35)]"
            : "h-1.5 rounded-full bg-white/[0.08]",
        )}
      />
      {model.solid ? (
        <div
          data-testid="timeline-solid"
          aria-hidden="true"
          className={cn(
            "pointer-events-none absolute top-1/2 -translate-y-1/2 bg-gradient-to-r from-cyan-500/70 via-cyan-400/55 to-sky-300/45",
            docked ? "h-[18px] rounded-l-md" : "h-1.5 rounded-full",
          )}
          style={{
            left: pct(fractionForMs(model.solid.fromMs)),
            width: pct(fractionForMs(model.solid.toMs) - fractionForMs(model.solid.fromMs)),
          }}
        />
      ) : null}
      {model.hatch ? (
        <div
          data-testid="timeline-hatch"
          aria-hidden="true"
          className={cn(
            "pointer-events-none absolute top-1/2 -translate-y-1/2 bg-slate-500/[0.12]",
            docked ? "h-[18px] rounded-r-md" : "h-1.5 rounded-full",
          )}
          style={{
            left: pct(fractionForMs(model.hatch.fromMs)),
            width: pct(fractionForMs(model.hatch.toMs) - fractionForMs(model.hatch.fromMs)),
            backgroundImage:
              "repeating-linear-gradient(135deg, rgba(148,163,184,0.24) 0px, rgba(148,163,184,0.24) 3px, rgba(15,23,42,0.28) 3px, rgba(15,23,42,0.28) 7px)",
          }}
        />
      ) : null}
      {model.markers.map((marker) => (
        <span
          key={marker.fh}
          data-testid="timeline-marker"
          data-fh={marker.fh}
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 h-px w-px -translate-x-1/2 -translate-y-1/2 opacity-0"
          style={{ left: pct(fractionForMs(marker.ms)) }}
        />
      ))}
      {ghostFh !== null ? (
        <div
          data-testid="timeline-ghost"
          aria-hidden="true"
          tabIndex={-1}
          className="pointer-events-none absolute top-1/2 h-6 w-6 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-amber-300/80 bg-amber-300/20"
          style={{ left: pct(ghostFraction) }}
        />
      ) : null}
      <div
        data-testid="timeline-thumb"
        role="slider"
        tabIndex={0}
        aria-label={model.sliderLabel}
        aria-describedby={availabilityId}
        aria-valuemin={committable[0] ?? committedFh}
        aria-valuemax={committable[committable.length - 1] ?? committedFh}
        aria-valuenow={committedFh}
        aria-valuetext={formatValueText(committedFh, committedMs, mode)}
        aria-orientation="horizontal"
        className={cn(
          "absolute top-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full",
          "h-8 w-8 pointer-coarse:h-11 pointer-coarse:w-11",
        )}
        style={{ left: pct(committedFraction) }}
      >
        <span
          aria-hidden="true"
          className={cn(
            "block rounded-full border border-white/80 bg-[linear-gradient(145deg,#ffffff_0%,#dce7ef_52%,#91a9ba_100%)]",
            docked
              ? "h-[15px] w-[15px] shadow-[0_1px_7px_rgba(0,0,0,0.42),0_0_0_2px_rgba(103,232,249,0.13),inset_0_1px_0_rgba(255,255,255,0.9)]"
              : "h-4 w-4 shadow-[0_1px_7px_rgba(0,0,0,0.38),0_0_0_2px_rgba(103,232,249,0.11),inset_0_1px_0_rgba(255,255,255,0.9)]",
          )}
        />
      </div>
    </div>
  );

  const densityToggle = (
    <button
      type="button"
      data-testid="timeline-density-toggle"
      aria-label={density === "standard" ? "Collapse timeline" : "Expand timeline"}
      aria-pressed={density === "compact"}
      title={density === "standard" ? "Collapse timeline" : "Expand timeline"}
      onClick={handleDensityToggle}
      className={cn(
        "flex shrink-0 justify-center text-white/60 transition-colors hover:text-white",
        hasDesktopTransport
          ? "absolute -top-8 right-4 z-20 h-8 w-9 items-end pointer-coarse:-top-11 pointer-coarse:h-11 pointer-coarse:w-11"
          : "h-8 w-8 items-center rounded-lg border border-white/10 bg-[#0a1825]/95 shadow-sm hover:bg-[#102334] pointer-coarse:h-11 pointer-coarse:w-11",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "flex items-center justify-center",
          hasDesktopTransport
            ? "h-5 w-9 rounded-t-lg border border-b-0 border-white/[0.11] bg-[#0a1825]/90 shadow-[0_-2px_9px_rgba(0,0,0,0.28),inset_0_1px_0_rgba(255,255,255,0.05)] backdrop-blur-md hover:bg-[#102334]/95"
            : "h-full w-full",
        )}
      >
        {density === "standard"
          ? <ChevronDown className="h-3.5 w-3.5" />
          : <ChevronUp className="h-3.5 w-3.5" />}
      </span>
    </button>
  );

  if (hasDesktopTransport) {
    const availabilityText = availabilityOverride || model.availabilityLine;
    return (
      <div
        data-testid="timeline-root"
        data-density={density}
        style={{ height }}
        className="relative flex w-full min-w-0 flex-col"
      >
        {densityToggle}
        {density === "standard" ? (
          <>
            <div
              data-testid="timeline-day-band"
              aria-hidden="true"
              className="relative h-5 w-full overflow-hidden border-b border-white/[0.07]"
            >
              {daySegments.map((segment, index) => (
                <span
                  key={`${segment.startMs}-${segment.endMs}`}
                  data-testid="timeline-day-segment"
                  className={cn(
                    "absolute inset-y-0 flex items-center overflow-hidden border-r border-white/[0.07] px-2 font-sans text-[11px] font-medium tracking-[0.01em] text-white/52",
                    index % 2 === 1 && "bg-white/[0.025]",
                  )}
                  style={{
                    left: pct(fractionForMs(segment.startMs)),
                    width: pct(fractionForMs(segment.endMs) - fractionForMs(segment.startMs)),
                  }}
                >
                  {segment.label}
                </span>
              ))}
            </div>
            <div
              data-testid="timeline-tick-row"
              aria-hidden="true"
              className="relative h-4 w-full"
            >
              {ticks.map((tick) => (
                <span
                  key={tick.ms}
                  data-testid="timeline-tick"
                  data-major={tick.major}
                  className={cn(
                    "absolute bottom-0 w-px -translate-x-1/2",
                    tick.major ? "h-[5px] bg-white/40" : "h-[3px] bg-white/20",
                  )}
                  style={{ left: pct(fractionForMs(tick.ms)) }}
                >
                  {tick.label ? (
                    <span className="absolute bottom-[5px] left-1/2 -translate-x-1/2 whitespace-nowrap font-sans text-[11px] font-medium leading-none tracking-[0.015em] text-white/55">
                      {tick.label}
                    </span>
                  ) : null}
                </span>
              ))}
            </div>
          </>
        ) : null}

        {renderTrack(true)}

        <div
          data-testid="timeline-transport"
          className="relative flex h-10 min-w-0 items-center gap-2 border-t border-white/[0.07]"
        >
          {desktopTransportStart}
          <span
            id={availabilityId}
            data-testid="timeline-availability"
            className="sr-only"
          >
            {availabilityText}
          </span>
          {ghostFh !== null ? (
            <span className="shrink-0 text-[11px] font-medium leading-none text-amber-300">
              FH {ghostFh} — not published yet
            </span>
          ) : snapBackNote ? (
            <span className="shrink-0 text-[11px] font-medium leading-none text-white/45">
              Snapped back to the last ready frame
            </span>
          ) : null}
          <div className="min-w-3 flex-1" />
          {desktopTransportEnd}
        </div>
      </div>
    );
  }

  const noteText = ghostFh !== null
    ? `FH ${ghostFh} — not published yet`
    : snapBackNote
      ? "Snapped back to the last ready frame"
      : null;

  return (
    <div
      data-testid="timeline-root"
      data-density={effectiveDensity}
      style={{ height: singleRow ? "100%" : height }}
      className="relative flex w-full min-w-0 items-center gap-2"
    >
      <div className="flex min-w-0 flex-1 flex-col justify-center gap-1">
        {singleRow ? (
          <span id={availabilityId} data-testid="timeline-availability" className="sr-only">
            {availabilityOverride || model.availabilityLine}
          </span>
        ) : (
          <div className="flex min-w-0 items-center gap-2">
            <span
              id={availabilityId}
              data-testid="timeline-availability"
              className="min-w-0 truncate text-[11px] font-medium leading-none text-white/60"
            >
              {availabilityOverride || model.availabilityLine}
            </span>
            {noteText ? (
              <span className={cn(
                "shrink-0 text-[11px] font-medium leading-none",
                ghostFh !== null ? "text-amber-300" : "text-white/45",
              )}>
                {noteText}
              </span>
            ) : null}
          </div>
        )}

        {renderTrack(false)}

        <div
          data-testid="timeline-tick-row"
          aria-hidden="true"
          className={cn("relative w-full", effectiveDensity === "standard" ? "h-4" : "h-3")}
        >
          {/* On the §8 one-row layout the track is ~180 px wide: at a long
              run's cadence every tick label is the same "00Z" and they overlap
              into mush. Marks keep the day rhythm; the actual dates come from
              the State B day strip and the inline readout. */}
          {singleRow
            ? ticks.map((tick) => (
              <span
                key={tick.ms}
                data-testid="timeline-tick"
                data-major={tick.major}
                className={cn(
                  "absolute top-0 w-px -translate-x-1/2",
                  tick.major ? "h-[5px] bg-white/38" : "h-[3px] bg-white/18",
                )}
                style={{ left: pct(fractionForMs(tick.ms)) }}
              />
            ))
            : ticks.filter((tick) => tick.label).map((tick) => (
              <span
                key={tick.ms}
                data-testid="timeline-tick"
                className="absolute top-0 -translate-x-1/2 whitespace-nowrap font-sans text-[11px] font-medium leading-none tracking-[0.015em] text-white/40"
                style={{ left: pct(fractionForMs(tick.ms)) }}
              >
                {tick.label}
              </span>
            ))}
        </div>
      </div>

      {/* §9: nothing in the gesture path may insert a row. The snap-back note
          is an absolute overlay on the single-row layout. */}
      {singleRow ? (
        noteText ? (
          <span className={cn(
            "pointer-events-none absolute left-0 top-0 max-w-full truncate text-[11px] font-medium leading-none",
            ghostFh !== null ? "text-amber-300" : "text-white/45",
          )}>
            {noteText}
          </span>
        ) : null
      ) : densityToggle}
    </div>
  );
});
