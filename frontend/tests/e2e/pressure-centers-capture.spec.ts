/**
 * H/L PRESSURE CENTRES IN CANVAS CAPTURES
 *
 * The live markers are DOM overlays (`.map-pressure-center`), so every canvas
 * read-back — the server headless screenshot (screenshot_service.py calls
 * `window.__cartoskyViewerCapture`), the signed-out live-canvas share capture,
 * and the share-modal draft preview — dropped them: hgt500_anom screenshots
 * shipped without their H/L annotations.
 *
 * The fix composites the markers onto the captured frame inside
 * `captureDraftDataUrl` (map-canvas.tsx), recomputing positions from the same
 * `buildPressureCenterScreenLabels` the DOM path uses. This spec pins that:
 * the capture hook's PNG must contain the marker glyphs at the projected
 * positions of the fixture's centres.
 *
 * ── WHY PURE-COLOUR PIXEL COUNTS ────────────────────────────────────────────
 * The letter fill is exactly rgb(255,0,0) (H) / rgb(0,80,255) (L) on a 2D
 * canvas — no blending in the glyph core, only at antialiased edges. The
 * anomaly colormaps contain reds and blues but not these exact byte triples in
 * a solid block, and each assertion samples only a small window around the
 * marker's own projected point, so a matching count is the glyph, not ramp.
 *
 * The flat arm renders ALL fixture centres (no hemisphere cull), so every one
 * of the eight must appear in the capture. The globe arm checks a culled
 * capture still paints its near-side markers inside the disc.
 */
import { expect, test, type Page } from '@playwright/test';

import {
  GLOBE_PRESSURE_CENTERS,
  stubGoldenBaselineRoutes,
  viewerUrlForCase,
} from './render-golden-baseline.fixtures';
import { captureLiveCanvas, waitForGoldenReady } from './render-golden-harness';

/** Half-size (CSS px) of the sampling window around a marker's letter. */
const WINDOW_PX = 20;
/** Minimum pure-colour pixels for a 24px/900 letter to count as painted. */
const MIN_GLYPH_PIXELS = 10;

type MarkerSample = { lon: number; lat: number; type: string; pureCount: number };

async function openCenters(page: Page, opts: { globe: boolean }) {
  // The shared golden stubs leave the city-label dataset unstubbed; against a
  // static build (playwright.verify.config.ts) that fetch dies on CORS and
  // city_labels_ready — one third of the data-viewer-ready gate — never fires.
  // Served empty, same as grid-smoke.spec.ts.
  await page.route('**/static/cities/v1/*.json', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ type: 'FeatureCollection', features: [] }),
    });
  });
  await stubGoldenBaselineRoutes(page, 'globe-4326-centers');
  const url = viewerUrlForCase('globe-4326-centers');
  await page.goto(opts.globe ? url : url.replace('&globe=1', ''));
  await waitForGoldenReady(page, `centers-capture${opts.globe ? '' : '-flat'}`);
}

/**
 * Decodes the captured PNG in-page and, for every DOM-rendered marker, counts
 * exact marker-colour pixels in a window around the letter's centre (the
 * letter sits ~4.5 CSS px above the anchor when a value renders beneath it).
 */
async function sampleCapturedMarkers(page: Page, dataUrl: string): Promise<MarkerSample[]> {
  return page.evaluate(
    async ({ png, windowPx }) => {
      const image = new Image();
      await new Promise<void>((resolve, reject) => {
        image.onload = () => resolve();
        image.onerror = () => reject(new Error('capture PNG failed to decode'));
        image.src = png;
      });
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const ctx = canvas.getContext('2d')!;
      ctx.drawImage(image, 0, 0);

      const mapCanvas = document.querySelector('.maplibregl-canvas') as HTMLCanvasElement;
      // Capture is in device px; DOM marker positions are CSS px.
      const scale = image.naturalWidth / (mapCanvas.clientWidth || image.naturalWidth);

      return Array.from(document.querySelectorAll('[data-testid="map-pressure-center"]')).map(
        (el) => {
          const marker = el as HTMLElement;
          const type = (marker.querySelector('.map-pressure-center__letter')?.textContent ?? '').trim();
          const hasValue = Boolean(marker.querySelector('.map-pressure-center__value'));
          const anchorX = parseFloat(marker.style.left);
          const anchorY = parseFloat(marker.style.top) - (hasValue ? 4.5 : 0);
          const cx = Math.round(anchorX * scale);
          const cy = Math.round(anchorY * scale);
          const half = Math.round(windowPx * scale);
          const x0 = Math.max(0, cx - half);
          const y0 = Math.max(0, cy - half);
          const w = Math.min(canvas.width, cx + half) - x0;
          const h = Math.min(canvas.height, cy + half) - y0;
          let pureCount = 0;
          if (w > 0 && h > 0) {
            const data = ctx.getImageData(x0, y0, w, h).data;
            for (let i = 0; i < data.length; i += 4) {
              const [r, g, b] = [data[i], data[i + 1], data[i + 2]];
              const isH = r === 255 && g === 0 && b === 0;
              const isL = r === 0 && g === 80 && b === 255;
              if ((type === 'H' && isH) || (type === 'L' && isL)) {
                pureCount += 1;
              }
            }
          }
          return {
            lon: Number(marker.dataset.centerLon),
            lat: Number(marker.dataset.centerLat),
            type,
            pureCount,
          };
        },
      );
    },
    { png: dataUrl, windowPx: WINDOW_PX },
  );
}

test.describe('pressure centres in canvas captures', () => {
  test('flat capture paints every H/L marker', async ({ page }) => {
    await openCenters(page, { globe: false });

    // All eight fixture centres render on mercator (no cull) — a vacuous
    // zero-marker pass is impossible.
    await expect
      .poll(async () => page.locator('[data-testid="map-pressure-center"]').count(), {
        timeout: 10_000,
        message: 'flat map shows every pressure centre',
      })
      .toBe(GLOBE_PRESSURE_CENTERS.length);

    const dataUrl = await captureLiveCanvas(page);
    const samples = await sampleCapturedMarkers(page, dataUrl);
    expect(samples.length).toBe(GLOBE_PRESSURE_CENTERS.length);
    for (const sample of samples) {
      expect(
        sample.pureCount,
        `${sample.type} at ${sample.lon},${sample.lat}: glyph pixels in capture`,
      ).toBeGreaterThanOrEqual(MIN_GLYPH_PIXELS);
    }
  });

  test('globe capture paints the surviving near-side markers', async ({ page }) => {
    await openCenters(page, { globe: true });

    const rendered = await page.locator('[data-testid="map-pressure-center"]').count();
    // The camera in the fixture provably keeps at least three centres in the
    // near cap (globe-pressure-centers.spec.ts) — the cull cannot empty this.
    expect(rendered).toBeGreaterThanOrEqual(3);

    const dataUrl = await captureLiveCanvas(page);
    const samples = await sampleCapturedMarkers(page, dataUrl);
    expect(samples.length).toBe(rendered);
    for (const sample of samples) {
      expect(
        sample.pureCount,
        `${sample.type} at ${sample.lon},${sample.lat}: glyph pixels in globe capture`,
      ).toBeGreaterThanOrEqual(MIN_GLYPH_PIXELS);
    }
  });
});
