/**
 * ════════════════════════════════════════════════════════════════════════════
 * GLOBE PRESSURE CENTRES — the far-hemisphere cull for H/L markers
 * ════════════════════════════════════════════════════════════════════════════
 *
 * THE DEFECT. On the globe with Coverage=Global, H/L markers rendered for the
 * WHOLE field, including centres behind the planet. Operator screenshot showed
 * "L 534" and "L 487" hanging in black space to the LEFT of the disc, plus
 * markers sitting slightly off the surface near the limb.
 *
 * WHY IT LOOKS LIKE TWO BUGS BUT IS ONE. The markers are DOM nodes positioned
 * by `map.project()` (map-canvas.tsx, `buildPressureCenterScreenLabels`), and
 * the only cull was an on-canvas bounds test. Under the globe `project()` is a
 * PERSPECTIVE transform: a far-side point still returns a point, and once it
 * crosses behind the camera plane the perspective divide mirrors it through the
 * screen origin and it lands far off the disc — the "floating in space" half.
 * A far-side point just past the limb instead lands just inside it, which reads
 * as a marker misaligned with the surface. Same missing test, both symptoms.
 *
 * This overlay class was MISSED by docs/GLOBE_OVERLAY_AUDIT_2026-08-01.md
 * because the audit's fixtures carried no `pressure_centers` sidecar at all.
 * Hence a fixture case that carries one: `globe-4326-centers`.
 *
 * ── WHY THE ASSERTIONS ARE NOT CIRCULAR ─────────────────────────────────────
 * The fix culls with `transform.isLocationOccluded()`. Asserting against that
 * same predicate would pass for any implementation, including a broken one, so
 * every visibility assertion here uses an INDEPENDENT geometric quantity: the
 * great-circle angle between the camera centre and the centre's lng/lat.
 *
 *   - angle >= 90 deg  ->  provably behind the planet, must NOT be in the DOM.
 *     (The true horizon is slightly TIGHTER than 90 deg at a finite camera
 *     distance, so "90 deg or more" is a strict subset of what must be hidden
 *     and the assertion cannot fail on a correct implementation.)
 *   - angle <= 60 deg  ->  provably well inside the near cap, MUST be in the
 *     DOM: this is the direction that fails if the cull over-fires and hides
 *     everything, which a "hide them all" non-fix would sail through.
 *
 * Position is checked against `map.project()` of the marker's own lng/lat,
 * which the component echoes to `data-center-lon`/`data-center-lat`.
 * `map.project()` IS globe-correct — that was never the bug — so this pins the
 * remaining half of the report: the surviving markers sit ON the surface.
 *
 * FLAT ARM: the same fixture with `globe=1` dropped must show ALL eight
 * centres, which is what makes the cull provably inert on mercator.
 */
import { expect, test, type Page } from '@playwright/test';

import {
  GLOBE_CENTERS_ROTATED_VIEW,
  GLOBE_CENTERS_VIEW,
  GLOBE_PRESSURE_CENTERS,
  stubGoldenBaselineRoutes,
  viewerUrlForCase,
} from './render-golden-baseline.fixtures';
import { waitForGoldenReady } from './render-golden-harness';

/** Provably behind the planet at any finite camera distance. */
const HIDDEN_ANGLE_DEG = 90;
/** Provably well inside the near cap at these cameras. */
const VISIBLE_ANGLE_DEG = 60;
/** Marker centre vs `map.project()` of its own lng/lat, in CSS px. */
const POSITION_TOLERANCE_PX = 1;

type RenderedCenter = { lon: number; lat: number; x: number; y: number; text: string };

const toRad = (deg: number) => (deg * Math.PI) / 180;

/** Great-circle angle in degrees between two lng/lat pairs. */
function angularSeparationDeg(
  a: { lon: number; lat: number },
  b: { lon: number; lat: number },
): number {
  const dot =
    Math.sin(toRad(a.lat)) * Math.sin(toRad(b.lat)) +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.cos(toRad(a.lon - b.lon));
  return (Math.acos(Math.max(-1, Math.min(1, dot))) * 180) / Math.PI;
}

function centersAtOrBeyond(camera: { lat: number; lon: number }, minAngleDeg: number) {
  return GLOBE_PRESSURE_CENTERS.filter(
    (center) => angularSeparationDeg(camera, center) >= minAngleDeg,
  );
}

function centersWithin(camera: { lat: number; lon: number }, maxAngleDeg: number) {
  return GLOBE_PRESSURE_CENTERS.filter(
    (center) => angularSeparationDeg(camera, center) <= maxAngleDeg,
  );
}

/**
 * Every rendered marker, with its source lng/lat and its ACTUAL on-screen
 * centre. The DOM node is positioned by `left`/`top` plus a
 * `translate(-50%, -50%)`, so the bounding box's midpoint is the anchor — read
 * from the box rather than from the style so the CSS transform is in the loop.
 */
async function readRenderedCenters(page: Page): Promise<RenderedCenter[]> {
  return page.evaluate(() => {
    const canvas = document.querySelector('.maplibregl-canvas') as HTMLCanvasElement | null;
    const origin = canvas?.getBoundingClientRect() ?? { left: 0, top: 0 };
    return Array.from(document.querySelectorAll('[data-testid="map-pressure-center"]')).map((el) => {
      const rect = (el as HTMLElement).getBoundingClientRect();
      return {
        lon: Number((el as HTMLElement).dataset.centerLon),
        lat: Number((el as HTMLElement).dataset.centerLat),
        x: rect.left + rect.width / 2 - origin.left,
        y: rect.top + rect.height / 2 - origin.top,
        text: (el.textContent ?? '').trim(),
      };
    });
  });
}

/** `map.project()` for a batch of lng/lat, straight off the live map. */
async function projectAll(page: Page, points: Array<{ lon: number; lat: number }>) {
  return page.evaluate((coords) => {
    const map = (
      window as typeof window & {
        __cartoskyGlobe: { map: { project: (l: [number, number]) => { x: number; y: number } } };
      }
    ).__cartoskyGlobe.map;
    return coords.map((c) => {
      const p = map.project([c.lon, c.lat]);
      return { x: p.x, y: p.y };
    });
  }, points);
}

async function jumpTo(page: Page, view: { lat: number; lon: number; zoom: number }) {
  await page.evaluate((v) => {
    const map = (
      window as typeof window & {
        __cartoskyGlobe: {
          map: { jumpTo: (o: { center: [number, number]; zoom: number }) => void };
        };
      }
    ).__cartoskyGlobe.map;
    map.jumpTo({ center: [v.lon, v.lat], zoom: v.zoom });
  }, view);
}

/** The markers re-sync on a rAF after `move`; poll rather than sleep. */
async function expectRenderedCount(page: Page, expected: number, message: string) {
  await expect
    .poll(async () => (await readRenderedCenters(page)).length, {
      timeout: 10_000,
      message,
    })
    .toBe(expected);
}

async function openCenters(page: Page, opts: { globe: boolean }) {
  await stubGoldenBaselineRoutes(page, 'globe-4326-centers');
  const url = viewerUrlForCase('globe-4326-centers');
  await page.goto(opts.globe ? url : url.replace('&globe=1', ''));
  await waitForGoldenReady(page, `globe-4326-centers${opts.globe ? '' : '-flat'}`);
}

test.describe('globe pressure centres', () => {
  test('far-hemisphere H/L markers are culled and near-side ones sit on the surface', async ({
    page,
  }) => {
    await openCenters(page, { globe: true });

    // Sanity: the fixture actually exercises both sides at this camera, so a
    // silently-empty sidecar cannot make this test vacuous.
    const provablyHidden = centersAtOrBeyond(GLOBE_CENTERS_VIEW, HIDDEN_ANGLE_DEG);
    const provablyVisible = centersWithin(GLOBE_CENTERS_VIEW, VISIBLE_ANGLE_DEG);
    expect(provablyHidden.length).toBeGreaterThanOrEqual(4);
    expect(provablyVisible.length).toBeGreaterThanOrEqual(3);

    const rendered = await readRenderedCenters(page);

    // (a) Zero markers for occluded lng/lat. Compared on the coordinate pair,
    // not on a count, so a cull that hid the wrong four would still fail.
    const renderedKeys = new Set(rendered.map((r) => `${r.lon},${r.lat}`));
    for (const center of provablyHidden) {
      expect(
        renderedKeys.has(`${center.lon},${center.lat}`),
        `far-side ${center.type} ${center.value} at ${center.lon},${center.lat} must not render`,
      ).toBe(false);
    }

    // ...and the near-side ones are still there: the cull did not over-fire.
    for (const center of provablyVisible) {
      expect(
        renderedKeys.has(`${center.lon},${center.lat}`),
        `near-side ${center.type} ${center.value} at ${center.lon},${center.lat} must render`,
      ).toBe(true);
    }

    // Nothing rendered may be on the far side, whatever its angle.
    for (const item of rendered) {
      expect(
        angularSeparationDeg(GLOBE_CENTERS_VIEW, item),
        `rendered marker "${item.text}" is behind the planet`,
      ).toBeLessThan(HIDDEN_ANGLE_DEG);
    }

    // (b) Every surviving marker is where map.project() says it is.
    const projected = await projectAll(page, rendered);
    rendered.forEach((item, index) => {
      expect(Math.abs(item.x - projected[index].x), `marker "${item.text}" x`).toBeLessThanOrEqual(
        POSITION_TOLERANCE_PX,
      );
      expect(Math.abs(item.y - projected[index].y), `marker "${item.text}" y`).toBeLessThanOrEqual(
        POSITION_TOLERANCE_PX,
      );
    });

    // The two markers from the operator screenshot, named explicitly.
    expect(rendered.map((r) => r.text).join(' ')).not.toContain('534');
    expect(rendered.map((r) => r.text).join(' ')).not.toContain('487');
  });

  test('markers reappear after rotating the far hemisphere into view', async ({ page }) => {
    await openCenters(page, { globe: true });

    const before = await readRenderedCenters(page);
    const beforeKeys = new Set(before.map((r) => `${r.lon},${r.lat}`));
    // The camera swings 180 deg: what was provably hidden is now provably in
    // the near cap.
    const nowVisible = centersWithin(GLOBE_CENTERS_ROTATED_VIEW, VISIBLE_ANGLE_DEG);
    expect(nowVisible.length).toBeGreaterThanOrEqual(2);
    for (const center of nowVisible) {
      expect(beforeKeys.has(`${center.lon},${center.lat}`)).toBe(false);
    }

    await jumpTo(page, GLOBE_CENTERS_ROTATED_VIEW);
    await expect
      .poll(
        async () => {
          const keys = new Set((await readRenderedCenters(page)).map((r) => `${r.lon},${r.lat}`));
          return nowVisible.every((c) => keys.has(`${c.lon},${c.lat}`));
        },
        { timeout: 10_000, message: 'far-side markers release after rotation' },
      )
      .toBe(true);

    const after = await readRenderedCenters(page);

    // The cull is symmetric: what rotated AWAY is now gone.
    const afterKeys = new Set(after.map((r) => `${r.lon},${r.lat}`));
    for (const center of centersAtOrBeyond(GLOBE_CENTERS_ROTATED_VIEW, HIDDEN_ANGLE_DEG)) {
      expect(
        afterKeys.has(`${center.lon},${center.lat}`),
        `${center.type} ${center.value} rotated behind the planet`,
      ).toBe(false);
    }

    // And they are positioned, not merely present.
    const projected = await projectAll(page, after);
    after.forEach((item, index) => {
      expect(Math.abs(item.x - projected[index].x)).toBeLessThanOrEqual(POSITION_TOLERANCE_PX);
      expect(Math.abs(item.y - projected[index].y)).toBeLessThanOrEqual(POSITION_TOLERANCE_PX);
    });
  });

  test('flat map renders every centre — the cull is inert off the globe', async ({ page }) => {
    await openCenters(page, { globe: false });

    // No hemisphere on a mercator map: all eight draw, including the ones the
    // globe arm hides. This is the arm that fails if the cull leaks into flat.
    await expectRenderedCount(
      page,
      GLOBE_PRESSURE_CENTERS.length,
      'flat map shows every pressure centre',
    );

    const rendered = await readRenderedCenters(page);
    const projected = await projectAll(page, rendered);
    rendered.forEach((item, index) => {
      expect(Math.abs(item.x - projected[index].x)).toBeLessThanOrEqual(POSITION_TOLERANCE_PX);
      expect(Math.abs(item.y - projected[index].y)).toBeLessThanOrEqual(POSITION_TOLERANCE_PX);
    });
  });
});
