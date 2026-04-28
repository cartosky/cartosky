import { createContext, useContext } from "react";
import type { BasemapMode } from "@/components/map-canvas";
import type { ObservedSourceStatusTone } from "@/lib/time-axis";
import type { ViewerLayoutMode } from "@/lib/viewer-layout";
import type { GroupedOption } from "@/lib/app-utils";
import type { LegendPayload } from "@/components/map-legend";

type Option = { value: string; label: string };
type VariableOption = Option & { group: string | null };

export type ViewerToolbarProps = {
  // Selectors
  region: string;
  onRegionChange: (value: string) => void;
  model: string;
  onModelChange: (value: string) => void;
  run: string;
  onRunChange: (value: string) => void;
  variable: string;
  onVariableChange: (value: string) => void;
  regions: Option[];
  models: GroupedOption[];
  runs: Option[];
  variables: VariableOption[];
  disabled?: boolean;
  // Run metadata
  runDisplayLabel?: string;
  latestAvailableRunLabel?: string | null;
  hasNewerRunAvailable?: boolean;
  onViewLatestRun?: () => void;
  runSelectionLocked?: boolean;
  // Source status
  sourceStatusLabel?: string | null;
  sourceStatusDescription?: string | null;
  sourceStatusTone?: ObservedSourceStatusTone | null;
  runAvailabilityLabel?: string | null;
  runAvailabilityDescription?: string | null;
  runAvailabilityTone?: ObservedSourceStatusTone | null;
  // Display settings
  pointLabelsEnabled: boolean;
  onPointLabelsEnabledChange: (next: boolean) => void;
  legendVisible: boolean;
  onLegendVisibleChange: (next: boolean) => void;
  basemapMode: BasemapMode;
  onBasemapModeChange: (next: BasemapMode) => void;
  opacity: number;
  onOpacityChange: (next: number) => void;
  zoomControlsVisible: boolean;
  onZoomControlsVisibleChange: (next: boolean) => void;
  legendPopoverOpen: boolean;
  onLegendPopoverOpenChange: (next: boolean) => void;
  // Display panel open state
  displayPanelOpen: boolean;
  onDisplayPanelOpenChange: (next: boolean) => void;
  // Legend
  legend: LegendPayload | null;
  // Share
  onShare?: () => void;
  // Layout
  layoutMode?: ViewerLayoutMode;
};

const ViewerToolbarContext = createContext<ViewerToolbarProps | null>(null);

export { ViewerToolbarContext };

export function useViewerToolbar(): ViewerToolbarProps | null {
  return useContext(ViewerToolbarContext);
}
