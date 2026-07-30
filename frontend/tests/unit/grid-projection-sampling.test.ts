import { test, expect } from "vitest";

import {
  computeDiffGrid,
  resampleGridToReference,
  type GridMeta,
} from "@/lib/compare-diff";
import {
  gridUvToLonLat,
  latToGridV,
  lonLatToGridUv,
  lonToGridU,
  sampleGridPoints,
} from "@/lib/grid-sample";

/**
 * Pure-logic contract tests for the CPU-side sampling paths under the global
 * domain's native EPSG:4326 grid (docs/GLOBAL_DOMAIN_4326_CONTRACT.md).
 */

/** Contract §1: 1440 x 721 at 0.25 degrees, cell-edge bbox. */
const GLOBAL_BBOX: [number, number, number, number] = [-180.125, -90.125, 179.875, 90.125];
const GLOBAL_WIDTH = 1440;
const GLOBAL_HEIGHT = 721;

/** Texel column a normalized u lands on, i.e. what sampleBilinearValue indexes. */
function texelXFor(u: number, width: number): number {
  return u * width - 0.5;
}

function texelYFor(v: number, height: number): number {
  return v * height - 0.5;
}

// --- Backend-formula parity: contract §1 center formulas -------------------

test("EPSG:4326 grid maps contract column/row centers exactly", () => {
  const cases: Array<[number, number]> = [
    [-180, 0],
    [-179.75, 1],
    [0, 720],
    [179.75, 1439],
  ];
  for (const [lon, expectedCol] of cases) {
    const u = lonToGridU(lon, GLOBAL_BBOX, "EPSG:4326");
    expect(u, `lon ${lon} must be inside the global bbox`).not.toBeNull();
    expect(texelXFor(u as number, GLOBAL_WIDTH)).toBeCloseTo(expectedCol, 9);
  }

  const rowCases: Array<[number, number]> = [
    [90, 0],
    [89.75, 1],
    [0, 360],
    [-89.75, 719],
    [-90, 720],
  ];
  for (const [lat, expectedRow] of rowCases) {
    const v = latToGridV(lat, GLOBAL_BBOX, "EPSG:4326");
    expect(v, `lat ${lat} must be inside the global bbox`).not.toBeNull();
    expect(texelYFor(v as number, GLOBAL_HEIGHT)).toBeCloseTo(expectedRow, 9);
  }
});

test("lon +180 wraps onto the -180 seam column (contract §3)", () => {
  const seam = lonToGridU(-180, GLOBAL_BBOX, "EPSG:4326");
  const wrapped = lonToGridU(180, GLOBAL_BBOX, "EPSG:4326");
  expect(wrapped).not.toBeNull();
  expect(wrapped).toBeCloseTo(seam as number, 12);
  expect(texelXFor(wrapped as number, GLOBAL_WIDTH)).toBeCloseTo(0, 9);

  // 179.8 is still inside the east cell edge (179.875), just past column 1439.
  const insideEast = lonToGridU(179.8, GLOBAL_BBOX, "EPSG:4326");
  expect(insideEast).not.toBeNull();
  expect(texelXFor(insideEast as number, GLOBAL_WIDTH)).toBeCloseTo(1439.2, 6);

  // 179.9 is PAST the east cell edge (contract §2: the bbox stops 0.125 short
  // of +180). It wraps to -180.1, i.e. into the western half-cell overhang of
  // the seam column, landing at texel -0.4 — nearer column 0 (at 180.0) than
  // column 1439 (at 179.75), which is the physically right answer. Blending
  // across the seam is Phase 3 work.
  const nearEast = lonToGridU(179.9, GLOBAL_BBOX, "EPSG:4326");
  expect(nearEast).not.toBeNull();
  expect(texelXFor(nearEast as number, GLOBAL_WIDTH)).toBeCloseTo(-0.4, 6);

  // -179.9 sits between the seam column center (-180) and column 1.
  const nearWest = lonToGridU(-179.9, GLOBAL_BBOX, "EPSG:4326");
  expect(nearWest).not.toBeNull();
  expect(texelXFor(nearWest as number, GLOBAL_WIDTH)).toBeCloseTo(0.4, 6);

  // A regional 4326 grid must NOT gain wrap-around behavior.
  const regional: [number, number, number, number] = [-100, 30, -80, 45];
  expect(lonToGridU(180, regional, "EPSG:4326")).toBeNull();
  expect(lonToGridU(-180, regional, "EPSG:4326")).toBeNull();
});

test("EPSG:4326 latitude is not mercator-clamped at the poles", () => {
  // The mercator branch clamps at +/-85.05; the geographic branch must reach
  // the literal pole rows (contract §1, §3).
  const north = latToGridV(90, GLOBAL_BBOX, "EPSG:4326");
  const south = latToGridV(-90, GLOBAL_BBOX, "EPSG:4326");
  expect(north).not.toBeNull();
  expect(south).not.toBeNull();
  expect(latToGridV(86, GLOBAL_BBOX, "EPSG:4326")).not.toBeCloseTo(
    latToGridV(88, GLOBAL_BBOX, "EPSG:4326") as number,
    9,
  );
});

// --- Mercator byte-identity ------------------------------------------------

const MERCATOR_HALF_WORLD = 20037508.342789244;
const CONUS_BBOX: [number, number, number, number] = [-14922340, 2714341, -6679169, 7361866];

/** Verbatim copy of the pre-projection-awareness mercator formulas. */
function legacyLonToGridU(lon: number, bbox: [number, number, number, number]): number | null {
  const [west, , east] = bbox;
  const toX = (x: number) => (x + MERCATOR_HALF_WORLD) / (2 * MERCATOR_HALF_WORLD);
  const mx = (lon * MERCATOR_HALF_WORLD) / 180;
  const left = toX(west);
  const right = toX(east);
  const pointX = toX(mx);
  const spanX = right - left;
  if (!Number.isFinite(spanX) || spanX === 0) {
    return null;
  }
  const u = (pointX - left) / spanX;
  if (!Number.isFinite(u) || u < 0 || u > 1) {
    return null;
  }
  return u;
}

function legacyLatToGridV(lat: number, bbox: [number, number, number, number]): number | null {
  const [, south, , north] = bbox;
  const toY = (y: number) => (MERCATOR_HALF_WORLD - y) / (2 * MERCATOR_HALF_WORLD);
  const clampedLat = Math.max(-85.05112878, Math.min(85.05112878, lat));
  const yRadians = (clampedLat * Math.PI) / 180;
  const my = Math.log(Math.tan(Math.PI / 4 + yRadians / 2)) * (MERCATOR_HALF_WORLD / Math.PI);
  const top = toY(north);
  const bottom = toY(south);
  const pointY = bottom - top;
  if (!Number.isFinite(pointY) || pointY === 0) {
    return null;
  }
  const v = (toY(my) - top) / pointY;
  if (!Number.isFinite(v) || v < 0 || v > 1) {
    return null;
  }
  return v;
}

function legacyGridUvToLonLat(
  u: number,
  v: number,
  bbox: [number, number, number, number],
): [number, number] {
  const [west, south, east, north] = bbox;
  const meterX = west + u * (east - west);
  const meterY = north + v * (south - north);
  const lon = (meterX * 180) / MERCATOR_HALF_WORLD;
  const latRadians = 2 * Math.atan(Math.exp((meterY * Math.PI) / MERCATOR_HALF_WORLD)) - Math.PI / 2;
  return [lon, (latRadians * 180) / Math.PI];
}

test("omitting projection reproduces the legacy mercator math bit-for-bit", () => {
  const lons = [-125, -110.375, -100, -95.5, -80, -66.9, -180, 0, 40];
  const lats = [24.1, 30, 37.7777, 45, 49.9, 85.1, -12, 89, -89];
  for (const lon of lons) {
    expect(lonToGridU(lon, CONUS_BBOX)).toEqual(legacyLonToGridU(lon, CONUS_BBOX));
    expect(lonToGridU(lon, CONUS_BBOX, "EPSG:3857")).toEqual(legacyLonToGridU(lon, CONUS_BBOX));
    expect(lonToGridU(lon, CONUS_BBOX, undefined)).toEqual(legacyLonToGridU(lon, CONUS_BBOX));
  }
  for (const lat of lats) {
    expect(latToGridV(lat, CONUS_BBOX)).toEqual(legacyLatToGridV(lat, CONUS_BBOX));
    expect(latToGridV(lat, CONUS_BBOX, "EPSG:3857")).toEqual(legacyLatToGridV(lat, CONUS_BBOX));
  }
  for (const u of [0, 0.13, 0.5, 0.87, 1]) {
    for (const v of [0, 0.13, 0.5, 0.87, 1]) {
      expect(gridUvToLonLat(u, v, CONUS_BBOX)).toEqual(legacyGridUvToLonLat(u, v, CONUS_BBOX));
      expect(gridUvToLonLat(u, v, CONUS_BBOX, "EPSG:3857"))
        .toEqual(legacyGridUvToLonLat(u, v, CONUS_BBOX));
    }
  }
  // Unknown / malformed declarations fall back to mercator (contract §4).
  expect(lonToGridU(-95, CONUS_BBOX, "epsg:9999")).toEqual(legacyLonToGridU(-95, CONUS_BBOX));
  expect(lonToGridU(-95, CONUS_BBOX, "")).toEqual(legacyLonToGridU(-95, CONUS_BBOX));
  expect(lonLatToGridUv(-95, 40, CONUS_BBOX)).toEqual(lonLatToGridUv(-95, 40, CONUS_BBOX, "EPSG:3857"));
});

// --- City-label sampling on the global grid --------------------------------

/**
 * Pack a uint16 frame whose value at (col, row) is `col` — so a decoded sample
 * reads back the column index the sampler chose, scale 1 / offset 0.
 */
function packColumnIndexFrame(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(width * height * 2);
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      const index = (row * width + col) * 2;
      bytes[index] = col & 0xff;
      bytes[index + 1] = (col >> 8) & 0xff;
    }
  }
  return bytes;
}

test("city sampling on the global 4326 grid resolves seam longitudes", () => {
  // A narrow stand-in for the global grid: same bbox and cell size logic, only
  // 8 rows, so the fixture stays small. Column math is what is under test.
  const height = 8;
  const bbox: [number, number, number, number] = GLOBAL_BBOX;
  const bytes = packColumnIndexFrame(GLOBAL_WIDTH, height);
  const grid = {
    width: GLOBAL_WIDTH,
    height,
    dtype: "uint16",
    scale: 1,
    offset: 0,
    nodata: 65535,
  };
  const values = sampleGridPoints({
    bytes,
    grid,
    bbox,
    projection: "EPSG:4326",
    points: [
      { id: "seam-west", lon: -180, lat: 0 },
      { id: "antimeridian", lon: 180, lat: 0 },
      { id: "near-west", lon: -179.9, lat: 0 },
      { id: "inside-east", lon: 179.8, lat: 0 },
      { id: "near-east", lon: 179.9, lat: 0 },
      { id: "greenwich", lon: 0, lat: 0 },
      { id: "pole", lon: 0, lat: 90 },
    ],
  });

  // Values are column indices; bilinear at a column center returns it exactly,
  // rounded to 0.1 by sampleBilinearValue.
  expect(values["seam-west"]).toBe(0);
  expect(values.antimeridian).toBe(0);
  expect(values["near-west"]).toBeCloseTo(0.4, 5);
  // 179.8 -> texel 1439.2, clamped against the last column: still 1439.
  expect(values["inside-east"]).toBe(1439);
  // 179.9 wraps into the seam column's west overhang -> texel -0.4 -> column 0.
  expect(values["near-east"]).toBe(0);
  expect(values.greenwich).toBe(720);
  expect(values.pole).toBe(720);

  // Without the projection the same call is garbage: the degree bbox read as
  // mercator meters puts every real longitude outside the grid.
  const mercatorRead = sampleGridPoints({
    bytes,
    grid,
    bbox,
    points: [{ id: "greenwich", lon: 0, lat: 0 }, { id: "seam-west", lon: -180, lat: 0 }],
  });
  expect(mercatorRead["seam-west"]).toBeNull();
});

// --- Mixed-projection compare ----------------------------------------------

/**
 * Smooth analytic field, low curvature over the test window so the residual is
 * dominated by bilinear interpolation error rather than by the field itself.
 */
function analyticField(lon: number, lat: number): number {
  return 15 + 0.4 * (lon + 90) + 0.25 * (lat - 37) + 0.5 * Math.sin((lon + 90) / 6);
}

function lonLatToMercator(lon: number, lat: number): [number, number] {
  const x = (lon * MERCATOR_HALF_WORLD) / 180;
  const phi = (lat * Math.PI) / 180;
  const y = Math.log(Math.tan(Math.PI / 4 + phi / 2)) * (MERCATOR_HALF_WORLD / Math.PI);
  return [x, y];
}

function fillFromField(meta: GridMeta, bias = 0): Float32Array {
  const out = new Float32Array(meta.width * meta.height);
  for (let row = 0; row < meta.height; row += 1) {
    for (let col = 0; col < meta.width; col += 1) {
      const u = (col + 0.5) / meta.width;
      const v = (row + 0.5) / meta.height;
      const [lon, lat] = gridUvToLonLat(u, v, meta.bbox, meta.projection);
      out[row * meta.width + col] = analyticField(lon, lat) + bias;
    }
  }
  return out;
}

/** Max |a - b| over the interior, skipping `margin` cells of edge clamping. */
function interiorMaxAbsDiff(a: Float32Array, b: Float32Array, meta: GridMeta, margin: number): number {
  let worst = 0;
  for (let row = margin; row < meta.height - margin; row += 1) {
    for (let col = margin; col < meta.width - margin; col += 1) {
      const index = row * meta.width + col;
      const delta = Math.abs(a[index] - b[index]);
      if (Number.isNaN(delta)) {
        return NaN;
      }
      worst = Math.max(worst, delta);
    }
  }
  return worst;
}

// Test window: CONUS-ish mid-latitudes, where mercator row spacing differs most
// visibly from uniform degrees within a region a compare pair could share.
const WINDOW = { west: -100, east: -80, south: 30, north: 45 };
const [mercWest, mercSouth] = lonLatToMercator(WINDOW.west, WINDOW.south);
const [mercEast, mercNorth] = lonLatToMercator(WINDOW.east, WINDOW.north);

const MERCATOR_META: GridMeta = {
  width: 160,
  height: 120,
  bbox: [mercWest, mercSouth, mercEast, mercNorth],
  dtype: "uint16",
  scale: 1,
  offset: 0,
  nodata: 65535,
};

const GEOGRAPHIC_META: GridMeta = {
  width: 81,
  height: 61,
  bbox: [WINDOW.west, WINDOW.south, WINDOW.east, WINDOW.north],
  projection: "EPSG:4326",
  dtype: "uint16",
  scale: 1,
  offset: 0,
  nodata: 65535,
};

/**
 * Tolerance derivation. Once both sides go through their own projection the
 * only residual is bilinear interpolation of a field with non-zero curvature in
 * the source grid's coordinate, bounded by |f''| * h^2 / 8 per axis. Here the
 * curvature comes from the sinusoid (amplitude 0.5, period ~38 deg of lon =>
 * |f''| <= 0.5/36 per deg^2) and from the lat-vs-mercator-y warp of the linear
 * term; at 0.125 deg/col and ~0.14 deg/row that bound is ~1e-4 per axis.
 * Measured worst-case interior residual: 3.4e-5 (3857 -> 4326) and 9.5e-5
 * (4326 -> 3857), consistent with the bound. 5e-4 is ~5x headroom over the
 * measurement and still three orders of magnitude below what a projection bug
 * costs (O(1) or all-NaN — see the negative controls below).
 *
 * The interior margin of 2 cells is required, not cosmetic: at the reference
 * edges the source uv falls outside the source's outermost texel centers and
 * `sampleBilinearFloat` clamps, which flat-extrapolates. Measured edge residual
 * with margin 0 is 4.1e-2 — an artifact of clamping, not of projection.
 */
const MIXED_PROJECTION_TOLERANCE = 5e-4;

test("resample between a 3857 grid and a 4326 grid recovers the same field", () => {
  const mercatorValues = fillFromField(MERCATOR_META);
  const geographicValues = fillFromField(GEOGRAPHIC_META);

  // 3857 source -> 4326 reference.
  const ontoGeographic = resampleGridToReference(mercatorValues, MERCATOR_META, GEOGRAPHIC_META);
  const forward = interiorMaxAbsDiff(ontoGeographic, geographicValues, GEOGRAPHIC_META, 2);
  expect(forward).toBeLessThan(MIXED_PROJECTION_TOLERANCE);

  // 4326 source -> 3857 reference.
  const ontoMercator = resampleGridToReference(geographicValues, GEOGRAPHIC_META, MERCATOR_META);
  const reverse = interiorMaxAbsDiff(ontoMercator, mercatorValues, MERCATOR_META, 2);
  expect(reverse).toBeLessThan(MIXED_PROJECTION_TOLERANCE);
});

test("mixed-projection resample detects a deliberate mismatch", () => {
  const geographicValues = fillFromField(GEOGRAPHIC_META);

  // Negative control 1: the same field biased by a constant must NOT pass.
  const biased = fillFromField(MERCATOR_META, 1);
  const ontoGeographic = resampleGridToReference(biased, MERCATOR_META, GEOGRAPHIC_META);
  const biasedResidual = interiorMaxAbsDiff(ontoGeographic, geographicValues, GEOGRAPHIC_META, 2);
  expect(biasedResidual).toBeGreaterThan(0.9);

  // Negative control 2: dropping the 4326 declaration (the pre-fix behavior)
  // reads the degree bbox as meters, so nothing maps into the source grid.
  const undeclared: GridMeta = { ...GEOGRAPHIC_META, projection: undefined };
  const mercatorValues = fillFromField(MERCATOR_META);
  const wrong = resampleGridToReference(mercatorValues, MERCATOR_META, undeclared);
  expect(wrong.every((value) => Number.isNaN(value))).toBe(true);
});

test("computeDiffGrid across projections is near-zero for an identical field", () => {
  // Pack both sides at 0.01-unit resolution so quantization stays well under
  // the interpolation tolerance, then diff via the public entry point.
  const packMeta = (meta: GridMeta): { bytes: Uint8Array; meta: GridMeta } => {
    const scale = 0.01;
    const offset = 0;
    const floats = fillFromField(meta);
    const bytes = new Uint8Array(meta.width * meta.height * 2);
    for (let index = 0; index < floats.length; index += 1) {
      const encoded = Math.round((floats[index] - offset) / scale);
      bytes[index * 2] = encoded & 0xff;
      bytes[index * 2 + 1] = (encoded >> 8) & 0xff;
    }
    return { bytes, meta: { ...meta, scale, offset, units: "degF" } };
  };

  const left = packMeta(MERCATOR_META);
  const right = packMeta(GEOGRAPHIC_META);
  const { diffFloats, refMeta } = computeDiffGrid(left.bytes, right.bytes, left.meta, right.meta);
  // The geographic grid is coarser, so it is the reference.
  expect(refMeta.projection).toBe("EPSG:4326");

  const zeros = new Float32Array(diffFloats.length);
  const worst = interiorMaxAbsDiff(diffFloats, zeros, refMeta, 2);
  // Interpolation tolerance plus one packing quantum on each side.
  expect(worst).toBeLessThan(MIXED_PROJECTION_TOLERANCE + 0.02);
});
