import { memo, useCallback, useEffect, useMemo, useRef } from "react";
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
  toAbsoluteGridFrameUrl,
} from "@/lib/app-utils";
import { API_V4_BASE, OVERLAY_DEFAULT_OPACITY } from "@/lib/config";
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
  /** Fires once city value labels are applied for the current selection (screenshot gate). */
  onCityLabelsReady?: () => void;
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
  /**
   * True once the capabilities catalog has resolved. Until then
   * `prefersGridSubstrate` (and therefore `gridActive`) reads false for
   * grid-backed selections, so the readiness gate must not treat the
   * selection as non-grid yet.
   */
  capabilitiesReady: boolean;
  error: string | null;
};

const EMPTY_CONTOUR_PREFETCH_URLS: string[] = [];

function selectionEpochForKey(key: string): number {
  let hash = 0;
  for (let index = 0; index < key.length; index += 1) {
    hash = (hash * 31 + key.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
}

function ComparePanelComponent({
  side,
  model,
  variable,
  region,
  regionViews,
  basemapMode,
  showLegend,
  onMapReady,
  onFirstFrameReady,
  onCityLabelsReady,
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
  capabilitiesReady,
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
    return toAbsoluteGridFrameUrl(url);
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
      apiBase: API_V4_BASE,
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
        apiBase: API_V4_BASE,
      });
      if (url && url !== contourGeoJsonUrl && !urls.includes(url)) {
        urls.push(url);
      }
    }
    return urls;
  }, [activeGridFrameHour, contourGeoJsonUrl, frameRows, gridActive, gridFrameHours, gridManifest, model, resolvedRun, variable]);

  // First-frame readiness gate (mirrors CompareDiffPanel): a grid selection is
  // ready when the frame texture is ready AND painted; "painted" is satisfied
  // by a double-rAF after the frame is ready, or by any map `idle`. The idle
  // listener is persistent and reads live refs — the old `once("idle")` design
  // captured `gridActive` at map-load time (false until the runs fetch +
  // manifest probe resolved), so a fast basemap load signaled ready with no
  // weather overlay rendered, and the consumed listener could never re-arm
  // after a selection change.
  const onFirstFrameReadyRef = useRef(onFirstFrameReady);
  onFirstFrameReadyRef.current = onFirstFrameReady;
  const firstFrameReadyFiredRef = useRef(false);
  const gridFrameReadyRef = useRef(false);
  const paintedRef = useRef(false);
  const gridActiveRef = useRef(gridActive);
  gridActiveRef.current = gridActive;
  const loadingRef = useRef(loading);
  loadingRef.current = loading;
  const capabilitiesReadyRef = useRef(capabilitiesReady);
  capabilitiesReadyRef.current = capabilitiesReady;
  // Never reset: the basemap doesn't re-render on selection changes, so a past
  // idle stays a valid "basemap painted" fact for the non-grid path.
  const mapEverIdleRef = useRef(false);

  useEffect(() => {
    firstFrameReadyFiredRef.current = false;
    gridFrameReadyRef.current = false;
    paintedRef.current = false;
  }, [selectionKey, forecastHour, gridActive]);

  const signalFirstFrameReady = useCallback(() => {
    if (firstFrameReadyFiredRef.current) {
      return;
    }
    firstFrameReadyFiredRef.current = true;
    onFirstFrameReadyRef.current?.();
  }, []);

  const maybeSignalReady = useCallback(() => {
    if (gridActiveRef.current) {
      if (gridFrameReadyRef.current && paintedRef.current) {
        signalFirstFrameReady();
      }
      return;
    }
    // Non-grid selection: only signal once capabilities AND the loader have
    // fully settled — before capabilities resolve, a grid-backed selection
    // still reads as non-grid (prefersGridSubstrate=false) with all loader
    // flags false, which is exactly the premature-ready window this gate
    // exists to close.
    if (capabilitiesReadyRef.current && !loadingRef.current && mapEverIdleRef.current) {
      signalFirstFrameReady();
    }
  }, [signalFirstFrameReady]);

  const handleGridFrameReady = useCallback(() => {
    gridFrameReadyRef.current = true;
    // Wait for the painted frame to flush before signaling render-complete —
    // controller warm/prefetch work can starve `idle` indefinitely.
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        paintedRef.current = true;
        maybeSignalReady();
      }),
    );
    maybeSignalReady();
  }, [maybeSignalReady]);

  const handleMapReady = useCallback(
    (map: maplibregl.Map) => {
      onMapReady(map);
      const onIdle = () => {
        mapEverIdleRef.current = true;
        paintedRef.current = true;
        maybeSignalReady();
      };
      if (map.loaded()) {
        map.on("idle", onIdle);
      } else {
        map.once("load", () => {
          map.on("idle", onIdle);
        });
      }
    },
    [maybeSignalReady, onMapReady],
  );

  // Loader/capabilities settling is the readiness trigger for non-grid
  // selections — there is no grid-frame event to drive the gate.
  useEffect(() => {
    if (!loading && capabilitiesReady) {
      maybeSignalReady();
    }
  }, [loading, capabilitiesReady, maybeSignalReady]);

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
        onCityLabelsReady={onCityLabelsReady}
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

// Memoized: hover tracking lives in page state and re-renders the page per
// mousemove; without memo each move re-rendered both MapCanvas trees. All
// props are referentially stable across hover renders (page callbacks are
// useCallback'd, loader-derived objects are memoized).
export const ComparePanel = memo(ComparePanelComponent);

export default ComparePanel;
