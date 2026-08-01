/**
 * Shared capture/diff harness for the render golden suites.
 *
 * Extracted from render-golden-baseline.spec.ts verbatim so that the globe
 * renderer goldens (render-golden-globe.spec.ts) are captured through exactly
 * the same paths and gated at exactly the same thresholds. Nothing here is new
 * behaviour — see the header of render-golden-baseline.spec.ts for the full
 * derivation of the numbers below and of the readiness gate.
 *
 * ── THRESHOLDS — why these numbers ──────────────────────────────────────────
 * Byte equality is explicitly NOT used: PNG encoders and the SwiftShader
 * rasterizer are free to differ in the last bit without any visible change.
 * Everything is a pixel diff with an explicit tolerance.
 *
 * Noise floor (measured, chromium/SwiftShader): each suite runs a DETERMINISM
 * PROBE that captures the same case twice back-to-back through the live-canvas
 * hook and diffs at tolerance 0. Observed 0 differing pixels, maxChannelDelta
 * 0 — SwiftShader is deterministic software rasterization with no GPU driver
 * variance, so a hard zero is the expected floor rather than luck, which is why
 * the budgets can be this tight.
 *
 *   - toHaveScreenshot: threshold 0.02 (per-pixel YIQ distance),
 *     maxDiffPixelRatio 0.0005.
 *   - In-page diffs: a pixel counts as differing when
 *     max |dR|,|dG|,|dB|,|dA| > CHANNEL_TOLERANCE (5/255 ~= 0.02, the
 *     absolute-channel equivalent of the YIQ 0.02), and the test fails when the
 *     differing ratio exceeds MAX_DIFF_PIXEL_RATIO (0.0005) — the same 0.05%
 *     budget as the server-side path.
 *   - Dimensions must match exactly; a size change is a hard failure, never
 *     absorbed by the ratio.
 */
import fs from 'node:fs';
import path from 'node:path';

import { expect, test, type Locator, type Page } from '@playwright/test';

import {
  stubGoldenBaselineRoutes,
  viewerUrlForCase,
  type GoldenCaseId,
} from './render-golden-baseline.fixtures';

/** Per-pixel channel tolerance for in-page diffs (5/255 ~= 0.02). */
export const CHANNEL_TOLERANCE = 5;
/** Fraction of pixels allowed to exceed CHANNEL_TOLERANCE. */
export const MAX_DIFF_PIXEL_RATIO = 0.0005;

// ESM spec module: derive the directory from import.meta.url rather than
// __dirname (absent under Playwright's ESM loader here).
const HARNESS_DIR = path.dirname(new URL(import.meta.url).pathname);
/**
 * ONE snapshot directory for both suites. Playwright's own
 * `toHaveScreenshot` goldens live in a per-spec directory it chooses; the
 * live-canvas goldens this module owns are pooled here, keyed by case id, which
 * is already unique across the suites.
 */
const SNAPSHOT_DIR = path.join(HARNESS_DIR, 'render-golden-baseline.spec.ts-snapshots');
const TIMING_ARTIFACT = path.join(HARNESS_DIR, 'render-golden-baseline.timing.json');

export type DiffReport = {
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
export async function diffPngs(
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
export async function expectMatchesGolden(
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
export function mapCanvas(page: Page): Locator {
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
export async function hideChromeOverCanvas(page: Page) {
  await page.addStyleTag({
    content: `
      *, *::before, *::after { visibility: hidden !important; }
      canvas.maplibregl-canvas { visibility: visible !important; }
    `,
  });
}

export async function captureLiveCanvas(page: Page): Promise<string> {
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
 * city_labels_ready. Waiting on grid_frame_ready alone was measured to allow an
 * 8.5% intermittent cross-run diff through (label collisions resolve in
 * glyph-arrival order), and the two-capture idle gate below could not catch it:
 * both captures agreed, the settled state itself differed between runs.
 */
export async function waitForGoldenReady(page: Page, id: string) {
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

export async function openCase(page: Page, id: GoldenCaseId, sampleRequests?: string[]) {
  await stubGoldenBaselineRoutes(page, id, undefined, sampleRequests);
  await page.goto(viewerUrlForCase(id));
  await waitForGoldenReady(page, id);
}

export type TimingEntry = { samples: number[]; median: number; p95: number };

/**
 * Timing method (stated per the task): each sample wraps ONE full repaint cycle
 * driven through the app's own GIF frame driver —
 *   performance.now() -> map.triggerRepaint() -> map.once("render") ->
 *   drawImage(canvas) -> performance.now()
 * (src/components/map-canvas.tsx captureCanvasSnapshot). That is the smallest
 * render-completion signal the app exposes without touching src/, so it is an
 * UPPER BOUND on the grid-webgl render pass: it also includes the
 * requestAnimationFrame scheduling latency and one full-size canvas copy. Both
 * addends are constant across a renderer change, so the same-machine
 * comparison is meaningful; a regression smaller than a few ms should still not
 * be read into this probe. 3 warm-up cycles discarded, then 20 measured.
 */
export async function measureRenderTimings(
  page: Page,
  id: string,
  into: Record<string, TimingEntry>,
): Promise<TimingEntry> {
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
  const entry: TimingEntry = {
    samples: sorted.map((value) => Number(value.toFixed(3))),
    median: Number(quantile(0.5).toFixed(3)),
    p95: Number(quantile(0.95).toFixed(3)),
  };
  into[id] = entry;
  // eslint-disable-next-line no-console
  console.log(`[render-timing] ${id} median=${entry.median}ms p95=${entry.p95}ms n=${entry.samples.length}`);
  return entry;
}

const TIMING_NOTE =
  'Wall time per full repaint cycle (triggerRepaint -> render event -> canvas copy) ' +
  'through window.__cartoskyGifDriver.captureFrame(). Includes rAF scheduling latency ' +
  'and one full-size canvas copy, both constant across the renderer change. Upper bound on ' +
  'the grid-webgl render pass; same-machine comparison only. 3 warm-up cycles ' +
  'discarded, 20 measured, chromium --use-angle=swiftshader.';

/**
 * Merge this file's measurements into the shared timing artifact. Each spec
 * writes its own key space, so running the suites serially accumulates both.
 */
export function writeTimingArtifact(
  cases: Record<string, TimingEntry>,
  viewport: { width: number; height: number },
  extra?: Record<string, unknown>,
) {
  if (Object.keys(cases).length === 0 && !extra) {
    return;
  }
  const existing = fs.existsSync(TIMING_ARTIFACT)
    ? (JSON.parse(fs.readFileSync(TIMING_ARTIFACT, 'utf8')) as Record<string, unknown>)
    : {};
  const existingCases = (existing.cases ?? {}) as Record<string, TimingEntry>;
  fs.writeFileSync(
    TIMING_ARTIFACT,
    `${JSON.stringify(
      {
        ...existing,
        generated_at_note: TIMING_NOTE,
        platform: process.platform,
        viewport,
        samples_per_case: 20,
        ...extra,
        cases: { ...existingCases, ...cases },
      },
      null,
      2,
    )}\n`,
  );
  // eslint-disable-next-line no-console
  console.log(`[render-timing] wrote ${TIMING_ARTIFACT}`);
}
