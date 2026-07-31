/**
 * Phase 3 sounding-panel contract (docs/SKEWT_DESIGN_2026-07-30.md §6, §7).
 *
 * Drives the real viewer against synthetic HRRR + sounding fixtures and asserts
 * the behaviours the design pins: the toggle ARMS a pick mode (plain clicks are
 * untouched when it is off), the panel renders both traces and the barb ladder,
 * the dewpoint trace is drawn FULL height (decision #3), below-ground levels
 * never reach the plot (§2 surface anchoring), the scrubber changes frame, and
 * `sounding=` round-trips through the permalink.
 *
 * Phase 4 adds the parcel path, the LCL/LFC/EL ticks and the indices readout —
 * including the capped-frame case where LFC/EL and the HRRR SBCAPE row are null
 * and must simply not render.
 */
import { expect, test } from '@playwright/test';

import {
  NO_SOUNDING_MODEL,
  SOUNDING_EXPECTED_MASKED_LEVELS,
  SOUNDING_MODEL,
  SOUNDING_PARCEL_DEFINITION,
  SOUNDING_SURFACE_PRESSURE,
  SOUNDING_VARIABLE,
  stubSoundingRoutes,
  type SoundingRequestLog,
} from './sounding-panel.fixtures';

const VIEWER_URL = `/viewer?m=${SOUNDING_MODEL}&r=latest&v=${SOUNDING_VARIABLE}&fh=0&reg=conus`;

/** Same log-p mapping the renderer uses, for asserting on emitted paths. */
function yForPressure(p: number, y0: number, plotH: number): number {
  const logBot = Math.log(1050);
  const logTop = Math.log(100);
  return y0 + plotH - ((logBot - Math.log(p)) / (logBot - logTop)) * plotH;
}

function pathYs(d: string): number[] {
  return [...d.matchAll(/[ML](-?[\d.]+) (-?[\d.]+)/g)].map((match) => Number(match[2]));
}

test.describe('Sounding panel', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium-only contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop docked-panel contract.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  async function bootViewer(page: import('@playwright/test').Page, requests: SoundingRequestLog) {
    await stubSoundingRoutes(page, requests);
    await page.goto(VIEWER_URL);
    await expect(page.locator('.maplibregl-canvas')).toBeVisible();
    return page.locator('[data-testid="viewer-map-slot"]');
  }

  test('a plain map click does nothing until the toggle arms the mode', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    const mapSlot = await bootViewer(page, requests);

    await mapSlot.click({ position: { x: 320, y: 220 } });
    await page.waitForTimeout(500);
    expect(requests).toHaveLength(0);
    await expect(page.locator('[data-testid="sounding-panel"]')).toHaveCount(0);

    await page.locator('[data-testid="sounding-toggle"]').click();
    await expect(page.locator('[data-testid="sounding-toggle"]')).toHaveAttribute('aria-pressed', 'true');
    await mapSlot.click({ position: { x: 320, y: 220 } });

    await expect(page.locator('[data-testid="sounding-panel"]')).toBeVisible();
    expect(requests).toHaveLength(1);
    expect(requests[0].model).toBe(SOUNDING_MODEL);
  });

  test('the panel renders both traces, the barb ladder and the header readout', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    const mapSlot = await bootViewer(page, requests);
    await page.locator('[data-testid="sounding-toggle"]').click();
    await mapSlot.click({ position: { x: 320, y: 220 } });

    const chart = page.locator('[data-testid="skewt-chart"]');
    await expect(chart).toBeVisible();

    const tPath = await page.locator('[data-testid="skewt-trace-t"]').getAttribute('d');
    const tdPath = await page.locator('[data-testid="skewt-trace-td"]').getAttribute('d');
    expect(tPath && tPath.length).toBeGreaterThan(0);
    expect(tdPath && tdPath.length).toBeGreaterThan(0);
    // A frame with a null level must break the pen, never bridge or emit NaN.
    expect(tPath).not.toContain('NaN');
    expect(tdPath).not.toContain('NaN');
    expect((tPath ?? '').match(/M/g)).toHaveLength(2);

    // Wind barbs are drawn from the surface block upward.
    const barbCount = Number(await chart.getAttribute('data-barbs'));
    expect(barbCount).toBeGreaterThan(3);

    await expect(page.locator('[data-testid="sounding-run"]')).toHaveText(/\d{8}_\d{2}z/);
    await expect(page.locator('[data-testid="sounding-grid-point"]')).toContainText('°');
    await expect(page.locator('[data-testid="sounding-distance"]')).toContainText('km from click');
    await expect(page.locator('[data-testid="sounding-marker"]')).toHaveCount(1);
  });

  test('the dewpoint trace is drawn to the top of the plot and below-ground levels are absent', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    const mapSlot = await bootViewer(page, requests);
    await page.locator('[data-testid="sounding-toggle"]').click();
    await mapSlot.click({ position: { x: 320, y: 220 } });

    const chart = page.locator('[data-testid="skewt-chart"]');
    await expect(chart).toBeVisible();

    const viewBox = (await chart.getAttribute('viewBox')) ?? '';
    const [, , , totalHeight] = viewBox.split(/\s+/).map(Number);
    // Margins are fixed constants in lib/skewt-geometry.ts.
    const y0 = 26;
    const plotH = totalHeight - 26 - 46;

    const tdYs = pathYs((await page.locator('[data-testid="skewt-trace-td"]').getAttribute('d')) ?? '');
    const tYs = pathYs((await page.locator('[data-testid="skewt-trace-t"]').getAttribute('d')) ?? '');

    // Decision #3: Td is full height — it reaches the 100 hPa top like T does.
    expect(Math.min(...tdYs)).toBeCloseTo(yForPressure(100, y0, plotH), 1);
    expect(Math.min(...tYs)).toBeCloseTo(yForPressure(100, y0, plotH), 1);

    // §2 surface anchoring: nothing is drawn at or below the ground.
    const groundY = yForPressure(SOUNDING_SURFACE_PRESSURE, y0, plotH);
    expect(Math.max(...tdYs)).toBeLessThanOrEqual(groundY + 0.2);
    expect(Math.max(...tYs)).toBeLessThanOrEqual(groundY + 0.2);
    expect(Number(await chart.getAttribute('data-masked-levels'))).toBe(
      SOUNDING_EXPECTED_MASKED_LEVELS,
    );
    await expect(page.locator('[data-testid="skewt-ground-line"]')).toHaveCount(1);
  });

  test('the in-panel scrubber changes the rendered frame', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    const mapSlot = await bootViewer(page, requests);
    await page.locator('[data-testid="sounding-toggle"]').click();
    await mapSlot.click({ position: { x: 320, y: 220 } });
    await expect(page.locator('[data-testid="skewt-chart"]')).toBeVisible();

    const scrubber = page.locator('[data-testid="sounding-scrubber"]');
    await expect(scrubber).toHaveValue('0');
    const firstValidTime = await page.locator('[data-testid="sounding-valid-time"]').textContent();
    const firstPath = await page.locator('[data-testid="skewt-trace-t"]').getAttribute('d');

    await scrubber.fill('2');
    await expect(scrubber).toHaveValue('2');
    await expect(page.locator('[data-testid="sounding-valid-time"]')).not.toHaveText(
      firstValidTime ?? '',
    );
    expect(await page.locator('[data-testid="skewt-trace-t"]').getAttribute('d')).not.toBe(firstPath);

    // Scrubbing is panel-local: it never issues a second sounding request.
    expect(requests).toHaveLength(1);
  });

  test('the parcel path, the LCL/LFC/EL ticks and the indices readout render', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    const mapSlot = await bootViewer(page, requests);
    await page.locator('[data-testid="sounding-toggle"]').click();
    await mapSlot.click({ position: { x: 320, y: 220 } });
    await expect(page.locator('[data-testid="skewt-chart"]')).toBeVisible();

    // The parcel is drawn, dashed, and distinct from both traces.
    const parcel = page.locator('[data-testid="skewt-parcel-path"]');
    await expect(parcel).toHaveCount(1);
    const parcelPath = (await parcel.getAttribute('d')) ?? '';
    expect(parcelPath.length).toBeGreaterThan(0);
    expect(parcelPath).not.toContain('NaN');
    await expect(parcel).toHaveAttribute('stroke-dasharray', '4 3');
    const tPath = await page.locator('[data-testid="skewt-trace-t"]').getAttribute('d');
    expect(parcelPath).not.toBe(tPath);
    await expect(page.locator('[data-testid="skewt-parcel-path"]')).not.toHaveAttribute(
      'stroke',
      (await page.locator('[data-testid="skewt-trace-t"]').getAttribute('stroke')) ?? '',
    );

    // Frame 0 has all three levels; the legend gained its row.
    await expect(page.locator('[data-testid="skewt-level-lcl"]')).toBeVisible();
    await expect(page.locator('[data-testid="skewt-level-lfc"]')).toBeVisible();
    await expect(page.locator('[data-testid="skewt-level-el"]')).toBeVisible();
    await expect(page.locator('[data-testid="skewt-legend"]')).toContainText('Parcel path');

    // Readout values are the scrubbed frame's, formatted, with units.
    await expect(page.locator('[data-testid="sounding-index-sbcape"]')).toHaveText('1200');
    await expect(page.locator('[data-testid="sounding-index-sbcin"]')).toHaveText('-12');
    await expect(page.locator('[data-testid="sounding-index-mlcape"]')).toHaveText('640');
    await expect(page.locator('[data-testid="sounding-index-mlcin"]')).toHaveText('-55');
    await expect(page.locator('[data-testid="sounding-index-pwat"]')).toHaveText('38.4 mm');
    await expect(page.locator('[data-testid="sounding-index-lcl"]')).toHaveText('902 hPa');
    // format_version 2 stack -> the HRRR diagnostic row is present.
    await expect(page.locator('[data-testid="sounding-index-model-sbcape"]')).toHaveText('1466');

    // The caption is the server's string, verbatim.
    await expect(page.locator('[data-testid="sounding-parcel-definition"]')).toHaveText(
      SOUNDING_PARCEL_DEFINITION,
    );
  });

  test('null indices show em dashes and the capped frame drops LFC/EL + the HRRR row', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    const mapSlot = await bootViewer(page, requests);
    await page.locator('[data-testid="sounding-toggle"]').click();
    await mapSlot.click({ position: { x: 320, y: 220 } });
    await expect(page.locator('[data-testid="skewt-chart"]')).toBeVisible();

    // fh 1 has null indices AND a null parcel: everything degrades to an em
    // dash and the parcel simply is not drawn — no zeros, no NaN paths.
    await page.locator('[data-testid="sounding-scrubber"]').fill('1');
    await expect(page.locator('[data-testid="sounding-scrubber"]')).toHaveValue('1');
    for (const key of ['sbcape', 'sbcin', 'mlcape', 'mlcin', 'pwat', 'lcl']) {
      await expect(page.locator(`[data-testid="sounding-index-${key}"]`)).toHaveText('—');
    }
    await expect(page.locator('[data-testid="skewt-parcel-path"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="skewt-level-markers"]')).toHaveAttribute(
      'data-count',
      '0',
    );
    // The profile itself is untouched by a missing indices block.
    await expect(page.locator('[data-testid="skewt-trace-t"]')).toBeVisible();

    await page.locator('[data-testid="sounding-scrubber"]').fill('2');
    await expect(page.locator('[data-testid="sounding-scrubber"]')).toHaveValue('2');

    // Indices follow the scrub.
    await expect(page.locator('[data-testid="sounding-index-sbcape"]')).toHaveText('1474');
    await expect(page.locator('[data-testid="sounding-index-pwat"]')).toHaveText('40.4 mm');

    // Null LFC/EL: no tick, no invented marker.
    await expect(page.locator('[data-testid="skewt-level-lcl"]')).toBeVisible();
    await expect(page.locator('[data-testid="skewt-level-lfc"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="skewt-level-el"]')).toHaveCount(0);
    // v1-shaped surface block: the HRRR SBCAPE row is absent, not dashed.
    await expect(page.locator('[data-testid="sounding-index-model-sbcape"]')).toHaveCount(0);
    // The parcel is still drawn.
    await expect(page.locator('[data-testid="skewt-parcel-path"]')).toHaveCount(1);
  });

  test('the Phase 5 overlays render: wet-bulb, omega, DGZ, hodograph and theta-e', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    const mapSlot = await bootViewer(page, requests);
    await page.locator('[data-testid="sounding-toggle"]').click();
    await mapSlot.click({ position: { x: 320, y: 220 } });
    await expect(page.locator('[data-testid="skewt-chart"]')).toBeVisible();

    // --- wet-bulb: a real trace, and SHORTER than T because the server sends
    // nulls at the below-ground levels T is anchored through.
    const wetbulb = page.locator('[data-testid="skewt-trace-tw"]');
    await expect(wetbulb).toHaveCount(1);
    const twPath = (await wetbulb.getAttribute('d')) ?? '';
    const tPath = (await page.locator('[data-testid="skewt-trace-t"]').getAttribute('d')) ?? '';
    expect(twPath.length).toBeGreaterThan(0);
    expect(twPath).not.toContain('NaN');
    expect(pathYs(twPath).length).toBeLessThanOrEqual(pathYs(tPath).length);
    // Tw lies between Td and T, so its path is neither of them.
    expect(twPath).not.toBe(tPath);
    await expect(page.locator('[data-testid="skewt-legend"]')).toContainText('Wetbulb');

    // --- omega strip: bars in the left gutter, ascent tagged.
    const omega = page.locator('[data-testid="skewt-omega"]');
    await expect(omega).toHaveCount(1);
    expect(Number(await omega.getAttribute('data-bars'))).toBeGreaterThan(3);
    expect(await page.locator('[data-testid="skewt-omega-bar"][data-ascent="1"]').count())
      .toBeGreaterThan(0);

    // --- DGZ: this column crosses −18…−12, so exactly one band is shaded.
    const dgz = page.locator('[data-testid="skewt-dgz"]');
    expect(Number(await dgz.getAttribute('data-bands'))).toBe(1);
    await expect(dgz).toContainText('DGZ');

    // --- hodograph: height-coloured, and the 12 km deep fixture reaches every
    // band, so all five colours are drawn with distinct strokes.
    const hodograph = page.locator('[data-testid="sounding-hodograph"]');
    await expect(hodograph).toBeVisible();
    const segments = page.locator('[data-testid="hodograph-segment"]');
    expect(await segments.count()).toBeGreaterThanOrEqual(4);
    const strokes = await segments.evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute('stroke')),
    );
    expect(new Set(strokes).size).toBe(strokes.length);
    await expect(page.locator('[data-testid="hodograph-surface"]')).toHaveCount(1);
    await expect(page.locator('[data-testid="hodograph-tick-1"]')).toHaveCount(1);
    await expect(page.locator('[data-testid="hodograph-tick-9"]')).toHaveCount(1);

    // --- theta-e inset.
    await expect(page.locator('[data-testid="sounding-thetae"]')).toBeVisible();
    const thetaePath = (await page.locator('[data-testid="thetae-trace"]').getAttribute('d')) ?? '';
    expect(thetaePath.length).toBeGreaterThan(0);
    expect(thetaePath).not.toContain('NaN');

    // Stacked sections, in order: chart, then the inset row, then the indices.
    const order = await page.evaluate(() => {
      const ids = ['skewt-chart', 'sounding-insets', 'sounding-indices'];
      return ids.map((id) => {
        const node = document.querySelector(`[data-testid="${id}"]`);
        return node ? node.getBoundingClientRect().top : Number.NaN;
      });
    });
    expect(order[0]).toBeLessThan(order[1]);
    expect(order[1]).toBeLessThan(order[2]);
  });

  test('the DGZ is absent in a warm frame and the inset row is absent without profiles', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    const mapSlot = await bootViewer(page, requests);
    await page.locator('[data-testid="sounding-toggle"]').click();
    await mapSlot.click({ position: { x: 320, y: 220 } });
    await expect(page.locator('[data-testid="skewt-chart"]')).toBeVisible();

    // fh 1: no `profiles` at all (pre-Phase-5 cached response, or a frame whose
    // overlay computation failed). The chart still draws; the row is gone.
    await page.locator('[data-testid="sounding-scrubber"]').fill('1');
    await expect(page.locator('[data-testid="sounding-scrubber"]')).toHaveValue('1');
    await expect(page.locator('[data-testid="skewt-trace-t"]')).toBeVisible();
    await expect(page.locator('[data-testid="sounding-insets"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="skewt-trace-tw"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="sounding-hodograph"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="sounding-thetae"]')).toHaveCount(0);
    // The omega strip is fed by `w`, not by `profiles`: it survives.
    expect(Number(await page.locator('[data-testid="skewt-omega"]').getAttribute('data-bars')))
      .toBeGreaterThan(3);

    // fh 2: a column that never reaches −12 °C has no DGZ — nothing is drawn
    // rather than a zero-height band or a fabricated default.
    await page.locator('[data-testid="sounding-scrubber"]').fill('2');
    await expect(page.locator('[data-testid="sounding-scrubber"]')).toHaveValue('2');
    await expect(page.locator('[data-testid="sounding-insets"]')).toHaveCount(1);
    await expect(page.locator('[data-testid="skewt-dgz"]')).toHaveAttribute('data-bands', '0');
    await expect(page.locator('[data-testid="skewt-dgz"]')).not.toContainText('DGZ');
  });

  test('the picked point round-trips through `sounding=` and closing clears it', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    const mapSlot = await bootViewer(page, requests);
    await page.locator('[data-testid="sounding-toggle"]').click();
    await mapSlot.click({ position: { x: 320, y: 220 } });
    await expect(page.locator('[data-testid="sounding-panel"]')).toBeVisible();

    await expect.poll(() => page.url(), { timeout: 10_000 }).toContain('sounding=');
    const soundingParam = new URL(page.url()).searchParams.get('sounding') ?? '';
    const [lat, lon] = soundingParam.split(',').map(Number);
    expect(Number.isFinite(lat)).toBe(true);
    expect(Number.isFinite(lon)).toBe(true);
    expect(Math.abs(lat - requests[0].lat)).toBeLessThan(0.01);

    await page.locator('[data-testid="sounding-close"]').click();
    await expect(page.locator('[data-testid="sounding-panel"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="sounding-marker"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="sounding-toggle"]')).toHaveAttribute('aria-pressed', 'false');
    await expect.poll(() => page.url(), { timeout: 10_000 }).not.toContain('sounding=');
  });

  test('a deep link opens the panel with the pick mode disarmed', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    await stubSoundingRoutes(page, requests);
    await page.goto(`${VIEWER_URL}&sounding=35.477,-97.506`);

    // Absorb dev-server compile latency the same way bootViewer does — every
    // other test waits for the map canvas before asserting on the panel.
    await expect(page.locator('.maplibregl-canvas')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('[data-testid="sounding-panel"]')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-testid="skewt-chart"]')).toBeVisible();
    await expect(page.locator('[data-testid="sounding-toggle"]')).toHaveAttribute('aria-pressed', 'false');
    expect(requests[0].lat).toBeCloseTo(35.477, 3);
    expect(requests[0].lon).toBeCloseTo(-97.506, 3);
  });

  test('Escape leaves the pick mode but keeps the open panel', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    const mapSlot = await bootViewer(page, requests);
    await page.locator('[data-testid="sounding-toggle"]').click();
    await mapSlot.click({ position: { x: 320, y: 220 } });
    await expect(page.locator('[data-testid="sounding-panel"]')).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(page.locator('[data-testid="sounding-toggle"]')).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('[data-testid="sounding-panel"]')).toBeVisible();
  });

  test('a failed request shows the error state and Retry recovers', async ({ page }) => {
    const requests: SoundingRequestLog = [];
    await stubSoundingRoutes(page, requests, { failFirstSounding: true });
    await page.goto(VIEWER_URL);
    await expect(page.locator('.maplibregl-canvas')).toBeVisible();
    await page.locator('[data-testid="sounding-toggle"]').click();
    await page.locator('[data-testid="viewer-map-slot"]').click({ position: { x: 320, y: 220 } });

    await expect(page.locator('[data-testid="sounding-error"]')).toBeVisible();
    await page.locator('[data-testid="sounding-retry"]').click();
    await expect(page.locator('[data-testid="skewt-chart"]')).toBeVisible();
  });
});

test.describe('Sounding panel (mobile)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium-only contract suite.');
  // Same override the Phase 8 suite uses: the mobile geometry contract is
  // specified at 390×844, and `hasTouch` flips `(pointer: coarse)`.
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

  test('the overflow toggle arms the pick and the panel opens as a bottom sheet', async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Runs on chromium with a mobile viewport.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
    const requests: SoundingRequestLog = [];
    await stubSoundingRoutes(page, requests);
    await page.goto(VIEWER_URL);
    // Mobile Chrome has no swiftshader flag, so the WebGL canvas may never
    // appear — settle on the chrome the Phase 8 suite uses instead.
    await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId('viewer-mobile-bar')).toBeVisible({ timeout: 20_000 });

    await page.getByTestId('mobile-bar-overflow-trigger').click();
    await page.getByTestId('sounding-toggle').click();
    await page.locator('[data-testid="viewer-map-slot"]').click({ position: { x: 160, y: 200 } });

    const panel = page.getByTestId('sounding-panel');
    await expect(panel).toBeVisible();
    await expect(page.getByTestId('skewt-chart')).toBeVisible();

    // Full-width sheet anchored to the bottom of the viewport.
    const box = (await panel.boundingBox())!;
    const viewport = page.viewportSize()!;
    expect(box.x).toBeLessThanOrEqual(1);
    expect(box.width).toBeCloseTo(viewport.width, 0);
    expect(box.y + box.height).toBeCloseTo(viewport.height, 0);
    // §8: the sheet never eats the whole screen — map stays visible above it.
    expect(box.height).toBeLessThan(viewport.height * 0.9);
  });
});

test.describe('Sounding availability', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium-only contract suite.');

  test('the toggle is hidden entirely on models without soundings', async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop docked-panel contract.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
    const requests: SoundingRequestLog = [];
    await stubSoundingRoutes(page, requests);
    await page.goto(
      `/viewer?m=${NO_SOUNDING_MODEL}&r=latest&v=${SOUNDING_VARIABLE}&fh=0&reg=conus`,
    );

    // Hidden, not disabled (Brian, Phase 3 prod review). Wait for the viewer to
    // finish booting so absence is meaningful, then assert the toggle never rendered.
    await expect(page.locator('.maplibregl-canvas')).toBeVisible({ timeout: 30_000 });
    const toggle = page.locator('[data-testid="sounding-toggle"]');
    await expect(toggle).toHaveCount(0);
  });
});
