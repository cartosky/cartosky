import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { type StyleSpecification } from "maplibre-gl";
import type { GeoJSON } from "geojson";

import type { LegendPayload } from "@/components/map-legend";
import { sanitizeAnchorFeatureCollection, type AnchorFeatureCollection } from "@/lib/anchor-labels";
import type { GridManifestResponse } from "@/lib/api";
import { API_ORIGIN, MAP_VIEW_DEFAULTS, TILES_BASE } from "@/lib/config";
import { GRID_WEBGL_LAYER_ID, GridWebglLayerController, type GridFrameVisiblePayload } from "@/lib/grid-webgl";

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
export type TileReadySource = "active" | "swap" | "prefetch" | "loop-warm";
export type TileReadyMeta = {
  source: TileReadySource;
  selectionEpoch?: number;
  selectionKey?: string;
};

type SelectionScopedMeta = {
  selectionEpoch: number;
  selectionKey: string;
};

const SCRUB_SWAP_TIMEOUT_MS = 650;
const AUTOPLAY_SWAP_TIMEOUT_MS = 1500;
const VARIABLE_SWITCH_SWAP_TIMEOUT_MS = 1100;
const SETTLE_TIMEOUT_MS = 1200;
const CONTINUOUS_CROSSFADE_MS = 120;
const MICRO_CROSSFADE_MS = 140;
const PREFETCH_BUFFER_COUNT = 8;
const OBSERVED_GRID_SCRUB_AHEAD_PREFETCH = 24;
const OBSERVED_GRID_SCRUB_BEHIND_PREFETCH = 6;
const OVERLAY_RASTER_CONTRAST = 0.11;
const OVERLAY_RASTER_SATURATION = 0.11;
const OVERLAY_RASTER_BRIGHTNESS_MIN = 0.02;
const OVERLAY_RASTER_BRIGHTNESS_MAX = 0.98;
const OVERLAY_RASTER_DARK_CONTRAST = 0.14;
const OVERLAY_RASTER_DARK_SATURATION = 0.14;
const OVERLAY_RASTER_DARK_BRIGHTNESS_MIN = 0.06;
const OVERLAY_RASTER_DARK_BRIGHTNESS_MAX = 1;
const OVERLAY_RASTER_DARK_GRAY_BOOST_CONTRAST = 0.2;
const OVERLAY_RASTER_DARK_GRAY_BOOST_SATURATION = 0.16;
const OVERLAY_RASTER_DARK_GRAY_BOOST_BRIGHTNESS_MIN = 0.1;
const OVERLAY_RASTER_DARK_GRAY_BOOST_BRIGHTNESS_MAX = 1;

// Keep inactive swap buffer warm at tiny opacity to avoid one-frame basemap flash.
const HIDDEN_SWAP_BUFFER_OPACITY = 0.001;
// Keep prefetch layers fully hidden by default to reduce overdraw/compositing cost.
// Prefetch layers are only warmed while an active prefetch URL is being requested.
const HIDDEN_PREFETCH_OPACITY = 0;
const WARM_PREFETCH_OPACITY = 0.001;
const PREFETCH_TILE_EVENT_BUDGET = 1;
const PREFETCH_READY_TIMEOUT_MS = 8000;
const WEBP_TO_TILE_STABLE_MS = 150;
const WEBP_TO_TILE_CROSSFADE_MS = 200;
const WEBP_TO_TILE_FORCE_CROSSFADE_MS = 900;
const ANCHOR_HOVER_RESUME_DELAY_MS = 30;
const ANCHOR_COLLISION_RADIUS_MIN_KM = 18;
const ANCHOR_COLLISION_RADIUS_MAX_KM = 170;
const CONTOUR_SOURCE_ID = "twf-contours";
const CONTOUR_LAYER_ID = "twf-contours";
const STATE_BOUNDARY_SOURCE_ID = "twf-boundaries";
const COASTLINE_LAYER_ID = "twf-coastline";
const STATE_BOUNDARY_LAYER_ID = "twf-state-boundaries";
const COUNTRY_BOUNDARY_LAYER_ID = "twf-country-boundaries";
const COUNTY_BOUNDARY_LAYER_ID = "twf-county-boundaries";
const LAKE_MASK_LAYER_ID = "twf-lake-mask";
const LAKE_SHORELINE_LAYER_ID = "twf-lake-shoreline";
const LOOP_SOURCE_ID = "twf-loop-image";
const LOOP_LAYER_ID = "twf-loop-image";
const LOOP_CANVAS_SOURCE_ID = "twf-loop-canvas";
const LOOP_CANVAS_LAYER_ID = "twf-loop-canvas";
const LOOP_CANVAS_ELEMENT_ID = "twf-loop-canvas-el";
const EMPTY_FEATURE_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

const TRANSPARENT_PIXEL_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";

const DEFAULT_LOOP_BBOX: [number, number, number, number] = [-134.0, 24.0, -60.0, 55.0];

type OverlayBuffer = "a" | "b";
type PlaybackMode = "autoplay" | "scrub" | "variable-switch";
const GRAY_LOW_END_VARIABLES = new Set(["precip_total", "snowfall_total", "qpf6h", "wspd10m", "wgst10m"]);
const SINGLE_OVERLAY_SOURCE = true;
const PRIMARY_OVERLAY_BUFFER: OverlayBuffer = "b";

function sourceId(buffer: OverlayBuffer): string {
  return `twf-overlay-${buffer}`;
}

function layerId(buffer: OverlayBuffer): string {
  return `twf-overlay-${buffer}`;
}

function otherBuffer(buffer: OverlayBuffer): OverlayBuffer {
  if (SINGLE_OVERLAY_SOURCE) {
    return PRIMARY_OVERLAY_BUFFER;
  }
  return buffer === "a" ? "b" : "a";
}

function prefetchSourceId(index: number): string {
  return `twf-prefetch-${index}`;
}

function prefetchLayerId(index: number): string {
  return `twf-prefetch-${index}`;
}

function getResamplingMode(
  variableKind?: string | null,
  displayResamplingOverride?: string | null
): "nearest" | "linear" {
  const normalizedOverride = String(displayResamplingOverride ?? "").trim().toLowerCase();
  if (normalizedOverride === "nearest" || normalizedOverride === "linear") {
    return normalizedOverride;
  }
  if (normalizedOverride === "bilinear") {
    return "linear";
  }
  const normalizedKind = String(variableKind ?? "").trim().toLowerCase();
  if (normalizedKind === "discrete" || normalizedKind === "indexed" || normalizedKind === "categorical") {
    return "nearest";
  }
  return "linear";
}

function getLoopResamplingMode(
  variable?: string,
  variableKind?: string | null,
  displayResamplingOverride?: string | null
): "nearest" | "linear" {
  const variableId = String(variable ?? "").trim().toLowerCase();
  if (variableId === "radar_ptype") {
    return "linear";
  }
  return getResamplingMode(variableKind, displayResamplingOverride);
}

function loopCoordinatesFromBbox(
  bbox: [number, number, number, number] | null | undefined
): [[number, number], [number, number], [number, number], [number, number]] {
  const [west, south, east, north] = bbox ?? DEFAULT_LOOP_BBOX;
  return [
    [west, north],
    [east, north],
    [east, south],
    [west, south],
  ];
}

function getOverlayPaintSettingsForDark(variable?: string): {
  contrast: number;
  saturation: number;
  brightnessMin: number;
  brightnessMax: number;
} {
  if (variable && GRAY_LOW_END_VARIABLES.has(variable)) {
    return {
      contrast: OVERLAY_RASTER_DARK_GRAY_BOOST_CONTRAST,
      saturation: OVERLAY_RASTER_DARK_GRAY_BOOST_SATURATION,
      brightnessMin: OVERLAY_RASTER_DARK_GRAY_BOOST_BRIGHTNESS_MIN,
      brightnessMax: OVERLAY_RASTER_DARK_GRAY_BOOST_BRIGHTNESS_MAX,
    };
  }

  return {
    contrast: OVERLAY_RASTER_DARK_CONTRAST,
    saturation: OVERLAY_RASTER_DARK_SATURATION,
    brightnessMin: OVERLAY_RASTER_DARK_BRIGHTNESS_MIN,
    brightnessMax: OVERLAY_RASTER_DARK_BRIGHTNESS_MAX,
  };
}

function getOverlayPaintSettings(variable?: string, basemapMode: BasemapMode = "light"): {
  contrast: number;
  saturation: number;
  brightnessMin: number;
  brightnessMax: number;
} {
  if (basemapMode === "dark") {
    return getOverlayPaintSettingsForDark(variable);
  }

  if (variable === "wspd10m" || variable === "wgst10m") {
    return {
      contrast: 0,
      saturation: 0,
      brightnessMin: 0,
      brightnessMax: 1,
    };
  }
  return {
    contrast: OVERLAY_RASTER_CONTRAST,
    saturation: OVERLAY_RASTER_SATURATION,
    brightnessMin: OVERLAY_RASTER_BRIGHTNESS_MIN,
    brightnessMax: OVERLAY_RASTER_BRIGHTNESS_MAX,
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
  "raster-resampling": "nearest" | "linear";
  "raster-opacity": number | LabelOpacityExpression;
  "raster-contrast": number;
  "raster-saturation": number;
  "raster-brightness-min": number;
  "raster-brightness-max": number;
} {
  const labelOpacityByZoom = ["interpolate", ["linear"], ["zoom"], 4.3, 0, 5.1, 1] as const;
  if (basemapMode === "dark") {
    return {
      // Use linear filtering to avoid blocky/pixelated labels on zoom.
      "raster-resampling": "linear",
      "raster-opacity": labelOpacityByZoom,
      "raster-contrast": 0.1,
      "raster-saturation": -0.06,
      "raster-brightness-min": 0.05,
      "raster-brightness-max": 1,
    };
  }
  return {
    // Use linear filtering to avoid blocky/pixelated labels on zoom.
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
    const active = feature.properties?.active === true;
    if (!id || !active || !label || !cityName || !Number.isFinite(lng) || !Number.isFinite(lat)) {
      continue;
    }
    activeMarkers.push({
      id,
      lngLat: [lng, lat],
      label,
      cityName,
      priority: anchorPriorityFromId(id),
    });
  }

  return thinAnchorMarkers(activeMarkers, zoom);
}

type MapStyleOptions = {
  includeRuntimeLoopCanvas?: boolean;
  includeRuntimeLoopImageSource?: boolean;
};

export function buildMapStyle(
  overlayUrl: string,
  opacity: number,
  variable?: string,
  variableKind?: string | null,
  displayResamplingOverride?: string | null,
  overlayFadeOutZoom?: { start: number; end: number } | null,
  contourGeoJsonUrl?: string | null,
  loopImageCoordinates: [[number, number], [number, number], [number, number], [number, number]] = loopCoordinatesFromBbox(null),
  basemapMode: BasemapMode = "light",
  options: MapStyleOptions = {}
): StyleSpecification {
  const { includeRuntimeLoopCanvas = true, includeRuntimeLoopImageSource = false } = options;
  const resamplingMode = getResamplingMode(variableKind, displayResamplingOverride);
  const loopResamplingMode = getLoopResamplingMode(variable, variableKind, displayResamplingOverride);
  const paintSettings = getOverlayPaintSettings(variable, basemapMode);
  const basemapTiles = basemapMode === "dark" ? CARTO_DARK_BASE_TILES : CARTO_LIGHT_BASE_TILES;
  const labelTiles = basemapMode === "dark" ? CARTO_DARK_LABEL_TILES : CARTO_LIGHT_LABEL_TILES;
  const mapBackgroundColor = getMapBackgroundColor(basemapMode);
  const boundaryLineColor = getBoundaryLineColor(basemapMode);
  const lakeFillColor = getLakeFillColor(basemapMode);
  const basemapPaint = getBasemapPaintSettings(basemapMode);
  const labelPaint = getLabelPaintSettings(basemapMode);
  const overlayOpacity: any = overlayFadeOutZoom
    ? [
      "interpolate",
      ["linear"],
      ["zoom"],
      overlayFadeOutZoom.start,
      opacity,
      overlayFadeOutZoom.end,
      0,
    ]
    : opacity;
  const overlayPaint: any = {
    "raster-opacity": overlayOpacity,
    "raster-resampling": resamplingMode,
    "raster-fade-duration": 0,
    "raster-contrast": paintSettings.contrast,
    "raster-saturation": paintSettings.saturation,
    "raster-brightness-min": paintSettings.brightnessMin,
    "raster-brightness-max": paintSettings.brightnessMax,
  };
  const prefetchSources = Object.fromEntries(
    Array.from({ length: PREFETCH_BUFFER_COUNT }, (_, index) => [
      prefetchSourceId(index + 1),
      {
        type: "raster",
        tiles: [overlayUrl],
        tileSize: 512,
        minzoom: 4,
      },
    ])
  );
  const prefetchLayers = Array.from({ length: PREFETCH_BUFFER_COUNT }, (_, index) => ({
    id: prefetchLayerId(index + 1),
    type: "raster" as const,
    source: prefetchSourceId(index + 1),
    layout: { visibility: "none" as const },
    paint: overlayPaint,
  }));
  const sources: StyleSpecification["sources"] = {
    "twf-basemap": {
      type: "raster",
      tiles: basemapTiles,
      tileSize: CARTO_TILE_SIZE,
    },
    [sourceId("a")]: {
      type: "raster",
      tiles: [overlayUrl],
      tileSize: 512,
      minzoom: 4,
    },
    [sourceId("b")]: {
      type: "raster",
      tiles: [overlayUrl],
      tileSize: 512,
      minzoom: 4,
    },
    ...prefetchSources,
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
      data: contourGeoJsonUrl ?? EMPTY_FEATURE_COLLECTION,
    },
  };
  if (includeRuntimeLoopImageSource) {
    sources[LOOP_SOURCE_ID] = {
      type: "image",
      url: TRANSPARENT_PIXEL_DATA_URL,
      coordinates: loopImageCoordinates,
    } as any;
  }
  if (includeRuntimeLoopCanvas) {
    sources[LOOP_CANVAS_SOURCE_ID] = {
      type: "canvas",
      canvas: LOOP_CANVAS_ELEMENT_ID,
      coordinates: loopImageCoordinates,
      animate: false,
    } as any;
  }
  const runtimeLoopCanvasLayers = includeRuntimeLoopCanvas
    ? [
        {
          id: LOOP_CANVAS_LAYER_ID,
          type: "raster" as const,
          source: LOOP_CANVAS_SOURCE_ID,
          layout: {
            visibility: "none" as const,
          },
          paint: {
            "raster-opacity": opacity,
            "raster-resampling": loopResamplingMode,
            "raster-fade-duration": 0,
            "raster-contrast": paintSettings.contrast,
            "raster-saturation": paintSettings.saturation,
            "raster-brightness-min": paintSettings.brightnessMin,
            "raster-brightness-max": paintSettings.brightnessMax,
          },
        },
      ]
    : [];
  const runtimeLoopImageLayers = includeRuntimeLoopImageSource
    ? [
        {
          id: LOOP_LAYER_ID,
          type: "raster" as const,
          source: LOOP_SOURCE_ID,
          layout: {
            visibility: "none" as const,
          },
          paint: {
            "raster-opacity": opacity,
            "raster-resampling": loopResamplingMode,
            "raster-fade-duration": 0,
            "raster-contrast": paintSettings.contrast,
            "raster-saturation": paintSettings.saturation,
            "raster-brightness-min": paintSettings.brightnessMin,
            "raster-brightness-max": paintSettings.brightnessMax,
          },
        },
      ]
    : [];

  return {
    version: 8,
    sources,
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
        id: layerId("a"),
        type: "raster",
        source: sourceId("a"),
        layout: {
          visibility: SINGLE_OVERLAY_SOURCE ? ("none" as const) : ("visible" as const),
        },
        paint: overlayPaint,
      },
      {
        id: layerId("b"),
        type: "raster",
        source: sourceId("b"),
        paint: overlayPaint,
      },
      ...prefetchLayers,
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
          "line-join": "round",
          "line-cap": "round",
        },
        paint: {
          "line-color": boundaryLineColor,
          "line-opacity": 0.9,
          "line-width": ["interpolate", ["linear"], ["zoom"], 4, 1, 8, 2, 12, 3],
        },
      },
      ...runtimeLoopImageLayers,
      ...runtimeLoopCanvasLayers,
      {
        id: "twf-labels",
        type: "raster",
        source: "twf-labels",
        paint: labelPaint,
      },
    ],
  };
}

type MapCanvasProps = {
  tileUrl: string;
  selectionKey: string;
  selectionEpoch: number;
  gridManifest?: GridManifestResponse | null;
  gridFrameUrl?: string | null;
  gridFrameHour?: number | null;
  gridLegend?: LegendPayload | null;
  gridActive?: boolean;
  contourGeoJsonUrl?: string | null;
  anchorGeoJson?: AnchorFeatureCollection | null;
  pointLabelsEnabled?: boolean;
  showZoomControls?: boolean;
  region: string;
  regionViews?: Record<string, RegionView>;
  opacity: number;
  mode: PlaybackMode;
  variable?: string;
  variableKind?: string | null;
  displayResamplingOverride?: string | null;
  overlayFadeOutZoom?: { start: number; end: number } | null;
  basemapMode: BasemapMode;
  prefetchTileUrls?: string[];
  crossfade?: boolean;
  loopImageUrl?: string | null;
  loopFrameBitmap?: ImageBitmap | null;
  loopImageBbox?: [number, number, number, number] | null;
  loopActive?: boolean;
  onFrameSettled?: (tileUrl: string, meta?: SelectionScopedMeta) => void;
  onTileReady?: (tileUrl: string, meta?: TileReadyMeta) => void;
  onTileViewportReady?: (tileUrl: string, meta?: SelectionScopedMeta) => void;
  onFrameLoadingChange?: (tileUrl: string, isLoading: boolean, meta?: SelectionScopedMeta) => void;
  onZoomBucketChange?: (bucket: number) => void;
  onZoomRoutingSignal?: (payload: { zoom: number; gestureActive: boolean }) => void;
  onViewportChange?: (payload: { lat: number; lon: number; z: number }) => void;
  onGridFrameVisible?: (payload: GridFrameVisiblePayload) => void;
  onGridFrameReady?: (frameUrl: string) => void;
  onMapReady?: (map: maplibregl.Map) => void;
  onMapHover?: (lat: number, lon: number, x: number, y: number) => void;
  onMapHoverEnd?: () => void;
  /** Exposes the imperative loop-canvas draw function to the parent so the
   *  playback ticker can blit decoded bitmaps without a React render cycle. */
  onDrawLoopFrameRef?: (draw: ((bitmap: ImageBitmap) => boolean) | null) => void;
  /** When true the playback ticker is driving the canvas imperatively; the
   *  prop-based loopFrameBitmap draw should be suppressed to avoid flicker. */
  loopImperativePlaybackActive?: boolean;
};

export function MapCanvas({
  tileUrl,
  selectionKey,
  selectionEpoch,
  gridManifest = null,
  gridFrameUrl = null,
  gridFrameHour = null,
  gridLegend = null,
  gridActive = false,
  contourGeoJsonUrl,
  anchorGeoJson = null,
  pointLabelsEnabled = true,
  showZoomControls = false,
  region,
  regionViews,
  opacity,
  mode,
  variable,
  variableKind,
  displayResamplingOverride = null,
  overlayFadeOutZoom = null,
  basemapMode,
  prefetchTileUrls = [],
  crossfade = false,
  loopImageUrl,
  loopFrameBitmap = null,
  loopImageBbox = null,
  loopActive = false,
  onFrameSettled,
  onTileReady,
  onTileViewportReady,
  onFrameLoadingChange,
  onZoomBucketChange,
  onZoomRoutingSignal,
  onViewportChange,
  onGridFrameVisible,
  onGridFrameReady,
  onMapReady,
  onMapHover,
  onMapHoverEnd,
  onDrawLoopFrameRef,
  loopImperativePlaybackActive = false,
}: MapCanvasProps) {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const loopCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const gridWebglControllerRef = useRef<GridWebglLayerController | null>(null);
  if (!gridWebglControllerRef.current) {
    gridWebglControllerRef.current = new GridWebglLayerController();
  }
  const [isLoaded, setIsLoaded] = useState(false);
  const [anchorTooltip, setAnchorTooltip] = useState<AnchorTooltipState | null>(null);
  const [readyLoopImageFrame, setReadyLoopImageFrame] = useState<{
    url: string;
    selectionEpoch: number;
    selectionKey: string;
  } | null>(null);
  const [readyLoopCanvasFrame, setReadyLoopCanvasFrame] = useState<{
    url: string;
    selectionEpoch: number;
    selectionKey: string;
  } | null>(null);
  const readyLoopImageUrl = readyLoopImageFrame?.url ?? null;
  const activeBufferRef = useRef<OverlayBuffer>(PRIMARY_OVERLAY_BUFFER);
  const activeTileUrlRef = useRef(tileUrl);
  const swapTokenRef = useRef(0);
  const prefetchTokenRef = useRef(0);
  const prefetchUrlsRef = useRef<string[]>(Array.from({ length: PREFETCH_BUFFER_COUNT }, () => ""));
  const sourceRequestedUrlRef = useRef<Map<string, string>>(new Map());
  const sourceRequestTokenRef = useRef<Map<string, number>>(new Map());
  const sourceEventCountRef = useRef<Map<string, number>>(new Map());
  const fadeTokenRef = useRef(0);
  const fadeRafRef = useRef<number | null>(null);
  const tileViewportReadyTokenRef = useRef(0);
  const basemapStyleSwapTokenRef = useRef(0);
  const lastAppliedBasemapModeRef = useRef<BasemapMode>(basemapMode);
  const loopToTileRafRef = useRef<number | null>(null);
  const loopToTileStableTimerRef = useRef<number | null>(null);
  const loopToTileForceTimerRef = useRef<number | null>(null);
  const loopToTileIdleCleanupRef = useRef<(() => void) | null>(null);
  const loopToTileTokenRef = useRef(0);
  const loopImageRequestTokenRef = useRef(0);
  const loopImagePreloadRef = useRef<HTMLImageElement | null>(null);
  const loopImagePendingSignatureRef = useRef<string | null>(null);
  const loopImageCommittedSignatureRef = useRef<string | null>(null);
  const previousLoopActiveRef = useRef(loopActive);
  const isLoopToTileTransitioningRef = useRef(false);
  const currentSelectionEpochRef = useRef(selectionEpoch);
  currentSelectionEpochRef.current = selectionEpoch;
  const anchorMarkersRef = useRef<Map<string, AnchorMarkerRecord>>(new Map());
  const isHoveringAnchorRef = useRef(false);
  const anchorHoverLeaveTimeoutRef = useRef<number | null>(null);
  const onMapReadyRef = useRef(onMapReady);
  onMapReadyRef.current = onMapReady;
  const onViewportChangeRef = useRef(onViewportChange);
  onViewportChangeRef.current = onViewportChange;
  const contourRequestTokenRef = useRef(0);
  const contourAbortRef = useRef<AbortController | null>(null);
  const contourCacheRef = useRef<Map<string, GeoJSON.FeatureCollection>>(new Map());

  const view = useMemo(() => {
    return regionViews?.[region] ?? {
      center: [MAP_VIEW_DEFAULTS.center[1], MAP_VIEW_DEFAULTS.center[0]] as [number, number],
      zoom: MAP_VIEW_DEFAULTS.zoom,
    };
  }, [region, regionViews]);
  const loopImageCoordinates = useMemo(
    () => loopCoordinatesFromBbox(loopImageBbox),
    [loopImageBbox]
  );
  const apiRoot = useMemo(() => API_ORIGIN.replace(/\/$/, ""), []);
  const gridPrefetchUrls = useMemo(() => {
    if (!gridManifest?.lods?.length || !gridFrameUrl || !Number.isFinite(gridFrameHour)) {
      return [] as string[];
    }
    const isObservedGrid = String(gridManifest?.model ?? "").trim().toLowerCase() === "mrms";
    const lod = gridManifest.lods.find((entry) => Number(entry?.level) === 0) ?? gridManifest.lods[0] ?? null;
    const frames = Array.isArray(lod?.frames) ? lod.frames : [];
    const frameHours = frames
      .map((entry) => Number(entry?.fh))
      .filter(Number.isFinite)
      .sort((a, b) => a - b);
    const pivot = frameHours.indexOf(Number(gridFrameHour));
    if (pivot < 0) {
      return [] as string[];
    }
    const urls: string[] = [];
    const remainingAhead = Math.max(0, frameHours.length - 1 - pivot);
    const remainingBehind = Math.max(0, pivot);
    const aheadTarget = mode === "autoplay"
      ? Math.min(remainingAhead, 8)
      : mode === "variable-switch"
        ? Math.min(remainingAhead, 6)
        : Math.min(remainingAhead, isObservedGrid ? OBSERVED_GRID_SCRUB_AHEAD_PREFETCH : 4);
    const behindTarget = mode === "autoplay"
      ? Math.min(remainingBehind, 2)
      : mode === "variable-switch"
        ? Math.min(remainingBehind, 2)
        : Math.min(remainingBehind, isObservedGrid ? OBSERVED_GRID_SCRUB_BEHIND_PREFETCH : 1);
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
      const frame = frames.find((entry) => Number(entry?.fh) === hour);
      const url = normalizeGridUrl(String(frame?.url ?? "").trim());
      if (url && url !== gridFrameUrl && !urls.includes(url)) {
        urls.push(url);
      }
    };
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
    return urls;
  }, [apiRoot, gridFrameHour, gridFrameUrl, gridManifest, mode]);
  const hasBitmapCanvasLoopFrame = Boolean(loopFrameBitmap);
  const hasReadyLoopCanvasFrame = Boolean(
    readyLoopCanvasFrame &&
    readyLoopCanvasFrame.selectionEpoch === selectionEpoch &&
    readyLoopCanvasFrame.selectionKey === selectionKey
  );
  const hasCanvasLoopFrame = Boolean(hasBitmapCanvasLoopFrame || hasReadyLoopCanvasFrame);
  // True when a frame from the *current* selection has been committed to the
  // ImageSource — regardless of which forecast hour it is. This keeps the loop
  // layer visible with the previously-committed frame while the next one loads,
  // but immediately goes false when selectionEpoch/selectionKey change.
  const isReadyLoopImage = Boolean(
    readyLoopImageFrame &&
    readyLoopImageFrame.selectionEpoch === selectionEpoch &&
    readyLoopImageFrame.selectionKey === selectionKey
  );
  const hasLoopVisual = Boolean(hasCanvasLoopFrame || isReadyLoopImage);

  useEffect(() => {
    if (!loopImageUrl) {
      setReadyLoopImageFrame(null);
      setReadyLoopCanvasFrame(null);
      return;
    }
    if (mode === "variable-switch" && readyLoopImageUrl !== loopImageUrl) {
      setReadyLoopImageFrame(null);
      setReadyLoopCanvasFrame(null);
    }
  }, [loopImageUrl, mode, readyLoopImageUrl]);

  const drawToLoopCanvas = useCallback(
    (frame: CanvasImageSource, width: number, height: number): boolean => {
      const canvas = loopCanvasRef.current;
      if (!canvas) {
        return false;
      }
      // Guard against detached ImageBitmaps whose backing store has been freed
      // by LRU eviction or dataset-change cache clears.  Attempting to
      // drawImage a closed bitmap throws InvalidStateError.
      if (frame instanceof ImageBitmap && (frame.width === 0 || frame.height === 0)) {
        return false;
      }
      const nextWidth = Math.max(1, Math.floor(width));
      const nextHeight = Math.max(1, Math.floor(height));
      if (canvas.width !== nextWidth) {
        canvas.width = nextWidth;
      }
      if (canvas.height !== nextHeight) {
        canvas.height = nextHeight;
      }
      const ctx = canvas.getContext("2d", { alpha: true });
      if (!ctx) {
        return false;
      }
      ctx.clearRect(0, 0, nextWidth, nextHeight);
      try {
        ctx.drawImage(frame, 0, 0, nextWidth, nextHeight);
      } catch {
        // ImageBitmap was detached between the guard check and drawImage.
        return false;
      }

      const map = mapRef.current;
      if (map && isLoaded) {
        const canvasSource = map.getSource(LOOP_CANVAS_SOURCE_ID) as maplibregl.CanvasSource | undefined;
        if (canvasSource && typeof canvasSource.setCoordinates === "function") {
          canvasSource.setCoordinates(loopImageCoordinates);
        }
        // MapLibre's CanvasSource with `animate: false` never re-reads canvas
        // pixels after initial load. Calling play() sets the internal `_playing`
        // flag so that the next `prepare()` call (during the render cycle)
        // uploads the updated canvas texture to the GPU. play() also calls
        // triggerRepaint() internally. We pause after the render completes to
        // avoid continuous repainting.
        if (canvasSource && typeof (canvasSource as any).play === "function") {
          (canvasSource as any).play();
          map.once("render", () => {
            if (typeof (canvasSource as any).pause === "function") {
              (canvasSource as any).pause();
            }
          });
        } else {
          map.triggerRepaint();
        }
      }
      return true;
    },
    [isLoaded, loopImageCoordinates]
  );

  // Publish a thin imperative draw handle so the playback ticker in App.tsx
  // can blit decoded bitmaps directly without triggering a React render cycle.
  useEffect(() => {
    if (!onDrawLoopFrameRef) return;
    const draw = (bitmap: ImageBitmap): boolean =>
      drawToLoopCanvas(bitmap, bitmap.width, bitmap.height);
    onDrawLoopFrameRef(draw);
    return () => {
      onDrawLoopFrameRef(null);
    };
  }, [drawToLoopCanvas, onDrawLoopFrameRef]);

  useEffect(() => {
    // When the imperative playback fast-path is active, the RAF ticker draws
    // frames directly — skip the prop-based draw to avoid stale-frame flicker.
    if (loopImperativePlaybackActive) {
      return;
    }
    if (!loopFrameBitmap) {
      return;
    }
    drawToLoopCanvas(loopFrameBitmap, loopFrameBitmap.width, loopFrameBitmap.height);
  }, [loopFrameBitmap, drawToLoopCanvas, loopImperativePlaybackActive]);

  const initializeSourceTracking = useCallback((currentTileUrl: string) => {
    const sourceA = sourceId("a");
    const sourceB = sourceId("b");
    sourceRequestedUrlRef.current.set(sourceA, currentTileUrl);
    sourceRequestedUrlRef.current.set(sourceB, currentTileUrl);
    sourceRequestTokenRef.current.set(sourceA, 0);
    sourceRequestTokenRef.current.set(sourceB, 0);
    sourceEventCountRef.current.set(sourceA, 0);
    sourceEventCountRef.current.set(sourceB, 0);

    for (let idx = 1; idx <= PREFETCH_BUFFER_COUNT; idx += 1) {
      const prefetchSource = prefetchSourceId(idx);
      sourceRequestedUrlRef.current.set(prefetchSource, currentTileUrl);
      sourceRequestTokenRef.current.set(prefetchSource, 0);
      sourceEventCountRef.current.set(prefetchSource, 0);
    }
  }, []);

  const setLayerOpacity = useCallback((map: maplibregl.Map, id: string, value: number) => {
    if (!map.getLayer(id)) {
      return;
    }
    map.setPaintProperty(id, "raster-opacity", Math.max(0, Math.min(1, value)));
  }, []);

  const setTilesSafe = useCallback(
    (
      source: maplibregl.RasterTileSource,
      tiles: string[],
      context: { sourceId: string; tileUrl: string; mode: string }
    ): boolean => {
      try {
        source.setTiles(tiles);
        return true;
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") {
          console.debug("[map] ignored setTiles AbortError", context);
          return false;
        }
        console.warn("[map] setTiles failed", { ...context, error });
        return false;
      }
    },
    []
  );

  const setLayerRasterPaint = useCallback(
    (
      map: maplibregl.Map,
      id: string,
      variableId?: string,
      variableKindId?: string | null,
      displayResamplingOverrideValue?: string | null,
      basemapModeValue: BasemapMode = "light"
    ) => {
      if (!map.getLayer(id)) {
        return;
      }
      const resamplingMode = id === LOOP_LAYER_ID || id === LOOP_CANVAS_LAYER_ID
        ? getLoopResamplingMode(variableId, variableKindId, displayResamplingOverrideValue)
        : getResamplingMode(variableKindId, displayResamplingOverrideValue);
      const paintSettings = getOverlayPaintSettings(variableId, basemapModeValue);
      map.setPaintProperty(id, "raster-resampling", resamplingMode);
      map.setPaintProperty(id, "raster-contrast", paintSettings.contrast);
      map.setPaintProperty(id, "raster-saturation", paintSettings.saturation);
      map.setPaintProperty(id, "raster-brightness-min", paintSettings.brightnessMin);
      map.setPaintProperty(id, "raster-brightness-max", paintSettings.brightnessMax);
    },
    []
  );

  const cancelPendingLoopImageUpdate = useCallback(() => {
    loopImageRequestTokenRef.current += 1;
    loopImagePendingSignatureRef.current = null;
    const pending = loopImagePreloadRef.current;
    if (!pending) {
      return;
    }
    pending.onload = null;
    pending.onerror = null;
    loopImagePreloadRef.current = null;
  }, []);

  const queueLoopImageUpdate = useCallback(
    (
      map: maplibregl.Map,
      nextLoopImageUrl: string | null | undefined,
      nextLoopImageCoordinates: [[number, number], [number, number], [number, number], [number, number]],
      selectionScope: SelectionScopedMeta,
    ) => {
      if (!nextLoopImageUrl) {
        cancelPendingLoopImageUpdate();
        loopImageCommittedSignatureRef.current = null;
        setReadyLoopImageFrame(null);
        setReadyLoopCanvasFrame(null);
        return;
      }

      const requestSignature = `${selectionScope.selectionEpoch}:${selectionScope.selectionKey}:${nextLoopImageUrl}`;
      if (
        loopImagePendingSignatureRef.current === requestSignature ||
        loopImageCommittedSignatureRef.current === requestSignature
      ) {
        const loopSource = map.getSource(LOOP_SOURCE_ID) as maplibregl.ImageSource | undefined;
        if (loopSource && typeof loopSource.setCoordinates === "function") {
          loopSource.setCoordinates(nextLoopImageCoordinates);
          map.triggerRepaint();
        }
        return;
      }

      cancelPendingLoopImageUpdate();
      loopImagePendingSignatureRef.current = requestSignature;

      const requestToken = loopImageRequestTokenRef.current;
      const image = new Image();
      image.decoding = "async";
      image.crossOrigin = "anonymous";
      loopImagePreloadRef.current = image;

      image.onload = () => {
        if (loopImageRequestTokenRef.current !== requestToken) {
          if (loopImagePendingSignatureRef.current === requestSignature) {
            loopImagePendingSignatureRef.current = null;
          }
          return;
        }
        if (selectionScope.selectionEpoch !== currentSelectionEpochRef.current) {
          if (loopImagePendingSignatureRef.current === requestSignature) {
            loopImagePendingSignatureRef.current = null;
          }
          return;
        }
        const loopSource = map.getSource(LOOP_SOURCE_ID) as maplibregl.ImageSource | undefined;
        if (!loopSource || typeof loopSource.updateImage !== "function") {
          return;
        }
        try {
          loopSource.updateImage({
            url: nextLoopImageUrl,
            coordinates: nextLoopImageCoordinates,
          });
          setReadyLoopImageFrame({ url: nextLoopImageUrl, selectionEpoch: selectionScope.selectionEpoch, selectionKey: selectionScope.selectionKey });
          setReadyLoopCanvasFrame(null);
          loopImageCommittedSignatureRef.current = requestSignature;
          loopImagePendingSignatureRef.current = null;
          map.triggerRepaint();
        } catch (error) {
          const drawnToCanvas = drawToLoopCanvas(image, image.naturalWidth || image.width, image.naturalHeight || image.height);
          if (drawnToCanvas) {
            setReadyLoopImageFrame(null);
            setReadyLoopCanvasFrame({
              url: nextLoopImageUrl,
              selectionEpoch: selectionScope.selectionEpoch,
              selectionKey: selectionScope.selectionKey,
            });
            loopImageCommittedSignatureRef.current = requestSignature;
            loopImagePendingSignatureRef.current = null;
            map.triggerRepaint();
          } else {
            setReadyLoopImageFrame(null);
            setReadyLoopCanvasFrame(null);
            loopImagePendingSignatureRef.current = null;
            console.warn("[map] failed to update loop image source", { loopImageUrl: nextLoopImageUrl, error });
          }
        } finally {
          if (loopImagePreloadRef.current === image) {
            loopImagePreloadRef.current = null;
          }
        }
      };

      image.onerror = () => {
        if (loopImageRequestTokenRef.current !== requestToken) {
          return;
        }
        if (loopImagePendingSignatureRef.current === requestSignature) {
          loopImagePendingSignatureRef.current = null;
        }
        setReadyLoopImageFrame(null);
        setReadyLoopCanvasFrame(null);
        console.warn("[map] failed to preload loop image", { loopImageUrl: nextLoopImageUrl });
        if (loopImagePreloadRef.current === image) {
          loopImagePreloadRef.current = null;
        }
      };

      image.src = nextLoopImageUrl;
    },
    [cancelPendingLoopImageUpdate, drawToLoopCanvas, mode, variable]
  );

  const enforceLayerOrder = useCallback((map: maplibregl.Map) => {
    if (!map.getLayer("twf-labels")) {
      return;
    }

    const beforeId = map.getLayer(CONTOUR_LAYER_ID) ? CONTOUR_LAYER_ID : "twf-labels";
    const overlayIds = [
      layerId("a"),
      layerId("b"),
      ...Array.from({ length: PREFETCH_BUFFER_COUNT }, (_, index) => prefetchLayerId(index + 1)),
    ];

    overlayIds.forEach((id) => {
      if (map.getLayer(id)) {
        map.moveLayer(id, beforeId);
      }
    });

    if (map.getLayer(CONTOUR_LAYER_ID)) {
      map.moveLayer(CONTOUR_LAYER_ID, "twf-labels");
    }
    if (map.getLayer(LOOP_LAYER_ID)) {
      map.moveLayer(LOOP_LAYER_ID, "twf-labels");
    }
    if (map.getLayer(LOOP_CANVAS_LAYER_ID)) {
      map.moveLayer(LOOP_CANVAS_LAYER_ID, "twf-labels");
    }
    if (map.getLayer(GRID_WEBGL_LAYER_ID) && map.getLayer(COASTLINE_LAYER_ID)) {
      map.moveLayer(GRID_WEBGL_LAYER_ID, COASTLINE_LAYER_ID);
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
    if (map.getLayer(LAKE_MASK_LAYER_ID)) {
      map.moveLayer(LAKE_MASK_LAYER_ID, "twf-labels");
    }
    if (map.getLayer(LAKE_SHORELINE_LAYER_ID)) {
      map.moveLayer(LAKE_SHORELINE_LAYER_ID, "twf-labels");
    }
    map.moveLayer("twf-labels");
  }, []);

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
          onMapHoverEndRef.current?.();
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
          onMapHoverEndRef.current?.();
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

        element.appendChild(chip);

        const marker = new maplibregl.Marker({
          element,
          anchor: "center",
          offset: [0, 0],
        })
          .setLngLat(activeMarker.lngLat)
          .addTo(map);

        anchorMarkersRef.current.set(activeMarker.id, {
          marker,
          element,
          chip,
        });
        snapAnchorMarkerToPixels(map, { marker, element, chip });
      }
    },
    [clearAnchorMarkers, hideAnchorTooltip, showAnchorTooltip]
  );

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

  const notifySettled = useCallback(
    (
      map: maplibregl.Map,
      source: string,
      url: string,
      readySource: TileReadySource,
      selectionScope: SelectionScopedMeta
    ) => {
      let done = false;
      let timeoutId: number | null = null;

      const cleanup = () => {
        map.off("sourcedata", onSourceData);
        if (timeoutId !== null) {
          window.clearTimeout(timeoutId);
          timeoutId = null;
        }
      };

      const fire = () => {
        if (done) return;
        done = true;
        cleanup();
        onTileReady?.(url, {
          source: readySource,
          selectionEpoch: selectionScope.selectionEpoch,
          selectionKey: selectionScope.selectionKey,
        });
        onFrameSettled?.(url, selectionScope);
      };

      const onSourceData = (event: maplibregl.MapSourceDataEvent) => {
        if (event.sourceId !== source) {
          return;
        }
        sourceEventCountRef.current.set(source, (sourceEventCountRef.current.get(source) ?? 0) + 1);
        if (map.isSourceLoaded(source)) {
          window.requestAnimationFrame(() => fire());
        }
      };

      if (map.isSourceLoaded(source)) {
        window.requestAnimationFrame(() => fire());
        return () => {
          done = true;
          cleanup();
        };
      }

      map.on("sourcedata", onSourceData);
      timeoutId = window.setTimeout(() => {
        console.warn("[map] settle fallback timeout", { sourceId: source, tileUrl: url });
        // Never mark settled from timeout; wait for real source readiness.
      }, SETTLE_TIMEOUT_MS);

      return () => {
        done = true;
        cleanup();
      };
    },
    [onTileReady, onFrameSettled]
  );

  const cancelCrossfade = useCallback(() => {
    fadeTokenRef.current += 1;
    if (fadeRafRef.current !== null) {
      window.cancelAnimationFrame(fadeRafRef.current);
      fadeRafRef.current = null;
    }
  }, []);

  const cancelLoopToTileTransition = useCallback(() => {
    loopToTileTokenRef.current += 1;
    if (loopToTileRafRef.current !== null) {
      window.cancelAnimationFrame(loopToTileRafRef.current);
      loopToTileRafRef.current = null;
    }
    if (loopToTileStableTimerRef.current !== null) {
      window.clearTimeout(loopToTileStableTimerRef.current);
      loopToTileStableTimerRef.current = null;
    }
    if (loopToTileForceTimerRef.current !== null) {
      window.clearTimeout(loopToTileForceTimerRef.current);
      loopToTileForceTimerRef.current = null;
    }
    if (loopToTileIdleCleanupRef.current) {
      loopToTileIdleCleanupRef.current();
      loopToTileIdleCleanupRef.current = null;
    }
    isLoopToTileTransitioningRef.current = false;
  }, []);

  useEffect(() => {
    swapTokenRef.current += 1;
    prefetchTokenRef.current += 1;
    tileViewportReadyTokenRef.current += 1;
    basemapStyleSwapTokenRef.current += 1;
    cancelCrossfade();
    cancelLoopToTileTransition();
    cancelPendingLoopImageUpdate();
    setReadyLoopImageFrame(null);
    setReadyLoopCanvasFrame(null);
    const loopCanvas = loopCanvasRef.current;
    if (loopCanvas) {
      const ctx = loopCanvas.getContext("2d", { alpha: true });
      if (ctx) {
        ctx.clearRect(0, 0, loopCanvas.width, loopCanvas.height);
      }
    }
  }, [
    selectionEpoch,
    selectionKey,
    cancelCrossfade,
    cancelLoopToTileTransition,
    cancelPendingLoopImageUpdate,
  ]);

  const runCrossfade = useCallback(
    (map: maplibregl.Map, fromBuffer: OverlayBuffer, toBuffer: OverlayBuffer, targetOpacity: number) => {
      cancelCrossfade();

      // When SINGLE_OVERLAY_SOURCE is enabled, both buffers are the same layer.
      // A dual-buffer crossfade is meaningless — just snap to target opacity.
      if (fromBuffer === toBuffer) {
        setLayerOpacity(map, layerId(toBuffer), targetOpacity);
        return;
      }

      const token = fadeTokenRef.current;
      const started = performance.now();

      const tick = (now: number) => {
        if (token !== fadeTokenRef.current) {
          return;
        }
        const progress = Math.min(1, (now - started) / CONTINUOUS_CROSSFADE_MS);
        const fromOpacity = targetOpacity * (1 - progress);
        const toOpacity = targetOpacity * progress;

        setLayerOpacity(map, layerId(fromBuffer), fromOpacity);
        setLayerOpacity(map, layerId(toBuffer), toOpacity);

        if (progress < 1) {
          fadeRafRef.current = window.requestAnimationFrame(tick);
          return;
        }

        setLayerOpacity(map, layerId(toBuffer), targetOpacity);
        // Defer old-buffer hide by 2 paint ticks to avoid white flash.
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => {
            if (token !== fadeTokenRef.current) {
              return;
            }
            setLayerOpacity(map, layerId(fromBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
          });
        });

        fadeRafRef.current = null;
      };

      setLayerOpacity(map, layerId(fromBuffer), targetOpacity);
      setLayerOpacity(map, layerId(toBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
      fadeRafRef.current = window.requestAnimationFrame(tick);
    },
    [cancelCrossfade, setLayerOpacity]
  );

  const runMicroCrossfade = useCallback(
    (map: maplibregl.Map, fromBuffer: OverlayBuffer, toBuffer: OverlayBuffer, targetOpacity: number, token: number) => {
      // When SINGLE_OVERLAY_SOURCE is enabled, both buffers are the same layer.
      // A dual-buffer micro-crossfade is meaningless — just snap to target opacity.
      if (fromBuffer === toBuffer) {
        setLayerOpacity(map, layerId(toBuffer), targetOpacity);
        return;
      }

      const started = performance.now();
      
      const tick = (now: number) => {
        if (token !== swapTokenRef.current) {
          return;
        }
        const elapsed = now - started;
        const progress = Math.min(1, elapsed / MICRO_CROSSFADE_MS);
        
        // Quick fade: new layer fades in while old layer stays visible, then old fades out
        const toOpacity = targetOpacity * progress;
        setLayerOpacity(map, layerId(toBuffer), toOpacity);
        
        if (progress < 1) {
          window.requestAnimationFrame(tick);
        } else {
          // Once new layer is fully visible, defer old-layer hide by 2 paint ticks
          // to avoid a brief basemap flash during rapid swaps.
          window.requestAnimationFrame(() => {
            window.requestAnimationFrame(() => {
              if (token !== swapTokenRef.current) {
                return;
              }
              setLayerOpacity(map, layerId(fromBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
            });
          });
        }
      };
      
      // Start with old layer at full opacity, new layer hidden
      setLayerOpacity(map, layerId(fromBuffer), targetOpacity);
      setLayerOpacity(map, layerId(toBuffer), 0);
      window.requestAnimationFrame(tick);
    },
    [setLayerOpacity]
  );

  const waitForSourceReady = useCallback(
    (
      map: maplibregl.Map,
      source: string,
      expectedUrl: string,
      expectedRequestToken: number,
      minEventCount: number,
      modeValue: PlaybackMode,
      onReady: () => void,
      onTimeout?: () => void,
      timeoutMsOverride?: number
    ) => {
      const timeoutMs = timeoutMsOverride
        ?? (
          modeValue === "autoplay"
            ? AUTOPLAY_SWAP_TIMEOUT_MS
            : modeValue === "variable-switch"
              ? VARIABLE_SWITCH_SWAP_TIMEOUT_MS
              : SCRUB_SWAP_TIMEOUT_MS
        );
      let done = false;
      let timeoutId: number | null = null;

      const cleanup = () => {
        map.off("sourcedata", onSourceData);
        if (timeoutId !== null) {
          window.clearTimeout(timeoutId);
          timeoutId = null;
        }
      };

      const finishReady = () => {
        if (done) return;
        done = true;
        cleanup();
        onReady();
      };

      const finishTimeout = () => {
        if (done) return;
        if (modeValue === "autoplay") {
          done = true;
          cleanup();
        }
        onTimeout?.();
      };

      const readyForMode = () => {
        const requested = sourceRequestedUrlRef.current.get(source);
        const token = sourceRequestTokenRef.current.get(source) ?? 0;
        const eventCount = sourceEventCountRef.current.get(source) ?? 0;
        return (
          map.isSourceLoaded(source) &&
          requested === expectedUrl &&
          token === expectedRequestToken &&
          eventCount > minEventCount
        );
      };

      const finishReadyAfterRender = () => {
        if (done) return;
        // Double RAF ensures tiles are rendered before swap
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => {
            if (!done) {
              finishReady();
            }
          });
        });
      };

      const onSourceData = (event: maplibregl.MapSourceDataEvent) => {
        if (event.sourceId !== source) {
          return;
        }
        sourceEventCountRef.current.set(source, (sourceEventCountRef.current.get(source) ?? 0) + 1);
        if (readyForMode()) {
          finishReadyAfterRender();
        }
      };

      map.on("sourcedata", onSourceData);

      timeoutId = window.setTimeout(() => finishTimeout(), timeoutMs);

      if (readyForMode()) {
        finishReadyAfterRender();
      }

      return () => {
        done = true;
        cleanup();
      };
    },
    []
  );

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) {
      return;
    }

    let resizeRafId: number | null = null;
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: buildMapStyle(
        tileUrl,
        opacity,
        variable,
        variableKind,
        displayResamplingOverride,
        overlayFadeOutZoom,
        contourGeoJsonUrl,
        loopImageCoordinates,
        basemapMode
      ),
      center: view.center,
      zoom: view.zoom,
      minZoom: view.minZoom ?? 3,
      maxZoom: view.maxZoom ?? 11,
      attributionControl: false,
      preserveDrawingBuffer: true,
    });

    const handleMapError = (event: { error?: unknown; sourceId?: unknown; tile?: unknown }) => {
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
        // Expected when setTiles() rapidly supersedes in-flight requests.
        return;
      }

      if (err) {
        console.warn("[map] MapLibre error", err);
      }
    };

    map.on("error", handleMapError as any);

    map.on("load", () => {
      setIsLoaded(true);
      initializeSourceTracking(tileUrl);
      lastAppliedBasemapModeRef.current = basemapMode;
      enforceLayerOrder(map);
      gridWebglControllerRef.current?.ensureAttached(map, COASTLINE_LAYER_ID);
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
      map.off("error", handleMapError as any);
      cancelCrossfade();
      cancelLoopToTileTransition();
      clearAnchorMarkers();
      gridWebglControllerRef.current?.remove(map);
      map.remove();
      mapRef.current = null;
      cancelPendingLoopImageUpdate();
      setIsLoaded(false);
    };
  }, [cancelCrossfade, cancelLoopToTileTransition, cancelPendingLoopImageUpdate, clearAnchorMarkers, enforceLayerOrder, initializeSourceTracking]);

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

    void fetch(normalizedUrl, {
      credentials: "omit",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Contour request failed: ${response.status}`);
        }
        return (await response.json()) as GeoJSON.FeatureCollection;
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
  }, [contourGeoJsonUrl, isLoaded]);

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

    return () => {
      map.off("move", scheduleSync);
      map.off("moveend", scheduleSync);
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

    const snapAllAnchorMarkers = () => {
      for (const record of anchorMarkersRef.current.values()) {
        snapAnchorMarkerToPixels(map, record);
      }
    };

    map.on("render", snapAllAnchorMarkers);
    map.on("moveend", snapAllAnchorMarkers);
    snapAllAnchorMarkers();

    return () => {
      map.off("render", snapAllAnchorMarkers);
      map.off("moveend", snapAllAnchorMarkers);
    };
  }, [isLoaded]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded || !map.getLayer(CONTOUR_LAYER_ID)) {
      return;
    }
    map.setLayoutProperty(
      CONTOUR_LAYER_ID,
      "visibility",
      "none"
    );
    enforceLayerOrder(map);
  }, [isLoaded, enforceLayerOrder]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    if (lastAppliedBasemapModeRef.current === basemapMode) {
      return;
    }

    const token = ++basemapStyleSwapTokenRef.current;
    lastAppliedBasemapModeRef.current = basemapMode;
    cancelCrossfade();

    const style = buildMapStyle(
      activeTileUrlRef.current,
      opacity,
      variable,
      variableKind,
      displayResamplingOverride,
      overlayFadeOutZoom,
      contourGeoJsonUrl,
      loopImageCoordinates,
      basemapMode
    );

    const onStyleData = () => {
      if (token !== basemapStyleSwapTokenRef.current) {
        return;
      }

      gridWebglControllerRef.current?.ensureAttached(map, COASTLINE_LAYER_ID);
      initializeSourceTracking(activeTileUrlRef.current);

      const activeBuffer = activeBufferRef.current;
      const inactiveBuffer = otherBuffer(activeBuffer);
      if (loopActive || gridActive) {
        setLayerVisibility(map, layerId(activeBuffer), false);
        setLayerVisibility(map, layerId(inactiveBuffer), false);
        setLayerOpacity(map, layerId(activeBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
        setLayerOpacity(map, layerId(inactiveBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
      } else {
        setLayerVisibility(map, layerId(activeBuffer), true);
        setLayerOpacity(map, layerId(activeBuffer), opacity);
        if (inactiveBuffer !== activeBuffer) {
          setLayerOpacity(map, layerId(inactiveBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
          setLayerVisibility(map, layerId(inactiveBuffer), false);
        }
      }

      if (gridActive) {
        cancelPendingLoopImageUpdate();
        setReadyLoopImageFrame(null);
        setReadyLoopCanvasFrame(null);
      } else if (loopFrameBitmap) {
        cancelPendingLoopImageUpdate();
      } else if (loopImageUrl) {
        queueLoopImageUpdate(map, loopImageUrl, loopImageCoordinates, { selectionEpoch, selectionKey });
      }
      const loopCanvasSource = map.getSource(LOOP_CANVAS_SOURCE_ID) as maplibregl.CanvasSource | undefined;
      if (loopCanvasSource && typeof loopCanvasSource.setCoordinates === "function") {
        loopCanvasSource.setCoordinates(loopImageCoordinates);
      }

      const shouldShowLoop = Boolean((loopActive || isLoopToTileTransitioningRef.current) && hasLoopVisual);
      setLayerVisibility(map, LOOP_LAYER_ID, shouldShowLoop && !hasCanvasLoopFrame);
      setLayerVisibility(map, LOOP_CANVAS_LAYER_ID, shouldShowLoop && hasCanvasLoopFrame);
      setLayerOpacity(map, LOOP_LAYER_ID, opacity);
      setLayerOpacity(map, LOOP_CANVAS_LAYER_ID, opacity);
      for (let idx = 1; idx <= PREFETCH_BUFFER_COUNT; idx += 1) {
        setLayerOpacity(map, prefetchLayerId(idx), HIDDEN_PREFETCH_OPACITY);
        setLayerVisibility(map, prefetchLayerId(idx), false);
      }
      setLayerVisibility(
        map,
        CONTOUR_LAYER_ID,
        false
      );

      setLayerRasterPaint(map, layerId("a"), variable, variableKind, displayResamplingOverride, basemapMode);
      setLayerRasterPaint(map, layerId("b"), variable, variableKind, displayResamplingOverride, basemapMode);
      for (let idx = 1; idx <= PREFETCH_BUFFER_COUNT; idx += 1) {
        setLayerRasterPaint(map, prefetchLayerId(idx), variable, variableKind, displayResamplingOverride, basemapMode);
      }
      setLayerRasterPaint(map, LOOP_LAYER_ID, variable, variableKind, displayResamplingOverride, basemapMode);
      setLayerRasterPaint(map, LOOP_CANVAS_LAYER_ID, variable, variableKind, displayResamplingOverride, basemapMode);

      enforceLayerOrder(map);
    };

    map.once("styledata", onStyleData);
    map.setStyle(style);

    return () => {
      map.off("styledata", onStyleData);
      cancelPendingLoopImageUpdate();
    };
  }, [
    basemapMode,
    isLoaded,
    cancelCrossfade,
    cancelPendingLoopImageUpdate,
    contourGeoJsonUrl,
    enforceLayerOrder,
    initializeSourceTracking,
    loopImageCoordinates,
    loopActive,
    hasCanvasLoopFrame,
    hasLoopVisual,
    loopImageUrl,
    overlayFadeOutZoom,
    opacity,
    queueLoopImageUpdate,
    setLayerOpacity,
    setLayerRasterPaint,
    gridActive,
    variable,
    variableKind,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    const controller = gridWebglControllerRef.current;
    if (!map || !isLoaded || !controller) {
      return;
    }

    controller.ensureAttached(map, COASTLINE_LAYER_ID);
    const gridPaintSettings = getOverlayPaintSettings(variable, basemapMode);
    controller.update({
      active: Boolean(gridActive && gridManifest && gridFrameUrl),
      manifest: gridManifest,
      frameUrl: gridFrameUrl,
      frameHour: gridFrameHour,
      legend: gridLegend,
      opacity,
      overlayFadeOutZoom,
      selectionEpoch,
      selectionKey,
      prefetchUrls: gridPrefetchUrls,
      rasterPaint: gridPaintSettings,
      onFrameVisible: onGridFrameVisible,
      onFrameReady: onGridFrameReady,
    });

    const shouldShowGrid = Boolean(gridActive && gridManifest && gridFrameUrl);
    const activeBuffer = activeBufferRef.current;
    const inactiveBuffer = otherBuffer(activeBuffer);
    const shouldShowLoop = Boolean((loopActive || isLoopToTileTransitioningRef.current) && hasLoopVisual);

    if (shouldShowGrid) {
      setLayerVisibility(map, layerId("a"), false);
      setLayerVisibility(map, layerId("b"), false);
      setLayerOpacity(map, layerId("a"), HIDDEN_SWAP_BUFFER_OPACITY);
      setLayerOpacity(map, layerId("b"), HIDDEN_SWAP_BUFFER_OPACITY);
      for (let idx = 1; idx <= PREFETCH_BUFFER_COUNT; idx += 1) {
        setLayerVisibility(map, prefetchLayerId(idx), false);
        setLayerOpacity(map, prefetchLayerId(idx), HIDDEN_PREFETCH_OPACITY);
      }
      setLayerVisibility(map, LOOP_LAYER_ID, false);
      setLayerVisibility(map, LOOP_CANVAS_LAYER_ID, false);
    } else {
      setLayerVisibility(map, layerId(activeBuffer), !loopActive);
      setLayerOpacity(map, layerId(activeBuffer), opacity);
      if (inactiveBuffer !== activeBuffer) {
        setLayerVisibility(map, layerId(inactiveBuffer), false);
        setLayerOpacity(map, layerId(inactiveBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
      }
      setLayerVisibility(map, LOOP_LAYER_ID, shouldShowLoop && !hasCanvasLoopFrame);
      setLayerVisibility(map, LOOP_CANVAS_LAYER_ID, shouldShowLoop && hasCanvasLoopFrame);
      setLayerOpacity(map, LOOP_LAYER_ID, opacity);
      setLayerOpacity(map, LOOP_CANVAS_LAYER_ID, opacity);
    }

    enforceLayerOrder(map);
  }, [
    basemapMode,
    enforceLayerOrder,
    gridActive,
    gridFrameHour,
    gridFrameUrl,
    gridLegend,
    gridManifest,
    gridPrefetchUrls,
    hasCanvasLoopFrame,
    hasLoopVisual,
    isLoaded,
    loopActive,
    onGridFrameVisible,
    onGridFrameReady,
    opacity,
    overlayFadeOutZoom,
    selectionEpoch,
    selectionKey,
    setLayerOpacity,
    variable,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }

    const lastHintStateRef = { current: false };
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
      if (!onZoomRoutingSignal) {
        return;
      }
      if (rafId !== null) {
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
      const zoom = map.getZoom();
      const bucket = Math.max(0, Math.floor(zoom));
      console.debug("[map] zoom", { zoom: Number(zoom.toFixed(2)), bucket });
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
        rafId = null;
      }
    };
  }, [isLoaded, onZoomBucketChange, onZoomRoutingSignal]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded || !loopActive) {
      return;
    }
    const selectionScope = { selectionEpoch, selectionKey };

    // Loop playback is fully canvas-backed. Report frame readiness for the
    // current selection without mutating tile sources.
    onFrameLoadingChange?.(tileUrl, false, selectionScope);
    onTileReady?.(tileUrl, { source: "loop-warm", ...selectionScope });
    onFrameSettled?.(tileUrl, selectionScope);
    onTileViewportReady?.(tileUrl, selectionScope);
  }, [
    isLoaded,
    loopActive,
    tileUrl,
    onTileReady,
    onFrameSettled,
    onTileViewportReady,
    onFrameLoadingChange,
    selectionEpoch,
    selectionKey,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    const selectionScope = { selectionEpoch, selectionKey };

    // Foreground tile swap work is disabled while loop mode is active.
    // A separate warm-path effect keeps the active tile buffer up to date
    // at tiny opacity to avoid flashes during WebP -> tile handoff.
    if (loopActive) {
      onFrameLoadingChange?.(tileUrl, false, selectionScope);
      return;
    }
    let settledCleanup: (() => void) | undefined;

    if (tileUrl === activeTileUrlRef.current) {
      const source = sourceId(activeBufferRef.current);
      setLayerVisibility(map, layerId(activeBufferRef.current), true);
      const inactive = otherBuffer(activeBufferRef.current);
      if (inactive !== activeBufferRef.current) {
        setLayerVisibility(map, layerId(inactive), false);
      }
      onFrameLoadingChange?.(tileUrl, false, selectionScope);
      const readyCleanup = waitForSourceReady(
        map,
        source,
        tileUrl,
        sourceRequestTokenRef.current.get(source) ?? 0,
        -1,
        mode,
        () => {
          settledCleanup = notifySettled(map, source, tileUrl, "active", selectionScope);
        },
        () => {
          onFrameLoadingChange?.(tileUrl, true, selectionScope);
          console.warn("[map] ready timeout", { sourceId: source, tileUrl, mode });
        }
      );
      return () => {
        readyCleanup?.();
        settledCleanup?.();
      };
    }

    const inactiveBuffer = otherBuffer(activeBufferRef.current);
    setLayerVisibility(map, layerId(inactiveBuffer), true);
    const inactiveSource = map.getSource(sourceId(inactiveBuffer)) as
      | maplibregl.RasterTileSource
      | undefined;
    if (!inactiveSource || typeof inactiveSource.setTiles !== "function") {
      return;
    }

    const inactiveSourceId = sourceId(inactiveBuffer);
    onFrameLoadingChange?.(tileUrl, true, selectionScope);
    if (
      !setTilesSafe(inactiveSource, [tileUrl], {
        sourceId: inactiveSourceId,
        tileUrl,
        mode: mode,
      })
    ) {
      onFrameLoadingChange?.(tileUrl, false, selectionScope);
      return;
    }
    sourceRequestedUrlRef.current.set(inactiveSourceId, tileUrl);
    const nextSwapRequestToken = (sourceRequestTokenRef.current.get(inactiveSourceId) ?? 0) + 1;
    sourceRequestTokenRef.current.set(inactiveSourceId, nextSwapRequestToken);
    const swapSourceEventBaseline = sourceEventCountRef.current.get(inactiveSourceId) ?? 0;
    const token = ++swapTokenRef.current;

    const finishSwap = (skipSettleNotify = false) => {
      if (token !== swapTokenRef.current) {
        return;
      }

      const previousActive = activeBufferRef.current;
      activeBufferRef.current = inactiveBuffer;
      activeTileUrlRef.current = tileUrl;
      setLayerVisibility(map, layerId(previousActive), true);
      setLayerVisibility(map, layerId(inactiveBuffer), true);

      if (mode === "scrub") {
        cancelCrossfade();
        // Anti-flash scrub swap: keep previous frame visible for extra paint ticks
        // while the next frame is promoted to full opacity, then hide previous.
        // This avoids a brief basemap-white flash between frames.
        setLayerOpacity(map, layerId(previousActive), opacity);
        setLayerOpacity(map, layerId(inactiveBuffer), opacity);
        if (previousActive !== inactiveBuffer) {
          window.requestAnimationFrame(() => {
            window.requestAnimationFrame(() => {
              if (token !== swapTokenRef.current) {
                return;
              }
              setLayerOpacity(map, layerId(previousActive), HIDDEN_SWAP_BUFFER_OPACITY);
            });
          });
        }
      } else if (crossfade) {
        runCrossfade(map, previousActive, inactiveBuffer, opacity);
      } else {
        cancelCrossfade();
        // Use micro-crossfade for smooth transition without noticeable flash
        runMicroCrossfade(map, previousActive, inactiveBuffer, opacity, token);
      }
      onFrameLoadingChange?.(tileUrl, false, selectionScope);
      if (!skipSettleNotify) {
        settledCleanup = notifySettled(map, sourceId(inactiveBuffer), tileUrl, "swap", selectionScope);
      }

      // After promotion, keep only the active buffer visible so MapLibre stops
      // maintaining/reloading stale tiles on the inactive source.
      if (previousActive !== inactiveBuffer) {
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => {
            if (token !== swapTokenRef.current) {
              return;
            }
            setLayerVisibility(map, layerId(previousActive), false);
            setLayerVisibility(map, layerId(inactiveBuffer), true);
          });
        });
      }
    };

    const readyCleanup = waitForSourceReady(map, inactiveSourceId, tileUrl, nextSwapRequestToken, swapSourceEventBaseline, mode, finishSwap, () => {
      if (token !== swapTokenRef.current) {
        return;
      }
      onFrameLoadingChange?.(tileUrl, true, selectionScope);
      console.warn("[map] swap timeout", { sourceId: inactiveSourceId, tileUrl, token, mode });
    });

    return () => {
      readyCleanup?.();
      settledCleanup?.();
    };
  }, [
    tileUrl,
    isLoaded,
    loopActive,
    mode,
    opacity,
    crossfade,
    waitForSourceReady,
    setTilesSafe,
    runCrossfade,
    cancelCrossfade,
    setLayerOpacity,
    notifySettled,
    onTileReady,
    onFrameSettled,
    onFrameLoadingChange,
    selectionEpoch,
    selectionKey,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    const selectionScope = { selectionEpoch, selectionKey };

    const token = ++prefetchTokenRef.current;
    const urls = Array.from({ length: PREFETCH_BUFFER_COUNT }, (_, idx) => prefetchTileUrls[idx] ?? "");
    const cleanups: Array<() => void> = [];

    urls.forEach((url, idx) => {
      const source = map.getSource(prefetchSourceId(idx + 1)) as maplibregl.RasterTileSource | undefined;
      if (!source || typeof source.setTiles !== "function") {
        return;
      }

      if (!url) {
        prefetchUrlsRef.current[idx] = "";
        setLayerOpacity(map, prefetchLayerId(idx + 1), HIDDEN_PREFETCH_OPACITY);
        setLayerVisibility(map, prefetchLayerId(idx + 1), false);
        return;
      }

      if (prefetchUrlsRef.current[idx] === url) {
        return;
      }

      prefetchUrlsRef.current[idx] = url;
      // Show the layer so MapLibre actually requests the tiles (visibility:none skips them).
      setLayerVisibility(map, prefetchLayerId(idx + 1), true);
      setLayerOpacity(map, prefetchLayerId(idx + 1), WARM_PREFETCH_OPACITY);
      if (
        !setTilesSafe(source, [url], {
          sourceId: prefetchSourceId(idx + 1),
          tileUrl: url,
          mode: "prefetch",
        })
      ) {
        setLayerOpacity(map, prefetchLayerId(idx + 1), HIDDEN_PREFETCH_OPACITY);
        setLayerVisibility(map, prefetchLayerId(idx + 1), false);
        return;
      }
      const prefetchSource = prefetchSourceId(idx + 1);
      sourceRequestedUrlRef.current.set(prefetchSource, url);
      const nextPrefetchRequestToken = (sourceRequestTokenRef.current.get(prefetchSource) ?? 0) + 1;
      sourceRequestTokenRef.current.set(prefetchSource, nextPrefetchRequestToken);
      const prefetchEventBaseline = sourceEventCountRef.current.get(prefetchSource) ?? 0;
      const prefetchEventBudgetThreshold = prefetchEventBaseline + PREFETCH_TILE_EVENT_BUDGET - 1;

      const cleanup = waitForSourceReady(
        map,
        prefetchSource,
        url,
        nextPrefetchRequestToken,
        prefetchEventBudgetThreshold,
        "autoplay",
        () => {
          if (token !== prefetchTokenRef.current) {
            return;
          }
          if (prefetchUrlsRef.current[idx] !== url) {
            return;
          }
          // Important: App.tsx autoplay waits on URLs being marked ready.
          // Prefetch sources should contribute to that readiness cache.
          onTileReady?.(url, { source: "prefetch", ...selectionScope });
          // Tiles are now in the browser cache — hide the layer so MapLibre stops
          // issuing new requests when the viewport changes.
          setLayerOpacity(map, prefetchLayerId(idx + 1), HIDDEN_PREFETCH_OPACITY);
          setLayerVisibility(map, prefetchLayerId(idx + 1), false);
        },
        () => {
          if (token !== prefetchTokenRef.current) {
            return;
          }
          if (prefetchUrlsRef.current[idx] !== url) {
            return;
          }
          // Best-effort: don't let autoplay deadlock if MapLibre never reports
          // the prefetch source as fully loaded within the timeout window.
          console.warn("[map] prefetch ready fallback timeout", {
            sourceId: prefetchSourceId(idx + 1),
            tileUrl: url,
            token,
          });
          setLayerOpacity(map, prefetchLayerId(idx + 1), HIDDEN_PREFETCH_OPACITY);
          setLayerVisibility(map, prefetchLayerId(idx + 1), false);
        },
        PREFETCH_READY_TIMEOUT_MS
      );

      if (cleanup) {
        cleanups.push(cleanup);
      }
    });

    return () => {
      cleanups.forEach((cleanup) => cleanup());
    };
  }, [prefetchTileUrls, isLoaded, waitForSourceReady, setTilesSafe, onTileReady, selectionEpoch, selectionKey]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }

    if (gridActive) {
      cancelPendingLoopImageUpdate();
      setReadyLoopImageFrame(null);
      setReadyLoopCanvasFrame(null);
    } else if (loopFrameBitmap) {
      cancelPendingLoopImageUpdate();
    } else {
      queueLoopImageUpdate(map, loopImageUrl, loopImageCoordinates, { selectionEpoch, selectionKey });
    }
    const loopCanvasSource = map.getSource(LOOP_CANVAS_SOURCE_ID) as maplibregl.CanvasSource | undefined;
    if (loopCanvasSource && typeof loopCanvasSource.setCoordinates === "function") {
      loopCanvasSource.setCoordinates(loopImageCoordinates);
    }

    const shouldShowLoop = Boolean((loopActive || isLoopToTileTransitioningRef.current) && hasLoopVisual);
    setLayerVisibility(map, LOOP_LAYER_ID, shouldShowLoop && !hasCanvasLoopFrame);
    setLayerVisibility(map, LOOP_CANVAS_LAYER_ID, shouldShowLoop && hasCanvasLoopFrame);
    setLayerVisibility(
      map,
      CONTOUR_LAYER_ID,
      false
    );
    enforceLayerOrder(map);
  }, [
    isLoaded,
    loopImageCoordinates,
    loopImageUrl,
    loopFrameBitmap,
    loopActive,
    gridActive,
    variable,
    hasCanvasLoopFrame,
    hasLoopVisual,
    queueLoopImageUpdate,
    enforceLayerOrder,
    selectionEpoch,
    selectionKey,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }

    const wasLoopActive = previousLoopActiveRef.current;
    previousLoopActiveRef.current = loopActive;

    const activeBuffer = activeBufferRef.current;
    const inactiveBuffer = otherBuffer(activeBuffer);
    const targetOpacity = Math.max(0, Math.min(1, opacity));

    if (!crossfade) {
      cancelCrossfade();
    }

    if (gridActive) {
      isLoopToTileTransitioningRef.current = false;
      cancelLoopToTileTransition();
      setLayerVisibility(map, layerId(activeBuffer), false);
      setLayerVisibility(map, layerId(inactiveBuffer), false);
      setLayerOpacity(map, layerId(activeBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
      setLayerOpacity(map, layerId(inactiveBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
      setLayerVisibility(map, LOOP_LAYER_ID, false);
      setLayerVisibility(map, LOOP_CANVAS_LAYER_ID, false);
      setLayerOpacity(map, LOOP_LAYER_ID, targetOpacity);
      setLayerOpacity(map, LOOP_CANVAS_LAYER_ID, targetOpacity);
      setLayerVisibility(map, CONTOUR_LAYER_ID, false);
      for (let idx = 1; idx <= PREFETCH_BUFFER_COUNT; idx += 1) {
        setLayerOpacity(map, prefetchLayerId(idx), HIDDEN_PREFETCH_OPACITY);
        setLayerVisibility(map, prefetchLayerId(idx), false);
      }
      return;
    }

    // If a loop→tile crossfade is already in progress and we're not going
    // back into loop mode, let it finish rather than canceling it.  Canceling
    // here would snap tiles to full opacity before they've actually loaded,
    // causing a transparent flash.  The transition will handle cleanup once
    // the crossfade completes.
    if (loopActive || !isLoopToTileTransitioningRef.current) {
      cancelLoopToTileTransition();
    }

    if (isLoopToTileTransitioningRef.current && !loopActive) {
      // Transition is still in progress — only update the loop-canvas layer
      // visibility in case the bitmap source changed, but don't touch tile
      // opacity or restart the transition.
      const showLoop = hasLoopVisual;
      setLayerVisibility(map, LOOP_LAYER_ID, showLoop && !hasCanvasLoopFrame);
      setLayerVisibility(map, LOOP_CANVAS_LAYER_ID, showLoop && hasCanvasLoopFrame);
      return;
    }

    if (loopActive) {
      isLoopToTileTransitioningRef.current = false;
      setLayerVisibility(map, layerId(activeBuffer), false);
      setLayerVisibility(map, layerId(inactiveBuffer), false);
      setLayerOpacity(map, layerId(activeBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
      setLayerOpacity(map, layerId(inactiveBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
      setLayerVisibility(map, LOOP_LAYER_ID, Boolean(hasLoopVisual && !hasCanvasLoopFrame));
      setLayerVisibility(map, LOOP_CANVAS_LAYER_ID, Boolean(hasLoopVisual && hasCanvasLoopFrame));
      setLayerOpacity(map, LOOP_LAYER_ID, targetOpacity);
      setLayerOpacity(map, LOOP_CANVAS_LAYER_ID, targetOpacity);
      setLayerVisibility(map, CONTOUR_LAYER_ID, false);
    } else if (wasLoopActive && hasLoopVisual) {
      isLoopToTileTransitioningRef.current = true;
      const transitionToken = ++loopToTileTokenRef.current;
      setLayerVisibility(map, layerId(activeBuffer), true);
      if (inactiveBuffer !== activeBuffer) {
        setLayerVisibility(map, layerId(inactiveBuffer), false);
      }
      setLayerOpacity(map, layerId(activeBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
      if (inactiveBuffer !== activeBuffer) {
        setLayerOpacity(map, layerId(inactiveBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
      }
      setLayerVisibility(map, LOOP_LAYER_ID, !hasCanvasLoopFrame);
      setLayerVisibility(map, LOOP_CANVAS_LAYER_ID, hasCanvasLoopFrame);
      setLayerOpacity(map, LOOP_LAYER_ID, targetOpacity);
      setLayerOpacity(map, LOOP_CANVAS_LAYER_ID, targetOpacity);

      const startCrossfade = () => {
        if (transitionToken !== loopToTileTokenRef.current) {
          return;
        }
        if (loopToTileForceTimerRef.current !== null) {
          window.clearTimeout(loopToTileForceTimerRef.current);
          loopToTileForceTimerRef.current = null;
        }
        if (loopToTileIdleCleanupRef.current) {
          loopToTileIdleCleanupRef.current();
          loopToTileIdleCleanupRef.current = null;
        }
        const startedAt = performance.now();
        const tick = (now: number) => {
          if (transitionToken !== loopToTileTokenRef.current) {
            return;
          }
          const progress = Math.min(1, (now - startedAt) / WEBP_TO_TILE_CROSSFADE_MS);
          const tileOpacity = HIDDEN_SWAP_BUFFER_OPACITY + (targetOpacity - HIDDEN_SWAP_BUFFER_OPACITY) * progress;
          const loopOpacity = targetOpacity * (1 - progress);
          setLayerOpacity(map, layerId(activeBuffer), tileOpacity);
          setLayerOpacity(map, LOOP_LAYER_ID, loopOpacity);
          setLayerOpacity(map, LOOP_CANVAS_LAYER_ID, loopOpacity);

          if (progress < 1) {
            loopToTileRafRef.current = window.requestAnimationFrame(tick);
            return;
          }

          setLayerOpacity(map, layerId(activeBuffer), targetOpacity);
          setLayerOpacity(map, LOOP_LAYER_ID, targetOpacity);
          setLayerOpacity(map, LOOP_CANVAS_LAYER_ID, targetOpacity);
          setLayerVisibility(map, LOOP_LAYER_ID, false);
          setLayerVisibility(map, LOOP_CANVAS_LAYER_ID, false);
          setLayerVisibility(map, CONTOUR_LAYER_ID, false);
          isLoopToTileTransitioningRef.current = false;
          loopToTileRafRef.current = null;
          if (loopToTileForceTimerRef.current !== null) {
            window.clearTimeout(loopToTileForceTimerRef.current);
            loopToTileForceTimerRef.current = null;
          }
        };

        loopToTileRafRef.current = window.requestAnimationFrame(tick);
      };

      const onIdle = () => {
        if (transitionToken !== loopToTileTokenRef.current) {
          return;
        }
        if (loopToTileStableTimerRef.current !== null) {
          window.clearTimeout(loopToTileStableTimerRef.current);
        }
        loopToTileStableTimerRef.current = window.setTimeout(() => {
          loopToTileStableTimerRef.current = null;
          startCrossfade();
        }, WEBP_TO_TILE_STABLE_MS);
      };

      loopToTileForceTimerRef.current = window.setTimeout(() => {
        if (transitionToken !== loopToTileTokenRef.current) {
          return;
        }
        startCrossfade();
      }, WEBP_TO_TILE_FORCE_CROSSFADE_MS);

      if (mode === "variable-switch") {
        startCrossfade();
      } else {
        map.on("idle", onIdle);
        loopToTileIdleCleanupRef.current = () => {
          map.off("idle", onIdle);
        };
        window.requestAnimationFrame(() => {
          if (map.areTilesLoaded()) {
            onIdle();
          }
        });
      }
    } else {
      isLoopToTileTransitioningRef.current = false;
      setLayerVisibility(map, layerId(activeBuffer), true);
      setLayerOpacity(map, layerId(activeBuffer), targetOpacity);
      if (inactiveBuffer !== activeBuffer) {
        setLayerOpacity(map, layerId(inactiveBuffer), HIDDEN_SWAP_BUFFER_OPACITY);
        setLayerVisibility(map, layerId(inactiveBuffer), false);
      }
      setLayerVisibility(map, LOOP_LAYER_ID, false);
      setLayerVisibility(map, LOOP_CANVAS_LAYER_ID, false);
      setLayerOpacity(map, LOOP_LAYER_ID, targetOpacity);
      setLayerOpacity(map, LOOP_CANVAS_LAYER_ID, targetOpacity);
      setLayerVisibility(map, CONTOUR_LAYER_ID, false);
    }
    for (let idx = 1; idx <= PREFETCH_BUFFER_COUNT; idx += 1) {
      setLayerOpacity(map, prefetchLayerId(idx), HIDDEN_PREFETCH_OPACITY);
    }
  }, [
    opacity,
    isLoaded,
    mode,
    crossfade,
    cancelCrossfade,
    cancelLoopToTileTransition,
    gridActive,
    setLayerOpacity,
    loopActive,
    hasCanvasLoopFrame,
    hasLoopVisual,
    loopImageUrl,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    const selectionScope = { selectionEpoch, selectionKey };

    if (loopActive) {
      window.requestAnimationFrame(() => {
        onTileViewportReady?.(tileUrl, selectionScope);
      });
      return;
    }

    const token = ++tileViewportReadyTokenRef.current;
    const activeSource = sourceId(activeBufferRef.current);
    const expectedTileUrl = tileUrl;

    const maybeNotify = () => {
      if (token !== tileViewportReadyTokenRef.current) {
        return;
      }
      if (activeTileUrlRef.current !== expectedTileUrl) {
        return;
      }
      if (!map.isSourceLoaded(activeSource)) {
        return;
      }
      onTileViewportReady?.(expectedTileUrl, selectionScope);
    };

    map.on("idle", maybeNotify);
    window.requestAnimationFrame(() => maybeNotify());

    return () => {
      map.off("idle", maybeNotify);
    };
  }, [isLoaded, tileUrl, onTileViewportReady, selectionEpoch, selectionKey, loopActive]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }

    setLayerRasterPaint(map, layerId("a"), variable, variableKind, displayResamplingOverride, basemapMode);
    setLayerRasterPaint(map, layerId("b"), variable, variableKind, displayResamplingOverride, basemapMode);
    for (let idx = 1; idx <= PREFETCH_BUFFER_COUNT; idx += 1) {
      setLayerRasterPaint(map, prefetchLayerId(idx), variable, variableKind, displayResamplingOverride, basemapMode);
    }
    setLayerRasterPaint(map, LOOP_LAYER_ID, variable, variableKind, displayResamplingOverride, basemapMode);
    setLayerRasterPaint(map, LOOP_CANVAS_LAYER_ID, variable, variableKind, displayResamplingOverride, basemapMode);
  }, [isLoaded, variable, variableKind, displayResamplingOverride, basemapMode, setLayerRasterPaint]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    if (view.bbox) {
      const [west, south, east, north] = view.bbox;
      map.fitBounds([[west, south], [east, north]], { duration: 600, padding: 24 });
    } else {
      map.easeTo({ center: view.center, zoom: view.zoom, duration: 600 });
    }
  }, [view, isLoaded]);

  // ── Hover events for sample tooltip ──────────────────────────────────
  const onMapHoverRef = useRef(onMapHover);
  onMapHoverRef.current = onMapHover;
  const onMapHoverEndRef = useRef(onMapHoverEnd);
  onMapHoverEndRef.current = onMapHoverEnd;

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) return;
    const canvas = map.getCanvas();
    canvas.style.cursor = "";

    const handleMove = (e: maplibregl.MapMouseEvent) => {
      if (isHoveringAnchorRef.current) {
        return;
      }
      const { lng, lat } = e.lngLat;
      const { x, y } = e.point;
      canvas.style.cursor = onMapHoverRef.current ? "crosshair" : "";
      onMapHoverRef.current?.(lat, lng, x, y);
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
      <canvas
        id={LOOP_CANVAS_ELEMENT_ID}
        ref={loopCanvasRef}
        className="pointer-events-none absolute -left-[9999px] -top-[9999px]"
        width={1}
        height={1}
        aria-hidden="true"
      />

      <div
        ref={mapContainerRef}
        className="absolute inset-0"
        style={{ backgroundColor: getMapBackgroundColor(basemapMode) }}
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

      <div
        className={`pointer-events-none fixed left-4 top-[calc(4.35rem+4.5rem)] z-50 hidden sm:block${showZoomControls ? "" : " sm:hidden"}`}
      >
        <div className="pointer-events-auto overflow-hidden rounded-xl border border-white/10 bg-black/40 shadow-[0_8px_32px_rgba(0,0,0,0.35)] backdrop-blur-md">
          <button
            type="button"
            className="flex h-[34px] w-[34px] items-center justify-center text-lg font-semibold text-white/95 transition-colors hover:bg-white/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            onClick={handleZoomIn}
            aria-label="Zoom in"
            title="Zoom in"
          >
            +
          </button>
          <button
            type="button"
            className="flex h-[34px] w-[34px] items-center justify-center border-t border-white/10 text-xl font-semibold text-white/95 transition-colors hover:bg-white/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            onClick={handleZoomOut}
            aria-label="Zoom out"
            title="Zoom out"
          >
            -
          </button>
        </div>
      </div>

    </>
  );
}
