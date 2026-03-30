import maplibregl from "maplibre-gl";

import type { LegendPayload } from "@/components/map-legend";
import type { GridManifestResponse } from "@/lib/api";

export const GRID_WEBGL_LAYER_ID = "twf-grid-webgl";

const GRID_FRAME_CACHE_BUDGET_DESKTOP_BYTES = 256 * 1024 * 1024;
const GRID_FRAME_CACHE_BUDGET_MOBILE_BYTES = 128 * 1024 * 1024;
const GRID_TEXTURE_CACHE_BUDGET_DESKTOP_BYTES = 128 * 1024 * 1024;
const GRID_TEXTURE_CACHE_BUDGET_MOBILE_BYTES = 64 * 1024 * 1024;
const GRID_TEXTURE_WARM_LIMIT = 8;
const GRID_LUT_SIZE = 4096;
const MERCATOR_HALF_WORLD = 20037508.342789244;
// Mipmap filtering on the data texture is disabled because the texture stores
// uint16-encoded values split across two bytes (R=low, G=high).  GPU mipmap
// generation averages the channels independently, which produces incorrect
// decoded values whenever the low byte wraps around a 256-boundary.  Those
// incorrect values then map to wrong LUT colours, creating visible
// contour-line artefacts at specific temperature/value thresholds.
//
// Smoothness is provided instead by LINEAR filtering on the LUT texture and
// by LINEAR min/mag filtering on the data texture (which only affects
// immediately adjacent pixels, keeping byte-boundary artefacts negligible).
const MIPMAP_FILTER_COLOR_MAP_IDS = new Set<string>([]);
const TRANSPARENT_BELOW_MIN_BY_COLOR_MAP_ID = new Map<string, number>([
  ["precip_total", 0.01],
  ["snowfall_total", 0.1],
]);

export type GridFrameVisiblePayload = {
  frameHour: number;
  selectionEpoch?: number;
  selectionKey?: string;
};

export type GridWebglLayerConfig = {
  active: boolean;
  manifest: GridManifestResponse | null;
  frameUrl: string | null;
  frameHour: number | null;
  legend: LegendPayload | null;
  opacity: number;
  overlayFadeOutZoom?: { start: number; end: number } | null;
  selectionEpoch: number;
  selectionKey: string;
  prefetchUrls?: string[];
  onFrameVisible?: ((payload: GridFrameVisiblePayload) => void) | null;
  onFrameReady?: ((frameUrl: string) => void) | null;
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
  const entries = Array.isArray(legend?.entries)
    ? legend.entries
      .map((entry) => ({ value: Number(entry.value), rgba: hexToRgba(entry.color) }))
      .filter((entry) => Number.isFinite(entry.value))
      .sort((left, right) => left.value - right.value)
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

function isMobileDevice(): boolean {
  if (typeof navigator === "undefined") {
    return false;
  }
  return /android|iphone|ipad|ipod|mobile/i.test(navigator.userAgent);
}

function resolveFrameCacheBudgetBytes(): number {
  return isMobileDevice() ? GRID_FRAME_CACHE_BUDGET_MOBILE_BYTES : GRID_FRAME_CACHE_BUDGET_DESKTOP_BYTES;
}

function resolveTextureCacheBudgetBytes(): number {
  return isMobileDevice() ? GRID_TEXTURE_CACHE_BUDGET_MOBILE_BYTES : GRID_TEXTURE_CACHE_BUDGET_DESKTOP_BYTES;
}

function expectedPackedFrameByteLength(width: number, height: number): number {
  return Math.max(0, Math.floor(width) * Math.floor(height) * 2);
}

function shouldUseMipmapFiltering(manifest: GridManifestResponse | null): boolean {
  const colorMapId = String(manifest?.palette?.color_map_id ?? "").trim().toLowerCase();
  return MIPMAP_FILTER_COLOR_MAP_IDS.has(colorMapId);
}

function transparentBelowMinForManifest(manifest: GridManifestResponse | null): number {
  const colorMapId = String(manifest?.palette?.color_map_id ?? "").trim().toLowerCase();
  return TRANSPARENT_BELOW_MIN_BY_COLOR_MAP_ID.get(colorMapId) ?? Number.NEGATIVE_INFINITY;
}

function powerNormGammaForManifest(manifest: GridManifestResponse | null): number {
  const gamma = Number(manifest?.palette?.power_norm_gamma ?? 1);
  return Number.isFinite(gamma) && gamma > 0 ? gamma : 1;
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
  lastUsedAt: number;
};

type CachedTexture = {
  texture: WebGLTexture;
  lastUsedAt: number;
  bytes: number;
};

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
};

export class GridWebglLayerController {
  private readonly frameCacheBudgetBytes = resolveFrameCacheBudgetBytes();
  private readonly textureCacheBudgetBytes = resolveTextureCacheBudgetBytes();
  private map: maplibregl.Map | null = null;
  private gl: WebGLRenderingContext | WebGL2RenderingContext | null = null;
  private active = false;
  private manifest: GridManifestResponse | null = null;
  private frameUrl: string | null = null;
  private frameHour: number | null = null;
  private legend: LegendPayload | null = null;
  private opacity = 1;
  private overlayFadeOutZoom: { start: number; end: number } | null = null;
  private selectionEpoch = 0;
  private selectionKey = "";
  private prefetchUrls: string[] = [];
  private onFrameVisible: ((payload: GridFrameVisiblePayload) => void) | null = null;
  private onFrameReady: ((frameUrl: string) => void) | null = null;
  private frameCache = new Map<string, CachedFrame>();
  private frameCacheBytes = 0;
  private invalidFrameUrls = new Set<string>();
  private textureCache = new Map<string, CachedTexture>();
  private textureCacheBytes = 0;
  private frameFetches = new Map<string, Promise<Uint8Array<ArrayBufferLike> | null>>();
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
  private hasPreviousTexture = false;
  private transitionStartedAt = 0;
  private transitionDurationMs = 0;
  private quadSignature: string | null = null;

  createLayer(): maplibregl.CustomLayerInterface {
    return {
      id: GRID_WEBGL_LAYER_ID,
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
    if (map.getLayer(GRID_WEBGL_LAYER_ID)) {
      return;
    }
    const resolvedBeforeId = beforeId && map.getLayer(beforeId) ? beforeId : undefined;
    map.addLayer(this.createLayer(), resolvedBeforeId);
  }

  update(config: GridWebglLayerConfig) {
    if (config.selectionKey !== this.selectionKey) {
      this.invalidFrameUrls.clear();
      this.textureWarmQueue = [];
      this.textureWarmQueued.clear();
    }
    this.active = config.active;
    this.manifest = config.manifest;
    this.frameUrl = config.frameUrl;
    this.frameHour = Number.isFinite(config.frameHour) ? Number(config.frameHour) : null;
    this.opacity = config.opacity;
    this.overlayFadeOutZoom = config.overlayFadeOutZoom ?? null;
    this.selectionEpoch = config.selectionEpoch;
    this.selectionKey = config.selectionKey;
    this.prefetchUrls = Array.isArray(config.prefetchUrls) ? config.prefetchUrls.filter(Boolean) : [];
    this.onFrameVisible = config.onFrameVisible ?? null;
    this.onFrameReady = config.onFrameReady ?? null;
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

    const prioritizedPrefetchUrls = this.prefetchUrls.slice(0, GRID_TEXTURE_WARM_LIMIT);
    this.pruneTextureWarmQueue(new Set([this.frameUrl, ...prioritizedPrefetchUrls]));

    if (nextSignature !== this.currentFrameSignature) {
      this.currentFrameSignature = nextSignature;
      this.visibleNotifiedSignature = null;
      void this.ensureFrameLoaded(this.frameUrl, nextSignature);
    }

    this.scheduleTextureWarm(this.frameUrl, "high");
    for (let index = 0; index < prioritizedPrefetchUrls.length; index += 1) {
      const prefetchUrl = prioritizedPrefetchUrls[index];
      this.scheduleTextureWarm(prefetchUrl, index < 2 ? "high" : "normal");
    }
    this.map?.triggerRepaint();
  }

  remove(map?: maplibregl.Map | null) {
    const target = map ?? this.map;
    if (target?.getLayer(GRID_WEBGL_LAYER_ID)) {
      target.removeLayer(GRID_WEBGL_LAYER_ID);
    }
    this.disposeGlResources();
  }

  private buildFrameSignature(frameUrl: string | null): string | null {
    if (!frameUrl) {
      return null;
    }
    return `${this.selectionEpoch}:${this.selectionKey}:${this.frameHour ?? "na"}:${frameUrl}`;
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
      vec4 colorizeSample(vec4 sample) {
        float low = floor(sample.r * 255.0 + 0.5);
        float high = floor(sample.g * 255.0 + 0.5);
        float encoded = low + high * 256.0;
        if (abs(encoded - u_nodata) < 0.5) {
          return vec4(0.0, 0.0, 0.0, 0.0);
        }
        float decoded = encoded * u_scale + u_offset;
        if (decoded <= u_transparentBelowMin) {
          return vec4(0.0, 0.0, 0.0, 0.0);
        }
        float denom = max(0.000001, u_valueMax - u_valueMin);
        float t = clamp((decoded - u_valueMin) / denom, 0.0, 1.0);
        if (u_powerNormGamma > 0.0 && u_powerNormGamma != 1.0) {
          t = pow(t, u_powerNormGamma);
        }
        return texture2D(u_lut, vec2(t, 0.5));
      }
      void main() {
        vec4 current = colorizeSample(texture2D(u_data, v_texCoord));
        vec4 previous = u_hasPrevious > 0.5
          ? colorizeSample(texture2D(u_prevData, v_texCoord))
          : current;
        vec4 mixed = mix(previous, current, clamp(u_mixAmount, 0.0, 1.0));
        if (mixed.a <= 0.0) {
          discard;
        }
        gl_FragColor = vec4(mixed.rgb, mixed.a * u_opacity);
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

    const existing = this.textureCache.get(frameUrl);
    if (existing) {
      existing.lastUsedAt = Date.now();
      return existing.texture;
    }
    if (!bytes) {
      return null;
    }

    const targetTexture = gl.createTexture();
    if (!targetTexture) {
      return null;
    }

    const width = Math.max(1, Math.floor(Number(grid.width) || 1));
    const height = Math.max(1, Math.floor(Number(grid.height) || 1));
    const expectedBytes = expectedPackedFrameByteLength(width, height);
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
    const useMipmaps = this.isWebGL2 && shouldUseMipmapFiltering(this.manifest);

    gl.bindTexture(gl.TEXTURE_2D, targetTexture);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texParameteri(
      gl.TEXTURE_2D,
      gl.TEXTURE_MIN_FILTER,
      useMipmaps ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR
    );
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

    if (this.isWebGL2) {
      const gl2 = gl as WebGL2RenderingContext;
      gl2.texImage2D(gl2.TEXTURE_2D, 0, gl2.RG8, width, height, 0, gl2.RG, gl2.UNSIGNED_BYTE, bytes);
    } else {
      const rgba = expandUint16BytesToRgba(bytes);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, rgba);
    }

    if (useMipmaps) {
      gl.generateMipmap(gl.TEXTURE_2D);
    }

    this.textureCache.set(frameUrl, {
      texture: targetTexture,
      lastUsedAt: Date.now(),
      bytes: bytes.byteLength,
    });
    this.textureCacheBytes += bytes.byteLength;
    this.evictTextureCache(frameUrl);
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
    const warmTexture = this.textureCache.get(frameUrl);
    if (warmTexture) {
      warmTexture.lastUsedAt = Date.now();
      this.activateFrameTexture(frameUrl, this.frameCache.get(frameUrl)?.bytes ?? new Uint8Array(), signature);
      return;
    }
    const cached = this.frameCache.get(frameUrl);
    if (cached) {
      cached.lastUsedAt = Date.now();
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
    const cached = this.frameCache.get(frameUrl);
    if (cached) {
      cached.lastUsedAt = Date.now();
      return cached.bytes;
    }
    const inFlight = this.frameFetches.get(frameUrl);
    if (inFlight) {
      return inFlight;
    }
    const request = fetch(frameUrl, { credentials: "omit" })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Grid frame request failed: ${response.status}`);
        }
        const bytes = new Uint8Array(await response.arrayBuffer());
        this.upsertFrameCache(frameUrl, bytes);
        this.onFrameReady?.(frameUrl);
        return bytes;
      })
      .catch(() => null)
      .finally(() => {
        this.frameFetches.delete(frameUrl);
      });
    this.frameFetches.set(frameUrl, request);
    return request;
  }

  private upsertFrameCache(frameUrl: string, bytes: Uint8Array<ArrayBufferLike>) {
    const existing = this.frameCache.get(frameUrl);
    if (existing) {
      this.frameCacheBytes -= existing.bytes.byteLength;
    }
    this.frameCache.set(frameUrl, { bytes, lastUsedAt: Date.now() });
    this.frameCacheBytes += bytes.byteLength;

    while (this.frameCacheBytes > this.frameCacheBudgetBytes && this.frameCache.size > 1) {
      let lruKey: string | null = null;
      let oldest = Number.POSITIVE_INFINITY;
      for (const [candidateKey, candidate] of this.frameCache.entries()) {
        if (candidate.lastUsedAt < oldest && candidateKey !== this.frameUrl) {
          oldest = candidate.lastUsedAt;
          lruKey = candidateKey;
        }
      }
      if (!lruKey) {
        break;
      }
      const evicted = this.frameCache.get(lruKey);
      if (!evicted) {
        break;
      }
      this.frameCache.delete(lruKey);
      this.frameCacheBytes -= evicted.bytes.byteLength;
    }
  }

  private evictTextureCache(preferredUrl?: string | null) {
    while (this.textureCacheBytes > this.textureCacheBudgetBytes && this.textureCache.size > 1) {
      let lruKey: string | null = null;
      let oldest = Number.POSITIVE_INFINITY;
      for (const [candidateKey, candidate] of this.textureCache.entries()) {
        if (
          candidateKey === preferredUrl
          || candidateKey === this.currentTextureUrl
          || candidateKey === this.previousTextureUrl
          || this.textureWarmQueued.has(candidateKey)
        ) {
          continue;
        }
        if (candidate.lastUsedAt < oldest) {
          oldest = candidate.lastUsedAt;
          lruKey = candidateKey;
        }
      }
      if (!lruKey) {
        break;
      }
      const evicted = this.textureCache.get(lruKey);
      if (!evicted) {
        break;
      }
      this.textureCache.delete(lruKey);
      this.textureCacheBytes -= evicted.bytes;
      this.gl?.deleteTexture(evicted.texture);
    }
  }

  private pruneTextureWarmQueue(desiredUrls: Set<string>) {
    this.textureWarmQueue = this.textureWarmQueue.filter((candidate) => desiredUrls.has(candidate));
    this.textureWarmQueued = new Set(this.textureWarmQueue);
  }

  private scheduleTextureWarm(frameUrl: string | null, priority: "high" | "normal" = "normal") {
    const normalized = String(frameUrl ?? "").trim();
    if (!normalized || this.invalidFrameUrls.has(normalized) || this.textureCache.has(normalized)) {
      return;
    }
    if (this.textureWarmQueued.has(normalized)) {
      if (priority === "high") {
        this.textureWarmQueue = [normalized, ...this.textureWarmQueue.filter((candidate) => candidate !== normalized)];
      }
    } else {
      this.textureWarmQueued.add(normalized);
      if (priority === "high") {
        this.textureWarmQueue.unshift(normalized);
      } else {
        this.textureWarmQueue.push(normalized);
      }
    }
    if (this.textureWarmQueue.length > GRID_TEXTURE_WARM_LIMIT) {
      const trimmed = this.textureWarmQueue.slice(0, GRID_TEXTURE_WARM_LIMIT);
      this.textureWarmQueue = trimmed;
      this.textureWarmQueued = new Set(trimmed);
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
    while (this.textureWarmQueue.length > 0) {
      const nextUrl = this.textureWarmQueue.shift() ?? "";
      this.textureWarmQueued.delete(nextUrl);
      if (!nextUrl || this.invalidFrameUrls.has(nextUrl) || this.textureCache.has(nextUrl)) {
        continue;
      }
      const bytes = await this.fetchFrameBytes(nextUrl);
      if (!bytes || !this.gl) {
        continue;
      }
      this.createTextureFromBytes(nextUrl, bytes);
      this.map?.triggerRepaint();
      break;
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
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

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
    const elapsed = performance.now() - this.transitionStartedAt;
    const mixAmount = this.hasPreviousTexture
      ? Math.max(0, Math.min(1, elapsed / Math.max(1, this.transitionDurationMs)))
      : 1;
    gl.uniform1f(bindings.mixLocation, mixAmount);
    gl.uniform1f(bindings.hasPrevLocation, this.hasPreviousTexture ? 1 : 0);

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
    this.pendingFrameUrl = null;
    this.quadSignature = null;
    this.gl = null;
    this.map = null;
  }
}
