/**
 * Phase 5 timeline contract suite
 * (docs/plans/2026-07-28-map-viewer-redesign-phase-5-timeline.md v2, Task 2).
 *
 * DOM contract under test (implemented by components/timeline/TimelineTrack):
 *   [data-testid="timeline-track"]            track container (positions measured against it)
 *   [data-testid="timeline-marker"][data-fh]  one per published frame
 *   [data-testid="timeline-thumb"]            the ONLY focusable thumb, role="slider"
 *   [data-testid="timeline-ghost"]            non-focusable, aria-hidden ghost (drag past boundary)
 *   [data-testid="timeline-solid"]            solid (ready) region
 *   [data-testid="timeline-hatch"]            hatched region, ends at expected_max_fh
 *   [data-testid="timeline-availability"]     mode-truthful availability line
 *   [data-testid="timeline-density-toggle"]   top-edge collapse/expand chevron
 *   [data-testid="shortcut-sheet"]            "?" keyboard sheet
 */
import { test, expect, type Page } from '@playwright/test';

import {
  FIXTURES,
  GFS_LONG_FHS,
  TIMELINE_RUN_ID,
  stubTimelineRoutes,
  timelineViewerUrl,
  trackFrameAssetRequests,
  validTimeIso,
  type TimelineFixture,
} from './viewer-timeline.fixtures';

async function openTimeline(page: Page, fixture: TimelineFixture, fh = 0) {
  await stubTimelineRoutes(page, fixture);
  await page.goto(timelineViewerUrl(fixture, fh));
  // Two-stage readiness: the suspense skeleton (lazy viewer chunk) must have
  // come and gone before the scrim wait means anything.
  await expect(page.getByTestId('viewer-suspense-skeleton')).toBeHidden({ timeout: 30_000 });
  await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });
}

/**
 * Phase 6: the Product/Variable/Run triggers moved out of the header into the
 * SOURCE section of the left rail, which §6.4 collapses by default below
 * 1312 px of viewport width. Selector plumbing only — no gate semantics.
 */
async function expandViewerRail(page: Page) {
  const rail = page.getByTestId('viewer-rail');
  await expect(rail).toBeVisible({ timeout: 20_000 });
  if ((await rail.getAttribute('data-rail-state')) === 'collapsed') {
    await page.getByTestId('rail-expand-toggle').click();
  }
  await expect(rail).toHaveAttribute('data-rail-state', 'expanded');
}

const runMs = (fh: number) => new Date(validTimeIso(fh)).getTime();

test.describe('Viewer timeline (Phase 5)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Pinned Chromium contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop contract projects.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  test('axis is linear in valid time across the GFS 240→246 cadence change', async ({ page }) => {
    await openTimeline(page, FIXTURES.gfsLong);
    const track = page.getByTestId('timeline-track');
    await expect(track).toBeVisible({ timeout: 20_000 });

    const measured = await page.evaluate(() => {
      const trackEl = document.querySelector('[data-testid="timeline-track"]')!;
      const trackRect = trackEl.getBoundingClientRect();
      const markers = Array.from(document.querySelectorAll('[data-testid="timeline-marker"]'));
      return {
        trackLeft: trackRect.left,
        trackWidth: trackRect.width,
        markers: markers.map((el) => ({
          fh: Number(el.getAttribute('data-fh')),
          x: el.getBoundingClientRect().left + el.getBoundingClientRect().width / 2,
        })),
      };
    });
    expect(measured.markers.length).toBe(GFS_LONG_FHS.length);
    // §5.2: dated tick labels actually render (regression guard for the
    // never-attached ResizeObserver found in verification).
    expect(await page.getByTestId('timeline-tick').count()).toBeGreaterThanOrEqual(3);
    const first = runMs(GFS_LONG_FHS[0]);
    const last = runMs(GFS_LONG_FHS[GFS_LONG_FHS.length - 1]);
    for (const marker of measured.markers) {
      const expectedX = measured.trackLeft
        + ((runMs(marker.fh) - first) / (last - first)) * measured.trackWidth;
      expect(
        Math.abs(marker.x - expectedX),
        `marker fh=${marker.fh} at ${marker.x}, expected ${expectedX}`,
      ).toBeLessThanOrEqual(1);
    }
  });

  test('forecast track: solid to ready_through_fh, hatch to expected_max_fh, truthful counts', async ({ page }) => {
    await openTimeline(page, FIXTURES.building); // published [0,3], ready 3, expected_max 12
    const track = page.getByTestId('timeline-track');
    await expect(track).toBeVisible({ timeout: 20_000 });

    const geometry = await page.evaluate(() => {
      const rect = (sel: string) => document.querySelector(sel)?.getBoundingClientRect() ?? null;
      return {
        track: rect('[data-testid="timeline-track"]'),
        solid: rect('[data-testid="timeline-solid"]'),
        hatch: rect('[data-testid="timeline-hatch"]'),
      };
    });
    expect(geometry.solid).not.toBeNull();
    expect(geometry.hatch).not.toBeNull();
    const span = runMs(12) - runMs(0);
    const xAt = (fh: number) => geometry.track!.left + ((runMs(fh) - runMs(0)) / span) * geometry.track!.width;
    // Solid ends at the boundary (fh 3); hatch runs from there to the horizon (fh 12 = track end).
    expect(Math.abs(geometry.solid!.right - xAt(3))).toBeLessThanOrEqual(2);
    expect(Math.abs(geometry.hatch!.left - xAt(3))).toBeLessThanOrEqual(2);
    expect(Math.abs(geometry.hatch!.right - (geometry.track!.left + geometry.track!.width))).toBeLessThanOrEqual(2);

    // Truthfulness: never "hours available" unless literal; availability line present.
    await expect(page.getByTestId('timeline-availability')).toBeVisible();
    const bodyText = await page.evaluate(() => document.body.innerText);
    expect(bodyText).not.toMatch(/hours available/i);
  });

  test('track uses continuous ready/queued regions without per-frame line marks', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await openTimeline(page, FIXTURES.building);
    await expect(page.getByTestId('timeline-solid')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('timeline-hatch')).toBeVisible();

    const markerOpacity = await page.getByTestId('timeline-marker').evaluateAll(
      (elements) => elements.map((element) => getComputedStyle(element).opacity),
    );
    expect(markerOpacity.length).toBeGreaterThan(0);
    expect(markerOpacity.every((opacity) => opacity === '0')).toBe(true);
  });

  test('boundary equals the scalar, not max published FH (out-of-order fixture)', async ({ page }) => {
    await openTimeline(page, FIXTURES.outOfOrder); // published [0,3,9,12], ready 3
    await expect(page.getByTestId('timeline-track')).toBeVisible({ timeout: 20_000 });
    const geometry = await page.evaluate(() => {
      const rect = (sel: string) => document.querySelector(sel)?.getBoundingClientRect() ?? null;
      return { track: rect('[data-testid="timeline-track"]'), solid: rect('[data-testid="timeline-solid"]') };
    });
    const span = runMs(12) - runMs(0);
    const xAt = (fh: number) => geometry.track!.left + ((runMs(fh) - runMs(0)) / span) * geometry.track!.width;
    // If the boundary were max published FH (12) the solid region would span the
    // whole track; it must end at 3.
    expect(Math.abs(geometry.solid!.right - xAt(3))).toBeLessThanOrEqual(2);
  });

  test('legacy manifest (no scalars): solid published range, no boundary UI', async ({ page }) => {
    await openTimeline(page, FIXTURES.legacy);
    await expect(page.getByTestId('timeline-track')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('timeline-hatch')).toHaveCount(0);
    await expect(page.getByTestId('timeline-ghost')).toHaveCount(0);
  });

  test('null ready_through_fh: no solid region, hatch to horizon, selection clamps to earliest published', async ({ page }) => {
    await openTimeline(page, FIXTURES.nullReady); // published [9,12], ready null, horizon 12
    await expect(page.getByTestId('timeline-track')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('timeline-solid')).toHaveCount(0);
    await expect(page.getByTestId('timeline-hatch')).toBeVisible();
    await expect.poll(() => new URL(page.url()).searchParams.get('fh')).toBe('9');
  });

  test('drag past the boundary: ghost + readout; release: snap back; nothing fetched beyond', async ({ page }) => {
    const assets = trackFrameAssetRequests(page, FIXTURES.outOfOrder);
    await openTimeline(page, FIXTURES.outOfOrder); // ready 3; 9 and 12 published but beyond
    const thumb = page.getByTestId('timeline-thumb');
    const track = page.getByTestId('timeline-track');
    await expect(thumb).toBeVisible({ timeout: 20_000 });

    const trackBox = (await track.boundingBox())!;
    const thumbBox = (await thumb.boundingBox())!;
    // Drag from the thumb to 90% of the track (past the fh-3 boundary at 25%).
    await page.mouse.move(thumbBox.x + thumbBox.width / 2, thumbBox.y + thumbBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(trackBox.x + trackBox.width * 0.9, thumbBox.y + thumbBox.height / 2, { steps: 8 });

    await expect(page.getByTestId('timeline-ghost')).toBeVisible();
    await expect(page.getByText(/not published yet/i)).toBeVisible();
    const committedDuringDrag = await thumb.boundingBox();
    const span = runMs(12) - runMs(0);
    const boundaryX = trackBox.x + ((runMs(3) - runMs(0)) / span) * trackBox.width;
    expect(committedDuringDrag!.x + committedDuringDrag!.width / 2).toBeLessThanOrEqual(boundaryX + 2);

    await page.mouse.up();
    await expect(page.getByTestId('timeline-ghost')).toBeHidden();
    await expect.poll(() => new URL(page.url()).searchParams.get('fh')).toBe('3');

    // Keyboard clamps at the boundary too.
    await thumb.focus();
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('End');
    await expect.poll(() => new URL(page.url()).searchParams.get('fh')).toBe('3');

    // Playback stops at the boundary.
    await page.keyboard.press('Space');
    await page.waitForTimeout(2_500);
    const fhNow = Number(new URL(page.url()).searchParams.get('fh'));
    expect(fhNow).toBeLessThanOrEqual(3);

    // No frame asset of any kind fetched beyond the boundary.
    expect(assets.fhs().filter((fh) => fh > 3)).toEqual([]);
  });

  test('cold deep link beyond the boundary clamps and fetches nothing beyond', async ({ page }) => {
    const assets = trackFrameAssetRequests(page, FIXTURES.outOfOrder);
    await openTimeline(page, FIXTURES.outOfOrder, 12); // deep link to published-but-beyond fh
    await expect.poll(() => new URL(page.url()).searchParams.get('fh'), { timeout: 20_000 }).toBe('3');
    expect(assets.fhs().filter((fh) => fh > 3)).toEqual([]);
  });

  test('run switch from longer-ready to shorter: clamps and never fetches stale-boundary frames', async ({ page }) => {
    // Plan Task 2 item 5: latest run is fully ready to 12; the older 06z run
    // is out-of-order published [0,3,9,12] with ready 3. Switching runs while
    // sitting at fh=12 must snap to 3 and fetch nothing beyond 3 for the new
    // run — including during the transition window before its manifest lands.
    const olderRun = '20260728_06z';
    const olderAssets = trackFrameAssetRequests(page, FIXTURES.runSwitch, olderRun);
    await openTimeline(page, FIXTURES.runSwitch, 12);
    await expect.poll(() => new URL(page.url()).searchParams.get('fh'), { timeout: 20_000 }).toBe('12');

    await page.keyboard.press('BracketLeft'); // older run
    await expect.poll(() => new URL(page.url()).searchParams.get('r'), { timeout: 20_000 }).toBe(olderRun);
    await expect.poll(() => new URL(page.url()).searchParams.get('fh'), { timeout: 20_000 }).toBe('3');
    await page.waitForTimeout(1_500); // let any prefetch settle
    expect(olderAssets.fhs().filter((fh) => fh > 3)).toEqual([]);
  });

  test('observed mode: freshness vocabulary only', async ({ page }) => {
    await openTimeline(page, FIXTURES.observed);
    await expect(page.getByTestId('timeline-track')).toBeVisible({ timeout: 20_000 });
    const bodyText = await page.evaluate(() => document.body.innerText);
    expect(bodyText).not.toMatch(/Building|ready through|queued|publish/i);
    await expect(page.getByTestId('timeline-hatch')).toHaveCount(0);
    await expect(page.getByTestId('timeline-availability')).toBeVisible();
  });

  test('valid mode: period and issue time only', async ({ page }) => {
    await openTimeline(page, FIXTURES.valid);
    await expect(page.getByTestId('timeline-availability')).toBeVisible({ timeout: 20_000 });
    const bodyText = await page.evaluate(() => document.body.innerText);
    expect(bodyText).toMatch(/Aug|ASO/i); // the valid period is on screen
    expect(bodyText).not.toMatch(/Building|ready through|queued|forecast hours/i);
    await expect(page.getByTestId('timeline-hatch')).toHaveCount(0);
  });

  test('keyboard map works from a cold load and never pans the map', async ({ page }) => {
    await openTimeline(page, FIXTURES.gfsLong);
    await expect(page.getByTestId('timeline-thumb')).toBeVisible({ timeout: 20_000 });
    const initialUrl = new URL(page.url());
    const initialLat = initialUrl.searchParams.get('lat');
    const initialLon = initialUrl.searchParams.get('lon');

    // Cold arrows: ±1 frame with NO prior click anywhere.
    await page.keyboard.press('ArrowRight');
    await expect.poll(() => new URL(page.url()).searchParams.get('fh'), { timeout: 15_000 }).toBe('3');
    await page.keyboard.press('ArrowLeft');
    await expect.poll(() => new URL(page.url()).searchParams.get('fh')).toBe('0');

    // Shift = +6h, Alt = +24h with directional nearest-frame snapping.
    await page.keyboard.press('Shift+ArrowRight');
    await expect.poll(() => new URL(page.url()).searchParams.get('fh')).toBe('6');
    await page.keyboard.press('Alt+ArrowRight');
    await expect.poll(() => new URL(page.url()).searchParams.get('fh')).toBe('30');
    // Home/End: first frame / ready boundary.
    await page.keyboard.press('End');
    await expect.poll(() => new URL(page.url()).searchParams.get('fh')).toBe('264');
    await page.keyboard.press('Home');
    await expect.poll(() => new URL(page.url()).searchParams.get('fh')).toBe('0');

    // Arrows must not pan the map.
    expect(new URL(page.url()).searchParams.get('lat')).toBe(initialLat);
    expect(new URL(page.url()).searchParams.get('lon')).toBe(initialLon);

    // Space toggles playback (pause control appears), Space again stops.
    await page.keyboard.press('Space');
    await expect(page.getByLabel('Pause animation').first()).toBeVisible({ timeout: 10_000 });
    await page.keyboard.press('Space');
    await expect(page.getByLabel('Play animation').first()).toBeVisible({ timeout: 10_000 });

    // Run stepping: [ = older run, ] = newer, clamped at the ends.
    await page.keyboard.press('BracketLeft');
    await expect.poll(() => new URL(page.url()).searchParams.get('r')).toBe('20260728_06z');
    await page.keyboard.press('BracketRight');
    await expect.poll(() => new URL(page.url()).searchParams.get('r')).toBe('latest');
    await page.keyboard.press('BracketRight'); // clamped at newest
    await expect.poll(() => new URL(page.url()).searchParams.get('r')).toBe('latest');

    // ? opens the shortcut sheet; Escape closes it.
    await page.keyboard.press('Shift+Slash');
    await expect(page.getByTestId('shortcut-sheet')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('shortcut-sheet')).toBeHidden();
  });

  test('shortcuts defer to interactive focus and inputs', async ({ page }) => {
    await openTimeline(page, FIXTURES.gfsLong);
    await expect(page.getByTestId('timeline-thumb')).toBeVisible({ timeout: 20_000 });

    // Space on a focused button activates the button only — playback must not start.
    // Phase 6: the Product trigger lives in the rail SOURCE section, which is
    // collapsed by default at this project's viewport.
    await expandViewerRail(page);
    const productTrigger = page.getByTestId('rail-source').getByRole('button', { name: 'GFS' });
    await productTrigger.focus();
    await page.keyboard.press('Space');
    await expect(page.getByLabel('Model picker')).toBeVisible();
    await expect(page.getByLabel(/pause/i)).toHaveCount(0);
    await page.keyboard.press('Escape');

    // Keys inside a text input do not step frames.
    await productTrigger.click();
    const search = page.getByPlaceholder('Search models…');
    await search.focus();
    const fhBefore = new URL(page.url()).searchParams.get('fh');
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('BracketLeft');
    await page.waitForTimeout(400);
    expect(new URL(page.url()).searchParams.get('fh')).toBe(fhBefore);
    expect(new URL(page.url()).searchParams.get('r')).toBe('latest');
    await page.keyboard.press('Escape');
  });

  test('slider a11y and DOM contract', async ({ page }) => {
    await openTimeline(page, FIXTURES.building);
    const thumb = page.getByTestId('timeline-thumb');
    await expect(thumb).toBeVisible({ timeout: 20_000 });

    await expect(thumb).toHaveAttribute('role', 'slider');
    await expect(thumb).toHaveAttribute('aria-label', /forecast time/i);
    await expect(thumb).toHaveAttribute('aria-valuetext', /FH 0/);
    await expect(thumb).toHaveAttribute('aria-describedby', /.+/);
    await thumb.focus();
    await page.keyboard.press('ArrowRight');
    await expect(thumb).toHaveAttribute('aria-valuetext', /FH 3/);

    // Exactly one focusable thumb; ghost (when present) is aria-hidden and unfocusable.
    const contract = await page.evaluate(() => {
      const thumbs = Array.from(document.querySelectorAll('[data-testid="timeline-thumb"]'))
        .filter((el) => el.getBoundingClientRect().width > 0);
      const ghosts = Array.from(document.querySelectorAll('[data-testid="timeline-ghost"]'));
      return {
        thumbCount: thumbs.length,
        thumbFocusable: thumbs.every((el) => (el as HTMLElement).tabIndex >= 0),
        ghostsHidden: ghosts.every(
          (el) => el.closest('[aria-hidden="true"]') !== null || el.getAttribute('aria-hidden') === 'true',
        ),
        ghostsUnfocusable: ghosts.every((el) => (el as HTMLElement).tabIndex < 0),
      };
    });
    expect(contract.thumbCount).toBe(1);
    expect(contract.thumbFocusable).toBe(true);
    expect(contract.ghostsHidden).toBe(true);
    expect(contract.ghostsUnfocusable).toBe(true);

    // The thumb meets the fine-pointer floor natively (Phase 4 exception closed).
    const box = (await thumb.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(32);
    expect(box.height).toBeGreaterThanOrEqual(32);
  });

  test('top chevron: 100px expanded, 74px collapsed, persisted', async ({ page }) => {
    await openTimeline(page, FIXTURES.gfsLong);
    const toggle = page.getByTestId('timeline-density-toggle');
    await expect(toggle).toBeVisible({ timeout: 20_000 });
    await expect(toggle).toHaveAttribute('aria-label', 'Collapse timeline');

    const expandedToggleGeometry = await page.evaluate(() => {
      const root = document.querySelector('[data-testid="timeline-root"]')?.getBoundingClientRect();
      const toggle = document.querySelector('[data-testid="timeline-density-toggle"]')?.getBoundingClientRect();
      return root && toggle
        ? { rootTop: root.top, toggleTop: toggle.top, toggleBottom: toggle.bottom }
        : null;
    });
    expect(expandedToggleGeometry).not.toBeNull();
    expect(expandedToggleGeometry!.toggleTop).toBeLessThan(expandedToggleGeometry!.rootTop);
    expect(expandedToggleGeometry!.toggleBottom).toBeLessThanOrEqual(expandedToggleGeometry!.rootTop + 1);

    const timelineHeight = () =>
      page.evaluate(
        () => document.querySelector('[data-testid="timeline-root"]')?.getBoundingClientRect().height ?? null,
      );
    expect(Math.round((await timelineHeight())!)).toBe(100); // desktop default: expanded
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-label', 'Expand timeline');
    await expect.poll(async () => Math.round((await timelineHeight())!)).toBe(74);
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem('twf.timeline.density')))
      .toBe('compact');
    await page.reload();
    await expect(page.getByTestId('timeline-density-toggle')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('timeline-density-toggle')).toHaveAttribute('aria-label', 'Expand timeline');
    await expect.poll(async () => Math.round((await timelineHeight())!)).toBe(74);
  });

  test('desktop standard mode is a docked four-row time rail', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await openTimeline(page, FIXTURES.gfsLong);
    await expect(page.getByTestId('timeline-track')).toBeVisible({ timeout: 20_000 });

    const geometry = await page.evaluate(() => {
      const rect = (testId: string) => {
        const element = document.querySelector(`[data-testid="${testId}"]`);
        const box = element?.getBoundingClientRect();
        return box
          ? {
              top: box.top,
              right: box.right,
              bottom: box.bottom,
              left: box.left,
              width: box.width,
              height: box.height,
            }
          : null;
      };
      const railEl = document.querySelector('[data-testid="viewer-rail"]');
      return {
        viewport: { width: window.innerWidth, height: window.innerHeight },
        // Phase 6 geometry seam: the rail offsets the map area the rail-mode
        // timeline spans (0 when no rail is mounted, e.g. mobile).
        railWidth: railEl ? railEl.getBoundingClientRect().width : 0,
        panel: rect('timeline-panel'),
        root: rect('timeline-root'),
        days: rect('timeline-day-band'),
        ticks: rect('timeline-tick-row'),
        track: rect('timeline-track'),
        transport: rect('timeline-transport'),
      };
    });

    expect(geometry.panel).not.toBeNull();
    expect(geometry.root).not.toBeNull();
    expect(geometry.days).not.toBeNull();
    expect(geometry.ticks).not.toBeNull();
    expect(geometry.track).not.toBeNull();
    expect(geometry.transport).not.toBeNull();

    // The mockup's time rail is part of the bottom chrome, not a centered
    // floating pill: it spans its full container and is attached to the bottom
    // edge. Phase 6 re-based that container from the viewport to the MAP AREA
    // (the plan's geometry seam gives the timeline `left: --viewer-rail-width`
    // so it centers within the map, not the viewport). Same assertion strength,
    // new origin.
    const mapAreaWidth = geometry.viewport.width - geometry.railWidth;
    expect(geometry.panel!.width).toBeGreaterThanOrEqual(mapAreaWidth - 2);
    expect(Math.abs(geometry.panel!.left - geometry.railWidth)).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.panel!.right - geometry.viewport.width)).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.panel!.bottom - geometry.viewport.height)).toBeLessThanOrEqual(1);

    // Visual-completion density: 20 px day band + a dedicated 16 px tick
    // gutter + 24 px track + 40 px transport. Rows remain ordered and
    // contiguous, while the transport has room around its 32 px controls.
    expect(Math.round(geometry.root!.height)).toBe(100);
    expect(Math.round(geometry.days!.height)).toBe(20);
    expect(Math.round(geometry.ticks!.height)).toBe(16);
    expect(Math.round(geometry.track!.height)).toBe(24);
    expect(Math.round(geometry.transport!.height)).toBe(40);
    expect(Math.abs(geometry.days!.bottom - geometry.ticks!.top)).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.ticks!.bottom - geometry.track!.top)).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.track!.bottom - geometry.transport!.top)).toBeLessThanOrEqual(1);

    await expect(page.getByTestId('timeline-day-segment')).toHaveCount(12);
    await expect(page.getByTestId('timeline-transport')).toContainText(/Ready|Complete/i);
    await expect(page.getByTestId('timeline-transport')).toContainText(/FH/i);
  });

  test('the first partial GFS day renders a complete compact date inside the day band', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openTimeline(page, FIXTURES.gfsLong);

    const firstSegment = page.getByTestId('timeline-day-segment').first();
    await expect(firstSegment).toBeVisible({ timeout: 20_000 });
    await expect(firstSegment).toHaveText(/^\d{1,2}\/\d{1,2}$/);
    const fit = await firstSegment.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
    expect(fit.scrollWidth).toBeLessThanOrEqual(fit.clientWidth);
  });

  test('calendar date bands change at the viewer local midnight, not at 00Z', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openTimeline(page, FIXTURES.gfsLong);

    const alignment = await page.evaluate(({ firstMs, lastMs }) => {
      const track = document.querySelector('[data-testid="timeline-track"]')!.getBoundingClientRect();
      const segments = Array.from(document.querySelectorAll('[data-testid="timeline-day-segment"]'));
      const starts = segments.slice(1).map((element) => {
        const rect = element.getBoundingClientRect();
        const fraction = (rect.left - track.left) / track.width;
        return firstMs + fraction * (lastMs - firstMs);
      });
      return starts.map((ms) => {
        const date = new Date(ms);
        const minutes = date.getHours() * 60 + date.getMinutes();
        return Math.min(minutes, 24 * 60 - minutes);
      });
    }, {
      firstMs: runMs(GFS_LONG_FHS[0]),
      lastMs: runMs(GFS_LONG_FHS[GFS_LONG_FHS.length - 1]),
    });

    expect(alignment.length).toBeGreaterThan(2);
    // Percent positioning lands on fractional CSS pixels, so converting the
    // measured x back to time can be a minute either side of midnight.
    expect(alignment.every((minutesFromMidnight) => minutesFromMidnight <= 1)).toBe(true);
  });

  test('day-indexed outlook products label the scrubber Day 1, Day 2, Day 3', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await openTimeline(page, FIXTURES.spcDays);

    await expect(page.getByTestId('timeline-day-segment')).toHaveText(['Day 1', 'Day 2', 'Day 3']);
  });

  test('00Z/12Z ticks are prominent above the track and transport controls breathe', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await openTimeline(page, FIXTURES.gfsLong);
    await expect(page.getByTestId('timeline-track')).toBeVisible({ timeout: 20_000 });

    const geometry = await page.evaluate(() => {
      const rect = (element: Element | null) => {
        const box = element?.getBoundingClientRect();
        return box
          ? { top: box.top, right: box.right, bottom: box.bottom, left: box.left, width: box.width, height: box.height }
          : null;
      };
      const ticks = Array.from(document.querySelectorAll('[data-testid="timeline-tick"]')).map((element) => ({
        rect: rect(element),
        major: element.getAttribute('data-major') === 'true',
        label: element.textContent?.trim() ?? '',
      }));
      const transport = document.querySelector('[data-testid="timeline-transport"]');
      const play = document.querySelector('[aria-label="Play animation"]');
      return {
        track: rect(document.querySelector('[data-testid="timeline-track"]')),
        transport: rect(transport),
        play: rect(play),
        ticks,
      };
    });

    expect(geometry.track).not.toBeNull();
    expect(geometry.transport).not.toBeNull();
    expect(geometry.play).not.toBeNull();
    expect(geometry.ticks.length).toBeGreaterThan(4);
    expect(geometry.ticks.every((tick) => tick.rect && tick.rect.bottom <= geometry.track!.top + 1)).toBe(true);

    const major = geometry.ticks.filter((tick) => tick.major);
    const minor = geometry.ticks.filter((tick) => !tick.major);
    expect(major.length).toBeGreaterThan(1);
    expect(minor.length).toBeGreaterThan(1);
    expect(Math.min(...major.map((tick) => tick.rect!.height)))
      .toBeGreaterThan(Math.max(...minor.map((tick) => tick.rect!.height)));
    expect(major.some((tick) => tick.label === '00Z')).toBe(true);
    expect(major.some((tick) => tick.label === '12Z')).toBe(true);
    expect(minor.every((tick) => tick.label === '')).toBe(true);

    expect(geometry.play!.top - geometry.transport!.top).toBeGreaterThanOrEqual(3);
    expect(geometry.transport!.bottom - geometry.play!.bottom).toBeGreaterThanOrEqual(3);
  });

  test('timeline typography and transport match the desktop visual contract', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await openTimeline(page, FIXTURES.gfsLong, 66);
    await expect(page.getByTestId('timeline-track')).toBeVisible({ timeout: 20_000 });

    const dayFont = await page.getByTestId('timeline-day-segment').first().evaluate(
      (element) => getComputedStyle(element).fontFamily,
    );
    const tickLabels = page.locator('[data-testid="timeline-tick"] > span');
    expect(await tickLabels.count()).toBeGreaterThan(0);
    const tickFont = await tickLabels.first().evaluate((element) => getComputedStyle(element).fontFamily);
    expect(dayFont).not.toMatch(/IBM Plex Mono/i);
    expect(tickFont).not.toMatch(/IBM Plex Mono/i);

    const thumbStyle = await page.getByTestId('timeline-thumb').locator('span').evaluate((element) => {
      const computed = getComputedStyle(element);
      return {
        backgroundImage: computed.backgroundImage,
        borderColor: computed.borderColor,
        borderWidth: computed.borderTopWidth,
      };
    });
    const thumbBorderChannels = thumbStyle.borderColor.match(/\d+(?:\.\d+)?/g)?.map(Number) ?? [];
    expect(thumbStyle.backgroundImage).not.toBe('none');
    expect(thumbStyle.borderWidth).toBe('1px');
    expect(Math.min(...thumbBorderChannels.slice(0, 3))).toBeGreaterThan(180);

    const play = page.getByTestId('timeline-play-button');
    const speed = page.getByTestId('timeline-speed-control');
    const jump = page.getByTestId('timeline-jump-select');
    const runReadout = page.getByTestId('timeline-run-readout');
    await expect(play).toBeVisible();
    await expect(speed).toBeVisible();
    await expect(jump).toBeVisible();
    await expect(runReadout).toContainText(/GFS \d{2}Z/);
    await expect(runReadout).toContainText(/FH 66/);

    const transportGeometry = await page.evaluate(() => {
      const rect = (testId: string) => document.querySelector(`[data-testid="${testId}"]`)?.getBoundingClientRect() ?? null;
      const play = document.querySelector('[data-testid="timeline-play-button"]');
      return {
        play: rect('timeline-play-button'),
        speed: rect('timeline-speed-control'),
        playBackground: play ? getComputedStyle(play).backgroundColor : null,
      };
    });
    expect(transportGeometry.play!.width).toBeGreaterThanOrEqual(40);
    expect(transportGeometry.speed!.width).toBeGreaterThanOrEqual(60);
    expect(transportGeometry.playBackground).not.toBe('rgba(0, 0, 0, 0)');

    await speed.click();
    const speedMenu = page.getByTestId('timeline-speed-menu');
    await expect(speedMenu).toBeVisible();
    const speedOptions = speedMenu.getByRole('menuitemradio');
    await expect(speedOptions).toHaveCount(4);
    expect((await speedOptions.allInnerTexts()).map((text) => text.trim())).toEqual([
      '0.5×',
      '1×',
      '2×',
      '4×',
    ]);
    await speedMenu.getByRole('menuitemradio', { name: '2×' }).click();
    await expect(speed).toHaveAccessibleName('Animation speed 2×');
    await expect(speedMenu).toBeHidden();

    await jump.selectOption('12');
    await expect.poll(() => new URL(page.url()).searchParams.get('fh')).toBe('12');
  });

  test('hover readout stays adjacent to the cursor after the desktop rail offset', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openTimeline(page, FIXTURES.gfsLong);
    await page.route('**/api/v4/sample?**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ value: 72, units: 'F', label: 'Hover probe' }),
      });
    });

    const map = (await page.getByTestId('viewer-map-slot').boundingBox())!;
    const cursor = { x: map.x + map.width * 0.58, y: map.y + map.height * 0.42 };
    await page.mouse.move(cursor.x, cursor.y);

    const tooltip = page.getByText('Hover probe', { exact: true });
    await expect(tooltip).toBeVisible({ timeout: 5_000 });
    const box = (await tooltip.boundingBox())!;
    expect(Math.abs(box.x - (cursor.x + 14))).toBeLessThanOrEqual(4);
    expect(Math.abs(box.y - (cursor.y - 32))).toBeLessThanOrEqual(4);
  });

  test('GIF export range ends at the ready boundary', async ({ page }) => {
    const assets = trackFrameAssetRequests(page, FIXTURES.outOfOrder);
    await openTimeline(page, FIXTURES.outOfOrder);
    // Phase 6: Share is a labeled button in the 48 px top bar (§6.1).
    await page.getByTestId('viewer-top-bar').getByRole('button', { name: 'Share' }).click();
    await expect(page.getByRole('dialog', { name: 'Share' })).toBeVisible({ timeout: 15_000 });
    await page.getByRole('dialog', { name: 'Share' }).getByRole('tab', { name: /GIF/i }).click();
    // Generating over the full available range must not touch beyond-boundary frames.
    const generate = page.getByRole('dialog', { name: 'Share' }).getByRole('button', { name: /generate/i });
    if (await generate.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await generate.click();
      await page.waitForTimeout(6_000);
    }
    expect(assets.fhs().filter((fh) => fh > 3)).toEqual([]);
    await page.keyboard.press('Escape');
  });
});

test.describe('Viewer timeline (Phase 5, mobile)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Pinned Chromium contract suite.');
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });

  test('day-indexed outlooks keep Day 1-3 visible on the compact scrubber', async ({ page }) => {
    await openTimeline(page, FIXTURES.spcDays);
    await expect(page.getByTestId('timeline-mobile-day-label')).toHaveText(['Day 1', 'Day 2', 'Day 3']);
  });

  test('mobile defaults to compact and thumb meets the coarse floor', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
    await stubTimelineRoutes(page, FIXTURES.building);
    await page.goto(timelineViewerUrl(FIXTURES.building));
    await expect(page.getByTestId('viewer-suspense-skeleton')).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });

    await expect(page.getByTestId('timeline-track')).toBeVisible({ timeout: 20_000 });
    const height = await page.evaluate(
      () => document.querySelector('[data-testid="timeline-root"]')?.getBoundingClientRect().height ?? null,
    );
    expect(height).not.toBeNull();
    expect(Math.round(height!)).toBe(64); // mobile default: compact

    const thumb = page.getByTestId('timeline-thumb');
    await expect(thumb).toBeVisible();
    const box = (await thumb.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);
  });
});
