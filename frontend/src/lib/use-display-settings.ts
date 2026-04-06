import { useEffect, useRef, useState } from "react";
import type { BasemapMode } from "@/components/map-canvas";
import { OVERLAY_DEFAULT_OPACITY } from "@/lib/config";
import { readBasemapModePreference, writeBasemapModePreference } from "@/lib/app-utils";
import { detectViewerLayoutMode, type ViewerLayoutMode } from "@/lib/viewer-layout";

export interface UseDisplaySettingsReturn {
  basemapMode: BasemapMode;
  setBasemapMode: React.Dispatch<React.SetStateAction<BasemapMode>>;
  pointLabelsEnabled: boolean;
  setPointLabelsEnabled: React.Dispatch<React.SetStateAction<boolean>>;
  zoomControlsVisible: boolean;
  setZoomControlsVisible: React.Dispatch<React.SetStateAction<boolean>>;
  legendVisible: boolean;
  setLegendVisible: React.Dispatch<React.SetStateAction<boolean>>;
  displayPanelOpen: boolean;
  setDisplayPanelOpen: React.Dispatch<React.SetStateAction<boolean>>;
  opacity: number;
  setOpacity: React.Dispatch<React.SetStateAction<number>>;
}

/**
 * Manages the viewer display settings panel state:
 * basemap mode (with localStorage persistence), point-label toggle, zoom
 * controls toggle, legend visibility (auto-hidden on compact viewports),
 * the display panel itself (auto-closed on non-desktop), and overlay opacity.
 */
export function useDisplaySettings(
  viewerLayoutMode: ViewerLayoutMode,
  isDesktopViewerLayout: boolean,
): UseDisplaySettingsReturn {
  const [basemapMode, setBasemapMode] = useState<BasemapMode>(() => readBasemapModePreference());
  const [pointLabelsEnabled, setPointLabelsEnabled] = useState(true);
  const [zoomControlsVisible, setZoomControlsVisible] = useState(false);
  const [legendVisible, setLegendVisible] = useState(() =>
    typeof window === "undefined" ? true : detectViewerLayoutMode() === "desktop"
  );
  const [displayPanelOpen, setDisplayPanelOpen] = useState(false);
  const [opacity, setOpacity] = useState(OVERLAY_DEFAULT_OPACITY);
  const wasCompactViewportRef = useRef<boolean>(viewerLayoutMode !== "desktop");

  // Persist basemap mode preference.
  useEffect(() => {
    writeBasemapModePreference(basemapMode);
  }, [basemapMode]);

  // Auto-hide legend when entering a compact viewport; restore it when
  // returning to desktop if it was previously visible.
  useEffect(() => {
    setLegendVisible((current) => {
      if (viewerLayoutMode !== "desktop") {
        wasCompactViewportRef.current = true;
        return false;
      }

      const next = wasCompactViewportRef.current ? true : current;
      wasCompactViewportRef.current = false;
      return next;
    });
  }, [viewerLayoutMode]);

  // Auto-close the display panel when leaving desktop layout.
  useEffect(() => {
    if (isDesktopViewerLayout || !displayPanelOpen) {
      return;
    }
    setDisplayPanelOpen(false);
  }, [displayPanelOpen, isDesktopViewerLayout]);

  return {
    basemapMode,
    setBasemapMode,
    pointLabelsEnabled,
    setPointLabelsEnabled,
    zoomControlsVisible,
    setZoomControlsVisible,
    legendVisible,
    setLegendVisible,
    displayPanelOpen,
    setDisplayPanelOpen,
    opacity,
    setOpacity,
  };
}
