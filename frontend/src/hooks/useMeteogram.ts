import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/react";

import { clerkJwtTemplate } from "@/lib/admin-api";
import { API_V4_BASE } from "@/lib/config";

export type MeteogramPoint = {
  fh: number;
  valid_time: string | null;
  value: number | null;
};

export type MeteogramVariable = {
  units: string;
  points: MeteogramPoint[] | null;
  error?: string;
};

export type MeteogramSeriesStatus = "ok" | "partial" | "unavailable" | "not_entitled";

export type MeteogramSeries = {
  status: MeteogramSeriesStatus;
  run_id?: string | null;
  run_time?: string | null;
  variables?: Record<string, MeteogramVariable>;
};

export type MeteogramResponse = {
  location: { lat: number; lon: number };
  generated_at: string;
  run_policy: { type: string };
  series: Record<string, MeteogramSeries>;
};

type UseMeteogramParams = {
  lat: number;
  lon: number;
  models: string[];
  variables: string[];
  enabled?: boolean;
};

type UseMeteogramResult = {
  data: MeteogramResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
};

/**
 * Fetches a multi-model meteogram for a location. Sends the Clerk token when
 * signed in (optional auth) so per-model entitlements resolve correctly. The
 * caller passes the eligible model set (already filtered for coverage +
 * entitlement); pill toggling is client-side and does not refetch.
 */
export function useMeteogram({
  lat,
  lon,
  models,
  variables,
  enabled = true,
}: UseMeteogramParams): UseMeteogramResult {
  const { getToken, isSignedIn } = useAuth();
  const [data, setData] = useState<MeteogramResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  const modelsKey = models.join(",");
  const variablesKey = variables.join(",");

  useEffect(() => {
    if (!enabled || models.length === 0 || variables.length === 0) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    let cancelled = false;

    (async () => {
      setLoading(true);
      setError(null);
      try {
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
          Accept: "application/json",
        };
        if (isSignedIn) {
          try {
            const token = await getToken({ template: clerkJwtTemplate() });
            if (token) headers.Authorization = `Bearer ${token}`;
          } catch {
            // Proceed unauthenticated; backend falls back to free entitlements.
          }
        }

        const response = await fetch(`${API_V4_BASE}/forecast/meteogram`, {
          method: "POST",
          headers,
          credentials: "omit",
          signal: controller.signal,
          body: JSON.stringify({
            lat,
            lon,
            models,
            variables,
            run_policy: { type: "latest_per_model" },
          }),
        });

        if (!response.ok) {
          if (response.status === 429) {
            throw new Error("Too many requests. Please wait a moment and retry.");
          }
          throw new Error(`Unable to load model guidance (${response.status}).`);
        }

        const json = (await response.json()) as MeteogramResponse;
        if (!cancelled) setData(json);
      } catch (err) {
        if (controller.signal.aborted) return;
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load model guidance.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lat, lon, modelsKey, variablesKey, enabled, reloadKey, getToken, isSignedIn]);

  return { data, loading, error, reload };
}
