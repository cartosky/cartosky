import type { MeteogramResponse } from "@/lib/meteogram-types";
import { API_V4_BASE } from "@/lib/config";

/** Match backend `Cache-Control: private, max-age=300` for meteogram responses. */
const METEOGRAM_CACHE_TTL_MS = 5 * 60 * 1000;

export type MeteogramFetchParams = {
  lat: number;
  lon: number;
  models: string[];
  variables: string[];
  pinnedRuns?: Record<string, string>;
  /**
   * Request per-member series (member pipeline Phase 5). Only send for
   * models that publish members — the backend rejects the whole request with
   * a 400 if ANY requested model lacks member support.
   */
  includeMembers?: boolean;
  getAuthHeaders: () => Promise<Record<string, string>>;
};

type CacheEntry = {
  data: MeteogramResponse | null;
  error: string | null;
  expiresAt: number | null;
};

const cache = new Map<string, CacheEntry>();
const inflight = new Map<string, Promise<MeteogramResponse>>();
const listeners = new Map<string, Set<() => void>>();

export function buildMeteogramCacheKey(
  lat: number,
  lon: number,
  models: string[],
  variables: string[],
  pinnedRuns?: Record<string, string>,
  includeMembers?: boolean,
): string {
  const modelsKey = [...models].sort().join(",");
  const variablesKey = [...variables].sort().join(",");
  const hasPins = pinnedRuns && Object.keys(pinnedRuns).length > 0;
  const runsKey = hasPins
    ? [...models]
        .sort()
        .map((m) => `${m}:${pinnedRuns![m] ?? "latest"}`)
        .join(",")
    : "";
  const suffix = runsKey ? `:${runsKey}` : "";
  // Member payloads are a superset but MUST cache separately (byte-identical
  // key when false, mirroring the backend's cache-key rule).
  const membersSuffix = includeMembers ? ":members" : "";
  return `${lat.toFixed(3)}:${lon.toFixed(3)}:${modelsKey}:${variablesKey}:${suffix}${membersSuffix}`;
}

export function meteogramLocationMatches(
  data: MeteogramResponse,
  lat: number,
  lon: number,
): boolean {
  return (
    Math.abs(data.location.lat - lat) < 0.0005 &&
    Math.abs(data.location.lon - lon) < 0.0005
  );
}

export function getMeteogramCacheEntry(key: string): CacheEntry | undefined {
  const entry = cache.get(key);
  if (!entry) {
    return undefined;
  }
  if (entry.expiresAt != null && Date.now() > entry.expiresAt) {
    cache.delete(key);
    return undefined;
  }
  return entry;
}

export function isMeteogramFetchInFlight(key: string): boolean {
  return inflight.has(key);
}

export function subscribeMeteogramCache(key: string, listener: () => void): () => void {
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

function notifyMeteogramCache(key: string) {
  listeners.get(key)?.forEach((listener) => listener());
}

function devLog(message: string, detail?: Record<string, unknown>) {
  if (!import.meta.env.DEV) return;
  if (detail) {
    console.debug(`[meteogram] ${message}`, detail);
  } else {
    console.debug(`[meteogram] ${message}`);
  }
}

async function requestMeteogram(
  params: MeteogramFetchParams,
  reason: string,
): Promise<MeteogramResponse> {
  const startedAt = import.meta.env.DEV ? performance.now() : 0;
  const key = buildMeteogramCacheKey(
    params.lat, params.lon, params.models, params.variables, params.pinnedRuns, params.includeMembers,
  );

  try {
    const authStartedAt = import.meta.env.DEV ? performance.now() : 0;
    const authHeaders = await params.getAuthHeaders();
    const authMs = import.meta.env.DEV ? performance.now() - authStartedAt : 0;

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...authHeaders,
    };

    const networkStartedAt = import.meta.env.DEV ? performance.now() : 0;
    const response = await fetch(`${API_V4_BASE}/forecast/meteogram`, {
      method: "POST",
      headers,
      credentials: "omit",
      body: JSON.stringify({
        lat: params.lat,
        lon: params.lon,
        models: params.models,
        variables: params.variables,
        run_policy: { type: "latest_per_model" },
        ...(params.pinnedRuns && Object.keys(params.pinnedRuns).length > 0
      ? { pinned_runs: params.pinnedRuns }
      : {}),
        ...(params.includeMembers ? { include_members: true } : {}),
      }),
    });
    const networkMs = import.meta.env.DEV ? performance.now() - networkStartedAt : 0;

    const parseStartedAt = import.meta.env.DEV ? performance.now() : 0;
    const json = (await response.json()) as MeteogramResponse;
    const parseMs = import.meta.env.DEV ? performance.now() - parseStartedAt : 0;

    if (!response.ok) {
      if (response.status === 429) {
        throw new Error("Too many requests. Please wait a moment and retry.");
      }
      throw new Error(`Unable to load model guidance (${response.status}).`);
    }

    if (!meteogramLocationMatches(json, params.lat, params.lon)) {
      throw new Error("Meteogram response location mismatch.");
    }

    cache.set(key, { data: json, error: null, expiresAt: Date.now() + METEOGRAM_CACHE_TTL_MS });
    notifyMeteogramCache(key);

    devLog("fetch ok", {
      key,
      reason,
      authMs: Math.round(authMs),
      networkMs: Math.round(networkMs),
      parseMs: Math.round(parseMs),
      totalMs: Math.round(performance.now() - startedAt),
    });

    return json;
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to load model guidance.";
    cache.set(key, { data: null, error: message, expiresAt: Date.now() + METEOGRAM_CACHE_TTL_MS });
    notifyMeteogramCache(key);
    devLog("fetch failed", {
      key,
      reason,
      totalMs: import.meta.env.DEV ? Math.round(performance.now() - startedAt) : undefined,
      error: message,
    });
    throw err;
  }
}

/** Fetch meteogram data with module-level deduplication and cache updates. */
export function fetchMeteogramCached(
  params: MeteogramFetchParams,
  options?: { reason?: string; force?: boolean },
): Promise<MeteogramResponse> {
  const key = buildMeteogramCacheKey(
    params.lat, params.lon, params.models, params.variables, params.pinnedRuns, params.includeMembers,
  );
  const reason = options?.reason ?? "fetch";
  const force = options?.force === true;

  if (!force) {
    const cached = getMeteogramCacheEntry(key);
    if (cached?.data) {
      devLog("cache hit", { key, reason });
      return Promise.resolve(cached.data);
    }
  }

  const existing = inflight.get(key);
  if (existing) {
    devLog("deduped in-flight request", { key, reason });
    return existing;
  }

  devLog("fetch start", { key, reason, force });
  const promise = requestMeteogram(params, reason).finally(() => {
    inflight.delete(key);
  });
  inflight.set(key, promise);
  return promise;
}

/** Warm the cache after a Forecast page location is selected. */
export function prefetchMeteogram(params: MeteogramFetchParams, reason = "prefetch"): void {
  const key = buildMeteogramCacheKey(
    params.lat, params.lon, params.models, params.variables, params.pinnedRuns, params.includeMembers,
  );
  if (params.models.length === 0 || params.variables.length === 0) return;
  if (getMeteogramCacheEntry(key)?.data) {
    devLog("prefetch skipped (cache hit)", { key, reason });
    return;
  }
  if (inflight.has(key)) {
    devLog("prefetch skipped (in flight)", { key, reason });
    return;
  }
  void fetchMeteogramCached(params, { reason }).catch(() => {
    // Errors are stored on the cache entry for hook subscribers.
  });
}
