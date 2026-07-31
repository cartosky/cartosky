# Global domain artifact contract — native EPSG:4326 (v1)

Status: **draft for Phase 1 gate review** — 2026-07-30.
Companion docs: `GLOBAL_STORAGE_G1_INVESTIGATION_2026-07-29.md` (measurements
and rationale), `MAX_WEEK_EXECUTION_PLAN_2026-07-27.md` (policy amendment
retracting the uniform-grid lines).

This contract governs every artifact published under a `domains/{d}` route
whose domain declares a native-geographic grid. First (and currently only)
adopter: the GFS `global` domain. Canonical artifacts are untouched.

---

## 1. Grid definition

| Property | Value |
|---|---|
| CRS | `EPSG:4326` |
| Registration | **point-registered** at 0.25° multiples (source-faithful) |
| Dimensions | **1440 × 721** (cols × rows) |
| Column centers | lon = −180.0 + 0.25·col, col ∈ [0, 1439] → −180.0 … +179.75 |
| Row centers | lat = 90.0 − 0.25·row, row ∈ [0, 720] → +90.0 … −90.0 (north-up) |
| Affine / bbox (cell edges) | **(−180.125, −90.125, 179.875, 90.125)**, pixel 0.25° × 0.25° |
| Frame payload | uint16 packed, per-var scale/offset unchanged → 1,038,240 cells, 2,076,480 bytes (1.98 MiB) |

- The bbox follows the raster (cell-edge) convention used everywhere else in
  the pipeline; the affine transform is
  `Affine(0.25, 0, −180.125, 0, −0.25, 90.125)`. Consumers converting
  lon/lat ↔ row/col MUST go through this transform (or the equivalent
  center formulas above), never through mercator meters.
- **Both poles are literal grid rows** (row 0 = +90, row 720 = −90). Pole
  rows carry the source model's values as published; no synthesis.
- The bbox lat edges (±90.125°) overhang the physical poles by half a cell.
  This is a registration artifact, not data coverage; consumers clamp
  display to ±90 (the mercator viewer clips far earlier, §3).

## 2. Longitude layout and the seam

- Source GFS longitudes (0 … 359.75) are **rolled** to −180 … +179.75. The
  roll is index arithmetic only — no interpolation, no reprojection.
- **No duplicate wrap column in v1.** Column 0 (center −180.0) is the seam
  column; its western half-cell spans the antimeridian. The cell-edge bbox
  therefore overhangs the west world edge by 0.125° and stops 0.125° short
  of +180 on the east. Seam rendering (world copies, wrap treatment) is
  Phase 3 work and is specified against exactly this geometry.

## 3. Coverage bounds (the explicit contract)

- **Data coverage:** all longitudes (full 360° wrap), latitudes −90 … +90
  inclusive.
- **Mercator display coverage:** the flat-map viewer clips at ±85.05113°
  (`MERCATOR_MAX_LATITUDE_DEG`). This is a display limitation of the
  projection, not missing data; the rows exist in the artifact and are
  sampleable.
- **Sampling:** values at grid points are exact source values (packing
  quantization only, ±scale/2). No bilinear-warp error term exists on this
  path.
- **lon = +180 special case:** the API accepts lon ∈ [−180, +180]. +180 is
  not a column center; the sampler's EPSG:4326 branch MUST wrap it to the
  −180 column (same physical meridian). General unwrapped-longitude
  normalization (±181 from world copies) remains Phase 3.
- **Renderer:** draws a single world copy in v1; the data layer ends at the
  seam when panning. World-copy replication is Phase 3.

## 4. Projection declaration

- Every binary frame's metadata and every grid manifest carries
  `projection: "EPSG:4326"` for grids under this contract.
- **Absence of the field means `EPSG:3857`** (legacy default,
  `GRID_PROJECTION`). Consumers MUST branch on the declared value and MUST
  NOT infer projection from model, domain, bbox magnitude, or route shape.
- Heterogeneous projections across domains of the same model are expected
  and legal (GFS canonical `na` stays 3857; GFS `global` is 4326).

## 5. Pipeline semantics

- **No reprojection on the write path.** Build = longitude roll + uint16
  packing. The bilinear warp step used by 3857 artifacts does not run.
- Derived variables computed on the target grid inherit the 4326 grid; any
  derived path that reprojects intermediate rasters must target the
  domain's declared CRS, not a hardcoded 3857.
- **Contours:** generated from the 4326 value grid; GeoJSON output remains
  EPSG:4326, features split at the antimeridian, coordinates within
  [−180, 180] — same pins as the G1 contour suite. Because the value grid's
  cell-edge bbox overhangs the world by half a cell (§2), contour vertices
  falling in the overhang are clamped onto ±180; this introduces up to
  0.125° (~13.9 km at the equator) of positional distortion on
  seam-column vertices only. Mercator contour output is never rewritten.
- Companion / display-prep rasters follow the grid's declared projection.

## 6. Explicitly unchanged

- Canonical domains: EPSG:3857, 25 km, all existing routes and formats.
- Anomaly variables: partially inverted by Phase 3A Wave 1 (2026-07-30,
  operator decision D2) — hgt500_anom / tmp2m_anom / tmp850_anom declare
  global against native 4326 ERA5 baselines; the four precip-window
  anomalies remain canonical-only until Wave 2 (streaming baseline build).
- Camera never selects resolution; one grid per (model, domain).
- Canonical artifact routes never accept `domain=`.
- `CARTOSKY_GLOBAL_DOMAIN_MODELS` default: unset. Nothing serves until the
  flag is flipped.

## 7. Size

1.98 MiB per **base-grid** frame (uint16). Display-prep variables upscale
×3 (4320 × 2163, ~17.8 MiB/frame) — same ×3 factor as their 3857
counterparts, so the areal ratio applies uniformly. Projected full GFS
global footprint ≈ **161 GiB** including companion/display-prep share
(vs ≈ 391 GiB under the retracted 3857 plan). See investigation §3 for the
measured derivation.
