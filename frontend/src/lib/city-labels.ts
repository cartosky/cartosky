import type maplibregl from "maplibre-gl";
import type { LayerSpecification } from "maplibre-gl";
import type { GeoJSON } from "geojson";

export const CITIES_GEOJSON_URL = "https://api.cartosky.com/static/cities/v1/cities_conus_can_v2.json";

export const CITIES_STATIC_SOURCE_ID = "cities-static";
export const CITY_LABEL_CANDIDATES_LAYER_ID = "city-label-candidates";
export const CITY_VALUE_LABELS_SOURCE_ID = "city-value-labels";
export const CITY_VALUE_LABEL_NAMES_LAYER_ID = "city-value-label-names";
export const CITY_VALUE_LABELS_LAYER_ID = "city-value-labels";

/** CORS-enabled glyph endpoint (Stadia / OpenMapTiles font stack). */
const CITY_LABEL_GLYPHS_URL =
  "https://tiles.stadiamaps.com/fonts/{fontstack}/{range}.pbf";
const CITY_VALUE_PILL_IMAGE_ID = "city-value-label-pill";
const CITY_LABEL_COLLISION_GAP_PX = 8;
const CITY_VALUE_LABEL_MAX_COUNT = 42;

/**
 * Loaded city candidate data. Plain module-level ref (not React state) so the
 * Phase 3 sampling wiring can read it synchronously without re-rendering.
 */
export let citiesStaticData: GeoJSON.FeatureCollection | null = null;

/**
 * Zoom/rank gating for which city candidates are eligible at a given zoom.
 * Higher-rank (less prominent) cities only become candidates as you zoom in.
 */
const CITY_CANDIDATE_ZOOM_FILTER = [
  "any",
  ["all", [">=", ["zoom"], 4], ["==", ["get", "rank"], 1]],
  ["all", [">=", ["zoom"], 5], ["==", ["get", "rank"], 2]],
  ["all", [">=", ["zoom"], 6], ["==", ["get", "rank"], 3]],
  ["all", [">=", ["zoom"], 7], ["==", ["get", "rank"], 4]],
  ["all", [">=", ["zoom"], 9], ["==", ["get", "rank"], 5]],
];

type ScreenRect = {
  left: number;
  top: number;
  right: number;
  bottom: number;
};

function addRoundedRectPath(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): void {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function ensureCityValuePillImage(map: maplibregl.Map): void {
  if (map.hasImage(CITY_VALUE_PILL_IMAGE_ID) || typeof document === "undefined") {
    return;
  }

  const width = 44;
  const height = 24;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }

  ctx.clearRect(0, 0, width, height);
  addRoundedRectPath(ctx, 1, 1, width - 2, height - 2, 9);
  ctx.fillStyle = "rgba(25, 29, 39, 0.90)";
  ctx.fill();
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.16)";
  ctx.stroke();
  addRoundedRectPath(ctx, 2, 2, width - 4, height - 4, 8);
  const highlight = ctx.createLinearGradient(0, 2, 0, height - 2);
  highlight.addColorStop(0, "rgba(255, 255, 255, 0.12)");
  highlight.addColorStop(0.35, "rgba(255, 255, 255, 0.04)");
  highlight.addColorStop(1, "rgba(255, 255, 255, 0)");
  ctx.fillStyle = highlight;
  ctx.fill();

  map.addImage(CITY_VALUE_PILL_IMAGE_ID, ctx.getImageData(0, 0, width, height), {
    stretchX: [[9, width - 9]],
    stretchY: [[9, height - 9]],
    content: [7, 3, width - 7, height - 3],
  });
}

function estimateCityLabelRect(point: { x: number; y: number }, name: string): ScreenRect {
  const nameWidth = Math.min(112, Math.max(32, name.length * 5.9));
  const valueWidth = 52;
  const width = Math.max(nameWidth, valueWidth) + 10;
  return {
    left: point.x - width / 2,
    right: point.x + width / 2,
    top: point.y - 16,
    bottom: point.y + 30,
  };
}

function cityLabelLimitForZoom(zoom: number): number {
  if (zoom < 5) return 24;
  if (zoom < 6) return 32;
  return CITY_VALUE_LABEL_MAX_COUNT;
}

function intersectsRect(left: ScreenRect, right: ScreenRect): boolean {
  return (
    left.left < right.right + CITY_LABEL_COLLISION_GAP_PX
    && left.right > right.left - CITY_LABEL_COLLISION_GAP_PX
    && left.top < right.bottom + CITY_LABEL_COLLISION_GAP_PX
    && left.bottom > right.top - CITY_LABEL_COLLISION_GAP_PX
  );
}

/** Keep city label symbol layers above weather overlays and basemap labels. */
export function moveCityLabelLayersToTop(map: maplibregl.Map): void {
  if (map.getLayer(CITY_LABEL_CANDIDATES_LAYER_ID)) {
    map.moveLayer(CITY_LABEL_CANDIDATES_LAYER_ID);
  }
  if (map.getLayer(CITY_VALUE_LABEL_NAMES_LAYER_ID)) {
    map.moveLayer(CITY_VALUE_LABEL_NAMES_LAYER_ID);
  }
  if (map.getLayer(CITY_VALUE_LABELS_LAYER_ID)) {
    map.moveLayer(CITY_VALUE_LABELS_LAYER_ID);
  }
}

export function setCityLabelNameOnlyMode(map: maplibregl.Map, nameOnly: boolean): void {
  if (!map.getLayer(CITY_LABEL_CANDIDATES_LAYER_ID)) return;
  // Show the candidate layer as visible name labels when no values are shown
  map.setPaintProperty(
    CITY_LABEL_CANDIDATES_LAYER_ID,
    "text-opacity",
    nameOnly ? 1 : 0
  );
  // In name-only mode, let the candidate symbol layer participate in normal
  // collision placement so dense clusters do not visually overlap.
  map.setLayoutProperty(
    CITY_LABEL_CANDIDATES_LAYER_ID,
    "text-allow-overlap",
    nameOnly ? false : true
  );
  map.setLayoutProperty(
    CITY_LABEL_CANDIDATES_LAYER_ID,
    "text-ignore-placement",
    nameOnly ? false : true
  );
  // Hide the value pill layer entirely in name-only mode
  if (map.getLayer(CITY_VALUE_LABELS_LAYER_ID)) {
    map.setLayoutProperty(
      CITY_VALUE_LABELS_LAYER_ID,
      "visibility",
      nameOnly ? "none" : "visible"
    );
  }
  if (map.getLayer(CITY_VALUE_LABEL_NAMES_LAYER_ID)) {
    map.setLayoutProperty(
      CITY_VALUE_LABEL_NAMES_LAYER_ID,
      "visibility",
      nameOnly ? "none" : "visible"
    );
  }
}

/**
 * Adds the city-label MapLibre sources and layers used by the zoom-adaptive
 * city label system. Fire-and-forget: handles its own errors and never throws.
 *
 * - `cities-static` / `city-label-candidates`: invisible data layer kept for
 *   style participation and future MapLibre queries.
 * - `city-value-labels`: the visible sampled value source/layers. The selected
 *   cities are pre-thinned in screen space before sampling.
 */
export async function initCityLayers(map: maplibregl.Map): Promise<boolean> {
  try {
    // Guard against double-init (e.g. style reloads re-running the load path).
    if (map.getSource(CITIES_STATIC_SOURCE_ID)) {
      ensureCityValuePillImage(map);
      return true;
    }

    // The CartoSky basemap uses raster PNG tiles, so the style has no `glyphs`
    // configured. MapLibre requires glyphs before any symbol layer can render
    // `text-field`. setGlyphs() (MapLibre GL JS v3+) sets them without a full
    // style reload, preserving existing sources/layers.
    const styleGlyphs = (map.getStyle() as { glyphs?: string }).glyphs;
    if (styleGlyphs !== CITY_LABEL_GLYPHS_URL) {
      // setGlyphs() in MapLibre GL JS v4 schedules an internal style update that
      // settles asynchronously; calling addSource/addLayer before it lands races
      // the update and throws. Wait for the map to go idle before proceeding.
      await new Promise<void>((resolve) => {
        map.setGlyphs(CITY_LABEL_GLYPHS_URL);
        map.once("idle", resolve);
      });
    }

    const response = await fetch(CITIES_GEOJSON_URL);
    if (!response.ok) {
      throw new Error(`Failed to fetch city labels: ${response.status} ${response.statusText}`);
    }
    citiesStaticData = (await response.json()) as GeoJSON.FeatureCollection;

    // The style may have been torn down while the fetch was in flight.
    if (map.getSource(CITIES_STATIC_SOURCE_ID)) {
      return true;
    }

    map.addSource(CITIES_STATIC_SOURCE_ID, {
      type: "geojson",
      data: citiesStaticData,
    });

    map.addLayer({
      id: CITY_LABEL_CANDIDATES_LAYER_ID,
      type: "symbol",
      source: CITIES_STATIC_SOURCE_ID,
      minzoom: 4,
      filter: CITY_CANDIDATE_ZOOM_FILTER as any,
      layout: {
        "text-field": ["get", "name"] as any,
        "text-font": ["Noto Sans Regular"],
        // Pure data-delivery layer: overlap + ignore-placement disable collision
        // so EVERY zoom/rank-filtered city renders (invisibly) and is therefore
        // queryable. Visual collision is handled by the city-value-labels layer.
        "text-allow-overlap": true,
        "text-ignore-placement": true,
        // MapLibre sorts ascending (lower key = higher priority), so negate
        // pop_max to keep high-population cities first.
        "symbol-sort-key": ["-", ["get", "pop_max"]] as any,
        // text-size must be > 0 or MapLibre skips layout and queryRenderedFeatures
        // returns nothing. Opacity hides the text; collision boxes stay real.
        "text-size": 12,
      },
      paint: {
        // Default hidden; setCityLabelNameOnlyMode() flips opacity to 1 when the
        // active variable/model has no scalar field to sample. Color + halo make
        // it legible against the basemap when shown.
        "text-opacity": 0,
        "text-color": "rgba(226, 244, 255, 0.90)",
        "text-halo-color": "rgba(4, 16, 30, 0.86)",
        "text-halo-width": 1.1,
        "text-halo-blur": 0,
      },
    } as LayerSpecification);

    map.addSource(CITY_VALUE_LABELS_SOURCE_ID, {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });

    ensureCityValuePillImage(map);

    map.addLayer({
      id: CITY_VALUE_LABEL_NAMES_LAYER_ID,
      type: "symbol",
      source: CITY_VALUE_LABELS_SOURCE_ID,
      layout: {
        "text-field": ["get", "name"] as any,
        "text-font": ["Noto Sans Bold", "Noto Sans Regular"],
        "text-size": 11,
        "text-anchor": "top",
        "text-offset": [0, 0.92],
        "text-allow-overlap": true,
        "text-ignore-placement": true,
      },
      paint: {
        "text-color": "rgba(246, 250, 255, 0.94)",
        "text-halo-color": "rgba(25, 29, 39, 0.88)",
        "text-halo-width": 2.1,
        "text-halo-blur": 0,
      },
    } as LayerSpecification);

    map.addLayer({
      id: CITY_VALUE_LABELS_LAYER_ID,
      type: "symbol",
      source: CITY_VALUE_LABELS_SOURCE_ID,
      layout: {
        "icon-image": CITY_VALUE_PILL_IMAGE_ID,
        "icon-text-fit": "both",
        "icon-text-fit-padding": [3, 8, 3, 8],
        "icon-anchor": "center",
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
        "text-field": ["coalesce", ["get", "value_label"], "…"] as any,
        // Collision is handled before sampling in queryVisibleCityPoints(), so
        // this layer just renders the selected city/value pairs.
        "text-allow-overlap": true,
        "text-ignore-placement": true,
        "text-font": ["Noto Sans Bold", "Noto Sans Regular"],
        "text-size": 12,
        "text-anchor": "center",
      },
      paint: {
        "text-color": "rgba(245, 252, 255, 0.98)",
        "text-halo-color": "rgba(25, 29, 39, 0.62)",
        "text-halo-width": 0.4,
      },
    } as LayerSpecification);

    // Put the city label layers on top now that they exist. The
    // repaint-once-cities-static-loads behavior lives in a dedicated effect in
    // map-canvas.tsx; a sourcedata listener here too would double-repaint.
    moveCityLabelLayersToTop(map);
    map.triggerRepaint();
    return true;
  } catch (error) {
    console.warn("[city-labels] Failed to initialize city label layers", error);
    return false;
  }
}

export type CityLabelPoint = {
  id: string;
  name: string;
  lng: number;
  lat: number;
  pop_max: number;
};

// Computes visible candidate cities directly from the loaded GeoJSON using the
// map's current bounds + zoom. Deliberately does NOT use queryRenderedFeatures:
// that returns [] until glyph PBFs finish downloading (only after the first font
// cache), which broke city labels on initial load.
export function queryVisibleCityPoints(map: maplibregl.Map): CityLabelPoint[] {
  if (!citiesStaticData) return [];

  const bounds = map.getBounds();
  const zoom = map.getZoom();

  // Match the same zoom/rank thresholds as CITY_CANDIDATE_ZOOM_FILTER.
  const maxRank =
    zoom >= 9 ? 5 :
    zoom >= 7 ? 4 :
    zoom >= 6 ? 3 :
    zoom >= 5 ? 2 :
    zoom >= 4 ? 1 : 0;

  if (maxRank === 0) return [];

  const results: CityLabelPoint[] = [];
  for (const feature of citiesStaticData.features) {
    const rank = feature.properties?.rank as number;
    if (!rank || rank > maxRank) continue;

    const geometry = feature.geometry;
    if (geometry?.type !== "Point") continue;
    const [lng, lat] = geometry.coordinates;
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) continue;
    if (!bounds.contains([lng, lat])) continue;

    const name = String(feature.properties?.name ?? "").trim();
    if (!name) continue;

    results.push({ id: name, name, lng, lat, pop_max: feature.properties?.pop_max ?? 0 });
  }

  // Sort by pop_max descending so high-population cities are sampled first.
  results.sort((a, b) => b.pop_max - a.pop_max);

  const accepted: CityLabelPoint[] = [];
  const occupiedRects: ScreenRect[] = [];
  for (const point of results) {
    const projected = map.project([point.lng, point.lat]);
    if (!Number.isFinite(projected.x) || !Number.isFinite(projected.y)) {
      continue;
    }
    const rect = estimateCityLabelRect(projected, point.name);
    if (occupiedRects.some((occupied) => intersectsRect(rect, occupied))) {
      continue;
    }
    accepted.push(point);
    occupiedRects.push(rect);
    if (accepted.length >= cityLabelLimitForZoom(zoom)) {
      break;
    }
  }

  // Cap visible labels by zoom to keep the larger value-first treatment readable.
  return accepted;
}

// Pushes a sampled FeatureCollection to city-value-labels.
// values: Record<id, number|null>
export function updateCityValueLabels(
  map: maplibregl.Map,
  points: CityLabelPoint[],
  values: Record<string, number | null>,
  _units: string,
): void {
  const source = map.getSource(CITY_VALUE_LABELS_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
  if (!source) return;
  const features: GeoJSON.Feature[] = points.map((p) => {
    const raw = values[p.id];
    const num = typeof raw === "number" && Number.isFinite(raw) ? raw : null;
    const rounded = num !== null ? Math.round(num * 10) / 10 : null;
    const valueLabel = rounded !== null
      ? Number.isInteger(rounded) ? String(Math.round(rounded)) : rounded.toFixed(1)
      : null;
    return {
      type: "Feature",
      geometry: { type: "Point", coordinates: [p.lng, p.lat] },
      properties: { name: p.name, value_label: valueLabel },
    };
  });
  source.setData({ type: "FeatureCollection", features });
}

// Clears city-value-labels (call on variable switch, model switch, etc.)
export function clearCityValueLabels(map: maplibregl.Map): void {
  const source = map.getSource(CITY_VALUE_LABELS_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
  if (!source) return;
  source.setData({ type: "FeatureCollection", features: [] });
}
