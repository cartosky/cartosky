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
 *      render URL now CARRIES the projection (reversing audit item 8d) — plus
 *      the headless render path itself, driven at three globe cameras and one
 *      flat control, asserted on the pixels it comes back with.
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

  test('the server-side screenshot render URL carries the globe projection and its camera', async ({ page }) => {
    test.setTimeout(120_000);
    await openViewer(page, 'globe-4326-world', '&proj=globe');
    // REVERSAL of audit item 8d. The v1 decision was that the server render
    // stays mercator, which meant a signed-in sharer on the globe got a flat
    // image back. It now renders the globe, so the render URL has to declare
    // both the projection AND a camera the globe can hold — a globe zoom is
    // latitude-adjusted and legitimately negative, and clamping it to the flat
    // floor of 0 would frame a different picture than the sharer's.
    const result = await page.evaluate(async () => {
      const mod = await import('/src/components/share/share-utils.ts');
      const build = (mod as {
        screenshotUrlForState: (permalink: string, state: Record<string, unknown>) => string;
      }).screenshotUrlForState;
      // A real polar globe camera: proj set, zoom negative, latitude past the
      // Web Mercator limit. All three must reach the render URL intact.
      const globe = new URL(build('?m=gfs&v=tmp2m&proj=globe', {
        center: [10, 89.9], zoom: -6.8404, fh: 0,
      }));
      // Same camera with no projection — the flat contract, unchanged.
      const flat = new URL(build('?m=gfs&v=tmp2m', {
        center: [10, 89.9], zoom: -6.8404, fh: 0,
      }));
      return {
        globe: {
          keys: [...globe.searchParams.keys()].sort(),
          proj: globe.searchParams.get('proj'),
          z: globe.searchParams.get('z'),
          lat: globe.searchParams.get('lat'),
        },
        flat: {
          proj: flat.searchParams.get('proj'),
          z: flat.searchParams.get('z'),
          lat: flat.searchParams.get('lat'),
        },
      };
    });
    // Beyond `proj` the builder still adds exactly the camera/frame set — the
    // reversal widened one param's treatment, it did not open the URL up.
    const permalinkKeys = ['m', 'v', 'proj'];
    expect(result.globe.keys.filter((key) => !permalinkKeys.includes(key)).sort())
      .toEqual(['fh', 'lat', 'lon', 'z']);
    expect(result.globe.proj, 'projection forwarded to the render URL').toBe('globe');
    expect(Number(result.globe.z), 'the globe camera keeps its negative zoom').toBeCloseTo(-6.84, 2);
    expect(Number(result.globe.lat), 'the globe camera keeps its polar centre').toBeCloseTo(89.9, 5);
    // MUTATION PIN: the flat contract is untouched, so the pass-through above
    // cannot be an unconditional "stop clamping".
    expect(result.flat.proj, 'a flat render URL still has no projection').toBeNull();
    expect(Number(result.flat.z), 'flat-legal zoom').toBeGreaterThanOrEqual(0);
    expect(Number(result.flat.lat), 'flat-legal latitude').toBeLessThanOrEqual(85.06);
  });

  // ── 5b. The headless (server) render path, at real globe cameras ──────────
  /**
   * What the screenshot service actually does, minus the service: build the
   * render URL with `screenshotUrlForState`, load it with the params
   * screenshot_service.py appends (`screenshot=1&legend=1&basemap=`), wait for
   * `data-viewer-ready`, and read `window.__cartoskyViewerCapture`.
   *
   * The two known hazards this is aimed at are both invisible in a URL: the
   * projection-sync effect needs the style loaded (it retries on
   * subscription/load/styledata/idle), and the capture can fire before the
   * globe mesh/program latch is up. Either one comes back as a flat or blank
   * frame, so the assertions are on PIXELS.
   */
  async function headlessRender(
    page: Page,
    id: GoldenCaseId,
    camera: { lat: number; lon: number; zoom: number },
    projection: 'globe' | 'flat',
  ) {
    const spec = goldenCase(id);
    await stubGoldenBaselineRoutes(page, id);
    // A cheap page that can import the app's modules, so the URL under test is
    // the one the share modal would send rather than one this spec hand-rolls.
    await page.goto('/src/components/share/share-utils.ts');
    const renderUrl = await page.evaluate(
      async ({ permalink, state }) => {
        const mod = await import('/src/components/share/share-utils.ts');
        return (mod as {
          screenshotUrlForState: (p: string, s: Record<string, unknown>) => string;
        }).screenshotUrlForState(permalink, state);
      },
      {
        permalink:
          `/viewer?m=${spec.model}&r=latest&v=${spec.variable}&fh=0&reg=conus` +
          (projection === 'globe' ? '&proj=globe' : ''),
        state: { center: [camera.lon, camera.lat], zoom: camera.zoom, fh: 0 },
      },
    );
    // Exactly the params screenshot_service.py appends before navigating.
    const url = new URL(renderUrl);
    url.searchParams.set('screenshot', '1');
    url.searchParams.set('legend', '1');
    url.searchParams.set('basemap', 'light');
    await page.goto(`${url.pathname}${url.search}`);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.getAttribute('data-viewer-ready')), {
        timeout: 60_000,
        message: `${id} ${projection}: headless viewer ready`,
      })
      .toBe('1');

    return { renderUrl, search: url.search };
  }

  /**
   * Read the capture the service would read, and describe it geometrically.
   *
   * `shareGlobeDiscForImage` is the app's own disc, in the capture's pixel
   * space — legitimate here because this IS the page whose canvas was captured
   * (the client-side share path cannot say the same about a SERVER frame, which
   * is why that path composes full-frame; see ScreenshotExportOptions).
   */
  async function describeCapture(page: Page) {
    return page.evaluate(async () => {
      const capture = (window as unknown as { __cartoskyViewerCapture?: () => Promise<string | null> })
        .__cartoskyViewerCapture;
      if (!capture) return null;
      const dataUrl = await capture();
      if (!dataUrl) return null;
      const bitmap = await createImageBitmap(await (await fetch(dataUrl)).blob());
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
      ctx.drawImage(bitmap, 0, 0);
      const { data } = ctx.getImageData(0, 0, bitmap.width, bitmap.height);
      const at = (x: number, y: number) => {
        const cx = Math.max(0, Math.min(bitmap.width - 1, Math.round(x)));
        const cy = Math.max(0, Math.min(bitmap.height - 1, Math.round(y)));
        const i = (cy * bitmap.width + cx) * 4;
        return [data[i], data[i + 1], data[i + 2], data[i + 3]];
      };
      const colors = new Set<number>();
      let opaque = 0;
      for (let i = 0; i < data.length; i += 4) {
        colors.add((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
        if (data[i + 3] === 255) opaque += 1;
      }
      const se = await import('/src/lib/screenshot_export.ts') as {
        shareGlobeDiscForImage: (w: number, h: number) => { centerX: number; centerY: number; radiusPx: number } | null;
      };
      const disc = se.shareGlobeDiscForImage(bitmap.width, bitmap.height);
      return {
        width: bitmap.width,
        height: bitmap.height,
        distinctColors: colors.size,
        opaqueFraction: opaque / (data.length / 4),
        disc,
        corners: [
          at(0, 0),
          at(bitmap.width - 1, 0),
          at(0, bitmap.height - 1),
          at(bitmap.width - 1, bitmap.height - 1),
        ],
        center: at(bitmap.width / 2, bitmap.height / 2),
        // Just inside and just outside the limb, on the horizontal through the
        // disc centre. Only meaningful when `disc` is non-null.
        insideLimb: disc ? at(disc.centerX + disc.radiusPx * 0.8, disc.centerY) : null,
        outsideLimb: disc ? at(disc.centerX + disc.radiusPx * 1.25, disc.centerY) : null,
      };
    });
  }

  /**
   * Run the capture through the composition the SERVER path uses — the same
   * `exportViewerScreenshotPng` the share modal calls on the bytes the service
   * returns, with `globeDiscSource: "captured-alpha"`.
   *
   * This page is standing in for the headless render only; the composition step
   * runs on the SHARER's machine, which is exactly why the disc has to come from
   * the frame rather than from a map transform.
   */
  async function composeAsServerPath(page: Page) {
    return page.evaluate(async () => {
      const se = await import('/src/lib/screenshot_export.ts') as {
        exportViewerScreenshotPng: (s: Record<string, unknown>, o: Record<string, unknown>) => Promise<Blob>;
        measureOpaqueDisc: (i: CanvasImageSource) => { centerX: number; centerY: number; radiusPx: number } | null;
        resolveShareBackdropColor: (n: Element | null) => string | null;
      };
      const capture = (window as unknown as { __cartoskyViewerCapture: () => Promise<string> })
        .__cartoskyViewerCapture;
      const dataUrl = await capture();
      const source = await createImageBitmap(await (await fetch(dataUrl)).blob());
      const discFromAlpha = se.measureOpaqueDisc(source);

      const blob = await se.exportViewerScreenshotPng(
        {
          center: [-40, 20],
          zoom: 1,
          isMobile: false,
          model: 'gfs',
          run: 'latest',
          variable: { key: 'tmp2m', label: '2 m Temp' },
          fh: 0,
          animationEnabled: false,
          capturedMapDataUrl: dataUrl,
          viewportWidth: source.width,
          viewportHeight: source.height,
        },
        { pixelRatio: 1, overlayLines: [], globeDiscSource: 'captured-alpha' },
      );
      const bitmap = await createImageBitmap(blob);
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
      ctx.drawImage(bitmap, 0, 0);
      const { data } = ctx.getImageData(0, 0, bitmap.width, bitmap.height);
      let opaque = 0;
      let transparent = 0;
      let partial = 0;
      for (let i = 3; i < data.length; i += 4) {
        if (data[i] === 255) opaque += 1;
        else if (data[i] === 0) transparent += 1;
        else partial += 1;
      }
      const at = (x: number, y: number) => {
        const offset = (Math.floor(y) * bitmap.width + Math.floor(x)) * 4;
        return [data[offset], data[offset + 1], data[offset + 2], data[offset + 3]];
      };
      return {
        discFromAlpha,
        width: bitmap.width,
        height: bitmap.height,
        opaqueFraction: opaque / (data.length / 4),
        transparent,
        partial,
        backdrop: se.resolveShareBackdropColor(document.querySelector('canvas.maplibregl-canvas')),
        // Mid-height on the left and right edges: the pillarbox the centred
        // square crop leaves, and the one place in the frame no chrome can
        // reach (the logo card sits inset from the top right and casts an
        // 18 px shadow, so literal corners are not a clean probe).
        pillarbox: [at(0, bitmap.height / 2), at(bitmap.width - 1, bitmap.height / 2)],
      };
    });
  }

  const HEADLESS_GLOBE_CAMERAS: Array<{
    label: string;
    id: GoldenCaseId;
    camera: { lat: number; lon: number; zoom: number };
  }> = [
    // The framed camera the goldens use — the ordinary case.
    { label: 'framed', id: 'globe-4326-world', camera: { lat: 20, lon: -40, zoom: 1 } },
    // Whole disc. Measured negative zoom, the value the flat clamp used to
    // destroy: at z 0 this camera frames a fraction of the planet instead.
    { label: 'whole-disc', id: 'globe-4326-world', camera: { lat: 20, lon: -40, zoom: -0.09 } },
    // Framed pole. Both a negative zoom AND a centre latitude past the Web
    // Mercator limit, which only the globe camera constrain permits.
    { label: 'polar', id: 'globe-4326-pole', camera: { lat: 89.9, lon: 0, zoom: -6.84 } },
  ];

  for (const { label, id, camera } of HEADLESS_GLOBE_CAMERAS) {
    test(`a headless render at the ${label} globe camera comes back as a disc`, async ({ page }) => {
      test.setTimeout(180_000);
      await headlessRender(page, id, camera, 'globe');
      await repaint(page);

      // The renderer really is on the sphere, not merely asked to be — the
      // projection-sync effect needs the style loaded and retries, so a URL
      // that says `proj=globe` proves nothing on its own.
      const stats = await readGlobeStats(page);
      expect(stats.lastGeometryMode, 'headless render is on the sphere').toBe('globe');
      expect(stats.mercatorQuadDraws, 'world-copy loop must stay dead').toBe(0);
      expect(await readProjectionMismatch(page), 'geometry/matrix tripwire').toBe(0);

      const shot = await describeCapture(page);
      expect(shot, 'capture hook must be published').not.toBeNull();
      expect(shot!.disc, 'the disc is derivable from the headless camera').not.toBeNull();

      // NON-BLANK. The blank-capture failure this hook exists for reads as one
      // colour; the G1 audit measured ~1200 on a real globe frame.
      expect(shot!.distinctColors, 'capture is not a flat fill').toBeGreaterThan(64);

      // DISC-SHAPED. Corners are background (the canvas is transparent outside
      // the limb, so alpha is the honest discriminator and survives whatever
      // colour the backdrop behind it happens to be), the centre is data, and
      // the transition happens AT the limb rather than anywhere convenient.
      for (const [index, corner] of shot!.corners.entries()) {
        expect(corner[3], `corner ${index} is outside the planet`).toBe(0);
      }
      expect(shot!.outsideLimb![3], 'just past the limb is outside the planet').toBe(0);
      expect(shot!.center[3], 'the disc centre is painted').toBe(255);
      expect(shot!.insideLimb![3], 'inside the limb is painted').toBe(255);
      // A globe frame is mostly void at these cameras; a full-frame paint here
      // would mean the projection never applied. The floor is low on purpose:
      // the disc's screen radius is set by zoom and is nearly canvas-
      // independent, so the whole-disc zoom FLOOR (-0.09 at lat 20) puts a
      // ~78 px planet in a 1400x900 frame — 2%. That measurement is the reason
      // the server capture is cropped rather than composed full-frame.
      expect(shot!.opaqueFraction, 'the planet does not fill the frame').toBeLessThan(0.9);
      expect(shot!.opaqueFraction, 'the planet is actually there').toBeGreaterThan(0.01);

      // COMPOSITION, on the frame the server would actually return. The client
      // cannot ask a transform about a frame it did not render, so the disc is
      // measured from the frame's own alpha; the crop geometry is the same
      // `globeShareCropRect` the live-canvas path has used since item 8c.
      const composed = await composeAsServerPath(page);
      expect(composed.discFromAlpha, 'the disc is measurable from the frame').not.toBeNull();
      // Measured disc vs the app's own disc for this very canvas — the two must
      // agree, which is what makes the alpha measurement trustworthy.
      expect(composed.discFromAlpha!.radiusPx).toBeGreaterThan(shot!.disc!.radiusPx * 0.9);
      expect(composed.discFromAlpha!.radiusPx).toBeLessThan(shot!.disc!.radiusPx * 1.15);
      // ...and the crop does its job: the planet goes from a marble to the
      // subject of the frame.
      expect(composed.opaqueFraction, 'the crop frames the planet').toBeGreaterThan(0.3);
      expect(composed.opaqueFraction).toBeGreaterThan(shot!.opaqueFraction);

      // ...and the composed STILL is opaque end to end (v1.1). Before the
      // backdrop fill reached this path the composed globe PNG was 44.3%
      // alpha-0 — a planet in a transparent square, which a light-background
      // host renders as a planet on white. Both the pillarbox the square crop
      // leaves and the void around the limb are now space.
      expect(composed.transparent, 'no transparent pixels in a composed globe still').toBe(0);
      expect(composed.partial, 'and no alpha edge for a viewer to composite').toBe(0);
      expect(composed.opaqueFraction, 'i.e. every pixel is opaque').toBe(1);
      // The colour is DERIVED from the DOM, not hardcoded, exactly as the GIF
      // path derives it — so the still and the GIF of one view cannot disagree.
      expect(composed.backdrop, 'backdrop resolved from the DOM').toMatch(/^rgb\(\d+, \d+, \d+\)$/);
      const backdropRgb = composed.backdrop!.match(/\d+/g)!.map(Number);
      for (const [index, edge] of composed.pillarbox.entries()) {
        expect(edge.slice(0, 3), `composed pillarbox ${index} is the resolved backdrop`).toEqual(backdropRgb);
        expect(edge[3], `composed pillarbox ${index} is opaque`).toBe(255);
      }
    });
  }

  test('the flat control renders unchanged through the same headless path', async ({ page }) => {
    test.setTimeout(180_000);
    const { search } = await headlessRender(page, 'globe-4326-world', GOLDEN_VIEW, 'flat');
    await repaint(page);

    // The render URL is the flat one, byte for byte: no projection, and the
    // camera clamped the way it always was.
    expect(search).not.toContain('proj');
    expect(search).toContain(`z=${GOLDEN_VIEW.zoom.toFixed(2)}`);

    expect((await readGlobeStats(page)).lastGeometryMode, 'stays mercator').toBe('mercator');

    const shot = await describeCapture(page);
    expect(shot, 'capture hook must be published').not.toBeNull();
    expect(shot!.distinctColors, 'capture is not a flat fill').toBeGreaterThan(64);
    // The discriminator against the globe cases above: a mercator frame is
    // painted edge to edge, so there are no transparent corners at all.
    expect(shot!.disc, 'no globe disc on the flat map').toBeNull();
    for (const [index, corner] of shot!.corners.entries()) {
      expect(corner[3], `corner ${index} is painted`).toBe(255);
    }
    expect(shot!.opaqueFraction, 'the flat map fills the frame').toBeGreaterThan(0.99);

    // And the composition is unchanged for it: an opaque frame has no disc to
    // measure, so `"captured-alpha"` degrades to today's full-frame compose.
    const composed = await composeAsServerPath(page);
    expect(composed.discFromAlpha, 'no disc measurable in an opaque frame').toBeNull();
    expect(composed.opaqueFraction, 'flat composes full-frame').toBeGreaterThan(0.99);
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

// ── 7. Space background (v1.1) ──────────────────────────────────────────────
/**
 * Operator report: outside the disc the globe sat on the flat map's background
 * grey, which reads as a failed load rather than as space. The fix paints the
 * MAP CONTAINER — the surface that shows through wherever the MapLibre canvas
 * is transparent, which under globe is everything outside the disc — with
 * `GLOBE_SPACE_BACKGROUND_COLOR`.
 *
 * Two things are asserted together because they are one mechanism: the PIXELS
 * a user sees, and the colour `resolveShareBackdropColor()` derives from the
 * DOM for the still/GIF backdrop. The share path must land on space by
 * construction, not by a second literal that could drift.
 */
test.describe('Globe space background (v1.1)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium/SwiftShader contract suite.');
  test.use({
    viewport: { width: 1400, height: 900 },
    timezoneId: 'UTC',
    locale: 'en-US',
    colorScheme: 'light',
  });

  /** `GLOBE_SPACE_BACKGROUND_COLOR` = #02060d, as the browser reports it. */
  const SPACE_RGB = [2, 6, 13];

  /**
   * Per-channel slack on the PIXEL assertions only.
   *
   * Measured, not guessed: three of the four corners come back at exactly
   * [2, 6, 13] and the fourth — the one nearest the disc — at [2, 7, 14]. The
   * map canvas is what paints it: MapLibre's globe draws a very faint halo
   * beyond the limb, so the corner closest to the planet picks up ~1/255 of it.
   * That is the renderer's own atmosphere over the new backdrop, not the
   * backdrop failing to apply, and a 1-count difference cannot hide a
   * regression: the colour this replaced (`#e8edf1` light / `#1f2a33` dark) is
   * 230 and 29 counts away respectively.
   *
   * WHERE THE EXACT VALUE IS PINNED: the share-backdrop assertion below, with
   * no tolerance at all. NOT by the globe goldens — those were not regenerated
   * and structurally cannot carry this colour, because
   * `hideChromeOverCanvas()` hides the map CONTAINER along with everything
   * else, so the region outside the disc in those PNGs is the page background
   * (measured: rgb(7, 17, 31), `html.dark`) and always was. That is also why
   * this change left all four of them bit-identical.
   */
  const CHANNEL_SLACK = 2;

  const expectPixelNear = (actual: number[], expected: number[], message: string) => {
    for (let channel = 0; channel < 3; channel += 1) {
      expect(
        Math.abs(actual[channel] - expected[channel]),
        `${message} (channel ${channel}: got ${actual.join(',')}, want ${expected.join(',')})`,
      ).toBeLessThanOrEqual(CHANNEL_SLACK);
    }
  };

  /**
   * The four corners of the map pane, as painted.
   *
   * Chrome above the map is hidden the same way the golden harness does it, but
   * the map CONTAINER is explicitly re-shown: its background is the thing under
   * test, and the harness's own helper hides it (correct for a map-content
   * golden, wrong here). `visibility` never reflows, so the map is not resized
   * and the render is untouched.
   */
  async function readMapCorners(page: Page) {
    const style = await page.addStyleTag({
      content: `
        *, *::before, *::after { visibility: hidden !important; }
        div[role="img"][aria-label="Weather map"],
        canvas.maplibregl-canvas { visibility: visible !important; }
        /* Re-showing the container re-shows MapLibre's OWN control container,
           which lives inside it and paints a translucent chip in the bottom
           left -- measured as [4, 23, 31] over the space colour. It is chrome
           like any other, so it goes back off. */
        .maplibregl-control-container,
        .maplibregl-control-container * { visibility: hidden !important; }
      `,
    });
    const container = page.locator('div[role="img"][aria-label="Weather map"]').first();
    const buffer = await container.screenshot();
    await style.evaluate((node) => node.remove());
    return page.evaluate(async (dataUrl) => {
      const bitmap = await createImageBitmap(await (await fetch(dataUrl)).blob());
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
      ctx.drawImage(bitmap, 0, 0);
      bitmap.close();
      const at = (x: number, y: number) => {
        const d = ctx.getImageData(x, y, 1, 1).data;
        return [d[0], d[1], d[2]];
      };
      return {
        width: canvas.width,
        height: canvas.height,
        corners: [
          at(2, 2),
          at(canvas.width - 3, 2),
          at(2, canvas.height - 3),
          at(canvas.width - 3, canvas.height - 3),
        ],
      };
    }, `data:image/png;base64,${buffer.toString('base64')}`);
  }

  const readShareBackdrop = (page: Page) =>
    page.evaluate(async () => {
      const se = (await import('/src/lib/screenshot_export.ts')) as {
        resolveShareBackdropColor: (n: Element | null) => string | null;
      };
      return se.resolveShareBackdropColor(document.querySelector('canvas.maplibregl-canvas'));
    });

  test('outside the disc is space, the share backdrop follows it, and flat is unchanged', async ({ page }) => {
    test.setTimeout(180_000);
    await openViewer(page, 'globe-4326-world', '&proj=globe');

    // The camera has to actually show a limb, or the corners prove nothing.
    const limb = await page.evaluate(() => {
      const transform = (window as unknown as {
        __cartoskyGlobe: { map: { transform: { width: number; height: number } & {
          isPointOnMapSurface: (p: { x: number; y: number }) => boolean } } };
      }).__cartoskyGlobe.map.transform;
      const cx = transform.width / 2;
      let lo = 0;
      let hi = Math.max(transform.width, transform.height) * 4;
      for (let i = 0; i < 40; i += 1) {
        const mid = (lo + hi) / 2;
        if (transform.isPointOnMapSurface({ x: cx + mid, y: transform.height / 2 })) lo = mid;
        else hi = mid;
      }
      return { radius: lo, cx, cy: transform.height / 2 };
    });
    expect(limb.radius, 'the corners must be off the disc').toBeLessThan(limb.cx);

    const globe = await readMapCorners(page);
    for (const [index, corner] of globe.corners.entries()) {
      expectPixelNear(corner, SPACE_RGB, `globe corner ${index} reads as space`);
      expect(
        corner,
        `globe corner ${index} is nowhere near the old map grey`,
      ).not.toEqual([232, 237, 241]);
    }
    expect(await readShareBackdrop(page), 'exports sit on space, derived not hardcoded')
      .toBe(`rgb(${SPACE_RGB.join(', ')})`);

    // ── Back to flat: zero visual change, and the backdrop follows. ─────────
    await globeToggle(page).click();
    await expect
      .poll(() => page.evaluate(() => window.location.search), { timeout: 15_000 })
      .not.toContain('proj');
    await repaint(page);

    const flatBackdrop = await readShareBackdrop(page);
    expect(flatBackdrop, 'flat resolves the page colour, not space').not.toBe(
      `rgb(${SPACE_RGB.join(', ')})`,
    );
    expect(flatBackdrop, 'and it is still a real resolved colour').toMatch(/^rgb\(\d+, \d+, \d+\)$/);

    // The map container reverts to the flat surface exactly — this is the
    // "applies and reverts cleanly on toggle" claim, read off the live DOM.
    const containerBackground = await page.evaluate(
      () =>
        getComputedStyle(document.querySelector('div[role="img"][aria-label="Weather map"]')!)
          .backgroundColor,
    );
    expect(containerBackground, 'the container is back on the flat map colour').toBe(flatBackdrop);

    // Corner PIXELS on the flat map are grid data, not the container — the
    // mercator quad covers the viewport edge to edge, which is exactly why
    // flat sees no visual change from any of this. So the claim asserted here
    // is the one that can be asserted from pixels: they are nothing like
    // space. Bit-identical flat rendering is pinned where it belongs, by
    // render-golden-baseline.
    const flat = await readMapCorners(page);
    for (const [index, corner] of flat.corners.entries()) {
      const distance = Math.max(...corner.map((value, channel) => Math.abs(value - SPACE_RGB[channel])));
      expect(distance, `flat corner ${index} is not space (got ${corner.join(',')})`).toBeGreaterThan(20);
    }
  });
});

// ── 8. measureOpaqueDisc decision table ─────────────────────────────────────
/**
 * `measureOpaqueDisc` is the ONLY thing standing between a server capture and a
 * wrong crop: it decides, from a frame's alpha alone, whether that frame is a
 * globe. Its three "no" answers are as load-bearing as its "yes" — a false
 * positive crops a flat map to a square.
 *
 * WHY THIS IS A BROWSER TEST AND NOT A VITEST UNIT TEST. The function is a
 * canvas pixel scan: it needs `document.createElement("canvas")` and a real 2D
 * context, and returns null without them. The unit config runs `environment:
 * "node"` with no jsdom and no `canvas` package installed, so a vitest file
 * would assert only that the guard clause fires. These cases drive the REAL
 * exported function over REAL rasters, synthesized in-page so each input is
 * exactly the shape being claimed.
 */
test.describe('measureOpaqueDisc — the globe/not-a-globe decision', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium/SwiftShader contract suite.');

  test('accepts a disc and refuses everything that is not one', async ({ page }) => {
    test.setTimeout(120_000);
    // Any page that can import app modules through vite; no map needed.
    await page.goto('/src/lib/screenshot_export.ts');

    const results = await page.evaluate(async () => {
      const se = (await import('/src/lib/screenshot_export.ts')) as {
        measureOpaqueDisc: (
          i: CanvasImageSource,
        ) => { centerX: number; centerY: number; radiusPx: number } | null;
      };
      const W = 400;
      const H = 300;
      const paint = (draw: (ctx: CanvasRenderingContext2D) => void) => {
        const canvas = document.createElement('canvas');
        canvas.width = W;
        canvas.height = H;
        const ctx = canvas.getContext('2d')!;
        ctx.clearRect(0, 0, W, H);
        draw(ctx);
        return canvas;
      };

      // 1. A real disc, well inside the frame.
      const disc = paint((ctx) => {
        ctx.fillStyle = '#3388cc';
        ctx.beginPath();
        ctx.arc(180, 140, 90, 0, Math.PI * 2);
        ctx.fill();
      });
      // 2. A flat map: opaque edge to edge.
      const flat = paint((ctx) => {
        ctx.fillStyle = '#3388cc';
        ctx.fillRect(0, 0, W, H);
      });
      // 3. A disc bigger than the frame — opaque in both directions, nothing
      //    to crop to.
      const overflowing = paint((ctx) => {
        ctx.fillStyle = '#3388cc';
        ctx.beginPath();
        ctx.arc(W / 2, H / 2, 400, 0, Math.PI * 2);
        ctx.fill();
      });
      // 4. A non-circular opaque blob whose bounding box is not edge to edge:
      //    a filled rectangle fills 1.0 of its box, outside [0.62, 0.92].
      const blob = paint((ctx) => {
        ctx.fillStyle = '#3388cc';
        ctx.fillRect(40, 30, 200, 150);
      });
      // 5. The other side of the window: a thin diagonal sliver fills far less
      //    than 0.62 of its bounding box.
      const sliver = paint((ctx) => {
        ctx.strokeStyle = '#3388cc';
        ctx.lineWidth = 6;
        ctx.beginPath();
        ctx.moveTo(50, 40);
        ctx.lineTo(300, 240);
        ctx.stroke();
      });
      // 6. Fully transparent: nothing at all.
      const empty = paint(() => {});

      return {
        disc: se.measureOpaqueDisc(disc),
        flat: se.measureOpaqueDisc(flat),
        overflowing: se.measureOpaqueDisc(overflowing),
        blob: se.measureOpaqueDisc(blob),
        sliver: se.measureOpaqueDisc(sliver),
        empty: se.measureOpaqueDisc(empty),
      };
    });

    // YES: the geometry comes back, and it is the geometry that was drawn.
    expect(results.disc, 'a disc is a disc').not.toBeNull();
    expect(results.disc!.centerX).toBeGreaterThan(178);
    expect(results.disc!.centerX).toBeLessThan(182);
    expect(results.disc!.centerY).toBeGreaterThan(138);
    expect(results.disc!.centerY).toBeLessThan(142);
    expect(results.disc!.radiusPx).toBeGreaterThan(88);
    expect(results.disc!.radiusPx).toBeLessThan(92);

    // NO, four ways. Each of these composing full-frame is the correct
    // behaviour; each of them cropped to a square is a broken share image.
    expect(results.flat, 'a flat map must compose full-frame').toBeNull();
    expect(results.overflowing, 'a disc larger than the frame has no crop').toBeNull();
    expect(results.blob, 'a rectangle fills 1.0 of its box, not ~0.785').toBeNull();
    expect(results.sliver, 'a sliver fills far too little of its box').toBeNull();
    expect(results.empty, 'an empty frame is not a globe').toBeNull();
  });
});
