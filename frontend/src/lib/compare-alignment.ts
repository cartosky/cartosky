import { parseRunId } from "@/lib/time-axis";

/**
 * Forecast-hour offset that aligns the right run's frames to the left run's
 * valid times: a left frame at hour `h` and a right frame at hour
 * `h + offset` are valid at the same instant. Positive when the left run is
 * newer (e.g. left 12Z vs right 00Z → +12). Returns 0 when either run id
 * fails to parse (unresolved runs during load degrade to plain same-hour
 * comparison).
 */
export function runAlignmentOffsetHours(
  leftRun: string | null | undefined,
  rightRun: string | null | undefined,
): number {
  const left = parseRunId(leftRun);
  const right = parseRunId(rightRun);
  if (!left || !right) {
    return 0;
  }
  return Math.round((left.getTime() - right.getTime()) / 3_600_000);
}

/**
 * Re-anchor the left-anchored forecast hour when compare panels are swapped
 * across runs with different init cycles. Before swap, both panels show the
 * same valid time at `forecastHour` (left) and `forecastHour + offset` (right).
 * After swap the old right panel becomes the new left anchor, so the shared
 * valid time is preserved at `forecastHour + offset`.
 */
export function reanchorForecastHourOnSwap(
  forecastHour: number,
  runOffsetHours: number,
): number {
  return forecastHour + runOffsetHours;
}

/**
 * Left-anchored hours at which BOTH sides have a ready grid frame for the
 * SAME valid time: left hour `h` is kept when the right side has a frame at
 * `h + offsetHours`. With offset 0 (same run / unresolved) this is the plain
 * intersection.
 */
export function alignedMutualGridHours(
  leftHours: number[],
  rightHours: number[],
  offsetHours: number,
): number[] {
  const rightSet = new Set(rightHours);
  return leftHours.filter((hour) => rightSet.has(hour + offsetHours));
}
