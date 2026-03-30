import maplibregl from "maplibre-gl";

import type { LegendPayload } from "@/components/map-legend";
import type { GridManifestResponse } from "@/lib/api";

export const GRID_WEBGL_LAYER_ID = "twf-grid-webgl";

const GRID_FRAME_CACHE_BUDGET_BYTES = 72 * 1024 * 1024;
const MERCATOR_HALF_WORLD = 20037508.342789244;

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

function buildLegendLut(legend: LegendPayload | null, size = 256): { pixels: Uint8Array; min: number; max: number } {
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

export class GridWebglLayerController {
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
  private frameCache = new Map<string, CachedFrame>();
  private frameCacheBytes = 0;
  private prefetchInFlight = new Set<string>();
  private frameAbortController: AbortController | null = null;
  private currentFrameSignature: string | null = null;
  private currentTextureSignature: string | null = null;
  private visibleNotifiedSignature: string | null = null;
  private pendingFrameBytes: Uint8Array<ArrayBufferLike> | null = null;
  private pendingFrameSignature: string | null = null;
  private isWebGL2 = false;
  private program: WebGLProgram | null = null;
  private vertexBuffer: WebGLBuffer | null = null;
  private texCoordBuffer: WebGLBuffer | null = null;
  private dataTexture: WebGLTexture | null = null;
  private lutTexture: WebGLTexture | null = null;
  private lutPixels: Uint8Array<ArrayBufferLike> = new Uint8Array(256 * 4);
  private lutMin = 0;
  private lutMax = 1;
  private lutDirty = true;

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
        if (this.pendingFrameBytes && this.pendingFrameSignature) {
          this.uploadFrameTexture(this.pendingFrameBytes, this.pendingFrameSignature);
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
    if (this.legend !== config.legend) {
      this.legend = config.legend;
      this.rebuildLegendTexture();
    }

    const nextSignature = this.buildFrameSignature(this.frameUrl);
    if (!this.active || !this.frameUrl || !this.manifest) {
      this.frameAbortController?.abort();
      this.currentFrameSignature = nextSignature;
      this.map?.triggerRepaint();
      return;
    }

    if (nextSignature !== this.currentFrameSignature) {
      this.currentFrameSignature = nextSignature;
      this.visibleNotifiedSignature = null;
      void this.ensureFrameLoaded(this.frameUrl, nextSignature);
    }

    for (const prefetchUrl of this.prefetchUrls) {
      void this.prefetchFrame(prefetchUrl);
    }
    this.map?.triggerRepaint();
  }

  remove(map?: maplibregl.Map | null) {
    this.frameAbortController?.abort();
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
      uniform sampler2D u_lut;
      uniform float u_scale;
      uniform float u_offset;
      uniform float u_nodata;
      uniform float u_valueMin;
      uniform float u_valueMax;
      uniform float u_opacity;
      void main() {
        vec4 sample = texture2D(u_data, v_texCoord);
        float low = floor(sample.r * 255.0 + 0.5);
        float high = floor(sample.g * 255.0 + 0.5);
        float encoded = low + high * 256.0;
        if (abs(encoded - u_nodata) < 0.5) {
          discard;
        }
        float decoded = encoded * u_scale + u_offset;
        float denom = max(0.000001, u_valueMax - u_valueMin);
        float t = clamp((decoded - u_valueMin) / denom, 0.0, 1.0);
        vec4 lut = texture2D(u_lut, vec2(t, 0.5));
        gl_FragColor = vec4(lut.rgb, lut.a * u_opacity);
      }
    `;

    this.program = createProgram(gl, vertexSource, fragmentSource);
    this.vertexBuffer = gl.createBuffer();
    this.texCoordBuffer = gl.createBuffer();
    this.dataTexture = gl.createTexture();
    this.lutTexture = gl.createTexture();

    if (!this.vertexBuffer || !this.texCoordBuffer || !this.dataTexture || !this.lutTexture) {
      throw new Error("Failed to initialize grid WebGL resources");
    }

    gl.bindBuffer(gl.ARRAY_BUFFER, this.vertexBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, buildQuadVertices(this.resolveBbox()), gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.texCoordBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, buildQuadTexCoords(), gl.STATIC_DRAW);
  }

  private resolveBbox(): [number, number, number, number] {
    const bbox = this.manifest?.bbox;
    if (Array.isArray(bbox) && bbox.length === 4) {
      return [Number(bbox[0]), Number(bbox[1]), Number(bbox[2]), Number(bbox[3])];
    }
    return [-14922340, 2714341, -6679169, 7361866];
  }

  private uploadQuadVertices() {
    const gl = this.gl;
    if (!gl || !this.vertexBuffer) {
      return;
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, this.vertexBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, buildQuadVertices(this.resolveBbox()), gl.STATIC_DRAW);
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
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, this.lutPixels);
    this.lutDirty = false;
  }

  private uploadFrameTexture(bytes: Uint8Array<ArrayBufferLike>, signature: string) {
    const gl = this.gl;
    const grid = this.manifest?.grid;
    if (!gl || !this.dataTexture || !grid) {
      this.pendingFrameBytes = bytes;
      this.pendingFrameSignature = signature;
      return;
    }

    const width = Math.max(1, Math.floor(Number(grid.width) || 1));
    const height = Math.max(1, Math.floor(Number(grid.height) || 1));
    gl.bindTexture(gl.TEXTURE_2D, this.dataTexture);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
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

    this.pendingFrameBytes = bytes;
    this.pendingFrameSignature = signature;
    this.currentTextureSignature = signature;
    this.map?.triggerRepaint();
  }

  private async ensureFrameLoaded(frameUrl: string, signature: string | null) {
    if (!signature) {
      return;
    }
    const cached = this.frameCache.get(frameUrl);
    if (cached) {
      cached.lastUsedAt = Date.now();
      this.uploadFrameTexture(cached.bytes, signature);
      return;
    }

    this.frameAbortController?.abort();
    const controller = new AbortController();
    this.frameAbortController = controller;

    try {
      const response = await fetch(frameUrl, { credentials: "omit", signal: controller.signal });
      if (!response.ok) {
        throw new Error(`Grid frame request failed: ${response.status}`);
      }
      const arrayBuffer = await response.arrayBuffer();
      if (controller.signal.aborted) {
        return;
      }
      const bytes = new Uint8Array(arrayBuffer);
      this.upsertFrameCache(frameUrl, bytes);
      if (signature !== this.currentFrameSignature) {
        return;
      }
      this.uploadFrameTexture(bytes, signature);
    } catch {
      if (controller.signal.aborted) {
        return;
      }
      this.currentTextureSignature = null;
      this.map?.triggerRepaint();
    }
  }

  private async prefetchFrame(frameUrl: string) {
    if (!frameUrl || this.frameCache.has(frameUrl) || this.prefetchInFlight.has(frameUrl)) {
      return;
    }
    this.prefetchInFlight.add(frameUrl);
    try {
      const response = await fetch(frameUrl, { credentials: "omit" });
      if (!response.ok) {
        return;
      }
      const bytes = new Uint8Array(await response.arrayBuffer());
      this.upsertFrameCache(frameUrl, bytes);
    } catch {
      // Best-effort warm path only.
    } finally {
      this.prefetchInFlight.delete(frameUrl);
    }
  }

  private upsertFrameCache(frameUrl: string, bytes: Uint8Array<ArrayBufferLike>) {
    const existing = this.frameCache.get(frameUrl);
    if (existing) {
      this.frameCacheBytes -= existing.bytes.byteLength;
    }
    this.frameCache.set(frameUrl, { bytes, lastUsedAt: Date.now() });
    this.frameCacheBytes += bytes.byteLength;

    while (this.frameCacheBytes > GRID_FRAME_CACHE_BUDGET_BYTES && this.frameCache.size > 1) {
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

  private render(matrix: number[]) {
    const gl = this.gl;
    const program = this.program;
    const grid = this.manifest?.grid;
    if (
      !gl
      || !program
      || !this.active
      || !grid
      || !this.dataTexture
      || !this.lutTexture
      || !this.vertexBuffer
      || !this.texCoordBuffer
      || !this.currentTextureSignature
    ) {
      return;
    }

    this.uploadQuadVertices();
    this.uploadLutTexture();

    const zoom = this.map?.getZoom() ?? 0;
    const resolvedOpacity = resolveOpacity(this.opacity, zoom, this.overlayFadeOutZoom);
    if (resolvedOpacity <= 0) {
      return;
    }

    gl.useProgram(program);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    const positionLocation = gl.getAttribLocation(program, "a_pos");
    const texCoordLocation = gl.getAttribLocation(program, "a_texCoord");
    const matrixLocation = gl.getUniformLocation(program, "u_matrix");
    const scaleLocation = gl.getUniformLocation(program, "u_scale");
    const offsetLocation = gl.getUniformLocation(program, "u_offset");
    const nodataLocation = gl.getUniformLocation(program, "u_nodata");
    const valueMinLocation = gl.getUniformLocation(program, "u_valueMin");
    const valueMaxLocation = gl.getUniformLocation(program, "u_valueMax");
    const opacityLocation = gl.getUniformLocation(program, "u_opacity");
    const dataLocation = gl.getUniformLocation(program, "u_data");
    const lutLocation = gl.getUniformLocation(program, "u_lut");

    gl.bindBuffer(gl.ARRAY_BUFFER, this.vertexBuffer);
    gl.enableVertexAttribArray(positionLocation);
    gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.texCoordBuffer);
    gl.enableVertexAttribArray(texCoordLocation);
    gl.vertexAttribPointer(texCoordLocation, 2, gl.FLOAT, false, 0, 0);

    gl.uniformMatrix4fv(matrixLocation, false, matrix);
    gl.uniform1f(scaleLocation, Number(grid.scale) || 1);
    gl.uniform1f(offsetLocation, Number(grid.offset) || 0);
    gl.uniform1f(nodataLocation, Number(grid.nodata) || 65535);
    gl.uniform1f(valueMinLocation, this.lutMin);
    gl.uniform1f(valueMaxLocation, this.lutMax);
    gl.uniform1f(opacityLocation, resolvedOpacity);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.dataTexture);
    gl.uniform1i(dataLocation, 0);

    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.lutTexture);
    gl.uniform1i(lutLocation, 1);

    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

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
      if (this.dataTexture) {
        gl.deleteTexture(this.dataTexture);
      }
      if (this.lutTexture) {
        gl.deleteTexture(this.lutTexture);
      }
    }
    this.program = null;
    this.vertexBuffer = null;
    this.texCoordBuffer = null;
    this.dataTexture = null;
    this.lutTexture = null;
    this.gl = null;
    this.map = null;
  }
}
