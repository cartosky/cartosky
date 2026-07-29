/**
 * Timeline availability view-model (map viewer redesign Phase 5, §5.4).
 *
 * One adapter turns the raw per-selection inputs into everything the
 * timeline track renders: a valid-time domain, the marker positions, the
 * solid/hatched split, and a mode-truthful availability sentence. Each mode
 * speaks its own vocabulary — forecast talks about readiness and the run
 * horizon, observed talks about freshness, valid talks about the period.
 */
import type { TimeAxisMode } from "@/lib/time-axis";

export type TimelineMarker = { fh: number; ms: number };
export type TimelineRegion = { fromMs: number; toMs: number };

export type TimelineViewModel = {
  /** Mode-specific accessible name for the committed thumb. */
  sliderLabel: string;
  availabilityLine: string;
  domainStartMs: number;
  domainEndMs: number;
  markers: TimelineMarker[];
  solid: TimelineRegion | null;
  hatch: TimelineRegion | null;
  /** Highest frame hour the user may commit to; null when nothing is ready. */
  boundaryFh: number | null;
  /** Published frames the user may commit to (ready ∩ published). */
  committableFhs: number[];
  /** True when the manifest carried a readiness boundary for this variable. */
  hasBoundary: boolean;
};

export type TimelineViewModelParams = {
  mode: TimeAxisMode;
  /** Published frame hours, ascending. */
  frames: number[];
  validTimeByHour?: Record<number, string>;
  runDateTimeISO?: string | null;
  /** `undefined` = manifest predates the scalar (legacy); `null` = nothing ready. */
  readyThroughFh?: number | null;
  expectedMaxFh?: number | null;
  /** Valid-mode period label (e.g. "VALID: Aug-Sep-Oct 2026"). */
  validPeriodLabel?: string | null;
  /** Valid-mode issue label. */
  issuedLabel?: string | null;
};

const HOUR_MS = 3_600_000;

/**
 * Valid time for a forecast hour. Published frames carry their own timestamp;
 * the run horizon (`expected_max_fh`) has no frame yet, so it is projected
 * from the run's base time — the only way the hatch can reach a frame that
 * does not exist.
 */
export function validTimeMsForHour(
  fh: number,
  validTimeByHour: Record<number, string> | undefined,
  runDateTimeISO: string | null | undefined,
): number | null {
  const iso = validTimeByHour?.[fh];
  if (iso) {
    const parsed = Date.parse(iso);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  if (runDateTimeISO) {
    const base = Date.parse(runDateTimeISO);
    if (Number.isFinite(base)) {
      return base + fh * HOUR_MS;
    }
  }
  return null;
}

function formatClockTime(ms: number): string {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(ms));
}

function observedLine(markers: TimelineMarker[]): string {
  if (markers.length === 0) {
    return "No observations loaded";
  }
  const latest = markers[markers.length - 1];
  const spanHours = Math.round((latest.ms - markers[0].ms) / HOUR_MS);
  const window = spanHours > 0 ? ` · last ${spanHours} h` : "";
  return `Latest ${formatClockTime(latest.ms)} · ${markers.length} frames${window}`;
}

function forecastLine(params: {
  frames: number[];
  readyThroughFh?: number | null;
  expectedMaxFh?: number | null;
}): string {
  const { frames, readyThroughFh, expectedMaxFh } = params;
  if (frames.length === 0) {
    return "No frames yet";
  }
  const first = frames[0];
  const last = frames[frames.length - 1];
  if (readyThroughFh === undefined) {
    return `${frames.length} frames · FH ${first}–${last}`;
  }
  const horizon = Number.isFinite(expectedMaxFh ?? Number.NaN) ? Number(expectedMaxFh) : null;
  if (readyThroughFh === null) {
    return horizon !== null
      ? `No frames ready yet · building to FH ${horizon}`
      : "No frames ready yet";
  }
  if (horizon !== null && horizon > readyThroughFh) {
    return `Ready through FH ${readyThroughFh} · building to FH ${horizon}`;
  }
  return `Complete · FH ${first}–${readyThroughFh}`;
}

export function buildTimelineViewModel(params: TimelineViewModelParams): TimelineViewModel | null {
  const {
    mode,
    validTimeByHour,
    runDateTimeISO,
    readyThroughFh,
    expectedMaxFh,
    validPeriodLabel,
    issuedLabel,
  } = params;

  const frames = Array.from(new Set(params.frames.filter(Number.isFinite))).sort((a, b) => a - b);
  const markers: TimelineMarker[] = [];
  for (const fh of frames) {
    const ms = validTimeMsForHour(fh, validTimeByHour, runDateTimeISO);
    if (ms !== null) {
      markers.push({ fh, ms });
    }
  }
  if (markers.length === 0) {
    return null;
  }

  // The forecast horizon is authoritative only in forecast mode; observed and
  // valid selections never carry the scalars.
  const isForecast = mode === "forecast";
  const horizonFh = isForecast && Number.isFinite(expectedMaxFh ?? Number.NaN)
    ? Number(expectedMaxFh)
    : null;

  const domainStartMs = markers[0].ms;
  const lastMarkerMs = markers[markers.length - 1].ms;
  const horizonMs = horizonFh !== null
    ? validTimeMsForHour(horizonFh, validTimeByHour, runDateTimeISO)
    : null;
  const domainEndRaw = horizonMs !== null ? Math.max(horizonMs, lastMarkerMs) : lastMarkerMs;
  // A single-frame selection has no span; keep the domain non-degenerate so
  // every downstream fraction stays finite.
  const domainEndMs = domainEndRaw > domainStartMs ? domainEndRaw : domainStartMs + HOUR_MS;

  const hasBoundary = isForecast && readyThroughFh !== undefined;
  const boundaryFh = hasBoundary
    ? (readyThroughFh === null ? null : Number(readyThroughFh))
    : frames[frames.length - 1];
  const committableFhs = boundaryFh === null
    ? []
    : frames.filter((fh) => fh <= boundaryFh);

  const boundaryMs = boundaryFh === null
    ? domainStartMs
    : validTimeMsForHour(boundaryFh, validTimeByHour, runDateTimeISO) ?? lastMarkerMs;

  const solid: TimelineRegion | null = boundaryFh === null
    ? null
    : { fromMs: domainStartMs, toMs: Math.max(domainStartMs, boundaryMs) };

  const hatch: TimelineRegion | null = hasBoundary && horizonMs !== null && horizonMs > boundaryMs
    ? { fromMs: boundaryMs, toMs: horizonMs }
    : null;

  const availabilityLine = mode === "observed"
    ? observedLine(markers)
    : mode === "valid"
      ? [validPeriodLabel, issuedLabel].filter(Boolean).join(" · ") || "Valid period"
      : forecastLine({ frames, readyThroughFh, expectedMaxFh });

  const sliderLabel = mode === "observed"
    ? "Observation time"
    : mode === "valid"
      ? "Valid period"
      : "Forecast time";

  return {
    sliderLabel,
    availabilityLine,
    domainStartMs,
    domainEndMs,
    markers,
    solid,
    hatch,
    boundaryFh,
    committableFhs,
    hasBoundary,
  };
}
