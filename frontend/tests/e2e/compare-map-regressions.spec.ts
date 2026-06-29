import { test, expect } from "@playwright/test";

import { resolveCityFrameSamplingOutcome } from "../../src/lib/city-label-sampling";
import type { CityLabelPoint } from "../../src/lib/city-labels";
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
