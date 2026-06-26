import type { RegionPreset } from "@/lib/api";

const NORTH_AMERICA_BBOX = [-154, 12, -48, 72] as [number, number, number, number];
const NWS_HAZARDS_CONUS_VIEW_BBOX = [-126.0, 24.0, -66.0, 50.0] as [number, number, number, number];

export type MapRegionView = {
  center: [number, number];
  zoom: number;
  bbox?: [number, number, number, number];
  fitMinZoom?: number;
  fitMinZoomBreakpoint?: number;
  minZoom?: number;
  maxZoom?: number;
};

export function buildMapRegionViews(
  regionPresets: Record<string, RegionPreset>,
  options: { model?: string | null } = {},
): Record<string, MapRegionView> {
  return Object.fromEntries(
    Object.entries(regionPresets).map(([id, preset]) => {
      const isNwsHazardsConusView = options.model === "nws_hazards" && id === "conus";
      return [
        id,
        {
          center: [preset.defaultCenter[0], preset.defaultCenter[1]] as [number, number],
          zoom: preset.defaultZoom,
          bbox: isNwsHazardsConusView
            ? NWS_HAZARDS_CONUS_VIEW_BBOX
            : id === "na"
              ? NORTH_AMERICA_BBOX
              : preset.bbox,
          fitMinZoom: isNwsHazardsConusView ? 3 : undefined,
          fitMinZoomBreakpoint: isNwsHazardsConusView ? 640 : undefined,
          minZoom: preset.minZoom,
          maxZoom: preset.maxZoom,
        },
      ];
    }),
  );
}
