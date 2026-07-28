/**
 * Fixed, repo-owned viewer route fixtures for the Phase 2 first-paint contract
 * (docs/plans/2026-07-28-map-viewer-redesign-phase-2-first-paint.md, Task 1).
 *
 * One deterministic GFS-style grid selection: capabilities, regions, runs, run
 * manifest, frames metadata, grid manifest, and a valid uint16 binary frame.
 * The grid binary responses await a test-controlled gate so the spec decides
 * exactly when the requested frame may finish — no live weather, no sleeps.
 */
import type { Page } from '@playwright/test';

export const FIRST_PAINT_MODEL = 'gfs';
export const FIRST_PAINT_RUN_ID = '20260728_12z';
export const FIRST_PAINT_VARIABLE = 'tmp2m';
/**
 * Declared run target horizon. Frames only reach FH 1, so the run is
 * incomplete and the timeline's "Building available/total hrs" strip renders
 * with available=1 (max published frame hour) and total=6.
 */
export const FIRST_PAINT_TARGET_MAX_FH = 6;
export const FIRST_PAINT_EXPECTED_BUILDING_TEXT = '1/6 hrs';

/** Same 2×2 uint16 layout the grid smoke fixtures use; 65535 is nodata. */
export const FIRST_PAINT_FRAME_BINARY = new Uint16Array([1320, 1405, 65535, 877]);

export type Deferred = {
  promise: Promise<void>;
  resolve: () => void;
};

export function createDeferred(): Deferred {
  let resolve: () => void = () => {};
  const promise = new Promise<void>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

export function capabilityPayload() {
  return {
    contract_version: 'v1',
    supported_models: [FIRST_PAINT_MODEL],
    model_catalog: {
      [FIRST_PAINT_MODEL]: {
        model_id: FIRST_PAINT_MODEL,
        name: 'GFS',
        product: 'forecast',
        canonical_region: 'conus',
        defaults: {
          default_var_key: FIRST_PAINT_VARIABLE,
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
          [FIRST_PAINT_VARIABLE]: {
            var_key: FIRST_PAINT_VARIABLE,
            display_name: 'Temperature 2m',
            kind: 'continuous',
            units: 'F',
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
      [FIRST_PAINT_MODEL]: {
        latest_run: FIRST_PAINT_RUN_ID,
        published_runs: [FIRST_PAINT_RUN_ID],
        latest_run_ready: true,
        latest_run_ready_vars: [FIRST_PAINT_VARIABLE],
        latest_run_ready_frame_count: 2,
        latest_run_target_max_fh: FIRST_PAINT_TARGET_MAX_FH,
        source: 'test',
        time_axis_mode: 'forecast',
      },
    },
  };
}

export function regionPayload() {
  return {
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
  };
}

export function manifestPayload() {
  return {
    model: FIRST_PAINT_MODEL,
    run: FIRST_PAINT_RUN_ID,
    region: 'conus',
    variables: {
      [FIRST_PAINT_VARIABLE]: {
        display_name: 'Temperature 2m',
        kind: 'continuous',
        units: 'F',
        frames: [
          { fh: 0, valid_time: '2026-07-28T12:00:00Z' },
          { fh: 1, valid_time: '2026-07-28T13:00:00Z' },
        ],
      },
    },
  };
}

export function framesPayload() {
  return [0, 1].map((fh) => ({
    fh,
    has_cog: true,
    run: FIRST_PAINT_RUN_ID,
    valid_time: `2026-07-28T${String(12 + fh).padStart(2, '0')}:00:00Z`,
    meta: {
      meta: {
        valid_time: `2026-07-28T${String(12 + fh).padStart(2, '0')}:00:00Z`,
        units: 'F',
        kind: 'continuous',
        display_name: 'Temperature 2m',
      },
    },
  }));
}

export function gridManifestPayload() {
  return {
    manifest_version: 1,
    subtype: 'grid',
    model: FIRST_PAINT_MODEL,
    run: FIRST_PAINT_RUN_ID,
    var: FIRST_PAINT_VARIABLE,
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
      color_map_id: 'tmp2m',
      kind: 'continuous',
      transparent_below_min: null,
      transparent_zero: false,
    },
    lods: [
      {
        level: 0,
        width: 2,
        height: 2,
        frames: [0, 1].map((fh) => ({
          fh,
          file: `fh${String(fh).padStart(3, '0')}.l0.u16.bin`,
          valid_time: `2026-07-28T${String(12 + fh).padStart(2, '0')}:00:00Z`,
          url: `/api/v4/grid/${FIRST_PAINT_MODEL}/${FIRST_PAINT_RUN_ID}/${FIRST_PAINT_VARIABLE}/fh${String(fh).padStart(3, '0')}.l0.u16.bin?v=${FIRST_PAINT_RUN_ID}-${FIRST_PAINT_VARIABLE}-${fh}`,
        })),
      },
    ],
  };
}

/**
 * Stub every route the cold viewer boot touches. All grid frame binaries —
 * including the requested one — await `gridFrameGate()` before responding, so
 * the caller controls exactly when the first weather frame can paint.
 */
export async function stubViewerFirstPaintRoutes(
  page: Page,
  options: { gridFrameGate: () => Promise<void> },
) {
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

  await page.route('**/api/v4/capabilities', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(capabilityPayload()) });
  });
  await page.route(`**/api/v4/${FIRST_PAINT_MODEL}/runs`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([FIRST_PAINT_RUN_ID]) });
  });
  for (const runSegment of ['latest', FIRST_PAINT_RUN_ID]) {
    await page.route(`**/api/v4/${FIRST_PAINT_MODEL}/${runSegment}/manifest**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(manifestPayload()) });
    });
    await page.route(`**/api/v4/${FIRST_PAINT_MODEL}/${runSegment}/${FIRST_PAINT_VARIABLE}/frames**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload()) });
    });
    await page.route(`**/api/v4/${FIRST_PAINT_MODEL}/${runSegment}/${FIRST_PAINT_VARIABLE}/grid-manifest**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload()) });
    });
  }
  await page.route(`**/api/v4/grid/${FIRST_PAINT_MODEL}/${FIRST_PAINT_RUN_ID}/${FIRST_PAINT_VARIABLE}/*.bin**`, async (route) => {
    await options.gridFrameGate();
    await route.fulfill({
      status: 200,
      contentType: 'application/octet-stream',
      body: Buffer.from(FIRST_PAINT_FRAME_BINARY.buffer),
    });
  });
}
