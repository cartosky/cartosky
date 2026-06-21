import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Palette } from "lucide-react";
import "maplibre-gl/dist/maplibre-gl.css";
import maplibregl, { type LayerSpecification, type StyleSpecification } from "maplibre-gl";
import type { GeoJSON } from "geojson";

import type { LegendPayload } from "@/components/map-legend";
import { type AnchorBatchPoint, type AnchorFeatureCollection } from "@/lib/anchor-labels";
import {
  CITIES_STATIC_SOURCE_ID,
  CITY_LABEL_CANDIDATES_LAYER_ID,
  CITY_VALUE_LABELS_LAYER_ID,
  clearCityValueLabels,
  initCityLayers,
  moveCityLabelLayersToTop,
  queryVisibleCityPoints,
  updateCityValueLabels,
  type CityLabelPoint,
} from "@/lib/city-labels";
import { productFetch, type GridManifestResponse, type PressureCenter } from "@/lib/api";
import { API_ORIGIN, MAP_VIEW_DEFAULTS, TILES_BASE } from "@/lib/config";
import {
  SCRUB_FAR_END_FORWARD_FH,
  SCRUB_FAR_END_FORWARD_FH_MOBILE,
  SCRUB_LAG_BURST_PREFETCH_BUDGET,
  SCRUB_LAG_BURST_PREFETCH_BUDGET_MOBILE,
  SCRUB_LONG_TIMELINE_FRAMES,
  SCRUB_LONG_TIMELINE_FRAMES_MOBILE,
} from "@/lib/app-utils";
import { GRID_WEBGL_LAYER_ID, GridWebglLayerController, type GridContourLayerConfig, type GridFrameVisiblePayload } from "@/lib/grid-webgl";
import { startNetworkTimer, trackNetworkFetchDuration } from "@/lib/network-diagnostics";
import type { SampleTooltipState } from "@/lib/use-sample-tooltip";

const IS_HIDPI = typeof window !== "undefined" && window.devicePixelRatio > 1;
const CARTO_TILE_SUFFIX = IS_HIDPI ? "@2x" : "";
const CARTO_TILE_SIZE = 256;

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
  fitMinZoom?: number;
  fitMinZoomBreakpoint?: number;
  minZoom?: number;
  maxZoom?: number;
};

export type BasemapMode = "light" | "dark";

type PlaybackMode = "autoplay" | "scrub" | "variable-switch" | "idle-warmup";

type AnimatedGridPlaybackState = {
  frameUrl: string | null;
  frameHour: number | null;
  prefetchPivotHour: number | null;
  compositeGridLayers: Array<{
    id: string;
    manifest: GridManifestResponse | null;
    frameUrl: string | null;
    frameHour: number | null;
    legend: LegendPayload | null;
    prefetchUrls?: string[];
  }>;
};

export type VectorHazardSelection = {
  x: number;
  y: number;
  title: string;
  areaLabel: string | null;
  riskLabel: string | null;
  hoverLabel: string | null;
  fillColor: string | null;
  expiresTime: string | null;
  alertIds: string[];
  activeHazards: string[];
};

/** Total prefetch budget for forecast scrub (ahead + behind). */
const FORECAST_SCRUB_PREFETCH_BUDGET = 14;
/** Minimum behind-direction slots during forecast scrub. */
const FORECAST_SCRUB_MIN_BEHIND = 1;
/** Minimum ahead-direction slots during forecast scrub. */
const FORECAST_SCRUB_MIN_AHEAD = 2;
const OBSERVED_MOBILE_AUTOPLAY_PREFETCH_AHEAD = 4;
const OBSERVED_MOBILE_AUTOPLAY_PREFETCH_BEHIND = 1;
const OBSERVED_MOBILE_SCRUB_PREFETCH_BUDGET = 6;
const OBSERVED_MOBILE_SCRUB_MIN_AHEAD = 2;
const OBSERVED_MOBILE_SCRUB_MIN_BEHIND = 2;
const OBSERVED_DESKTOP_SCRUB_PREFETCH_BUDGET = 12;
const OBSERVED_DESKTOP_SCRUB_MIN_AHEAD = 3;
const OBSERVED_DESKTOP_SCRUB_MIN_BEHIND = 2;
const CONTOUR_CACHE_MAX_ENTRIES = 96;
const CONTOUR_PREFETCH_CONCURRENCY_DESKTOP = 8;
const CONTOUR_PREFETCH_CONCURRENCY_MOBILE = 1;
const CONTOUR_PREFETCH_MOBILE_LIMIT = 8;
const CONTOUR_PREFETCH_MOBILE_YIELD_MS = 24;

const CONTOUR_SOURCE_ID = "twf-contours";
const CONTOUR_LAYER_ID = "twf-contours";
const VECTOR_SOURCE_IDS = ["twf-vectors-a", "twf-vectors-b"] as const;
const VECTOR_FILL_LAYER_IDS = ["twf-vectors-fill-a", "twf-vectors-fill-b"] as const;
const VECTOR_HALO_LINE_LAYER_IDS = ["twf-vectors-halo-a", "twf-vectors-halo-b"] as const;
const VECTOR_LINE_LAYER_IDS = ["twf-vectors-line-a", "twf-vectors-line-b"] as const;
const RASTER_RGB_SOURCE_IDS = ["raster-rgb-a", "raster-rgb-b"] as const;
const RASTER_RGB_LAYER_IDS = ["raster-rgb-layer-a", "raster-rgb-layer-b"] as const;
const VECTOR_HALO_LINE_COLOR = "#000000";
const VECTOR_HALO_LINE_BASE_OPACITY = 0.6;
const VECTOR_HALO_LINE_WIDTH_OFFSET = 2;
const VECTOR_TRANSITION_MS = 180;
const STATE_BOUNDARY_SOURCE_ID = "twf-boundaries";
const COASTLINE_LAYER_ID = "twf-coastline";
const STATE_BOUNDARY_LAYER_ID = "twf-state-boundaries";
const COUNTRY_BOUNDARY_LAYER_ID = "twf-country-boundaries";
const COUNTY_BOUNDARY_LAYER_ID = "twf-county-boundaries";
const LAKE_MASK_LAYER_ID = "twf-lake-mask";
const LAKE_SHORELINE_LAYER_ID = "twf-lake-shoreline";
const CONTOUR_LINE_COLOR = "#000000";
const CONTOUR_LABEL_COLOR = "rgba(0,0,0,0.72)";
const CONTOUR_LABEL_SHADOW = "0 1px 2px rgba(255,255,255,0.62)";
const TRANSPARENT_PIXEL_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mP8z8AARQMBgAEAtwH9WwAAAABJRU5ErkJggg==";

// CONUS GOES-East grid extent converted from EPSG:3857 meters:
// [-14920000.0, 2752000.0, -6676000.0, 7364000.0]
const RASTER_RGB_BBOX_LNGLAT: [number, number, number, number] = [
  -134.02864,
  23.988444,
  -59.971528,
  55.010993,
];

const RASTER_RGB_COORDINATES: [[number, number], [number, number], [number, number], [number, number]] = [
  [RASTER_RGB_BBOX_LNGLAT[0], RASTER_RGB_BBOX_LNGLAT[3]],
  [RASTER_RGB_BBOX_LNGLAT[2], RASTER_RGB_BBOX_LNGLAT[3]],
  [RASTER_RGB_BBOX_LNGLAT[2], RASTER_RGB_BBOX_LNGLAT[1]],
  [RASTER_RGB_BBOX_LNGLAT[0], RASTER_RGB_BBOX_LNGLAT[1]],
];

const RASTER_RGB_LAYER_PAINT = {
  "raster-opacity": 0,
  "raster-fade-duration": 0,
  "raster-opacity-transition": { duration: 0, delay: 0 },
} as const;

const EMPTY_FEATURE_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

const RASTER_RGB_PRELOAD_CACHE_LIMIT = 192;
const rasterRgbImagePreloads = new Map<string, Promise<void>>();
const rasterRgbLoadedUrls = new Set<string>();
const rasterRgbLoadedUrlOrder: string[] = [];

function rememberRasterRgbLoadedUrl(url: string): void {
  const normalized = String(url ?? "").trim();
  if (!normalized) {
    return;
  }
  const existingIndex = rasterRgbLoadedUrlOrder.indexOf(normalized);
  if (existingIndex >= 0) {
    rasterRgbLoadedUrlOrder.splice(existingIndex, 1);
  }
  rasterRgbLoadedUrlOrder.push(normalized);
  rasterRgbLoadedUrls.add(normalized);
  while (rasterRgbLoadedUrlOrder.length > RASTER_RGB_PRELOAD_CACHE_LIMIT) {
    const evictedUrl = rasterRgbLoadedUrlOrder.shift();
    if (evictedUrl) {
      rasterRgbLoadedUrls.delete(evictedUrl);
      rasterRgbImagePreloads.delete(evictedUrl);
    }
  }
}

function preloadRasterRgbImage(url: string): Promise<void> {
  const normalized = String(url ?? "").trim();
  if (!normalized) {
    return Promise.resolve();
  }
  if (rasterRgbLoadedUrls.has(normalized)) {
    return Promise.resolve();
  }
  const existing = rasterRgbImagePreloads.get(normalized);
  if (existing) {
    return existing;
  }
  const promise = new Promise<void>((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.decoding = "async";
    img.onload = () => {
      const markReady = () => {
        rememberRasterRgbLoadedUrl(normalized);
        resolve();
      };
      if (typeof img.decode === "function") {
        void img.decode().then(markReady).catch(markReady);
        return;
      }
      markReady();
    };
    img.onerror = () => reject(new Error(`Failed to preload RGB frame: ${normalized}`));
    img.src = normalized;
  }).catch((error) => {
    rasterRgbImagePreloads.delete(normalized);
    throw error;
  });
  rasterRgbImagePreloads.set(normalized, promise);
  return promise;
}

class RasterRgbLayerController {
  private activeBuffer: 0 | 1 = 0;
  private attached = false;
  private currentUrl: string | null = null;
  private bufferUrls: [string | null, string | null] = [null, null];
  private desiredUrl: string | null | undefined = undefined;
  private desiredOpacity = 0;
  private desiredBeforeLayerId = "";
  private desiredGeneration = 0;
  private loadLoopRunning = false;
  private onFrameReady: ((url: string) => void) | null = null;
  private supersessionPoll: ReturnType<typeof setInterval> | null = null;

  private clearSupersessionPoll(): void {
    if (this.supersessionPoll !== null) {
      window.clearInterval(this.supersessionPoll);
      this.supersessionPoll = null;
    }
  }

  private ensureInstantOpacityTransition(map: maplibregl.Map): void {
    for (const layerId of RASTER_RGB_LAYER_IDS) {
      if (map.getLayer(layerId)) {
        map.setPaintProperty(layerId, "raster-opacity-transition", { duration: 0, delay: 0 });
      }
    }
  }

  setOnFrameReady(callback: ((url: string) => void) | null): void {
    this.onFrameReady = callback;
  }

  prefetch(urls: string[]): void {
    for (const url of urls) {
      void preloadRasterRgbImage(url)
        .then(() => {
          this.onFrameReady?.(url);
        })
        .catch(() => undefined);
    }
  }

  ensureAttached(map: maplibregl.Map, beforeLayerId: string): void {
    const hasBothSources = RASTER_RGB_SOURCE_IDS.every((sourceId) => Boolean(map.getSource(sourceId)));
    const hasBothLayers = RASTER_RGB_LAYER_IDS.every((layerId) => Boolean(map.getLayer(layerId)));
    if (this.attached && hasBothSources && hasBothLayers) {
      this.ensureInstantOpacityTransition(map);
      return;
    }

    if (!hasBothSources || !hasBothLayers) {
      this.currentUrl = null;
      this.bufferUrls = [null, null];
    }

    for (let i = 0; i < 2; i += 1) {
      const sourceId = RASTER_RGB_SOURCE_IDS[i];
      const layerId = RASTER_RGB_LAYER_IDS[i];
      if (!map.getSource(sourceId)) {
        map.addSource(sourceId, {
          type: "image",
          url: TRANSPARENT_PIXEL_DATA_URL,
          coordinates: RASTER_RGB_COORDINATES,
        });
      }
      if (!map.getLayer(layerId)) {
        map.addLayer({
          id: layerId,
          type: "raster",
          source: sourceId,
          paint: { ...RASTER_RGB_LAYER_PAINT },
        }, map.getLayer(beforeLayerId) ? beforeLayerId : undefined);
      }
    }
    this.ensureInstantOpacityTransition(map);
    this.attached = true;
  }

  private replaceImageSource(
    map: maplibregl.Map,
    sourceId: string,
    layerId: string,
    beforeLayerId: string,
    url: string,
  ): void {
    const existing = map.getSource(sourceId) as maplibregl.ImageSource | undefined;
    if (existing && typeof existing.updateImage === "function") {
      existing.updateImage({ url, coordinates: RASTER_RGB_COORDINATES });
      return;
    }

    if (map.getLayer(layerId)) {
      map.removeLayer(layerId);
    }
    if (map.getSource(sourceId)) {
      map.removeSource(sourceId);
    }
    map.addSource(sourceId, {
      type: "image",
      url,
      coordinates: RASTER_RGB_COORDINATES,
    });
    map.addLayer({
      id: layerId,
      type: "raster",
      source: sourceId,
      paint: { ...RASTER_RGB_LAYER_PAINT },
    }, map.getLayer(beforeLayerId) ? beforeLayerId : undefined);
  }

  private swapToBuffer(
    map: maplibregl.Map,
    nextBuffer: 0 | 1,
    opacity: number,
  ): void {
    const nextLayerId = RASTER_RGB_LAYER_IDS[nextBuffer];
    const activeLayerId = RASTER_RGB_LAYER_IDS[this.activeBuffer];
    map.setPaintProperty(nextLayerId, "raster-opacity", opacity);
    map.setPaintProperty(activeLayerId, "raster-opacity", 0);
    this.activeBuffer = nextBuffer;
  }

  private waitForSourceLoaded(
    map: maplibregl.Map,
    sourceId: string,
    generation: number,
    url: string,
    wasPreloaded: boolean,
  ): Promise<boolean> {
    return new Promise((resolve) => {
      if (!this.isDesiredGenerationCurrent(generation, url)) {
        resolve(false);
        return;
      }

      let settled = false;
      const finish = (ready: boolean) => {
        if (settled) {
          return;
        }
        settled = true;
        window.clearInterval(cancelPoll);
        window.clearTimeout(timeoutId);
        map.off("sourcedata", onSourceData);
        resolve(ready);
      };

      const finishAfterPaint = () => {
        window.requestAnimationFrame(() => {
          finish(this.isDesiredGenerationCurrent(generation, url));
        });
      };

      const onSourceData = (event: maplibregl.MapSourceDataEvent) => {
        if (event.sourceId !== sourceId || !event.isSourceLoaded) {
          return;
        }
        map.off("sourcedata", onSourceData);
        finishAfterPaint();
      };

      const cancelPoll = window.setInterval(() => {
        if (!this.isDesiredGenerationCurrent(generation, url)) {
          finish(false);
        }
      }, 16);

      const timeoutMs = wasPreloaded ? 750 : 3000;
      const timeoutId = window.setTimeout(() => {
        finish(this.isDesiredGenerationCurrent(generation, url) && map.isSourceLoaded(sourceId));
      }, timeoutMs);

      if (map.isSourceLoaded(sourceId)) {
        finishAfterPaint();
        return;
      }

      map.on("sourcedata", onSourceData);
      map.triggerRepaint();
    });
  }

  private isDesiredGenerationCurrent(generation: number, url: string | null): boolean {
    return generation === this.desiredGeneration && this.desiredUrl === url;
  }

  private waitForSupersession(generation: number, url: string): Promise<void> {
    this.clearSupersessionPoll();
    return new Promise((resolve) => {
      if (!this.isDesiredGenerationCurrent(generation, url)) {
        resolve();
        return;
      }
      this.supersessionPoll = window.setInterval(() => {
        if (!this.isDesiredGenerationCurrent(generation, url)) {
          this.clearSupersessionPoll();
          resolve();
        }
      }, 8);
    });
  }

  private async runLoadLoop(map: maplibregl.Map): Promise<void> {
    if (this.loadLoopRunning) {
      return;
    }
    this.loadLoopRunning = true;
    try {
      while (this.desiredUrl !== undefined && this.desiredUrl !== this.currentUrl) {
        const url = this.desiredUrl;
        const opacity = this.desiredOpacity;
        const beforeLayerId = this.desiredBeforeLayerId;
        const generation = this.desiredGeneration;
        const inactiveBuffer = (1 - this.activeBuffer) as 0 | 1;
        const nextSourceId = RASTER_RGB_SOURCE_IDS[inactiveBuffer];
        const nextLayerId = RASTER_RGB_LAYER_IDS[inactiveBuffer];
        const activeLayerId = RASTER_RGB_LAYER_IDS[this.activeBuffer];

        if (!url) {
          map.setPaintProperty(nextLayerId, "raster-opacity", 0);
          map.setPaintProperty(activeLayerId, "raster-opacity", 0);
          this.activeBuffer = inactiveBuffer;
          this.currentUrl = null;
          this.bufferUrls = [null, null];
          if (this.desiredUrl === url) {
            this.desiredUrl = undefined;
          }
          continue;
        }

        const bufferAlreadyReady = this.bufferUrls[inactiveBuffer] === url && map.isSourceLoaded(nextSourceId);
        if (!bufferAlreadyReady) {
          const wasPreloaded = rasterRgbLoadedUrls.has(url);
          try {
            await Promise.race([
              preloadRasterRgbImage(url),
              this.waitForSupersession(generation, url),
            ]);
          } catch {
            this.clearSupersessionPoll();
            if (this.desiredUrl === url) {
              this.desiredUrl = undefined;
            }
            continue;
          } finally {
            this.clearSupersessionPoll();
          }

          if (!this.isDesiredGenerationCurrent(generation, url)) {
            continue;
          }

          this.replaceImageSource(map, nextSourceId, nextLayerId, beforeLayerId, url);
          this.bufferUrls[inactiveBuffer] = url;
          map.setPaintProperty(activeLayerId, "raster-opacity", this.currentUrl ? opacity : 0);
          map.setPaintProperty(nextLayerId, "raster-opacity", 0);

          const sourceReady = await Promise.race([
            this.waitForSourceLoaded(
              map,
              nextSourceId,
              generation,
              url,
              wasPreloaded || rasterRgbLoadedUrls.has(url),
            ),
            this.waitForSupersession(generation, url).then(() => false),
          ]);
          if (!sourceReady || !this.isDesiredGenerationCurrent(generation, url)) {
            continue;
          }
        }

        this.swapToBuffer(map, inactiveBuffer, opacity);
        this.currentUrl = url;
        if (this.desiredUrl === url) {
          this.desiredUrl = undefined;
        }
        this.onFrameReady?.(url);
      }
    } finally {
      this.clearSupersessionPoll();
      this.loadLoopRunning = false;
      if (this.desiredUrl !== undefined && this.desiredUrl !== this.currentUrl) {
        void this.runLoadLoop(map);
      }
    }
  }

  update(map: maplibregl.Map, url: string | null, opacity: number, beforeLayerId: string): void {
    if (!this.attached) {
      return;
    }
    const inactiveBuffer = (1 - this.activeBuffer) as 0 | 1;
    if (url === this.currentUrl && this.desiredUrl === undefined) {
      map.setPaintProperty(RASTER_RGB_LAYER_IDS[this.activeBuffer], "raster-opacity", url ? opacity : 0);
      map.setPaintProperty(RASTER_RGB_LAYER_IDS[inactiveBuffer], "raster-opacity", 0);
      return;
    }

    if (url && url !== this.currentUrl) {
      const inactiveSourceId = RASTER_RGB_SOURCE_IDS[inactiveBuffer];
      if (this.bufferUrls[inactiveBuffer] === url && map.isSourceLoaded(inactiveSourceId)) {
        this.desiredUrl = undefined;
        this.desiredOpacity = opacity;
        this.desiredBeforeLayerId = beforeLayerId;
        this.swapToBuffer(map, inactiveBuffer, opacity);
        this.currentUrl = url;
        this.onFrameReady?.(url);
        return;
      }
    }

    if (this.desiredUrl !== url) {
      this.desiredGeneration += 1;
    }
    this.desiredUrl = url;
    this.desiredOpacity = opacity;
    this.desiredBeforeLayerId = beforeLayerId;
    void this.runLoadLoop(map);
  }

  remove(map: maplibregl.Map): void {
    if (!this.attached) {
      return;
    }
    this.desiredGeneration += 1;
    this.desiredUrl = undefined;
    this.loadLoopRunning = false;
    this.clearSupersessionPoll();
    this.bufferUrls = [null, null];
    for (let i = 0; i < 2; i += 1) {
      const layerId = RASTER_RGB_LAYER_IDS[i];
      const sourceId = RASTER_RGB_SOURCE_IDS[i];
      if (map.getLayer(layerId)) {
        map.removeLayer(layerId);
      }
      if (map.getSource(sourceId)) {
        map.removeSource(sourceId);
      }
    }
    this.attached = false;
    this.currentUrl = null;
  }
}

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

function readStringArrayProperty(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  if (typeof value !== "string") {
    return [];
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return [];
  }
  try {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) {
      return parsed.map((item) => String(item).trim()).filter(Boolean);
    }
  } catch {
    // MapLibre may surface GeoJSON array properties as comma-delimited strings.
  }
  return trimmed.split(",").map((item) => item.trim()).filter(Boolean);
}

function hazardSelectionFromFeature(
  feature: { properties?: Record<string, unknown> } | undefined,
  point: { x: number; y: number },
): VectorHazardSelection | null {
  const properties = feature?.properties;
  if (!properties) {
    return null;
  }
  const riskLabel = typeof properties.risk_label === "string" ? properties.risk_label.trim() : "";
  const hoverLabel = typeof properties.hover_label === "string" ? properties.hover_label.trim() : "";
  const areaLabel = typeof properties.county_name === "string" && properties.county_name.trim()
    ? properties.county_name.trim()
    : typeof properties.zone_name === "string" && properties.zone_name.trim()
      ? properties.zone_name.trim()
      : typeof properties.area_description === "string" && properties.area_description.trim()
        ? properties.area_description.trim()
        : "";
  const alertIds = readStringArrayProperty(properties.alert_ids);
  const activeHazards = readStringArrayProperty(properties.active_hazards);
  if (!alertIds.length) {
    return null;
  }
  const fillColor = typeof properties.fill === "string" && properties.fill.trim() ? properties.fill.trim() : null;
  const expiresTime = typeof properties.expires_time === "string" && properties.expires_time.trim()
    ? properties.expires_time.trim()
    : null;
  return {
    x: point.x,
    y: point.y,
    title: hoverLabel || [areaLabel, riskLabel || activeHazards[0]].filter(Boolean).join(": ") || "NWS Hazard",
    areaLabel: areaLabel || null,
    riskLabel: riskLabel || activeHazards[0] || null,
    hoverLabel: hoverLabel || null,
    fillColor,
    expiresTime,
    alertIds,
    activeHazards,
  };
}

type GridPaintSettings = {
  contrast: number;
  saturation: number;
  brightnessMin: number;
  brightnessMax: number;
};

type ContourScreenLabel = {
  id: string;
  label: string;
  x: number;
  y: number;
  angle: number;
};

type PressureCenterScreenLabel = {
  id: string;
  type: "H" | "L";
  valueLabel: string;
  x: number;
  y: number;
};

type LngLatPair = [number, number];

type ContourLinePlacement = {
  coord: LngLatPair;
  angle: number;
  distancePx: number;
  totalDistancePx: number;
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

function readContourLineStrings(geometry: GeoJSON.Geometry | null | undefined): LngLatPair[][] {
  if (!geometry) {
    return [];
  }
  if (geometry.type === "LineString") {
    return [geometry.coordinates as LngLatPair[]];
  }
  if (geometry.type === "MultiLineString") {
    return geometry.coordinates as LngLatPair[][];
  }
  return [];
}

function longestLineString(lines: LngLatPair[][]): LngLatPair[] | null {
  let best: LngLatPair[] | null = null;
  let bestLength = 0;
  for (const line of lines) {
    if (!Array.isArray(line) || line.length < 2) {
      continue;
    }
    if (line.length > bestLength) {
      best = line;
      bestLength = line.length;
    }
  }
  return best;
}

function longestLineStringIndex(lines: LngLatPair[][]): number {
  let bestIndex = -1;
  let bestLength = 0;
  lines.forEach((line, index) => {
    if (!Array.isArray(line) || line.length < 2) {
      return;
    }
    if (line.length > bestLength) {
      bestIndex = index;
      bestLength = line.length;
    }
  });
  return bestIndex;
}

function interpolateLngLat(start: LngLatPair, end: LngLatPair, t: number): LngLatPair {
  return [
    Number(start[0]) + (Number(end[0]) - Number(start[0])) * t,
    Number(start[1]) + (Number(end[1]) - Number(start[1])) * t,
  ];
}

function contourLinePlacement(line: LngLatPair[], map: maplibregl.Map): ContourLinePlacement | null {
  if (!Array.isArray(line) || line.length < 2) {
    return null;
  }

  const projected = line.map((coord) => map.project(coord));
  const distances: number[] = [0];
  for (let index = 1; index < projected.length; index += 1) {
    const previous = projected[index - 1];
    const current = projected[index];
    const dx = current.x - previous.x;
    const dy = current.y - previous.y;
    distances.push(distances[index - 1] + Math.hypot(dx, dy));
  }

  const totalDistance = distances[distances.length - 1] ?? 0;
  if (!Number.isFinite(totalDistance) || totalDistance <= 0) {
    return null;
  }

  const targetDistance = totalDistance / 2;
  for (let index = 0; index < line.length - 1; index += 1) {
    const startDistance = distances[index];
    const endDistance = distances[index + 1];
    if (targetDistance < startDistance || targetDistance > endDistance) {
      continue;
    }
    const start = line[index];
    const end = line[index + 1];
    const segmentDistance = endDistance - startDistance;
    const t = segmentDistance > 0 ? (targetDistance - startDistance) / segmentDistance : 0;
    const coord = interpolateLngLat(start, end, Math.max(0, Math.min(1, t)));
    const startPoint = projected[index];
    const endPoint = projected[index + 1];
    let angle = Math.atan2(endPoint.y - startPoint.y, endPoint.x - startPoint.x) * 180 / Math.PI;
    if (angle > 90) angle -= 180;
    if (angle < -90) angle += 180;
    return {
      coord,
      angle,
      distancePx: targetDistance,
      totalDistancePx: totalDistance,
    };
  }

  return null;
}

function splitContourLabelLine(line: LngLatPair[], map: maplibregl.Map): LngLatPair[][] {
  const placement = contourLinePlacement(line, map);
  if (!placement || placement.totalDistancePx < 24) {
    return [line];
  }

  const gapHalfPx = 13;
  const centerDistance = placement.distancePx;
  const projected = line.map((coord) => map.project(coord));
  const distances: number[] = [0];
  for (let index = 1; index < projected.length; index += 1) {
    const previous = projected[index - 1];
    const current = projected[index];
    const dx = current.x - previous.x;
    const dy = current.y - previous.y;
    distances.push(distances[index - 1] + Math.hypot(dx, dy));
  }
  const totalDistance = placement.totalDistancePx;
  const gapStartDistance = Math.max(0, centerDistance - gapHalfPx);
  const gapEndDistance = Math.min(totalDistance, centerDistance + gapHalfPx);

  const before: LngLatPair[] = [];
  const after: LngLatPair[] = [];
  for (let index = 0; index < line.length - 1; index += 1) {
    const startDistance = distances[index];
    const endDistance = distances[index + 1];
    const start = line[index];
    const end = line[index + 1];
    const segmentDistance = endDistance - startDistance;
    if (!Number.isFinite(segmentDistance) || segmentDistance <= 0) {
      continue;
    }

    if (endDistance <= gapStartDistance) {
      if (before.length === 0) {
        before.push(start);
      }
      before.push(end);
      continue;
    }

    if (startDistance >= gapEndDistance) {
      if (after.length === 0) {
        after.push(start);
      }
      after.push(end);
      continue;
    }

    if (startDistance < gapStartDistance && gapStartDistance < endDistance) {
      const t = (gapStartDistance - startDistance) / segmentDistance;
      if (before.length === 0) {
        before.push(start);
      }
      before.push(interpolateLngLat(start, end, t));
    }

    if (startDistance < gapEndDistance && gapEndDistance < endDistance) {
      const t = (gapEndDistance - startDistance) / segmentDistance;
      after.push(interpolateLngLat(start, end, t));
      after.push(end);
    }
  }

  const segments = [before, after].filter((segment) => segment.length >= 2);
  return segments.length > 0 ? segments : [line];
}

function buildContourLineDisplayPayload(payload: GeoJSON.FeatureCollection, map: maplibregl.Map): GeoJSON.FeatureCollection {
  return {
    ...payload,
    features: payload.features.map((feature) => {
      const label = typeof feature.properties?.label === "string" ? feature.properties.label.trim() : "";
      if (!label) {
        return feature;
      }
      const lines = readContourLineStrings(feature.geometry);
      const targetIndex = longestLineStringIndex(lines);
      if (targetIndex < 0) {
        return feature;
      }

      const nextLines = lines.flatMap((line, index) => (
        index === targetIndex ? splitContourLabelLine(line, map) : [line]
      ));
      if (nextLines.length === 0) {
        return feature;
      }

      return {
        ...feature,
        geometry: {
          type: "MultiLineString",
          coordinates: nextLines,
        },
      };
    }),
  };
}

function buildContourScreenLabels(
  payload: GeoJSON.FeatureCollection | null,
  map: maplibregl.Map
): ContourScreenLabel[] {
  if (!payload || !Array.isArray(payload.features)) {
    return [];
  }

  const canvas = map.getCanvas();
  const width = canvas.clientWidth || canvas.width;
  const height = canvas.clientHeight || canvas.height;
  const labels: ContourScreenLabel[] = [];
  const marginPx = 32;

  payload.features.forEach((feature, index) => {
    const label = typeof feature.properties?.label === "string" ? feature.properties.label.trim() : "";
    if (!label) {
      return;
    }
    const line = longestLineString(readContourLineStrings(feature.geometry));
    if (!line || line.length < 2) {
      return;
    }
    const placement = contourLinePlacement(line, map);
    if (!placement) {
      return;
    }
    const lng = Number(placement.coord[0]);
    const lat = Number(placement.coord[1]);
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
      return;
    }
    const point = map.project([lng, lat]);
    if (
      point.x < -marginPx ||
      point.y < -marginPx ||
      point.x > width + marginPx ||
      point.y > height + marginPx
    ) {
      return;
    }
    labels.push({
      id: `${label}-${index}`,
      label,
      x: point.x,
      y: point.y,
      angle: placement.angle,
    });
  });

  return labels;
}

function pressureCenterValueLabel(center: PressureCenter): string {
  const rawValue = center.value;
  const numericValue = typeof rawValue === "number" ? rawValue : Number(rawValue);
  if (!Number.isFinite(numericValue)) {
    return "";
  }
  return Math.abs(numericValue) >= 100
    ? String(Math.round(numericValue))
    : numericValue.toFixed(1).replace(/\.0$/, "");
}

function buildPressureCenterScreenLabels(
  centers: PressureCenter[] | null | undefined,
  map: maplibregl.Map
): PressureCenterScreenLabel[] {
  if (!Array.isArray(centers) || centers.length === 0) {
    return [];
  }
  const canvas = map.getCanvas();
  const width = canvas.clientWidth || canvas.width;
  const height = canvas.clientHeight || canvas.height;
  const labels: PressureCenterScreenLabel[] = [];
  const marginPx = 48;

  centers.forEach((center, index) => {
    const type = String(center.type ?? "").trim().toUpperCase();
    if (type !== "H" && type !== "L") {
      return;
    }
    const lon = Number(center.lon);
    const lat = Number(center.lat);
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) {
      return;
    }
    const point = map.project([lon, lat]);
    if (
      point.x < -marginPx ||
      point.y < -marginPx ||
      point.x > width + marginPx ||
      point.y > height + marginPx
    ) {
      return;
    }
    labels.push({
      id: `${type}-${index}-${lat.toFixed(3)}-${lon.toFixed(3)}`,
      type,
      valueLabel: pressureCenterValueLabel(center),
      x: point.x,
      y: point.y,
    });
  });

  return labels;
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

function normalizeDataUrl(value: string | null | undefined): string {
  return String(value ?? "").trim();
}

function buildVectorBufferLayers(): LayerSpecification[] {
  return [0, 1].flatMap((bufferIndex) => {
    const sourceId = VECTOR_SOURCE_IDS[bufferIndex as 0 | 1];
    const fillLayerId = VECTOR_FILL_LAYER_IDS[bufferIndex as 0 | 1];
    const haloLineLayerId = VECTOR_HALO_LINE_LAYER_IDS[bufferIndex as 0 | 1];
    const lineLayerId = VECTOR_LINE_LAYER_IDS[bufferIndex as 0 | 1];
    const strokeWidthExpression = ["coalesce", ["get", "stroke_width"], 1.25] as any;
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
        id: haloLineLayerId,
        type: "line",
        source: sourceId,
        layout: {
          visibility: "none",
          "line-join": "round",
          "line-cap": "round",
          "line-sort-key": ["coalesce", ["get", "sort_rank"], 0] as any,
        },
        paint: {
          "line-color": VECTOR_HALO_LINE_COLOR,
          "line-opacity": 0,
          "line-width": ["+", strokeWidthExpression, VECTOR_HALO_LINE_WIDTH_OFFSET] as any,
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
          "line-width": strokeWidthExpression,
        },
      } as LayerSpecification,
    ];
  });
}

function vectorFillOpacityExpression(fade: number) {
  return ["*", Math.max(0, Math.min(1, fade)), ["coalesce", ["get", "fill_opacity"], 0.65]] as const;
}

function vectorHaloLineOpacity(lineOpacity: number, haloEnabled: boolean): number {
  const clampedLineOpacity = Math.max(0, Math.min(1, lineOpacity));
  return haloEnabled ? clampedLineOpacity : 0;
}

function setVectorLayerFade(map: maplibregl.Map, bufferIndex: 0 | 1, fade: number, haloEnabled = false) {
  const clampedFade = Math.max(0, Math.min(1, fade));
  const lineOpacity = clampedFade;
  const haloLineOpacity = vectorHaloLineOpacity(lineOpacity, haloEnabled);
  const fillLayerId = VECTOR_FILL_LAYER_IDS[bufferIndex];
  const haloLineLayerId = VECTOR_HALO_LINE_LAYER_IDS[bufferIndex];
  const lineLayerId = VECTOR_LINE_LAYER_IDS[bufferIndex];
  if (map.getLayer(fillLayerId)) {
    map.setPaintProperty(fillLayerId, "fill-opacity", vectorFillOpacityExpression(clampedFade));
  }
  if (map.getLayer(haloLineLayerId)) {
    map.setPaintProperty(haloLineLayerId, "line-opacity", haloLineOpacity);
  }
  if (map.getLayer(lineLayerId)) {
    map.setPaintProperty(lineLayerId, "line-opacity", lineOpacity);
  }
}

export function buildMapStyle(
  contourGeoJsonUrl?: string | null,
  vectorGeoJsonUrl?: string | null,
  basemapMode: BasemapMode = "light"
): StyleSpecification {
  void vectorGeoJsonUrl;
  const screenshotMode = typeof window !== "undefined"
    && new URLSearchParams(window.location.search).get("screenshot") === "1";
  const basemapTiles = basemapMode === "dark" ? CARTO_DARK_BASE_TILES : CARTO_LIGHT_BASE_TILES;
  const labelTiles = basemapMode === "dark" ? CARTO_DARK_LABEL_TILES : CARTO_LIGHT_LABEL_TILES;
  const mapBackgroundColor = getMapBackgroundColor(basemapMode);
  const boundaryLineColor = getBoundaryLineColor(basemapMode);
  const lakeFillColor = getLakeFillColor(basemapMode);
  const basemapPaint = getBasemapPaintSettings(basemapMode);
  const labelPaint = getLabelPaintSettings(basemapMode);

  return {
    version: 8,
    sources: {
      "twf-basemap": {
        type: "raster",
        tiles: basemapTiles,
        tileSize: CARTO_TILE_SIZE,
      },
      ...(screenshotMode
        ? {}
        : {
            "twf-labels": {
              type: "raster",
              tiles: labelTiles,
              tileSize: CARTO_TILE_SIZE,
            },
          }),
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
          "line-color": CONTOUR_LINE_COLOR,
          "line-opacity": 0.9,
          "line-width": ["interpolate", ["linear"], ["zoom"], 4, 1.5, 8, 2.5, 12, 3.5],
        },
      },
      ...(screenshotMode
        ? []
        : [{
            id: "twf-labels",
            type: "raster",
            source: "twf-labels",
            paint: labelPaint,
          } as LayerSpecification]),
      ...buildVectorBufferLayers(),
    ],
  };
}

type MapCanvasProps = {
  productId?: string | null;
  selectionKey: string;
  selectionEpoch: number;
  gridManifest?: GridManifestResponse | null;
  compositeGridLayers?: Array<{
    id: string;
    manifest: GridManifestResponse | null;
    frameUrl: string | null;
    frameHour: number | null;
    legend: LegendPayload | null;
    prefetchUrls?: string[];
  }>;
  gridLodLevel?: number | null;
  gridFrameUrl?: string | null;
  gridFrameHour?: number | null;
  gridPrefetchPivotHour?: number | null;
  gridLegend?: LegendPayload | null;
  gridActive?: boolean;
  rasterRgbFrameUrl?: string | null;
  rasterRgbPrefetchUrls?: string[];
  rasterRgbActive?: boolean;
  gridContour?: GridContourLayerConfig | null;
  contourGeoJsonUrl?: string | null;
  contourPrefetchUrls?: string[];
  pressureCenters?: PressureCenter[];
  vectorGeoJsonUrl?: string | null;
  vectorPrefetchUrls?: string[];
  vectorLineHaloEnabled?: boolean;
  anchorGeoJson?: AnchorFeatureCollection | null;
  pointLabelsEnabled?: boolean;
  showZoomControls?: boolean;
  isDesktopLayout?: boolean;
  legendButtonVisible?: boolean;
  legendButtonActive?: boolean;
  onLegendButtonClick?: () => void;
  manualLocationJumpRef?: { current: boolean };
  geolocationMarker?: { lat: number; lon: number } | null;
  region: string;
  regionViews?: Record<string, RegionView>;
  viewResetSignal?: number;
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
  onRasterRgbFrameReady?: (frameUrl: string) => void;
  getAnimatedGridPlaybackState?: (() => AnimatedGridPlaybackState | null) | null;
  /** Foreground grid frame path for scrub / post-scrub load (mirrors animation rAF delivery). */
  getDirectGridPlaybackState?: (() => AnimatedGridPlaybackState | null) | null;
  directGridPlaybackActive?: boolean;
  isAnimating?: boolean;
  /** True while grid playback is buffering or running; drives animation-only warm throttling. */
  isGridPlaybackAnimating?: boolean;
  isScrubbing?: boolean;
  /** True when scrub target is far ahead/behind the displayed ready frame on long timelines. */
  scrubLagBurstActive?: boolean;
  scrubProtectedFetchUrlsRef?: { current: string[] };
  onMapReady?: (map: maplibregl.Map) => void;
  onLatestMapDataUrl?: (getter: (() => string | null) | null) => void;
  onCaptureDraft?: (capture: (() => Promise<string | null>) | null) => void;
  onMapHover?: (lat: number, lon: number, x: number, y: number, tooltip?: Exclude<SampleTooltipState, null>) => void;
  onMapHoverEnd?: () => void;
  onAnchorClick?: (anchor: { id: string; city: string; state: string; st: string }) => void;
  onVectorHazardClick?: (selection: VectorHazardSelection) => void;
  anchorBatchPoints?: AnchorBatchPoint[];
  onAnchorFrameSampled?: (payload: {
    frameHour: number;
    selectionEpoch?: number;
    selectionKey?: string;
    gridSampled: boolean;
    values: Record<string, number | null>;
    units: string;
  }) => void;
  onCityFrameSampled?: (payload: {
    frameHour: number;
    selectionEpoch?: number;
    selectionKey?: string;
    gridSampled: boolean;
    points: CityLabelPoint[];
    values: Record<string, number | null>;
    units: string;
  }) => void;
};

export function MapCanvas({
  productId = null,
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
  rasterRgbFrameUrl = null,
  rasterRgbPrefetchUrls = [],
  rasterRgbActive = false,
  gridContour = null,
  contourGeoJsonUrl,
  contourPrefetchUrls = [],
  pressureCenters = [],
  vectorGeoJsonUrl,
  vectorPrefetchUrls = [],
  vectorLineHaloEnabled = false,
  anchorGeoJson = null,
  pointLabelsEnabled = true,
  showZoomControls = false,
  isDesktopLayout = false,
  legendButtonVisible = false,
  legendButtonActive = false,
  onLegendButtonClick,
  manualLocationJumpRef,
  geolocationMarker = null,
  region,
  regionViews,
  viewResetSignal = 0,
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
  onRasterRgbFrameReady,
  getAnimatedGridPlaybackState = null,
  getDirectGridPlaybackState = null,
  directGridPlaybackActive = false,
  isAnimating = false,
  isGridPlaybackAnimating = false,
  isScrubbing = false,
  scrubLagBurstActive = false,
  scrubProtectedFetchUrlsRef = undefined,
  onMapReady,
  onLatestMapDataUrl,
  onCaptureDraft,
  onMapHover,
  onMapHoverEnd,
  onAnchorClick,
  onVectorHazardClick,
  anchorBatchPoints = [],
  onAnchorFrameSampled,
  onCityFrameSampled,
}: MapCanvasProps) {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapSlotRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const latestMapDataUrlRef = useRef<string | null>(null);
  const gridWebglControllerRef = useRef<GridWebglLayerController | null>(null);
  if (!gridWebglControllerRef.current) {
    gridWebglControllerRef.current = new GridWebglLayerController();
  }
  const rasterRgbControllerRef = useRef<RasterRgbLayerController | null>(null);
  const onRasterRgbFrameReadyRef = useRef(onRasterRgbFrameReady);
  onRasterRgbFrameReadyRef.current = onRasterRgbFrameReady;
  const compositeGridControllersRef = useRef<Map<string, GridWebglLayerController>>(new Map());
  const gridRepaintRafRef = useRef<number | null>(null);
  const requestGridRepaint = useCallback(() => {
    const map = mapRef.current;
    if (!map) {
      return;
    }
    if (gridRepaintRafRef.current !== null) {
      return;
    }
    gridRepaintRafRef.current = window.requestAnimationFrame(() => {
      gridRepaintRafRef.current = null;
      mapRef.current?.triggerRepaint();
    });
  }, []);
  useEffect(() => {
    return () => {
      if (gridRepaintRafRef.current !== null) {
        window.cancelAnimationFrame(gridRepaintRafRef.current);
        gridRepaintRafRef.current = null;
      }
    };
  }, []);

  const vectorFetchProductId = useMemo(() => {
    const url = String(vectorGeoJsonUrl ?? "").trim();
    if (url.includes("/nws_hazards/") || url.includes("/nws-hazards/")) {
      return "nws_hazards";
    }
    return productId;
  }, [productId, vectorGeoJsonUrl]);
  const vectorLineHaloEnabledRef = useRef(vectorLineHaloEnabled);
  vectorLineHaloEnabledRef.current = vectorLineHaloEnabled;

  const [isLoaded, setIsLoaded] = useState(false);
  const [vectorCacheRevision, setVectorCacheRevision] = useState(0);
  const [contourScreenLabels, setContourScreenLabels] = useState<ContourScreenLabel[]>([]);
  const [pressureCenterScreenLabels, setPressureCenterScreenLabels] = useState<PressureCenterScreenLabel[]>([]);

  const geolocationMarkerRef = useRef<maplibregl.Marker | null>(null);
  const prevGridFrameHourRef = useRef<number | null>(null);
  /** Detected scrub direction: 1 = forward, -1 = backward, 0 = unknown. */
  const scrubDirectionRef = useRef<1 | -1 | 0>(0);
  const onMapReadyRef = useRef(onMapReady);
  onMapReadyRef.current = onMapReady;
  const onLatestMapDataUrlRef = useRef(onLatestMapDataUrl);
  onLatestMapDataUrlRef.current = onLatestMapDataUrl;
  const onCaptureDraftRef = useRef(onCaptureDraft);
  onCaptureDraftRef.current = onCaptureDraft;
  const onViewportChangeRef = useRef(onViewportChange);
  onViewportChangeRef.current = onViewportChange;
  const contourRequestTokenRef = useRef(0);
  const contourAbortRef = useRef<AbortController | null>(null);
  const isAnimatingRef = useRef(isAnimating);
  isAnimatingRef.current = isAnimating;
  const contourCacheRef = useRef<Map<string, GeoJSON.FeatureCollection>>(new Map());
  const contourPrefetchInFlightRef = useRef<Set<string>>(new Set());
  const activeContourPayloadRef = useRef<GeoJSON.FeatureCollection | null>(null);
  const activeContourUrlRef = useRef("");
  const vectorRequestTokenRef = useRef(0);
  const vectorAbortRef = useRef<AbortController | null>(null);
  const vectorCacheRef = useRef<Map<string, GeoJSON.FeatureCollection>>(new Map());
  const pendingVectorPayloadRef = useRef<{ url: string; payload: GeoJSON.FeatureCollection } | null>(null);
  const activeVectorFetchKeyRef = useRef("");
  const activeVectorBufferRef = useRef<0 | 1 | null>(null);
  const activeVectorUrlRef = useRef("");
  const vectorTransitionRafRef = useRef<number | null>(null);
  const lastAppliedBasemapModeRef = useRef<BasemapMode>(basemapMode);
  const autoplayGridStateSignatureRef = useRef("");
  const directGridStateSignatureRef = useRef("");
  const lastAppliedViewResetSignalRef = useRef<number | null>(null);

  const view = useMemo(() => {
    return regionViews?.[region] ?? {
      center: [MAP_VIEW_DEFAULTS.center[1], MAP_VIEW_DEFAULTS.center[0]] as [number, number],
      zoom: MAP_VIEW_DEFAULTS.zoom,
    };
  }, [region, regionViews]);

  const refreshContourScreenLabels = useCallback(() => {
    const map = mapRef.current;
    if (!map || !activeContourPayloadRef.current) {
      setContourScreenLabels([]);
      return;
    }
    setContourScreenLabels(buildContourScreenLabels(activeContourPayloadRef.current, map));
  }, []);
  const applyContourPayload = useCallback((
    map: maplibregl.Map,
    source: maplibregl.GeoJSONSource,
    rawUrl: string,
    payload: GeoJSON.FeatureCollection,
  ) => {
    const normalizedUrl = normalizeDataUrl(rawUrl);
    activeContourUrlRef.current = normalizedUrl;
    activeContourPayloadRef.current = payload;
    setLayerVisibility(map, CONTOUR_LAYER_ID, Boolean(normalizedUrl));
    source.setData(buildContourLineDisplayPayload(payload, map) as any);
    refreshContourScreenLabels();
  }, [refreshContourScreenLabels]);

  const apiRoot = useMemo(() => API_ORIGIN.replace(/\/$/, ""), []);
  const normalizedRasterRgbFrameUrl = useMemo(() => {
    const rawUrl = String(rasterRgbFrameUrl ?? "").trim();
    if (!rawUrl) {
      return null;
    }
    if (/^https?:\/\//i.test(rawUrl)) {
      return rawUrl;
    }
    return `${apiRoot}${rawUrl.startsWith("/") ? "" : "/"}${rawUrl}`;
  }, [apiRoot, rasterRgbFrameUrl]);
  const buildGridPrefetchUrls = useCallback((params: {
    frameUrl: string | null;
    frameHour: number | null;
    prefetchPivotHour: number | null;
    manifest?: GridManifestResponse | null;
  }): string[] => {
    const { frameUrl, frameHour, prefetchPivotHour } = params;
    const sourceManifest = params.manifest ?? gridManifest;
    if (!sourceManifest?.lods?.length || !frameUrl || !Number.isFinite(frameHour)) {
      return [] as string[];
    }

    const isObservedGrid = String(sourceManifest.model ?? "").trim().toLowerCase() === "mrms";
    const lod = sourceManifest.lods.find((entry) => Number(entry?.level) === Number(gridLodLevel))
      ?? sourceManifest.lods.find((entry) => Number(entry?.level) === 0)
      ?? sourceManifest.lods[0]
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
    const effectivePivotHour = Number.isFinite(prefetchPivotHour)
      ? Number(prefetchPivotHour)
      : Number(frameHour);
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

    const longTimelineFrames = isDesktopLayout
      ? SCRUB_LONG_TIMELINE_FRAMES
      : SCRUB_LONG_TIMELINE_FRAMES_MOBILE;
    const farEndForwardFh = isDesktopLayout
      ? SCRUB_FAR_END_FORWARD_FH
      : SCRUB_FAR_END_FORWARD_FH_MOBILE;
    const burstPrefetchBudget = isDesktopLayout
      ? SCRUB_LAG_BURST_PREFETCH_BUDGET
      : SCRUB_LAG_BURST_PREFETCH_BUDGET_MOBILE;
    const isLongTimeline = frameHours.length >= longTimelineFrames;
    const isFarEndForwardScrub = isScrubbing
      && isLongTimeline
      && direction > 0
      && effectivePivotHour >= farEndForwardFh;
    const preferAheadOnly = (scrubLagBurstActive && direction > 0) || isFarEndForwardScrub;
    const preferBehindOnly = scrubLagBurstActive && direction < 0;
    const expandedPrefetchBudget = scrubLagBurstActive || isFarEndForwardScrub;

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
          const budget = OBSERVED_DESKTOP_SCRUB_PREFETCH_BUDGET;
          if (direction > 0) {
            behindTarget = Math.min(remainingBehind, OBSERVED_DESKTOP_SCRUB_MIN_BEHIND);
            aheadTarget = Math.min(remainingAhead, budget - behindTarget);
          } else if (direction < 0) {
            aheadTarget = Math.min(remainingAhead, OBSERVED_DESKTOP_SCRUB_MIN_AHEAD);
            behindTarget = Math.min(remainingBehind, budget - aheadTarget);
          } else {
            const halfBudget = Math.floor(budget / 2);
            aheadTarget = Math.min(remainingAhead, halfBudget + 1);
            behindTarget = Math.min(remainingBehind, budget - aheadTarget);
          }
        }
      }
    } else if (mode === "idle-warmup") {
      // Idle: progressively warm the full timeline outward from the current frame.
      aheadTarget = remainingAhead;
      behindTarget = remainingBehind;
      if (preferAheadOnly) {
        behindTarget = 0;
      } else if (preferBehindOnly) {
        aheadTarget = Math.min(remainingAhead, 1);
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
      const budget = expandedPrefetchBudget
        ? burstPrefetchBudget
        : FORECAST_SCRUB_PREFETCH_BUDGET;
      if (preferAheadOnly) {
        behindTarget = 0;
        aheadTarget = Math.min(remainingAhead, budget);
      } else if (preferBehindOnly) {
        aheadTarget = Math.min(remainingAhead, 1);
        behindTarget = Math.min(remainingBehind, budget - aheadTarget);
      } else if (direction > 0) {
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
      if (url && url !== frameUrl && !urls.includes(url)) {
        urls.push(url);
      }
    };

    // Warm the scrub/playback pivot itself first when it differs from the
    // currently displayed frame (nearest-ready during fast cold scrub).
    pushFrameUrl(effectivePivotHour);

    // Interleave ahead/behind nearest-first for scrub and idle warmup so both
    // directions stay warm in the texture queue.  Autoplay keeps sequential
    // ordering for its forward-biased prefetch window.
    const useInterleavedPrefetch = (isObservedGrid && mode !== "autoplay") || mode === "scrub" || mode === "idle-warmup";
    if (preferAheadOnly) {
      for (let step = 1; step <= aheadTarget; step += 1) {
        if (pivot + step < frameHours.length) {
          pushFrameUrl(frameHours[pivot + step]);
        }
      }
    } else if (preferBehindOnly) {
      for (let step = 1; step <= behindTarget; step += 1) {
        if (pivot - step >= 0) {
          pushFrameUrl(frameHours[pivot - step]);
        }
      }
    } else if (useInterleavedPrefetch) {
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

    // Prefetch only schedules pivot±N; frameUrl alone loads the pivot hour.
    // When those diverge (scrub jump or play before the target frame renders),
    // queue the pivot frame explicitly so playback buffering can proceed.
    if (
      Number.isFinite(effectivePivotHour)
      && effectivePivotHour !== Number(frameHour)
    ) {
      const pivotFrame = frameByHour.get(effectivePivotHour);
      const pivotUrl = normalizeGridUrl(String(pivotFrame?.url ?? "").trim());
      if (pivotUrl && pivotUrl !== frameUrl && !urls.includes(pivotUrl)) {
        urls.unshift(pivotUrl);
      }
    }

    return urls;
  }, [apiRoot, gridLodLevel, gridManifest, isDesktopLayout, isScrubbing, mode, scrubLagBurstActive]);
  const gridPrefetchUrls = useMemo(() => {
    return buildGridPrefetchUrls({
      frameUrl: gridFrameUrl,
      frameHour: gridFrameHour,
      prefetchPivotHour: gridPrefetchPivotHour,
      manifest: gridManifest,
    });
  }, [buildGridPrefetchUrls, gridFrameHour, gridFrameUrl, gridPrefetchPivotHour]);
  const shouldUseGridController = Boolean(
    gridActive || gridManifest || gridFrameUrl || gridPrefetchUrls.length > 0 || compositeGridLayers.length > 0
  );

  const emitGridFrameVisible = useCallback((
    payload: GridFrameVisiblePayload,
    sampler: GridWebglLayerController | null,
  ) => {
    onGridFrameVisible?.(payload);
    if (onAnchorFrameSampled && anchorBatchPoints.length > 0 && sampler) {
      const sampled = sampler.sampleAnchorPoints(anchorBatchPoints);
      onAnchorFrameSampled({
        frameHour: payload.frameHour,
        selectionEpoch: payload.selectionEpoch,
        selectionKey: payload.selectionKey,
        gridSampled: Boolean(sampled),
        values: sampled?.values ?? {},
        units: sampled?.units ?? "",
      });
    }

    // City label sampling — the MapLibre symbol layer is the canonical label
    // renderer. Skipped when point labels are toggled off (the effect that
    // watches pointLabelsEnabled clears the source). queryVisibleCityPoints is a
    // synchronous bounds query over the loaded GeoJSON (no glyph/render
    // dependency), so this runs inline without idle deferral.
    if (sampler && pointLabelsEnabled && mapRef.current) {
      const map = mapRef.current;
      const cityPoints = queryVisibleCityPoints(map);
      if (cityPoints.length > 0) {
        const cityBatchPoints = cityPoints.map((p) => ({ id: p.id, lat: p.lat, lon: p.lng }));
        const citySampled = sampler.sampleAnchorPoints(cityBatchPoints);
        if (citySampled) {
          updateCityValueLabels(map, cityPoints, citySampled.values, citySampled.units);
        }
      }
    }
  }, [anchorBatchPoints, onAnchorFrameSampled, onGridFrameVisible, pointLabelsEnabled]);

  const syncGridControllers = useCallback((params: {
    frameUrl: string | null;
    frameHour: number | null;
    prefetchPivotHour: number | null;
    compositeLayers: AnimatedGridPlaybackState["compositeGridLayers"];
  }) => {
    const map = mapRef.current;
    const controller = gridWebglControllerRef.current;
    if (!map || !isLoaded || !controller) {
      return;
    }

    const { frameUrl, frameHour, prefetchPivotHour, compositeLayers } = params;
    const gridScrubPrefetch = isScrubbing || mode === "idle-warmup";
    const primaryCompositeLayerId = compositeLayers[0]?.id ?? null;
    const protectedFetchUrls = scrubProtectedFetchUrlsRef?.current ?? [];
    const activePrefetchUrls = buildGridPrefetchUrls({
      frameUrl,
      frameHour,
      prefetchPivotHour,
    });
    const shouldAttachGridController = Boolean(
      gridActive || gridManifest || frameUrl || activePrefetchUrls.length > 0 || compositeLayers.length > 0
    );

    if (!shouldAttachGridController) {
      controller.remove(map);
      for (const compositeController of compositeGridControllersRef.current.values()) {
        compositeController.remove(map);
      }
      compositeGridControllersRef.current.clear();
      return;
    }

    const normalizedContourUrl = normalizeDataUrl(contourGeoJsonUrl);
    const contourSource = map.getSource(CONTOUR_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
    const cachedContourPayload = normalizedContourUrl ? contourCacheRef.current.get(normalizedContourUrl) : null;
    if (contourSource && typeof contourSource.setData === "function" && cachedContourPayload) {
      applyContourPayload(map, contourSource, normalizedContourUrl, cachedContourPayload);
    }

    controller.ensureAttached(map, gridOverlayBeforeLayerId(map));
    controller.update({
      active: Boolean(gridActive && gridManifest && frameUrl),
      manifest: gridManifest,
      lodLevel: gridLodLevel,
      frameUrl,
      frameHour,
      legend: gridLegend,
      opacity,
      overlayFadeOutZoom,
      selectionEpoch,
      selectionKey,
      prefetchUrls: activePrefetchUrls,
      contour: gridContour,
      rasterPaint: getGridPaintSettings(variable, basemapMode),
      onFrameVisible: (payload) => {
        emitGridFrameVisible(payload, compositeLayers.length === 0 ? controller : null);
      },
      onFrameReady: onGridFrameReady,
      onFrameEvicted: onGridFrameEvicted,
      requestRepaint: requestGridRepaint,
      isAnimating: isGridPlaybackAnimating,
      isScrubPrefetch: gridScrubPrefetch,
      scrubLagBurst: scrubLagBurstActive,
      protectedFetchUrls,
    });

    const activeCompositeLayerIds = new Set<string>();
    for (const layer of compositeLayers) {
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
        prefetchUrls: layer.prefetchUrls ?? buildGridPrefetchUrls({
          frameUrl: layer.frameUrl,
          frameHour: layer.frameHour,
          prefetchPivotHour,
          manifest: layer.manifest,
        }),
        contour: layer.id === compositeLayers[compositeLayers.length - 1]?.id ? gridContour : null,
        rasterPaint: getGridPaintSettings(variable, basemapMode),
        onFrameVisible: (payload) => {
          emitGridFrameVisible(
            payload,
            layer.id === primaryCompositeLayerId ? compositeController : null,
          );
        },
        onFrameReady: onGridFrameReady,
        onFrameEvicted: onGridFrameEvicted,
        requestRepaint: requestGridRepaint,
        isAnimating: isGridPlaybackAnimating,
        isScrubPrefetch: gridScrubPrefetch,
        scrubLagBurst: scrubLagBurstActive,
        protectedFetchUrls,
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
    applyContourPayload,
    basemapMode,
    buildGridPrefetchUrls,
    contourGeoJsonUrl,
    emitGridFrameVisible,
    gridActive,
    gridContour,
    gridLegend,
    gridLodLevel,
    gridManifest,
    isGridPlaybackAnimating,
    isLoaded,
    isScrubbing,
    mode,
    onGridFrameEvicted,
    scrubLagBurstActive,
    scrubProtectedFetchUrlsRef,
    onGridFrameReady,
    opacity,
    overlayFadeOutZoom,
    requestGridRepaint,
    selectionEpoch,
    selectionKey,
    variable,
  ]);

  useEffect(() => {
    return () => {
      geolocationMarkerRef.current?.remove();
      geolocationMarkerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    if (!geolocationMarker || !Number.isFinite(geolocationMarker.lat) || !Number.isFinite(geolocationMarker.lon)) {
      geolocationMarkerRef.current?.remove();
      geolocationMarkerRef.current = null;
      return;
    }

    if (!geolocationMarkerRef.current) {
      const element = document.createElement("div");
      element.setAttribute("aria-hidden", "true");
      element.style.width = "18px";
      element.style.height = "18px";
      element.style.borderRadius = "9999px";
      element.style.background = "rgba(55,138,221,0.26)";
      element.style.border = "1px solid rgba(55,138,221,0.42)";
      element.style.display = "flex";
      element.style.alignItems = "center";
      element.style.justifyContent = "center";
      element.style.boxShadow = "0 0 0 1px rgba(255,255,255,0.08), 0 6px 18px rgba(10,18,32,0.35)";

      const core = document.createElement("div");
      core.style.width = "8px";
      core.style.height = "8px";
      core.style.borderRadius = "9999px";
      core.style.background = "rgba(117,196,255,0.98)";
      core.style.boxShadow = "0 0 0 2px rgba(6,18,33,0.55)";
      element.appendChild(core);

      geolocationMarkerRef.current = new maplibregl.Marker({
        element,
        anchor: "center",
      })
        .setLngLat([geolocationMarker.lon, geolocationMarker.lat])
        .addTo(map);
      return;
    }

    geolocationMarkerRef.current.setLngLat([geolocationMarker.lon, geolocationMarker.lat]);
  }, [geolocationMarker, isLoaded]);

  const enforceLayerOrder = useCallback((map: maplibregl.Map) => {
    if (!map.getLayer("twf-labels")) {
      return;
    }

    const firstVectorFillLayerId = VECTOR_FILL_LAYER_IDS.find((layerId) => map.getLayer(layerId));
    if (map.getLayer(LAKE_MASK_LAYER_ID) && firstVectorFillLayerId) {
      map.moveLayer(LAKE_MASK_LAYER_ID, firstVectorFillLayerId);
    }
    if (map.getLayer(GRID_WEBGL_LAYER_ID) && map.getLayer(COASTLINE_LAYER_ID)) {
      map.moveLayer(GRID_WEBGL_LAYER_ID, COASTLINE_LAYER_ID);
    }
    for (const layerId of RASTER_RGB_LAYER_IDS) {
      if (map.getLayer(layerId) && map.getLayer(COASTLINE_LAYER_ID)) {
        map.moveLayer(layerId, COASTLINE_LAYER_ID);
      }
    }
    for (const layerId of compositeGridControllersRef.current.keys()) {
      if (map.getLayer(layerId) && map.getLayer(COASTLINE_LAYER_ID)) {
        map.moveLayer(layerId, COASTLINE_LAYER_ID);
      }
    }
    for (const layerId of VECTOR_FILL_LAYER_IDS) {
      if (map.getLayer(layerId) && map.getLayer(COASTLINE_LAYER_ID)) {
        map.moveLayer(layerId, COASTLINE_LAYER_ID);
      }
    }
    for (const layerId of VECTOR_HALO_LINE_LAYER_IDS) {
      if (map.getLayer(layerId) && map.getLayer(COASTLINE_LAYER_ID)) {
        map.moveLayer(layerId, COASTLINE_LAYER_ID);
      }
    }
    for (const layerId of VECTOR_LINE_LAYER_IDS) {
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
    map.moveLayer("twf-labels");
    // City label layers sit above weather raster/WebGL and basemap labels.
    moveCityLabelLayersToTop(map);
  }, []);

  useEffect(() => {
    onLatestMapDataUrlRef.current?.(() => latestMapDataUrlRef.current);

    return () => {
      onLatestMapDataUrlRef.current?.(null);
    };
  }, []);

  useEffect(() => {
    const captureDraftDataUrl = (): Promise<string | null> => {
      const map = mapRef.current;
      if (!map) {
        return Promise.resolve(null);
      }
      // The live map keeps preserveDrawingBuffer disabled, so toDataURL() must
      // run inside a render callback (before the buffer swap) to capture pixels.
      return new Promise((resolve) => {
        map.once("render", () => {
          try {
            resolve(map.getCanvas().toDataURL("image/jpeg", 0.7));
          } catch {
            resolve(null);
          }
        });
        map.triggerRepaint();
      });
    };
    onCaptureDraftRef.current?.(captureDraftDataUrl);

    return () => {
      onCaptureDraftRef.current?.(null);
    };
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
      maxZoom: view.maxZoom ?? 14,
      minPitch: 0,
      maxPitch: 0,
      pitchWithRotate: false,
      dragRotate: false,
      touchPitch: false,
      attributionControl: false,
      preserveDrawingBuffer: false,
    });

    map.touchZoomRotate.disableRotation();

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
      // Fire-and-forget: fetches city candidates and adds the city label
      // sources/layers. Handles its own errors and never throws. A follow-up
      // enforceLayerOrder() runs once isLoaded flips, re-ordering these layers.
      void initCityLayers(map).then((initialized) => {
        if (initialized) {
          enforceLayerOrder(map);
          map.triggerRepaint();
        }
      });
      onMapReadyRef.current?.(map);
    });

    mapRef.current = map;
    const resizeMap = () => {
      map.resize();
    };
    resizeRafId = window.requestAnimationFrame(() => {
      resizeMap();
      window.requestAnimationFrame(resizeMap);
    });

    const mapSlot = mapSlotRef.current;
    const resizeObserver = mapSlot && typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(() => {
        resizeMap();
      })
      : null;
    if (mapSlot) {
      resizeObserver?.observe(mapSlot);
    }

    return () => {
      resizeObserver?.disconnect();
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
      clearCityValueLabels(map);
      gridWebglControllerRef.current?.remove(map);
      rasterRgbControllerRef.current?.remove(map);
      rasterRgbControllerRef.current = null;
      for (const controller of compositeGridControllersRef.current.values()) {
        controller.remove(map);
      }
      compositeGridControllersRef.current.clear();
      map.remove();
      mapRef.current = null;
      setIsLoaded(false);
    };
  }, [enforceLayerOrder]);

  // Viewer map keeps preserveDrawingBuffer disabled for pan performance, so canvas
  // snapshots are not cached here. screenshot_export.ts rebuilds an offscreen map.
  useEffect(() => {
    latestMapDataUrlRef.current = null;
  }, [isAnimating, isLoaded]);

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
      if (rasterRgbActive && normalizedRasterRgbFrameUrl) {
        if (!rasterRgbControllerRef.current) {
          rasterRgbControllerRef.current = new RasterRgbLayerController();
        }
        rasterRgbControllerRef.current.ensureAttached(map, gridOverlayBeforeLayerId(map));
        rasterRgbControllerRef.current.update(map, normalizedRasterRgbFrameUrl, opacity, gridOverlayBeforeLayerId(map));
      }
      const contourSource = map.getSource(CONTOUR_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
      if (contourSource && typeof contourSource.setData === "function") {
        const activeUrl = activeContourUrlRef.current;
        const activePayload = activeUrl ? contourCacheRef.current.get(activeUrl) ?? activeContourPayloadRef.current : null;
        if (activeUrl && activePayload) {
          applyContourPayload(map, contourSource, activeUrl, activePayload);
        } else {
          setLayerVisibility(map, CONTOUR_LAYER_ID, false);
          contourSource.setData(EMPTY_FEATURE_COLLECTION as any);
        }
      }
      enforceLayerOrder(map);
      // setStyle() wiped all sources/layers, including the city label layers.
      // Re-init them; the double-init guard and setGlyphs are both idempotent.
      void initCityLayers(map).then((initialized) => {
        if (initialized) {
          enforceLayerOrder(map);
          map.triggerRepaint();
        }
      });
    };

    map.once("styledata", onStyleData);
    map.setStyle(buildMapStyle(null, null, basemapMode));

    return () => {
      map.off("styledata", onStyleData);
    };
  }, [applyContourPayload, basemapMode, enforceLayerOrder, isLoaded, normalizedRasterRgbFrameUrl, opacity, rasterRgbActive, shouldUseGridController]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      setPressureCenterScreenLabels([]);
      return;
    }

    let rafId: number | null = null;
    const scheduleSync = () => {
      if (rafId !== null) {
        return;
      }
      rafId = window.requestAnimationFrame(() => {
        rafId = null;
        setPressureCenterScreenLabels(buildPressureCenterScreenLabels(pressureCenters, map));
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
  }, [isLoaded, pressureCenters]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    const source = map.getSource(CONTOUR_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
    if (!source || typeof source.setData !== "function") {
      return;
    }

    const normalizedUrl = normalizeDataUrl(contourGeoJsonUrl);

    if (!normalizedUrl) {
      contourAbortRef.current?.abort();
      contourAbortRef.current = null;
      activeContourUrlRef.current = "";
      activeContourPayloadRef.current = null;
      setContourScreenLabels([]);
      setLayerVisibility(map, CONTOUR_LAYER_ID, false);
      source.setData(EMPTY_FEATURE_COLLECTION as any);
      return;
    }

    const cached = contourCacheRef.current.get(normalizedUrl);
    if (cached) {
      applyContourPayload(map, source, normalizedUrl, cached);
      return;
    }

    const requestToken = ++contourRequestTokenRef.current;
    contourAbortRef.current?.abort();
    contourAbortRef.current = null;

    if (!isAnimatingRef.current || !activeContourPayloadRef.current) {
      activeContourUrlRef.current = "";
      activeContourPayloadRef.current = null;
      setContourScreenLabels([]);
      setLayerVisibility(map, CONTOUR_LAYER_ID, false);
      source.setData(EMPTY_FEATURE_COLLECTION as any);
    } else {
      setLayerVisibility(map, CONTOUR_LAYER_ID, true);
    }

    const controller = new AbortController();
    contourAbortRef.current = controller;
    const startedAtMs = startNetworkTimer();

    void productFetch(productId, normalizedUrl, {
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
        while (contourCacheRef.current.size > CONTOUR_CACHE_MAX_ENTRIES) {
          const oldestKey = contourCacheRef.current.keys().next().value;
          if (!oldestKey) {
            break;
          }
          contourCacheRef.current.delete(oldestKey);
        }
        applyContourPayload(map, source, normalizedUrl, payload);
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
  }, [applyContourPayload, contourGeoJsonUrl, isLoaded, productId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }

    let rafId: number | null = null;
    const scheduleRefresh = () => {
      if (rafId !== null) {
        return;
      }
      rafId = window.requestAnimationFrame(() => {
        rafId = null;
        refreshContourScreenLabels();
      });
    };

    map.on("move", scheduleRefresh);
    map.on("zoom", scheduleRefresh);
    map.on("resize", scheduleRefresh);
    scheduleRefresh();

    return () => {
      map.off("move", scheduleRefresh);
      map.off("zoom", scheduleRefresh);
      map.off("resize", scheduleRefresh);
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId);
      }
    };
  }, [isLoaded, refreshContourScreenLabels]);

  useEffect(() => {
    if (!isLoaded || contourPrefetchUrls.length === 0) {
      return;
    }

    const controller = new AbortController();
    const prefetchCandidates = isDesktopLayout
      ? contourPrefetchUrls
      : contourPrefetchUrls.slice(0, CONTOUR_PREFETCH_MOBILE_LIMIT);
    const pendingUrls: string[] = [];
    for (const rawUrl of prefetchCandidates) {
      const normalizedUrl = normalizeDataUrl(rawUrl);
      if (
        !normalizedUrl
        || contourCacheRef.current.has(normalizedUrl)
        || contourPrefetchInFlightRef.current.has(normalizedUrl)
      ) {
        continue;
      }
      pendingUrls.push(normalizedUrl);
    }

    if (pendingUrls.length === 0) {
      return () => {
        controller.abort();
      };
    }

    let nextIndex = 0;
    const workerCount = Math.min(
      pendingUrls.length,
      isDesktopLayout ? CONTOUR_PREFETCH_CONCURRENCY_DESKTOP : CONTOUR_PREFETCH_CONCURRENCY_MOBILE,
    );
    const mobileYield = !isDesktopLayout
      ? () => new Promise<void>((resolve) => window.setTimeout(resolve, CONTOUR_PREFETCH_MOBILE_YIELD_MS))
      : null;

    const runPrefetchWorker = async () => {
      while (!controller.signal.aborted) {
        const normalizedUrl = pendingUrls[nextIndex];
        nextIndex += 1;
        if (!normalizedUrl) {
          return;
        }
        if (contourCacheRef.current.has(normalizedUrl) || contourPrefetchInFlightRef.current.has(normalizedUrl)) {
          continue;
        }
        contourPrefetchInFlightRef.current.add(normalizedUrl);
        try {
          const response = await productFetch(productId, normalizedUrl, {
            credentials: "omit",
            signal: controller.signal,
          });
          if (!response.ok) {
            throw new Error(`Contour prefetch failed: ${response.status}`);
          }
          const payload = withContourLabels((await response.json()) as GeoJSON.FeatureCollection);
          if (controller.signal.aborted) {
            return;
          }
          contourCacheRef.current.set(normalizedUrl, payload);
          while (contourCacheRef.current.size > CONTOUR_CACHE_MAX_ENTRIES) {
            const oldestKey = contourCacheRef.current.keys().next().value;
            if (!oldestKey) {
              break;
            }
            contourCacheRef.current.delete(oldestKey);
          }
        } catch (error) {
          if (controller.signal.aborted) {
            return;
          }
          console.warn("[map] contour prefetch failed", { contourGeoJsonUrl: normalizedUrl, error });
        } finally {
          contourPrefetchInFlightRef.current.delete(normalizedUrl);
        }
        if (mobileYield && !controller.signal.aborted) {
          await mobileYield();
        }
      }
    };

    for (let index = 0; index < workerCount; index += 1) {
      void runPrefetchWorker();
    }

    return () => {
      controller.abort();
    };
  }, [contourGeoJsonUrl, contourPrefetchUrls, isDesktopLayout, isLoaded, productId]);

  useEffect(() => {
    const normalizedUrl = String(vectorGeoJsonUrl ?? "").trim();
    const fetchKey = `${vectorFetchProductId}|${normalizedUrl}`;
    const keyChanged = activeVectorFetchKeyRef.current !== fetchKey;

    if (!normalizedUrl) {
      activeVectorFetchKeyRef.current = "";
      vectorRequestTokenRef.current += 1;
      vectorAbortRef.current?.abort();
      vectorAbortRef.current = null;
      return;
    }

    if (keyChanged) {
      activeVectorFetchKeyRef.current = fetchKey;
      vectorRequestTokenRef.current += 1;
      vectorAbortRef.current?.abort();
      vectorAbortRef.current = null;
    }

    if (vectorCacheRef.current.has(normalizedUrl)) {
      return;
    }

    if (vectorAbortRef.current !== null) {
      return;
    }

    const requestToken = vectorRequestTokenRef.current;
    const controller = new AbortController();
    vectorAbortRef.current = controller;

    void productFetch(vectorFetchProductId, normalizedUrl, {
      credentials: "omit",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Vector request failed: ${response.status}`);
        }
        return (await response.json()) as GeoJSON.FeatureCollection;
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
        setVectorCacheRevision((revision) => revision + 1);
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
  }, [vectorFetchProductId, vectorGeoJsonUrl]);

  useEffect(() => {
    const normalizedUrl = String(vectorGeoJsonUrl ?? "").trim();

    const map = mapRef.current;
    const mapReady = Boolean(
      map
      && isLoaded
      && map.getSource(VECTOR_SOURCE_IDS[0])
      && map.getSource(VECTOR_SOURCE_IDS[1]),
    );

    const resolveVectorSource = (bufferIndex: 0 | 1) => {
      if (!map) {
        return null;
      }
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
      if (!map) {
        return;
      }
      setLayerVisibility(map, VECTOR_FILL_LAYER_IDS[bufferIndex], false);
      setLayerVisibility(map, VECTOR_HALO_LINE_LAYER_IDS[bufferIndex], false);
      setLayerVisibility(map, VECTOR_LINE_LAYER_IDS[bufferIndex], false);
      setVectorLayerFade(map, bufferIndex, 0, vectorLineHaloEnabledRef.current);
    };
    const showVectorBuffer = (bufferIndex: 0 | 1, fade: number) => {
      if (!map) {
        return;
      }
      const haloEnabled = vectorLineHaloEnabledRef.current;
      setLayerVisibility(map, VECTOR_FILL_LAYER_IDS[bufferIndex], true);
      setLayerVisibility(map, VECTOR_HALO_LINE_LAYER_IDS[bufferIndex], haloEnabled);
      setLayerVisibility(map, VECTOR_LINE_LAYER_IDS[bufferIndex], true);
      setVectorLayerFade(map, bufferIndex, fade, haloEnabled);
    };
    const finishOnBuffer = (bufferIndex: 0 | 1, payload: GeoJSON.FeatureCollection, url: string) => {
      if (!applyVectorData(bufferIndex, payload)) {
        pendingVectorPayloadRef.current = { url, payload };
        return;
      }
      pendingVectorPayloadRef.current = null;
      showVectorBuffer(bufferIndex, 1);
      hideVectorBuffer((bufferIndex === 0 ? 1 : 0));
      activeVectorBufferRef.current = bufferIndex;
      activeVectorUrlRef.current = url;
    };
    const startCrossfade = (fromBuffer: 0 | 1, toBuffer: 0 | 1, payload: GeoJSON.FeatureCollection, url: string) => {
      if (!applyVectorData(toBuffer, payload)) {
        pendingVectorPayloadRef.current = { url, payload };
        return;
      }
      pendingVectorPayloadRef.current = null;
      if (!map) {
        return;
      }
      if (vectorTransitionRafRef.current !== null) {
        window.cancelAnimationFrame(vectorTransitionRafRef.current);
        vectorTransitionRafRef.current = null;
      }
      showVectorBuffer(toBuffer, 0);
      showVectorBuffer(fromBuffer, 1);
      const startedAt = performance.now();
      const haloEnabled = vectorLineHaloEnabledRef.current;
      const tick = (now: number) => {
        if (!map) {
          return;
        }
        const progress = Math.min(1, (now - startedAt) / VECTOR_TRANSITION_MS);
        setVectorLayerFade(map, fromBuffer, 1 - progress, haloEnabled);
        setVectorLayerFade(map, toBuffer, progress, haloEnabled);
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
    const deliverVectorPayload = (payload: GeoJSON.FeatureCollection, url: string) => {
      if (!mapReady) {
        pendingVectorPayloadRef.current = { url, payload };
        return;
      }
      if (activeVectorUrlRef.current === url && activeVectorBufferRef.current !== null) {
        const activeBuffer = activeVectorBufferRef.current;
        showVectorBuffer(activeBuffer, 1);
        hideVectorBuffer(activeBuffer === 0 ? 1 : 0);
        pendingVectorPayloadRef.current = null;
        return;
      }
      const activeBuffer = activeVectorBufferRef.current;
      if (activeBuffer === null) {
        finishOnBuffer(0, payload, url);
        return;
      }
      startCrossfade(activeBuffer, activeBuffer === 0 ? 1 : 0, payload, url);
    };

    if (!normalizedUrl) {
      pendingVectorPayloadRef.current = null;
      if (mapReady) {
        applyVectorData(0, EMPTY_FEATURE_COLLECTION);
        applyVectorData(1, EMPTY_FEATURE_COLLECTION);
        hideVectorBuffer(0);
        hideVectorBuffer(1);
        activeVectorBufferRef.current = null;
        activeVectorUrlRef.current = "";
      }
      return;
    }

    const pending = pendingVectorPayloadRef.current;
    if (pending?.url === normalizedUrl) {
      deliverVectorPayload(pending.payload, normalizedUrl);
      return;
    }

    const cached = vectorCacheRef.current.get(normalizedUrl);
    if (!cached) {
      return;
    }

    deliverVectorPayload(cached, normalizedUrl);
  }, [basemapMode, isLoaded, vectorCacheRevision, vectorGeoJsonUrl]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    for (const bufferIndex of [0, 1] as const) {
      const fillLayerId = VECTOR_FILL_LAYER_IDS[bufferIndex];
      const haloLayerId = VECTOR_HALO_LINE_LAYER_IDS[bufferIndex];
      if (!map.getLayer(fillLayerId) || !map.getLayer(haloLayerId)) {
        continue;
      }
      const fillVisible = map.getLayoutProperty(fillLayerId, "visibility") === "visible";
      if (!fillVisible) {
        setLayerVisibility(map, haloLayerId, false);
        if (map.getLayer(haloLayerId)) {
          map.setPaintProperty(haloLayerId, "line-opacity", 0);
        }
        continue;
      }
      const lineOpacity = Number(map.getPaintProperty(VECTOR_LINE_LAYER_IDS[bufferIndex], "line-opacity") ?? 0);
      const fade = Math.max(0, Math.min(1, lineOpacity));
      setLayerVisibility(map, haloLayerId, vectorLineHaloEnabled);
      setVectorLayerFade(map, bufferIndex, fade, vectorLineHaloEnabled);
    }
  }, [isLoaded, vectorLineHaloEnabled, vectorGeoJsonUrl]);

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

        void productFetch(vectorFetchProductId, normalizedUrl, {
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
  }, [isLoaded, vectorFetchProductId, vectorGeoJsonUrl, vectorPrefetchUrls]);

  // Point labels toggled off: clear the city value labels immediately. When
  // toggled back on, the next grid frame sample (emitGridFrameVisible) repopulates.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded || pointLabelsEnabled) {
      return;
    }
    clearCityValueLabels(map);
  }, [isLoaded, pointLabelsEnabled]);

  // Clear stale city value labels on variable/model switch (selectionKey
  // changes on either). The next grid frame sample repopulates them.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    clearCityValueLabels(map);
  }, [isLoaded, variable, selectionKey]);

  // One-shot: run city sampling as soon as cities-static finishes loading.
  // Handles the race where emitGridFrameVisible fires before the GeoJSON source
  // is ready, which causes isSourceLoaded to return false and sampling to be skipped.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) return;

    // If already loaded, nothing to do — emitGridFrameVisible will sample on next frame.
    if (map.getSource(CITIES_STATIC_SOURCE_ID) && map.isSourceLoaded(CITIES_STATIC_SOURCE_ID)) {
      return;
    }

    const onSourceData = (e: maplibregl.MapSourceDataEvent) => {
      if (e.sourceId !== CITIES_STATIC_SOURCE_ID || !e.isSourceLoaded) return;
      map.off("sourcedata", onSourceData);
      // Force a repaint so onFrameVisible fires and populates city labels.
      map.triggerRepaint();
    };

    map.on("sourcedata", onSourceData);
    return () => { map.off("sourcedata", onSourceData); };
  }, [isLoaded]);

  // --- Grid controller update (runs on every frame / config change) ---
  useEffect(() => {
    if (getAnimatedGridPlaybackState && isAnimating && mode === "autoplay") {
      return;
    }
    if (directGridPlaybackActive && getDirectGridPlaybackState) {
      return;
    }
    syncGridControllers({
      frameUrl: gridFrameUrl,
      frameHour: gridFrameHour,
      prefetchPivotHour: gridPrefetchPivotHour,
      compositeLayers: compositeGridLayers,
    });
  }, [
    compositeGridLayers,
    directGridPlaybackActive,
    gridFrameHour,
    gridFrameUrl,
    gridPrefetchPivotHour,
    getAnimatedGridPlaybackState,
    getDirectGridPlaybackState,
    isAnimating,
    mode,
    syncGridControllers,
  ]);

  useEffect(() => {
    if (!rasterRgbActive || rasterRgbPrefetchUrls.length === 0) {
      return;
    }
    if (!rasterRgbControllerRef.current) {
      rasterRgbControllerRef.current = new RasterRgbLayerController();
      rasterRgbControllerRef.current.setOnFrameReady((url) => {
        onRasterRgbFrameReadyRef.current?.(url);
      });
    }
    rasterRgbControllerRef.current.prefetch(rasterRgbPrefetchUrls);
  }, [rasterRgbActive, rasterRgbPrefetchUrls]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    if (!rasterRgbActive) {
      rasterRgbControllerRef.current?.remove(map);
      rasterRgbControllerRef.current = null;
      return;
    }
    if (!rasterRgbControllerRef.current) {
      rasterRgbControllerRef.current = new RasterRgbLayerController();
    }
    rasterRgbControllerRef.current.setOnFrameReady((url) => {
      onRasterRgbFrameReadyRef.current?.(url);
    });
    rasterRgbControllerRef.current.ensureAttached(map, gridOverlayBeforeLayerId(map));
    rasterRgbControllerRef.current.update(map, normalizedRasterRgbFrameUrl, opacity, gridOverlayBeforeLayerId(map));
    enforceLayerOrder(map);
  }, [enforceLayerOrder, isLoaded, normalizedRasterRgbFrameUrl, opacity, rasterRgbActive]);

  useEffect(() => {
    if (!getDirectGridPlaybackState || !directGridPlaybackActive) {
      directGridStateSignatureRef.current = "";
      return;
    }

    let rafId: number | null = null;
    const syncDirectState = () => {
      const nextState = getDirectGridPlaybackState();
      if (!nextState) {
        directGridStateSignatureRef.current = "";
        rafId = window.requestAnimationFrame(syncDirectState);
        return;
      }
      const nextSignature = JSON.stringify({
        frameUrl: nextState.frameUrl,
        frameHour: nextState.frameHour,
        prefetchPivotHour: nextState.prefetchPivotHour,
        compositeGridLayers: nextState.compositeGridLayers.map((layer) => ({
          id: layer.id,
          frameUrl: layer.frameUrl,
          frameHour: layer.frameHour,
        })),
      });
      if (nextSignature !== directGridStateSignatureRef.current) {
        directGridStateSignatureRef.current = nextSignature;
        syncGridControllers({
          frameUrl: nextState.frameUrl,
          frameHour: nextState.frameHour,
          prefetchPivotHour: nextState.prefetchPivotHour,
          compositeLayers: nextState.compositeGridLayers,
        });
      }
      rafId = window.requestAnimationFrame(syncDirectState);
    };

    syncDirectState();
    return () => {
      directGridStateSignatureRef.current = "";
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId);
      }
    };
  }, [directGridPlaybackActive, getDirectGridPlaybackState, syncGridControllers]);

  useEffect(() => {
    if (!getAnimatedGridPlaybackState || !isAnimating || mode !== "autoplay") {
      autoplayGridStateSignatureRef.current = "";
      return;
    }

    let rafId: number | null = null;
    const syncAnimatedState = () => {
      const nextState = getAnimatedGridPlaybackState();
      if (!nextState) {
        autoplayGridStateSignatureRef.current = "";
        rafId = window.requestAnimationFrame(syncAnimatedState);
        return;
      }
      const nextSignature = JSON.stringify({
        frameUrl: nextState.frameUrl,
        frameHour: nextState.frameHour,
        prefetchPivotHour: nextState.prefetchPivotHour,
        compositeGridLayers: nextState.compositeGridLayers.map((layer) => ({
          id: layer.id,
          frameUrl: layer.frameUrl,
          frameHour: layer.frameHour,
        })),
      });
      if (nextSignature !== autoplayGridStateSignatureRef.current) {
        autoplayGridStateSignatureRef.current = nextSignature;
        syncGridControllers({
          frameUrl: nextState.frameUrl,
          frameHour: nextState.frameHour,
          prefetchPivotHour: nextState.prefetchPivotHour,
          compositeLayers: nextState.compositeGridLayers,
        });
      }
      rafId = window.requestAnimationFrame(syncAnimatedState);
    };

    syncAnimatedState();
    return () => {
      autoplayGridStateSignatureRef.current = "";
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId);
      }
    };
  }, [getAnimatedGridPlaybackState, isAnimating, mode, syncGridControllers]);

  // --- Enforce layer order only on structural changes (not every frame) ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    enforceLayerOrder(map);
  }, [enforceLayerOrder, gridActive, isLoaded, selectionKey, vectorGeoJsonUrl]);

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
    map.setMaxZoom(view.maxZoom ?? 14);
  }, [isLoaded, view.maxZoom, view.minZoom]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    if (lastAppliedViewResetSignalRef.current === viewResetSignal) {
      return;
    }
    lastAppliedViewResetSignalRef.current = viewResetSignal;
    if (manualLocationJumpRef?.current) {
      manualLocationJumpRef.current = false;
      return;
    }
    if (view.bbox) {
      const [west, south, east, north] = view.bbox;
      const fitMinZoom = Number.isFinite(view.fitMinZoom) ? Number(view.fitMinZoom) : null;
      const fitMinZoomBreakpoint = Number.isFinite(view.fitMinZoomBreakpoint) ? Number(view.fitMinZoomBreakpoint) : 640;
      if (fitMinZoom !== null && map.getContainer().clientWidth <= fitMinZoomBreakpoint) {
        const camera = map.cameraForBounds([[west, south], [east, north]], { padding: 24 });
        if (camera?.center && Number.isFinite(camera.zoom)) {
          map.easeTo({
            center: camera.center,
            zoom: Math.max(Number(camera.zoom), fitMinZoom),
            duration: 600,
          });
          return;
        }
      }
      map.fitBounds([[west, south], [east, north]], {
        duration: 600,
        padding: 24,
        ...(Number.isFinite(view.maxZoom) ? { maxZoom: view.maxZoom } : {}),
      });
    } else {
      map.easeTo({ center: view.center, zoom: view.zoom, duration: 600 });
    }
  }, [isLoaded, manualLocationJumpRef, view, viewResetSignal]);

  const onMapHoverRef = useRef(onMapHover);
  onMapHoverRef.current = onMapHover;
  const onMapHoverEndRef = useRef(onMapHoverEnd);
  onMapHoverEndRef.current = onMapHoverEnd;
  const onVectorHazardClickRef = useRef(onVectorHazardClick);
  onVectorHazardClickRef.current = onVectorHazardClick;
  const onAnchorClickRef = useRef(onAnchorClick);
  onAnchorClickRef.current = onAnchorClick;

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isLoaded) {
      return;
    }
    const canvas = map.getCanvas();
    canvas.style.cursor = "";

    const handleMove = (e: maplibregl.MapMouseEvent) => {
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
      const hazardSelection = hazardSelectionFromFeature(vectorFeature, { x, y });
      canvas.style.cursor = hazardSelection && onVectorHazardClickRef.current ? "pointer" : onMapHoverRef.current ? "crosshair" : "";
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

    const handleClick = (e: maplibregl.MapMouseEvent) => {
      if (!onVectorHazardClickRef.current) {
        return;
      }
      const vectorFeatures = map.queryRenderedFeatures(e.point, {
        layers: [...VECTOR_FILL_LAYER_IDS],
      }) as Array<{ properties?: Record<string, unknown> }>;
      const selection = hazardSelectionFromFeature(vectorFeatures[0], e.point);
      if (!selection) {
        return;
      }
      onVectorHazardClickRef.current(selection);
    };

    const handleLeave = () => {
      canvas.style.cursor = "";
      onMapHoverEndRef.current?.();
    };

    // City value labels (MapLibre symbol layer) behave like the old anchor
    // chips: pointer cursor on hover, fire onAnchorClick on click.
    const handleCityClick = (e: maplibregl.MapLayerMouseEvent) => {
      const feature = e.features?.[0];
      const name = typeof feature?.properties?.name === "string" ? feature.properties.name.trim() : "";
      if (!name) {
        return;
      }
      // State/wfo lookup is a Phase 6 concern — pass the city name only.
      onAnchorClickRef.current?.({ id: name, city: name, state: "", st: "" });
    };
    const handleCityEnter = () => {
      canvas.style.cursor = "pointer";
    };
    const handleCityLeave = () => {
      canvas.style.cursor = "";
    };

    map.on("mousemove", handleMove);
    map.on("click", handleClick);
    map.on("click", CITY_VALUE_LABELS_LAYER_ID, handleCityClick);
    map.on("mouseenter", CITY_VALUE_LABELS_LAYER_ID, handleCityEnter);
    map.on("mouseleave", CITY_VALUE_LABELS_LAYER_ID, handleCityLeave);
    canvas.addEventListener("mouseleave", handleLeave);

    return () => {
      map.off("mousemove", handleMove);
      map.off("click", handleClick);
      map.off("click", CITY_VALUE_LABELS_LAYER_ID, handleCityClick);
      map.off("mouseenter", CITY_VALUE_LABELS_LAYER_ID, handleCityEnter);
      map.off("mouseleave", CITY_VALUE_LABELS_LAYER_ID, handleCityLeave);
      canvas.removeEventListener("mouseleave", handleLeave);
      canvas.style.cursor = "";
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
      <div ref={mapSlotRef} className="viewer-map-slot absolute inset-0">
        <div
          ref={mapContainerRef}
          className="h-full w-full"
          style={{ backgroundColor: getMapBackgroundColor(basemapMode) }}
          role="img"
          aria-label="Weather map"
        />
      </div>

      {pressureCenterScreenLabels.map((item) => (
        <div
          key={item.id}
          className={`map-pressure-center map-pressure-center--${item.type.toLowerCase()}`}
          style={{
            left: item.x,
            top: item.y,
          }}
          aria-hidden="true"
        >
          <div className="map-pressure-center__letter">{item.type}</div>
          {item.valueLabel && <div className="map-pressure-center__value">{item.valueLabel}</div>}
        </div>
      ))}

      {contourScreenLabels.map((item) => (
        <div
          key={item.id}
          className="pointer-events-none absolute z-[35] rounded-[3px] px-1 font-mono text-[10px] font-semibold leading-none tracking-normal shadow-sm"
          style={{
            left: item.x,
            top: item.y,
            transform: `translate(-50%, -50%) rotate(${item.angle}deg)`,
            color: CONTOUR_LABEL_COLOR,
            textShadow: CONTOUR_LABEL_SHADOW,
          }}
          aria-hidden="true"
        >
          {item.label}
        </div>
      ))}

      {(showZoomControls || legendButtonVisible) && (
        <div
          className="pointer-events-none fixed left-4 z-50 flex flex-col gap-2"
          style={{ top: isDesktopLayout ? "calc(4.5rem + 10px)" : "calc(3.5rem + 1rem)" }}
        >
          {showZoomControls && (
            <div className="glass pointer-events-auto overflow-hidden rounded-xl">
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
            </div>
          )}
          {legendButtonVisible && (
            <div className="glass pointer-events-auto overflow-hidden rounded-xl">
              <button
                type="button"
                className={`flex h-[34px] w-[34px] items-center justify-center transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring ${legendButtonActive ? "bg-white/[0.12] text-white" : "text-white/60 hover:bg-white/[0.07] hover:text-white/90"}`}
                onClick={onLegendButtonClick}
                aria-label="Toggle legend"
                title="Legend"
              >
                <Palette className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
