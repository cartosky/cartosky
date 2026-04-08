import { useEffect, useState } from "react";
import type { BasemapMode } from "@/components/map-canvas";
import { OVERLAY_DEFAULT_OPACITY } from "@/lib/config";
import {
  readBasemapModePreference,
  readLegendVisibilityPreference,
  readPointLabelsPreference,
  readZoomControlsPreference,
  writeBasemapModePreference,
  writeLegendVisibilityPreference,
  writePointLabelsPreference,
  writeZoomControlsPreference,
} from "@/lib/app-utils";
import type { ViewerLayoutMode } from "@/lib/viewer-layout";

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
 * controls toggle, legend visibility (persisted for desktop and auto-hidden
 * on compact viewports), the display panel itself (auto-closed on
 * non-desktop), and overlay opacity.
 */
export function useDisplaySettings(
  viewerLayoutMode: ViewerLayoutMode,
  isDesktopViewerLayout: boolean,
): UseDisplaySettingsReturn {
  const [basemapMode, setBasemapMode] = useState<BasemapMode>(() => readBasemapModePreference());
  const [pointLabelsEnabled, setPointLabelsEnabled] = useState(() => readPointLabelsPreference());
  const [zoomControlsVisible, setZoomControlsVisible] = useState(() => readZoomControlsPreference());
  const [legendPreferenceVisible, setLegendPreferenceVisible] = useState<boolean | null>(() => readLegendVisibilityPreference());
  const [displayPanelOpen, setDisplayPanelOpen] = useState(false);
  const [opacity, setOpacity] = useState(OVERLAY_DEFAULT_OPACITY);
  const legendVisible = viewerLayoutMode === "desktop"
    ? (legendPreferenceVisible ?? true)
    : false;

  const setLegendVisible: React.Dispatch<React.SetStateAction<boolean>> = (value) => {
    setLegendPreferenceVisible((current) => {
      const effectiveCurrent = current ?? true;
      return typeof value === "function" ? value(effectiveCurrent) : value;
    });
  };

  // Persist basemap mode preference.
  useEffect(() => {
    writeBasemapModePreference(basemapMode);
  }, [basemapMode]);

  useEffect(() => {
    writePointLabelsPreference(pointLabelsEnabled);
  }, [pointLabelsEnabled]);

  useEffect(() => {
    writeZoomControlsPreference(zoomControlsVisible);
  }, [zoomControlsVisible]);

  // Persist the user's desktop legend preference while compact layouts keep
  // the legend hidden transiently.
  useEffect(() => {
    if (legendPreferenceVisible === null) {
      return;
    }
    writeLegendVisibilityPreference(legendPreferenceVisible);
  }, [legendPreferenceVisible]);

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
