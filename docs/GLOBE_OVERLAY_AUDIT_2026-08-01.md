# Globe overlay audit — every viewer overlay on the globe-spike prototype

**Date:** 2026-08-01 · **Status:** audit only. Nothing fixed, nothing committed, working tree clean.
**Scope:** closes caveat 3 of `docs/GLOBE_SPIKE_2026-08-01.md` §7 ("everything that is
not the grid layer is unaudited") and de-risks item 7 of its production plan.

**Headline:** the overlays are in much better shape than the spike feared. Nine of ten
matrix items work or need only cosmetic work. **One item is a genuine product bug
(hover pins to the horizon), one is a genuine composition problem (share export around
a disc), and one is a camera defect that was not previously known (you cannot frame
either pole).** Total added cost: **+3.0 days** against the spike's 11.75-day plan,
of which 1.25 d is already inside the spike's existing line items.

Evidence: `frontend/globe-spike-artifacts/g*.png` (51 screenshots) and
`frontend/globe-spike-artifacts/overlay-audit.json` (raw probe output).

---

## Method

Two things made this cheap and are worth reusing.

1. **Real prod data, not fixtures.** A dev server with
   `CARTOSKY_DEV_PROXY_TARGET=https://api.cartosky.com` on port 4173, driven by
   Playwright with `PLAYWRIGHT_USE_EXISTING_SERVER=1`. The golden-baseline fixtures
   stub warnings to `[]`, boundaries to empty MVTs and the basemap to a transparent
   PNG, so they cannot answer overlay questions at all. Real data gave live NWS
   warning polygons, the real boundary/coastline vector tiles, and the real city
   label set.
2. **The mercator control arm needs no source change.** `__cartoskyGlobeSpikeMap` is
   only published under `?globe=1`, so every A/B arm loads *with* the flag and the
   mercator arm is produced by calling `map.setProjection({type:'mercator'})` from the
   test — the same trick the spike's own perf A/B used. Same page, same controller,
   same handle.

Scratch spec: `frontend/tests/e2e/globe-overlay-audit.spec.ts` (gitignored by
`.gitignore:31`, `frontend/tests/e2e/*`). No `toHaveScreenshot()`, no goldens.

```bash
cd frontend
CARTOSKY_DEV_PROXY_TARGET=https://api.cartosky.com npm run dev -- --host 127.0.0.1 --port 4173 &
PLAYWRIGHT_USE_EXISTING_SERVER=1 npx playwright test globe-overlay-audit --project=chromium
```

### One geometry fact that explains most of the results

In globe projection the sphere's circumference equals the mercator world width, so the
disc radius is `worldSize / 2π`. Measured on a 1128 × 800 canvas:

| zoom | disc radius | disc vs canvas |
|---|---|---|
| 1 | 326 px | small disc, huge dead background |
| 2 | 652 px | disc fits, limb visible all round |
| 3 | 652 px (lat 25) | limb visible left/right |
| 4 | **1304 px** | **disc exceeds the canvas — no limb on screen** |

**Above ~z3.5 the globe fills the viewport and there is no limb and no far side.**
Several overlays are safe only because of this, which is worth knowing before anyone
changes the zoom band the globe is offered in.

---

## Verdict table

| # | Item | Status | Fix est. | Mechanism |
|---|---|---|---|---|
| 1 | City value labels (+ anchor chips) | **Works** — latent hazard | 0.5 d | `queryVisibleCityPoints()` culls with `getBounds().contains()` + a manual `map.project()` collision loop, and the symbol layers set `text/icon-allow-overlap` **and** `-ignore-placement` so MapLibre does no placement of its own. Both are flat-viewport assumptions. They never bite today because the rank-by-zoom threshold yields **0 labels at z ≤ 3** and by z4 the disc exceeds the canvas. Guard `getBounds()` + `isLocationOccluded()` before the band is widened. |
| 2 | NWS warnings (vector polygons) | **Works** | 0 d | `twf-vectors-*` are ordinary MapLibre fill/line layers; MapLibre projects them on the sphere itself. z4 globe renders 56 fills, byte-for-byte the same count as the mercator control; a point query at a polygon returns 1 in both. Curvature correct at z2. |
| 3a | Contours — raster (shader) | **Works** | 0 d | Already proven by the spike: the globe program shares `buildFragmentSource()` verbatim, contour sampling included. |
| 3b | Contours — vector (`twf-contours`) | **Dormant, inherit** | 0 d | Every prod `grid-manifest` probed returns `contours: {}` (gfs prmsl, gfs hgt500, ecmwf prmsl, nbm tmp2m) and the source carried 0 features in every run — the layer is not populated in production today. Structurally it is a GeoJSON line + `symbol-placement: line` label layer, i.e. the same class as the warnings layers that are verified working. |
| 3c | Boundaries / coastline / roads | **Works** | 0 d | Vector-tile layers off `twf-boundaries` / `twf-roads`. Measured 354 coastline + 128 state + 9 country features rendered on the globe, visually correct curvature at every zoom tested. |
| 4 | Hover sampling + tooltip near the limb | **BROKEN** | 0.5 d | `map.unproject()` clamps to the nearest horizon point outside the visible cap, and `use-sample-tooltip.ts` has no occlusion guard — so the readout **pins to the horizon value and keeps rendering far outside the disc, over empty background**. |
| 5 | Compare mode (two panes) | **Works** — recommend out of v1 | 1.0 d if in scope | `?globe=1` reaches both panes (the `on("load")` handler is per-`MapCanvas`); both panes go spherical and the existing `move` → `jumpTo` sync keeps them identical through a drag. Cosmetic: each pane is ~700 px so each disc is clipped at the divider — two half-globes. |
| 6 | Skew-T map-click pin + panel | **Works** — cosmetic | 0.25 d | `maplibregl.Marker` with `anchor:"center"`. Marker centre matched `map.project()` within 1–2 px at z4 and z1.5. On the far side MapLibre auto-fades it to `opacity: 0.2` and `isLocationOccluded` is true — but 0.2 is still legible, so the pin ghosts through the earth. Panel unaffected. |
| 7 | Timeline scrub + playback | **Works, clean** | 0 d | Jump-to-FH across 3/6/12/24/6 and 12 s of playback: `drewMismatched = 0`, `held = 0`, `mercatorQuadDraws = 0` throughout, `coherentDraws` climbing monotonically. A Coverage NA→Global swap on the globe produced `held = 3, drewMismatched = 0` — the guard held and released correctly through a domain change. |
| 8a | Share — live-canvas capture | **Works** | 0 d | 407 ms, 1112 × 900, 1232 distinct colours, non-blank. |
| 8b | Share — GIF frame driver | **Works** | 0 d | `__cartoskyGifDriver` exposes all 12 methods on the globe; `showFrame()` + `captureFrame(640, fh)` succeeded for fh 0/6/12. |
| 8c | Share — **composition** | **BROKEN (cosmetic-major)** | 1.0 d | `composeShareFrame()` sizes chrome for a full-bleed map. The composed image is a ~200 px disc adrift in a 1112 × 900 rectangle with a full-width legend bar and a corner title chip. |
| 8d | Server-side screenshot | **Flat — acceptable v1, must be stated** | 0 d (v1) | No projection param exists anywhere in `screenshot_service.py` or `screenshotUrlForState()`, so the server render is always mercator. Consequence: a shared Link/OG image does not match what the sharer saw. |
| 9 | Mobile viewport + touch | **Works** | 0.25 d | 390 × 844 → canvas 390 × 644, disc radius 326 px, so the sphere is 652 px wide and horizontally cropped. Drag rotated the globe (lng −70 → −48.9); `dragRotate` false, `touchPitch` false, `touchZoomRotate` true. Mobile chrome overlays the disc sanely. |
| 10a | Coverage = Global on the globe | **Works — the flagship view** | 0 d | The full sphere is covered edge to edge, poles included. This is the single best argument for the feature. |
| 10b | Regional 3857 artifact on the globe | **Works** | 0 d | GFS NA and HRRR CONUS render with a correctly bowed footprint edge on the sphere. |
| 10c | World camera preset + polar camera | **BROKEN (camera)** | 1.0 d | `reg=world` did not retarget the camera (stayed at the NA centre, z 2.4). Worse: `jumpTo({center:[0,88]})` is **hard-clamped to lat 85.051** (the mercator limit) and, because the globe camera inherits mercator's latitude-dependent scale, at lat 85 / z 0.6 the view magnifies ~11× and the disc leaves the screen entirely. **You cannot frame either pole.** |

**Total added cost: +3.0 d.** Of that, 1.25 d (items 6, 9, 10c) belongs inside the
spike's existing plan item 6 "Camera + interaction"; 1.5 d (items 4, 8c) belongs
inside its items 8 and 9. **Genuinely new work not already budgeted: +0.5 d** (item 1's
occlusion guard), plus the spike's own 2.0-day item 7 "Overlay audit" is now spent and
can be struck. Net effect on the 11.75-day plan: **≈ 10.25 days**, i.e. the audit
*reduced* the estimate.

---

## The three findings that matter

### 1. Hover pins to the horizon (item 4) — the only true bug

Measured on Coverage = Global, camera lat 20 / lon 0 / z1, sweeping the cursor along the
horizontal radius (`10b_global_limb_hover`, `04_limb_probes`):

| distance from disc centre | tooltip | `unproject`→`project` round-trip | `isLocationOccluded` |
|---|---|---|---|
| 0 (sub-camera) | 103.7 °F | 0.00 px | false |
| 0.5 R | 100.5 °F | 0.00 px | false |
| 0.8 R | 76.9 °F | 0.94 px | false |
| 0.9 R | 82.7 °F | — | false |
| **1.05 R** | **83.2 °F** | 43 px | true |
| **1.4 R** | **83.2 °F** | 245 px | true |

`g10b-global-limb-hover-full.png` shows it plainly: an `83.2 °F` chip floating in empty
grey background, well clear of the globe. The value is real — it is the horizon point's
value — which makes it worse than a blank, because it is indistinguishable from a
legitimate reading.

Two implementation notes for whoever fixes it:

- **`isLocationOccluded()` alone is a slightly loose gate.** It flips true at ~0.98 R,
  but `unproject` has already begun clamping by 0.8 R (0.94 px round-trip error, and the
  0.85/0.98/1.15/1.6 probes in test 04 all returned the *identical* lng/lat). A tight
  gate is `isLocationOccluded() || roundTripError > ~1 px`.
- **Product recommendation: suppress the readout outside the visible cap.** Blank is
  correct here. Pinning is the one behaviour that cannot be distinguished from a real
  value, and the "no data" path already exists in `use-sample-tooltip.ts:91-101` — it
  fired correctly over ocean outside the NA footprint during test 04, so the plumbing is
  there and only needs a second reason to blank.

### 2. Share composition around a disc (item 8c)

`g08-share-modal-globe-full.png`. Capture and the GIF driver both work unchanged — the
spike was right about that — but `composeShareFrame()` (`screenshot_export.ts:1098-1145`)
lays out background → map cover → overlay card → logo → legend on the assumption the map
fills the frame. With a disc, the result is a small planet in a large empty rectangle
with a full-bleed legend bar underneath it. Cheapest fix is to crop/letterbox the
composed frame to the disc's bounding box before the chrome pass, which also makes the
0.72 chrome scale factor mean what it means today.

Note also **8d**: because the server-side screenshot has no projection param, the Link
tab and any OG image render mercator while the user is looking at a globe. That is a
defensible v1 position, but it should be a stated decision, not a surprise.

### 3. You cannot look at the poles (item 10c) — newly discovered

The spike showed the poles *render* correctly (`b3`/`b4`, the degenerate polar fan). It
did not check whether the camera can be pointed at them. It cannot: `map.jumpTo` clamps
centre latitude to ±85.051° — the Web Mercator limit — even under globe projection, and
the globe camera inherits mercator's `1/cos(lat)` scale, so approaching the clamp
magnifies the view ~11× and pushes the disc off screen
(`g10b-coverage-global-northpole-full.png`). A polar view therefore needs explicit
camera work, not just a preset. This belongs with the spike's plan item 6 and is the
main reason that item should not be trimmed.

---

## Recommended v1 scope

**Ships on the globe:**

- The grid layer (both 4326 global and 3857 regional artifacts) — proven by the spike,
  re-confirmed here on real data.
- All vector overlays: NWS warnings, boundaries, coastline, roads, lake mask, and the
  (currently dormant) vector contour layer. Zero work.
- City value labels. Zero work *at the shipped zoom band*; +0.5 d for the occlusion
  guard if the band is ever widened below ~z3.5.
- Timeline scrubbing and playback. Zero work; the coherence guard is projection-agnostic
  as the spike predicted and as re-verified through a live domain swap.
- Skew-T pins and panel. +0.25 d to take the far-side marker to opacity 0.
- Mobile. +0.25 d for a zoom floor that fits the disc to the narrow viewport.
- Live-canvas capture and GIF export **of the map itself**.

**Flattens or hides in v1, with rationale:**

- **Compare mode → no globe.** Two clipped half-discs in ~700 px panes is worse than two
  flat maps at every zoom, and compare's whole value is pixel-comparable panes. Note this
  is a *decision*, not a limitation: the flag already reaches both panes and they sync
  correctly, so it is a one-line gate today and can be revisited. **Say so in the ticket
  rather than letting it look like an oversight.**
- **Server-side screenshot → stays mercator.** No projection param exists; adding one
  touches the backend, which this audit was scoped out of. Accept for v1 and put it in
  the release note.
- **Share composition → gate the globe out of export, or spend the 1.0 d.** Shipping the
  globe with today's composer produces a bad artefact on the most public surface the
  product has. Pick one; do not ship both untouched.
- **Hover readout → suppressed outside the visible cap.** Not optional; see above.
- **Polar cameras → not offered in v1.** No preset should promise a pole view until the
  camera work in plan item 6 lands.

---

## Top 3 risks

1. **The city-label safety is accidental, not designed.** Labels are safe on the globe
   only because the rank-by-zoom threshold and the disc-exceeds-canvas threshold happen to
   straddle the same zoom. Any change to the label rank ladder, the globe zoom band, or the
   canvas aspect ratio breaks it silently — far-side labels will simply start appearing,
   with no error, because `-ignore-placement: true` means MapLibre will not cull them for
   us. **Mitigation:** land the `isLocationOccluded()` guard in v1 even though it is a
   no-op today, and add a regression test that asserts zero occluded labels at z2–z3.
2. **The share surface is the one users judge.** Capture works, which makes the
   composition problem easy to under-price — the modal renders, the download succeeds, and
   the output is just bad. **Mitigation:** treat 8c as a ship gate, not polish, and decide
   the server-screenshot mismatch (8d) explicitly.
3. **Camera work is bigger than the spike's 1.5 d.** Plan item 6 was written before the
   latitude clamp and the `1/cos(lat)` magnification were known, and it also has to absorb
   the mobile disc-fit and the world preset not retargeting. **Mitigation:** re-scope item 6
   first, before any ship date is quoted; it is now the largest single unknown in the plan,
   having replaced the overlay audit in that role.

---

## Files touched

**None.** The working tree is clean; nothing was committed.

- `frontend/tests/e2e/globe-overlay-audit.spec.ts` — new scratch spec, ~700 lines,
  15 tests. Ignored by `.gitignore:31`. Delete with the spike.
- `frontend/globe-spike-artifacts/g*.png` (51) + `overlay-audit.json` — untracked;
  the directory is already ignored (`.gitignore:60`).
- `docs/GLOBE_OVERLAY_AUDIT_2026-08-01.md` — this file.

No spike-flag plumbing turned out to be necessary: the mercator-control problem was
solved from the test side with `setProjection`, so `frontend/src/lib/globe-spike.ts`,
`grid-webgl.ts` and `map-canvas.tsx` are untouched. Backend, Coverage/domain logic and
the coherence guard were not modified, as required.

### Screenshot index (selected)

| file | shows |
|---|---|
| `g10b-coverage-global-globe-full.png` | Coverage = Global on the globe — full sphere, the flagship view |
| `g10b-global-limb-hover-full.png` | **the hover bug**: `83.2 °F` floating outside the disc |
| `g08-share-modal-globe-full.png` | **the composition problem**: small disc in a large empty frame |
| `g10b-coverage-global-northpole-full.png` | **the camera clamp**: lat 88 → 85.051, ~11× magnification, disc gone |
| `g02b-warnings-globe-conus.png` | live NWS warning polygons on the globe at z4 |
| `g02c-warnings-globe-z2-full.png` | the same warnings curved on a z2 disc |
| `g03-mslp-conus-globe-full.png` | city value labels on the globe at z4 (disc fills canvas) |
| `g01d-cities-globe-z3-full.png` | z3: limb visible, **zero** labels — why the hazard is latent |
| `g05b-compare-globe-z2-after-drag-full.png` | compare: two synced globes, each clipped at the divider |
| `g06b-sounding-hrrr-globe-farside-full.png` | Skew-T pin ghosting through the earth at opacity 0.2 |
| `g09-mobile-globe-before-full.png` | 390 × 844 mobile globe, disc cropped horizontally |
| `g00-smoke-globe-full.png` | GFS NA (3857) regional artifact curved on the sphere at z1 |
