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
 *
 * PHASE 6 RE-TARGETING (2026-07-29, Phase 6 plan Task 4). The audited chrome
 * moved: the desktop header trigger row and the display-settings panel no
 * longer exist. The matrix is re-pointed, not reduced —
 *   header trigger row      → `viewer-rail` SOURCE section (expanded)
 *   display-settings panel  → `viewer-rail` VIEW section (toggles, opacity,
 *                             region row)
 *   header legend panel     → expanded-rail legend mount + the floating panel
 *                             that still serves the collapsed rail
 *   header attribution chips→ overflow → Attribution dialog
 *   header Share / Feedback → 48 px top bar Share + `•••` overflow menu
 * and two states are added that had no pre-Phase-6 equivalent: the collapsed
 * rail (11 px captions, ≥44 coarse items) and the overflow menu. The tablet
 * header-wrap geometry test becomes rail-at-tablet geometry: ≥768 now renders
 * bar + rail, NOT the mobile sheet (the mobile threshold moved 639 → 767).
 *
 * PHASE 7 EXTENSION (2026-07-29, Phase 7 plan Task 5b). Two states join the
 * matrix, both new surfaces rather than re-points:
 *   legend-chip           the §7.2 compact map-corner chip that now serves the
 *                         collapsed rail (it replaced the Phase 6 interim
 *                         floating full legend, which the `rail-collapsed`
 *                         state audited only incidentally)
 *   legend-chip-expanded  the chip's in-place expanded panel (full inline
 *                         legend), reachable from no other surface
 * and `rail-collapsed` now carries the third rail item (`rail-collapsed-legend`,
 * §6.3). Thresholds are unchanged: ≥32 fine / ≥44 coarse targets, ≥11 px type
 * floor, exhaustive focus walk with the cyan outline token.
 */
import { test, expect, type Page } from '@playwright/test';

import { COLORMAP_VIEW, stubViewerColormapRoutes } from './viewer-colormap.fixtures';

const VIEWER_URL =
  `/viewer?m=gfs&r=latest&v=tmp2m&fh=0&reg=conus&lat=${COLORMAP_VIEW.lat}&lon=${COLORMAP_VIEW.lon}&z=${COLORMAP_VIEW.zoom}`;
const COMPARE_URL =
  `/compare?lm=gfs&lv=tmp2m&lr=latest&rm=gfs&rv=tmp2m&rr=latest&fh=0&lat=${COLORMAP_VIEW.lat}&lon=${COLORMAP_VIEW.lon}&z=5`;

const FOCUS_RING_RGB = '103, 232, 249';
const FOCUS_RING_MIN_WIDTH_PX = 2;

/** Kept in sync with lib/viewer-rail.ts. */
const RAIL_EXPANDED_PX = 288;
const RAIL_COLLAPSED_PX = 72;

type Violation = { surface: string; selector: string; detail: string };

const rail = (page: Page) => page.getByTestId('viewer-rail');
const topBar = (page: Page) => page.getByTestId('viewer-top-bar');
const railSource = (page: Page) => page.getByTestId('rail-source');

async function expandRail(page: Page) {
  if ((await rail(page).getAttribute('data-rail-state')) === 'collapsed') {
    await page.getByTestId('rail-expand-toggle').click();
  }
  await expect(rail(page)).toHaveAttribute('data-rail-state', 'expanded');
  await expect(railSource(page).getByRole('button', { name: 'GFS' })).toBeVisible();
}

async function collapseRail(page: Page) {
  if ((await rail(page).getAttribute('data-rail-state')) === 'expanded') {
    await page.getByTestId('rail-collapse-toggle').click();
  }
  await expect(rail(page)).toHaveAttribute('data-rail-state', 'collapsed');
  await expect(page.getByTestId('rail-collapsed-source')).toBeVisible();
}

/**
 * Loads the fixed-fixture viewer and leaves the rail EXPANDED, so the surface
 * matrix is deterministic regardless of the project viewport's default rail
 * state (§6.4 computes it from map width). The collapsed rail is its own
 * audited surface below.
 */
async function openViewer(page: Page) {
  await stubViewerColormapRoutes(page);
  await page.goto(VIEWER_URL);
  await expect(rail(page)).toBeVisible({ timeout: 30_000 });
  await expandRail(page);
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
  // Closed chrome = 48 px top bar + expanded rail (SOURCE, VIEW, legend mount),
  // timeline, zoom stack, map-corner controls.
  { name: 'closed-chrome' },
  {
    // Successor to the pre-Phase-6 `display-settings` panel state: the VIEW
    // section owns the toggles, the opacity slider and the region row.
    name: 'rail-view',
    setup: async (page) => {
      const view = page.getByTestId('rail-view');
      await view.scrollIntoViewIfNeeded();
      await expect(view.getByTestId('rail-region-value')).toBeVisible();
      await expect(view.getByTestId('rail-opacity-value')).toBeVisible();
    },
  },
  {
    name: 'product-panel',
    setup: async (page) => {
      await railSource(page).getByRole('button', { name: 'GFS' }).click();
      await expect(page.getByLabel('Model picker')).toBeVisible();
    },
    teardown: escapeTeardown,
  },
  {
    name: 'variable-panel',
    setup: async (page) => {
      // Trigger label is the frontend VARIABLE_UI_OVERRIDES display name.
      await railSource(page).getByRole('button', { name: 'Surface Temp' }).click();
      await expect(page.getByLabel('Variable picker')).toBeVisible();
    },
    teardown: escapeTeardown,
  },
  {
    name: 'statistic-popover',
    setup: async (page) => {
      await railSource(page).getByRole('button', { name: 'Mean' }).click();
      await expect(page.getByRole('button', { name: 'Percentile' })).toBeVisible();
    },
    teardown: escapeTeardown,
  },
  {
    name: 'run-select',
    setup: async (page) => {
      await railSource(page).getByRole('combobox').first().click();
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
      await rail(page).locator('[aria-label^="Region:"]').click();
      await expect(page.getByPlaceholder('Search city or zip…')).toBeVisible();
    },
    teardown: escapeTeardown,
  },
  {
    // New in Phase 6: the `•••` menu absorbed Feedback, Compare, Replay tour,
    // Keyboard shortcuts, Attribution and the sign-in/account entry.
    name: 'overflow-menu',
    setup: async (page) => {
      await page.getByTestId('viewer-overflow-trigger').click();
      await expect(page.getByTestId('viewer-overflow-menu')).toBeVisible();
    },
    teardown: escapeTeardown,
  },
  {
    // Successor to the display panel's attribution chip links, which the
    // Phase 4 audit called out explicitly (no inline-prose exemption).
    name: 'attribution-dialog',
    setup: async (page) => {
      await page.getByTestId('viewer-overflow-trigger').click();
      await page.getByTestId('viewer-overflow-menu').getByRole('menuitem', { name: 'Attribution' }).click();
      await expect(page.getByRole('dialog', { name: 'Map attribution' })).toBeVisible();
    },
    teardown: async (page) => {
      await page.getByLabel('Close attribution').click();
      await expect(page.getByRole('dialog', { name: 'Map attribution' })).toBeHidden();
    },
  },
  {
    name: 'share-dialog',
    setup: async (page) => {
      await topBar(page).getByRole('button', { name: 'Share' }).click();
      await expect(page.getByRole('dialog', { name: 'Share' })).toBeVisible({ timeout: 15_000 });
    },
    teardown: async (page) => {
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog', { name: 'Share' })).toBeHidden();
    },
  },
  {
    // New in Phase 6 (§6.3), extended in Phase 7: the 72 px rail — expand
    // chevron plus icon+caption Source/View/**Legend** items.
    name: 'rail-collapsed',
    setup: async (page) => {
      await collapseRail(page);
      await expect(page.getByTestId('rail-collapsed-legend')).toBeVisible();
    },
    // Left collapsed for the two chip surfaces below; `legend-chip-expanded`
    // restores the expanded rail at the end of the matrix.
  },
  {
    // New in Phase 7 (§7.2): the compact map-corner chip that serves the
    // collapsed rail. It replaced the Phase 6 interim floating full legend.
    name: 'legend-chip',
    setup: async (page) => {
      const chip = page.getByTestId('compact-legend-chip');
      await expect(chip).toBeVisible({ timeout: 20_000 });
      await expect(chip.getByTestId('compact-legend-chip-toggle')).toBeVisible();
    },
  },
  {
    // New in Phase 7 (§7.2 decision 3): the chip expanded in place into the
    // full inline legend — a surface reachable from nowhere else.
    name: 'legend-chip-expanded',
    setup: async (page) => {
      await page.getByTestId('compact-legend-chip').getByTestId('compact-legend-chip-toggle').click();
      await expect(page.getByTestId('compact-legend-chip-panel')).toBeVisible();
    },
    teardown: async (page) => {
      await page.getByTestId('compact-legend-chip').getByTestId('compact-legend-chip-toggle').click();
      await expect(page.getByTestId('compact-legend-chip-panel')).toBeHidden();
      await expandRail(page);
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
          // DOCUMENTED EXCEPTION — the opacity slider (now the rail VIEW
          // slider) keeps the stock 16px Radix thumb: enlarging it re-treads
          // the Phase 4 wrapper positioning regressions. `ui/slider.tsx` is
          // shared with the Phase 5 timeline thumb, which IS fully compliant;
          // widening the shared primitive is owned by its own change, not by
          // this audit. Exception carried forward from Phase 4 unchanged.
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
  for (let step = 0; step < 160; step += 1) {
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
    // 1440×900: §6.4 default is the EXPANDED rail (1152 px of map width).
    test.use({ viewport: { width: 1440, height: 900 } });

    test('every interactive element is at least 32x32 across all surfaces', async ({ page }) => {
      test.slow(); // 13-surface matrix (Phase 7 added the compact chip and its expanded panel).
      await openViewer(page);
      const violations = await runSurfaces(page, (p, s) => auditTargets(p, s, 32));
      expect(violations, formatViolations(violations)).toEqual([]);
    });

    test('no viewer-chrome text below 11px, operational labels at least 12px', async ({ page }) => {
      test.slow();
      await openViewer(page);
      const violations = await runSurfaces(page, (p, s) => auditTypeFloor(p, s, 11));
      expect(violations, formatViolations(violations)).toEqual([]);

      // Operational labels (explicit inventory list — classification is human).
      // Phase 6: the field captions moved from the header row into the rail
      // SOURCE section and gained the two section headers.
      const operational = await page.evaluate(() => {
        const probes: Array<{ label: string; text: string }> = [
          { label: 'rail-section-source', text: 'Source' },
          { label: 'rail-section-view', text: 'View' },
          { label: 'field-caption-model', text: 'Model' },
          { label: 'field-caption-variable', text: 'Variable' },
          { label: 'field-caption-statistic', text: 'Statistic' },
          { label: 'field-caption-run', text: 'Run' },
          { label: 'field-caption-region', text: 'Region' },
          // 'timeline-model' probe retired in Phase 5: the desktop timeline no
          // longer carries a model label (identity now lives in the rail
          // Model trigger; the mobile bar keeps its 12px label, covered by the
          // mobile audit state). Pre-Phase-5 this probe was satisfied only by
          // a display:none mobile duplicate — a latent probe bug.
        ];
        const results: Array<{ label: string; size: number | null }> = [];
        const root = document.querySelector('[data-testid="viewer-rail"]');
        for (const probe of probes) {
          const match = root
            ? Array.from(root.querySelectorAll('span, div')).filter(
              (el) => {
                const rect = el.getBoundingClientRect();
                // The collapsed body stays mounted (display:none) so a width
                // toggle never remounts a picker; its 11 px captions are the
                // §6.3 contract, not operational labels. Rect-filter to the
                // rendered, expanded section/field labels.
                return rect.width > 0 && rect.height > 0;
              },
            ).find(
              (el) =>
                (el.textContent ?? '').trim() === probe.text
                && Array.from(el.childNodes).some(
                  (c) => c.nodeType === Node.TEXT_NODE && (c.textContent ?? '').trim().length > 0,
                ),
            )
            : undefined;
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
        // Phase 6: the trigger family lives in the rail SOURCE section.
        const source = document.querySelector('[data-testid="rail-source"]')!;
        const byText = (text: string) =>
          Array.from(source.querySelectorAll('button')).find(
            (el) => (el.textContent ?? '').trim().startsWith(text),
          ) ?? null;
        const run = source.querySelector('[role="combobox"]');
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
      test.slow(); // exhaustive Tab-walk × 13 surfaces.
      await openViewer(page);
      const violations = await runSurfaces(page, (p, s) => auditFocus(p, s));
      expect(violations, formatViolations(violations)).toEqual([]);

      // Mouse focus must NOT show the ring.
      const productTrigger = railSource(page).getByRole('button', { name: 'GFS' });
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

    test('collapsed rail keeps captions at the 11px floor and items above the fine floor', async ({ page }) => {
      test.slow();
      // 1200×800 → 912 px of map width → §6.4 default is COLLAPSED.
      await page.setViewportSize({ width: 1200, height: 800 });
      await stubViewerColormapRoutes(page);
      await page.goto(VIEWER_URL);
      await expect(rail(page)).toBeVisible({ timeout: 30_000 });
      await expect(rail(page)).toHaveAttribute('data-rail-state', 'collapsed');
      await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });

      const geometry = await page.evaluate(() => {
        const railEl = document.querySelector('[data-testid="viewer-rail"]')!;
        const measure = (testId: string) => {
          const el = document.querySelector(`[data-testid="${testId}"]`);
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return { width: r.width, height: r.height };
        };
        const captions = Array.from(railEl.querySelectorAll('[data-testid="rail-collapsed-caption"]')).map((el) => ({
          text: (el.textContent ?? '').trim(),
          fontSize: Number.parseFloat(window.getComputedStyle(el).fontSize),
        }));
        return {
          railWidth: Math.round(railEl.getBoundingClientRect().width),
          expandToggle: measure('rail-expand-toggle'),
          source: measure('rail-collapsed-source'),
          view: measure('rail-collapsed-view'),
          legend: measure('rail-collapsed-legend'),
          chipToggle: measure('compact-legend-chip-toggle'),
          captions,
        };
      });

      expect(geometry.railWidth).toBe(RAIL_COLLAPSED_PX);
      // Phase 7 (§6.3): the third rail item joined Source and View.
      expect(geometry.captions.map((c) => c.text)).toEqual(['Source', 'View', 'Legend']);
      for (const caption of geometry.captions) {
        expect(caption.fontSize, `collapsed caption "${caption.text}" is ${caption.fontSize}px`).toBeGreaterThanOrEqual(11);
      }
      for (const [name, box] of [
        ['rail-expand-toggle', geometry.expandToggle],
        ['rail-collapsed-source', geometry.source],
        ['rail-collapsed-view', geometry.view],
        ['rail-collapsed-legend', geometry.legend],
        // The §7.2 chip serves this state; its toggle is a first-class target.
        ['compact-legend-chip-toggle', geometry.chipToggle],
      ] as const) {
        expect(box, `${name} missing`).not.toBeNull();
        expect(Math.min(box!.width, box!.height), `${name} min side ${JSON.stringify(box)} < 32`).toBeGreaterThanOrEqual(32);
      }

      // Full audits over the default-collapsed state (chip included).
      const targets = await auditTargets(page, 'collapsed-default', 32);
      expect(targets, formatViolations(targets)).toEqual([]);
      const type = await auditTypeFloor(page, 'collapsed-default', 11);
      expect(type, formatViolations(type)).toEqual([]);
      const focus = await auditFocus(page, 'collapsed-default');
      expect(focus, formatViolations(focus)).toEqual([]);

      // …and over the chip expanded in place into the full inline legend.
      await page.getByTestId('compact-legend-chip').getByTestId('compact-legend-chip-toggle').click();
      await expect(page.getByTestId('compact-legend-chip-panel')).toBeVisible();
      const panelTargets = await auditTargets(page, 'collapsed-chip-expanded', 32);
      expect(panelTargets, formatViolations(panelTargets)).toEqual([]);
      const panelType = await auditTypeFloor(page, 'collapsed-chip-expanded', 11);
      expect(panelType, formatViolations(panelType)).toEqual([]);
      const panelFocus = await auditFocus(page, 'collapsed-chip-expanded');
      expect(panelFocus, formatViolations(panelFocus)).toEqual([]);
    });
  });

  test.describe('desktop layout under a coarse pointer', () => {
    // A large touch display (1440x1000 exceeds both tablet-touch thresholds)
    // keeps the DESKTOP layout while hasTouch makes (pointer: coarse) real —
    // the touchscreen-desktop case. Phase 6 closed the pre-existing gap here:
    // tablet-touch used to have no display-settings surface at all; VIEW now
    // lives in the rail, so every layout reaches it.
    test.use({ hasTouch: true, viewport: { width: 1440, height: 1000 } });

    test('rail VIEW controls meet the 44px floor', async ({ page }) => {
      await openViewer(page);
      const view = page.getByTestId('rail-view');
      await view.scrollIntoViewIfNeeded();
      await expect(view.getByTestId('rail-region-value')).toBeVisible();
      await expect(view.getByTestId('rail-opacity-value')).toBeVisible();
      const violations = await auditTargets(page, 'rail-view-coarse', 44);
      expect(violations, formatViolations(violations)).toEqual([]);
    });
  });

  test.describe('coarse pointer', () => {
    test.use({ hasTouch: true });

    test('every interactive element is at least 44x44 across all surfaces', async ({ page }) => {
      test.slow();
      // The legend follows the existing `twf.map.legend_visible` preference,
      // whose default is OFF on non-desktop layouts (use-display-settings.ts).
      // Opt in explicitly so the Phase 7 chip surfaces exist under the coarse
      // regime too, rather than silently dropping them from the matrix.
      await page.addInitScript(() => localStorage.setItem('twf.map.legend_visible', 'true'));
      await openViewer(page);
      const violations = await runSurfaces(page, (p, s) => auditTargets(p, s, 44));
      expect(violations, formatViolations(violations)).toEqual([]);
    });

    test('rail chrome fits tablet viewports without overlap or overflow', async ({ page }) => {
      test.slow();
      // Zoom controls default hidden on non-desktop layouts; the geometry
      // anchor needs them, so seed the persisted preference on.
      await page.addInitScript(() => localStorage.setItem('twf.map.zoom_controls_visible', 'true'));
      // §6.4: ≥768 gets bar + rail, NOT the mobile sheet (threshold 639 → 767).
      for (const viewport of [{ width: 768, height: 1024 }, { width: 1024, height: 768 }]) {
        await page.setViewportSize(viewport);
        await openViewer(page);
        const label = `${viewport.width}x${viewport.height}`;

        const geometry = await page.evaluate(() => {
          const bar = document.querySelector('[data-testid="viewer-top-bar"]')!;
          const barRect = bar.getBoundingClientRect();
          const railEl = document.querySelector('[data-testid="viewer-rail"]');
          const railRect = railEl?.getBoundingClientRect() ?? null;
          const rowRects = railEl
            ? Array.from(railEl.querySelectorAll('button, [role="combobox"]'))
              .map((el) => el.getBoundingClientRect())
              .filter((r) => r.width > 0 && r.height > 0)
            : [];
          const zoomIn = document.querySelector('[aria-label="Zoom in"]');
          const zoomRect = zoomIn?.getBoundingClientRect() ?? null;
          return {
            barHeight: barRect.height,
            barBottom: barRect.bottom,
            barOverflowX: bar.scrollWidth > bar.clientWidth + 1,
            railPresent: Boolean(railEl),
            railWidth: railRect ? Math.round(railRect.width) : null,
            railTop: railRect?.top ?? null,
            railRight: railRect?.right ?? null,
            railRowCount: rowRects.length,
            railRowsInViewport: rowRects.every(
              (r) => r.left >= -1 && r.right <= window.innerWidth + 1,
            ),
            zoomLeft: zoomRect?.left ?? null,
            zoomTop: zoomRect?.top ?? null,
            zoomVisible: zoomRect ? zoomRect.top >= 0 && zoomRect.bottom <= window.innerHeight : false,
            mobileSheetPresent: Boolean(document.querySelector('[data-tour-target="mobile-controls-button"]')),
            documentOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
          };
        });

        // Bar geometry stays sane (48 px baseline; coarse targets may grow it,
        // never past the mobile header's own 56 px + one wrapped row).
        expect(geometry.barHeight, `${label}: bar height ${geometry.barHeight}`).toBeGreaterThanOrEqual(47);
        expect(geometry.barHeight, `${label}: bar height ${geometry.barHeight}`).toBeLessThanOrEqual(112);
        expect(geometry.barOverflowX, `${label}: top bar overflows horizontally`).toBe(false);

        // The rail — not the mobile sheet — owns this band.
        expect(geometry.railPresent, `${label}: rail missing`).toBe(true);
        expect(geometry.mobileSheetPresent, `${label}: mobile sheet rendered above the 767 threshold`).toBe(false);
        expect([RAIL_EXPANDED_PX, RAIL_COLLAPSED_PX]).toContain(geometry.railWidth);

        // The rail starts below the bar and its rows stay inside the viewport.
        expect(
          geometry.railTop!,
          `${label}: rail top ${geometry.railTop} above bar bottom ${geometry.barBottom}`,
        ).toBeGreaterThanOrEqual(geometry.barBottom - 1);
        expect(geometry.railRowCount, `${label}: no rail rows measured`).toBeGreaterThan(0);
        expect(geometry.railRowsInViewport, `${label}: a rail row escapes the viewport`).toBe(true);

        // Zoom stack clears both the bar and the rail.
        expect(geometry.zoomTop, `${label}: zoom control missing`).not.toBeNull();
        expect(
          geometry.zoomTop!,
          `${label}: bar bottom ${geometry.barBottom} overlaps zoom top ${geometry.zoomTop}`,
        ).toBeGreaterThanOrEqual(geometry.barBottom);
        expect(
          geometry.zoomLeft!,
          `${label}: rail right ${geometry.railRight} overlaps zoom left ${geometry.zoomLeft}`,
        ).toBeGreaterThanOrEqual(geometry.railRight!);
        expect(geometry.zoomVisible, `${label}: zoom stack outside the viewport`).toBe(true);

        expect(geometry.documentOverflowX, `${label}: document overflows horizontally`).toBe(false);
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
    // Below the §6.4 threshold (767) the mobile sheet owns the chrome.
    //
    // PHASE 8 RE-TARGETING (2026-07-29, Phase 8 plan Task 4). The audited
    // mobile chrome moved into the §8 three states; the matrix is re-pointed
    // and widened, not reduced —
    //   56 px header + ViewerNavMobile tabs → 52 px `viewer-mobile-bar`
    //                             (labeled Share + `•••` overflow)
    //   `mobile-controls-button`  → the always-present 84 px sheet peek
    //                             (`mobile-sheet-peek`, tap to expand)
    //   Selection / Display tabs  → scrollable Source → View → Legend sections
    //   bottom controls row       → the 64 px one-row `mobile-timeline-row`
    // and the single pre-Phase-8 open state becomes BOTH sheet snaps (half and
    // full), since the sections scroll differently at each. Thresholds are
    // unchanged: ≥44 coarse targets, ≥11 px type floor, exhaustive focus walk.
    test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });

    /** The handle advances peek → half → full → peek. */
    async function snapTo(page: Page, snap: 'peek' | 'half' | 'full') {
      const sheet = page.getByTestId('mobile-sheet');
      for (let guard = 0; guard < 4; guard += 1) {
        if ((await sheet.getAttribute('data-snap')) === snap) return;
        await page.getByTestId('mobile-sheet-handle').click();
      }
      await expect(sheet).toHaveAttribute('data-snap', snap);
    }

    async function openMobileViewer(page: Page) {
      await stubViewerColormapRoutes(page);
      await page.goto(VIEWER_URL);
      await expect(page.getByTestId('viewer-initial-map-scrim')).toBeHidden({ timeout: 30_000 });
      await expect(page.getByTestId('viewer-rail')).toHaveCount(0);
      await expect(page.getByTestId('viewer-mobile-bar')).toBeVisible({ timeout: 20_000 });
      await expect(page.getByTestId('mobile-sheet')).toHaveAttribute('data-snap', 'peek');
    }

    test('mobile bar, peek, timeline row, sheet sections and inline picker meet 44x44', async ({ page }) => {
      test.slow();
      await openMobileViewer(page);

      // State A surfaces are all mounted at rest.
      await expect(page.getByTestId('mobile-sheet-peek')).toBeVisible();
      await expect(page.getByTestId('mobile-timeline-row')).toBeVisible();
      await expect(page.getByTestId('viewer-mobile-bar').getByRole('button', { name: 'Share' })).toBeVisible();

      const all: Violation[] = [];
      all.push(...(await auditTargets(page, 'mobile-rest', 44)));

      // The bar's `•••` overflow is reachable from no other surface.
      await page.getByTestId('mobile-bar-overflow-trigger').click();
      await expect(page.getByTestId('mobile-bar-overflow-menu')).toBeVisible();
      all.push(...(await auditTargets(page, 'mobile-bar-overflow', 44)));
      await page.keyboard.press('Escape');
      await expect(page.getByTestId('mobile-bar-overflow-menu')).toBeHidden();

      await snapTo(page, 'half');
      await expect(page.getByLabel('Close controls')).toBeVisible();
      all.push(...(await auditTargets(page, 'mobile-sheet-half', 44)));

      await snapTo(page, 'full');
      await expect(page.getByTestId('mobile-section-heading').first()).toBeVisible();
      all.push(...(await auditTargets(page, 'mobile-sheet-full', 44)));

      await page.getByRole('button', { name: 'GFS' }).first().click();
      await expect(page.getByLabel('Model picker')).toBeVisible();
      all.push(...(await auditTargets(page, 'mobile-product-panel', 44)));

      expect(all, formatViolations(all)).toEqual([]);
    });

    test('mobile chrome holds the 11px type floor and the focus-ring token', async ({ page }) => {
      test.slow();
      await openMobileViewer(page);

      const all: Violation[] = [];
      all.push(...(await auditTypeFloor(page, 'mobile-rest', 11)));
      all.push(...(await auditFocus(page, 'mobile-rest')));

      await snapTo(page, 'full');
      await expect(page.getByTestId('mobile-section-heading').first()).toBeVisible();
      all.push(...(await auditTypeFloor(page, 'mobile-sheet-full', 11)));
      all.push(...(await auditFocus(page, 'mobile-sheet-full')));

      expect(all, formatViolations(all)).toEqual([]);
    });
  });
});
