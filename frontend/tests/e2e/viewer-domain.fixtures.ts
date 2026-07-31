/**
 * Synthetic region-scoped fixtures for the Phase 2B data-domain contract
 * (docs/PHASE_2B_FRONTEND_DOMAIN_SPLIT_PLAN_2026-07-29.md, D7).
 *
 * No global artifacts exist yet (they arrive in Phase 3), so these fixtures
 * fabricate a capabilities payload whose variable declares
 * `supported_build_regions: ["conus", "global"]` and serve domain-scoped
 * responses on the Phase 2A URL shapes. The specs assert request ROUTING —
 * `domain=` on control APIs, `domains/{d}` on artifact URLs — not real data.
 */
import type { Page } from '@playwright/test';

export const DOMAIN_MODEL = 'gfs';
export const DOMAIN_SECOND_MODEL = 'nam';
export const DOMAIN_RUN_ID = '20260729_12z';
export const DOMAIN_VARIABLE = 'tmp2m';
/**
 * Canonical-only companion variable (global go-live design §2/§3): declares
 * only the canonical build region, so requesting `domain=global` on it must
 * degrade to canonical AND surface the inline note / row badge.
 */
export const DOMAIN_CANONICAL_ONLY_VARIABLE = 'precip_7d_anom';
/**
 * Run-scoped degrade, shape A — capabilities DECLARE `global`, and the global
 * run manifest OMITS the variable entirely.
 */
export const DOMAIN_UNPUBLISHED_GLOBAL_VARIABLE = 'tmp2m_anom';
/**
 * Run-scoped degrade, shape B — THE operator repro. Capabilities declare
 * `global` and the global manifest DOES list the variable, declared-but-unbuilt
 * with zero artifacts behind it. Verified against prod (api.cartosky.com, run
 * 20260731_12z, `?domain=global`), where the Wave-1 anomalies carry exactly
 * `{expected_frames, available_frames: 0, ready_through_fh: null,
 * expected_max_fh, frames: []}` and `/frames` returns `[]`.
 */
export const DOMAIN_UNBUILT_GLOBAL_VARIABLE = 'precip_5d_anom';
export const DOMAIN_ID = 'global';

export const DOMAIN_FRAME_BINARY = new Uint16Array([1320, 1405, 65535, 877]);
/** Global-domain frames are 4x2 (see `gridManifestPayload`), so 8 samples. */
export const DOMAIN_GLOBAL_FRAME_BINARY = new Uint16Array([1320, 1405, 65535, 877, 1500, 1610, 1720, 1830]);

function modelCatalogEntry(modelId: string, options: { supportsGlobal: boolean }) {
  return {
    model_id: modelId,
    name: modelId.toUpperCase(),
    product: 'forecast',
    canonical_region: 'conus',
    defaults: {
      default_var_key: DOMAIN_VARIABLE,
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
      [DOMAIN_VARIABLE]: {
        var_key: DOMAIN_VARIABLE,
        display_name: 'Temperature 2m',
        kind: 'continuous',
        units: 'F',
        group: 'Temperature',
        default_fh: 0,
        buildable: true,
        color_map_id: 'tmp2m',
        render_substrates: ['grid'],
        ...(options.supportsGlobal ? { supported_build_regions: ['conus', DOMAIN_ID] } : {}),
        constraints: {},
        derived: false,
        derive_strategy_id: null,
      },
      [DOMAIN_CANONICAL_ONLY_VARIABLE]: {
        var_key: DOMAIN_CANONICAL_ONLY_VARIABLE,
        display_name: '7-Day Precip Anomaly',
        kind: 'continuous',
        units: 'in',
        group: 'Precipitation',
        default_fh: 0,
        buildable: true,
        color_map_id: 'precip_anom',
        render_substrates: ['grid'],
        supported_build_regions: ['conus'],
        constraints: {},
        derived: false,
        derive_strategy_id: null,
      },
      [DOMAIN_UNPUBLISHED_GLOBAL_VARIABLE]: {
        var_key: DOMAIN_UNPUBLISHED_GLOBAL_VARIABLE,
        display_name: 'Temp Anomaly 2m',
        kind: 'continuous',
        units: 'F',
        group: 'Temperature',
        default_fh: 0,
        buildable: true,
        color_map_id: 'tmp2m',
        render_substrates: ['grid'],
        // DECLARED global — but `manifestPayload` omits it from the global
        // manifest, so only a run-scoped probe can discover it is missing.
        ...(options.supportsGlobal ? { supported_build_regions: ['conus', DOMAIN_ID] } : {}),
        constraints: {},
        derived: false,
        derive_strategy_id: null,
      },
      [DOMAIN_UNBUILT_GLOBAL_VARIABLE]: {
        var_key: DOMAIN_UNBUILT_GLOBAL_VARIABLE,
        display_name: '5-Day Precip Anomaly',
        kind: 'continuous',
        units: 'in',
        group: 'Precipitation',
        default_fh: 0,
        buildable: true,
        color_map_id: 'precip_anom',
        render_substrates: ['grid'],
        // DECLARED global — and PRESENT in the global manifest, but with zero
        // artifacts behind it (the Wave-1 capability flip landing ahead of the
        // ERA5 baselines).
        ...(options.supportsGlobal ? { supported_build_regions: ['conus', DOMAIN_ID] } : {}),
        constraints: {},
        derived: false,
        derive_strategy_id: null,
      },
    },
  };
}

export function capabilityPayload() {
  return {
    contract_version: 'v1',
    supported_models: [DOMAIN_MODEL, DOMAIN_SECOND_MODEL],
    model_catalog: {
      [DOMAIN_MODEL]: modelCatalogEntry(DOMAIN_MODEL, { supportsGlobal: true }),
      [DOMAIN_SECOND_MODEL]: modelCatalogEntry(DOMAIN_SECOND_MODEL, { supportsGlobal: false }),
    },
    availability: Object.fromEntries(
      [DOMAIN_MODEL, DOMAIN_SECOND_MODEL].map((modelId) => [
        modelId,
        {
          latest_run: DOMAIN_RUN_ID,
          published_runs: [DOMAIN_RUN_ID],
          latest_run_ready: true,
          latest_run_ready_vars: [DOMAIN_VARIABLE],
          latest_run_ready_frame_count: 2,
          latest_run_target_max_fh: 1,
          source: 'test',
          time_axis_mode: 'forecast',
        },
      ]),
    ),
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
      midwest: {
        label: 'Midwest',
        bbox: [-104, 36, -82, 49],
        defaultCenter: [-93, 42.5],
        defaultZoom: 5,
        minZoom: 2,
        maxZoom: 9,
      },
      // Global go-live §4 / U3: the full-extent camera. Its bbox is not
      // contained by the canonical (conus) preset, which is what makes the
      // existing coverage filter hide it unless a non-canonical domain is
      // active — no new filter logic exists to test.
      world: {
        label: 'World',
        bbox: [-180, -85, 180, 85],
        defaultCenter: [-30, 25],
        defaultZoom: 1.3,
        minZoom: 0,
        maxZoom: 9,
      },
    },
  };
}

const MANIFEST_FRAMES = [
  { fh: 0, valid_time: '2026-07-29T12:00:00Z' },
  { fh: 1, valid_time: '2026-07-29T13:00:00Z' },
];

/**
 * Run manifest, scoped by DATA DOMAIN exactly like the backend's.
 *
 * Modelled on prod (api.cartosky.com, run 20260731_12z), which scopes the
 * manifest strictly by domain:
 *
 *  - the global manifest lists ONLY domain-declared variables, so every
 *    canonical-only variable is absent from it (shape A degrade, and the
 *    reason the variable picker may not be driven by this manifest);
 *  - a declared-but-unbuilt variable IS listed, with zero artifacts behind it
 *    (shape B — the operator repro);
 *  - `region` reports the domain, which is what the Phase-5 readiness-boundary
 *    guard in App.tsx compares against the effective domain.
 *
 * The healthy global variable is deliberately PARTIALLY built
 * (`available_frames: 2` of 3, `ready_through_fh: 1` under `expected_max_fh: 2`)
 * — the normal mid-cycle state. It must render partial global data with the
 * readiness hatch, never degrade.
 */
export function manifestPayload(modelId: string, domain: string | null = null) {
  const isGlobal = domain === DOMAIN_ID;
  const variables: Record<string, unknown> = {
    [DOMAIN_VARIABLE]: {
      display_name: 'Temperature 2m',
      kind: 'continuous',
      units: 'F',
      frames: MANIFEST_FRAMES,
      ...(isGlobal
        ? { expected_frames: 3, available_frames: 2, ready_through_fh: 1, expected_max_fh: 2 }
        : {}),
    },
  };
  if (isGlobal) {
    variables[DOMAIN_UNBUILT_GLOBAL_VARIABLE] = {
      display_name: '5-Day Precip Anomaly',
      kind: 'continuous',
      units: 'in',
      expected_frames: 105,
      available_frames: 0,
      ready_through_fh: null,
      expected_max_fh: 384,
      frames: [],
    };
  } else {
    variables[DOMAIN_CANONICAL_ONLY_VARIABLE] = {
      display_name: '7-Day Precip Anomaly',
      kind: 'continuous',
      units: 'in',
      frames: MANIFEST_FRAMES,
    };
    variables[DOMAIN_UNPUBLISHED_GLOBAL_VARIABLE] = {
      display_name: 'Temp Anomaly 2m',
      kind: 'continuous',
      units: 'F',
      frames: MANIFEST_FRAMES,
    };
    variables[DOMAIN_UNBUILT_GLOBAL_VARIABLE] = {
      display_name: '5-Day Precip Anomaly',
      kind: 'continuous',
      units: 'in',
      frames: MANIFEST_FRAMES,
    };
  }
  return {
    model: modelId,
    run: DOMAIN_RUN_ID,
    region: isGlobal ? DOMAIN_ID : 'conus',
    variables,
  };
}

export function framesPayload() {
  return [0, 1].map((fh) => ({
    fh,
    has_cog: true,
    run: DOMAIN_RUN_ID,
    valid_time: `2026-07-29T${String(12 + fh).padStart(2, '0')}:00:00Z`,
    meta: {
      meta: {
        valid_time: `2026-07-29T${String(12 + fh).padStart(2, '0')}:00:00Z`,
        units: 'F',
        kind: 'continuous',
        display_name: 'Temperature 2m',
      },
    },
  }));
}

/**
 * Grid manifest whose frame URLs mirror the backend's Phase 2A behavior:
 * canonical requests get canonical `/api/v4/grid/{model}/...` URLs, requests
 * carrying `domain=` get `domains/{d}`-prefixed URLs (`_grid_file_url`).
 *
 * Geometry differs by domain exactly as the real artifacts do: the canonical
 * (NA) domain is EPSG:3857 with a metre bbox, the global domain is EPSG:4326
 * with a degree bbox and a different grid shape. That difference is what makes
 * a domain toggle a manifest-identity change, so the coherence guard in
 * grid-webgl has something real to catch.
 */
export function gridManifestPayload(modelId: string, domain: string | null, varId: string = DOMAIN_VARIABLE) {
  const gridPrefix = domain ? `/api/v4/grid/domains/${domain}/` : '/api/v4/grid/';
  const isGlobal = domain === DOMAIN_ID;
  return {
    manifest_version: 1,
    subtype: 'grid',
    model: modelId,
    run: DOMAIN_RUN_ID,
    var: varId,
    projection: isGlobal ? 'EPSG:4326' : 'EPSG:3857',
    bbox: isGlobal
      ? [-180.0, -90.0, 180.0, 90.0]
      : [-14920000.0, 7356000.0, -14914000.0, 7362000.0],
    grid: {
      width: isGlobal ? 4 : 2,
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
        width: isGlobal ? 4 : 2,
        height: 2,
        frames: [0, 1].map((fh) => ({
          fh,
          file: `fh${String(fh).padStart(3, '0')}.l0.u16.bin`,
          valid_time: `2026-07-29T${String(12 + fh).padStart(2, '0')}:00:00Z`,
          url: `${gridPrefix}${modelId}/${DOMAIN_RUN_ID}/${varId}/fh${String(fh).padStart(3, '0')}.l0.u16.bin?v=${DOMAIN_RUN_ID}-${varId}-${fh}`,
        })),
      },
    ],
  };
}

const ALL_FIXTURE_VARIABLES = [
  DOMAIN_VARIABLE,
  DOMAIN_CANONICAL_ONLY_VARIABLE,
  DOMAIN_UNPUBLISHED_GLOBAL_VARIABLE,
  DOMAIN_UNBUILT_GLOBAL_VARIABLE,
];

/**
 * Stub every route the viewer/compare boot touches and record every
 * `/api/v4/` request URL (pathname + search) into `recordedApiUrls`.
 */
export async function stubViewerDomainRoutes(page: Page, recordedApiUrls: string[]) {
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.includes('/api/v4/')) {
      recordedApiUrls.push(`${url.pathname}${url.search}`);
    }
  });

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

  for (const modelId of [DOMAIN_MODEL, DOMAIN_SECOND_MODEL]) {
    await page.route(`**/api/v4/${modelId}/runs**`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([DOMAIN_RUN_ID]) });
    });
    for (const runSegment of ['latest', DOMAIN_RUN_ID]) {
      await page.route(`**/api/v4/${modelId}/${runSegment}/manifest**`, async (route) => {
        const domain = new URL(route.request().url()).searchParams.get('domain');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(manifestPayload(modelId, domain)),
        });
      });
      for (const varId of ALL_FIXTURE_VARIABLES) {
        await page.route(`**/api/v4/${modelId}/${runSegment}/${varId}/frames**`, async (route) => {
          // Prod returns [] for a declared-but-unbuilt variable in its domain.
          const domain = new URL(route.request().url()).searchParams.get('domain');
          const unbuilt = domain === DOMAIN_ID && varId === DOMAIN_UNBUILT_GLOBAL_VARIABLE;
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(unbuilt ? [] : framesPayload()),
          });
        });
        await page.route(`**/api/v4/${modelId}/${runSegment}/${varId}/grid-manifest**`, async (route) => {
          const requestUrl = new URL(route.request().url());
          const domain = requestUrl.searchParams.get('domain');
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(gridManifestPayload(modelId, domain, varId)),
          });
        });
      }
    }
    // Canonical and domain-scoped grid binaries.
    for (const varId of ALL_FIXTURE_VARIABLES) {
      await page.route(`**/api/v4/grid/${modelId}/${DOMAIN_RUN_ID}/${varId}/*.bin**`, async (route) => {
        await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(DOMAIN_FRAME_BINARY.buffer) });
      });
      await page.route(`**/api/v4/grid/domains/${DOMAIN_ID}/${modelId}/${DOMAIN_RUN_ID}/${varId}/*.bin**`, async (route) => {
        await route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from(DOMAIN_GLOBAL_FRAME_BINARY.buffer) });
      });
    }
  }
}
