/**
 * Skew-T projection + profile geometry (Skew-T design 2026-07-30 §6).
 *
 * Ported verbatim from the validated v4 prototype
 * (`skewt-spike/skewt_template.html`), which is the approved look. Everything
 * here is pure — no React, no DOM — so the 45° invariant, the log-p mapping,
 * the barb decomposition and the surface anchoring are unit-testable.
 *
 * ## Scale
 * The prototype draws a 620x640 plot rectangle inside an 862 px page. The
 * viewer panel is ~380-460 px wide, so the plot rectangle is scaled down while
 * the FIXED-SIZE chrome (margins, wind-barb strip, text, barb glyphs) keeps its
 * prototype pixel sizes: the SVG viewBox maps ~1 user unit to 1 CSS px at the
 * docked panel width, so labels stay legible. The prototype's 620:640 aspect is
 * preserved exactly, which is what fixes the temperature-per-pixel scale and
 * therefore the *shape* of the adiabats. The 45° isotherm angle is a property
 * of SKEW = 1 and holds at every scale.
 */

export const SKEWT_P_BOTTOM = 1050;
export const SKEWT_P_TOP = 100;
/** Temperature window at the BOTTOM of the plot (prototype constants). */
export const SKEWT_T_MIN = -45;
export const SKEWT_T_MAX = 50;
/** px of x per px of height — 1.0 puts isotherms at exactly 45°. */
export const SKEWT_SKEW = 1.0;

const REFERENCE_PLOT_W = 620;
const REFERENCE_PLOT_H = 640;

/**
 * Left-hand omega strip (Phase 5, TT layout): a fixed-width gutter between the
 * pressure labels and the plot edge. The width is reserved unconditionally so
 * the plot rectangle — and therefore the adiabat shapes — does not move
 * between a frame that has VVEL and one that does not.
 */
export const SKEWT_OMEGA_W = 22;
const OMEGA_GAP = 5;
/** Pressure labels sit to the LEFT of the omega strip. */
const PRESSURE_LABEL_W = 46;

export const SKEWT_MARGIN = {
  top: 26,
  right: 12,
  bottom: 46,
  left: PRESSURE_LABEL_W + SKEWT_OMEGA_W + OMEGA_GAP,
} as const;
/** Right-hand wind strip. Fixed size: barb glyphs never scale. */
export const SKEWT_BARB_W = 64;
const BARB_GAP = 8;

/** Default plot width — a ~440 px panel renders the chart at roughly 1:1. */
export const SKEWT_DEFAULT_PLOT_W = 300;

const LOG_BOT = Math.log(SKEWT_P_BOTTOM);
const LOG_TOP = Math.log(SKEWT_P_TOP);

export type SkewTGeometry = {
  plotW: number;
  plotH: number;
  width: number;
  height: number;
  /** Plot rectangle corners. */
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  /** Wind-barb column centre. */
  barbCx: number;
  barbX0: number;
  barbX1: number;
  /** Omega strip: left edge, right edge, and the zero baseline between them. */
  omegaX0: number;
  omegaX1: number;
  omegaCx: number;
  yOf: (p: number) => number;
  xOf: (t: number, p: number) => number;
};

/**
 * Builds every derived constant from one lever, the plot width. The plot height
 * follows the prototype aspect so the adiabat shapes are unchanged.
 */
export function createSkewTGeometry(plotWidth: number = SKEWT_DEFAULT_PLOT_W): SkewTGeometry {
  const plotW = plotWidth;
  const plotH = (plotWidth * REFERENCE_PLOT_H) / REFERENCE_PLOT_W;
  const x0 = SKEWT_MARGIN.left;
  const y0 = SKEWT_MARGIN.top;
  const x1 = x0 + plotW;
  const y1 = y0 + plotH;
  const barbX0 = x1 + BARB_GAP;
  const barbX1 = barbX0 + SKEWT_BARB_W;
  const omegaX1 = x0 - OMEGA_GAP;
  const omegaX0 = omegaX1 - SKEWT_OMEGA_W;

  const yOf = (p: number) => y1 - ((LOG_BOT - Math.log(p)) / (LOG_BOT - LOG_TOP)) * plotH;
  const xOf = (t: number, p: number) =>
    x0 + ((t - SKEWT_T_MIN) / (SKEWT_T_MAX - SKEWT_T_MIN)) * plotW + (y1 - yOf(p)) * SKEWT_SKEW;

  return {
    plotW,
    plotH,
    width: x0 + plotW + BARB_GAP + SKEWT_BARB_W + SKEWT_MARGIN.right,
    height: y0 + plotH + SKEWT_MARGIN.bottom,
    x0,
    y0,
    x1,
    y1,
    barbCx: (barbX0 + barbX1) / 2,
    barbX0,
    barbX1,
    omegaX0,
    omegaX1,
    omegaCx: (omegaX0 + omegaX1) / 2,
    yOf,
    xOf,
  };
}

export function celsiusToFahrenheit(c: number): number {
  return (c * 9) / 5 + 32;
}

/**
 * Builds an SVG path from parallel (pressure, temperature) arrays.
 *
 * Nulls / non-finite samples BREAK the pen rather than being bridged or
 * emitted, so a frame with holes never produces `NaN` in a path attribute.
 */
export function dataPath(
  geo: SkewTGeometry,
  pressures: ReadonlyArray<number | null | undefined>,
  temperatures: ReadonlyArray<number | null | undefined>,
): string {
  let d = "";
  let pen = false;
  for (let i = 0; i < pressures.length; i += 1) {
    const p = pressures[i];
    const t = temperatures[i];
    if (p == null || t == null || !Number.isFinite(p) || !Number.isFinite(t)) {
      pen = false;
      continue;
    }
    const x = geo.xOf(t, p);
    const y = geo.yOf(p);
    // yOf is log-based: p <= 0 yields non-finite coords even for finite input.
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)} `;
    pen = true;
  }
  return d.trim();
}

// ---------------------------------------------------------------- wind barbs

const MS_TO_KT = 1.9438445;

export function windSpeedKt(u: number, v: number): number {
  return Math.hypot(u, v) * MS_TO_KT;
}

/** One wind COMPONENT, m/s -> kt (the hodograph plots components, not speed). */
export function msToKt(value: number): number {
  return value * MS_TO_KT;
}

export type BarbDecomposition = { pennants: number; fulls: number; halves: number };

/**
 * Standard barb decomposition: pennant = 50 kt, full = 10 kt, half = 5 kt.
 * The speed is rounded to the nearest 5 kt first, as the prototype does.
 */
export function decomposeBarb(speedKt: number): BarbDecomposition {
  const n = Math.round(speedKt / 5) * 5;
  const pennants = Math.floor(n / 50);
  let rem = n - pennants * 50;
  const fulls = Math.floor(rem / 10);
  rem -= fulls * 10;
  return { pennants, fulls, halves: rem >= 5 ? 1 : 0 };
}

/** Below this the barb renders as a calm circle instead of a shaft. */
export const BARB_CALM_KT = 2.5;

// -------------------------------------------------- surface anchoring (§2/§6)

export type SoundingFrameLike = {
  surface: {
    pres_sfc?: number | null;
    t2m?: number | null;
    td2m?: number | null;
    u10m?: number | null;
    v10m?: number | null;
  };
  t: ReadonlyArray<number | null>;
  td: ReadonlyArray<number | null>;
  u: ReadonlyArray<number | null>;
  v: ReadonlyArray<number | null>;
};

export type AnchoredProfile = {
  /** Surface pressure in hPa, or null when the surface block has no value. */
  surfacePressure: number | null;
  pressures: number[];
  t: (number | null)[];
  td: (number | null)[];
  u: (number | null)[];
  v: (number | null)[];
  /** Isobaric levels dropped because they sit at or below the ground. */
  maskedCount: number;
  /** True when index 0 is the surface block rather than an isobaric level. */
  hasSurfaceAnchor: boolean;
};

/**
 * Drops every isobaric level at or below the surface pressure and prepends the
 * surface block, so traces start at the real ground (design §2 — the 1000 hPa
 * level is below ground over most of CONUS).
 *
 * With no usable surface pressure there is nothing to mask against: every level
 * is kept and no surface point is prepended, rather than guessing.
 */
export function anchorProfile(
  levelsHPa: ReadonlyArray<number>,
  frame: SoundingFrameLike,
): AnchoredProfile {
  const psfcRaw = frame.surface?.pres_sfc;
  const psfc = typeof psfcRaw === "number" && Number.isFinite(psfcRaw) ? psfcRaw : null;

  const keep: number[] = [];
  for (let i = 0; i < levelsHPa.length; i += 1) {
    if (psfc == null || levelsHPa[i] < psfc) {
      keep.push(i);
    }
  }

  const pressures = keep.map((i) => levelsHPa[i]);
  const t = keep.map((i) => frame.t?.[i] ?? null);
  const td = keep.map((i) => frame.td?.[i] ?? null);
  const u = keep.map((i) => frame.u?.[i] ?? null);
  const v = keep.map((i) => frame.v?.[i] ?? null);

  const hasSurfaceAnchor = psfc != null;
  if (hasSurfaceAnchor) {
    pressures.unshift(psfc);
    t.unshift(frame.surface?.t2m ?? null);
    td.unshift(frame.surface?.td2m ?? null);
    u.unshift(frame.surface?.u10m ?? null);
    v.unshift(frame.surface?.v10m ?? null);
  }

  return {
    surfacePressure: psfc,
    pressures,
    t,
    td,
    u,
    v,
    maskedCount: levelsHPa.length - keep.length,
    hasSurfaceAnchor,
  };
}

// ----------------------------------------------- dendritic growth zone (§7 P5)

/** Warm and cold edges of the DGZ, °C. */
export const DGZ_T_WARM = -12;
export const DGZ_T_COLD = -18;

export type PressureBand = {
  /** Higher pressure — the bottom of the band on screen. */
  pBottom: number;
  /** Lower pressure — the top. */
  pTop: number;
};

/** Pressure at fraction `s` along a layer, interpolating in log-p. */
function interpolatePressure(pA: number, pB: number, s: number): number {
  return Math.exp(Math.log(pA) + s * (Math.log(pB) - Math.log(pA)));
}

/**
 * Pressure interval(s) where the ENVIRONMENT temperature lies in
 * [−18, −12] °C — the dendritic growth zone.
 *
 * The crossing pressures are interpolated *inside* each layer rather than
 * snapped to the level ladder: at 25 hPa spacing a snapped band is up to a
 * whole level too tall, which on a 640 px plot is visibly wrong.
 *
 * Returns an ARRAY because the zone is not always one contiguous slab: an
 * elevated inversion can re-enter it. A warm column simply returns `[]` and the
 * caller draws nothing — the DGZ is legitimately absent, not empty-at-zero.
 */
export function dgzBands(
  pressures: ReadonlyArray<number | null | undefined>,
  temperatures: ReadonlyArray<number | null | undefined>,
): PressureBand[] {
  const raw: Array<[number, number]> = [];

  const usable = (index: number) => {
    const p = pressures[index];
    const t = temperatures[index];
    return p != null && t != null && Number.isFinite(p) && Number.isFinite(t) && p > 0;
  };

  for (let i = 0; i + 1 < pressures.length; i += 1) {
    if (!usable(i) || !usable(i + 1)) continue;
    const p0 = Number(pressures[i]);
    const p1 = Number(pressures[i + 1]);
    const t0 = Number(temperatures[i]);
    const t1 = Number(temperatures[i + 1]);

    let sLo: number;
    let sHi: number;
    if (t0 === t1) {
      // An isothermal layer is either entirely inside the zone or entirely out.
      if (t0 > DGZ_T_WARM || t0 < DGZ_T_COLD) continue;
      sLo = 0;
      sHi = 1;
    } else {
      const sWarm = (DGZ_T_WARM - t0) / (t1 - t0);
      const sCold = (DGZ_T_COLD - t0) / (t1 - t0);
      sLo = Math.max(0, Math.min(sWarm, sCold));
      sHi = Math.min(1, Math.max(sWarm, sCold));
      if (!(sHi > sLo)) continue;
    }

    const pA = interpolatePressure(p0, p1, sLo);
    const pB = interpolatePressure(p0, p1, sHi);
    raw.push([Math.max(pA, pB), Math.min(pA, pB)]);
  }

  if (raw.length === 0) return [];

  // Merge layers that meet at a shared level into one band.
  raw.sort((a, b) => b[0] - a[0]);
  const merged: PressureBand[] = [];
  for (const [bottom, top] of raw) {
    const last = merged[merged.length - 1];
    if (last && bottom >= last.pTop - 1e-6) {
      last.pTop = Math.min(last.pTop, top);
    } else {
      merged.push({ pBottom: bottom, pTop: top });
    }
  }
  return merged;
}

// ------------------------------------------------------ omega strip (§7 P5)

/** Bar length saturates here, Pa/s. Beyond it the tone still reads. */
export const OMEGA_CLAMP_PA_S = 2.0;
/** Below this the level is left blank rather than drawn as a hairline. */
export const OMEGA_MIN_PA_S = 0.05;

export type OmegaBar = {
  p: number;
  w: number;
  /** Meteorological ascent is NEGATIVE omega. */
  ascent: boolean;
  /** |w| / clamp, in [0, 1] — the bar's share of the half-strip. */
  fraction: number;
};

/**
 * Per-level omega bars, dropping levels too weak to be worth a mark.
 *
 * Sign convention is the one thing that matters here: `w < 0` is upward motion,
 * so ascent is what gets the warm tone and points at the plot.
 */
export function omegaBars(
  pressures: ReadonlyArray<number | null | undefined>,
  omega: ReadonlyArray<number | null | undefined>,
): OmegaBar[] {
  const out: OmegaBar[] = [];
  for (let i = 0; i < pressures.length; i += 1) {
    const p = pressures[i];
    const w = omega[i];
    if (p == null || w == null || !Number.isFinite(p) || !Number.isFinite(w)) continue;
    if (Math.abs(w) < OMEGA_MIN_PA_S) continue;
    out.push({
      p,
      w,
      ascent: w < 0,
      fraction: Math.min(1, Math.abs(w) / OMEGA_CLAMP_PA_S),
    });
  }
  return out;
}

/**
 * Anchors ONE extra level-aligned series onto the pressure ladder that
 * :func:`anchorProfile` produced, applying the identical below-ground mask.
 *
 * The Phase 5 overlays (wet-bulb, omega) are level-aligned arrays that arrive
 * outside the `SoundingFrameLike` shape, and duplicating the masking rule in
 * each of them is exactly how the two would drift apart.
 *
 * `surfaceValue` is optional: omega has no surface counterpart, so its anchored
 * array simply starts with a null and the bar for that row is skipped.
 */
export function anchorSeries(
  levelsHPa: ReadonlyArray<number>,
  values: ReadonlyArray<number | null | undefined>,
  options: { surfacePressure: number | null; surfaceValue?: number | null },
): (number | null)[] {
  const { surfacePressure, surfaceValue } = options;
  const out: (number | null)[] = [];
  if (surfacePressure != null) {
    out.push(typeof surfaceValue === "number" && Number.isFinite(surfaceValue) ? surfaceValue : null);
  }
  for (let i = 0; i < levelsHPa.length; i += 1) {
    if (surfacePressure != null && levelsHPa[i] >= surfacePressure) continue;
    const value = values[i];
    out.push(typeof value === "number" && Number.isFinite(value) ? value : null);
  }
  return out;
}

/**
 * Pressure ticks and the labelled subset — the Tropical Tidbits ladder the
 * prototype adopted.
 */
export const SKEWT_TICK_PRESSURES = [
  100, 150, 200, 250, 300, 400, 500, 600, 700, 800, 900, 1000,
] as const;
export const SKEWT_MAJOR_PRESSURES = [100, 200, 300, 500, 700, 1000] as const;

/** Isotherm labels / lines every 10 °C. */
export const SKEWT_ISOTHERM_STEP = 10;
export const SKEWT_ISOTHERM_MIN = -140;

/** TT convention: saturation mixing-ratio lines only in the lower troposphere. */
export const SKEWT_MIXR_TOP_P = 440;
/** Mixing-ratio value labels sit on the curve near this pressure. */
export const SKEWT_MIXR_LABEL_P = 640;

/** Prototype palette (design decision #3 — green is reserved for dewpoint). */
export const SKEWT_COLORS = {
  isotherm: "hsl(210 10% 27%)",
  isothermZero: "hsl(210 12% 38%)",
  dryAdiabat: "hsl(28 50% 34%)",
  moistAdiabat: "hsl(217 42% 40%)",
  mixingRatio: "hsl(180 30% 32%)",
  mixingRatioLabel: "hsl(180 35% 48%)",
  temperature: "hsl(4 84% 60%)",
  dewpoint: "hsl(140 68% 52%)",
  plotBackground: "hsl(222 24% 6%)",
  frame: "hsl(215 14% 40%)",
  gridMinor: "hsl(215 12% 25%)",
  tickLabel: "hsl(210 20% 80%)",
  axisLabel: "hsl(215 14% 55%)",
  ground: "hsl(30 25% 45%)",
  barb: "hsl(200 30% 74%)",
  /** SB parcel ascent (Phase 4). Purple: distinct from both traces and from
   *  every background family (tan / steel-blue / teal). */
  parcel: "hsl(280 68% 72%)",
  /** LCL / LFC / EL ticks + labels — quiet annotations, not a fourth trace. */
  levelMarker: "hsl(280 30% 62%)",
  /** Wet-bulb trace (Phase 5). A saturated blue per TT convention — well clear
   *  of the dark, desaturated steel-blue the moist adiabats use. */
  wetbulb: "hsl(200 92% 60%)",
  /** Omega strip: ascent warm/gold, descent neutral grey. */
  omegaAscent: "hsl(38 92% 58%)",
  omegaDescent: "hsl(210 10% 55%)",
  /** Dendritic growth zone: a tint, not a highlight. */
  dgzFill: "hsl(0 72% 58%)",
  dgzEdge: "hsl(0 62% 62%)",
  /** Theta-e inset. */
  thetaE: "hsl(174 62% 52%)",
} as const;

/**
 * Hodograph height bands, ascending (Phase 5). TT's red → green → blue →
 * purple ordering; the >9 km tail is neutral so it never competes with the
 * storm-relevant lower layers.
 */
export const HODOGRAPH_HEIGHT_BANDS = [
  { maxKm: 1, label: "0-1 km", color: "hsl(4 84% 62%)" },
  { maxKm: 3, label: "1-3 km", color: "hsl(140 62% 50%)" },
  { maxKm: 6, label: "3-6 km", color: "hsl(210 85% 62%)" },
  { maxKm: 9, label: "6-9 km", color: "hsl(280 68% 70%)" },
  { maxKm: Number.POSITIVE_INFINITY, label: ">9 km", color: "hsl(210 12% 62%)" },
] as const;
