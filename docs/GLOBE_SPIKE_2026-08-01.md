# Globe spike — custom WebGL grid layer on MapLibre 5.24 native globe

**Date:** 2026-08-01 · **Status:** spike complete, nothing committed, nothing shipped
**Recommendation:** **GO WITH CAVEATS** (see §7)

Scope: prove that `GridWebglLayerController` (`frontend/src/lib/grid-webgl.ts`) can
render on `map.setProjection({type:'globe'})`, and cost the production work. A
working prototype exists behind `?globe=1`; with the flag absent the renderer is
byte-for-byte unchanged (verified — §3.1).

Everything below is measured on this machine, chromium + SwiftShader, through the
existing `render-golden-baseline` fixture infrastructure. Raw numbers:
`frontend/globe-spike-artifacts/findings.json`. Screenshots:
`frontend/globe-spike-artifacts/*.png`.

---

## 1. What MapLibre 5.24 hands a custom layer under globe projection

Evidence read from the installed package (`frontend/node_modules/maplibre-gl@5.24.0`,
`dist/maplibre-gl.d.ts` and `dist/maplibre-gl-dev.js`), not from docs.

### 1.1 The render arguments

`CustomRenderMethod(gl, options: CustomRenderMethodInput)` — `maplibre-gl.d.ts:4901`.
Assembled in `drawCustom()` (`maplibre-gl-dev.js:64871`):

| field | under globe |
|---|---|
| `defaultProjectionData.mainMatrix` | **unit-sphere → clip** (vertical-perspective). NOT a mercator matrix. |
| `defaultProjectionData.fallbackMatrix` | mercator-units → clip (the mercator custom-layer matrix) |
| `defaultProjectionData.clippingPlane` | horizon plane in unit-sphere space (`vec4`) |
| `defaultProjectionData.projectionTransition` | 1 = globe, 0 = mercator, fractional = transition — **but see §5, it is hard-coded to 1 for custom layers** |
| `defaultProjectionData.tileMercatorCoords` | forced to `[0,0,1,1]`, i.e. "custom layers speak mercator 0..1" |
| `shaderData.variantName` | `'globe'` or `'mercator'` — the sanctioned shader-cache key |
| `shaderData.define` | `#define GLOBE` / `#define PROJECTION_MERCATOR` |
| `shaderData.vertexShaderPrelude` | `const float PI…; uniform mat4 u_projection_matrix; ` + the projection's vertex prelude |
| `modelViewProjectionMatrix`, `projectionMatrix`, `nearZ`, `farZ`, `fov` | camera, unchanged shape |

**This is the first hazard, and it is silent.** Our layer currently does
`Array.from(args.defaultProjectionData.mainMatrix)` and multiplies its
mercator-unit quad by it (`grid-webgl.ts` `render()`). Under globe that matrix
expects unit-sphere XYZ, so the existing code does not "degrade" on the globe —
it draws a garbage quad. There is no error, no warning, no type change.

### 1.2 The sanctioned pattern

Compile **two** programs, keyed by `shaderData.variantName`, each built as
`vertexShaderPrelude + define + your vertex source`, and call `projectTile(vec2)`
instead of `u_matrix * vec4(pos,0,1)`. Under globe `projectTile` resolves to
`interpolateProjection(posInTile, projectToSphere(posInTile), 0.0)`
(`projectionGlobeVert`, `maplibre-gl-dev.js:54760`), which does the sphere
projection, the horizon clip and the globe↔mercator blend for you.

**We deliberately did not use it, and that is a finding.** `projectToSphere()`
takes **mercator** coordinates and derives latitude with
`2*atan(exp(PI - y*2PI)) - PI/2`. Mercator Y for lat ±90° is infinite, so a layer
that routes through `projectTile()` **cannot express a vertex north of ~85.051°**.
MapLibre's own tiles never need to: the mercator tile pyramid stops there, and the
polar caps you see on a MapLibre globe are drawn by a separate "pole" geometry with
a `rawPos.y` sentinel (`if (rawPos.y < -32767.5) pos = vec3(0,1,0)`) that only the
tile path emits. A global GFS artifact is `1440x721` with **rows at exactly ±90°**
(`docs/GLOBAL_DOMAIN_4326_CONTRACT.md`) — through the prelude, the top and bottom
~5° of every global field would be a hole.

So the prototype computes the sphere position from **true lon/lat** and multiplies
by `mainMatrix` directly, then reimplements `interpolateProjection` and
`globeComputeClippingZ` in ~12 lines using the exact uniforms MapLibre hands over
(`GLOBE_SPIKE_VERTEX_SOURCE` in `frontend/src/lib/globe-spike.ts`). Cost of leaving
the sanctioned path: those ~12 lines are copied from MapLibre internals and would
need re-checking on a MapLibre major upgrade.

### 1.3 Transition zoom

`type: 'globe'` is sugar. `createProjectionFromName('globe')`
(`maplibre-gl-dev.js:59739`) expands it to:

```
['interpolate', ['linear'], ['zoom'], 11, 'vertical-perspective', 12, 'mercator']
```

so the transition is a **pure function of zoom**, not a timed animation — it can be
parked and screenshotted. Measured (`e-transition-*.png`, `findings.json`):

| zoom | MapLibre `transitionState` | value handed to our layer | our path | mercator quad draws |
|---|---|---|---|---|
| 10.5 | 1 | 1 | globe mesh | 0 |
| 11.0 | 1 | 1 | globe mesh | 0 |
| 11.25 | 0.75 | **1** | globe mesh | 0 |
| 11.5 | 0.5 | **1** | globe mesh | 0 |
| 11.75 | 0.25 | **1** | globe mesh | 0 |
| 12.0 | 0 | 0 | mercator quad | 3 |

---

## 2. Prototype

`?globe=1` (optionally `&globeMesh=<cols>x<rows>`, default `128x64`) read once at
module load. It (a) calls `map.setProjection({type:'globe'})` on the map's `load`
event and (b) switches `grid-webgl.ts` onto a second program + a subdivided mesh.

### 2.1 Mesh

Built per artifact footprint (`buildGlobeMesh`, `globe-spike.ts`). Each vertex
carries **two** coordinate systems:

- `a_pos` — mercator-unit XY, latitude-clamped to ±85.051°. Identical to what the
  existing 4-vertex quad carries; feeds `fallbackMatrix` during the transition and
  feeds the existing `v_mercUnit` varying, so nothing downstream changes shape.
- `a_lonLat` — true lon/lat in radians, **not** clamped. This is what makes ±90°
  reachable.

Row spacing is per-projection: **EPSG:4326 rows are linear in latitude** (which is
exactly a 4326 artifact's own row layout), **EPSG:3857 rows are linear in mercator
Y** (forward-Gudermannian applied at vertices instead of per fragment).

### 2.2 Texture coordinates

Unchanged in intent from `buildQuadTexCoords()`: `s = (lon - bbox.west)/lonSpan`,
so a full-world geographic grid keeps the half-cell inset
(`s(-180) = 0.5/1440`, `s(+180) = 1 + 0.5/1440`) that makes the seam column blend.
`v` is linear in the row parameter, which for 4326 is linear in latitude.

The fragment shader is **the same source** for both programs except one line: for
the globe variant `projectedUv()` reads an exact `v_latRad` varying instead of
`latitudeRadFromMercatorUnitY(v_mercUnit.y)`. The 4326 latitude→row mapping is
therefore *more* accurate on the globe than in mercator (exact at every vertex,
linear in latitude between them, rather than a per-fragment inverse Gudermannian
against a mercator-clamped quad). Bilinear sampling, the `u_wrapS` seam wrap, the
categorical/ptype paths, contours and the LUT are untouched.

### 2.3 Results — 4326 global fixture (`gfs-global-seam`, 1440x721, ±90°)

| artifact | what it shows |
|---|---|
| `b1-globe-4326-atlantic.png` | full global field on the sphere, clean limb |
| `b2-globe-4326-antimeridian.png` | camera on lon 180 |
| `b3-globe-4326-northpole.png` | **north pole renders, converging to a single point — no hole, no tear** |
| `b4-globe-4326-southpole.png` | same at the south pole |

**Poles.** Rows at exactly ±90° render. Topology: the mesh's polar row is a
degenerate ring — `cols` vertices all at the same sphere point — so the polar cells
collapse into a triangle fan and every triangle there has zero area at one corner.
SwiftShader rasterizes this without artifacts; the visible result is the expected
radial "fan" of the field converging on the pole (`b3`). Note the pole *cell* of a
0.25° grid is 1440 identical values in the artifact itself, so the fan is data, not
error. No special-casing was needed — but the degenerate ring is a latent hazard on
drivers less forgiving than SwiftShader, and production should collapse the polar
row to a single shared vertex + a real fan index list rather than rely on it.

**Seam.** Numeric probe (`antimeridian seam continuity on the globe`), camera on
lon 180, scanline through the disc centre:

```
discLeft=322 discRight=605 seamX=464
seamStepMax = 1.783   controlStepMax = 8.930
```

The largest adjacent-pixel step within ±3 px of the seam is **five times smaller**
than the largest step elsewhere on the same scanline — the wrap is closed. (The two
halves of `b2` look different because the fixture's `sin(3·lon)` term is
anti-symmetric about lon 180; that is the field, not a seam.)

**Horizon curvature vs mesh density** (`c-mesh-*.png`, same camera):

| mesh | indices | result |
|---|---|---|
| `4x2` | 48 | grossly faceted — an octahedron inscribed in the globe |
| `16x8` | 768 | reads as a sphere, but a white sliver at the limb and a notch at the pole |
| `64x32` | 12,288 | clean at zoom 1–2 |
| `128x64` (default) | 49,152 | clean |
| `256x128` | 196,608 | no visible improvement over 128x64 |

**`64x32` is the smallest density with no visible artifact at whole-globe zooms.**
Production should size the mesh from the artifact's on-screen extent rather than
fix it — a CONUS 3857 grid at zoom 6 needs far less than a global one at zoom 1.

### 2.4 Results — 3857 regional fixture

`d-globe-3857-conus.png`: the CONUS quad renders on the sphere with correct
curvature (the footprint's north edge visibly bows). The forward-Gudermannian
`v` mapping at vertices reproduces the mercator appearance; no seam or wrap
involvement (`u_wrapS = 0`, unchanged).

---

## 3. Interaction with existing machinery

### 3.1 Flag-off parity — **verified bit-identical**

Full `render-golden-baseline` suite re-run after all edits, flag absent:

```
10 passed
determinism probe          exactDiffPixels=0/649600  maxChannelDelta=0
gfs-tmp2m-live-canvas      diffPixels=0/649600  maxChannelDelta=0
hrrr-radar-ptype           diffPixels=0/649600  maxChannelDelta=0
mrms-reflectivity          diffPixels=0/649600  maxChannelDelta=0
gfs-global-seam            diffPixels=0/649600  maxChannelDelta=0
gfs-global-seam-zoom       diffPixels=0/649600  maxChannelDelta=0
gif frames 0/1/2           diffPixels=0/390960  maxChannelDelta=0
```

plus all five server-side `toHaveScreenshot` goldens. The mercator fragment source
is produced by `buildFragmentSource(false)`, whose spike interpolations collapse to
`""` / the exact previous text, so the mercator program compiles the identical
string it always did.

*(The suite rewrites `frontend/tests/e2e/render-golden-baseline.timing.json`; it was
restored with `git checkout` after the run.)*

### 3.2 World-copy loop — must be, and is, bypassed

`visibleWorldOffsets()` / `translatedWorldMatrix()` add whole mercator worlds to the
model translation, which is meaningless against a unit-sphere matrix — and the
sphere already closes on itself, so there is nothing to replicate. The globe path
issues **exactly one `drawElements`**. Instrumented and asserted in every globe
test: `mercatorQuadDraws === 0` on all globe frames, `> 0` on mercator frames. **No
double draw.**

Secondary hazard worth recording: `visibleWorldOffsets()` derives world indices from
`map.unproject()` of the four canvas corners. On the globe those corners are off the
disc and unproject clamps them to horizon longitudes, so the derived range would be
nonsense. The existing `WORLD_INDEX_LIMIT` / `MAX_WORLD_COPIES` clamps prevent a
hang, but the function must not run on globe frames regardless. It doesn't.

### 3.3 Coherence guard — unaffected

As predicted, it is projection-agnostic: it compares `manifestIdentityKey(manifest)`
against `currentTextureManifestKey`, both texture/manifest identity. Verified on
every globe test: `drewMismatched === 0`, `coherentDraws` climbing. `visibleFrameHour()`
needs no change. **Not modified.**

### 3.4 Hover / `map.unproject` on the globe — correct, with a horizon caveat

Round-trip `map.project(map.unproject(px))`, camera lat 0 lon 0 zoom 1:

| probe | error |
|---|---|
| sub-camera point | 0.00 px |
| 0.3 R off-centre (x and y) | 0.00 px |
| 0.7 R off-centre | **4.31 px** |
| 0.9 R off-centre | 69.5 px |

The non-zero entries are not error: at zoom 1 the camera is close enough that the
**visible cap ends at ±82.28° of longitude, well inside the geometric limb** — those
probes are past the horizon, where `unproject` deliberately clamps to the nearest
horizon point. Inside the horizon it is exact. Two consequences for production:

- Hover values are correct wherever there is data to hover.
- Hover *near the limb* silently returns the horizon lng/lat, so the readout will
  pin to a fixed value instead of going blank. `transform.isLocationOccluded()` is
  the public-ish escape hatch; a horizon dead-zone needs an explicit product
  decision.
- **In globe projection the sphere's circumference equals the mercator world width,
  so the disc radius is `worldSize/(2π)` — at zoom 1 the globe is only ~326 px
  across on a 928 px canvas.** Any pixel-space logic that assumes "the map fills the
  viewport" (city label culling, the mobile chrome geometry, the export composer)
  needs auditing.

### 3.5 Capture — works unchanged

`window.__cartoskyViewerCapture()` on the globe returns a valid PNG in 61.5 ms with
1052 distinct colours (not blank). The repaint-hook fix from Share Phase 0 is
projection-independent — it forces a repaint and reads the same canvas. **Not
modified.** GIF export was not exercised on the globe (it drives the same hook, so
it is expected to work, but that is an assumption, not a measurement).

---

## 4. Effort not spent / not measured

Honest gaps in this spike:

- City labels, the warnings overlay, compare mode and the sounding/anchor overlays
  were not exercised on the globe. The golden fixtures stub the basemap to a
  transparent PNG, so the grid-vs-basemap *visual* alignment during the transition
  was checked numerically (§5) rather than by eye.
- Mobile/touch on a globe camera: untouched.
- Non-SwiftShader GPUs: untouched. The degenerate polar ring (§2.3) is the one thing
  I would expect to behave differently on a real driver.

---

## 5. The transition — the highest-unknown item, resolved

**Finding: MapLibre 5.24 hands custom layers `projectionTransition = 1` for the
entire globe→mercator transition. It never gives them the real blend factor.**

Root cause, `maplibre-gl-dev.js`:

- Tiles go through `GlobeTransform.getProjectionData()` (:59182), which sets
  `projectionTransition: applyGlobeMatrix ? this._globeness : 0` — the true ramp.
- Custom layers go through `GlobeTransform.getProjectionDataForCustomLayer()`
  (:59306), which delegates to `VerticalPerspectiveTransform.getProjectionData()`
  (:58324), which returns **`projectionTransition: applyGlobeMatrix ? 1 : 0`** —
  a hard-coded literal that never consults `_globeness`.

So a custom layer that faithfully implements `interpolateProjection()` from the
handed-over uniforms stays fully spherical while the basemap tiles morph flat. The
layer's `fallbackMatrix` branch is dead code in practice.

**Measured consequence: negligible.** For each of four viewport points we computed
(a) where the spike layer puts a lngLat and (b) where a MapLibre tile puts the same
lngLat, using tiles' real blend factor:

| zoom | true `transitionState` | handed to layer | max layer-vs-tile divergence |
|---|---|---|---|
| 11.00 | 1.00 | 1 | 0.000 px |
| 11.25 | 0.75 | 1 | 0.056 px |
| 11.50 | 0.50 | 1 | 0.108 px |
| 11.75 | 0.25 | 1 | 0.156 px |

Sub-pixel throughout, because MapLibre picked zoom 11–12 for the transition
precisely where globe and mercator have already converged (viewport ≈ a few km).
`e-transition-z11_5.png` shows the grid sitting correctly under live road/boundary
geometry mid-transition.

**Production stance:** do not try to work around it. Either (a) accept ≤0.16 px, or
(b) hard-flip the layer to the mercator quad path as soon as `transitionState < 1`
— at zoom ≥ 11 curvature is invisible anyway, and the flip costs nothing. (b) is
cheaper and removes the fallback branch entirely. The one thing to *avoid* is
reaching into `map.style.projection.transitionState` (private) to fake the correct
value: it buys 0.16 px and creates an upgrade landmine.

---

## 6. Performance

Same-session A/B on the global fixture (`gfs-global-seam`, 1440x721) at lat 20 /
lon -40 / zoom 1. Both arms load with `?globe=1` so the page, camera and map handle
are identical; only `map.setProjection()` differs, so the mercator arm exercises the
real quad + world-copy path. 25 repaints, first 5 discarded, each forced to
completion with a 1-px `readPixels`. Four independent runs:

| arm | median (ms) | p95 (ms) |
|---|---|---|
| mercator (quad + world copies) | 71.3 / 51.5 / **44.5** / **44.5** | 149.5 / 68.5 / 47.8 / 49.6 |
| globe `64x32` (12k idx) | 41.8 / 66.1 / **40.3** / **42.5** | 51.4 / 135.9 / 48.2 / 67.6 |
| globe `128x64` (49k idx) | 44.7 / 43.4 / **42.0** / **44.2** | 56.5 / 89.6 / 54.8 / 56.7 |
| globe `256x128` (197k idx) | 53.4 / 49.3 / **50.2** / **51.2** | 93.9 / 68.0 / 62.9 / 61.8 |

Reading (the two quiet runs, bolded, are the trustworthy ones; SwiftShader noise is
±7 ms median / ±20 ms p95, and the first run of a session is always contaminated):

- **Globe at 64x32 and 128x64 is at parity with mercator** (within noise).
- 256x128 costs ~**+6 ms median (~+14%)** — the first density where vertex work is
  measurable.
- The frame is **fill-rate bound, not vertex bound**. At zoom 1 the mercator arm
  rasterizes 3–5 full-viewport world copies of the same texture while the globe
  rasterizes one ~326 px disc, which is why the globe is not slower despite 49,152
  indices vs 4 vertices.
- Mesh rebuild is signature-cached (`buildGlobeMesh` runs only when bbox/projection/
  density change), so it is off the per-frame path entirely.

**Conclusion: mesh density is not the cost driver below ~50k indices. Budget 64x32
–128x64 and stop thinking about it.**

---

## 7. Recommendation: GO WITH CAVEATS

The renderer question is answered affirmatively and with margin: a 4326 global grid
renders on the globe **including both poles**, with the antimeridian seam closed,
one draw call, no perf regression, and the coherence guard and capture path
unaffected. The existing fragment shader — every decode, palette, ptype, contour and
edge-fade path — is reused verbatim.

The caveats are not renderer caveats:

1. **`projectTile()` is unusable for us** (§1.2). We must own ~12 lines of copied
   MapLibre projection math, re-verified on every MapLibre major.
2. **The silent-garbage failure mode** (§1.1). Any future code path that reaches
   `render()` with a globe `mainMatrix` but takes the quad branch draws nonsense
   with no error. Needs an explicit assertion, not a comment.
3. **Everything that is not the grid layer is unaudited** (§4): city labels,
   warnings overlay, compare mode, mobile, export composition, and the "map fills
   the viewport" pixel assumptions (§3.4).
4. **Poles are a degenerate ring today** (§2.3) and need a proper fan on real GPUs.
5. **Hover near the limb** silently pins to the horizon (§3.4) — a product decision,
   not a bug.

Recommend proceeding, but scoping the project as *"globe as a view mode for global
domains"* with an explicit audit phase for the non-grid overlays, rather than as a
renderer change.

---

## 8. Production plan

Day estimates assume one engineer, and include tests.

| # | Item | Est. | Notes |
|---|---|---|---|
| 1 | Mesh path: promote `buildGlobeMesh` into `grid-webgl.ts`, collapse the polar row to a real fan, size density from on-screen extent | 1.5 d | the degenerate-ring fix is the fiddly part |
| 2 | Shader: single fragment source with the `v_latRad` varying (drop the two-variant builder), second vertex program keyed on `shaderData.variantName`, program cache | 1.0 d | |
| 3 | Projection-state plumbing: `readGlobeFrameProjection`, hard flip to the mercator quad when `projectionTransition < 1` (§5 option b), **hard assertion** that the quad path never sees a globe matrix | 0.5 d | closes caveat 2 |
| 4 | World-copy loop guarded off + regression test asserting exactly one draw | 0.25 d | done in spike; needs a real test |
| 5 | Button / URL param / persisted preference / analytics event | 1.0 d | needs a product decision on where the control lives (Region section of the rail?) and whether it is global-domain-only |
| 6 | Camera + interaction: min/max zoom for globe, what happens when the user zooms past 12, restoring the mercator camera, mobile touch | 1.5 d | |
| 7 | **Overlay audit** — city labels, warnings overlay, anchor/value labels, compare mode, sounding pins on the globe | 2.0 d | biggest unknown; may itself spawn work |
| 8 | Hover/limb behaviour: `isLocationOccluded` gating, blank vs pinned readout | 0.5 d | product decision first |
| 9 | Capture / export decision: does share export offer the globe? GIF on the globe? chrome scaling against a non-viewport-filling map | 1.0 d | capture itself works; composition does not |
| 10 | Goldens: new globe cases (global 4326, 3857 regional, pole, seam, transition) added to `render-golden-baseline`; existing mercator goldens re-baselined **only if** item 2 changes the shared fragment source | 1.0 d | spike proved a re-baseline is avoidable if done carefully |
| 11 | Perf gate on a real GPU + a low-end device | 0.5 d | SwiftShader is not the risk profile that matters |

**Total: ~11.75 days** ≈ 2.5 calendar weeks with normal interruption.

### Risks

| risk | severity | mitigation |
|---|---|---|
| Overlay audit (#7) uncovers structural work (label placement, occlusion culling) | **high** | timebox #7 first, before committing to a ship date |
| Degenerate polar fan misbehaves on a real GPU/driver | medium | #1 fixes it properly; verify on hardware early |
| MapLibre changes `getProjectionDataForCustomLayer` or the globe prelude | medium | pin MapLibre; the copied math is 12 lines in one file with a source citation |
| `projectionTransition` hard-code is fixed upstream and our hard flip becomes redundant | low | harmless |
| "Map fills the viewport" assumptions elsewhere in the app | medium | grep for `clientWidth`/`getBounds` consumers during #7 |
| Global artifacts are 5.5× the disk/latency of regional (`docs/GLOBAL_MODEL_SIZING_SPIKE_2026-07-22.md`) | — | orthogonal, already tracked |

---

## 9. Files the spike touched

**New (all spike-only, safe to delete):**

- `frontend/src/lib/globe-spike.ts` — 297 lines. Flag, `readGlobeFrameProjection`,
  `buildGlobeMesh`, `GLOBE_SPIKE_VERTEX_SOURCE`.
- `frontend/tests/e2e/globe-spike.spec.ts` — 642 lines, scratch spec, 25 tests, no
  goldens, no `toHaveScreenshot`. **Note: `.gitignore:31` (`frontend/tests/e2e/*`)
  ignores it by default** — it needs an explicit allowlist entry if it is ever
  promoted.
- `frontend/globe-spike-artifacts/` — 16 PNGs + `findings.json`. Untracked. (Not
  under `test-results/`: Playwright wipes per-test output dirs mid-run and silently
  deleted the first batch.) A line `frontend/globe-spike-artifacts` appeared in
  `.gitignore:60` during the spike — **not written by me**; `.gitignore` was already
  dirty from a concurrent session and was left alone, but that one line belongs to
  this spike and should be removed with the directory.
- `docs/GLOBE_SPIKE_2026-08-01.md` — this file.

**Modified (all changes marked `[GLOBE SPIKE]`, 15 markers + 3 markers):**

- `frontend/src/lib/grid-webgl.ts`
  - import block
  - 10 new private fields (globe program / bindings / buffers / mesh signature)
  - `const fragmentSource = \`…\`` → `const buildFragmentSource = (globeVariant) => \`…\``
    with two interpolations that collapse to the previous text when `false`
  - the `this.bindings = {…}` literal → a `buildBindings(program)` builder
    (mechanical `this.program` → `program`; this is most of the diff's line count)
  - globe program creation + `uploadGlobeMeshIfNeeded()`
  - `render(matrix)` → `render(matrix, globe)`; program/bindings/buffer selection;
    globe uniforms; the draw branch
  - `globeSpikeStats` export + `window.__cartoskyGlobeSpike` seam
  - disposal of the globe resources
  - **one behaviour-bearing line outside a flag guard:** `globeSpikeStats.mercatorQuadDraws += 1`
    inside the mercator draw loop. An integer increment; it cannot change pixels
    (goldens confirm) but it is the only unguarded edit.
- `frontend/src/components/map-canvas.tsx` — import, `__cartoskyGlobeSpikeMap`
  handle, and `map.setProjection({type:'globe'})` inside the existing `on("load")`
  handler. (`setProjection` before `load` throws *"Style is not done loading"* and
  killed the whole map-init effect — worth knowing for production.)

**Not modified, as required:** backend, the coherence guard, capture paths,
Coverage/domain logic. `frontend/tests/e2e/render-golden-baseline.timing.json` was
rewritten by a verification run and restored with `git checkout`.

**Nothing was committed.**

### Reproducing

```bash
cd frontend
npx playwright test globe-spike --project=chromium          # 25 tests, ~3 min
npx playwright test render-golden-baseline --project=chromium  # flag-off parity
# manual: npm run dev, then /viewer?...&globe=1&globeMesh=16x8
```
