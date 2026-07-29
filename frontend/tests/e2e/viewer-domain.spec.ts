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
