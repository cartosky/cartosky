import { readFile } from 'node:fs/promises';

import { test, expect, type Page } from '@playwright/test';

const mobileControlSurface = new URL('../../src/styles/globals.css', import.meta.url);
const bottomForecastControls = new URL('../../src/components/bottom-forecast-controls.tsx', import.meta.url);
const compareMobileDrawer = new URL('../../src/components/compare/CompareMobileDrawer.tsx', import.meta.url);

test('mobile viewer and comparison controls share an opaque glass surface', async () => {
  const [styles, viewerControls, compareControls] = await Promise.all([
    readFile(mobileControlSurface, 'utf8'),
    readFile(bottomForecastControls, 'utf8'),
    readFile(compareMobileDrawer, 'utf8'),
  ]);

  expect(styles).toMatch(/\.viewer-mobile-control-surface\s*\{[\s\S]*?background-color:\s*rgba\(4, 16, 30, 0\.88\)/);
  expect(styles).toMatch(/\.viewer-mobile-control-surface\s*\{[\s\S]*?backdrop-filter:\s*blur\(12px\) saturate\(1\.6\)/);
  expect(viewerControls).toContain('viewer-mobile-control-surface');
  expect(compareControls).toContain('viewer-mobile-control-surface');
});

function nearestFrame(frames: number[], current: number): number {
  if (frames.length === 0) return 0;
  if (frames.includes(current)) return current;
  return frames.reduce((nearest, value) => {
    const nearestDelta = Math.abs(nearest - current);
    const valueDelta = Math.abs(value - current);
    return valueDelta < nearestDelta || (valueDelta === nearestDelta && value > nearest) ? value : nearest;
  }, frames[0]);
}

function intersectSortedHours(left: number[], right: number[]): number[] {
  const rightSet = new Set(right);
  return left.filter((hour) => rightSet.has(hour));
}

function resolveMutualGridHour(left: number[], right: number[], forecastHour: number): number | null {
  const mutual = intersectSortedHours(left, right);
  if (mutual.length === 0) {
    return null;
  }
  return nearestFrame(mutual, forecastHour);
}

const GRID_RUN_ID = '20260330_12z';
const GRID_FRAME_A = new Uint16Array([1320, 1405, 65535, 877]);
const GRID_FRAME_B = new Uint16Array([1315, 1390, 65535, 860]);
const GRID_FRAME_DP = new Uint16Array([1290, 1350, 65535, 820]);

function capabilityPayload() {
  return {
    contract_version: 'v1',
    supported_models: ['hrrr'],
    model_catalog: {
      hrrr: {
        model_id: 'hrrr',
        name: 'HRRR',
        product: 'forecast',
        canonical_region: 'conus',
        defaults: {
          default_var_key: 'tmp2m',
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
        variables: {
          tmp2m: {
            var_key: 'tmp2m',
            display_name: 'Temperature 2m',
            kind: 'continuous',
            units: 'F',
            order: 0,
            group: 'Temperature',
            default_fh: 0,
            buildable: true,
            color_map_id: 'tmp2m',
            render_substrates: ['grid'],
            constraints: {},
            derived: false,
            derive_strategy_id: null,
          },
          dp2m: {
            var_key: 'dp2m',
            display_name: 'Dew Point 2m',
            kind: 'continuous',
            units: 'F',
            order: 1,
            group: 'Temperature',
            default_fh: 0,
            buildable: true,
            color_map_id: 'dp2m',
            render_substrates: ['grid'],
            constraints: {},
            derived: false,
            derive_strategy_id: null,
          },
        },
      },
    },
    availability: {
      hrrr: {
        latest_run: GRID_RUN_ID,
        published_runs: [GRID_RUN_ID],
        latest_run_ready: true,
        latest_run_ready_vars: ['tmp2m', 'dp2m'],
        latest_run_ready_frame_count: 2,
        source: 'test',
        time_axis_mode: 'forecast',
      },
    },
  };
}

function regionPayload() {
  return {
    regions: {
      conus: {
        label: 'CONUS',
        bbox: [-125, 24, -66, 50],
        defaultCenter: [39.83, -98.58],
        defaultZoom: 4,
        minZoom: 2,
        maxZoom: 9,
      },
    },
  };
}

function manifestPayload(varKey: string) {
  return {
    model: 'hrrr',
    run: GRID_RUN_ID,
    region: 'conus',
    variables: {
      [varKey]: {
        display_name: varKey === 'tmp2m' ? 'Temperature 2m' : 'Dew Point 2m',
        kind: 'continuous',
        units: 'F',
        frames: [
          { fh: 0, valid_time: '2026-03-30T12:00:00Z' },
          { fh: 1, valid_time: '2026-03-30T13:00:00Z' },
        ],
      },
    },
  };
}

function framesPayload(varKey: string) {
  return [
    {
      fh: 0,
      has_cog: true,
      run: GRID_RUN_ID,
      valid_time: '2026-03-30T12:00:00Z',
      meta: {
        meta: {
          valid_time: '2026-03-30T12:00:00Z',
          units: 'F',
          kind: 'continuous',
          display_name: varKey === 'tmp2m' ? 'Temperature 2m' : 'Dew Point 2m',
        },
      },
    },
    {
      fh: 1,
      has_cog: true,
      run: GRID_RUN_ID,
      valid_time: '2026-03-30T13:00:00Z',
      meta: {
        meta: {
          valid_time: '2026-03-30T13:00:00Z',
          units: 'F',
          kind: 'continuous',
          display_name: varKey === 'tmp2m' ? 'Temperature 2m' : 'Dew Point 2m',
        },
      },
    },
  ];
}

function gridManifestPayload(varKey: string) {
  return {
    manifest_version: 1,
    subtype: 'grid',
    model: 'hrrr',
    run: GRID_RUN_ID,
    var: varKey,
    projection: 'EPSG:3857',
    bbox: [-14920000.0, 7356000.0, -14914000.0, 7362000.0],
    grid: {
      width: 2,
      height: 2,
      dtype: 'uint16',
      endianness: 'little',
      scale: 0.1,
      offset: -100.0,
      nodata: 65535,
      units: 'F',
    },
    palette: {
      color_map_id: varKey,
      kind: 'continuous',
      transparent_below_min: null,
      transparent_zero: false,
    },
    lods: [
      {
        level: 0,
        width: 2,
        height: 2,
        frames: [
          {
            fh: 0,
            file: 'fh000.l0.u16.bin',
            valid_time: '2026-03-30T12:00:00Z',
            url: `/api/v4/grid/hrrr/${GRID_RUN_ID}/${varKey}/fh000.l0.u16.bin?v=${GRID_RUN_ID}-${varKey}-0`,
          },
          {
            fh: 1,
            file: 'fh001.l0.u16.bin',
            valid_time: '2026-03-30T13:00:00Z',
            url: `/api/v4/grid/hrrr/${GRID_RUN_ID}/${varKey}/fh001.l0.u16.bin?v=${GRID_RUN_ID}-${varKey}-1`,
          },
        ],
      },
    ],
  };
}

function gridManifestPayloadFor(model: string, varKey: string) {
  return {
    ...gridManifestPayload(varKey),
    model,
    url: undefined,
    lods: [
      {
        ...gridManifestPayload(varKey).lods[0],
        frames: [
          {
            fh: 0,
            file: 'fh000.l0.u16.bin',
            valid_time: '2026-03-30T12:00:00Z',
            url: `/api/v4/grid/${model}/${GRID_RUN_ID}/${varKey}/fh000.l0.u16.bin?v=${GRID_RUN_ID}-${model}-${varKey}-0`,
          },
          {
            fh: 1,
            file: 'fh001.l0.u16.bin',
            valid_time: '2026-03-30T13:00:00Z',
            url: `/api/v4/grid/${model}/${GRID_RUN_ID}/${varKey}/fh001.l0.u16.bin?v=${GRID_RUN_ID}-${model}-${varKey}-1`,
          },
        ],
      },
    ],
  };
}

function variableFallbackCapabilityPayload() {
  return {
    contract_version: 'v1',
    supported_models: ['hrrr', 'gfs'],
    model_catalog: {
      hrrr: capabilityPayload().model_catalog.hrrr,
      gfs: {
        model_id: 'gfs',
        name: 'GFS',
        product: 'forecast',
        canonical_region: 'conus',
        defaults: {
          default_var_key: 'tmp2m',
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
        variables: {
          tmp2m: {
            var_key: 'tmp2m',
            display_name: 'Temperature 2m',
            kind: 'continuous',
            units: 'F',
            order: 0,
            group: 'Temperature',
            default_fh: 0,
            buildable: true,
            color_map_id: 'tmp2m',
            render_substrates: ['grid'],
            constraints: {},
            derived: false,
            derive_strategy_id: null,
          },
        },
      },
    },
    availability: {
      hrrr: capabilityPayload().availability.hrrr,
      gfs: {
        latest_run: GRID_RUN_ID,
        published_runs: [GRID_RUN_ID],
        latest_run_ready: true,
        latest_run_ready_vars: ['tmp2m'],
        latest_run_ready_frame_count: 2,
        source: 'test',
        time_axis_mode: 'forecast',
      },
    },
  };
}

function spcEmptyCapabilityPayload() {
  return {
    contract_version: 'v1',
    supported_models: ['spc'],
    model_catalog: {
      spc: {
        model_id: 'spc',
        name: 'SPC Outlooks',
        product: 'forecast',
        canonical_region: 'conus',
        defaults: {
          default_var_key: 'convective',
          default_run: 'latest',
          default_frame_selection: 'first',
          default_render_substrate: 'vector',
        },
        constraints: {
          canonical_region: 'conus',
          time_axis_mode: 'forecast',
          latest_only: false,
          supports_sampling: false,
        },
        run_discovery: {},
        variables: {
          convective: {
            var_key: 'convective',
            display_name: 'Convective Outlook',
            kind: 'categorical',
            units: '',
            order: 0,
            group: 'Outlooks',
            default_fh: 0,
            buildable: true,
            color_map_id: 'spc',
            render_substrates: ['vector'],
            constraints: {},
            derived: false,
            derive_strategy_id: null,
          },
        },
      },
    },
    availability: {
      spc: {
        latest_run: GRID_RUN_ID,
        published_runs: [GRID_RUN_ID],
        latest_run_ready: true,
        latest_run_ready_vars: ['convective'],
        latest_run_ready_frame_count: 0,
        source: 'test',
        time_axis_mode: 'forecast',
      },
    },
  };
}

async function stubSharedViewerRoutes(page: Page) {
  await page.route('https://us.i.posthog.com/**', async (route) => {
    await route.fulfill({ status: 204, body: '' });
  });
  await page.route('**/api/regions', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(regionPayload()) });
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
}

async function stubViewerGridRoutes(page: Page) {
  await stubSharedViewerRoutes(page);
  await page.route('**/api/v4/capabilities', async (route) => {
    await new Promise((resolve) => {
      setTimeout(resolve, 250);
    });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(capabilityPayload()) });
  });
  await page.route(`**/api/v4/hrrr/runs`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([GRID_RUN_ID]) });
  });
  await page.route(`**/api/v4/hrrr/latest/manifest**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        model: 'hrrr',
        run: GRID_RUN_ID,
        region: 'conus',
        variables: {
          tmp2m: manifestPayload('tmp2m').variables.tmp2m,
          dp2m: manifestPayload('dp2m').variables.dp2m,
        },
      }),
    });
  });
  await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/manifest**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        model: 'hrrr',
        run: GRID_RUN_ID,
        region: 'conus',
        variables: {
          tmp2m: manifestPayload('tmp2m').variables.tmp2m,
          dp2m: manifestPayload('dp2m').variables.dp2m,
        },
      }),
    });
  });
  await page.route(`**/api/v4/hrrr/latest/tmp2m/frames**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('tmp2m')) });
  });
  await page.route(`**/api/v4/hrrr/latest/dp2m/frames**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('dp2m')) });
  });
  await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/tmp2m/frames**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('tmp2m')) });
  });
  await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/dp2m/frames**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('dp2m')) });
  });
  await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/tmp2m/grid-manifest**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload('tmp2m')) });
  });
  await page.route(`**/api/v4/hrrr/latest/tmp2m/grid-manifest**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload('tmp2m')) });
  });
  await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/dp2m/grid-manifest**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload('dp2m')) });
  });
  await page.route(`**/api/v4/hrrr/latest/dp2m/grid-manifest**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload('dp2m')) });
  });
  await page.route(`**/api/v4/grid/hrrr/${GRID_RUN_ID}/tmp2m/fh000.l0.u16.bin**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(GRID_FRAME_A.buffer) });
  });
  await page.route(`**/api/v4/grid/hrrr/${GRID_RUN_ID}/tmp2m/fh001.l0.u16.bin**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(GRID_FRAME_B.buffer) });
  });
  await page.route(`**/api/v4/grid/hrrr/${GRID_RUN_ID}/dp2m/fh000.l0.u16.bin**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(GRID_FRAME_DP.buffer) });
  });
  await page.route(`**/api/v4/grid/hrrr/${GRID_RUN_ID}/dp2m/fh001.l0.u16.bin**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(GRID_FRAME_B.buffer) });
  });
}

async function stubViewerVariableFallbackRoutes(page: Page) {
  await stubSharedViewerRoutes(page);
  await page.route('**/api/v4/capabilities', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(variableFallbackCapabilityPayload()) });
  });
  await page.route('**/api/v4/hrrr/runs', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([GRID_RUN_ID]) });
  });
  await page.route('**/api/v4/gfs/runs', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([GRID_RUN_ID]) });
  });
  await page.route('**/api/v4/hrrr/latest/manifest**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        model: 'hrrr',
        run: GRID_RUN_ID,
        region: 'conus',
        variables: {
          tmp2m: manifestPayload('tmp2m').variables.tmp2m,
          dp2m: manifestPayload('dp2m').variables.dp2m,
        },
      }),
    });
  });
  await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/manifest**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        model: 'hrrr',
        run: GRID_RUN_ID,
        region: 'conus',
        variables: {
          tmp2m: manifestPayload('tmp2m').variables.tmp2m,
          dp2m: manifestPayload('dp2m').variables.dp2m,
        },
      }),
    });
  });
  await page.route('**/api/v4/gfs/latest/manifest**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        model: 'gfs',
        run: GRID_RUN_ID,
        region: 'conus',
        variables: {
          tmp2m: manifestPayload('tmp2m').variables.tmp2m,
        },
      }),
    });
  });
  await page.route(`**/api/v4/gfs/${GRID_RUN_ID}/manifest**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        model: 'gfs',
        run: GRID_RUN_ID,
        region: 'conus',
        variables: {
          tmp2m: manifestPayload('tmp2m').variables.tmp2m,
        },
      }),
    });
  });
  await page.route('**/api/v4/hrrr/latest/tmp2m/frames**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('tmp2m')) });
  });
  await page.route('**/api/v4/hrrr/latest/dp2m/frames**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('dp2m')) });
  });
  await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/tmp2m/frames**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('tmp2m')) });
  });
  await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/dp2m/frames**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('dp2m')) });
  });
  await page.route('**/api/v4/gfs/latest/tmp2m/frames**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('tmp2m')) });
  });
  await page.route(`**/api/v4/gfs/${GRID_RUN_ID}/tmp2m/frames**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('tmp2m')) });
  });
  await page.route('**/api/v4/hrrr/latest/tmp2m/grid-manifest**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayloadFor('hrrr', 'tmp2m')) });
  });
  await page.route('**/api/v4/hrrr/latest/dp2m/grid-manifest**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayloadFor('hrrr', 'dp2m')) });
  });
  await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/tmp2m/grid-manifest**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayloadFor('hrrr', 'tmp2m')) });
  });
  await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/dp2m/grid-manifest**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayloadFor('hrrr', 'dp2m')) });
  });
  await page.route('**/api/v4/gfs/latest/tmp2m/grid-manifest**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayloadFor('gfs', 'tmp2m')) });
  });
  await page.route(`**/api/v4/gfs/${GRID_RUN_ID}/tmp2m/grid-manifest**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayloadFor('gfs', 'tmp2m')) });
  });
  await page.route('**/api/v4/grid/hrrr/**', async (route) => {
    const url = route.request().url();
    const body = url.includes('/dp2m/') ? Buffer.from(GRID_FRAME_DP.buffer) : Buffer.from(GRID_FRAME_A.buffer);
    await route.fulfill({ status: 200, contentType: 'application/octet-stream', body });
  });
  await page.route('**/api/v4/grid/gfs/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(GRID_FRAME_B.buffer) });
  });
}

const OFFSET_LEFT_RUN = '20260330_12z';
const OFFSET_RIGHT_RUN = '20260330_00z';
const OFFSET_HOURS = [0, 6, 12, 18, 24];

function offsetValidTime(run: string, fh: number) {
  const cycle = run === OFFSET_LEFT_RUN ? 12 : 0;
  return new Date(Date.UTC(2026, 2, 30, cycle + fh)).toISOString();
}

function offsetRunManifest(model: string, run: string) {
  return {
    model,
    run,
    region: 'conus',
    variables: {
      tmp2m: {
        display_name: 'Temperature 2m',
        kind: 'continuous',
        units: 'F',
        frames: OFFSET_HOURS.map((fh) => ({ fh, valid_time: offsetValidTime(run, fh) })),
      },
    },
  };
}

function offsetFrames(run: string) {
  return OFFSET_HOURS.map((fh) => ({
    fh,
    has_cog: true,
    run,
    valid_time: offsetValidTime(run, fh),
    meta: { meta: { valid_time: offsetValidTime(run, fh), units: 'F', kind: 'continuous', display_name: 'Temperature 2m' } },
  }));
}

function offsetGridManifest(model: string, run: string) {
  return {
    manifest_version: 1,
    subtype: 'grid',
    model,
    run,
    var: 'tmp2m',
    projection: 'EPSG:3857',
    bbox: [-14920000.0, 7356000.0, -14914000.0, 7362000.0],
    grid: { width: 2, height: 2, dtype: 'uint16', endianness: 'little', scale: 0.1, offset: -100.0, nodata: 65535, units: 'F' },
    palette: { color_map_id: 'tmp2m', kind: 'continuous', transparent_below_min: null, transparent_zero: false },
    lods: [{
      level: 0,
      width: 2,
      height: 2,
      frames: OFFSET_HOURS.map((fh) => ({
        fh,
        file: `fh${String(fh).padStart(3, '0')}.l0.u16.bin`,
        valid_time: offsetValidTime(run, fh),
        url: `/api/v4/grid/${model}/${run}/tmp2m/fh${String(fh).padStart(3, '0')}.l0.u16.bin`,
      })),
    }],
  };
}

async function stubCompareOffsetRoutes(
  page: Page,
  waitForGridManifest: () => Promise<void> = async () => {},
) {
  await stubSharedViewerRoutes(page);
  const regions = regionPayload();
  regions.regions.conus.defaultCenter = [-98.58, 39.83];
  await page.route('**/api/regions', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(regions) });
  });
  const capabilities = variableFallbackCapabilityPayload();
  capabilities.availability.hrrr.latest_run = OFFSET_LEFT_RUN;
  capabilities.availability.hrrr.published_runs = [OFFSET_LEFT_RUN];
  capabilities.availability.gfs.latest_run = OFFSET_RIGHT_RUN;
  capabilities.availability.gfs.published_runs = [OFFSET_RIGHT_RUN];
  await page.route('**/api/v4/capabilities', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(capabilities) });
  });
  for (const [model, run] of [['hrrr', OFFSET_LEFT_RUN], ['gfs', OFFSET_RIGHT_RUN]] as const) {
    await page.route(`**/api/v4/${model}/runs`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([run]) });
    });
    for (const runKey of ['latest', run]) {
      await page.route(`**/api/v4/${model}/${runKey}/manifest**`, async (route) => {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(offsetRunManifest(model, run)) });
      });
      await page.route(`**/api/v4/${model}/${runKey}/tmp2m/frames**`, async (route) => {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(offsetFrames(run)) });
      });
      await page.route(`**/api/v4/${model}/${runKey}/tmp2m/grid-manifest**`, async (route) => {
        await waitForGridManifest();
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(offsetGridManifest(model, run)) });
      });
    }
  }
  await page.route('**/api/v4/grid/hrrr/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(GRID_FRAME_A.buffer) });
  });
  await page.route('**/api/v4/grid/gfs/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(GRID_FRAME_B.buffer) });
  });
  await page.route('**/static/cities/v1/cities_conus_can_v2.json', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ type: 'FeatureCollection', features: [] }) });
  });
}

async function stubViewerSpcEmptyStateRoutes(page: Page) {
  await stubSharedViewerRoutes(page);
  await page.route('**/api/v4/capabilities', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(spcEmptyCapabilityPayload()) });
  });
  await page.route('**/api/v4/spc/runs', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([GRID_RUN_ID]) });
  });
  await page.route('**/api/v4/spc/latest/manifest**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        model: 'spc',
        run: GRID_RUN_ID,
        region: 'conus',
        variables: {
          convective: {
            display_name: 'Convective Outlook',
            kind: 'categorical',
            units: '',
            frames: [],
          },
        },
      }),
    });
  });
  await page.route('**/api/v4/spc/latest/convective/frames**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
}

test.describe('compare diff hour resolution', () => {
  test('independent nearestFrame can pick different hours for the same fh', () => {
    const leftHours = [0, 12];
    const rightHours = [0, 6, 12];
    expect(nearestFrame(leftHours, 6)).toBe(12);
    expect(nearestFrame(rightHours, 6)).toBe(6);
  });

  test('resolveMutualGridHour snaps both sides to the same mutual hour', () => {
    expect(resolveMutualGridHour([0, 12], [0, 6, 12], 6)).toBe(12);
    expect(resolveMutualGridHour([0, 6, 18], [0, 12, 18], 6)).toBe(0);
  });
});

test.describe('Grid-only smoke', () => {
  test('compare swap preserves valid time when both offset runs are selected as Latest', async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop-only swap control.');
    let releaseGridManifests: () => void = () => {};
    const gridManifestGate = new Promise<void>((resolve) => {
      releaseGridManifests = resolve;
    });
    await stubCompareOffsetRoutes(page, () => gridManifestGate);
    await page.goto('/compare?lm=hrrr&lv=tmp2m&lr=latest&rm=gfs&rv=tmp2m&rr=latest&fh=6&lat=39.83&lon=-98.58&z=4');
    const swapButton = page.getByRole('button', { name: 'Swap left and right panels' }).first();
    await expect(swapButton).toBeDisabled({ timeout: 15_000 });
    releaseGridManifests();
    await expect(page.getByText('FH 6 / 18', { exact: true })).toBeVisible();
    await expect(swapButton).toBeEnabled();
    await swapButton.click();
    await expect(page.getByText('FH 18 / 6', { exact: true })).toBeVisible();
  });

  test('compare diff hides the prior result in the selection-change commit', async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop-only swap control.');
    let blockGridManifests = false;
    let releaseGridManifests: () => void = () => {};
    const gridManifestGate = new Promise<void>((resolve) => {
      releaseGridManifests = resolve;
    });
    await stubCompareOffsetRoutes(
      page,
      () => (blockGridManifests ? gridManifestGate : Promise.resolve()),
    );
    await page.goto('/compare?mode=diff&lm=hrrr&lv=tmp2m&lr=latest&rm=gfs&rv=tmp2m&rr=latest&fh=6&lat=39.83&lon=-98.58&z=4');

    const oldLegend = page.getByText('Difference: HRRR − GFS', { exact: true });
    const swapButton = page.getByRole('button', { name: 'Swap left and right panels' }).first();
    await expect(oldLegend).toBeVisible({ timeout: 15_000 });
    await expect(swapButton).toBeEnabled();
    blockGridManifests = true;

    await page.evaluate(() => {
      const probeWindow = window as typeof window & {
        __staleDiffAtSelectionCommit?: boolean | null;
      };
      probeWindow.__staleDiffAtSelectionCommit = null;
      const observer = new MutationObserver(() => {
        const button = document.querySelector<HTMLButtonElement>(
          'button[aria-label="Swap left and right panels"]',
        );
        if (!button?.disabled) {
          return;
        }
        probeWindow.__staleDiffAtSelectionCommit =
          document.body.textContent?.includes('Difference: HRRR − GFS') ?? false;
        observer.disconnect();
      });
      observer.observe(document.body, {
        attributes: true,
        childList: true,
        characterData: true,
        subtree: true,
      });
    });

    await swapButton.click();
    await expect.poll(() => page.evaluate(() => (
      window as typeof window & { __staleDiffAtSelectionCommit?: boolean | null }
    ).__staleDiffAtSelectionCommit)).toBe(false);
    await expect(oldLegend).toBeHidden();
    await expect(swapButton).toBeDisabled();

    releaseGridManifests();
    await expect(page.getByText('Difference: GFS − HRRR', { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(swapButton).toBeEnabled();
  });

  test('grid-default viewer path avoids retired legacy requests', async ({ page }) => {
    const loopRequests: string[] = [];
    const tileRequests: string[] = [];
    const gridManifestRequests: string[] = [];
    page.on('request', (request) => {
      const url = request.url();
      if (url.includes('/grid-manifest')) {
        gridManifestRequests.push(url);
      }
      if (url.includes('/loop-manifest') || url.includes('/loop.webp')) {
        loopRequests.push(url);
      }
      if (url.includes('/tiles/v3/') && url.endsWith('.png')) {
        tileRequests.push(url);
      }
    });

    await stubViewerGridRoutes(page);

    await page.goto('/viewer?m=hrrr&r=latest&v=tmp2m&reg=conus');
    await page.waitForLoadState('networkidle');

    await expect.poll(() => gridManifestRequests.length).toBeGreaterThan(0);
    expect(gridManifestRequests.length).toBeGreaterThanOrEqual(1);
    expect(loopRequests).toEqual([]);
    expect(tileRequests).toEqual([]);
  });

  test('viewer logo is vertically centered in the desktop header', async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop-only header layout.');

    await stubViewerGridRoutes(page);
    await page.goto('/viewer?m=hrrr&r=latest&v=tmp2m&reg=conus');
    await expect(page.getByText('Product', { exact: true })).toBeVisible();

    const header = page.locator('header').filter({ has: page.getByText('Product', { exact: true }) });
    const logo = header.getByRole('img', { name: 'CartoSky' });
    const headerBox = await header.boundingBox();
    const logoBox = await logo.boundingBox();

    expect(headerBox).not.toBeNull();
    expect(logoBox).not.toBeNull();
    expect(Math.abs(
      (logoBox!.y + logoBox!.height / 2) - (headerBox!.y + headerBox!.height / 2),
    )).toBeLessThanOrEqual(1);
  });

  test('compare grid mount avoids maximum update depth warnings', async ({ page }) => {
    const reactLoopErrors: string[] = [];
    const loaderRequests: string[] = [];
    const captureMessage = (message: string) => {
      if (message.includes('Maximum update depth exceeded')) {
        reactLoopErrors.push(message);
      }
    };
    page.on('console', (message) => captureMessage(message.text()));
    page.on('pageerror', (error) => captureMessage(error.message));
    page.on('request', (request) => {
      const url = request.url();
      if (url.includes('/manifest') || url.includes('/grid-manifest') || url.includes('/frames')) {
        loaderRequests.push(url);
      }
    });

    await stubViewerGridRoutes(page);

    await page.goto('/compare?lm=hrrr&lv=tmp2m&lr=latest&rm=hrrr&rv=dp2m&rr=latest&fh=0&screenshot=1');
    await page.waitForLoadState('networkidle');

    expect(reactLoopErrors).toEqual([]);
    const latestManifestRequests = loaderRequests.filter((url) => url.includes('/api/v4/hrrr/latest/manifest'));
    expect(latestManifestRequests.length).toBeLessThanOrEqual(4);
  });

  test('default compare grid mount avoids maximum update depth warnings', async ({ page }) => {
    const reactLoopErrors: string[] = [];
    const captureMessage = (message: string) => {
      if (message.includes('Maximum update depth exceeded')) {
        reactLoopErrors.push(message);
      }
    };
    page.on('console', (message) => captureMessage(message.text()));
    page.on('pageerror', (error) => captureMessage(error.message));

    await stubViewerVariableFallbackRoutes(page);

    await page.goto('/compare');
    await page.waitForLoadState('networkidle');

    expect(reactLoopErrors).toEqual([]);
  });

  test('viewer globe dropdown location search updates and restores permalink camera', async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop-only globe dropdown control.');

    await stubViewerGridRoutes(page);
    await page.route('**/api/v4/locations/search**', async (route) => {
      const url = new URL(route.request().url());
      const query = url.searchParams.get('q')?.toLowerCase() ?? '';
      const results = query.includes('denver')
        ? [{
            display_name: 'Denver, CO',
            latitude: 39.7392,
            longitude: -104.9903,
            timezone: 'America/Denver',
            country_code: 'US',
            admin1: 'Colorado',
            country: 'United States',
          }]
        : [];
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ query, results }) });
    });

    await page.goto('/viewer?m=hrrr&r=latest&v=tmp2m&reg=conus');
    await page.waitForLoadState('networkidle');

    await page.getByLabel('Region: CONUS').click();
    await page.getByPlaceholder('Search city or zip…').fill('Denver');
    await page.getByRole('button', { name: /Denver, CO/ }).first().click();

    await expect.poll(() => {
      const url = new URL(page.url(), 'http://localhost');
      return {
        lat: url.searchParams.get('lat'),
        lon: url.searchParams.get('lon'),
        z: url.searchParams.get('z'),
      };
    }).toEqual({
      lat: '39.73920',
      lon: '-104.99030',
      z: '10.00',
    });

    const selectedSearch = new URL(page.url(), 'http://localhost').search;
    await page.reload();
    await page.waitForLoadState('networkidle');

    await expect.poll(
      () => new URL(page.url(), 'http://localhost').search,
      { timeout: 15000 }
    ).toBe(selectedSearch);
  });

  test('expired run deep links show a fallback notice and recover to the latest viewer state', async ({ page }) => {
    await stubViewerGridRoutes(page);

    await page.goto('/viewer?m=hrrr&r=20200101_00z&v=tmp2m&reg=conus');
    await page.waitForLoadState('networkidle');

    await expect(page.getByTestId('viewer-notice')).toContainText('This link may be outdated - loading default view');
    await expect
      .poll(() => new URL(page.url(), 'http://localhost').searchParams.get('r'))
      .toBe('latest');
  });

  test('unsupported variables fall back to the next model default when switching products', async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop-only model picker flow.');

    await stubViewerVariableFallbackRoutes(page);

    await page.goto('/viewer?m=hrrr&r=latest&v=dp2m&reg=conus');
    await page.waitForLoadState('networkidle');

    const modelTrigger = page.getByRole('button', { name: /HRRR/i }).first();
    await expect(modelTrigger).toBeVisible();
    await modelTrigger.click({ force: true });

    const dialog = page.getByRole('dialog', { name: /model picker/i });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: /^GFS$/i }).first().click();

    await expect(dialog).not.toBeVisible();
    await expect
      .poll(() => ({
        model: new URL(page.url(), 'http://localhost').searchParams.get('m'),
        variable: new URL(page.url(), 'http://localhost').searchParams.get('v'),
      }))
      .toEqual({ model: 'gfs', variable: 'tmp2m' });
  });

  test('SPC selections with no active data show an explicit empty state', async ({ page }) => {
    await stubViewerSpcEmptyStateRoutes(page);

    await page.goto('/viewer?m=spc&r=latest&v=convective&reg=conus');

    await expect(page.getByTestId('viewer-empty-state')).toContainText('Nothing active right now');
    await expect(page.getByTestId('viewer-error')).toHaveCount(0);
  });
});
