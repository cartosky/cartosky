# Phase 3 — GFS global at 25 km — implementation plan (2026-07-29)

Status: LOCKED. Implements the Phase 3 section of
`docs/MAX_WEEK_EXECUTION_PLAN_2026-07-27.md` for GFS only, on the Phase 2A
contract (activation shape prescribed at
`docs/PHASE_2A_DOMAIN_CONTRACT_DESIGN_2026-07-28.md` §202) with Phase 2B
frontend plumbing already landed (`37ae8daa`). Numbers from
`docs/GLOBAL_MODEL_SIZING_SPIKE_2026-07-22.md` §2 (GFS global: 25 km, 1604²,
55.82 GiB/run converted, 1 181 MB peak RSS, ~1.5 h wall).

**Deliverable shape: wired but DARK.** Everything lands behind a default-empty
env allowlist; zero behavior change until the flag flips on prod. The prod
gates (G2 Cloudflare, G3 perf signoff, G4 on real artifacts, disk checkpoint,
real-device mobile) run from the runbook in §7 and are operator work.

## 1. Global region registration (backend)

- `REGION_BBOX_3857["global"] = (-20037508.342789244, -20037508.342789244,
  20037508.342789244, 20037508.342789244)` and
  `REGION_BBOX_4326["global"] = (-180.0, -85.05112877980659, 180.0,
  85.05112877980659)` in `backend/app/services/builder/raster_grid.py:45-56`.
  Web-Mercator pole clipping at ±85.05° is the locked answer to the 2A note.
- `grid_meters_by_region["global"] = 25_000.0` in `GFS_CAPABILITIES`
  (`backend/app/models/gfs.py:1236-1264`). Grid shape is computed
  (`compute_transform_and_shape`), expected ≈1604².
- LOD chain: `grid_lod_specs(model, var)` is region-independent — the global
  grid reuses GFS's existing LOD config. Verify the produced LOD dims in tests;
  do not add a global-specific LOD entry unless a test proves the default chain
  degenerate.

## 2. Variable scope (G6 anomaly exclusion)

Every buildable GFS grid variable declares
`supported_build_regions=["na", "global"]` EXCEPT anomaly variables
(`derive="anomaly_departure"` / `*_anom` — gfs.py:405, 468, 495 et al.), which
declare nothing (canonical-only). Exclusion is by omission in the declaration
— not a runtime check — and is PINNED by a test asserting no GFS variable with
an anomaly derive strategy (or `_anom` suffix) has `"global"` in
`supported_build_regions`. A second guard asserts every declared build region
has grid params (`_grid_params_available`) so a typo cannot silently no-op.

## 3. Rollout control — dark flag

`CARTOSKY_GLOBAL_DOMAIN_MODELS` (comma-separated model ids, default empty),
following the `member_publish_models()` pattern
(`backend/app/config/__init__.py:87-100`). Two chokepoints, one flag:

- `declared_domains_for_var` (`backend/app/services/domains.py:123-152`)
  returns non-canonical extras only when the model is allowlisted → scheduler
  neither builds nor publishes global while dark.
- Capability serialization (`serialization.py:91-115`) emits
  `supported_build_regions` filtered to canonical-only when the model is not
  allowlisted → API/frontend see no global domain while dark (2B's
  `resolveDataDomain` then degrades any stray `domain=global` URL to
  canonical, which is the intended dark behavior).

Flag unset ⇒ byte-identical capabilities payload and build targets (pinned by
test). Prod flip = set env on GFS scheduler unit AND API unit, restart both
(descriptor-flip lesson: scheduler AND API).

## 4. Global-aware sanity ranges (G6)

`_check_value_array_sanity` (`backend/app/services/builder/pipeline.py:616-735`)
gains region awareness: `VarSpec` gets optional
`range_by_region: dict[str, tuple[float, float]]`; the sanity check consults
`range_by_region[region]` before falling back to `range`. GFS variables that
declare global get global-physical bounds (e.g. tmp2m −130…135 °F covering
Vostok/Death Valley; dewpoint floor −115 °F — the spike observed −111 °F
Antarctic dewpoints tripping NA ranges). Warn-only semantics unchanged. NA
ranges untouched.

## 5. Frontend: camera-preset collapse fix + selector

**Bug (must land before any prod flip):** `filterRegionOptionsForVariable`
(`frontend/src/lib/app-utils.ts:919-938`) treats a non-empty
`supported_build_regions` as the CAMERA option list. With GFS declaring
`["na","global"]`, viewer camera options would collapse to those two ids,
dropping conus/midwest/etc. No model declares the field today (the branch is
dead), so the fix is a semantics correction, not a behavior change:

- Camera options derive from the ACTIVE data domain's coverage: effective
  domain non-canonical (global) → all camera presets; canonical → today's
  `filterRegionOptionsByCoverage(presets, canonical_region)`. Build-region
  declarations no longer constrain the camera list.
- Domain SELECTOR UI: deferred to a separate small change coordinated with the
  viewer-redesign session (ViewerRail's new Region section is their active
  file). Until then global is URL-driven (`?domain=global`), which the dark
  rollout requires anyway. Not a gate for this phase's Mac-side completion.

## 6. Tests (Mac-side gates)

- **Activation:** with a fake allowlisted plugin declaring global,
  `_build_regions_for_var` returns `(canonical, "global")`; without the flag,
  exactly `(canonical,)`; capabilities payload byte-identical with flag unset.
- **G1 sampling oracle (synthetic):** build a synthetic global 25 km grid from
  a known analytic field (function of lon/lat) through the REAL warp
  (`warp_to_target_grid`) from a 0–360 source; sample at 179°E, 179°W, 0°,
  and a near-seam spread via the REAL sampling path against the domain tree;
  each point must match the analytic reference within packing tolerance.
  179°E and 179°W are distinct locations — no equality assertion between them.
- **G1 contours (synthetic):** `build_iso_contour_geojson` on a synthetic
  global grid with a feature crossing the seam; assert no coordinate outside
  [−180, 180], no feature bbox spanning ≳360° (globe-spanning artifact), and
  contours terminate/wrap at the boundary.
- **Sanity ranges:** global build of a synthetic Antarctic-cold field warns
  under NA ranges and does not warn under `range_by_region["global"]`.
- **Anomaly pin** (§2) and **domain-isolation reuse**: 2A's coexistence tests
  already pin `LATEST`/retention/pruning isolation; extend only if the global
  entries expose a new gap.
- Full backend suite green; ruff no-new-errors; frontend tsc/build green if
  frontend files change.

## 7. Prod runbook (operator) — written as part of this phase

Dark build → verify → (Brian's signoff) → flip. Steps land in
`docs/PHASE_3_GFS_GLOBAL_RUNBOOK.md`: deploy; set
`CARTOSKY_GLOBAL_DOMAIN_MODELS=gfs` on the GFS scheduler unit only (API stays
dark) for one build cycle under current caps with frame work serialized;
record RSS vs Phase 0 baseline (G5), build duration, disk checkpoint (~52%
projection); G2 first/second-request checks incl. new `domains/` nginx/CF rule
coverage; G4 screenshot/GIF on `?domain=global` URLs; G3 measurement per the
plan's contract with explicit pass/fail; real-device mobile. Only after all
gates: set the flag on the API unit to go visible.

## Explicitly out of scope this phase

AIGFS/AIFS/ECMWF (next models); per-domain retention counts and entitlement
gating (2A deferred list); global ERA5 baselines (anomalies stay
canonical-only); domain selector UI (coordinated separately, §5); admin
per-domain surfaces beyond a minimal `domains` section in model status
(published runs + LATEST + disk bytes per domain).
