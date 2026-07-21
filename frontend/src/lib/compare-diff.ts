import { productFetch, type GridManifestResponse } from "@/lib/api";
import {
  alignedMutualGridHours,
  reanchorForecastHourOnSwap,
  runAlignmentOffsetHours,
} from "@/lib/compare-alignment";
import { gridFrameCache } from "@/lib/grid-frame-cache";
import { gridUvToLonLat, latToGridV, lonToGridU } from "@/lib/grid-sample";
import type { DiffScale } from "@/lib/compare-diff-scales";

export {
  alignedMutualGridHours,
  reanchorForecastHourOnSwap,
  runAlignmentOffsetHours,
} from "@/lib/compare-alignment";

/**
 * Frame fetching, decoding, resampling, and diff computation for compare
 * difference mode. This module owns ALL frame data access for diff mode
 * independently of `GridWebglLayerController` (design doc, Architectural
 * Decisions #1). It never reads from or writes to the WebGL controller.
 */

/** Geometry + packing metadata for one grid frame. `bbox` is EPSG:3857 meters. */
export type GridMeta = {
  width: number;
  height: number;
  bbox: [number, number, number, number];
  dtype: "uint8" | "uint16";
  scale: number;
  offset: number;
  nodata: number;
  units?: string;
};

// Synthetic diff grid packing: reserve the top uint16 code as nodata and map
// the remaining range linearly onto [-maxAbs, +maxAbs]. This is the exact
// inverse of `decodeGridFrame`, so MapCanvas decodes it like any other grid.
const DIFF_NODATA = 65535;
const DIFF_ENCODE_MAX = 65534;

/** Intersection of two sorted hour lists (both sides must have a ready grid frame). */
export function intersectSortedHours(left: number[], right: number[]): number[] {
  const rightSet = new Set(right);
  return left.filter((hour) => rightSet.has(hour));
}

// Signal-less (prefetch) fetches in flight, shared so a compute fetch for the
// same URL awaits the existing download instead of duplicating it. Fetches
// WITH an AbortSignal are never registered — sharing an abortable promise
// would let one caller's abort reject another caller that is still current.
const inflightFrameFetches = new Map<string, Promise<Uint8Array>>();

/**
 * Fetch raw frame bytes, going through the diff-only {@link gridFrameCache}.
 * Clean independent fetch — does not reuse any `GridWebglLayerController` logic.
 * Goes through {@link productFetch} so protected models (e.g. ECMWF) get the
 * same Bearer-token authorization as the split-mode controller fetch of the
 * identical URLs. Uses the default HTTP cache mode — frame URLs are versioned
 * and served immutable, so the browser cache makes split→diff transitions
 * cache-hot for free. Throws `AbortError` if the signal aborts.
 */
export async function fetchGridFrameBytes(
  url: string,
  model: string | null,
  signal?: AbortSignal,
): Promise<Uint8Array> {
  const cached = gridFrameCache.get(url);
  if (cached) {
    return cached;
  }
  const inflight = inflightFrameFetches.get(url);
  if (inflight) {
    return inflight;
  }
  const doFetch = async (): Promise<Uint8Array> => {
    const response = await productFetch(model, url, { signal, credentials: "omit" });
    if (!response.ok) {
      throw new Error(`Frame fetch failed: ${response.status} ${response.statusText}`);
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    gridFrameCache.set(url, bytes);
    return bytes;
  };
  if (signal) {
    return doFetch();
  }
  const promise = doFetch().finally(() => {
    inflightFrameFetches.delete(url);
  });
  inflightFrameFetches.set(url, promise);
  return promise;
}

/**
 * Decode packed uint8/uint16 bytes to physical float values using the meta's
 * scale/offset, matching `grid-sample.ts`'s little-endian packing. Nodata
 * pixels become `NaN`. Returns a row-major `Float32Array` of `width * height`.
 */
export function decodeGridFrame(bytes: Uint8Array, gridMeta: GridMeta): Float32Array {
  const { width, height, dtype, scale, offset, nodata } = gridMeta;
  const count = Math.max(0, Math.floor(width) * Math.floor(height));
  const bytesPerSample = dtype === "uint8" ? 1 : 2;
  const expectedBytes = count * bytesPerSample;
  if (bytes.byteLength < expectedBytes) {
    throw new Error(
      `Frame undersized: expected ${expectedBytes} bytes for ${width}x${height} ${dtype}, got ${bytes.byteLength}`,
    );
  }
  const out = new Float32Array(count);
  for (let index = 0; index < count; index += 1) {
    const byteIndex = index * bytesPerSample;
    let encoded = bytes[byteIndex] ?? 0;
    if (dtype === "uint16") {
      encoded += (bytes[byteIndex + 1] ?? 0) * 256;
    }
    if (Math.abs(encoded - nodata) < 0.5) {
      out[index] = NaN;
      continue;
    }
    out[index] = encoded * scale + offset;
  }
  return out;
}

function sampleFloatAt(src: Float32Array, width: number, height: number, col: number, row: number): number {
  const clampedCol = Math.max(0, Math.min(width - 1, col));
  const clampedRow = Math.max(0, Math.min(height - 1, row));
  return src[clampedRow * width + clampedCol] ?? NaN;
}

/**
 * NaN-aware bilinear interpolation over a decoded float grid. Mirrors the
 * weighting of `grid-sample.ts`'s `sampleBilinearValue` (corners that are nodata
 * drop out and the present weights are renormalized), but operates in the
 * decoded-float domain so it composes with `decodeGridFrame`. Returns `NaN` when
 * all four corners are nodata.
 */
function sampleBilinearFloat(src: Float32Array, width: number, height: number, u: number, v: number): number {
  const texelX = u * width - 0.5;
  const texelY = v * height - 0.5;
  const x0 = Math.floor(texelX);
  const y0 = Math.floor(texelY);
  const fx = texelX - x0;
  const fy = texelY - y0;

  const v00 = sampleFloatAt(src, width, height, x0, y0);
  const v10 = sampleFloatAt(src, width, height, x0 + 1, y0);
  const v01 = sampleFloatAt(src, width, height, x0, y0 + 1);
  const v11 = sampleFloatAt(src, width, height, x0 + 1, y0 + 1);

  const w00 = Number.isNaN(v00) ? 0 : 1;
  const w10 = Number.isNaN(v10) ? 0 : 1;
  const w01 = Number.isNaN(v01) ? 0 : 1;
  const w11 = Number.isNaN(v11) ? 0 : 1;

  const bw00 = (1 - fx) * (1 - fy) * w00;
  const bw10 = fx * (1 - fy) * w10;
  const bw01 = (1 - fx) * fy * w01;
  const bw11 = fx * fy * w11;
  const weightSum = bw00 + bw10 + bw01 + bw11;
  if (weightSum <= 0) {
    return NaN;
  }
  return (
    (w00 ? v00 : 0) * bw00
    + (w10 ? v10 : 0) * bw10
    + (w01 ? v01 : 0) * bw01
    + (w11 ? v11 : 0) * bw11
  ) / weightSum;
}

function metaMatches(a: GridMeta, b: GridMeta): boolean {
  return (
    a.width === b.width
    && a.height === b.height
    && a.bbox[0] === b.bbox[0]
    && a.bbox[1] === b.bbox[1]
    && a.bbox[2] === b.bbox[2]
    && a.bbox[3] === b.bbox[3]
  );
}

/**
 * Resample a decoded source grid onto `refMeta`'s coordinate space via bilinear
 * interpolation. Pixels outside the source bbox become `NaN`. Identical
 * dimensions + bbox short-circuit to the source unchanged.
 *
 * The Web-Mercator mapping is separable — lon depends only on `u` and lat only
 * on `v` — so the per-pixel projection collapses to one lookup table per axis
 * (W+H projections instead of W×H). {@link lonToGridU}/{@link latToGridV} are
 * the axis components of `lonLatToGridUv`, so the result is identical to the
 * per-pixel formulation.
 */
export function resampleGridToReference(
  source: Float32Array,
  sourceMeta: GridMeta,
  refMeta: GridMeta,
): Float32Array {
  if (metaMatches(sourceMeta, refMeta)) {
    return source;
  }
  const refWidth = Math.floor(refMeta.width);
  const refHeight = Math.floor(refMeta.height);
  const srcWidth = Math.floor(sourceMeta.width);
  const srcHeight = Math.floor(sourceMeta.height);

  // Per-column source u (NaN = outside the source bbox horizontally).
  const sourceUForCol = new Float64Array(refWidth);
  for (let col = 0; col < refWidth; col += 1) {
    const u = (col + 0.5) / refWidth;
    const [lon] = gridUvToLonLat(u, 0.5, refMeta.bbox);
    const sourceU = lonToGridU(lon, sourceMeta.bbox);
    sourceUForCol[col] = sourceU === null ? NaN : sourceU;
  }
  // Per-row source v (NaN = outside the source bbox vertically).
  const sourceVForRow = new Float64Array(refHeight);
  for (let row = 0; row < refHeight; row += 1) {
    const v = (row + 0.5) / refHeight;
    const [, lat] = gridUvToLonLat(0.5, v, refMeta.bbox);
    const sourceV = latToGridV(lat, sourceMeta.bbox);
    sourceVForRow[row] = sourceV === null ? NaN : sourceV;
  }

  const out = new Float32Array(refWidth * refHeight);
  for (let row = 0; row < refHeight; row += 1) {
    const sourceV = sourceVForRow[row];
    const rowOffset = row * refWidth;
    if (Number.isNaN(sourceV)) {
      out.fill(NaN, rowOffset, rowOffset + refWidth);
      continue;
    }
    for (let col = 0; col < refWidth; col += 1) {
      const sourceU = sourceUForCol[col];
      out[rowOffset + col] = Number.isNaN(sourceU)
        ? NaN
        : sampleBilinearFloat(source, srcWidth, srcHeight, sourceU, sourceV);
    }
  }
  return out;
}

/**
 * Reference grid for the diff: the coarser of the two (fewer total pixels). On a
 * tie, the left grid wins — a hard rule for permalink/screenshot reproducibility
 * (design doc, Architectural Decisions #8).
 */
export function chooseReferenceGrid(leftMeta: GridMeta, rightMeta: GridMeta): GridMeta {
  const leftPixels = leftMeta.width * leftMeta.height;
  const rightPixels = rightMeta.width * rightMeta.height;
  return rightPixels < leftPixels ? rightMeta : leftMeta;
}

/**
 * Compute the physical-units diff grid: choose the reference grid, decode both
 * frames, resample both onto the reference, then subtract (`left − right`). Any
 * nodata input propagates to `NaN`.
 *
 * Fails closed on a units mismatch: subtracting fields packed in different
 * units (e.g. °F vs °C) produces silent garbage, and correctness otherwise
 * rests entirely on backend packing conventions staying unit-consistent per
 * var_key. Missing units on either side skip the check (older manifests).
 */
export function computeDiffGrid(
  leftBytes: Uint8Array,
  rightBytes: Uint8Array,
  leftMeta: GridMeta,
  rightMeta: GridMeta,
): { diffFloats: Float32Array; refMeta: GridMeta } {
  const leftUnits = String(leftMeta.units ?? "").trim().toLowerCase();
  const rightUnits = String(rightMeta.units ?? "").trim().toLowerCase();
  if (leftUnits && rightUnits && leftUnits !== rightUnits) {
    throw new Error(
      `Unit mismatch: left frame is "${leftMeta.units}" but right frame is "${rightMeta.units}" — refusing to diff`,
    );
  }
  const refMeta = chooseReferenceGrid(leftMeta, rightMeta);
  const leftFloats = decodeGridFrame(leftBytes, leftMeta);
  const rightFloats = decodeGridFrame(rightBytes, rightMeta);
  const leftRef = resampleGridToReference(leftFloats, leftMeta, refMeta);
  const rightRef = resampleGridToReference(rightFloats, rightMeta, refMeta);
  const count = Math.floor(refMeta.width) * Math.floor(refMeta.height);
  const diffFloats = new Float32Array(count);
  for (let index = 0; index < count; index += 1) {
    const left = leftRef[index];
    const right = rightRef[index];
    diffFloats[index] = Number.isNaN(left) || Number.isNaN(right) ? NaN : left - right;
  }
  return { diffFloats, refMeta };
}

/**
 * Pack a float diff grid back into uint16 over the symmetric `±scale.maxAbs`
 * range and wrap it in a synthetic manifest consumable by MapCanvas /
 * GridWebglLayerController. The packing is the exact inverse of
 * {@link decodeGridFrame}.
 *
 * The frame bytes are returned as a separate object-URL `frameUrl` rather than
 * embedded in `manifest.lods[].frames[].url`. MapCanvas's prefetch builder runs
 * manifest frame URLs through a normalizer that rewrites any non-`http(s)` URL by
 * prepending the API root — which turns a `blob:` URL into a bogus `/blob:` path
 * and triggers an "undersized frame texture upload". Keeping the blob out of the
 * manifest and passing it straight to MapCanvas as `gridFrameUrl` avoids that.
 *
 * Callers own revoking the returned `frameUrl` when it is replaced.
 */
export function buildDiffManifest(
  refMeta: GridMeta,
  diffFloats: Float32Array,
  scale: DiffScale,
): { manifest: GridManifestResponse; frameUrl: string } {
  const width = Math.floor(refMeta.width);
  const height = Math.floor(refMeta.height);
  const expectedLength = width * height;
  // Fail closed: never emit a blob from a malformed/partial diff (e.g. a
  // zero-initialized or wrongly-sized Float32Array). Throwing here routes the
  // failure to the hook's error state instead of producing an undersized frame.
  if (!Number.isFinite(expectedLength) || expectedLength <= 0 || diffFloats.length !== expectedLength) {
    throw new Error(
      `buildDiffManifest: refMeta ${width}x${height} expects ${expectedLength} samples but got ${diffFloats.length}`,
    );
  }
  const maxAbs = scale.maxAbs;
  const packScale = (2 * maxAbs) / DIFF_ENCODE_MAX;
  const packOffset = -maxAbs;

  const packed = new Uint8Array(expectedLength * 2);
  for (let index = 0; index < expectedLength; index += 1) {
    const value = diffFloats[index];
    let encoded: number;
    if (Number.isNaN(value)) {
      encoded = DIFF_NODATA;
    } else {
      const clamped = Math.max(-maxAbs, Math.min(maxAbs, value));
      encoded = Math.round((clamped - packOffset) / packScale);
      encoded = Math.max(0, Math.min(DIFF_ENCODE_MAX, encoded));
    }
    const byteIndex = index * 2;
    packed[byteIndex] = encoded & 0xff;
    packed[byteIndex + 1] = (encoded >> 8) & 0xff;
  }

  const frameUrl = URL.createObjectURL(new Blob([packed], { type: "application/octet-stream" }));

  const manifest: GridManifestResponse = {
    manifest_version: 1,
    subtype: "grid",
    model: "compare-diff",
    run: "diff",
    var: "diff",
    bbox: refMeta.bbox,
    grid: {
      width,
      height,
      dtype: "uint16",
      endianness: "little",
      scale: packScale,
      offset: packOffset,
      nodata: DIFF_NODATA,
      units: scale.units,
    },
    lods: [
      {
        level: 0,
        width,
        height,
        // `url` intentionally omitted — the frame is delivered out-of-band via
        // the returned `frameUrl` so the prefetch normalizer can't mangle it.
        frames: [{ fh: 0, file: "diff" }],
      },
    ],
  };

  return { manifest, frameUrl };
}
