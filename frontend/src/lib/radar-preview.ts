import type { StyleSpecification } from "maplibre-gl";
import type maplibregl from "maplibre-gl";

import type { GridManifestFrame, GridManifestResponse } from "@/lib/api";
import {
  CITY_LABEL_CANDIDATES_LAYER_ID,
  initCityLayers,
  moveCityLabelLayersToTop,
  setCityLabelNameOnlyMode,
} from "@/lib/city-labels";
import { API_ORIGIN, TILES_BASE } from "@/lib/config";
import { selectGridManifestLod } from "@/lib/grid-lod";
import { buildPermalinkSearch } from "@/lib/permalink";

export const RADAR_PREVIEW_MODEL = "mrms";
export const RADAR_PREVIEW_VARIABLE = "reflectivity";
export const RADAR_PREVIEW_REGION = "conus";
export const RADAR_PREVIEW_ZOOM = 9;
export const RADAR_PREVIEW_LOOP_MS = 875;
export const RADAR_PREVIEW_FRAME_UPDATE_TIMEOUT_MS = RADAR_PREVIEW_LOOP_MS * 2;
export const RADAR_PREVIEW_INITIAL_TIMEOUT_MS = 8000;
export const RADAR_PREVIEW_REFRESH_MS = 120_000;
export const RADAR_PREVIEW_LOOP_FRAME_COUNT = { min: 3, max: 5 } as const;
export const RADAR_PREVIEW_MAP_HEIGHT_CLASS = "h-[108px] md:h-[120px]";

const BOUNDARIES_VECTOR_TILES_URL = `${TILES_BASE.replace(/\/$/, "")}/tiles/v3/boundaries/v1/tilejson.json`;
const PREVIEW_BOUNDARY_SOURCE_ID = "radar-preview-boundaries";
export const RADAR_PREVIEW_STATE_LAYER_ID = "radar-preview-state-boundaries";
export const RADAR_PREVIEW_COUNTY_LAYER_ID = "radar-preview-county-boundaries";
export const RADAR_PREVIEW_COASTLINE_LAYER_ID = "radar-preview-coastline";

const IS_HIDPI = typeof window !== "undefined" && window.devicePixelRatio > 1;
const CARTO_TILE_SUFFIX = IS_HIDPI ? "@2x" : "";
const CARTO_TILE_SIZE = 256;

const CARTO_DARK_BASE_TILES = [
  `https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://b.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://c.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://d.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
];

export type PreviewFrame = {
  hour: number;
  url: string;
  validTime: string | null;
};

/** MRMS CONUS manifest bbox — v1 gate; edge cases near AK/Caribbean/ocean are acceptable. */
export function isRadarPreviewAvailable(lat: number, lon: number): boolean {
  return lon >= -134 && lon <= -60 && lat >= 24 && lat <= 55;
}

export function normalizeRadarFrameUrl(url: string | null | undefined): string | null {
  const trimmed = String(url ?? "").trim();
  if (!trimmed) {
    return null;
  }
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }
  const root = API_ORIGIN.replace(/\/$/, "");
  return `${root}${trimmed.startsWith("/") ? "" : "/"}${trimmed}`;
}

export function selectPreviewLod(manifest: GridManifestResponse | null): {
  lodLevel: number | null;
  lod: ReturnType<typeof selectGridManifestLod>;
} {
  const lod = selectGridManifestLod(manifest, RADAR_PREVIEW_ZOOM);
  return {
    lod,
    lodLevel: lod && Number.isFinite(Number(lod.level)) ? Number(lod.level) : null,
  };
}

export function resolvePreviewFrameForHour(
  manifest: GridManifestResponse,
  hour: number,
  preferredLodLevel: number | null,
): GridManifestFrame | null {
  const lods = Array.isArray(manifest.lods) ? manifest.lods : [];
  if (preferredLodLevel !== null) {
    const preferred = lods.find((entry) => Number(entry.level) === preferredLodLevel);
    const match = preferred?.frames?.find((frame) => Number(frame.fh) === hour);
    if (match) {
      return match;
    }
  }
  for (const lod of lods) {
    const match = lod.frames?.find((frame) => Number(frame.fh) === hour);
    if (match) {
      return match;
    }
  }
  return null;
}

export function buildPreviewLoopFrames(
  manifest: GridManifestResponse,
  lodLevel: number | null,
): PreviewFrame[] {
  const lod = selectGridManifestLod(manifest, RADAR_PREVIEW_ZOOM);
  const hours = (Array.isArray(lod?.frames) ? lod.frames : [])
    .map((frame) => Number(frame.fh))
    .filter(Number.isFinite)
    .sort((left, right) => left - right);

  const uniqueHours = Array.from(new Set(hours));
  const count = Math.min(RADAR_PREVIEW_LOOP_FRAME_COUNT.max, uniqueHours.length);
  const loopHours = uniqueHours.slice(-count);

  return loopHours
    .map((hour) => {
      const frame = resolvePreviewFrameForHour(manifest, hour, lodLevel);
      const url = normalizeRadarFrameUrl(frame?.url ?? frame?.file ?? null);
      if (!url) {
        return null;
      }
      return {
        hour,
        url,
        validTime: typeof frame?.valid_time === "string" ? frame.valid_time : null,
      };
    })
    .filter((frame): frame is PreviewFrame => frame !== null);
}

export function viewerRadarHref(lat: number, lon: number): string {
  return `/viewer${buildPermalinkSearch({
    model: RADAR_PREVIEW_MODEL,
    var: RADAR_PREVIEW_VARIABLE,
    region: RADAR_PREVIEW_REGION,
    lat,
    lon,
    z: RADAR_PREVIEW_ZOOM,
  })}`;
}

export function formatRadarFrameAge(validTime: string | null | undefined): string {
  if (!validTime) {
    return "";
  }
  const timestamp = Date.parse(validTime);
  if (!Number.isFinite(timestamp)) {
    return "";
  }
  const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60_000));
  if (minutes < 1) {
    return "just now";
  }
  return `${minutes}m ago`;
}

export function buildRadarPreviewMapStyle(): StyleSpecification {
  const boundaryColor = "#e8eef4";

  return {
    version: 8,
    sources: {
      "radar-preview-basemap": {
        type: "raster",
        tiles: CARTO_DARK_BASE_TILES,
        tileSize: CARTO_TILE_SIZE,
      },
      [PREVIEW_BOUNDARY_SOURCE_ID]: {
        type: "vector",
        url: BOUNDARIES_VECTOR_TILES_URL,
      },
    },
    layers: [
      {
        id: "radar-preview-background",
        type: "background",
        paint: {
          "background-color": "#1f2a33",
        },
      },
      {
        id: "radar-preview-basemap",
        type: "raster",
        source: "radar-preview-basemap",
        paint: {
          "raster-brightness-min": 0.08,
          "raster-brightness-max": 0.94,
          "raster-contrast": -0.06,
          "raster-saturation": -0.08,
        },
      },
      {
        id: RADAR_PREVIEW_STATE_LAYER_ID,
        type: "line",
        source: PREVIEW_BOUNDARY_SOURCE_ID,
        "source-layer": "boundaries",
        filter: ["==", "kind", "state"],
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": boundaryColor,
          "line-opacity": 0.34,
          "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.85, 9, 1.05, 11, 1.25],
          "line-blur": 0.08,
        },
      },
      {
        id: RADAR_PREVIEW_COUNTY_LAYER_ID,
        type: "line",
        source: PREVIEW_BOUNDARY_SOURCE_ID,
        "source-layer": "counties",
        minzoom: 5,
        filter: ["==", "kind", "county"],
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": boundaryColor,
          "line-opacity": 0.2,
          "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.45, 9, 0.62, 11, 0.78],
          "line-blur": 0.06,
        },
      },
      {
        id: RADAR_PREVIEW_COASTLINE_LAYER_ID,
        type: "line",
        source: PREVIEW_BOUNDARY_SOURCE_ID,
        "source-layer": "hydro",
        filter: ["==", "kind", "coastline"],
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": boundaryColor,
          "line-opacity": 0.24,
          "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.7, 9, 0.95, 11, 1.15],
          "line-blur": 0.1,
        },
      },
    ],
  };
}

/** Faint major-city labels for geographic context (above radar once layers are ordered). */
export async function installRadarPreviewCityLabels(map: maplibregl.Map): Promise<void> {
  const initialized = await initCityLayers(map);
  if (!initialized) {
    return;
  }
  setCityLabelNameOnlyMode(map, true);
  if (map.getLayer(CITY_LABEL_CANDIDATES_LAYER_ID)) {
    map.setPaintProperty(CITY_LABEL_CANDIDATES_LAYER_ID, "text-opacity", 0.4);
    map.setPaintProperty(CITY_LABEL_CANDIDATES_LAYER_ID, "text-color", "rgba(226, 244, 255, 0.58)");
    map.setLayoutProperty(CITY_LABEL_CANDIDATES_LAYER_ID, "text-size", 10);
  }
  moveCityLabelLayersToTop(map);
}
