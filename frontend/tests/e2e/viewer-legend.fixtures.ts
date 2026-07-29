/**
 * Phase 7 legend contract fixtures
 * (docs/plans/2026-07-29-map-viewer-redesign-phase-7-legend.md, Task 4).
 *
 * Fixed, repo-owned legend metadata covering all four raw kinds
 * (`continuous | discrete | categorical | indexed`) and the three derived
 * presentation modes (`ptype-intensity`, `radar-ptype`, `composite-group`).
 * Every palette mirrors the shape the backend sidecars actually emit
 * (`backend/app/services/colormaps.py`) — never live weather.
 *
 * The per-type numeric ladders are copied from the real palettes on purpose:
 * MRMS radar-ptype and GFS ptype-intensity both have *diverging* per-type
 * levels, so the shared-scale branch of the model is exercised by the one
 * synthetic `radar_ptype` fixture whose four types genuinely share a ladder.
 */
import type { Page } from '@playwright/test';

export const LEGEND_RUN_ID = '20260728_12z';
const RUN_BASE_MS = Date.UTC(2026, 6, 28, 12, 0, 0);
const FRAME_HOURS = [0, 1];

export function legendValidTime(fh: number): string {
  return new Date(RUN_BASE_MS + fh * 3_600_000).toISOString().replace('.000Z', 'Z');
}

// ── Precipitation-type palettes ────────────────────────────────────────────

export type PtypePalette = {
  order: string[];
  levels: Record<string, number[]>;
  colors: Record<string, string[]>;
};

export type FlatPtypePalette = {
  order: string[];
  colors: string[];
  levels: number[];
  breaks: Record<string, { offset: number; count: number }>;
  levelsByType: Record<string, number[]>;
};

/** Same flattening the backend `_build_*_flat_palette` helpers perform. */
export function flattenPtypePalette(palette: PtypePalette): FlatPtypePalette {
  const colors: string[] = [];
  const levels: number[] = [];
  const breaks: Record<string, { offset: number; count: number }> = {};
  const levelsByType: Record<string, number[]> = {};
  let offset = 0;
  for (const key of palette.order) {
    const typeColors = palette.colors[key] ?? [];
    const typeLevels = (palette.levels[key] ?? []).slice(0, typeColors.length);
    const count = Math.min(typeColors.length, typeLevels.length);
    if (count <= 0) continue;
    colors.push(...typeColors.slice(0, count));
    levels.push(...typeLevels.slice(0, count));
    breaks[key] = { offset, count };
    levelsByType[key] = typeLevels.slice(0, count);
    offset += count;
  }
  return { order: palette.order, colors, levels, breaks, levelsByType };
}

/** GFS `ptype_intensity` (indexed, in/hr) — diverging per-type bins. */
export const GFS_PTYPE_INTENSITY: PtypePalette = {
  order: ['rain', 'snow', 'ice'],
  levels: {
    rain: [0.0, 0.01, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5],
    snow: [0.05, 0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0, 7.0],
    ice: [0.01, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.25, 1.5],
  },
  colors: {
    rain: [
      '#b4e6b7', '#9ed1a0', '#89be8a', '#74ab76', '#609961', '#4d864d', '#407f45',
      '#34783c', '#277134', '#1c6a2b', '#126324', '#f5f166', '#f7d95b', '#f9c252',
      '#fbab48', '#fd943f',
    ],
    snow: [
      '#bdd7ff', '#a0c4ff', '#7eb0ff', '#5e9cff', '#4289ff', '#3478f6', '#2f74f0',
      '#1d5dd8', '#1550cc', '#0d47bf',
    ],
    ice: [
      '#fff0f5', '#fff7f3', '#fde0dd', '#fcc5c0', '#faa3a0', '#f768a1', '#ea4b9a',
      '#dd3497', '#c4298e', '#ae017e', '#930086', '#7a0177', '#67006b', '#550061',
      '#49006a', '#3d0052', '#2d0044', '#1a0033',
    ],
  },
};

/** MRMS `mrms_radar_ptype` (indexed, dBZ) — rain's ladder differs from the rest. */
export const MRMS_RADAR_PTYPE: PtypePalette = {
  order: ['rain', 'snow', 'sleet', 'frzr'],
  levels: {
    rain: [5, 10, 15, 20, 23, 25, 28, 30, 33, 35, 38, 40, 43, 45, 48, 50, 55, 60, 65, 70],
    snow: [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 70],
    sleet: [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 70],
    frzr: [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 70],
  },
  colors: {
    rain: [
      '#d7f7cf', '#9cf29a', '#4be85a', '#02fd02', '#01dc02', '#01c501',
      '#00a901', '#008e00', '#80ca01', '#fdf802', '#f0d600', '#e5bc00',
      '#fdae00', '#fd9500', '#fd5f00', '#fd0000', '#d40000', '#bc0000',
      '#f800fd', '#fdfdfd',
    ],
    snow: [
      '#ffffff', '#55ffff', '#4feaff', '#48d3ff', '#42bfff', '#3caaff', '#3693ff',
      '#2a6aee', '#1e40d0', '#110ba7', '#2a009a', '#0c276f', '#540093', '#bc0f9c',
      '#d30085', '#f5007f',
    ],
    sleet: [
      '#ffffff', '#b49dff', '#b788ff', '#c56cff', '#c54ef9', '#c54ef9', '#b730e7',
      '#a913d3', '#a913d3', '#9b02b4', '#bc0f9c', '#a50085', '#c52c7b', '#cf346f',
      '#d83c64', '#e24556',
    ],
    frzr: [
      '#ffffff', '#fbcad0', '#f893ba', '#e96c9f', '#dd88a5', '#dc4f8b', '#d03a80',
      '#c62773', '#bd1366', '#b00145', '#c21230', '#da2d0d', '#e33403', '#f53c00',
      '#f53c00', '#f54603',
    ],
  },
};

const SHARED_DBZ_LADDER = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70];

/** Modeled `radar_ptype` — all four types share one dBZ ladder (shared-scale branch). */
export const SHARED_RADAR_PTYPE: PtypePalette = {
  order: ['rain', 'snow', 'sleet', 'frzr'],
  levels: {
    rain: [...SHARED_DBZ_LADDER],
    snow: [...SHARED_DBZ_LADDER],
    sleet: [...SHARED_DBZ_LADDER],
    frzr: [...SHARED_DBZ_LADDER],
  },
  colors: {
    rain: [
      '#a8f0a8', '#4efb4c', '#2d9e2e', '#155719', '#c8e640', '#feff50', '#f8b422',
      '#f57030', '#d41020', '#c21230', '#8e1a2e', '#5a0715', '#e57743', '#fdfdfd',
    ],
    snow: [
      '#c8ffff', '#55ffff', '#7cc7ff', '#3caaff', '#2f74f0', '#1e40d0', '#181bb4',
      '#2a009a', '#130495', '#172a8f', '#3e65b8', '#7ea6de', '#b9d4ff', '#fdfdfd',
    ],
    sleet: [
      '#ddd0ff', '#b49dff', '#c07dff', '#c54ef9', '#b730e7', '#a913d3', '#b011b8',
      '#bc0f9c', '#d40a4e', '#fd0000', '#f800fd', '#c968d9', '#9854c6', '#fdfdfd',
    ],
    frzr: [
      '#ffd6e1', '#fbcad0', '#ef8fb5', '#dc4f8b', '#cd2e76', '#bd1366', '#cd2039',
      '#da2d0d', '#ee3606', '#fd0000', '#f800fd', '#c368d0', '#9854c6', '#fdfdfd',
    ],
  },
};

// ── Non-ptype palettes ─────────────────────────────────────────────────────

/** `continuous` — a coarse tmp2m-shaped anchor ladder in °F. */
export const CONTINUOUS_STOPS: Array<[number, string]> = [
  [-20, '#8175b4'], [-10, '#70457a'], [0, '#682480'], [10, '#b19bc1'],
  [20, '#8aacd1'], [30, '#004fab'], [40, '#779073'], [50, '#fff19f'],
  [60, '#ffbe42'], [70, '#f98517'], [80, '#e8420e'], [90, '#b30d1d'],
  [100, '#7c1e56'], [110, '#a86bc4'], [120, '#f0d8ff'],
];

const RH_COLORS = [
  '#543004', '#714107', '#8d520b', '#a96c1e', '#c28634', '#d3aa5f', '#e2c786',
  '#efdcad', '#f6ebcd', '#f5f2e8', '#e9f2f1', '#d0ece8', '#a9dcd4', '#79c8bf',
  '#4aaea4', '#2b8b83', '#186a63', '#0b4f49', '#053b36', '#022924',
];

/** `discrete` — rh %, range-labeled entries (the labels ARE the break values). */
export const RH_DISCRETE_ENTRIES = RH_COLORS.map((color, index) => ({
  value: index * 5,
  color,
  label: `${index * 5}-${(index + 1) * 5}`,
}));

/** `categorical` — purely nominal hazard names; no numeric semantics at all. */
export const NOMINAL_CATEGORICAL_ENTRIES = [
  { value: 0, color: '#3a5f3a', label: 'General Thunderstorms' },
  { value: 1, color: '#7fc97f', label: 'Marginal' },
  { value: 2, color: '#f7f04a', label: 'Slight' },
  { value: 3, color: '#e8a33d', label: 'Enhanced' },
];

/** `indexed` — bare 1..n index codes with names: indices are not measurements. */
export const INDEXED_ENTRIES = [
  { value: 1, color: '#2d9e2e', label: 'Rain' },
  { value: 2, color: '#3caaff', label: 'Snow' },
  { value: 3, color: '#b49dff', label: 'Sleet' },
  { value: 4, color: '#dc4f8b', label: 'Freezing Rain' },
];

// ── Fixture definitions ────────────────────────────────────────────────────

export type CompositeLayerFixture = {
  id: string;
  var: string;
  displayName: string;
  kind: string;
  units?: string;
  stops: Array<[number, string]>;
};

export type LegendFixture = {
  name: string;
  model: string;
  variable: string;
  displayName: string;
  legendTitle?: string;
  kind: string;
  units?: string;
  /** Extra frame-meta legend fields (entries / stops / colors + ptype_*). */
  legendMeta: Record<string, unknown>;
  composite?: CompositeLayerFixture[];
};

function ptypeMeta(palette: PtypePalette): Record<string, unknown> {
  const flat = flattenPtypePalette(palette);
  return {
    colors: flat.colors,
    levels: flat.levels,
    ptype_order: flat.order,
    ptype_breaks: flat.breaks,
    ptype_levels: flat.levelsByType,
  };
}

export const LEGEND_FIXTURES: Record<string, LegendFixture> = {
  continuous: {
    name: 'continuous',
    model: 'gfs',
    variable: 'tmp2m',
    displayName: 'Temperature 2m',
    legendTitle: 'Temperature (°F)',
    kind: 'continuous',
    units: 'F',
    legendMeta: { legend_stops: CONTINUOUS_STOPS },
  },
  discrete: {
    name: 'discrete',
    model: 'gfs',
    variable: 'rh',
    displayName: 'Relative Humidity',
    legendTitle: 'Relative Humidity (%)',
    kind: 'discrete',
    units: '%',
    legendMeta: { legend_entries: RH_DISCRETE_ENTRIES },
  },
  categorical: {
    name: 'categorical',
    model: 'gfs',
    variable: 'storm_hazard',
    displayName: 'Storm Hazard',
    legendTitle: 'Storm Hazard',
    kind: 'categorical',
    legendMeta: { legend_entries: NOMINAL_CATEGORICAL_ENTRIES },
  },
  indexed: {
    name: 'indexed',
    model: 'gfs',
    variable: 'ptype_category',
    displayName: 'Precipitation Type',
    legendTitle: 'Precipitation Type',
    kind: 'indexed',
    legendMeta: { legend_entries: INDEXED_ENTRIES },
  },
  ptypeIntensity: {
    name: 'ptypeIntensity',
    model: 'gfs',
    variable: 'ptype_intensity',
    displayName: 'Precipitation Type & Intensity',
    legendTitle: 'Precipitation Type',
    kind: 'indexed',
    units: 'in/hr',
    legendMeta: ptypeMeta(GFS_PTYPE_INTENSITY),
  },
  radarPtype: {
    name: 'radarPtype',
    model: 'mrms',
    variable: 'mrms_radar_ptype',
    displayName: 'Reflectivity + Ptype',
    legendTitle: 'MRMS Reflectivity + Ptype (dBZ)',
    kind: 'indexed',
    units: 'dBZ',
    legendMeta: ptypeMeta(MRMS_RADAR_PTYPE),
  },
  /**
   * The HRRR/NAM per-type reflectivity shape (`radar_ptype_snow` et al.): a
   * plain continuous ladder whose id contains "radar" but which defines NO
   * ptype grouping metadata. Must render as continuous — the verifier's
   * refutation case where radar-ptype mode fabricated a "Rain 1.2–75 dBZ"
   * ramp from the zero-delimiter fallback.
   */
  singleRadarContinuous: {
    name: 'singleRadarContinuous',
    model: 'hrrr',
    variable: 'radar_ptype_snow',
    displayName: 'Snow Reflectivity',
    legendTitle: 'Snow Reflectivity (dBZ)',
    kind: 'continuous',
    units: 'dBZ',
    legendMeta: {
      legend_stops: [
        [0, '#04121f'], [10, '#04e9e7'], [25, '#019ff4'], [40, '#02fd02'],
        [55, '#fdf802'], [65, '#fd9500'], [75, '#bc0000'],
      ] as Array<[number, string]>,
    },
  },
  sharedRadarPtype: {
    name: 'sharedRadarPtype',
    model: 'hrrr',
    variable: 'radar_ptype',
    displayName: 'Reflectivity + Ptype',
    legendTitle: 'Reflectivity + Ptype (dBZ)',
    kind: 'indexed',
    units: 'dBZ',
    legendMeta: ptypeMeta(SHARED_RADAR_PTYPE),
  },
  composite: {
    name: 'composite',
    model: 'gfs',
    variable: 'refl_composite',
    displayName: 'Composite Reflectivity',
    legendTitle: 'Composite Reflectivity (dBZ)',
    kind: 'indexed',
    units: 'dBZ',
    legendMeta: { legend_stops: [[5, '#a8f0a8'], [35, '#feff50'], [70, '#fdfdfd']] as Array<[number, string]> },
    composite: [
      {
        id: 'rain_layer',
        var: 'refl_rain',
        displayName: 'Rain Reflectivity',
        kind: 'discrete',
        units: 'dBZ',
        stops: [[5, '#a8f0a8'], [25, '#c8e640'], [45, '#d41020'], [70, '#fdfdfd']],
      },
      {
        id: 'snow_layer',
        var: 'refl_snow',
        displayName: 'Snow Reflectivity',
        kind: 'discrete',
        units: 'dBZ',
        stops: [[5, '#c8ffff'], [25, '#2f74f0'], [45, '#130495'], [70, '#fdfdfd']],
      },
    ],
  },
};

export function legendViewerUrl(fixture: LegendFixture, fh = 0): string {
  return `/viewer?m=${fixture.model}&r=latest&v=${fixture.variable}&fh=${fh}&reg=conus&lat=39.83&lon=-98.58&z=6`;
}

/** Every ptype fixture's expected ramp count = its `ptype_order` length. */
export function ptypeOrderOf(fixture: LegendFixture): string[] {
  const order = (fixture.legendMeta as { ptype_order?: string[] }).ptype_order;
  return Array.isArray(order) ? order : [];
}

// ── Route stubs ────────────────────────────────────────────────────────────

function variableCatalogEntry(fixture: LegendFixture) {
  return {
    var_key: fixture.variable,
    display_name: fixture.displayName,
    kind: fixture.kind,
    units: fixture.units ?? '',
    group: 'Test',
    default_fh: 0,
    buildable: true,
    color_map_id: fixture.variable,
    render_substrates: ['grid'],
    constraints: {},
    derived: false,
    derive_strategy_id: null,
  };
}

function capabilityPayload(fixture: LegendFixture) {
  return {
    contract_version: 'v1',
    supported_models: [fixture.model],
    model_catalog: {
      [fixture.model]: {
        model_id: fixture.model,
        name: fixture.model.toUpperCase(),
        product: 'forecast',
        canonical_region: 'conus',
        defaults: {
          default_var_key: fixture.variable,
          default_run: 'latest',
          default_frame_selection: 'first',
          default_render_substrate: 'grid',
        },
        constraints: {
          canonical_region: 'conus',
          time_axis_mode: 'forecast',
          latest_only: false,
          supports_sampling: true,
        },
        run_discovery: {},
        variables: { [fixture.variable]: variableCatalogEntry(fixture) },
      },
    },
    availability: {
      [fixture.model]: {
        latest_run: LEGEND_RUN_ID,
        published_runs: [LEGEND_RUN_ID],
        latest_run_ready: true,
        latest_run_ready_vars: [fixture.variable],
        latest_run_ready_frame_count: FRAME_HOURS.length,
        source: 'test',
        time_axis_mode: 'forecast',
      },
    },
  };
}

function manifestPayload(fixture: LegendFixture) {
  return {
    model: fixture.model,
    run: LEGEND_RUN_ID,
    region: 'conus',
    last_updated: new Date().toISOString(),
    variables: {
      [fixture.variable]: {
        display_name: fixture.displayName,
        kind: fixture.kind,
        units: fixture.units ?? '',
        ready_through_fh: FRAME_HOURS[FRAME_HOURS.length - 1],
        expected_max_fh: FRAME_HOURS[FRAME_HOURS.length - 1],
        frames: FRAME_HOURS.map((fh) => ({ fh, valid_time: legendValidTime(fh) })),
      },
    },
  };
}

function frameMetaPayload(fixture: LegendFixture, fh: number) {
  return {
    meta: {
      valid_time: legendValidTime(fh),
      units: fixture.units ?? '',
      kind: fixture.kind,
      display_name: fixture.displayName,
      legend_title: fixture.legendTitle ?? fixture.displayName,
      var_key: fixture.variable,
      ...fixture.legendMeta,
    },
  };
}

function framesPayload(fixture: LegendFixture) {
  return FRAME_HOURS.map((fh) => ({
    fh,
    has_cog: true,
    run: LEGEND_RUN_ID,
    valid_time: legendValidTime(fh),
    meta: frameMetaPayload(fixture, fh),
  }));
}

const BBOX: [number, number, number, number] = [-11273875, 4742782, -10673875, 4942782];

function gridManifestPayload(fixture: LegendFixture) {
  return {
    manifest_version: 1,
    subtype: 'grid',
    model: fixture.model,
    run: LEGEND_RUN_ID,
    var: fixture.variable,
    projection: 'EPSG:3857',
    bbox: BBOX,
    grid: {
      width: 2,
      height: 1,
      dtype: 'uint16',
      endianness: 'little',
      scale: 0.1,
      offset: -100.0,
      nodata: 65535,
      units: fixture.units ?? '',
    },
    palette: {
      color_map_id: fixture.variable,
      kind: fixture.kind,
      transparent_below_min: null,
      transparent_zero: false,
    },
    ...(fixture.composite
      ? {
          composite_mode: 'stack',
          composite_layers: fixture.composite.map((layer) => ({ id: layer.id, var: layer.var })),
        }
      : {}),
    lods: [
      {
        level: 0,
        width: 2,
        height: 1,
        frames: FRAME_HOURS.map((fh) => ({
          fh,
          file: `fh${String(fh).padStart(3, '0')}.l0.u16.bin`,
          valid_time: legendValidTime(fh),
          url: `/api/v4/grid/${fixture.model}/${LEGEND_RUN_ID}/${fixture.variable}/fh${String(fh).padStart(3, '0')}.l0.u16.bin?v=${LEGEND_RUN_ID}-${fh}`,
        })),
      },
    ],
  };
}

function compositeGridManifestPayload(fixture: LegendFixture, layer: CompositeLayerFixture) {
  return {
    manifest_version: 1,
    subtype: 'grid',
    model: fixture.model,
    run: LEGEND_RUN_ID,
    var: layer.var,
    display_name: layer.displayName,
    projection: 'EPSG:3857',
    bbox: BBOX,
    grid: {
      width: 2,
      height: 1,
      dtype: 'uint16',
      endianness: 'little',
      scale: 0.1,
      offset: -100.0,
      nodata: 65535,
      units: layer.units ?? '',
    },
    palette: {
      color_map_id: layer.var,
      kind: layer.kind,
      transparent_below_min: null,
      transparent_zero: false,
    },
    legend: { type: layer.kind, stops: layer.stops },
    lods: [
      {
        level: 0,
        width: 2,
        height: 1,
        frames: FRAME_HOURS.map((fh) => ({
          fh,
          file: `fh${String(fh).padStart(3, '0')}.l0.u16.bin`,
          valid_time: legendValidTime(fh),
          url: `/api/v4/grid/${fixture.model}/${LEGEND_RUN_ID}/${layer.var}/fh${String(fh).padStart(3, '0')}.l0.u16.bin?v=${LEGEND_RUN_ID}-${fh}`,
        })),
      },
    ],
  };
}

function frameBinary(fh: number): Buffer {
  return Buffer.from(new Uint16Array([(30 + fh + 100) * 10, (55 + fh + 100) * 10]).buffer);
}

export async function stubLegendRoutes(page: Page, fixture: LegendFixture) {
  await page.route('https://us.i.posthog.com/**', async (route) => {
    await route.fulfill({ status: 204, body: '' });
  });
  await page.route('**/api/regions', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        regions: {
          conus: {
            label: 'CONUS',
            bbox: [-125, 24, -66, 50],
            defaultCenter: [-98.58, 39.83],
            defaultZoom: 4,
            minZoom: 2,
            maxZoom: 9,
          },
        },
      }),
    });
  });
  await page.route('**/api/v4/sample/batch', async (route) => {
    await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not found' }) });
  });
  await page.route('**/tiles/v3/boundaries/v2/tilejson.json', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tilejson: '2.2.0',
        name: 'Boundaries',
        id: 'boundaries',
        scheme: 'xyz',
        format: 'pbf',
        minzoom: 0,
        maxzoom: 10,
        bounds: [-180, -85.0511, 180, 85.0511],
        center: [-98.58, 39.83, 4],
        tiles: ['https://api.cartosky.com/tiles/v3/boundaries/v2/{z}/{x}/{y}.mvt'],
      }),
    });
  });
  await page.route('**/tiles/v3/boundaries/v2/**/*.mvt', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/vnd.mapbox-vector-tile', body: '' });
  });
  await page.route('**/api/v4/**/loop-manifest', async (route) => {
    await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not found' }) });
  });
  await page.route('**/api/v4/**/loop.webp**', async (route) => {
    await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not found' }) });
  });
  await page.route('**/tiles/v3/**/*.png**', async (route) => {
    await route.fulfill({ status: 404, body: '' });
  });
  await page.route('**/api/v4/capabilities', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(capabilityPayload(fixture)) });
  });
  await page.route(`**/api/v4/${fixture.model}/runs`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([LEGEND_RUN_ID]) });
  });
  for (const runSegment of ['latest', LEGEND_RUN_ID]) {
    await page.route(`**/api/v4/${fixture.model}/${runSegment}/manifest**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(manifestPayload(fixture)) });
    });
    await page.route(`**/api/v4/${fixture.model}/${runSegment}/${fixture.variable}/frames**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload(fixture)) });
    });
    await page.route(`**/api/v4/${fixture.model}/${runSegment}/${fixture.variable}/grid-manifest**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload(fixture)) });
    });
    for (const layer of fixture.composite ?? []) {
      await page.route(`**/api/v4/${fixture.model}/${runSegment}/${layer.var}/grid-manifest**`, async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(compositeGridManifestPayload(fixture, layer)),
        });
      });
    }
  }
  await page.route(`**/api/v4/grid/${fixture.model}/*/**/*.bin**`, async (route) => {
    const match = /fh(\d{3})\.l0\.u16\.bin/.exec(route.request().url());
    await route.fulfill({
      status: 200,
      contentType: 'application/octet-stream',
      body: frameBinary(match ? Number(match[1]) : 0),
    });
  });
}
