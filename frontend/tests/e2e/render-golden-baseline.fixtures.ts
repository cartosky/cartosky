/**
 * Step 1 render golden baseline — deterministic fixtures.
 *
 * Purpose: freeze REAL rendered map content (through src/lib/grid-webgl.ts's
 * WebGL shader, composited by MapLibre on SwiftShader) for three cases that
 * take three DIFFERENT shader branches, so an upcoming per-fragment
 * reprojection change in grid-webgl.ts can be proven pixel-equivalent.
 *
 *   A. gfs / tmp2m        uint16 continuous decode -> sampleBilinear()
 *   B. hrrr / radar_ptype uint16 packed-ptype      -> sampleRadarPtypePacked()
 *                                                     (u_radarPtypePacked = 1)
 *   C. mrms / reflectivity uint8 + sparse nodata   -> sampleBilinear() with
 *                                                     u_edgeFade = 1
 *
 * Route stubbing follows tests/e2e/viewer-colormap.fixtures.ts (which itself
 * mirrors viewer-first-paint.fixtures.ts): identical stub list, identical
 * capability/manifest/frames/grid-manifest shapes. Only the grid payloads,
 * packing and palette metadata differ per case.
 *
 * ── Fidelity notes (what is copied from repo evidence vs. synthesized) ─────
 * Copied verbatim from the backend so the manifests are faithful:
 *   - packing per (model, var) from backend/app/services/grid.py
 *     _PACKING_BY_MODEL_VAR:
 *       ("hrrr","radar_ptype")   scale 1.0  offset 0.0   nodata 65535 dBZ
 *       ("mrms","reflectivity")  uint8, scale 0.5 offset -10.0 nodata 255 dBZ
 *   - LOD ladders from _GRID_LOD_CONFIG_BY_MODEL_VAR: both hrrr/radar_ptype
 *     and mrms/reflectivity publish 3 LODs
 *       (level 0 min_zoom 5.5 | level 1 4.0..5.5 | level 2 max_zoom 4.0).
 *     GFS has no entry there, so its manifest carries a single level-0 LOD.
 *   - display_prep from backend/app/services/grid_display_prep.py:
 *       hrrr/radar_ptype -> id hrrr_radar_ptype_display_v3, upscale 1,
 *                           render_categorical_nearest False (so NO
 *                           categorical_nearest key in the manifest)
 *       mrms/reflectivity -> id mrms_reflectivity_display_v2, smooth_sigma
 *                           0.45, support_min_value -35, edge_fade true,
 *                           edge_fill_value 0.0
 *   - palette flags derived exactly as backend/app/services/grid.py does from
 *     the colormap spec: radar_ptype type "indexed" + transparent_zero true;
 *     mrms_reflectivity type "discrete" + transparent_below_min -> first
 *     level (5.0).
 *   - radar_ptype ptype_order/ptype_breaks: rain 0/64, snow 64/66,
 *     sleet 130/66, frzr 196/66 (262 packed bins), matching
 *     colormaps.py _build_radar_ptype_flat_palette() and the frontend's
 *     DEFAULT_RADAR_PTYPE_BREAKS.
 *   - mrms reflectivity legend colors: reproduced EXACTLY by porting
 *     colormaps._expand_color_anchors over the real MRMS anchor list
 *     (levels 5..80 dBZ, 76 stops).
 *
 * Synthesized on purpose:
 *   - The 262 radar_ptype ramp COLORS. The production ramps are four expanded
 *     reflectivity palettes (~200 lines of hex) that would silently drift from
 *     the backend. Since buildLegendLut() treats kind "indexed" as a pure
 *     index -> color table, the packed shader math is exercised identically by
 *     any 262-entry table; four clearly separated synthetic hue ramps make a
 *     v-coordinate shift MORE visible, not less. The bin STRUCTURE (counts,
 *     offsets, order) is the production structure.
 *   - The grid VALUES in every case (see structuredField): a diagonal ramp
 *     plus a row-locked 6-cycle sine plus three sharp cones. The sine ties
 *     brightness to the row index, so any v-coordinate remapping error shifts
 *     rows and blows far past the pixel-diff thresholds.
 */
import type { Page } from '@playwright/test';

import { approvedLegendStops } from './viewer-colormap.fixtures';

// ── Fixed view ────────────────────────────────────────────────────────────
// Pinned viewport + camera. Zoom 6 is >= the level-0 min_zoom (5.5) of the
// hrrr/mrms LOD ladders, so selectGridManifestLod() deterministically picks
// level 0 for every case.
export const GOLDEN_VIEWPORT = { width: 1000, height: 700 };
export const GOLDEN_VIEW = { lat: 39.83, lon: -98.58, zoom: 6 };

/**
 * Web-mercator bbox around the CONUS default center, sized to overhang the
 * pinned viewport at zoom 6 (~1.88 km/px -> ~1.88e6 x 1.32e6 m visible) so no
 * case renders a grid edge inside the frame. Square cells: 2.2e6 / 1.6e6 =
 * 88 / 64.
 */
export const GOLDEN_BBOX = [-12073876, 4042782, -9873876, 5642782];
export const GOLDEN_GRID_WIDTH = 88;
export const GOLDEN_GRID_HEIGHT = 64;

/** Frozen so nothing derived from it (overlay text, cache keys) drifts. */
const FIXED_LAST_UPDATED = '2026-07-29T12:00:00.000Z';

/** 1x1 fully transparent PNG, used to serve a deterministic empty basemap. */
const TRANSPARENT_PNG_1X1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII=',
  'base64',
);

// ── Deterministic value field ─────────────────────────────────────────────

type Disc = { u: number; v: number; r: number; amp: number };

/** Three sharp cones: high-contrast features with hard gradients at the rim. */
const DISCS: Disc[] = [
  { u: 0.22, v: 0.30, r: 0.13, amp: 0.35 },
  { u: 0.62, v: 0.58, r: 0.10, amp: -0.30 },
  { u: 0.82, v: 0.22, r: 0.08, amp: 0.28 },
];

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

/**
 * Normalized [0,1] field at grid cell (col,row). Pure function of the indices
 * and `phase` — no Math.random, no Date, so every run and every LOD level
 * reproduces bit-identical bytes.
 *
 * The 0.14*sin(v * 6pi) term is the v-shift detector: it makes luminance a
 * steep function of ROW, so remapping rows by even one texel changes a large
 * fraction of pixels.
 */
export function structuredField(
  col: number,
  row: number,
  width: number,
  height: number,
  phase: number,
): number {
  const u = width <= 1 ? 0 : col / (width - 1);
  const v = height <= 1 ? 0 : row / (height - 1);
  let t = 0.35 * u + 0.45 * (1 - v);
  t += 0.14 * Math.sin(v * Math.PI * 6 + phase);
  t += 0.06 * Math.cos(u * Math.PI * 4 - phase);
  for (const disc of DISCS) {
    const distance = Math.hypot(u - disc.u, v - disc.v);
    if (distance < disc.r) {
      t += disc.amp * (1 - distance / disc.r);
    }
  }
  return clamp01(t);
}

/**
 * Packed-index code region: two full rain/snow/sleet/frzr cycles across the
 * bbox, sheared by row. All four packed sub-ramps are therefore visible in the
 * frame, and every one of the ~8 ptype boundaries is a diagonal whose position
 * depends on the row — so a v remapping moves each boundary, which the packed
 * branch's bilinear ptype vote turns into a wide band of changed pixels.
 */
function ptypeCodeAt(u: number, v: number): number {
  const s = (u * 2 + 0.45 * (1 - v)) % 1;
  return Math.max(0, Math.min(3, Math.floor(s * 4)));
}

function encodeUint16(values: Array<number | null>, scale: number, offset: number, nodata: number) {
  const out = new Uint16Array(values.length);
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (value === null) {
      out[index] = nodata;
      continue;
    }
    out[index] = Math.max(0, Math.min(nodata - 1, Math.round((value - offset) / scale)));
  }
  return out;
}

function encodeUint8(values: Array<number | null>, scale: number, offset: number, nodata: number) {
  const out = new Uint8Array(values.length);
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (value === null) {
      out[index] = nodata;
      continue;
    }
    out[index] = Math.max(0, Math.min(nodata - 1, Math.round((value - offset) / scale)));
  }
  return out;
}

// ── Palette helpers ───────────────────────────────────────────────────────

const hexToRgb = (hex: string): [number, number, number] => {
  const h = hex.replace('#', '');
  return [0, 2, 4].map((i) => Number.parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
};

const rgbToHex = (rgb: [number, number, number]) =>
  `#${rgb.map((c) => Math.max(0, Math.min(255, Math.round(c))).toString(16).padStart(2, '0')).join('')}`;

/** Port of backend colormaps._expand_color_anchors (np.interp per channel). */
function expandColorAnchors(levels: number[], anchors: Array<[number, string]>): string[] {
  const ordered = [...anchors].sort((a, b) => a[0] - b[0]);
  const xs = ordered.map(([value]) => value);
  const cs = ordered.map(([, color]) => hexToRgb(color));
  const interp = (x: number, channel: number) => {
    if (x <= xs[0]) return cs[0][channel];
    if (x >= xs[xs.length - 1]) return cs[cs.length - 1][channel];
    let i = 0;
    while (i < xs.length - 1 && x > xs[i + 1]) i += 1;
    const span = xs[i + 1] - xs[i];
    const t = span <= 0 ? 0 : (x - xs[i]) / span;
    return cs[i][channel] + (cs[i + 1][channel] - cs[i][channel]) * t;
  };
  return levels.map((level) => rgbToHex([interp(level, 0), interp(level, 1), interp(level, 2)]));
}

// ── Case A: gfs / tmp2m (uint16 continuous) ───────────────────────────────

export const GFS_MODEL = 'gfs';
export const GFS_VARIABLE = 'tmp2m';
export const GFS_RUN_ID = '20260729_12z';
export const GFS_FRAME_HOURS = [0, 1, 2];
/** Phase per forecast hour: FH1/FH2 are visibly different frames (needed by
 *  the GIF case) but still fully deterministic. */
const GFS_PHASE_BY_FH: Record<number, number> = { 0: 0, 1: 0.7, 2: 1.4 };
const GFS_MIN_F = 20;
const GFS_MAX_F = 110;

export function gfsFrameBytes(fh: number, width = GOLDEN_GRID_WIDTH, height = GOLDEN_GRID_HEIGHT): Buffer {
  const phase = GFS_PHASE_BY_FH[fh] ?? 0;
  const values: number[] = [];
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      const t = structuredField(col, row, width, height, phase);
      values.push(GFS_MIN_F + t * (GFS_MAX_F - GFS_MIN_F));
    }
  }
  return Buffer.from(encodeUint16(values, 0.1, -100, 65535).buffer);
}

// ── Case B: hrrr / radar_ptype (uint16 packed ptype) ──────────────────────

export const HRRR_MODEL = 'hrrr';
export const HRRR_VARIABLE = 'radar_ptype';
export const HRRR_RUN_ID = '20260729_12z';

/** Production packed layout (colormaps._build_radar_ptype_flat_palette). */
export const RADAR_PTYPE_ORDER = ['rain', 'snow', 'sleet', 'frzr'];
export const RADAR_PTYPE_BREAKS: Record<string, { offset: number; count: number }> = {
  rain: { offset: 0, count: 64 },
  snow: { offset: 64, count: 66 },
  sleet: { offset: 130, count: 66 },
  frzr: { offset: 196, count: 66 },
};
export const RADAR_PTYPE_TOTAL_BINS = 262;

/**
 * Synthetic 262-entry packed ramp (see header "Synthesized on purpose").
 * One saturated hue per ptype, dark -> light within the ramp, so each of the
 * four packed sub-ranges is unmistakable in the golden image.
 */
function radarPtypeRampColors(): string[] {
  const hueByCode: Array<[number, number, number]> = [
    [0, 200, 60], // rain   green
    [60, 140, 255], // snow   blue
    [200, 90, 255], // sleet  violet
    [255, 90, 120], // frzr   red-pink
  ];
  const colors: string[] = new Array(RADAR_PTYPE_TOTAL_BINS).fill('#000000');
  RADAR_PTYPE_ORDER.forEach((key, code) => {
    const { offset, count } = RADAR_PTYPE_BREAKS[key];
    const base = hueByCode[code];
    for (let index = 0; index < count; index += 1) {
      const t = count <= 1 ? 0 : index / (count - 1);
      // 0.25 -> 1.0 brightness inside the ramp.
      const k = 0.25 + 0.75 * t;
      colors[offset + index] = rgbToHex([base[0] * k, base[1] * k, base[2] * k]);
    }
  });
  return colors;
}

const RADAR_PTYPE_COLORS = radarPtypeRampColors();

/** Flat legend stops in PACKED order — the shape the real sidecar's
 *  legend.stops carries for radar_ptype (pipeline._build_legend ptype path).
 *  Value = local bin index within the ptype (the production stops use that
 *  ptype's dBZ levels; the LUT ignores values for kind "indexed"). */
export function radarPtypeLegendStops(): Array<[number, string]> {
  const stops: Array<[number, string]> = [];
  for (const key of RADAR_PTYPE_ORDER) {
    const { offset, count } = RADAR_PTYPE_BREAKS[key];
    for (let index = 0; index < count; index += 1) {
      stops.push([index, RADAR_PTYPE_COLORS[offset + index]]);
    }
  }
  return stops;
}

export function hrrrPtypeFrameBytes(width = GOLDEN_GRID_WIDTH, height = GOLDEN_GRID_HEIGHT): Buffer {
  const values: Array<number | null> = [];
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      const t = structuredField(col, row, width, height, 0);
      // Sparse holes: exercises the packed branch's selectedWeight == 0 early
      // out and the bilinear ptype-vote at echo boundaries.
      if (t < 0.18) {
        values.push(null);
        continue;
      }
      const u = width <= 1 ? 0 : col / (width - 1);
      const v = height <= 1 ? 0 : row / (height - 1);
      const code = ptypeCodeAt(u, v);
      const key = RADAR_PTYPE_ORDER[code];
      const { offset, count } = RADAR_PTYPE_BREAKS[key];
      // localBin >= 12 keeps intensityAlpha = smoothstep(0,10,localBin)
      // saturated, so alpha is driven by geometry, not by the ramp floor.
      const localBin = Math.round(12 + t * (count - 1 - 12));
      values.push(offset + localBin);
    }
  }
  // scale 1.0 / offset 0.0: the stored uint16 IS the packed index.
  return Buffer.from(encodeUint16(values, 1.0, 0.0, 65535).buffer);
}

// ── Case C: mrms / reflectivity (uint8 sparse + edge fade) ────────────────

export const MRMS_MODEL = 'mrms';
export const MRMS_VARIABLE = 'reflectivity';
export const MRMS_RUN_ID = '20260729_1200z';

const MRMS_REFLECTIVITY_LEVELS = Array.from({ length: 76 }, (_, index) => 5 + index);
/** Real anchors from colormaps._build_mrms_reflectivity_palette(). */
const MRMS_REFLECTIVITY_ANCHORS: Array<[number, string]> = [
  [5.0, '#04e9e7'],
  [10.0, '#019ff4'],
  [15.0, '#0300f4'],
  [20.0, '#02fd02'],
  [25.0, '#01c501'],
  [30.0, '#008e00'],
  [35.0, '#fdf802'],
  [40.0, '#e5bc00'],
  [45.0, '#fd9500'],
  [50.0, '#fd0000'],
  [55.0, '#d40000'],
  [60.0, '#bc0000'],
  [65.0, '#f800fd'],
  [70.0, '#9854c6'],
  [75.0, '#fdfdfd'],
  [80.0, '#fdfdfd'],
];
const MRMS_REFLECTIVITY_COLORS = expandColorAnchors(
  MRMS_REFLECTIVITY_LEVELS,
  MRMS_REFLECTIVITY_ANCHORS,
);

export function mrmsReflectivityLegendStops(): Array<[number, string]> {
  return MRMS_REFLECTIVITY_LEVELS.map((level, index) => [level, MRMS_REFLECTIVITY_COLORS[index]]);
}

/** Echo threshold: everything below is nodata, which is what makes the frame
 *  sparse (~55% nodata) and gives the edge-fade branch real boundaries. */
const MRMS_ECHO_THRESHOLD = 0.45;

export function mrmsFrameBytes(width = GOLDEN_GRID_WIDTH, height = GOLDEN_GRID_HEIGHT): Buffer {
  const values: Array<number | null> = [];
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      const t = structuredField(col, row, width, height, 0);
      if (t < MRMS_ECHO_THRESHOLD) {
        values.push(null);
        continue;
      }
      const echo = (t - MRMS_ECHO_THRESHOLD) / (1 - MRMS_ECHO_THRESHOLD);
      values.push(5 + echo * (80 - 5));
    }
  }
  // uint8, scale 0.5, offset -10.0, nodata 255.
  return Buffer.from(encodeUint8(values, 0.5, -10.0, 255).buffer);
}

// ── Case D: gfs global / tmp2m (native EPSG:4326, full-360 seam) ──────────

/**
 * Phase 3 antimeridian case. The grid is the real global-domain contract grid
 * (docs/GLOBAL_DOMAIN_4326_CONTRACT.md §1): point-registered 0.25°, 1440x721,
 * cell-edge bbox (-180.125, -90.125, 179.875, 90.125), column 0 centred on
 * lon -180 and NO duplicate wrap column. That geometry is the whole point —
 * the renderer has to inset the quad's texcoords by the half cell and wrap
 * column 1439 -> 0 to draw it seamlessly.
 */
export const GLOBAL_SEAM_MODEL = 'gfs';
export const GLOBAL_SEAM_VARIABLE = 'tmp2m';
export const GLOBAL_SEAM_RUN_ID = '20260729_12z';
export const GLOBAL_SEAM_BBOX = [-180.125, -90.125, 179.875, 90.125];
export const GLOBAL_SEAM_GRID_WIDTH = 1440;
export const GLOBAL_SEAM_GRID_HEIGHT = 721;
/**
 * Camera pinned ON the antimeridian, asking for the widest view the region
 * allows (regionMinZoom 0). MapLibre's own constraint then settles it where the
 * mercator world exactly fills the viewport HEIGHT — measured 700 px per world
 * against the 928 px map pane, i.e. ~1.33 worlds across. That is what the seam
 * case needs and it is fully deterministic, but the exact number is MapLibre's
 * to choose, so the spec MEASURES the world period from the image rather than
 * assuming it (probeSeam). Consequences pinned by the spec: the seam sits on
 * the canvas's vertical centre line, and pixels beyond one world width can only
 * be painted by a second world copy.
 */
export const GLOBAL_SEAM_VIEW = { lat: 0, lon: 180, zoom: 0 };

/** Shortest signed longitude difference, in degrees (periodic on 360). */
function lonDelta(lon: number, center: number): number {
  return ((((lon - center + 180) % 360) + 360) % 360) - 180;
}

/**
 * Value field for the seam case, in °F, as a function of TRUE lon/lat.
 *
 * Every term is genuinely periodic in longitude, so the physical field has no
 * discontinuity at ±180 — any step the golden shows at the seam is the
 * renderer's, not the fixture's. Ingredients:
 *   - 3-cycle longitude sinusoid: smooth, periodic, and shifted by any u error.
 *   - 4-cycle latitude sinusoid: the row-shift detector, same role the
 *     structuredField sine plays for the 3857 cases.
 *   - a narrow warm ridge centred exactly ON lon 180 (the straddling feature:
 *     it lives in columns 1435..1444 mod 1440, i.e. on both sides of the seam)
 *   - a narrow cold trough centred on lon 0, the feature that must appear
 *     identically in two world copies.
 */
function globalSeamValueF(lon: number, lat: number): number {
  const lonRad = (lon * Math.PI) / 180;
  let t = 0.5;
  t += 0.18 * Math.sin(3 * lonRad);
  t += 0.16 * Math.sin((lat * Math.PI) / 22.5);
  t += 0.26 * Math.exp(-((lonDelta(lon, 180) / 11) ** 2));
  t -= 0.22 * Math.exp(-((lonDelta(lon, 0) / 11) ** 2));
  return GFS_MIN_F + clamp01(t) * (GFS_MAX_F - GFS_MIN_F);
}

export function globalSeamFrameBytes(
  width = GLOBAL_SEAM_GRID_WIDTH,
  height = GLOBAL_SEAM_GRID_HEIGHT,
): Buffer {
  // Contract §1 centre formulas, derived from the grid dims so a downscaled
  // LOD would stay on the same physical field.
  const lonStep = 360 / width;
  const latStep = 180 / (height - 1);
  const values: number[] = new Array(width * height);
  for (let row = 0; row < height; row += 1) {
    const lat = 90 - latStep * row;
    for (let col = 0; col < width; col += 1) {
      values[row * width + col] = globalSeamValueF(-180 + lonStep * col, lat);
    }
  }
  return Buffer.from(encodeUint16(values, 0.1, -100, 65535).buffer);
}

// ── Case E: gfs global / tmp2m ZOOMED onto the seam ───────────────────────

/**
 * The zoom-0 seam case above pins the world-copy loop, but it CANNOT pin the
 * wrapping bilinear: at that camera one world renders into ~700 device px
 * against 1440 texture columns (2.06 texels per pixel), so no fragment's uv.x
 * ever leaves [0, 1], `fract` is pure identity and the 1439<->0 blend never
 * executes. The half-cell geometry inset is likewise 0.24 px there.
 *
 * This case moves the camera to zoom 5, where one world is 512 * 2^5 = 16384
 * device px — 11.38 px per 0.25 degree column. The blend band (the column pair
 * 1439|0, i.e. lon 179.75 .. 180) is then ~11 px wide and the fragments inside
 * it carry uv.x up to 1 + 0.5/1440, which only the wrap can resolve.
 */
export const GLOBAL_SEAM_ZOOM_VIEW = { lat: 0, lon: 180, zoom: 5 };
/** Longitude degrees per grid column, and the sine period used below. */
const SEAM_ZOOM_COLUMN_DEG = 360 / GLOBAL_SEAM_GRID_WIDTH;
const SEAM_ZOOM_SINE_PERIOD_DEG = 4;

/**
 * Value field for the zoomed seam case, in °F.
 *
 * A pure longitude sine of period 4° (16 columns), phased so that lon 180 —
 * the seam column's own centre — is a ZERO CROSSING, i.e. the point of maximum
 * gradient. That phasing is what makes the case discriminating:
 *
 *   col 1439 (lon 179.75) = 0.5 - 0.38*sin(22.5°) = 0.3546
 *   col 0    (lon 180.00) = 0.5
 *   col 1    (lon -179.75)= 0.5 + 0.38*sin(22.5°) = 0.6454
 *
 * so the cross-seam column step (0.1454 normalized ≈ 13.1 °F) is the LARGEST
 * adjacent-column step anywhere in the grid, and it is reproduced at every
 * other zero crossing too — 2° is exactly 8 columns, so every crossing lands on
 * a column centre. A correct wrapping bilinear therefore renders the seam with
 * the same per-pixel gradient as the rest of the frame; a clamped one collapses
 * the whole 11 px west band to the constant col-1439 value and dumps the entire
 * 13 °F change into a single pixel at the seam. The spec asserts on exactly
 * that difference.
 *
 * The latitude term is deliberately gentle (0.0002 normalized per pixel at this
 * zoom) so it cannot pollute the horizontal-step measurements, while still
 * shifting a large fraction of the frame if v is ever remapped.
 */
function globalSeamZoomValueF(lon: number, lat: number): number {
  let t = 0.5;
  t += 0.38 * Math.sin((2 * Math.PI * (lon - 180)) / SEAM_ZOOM_SINE_PERIOD_DEG);
  t += 0.08 * Math.sin((lat * Math.PI) / 20);
  return GFS_MIN_F + clamp01(t) * (GFS_MAX_F - GFS_MIN_F);
}

export function globalSeamZoomFrameBytes(
  width = GLOBAL_SEAM_GRID_WIDTH,
  height = GLOBAL_SEAM_GRID_HEIGHT,
): Buffer {
  const lonStep = 360 / width;
  const latStep = 180 / (height - 1);
  const values: number[] = new Array(width * height);
  for (let row = 0; row < height; row += 1) {
    const lat = 90 - latStep * row;
    for (let col = 0; col < width; col += 1) {
      values[row * width + col] = globalSeamZoomValueF(-180 + lonStep * col, lat);
    }
  }
  return Buffer.from(encodeUint16(values, 0.1, -100, 65535).buffer);
}

/**
 * Device pixels per grid column at GLOBAL_SEAM_ZOOM_VIEW: one world is
 * 512 * 2^zoom px wide (MapLibre tileSize 512, dpr 1 under Playwright) shared
 * across GLOBAL_SEAM_GRID_WIDTH columns. The spec uses this to place its probes
 * on the column pair that straddles the seam, and asserts the measured seam
 * position independently.
 */
export const GLOBAL_SEAM_ZOOM_COLUMN_PX =
  (512 * 2 ** GLOBAL_SEAM_ZOOM_VIEW.zoom) / GLOBAL_SEAM_GRID_WIDTH;

// ── Shared manifest / capability builders ─────────────────────────────────

// ── Cases F-I: the same fixtures under MapLibre's globe projection ────────
//
// Globe (Phase G1) is a RENDERER mode, not a data selection: these cases reuse
// the 4326 global fixture and the 3857 CONUS fixture byte-for-byte and change
// only the camera and the `globe=1` boot flag. They live in
// render-golden-globe.spec.ts; GOLDEN_CASE_IDS below is deliberately left
// untouched so the mercator goldens keep their exact identities.
//
// Camera notes (docs/GLOBE_OVERLAY_AUDIT_2026-08-01.md "one geometry fact"):
// under globe projection the disc radius is worldSize/2pi, so at zoom 1 the
// globe is ~326 px across a 1000 px canvas (limb visible all round) and by
// zoom 4 the disc exceeds the canvas. The world/seam/pole cases sit at zoom 1
// on purpose — the limb is part of what they pin.

/** Whole-globe view of the 4326 global field, camera off the seam. */
export const GLOBE_WORLD_VIEW = { lat: 20, lon: -40, zoom: 1 };
/** Camera exactly on the antimeridian: the seam runs down the disc centre. */
export const GLOBE_SEAM_VIEW = { lat: 0, lon: 180, zoom: 1 };
/**
 * Polar view. Centre latitude is deliberately 70, not 90: at lat 70 / zoom 1
 * the north polar cap sits inside the disc with the limb still visible, which
 * is what this golden pins -- the +90 deg apex fan's TOPOLOGY, not the camera.
 *
 * It was originally 70 because it had to be: MapLibre hard-clamps centre
 * latitude to the Web Mercator limit (85.051) even under globe, and an
 * explicitly supplied zoom is not latitude-adjusted, so approaching the clamp
 * magnified ~11x and pushed the disc off screen (audit item 10c). Phase G2
 * lifts that clamp (globe-map.ts, `constrainGlobeCamera`) and pins the polar
 * camera in globe-view.spec.ts; this golden stays at 70 so its identity is
 * unchanged across the two phases.
 */
export const GLOBE_POLE_VIEW = { lat: 70, lon: 0, zoom: 1 };
/** Regional EPSG:3857 footprint on the sphere, limb visible. */
export const GLOBE_REGIONAL_VIEW = { lat: 39.83, lon: -98.58, zoom: 2 };

export type GoldenCaseId =
  | 'gfs-tmp2m'
  | 'hrrr-radar-ptype'
  | 'mrms-reflectivity'
  | 'gfs-global-seam'
  | 'gfs-global-seam-zoom'
  | 'globe-4326-world'
  | 'globe-4326-seam'
  | 'globe-4326-pole'
  | 'globe-3857-regional';

type CaseSpec = {
  id: GoldenCaseId;
  model: string;
  variable: string;
  runId: string;
  frameHours: number[];
  displayName: string;
  units: string;
  /** Sidecar/legend kind. */
  kind: string;
  colorMapId: string;
  product: 'forecast' | 'observed';
  timeAxisMode: 'forecast' | 'observed';
  defaultFrameSelection: 'first' | 'latest';
  grid: {
    dtype: 'uint8' | 'uint16';
    scale: number;
    offset: number;
    nodata: number;
  };
  palette: Record<string, unknown>;
  displayPrep: Record<string, unknown> | null;
  /** Multi-LOD ladder from _GRID_LOD_CONFIG_BY_MODEL_VAR, or null for a
   *  single level-0 LOD. */
  lodLadder: Array<{ level: number; scaleFactor: number; minZoom?: number; maxZoom?: number }> | null;
  legendStops: () => Array<[number, string]>;
  /** radar_ptype only. */
  ptype?: { order: string[]; breaks: Record<string, { offset: number; count: number }> };
  frameBytes: (fh: number, width: number, height: number) => Buffer;
  fileSuffix: string;
  validTime: (fh: number) => string;
  /** Grid CRS. Absent = the legacy EPSG:3857 default (contract §4). */
  projection?: string;
  /** Grid extent + dims. Absent = the CONUS-sized 3857 defaults above. */
  bbox?: number[];
  gridWidth?: number;
  gridHeight?: number;
  /** Camera in the viewer URL. Absent = GOLDEN_VIEW. */
  view?: { lat: number; lon: number; zoom: number };
  /** Region-catalog minZoom. Absent = 2, the value the 3857 cases have always
   *  been served; the seam case needs 0 to fit more than one world copy. */
  regionMinZoom?: number;
  /** Boots the viewer with `?globe=1`, i.e. MapLibre's globe projection. */
  globe?: boolean;
};

const caseBbox = (spec: CaseSpec) => spec.bbox ?? GOLDEN_BBOX;
const caseGridWidth = (spec: CaseSpec) => spec.gridWidth ?? GOLDEN_GRID_WIDTH;
const caseGridHeight = (spec: CaseSpec) => spec.gridHeight ?? GOLDEN_GRID_HEIGHT;

const OBSERVED_LOD_LADDER = [
  { level: 0, scaleFactor: 1, minZoom: 5.5 },
  { level: 1, scaleFactor: 2, minZoom: 4.0, maxZoom: 5.5 },
  { level: 2, scaleFactor: 4, maxZoom: 4.0 },
];

/**
 * Shared fixture bodies for the globe cases: the SAME artifacts the mercator
 * cases use (4326 global 1440x721 with rows at +/-90, and the 3857 CONUS grid),
 * so a globe golden can only differ from its mercator sibling by projection.
 */
const GOLDEN_CASES_BASE_GLOBAL: Omit<CaseSpec, 'id'> = {
  model: GLOBAL_SEAM_MODEL,
  variable: GLOBAL_SEAM_VARIABLE,
  runId: GLOBAL_SEAM_RUN_ID,
  frameHours: [0],
  displayName: 'Temperature 2m',
  units: 'F',
  kind: 'continuous',
  colorMapId: 'tmp2m',
  product: 'forecast',
  timeAxisMode: 'forecast',
  defaultFrameSelection: 'first',
  grid: { dtype: 'uint16', scale: 0.1, offset: -100.0, nodata: 65535 },
  palette: {
    color_map_id: 'tmp2m',
    kind: 'continuous',
    transparent_below_min: null,
    transparent_zero: false,
  },
  displayPrep: null,
  lodLadder: null,
  legendStops: approvedLegendStops,
  frameBytes: (_fh, width, height) => globalSeamFrameBytes(width, height),
  fileSuffix: 'u16',
  validTime: () => '2026-07-29T12:00:00Z',
  projection: 'EPSG:4326',
  bbox: GLOBAL_SEAM_BBOX,
  gridWidth: GLOBAL_SEAM_GRID_WIDTH,
  gridHeight: GLOBAL_SEAM_GRID_HEIGHT,
};

const GOLDEN_CASES_BASE_REGIONAL: Omit<CaseSpec, 'id'> = {
  model: GFS_MODEL,
  variable: GFS_VARIABLE,
  runId: GFS_RUN_ID,
  frameHours: GFS_FRAME_HOURS,
  displayName: 'Temperature 2m',
  units: 'F',
  kind: 'continuous',
  colorMapId: 'tmp2m',
  product: 'forecast',
  timeAxisMode: 'forecast',
  defaultFrameSelection: 'first',
  grid: { dtype: 'uint16', scale: 0.1, offset: -100.0, nodata: 65535 },
  palette: {
    color_map_id: 'tmp2m',
    kind: 'continuous',
    transparent_below_min: null,
    transparent_zero: false,
  },
  displayPrep: null,
  lodLadder: null,
  legendStops: approvedLegendStops,
  frameBytes: (fh, width, height) => gfsFrameBytes(fh, width, height),
  fileSuffix: 'u16',
  validTime: (fh) => `2026-07-29T${String(12 + fh).padStart(2, '0')}:00:00Z`,
};

const GOLDEN_CASES: Record<GoldenCaseId, CaseSpec> = {
  'gfs-tmp2m': {
    id: 'gfs-tmp2m',
    model: GFS_MODEL,
    variable: GFS_VARIABLE,
    runId: GFS_RUN_ID,
    frameHours: GFS_FRAME_HOURS,
    displayName: 'Temperature 2m',
    units: 'F',
    kind: 'continuous',
    colorMapId: 'tmp2m',
    product: 'forecast',
    timeAxisMode: 'forecast',
    defaultFrameSelection: 'first',
    grid: { dtype: 'uint16', scale: 0.1, offset: -100.0, nodata: 65535 },
    palette: {
      color_map_id: 'tmp2m',
      kind: 'continuous',
      transparent_below_min: null,
      transparent_zero: false,
    },
    displayPrep: null,
    lodLadder: null,
    legendStops: approvedLegendStops,
    frameBytes: (fh, width, height) => gfsFrameBytes(fh, width, height),
    fileSuffix: 'u16',
    validTime: (fh) => `2026-07-29T${String(12 + fh).padStart(2, '0')}:00:00Z`,
  },
  'hrrr-radar-ptype': {
    id: 'hrrr-radar-ptype',
    model: HRRR_MODEL,
    variable: HRRR_VARIABLE,
    runId: HRRR_RUN_ID,
    frameHours: [0],
    displayName: 'Composite Reflectivity + P-Type',
    units: 'dBZ',
    kind: 'indexed',
    colorMapId: 'radar_ptype',
    product: 'forecast',
    timeAxisMode: 'forecast',
    defaultFrameSelection: 'first',
    grid: { dtype: 'uint16', scale: 1.0, offset: 0.0, nodata: 65535 },
    palette: {
      color_map_id: 'radar_ptype',
      kind: 'indexed',
      transparent_zero: true,
      ptype_order: RADAR_PTYPE_ORDER,
      ptype_breaks: RADAR_PTYPE_BREAKS,
    },
    // grid_display_prep: upscale 1, no smoothing, render_categorical_nearest
    // False -> the categorical_nearest key is intentionally absent.
    displayPrep: {
      id: 'hrrr_radar_ptype_display_v3',
      upscale_factor: 1,
      smooth_sigma: 0.0,
    },
    lodLadder: OBSERVED_LOD_LADDER,
    legendStops: radarPtypeLegendStops,
    ptype: { order: RADAR_PTYPE_ORDER, breaks: RADAR_PTYPE_BREAKS },
    frameBytes: (_fh, width, height) => hrrrPtypeFrameBytes(width, height),
    fileSuffix: 'u16',
    validTime: (fh) => `2026-07-29T${String(12 + fh).padStart(2, '0')}:00:00Z`,
  },
  'mrms-reflectivity': {
    id: 'mrms-reflectivity',
    model: MRMS_MODEL,
    variable: MRMS_VARIABLE,
    runId: MRMS_RUN_ID,
    frameHours: [0],
    displayName: 'Base Reflectivity',
    units: 'dBZ',
    kind: 'discrete',
    colorMapId: 'mrms_reflectivity',
    product: 'observed',
    timeAxisMode: 'observed',
    defaultFrameSelection: 'latest',
    grid: { dtype: 'uint8', scale: 0.5, offset: -10.0, nodata: 255 },
    palette: {
      color_map_id: 'mrms_reflectivity',
      kind: 'discrete',
      // spec transparent_below_min: True + discrete -> first level.
      transparent_below_min: 5.0,
      transparent_zero: false,
    },
    displayPrep: {
      id: 'mrms_reflectivity_display_v2',
      upscale_factor: 1,
      smooth_sigma: 0.45,
      support_min_value: -35.0,
      edge_fade: true,
      edge_fill_value: 0.0,
    },
    lodLadder: OBSERVED_LOD_LADDER,
    legendStops: mrmsReflectivityLegendStops,
    frameBytes: (_fh, width, height) => mrmsFrameBytes(width, height),
    fileSuffix: 'u8',
    validTime: () => '2026-07-29T12:00:00Z',
  },
  'gfs-global-seam': {
    id: 'gfs-global-seam',
    model: GLOBAL_SEAM_MODEL,
    variable: GLOBAL_SEAM_VARIABLE,
    runId: GLOBAL_SEAM_RUN_ID,
    frameHours: [0],
    displayName: 'Temperature 2m',
    units: 'F',
    kind: 'continuous',
    colorMapId: 'tmp2m',
    product: 'forecast',
    timeAxisMode: 'forecast',
    defaultFrameSelection: 'first',
    grid: { dtype: 'uint16', scale: 0.1, offset: -100.0, nodata: 65535 },
    palette: {
      color_map_id: 'tmp2m',
      kind: 'continuous',
      transparent_below_min: null,
      transparent_zero: false,
    },
    displayPrep: null,
    lodLadder: null,
    legendStops: approvedLegendStops,
    frameBytes: (_fh, width, height) => globalSeamFrameBytes(width, height),
    fileSuffix: 'u16',
    validTime: () => '2026-07-29T12:00:00Z',
    projection: 'EPSG:4326',
    bbox: GLOBAL_SEAM_BBOX,
    gridWidth: GLOBAL_SEAM_GRID_WIDTH,
    gridHeight: GLOBAL_SEAM_GRID_HEIGHT,
    view: GLOBAL_SEAM_VIEW,
    regionMinZoom: 0,
  },
  'gfs-global-seam-zoom': {
    id: 'gfs-global-seam-zoom',
    model: GLOBAL_SEAM_MODEL,
    variable: GLOBAL_SEAM_VARIABLE,
    runId: GLOBAL_SEAM_RUN_ID,
    frameHours: [0],
    displayName: 'Temperature 2m',
    units: 'F',
    kind: 'continuous',
    colorMapId: 'tmp2m',
    product: 'forecast',
    timeAxisMode: 'forecast',
    defaultFrameSelection: 'first',
    grid: { dtype: 'uint16', scale: 0.1, offset: -100.0, nodata: 65535 },
    palette: {
      color_map_id: 'tmp2m',
      kind: 'continuous',
      transparent_below_min: null,
      transparent_zero: false,
    },
    displayPrep: null,
    lodLadder: null,
    legendStops: approvedLegendStops,
    frameBytes: (_fh, width, height) => globalSeamZoomFrameBytes(width, height),
    fileSuffix: 'u16',
    validTime: () => '2026-07-29T12:00:00Z',
    projection: 'EPSG:4326',
    bbox: GLOBAL_SEAM_BBOX,
    gridWidth: GLOBAL_SEAM_GRID_WIDTH,
    gridHeight: GLOBAL_SEAM_GRID_HEIGHT,
    view: GLOBAL_SEAM_ZOOM_VIEW,
  },
  // ── Globe renderer cases (Phase G1) ────────────────────────────────────
  // Each is an existing fixture verbatim + a globe camera. `regionMinZoom: 0`
  // because the region catalog's floor is applied to the map and these cameras
  // sit at zoom 1-2.
  'globe-4326-world': {
    ...GOLDEN_CASES_BASE_GLOBAL,
    id: 'globe-4326-world',
    view: GLOBE_WORLD_VIEW,
    regionMinZoom: 0,
    globe: true,
  },
  'globe-4326-seam': {
    ...GOLDEN_CASES_BASE_GLOBAL,
    id: 'globe-4326-seam',
    view: GLOBE_SEAM_VIEW,
    regionMinZoom: 0,
    globe: true,
  },
  'globe-4326-pole': {
    ...GOLDEN_CASES_BASE_GLOBAL,
    id: 'globe-4326-pole',
    view: GLOBE_POLE_VIEW,
    regionMinZoom: 0,
    globe: true,
  },
  'globe-3857-regional': {
    ...GOLDEN_CASES_BASE_REGIONAL,
    id: 'globe-3857-regional',
    view: GLOBE_REGIONAL_VIEW,
    regionMinZoom: 0,
    globe: true,
  },
};

export function goldenCase(id: GoldenCaseId): CaseSpec {
  return GOLDEN_CASES[id];
}

export const GOLDEN_CASE_IDS: GoldenCaseId[] = [
  'gfs-tmp2m',
  'hrrr-radar-ptype',
  'mrms-reflectivity',
  'gfs-global-seam',
  'gfs-global-seam-zoom',
];

/** Globe renderer cases. Driven by render-golden-globe.spec.ts. */
export const GLOBE_GOLDEN_CASE_IDS: GoldenCaseId[] = [
  'globe-4326-world',
  'globe-4326-seam',
  'globe-4326-pole',
  'globe-3857-regional',
];

export function viewerUrlForCase(id: GoldenCaseId): string {
  const spec = GOLDEN_CASES[id];
  const view = spec.view ?? GOLDEN_VIEW;
  return (
    `/viewer?m=${spec.model}&r=latest&v=${spec.variable}&fh=0&reg=conus` +
    `&lat=${view.lat}&lon=${view.lon}&z=${view.zoom}&screenshot=1` +
    (spec.globe ? '&globe=1' : '')
  );
}

function lodEntriesFor(spec: CaseSpec) {
  const ladder = spec.lodLadder ?? [{ level: 0, scaleFactor: 1 }];
  return ladder.map((entry) => {
    const width = Math.max(1, Math.round(caseGridWidth(spec) / entry.scaleFactor));
    const height = Math.max(1, Math.round(caseGridHeight(spec) / entry.scaleFactor));
    return {
      level: entry.level,
      width,
      height,
      ...(entry.minZoom === undefined ? {} : { min_zoom: entry.minZoom }),
      ...(entry.maxZoom === undefined ? {} : { max_zoom: entry.maxZoom }),
      frames: spec.frameHours.map((fh) => ({
        fh,
        file: `fh${String(fh).padStart(3, '0')}.l${entry.level}.${spec.fileSuffix}.bin`,
        valid_time: spec.validTime(fh),
        url:
          `/api/v4/grid/${spec.model}/${spec.runId}/${spec.variable}/` +
          `fh${String(fh).padStart(3, '0')}.l${entry.level}.${spec.fileSuffix}.bin` +
          `?v=${spec.runId}-${spec.variable}-${fh}`,
      })),
    };
  });
}

function gridDimsForLevel(spec: CaseSpec, level: number) {
  const ladder = spec.lodLadder ?? [{ level: 0, scaleFactor: 1 }];
  const entry = ladder.find((candidate) => candidate.level === level) ?? ladder[0];
  return {
    width: Math.max(1, Math.round(caseGridWidth(spec) / entry.scaleFactor)),
    height: Math.max(1, Math.round(caseGridHeight(spec) / entry.scaleFactor)),
  };
}

function capabilityPayload(spec: CaseSpec) {
  return {
    contract_version: 'v1',
    supported_models: [spec.model],
    model_catalog: {
      [spec.model]: {
        model_id: spec.model,
        name: spec.model.toUpperCase(),
        product: spec.product,
        canonical_region: 'conus',
        defaults: {
          default_var_key: spec.variable,
          default_run: 'latest',
          default_frame_selection: spec.defaultFrameSelection,
          default_render_substrate: 'grid',
        },
        constraints: {
          canonical_region: 'conus',
          time_axis_mode: spec.timeAxisMode,
          latest_only: spec.timeAxisMode === 'observed',
          supports_sampling: true,
        },
        run_discovery: {},
        variables: {
          [spec.variable]: {
            var_key: spec.variable,
            display_name: spec.displayName,
            kind: spec.kind,
            units: spec.units,
            group: 'Golden',
            default_fh: 0,
            buildable: true,
            color_map_id: spec.colorMapId,
            render_substrates: ['grid'],
            constraints: {},
            derived: false,
            derive_strategy_id: null,
          },
        },
      },
    },
    availability: {
      [spec.model]: {
        latest_run: spec.runId,
        published_runs: [spec.runId],
        latest_run_ready: true,
        latest_run_ready_vars: [spec.variable],
        latest_run_ready_frame_count: spec.frameHours.length,
        source: 'test',
        time_axis_mode: spec.timeAxisMode,
      },
    },
  };
}

function legendMeta(spec: CaseSpec, fh: number) {
  return {
    valid_time: spec.validTime(fh),
    units: spec.units,
    kind: spec.kind,
    display_name: spec.displayName,
    legend_stops: spec.legendStops(),
    ...(spec.ptype
      ? { ptype_order: spec.ptype.order, ptype_breaks: spec.ptype.breaks }
      : {}),
  };
}

function manifestPayload(spec: CaseSpec) {
  return {
    model: spec.model,
    run: spec.runId,
    region: 'conus',
    last_updated: FIXED_LAST_UPDATED,
    variables: {
      [spec.variable]: {
        display_name: spec.displayName,
        kind: spec.kind,
        units: spec.units,
        frames: spec.frameHours.map((fh) => ({ fh, valid_time: spec.validTime(fh) })),
      },
    },
  };
}

function framesPayload(spec: CaseSpec) {
  return spec.frameHours.map((fh) => ({
    fh,
    has_cog: true,
    run: spec.runId,
    valid_time: spec.validTime(fh),
    meta: { meta: legendMeta(spec, fh) },
  }));
}

function gridManifestPayload(spec: CaseSpec) {
  return {
    manifest_version: 1,
    subtype: 'grid',
    model: spec.model,
    run: spec.runId,
    var: spec.variable,
    projection: spec.projection ?? 'EPSG:3857',
    bbox: caseBbox(spec),
    grid: {
      width: caseGridWidth(spec),
      height: caseGridHeight(spec),
      dtype: spec.grid.dtype,
      endianness: 'little',
      scale: spec.grid.scale,
      offset: spec.grid.offset,
      nodata: spec.grid.nodata,
      units: spec.units,
    },
    palette: spec.palette,
    ...(spec.displayPrep ? { display_prep: spec.displayPrep } : {}),
    lods: lodEntriesFor(spec),
  };
}

/**
 * Same stub list as viewer-colormap.fixtures.stubViewerColormapRoutes /
 * viewer-first-paint.fixtures.stubViewerFirstPaintRoutes: analytics, regions,
 * sampling, boundary tiles, loop manifests, basemap raster tiles, then the
 * per-model capability / manifest / frames / grid-manifest / .bin routes.
 *
 * `binRequests` (optional) collects the served grid-binary URLs so a test can
 * pin the fetch count.
 * `sampleRequests` (optional) collects the hover-sample URLs so a test can pin
 * the longitude the client actually asks for.
 */
export async function stubGoldenBaselineRoutes(
  page: Page,
  id: GoldenCaseId,
  binRequests?: string[],
  sampleRequests?: string[],
) {
  const spec = GOLDEN_CASES[id];

  await page.route('https://us.i.posthog.com/**', async (route) => {
    await route.fulfill({ status: 204, body: '' });
  });
  await page.route('**/api/regions', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        regions: {
          conus: {
            label: 'CONUS',
            bbox: [-125, 24, -66, 50],
            defaultCenter: [-98.58, 39.83],
            defaultZoom: 4,
            // Only the seam case lowers this: map-canvas applies the region's
            // minZoom to the map, and zoom 0 is what fits more than one world
            // copy in the frame. The camera itself always comes from the URL.
            minZoom: spec.regionMinZoom ?? 2,
            maxZoom: 9,
          },
        },
      }),
    });
  });
  await page.route('**/api/v4/sample/batch', async (route) => {
    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'not found' }),
    });
  });
  // Single-point hover sampling. Registered AFTER the batch route (Playwright
  // gives later handlers precedence) and matched on the `?` so it can never
  // swallow /sample/batch. Mirrors the API's own contract: lon outside
  // [-180, 180] is a 422, exactly as the backend Query bound declares it.
  await page.route('**/api/v4/sample?*', async (route) => {
    const url = route.request().url();
    sampleRequests?.push(url);
    const lon = Number(new URL(url).searchParams.get('lon'));
    if (!Number.isFinite(lon) || lon < -180 || lon > 180) {
      await route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'lon out of range' }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ value: 61.5, units: spec.units, label: spec.displayName }),
    });
  });
  // Live NWS active warnings. The MRMS case turns this overlay on
  // (supportsNwsWarningsOverlay), so leaving it unstubbed makes that golden a
  // function of real-world weather at capture time — measured as a 79,672 px
  // (12.3%) cross-run diff, entirely warning polygons drawn over an otherwise
  // pixel-identical grid. Served empty, which is the state the stored goldens
  // were captured in.
  await page.route('**/api/v4/nws-hazards/active/warnings**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/geo+json',
      body: JSON.stringify({ type: 'FeatureCollection', features: [] }),
    });
  });
  await page.route('**/tiles/v3/boundaries/v2/tilejson.json', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tilejson: '2.2.0',
        name: 'Boundaries',
        id: 'boundaries',
        scheme: 'xyz',
        format: 'pbf',
        minzoom: 0,
        maxzoom: 10,
        bounds: [-180, -85.0511, 180, 85.0511],
        center: [-98.58, 39.83, 4],
        tiles: ['https://api.cartosky.com/tiles/v3/boundaries/v2/{z}/{x}/{y}.mvt'],
      }),
    });
  });
  await page.route('**/tiles/v3/boundaries/v2/**/*.mvt', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/vnd.mapbox-vector-tile', body: '' });
  });
  await page.route('**/api/v4/**/loop-manifest', async (route) => {
    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'not found' }),
    });
  });
  await page.route('**/api/v4/**/loop.webp**', async (route) => {
    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'not found' }),
    });
  });
  await page.route('**/tiles/v3/**/*.png**', async (route) => {
    await route.fulfill({ status: 404, body: '' });
  });
  // Extra pin beyond the colormap stub list: the viewer's actual basemap is
  // Carto raster PNG (map-canvas.tsx CARTO_* tile URLs). Left unstubbed the
  // golden would silently depend on live third-party imagery, so it is failed
  // closed here and the goldens contain no basemap raster at all.
  // Extra pin beyond the colormap stub list: the viewer's real basemap is Carto
  // raster PNG (map-canvas.tsx CARTO_* tile URLs). Left unstubbed, the goldens
  // would silently depend on live third-party imagery. Failing them CLOSED
  // (404) is not an option — MapLibre's raster source never settles and the
  // app's grid_frame_ready gate never fires — so each tile is served as a valid
  // 1x1 fully transparent PNG: the source loads and settles normally, and the
  // basemap contributes exactly nothing to the golden.
  await page.route('https://*.basemaps.cartocdn.com/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: TRANSPARENT_PNG_1X1,
    });
  });

  await page.route('**/api/v4/capabilities', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(capabilityPayload(spec)),
    });
  });
  await page.route(`**/api/v4/${spec.model}/runs`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([spec.runId]),
    });
  });
  for (const runSegment of ['latest', spec.runId]) {
    await page.route(`**/api/v4/${spec.model}/${runSegment}/manifest**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(manifestPayload(spec)),
      });
    });
    await page.route(`**/api/v4/${spec.model}/${runSegment}/${spec.variable}/frames**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(framesPayload(spec)),
      });
    });
    await page.route(`**/api/v4/${spec.model}/${runSegment}/${spec.variable}/grid-manifest**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(gridManifestPayload(spec)),
      });
    });
  }
  await page.route(`**/api/v4/grid/${spec.model}/${spec.runId}/${spec.variable}/*.bin**`, async (route) => {
    const url = route.request().url();
    binRequests?.push(url);
    const match = url.match(/fh(\d{3})\.l(\d+)\./);
    const fh = match ? Number(match[1]) : 0;
    const level = match ? Number(match[2]) : 0;
    const dims = gridDimsForLevel(spec, level);
    await route.fulfill({
      status: 200,
      contentType: 'application/octet-stream',
      body: spec.frameBytes(fh, dims.width, dims.height),
    });
  });
}
