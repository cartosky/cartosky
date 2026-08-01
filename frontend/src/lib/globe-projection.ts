/**
 * Globe (vertical-perspective) projection support for the custom WebGL grid
 * layer.
 *
 * Background and evidence: docs/GLOBE_SPIKE_2026-08-01.md and
 * docs/GLOBE_OVERLAY_AUDIT_2026-08-01.md. This module is the production
 * successor to the spike's `globe-spike.ts`.
 *
 * Three things live here, and nothing else:
 *
 *  1. The globe-mode switch (`isGlobeRenderingEnabled` / `setGlobeRenderingEnabled`).
 *     Phase G1 exposes it programmatically only — the boot flag `?globe=1` and
 *     `window.__cartoskyGlobe`. The user-facing control is Phase G2 and calls
 *     the same setter.
 *  2. Reading the projection state MapLibre hands a custom layer each frame
 *     (`readFrameProjection`) and the map's projection transition ramp
 *     (`readProjectionTransitionState`).
 *  3. The subdivided sphere mesh (`buildGlobeMesh`) and the globe vertex shader
 *     (`GLOBE_VERTEX_SOURCE`), which contains the one block of copied MapLibre
 *     projection math this feature owns.
 *
 * PROJECTION MODE IS A RENDERER INPUT, NOT A DATA SELECTION. Nothing in this
 * module touches domain/coverage/manifest resolution, and the grid layer's
 * manifest/texture coherence guard is orthogonal to it.
 */

// ─── Globe mode switch ───────────────────────────────────────────────────────

/**
 * Mesh density.
 *
 * 64x32 was the spike's measured floor for INTERIOR faceting (§2.3) — no
 * visible facets in the field itself at whole-globe zooms. It was not enough at
 * the LIMB, and the limb is a different problem: the mesh's silhouette is a
 * polygon INSCRIBED in the true circular limb, so every facet's chord sits
 * inside the limb by its sagitta and a sliver of MapLibre's basemap (which does
 * reach the true limb) shows between our data edge and space. An operator on a
 * retina display reported exactly that — a thin light line around the limb,
 * strongest at the equator and fading toward the poles, which is the mesh's own
 * pole convergence making the facets shorter there.
 *
 * The deficit is ∝ radius / rows², so it is invisible on a small disc at DPR 1
 * and plain on a large one at DPR 2. Measured by ablation at a 1042-device-px
 * disc, DPR 2 (tests/e2e/globe-limb-gap.spec.ts, equatorial band, device px):
 *
 *     16x8    mean 25.10   max 43.13
 *     64x32   mean  1.93   max  4.13
 *     192x96  mean  0.62   max  1.63
 *
 * ~0.4 mean / ~1.6 max of every row is the measurement's own floor (nearest-
 * device-pixel sampling of a hard, unantialiased clip edge), so 192x96 is
 * within ~0.2 device px of a perfect silhouette at that radius and the residual
 * stays sub-pixel out past a 2000-device-px disc.
 *
 * WHY DENSITY AND NOT AN ANALYTIC PER-FRAGMENT LIMB CLIP: a fragment-side clip
 * would be density-independent, but it reintroduces the screen-space/DPR
 * coupling this renderer deliberately avoids, and it would need its own copy of
 * MapLibre's camera math. Density needs neither, and it is free here — the mesh
 * is signature-gated, so the build is one-time per footprint, and the perf pass
 * measured mesh density as a non-driver of frame time (336x the index count sat
 * inside the noise; the frame is fill-rate bound). Re-confirmed at 192x96.
 *
 * 192 is also the ceiling (`MAX_GLOBE_MESH_AXIS`), so this is the densest mesh
 * the Uint16 index space is guaranteed to hold; there is no headroom left to
 * spend on a still-larger disc, and the next step would have to be the analytic
 * clip.
 */
const DEFAULT_GLOBE_MESH_COLS = 192;
const DEFAULT_GLOBE_MESH_ROWS = 96;
/**
 * Hard bound on the dev `?globeMesh=` override. The index buffer is
 * Uint16Array, so the vertex count (cols+1)*(rows+1) + 2 poles must stay under
 * 65536; 192 on each axis leaves a wide margin.
 */
const MAX_GLOBE_MESH_AXIS = 192;

type GlobeBootFlags = { enabled: boolean; meshCols: number; meshRows: number };

function readBootFlags(): GlobeBootFlags {
  const off = {
    enabled: false,
    meshCols: DEFAULT_GLOBE_MESH_COLS,
    meshRows: DEFAULT_GLOBE_MESH_ROWS,
  };
  if (typeof window === "undefined") {
    return off;
  }
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(window.location.search);
  } catch {
    return off;
  }
  const raw = params.get("globeMesh") ?? "";
  const match = /^(\d+)x(\d+)$/.exec(raw.trim());
  const clamp = (value: number, fallback: number) =>
    Number.isFinite(value) && value >= 2 ? Math.min(MAX_GLOBE_MESH_AXIS, Math.floor(value)) : fallback;
  return {
    enabled: params.get("globe") === "1",
    meshCols: match ? clamp(Number(match[1]), DEFAULT_GLOBE_MESH_COLS) : DEFAULT_GLOBE_MESH_COLS,
    meshRows: match ? clamp(Number(match[2]), DEFAULT_GLOBE_MESH_ROWS) : DEFAULT_GLOBE_MESH_ROWS,
  };
}

const BOOT = readBootFlags();

export const GLOBE_MESH_COLS = BOOT.meshCols;
export const GLOBE_MESH_ROWS = BOOT.meshRows;

let globeRenderingEnabled = BOOT.enabled;
const globeRenderingListeners = new Set<(enabled: boolean) => void>();

/** True when the viewer should ask MapLibre for the globe projection. */
export function isGlobeRenderingEnabled(): boolean {
  return globeRenderingEnabled;
}

/**
 * Runtime setter. Phase G1 has no UI; the G2 button calls this. Idempotent, and
 * listeners are notified only on a real change so a no-op set cannot cause a
 * projection churn.
 */
export function setGlobeRenderingEnabled(enabled: boolean): void {
  const next = Boolean(enabled);
  if (next === globeRenderingEnabled) {
    return;
  }
  globeRenderingEnabled = next;
  for (const listener of globeRenderingListeners) {
    try {
      listener(next);
    } catch {
      // A listener throwing must never strand the switch half-applied.
    }
  }
}

/**
 * Whether the running MapLibre can actually switch projections.
 *
 * Starts FALSE and is set by MapCanvas once it holds a real map and has checked
 * for the methods the feature needs. The G2 toggle is rendered only when this is
 * true, so a MapLibre that drops globe support degrades to no button rather than
 * to a button that does nothing.
 */
let globeProjectionSupported = false;
const globeSupportListeners = new Set<(supported: boolean) => void>();

export function isGlobeProjectionSupported(): boolean {
  return globeProjectionSupported;
}

export function setGlobeProjectionSupported(supported: boolean): void {
  const next = Boolean(supported);
  if (next === globeProjectionSupported) {
    return;
  }
  globeProjectionSupported = next;
  for (const listener of globeSupportListeners) {
    try {
      listener(next);
    } catch {
      // A listener throwing must not strand the capability flag.
    }
  }
}

/** Subscribe to support-detection changes. Returns an unsubscribe function. */
export function subscribeGlobeProjectionSupport(listener: (supported: boolean) => void): () => void {
  globeSupportListeners.add(listener);
  return () => {
    globeSupportListeners.delete(listener);
  };
}

/** Subscribe to globe-mode changes. Returns an unsubscribe function. */
export function subscribeGlobeRendering(listener: (enabled: boolean) => void): () => void {
  globeRenderingListeners.add(listener);
  return () => {
    globeRenderingListeners.delete(listener);
  };
}

if (typeof window !== "undefined") {
  (window as unknown as Record<string, unknown>).__cartoskyGlobe = {
    isEnabled: isGlobeRenderingEnabled,
    setEnabled: setGlobeRenderingEnabled,
    subscribe: subscribeGlobeRendering,
    meshCols: GLOBE_MESH_COLS,
    meshRows: GLOBE_MESH_ROWS,
  };
}

// ─── Space background ────────────────────────────────────────────────────────

/**
 * What the area OUTSIDE the globe disc is painted.
 *
 * Operator report (Globe v1.1): the planet floated on the flat map's own
 * background grey, which reads as "the map failed to load" rather than as
 * space.
 *
 * COLOUR. Two stops darker than `#04101e`, the deep-navy surface every piece of
 * viewer glass chrome is built on (top bar, rail popovers, legend chip), so it
 * belongs to the same palette while still sitting clearly BEHIND that chrome —
 * chrome at the same value would dissolve into the backdrop over the globe.
 *
 * THE SAME IN LIGHT AND DARK CHROME, on purpose, twice over: space is space,
 * it has no light mode; and share/GIF output derives its backdrop from this
 * colour (`resolveShareBackdropColor`), so a mode-dependent value would make
 * exports of the same view differ by the sharer's UI preference.
 *
 * SOLID, deliberately. Stars, a limb glow and a vignette are the obvious next
 * polish and are held back from v1 on purpose: they are texture over a colour
 * decision, and the colour has to be settled (and pinned by goldens) first.
 */
export const GLOBE_SPACE_BACKGROUND_COLOR = "#02060d";

// ─── Disc geometry ───────────────────────────────────────────────────────────

/**
 * The camera parameters a globe disc's screen geometry depends on.
 *
 * Read off a MapLibre transform, but declared structurally so the geometry is
 * a pure function that a unit test can drive without a WebGL context.
 */
export type GlobeDiscCamera = {
  /** `transform.worldSize` — tileSize * 2^zoom. */
  worldSize: number;
  /** Map centre latitude in degrees. */
  centerLatDeg: number;
  /** `transform.cameraToCenterDistance`, in pixels. */
  cameraToCenterDistance: number;
  width: number;
  height: number;
};

/** Where the sphere's silhouette lands on the canvas. */
export type GlobeDisc = {
  centerX: number;
  centerY: number;
  radiusPx: number;
};

/**
 * MapLibre's own globe radius: the sphere's circumference equals the mercator
 * world width, scaled up by 1/cos(lat) so a feature at the map centre keeps the
 * pixel size it would have on the flat map (maplibre-gl-dev.js:57475,
 * `getGlobeRadiusPixels`).
 */
export function globeRadiusPixels(worldSize: number, centerLatDeg: number): number {
  return worldSize / (2 * Math.PI) / Math.cos((centerLatDeg * Math.PI) / 180);
}

/**
 * Screen geometry of the globe's silhouette — the limb, i.e. the exact boundary
 * between "this pixel is on the planet" and "this pixel is empty background".
 *
 * DERIVATION, and why it is not simply `globeRadiusPixels`. MapLibre places the
 * sphere of radius `R = globeRadiusPixels` so that its NEAR SURFACE sits at the
 * camera's focal distance (`_calcMatrices` translates by `-globeRadiusPixels`
 * before scaling, maplibre-gl-dev.js:58474). The camera is therefore `D = c2c +
 * R` from the sphere CENTRE, and the silhouette of a sphere seen from `D` has
 * angular radius `asin(R/D)`, which a pinhole of focal length `c2c` px images at
 *
 *     radiusPx = c2c * tan(asin(R/D)) = c2c * R / sqrt(D^2 - R^2)
 *
 * `R` alone is only the small-angle limit of that, and it is wrong by a large
 * margin at the zooms the globe is interesting at (measured 1128x800, lat 20,
 * z3: R = 693.7 px, true limb = 472.4 px — R overstates the disc by 47%).
 *
 * Verified against `transform.isPointOnMapSurface()` — MapLibre's own
 * ray/planet intersection — by bisecting for the limb along the +x radius at
 * four cameras (lat 20/z1, 45/z2, -60/z0.5, 0/z3): agreement to 0.01 px at
 * every one.
 *
 * Assumes pitch 0 and no centre offset, which is the viewer's only camera
 * (`dragRotate`/`touchPitch` are disabled and pitch is never set). Callers that
 * can reach a MapLibre transform should prefer `isPointOnMapSurface`, which is
 * exact under any camera; this is the fallback and the testable statement of
 * what the disc IS.
 */
export function globeDiscGeometry(camera: GlobeDiscCamera): GlobeDisc | null {
  const { worldSize, centerLatDeg, cameraToCenterDistance, width, height } = camera;
  if (
    !Number.isFinite(worldSize) || worldSize <= 0 ||
    !Number.isFinite(cameraToCenterDistance) || cameraToCenterDistance <= 0 ||
    !Number.isFinite(width) || width <= 0 || !Number.isFinite(height) || height <= 0 ||
    !Number.isFinite(centerLatDeg)
  ) {
    return null;
  }
  const radius = globeRadiusPixels(worldSize, centerLatDeg);
  if (!Number.isFinite(radius) || radius <= 0) {
    return null;
  }
  const distance = cameraToCenterDistance + radius;
  const denominator = Math.sqrt(distance * distance - radius * radius);
  if (!Number.isFinite(denominator) || denominator <= 0) {
    return null;
  }
  return {
    centerX: width / 2,
    centerY: height / 2,
    radiusPx: (cameraToCenterDistance * radius) / denominator,
  };
}

/** True when a canvas-space point falls on the planet rather than on the void. */
export function isPointInsideGlobeDisc(disc: GlobeDisc | null, x: number, y: number): boolean {
  if (!disc || !Number.isFinite(x) || !Number.isFinite(y)) {
    return false;
  }
  return Math.hypot(x - disc.centerX, y - disc.centerY) <= disc.radiusPx;
}

// ─── Per-frame projection state ──────────────────────────────────────────────

/**
 * The space a 4x4 matrix handed to the custom layer expects its vertices in.
 *
 * This is the discriminator the silent-garbage tripwire keys on: multiplying a
 * mercator-unit quad by a unit-sphere matrix draws nonsense with no GL error
 * and no type error (GLOBE_SPIKE §1.1).
 */
export type ProjectionMatrixSpace = "mercator-unit" | "unit-sphere";

/**
 * Which geometry the grid layer submits for a frame.
 *
 * `mercator` = the 4-vertex quad + the world-copy loop (every frame the viewer
 * has ever drawn). `globe` = the subdivided sphere mesh. This is a RENDERER
 * input derived from the map's projection state, never from data selection.
 */
export type GridGeometryMode = "mercator" | "globe";

/**
 * The ONLY matrix space a given geometry may be drawn with.
 */
export function expectedMatrixSpaceFor(mode: GridGeometryMode): ProjectionMatrixSpace {
  return mode === "globe" ? "unit-sphere" : "mercator-unit";
}

/**
 * True when a geometry/matrix pairing is safe to draw. `null` (a matrix of
 * unknown provenance) is never safe.
 */
export function isProjectionPairingCoherent(
  mode: GridGeometryMode,
  matrixSpace: ProjectionMatrixSpace | null,
): boolean {
  return matrixSpace !== null && matrixSpace === expectedMatrixSpaceFor(mode);
}

/** Globe projection data for one frame, extracted from `defaultProjectionData`. */
export type GlobeFrameProjection = {
  /** Unit sphere -> clip (MapLibre's vertical-perspective main matrix). */
  mainMatrix: number[];
  /** Mercator-units -> clip. Always a mercator matrix, even under globe. */
  fallbackMatrix: number[];
  /** Horizon plane in unit-sphere space, for back-face clipping. */
  clippingPlane: [number, number, number, number];
};

export type FrameProjection = {
  /**
   * Space of `defaultProjectionData.mainMatrix`, taken from MapLibre's own
   * `shaderData.variantName` — the sanctioned shader-cache key
   * (maplibre-gl-dev.js:64885, `variantName: projection.shaderVariantName`).
   * Deliberately NOT derived from `projectionTransition`, so the tripwire has a
   * second, independent opinion about what the matrix is.
   */
  mainMatrixSpace: ProjectionMatrixSpace;
  /** Non-null exactly when `mainMatrixSpace === "unit-sphere"`. */
  globe: GlobeFrameProjection | null;
};

const MERCATOR_FRAME: FrameProjection = { mainMatrixSpace: "mercator-unit", globe: null };

/**
 * Read the projection state out of the custom-layer render args.
 *
 * `args` may also be the legacy bare-matrix array, in which case the frame is
 * plain mercator by construction.
 */
export function readFrameProjection(args: unknown): FrameProjection {
  if (!args || Array.isArray(args) || typeof args !== "object") {
    return MERCATOR_FRAME;
  }
  const typed = args as {
    shaderData?: { variantName?: unknown };
    defaultProjectionData?: {
      mainMatrix?: ArrayLike<number>;
      fallbackMatrix?: ArrayLike<number>;
      clippingPlane?: ArrayLike<number>;
      projectionTransition?: number;
    };
  };
  const data = typed.defaultProjectionData;
  if (!data?.mainMatrix) {
    return MERCATOR_FRAME;
  }
  // Two independent signals that this frame's mainMatrix is a sphere matrix:
  // the sanctioned shader variant key, and `projectionTransition > 0` (which
  // GlobeTransform sets from `isGlobeRendering`). They agree in MapLibre 5.24;
  // requiring BOTH means a future divergence degrades to the mercator path
  // rather than to a garbage draw.
  const variantIsGlobe = String(typed.shaderData?.variantName ?? "") === "globe";
  const transitionIsGlobe = Number(data.projectionTransition ?? 0) > 0;
  if (!variantIsGlobe || !transitionIsGlobe) {
    return MERCATOR_FRAME;
  }
  const plane = data.clippingPlane ?? [0, 0, 0, 0];
  return {
    mainMatrixSpace: "unit-sphere",
    globe: {
      mainMatrix: Array.from(data.mainMatrix),
      fallbackMatrix: Array.from(data.fallbackMatrix ?? data.mainMatrix),
      clippingPlane: [
        Number(plane[0]) || 0,
        Number(plane[1]) || 0,
        Number(plane[2]) || 0,
        Number(plane[3]) || 0,
      ],
    },
  };
}

/**
 * The space of `candidate`, derived from WHICH MATRIX OBJECT IT IS.
 *
 * This is the load-bearing half of the silent-garbage tripwire, and it is
 * deliberately not a label. An earlier revision tagged the space with a literal
 * assigned by the same branch that chose the matrix, which made the guard a
 * tautology: every branch was self-consistent by construction, so handing the
 * globe branch `fallbackMatrix` while it still claimed "unit-sphere" drew
 * 647,240 of 649,600 pixels wrong with the counter reading zero.
 *
 * Provenance instead of assertion: `readFrameProjection` copies MapLibre's two
 * matrices ONCE per frame into two distinct arrays, so reference identity
 * answers "is this the matrix MapLibre labelled unit-sphere->clip, or the one it
 * labelled mercator-units->clip". A matrix that is neither returns null, which
 * `isProjectionPairingCoherent` treats as unsafe — so a future path that
 * synthesises its own matrix has to come here and say what it is rather than
 * silently drawing garbage.
 *
 * `frameMainMatrix` is the array the custom-layer render callback built from
 * `defaultProjectionData.mainMatrix`; on a non-globe frame that is the only
 * matrix in play, and it is a mercator matrix by definition of the frame.
 */
export function deriveMatrixSpace(
  candidate: number[],
  frameMainMatrix: number[],
  frame: FrameProjection,
): ProjectionMatrixSpace | null {
  if (frame.globe) {
    if (candidate === frame.globe.mainMatrix) {
      return "unit-sphere";
    }
    if (candidate === frame.globe.fallbackMatrix) {
      return "mercator-unit";
    }
    return null;
  }
  return candidate === frameMainMatrix ? "mercator-unit" : null;
}

/**
 * MapLibre's globe->mercator transition ramp, 1 = full globe, 0 = full
 * mercator.
 *
 * WHY THIS IS READ FROM THE MAP AND NOT FROM THE RENDER ARGS: MapLibre 5.24
 * hands custom layers a hard-coded `projectionTransition: 1` for the ENTIRE
 * transition. Tiles go through `GlobeTransform.getProjectionData()`, which sets
 * the true `_globeness`; custom layers go through
 * `GlobeTransform.getProjectionDataForCustomLayer()` (maplibre-gl-dev.js:59306)
 * which delegates to `VerticalPerspectiveTransform.getProjectionData()` and
 * returns the literal 1. See GLOBE_SPIKE §5.
 *
 * `Projection.transitionState` is a typed getter on the public `Projection`
 * interface (maplibre-gl.d.ts:7071, marked `@internal` but not private) reached
 * through `Style.projection` (maplibre-gl.d.ts:7246). This is deliberately NOT
 * `GlobeTransform._globeness`, which is a genuinely private field.
 *
 * Fails OPEN to 1 (full globe): MapLibre only hands the layer a sphere matrix
 * while it is globe-rendering, so treating an unreadable ramp as "fully
 * spherical" keeps geometry and matrix coherent. The worst case is the spike's
 * measured <= 0.16 px divergence from the basemap mid-transition, never a
 * garbage draw.
 */
export function readProjectionTransitionState(map: unknown): number {
  const projection = (map as { style?: { projection?: { transitionState?: unknown } } } | null)
    ?.style?.projection;
  const value = Number(projection?.transitionState);
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 1;
}

// ─── Sphere mesh ─────────────────────────────────────────────────────────────

export type GlobeMesh = {
  /** Mercator-unit XY per vertex, latitude-clamped. Feeds `v_mercUnit`. */
  positions: Float32Array;
  /** TRUE longitude/latitude in radians per vertex, NOT mercator-clamped. */
  lonLat: Float32Array;
  /** Same texcoord convention as buildQuadTexCoords(). */
  texCoords: Float32Array;
  indices: Uint16Array;
  indexCount: number;
  /** Number of triangles emitted by the polar fans (0, 1 or 2 poles). */
  poleFanTriangles: number;
  signature: string;
};

const MERCATOR_MAX_LATITUDE_DEG = 85.05112877980659;
const MERCATOR_HALF_WORLD = 20037508.342789244;
const MERCATOR_EARTH_RADIUS = MERCATOR_HALF_WORLD / Math.PI;
/** A row within this of a pole is treated as being AT the pole. */
const POLE_EPSILON_DEG = 1e-6;

function mercatorUnitXFromLonDeg(lonDeg: number): number {
  return (lonDeg + 180) / 360;
}

function mercatorUnitYFromLatDeg(latDeg: number): number {
  const clamped = Math.max(-MERCATOR_MAX_LATITUDE_DEG, Math.min(MERCATOR_MAX_LATITUDE_DEG, latDeg));
  const phi = (clamped * Math.PI) / 180;
  const northing = MERCATOR_EARTH_RADIUS * Math.log(Math.tan(Math.PI / 4 + phi / 2));
  return (MERCATOR_HALF_WORLD - northing) / (2 * MERCATOR_HALF_WORLD);
}

function mercatorUnitYFromMeters(y: number): number {
  return (MERCATOR_HALF_WORLD - y) / (2 * MERCATOR_HALF_WORLD);
}

function lonDegFromMercatorUnitX(x: number): number {
  return x * 360 - 180;
}

function latDegFromMercatorUnitY(y: number): number {
  const phi = 2 * Math.atan(Math.exp(Math.PI - y * 2 * Math.PI)) - Math.PI / 2;
  return (phi * 180) / Math.PI;
}

/**
 * The mesh's cache key, as a function of the inputs ALONE.
 *
 * Split out of `buildGlobeMesh` so a caller can ask "would this build produce
 * the mesh I already uploaded?" WITHOUT building it. Checking `mesh.signature`
 * after the fact still skipped the GL upload, but it ran the whole generator —
 * measured at 0.041 ms and ~50 KB of throwaway typed arrays on EVERY globe
 * frame, which was the single largest item in this layer's per-frame JS.
 *
 * `geographic` and `fullWorld` are deliberately absent: both are pure functions
 * of `projection` and `bbox`, so including them could not discriminate anything
 * the two already do.
 */
export function globeMeshSignature(
  bbox: [number, number, number, number],
  projection: string | null | undefined,
  cols: number,
  rows: number,
): string {
  return `${projection ?? ""}|${bbox.join(",")}|${cols}x${rows}`;
}

/**
 * Subdivided mesh for one grid artifact's footprint, in sphere space.
 *
 * TWO coordinate systems per vertex, on purpose:
 *
 *  - `positions` (a_pos) is exactly what the existing 4-vertex quad carries:
 *    mercator-unit XY, latitude-clamped to ±85.051°. It feeds the shared
 *    fragment source's `v_mercUnit` varying so the fragment shader stays a
 *    single source for both programs.
 *  - `lonLat` (a_lonLat) is the TRUE spherical position in radians, NOT
 *    mercator-clamped. This is the only reason a row at lat ±90 can exist at
 *    all: MapLibre's own `projectToSphere()` derives latitude from mercator Y,
 *    where ±90° is at infinity, so a layer routed through the sanctioned
 *    `projectTile()` prelude could never draw a polar cap. A global GFS
 *    artifact is 1440x721 with rows at exactly ±90°
 *    (docs/GLOBAL_DOMAIN_4326_CONTRACT.md), so that is not optional for us.
 *
 * ROW SPACING is per texture family, matching the artifact's own row layout so
 * the row->latitude mapping is exact at every vertex:
 *  - EPSG:4326  rows linear in LATITUDE.
 *  - EPSG:3857  rows linear in MERCATOR Y, i.e. the forward Gudermannian is
 *    applied at vertices instead of per fragment.
 *
 * POLES. Where a geographic footprint reaches +-90 degrees, that row is emitted
 * as a RING of `cols + 1` vertices that share the same sphere position but keep
 * their own longitude and their own `s` texcoord, and the band below it emits
 * ONE triangle per column instead of two.
 *
 * Why not a single apex vertex: a vertex carries exactly one texcoord, so
 * collapsing the ring collapses TEXTURE LONGITUDE across the whole polar band.
 * Every fragment in that band then interpolates its `s` between the apex's one
 * longitude and the row below, which bends the meridians into visible S-shaped
 * spokes at production mesh density (measured: up to 83.5 degrees of longitude
 * error at barycentric 0.9, across 5.51 degrees of latitude / 22 texture rows).
 * The ring is not a workaround for that -- it is the correct topology: at a pole
 * all longitudes ARE the same point, so coincident positions with distinct
 * texcoords is exactly what the geometry means.
 *
 * The two triangles of a polar quad are (topLeft, bottomLeft, topRight) and
 * (topRight, bottomLeft, bottomRight); the one whose two ring corners are
 * coincident has zero area and is discarded by the rasterizer, so only the
 * other is emitted. That makes the "degenerate triangle" concern moot by
 * construction rather than by trusting the driver.
 *
 * The polar latitude is clamped to exactly +-90 even when the cell-edge bbox
 * overhangs it (+-90.125 in the global contract), which also makes the
 * fragment's recovered texture row land exactly on the polar row centre instead
 * of half a row outside it.
 */
export function buildGlobeMesh(
  bbox: [number, number, number, number],
  projection: string | null | undefined,
  geographic: boolean,
  fullWorld: boolean,
  cols: number,
  rows: number,
): GlobeMesh {
  const signature = globeMeshSignature(bbox, projection, cols, rows);
  const vertsX = cols + 1;

  // Longitude span of the drawn footprint. A full-world geographic grid is
  // drawn world-exact (-180..180) with the half-cell texcoord inset, matching
  // buildQuadVertices/buildQuadTexCoords.
  const westDeg = geographic
    ? (fullWorld ? -180 : bbox[0])
    : lonDegFromMercatorUnitX((bbox[0] + MERCATOR_HALF_WORLD) / (2 * MERCATOR_HALF_WORLD));
  const eastDeg = geographic
    ? (fullWorld ? 180 : bbox[2])
    : lonDegFromMercatorUnitX((bbox[2] + MERCATOR_HALF_WORLD) / (2 * MERCATOR_HALF_WORLD));
  const lonSpanBboxDeg = geographic ? bbox[2] - bbox[0] : 0;
  const centreLonDeg = (westDeg + eastDeg) / 2;

  const merTop = geographic ? mercatorUnitYFromLatDeg(bbox[3]) : mercatorUnitYFromMeters(bbox[3]);
  const merBottom = geographic ? mercatorUnitYFromLatDeg(bbox[1]) : mercatorUnitYFromMeters(bbox[1]);

  // Per-row latitude / mercator-unit-y / v texcoord.
  const rowLatDeg: number[] = [];
  const rowMerY: number[] = [];
  const rowV: number[] = [];
  for (let row = 0; row <= rows; row += 1) {
    const ty = row / rows;
    if (geographic) {
      const latDeg = bbox[3] + (bbox[1] - bbox[3]) * ty;
      const clamped = Math.max(-90, Math.min(90, latDeg));
      rowLatDeg.push(clamped);
      rowMerY.push(mercatorUnitYFromLatDeg(clamped));
    } else {
      const merY = merTop + (merBottom - merTop) * ty;
      rowLatDeg.push(latDegFromMercatorUnitY(merY));
      rowMerY.push(merY);
    }
    rowV.push(ty);
  }

  // A pole is a row AT +-90, not a special vertex. Rows are emitted uniformly;
  // only the INDEXING of the adjacent band changes.
  const northPoleRing = geographic && rowLatDeg[0] >= 90 - POLE_EPSILON_DEG;
  const southPoleRing = geographic && rowLatDeg[rows] <= -90 + POLE_EPSILON_DEG;

  const vertexCount = (rows + 1) * vertsX;
  const positions = new Float32Array(vertexCount * 2);
  const lonLat = new Float32Array(vertexCount * 2);
  const texCoords = new Float32Array(vertexCount * 2);

  const writeVertex = (index: number, lonDeg: number, latDeg: number, merY: number, s: number, v: number) => {
    const at = index * 2;
    positions[at] = mercatorUnitXFromLonDeg(lonDeg);
    positions[at + 1] = merY;
    lonLat[at] = (lonDeg * Math.PI) / 180;
    lonLat[at + 1] = (latDeg * Math.PI) / 180;
    texCoords[at] = s;
    texCoords[at + 1] = v;
  };

  const sFor = (lonDeg: number, tx: number) =>
    geographic && lonSpanBboxDeg > 0 ? (lonDeg - bbox[0]) / lonSpanBboxDeg : tx;

  for (let row = 0; row <= rows; row += 1) {
    const rowBase = row * vertsX;
    for (let col = 0; col < vertsX; col += 1) {
      const tx = col / cols;
      const lonDeg = westDeg + (eastDeg - westDeg) * tx;
      writeVertex(rowBase + col, lonDeg, rowLatDeg[row], rowMerY[row], sFor(lonDeg, tx), rowV[row]);
    }
  }

  // Index generation. Every band is the same quad pair
  //   (topLeft, bottomLeft, topRight) and (topRight, bottomLeft, bottomRight)
  // except a band bounded by a pole ring, where the triangle with two coincident
  // ring corners has zero area and is simply not emitted.
  const poleFanTriangles = (northPoleRing ? cols : 0) + (southPoleRing ? cols : 0);
  const fullBands = rows - (northPoleRing ? 1 : 0) - (southPoleRing ? 1 : 0);
  const indices = new Uint16Array(fullBands * cols * 6 + poleFanTriangles * 3);
  let cursor = 0;
  for (let band = 0; band < rows; band += 1) {
    const topIsPole = northPoleRing && band === 0;
    const bottomIsPole = southPoleRing && band === rows - 1;
    for (let col = 0; col < cols; col += 1) {
      const topLeft = band * vertsX + col;
      const topRight = topLeft + 1;
      const bottomLeft = topLeft + vertsX;
      const bottomRight = bottomLeft + 1;
      if (!topIsPole) {
        indices[cursor] = topLeft;
        indices[cursor + 1] = bottomLeft;
        indices[cursor + 2] = topRight;
        cursor += 3;
      }
      if (!bottomIsPole) {
        indices[cursor] = topRight;
        indices[cursor + 1] = bottomLeft;
        indices[cursor + 2] = bottomRight;
        cursor += 3;
      }
    }
  }

  return {
    positions,
    lonLat,
    texCoords,
    indices,
    indexCount: indices.length,
    poleFanTriangles,
    signature,
  };
}

// ─── Globe vertex shader ─────────────────────────────────────────────────────

/**
 * Vertex shader for the globe geometry path.
 *
 * It deliberately does NOT use MapLibre's `shaderData.vertexShaderPrelude` /
 * `projectTile()`. The prelude's `projectToSphere()` takes MERCATOR
 * coordinates and derives latitude with `2*atan(exp(PI - y*2PI)) - PI/2`;
 * mercator Y for ±90° is infinite, so a layer routed through the prelude cannot
 * express a vertex north of ~85.051° and the polar caps of a global 4326
 * artifact would be holes. Computing the sphere position from true lon/lat and
 * multiplying by `mainMatrix` (documented as "projects a unit sphere planet to
 * screen") avoids the singularity entirely. See GLOBE_SPIKE §1.2.
 *
 * The price is the block below.
 */
export const GLOBE_VERTEX_SOURCE = `
  attribute vec2 a_pos;
  attribute vec2 a_texCoord;
  attribute vec2 a_lonLat;
  uniform mat4 u_matrix;
  uniform vec4 u_globeClippingPlane;
  varying vec2 v_texCoord;
  varying vec2 v_mercUnit;
  varying float v_latRad;
  void main() {
    v_texCoord = a_texCoord;
    v_mercUnit = a_pos;
    v_latRad = a_lonLat.y;

    // ======================================================================
    // BEGIN COPIED MAPLIBRE PROJECTION MATH -- PINNED MAINTENANCE SURFACE
    //
    // Source: maplibre-gl@5.24.0, dist/maplibre-gl-dev.js line 54760, the
    // projectionGlobeVert shader string. Re-verify this block on every
    // MapLibre MAJOR upgrade (and on any minor that touches
    // projectionGlobeVert or getProjectionDataForCustomLayer).
    //
    // Upgrade check, in order:
    //   1. grep the new dist/maplibre-gl-dev.js for var projectionGlobeVert.
    //   2. Confirm projectToSphere still builds the sphere vector as
    //      vec3(sin(x)*len, sin(y), cos(x)*len) with x = mercatorX*2PI + PI
    //      and len = cos(latitude). (x reduces to the longitude in radians
    //      modulo 2PI, which is why line [1] can take lon directly.)
    //   3. Confirm globeComputeClippingZ(spherePos) is still
    //      1.0 - (dot(spherePos, u_projection_clipping_plane.xyz)
    //             + u_projection_clipping_plane.w)
    //      and that interpolateProjection still applies it as
    //      globePosition.z = globeComputeClippingZ(pos) * globePosition.w.
    //   4. Confirm getProjectionDataForCustomLayer (:59306) still returns
    //      mainMatrix = unit-sphere->clip and clippingPlane in sphere space.
    // If any of those moved, this block moves with them -- nothing else in the
    // codebase depends on MapLibre internals.
    //
    // [1] projectToSphere(): sphere vector from lon/lat in radians.
    float len = cos(a_lonLat.y);
    vec3 sphere = vec3(sin(a_lonLat.x) * len, sin(a_lonLat.y), cos(a_lonLat.x) * len);
    // [2] interpolateProjection(): globePosition = u_projection_matrix * pos.
    vec4 globePosition = u_matrix * vec4(sphere, 1.0);
    // [3] globeComputeClippingZ(), applied as interpolateProjection does -- the
    //     horizon plane clips the far side of the sphere through depth.
    globePosition.z =
      (1.0 - (dot(sphere, u_globeClippingPlane.xyz) + u_globeClippingPlane.w)) * globePosition.w;
    //
    // NOT copied, on purpose: interpolateProjection's globe<->mercator blend
    // (the u_projection_transition mix) and its +/-32767 pole sentinel. The layer
    // hard-flips to the mercator quad path for the whole transition
    // (GLOBE_SPIKE sec 5 option b), so the blend branch would be dead code, and
    // the pole sentinel is a tile-path device this mesh replaces with real
    // +/-90 deg vertices.
    //
    // END COPIED MAPLIBRE PROJECTION MATH
    // ======================================================================

    gl_Position = globePosition;
  }
`;
