import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Layers } from "lucide-react";
import maplibregl, { type LayerSpecification, type StyleSpecification } from "maplibre-gl";
import type { GeoJSON } from "geojson";

import type { LegendPayload } from "@/components/map-legend";
import { sanitizeAnchorFeatureCollection, type AnchorFeatureCollection } from "@/lib/anchor-labels";
import type { GridManifestResponse } from "@/lib/api";
import { API_ORIGIN, MAP_VIEW_DEFAULTS, TILES_BASE } from "@/lib/config";
import { GRID_WEBGL_LAYER_ID, GridWebglLayerController, type GridFrameVisiblePayload } from "@/lib/grid-webgl";
import { startNetworkTimer, trackNetworkFetchDuration } from "@/lib/network-diagnostics";
import type { SampleTooltipState } from "@/lib/use-sample-tooltip";

const IS_HIDPI = typeof window !== "undefined" && window.devicePixelRatio > 1;
const CARTO_TILE_SUFFIX = IS_HIDPI ? "@2x" : "";
const CARTO_TILE_SIZE = IS_HIDPI ? 512 : 256;

const CARTO_LIGHT_BASE_TILES = [
  `https://a.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://b.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://c.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://d.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
];

const CARTO_LIGHT_LABEL_TILES = [
  `https://a.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://b.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://c.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://d.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
];

const CARTO_DARK_BASE_TILES = [
  `https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://b.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://c.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://d.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
];

const CARTO_DARK_LABEL_TILES = [
  `https://a.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://b.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://c.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
  `https://d.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}${CARTO_TILE_SUFFIX}.png`,
];

const BOUNDARIES_VECTOR_TILES_URL = `${TILES_BASE}/tiles/v3/boundaries/v1/tilejson.json`;

type RegionView = {
  center: [number, number];
  zoom: number;
  bbox?: [number, number, number, number];
  minZoom?: number;
  maxZoom?: number;
};

export type BasemapMode = "light" | "dark";

type PlaybackMode = "autoplay" | "scrub" | "variable-switch";

/** Total prefetch budget for forecast scrub (ahead + behind). */
const FORECAST_SCRUB_PREFETCH_BUDGET = 10;
/** Minimum behind-direction slots during forecast scrub. */
const FORECAST_SCRUB_MIN_BEHIND = 1;
/** Minimum ahead-direction slots during forecast scrub. */
const FORECAST_SCRUB_MIN_AHEAD = 2;
const OBSERVED_MOBILE_AUTOPLAY_PREFETCH_AHEAD = 4;
const OBSERVED_MOBILE_AUTOPLAY_PREFETCH_BEHIND = 1;
const OBSERVED_MOBILE_SCRUB_PREFETCH_BUDGET = 6;
const OBSERVED_MOBILE_SCRUB_MIN_AHEAD = 2;
const OBSERVED_MOBILE_SCRUB_MIN_BEHIND = 2;
const ANCHOR_HOVER_RESUME_DELAY_MS = 30;
const ANCHOR_COLLISION_RADIUS_MIN_KM = 18;
const ANCHOR_COLLISION_RADIUS_MAX_KM = 170;

const CONTOUR_SOURCE_ID = "twf-contours";
const CONTOUR_LAYER_ID = "twf-contours";
const CONTOUR_LABEL_LAYER_ID = "twf-contour-labels";
const VECTOR_SOURCE_IDS = ["twf-vectors-a", "twf-vectors-b"] as const;
const VECTOR_FILL_LAYER_IDS = ["twf-vectors-fill-a", "twf-vectors-fill-b"] as const;
const VECTOR_LINE_LAYER_IDS = ["twf-vectors-line-a", "twf-vectors-line-b"] as const;
const VECTOR_TRANSITION_MS = 180;
const STATE_BOUNDARY_SOURCE_ID = "twf-boundaries";
const COASTLINE_LAYER_ID = "twf-coastline";
const STATE_BOUNDARY_LAYER_ID = "twf-state-boundaries";
const COUNTRY_BOUNDARY_LAYER_ID = "twf-country-boundaries";
const COUNTY_BOUNDARY_LAYER_ID = "twf-county-boundaries";
const LAKE_MASK_LAYER_ID = "twf-lake-mask";
const LAKE_SHORELINE_LAYER_ID = "twf-lake-shoreline";

const EMPTY_FEATURE_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

function contourLabelFromValue(value: unknown): string | null {
  const numericValue = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numericValue)) {
    return null;
  }
  return String(Math.round(numericValue));
}

function withContourLabels(payload: GeoJSON.FeatureCollection): GeoJSON.FeatureCollection {
  if (!payload || payload.type !== "FeatureCollection" || !Array.isArray(payload.features)) {
    return EMPTY_FEATURE_COLLECTION;
  }

  return {
    ...payload,
    features: payload.features.map((feature) => {
      const properties = feature.properties && typeof feature.properties === "object" ? feature.properties : {};
      const label = contourLabelFromValue((properties as Record<string, unknown>).value);
      if (!label) {
        return feature;
      }
      return {
        ...feature,
        properties: {
          ...properties,
          label,
        },
      };
    }),
  };
}

function isMobileDevice(): boolean {
  if (typeof navigator === "undefined") {
    return false;
  }
  return /android|iphone|ipad|ipod|mobile/i.test(navigator.userAgent);
}

type GridPaintSettings = {
  contrast: number;
  saturation: number;
  brightnessMin: number;
  brightnessMax: number;
};

function getGridPaintSettings(variable?: string, basemapMode: BasemapMode = "light"): GridPaintSettings {
  return {
    contrast: 0,
    saturation: 0,
    brightnessMin: 0,
    brightnessMax: 1,
  };
}

function getBoundaryLineColor(basemapMode: BasemapMode): string {
  return basemapMode === "dark" ? "#f3f4f6" : "#000000";
}

function getLakeFillColor(basemapMode: BasemapMode): string {
  return basemapMode === "dark" ? "#2C353C" : "#d4dadc";
}

function getBasemapPaintSettings(basemapMode: BasemapMode): {
  "raster-brightness-min": number;
  "raster-brightness-max": number;
  "raster-contrast": number;
  "raster-saturation": number;
} {
  if (basemapMode === "dark") {
    return {
      "raster-brightness-min": 0.08,
      "raster-brightness-max": 0.94,
      "raster-contrast": -0.06,
      "raster-saturation": -0.08,
    };
  }

  return {
    "raster-brightness-min": 0,
    "raster-brightness-max": 1,
    "raster-contrast": 0,
    "raster-saturation": 0,
  };
}

function getMapBackgroundColor(basemapMode: BasemapMode): string {
  return basemapMode === "dark" ? "#1f2a33" : "#e8edf1";
}

type LabelOpacityExpression = readonly [
  "interpolate",
  readonly ["linear"],
  readonly ["zoom"],
  number,
  number,
  number,
  number,
];

function getLabelPaintSettings(basemapMode: BasemapMode): {
  "raster-resampling": "linear";
  "raster-opacity": number | LabelOpacityExpression;
  "raster-contrast": number;
  "raster-saturation": number;
  "raster-brightness-min": number;
  "raster-brightness-max": number;
} {
  const labelOpacityByZoom = ["interpolate", ["linear"], ["zoom"], 4.3, 0, 5.1, 1] as const;
  if (basemapMode === "dark") {
    return {
      "raster-resampling": "linear",
      "raster-opacity": labelOpacityByZoom,
      "raster-contrast": 0.1,
      "raster-saturation": -0.06,
      "raster-brightness-min": 0.05,
      "raster-brightness-max": 1,
    };
  }
  return {
    "raster-resampling": "linear",
    "raster-opacity": labelOpacityByZoom,
    "raster-contrast": 0.08,
    "raster-saturation": -0.06,
    "raster-brightness-min": 0,
    "raster-brightness-max": 1,
  };
}

function setLayerVisibility(map: maplibregl.Map, id: string, visible: boolean) {
  if (!map.getLayer(id)) {
    return;
  }
  map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
}

function gridOverlayBeforeLayerId(map: maplibregl.Map): string {
  if (map.getLayer(CONTOUR_LAYER_ID)) {
    return CONTOUR_LAYER_ID;
  }
  return COASTLINE_LAYER_ID;
}

function buildVectorBufferLayers(): LayerSpecification[] {
  return [0, 1].flatMap((bufferIndex) => {
    const sourceId = VECTOR_SOURCE_IDS[bufferIndex as 0 | 1];
    const fillLayerId = VECTOR_FILL_LAYER_IDS[bufferIndex as 0 | 1];
    const lineLayerId = VECTOR_LINE_LAYER_IDS[bufferIndex as 0 | 1];
    return [
      {
        id: fillLayerId,
        type: "fill",
        source: sourceId,
        layout: {
          visibility: "none",
          "fill-sort-key": ["coalesce", ["get", "sort_rank"], 0] as any,
        },
        paint: {
          "fill-color": ["coalesce", ["get", "fill"], "#ffffff"] as any,
          "fill-opacity": vectorFillOpacityExpression(0) as any,
        },
      } as LayerSpecification,
      {
        id: lineLayerId,
        type: "line",
        source: sourceId,
        layout: {
          visibility: "none",
          "line-join": "round",
          "line-cap": "round",
          "line-sort-key": ["coalesce", ["get", "sort_rank"], 0] as any,
        },
        paint: {
          "line-color": ["coalesce", ["get", "stroke"], "#000000"] as any,
          "line-opacity": 0,
          "line-width": ["coalesce", ["get", "stroke_width"], 1.25] as any,
        },
      } as LayerSpecification,
    ];
  });
}

function vectorFillOpacityExpression(fade: number) {
  return ["*", Math.max(0, Math.min(1, fade)), ["coalesce", ["get", "fill_opacity"], 0.65]] as const;
}

function setVectorLayerFade(map: maplibregl.Map, bufferIndex: 0 | 1, fade: number) {
  const fillLayerId = VECTOR_FILL_LAYER_IDS[bufferIndex];
  const lineLayerId = VECTOR_LINE_LAYER_IDS[bufferIndex];
  if (map.getLayer(fillLayerId)) {
    map.setPaintProperty(fillLayerId, "fill-opacity", vectorFillOpacityExpression(fade));
  }
  if (map.getLayer(lineLayerId)) {
    map.setPaintProperty(lineLayerId, "line-opacity", Math.max(0, Math.min(1, fade)));
  }
}

type AnchorMarkerRecord = {
  marker: maplibregl.Marker;
  element: HTMLDivElement;
  chip: HTMLDivElement;
};

function snapAnchorMarkerToPixels(map: maplibregl.Map, record: AnchorMarkerRecord) {
  const { lng, lat } = record.marker.getLngLat();
  const projected = map.project([lng, lat]);
  record.element.style.transform = `translate(-50%, -50%) translate(${Math.round(projected.x)}px, ${Math.round(projected.y)}px)`;
}

type AnchorTooltipState = {
  cityName: string;
  x: number;
  y: number;
};

type ActiveAnchorMarker = {
  id: string;
  lngLat: [number, number];
  label: string;
  cityName: string;
  state: string;
  st: string;
  priority: number;
};

function haversineKm(a: [number, number], b: [number, number]): number {
  const [lonA, latA] = a;
  const [lonB, latB] = b;
  const earthRadiusKm = 6371;
  const latDelta = (latB - latA) * Math.PI / 180;
  const lonDelta = (lonB - lonA) * Math.PI / 180;
  const latARadians = latA * Math.PI / 180;
  const latBRadians = latB * Math.PI / 180;
  const latSin = Math.sin(latDelta / 2);
  const lonSin = Math.sin(lonDelta / 2);
  const arc = latSin * latSin
    + Math.cos(latARadians) * Math.cos(latBRadians) * lonSin * lonSin;

  return 2 * earthRadiusKm * Math.asin(Math.sqrt(arc));
}

function anchorPriorityFromId(id: string): number {
  const parts = id.split("_");
  const suffix = Number(parts[parts.length - 1]);
  if (!Number.isFinite(suffix) || suffix < 1) {
    return Number.MAX_SAFE_INTEGER;
  }
  return suffix;
}

function interpolateAnchorCollisionRadiusKm(zoom: number): number {
  const zoomStops: Array<[number, number]> = [
    [3, ANCHOR_COLLISION_RADIUS_MAX_KM],
    [4.5, 125],
    [6, 82],
    [7.5, 52],
    [9, 30],
    [11, ANCHOR_COLLISION_RADIUS_MIN_KM],
  ];

  if (zoom <= zoomStops[0][0]) {
    return zoomStops[0][1];
  }

  for (let index = 1; index < zoomStops.length; index += 1) {
    const [endZoom, endRadius] = zoomStops[index];
    const [startZoom, startRadius] = zoomStops[index - 1];
    if (zoom <= endZoom) {
      const progress = (zoom - startZoom) / (endZoom - startZoom);
      return startRadius + (endRadius - startRadius) * progress;
    }
  }

  return ANCHOR_COLLISION_RADIUS_MIN_KM;
}

function thinAnchorMarkers(markers: ActiveAnchorMarker[], zoom: number): ActiveAnchorMarker[] {
  const sorted = [...markers].sort((left, right) => {
    if (left.priority !== right.priority) {
      return left.priority - right.priority;
    }
    return left.id.localeCompare(right.id);
  });

  const collisionRadiusKm = interpolateAnchorCollisionRadiusKm(zoom);
  const accepted: ActiveAnchorMarker[] = [];
  for (const marker of sorted) {
    const overlapsExisting = accepted.some(
      (existing) => haversineKm(existing.lngLat, marker.lngLat) < collisionRadiusKm
    );
    if (!overlapsExisting) {
      accepted.push(marker);
    }
  }
  return accepted;
}

function getActiveAnchorMarkers(
  collection: AnchorFeatureCollection | null | undefined,
  zoom: number
): ActiveAnchorMarker[] {
  const sanitized = sanitizeAnchorFeatureCollection(collection);
  if (!sanitized) {
    return [];
  }

  const activeMarkers: ActiveAnchorMarker[] = [];
  for (const feature of sanitized.features) {
    const id = typeof feature.id === "string" ? feature.id : null;
    const coordinates = feature.geometry?.type === "Point" ? feature.geometry.coordinates : null;
    const lng = Number(coordinates?.[0]);
    const lat = Number(coordinates?.[1]);
    const label = typeof feature.properties?.label === "string" ? feature.properties.label.trim() : "";
    const cityName = typeof feature.properties?.city === "string" ? feature.properties.city.trim() : "";
    const stateName = typeof feature.properties?.state === "string" ? feature.properties.state.trim() : "";
    const stAbbr = typeof feature.properties?.st === "string" ? feature.properties.st.trim() : "";
    const active = feature.properties?.active === true;
    if (!id || !active || !label || !cityName || !Number.isFinite(lng) || !Number.isFinite(lat)) {
      continue;
    }
    activeMarkers.push({
      id,
      lngLat: [lng, lat],
      label,
      cityName,
      state: stateName,
      st: stAbbr,
      priority: anchorPriorityFromId(id),
    });
  }

  return thinAnchorMarkers(activeMarkers, zoom);
}

export function buildMapStyle(
  contourGeoJsonUrl?: string | null,
  vectorGeoJsonUrl?: string | null,
  basemapMode: BasemapMode = "light"
): StyleSpecification {
  void vectorGeoJsonUrl;
  const basemapTiles = basemapMode === "dark" ? CARTO_DARK_BASE_TILES : CARTO_LIGHT_BASE_TILES;
  const labelTiles = basemapMode === "dark" ? CARTO_DARK_LABEL_TILES : CARTO_LIGHT_LABEL_TILES;
  const mapBackgroundColor = getMapBackgroundColor(basemapMode);
  const boundaryLineColor = getBoundaryLineColor(basemapMode);
  const lakeFillColor = getLakeFillColor(basemapMode);
  const basemapPaint = getBasemapPaintSettings(basemapMode);
  const labelPaint = getLabelPaintSettings(basemapMode);

  return {
    version: 8,
    glyphs: "https://basemaps.cartocdn.com/gl/fonts/{fontstack}/{range}.pbf",
    sources: {
      "twf-basemap": {
        type: "raster",
        tiles: basemapTiles,
        tileSize: CARTO_TILE_SIZE,
      },
      "twf-labels": {
        type: "raster",
        tiles: labelTiles,
        tileSize: CARTO_TILE_SIZE,
      },
      [STATE_BOUNDARY_SOURCE_ID]: {
        type: "vector",
        url: BOUNDARIES_VECTOR_TILES_URL,
      },
      [CONTOUR_SOURCE_ID]: {
        type: "geojson",
        data: contourGeoJsonUrl ? contourGeoJsonUrl : EMPTY_FEATURE_COLLECTION,
      },
      [VECTOR_SOURCE_IDS[0]]: {
        type: "geojson",
        data: EMPTY_FEATURE_COLLECTION,
      },
      [VECTOR_SOURCE_IDS[1]]: {
        type: "geojson",
        data: EMPTY_FEATURE_COLLECTION,
      },
    },
    layers: [
      {
        id: "twf-background",
        type: "background",
        paint: {
          "background-color": mapBackgroundColor,
        },
      },
      {
        id: "twf-basemap",
        type: "raster",
        source: "twf-basemap",
        paint: basemapPaint,
      },
      {
        id: COASTLINE_LAYER_ID,
        type: "line",
        source: STATE_BOUNDARY_SOURCE_ID,
        "source-layer": "hydro",
        filter: ["==", "kind", "coastline"],
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": boundaryLineColor,
          "line-opacity": 0.86,
          "line-width": ["interpolate", ["linear"], ["zoom"], 4, 0.95, 7, 1.3, 10, 1.7],
          "line-blur": ["interpolate", ["linear"], ["zoom"], 3, 0.18, 6, 0.1, 10, 0.04],
        },
      },
      {
        id: COUNTRY_BOUNDARY_LAYER_ID,
        type: "line",
        source: STATE_BOUNDARY_SOURCE_ID,
        "source-layer": "boundaries",
        filter: ["==", "kind", "country"],
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": boundaryLineColor,
          "line-opacity": 0.78,
          "line-width": ["interpolate", ["linear"], ["zoom"], 4, 0.9, 7, 1.2, 10, 1.55],
          "line-blur": ["interpolate", ["linear"], ["zoom"], 3, 0.16, 6, 0.08, 10, 0.03],
        },
      },
      {
        id: STATE_BOUNDARY_LAYER_ID,
        type: "line",
        source: STATE_BOUNDARY_SOURCE_ID,
        "source-layer": "boundaries",
        filter: ["==", "kind", "state"],
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": boundaryLineColor,
          "line-opacity": 0.92,
          "line-width": ["interpolate", ["linear"], ["zoom"], 4, 1.1, 7, 1.5, 10, 1.9],
          "line-blur": ["interpolate", ["linear"], ["zoom"], 3, 0.14, 6, 0.08, 10, 0.03],
        },
      },
      {
        id: COUNTY_BOUNDARY_LAYER_ID,
        type: "line",
        source: STATE_BOUNDARY_SOURCE_ID,
        "source-layer": "counties",
        minzoom: 5,
        maxzoom: 10,
        filter: ["==", "kind", "county"],
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": boundaryLineColor,
          "line-opacity": ["interpolate", ["linear"], ["zoom"], 5, 0.68, 6, 0.66, 7, 0.64, 8, 0.62, 10, 0.58],
          "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.58, 6, 0.72, 8, 0.88, 10, 1],
          "line-blur": ["interpolate", ["linear"], ["zoom"], 5, 0.12, 7, 0.08, 10, 0.02],
        },
      },
      {
        id: LAKE_MASK_LAYER_ID,
        type: "fill",
        source: STATE_BOUNDARY_SOURCE_ID,
        "source-layer": "hydro",
        filter: ["==", "kind", "great_lake_polygon"],
        paint: {
          "fill-color": lakeFillColor,
          "fill-opacity": 1,
        },
      },
      {
        id: LAKE_SHORELINE_LAYER_ID,
        type: "line",
        source: STATE_BOUNDARY_SOURCE_ID,
        "source-layer": "hydro",
        minzoom: 3,
        filter: ["==", "kind", "great_lake_shoreline"],
        layout: {
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": boundaryLineColor,
          "line-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.45, 4, 0.62, 5, 0.75, 7, 0.9, 10, 0.9],
          "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 4, 0.75, 5, 1.05, 7, 1.4, 10, 1.8],
        },
      },
      {
        id: CONTOUR_LAYER_ID,
        type: "line",
        source: CONTOUR_SOURCE_ID,
        layout: {
          visibility: contourGeoJsonUrl ? "visible" : "none",
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": boundaryLineColor,
          "line-opacity": 0.9,
          "line-width": ["interpolate", ["linear"], ["zoom"], 4, 1, 8, 2, 12, 3],
        },
      },
      {
        id: CONTOUR_LABEL_LAYER_ID,
        type: "symbol",
        source: CONTOUR_SOURCE_ID,
        layout: {
          visibility: contourGeoJsonUrl ? "visible" : "none",
          "symbol-placement": "line",
          "symbol-spacing": ["interpolate", ["linear"], ["zoom"], 4, 420, 7, 360, 10, 300],
          "text-field": ["get", "label"],
          "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
          "text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 8, 11.5, 12, 13],
          "text-rotation-alignment": "map",
          "text-pitch-alignment": "viewport",
          "text-keep-upright": true,
          "text-allow-overlap": false,
          "text-ignore-placement": false,
          "text-padding": 8,
        },
        paint: {
          "text-color": basemapMode === "dark" ? "rgba(248,250,252,0.72)" : "rgba(17,24,39,0.66)",
          "text-halo-color": basemapMode === "dark" ? "rgba(3,7,18,0.58)" : "rgba(255,255,255,0.7)",
          "text-halo-width": 1,
          "text-halo-blur": 0.7,
          "text-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.45, 5, 0.64, 8, 0.72],
        },
      },
      {
        id: "twf-labels",
        type: "raster",
        source: "twf-labels",
        paint: labelPaint,
      },
      ...buildVectorBufferLayers(),
    ],
  };
}

type MapCanvasProps = {
  selectionKey: string;
  selectionEpoch: number;
  gridManifest?: GridManifestResponse | null;
  compositeGridLayers?: Array<{
    id: string;
    manifest: GridManifestResponse | null;
    frameUrl: string | null;
    frameHour: number | null;
    legend: LegendPayload | null;
  }>;
  gridLodLevel?: number | null;
  gridFrameUrl?: string | null;
  gridFrameHour?: number | null;
  gridPrefetchPivotHour?: number | null;
  gridLegend?: LegendPayload | null;
  gridActive?: boolean;
  contourGeoJsonUrl?: string | null;
  contourPrefetchUrls?: string[];
  vectorGeoJsonUrl?: string | null;
  vectorPrefetchUrls?: string[];
  anchorGeoJson?: AnchorFeatureCollection | null;
  pointLabelsEnabled?: boolean;
  showZoomControls?: boolean;
  legendButtonVisible?: boolean;
  legendButtonActive?: boolean;
  onLegendButtonClick?: () => void;
  region: string;
  regionViews?: Record<string, RegionView>;
  opacity: number;
  mode: PlaybackMode;
  variable?: string;
  overlayFadeOutZoom?: { start: number; end: number } | null;
  basemapMode: BasemapMode;
  onZoomBucketChange?: (bucket: number) => void;
  onZoomRoutingSignal?: (payload: { zoom: number; gestureActive: boolean }) => void;
  onViewportChange?: (payload: { lat: number; lon: number; z: number }) => void;
  onGridFrameVisible?: (payload: GridFrameVisiblePayload) => void;
  onGridFrameReady?: (frameUrl: string) => void;
  onGridFrameEvicted?: (frameUrl: string) => void;
  isAnimating?: boolean;
  onMapReady?: (map: maplibregl.Map) => void;
  onMapHover?: (lat: number, lon: number, x: number, y: number, tooltip?: Exclude<SampleTooltipState, null>) => void;
  onMapHoverEnd?: () => void;
  onAnchorClick?: (anchor: { id: string; city: string; state: string; st: string }) => void;
};

export function MapCanvas({
  selectionKey,
  selectionEpoch,
  gridManifest = null,
  compositeGridLayers = [],
  gridLodLevel = null,
  gridFrameUrl = null,
  gridFrameHour = null,
  gridPrefetchPivotHour = null,
  gridLegend = null,
  gridActive = false,
  contourGeoJsonUrl,
  contourPrefetchUrls = [],
  vectorGeoJsonUrl,
  vectorPrefetchUrls = [],
  anchorGeoJson = null,
  pointLabelsEnabled = true,
  showZoomControls = false,
  legendButtonVisible = false,
  legendButtonActive = false,
  onLegendButtonClick,
  region,
  regionViews,
  opacity,
  mode,
  variable,
  overlayFadeOutZoom = null,
  basemapMode,
  onZoomBucketChange,
  onZoomRoutingSignal,
  onViewportChange,
  onGridFrameVisible,
  onGridFrameReady,
  onGridFrameEvicted,
  isAnimating = false,
  onMapReady,
  onMapHover,
  onMapHoverEnd,
  onAnchorClick,
}: MapCanvasProps) {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const gridWebglControllerRef = useRef<GridWebglLayerController | null>(null);
  if (!gridWebglControllerRef.current) {
    gridWebglControllerRef.current = new GridWebglLayerController();
  }
  const compositeGridControllersRef = useRef<Map<string, GridWebglLayerController>>(new Map());

  const [isLoaded, setIsLoaded] = useState(false);
  const [anchorTooltip, setAnchorTooltip] = useState<AnchorTooltipState | null>(null);

  const anchorMarkersRef = useRef<Map<string, AnchorMarkerRecord>>(new Map());
  const isHoveringAnchorRef = useRef(false);
  const anchorHoverLeaveTimeoutRef = useRef<number | null>(null);
  const prevGridFrameHourRef = useRef<number | null>(null);
  /** Detected scrub direction: 1 = forward, -1 = backward, 0 = unknown. */
  const scrubDirectionRef = useRef<1 | -1 | 0>(0);
  const onMapReadyRef = useRef(onMapReady);
  onMapReadyRef.current = onMapReady;
  const onViewportChangeRef = useRef(onViewportChange);
  onViewportChangeRef.current = onViewportChange;
  const contourRequestTokenRef = useRef(0);
  const contourAbortRef = useRef<AbortController | null>(null);
  const contourCacheRef = useRef<Map<string, GeoJSON.FeatureCollection>>(new Map());
  const vectorRequestTokenRef = useRef(0);
  const vectorAbortRef = useRef<AbortController | null>(null);
  const vectorCacheRef = useRef<Map<string, GeoJSON.FeatureCollection>>(new Map());
  const activeVectorBufferRef = useRef<0 | 1 | null>(null);
  const activeVectorUrlRef = useRef("");
  const vectorTransitionRafRef = useRef<number | null>(null);
  const lastAppliedBasemapModeRef = useRef<BasemapMode>(basemapMode);

  const view = useMemo(() => {
    return regionViews?.[region] ?? {
      center: [MAP_VIEW_DEFAULTS.center[1], MAP_VIEW_DEFAULTS.center[0]] as [number, number],
      zoom: MAP_VIEW_DEFAULTS.zoom,
    };
  }, [region, regionViews]);

  const apiRoot = useMemo(() => API_ORIGIN.replace(/\/$/, ""), []);
  const gridPrefetchUrls = useMemo(() => {
    if (!gridManifest?.lods?.length || !gridFrameUrl || !Number.isFinite(gridFrameHour)) {
      return [] as string[];
    }

    const isObservedGrid = String(gridManifest.model ?? "").trim().toLowerCase() === "mrms";
    const lod = gridManifest.lods.find((entry) => Number(entry?.level) === Number(gridLodLevel))
      ?? gridManifest.lods.find((entry) => Number(entry?.level) === 0)
      ?? gridManifest.lods[0]
      ?? null;
    const frames = Array.isArray(lod?.frames) ? lod.frames : [];
    const frameByHour = new Map<number, typeof frames[number]>();
    for (const frame of frames) {
      const hour = Number(frame?.fh);
      if (!Number.isFinite(hour)) {
        continue;
      }
      frameByHour.set(hour, frame);
    }
    const frameHours = Array.from(frameByHour.keys()).sort((a, b) => a - b);

    // Use the prefetch pivot hour (the requested/target hour) when available so
    // that jumping directly to a far forecast hour immediately prefetches around
    // the destination rather than the currently-displayed frame.  Falls back to
    // gridFrameHour (the presented/visible hour) when no explicit pivot is given.
    const effectivePivotHour = Number.isFinite(gridPrefetchPivotHour)
      ? Number(gridPrefetchPivotHour)
      : Number(gridFrameHour);
    const pivot = frameHours.indexOf(effectivePivotHour);
    if (pivot < 0) {
      return [] as string[];
    }

    // Track scrub direction from frame-to-frame movement using the effective
    // pivot so a direct jump (without scrubbing) still sets the direction.
    const prevHour = prevGridFrameHourRef.current;
    const currentHour = effectivePivotHour;
    if (prevHour !== null && Number.isFinite(prevHour) && prevHour !== currentHour) {
      scrubDirectionRef.current = currentHour > prevHour ? 1 : -1;
    }
    prevGridFrameHourRef.current = currentHour;

    const urls: string[] = [];
    const remainingAhead = Math.max(0, frameHours.length - 1 - pivot);
    const remainingBehind = Math.max(0, pivot);
    const direction = scrubDirectionRef.current;

    let aheadTarget: number;
    let behindTarget: number;

    if (isObservedGrid) {
      const mobileObserved = isMobileDevice();
      // Observed grids (MRMS): prefetch the *entire* timeline, ordered
      // outward from the current frame so the nearest neighbors arrive
      // first (progressive prefetch).  During autoplay bias forward.
      if (mode === "autoplay") {
        if (mobileObserved) {
          aheadTarget = Math.min(remainingAhead, OBSERVED_MOBILE_AUTOPLAY_PREFETCH_AHEAD);
          behindTarget = Math.min(remainingBehind, OBSERVED_MOBILE_AUTOPLAY_PREFETCH_BEHIND);
        } else {
          aheadTarget = remainingAhead;
          behindTarget = Math.min(remainingBehind, 2);
        }
      } else {
        // Scrub/idle: interleave ahead and behind from the pivot so
        // the nearest frames in *both* directions are always warm.
        // Direction bias puts the travel-direction frames at odd
        // positions (first) and the opposite direction at even
        // positions, but both are interleaved rather than sequential.
        if (mobileObserved) {
          const budget = OBSERVED_MOBILE_SCRUB_PREFETCH_BUDGET;
          if (direction > 0) {
            behindTarget = Math.min(remainingBehind, OBSERVED_MOBILE_SCRUB_MIN_BEHIND);
            aheadTarget = Math.min(remainingAhead, budget - behindTarget);
          } else if (direction < 0) {
            aheadTarget = Math.min(remainingAhead, OBSERVED_MOBILE_SCRUB_MIN_AHEAD);
            behindTarget = Math.min(remainingBehind, budget - aheadTarget);
          } else {
            const halfBudget = Math.floor(budget / 2);
            aheadTarget = Math.min(remainingAhead, halfBudget + 1);
            behindTarget = Math.min(remainingBehind, budget - aheadTarget);
          }
        } else {
          aheadTarget = remainingAhead;
          behindTarget = remainingBehind;
        }
      }
    } else if (mode === "autoplay") {
      aheadTarget = Math.min(remainingAhead, 8);
      behindTarget = Math.min(remainingBehind, 2);
    } else if (mode === "variable-switch") {
      aheadTarget = Math.min(remainingAhead, 6);
      behindTarget = Math.min(remainingBehind, 2);
    } else {
      // Adaptive forecast scrub prefetch: direction-aware window within a
      // fixed total budget.  When the user is scrubbing forward, bias ahead;
      // when scrubbing backward, bias behind; when idle/unknown, split evenly.
      const budget = FORECAST_SCRUB_PREFETCH_BUDGET;
      if (direction > 0) {
        // Forward: most of the budget goes ahead.
        behindTarget = Math.min(remainingBehind, FORECAST_SCRUB_MIN_BEHIND);
        aheadTarget = Math.min(remainingAhead, budget - behindTarget);
      } else if (direction < 0) {
        // Backward: most of the budget goes behind.
        aheadTarget = Math.min(remainingAhead, FORECAST_SCRUB_MIN_AHEAD);
        behindTarget = Math.min(remainingBehind, budget - aheadTarget);
      } else {
        // Unknown: split the budget evenly, slight forward bias.
        const halfBudget = Math.floor(budget / 2);
        aheadTarget = Math.min(remainingAhead, halfBudget + 1);
        behindTarget = Math.min(remainingBehind, budget - aheadTarget);
      }
    }

    const normalizeGridUrl = (rawUrl: string): string => {
      if (!rawUrl) {
        return "";
      }
      if (/^https?:\/\//i.test(rawUrl)) {
        return rawUrl;
      }
      return `${apiRoot}${rawUrl.startsWith("/") ? "" : "/"}${rawUrl}`;
    };

    const pushFrameUrl = (hour: number) => {
      const frame = frameByHour.get(hour);
      const url = normalizeGridUrl(String(frame?.url ?? "").trim());
      if (url && url !== gridFrameUrl && !urls.includes(url)) {
        urls.push(url);
      }
    };

    // Push direction-of-travel frames first so they receive higher priority
    // in the downstream texture warm queue.  During backward scrub the
    // behind-frames are the ones the user will need next.
    if (isObservedGrid && mode !== "autoplay") {
      // MRMS scrub/idle: interleave ahead and behind, nearest-first, so both
      // directions stay warm in the limited texture queue.  Direction bias
      // determines which side goes first at each interleave step.
      const maxStep = Math.max(aheadTarget, behindTarget);
      for (let step = 1; step <= maxStep; step += 1) {
        if (direction < 0) {
          // Backward: behind frame first at this distance, then ahead.
          if (step <= behindTarget && pivot - step >= 0) {
            pushFrameUrl(frameHours[pivot - step]);
          }
          if (step <= aheadTarget && pivot + step < frameHours.length) {
            pushFrameUrl(frameHours[pivot + step]);
          }
        } else {
          // Forward or neutral: ahead first at this distance, then behind.
          if (step <= aheadTarget && pivot + step < frameHours.length) {
            pushFrameUrl(frameHours[pivot + step]);
          }
          if (step <= behindTarget && pivot - step >= 0) {
            pushFrameUrl(frameHours[pivot - step]);
          }
        }
      }
    } else if (direction < 0) {
      // Backward: behind first, then ahead.
      for (let step = 1; step <= behindTarget; step += 1) {
        if (pivot - step >= 0) {
          pushFrameUrl(frameHours[pivot - step]);
        }
      }
      for (let step = 1; step <= aheadTarget; step += 1) {
        if (pivot + step < frameHours.length) {
          pushFrameUrl(frameHours[pivot + step]);
        }
      }
    } else {
      // Forward or neutral: ahead first, then behind.
      for (let step = 1; step <= aheadTarget; step += 1) {
        if (pivot + step < frameHours.length) {
          pushFrameUrl(frameHours[pivot + step]);
        }
      }
      for (let step = 1; step <= behindTarget; step += 1) {
        if (pivot - step >= 0) {
          pushFrameUrl(frameHours[pivot - step]);
        }
      }
    }
    return urls;
  }, [apiRoot, gridFrameHour, gridFrameUrl, gridLodLevel, gridManifest, gridPrefetchPivotHour, mode]);
  const shouldUseGridController = Boolean(
    gridActive || gridManifest || gridFrameUrl || gridPrefetchUrls.length > 0 || compositeGridLayers.length > 0
  );

  const clearAnchorMarkers = useCallback(() => {
    if (anchorHoverLeaveTimeoutRef.current !== null) {
      window.clearTimeout(anchorHoverLeaveTimeoutRef.current);
      anchorHoverLeaveTimeoutRef.current = null;
    }
    isHoveringAnchorRef.current = false;
    setAnchorTooltip(null);
    for (const record of anchorMarkersRef.current.values()) {
      record.marker.remove();
    }
    anchorMarkersRef.current.clear();
  }, []);

  const showAnchorTooltip = useCallback((map: maplibregl.Map, cityName: string, lngLat: [number, number]) => {
    const projected = map.project(lngLat);
    setAnchorTooltip({
      cityName,
      x: projected.x,
      y: projected.y,
    });
  }, []);

  const hideAnchorTooltip = useCallback(() => {
    setAnchorTooltip(null);
  }, []);

  const syncAnchorMarkers = useCallback(
    (map: maplibregl.Map, data: AnchorFeatureCollection | null, visible: boolean) => {
      if (!visible) {
        clearAnchorMarkers();
        return;
      }

      const activeMarkers = getActiveAnchorMarkers(data, map.getZoom());
      const nextIds = new Set(activeMarkers.map((item) => item.id));

      for (const [id, record] of anchorMarkersRef.current) {
        if (nextIds.has(id)) {
          continue;
        }
        record.marker.remove();
        anchorMarkersRef.current.delete(id);
      }

      for (const activeMarker of activeMarkers) {
        const existing = anchorMarkersRef.current.get(activeMarker.id);
        if (existing) {
          if (existing.chip.textContent !== activeMarker.label) {
            existing.chip.textContent = activeMarker.label;
          }
          if (existing.chip.getAttribute("aria-label") !== activeMarker.cityName) {
            existing.chip.setAttribute("aria-label", activeMarker.cityName);
          }
          existing.marker.setLngLat(activeMarker.lngLat);
          snapAnchorMarkerToPixels(map, existing);
          continue;
        }

        const element = document.createElement("div");
        element.className = "map-anchor-marker";
        element.setAttribute("aria-hidden", "true");

        const chip = document.createElement("div");
        chip.className = "map-anchor-marker__chip";
        chip.textContent = activeMarker.label;
        chip.setAttribute("aria-label", activeMarker.cityName);
        chip.addEventListener("mouseenter", () => {
          if (anchorHoverLeaveTimeoutRef.current !== null) {
            window.clearTimeout(anchorHoverLeaveTimeoutRef.current);
            anchorHoverLeaveTimeoutRef.current = null;
          }
          isHoveringAnchorRef.current = true;
          onMapHoverEnd?.();
          showAnchorTooltip(map, activeMarker.cityName, activeMarker.lngLat);
        });
        chip.addEventListener("mouseleave", () => {
          hideAnchorTooltip();
          if (anchorHoverLeaveTimeoutRef.current !== null) {
            window.clearTimeout(anchorHoverLeaveTimeoutRef.current);
          }
          anchorHoverLeaveTimeoutRef.current = window.setTimeout(() => {
            isHoveringAnchorRef.current = false;
            anchorHoverLeaveTimeoutRef.current = null;
          }, ANCHOR_HOVER_RESUME_DELAY_MS);
        });
        chip.addEventListener("focus", () => {
          if (anchorHoverLeaveTimeoutRef.current !== null) {
            window.clearTimeout(anchorHoverLeaveTimeoutRef.current);
            anchorHoverLeaveTimeoutRef.current = null;
          }
          isHoveringAnchorRef.current = true;
          onMapHoverEnd?.();
          showAnchorTooltip(map, activeMarker.cityName, activeMarker.lngLat);
        });
        chip.addEventListener("blur", () => {
          hideAnchorTooltip();
          if (anchorHoverLeaveTimeoutRef.current !== null) {
            window.clearTimeout(anchorHoverLeaveTimeoutRef.current);
          }
          anchorHoverLeaveTimeoutRef.current = window.setTimeout(() => {
            isHoveringAnchorRef.current = false;
            anchorHoverLeaveTimeoutRef.current = null;
          }, ANCHOR_HOVER_RESUME_DELAY_MS);
        });
        chip.addEventListener("click", (e) => {
          e.stopPropagation();
          onAnchorClick?.({
            id: activeMarker.id,
            city: activeMarker.cityName,
            state: activeMarker.state,
            st: activeMarker.st,
          });
        });

        element.appendChild(chip);

        const marker = new maplibregl.Marker({
          element,
          anchor: "center",
          offset: [0, 0],
        })
          .setLngLat(activeMarker.lngLat)
          .addTo(map);

        const record = { marker, element, chip };
        anchorMarkersRef.current.set(activeMarker.id, record);
        snapAnchorMarkerToPixels(map, record);
      }
    },
    [clearAnchorMarkers, hideAnchorTooltip, onAnchorClick, onMapHoverEnd, showAnchorTooltip]
  );

  const enforceLayerOrder = useCallback((map: maplibregl.Map) => {
    if (!map.getLayer("twf-labels")) {
      return;
    }

    const firstVectorFillLayerId = VECTOR_FILL_LAYER_IDS.find((layerId) => map.getLayer(layerId));
    if (map.getLayer(LAKE_MASK_LAYER_ID) && firstVectorFillLayerId) {
      map.moveLayer(LAKE_MASK_LAYER_ID, firstVectorFillLayerId);
    }
    for (const layerId of VECTOR_FILL_LAYER_IDS) {
      if (map.getLayer(layerId) && map.getLayer(COASTLINE_LAYER_ID)) {
        map.moveLayer(layerId, COASTLINE_LAYER_ID);
      }
    }
    for (const layerId of VECTOR_LINE_LAYER_IDS) {
      if (map.getLayer(layerId) && map.getLayer(COASTLINE_LAYER_ID)) {
        map.moveLayer(layerId, COASTLINE_LAYER_ID);
      }
    }
    if (map.getLayer(GRID_WEBGL_LAYER_ID) && map.getLayer(COASTLINE_LAYER_ID)) {
      map.moveLayer(GRID_WEBGL_LAYER_ID, COASTLINE_LAYER_ID);
    }
    for (const layerId of compositeGridControllersRef.current.keys()) {
      if (map.getLayer(layerId) && map.getLayer(COASTLINE_LAYER_ID)) {
        map.moveLayer(layerId, COASTLINE_LAYER_ID);
      }
    }
    if (map.getLayer(CONTOUR_LAYER_ID)) {
      map.moveLayer(CONTOUR_LAYER_ID, map.getLayer(COASTLINE_LAYER_ID) ? COASTLINE_LAYER_ID : "twf-labels");
    }
    if (map.getLayer(COASTLINE_LAYER_ID)) {
      map.moveLayer(COASTLINE_LAYER_ID, "twf-labels");
    }
    if (map.getLayer(COUNTRY_BOUNDARY_LAYER_ID)) {
      map.moveLayer(COUNTRY_BOUNDARY_LAYER_ID, "twf-labels");
    }
    if (map.getLayer(STATE_BOUNDARY_LAYER_ID)) {
      map.moveLayer(STATE_BOUNDARY_LAYER_ID, "twf-labels");
    }
    if (map.getLayer(COUNTY_BOUNDARY_LAYER_ID)) {
      map.moveLayer(COUNTY_BOUNDARY_LAYER_ID, "twf-labels");
    }
    if (map.getLayer(LAKE_SHORELINE_LAYER_ID)) {
      map.moveLayer(LAKE_SHORELINE_LAYER_ID, "twf-labels");
    }
    if (map.getLayer(CONTOUR_LABEL_LAYER_ID)) {
      map.moveLayer(CONTOUR_LABEL_LAYER_ID, "twf-labels");
    }
    map.moveLayer("twf-labels");
  }, []);

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) {
      return;
    }

    let resizeRafId: number | null = null;
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: buildMapStyle(null, null, basemapMode),
      center: view.center,
      zoom: view.zoom,
      minZoom: view.minZoom ?? 3,
      maxZoom: view.maxZoom ?? 11,
      attributionControl: false,
      preserveDrawingBuffer: true,
    });

    const handleMapError = (event: { error?: unknown }) => {
      const err = event?.error;
      const errName =
        typeof err === "object" && err !== null && "name" in err
          ? String((err as { name?: unknown }).name ?? "")
          : "";
      const errMessage =
        typeof err === "object" && err !== null && "message" in err
          ? String((err as { message?: unknown }).message ?? "")
          : "";
      if (errName === "AbortError" || errMessage === "AbortError") {
        return;
      }
      if (err) {
        console.warn("[map] MapLibre error", err);
      }
    };

    map.on("error", handleMapError as any);
    map.on("load", () => {
      setIsLoaded(true);
      lastAppliedBasemapModeRef.current = basemapMode;
      enforceLayerOrder(map);
      onMapReadyRef.current?.(map);
    });

    mapRef.current = map;
    resizeRafId = window.requestAnimationFrame(() => {
      map.resize();
    });

    return () => {
      if (resizeRafId !== null) {
        window.cancelAnimationFrame(resizeRafId);
      }
      if (vectorTransitionRafRef.current !== null) {
        window.cancelAnimationFrame(vectorTransitionRafRef.current);
        vectorTransitionRafRef.current = null;
      }
      contourAbortRef.current?.abort();
      contourAbortRef.current = null;
      vectorAbortRef.current?.abort();
      vectorAbortRef.current = null;
      map.off("error", handleMapError as any);
      clearAnchorMarkers();
      gridWebglControllerRef.current?.remove(map);
      for (const controller of compositeGridControllersRef.current.values()) {
        controller.remove(map);
      }
      compositeGridControllersRef.current.clear();
      map.remove();
      mapRef.current = null;
      setIsLoaded(false);
    };
  }, [clearAnchorMarkers, enforceLayerOrder]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    if (lastAppliedBasemapModeRef.current === basemapMode) {
      return;
    }

    lastAppliedBasemapModeRef.current = basemapMode;
    const controller = gridWebglControllerRef.current;
    const onStyleData = () => {
      if (shouldUseGridController) {
        controller?.ensureAttached(map, gridOverlayBeforeLayerId(map));
        for (const compositeController of compositeGridControllersRef.current.values()) {
          compositeController.ensureAttached(map, gridOverlayBeforeLayerId(map));
        }
      }
      setLayerVisibility(map, CONTOUR_LAYER_ID, Boolean(contourGeoJsonUrl));
      setLayerVisibility(map, CONTOUR_LABEL_LAYER_ID, Boolean(contourGeoJsonUrl));
      enforceLayerOrder(map);
    };

    map.once("styledata", onStyleData);
    map.setStyle(buildMapStyle(contourGeoJsonUrl, vectorGeoJsonUrl, basemapMode));

    return () => {
      map.off("styledata", onStyleData);
    };
  }, [basemapMode, contourGeoJsonUrl, enforceLayerOrder, isLoaded, shouldUseGridController, vectorGeoJsonUrl]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    const source = map.getSource(CONTOUR_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
    if (!source || typeof source.setData !== "function") {
      return;
    }

    const normalizedUrl = String(contourGeoJsonUrl ?? "").trim();
    const requestToken = ++contourRequestTokenRef.current;
    contourAbortRef.current?.abort();
    contourAbortRef.current = null;
    setLayerVisibility(map, CONTOUR_LAYER_ID, Boolean(normalizedUrl));
    setLayerVisibility(map, CONTOUR_LABEL_LAYER_ID, Boolean(normalizedUrl));

    if (!normalizedUrl) {
      source.setData(EMPTY_FEATURE_COLLECTION as any);
      return;
    }

    const cached = contourCacheRef.current.get(normalizedUrl);
    if (cached) {
      source.setData(cached as any);
      return;
    }

    const controller = new AbortController();
    contourAbortRef.current = controller;
    const startedAtMs = startNetworkTimer();

    void fetch(normalizedUrl, {
      credentials: "omit",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Contour request failed: ${response.status}`);
        }
        const payload = (await response.json()) as GeoJSON.FeatureCollection;
        trackNetworkFetchDuration({
          metric_name: "contour_fetch_duration",
          started_at_ms: startedAtMs,
          response,
          meta: {
            contour_url_path: normalizedUrl,
          },
        });
        return withContourLabels(payload);
      })
      .then((payload) => {
        if (controller.signal.aborted || contourRequestTokenRef.current !== requestToken) {
          return;
        }
        contourCacheRef.current.set(normalizedUrl, payload);
        while (contourCacheRef.current.size > 16) {
          const oldestKey = contourCacheRef.current.keys().next().value;
          if (!oldestKey) {
            break;
          }
          contourCacheRef.current.delete(oldestKey);
        }
        source.setData(payload as any);
      })
      .catch((error) => {
        if (controller.signal.aborted) {
          return;
        }
        console.warn("[map] contour fetch failed", { contourGeoJsonUrl: normalizedUrl, error });
      })
      .finally(() => {
        if (contourAbortRef.current === controller) {
          contourAbortRef.current = null;
        }
      });

    return () => {
      controller.abort();
      if (contourAbortRef.current === controller) {
        contourAbortRef.current = null;
      }
    };
  }, [basemapMode, contourGeoJsonUrl, isLoaded]);

  useEffect(() => {
    if (!isLoaded || contourPrefetchUrls.length === 0) {
      return;
    }

    const normalizedActiveUrl = String(contourGeoJsonUrl ?? "").trim();
    if (normalizedActiveUrl && !contourCacheRef.current.has(normalizedActiveUrl)) {
      return;
    }

    const controller = new AbortController();
    const startPrefetchTimer = window.setTimeout(() => {
      for (const rawUrl of contourPrefetchUrls) {
        const normalizedUrl = String(rawUrl ?? "").trim();
        if (!normalizedUrl || contourCacheRef.current.has(normalizedUrl)) {
          continue;
        }

        void fetch(normalizedUrl, {
          credentials: "omit",
          signal: controller.signal,
        })
          .then(async (response) => {
            if (!response.ok) {
              throw new Error(`Contour prefetch failed: ${response.status}`);
            }
            return withContourLabels((await response.json()) as GeoJSON.FeatureCollection);
          })
          .then((payload) => {
            if (controller.signal.aborted) {
              return;
            }
            contourCacheRef.current.set(normalizedUrl, payload);
            while (contourCacheRef.current.size > 24) {
              const oldestKey = contourCacheRef.current.keys().next().value;
              if (!oldestKey) {
                break;
              }
              contourCacheRef.current.delete(oldestKey);
            }
          })
          .catch((error) => {
            if (controller.signal.aborted) {
              return;
            }
            console.warn("[map] contour prefetch failed", { contourGeoJsonUrl: normalizedUrl, error });
          });
      }
    }, 120);

    return () => {
      window.clearTimeout(startPrefetchTimer);
      controller.abort();
    };
  }, [contourGeoJsonUrl, contourPrefetchUrls, isLoaded]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    const resolveVectorSource = (bufferIndex: 0 | 1) => {
      const source = map.getSource(VECTOR_SOURCE_IDS[bufferIndex]) as maplibregl.GeoJSONSource | undefined;
      return source && typeof source.setData === "function" ? source : null;
    };
    const applyVectorData = (bufferIndex: 0 | 1, payload: GeoJSON.FeatureCollection) => {
      const source = resolveVectorSource(bufferIndex);
      if (!source) {
        return false;
      }
      source.setData(payload as any);
      return true;
    };
    const hideVectorBuffer = (bufferIndex: 0 | 1) => {
      setLayerVisibility(map, VECTOR_FILL_LAYER_IDS[bufferIndex], false);
      setLayerVisibility(map, VECTOR_LINE_LAYER_IDS[bufferIndex], false);
      setVectorLayerFade(map, bufferIndex, 0);
    };
    const showVectorBuffer = (bufferIndex: 0 | 1, fade: number) => {
      setLayerVisibility(map, VECTOR_FILL_LAYER_IDS[bufferIndex], true);
      setLayerVisibility(map, VECTOR_LINE_LAYER_IDS[bufferIndex], true);
      setVectorLayerFade(map, bufferIndex, fade);
    };
    const finishOnBuffer = (bufferIndex: 0 | 1, payload: GeoJSON.FeatureCollection, url: string) => {
      if (!applyVectorData(bufferIndex, payload)) {
        return;
      }
      showVectorBuffer(bufferIndex, 1);
      hideVectorBuffer((bufferIndex === 0 ? 1 : 0));
      activeVectorBufferRef.current = bufferIndex;
      activeVectorUrlRef.current = url;
    };
    const startCrossfade = (fromBuffer: 0 | 1, toBuffer: 0 | 1, payload: GeoJSON.FeatureCollection, url: string) => {
      if (!applyVectorData(toBuffer, payload)) {
        return;
      }
      if (vectorTransitionRafRef.current !== null) {
        window.cancelAnimationFrame(vectorTransitionRafRef.current);
        vectorTransitionRafRef.current = null;
      }
      showVectorBuffer(toBuffer, 0);
      showVectorBuffer(fromBuffer, 1);
      const startedAt = performance.now();
      const tick = (now: number) => {
        const progress = Math.min(1, (now - startedAt) / VECTOR_TRANSITION_MS);
        setVectorLayerFade(map, fromBuffer, 1 - progress);
        setVectorLayerFade(map, toBuffer, progress);
        if (progress >= 1) {
          vectorTransitionRafRef.current = null;
          hideVectorBuffer(fromBuffer);
          activeVectorBufferRef.current = toBuffer;
          activeVectorUrlRef.current = url;
          return;
        }
        vectorTransitionRafRef.current = window.requestAnimationFrame(tick);
      };
      vectorTransitionRafRef.current = window.requestAnimationFrame(tick);
    };

    if (!resolveVectorSource(0) || !resolveVectorSource(1)) {
      return;
    }

    const normalizedUrl = String(vectorGeoJsonUrl ?? "").trim();
    const requestToken = ++vectorRequestTokenRef.current;
    vectorAbortRef.current?.abort();
    vectorAbortRef.current = null;

    if (!normalizedUrl) {
      applyVectorData(0, EMPTY_FEATURE_COLLECTION);
      applyVectorData(1, EMPTY_FEATURE_COLLECTION);
      hideVectorBuffer(0);
      hideVectorBuffer(1);
      activeVectorBufferRef.current = null;
      activeVectorUrlRef.current = "";
      return;
    }

    if (activeVectorUrlRef.current === normalizedUrl && activeVectorBufferRef.current !== null) {
      const activeBuffer = activeVectorBufferRef.current;
      showVectorBuffer(activeBuffer, 1);
      hideVectorBuffer(activeBuffer === 0 ? 1 : 0);
      return;
    }

    const cached = vectorCacheRef.current.get(normalizedUrl);
    if (cached) {
      const activeBuffer = activeVectorBufferRef.current;
      if (activeBuffer === null) {
        finishOnBuffer(0, cached, normalizedUrl);
      } else {
        startCrossfade(activeBuffer, activeBuffer === 0 ? 1 : 0, cached, normalizedUrl);
      }
      return;
    }

    const controller = new AbortController();
    vectorAbortRef.current = controller;
    const startedAtMs = startNetworkTimer();

    void fetch(normalizedUrl, {
      credentials: "omit",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Vector request failed: ${response.status}`);
        }
        const payload = (await response.json()) as GeoJSON.FeatureCollection;
        trackNetworkFetchDuration({
          metric_name: "vector_fetch_duration",
          started_at_ms: startedAtMs,
          response,
          meta: {
            vector_url_path: normalizedUrl,
          },
        });
        return payload;
      })
      .then((payload) => {
        if (controller.signal.aborted || vectorRequestTokenRef.current !== requestToken) {
          return;
        }
        vectorCacheRef.current.set(normalizedUrl, payload);
        while (vectorCacheRef.current.size > 16) {
          const oldestKey = vectorCacheRef.current.keys().next().value;
          if (!oldestKey) {
            break;
          }
          vectorCacheRef.current.delete(oldestKey);
        }
        const activeBuffer = activeVectorBufferRef.current;
        if (activeBuffer === null) {
          finishOnBuffer(0, payload, normalizedUrl);
          return;
        }
        startCrossfade(activeBuffer, activeBuffer === 0 ? 1 : 0, payload, normalizedUrl);
      })
      .catch((error) => {
        if (controller.signal.aborted) {
          return;
        }
        console.warn("[map] vector fetch failed", { vectorGeoJsonUrl: normalizedUrl, error });
      })
      .finally(() => {
        if (vectorAbortRef.current === controller) {
          vectorAbortRef.current = null;
        }
      });

    return () => {
      controller.abort();
      if (vectorAbortRef.current === controller) {
        vectorAbortRef.current = null;
      }
    };
  }, [basemapMode, isLoaded, vectorGeoJsonUrl]);

  useEffect(() => {
    if (!isLoaded || vectorPrefetchUrls.length === 0) {
      return;
    }

    const normalizedActiveUrl = String(vectorGeoJsonUrl ?? "").trim();
    if (
      normalizedActiveUrl
      && activeVectorUrlRef.current !== normalizedActiveUrl
      && !vectorCacheRef.current.has(normalizedActiveUrl)
    ) {
      return;
    }

    const controller = new AbortController();
    const startPrefetchTimer = window.setTimeout(() => {
      for (const rawUrl of vectorPrefetchUrls) {
        const normalizedUrl = String(rawUrl ?? "").trim();
        if (!normalizedUrl || vectorCacheRef.current.has(normalizedUrl)) {
          continue;
        }

        void fetch(normalizedUrl, {
          credentials: "omit",
          signal: controller.signal,
        })
          .then(async (response) => {
            if (!response.ok) {
              throw new Error(`Vector prefetch failed: ${response.status}`);
            }
            return (await response.json()) as GeoJSON.FeatureCollection;
          })
          .then((payload) => {
            if (controller.signal.aborted) {
              return;
            }
            vectorCacheRef.current.set(normalizedUrl, payload);
            while (vectorCacheRef.current.size > 24) {
              const oldestKey = vectorCacheRef.current.keys().next().value;
              if (!oldestKey) {
                break;
              }
              vectorCacheRef.current.delete(oldestKey);
            }
          })
          .catch((error) => {
            if (controller.signal.aborted) {
              return;
            }
            console.warn("[map] vector prefetch failed", { vectorGeoJsonUrl: normalizedUrl, error });
          });
      }
    }, 350);

    return () => {
      window.clearTimeout(startPrefetchTimer);
      controller.abort();
    };
  }, [isLoaded, vectorGeoJsonUrl, vectorPrefetchUrls]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }

    let rafId: number | null = null;
    const scheduleSync = () => {
      if (rafId !== null) {
        return;
      }
      rafId = window.requestAnimationFrame(() => {
        rafId = null;
        syncAnchorMarkers(map, anchorGeoJson, pointLabelsEnabled);
      });
    };

    scheduleSync();
    map.on("move", scheduleSync);
    map.on("moveend", scheduleSync);
    map.on("resize", scheduleSync);

    return () => {
      map.off("move", scheduleSync);
      map.off("moveend", scheduleSync);
      map.off("resize", scheduleSync);
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId);
      }
    };
  }, [anchorGeoJson, isLoaded, pointLabelsEnabled, syncAnchorMarkers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }

    const hideTooltipOnMove = () => {
      setAnchorTooltip(null);
    };

    map.on("movestart", hideTooltipOnMove);
    map.on("zoomstart", hideTooltipOnMove);

    return () => {
      map.off("movestart", hideTooltipOnMove);
      map.off("zoomstart", hideTooltipOnMove);
    };
  }, [isLoaded]);

  // --- Grid controller update (runs on every frame / config change) ---
  useEffect(() => {
    const map = mapRef.current;
    const controller = gridWebglControllerRef.current;
    if (!map || !isLoaded || !controller) {
      return;
    }

    if (!shouldUseGridController) {
      controller.remove(map);
      for (const compositeController of compositeGridControllersRef.current.values()) {
        compositeController.remove(map);
      }
      compositeGridControllersRef.current.clear();
      return;
    }

    controller.ensureAttached(map, gridOverlayBeforeLayerId(map));
    controller.update({
      active: Boolean(gridActive && gridManifest && gridFrameUrl),
      manifest: gridManifest,
      lodLevel: gridLodLevel,
      frameUrl: gridFrameUrl,
      frameHour: gridFrameHour,
      legend: gridLegend,
      opacity,
      overlayFadeOutZoom,
      selectionEpoch,
      selectionKey,
      prefetchUrls: gridPrefetchUrls,
      rasterPaint: getGridPaintSettings(variable, basemapMode),
      onFrameVisible: onGridFrameVisible,
      onFrameReady: onGridFrameReady,
      onFrameEvicted: onGridFrameEvicted,
      isAnimating,
    });

    const activeCompositeLayerIds = new Set<string>();
    for (const layer of compositeGridLayers) {
      const layerId = `${GRID_WEBGL_LAYER_ID}-${layer.id}`;
      activeCompositeLayerIds.add(layerId);
      let compositeController = compositeGridControllersRef.current.get(layerId);
      if (!compositeController) {
        compositeController = new GridWebglLayerController(layerId);
        compositeGridControllersRef.current.set(layerId, compositeController);
      }
      compositeController.ensureAttached(map, gridOverlayBeforeLayerId(map));
      compositeController.update({
        active: Boolean(gridActive && layer.manifest && layer.frameUrl),
        manifest: layer.manifest,
        lodLevel: gridLodLevel,
        frameUrl: layer.frameUrl,
        frameHour: layer.frameHour,
        legend: layer.legend,
        opacity,
        overlayFadeOutZoom,
        selectionEpoch,
        selectionKey: `${selectionKey}:${layer.id}`,
        prefetchUrls: [],
        rasterPaint: getGridPaintSettings(variable, basemapMode),
        onFrameVisible: onGridFrameVisible,
        onFrameReady: onGridFrameReady,
        onFrameEvicted: onGridFrameEvicted,
        isAnimating,
      });
    }

    for (const [layerId, compositeController] of compositeGridControllersRef.current.entries()) {
      if (activeCompositeLayerIds.has(layerId)) {
        continue;
      }
      compositeController.remove(map);
      compositeGridControllersRef.current.delete(layerId);
    }
  }, [
    basemapMode,
    compositeGridLayers,
    gridActive,
    gridFrameHour,
    gridFrameUrl,
    gridLegend,
    gridLodLevel,
    gridManifest,
    gridPrefetchUrls,
    isAnimating,
    isLoaded,
    onGridFrameEvicted,
    onGridFrameReady,
    onGridFrameVisible,
    opacity,
    overlayFadeOutZoom,
    selectionEpoch,
    selectionKey,
    shouldUseGridController,
    variable,
  ]);

  // --- Enforce layer order only on structural changes (not every frame) ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    enforceLayerOrder(map);
  }, [enforceLayerOrder, gridActive, isLoaded, selectionKey]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }

    const lastZoomBucketRef = { current: Number.NaN };
    const gestureActiveRef = { current: false };
    let rafId: number | null = null;

    const emitRoutingSignal = () => {
      if (!onZoomRoutingSignal) {
        return;
      }
      onZoomRoutingSignal({ zoom: map.getZoom(), gestureActive: gestureActiveRef.current });
    };

    const scheduleRoutingSignal = () => {
      if (!onZoomRoutingSignal || rafId !== null) {
        return;
      }
      rafId = window.requestAnimationFrame(() => {
        rafId = null;
        emitRoutingSignal();
      });
    };

    const emitViewportChange = () => {
      if (!onViewportChangeRef.current) {
        return;
      }
      const center = map.getCenter();
      onViewportChangeRef.current({
        lat: center.lat,
        lon: center.lng,
        z: map.getZoom(),
      });
    };

    const checkZoom = () => {
      const zoom = map.getZoom();
      const bucket = Math.max(0, Math.floor(zoom));
      if (bucket !== lastZoomBucketRef.current) {
        lastZoomBucketRef.current = bucket;
        onZoomBucketChange?.(bucket);
      }
      scheduleRoutingSignal();
    };

    const handleZoomStart = () => {
      gestureActiveRef.current = true;
      emitRoutingSignal();
    };

    const handleZoomEnd = () => {
      gestureActiveRef.current = false;
      emitRoutingSignal();
      emitViewportChange();
    };

    const handleMoveEnd = () => {
      checkZoom();
      emitViewportChange();
    };

    map.on("zoomstart", handleZoomStart);
    map.on("zoomend", handleZoomEnd);
    map.on("moveend", handleMoveEnd);
    map.on("zoom", checkZoom);
    checkZoom();
    emitRoutingSignal();
    emitViewportChange();

    return () => {
      map.off("zoomstart", handleZoomStart);
      map.off("zoomend", handleZoomEnd);
      map.off("moveend", handleMoveEnd);
      map.off("zoom", checkZoom);
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId);
      }
    };
  }, [isLoaded, onZoomBucketChange, onZoomRoutingSignal]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    map.setMinZoom(view.minZoom ?? 3);
    map.setMaxZoom(view.maxZoom ?? 11);
  }, [isLoaded, view.maxZoom, view.minZoom]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    if (view.bbox) {
      const [west, south, east, north] = view.bbox;
      map.fitBounds([[west, south], [east, north]], {
        duration: 600,
        padding: 24,
        ...(Number.isFinite(view.zoom) ? { maxZoom: view.zoom } : {}),
      });
    } else {
      map.easeTo({ center: view.center, zoom: view.zoom, duration: 600 });
    }
  }, [isLoaded, view]);

  const onMapHoverRef = useRef(onMapHover);
  onMapHoverRef.current = onMapHover;
  const onMapHoverEndRef = useRef(onMapHoverEnd);
  onMapHoverEndRef.current = onMapHoverEnd;

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    const canvas = map.getCanvas();
    canvas.style.cursor = "";

    const handleMove = (e: maplibregl.MapMouseEvent) => {
      if (isHoveringAnchorRef.current) {
        return;
      }
      const { lng, lat } = e.lngLat;
      const { x, y } = e.point;
      const vectorFeatures = map.queryRenderedFeatures(e.point, {
        layers: [...VECTOR_FILL_LAYER_IDS],
      }) as Array<{ properties?: Record<string, unknown> }>;
      const vectorFeature = vectorFeatures.find((feature) => {
        const hover = typeof feature?.properties?.hover_label === "string"
          ? feature.properties.hover_label.trim()
          : "";
        const risk = typeof feature?.properties?.risk_label === "string"
          ? feature.properties.risk_label.trim()
          : "";
        return /\d+%/.test(hover) || /\d+%/.test(risk);
      }) ?? vectorFeatures[0];
      const hoverLabel = typeof vectorFeature?.properties?.hover_label === "string"
        ? vectorFeature.properties.hover_label.trim()
        : "";
      const riskLabel = typeof vectorFeature?.properties?.risk_label === "string"
        ? vectorFeature.properties.risk_label.trim()
        : "";
      const fillColor = typeof vectorFeature?.properties?.fill === "string"
        ? vectorFeature.properties.fill.trim()
        : null;
      canvas.style.cursor = onMapHoverRef.current ? "crosshair" : "";
      onMapHoverRef.current?.(
        lat,
        lng,
        x,
        y,
        (hoverLabel || riskLabel)
          ? {
              kind: "label",
              label: hoverLabel || riskLabel,
              color: fillColor,
              x,
              y,
            }
          : undefined,
      );
    };

    const handleLeave = () => {
      canvas.style.cursor = "";
      onMapHoverEndRef.current?.();
    };

    map.on("mousemove", handleMove);
    canvas.addEventListener("mouseleave", handleLeave);

    return () => {
      map.off("mousemove", handleMove);
      canvas.removeEventListener("mouseleave", handleLeave);
      canvas.style.cursor = "";
      if (anchorHoverLeaveTimeoutRef.current !== null) {
        window.clearTimeout(anchorHoverLeaveTimeoutRef.current);
        anchorHoverLeaveTimeoutRef.current = null;
      }
      isHoveringAnchorRef.current = false;
    };
  }, [isLoaded]);

  const handleZoomIn = useCallback(() => {
    mapRef.current?.zoomIn({ duration: 180 });
  }, []);

  const handleZoomOut = useCallback(() => {
    mapRef.current?.zoomOut({ duration: 180 });
  }, []);

  return (
    <>
      <div
        ref={mapContainerRef}
        className="absolute inset-0"
        style={{ backgroundColor: getMapBackgroundColor(basemapMode) }}
        role="img"
        aria-label="Weather map"
      />

      {anchorTooltip && (
        <div
          className="pointer-events-none absolute z-[60] rounded-xl glass px-2.5 py-1.5 text-[11px] font-medium text-white/95 shadow-xl"
          style={{
            left: anchorTooltip.x,
            top: anchorTooltip.y,
            transform: "translate(-50%, calc(-100% - 10px))",
          }}
        >
          {anchorTooltip.cityName}
        </div>
      )}

      {(showZoomControls || legendButtonVisible) && (
        <div className="pointer-events-none fixed left-4 top-[calc(3.5rem+1rem)] z-50">
          <div className="glass pointer-events-auto overflow-hidden rounded-xl">
            {showZoomControls && (
              <>
                <button
                  type="button"
                  className="flex h-[34px] w-[34px] items-center justify-center text-lg font-semibold text-white/90 transition-colors hover:bg-white/[0.07] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  onClick={handleZoomIn}
                  aria-label="Zoom in"
                  title="Zoom in"
                >
                  +
                </button>
                <button
                  type="button"
                  className="flex h-[34px] w-[34px] items-center justify-center border-t border-[#1a3a5c]/60 text-xl font-semibold text-white/90 transition-colors hover:bg-white/[0.07] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  onClick={handleZoomOut}
                  aria-label="Zoom out"
                  title="Zoom out"
                >
                  -
                </button>
              </>
            )}
            {legendButtonVisible && (
              <button
                type="button"
                className={`flex h-[34px] w-[34px] items-center justify-center transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring ${showZoomControls ? "border-t border-[#1a3a5c]/60" : ""} ${legendButtonActive ? "bg-white/[0.12] text-white" : "text-white/60 hover:bg-white/[0.07] hover:text-white/90"}`}
                onClick={onLegendButtonClick}
                aria-label="Toggle legend"
                title="Legend"
              >
                <Layers className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
