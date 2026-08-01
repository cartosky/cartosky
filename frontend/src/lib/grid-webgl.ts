import maplibregl from "maplibre-gl";

import type { LegendPayload } from "@/components/map-legend";
import { GRID_LUT_SIZE, buildLegendLut } from "@/lib/grid-lut";
import { productFetch, type GridManifestResponse } from "@/lib/api";
import type { AnchorBatchPoint } from "@/lib/anchor-labels";
import { SCRUB_LAG_BURST_WARM_LIMIT, SCRUB_LAG_BURST_WARM_LIMIT_MOBILE } from "@/lib/app-utils";
import { sampleGridPoints } from "@/lib/grid-sample";
import {
  GLOBE_MESH_COLS,
  GLOBE_MESH_ROWS,
  GLOBE_VERTEX_SOURCE,
  buildGlobeMesh,
  deriveMatrixSpace,
  globeMeshSignature,
  isProjectionPairingCoherent,
  readFrameProjection,
  readProjectionTransitionState,
  type FrameProjection,
  type GridGeometryMode,
  type ProjectionMatrixSpace,
} from "@/lib/globe-projection";
import { startNetworkTimer, trackClientProcessingDuration, trackNetworkFetchDuration } from "@/lib/network-diagnostics";

export const GRID_WEBGL_LAYER_ID = "twf-grid-webgl";

const GRID_FRAME_CACHE_BUDGET_DESKTOP_BYTES = 768 * 1024 * 1024;
const GRID_FRAME_CACHE_BUDGET_MOBILE_BYTES = 384 * 1024 * 1024;
const GRID_TEXTURE_CACHE_BUDGET_DESKTOP_BYTES = 512 * 1024 * 1024;
const GRID_TEXTURE_CACHE_BUDGET_MOBILE_BYTES = 256 * 1024 * 1024;
const GRID_TEXTURE_WARM_LIMIT = 12;
/** Expanded warm queue for scrub/idle prefetch (forecast models only). */
const GRID_TEXTURE_WARM_LIMIT_SCRUB = 14;
const GRID_TEXTURE_WARM_LIMIT_SCRUB_MOBILE = 12;
const GRID_TEXTURE_WARM_BATCH_SIZE = 3;
const GRID_TEXTURE_WARM_BATCH_SIZE_ANIMATING_DESKTOP = 2;
const GRID_TEXTURE_WARM_BATCH_SIZE_ANIMATING_MOBILE = 1;
const OBSERVED_GRID_TEXTURE_WARM_LIMIT_DESKTOP = 28;
const OBSERVED_GRID_TEXTURE_WARM_LIMIT_MOBILE = 10;
const OBSERVED_GRID_TEXTURE_WARM_BATCH_SIZE_DESKTOP = 4;
const OBSERVED_GRID_TEXTURE_WARM_BATCH_SIZE_MOBILE = 2;
const OBSERVED_GRID_TEXTURE_WARM_BATCH_SIZE_ANIMATING_DESKTOP = 2;
const OBSERVED_GRID_TEXTURE_WARM_BATCH_SIZE_ANIMATING_MOBILE = 1;
const OBSERVED_GRID_TEXTURE_HIGH_PRIORITY_COUNT_DESKTOP = 6;
const OBSERVED_GRID_TEXTURE_HIGH_PRIORITY_COUNT_MOBILE = 4;
const OBSERVED_HIGH_RES_LOD_PIXEL_THRESHOLD = 6_000_000;
const OBSERVED_VERY_HIGH_RES_LOD_PIXEL_THRESHOLD = 10_000_000;
const MERCATOR_HALF_WORLD = 20037508.342789244;
const TRANSPARENT_BELOW_MIN_BY_COLOR_MAP_ID = new Map<string, number>([
  ["precip_total", 0.01],
  ["snowfall_total", 0.1],
  ["pwat", 0.05],
]);
const SUPPORT_COVERAGE_THRESHOLD_DISABLED_COLOR_MAP_IDS = new Set<string>([
  "precip_total",
  "snowfall_total",
]);
// Prune only the first visible accumulated precip/snow bin when bilinear
// sampling is mostly interpolating from transparent neighbours.
const LOW_END_EDGE_CLEANUP_BY_COLOR_MAP_ID = new Map<string, { maxValue: number; supportCoverageThreshold: number }>([
  ["precip_total", { maxValue: 0.05, supportCoverageThreshold: 0.5 }],
  ["snowfall_total", { maxValue: 0.5, supportCoverageThreshold: 0.5 }],
]);
const DEFAULT_RADAR_PTYPE_ORDER = ["rain", "snow", "sleet", "frzr"];
const DEFAULT_RADAR_PTYPE_BREAKS: Record<string, { offset: number; count: number }> = {
  rain: { offset: 0, count: 64 },
  snow: { offset: 64, count: 66 },
  sleet: { offset: 130, count: 66 },
  frzr: { offset: 196, count: 66 },
};

export type GridFrameVisiblePayload = {
  frameHour: number;
  selectionEpoch?: number;
  selectionKey?: string;
};

export type GridRasterPaint = {
  contrast: number;
  saturation: number;
  brightnessMin: number;
  brightnessMax: number;
};

export type GridContourLayerConfig = {
  frameUrl: string | null;
  frameHour: number | null;
  grid: GridManifestResponse["grid"] | null;
  width: number;
  height: number;
  interval: number;
  color?: [number, number, number, number] | null;
  /**
   * Projection of the contour artifact ("EPSG:3857" | "EPSG:4326"). The contour
   * texture is sampled with the SAME quad texcoords as the data texture, so it
   * is already assumed to be co-registered with the data artifact's extent;
   * when omitted, the data manifest's projection and bbox are used.
   */
  projection?: string | null;
  /** Contour artifact bbox, in the units implied by `projection`. */
  bbox?: [number, number, number, number] | null;
};

export type GridWebglLayerConfig = {
  active: boolean;
  manifest: GridManifestResponse | null;
  lodLevel?: number | null;
  frameUrl: string | null;
  frameHour: number | null;
  legend: LegendPayload | null;
  opacity: number;
  overlayFadeOutZoom?: { start: number; end: number } | null;
  selectionEpoch: number;
  selectionKey: string;
  prefetchUrls?: string[];
  contour?: GridContourLayerConfig | null;
  rasterPaint?: GridRasterPaint | null;
  /** When true, the controller deprioritizes background texture warming to
   *  avoid competing with animation playback for main-thread and GPU time. */
  isAnimating?: boolean;
  /** When true, use full warm throughput and an expanded queue for scrub/idle prefetch. */
  isScrubPrefetch?: boolean;
  /** Expand warm queue/batch when scrub target is far ahead of the displayed ready frame. */
  scrubLagBurst?: boolean;
  /** Frame URLs that must not be aborted during fast scrub (recent scrub targets). */
  protectedFetchUrls?: string[];
  onFrameVisible?: ((payload: GridFrameVisiblePayload) => void) | null;
  /** Fired when a frame has a live GPU texture and can be shown without a foreground upload. */
  onFrameReady?: ((frameUrl: string) => void) | null;
  /** Fired when a frame is no longer texture-ready. */
  onFrameEvicted?: ((frameUrl: string) => void) | null;
  /** Optional repaint scheduler for callers that need to batch multiple grid layers. */
  requestRepaint?: (() => void) | null;
};

function mercatorXFromMeters(x: number): number {
  return (x + MERCATOR_HALF_WORLD) / (2 * MERCATOR_HALF_WORLD);
}

function mercatorYFromMeters(y: number): number {
  return (MERCATOR_HALF_WORLD - y) / (2 * MERCATOR_HALF_WORLD);
}

/**
 * Latitude limit of the Web Mercator world. A geographic (EPSG:4326) artifact
 * may carry data all the way to the poles, but the quad must never extend past
 * this — Mercator y diverges at ±90°. The poleward rows simply are not drawn.
 * "Full pole coverage" for a global artifact is a storage/sampling property,
 * never a display one.
 */
const MERCATOR_MAX_LATITUDE_DEG = 85.05112877980659;
/** Mercator sphere radius: MERCATOR_HALF_WORLD = PI * R. */
const MERCATOR_EARTH_RADIUS = MERCATOR_HALF_WORLD / Math.PI;

/** True when a grid manifest/frame declares geographic (degree) coordinates. */
function isGeographicProjection(projection: string | null | undefined): boolean {
  return String(projection ?? "").trim().toUpperCase() === "EPSG:4326";
}

function mercatorXFromLonDeg(lonDeg: number): number {
  return (lonDeg * MERCATOR_HALF_WORLD) / 180;
}

/**
 * True when a geographic manifest's bbox spans the whole 360° of longitude —
 * the global-domain grid of docs/GLOBAL_DOMAIN_4326_CONTRACT.md §2, whose
 * cell-edge bbox is (-180.125, ..., 179.875) around a seam column centred on
 * -180. Only such a grid may abut its own world copies, so only such a grid
 * gets the world-exact quad and the wrapping column fetch.
 */
function isFullWorldGeographic(
  projection: string | null | undefined,
  bbox: [number, number, number, number],
): boolean {
  return isGeographicProjection(projection) && Math.abs((bbox[2] - bbox[0]) - 360) < 1e-6;
}

/**
 * Inverse Gudermannian: gd^-1(phi) = R * ln(tan(PI/4 + phi/2)), i.e. the
 * Web Mercator northing in metres for a latitude in degrees.
 */
function mercatorYFromLatDeg(latDeg: number): number {
  const clamped = Math.max(-MERCATOR_MAX_LATITUDE_DEG, Math.min(MERCATOR_MAX_LATITUDE_DEG, latDeg));
  const phi = (clamped * Math.PI) / 180;
  return MERCATOR_EARTH_RADIUS * Math.log(Math.tan(Math.PI / 4 + phi / 2));
}

/**
 * Quad corners in MapLibre mercator-unit space.
 *
 * EPSG:3857 manifests (every canonical artifact) take the original path
 * unchanged: the bbox is already in mercator metres.
 *
 * EPSG:4326 manifests (the global domain, native 0.25°) carry a bbox in
 * degrees. Longitude is linear in mercator x; latitude goes through the
 * inverse Gudermannian and the quad's latitude extent is CLIPPED to
 * ±MERCATOR_MAX_LATITUDE_DEG. The clip is why the fragment shader cannot use
 * the interpolated t texcoord for a geographic texture: it must recover
 * latitude from the fragment's mercator position and index the FULL artifact
 * latitude extent (see u_latNorthRad/u_latSouthRad).
 *
 * A FULL-WORLD geographic bbox is additionally clipped in longitude to exactly
 * ±180: its half-cell overhang (±0.125°) sits outside the mercator world, and
 * once world copies are drawn (visibleWorldOffsets) the overhang would be
 * painted twice — a darker strip along the seam under any layer opacity < 1.
 * The quad therefore spans mercator-unit x exactly [0, 1] and the texcoords
 * (buildQuadTexCoords) carry the half-cell offset instead.
 */
function buildQuadVertices(
  bbox: [number, number, number, number],
  projection?: string | null,
): Float32Array {
  const [, south, , north] = bbox;
  const geographic = isGeographicProjection(projection);
  const fullWorld = isFullWorldGeographic(projection, bbox);
  const west = fullWorld ? -180 : bbox[0];
  const east = fullWorld ? 180 : bbox[2];
  const left = geographic ? mercatorXFromLonDeg(west) : west;
  const right = geographic ? mercatorXFromLonDeg(east) : east;
  const top = geographic ? mercatorYFromLatDeg(north) : north;
  const bottom = geographic ? mercatorYFromLatDeg(south) : south;
  return new Float32Array([
    mercatorXFromMeters(left), mercatorYFromMeters(top),
    mercatorXFromMeters(right), mercatorYFromMeters(top),
    mercatorXFromMeters(left), mercatorYFromMeters(bottom),
    mercatorXFromMeters(right), mercatorYFromMeters(bottom),
  ]);
}

/**
 * Largest number of world copies drawn in one frame. A copy is one extra quad;
 * at any usable zoom the viewport spans at most two worlds and the ±1 padding
 * makes four, so this only ever bites on a degenerate/zoomed-way-out transform.
 */
const MAX_WORLD_COPIES = 5;

/**
 * Absolute clamp on a derived world index before it reaches any loop. Guards
 * against a degenerate transform (an above-horizon unproject once pitch is
 * enabled) producing an effectively unbounded range.
 */
const WORLD_INDEX_LIMIT = 64;

/**
 * `matrix` with the model translated `worldOffset` whole worlds east.
 *
 * The quad lives in mercator-unit space where one world is exactly 1.0 in x, so
 * translating the model by (offset, 0, 0) is M * (v + offset*e_x) — i.e. add
 * `offset` times the matrix's first column to its translation column. Cheaper
 * and more precise than re-deriving a matrix, and it inherits whatever
 * projection/pitch MapLibre baked into the frame's mainMatrix.
 */
function translatedWorldMatrix(matrix: number[], worldOffset: number): number[] {
  if (worldOffset === 0) {
    return matrix;
  }
  const shifted = matrix.slice();
  shifted[12] += worldOffset * matrix[0];
  shifted[13] += worldOffset * matrix[1];
  shifted[14] += worldOffset * matrix[2];
  shifted[15] += worldOffset * matrix[3];
  return shifted;
}

/**
 * Per-manifest texture-projection uniforms. For EPSG:3857 the shader takes the
 * existing path and the latitude bounds are unused. For EPSG:4326 the bounds
 * are the artifact's TRUE latitude extent in radians (e.g. ±90°), NOT the
 * Mercator-clipped quad extent, so v indexes the right texture rows.
 */
function textureProjectionUniforms(
  projection: string | null | undefined,
  bbox: [number, number, number, number],
): { geographic: number; latNorthRad: number; latSouthRad: number } {
  if (!isGeographicProjection(projection)) {
    return { geographic: 0, latNorthRad: 0, latSouthRad: 0 };
  }
  return {
    geographic: 1,
    latNorthRad: (bbox[3] * Math.PI) / 180,
    latSouthRad: (bbox[1] * Math.PI) / 180,
  };
}

/**
 * Quad texcoords. s is the fraction across the artifact's longitude bbox, so
 * for every grid whose quad IS the bbox this is the plain 0..1 outer square,
 * bit-for-bit as before.
 *
 * A full-world geographic quad is world-exact (buildQuadVertices) rather than
 * bbox-exact, so its s range has to be re-expressed against the bbox:
 *   s(lon) = (lon - west) / (east - west)
 * With the contract's bbox (west -180.125, span 360) that is
 *   s(-180) = 0.125/360 = 0.5/1440  -> texel index 0     (column 0 centre)
 *   s(+180) = 360.125/360 = 1 + 0.5/1440 -> texel index 1440, i.e. column 0
 *             again after the wrap in wrapSampleUv()
 * — exactly a half-texel inset on each side, which is what makes lon -180 and
 * lon +180 both land on the seam column's centre and makes fragments between
 * columns 1439 and 0 blend across the seam.
 */
function buildQuadTexCoords(
  bbox: [number, number, number, number],
  projection?: string | null,
): Float32Array {
  let uLeft = 0;
  let uRight = 1;
  if (isFullWorldGeographic(projection, bbox)) {
    const span = bbox[2] - bbox[0];
    uLeft = (-180 - bbox[0]) / span;
    uRight = (180 - bbox[0]) / span;
  }
  return new Float32Array([
    uLeft, 0,
    uRight, 0,
    uLeft, 1,
    uRight, 1,
  ]);
}

function expandUint16BytesToRgba(bytes: Uint8Array): Uint8Array {
  const pixelCount = Math.floor(bytes.length / 2);
  const expanded = new Uint8Array(pixelCount * 4);
  for (let index = 0; index < pixelCount; index += 1) {
    const src = index * 2;
    const dst = index * 4;
    expanded[dst] = bytes[src] ?? 0;
    expanded[dst + 1] = bytes[src + 1] ?? 0;
    expanded[dst + 2] = 0;
    expanded[dst + 3] = 255;
  }
  return expanded;
}

function expandUint8BytesToRgba(bytes: Uint8Array): Uint8Array {
  const pixelCount = bytes.length;
  const expanded = new Uint8Array(pixelCount * 4);
  for (let index = 0; index < pixelCount; index += 1) {
    const value = bytes[index] ?? 0;
    const dst = index * 4;
    expanded[dst] = value;
    expanded[dst + 1] = 0;
    expanded[dst + 2] = 0;
    expanded[dst + 3] = 255;
  }
  return expanded;
}

// ─── Device tier ─────────────────────────────────────────────────────────────
// Three tiers drive cache budgets and texture warm aggressiveness.
// "low"  < 2 GB  — low-end phones: conservative limits to avoid OOM/jank
// "mid"  2–5 GB  — mid-range phones and tablets: 75 % of desktop limits
// "high" ≥ 6 GB  — flagship phones, tablets, and all desktops: full limits
//
// navigator.deviceMemory (Chrome/Edge, capped at 8 by the spec) is the primary
// signal. For browsers that don't expose it (Firefox, Safari) we fall back to
// UA sniffing: mobile UA → "low", everything else → "mid".
type DeviceTier = "low" | "mid" | "high";

function resolveDeviceTier(): DeviceTier {
  if (typeof navigator === "undefined") {
    return "high"; // SSR / test environment — assume capable
  }
  const mem = (navigator as Navigator & { deviceMemory?: number }).deviceMemory;
  if (mem !== undefined) {
    if (mem >= 6) return "high";
    if (mem >= 2) return "mid";
    return "low";
  }
  // deviceMemory not supported — fall back to UA sniff.
  const isMobileUa = /android|iphone|ipad|ipod|mobile/i.test(navigator.userAgent);
  return isMobileUa ? "low" : "mid";
}

// Computed once at module load; the device doesn't change memory at runtime.
const DEVICE_TIER: DeviceTier = resolveDeviceTier();

function resolveFrameCacheBudgetBytes(): number {
  if (DEVICE_TIER === "high") return GRID_FRAME_CACHE_BUDGET_DESKTOP_BYTES;            // 768 MB
  if (DEVICE_TIER === "mid")  return Math.round(GRID_FRAME_CACHE_BUDGET_DESKTOP_BYTES * 0.75); // 576 MB
  return GRID_FRAME_CACHE_BUDGET_MOBILE_BYTES;                                          // 384 MB
}

/**
 * Device-tier frame cache budget, exposed so the idle-warmup logic can decide
 * whether an entire run's frames fit in cache (full-run warm) or whether it
 * must stop at a partial ratio to avoid LRU eviction churn.
 */
export function idleWarmupFrameBudgetBytes(): number {
  return resolveFrameCacheBudgetBytes();
}

function resolveTextureCacheBudgetBytes(): number {
  if (DEVICE_TIER === "high") return GRID_TEXTURE_CACHE_BUDGET_DESKTOP_BYTES;            // 512 MB
  if (DEVICE_TIER === "mid")  return Math.round(GRID_TEXTURE_CACHE_BUDGET_DESKTOP_BYTES * 0.75); // 384 MB
  return GRID_TEXTURE_CACHE_BUDGET_MOBILE_BYTES;                                          // 256 MB
}

function resolveCombinedCacheBudgetBytes(): number {
  const frameBudget = resolveFrameCacheBudgetBytes();
  const textureBudget = resolveTextureCacheBudgetBytes();
  // Keep independent soft caps, but also apply a shared hard cap so a frame
  // timeline can't fully saturate both memory pools at once.
  return frameBudget + Math.round(textureBudget * 0.5);
}

function resolveObservedTextureWarmLimit(): number {
  if (DEVICE_TIER === "high") return OBSERVED_GRID_TEXTURE_WARM_LIMIT_DESKTOP; // 28
  if (DEVICE_TIER === "mid")  return 18;
  return OBSERVED_GRID_TEXTURE_WARM_LIMIT_MOBILE;                               // 10
}

function resolveObservedTextureWarmBatchSize(animating: boolean): number {
  if (DEVICE_TIER === "high") {
    return animating ? OBSERVED_GRID_TEXTURE_WARM_BATCH_SIZE_ANIMATING_DESKTOP : OBSERVED_GRID_TEXTURE_WARM_BATCH_SIZE_DESKTOP;
  }
  if (DEVICE_TIER === "mid") {
    return animating ? OBSERVED_GRID_TEXTURE_WARM_BATCH_SIZE_ANIMATING_MOBILE : OBSERVED_GRID_TEXTURE_WARM_BATCH_SIZE_MOBILE;
  }
  return animating ? OBSERVED_GRID_TEXTURE_WARM_BATCH_SIZE_ANIMATING_MOBILE : OBSERVED_GRID_TEXTURE_WARM_BATCH_SIZE_MOBILE;
}

function resolveForecastTextureWarmBatchSize(animating: boolean): number {
  if (!animating) {
    return GRID_TEXTURE_WARM_BATCH_SIZE;
  }
  return DEVICE_TIER === "low"
    ? GRID_TEXTURE_WARM_BATCH_SIZE_ANIMATING_MOBILE
    : GRID_TEXTURE_WARM_BATCH_SIZE_ANIMATING_DESKTOP;
}

function resolveForecastScrubTextureWarmLimit(scrubLagBurst: boolean): number {
  if (scrubLagBurst) {
    return DEVICE_TIER === "low" ? SCRUB_LAG_BURST_WARM_LIMIT_MOBILE : SCRUB_LAG_BURST_WARM_LIMIT;
  }
  return DEVICE_TIER === "low" ? GRID_TEXTURE_WARM_LIMIT_SCRUB_MOBILE : GRID_TEXTURE_WARM_LIMIT_SCRUB;
}

function resolveScrubTextureWarmBatchSize(scrubLagBurst: boolean): number {
  if (scrubLagBurst) {
    if (DEVICE_TIER === "low") {
      return 3;
    }
    return GRID_TEXTURE_WARM_BATCH_SIZE + 1;
  }
  return DEVICE_TIER === "low" ? 2 : GRID_TEXTURE_WARM_BATCH_SIZE;
}

function resolveScrubTextureWarmFrameBudgetMs(scrubLagBurst: boolean): number {
  if (DEVICE_TIER === "high") {
    return scrubLagBurst ? 10 : 8;
  }
  if (DEVICE_TIER === "mid") {
    return scrubLagBurst ? 9 : 7;
  }
  return scrubLagBurst ? 8 : 6;
}

function resolveTextureWarmFrameBudgetMs(animating: boolean): number {
  if (animating) {
    if (DEVICE_TIER === "high") return 5;
    if (DEVICE_TIER === "mid") return 4;
    return 3;
  }
  if (DEVICE_TIER === "high") return 8;
  if (DEVICE_TIER === "mid") return 6;
  return 4;
}

function resolveRecentFrameRetentionCount(): number {
  if (DEVICE_TIER === "high") return 12;
  if (DEVICE_TIER === "mid") return 8;
  return 5;
}

function resolveObservedTextureHighPriorityCount(): number {
  if (DEVICE_TIER === "high") return OBSERVED_GRID_TEXTURE_HIGH_PRIORITY_COUNT_DESKTOP; // 6
  if (DEVICE_TIER === "mid")  return 5;
  return OBSERVED_GRID_TEXTURE_HIGH_PRIORITY_COUNT_MOBILE;                               // 4
}

function resolveGridDtype(dtype: string | null | undefined): "uint8" | "uint16" {
  return String(dtype ?? "").trim().toLowerCase() === "uint8" ? "uint8" : "uint16";
}

function expectedPackedFrameByteLength(width: number, height: number, dtype: string | null | undefined): number {
  const bytesPerSample = resolveGridDtype(dtype) === "uint8" ? 1 : 2;
  return Math.max(0, Math.floor(width) * Math.floor(height) * bytesPerSample);
}

function resolveMaxTextureSize(gl: WebGLRenderingContext | WebGL2RenderingContext): number {
  const maxTextureSize = Number(gl.getParameter(gl.MAX_TEXTURE_SIZE));
  return Number.isFinite(maxTextureSize) && maxTextureSize > 0 ? Math.floor(maxTextureSize) : 4096;
}

function downsamplePackedGrid(
  bytes: Uint8Array<ArrayBufferLike>,
  sourceWidth: number,
  sourceHeight: number,
  targetWidth: number,
  targetHeight: number,
  dtype: "uint8" | "uint16",
): Uint8Array<ArrayBufferLike> {
  const bytesPerSample = dtype === "uint8" ? 1 : 2;
  const output = new Uint8Array(targetWidth * targetHeight * bytesPerSample);
  for (let y = 0; y < targetHeight; y += 1) {
    const sourceY = Math.min(sourceHeight - 1, Math.floor((y * sourceHeight) / targetHeight));
    for (let x = 0; x < targetWidth; x += 1) {
      const sourceX = Math.min(sourceWidth - 1, Math.floor((x * sourceWidth) / targetWidth));
      const sourceIndex = (sourceY * sourceWidth + sourceX) * bytesPerSample;
      const targetIndex = (y * targetWidth + x) * bytesPerSample;
      output[targetIndex] = bytes[sourceIndex] ?? 0;
      if (bytesPerSample === 2) {
        output[targetIndex + 1] = bytes[sourceIndex + 1] ?? 0;
      }
    }
  }
  return output;
}

function preparePackedGridUpload(
  gl: WebGLRenderingContext | WebGL2RenderingContext,
  bytes: Uint8Array<ArrayBufferLike>,
  width: number,
  height: number,
  dtype: "uint8" | "uint16",
): PreparedGridUpload {
  const maxTextureSize = resolveMaxTextureSize(gl);
  if (width <= maxTextureSize && height <= maxTextureSize) {
    return { bytes, width, height, downsampled: false };
  }
  const scale = Math.min(maxTextureSize / width, maxTextureSize / height);
  const targetWidth = Math.max(1, Math.min(maxTextureSize, Math.round(width * scale)));
  const targetHeight = Math.max(1, Math.min(maxTextureSize, Math.round(height * scale)));
  return {
    bytes: downsamplePackedGrid(bytes, width, height, targetWidth, targetHeight, dtype),
    width: targetWidth,
    height: targetHeight,
    downsampled: true,
  };
}

function parseContentLengthHeader(response: Response): number | null {
  const rawValue = response.headers.get("Content-Length");
  if (!rawValue) {
    return null;
  }
  const parsed = Number.parseInt(rawValue, 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function transparentBelowMinForManifest(manifest: GridManifestResponse | null): number {
  const paletteThreshold = Number(manifest?.palette?.transparent_below_min);
  if (Number.isFinite(paletteThreshold)) {
    return paletteThreshold;
  }
  const colorMapId = String(manifest?.palette?.color_map_id ?? "").trim().toLowerCase();
  return TRANSPARENT_BELOW_MIN_BY_COLOR_MAP_ID.get(colorMapId) ?? Number.NEGATIVE_INFINITY;
}

function powerNormGammaForManifest(manifest: GridManifestResponse | null): number {
  const gamma = Number(manifest?.palette?.power_norm_gamma ?? 1);
  return Number.isFinite(gamma) && gamma > 0 ? gamma : 1;
}

function categoricalPaletteForManifest(manifest: GridManifestResponse | null): boolean {
  const kind = String(manifest?.palette?.kind ?? "").trim().toLowerCase();
  return kind === "indexed" || kind === "categorical";
}

function categoricalNearestForManifest(manifest: GridManifestResponse | null): boolean {
  return Boolean(manifest?.display_prep?.categorical_nearest);
}

function edgeFadeForManifest(manifest: GridManifestResponse | null): boolean {
  return Boolean(manifest?.display_prep?.edge_fade);
}

function edgeFillValueForManifest(manifest: GridManifestResponse | null): number {
  const value = Number(manifest?.display_prep?.edge_fill_value);
  return Number.isFinite(value) ? value : 0;
}

function radarPtypePackedForManifest(manifest: GridManifestResponse | null): boolean {
  const colorMapId = String(manifest?.palette?.color_map_id ?? "").trim().toLowerCase();
  return colorMapId === "radar_ptype";
}

function radarPtypeBreaksForDisplay(
  manifest: GridManifestResponse | null,
  legend: LegendPayload | null,
): { offsets: [number, number, number, number]; counts: [number, number, number, number]; totalBins: number } {
  const order = Array.isArray(manifest?.palette?.ptype_order) && manifest.palette.ptype_order.length >= 4
    ? manifest.palette.ptype_order
    : (Array.isArray(legend?.ptype_order) && legend.ptype_order.length >= 4
      ? legend.ptype_order
      : DEFAULT_RADAR_PTYPE_ORDER);
  const breaks = manifest?.palette?.ptype_breaks ?? legend?.ptype_breaks ?? DEFAULT_RADAR_PTYPE_BREAKS;
  const offsets = [0, 64, 130, 196] as [number, number, number, number];
  const counts = [64, 66, 66, 66] as [number, number, number, number];

  for (let index = 0; index < 4; index += 1) {
    const key = String(order[index] ?? DEFAULT_RADAR_PTYPE_ORDER[index]).trim().toLowerCase();
    const fallback = DEFAULT_RADAR_PTYPE_BREAKS[DEFAULT_RADAR_PTYPE_ORDER[index]];
    const boundary = breaks?.[key] ?? fallback;
    const offset = Number(boundary?.offset);
    const count = Number(boundary?.count);
    offsets[index] = Number.isFinite(offset) && offset >= 0 ? Math.floor(offset) : offsets[index];
    counts[index] = Number.isFinite(count) && count > 0 ? Math.floor(count) : counts[index];
  }

  const totalBins = Math.max(1, ...offsets.map((offset, index) => offset + counts[index]));
  return { offsets, counts, totalBins };
}

function supportCoverageThresholdForManifest(manifest: GridManifestResponse | null): number {
  const colorMapId = String(manifest?.palette?.color_map_id ?? "").trim().toLowerCase();
  // Accumulated precip/snow already use transparent_below_min to fade weak edges.
  // A second support-coverage rejection step creates peppered holes along some
  // low-end contours, so disable that extra pruning for these fields.
  if (SUPPORT_COVERAGE_THRESHOLD_DISABLED_COLOR_MAP_IDS.has(colorMapId)) {
    return 0;
  }
  const threshold = Number(manifest?.display_prep?.support_coverage_threshold);
  if (Number.isFinite(threshold)) {
    return Math.max(0, Math.min(1, threshold));
  }
  return 0;
}

function lowEndEdgeCleanupForManifest(manifest: GridManifestResponse | null): { maxValue: number; supportCoverageThreshold: number } {
  const colorMapId = String(manifest?.palette?.color_map_id ?? "").trim().toLowerCase();
  return LOW_END_EDGE_CLEANUP_BY_COLOR_MAP_ID.get(colorMapId)
    ?? { maxValue: Number.NEGATIVE_INFINITY, supportCoverageThreshold: 0 };
}

function isObservedGridManifest(manifest: GridManifestResponse | null): boolean {
  return String(manifest?.model ?? "").trim().toLowerCase() === "mrms";
}

/**
 * Identity of everything in a manifest that the draw call bakes into geometry
 * or into the interpretation of the frame texture's raw bytes: the quad
 * (projection + bbox) and the grid decode (dimensions + dtype + scale/offset/
 * nodata), plus the selection triple so an identity-general mismatch — model,
 * variable, run, OR data domain — is caught by the same key.
 *
 * A frame texture is only drawable against the manifest whose key matches the
 * one in force when the texture was installed. The NA (EPSG:3857, metre bbox)
 * and global (EPSG:4326, degree bbox) domains differ in projection, bbox and
 * grid dimensions, so a domain toggle always changes this key.
 */
function manifestIdentityKey(manifest: GridManifestResponse | null): string {
  if (!manifest) {
    return "";
  }
  const bbox = Array.isArray(manifest.bbox) ? manifest.bbox.join(",") : "";
  const grid = manifest.grid;
  return [
    manifest.model ?? "",
    manifest.var ?? "",
    manifest.run ?? "",
    manifest.projection ?? "",
    bbox,
    grid?.width ?? "",
    grid?.height ?? "",
    grid?.dtype ?? "",
    grid?.scale ?? "",
    grid?.offset ?? "",
    grid?.nodata ?? "",
  ].join("|");
}

/**
 * Structural identity of the frame TEXTURE itself — the only manifest fields
 * baked into the uploaded GPU object. Everything else a manifest carries
 * (bbox, projection, scale/offset/nodata, palette) is applied per-draw from
 * `this.manifest`, so a manifest change that leaves this key intact can be
 * reconciled by simply restamping the existing texture; a change to this key
 * needs a real re-upload.
 */
function textureCompatKey(manifest: GridManifestResponse | null): string {
  const grid = manifest?.grid;
  const width = Math.max(1, Math.floor(Number(grid?.width) || 1));
  const height = Math.max(1, Math.floor(Number(grid?.height) || 1));
  return `${width}x${height}:${resolveGridDtype(grid?.dtype)}`;
}

export type { GridGeometryMode } from "@/lib/globe-projection";

/**
 * In-render coherence telemetry.
 *
 * - `held` — draws suppressed because the loaded frame texture belonged to a
 *   different manifest than the one now configured.
 * - `drewMismatched` — draws ISSUED against a mismatched pair. Must always
 *   be 0; this is the tripwire.
 * - `coherentDraws` — draws that passed the tripwire. A build that holds
 *   forever is just as broken as one that draws garbage, so tests assert this
 *   keeps increasing.
 * - `reconciled` — holds resolved by restamping the existing texture under a
 *   new manifest identity (see `reconcileManifestIdentity`).
 * - `resetForReupload` — holds resolved by dropping the texture so the normal
 *   fetch/upload path re-runs.
 *
 * Only an in-render check is race-free (React effects observe state after the
 * offending frame has already been painted), so the assertion surface lives
 * here rather than in App state.
 */
export interface GridCoherenceStats {
  held: number;
  drewMismatched: number;
  coherentDraws: number;
  reconciled: number;
  resetForReupload: number;
  /**
   * Geometry/matrix projection mismatches caught before the draw. MUST stay 0.
   *
   * A mercator quad multiplied by a globe (unit-sphere) matrix draws garbage
   * with no GL error, no warning and no type change (GLOBE_SPIKE §1.1) — the
   * one failure mode in this layer that is completely silent. The guard holds
   * the draw exactly as the manifest/texture guard does.
   *
   * Orthogonal to `held`/`drewMismatched`, which are about manifest/texture
   * identity and know nothing about projection.
   */
  projectionMismatch: number;
}

const gridCoherenceStats: GridCoherenceStats = {
  held: 0,
  drewMismatched: 0,
  coherentDraws: 0,
  reconciled: 0,
  resetForReupload: 0,
  projectionMismatch: 0,
};

export function readGridCoherenceStats(): GridCoherenceStats {
  return { ...gridCoherenceStats };
}

/**
 * Renderer-projection counters. The world-copy loop is a mercator-only device,
 * so `mercatorQuadDraws` frozen while `globeMeshDraws` climbs is the proof that
 * the globe path issues exactly one draw per frame and never double-draws.
 */
export const globeRenderStats = {
  /**
   * Frames that reached the draw stage. `globeMeshDraws === drawFrames` (and
   * `mercatorQuadDraws === 0`) is the exact statement of "one draw per frame,
   * world-copy loop bypassed" — a bound on the raw draw count is not, because
   * the number of frames a repaint produces is MapLibre's to choose.
   */
  drawFrames: 0,
  globeMeshDraws: 0,
  mercatorQuadDraws: 0,
  /** Geometry mode of the last draw the layer issued. */
  lastGeometryMode: "mercator" as GridGeometryMode,
  /** Space of the matrix that draw was submitted with. */
  lastMatrixSpace: "mercator-unit" as ProjectionMatrixSpace,
  /** MapLibre's projection transition ramp on the last frame (1 = full globe). */
  lastTransitionState: 0,
  lastIndexCount: 0,
  lastPoleFanTriangles: 0,
  /** Last projection data handed to the layer, for the transition-flip probe. */
  lastMainMatrix: null as number[] | null,
  lastFallbackMatrix: null as number[] | null,
  lastClippingPlane: null as number[] | null,
};

export function resetGlobeRenderStats() {
  globeRenderStats.drawFrames = 0;
  globeRenderStats.globeMeshDraws = 0;
  globeRenderStats.mercatorQuadDraws = 0;
  globeRenderStats.lastIndexCount = 0;
  globeRenderStats.lastPoleFanTriangles = 0;
  // The `last*` describers are reset too: a stale "globe" left over from before
  // the reset reads as a fresh observation and would let a test assert on a
  // frame that never happened.
  globeRenderStats.lastGeometryMode = "mercator";
  globeRenderStats.lastMatrixSpace = "mercator-unit";
  globeRenderStats.lastTransitionState = 0;
  globeRenderStats.lastMainMatrix = null;
  globeRenderStats.lastFallbackMatrix = null;
  globeRenderStats.lastClippingPlane = null;
}

export function resetGridCoherenceStats() {
  gridCoherenceStats.held = 0;
  gridCoherenceStats.drewMismatched = 0;
  gridCoherenceStats.coherentDraws = 0;
  gridCoherenceStats.reconciled = 0;
  gridCoherenceStats.resetForReupload = 0;
  gridCoherenceStats.projectionMismatch = 0;
}

/**
 * Live controllers by layer id, so tests can interrogate the real instance's
 * `visibleFrameHour()` (the capture gate) rather than infer it. Entries are
 * removed on detach; the key space is the fixed set of layer ids, so this
 * cannot grow unbounded.
 */
const gridControllerRegistry = new Map<string, GridWebglLayerController>();

if (typeof window !== "undefined") {
  (window as unknown as Record<string, unknown>).__cartoskyGridCoherence = {
    read: readGridCoherenceStats,
    reset: resetGridCoherenceStats,
    /** Capture gate as the exporter sees it. null while a hold is in effect. */
    visibleFrameHour: (layerId?: string) =>
      gridControllerRegistry.get(layerId ?? GRID_WEBGL_LAYER_ID)?.visibleFrameHour() ?? null,
  };
  // Renderer-projection debug seam. Registered unconditionally so a spec can
  // assert globeMeshDraws === 0 with globe mode off.
  (window as unknown as Record<string, unknown>).__cartoskyGlobeRender = {
    meshCols: GLOBE_MESH_COLS,
    meshRows: GLOBE_MESH_ROWS,
    read: () => ({ ...globeRenderStats }),
    reset: resetGlobeRenderStats,
  };
}

function transparentZeroForManifest(manifest: GridManifestResponse | null): boolean {
  return Boolean(manifest?.palette?.transparent_zero);
}

/** True when a sampled value would render fully transparent under the
 *  manifest's palette rules (transparent_below_min / transparent_zero) —
 *  i.e. the map shows no data at that point, so value labels should too. */
export function gridValueRendersTransparent(
  manifest: GridManifestResponse | null,
  value: number,
): boolean {
  if (value <= transparentBelowMinForManifest(manifest)) {
    return true;
  }
  return transparentZeroForManifest(manifest) && value < 0.5;
}

/** Convert user-facing raster-contrast (−1 … 1) to the shader factor.
 *  Matches MapLibre's internal `contrastFactor()`. */
function contrastFactor(contrast: number): number {
  return contrast > 0 ? 1 / (1 - contrast) : 1 + contrast;
}

/** Convert user-facing raster-saturation (−1 … 1) to the shader factor.
 *  Matches MapLibre's internal `saturationFactor()`. */
function saturationFactor(saturation: number): number {
  return saturation > 0 ? 1 - 1 / (1.001 - saturation) : -saturation;
}

function compileShader(gl: WebGLRenderingContext | WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) {
    throw new Error("Failed to create shader");
  }
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const info = gl.getShaderInfoLog(shader) ?? "unknown shader error";
    gl.deleteShader(shader);
    throw new Error(info);
  }
  return shader;
}

function fragmentFloatPrecisionQualifier(gl: WebGLRenderingContext | WebGL2RenderingContext): "highp" | "mediump" {
  const highPrecision = gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER, gl.HIGH_FLOAT);
  return highPrecision && highPrecision.precision > 0 ? "highp" : "mediump";
}

function createProgram(
  gl: WebGLRenderingContext | WebGL2RenderingContext,
  vertexSource: string,
  fragmentSource: string,
): WebGLProgram {
  const vertex = compileShader(gl, gl.VERTEX_SHADER, vertexSource);
  const fragment = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
  const program = gl.createProgram();
  if (!program) {
    gl.deleteShader(vertex);
    gl.deleteShader(fragment);
    throw new Error("Failed to create GL program");
  }
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const info = gl.getProgramInfoLog(program) ?? "unknown program link error";
    gl.deleteProgram(program);
    throw new Error(info);
  }
  return program;
}

function resolveOpacity(
  opacity: number,
  zoom: number,
  overlayFadeOutZoom?: { start: number; end: number } | null,
): number {
  if (!overlayFadeOutZoom) {
    return Math.max(0, Math.min(1, opacity));
  }
  if (zoom <= overlayFadeOutZoom.start) {
    return Math.max(0, Math.min(1, opacity));
  }
  if (zoom >= overlayFadeOutZoom.end) {
    return 0;
  }
  const span = Math.max(1e-6, overlayFadeOutZoom.end - overlayFadeOutZoom.start);
  const progress = (zoom - overlayFadeOutZoom.start) / span;
  return Math.max(0, Math.min(1, opacity * (1 - progress)));
}

type CachedFrame = {
  bytes: Uint8Array<ArrayBufferLike>;
  preparedUploadKey?: string | null;
  preparedUpload?: PreparedGridUpload | null;
};

type CachedTexture = {
  texture: WebGLTexture;
  bytes: number;
  width: number;
  height: number;
};

type PreparedGridUpload = {
  bytes: Uint8Array<ArrayBufferLike>;
  width: number;
  height: number;
  downsampled: boolean;
};

type LruNode<T> = {
  key: string;
  value: T;
  prev: LruNode<T> | null;
  next: LruNode<T> | null;
};

class LruStore<T> {
  private readonly nodes = new Map<string, LruNode<T>>();
  private head: LruNode<T> | null = null;
  private tail: LruNode<T> | null = null;

  get size(): number {
    return this.nodes.size;
  }

  has(key: string): boolean {
    return this.nodes.has(key);
  }

  get(key: string): T | undefined {
    return this.nodes.get(key)?.value;
  }

  touch(key: string): T | undefined {
    const node = this.nodes.get(key);
    if (!node) {
      return undefined;
    }
    this.moveToTail(node);
    return node.value;
  }

  set(key: string, value: T) {
    const existing = this.nodes.get(key);
    if (existing) {
      existing.value = value;
      this.moveToTail(existing);
      return;
    }
    const node: LruNode<T> = { key, value, prev: this.tail, next: null };
    if (this.tail) {
      this.tail.next = node;
    } else {
      this.head = node;
    }
    this.tail = node;
    this.nodes.set(key, node);
  }

  delete(key: string): T | undefined {
    const node = this.nodes.get(key);
    if (!node) {
      return undefined;
    }
    this.detach(node);
    this.nodes.delete(key);
    return node.value;
  }

  clear() {
    this.nodes.clear();
    this.head = null;
    this.tail = null;
  }

  *values(): IterableIterator<T> {
    let current = this.head;
    while (current) {
      yield current.value;
      current = current.next;
    }
  }

  evictLeastRecentlyUsed(shouldKeep?: (key: string, value: T) => boolean): { key: string; value: T } | null {
    let current = this.head;
    while (current) {
      const next = current.next;
      if (!shouldKeep || !shouldKeep(current.key, current.value)) {
        const key = current.key;
        const value = current.value;
        this.detach(current);
        this.nodes.delete(key);
        return { key, value };
      }
      current = next;
    }
    return null;
  }

  private moveToTail(node: LruNode<T>) {
    if (this.tail === node) {
      return;
    }
    this.detach(node);
    node.prev = this.tail;
    node.next = null;
    if (this.tail) {
      this.tail.next = node;
    } else {
      this.head = node;
    }
    this.tail = node;
  }

  private detach(node: LruNode<T>) {
    if (node.prev) {
      node.prev.next = node.next;
    } else {
      this.head = node.next;
    }
    if (node.next) {
      node.next.prev = node.prev;
    } else {
      this.tail = node.prev;
    }
    node.prev = null;
    node.next = null;
  }
}

type ProgramBindings = {
  positionLocation: number;
  texCoordLocation: number;
  matrixLocation: WebGLUniformLocation | null;
  scaleLocation: WebGLUniformLocation | null;
  offsetLocation: WebGLUniformLocation | null;
  nodataLocation: WebGLUniformLocation | null;
  valueMinLocation: WebGLUniformLocation | null;
  valueMaxLocation: WebGLUniformLocation | null;
  opacityLocation: WebGLUniformLocation | null;
  transparentBelowMinLocation: WebGLUniformLocation | null;
  dataLocation: WebGLUniformLocation | null;
  prevDataLocation: WebGLUniformLocation | null;
  lutLocation: WebGLUniformLocation | null;
  mixLocation: WebGLUniformLocation | null;
  hasPrevLocation: WebGLUniformLocation | null;
  powerNormGammaLocation: WebGLUniformLocation | null;
  dataEncodingLocation: WebGLUniformLocation | null;
  texSizeLocation: WebGLUniformLocation | null;
  categoricalLocation: WebGLUniformLocation | null;
  categoricalNearestLocation: WebGLUniformLocation | null;
  edgeFadeLocation: WebGLUniformLocation | null;
  edgeFillValueLocation: WebGLUniformLocation | null;
  radarPtypePackedLocation: WebGLUniformLocation | null;
  radarPtypeOffsetsLocation: WebGLUniformLocation | null;
  radarPtypeCountsLocation: WebGLUniformLocation | null;
  radarPtypeTotalBinsLocation: WebGLUniformLocation | null;
  supportCoverageThresholdLocation: WebGLUniformLocation | null;
  lowEndEdgeMaxLocation: WebGLUniformLocation | null;
  lowEndEdgeSupportCoverageThresholdLocation: WebGLUniformLocation | null;
  transparentZeroLocation: WebGLUniformLocation | null;
  contrastFactorLocation: WebGLUniformLocation | null;
  saturationFactorLocation: WebGLUniformLocation | null;
  brightnessLowLocation: WebGLUniformLocation | null;
  brightnessHighLocation: WebGLUniformLocation | null;
  contourDataLocation: WebGLUniformLocation | null;
  contourEnabledLocation: WebGLUniformLocation | null;
  contourScaleLocation: WebGLUniformLocation | null;
  contourOffsetLocation: WebGLUniformLocation | null;
  contourNodataLocation: WebGLUniformLocation | null;
  contourDataEncodingLocation: WebGLUniformLocation | null;
  contourTexSizeLocation: WebGLUniformLocation | null;
  contourIntervalLocation: WebGLUniformLocation | null;
  contourColorLocation: WebGLUniformLocation | null;
  texProjectionLocation: WebGLUniformLocation | null;
  latNorthRadLocation: WebGLUniformLocation | null;
  latSouthRadLocation: WebGLUniformLocation | null;
  contourTexProjectionLocation: WebGLUniformLocation | null;
  contourLatNorthRadLocation: WebGLUniformLocation | null;
  contourLatSouthRadLocation: WebGLUniformLocation | null;
  wrapSLocation: WebGLUniformLocation | null;
  contourWrapSLocation: WebGLUniformLocation | null;
};

export class GridWebglLayerController {
  private readonly layerId: string;
  private readonly frameCacheBudgetBytes = resolveFrameCacheBudgetBytes();
  private readonly textureCacheBudgetBytes = resolveTextureCacheBudgetBytes();
  private readonly combinedCacheBudgetBytes = resolveCombinedCacheBudgetBytes();
  private readonly recentFrameRetentionCount = resolveRecentFrameRetentionCount();
  private map: maplibregl.Map | null = null;
  private gl: WebGLRenderingContext | WebGL2RenderingContext | null = null;
  private active = false;
  private manifest: GridManifestResponse | null = null;
  private lodLevel: number | null = null;
  private frameUrl: string | null = null;
  private frameHour: number | null = null;
  private legend: LegendPayload | null = null;
  private opacity = 1;
  private overlayFadeOutZoom: { start: number; end: number } | null = null;
  private rasterPaint: GridRasterPaint = { contrast: 0, saturation: 0, brightnessMin: 0, brightnessMax: 1 };
  private selectionEpoch = 0;
  private selectionKey = "";
  private prefetchUrls: string[] = [];
  private contour: GridContourLayerConfig | null = null;
  private animating = false;
  private isScrubPrefetch = false;
  private scrubLagBurst = false;
  private protectedFetchUrls: string[] = [];
  private onFrameVisible: ((payload: GridFrameVisiblePayload) => void) | null = null;
  private onFrameReady: ((frameUrl: string) => void) | null = null;
  private onFrameEvicted: ((frameUrl: string) => void) | null = null;
  private requestRepaintCallback: (() => void) | null = null;
  private frameCache = new LruStore<CachedFrame>();
  private frameCacheBytes = 0;
  private invalidFrameUrls = new Set<string>();
  private textureCache = new LruStore<CachedTexture>();
  private textureCacheBytes = 0;
  private frameFetches = new Map<string, Promise<Uint8Array<ArrayBufferLike> | null>>();
  private frameFetchAbortControllers = new Map<string, AbortController>();
  private textureWarmQueue: string[] = [];
  private textureWarmQueued = new Set<string>();
  private textureWarmRafId: number | null = null;
  private recentFrameUrls: string[] = [];
  private recentFrameUrlSet = new Set<string>();
  private currentFrameSignature: string | null = null;
  private currentTextureSignature: string | null = null;
  private currentTextureFrameHour: number | null = null;
  private currentTextureSelectionEpoch = 0;
  private currentTextureSelectionKey = "";
  /** Manifest identity in force when `currentTexture` was installed. */
  private currentTextureManifestKey = "";
  /** Structural shape of `currentTexture` (dims + dtype) as uploaded. */
  private currentTextureCompatKey = "";
  private currentTextureUrl: string | null = null;
  private previousTextureUrl: string | null = null;
  private visibleNotifiedSignature: string | null = null;
  private pendingFrameBytes: Uint8Array<ArrayBufferLike> | null = null;
  private pendingFrameSignature: string | null = null;
  private pendingFrameUrl: string | null = null;
  private isWebGL2 = false;
  private program: WebGLProgram | null = null;
  private bindings: ProgramBindings | null = null;
  private vertexBuffer: WebGLBuffer | null = null;
  private texCoordBuffer: WebGLBuffer | null = null;
  private lutTexture: WebGLTexture | null = null;
  private lutPixels: Uint8Array<ArrayBufferLike> = new Uint8Array(GRID_LUT_SIZE * 4);
  private lutMin = 0;
  private lutMax = 1;
  private lutDirty = true;
  private currentTexture: WebGLTexture | null = null;
  private currentContourTexture: WebGLTexture | null = null;
  private currentContourTextureUrl: string | null = null;
  private currentContourTextureWidth = 1;
  private currentContourTextureHeight = 1;
  private previousTexture: WebGLTexture | null = null;
  private currentTextureWidth = 1;
  private currentTextureHeight = 1;
  private hasPreviousTexture = false;
  private transitionStartedAt = 0;
  private transitionDurationMs = 0;
  private quadSignature: string | null = null;
  /**
   * Globe program + sphere-mesh buffers. Created LAZILY, on the first frame
   * MapLibre hands this layer a unit-sphere matrix — so with globe mode off the
   * layer allocates and compiles exactly what it always did.
   */
  private globeProgram: WebGLProgram | null = null;
  private globeBindings: ProgramBindings | null = null;
  private globeUniforms: {
    clippingPlaneLocation: WebGLUniformLocation | null;
    lonLatLocation: number;
  } | null = null;
  private globeLonLatBuffer: WebGLBuffer | null = null;
  private globeIndexBuffer: WebGLBuffer | null = null;
  private globeVertexBuffer: WebGLBuffer | null = null;
  private globeTexCoordBuffer: WebGLBuffer | null = null;
  private globeMeshSignature: string | null = null;
  private globeMeshIndexCount = 0;
  private globeMeshPoleFanTriangles = 0;
  /** Set once a globe-program build has failed, so it is not retried per frame. */
  private globeProgramFailed = false;
  /** Builder for the globe fragment source, captured at mercator init time. */
  private buildFragmentSourceForGlobe: (() => string) | null = null;
  private buildProgramBindings: ((program: WebGLProgram) => ProgramBindings) | null = null;

  constructor(layerId = GRID_WEBGL_LAYER_ID) {
    this.layerId = layerId;
  }

  private buildDiagnosticMeta(frameUrl: string): Record<string, unknown> {
    const selectedLod = this.resolveSelectedLod();
    return {
      frame_url: frameUrl,
      grid_width: selectedLod?.width ?? this.manifest?.grid?.width ?? null,
      grid_height: selectedLod?.height ?? this.manifest?.grid?.height ?? null,
      grid_dtype: this.manifest?.grid?.dtype ?? null,
      grid_lod_level: selectedLod?.level ?? this.lodLevel,
      selection_key: this.selectionKey || null,
      webgl_backend: this.isWebGL2 ? "webgl2" : "webgl1",
    };
  }

  private requestRepaint() {
    if (this.requestRepaintCallback) {
      this.requestRepaintCallback();
      return;
    }
    this.map?.triggerRepaint();
  }

  private buildPreparedUploadCacheKey(
    width: number,
    height: number,
    dtype: "uint8" | "uint16",
    maxTextureSize: number,
  ): string {
    return `${width}x${height}:${dtype}:${maxTextureSize}`;
  }

  private recentFrameRetentionLimit(): number {
    const baseLimit = this.recentFrameRetentionCount;
    if (this.isScrubPrefetch && this.scrubLagBurst) {
      const burstLimit = DEVICE_TIER === "low"
        ? SCRUB_LAG_BURST_WARM_LIMIT_MOBILE
        : SCRUB_LAG_BURST_WARM_LIMIT;
      return Math.max(baseLimit * 2, burstLimit);
    }
    if (this.isScrubPrefetch) {
      return Math.max(baseLimit * 2, DEVICE_TIER === "low" ? 10 : baseLimit * 2);
    }
    return baseLimit;
  }

  private markFrameRetained(frameUrl: string | null | undefined) {
    const normalized = String(frameUrl ?? "").trim();
    if (!normalized) {
      return;
    }
    if (this.recentFrameUrlSet.has(normalized)) {
      const existingIndex = this.recentFrameUrls.indexOf(normalized);
      if (existingIndex >= 0) {
        this.recentFrameUrls.splice(existingIndex, 1);
      }
    } else {
      this.recentFrameUrlSet.add(normalized);
    }
    this.recentFrameUrls.push(normalized);
    const retentionLimit = this.recentFrameRetentionLimit();
    while (this.recentFrameUrls.length > retentionLimit) {
      const removed = this.recentFrameUrls.shift();
      if (removed) {
        this.recentFrameUrlSet.delete(removed);
      }
    }
  }

  private shouldRetainFrameUrl(frameUrl: string | null | undefined, preferredUrl?: string | null): boolean {
    const normalized = String(frameUrl ?? "").trim();
    if (!normalized) {
      return false;
    }
    if (
      normalized === preferredUrl
      || normalized === this.frameUrl
      || normalized === this.pendingFrameUrl
      || normalized === this.currentTextureUrl
      || normalized === this.currentContourTextureUrl
      || normalized === this.previousTextureUrl
      || this.frameFetches.has(normalized)
      || this.textureWarmQueued.has(normalized)
      || this.recentFrameUrlSet.has(normalized)
    ) {
      return true;
    }
    const prefetchIndex = this.prefetchUrls.indexOf(normalized);
    return prefetchIndex >= 0 && prefetchIndex < this.textureWarmHighPriorityCount();
  }

  private resolvePreparedUpload(
    frameUrl: string,
    bytes: Uint8Array<ArrayBufferLike>,
    width: number,
    height: number,
    dtype: "uint8" | "uint16",
  ): { preparedUpload: PreparedGridUpload; cacheHit: boolean; maxTextureSize: number; prepareDurationMs: number } {
    const gl = this.gl;
    if (!gl) {
      return {
        preparedUpload: { bytes, width, height, downsampled: false },
        cacheHit: false,
        maxTextureSize: 4096,
        prepareDurationMs: 0,
      };
    }
    const maxTextureSize = resolveMaxTextureSize(gl);
    const cacheKey = this.buildPreparedUploadCacheKey(width, height, dtype, maxTextureSize);
    const cachedFrame = this.frameCache.get(frameUrl);
    if (cachedFrame?.preparedUploadKey === cacheKey && cachedFrame.preparedUpload) {
      return {
        preparedUpload: cachedFrame.preparedUpload,
        cacheHit: true,
        maxTextureSize,
        prepareDurationMs: 0,
      };
    }

    const prepareStartedAtMs = startNetworkTimer();
    const preparedUpload = preparePackedGridUpload(gl, bytes, width, height, dtype);
    const prepareDurationMs = startNetworkTimer() - prepareStartedAtMs;
    if (cachedFrame) {
      cachedFrame.preparedUploadKey = cacheKey;
      cachedFrame.preparedUpload = preparedUpload;
    }
    return { preparedUpload, cacheHit: false, maxTextureSize, prepareDurationMs };
  }

  private resolveSelectedLod(): { level: number; width: number; height: number } | null {
    const lods = Array.isArray(this.manifest?.lods) ? this.manifest?.lods : [];
    const selected = lods.find((entry) => Number(entry?.level) === Number(this.lodLevel))
      ?? lods.find((entry) => Number(entry?.level) === 0)
      ?? lods[0]
      ?? null;
    if (!selected) {
      return null;
    }
    return {
      level: Number(selected.level),
      width: Math.max(1, Math.floor(Number(selected.width) || 1)),
      height: Math.max(1, Math.floor(Number(selected.height) || 1)),
    };
  }

  createLayer(): maplibregl.CustomLayerInterface {
    return {
      id: this.layerId,
      type: "custom",
      renderingMode: "2d",
      onAdd: (map, gl) => {
        this.map = map;
        this.gl = gl as WebGLRenderingContext | WebGL2RenderingContext;
        this.isWebGL2 = typeof WebGL2RenderingContext !== "undefined" && gl instanceof WebGL2RenderingContext;
        this.initializeGlResources();
        this.uploadLutTexture();
        if (this.pendingFrameBytes && this.pendingFrameSignature && this.pendingFrameUrl) {
          this.activateFrameTexture(this.pendingFrameUrl, this.pendingFrameBytes, this.pendingFrameSignature);
        } else if (this.active && this.frameUrl) {
          void this.ensureFrameLoaded(this.frameUrl, this.currentFrameSignature);
        }
      },
      render: (_gl, args) => {
        const matrix =
          Array.isArray(args)
            ? args
            : Array.from(args.defaultProjectionData.mainMatrix);

        // `mainMatrixSpace` is "mercator-unit" on every frame MapLibre is not
        // globe-rendering, which is every frame with globe mode off — so the
        // call below is the pre-globe call.
        this.render(matrix, readFrameProjection(args));
      },
      onRemove: () => {
        this.disposeGlResources();
      },
    } satisfies maplibregl.CustomLayerInterface;
  }

  ensureAttached(map: maplibregl.Map, beforeId?: string) {
    if (map.getLayer(this.layerId)) {
      return;
    }
    const resolvedBeforeId = beforeId && map.getLayer(beforeId) ? beforeId : undefined;
    map.addLayer(this.createLayer(), resolvedBeforeId);
  }

  /**
   * Query live cache state for a frame URL.
   * Returns "texture" if the GPU texture is hot, "bytes" if only the raw
   * frame bytes are cached (texture upload still needed), or "none" if
   * the frame must be fetched from the network.
   */
  isFrameAvailable(frameUrl: string | null | undefined): "texture" | "bytes" | "none" {
    const normalized = String(frameUrl ?? "").trim();
    if (!normalized || this.invalidFrameUrls.has(normalized)) {
      return "none";
    }
    if (this.textureCache.has(normalized)) {
      return "texture";
    }
    if (this.frameCache.has(normalized)) {
      return "bytes";
    }
    return "none";
  }

  update(config: GridWebglLayerConfig) {
    if (config.selectionKey !== this.selectionKey) {
      this.invalidFrameUrls.clear();
      this.textureWarmQueue = [];
      this.textureWarmQueued.clear();
      this.recentFrameUrls = [];
      this.recentFrameUrlSet.clear();
      // Abort all in-flight fetches for the previous selection.
      for (const controller of this.frameFetchAbortControllers.values()) {
        controller.abort();
      }
      // Clear the current texture so the render loop skips drawing until the
      // new frame is ready. Without this, the next render applies the incoming
      // variable's new LUT to the previous variable's raw data bytes, producing
      // a mis-colored flash (typically orange from the colormap edge values).
      this.currentTexture = null;
      this.currentContourTexture = null;
      this.currentContourTextureUrl = null;
      this.currentTextureSignature = null;
      this.currentTextureFrameHour = null;
      this.currentTextureSelectionEpoch = config.selectionEpoch;
      this.currentTextureSelectionKey = config.selectionKey;
      this.currentTextureManifestKey = "";
      this.currentTextureCompatKey = "";
      this.hasPreviousTexture = false;
      this.previousTexture = null;
    }
    this.active = config.active;
    this.manifest = config.manifest;
    this.lodLevel = Number.isFinite(config.lodLevel) ? Number(config.lodLevel) : null;
    this.frameUrl = config.frameUrl;
    this.frameHour = Number.isFinite(config.frameHour) ? Number(config.frameHour) : null;
    this.opacity = config.opacity;
    this.overlayFadeOutZoom = config.overlayFadeOutZoom ?? null;
    this.rasterPaint = config.rasterPaint ?? { contrast: 0, saturation: 0, brightnessMin: 0, brightnessMax: 1 };
    this.selectionEpoch = config.selectionEpoch;
    this.selectionKey = config.selectionKey;
    this.prefetchUrls = Array.isArray(config.prefetchUrls) ? config.prefetchUrls.filter(Boolean) : [];
    this.contour = config.contour?.frameUrl && config.contour.grid ? config.contour : null;
    this.animating = config.isAnimating ?? false;
    this.isScrubPrefetch = config.isScrubPrefetch ?? false;
    this.scrubLagBurst = config.scrubLagBurst ?? false;
    this.protectedFetchUrls = Array.isArray(config.protectedFetchUrls)
      ? config.protectedFetchUrls.filter(Boolean)
      : [];
    this.onFrameVisible = config.onFrameVisible ?? null;
    this.onFrameReady = config.onFrameReady ?? null;
    this.onFrameEvicted = config.onFrameEvicted ?? null;
    this.requestRepaintCallback = config.requestRepaint ?? null;
    if (this.legend !== config.legend) {
      this.legend = config.legend;
      this.rebuildLegendTexture();
    }

    const nextSignature = this.buildFrameSignature(this.frameUrl);
    this.reconcileManifestIdentity(nextSignature);
    if (!this.active || !this.frameUrl || !this.manifest) {
      this.currentFrameSignature = nextSignature;
      this.requestRepaint();
      return;
    }

    const prioritizedPrefetchUrls = this.prefetchUrls.slice(0, this.textureWarmLimit());
    const protectedFetchUrlSet = new Set(this.protectedFetchUrls);
    this.markFrameRetained(this.frameUrl);
    for (const prefetchUrl of prioritizedPrefetchUrls.slice(0, this.textureWarmHighPriorityCount())) {
      this.markFrameRetained(prefetchUrl);
    }
    for (const protectedUrl of protectedFetchUrlSet) {
      this.markFrameRetained(protectedUrl);
    }
    this.pruneTextureWarmQueue(new Set([
      this.frameUrl,
      ...prioritizedPrefetchUrls,
      ...protectedFetchUrlSet,
    ]));

    if (nextSignature !== this.currentFrameSignature) {
      this.currentFrameSignature = nextSignature;
      this.visibleNotifiedSignature = null;
      void this.ensureFrameLoaded(this.frameUrl, nextSignature);
    } else if (
      this.frameUrl
      && !this.invalidFrameUrls.has(this.frameUrl)
      && !this.textureCache.has(this.frameUrl)
      && !this.frameFetches.has(this.frameUrl)
    ) {
      void this.ensureFrameLoaded(this.frameUrl, nextSignature);
    }

    this.scheduleTextureWarm(this.frameUrl, "high");
    const highPriorityCount = this.textureWarmHighPriorityCount();
    for (let index = 0; index < prioritizedPrefetchUrls.length; index += 1) {
      const prefetchUrl = prioritizedPrefetchUrls[index];
      this.scheduleTextureWarm(prefetchUrl, index < highPriorityCount ? "high" : "normal");
    }
    this.requestRepaint();
  }

  remove(map?: maplibregl.Map | null) {
    const target = map ?? this.map;
    if (target?.getLayer(this.layerId)) {
      target.removeLayer(this.layerId);
    }
    this.disposeGlResources();
    if (gridControllerRegistry.get(this.layerId) === this) {
      gridControllerRegistry.delete(this.layerId);
    }
  }

  /**
   * Make every hold converge.
   *
   * The render guard is a safety net, not a state machine: it refuses to draw
   * an incoherent (manifest, texture) pair but cannot by itself produce a
   * coherent one. The texture stamp is only written in `activateFrameTexture`,
   * which runs on frame-SIGNATURE change — and the signature carries no
   * manifest identity. So a manifest whose identity fields move while the
   * frame URLs stay put (App background-polls the same run's grid manifest
   * every 30s and applies any JSON-different response) would leave the stamp
   * permanently stale: the guard holds forever, nothing draws, and no loader
   * is showing because from React's point of view everything is loaded.
   *
   * Convergence rule, given the frame itself did not change:
   *  - The GPU texture only bakes in dims + dtype. Every other manifest field
   *    (bbox, projection, scale/offset/nodata, palette) is read from
   *    `this.manifest` at draw time. So if the compat key still matches, the
   *    existing texture IS valid under the new manifest — restamp it and the
   *    next draw uses the NEW decode params.
   *  - If dims/dtype moved, the upload is genuinely stale: drop it and let the
   *    normal fetch/upload path re-run. (In practice a base-LOD flip changes
   *    the frame filename too, so the signature branch below already covers
   *    it; this is the belt-and-braces arm.)
   *
   * Either way the hold is bounded. This runs in `update()` — the only place
   * `this.manifest` is ever assigned — so no manifest change can slip past it.
   * It is a liveness mechanism, not a safety one: the in-render guard remains
   * the race-free authority on what may actually be drawn.
   */
  private reconcileManifestIdentity(nextSignature: string | null) {
    if (!this.currentTexture || !this.manifest) {
      return;
    }
    const manifestKey = manifestIdentityKey(this.manifest);
    if (manifestKey === this.currentTextureManifestKey) {
      return;
    }
    // A changed frame signature means the normal load path is already fetching
    // the correct frame for this manifest — holding until it lands is exactly
    // the intended behavior (this is the domain-toggle case).
    if (nextSignature !== this.currentTextureSignature) {
      return;
    }
    if (textureCompatKey(this.manifest) === this.currentTextureCompatKey) {
      this.currentTextureManifestKey = manifestKey;
      gridCoherenceStats.reconciled += 1;
      this.requestRepaint();
      return;
    }
    this.currentTexture = null;
    this.currentContourTexture = null;
    this.currentContourTextureUrl = null;
    this.currentTextureSignature = null;
    this.currentTextureFrameHour = null;
    this.currentTextureManifestKey = "";
    this.currentTextureCompatKey = "";
    this.currentFrameSignature = null;
    this.hasPreviousTexture = false;
    this.previousTexture = null;
    gridCoherenceStats.resetForReupload += 1;
    this.requestRepaint();
  }

  private buildFrameSignature(frameUrl: string | null): string | null {
    if (!frameUrl) {
      return null;
    }
    const contourUrl = this.contour?.frameUrl ?? "";
    return `${this.selectionEpoch}:${this.selectionKey}:${this.frameHour ?? "na"}:${frameUrl}:${contourUrl}`;
  }

  private textureWarmLimit(): number {
    if (isObservedGridManifest(this.manifest)) {
      const baseLimit = resolveObservedTextureWarmLimit();
      const lodPixelCount = this.resolveSelectedLodPixelCount();
      if (lodPixelCount !== null && lodPixelCount >= OBSERVED_VERY_HIGH_RES_LOD_PIXEL_THRESHOLD) {
        return Math.max(6, Math.floor(baseLimit * 0.35));
      }
      if (lodPixelCount !== null && lodPixelCount >= OBSERVED_HIGH_RES_LOD_PIXEL_THRESHOLD) {
        return Math.max(8, Math.floor(baseLimit * 0.5));
      }
      return baseLimit;
    }
    if (this.isScrubPrefetch) {
      return resolveForecastScrubTextureWarmLimit(this.scrubLagBurst);
    }
    return GRID_TEXTURE_WARM_LIMIT;
  }

  private textureWarmBatchSize(): number {
    if (isObservedGridManifest(this.manifest)) {
      const baseBatchSize = resolveObservedTextureWarmBatchSize(this.animating);
      const lodPixelCount = this.resolveSelectedLodPixelCount();
      if (lodPixelCount !== null && lodPixelCount >= OBSERVED_VERY_HIGH_RES_LOD_PIXEL_THRESHOLD) {
        return 1;
      }
      if (lodPixelCount !== null && lodPixelCount >= OBSERVED_HIGH_RES_LOD_PIXEL_THRESHOLD) {
        return this.animating ? 1 : Math.max(1, baseBatchSize - 1);
      }
      return baseBatchSize;
    }
    if (this.isScrubPrefetch) {
      return resolveScrubTextureWarmBatchSize(this.scrubLagBurst);
    }
    return resolveForecastTextureWarmBatchSize(this.animating && !this.isScrubPrefetch);
  }

  private textureWarmHighPriorityCount(): number {
    if (isObservedGridManifest(this.manifest)) {
      return resolveObservedTextureHighPriorityCount();
    }
    if (this.isScrubPrefetch && this.scrubLagBurst) {
      return DEVICE_TIER === "low" ? 6 : 8;
    }
    if (this.isScrubPrefetch) {
      return DEVICE_TIER === "low" ? 5 : 4;
    }
    return 4;
  }

  private resolveSelectedLodPixelCount(): number | null {
    const selected = this.resolveSelectedLod();
    if (!selected) {
      return null;
    }
    const width = Number(selected.width);
    const height = Number(selected.height);
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
      return null;
    }
    return Math.floor(width) * Math.floor(height);
  }

  private rebuildLegendTexture() {
    const lut = buildLegendLut(this.legend);
    this.lutPixels = lut.pixels;
    this.lutMin = lut.min;
    this.lutMax = lut.max;
    this.lutDirty = true;
    this.uploadLutTexture();
  }

  private initializeGlResources() {
    const gl = this.gl;
    if (!gl || this.program) {
      return;
    }

    const vertexSource = `
      attribute vec2 a_pos;
      attribute vec2 a_texCoord;
      uniform mat4 u_matrix;
      varying vec2 v_texCoord;
      // Mercator-unit position of this vertex (a_pos verbatim). Only read when
      // a texture is geographic; the mercator->latitude inverse needs the
      // fragment's world position, which the interpolated texcoord cannot give
      // once the quad is latitude-clipped.
      varying vec2 v_mercUnit;
      void main() {
        v_texCoord = a_texCoord;
        v_mercUnit = a_pos;
        gl_Position = u_matrix * vec4(a_pos, 0.0, 1.0);
      }
    `;
    const fragmentPrecision = fragmentFloatPrecisionQualifier(gl);
    // ONE fragment source for both programs, parameterised by a single boolean.
    // `buildFragmentSource(false)` is byte-identical to the pre-globe literal —
    // both interpolations collapse to "" / to the exact previous text — so the
    // mercator program compiles the same string it always did, which is what
    // keeps the existing goldens bit-identical.
    //
    // The only difference on the globe variant: `projectedUv()` reads an exact
    // `v_latRad` varying instead of inverting the mercator y per fragment. For
    // a 4326 artifact that is strictly MORE accurate (exact at every vertex,
    // linear in latitude between them) and it is what makes the polar rows
    // addressable at all.
    const buildFragmentSource = (globeVariant: boolean) => `
      precision ${fragmentPrecision} float;
      varying vec2 v_texCoord;
      varying vec2 v_mercUnit;${globeVariant ? "\n      varying float v_latRad;" : ""}
      uniform sampler2D u_data;
      uniform sampler2D u_prevData;
      uniform sampler2D u_lut;
      uniform sampler2D u_contourData;
      uniform float u_scale;
      uniform float u_offset;
      uniform float u_nodata;
      uniform float u_valueMin;
      uniform float u_valueMax;
      uniform float u_opacity;
      uniform float u_transparentBelowMin;
      uniform float u_mixAmount;
      uniform float u_hasPrevious;
      uniform float u_powerNormGamma;
      uniform float u_dataEncoding;
      uniform vec2 u_texSize;
      uniform float u_categorical;
      uniform float u_categoricalNearest;
      uniform float u_edgeFade;
      uniform float u_edgeFillValue;
      uniform float u_radarPtypePacked;
      uniform vec4 u_radarPtypeOffsets;
      uniform vec4 u_radarPtypeCounts;
      uniform float u_radarPtypeTotalBins;
      uniform float u_supportCoverageThreshold;
      uniform float u_lowEndEdgeMax;
      uniform float u_lowEndEdgeSupportCoverageThreshold;
      uniform float u_transparentZero;
      uniform float u_contrastFactor;
      uniform float u_saturationFactor;
      uniform float u_brightnessLow;
      uniform float u_brightnessHigh;
      uniform float u_contourEnabled;
      uniform float u_contourScale;
      uniform float u_contourOffset;
      uniform float u_contourNodata;
      uniform float u_contourDataEncoding;
      uniform vec2 u_contourTexSize;
      uniform float u_contourInterval;
      uniform vec4 u_contourColor;
      // Texture-projection branch: 0.0 = EPSG:3857 (uv passes through
      // untouched), 1.0 = EPSG:4326 (uv.y is recomputed per fragment).
      // Uniform across the draw, so the branch is effectively free; deliberately
      // NOT a compiled shader variant.
      uniform float u_texProjection;
      uniform float u_latNorthRad;
      uniform float u_latSouthRad;
      uniform float u_contourTexProjection;
      uniform float u_contourLatNorthRad;
      uniform float u_contourLatSouthRad;
      // Column-wrap branch: 0.0 = clamp at the texture edge (every canonical
      // artifact, unchanged), 1.0 = full-360 geographic grid whose first and
      // last columns are longitudinal neighbours. Same uniform-branch pattern
      // as u_texProjection: constant across the draw, no shader variant.
      uniform float u_wrapS;
      uniform float u_contourWrapS;

      const float CSKY_PI = 3.141592653589793;

      /**
       * Latitude (radians) of a fragment from its mercator-unit y.
       *
       * Mercator-unit y is 0 at +MERCATOR_HALF_WORLD and 1 at
       * -MERCATOR_HALF_WORLD, and MERCATOR_HALF_WORLD = PI * R, so the northing
       * in earth radii is exactly PI * (1 - 2y) — no radius constant needed.
       * Then the inverse Gudermannian: lat = 2*atan(exp(y/R)) - PI/2.
       */
      float latitudeRadFromMercatorUnitY(float mercUnitY) {
        float northingOverR = CSKY_PI * (1.0 - 2.0 * mercUnitY);
        return 2.0 * atan(exp(northingOverR)) - CSKY_PI * 0.5;
      }

      /**
       * uv for a texture whose projection is texProjection.
       *
       * EPSG:3857: returns uv bit-for-bit unchanged.
       * EPSG:4326: uv.x is untouched (mercator x is linear in longitude, so the
       * interpolated s texcoord is already correct); uv.y is rebuilt from the
       * fragment's latitude against the artifact's FULL latitude extent.
       */
      vec2 projectedUv(vec2 uv, float texProjection, float latNorthRad, float latSouthRad) {
        if (texProjection < 0.5) {
          return uv;
        }
        float latSpan = latNorthRad - latSouthRad;
        if (abs(latSpan) < 1e-9) {
          return uv;
        }
        float lat = ${globeVariant ? "v_latRad" : "latitudeRadFromMercatorUnitY(v_mercUnit.y)"};
        return vec2(uv.x, (latNorthRad - lat) / latSpan);
      }

      /**
       * Texel-fetch coordinate for one neighbour of a manual bilinear.
       *
       * wrapS = 0: returned unchanged, so CLAMP_TO_EDGE keeps handling the
       * outer half-texel exactly as before.
       * wrapS = 1: the COLUMN index wraps modulo the texture width. Column
       * indices live in s as (i + 0.5) / width, so i = -1 gives s = -0.5/width
       * and i = width gives s = 1 + 0.5/width; fract() maps both back onto the
       * opposite edge column and is the identity for every in-range i. Rows are
       * untouched — latitude does not wrap.
       */
      vec2 wrapSampleUv(vec2 uv, float wrapS) {
        if (wrapS < 0.5) {
          return uv;
        }
        return vec2(fract(uv.x), uv.y);
      }

      // Decode a single texel from raw R/G bytes to a physical value.
      // Returns the decoded value, or -1e30 if nodata.
      float decodeSample(vec4 sample) {
        float low = floor(sample.r * 255.0 + 0.5);
        float encoded = low;
        if (u_dataEncoding > 0.5) {
          float high = floor(sample.g * 255.0 + 0.5);
          encoded += high * 256.0;
        }
        if (abs(encoded - u_nodata) < 0.5) {
          return -1e30;
        }
        return encoded * u_scale + u_offset;
      }

      float decodeContourSample(vec4 sample) {
        float low = floor(sample.r * 255.0 + 0.5);
        float encoded = low;
        if (u_contourDataEncoding > 0.5) {
          float high = floor(sample.g * 255.0 + 0.5);
          encoded += high * 256.0;
        }
        if (abs(encoded - u_contourNodata) < 0.5) {
          return -1e30;
        }
        return encoded * u_contourScale + u_contourOffset;
      }

      float sampleContourBilinear(vec2 uv) {
        vec2 texel = uv * u_contourTexSize - 0.5;
        vec2 f = fract(texel);
        vec2 base = (floor(texel) + 0.5) / u_contourTexSize;
        vec2 step = 1.0 / u_contourTexSize;
        float v00 = decodeContourSample(texture2D(u_contourData, wrapSampleUv(base, u_contourWrapS)));
        float v10 = decodeContourSample(texture2D(u_contourData, wrapSampleUv(base + vec2(step.x, 0.0), u_contourWrapS)));
        float v01 = decodeContourSample(texture2D(u_contourData, wrapSampleUv(base + vec2(0.0, step.y), u_contourWrapS)));
        float v11 = decodeContourSample(texture2D(u_contourData, wrapSampleUv(base + step, u_contourWrapS)));
        float w00 = v00 > -1e29 ? 1.0 : 0.0;
        float w10 = v10 > -1e29 ? 1.0 : 0.0;
        float w01 = v01 > -1e29 ? 1.0 : 0.0;
        float w11 = v11 > -1e29 ? 1.0 : 0.0;
        float bw00 = (1.0 - f.x) * (1.0 - f.y) * w00;
        float bw10 = f.x * (1.0 - f.y) * w10;
        float bw01 = (1.0 - f.x) * f.y * w01;
        float bw11 = f.x * f.y * w11;
        float wSum = bw00 + bw10 + bw01 + bw11;
        if (wSum <= 0.0) {
          return -1e30;
        }
        return ((v00 > -1e29 ? v00 : 0.0) * bw00
          + (v10 > -1e29 ? v10 : 0.0) * bw10
          + (v01 > -1e29 ? v01 : 0.0) * bw01
          + (v11 > -1e29 ? v11 : 0.0) * bw11) / wSum;
      }

      float contourLineAlpha(vec2 uv) {
        if (u_contourEnabled < 0.5 || u_contourInterval <= 0.0) {
          return 0.0;
        }
        float value = sampleContourBilinear(uv);
        if (value <= -1e29) {
          return 0.0;
        }
        float phase = abs(fract(value / u_contourInterval + 0.5) - 0.5);
        float texelWidth = max(1.0 / max(u_contourTexSize.x, 1.0), 1.0 / max(u_contourTexSize.y, 1.0));
        float width = max(0.022, texelWidth * 2.0);
        return 1.0 - smoothstep(0.0, width, phase);
      }

      // Bilinear interpolation in decoded value space, then LUT lookup.
      // The data texture uses NEAREST filtering so the GPU does not
      // interpolate the raw encoded bytes (which would corrupt uint16
      // values at byte-wrap boundaries).  Instead we fetch four
      // neighbouring texels, decode each independently, bilinearly
      // interpolate the physical values, and then map through the LUT.
      vec4 sampleBilinear(sampler2D tex, vec2 uv) {
        vec2 texel = uv * u_texSize - 0.5;
        vec2 f = fract(texel);
        vec2 base = (floor(texel) + 0.5) / u_texSize;
        vec2 step = 1.0 / u_texSize;
        float v00 = decodeSample(texture2D(tex, wrapSampleUv(base, u_wrapS)));
        float v10 = decodeSample(texture2D(tex, wrapSampleUv(base + vec2(step.x, 0.0), u_wrapS)));
        float v01 = decodeSample(texture2D(tex, wrapSampleUv(base + vec2(0.0, step.y), u_wrapS)));
        float v11 = decodeSample(texture2D(tex, wrapSampleUv(base + step, u_wrapS)));

        // Count valid (non-nodata) neighbours.
        float w00 = v00 > -1e29 ? 1.0 : 0.0;
        float w10 = v10 > -1e29 ? 1.0 : 0.0;
        float w01 = v01 > -1e29 ? 1.0 : 0.0;
        float w11 = v11 > -1e29 ? 1.0 : 0.0;
        float totalWeight = w00 + w10 + w01 + w11;

        if (totalWeight <= 0.0) {
          return vec4(0.0, 0.0, 0.0, 0.0);
        }

        // Edge fill (sparse fields like radar): nodata neighbours contribute
        // u_edgeFillValue at their ORIGINAL bilinear weights, so the visible
        // boundary is the smooth iso-contour where the interpolated surface
        // crosses the transparency floor — curved, sub-texel edges instead of
        // the hard texel boxes the renormalising path below produces. This
        // reproduces exactly how legacy frames (whose nodata was a valid-0
        // skirt) rendered, without putting fabricated values in the data.
        if (u_edgeFade > 0.5) {
          float bwe00 = (1.0 - f.x) * (1.0 - f.y);
          float bwe10 = f.x * (1.0 - f.y);
          float bwe01 = (1.0 - f.x) * f.y;
          float bwe11 = f.x * f.y;
          float e00 = w00 > 0.5 ? v00 : u_edgeFillValue;
          float e10 = w10 > 0.5 ? v10 : u_edgeFillValue;
          float e01 = w01 > 0.5 ? v01 : u_edgeFillValue;
          float e11 = w11 > 0.5 ? v11 : u_edgeFillValue;
          float decodedEdge = e00 * bwe00 + e10 * bwe10 + e01 * bwe01 + e11 * bwe11;
          if (decodedEdge <= u_transparentBelowMin) {
            return vec4(0.0, 0.0, 0.0, 0.0);
          }
          float denomEdge = max(0.000001, u_valueMax - u_valueMin);
          float tEdge = clamp((decodedEdge - u_valueMin) / denomEdge, 0.0, 1.0);
          if (u_powerNormGamma > 0.0 && u_powerNormGamma != 1.0) {
            tEdge = pow(tEdge, u_powerNormGamma);
          }
          return texture2D(u_lut, vec2(tEdge, 0.5));
        }

        // Replace nodata texels with 0 contribution for the lerp.
        float s00 = v00 > -1e29 ? v00 : 0.0;
        float s10 = v10 > -1e29 ? v10 : 0.0;
        float s01 = v01 > -1e29 ? v01 : 0.0;
        float s11 = v11 > -1e29 ? v11 : 0.0;

        // Standard bilinear weights.
        float bw00 = (1.0 - f.x) * (1.0 - f.y);
        float bw10 = f.x * (1.0 - f.y);
        float bw01 = (1.0 - f.x) * f.y;
        float bw11 = f.x * f.y;

        // Zero out weights for nodata texels and re-normalise.
        bw00 *= w00; bw10 *= w10; bw01 *= w01; bw11 *= w11;
        float wSum = bw00 + bw10 + bw01 + bw11;
        if (wSum <= 0.0) {
          return vec4(0.0, 0.0, 0.0, 0.0);
        }
        float decoded = (s00 * bw00 + s10 * bw10 + s01 * bw01 + s11 * bw11) / wSum;
        if (decoded <= u_transparentBelowMin) {
          return vec4(0.0, 0.0, 0.0, 0.0);
        }

        float supportCoverage = 1.0;
        if (
          u_transparentBelowMin > -1e20
          && (u_supportCoverageThreshold > 0.0 || u_lowEndEdgeSupportCoverageThreshold > 0.0)
        ) {
          float sw00 = v00 > u_transparentBelowMin ? bw00 : 0.0;
          float sw10 = v10 > u_transparentBelowMin ? bw10 : 0.0;
          float sw01 = v01 > u_transparentBelowMin ? bw01 : 0.0;
          float sw11 = v11 > u_transparentBelowMin ? bw11 : 0.0;
          supportCoverage = (sw00 + sw10 + sw01 + sw11) / wSum;
          if (u_supportCoverageThreshold > 0.0 && supportCoverage < u_supportCoverageThreshold) {
            return vec4(0.0, 0.0, 0.0, 0.0);
          }
          if (
            u_lowEndEdgeSupportCoverageThreshold > 0.0
            && decoded < u_lowEndEdgeMax
            && supportCoverage < u_lowEndEdgeSupportCoverageThreshold
          ) {
            return vec4(0.0, 0.0, 0.0, 0.0);
          }
        }

        // Normalise to [0,1] and apply power-norm gamma.
        float denom = max(0.000001, u_valueMax - u_valueMin);
        float t = clamp((decoded - u_valueMin) / denom, 0.0, 1.0);
        if (u_powerNormGamma > 0.0 && u_powerNormGamma != 1.0) {
          t = pow(t, u_powerNormGamma);
        }

        vec4 color = texture2D(u_lut, vec2(t, 0.5));
        return color;
      }

      float categoricalVisibleWeight(float decoded) {
        if (decoded <= -1e29) {
          return 0.0;
        }
        if (u_transparentZero > 0.5 && decoded < 0.5) {
          return 0.0;
        }
        return 1.0;
      }

      float categoricalDominantValue(sampler2D tex, vec2 uv) {
        vec2 texel = uv * u_texSize - 0.5;
        vec2 f = fract(texel);
        vec2 base = (floor(texel) + 0.5) / u_texSize;
        vec2 step = 1.0 / u_texSize;

        float v00 = decodeSample(texture2D(tex, wrapSampleUv(base, u_wrapS)));
        float v10 = decodeSample(texture2D(tex, wrapSampleUv(base + vec2(step.x, 0.0), u_wrapS)));
        float v01 = decodeSample(texture2D(tex, wrapSampleUv(base + vec2(0.0, step.y), u_wrapS)));
        float v11 = decodeSample(texture2D(tex, wrapSampleUv(base + step, u_wrapS)));

        float bw00 = (1.0 - f.x) * (1.0 - f.y) * categoricalVisibleWeight(v00);
        float bw10 = f.x * (1.0 - f.y) * categoricalVisibleWeight(v10);
        float bw01 = (1.0 - f.x) * f.y * categoricalVisibleWeight(v01);
        float bw11 = f.x * f.y * categoricalVisibleWeight(v11);

        float bestWeight = 0.0;
        float bestValue = -1e30;
        if (bw00 > bestWeight) {
          bestWeight = bw00;
          bestValue = v00;
        }
        if (bw10 > bestWeight) {
          bestWeight = bw10;
          bestValue = v10;
        }
        if (bw01 > bestWeight) {
          bestWeight = bw01;
          bestValue = v01;
        }
        if (bw11 > bestWeight) {
          bestWeight = bw11;
          bestValue = v11;
        }
        return bestValue;
      }

      vec4 sampleCategorical(sampler2D tex, vec2 uv) {
        float decoded = categoricalDominantValue(tex, uv);
        if (decoded <= -1e29) {
          return vec4(0.0, 0.0, 0.0, 0.0);
        }
        float denom = max(1.0, u_valueMax - u_valueMin + 1.0);
        float t = clamp((floor(decoded + 0.5) - u_valueMin + 0.5) / denom, 0.0, 1.0);
        return texture2D(u_lut, vec2(t, 0.5));
      }

      vec4 sampleCategoricalNearest(sampler2D tex, vec2 uv) {
        float decoded = decodeSample(texture2D(tex, wrapSampleUv(uv, u_wrapS)));
        if (decoded <= -1e29) {
          return vec4(0.0, 0.0, 0.0, 0.0);
        }
        if (u_transparentZero > 0.5 && decoded < 0.5) {
          return vec4(0.0, 0.0, 0.0, 0.0);
        }
        float denom = max(1.0, u_valueMax - u_valueMin + 1.0);
        float t = clamp((floor(decoded + 0.5) - u_valueMin + 0.5) / denom, 0.0, 1.0);
        return texture2D(u_lut, vec2(t, 0.5));
      }

      float radarPtypeCode(float decoded) {
        if (decoded <= -1e29) {
          return -1.0;
        }
        float rounded = floor(decoded + 0.5);
        if (rounded >= u_radarPtypeOffsets.x && rounded < u_radarPtypeOffsets.x + u_radarPtypeCounts.x) {
          return 0.0;
        }
        if (rounded >= u_radarPtypeOffsets.y && rounded < u_radarPtypeOffsets.y + u_radarPtypeCounts.y) {
          return 1.0;
        }
        if (rounded >= u_radarPtypeOffsets.z && rounded < u_radarPtypeOffsets.z + u_radarPtypeCounts.z) {
          return 2.0;
        }
        if (rounded >= u_radarPtypeOffsets.w && rounded < u_radarPtypeOffsets.w + u_radarPtypeCounts.w) {
          return 3.0;
        }
        return -1.0;
      }

      float radarPtypeOffsetForCode(float code) {
        if (code < 0.5) {
          return u_radarPtypeOffsets.x;
        }
        if (code < 1.5) {
          return u_radarPtypeOffsets.y;
        }
        if (code < 2.5) {
          return u_radarPtypeOffsets.z;
        }
        return u_radarPtypeOffsets.w;
      }

      float radarPtypeCountForCode(float code) {
        if (code < 0.5) {
          return u_radarPtypeCounts.x;
        }
        if (code < 1.5) {
          return u_radarPtypeCounts.y;
        }
        if (code < 2.5) {
          return u_radarPtypeCounts.z;
        }
        return u_radarPtypeCounts.w;
      }

      float radarPtypeLocalBin(float decoded, float code) {
        float count = max(1.0, radarPtypeCountForCode(code));
        return clamp((floor(decoded + 0.5)) - radarPtypeOffsetForCode(code), 0.0, count - 1.0);
      }

      vec4 sampleRadarPtypePacked(sampler2D tex, vec2 uv) {
        vec2 texel = uv * u_texSize - 0.5;
        vec2 f = fract(texel);
        vec2 base = (floor(texel) + 0.5) / u_texSize;
        vec2 step = 1.0 / u_texSize;

        float v00 = decodeSample(texture2D(tex, wrapSampleUv(base, u_wrapS)));
        float v10 = decodeSample(texture2D(tex, wrapSampleUv(base + vec2(step.x, 0.0), u_wrapS)));
        float v01 = decodeSample(texture2D(tex, wrapSampleUv(base + vec2(0.0, step.y), u_wrapS)));
        float v11 = decodeSample(texture2D(tex, wrapSampleUv(base + step, u_wrapS)));

        float bw00 = (1.0 - f.x) * (1.0 - f.y);
        float bw10 = f.x * (1.0 - f.y);
        float bw01 = (1.0 - f.x) * f.y;
        float bw11 = f.x * f.y;

        float c00 = radarPtypeCode(v00);
        float c10 = radarPtypeCode(v10);
        float c01 = radarPtypeCode(v01);
        float c11 = radarPtypeCode(v11);

        float wRain = (c00 == 0.0 ? bw00 : 0.0) + (c10 == 0.0 ? bw10 : 0.0) + (c01 == 0.0 ? bw01 : 0.0) + (c11 == 0.0 ? bw11 : 0.0);
        float wSnow = (c00 == 1.0 ? bw00 : 0.0) + (c10 == 1.0 ? bw10 : 0.0) + (c01 == 1.0 ? bw01 : 0.0) + (c11 == 1.0 ? bw11 : 0.0);
        float wSleet = (c00 == 2.0 ? bw00 : 0.0) + (c10 == 2.0 ? bw10 : 0.0) + (c01 == 2.0 ? bw01 : 0.0) + (c11 == 2.0 ? bw11 : 0.0);
        float wFrzr = (c00 == 3.0 ? bw00 : 0.0) + (c10 == 3.0 ? bw10 : 0.0) + (c01 == 3.0 ? bw01 : 0.0) + (c11 == 3.0 ? bw11 : 0.0);

        float selectedCode = 0.0;
        float selectedWeight = wRain;
        if (wSnow > selectedWeight) {
          selectedCode = 1.0;
          selectedWeight = wSnow;
        }
        if (wSleet > selectedWeight) {
          selectedCode = 2.0;
          selectedWeight = wSleet;
        }
        if (wFrzr > selectedWeight) {
          selectedCode = 3.0;
          selectedWeight = wFrzr;
        }
        if (selectedWeight <= 0.0) {
          return vec4(0.0, 0.0, 0.0, 0.0);
        }

        float localSum = 0.0;
        localSum += c00 == selectedCode ? radarPtypeLocalBin(v00, selectedCode) * bw00 : 0.0;
        localSum += c10 == selectedCode ? radarPtypeLocalBin(v10, selectedCode) * bw10 : 0.0;
        localSum += c01 == selectedCode ? radarPtypeLocalBin(v01, selectedCode) * bw01 : 0.0;
        localSum += c11 == selectedCode ? radarPtypeLocalBin(v11, selectedCode) * bw11 : 0.0;
        float localBin = localSum / selectedWeight;
        float selectedCount = max(1.0, radarPtypeCountForCode(selectedCode));
        float packedValue = radarPtypeOffsetForCode(selectedCode) + clamp(localBin, 0.0, selectedCount - 1.0);
        float t = clamp((packedValue + 0.5) / max(1.0, u_radarPtypeTotalBins), 0.0, 1.0);
        vec4 color = texture2D(u_lut, vec2(t, 0.5));
        float edgeAlpha = smoothstep(0.0, 0.5, selectedWeight);
        float intensityAlpha = smoothstep(0.0, 10.0, localBin);
        color.a *= edgeAlpha * intensityAlpha;
        return color;
      }

      void main() {
        // Identical to v_texCoord for EPSG:3857 artifacts; latitude-remapped in
        // v for EPSG:4326 ones. Everything downstream — the byte-wrap-safe
        // 4-texel bilinear, the categorical/ptype dominance passes, the contour
        // sampler — is unchanged and simply consumes this uv.
        vec2 uv = projectedUv(v_texCoord, u_texProjection, u_latNorthRad, u_latSouthRad);
        vec2 contourUv = projectedUv(
          v_texCoord,
          u_contourTexProjection,
          u_contourLatNorthRad,
          u_contourLatSouthRad
        );
        vec4 current = u_radarPtypePacked > 0.5
          ? sampleRadarPtypePacked(u_data, uv)
          : (u_categorical > 0.5
          ? (u_categoricalNearest > 0.5
            ? sampleCategoricalNearest(u_data, uv)
            : sampleCategorical(u_data, uv))
          : sampleBilinear(u_data, uv));
        vec4 previous = u_hasPrevious > 0.5
          ? (u_radarPtypePacked > 0.5
            ? sampleRadarPtypePacked(u_prevData, uv)
            : (u_categorical > 0.5
            ? (u_categoricalNearest > 0.5
              ? sampleCategoricalNearest(u_prevData, uv)
              : sampleCategorical(u_prevData, uv))
            : sampleBilinear(u_prevData, uv)))
          : current;
        vec4 mixed = mix(previous, current, clamp(u_mixAmount, 0.0, 1.0));
        float contourAlpha = contourLineAlpha(contourUv) * u_contourColor.a * u_opacity;
        if (mixed.a <= 0.0 && contourAlpha <= 0.0) {
          discard;
        }
        vec3 rgb = mixed.rgb;

        // Raster paint adjustments (matches MapLibre raster shader).
        // saturation
        float average = (rgb.r + rgb.g + rgb.b) / 3.0;
        rgb += (average - rgb) * u_saturationFactor;
        // contrast
        rgb = (rgb - 0.5) * u_contrastFactor + 0.5;
        // brightness
        rgb = mix(vec3(u_brightnessLow), vec3(u_brightnessHigh), rgb);

        float fillAlpha = mixed.a * u_opacity;
        vec3 premultiplied = rgb * fillAlpha;
        premultiplied = premultiplied * (1.0 - contourAlpha) + u_contourColor.rgb * contourAlpha;
        float finalAlpha = contourAlpha + fillAlpha * (1.0 - contourAlpha);
        gl_FragColor = vec4(premultiplied, finalAlpha);
      }
    `;

    const fragmentSource = buildFragmentSource(false);

    this.program = createProgram(gl, vertexSource, fragmentSource);
    this.vertexBuffer = gl.createBuffer();
    this.texCoordBuffer = gl.createBuffer();
    this.lutTexture = gl.createTexture();
    // A newly created LUT texture is EMPTY, and an empty texture samples as
    // opaque black. The CPU-side lutPixels survive dispose/re-add (layer
    // remounts on selection change), so mark them dirty to force a re-upload
    // into the fresh texture. Without this, a legend whose reference hasn't
    // changed since the last configure() never triggers rebuildLegendTexture,
    // and the overlay renders solid black after any remount.
    this.lutDirty = true;

    if (!this.vertexBuffer || !this.texCoordBuffer || !this.lutTexture) {
      throw new Error("Failed to initialize grid WebGL resources");
    }

    // A builder rather than a literal so the globe program can reuse it; every
    // `this.program` inside became the `program` parameter.
    const buildBindings = (program: WebGLProgram): ProgramBindings => ({
      positionLocation: gl.getAttribLocation(program, "a_pos"),
      texCoordLocation: gl.getAttribLocation(program, "a_texCoord"),
      matrixLocation: gl.getUniformLocation(program, "u_matrix"),
      scaleLocation: gl.getUniformLocation(program, "u_scale"),
      offsetLocation: gl.getUniformLocation(program, "u_offset"),
      nodataLocation: gl.getUniformLocation(program, "u_nodata"),
      valueMinLocation: gl.getUniformLocation(program, "u_valueMin"),
      valueMaxLocation: gl.getUniformLocation(program, "u_valueMax"),
      opacityLocation: gl.getUniformLocation(program, "u_opacity"),
      transparentBelowMinLocation: gl.getUniformLocation(program, "u_transparentBelowMin"),
      dataLocation: gl.getUniformLocation(program, "u_data"),
      prevDataLocation: gl.getUniformLocation(program, "u_prevData"),
      lutLocation: gl.getUniformLocation(program, "u_lut"),
      mixLocation: gl.getUniformLocation(program, "u_mixAmount"),
      hasPrevLocation: gl.getUniformLocation(program, "u_hasPrevious"),
      powerNormGammaLocation: gl.getUniformLocation(program, "u_powerNormGamma"),
      dataEncodingLocation: gl.getUniformLocation(program, "u_dataEncoding"),
      texSizeLocation: gl.getUniformLocation(program, "u_texSize"),
      categoricalLocation: gl.getUniformLocation(program, "u_categorical"),
      categoricalNearestLocation: gl.getUniformLocation(program, "u_categoricalNearest"),
      edgeFadeLocation: gl.getUniformLocation(program, "u_edgeFade"),
      edgeFillValueLocation: gl.getUniformLocation(program, "u_edgeFillValue"),
      radarPtypePackedLocation: gl.getUniformLocation(program, "u_radarPtypePacked"),
      radarPtypeOffsetsLocation: gl.getUniformLocation(program, "u_radarPtypeOffsets"),
      radarPtypeCountsLocation: gl.getUniformLocation(program, "u_radarPtypeCounts"),
      radarPtypeTotalBinsLocation: gl.getUniformLocation(program, "u_radarPtypeTotalBins"),
      supportCoverageThresholdLocation: gl.getUniformLocation(program, "u_supportCoverageThreshold"),
      lowEndEdgeMaxLocation: gl.getUniformLocation(program, "u_lowEndEdgeMax"),
      lowEndEdgeSupportCoverageThresholdLocation: gl.getUniformLocation(
        program,
        "u_lowEndEdgeSupportCoverageThreshold",
      ),
      transparentZeroLocation: gl.getUniformLocation(program, "u_transparentZero"),
      contrastFactorLocation: gl.getUniformLocation(program, "u_contrastFactor"),
      saturationFactorLocation: gl.getUniformLocation(program, "u_saturationFactor"),
      brightnessLowLocation: gl.getUniformLocation(program, "u_brightnessLow"),
      brightnessHighLocation: gl.getUniformLocation(program, "u_brightnessHigh"),
      contourDataLocation: gl.getUniformLocation(program, "u_contourData"),
      contourEnabledLocation: gl.getUniformLocation(program, "u_contourEnabled"),
      contourScaleLocation: gl.getUniformLocation(program, "u_contourScale"),
      contourOffsetLocation: gl.getUniformLocation(program, "u_contourOffset"),
      contourNodataLocation: gl.getUniformLocation(program, "u_contourNodata"),
      contourDataEncodingLocation: gl.getUniformLocation(program, "u_contourDataEncoding"),
      contourTexSizeLocation: gl.getUniformLocation(program, "u_contourTexSize"),
      contourIntervalLocation: gl.getUniformLocation(program, "u_contourInterval"),
      contourColorLocation: gl.getUniformLocation(program, "u_contourColor"),
      texProjectionLocation: gl.getUniformLocation(program, "u_texProjection"),
      latNorthRadLocation: gl.getUniformLocation(program, "u_latNorthRad"),
      latSouthRadLocation: gl.getUniformLocation(program, "u_latSouthRad"),
      contourTexProjectionLocation: gl.getUniformLocation(program, "u_contourTexProjection"),
      contourLatNorthRadLocation: gl.getUniformLocation(program, "u_contourLatNorthRad"),
      contourLatSouthRadLocation: gl.getUniformLocation(program, "u_contourLatSouthRad"),
      wrapSLocation: gl.getUniformLocation(program, "u_wrapS"),
      contourWrapSLocation: gl.getUniformLocation(program, "u_contourWrapS"),
    });

    this.bindings = buildBindings(this.program);
    // Captured, not built: the globe program is compiled on the first globe
    // frame (ensureGlobeProgram) so that with globe mode off this layer
    // allocates and compiles exactly what it always did.
    this.buildFragmentSourceForGlobe = () => buildFragmentSource(true);
    this.buildProgramBindings = buildBindings;

    this.uploadQuadVerticesIfNeeded();
  }

  /**
   * Compile the globe program + create its mesh buffers, once.
   *
   * Returns false if the resources are unavailable, in which case the caller
   * MUST fall back to the mercator quad against a mercator matrix — never to
   * the quad against the sphere matrix.
   */
  private ensureGlobeProgram(): boolean {
    // The readiness check covers the BUFFERS too, not just the program. An
    // earlier revision returned early on `globeProgram && bindings && uniforms`
    // while a null gl.createBuffer() had left the buffers missing — so every
    // later frame reported "ready", uploadGlobeMeshIfNeeded() bailed out, and
    // the layer drew 0 indices: a silently blank globe that never retried.
    if (this.globeResourcesReady()) {
      return true;
    }
    const gl = this.gl;
    if (
      this.globeProgramFailed
      || !gl
      || !this.buildFragmentSourceForGlobe
      || !this.buildProgramBindings
    ) {
      return false;
    }
    try {
      const program = createProgram(gl, GLOBE_VERTEX_SOURCE, this.buildFragmentSourceForGlobe());
      this.globeProgram = program;
      this.globeBindings = this.buildProgramBindings(program);
      this.globeUniforms = {
        clippingPlaneLocation: gl.getUniformLocation(program, "u_globeClippingPlane"),
        lonLatLocation: gl.getAttribLocation(program, "a_lonLat"),
      };
      this.globeVertexBuffer = gl.createBuffer();
      this.globeTexCoordBuffer = gl.createBuffer();
      this.globeLonLatBuffer = gl.createBuffer();
      this.globeIndexBuffer = gl.createBuffer();
      this.globeMeshSignature = null;
      if (!this.globeResourcesReady()) {
        throw new Error("globe mesh buffers could not be created");
      }
    } catch (error) {
      // Latch, and leave NOTHING half-built: a partially-created set would make
      // the readiness check above pass on some later frame.
      this.releaseGlobeResources();
      this.globeProgramFailed = true;
      console.warn("[grid-webgl] globe program unavailable; staying on the mercator path", error);
      return false;
    }
    return true;
  }

  /** Every resource the globe draw path touches exists. */
  private globeResourcesReady(): boolean {
    return Boolean(
      this.globeProgram
      && this.globeBindings
      && this.globeUniforms
      && this.globeVertexBuffer
      && this.globeTexCoordBuffer
      && this.globeLonLatBuffer
      && this.globeIndexBuffer,
    );
  }

  /** Delete + null the globe GL resources. Safe to call on a partial set. */
  private releaseGlobeResources() {
    const gl = this.gl;
    if (gl) {
      if (this.globeProgram) {
        gl.deleteProgram(this.globeProgram);
      }
      for (
        const buffer of [
          this.globeVertexBuffer,
          this.globeTexCoordBuffer,
          this.globeLonLatBuffer,
          this.globeIndexBuffer,
        ]
      ) {
        if (buffer) {
          gl.deleteBuffer(buffer);
        }
      }
    }
    this.globeProgram = null;
    this.globeBindings = null;
    this.globeUniforms = null;
    this.globeVertexBuffer = null;
    this.globeTexCoordBuffer = null;
    this.globeLonLatBuffer = null;
    this.globeIndexBuffer = null;
    this.globeMeshSignature = null;
    this.globeMeshIndexCount = 0;
    this.globeMeshPoleFanTriangles = 0;
  }

  /**
   * Rebuild + upload the subdivided sphere mesh when the artifact footprint or
   * the mesh density changes. Same cache-by-signature shape as
   * uploadQuadVerticesIfNeeded(), so it is off the per-frame path entirely.
   */
  private uploadGlobeMeshIfNeeded() {
    const gl = this.gl;
    if (
      !gl
      || !this.globeVertexBuffer
      || !this.globeTexCoordBuffer
      || !this.globeLonLatBuffer
      || !this.globeIndexBuffer
    ) {
      return;
    }
    const bbox = this.resolveBbox();
    const projection = this.resolveProjection();
    // Signature FIRST, mesh second. The signature is a pure function of the
    // arguments already in hand, so an unchanged footprint costs one string
    // compare instead of a full mesh generation. Checking `mesh.signature`
    // afterwards skipped only the GL upload: the generator still ran on every
    // globe frame and threw away four typed arrays (~50 KB) each time, which
    // profiled as the largest single item in this layer's per-frame JS.
    const signature = globeMeshSignature(bbox, projection, GLOBE_MESH_COLS, GLOBE_MESH_ROWS);
    if (signature === this.globeMeshSignature) {
      return;
    }
    const geographic = isGeographicProjection(projection);
    const fullWorld = isFullWorldGeographic(projection, bbox);
    const mesh = buildGlobeMesh(
      bbox,
      projection,
      geographic,
      fullWorld,
      GLOBE_MESH_COLS,
      GLOBE_MESH_ROWS,
    );
    gl.bindBuffer(gl.ARRAY_BUFFER, this.globeVertexBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.positions, gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.globeTexCoordBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.texCoords, gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.globeLonLatBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.lonLat, gl.STATIC_DRAW);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.globeIndexBuffer);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);
    this.globeMeshIndexCount = mesh.indexCount;
    this.globeMeshPoleFanTriangles = mesh.poleFanTriangles;
    this.globeMeshSignature = mesh.signature;
  }

  private resolveBbox(): [number, number, number, number] {
    const bbox = this.manifest?.bbox;
    if (Array.isArray(bbox) && bbox.length === 4) {
      return [Number(bbox[0]), Number(bbox[1]), Number(bbox[2]), Number(bbox[3])];
    }
    return [-14922340, 2714341, -6679169, 7361866];
  }

  /** Artifact projection for the displayed manifest; defaults to EPSG:3857. */
  private resolveProjection(): string {
    const projection = this.manifest?.projection;
    return typeof projection === "string" && projection.trim() ? projection.trim() : "EPSG:3857";
  }

  private uploadQuadVerticesIfNeeded() {
    const gl = this.gl;
    if (!gl || !this.vertexBuffer || !this.texCoordBuffer) {
      return;
    }
    const bbox = this.resolveBbox();
    const projection = this.resolveProjection();
    const signature = `${projection}|${bbox.join(",")}`;
    if (signature === this.quadSignature) {
      return;
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, this.vertexBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, buildQuadVertices(bbox, projection), gl.STATIC_DRAW);
    // The texcoords are no longer a constant outer square: a full-world
    // geographic quad carries the half-cell inset, so they move with the quad.
    gl.bindBuffer(gl.ARRAY_BUFFER, this.texCoordBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, buildQuadTexCoords(bbox, projection), gl.STATIC_DRAW);
    this.quadSignature = signature;
  }

  private uploadLutTexture() {
    const gl = this.gl;
    if (!gl || !this.lutTexture || !this.lutDirty) {
      return;
    }
    gl.bindTexture(gl.TEXTURE_2D, this.lutTexture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, GRID_LUT_SIZE, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, this.lutPixels);
    this.lutDirty = false;
  }

  private createTextureFromBytes(
    frameUrl: string,
    bytes: Uint8Array<ArrayBufferLike> | null,
    sourceGrid: GridManifestResponse["grid"] | null = this.manifest?.grid ?? null,
    sourceSize?: { width: number; height: number } | null,
  ): WebGLTexture | null {
    const gl = this.gl;
    const grid = sourceGrid;
    if (!gl || !grid) {
      return null;
    }

    const existing = this.textureCache.touch(frameUrl);
    if (existing) {
      this.onFrameReady?.(frameUrl);
      return existing.texture;
    }
    if (!bytes) {
      return null;
    }
    const diagnosticMeta: Record<string, unknown> = {
      ...this.buildDiagnosticMeta(frameUrl),
      payload_bytes: bytes.byteLength,
    };

    const targetTexture = gl.createTexture();
    if (!targetTexture) {
      return null;
    }

    const selectedLod = this.resolveSelectedLod();
    const width = sourceSize?.width ?? selectedLod?.width ?? Math.max(1, Math.floor(Number(grid.width) || 1));
    const height = sourceSize?.height ?? selectedLod?.height ?? Math.max(1, Math.floor(Number(grid.height) || 1));
    const gridDtype = resolveGridDtype(grid.dtype);
    const expectedBytes = expectedPackedFrameByteLength(width, height, gridDtype);
    if (bytes.byteLength < expectedBytes) {
      this.invalidFrameUrls.add(frameUrl);
      console.warn("[grid-webgl] skipping undersized frame texture upload", {
        frameUrl,
        actualBytes: bytes.byteLength,
        expectedBytes,
        width,
        height,
      });
      return null;
    }

    // NEAREST filtering is required for the data texture because encoded
    // uint16 values are split across two bytes (R=low, G=high).  LINEAR
    // filtering interpolates each channel independently, which produces
    // incorrect decoded values at 256-boundaries (where the low byte wraps
    // from 255→0 while the high byte increments).  Those incorrect values
    // map to wrong LUT colours, creating subtle contour-line artefacts.
    // Visual smoothness comes from LINEAR filtering on the LUT texture.
    gl.bindTexture(gl.TEXTURE_2D, targetTexture);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

    const { preparedUpload, cacheHit: preparedUploadCacheHit, maxTextureSize, prepareDurationMs } =
      this.resolvePreparedUpload(frameUrl, bytes, width, height, gridDtype);
    diagnosticMeta.max_texture_size = maxTextureSize;
    diagnosticMeta.upload_width = preparedUpload.width;
    diagnosticMeta.upload_height = preparedUpload.height;
    diagnosticMeta.upload_bytes = preparedUpload.bytes.byteLength;
    diagnosticMeta.texture_downsampled = preparedUpload.downsampled;
    diagnosticMeta.prepared_upload_cache_hit = preparedUploadCacheHit;
    diagnosticMeta.prepared_upload_duration_ms = Math.round(prepareDurationMs);

    if (this.isWebGL2) {
      const gl2 = gl as WebGL2RenderingContext;
      const uploadStartedAtMs = startNetworkTimer();
      if (gridDtype === "uint8") {
        gl2.texImage2D(
          gl2.TEXTURE_2D,
          0,
          gl2.R8,
          preparedUpload.width,
          preparedUpload.height,
          0,
          gl2.RED,
          gl2.UNSIGNED_BYTE,
          preparedUpload.bytes,
        );
      } else {
        gl2.texImage2D(
          gl2.TEXTURE_2D,
          0,
          gl2.RG8,
          preparedUpload.width,
          preparedUpload.height,
          0,
          gl2.RG,
          gl2.UNSIGNED_BYTE,
          preparedUpload.bytes,
        );
      }
      trackClientProcessingDuration({
        metric_name: "grid_texture_upload_duration",
        duration_ms: startNetworkTimer() - uploadStartedAtMs,
        model_id: this.manifest?.model ?? null,
        variable_id: this.manifest?.var ?? null,
        run_id: this.manifest?.run ?? null,
        forecast_hour: this.frameHour,
        meta: diagnosticMeta,
      });
    } else {
      const expandStartedAtMs = startNetworkTimer();
      const rgba = gridDtype === "uint8"
        ? expandUint8BytesToRgba(preparedUpload.bytes)
        : expandUint16BytesToRgba(preparedUpload.bytes);
      trackClientProcessingDuration({
        metric_name: "grid_webgl1_expand_duration",
        duration_ms: startNetworkTimer() - expandStartedAtMs,
        model_id: this.manifest?.model ?? null,
        variable_id: this.manifest?.var ?? null,
        run_id: this.manifest?.run ?? null,
        forecast_hour: this.frameHour,
        meta: diagnosticMeta,
      });
      const uploadStartedAtMs = startNetworkTimer();
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA,
        preparedUpload.width,
        preparedUpload.height,
        0,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        rgba,
      );
      trackClientProcessingDuration({
        metric_name: "grid_texture_upload_duration",
        duration_ms: startNetworkTimer() - uploadStartedAtMs,
        model_id: this.manifest?.model ?? null,
        variable_id: this.manifest?.var ?? null,
        run_id: this.manifest?.run ?? null,
        forecast_hour: this.frameHour,
        meta: diagnosticMeta,
      });
    }
    trackClientProcessingDuration({
      metric_name: "grid_texture_prepare_duration",
      duration_ms: prepareDurationMs,
      model_id: this.manifest?.model ?? null,
      variable_id: this.manifest?.var ?? null,
      run_id: this.manifest?.run ?? null,
      forecast_hour: this.frameHour,
      meta: diagnosticMeta,
    });

    this.textureCache.set(frameUrl, {
      texture: targetTexture,
      bytes: preparedUpload.bytes.byteLength,
      width: preparedUpload.width,
      height: preparedUpload.height,
    });
    this.textureCacheBytes += preparedUpload.bytes.byteLength;
    this.markFrameRetained(frameUrl);
    this.evictCaches(frameUrl);
    this.onFrameReady?.(frameUrl);
    return targetTexture;
  }

  private activateFrameTexture(
    frameUrl: string,
    bytes: Uint8Array<ArrayBufferLike> | null,
    signature: string,
    contourBytes: Uint8Array<ArrayBufferLike> | null = null,
  ) {
    const targetTexture = this.createTextureFromBytes(frameUrl, bytes);
    if (!targetTexture) {
      this.pendingFrameBytes = bytes;
      this.pendingFrameSignature = signature;
      this.pendingFrameUrl = frameUrl;
      return;
    }
    let contourTexture: WebGLTexture | null = null;
    if (this.contour?.frameUrl && this.contour.grid) {
      contourTexture = this.createTextureFromBytes(
        this.contour.frameUrl,
        contourBytes ?? this.frameCache.get(this.contour.frameUrl)?.bytes ?? null,
        this.contour.grid,
        { width: this.contour.width, height: this.contour.height },
      );
    }

    this.pendingFrameBytes = bytes ?? this.frameCache.get(frameUrl)?.bytes ?? null;
    this.pendingFrameSignature = signature;
    this.pendingFrameUrl = frameUrl;
    this.previousTexture = null;
    this.previousTextureUrl = null;
    this.hasPreviousTexture = false;
    this.currentTexture = targetTexture;
    this.currentTextureUrl = frameUrl;
    this.currentContourTexture = contourTexture;
    this.currentContourTextureUrl = this.contour?.frameUrl ?? null;
    this.currentContourTextureWidth = Math.max(1, Math.floor(Number(this.contour?.width) || 1));
    this.currentContourTextureHeight = Math.max(1, Math.floor(Number(this.contour?.height) || 1));
    const cachedTexture = this.textureCache.get(frameUrl);
    this.currentTextureWidth = cachedTexture?.width ?? Math.max(1, Math.floor(Number(this.manifest?.grid?.width) || 1));
    this.currentTextureHeight = cachedTexture?.height ?? Math.max(1, Math.floor(Number(this.manifest?.grid?.height) || 1));
    this.currentTextureSignature = signature;
    this.currentTextureFrameHour = Number.isFinite(this.frameHour) ? Number(this.frameHour) : null;
    this.currentTextureSelectionEpoch = this.selectionEpoch;
    this.currentTextureSelectionKey = this.selectionKey;
    this.currentTextureManifestKey = manifestIdentityKey(this.manifest);
    this.currentTextureCompatKey = textureCompatKey(this.manifest);
    this.transitionStartedAt = performance.now();
    this.markFrameRetained(frameUrl);
    this.requestRepaint();
  }

  private async ensureFrameLoaded(frameUrl: string, signature: string | null) {
    if (!signature) {
      return;
    }
    if (this.invalidFrameUrls.has(frameUrl)) {
      return;
    }
    const contourUrl = this.contour?.frameUrl ?? null;
    const contourGrid = this.contour?.grid ?? null;
    const warmTexture = this.textureCache.touch(frameUrl);
    if (warmTexture) {
      let contourBytes: Uint8Array<ArrayBufferLike> | null = null;
      if (contourUrl && contourGrid) {
        contourBytes = await this.fetchFrameBytes(contourUrl);
        if (signature !== this.currentFrameSignature) {
          return;
        }
      }
      this.activateFrameTexture(frameUrl, this.frameCache.get(frameUrl)?.bytes ?? new Uint8Array(), signature, contourBytes);
      return;
    }
    const cached = this.frameCache.touch(frameUrl);
    if (cached) {
      let contourBytes: Uint8Array<ArrayBufferLike> | null = null;
      if (contourUrl && contourGrid) {
        contourBytes = await this.fetchFrameBytes(contourUrl);
        if (signature !== this.currentFrameSignature) {
          return;
        }
      }
      this.activateFrameTexture(frameUrl, cached.bytes, signature, contourBytes);
      return;
    }

    try {
      const bytes = await this.fetchFrameBytes(frameUrl);
      if (!bytes || signature !== this.currentFrameSignature) {
        return;
      }
      let contourBytes: Uint8Array<ArrayBufferLike> | null = null;
      if (contourUrl && contourGrid) {
        contourBytes = await this.fetchFrameBytes(contourUrl);
        if (signature !== this.currentFrameSignature) {
          return;
        }
      }
      this.activateFrameTexture(frameUrl, bytes, signature, contourBytes);
    } catch {
      // Keep the previously-visible frame on screen if the new one fails.
    }
  }

  private async fetchFrameBytes(frameUrl: string): Promise<Uint8Array<ArrayBufferLike> | null> {
    if (this.invalidFrameUrls.has(frameUrl)) {
      return null;
    }
    const cached = this.frameCache.touch(frameUrl);
    if (cached) {
      this.markFrameRetained(frameUrl);
      return cached.bytes;
    }
    const inFlight = this.frameFetches.get(frameUrl);
    if (inFlight) {
      return inFlight;
    }
    const abortController = new AbortController();
    this.frameFetchAbortControllers.set(frameUrl, abortController);
    const startedAtMs = startNetworkTimer();
    const request = productFetch(this.manifest?.model, frameUrl, { credentials: "omit", signal: abortController.signal })
      .then(async (response) => {
        const diagnosticMeta = this.buildDiagnosticMeta(frameUrl);
        const contentLengthBytes = parseContentLengthHeader(response);
        const responseDiagnosticMeta = {
          ...diagnosticMeta,
          cf_cache_status: response.headers.get("CF-Cache-Status")?.trim() || null,
          server_timing: response.headers.get("Server-Timing")?.trim() || null,
          cache_control: response.headers.get("Cache-Control")?.trim() || null,
          age: response.headers.get("Age")?.trim() || null,
          content_encoding: response.headers.get("Content-Encoding")?.trim() || null,
          content_length_bytes: contentLengthBytes,
        };
        trackNetworkFetchDuration({
          metric_name: "grid_binary_fetch_duration",
          started_at_ms: startedAtMs,
          response,
          model_id: this.manifest?.model ?? null,
          variable_id: this.manifest?.var ?? null,
          run_id: this.manifest?.run ?? null,
          forecast_hour: this.frameHour,
          meta: responseDiagnosticMeta,
        });
        if (!response.ok) {
          throw new Error(`Grid frame request failed: ${response.status}`);
        }
        const arrayBufferStartedAtMs = startNetworkTimer();
        const arrayBuffer = await response.arrayBuffer();
        trackClientProcessingDuration({
          metric_name: "grid_binary_array_buffer_duration",
          duration_ms: startNetworkTimer() - arrayBufferStartedAtMs,
          model_id: this.manifest?.model ?? null,
          variable_id: this.manifest?.var ?? null,
          run_id: this.manifest?.run ?? null,
          forecast_hour: this.frameHour,
          meta: {
            ...responseDiagnosticMeta,
            array_buffer_byte_length: arrayBuffer.byteLength,
            payload_bytes: arrayBuffer.byteLength,
          },
        });
        const bytes = new Uint8Array(arrayBuffer);
        this.upsertFrameCache(frameUrl, bytes);
        this.markFrameRetained(frameUrl);
        return bytes;
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return null;
        }
        return null;
      })
      .finally(() => {
        this.frameFetches.delete(frameUrl);
        this.frameFetchAbortControllers.delete(frameUrl);
      });
    this.frameFetches.set(frameUrl, request);
    return request;
  }

  private upsertFrameCache(frameUrl: string, bytes: Uint8Array<ArrayBufferLike>) {
    const existing = this.frameCache.get(frameUrl);
    if (existing) {
      this.frameCacheBytes -= existing.bytes.byteLength;
    }
    this.frameCache.set(frameUrl, { bytes, preparedUploadKey: null, preparedUpload: null });
    this.frameCacheBytes += bytes.byteLength;
    this.evictCaches(frameUrl);
  }

  private combinedCacheBytes(): number {
    return this.frameCacheBytes + this.textureCacheBytes;
  }

  private evictFrameCacheEntry(preferredUrl?: string | null, duplicateOnly = false): boolean {
    const evicted = this.frameCache.evictLeastRecentlyUsed((candidateKey) => {
      if (this.shouldRetainFrameUrl(candidateKey, preferredUrl)) {
        return true;
      }
      return duplicateOnly ? !this.textureCache.has(candidateKey) : false;
    });
    if (!evicted) {
      return false;
    }
    this.frameCacheBytes -= evicted.value.bytes.byteLength;
    if (!this.textureCache.has(evicted.key)) {
      this.onFrameEvicted?.(evicted.key);
    }
    return true;
  }

  private evictTextureCacheEntry(preferredUrl?: string | null): boolean {
    const evicted = this.textureCache.evictLeastRecentlyUsed(
      (candidateKey) => this.shouldRetainFrameUrl(candidateKey, preferredUrl),
    );
    if (!evicted) {
      return false;
    }
    this.textureCacheBytes -= evicted.value.bytes;
    this.gl?.deleteTexture(evicted.value.texture);
    this.onFrameEvicted?.(evicted.key);
    return true;
  }

  private evictCaches(preferredUrl?: string | null) {
    let safety = 0;
    while (
      safety < 256
      && (
        this.frameCacheBytes > this.frameCacheBudgetBytes
        || this.textureCacheBytes > this.textureCacheBudgetBytes
        || this.combinedCacheBytes() > this.combinedCacheBudgetBytes
      )
    ) {
      safety += 1;
      const frameOverBudget = this.frameCacheBytes > this.frameCacheBudgetBytes;
      const textureOverBudget = this.textureCacheBytes > this.textureCacheBudgetBytes;
      const combinedOverBudget = this.combinedCacheBytes() > this.combinedCacheBudgetBytes;

      let evicted = false;
      if (textureOverBudget) {
        evicted = this.evictTextureCacheEntry(preferredUrl);
      }
      if (!evicted && frameOverBudget) {
        evicted = this.evictFrameCacheEntry(preferredUrl);
      }
      if (!evicted && combinedOverBudget) {
        // Under shared pressure, prefer dropping duplicated CPU-side bytes
        // first so recently-uploaded textures stay immediately usable.
        evicted = this.evictFrameCacheEntry(preferredUrl, true);
        if (!evicted) {
          evicted = this.evictFrameCacheEntry(preferredUrl);
        }
        if (!evicted) {
          evicted = this.evictTextureCacheEntry(preferredUrl);
        }
      }
      if (!evicted) {
        break;
      }
    }
  }

  private pruneTextureWarmQueue(desiredUrls: Set<string>) {
    const protectedForegroundUrl = String(this.frameUrl ?? "").trim();
    const protectedFetchUrlSet = new Set(this.protectedFetchUrls);
    // Abort in-flight fetches for URLs that are no longer in the desired set.
    for (const [fetchUrl, controller] of this.frameFetchAbortControllers) {
      if (protectedForegroundUrl && fetchUrl === protectedForegroundUrl) {
        continue;
      }
      if (protectedFetchUrlSet.has(fetchUrl)) {
        continue;
      }
      if (this.recentFrameUrlSet.has(fetchUrl)) {
        continue;
      }
      if (!desiredUrls.has(fetchUrl) && !this.textureCache.has(fetchUrl) && !this.frameCache.has(fetchUrl)) {
        controller.abort();
      }
    }
    let writeIndex = 0;
    for (let readIndex = 0; readIndex < this.textureWarmQueue.length; readIndex += 1) {
      const candidate = this.textureWarmQueue[readIndex];
      if (!desiredUrls.has(candidate)) {
        continue;
      }
      this.textureWarmQueue[writeIndex] = candidate;
      writeIndex += 1;
    }
    this.textureWarmQueue.length = writeIndex;
    this.textureWarmQueued.clear();
    for (const candidate of this.textureWarmQueue) {
      this.textureWarmQueued.add(candidate);
    }
  }

  private pushTextureWarmQueueFront(frameUrl: string) {
    const existingIndex = this.textureWarmQueue.indexOf(frameUrl);
    if (existingIndex === 0) {
      return;
    }
    if (existingIndex > 0) {
      this.textureWarmQueue.splice(existingIndex, 1);
    }
    this.textureWarmQueue.unshift(frameUrl);
  }

  private trimTextureWarmQueue(warmLimit: number) {
    while (this.textureWarmQueue.length > warmLimit) {
      const removed = this.textureWarmQueue.pop();
      if (removed) {
        this.textureWarmQueued.delete(removed);
      }
    }
  }

  private scheduleTextureWarm(frameUrl: string | null, priority: "high" | "normal" = "normal") {
    const normalized = String(frameUrl ?? "").trim();
    if (!normalized || this.invalidFrameUrls.has(normalized) || this.textureCache.has(normalized)) {
      return;
    }
    if (this.textureWarmQueued.has(normalized)) {
      if (priority === "high") {
        this.pushTextureWarmQueueFront(normalized);
      }
    } else {
      this.textureWarmQueued.add(normalized);
      if (priority === "high") {
        this.pushTextureWarmQueueFront(normalized);
      } else {
        this.textureWarmQueue.push(normalized);
      }
    }
    const warmLimit = this.textureWarmLimit();
    if (this.textureWarmQueue.length > warmLimit) {
      this.trimTextureWarmQueue(warmLimit);
    }
    if (this.textureWarmRafId !== null || typeof window === "undefined") {
      return;
    }
    this.textureWarmRafId = window.requestAnimationFrame(() => {
      this.textureWarmRafId = null;
      void this.pumpTextureWarmQueue();
    });
  }

  private async pumpTextureWarmQueue() {
    // Keep warming adaptive so high-resolution observed LODs don't saturate
    // frame time while the user is actively scrubbing or animating.
    const effectiveBatchSize = this.textureWarmBatchSize();
    let frameBudgetMs = this.isScrubPrefetch
      ? resolveScrubTextureWarmFrameBudgetMs(this.scrubLagBurst)
      : resolveTextureWarmFrameBudgetMs(this.animating);
    if (isObservedGridManifest(this.manifest)) {
      const lodPixelCount = this.resolveSelectedLodPixelCount();
      if (lodPixelCount !== null && lodPixelCount >= OBSERVED_VERY_HIGH_RES_LOD_PIXEL_THRESHOLD) {
        frameBudgetMs = Math.min(frameBudgetMs, this.animating ? 3 : 4);
      } else if (lodPixelCount !== null && lodPixelCount >= OBSERVED_HIGH_RES_LOD_PIXEL_THRESHOLD) {
        frameBudgetMs = Math.min(frameBudgetMs, this.animating ? 4 : 5);
      }
    }
    const pumpStartedAt = typeof performance !== "undefined" ? performance.now() : 0;
    let warmedAny = false;
    let warmedCount = 0;

    while (this.textureWarmQueue.length > 0 && warmedCount < effectiveBatchSize) {
      if (
        warmedCount > 0
        && typeof performance !== "undefined"
        && performance.now() - pumpStartedAt >= frameBudgetMs
      ) {
        break;
      }
      const nextUrl = this.textureWarmQueue.shift() ?? "";
      this.textureWarmQueued.delete(nextUrl);
      if (!nextUrl || this.invalidFrameUrls.has(nextUrl) || this.textureCache.has(nextUrl)) {
        continue;
      }

      const bytes = await this.fetchFrameBytes(nextUrl);
      if (!bytes || !this.gl) {
        continue;
      }

      if (
        warmedCount > 0
        && typeof performance !== "undefined"
        && performance.now() - pumpStartedAt >= frameBudgetMs
      ) {
        this.textureWarmQueued.add(nextUrl);
        this.pushTextureWarmQueueFront(nextUrl);
        break;
      }

      if (this.createTextureFromBytes(nextUrl, bytes)) {
        warmedAny = true;
      }
      warmedCount += 1;
    }

    if (warmedAny) {
      this.requestRepaint();
    }

    if (this.textureWarmQueue.length > 0 && this.textureWarmRafId === null && typeof window !== "undefined") {
      this.textureWarmRafId = window.requestAnimationFrame(() => {
        this.textureWarmRafId = null;
        void this.pumpTextureWarmQueue();
      });
    }
  }

  /**
   * World-copy offsets (whole worlds east of the primary) to draw this frame.
   *
   * MapLibre repeats TILE layers across world copies but leaves custom layers to
   * replicate themselves, so without this the data layer stops dead at the
   * antimeridian and panning past it shows basemap with no weather. This is not
   * global-specific: a regional EPSG:3857 quad repeats per world too, which is
   * what makes an NA product visible when the camera has wrapped.
   *
   * The visible copies are the integer world indices spanned by the four canvas
   * corners — map.unproject reports UNWRAPPED longitudes, so floor((lng+180)/360)
   * IS the world index — padded by one so a copy entering the viewport is never
   * a frame late. Same construction MapLibre's own
   * transform.getVisibleUnwrappedCoordinates uses, via public API only.
   */
  private visibleWorldOffsets(): number[] {
    const map = this.map;
    if (!map) {
      return [0];
    }
    if (typeof map.getRenderWorldCopies === "function" && !map.getRenderWorldCopies()) {
      return [0];
    }
    const canvas = map.getCanvas();
    const width = canvas.clientWidth || canvas.width;
    const height = canvas.clientHeight || canvas.height;
    if (!(width > 0) || !(height > 0)) {
      return [0];
    }
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;
    try {
      const corners: Array<[number, number]> = [[0, 0], [width, 0], [width, height], [0, height]];
      for (const [x, y] of corners) {
        const index = Math.floor((map.unproject([x, y]).lng + 180) / 360);
        if (!Number.isFinite(index)) {
          return [0];
        }
        min = Math.min(min, index);
        max = Math.max(max, index);
      }
    } catch {
      // A degenerate transform (pitched past the horizon, zero-size canvas)
      // must never take the data layer down with it.
      return [0];
    }
    // Hard clamp BEFORE any loop runs. maxPitch is 0 today, which bounds the
    // corner longitudes, but an above-horizon unproject under a future pitch
    // returns an arbitrarily large lng — and an unbounded world index would
    // turn both loops below into a hang. WORLD_INDEX_LIMIT is far past any
    // number of copies that could matter.
    min = Math.max(min - 1, -WORLD_INDEX_LIMIT);
    max = Math.min(max + 1, WORLD_INDEX_LIMIT);
    // The primary copy is always drawn, exactly as MapLibre's own
    // getVisibleUnwrappedCoordinates always emits wrap 0: an off-screen quad is
    // clipped away for free, a missing on-screen one is a hole. When it falls
    // outside the visible range it costs one of the MAX_WORLD_COPIES slots, so
    // the budget is reduced first and the total can never exceed the cap.
    // Trimming only shrinks the range inward, so a 0 outside it stays outside.
    const primaryOutsideRange = min > 0 || max < 0;
    const budget = MAX_WORLD_COPIES - (primaryOutsideRange ? 1 : 0);
    while (max - min + 1 > budget) {
      max -= 1;
      if (max - min + 1 > budget) {
        min += 1;
      }
    }
    const offsets = primaryOutsideRange ? [0] : [];
    for (let index = min; index <= max; index += 1) {
      offsets.push(index);
    }
    return offsets;
  }

  /**
   * Resolve this frame's geometry mode and the matrix that goes with it.
   *
   * Two rules, and the whole projection story is in them:
   *
   *  - Globe geometry is used only when MapLibre handed this layer a
   *    unit-sphere matrix AND its projection transition is complete. During the
   *    transition (`transitionState < 1`) the layer HARD-FLIPS to the mercator
   *    quad driven by `fallbackMatrix`. That is GLOBE_SPIKE section 5 option
   *    (b): MapLibre hard-codes `projectionTransition = 1` for custom layers for
   *    the whole ramp, so faithfully implementing the blend is impossible from
   *    the handed-over uniforms; and MapLibre puts the transition at zoom 11-12,
   *    where globe and mercator have already converged to <= 0.16 px, so the
   *    flip costs nothing and deletes the fallback branch entirely.
   *  - This function returns a matrix and a mode, and NOTHING ELSE. It does not
   *    get to say what space its matrix is in — that is derived from the matrix
   *    itself at the point of use (`deriveMatrixSpace`), so a bug here cannot
   *    also supply the alibi for itself.
   */
  private resolveFrameGeometry(matrix: number[], frame: FrameProjection): {
    mode: GridGeometryMode;
    matrix: number[];
    transitionState: number;
  } {
    const globe = frame.globe;
    if (!globe) {
      return { mode: "mercator", matrix, transitionState: 0 };
    }
    const transitionState = readProjectionTransitionState(this.map);
    if (transitionState >= 1 && this.ensureGlobeProgram()) {
      return { mode: "globe", matrix: globe.mainMatrix, transitionState };
    }
    // Mid-transition, or the globe program is unavailable. `fallbackMatrix` is
    // MapLibre's mercator custom-layer matrix even under globe rendering
    // (GlobeTransform.getProjectionDataForCustomLayer, maplibre-gl-dev.js:59306
    // -- `globeData.fallbackMatrix = mercatorData.mainMatrix`), so the quad has
    // a matrix in its own space and the pair stays coherent.
    return { mode: "mercator", matrix: globe.fallbackMatrix, transitionState };
  }

  private render(matrix: number[], frame: FrameProjection = { mainMatrixSpace: "mercator-unit", globe: null }) {
    // The controller MapLibre is driving is the live one — App may construct
    // several (StrictMode remount, composite/sampler layers) that share a
    // layer id, and only this one is on screen. Registering here rather than
    // in the constructor keeps the test seam pointed at the real instance.
    if (gridControllerRegistry.get(this.layerId) !== this) {
      gridControllerRegistry.set(this.layerId, this);
    }
    const gl = this.gl;
    // Projection mode is a renderer input, resolved per frame from the map's
    // projection state. On every non-globe frame this is "mercator" with the
    // matrix passed straight through, i.e. the pre-globe behaviour.
    const geometry = this.resolveFrameGeometry(matrix, frame);
    const globeActive = geometry.mode === "globe";
    const program = globeActive ? this.globeProgram : this.program;
    const bindings = globeActive ? this.globeBindings : this.bindings;
    const grid = this.manifest?.grid;
    if (
      !gl
      || !program
      || !bindings
      || !this.active
      || !grid
      || !this.currentTexture
      || !this.lutTexture
      || !this.vertexBuffer
      || !this.texCoordBuffer
      || !this.currentTextureSignature
    ) {
      return;
    }

    // Coherence guard. The quad geometry and every decode uniform below come
    // from `this.manifest`, while the sampled bytes come from `currentTexture`.
    // `update()` installs a new manifest the moment React hands one over, but
    // the matching frame texture only lands after its fetch resolves — so
    // without this check the intervening draws pair new geometry with the
    // previous manifest's data. A domain toggle makes that mismatch total
    // (EPSG:3857 metre bbox vs EPSG:4326 degree bbox), which is the "completely
    // off" flash. Hold the draw until the pair is coherent; this is the same
    // thing a model switch already does by nulling the texture outright, so it
    // reuses the existing loading state rather than inventing a new one.
    if (manifestIdentityKey(this.manifest) !== this.currentTextureManifestKey) {
      gridCoherenceStats.held += 1;
      return;
    }

    this.uploadQuadVerticesIfNeeded();
    if (globeActive) {
      this.uploadGlobeMeshIfNeeded();
    }
    this.uploadLutTexture();

    const zoom = this.map?.getZoom() ?? 0;
    const resolvedOpacity = resolveOpacity(this.opacity, zoom, this.overlayFadeOutZoom);
    if (resolvedOpacity <= 0) {
      return;
    }

    gl.useProgram(program);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);

    gl.bindBuffer(gl.ARRAY_BUFFER, globeActive ? this.globeVertexBuffer : this.vertexBuffer);
    gl.enableVertexAttribArray(bindings.positionLocation);
    gl.vertexAttribPointer(bindings.positionLocation, 2, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, globeActive ? this.globeTexCoordBuffer : this.texCoordBuffer);
    gl.enableVertexAttribArray(bindings.texCoordLocation);
    gl.vertexAttribPointer(bindings.texCoordLocation, 2, gl.FLOAT, false, 0, 0);

    // True lon/lat attribute + the horizon clipping plane, both straight from
    // what MapLibre hands custom layers in defaultProjectionData.
    if (globeActive && frame.globe && this.globeUniforms) {
      if (this.globeUniforms.lonLatLocation >= 0) {
        gl.bindBuffer(gl.ARRAY_BUFFER, this.globeLonLatBuffer);
        gl.enableVertexAttribArray(this.globeUniforms.lonLatLocation);
        gl.vertexAttribPointer(this.globeUniforms.lonLatLocation, 2, gl.FLOAT, false, 0, 0);
      }
      gl.uniform4f(this.globeUniforms.clippingPlaneLocation, ...frame.globe.clippingPlane);
    }

    gl.uniform1f(bindings.scaleLocation, Number(grid.scale) || 1);
    gl.uniform1f(bindings.offsetLocation, Number(grid.offset) || 0);
    gl.uniform1f(bindings.nodataLocation, Number(grid.nodata) || 65535);
    gl.uniform1f(bindings.valueMinLocation, this.lutMin);
    gl.uniform1f(bindings.valueMaxLocation, this.lutMax);
    gl.uniform1f(bindings.opacityLocation, resolvedOpacity);
    gl.uniform1f(bindings.transparentBelowMinLocation, transparentBelowMinForManifest(this.manifest));
    gl.uniform1f(bindings.powerNormGammaLocation, powerNormGammaForManifest(this.manifest));
    gl.uniform1f(bindings.dataEncodingLocation, resolveGridDtype(grid.dtype) === "uint16" ? 1 : 0);
    gl.uniform1f(bindings.categoricalLocation, categoricalPaletteForManifest(this.manifest) ? 1 : 0);
    gl.uniform1f(bindings.categoricalNearestLocation, categoricalNearestForManifest(this.manifest) ? 1 : 0);
    gl.uniform1f(bindings.edgeFadeLocation, edgeFadeForManifest(this.manifest) ? 1 : 0);
    gl.uniform1f(bindings.edgeFillValueLocation, edgeFillValueForManifest(this.manifest));
    gl.uniform1f(bindings.radarPtypePackedLocation, radarPtypePackedForManifest(this.manifest) ? 1 : 0);
    const radarPtypeBreaks = radarPtypeBreaksForDisplay(this.manifest, this.legend);
    gl.uniform4f(bindings.radarPtypeOffsetsLocation, ...radarPtypeBreaks.offsets);
    gl.uniform4f(bindings.radarPtypeCountsLocation, ...radarPtypeBreaks.counts);
    gl.uniform1f(bindings.radarPtypeTotalBinsLocation, radarPtypeBreaks.totalBins);
    gl.uniform1f(bindings.supportCoverageThresholdLocation, supportCoverageThresholdForManifest(this.manifest));
    const lowEndEdgeCleanup = lowEndEdgeCleanupForManifest(this.manifest);
    gl.uniform1f(bindings.lowEndEdgeMaxLocation, lowEndEdgeCleanup.maxValue);
    gl.uniform1f(
      bindings.lowEndEdgeSupportCoverageThresholdLocation,
      lowEndEdgeCleanup.supportCoverageThreshold,
    );
    gl.uniform1f(bindings.transparentZeroLocation, transparentZeroForManifest(this.manifest) ? 1 : 0);
    gl.uniform2f(
      bindings.texSizeLocation,
      this.currentTextureWidth,
      this.currentTextureHeight,
    );
    const elapsed = performance.now() - this.transitionStartedAt;
    const mixAmount = this.hasPreviousTexture
      ? Math.max(0, Math.min(1, elapsed / Math.max(1, this.transitionDurationMs)))
      : 1;
    gl.uniform1f(bindings.mixLocation, mixAmount);
    gl.uniform1f(bindings.hasPrevLocation, this.hasPreviousTexture ? 1 : 0);
    gl.uniform1f(bindings.contrastFactorLocation, contrastFactor(this.rasterPaint.contrast));
    gl.uniform1f(bindings.saturationFactorLocation, saturationFactor(this.rasterPaint.saturation));
    gl.uniform1f(bindings.brightnessLowLocation, this.rasterPaint.brightnessMin);
    gl.uniform1f(bindings.brightnessHighLocation, this.rasterPaint.brightnessMax);
    const contourGrid = this.contour?.grid ?? null;
    const contourEnabled = Boolean(
      this.currentContourTexture
      && this.contour?.frameUrl
      && this.contour.frameUrl === this.currentContourTextureUrl
      && contourGrid
      && Number(this.contour?.interval) > 0
    );
    gl.uniform1f(bindings.contourEnabledLocation, contourEnabled ? 1 : 0);
    gl.uniform1f(bindings.contourScaleLocation, Number(contourGrid?.scale) || 1);
    gl.uniform1f(bindings.contourOffsetLocation, Number(contourGrid?.offset) || 0);
    gl.uniform1f(bindings.contourNodataLocation, Number(contourGrid?.nodata) || 65535);
    gl.uniform1f(bindings.contourDataEncodingLocation, resolveGridDtype(contourGrid?.dtype) === "uint16" ? 1 : 0);
    gl.uniform2f(bindings.contourTexSizeLocation, this.currentContourTextureWidth, this.currentContourTextureHeight);
    gl.uniform1f(bindings.contourIntervalLocation, Number(this.contour?.interval) || 0);

    // Per-manifest texture projection. Inert (0 / 0 / 0) for every EPSG:3857
    // artifact, which is every canonical one.
    const dataBbox = this.resolveBbox();
    const dataProjection = textureProjectionUniforms(this.resolveProjection(), dataBbox);
    gl.uniform1f(bindings.texProjectionLocation, dataProjection.geographic);
    gl.uniform1f(bindings.latNorthRadLocation, dataProjection.latNorthRad);
    gl.uniform1f(bindings.latSouthRadLocation, dataProjection.latSouthRad);
    // The contour overlay is a second grid frame. Its bbox and its projection
    // must come from the SAME source: a bbox read from the contour artifact
    // paired with the data manifest's projection (or vice versa) can declare a
    // grid that exists nowhere — e.g. degree bounds judged against EPSG:3857,
    // or metre bounds judged full-world. Either the contour carries its own
    // geometry or it inherits the data artifact's whole, never a mix.
    const contourGeometry = this.contour?.bbox
      ? { bbox: this.contour.bbox, projection: this.contour.projection ?? null }
      : { bbox: dataBbox, projection: this.resolveProjection() };
    const contourProjection = contourEnabled
      ? textureProjectionUniforms(contourGeometry.projection, contourGeometry.bbox)
      : { geographic: 0, latNorthRad: 0, latSouthRad: 0 };
    gl.uniform1f(bindings.contourTexProjectionLocation, contourProjection.geographic);
    gl.uniform1f(bindings.contourLatNorthRadLocation, contourProjection.latNorthRad);
    gl.uniform1f(bindings.contourLatSouthRadLocation, contourProjection.latSouthRad);
    // Column wrap: only a full-360 geographic grid has columns 0 and W-1 as
    // longitudinal neighbours. Every other grid keeps the clamp path.
    gl.uniform1f(bindings.wrapSLocation, isFullWorldGeographic(this.resolveProjection(), dataBbox) ? 1 : 0);
    gl.uniform1f(
      bindings.contourWrapSLocation,
      contourEnabled && isFullWorldGeographic(contourGeometry.projection, contourGeometry.bbox) ? 1 : 0,
    );

    const contourColor = this.contour?.color ?? [0, 0, 0, 0.82];
    gl.uniform4f(
      bindings.contourColorLocation,
      contourColor[0],
      contourColor[1],
      contourColor[2],
      contourColor[3],
    );

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.currentTexture);
    gl.uniform1i(bindings.dataLocation, 0);

    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.hasPreviousTexture ? this.previousTexture : this.currentTexture);
    gl.uniform1i(bindings.prevDataLocation, 1);

    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D, this.lutTexture);
    gl.uniform1i(bindings.lutLocation, 2);

    gl.activeTexture(gl.TEXTURE3);
    gl.bindTexture(gl.TEXTURE_2D, contourEnabled ? this.currentContourTexture : this.currentTexture);
    gl.uniform1i(bindings.contourDataLocation, 3);

    // ── Silent-garbage tripwire ────────────────────────────────────────────
    // A mercator quad multiplied by a unit-sphere matrix draws garbage with no
    // GL error, no warning and no type change; the sphere mesh against a
    // mercator matrix does the same in reverse. Nothing downstream can detect
    // it.
    //
    // The space of the matrix about to be submitted is DERIVED FROM THE MATRIX
    // (deriveMatrixSpace: reference identity against the two arrays MapLibre's
    // projection data was copied into this frame), never from a label the
    // choosing branch supplied. That is what makes this a check rather than a
    // restatement: hand the globe branch `fallbackMatrix` and the derivation
    // says "mercator-unit" against a "globe" geometry, and the draw is held.
    // A matrix of unknown provenance derives to null, which is also unsafe.
    //
    // Placed BEFORE the manifest-coherence counters: a held frame draws
    // nothing, so it must not be counted as a coherent draw either.
    const drawMatrixSpace = deriveMatrixSpace(geometry.matrix, matrix, frame);
    if (drawMatrixSpace === null || !isProjectionPairingCoherent(geometry.mode, drawMatrixSpace)) {
      gridCoherenceStats.projectionMismatch += 1;
      // Same response as the manifest/texture guard: HOLD. Never draw the pair.
      gridCoherenceStats.held += 1;
      if (import.meta.env?.DEV) {
        console.error(
          "[grid-webgl] projection mismatch: refusing to draw "
            + `${geometry.mode} geometry against a ${drawMatrixSpace ?? "unknown-provenance"} matrix `
            + `(frame mainMatrixSpace=${frame.mainMatrixSpace}, `
            + `transitionState=${geometry.transitionState}). `
            + "This would render silent garbage; see docs/GLOBE_SPIKE_2026-08-01.md section 1.1.",
        );
      }
      return;
    }

    // Tripwire at the point of no return: re-read the pair immediately before
    // issuing geometry, so any future draw path that bypasses the guard above
    // is still caught. This must never fire.
    const drawIsCoherent = manifestIdentityKey(this.manifest) === this.currentTextureManifestKey;
    if (drawIsCoherent) {
      // Liveness signal: a build that holds forever is as broken as one that
      // draws garbage, so tests assert this keeps climbing after a switch.
      gridCoherenceStats.coherentDraws += 1;
    } else {
      gridCoherenceStats.drewMismatched += 1;
    }

    globeRenderStats.drawFrames += 1;
    globeRenderStats.lastGeometryMode = geometry.mode;
    globeRenderStats.lastMatrixSpace = drawMatrixSpace;
    globeRenderStats.lastTransitionState = geometry.transitionState;
    globeRenderStats.lastMainMatrix = frame.globe ? frame.globe.mainMatrix : matrix;
    globeRenderStats.lastFallbackMatrix = frame.globe?.fallbackMatrix ?? null;
    globeRenderStats.lastClippingPlane = frame.globe?.clippingPlane ?? null;

    if (globeActive) {
      // Exactly ONE draw. The world-copy loop is a mercator-only device:
      // translatedWorldMatrix() adds whole mercator worlds to the model
      // translation, which is meaningless against a unit-sphere matrix, and the
      // sphere already closes on itself so there is nothing to replicate.
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.globeIndexBuffer);
      gl.uniformMatrix4fv(bindings.matrixLocation, false, geometry.matrix);
      gl.drawElements(gl.TRIANGLES, this.globeMeshIndexCount, gl.UNSIGNED_SHORT, 0);
      globeRenderStats.globeMeshDraws += 1;
      globeRenderStats.lastIndexCount = this.globeMeshIndexCount;
      globeRenderStats.lastPoleFanTriangles = this.globeMeshPoleFanTriangles;
      // Vertex-attrib enable state is global and outlives this draw. The
      // mercator program has no a_lonLat, so leaving the array enabled would
      // hand a later quad draw (or another layer) a stale pointer into the
      // sphere mesh. Only the globe path enables it, so only the globe path
      // clears it.
      if (this.globeUniforms && this.globeUniforms.lonLatLocation >= 0) {
        gl.disableVertexAttribArray(this.globeUniforms.lonLatLocation);
      }
    } else if (frame.globe) {
      // Mid-transition flip: mercator quad, mercator fallback matrix, and still
      // exactly one copy. visibleWorldOffsets() derives world indices from
      // map.unproject() of the canvas corners, which under a globe transform
      // clamps off-disc corners to horizon longitudes and yields nonsense; and
      // the transition lives at zoom >= 11, where one world is far wider than
      // any viewport, so there is no copy to draw anyway.
      gl.uniformMatrix4fv(bindings.matrixLocation, false, geometry.matrix);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      globeRenderStats.mercatorQuadDraws += 1;
    } else {
      for (const offset of this.visibleWorldOffsets()) {
        gl.uniformMatrix4fv(bindings.matrixLocation, false, translatedWorldMatrix(geometry.matrix, offset));
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        globeRenderStats.mercatorQuadDraws += 1;
      }
    }
    if (this.hasPreviousTexture && mixAmount < 1) {
      this.requestRepaint();
    } else if (mixAmount >= 1) {
      this.hasPreviousTexture = false;
    }

    if (
      this.onFrameVisible
      && this.currentTextureSignature
      && this.visibleNotifiedSignature !== this.currentTextureSignature
      && Number.isFinite(this.currentTextureFrameHour)
    ) {
      this.visibleNotifiedSignature = this.currentTextureSignature;
      this.onFrameVisible({
        frameHour: Number(this.currentTextureFrameHour),
        selectionEpoch: this.currentTextureSelectionEpoch,
        selectionKey: this.currentTextureSelectionKey,
      });
    }
  }

  /** Hour of the frame this layer draws right now (null = no texture, e.g.
   * mid selection swap). Checked synchronously inside capture render
   * callbacks so GIF frames can't be read while the grid is transiently
   * cleared (share overhaul Phase 4).
   *
   * A coherence hold is the same situation as "no texture" from the caller's
   * point of view — the layer is holding a texture it refuses to draw, so the
   * grid is not on screen. Reporting the stale texture's hour here would let
   * the atomic capture gate pass while the layer draws nothing, producing
   * gridless exports; the docstring promise above has to stay literally true. */
  visibleFrameHour(): number | null {
    if (
      !this.active
      || !this.currentTexture
      || !this.currentTextureSignature
      || !Number.isFinite(this.currentTextureFrameHour)
    ) {
      return null;
    }
    if (manifestIdentityKey(this.manifest) !== this.currentTextureManifestKey) {
      return null;
    }
    return Number(this.currentTextureFrameHour);
  }

  /** True when `value` renders fully transparent under the current manifest's
   *  palette rules — used to hide city value labels where the map shows no data. */
  valueRendersTransparent(value: number): boolean {
    return gridValueRendersTransparent(this.manifest, value);
  }

  /** Sample anchor-city values from the currently displayed grid frame bytes. */
  sampleAnchorPoints(
    points: AnchorBatchPoint[],
  ): { values: Record<string, number | null>; units: string } | null {
    if (
      points.length === 0
      || categoricalPaletteForManifest(this.manifest)
      || radarPtypePackedForManifest(this.manifest)
    ) {
      return null;
    }

    const frameUrl = this.currentTextureUrl;
    const grid = this.manifest?.grid;
    if (!frameUrl || !grid) {
      return null;
    }

    const cachedFrame = this.frameCache.get(frameUrl);
    if (!cachedFrame?.bytes) {
      return null;
    }

    const selectedLod = this.resolveSelectedLod();
    const sourceWidth = selectedLod?.width ?? Math.max(1, Math.floor(Number(grid.width) || 1));
    const sourceHeight = selectedLod?.height ?? Math.max(1, Math.floor(Number(grid.height) || 1));
    const gridDtype = resolveGridDtype(grid.dtype);
    const { preparedUpload } = this.resolvePreparedUpload(
      frameUrl,
      cachedFrame.bytes,
      sourceWidth,
      sourceHeight,
      gridDtype,
    );

    const values = sampleGridPoints({
      bytes: preparedUpload.bytes,
      grid: {
        width: preparedUpload.width,
        height: preparedUpload.height,
        dtype: grid.dtype,
        scale: Number(grid.scale) || 1,
        offset: Number(grid.offset) || 0,
        nodata: Number(grid.nodata) || 65535,
        units: grid.units,
      },
      bbox: this.resolveBbox(),
      projection: this.resolveProjection(),
      points,
    });

    return {
      values,
      units: typeof grid.units === "string" ? grid.units : "",
    };
  }

  private disposeGlResources() {
    const gl = this.gl;
    if (gl) {
      if (this.program) {
        gl.deleteProgram(this.program);
      }
      if (this.vertexBuffer) {
        gl.deleteBuffer(this.vertexBuffer);
      }
      if (this.texCoordBuffer) {
        gl.deleteBuffer(this.texCoordBuffer);
      }
      for (const entry of this.textureCache.values()) {
        gl.deleteTexture(entry.texture);
      }
      if (this.lutTexture) {
        gl.deleteTexture(this.lutTexture);
      }
    }
    if (this.textureWarmRafId !== null && typeof window !== "undefined") {
      window.cancelAnimationFrame(this.textureWarmRafId);
    }
    // Abort all in-flight frame fetches.
    for (const controller of this.frameFetchAbortControllers.values()) {
      controller.abort();
    }
    this.frameFetchAbortControllers.clear();
    // Deletes and nulls the whole globe set (no-op before the first globe frame).
    this.releaseGlobeResources();
    this.globeProgramFailed = false;
    this.buildFragmentSourceForGlobe = null;
    this.buildProgramBindings = null;
    this.program = null;
    this.bindings = null;
    this.vertexBuffer = null;
    this.texCoordBuffer = null;
    this.lutTexture = null;
    this.textureCache.clear();
    this.textureCacheBytes = 0;
    this.invalidFrameUrls.clear();
    this.textureWarmQueue = [];
    this.textureWarmQueued.clear();
    this.textureWarmRafId = null;
    this.recentFrameUrls = [];
    this.recentFrameUrlSet.clear();
    this.currentTexture = null;
    this.currentContourTexture = null;
    this.currentContourTextureUrl = null;
    this.previousTexture = null;
    this.currentTextureUrl = null;
    this.previousTextureUrl = null;
    this.currentTextureWidth = 1;
    this.currentTextureHeight = 1;
    this.currentContourTextureWidth = 1;
    this.currentContourTextureHeight = 1;
    this.pendingFrameUrl = null;
    this.quadSignature = null;
    this.gl = null;
    this.map = null;
  }
}
