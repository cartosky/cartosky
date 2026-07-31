/**
 * Phase 2B data-domain / camera-preset split contract
 * (docs/PHASE_2B_FRONTEND_DOMAIN_SPLIT_PLAN_2026-07-29.md, D7).
 *
 * Node-side blocks pin the permalink serializers (byte-identity without
 * `domain`, correct serialization with it). Browser blocks drive the viewer
 * and compare pages against synthetic domain-scoped fixtures and assert
 * request ROUTING only — no real global artifacts exist until Phase 3.
 */
import { test, expect } from '@playwright/test';

import { buildPermalinkSearch } from '../../src/lib/permalink';
import { buildComparePermalinkSearch } from '../../src/lib/compare-permalink';
import {
  DOMAIN_CANONICAL_ONLY_VARIABLE,
  DOMAIN_ID,
  DOMAIN_MODEL,
  DOMAIN_RUN_ID,
  DOMAIN_SECOND_MODEL,
  DOMAIN_VARIABLE,
  stubViewerDomainRoutes,
} from './viewer-domain.fixtures';

const CONTROL_API_SEGMENTS = ['/runs', '/manifest', '/frames', '/grid-manifest'];

function controlApiUrls(recorded: string[]): string[] {
  return recorded.filter((url) => CONTROL_API_SEGMENTS.some((segment) => url.split('?')[0].endsWith(segment)));
}

test.describe('Permalink serializers (pure)', () => {
  test('viewer permalink without domain is byte-identical to the pre-domain shape', () => {
    const search = buildPermalinkSearch({
      model: 'gfs', run: 'latest', var: 'tmp2m', fh: 6, region: 'midwest', lat: 39.83, lon: -98.58, z: 4,
    });
    expect(search).toBe('?m=gfs&r=latest&v=tmp2m&fh=6&reg=midwest&lat=39.83&lon=-98.58&z=4');
    expect(search).not.toContain('domain');
  });

  test('viewer permalink serializes a non-null domain under the literal `domain` key', () => {
    const search = buildPermalinkSearch({ model: 'gfs', var: 'tmp2m', domain: 'global' });
    expect(search).toBe('?m=gfs&v=tmp2m&domain=global');
  });

  test('compare permalink without domain is byte-identical to the pre-domain shape', () => {
    const search = buildComparePermalinkSearch({
      lm: 'gfs', lv: 'tmp2m', lr: 'latest', rm: 'nam', rv: 'tmp2m', rr: 'latest', fh: 0, mode: 'split',
    });
    expect(search).toBe('?lm=gfs&lv=tmp2m&lr=latest&rm=nam&rv=tmp2m&rr=latest&fh=0');
    expect(search).not.toContain('domain');
  });

  test('compare permalink serializes a shared domain', () => {
    const search = buildComparePermalinkSearch({ lm: 'gfs', rm: 'gfs', domain: 'global' });
    expect(search).toBe('?lm=gfs&rm=gfs&domain=global');
  });
});

test.describe('Viewer data-domain routing', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium-only contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop routing contract.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  test('?domain=global keys control APIs with domain= and fetches domain-scoped grid binaries', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    const binaryRequest = page.waitForRequest((request) =>
      request.url().includes(`/api/v4/grid/domains/${DOMAIN_ID}/${DOMAIN_MODEL}/`));
    await page.goto(`/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus&domain=${DOMAIN_ID}&screenshot=1`);
    await binaryRequest;

    const controlUrls = controlApiUrls(recorded);
    expect(controlUrls.length).toBeGreaterThan(0);
    for (const url of controlUrls) {
      expect(url, `control API request must carry domain=: ${url}`).toContain(`domain=${DOMAIN_ID}`);
    }
    // The canonical (unprefixed) binary route must not have been touched.
    expect(recorded.some((url) => url.startsWith(`/api/v4/grid/${DOMAIN_MODEL}/`))).toBe(false);
  });

  test('a permalink without domain= issues byte-identical canonical requests (TWF compatibility)', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    const binaryRequest = page.waitForRequest((request) =>
      request.url().includes(`/api/v4/grid/${DOMAIN_MODEL}/${DOMAIN_RUN_ID}/`));
    await page.goto(`/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus&screenshot=1`);
    await binaryRequest;

    for (const url of recorded) {
      expect(url, `no request may carry domain= on a canonical load: ${url}`).not.toContain('domain=');
      expect(url, `no request may carry a domains/ segment on a canonical load: ${url}`).not.toContain('/domains/');
    }
  });

  test('an unsupported domain degrades to canonical requests but stays sticky in the URL', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    const binaryRequest = page.waitForRequest((request) =>
      request.url().includes(`/api/v4/grid/${DOMAIN_MODEL}/${DOMAIN_RUN_ID}/`));
    await page.goto(`/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus&domain=mars&screenshot=1`);
    await binaryRequest;

    for (const url of recorded) {
      expect(url, `unsupported domain must not reach requests: ${url}`).not.toContain('domain=');
    }
    // Sticky: the URL keeps the requested domain (permalink write-back must
    // not strip it), so a later supported selection can adopt it.
    await expect.poll(() => page.evaluate(() => window.location.search)).toContain('domain=mars');
  });

  /**
   * Phase 3 §5: `supported_build_regions` (the fixture variable declares
   * ["conus", "global"]) describes which DATA domains may be requested and must
   * never constrain the CAMERA option list. Observable without touching the
   * region control: if the options collapsed to the declared build regions,
   * `midwest` would leave the allowed set and the viewer would snap the camera
   * back to `conus` — rewriting `reg=` in the permalink.
   */
  test('camera presets never collapse to supported_build_regions', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    for (const search of [
      `?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=midwest&domain=${DOMAIN_ID}`,
      `?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=midwest`,
    ]) {
      await page.goto(`/viewer${search}`);
      await page.waitForRequest((request) => request.url().includes('/grid-manifest'));
      await page.waitForLoadState('networkidle');
      expect(
        new URL(page.url()).searchParams.get('reg'),
        `camera preset must survive on ${search}`,
      ).toBe('midwest');
    }
  });

  test('region= alone still changes only the viewport, with or without domain=', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    const binaryRequest = page.waitForRequest((request) =>
      request.url().includes(`/api/v4/grid/domains/${DOMAIN_ID}/${DOMAIN_MODEL}/`));
    await page.goto(`/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=midwest&domain=${DOMAIN_ID}&screenshot=1`);
    await binaryRequest;

    // The camera preset id must never appear as a data-request key: identical
    // artifact routing to the reg=conus case above.
    for (const url of controlApiUrls(recorded)) {
      expect(url).toContain(`domain=${DOMAIN_ID}`);
      expect(url).not.toContain('midwest');
    }
  });
});

/**
 * Coverage control — global go-live design §1/§2 (decisions U1/U2).
 *
 * The control is capabilities-driven: the fixture's `gfs` declares
 * `supported_build_regions: ['conus', 'global']`, `nam` declares none.
 */
test.describe('Coverage control', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium-only contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop rail contract.');
    // Wide enough for the rail to boot expanded (same size the Phase 6 rail
    // chrome suite uses).
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  test('flipping to Global adds domain=, flipping back drops the param entirely', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    await page.goto(`/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus`);
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));

    const coverage = page.getByTestId('coverage-control');
    await expect(coverage).toBeVisible();
    // §1: canonical segment label derived from the model's canonical region.
    await expect(coverage.getByRole('radio', { name: 'CONUS' })).toHaveAttribute('aria-checked', 'true');
    // Absence of `domain=` is the canonical default (§7 flip point returns null).
    expect(new URL(page.url()).searchParams.get('domain')).toBeNull();

    await coverage.getByRole('radio', { name: 'Global' }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('domain')).toBe(DOMAIN_ID);
    await expect(coverage.getByRole('radio', { name: 'Global' })).toHaveAttribute('aria-checked', 'true');
    // The flip must actually re-key the data requests.
    await expect
      .poll(() => recorded.some((url) => url.includes(`/api/v4/grid/domains/${DOMAIN_ID}/${DOMAIN_MODEL}/`)))
      .toBe(true);

    await coverage.getByRole('radio', { name: 'CONUS' }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('domain')).toBeNull();
    await expect.poll(() => page.evaluate(() => window.location.search)).not.toContain('domain');
  });

  test('a variable without the requested domain keeps the segment and shows the degraded note', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    await page.goto(
      `/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_CANONICAL_ONLY_VARIABLE}&fh=0&reg=conus&domain=${DOMAIN_ID}`,
    );
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));

    const coverage = page.getByTestId('coverage-control');
    // U2: sticky — the toggle stays on the REQUESTED segment...
    await expect(coverage.getByRole('radio', { name: 'Global' })).toHaveAttribute('aria-checked', 'true');
    await expect.poll(() => new URL(page.url()).searchParams.get('domain')).toBe(DOMAIN_ID);
    // ...with an inline note naming the canonical coverage actually shown.
    await expect(page.getByTestId('coverage-degraded-note'))
      .toHaveText('Not available for this variable — showing CONUS');
    // ...while requests silently degrade to canonical (Phase 2B, unchanged).
    for (const url of controlApiUrls(recorded)) {
      expect(url, `degraded selection must not carry domain=: ${url}`).not.toContain('domain=');
    }
  });

  test('a canonical-only model renders no Coverage control, note or badges — even with a sticky domain=', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    // WITH the param: this is reachable through pure UI (GFS → Global → switch
    // to NAM), and it is the case that leaked NA badges onto a model with no
    // control to explain them.
    await page.goto(`/viewer?m=${DOMAIN_SECOND_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus&domain=${DOMAIN_ID}`);
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));
    await expect(page.getByTestId('rail-source')).toBeVisible();
    await expect(page.getByTestId('coverage-control')).toHaveCount(0);
    await expect(page.getByTestId('coverage-degraded-note')).toHaveCount(0);

    // Open the variable picker: not one row may carry a coverage chip.
    await page.getByTestId('rail-source-fields-card').getByRole('button', { name: 'Surface Temp' }).click();
    await expect(page.getByRole('dialog', { name: 'Variable picker' })).toBeVisible();
    await expect(page.getByTestId('variable-coverage-badge')).toHaveCount(0);
  });

  test('variable rows lacking the requested domain carry the canonical chip', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    await page.goto(`/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus&domain=${DOMAIN_ID}`);
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));

    await page.getByTestId('rail-source-fields-card').getByRole('button', { name: 'Surface Temp' }).click();
    await expect(page.getByRole('dialog', { name: 'Variable picker' })).toBeVisible();
    // Search brings both variables into one list regardless of category.
    await page.getByPlaceholder('Search variables…').fill('e');
    const badges = page.getByTestId('variable-coverage-badge');
    await expect(badges).toHaveCount(1);
    await expect(badges.first()).toHaveText('CONUS');
  });
});

/**
 * World camera preset — design §4 / decision U3. No new filter logic exists:
 * `filterRegionOptionsForDataDomain` drops every preset not contained by the
 * canonical region unless a non-canonical domain is ACTIVE.
 */
test.describe('World camera preset gating', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium-only contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop rail contract.');
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  async function openRegionOptions(page: import('@playwright/test').Page) {
    await page.getByTestId('rail-region-row').getByRole('button').first().click();
    await expect(page.getByTestId('region-picker-panel')).toBeVisible();
    return page.getByTestId('region-picker-panel');
  }

  test('World appears only while a non-canonical domain is active', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    await page.goto(`/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus&domain=${DOMAIN_ID}`);
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));
    await expect((await openRegionOptions(page)).getByRole('button', { name: 'World' })).toBeVisible();
    await page.keyboard.press('Escape');

    // Canonical load: same fixture presets, World filtered out.
    await page.goto(`/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_VARIABLE}&fh=0&reg=conus`);
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));
    await expect((await openRegionOptions(page)).getByRole('button', { name: 'World' })).toHaveCount(0);
  });

  test('World disappears again when a degraded variable drops the effective domain', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    await page.goto(
      `/viewer?m=${DOMAIN_MODEL}&r=latest&v=${DOMAIN_CANONICAL_ONLY_VARIABLE}&fh=0&reg=conus&domain=${DOMAIN_ID}`,
    );
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));
    // Requested global, resolved canonical → the camera list stays canonical.
    await expect((await openRegionOptions(page)).getByRole('button', { name: 'World' })).toHaveCount(0);
  });
});

test.describe('Compare shared-domain rule', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium-only contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop routing contract.');
  });

  test('domain applies only when BOTH panes support it', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    // gfs supports global, nam does not → both panes degrade to canonical.
    await page.goto(`/compare?lm=${DOMAIN_MODEL}&lv=${DOMAIN_VARIABLE}&lr=latest&rm=${DOMAIN_SECOND_MODEL}&rv=${DOMAIN_VARIABLE}&rr=latest&fh=0&domain=${DOMAIN_ID}`);
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));
    await expect.poll(() => controlApiUrls(recorded).length).toBeGreaterThan(1);
    for (const url of controlApiUrls(recorded)) {
      expect(url, `mixed-support compare must not carry domain=: ${url}`).not.toContain('domain=');
    }
  });

  test('both panes carry the shared domain when both support it', async ({ page }) => {
    const recorded: string[] = [];
    await stubViewerDomainRoutes(page, recorded);

    await page.goto(`/compare?lm=${DOMAIN_MODEL}&lv=${DOMAIN_VARIABLE}&lr=latest&rm=${DOMAIN_MODEL}&rv=${DOMAIN_VARIABLE}&rr=latest&fh=0&domain=${DOMAIN_ID}`);
    await page.waitForRequest((request) => request.url().includes('/grid-manifest'));
    await expect.poll(() => controlApiUrls(recorded).length).toBeGreaterThan(1);
    for (const url of controlApiUrls(recorded)) {
      expect(url, `both-support compare must carry domain= on every control request: ${url}`).toContain(`domain=${DOMAIN_ID}`);
    }
  });
});
