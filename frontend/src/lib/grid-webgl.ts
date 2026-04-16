import maplibregl from "maplibre-gl";

import type { LegendPayload } from "@/components/map-legend";
import type { GridManifestResponse } from "@/lib/api";
import { startNetworkTimer, trackClientProcessingDuration, trackNetworkFetchDuration } from "@/lib/network-diagnostics";

export const GRID_WEBGL_LAYER_ID = "twf-grid-webgl";

const GRID_FRAME_CACHE_BUDGET_DESKTOP_BYTES = 768 * 1024 * 1024;
const GRID_FRAME_CACHE_BUDGET_MOBILE_BYTES = 384 * 1024 * 1024;
const GRID_TEXTURE_CACHE_BUDGET_DESKTOP_BYTES = 512 * 1024 * 1024;
const GRID_TEXTURE_CACHE_BUDGET_MOBILE_BYTES = 256 * 1024 * 1024;
const GRID_TEXTURE_WARM_LIMIT = 12;
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
const GRID_LUT_SIZE = 4096;
const MERCATOR_HALF_WORLD = 20037508.342789244;
const TRANSPARENT_BELOW_MIN_BY_COLOR_MAP_ID = new Map<string, number>([
  ["precip_total", 0.01],
  ["snowfall_total", 0.1],
  ["pwat", 0.05],
]);

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
  rasterPaint?: GridRasterPaint | null;
  /** When true, the controller deprioritizes background texture warming to
   *  avoid competing with the animation/scrub for main-thread and GPU time. */
  isAnimating?: boolean;
  onFrameVisible?: ((payload: GridFrameVisiblePayload) => void) | null;
  onFrameReady?: ((frameUrl: string) => void) | null;
  onFrameEvicted?: ((frameUrl: string) => void) | null;
};

function mercatorXFromMeters(x: number): number {
  return (x + MERCATOR_HALF_WORLD) / (2 * MERCATOR_HALF_WORLD);
}

function mercatorYFromMeters(y: number): number {
  return (MERCATOR_HALF_WORLD - y) / (2 * MERCATOR_HALF_WORLD);
}

function buildQuadVertices(bbox: [number, number, number, number]): Float32Array {
  const [west, south, east, north] = bbox;
  const left = mercatorXFromMeters(west);
  const right = mercatorXFromMeters(east);
  const top = mercatorYFromMeters(north);
  const bottom = mercatorYFromMeters(south);
  return new Float32Array([
    left, top,
    right, top,
    left, bottom,
    right, bottom,
  ]);
}

function buildQuadTexCoords(): Float32Array {
  return new Float32Array([
    0, 0,
    1, 0,
    0, 1,
    1, 1,
  ]);
}

function hexToRgba(color: string): [number, number, number, number] {
  const normalized = color.trim().replace(/^#/, "");
  if (normalized.length !== 6 && normalized.length !== 8) {
    return [0, 0, 0, 0];
  }
  const r = Number.parseInt(normalized.slice(0, 2), 16);
  const g = Number.parseInt(normalized.slice(2, 4), 16);
  const b = Number.parseInt(normalized.slice(4, 6), 16);
  const a = normalized.length === 8 ? Number.parseInt(normalized.slice(6, 8), 16) : 255;
  return [
    Number.isFinite(r) ? r : 0,
    Number.isFinite(g) ? g : 0,
    Number.isFinite(b) ? b : 0,
    Number.isFinite(a) ? a : 255,
  ];
}

function lerpColor(left: [number, number, number, number], right: [number, number, number, number], t: number) {
  return [
    Math.round(left[0] + (right[0] - left[0]) * t),
    Math.round(left[1] + (right[1] - left[1]) * t),
    Math.round(left[2] + (right[2] - left[2]) * t),
    Math.round(left[3] + (right[3] - left[3]) * t),
  ] as [number, number, number, number];
}

function buildLegendLut(legend: LegendPayload | null, size = GRID_LUT_SIZE): { pixels: Uint8Array; min: number; max: number } {
  const normalizedKind = String(legend?.kind ?? "").trim().toLowerCase();
  const isCategorical = normalizedKind === "indexed" || normalizedKind === "categorical";
  const isDiscrete = normalizedKind === "discrete";
  const entries = Array.isArray(legend?.entries)
    ? legend.entries
      .map((entry) => ({ value: Number(entry.value), rgba: hexToRgba(entry.color) }))
      .filter((entry) => Number.isFinite(entry.value))
      .sort((left, right) => (isCategorical ? 0 : left.value - right.value))
    : [];

  const pixels = new Uint8Array(size * 4);
  if (entries.length === 0) {
    for (let index = 0; index < size; index += 1) {
      const offset = index * 4;
      pixels[offset] = 0;
      pixels[offset + 1] = 0;
      pixels[offset + 2] = 0;
      pixels[offset + 3] = 0;
    }
    return { pixels, min: 0, max: 1 };
  }

  if (isCategorical) {
    const maxIndex = Math.max(0, entries.length - 1);
    const denom = Math.max(1, size - 1);
    for (let index = 0; index < size; index += 1) {
      const paletteIndex = Math.min(maxIndex, Math.round((maxIndex * index) / denom));
      const rgba = entries[paletteIndex]?.rgba ?? [0, 0, 0, 0];
      const offset = index * 4;
      pixels[offset] = rgba[0];
      pixels[offset + 1] = rgba[1];
      pixels[offset + 2] = rgba[2];
      pixels[offset + 3] = rgba[3];
    }
    return { pixels, min: 0, max: maxIndex };
  }

  if (isDiscrete) {
    const min = entries[0].value;
    const max = entries[entries.length - 1].value;
    const denom = Math.max(1e-6, max - min);

    for (let index = 0; index < size; index += 1) {
      const value = min + (denom * index) / Math.max(1, size - 1);
      let selected = entries[0];
      for (let cursor = 0; cursor < entries.length; cursor += 1) {
        const current = entries[cursor];
        const next = entries[cursor + 1];
        selected = current;
        if (!next || value < next.value) {
          break;
        }
      }
      const offset = index * 4;
      pixels[offset] = selected.rgba[0];
      pixels[offset + 1] = selected.rgba[1];
      pixels[offset + 2] = selected.rgba[2];
      pixels[offset + 3] = selected.rgba[3];
    }

    return { pixels, min, max };
  }

  const min = entries[0].value;
  const max = entries[entries.length - 1].value;
  const denom = Math.max(1e-6, max - min);

  for (let index = 0; index < size; index += 1) {
    const value = min + (denom * index) / Math.max(1, size - 1);
    let left = entries[0];
    let right = entries[entries.length - 1];
    for (let cursor = 0; cursor < entries.length - 1; cursor += 1) {
      const current = entries[cursor];
      const next = entries[cursor + 1];
      if (value >= current.value && value <= next.value) {
        left = current;
        right = next;
        break;
      }
      if (value < entries[0].value) {
        left = entries[0];
        right = entries[0];
        break;
      }
    }
    const span = Math.max(1e-6, right.value - left.value);
    const t = right.value <= left.value ? 0 : (value - left.value) / span;
    const rgba = lerpColor(left.rgba, right.rgba, Math.max(0, Math.min(1, t)));
    const offset = index * 4;
    pixels[offset] = rgba[0];
    pixels[offset + 1] = rgba[1];
    pixels[offset + 2] = rgba[2];
    pixels[offset + 3] = rgba[3];
  }

  return { pixels, min, max };
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

function supportCoverageThresholdForManifest(manifest: GridManifestResponse | null): number {
  const threshold = Number(manifest?.display_prep?.support_coverage_threshold);
  if (Number.isFinite(threshold)) {
    return Math.max(0, Math.min(1, threshold));
  }
  return 0;
}

function isObservedGridManifest(manifest: GridManifestResponse | null): boolean {
  return String(manifest?.model ?? "").trim().toLowerCase() === "mrms";
}

function transparentZeroForManifest(manifest: GridManifestResponse | null): boolean {
  return Boolean(manifest?.palette?.transparent_zero);
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
  supportCoverageThresholdLocation: WebGLUniformLocation | null;
  transparentZeroLocation: WebGLUniformLocation | null;
  contrastFactorLocation: WebGLUniformLocation | null;
  saturationFactorLocation: WebGLUniformLocation | null;
  brightnessLowLocation: WebGLUniformLocation | null;
  brightnessHighLocation: WebGLUniformLocation | null;
};

export class GridWebglLayerController {
  private readonly layerId: string;
  private readonly frameCacheBudgetBytes = resolveFrameCacheBudgetBytes();
  private readonly textureCacheBudgetBytes = resolveTextureCacheBudgetBytes();
  private readonly combinedCacheBudgetBytes = resolveCombinedCacheBudgetBytes();
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
  private animating = false;
  private onFrameVisible: ((payload: GridFrameVisiblePayload) => void) | null = null;
  private onFrameReady: ((frameUrl: string) => void) | null = null;
  private onFrameEvicted: ((frameUrl: string) => void) | null = null;
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
  private currentFrameSignature: string | null = null;
  private currentTextureSignature: string | null = null;
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
  private previousTexture: WebGLTexture | null = null;
  private currentTextureWidth = 1;
  private currentTextureHeight = 1;
  private hasPreviousTexture = false;
  private transitionStartedAt = 0;
  private transitionDurationMs = 0;
  private quadSignature: string | null = null;

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
      render: (_gl, matrix) => {
        this.render(matrix as number[]);
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
      // Abort all in-flight fetches for the previous selection.
      for (const controller of this.frameFetchAbortControllers.values()) {
        controller.abort();
      }
      // Clear the current texture so the render loop skips drawing until the
      // new frame is ready. Without this, the next render applies the incoming
      // variable's new LUT to the previous variable's raw data bytes, producing
      // a mis-colored flash (typically orange from the colormap edge values).
      this.currentTexture = null;
      this.currentTextureSignature = null;
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
    this.animating = config.isAnimating ?? false;
    this.onFrameVisible = config.onFrameVisible ?? null;
    this.onFrameReady = config.onFrameReady ?? null;
    this.onFrameEvicted = config.onFrameEvicted ?? null;
    if (this.legend !== config.legend) {
      this.legend = config.legend;
      this.rebuildLegendTexture();
    }

    const nextSignature = this.buildFrameSignature(this.frameUrl);
    if (!this.active || !this.frameUrl || !this.manifest) {
      this.currentFrameSignature = nextSignature;
      this.map?.triggerRepaint();
      return;
    }

    const prioritizedPrefetchUrls = this.prefetchUrls.slice(0, this.textureWarmLimit());
    this.pruneTextureWarmQueue(new Set([this.frameUrl, ...prioritizedPrefetchUrls]));

    if (nextSignature !== this.currentFrameSignature) {
      this.currentFrameSignature = nextSignature;
      this.visibleNotifiedSignature = null;
      void this.ensureFrameLoaded(this.frameUrl, nextSignature);
    }

    this.scheduleTextureWarm(this.frameUrl, "high");
    const highPriorityCount = this.textureWarmHighPriorityCount();
    for (let index = 0; index < prioritizedPrefetchUrls.length; index += 1) {
      const prefetchUrl = prioritizedPrefetchUrls[index];
      this.scheduleTextureWarm(prefetchUrl, index < highPriorityCount ? "high" : "normal");
    }
    this.map?.triggerRepaint();
  }

  remove(map?: maplibregl.Map | null) {
    const target = map ?? this.map;
    if (target?.getLayer(this.layerId)) {
      target.removeLayer(this.layerId);
    }
    this.disposeGlResources();
  }

  private buildFrameSignature(frameUrl: string | null): string | null {
    if (!frameUrl) {
      return null;
    }
    return `${this.selectionEpoch}:${this.selectionKey}:${this.frameHour ?? "na"}:${frameUrl}`;
  }

  private textureWarmLimit(): number {
    if (isObservedGridManifest(this.manifest)) {
      return resolveObservedTextureWarmLimit();
    }
    return GRID_TEXTURE_WARM_LIMIT;
  }

  private textureWarmBatchSize(): number {
    if (isObservedGridManifest(this.manifest)) {
      return resolveObservedTextureWarmBatchSize(this.animating);
    }
    return resolveForecastTextureWarmBatchSize(this.animating);
  }

  private textureWarmHighPriorityCount(): number {
    return isObservedGridManifest(this.manifest) ? resolveObservedTextureHighPriorityCount() : 4;
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
      void main() {
        v_texCoord = a_texCoord;
        gl_Position = u_matrix * vec4(a_pos, 0.0, 1.0);
      }
    `;
    const fragmentSource = `
      precision mediump float;
      varying vec2 v_texCoord;
      uniform sampler2D u_data;
      uniform sampler2D u_prevData;
      uniform sampler2D u_lut;
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
      uniform float u_supportCoverageThreshold;
      uniform float u_transparentZero;
      uniform float u_contrastFactor;
      uniform float u_saturationFactor;
      uniform float u_brightnessLow;
      uniform float u_brightnessHigh;

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
        float v00 = decodeSample(texture2D(tex, base));
        float v10 = decodeSample(texture2D(tex, base + vec2(step.x, 0.0)));
        float v01 = decodeSample(texture2D(tex, base + vec2(0.0, step.y)));
        float v11 = decodeSample(texture2D(tex, base + step));

        // Count valid (non-nodata) neighbours.
        float w00 = v00 > -1e29 ? 1.0 : 0.0;
        float w10 = v10 > -1e29 ? 1.0 : 0.0;
        float w01 = v01 > -1e29 ? 1.0 : 0.0;
        float w11 = v11 > -1e29 ? 1.0 : 0.0;
        float totalWeight = w00 + w10 + w01 + w11;

        if (totalWeight <= 0.0) {
          return vec4(0.0, 0.0, 0.0, 0.0);
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

        // Normalise to [0,1] and apply power-norm gamma.
        float denom = max(0.000001, u_valueMax - u_valueMin);
        float t = clamp((decoded - u_valueMin) / denom, 0.0, 1.0);
        if (u_powerNormGamma > 0.0 && u_powerNormGamma != 1.0) {
          t = pow(t, u_powerNormGamma);
        }

        if (u_transparentBelowMin > -1e20 && u_supportCoverageThreshold > 0.0) {
          float sw00 = v00 > u_transparentBelowMin ? bw00 : 0.0;
          float sw10 = v10 > u_transparentBelowMin ? bw10 : 0.0;
          float sw01 = v01 > u_transparentBelowMin ? bw01 : 0.0;
          float sw11 = v11 > u_transparentBelowMin ? bw11 : 0.0;
          float supportCoverage = (sw00 + sw10 + sw01 + sw11) / wSum;
          if (supportCoverage < u_supportCoverageThreshold) {
            return vec4(0.0, 0.0, 0.0, 0.0);
          }
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

        float v00 = decodeSample(texture2D(tex, base));
        float v10 = decodeSample(texture2D(tex, base + vec2(step.x, 0.0)));
        float v01 = decodeSample(texture2D(tex, base + vec2(0.0, step.y)));
        float v11 = decodeSample(texture2D(tex, base + step));

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
        float decoded = decodeSample(texture2D(tex, uv));
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

      void main() {
        vec4 current = u_categorical > 0.5
          ? (u_categoricalNearest > 0.5
            ? sampleCategoricalNearest(u_data, v_texCoord)
            : sampleCategorical(u_data, v_texCoord))
          : sampleBilinear(u_data, v_texCoord);
        vec4 previous = u_hasPrevious > 0.5
          ? (u_categorical > 0.5
            ? (u_categoricalNearest > 0.5
              ? sampleCategoricalNearest(u_prevData, v_texCoord)
              : sampleCategorical(u_prevData, v_texCoord))
            : sampleBilinear(u_prevData, v_texCoord))
          : current;
        vec4 mixed = mix(previous, current, clamp(u_mixAmount, 0.0, 1.0));
        if (mixed.a <= 0.0) {
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

        float finalAlpha = mixed.a * u_opacity;
        gl_FragColor = vec4(rgb * finalAlpha, finalAlpha);
      }
    `;

    this.program = createProgram(gl, vertexSource, fragmentSource);
    this.vertexBuffer = gl.createBuffer();
    this.texCoordBuffer = gl.createBuffer();
    this.lutTexture = gl.createTexture();

    if (!this.vertexBuffer || !this.texCoordBuffer || !this.lutTexture) {
      throw new Error("Failed to initialize grid WebGL resources");
    }

    this.bindings = {
      positionLocation: gl.getAttribLocation(this.program, "a_pos"),
      texCoordLocation: gl.getAttribLocation(this.program, "a_texCoord"),
      matrixLocation: gl.getUniformLocation(this.program, "u_matrix"),
      scaleLocation: gl.getUniformLocation(this.program, "u_scale"),
      offsetLocation: gl.getUniformLocation(this.program, "u_offset"),
      nodataLocation: gl.getUniformLocation(this.program, "u_nodata"),
      valueMinLocation: gl.getUniformLocation(this.program, "u_valueMin"),
      valueMaxLocation: gl.getUniformLocation(this.program, "u_valueMax"),
      opacityLocation: gl.getUniformLocation(this.program, "u_opacity"),
      transparentBelowMinLocation: gl.getUniformLocation(this.program, "u_transparentBelowMin"),
      dataLocation: gl.getUniformLocation(this.program, "u_data"),
      prevDataLocation: gl.getUniformLocation(this.program, "u_prevData"),
      lutLocation: gl.getUniformLocation(this.program, "u_lut"),
      mixLocation: gl.getUniformLocation(this.program, "u_mixAmount"),
      hasPrevLocation: gl.getUniformLocation(this.program, "u_hasPrevious"),
      powerNormGammaLocation: gl.getUniformLocation(this.program, "u_powerNormGamma"),
      dataEncodingLocation: gl.getUniformLocation(this.program, "u_dataEncoding"),
      texSizeLocation: gl.getUniformLocation(this.program, "u_texSize"),
      categoricalLocation: gl.getUniformLocation(this.program, "u_categorical"),
      categoricalNearestLocation: gl.getUniformLocation(this.program, "u_categoricalNearest"),
      supportCoverageThresholdLocation: gl.getUniformLocation(this.program, "u_supportCoverageThreshold"),
      transparentZeroLocation: gl.getUniformLocation(this.program, "u_transparentZero"),
      contrastFactorLocation: gl.getUniformLocation(this.program, "u_contrastFactor"),
      saturationFactorLocation: gl.getUniformLocation(this.program, "u_saturationFactor"),
      brightnessLowLocation: gl.getUniformLocation(this.program, "u_brightnessLow"),
      brightnessHighLocation: gl.getUniformLocation(this.program, "u_brightnessHigh"),
    };

    gl.bindBuffer(gl.ARRAY_BUFFER, this.texCoordBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, buildQuadTexCoords(), gl.STATIC_DRAW);
    this.uploadQuadVerticesIfNeeded();
  }

  private resolveBbox(): [number, number, number, number] {
    const bbox = this.manifest?.bbox;
    if (Array.isArray(bbox) && bbox.length === 4) {
      return [Number(bbox[0]), Number(bbox[1]), Number(bbox[2]), Number(bbox[3])];
    }
    return [-14922340, 2714341, -6679169, 7361866];
  }

  private uploadQuadVerticesIfNeeded() {
    const gl = this.gl;
    if (!gl || !this.vertexBuffer) {
      return;
    }
    const bbox = this.resolveBbox();
    const signature = bbox.join(",");
    if (signature === this.quadSignature) {
      return;
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, this.vertexBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, buildQuadVertices(bbox), gl.STATIC_DRAW);
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

  private createTextureFromBytes(frameUrl: string, bytes: Uint8Array<ArrayBufferLike> | null): WebGLTexture | null {
    const gl = this.gl;
    const grid = this.manifest?.grid;
    if (!gl || !grid) {
      return null;
    }

    const existing = this.textureCache.touch(frameUrl);
    if (existing) {
      return existing.texture;
    }
    if (!bytes) {
      return null;
    }
    const prepareStartedAtMs = startNetworkTimer();
    const diagnosticMeta: Record<string, unknown> = {
      ...this.buildDiagnosticMeta(frameUrl),
      payload_bytes: bytes.byteLength,
    };

    const targetTexture = gl.createTexture();
    if (!targetTexture) {
      return null;
    }

    const selectedLod = this.resolveSelectedLod();
    const width = selectedLod?.width ?? Math.max(1, Math.floor(Number(grid.width) || 1));
    const height = selectedLod?.height ?? Math.max(1, Math.floor(Number(grid.height) || 1));
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

    const preparedUpload = preparePackedGridUpload(gl, bytes, width, height, gridDtype);
    diagnosticMeta.max_texture_size = resolveMaxTextureSize(gl);
    diagnosticMeta.upload_width = preparedUpload.width;
    diagnosticMeta.upload_height = preparedUpload.height;
    diagnosticMeta.upload_bytes = preparedUpload.bytes.byteLength;
    diagnosticMeta.texture_downsampled = preparedUpload.downsampled;

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
      duration_ms: startNetworkTimer() - prepareStartedAtMs,
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
    this.evictCaches(frameUrl);
    return targetTexture;
  }

  private activateFrameTexture(frameUrl: string, bytes: Uint8Array<ArrayBufferLike> | null, signature: string) {
    const targetTexture = this.createTextureFromBytes(frameUrl, bytes);
    if (!targetTexture) {
      this.pendingFrameBytes = bytes;
      this.pendingFrameSignature = signature;
      this.pendingFrameUrl = frameUrl;
      return;
    }

    this.pendingFrameBytes = bytes ?? this.frameCache.get(frameUrl)?.bytes ?? null;
    this.pendingFrameSignature = signature;
    this.pendingFrameUrl = frameUrl;
    this.previousTexture = null;
    this.previousTextureUrl = null;
    this.hasPreviousTexture = false;
    this.currentTexture = targetTexture;
    this.currentTextureUrl = frameUrl;
    const cachedTexture = this.textureCache.get(frameUrl);
    this.currentTextureWidth = cachedTexture?.width ?? Math.max(1, Math.floor(Number(this.manifest?.grid?.width) || 1));
    this.currentTextureHeight = cachedTexture?.height ?? Math.max(1, Math.floor(Number(this.manifest?.grid?.height) || 1));
    this.currentTextureSignature = signature;
    this.transitionStartedAt = performance.now();
    this.map?.triggerRepaint();
  }

  private async ensureFrameLoaded(frameUrl: string, signature: string | null) {
    if (!signature) {
      return;
    }
    if (this.invalidFrameUrls.has(frameUrl)) {
      return;
    }
    const warmTexture = this.textureCache.touch(frameUrl);
    if (warmTexture) {
      this.activateFrameTexture(frameUrl, this.frameCache.get(frameUrl)?.bytes ?? new Uint8Array(), signature);
      return;
    }
    const cached = this.frameCache.touch(frameUrl);
    if (cached) {
      this.activateFrameTexture(frameUrl, cached.bytes, signature);
      return;
    }

    try {
      const bytes = await this.fetchFrameBytes(frameUrl);
      if (!bytes) {
        return;
      }
      if (signature !== this.currentFrameSignature) {
        return;
      }
      this.activateFrameTexture(frameUrl, bytes, signature);
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
      return cached.bytes;
    }
    const inFlight = this.frameFetches.get(frameUrl);
    if (inFlight) {
      return inFlight;
    }
    const abortController = new AbortController();
    this.frameFetchAbortControllers.set(frameUrl, abortController);
    const startedAtMs = startNetworkTimer();
    const request = fetch(frameUrl, { credentials: "omit", signal: abortController.signal })
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
        this.onFrameReady?.(frameUrl);
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
    this.frameCache.set(frameUrl, { bytes });
    this.frameCacheBytes += bytes.byteLength;
    this.evictCaches(frameUrl);
  }

  private combinedCacheBytes(): number {
    return this.frameCacheBytes + this.textureCacheBytes;
  }

  private evictFrameCacheEntry(preferredUrl?: string | null, duplicateOnly = false): boolean {
    const evicted = this.frameCache.evictLeastRecentlyUsed((candidateKey) => {
      if (
        candidateKey === preferredUrl
        || candidateKey === this.frameUrl
        || candidateKey === this.pendingFrameUrl
        || this.frameFetches.has(candidateKey)
      ) {
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
      (candidateKey) =>
        candidateKey === preferredUrl
        || candidateKey === this.currentTextureUrl
        || candidateKey === this.previousTextureUrl
        || this.textureWarmQueued.has(candidateKey),
    );
    if (!evicted) {
      return false;
    }
    this.textureCacheBytes -= evicted.value.bytes;
    this.gl?.deleteTexture(evicted.value.texture);
    if (!this.frameCache.has(evicted.key)) {
      this.onFrameEvicted?.(evicted.key);
    }
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
    // Abort in-flight fetches for URLs that are no longer in the desired set.
    for (const [fetchUrl, controller] of this.frameFetchAbortControllers) {
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
    // During animation/scrub, observed MRMS grids can now warm a little more
    // aggressively on desktop because per-frame upload cost is lower.
    const effectiveBatchSize = this.textureWarmBatchSize();
    const frameBudgetMs = resolveTextureWarmFrameBudgetMs(this.animating);
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
      this.map?.triggerRepaint();
    }

    if (this.textureWarmQueue.length > 0 && this.textureWarmRafId === null && typeof window !== "undefined") {
      this.textureWarmRafId = window.requestAnimationFrame(() => {
        this.textureWarmRafId = null;
        void this.pumpTextureWarmQueue();
      });
    }
  }

  private render(matrix: number[]) {
    const gl = this.gl;
    const program = this.program;
    const bindings = this.bindings;
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

    this.uploadQuadVerticesIfNeeded();
    this.uploadLutTexture();

    const zoom = this.map?.getZoom() ?? 0;
    const resolvedOpacity = resolveOpacity(this.opacity, zoom, this.overlayFadeOutZoom);
    if (resolvedOpacity <= 0) {
      return;
    }

    gl.useProgram(program);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.vertexBuffer);
    gl.enableVertexAttribArray(bindings.positionLocation);
    gl.vertexAttribPointer(bindings.positionLocation, 2, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.texCoordBuffer);
    gl.enableVertexAttribArray(bindings.texCoordLocation);
    gl.vertexAttribPointer(bindings.texCoordLocation, 2, gl.FLOAT, false, 0, 0);

    gl.uniformMatrix4fv(bindings.matrixLocation, false, matrix);
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
    gl.uniform1f(bindings.supportCoverageThresholdLocation, supportCoverageThresholdForManifest(this.manifest));
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

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.currentTexture);
    gl.uniform1i(bindings.dataLocation, 0);

    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.hasPreviousTexture ? this.previousTexture : this.currentTexture);
    gl.uniform1i(bindings.prevDataLocation, 1);

    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D, this.lutTexture);
    gl.uniform1i(bindings.lutLocation, 2);

    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    if (this.hasPreviousTexture && mixAmount < 1) {
      this.map?.triggerRepaint();
    } else if (mixAmount >= 1) {
      this.hasPreviousTexture = false;
    }

    if (
      this.onFrameVisible
      && this.currentTextureSignature
      && this.visibleNotifiedSignature !== this.currentTextureSignature
      && Number.isFinite(this.frameHour)
    ) {
      this.visibleNotifiedSignature = this.currentTextureSignature;
      this.onFrameVisible({
        frameHour: Number(this.frameHour),
        selectionEpoch: this.selectionEpoch,
        selectionKey: this.selectionKey,
      });
    }
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
    this.currentTexture = null;
    this.previousTexture = null;
    this.currentTextureUrl = null;
    this.previousTextureUrl = null;
    this.currentTextureWidth = 1;
    this.currentTextureHeight = 1;
    this.pendingFrameUrl = null;
    this.quadSignature = null;
    this.gl = null;
    this.map = null;
  }
}
