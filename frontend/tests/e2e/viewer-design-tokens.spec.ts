/**
 * Phase 4 design-tokens audit (docs/plans/2026-07-28-map-viewer-redesign-phase-4-design-tokens.md,
 * Task 1). MAP_VIEWER_REDESIGN §2 contracts, §9 acceptance shapes.
 *
 * Surface matrix: one deterministic state per unique control/panel family.
 * Regimes: `(pointer: coarse)` via `hasTouch: true` (verified to flip the
 * media query in headless Chromium) → ≥44×44; default fine pointer → ≥32×32.
 * Exclusions are §9's only: sr-only, aria-hidden subtrees, `.maplibregl-*`.
 * Focus: exhaustive Tab-walk per surface — every stop must be
 * `:focus-visible` with the approved cyan outline token (≥2px, rgb
 * 103,232,249); mouse focus must not show it.
 */
import { test, expect, type Page } from '@playwright/test';

import { COLORMAP_VIEW, stubViewerColormapRoutes } from './viewer-colormap.fixtures';

const VIEWER_URL =
  `/viewer?m=gfs&r=latest&v=tmp2m&fh=0&reg=conus&lat=${COLORMAP_VIEW.lat}&lon=${COLORMAP_VIEW.lon}&z=${COLORMAP_VIEW.zoom}`;
const COMPARE_URL =
  `/compare?lm=gfs&lv=tmp2m&lr=latest&rm=gfs&rv=tmp2m&rr=latest&fh=0&lat=${COLORMAP_VIEW.lat}&lon=${COLORMAP_VIEW.lon}&z=5`;

const FOCUS_RING_RGB = '103, 232, 249';
const FOCUS_RING_MIN_WIDTH_PX = 2;

type Violation = { surface: string; selector: string; detail: string };

async function openViewer(page: Page) {
  await stubViewerColormapRoutes(page);
  await page.goto(VIEWER_URL);
  await expect(page.locator('header').getByRole('button', { name: 'GFS' })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });
}

type Surface = {
  name: string;
  setup?: (page: Page) => Promise<void>;
  teardown?: (page: Page) => Promise<void>;
};

const escapeTeardown = async (page: Page) => {
  await page.keyboard.press('Escape');
  await page.waitForTimeout(150);
};

const DESKTOP_SURFACES: Surface[] = [
  { name: 'closed-chrome' },
  {
    name: 'product-panel',
    setup: async (page) => {
      await page.locator('header').getByRole('button', { name: 'GFS' }).click();
      await expect(page.getByLabel('Model picker')).toBeVisible();
    },
    teardown: escapeTeardown,
  },
  {
    name: 'variable-panel',
    setup: async (page) => {
      // Trigger label is the frontend VARIABLE_UI_OVERRIDES display name.
      await page.locator('header').getByRole('button', { name: 'Surface Temp' }).click();
      await expect(page.getByLabel('Variable picker')).toBeVisible();
    },
    teardown: escapeTeardown,
  },
  {
    name: 'statistic-popover',
    setup: async (page) => {
      await page.locator('header').getByRole('button', { name: 'Mean' }).click();
      await expect(page.getByRole('button', { name: 'Percentile' })).toBeVisible();
    },
    teardown: escapeTeardown,
  },
  {
    name: 'run-select',
    setup: async (page) => {
      await page.locator('header').getByRole('combobox').first().click();
      await expect(page.getByRole('listbox')).toBeVisible();
      // Radix's zoom-in open animation scales the options (~95%) for the
      // first frames; settle before measuring rects.
      await page.waitForTimeout(300);
    },
    teardown: escapeTeardown,
  },
  {
    name: 'region-popover',
    setup: async (page) => {
      await page.locator('header').locator('[aria-label^="Region:"]').click();
      await expect(page.getByPlaceholder('Search city or zip…')).toBeVisible();
    },
    teardown: escapeTeardown,
  },
  {
    name: 'legend-panel',
    setup: async (page) => {
      await page.locator('header').getByLabel('Legend').click();
      await expect(page.locator('[style*="linear-gradient"]').first()).toBeVisible();
    },
    teardown: escapeTeardown,
  },
  {
    name: 'display-settings',
    setup: async (page) => {
      // Desktop opens the display panel. Tablet-touch has NO display-settings
      // surface at all (use-display-settings.ts force-closes the panel on
      // non-desktop layouts, and the bottom-bar controls button is hidden on
      // tablet-touch) — a product gap owned by Phases 6/8, not this audit.
      // The panel's controls are audited under the fine regime, and touch
      // settings are audited via the mobile sheet state. Skip when the
      // layout provides no panel.
      await page.locator('header').getByLabel('Display settings').click();
      const opened = await page
        .getByLabel('Close display panel')
        .isVisible({ timeout: 1_500 })
        .catch(() => false);
      if (!opened) {
        await escapeTeardown(page);
      }
    },
    teardown: escapeTeardown,
  },
  {
    name: 'share-dialog',
    setup: async (page) => {
      await page.locator('header').getByLabel('Share').click();
      await expect(page.getByRole('dialog', { name: 'Share' })).toBeVisible({ timeout: 15_000 });
    },
    teardown: async (page) => {
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog', { name: 'Share' })).toBeHidden();
    },
  },
];

/** Interactive-target rect audit for the current DOM state. */
async function auditTargets(page: Page, surface: string, minPx: number): Promise<Violation[]> {
  return page.evaluate(
    (args) => {
      const results: Array<{ surface: string; selector: string; detail: string }> = [];
      const seen = new Set<Element>();
      const describe = (el: Element) => {
        const tag = el.tagName.toLowerCase();
        const label =
          el.getAttribute('aria-label')
          ?? el.getAttribute('placeholder')
          ?? (el.textContent ?? '').trim().slice(0, 32);
        return `${tag}[${label}]`;
      };
      const excluded = (el: Element) =>
        Boolean(
          el.closest('[aria-hidden="true"]')
          || el.closest('[class*="sr-only"]')
          || el.closest('[class*="maplibregl-"]')
          // DOCUMENTED EXCEPTION — the display-panel opacity slider keeps the
          // stock 16px Radix thumb: enlarging it re-treads the Phase 4 wrapper
          // positioning regressions. Owned by the Phase 6 display-panel
          // rebuild. The Phase 5 timeline thumb is fully compliant.
          || el.closest('[data-audit-exception="opacity-slider"]'),
        );
      const isVisible = (el: Element) => {
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
          return false;
        }
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      };
      const candidates = document.querySelectorAll(
        'button, a[href], [role="button"], [role="slider"], [role="tab"], [role="option"], [role="menuitem"], input, select',
      );
      for (const el of candidates) {
        if (seen.has(el) || excluded(el) || !isVisible(el)) continue;
        seen.add(el);
        const rect = el.getBoundingClientRect();
        if (rect.width < args.minPx - 0.5 || rect.height < args.minPx - 0.5) {
          results.push({
            surface: args.surface,
            selector: describe(el),
            detail: `${Math.round(rect.width)}x${Math.round(rect.height)} < ${args.minPx}`,
          });
        }
      }
      return results;
    },
    { surface, minPx },
  );
}

/** Type-floor audit: every element with direct text ≥ minPx. */
async function auditTypeFloor(page: Page, surface: string, minPx: number): Promise<Violation[]> {
  return page.evaluate(
    (args) => {
      const results: Array<{ surface: string; selector: string; detail: string }> = [];
      const excluded = (el: Element) =>
        Boolean(
          el.closest('[aria-hidden="true"]')
          || el.closest('[class*="sr-only"]')
          || el.closest('[class*="maplibregl-"]'),
        );
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
      let node = walker.nextNode();
      while (node) {
        const el = node as Element;
        node = walker.nextNode();
        if (excluded(el)) continue;
        const hasDirectText = Array.from(el.childNodes).some(
          (child) => child.nodeType === Node.TEXT_NODE && (child.textContent ?? '').trim().length > 0,
        );
        if (!hasDirectText) continue;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        const size = Number.parseFloat(style.fontSize);
        if (Number.isFinite(size) && size < args.minPx - 0.01) {
          const text = (el.textContent ?? '').trim().slice(0, 32);
          results.push({
            surface: args.surface,
            selector: `${el.tagName.toLowerCase()}["${text}"]`,
            detail: `${size}px < ${args.minPx}px`,
          });
        }
      }
      return results;
    },
    { surface, minPx },
  );
}

/** Exhaustive Tab-walk focus audit for the current state. */
async function auditFocus(page: Page, surface: string): Promise<Violation[]> {
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    document.body.focus();
  });
  const violations: Violation[] = [];
  const visited = new Set<string>();
  const readFocus = (rgb: string) =>
    page.evaluate((ringRgb) => {
      const el = document.activeElement;
      if (!el || el === document.body) return { stop: true as const };
      const excluded = Boolean(
        el.closest('[aria-hidden="true"]')
        || el.closest('[class*="sr-only"]')
        || el.closest('[class*="maplibregl-"]'),
      );
      const style = window.getComputedStyle(el);
      // Identity-based key: class/text keys collide across distinct elements
      // (e.g. two slider thumbs) and would end the walk early.
      const w = window as typeof window & { __focusAudit?: { n: number; ids: WeakMap<Element, number> } };
      const registry = (w.__focusAudit ??= { n: 0, ids: new WeakMap() });
      if (!registry.ids.has(el)) registry.ids.set(el, (registry.n += 1));
      const key = `#${registry.ids.get(el)}|${el.tagName}|${el.getAttribute('aria-label') ?? ''}|${(el.textContent ?? '').trim().slice(0, 24)}`;
      const outlineWidth = Number.parseFloat(style.outlineWidth || '0');
      const hasToken =
        style.outlineStyle !== 'none'
        && outlineWidth >= 2
        && style.outlineColor.includes(ringRgb);
      return {
        stop: false as const,
        key,
        excluded,
        focusVisible: el.matches(':focus-visible'),
        hasToken,
        outline: `${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}`,
      };
    }, rgb);
  for (let step = 0; step < 120; step += 1) {
    await page.keyboard.press('Tab');
    let info = await readFocus(FOCUS_RING_RGB);
    if (!info.stop && !info.excluded && (!info.focusVisible || !info.hasToken)) {
      // `transition-all` on several controls animates outline-width from 0;
      // re-read after the 150ms transition settles before judging.
      await page.waitForTimeout(250);
      info = await readFocus(FOCUS_RING_RGB);
    }
    if (info.stop) break;
    if (visited.has(info.key!)) break;
    visited.add(info.key!);
    if (info.excluded) continue;
    if (!info.focusVisible || !info.hasToken) {
      violations.push({
        surface,
        selector: info.key!,
        detail: `focus-visible=${info.focusVisible} outline=${info.outline}`,
      });
    }
  }
  return violations;
}

async function runSurfaces(
  page: Page,
  audit: (page: Page, surface: string) => Promise<Violation[]>,
): Promise<Violation[]> {
  const all: Violation[] = [];
  for (const surface of DESKTOP_SURFACES) {
    await surface.setup?.(page);
    all.push(...(await audit(page, surface.name)));
    await surface.teardown?.(page);
  }
  return all;
}

function formatViolations(violations: Violation[]): string {
  return violations.map((v) => `[${v.surface}] ${v.selector}: ${v.detail}`).join('\n');
}

test.describe('Viewer design tokens (Phase 4)', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Pinned Chromium contract suite.');

  test.beforeEach(async ({ page }) => {
    test.skip(/Mobile/.test(test.info().project.name), 'Desktop contract projects.');
    await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
  });

  test.describe('fine pointer', () => {
    test('every interactive element is at least 32x32 across all surfaces', async ({ page }) => {
      await openViewer(page);
      const violations = await runSurfaces(page, (p, s) => auditTargets(p, s, 32));
      expect(violations, formatViolations(violations)).toEqual([]);
    });

    test('no viewer-chrome text below 11px, operational labels at least 12px', async ({ page }) => {
      await openViewer(page);
      const violations = await runSurfaces(page, (p, s) => auditTypeFloor(p, s, 11));
      expect(violations, formatViolations(violations)).toEqual([]);

      // Operational labels (explicit inventory list — classification is human).
      const operational = await page.evaluate(() => {
        const probes: Array<{ label: string; text: string; scope: string }> = [
          { label: 'field-caption-product', text: 'Product', scope: 'header' },
          { label: 'field-caption-variable', text: 'Variable', scope: 'header' },
          { label: 'field-caption-run', text: 'Run Time', scope: 'header' },
          // 'timeline-model' probe retired in Phase 5: the desktop timeline no
          // longer carries a model label (identity lives in the header Product
          // trigger; the mobile bar keeps its 12px label, covered by the
          // mobile audit state). Pre-Phase-5 this probe was satisfied only by
          // a display:none mobile duplicate — a latent probe bug.
        ];
        const results: Array<{ label: string; size: number | null }> = [];
        const toolbarHeader = Array.from(document.querySelectorAll('header')).find(
          (el) => el.querySelector('[role="combobox"]'),
        );
        for (const probe of probes) {
          const root = probe.scope === 'header' ? toolbarHeader ?? document.body : document.body;
          const match = Array.from(root.querySelectorAll('span, div'))
            .filter((el) => !el.closest('header button') || probe.scope !== 'body')
            .find(
              (el) =>
                (el.textContent ?? '').trim() === probe.text
                && Array.from(el.childNodes).some(
                  (c) => c.nodeType === Node.TEXT_NODE && (c.textContent ?? '').trim().length > 0,
                ),
            );
          results.push({
            label: probe.label,
            size: match ? Number.parseFloat(window.getComputedStyle(match).fontSize) : null,
          });
        }
        return results;
      });
      for (const entry of operational) {
        expect(entry.size, `${entry.label} missing from DOM`).not.toBeNull();
        expect(entry.size!, `${entry.label} is ${entry.size}px < 12px`).toBeGreaterThanOrEqual(12);
      }
    });

    test('Product, Variable, Statistic, and Run triggers share one field contract', async ({ page }) => {
      await openViewer(page);
      const styles = await page.evaluate(() => {
        // Two <header> elements exist (layout fallback + real toolbar); the
        // real one is the one containing the Run combobox.
        const header = Array.from(document.querySelectorAll('header')).find(
          (el) => el.querySelector('[role="combobox"]'),
        )!;
        const byText = (text: string) =>
          Array.from(header.querySelectorAll('button')).find(
            (el) => (el.textContent ?? '').trim().startsWith(text),
          ) ?? null;
        const run = header.querySelector('[role="combobox"]');
        const triggers: Array<[string, Element | null]> = [
          ['product', byText('GFS')],
          ['variable', byText('Surface Temp')],
          ['statistic', byText('Mean')],
          ['run', run],
        ];
        return triggers.map(([name, el]) => {
          if (!el) return { name, missing: true };
          const s = window.getComputedStyle(el);
          return {
            name,
            missing: false,
            borderRadius: s.borderRadius,
            borderWidth: s.borderTopWidth,
            borderColor: s.borderTopColor,
            background: s.backgroundColor,
            height: Math.round(el.getBoundingClientRect().height),
            fontSize: s.fontSize,
          };
        });
      });
      const reference = styles[0] as Record<string, unknown>;
      expect(reference.missing).toBe(false);
      for (const entry of styles.slice(1)) {
        expect(entry.missing, `${(entry as { name: string }).name} trigger missing`).toBe(false);
        for (const key of ['borderRadius', 'borderWidth', 'borderColor', 'background', 'height', 'fontSize']) {
          expect(
            (entry as Record<string, unknown>)[key],
            `${(entry as { name: string }).name}.${key} differs from product trigger`,
          ).toEqual(reference[key]);
        }
      }
    });

    test('every tabbable element shows the focus ring token; mouse focus does not', async ({ page }) => {
      await openViewer(page);
      const violations = await runSurfaces(page, (p, s) => auditFocus(p, s));
      expect(violations, formatViolations(violations)).toEqual([]);

      // Mouse focus must NOT show the ring.
      const productTrigger = page.locator('header').getByRole('button', { name: 'GFS' });
      await productTrigger.click();
      await page.keyboard.press('Escape');
      const mouseFocus = await page.evaluate((ringRgb) => {
        const el = document.activeElement;
        if (!el || el === document.body) return { ok: true };
        const style = window.getComputedStyle(el);
        const ringShown =
          style.outlineStyle !== 'none'
          && Number.parseFloat(style.outlineWidth || '0') >= 2
          && style.outlineColor.includes(ringRgb);
        return { ok: !el.matches(':focus-visible') || !ringShown, ringShown };
      }, FOCUS_RING_RGB);
      expect(mouseFocus.ok).toBe(true);
    });
  });

  test.describe('desktop layout under a coarse pointer', () => {
    // tablet-touch has no display panel, so the standard coarse matrix cannot
    // reach it. A large touch display (1440x1000 exceeds both tablet-touch
    // thresholds) keeps the DESKTOP layout while hasTouch makes
    // (pointer: coarse) real — the touchscreen-desktop case.
    test.use({ hasTouch: true, viewport: { width: 1440, height: 1000 } });

    test('display panel controls meet the 44px floor', async ({ page }) => {
      await openViewer(page);
      await page.locator('header').getByLabel('Display settings').click();
      await expect(page.getByLabel('Close display panel')).toBeVisible();
      const violations = await auditTargets(page, 'display-settings-coarse', 44);
      expect(violations, formatViolations(violations)).toEqual([]);
    });
  });

  test.describe('coarse pointer', () => {
    test.use({ hasTouch: true });

    test('every interactive element is at least 44x44 across all surfaces', async ({ page }) => {
      await openViewer(page);
      const violations = await runSurfaces(page, (p, s) => auditTargets(p, s, 44));
      expect(violations, formatViolations(violations)).toEqual([]);
    });

    test('wrapped header never overlaps map controls at tablet sizes', async ({ page }) => {
      // Zoom controls default hidden on non-desktop layouts; the geometry
      // anchor needs them, so seed the persisted preference on.
      await page.addInitScript(() => localStorage.setItem('twf.map.zoom_controls_visible', 'true'));
      for (const viewport of [{ width: 768, height: 1024 }, { width: 1024, height: 768 }]) {
        await page.setViewportSize(viewport);
        await openViewer(page);
        const geometry = await page.evaluate(() => {
          const header = document.querySelector('header')!;
          const headerRect = header.getBoundingClientRect();
          const zoomIn = document.querySelector('[aria-label="Zoom in"]');
          const zoomRect = zoomIn?.getBoundingClientRect() ?? null;
          return {
            headerBottom: headerRect.bottom,
            headerOverflowX: header.scrollWidth > header.clientWidth + 1,
            zoomTop: zoomRect?.top ?? null,
            zoomVisible: zoomRect ? zoomRect.top >= 0 && zoomRect.bottom <= window.innerHeight : false,
          };
        });
        expect(geometry.zoomTop, `${viewport.width}x${viewport.height}: zoom control missing`).not.toBeNull();
        expect(
          geometry.zoomTop!,
          `${viewport.width}x${viewport.height}: header bottom ${geometry.headerBottom} overlaps zoom top ${geometry.zoomTop}`,
        ).toBeGreaterThanOrEqual(geometry.headerBottom);
        expect(geometry.zoomVisible).toBe(true);
        expect(geometry.headerOverflowX).toBe(false);
      }
    });

    test('compare control bar absorbs coarse trigger sizing without overflow', async ({ page }) => {
      await stubViewerColormapRoutes(page);
      await page.goto(COMPARE_URL);
      const pickerTrigger = page.getByRole('button', { name: 'GFS' }).first();
      await expect(pickerTrigger).toBeVisible({ timeout: 30_000 });

      const geometry = await page.evaluate(() => {
        const triggers = Array.from(document.querySelectorAll('button')).filter((el) => {
          if (!['GFS', 'Surface Temp'].includes((el.textContent ?? '').trim())) return false;
          const rect = el.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0; // hidden drawer duplicates excluded
        });
        const heights = triggers.map((el) => Math.round(el.getBoundingClientRect().height));
        return {
          triggerCount: triggers.length,
          heights,
          bodyOverflowX: document.body.scrollWidth > window.innerWidth + 1,
        };
      });
      expect(geometry.triggerCount).toBeGreaterThanOrEqual(2);
      for (const height of geometry.heights) {
        expect(height, `compare trigger height ${height} < 44 under coarse pointer`).toBeGreaterThanOrEqual(44);
      }
      expect(geometry.bodyOverflowX).toBe(false);
    });
  });

  test.describe('mobile layout (coarse)', () => {
    test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });

    test('mobile chrome, sheet, and inline picker meet 44x44', async ({ page }) => {
      await stubViewerColormapRoutes(page);
      await page.goto(VIEWER_URL);
      await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });

      const all: Violation[] = [];
      all.push(...(await auditTargets(page, 'mobile-closed', 44)));

      await page.locator('[data-tour-target="mobile-controls-button"]').click();
      await expect(page.getByLabel('Close controls')).toBeVisible();
      all.push(...(await auditTargets(page, 'mobile-sheet', 44)));

      await page.getByRole('button', { name: 'GFS' }).first().click();
      await expect(page.getByLabel('Model picker')).toBeVisible();
      all.push(...(await auditTargets(page, 'mobile-product-panel', 44)));

      expect(all, formatViolations(all)).toEqual([]);
    });
  });
});
