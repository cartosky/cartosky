import { useEffect, useMemo, useState } from "react";

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

export function useRadarPreview(lat: number, lon: number, enabled: boolean): UseRadarPreviewResult {
  const [manifest, setManifest] = useState<GridManifestResponse | null>(null);
  const [frameRows, setFrameRows] = useState<FrameRow[]>([]);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setManifest(null);
      setFrameRows([]);
      setLoading(false);
      setError(null);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError(null);

    Promise.all([
      fetchGridManifest(
        RADAR_PREVIEW_MODEL,
        "latest",
        RADAR_PREVIEW_VARIABLE,
        RADAR_PREVIEW_REGION,
        null,
        { signal: controller.signal },
      ),
      fetchFrames(
        RADAR_PREVIEW_MODEL,
        "latest",
        RADAR_PREVIEW_VARIABLE,
        RADAR_PREVIEW_REGION,
        null,
        { signal: controller.signal },
      ),
    ])
      .then(([manifestResponse, framesResponse]) => {
        if (controller.signal.aborted) {
          return;
        }
        if (!manifestResponse) {
          setManifest(null);
          setFrameRows([]);
          setError("manifest_unavailable");
          return;
        }
        setManifest(manifestResponse);
        setFrameRows(Array.isArray(framesResponse) ? framesResponse : []);
        setError(null);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) {
          return;
        }
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
  }, [enabled, lat, lon]);

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
