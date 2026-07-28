/**
 * Phase 2 first-paint loading-scope contract
 * (docs/plans/2026-07-28-map-viewer-redesign-phase-2-first-paint.md, Task 1).
 *
 * While the requested grid frame binary is deferred, the viewer must paint a
 * viewer-shaped shell, keep the Product control usable, gate only the map
 * behind a map-local scrim, and report truthful frame/build progress. The
 * scrim may not disappear before the existing requested-frame readiness gate
 * (`grid_frame_ready`) resolves.
 */
import { test, expect, type Page } from '@playwright/test';

import {
  FIRST_PAINT_EXPECTED_BUILDING_TEXT,
  createDeferred,
  stubViewerFirstPaintRoutes,
} from './viewer-first-paint.fixtures';

const VIEWER_URL = '/viewer?m=gfs&r=latest&v=tmp2m&fh=0&reg=conus&screenshot=1';
const SCRIM_TESTID = 'viewer-initial-map-scrim';

test.describe('Viewer first paint', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium-only contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop loading-scope contract.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  test('static document paints a viewer-shaped shell for /viewer and keeps the generic boot card elsewhere', async ({ page }) => {
    // Defer the app bundle so the pre-React document state is observable
    // deterministically instead of racing the module graph.
    let moduleGate = createDeferred();
    await page.route('**/src/main.tsx', async (route) => {
      const gate = moduleGate;
      await gate.promise;
      // The page may have navigated away while the gate was held.
      await route.continue().catch(() => {});
    });

    await page.goto(VIEWER_URL, { waitUntil: 'commit' });

    const viewerShell = page.locator('[data-viewer-boot-shell]');
    await expect(viewerShell).toBeVisible();
    // Viewer-shaped: header surface, map surface, and timeline placeholder.
    await expect(page.locator('[data-viewer-boot-header]')).toBeVisible();
    await expect(page.locator('[data-viewer-boot-timeline]')).toBeVisible();
    // Dark surface — no light/white flash layer on the boot path.
    const shellIsDark = await viewerShell.evaluate((element) => {
      const surface = window.getComputedStyle(element);
      const body = window.getComputedStyle(document.body);
      const parse = (value: string) => {
        const match = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        if (!match) return null;
        return (Number(match[1]) + Number(match[2]) + Number(match[3])) / 3;
      };
      const surfaceLuma = parse(surface.backgroundColor ?? '');
      const bodyLuma = parse(body.backgroundColor ?? '');
      return (surfaceLuma === null || surfaceLuma < 64) && bodyLuma !== null && bodyLuma < 64;
    });
    expect(shellIsDark).toBe(true);
    // The generic boot card must not show on /viewer.
    await expect(page.locator('.cartosky-boot-card')).toBeHidden();

    const firstGate = moduleGate;

    // Non-viewer routes keep the existing generic boot card.
    moduleGate = createDeferred();
    firstGate.resolve();
    await page.goto('/roadmap', { waitUntil: 'commit' });
    await expect(page.locator('.cartosky-boot-card')).toBeVisible();
    await expect(page.locator('[data-viewer-boot-shell]')).toBeHidden();
    moduleGate.resolve();
  });

  test('keeps Product usable and gates only the map while the requested frame is blocked', async ({ page }) => {
    const frameGate = createDeferred();
    await stubViewerFirstPaintRoutes(page, { gridFrameGate: () => frameGate.promise });

    await page.goto(VIEWER_URL);

    // The real toolbar mounts with the frame still blocked.
    const productTrigger = page.locator('header').getByRole('button', { name: 'GFS' });
    await expect(productTrigger).toBeVisible({ timeout: 20_000 });
    await expect(productTrigger).toBeEnabled();

    // No fixed, viewport-covering loading status may exist once the real
    // viewer shell has mounted — loading state must be confined to the map.
    const coveringStatuses = await page.evaluate(() => {
      return Array.from(document.querySelectorAll('[role="status"]'))
        .filter((element) => {
          const style = window.getComputedStyle(element);
          if (style.display === 'none' || style.visibility === 'hidden') return false;
          if (style.position !== 'fixed') return false;
          const rect = element.getBoundingClientRect();
          return rect.width >= window.innerWidth - 1 && rect.height >= window.innerHeight - 1;
        })
        .map((element) => (element.getAttribute('aria-label') ?? element.textContent ?? '').trim());
    });
    expect(coveringStatuses).toEqual([]);

    // The map-local scrim is visible and truthful while the frame is blocked.
    const scrim = page.getByTestId(SCRIM_TESTID);
    await expect(scrim).toBeVisible();
    // Truthful stage copy: names the current work (the deterministic settled
    // stage under a blocked frame is downloading/drawing the requested FH),
    // not an indeterminate generic loader.
    await expect(scrim).toContainText('Downloading and drawing FH 0', { timeout: 20_000 });

    // Product opens its picker before the frame is released.
    await productTrigger.click({ timeout: 5_000 });
    await expect(page.getByLabel('Model picker')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByLabel('Model picker')).toBeHidden();

    // Hit-testing: header and timeline stack above the scrim; the map center
    // resolves to the scrim, proving only map interaction is gated.
    const hits = await page.evaluate((scrimTestId) => {
      const hitAt = (x: number, y: number) => document.elementFromPoint(x, y);
      const headerRect = document.querySelector('header')?.getBoundingClientRect();
      const timelineRect = document
        .querySelector('[data-tour-target="forecast-scrubber"]')
        ?.getBoundingClientRect();
      const headerHit = headerRect
        ? hitAt(headerRect.left + headerRect.width / 2, headerRect.top + headerRect.height / 2)
        : null;
      const timelineHit = timelineRect
        ? hitAt(timelineRect.left + timelineRect.width / 2, timelineRect.top + timelineRect.height / 2)
        : null;
      const mapHit = hitAt(window.innerWidth / 2, window.innerHeight / 2);
      return {
        headerInHeader: Boolean(headerHit?.closest('header')),
        headerOnScrim: Boolean(headerHit?.closest(`[data-testid="${scrimTestId}"]`)),
        timelineFound: Boolean(timelineRect),
        timelineAboveScrim: Boolean(timelineHit) && !timelineHit?.closest(`[data-testid="${scrimTestId}"]`),
        mapOnScrim: Boolean(mapHit?.closest(`[data-testid="${scrimTestId}"]`)),
      };
    }, SCRIM_TESTID);
    expect(hits.headerInHeader).toBe(true);
    expect(hits.headerOnScrim).toBe(false);
    expect(hits.timelineFound).toBe(true);
    expect(hits.timelineAboveScrim).toBe(true);
    expect(hits.mapOnScrim).toBe(true);

    // The requested-frame readiness gate has not resolved yet.
    const gateEventsWhileBlocked = await page.evaluate(() =>
      ((window as typeof window & { __cartoskyGateLog?: Array<{ event: string }> }).__cartoskyGateLog ?? [])
        .map((entry) => entry.event),
    );
    expect(gateEventsWhileBlocked).not.toContain('grid_frame_ready');

    // Record when the scrim leaves the DOM so ordering against the readiness
    // gate log is provable, then release the frame.
    await page.evaluate((scrimTestId) => {
      const w = window as typeof window & { __scrimRemovedAtMs?: number | null };
      w.__scrimRemovedAtMs = null;
      const observer = new MutationObserver(() => {
        if (!document.querySelector(`[data-testid="${scrimTestId}"]`)) {
          w.__scrimRemovedAtMs = performance.now();
          observer.disconnect();
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }, SCRIM_TESTID);

    frameGate.resolve();

    await expect(scrim).toBeHidden({ timeout: 20_000 });
    const ordering = await page.evaluate(() => {
      const w = window as typeof window & {
        __cartoskyGateLog?: Array<{ event: string; tMs: number }>;
        __scrimRemovedAtMs?: number | null;
      };
      const ready = (w.__cartoskyGateLog ?? []).find((entry) => entry.event === 'grid_frame_ready') ?? null;
      return { readyTMs: ready?.tMs ?? null, scrimRemovedAtMs: w.__scrimRemovedAtMs ?? null };
    });
    expect(ordering.readyTMs).not.toBeNull();
    expect(ordering.scrimRemovedAtMs).not.toBeNull();
    // grid_frame_ready happens before (or in the same tick as) scrim removal.
    expect(ordering.scrimRemovedAtMs!).toBeGreaterThanOrEqual(ordering.readyTMs! - 1);
  });

  test('map scrim reuses the timeline\'s Building available/total hrs values for an incomplete run', async ({ page }) => {
    const frameGate = createDeferred();
    await stubViewerFirstPaintRoutes(page, { gridFrameGate: () => frameGate.promise });

    await page.goto(VIEWER_URL);

    const scrim = page.getByTestId(SCRIM_TESTID);
    await expect(scrim).toBeVisible({ timeout: 20_000 });

    // The timeline's own strip renders the canonical values…
    const timelineBuilding = page
      .locator('[data-tour-target="forecast-scrubber"]')
      .getByText(FIRST_PAINT_EXPECTED_BUILDING_TEXT, { exact: true });
    await expect(timelineBuilding).toBeVisible({ timeout: 20_000 });

    // …and the scrim shows the same clamped available/total values.
    await expect(scrim.getByText(FIRST_PAINT_EXPECTED_BUILDING_TEXT, { exact: true })).toBeVisible();
    await expect(scrim).toContainText('Building');

    frameGate.resolve();
    await expect(scrim).toBeHidden({ timeout: 20_000 });
  });
});
