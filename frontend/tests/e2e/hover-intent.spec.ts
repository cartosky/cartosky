/**
 * ════════════════════════════════════════════════════════════════════════════
 * HOVER INTENT — drag suppression + stationary dwell (Globe v1.1)
 * ════════════════════════════════════════════════════════════════════════════
 *
 * Operator report: click-dragging the globe to spin it pops the sample readout,
 * and it can stick until the pan ends. Two mechanisms fix it, both in the one
 * shared hover path (`src/lib/hover-intent.ts`, wired in map-canvas.tsx), so
 * FLAT AND GLOBE — and the viewer and the compare panes — are covered by the
 * same code. This suite pins them on both projections and on compare.
 *
 * EVERY suppression assertion is on the SAMPLE REQUEST, not on tooltip
 * visibility. A readout that is merely hidden while its fetch still goes out
 * would leave the bug half-fixed (and would still burn the API budget on a
 * gesture the user never meant as a hover). Tooltip assertions are added on top
 * where the *user-visible* symptom is the point.
 *
 * The state machine's own edge cases (jitter tolerance, the fast-move hide
 * rule, inertia, timer races) are unit-tested with a mocked clock in
 * `tests/unit/hover-intent.test.ts`; this suite proves the wiring.
 *
 * Fixtures are the render-golden fixtures verbatim. Chromium/SwiftShader only.
 */
import { expect, test, type Page } from '@playwright/test';

import {
  GOLDEN_VIEW,
  goldenCase,
  stubGoldenBaselineRoutes,
  type GoldenCaseId,
} from './render-golden-baseline.fixtures';

/**
 * Comfortably longer than the ~365 ms measured end-to-end first-readout latency
 * (HOVER_DWELL_MS 150 + the sampling hook's own 80 ms debounce + fetch + React
 * commit). Nothing here races the dwell: every "should sample"
 * assertion polls, and every "should not sample" assertion first parks the
 * cursor for a period the dwell could not survive.
 */
const SETTLE_MS = 900;

/** One sweep step. 30 ms < 150 ms dwell, so the dwell can never complete. */
const SWEEP_STEP_MS = 30;
/** > HOVER_DWELL_TOLERANCE_PX (4), so every step re-arms the dwell. */
const SWEEP_STEP_PX = 20;

async function openViewer(
  page: Page,
  id: GoldenCaseId,
  sampleRequests: string[],
  search = '',
): Promise<void> {
  const spec = goldenCase(id);
  const view = spec.view ?? GOLDEN_VIEW;
  await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  await stubGoldenBaselineRoutes(page, id, undefined, sampleRequests);
  await page.goto(
    `/viewer?m=${spec.model}&r=latest&v=${spec.variable}&fh=0&reg=conus` +
      `&lat=${view.lat}&lon=${view.lon}&z=${view.zoom}&screenshot=1${search}`,
  );
  await expect
    .poll(() => page.evaluate(() => document.documentElement.getAttribute('data-viewer-ready')), {
      timeout: 60_000,
      message: `${id}: viewer ready`,
    })
    .toBe('1');
  // The opening fitBounds/easeTo is itself a camera move, and a camera move is
  // suppression — wait it out so nothing below is measuring the boot animation.
  // (The 600 ms ease plus a margin; `data-viewer-ready` already required
  // map_idle, so this is belt and braces rather than the real gate.)
  await page.waitForTimeout(1_500);
}

const tooltip = (page: Page) => page.getByTestId('map-sample-tooltip');

/** Park the cursor and wait long enough for the dwell to be earned. */
async function restAt(page: Page, x: number, y: number): Promise<void> {
  await page.mouse.move(x, y);
  await page.waitForTimeout(SETTLE_MS);
}

/**
 * A continuous cursor sweep: N moves, each further than the dwell tolerance and
 * closer together in time than the dwell. Under the pre-v1.1 behaviour every
 * one of these would have produced a debounced sample request.
 */
async function sweep(page: Page, x: number, y: number, steps: number, dx = SWEEP_STEP_PX) {
  for (let step = 1; step <= steps; step += 1) {
    await page.mouse.move(x + step * dx, y);
    await page.waitForTimeout(SWEEP_STEP_MS);
  }
}

test.describe('Hover intent — drag suppression and stationary dwell', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium/SwiftShader contract suite.');
  test.describe.configure({ mode: 'serial' });
  test.use({ viewport: { width: 1200, height: 860 } });

  test.beforeEach(() => {
    test.skip(/Mobile/.test(test.info().project.name), 'Pointer hover is a desktop affordance.');
  });

  // ── 1. Flat map: the dwell ────────────────────────────────────────────────
  test('flat: a continuous sweep never samples, and resting does', async ({ page }) => {
    test.setTimeout(180_000);
    const sampleRequests: string[] = [];
    await openViewer(page, 'gfs-tmp2m', sampleRequests);

    const canvas = page.locator('canvas.maplibregl-canvas').first();
    const box = (await canvas.boundingBox())!;
    const startX = box.x + box.width * 0.3;
    const y = box.y + box.height / 2;

    // (a) THE DWELL. 15 steps x 30 ms = 450 ms of continuous movement, every
    // step past the 4 px tolerance, so the 150 ms dwell restarts before it can
    // ever fire. Not one request, and nothing on screen.
    sampleRequests.length = 0;
    await sweep(page, startX, y, 15);
    expect(sampleRequests, 'a sweep across the map is not a hover').toEqual([]);
    await expect(tooltip(page)).toBeHidden();

    // (b) The probe is live: park the cursor and it samples.
    await page.waitForTimeout(SETTLE_MS);
    await expect
      .poll(() => sampleRequests.length, { timeout: 15_000, message: 'resting must sample' })
      .toBeGreaterThan(0);
    await expect(tooltip(page)).toBeVisible();

    // (c) Once visible it tracks the cursor live — no second dwell to earn.
    const shownAt = sampleRequests.length;
    await page.mouse.move(startX + 3, y);
    await page.mouse.move(startX + 6, y);
    await expect(tooltip(page), 'slow exploration must not flicker').toBeVisible();
    expect(sampleRequests.length, 'and keeps sampling as it moves').toBeGreaterThanOrEqual(shownAt);
  });

  // ── 2. Flat map: drag suppression ─────────────────────────────────────────
  test('flat: a click-drag pan issues no sample request and drops the readout', async ({ page }) => {
    test.setTimeout(180_000);
    const sampleRequests: string[] = [];
    await openViewer(page, 'gfs-tmp2m', sampleRequests);

    const canvas = page.locator('canvas.maplibregl-canvas').first();
    const box = (await canvas.boundingBox())!;
    const startX = box.x + box.width * 0.5;
    const y = box.y + box.height / 2;

    // Get the readout up first, so the drag has something to stick.
    await restAt(page, startX, y);
    await expect(tooltip(page), 'precondition: a readout is showing').toBeVisible();

    sampleRequests.length = 0;
    await page.mouse.down();
    await page.mouse.move(startX - 40, y);
    // THE OPERATOR SYMPTOM: it must be gone on the first drag move, not at
    // the end of the pan.
    await expect(tooltip(page), 'the readout drops the instant the pan starts').toBeHidden();
    for (let step = 2; step <= 10; step += 1) {
      await page.mouse.move(startX - 40 * step, y + step * 4);
      await page.waitForTimeout(SWEEP_STEP_MS);
    }
    expect(sampleRequests, 'no sampling mid-drag').toEqual([]);
    await expect(tooltip(page), 'and none appears during the pan').toBeHidden();

    await page.mouse.up();
    // Inertia: `map.isMoving()` stays true after the release, and the readout
    // must stay down through it rather than reappearing over a coasting map.
    await page.waitForTimeout(SETTLE_MS);
    expect(sampleRequests, 'no sampling through release + inertia').toEqual([]);

    // Recovery: the map still hovers normally once everything settles.
    await restAt(page, startX + 30, y + 30);
    await expect
      .poll(() => sampleRequests.length, { timeout: 15_000, message: 'hover recovers after the pan' })
      .toBeGreaterThan(0);
    await expect(tooltip(page)).toBeVisible();
  });

  // ── 3. Globe: the reported case ───────────────────────────────────────────
  test('globe: dragging the disc issues no sample request and drops the readout', async ({ page }) => {
    test.setTimeout(180_000);
    const sampleRequests: string[] = [];
    await openViewer(page, 'globe-4326-world', sampleRequests, '&proj=globe');

    const canvas = page.locator('canvas.maplibregl-canvas').first();
    const box = (await canvas.boundingBox())!;
    // Disc centre: guaranteed on the planet, so the existing disc gate is not
    // the thing being measured here.
    const centre = await page.evaluate(() => {
      const map = (window as unknown as {
        __cartoskyGlobe: { map: { transform: { width: number; height: number } } };
      }).__cartoskyGlobe.map;
      return { x: map.transform.width / 2, y: map.transform.height / 2 };
    });
    const x = box.x + centre.x;
    const y = box.y + centre.y;

    await restAt(page, x, y);
    await expect(tooltip(page), 'precondition: a readout is showing on the disc').toBeVisible();

    sampleRequests.length = 0;
    await page.mouse.down();
    await page.mouse.move(x - 30, y);
    await expect(tooltip(page), 'spinning the globe must not carry a readout').toBeHidden();
    for (let step = 2; step <= 10; step += 1) {
      await page.mouse.move(x - 30 * step, y + step * 3);
      await page.waitForTimeout(SWEEP_STEP_MS);
    }
    expect(sampleRequests, 'no sampling while the globe spins').toEqual([]);
    await page.mouse.up();
    await page.waitForTimeout(SETTLE_MS);
    expect(sampleRequests, 'no sampling through the spin-down').toEqual([]);
    await expect(tooltip(page)).toBeHidden();
  });

  // ── 4. Globe: the dwell, on the sphere ────────────────────────────────────
  test('globe: a sweep across the disc never samples, and resting on it does', async ({ page }) => {
    test.setTimeout(180_000);
    const sampleRequests: string[] = [];
    await openViewer(page, 'globe-4326-world', sampleRequests, '&proj=globe');

    const canvas = page.locator('canvas.maplibregl-canvas').first();
    const box = (await canvas.boundingBox())!;
    // Sweep along a chord that stays inside the disc, found from MapLibre's own
    // ray/planet test so the disc gate cannot be what suppresses the sweep.
    const geometry = await page.evaluate(() => {
      const map = (window as unknown as {
        __cartoskyGlobe: {
          map: {
            transform: { width: number; height: number } & {
              isPointOnMapSurface: (p: { x: number; y: number }) => boolean;
            };
          };
        };
      }).__cartoskyGlobe.map;
      const transform = map.transform;
      const cx = transform.width / 2;
      const cy = transform.height / 2;
      let lo = 0;
      let hi = Math.max(transform.width, transform.height) * 4;
      for (let i = 0; i < 40; i += 1) {
        const mid = (lo + hi) / 2;
        if (transform.isPointOnMapSurface({ x: cx + mid, y: cy })) lo = mid;
        else hi = mid;
      }
      return { cx, cy, limb: lo };
    });
    const span = geometry.limb * 1.4; // -0.7 R .. +0.7 R, comfortably inside
    const steps = 12;
    const dx = span / steps;
    const startX = box.x + geometry.cx - span / 2;
    const y = box.y + geometry.cy;

    sampleRequests.length = 0;
    await sweep(page, startX, y, steps, dx);
    expect(sampleRequests, 'a sweep across the sphere is not a hover').toEqual([]);
    await expect(tooltip(page)).toBeHidden();

    await page.waitForTimeout(SETTLE_MS);
    await expect
      .poll(() => sampleRequests.length, { timeout: 15_000, message: 'resting on the disc must sample' })
      .toBeGreaterThan(0);
    await expect(tooltip(page)).toBeVisible();
  });

  // ── 5. Compare inherits, because it is the same funnel ────────────────────
  test('compare: panes still sample on a dwell and stay silent through a drag', async ({ page }) => {
    test.setTimeout(180_000);
    const sampleRequests: string[] = [];
    const spec = goldenCase('gfs-tmp2m');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
    await stubGoldenBaselineRoutes(page, 'gfs-tmp2m', undefined, sampleRequests);
    await page.goto(
      `/compare?lm=${spec.model}&lv=${spec.variable}&lr=latest` +
        `&rm=${spec.model}&rv=${spec.variable}&rr=latest&fh=0`,
    );
    const canvas = page.locator('canvas.maplibregl-canvas').first();
    await expect(canvas).toBeVisible({ timeout: 60_000 });
    await page.waitForTimeout(4_000);

    const box = (await canvas.boundingBox())!;
    const x = box.x + box.width * 0.5;
    const y = box.y + box.height * 0.5;

    // (a) The dwell reaches compare — a rest still samples. Compare is a
    // SEPARATE useSampleTooltip instance per pane but the same MapCanvas hover
    // handler, so this is the assertion that the wiring is in the shared path
    // and not in the viewer page.
    sampleRequests.length = 0;
    await restAt(page, x, y);
    await expect
      .poll(() => sampleRequests.length, { timeout: 15_000, message: 'compare pane must still sample' })
      .toBeGreaterThan(0);

    // (b) And a pane drag is suppressed just like the viewer's.
    sampleRequests.length = 0;
    await page.mouse.down();
    for (let step = 1; step <= 8; step += 1) {
      await page.mouse.move(x - 25 * step, y + step * 3);
      await page.waitForTimeout(SWEEP_STEP_MS);
    }
    await page.mouse.up();
    await page.waitForTimeout(SETTLE_MS);
    expect(sampleRequests, 'no sampling while a compare pane is panned').toEqual([]);
  });
});
