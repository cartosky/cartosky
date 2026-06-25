import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { useAuth } from "@clerk/react";

import {
  buildMeteogramCacheKey,
  fetchMeteogramCached,
  getMeteogramCacheEntry,
  isMeteogramFetchInFlight,
  meteogramLocationMatches,
  subscribeMeteogramCache,
} from "@/lib/meteogram-cache";
import { meteogramAuthHeaders, meteogramAuthScope } from "@/lib/meteogram-auth";

export type {
  MeteogramPoint,
  MeteogramResponse,
  MeteogramSeries,
  MeteogramSeriesStatus,
  MeteogramVariable,
} from "@/lib/meteogram-types";

type UseMeteogramParams = {
  lat: number;
  lon: number;
  models: string[];
  variables: string[];
  enabled?: boolean;
};

import type { MeteogramResponse } from "@/lib/meteogram-types";

type UseMeteogramResult = {
  data: MeteogramResponse | null;
  /** True only when there is no displayable data yet and a fetch is in progress. */
  loading: boolean;
  /** True when a same-location refetch is in progress but prior data is still shown. */
  isUpdating: boolean;
  error: string | null;
  reload: () => void;
};

/**
 * Fetches a multi-model meteogram for a location. Shares a module-level cache
 * with background prefetch so the Models tab can render immediately when warmed.
 */
export function useMeteogram({
  lat,
  lon,
  models,
  variables,
  enabled = true,
}: UseMeteogramParams): UseMeteogramResult {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [, bumpCacheVersion] = useReducer((version: number) => version + 1, 0);
  const [reloadKey, setReloadKey] = useState(0);

  const authScope = meteogramAuthScope(isLoaded === true, isSignedIn === true);

  const modelsKey = models.join(",");
  const variablesKey = variables.join(",");
  const cacheKey = useMemo(
    () =>
      authScope
        ? buildMeteogramCacheKey(lat, lon, models, variables, authScope)
        : "",
    [authScope, lat, lon, models, modelsKey, variables, variablesKey],
  );

  const getAuthHeaders = useCallback(
    () => meteogramAuthHeaders(getToken, isSignedIn === true),
    [getToken, isSignedIn],
  );

  useEffect(() => {
    if (!enabled || !authScope || models.length === 0 || variables.length === 0) {
      return;
    }

    const unsubscribe = subscribeMeteogramCache(cacheKey, bumpCacheVersion);

    void fetchMeteogramCached(
      { lat, lon, models, variables, authScope, getAuthHeaders },
    {
      reason: reloadKey > 0 ? "useMeteogram:reload" : "useMeteogram",
      force: reloadKey > 0,
    },
  ).catch(() => {
      // Cache entry + subscribers carry the error state.
    });

    return unsubscribe;
  }, [
    authScope,
    cacheKey,
    enabled,
    getAuthHeaders,
    lat,
    lon,
    models,
    modelsKey,
    reloadKey,
    variables,
    variablesKey,
  ]);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  const entry = getMeteogramCacheEntry(cacheKey);
  const rawData = entry?.data ?? null;
  const data =
    rawData && meteogramLocationMatches(rawData, lat, lon) ? rawData : null;
  const error = entry?.error ?? null;
  const inFlight = isMeteogramFetchInFlight(cacheKey);

  const loading = enabled && models.length > 0 && !data && inFlight;
  const isUpdating = enabled && !!data && inFlight;

  return { data, loading, isUpdating, error, reload };
}
