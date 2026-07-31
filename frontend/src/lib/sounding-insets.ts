/**
 * Geometry for the two stacked inset plots (Skew-T design §7 Phase 5):
 * the hodograph and the θe-vs-pressure inset.
 *
 * Pure, DOM-free and React-free, exactly like `skewt-geometry.ts` — the
 * band-splitting and the auto-scaling are the parts that can silently go wrong,
 * so they are unit-tested rather than eyeballed in a browser.
 *
 * Heights come from the server (`profiles.height_m_agl`, hypsometrically
 * reconstructed): the client still does no thermodynamics.
 */

import { HODOGRAPH_HEIGHT_BANDS, msToKt } from "@/lib/skewt-geometry";

// ------------------------------------------------------------- hodograph

export type HodographPoint = {
  /** Wind components in KNOTS (the axis unit). */
  u: number;
  v: number;
  heightM: number;
};

export type HodographSegment = {
  /** Index into `HODOGRAPH_HEIGHT_BANDS`. */
  band: number;
  color: string;
  points: HodographPoint[];
};

export type HodographTick = { km: number; u: number; v: number };

export type HodographModel = {
  points: HodographPoint[];
  segments: HodographSegment[];
  ticks: HodographTick[];
  /** Surface wind, in knots — drawn as a dot. */
  surface: HodographPoint | null;
  /** Ring extent in kt: the axis half-range. */
  ringMaxKt: number;
};

/** Ring spacing, kt. Labels are drawn every other ring (design §7 Phase 5). */
export const HODOGRAPH_RING_STEP_KT = 10;
/** The axis never shrinks below this, so weak-flow days stay readable. */
export const HODOGRAPH_MIN_RING_KT = 40;
/** Height labels drawn on the trace. */
export const HODOGRAPH_TICK_KM = [1, 3, 6, 9] as const;

export function hodographBandIndex(heightM: number): number {
  const km = heightM / 1000;
  for (let i = 0; i < HODOGRAPH_HEIGHT_BANDS.length; i += 1) {
    if (km < HODOGRAPH_HEIGHT_BANDS[i].maxKm) return i;
  }
  return HODOGRAPH_HEIGHT_BANDS.length - 1;
}

/**
 * Ring extent for a set of points: the next 10 kt step at or above the fastest
 * wind, floored at 40 kt so a calm profile does not blow itself up to fill the
 * box.
 */
export function hodographRingMaxKt(points: ReadonlyArray<HodographPoint>): number {
  let max = 0;
  for (const point of points) {
    max = Math.max(max, Math.hypot(point.u, point.v));
  }
  return Math.max(
    HODOGRAPH_MIN_RING_KT,
    Math.ceil(max / HODOGRAPH_RING_STEP_KT) * HODOGRAPH_RING_STEP_KT,
  );
}

function interpolateAtHeight(
  a: HodographPoint,
  b: HodographPoint,
  heightM: number,
): HodographPoint {
  const span = b.heightM - a.heightM;
  const s = span === 0 ? 0 : (heightM - a.heightM) / span;
  return { u: a.u + s * (b.u - a.u), v: a.v + s * (b.v - a.v), heightM };
}

export type HodographInput = {
  u: ReadonlyArray<number | null | undefined>;
  v: ReadonlyArray<number | null | undefined>;
  /** Height AGL in metres, index-aligned with `u`/`v`; null below ground. */
  heightM: ReadonlyArray<number | null | undefined>;
  /** Surface wind components in m/s, plotted at 0 m AGL. */
  surfaceU?: number | null;
  surfaceV?: number | null;
};

/**
 * Builds the height-coloured hodograph, or `null` when there is not enough
 * usable data to draw a line.
 *
 * A level missing any of u / v / height is skipped rather than interpolated
 * across — the resulting gap in the trace is honest. Heights that fail to
 * increase are also skipped: the colour bands and the tick interpolation both
 * assume a monotonic height axis, and one bad level must not corrupt them.
 */
export function buildHodograph(input: HodographInput): HodographModel | null {
  const points: HodographPoint[] = [];

  const surfaceUsable =
    typeof input.surfaceU === "number" &&
    typeof input.surfaceV === "number" &&
    Number.isFinite(input.surfaceU) &&
    Number.isFinite(input.surfaceV);
  const surface: HodographPoint | null = surfaceUsable
    ? { u: msToKt(Number(input.surfaceU)), v: msToKt(Number(input.surfaceV)), heightM: 0 }
    : null;
  if (surface) points.push(surface);

  let lastHeight = surface ? 0 : Number.NEGATIVE_INFINITY;
  for (let i = 0; i < input.heightM.length; i += 1) {
    const height = input.heightM[i];
    const u = input.u[i];
    const v = input.v[i];
    if (height == null || u == null || v == null) continue;
    if (!Number.isFinite(height) || !Number.isFinite(u) || !Number.isFinite(v)) continue;
    if (height <= lastHeight) continue;
    lastHeight = height;
    points.push({ u: msToKt(u), v: msToKt(v), heightM: height });
  }

  if (points.length < 2) return null;

  const segments: HodographSegment[] = [];
  const push = (band: number, point: HodographPoint) => {
    const current = segments[segments.length - 1];
    if (current && current.band === band) {
      current.points.push(point);
      return;
    }
    segments.push({ band, color: HODOGRAPH_HEIGHT_BANDS[band].color, points: [point] });
  };

  let previous = points[0];
  let previousBand = hodographBandIndex(previous.heightM);
  push(previousBand, previous);
  for (let i = 1; i < points.length; i += 1) {
    const point = points[i];
    const band = hodographBandIndex(point.heightM);
    let from = previous;
    let fromBand = previousBand;
    // Split exactly at each band boundary the layer crosses, so the colour
    // change sits at 1 / 3 / 6 / 9 km rather than at the nearest level.
    while (fromBand < band) {
      const boundary = HODOGRAPH_HEIGHT_BANDS[fromBand].maxKm * 1000;
      const crossing = interpolateAtHeight(from, point, boundary);
      push(fromBand, crossing);
      fromBand += 1;
      push(fromBand, crossing);
      from = crossing;
    }
    push(band, point);
    previous = point;
    previousBand = band;
  }

  const ticks: HodographTick[] = [];
  for (const km of HODOGRAPH_TICK_KM) {
    const target = km * 1000;
    for (let i = 0; i + 1 < points.length; i += 1) {
      if (points[i].heightM <= target && target <= points[i + 1].heightM) {
        const at = interpolateAtHeight(points[i], points[i + 1], target);
        ticks.push({ km, u: at.u, v: at.v });
        break;
      }
    }
  }

  return {
    points,
    segments: segments.filter((segment) => segment.points.length >= 2),
    ticks,
    surface,
    ringMaxKt: hodographRingMaxKt(points),
  };
}

// -------------------------------------------------------------- theta-e

/** Inset pressure window (design §7 Phase 5 — TT's bottom-right panel). */
export const THETAE_P_BOTTOM = 1000;
export const THETAE_P_TOP = 300;
export const THETAE_TICK_PRESSURES = [1000, 850, 700, 500, 400, 300] as const;
/** Candidate x steps, K. ~10 K is the target; wider spans fall back. */
const THETAE_TICK_STEPS = [5, 10, 20, 25, 50] as const;
const THETAE_MAX_TICKS = 5;

export type ThetaEPoint = { p: number; value: number };
export type ThetaEModel = {
  points: ThetaEPoint[];
  xMin: number;
  xMax: number;
  xTicks: number[];
};

export type ThetaEInput = {
  levelsHPa: ReadonlyArray<number>;
  thetaE: ReadonlyArray<number | null | undefined>;
  /** Surface anchor: the surface has no slot on the isobaric ladder. */
  surfaceP?: number | null;
  surfaceValue?: number | null;
};

/**
 * θe against pressure over the 1000-300 hPa window, or `null` with nothing to
 * draw. The x domain is data-driven and padded out to whole ticks, because a
 * fixed 280-360 K domain would flatten the line to a straight edge on a cold
 * winter column.
 */
export function buildThetaE(input: ThetaEInput): ThetaEModel | null {
  const points: ThetaEPoint[] = [];

  const surfaceP = input.surfaceP;
  const surfaceValue = input.surfaceValue;
  if (
    typeof surfaceP === "number" &&
    typeof surfaceValue === "number" &&
    Number.isFinite(surfaceP) &&
    Number.isFinite(surfaceValue) &&
    surfaceP <= THETAE_P_BOTTOM &&
    surfaceP >= THETAE_P_TOP
  ) {
    points.push({ p: surfaceP, value: surfaceValue });
  }

  for (let i = 0; i < input.levelsHPa.length; i += 1) {
    const p = input.levelsHPa[i];
    const value = input.thetaE[i];
    if (value == null || !Number.isFinite(value) || !Number.isFinite(p)) continue;
    if (p > THETAE_P_BOTTOM || p < THETAE_P_TOP) continue;
    if (points.length > 0 && p >= points[points.length - 1].p) continue;
    points.push({ p, value });
  }

  if (points.length < 2) return null;

  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const point of points) {
    min = Math.min(min, point.value);
    max = Math.max(max, point.value);
  }

  const step =
    THETAE_TICK_STEPS.find(
      (candidate) => (max - min) / candidate + 1 <= THETAE_MAX_TICKS,
    ) ?? THETAE_TICK_STEPS[THETAE_TICK_STEPS.length - 1];
  const xMin = Math.floor(min / step) * step;
  const xMax = Math.ceil(max / step) * step;
  const xTicks: number[] = [];
  for (let tick = xMin; tick <= xMax + 1e-9; tick += step) xTicks.push(tick);

  // A perfectly isothermal-θe column would collapse the domain to zero width.
  return { points, xMin, xMax: xMax > xMin ? xMax : xMin + step, xTicks };
}
