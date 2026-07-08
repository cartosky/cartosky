import type { AnchorBatchPoint } from "@/lib/anchor-labels";

const MERCATOR_HALF_WORLD = 20037508.342789244;

export function lonLatToMercatorMeters(lon: number, lat: number): [number, number] {
  const clampedLat = Math.max(-85.05112878, Math.min(85.05112878, lat));
  const x = (lon * MERCATOR_HALF_WORLD) / 180;
  const yRadians = (clampedLat * Math.PI) / 180;
  const y = Math.log(Math.tan(Math.PI / 4 + yRadians / 2)) * (MERCATOR_HALF_WORLD / Math.PI);
  return [x, y];
}

function mercatorXFromMeters(x: number): number {
  return (x + MERCATOR_HALF_WORLD) / (2 * MERCATOR_HALF_WORLD);
}

function mercatorYFromMeters(y: number): number {
  return (MERCATOR_HALF_WORLD - y) / (2 * MERCATOR_HALF_WORLD);
}

/**
 * Inverse of {@link lonLatToGridUv}: map a normalized grid coordinate (u, v) in
 * [0, 1] back to lon/lat. `bbox` is in Web-Mercator meters (EPSG:3857), the same
 * convention `lonLatToGridUv` expects. The mercator-normalization affine cancels
 * out, so the meters span maps linearly; only the Y→lat step is non-linear.
 */
export function gridUvToLonLat(
  u: number,
  v: number,
  bbox: [number, number, number, number],
): [number, number] {
  const [west, south, east, north] = bbox;
  const meterX = west + u * (east - west);
  const meterY = north + v * (south - north);
  const lon = (meterX * 180) / MERCATOR_HALF_WORLD;
  const latRadians = 2 * Math.atan(Math.exp((meterY * Math.PI) / MERCATOR_HALF_WORLD)) - Math.PI / 2;
  const lat = (latRadians * 180) / Math.PI;
  return [lon, lat];
}

/**
 * Horizontal component of {@link lonLatToGridUv}: lon → u within the bbox, or
 * null when outside/degenerate. In Web Mercator, u depends only on lon (and v
 * only on lat), so callers that map a whole grid can precompute one u per
 * column and one v per row instead of projecting every pixel.
 */
export function lonToGridU(lon: number, bbox: [number, number, number, number]): number | null {
  const [west, , east] = bbox;
  const mx = (lon * MERCATOR_HALF_WORLD) / 180;
  const left = mercatorXFromMeters(west);
  const right = mercatorXFromMeters(east);
  const pointX = mercatorXFromMeters(mx);
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

/** Vertical component of {@link lonLatToGridUv}: lat → v within the bbox, or null. */
export function latToGridV(lat: number, bbox: [number, number, number, number]): number | null {
  const [, south, , north] = bbox;
  const clampedLat = Math.max(-85.05112878, Math.min(85.05112878, lat));
  const yRadians = (clampedLat * Math.PI) / 180;
  const my = Math.log(Math.tan(Math.PI / 4 + yRadians / 2)) * (MERCATOR_HALF_WORLD / Math.PI);
  const top = mercatorYFromMeters(north);
  const bottom = mercatorYFromMeters(south);
  const pointY = mercatorYFromMeters(my);
  const spanY = bottom - top;
  if (!Number.isFinite(spanY) || spanY === 0) {
    return null;
  }
  const v = (pointY - top) / spanY;
  if (!Number.isFinite(v) || v < 0 || v > 1) {
    return null;
  }
  return v;
}

export function lonLatToGridUv(
  lon: number,
  lat: number,
  bbox: [number, number, number, number],
): [number, number] | null {
  const u = lonToGridU(lon, bbox);
  const v = latToGridV(lat, bbox);
  if (u === null || v === null) {
    return null;
  }
  return [u, v];
}

function decodePackedSample(
  bytes: Uint8Array<ArrayBufferLike>,
  width: number,
  height: number,
  col: number,
  row: number,
  dtype: "uint8" | "uint16",
  scale: number,
  offset: number,
  nodata: number,
): number | null {
  const clampedCol = Math.max(0, Math.min(width - 1, col));
  const clampedRow = Math.max(0, Math.min(height - 1, row));
  const byteIndex = (clampedRow * width + clampedCol) * (dtype === "uint8" ? 1 : 2);
  if (byteIndex + (dtype === "uint8" ? 1 : 2) > bytes.byteLength) {
    return null;
  }
  let encoded = bytes[byteIndex] ?? 0;
  if (dtype === "uint16") {
    encoded += (bytes[byteIndex + 1] ?? 0) * 256;
  }
  if (Math.abs(encoded - nodata) < 0.5) {
    return null;
  }
  return encoded * scale + offset;
}

export function sampleBilinearValue(
  bytes: Uint8Array<ArrayBufferLike>,
  width: number,
  height: number,
  u: number,
  v: number,
  dtype: "uint8" | "uint16",
  scale: number,
  offset: number,
  nodata: number,
): number | null {
  const texelX = u * width - 0.5;
  const texelY = v * height - 0.5;
  const x0 = Math.floor(texelX);
  const y0 = Math.floor(texelY);
  const fx = texelX - x0;
  const fy = texelY - y0;

  const v00 = decodePackedSample(bytes, width, height, x0, y0, dtype, scale, offset, nodata);
  const v10 = decodePackedSample(bytes, width, height, x0 + 1, y0, dtype, scale, offset, nodata);
  const v01 = decodePackedSample(bytes, width, height, x0, y0 + 1, dtype, scale, offset, nodata);
  const v11 = decodePackedSample(bytes, width, height, x0 + 1, y0 + 1, dtype, scale, offset, nodata);

  const w00 = v00 !== null ? 1 : 0;
  const w10 = v10 !== null ? 1 : 0;
  const w01 = v01 !== null ? 1 : 0;
  const w11 = v11 !== null ? 1 : 0;
  if (w00 + w10 + w01 + w11 <= 0) {
    return null;
  }

  const bw00 = (1 - fx) * (1 - fy) * w00;
  const bw10 = fx * (1 - fy) * w10;
  const bw01 = (1 - fx) * fy * w01;
  const bw11 = fx * fy * w11;
  const wSum = bw00 + bw10 + bw01 + bw11;
  if (wSum <= 0) {
    return null;
  }

  const decoded = (
    (v00 ?? 0) * bw00
    + (v10 ?? 0) * bw10
    + (v01 ?? 0) * bw01
    + (v11 ?? 0) * bw11
  ) / wSum;
  return Math.round(decoded * 10) / 10;
}

export type GridSampleGrid = {
  width: number;
  height: number;
  dtype: string;
  scale: number;
  offset: number;
  nodata: number;
  units?: string;
};

export function sampleGridPoints(params: {
  bytes: Uint8Array<ArrayBufferLike>;
  grid: GridSampleGrid;
  bbox: [number, number, number, number];
  points: AnchorBatchPoint[];
}): Record<string, number | null> {
  const dtype = String(params.grid.dtype ?? "").trim().toLowerCase() === "uint8" ? "uint8" : "uint16";
  const scale = Number(params.grid.scale) || 1;
  const offset = Number(params.grid.offset) || 0;
  const nodata = Number(params.grid.nodata) || 65535;
  const width = Math.max(1, Math.floor(params.grid.width));
  const height = Math.max(1, Math.floor(params.grid.height));
  const values: Record<string, number | null> = {};

  for (const point of params.points) {
    const uv = lonLatToGridUv(point.lon, point.lat, params.bbox);
    if (!uv) {
      values[point.id] = null;
      continue;
    }
    values[point.id] = sampleBilinearValue(
      params.bytes,
      width,
      height,
      uv[0],
      uv[1],
      dtype,
      scale,
      offset,
      nodata,
    );
  }

  return values;
}
