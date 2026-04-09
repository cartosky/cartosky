import type { Feature, FeatureCollection, Point } from "geojson";

export type AnchorFeatureProperties = {
  st?: string | null;
  state?: string | null;
  city?: string | null;
  label?: string;
  active?: boolean;
  value?: number | null;
  units?: string | null;
  wfo?: string | null;
  gridX?: number | null;
  gridY?: number | null;
};

export type AnchorFeature = Feature<Point, AnchorFeatureProperties> & {
  id: string;
};

export type AnchorFeatureCollection = FeatureCollection<Point, AnchorFeatureProperties>;

export type AnchorBatchPoint = {
  id: string;
  lat: number;
  lon: number;
};

export type AnchorBatchResponse = {
  units: string;
  values: Record<string, number | null>;
};

export type ActiveAnchorLabel = {
  id: string;
  lngLat: [number, number];
  label: string;
  cityName: string;
  priority: number;
};

export type AnchorDisplayMode = "always" | "active-only" | "hidden";

export type AnchorDisplayRule = {
  mode: AnchorDisplayMode;
  threshold?: number;
};

const DEFAULT_ANCHOR_DISPLAY_RULE: AnchorDisplayRule = Object.freeze({ mode: "always" });

export const ANCHOR_DISPLAY_RULES: Readonly<Record<string, AnchorDisplayRule>> = Object.freeze({
  tmp2m: { mode: "always" },
  dpt2m: { mode: "always" },
  dewpoint2m: { mode: "always" },
  dewpoint: { mode: "always" },
  wspd850: { mode: "hidden" },
  wspd300: { mode: "hidden" },
  wspd10m: { mode: "always" },
  wgst10m: { mode: "always" },
  sbcape: { mode: "active-only", threshold: 100 },
  mlcape: { mode: "active-only", threshold: 100 },
  mucape: { mode: "active-only", threshold: 100 },
  pwat: { mode: "active-only", threshold: 0.2 },
  precip_total: { mode: "active-only", threshold: 0.01 },
  snowfall_total: { mode: "active-only", threshold: 0.1 },
  snowfall_kuchera_total: { mode: "active-only", threshold: 0.1 },
  refc: { mode: "active-only", threshold: 15 },
  cref: { mode: "active-only", threshold: 15 },
  reflectivity: { mode: "active-only", threshold: 15 },
  radar_reflectivity: { mode: "active-only", threshold: 15 },
  vort500: { mode: "hidden" },
  radar_ptype: { mode: "hidden" },
  mrms_radar_ptype: { mode: "hidden" },
  ptype_intensity: { mode: "hidden" },
});

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function normalizeAnchorVariableKey(varKey: string): string {
  return varKey.trim().toLowerCase();
}

export function resolveAnchorDisplayRule(varKey: string): AnchorDisplayRule {
  const normalized = normalizeAnchorVariableKey(varKey);
  return ANCHOR_DISPLAY_RULES[normalized] ?? DEFAULT_ANCHOR_DISPLAY_RULE;
}

export function formatAnchorValueLabel(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? String(Math.round(rounded)) : rounded.toFixed(1);
}

function readAnchorString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function readAnchorNumber(value: unknown): number | null {
  const numericValue = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numericValue) ? numericValue : null;
}

function buildAnchorFeatureProperties(
  feature: Feature<Point, AnchorFeatureProperties>,
  overrides?: {
    label?: string;
    active?: boolean;
    value?: number | null;
    units?: string | null;
  }
): AnchorFeatureProperties {
  const sourceProperties = feature.properties ?? {};
  return {
    st: readAnchorString(sourceProperties.st),
    state: readAnchorString(sourceProperties.state),
    city: readAnchorString(sourceProperties.city),
    label: typeof overrides?.label === "string" ? overrides.label : "",
    active: overrides?.active === true,
    value: readAnchorNumber(overrides?.value),
    units: readAnchorString(overrides?.units),
  };
}

export function anchorBatchPointsFromGeoJson(
  collection: AnchorFeatureCollection | null | undefined
): AnchorBatchPoint[] {
  if (!collection || !Array.isArray(collection.features)) {
    return [];
  }

  const points: AnchorBatchPoint[] = [];
  for (const feature of collection.features) {
    const featureId = typeof feature.id === "string" ? feature.id : null;
    const coordinates = feature.geometry?.type === "Point" ? feature.geometry.coordinates : null;
    const lon = Number(coordinates?.[0]);
    const lat = Number(coordinates?.[1]);
    if (!featureId || !Number.isFinite(lat) || !Number.isFinite(lon)) {
      continue;
    }
    points.push({ id: featureId, lat, lon });
  }
  return points;
}

export function buildAnchorDisplayGeoJson(params: {
  baseCollection: AnchorFeatureCollection;
  varKey: string;
  values: Record<string, number | null | undefined>;
  units?: string | null;
}): AnchorFeatureCollection {
  const rule = resolveAnchorDisplayRule(params.varKey);
  const units = typeof params.units === "string" ? params.units : "";

  return {
    type: "FeatureCollection",
    features: params.baseCollection.features.map((feature) => {
      const rawValue = params.values[String(feature.id)];
      const numericValue = Number(rawValue);
      const hasValue = Number.isFinite(numericValue);
      const isActive =
        rule.mode !== "hidden"
        && hasValue
        && (rule.mode !== "active-only" || numericValue > Number(rule.threshold ?? 0));

      return {
        ...feature,
        properties: buildAnchorFeatureProperties(feature, {
          label: isActive ? formatAnchorValueLabel(numericValue) : "",
          active: isActive,
          value: isActive ? numericValue : null,
          units,
        }),
      };
    }),
  };
}

export function buildInactiveAnchorFeatureCollection(
  baseCollection: AnchorFeatureCollection,
  units = ""
): AnchorFeatureCollection {
  return {
    type: "FeatureCollection",
    features: baseCollection.features.map((feature) => ({
      ...feature,
      properties: buildAnchorFeatureProperties(feature, {
        label: "",
        active: false,
        value: null,
        units,
      }),
    })),
  };
}

export function sanitizeAnchorFeatureCollection(
  collection: AnchorFeatureCollection | null | undefined
): AnchorFeatureCollection | null {
  if (!collection) {
    return null;
  }

  return {
    type: "FeatureCollection",
    features: collection.features.map((feature) => ({
      ...feature,
      properties: buildAnchorFeatureProperties(feature as AnchorFeature, {
        label: typeof feature.properties?.label === "string" ? feature.properties.label : "",
        active: feature.properties?.active === true,
        value: readAnchorNumber(feature.properties?.value),
        units: readAnchorString(feature.properties?.units),
      }),
    })),
  };
}

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
  const arc = latSin * latSin + Math.cos(latARadians) * Math.cos(latBRadians) * lonSin * lonSin;

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
    [3, 170],
    [4.5, 125],
    [6, 82],
    [7.5, 52],
    [9, 30],
    [11, 18],
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

  return 18;
}

function thinAnchorMarkers(markers: ActiveAnchorLabel[], zoom: number): ActiveAnchorLabel[] {
  const sorted = [...markers].sort((left, right) => {
    if (left.priority !== right.priority) {
      return left.priority - right.priority;
    }
    return left.id.localeCompare(right.id);
  });

  const collisionRadiusKm = interpolateAnchorCollisionRadiusKm(zoom);
  const accepted: ActiveAnchorLabel[] = [];
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

export function getActiveAnchorLabels(
  collection: AnchorFeatureCollection | null | undefined,
  zoom: number
): ActiveAnchorLabel[] {
  const sanitized = sanitizeAnchorFeatureCollection(collection);
  if (!sanitized) {
    return [];
  }

  const activeMarkers: ActiveAnchorLabel[] = [];
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

export function isAnchorFeatureCollection(value: unknown): value is AnchorFeatureCollection {
  if (!isObject(value) || value.type !== "FeatureCollection" || !Array.isArray(value.features)) {
    return false;
  }

  return value.features.every((feature) => {
    if (!isObject(feature) || feature.type !== "Feature") {
      return false;
    }
    if (typeof feature.id !== "string" || !feature.id.trim()) {
      return false;
    }
    if (!isObject(feature.geometry) || feature.geometry.type !== "Point") {
      return false;
    }
    const coordinates = feature.geometry.coordinates;
    return Array.isArray(coordinates)
      && coordinates.length >= 2
      && Number.isFinite(Number(coordinates[0]))
      && Number.isFinite(Number(coordinates[1]));
  });
}
