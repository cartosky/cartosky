/**
 * The live-map half of globe support: everything that needs a MapLibre `Map`
 * instance, kept out of `globe-projection.ts` so that module stays importable
 * from unit tests without a WebGL context.
 *
 * Three concerns, all of them CAMERA concerns — none of this influences which
 * artifact is fetched:
 *
 *  1. Where the disc is on the canvas, and whether a cursor is on it
 *     (`isCursorOnGlobe`). Fixes the audit's item 4: the hover readout pinned to
 *     the horizon value and kept rendering far outside the disc.
 *  2. Whether a lng/lat is on the near hemisphere (`isLngLatVisibleOnGlobe`).
 *     Fixes the audit's item 1 / risk 1: the city label layers set both
 *     `text-allow-overlap` and `text-ignore-placement`, so MapLibre does no
 *     placement of its own and will happily draw far-side labels.
 *  3. The polar-framing camera constraint (`applyGlobeCameraConstrain`). Fixes
 *     the audit's item 10c.
 *
 * See docs/GLOBE_OVERLAY_AUDIT_2026-08-01.md.
 */

import type maplibregl from "maplibre-gl";

import { globeDiscGeometry, isPointInsideGlobeDisc, type GlobeDisc } from "@/lib/globe-projection";

/**
 * MapLibre internals this module reaches for. Each one is a typed member of the
 * public `ITransform` interface in maplibre-gl.d.ts — `isPointOnMapSurface`
 * (:6037), `isLocationOccluded` (:6119), `setConstrainOverride` (:5909) — but
 * `Map.transform` itself is not in the public surface, so the access is typed
 * here and every use is written to degrade rather than throw.
 */
type GlobeTransform = {
  worldSize?: number;
  cameraToCenterDistance?: number;
  width?: number;
  height?: number;
  center?: { lat: number; lng: number };
  isPointOnMapSurface?: (point: { x: number; y: number }) => boolean;
  isLocationOccluded?: (lngLat: { lng: number; lat: number }) => boolean;
  setConstrainOverride?: (
    constrain:
      | ((lngLat: { lng: number; lat: number }, zoom: number) => { center: unknown; zoom: number })
      | null,
  ) => void;
};

function readTransform(map: maplibregl.Map | null | undefined): GlobeTransform | null {
  const transform = (map as unknown as { transform?: GlobeTransform } | null)?.transform;
  return transform && typeof transform === "object" ? transform : null;
}

/** Disc geometry for the map's current camera, or null if it can't be derived. */
export function readGlobeDisc(map: maplibregl.Map | null | undefined): GlobeDisc | null {
  const transform = readTransform(map);
  if (!transform) {
    return null;
  }
  return globeDiscGeometry({
    worldSize: Number(transform.worldSize),
    centerLatDeg: Number(transform.center?.lat),
    cameraToCenterDistance: Number(transform.cameraToCenterDistance),
    width: Number(transform.width),
    height: Number(transform.height),
  });
}

/**
 * True when a canvas-space point is on the planet.
 *
 * Primary mechanism is MapLibre's own `transform.isPointOnMapSurface()`, which
 * is a ray/planet intersection and is therefore EXACT at the limb — measured
 * with a bisection sweep along the radius: at 0.999 of the limb the
 * unproject/project round-trip error is 0.00 px and the predicate is true; one
 * pixel past it the predicate flips false and `unproject` starts returning the
 * same clamped horizon lng/lat for every point out to 1.4 R. That clamping is
 * exactly the audit's bug, and this is the boundary it begins at.
 *
 * `globeDiscGeometry` is the fallback for a MapLibre that no longer exposes the
 * method. Both fail CLOSED (no hover) rather than open, because a pinned
 * horizon reading is indistinguishable from a real one.
 */
export function isCursorOnGlobe(map: maplibregl.Map | null | undefined, x: number, y: number): boolean {
  const transform = readTransform(map);
  if (!transform) {
    return false;
  }
  if (typeof transform.isPointOnMapSurface === "function") {
    try {
      return Boolean(transform.isPointOnMapSurface({ x, y }));
    } catch {
      // fall through to the geometric test
    }
  }
  return isPointInsideGlobeDisc(readGlobeDisc(map), x, y);
}

/**
 * True when a geographic point is on the hemisphere facing the camera.
 *
 * `isLocationOccluded` is MapLibre's own horizon-plane test — the same plane
 * the renderer clips tiles against — so a label this admits is a label the
 * basemap is also drawing. Fails CLOSED (hidden) when the method is missing.
 */
export function isLngLatVisibleOnGlobe(
  map: maplibregl.Map | null | undefined,
  lng: number,
  lat: number,
): boolean {
  const transform = readTransform(map);
  if (!transform || typeof transform.isLocationOccluded !== "function") {
    return false;
  }
  try {
    return !transform.isLocationOccluded({ lng, lat });
  } catch {
    return false;
  }
}

// ─── Polar camera framing ────────────────────────────────────────────────────

/**
 * How close to a pole the globe camera may be centred.
 *
 * Not 90: the globe's screen radius carries a 1/cos(lat) factor
 * (`globeRadiusPixels`), which is a division by zero AT the pole. 89.9° is
 * ~11 km short of it — below one screen pixel at any zoom that shows the whole
 * disc — and keeps that factor finite at 573.
 */
export const GLOBE_MAX_CENTER_LATITUDE_DEG = 89.9;

/**
 * Zoom, referred to the equator, that the globe camera may zoom out to.
 *
 * The viewer's flat presets set `minZoom` as high as 3, and under the globe a
 * min of 3 means the sphere ALWAYS overflows the canvas — at 1128x800, lat 20,
 * z3 the limb is 472 px off the canvas edge, so there is no whole-globe view at
 * all and no polar framing. The floor is only ever LOWERED (`Math.min` against
 * the map's own minZoom), and only while the globe is active.
 */
const GLOBE_MIN_ZOOM_AT_EQUATOR = 0;

/**
 * MapLibre's own latitude/zoom relation, `getZoomAdjustment(0, lat)`
 * (maplibre-gl-dev.js:57550, with `planetScaleAtLatitude(lat) = cos(lat)`):
 * the zoom offset that keeps the globe the same size on screen as the centre
 * moves off the equator. Reimplemented rather than imported because it is not
 * on the public surface; it is two lines and pinned by a unit test.
 */
export function globeZoomAdjustmentFromEquator(latDeg: number): number {
  const clamped = Math.max(
    -GLOBE_MAX_CENTER_LATITUDE_DEG,
    Math.min(GLOBE_MAX_CENTER_LATITUDE_DEG, latDeg),
  );
  return Math.log2(Math.cos((clamped * Math.PI) / 180));
}

/**
 * The globe's centre/zoom constraint, as a pure function so it is unit-testable
 * without a map.
 *
 * WHAT IT CHANGES vs MapLibre's default (`VerticalPerspectiveTransform.
 * defaultConstrain`, maplibre-gl-dev.js:58282). The default clamps centre
 * latitude to ±85.051 — the WEB MERCATOR limit, which has no meaning on a
 * sphere and is flagged `TODO` in MapLibre's own source. That clamp is why the
 * audit found "you cannot frame either pole": `jumpTo({center:[0,88]})` landed
 * at 85.051, and because an explicitly supplied zoom is NOT latitude-adjusted
 * the disc then blew up by 1/cos(85) ≈ 11x and left the screen.
 *
 * Everything else is kept verbatim, including the `minZoom + zoomAdjustment`
 * form of the zoom floor — that form is what holds the disc's screen size
 * constant as the centre moves, and dropping it is what would break the drag.
 */
export function constrainGlobeCamera(
  latDeg: number,
  zoom: number,
  minZoom: number,
  maxZoom: number,
): { lat: number; zoom: number } {
  const lat = Math.max(
    -GLOBE_MAX_CENTER_LATITUDE_DEG,
    Math.min(GLOBE_MAX_CENTER_LATITUDE_DEG, Number.isFinite(latDeg) ? latDeg : 0),
  );
  const floor = Math.min(minZoom, GLOBE_MIN_ZOOM_AT_EQUATOR) + globeZoomAdjustmentFromEquator(lat);
  const requested = Number.isFinite(zoom) ? Number(zoom) : floor;
  return { lat, zoom: Math.max(floor, Math.min(maxZoom, requested)) };
}

/**
 * Install (or remove) the polar-framing constraint on a live map.
 *
 * Idempotent, and `enabled === false` restores MapLibre's default constrain
 * exactly, so the flat map keeps today's ±85.051 clamp and today's zoom floor
 * with no code of ours in the path. Removing the override does not itself
 * re-constrain a camera that is now out of bounds, so the caller's
 * `setMinZoom`/`jumpTo` pass does that — see map-canvas.tsx.
 */
export function applyGlobeCameraConstrain(map: maplibregl.Map | null | undefined, enabled: boolean): void {
  const transform = readTransform(map);
  if (!map || !transform || typeof transform.setConstrainOverride !== "function") {
    return;
  }
  try {
    if (!enabled) {
      transform.setConstrainOverride(null);
      return;
    }
    // MapLibre wants a real LngLat back, not a plain object (it calls .wrap()
    // on the result downstream). The constructor is taken off a live instance
    // rather than imported: this module is pulled into plain Node by several
    // Playwright specs, where a VALUE import of "maplibre-gl" fails to resolve
    // its named exports. `type`-only imports stay erased, so they are fine.
    const LngLatCtor = map.getCenter().constructor as new (lng: number, lat: number) => unknown;
    transform.setConstrainOverride((lngLat, zoom) => {
      const constrained = constrainGlobeCamera(lngLat.lat, zoom, map.getMinZoom(), map.getMaxZoom());
      return { center: new LngLatCtor(lngLat.lng, constrained.lat), zoom: constrained.zoom };
    });
  } catch (error) {
    // A MapLibre that no longer accepts an override degrades to the default
    // clamp — no polar framing, but never a broken camera.
    console.warn("[globe] camera constrain unavailable", error);
  }
}
