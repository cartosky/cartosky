/**
 * ════════════════════════════════════════════════════════════════════════════
 * STEP 1 — RENDER GOLDEN BASELINE (pre-change)
 * ════════════════════════════════════════════════════════════════════════════
 *
 * This suite freezes REAL rendered map content so an upcoming per-fragment
 * reprojection branch in src/lib/grid-webgl.ts can be shown to be
 * pixel-equivalent. It touches nothing in src/.
 *
 * Three still cases, each on a different shader path (see
 * render-golden-baseline.fixtures.ts for how each manifest is derived from
 * backend evidence):
 *   A. gfs/tmp2m         — uint16 continuous decode, sampleBilinear()
 *   B. hrrr/radar_ptype  — u_radarPtypePacked = 1, sampleRadarPtypePacked()
 *   C. mrms/reflectivity — uint8 + sparse nodata, u_edgeFade = 1
 *
 * Each still case is captured through BOTH paths:
 *   1. SERVER-SIDE: Playwright element screenshot of the MapLibre canvas only
 *      (not the page) via toHaveScreenshot(), so Playwright owns the goldens.
 *      Screenshotting the canvas element — never the page — keeps viewer chrome
 *      (rail, top bar, timeline, legend chip) out of the map-content gate.
 *   2. LIVE-CANVAS: the app's own capture hook, window.__cartoskyViewerCapture
 *      (the share-overhaul repaint hook in src/components/map-canvas.tsx:
 *      triggerRepaint -> once("render") -> canvas.toDataURL). Compared against
 *      a stored PNG golden with an in-page pixel diff.
 *
 * Plus a GIF case on the GFS fixture: the real Share > GIF > Generate path
 * (useGifExport + the gifenc worker) over the 3 fixture frames, with the
 * encoded GIF decoded in-page by WebCodecs ImageDecoder and each frame
 * pixel-diffed against its own golden. No new npm dependencies.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THRESHOLDS — why these numbers
 * ────────────────────────────────────────────────────────────────────────────
 * Byte equality is explicitly NOT used: PNG encoders and the SwiftShader
 * rasterizer are free to differ in the last bit without any visible change.
 * Everything below is a pixel diff with an explicit tolerance.
 *
 * Noise floor (measured, this machine, chromium/SwiftShader): the suite runs a
 * DETERMINISM PROBE test that captures the same case twice back-to-back
 * through the live-canvas hook and diffs them at tolerance 0 (exact). Observed:
 * 0 differing pixels out of 649,600 (928x700), maxChannelDelta 0 — see the
 * console line "[determinism-probe]". Cross-RUN agreement is the same: on the
 * verification run every stored golden reported diffPixels=0 (3 live canvases
 * at 928x700 and 3 GIF frames at 720x543), and that held across three
 * consecutive full runs. SwiftShader is deterministic
 * software rasterization with no GPU driver variance, so a hard zero is the
 * expected floor rather than luck — which is exactly why the budgets below can
 * be set this tight.
 *
 * Content-shift magnitude: the fixture field is
 *   0.35*u + 0.45*(1-v) + 0.14*sin(6*pi*v) + cones
 * i.e. luminance is a steep function of ROW. A one-texel v shift on an 88x64
 * grid stretched over 700 px is a ~11 px image shift and changes essentially
 * every pixel in the frame — on the order of 10^5 differing pixels, five to six
 * orders of magnitude above the thresholds below.
 *
 * Chosen values:
 *   - toHaveScreenshot: threshold 0.02 (per-pixel YIQ distance),
 *     maxDiffPixelRatio 0.0005. At 1000x700 that allows 350 pixels to differ —
 *     enough to absorb an antialiasing seam along one edge of the map or a
 *     handful of jittered label pixels, while a row shift exceeds it ~1000x.
 *   - In-page diffs (live canvas, GIF frames): a pixel counts as differing
 *     when max |dR|,|dG|,|dB|,|dA| > CHANNEL_TOLERANCE (5/255 ≈ 0.02, the
 *     absolute-channel equivalent of the YIQ 0.02 above), and the test fails
 *     when the differing ratio exceeds MAX_DIFF_PIXEL_RATIO (0.0005) — the same
 *     0.05% budget as the server-side path, so both paths gate equally hard.
 *   - Dimensions must match exactly; a size change is a hard failure, never
 *     absorbed by the ratio.
 *
 * Determinism pins (all inherited from the existing viewer specs, which is why
 * the stub list is copied rather than reinvented):
 *   - ?screenshot=1 (headless render mode: registers the capture hook, silences
 *     RUM, and writes the readiness gate log the waits below poll)
 *   - localStorage csky_viewer_tour_v1 = "completed" (no tour spotlight)
 *   - Carto basemap raster tiles served as a 1x1 transparent PNG and boundary
 *     MVTs served empty, so no third-party imagery reaches the canvas. (Note:
 *     404-ing the Carto tiles instead is NOT viable — MapLibre's raster source
 *     never settles and the app's grid_frame_ready gate never fires.)
 *   - remaining live-network dependency, stated honestly: MapLibre glyph PBFs
 *     (tiles.stadiamaps.com, map-canvas.tsx style `glyphs`) still load from the
 *     network, because the city labels they draw ARE canvas map content. Their
 *     values are sampled from the fixture grid, so they are a useful v-shift
 *     detector; but a glyph-fetch failure during the post-change run would show
 *     up as a large diff rather than a subtle one.
 *   - /api/v4/sample/batch 404 (no city value labels)
 *   - manifest last_updated frozen (no wall-clock text anywhere downstream)
 *   - capture only after html[data-viewer-ready]="1" — the app's own composite
 *     latch (grid frame ready AND map idle AND city labels ready). This one was
 *     load-bearing: see waitForGoldenReady for the measured 8.5% intermittent
 *     diff that waiting on grid_frame_ready alone allowed through.
 *   - chromium-only, desktop-only, viewport pinned to 1000x700, camera pinned
 *     to lat/lon/zoom in the URL, zoom 6 so the multi-LOD cases deterministically
 *     resolve to level 0
 *
 * Nothing is committed: the Playwright snapshot dir and this spec/fixture pair
 * are allowlisted in the root .gitignore only so the paths are visible, and the
 * task explicitly ends without a commit.
 */
import { test, expect, type Page } from '@playwright/test';

import {
  GOLDEN_CASE_IDS,
  GOLDEN_VIEWPORT,
  GFS_FRAME_HOURS,
  GLOBAL_SEAM_ZOOM_COLUMN_PX,
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
  openCase,
  writeTimingArtifact,
  type TimingEntry,
} from './render-golden-harness';

/** Measured render() samples per still case; written out by the last test. */
const timingByCase: Record<string, TimingEntry> = {};

type SeamReport = {
  width: number;
  height: number;
  seamX: number;
  /** Alpha extremes across the strip that spans the seam. */
  minAlpha: number;
  maxAlpha: number;
  /** Largest adjacent-pixel luminance step in the 4 px straddling the seam. */
  seamStepMax: number;
  /** Largest adjacent-pixel luminance step everywhere ELSE in the strip. */
  controlStepMax: number;
  /** Alpha well to the west / east of the seam: data on BOTH sides. */
  westAlpha: number;
  eastAlpha: number;
  /** Alpha at the extreme left / right of the canvas. */
  leftEdgeAlpha: number;
  rightEdgeAlpha: number;
  /** Best horizontal repeat period found, and its mean per-channel error. */
  copyPeriodPx: number;
  copyPeriodError: number;
  /** Runner-up period, so an exact match cannot be read as a flat image. */
  nextBestPeriodError: number;
  copyOverlapPx: number;
};

/**
 * Pixel probe for the antimeridian case, run in-page on the app's own
 * live-canvas PNG.
 *
 * Geometry (see GLOBAL_SEAM_VIEW): the camera sits exactly on lon 180, so the
 * seam is the canvas's vertical centre line. The world width in pixels is
 * MapLibre's to choose, so it is MEASURED here: the smallest horizontal shift
 * (searched from 300 px up, which excludes the field's own 120°-of-longitude
 * internal period) at which the image reproduces itself is one world copy.
 */
async function probeSeam(
  page: Page,
  source: string,
): Promise<SeamReport> {
  return page.evaluate(
    async (args) => {
      const response = await fetch(args.source);
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

      const seamX = Math.round(width / 2);
      // Rows around the equator (mercator centre), where the seam-straddling
      // ridge is strongest and the frame is certainly covered by data.
      const rows: number[] = [];
      for (let y = Math.round(height / 2) - 40; y <= Math.round(height / 2) + 40; y += 8) {
        rows.push(y);
      }
      const half = 60;

      let minAlpha = 255;
      let maxAlpha = 0;
      let seamStepMax = 0;
      let controlStepMax = 0;
      let westAlpha = 255;
      let eastAlpha = 255;
      let leftEdgeAlpha = 255;
      let rightEdgeAlpha = 255;
      for (const y of rows) {
        westAlpha = Math.min(westAlpha, image[at(seamX - half, y) + 3]);
        eastAlpha = Math.min(eastAlpha, image[at(seamX + half, y) + 3]);
        leftEdgeAlpha = Math.min(leftEdgeAlpha, image[at(0, y) + 3]);
        rightEdgeAlpha = Math.min(rightEdgeAlpha, image[at(width - 1, y) + 3]);
        for (let x = seamX - half; x <= seamX + half; x += 1) {
          const alpha = image[at(x, y) + 3];
          minAlpha = Math.min(minAlpha, alpha);
          maxAlpha = Math.max(maxAlpha, alpha);
          if (x === seamX + half) {
            continue;
          }
          const step = Math.abs(luma(x + 1, y) - luma(x, y));
          // A seam artefact is 1-2 px wide and can land either side of the
          // exact centre line, so the seam band is deliberately generous.
          if (x >= seamX - 2 && x <= seamX + 1) {
            seamStepMax = Math.max(seamStepMax, step);
          } else {
            controlStepMax = Math.max(controlStepMax, step);
          }
        }
      }

      // World-copy period: the shift at which the whole image reproduces
      // itself. Only a replicated world does that; a single quad leaves
      // transparent canvas instead.
      const scanRows: number[] = [];
      for (let y = Math.round(height / 2) - 150; y <= Math.round(height / 2) + 150; y += 10) {
        scanRows.push(y);
      }
      let copyPeriodPx = 0;
      let copyPeriodError = Number.POSITIVE_INFINITY;
      let nextBestPeriodError = Number.POSITIVE_INFINITY;
      for (let period = 300; period <= width - 150; period += 1) {
        let total = 0;
        let count = 0;
        for (const y of scanRows) {
          for (let x = 0; x + period < width; x += 3) {
            const a = at(x, y);
            const b = at(x + period, y);
            total +=
              Math.abs(image[a] - image[b])
              + Math.abs(image[a + 1] - image[b + 1])
              + Math.abs(image[a + 2] - image[b + 2])
              + Math.abs(image[a + 3] - image[b + 3]);
            count += 4;
          }
        }
        const error = count === 0 ? Number.POSITIVE_INFINITY : total / count;
        if (error < copyPeriodError) {
          copyPeriodError = error;
          copyPeriodPx = period;
        }
      }
      // Runner-up outside a +-3 px neighbourhood of the winner, so a uniformly
      // flat (and therefore trivially self-similar) image cannot pass.
      for (let period = 300; period <= width - 150; period += 1) {
        if (Math.abs(period - copyPeriodPx) <= 3) {
          continue;
        }
        let total = 0;
        let count = 0;
        for (const y of scanRows) {
          for (let x = 0; x + period < width; x += 3) {
            const a = at(x, y);
            const b = at(x + period, y);
            total +=
              Math.abs(image[a] - image[b])
              + Math.abs(image[a + 1] - image[b + 1])
              + Math.abs(image[a + 2] - image[b + 2])
              + Math.abs(image[a + 3] - image[b + 3]);
            count += 4;
          }
        }
        const error = count === 0 ? Number.POSITIVE_INFINITY : total / count;
        nextBestPeriodError = Math.min(nextBestPeriodError, error);
      }

      return {
        width,
        height,
        seamX,
        minAlpha,
        maxAlpha,
        seamStepMax,
        controlStepMax,
        westAlpha,
        eastAlpha,
        leftEdgeAlpha,
        rightEdgeAlpha,
        copyPeriodPx,
        copyPeriodError,
        nextBestPeriodError,
        copyOverlapPx: width - copyPeriodPx,
      };
    },
    { source },
  );
}

type SeamZoomReport = {
  width: number;
  height: number;
  seamX: number;
  columnPx: number;
  minAlpha: number;
  maxAlpha: number;
  /** Max adjacent-pixel step in the 6 px straddling the seam. */
  seamStepMax: number;
  /** Max adjacent-pixel step everywhere else within +-140 px. */
  controlStepMax: number;
  /**
   * Max adjacent-pixel step inside the column EAST of the seam — the exact
   * mirror of the wrapped column, at the same colormap position and the same
   * fixture gradient, but needing no wrap. The like-for-like reference.
   */
  mirrorStepMax: number;
  /** Luminance change across the column WEST of the seam (the wrapped pair). */
  westBandDelta: number;
  /** Luminance change across the column EAST of the seam (never needs wrap). */
  eastBandDelta: number;
};

/**
 * Zoomed seam probe. Builds a row-averaged 1-D luminance profile (rows around
 * the equator, where the fixture's latitude term is flat) and measures the two
 * things that separate a true cross-seam blend from a clamp smear:
 *
 *  - seamStepMax vs controlStepMax. The fixture puts its steepest
 *    adjacent-column gradient exactly on the seam AND on every other zero
 *    crossing, so a correct render has the same per-pixel step at both. A
 *    clamped render collapses one whole column into a single pixel.
 *  - westBandDelta vs eastBandDelta. West of the seam is the column pair
 *    1439|0, which ONLY the wrap can interpolate; east of it is 0|1, which
 *    needs no wrap. The fixture makes those two column steps equal in
 *    magnitude, so a correct render gives westBandDelta ~= eastBandDelta and a
 *    clamped one gives westBandDelta ~= 0. This is the positive assertion that
 *    the wrapping fetch actually executed.
 */
async function probeSeamZoom(
  page: Page,
  source: string,
  columnPx: number,
): Promise<SeamZoomReport> {
  return page.evaluate(
    async (args) => {
      const response = await fetch(args.source);
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

      const rows: number[] = [];
      for (let y = Math.round(height / 2) - 40; y <= Math.round(height / 2) + 40; y += 4) {
        rows.push(y);
      }
      // Row-averaged luminance profile: kills per-pixel dither without
      // smoothing along x, which is the axis under test.
      const profile = new Float64Array(width);
      let minAlpha = 255;
      let maxAlpha = 0;
      for (let x = 0; x < width; x += 1) {
        let sum = 0;
        for (const y of rows) {
          const o = at(x, y);
          sum += 0.299 * image[o] + 0.587 * image[o + 1] + 0.114 * image[o + 2];
          minAlpha = Math.min(minAlpha, image[o + 3]);
          maxAlpha = Math.max(maxAlpha, image[o + 3]);
        }
        profile[x] = sum / rows.length;
      }

      const seamX = Math.round(width / 2);
      const column = args.columnPx;
      let seamStepMax = 0;
      let controlStepMax = 0;
      for (let x = seamX - 140; x <= seamX + 139; x += 1) {
        if (x < 0 || x + 1 >= width) {
          continue;
        }
        const step = Math.abs(profile[x + 1] - profile[x]);
        // The seam band is deliberately generous: a 1-2 px artefact can land
        // either side of the exact centre line.
        if (x >= seamX - 3 && x <= seamX + 2) {
          seamStepMax = Math.max(seamStepMax, step);
        } else {
          controlStepMax = Math.max(controlStepMax, step);
        }
      }

      let mirrorStepMax = 0;
      for (let x = seamX + 3; x < Math.round(seamX + column) && x + 1 < width; x += 1) {
        mirrorStepMax = Math.max(mirrorStepMax, Math.abs(profile[x + 1] - profile[x]));
      }

      // One inset pixel at each end so the measurement never straddles the
      // neighbouring column's own ramp.
      const inset = 2;
      const westBandDelta = Math.abs(
        profile[seamX - inset] - profile[Math.round(seamX - column) + inset],
      );
      const eastBandDelta = Math.abs(
        profile[Math.round(seamX + column) - inset] - profile[seamX + inset],
      );

      return {
        width,
        height,
        seamX,
        columnPx: column,
        minAlpha,
        maxAlpha,
        seamStepMax,
        controlStepMax,
        mirrorStepMax,
        westBandDelta,
        eastBandDelta,
      };
    },
    { source, columnPx },
  );
}

test.describe('Render golden baseline (Step 1, pre-change)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Pinned Chromium/SwiftShader baseline.');

  // Serial, deliberately: parallel workers sharing one dev server and one CPU
  // would contaminate the render-timing samples this suite records, and the
  // timing artifact is aggregated in-process by afterAll.
  test.describe.configure({ mode: 'serial' });

  // Pinned environment. timezoneId/locale matter because the GIF exporter bakes
  // the frame's valid time into each frame's overlay ("… 9:00 AM 7/29/26"), so
  // without them the GIF goldens would only reproduce in the machine's local
  // timezone. colorScheme is pinned explicitly even though it matches
  // Playwright's default — the viewer's basemap variant is theme-sensitive.
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

  // ── Noise-floor probe ───────────────────────────────────────────────────
  test('determinism probe: two back-to-back captures of the same case are identical', async ({ page }) => {
    await openCase(page, 'gfs-tmp2m');
    const first = await captureLiveCanvas(page);
    const second = await captureLiveCanvas(page);
    const report = await diffPngs(page, second, first, 0);
    // eslint-disable-next-line no-console
    console.log(
      `[determinism-probe] gfs-tmp2m ${report.width}x${report.height} ` +
        `exactDiffPixels=${report.diffPixels}/${report.totalPixels} ` +
        `maxChannelDelta=${report.maxChannelDelta}`,
    );
    // Reported with tolerance 0 (exact) to establish the floor the thresholds
    // sit above. Allowed to be small but not structural.
    expect(report.diffRatio).toBeLessThanOrEqual(MAX_DIFF_PIXEL_RATIO);
  });

  // ── Still cases: both capture paths + timing ─────────────────────────────
  for (const id of GOLDEN_CASE_IDS) {
    test(`${id}: map canvas golden (server-side screenshot + live-canvas hook)`, async ({ page }) => {
      // Readiness + idle gate + 20 timed SwiftShader repaints (~140 ms each).
      test.setTimeout(120_000);
      await openCase(page, id);

      // 1. LIVE-CANVAS PATH — the app's own repaint-hook PNG. Taken first,
      //    while the DOM is untouched.
      const liveDataUrl = await captureLiveCanvas(page);
      await expectMatchesGolden(page, `${id}-live-canvas.png`, liveDataUrl);

      // 2. Frame timing baseline for the post-change comparison (also before
      //    any style injection).
      await measureRenderTimings(page, id, timingByCase);

      // 3. SERVER-SIDE PATH — Playwright owns this golden. Chrome hidden so the
      //    canvas element screenshot is map content only.
      await hideChromeOverCanvas(page);
      await expect(mapCanvas(page)).toHaveScreenshot(`${id}-map-canvas.png`, {
        threshold: 0.02,
        maxDiffPixelRatio: MAX_DIFF_PIXEL_RATIO,
        animations: 'disabled',
        caret: 'hide',
      });

      // Cheap structural assertion so a silently-blank canvas can never pass
      // as a golden: the rendered map must contain more than one color.
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

  // ── Antimeridian: seam continuity + world copies + hover normalization ──
  test('gfs-global-seam: data is continuous across the antimeridian and repeats per world copy', async ({ page }) => {
    test.setTimeout(120_000);
    await openCase(page, 'gfs-global-seam');

    const liveDataUrl = await captureLiveCanvas(page);
    const report = await probeSeam(page, liveDataUrl);
    // eslint-disable-next-line no-console
    console.log(
      `[seam-probe] ${report.width}x${report.height} seamX=${report.seamX} ` +
        `alpha=[${report.minAlpha},${report.maxAlpha}] west=${report.westAlpha} east=${report.eastAlpha} ` +
        `edges=[${report.leftEdgeAlpha},${report.rightEdgeAlpha}] ` +
        `seamStepMax=${report.seamStepMax.toFixed(2)} controlStepMax=${report.controlStepMax.toFixed(2)} ` +
        `copyPeriod=${report.copyPeriodPx}px err=${report.copyPeriodError.toFixed(3)} ` +
        `nextBestErr=${report.nextBestPeriodError.toFixed(3)} overlap=${report.copyOverlapPx}px`,
    );

    // 1. Data on BOTH sides of the seam. Before Phase 3 the layer stopped dead
    //    at the world edge, so one of these was fully transparent.
    expect(report.westAlpha, 'weather data west of the antimeridian').toBeGreaterThan(200);
    expect(report.eastAlpha, 'weather data east of the antimeridian').toBeGreaterThan(200);

    // 2. No gap and no dark strip AT the seam. A missing column reads as an
    //    alpha dip; a double-drawn half-cell overhang reads as a luminance
    //    step that stands out against the field's own gradient.
    expect(report.maxAlpha, 'seam strip carries data').toBeGreaterThan(200);
    expect(
      report.minAlpha,
      'no alpha dip anywhere in the 121 px strip straddling the seam',
    ).toBeGreaterThanOrEqual(report.maxAlpha - 2);
    expect(
      report.seamStepMax,
      'luminance step at the seam is not an outlier against the field gradient',
    ).toBeLessThanOrEqual(Math.max(6, 3 * report.controlStepMax));

    // 3. World-copy continuity. One world is narrower than the map pane here,
    //    so a single-quad render (the pre-Phase-3 behaviour) leaves the outer
    //    ~230 px of canvas transparent and has no repeat period at all.
    expect(report.leftEdgeAlpha, 'data reaches the west canvas edge').toBeGreaterThan(200);
    expect(report.rightEdgeAlpha, 'data reaches the east canvas edge').toBeGreaterThan(200);
    expect(
      report.copyPeriodError,
      `image reproduces itself exactly at ${report.copyPeriodPx} px — one world copy`,
    ).toBeLessThanOrEqual(1);
    expect(
      report.copyOverlapPx,
      'the repeat is verified over a meaningful overlap, not a sliver',
    ).toBeGreaterThanOrEqual(150);
    // Not a trivially self-similar (flat) image: the winner is EXACT (measured
    // 0.000 mean channel error) and every shift outside +-3 px of it is not
    // (measured 4.70). A flat or low-contrast frame would make both small.
    expect(
      report.nextBestPeriodError,
      'the repeat period is unique, so the match is a world copy and not flatness',
    ).toBeGreaterThan(2);

    // 4. Regression golden through the app's own capture hook, same contract as
    //    the other cases.
    await expectMatchesGolden(page, 'gfs-global-seam-live-canvas.png', liveDataUrl);
  });

  test('gfs-global-seam-zoom: the 1439|0 column pair is interpolated, not clamp-smeared', async ({ page }) => {
    test.setTimeout(120_000);
    await openCase(page, 'gfs-global-seam-zoom');

    const liveDataUrl = await captureLiveCanvas(page);
    const report = await probeSeamZoom(page, liveDataUrl, GLOBAL_SEAM_ZOOM_COLUMN_PX);
    // eslint-disable-next-line no-console
    console.log(
      `[seam-zoom-probe] ${report.width}x${report.height} seamX=${report.seamX} ` +
        `columnPx=${report.columnPx.toFixed(2)} alpha=[${report.minAlpha},${report.maxAlpha}] ` +
        `seamStepMax=${report.seamStepMax.toFixed(2)} mirrorStepMax=${report.mirrorStepMax.toFixed(2)} ` +
        `controlStepMax=${report.controlStepMax.toFixed(2)} ` +
        `westBandDelta=${report.westBandDelta.toFixed(2)} eastBandDelta=${report.eastBandDelta.toFixed(2)}`,
    );

    // Coverage first: the whole probed strip carries data at full alpha.
    expect(report.maxAlpha, 'seam strip carries data').toBeGreaterThan(200);
    expect(report.minAlpha, 'no alpha dip across the seam').toBeGreaterThanOrEqual(
      report.maxAlpha - 2,
    );

    // The east column (0|1) needs no wrap, so it is the control magnitude and
    // must be a real, large ramp — otherwise every ratio below is vacuous.
    expect(
      report.eastBandDelta,
      'the fixture really does put a steep gradient beside the seam',
    ).toBeGreaterThan(20);

    // THE wrap assertion. West of the seam is the 1439|0 pair. The fixture
    // makes that column step equal in magnitude to the 0|1 step east of it, so
    // a correct wrapping bilinear renders both ramps alike. Clamping flattens
    // the west band to the constant col-1439 value -> westBandDelta collapses.
    expect(
      report.westBandDelta,
      'the 1439|0 column ramps like its mirror, i.e. the wrapped fetch executed',
    ).toBeGreaterThanOrEqual(0.6 * report.eastBandDelta);

    // Continuity gate, derived from the fixture rather than eyeballed, and
    // measured against the seam's OWN MIRROR (the 0|1 column immediately east)
    // rather than a global max — same colormap position, same fixture
    // gradient, no wrap needed, so the two must agree up to sampling phase.
    // A clamped render dumps a whole column (~30 luminance, see westBandDelta)
    // into one pixel, i.e. ~7x the mirror step. Measured: 4.30 seam vs 4.30
    // mirror; the bound below sits at 7.5.
    expect(
      report.mirrorStepMax,
      'the mirror column really is a steep ramp, so the bound is not vacuous',
    ).toBeGreaterThan(2);
    expect(
      report.seamStepMax,
      'seam gradient matches its own mirror column, i.e. no clamp step',
    ).toBeLessThanOrEqual(1.5 * report.mirrorStepMax + 1);

    await expectMatchesGolden(page, 'gfs-global-seam-zoom-live-canvas.png', liveDataUrl);
  });

  test('gfs-global-seam: hovering a world copy samples a longitude inside the API bounds', async ({ page }) => {
    test.setTimeout(120_000);
    const sampleRequests: string[] = [];
    await openCase(page, 'gfs-global-seam', sampleRequests);

    const canvas = mapCanvas(page);
    const box = (await canvas.boundingBox())!;
    // Two probes: one just west of the seam (canonical longitudes), and one far
    // east of it — past the antimeridian, where MapLibre reports an UNWRAPPED
    // lngLat above +180 that the API rejects with a 422.
    const points = [
      { x: box.x + box.width / 2 - 120, y: box.y + box.height / 2 },
      { x: box.x + box.width - 40, y: box.y + box.height / 2 },
    ];
    for (const point of points) {
      await page.mouse.move(point.x, point.y);
      await page.waitForTimeout(400);
    }

    await expect
      .poll(() => sampleRequests.length, { timeout: 15_000, message: 'hover sample requests issued' })
      .toBeGreaterThanOrEqual(2);

    const lons = sampleRequests.map((url) => Number(new URL(url).searchParams.get('lon')));
    // eslint-disable-next-line no-console
    console.log(`[seam-hover] sampled lons: ${lons.join(', ')}`);
    for (const lon of lons) {
      expect(Number.isFinite(lon), `sample lon is numeric (${lon})`).toBe(true);
      expect(lon, 'sample lon respects the API contract lower bound').toBeGreaterThanOrEqual(-180);
      expect(lon, 'sample lon respects the API contract upper bound').toBeLessThanOrEqual(180);
    }
    // The stub itself fails closed on an out-of-range longitude (422, exactly
    // like the real Query bound), so an unnormalized hover cannot pass here
    // even if the assertions above were ever relaxed.
  });

  // ── GIF case: the real client export over the 3 GFS fixture frames ──────
  test('gfs-tmp2m: real GIF export produces exactly 3 frames, each pinned to a golden', async ({ page }) => {
    // Readiness + 3 stepped frame captures + palette quantization on
    // SwiftShader: well past the suite's 60 s default.
    test.setTimeout(240_000);
    await openCase(page, 'gfs-tmp2m');

    await page.getByRole('button', { name: 'Share', exact: true }).first().click();
    const dialog = page.getByRole('dialog', { name: 'Share' });
    await expect(dialog).toBeVisible({ timeout: 15_000 });
    await dialog.getByRole('tab', { name: 'GIF' }).click();
    await dialog.getByRole('button', { name: 'Generate GIF' }).click();

    const preview = dialog.getByRole('img', { name: 'Animated GIF preview' });
    await expect(preview).toBeVisible({ timeout: 60_000 });

    // Frame count from the app's own readout (gifFrameCount = frames written
    // into the gifenc worker).
    await expect(dialog.getByText(/\d+ frames · /)).toContainText(
      `${GFS_FRAME_HOURS.length} frames`,
    );

    // Decode the ENCODED GIF (the artifact the user downloads) in-page and
    // re-emit each frame as a PNG data URL for the same pixel diff.
    const decoded = await preview.evaluate(async (element) => {
      const source = (element as HTMLImageElement).src;
      const Decoder = (window as unknown as { ImageDecoder?: unknown }).ImageDecoder as
        | (new (init: { data: ArrayBuffer | Uint8Array; type: string }) => {
            completed: Promise<void>;
            tracks: { ready: Promise<void>; selectedTrack: { frameCount: number } | null };
            decode: (options: { frameIndex: number }) => Promise<{ image: VideoFrame }>;
          })
        | undefined;
      const buffer = await (await fetch(source)).arrayBuffer();
      if (!Decoder) {
        return { supported: false as const, byteLength: buffer.byteLength, frames: [] as string[] };
      }
      const decoder = new Decoder({ data: buffer, type: 'image/gif' });
      // tracks.ready must settle before selectedTrack (and therefore
      // frameCount) is populated; completed alone leaves it null.
      await decoder.tracks.ready;
      await decoder.completed;
      const frameCount = decoder.tracks.selectedTrack?.frameCount ?? 0;
      const frames: string[] = [];
      for (let index = 0; index < frameCount; index += 1) {
        const { image } = await decoder.decode({ frameIndex: index });
        const canvas = document.createElement('canvas');
        canvas.width = image.displayWidth;
        canvas.height = image.displayHeight;
        const ctx = canvas.getContext('2d')!;
        ctx.drawImage(image as unknown as CanvasImageSource, 0, 0);
        image.close();
        frames.push(canvas.toDataURL('image/png'));
      }
      return { supported: true as const, byteLength: buffer.byteLength, frames };
    });

    expect(decoded.byteLength, 'encoded GIF is non-trivial').toBeGreaterThan(1000);
    expect(
      decoded.supported,
      'WebCodecs ImageDecoder available in chromium for per-frame GIF pinning',
    ).toBe(true);
    // The decoded artifact itself carries exactly the 3 fixture frames.
    expect(decoded.frames.length, 'decoded GIF frame count').toBe(GFS_FRAME_HOURS.length);

    for (let index = 0; index < decoded.frames.length; index += 1) {
      await expectMatchesGolden(page, `gfs-tmp2m-gif-frame${index}.png`, decoded.frames[index]);
    }

    await page.keyboard.press('Escape');
  });

  // ── Timing artifact ─────────────────────────────────────────────────────
  test.afterAll(async () => {
    writeTimingArtifact(timingByCase, GOLDEN_VIEWPORT);
  });
});
