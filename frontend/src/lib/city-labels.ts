import type maplibregl from "maplibre-gl";
import type { LayerSpecification } from "maplibre-gl";
import type { GeoJSON } from "geojson";

export const CITIES_GEOJSON_URL = "https://api.cartosky.com/static/cities/v1/cities_conus_can.json";

export const CITIES_STATIC_SOURCE_ID = "cities-static";
export const CITY_LABEL_CANDIDATES_LAYER_ID = "city-label-candidates";
export const CITY_VALUE_LABELS_SOURCE_ID = "city-value-labels";
export const CITY_VALUE_LABELS_LAYER_ID = "city-value-labels";

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

/**
 * Adds the city-label MapLibre sources and layers used by the zoom-adaptive
 * city label system. Fire-and-forget: handles its own errors and never throws.
 *
 * - `cities-static` / `city-label-candidates`: invisible symbol layer that runs
 *   MapLibre's collision solver over the candidate cities so Phase 3 can sample
 *   which labels survive at the current zoom.
 * - `city-value-labels`: the visible layer Phase 3 will populate with sampled
 *   values. Collision is already resolved upstream, so overlap is allowed here.
 */
export async function initCityLayers(map: maplibregl.Map): Promise<void> {
  try {
    // Guard against double-init (e.g. style reloads re-running the load path).
    if (map.getSource(CITIES_STATIC_SOURCE_ID)) {
      return;
    }

    // The CartoSky basemap uses raster PNG tiles, so the style has no `glyphs`
    // configured. MapLibre requires glyphs before any symbol layer can render
    // `text-field`. setGlyphs() (MapLibre GL JS v3+) sets them without a full
    // style reload, preserving existing sources/layers.
    if (!(map.getStyle() as any).glyphs) {
      map.setGlyphs("https://fonts.openmaptiles.org/{fontstack}/{range}.pbf");
    }

    const response = await fetch(CITIES_GEOJSON_URL);
    if (!response.ok) {
      throw new Error(`Failed to fetch city labels: ${response.status} ${response.statusText}`);
    }
    citiesStaticData = (await response.json()) as GeoJSON.FeatureCollection;

    // The style may have been torn down while the fetch was in flight.
    if (map.getSource(CITIES_STATIC_SOURCE_ID)) {
      return;
    }

    map.addSource(CITIES_STATIC_SOURCE_ID, {
      type: "geojson",
      data: citiesStaticData,
    });

    map.addLayer({
      id: CITY_LABEL_CANDIDATES_LAYER_ID,
      type: "symbol",
      source: CITIES_STATIC_SOURCE_ID,
      filter: CITY_CANDIDATE_ZOOM_FILTER as any,
      layout: {
        "text-field": ["get", "name"] as any,
        "text-allow-overlap": false,
        "symbol-sort-key": ["-", ["get", "pop_max"]] as any,
        "text-size": 0,
      },
      paint: {
        "text-opacity": 0,
      },
    } as LayerSpecification);

    map.addSource(CITY_VALUE_LABELS_SOURCE_ID, {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });

    map.addLayer({
      id: CITY_VALUE_LABELS_LAYER_ID,
      type: "symbol",
      source: CITY_VALUE_LABELS_SOURCE_ID,
      layout: {
        "text-field": [
          "format",
          ["get", "name"], { "font-scale": 0.85 },
          "\n", {},
          ["coalesce", ["get", "value_label"], "…"], { "font-scale": 1.0 },
        ] as any,
        // Collision is already resolved by the candidate layer, so the visible
        // value labels are allowed to overlap one another freely.
        "text-allow-overlap": true,
        "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
        "text-size": 12,
        "text-anchor": "top",
      },
      paint: {
        "text-color": "#ffffff",
        "text-halo-color": "#000000",
        "text-halo-width": 1.5,
      },
    } as LayerSpecification);
  } catch (error) {
    console.warn("[city-labels] Failed to initialize city label layers", error);
  }
}
