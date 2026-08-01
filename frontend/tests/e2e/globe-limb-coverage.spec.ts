/**
 * ════════════════════════════════════════════════════════════════════════════
 * GLOBE LIMB COVERAGE — the grid layer must reach the sphere's true limb
 * ════════════════════════════════════════════════════════════════════════════
 *
 * THIS IS THE TEST THAT WOULD HAVE CAUGHT THE DEFECT. An operator on a retina
 * display reported a thin pixelated line around the globe's limb separating the
 * planet from space — most prominent at the equator, fading toward the poles.
 * Cause: the weather layer is a subdivided sphere mesh, and the mesh's
 * silhouette is a POLYGON inscribed in the true circular limb. Every facet's
 * chord sits inside the limb by its sagitta, so a sliver of MapLibre's own
 * basemap (which does reach the true limb) showed between our data edge and
 * space. At the shipped 64x32 density that sliver measured a mean of 1.9 and a
 * max of 4.1 DEVICE pixels at a 1042-device-px disc radius — invisible to every
 * golden in the suite, because the goldens stub the basemap TRANSPARENT and the
 * sliver is then the same colour as space.
 *
 * The four globe goldens therefore cannot see this class of bug at all, and
 * neither can any DPR-1 test: the defect is a sub-CSS-pixel geometry error that
 * only resolves on a retina buffer. Hence a dedicated, DPR-2, geometry test.
 *
 * ── METHOD ──────────────────────────────────────────────────────────────────
 * TRUE LIMB is MapLibre's own `transform.isPointOnMapSurface()`, bisected along
 * each ray from the disc centre — a ray/planet intersection, exact at the limb,
 * and completely independent of our mesh. (`globeDiscGeometry` agrees with it
 * to well under a pixel and is asserted against it here, so the closed form
 * stays pinned too.)
 *
 * OUR EDGE is measured by ABLATION: capture the raw WebGL surface with the grid
 * layer visible and again with it hidden, and call a device pixel covered where
 * the two differ. An alpha test does NOT work — MapLibre paints the whole disc
 * opaque under globe, so an alpha-based edge measures MapLibre's limb and is
 * identical at 16x8 and 64x32 mesh density (measured; this is why the first cut
 * of the measurement harness reported no defect).
 *
 * CHORD MIDPOINTS ARE THE WORST CASE and are what this samples: the deficit is
 * zero at a mesh vertex and maximal halfway between two, so a handful of
 * compass points aligned with vertices would report a clean limb on a badly
 * faceted mesh. RAYS is set well above the mesh's silhouette facet count so
 * every facet is sampled several times across, midpoints included.
 *
 * The camera is deliberately NOT the goldens' camera: a 1200x1200 viewport at
 * z3 puts the limb at ~521 CSS / ~1042 device px, which is the operator's scale.
 * The deficit is proportional to disc radius, so measuring on the goldens'
 * 150-px disc would divide the signal by seven.
 */
import { expect, test, type Page } from '@playwright/test';

import { stubGoldenBaselineRoutes, viewerUrlForCase } from './render-golden-baseline.fixtures';
import { waitForGoldenReady } from './render-golden-harness';

/** Square and large: the defect scales with disc radius, so measure at scale. */
const VIEWPORT = { width: 1200, height: 1200 };
/** z3 at lat 20 puts the whole limb inside a 1200 px canvas at ~521 CSS px. */
const ZOOM = 3;
/** Rays swept around the limb. >> the mesh's silhouette facet count. */
const RAYS = 720;

/**
 * Budgets, in DEVICE pixels, measured — not guessed.
 *
 * Every number below has two components:
 *   - the real silhouette deficit, which falls off ~quadratically with mesh
 *     density and scales LINEARLY with the disc's device-pixel radius;
 *   - a measurement floor of ~0.35 mean / ~1.6 max device px, from
 *     nearest-device-pixel sampling of a hard, unantialiased clip edge. A
 *     perfect silhouette cannot measure below that floor.
 *
 * Density sweep at this exact camera (tests/e2e/globe-limb-gap.spec.ts), mean
 * and max over all rays, in device px:
 *
 *     mesh       DPR 1 (r=521 dev)     DPR 2 (r=1043 dev)
 *     16x8        10.65 / 22.07          21.05 / 43.13
 *     32x16        3.00 /  6.57           5.73 / 11.63
 *     64x32        0.92 /  2.57           1.63 /  4.13   <- shipped, the defect
 *     96x48        0.56 /  2.07           0.89 /  2.63
 *     128x64       0.44 /  2.07           0.65 /  2.13
 *     192x96       0.36 /  2.07           0.47 /  2.13   <- current default
 *     192x192      0.31 /  1.57           0.40 /  2.13
 *
 * Three things that table settles:
 *   - the deficit is ~3.6x smaller per doubling of density, i.e. quadratic once
 *     the floor is subtracted, which is the signature of a chord sagitta and is
 *     what confirms the diagnosis;
 *   - it is ~2x larger at DPR 2 than at DPR 1 for the same camera, because it
 *     is a fixed CSS-space geometry error rendered onto twice as many device
 *     pixels. That is precisely why the operator sees it on retina and a DPR-1
 *     suite does not;
 *   - 192x96 is ~0.1 device px off 192x192, i.e. off the achievable floor, so
 *     spending 2.2x the mesh on the last axis doubling buys nothing visible.
 *
 * Budgets sit between 192x96 and 64x32 so this is a real gate: ~50% headroom at
 * the shipped density, and 64x32 fails it by 2.3x (DPR 2) / 1.7x (DPR 1) on the
 * mean. Verified by mutation — `GLOBE_LIMB_MESH=64x32` fails both DPRs.
 */
const MAX_MEAN_GAP_DEVICE_PX: Record<number, number> = { 1: 0.55, 2: 0.7 };
/** One budget for both DPRs: the worst ray is floor-dominated at either. */
const MAX_GAP_DEVICE_PX = 3;
/** Coverage must be solid this far inside the limb, at every compass point. */
const SOLID_COVERAGE_FRACTION = 0.995;
const COMPASS_POINTS = 16;

type Reading = {
  dpr: number;
  radiusCssPx: number;
  closedFormRadiusCssPx: number;
  gaps: number[];
  angles: number[];
  compassCovered: boolean[];
};

async function openGlobe(page: Page, meshOverride: string | undefined) {
  await stubGoldenBaselineRoutes(page, 'globe-4326-world');
  const url =
    viewerUrlForCase('globe-4326-world').replace('&z=1&', `&z=${ZOOM}&`) +
    (meshOverride ? `&globeMesh=${meshOverride}` : '');
  await page.goto(url);
  await waitForGoldenReady(page, 'globe-limb-coverage');
}

async function readLimb(page: Page, rays: number, compassPoints: number, solidFraction: number) {
  return page.evaluate(
    async ({ rays: rayCount, compassPoints: compass, solidFraction: solid }) => {
      const w = window as typeof window & {
        __cartoskyViewerCapture: () => Promise<string | null>;
        __cartoskyGlobe: {
          map: {
            setLayoutProperty: (layer: string, prop: string, value: string) => void;
            getStyle: () => { layers: Array<{ id: string; type: string }> };
            transform: {
              width: number;
              height: number;
              worldSize: number;
              cameraToCenterDistance: number;
              center: { lat: number };
              isPointOnMapSurface: (p: { x: number; y: number }) => boolean;
            };
          };
        };
      };
      const map = w.__cartoskyGlobe.map;
      const t = map.transform;
      const cx = t.width / 2;
      const cy = t.height / 2;

      // Everything the basemap style draws ABOVE our grid layer — coastlines,
      // country/state boundaries, city labels — is byte-identical in both
      // ablation captures, so those pixels read as "our layer did not draw
      // here". Near the limb the sphere compresses those vectors into a dense
      // band, which produced intermittent 20+ device-px false deficits over
      // whole arcs (measured: 128-151 deg, the crowded North American west
      // coast). Hiding every non-background layer but ours leaves the ablation
      // measuring exactly one thing.
      for (const layer of map.getStyle().layers) {
        if (layer.id === 'twf-grid-webgl' || layer.type === 'background') continue;
        try {
          map.setLayoutProperty(layer.id, 'visibility', 'none');
        } catch {
          // A layer without a layout slot is not one that can occlude us.
        }
      }
      await new Promise<void>((resolve) => { setTimeout(resolve, 400); });

      // globeDiscGeometry's closed form, replicated so the page needs no new
      // export; asserted against the bisection by the caller.
      const R = t.worldSize / (2 * Math.PI) / Math.cos((t.center.lat * Math.PI) / 180);
      const D = t.cameraToCenterDistance + R;
      const closedForm = (t.cameraToCenterDistance * R) / Math.sqrt(D * D - R * R);

      const grab = async () => {
        const dataUrl = await w.__cartoskyViewerCapture();
        const bitmap = await createImageBitmap(await (await fetch(dataUrl!)).blob());
        const scratch = document.createElement('canvas');
        scratch.width = bitmap.width;
        scratch.height = bitmap.height;
        const ctx = scratch.getContext('2d', { willReadFrequently: true })!;
        ctx.drawImage(bitmap, 0, 0);
        bitmap.close();
        return ctx.getImageData(0, 0, scratch.width, scratch.height);
      };
      const settle = () => new Promise<void>((resolve) => { setTimeout(resolve, 300); });

      const trueLimb = (dx: number, dy: number) => {
        let lo = 1;
        let hi = Math.min(t.width, t.height);
        for (let step = 0; step < 40; step += 1) {
          const mid = (lo + hi) / 2;
          if (t.isPointOnMapSurface({ x: cx + dx * mid, y: cy + dy * mid })) lo = mid;
          else hi = mid;
        }
        return (lo + hi) / 2;
      };

      // Ray table is camera-only, so it is computed once and shared by every
      // ablation repeat.
      const rays_ = [] as Array<{ theta: number; dx: number; dy: number; limb: number }>;
      for (let i = 0; i < rayCount; i += 1) {
        const theta = (i / rayCount) * Math.PI * 2;
        const dx = Math.cos(theta);
        const dy = -Math.sin(theta);
        rays_.push({ theta, dx, dy, limb: trueLimb(dx, dy) });
      }
      const compassRays = [] as Array<{ dx: number; dy: number; limb: number }>;
      for (let i = 0; i < compass; i += 1) {
        const theta = (i / compass) * Math.PI * 2;
        const dx = Math.cos(theta);
        const dy = -Math.sin(theta);
        compassRays.push({ dx, dy, limb: trueLimb(dx, dy) });
      }

      /**
       * REPEATS, and why the estimator is a MINIMUM.
       *
       * A capture pair can land on a frame where MapLibre has not finished
       * re-rasterizing after the visibility toggle, which shows up as a
       * localized band of "not covered" (measured: ~23 device px deep over a
       * few degrees, intermittently, at any mesh density). That error can only
       * ever REMOVE coverage, never invent it — so taking the per-ray minimum
       * gap over independent repeats cancels it without being able to mask a
       * real deficit, which is present in every repeat by construction.
       */
      const REPEATS = 3;
      const bestGap = new Array<number>(rayCount).fill(Number.POSITIVE_INFINITY);
      const compassCovered = new Array<boolean>(compass).fill(false);
      let dpr = 1;
      for (let rep = 0; rep < REPEATS; rep += 1) {
        const withLayer = await grab();
        map.setLayoutProperty('twf-grid-webgl', 'visibility', 'none');
        await settle();
        const withoutLayer = await grab();
        map.setLayoutProperty('twf-grid-webgl', 'visibility', 'visible');
        await settle();

        dpr = withLayer.width / t.width;
        const covered = (x: number, y: number) => {
          const px = Math.round(x * dpr);
          const py = Math.round(y * dpr);
          if (px < 0 || py < 0 || px >= withLayer.width || py >= withLayer.height) return false;
          const at = (py * withLayer.width + px) * 4;
          return (
            Math.max(
              Math.abs(withLayer.data[at] - withoutLayer.data[at]),
              Math.abs(withLayer.data[at + 1] - withoutLayer.data[at + 1]),
              Math.abs(withLayer.data[at + 2] - withoutLayer.data[at + 2]),
              Math.abs(withLayer.data[at + 3] - withoutLayer.data[at + 3]),
            ) > 12
          );
        };

        const stepCss = 0.5 / dpr;
        for (let i = 0; i < rayCount; i += 1) {
          const { dx, dy, limb } = rays_[i];
          let edge = Number.NaN;
          for (let r = limb * 0.95; r <= limb + 2 / dpr; r += stepCss) {
            if (covered(cx + dx * r, cy + dy * r)) edge = r;
          }
          if (!Number.isFinite(edge)) continue;
          bestGap[i] = Math.min(bestGap[i], (limb - edge) * dpr);
        }
        for (let i = 0; i < compass; i += 1) {
          const { dx, dy, limb } = compassRays[i];
          if (covered(cx + dx * limb * solid, cy + dy * limb * solid)) compassCovered[i] = true;
        }
      }

      const gaps: number[] = [];
      const angles: number[] = [];
      for (let i = 0; i < rayCount; i += 1) {
        if (!Number.isFinite(bestGap[i])) continue;
        gaps.push(bestGap[i]);
        angles.push(rays_[i].theta);
      }

      return {
        dpr,
        radiusCssPx: rays_.reduce((sum, ray) => sum + ray.limb, 0) / rayCount,
        closedFormRadiusCssPx: closedForm,
        gaps,
        angles,
        compassCovered,
      };
    },
    { rays, compassPoints, solidFraction },
  );
}

function summarize(reading: Reading) {
  const sorted = [...reading.gaps].sort((a, b) => a - b);
  const mean = sorted.reduce((a, b) => a + b, 0) / Math.max(1, sorted.length);
  return {
    rays: sorted.length,
    mean,
    max: sorted[sorted.length - 1] ?? Number.NaN,
    p95: sorted[Math.floor(sorted.length * 0.95)] ?? Number.NaN,
  };
}

/** Mesh override for the mutation check; unset in normal runs. */
const MESH_OVERRIDE = process.env.GLOBE_LIMB_MESH;

test.describe('Globe limb coverage', () => {
  test.describe.configure({ mode: 'serial' });

  for (const dpr of [2, 1]) {
    test(`grid layer reaches the true limb at DPR ${dpr}`, async ({ browser }) => {
      test.setTimeout(300_000);
      const context = await browser.newContext({
        viewport: VIEWPORT,
        deviceScaleFactor: dpr,
        timezoneId: 'UTC',
        locale: 'en-US',
        colorScheme: 'light',
      });
      const page = await context.newPage();
      await page.addInitScript(() => localStorage.setItem('csky_viewer_tour_v1', 'completed'));
      try {
        await openGlobe(page, MESH_OVERRIDE);
        const reading = (await readLimb(
          page,
          RAYS,
          COMPASS_POINTS,
          SOLID_COVERAGE_FRACTION,
        )) as Reading;

        expect(reading.dpr, 'context deviceScaleFactor reached the drawing buffer').toBeCloseTo(dpr, 3);
        // A ray has no measurable edge when our layer's colour happens to match
        // the layer-off render there to within the ablation tolerance. Measured
        // at up to 6 rays in 1440; 2% is generous headroom over that, and a
        // wholesale loss of the edge (a blank or mis-projected layer) still
        // fails here rather than passing with a tiny sample.
        expect(reading.gaps.length, 'rays with a measurable data edge').toBeGreaterThanOrEqual(
          Math.floor(RAYS * 0.98),
        );
        // The closed-form disc radius stays pinned to MapLibre's own ray test.
        expect(
          Math.abs(reading.radiusCssPx - reading.closedFormRadiusCssPx),
          'globeDiscGeometry vs transform.isPointOnMapSurface',
        ).toBeLessThan(0.5);

        const stats = summarize(reading);
        const worst = reading.gaps
          .map((gap, index) => ({ gap, deg: (reading.angles[index] * 180) / Math.PI }))
          .sort((a, b) => b.gap - a.gap)
          .slice(0, 6)
          .map((entry) => `${entry.deg.toFixed(1)}deg:${entry.gap.toFixed(2)}`)
          .join(' ');
        // eslint-disable-next-line no-console
        console.log(
          `[limb-coverage] dpr=${dpr} radius=${(reading.radiusCssPx * dpr).toFixed(1)}dev ` +
            `rays=${stats.rays} mean=${stats.mean.toFixed(3)} p95=${stats.p95.toFixed(3)} ` +
            `max=${stats.max.toFixed(3)} device px | worst ${worst}`,
        );

        expect(
          reading.compassCovered.every(Boolean),
          `grid layer is solid at ${SOLID_COVERAGE_FRACTION} of the limb radius at all ${COMPASS_POINTS} compass points`,
        ).toBe(true);

        // The discriminating assertions.
        expect(stats.mean, 'mean limb deficit around the whole disc').toBeLessThanOrEqual(
          MAX_MEAN_GAP_DEVICE_PX[dpr],
        );
        expect(stats.max, 'worst-case limb deficit (chord midpoint)').toBeLessThanOrEqual(
          MAX_GAP_DEVICE_PX,
        );
      } finally {
        await context.close();
      }
    });
  }
});
