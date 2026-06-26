// Shared timezone + axis-time helpers for Model Guidance charts. DST-safe via
// Intl (no fixed-offset assumptions). Extracted so every chart formats the x-axis
// and day boundaries identically.

/** Unix seconds for an ISO valid_time string, or null when unparseable. */
export function toTimestampSec(validTime: string): number | null {
  const ts = Math.floor(new Date(validTime).getTime() / 1000);
  return Number.isFinite(ts) ? ts : null;
}

// Offset (ms) such that local_wall_clock_ms = utcMs + offset, for `tz` at the
// given instant. Derived by formatting the instant in `tz` and diffing.
function tzOffsetMs(utcMs: number, tz: string): number {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const map: Record<string, string> = {};
  for (const part of dtf.formatToParts(new Date(utcMs))) {
    if (part.type !== "literal") map[part.type] = part.value;
  }
  const asUTC = Date.UTC(
    Number(map.year),
    Number(map.month) - 1,
    Number(map.day),
    Number(map.hour),
    Number(map.minute),
    Number(map.second),
  );
  return asUTC - utcMs;
}

// UTC instant (ms) of 00:00 local time on the given calendar date in `tz`.
// Two-pass refine handles DST transition days.
function localMidnightMs(year: number, month: number, day: number, tz: string): number {
  const naive = Date.UTC(year, month - 1, day, 0, 0, 0);
  let t = naive - tzOffsetMs(naive, tz);
  t = naive - tzOffsetMs(t, tz);
  return t;
}

export function localYMD(
  utcMs: number,
  tz: string,
): { year: number; month: number; day: number } {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const map: Record<string, string> = {};
  for (const part of dtf.formatToParts(new Date(utcMs))) {
    if (part.type !== "literal") map[part.type] = part.value;
  }
  return { year: Number(map.year), month: Number(map.month), day: Number(map.day) };
}

/** UTC seconds of local noon on the calendar day containing `utcMs` in `tz`. */
export function localNoonSec(utcMs: number, tz: string): number {
  const { year, month, day } = localYMD(utcMs, tz);
  return Math.floor((localMidnightMs(year, month, day, tz) + 12 * 3600 * 1000) / 1000);
}

// Unix seconds of each 00:00-local day boundary within [minSec, maxSec].
export function localDayBoundaries(minSec: number, maxSec: number, tz: string): number[] {
  if (!Number.isFinite(minSec) || !Number.isFinite(maxSec) || maxSec <= minSec) return [];
  const out: number[] = [];
  const start = localYMD(minSec * 1000, tz);
  let cur = localMidnightMs(start.year, start.month, start.day, tz);
  let guard = 0;
  while (cur <= maxSec * 1000 && guard < 400) {
    const sec = Math.floor(cur / 1000);
    if (sec >= minSec && sec <= maxSec) out.push(sec);
    // Advance to the next calendar day. +26h lands inside the next day even
    // across a spring-forward; snap back to that day's local midnight.
    const next = localYMD(cur + 26 * 3600 * 1000, tz);
    cur = localMidnightMs(next.year, next.month, next.day, tz);
    guard += 1;
  }
  return out;
}

// X tick label per plan: `EEE h a` (<48 h span) else `MMM d`, in `tz`.
export function formatXTick(sec: number, tz: string, spanSec: number): string {
  const date = new Date(sec * 1000);
  if (spanSec < 48 * 3600) {
    const dtf = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      weekday: "short",
      hour: "numeric",
      hour12: true,
    });
    const map: Record<string, string> = {};
    for (const part of dtf.formatToParts(date)) {
      if (part.type !== "literal") map[part.type] = part.value;
    }
    return `${map.weekday} ${map.hour} ${map.dayPeriod}`;
  }
  return new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    month: "short",
    day: "numeric",
  }).format(date);
}

// Header timestamp for the floating cursor tooltip, in `tz`: e.g. "Mon, Jun 23, 3 PM".
export function formatTooltipTime(sec: number, tz: string): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    hour12: true,
  }).format(new Date(sec * 1000));
}
