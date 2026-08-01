/**
 * ════════════════════════════════════════════════════════════════════════════
 * RENDER GOLDEN BASELINE — GLOBE RENDERER (Phase G1)
 * ════════════════════════════════════════════════════════════════════════════
 *
 * Pins the custom WebGL grid layer under MapLibre's globe (vertical-
 * perspective) projection. Captured through the SAME harness as the mercator
 * goldens (render-golden-harness.ts): same stubs, same readiness gate, same
 * live-canvas hook, same in-page pixel diff, same thresholds. The fixtures are
 * the mercator fixtures verbatim — only the camera and the `globe=1` boot flag
 * differ — so a globe golden can only diverge from its mercator sibling by
 * projection.
 *
 * Four cases (render-golden-baseline.fixtures.ts):
 *   F. globe-4326-world     global 0.25 deg field on the sphere, limb all round
 *   G. globe-4326-seam      camera on lon 180: the antimeridian down the middle
 *   H. globe-4326-pole      north polar cap, i.e. the +90 deg apex fan
 *   I. globe-3857-regional  a CONUS mercator footprint curved onto the sphere
 *
 * Beyond the images, this suite pins the four renderer invariants that a
 * picture cannot state:
 *   1. ONE draw per frame. The world-copy loop is a mercator-only device and
 *      must not run on globe frames (globeMeshDraws climbs, mercatorQuadDraws
 *      frozen at 0).
 *   2. The geometry/matrix tripwire never fires (projectionMismatch === 0). A
 *      mercator quad multiplied by a unit-sphere matrix draws garbage with no
 *      GL error; that is the one silent failure mode in this layer.
 *   3. The manifest/texture coherence guard is untouched and stays orthogonal
 *      to projection (drewMismatched === 0, coherentDraws climbing).
 *   4. The transition hard-flip: whenever MapLibre's projection transition is
 *      running (transitionState < 1) the layer draws the mercator quad against
 *      MapLibre's fallback matrix, and the flip is sub-pixel.
 *
 * Determinism pins are inherited from the harness. Chromium/SwiftShader only.
 */
import { expect, test, type Page } from '@playwright/test';

import {
  GLOBE_GOLDEN_CASE_IDS,
  GOLDEN_VIEWPORT,
  stubGoldenBaselineRoutes,
  viewerUrlForCase,
  type GoldenCaseId,
} from './render-golden-baseline.fixtures';
import {
  MAX_DIFF_PIXEL_RATIO,
  captureLiveCanvas,
  diffPngs,
  expectMatchesGolden,
  hideChromeOverCanvas,
  mapCanvas,
  measureRenderTimings,
  waitForGoldenReady,
  writeTimingArtifact,
  type TimingEntry,
} from './render-golden-harness';

const timingByCase: Record<string, TimingEntry> = {};



type GlobeRenderStats = {
  drawFrames: number;
  globeMeshDraws: number;
  mercatorQuadDraws: number;
  lastGeometryMode: 'mercator' | 'globe';
  lastMatrixSpace: 'mercator-unit' | 'unit-sphere';
  lastTransitionState: number;
  lastIndexCount: number;
  lastPoleFanTriangles: number;
  lastMainMatrix: number[] | null;
  lastFallbackMatrix: number[] | null;
  lastClippingPlane: number[] | null;
};

type CoherenceStats = {
  held: number;
  drewMismatched: number;
  coherentDraws: number;
  reconciled: number;
  resetForReupload: number;
  projectionMismatch: number;
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

const EMPTY_COHERENCE: CoherenceStats = {
  held: 0,
  drewMismatched: 0,
  coherentDraws: 0,
  reconciled: 0,
  resetForReupload: 0,
  projectionMismatch: 0,
};

/** Null-safe: the debug surface only exists once the grid module has loaded. */
const readCoherence = (page: Page): Promise<CoherenceStats> =>
  page.evaluate(
    () =>
      (window as unknown as { __cartoskyGridCoherence?: { read: () => CoherenceStats } })
        .__cartoskyGridCoherence?.read() ?? {
          held: 0,
          drewMismatched: 0,
          coherentDraws: 0,
          reconciled: 0,
          resetForReupload: 0,
          projectionMismatch: 0,
        },
  ).catch(() => EMPTY_COHERENCE);

/**
 * Open a globe case, failing FAST and accurately on a projection mismatch.
 *
 * The tripwire holds every draw, so `onFrameVisible` never fires and the app's
 * readiness latch never closes -- a plain wait would spend 60 s and then blame
 * readiness. Racing the counter against the latch makes the counter the thing
 * the suite reports, and it reports it before any golden is compared.
 */
async function openGlobeCase(page: Page, id: GoldenCaseId) {
  await stubGoldenBaselineRoutes(page, id);
  await page.goto(viewerUrlForCase(id));
  await expect
    .poll(
      async () => {
        const mismatch = (await readCoherence(page)).projectionMismatch;
        if (mismatch > 0) {
          return `projectionMismatch=${mismatch}`;
        }
        return page.evaluate(() => document.documentElement.getAttribute('data-viewer-ready'));
      },
      {
        timeout: 60_000,
        message: `${id}: viewer ready AND the geometry/matrix tripwire never fired`,
      },
    )
    .toBe('1');
  await waitForGoldenReady(page, id);
}

/**
 * Force enough repaints that the counters describe steady state rather than
 * whatever the readiness gate happened to leave behind.
 */
async function repaint(page: Page, frames = 3) {
  await page.evaluate(async (count) => {
    const map = (window as unknown as { __cartoskyGlobe: { map?: { triggerRepaint: () => void } } })
      .__cartoskyGlobe.map!;
    for (let index = 0; index < count; index += 1) {
      map.triggerRepaint();
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }
  }, frames);
}

test.describe('Render golden baseline — globe renderer (Phase G1)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Pinned Chromium/SwiftShader baseline.');

  // Serial, deliberately: parallel workers sharing one dev server and one CPU
  // would contaminate the render-timing samples, and the timing artifact is
  // aggregated in-process by afterAll.
  test.describe.configure({ mode: 'serial' });

  test.use({
    viewport: GOLDEN_VIEWPORT,
    timezoneId: 'UTC',
    locale: 'en-US',
    colorScheme: 'light',
  });

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop baseline.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  // ── Noise-floor probe, measured on the globe path itself ─────────────────
  test('determinism probe: two back-to-back globe captures are identical', async ({ page }) => {
    test.setTimeout(120_000);
    await openGlobeCase(page, 'globe-4326-world');
    const first = await captureLiveCanvas(page);
    const second = await captureLiveCanvas(page);
    const report = await diffPngs(page, second, first, 0);
    // eslint-disable-next-line no-console
    console.log(
      `[determinism-probe] globe-4326-world ${report.width}x${report.height} ` +
        `exactDiffPixels=${report.diffPixels}/${report.totalPixels} ` +
        `maxChannelDelta=${report.maxChannelDelta}`,
    );
    // Reported at tolerance 0 (exact) to establish the floor the thresholds sit
    // above, exactly as the mercator suite does.
    expect(report.diffRatio).toBeLessThanOrEqual(MAX_DIFF_PIXEL_RATIO);
  });

  // ── Still cases: both capture paths + renderer invariants + timing ───────
  for (const id of GLOBE_GOLDEN_CASE_IDS) {
    test(`${id}: map canvas golden on the globe`, async ({ page }) => {
      test.setTimeout(120_000);
      await openGlobeCase(page, id);

      // The renderer really is on the globe path, and only on it.
      await resetGlobeStats(page);
      await repaint(page);
      const stats = await readGlobeStats(page);
      expect(stats.lastGeometryMode, `${id}: geometry mode`).toBe('globe');
      expect(stats.lastMatrixSpace, `${id}: matrix space`).toBe('unit-sphere');
      expect(stats.lastTransitionState, `${id}: transition complete`).toBe(1);
      expect(stats.drawFrames, `${id}: frames reached the draw stage`).toBeGreaterThan(0);
      // THE no-double-draw assertion, stated exactly: one drawElements per
      // frame and nothing else. The world-copy loop is a mercator-only device
      // (it adds whole mercator worlds to the model translation, which is
      // meaningless against a unit-sphere matrix) and must not run at all.
      expect(stats.globeMeshDraws, `${id}: exactly one mesh draw per frame`).toBe(stats.drawFrames);
      expect(stats.mercatorQuadDraws, `${id}: world-copy loop must not run on globe`).toBe(0);

      const coherence = await readCoherence(page);
      expect(coherence.projectionMismatch, `${id}: geometry/matrix tripwire`).toBe(0);
      expect(coherence.drewMismatched, `${id}: manifest/texture coherence`).toBe(0);
      expect(coherence.coherentDraws, `${id}: layer is actually drawing`).toBeGreaterThan(0);

      // 1. LIVE-CANVAS PATH — the app's own repaint-hook PNG, DOM untouched.
      const liveDataUrl = await captureLiveCanvas(page);
      await expectMatchesGolden(page, `${id}-live-canvas.png`, liveDataUrl);

      // 2. Frame timing, before any style injection.
      await measureRenderTimings(page, id, timingByCase);

      // 3. SERVER-SIDE PATH — Playwright owns this golden.
      await hideChromeOverCanvas(page);
      await expect(mapCanvas(page)).toHaveScreenshot(`${id}-map-canvas.png`, {
        threshold: 0.02,
        maxDiffPixelRatio: MAX_DIFF_PIXEL_RATIO,
        animations: 'disabled',
        caret: 'hide',
      });

      // A silently-blank disc can never pass as a golden.
      const distinctColors = await page.evaluate(async (source) => {
        const response = await fetch(source);
        const bitmap = await createImageBitmap(await response.blob());
        const canvas = document.createElement('canvas');
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
        ctx.drawImage(bitmap, 0, 0);
        bitmap.close();
        const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        const seen = new Set<number>();
        for (let offset = 0; offset < data.length; offset += 4 * 37) {
          seen.add((data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]);
          if (seen.size > 64) break;
        }
        return seen.size;
      }, liveDataUrl);
      expect(distinctColors, `${id}: rendered canvas is not flat`).toBeGreaterThan(8);
    });
  }

  // ── Poles: the apex fan, not a hole and not a degenerate ring ────────────
  test('globe-4326-pole: the +90 deg row renders as a real fan converging on one point', async ({ page }) => {
    test.setTimeout(120_000);
    await openGlobeCase(page, 'globe-4326-pole');
    await repaint(page);

    const stats = await readGlobeStats(page);
    const meshCols = await page.evaluate(
      () => (window as unknown as { __cartoskyGlobe: { meshCols: number } }).__cartoskyGlobe.meshCols,
    );
    // Both poles are inside a full-world 4326 footprint, so both fans exist in
    // the uploaded mesh even though only one is on screen.
    expect(stats.lastPoleFanTriangles, 'two polar fans of `meshCols` triangles').toBe(2 * meshCols);
    expect(stats.lastIndexCount, 'mesh is the subdivided sphere, not a quad').toBeGreaterThan(1000);

    // The pole is on screen and covered by opaque data — no hole, no tear. The
    // probe reads the app's own capture PNG at the pixel MapLibre projects
    // lon 0 / lat 90 to, so it cannot pass by accident on a blank canvas.
    const liveDataUrl = await captureLiveCanvas(page);
    const probe = await page.evaluate(async (source) => {
      const map = (window as unknown as {
        __cartoskyGlobe: {
          map: {
            project: (lngLat: [number, number]) => { x: number; y: number };
            getCanvas: () => HTMLCanvasElement;
          };
        };
      }).__cartoskyGlobe.map;
      const point = map.project([0, 90]);
      const canvasEl = map.getCanvas();
      const response = await fetch(source);
      const bitmap = await createImageBitmap(await response.blob());
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
      ctx.drawImage(bitmap, 0, 0);
      bitmap.close();
      // The capture PNG is the drawing buffer; map.project() is in CSS px.
      const scaleX = canvas.width / canvasEl.clientWidth;
      const scaleY = canvas.height / canvasEl.clientHeight;
      const x = Math.round(point.x * scaleX);
      const y = Math.round(point.y * scaleY);
      const inside = x >= 0 && y >= 0 && x < canvas.width && y < canvas.height;
      // Sample a small neighbourhood: a tear or a pinhole at the apex would be
      // a low-alpha pixel next to opaque ones.
      let minAlpha = 255;
      let samples = 0;
      if (inside) {
        for (let dy = -6; dy <= 6; dy += 2) {
          for (let dx = -6; dx <= 6; dx += 2) {
            const px = x + dx;
            const py = y + dy;
            if (px < 0 || py < 0 || px >= canvas.width || py >= canvas.height) continue;
            const alpha = ctx.getImageData(px, py, 1, 1).data[3];
            minAlpha = Math.min(minAlpha, alpha);
            samples += 1;
          }
        }
      }
      return { x, y, inside, minAlpha, samples, width: canvas.width, height: canvas.height };
    }, liveDataUrl);

    // eslint-disable-next-line no-console
    console.log(
      `[pole-probe] lon0/lat90 -> (${probe.x},${probe.y}) inside=${probe.inside} ` +
        `minAlpha=${probe.minAlpha} samples=${probe.samples} canvas=${probe.width}x${probe.height}`,
    );
    expect(probe.inside, 'the north pole is inside the captured frame').toBe(true);
    expect(probe.samples, 'the neighbourhood probe actually sampled pixels').toBeGreaterThan(20);
    expect(probe.minAlpha, 'no hole or tear at the polar apex').toBeGreaterThan(200);
  });

  /**
   * POLAR CAP AT TWO MESH DENSITIES -- the visual evidence for the ring
   * topology, attached to the run as `pole-crop-64x32.png` /
   * `pole-crop-64x256.png` and logged as annulus statistics.
   *
   * DELIBERATELY NOT A CONVERGENCE THRESHOLD. Raising rows 32 -> 256 moves the
   * first mesh row from lat 84.4 to lat 89.3, so the geometry of the polar band
   * genuinely differs between the two renders no matter how the pole is wired:
   * measured, the innermost annulus differs ~14x more than the outer one even
   * with correct topology. A pixel comparison here cannot separate "apex shear"
   * from "coarse latitude sampling near the pole", so it is reported, not gated.
   *
   * The GATE for the apex defect is the unit test
   * `keeps texture longitude within one column of geometric longitude inside
   * every polar triangle` (tests/unit/globe-mesh.test.ts), which measures the
   * defect directly and is independent of mesh density: ring topology scores
   * 2.626 degrees against a one-column bound of 5.625; a single apex vertex
   * scores 141.75.
   *
   * What this test does gate: the polar cap is opaque and on the globe path at
   * BOTH densities, i.e. no hole, no tear, and no density at which the pole
   * stops rendering.
   */
  test('globe-4326-pole: polar cap renders cleanly at 64x32 and 64x256 (evidence + coverage)', async ({ page }) => {
    test.setTimeout(180_000);

    const capture = async (density: string) => {
      await stubGoldenBaselineRoutes(page, 'globe-4326-pole');
      await page.goto(`${viewerUrlForCase('globe-4326-pole')}&globeMesh=${density}`);
      await waitForGoldenReady(page, `globe-4326-pole@${density}`);
      const stats = await readGlobeStats(page);
      expect(stats.lastGeometryMode, `${density}: on the globe path`).toBe('globe');
      return { dataUrl: await captureLiveCanvas(page), indices: stats.lastIndexCount };
    };

    // Crop box centred on the projected pole. Everything outside it is ordinary
    // mesh whose latitude sampling legitimately differs with row count, so the
    // comparison is deliberately confined to the polar cap.
    const CROP = 280;
    const cropAroundPole = async (dataUrl: string, label: string) => {
      const cropped = await page.evaluate(
        async (args) => {
          const map = (window as unknown as {
            __cartoskyGlobe: {
              map: {
                project: (lngLat: [number, number]) => { x: number; y: number };
                getCanvas: () => HTMLCanvasElement;
              };
            };
          }).__cartoskyGlobe.map;
          const point = map.project([0, 90]);
          const element = map.getCanvas();
          const response = await fetch(args.dataUrl);
          const bitmap = await createImageBitmap(await response.blob());
          const full = document.createElement('canvas');
          full.width = bitmap.width;
          full.height = bitmap.height;
          full.getContext('2d')!.drawImage(bitmap, 0, 0);
          bitmap.close();
          const scaleX = full.width / element.clientWidth;
          const scaleY = full.height / element.clientHeight;
          const cx = Math.round(point.x * scaleX);
          const cy = Math.round(point.y * scaleY);
          const half = Math.floor(args.size / 2);
          const left = Math.max(0, Math.min(full.width - args.size, cx - half));
          const top = Math.max(0, Math.min(full.height - args.size, cy - half));
          const crop = document.createElement('canvas');
          crop.width = args.size;
          crop.height = args.size;
          crop.getContext('2d')!.drawImage(full, left, top, args.size, args.size, 0, 0, args.size, args.size);
          return { dataUrl: crop.toDataURL('image/png'), left, top, cx, cy };
        },
        { dataUrl, size: CROP },
      );
      await test.info().attach(`pole-crop-${label}.png`, {
        body: Buffer.from(cropped.dataUrl.replace(/^data:image\/png;base64,/, ''), 'base64'),
        contentType: 'image/png',
      });
      return cropped;
    };

    const production = await capture('64x32');
    const productionCrop = await cropAroundPole(production.dataUrl, '64x32');
    const dense = await capture('64x256');
    const denseCrop = await cropAroundPole(dense.dataUrl, '64x256');

    // Same crop box in both, or the diff would be measuring a shift.
    expect(denseCrop.left).toBe(productionCrop.left);
    expect(denseCrop.top).toBe(productionCrop.top);
    expect(dense.indices).toBeGreaterThan(production.indices * 4);

    // Annulus statistics, REPORTED for the record (see the docblock for why
    // they are not a threshold).
    const annuli = await page.evaluate(
      async (args) => {
        const toImageData = async (source: string) => {
          const response = await fetch(source);
          const bitmap = await createImageBitmap(await response.blob());
          const canvas = document.createElement('canvas');
          canvas.width = bitmap.width;
          canvas.height = bitmap.height;
          canvas.getContext('2d', { willReadFrequently: true })!.drawImage(bitmap, 0, 0);
          bitmap.close();
          return canvas
            .getContext('2d', { willReadFrequently: true })!
            .getImageData(0, 0, canvas.width, canvas.height);
        };
        const a = await toImageData(args.a);
        const b = await toImageData(args.b);
        const centre = args.size / 2;
        const bands = args.bands.map(([inner, outer]: [number, number]) => ({
          inner,
          outer,
          total: 0,
          diff: 0,
          maxDelta: 0,
          minAlphaA: 255,
          minAlphaB: 255,
        }));
        for (let y = 0; y < args.size; y += 1) {
          for (let x = 0; x < args.size; x += 1) {
            const radius = Math.hypot(x - centre, y - centre);
            const band = bands.find((entry) => radius >= entry.inner && radius < entry.outer);
            if (!band) continue;
            const o = (y * args.size + x) * 4;
            const delta = Math.max(
              Math.abs(a.data[o] - b.data[o]),
              Math.abs(a.data[o + 1] - b.data[o + 1]),
              Math.abs(a.data[o + 2] - b.data[o + 2]),
              Math.abs(a.data[o + 3] - b.data[o + 3]),
            );
            band.total += 1;
            band.maxDelta = Math.max(band.maxDelta, delta);
            band.minAlphaA = Math.min(band.minAlphaA, a.data[o + 3]);
            band.minAlphaB = Math.min(band.minAlphaB, b.data[o + 3]);
            if (delta > args.tolerance) band.diff += 1;
          }
        }
        return bands.map((entry) => ({
          ...entry,
          ratio: entry.total === 0 ? 0 : entry.diff / entry.total,
        }));
      },
      {
        a: productionCrop.dataUrl,
        b: denseCrop.dataUrl,
        size: CROP,
        tolerance: 5,
        bands: [[0, 30], [30, 70], [70, 140]] as Array<[number, number]>,
      },
    );

    // eslint-disable-next-line no-console
    console.log(
      `[pole-crops] 64x32 (${production.indices} idx) vs 64x256 (${dense.indices} idx) ` +
        `crop ${CROP}x${CROP} at (${productionCrop.left},${productionCrop.top}) :: ` +
        annuli
          .map((band) => `r${band.inner}-${band.outer} ratio=${band.ratio.toFixed(4)} max=${band.maxDelta}`)
          .join(' | '),
    );

    // Coverage IS gated: the polar cap must be opaque at both densities. A hole,
    // a tear, or a density at which the pole stops rendering fails here.
    for (const band of annuli) {
      expect(band.total, `annulus r${band.inner}-${band.outer} sampled pixels`).toBeGreaterThan(100);
      expect(
        band.minAlphaA,
        `64x32: no hole in the polar cap at r${band.inner}-${band.outer}`,
      ).toBeGreaterThan(200);
      expect(
        band.minAlphaB,
        `64x256: no hole in the polar cap at r${band.inner}-${band.outer}`,
      ).toBeGreaterThan(200);
    }
  });

  // ── Seam: the antimeridian closes on the sphere too ──────────────────────
  test('globe-4326-seam: data is continuous across the antimeridian on the sphere', async ({ page }) => {
    test.setTimeout(120_000);
    await openGlobeCase(page, 'globe-4326-seam');

    const liveDataUrl = await captureLiveCanvas(page);
    const report = await page.evaluate(async (source) => {
      const response = await fetch(source);
      const bitmap = await createImageBitmap(await response.blob());
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
      ctx.drawImage(bitmap, 0, 0);
      bitmap.close();
      const { width, height } = canvas;
      const image = ctx.getImageData(0, 0, width, height).data;
      const at = (x: number, y: number) => (y * width + x) * 4;
      const luma = (x: number, y: number) => {
        const o = at(x, y);
        return 0.299 * image[o] + 0.587 * image[o + 1] + 0.114 * image[o + 2];
      };

      // Scanline through the disc centre. The camera sits on lon 180 and lat 0,
      // so the sub-camera point IS the seam and it lands on the canvas centre.
      const y = Math.round(height / 2);
      const seamX = Math.round(width / 2);
      // Find the disc by alpha, so the "control" band never wanders onto the
      // transparent background outside the limb.
      let discLeft = seamX;
      let discRight = seamX;
      while (discLeft > 1 && image[at(discLeft - 1, y) + 3] > 200) discLeft -= 1;
      while (discRight < width - 2 && image[at(discRight + 1, y) + 3] > 200) discRight += 1;

      let seamStepMax = 0;
      let controlStepMax = 0;
      // Inset from the limb: the outermost pixels are the sphere's silhouette,
      // where one pixel spans a huge longitude span and the step is geometric,
      // not a seam artefact.
      const inset = 8;
      for (let x = discLeft + inset; x < discRight - inset; x += 1) {
        const step = Math.abs(luma(x + 1, y) - luma(x, y));
        if (x >= seamX - 3 && x <= seamX + 2) {
          seamStepMax = Math.max(seamStepMax, step);
        } else {
          controlStepMax = Math.max(controlStepMax, step);
        }
      }
      let minAlpha = 255;
      for (let x = seamX - 40; x <= seamX + 40; x += 1) {
        minAlpha = Math.min(minAlpha, image[at(x, y) + 3]);
      }
      return { width, height, y, seamX, discLeft, discRight, seamStepMax, controlStepMax, minAlpha };
    }, liveDataUrl);

    // eslint-disable-next-line no-console
    console.log(
      `[globe-seam-probe] ${report.width}x${report.height} disc=[${report.discLeft},${report.discRight}] ` +
        `seamX=${report.seamX} minAlpha=${report.minAlpha} ` +
        `seamStepMax=${report.seamStepMax.toFixed(2)} controlStepMax=${report.controlStepMax.toFixed(2)}`,
    );

    // The disc is real and the seam band carries data at full alpha — a missing
    // wrap column would read as an alpha dip right here.
    expect(report.discRight - report.discLeft, 'the globe disc was found').toBeGreaterThan(200);
    expect(report.minAlpha, 'no gap along the seam').toBeGreaterThan(200);
    // The fixture field is periodic in longitude, so any step AT the seam is
    // the renderer's. Measured on the spike: 1.78 seam vs 8.93 control. The
    // bound is the same shape as the mercator suite's: the seam must not be an
    // outlier against the field's own gradient.
    expect(report.controlStepMax, 'the field really does have a gradient').toBeGreaterThan(2);
    expect(
      report.seamStepMax,
      'luminance step at the seam is not an outlier against the field gradient',
    ).toBeLessThanOrEqual(Math.max(6, report.controlStepMax));
  });

  // ── Transition: the hard flip to the mercator quad ───────────────────────
  test('projection transition hard-flips to the mercator quad, sub-pixel, with no mismatch', async ({ page }) => {
    test.setTimeout(180_000);
    await openGlobeCase(page, 'globe-4326-world');

    // The fixture region caps maxZoom at 9; the transition MapLibre builds for
    // `{type:'globe'}` lives at zoom 11..12 (interpolate linear zoom 11
    // 'vertical-perspective' 12 'mercator'), so the camera cap is lifted here.
    // This is test camera setup, not a renderer change.
    await page.evaluate(() => {
      (window as unknown as { __cartoskyGlobe: { map: { setMaxZoom: (z: number) => void } } })
        .__cartoskyGlobe.map.setMaxZoom(14);
    });

    /**
     * Park the camera, let it settle, THEN zero the draw counters and measure.
     * The counters must describe steady state at the new zoom: MapLibre
     * re-evaluates the projection's zoom-interpolated `type` during style
     * recalculation, so a frame issued between jumpTo() and that recalculation
     * legitimately still belongs to the previous transition state.
     */
    const parkAt = async (zoom: number) => {
      await page.evaluate((z) => {
        (window as unknown as {
          __cartoskyGlobe: { map: { jumpTo: (options: { zoom: number }) => void } };
        }).__cartoskyGlobe.map.jumpTo({ zoom: z });
      }, zoom);
      await repaint(page, 4);
      await resetGlobeStats(page);
      await repaint(page, 3);
      return readGlobeStats(page);
    };

    // 1. Fully spherical: globe mesh against the unit-sphere matrix.
    const atGlobe = await parkAt(10.5);
    expect(atGlobe.lastTransitionState, 'z10.5 is fully globe').toBe(1);
    expect(atGlobe.lastGeometryMode).toBe('globe');
    expect(atGlobe.lastMatrixSpace).toBe('unit-sphere');

    // 2. Mid-transition: MapLibre still hands the layer a sphere matrix AND a
    //    hard-coded projectionTransition of 1 (GLOBE_SPIKE sec 5), so this is
    //    exactly the case that would silently draw garbage. The layer must flip
    //    to the quad and take the FALLBACK matrix.
    for (const zoom of [11.25, 11.5, 11.75]) {
      const mid = await parkAt(zoom);
      expect(mid.lastTransitionState, `z${zoom}: transition is running`).toBeLessThan(1);
      expect(mid.lastTransitionState, `z${zoom}: transition is running`).toBeGreaterThan(0);
      expect(mid.lastGeometryMode, `z${zoom}: flipped to the quad`).toBe('mercator');
      expect(mid.lastMatrixSpace, `z${zoom}: quad uses a mercator matrix`).toBe('mercator-unit');
      expect(mid.globeMeshDraws, `z${zoom}: no sphere mesh`).toBe(0);
      expect(mid.drawFrames, `z${zoom}: frames reached the draw stage`).toBeGreaterThan(0);
      // Still exactly ONE copy per frame: visibleWorldOffsets() must not run on
      // a globe transform, whose corner unprojects clamp to horizon longitudes
      // and would yield a nonsense world range.
      expect(mid.mercatorQuadDraws, `z${zoom}: one copy per frame`).toBe(mid.drawFrames);
    }

    // 3. Past the transition MapLibre stops globe-rendering entirely and the
    //    frame is a plain mercator frame again.
    const atMercator = await parkAt(12.5);
    expect(atMercator.lastGeometryMode).toBe('mercator');
    expect(atMercator.lastMatrixSpace).toBe('mercator-unit');
    expect(atMercator.globeMeshDraws).toBe(0);

    // 4. THE FLIP IS SUB-PIXEL. At the flip zoom the layer still has both
    //    matrices in hand, so the two paths can be evaluated against the same
    //    camera: place a set of lngLats through the globe path (sphere vector x
    //    mainMatrix, the production vertex shader) and through the quad path
    //    (mercator-unit x fallbackMatrix) and compare in device pixels.
    await parkAt(11.0);
    const divergence = await page.evaluate(() => {
      const globeApi = (window as unknown as {
        __cartoskyGlobe: {
          map: {
            getCenter: () => { lng: number; lat: number };
            getCanvas: () => HTMLCanvasElement;
          };
        };
      }).__cartoskyGlobe;
      const stats = (window as unknown as {
        __cartoskyGlobeRender: { read: () => { lastMainMatrix: number[] | null; lastFallbackMatrix: number[] | null } };
      }).__cartoskyGlobeRender.read();
      const main = stats.lastMainMatrix;
      const fallback = stats.lastFallbackMatrix;
      if (!main || !fallback) {
        return null;
      }
      const canvas = globeApi.map.getCanvas();
      const width = canvas.width;
      const height = canvas.height;
      const apply = (m: number[], v: [number, number, number]) => {
        const x = m[0] * v[0] + m[4] * v[1] + m[8] * v[2] + m[12];
        const y = m[1] * v[0] + m[5] * v[1] + m[9] * v[2] + m[13];
        const w = m[3] * v[0] + m[7] * v[1] + m[11] * v[2] + m[15];
        return [((x / w) * 0.5 + 0.5) * width, (0.5 - (y / w) * 0.5) * height];
      };
      const centre = globeApi.map.getCenter();
      let worst = 0;
      const rows: string[] = [];
      for (const [dLng, dLat] of [[0, 0], [0.002, 0], [0, 0.002], [-0.002, -0.002]]) {
        const lng = centre.lng + dLng;
        const lat = centre.lat + dLat;
        const lngRad = (lng * Math.PI) / 180;
        const latRad = (lat * Math.PI) / 180;
        const len = Math.cos(latRad);
        const globePx = apply(main, [
          Math.sin(lngRad) * len,
          Math.sin(latRad),
          Math.cos(lngRad) * len,
        ]);
        const mercX = (lng + 180) / 360;
        const mercY = 0.5 - Math.log(Math.tan(Math.PI / 4 + latRad / 2)) / (2 * Math.PI);
        const quadPx = apply(fallback, [mercX, mercY, 0]);
        const distance = Math.hypot(globePx[0] - quadPx[0], globePx[1] - quadPx[1]);
        worst = Math.max(worst, distance);
        rows.push(`${dLng},${dLat}=${distance.toFixed(3)}px`);
      }
      return { worst, rows, width, height };
    });

    expect(divergence, 'both matrices were captured at the flip zoom').not.toBeNull();
    // eslint-disable-next-line no-console
    console.log(
      `[transition-flip] worst=${divergence!.worst.toFixed(3)}px ` +
        `(${divergence!.rows.join(' ')}) canvas=${divergence!.width}x${divergence!.height}`,
    );
    // The spike measured 0.000 px at transitionState 1 and <= 0.156 px through
    // the whole ramp. 1 device pixel is a generous ceiling that still fails
    // loudly if the two paths ever stop agreeing.
    expect(divergence!.worst, 'globe and quad paths agree at the flip').toBeLessThan(1);

    // 5. The tripwire never fired anywhere in the sweep.
    const coherence = await readCoherence(page);
    expect(coherence.projectionMismatch, 'geometry/matrix tripwire across the transition').toBe(0);
    expect(coherence.drewMismatched, 'manifest/texture coherence across the transition').toBe(0);
  });

  // ── Perf: same-session globe vs mercator A/B ─────────────────────────────
  //
  // ONE page, ONE map instance, ONE camera per arm — only the projection
  // differs, and it is flipped through the production runtime setter
  // (window.__cartoskyGlobe.setEnabled), which is the same entry point the
  // Phase G2 button will call. That also makes this a functional test of the
  // internal switch.
  for (const id of ['globe-4326-world', 'globe-3857-regional'] as GoldenCaseId[]) {
    test(`${id}: globe vs mercator frame-time A/B`, async ({ page }) => {
      test.setTimeout(180_000);
      await openGlobeCase(page, id);

      const globeArm = await measureRenderTimings(page, `${id}:globe`, timingByCase);
      const statsOnGlobe = await readGlobeStats(page);
      expect(statsOnGlobe.lastGeometryMode, `${id}: A arm is the globe`).toBe('globe');

      // Flip to mercator through the runtime setter and let the map settle.
      await page.evaluate(() => {
        (window as unknown as { __cartoskyGlobe: { setEnabled: (value: boolean) => void } })
          .__cartoskyGlobe.setEnabled(false);
      });
      await expect
        .poll(async () => (await readGlobeStats(page)).lastGeometryMode, { timeout: 20_000 })
        .toBe('mercator');
      await repaint(page, 5);
      await resetGlobeStats(page);
      await repaint(page, 3);

      const afterFlip = await readGlobeStats(page);
      expect(afterFlip.mercatorQuadDraws, `${id}: mercator arm draws the quad`).toBeGreaterThan(0);
      expect(afterFlip.drawFrames, `${id}: mercator arm reached the draw stage`).toBeGreaterThan(0);
      expect(afterFlip.globeMeshDraws, `${id}: mercator arm draws no mesh`).toBe(0);
      expect(afterFlip.lastMatrixSpace).toBe('mercator-unit');

      const mercatorArm = await measureRenderTimings(page, `${id}:mercator`, timingByCase);

      // The runtime setter round-trips.
      await page.evaluate(() => {
        (window as unknown as { __cartoskyGlobe: { setEnabled: (value: boolean) => void } })
          .__cartoskyGlobe.setEnabled(true);
      });
      await expect
        .poll(async () => (await readGlobeStats(page)).lastGeometryMode, { timeout: 20_000 })
        .toBe('globe');

      const coherence = await readCoherence(page);
      expect(coherence.projectionMismatch, `${id}: tripwire across the projection flip`).toBe(0);
      expect(coherence.drewMismatched, `${id}: coherence across the projection flip`).toBe(0);

      // eslint-disable-next-line no-console
      console.log(
        `[globe-ab] ${id} globe median=${globeArm.median}ms p95=${globeArm.p95}ms | ` +
          `mercator median=${mercatorArm.median}ms p95=${mercatorArm.p95}ms | ` +
          `indices=${statsOnGlobe.lastIndexCount}`,
      );
      // Recorded, not gated: SwiftShader median noise was measured at +/-7 ms
      // in the spike, so a hard threshold here would be flaky. The numbers land
      // in render-golden-baseline.timing.json for the report.
    });
  }

  // ── Perf attribution: is the mesh the cost driver? ───────────────────────
  //
  // The A/B above compares projections, which on the regional fixture also
  // compares MapLibre's OWN globe pass against its mercator pass. This test
  // isolates our contribution instead: same page, same projection, same camera,
  // only the mesh density differs (the `globeMesh` dev override). The spike
  // found the frame fill-rate bound rather than vertex bound below ~50k
  // indices; this is the check that the finding survived productionization.
  //
  // 192x96 (109,440 indices) is in the sweep because it is the shipped default
  // — the limb-fringe fix raised it there — so the arm has to span the density
  // actually in production, not stop below it.
  test('globe mesh density is not the frame-time driver', async ({ page }) => {
    test.setTimeout(240_000);
    const densities = ['8x4', '64x32', '128x64', '192x96'];
    const byDensity: Record<string, TimingEntry> = {};
    for (const density of densities) {
      await stubGoldenBaselineRoutes(page, 'globe-4326-world');
      await page.goto(`${viewerUrlForCase('globe-4326-world')}&globeMesh=${density}`);
      await waitForGoldenReady(page, `globe-mesh-${density}`);
      await repaint(page);
      const stats = await readGlobeStats(page);
      expect(stats.lastGeometryMode, `${density}: on the globe path`).toBe('globe');
      const entry = await measureRenderTimings(page, `globe-4326-world:mesh-${density}`, timingByCase);
      byDensity[density] = entry;
      // eslint-disable-next-line no-console
      console.log(`[globe-mesh] ${density} indices=${stats.lastIndexCount} median=${entry.median}ms`);
    }
    // Recorded, not gated: SwiftShader median noise was measured at +/-7 ms in
    // the spike, and 8x4 vs 128x64 is a 1000x change in index count. The
    // numbers land in render-golden-baseline.timing.json for the report.
    expect(Object.keys(byDensity)).toHaveLength(densities.length);
  });

  test.afterAll(async () => {
    writeTimingArtifact(timingByCase, GOLDEN_VIEWPORT, {
      globe_note:
        'Cases prefixed `globe-` are MapLibre globe projection. The `:globe` / `:mercator` ' +
        'pairs are a same-session A/B on one map instance, flipped through ' +
        'window.__cartoskyGlobe.setEnabled — only the projection differs.',
    });
  });
});
