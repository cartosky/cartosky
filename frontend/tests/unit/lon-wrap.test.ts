import { test, expect } from "vitest";

import { normalizeLongitudeDeg } from "@/lib/lon-wrap";

/**
 * Phase 3 / G1 1c: MapLibre hands the hover handler UNWRAPPED longitudes on
 * world copies, and the sampling API declares `lon ge=-180 le=180` — so
 * anything outside that range is a 422 and a silently vanishing tooltip.
 * These pin the fold, and pin just as hard that canonical longitudes are
 * returned untouched.
 */

test("canonical longitudes pass through bit-identical", () => {
  for (const lon of [-180, -179.9, -98.58, -0.0001, 0, 0.25, 179.75, 179.99, 180]) {
    expect(normalizeLongitudeDeg(lon)).toBe(lon);
  }
});

test("one world copy either side folds back onto the canonical meridian", () => {
  expect(normalizeLongitudeDeg(181)).toBeCloseTo(-179, 10);
  expect(normalizeLongitudeDeg(-181)).toBeCloseTo(179, 10);
  expect(normalizeLongitudeDeg(200)).toBeCloseTo(-160, 10);
  expect(normalizeLongitudeDeg(-200)).toBeCloseTo(160, 10);
  expect(normalizeLongitudeDeg(360)).toBeCloseTo(0, 10);
  expect(normalizeLongitudeDeg(-360)).toBeCloseTo(0, 10);
});

test("multiple world copies out fold too", () => {
  // +-540 is the antimeridian itself, one and a half worlds out.
  expect(normalizeLongitudeDeg(540)).toBe(-180);
  expect(normalizeLongitudeDeg(-540)).toBe(-180);
  expect(normalizeLongitudeDeg(541)).toBeCloseTo(-179, 10);
  expect(normalizeLongitudeDeg(-541)).toBeCloseTo(179, 10);
  expect(normalizeLongitudeDeg(1080 + 42)).toBeCloseTo(42, 10);
  expect(normalizeLongitudeDeg(-(1080 + 42))).toBeCloseTo(-42, 10);
});

test("every fold lands inside the API's declared bounds", () => {
  for (let lon = -1100; lon <= 1100; lon += 0.37) {
    const wrapped = normalizeLongitudeDeg(lon);
    expect(wrapped).toBeGreaterThanOrEqual(-180);
    expect(wrapped).toBeLessThanOrEqual(180);
    // Same physical meridian: the difference is a whole number of turns.
    const turns = (lon - wrapped) / 360;
    expect(Math.abs(turns - Math.round(turns))).toBeLessThan(1e-9);
  }
});

test("non-finite input is passed through for the caller's own validity check", () => {
  expect(normalizeLongitudeDeg(Number.NaN)).toBeNaN();
  expect(normalizeLongitudeDeg(Number.POSITIVE_INFINITY)).toBe(Number.POSITIVE_INFINITY);
});
