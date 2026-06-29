import { test, expect } from "@playwright/test";
import type { LayerSpecification } from "maplibre-gl";

import { resolveCityFrameSamplingOutcome } from "../../src/lib/city-label-sampling";
import {
  CITY_LABEL_CANDIDATES_LAYER_ID,
  initCityLayers,
  queryVisibleCityPoints,
  shouldRefreshCityLabelsAfterSelectionReset,
  type CityLabelPoint,
} from "../../src/lib/city-labels";
import { resolveGridContourGeoJsonUrl } from "../../src/lib/grid-contours";
import { buildMapRegionViews } from "../../src/lib/map-region-views";

const cityPoints: CityLabelPoint[] = [
  { id: "Chicago", name: "Chicago", lat: 41.8781, lng: -87.6298, pop_max: 2_746_388 },
  { id: "St. Louis", name: "St. Louis", lat: 38.627, lng: -90.1994, pop_max: 301_578 },
];

test("compare grid contour URL resolves from manifest contour metadata", () => {
  const url = resolveGridContourGeoJsonUrl({
    model: "gfs",
    run: "20260624_00z",
    variable: "hgt500_anom",
    hour: 120,
    gridManifest: {
      model: "gfs",
      run: "20260624_00z",
      var: "hgt500_anom",
      bbox: [-130, 15, -55, 60],
      grid: {
        width: 2,
        height: 2,
        dtype: "uint16",
        endianness: "little",
        scale: 1,
        offset: 0,
        nodata: 65535,
      },
      palette: {},
      contours: {
        height: { format: "geojson", path: "contours/height.geojson" },
      },
      lods: [],
    },
    frameRows: [],
  });

  expect(url).toContain("/api/v4/gfs/20260624_00z/hgt500_anom/120/contours/height");
});

test("compare region views preserve wide-map zoom limits from region presets", () => {
  const views = buildMapRegionViews({
    conus: {
      bbox: [-125, 24, -66, 50],
      defaultCenter: [-98.58, 39.83],
      defaultZoom: 4,
      minZoom: 2,
      maxZoom: 14,
    },
    na: {
      bbox: [-168, 5, -40, 82],
      defaultCenter: [-100, 45],
      defaultZoom: 2.3,
      minZoom: 1.5,
      maxZoom: 12,
    },
  });

  expect(views.conus.minZoom).toBe(2);
  expect(views.na.minZoom).toBe(1.5);
  expect(views.na.bbox).toEqual([-154, 12, -48, 72]);
});

test("city labels request batch fallback when visible grid bytes are not sampleable", () => {
  const outcome = resolveCityFrameSamplingOutcome({
    frameHour: 120,
    selectionEpoch: 42,
    selectionKey: "ecmwf:20260629_00z:tmp2m:conus:-",
    points: cityPoints,
    sampled: null,
  });

  expect(outcome.kind).toBe("fallback");
  expect(outcome.payload).toEqual({
    frameHour: 120,
    selectionEpoch: 42,
    selectionKey: "ecmwf:20260629_00z:tmp2m:conus:-",
    gridSampled: false,
    points: cityPoints,
    values: {},
    units: "",
  });
});

test("city labels use direct grid samples when they are available", () => {
  const outcome = resolveCityFrameSamplingOutcome({
    frameHour: 120,
    selectionEpoch: 42,
    selectionKey: "ecmwf:20260629_00z:tmp2m:conus:-",
    points: cityPoints,
    sampled: {
      values: { Chicago: 72.4, "St. Louis": 77.1 },
      units: "F",
    },
  });

  expect(outcome.kind).toBe("direct");
  expect(outcome.values).toEqual({ Chicago: 72.4, "St. Louis": 77.1 });
  expect(outcome.units).toBe("F");
});

test("city label candidates include sparse-region rank 2 and 3 cities at fitted CONUS zoom", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [-116.20345, 43.6135] },
        properties: { name: "Boise", rank: 2, pop_max: 235684 },
      },
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [-104.82025, 41.13998] },
        properties: { name: "Cheyenne", rank: 3, pop_max: 65132 },
      },
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [-100.78374, 46.80833] },
        properties: { name: "Bismarck", rank: 2, pop_max: 75092 },
      },
    ],
  }), { status: 200 })) as typeof fetch;

  const map = {
    getSource: () => undefined,
    getStyle: () => ({}),
    setGlyphs: () => undefined,
    once: (_event: string, callback: () => void) => callback(),
    addSource: () => undefined,
    hasImage: () => true,
    addImage: () => undefined,
    addLayer: () => undefined,
    getLayer: () => undefined,
    moveLayer: () => undefined,
    triggerRepaint: () => undefined,
    getZoom: () => 3.75,
    getBounds: () => ({
      contains: ([lng, lat]: [number, number]) => lng >= -134 && lng <= -60 && lat >= 24 && lat <= 55,
    }),
    project: ([lng, lat]: [number, number]) => ({
      x: (lng + 134) * 16,
      y: (55 - lat) * 16,
    }),
  };

  try {
    await initCityLayers(map as never);
    const cityNames = queryVisibleCityPoints(map as never).map((point) => point.name);

    expect(cityNames).toEqual(["Boise", "Bismarck", "Cheyenne"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("name-only city labels add collision padding for lower-zoom dense regions", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({
    type: "FeatureCollection",
    features: [],
  }), { status: 200 })) as typeof fetch;

  const layers: LayerSpecification[] = [];
  const map = {
    getSource: () => undefined,
    getStyle: () => ({}),
    setGlyphs: () => undefined,
    once: (_event: string, callback: () => void) => callback(),
    addSource: () => undefined,
    hasImage: () => true,
    addImage: () => undefined,
    addLayer: (layer: LayerSpecification) => { layers.push(layer); },
    getLayer: () => undefined,
    moveLayer: () => undefined,
    triggerRepaint: () => undefined,
  };

  try {
    await initCityLayers(map as never);
    const candidateLayer = layers.find((layer) => layer.id === CITY_LABEL_CANDIDATES_LAYER_ID);

    expect(candidateLayer?.layout?.["text-padding"]).toBe(18);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("name-only city labels refresh after product selection reset", () => {
  expect(shouldRefreshCityLabelsAfterSelectionReset({
    cityLabelMode: "name-only",
    pointLabelsEnabled: true,
  })).toBe(true);
  expect(shouldRefreshCityLabelsAfterSelectionReset({
    cityLabelMode: "value",
    pointLabelsEnabled: true,
  })).toBe(false);
  expect(shouldRefreshCityLabelsAfterSelectionReset({
    cityLabelMode: "name-only",
    pointLabelsEnabled: false,
  })).toBe(false);
});
