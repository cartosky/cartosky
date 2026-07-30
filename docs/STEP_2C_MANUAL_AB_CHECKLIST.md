# Step 2c — manual A/B check on real hardware (operator)

Purpose: SwiftShader proved the logic; this checks driver behavior. GLSL
`exp`/`atan` precision is implementation-defined, and adding a uniform
branch can change register allocation in the untouched EPSG:3857 path on
some drivers. The bar is **"visually indistinguishable," not bit-identity.**

## Setup

- A/B toggle: the renderer change lives only in
  `frontend/src/lib/grid-webgl.ts` (uncommitted). With the dev server
  running:

```bash
cd /Users/brianaustin/cartosky && git stash push -- frontend/src/lib/grid-webgl.ts
```

  = **BEFORE** (HMR reloads in a second or two);

```bash
cd /Users/brianaustin/cartosky && git stash pop
```

  = **AFTER**. Verify which state you are in with `git status --short --
  frontend/src/lib/grid-webgl.ts` (modified = AFTER).
- Mac: Chrome AND Safari if you have five extra minutes — different GL
  stacks (ANGLE-Metal vs WebKit). Phone: hit the dev server over LAN
  (`vite --host`, port 5174, the frontend-prod-proxy config so real
  canonical prod data renders).
- All three URLs are canonical CONUS with real data — the EPSG:3857 path,
  i.e. the branch-NOT-taken side. That is the point: 2c is checking the
  untouched path on real drivers.

## The three cases

1. GFS tmp2m: `/viewer?m=gfs&v=tmp2m&reg=conus&fh=12`
2. HRRR ptype: `/viewer?m=hrrr&v=radar_ptype&reg=conus&fh=6` (pick an fh
   with weather if 6 is dry)
3. MRMS reflectivity: `/viewer?m=mrms&v=reflectivity&reg=conus` (observed —
   note the frame TIME so before/after compare the same frame; MRMS
   advances quickly, so do the A/B toggle within a minute or pin `fh`)

## What to look at, per case (before → after)

1. **Geographic registration (the v-mapping leak detector).** Pick one
   field edge anchored to a landmark near the TOP of CONUS (~49°N — a
   radar echo edge or temperature gradient against the Canadian border)
   and one near the BOTTOM (~25°N — Florida coastline). A v-mapping error
   leaking into the 3857 path displaces data NORTH-SOUTH, worst at the
   viewport's top/bottom edges and invisible mid-screen.
   **FAIL: any perceptible N-S shift of data relative to basemap
   geography at zoom ≥ 5.**
2. **Gradient quality (precision).** On tmp2m, zoom to a smooth strong
   gradient (morning coastal or frontal boundary), z 6-8.
   **FAIL: new horizontal banding, stair-step contours, or posterization
   that was not there before.**
3. **Category boundaries (ptype packed branch).** Rain/snow/ice
   transition lines. **FAIL: color bleed or fringing between categories,
   or boundary pixels flickering while panning.**
4. **Sparse edges (MRMS edge-fade branch).** Radar echo edges.
   **FAIL: soft fades replaced by hard texel boxes, or halos around
   echoes.**
5. **Temporal stability.** Scrub 5-6 forecast hours, then pan/zoom
   continuously for ~10 s. **FAIL: shimmer/speckle in the field while the
   camera moves, frame-rate obviously worse than BEFORE, or flicker
   between frames.** (Phone especially — this is where mediump precision
   and register pressure would show.)

## Explicitly NOT a fail

- City-label LAYOUT differences between loads (which labels appear, where
  chips sit). Label collision order is load-order-dependent — a known,
  pre-existing nondeterminism, unrelated to this change.
- Different weather between A and B on MRMS if the observed frame
  advanced — re-check with a pinned frame before calling it a diff.
- Anything you can only find with a screenshot-diff tool. The bar is
  visually indistinguishable at normal viewing.

## Reporting back

Per device/browser: PASS, or the case number + what you saw + a
screenshot pair. Any single FAIL = stop; renderer change does not proceed
to Step 3.
