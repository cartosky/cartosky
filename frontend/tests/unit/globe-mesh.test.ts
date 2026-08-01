/**
 * Sphere-mesh topology for the globe grid renderer.
 *
 * These pin the properties a golden image cannot state precisely, and they are
 * written to CATCH index mutations rather than merely describe the current
 * output: a wrong polar band still produces a plausible-looking sphere, and the
 * pixel budget absorbed an off-by-one fan in review.
 */
import { describe, expect, it } from 'vitest';

import {
  buildGlobeMesh,
  expectedMatrixSpaceFor,
  isProjectionPairingCoherent,
  deriveMatrixSpace,
  readFrameProjection,
} from '../../src/lib/globe-projection';

/**
 * All mesh attributes are Float32Array (they are uploaded straight to GL), so
 * every tolerance below is a float32 tolerance, not a float64 one. A latitude
 * near 1.5 rad carries ~1.8e-7 rad of float32 rounding, i.e. ~1e-5 degrees, so
 * degree-scale comparisons get 4 decimal places.
 */
const F32_DIGITS = 4;

/** The global-domain contract grid: cell-edge bbox, rows at exactly +/-90. */
const GLOBAL_BBOX: [number, number, number, number] = [-180.125, -90.125, 179.875, 90.125];
/** A CONUS-sized EPSG:3857 footprint in metres. */
const REGIONAL_BBOX: [number, number, number, number] = [-12073876, 4042782, -9873876, 5642782];

const COLS = 64;
const ROWS = 32;
const VERTS_X = COLS + 1;

type Mesh = ReturnType<typeof buildGlobeMesh>;

const globalMesh = () => buildGlobeMesh(GLOBAL_BBOX, 'EPSG:4326', true, true, COLS, ROWS);
const regionalMesh = () => buildGlobeMesh(REGIONAL_BBOX, 'EPSG:3857', false, false, COLS, ROWS);

/** Unit-sphere position for a vertex, the same construction the shader uses. */
function sphereAt(mesh: Mesh, vertex: number): [number, number, number] {
  const lon = mesh.lonLat[vertex * 2];
  const lat = mesh.lonLat[vertex * 2 + 1];
  const len = Math.cos(lat);
  return [Math.sin(lon) * len, Math.sin(lat), Math.cos(lon) * len];
}

const rowOf = (vertex: number) => Math.floor(vertex / VERTS_X);
const colOf = (vertex: number) => vertex % VERTS_X;

function triangles(mesh: Mesh): Array<[number, number, number]> {
  const out: Array<[number, number, number]> = [];
  for (let i = 0; i < mesh.indices.length; i += 3) {
    out.push([mesh.indices[i], mesh.indices[i + 1], mesh.indices[i + 2]]);
  }
  return out;
}

/**
 * Twice the signed area vector of a triangle in sphere space. Its magnitude is
 * the area; its sign against the outward radial direction is the winding.
 */
function areaVector(mesh: Mesh, tri: [number, number, number]): [number, number, number] {
  const [pa, pb, pc] = tri.map((v) => sphereAt(mesh, v));
  const u = [pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]];
  const v = [pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]];
  return [
    u[1] * v[2] - u[2] * v[1],
    u[2] * v[0] - u[0] * v[2],
    u[0] * v[1] - u[1] * v[0],
  ];
}

const area = (mesh: Mesh, tri: [number, number, number]) => {
  const a = areaVector(mesh, tri);
  return 0.5 * Math.hypot(a[0], a[1], a[2]);
};

/** +1 when the triangle faces outward from the sphere centre, -1 inward. */
function windingSign(mesh: Mesh, tri: [number, number, number]): number {
  const a = areaVector(mesh, tri);
  const centroid = tri
    .map((v) => sphereAt(mesh, v))
    .reduce((acc, p) => [acc[0] + p[0] / 3, acc[1] + p[1] / 3, acc[2] + p[2] / 3], [0, 0, 0]);
  return Math.sign(a[0] * centroid[0] + a[1] * centroid[1] + a[2] * centroid[2]);
}

describe('buildGlobeMesh -- polar topology', () => {
  it('emits a pole RING of per-column vertices, coincident in space, distinct in texture', () => {
    const mesh = globalMesh();
    const vertexCount = mesh.lonLat.length / 2;
    expect(vertexCount).toBe((ROWS + 1) * VERTS_X);

    for (const [label, row, expectedLat] of [
      ['north', 0, Math.PI / 2],
      ['south', ROWS, -Math.PI / 2],
    ] as const) {
      const ring: number[] = [];
      for (let col = 0; col < VERTS_X; col += 1) {
        ring.push(row * VERTS_X + col);
      }
      // Latitude clamped to exactly +-90 despite the +-90.125 cell-edge bbox.
      for (const vertex of ring) {
        expect(mesh.lonLat[vertex * 2 + 1], `${label} ring latitude`).toBeCloseTo(expectedLat, F32_DIGITS);
      }
      // Coincident in SPACE...
      const first = sphereAt(mesh, ring[0]);
      for (const vertex of ring) {
        const p = sphereAt(mesh, vertex);
        expect(
          Math.hypot(p[0] - first[0], p[1] - first[1], p[2] - first[2]),
          `${label} ring is one point on the sphere`,
        ).toBeLessThan(1e-6);
      }
      // ...but DISTINCT in texture. This is the whole point of the ring: a
      // single apex vertex carries one texcoord, which collapses texture
      // longitude across the polar band and bends the meridians into S-shaped
      // spokes. Ring s must advance monotonically, column by column.
      for (let col = 1; col < VERTS_X; col += 1) {
        const previous = mesh.texCoords[ring[col - 1] * 2];
        const current = mesh.texCoords[ring[col] * 2];
        expect(current, `${label} ring s advances at column ${col}`).toBeGreaterThan(previous);
      }
      // And it must match the s of the SAME column on the neighbouring row, so
      // meridians run straight through the pole rather than shearing.
      const neighbourRow = row === 0 ? 1 : ROWS - 1;
      for (let col = 0; col < VERTS_X; col += 1) {
        expect(
          mesh.texCoords[ring[col] * 2],
          `${label} ring s matches its own meridian at column ${col}`,
        ).toBeCloseTo(mesh.texCoords[(neighbourRow * VERTS_X + col) * 2], 6);
      }
    }
  });

  it('emits exactly one triangle per column in each polar band, and no zero-area triangles', () => {
    const mesh = globalMesh();
    expect(mesh.poleFanTriangles).toBe(2 * COLS);

    const all = triangles(mesh);
    // Total = interior bands (2 tris/col) + 2 polar bands (1 tri/col).
    expect(all.length).toBe((ROWS - 2) * COLS * 2 + 2 * COLS);

    // The finest triangles span 360/64 deg of longitude; 1e-9 is orders of
    // magnitude below that and far above float noise, so this only catches
    // genuinely collapsed corners -- including "emitted the wrong one of the
    // two polar triangles", whose two ring corners are the same point.
    for (const tri of all) {
      expect(area(mesh, tri), `triangle ${tri.join(',')} has area`).toBeGreaterThan(1e-9);
    }
  });

  it('covers every polar column wedge exactly once, with consistent winding', () => {
    const mesh = globalMesh();
    const all = triangles(mesh);

    // Winding is uniform across the WHOLE mesh, poles included: a flipped polar
    // band would light this up even though it renders identically with culling
    // disabled.
    const signs = new Set(all.map((tri) => windingSign(mesh, tri)));
    expect(signs.size, 'one winding for the whole mesh').toBe(1);

    for (const [label, ringRow, neighbourRow] of [
      ['north', 0, 1],
      ['south', ROWS, ROWS - 1],
    ] as const) {
      const polar = all.filter((tri) => tri.some((v) => rowOf(v) === ringRow));
      expect(polar.length, `${label} polar band triangle count`).toBe(COLS);

      const wedges = new Set<string>();
      for (const tri of polar) {
        const onRing = tri.filter((v) => rowOf(v) === ringRow);
        const onNeighbour = tri.filter((v) => rowOf(v) === neighbourRow);
        // Exactly one ring corner and two corners on the ADJACENT row. A band
        // wired to the wrong row fails here.
        expect(onRing.length, `${label}: one ring corner`).toBe(1);
        expect(onNeighbour.length, `${label}: two corners on the adjacent row`).toBe(2);

        // The wedge this triangle covers, in COLUMN space. A fan off-by-one
        // shifts every wedge, which leaves column 0 uncovered and pushes the
        // last one out of range -- caught by the exact-cover assertion below.
        const columns = [...onNeighbour.map(colOf), colOf(onRing[0])].sort((a, b) => a - b);
        expect(columns[2] - columns[0], `${label}: wedge spans adjacent columns`).toBe(1);
        wedges.add(`${columns[0]}-${columns[2]}`);

        // Texture-space statement of the same thing: the triangle's three s
        // values are exactly the two column texcoords of its own wedge.
        const sValues = new Set(tri.map((v) => mesh.texCoords[v * 2].toFixed(6)));
        expect(sValues.size, `${label}: wedge spans exactly two texture columns`).toBe(2);
      }
      // EXACT cover: every adjacent column pair once, none missing, none twice.
      expect(wedges.size, `${label}: every column wedge covered exactly once`).toBe(COLS);
      for (let col = 0; col < COLS; col += 1) {
        expect(wedges.has(`${col}-${col + 1}`), `${label}: wedge ${col}-${col + 1} present`).toBe(true);
      }
    }
  });

  it('indexes every vertex it allocates and never past the end', () => {
    const mesh = globalMesh();
    const vertexCount = mesh.lonLat.length / 2;
    expect(mesh.indexCount).toBe(mesh.indices.length);
    const referenced = new Set<number>(mesh.indices);
    for (const index of mesh.indices) {
      expect(index, 'index inside the allocated vertex range').toBeLessThan(vertexCount);
    }

    // Every vertex is referenced EXCEPT one per pole ring. Each polar wedge is
    // (ring[c+1], row[c], row[c+1]), so the wedge's apex is the ring vertex on
    // its eastern meridian -- which leaves the ring's westernmost vertex with no
    // wedge to belong to. Nothing is lost: on a full-world grid ring[0] and
    // ring[COLS] are the same sphere point and the same texture column after the
    // u_wrapS fetch. Stated exactly so an indexing change cannot quietly orphan
    // a whole row.
    const unreferenced = [];
    for (let vertex = 0; vertex < vertexCount; vertex += 1) {
      if (!referenced.has(vertex)) {
        unreferenced.push(vertex);
      }
    }
    expect(unreferenced, 'exactly the two ring end vertices are unreferenced').toEqual([
      0,
      ROWS * VERTS_X + COLS,
    ]);
    // Uint16 index buffer: the vertex count must stay addressable.
    expect(vertexCount).toBeLessThan(65536);
  });

  it('gives a regional EPSG:3857 footprint no poles at all', () => {
    const mesh = regionalMesh();
    expect(mesh.poleFanTriangles).toBe(0);
    expect(mesh.indexCount).toBe(COLS * ROWS * 6);
    for (const tri of triangles(mesh)) {
      expect(area(mesh, tri)).toBeGreaterThan(1e-9);
    }
  });
});

describe('buildGlobeMesh -- polar longitude fidelity', () => {
  /**
   * THE measurement that separates a correct pole from a plausible-looking one,
   * and the reason the ring topology exists.
   *
   * Inside any polar triangle, interpolate BOTH channels the way the GPU does --
   * the sphere position from a_lonLat, and the texture coordinate from
   * a_texCoord -- and ask whether they still describe the same longitude. They
   * must: `s` is a linear function of longitude, so the texture longitude at a
   * fragment has to track the geometric longitude underneath it.
   *
   * With the ring, each polar triangle is contained in the wedge between two
   * adjacent column meridians AND its three `s` values are exactly that wedge's
   * two column texcoords, so the error is bounded by ONE COLUMN by construction.
   *
   * With a single apex vertex the apex carries one longitude for the whole cap,
   * so `s` is dragged toward that one meridian while the geometry fans out --
   * the verifier measured 83.5 degrees of error at barycentric 0.9, i.e. the
   * texture is showing a place a quarter of the world away from where the
   * geometry puts it. That is the S-bent spoke, in numbers.
   */
  it('keeps texture longitude within one column of geometric longitude inside every polar triangle', () => {
    const mesh = globalMesh();
    const lonSpanDeg = GLOBAL_BBOX[2] - GLOBAL_BBOX[0];
    const columnWidthDeg = 360 / COLS;

    // Barycentric sample grid, skipping the exact corners.
    const samples: Array<[number, number, number]> = [];
    const STEPS = 10;
    for (let i = 1; i < STEPS; i += 1) {
      for (let j = 1; j < STEPS - i; j += 1) {
        samples.push([i / STEPS, j / STEPS, 1 - i / STEPS - j / STEPS]);
      }
    }

    let worstErrorDeg = 0;
    let worstAt = '';
    for (const [ringRow, label] of [[0, 'north'], [ROWS, 'south']] as const) {
      const polar = triangles(mesh).filter((tri) => tri.some((v) => rowOf(v) === ringRow));
      for (const tri of polar) {
        const corners = tri.map((v) => ({
          sphere: sphereAt(mesh, v),
          s: mesh.texCoords[v * 2],
        }));
        for (const [wa, wb, wc] of samples) {
          const w = [wa, wb, wc];
          const point: [number, number, number] = [0, 0, 0];
          let s = 0;
          for (let k = 0; k < 3; k += 1) {
            point[0] += corners[k].sphere[0] * w[k];
            point[1] += corners[k].sphere[1] * w[k];
            point[2] += corners[k].sphere[2] * w[k];
            s += corners[k].s * w[k];
          }
          // Geometric longitude of the interpolated position. Degenerate only
          // AT the pole itself, which the corner-skipping avoids.
          const horizontal = Math.hypot(point[0], point[2]);
          if (horizontal < 1e-6) continue;
          const geometricLon = (Math.atan2(point[0], point[2]) * 180) / Math.PI;
          // Longitude the texture coordinate is pointing at.
          const textureLon = GLOBAL_BBOX[0] + s * lonSpanDeg;
          const error = Math.abs(((textureLon - geometricLon + 540) % 360) - 180);
          if (error > worstErrorDeg) {
            worstErrorDeg = error;
            worstAt = `${label} bary ${w.map((x) => x.toFixed(1)).join('/')}`;
          }
        }
      }
    }

    // eslint-disable-next-line no-console
    console.log(
      `[pole-longitude] worst texture-vs-geometry longitude error ` +
        `${worstErrorDeg.toFixed(3)} deg (${worstAt}); one column = ${columnWidthDeg.toFixed(3)} deg`,
    );
    expect(
      worstErrorDeg,
      'texture longitude never drifts more than one mesh column from the geometry',
    ).toBeLessThanOrEqual(columnWidthDeg + 1e-3);
  });
});

describe('buildGlobeMesh -- texture families', () => {
  it('spaces EPSG:4326 rows linearly in LATITUDE', () => {
    const mesh = globalMesh();
    const latOfRow = (row: number) => (mesh.lonLat[row * VERTS_X * 2 + 1] * 180) / Math.PI;
    // Rows 1..4: away from the clamped polar rows, spacing is the raw bbox step.
    const deltas: number[] = [];
    for (let row = 2; row <= 5; row += 1) {
      deltas.push(latOfRow(row - 1) - latOfRow(row));
    }
    for (const delta of deltas) {
      expect(delta).toBeCloseTo(deltas[0], F32_DIGITS);
    }
    // 180.25 deg over 32 rows.
    expect(deltas[0]).toBeCloseTo(180.25 / ROWS, F32_DIGITS);
  });

  it('spaces EPSG:3857 rows linearly in MERCATOR Y (forward Gudermannian at vertices)', () => {
    const mesh = regionalMesh();
    const merYOfRow = (row: number) => mesh.positions[row * VERTS_X * 2 + 1];
    const deltas: number[] = [];
    for (let row = 1; row <= 4; row += 1) {
      deltas.push(merYOfRow(row) - merYOfRow(row - 1));
    }
    for (const delta of deltas) {
      expect(delta).toBeCloseTo(deltas[0], 7);
    }
    // Latitude spacing, by contrast, must NOT be uniform for a 3857 footprint.
    const latOfRow = (row: number) => mesh.lonLat[row * VERTS_X * 2 + 1];
    const latDeltaTop = latOfRow(0) - latOfRow(1);
    const latDeltaLower = latOfRow(3) - latOfRow(4);
    expect(Math.abs(latDeltaTop - latDeltaLower)).toBeGreaterThan(1e-6);
  });
});

describe('buildGlobeMesh -- seam and texcoords', () => {
  it('closes a full-world grid at +/-180 and carries the half-cell texcoord inset', () => {
    const mesh = globalMesh();
    // Row 1: the first non-polar row.
    const west = VERTS_X;
    const east = VERTS_X + COLS;
    expect((mesh.lonLat[west * 2] * 180) / Math.PI).toBeCloseTo(-180, F32_DIGITS);
    expect((mesh.lonLat[east * 2] * 180) / Math.PI).toBeCloseTo(180, F32_DIGITS);
    // Same sphere point, so the mesh closes on itself.
    const a = sphereAt(mesh, west);
    const b = sphereAt(mesh, east);
    expect(Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])).toBeLessThan(1e-6);

    // Half-cell inset, identical to buildQuadTexCoords: s(-180) = 0.5/1440 and
    // s(+180) = 1 + 0.5/1440, which is what makes column 1439 and column 0
    // blend across the seam through u_wrapS.
    expect(mesh.texCoords[west * 2]).toBeCloseTo(0.5 / 1440, 8);
    expect(mesh.texCoords[east * 2]).toBeCloseTo(1 + 0.5 / 1440, 7);
  });

  it('carries the seam inset on the pole ring too', () => {
    const mesh = globalMesh();
    expect(mesh.texCoords[0]).toBeCloseTo(0.5 / 1440, 8);
    expect(mesh.texCoords[COLS * 2]).toBeCloseTo(1 + 0.5 / 1440, 7);
  });
});

describe('projection pairing tripwire', () => {
  const globeFrame = () => {
    const args = {
      shaderData: { variantName: 'globe' },
      defaultProjectionData: {
        mainMatrix: new Float64Array(16).fill(1),
        fallbackMatrix: new Float64Array(16).fill(2),
        clippingPlane: [0, 0, 1, 0],
        projectionTransition: 1,
      },
    };
    return readFrameProjection(args);
  };

  it('accepts only matching geometry/matrix pairs, and never an unknown matrix', () => {
    expect(expectedMatrixSpaceFor('globe')).toBe('unit-sphere');
    expect(expectedMatrixSpaceFor('mercator')).toBe('mercator-unit');

    expect(isProjectionPairingCoherent('globe', 'unit-sphere')).toBe(true);
    expect(isProjectionPairingCoherent('mercator', 'mercator-unit')).toBe(true);
    // THE failure mode: a mercator quad against a unit-sphere matrix draws
    // garbage with no GL error (docs/GLOBE_SPIKE_2026-08-01.md section 1.1).
    expect(isProjectionPairingCoherent('mercator', 'unit-sphere')).toBe(false);
    expect(isProjectionPairingCoherent('globe', 'mercator-unit')).toBe(false);
    // A matrix whose provenance could not be established is never safe.
    expect(isProjectionPairingCoherent('globe', null)).toBe(false);
    expect(isProjectionPairingCoherent('mercator', null)).toBe(false);
  });

  it('derives the space from the MATRIX, not from a caller-supplied label', () => {
    const frame = globeFrame();
    const globe = frame.globe!;
    const frameMainMatrix = Array.from(globe.mainMatrix);

    expect(deriveMatrixSpace(globe.mainMatrix, frameMainMatrix, frame)).toBe('unit-sphere');
    expect(deriveMatrixSpace(globe.fallbackMatrix, frameMainMatrix, frame)).toBe('mercator-unit');

    // Identity, not equality: a copy with the same numbers is NOT the matrix
    // MapLibre labelled, so its provenance is unknown and it is refused.
    expect(deriveMatrixSpace([...globe.mainMatrix], frameMainMatrix, frame)).toBeNull();
    expect(deriveMatrixSpace(new Array(16).fill(0), frameMainMatrix, frame)).toBeNull();

    // On a non-globe frame the only legitimate matrix is the frame's own.
    const mercatorFrame = readFrameProjection({
      shaderData: { variantName: 'mercator' },
      defaultProjectionData: { mainMatrix: new Float64Array(16), projectionTransition: 0 },
    });
    const mercatorMatrix = new Array(16).fill(3);
    expect(deriveMatrixSpace(mercatorMatrix, mercatorMatrix, mercatorFrame)).toBe('mercator-unit');
    expect(deriveMatrixSpace(new Array(16).fill(3), mercatorMatrix, mercatorFrame)).toBeNull();
  });

  it('MUTATION M1b: globe geometry handed the fallback matrix is refused', () => {
    // The exact silent-garbage bug the verifier injected: the globe branch
    // returns `fallbackMatrix` while still calling itself globe geometry. Under
    // the old label-based tag this drew 647,240/649,600 pixels wrong with
    // projectionMismatch reading 0. Derivation makes the label irrelevant.
    const frame = globeFrame();
    const globe = frame.globe!;
    const frameMainMatrix = Array.from(globe.mainMatrix);

    const mutatedChoice = { mode: 'globe' as const, matrix: globe.fallbackMatrix };
    const space = deriveMatrixSpace(mutatedChoice.matrix, frameMainMatrix, frame);
    expect(space, 'derived from the matrix, so it reports what it really is').toBe('mercator-unit');
    expect(
      isProjectionPairingCoherent(mutatedChoice.mode, space),
      'M1b must be refused before any draw',
    ).toBe(false);

    // And the mirror: mercator geometry handed the sphere matrix.
    expect(
      isProjectionPairingCoherent('mercator', deriveMatrixSpace(globe.mainMatrix, frameMainMatrix, frame)),
    ).toBe(false);
  });
});

describe('readFrameProjection', () => {
  const sphereArgs = (overrides: Record<string, unknown> = {}) => ({
    shaderData: { variantName: 'globe' },
    defaultProjectionData: {
      mainMatrix: new Float64Array(16),
      fallbackMatrix: new Float64Array(16),
      clippingPlane: [0, 0, 1, 0],
      projectionTransition: 1,
      ...overrides,
    },
  });

  it('reports mercator for the legacy bare-matrix argument', () => {
    expect(readFrameProjection(new Array(16).fill(0)).mainMatrixSpace).toBe('mercator-unit');
    expect(readFrameProjection(null).globe).toBeNull();
  });

  it('reports mercator when MapLibre is not globe-rendering', () => {
    const args = {
      shaderData: { variantName: 'mercator' },
      defaultProjectionData: { mainMatrix: new Float64Array(16), projectionTransition: 0 },
    };
    expect(readFrameProjection(args).mainMatrixSpace).toBe('mercator-unit');
    expect(readFrameProjection(args).globe).toBeNull();
  });

  it('reports unit-sphere only when BOTH MapLibre signals agree', () => {
    expect(readFrameProjection(sphereArgs()).mainMatrixSpace).toBe('unit-sphere');
    expect(readFrameProjection(sphereArgs()).globe).not.toBeNull();

    // Either signal disagreeing degrades to the mercator path rather than to a
    // garbage draw — the conservative direction if MapLibre ever diverges.
    const variantOnly = { ...sphereArgs(), shaderData: { variantName: 'mercator' } };
    expect(readFrameProjection(variantOnly).mainMatrixSpace).toBe('mercator-unit');
    expect(readFrameProjection(sphereArgs({ projectionTransition: 0 })).mainMatrixSpace)
      .toBe('mercator-unit');
  });
});
