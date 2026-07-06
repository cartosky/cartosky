import { useCallback, useEffect, useMemo, useRef } from "react";
import type maplibregl from "maplibre-gl";

import {
  type FrameRow,
  type GridManifestFrame,
  type GridManifestResponse,
} from "@/lib/api";
import {
  buildLegend,
  extractLegendMeta,
  nearestFrame,
} from "@/lib/app-utils";
import { API_ORIGIN, OVERLAY_DEFAULT_OPACITY } from "@/lib/config";
import { resolveGridContourGeoJsonUrl } from "@/lib/grid-contours";
import { selectGridManifestLod } from "@/lib/grid-lod";
import type { MapRegionView } from "@/lib/map-region-views";
import { MapCanvas, type BasemapMode } from "@/components/map-canvas";
import { MapLegend } from "@/components/map-legend";

type ComparePanelProps = {
  side: "left" | "right";
  model: string;
  variable: string;
  region: string;
  regionViews: Record<string, MapRegionView>;
  basemapMode: BasemapMode;
  showLegend: boolean;
  onMapReady: (map: maplibregl.Map) => void;
  onFirstFrameReady?: () => void;
  /** Pass-through to MapCanvas: skips the one-shot region fit after load. */
  manualLocationJumpRef?: { current: boolean };
  onMapHover?: (lat: number, lon: number, x: number, y: number) => void;
  onMapHoverEnd?: () => void;
  // Derived from loader in parent — no loader runs inside this component
  resolvedRun: string;
  gridManifest: GridManifestResponse | null;
  gridFrameHours: number[];
  gridFrameByHour: Map<number, GridManifestFrame>;
  frameRows: FrameRow[];
  frameHours: number[];
  prefersGridSubstrate: boolean;
  forecastHour: number;
  loading: boolean;
  error: string | null;
};

const API_ROOT = API_ORIGIN.replace(/\/$/, "");
const EMPTY_CONTOUR_PREFETCH_URLS: string[] = [];

function selectionEpochForKey(key: string): number {
  let hash = 0;
  for (let index = 0; index < key.length; index += 1) {
    hash = (hash * 31 + key.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
}

export function ComparePanel({
  side,
  model,
  variable,
  region,
  regionViews,
  basemapMode,
  showLegend,
  onMapReady,
  onFirstFrameReady,
  manualLocationJumpRef,
  onMapHover,
  onMapHoverEnd,
  resolvedRun,
  gridManifest,
  gridFrameHours,
  gridFrameByHour,
  frameRows,
  frameHours,
  prefersGridSubstrate,
  forecastHour,
  loading,
  error,
}: ComparePanelProps) {
  // ── Active grid frame (derived locally, per spec) ──────────────────────
  const activeGridFrameHour = gridFrameHours.length > 0 ? nearestFrame(gridFrameHours, forecastHour) : null;
  const activeGridFrame = activeGridFrameHour !== null ? gridFrameByHour.get(activeGridFrameHour) ?? null : null;
  const activeGridFrameUrl = useMemo(() => {
    const url = activeGridFrame?.url;
    if (!url) {
      return null;
    }
    return /^https?:\/\//i.test(url) ? url : `${API_ROOT}${url.startsWith("/") ? "" : "/"}${url}`;
  }, [activeGridFrame]);

  const gridLodLevel = useMemo(() => {
    const lod = selectGridManifestLod(gridManifest, null);
    return lod ? Number(lod.level) : null;
  }, [gridManifest]);

  // ── Legend for the active variable ─────────────────────────────────────
  const frameByHour = useMemo(() => {
    const map = new Map<number, FrameRow>();
    for (const row of frameRows) {
      const fh = Number(row.fh);
      if (Number.isFinite(fh)) {
        map.set(fh, row);
      }
    }
    return map;
  }, [frameRows]);

  const activeFrameHour = frameHours.length > 0 ? nearestFrame(frameHours, forecastHour) : null;
  const currentFrame = activeFrameHour !== null ? frameByHour.get(activeFrameHour) ?? null : null;

  const legend = useMemo(() => {
    const frameMeta = extractLegendMeta(currentFrame) ?? extractLegendMeta(frameRows[0] ?? null);
    const fromFrame = buildLegend(frameMeta, OVERLAY_DEFAULT_OPACITY, model);
    if (fromFrame) {
      return fromFrame;
    }
    // Grid frame rows often carry no legend stops — fall back to the manifest's
    // palette so the colorbar still renders (mirrors the viewer's composite path).
    if (gridManifest) {
      const manifestMeta = {
        ...(typeof gridManifest.palette?.kind === "string" ? { kind: gridManifest.palette.kind } : {}),
        ...(typeof gridManifest.grid?.units === "string" ? { units: gridManifest.grid.units } : {}),
        ...(typeof gridManifest.display_name === "string" ? { display_name: gridManifest.display_name } : {}),
        ...(gridManifest.legend ? { legend: gridManifest.legend } : {}),
        var_key: gridManifest.var,
      };
      return buildLegend(manifestMeta, OVERLAY_DEFAULT_OPACITY, model);
    }
    return null;
  }, [currentFrame, frameRows, gridManifest, model]);

  // ── MapCanvas selection identity ───────────────────────────────────────
  const selectionKey = `${side}:${model}:${resolvedRun}:${variable}:${region}`;
  const selectionEpoch = useMemo(() => selectionEpochForKey(selectionKey), [selectionKey]);

  const gridActive = prefersGridSubstrate && Boolean(gridManifest) && Boolean(activeGridFrameUrl);

  const contourGeoJsonUrl = useMemo(() => {
    if (!gridActive) {
      return null;
    }
    return resolveGridContourGeoJsonUrl({
      model,
      run: resolvedRun,
      variable,
      hour: activeGridFrameHour,
      gridManifest,
      frameRows,
      apiBase: `${API_ROOT}/api/v4`,
    });
  }, [activeGridFrameHour, frameRows, gridActive, gridManifest, model, resolvedRun, variable]);

  const contourPrefetchUrls = useMemo(() => {
    if (!gridActive || activeGridFrameHour === null || gridFrameHours.length <= 1) {
      return EMPTY_CONTOUR_PREFETCH_URLS;
    }
    const pivotIndex = gridFrameHours.indexOf(activeGridFrameHour);
    const candidateHours = pivotIndex >= 0
      ? [
          ...gridFrameHours.slice(pivotIndex + 1, pivotIndex + 7),
          ...gridFrameHours.slice(Math.max(0, pivotIndex - 2), pivotIndex).reverse(),
        ]
      : gridFrameHours.slice(1, 7);
    const urls: string[] = [];
    for (const hour of candidateHours) {
      const url = resolveGridContourGeoJsonUrl({
        model,
        run: resolvedRun,
        variable,
        hour,
        gridManifest,
        frameRows,
        apiBase: `${API_ROOT}/api/v4`,
      });
      if (url && url !== contourGeoJsonUrl && !urls.includes(url)) {
        urls.push(url);
      }
    }
    return urls;
  }, [activeGridFrameHour, contourGeoJsonUrl, frameRows, gridActive, gridFrameHours, gridManifest, model, resolvedRun, variable]);

  const onFirstFrameReadyRef = useRef(onFirstFrameReady);
  onFirstFrameReadyRef.current = onFirstFrameReady;
  const firstFrameReadyFiredRef = useRef(false);
  const gridFrameReadyRef = useRef(false);
  const mapIdleReadyRef = useRef(false);

  useEffect(() => {
    firstFrameReadyFiredRef.current = false;
    gridFrameReadyRef.current = false;
    mapIdleReadyRef.current = false;
  }, [selectionKey, forecastHour, gridActive]);

  const signalFirstFrameReady = useCallback(() => {
    if (firstFrameReadyFiredRef.current) {
      return;
    }
    firstFrameReadyFiredRef.current = true;
    onFirstFrameReadyRef.current?.();
  }, []);

  const maybeSignalBothReady = useCallback(() => {
    if (gridFrameReadyRef.current && mapIdleReadyRef.current) {
      signalFirstFrameReady();
    }
  }, [signalFirstFrameReady]);

  const handleGridFrameReady = useCallback(() => {
    gridFrameReadyRef.current = true;
    maybeSignalBothReady();
  }, [maybeSignalBothReady]);

  const handleMapReady = useCallback(
    (map: maplibregl.Map) => {
      onMapReady(map);
      const onIdle = () => {
        if (gridActive) {
          mapIdleReadyRef.current = true;
          maybeSignalBothReady();
        } else {
          signalFirstFrameReady();
        }
      };
      if (map.loaded()) {
        map.once("idle", onIdle);
      } else {
        map.once("load", () => {
          map.once("idle", onIdle);
        });
      }
    },
    [gridActive, maybeSignalBothReady, onMapReady, signalFirstFrameReady],
  );

  return (
    <div className="relative w-full h-full overflow-hidden">
      <MapCanvas
        productId={model}
        selectionKey={selectionKey}
        selectionEpoch={selectionEpoch}
        gridManifest={gridActive ? gridManifest : null}
        gridLodLevel={gridActive ? gridLodLevel : null}
        gridFrameUrl={gridActive ? activeGridFrameUrl : null}
        gridFrameHour={gridActive && activeGridFrameHour !== null ? activeGridFrameHour : null}
        gridLegend={gridActive ? legend : null}
        gridActive={gridActive}
        contourGeoJsonUrl={gridActive ? contourGeoJsonUrl : null}
        contourPrefetchUrls={gridActive ? contourPrefetchUrls : EMPTY_CONTOUR_PREFETCH_URLS}
        variable={variable}
        region={region}
        regionViews={regionViews}
        opacity={OVERLAY_DEFAULT_OPACITY}
        mode="idle-warmup"
        basemapMode={basemapMode}
        onMapReady={handleMapReady}
        onGridFrameReady={handleGridFrameReady}
        manualLocationJumpRef={manualLocationJumpRef}
        onMapHover={onMapHover ? (lat, lon, x, y) => onMapHover(lat, lon, x, y) : undefined}
        onMapHoverEnd={onMapHoverEnd}
      />

      {/* Colorbar / legend */}
      {showLegend && legend ? (
        <div className="absolute top-3 right-3 z-20 max-w-[220px] rounded-xl border border-[#1a3a5c]/60 bg-[#04101e]/[0.82] shadow-[0_8px_32px_rgba(0,0,0,0.5),inset_0_1px_0_rgba(100,180,255,0.08)] backdrop-blur-md overflow-hidden">
          <MapLegend
            legend={legend}
            inline
            defaultExpanded={false}
          />
        </div>
      ) : null}

      {/* Loading / error status */}
      {loading ? (
        <div className="absolute top-3 right-3 z-20 rounded-lg bg-background/85 px-2 py-1 text-xs text-muted-foreground shadow-sm backdrop-blur">
          Loading…
        </div>
      ) : null}
      {error ? (
        <div className="absolute top-3 right-3 z-20 max-w-[60%] rounded-lg bg-destructive/90 px-2 py-1 text-xs text-destructive-foreground shadow-sm">
          {error}
        </div>
      ) : null}

    </div>
  );
}

export default ComparePanel;
