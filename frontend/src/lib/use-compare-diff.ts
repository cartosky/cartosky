import { useEffect, useRef, useState } from "react";

import type { GridManifestResponse } from "@/lib/api";
import type { LegendPayload } from "@/components/map-legend";
import {
  buildDiffManifest,
  computeDiffGrid,
  fetchGridFrameBytes,
  type GridMeta,
} from "@/lib/compare-diff";
import { gridFrameCache } from "@/lib/grid-frame-cache";
import { buildDiffLegend, getDiffScale } from "@/lib/compare-diff-scales";

const DIFF_DEBOUNCE_MS = 150;

export type CompareDiffReadySteps = {
  leftFetched: boolean;
  rightFetched: boolean;
  computeDone: boolean;
};

const RESET_STEPS: CompareDiffReadySteps = {
  leftFetched: false,
  rightFetched: false,
  computeDone: false,
};

export type UseCompareDiffParams = {
  leftFrameUrl: string | null;
  rightFrameUrl: string | null;
  leftGridMeta: GridMeta | null;
  rightGridMeta: GridMeta | null;
  /** Needed for the diverging legend title (Left − Right). */
  leftModel: string;
  rightModel: string;
  varKey: string | null;
  /** False when not in diff mode — the hook is a no-op returning null state. */
  enabled: boolean;
  /** Adjacent-hour frame URLs (left side) to warm into GridFrameCache after a diff settles. */
  leftPrefetchUrls?: string[];
  /** Adjacent-hour frame URLs (right side) to warm into GridFrameCache after a diff settles. */
  rightPrefetchUrls?: string[];
};

export type UseCompareDiffResult = {
  diffManifest: GridManifestResponse | null;
  /** Object-URL of the packed diff frame, passed to MapCanvas as gridFrameUrl. */
  diffFrameUrl: string | null;
  diffLegend: LegendPayload | null;
  isLoading: boolean;
  error: string | null;
  readySteps: CompareDiffReadySteps;
};

/**
 * Orchestrates the diff pipeline (fetch → decode → resample → subtract → pack)
 * for `compare.tsx`. Debounced, abortable, and epoch-guarded; fails closed.
 * `CompareDiffPanel` is render-only and never calls into this — all diff
 * orchestration lives here (design doc, Data Pipeline ownership).
 */
export function useCompareDiff(params: UseCompareDiffParams): UseCompareDiffResult {
  const {
    leftFrameUrl,
    rightFrameUrl,
    leftGridMeta,
    rightGridMeta,
    leftModel,
    rightModel,
    varKey,
    enabled,
  } = params;

  const [diffManifest, setDiffManifest] = useState<GridManifestResponse | null>(null);
  const [diffFrameUrl, setDiffFrameUrl] = useState<string | null>(null);
  const [diffLegend, setDiffLegend] = useState<LegendPayload | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [readySteps, setReadySteps] = useState<CompareDiffReadySteps>(RESET_STEPS);

  // Bumped on every input change; async results from a stale epoch are discarded.
  const epochRef = useRef(0);
  // Object-URL of the currently-published synthetic manifest, revoked on replace.
  const blobUrlRef = useRef<string | null>(null);

  const revokePublishedBlob = () => {
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  };

  // Latest adjacent-hour URLs, read via ref so they never re-trigger the compute
  // effect (prefetch is a side benefit of a settled diff, not a compute input).
  const prefetchUrlsRef = useRef<string[]>([]);
  prefetchUrlsRef.current = [
    ...(params.leftPrefetchUrls ?? []),
    ...(params.rightPrefetchUrls ?? []),
  ];

  // Fire-and-forget, low-priority warming of adjacent frames into GridFrameCache.
  // No AbortSignal — prefetch must never cancel or be cancelled by the active
  // compute cycle; it only populates the cache for the next scrub step.
  const prefetchCancelRef = useRef<(() => void) | null>(null);
  const cancelPrefetch = () => {
    prefetchCancelRef.current?.();
    prefetchCancelRef.current = null;
  };
  const schedulePrefetch = () => {
    cancelPrefetch();
    const urls = prefetchUrlsRef.current.filter((url) => url && !gridFrameCache.has(url));
    if (urls.length === 0) {
      return;
    }
    const warm = () => {
      for (const url of urls) {
        void fetchGridFrameBytes(url).catch(() => {
          // Best-effort warm — a failed prefetch just means the next scrub
          // fetches normally; never surface it as an error.
        });
      }
    };
    if (typeof requestIdleCallback === "function") {
      const handle = requestIdleCallback(warm, { timeout: 1500 });
      prefetchCancelRef.current = () => cancelIdleCallback(handle);
    } else {
      const handle = window.setTimeout(warm, 200);
      prefetchCancelRef.current = () => window.clearTimeout(handle);
    }
  };

  useEffect(() => {
    const epoch = epochRef.current + 1;
    epochRef.current = epoch;
    cancelPrefetch();

    // Readiness must always re-confirm for the new selection (screenshot gate).
    setReadySteps(RESET_STEPS);

    const ready = Boolean(
      enabled && leftFrameUrl && rightFrameUrl && leftGridMeta && rightGridMeta && varKey,
    );
    if (!ready) {
      revokePublishedBlob();
      setDiffManifest(null);
      setDiffFrameUrl(null);
      setDiffLegend(null);
      setIsLoading(false);
      setError(null);
      return;
    }

    // If both frames are already cached (sequential scrub after prefetch), the
    // recompute is near-instant: keep the previous diff on screen and show no
    // loading overlay. Only a real network fetch clears the stale frame + spins.
    const bothCached = gridFrameCache.has(leftFrameUrl!) && gridFrameCache.has(rightFrameUrl!);
    if (bothCached) {
      setIsLoading(false);
    } else {
      revokePublishedBlob();
      setDiffManifest(null);
      setDiffFrameUrl(null);
      setDiffLegend(null);
      setIsLoading(true);
    }
    setError(null);

    const controller = new AbortController();
    const isCurrent = () => epochRef.current === epoch && !controller.signal.aborted;

    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const leftPromise = fetchGridFrameBytes(leftFrameUrl!, controller.signal).then((bytes) => {
            if (isCurrent()) {
              setReadySteps((steps) => ({ ...steps, leftFetched: true }));
            }
            return bytes;
          });
          const rightPromise = fetchGridFrameBytes(rightFrameUrl!, controller.signal).then((bytes) => {
            if (isCurrent()) {
              setReadySteps((steps) => ({ ...steps, rightFetched: true }));
            }
            return bytes;
          });
          const [leftBytes, rightBytes] = await Promise.all([leftPromise, rightPromise]);
          if (!isCurrent()) {
            return;
          }

          const { diffFloats, refMeta } = computeDiffGrid(
            leftBytes,
            rightBytes,
            leftGridMeta!,
            rightGridMeta!,
          );
          // Epoch may have advanced during compute even if the fetch wasn't
          // aborted in time — discard silently.
          if (!isCurrent()) {
            return;
          }

          const scale = getDiffScale(varKey);
          const { manifest, frameUrl } = buildDiffManifest(refMeta, diffFloats, scale);
          if (!isCurrent()) {
            // Late result: don't leak the blob we just created.
            URL.revokeObjectURL(frameUrl);
            return;
          }

          // Revoke the just-replaced frame now (in the cached path it was kept on
          // screen rather than revoked up front). It is fully rendered by now, so
          // this never truncates an in-flight controller fetch.
          if (blobUrlRef.current && blobUrlRef.current !== frameUrl) {
            URL.revokeObjectURL(blobUrlRef.current);
          }
          blobUrlRef.current = frameUrl;
          setDiffManifest(manifest);
          setDiffFrameUrl(frameUrl);
          setDiffLegend(buildDiffLegend(leftModel, rightModel, varKey!, scale));
          setReadySteps((steps) => ({ ...steps, computeDone: true }));
          setIsLoading(false);

          // Warm the adjacent forecast hours so the next scrub step is cache-hot.
          schedulePrefetch();
        } catch (err) {
          if (!isCurrent() || (err instanceof DOMException && err.name === "AbortError")) {
            return;
          }
          // Fail closed: clear output, drop readiness, surface the error.
          // (buildDiffManifest throws on a malformed diff, landing here.)
          revokePublishedBlob();
          setDiffManifest(null);
          setDiffFrameUrl(null);
          setDiffLegend(null);
          setReadySteps(RESET_STEPS);
          setError(err instanceof Error ? err.message : String(err));
          setIsLoading(false);
        }
      })();
    }, DIFF_DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    enabled,
    leftFrameUrl,
    rightFrameUrl,
    leftGridMeta,
    rightGridMeta,
    leftModel,
    rightModel,
    varKey,
  ]);

  // Revoke the last blob + cancel any pending prefetch on unmount.
  useEffect(() => () => {
    revokePublishedBlob();
    cancelPrefetch();
  }, []);

  return { diffManifest, diffFrameUrl, diffLegend, isLoading, error, readySteps };
}
