/**
 * ══════════════════════════════════════════════════════════════════════════
 * GLOBE SPIKE — dev-only. NOT production code. See docs/GLOBE_SPIKE_2026-08-01.md
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Everything in this file is gated behind `?globe=1`, read ONCE at module
 * evaluation (boot). With the flag absent every export is inert and the
 * callers in grid-webgl.ts / map-canvas.tsx take their existing code paths
 * byte-for-byte.
 *
 * Delete this file + the `[GLOBE SPIKE]` blocks in grid-webgl.ts and
 * map-canvas.tsx to remove the spike entirely.
 */

function readBootFlags(): { enabled: boolean; meshCols: number; meshRows: number } {
  const off = { enabled: false, meshCols: 0, meshRows: 0 };
  if (typeof window === "undefined") {
    return off;
  }
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(window.location.search);
  } catch {
    return off;
  }
  if (params.get("globe") !== "1") {
    return off;
  }
  // ?globeMesh=<cols>x<rows> (default 128x64) — the perf/quality knob the
  // spike measures.
  const raw = params.get("globeMesh") ?? "";
  const match = /^(\d+)x(\d+)$/.exec(raw.trim());
  const cols = match ? Number(match[1]) : 128;
  const rows = match ? Number(match[2]) : 64;
  return {
    enabled: true,
    meshCols: Math.max(1, Math.min(512, cols)),
    meshRows: Math.max(1, Math.min(512, rows)),
  };
}

const BOOT = readBootFlags();

/** True only when the page was loaded with `?globe=1`. */
export const GLOBE_SPIKE_ENABLED = BOOT.enabled;
export const GLOBE_SPIKE_MESH_COLS = BOOT.meshCols;
export const GLOBE_SPIKE_MESH_ROWS = BOOT.meshRows;

/**
 * Projection data a custom layer needs to render on the globe, extracted from
 * MapLibre 5.24's `CustomRenderMethodInput.defaultProjectionData`.
 *
 * `projectionTransition` (MapLibre's `_globeness`, uniform
 * `u_projection_transition`) is the discriminator: it is > 0 exactly when
 * `mainMatrix` is the vertical-perspective UNIT SPHERE matrix rather than a
 * mercator matrix. At 0 the layer must take its normal mercator path.
 */
export type GlobeFrameProjection = {
  /** Unit sphere -> clip. */
  mainMatrix: number[];
  /** Mercator-units -> clip; MapLibre's own globe->mercator fallback. */
  fallbackMatrix: number[];
  /** Horizon plane in unit-sphere space, for back-face clipping. */
  clippingPlane: [number, number, number, number];
  /** 1 = full globe, 0 = full mercator, in-between = transition animation. */
  transition: number;
};

/**
 * Read the globe projection state out of the custom-layer render args, or
 * null when this frame is plain mercator (or the arg shape is the legacy
 * bare-matrix array).
 */
export function readGlobeFrameProjection(args: unknown): GlobeFrameProjection | null {
  if (!GLOBE_SPIKE_ENABLED || !args || Array.isArray(args) || typeof args !== "object") {
    return null;
  }
  const data = (args as { defaultProjectionData?: unknown }).defaultProjectionData as
    | {
      mainMatrix?: ArrayLike<number>;
      fallbackMatrix?: ArrayLike<number>;
      clippingPlane?: ArrayLike<number>;
      projectionTransition?: number;
    }
    | undefined;
  if (!data) {
    return null;
  }
  const transition = Number(data.projectionTransition ?? 0);
  if (!(transition > 0) || !data.mainMatrix) {
    return null;
  }
  const plane = data.clippingPlane ?? [0, 0, 0, 0];
  return {
    mainMatrix: Array.from(data.mainMatrix),
    fallbackMatrix: Array.from(data.fallbackMatrix ?? data.mainMatrix),
    clippingPlane: [
      Number(plane[0]) || 0,
      Number(plane[1]) || 0,
      Number(plane[2]) || 0,
      Number(plane[3]) || 0,
    ],
    transition,
  };
}

export type GlobeMesh = {
  /** Mercator-unit XY per vertex, latitude-clamped — the mercator/fallback path. */
  positions: Float32Array;
  /** TRUE longitude/latitude in radians per vertex — the sphere path. */
  lonLat: Float32Array;
  /** Same texcoord convention as buildQuadTexCoords(). */
  texCoords: Float32Array;
  indices: Uint16Array;
  indexCount: number;
  signature: string;
};

const MERCATOR_MAX_LATITUDE_DEG = 85.051129;

function mercatorXFromLonDeg(lonDeg: number): number {
  return (lonDeg + 180) / 360;
}

function mercatorYFromLatDeg(latDeg: number): number {
  const clamped = Math.max(-MERCATOR_MAX_LATITUDE_DEG, Math.min(MERCATOR_MAX_LATITUDE_DEG, latDeg));
  const phi = (clamped * Math.PI) / 180;
  return 0.5 - Math.log(Math.tan(Math.PI / 4 + phi / 2)) / (2 * Math.PI);
}

function lonDegFromMercatorX(x: number): number {
  return x * 360 - 180;
}

function latDegFromMercatorY(y: number): number {
  const phi = 2 * Math.atan(Math.exp(Math.PI - y * 2 * Math.PI)) - Math.PI / 2;
  return (phi * 180) / Math.PI;
}

/**
 * Subdivided mesh for one grid artifact's footprint.
 *
 * TWO coordinate systems per vertex, on purpose:
 *
 *  - `positions` (a_pos) is exactly what the existing 4-vertex quad carries:
 *    mercator-unit XY, latitude-clamped to ±85.051°. It feeds MapLibre's
 *    fallback matrix during the globe->mercator transition and it feeds the
 *    existing `v_mercUnit` varying, so nothing downstream changes shape.
 *
 *  - `lonLat` (a_lonLat) is the TRUE spherical position in radians, NOT
 *    mercator-clamped. This is the only reason a row at lat ±90 can exist at
 *    all: MapLibre's own `projectToSphere()` derives latitude from mercator Y,
 *    where ±90° is at infinity, so a layer that routes through the sanctioned
 *    `projectTile()` prelude can never draw a polar cap.
 *
 * Geographic (EPSG:4326) grids get rows spaced linearly in LATITUDE and a
 * v texcoord linear in latitude — which is exactly the 4326 row layout, so the
 * per-fragment inverse-Gudermannian of the mercator path becomes unnecessary
 * (and exact, rather than piecewise-linear).
 *
 * EPSG:3857 grids get rows spaced linearly in MERCATOR Y (the artifact's own
 * row layout) with a v texcoord linear in mercator Y — the forward-Gudermannian
 * mapping, applied at vertices instead of per fragment.
 */
export function buildGlobeMesh(
  bbox: [number, number, number, number],
  projection: string | null | undefined,
  geographic: boolean,
  fullWorld: boolean,
  cols: number,
  rows: number,
): GlobeMesh {
  const signature = `${projection ?? ""}|${bbox.join(",")}|${cols}x${rows}`;
  const vertsX = cols + 1;
  const vertsY = rows + 1;
  const vertexCount = vertsX * vertsY;
  const positions = new Float32Array(vertexCount * 2);
  const lonLat = new Float32Array(vertexCount * 2);
  const texCoords = new Float32Array(vertexCount * 2);

  // Longitude span of the drawn footprint. A full-world geographic grid is
  // drawn world-exact (-180..180) with the half-cell texcoord inset, matching
  // buildQuadVertices/buildQuadTexCoords.
  const westDeg = geographic ? (fullWorld ? -180 : bbox[0]) : lonDegFromMercatorX(bbox[0] / 20037508.342789244 / 2 + 0.5);
  const eastDeg = geographic ? (fullWorld ? 180 : bbox[2]) : lonDegFromMercatorX(bbox[2] / 20037508.342789244 / 2 + 0.5);
  const lonSpanBboxDeg = geographic ? bbox[2] - bbox[0] : 0;

  // 3857 grids: mercator-unit Y bounds straight from the metre bbox.
  const merTop = geographic
    ? mercatorYFromLatDeg(bbox[3])
    : 0.5 - bbox[3] / (2 * Math.PI * 6378137);
  const merBottom = geographic
    ? mercatorYFromLatDeg(bbox[1])
    : 0.5 - bbox[1] / (2 * Math.PI * 6378137);

  for (let row = 0; row < vertsY; row += 1) {
    const ty = row / rows;
    let latDeg: number;
    let merY: number;
    let v: number;
    if (geographic) {
      // Linear in latitude: matches how a 4326 artifact's rows are laid out.
      latDeg = bbox[3] + (bbox[1] - bbox[3]) * ty;
      merY = mercatorYFromLatDeg(latDeg);
      v = ty;
    } else {
      merY = merTop + (merBottom - merTop) * ty;
      latDeg = latDegFromMercatorY(merY);
      v = ty;
    }
    const latRad = (latDeg * Math.PI) / 180;
    for (let col = 0; col < vertsX; col += 1) {
      const tx = col / cols;
      const lonDeg = westDeg + (eastDeg - westDeg) * tx;
      const index = (row * vertsX + col) * 2;
      positions[index] = mercatorXFromLonDeg(lonDeg);
      positions[index + 1] = merY;
      lonLat[index] = (lonDeg * Math.PI) / 180;
      lonLat[index + 1] = latRad;
      texCoords[index] = geographic && lonSpanBboxDeg > 0
        ? (lonDeg - bbox[0]) / lonSpanBboxDeg
        : tx;
      texCoords[index + 1] = v;
    }
  }

  const indices = new Uint16Array(cols * rows * 6);
  let cursor = 0;
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const topLeft = row * vertsX + col;
      const topRight = topLeft + 1;
      const bottomLeft = topLeft + vertsX;
      const bottomRight = bottomLeft + 1;
      indices[cursor] = topLeft;
      indices[cursor + 1] = bottomLeft;
      indices[cursor + 2] = topRight;
      indices[cursor + 3] = topRight;
      indices[cursor + 4] = bottomLeft;
      indices[cursor + 5] = bottomRight;
      cursor += 6;
    }
  }

  return { positions, lonLat, texCoords, indices, indexCount: indices.length, signature };
}

/**
 * Globe vertex shader.
 *
 * Deliberately does NOT use MapLibre's `shaderData.vertexShaderPrelude` /
 * `projectTile()`. The prelude's `projectToSphere()` takes MERCATOR
 * coordinates, so latitudes beyond ±85.051° are unreachable and the polar caps
 * of a 1440x721 GFS grid would be holes. Computing the sphere position from
 * true lon/lat and multiplying by `mainMatrix` (documented as "projects a unit
 * sphere planet to screen") avoids the singularity entirely.
 *
 * The cost of leaving the sanctioned path is that the transition blend and the
 * horizon clip have to be reimplemented here. Both are copied line-for-line
 * from MapLibre 5.24's `interpolateProjection()` /`globeComputeClippingZ()`
 * (dist/maplibre-gl-dev.js, `projectionGlobeVert`), using the exact uniforms
 * MapLibre hands over in `defaultProjectionData`.
 */
export const GLOBE_SPIKE_VERTEX_SOURCE = `
  attribute vec2 a_pos;
  attribute vec2 a_texCoord;
  attribute vec2 a_lonLat;
  uniform mat4 u_matrix;
  uniform mat4 u_globeFallbackMatrix;
  uniform vec4 u_globeClippingPlane;
  uniform float u_globeTransition;
  varying vec2 v_texCoord;
  varying vec2 v_mercUnit;
  varying float v_latRad;
  void main() {
    v_texCoord = a_texCoord;
    v_mercUnit = a_pos;
    v_latRad = a_lonLat.y;
    float len = cos(a_lonLat.y);
    // MapLibre sphere convention (projectionGlobeVert::projectToSphere):
    // spherical.x = mercatorX * 2PI + PI, which reduces to the longitude in
    // radians modulo 2PI.
    vec3 sphere = vec3(sin(a_lonLat.x) * len, sin(a_lonLat.y), cos(a_lonLat.x) * len);
    vec4 globePosition = u_matrix * vec4(sphere, 1.0);
    globePosition.z =
      (1.0 - (dot(sphere, u_globeClippingPlane.xyz) + u_globeClippingPlane.w)) * globePosition.w;
    if (u_globeTransition > 0.999) {
      gl_Position = globePosition;
      return;
    }
    vec4 flatPosition = u_globeFallbackMatrix * vec4(a_pos, 0.0, 1.0);
    vec4 result = globePosition;
    result.z = mix(0.0, globePosition.z, clamp((u_globeTransition - 0.2) / 0.8, 0.0, 1.0));
    result.xyw = mix(flatPosition.xyw, globePosition.xyw, u_globeTransition);
    gl_Position = result;
  }
`;
