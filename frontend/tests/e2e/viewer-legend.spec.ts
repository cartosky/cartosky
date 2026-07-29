/**
 * Phase 7 legend contract suite
 * (docs/plans/2026-07-29-map-viewer-redesign-phase-7-legend.md, Task 4).
 *
 * MAP_VIEWER_REDESIGN §7: one legend model drives every rendering; numeric
 * truth is normative where the metadata is numeric and nominal categories
 * invent nothing; the compact map-corner chip keeps EVERY applicable
 * precipitation-type ramp; the export composition contains exactly one baked
 * legend and no chip.
 *
 * Fixed, repo-owned fixtures only (viewer-legend.fixtures.ts) — never live
 * weather.
 *
 * DOM contract under test:
 *   [data-testid="legend-presentation"][data-legend-mode]  the resolved mode
 *   [data-testid="legend-gradient"] / "legend-tick"        continuous
 *   [data-testid="legend-swatch-row"]                      discrete|categorical|indexed
 *   [data-testid="legend-ramp"][data-ramp-key]             ptype modes
 *   [data-testid="legend-ramp-endpoints"]                  per-ramp numbers
 *   [data-testid="legend-shared-scale"]                    shared numeric ladder
 *   [data-testid="legend-group"]                           composite-group
 *   [data-testid="compact-legend-chip"][data-export-exclude="legend-chip"]
 *   [data-testid="compact-legend-chip-toggle"|"compact-legend-chip-panel"]
 *   [data-testid="rail-collapsed-legend"]                  §6.3 third rail item
 */
import { test, expect, type Page } from '@playwright/test';

import {
  CONTINUOUS_STOPS,
  INDEXED_ENTRIES,
  LEGEND_FIXTURES,
  NOMINAL_CATEGORICAL_ENTRIES,
  RH_DISCRETE_ENTRIES,
  legendViewerUrl,
  ptypeOrderOf,
  stubLegendRoutes,
  type LegendFixture,
} from './viewer-legend.fixtures';

const RAIL_COLLAPSED_PX = 72;
/** ≥1024 px of map with the rail expanded ⇒ expanded default (§6.4). */
const WIDE = { width: 1440, height: 900 };
/** <1024 px of map ⇒ collapsed default, which is where the chip lives. */
const COLLAPSED = { width: 1200, height: 800 };
/** Narrowest desktop rail width — the §7.2 width-pressure case. */
const TIGHT = { width: 768, height: 900 };

async function openLegendViewer(page: Page, fixture: LegendFixture, viewport = WIDE) {
  await page.setViewportSize(viewport);
  await stubLegendRoutes(page, fixture);
  await page.goto(legendViewerUrl(fixture));
  await expect(page.getByTestId('viewer-suspense-skeleton')).toBeHidden({ timeout: 30_000 });
  await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });
  await expect(page.getByTestId('viewer-rail')).toBeVisible({ timeout: 20_000 });
}

async function expandRail(page: Page) {
  if ((await page.getByTestId('viewer-rail').getAttribute('data-rail-state')) === 'collapsed') {
    await page.getByTestId('rail-expand-toggle').click();
  }
  await expect(page.getByTestId('viewer-rail')).toHaveAttribute('data-rail-state', 'expanded');
}

/**
 * The full (rail-mounted) legend rendering for the current fixture. Composite
 * groups nest a presentation per component, so this is the outermost one.
 */
function railLegend(page: Page) {
  return page.locator('[data-legend-mount="rail"] [data-testid="legend-presentation"]').first();
}

function chip(page: Page) {
  return page.getByTestId('compact-legend-chip');
}

/** Every rendered text run inside a surface, with its computed font size. */
async function textRuns(page: Page, selector: string): Promise<Array<{ text: string; size: number }>> {
  return page.evaluate((sel) => {
    const root = document.querySelector(sel);
    if (!root) return [];
    const runs: Array<{ text: string; size: number }> = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      const text = (node.textContent ?? '').trim();
      const parent = node.parentElement;
      if (text && parent) {
        const style = getComputedStyle(parent);
        if (style.display !== 'none' && style.visibility !== 'hidden') {
          runs.push({ text, size: Number.parseFloat(style.fontSize) });
        }
      }
      node = walker.nextNode();
    }
    return runs;
  }, selector);
}

function hexToRgb(hex: string): number[] {
  const value = hex.replace('#', '');
  return [0, 2, 4].map((index) => Number.parseInt(value.slice(index, index + 2), 16));
}

test.describe('Viewer legend (Phase 7)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Pinned Chromium contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop contract; the mobile chip is Phase 8.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  // ── 1. Full renderings per the §7.1 table ─────────────────────────────────

  test('continuous renders a gradient with numeric ticks and units', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.continuous;
    await openLegendViewer(page, fixture);
    await expandRail(page);

    const legend = railLegend(page);
    await expect(legend, 'legend presentation').toHaveAttribute('data-legend-mode', 'continuous', { timeout: 20_000 });
    const gradient = legend.getByTestId('legend-gradient');
    await expect(gradient).toBeVisible();
    const backgroundImage = await gradient.evaluate((el) => getComputedStyle(el).backgroundImage);
    expect(backgroundImage).toContain('linear-gradient');
    const [warmR, warmG, warmB] = hexToRgb(CONTINUOUS_STOPS[10][1]);
    expect(backgroundImage).toContain(`rgb(${warmR}, ${warmG}, ${warmB})`);

    const ticks = legend.getByTestId('legend-tick');
    expect(await ticks.count()).toBeGreaterThanOrEqual(3);
    const tickTexts = await ticks.allInnerTexts();
    for (const text of tickTexts) {
      expect(text.trim()).toMatch(/^-?\d+(?:\.\d+)?$/);
    }
    const tickValues = tickTexts.map((text) => Number(text.trim()));
    expect(Math.min(...tickValues)).toBeGreaterThanOrEqual(-20);
    expect(Math.max(...tickValues)).toBeLessThanOrEqual(120);

    // Units live in the legend title (§7.1: gradient + ticks + units).
    await expect(page.locator('[data-legend-mount="rail"]')).toContainText('(F)');
  });

  test('discrete renders swatches carrying the metadata break values', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.discrete;
    await openLegendViewer(page, fixture);
    await expandRail(page);

    const legend = railLegend(page);
    await expect(legend, 'legend presentation').toHaveAttribute('data-legend-mode', 'discrete', { timeout: 20_000 });
    const rows = legend.getByTestId('legend-swatch-row');
    await expect(rows).toHaveCount(RH_DISCRETE_ENTRIES.length);
    const text = (await legend.innerText()).replace(/\s+/g, ' ');
    // Break values the metadata defines, not invented ones.
    expect(text).toContain('0-5');
    expect(text).toContain('95-100');
  });

  test('nominal categorical renders named swatches and invents no numbers', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.categorical;
    await openLegendViewer(page, fixture);
    await expandRail(page);

    const legend = railLegend(page);
    await expect(legend, 'legend presentation').toHaveAttribute('data-legend-mode', 'categorical', { timeout: 20_000 });
    const rows = legend.getByTestId('legend-swatch-row');
    await expect(rows).toHaveCount(NOMINAL_CATEGORICAL_ENTRIES.length);
    for (const entry of NOMINAL_CATEGORICAL_ENTRIES) {
      await expect(legend).toContainText(entry.label);
    }
    // §7.1: purely nominal categories do not invent numeric values.
    const body = await legend.innerText();
    expect(body, `nominal categorical rendered digits: ${body}`).not.toMatch(/\d/);
  });

  test('indexed renders index labels without presenting the index codes as measurements', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.indexed;
    await openLegendViewer(page, fixture);
    await expandRail(page);

    const legend = railLegend(page);
    await expect(legend, 'legend presentation').toHaveAttribute('data-legend-mode', 'indexed', { timeout: 20_000 });
    await expect(legend.getByTestId('legend-swatch-row')).toHaveCount(INDEXED_ENTRIES.length);
    for (const entry of INDEXED_ENTRIES) {
      await expect(legend).toContainText(entry.label);
    }
    // Bare 1..n index codes are not semantically meaningful numbers (§7.1).
    const body = await legend.innerText();
    expect(body, `indexed rendered raw index codes: ${body}`).not.toMatch(/\d/);
  });

  test('ptype-intensity renders every ramp with truthful in/hr endpoints', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.ptypeIntensity;
    const order = ptypeOrderOf(fixture);
    await openLegendViewer(page, fixture);
    await expandRail(page);

    const legend = railLegend(page);
    await expect(legend, 'legend presentation').toHaveAttribute('data-legend-mode', 'ptype-intensity', { timeout: 20_000 });
    const ramps = legend.getByTestId('legend-ramp');
    await expect(ramps).toHaveCount(order.length);
    expect(await ramps.evaluateAll((els) => els.map((el) => el.getAttribute('data-ramp-key')))).toEqual(order);
    for (const label of ['Rain', 'Snow', 'Ice']) {
      await expect(legend).toContainText(label);
    }

    // Per-type bins diverge, so each ramp carries its own numeric endpoints.
    await expect(legend.getByTestId('legend-shared-scale')).toHaveCount(0);
    const endpoints = legend.getByTestId('legend-ramp-endpoints');
    await expect(endpoints).toHaveCount(order.length);
    const rainEndpoints = await endpoints.first().innerText();
    expect(rainEndpoints).toMatch(/0\s*[–-]\s*2\.5/);
    expect(rainEndpoints).toContain('in/hr');
  });

  test('radar-ptype backs its (dBZ) title with rendered dBZ numbers on all four ramps', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.radarPtype;
    const order = ptypeOrderOf(fixture);
    await openLegendViewer(page, fixture);
    await expandRail(page);

    const mount = page.locator('[data-legend-mount="rail"]');
    await expect(mount).toContainText('dBZ');

    const legend = railLegend(page);
    await expect(legend, 'legend presentation').toHaveAttribute('data-legend-mode', 'radar-ptype', { timeout: 20_000 });
    const ramps = legend.getByTestId('legend-ramp');
    await expect(ramps).toHaveCount(order.length);
    expect(await ramps.evaluateAll((els) => els.map((el) => el.getAttribute('data-ramp-key')))).toEqual(order);

    // Real MRMS ladders diverge (rain 5–70, the rest 0–60), so the model must
    // NOT claim a shared scale — each ramp speaks its own numbers.
    await expect(legend.getByTestId('legend-shared-scale')).toHaveCount(0);
    const endpoints = await legend.getByTestId('legend-ramp-endpoints').allInnerTexts();
    expect(endpoints).toHaveLength(order.length);
    expect(endpoints[0]).toMatch(/5\s*[–-]\s*70/);
    expect(endpoints[1]).toMatch(/0\s*[–-]\s*60/);
    for (const text of endpoints) {
      expect(text).toMatch(/\d/);
    }
  });

  test('a radar-id legend without ptype grouping renders continuous and fabricates nothing', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.singleRadarContinuous;
    await openLegendViewer(page, fixture);
    await expandRail(page);

    const legend = railLegend(page);
    // The `radar` id must not force ptype mode when the metadata defines no
    // grouping — a single zero-delimited ladder is one continuous product.
    await expect(legend, 'legend presentation').toHaveAttribute('data-legend-mode', 'continuous', { timeout: 20_000 });
    await expect(legend.getByTestId('legend-ramp')).toHaveCount(0);
    await expect(legend.getByTestId('legend-gradient')).toBeVisible();

    const text = (await legend.innerText()).replace(/\s+/g, ' ');
    // No invented ramp identity and no fabricated threshold: the metadata
    // ladder is 0/10/25/40/55/65/75, so "Rain" and interpolated minima like
    // "1.2" must not appear anywhere.
    expect(text).not.toContain('Rain');
    expect(text).not.toContain('1.2');
    const ticks = legend.getByTestId('legend-tick');
    const tickValues = (await ticks.allInnerTexts()).map((t) => Number(t.trim()));
    expect(tickValues.length).toBeGreaterThanOrEqual(3);
    for (const value of tickValues) {
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThanOrEqual(75);
    }
  });

  test('radar-ptype with one shared ladder renders the numeric scale exactly once', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.sharedRadarPtype;
    const order = ptypeOrderOf(fixture);
    await openLegendViewer(page, fixture);
    await expandRail(page);

    const legend = railLegend(page);
    await expect(legend, 'legend presentation').toHaveAttribute('data-legend-mode', 'radar-ptype', { timeout: 20_000 });
    await expect(legend.getByTestId('legend-ramp')).toHaveCount(order.length);

    const shared = legend.getByTestId('legend-shared-scale');
    await expect(shared).toHaveCount(1);
    const sharedText = await shared.innerText();
    expect(sharedText).toContain('5');
    expect(sharedText).toContain('70');
    expect(sharedText).toContain('dBZ');
    // The shared ladder replaces the per-ramp repetition.
    await expect(legend.getByTestId('legend-ramp-endpoints')).toHaveCount(0);
  });

  test('composite renders one labeled group per component layer', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.composite;
    await openLegendViewer(page, fixture);
    await expandRail(page);

    const legend = railLegend(page);
    await expect(legend, 'legend presentation').toHaveAttribute('data-legend-mode', 'composite-group', { timeout: 20_000 });
    const groups = legend.getByTestId('legend-group');
    await expect(groups).toHaveCount(fixture.composite!.length);
    for (const layer of fixture.composite!) {
      await expect(legend).toContainText(layer.displayName);
    }
  });

  // ── 2. Compact chip in the collapsed-rail state ───────────────────────────

  test('chip keeps every ptype ramp, drops repeated labels before ramps under width pressure', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.radarPtype;
    const order = ptypeOrderOf(fixture);
    await openLegendViewer(page, fixture, COLLAPSED);
    await expect(page.getByTestId('viewer-rail')).toHaveAttribute('data-rail-state', 'collapsed');

    const chipRoot = chip(page);
    await expect(chipRoot).toBeVisible({ timeout: 20_000 });
    // Map corner, clear of the rail.
    const box = await chipRoot.boundingBox();
    expect(box!.x).toBeGreaterThan(RAIL_COLLAPSED_PX);

    const ramps = chipRoot.getByTestId('legend-ramp');
    await expect(ramps).toHaveCount(order.length);
    expect(await ramps.evaluateAll((els) => els.map((el) => el.getAttribute('data-ramp-key')))).toEqual(order);
    // Never "dominant ramp + N more".
    await expect(chipRoot).not.toContainText(/\bmore\b/i);
    await expect(chipRoot.getByTestId('legend-ramp-endpoints').first()).toBeVisible();

    // §7.2: under width pressure the repeated break labels go, never a ramp.
    await page.setViewportSize(TIGHT);
    // Under parallel load the resize can land well after the call returns;
    // wait for the page to actually observe it before judging the chip.
    await expect
      .poll(() => page.evaluate(() => window.innerWidth), { timeout: 20_000 })
      .toBe(TIGHT.width);
    await expect(page.getByTestId('viewer-rail')).toHaveAttribute('data-rail-state', 'collapsed');
    await expect(chipRoot).toBeVisible({ timeout: 20_000 });
    await expect(chipRoot).toHaveAttribute('data-compact-density', 'tight');
    await expect(chipRoot.getByTestId('legend-ramp')).toHaveCount(order.length);
    await expect(chipRoot.getByTestId('legend-ramp-endpoints')).toHaveCount(0);
  });

  test('chip renders the shared numeric scale exactly once', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.sharedRadarPtype;
    await openLegendViewer(page, fixture, COLLAPSED);

    const chipRoot = chip(page);
    await expect(chipRoot).toBeVisible({ timeout: 20_000 });
    await expect(chipRoot.getByTestId('legend-ramp')).toHaveCount(ptypeOrderOf(fixture).length);
    await expect(chipRoot.getByTestId('legend-shared-scale')).toHaveCount(1);
  });

  test('chip summarizes swatch kinds truthfully and expands in place to the full list', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.discrete;
    await openLegendViewer(page, fixture, COLLAPSED);

    const chipRoot = chip(page);
    await expect(chipRoot).toBeVisible({ timeout: 20_000 });
    const summary = chipRoot.getByTestId('legend-summary-count');
    await expect(summary).toHaveText(`${RH_DISCRETE_ENTRIES.length} levels`, { timeout: 20_000 });

    // Tap expands in place to the same full inline legend.
    await chipRoot.getByTestId('compact-legend-chip-toggle').click();
    const panel = page.getByTestId('compact-legend-chip-panel');
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId('legend-swatch-row')).toHaveCount(RH_DISCRETE_ENTRIES.length);

    // A second tap collapses it back.
    await chipRoot.getByTestId('compact-legend-chip-toggle').click();
    await expect(panel).toBeHidden();

    // Escape collapses it too.
    await chipRoot.getByTestId('compact-legend-chip-toggle').click();
    await expect(panel).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
  });

  test('chip continuous form is a mini gradient with endpoints and units', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.continuous;
    await openLegendViewer(page, fixture, COLLAPSED);

    const chipRoot = chip(page);
    await expect(chipRoot).toBeVisible({ timeout: 20_000 });
    await expect(chipRoot.locator('[data-testid="legend-presentation"]')).toHaveAttribute(
      'data-legend-mode',
      'continuous',
      { timeout: 20_000 },
    );
    await expect(chipRoot.getByTestId('legend-gradient')).toBeVisible();
    const text = (await chipRoot.innerText()).replace(/\s+/g, ' ');
    expect(text).toContain('-20');
    expect(text).toContain('120');
    expect(text).toContain('(F)');
  });

  test('chip is hidden when the legend preference is off and when the rail is expanded', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.continuous;
    await openLegendViewer(page, fixture, COLLAPSED);
    await expect(chip(page)).toBeVisible({ timeout: 20_000 });

    await expandRail(page);
    await expect(chip(page)).toHaveCount(0);

    await page.evaluate(() => localStorage.setItem('twf.map.legend_visible', 'false'));
    await page.reload();
    await expect(page.getByTestId('viewer-suspense-skeleton')).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId('viewer-rail')).toBeVisible({ timeout: 20_000 });
    await expect(chip(page)).toHaveCount(0);
  });

  // ── 3. Collapsed rail gains the Legend item (§6.3) ────────────────────────

  test('collapsed rail shows Source / View / Legend and Legend expands to the legend mount', async ({ page }) => {
    await openLegendViewer(page, LEGEND_FIXTURES.continuous, COLLAPSED);
    const rail = page.getByTestId('viewer-rail');
    await expect(rail).toHaveAttribute('data-rail-state', 'collapsed');

    const captions = rail.getByTestId('rail-collapsed-caption');
    await expect(captions).toHaveText(['Source', 'View', 'Legend'], { timeout: 20_000 });
    for (const testId of ['rail-collapsed-source', 'rail-collapsed-view', 'rail-collapsed-legend']) {
      const caption = page.getByTestId(testId).getByTestId('rail-collapsed-caption');
      const fontSize = await caption.evaluate((el) => Number.parseFloat(getComputedStyle(el).fontSize));
      expect(fontSize, `${testId} caption font-size`).toBeGreaterThanOrEqual(11);
      const box = await page.getByTestId(testId).boundingBox();
      expect(Math.min(box!.width, box!.height), `${testId} min side`).toBeGreaterThanOrEqual(32);
    }

    await page.getByTestId('rail-collapsed-legend').click();
    await expect(rail).toHaveAttribute('data-rail-state', 'expanded');
    await expect(page.locator('[data-legend-mount="rail"]')).toBeInViewport();
    await expect(railLegend(page)).toBeVisible();
  });

  // ── 4. Export exclusion (§7.3) ────────────────────────────────────────────

  test('the real Share export contains exactly one baked legend and no chip pixels', async ({ page }) => {
    const fixture = LEGEND_FIXTURES.continuous;
    // `?screenshot=1` installs the canvas-only capture hook the exporter uses;
    // it does not hide any chrome, so the chip is on screen exactly as usual.
    await page.setViewportSize(COLLAPSED);
    await stubLegendRoutes(page, fixture);
    await page.goto(`${legendViewerUrl(fixture)}&screenshot=1`);
    await expect(page.getByTestId('viewer-suspense-skeleton')).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId('viewer-rail')).toBeVisible({ timeout: 20_000 });

    const chipRoot = chip(page);
    await expect(chipRoot).toBeVisible({ timeout: 20_000 });
    // The explicit §7.3 exclusion marker: a future capture change fails loudly.
    await expect(chipRoot).toHaveAttribute('data-export-exclude', 'legend-chip');

    const map = page.locator('div[role="img"][aria-label="Weather map"]').first();
    const mapBox = (await map.boundingBox())!;
    const chipBox = (await chipRoot.boundingBox())!;
    // The chip must actually sit over the map for this proof to mean anything.
    expect(chipBox.x).toBeGreaterThanOrEqual(mapBox.x);
    expect(chipBox.y).toBeGreaterThanOrEqual(mapBox.y);
    expect(chipBox.x + chipBox.width).toBeLessThanOrEqual(mapBox.x + mapBox.width + 1);

    // Normalized position of the chip's centre inside the map element.
    const u = (chipBox.x + chipBox.width / 2 - mapBox.x) / mapBox.width;
    const v = (chipBox.y + chipBox.height / 2 - mapBox.y) / mapBox.height;

    // Colors only the legend ramp can produce: the fixture grid holds 30 °F
    // and 55 °F, so the 0 °F and 110 °F anchors cannot come from map data.
    const probes = [hexToRgb(CONTINUOUS_STOPS[2][1]), hexToRgb(CONTINUOUS_STOPS[13][1])];

    // Canvas-only capture: chip-free by construction, the reference truth.
    const captureMap = () =>
      page.evaluate(async () => {
        const w = window as typeof window & { __cartoskyViewerCapture?: () => Promise<string | null> };
        return (await w.__cartoskyViewerCapture?.()) ?? null;
      });
    await expect.poll(async () => (await captureMap()) !== null, { timeout: 30_000 }).toBe(true);
    const liveDataUrl = await captureMap();
    expect(liveDataUrl).not.toBeNull();

    await page.getByRole('button', { name: 'Share', exact: true }).first().click();
    const preview = page
      .getByRole('dialog', { name: 'Share' })
      .getByRole('img', { name: 'Screenshot preview' });
    await expect(preview).toBeVisible({ timeout: 20_000 });

    const report = await preview.evaluate(
      async (element, args) => {
        const decode = async (source: string) => {
          const response = await fetch(source);
          const bitmap = await createImageBitmap(await response.blob());
          const canvas = document.createElement('canvas');
          canvas.width = bitmap.width;
          canvas.height = bitmap.height;
          const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
          ctx.drawImage(bitmap, 0, 0);
          bitmap.close();
          return { canvas, ctx };
        };

        const live = await decode(args.liveDataUrl);
        const exported = await decode((element as HTMLImageElement).src);

        // Reproduce drawMapImageCover's centre crop to map (u, v) into the
        // composed frame — never rely on the chip's screen position alone.
        const sourceAspect = live.canvas.width / live.canvas.height;
        const targetAspect = exported.canvas.width / exported.canvas.height;
        let sx = 0;
        let sy = 0;
        let sw = live.canvas.width;
        let sh = live.canvas.height;
        if (sourceAspect > targetAspect) {
          sw = live.canvas.height * targetAspect;
          sx = (live.canvas.width - sw) / 2;
        } else if (sourceAspect < targetAspect) {
          sh = live.canvas.width / targetAspect;
          sy = (live.canvas.height - sh) / 2;
        }
        const liveX = args.u * live.canvas.width;
        const liveY = args.v * live.canvas.height;
        const exportX = Math.round(((liveX - sx) / sw) * exported.canvas.width);
        const exportY = Math.round(((liveY - sy) / sh) * exported.canvas.height);

        const sampleMean = (
          ctx: CanvasRenderingContext2D,
          cx: number,
          cy: number,
          half: number,
        ) => {
          const x = Math.max(0, Math.round(cx - half));
          const y = Math.max(0, Math.round(cy - half));
          const data = ctx.getImageData(x, y, half * 2, half * 2).data;
          let r = 0;
          let g = 0;
          let b = 0;
          const count = data.length / 4;
          for (let offset = 0; offset < data.length; offset += 4) {
            r += data[offset];
            g += data[offset + 1];
            b += data[offset + 2];
          }
          return [r / count, g / count, b / count];
        };

        const findProbes = (
          ctx: CanvasRenderingContext2D,
          x0: number,
          y0: number,
          w: number,
          h: number,
        ) => {
          const data = ctx.getImageData(x0, y0, w, h).data;
          const found = args.probes.map(() => false);
          for (let offset = 0; offset < data.length; offset += 4) {
            for (let p = 0; p < args.probes.length; p += 1) {
              if (found[p]) continue;
              const probe = args.probes[p];
              const dr = data[offset] - probe[0];
              const dg = data[offset + 1] - probe[1];
              const db = data[offset + 2] - probe[2];
              if (Math.sqrt(dr * dr + dg * dg + db * db) <= 40) found[p] = true;
            }
          }
          return found;
        };

        const bottomY = Math.floor(exported.canvas.height * 0.8);
        return {
          exportWidth: exported.canvas.width,
          exportHeight: exported.canvas.height,
          exportX,
          exportY,
          chipRegionExport: sampleMean(exported.ctx, exportX, exportY, 12),
          chipRegionLive: sampleMean(
            live.ctx,
            (liveX / live.canvas.width) * live.canvas.width,
            liveY,
            (12 * live.canvas.width) / exported.canvas.width,
          ),
          bakedLegendProbes: findProbes(
            exported.ctx,
            0,
            bottomY,
            exported.canvas.width,
            exported.canvas.height - bottomY,
          ),
          upperFrameProbes: findProbes(
            exported.ctx,
            0,
            0,
            exported.canvas.width,
            Math.floor(exported.canvas.height * 0.6),
          ),
        };
      },
      { liveDataUrl: liveDataUrl!, u, v, probes },
    );

    // (a) The exporter's own legend is present, at its own position, once.
    expect(report.bakedLegendProbes, 'baked legend missing from the export').toEqual([true, true]);
    // The chip lives in the upper frame; its ramp colors must appear nowhere there.
    expect(
      report.upperFrameProbes,
      `legend ramp colors leaked into the composed frame above the baked legend (chip at ${report.exportX},${report.exportY})`,
    ).toEqual([false, false]);

    // (b) The export at the chip's mapped position matches the chip-free
    // canvas capture: the chip contributed no pixels.
    const distance = Math.hypot(
      report.chipRegionExport[0] - report.chipRegionLive[0],
      report.chipRegionExport[1] - report.chipRegionLive[1],
      report.chipRegionExport[2] - report.chipRegionLive[2],
    );
    expect(distance, 'export differs from the chip-free canvas capture at the chip position').toBeLessThanOrEqual(24);

    await page.keyboard.press('Escape');
  });

  // ── 5. Type floor and target sizes (§2) ───────────────────────────────────

  test('chip and expanded chip meet the 11 px type floor and 32 px target floor', async ({ page }) => {
    await openLegendViewer(page, LEGEND_FIXTURES.radarPtype, COLLAPSED);
    const chipRoot = chip(page);
    await expect(chipRoot).toBeVisible({ timeout: 20_000 });

    const compactRuns = await textRuns(page, '[data-testid="compact-legend-chip"]');
    expect(compactRuns.length).toBeGreaterThan(0);
    for (const run of compactRuns) {
      expect(run.size, `chip text "${run.text}" font-size`).toBeGreaterThanOrEqual(11);
    }

    const toggleBox = await chipRoot.getByTestId('compact-legend-chip-toggle').boundingBox();
    expect(Math.min(toggleBox!.width, toggleBox!.height)).toBeGreaterThanOrEqual(32);

    await chipRoot.getByTestId('compact-legend-chip-toggle').click();
    await expect(page.getByTestId('compact-legend-chip-panel')).toBeVisible();
    const panelRuns = await textRuns(page, '[data-testid="compact-legend-chip-panel"]');
    expect(panelRuns.length).toBeGreaterThan(0);
    for (const run of panelRuns) {
      expect(run.size, `expanded chip text "${run.text}" font-size`).toBeGreaterThanOrEqual(11);
    }
  });

  test.describe('coarse pointer', () => {
    test.use({ hasTouch: true, viewport: { width: 1024, height: 900 } });

    test('chip targets are ≥44 px on coarse pointers', async ({ page }) => {
      // The chip follows the existing preference; on a touch tablet the
      // layout default is off, so opt in explicitly rather than change it.
      await page.addInitScript(() => localStorage.setItem('twf.map.legend_visible', 'true'));
      await stubLegendRoutes(page, LEGEND_FIXTURES.continuous);
      await page.goto(legendViewerUrl(LEGEND_FIXTURES.continuous));
      await expect(page.getByTestId('viewer-suspense-skeleton')).toBeHidden({ timeout: 30_000 });
      await expect(page.getByTestId('viewer-rail')).toBeVisible({ timeout: 20_000 });
      await expect(page.getByTestId('viewer-rail')).toHaveAttribute('data-rail-state', 'collapsed');

      const toggle = chip(page).getByTestId('compact-legend-chip-toggle');
      await expect(toggle).toBeVisible({ timeout: 20_000 });
      const box = await toggle.boundingBox();
      expect(Math.min(box!.width, box!.height)).toBeGreaterThanOrEqual(44);

      const legendItem = await page.getByTestId('rail-collapsed-legend').boundingBox();
      expect(Math.min(legendItem!.width, legendItem!.height)).toBeGreaterThanOrEqual(44);
    });
  });
});
