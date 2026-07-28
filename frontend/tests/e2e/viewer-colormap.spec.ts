/**
 * Phase 3 colormap contract (docs/plans/2026-07-28-map-viewer-redesign-phase-3-colormap.md,
 * Tasks 2 and 4). Approved Candidate A anchors, Brian 2026-07-28.
 *
 * WebGL note (§9): the render tests sample real MapLibre + SwiftShader output
 * composited at the default 0.9 overlay opacity over the basemap. Reviewed
 * tolerances: band samples must be pairwise separated by >= 15 RGB euclidean
 * distance (the approved ramp's 10 °F pairs sit at ΔE >= 23.6, far above this
 * floor — the backend pytest gate, not this floor, discriminates old vs new);
 * legend-probe matches allow <= 40 distance for chip compositing; the two
 * compare panes must agree within <= 10. The LUT test uses no WebGL and is
 * exact.
 */
import { test, expect, type Page } from '@playwright/test';

import {
  COLORMAP_BAND_VALUES_BY_FH,
  COLORMAP_BAND_VALUES_F,
  COLORMAP_BBOX,
  COLORMAP_VIEW,
  TMP2M_APPROVED_LEGEND_ENTRIES,
  stubViewerColormapRoutes,
} from './viewer-colormap.fixtures';

const VIEWER_URL =
  `/viewer?m=gfs&r=latest&v=tmp2m&fh=0&reg=conus&lat=${COLORMAP_VIEW.lat}&lon=${COLORMAP_VIEW.lon}&z=${COLORMAP_VIEW.zoom}&screenshot=1`;
const COMPARE_URL =
  `/compare?lm=gfs&lv=tmp2m&lr=latest&rm=gfs&rv=tmp2m&rr=latest&fh=0&lat=${COLORMAP_VIEW.lat}&lon=${COLORMAP_VIEW.lon}&z=5&screenshot=1`;

const BBOX_WIDTH_METERS = COLORMAP_BBOX[2] - COLORMAP_BBOX[0];
const FIXTURE_FRAME_COUNT = 3;

function metersPerPixel(zoom: number): number {
  return (156543.03392 * Math.cos((COLORMAP_VIEW.lat * Math.PI) / 180)) / 2 ** zoom;
}

const rgbDistance = (a: number[], b: number[]) =>
  Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

function approvedColorAt(value: number): number[] {
  const entry = TMP2M_APPROVED_LEGEND_ENTRIES.find((candidate) => candidate.value === value);
  if (!entry) throw new Error(`no approved entry at ${value}`);
  const h = entry.color.replace('#', '');
  return [0, 2, 4].map((i) => Number.parseInt(h.slice(i, i + 2), 16));
}

/** Decode a PNG data/blob URL in-page and sample 1x1 pixels. */
async function samplePixels(
  page: Page,
  source: string,
  points: Array<{ x: number; y: number }>,
): Promise<{ width: number; height: number; samples: number[][] }> {
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
      const samples = args.points.map((point) =>
        Array.from(
          ctx.getImageData(
            Math.round(point.x < 0 ? canvas.width + point.x : point.x),
            Math.round(point.y < 0 ? canvas.height + point.y : point.y),
            1,
            1,
          ).data,
        ),
      );
      return { width: canvas.width, height: canvas.height, samples };
    },
    { source, points },
  );
}

test.describe('Viewer colormap (Phase 3)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Pinned Chromium/SwiftShader contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop contract.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  test('buildLegendLut reproduces the approved anchors exactly through the exported seam', async ({ page }) => {
    await page.goto('/src/lib/grid-lut.ts');
    const result = await page.evaluate(async (entries) => {
      const module = await import(/* @vite-ignore */ '/src/lib/grid-lut.ts');
      const legend = { title: 'Temperature 2m', kind: 'continuous', entries, opacity: 1 };
      const { pixels, min, max } = module.buildLegendLut(legend);
      const size = pixels.length / 4;

      const hexToRgba = (color: string) => {
        const h = color.replace('#', '');
        return [0, 2, 4].map((i) => Number.parseInt(h.slice(i, i + 2), 16)).concat(255);
      };
      const sorted = [...entries].sort((a, b) => a.value - b.value);
      const expectedAt = (value: number) => {
        let left = sorted[0];
        let right = sorted[sorted.length - 1];
        for (let cursor = 0; cursor < sorted.length - 1; cursor += 1) {
          if (value >= sorted[cursor].value && value <= sorted[cursor + 1].value) {
            left = sorted[cursor];
            right = sorted[cursor + 1];
            break;
          }
        }
        const span = Math.max(1e-6, right.value - left.value);
        const t = right.value <= left.value ? 0 : Math.max(0, Math.min(1, (value - left.value) / span));
        const l = hexToRgba(left.color);
        const r = hexToRgba(right.color);
        return l.map((c, i) => Math.round(c + (r[i] - c) * t));
      };

      const mismatches: string[] = [];
      const sampled: Record<number, number[]> = {};
      for (let v = 70; v <= 95; v += 1) {
        const index = Math.round(((v - min) / (max - min)) * (size - 1));
        const bucketValue = min + ((max - min) * index) / (size - 1);
        const actual = Array.from(pixels.slice(index * 4, index * 4 + 4));
        const expected = expectedAt(bucketValue);
        sampled[v] = actual;
        if (actual.some((c: number, i: number) => c !== expected[i])) {
          mismatches.push(`${v}F: got ${actual.join(',')} expected ${expected.join(',')}`);
        }
      }

      const dist = (a: number[], b: number[]) =>
        Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
      const pairDistances = [];
      for (let v = 70; v <= 85; v += 1) {
        pairDistances.push({ pair: `${v}v${v + 10}`, distance: dist(sampled[v], sampled[v + 10]) });
      }
      return { min, max, mismatches, minPair: pairDistances.reduce((a, b) => (a.distance <= b.distance ? a : b)) };
    }, TMP2M_APPROVED_LEGEND_ENTRIES);

    expect(result.min).toBe(-60);
    expect(result.max).toBe(120);
    expect(result.mismatches).toEqual([]);
    // Every 10 °F pair in the band is distinct in the LUT itself.
    expect(result.minPair.distance).toBeGreaterThanOrEqual(25);
  });

  test('approved ramp flows through the live map and the real Share export with an exact fetch pin', async ({ page }) => {
    const binRequests: string[] = [];
    page.on('request', (request) => {
      if (/\/api\/v4\/grid\/.*\.bin/.test(request.url())) {
        binRequests.push(request.url());
      }
    });
    await stubViewerColormapRoutes(page);
    await page.goto(VIEWER_URL);

    await expect
      .poll(
        () =>
          page.evaluate(() =>
            ((window as typeof window & { __cartoskyGateLog?: Array<{ event: string }> }).__cartoskyGateLog ?? [])
              .some((entry) => entry.event === 'grid_frame_ready'),
          ),
        { timeout: 30_000 },
      )
      .toBe(true);
    await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 20_000 });

    // The warm queue fetches the whole 3-frame fixture run; wait for it to
    // finish so the fetch pin below is exact rather than racing the warmup.
    await expect
      .poll(() => new Set(binRequests).size, { timeout: 20_000 })
      .toBe(FIXTURE_FRAME_COUNT);
    expect(binRequests.length).toBe(FIXTURE_FRAME_COUNT);

    const cellPx = (BBOX_WIDTH_METERS / 3) / metersPerPixel(COLORMAP_VIEW.zoom);
    const captureCells = async (): Promise<number[][]> => {
      const dataUrl = await page.evaluate(async () => {
        const w = window as typeof window & { __cartoskyViewerCapture?: () => Promise<string | null> };
        return (await w.__cartoskyViewerCapture?.()) ?? null;
      });
      expect(dataUrl).not.toBeNull();
      const probe = await samplePixels(page, dataUrl!, [{ x: 0, y: 0 }]);
      const cx = probe.width / 2;
      const cy = probe.height / 2;
      const { samples } = await samplePixels(page, dataUrl!, [
        { x: cx - cellPx, y: cy },
        { x: cx, y: cy },
        { x: cx + cellPx, y: cy },
      ]);
      return samples;
    };

    // Live map: the three FH0 band cells are pairwise distinct.
    const liveCells = await captureCells();
    expect(rgbDistance(liveCells[0], liveCells[1])).toBeGreaterThanOrEqual(15);
    expect(rgbDistance(liveCells[1], liveCells[2])).toBeGreaterThanOrEqual(15);
    expect(rgbDistance(liveCells[0], liveCells[2])).toBeGreaterThanOrEqual(15);

    // Real Share export: open the dialog and sample the composed preview —
    // the actual exportViewerScreenshotPng output with its baked legend.
    const map = page.locator('div[role="img"][aria-label="Weather map"]').first();
    const mapDimensions = await map.evaluate((element) => ({
      width: element.clientWidth,
      height: element.clientHeight,
    }));
    await page.getByRole('button', { name: 'Share', exact: true }).first().click();
    const preview = page
      .getByRole('dialog', { name: 'Share' })
      .getByRole('img', { name: 'Screenshot preview' });
    await expect(preview).toBeVisible({ timeout: 15_000 });
    const previewSource = await preview.getAttribute('src');
    expect(previewSource).toBeTruthy();

    // The preview src is a blob URL that the modal revokes when it refreshes,
    // so re-read the CURRENT src and do every export read in one evaluate
    // with a single fetch.
    const exportReport = await preview.evaluate(
      async (element, args) => {
        const source = (element as HTMLImageElement).src;
        const response = await fetch(source);
        const bitmap = await createImageBitmap(await response.blob());
        const canvas = document.createElement('canvas');
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
        ctx.drawImage(bitmap, 0, 0);
        bitmap.close();

        const exportScale = canvas.width / args.mapWidth;
        const cy = Math.round(canvas.height / 2);
        const at = (x: number) => Array.from(ctx.getImageData(Math.round(x), cy, 1, 1).data);
        const cells = [
          at(canvas.width / 2 - args.cellPx * exportScale),
          at(canvas.width / 2),
          at(canvas.width / 2 + args.cellPx * exportScale),
        ];

        // Baked-legend probe over the bottom strip.
        const startY = Math.floor(canvas.height * 0.8);
        const data = ctx.getImageData(0, startY, canvas.width, canvas.height - startY).data;
        const found = args.probes.map(() => false);
        for (let offset = 0; offset < data.length; offset += 4 * 3) {
          for (let p = 0; p < args.probes.length; p += 1) {
            if (found[p]) continue;
            const probe = args.probes[p];
            const dr = data[offset] - probe[0];
            const dg = data[offset + 1] - probe[1];
            const db = data[offset + 2] - probe[2];
            if (Math.sqrt(dr * dr + dg * dg + db * db) <= 40) {
              found[p] = true;
            }
          }
        }
        return { cells, legendFound: found };
      },
      { mapWidth: mapDimensions.width, cellPx, probes: [approvedColorAt(60), approvedColorAt(110)] },
    );

    expect(rgbDistance(exportReport.cells[0], exportReport.cells[1])).toBeGreaterThanOrEqual(15);
    expect(rgbDistance(exportReport.cells[1], exportReport.cells[2])).toBeGreaterThanOrEqual(15);
    // The export matches the live canvas cells (same seam, composed output).
    for (let i = 0; i < 3; i += 1) {
      expect(rgbDistance(exportReport.cells[i], liveCells[i])).toBeLessThanOrEqual(20);
    }
    // Baked legend colors that cannot come from the 72/82/92 °F map data.
    expect(exportReport.legendFound).toEqual([true, true]);

    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Share' })).toBeHidden();

    // Step to FH1 and confirm it RENDERED (the fixture shifts each frame by
    // +2 °F, so the sampled cells change when — and only when — FH1 paints).
    // Focus (not click) the thumb: since the Phase 4 hit-area enlargement a
    // click can commit a track jump of its own; keyboard-only stepping keeps
    // the FH transition deterministic at 0 -> 1.
    await page.locator('[data-tour-target="forecast-scrubber"] [role="slider"]').first().focus();
    await page.keyboard.press('ArrowRight');
    await expect.poll(() => new URL(page.url()).searchParams.get('fh'), { timeout: 15_000 }).toBe('1');
    await expect
      .poll(async () => rgbDistance((await captureCells())[1], liveCells[1]), { timeout: 20_000 })
      .toBeGreaterThanOrEqual(3);

    // Exact fetch pin after confirmed FH1 render: still exactly one request
    // per fixture frame — stepping onto a warmed frame refetches nothing.
    expect(binRequests.length).toBe(FIXTURE_FRAME_COUNT);
    expect(new Set(binRequests).size).toBe(FIXTURE_FRAME_COUNT);
  });

  test('compare panes render the approved ramp identically in legends and pixels', async ({ page }) => {
    await stubViewerColormapRoutes(page);
    await page.goto(COMPARE_URL);

    const gradients = page.locator('[style*="linear-gradient"]');
    await expect
      .poll(async () => {
        const styles = await gradients.evaluateAll((elements) =>
          elements
            .map((element) => (element as HTMLElement).style.backgroundImage)
            .filter((value) => value.includes('linear-gradient')),
        );
        return styles.length;
      }, { timeout: 30_000 })
      .toBeGreaterThanOrEqual(2);

    const styles = await gradients.evaluateAll((elements) =>
      elements
        .map((element) => (element as HTMLElement).style.backgroundImage)
        .filter((value) => value.includes('linear-gradient')),
    );
    const [leftLegend, rightLegend] = styles;
    expect(leftLegend).toBe(rightLegend);

    const warmRgb = TMP2M_APPROVED_LEGEND_ENTRIES.filter((entry) => entry.value >= 55).map((entry) => {
      const h = entry.color.replace('#', '');
      const [r, g, b] = [0, 2, 4].map((i) => Number.parseInt(h.slice(i, i + 2), 16));
      return `rgb(${r}, ${g}, ${b})`;
    });
    expect(warmRgb.some((rgb) => leftLegend.includes(rgb))).toBe(true);

    // Pixel proof: capture both panes through the compare capture seam (a
    // side-by-side composite) and sample the three band cells in EACH pane.
    const cellPx = (BBOX_WIDTH_METERS / 3) / metersPerPixel(5);
    const captureComposite = () =>
      page.evaluate(async () => {
        const w = window as typeof window & { __cartoskyCompareCapture?: () => Promise<string | null> };
        return (await w.__cartoskyCompareCapture?.()) ?? null;
      });

    // Poll until both panes have painted the grid (left pane cells distinct).
    await expect
      .poll(
        async () => {
          const dataUrl = await captureComposite();
          if (!dataUrl) return -1;
          const probe = await samplePixels(page, dataUrl, [{ x: 0, y: 0 }]);
          const paneCx = probe.width / 4;
          const { samples } = await samplePixels(page, dataUrl, [
            { x: paneCx - cellPx, y: probe.height / 2 },
            { x: paneCx, y: probe.height / 2 },
          ]);
          return rgbDistance(samples[0], samples[1]);
        },
        { timeout: 30_000 },
      )
      .toBeGreaterThanOrEqual(15);

    const dataUrl = await captureComposite();
    expect(dataUrl).not.toBeNull();
    const probe = await samplePixels(page, dataUrl!, [{ x: 0, y: 0 }]);
    const cy = probe.height / 2;
    const leftCx = probe.width / 4;
    const rightCx = (3 * probe.width) / 4;
    const { samples } = await samplePixels(page, dataUrl!, [
      { x: leftCx - cellPx, y: cy },
      { x: leftCx, y: cy },
      { x: leftCx + cellPx, y: cy },
      { x: rightCx - cellPx, y: cy },
      { x: rightCx, y: cy },
      { x: rightCx + cellPx, y: cy },
    ]);
    const left = samples.slice(0, 3);
    const right = samples.slice(3, 6);
    for (const pane of [left, right]) {
      expect(rgbDistance(pane[0], pane[1])).toBeGreaterThanOrEqual(15);
      expect(rgbDistance(pane[1], pane[2])).toBeGreaterThanOrEqual(15);
      expect(rgbDistance(pane[0], pane[2])).toBeGreaterThanOrEqual(15);
    }
    // Same data + same ramp: the panes must agree cell-for-cell.
    for (let i = 0; i < 3; i += 1) {
      expect(rgbDistance(left[i], right[i])).toBeLessThanOrEqual(10);
    }
  });
});
