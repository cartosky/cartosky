import { test, expect } from '@playwright/test';

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
          default_render_substrate: 'grid_webgl_v1',
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
            render_substrates: ['grid_webgl_v1'],
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
            render_substrates: ['grid_webgl_v1'],
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
    subtype: 'grid_webgl_v1',
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
            url: `/api/v4/grid/v1/hrrr/${GRID_RUN_ID}/${varKey}/fh000.l0.u16.bin?v=${GRID_RUN_ID}-${varKey}-0`,
          },
          {
            fh: 1,
            file: 'fh001.l0.u16.bin',
            valid_time: '2026-03-30T13:00:00Z',
            url: `/api/v4/grid/v1/hrrr/${GRID_RUN_ID}/${varKey}/fh001.l0.u16.bin?v=${GRID_RUN_ID}-${varKey}-1`,
          },
        ],
      },
    ],
  };
}

test.describe('Grid-only smoke', () => {
  test('grid-supported viewer path avoids loop and weather tile requests', async ({ page }) => {
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

    await page.route('https://us.i.posthog.com/**', async (route) => {
      await route.fulfill({ status: 204, body: '' });
    });
    await page.route('**/api/regions', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(regionPayload()) });
    });
    await page.route('**/api/v4/capabilities', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(capabilityPayload()) });
    });
    await page.route(`**/api/v4/hrrr/runs`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([GRID_RUN_ID]) });
    });
    await page.route(`**/api/v4/hrrr/latest/manifest`, async (route) => {
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
    await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/manifest`, async (route) => {
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
    await page.route(`**/api/v4/hrrr/latest/tmp2m/frames`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('tmp2m')) });
    });
    await page.route(`**/api/v4/hrrr/latest/dp2m/frames`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('dp2m')) });
    });
    await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/tmp2m/frames`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('tmp2m')) });
    });
    await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/dp2m/frames`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload('dp2m')) });
    });
    await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/tmp2m/grid-manifest`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload('tmp2m')) });
    });
    await page.route(`**/api/v4/hrrr/latest/tmp2m/grid-manifest`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload('tmp2m')) });
    });
    await page.route(`**/api/v4/hrrr/${GRID_RUN_ID}/dp2m/grid-manifest`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload('dp2m')) });
    });
    await page.route(`**/api/v4/hrrr/latest/dp2m/grid-manifest`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload('dp2m')) });
    });
    await page.route(`**/api/v4/grid/v1/hrrr/${GRID_RUN_ID}/tmp2m/fh000.l0.u16.bin**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(GRID_FRAME_A.buffer) });
    });
    await page.route(`**/api/v4/grid/v1/hrrr/${GRID_RUN_ID}/tmp2m/fh001.l0.u16.bin**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(GRID_FRAME_B.buffer) });
    });
    await page.route(`**/api/v4/grid/v1/hrrr/${GRID_RUN_ID}/dp2m/fh000.l0.u16.bin**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(GRID_FRAME_DP.buffer) });
    });
    await page.route(`**/api/v4/grid/v1/hrrr/${GRID_RUN_ID}/dp2m/fh001.l0.u16.bin**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(GRID_FRAME_B.buffer) });
    });
    await page.route('**/api/v4/sample/batch', async (route) => {
      await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not found' }) });
    });
    await page.route('**/tiles/v3/boundaries/v1/tilejson.json', async (route) => {
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
          tiles: ['https://api.cartosky.com/tiles/v3/boundaries/v1/{z}/{x}/{y}.mvt'],
        }),
      });
    });
    await page.route('**/tiles/v3/boundaries/v1/**/*.mvt', async (route) => {
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

    await page.goto('/viewer?m=hrrr&r=latest&v=tmp2m&reg=conus');
    await page.waitForLoadState('networkidle');

    await expect.poll(() => gridManifestRequests.length).toBeGreaterThan(0);
    await expect(page.getByLabel('Weather map')).toBeVisible();
    const playButton = page.locator('button[aria-label="Play animation"]:visible').first();
    await expect(playButton).toBeVisible();

    await playButton.click();
    const pauseButton = page.locator('button[aria-label="Pause animation"]:visible').first();
    await expect(pauseButton).toBeVisible();
    await pauseButton.click();

    const slider = page.getByRole('slider').first();
    await slider.focus();
    await page.keyboard.press('ArrowRight');
    expect(gridManifestRequests.length).toBeGreaterThanOrEqual(1);
    expect(loopRequests).toEqual([]);
    expect(tileRequests).toEqual([]);
  });
});
