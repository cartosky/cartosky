import { useEffect, useRef, useState } from "react";
import { PERMALINK_SYNC_DEBOUNCE_MS } from "@/lib/app-utils";

/** Params accepted by usePermalinkSync. */
export interface UsePermalinkSyncParams {
  /** True once the bootstrap selection (model/run/var/region) has been hydrated from the URL. */
  bootstrapHydrated: boolean;
  /** Ref that becomes `true` once the map view has been hydrated from the permalink. */
  mapViewHydratedRef: React.RefObject<boolean>;
  /** Tick counter that increments every time the map view is applied/updated. */
  mapViewTick: number;
  /** Ref holding the current map center + zoom. */
  mapViewRef: React.RefObject<{ lat: number; lon: number; z: number }>;
  /** Current selection values that feed the permalink search string. */
  model: string | null;
  product?: string | null;
  run: string | null;
  variable: string | null;
  ensembleView: string | null;
  resolvedForecastHourPermalink: number | null;
  region: string | null;
  /**
   * While true, URL write-back is paused (hydration detection still runs).
   * Used during autoplay so history.replaceState doesn't fire every UI tick;
   * when it flips back to false the effect re-runs and flushes the final state.
   */
  suspended?: boolean;
}

/**
 * Manages permalink (URL) synchronization for the viewer.
 *
 * Two concerns:
 * 1. **Hydration detection** — waits for both bootstrap selection and map view to
 *    be hydrated, then marks the permalink as "hydrated" so URL syncing can begin.
 * 2. **URL write-back** — debounced effect that replaces the URL search params
 *    whenever the selection or map view changes (skipping the first write to
 *    avoid overwriting the initial permalink).
 */
export function usePermalinkSync({
  bootstrapHydrated,
  mapViewHydratedRef,
  mapViewTick,
  mapViewRef,
  model,
  product,
  run,
  variable,
  ensembleView,
  resolvedForecastHourPermalink,
  region,
  suspended = false,
}: UsePermalinkSyncParams): void {
  const [permalinkHydrated, setPermalinkHydrated] = useState(false);

  const permalinkHydratedRef = useRef(false);
  const lastSyncedPermalinkSearchRef = useRef("");
  const suppressNextUrlSyncRef = useRef(true);

  // --- Hydration detection ---------------------------------------------------
  // Once bootstrap selection AND map view are both hydrated, mark the permalink
  // as hydrated so the URL sync effect below can start running.
  useEffect(() => {
    if (permalinkHydratedRef.current || !bootstrapHydrated || !mapViewHydratedRef.current) {
      return;
    }
    permalinkHydratedRef.current = true;
    suppressNextUrlSyncRef.current = true;
    setPermalinkHydrated(true);
    if (typeof window !== "undefined") {
      lastSyncedPermalinkSearchRef.current = window.location.search;
    }
  }, [bootstrapHydrated, mapViewTick]);

  // --- URL write-back --------------------------------------------------------
  // Debounced effect: whenever the selection or map view changes, update the
  // browser URL with the new permalink search params.
  useEffect(() => {
    if (!permalinkHydrated || typeof window === "undefined") {
      return;
    }
    if (suppressNextUrlSyncRef.current) {
      suppressNextUrlSyncRef.current = false;
      lastSyncedPermalinkSearchRef.current = window.location.search;
      return;
    }
    if (suspended) {
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void import("@/lib/permalink").then(({ buildPermalinkSearch, replaceUrlQuery }) => {
        if (cancelled) {
          return;
        }
        const mapView = mapViewRef.current;
        const search = buildPermalinkSearch({
          model: model || undefined,
          run: run || undefined,
          var: variable || undefined,
          ensembleView: ensembleView || undefined,
          product: product || undefined,
          fh: Number.isFinite(resolvedForecastHourPermalink)
            ? Number(resolvedForecastHourPermalink)
            : undefined,
          region: region || undefined,
          lat: mapView.lat,
          lon: mapView.lon,
          z: mapView.z,
        });
        if (search === lastSyncedPermalinkSearchRef.current || search === window.location.search) {
          lastSyncedPermalinkSearchRef.current = search;
          return;
        }
        replaceUrlQuery(search);
        lastSyncedPermalinkSearchRef.current = search;
      });
    }, PERMALINK_SYNC_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [
    permalinkHydrated,
    model,
    run,
    variable,
    ensembleView, product,
    resolvedForecastHourPermalink,
    region,
    mapViewTick,
    suspended,
  ]);
}
