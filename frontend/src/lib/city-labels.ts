import type maplibregl from "maplibre-gl";
import type { LayerSpecification } from "maplibre-gl";
import type { GeoJSON } from "geojson";

export const CITIES_GEOJSON_URL = "https://api.cartosky.com/static/cities/v1/cities_conus_can.json";

export const CITIES_STATIC_SOURCE_ID = "cities-static";
export const CITY_LABEL_CANDIDATES_LAYER_ID = "city-label-candidates";
export const CITY_VALUE_LABELS_SOURCE_ID = "city-value-labels";
export const CITY_VALUE_LABELS_LAYER_ID = "city-value-labels";

/** CORS-enabled glyph endpoint (Stadia / OpenMapTiles font stack). */
const CITY_LABEL_GLYPHS_URL =
  "https://tiles.stadiamaps.com/fonts/{fontstack}/{range}.pbf";

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

/** Keep city label symbol layers above weather overlays and basemap labels. */
export function moveCityLabelLayersToTop(map: maplibregl.Map): void {
  if (!map.getLayer("twf-labels")) {
    return;
  }
  if (map.getLayer(CITY_LABEL_CANDIDATES_LAYER_ID)) {
    map.moveLayer(CITY_LABEL_CANDIDATES_LAYER_ID);
  }
  if (map.getLayer(CITY_VALUE_LABELS_LAYER_ID)) {
    map.moveLayer(CITY_VALUE_LABELS_LAYER_ID);
  }
}

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
export async function initCityLayers(map: maplibregl.Map): Promise<boolean> {
  try {
    // Guard against double-init (e.g. style reloads re-running the load path).
    if (map.getSource(CITIES_STATIC_SOURCE_ID)) {
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
        "text-font": ["Noto Sans Regular"],
        "text-size": 12,
        "text-anchor": "top",
      },
      paint: {
        "text-color": "#ffffff",
        "text-halo-color": "#000000",
        "text-halo-width": 1.5,
      },
    } as LayerSpecification);

    // The candidate query returns [] until the source finishes loading into the
    // tile system. Trigger one repaint when it lands so onFrameVisible fires
    // again and the now-queryable candidates get sampled and labeled.
    //
    // Note: a one-shot map.once("sourcedata") would be consumed by the first
    // sourcedata event from ANY source (basemap/boundary tiles fire these
    // constantly during load), so it rarely matches cities-static. Use a
    // self-removing handler that detaches only once our source has loaded.
    const onCitiesSourceLoaded = (e: maplibregl.MapSourceDataEvent) => {
      if (e.sourceId === CITIES_STATIC_SOURCE_ID && e.isSourceLoaded) {
        map.off("sourcedata", onCitiesSourceLoaded);
        moveCityLabelLayersToTop(map);
        map.triggerRepaint();
      }
    };
    map.on("sourcedata", onCitiesSourceLoaded);
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
};

// Accepts a MapLibre map instance, queries the invisible candidate layer,
// and returns the collision-resolved visible city set for this viewport.
// Returns empty array if the source isn't loaded yet.
function cityRankEligibleAtZoom(rank: number, zoom: number): boolean {
  if (rank === 1) return zoom >= 4;
  if (rank === 2) return zoom >= 5;
  if (rank === 3) return zoom >= 6;
  if (rank === 4) return zoom >= 7;
  if (rank === 5) return zoom >= 9;
  return false;
}

function cityPointsFromFeatures(features: maplibregl.MapGeoJSONFeature[]): CityLabelPoint[] {
  const seen = new Set<string>();
  const points: CityLabelPoint[] = [];
  for (const feature of features) {
    const name = String(feature.properties?.name ?? "").trim();
    if (!name || seen.has(name)) {
      continue;
    }
    if (feature.geometry?.type !== "Point") {
      continue;
    }
    const [lng, lat] = feature.geometry.coordinates;
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
      continue;
    }
    seen.add(name);
    points.push({
      id: name, // use name as stable ID — f.id is always undefined for Natural Earth features
      name,
      lng,
      lat,
    });
  }
  return points;
}

export function queryVisibleCityPoints(map: maplibregl.Map): CityLabelPoint[] {
  if (!map.getSource(CITIES_STATIC_SOURCE_ID) || !map.getLayer(CITY_LABEL_CANDIDATES_LAYER_ID)) {
    return [];
  }

  const rendered = map.queryRenderedFeatures(undefined, {
    layers: [CITY_LABEL_CANDIDATES_LAYER_ID],
  });
  const renderedPoints = cityPointsFromFeatures(rendered);
  if (renderedPoints.length > 0) {
    return renderedPoints;
  }

  // Before the first symbol-placement pass completes, queryRenderedFeatures can
  // still be empty even though the source is loaded. Fall back to viewport
  // features filtered by the same zoom/rank gates as the candidate layer.
  if (!map.isSourceLoaded(CITIES_STATIC_SOURCE_ID)) {
    return [];
  }
  const zoom = map.getZoom();
  const bounds = map.getBounds();
  const sourceFeatures = map.querySourceFeatures(CITIES_STATIC_SOURCE_ID);
  const viewportFeatures = sourceFeatures.filter((feature) => {
    if (feature.geometry?.type !== "Point") {
      return false;
    }
    const rank = Number(feature.properties?.rank);
    if (!Number.isFinite(rank) || !cityRankEligibleAtZoom(rank, zoom)) {
      return false;
    }
    const [lng, lat] = feature.geometry.coordinates;
    return bounds.contains([lng, lat]);
  });
  viewportFeatures.sort(
    (a, b) => Number(b.properties?.pop_max ?? 0) - Number(a.properties?.pop_max ?? 0),
  );
  return cityPointsFromFeatures(viewportFeatures).slice(0, 80);
}

// Pushes a sampled FeatureCollection to city-value-labels.
// values: Record<id, number|null>, units: string (e.g. "°F")
export function updateCityValueLabels(
  map: maplibregl.Map,
  points: CityLabelPoint[],
  values: Record<string, number | null>,
  units: string,
): void {
  const source = map.getSource(CITY_VALUE_LABELS_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
  if (!source) return;
  const features: GeoJSON.Feature[] = points.map((p) => {
    const raw = values[p.id];
    const num = typeof raw === "number" && Number.isFinite(raw) ? raw : null;
    const valueLabel = num !== null
      ? `${Math.round(num * 10) / 10}${units}`
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
