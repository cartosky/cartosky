import { useEffect, useState } from "react";
import type { BasemapMode } from "@/components/map-canvas";
import { OVERLAY_DEFAULT_OPACITY } from "@/lib/config";
import {
  defaultPointLabelsEnabledForSelection,
  readBasemapModePreference,
  readHrrrSmokePointLabelsPreference,
  readLegendVisibilityPreference,
  readNwsWarningsPreference,
  readPointLabelsPreference,
  readZoomControlsPreference,
  writeBasemapModePreference,
  writeHrrrSmokePointLabelsPreference,
  writeLegendVisibilityPreference,
  writeNwsWarningsPreference,
  writePointLabelsPreference,
  writeZoomControlsPreference,
  isHrrrSmokePointLabelsSelection,
} from "@/lib/app-utils";
import type { ViewerLayoutMode } from "@/lib/viewer-layout";

export interface UseDisplaySettingsReturn {
  basemapMode: BasemapMode;
  setBasemapMode: React.Dispatch<React.SetStateAction<BasemapMode>>;
  pointLabelsEnabled: boolean;
  setPointLabelsEnabled: React.Dispatch<React.SetStateAction<boolean>>;
  nwsWarningsEnabled: boolean;
  setNwsWarningsEnabled: React.Dispatch<React.SetStateAction<boolean>>;
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
 * Manages viewer display settings with localStorage persistence.
 *
 * Zoom controls and legend default behaviour by layout:
 *   - Desktop:        default ON  (null preference → true)
 *   - Mobile/tablet:  default OFF (null preference → false)
 *   - Legend on MOBILE (<768): default ON — Phase 8 (§8 State A decision 6)
 *     shows the compact chip at rest. Tablet keeps its current default. This
 *     is a READ-SIDE fallback only: nothing new is written to storage, so the
 *     rollback condition stays "revert the commits".
 *
 * Once a user explicitly toggles either setting on any layout, that choice is
 * persisted and restored on the next load — including on mobile/tablet. The
 * layout-specific default only applies when no preference has ever been saved.
 */
export function useDisplaySettings(
  viewerLayoutMode: ViewerLayoutMode,
  isDesktopViewerLayout: boolean,
  model: string,
  variable: string,
): UseDisplaySettingsReturn {
  const [basemapMode, setBasemapMode] = useState<BasemapMode>(() => readBasemapModePreference());
  const [pointLabelsPreference, setPointLabelsPreference] = useState(() => readPointLabelsPreference());
  const [hrrrSmokePointLabelsPreference, setHrrrSmokePointLabelsPreference] = useState<boolean | null>(
    () => readHrrrSmokePointLabelsPreference(),
  );
  const [nwsWarningsEnabled, setNwsWarningsEnabled] = useState(() => readNwsWarningsPreference());

  // null = never explicitly set; resolve to layout-appropriate default below.
  const [zoomPreference, setZoomPreference] = useState<boolean | null>(
    () => readZoomControlsPreference(),
  );
  const [legendPreference, setLegendPreference] = useState<boolean | null>(
    () => readLegendVisibilityPreference(),
  );

  const [displayPanelOpen, setDisplayPanelOpen] = useState(false);
  const [opacity, setOpacity] = useState(OVERLAY_DEFAULT_OPACITY);

  // Resolve null → layout default. Explicit true/false is always honoured.
  const isMobileLayout = viewerLayoutMode === "mobile";
  const smokeSelectionActive = isHrrrSmokePointLabelsSelection(model, variable);
  const pointLabelsDefault = defaultPointLabelsEnabledForSelection(model, variable);
  const pointLabelsEnabled = smokeSelectionActive
    ? (hrrrSmokePointLabelsPreference ?? pointLabelsDefault)
    : pointLabelsPreference;
  const zoomControlsVisible = zoomPreference ?? (isDesktopViewerLayout ? true : false);
  const legendDefault = isDesktopViewerLayout || isMobileLayout;
  const legendVisible = legendPreference ?? legendDefault;

  const setPointLabelsEnabled: React.Dispatch<React.SetStateAction<boolean>> = (value) => {
    if (smokeSelectionActive) {
      setHrrrSmokePointLabelsPreference((current) => {
        const effective = current ?? pointLabelsDefault;
        return typeof value === "function" ? value(effective) : value;
      });
      return;
    }
    setPointLabelsPreference((current) => (typeof value === "function" ? value(current) : value));
  };

  const setZoomControlsVisible: React.Dispatch<React.SetStateAction<boolean>> = (value) => {
    setZoomPreference((current) => {
      const effective = current ?? (isDesktopViewerLayout ? true : false);
      return typeof value === "function" ? value(effective) : value;
    });
  };

  const setLegendVisible: React.Dispatch<React.SetStateAction<boolean>> = (value) => {
    setLegendPreference((current) => {
      const effective = current ?? legendDefault;
      return typeof value === "function" ? value(effective) : value;
    });
  };

  useEffect(() => { writeBasemapModePreference(basemapMode); }, [basemapMode]);
  useEffect(() => { writePointLabelsPreference(pointLabelsPreference); }, [pointLabelsPreference]);
  useEffect(() => {
    if (hrrrSmokePointLabelsPreference !== null) {
      writeHrrrSmokePointLabelsPreference(hrrrSmokePointLabelsPreference);
    }
  }, [hrrrSmokePointLabelsPreference]);
  useEffect(() => { writeNwsWarningsPreference(nwsWarningsEnabled); }, [nwsWarningsEnabled]);

  useEffect(() => {
    if (zoomPreference !== null) writeZoomControlsPreference(zoomPreference);
  }, [zoomPreference]);

  useEffect(() => {
    if (legendPreference !== null) writeLegendVisibilityPreference(legendPreference);
  }, [legendPreference]);

  // Auto-close the display panel when leaving desktop layout.
  useEffect(() => {
    if (isDesktopViewerLayout || !displayPanelOpen) return;
    setDisplayPanelOpen(false);
  }, [displayPanelOpen, isDesktopViewerLayout]);

  return {
    basemapMode,
    setBasemapMode,
    pointLabelsEnabled,
    setPointLabelsEnabled,
    nwsWarningsEnabled,
    setNwsWarningsEnabled,
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
