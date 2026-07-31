/**
 * Phase 8 mobile contract suite
 * (docs/plans/2026-07-29-map-viewer-redesign-phase-8-mobile.md, Task 3).
 *
 * MAP_VIEWER_REDESIGN §8: three mobile states at 390×844.
 *   State A — 52 px bar, map ≥72% of the viewport with a source badge and the
 *             compact legend chip, a 64 px one-row timeline, an 84 px sheet
 *             peek that owns run state only.
 *   State B — scrubbing raises a day strip and a bottom-center target readout
 *             as absolutely-positioned overlays; the map element's box is
 *             bit-identical through the whole gesture; both revert 400 ms
 *             after pointerup.
 *   State C — sheet snaps peek → half → full with ≥180 px of map still
 *             visible, the timeline hidden (visibility, never layout) and its
 *             valid time moved into the sheet header, sections in rail order
 *             Source → View → Legend, every control ≥44 px.
 *
 * Fixed, repo-owned fixtures only (viewer-timeline.fixtures / viewer-legend.
 * fixtures) — never live weather.
 *
 * DOM contract under test:
 *   [data-testid="viewer-mobile-bar"]            52 px State A bar
 *   [data-testid="mobile-bar-search"]            ⌕ → sheet half + region search
 *   [data-testid="mobile-bar-overflow-menu"]     ••• menu (decision 3 items)
 *   [data-testid="viewer-map-slot"]              THE map element (§9 geometry)
 *   [data-testid="map-source-badge"]             MODEL · Variable
 *   [data-testid="mobile-timeline-row"]          64 px one-row timeline
 *   [data-testid="mobile-day-strip"]             State B overlay
 *   [data-testid="mobile-scrub-readout"]         State B overlay
 *   [data-testid="mobile-sheet"][data-snap]      peek | half | full
 *   [data-testid="mobile-sheet-peek"]            run state only
 *   [data-testid="mobile-sheet-valid-time"]      timeline's readout at half/full
 *   [data-testid="mobile-section-heading"]       Source → View → Legend
 */
import { test, expect, type Page } from '@playwright/test';

import {
  FIXTURES,
  stubTimelineRoutes,
  timelineViewerUrl,
  type TimelineFixture,
} from './viewer-timeline.fixtures';
import {
  LEGEND_FIXTURES,
  legendViewerUrl,
  ptypeOrderOf,
  stubLegendRoutes,
  type LegendFixture,
} from './viewer-legend.fixtures';

/** §8 / §9: the mobile geometry contract is specified at 390×844. */
const VIEWPORT = { width: 390, height: 844 };
/** `hasTouch` flips `(pointer: coarse)` in headless Chromium (design-tokens §). */
test.use({ viewport: VIEWPORT, hasTouch: true });

const BAR_PX = 52;
const TIMELINE_PX = 64;
const PEEK_PX = 84;
/** §8 State A arithmetic: 844 − 52 − (64 + 84) = 644 = 76.3%. */
const MAP_FLOOR_FRACTION = 0.72;
/** §8 State C: the map strip above the full snap never drops below this. */
const MIN_VISIBLE_MAP_PX = 180;
/** §8 State B: overlays revert this long after pointerup. */
const REVERT_MS = 400;

type Box = { x: number; y: number; width: number; height: number };

async function boxOf(page: Page, testId: string): Promise<Box> {
  const box = await page.getByTestId(testId).boundingBox();
  expect(box, `${testId} has no box`).not.toBeNull();
  return box as Box;
}

async function settleViewer(page: Page) {
  await expect(page.getByTestId('viewer-suspense-skeleton')).toBeHidden({ timeout: 30_000 });
  await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });
  await expect(page.getByTestId('viewer-mobile-bar')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('mobile-sheet')).toHaveAttribute('data-snap', 'peek');
}

async function openTimelineViewer(page: Page, fixture: TimelineFixture, fh = 0) {
  await page.addInitScript(() => window.localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  await stubTimelineRoutes(page, fixture);
  await page.goto(timelineViewerUrl(fixture, fh));
  await settleViewer(page);
}

async function openLegendViewer(page: Page, fixture: LegendFixture) {
  await page.addInitScript(() => window.localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  await stubLegendRoutes(page, fixture);
  await page.goto(legendViewerUrl(fixture));
  await settleViewer(page);
}

/** Click the sheet handle, which advances peek → half → full → peek. */
async function snapTo(page: Page, snap: 'peek' | 'half' | 'full') {
  const sheet = page.getByTestId('mobile-sheet');
  for (let guard = 0; guard < 4; guard += 1) {
    if ((await sheet.getAttribute('data-snap')) === snap) return;
    await page.getByTestId('mobile-sheet-handle').click();
  }
  await expect(sheet).toHaveAttribute('data-snap', snap);
}

function permalinkParams(page: Page): Record<string, string | null> {
  const url = new URL(page.url());
  const out: Record<string, string | null> = {};
  for (const key of ['m', 'r', 'v', 'fh', 'reg', 'lat', 'lon', 'z']) {
    out[key] = url.searchParams.get(key);
  }
  return out;
}

// ── 1. §9 geometry at rest ──────────────────────────────────────────────────

test.describe('§9 mobile geometry at rest', () => {
  test('map is ≥72% of the viewport with a 52/64/84 chrome stack', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.gfsLong);

    const bar = await boxOf(page, 'viewer-mobile-bar');
    const map = await boxOf(page, 'viewer-map-slot');
    const timeline = await boxOf(page, 'mobile-timeline-row');
    const sheet = await boxOf(page, 'mobile-sheet');

    expect(Math.abs(bar.height - BAR_PX), `bar height ${bar.height}`).toBeLessThanOrEqual(1);
    expect(Math.abs(timeline.height - TIMELINE_PX), `timeline height ${timeline.height}`).toBeLessThanOrEqual(1);
    expect(Math.abs(sheet.height - PEEK_PX), `peek height ${sheet.height}`).toBeLessThanOrEqual(1);

    const grabber = page.getByTestId('mobile-sheet-grabber');
    await expect(grabber).toBeVisible();
    const grabberBox = await grabber.boundingBox();
    const grabberStyle = await grabber.evaluate((element) => {
      const computed = getComputedStyle(element);
      return {
        backgroundColor: computed.backgroundColor,
        boxShadow: computed.boxShadow,
      };
    });
    expect(grabberBox).not.toBeNull();
    expect(grabberBox!.width).toBeGreaterThanOrEqual(44);
    expect(grabberBox!.height).toBeGreaterThanOrEqual(5);
    expect(grabberBox!.y - sheet.y).toBeLessThanOrEqual(12);
    expect(Math.abs(
      (grabberBox!.x + grabberBox!.width / 2) - (sheet.x + sheet.width / 2),
    )).toBeLessThanOrEqual(1);
    expect(grabberStyle.backgroundColor).not.toBe('rgba(0, 0, 0, 0)');
    expect(grabberStyle.boxShadow).not.toBe('none');

    // The permanent mobile timestamp is retired in favor of the scrub pill,
    // leaving the track to consume the full row after the play button.
    const track = await boxOf(page, 'timeline-track');
    expect(track.width).toBeGreaterThanOrEqual(280);
    expect(track.x + track.width).toBeGreaterThanOrEqual(VIEWPORT.width - 10);

    expect(map.height / VIEWPORT.height, `map fraction ${map.height / VIEWPORT.height}`)
      .toBeGreaterThanOrEqual(MAP_FLOOR_FRACTION);
    // Nothing occludes the map: it ends exactly where the timeline begins.
    expect(Math.abs((map.y + map.height) - timeline.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(map.y - (bar.y + bar.height))).toBeLessThanOrEqual(1);

    const noOverflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    expect(noOverflow, 'horizontal overflow on the mobile viewer').toBe(true);
  });
});

// ── 2. §9 no reflow through the scrub gesture ───────────────────────────────

test.describe('§9 State B — no layout reflow during the gesture', () => {
  test('map box is identical before, during and after a scrub', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.gfsLong);

    const readout = page.getByTestId('mobile-scrub-readout');
    await expect(page.getByTestId('mobile-day-strip')).toHaveCount(0);
    await expect(readout).toBeHidden();

    const before = await boxOf(page, 'viewer-map-slot');
    const track = await boxOf(page, 'timeline-track');
    const y = track.y + track.height / 2;

    await page.mouse.move(track.x + track.width * 0.3, y);
    await page.mouse.down();
    const atDown = await boxOf(page, 'viewer-map-slot');
    await expect(page.getByTestId('mobile-day-strip')).toHaveCount(0);
    await expect(readout).toBeVisible();

    const duringSamples: Box[] = [];
    for (const fraction of [0.45, 0.6, 0.75]) {
      await page.mouse.move(track.x + track.width * fraction, y);
      duringSamples.push(await boxOf(page, 'viewer-map-slot'));
    }

    // The one retained scrub pill must be genuinely ON SCREEN, not merely
    // laid out there.
    // `getBoundingClientRect()` reports the geometric box even when an
    // ancestor's overflow clips the element away — an IntersectionObserver
    // ratio does not, so this is what catches a clipped overlay.
    for (const testId of ['mobile-scrub-readout']) {
      const ratio = await page.evaluate((id) => new Promise<number>((resolve) => {
        const element = document.querySelector(`[data-testid="${id}"]`);
        if (!element) { resolve(-1); return; }
        const observer = new IntersectionObserver((entries) => {
          observer.disconnect();
          resolve(entries[0]?.intersectionRatio ?? -1);
        });
        observer.observe(element);
      }), testId);
      expect(ratio, `${testId} is clipped or off-screen (ratio ${ratio})`).toBeGreaterThan(0.95);
    }

    // The readout sits over the map, horizontally centered, above the timeline.
    const readoutBox = (await readout.boundingBox())!;
    const timeline = await boxOf(page, 'mobile-timeline-row');
    expect(readoutBox.y).toBeGreaterThanOrEqual(before.y);
    expect(readoutBox.y + readoutBox.height).toBeLessThanOrEqual(timeline.y + 1);
    const readoutCenter = readoutBox.x + readoutBox.width / 2;
    expect(Math.abs(readoutCenter - (before.x + before.width / 2))).toBeLessThanOrEqual(2);

    await page.mouse.up();
    const atUp = await boxOf(page, 'viewer-map-slot');

    await page.waitForTimeout(REVERT_MS + 250);
    const afterRevert = await boxOf(page, 'viewer-map-slot');
    await expect(page.getByTestId('mobile-day-strip')).toHaveCount(0);
    await expect(readout).toBeHidden();

    for (const [label, sample] of [
      ['pointerdown', atDown],
      ...duringSamples.map((sample, index) => [`move ${index}`, sample] as const),
      ['pointerup', atUp],
      ['after revert', afterRevert],
    ] as Array<readonly [string, Box]>) {
      expect(sample.height, `map height changed at ${label}`).toBe(before.height);
      expect(sample.y, `map top changed at ${label}`).toBe(before.y);
    }
  });
});

// ── 3. §8 State C — sheet snaps ─────────────────────────────────────────────

test.describe('§8 State C — sheet snaps', () => {
  test('peek → half → full keeps the map box and the section order', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.gfsLong);

    const atPeek = await boxOf(page, 'viewer-map-slot');
    const timelineRow = page.getByTestId('mobile-timeline-row');
    await expect(timelineRow).toBeVisible();

    await snapTo(page, 'half');
    const atHalf = await boxOf(page, 'viewer-map-slot');
    expect(atHalf.height).toBe(atPeek.height);
    await expect(timelineRow).toHaveCSS('visibility', 'hidden');
    await expect(page.getByTestId('mobile-sheet-valid-time')).toBeVisible();

    await snapTo(page, 'full');
    const atFull = await boxOf(page, 'viewer-map-slot');
    expect(atFull.height).toBe(atPeek.height);
    expect(atFull.y).toBe(atPeek.y);
    await expect(timelineRow).toHaveCSS('visibility', 'hidden');

    const sheet = await boxOf(page, 'mobile-sheet');
    const bar = await boxOf(page, 'viewer-mobile-bar');
    const visibleMapStrip = sheet.y - (bar.y + bar.height);
    expect(visibleMapStrip, `only ${visibleMapStrip}px of map above the full snap`)
      .toBeGreaterThanOrEqual(MIN_VISIBLE_MAP_PX);

    const headings = await page.getByTestId('mobile-section-heading').allInnerTexts();
    expect(headings.map((text) => text.trim())).toEqual(['Source', 'View', 'Legend']);
  });

  test('every sheet control meets the 44 px floor', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.gfsLong);
    await snapTo(page, 'full');

    const violations = await page.evaluate(() => {
      const root = document.querySelector('[data-testid="mobile-sheet"]');
      if (!root) return ['no sheet'];
      const selector = 'button, a[href], [role="button"], [role="slider"], input, select';
      const out: string[] = [];
      for (const element of Array.from(root.querySelectorAll(selector))) {
        if (element.closest('[data-audit-exception]')) continue;
        if (element.closest('[class*="sr-only"]')) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        if (rect.width < 44 || rect.height < 44) {
          out.push(`${element.tagName}.${(element as HTMLElement).className}: ${Math.round(rect.width)}×${Math.round(rect.height)}`);
        }
      }
      return out;
    });
    expect(violations, violations.join('\n')).toEqual([]);
  });
});

// ── 4. §8 no duplication ────────────────────────────────────────────────────

test.describe('§8 no duplication', () => {
  /**
   * The badge speaks the UI variable label, not the manifest `display_name`:
   * the frontend `VARIABLE_UI_OVERRIDES` table is the single source of truth
   * for viewer-facing variable names, and `tmp2m` renders as "Surface Temp".
   */
  const GFS_LONG_UI_VARIABLE_LABEL = 'Surface Temp';

  test('badge owns source identity and the peek owns run state only', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.gfsLong);

    const badge = page.getByTestId('map-source-badge');
    await expect(badge).toBeVisible();
    expect((await badge.innerText()).replace(/\s+/g, ' ').trim())
      .toBe(`${FIXTURES.gfsLong.model.toUpperCase()} · ${GFS_LONG_UI_VARIABLE_LABEL}`);

    // The peek carries the COMPACT freshness ("18 min ago") so its second line
    // has the full width for the availability string; the "Updated " prefix
    // lives in the expanded Source section, which has room for it.
    const peekText = (await page.getByTestId('mobile-sheet-peek').innerText()).replace(/\s+/g, ' ');
    expect(peekText).toMatch(/just now|min ago|hr ago| d ago/);
    expect(peekText).not.toMatch(/Updated/);
    expect(peekText).toMatch(/hours available|Ready through|Building|frames/);
    expect(peekText).not.toContain(GFS_LONG_UI_VARIABLE_LABEL);
    expect(peekText).not.toContain(FIXTURES.gfsLong.displayName);
    expect(peekText).not.toMatch(new RegExp(FIXTURES.gfsLong.model, 'i'));

    await expect(page.getByTestId('mobile-run-step-older')).toBeVisible();
    await expect(page.getByTestId('mobile-run-step-newer')).toBeVisible();
  });
});

// ── 5. §5.4 / §9 truthfulness ───────────────────────────────────────────────

test.describe('§9 run-state truthfulness on the peek', () => {
  test('an incomplete run speaks building vocabulary, never "hours available"', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.building);

    const peekText = (await page.getByTestId('mobile-sheet-peek').innerText()).replace(/\s+/g, ' ');
    expect(peekText).toMatch(/Building/i);

    const pageText = await page.evaluate(() => document.body.innerText);
    expect(pageText).not.toMatch(/hours available/i);
    expect(pageText).not.toMatch(/hrs available/i);
  });

  test('an observed selection speaks observed vocabulary', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.observed);

    const peekText = (await page.getByTestId('mobile-sheet-peek').innerText()).replace(/\s+/g, ' ');
    expect(peekText).toMatch(/frames observed|No observations loaded/);
    expect(peekText).not.toMatch(/Building|Ready through|hours available/i);
  });
});

// ── 6. §7 chip on mobile ────────────────────────────────────────────────────

test.describe('§8 State A — compact legend chip', () => {
  const fixture = LEGEND_FIXTURES.radarPtype;

  test('is visible over the map with every ramp and stays export-excluded', async ({ page }) => {
    await openLegendViewer(page, fixture);

    const chip = page.getByTestId('compact-legend-chip');
    await expect(chip).toBeVisible();
    await expect(chip).toHaveAttribute('data-export-exclude', 'legend-chip');

    const map = await boxOf(page, 'viewer-map-slot');
    const chipBox = (await chip.boundingBox())!;
    expect(chipBox.y).toBeGreaterThanOrEqual(map.y - 1);
    expect(chipBox.y + chipBox.height).toBeLessThanOrEqual(map.y + map.height + 1);

    const ramps = chip.locator('[data-testid="legend-ramp"]');
    await expect(ramps).toHaveCount(ptypeOrderOf(fixture).length);

    await chip.getByTestId('compact-legend-chip-toggle').click();
    await expect(chip.getByTestId('compact-legend-chip-panel')).toBeVisible();
    await expect(chip).toHaveAttribute('data-expanded', 'true');
  });
});

// ── 7. §8 State A — the 52 px bar ───────────────────────────────────────────

test.describe('§8 State A — mobile bar', () => {
  test('carries the product destinations, Share, and overflow without a location-search button', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.gfsLong);

    const bar = page.getByTestId('viewer-mobile-bar');
    expect(Math.abs((await bar.boundingBox())!.height - BAR_PX)).toBeLessThanOrEqual(1);
    await expect(bar.getByRole('button', { name: 'Share' })).toBeVisible();
    const logo = bar.getByRole('img', { name: 'CartoSky' });
    expect((await logo.boundingBox())!.height).toBeGreaterThanOrEqual(39);
    const switcher = page.getByTestId('viewer-mobile-section-switcher');
    await expect(switcher.getByRole('link', { name: 'Viewer' })).toHaveAttribute('href', '/viewer');
    await expect(switcher.getByRole('link', { name: 'Forecast' })).toHaveAttribute('href', '/forecast');
    await expect(switcher.getByRole('link', { name: 'Climate' })).toHaveAttribute('href', '/climate');
    await expect(page.getByTestId('mobile-bar-search')).toHaveCount(0);

    await page.getByTestId('mobile-bar-overflow-trigger').click();
    const items = await page.getByTestId('mobile-bar-overflow-menu').getByRole('menuitem').allInnerTexts();
    expect(items.map((text) => text.trim())).toEqual([
      'Send feedback',
      'Compare',
      // Skew-T Phase 3: the sounding item lives in the overflow on mobile but is
      // hidden entirely when the active model has no stacks (this fixture is non-HRRR).
      'Replay tour',
      'Attribution',
      'Sign in',
    ]);
    await page.keyboard.press('Escape');

  });

  test('mobile zoom controls clear the source card and the scrubber centers beside Play', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('twf.map.zoom_controls_visible', 'true'));
    await openTimelineViewer(page, FIXTURES.gfsLong);

    const geometry = await page.evaluate(() => {
      const rect = (selector: string) => document.querySelector(selector)?.getBoundingClientRect() ?? null;
      return {
        badge: rect('[data-testid="map-source-badge"]'),
        zoom: rect('[aria-label="Zoom in"]'),
        play: rect('[data-testid="timeline-play-button"]'),
        track: rect('[data-testid="timeline-track"]'),
        thumb: rect('[data-testid="timeline-thumb"]'),
      };
    });
    expect(geometry.badge).not.toBeNull();
    expect(geometry.zoom).not.toBeNull();
    expect(geometry.zoom!.top).toBeGreaterThanOrEqual(geometry.badge!.bottom + 8);
    expect(Math.abs(
      (geometry.track!.top + geometry.track!.height / 2)
      - (geometry.play!.top + geometry.play!.height / 2),
    )).toBeLessThanOrEqual(1);
    expect(geometry.thumb!.left).toBeGreaterThanOrEqual(geometry.play!.right + 2);
  });
});

// ── 8. §9 / §12 preservation ────────────────────────────────────────────────

test.describe('§9 preservation', () => {
  test('sheet snaps change no permalink param and fire no frame request', async ({ page }) => {
    // A short run so the idle warm-up finishes; the §12 claim is that a CHROME
    // move fetches nothing, not that the viewer never prefetches.
    await openTimelineViewer(page, FIXTURES.building);

    const frameRequests: string[] = [];
    page.on('request', (request) => {
      const url = request.url();
      if (/fh\d{3}\.l0\.u16\.bin/.test(url)) frameRequests.push(url);
    });
    // Wait for frame traffic to go quiet before the chrome interaction.
    for (let quiet = 0; quiet < 20; quiet += 1) {
      const seen = frameRequests.length;
      await page.waitForTimeout(250);
      if (frameRequests.length === seen && quiet > 2) break;
    }
    const before = permalinkParams(page);
    frameRequests.length = 0;

    await snapTo(page, 'half');
    await snapTo(page, 'full');
    await snapTo(page, 'peek');
    await page.waitForTimeout(400);

    expect(permalinkParams(page)).toEqual(before);
    expect(frameRequests, `sheet snaps fetched ${frameRequests.length} frames`).toEqual([]);
  });

  test('a scrub preserves every permalink param except the forecast hour', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.gfsLong);
    const before = permalinkParams(page);

    const track = await boxOf(page, 'timeline-track');
    const y = track.y + track.height / 2;
    await page.mouse.move(track.x + track.width * 0.2, y);
    await page.mouse.down();
    await page.mouse.move(track.x + track.width * 0.5, y);
    await page.mouse.up();
    await page.waitForTimeout(600);

    const after = permalinkParams(page);
    for (const key of ['m', 'r', 'v', 'reg', 'lat', 'lon', 'z']) {
      expect(after[key], `${key} changed through the scrub`).toBe(before[key]);
    }
    expect(after.fh).not.toBeNull();
  });
});

// ── 9. §8 run state is readable, not clipped ────────────────────────────────

test.describe('§5.4 run state readability', () => {
  /**
   * The single-line peek truncated the availability string mid-number at
   * 390 px ("Building · ready through FH 3 of…"), losing the one part of the
   * §5.4 vocabulary that carries the actual number. The peek is now two lines
   * and the expanded Source section repeats the full pair.
   */
  for (const [label, fixture, pattern] of [
    ['building', FIXTURES.building, /Building · ready through FH 3 of 12/],
    ['observed', FIXTURES.observed, /frames observed|No observations loaded/],
  ] as Array<readonly [string, TimelineFixture, RegExp]>) {
    test(`the ${label} availability line is never clipped on the peek`, async ({ page }) => {
      await openTimelineViewer(page, fixture);

      const availability = page.getByTestId('mobile-peek-availability');
      await expect(availability).toBeVisible();
      await expect(availability).toHaveText(pattern);

      const overflow = await availability.evaluate((element) => ({
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
      }));
      expect(
        overflow.scrollWidth,
        `availability clipped: scrollWidth ${overflow.scrollWidth} > clientWidth ${overflow.clientWidth}`,
      ).toBeLessThanOrEqual(overflow.clientWidth);

      // Line 1 (run + compact freshness) must fit on its own row too.
      const runLabel = page.getByTestId('mobile-peek-run');
      const runOverflow = await runLabel.evaluate((element) => ({
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
      }));
      expect(runOverflow.scrollWidth).toBeLessThanOrEqual(runOverflow.clientWidth);
    });
  }

  test('the expanded Source section repeats the full freshness and availability', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.building);

    const peekAvailability = (await page.getByTestId('mobile-peek-availability').innerText()).trim();

    await snapTo(page, 'half');
    const source = page.getByTestId('mobile-sheet-source');
    await expect(source.getByTestId('mobile-sheet-freshness')).toHaveText(/^Updated /);
    await expect(source.getByTestId('mobile-sheet-availability')).toHaveText(peekAvailability);

    // §5.4 vocabulary is unchanged — an incomplete run still never claims
    // "hours available" anywhere on the page, expanded sheet included.
    const pageText = await page.evaluate(() => document.body.innerText);
    expect(pageText).not.toMatch(/hours available|hrs available/i);
  });
});

// ── 10. §9 top-chrome seam ──────────────────────────────────────────────────

test.describe('§9 top chrome hands off without a seam', () => {
  test('the boot shell header is the same 52 px the React bar is', async ({ page }) => {
    await stubTimelineRoutes(page, FIXTURES.gfsLong);
    // Abort every SCRIPT request so the bundle never mounts. index.html's
    // inline boot script is not a request, so the boot shell still resolves
    // its geometry — which is exactly the surface under test.
    await page.route('**/*', (route) => (
      route.request().resourceType() === 'script' ? route.abort() : route.continue()
    ));
    await page.goto(timelineViewerUrl(FIXTURES.gfsLong, 0), { waitUntil: 'domcontentloaded' });

    const bootHeader = page.locator('[data-viewer-boot-header]');
    await expect(bootHeader).toBeVisible();
    const bootHeight = (await bootHeader.boundingBox())!.height;
    expect(Math.abs(bootHeight - BAR_PX), `boot header height ${bootHeight}`).toBeLessThanOrEqual(1);
  });

  test('nothing paints a header strip between the bar and the map', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.gfsLong);

    const bar = await boxOf(page, 'viewer-mobile-bar');
    expect(Math.abs(bar.height - BAR_PX)).toBeLessThanOrEqual(1);

    // AppLayout's persistent backdrop row sits behind the bar; if it is taller
    // than the bar it shows as a strip below the bar's bottom edge.
    const fallbackRowHeight = await page.evaluate(() => {
      const row = document.querySelector('.viewer-chrome-fallback-row');
      return row ? row.getBoundingClientRect().height : null;
    });
    if (fallbackRowHeight !== null) {
      expect(Math.abs(fallbackRowHeight - BAR_PX), `backdrop row ${fallbackRowHeight}px vs ${BAR_PX}px bar`)
        .toBeLessThanOrEqual(1);
    }

    // Exactly one visible header edge: probe the band the seam occupied.
    const probes = await page.evaluate((barPx) => {
      const out: Array<{ y: number; header: boolean; map: boolean; tag: string | null }> = [];
      for (const y of [barPx + 1, barPx + 2, barPx + 3, barPx + 4]) {
        const element = document.elementFromPoint(195, y);
        out.push({
          y,
          header: Boolean(element?.closest('header')),
          map: Boolean(element?.closest('[data-testid="viewer-map-slot"]')),
          tag: element?.tagName ?? null,
        });
      }
      return out;
    }, BAR_PX);

    for (const probe of probes) {
      expect(probe.header, `a header still paints at y=${probe.y} (${probe.tag})`).toBe(false);
      expect(probe.map, `the map does not reach y=${probe.y} (${probe.tag})`).toBe(true);
    }
  });
});

// ── 11. §8 short viewports ──────────────────────────────────────────────────

test.describe('§8 short viewport — the 72% floor still holds at 660', () => {
  test.use({ viewport: { width: 390, height: 660 }, hasTouch: true });

  test('the shrunken peek is floored, never rounded past the budget', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.gfsLong);

    const map = await boxOf(page, 'viewer-map-slot');
    const timeline = await boxOf(page, 'mobile-timeline-row');
    const sheet = await boxOf(page, 'mobile-sheet');

    // Budget = 660 × 0.28 − 52 = 132.8. Rounding the 68.8 px peek allowance up
    // to 69 overspends by 0.2 and yields a 0.7197 fraction; flooring to 68
    // yields 0.7212.
    expect(sheet.height, `peek ${sheet.height} overspent the 132.8px budget`).toBeLessThanOrEqual(68);
    expect(sheet.height).toBeGreaterThanOrEqual(64);
    expect(timeline.height).toBe(TIMELINE_PX);

    const fraction = map.height / 660;
    expect(fraction, `map fraction ${fraction} at 390×660`).toBeGreaterThanOrEqual(MAP_FLOOR_FRACTION);
  });
});

test.describe('§8 short viewport — below ~586 the control minimums win', () => {
  test.use({ viewport: { width: 390, height: 568 }, hasTouch: true });

  /**
   * DOCUMENTED COLLISION (lib/viewer-mobile.ts): bar + minPeek + minTimeline =
   * 52 + 64 + 48 = 164 px, so the 72 % floor is only reachable for
   * viewportHeight ≥ 164 / 0.28 ≈ 586. At 568 the two constraints are mutually
   * exclusive and §8's stated priority — usable controls — wins. This test
   * asserts the behavior that IS achievable, not the impossible fraction.
   */
  test('the peek and timeline hold their minimums instead of collapsing', async ({ page }) => {
    await openTimelineViewer(page, FIXTURES.gfsLong);

    const bar = await boxOf(page, 'viewer-mobile-bar');
    const map = await boxOf(page, 'viewer-map-slot');
    const timeline = await boxOf(page, 'mobile-timeline-row');
    const sheet = await boxOf(page, 'mobile-sheet');

    expect(Math.abs(bar.height - BAR_PX)).toBeLessThanOrEqual(1);
    expect(timeline.height, `timeline ${timeline.height} below the 48 px minimum`).toBeGreaterThanOrEqual(48);
    expect(sheet.height, `peek ${sheet.height} below the 64 px minimum`).toBeGreaterThanOrEqual(64);

    // The chrome spends exactly its hard minimum and nothing more.
    const chrome = bar.height + timeline.height + sheet.height;
    expect(Math.abs(chrome - 164), `chrome spend ${chrome} ≠ the 164 px minimum`).toBeLessThanOrEqual(1);
    // …and the map gets everything that is left, still stacked without gaps.
    expect(Math.abs((map.y + map.height) - timeline.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(map.height - (568 - 164))).toBeLessThanOrEqual(1);
  });
});

// ── 12. §8 the mobile tour completes ────────────────────────────────────────

test.describe('§8 mobile tour', () => {
  /**
   * Regression: steps targeting mobile-region-row / mobile-view-section /
   * mobile-legend-section sat below the half snap's scroll fold (measured at
   * y = 884 / 855 / 1210 against an 844 px viewport). The tooltip is
   * positioned from the target's rect, so its Next/Done button landed outside
   * the viewport and the tour could not be advanced or finished.
   */
  test('walks Get started → every Next → Done with every step on screen', async ({ page }) => {
    await stubTimelineRoutes(page, FIXTURES.gfsLong);
    // No tour-completed key: useTour auto-starts 1.5 s after the map is ready.
    await page.goto(timelineViewerUrl(FIXTURES.gfsLong, 0));
    await expect(page.getByTestId('viewer-suspense-skeleton')).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId('viewer-mobile-bar')).toBeVisible({ timeout: 20_000 });

    const welcome = page.getByRole('dialog', { name: 'Welcome to CartoSky Map Viewer' });
    await expect(welcome).toBeVisible({ timeout: 20_000 });
    await welcome.getByRole('button', { name: 'Get started →' }).click();

    const tooltip = page.getByTestId('tour-tooltip');
    const highlight = page.getByTestId('tour-highlight');
    const seen: string[] = [];
    const bodies: string[] = [];

    for (let guard = 0; guard < 20; guard += 1) {
      await expect(tooltip).toBeVisible({ timeout: 10_000 });
      const label = (await tooltip.getAttribute('aria-label')) ?? `step ${guard}`;
      // Let the sheet's 320 ms height transition and the section scroll settle
      // before measuring — the overlay re-measures on scroll and on a timer.
      await page.waitForTimeout(700);

      // 1. The spotlight target is actually on screen, not below the fold.
      await expect(highlight, `${label}: no highlight`).toBeVisible();
      const highlightBox = (await highlight.boundingBox())!;
      const visibleTop = Math.max(0, highlightBox.y);
      const visibleBottom = Math.min(VIEWPORT.height, highlightBox.y + highlightBox.height);
      const visibleFraction = Math.max(0, visibleBottom - visibleTop) / highlightBox.height;
      expect(highlightBox.y, `${label}: highlight top ${highlightBox.y} is below the fold`)
        .toBeLessThan(VIEWPORT.height);
      expect(highlightBox.y, `${label}: highlight top ${highlightBox.y} is above the fold`)
        .toBeGreaterThan(-highlightBox.height);
      expect(
        visibleFraction,
        `${label}: only ${Math.round(visibleFraction * 100)}% of the highlight is on screen (rect y=${highlightBox.y} h=${highlightBox.height})`,
      ).toBeGreaterThanOrEqual(0.6);

      // 2. The tooltip — and therefore its Next/Done button — is FULLY inside
      //    the viewport. This is what the off-screen rect broke.
      const tooltipBox = (await tooltip.boundingBox())!;
      expect(tooltipBox.y, `${label}: tooltip top ${tooltipBox.y}`).toBeGreaterThanOrEqual(-1);
      expect(tooltipBox.x, `${label}: tooltip left ${tooltipBox.x}`).toBeGreaterThanOrEqual(-1);
      expect(
        tooltipBox.y + tooltipBox.height,
        `${label}: tooltip bottom ${tooltipBox.y + tooltipBox.height} exceeds the ${VIEWPORT.height}px viewport`,
      ).toBeLessThanOrEqual(VIEWPORT.height + 1);
      expect(tooltipBox.x + tooltipBox.width).toBeLessThanOrEqual(VIEWPORT.width + 1);

      seen.push(label);
      bodies.push((await tooltip.innerText()).replace(/\s+/g, ' '));

      const advance = tooltip.getByRole('button', { name: /^(Next|Done)$/ });
      const isDone = (await advance.innerText()).trim() === 'Done';
      await advance.click();
      if (isDone) break;
    }

    // Every content step was walked, and the tour actually finished.
    expect(seen.length, `only reached ${seen.length} steps: ${seen.join(' | ')}`).toBe(8);
    expect(bodies.join(' ')).not.toMatch(/magnifier in the top bar/i);
    expect(seen[seen.length - 1]).toContain('8 of 8');
    await expect(tooltip).toBeHidden();
    await expect(page.getByRole('status')).toContainText('all set', { timeout: 5_000 });
  });
});
