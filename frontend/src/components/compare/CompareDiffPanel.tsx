import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { Loader2 } from "lucide-react";

import { MapCanvas, type BasemapMode } from "@/components/map-canvas";
import type { LegendPayload } from "@/components/map-legend";
import type { GridManifestResponse } from "@/lib/api";
import { OVERLAY_DEFAULT_OPACITY } from "@/lib/config";
import type { MapRegionView } from "@/lib/map-region-views";
import { CompareDiffLegend } from "@/components/compare/CompareDiffLegend";

/**
 * Render-only difference-mode map panel. All loaders, fetching, and diff
 * computation live in `compare.tsx` / `useCompareDiff`; this component only
 * renders the resolved props (design doc, Data Pipeline ownership). It never
 * instantiates loaders, fetches frames, or calls into `compare-diff.ts`.
 */
export type CompareDiffPanelProps = {
  /** False → no comparable continuous variables; show the blocking empty state. */
  hasMutualEligibleVariables: boolean;
  leftModel: string;
  rightModel: string;
  variable: string;
  region: string;
  regionViews: Record<string, MapRegionView>;
  basemapMode: BasemapMode;
  showLegend: boolean;
  /** Pre-computed synthetic diff manifest from the pipeline. */
  diffManifest: GridManifestResponse | null;
  /** Object-URL of the packed diff frame (delivered out-of-band, not in the manifest). */
  diffFrameUrl: string | null;
  /** Client-built diverging legend payload. */
  diffLegend: LegendPayload | null;
  isLoading: boolean;
  error: string | null;
  onMapReady?: (map: MapLibreMap) => void;
  /** Fires once the diff frame has rendered + map is idle (readiness gate step 4). */
  onDiffMapReady?: () => void;
  /** Fires once city value labels are applied (readiness gate step 5 in screenshot mode). */
  onCityLabelsReady?: () => void;
  /** Pass-through to MapCanvas: skips the one-shot region fit after load. */
  manualLocationJumpRef?: { current: boolean };
  onMapHover?: (lat: number, lon: number, x: number, y: number) => void;
  onMapHoverEnd?: () => void;
};

export function CompareDiffPanel({
  hasMutualEligibleVariables,
  leftModel,
  variable,
  region,
  regionViews,
  basemapMode,
  showLegend,
  diffManifest,
  diffFrameUrl,
  diffLegend,
  isLoading,
  error,
  onMapReady,
  onDiffMapReady,
  onCityLabelsReady,
  manualLocationJumpRef,
  onMapHover,
  onMapHoverEnd,
}: CompareDiffPanelProps) {
  const frameUrl = diffFrameUrl;
  const gridActive = Boolean(diffManifest && frameUrl);

  // MapCanvas selection identity — changes whenever the diff frame changes so the
  // controller reloads. The blob URL is unique per compute, so it is sufficient.
  const selectionKey = `diff:${leftModel}:${variable}:${frameUrl ?? "none"}`;
  const [selectionEpoch, setSelectionEpoch] = useState(0);
  useEffect(() => {
    setSelectionEpoch((epoch) => epoch + 1);
  }, [selectionKey]);

  // Readiness gate step 4: the diff frame is rendered. We require the grid frame
  // texture to be ready AND painted. "Painted" is satisfied by the map's `idle`
  // event when it fires, but the grid controller's repaint/warm activity can keep
  // the map from ever going idle — so a double-rAF after the frame is ready is an
  // equally valid "rendered" signal (design: "onGridFrameReady / map idle equivalent").
  const onDiffMapReadyRef = useRef(onDiffMapReady);
  onDiffMapReadyRef.current = onDiffMapReady;
  const firedRef = useRef(false);
  const gridFrameReadyRef = useRef(false);
  const paintedRef = useRef(false);

  useEffect(() => {
    firedRef.current = false;
    gridFrameReadyRef.current = false;
    paintedRef.current = false;
  }, [selectionKey]);

  const maybeSignalReady = useCallback(() => {
    if (firedRef.current) {
      return;
    }
    if (gridFrameReadyRef.current && paintedRef.current) {
      firedRef.current = true;
      onDiffMapReadyRef.current?.();
    }
  }, []);

  const handleGridFrameReady = useCallback(() => {
    gridFrameReadyRef.current = true;
    // Wait for the painted frame to flush before signaling render-complete.
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        paintedRef.current = true;
        maybeSignalReady();
      }),
    );
    maybeSignalReady();
  }, [maybeSignalReady]);

  const handleMapReady = useCallback(
    (map: MapLibreMap) => {
      onMapReady?.(map);
      // Map idle is a strong "settled + painted" signal when it fires; treat it
      // as an alternate way to satisfy the painted condition.
      const onIdle = () => {
        paintedRef.current = true;
        maybeSignalReady();
      };
      if (map.loaded()) {
        map.once("idle", onIdle);
      } else {
        map.once("load", () => {
          map.once("idle", onIdle);
        });
      }
    },
    [maybeSignalReady, onMapReady],
  );

  const mapHoverHandler = useMemo(
    () => (onMapHover ? (lat: number, lon: number, x: number, y: number) => onMapHover(lat, lon, x, y) : undefined),
    [onMapHover],
  );

  if (!hasMutualEligibleVariables) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-[#07111f] px-6">
        <p className="max-w-sm text-center text-sm font-medium text-white/60">
          These models have no comparable continuous variables in common.
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-[#07111f] px-6">
        <div className="max-w-sm text-center">
          <p className="text-sm font-medium text-white/80">Unable to compute difference</p>
          <p className="mt-1 text-xs text-white/45">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full overflow-hidden">
      <MapCanvas
        productId={leftModel}
        selectionKey={selectionKey}
        selectionEpoch={selectionEpoch}
        gridManifest={gridActive ? diffManifest : null}
        gridLodLevel={gridActive ? 0 : null}
        gridFrameUrl={gridActive ? frameUrl : null}
        gridFrameHour={gridActive ? 0 : null}
        gridLegend={gridActive ? diffLegend : null}
        gridActive={gridActive}
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
        onMapHover={mapHoverHandler}
        onMapHoverEnd={onMapHoverEnd}
      />

      {showLegend && diffLegend ? <CompareDiffLegend legend={diffLegend} /> : null}

      {isLoading ? (
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-[#04101e]/35 backdrop-blur-[1px]">
          <Loader2 className="h-6 w-6 animate-spin text-cyan-200/80" />
        </div>
      ) : null}
    </div>
  );
}

export default CompareDiffPanel;
