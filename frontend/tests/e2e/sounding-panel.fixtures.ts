/**
 * Fixtures for the Phase 3 sounding-panel contract
 * (docs/SKEWT_DESIGN_2026-07-30.md §6 + §7 Phase 3).
 *
 * One deterministic HRRR selection (capabilities → runs → manifest → frames →
 * grid manifest → one 2×2 binary frame) plus a synthetic three-frame
 * `POST /api/v4/forecast/sounding` response shaped exactly like the live
 * endpoint: 37 levels 1000→100 hPa, a surface block whose pressure puts the
 * bottom two levels below ground, and one deliberate null level so the
 * pen-break path is exercised in the browser and not only in unit tests.
 */
import type { Page } from '@playwright/test';

export const SOUNDING_MODEL = 'hrrr';
/** A second published model with no sounding stacks — drives the disabled toggle. */
export const NO_SOUNDING_MODEL = 'gfs';
export const SOUNDING_RUN_ID = '20260730_12z';
export const SOUNDING_VARIABLE = 'tmp2m';
export const SOUNDING_FRAME_HOURS = [0, 1, 2];

/** 1000 → 100 hPa in 25 hPa steps: the shipped 37-level ladder. */
export const SOUNDING_LEVELS: number[] = [];
for (let p = 1000; p >= 100; p -= 25) SOUNDING_LEVELS.push(p);

/** Below 968.5 hPa there is ground: the 1000 and 975 levels must be masked. */
export const SOUNDING_SURFACE_PRESSURE = 968.5;
export const SOUNDING_EXPECTED_MASKED_LEVELS = 2;
/** Index (into SOUNDING_LEVELS) whose temperature is nodata in every frame. */
const NULL_LEVEL_INDEX = 12;

const RUN_BASE_MS = Date.UTC(2026, 6, 30, 12, 0, 0);

function validTime(fh: number): string {
  return new Date(RUN_BASE_MS + fh * 3_600_000).toISOString().replace('.000Z', 'Z');
}

/**
 * A plausible summer CONUS column: ~6.5 K/km through the troposphere flattening
 * above 200 hPa, dewpoint depression widening with height, and a veering,
 * strengthening wind so the barb ladder shows halves, fulls and a pennant.
 */
function frameFor(fh: number) {
  const warmth = fh * 0.8;
  const t: (number | null)[] = [];
  const td: (number | null)[] = [];
  const u: (number | null)[] = [];
  const v: (number | null)[] = [];
  const w: (number | null)[] = [];

  SOUNDING_LEVELS.forEach((p, index) => {
    if (index === NULL_LEVEL_INDEX) {
      t.push(null);
      td.push(null);
      u.push(null);
      v.push(null);
      w.push(null);
      return;
    }
    const heightKm = 44.3 * (1 - Math.pow(p / 1013.25, 0.19));
    const temperature = p >= 200 ? 28 + warmth - 6.5 * heightKm : -56 + (200 - p) * 0.05;
    const depression = 3 + heightKm * 2.2;
    t.push(Number(temperature.toFixed(2)));
    td.push(Number((temperature - depression).toFixed(2)));
    // Veering with height, 3 m/s at the bottom to ~35 m/s at 100 hPa.
    const speed = 3 + heightKm * 2.0;
    const angle = (index / SOUNDING_LEVELS.length) * Math.PI * 0.6;
    u.push(Number((speed * Math.cos(angle)).toFixed(2)));
    v.push(Number((speed * Math.sin(angle)).toFixed(2)));
    w.push(Number((-0.05 * heightKm).toFixed(3)));
  });

  return {
    fh,
    valid_time: validTime(fh),
    surface: {
      pres_sfc: SOUNDING_SURFACE_PRESSURE,
      t2m: Number((31.5 + warmth).toFixed(2)),
      td2m: Number((19.0 + warmth * 0.2).toFixed(2)),
      u10m: 3.4,
      v10m: -2.1,
    },
    t,
    td,
    u,
    v,
    w,
  };
}

export function soundingPayload(lat: number, lon: number) {
  return {
    model: SOUNDING_MODEL,
    run: SOUNDING_RUN_ID,
    location: { lat, lon },
    grid_point: {
      // Deliberately offset from the click so the "~N km from click" readout
      // has something real to say.
      lat: Number((lat + 0.04).toFixed(5)),
      lon: Number((lon - 0.03).toFixed(5)),
      row: 160,
      col: 225,
      distance_km: 4.7,
    },
    levels_hPa: SOUNDING_LEVELS,
    units: { t: 'degC', td: 'degC', u: 'm s-1', v: 'm s-1', w: 'Pa s-1', pres_sfc: 'hPa' },
    frames: SOUNDING_FRAME_HOURS.map(frameFor),
    generated_at: '2026-07-30T12:31:00Z',
  };
}

const FRAME_BINARY = new Uint16Array([1320, 1405, 65535, 877]);

function modelCatalogEntry(modelId: string, name: string) {
  return {
        model_id: modelId,
        name,
        product: 'forecast',
        canonical_region: 'conus',
        defaults: {
          default_var_key: SOUNDING_VARIABLE,
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
          [SOUNDING_VARIABLE]: {
            var_key: SOUNDING_VARIABLE,
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
  };
}

function availabilityEntry() {
  return {
    latest_run: SOUNDING_RUN_ID,
    published_runs: [SOUNDING_RUN_ID],
    latest_run_ready: true,
    latest_run_ready_vars: [SOUNDING_VARIABLE],
    latest_run_ready_frame_count: SOUNDING_FRAME_HOURS.length,
    latest_run_target_max_fh: 2,
    source: 'test',
    time_axis_mode: 'forecast',
  };
}

function capabilityPayload() {
  return {
    contract_version: 'v1',
    supported_models: [SOUNDING_MODEL, NO_SOUNDING_MODEL],
    model_catalog: {
      [SOUNDING_MODEL]: modelCatalogEntry(SOUNDING_MODEL, 'HRRR'),
      [NO_SOUNDING_MODEL]: modelCatalogEntry(NO_SOUNDING_MODEL, 'GFS'),
    },
    availability: {
      [SOUNDING_MODEL]: availabilityEntry(),
      [NO_SOUNDING_MODEL]: availabilityEntry(),
    },
  };
}

function regionPayload() {
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

function manifestPayload(modelId: string) {
  return {
    model: modelId,
    run: SOUNDING_RUN_ID,
    region: 'conus',
    variables: {
      [SOUNDING_VARIABLE]: {
        display_name: 'Temperature 2m',
        kind: 'continuous',
        units: 'F',
        frames: SOUNDING_FRAME_HOURS.map((fh) => ({ fh, valid_time: validTime(fh) })),
      },
    },
  };
}

function framesPayload() {
  return SOUNDING_FRAME_HOURS.map((fh) => ({
    fh,
    has_cog: true,
    run: SOUNDING_RUN_ID,
    valid_time: validTime(fh),
    meta: {
      meta: {
        valid_time: validTime(fh),
        units: 'F',
        kind: 'continuous',
        display_name: 'Temperature 2m',
      },
    },
  }));
}

function gridManifestPayload(modelId: string) {
  return {
    manifest_version: 1,
    subtype: 'grid',
    model: modelId,
    run: SOUNDING_RUN_ID,
    var: SOUNDING_VARIABLE,
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
        frames: SOUNDING_FRAME_HOURS.map((fh) => ({
          fh,
          file: `fh${String(fh).padStart(3, '0')}.l0.u16.bin`,
          valid_time: validTime(fh),
          url: `/api/v4/grid/${modelId}/${SOUNDING_RUN_ID}/${SOUNDING_VARIABLE}/fh${String(fh).padStart(3, '0')}.l0.u16.bin?v=${SOUNDING_RUN_ID}-${SOUNDING_VARIABLE}-${fh}`,
        })),
      },
    ],
  };
}

export type SoundingRequestLog = Array<{ model: string; lat: number; lon: number }>;

/** Stubs the whole viewer boot plus the sounding endpoint. */
export async function stubSoundingRoutes(
  page: Page,
  requests: SoundingRequestLog,
  options: { failFirstSounding?: boolean } = {},
) {
  let soundingCalls = 0;

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
  for (const modelId of [SOUNDING_MODEL, NO_SOUNDING_MODEL]) {
    await page.route(`**/api/v4/${modelId}/runs`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([SOUNDING_RUN_ID]) });
    });
    for (const runSegment of ['latest', SOUNDING_RUN_ID]) {
      await page.route(`**/api/v4/${modelId}/${runSegment}/manifest**`, async (route) => {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(manifestPayload(modelId)) });
      });
      await page.route(`**/api/v4/${modelId}/${runSegment}/${SOUNDING_VARIABLE}/frames**`, async (route) => {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload()) });
      });
      await page.route(`**/api/v4/${modelId}/${runSegment}/${SOUNDING_VARIABLE}/grid-manifest**`, async (route) => {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload(modelId)) });
      });
    }
    await page.route(`**/api/v4/grid/${modelId}/${SOUNDING_RUN_ID}/${SOUNDING_VARIABLE}/*.bin**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/octet-stream',
        body: Buffer.from(FRAME_BINARY.buffer),
      });
    });
  }

  await page.route('**/api/v4/forecast/sounding', async (route) => {
    soundingCalls += 1;
    const body = JSON.parse(route.request().postData() || '{}');
    requests.push({ model: body.model, lat: body.lat, lon: body.lon });
    if (options.failFirstSounding && soundingCalls === 1) {
      await route.fulfill({
        status: 429,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'rate limit exceeded', retryAfterSec: 60 }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(soundingPayload(Number(body.lat), Number(body.lon))),
    });
  });
}
