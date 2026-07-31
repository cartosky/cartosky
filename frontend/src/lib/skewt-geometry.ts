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

export const SKEWT_MARGIN = { top: 26, right: 12, bottom: 46, left: 46 } as const;
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
} as const;
