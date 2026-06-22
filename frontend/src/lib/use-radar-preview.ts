import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  fetchFrames,
  fetchGridManifest,
  type FrameRow,
  type GridManifestResponse,
} from "@/lib/api";
import { buildLegend, extractLegendMeta } from "@/lib/app-utils";
import { OVERLAY_DEFAULT_OPACITY } from "@/lib/config";
import type { LegendPayload } from "@/components/map-legend";
import {
  buildPreviewLoopFrames,
  RADAR_PREVIEW_LOOP_FRAME_COUNT,
  RADAR_PREVIEW_MODEL,
  RADAR_PREVIEW_REFRESH_MS,
  RADAR_PREVIEW_REGION,
  RADAR_PREVIEW_VARIABLE,
  selectPreviewLod,
  type PreviewFrame,
} from "@/lib/radar-preview";

export type UseRadarPreviewResult = {
  manifest: GridManifestResponse | null;
  legend: LegendPayload | null;
  loopFrames: PreviewFrame[];
  lodLevel: number | null;
  loading: boolean;
  error: string | null;
  supportsAnimation: boolean;
};

type RadarPreviewPayload = {
  manifest: GridManifestResponse;
  frameRows: FrameRow[];
};

function buildRadarPreviewSignature(manifest: GridManifestResponse, loopFrames: PreviewFrame[]): string {
  const latest = loopFrames[loopFrames.length - 1];
  if (!latest) {
    return `${manifest.run}|empty`;
  }
  return `${manifest.run}|${latest.hour}|${latest.url}|${latest.validTime ?? ""}`;
}

async function loadRadarPreviewPayload(signal: AbortSignal): Promise<RadarPreviewPayload | null> {
  const [manifestResponse, framesResponse] = await Promise.all([
    fetchGridManifest(
      RADAR_PREVIEW_MODEL,
      "latest",
      RADAR_PREVIEW_VARIABLE,
      RADAR_PREVIEW_REGION,
      null,
      { signal },
    ),
    fetchFrames(
      RADAR_PREVIEW_MODEL,
      "latest",
      RADAR_PREVIEW_VARIABLE,
      RADAR_PREVIEW_REGION,
      null,
      { signal },
    ),
  ]);

  if (!manifestResponse) {
    return null;
  }

  return {
    manifest: manifestResponse,
    frameRows: Array.isArray(framesResponse) ? framesResponse : [],
  };
}

export function useRadarPreview(lat: number, lon: number, enabled: boolean): UseRadarPreviewResult {
  const [manifest, setManifest] = useState<GridManifestResponse | null>(null);
  const [frameRows, setFrameRows] = useState<FrameRow[]>([]);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const signatureRef = useRef("");

  const applyPayload = useCallback((payload: RadarPreviewPayload | null, options?: { initial?: boolean }) => {
    if (!payload) {
      if (options?.initial) {
        setManifest(null);
        setFrameRows([]);
        setError("manifest_unavailable");
      }
      return false;
    }

    const { lodLevel } = selectPreviewLod(payload.manifest);
    const nextLoopFrames = buildPreviewLoopFrames(payload.manifest, lodLevel);
    const nextSignature = buildRadarPreviewSignature(payload.manifest, nextLoopFrames);

    if (nextSignature === signatureRef.current && !options?.initial) {
      return false;
    }

    signatureRef.current = nextSignature;
    setManifest(payload.manifest);
    setFrameRows(payload.frameRows);
    setError(null);
    return true;
  }, []);

  useEffect(() => {
    if (!enabled) {
      signatureRef.current = "";
      setManifest(null);
      setFrameRows([]);
      setLoading(false);
      setError(null);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError(null);

    loadRadarPreviewPayload(controller.signal)
      .then((payload) => {
        if (controller.signal.aborted) {
          return;
        }
        applyPayload(payload, { initial: true });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) {
          return;
        }
        signatureRef.current = "";
        setManifest(null);
        setFrameRows([]);
        setError(err instanceof Error ? err.message : "radar_preview_failed");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [applyPayload, enabled, lat, lon]);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    let refreshController: AbortController | null = null;

    const refresh = () => {
      if (typeof document !== "undefined" && document.visibilityState !== "visible") {
        return;
      }
      refreshController?.abort();
      refreshController = new AbortController();
      loadRadarPreviewPayload(refreshController.signal)
        .then((payload) => {
          if (refreshController?.signal.aborted) {
            return;
          }
          applyPayload(payload);
        })
        .catch((err: unknown) => {
          if (refreshController?.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) {
            return;
          }
          // Keep the last good frames visible if a background refresh fails.
        });
    };

    const intervalId = window.setInterval(refresh, RADAR_PREVIEW_REFRESH_MS);
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        refresh();
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      refreshController?.abort();
    };
  }, [applyPayload, enabled, lat, lon]);

  const { lodLevel } = useMemo(() => selectPreviewLod(manifest), [manifest]);

  const loopFrames = useMemo(
    () => (manifest ? buildPreviewLoopFrames(manifest, lodLevel) : []),
    [manifest, lodLevel],
  );

  const legend = useMemo(() => {
    if (!manifest) {
      return null;
    }
    const frameMeta = extractLegendMeta(frameRows[0] ?? null);
    const fromFrame = buildLegend(frameMeta, OVERLAY_DEFAULT_OPACITY, RADAR_PREVIEW_MODEL);
    if (fromFrame) {
      return fromFrame;
    }
    const manifestMeta = {
      ...(typeof manifest.palette?.kind === "string" ? { kind: manifest.palette.kind } : {}),
      ...(typeof manifest.grid?.units === "string" ? { units: manifest.grid.units } : {}),
      ...(typeof manifest.display_name === "string" ? { display_name: manifest.display_name } : {}),
      ...(manifest.legend ? { legend: manifest.legend } : {}),
      var_key: manifest.var,
    };
    return buildLegend(manifestMeta, OVERLAY_DEFAULT_OPACITY, RADAR_PREVIEW_MODEL);
  }, [frameRows, manifest]);

  const supportsAnimation = loopFrames.length >= RADAR_PREVIEW_LOOP_FRAME_COUNT.min;

  return {
    manifest,
    legend,
    loopFrames,
    lodLevel,
    loading,
    error,
    supportsAnimation,
  };
}
