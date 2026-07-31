/**
 * Skew-T projection + profile geometry (Skew-T design 2026-07-30 §6, Phase 3).
 *
 * These pin the properties the prototype was VERIFIED on (design §9): a
 * constant-temperature profile plots at exactly 45°, pressure maps
 * logarithmically, barbs decompose by the standard 50/10/5 kt convention, and
 * below-ground levels never reach the renderer.
 */
import { describe, expect, it } from "vitest";

import {
  anchorProfile,
  anchorSeries,
  celsiusToFahrenheit,
  createSkewTGeometry,
  dataPath,
  decomposeBarb,
  dgzBands,
  omegaBars,
  SKEWT_P_BOTTOM,
  SKEWT_P_TOP,
  windSpeedKt,
} from "../../src/lib/skewt-geometry";

const geo = createSkewTGeometry(300);

describe("skew projection", () => {
  it("plots a constant-temperature profile at exactly 45 degrees", () => {
    for (const plotWidth of [200, 300, 620]) {
      const g = createSkewTGeometry(plotWidth);
      for (const t of [-40, -10, 0, 25]) {
        const bottom = { x: g.xOf(t, 1000), y: g.yOf(1000) };
        const top = { x: g.xOf(t, 200), y: g.yOf(200) };
        const angleDeg = (Math.atan2(-(top.y - bottom.y), top.x - bottom.x) * 180) / Math.PI;
        expect(angleDeg).toBeCloseTo(45, 9);
      }
    }
  });

  it("keeps the temperature axis linear at a fixed pressure", () => {
    const at0 = geo.xOf(0, 700);
    const at10 = geo.xOf(10, 700);
    const at20 = geo.xOf(20, 700);
    expect(at10 - at0).toBeCloseTo(at20 - at10, 9);
  });

  it("maps pressure logarithmically, bottom to top", () => {
    expect(geo.yOf(SKEWT_P_BOTTOM)).toBeCloseTo(geo.y1, 9);
    expect(geo.yOf(SKEWT_P_TOP)).toBeCloseTo(geo.y0, 9);
    // Equal pressure RATIOS span equal screen distances.
    const decadeA = geo.yOf(1000) - geo.yOf(500);
    const decadeB = geo.yOf(400) - geo.yOf(200);
    expect(decadeA).toBeCloseTo(decadeB, 6);
    // and lower pressure is higher on screen.
    expect(geo.yOf(500)).toBeLessThan(geo.yOf(1000));
  });

  it("preserves the prototype 620:640 plot aspect at any width", () => {
    const g = createSkewTGeometry(310);
    expect(g.plotH / g.plotW).toBeCloseTo(640 / 620, 12);
  });
});

describe("dataPath", () => {
  it("breaks the pen at nulls instead of bridging them", () => {
    const d = dataPath(geo, [1000, 900, 800, 700], [10, null, 0, -5]);
    expect(d.match(/M/g)).toHaveLength(2);
    expect(d).not.toContain("NaN");
  });

  it("never emits NaN for non-finite samples", () => {
    const d = dataPath(geo, [1000, 900, 800], [Number.NaN, Number.POSITIVE_INFINITY, -5]);
    expect(d).not.toContain("NaN");
    expect(d).not.toContain("Infinity");
    expect(d.match(/M/g)).toHaveLength(1);
  });

  it("returns an empty string when every sample is missing", () => {
    expect(dataPath(geo, [1000, 900], [null, null])).toBe("");
  });
});

describe("wind barbs", () => {
  it("converts m/s to kt", () => {
    expect(windSpeedKt(10, 0)).toBeCloseTo(19.438445, 5);
    expect(windSpeedKt(3, 4)).toBeCloseTo(5 * 1.9438445, 5);
  });

  it("decomposes with pennant=50, full=10, half=5", () => {
    expect(decomposeBarb(3)).toEqual({ pennants: 0, fulls: 0, halves: 1 });
    expect(decomposeBarb(5)).toEqual({ pennants: 0, fulls: 0, halves: 1 });
    expect(decomposeBarb(10)).toEqual({ pennants: 0, fulls: 1, halves: 0 });
    expect(decomposeBarb(25)).toEqual({ pennants: 0, fulls: 2, halves: 1 });
    expect(decomposeBarb(50)).toEqual({ pennants: 1, fulls: 0, halves: 0 });
    expect(decomposeBarb(75)).toEqual({ pennants: 1, fulls: 2, halves: 1 });
    expect(decomposeBarb(125)).toEqual({ pennants: 2, fulls: 2, halves: 1 });
  });

  it("rounds to the nearest 5 kt before decomposing", () => {
    expect(decomposeBarb(12)).toEqual({ pennants: 0, fulls: 1, halves: 0 });
    expect(decomposeBarb(13)).toEqual({ pennants: 0, fulls: 1, halves: 1 });
  });
});

describe("surface anchoring", () => {
  const levels = [1000, 975, 950, 925, 900];
  const frame = {
    surface: { pres_sfc: 968.5, t2m: 30, td2m: 20, u10m: 4, v10m: -3 },
    t: [26, 25, 24, 23, 22],
    td: [18, 17, 16, 15, 14],
    u: [1, 2, 3, 4, 5],
    v: [1, 2, 3, 4, 5],
  };

  it("drops below-ground levels and prepends the surface block", () => {
    const anchored = anchorProfile(levels, frame);
    expect(anchored.surfacePressure).toBe(968.5);
    // 1000 and 975 are at or below ground.
    expect(anchored.maskedCount).toBe(2);
    expect(anchored.pressures).toEqual([968.5, 950, 925, 900]);
    expect(anchored.t).toEqual([30, 24, 23, 22]);
    expect(anchored.td).toEqual([20, 16, 15, 14]);
    expect(anchored.u).toEqual([4, 3, 4, 5]);
    expect(anchored.v).toEqual([-3, 3, 4, 5]);
    expect(anchored.hasSurfaceAnchor).toBe(true);
  });

  it("never keeps a level at or below the surface pressure", () => {
    const anchored = anchorProfile(levels, {
      ...frame,
      surface: { ...frame.surface, pres_sfc: 950 },
    });
    expect(anchored.pressures.slice(1).every((p) => p < 950)).toBe(true);
    expect(anchored.pressures[0]).toBe(950);
  });

  it("keeps every level and adds no surface point when pres_sfc is null", () => {
    const anchored = anchorProfile(levels, {
      ...frame,
      surface: { pres_sfc: null, t2m: null, td2m: null, u10m: null, v10m: null },
    });
    expect(anchored.hasSurfaceAnchor).toBe(false);
    expect(anchored.surfacePressure).toBeNull();
    expect(anchored.pressures).toEqual(levels);
    expect(anchored.maskedCount).toBe(0);
  });

  it("carries per-level nulls through untouched for the pen-break", () => {
    const anchored = anchorProfile(levels, {
      ...frame,
      t: [26, 25, null, 23, 22],
    });
    expect(anchored.t).toEqual([30, null, 23, 22]);
  });

  it("produces a dewpoint path that spans the full plot height", () => {
    // Decision #3: Td is drawn full, never clipped at the -40 C level.
    const anchored = anchorProfile([1000, 500, 200, 100], {
      surface: { pres_sfc: 990, t2m: 30, td2m: 20, u10m: 0, v10m: 0 },
      t: [26, -5, -50, -70],
      td: [18, -20, -60, -80],
      u: [0, 0, 0, 0],
      v: [0, 0, 0, 0],
    });
    const d = dataPath(geo, anchored.pressures, anchored.td);
    const ys = [...d.matchAll(/[ML]\S+ (\S+)/g)].map((m) => Number(m[1]));
    expect(Math.min(...ys)).toBeCloseTo(geo.yOf(100), 6);
  });
});

describe("celsiusToFahrenheit", () => {
  it("matches the trace-bottom label conversion", () => {
    expect(celsiusToFahrenheit(0)).toBe(32);
    expect(Math.round(celsiusToFahrenheit(37.2))).toBe(99);
  });
});

// ---------------------------------------------------------------- Phase 5

describe("dendritic growth zone", () => {
  /** Linear-in-log-p column crossing −12 and −18 between known levels. */
  const pressures = [900, 800, 700, 600, 500, 400, 300];
  const temps = [10, 2, -6, -14, -24, -36, -48];

  it("interpolates the crossing pressures instead of snapping to levels", () => {
    const bands = dgzBands(pressures, temps);
    expect(bands).toHaveLength(1);
    const [band] = bands;
    // −12 °C sits between 700 (−6) and 600 (−14); −18 between 600 and 500.
    expect(band.pBottom).toBeGreaterThan(600);
    expect(band.pBottom).toBeLessThan(700);
    expect(band.pTop).toBeGreaterThan(500);
    expect(band.pTop).toBeLessThan(600);
    // Not a level value: a snapped implementation would return 700/500.
    expect(pressures).not.toContain(band.pBottom);
    expect(pressures).not.toContain(band.pTop);
  });

  it("is empty for a warm column", () => {
    expect(dgzBands(pressures, temps.map((t) => t + 40))).toEqual([]);
  });

  it("is empty for a column that is already colder than the zone", () => {
    expect(dgzBands(pressures, temps.map((t) => t - 40))).toEqual([]);
  });

  it("reports every slab when the column crosses the zone repeatedly", () => {
    // Cools through the zone, warms back out, then cools through it again:
    // three separate passes, and a single-band implementation would smear them
    // into one slab spanning the warm nose in between.
    const p = [700, 650, 600, 550, 500, 450];
    const t = [-10, -16, -20, -16, -10, -20];
    const bands = dgzBands(p, t);
    expect(bands).toHaveLength(3);
    for (let i = 0; i < bands.length; i += 1) {
      expect(bands[i].pBottom).toBeGreaterThan(bands[i].pTop);
      // Sorted downward and disjoint.
      if (i > 0) expect(bands[i].pBottom).toBeLessThan(bands[i - 1].pTop);
    }
  });

  it("merges layers that meet at a shared level into one band", () => {
    // Whole 700-500 span sits inside the zone.
    const bands = dgzBands([700, 600, 500], [-13, -15, -17]);
    expect(bands).toHaveLength(1);
    expect(bands[0].pBottom).toBeCloseTo(700, 6);
    expect(bands[0].pTop).toBeCloseTo(500, 6);
  });

  it("breaks at nulls instead of bridging them", () => {
    const bands = dgzBands([700, 600, 500], [-13, null, -17]);
    expect(bands).toEqual([]);
  });
});

describe("omega bars", () => {
  it("marks negative omega as ascent and scales against the clamp", () => {
    const bars = omegaBars([900, 800, 700], [-1.0, 0.5, -4.0]);
    expect(bars.map((bar) => bar.ascent)).toEqual([true, false, true]);
    expect(bars[0].fraction).toBeCloseTo(0.5, 9);
    expect(bars[1].fraction).toBeCloseTo(0.25, 9);
    // Clamped, not extrapolated off the end of the strip.
    expect(bars[2].fraction).toBe(1);
  });

  it("drops levels below the noise floor and nulls entirely", () => {
    const bars = omegaBars([900, 800, 700, 600], [0.01, null, -0.04, -0.6]);
    expect(bars.map((bar) => bar.p)).toEqual([600]);
  });

  it("returns nothing when the frame carries no omega", () => {
    expect(omegaBars([900, 800], [null, null])).toEqual([]);
    expect(omegaBars([900, 800], [])).toEqual([]);
  });
});

describe("anchorSeries", () => {
  const levels = [1000, 975, 950, 925];

  it("applies the same below-ground mask as anchorProfile", () => {
    const profile = anchorProfile(levels, {
      surface: { pres_sfc: 968.5, t2m: 30, td2m: 20, u10m: 1, v10m: 2 },
      t: [1, 2, 3, 4],
      td: [0, 1, 2, 3],
      u: [1, 1, 1, 1],
      v: [1, 1, 1, 1],
    });
    const series = anchorSeries(levels, [10, 20, 30, 40], {
      surfacePressure: profile.surfacePressure,
      surfaceValue: 5,
    });
    expect(series).toHaveLength(profile.pressures.length);
    expect(series).toEqual([5, 30, 40]);
  });

  it("starts with a null when the series has no surface counterpart", () => {
    const series = anchorSeries(levels, [10, 20, 30, 40], { surfacePressure: 968.5 });
    expect(series[0]).toBeNull();
  });

  it("keeps every level when there is no surface pressure to mask against", () => {
    expect(anchorSeries(levels, [10, 20, 30, 40], { surfacePressure: null })).toEqual([
      10, 20, 30, 40,
    ]);
  });

  it("normalises missing and non-finite samples to null", () => {
    expect(
      anchorSeries(levels, [10, undefined, Number.NaN, null], { surfacePressure: null }),
    ).toEqual([10, null, null, null]);
  });
});
