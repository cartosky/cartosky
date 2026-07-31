/**
 * Manifest/frame coherence regression pin.
 *
 * Toggling Coverage (NA <-> Global) swaps the grid manifest to a completely
 * different quad geometry AND shader branch (NA = EPSG:3857 metre bbox,
 * global = EPSG:4326 degree bbox). Before the fix, `GridWebglLayerController`
 * installed the new manifest immediately while still holding the previous
 * domain's frame texture, so intermediate draws paired new geometry with old
 * data — the operator-visible "completely off map" flash.
 *
 * The load-bearing assertions are the in-render counters: only an in-render
 * check is race-free, because a React-effect-level assertion observes state
 * after the offending frame has already been painted.
 *
 * Three properties are pinned, and all three are needed — dropping any one
 * leaves a broken build passing:
 *   1. `drewMismatched === 0`  — never draws an incoherent pair.
 *   2. `held > 0`              — the mismatch was really reached and held,
 *                                not merely absent by timing luck.
 *   3. `coherentDraws` rises   — the hold CONVERGES. A build that holds
 *                                forever satisfies (1) and (2) trivially.
 */
import { test, expect, type Page } from '@playwright/test';

import {
  DOMAIN_ID,
  DOMAIN_MODEL,
  DOMAIN_SECOND_MODEL,
  DOMAIN_VARIABLE,
  gridManifestPayload,
  stubViewerDomainRoutes,
} from './viewer-domain.fixtures';

interface CoherenceStats {
  held: number;
  drewMismatched: number;
  coherentDraws: number;
  reconciled: number;
  resetForReupload: number;
}

declare global {
  interface Window {
    __cartoskyGridCoherence?: {
      read: () => CoherenceStats;
      reset: () => void;
      visibleFrameHour: (layerId?: string) => number | null;
    };
  }
}

/** Null-safe: the debug surface only exists once the grid module has loaded. */
const readStats = async (page: Page): Promise<CoherenceStats> =>
  page.evaluate(
    () =>
      window.__cartoskyGridCoherence?.read() ?? {
        held: 0,
        drewMismatched: 0,
        coherentDraws: 0,
        reconciled: 0,
        resetForReupload: 0,
      },
  );

const readVisibleFrameHour = async (page: Page): Promise<number | null> =>
  page.evaluate(() => window.__cartoskyGridCoherence?.visibleFrameHour() ?? null);

/**
 * The fixture frames are a handful of bytes and are fulfilled from memory, so
 * the window between "new manifest applied" and "new frame texture uploaded"
 * collapses to less than one animation frame — the race exists but cannot be
 * observed. Prod frames are megabytes over the network, which is exactly why
 * operators see the flash. Delay the grid binaries so the window is real and
 * the test is deterministic; registered after `stubViewerDomainRoutes` so it
 * wins.
 */
const FRAME_LATENCY_MS = 400;

async function delayGridBinaries(page: Page) {
  await page.route('**/api/v4/grid/**/*.bin**', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, FRAME_LATENCY_MS));
    await route.fallback();
  });
}

/**
 * Wait for the layer to actually be DRAWING, rather than sleeping a fixed
 * interval. A fixed settle silently starves the setup whenever frame latency
 * grows, which turns a real assertion into a false failure.
 */
async function waitForCoherentDraws(page: Page, atLeast = 1) {
  await expect
    .poll(async () => (await readStats(page)).coherentDraws, { timeout: 30_000 })
    .toBeGreaterThanOrEqual(atLeast);
}

/** Settle to a drawing state, then zero the counters for the measurement. */
async function settleAndReset(page: Page) {
  await waitForCoherentDraws(page);
  await page.evaluate(() => window.__cartoskyGridCoherence?.reset());
}

/**
 * Sample the capture gate and the hold counter together across the swap. Runs
 * in-page on rAF so it observes the same frames the renderer does — sampling
 * from the test process would alias straight past the hold window.
 */
async function sampleGateDuringSwap(page: Page, durationMs: number) {
  return page.evaluate(async (duration) => {
    const api = window.__cartoskyGridCoherence!;
    const series: { held: number; heldDelta: number; hour: number | null }[] = [];
    let previousHeld = api.read().held;
    const startedAt = performance.now();
    await new Promise<void>((resolve) => {
      const tick = () => {
        const held = api.read().held;
        series.push({ held, heldDelta: held - previousHeld, hour: api.visibleFrameHour() });
        previousHeld = held;
        if (performance.now() - startedAt >= duration) {
          resolve();
          return;
        }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    return series;
  }, durationMs);
}

test.describe('Grid manifest/frame coherence', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium-only WebGL contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop rail contract.');
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  test('coverage toggle never draws a frame against the wrong manifest, and converges', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);
    await delayGridBinaries(page);

    await page.goto(`/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus`);
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));

    const coverage = page.getByTestId('coverage-control');
    await expect(coverage).toBeVisible();
    await expect(coverage.getByRole('radio', { name: 'CONUS' })).toHaveAttribute('aria-checked', 'true');

    expect(await page.evaluate(() => typeof window.__cartoskyGridCoherence?.read)).toBe('function');

    // Canonical (NA / EPSG:3857) frame is on screen before we measure anything.
    await expect
      .poll(() => recorded.some((url) => url.includes(`/api/v4/grid/${DOMAIN_MODEL}/`)))
      .toBe(true);
    await settleAndReset(page);

    // --- NA -> Global -------------------------------------------------------
    await coverage.getByRole('radio', { name: 'Global' }).click();
    // Sample the capture gate across the swap window (F2): while the layer is
    // holding it must report "nothing visible", or a capture taken mid-swap
    // would silently export a gridless map.
    const forwardSeries = await sampleGateDuringSwap(page, FRAME_LATENCY_MS * 3);

    await expect.poll(() => new URL(page.url()).searchParams.get('domain')).toBe(DOMAIN_ID);
    await expect
      .poll(() => recorded.some((url) => url.includes(`/api/v4/grid/domains/${DOMAIN_ID}/${DOMAIN_MODEL}/`)))
      .toBe(true);

    const heldSamples = forwardSeries.filter((sample) => sample.heldDelta > 0);
    expect(heldSamples.length, 'the swap must actually reach the hold').toBeGreaterThan(0);
    expect(
      heldSamples.every((sample) => sample.hour === null),
      'visibleFrameHour must report not-visible while the coherence guard holds',
    ).toBe(true);

    // Converges: drawing again, and never drew a mismatched pair.
    await waitForCoherentDraws(page);
    const afterForward = await readStats(page);
    expect(afterForward.drewMismatched).toBe(0);
    expect(afterForward.held).toBeGreaterThan(0);
    expect(afterForward.coherentDraws).toBeGreaterThan(0);
    // Gate reopens once the pair is coherent again.
    expect(await readVisibleFrameHour(page)).not.toBeNull();

    // --- Global -> NA -------------------------------------------------------
    await page.evaluate(() => window.__cartoskyGridCoherence?.reset());
    await coverage.getByRole('radio', { name: 'CONUS' }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('domain')).toBeNull();

    await waitForCoherentDraws(page);
    const afterBack = await readStats(page);
    // No `held > 0` here: the canonical frame's texture is still in the GPU
    // cache from the initial load, so the return leg can complete inside a
    // single tick with no hold window at all. The forward leg above is what
    // proves the hold path runs; this leg proves the return stays coherent
    // and still converges.
    expect(afterBack.drewMismatched).toBe(0);
    expect(afterBack.coherentDraws).toBeGreaterThan(0);

    await expect(coverage.getByRole('radio', { name: 'CONUS' })).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByTestId('viewer-map-slot')).toBeVisible();
  });

  test('model switching keeps the same coherence guarantee', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);
    await delayGridBinaries(page);

    await page.goto(`/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus`);
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));
    await settleAndReset(page);

    await page.goto(`/viewer?m=${DOMAIN_SECOND_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus`);
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));
    await waitForCoherentDraws(page);

    const stats = await readStats(page);
    expect(stats.drewMismatched).toBe(0);
    expect(stats.coherentDraws).toBeGreaterThan(0);
  });
});

/**
 * A manifest whose identity fields move while the frame URLs stay put.
 *
 * App background-polls the same run's grid manifest every 30s and applies any
 * JSON-different response. The texture's manifest stamp is only written when a
 * frame is (re)activated, which keys off the frame SIGNATURE — and the
 * signature carries no manifest identity. Without an explicit convergence
 * step the guard would hold forever: no draw, no loader, no recovery.
 */
test.describe('Grid manifest identity drift on the same run', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium-only WebGL contract suite.');

  // The production poll interval is 30s and lives in App.tsx, which this suite
  // must not modify, so the test waits it out.
  test.setTimeout(300_000);

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop rail contract.');
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  type Mutation = 'none' | 'scale' | 'projection-bbox' | 'bbox';

  /**
   * Serves a mutated grid manifest once `state.mutation` flips. Frame URLs are
   * left untouched, so the frame signature is unchanged — precisely the case
   * the render guard alone cannot resolve.
   */
  async function stubDriftingGridManifest(page: Page, state: { mutation: Mutation }) {
    await page.route(`**/api/v4/${DOMAIN_MODEL}/*/${DOMAIN_VARIABLE}/grid-manifest**`, async (route) => {
      const requestUrl = new URL(route.request().url());
      const payload = gridManifestPayload(
        DOMAIN_MODEL,
        requestUrl.searchParams.get('domain'),
        DOMAIN_VARIABLE,
      ) as unknown as { grid: Record<string, unknown>; bbox: number[]; projection: string };
      if (state.mutation === 'scale') {
        payload.grid.scale = Number(payload.grid.scale) * 2;
      } else if (state.mutation === 'projection-bbox') {
        payload.projection = 'EPSG:3857';
        payload.bbox = [-14000000, 2800000, -7000000, 7000000];
      } else if (state.mutation === 'bbox') {
        payload.bbox = [-170, -80, 170, 80];
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(payload),
      });
    });
  }

  /**
   * All three mutations run against ONE page session: each has to wait out a
   * real 30s poll, so separate tests would triple the wall time (and the
   * exposure to a dev server dying mid-run) for no extra coverage.
   */
  test('same-run manifest drift always converges to drawing', async ({ page }) => {
    const recorded: string[] = [];
    const state: { mutation: Mutation } = { mutation: 'none' };
    await stubViewerDomainRoutes(page, recorded);
    await stubDriftingGridManifest(page, state);

    // Global domain: a full-world 4326 quad actually covers the viewport, so
    // a decode-parameter change is visible in the rendered pixels.
    await page.goto(
      `/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus&domain=${DOMAIN_ID}`,
    );
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));
    await settleAndReset(page);

    const mapCanvas = page.locator('canvas.maplibregl-canvas').first();
    const pixelsBeforeAnyDrift = await mapCanvas.screenshot();
    let convergences = 0;

    for (const mutation of ['scale', 'projection-bbox', 'bbox'] as const) {
      state.mutation = mutation;

      // Wait out the 30s poll and let the mutated manifest be applied. The
      // reconcile runs in `update()`, so in practice it lands before any frame
      // can hold — the point is that it lands AT ALL and is bounded.
      await expect
        .poll(async () => {
          const stats = await readStats(page);
          return stats.reconciled + stats.resetForReupload;
        }, { timeout: 90_000, intervals: [1000] })
        .toBeGreaterThan(convergences);
      convergences = (await readStats(page)).reconciled + (await readStats(page)).resetForReupload;

      // The hold must not be unbounded: `held` stops climbing.
      const heldAfterConvergence = (await readStats(page)).held;
      await page.waitForTimeout(1500);
      expect((await readStats(page)).held, `${mutation}: hold must be bounded`).toBe(heldAfterConvergence);

      // Converged to DRAWING, not to holding. MapLibre parks the render loop
      // once the scene is static, so nudge the viewport to demand fresh frames
      // and require that they actually get drawn.
      const drawsBeforeNudge = (await readStats(page)).coherentDraws;
      await page.setViewportSize({ width: 1436, height: 900 });
      await expect
        .poll(async () => (await readStats(page)).coherentDraws, { timeout: 30_000 })
        .toBeGreaterThan(drawsBeforeNudge);
      await page.setViewportSize({ width: 1440, height: 900 });

      // Nothing incoherent was ever drawn. Because the manifest identity key
      // covers scale/offset/nodata/bbox/projection, `drewMismatched === 0`
      // combined with fresh draws is precisely the statement "every frame
      // drawn since the change used the NEW decode parameters".
      expect((await readStats(page)).drewMismatched, `${mutation}: tripwire`).toBe(0);

      // The capture gate is open again.
      expect(await readVisibleFrameHour(page), `${mutation}: capture gate`).not.toBeNull();
    }

    // The decode/geometry changes really did reach the screen.
    await expect
      .poll(async () => (await mapCanvas.screenshot()).equals(pixelsBeforeAnyDrift), { timeout: 15_000 })
      .toBe(false);
  });
});
