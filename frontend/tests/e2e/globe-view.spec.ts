/**
 * ════════════════════════════════════════════════════════════════════════════
 * GLOBE VIEW — USER-FACING LAYER (Phase G2)
 * ════════════════════════════════════════════════════════════════════════════
 *
 * G1 shipped the renderer behind `?globe=1`. This suite pins the layer on top
 * of it: the VIEW-section toggle, the `proj=globe` permalink, and the three
 * audit fixes that ride with it
 * (docs/GLOBE_OVERLAY_AUDIT_2026-08-01.md).
 *
 *   1. Toggle round-trip — the URL gains and drops `proj=globe` and the
 *      RENDERER actually switches, asserted on the draw counters rather than on
 *      a screenshot: `globeMeshDraws` climbing with `mercatorQuadDraws` frozen
 *      at 0, and the exact reverse on the way back.
 *   2. Hover disc gate (audit item 4, the only true product bug). Outside the
 *      disc, `unproject` clamps to the nearest horizon point, so the readout
 *      pinned to a REAL value floating in empty background at 1.4 R. The
 *      suppression is asserted on the SAMPLE REQUEST, not on the tooltip: a
 *      hidden tooltip that still fetched would leave the bug half-fixed.
 *   3. Polar framing (audit item 10c). Centre latitude was hard-clamped to the
 *      Web Mercator limit under globe, so neither pole could be framed.
 *   4. Compare stays flat (audit item 5) — a decision, not a limitation.
 *   5. Share capture on the globe is non-blank, and the SERVER-side screenshot
 *      is never told about the projection (audit item 8d).
 *   6. A canonical (EPSG:3857) regional artifact toggles onto the globe.
 *
 * Fixtures are the render-golden fixtures verbatim, so the data under every
 * assertion here is the same data the goldens pin. Chromium/SwiftShader only.
 */
import { expect, test, type Page } from '@playwright/test';

import {
  GOLDEN_VIEW,
  goldenCase,
  stubGoldenBaselineRoutes,
  type GoldenCaseId,
} from './render-golden-baseline.fixtures';

type GlobeRenderStats = {
  drawFrames: number;
  globeMeshDraws: number;
  mercatorQuadDraws: number;
  lastGeometryMode: 'mercator' | 'globe';
  lastMatrixSpace: 'mercator-unit' | 'unit-sphere';
};

const readGlobeStats = (page: Page): Promise<GlobeRenderStats> =>
  page.evaluate(
    () =>
      (window as unknown as { __cartoskyGlobeRender: { read: () => GlobeRenderStats } })
        .__cartoskyGlobeRender.read(),
  );

const resetGlobeStats = (page: Page) =>
  page.evaluate(() =>
    (window as unknown as { __cartoskyGlobeRender: { reset: () => void } })
      .__cartoskyGlobeRender.reset(),
  );

const readProjectionMismatch = (page: Page): Promise<number> =>
  page
    .evaluate(
      () =>
        (window as unknown as {
          __cartoskyGridCoherence?: { read: () => { projectionMismatch: number } };
        }).__cartoskyGridCoherence?.read().projectionMismatch ?? 0,
    )
    .catch(() => 0);

/**
 * Force repaints so the counters describe steady state rather than whatever the
 * readiness gate left behind. Drives the map through the G1 debug seam, which
 * G2 keeps published for exactly this.
 */
async function repaint(page: Page, frames = 4) {
  await page.evaluate(async (count) => {
    const map = (window as unknown as { __cartoskyGlobe?: { map?: { triggerRepaint: () => void } } })
      .__cartoskyGlobe?.map;
    for (let index = 0; index < count; index += 1) {
      map?.triggerRepaint();
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }
  }, frames);
}

/**
 * Open a golden case's data. `screenshot=1` is on for its READINESS LATCH only
 * (`data-viewer-ready`, which is the only signal that says the grid frame is
 * actually painted) — it does not hide the rail, so the control under test is
 * still there. `globe` defaults to off, the shipped default, and the G1 boot
 * flag is never used: every globe frame here is reached through the toggle or
 * through `proj=globe`.
 */
async function openViewer(page: Page, id: GoldenCaseId, search = ''): Promise<void> {
  const spec = goldenCase(id);
  // Cases without their own camera use the shared golden view.
  const view = spec.view ?? GOLDEN_VIEW;
  await stubGoldenBaselineRoutes(page, id);
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
}

/** The DisplayRow row button (aria-pressed carries the state). */
const globeToggle = (page: Page) =>
  page.getByTestId('rail-toggle-globe-view').locator('button').first();

test.describe('Globe view — user-facing layer (Phase G2)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium/SwiftShader contract suite.');
  test.describe.configure({ mode: 'serial' });

  test.use({
    // Wider than GOLDEN_VIEWPORT on purpose: at 1000 px the rail renders its
    // COLLAPSED tree and the VIEW rows are hidden, so the control under test
    // would not be reachable. The data is the golden fixture either way.
    viewport: { width: 1400, height: 900 },
    timezoneId: 'UTC',
    locale: 'en-US',
    colorScheme: 'light',
  });

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop control; the mobile sheet row is pinned separately.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  // ── 1. Toggle round-trip ──────────────────────────────────────────────────
  test('the VIEW toggle switches the renderer and round-trips through proj=globe', async ({ page }) => {
    test.setTimeout(180_000);
    await openViewer(page, 'globe-4326-world');

    // Default OFF, and the control is present because the runtime projection
    // setter was detected (it is gated on that, not on a hardcoded true).
    await expect(page.getByTestId('rail-toggle-globe-view')).toBeVisible();
    expect(await page.evaluate(() => window.location.search)).not.toContain('proj');

    await resetGlobeStats(page);
    await repaint(page);
    const flatBefore = await readGlobeStats(page);
    expect(flatBefore.lastGeometryMode, 'boots flat').toBe('mercator');
    expect(flatBefore.globeMeshDraws, 'no globe draws before the toggle').toBe(0);

    await globeToggle(page).click();

    await expect
      .poll(() => page.evaluate(() => window.location.search), { timeout: 15_000 })
      .toContain('proj=globe');

    await resetGlobeStats(page);
    await repaint(page);
    const onGlobe = await readGlobeStats(page);
    expect(onGlobe.lastGeometryMode, 'renderer switched to the sphere mesh').toBe('globe');
    expect(onGlobe.lastMatrixSpace).toBe('unit-sphere');
    expect(onGlobe.globeMeshDraws, 'sphere mesh is drawing').toBeGreaterThan(0);
    // The world-copy loop is a mercator-only device and must stay dead.
    expect(onGlobe.mercatorQuadDraws, 'world-copy loop must not run on globe').toBe(0);
    expect(await readProjectionMismatch(page), 'geometry/matrix tripwire').toBe(0);

    // ...and back. `proj` is absent-when-default, never `proj=mercator`.
    await globeToggle(page).click();
    await expect
      .poll(() => page.evaluate(() => window.location.search), { timeout: 15_000 })
      .not.toContain('proj');

    await resetGlobeStats(page);
    await repaint(page);
    const backFlat = await readGlobeStats(page);
    expect(backFlat.lastGeometryMode, 'renderer returned to the quad').toBe('mercator');
    expect(backFlat.mercatorQuadDraws).toBeGreaterThan(0);
    expect(backFlat.globeMeshDraws, 'sphere mesh must stop drawing').toBe(0);
    expect(await readProjectionMismatch(page), 'geometry/matrix tripwire').toBe(0);
  });

  test('a proj=globe deep link boots on the globe with the toggle reading on', async ({ page }) => {
    test.setTimeout(120_000);
    await openViewer(page, 'globe-4326-world', '&proj=globe');

    await resetGlobeStats(page);
    await repaint(page);
    expect((await readGlobeStats(page)).lastGeometryMode).toBe('globe');
    await expect(globeToggle(page)).toHaveAttribute('aria-pressed', 'true');
    // Sticky: the write-back must not strip a param it was deep-linked with.
    await expect
      .poll(() => page.evaluate(() => window.location.search), { timeout: 15_000 })
      .toContain('proj=globe');
  });

  // ── 2. Hover disc gate (audit item 4) ─────────────────────────────────────
  test('hover samples inside the disc and issues no request outside it', async ({ page }) => {
    test.setTimeout(180_000);
    const sampleRequests: string[] = [];
    page.on('request', (request) => {
      if (request.url().includes('/sample?')) {
        sampleRequests.push(request.url());
      }
    });

    await openViewer(page, 'globe-4326-world', '&proj=globe');
    await repaint(page);

    // Where the limb actually is, from MapLibre's own ray/planet intersection —
    // the same predicate isCursorOnGlobe() uses in the app.
    const geometry = await page.evaluate(() => {
      const map = (window as unknown as {
        __cartoskyGlobe: { map: { transform: Record<string, number> & {
          isPointOnMapSurface: (p: { x: number; y: number }) => boolean } } };
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
      return { cx, cy, limb: lo, width: transform.width, height: transform.height };
    });
    // The camera has to actually show a limb, or the test proves nothing.
    expect(geometry.limb, 'limb must be on screen at this camera').toBeLessThan(geometry.cx);

    const canvas = page.locator('canvas.maplibregl-canvas').first();
    const box = (await canvas.boundingBox())!;

    // (a) Well inside the disc: sampling fires.
    sampleRequests.length = 0;
    await page.mouse.move(box.x + geometry.cx, box.y + geometry.cy);
    await page.mouse.move(box.x + geometry.cx + 4, box.y + geometry.cy + 4);
    await expect
      .poll(() => sampleRequests.length, { timeout: 15_000, message: 'inside the disc must sample' })
      .toBeGreaterThan(0);

    // (b) At 1.4 R — the exact geometry the audit screenshotted an "83.2 °F"
    // chip at — nothing is fetched at all.
    sampleRequests.length = 0;
    const outsideX = geometry.cx + geometry.limb * 1.4;
    expect(outsideX, 'the 1.4 R probe has to be on the canvas').toBeLessThan(geometry.width - 2);
    await page.mouse.move(box.x + outsideX, box.y + geometry.cy);
    await page.mouse.move(box.x + outsideX + 2, box.y + geometry.cy + 2);
    await page.waitForTimeout(1500);
    expect(sampleRequests, 'no sample request outside the disc').toEqual([]);
  });

  // ── 3. Polar framing (audit item 10c) ─────────────────────────────────────
  for (const pole of [
    { name: 'north', lat: 90 },
    { name: 'south', lat: -90 },
  ]) {
    test(`the ${pole.name} pole can be framed under globe`, async ({ page }) => {
      test.setTimeout(120_000);
      await openViewer(page, 'globe-4326-world', '&proj=globe');
      await repaint(page);

      const framed = await page.evaluate((targetLat) => {
        const map = (window as unknown as {
          __cartoskyGlobe: { map: {
            jumpTo: (o: { center: [number, number] }) => void;
            getCenter: () => { lat: number };
            transform: Record<string, number> & {
              isPointOnMapSurface: (p: { x: number; y: number }) => boolean };
          } };
        }).__cartoskyGlobe.map;
        // No zoom supplied on purpose: MapLibre's own globe jumpTo carries the
        // latitude/zoom adjustment that holds the disc's screen size constant.
        map.jumpTo({ center: [0, targetLat] });
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
        return { lat: map.getCenter().lat, limb: lo, half: Math.min(cx, cy) };
      }, pole.lat);

      // Before G2 this landed on 85.051 — the Web Mercator limit — every time.
      expect(Math.abs(Math.abs(framed.lat) - 90), `${pole.name}: centred on the pole`)
        .toBeLessThanOrEqual(2);
      expect(Math.sign(framed.lat)).toBe(Math.sign(pole.lat));
      // ...and the disc is still on screen rather than magnified off it.
      expect(framed.limb, `${pole.name}: disc fits the viewport`).toBeLessThanOrEqual(framed.half);
      expect(await readProjectionMismatch(page)).toBe(0);
    });
  }

  test('leaving the globe from a polar camera returns the flat map to legal bounds', async ({ page }) => {
    test.setTimeout(120_000);
    await openViewer(page, 'globe-4326-world', '&proj=globe');
    await page.evaluate(() => {
      (window as unknown as { __cartoskyGlobe: { map: { jumpTo: (o: { center: [number, number] }) => void } } })
        .__cartoskyGlobe.map.jumpTo({ center: [0, 90] });
    });
    await globeToggle(page).click();
    await expect
      .poll(() => page.evaluate(() => window.location.search), { timeout: 15_000 })
      .not.toContain('proj');
    // The flat map keeps today's clamps exactly, so a polar centre the globe
    // allowed has to be pulled back inside them.
    const lat = await page.evaluate(() =>
      (window as unknown as { __cartoskyGlobe: { map: { getCenter: () => { lat: number } } } })
        .__cartoskyGlobe.map.getCenter().lat);
    expect(Math.abs(lat), 'flat centre back inside the mercator limit').toBeLessThanOrEqual(85.06);
  });

  // ── 3b. Permalink round-trip of the two negative-zoom cameras ────────────
  for (const camera of [
    // Fully zoomed out at lat 20: the constrain floor is log2(cos 20) =
    // -0.0897, so requesting anything below it lands exactly there.
    { name: 'whole-disc', lat: 20, lon: -40, zoom: -5 },
    // No zoom requested: MapLibre's globe jumpTo carries the latitude
    // adjustment itself, which puts a framed pole at about -6.84.
    { name: 'polar', lat: 89.5, lon: 10, zoom: null },
  ]) {
    test(`the ${camera.name} globe camera survives a permalink round-trip`, async ({ page }) => {
      test.setTimeout(180_000);
      await openViewer(page, 'globe-4326-world', '&proj=globe');
      await repaint(page);

      // Drive the camera the way a user would reach it, then read back what
      // the app WROTE to the address bar. Both of these zooms are negative
      // (whole disc at lat 20 = -0.09, framed pole at lat 89.5 = -6.84), which
      // the old `z >= 0` permalink guard silently dropped.
      const applied = await page.evaluate((target) => {
        const map = (window as unknown as { __cartoskyGlobe: { map: {
          jumpTo: (o: { center: [number, number]; zoom?: number }) => void;
          getCenter: () => { lat: number; lng: number };
          getZoom: () => number;
        } } }).__cartoskyGlobe.map;
        map.jumpTo(target.zoom === null
          ? { center: [target.lon, target.lat] }
          : { center: [target.lon, target.lat], zoom: target.zoom });
        return { lat: map.getCenter().lat, lng: map.getCenter().lng, zoom: map.getZoom() };
      }, camera);
      expect(applied.zoom, `${camera.name}: this camera really is negative-zoom`).toBeLessThan(0);

      await expect
        .poll(() => page.evaluate(() => window.location.search), { timeout: 20_000 })
        .toContain('z=-');
      const search = await page.evaluate(() => window.location.search);
      expect(search).toContain('proj=globe');

      // Reload the URL the app itself produced — the actual share path.
      await page.goto(`/viewer${search}${search.includes('screenshot=') ? '' : '&screenshot=1'}`);
      await expect
        .poll(() => page.evaluate(() => document.documentElement.getAttribute('data-viewer-ready')), {
          timeout: 60_000,
        })
        .toBe('1');
      const restored = await page.evaluate(() => {
        const map = (window as unknown as { __cartoskyGlobe: { map?: {
          getCenter: () => { lat: number; lng: number }; getZoom: () => number } } }).__cartoskyGlobe.map;
        return map ? { lat: map.getCenter().lat, lng: map.getCenter().lng, zoom: map.getZoom() } : null;
      });
      expect(restored, `${camera.name}: map handle after reload`).not.toBeNull();
      // Serialized at 5 dp (lat/lon) and 2 dp (z).
      expect(restored!.lat, `${camera.name}: latitude`).toBeCloseTo(applied.lat, 3);
      expect(restored!.lng, `${camera.name}: longitude`).toBeCloseTo(applied.lng, 3);
      expect(restored!.zoom, `${camera.name}: zoom`).toBeCloseTo(applied.zoom, 1);
    });
  }

  // ── 3c. City labels: no far-side labels, in EITHER label mode ────────────
  test('city labels never render on the far side of the globe, in both modes', async ({ page }) => {
    test.setTimeout(180_000);
    await openViewer(page, 'globe-4326-world', '&proj=globe');
    // Settle before evaluating: the viewer's own post-hydration permalink
    // flush lands shortly after the readiness latch, and evaluating across it
    // loses the execution context.
    await repaint(page);
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(1000);

    /**
     * Every label the two visible city layers would draw, tested against
     * MapLibre's own horizon plane. Reads the layer FILTERS as the app left
     * them, so it catches the name-only path (which draws straight off the
     * static source and would otherwise show the whole planet's cities).
     */
    const occludedAt = (page_: Page, zoom: number) =>
      page_.evaluate(async (z) => {
        const map = (window as unknown as { __cartoskyGlobe: { map: {
          jumpTo: (o: { zoom: number }) => void;
          queryRenderedFeatures: (o: { layers: string[] }) => Array<{ geometry: { coordinates: number[] } }>;
          getLayer: (id: string) => unknown;
          transform: { isLocationOccluded: (l: { lng: number; lat: number }) => boolean };
        } } }).__cartoskyGlobe.map;
        map.jumpTo({ zoom: z });
        await new Promise((resolve) => setTimeout(resolve, 900));
        const layers = ['city-label-candidates', 'city-value-labels', 'city-value-label-names']
          .filter((id) => map.getLayer(id));
        const features = layers.length ? map.queryRenderedFeatures({ layers }) : [];
        let occluded = 0;
        for (const feature of features) {
          const [lng, lat] = feature.geometry.coordinates;
          if (map.transform.isLocationOccluded({ lng, lat })) occluded += 1;
        }
        return { rendered: features.length, occluded };
      }, zoom);

    // The audit's requested regression: zero occluded labels at z2-z3, where
    // the limb is on screen and a far side exists at all.
    for (const zoom of [2, 2.5, 3]) {
      const report = await occludedAt(page, zoom);
      expect(report.occluded, `z${zoom}: occluded labels`).toBe(0);
    }

    // ...and again in name-only mode, which is the path that was unguarded:
    // `city-label-candidates` draws off the static source under a zoom/rank
    // expression, so without the cull every rank-passing city on the planet
    // renders, back side included.
    await page.evaluate(async () => {
      const mod = await import('/src/lib/city-labels.ts');
      const map = (window as unknown as { __cartoskyGlobe: { map: unknown } }).__cartoskyGlobe.map;
      (mod as { setCityLabelNameOnlyMode: (m: unknown, on: boolean) => void })
        .setCityLabelNameOnlyMode(map, true);
    });
    for (const zoom of [2, 2.5, 3]) {
      const report = await occludedAt(page, zoom);
      expect(report.occluded, `z${zoom} name-only: occluded labels`).toBe(0);
    }

    // Rotation brings the other hemisphere's labels back — the cull is a
    // per-frame visibility test, not a one-shot prune of the source.
    const afterRotation = await page.evaluate(async () => {
      const map = (window as unknown as { __cartoskyGlobe: { map: {
        jumpTo: (o: { center: [number, number]; zoom: number }) => void;
        queryRenderedFeatures: (o: { layers: string[] }) => unknown[];
        getLayer: (id: string) => unknown;
      } } }).__cartoskyGlobe.map;
      const layers = ['city-label-candidates', 'city-value-labels', 'city-value-label-names']
        .filter((id) => map.getLayer(id));
      map.jumpTo({ center: [-98, 39], zoom: 4 });
      await new Promise((resolve) => setTimeout(resolve, 1200));
      const mod = await import('/src/lib/city-labels.ts');
      (mod as { setCityLabelNameOnlyMode: (m: unknown, on: boolean) => void })
        .setCityLabelNameOnlyMode(map, true);
      await new Promise((resolve) => setTimeout(resolve, 900));
      return map.queryRenderedFeatures({ layers }).length;
    });
    expect(afterRotation, 'labels return once their hemisphere faces the camera')
      .toBeGreaterThan(0);
  });

  // ── 3d. Sounding pick is disc-gated (same class as the hover bug) ────────
  test('an armed sounding pick outside the disc opens nothing', async ({ page }) => {
    test.setTimeout(180_000);
    // HRRR is the only model that publishes soundings (`modelSupportsSounding`
    // is a static allowlist), and `hrrr-radar-ptype` is the only golden case on
    // it — the globe cases are all GFS. So this one opens a MERCATOR case and
    // reaches the globe through the control, which is the fuller path anyway.
    await openViewer(page, 'hrrr-radar-ptype');
    await globeToggle(page).click();
    await expect
      .poll(() => page.evaluate(() => window.location.search), { timeout: 15_000 })
      .toContain('proj=globe');
    await repaint(page);

    const toggle = page.getByTestId('sounding-toggle');
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');

    const geometry = await page.evaluate(() => {
      const map = (window as unknown as { __cartoskyGlobe: { map: {
        jumpTo: (o: { center: [number, number]; zoom: number }) => void;
        transform: Record<string, number> & {
          isPointOnMapSurface: (p: { x: number; y: number }) => boolean };
      } } }).__cartoskyGlobe.map;
      // Pull back until the limb is on screen; at the case's own camera the
      // disc exceeds the canvas and there is no outside to click.
      map.jumpTo({ center: [-98.58, 39.83], zoom: 1 });
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
      return { cx, cy, limb: lo, width: transform.width };
    });
    expect(geometry.limb, 'limb must be on screen').toBeLessThan(geometry.cx);

    const canvas = page.locator('canvas.maplibregl-canvas').first();
    const box = (await canvas.boundingBox())!;
    const outsideX = geometry.cx + geometry.limb * 1.3;
    expect(outsideX).toBeLessThan(geometry.width - 2);

    // Outside the disc: e.lngLat is MapLibre's clamped horizon point, so an
    // ungated pick would silently drop a Skew-T on the limb.
    await page.mouse.click(box.x + outsideX, box.y + geometry.cy);
    await page.waitForTimeout(1500);
    expect(await page.evaluate(() => window.location.search), 'no sounding param')
      .not.toContain('sounding=');
    // Still armed, because nothing consumed the click.
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');

    // Inside the disc the same click does pick a point.
    await page.mouse.click(box.x + geometry.cx, box.y + geometry.cy);
    await expect
      .poll(() => page.evaluate(() => window.location.search), { timeout: 20_000 })
      .toContain('sounding=');
  });

  // ── 5. Share ──────────────────────────────────────────────────────────────
  test('a live capture on the globe is non-blank', async ({ page }) => {
    test.setTimeout(120_000);
    await openViewer(page, 'globe-4326-world', '&proj=globe');
    await repaint(page);

    const report = await page.evaluate(async () => {
      const capture = (window as unknown as { __cartoskyViewerCapture?: () => Promise<string | null> })
        .__cartoskyViewerCapture;
      if (!capture) return null;
      const dataUrl = await capture();
      if (!dataUrl) return null;
      const bitmap = await createImageBitmap(await (await fetch(dataUrl)).blob());
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      canvas.getContext('2d')!.drawImage(bitmap, 0, 0);
      const { data } = canvas.getContext('2d')!.getImageData(0, 0, bitmap.width, bitmap.height);
      const colors = new Set<number>();
      for (let i = 0; i < data.length; i += 4) {
        colors.add((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
      }
      return { width: bitmap.width, height: bitmap.height, distinctColors: colors.size };
    });

    expect(report, 'capture hook must be published').not.toBeNull();
    expect(report!.width).toBeGreaterThan(0);
    // The G1 audit measured 1232 distinct colours on a globe capture; anything
    // in the tens rules out the blank/one-colour failure this hook exists for.
    expect(report!.distinctColors, 'capture is not a flat fill').toBeGreaterThan(64);
  });

  test('a globe GIF frame is fully opaque, with the limb flattened onto the real backdrop', async ({ page }) => {
    test.setTimeout(180_000);
    await openViewer(page, 'globe-4326-world', '&proj=globe');
    await repaint(page);

    /**
     * Composes one frame exactly the way the GIF driver does, with and without
     * the backdrop, and reports the alpha/colour facts that decide whether the
     * encoded GIF shows a ring.
     *
     * WHY THIS IS THE ASSERTION. gifenc quantizes at its default
     * `format: "rgb565"`, which reads only R/G/B — the alpha channel is
     * discarded outright. So any pixel that is not fully opaque before
     * read-back contributes whatever RGB it happens to carry, and on a scaled
     * alpha edge that RGB is the browser's premultiplied value divided back out
     * by a small alpha, which overshoots toward white.
     */
    const report = await page.evaluate(async () => {
      const se = await import('/src/lib/screenshot_export.ts') as {
        composeShareFrame: (c: HTMLCanvasElement, i: CanvasImageSource, o: Record<string, unknown>) => Promise<void>;
        shareGlobeDiscForImage: (w: number, h: number) => unknown;
        resolveShareBackdropColor: (n: Element | null) => string | null;
      };
      const capture = (window as unknown as { __cartoskyViewerCapture: () => Promise<string> })
        .__cartoskyViewerCapture;
      const bitmap = await createImageBitmap(await (await fetch(await capture())).blob());
      const backdrop = se.resolveShareBackdropColor(document.querySelector('canvas.maplibregl-canvas'));
      const width = 720;
      const height = Math.round((720 * bitmap.height) / bitmap.width);

      const compose = async (backdropColor: string | null) => {
        const canvas = document.createElement('canvas');
        await se.composeShareFrame(canvas, bitmap, {
          width, height, pixelRatio: 1, legend: null, overlayLines: [],
          isMobile: false, chromeScale: 0.5, chromeShadows: false,
          globeDisc: se.shareGlobeDiscForImage(bitmap.width, bitmap.height),
          backdropColor,
        });
        const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
        const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
        let transparent = 0;
        let partial = 0;
        let strayEdge = 0;
        for (let i = 0; i < data.length; i += 4) {
          const alpha = data[i + 3];
          if (alpha === 0) transparent += 1;
          else if (alpha < 255) {
            partial += 1;
            // RGB far from BOTH the map's palette range and the backdrop: the
            // un-premultiplication overshoot that becomes the visible ring.
            if (data[i] > 200 && data[i + 1] > 200 && data[i + 2] > 200) strayEdge += 1;
          }
        }
        return {
          transparent, partial, strayEdge,
          corner: [data[0], data[1], data[2], data[3]],
        };
      };

      return { backdrop, withBackdrop: await compose(backdrop), without: await compose(null) };
    });

    // The backdrop is READ from the DOM behind the transparent canvas, never
    // hardcoded — the map canvas's own pixels are transparent out there and say
    // nothing about what shows through.
    expect(report.backdrop, 'backdrop resolved from the DOM').toMatch(/^rgb\(\d+, \d+, \d+\)$/);
    const backdropRgb = report.backdrop!.match(/\d+/g)!.map(Number);

    // THE FIX: nothing left for the encoder to guess at.
    expect(report.withBackdrop.transparent, 'no transparent pixels survive to the encoder').toBe(0);
    expect(report.withBackdrop.partial, 'no partial-alpha pixels survive to the encoder').toBe(0);
    // Outside the disc is the real backdrop, derived — not a literal.
    expect(report.withBackdrop.corner.slice(0, 3)).toEqual(backdropRgb);
    expect(report.withBackdrop.corner[3], 'fully opaque').toBe(255);

    // MUTATION PIN: without the backdrop the same frame carries exactly the
    // defect — transparent pixels plus an alpha edge whose RGB overshoots. If
    // this ever stops being true the test above has gone vacuous.
    expect(report.without.transparent, 'control: the defect is real').toBeGreaterThan(0);
    expect(report.without.partial, 'control: the limb is an alpha edge').toBeGreaterThan(0);
    expect(report.without.strayEdge, 'control: the white ring pixels').toBeGreaterThan(0);
  });

  test('the server-side screenshot render URL is never told about the projection', async ({ page }) => {
    test.setTimeout(120_000);
    await openViewer(page, 'globe-4326-world', '&proj=globe');
    // Audit item 8d, an explicit v1 decision: the server render stays mercator,
    // so a shared Link/OG image does not match what the sharer saw. Pinned here
    // so wiring a projection param through has to change a test.
    const result = await page.evaluate(async () => {
      const mod = await import('/src/components/share/share-utils.ts');
      const build = (mod as {
        screenshotUrlForState: (permalink: string, state: Record<string, unknown>) => string;
      }).screenshotUrlForState;
      // Fed a permalink that DOES carry the param, so the assertion is about
      // what the render URL declares, not about an input that happened to omit
      // it. The share modal builds this from a permalink whose projection is
      // the sharer's — the render still has to come back mercator.
      // A real polar globe camera: proj set, zoom negative, latitude past the
      // Web Mercator limit. None of the three may reach the render URL as-is.
      const url = build('?m=gfs&v=tmp2m&proj=globe', {
        center: [10, 89.9], zoom: -6.8404, fh: 0,
      });
      const params = new URL(url).searchParams;
      return {
        url,
        keys: [...params.keys()].sort(),
        z: params.get('z'),
        lat: params.get('lat'),
      };
    });
    // The one param that could reach the screenshot service is `proj`, and it
    // only got there because the test put it there — the builder must not add
    // one of its own, so the added key set is exactly the camera/frame set.
    const permalinkKeys = ['m', 'v'];
    expect(result.keys.filter((key) => !permalinkKeys.includes(key)).sort())
      .toEqual(['fh', 'lat', 'lon', 'z']);
    // `proj` was in the INPUT permalink and must not survive: the server render
    // is flat by design, and App.tsx feeds this function the viewer's own share
    // permalink, which carries proj=globe whenever the sharer is on the globe.
    expect(result.keys, 'projection stripped from the render URL').not.toContain('proj');
    // ...and the globe's latitude-adjusted negative zoom is clamped into a
    // camera the flat renderer can actually hold.
    expect(Number(result.z), 'flat-legal zoom').toBeGreaterThanOrEqual(0);
    expect(Number(result.lat), 'flat-legal latitude').toBeLessThanOrEqual(85.06);
  });

  // ── 6. Canonical (3857) model ─────────────────────────────────────────────
  test('a canonical EPSG:3857 regional artifact toggles onto the globe', async ({ page }) => {
    test.setTimeout(180_000);
    await openViewer(page, 'globe-3857-regional');
    await resetGlobeStats(page);
    await repaint(page);
    expect((await readGlobeStats(page)).lastGeometryMode).toBe('mercator');

    await globeToggle(page).click();
    await expect
      .poll(() => page.evaluate(() => window.location.search), { timeout: 15_000 })
      .toContain('proj=globe');

    await resetGlobeStats(page);
    await repaint(page);
    const stats = await readGlobeStats(page);
    // Not gated per model in v1: the globe pass costs ~3x the frame time on a
    // canonical artifact (measured in G1), but it renders correctly, and a
    // per-model gate would make the control flicker as the user browses.
    expect(stats.lastGeometryMode).toBe('globe');
    expect(stats.globeMeshDraws).toBeGreaterThan(0);
    expect(stats.mercatorQuadDraws).toBe(0);
    expect(await readProjectionMismatch(page)).toBe(0);
  });
});

// ── 4. Compare stays flat (audit item 5) ────────────────────────────────────
test.describe('Compare ignores proj (Phase G2)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium/SwiftShader contract suite.');

  test('compare never enters globe, even from a proj=globe deep link', async ({ page }) => {
    test.setTimeout(120_000);
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop compare.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));

    const spec = goldenCase('globe-4326-world');
    await stubGoldenBaselineRoutes(page, 'globe-4326-world');
    // Both the `proj` param AND the G1 boot flag, so the gate is pinned against
    // the two ways the globe can be requested. A decision, not a limitation:
    // two ~700 px panes clip the disc at the divider into two half-globes.
    await page.goto(
      `/compare?lm=${spec.model}&lv=${spec.variable}&lr=latest` +
        `&rm=${spec.model}&rv=${spec.variable}&rr=latest&fh=0&proj=globe&globe=1`,
    );
    await page.waitForTimeout(4000);
    expect(
      await page.evaluate(() =>
        (window as unknown as { __cartoskyGlobe?: { isEnabled: () => boolean } })
          .__cartoskyGlobe?.isEnabled() ?? false),
      'compare must drop the renderer switch',
    ).toBe(false);
  });
});
