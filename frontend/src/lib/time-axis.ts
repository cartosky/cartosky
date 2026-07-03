export type TimeAxisMode = "forecast" | "observed" | "valid";
export type ObservedSourceStatusTone = "live" | "delayed" | "stale" | "unavailable";

const OBSERVED_SOURCE_THRESHOLDS: Record<string, { delayed: number; stale: number }> = {
  mrms: { delayed: 8, stale: 15 },
  current_analysis: { delayed: 45, stale: 60 },
  "goes-east": { delayed: 30, stale: 45 },
};

export type ObservedSourceStatus = {
  tone: ObservedSourceStatusTone;
  label: string;
  description: string;
  ageMinutes: number | null;
};

type ObservedAvailabilityInput = {
  freshness_state?: string | null;
  latest_scan_age_minutes?: number | null;
  usable?: boolean | null;
  degraded_reason?: string | null;
};

const RUN_ID_RE = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})?z$/i;

export function parseRunId(runId: string | null | undefined): Date | null {
  const trimmed = String(runId ?? "").trim();
  const match = trimmed.match(RUN_ID_RE);
  if (!match) {
    return null;
  }
  const [, year, month, day, hour, minuteRaw] = match;
  const minute = Number(minuteRaw ?? "0");
  const parsed = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), minute, 0));
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function runIdToIso(runId: string | null | undefined): string | null {
  const parsed = parseRunId(runId);
  return parsed ? parsed.toISOString() : null;
}

export function formatRunLabel(runId: string): string {
  const parsed = parseRunId(runId);
  if (!parsed) {
    return runId;
  }
  const month = parsed.getUTCMonth() + 1;
  const day = String(parsed.getUTCDate()).padStart(2, "0");
  const hour = String(parsed.getUTCHours()).padStart(2, "0");
  const minute = parsed.getUTCMinutes();
  const timeLabel = minute > 0 ? `${hour}:${String(minute).padStart(2, "0")}Z` : `${hour}Z`;
  return `${timeLabel} ${month}/${day}`;
}

export function formatValidRunIssuedLabel(runId: string): string {
  const parsed = parseRunId(runId);
  if (!parsed) {
    return runId;
  }
  return formatIssuedTimeLabel(parsed);
}

export function formatIssuedTimeISO(iso: string | null | undefined): string | null {
  const parsed = parseIsoDate(iso);
  if (!parsed) {
    return null;
  }
  return formatIssuedTimeLabel(parsed);
}

// Formatter construction is expensive (~0.1-1ms each) and these run in the
// timeline readout path on every frame crossing during scrubs and playback —
// construct once at module load and share (Intl format() calls are stateless).
const ISSUED_TIME_FORMAT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
const HOUR_MINUTE_FORMAT = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
});
const FULL_VALID_TIME_FORMAT = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZoneName: "short",
});
const VALID_DATE_FORMAT = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZoneName: "short",
});
const COMPACT_DATE_TIME_FORMAT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
const COMPACT_DATE_FORMAT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
});

function formatIssuedTimeLabel(date: Date): string {
  return ISSUED_TIME_FORMAT.format(date);
}

export function formatObservedRunLabel(runId: string): string {
  const parsed = parseRunId(runId);
  if (!parsed) {
    return runId;
  }
  const timeLabel = HOUR_MINUTE_FORMAT.format(parsed);
  const month = parsed.getMonth() + 1;
  const day = String(parsed.getDate()).padStart(2, "0");
  return `${timeLabel} ${month}/${day}`;
}

export function formatObservedValidTime(iso: string | null | undefined): string | null {
  const parsed = parseIsoDate(iso);
  if (!parsed) {
    return null;
  }
  return FULL_VALID_TIME_FORMAT.format(parsed);
}

export function formatObservedCompactTime(iso: string | null | undefined): string | null {
  const parsed = parseIsoDate(iso);
  if (!parsed) {
    return null;
  }
  return HOUR_MINUTE_FORMAT.format(parsed);
}

function isWindGustVariable(variableId: string | null | undefined): boolean {
  const normalized = String(variableId ?? "").trim().toLowerCase();
  return normalized === "wgust_6h_max" || normalized === "wgust_24h_max";
}

function forecastHourFromValidTime(
  runTimeISO: string | null | undefined,
  validTimeISO: string | null | undefined,
): number | null {
  const runTime = parseIsoDate(runTimeISO);
  const validTime = parseIsoDate(validTimeISO);
  if (!runTime || !validTime) {
    return null;
  }
  const diffHours = Math.round((validTime.getTime() - runTime.getTime()) / 3_600_000);
  return Number.isFinite(diffHours) && diffHours >= 0 ? diffHours : null;
}

export function formatValidTime(
  iso: string | null | undefined,
  variableId?: string | null | undefined,
): string | null {
  const parsed = parseIsoDate(iso);
  if (!parsed) {
    return null;
  }
  if (isWindGustVariable(variableId)) {
    return FULL_VALID_TIME_FORMAT.format(parsed);
  }
  return VALID_DATE_FORMAT.format(parsed);
}

export function formatValidCompactTime(
  iso: string | null | undefined,
  variableId?: string | null | undefined,
): string | null {
  const parsed = parseIsoDate(iso);
  if (!parsed) {
    return null;
  }
  if (isWindGustVariable(variableId)) {
    return COMPACT_DATE_TIME_FORMAT.format(parsed);
  }
  return COMPACT_DATE_FORMAT.format(parsed);
}

export function validDayLabel(forecastHour: number | null | undefined): string {
  const resolved = Number.isFinite(forecastHour) ? Math.max(0, Math.round(Number(forecastHour))) : 0;
  return `Day ${resolved + 1}`;
}

export function validAxisLabel(
  forecastHour: number | null | undefined,
  variableId?: string | null | undefined,
  runTimeISO?: string | null | undefined,
  validTimeISO?: string | null | undefined,
  dayLabelOverride?: string | null | undefined,
): string {
  const overrideLabel = typeof dayLabelOverride === "string" ? dayLabelOverride.trim() : "";
  if (overrideLabel) {
    return overrideLabel;
  }
  const resolved = Number.isFinite(forecastHour) ? Math.max(0, Math.round(Number(forecastHour))) : 0;
  const normalizedVariableId = String(variableId ?? "").trim().toLowerCase();
  if (normalizedVariableId === "maxt" || normalizedVariableId === "mint") {
    return validDayLabel(resolved);
  }
  if (isWindGustVariable(normalizedVariableId)) {
    const actualForecastHour = forecastHourFromValidTime(runTimeISO, validTimeISO);
    if (actualForecastHour !== null) {
      return `FH ${actualForecastHour}`;
    }
  }
  if (resolved >= 6) {
    return `FH ${resolved}`;
  }
  return validDayLabel(resolved);
}

export function frameValidTime(row: { valid_time?: string; meta?: { meta?: { valid_time?: string | null } | null } | null } | null | undefined): string | null {
  const direct = typeof row?.valid_time === "string" && row.valid_time.trim() ? row.valid_time.trim() : null;
  if (direct) {
    return direct;
  }
  const nested = row?.meta?.meta?.valid_time;
  return typeof nested === "string" && nested.trim() ? nested.trim() : null;
}

export function frameIssueTime(row: { meta?: { meta?: { issue_time?: string | null } | null } | null } | null | undefined): string | null {
  const nested = row?.meta?.meta?.issue_time;
  return typeof nested === "string" && nested.trim() ? nested.trim() : null;
}

export function deriveObservedSourceStatus(params: {
  latestRunAvailable: boolean;
  latestRunReady: boolean | null | undefined;
  newestValidTimeISO: string | null | undefined;
  availableFrameCount: number;
  nowMs?: number;
  source?: string | null | undefined;
  delayedThresholdMinutes?: number;
  staleThresholdMinutes?: number;
}): ObservedSourceStatus {
  const sourceId = String(params.source ?? "").trim().toLowerCase();
  const defaults = OBSERVED_SOURCE_THRESHOLDS[sourceId];
  const delayedThresholdMinutes = Math.max(1, params.delayedThresholdMinutes ?? defaults?.delayed ?? 10);
  const staleThresholdMinutes = Math.max(
    delayedThresholdMinutes + 1,
    params.staleThresholdMinutes ?? defaults?.stale ?? 15,
  );

  if (!params.latestRunAvailable || params.latestRunReady === false || params.availableFrameCount <= 0) {
    return {
      tone: "unavailable",
      label: "Unavailable",
      description: "No publishable observed analysis bundle is available.",
      ageMinutes: null,
    };
  }

  const newest = parseIsoDate(params.newestValidTimeISO);
  if (!newest) {
    return {
      tone: "unavailable",
      label: "Unavailable",
      description: "Latest scan time is unavailable.",
      ageMinutes: null,
    };
  }

  const nowMs = params.nowMs ?? Date.now();
  const ageMinutes = Math.max(0, Math.round((nowMs - newest.getTime()) / 60000));
  if (ageMinutes >= staleThresholdMinutes) {
    return {
      tone: "stale",
      label: "Stale",
      description: `Newest scan is ${ageMinutes} minutes old.`,
      ageMinutes,
    };
  }
  if (ageMinutes >= delayedThresholdMinutes) {
    return {
      tone: "delayed",
      label: "Delayed",
      description: `Newest scan is ${ageMinutes} minutes old.`,
      ageMinutes,
    };
  }
  return {
    tone: "live",
    label: "Live",
    description: `Newest scan is ${ageMinutes} minute${ageMinutes === 1 ? "" : "s"} old.`,
    ageMinutes,
  };
}

export function observedSourceStatusFromAvailability(
  input: ObservedAvailabilityInput | null | undefined
): ObservedSourceStatus | null {
  const freshnessState = String(input?.freshness_state ?? "").trim().toLowerCase();
  if (!freshnessState) {
    return null;
  }
  const ageMinutes = Number.isFinite(input?.latest_scan_age_minutes)
    ? Math.max(0, Number(input?.latest_scan_age_minutes))
    : null;
  const degradedReason = String(input?.degraded_reason ?? "").trim().replace(/_/g, " ");
  const ageDescription =
    ageMinutes === null
      ? null
      : `Newest scan is ${ageMinutes} minute${ageMinutes === 1 ? "" : "s"} old.`;

  if (freshnessState === "live") {
    return {
      tone: "live",
      label: "Live",
      description: ageDescription ?? "Newest scan is within the normal freshness window.",
      ageMinutes,
    };
  }
  if (freshnessState === "delayed") {
    return {
      tone: "delayed",
      label: "Delayed",
      description: ageDescription ?? "Newest scan is delayed.",
      ageMinutes,
    };
  }
  if (freshnessState === "stale") {
    return {
      tone: "stale",
      label: "Stale",
      description: ageDescription ?? "Newest scan is stale.",
      ageMinutes,
    };
  }
  return {
    tone: "unavailable",
    label: "Unavailable",
    description: degradedReason ? `Observed source is unavailable: ${degradedReason}.` : "No publishable observed analysis bundle is available.",
    ageMinutes: null,
  };
}

function parseIsoDate(value: string | null | undefined): Date | null {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) {
    return null;
  }
  const parsed = new Date(trimmed);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}
