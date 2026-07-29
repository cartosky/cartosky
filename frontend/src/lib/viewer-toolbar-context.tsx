import { createContext, useContext } from "react";
import type { BasemapMode } from "@/components/map-canvas";
import type { ObservedSourceStatusTone, TimeAxisMode } from "@/lib/time-axis";
import type { RailBreakpointClass, RailSection, RailState } from "@/lib/viewer-rail";
import type { ViewerLayoutMode } from "@/lib/viewer-layout";
import type { GroupedOption } from "@/lib/app-utils";
import type { CompositeLegendLayer, LegendPayload } from "@/components/map-legend";
import type { EnsembleProductOption } from "@/lib/api";

type Option = { value: string; label: string };
type VariableOption = Option & { group: string | null };

export type ViewerToolbarProps = {
  // Selectors
  region: string;
  onRegionChange: (value: string) => void;
  onLocationJump?: (lat: number, lon: number, zoom?: number, source?: "search" | "geolocation") => void;
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
  variableCatalog: VariableOption[];
  supportedVariableIds: string[];
  // Ensemble stats product selector (stats design §7 / D-D): present only
  // when the selected variable declares products; option availability is
  // resolved against the current run's manifest.
  ensembleProducts?: EnsembleProductOption[];
  product?: string;
  onProductChange?: (key: string) => void;
  productAvailability?: Record<string, boolean>;
  disabled?: boolean;
  // Non-blocking top progress bar: a model/variable/run/region switch has a
  // manifest fetch in flight (never true during cold boot — see App.tsx).
  isFrameSwitching?: boolean;
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
  nwsWarningsEnabled: boolean;
  onNwsWarningsEnabledChange: (next: boolean) => void;
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
  /**
   * Phase 7 (§7.1 decision 5): component-layer legends for composite products,
   * built from grid manifests App.tsx has already fetched. Never a new request.
   */
  compositeLegendLayers?: CompositeLegendLayer[];
  // Compare
  compareHref?: string;
  // Share
  onShare?: () => void;
  // Feedback
  onFeedback?: () => void;
  // Tour
  onReplayTour?: () => void;
  // Mobile controls sheet
  mobileControlsOpen?: boolean;
  onMobileControlsOpenChange?: (open: boolean) => void;
  // Keyboard shortcut sheet (Phase 6 overflow menu)
  onOpenShortcuts?: () => void;
  // Phase 6 rail (§6.2 §6.3 §6.4) — state stays in App.tsx; the rail and bar
  // are presentational consumers.
  railState?: RailState;
  railBreakpointClass?: RailBreakpointClass;
  onRailToggle?: () => void;
  onRailExpandTo?: (section: RailSection) => void;
  railPendingSection?: RailSection | null;
  onRailPendingSectionHandled?: () => void;
  // Phase 5 truth inputs the rail's SOURCE section speaks (§5.4 adapters).
  timeAxisMode?: TimeAxisMode;
  availableFrameHours?: number[];
  readyThroughFh?: number | null;
  expectedMaxFh?: number | null;
  /** `RunManifestResponse.last_updated`; absent hides the freshness row. */
  runLastUpdatedISO?: string | null;
  // Layout
  layoutMode?: ViewerLayoutMode;
};

const ViewerToolbarContext = createContext<ViewerToolbarProps | null>(null);

export { ViewerToolbarContext };

export function useViewerToolbar(): ViewerToolbarProps | null {
  return useContext(ViewerToolbarContext);
}
