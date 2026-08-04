/**
 * Phase 5 timeline contract fixtures
 * (docs/plans/2026-07-28-map-viewer-redesign-phase-5-timeline.md, Task 2).
 *
 * One config-driven stub covering every fixture variant: manifests carry the
 * new `ready_through_fh` / `expected_max_fh` scalars (or omit them for the
 * legacy case), frames are deterministic and repo-owned, and every grid
 * binary is generated per-FH so no live weather is involved.
 */
import type { Page } from '@playwright/test';

export type TimelineFixture = {
  name: string;
  model: string;
  runId: string;
  variable: string;
  displayName: string;
  timeAxisMode: 'forecast' | 'observed' | 'valid';
  /** Published forecast hours (frames that exist). */
  publishedFhs: number[];
  /** Scalars; use `omitScalars` for the legacy (old-manifest) case. */
  readyThroughFh?: number | null;
  expectedMaxFh?: number;
  omitScalars?: boolean;
  /** Optional CPC-style valid-period metadata on every frame. */
  cpcValid?: { seas: string; start: string; end: string };
  /** Operational issuance timestamp carried by frame metadata. */
  issueTime?: string;
  /** Optional operational labels for day-indexed products such as SPC outlooks. */
  dayLabels?: Record<number, string>;
  /** Per-run overrides for run-switch scenarios (keyed by concrete run id). */
  runOverrides?: Record<string, { publishedFhs: number[]; readyThroughFh?: number | null; expectedMaxFh?: number }>;
};

/** Run base time: 2026-07-28T12:00Z; valid time = base + fh hours. */
export const TIMELINE_RUN_ID = '20260728_12z';
const RUN_BASE_MS = Date.UTC(2026, 6, 28, 12, 0, 0);

export function validTimeIso(fh: number): string {
  return new Date(RUN_BASE_MS + fh * 3_600_000).toISOString().replace('.000Z', 'Z');
}

/** Real GFS cadence shape, capped for test weight: 3-hourly to 240, 6-hourly to 264. */
export const GFS_LONG_FHS = [
  ...Array.from({ length: 81 }, (_, i) => i * 3), // 0..240 step 3
  246, 252, 258, 264,
];

export const FIXTURES: Record<string, TimelineFixture> = {
  gfsLong: {
    name: 'gfsLong',
    model: 'gfs',
    runId: TIMELINE_RUN_ID,
    variable: 'tmp2m',
    displayName: 'Temperature 2m',
    timeAxisMode: 'forecast',
    publishedFhs: GFS_LONG_FHS,
    readyThroughFh: 264,
    expectedMaxFh: 264,
  },
  outOfOrder: {
    name: 'outOfOrder',
    model: 'gfs',
    runId: TIMELINE_RUN_ID,
    variable: 'tmp2m',
    displayName: 'Temperature 2m',
    timeAxisMode: 'forecast',
    publishedFhs: [0, 3, 9, 12],
    readyThroughFh: 3,
    expectedMaxFh: 12,
  },
  building: {
    name: 'building',
    model: 'gfs',
    runId: TIMELINE_RUN_ID,
    variable: 'tmp2m',
    displayName: 'Temperature 2m',
    timeAxisMode: 'forecast',
    publishedFhs: [0, 3],
    readyThroughFh: 3,
    expectedMaxFh: 12,
  },
  legacy: {
    name: 'legacy',
    model: 'gfs',
    runId: TIMELINE_RUN_ID,
    variable: 'tmp2m',
    displayName: 'Temperature 2m',
    timeAxisMode: 'forecast',
    publishedFhs: [0, 3, 6, 9],
    omitScalars: true,
  },
  nullReady: {
    name: 'nullReady',
    model: 'gfs',
    runId: TIMELINE_RUN_ID,
    variable: 'tmp2m',
    displayName: 'Temperature 2m',
    timeAxisMode: 'forecast',
    publishedFhs: [9, 12],
    readyThroughFh: null,
    expectedMaxFh: 12,
  },
  runSwitch: {
    name: 'runSwitch',
    model: 'gfs',
    runId: TIMELINE_RUN_ID,
    variable: 'tmp2m',
    displayName: 'Temperature 2m',
    timeAxisMode: 'forecast',
    publishedFhs: [0, 3, 6, 9, 12],
    readyThroughFh: 12,
    expectedMaxFh: 12,
    // Older run: shorter ready boundary with out-of-order published frames —
    // the longer→shorter switch scenario (plan Task 2 item 5).
    runOverrides: {
      '20260728_06z': { publishedFhs: [0, 3, 9, 12], readyThroughFh: 3, expectedMaxFh: 12 },
    },
  },
  observed: {
    name: 'observed',
    model: 'mrms',
    runId: TIMELINE_RUN_ID,
    variable: 'reflectivity',
    displayName: 'Composite Reflectivity',
    timeAxisMode: 'observed',
    publishedFhs: [0, 1, 2, 3],
    omitScalars: true,
  },
  valid: {
    name: 'valid',
    model: 'cpc',
    runId: TIMELINE_RUN_ID,
    variable: 'temp_outlook',
    displayName: 'Temperature Outlook',
    timeAxisMode: 'valid',
    publishedFhs: [0],
    omitScalars: true,
    issueTime: '2026-08-03T19:15:00Z',
    cpcValid: { seas: 'ASO 2026', start: '2026-08-01', end: '2026-10-31' },
  },
  spcDays: {
    name: 'spcDays',
    model: 'spc',
    runId: TIMELINE_RUN_ID,
    variable: 'convective',
    displayName: 'Convective Outlook',
    timeAxisMode: 'valid',
    publishedFhs: [0, 1, 2],
    omitScalars: true,
    dayLabels: { 0: 'Day 1', 1: 'Day 2', 2: 'Day 3' },
  },
};

export function timelineViewerUrl(fixture: TimelineFixture, fh = 0): string {
  return `/viewer?m=${fixture.model}&r=latest&v=${fixture.variable}&fh=${fh}&reg=conus&lat=39.83&lon=-98.58&z=6`;
}

function capabilityPayload(fixture: TimelineFixture) {
  return {
    contract_version: 'v1',
    supported_models: [fixture.model],
    model_catalog: {
      [fixture.model]: {
        model_id: fixture.model,
        name: fixture.model.toUpperCase(),
        product: fixture.timeAxisMode === 'observed' ? 'obs' : 'forecast',
        canonical_region: 'conus',
        defaults: {
          default_var_key: fixture.variable,
          default_run: 'latest',
          default_frame_selection: 'first',
          default_render_substrate: 'grid',
        },
        constraints: {
          canonical_region: 'conus',
          time_axis_mode: fixture.timeAxisMode,
          latest_only: false,
          supports_sampling: true,
        },
        run_discovery: {},
        variables: {
          [fixture.variable]: {
            var_key: fixture.variable,
            display_name: fixture.displayName,
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
      [fixture.model]: {
        latest_run: fixture.runId,
        published_runs: [fixture.runId, '20260728_06z', '20260728_00z'],
        latest_run_ready: true,
        latest_run_ready_vars: [fixture.variable],
        latest_run_ready_frame_count: fixture.publishedFhs.length,
        source: 'test',
        time_axis_mode: fixture.timeAxisMode,
      },
    },
  };
}

type RunConfig = { publishedFhs: number[]; readyThroughFh?: number | null; expectedMaxFh?: number; omitScalars?: boolean };

function runConfigFor(fixture: TimelineFixture, runSegment: string): RunConfig {
  const override = runSegment !== 'latest' ? fixture.runOverrides?.[runSegment] : undefined;
  if (override) {
    return { ...override, omitScalars: fixture.omitScalars };
  }
  return fixture;
}

function variableManifestEntry(fixture: TimelineFixture, config: RunConfig = fixture) {
  const entry: Record<string, unknown> = {
    display_name: fixture.displayName,
    kind: 'continuous',
    units: 'F',
    expected_frames: config.expectedMaxFh !== undefined
      ? undefined
      : config.publishedFhs.length,
    available_frames: config.publishedFhs.length,
    frames: config.publishedFhs.map((fh) => ({ fh, valid_time: validTimeIso(fh) })),
  };
  if (!config.omitScalars) {
    entry.ready_through_fh = config.readyThroughFh ?? null;
    entry.expected_max_fh = config.expectedMaxFh;
  }
  return entry;
}

function manifestPayload(fixture: TimelineFixture, runSegment = 'latest') {
  const config = runConfigFor(fixture, runSegment);
  const runId = runSegment === 'latest' ? fixture.runId : runSegment;
  return {
    model: fixture.model,
    run: runId,
    region: 'conus',
    last_updated: new Date().toISOString(),
    variables: { [fixture.variable]: variableManifestEntry(fixture, config) },
  };
}

function frameMeta(fixture: TimelineFixture, fh: number) {
  return {
    meta: {
      valid_time: validTimeIso(fh),
      units: 'F',
      kind: 'continuous',
      display_name: fixture.displayName,
      ...(fixture.issueTime ? { issue_time: fixture.issueTime } : {}),
      ...(fixture.cpcValid
        ? {
            // Production frame-meta key names (backend cpc_outlook.py emits
            // valid_seas/valid_start/valid_end); the viewer reads these.
            valid_seas: fixture.cpcValid.seas,
            valid_start: fixture.cpcValid.start,
            valid_end: fixture.cpcValid.end,
          }
        : {}),
      ...(fixture.dayLabels?.[fh] ? { day_label: fixture.dayLabels[fh] } : {}),
    },
  };
}

function framesPayload(fixture: TimelineFixture, runSegment = 'latest') {
  const config = runConfigFor(fixture, runSegment);
  const runId = runSegment === 'latest' ? fixture.runId : runSegment;
  return config.publishedFhs.map((fh) => ({
    fh,
    has_cog: true,
    run: runId,
    valid_time: validTimeIso(fh),
    meta: frameMeta(fixture, fh),
  }));
}

function gridManifestPayload(fixture: TimelineFixture, runSegment = 'latest') {
  const config = runConfigFor(fixture, runSegment);
  const runId = runSegment === 'latest' ? fixture.runId : runSegment;
  return {
    manifest_version: 1,
    subtype: 'grid',
    model: fixture.model,
    run: runId,
    var: fixture.variable,
    projection: 'EPSG:3857',
    bbox: [-11273875, 4742782, -10673875, 4942782],
    grid: {
      width: 2,
      height: 1,
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
        height: 1,
        frames: config.publishedFhs.map((fh) => ({
          fh,
          file: `fh${String(fh).padStart(3, '0')}.l0.u16.bin`,
          valid_time: validTimeIso(fh),
          url: `/api/v4/grid/${fixture.model}/${runId}/${fixture.variable}/fh${String(fh).padStart(3, '0')}.l0.u16.bin?v=${runId}-${fh}`,
        })),
      },
    ],
  };
}

function frameBinary(fh: number): Buffer {
  const values = new Uint16Array([(70 + (fh % 30) + 100) * 10, (80 + (fh % 30) + 100) * 10]);
  return Buffer.from(values.buffer);
}

/**
 * Record every frame-asset request (grid binaries, contour GeoJSON, RGB — any
 * URL carrying an fhNNN token for this run) so the no-fetch-past-boundary
 * assertions cover all asset kinds, not only `.bin`.
 */
export function trackFrameAssetRequests(page: Page, fixture: TimelineFixture, runId?: string): { urls: string[]; fhs: () => number[] } {
  const targetRun = runId ?? fixture.runId;
  const urls: string[] = [];
  page.on('request', (request) => {
    const url = request.url();
    if (url.includes(targetRun) && /fh(\d{1,3})/.test(url)) {
      urls.push(url);
    }
  });
  return {
    urls,
    fhs: () => urls
      .map((url) => Number(/fh(\d{1,3})/.exec(url)?.[1]))
      .filter((fh) => Number.isFinite(fh)),
  };
}

export async function stubTimelineRoutes(page: Page, fixture: TimelineFixture) {
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
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([fixture.runId, '20260728_06z', '20260728_00z']),
    });
  });
  for (const runSegment of ['latest', fixture.runId, '20260728_06z', '20260728_00z']) {
    await page.route(`**/api/v4/${fixture.model}/${runSegment}/manifest**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(manifestPayload(fixture, runSegment)) });
    });
    await page.route(`**/api/v4/${fixture.model}/${runSegment}/${fixture.variable}/frames**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(framesPayload(fixture, runSegment)) });
    });
    await page.route(`**/api/v4/${fixture.model}/${runSegment}/${fixture.variable}/grid-manifest**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gridManifestPayload(fixture, runSegment)) });
    });
  }
  await page.route(`**/api/v4/grid/${fixture.model}/*/${fixture.variable}/*.bin**`, async (route) => {
    const match = /fh(\d{3})\.l0\.u16\.bin/.exec(route.request().url());
    await route.fulfill({
      status: 200,
      contentType: 'application/octet-stream',
      body: frameBinary(match ? Number(match[1]) : 0),
    });
  });
}
