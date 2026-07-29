/**
 * Phase 6 chrome contract suite
 * (docs/plans/2026-07-29-map-viewer-redesign-phase-6-chrome.md, Task 3).
 *
 * MAP_VIEWER_REDESIGN §6: 48 px top bar + SOURCE/VIEW rail. Fixed, repo-owned
 * fixtures only (colormap plumbing for the full catalog + legend, timeline
 * plumbing for the incomplete-run truthfulness case) — never live weather.
 *
 * DOM contract under test (implemented by ViewerTopBar / ViewerRail):
 *   header                                        the 48 px bar
 *   [data-testid="viewer-section-switcher"]       Viewer · Forecast · Climate
 *   [data-testid="viewer-overflow-trigger"]       `•••`
 *   [data-testid="viewer-overflow-menu"]          overflow menu panel
 *   [data-testid="viewer-rail"][data-rail-state]  the rail, expanded|collapsed
 *   [data-testid="rail-source"] / [data-testid="rail-view"]      sections
 *   [data-testid="rail-availability"]             §5.4 truthful availability line
 *   [data-testid="rail-region-value"]             region display name as text
 *   [data-testid="rail-opacity-value"]            opacity % readout
 *   [data-legend-mount="rail"]                    reserved Phase 7 legend mount
 *   [data-testid="rail-collapsed-source"|"rail-collapsed-view"]  collapsed items
 *   [data-testid="rail-collapse-toggle"|"rail-expand-toggle"]    width toggle
 */
import { test, expect, type Page } from '@playwright/test';

import { COLORMAP_VIEW, stubViewerColormapRoutes } from './viewer-colormap.fixtures';
import { FIXTURES, stubTimelineRoutes, timelineViewerUrl } from './viewer-timeline.fixtures';

const VIEWER_URL =
  `/viewer?m=gfs&r=latest&v=tmp2m&fh=0&reg=conus&lat=${COLORMAP_VIEW.lat}&lon=${COLORMAP_VIEW.lon}&z=${COLORMAP_VIEW.zoom}`;

const RAIL_EXPANDED_PX = 288;
const RAIL_COLLAPSED_PX = 72;

/** Same formula as lib/viewer-rail.ts (§6.4): available = viewport − 288. */
function expectedDefaultWidth(viewportWidth: number): number {
  if (viewportWidth < 768) return 0;
  return viewportWidth - RAIL_EXPANDED_PX >= 1024 ? RAIL_EXPANDED_PX : RAIL_COLLAPSED_PX;
}

async function openViewer(page: Page, url = VIEWER_URL) {
  await stubViewerColormapRoutes(page);
  await page.goto(url);
  await expect(page.getByTestId('viewer-suspense-skeleton')).toBeHidden({ timeout: 30_000 });
  await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });
  if ((page.viewportSize()?.width ?? 0) >= 768) {
    await expect(page.getByTestId('viewer-rail')).toBeVisible({ timeout: 20_000 });
  }
}

/** Reads the DOM directly: `boundingBox()` auto-waits, which would swallow the
 *  "rail is absent" case the mobile-threshold assertion depends on. */
async function railWidth(page: Page): Promise<number> {
  return page.evaluate(() => {
    const element = document.querySelector('[data-testid="viewer-rail"]');
    return element ? Math.round(element.getBoundingClientRect().width) : 0;
  });
}

async function expandRail(page: Page) {
  if ((await page.getByTestId('viewer-rail').getAttribute('data-rail-state')) === 'collapsed') {
    await page.getByTestId('rail-expand-toggle').click();
  }
  await expect(page.getByTestId('viewer-rail')).toHaveAttribute('data-rail-state', 'expanded');
}

async function collapseRail(page: Page) {
  if ((await page.getByTestId('viewer-rail').getAttribute('data-rail-state')) === 'expanded') {
    await page.getByTestId('rail-collapse-toggle').click();
  }
  await expect(page.getByTestId('viewer-rail')).toHaveAttribute('data-rail-state', 'collapsed');
}

test.describe('Viewer chrome — top bar and rail (Phase 6)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Pinned Chromium contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop contract projects.');
    await page.addInitScript(() => {
      localStorage.setItem('csky_viewer_tour_v1', 'completed');
    });
  });

  // ── 1. Top bar ────────────────────────────────────────────────────────────
  test('bar is 48 px with flat destination links, separate Compare and Share actions, and no selectors', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openViewer(page);

    const header = page.getByTestId('viewer-top-bar');
    const headerBox = await header.boundingBox();
    expect(headerBox).not.toBeNull();
    expect(Math.abs(headerBox!.height - 48)).toBeLessThanOrEqual(1);

    // Logo → "/"
    const brandLink = header.getByRole('link', { name: 'CartoSky' });
    await expect(brandLink).toHaveAttribute('href', '/');
    const brandImage = brandLink.locator('img');
    await expect(brandImage).toBeVisible();
    const brandBox = await brandImage.boundingBox();
    expect(brandBox).not.toBeNull();
    expect(brandBox!.height).toBeGreaterThanOrEqual(35);

    // Viewer / Forecast / Climate are independent destinations, not a
    // segmented control that reads as three modes of the same product.
    const switcher = page.getByTestId('viewer-section-switcher');
    await expect(switcher.getByRole('link', { name: 'Viewer' })).toHaveAttribute('href', '/viewer');
    await expect(switcher.getByRole('link', { name: 'Forecast' })).toHaveAttribute('href', '/forecast');
    await expect(switcher.getByRole('link', { name: 'Climate' })).toHaveAttribute('href', '/climate');
    await expect(switcher.getByRole('link', { name: 'Viewer' })).toHaveAttribute('aria-current', 'page');
    const navStyle = await switcher.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        borderWidth: style.borderTopWidth,
        backgroundColor: style.backgroundColor,
      };
    });
    expect(navStyle.borderWidth).toBe('0px');
    expect(navStyle.backgroundColor).toBe('rgba(0, 0, 0, 0)');
    const activeStyle = await switcher.getByRole('link', { name: 'Viewer' }).evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        borderBottomWidth: style.borderBottomWidth,
        borderBottomColor: style.borderBottomColor,
      };
    });
    expect(parseFloat(activeStyle.borderBottomWidth)).toBeGreaterThan(0);
    expect(activeStyle.borderBottomColor).not.toBe('rgba(0, 0, 0, 0)');

    // Compare and Share are peer actions in the bar.
    await expect(header.getByRole('link', { name: 'Compare' })).toBeVisible();
    await expect(header.getByRole('link', { name: 'Compare' })).toHaveAttribute('href', /\/compare/);
    await expect(header.getByRole('button', { name: 'Share' })).toBeVisible();

    // Overflow menu contents.
    await page.getByTestId('viewer-overflow-trigger').click();
    const menu = page.getByTestId('viewer-overflow-menu');
    await expect(menu).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'Send feedback' })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'Compare' })).toHaveCount(0);
    await expect(menu.getByRole('menuitem', { name: 'Replay tour' })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'Keyboard shortcuts' })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'Attribution' })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: /Sign in|Account/ })).toBeVisible();
    await page.keyboard.press('Escape');

    // The selectors left the bar entirely — they live in the rail now.
    await expect(header.locator('[data-tour-target="product-variable-run"]')).toHaveCount(0);
    await expect(header.getByRole('button', { name: 'GFS' })).toHaveCount(0);
    await expect(header.getByRole('button', { name: 'Surface Temp' })).toHaveCount(0);
  });

  // ── 2. Rail expanded ──────────────────────────────────────────────────────
  test('expanded rail (1440×900) carries SOURCE, VIEW and the legend mount', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openViewer(page);

    const rail = page.getByTestId('viewer-rail');
    await expect(rail).toHaveAttribute('data-rail-state', 'expanded');
    expect(await railWidth(page)).toBe(RAIL_EXPANDED_PX);

    // SOURCE: Model, Variable, Run triggers + the one retained freshness line.
    const source = page.getByTestId('rail-source');
    await expect(source.getByRole('button', { name: 'GFS' })).toBeVisible();
    await expect(source.getByRole('button', { name: 'Surface Temp' })).toBeVisible();
    await expect(source.getByTestId('rail-run-trigger')).toBeVisible();
    await expect(source.getByTestId('rail-run-step-older')).toBeVisible();
    await expect(source.getByTestId('rail-run-step-newer')).toBeVisible();
    await expect(source.getByTestId('rail-freshness')).toContainText(/^Updated /);
    await expect(source.getByTestId('rail-availability')).toHaveCount(0);

    // VIEW: region value readable without opening anything (§6.2).
    const view = page.getByTestId('rail-view');
    await expect(view.getByTestId('rail-region-value')).toHaveText('CONUS');
    await expect(view.getByRole('button', { name: /City labels/i })).toBeVisible();
    await expect(view.getByRole('button', { name: /Dark basemap/i })).toBeVisible();
    await expect(view.getByTestId('rail-opacity-value')).toHaveText(/^\d{1,3}%$/);

    // Legend mount hosts the existing MapLegend rendering.
    const mount = page.locator('[data-legend-mount="rail"]');
    await expect(mount).toBeVisible();
    await expect(mount.getByRole('complementary', { name: 'Map legend' })).toBeVisible();
  });

  test('expanded rail removes decorative label icons and separates Source from View', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openViewer(page);

    for (const testId of [
      'rail-source-heading',
      'rail-model-label',
      'rail-variable-label',
      'rail-run-label',
      'rail-view-heading',
    ]) {
      const label = page.getByTestId(testId);
      await expect(label).toBeVisible();
      await expect(label.locator('svg')).toHaveCount(0);
    }

    for (const testId of ['rail-source-heading', 'rail-view-heading']) {
      const headingStyle = await page.getByTestId(testId).evaluate((element) => {
        const computed = getComputedStyle(element);
        return {
          color: computed.color,
          fontFamily: computed.fontFamily,
        };
      });
      const channels = headingStyle.color.match(/\d+(?:\.\d+)?/g)?.map(Number) ?? [];
      expect(channels[1]).toBeGreaterThan(channels[0]);
      expect(channels[2]).toBeGreaterThan(channels[0]);
      const fontFamily = headingStyle.fontFamily;
      expect(fontFamily).not.toMatch(/IBM Plex Mono/i);
    }

    const sourceCard = page.getByTestId('rail-source-fields-card');
    await expect(sourceCard).toBeVisible();
    await expect(sourceCard.getByTestId('rail-model-label')).toBeVisible();
    await expect(sourceCard.getByTestId('rail-variable-label')).toBeVisible();
    await expect(sourceCard.getByTestId('rail-run-label')).toBeVisible();
    const sourceCardStyle = await sourceCard.evaluate((element) => {
      const computed = getComputedStyle(element);
      return {
        borderWidth: computed.borderTopWidth,
        backgroundColor: computed.backgroundColor,
        backgroundImage: computed.backgroundImage,
        backdropFilter: computed.backdropFilter,
      };
    });
    expect(sourceCardStyle.borderWidth).toBe('1px');
    expect(sourceCardStyle.backgroundColor).not.toBe('rgba(0, 0, 0, 0)');
    expect(sourceCardStyle.backgroundImage).not.toBe('none');
    expect(sourceCardStyle.backdropFilter).toContain('blur');

    const source = await page.getByTestId('rail-source').boundingBox();
    const divider = await page.getByTestId('rail-section-divider').boundingBox();
    const view = await page.getByTestId('rail-view').boundingBox();
    expect(source).not.toBeNull();
    expect(divider).not.toBeNull();
    expect(view).not.toBeNull();
    expect(divider!.y).toBeGreaterThanOrEqual(source!.y + source!.height);
    expect(view!.y).toBeGreaterThanOrEqual(divider!.y);
    expect(divider!.width).toBeGreaterThan(220);

    await expect(page.getByTestId('rail-collapse-toggle').locator('.lucide-chevron-left')).toBeVisible();
    await expect(page.getByTestId('rail-collapse-toggle').locator('.lucide-panel-left-close')).toHaveCount(0);

    const expandedRail = await page.getByTestId('viewer-rail').boundingBox();
    const collapseToggle = await page.getByTestId('rail-collapse-toggle').boundingBox();
    expect(expandedRail).not.toBeNull();
    expect(collapseToggle).not.toBeNull();
    expect(collapseToggle!.x).toBeGreaterThanOrEqual(expandedRail!.x + expandedRail!.width - 1);
    expect(collapseToggle!.x + collapseToggle!.width).toBeGreaterThan(expandedRail!.x + expandedRail!.width + 20);

    await collapseRail(page);
    const collapsedRail = await page.getByTestId('viewer-rail').boundingBox();
    const expandToggle = await page.getByTestId('rail-expand-toggle').boundingBox();
    expect(collapsedRail).not.toBeNull();
    expect(expandToggle).not.toBeNull();
    expect(expandToggle!.x).toBeGreaterThanOrEqual(collapsedRail!.x + collapsedRail!.width - 1);
    expect(expandToggle!.x + expandToggle!.width).toBeGreaterThan(collapsedRail!.x + collapsedRail!.width + 20);
  });

  test('region is self-labeled and its panel stays fully inside the viewport', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openViewer(page);

    const row = page.getByTestId('rail-region-row');
    await expect(row.locator(':scope > span', { hasText: /^Region$/ })).toHaveCount(0);
    const trigger = row.getByRole('button', { name: 'Region: CONUS' });
    await expect(trigger).toContainText(/Region.*CONUS/);
    await expect(trigger.getByTestId('region-prefix-dot')).toBeVisible();
    await trigger.click();

    const panel = page.getByTestId('region-picker-panel');
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(1440);
  });

  test('View controls are flat switches without On/Off text or an opacity card', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openViewer(page);

    for (const testId of ['rail-toggle-city-labels', 'rail-toggle-dark-basemap', 'rail-toggle-zoom-controls']) {
      const row = page.getByTestId(testId);
      const button = row.getByRole('button');
      await expect(button).toBeVisible();
      await expect(button).not.toContainText(/\b(?:On|Off)\b/);
      await expect(button.getByTestId('rail-toggle-switch')).toBeVisible();
      const style = await button.evaluate((element) => {
        const computed = getComputedStyle(element);
        return {
          borderWidth: computed.borderTopWidth,
          backgroundColor: computed.backgroundColor,
        };
      });
      expect(style.borderWidth).toBe('0px');
      expect(style.backgroundColor).toBe('rgba(0, 0, 0, 0)');
    }

    const opacity = page.getByTestId('rail-opacity-control');
    const opacityStyle = await opacity.evaluate((element) => {
      const computed = getComputedStyle(element);
      return {
        borderWidth: computed.borderTopWidth,
        backgroundColor: computed.backgroundColor,
      };
    });
    expect(opacityStyle.borderWidth).toBe('0px');
    expect(opacityStyle.backgroundColor).toBe('rgba(0, 0, 0, 0)');
  });

  // ── 3. Rail collapsed ─────────────────────────────────────────────────────
  test('collapsed rail (1200×800) shows captioned icons, expands to a section, keeps the map legend', async ({ page }) => {
    await page.setViewportSize({ width: 1200, height: 800 });
    await openViewer(page);

    const rail = page.getByTestId('viewer-rail');
    await expect(rail).toHaveAttribute('data-rail-state', 'collapsed');
    expect(await railWidth(page)).toBe(RAIL_COLLAPSED_PX);

    // Icon + ≥11 px caption per section (§6.3 / §2.2).
    for (const testId of ['rail-collapsed-source', 'rail-collapsed-view']) {
      const caption = page.getByTestId(testId).getByTestId('rail-collapsed-caption');
      await expect(caption).toBeVisible();
      const fontSize = await caption.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
      expect(fontSize, `${testId} caption font-size`).toBeGreaterThanOrEqual(11);
    }
    await expect(page.getByTestId('rail-collapsed-source').getByTestId('rail-collapsed-caption')).toHaveText('Source');
    await expect(page.getByTestId('rail-collapsed-view').getByTestId('rail-collapsed-caption')).toHaveText('View');

    // §6.3: the current map legend survives the collapse.
    await expect(page.getByRole('complementary', { name: 'Map legend' })).toBeVisible();
    const legendBox = await page.getByRole('complementary', { name: 'Map legend' }).boundingBox();
    expect(legendBox!.x).toBeGreaterThan(RAIL_COLLAPSED_PX);

    // Clicking a caption expands scrolled to that section.
    await page.getByTestId('rail-collapsed-view').click();
    await expect(rail).toHaveAttribute('data-rail-state', 'expanded');
    expect(await railWidth(page)).toBe(RAIL_EXPANDED_PX);
    await expect(page.getByTestId('rail-view')).toBeInViewport();
  });

  // ── 4. Responsive default from map width ──────────────────────────────────
  test('default rail state is computed from map width, not viewport width', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openViewer(page);

    for (const width of [1440, 1320, 1200, 800]) {
      await page.setViewportSize({ width, height: 900 });
      await expect
        .poll(() => railWidth(page), { message: `rail width at ${width}px viewport` })
        .toBe(expectedDefaultWidth(width));
    }

    // §6.4 threshold moved 639 → 767: below it the mobile sheet owns the chrome.
    await page.setViewportSize({ width: 700, height: 900 });
    await expect(page.getByTestId('viewer-rail')).toHaveCount(0);
    const headerBox = await page.getByTestId('viewer-top-bar').boundingBox();
    expect(Math.abs(headerBox!.height - 56)).toBeLessThanOrEqual(1);
  });

  // ── 5. Override keyed by breakpoint class ─────────────────────────────────
  test('rail override is stored per breakpoint class and survives reload', async ({ page }) => {
    await page.setViewportSize({ width: 1200, height: 800 });
    await openViewer(page);

    const readKeys = () => page.evaluate(() => ({
      wide: localStorage.getItem('twf.rail.mode.wide'),
      narrow: localStorage.getItem('twf.rail.mode.narrow'),
    }));

    await expandRail(page);
    expect(await readKeys()).toEqual({ wide: null, narrow: 'expanded' });

    await page.reload();
    await expect(page.getByTestId('viewer-suspense-skeleton')).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId('viewer-rail')).toHaveAttribute('data-rail-state', 'expanded');

    // The narrow preference must not follow the user to a wide display.
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.getByTestId('viewer-rail')).toHaveAttribute('data-rail-state', 'expanded');
    expect((await readKeys()).wide).toBeNull();

    await collapseRail(page);
    expect(await readKeys()).toEqual({ wide: 'collapsed', narrow: 'expanded' });
  });

  // ── 6. Geometry seam ──────────────────────────────────────────────────────
  test('rail offsets the map and the timeline, and toggling it fetches no grid binaries', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openViewer(page);
    await expandRail(page);

    const canvas = page.locator('canvas.maplibregl-canvas').first();
    const expandedBox = await canvas.boundingBox();
    expect(Math.abs(expandedBox!.x - RAIL_EXPANDED_PX)).toBeLessThanOrEqual(2);

    // Timeline centers inside the map area, not the viewport (§6 geometry seam).
    const timelineBox = await page.getByTestId('timeline-panel').boundingBox();
    const timelineCenter = timelineBox!.x + timelineBox!.width / 2;
    expect(Math.abs(timelineCenter - (RAIL_EXPANDED_PX + (1440 - RAIL_EXPANDED_PX) / 2))).toBeLessThanOrEqual(4);

    // §12: a rail toggle is presentation only — no grid binary may be refetched.
    const gridRequests: string[] = [];
    page.on('request', (request) => {
      if (/\/api\/v4\/grid\/.*\.bin/.test(request.url())) {
        gridRequests.push(request.url());
      }
    });

    await collapseRail(page);
    // Generous budget: under full-suite parallel load the first post-toggle
    // evaluate round-trip on the swiftshader map can take >5 s by itself.
    await expect
      .poll(async () => Math.round((await canvas.boundingBox())!.x), { timeout: 20_000 })
      .toBeLessThanOrEqual(RAIL_COLLAPSED_PX + 2);
    const collapsedBox = await canvas.boundingBox();
    expect(collapsedBox!.width).toBeGreaterThan(expandedBox!.width);

    await page.waitForTimeout(750);
    expect(gridRequests, `grid binaries refetched on rail toggle: ${gridRequests.join(', ')}`).toEqual([]);
  });

  // ── 7. Availability truthfulness ──────────────────────────────────────────
  test('incomplete run keeps Updated plus one visible Building indicator', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await stubTimelineRoutes(page, FIXTURES.building); // published [0,3], ready 3, horizon 12
    await page.goto(timelineViewerUrl(FIXTURES.building, 0));
    await expect(page.getByTestId('viewer-suspense-skeleton')).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });
    await expandRail(page);

    await expect(page.getByTestId('rail-freshness')).toContainText(/^Updated /);
    await expect(page.getByTestId('rail-availability')).toHaveCount(0);
    await expect(page.getByTestId('timeline-building-status')).toBeVisible();
    await expect(page.getByTestId('timeline-availability')).toHaveClass(/sr-only/);
  });

  // ── 8. Coarse pointer regime ──────────────────────────────────────────────
  test.describe('coarse pointer', () => {
    test.use({ hasTouch: true, viewport: { width: 768, height: 1024 } });

    test('tablet gets the rail with ≥44 px targets and no horizontal overflow', async ({ page }) => {
      await openViewer(page);

      const rail = page.getByTestId('viewer-rail');
      await expect(rail).toBeVisible();
      expect(await railWidth(page)).toBe(RAIL_COLLAPSED_PX);

      const measure = async (locator: ReturnType<Page['locator']>) => {
        const box = await locator.boundingBox();
        return box ? Math.min(box.width, box.height) : 0;
      };
      for (const testId of ['rail-collapsed-source', 'rail-collapsed-view', 'rail-expand-toggle']) {
        expect(await measure(page.getByTestId(testId)), `${testId} min side`).toBeGreaterThanOrEqual(44);
      }
      expect(
        await measure(page.getByTestId('viewer-top-bar').getByRole('button', { name: 'Share' })),
      ).toBeGreaterThanOrEqual(44);
      expect(await measure(page.getByTestId('viewer-overflow-trigger'))).toBeGreaterThanOrEqual(44);

      await expandRail(page);
      for (const testId of ['rail-region-row', 'rail-toggle-city-labels']) {
        expect(await measure(page.getByTestId(testId)), `${testId} min side`).toBeGreaterThanOrEqual(44);
      }

      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1);
    });
  });
});
