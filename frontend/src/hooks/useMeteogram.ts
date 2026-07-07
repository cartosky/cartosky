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
import { meteogramAuthHeaders } from "@/lib/meteogram-auth";

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
  pinnedRuns?: Record<string, string>;
  /**
   * Request per-member series (member pipeline Phase 5). Only pass models
   * that publish members (see MEMBER_PLUME_MODELS) — the backend 400s the
   * whole request if any requested model lacks member support.
   */
  includeMembers?: boolean;
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
  pinnedRuns,
  includeMembers = false,
  enabled = true,
}: UseMeteogramParams): UseMeteogramResult {
  const { getToken, isSignedIn } = useAuth();
  const [, bumpCacheVersion] = useReducer((version: number) => version + 1, 0);
  const [reloadKey, setReloadKey] = useState(0);

  const modelsKey = models.join(",");
  const variablesKey = variables.join(",");
  const pinnedRunsKey = JSON.stringify(pinnedRuns ?? {});
  const cacheKey = useMemo(
    () => buildMeteogramCacheKey(lat, lon, models, variables, pinnedRuns, includeMembers),
    [lat, lon, modelsKey, variablesKey, pinnedRunsKey, includeMembers],
  );

  const getAuthHeaders = useCallback(
    () => meteogramAuthHeaders(getToken, isSignedIn === true),
    [getToken, isSignedIn],
  );

  useEffect(() => {
    if (!enabled || models.length === 0 || variables.length === 0) {
      return;
    }

    const unsubscribe = subscribeMeteogramCache(cacheKey, bumpCacheVersion);

  void fetchMeteogramCached(
    { lat, lon, models, variables, pinnedRuns, includeMembers, getAuthHeaders },
    {
      reason: reloadKey > 0 ? "useMeteogram:reload" : "useMeteogram",
      force: reloadKey > 0,
    },
  ).catch(() => {
      // Cache entry + subscribers carry the error state.
    });

    return unsubscribe;
    // The raw `models`/`variables` arrays are intentionally NOT dependencies —
    // callers routinely pass fresh array literals each render, and an error
    // response never becomes a cache hit, so depending on array identity turns
    // any 4xx into an infinite refetch storm (each retry re-renders via the
    // cache notification, minting new arrays). The string keys carry the
    // actual identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    cacheKey,
    enabled,
    getAuthHeaders,
    lat,
    lon,
    modelsKey,
    reloadKey,
    variablesKey,
    pinnedRunsKey,
    includeMembers,
  ]);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  const entry = getMeteogramCacheEntry(cacheKey);
  const rawData = entry?.data ?? null;
  const data =
    rawData && meteogramLocationMatches(rawData, lat, lon) ? rawData : null;
  const error = entry?.error ?? null;
  const inFlight = isMeteogramFetchInFlight(cacheKey);

  // "No data and no error yet" means a fetch is pending or about to start —
  // treat it as loading. Relying on `inFlight` alone misses the gap between a
  // cache-key change (e.g. switching runs) and the effect that starts the fetch:
  // the fetch start does not re-render, so `inFlight` reads false for the whole
  // request and the empty state would flash until the fetch resolves.
  const loading =
    enabled && models.length > 0 && variables.length > 0 && !data && !error;
  const isUpdating = enabled && !!data && inFlight;

  return { data, loading, isUpdating, error, reload };
}
