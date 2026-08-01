import { afterEach, describe, expect, it, vi } from "vitest";

import {
  constrainGlobeCamera,
  GLOBE_MAX_CENTER_LATITUDE_DEG,
  globeZoomAdjustmentFromEquator,
} from "@/lib/globe-map";
import {
  globeDiscGeometry,
  globeRadiusPixels,
  isGlobeProjectionSupported,
  isPointInsideGlobeDisc,
  setGlobeProjectionSupported,
  subscribeGlobeProjectionSupport,
} from "@/lib/globe-projection";

/**
 * Phase G2 predicates. Two of them fix audit findings that a picture cannot
 * state, so they are pinned as pure functions:
 *
 *  - the disc geometry, which decides whether the hover readout fires at all
 *    (audit item 4: the tooltip pinned to the horizon value and kept rendering
 *    out at 1.4 R over empty background);
 *  - the polar camera constraint (audit item 10c: you could not frame a pole).
 */

const CAMERA = { cameraToCenterDistance: 1200, width: 1128, height: 800 };

describe("globeDiscGeometry", () => {
  /**
   * GROUND TRUTH. Each expected radius was measured on a real MapLibre 5.24
   * globe by bisecting along the +x radius from the canvas centre for the point
   * where `transform.isPointOnMapSurface()` — MapLibre's own ray/planet
   * intersection — flips false. The closed form agreed with all four to 0.01 px.
   */
  const cases: Array<{ label: string; worldSize: number; lat: number; radiusPx: number }> = [
    { label: "z1 / lat 20", worldSize: 512 * 2 ** 1, lat: 20, radiusPx: 152.76 },
    { label: "z2 / lat 45", worldSize: 512 * 2 ** 2, lat: 45, radiusPx: 346.65 },
    { label: "z0.5 / lat -60", worldSize: 512 * 2 ** 0.5, lat: -60, radiusPx: 195.91 },
    { label: "z3 / lat 0", worldSize: 512 * 2 ** 3, lat: 0, radiusPx: 451.31 },
  ];

  for (const testCase of cases) {
    it(`matches MapLibre's own limb at ${testCase.label}`, () => {
      const disc = globeDiscGeometry({
        ...CAMERA,
        worldSize: testCase.worldSize,
        centerLatDeg: testCase.lat,
      });
      expect(disc).not.toBeNull();
      expect(disc!.radiusPx).toBeCloseTo(testCase.radiusPx, 1);
      expect(disc!.centerX).toBe(CAMERA.width / 2);
      expect(disc!.centerY).toBe(CAMERA.height / 2);
    });
  }

  it("is not the naive worldSize/2pi radius", () => {
    // The audit's "disc radius is worldSize/2pi" is the small-angle limit only.
    // MapLibre puts the sphere's NEAR SURFACE at the focal distance, so the
    // camera is c2c + R from the centre and the silhouette is SMALLER than R at
    // the zooms the globe is interesting at. Getting this backwards would let
    // the hover gate admit points well off the planet.
    const worldSize = 512 * 2 ** 3;
    const disc = globeDiscGeometry({ ...CAMERA, worldSize, centerLatDeg: 0 })!;
    expect(globeRadiusPixels(worldSize, 0)).toBeCloseTo(651.9, 1);
    expect(disc.radiusPx).toBeLessThan(globeRadiusPixels(worldSize, 0));
  });

  it("returns null rather than a nonsense disc for unusable cameras", () => {
    for (const bad of [
      { worldSize: 0 },
      { worldSize: Number.NaN },
      { cameraToCenterDistance: 0 },
      { width: 0 },
      { height: Number.POSITIVE_INFINITY },
      { centerLatDeg: Number.NaN },
    ]) {
      expect(globeDiscGeometry({ ...CAMERA, worldSize: 1024, centerLatDeg: 20, ...bad })).toBeNull();
    }
  });
});

describe("isPointInsideGlobeDisc", () => {
  const disc = { centerX: 564, centerY: 400, radiusPx: 152.76 };

  it("admits the sub-camera point and the limb itself", () => {
    expect(isPointInsideGlobeDisc(disc, 564, 400)).toBe(true);
    expect(isPointInsideGlobeDisc(disc, 564 + disc.radiusPx, 400)).toBe(true);
    expect(isPointInsideGlobeDisc(disc, 564 + disc.radiusPx - 0.01, 400)).toBe(true);
  });

  it("rejects one pixel past the limb, a missing disc, and NaN", () => {
    expect(isPointInsideGlobeDisc(disc, 564 + disc.radiusPx + 1, 400)).toBe(false);
    // 1.4 R — the exact case the audit screenshotted an "83.2 °F" chip at.
    expect(isPointInsideGlobeDisc(disc, 564 + disc.radiusPx * 1.4, 400)).toBe(false);
    expect(isPointInsideGlobeDisc(null, 564, 400)).toBe(false);
    expect(isPointInsideGlobeDisc(disc, Number.NaN, 400)).toBe(false);
  });
});

describe("globeZoomAdjustmentFromEquator", () => {
  it("reproduces MapLibre's getZoomAdjustment(0, lat)", () => {
    // planetScaleAtLatitude(lat) = cos(lat), so the offset is log2(cos lat):
    // zero at the equator and increasingly negative toward a pole.
    expect(globeZoomAdjustmentFromEquator(0)).toBe(0);
    expect(globeZoomAdjustmentFromEquator(60)).toBeCloseTo(-1, 6);
    expect(globeZoomAdjustmentFromEquator(-60)).toBeCloseTo(-1, 6);
    expect(globeZoomAdjustmentFromEquator(85)).toBeCloseTo(-3.52, 2);
  });

  it("stays finite at the pole by clamping, not by dividing by zero", () => {
    expect(Number.isFinite(globeZoomAdjustmentFromEquator(90))).toBe(true);
    expect(globeZoomAdjustmentFromEquator(90)).toBe(
      globeZoomAdjustmentFromEquator(GLOBE_MAX_CENTER_LATITUDE_DEG),
    );
  });
});

describe("constrainGlobeCamera", () => {
  it("reaches the poles instead of stopping at the Web Mercator limit", () => {
    // The whole of audit item 10c: MapLibre's globe constrain clamps centre
    // latitude to 85.051 — a mercator number with no meaning on a sphere.
    expect(constrainGlobeCamera(89.5, 1, 3, 14).lat).toBe(89.5);
    expect(constrainGlobeCamera(95, 1, 3, 14).lat).toBe(GLOBE_MAX_CENTER_LATITUDE_DEG);
    expect(constrainGlobeCamera(-95, 1, 3, 14).lat).toBe(-GLOBE_MAX_CENTER_LATITUDE_DEG);
    expect(GLOBE_MAX_CENTER_LATITUDE_DEG).toBeGreaterThan(85.05112877980659);
  });

  it("lowers a flat preset's zoom floor so the whole disc can fit", () => {
    // A preset minZoom of 3 means the sphere ALWAYS overflows the canvas, so
    // there is no whole-globe view and no polar framing at all.
    expect(constrainGlobeCamera(0, -1, 3, 14).zoom).toBe(0);
    expect(constrainGlobeCamera(89.9, -20, 3, 14).zoom).toBeCloseTo(-9.16, 2);
  });

  it("never RAISES a floor a preset already set below zero", () => {
    expect(constrainGlobeCamera(0, -3, -4, 14).zoom).toBe(-3);
    expect(constrainGlobeCamera(0, -9, -4, 14).zoom).toBe(-4);
  });

  it("still clamps from above at maxZoom", () => {
    expect(constrainGlobeCamera(20, 99, 3, 14).zoom).toBe(14);
  });

  it("never produces NaN from non-finite input", () => {
    const fromNaN = constrainGlobeCamera(Number.NaN, Number.NaN, 3, 14);
    expect(Number.isFinite(fromNaN.lat)).toBe(true);
    expect(Number.isFinite(fromNaN.zoom)).toBe(true);
  });
});

describe("globe projection support flag", () => {
  afterEach(() => {
    setGlobeProjectionSupported(false);
  });

  it("starts false so the toggle is absent until a map proves the setter exists", () => {
    expect(isGlobeProjectionSupported()).toBe(false);
  });

  it("notifies once per real change and never for a no-op", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeGlobeProjectionSupport(listener);

    setGlobeProjectionSupported(true);
    setGlobeProjectionSupported(true);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenLastCalledWith(true);
    expect(isGlobeProjectionSupported()).toBe(true);

    unsubscribe();
    setGlobeProjectionSupported(false);
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
