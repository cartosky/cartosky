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
/** Standard-atmosphere height of that surface pressure — the AGL datum. */
const SOUNDING_SURFACE_HEIGHT_KM = 44.3 * (1 - Math.pow(SOUNDING_SURFACE_PRESSURE / 1013.25, 0.19));
/** Index (into SOUNDING_LEVELS) whose temperature is nodata in every frame. */
const NULL_LEVEL_INDEX = 12;

/** Verbatim server string; the panel must render it and not invent its own. */
export const SOUNDING_PARCEL_DEFINITION =
  'SB parcel: HRRR 2 m T/Td, virtual-temperature corrected';

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
  const tw: (number | null)[] = [];
  const thetaE: (number | null)[] = [];
  const heightM: (number | null)[] = [];

  const warmColumn = fh === SOUNDING_WARM_FH;

  SOUNDING_LEVELS.forEach((p, index) => {
    const belowGround = p >= SOUNDING_SURFACE_PRESSURE;
    if (index === NULL_LEVEL_INDEX) {
      t.push(null);
      td.push(null);
      u.push(null);
      v.push(null);
      w.push(null);
      tw.push(null);
      thetaE.push(null);
      heightM.push(null);
      return;
    }
    const heightKm = 44.3 * (1 - Math.pow(p / 1013.25, 0.19));
    // The warm frame never reaches −12 °C, so it has NO dendritic growth
    // zone — the only way to exercise the absent case, since a real
    // full-depth column always passes through the −18…−12 slab somewhere.
    const temperature = warmColumn
      ? 28 + warmth - 1.0 * heightKm
      : p >= 200
        ? 28 + warmth - 6.5 * heightKm
        : -56 + (200 - p) * 0.05;
    const depression = 3 + heightKm * 2.2;
    t.push(Number(temperature.toFixed(2)));
    td.push(Number((temperature - depression).toFixed(2)));
    // Veering with height, 3 m/s at the bottom to ~35 m/s at 100 hPa.
    const speed = 3 + heightKm * 2.0;
    const angle = (index / SOUNDING_LEVELS.length) * Math.PI * 0.6;
    u.push(Number((speed * Math.cos(angle)).toFixed(2)));
    v.push(Number((speed * Math.sin(angle)).toFixed(2)));
    w.push(Number((-0.05 * heightKm).toFixed(3)));

    // Phase 5 per-level overlay inputs. Server-shaped: computed on the
    // anchored column and shipped level-aligned, so below-ground levels carry
    // nulls exactly like the isobaric arrays do NOT (they carry real values) —
    // this is the difference the client has to respect.
    if (belowGround) {
      tw.push(null);
      thetaE.push(null);
      heightM.push(null);
      return;
    }
    tw.push(Number((temperature - depression * 0.35).toFixed(2)));
    thetaE.push(Number((338 + heightKm * 3.1 + warmth).toFixed(2)));
    heightM.push(Number(((heightKm - SOUNDING_SURFACE_HEIGHT_KM) * 1000).toFixed(1)));
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
      // format_version 2 only; fh 2 is deliberately left v1-shaped (below).
      cape_sfc: fh === SOUNDING_LAST_FH ? null : 1466.0,
    },
    t,
    td,
    u,
    v,
    w,
    // fh 1 stands in for a frame whose server-side index computation failed:
    // the profile still draws, every readout row falls back to an em dash.
    indices: fh === SOUNDING_NULL_INDICES_FH ? null : indicesFor(fh),
    parcel: fh === SOUNDING_NULL_INDICES_FH ? null : parcelFor(fh),
    // Same frame stands in for a pre-Phase-5 / failed overlay block: the inset
    // row must simply not render.
    profiles:
      fh === SOUNDING_NULL_INDICES_FH
        ? null
        : {
            tw,
            theta_e: thetaE,
            height_m_agl: heightM,
            surface_tw: Number((31.5 + warmth - 4.4).toFixed(2)),
            surface_theta_e: Number((338 + warmth).toFixed(2)),
          },
  };
}

/** The last frame stands in for a v1 stack: no model SBCAPE, no LFC/EL. */
export const SOUNDING_LAST_FH = SOUNDING_FRAME_HOURS[SOUNDING_FRAME_HOURS.length - 1];

/** This frame carries `indices` / `parcel` / `profiles` all null. */
export const SOUNDING_NULL_INDICES_FH = SOUNDING_FRAME_HOURS[1];

/**
 * The DGZ-free frame. Its column is deliberately unphysical (a ~1 K/km lapse
 * all the way to 100 hPa) because a realistic full-depth sounding ALWAYS
 * crosses −18…−12 °C somewhere, and the "renders nothing" branch still has to
 * be proven.
 */
export const SOUNDING_WARM_FH = SOUNDING_FRAME_HOURS[2];

/**
 * Server-shaped Phase 4 block. Values are per-fh so the scrubber assertions can
 * tell frames apart; the last frame is the CAPPED case — null LFC/EL and null
 * `model_sbcape` — which is what drives the "—" assertions.
 */
function indicesFor(fh: number) {
  const capped = fh === SOUNDING_LAST_FH;
  return {
    sbcape: 1200 + fh * 137,
    sbcin: -12 - fh,
    mlcape: 640 + fh * 40,
    mlcin: -55 - fh,
    lcl_hPa: 902.4 - fh * 3,
    lcl_C: 18.6,
    lfc_hPa: capped ? null : 780.2,
    el_hPa: capped ? null : 210.5,
    pwat_mm: 38.4 + fh,
    model_sbcape: capped ? null : 1466,
  };
}

/**
 * A monotone dry-then-moist ascent from the surface. Not physically derived —
 * the client never computes thermodynamics, so the fixture only has to be
 * shaped like the server's polyline.
 */
function parcelFor(fh: number) {
  const p: number[] = [];
  const t: number[] = [];
  const surfaceT = 31.5 + fh * 0.8;
  const lcl = 902.4 - fh * 3;
  p.push(SOUNDING_SURFACE_PRESSURE);
  t.push(Number(surfaceT.toFixed(1)));
  for (const level of SOUNDING_LEVELS) {
    if (level >= SOUNDING_SURFACE_PRESSURE) continue;
    if (p[p.length - 1] > lcl && level < lcl) {
      p.push(Number(lcl.toFixed(1)));
      t.push(Number((surfaceT - (SOUNDING_SURFACE_PRESSURE - lcl) * 0.095).toFixed(1)));
    }
    const dry = level > lcl;
    const value = dry
      ? surfaceT - (SOUNDING_SURFACE_PRESSURE - level) * 0.095
      : surfaceT - (SOUNDING_SURFACE_PRESSURE - lcl) * 0.095 - (lcl - level) * 0.075;
    p.push(level);
    t.push(Number(value.toFixed(1)));
  }
  return { p, t };
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
    units: {
      t: 'degC',
      td: 'degC',
      u: 'm s-1',
      v: 'm s-1',
      w: 'Pa s-1',
      pres_sfc: 'hPa',
      cape_sfc: 'J kg-1',
    },
    parcel_definition: SOUNDING_PARCEL_DEFINITION,
    frames: SOUNDING_FRAME_HOURS.map(frameFor),
    generated_at: '2026-07-30T12:31:00Z',
  };
}

const FRAME_BINARY = new Uint16Array([1320, 1405, 65535, 877]);

/** 1x1 fully transparent PNG, used to serve a deterministic empty basemap. */
const TRANSPARENT_PNG_1X1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII=',
  'base64',
);

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
  // Same pin the golden-baseline fixtures carry: the viewer's basemap is Carto
  // raster PNG fetched from a live third-party CDN. Left unstubbed, MapLibre's
  // `load` event — which gates the map click-handler binding — waits on real
  // network tiles, and under worker contention that intermittently exceeded
  // every test wait (the "click produced no sounding POST" flake). A valid
  // transparent tile lets the raster source settle deterministically; a 404
  // would leave the source unsettled forever (see render-golden-baseline
  // fixtures for the measurement behind this).
  await page.route('https://*.basemaps.cartocdn.com/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: TRANSPARENT_PNG_1X1 });
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
