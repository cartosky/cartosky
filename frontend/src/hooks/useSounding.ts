import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { useAuth } from "@clerk/react";

import { meteogramAuthHeaders } from "@/lib/meteogram-auth";
import {
  buildSoundingCacheKey,
  fetchSoundingCached,
  getSoundingCacheEntry,
  subscribeSoundingCache,
} from "@/lib/sounding-cache";
import type { SoundingResponse } from "@/lib/sounding-types";

type UseSoundingParams = {
  model: string;
  lat: number | null;
  lon: number | null;
  run?: string | null;
  enabled?: boolean;
};

type UseSoundingResult = {
  data: SoundingResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
};

/**
 * Fetches one point's full multi-fh sounding (design §5), sharing the
 * module-level cache so re-picking the same grid cell is instant.
 *
 * Deliberately simpler than `useMeteogram`: there is no prefetch path and no
 * stale-while-revalidate horizon — a sounding panel is opened by an explicit
 * pick, so the plain 5-minute TTL is enough.
 */
export function useSounding({
  model,
  lat,
  lon,
  run,
  enabled = true,
}: UseSoundingParams): UseSoundingResult {
  const { getToken, isSignedIn } = useAuth();
  const [, bumpCacheVersion] = useReducer((version: number) => version + 1, 0);
  const [reloadKey, setReloadKey] = useState(0);

  const active =
    enabled && !!model && lat != null && lon != null && Number.isFinite(lat) && Number.isFinite(lon);

  const cacheKey = useMemo(
    () => (active ? buildSoundingCacheKey(model, Number(lat), Number(lon), run) : ""),
    [active, model, lat, lon, run],
  );

  const getAuthHeaders = useCallback(
    () => meteogramAuthHeaders(getToken, isSignedIn === true),
    [getToken, isSignedIn],
  );

  useEffect(() => {
    if (!active || !cacheKey) return undefined;
    const unsubscribe = subscribeSoundingCache(cacheKey, bumpCacheVersion);
    void fetchSoundingCached(
      { model, lat: Number(lat), lon: Number(lon), run, getAuthHeaders },
      { force: reloadKey > 0 },
    ).catch(() => {
      // The cache entry carries the error for subscribers.
    });
    return unsubscribe;
    // `getAuthHeaders` is stable per Clerk session; `cacheKey` carries the
    // request identity, exactly as in useMeteogram.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, cacheKey, reloadKey, getAuthHeaders]);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  const entry = cacheKey ? getSoundingCacheEntry(cacheKey) : undefined;
  const data = entry?.data ?? null;
  const error = entry?.error ?? null;

  // Mirrors useMeteogram: "no data and no error yet" is the loading state, so
  // the gap between a key change and the effect starting the fetch does not
  // flash an empty panel.
  const loading = active && !data && !error;

  return { data, loading, error, reload };
}
