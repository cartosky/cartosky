# G1 render/sampling state + global storage-format investigation — 2026-07-29

Investigation report only. No code changed, nothing published, no caps
touched. Phase 3 GFS (dark) is committed and pushed as `f0c92b30` before any
of this. Line numbers cite that commit's tree.

---

## 1. G1 antimeridian — what is actually done and what is not

Honest summary first: **the backend halves of G1 (warp, contours, sampling at
in-range longitudes) are done and test-pinned. The frontend rendering half
(1a, 1b) and unwrapped-longitude sampling (the ±181 case in 1c) are NOT
handled — nothing draws world copies, and unwrapped longitudes are rejected,
not normalized.** The Phase 3 completion notes said the "WebGL/MapLibre
rendering half" remained a prod-gate risk; the precise state is:

### 1a. World copies — NOT handled

`frontend/src/lib/grid-webgl.ts` draws exactly one 4-vertex quad:
`buildQuadVertices` (grid-webgl.ts:121-133) maps the artifact bbox into
single-world mercator unit coordinates, and the custom layer's render hook
uploads one matrix — `args.defaultProjectionData.mainMatrix` — and issues one
draw (grid-webgl.ts:984-991, 2396). MapLibre custom layers are responsible
for their own world-copy handling; tile (basemap) layers repeat, custom
layers do not. **The data layer stops dead at the seam**: panning across the
dateline shows repeated basemap with no weather on any world copy other than
the primary. This is pre-existing behavior (visible today if you pan an NA
product far west), but a global product makes it a first-class defect.

### 1b. Texture edges / wrap column — NOT handled

- **No duplicated wrap column exists in the artifact.** The global grid is
  1604 columns spanning ±20 050 000 m (snap-outward from ±20 037 508.34;
  measured, §3). Edge-column centers sit at ≈±179.888°; the outer half of
  col 0 and col 1603 extends ≈12.5 km past the mercator world edge (to
  ≈±180.112° equivalent). GDAL fills those from the 0–360 source across the
  seam (`test_warp_leaves_no_seam_gap` — no nodata on the seam band), so the
  data is continuous, but no column is a duplicate of the opposite edge.
- **Texcoords are the hardcoded outer square** `0..1`
  (`buildQuadTexCoords`, grid-webgl.ts:135-142), not texel centers, with
  `CLAMP_TO_EDGE` on S and T (grid-webgl.ts:1773-1774, 1838-1839). The
  shader's own 4-texel manual bilinear (`sampleBilinear`,
  grid-webgl.ts:1345+) clamps its neighbor fetches at the edge, so the last
  half-texel renders as a clamp-smear of the outermost column.
- Net for a global artifact: the quad overhangs the world edge by ~12.5 km,
  the final half-texel is smeared, and there is no abutting copy on the
  other side (per 1a). "Adjacent copies abut" is moot until copies are drawn
  at all; when they are, the current 0..1 texcoords would make copies
  overlap by the overhang rather than abut.

### 1c. Sampling longitude normalization — PARTIAL

- `_sample_binary_frame_index` (backend/app/services/sampling.py:154-163)
  applies no longitude wrapping: lon goes straight into the pyproj
  transformer, and `read_binary_sample_value` (sampling.py:181+) returns
  `(None, True)` for out-of-range rows/cols.
- **In-range is tested and correct**: the G1 suite samples ±179, ±179.9,
  ±179.99, and exactly ±180.0 through the real HTTP route
  (`backend/tests/test_gfs_global_antimeridian.py`), including the pin that
  lon −180 → col 0 and lon +180 → col 1603. Exactly ±180.0 lands in-bounds
  because the grid overhangs to ±20 050 000 m.
- **±181 (unwrapped world-copy lngLat) is rejected, not normalized, and
  untested.** The API contract forbids it before sampling is reached:
  `/api/v4/sample` declares `lon: float = Query(..., ge=-180, le=180)`
  (backend/app/main.py:5854-5855) → HTTP 422; `SampleBatchPointIn.lon` has
  the same bounds (main.py:2600-2601). No test exercises ±181 anywhere.
- **The client sends unwrapped coordinates.** The hover handler reads
  `e.lngLat` raw (frontend/src/components/map-canvas.tsx:3681); no `.wrap()`
  or normalization exists in `use-sample-tooltip.ts` or `api.ts` (grepped).
  MapLibre reports unwrapped longitudes on world copies, so hover-sampling
  in a second world copy will 422 and the tooltip silently disappears
  (the catch at use-sample-tooltip.ts:180-187 swallows it). City-value
  batch points come from anchor GeoJSON (canonical −180..180 coords), so
  batch is safe today.

### 1d. Contours — backend DONE, edge cache rules UNVERIFIED

- Generation cannot emit out-of-range coordinates in the tested
  configuration: the G1 contour suite runs the real
  `build_iso_contour_geojson` (backend/app/services/builder/pipeline.py:996-
  1075, which shells to `gdalwarp -t_srs EPSG:4326` + `gdal_contour`) on a
  seam-straddling synthetic feature and pins: every coordinate within
  [−180, 180]; max single-feature longitudinal span 28.3° (threshold 350°);
  the seam feature present as two pieces terminating exactly on the
  dateline; a control ring closed. No globe-spanning artifact was observed —
  GDAL splits at the seam.
- **The `/contours/` cache rules for the `domains/{d}` prefix cannot be
  verified from this repo.** The live nginx server block and Cloudflare
  rules are not checked in (the only nginx files in `deployment/nginx/` are
  the grafana vhost and the grid-accel internal-location example, which
  covers serving, not cache headers). Canonical contour URLs are
  `/api/v4/{model}/.../contours/{key}`; domain URLs are
  `/api/v4/domains/{d}/{model}/.../contours/{key}` — a regex rule on
  `/contours/` covers both, a prefix rule does not. This is runbook §3's
  explicit check; **status: unverified until you run it on prod.**

---

## 2. Has anything been published yet?

**Mac: no.** No `domains/` directory exists anywhere under the repo's data
roots (searched `/Users/brianaustin/cartosky/data` and
`backend/data`): staging holds one stale canonical GFS run
(`backend/data/staging/gfs/20260715_00z`), published (`data/v3/published`)
holds only `hrrr`. Zero global artifacts.

**Prod: cannot contain Phase 3 artifacts — with one caveat you should
confirm.** The Phase 3 code first reached origin today (`f0c92b30`, not yet
pulled on prod) and `CARTOSKY_GLOBAL_DOMAIN_MODELS` has never been set, so
nothing can exist under `published/gfs/domains/` or `staging/gfs/domains/`.
The caveat is the July 22 sizing spike: it built 55.8 GiB of global GFS
artifacts on prod through an in-memory region, outside the published tree
(reports persist under `/opt/cartosky-dev/reports/`). If its converted
outputs were not cleaned up, they are spike leftovers, not published
artifacts — but they would confuse a disk checkpoint. Please run:

```bash
ls -d /opt/cartosky/data/published/gfs/domains /opt/cartosky/data/staging/gfs/domains 2>/dev/null; sudo du -sh /opt/cartosky-dev/* 2>/dev/null | sort -rh | head -15
```

Expected: both `domains` paths absent; the `du` shows whether spike output
still occupies disk. **Conclusion: Change A is a format decision, not a
re-publish** — nothing real has been published on the Phase 2A layout.

## 3. Storage format — measured, EPSG:3857 warp vs native EPSG:4326

All numbers measured by running the repo's own
`get_grid_params`/`compute_transform_and_shape` for `("gfs", "global")` at
commit `f0c92b30` (not estimated):

| | EPSG:3857 (current) | EPSG:4326 native 0.25° | ratio |
|---|---|---|---|
| Dimensions | **1604 × 1604** | 1440 × 721 | |
| Cells | **2 572 816** | 1 038 240 | **2.478×** |
| Per-frame bytes (uint16) | **5 145 632 (4.91 MiB)** | 2 076 480 (1.98 MiB) | 2.478× |
| Latitude coverage | ±85.051° (see below) | **±90° (full poles)** | |

- **Projected GFS global total:** 390.7 GiB ÷ 2.478 = **157.7 GiB** on pure
  cell-count scaling; **≈161 GiB** refined for the ~2–4% of the converted
  set that is contour GeoJSON (scales with the linear ratio √2.478 ≈ 1.574,
  not the areal one; composition per the sizing spike §2). Companion/
  display-prep rasters (~18–23%) scale with cells like the grid binaries.
- **Your hypothesis is CONFIRMED: ~2.4× (measured 2.478×), ~390 → ~158–161
  GiB, plus full pole coverage in storage.** One correction to the framing:
  full pole coverage applies to the *artifact* (sampling, meteograms, any
  future projection). The mercator *viewer* still cannot display poleward
  of ±85.05° — display coverage is unchanged by the format.
- **The exact latitude bound the current warp produces:** the snap-outward
  transform runs to ±20 050 000 m → outer-edge latitude **85.0608°**; the
  last row *center* sits at **85.0511°** (the mercator world edge is
  85.05113°). So the current artifact's real data coverage ends at
  ≈±85.05–85.06°, with rows near it oversampled ~11.6× in area relative to
  the 0.25° source (25 km pixels at a latitude where 0.25° of longitude is
  ~2.4 km E-W... equivalently: cos(85.05°) ≈ 0.0863).
- **Yes, the 4326 path eliminates a resampling step.** Current chain:
  0.25° 4326 source GRIB → `rasterio.warp.reproject`, default
  `resampling="bilinear"` (raster_grid.py:244, 281) → 25 km 3857 grid →
  uint16 packing. Native 4326 storage is a longitude roll (0–360 → ±180) +
  packing with **no reprojection**. Consequences for sampling fidelity:
  a sample at a source grid point returns the source value exactly (packing
  quantization only, ±scale/2); the G1 oracle's dominant error term — the
  12.5 km pixel-center offset (0.154 °F of its 0.293 °F tolerance) — shrinks
  to the 0.125° 4326 half-pixel, and the bilinear-warp slack term vanishes.
  Sampling gets strictly more faithful to the source.
- Bonus, not asked: `_sample_binary_frame_index` **already has a native
  EPSG:4326 branch** (sampling.py:155-158 — `projection == "EPSG:4326"`
  uses lon/lat directly), so the backend sampling side is largely
  format-ready. The writer side is not: `GRID_PROJECTION = "EPSG:3857"` is
  a module constant (backend/app/services/grid.py:30) baked into sidecars
  and manifests; making it per-region/per-domain is part of any format
  change.

**Flag — this contradicts a locked policy line.** The max-week plan's Phase 3
checklist requires "canonical and global manifests report the same 25 km
model grid," and the locked uniform-grid policy says domain never selects
resolution. Native-4326 global storage breaks both literally: global would
be 0.25° geographic (~27.8 km at the equator, anisotropic poleward) while
canonical NA stays 25 km mercator. I think the policy's *intent* (no
viewport-dependent resolution; one source-faithful grid per domain) survives,
but adopting Change A requires an explicit amendment to those two lines, not
a silent deviation.

## 4. Cheap render path — per-fragment inverse Gudermannian

**Viable, and cleaner than the question assumes.** Key facts from the
shader (grid-webgl.ts:1215-1400):

- The fragment shader already does **manual 4-texel bilinear**: the data
  texture is NEAREST-filtered, and `sampleBilinear` (1345-1364) fetches
  four neighbors, decodes each independently (`decodeSample`, 1272-1283 —
  this is the four-texel fetch around byte-wrap boundaries in the comment
  at 1339-1344), and interpolates in decoded value space. Everything is
  driven by a single `uv` input. **Remapping `uv.y` upstream of
  `sampleBilinear` composes with the decode path with zero interaction** —
  the byte-wrap protection is per-texel-fetch and doesn't care where uv
  came from.
- Mercator x is linear in longitude, so **u needs no remap at all** for an
  equirectangular texture. Only v changes.
- The vertex shader (1215-1224) currently forwards only `a_texCoord`. The
  smallest diff:
  1. Add a varying carrying the quad's mercator-unit position (or reuse:
     `v_texCoord` is affine in mercator position across the quad, so v can
     be derived from it plus the bbox — passing the position explicitly is
     clearer).
  2. New uniforms: `u_texProjection` (0 = 3857, 1 = 4326) and the
     artifact's north/south latitudes (or precomputed
     `gd_min`/`gd_span`).
  3. In `main()`, when `u_texProjection > 0.5`: reconstruct mercator y in
     radians-of-world, `lat = 2.0*atan(exp(y)) - PI/2` (one `exp` + one
     `atan` + a few mads), then `uv.y = (u_latNorth - lat) / u_latSpan`;
     pass `uv` into the existing `sampleBilinear`/`contourLineAlpha` calls
     unchanged.
- **Per-fragment cost:** one `exp`, one `atan`, ~4 mul/adds — trivial next
  to the existing 4 (data) + 4 (contour overlay, when enabled) texture
  fetches with per-texel decode. No measurable frame-time risk on the
  hardware class that already runs this shader; precision is fine under
  `highp` (`fragmentFloatPrecisionQualifier`, 1225 — mediump fallback
  devices would need a look, flagged not solved).
- **No separate compiled shader variant needed**: a uniform branch that is
  constant across the draw is effectively free on modern GPUs; a
  compile-time variant keyed on projection is available as a fallback if a
  mediump device misbehaves.
- **Per-frame selection is available where the question hoped**: each frame
  sidecar carries `projection` (that is exactly what
  `_sample_binary_frame_index` branches on), and the grid manifest carries
  it too — the renderer already holds the manifest
  (`this.manifest`) and sets per-manifest uniforms each draw
  (grid-webgl.ts:2396-2417), so `u_texProjection` slots into the existing
  uniform upload with no new plumbing.
- Two things the cheap path does NOT fix, so they stay on the G1 ledger:
  world copies (1a — a per-copy draw loop with x-offset matrices or
  offset quads is a separate, also-small change) and the CLAMP_TO_EDGE seam
  half-texel (1b — for a 4326 global artifact whose columns tile the full
  360°, REPEAT wrapping on S — or a one-column duplicate — becomes the
  correct seam treatment and composes with the copy loop).
- The contour overlay texture (`u_contourData`, e.g. heights-over-vorticity)
  is a second grid frame and needs the same v remap when its artifact is
  4326 — same uniform, same helper, covered by the diff shape above.

## Summary of corrections to your framing

1. Hypothesis §3 confirmed with numbers: 2.478×, 157.7 GiB pure /
   ≈161 GiB refined. You were right.
2. "Full pole coverage" is a storage/sampling win only; mercator display
   still ends at ±85.05°.
3. Native-4326 global storage contradicts two locked policy lines (same-grid
   checklist item; domain-never-selects-resolution) — adopt by amendment,
   not silently.
4. The seam question in 1b is really two defects (no copy drawing + edge
   clamp), and the current 3857 artifact *overhangs* the world edge by
   ~12.5 km per side — copies would overlap, not abut, until texcoords/bbox
   are made world-exact.
5. Backend sampling already has the 4326 branch; the format change's backend
   cost is concentrated in the writer (`GRID_PROJECTION` constant → per-
   region) and manifests, not the samplers.
