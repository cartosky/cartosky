/**
 * Module-level cache + in-flight dedupe for sounding requests.
 *
 * Same shape as `meteogram-cache.ts` (fresh TTL matching the backend's
 * `Cache-Control: private, max-age=300`, subscriber notification, one request
 * per key in flight), trimmed to this endpoint's single-point payload.
 */
import { API_V4_BASE } from "@/lib/config";
import type { SoundingResponse } from "@/lib/sounding-types";

/** Match the backend's `Cache-Control: private, max-age=300`. */
const SOUNDING_CACHE_TTL_MS = 5 * 60 * 1000;
const SOUNDING_FETCH_TIMEOUT_MS = 30 * 1000;

export type SoundingFetchParams = {
  model: string;
  lat: number;
  lon: number;
  run?: string | null;
  getAuthHeaders: () => Promise<Record<string, string>>;
};

type CacheEntry = {
  data: SoundingResponse | null;
  error: string | null;
  expiresAt: number;
};

const cache = new Map<string, CacheEntry>();
const inflight = new Map<string, Promise<SoundingResponse>>();
const listeners = new Map<string, Set<() => void>>();

/**
 * Keyed on the REQUESTED point rounded to 3 decimals (~110 m). The backend
 * snaps to the nearest grid cell anyway, so a sub-cell mouse jitter must not
 * mint a new key and a new request.
 */
export function buildSoundingCacheKey(
  model: string,
  lat: number,
  lon: number,
  run?: string | null,
): string {
  return `${model}|${lat.toFixed(3)}|${lon.toFixed(3)}|${run || "latest"}`;
}

export function getSoundingCacheEntry(key: string): CacheEntry | undefined {
  const entry = cache.get(key);
  if (!entry) return undefined;
  if (Date.now() > entry.expiresAt) {
    cache.delete(key);
    return undefined;
  }
  return entry;
}

export function isSoundingFetchInFlight(key: string): boolean {
  return inflight.has(key);
}

export function subscribeSoundingCache(key: string, listener: () => void): () => void {
  let bucket = listeners.get(key);
  if (!bucket) {
    bucket = new Set();
    listeners.set(key, bucket);
  }
  bucket.add(listener);
  return () => {
    bucket?.delete(listener);
    if (bucket && bucket.size === 0) listeners.delete(key);
  };
}

function notify(key: string) {
  listeners.get(key)?.forEach((listener) => listener());
}

async function requestSounding(params: SoundingFetchParams): Promise<SoundingResponse> {
  const key = buildSoundingCacheKey(params.model, params.lat, params.lon, params.run);
  try {
    const authHeaders = await params.getAuthHeaders();
    const response = await fetch(`${API_V4_BASE}/forecast/sounding`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...authHeaders,
      },
      credentials: "omit",
      signal: AbortSignal.timeout(SOUNDING_FETCH_TIMEOUT_MS),
      body: JSON.stringify({
        model: params.model,
        lat: params.lat,
        lon: params.lon,
        ...(params.run ? { run: params.run } : {}),
      }),
    });

    if (!response.ok) {
      if (response.status === 429) {
        throw new Error("Too many requests. Please wait a moment and retry.");
      }
      if (response.status === 404) {
        throw new Error("No sounding data published for this run yet.");
      }
      if (response.status === 400) {
        throw new Error("That point is outside the model domain.");
      }
      throw new Error(`Unable to load sounding (${response.status}).`);
    }

    const json = (await response.json()) as SoundingResponse;
    if (!Array.isArray(json?.frames) || json.frames.length === 0) {
      throw new Error("No sounding data published for this run yet.");
    }

    cache.set(key, { data: json, error: null, expiresAt: Date.now() + SOUNDING_CACHE_TTL_MS });
    notify(key);
    return json;
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to load sounding.";
    // Errors expire fast: re-picking a point that transiently failed (429, blip)
    // should retry over the network, not replay a stale error for the full TTL.
    cache.set(key, { data: null, error: message, expiresAt: Date.now() + 10_000 });
    notify(key);
    throw err;
  }
}

export function fetchSoundingCached(
  params: SoundingFetchParams,
  options?: { force?: boolean },
): Promise<SoundingResponse> {
  const key = buildSoundingCacheKey(params.model, params.lat, params.lon, params.run);

  if (!options?.force) {
    const cached = getSoundingCacheEntry(key);
    if (cached?.data) return Promise.resolve(cached.data);
  } else {
    cache.delete(key);
  }

  const existing = inflight.get(key);
  if (existing) return existing;

  const promise = requestSounding(params).finally(() => {
    inflight.delete(key);
  });
  inflight.set(key, promise);
  return promise;
}
