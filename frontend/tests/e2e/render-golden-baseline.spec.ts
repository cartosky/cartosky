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
import fs from 'node:fs';
import path from 'node:path';

import { test, expect, type Locator, type Page } from '@playwright/test';

import {
  GOLDEN_CASE_IDS,
  GOLDEN_VIEWPORT,
  GFS_FRAME_HOURS,
  stubGoldenBaselineRoutes,
  viewerUrlForCase,
  type GoldenCaseId,
} from './render-golden-baseline.fixtures';

/** Per-pixel channel tolerance for in-page diffs (5/255 ≈ 0.02). */
const CHANNEL_TOLERANCE = 5;
/** Fraction of pixels allowed to exceed CHANNEL_TOLERANCE. */
const MAX_DIFF_PIXEL_RATIO = 0.0005;

// ESM spec module: derive the spec's own directory from import.meta.url rather
// than __dirname (absent under Playwright's ESM loader here).
const SPEC_DIR = path.dirname(new URL(import.meta.url).pathname);
const SNAPSHOT_DIR = path.join(SPEC_DIR, 'render-golden-baseline.spec.ts-snapshots');
const TIMING_ARTIFACT = path.join(SPEC_DIR, 'render-golden-baseline.timing.json');

/** Measured render() samples per still case; written out by the last test. */
const timingByCase: Record<string, { samples: number[]; median: number; p95: number }> = {};

type DiffReport = {
  width: number;
  height: number;
  totalPixels: number;
  diffPixels: number;
  diffRatio: number;
  maxChannelDelta: number;
};

/**
 * Decode two PNG sources (data: or blob: URLs) in the page and count pixels
 * whose max channel delta exceeds `tolerance`. Runs in-page so the real
 * chromium PNG decoder is used for both sides — the same decode path the app
 * itself would take, and no Node image dependency.
 */
async function diffPngs(
  page: Page,
  actualSource: string,
  expectedSource: string,
  tolerance = CHANNEL_TOLERANCE,
): Promise<DiffReport> {
  return page.evaluate(
    async (args) => {
      const toImageData = async (source: string) => {
        const response = await fetch(source);
        const bitmap = await createImageBitmap(await response.blob());
        const canvas = document.createElement('canvas');
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
        ctx.drawImage(bitmap, 0, 0);
        bitmap.close();
        return ctx.getImageData(0, 0, canvas.width, canvas.height);
      };
      const actual = await toImageData(args.actualSource);
      const expected = await toImageData(args.expectedSource);
      if (actual.width !== expected.width || actual.height !== expected.height) {
        return {
          width: actual.width,
          height: actual.height,
          totalPixels: actual.width * actual.height,
          diffPixels: Number.MAX_SAFE_INTEGER,
          diffRatio: 1,
          maxChannelDelta: 255,
        };
      }
      let diffPixels = 0;
      let maxChannelDelta = 0;
      const a = actual.data;
      const b = expected.data;
      for (let offset = 0; offset < a.length; offset += 4) {
        const d = Math.max(
          Math.abs(a[offset] - b[offset]),
          Math.abs(a[offset + 1] - b[offset + 1]),
          Math.abs(a[offset + 2] - b[offset + 2]),
          Math.abs(a[offset + 3] - b[offset + 3]),
        );
        if (d > maxChannelDelta) maxChannelDelta = d;
        if (d > args.tolerance) diffPixels += 1;
      }
      const totalPixels = actual.width * actual.height;
      return {
        width: actual.width,
        height: actual.height,
        totalPixels,
        diffPixels,
        diffRatio: totalPixels === 0 ? 1 : diffPixels / totalPixels,
        maxChannelDelta,
      };
    },
    { actualSource, expectedSource, tolerance },
  );
}

/** Read a stored golden PNG back as a data URL usable inside the page. */
function goldenAsDataUrl(name: string): string | null {
  const file = path.join(SNAPSHOT_DIR, name);
  if (!fs.existsSync(file)) {
    return null;
  }
  return `data:image/png;base64,${fs.readFileSync(file).toString('base64')}`;
}

function writeGolden(name: string, dataUrl: string) {
  fs.mkdirSync(SNAPSHOT_DIR, { recursive: true });
  const base64 = dataUrl.replace(/^data:image\/png;base64,/, '');
  fs.writeFileSync(path.join(SNAPSHOT_DIR, name), Buffer.from(base64, 'base64'));
}

/**
 * Compare a PNG data URL against a stored golden, writing the golden on first
 * run (same "first run creates, later runs verify" contract as
 * toHaveScreenshot). Returns the diff report, or null when the golden was just
 * created.
 */
async function expectMatchesGolden(
  page: Page,
  name: string,
  dataUrl: string,
): Promise<DiffReport | null> {
  const expected = goldenAsDataUrl(name);
  if (!expected) {
    writeGolden(name, dataUrl);
    // eslint-disable-next-line no-console
    console.log(`[golden-write] ${name}`);
    return null;
  }
  const report = await diffPngs(page, dataUrl, expected);
  // eslint-disable-next-line no-console
  console.log(
    `[golden-diff] ${name} ${report.width}x${report.height} ` +
      `diffPixels=${report.diffPixels}/${report.totalPixels} ` +
      `ratio=${report.diffRatio.toExponential(2)} maxChannelDelta=${report.maxChannelDelta}`,
  );
  if (report.diffRatio > MAX_DIFF_PIXEL_RATIO) {
    // Persist + attach the actual so a failure is diagnosable the same way a
    // toHaveScreenshot failure is.
    const actualPath = test.info().outputPath(name.replace(/\.png$/, '-actual.png'));
    fs.writeFileSync(
      actualPath,
      Buffer.from(dataUrl.replace(/^data:image\/png;base64,/, ''), 'base64'),
    );
    await test.info().attach(name, { path: actualPath, contentType: 'image/png' });
  }
  expect(
    report.diffPixels,
    `${name}: dimensions must match the golden exactly`,
  ).toBeLessThan(Number.MAX_SAFE_INTEGER);
  expect(report.diffRatio, `${name}: differing-pixel ratio`).toBeLessThanOrEqual(
    MAX_DIFF_PIXEL_RATIO,
  );
  return report;
}

/** The MapLibre drawing surface — map content only, no viewer chrome. */
function mapCanvas(page: Page): Locator {
  return page
    .locator('div[role="img"][aria-label="Weather map"] canvas.maplibregl-canvas')
    .first();
}

/**
 * The MapLibre canvas fills the map pane, and the viewer's chrome (top bar,
 * rail, timeline, legend chip, build pill) is DOM painted OVER that same box —
 * so a plain element screenshot of the canvas still composites the chrome into
 * the image and would let a chrome change fail the map-content gate.
 *
 * `visibility` is inherited and a more specific !important rule wins, so
 * hiding everything and re-showing only the canvas leaves exactly the WebGL
 * surface painted, with no layout change (visibility never reflows, so the map
 * is not resized and the render is untouched). Applied only immediately before
 * the server-side screenshot.
 */
async function hideChromeOverCanvas(page: Page) {
  await page.addStyleTag({
    content: `
      *, *::before, *::after { visibility: hidden !important; }
      canvas.maplibregl-canvas { visibility: visible !important; }
    `,
  });
}

async function captureLiveCanvas(page: Page): Promise<string> {
  const dataUrl = await page.evaluate(async () => {
    const w = window as typeof window & {
      __cartoskyViewerCapture?: () => Promise<string | null>;
    };
    return (await w.__cartoskyViewerCapture?.()) ?? null;
  });
  expect(dataUrl, 'window.__cartoskyViewerCapture returned a PNG').toBeTruthy();
  return dataUrl!;
}

/**
 * Full readiness before any golden is taken.
 *
 * The gate is the app's OWN composite readiness signal, html[data-viewer-ready]
 * (App.tsx maybeSignalViewerReady — the same latch the headless screenshot
 * service waits on). It requires all three of grid_frame_ready, map_idle AND
 * city_labels_ready.
 *
 * Waiting on grid_frame_ready alone — as the colormap spec does, because it only
 * samples individual cells — is NOT enough here and was measured to be the one
 * real determinism failure in this suite: the MapLibre city-label layer resolves
 * label collisions in glyph-arrival order, so capturing before
 * city_labels_ready lands in a *stable but run-dependent* label layout. That
 * showed up as an intermittent 55,056/649,600 (8.5%) cross-run diff with
 * maxChannelDelta 202 — dark label chips over the warm ramp. Note the two-
 * capture idle gate below could not catch it: both captures agreed, the settled
 * state itself differed between runs.
 *
 * After the app's latch: the initial scrim gone, the capture hook installed, and
 * two consecutive live captures agreeing within tolerance.
 */
async function waitForGoldenReady(page: Page, id: GoldenCaseId) {
  await expect
    .poll(
      () =>
        page.evaluate(() => document.documentElement.getAttribute('data-viewer-ready')),
      { timeout: 60_000, message: `${id}: html[data-viewer-ready] (grid + idle + city labels)` },
    )
    .toBe('1');
  await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 20_000 });
  await expect(mapCanvas(page)).toBeVisible();
  await expect
    .poll(
      () =>
        page.evaluate(
          () => typeof (window as typeof window & { __cartoskyViewerCapture?: unknown }).__cartoskyViewerCapture,
        ),
      { timeout: 20_000, message: `${id}: capture hook installed` },
    )
    .toBe('function');

  // Idle gate: repaint until two back-to-back captures agree within tolerance.
  await expect
    .poll(
      async () => {
        const first = await captureLiveCanvas(page);
        const second = await captureLiveCanvas(page);
        const report = await diffPngs(page, second, first);
        return report.diffRatio;
      },
      { timeout: 30_000, intervals: [500], message: `${id}: map idle` },
    )
    .toBeLessThanOrEqual(MAX_DIFF_PIXEL_RATIO);
}

async function openCase(page: Page, id: GoldenCaseId) {
  await stubGoldenBaselineRoutes(page, id);
  await page.goto(viewerUrlForCase(id));
  await waitForGoldenReady(page, id);
}

/**
 * Timing method (stated per the task): each sample wraps ONE full repaint
 * cycle driven through the app's own GIF frame driver —
 *   performance.now() -> map.triggerRepaint() -> map.once("render") ->
 *   drawImage(canvas) -> performance.now()
 * (src/components/map-canvas.tsx captureCanvasSnapshot). That is the smallest
 * render-completion signal the app exposes without touching src/, so it is an
 * UPPER BOUND on the grid-webgl render pass: it also includes the
 * requestAnimationFrame scheduling latency and one full-size canvas copy. Both
 * addends are constant across the renderer change, and measured cycles land
 * around 130 ms — roughly 8x the ~16.7 ms frame interval — so the SwiftShader
 * render pass dominates and the pre/post comparison on the SAME machine is
 * meaningful. A regression smaller than a few ms should still not be read into
 * this probe.
 * 3 warm-up cycles are discarded, then 20 are measured.
 */
async function measureRenderTimings(page: Page, id: GoldenCaseId) {
  const samples = await page.evaluate(async () => {
    const w = window as typeof window & {
      __cartoskyGifDriver?: {
        captureFrame: (maxWidth?: number, expectGridHour?: number | null) => Promise<HTMLCanvasElement | null>;
      };
    };
    const driver = w.__cartoskyGifDriver;
    if (!driver) {
      return null;
    }
    const measured: number[] = [];
    for (let i = 0; i < 23; i += 1) {
      const started = performance.now();
      const canvas = await driver.captureFrame();
      const elapsed = performance.now() - started;
      if (!canvas) {
        continue;
      }
      if (i >= 3) {
        measured.push(elapsed);
      }
    }
    return measured;
  });

  expect(samples, `${id}: __cartoskyGifDriver present (dev build) for timing`).not.toBeNull();
  expect(samples!.length, `${id}: measured render samples`).toBeGreaterThanOrEqual(15);
  const sorted = [...samples!].sort((a, b) => a - b);
  const quantile = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
  const entry = {
    samples: sorted.map((value) => Number(value.toFixed(3))),
    median: Number(quantile(0.5).toFixed(3)),
    p95: Number(quantile(0.95).toFixed(3)),
  };
  timingByCase[id] = entry;
  // eslint-disable-next-line no-console
  console.log(`[render-timing] ${id} median=${entry.median}ms p95=${entry.p95}ms n=${entry.samples.length}`);
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
      await measureRenderTimings(page, id);

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
    if (Object.keys(timingByCase).length === 0) {
      return;
    }
    const existing = fs.existsSync(TIMING_ARTIFACT)
      ? (JSON.parse(fs.readFileSync(TIMING_ARTIFACT, 'utf8')) as Record<string, unknown>)
      : {};
    fs.writeFileSync(
      TIMING_ARTIFACT,
      `${JSON.stringify(
        {
          ...existing,
          generated_at_note:
            'Wall time per full repaint cycle (triggerRepaint -> render event -> canvas copy) ' +
            'through window.__cartoskyGifDriver.captureFrame(). Includes rAF scheduling latency ' +
            'and one full-size canvas copy, both constant across the renderer change; at ~130 ms ' +
            'per cycle the SwiftShader render pass dominates those addends by ~8x. Upper bound on ' +
            'the grid-webgl render pass; same-machine pre/post comparison only. 3 warm-up cycles ' +
            'discarded, 20 measured, chromium --use-angle=swiftshader.',
          platform: process.platform,
          viewport: GOLDEN_VIEWPORT,
          samples_per_case: 20,
          cases: timingByCase,
        },
        null,
        2,
      )}\n`,
    );
    // eslint-disable-next-line no-console
    console.log(`[render-timing] wrote ${TIMING_ARTIFACT}`);
  });
});
