# Max Week Execution Plan — 2026-07-27

## TLDR

One deep spine, one measured parallel spike.

**Spine:** operational gate → legacy value-COG removal → **backend artifact-domain contract** → frontend domain/camera split → global GFS.
**Parallel:** Skew-T data-contract spike (backend only, one model, no UI).
**Stretch:** AIGFS global, then OISST absolute SST.

**Revised success target:** operational gate complete, legacy value-COG removal complete, region-scoped backend artifact contract implemented and tested, and **global GFS working end-to-end**. AIGFS is the next-model target if time remains. AIFS and ECMWF are almost certainly out of scope this week and should be planned as dark-wired follow-ups.

A correct domain namespace plus one solid global model is a successful week. Skipping that foundation to hit a four-model schedule is not — it would leave a collision hazard in the publication path going into busy season.

---

## Disk: capacity is green, production readiness is not

Measured on prod, 2026-07-27:

```
/dev/vda4       2.0T  584G  1.4T  31% /
/opt/cartosky/data/published    528G
```

This retires the ~878 GB figure from earlier planning context; that number was stale and anything derived from it is void.

Per-model 25 km global additions and the AIFS/ECMWF canonical-grid conversion
(`docs/GLOBAL_MODEL_SIZING_SPIKE_2026-07-22.md`, Sections 3–4):

| Milestone | Net change | Cumulative used | vda4 |
|---|---|---|---|
| Today | — | 584 GiB | 31% |
| + GFS global | 390.7 GiB | ~975 GiB | ~52% |
| + AIGFS global | 58.8 GiB | ~1034 GiB | **~55%** |
| + AIFS + ECMWF global, canonical grids migrated to 25 km | ~−14 GiB | ~1020 GiB | **~54%** |
| + Tier 1 NA ensemble members (signed off) | ~98 GiB | ~1118 GiB | **~59%** |

> [!IMPORTANT]
> Canonical and global artifacts remain separate extents so the canonical viewer
> never fetches a global binary and crops it client-side. They now share a
> uniform 25 km grid. The ~−14 GiB AIFS/ECMWF milestone is a derived net:
> add ~132 GiB of 25 km global artifacts, replace ~169 GiB of retained 9 km
> canonical artifacts with approximately 23 GiB at 25 km. Verify the actual
> disk checkpoint during each model cutover.

**Storage capacity is green for all four models.** That is a capacity statement only; production readiness per model remains conditional on the gates below.

Note the asymmetry: **GFS global alone is 390.7 GiB — roughly 90% of the
four-model net increase after the AIFS/ECMWF canonical-grid conversion.** If
retention or variable scope ever needs trimming, GFS global is the lever, not
the smaller models. It is also the first model to ship, so the ~52% checkpoint
arrives early and is the most informative one.

No new block storage for this rollout. The entire data root stays on vda4 so staging and publish targets remain on one filesystem (atomic rename constraint).

---

## Cross-cutting acceptance gates

### G1 — Antimeridian (global phases only) — **highest schedule risk**

Going global breaks seam handling in four independent places: WebGL layer wrap in `GridWebglLayerController`, MapLibre world-copy rendering continuity, point sampling at ±180°, and contour polygon generation across the seam.

**Oracle — corrected.** Do not assert equality between 179°E and 179°W; they are distinct locations. Instead:

- Each sampled point (179°E, 179°W, 0°, and a spread of near-seam longitudes) must agree with the corresponding source/warped reference value **within packing tolerance**.
- Contours: no globe-spanning erroneous segments or polygons; correct termination or wrapping at the boundary.
- Rendering: visually continuous world-copy behavior where intended, no tearing or duplicated features.

Budget explicit time. No day-table in any revision of this plan has accounted for this, and it remains the item most likely to consume the week.

### G2 — Cloudflare cache

For every new artifact path (global grid binaries, global contour GeoJSON): record the **first** response status, then issue an **identical second request** and require `CF-Cache-Status: HIT`.

A genuinely cold first request may legitimately return `MISS` — that is expected and is not a failure. What is a bug is `DYNAMIC` on a binary at any point, or a second identical request that fails to return `HIT`. Verify per model, per artifact type. Confirm `/contours/` rules cover the new paths.

### G3 — Performance contract (replaces the undefined "sub-100ms" gate)

The 5.5× figure from the sizing spike is a **same-resolution extent multiplier
across the total converted artifact set**, not a per-request multiplier.
Migrating ECMWF/AIFS canonical artifacts from 9 km to 25 km makes their
canonical payloads substantially smaller; selecting the separate 25 km global
domain still expands the covered extent. Measure actual selected-LOD requests
rather than inferring them from either aggregate.

The viewer downloads an entire selected LOD frame, not a viewport subset. Seam position therefore affects rendering and GPU behavior (tested under G1) but **not** payload timing, except insofar as zoom selects an LOD.

Define the contract before measuring:

- **Devices:** one named desktop and one named mobile device.
- **Network:** fixed, stated conditions.
- **Cache:** warm Cloudflare p50 and p95 reported separately; cold-cache observations reported separately, not blended into the percentiles.
- **Variables:** one representative small, one medium, one large.
- **Timings:** transfer, decode, GPU upload, and first-visible-frame, each measured separately with stated start/end boundaries.
- **Baseline:** compared against the equivalent NA LOD, not against an absolute number in isolation.

**Decision rule.** Measurement without a pass/fail rule is just data collection. Before measuring, agree what counts as a material regression against the NA/LOD baseline (a stated percentage on first-visible-frame at warm p95 is the simplest usable form). Then:

- **Within the agreed threshold** → proceed.
- **Materially regressed** → rollout is **blocked pending explicit operator signoff**. Brian decides to accept the regression, apply a lever, or hold the model dark. Recording the numbers and shipping anyway is not an outcome.

This applies per model. A regression accepted for GFS does not pre-authorize the same regression for the next model.

If the contract fails, the available levers are LOD policy, tiling, variable scope, compression, and resolution. Revision 1's claim that "the fix is LOD/tiling, not resolution" was too absolute and is withdrawn. Preference ordering when evidence permits a choice: LOD/compression first, variable scope second, resolution last — but resolution is a legitimate lever if evidence requires it. What is **not** negotiable is pixel-accurate point sampling; do not trade that for frame timing.

### G4 — Screenshot / share / export

Verified after **every** phase, not just the ones that look related.

- Deterministic mocked data and tiles; do not gate on live upstream.
- Pixel-diff with an explicit threshold — **not** byte equality. GPU output, tile timing, and metadata make byte comparison flaky, and a flaky gate here is worse than no gate.
- Map/overlay geometry assertions.
- Explicit presence checks: visible weather pixels, legend, attribution, correct bounds.
- Dual-boolean readiness gate intact: MapLibre `idle` + `onGridFrameReady`.
- Both capture paths exercised: live-canvas WYSIWYG and server-side Playwright.
- GIF export (forecast-hour progression and run-over-run) still produces correct frames.

### G5 — Scheduler memory

**Do not raise `MemoryHigh` preemptively.** Start every global build at current caps with frame work serialized. Raise a cap only when a measured canary shows legitimate pressure, per-unit, with justification recorded. EPS remains the tight unit (~2.5–2.6 GB against a 3 GB cap); global work must not push it.

### G6 — Model heterogeneity

- **ECMWF cycle-length asymmetry** — 06z/18z short-horizon, 00z/12z full-horizon. GFS, AIFS, and AIGFS do not share this. Test both cycle types per model.
- Forecast hours, variables, cadence, and availability differ per model and per region. Never generalize.
- **Global anomaly products excluded** — ERA5 baselines are NA-scoped. Exclude anomaly variables from the global capability contract explicitly, not via a runtime check that could silently pass.
- **Global-aware scientific sanity ranges** — thresholds tuned on NA will fire constantly on Antarctic temperatures and tropical PWAT. False alarms train you to ignore the gate.

---

## Phase 0 — Operational gate — **½ day, hard timebox** (S)

**DONE** ~~1. **Finish or isolate the current MRMS work.** The worktree has uncommitted MRMS and frontend changes overlapping files Phase 1 touches. Commit or stash to a branch.~~
2. **Memory regression** (`docs/MEMORY_REGIME_SHIFT_INVESTIGATION_2026-07-26.md:23`): the 1.5–2× scheduler RSS step is an **efficiency issue, not a leak**, and is not a reason to block the week. Ship the thread-per-Herbie-call fix if it is a small diff; otherwise export the timeout/runtime counters as production metrics and move on.

   **Decision recorded 2026-07-27:** the thread lifecycle is not a small safe
   revert. The deadline wrapper protects build slots from uncancellable Herbie
   calls, and the isolated download path protects canonical artifacts from late
   writers. Take the metrics branch: export the existing process-local counters
   and timers through scheduler snapshots and the API's Prometheus surface.

   **Implemented and observed on production.** Snapshots are
   atomically written per model, retain cumulative totals across scheduler
   process restarts, refresh after member/stat post-processing, and are exposed
   through the existing API `/metrics` scrape. Production verification is
   recorded below.
3. **Define and start the arena canary.** Not "set `MALLOC_ARENA_MAX=2` and see what happens." Specify before starting:
   - The **single** scheduler unit under test (do not apply fleet-wide and call it a canary).
   - Control and treatment periods, and whether the unit restarts between them.
   - Metrics: RSS, swap, build latency.
   - Minimum observation duration.
   - Rollback condition and the exact rollback command.
4. **Capture post-upgrade baselines at current caps:** scheduler RSS per unit, swap, build latency, simultaneous-scheduler load. These are the comparison point for every global build.
5. **Correct and deploy `deployment/systemd/csky-satellite-rgb-scheduler.service`.** The template **is** checked into the deployment repo (verified). Its problems are `User=root` and the absence of any cgroup memory cap — it sets `GDAL_CACHEMAX=256` but has no `MemoryHigh` / `MemoryMax`. Fix the checked-in template, then deploy it through the normal workflow. ~20 minutes.

   **Before deploying, diff the live unit on prod against the repo template.** Prior context indicated a hand-placed copy under `/etc/systemd/system`. If a drifted copy is running, editing the repo template alone changes nothing on prod — and a silent divergence between the checked-in template and the live unit is itself the more serious finding. Record the diff result either way.

### Phase 0 production record — 2026-07-27

- Pre-deploy, `/opt/cartosky-dev` was at `5450cde`. The production checkout
  `/opt/cartosky` was subsequently observed at deployed commit `5749d2c5`.
- `csky-satellite-rgb-scheduler.service` is **masked and inactive**. There is
  no active live unit or process to diff or measure; `MainPID=0`, memory and
  CPU/IO accounting are unset, and effective memory limits are infinity. Keep
  the service masked during this week. The corrected template is committed and
  present in the production checkout, but the submitted evidence does not show
  that a live `/etc/systemd/system` copy was replaced; that distinction is
  non-blocking while the unit remains masked.
- The Herbie runtime bridge is live. Atomic snapshots existed for all nine
  model schedulers (`aifs`, `aigfs`, `ecmwf`, `eps`, `gefs`, `gfs`, `hrrr`,
  `nam`, and `nbm`), owned by `cartosky:cartosky`. The API scrape exposed all
  five expected families: counter, timer count, timer sum, timer max, and
  snapshot timestamp. No timeout-named counter was present in this first
  scrape.
- Initial maximum observed `herbie_call_ms` by model was: AIFS 2885 ms, AIGFS
  2299 ms, ECMWF 3949 ms, EPS 2829 ms, GEFS 3452 ms, GFS 1449 ms, HRRR
  1941 ms, NAM 176 ms, and NBM 1114 ms. These are post-deploy startup/poll
  observations, not cycle-level build-latency measurements.
- All running scheduler services reported
  `ActiveEnterTimestamp=2026-07-27 16:13:59 CDT` and `NRestarts=0` in the
  sample taken about five minutes later.
  This synchronized post-deploy restart makes the following a clean point
  baseline, but not the required 48-hour/eight-cycle control.

| Unit | cgroup current | cgroup peak | `MemoryHigh` / `MemoryMax` | process RSS | Threads |
|---|---:|---:|---:|---:|---:|
| AIFS | 127 MiB | 130 MiB | 2.0 / 2.44 GiB | 199 MiB | 47 |
| AIGFS | 122 MiB | 126 MiB | 2.44 / 3.0 GiB | 196 MiB | 47 |
| CPC | 264 MiB | 602 MiB | 600 / 800 MiB | 283 MiB | 16 |
| ECMWF | 132 MiB | 144 MiB | 8.0 / 9.0 GiB | 204 MiB | 47 |
| EPS | 163 MiB | 163 MiB | 3.0 / 4.0 GiB | 228 MiB | 47 |
| GEFS | 147 MiB | 174 MiB | 3.0 / 3.42 GiB | 208 MiB | 47 |
| GFS | 131 MiB | 154 MiB | 8.0 / 9.0 GiB | 201 MiB | 47 |
| HRRR | 1.15 GiB | 1.60 GiB | 4.0 / 5.0 GiB | 693 MiB | 47 |
| NAM | 124 MiB | 135 MiB | 7.0 / 8.0 GiB | 197 MiB | 47 |
| NBM | 119 MiB | 121 MiB | 3.42 / 4.0 GiB | 194 MiB | 48 |
| NDFD | 88 MiB | 232 MiB | 1.95 / 2.44 GiB | 141 MiB | 31 |
| NWS hazards | 424 MiB | 902 MiB | 900 MiB / 1.17 GiB | 265 MiB | 16 |
| Radar | 838 MiB | 1.87 GiB | 2.0 / 3.0 GiB | 540 MiB | 32 |
| RTMA-RU | 116 MiB | 116 MiB | 1.17 / 1.46 GiB | 186 MiB | 47 |
| Satellite | 112 MiB | 1.00 GiB | 1.0 / 2.0 GiB | 167 MiB | 47 |
| SPC | 24 MiB | 24 MiB | 150 / 200 MiB | 44 MiB | 16 |
| WPC | 110 MiB | 267 MiB | 600 / 800 MiB | 168 MiB | 31 |

- Host point samples:
  - Pre-deploy: 62 GiB RAM total, 11 GiB used, 51 GiB available; swap
    4.0/8.0 GiB used.
  - About five minutes after the synchronized restart: 62 GiB total, 8.5 GiB
    used, 54 GiB available; swap 808.5 MiB/8.0 GiB used.
  - The lower second sample is recorded but must not be attributed to the
    metrics change or allocator behavior from two point observations.

### Arena canary protocol — locked 2026-07-27

- **Treatment unit:** `csky-gfs-scheduler.service` only. GFS showed the
  clearest post-regression RSS increase and naturally restarts after successful
  builds, making process-lifetime comparisons less ambiguous.
- **Control:** the immediately preceding 48 hours, requiring eight completed
  GFS cycles at the current worker count and memory caps. The clean control
  window starts from the synchronized deployment restart at
  `2026-07-27 16:13:59 CDT`; do not begin treatment before both 48 hours and
  eight completed GFS cycles have elapsed.
- **Treatment:** add `MALLOC_ARENA_MAX=2` to the checked-in GFS unit template
  in a dedicated commit, deploy through the normal workflow, then observe the
  next 48 hours / eight completed cycles. Do not bundle any other memory
  setting with that deploy.
- **Restart:** one intentional `daemon-reload` and GFS restart at the treatment
  boundary; natural post-success restarts continue in both periods.
- **Metrics:** per-process RSS p50/p95/peak, anonymous mapping count, host swap
  used/peak, build duration by cycle, Herbie timeout counts, and scheduler
  failures. Record concurrent scheduler load for both windows.
- **Rollback:** immediately for a start failure, OOM kill, or two consecutive
  attributable build failures; also roll back if two consecutive completed
  treatment cycles regress build duration by more than 25% versus their
  control comparison without an upstream-delay explanation. Revert the
  dedicated canary commit on Mac, push, pull on prod, run
  `sudo systemctl daemon-reload`, and restart only
  `csky-gfs-scheduler.service`.

### Phase 0 gate status after the production deployment

- **Complete:** the runtime metrics path is deployed and returning live data
  for all nine model schedulers.
- **Complete:** the RGB template correction is committed in the deployed
  checkout; the service remains deliberately masked and inactive.
- **Recorded:** a post-deploy per-unit memory/cap point sample and two host
  memory/swap point samples.
- **In progress:** the GFS arena canary is in its control phase. No
  `MALLOC_ARENA_MAX` treatment has been applied.
- **Still required before treatment:** 48 hours and eight completed GFS cycles,
  including cycle build durations, RSS p50/p95/peak, anonymous-map counts, host
  swap peak, failures/upstream-delay notes, Herbie timeout counts, and
  concurrent-load context.

**Stop-and-verify:** baselines recorded, working tree clean, canary defined and running on one unit, RGB service unit corrected and committed.

---

## Phase 1 — Legacy value-COG removal — **1–2 days** (M)

Framing matters: this is **"remove legacy value-COG paths *and* extract still-live raster utilities,"** not "delete COG." The short framing produces either a left-behind emergency fallback or gutted raster code.

**Sequencing note:** `_resolve_val_cog` (`sampling.py:579`) is one of the functions carrying the discarded-`region` pattern. Deleting it in Phase 1 shrinks the surface Phase 2A must plumb. Doing COG removal before the domain contract is deliberate, not incidental.

### Remove
- Value-COG writers and the emergency COG sampling/write fallback
- Conditional COG branches in sample endpoints and meteogram paths
- COG-aware scheduler and telemetry logic
- Resolution helpers, endpoint resolvers, migration flags, canary tooling
- Obsolete sampling-source cache-key branches
- COG-only tests and migration fixtures
- Standalone publishers still importing `write_value_cog` (easy to miss with a naive grep of the main pipeline)

### Preserve and extract
- Warping, grid geometry, and RGBA-writing functions currently inside `cog_writer.py` — load-bearing for visualization and contour generation
- All raster code still used for visualization, contour generation, source decoding, or warping
- **"COG removal" must not become "remove rasterio."**

### Rename / split
`cog_writer.py` now holds non-COG responsibilities. Split the live helpers into an accurately named module rather than leaving a misleading filename.

**Stop-and-verify:** backend suite green, `ruff` clean on `backend/app backend/tests backend/scripts`, Playwright green, G4 green, and manual confirmation that sampling, meteograms, city values, observed products, and binary quality gates all work.

### Phase 1 completion record — 2026-07-28

Landed on `main` through `8b985e4f` (inventory `cf3e230b` → extraction
`13d2806e` → fixture migration → read path → write path → fallbacks/canary →
flag retirement → ETag cleanup). Pre-deletion state recoverable from git
history; the canary's audited scope classifier lives on in
`backend/tests/helpers_variable_scope.py`.

- **Backend suite:** 1709 passed, 1 skipped, 0 failed. (Started at 1865; the
  delta is deleted COG/parity/shadow-mode/substrate-switch tests. The
  DNS-dependent Herbie failure never reproduced this session.)
- **ruff:** 327 errors vs 401 at the pre-Phase-1 baseline — the gate is
  restated as "no new errors"; a zero-error cleanup is a separate task.
- **Sweep:** zero references to `write_value_cog` / `validate_cog` /
  `_resolve_val_cog` / `cog_writer` / `build_grid_for_run` /
  `CARTOSKY_COG_SAMPLING_MODELS` / `binary_sampling_enabled` across backend
  and deployment. Deliberate keeps: the `has_cog` wire field (client
  contract), the pinned `val.cog.tif not found` 404 body, and pipeline's
  staging-cleanup path entry.
- **Frontend:** production build green. **Playwright: NOT run to green** —
  collection fails on main and branch identically
  (`compare-map-regressions.spec.ts` imports Vite-only app source;
  pre-existing, tracked separately). G4 and the manual
  sampling/meteogram/city-values/observed checks remain **operator
  verification on deploy**.
- Found and fixed in passing: the forecast-page MRMS recent-precip lookup
  still called the COG sampler, silently returning all-None since the MRMS
  cutover; it now samples grid binaries, with a regression pin.
- `_resolve_val_cog` is gone, so the discarded-`region` surface Phase 2A must
  plumb is smaller, as intended.

---

## Phase 2A — Backend artifact-domain contract — **the new blocker phase** (L)

**This did not exist in revision 1 and is the reason Phase 2 implementation is not yet authorized.**

Today the region argument is accepted and discarded throughout the artifact path. A global run would land in the same directories as the NA run. Before any global publication, the following must be genuinely region-scoped:

- Staging and published run roots
- Manifests and latest pointers (`LATEST`)
- Grid manifests and frame URLs
- Sampling and meteogram resolution
- Promotion, retention, pruning, telemetry, and admin status
- Scheduler build targets (`_build_regions_for_var` must be able to return more than the canonical region)
- Canonical-region backward compatibility

### Design decision to lock **before** implementation

The physical directory layout is load-bearing and must not be delegated implicitly to the implementing agent. The safest shape: **preserve today's canonical paths byte-for-byte, and place non-canonical domains in an explicit namespace.** Decide and record the exact layout, then write tests that prove:

- [ ] Existing NA/CONUS URLs are unchanged
- [ ] Global and canonical builds of the same model/run/variable coexist without collision
- [ ] `LATEST`, manifests, retention, and pruning **cannot cross domains**
- [ ] Promotion remains an atomic same-filesystem rename
- [ ] An absent `domain=` resolves exactly as today

### Type the domain correctly

**Do not hardcode a `na | global` union.** `_default_build_region` reads `capabilities.canonical_region` and falls back to `CANONICAL_COVERAGE = "conus"`. Several models use `conus` or another ID as their canonical build region. The generic concept is **"published build-region ID,"** capability-driven per model, even though the four global models will initially offer only their canonical region plus `global`.

A universal `na | global` union would regress HRRR, NAM, NBM, MRMS, and observed products.

**Stop-and-verify:** the coexistence and non-crossing tests above pass; no existing NA behavior changes; nothing is published globally yet.

### Phase 2A design locked — 2026-07-28

Full design with verified inventory, path shapes, type design, and test list:
`docs/PHASE_2A_DOMAIN_CONTRACT_DESIGN_2026-07-28.md`. Operator decisions:

- **Layout: Option B — parallel domain run tree.** Canonical paths byte-for-byte
  unchanged; non-canonical domains under
  `{staging|published}/{model}/domains/{d}/{run}/...`,
  `published/{model}/domains/{d}/LATEST.json`,
  `manifests/{model}/domains/{d}/{run}.json`. The `domains/` literal can never
  match `RUN_ID_RE`, so every existing retention/backfill/scan already skips it.
- **Edge-served artifact URLs carry the domain in the path** (amended after
  adversarial review — do not rely on a query parameter to isolate immutable
  artifact bodies): `domains/{d}` inserted immediately before `{model}` on the
  grid-file, contour, and vector routes, e.g.
  `/api/v4/grid/domains/{d}/{model}/{run}/{var}/{file}?v=...` and
  `/api/v4/domains/{d}/{model}/{run}/{var}/{fh}/contours/{key}`. Canonical
  artifact URLs remain exactly unchanged with neither a domain segment nor
  `domain=`. `domain=` stays the mechanism on control/selection APIs and
  permalink state only.
- **Declaration granularity: var-level `supported_build_regions`** (existing
  capability field) — per-variable global rollout permitted; run manifests per
  domain are filtered to the declaring variable subset.
- **Publish gating:** canonical promote/LATEST gated on canonical readiness
  specifically; canonical publishes first; each non-canonical domain publishes
  independently under its own try/except so its failure cannot abort canonical
  promotion or the retention tail.
- **Admin status/telemetry stay canonical-only in 2A**; per-domain surfaces are
  Phase 3 work.
- **frames-404 telemetry gains a `domain` column now.**
- Adversarial design review ran 2026-07-28: approve-with-amendments; all
  amendments (incl. the bootstrap viewport-preset trap and the non-mechanical
  resolver-rename table) are incorporated in the design doc, §7.
- Deferred to Phase 3: per-domain retention counts, `domain=global`
  entitlement gating, storage placement if a separate volume is ever added.

---

## Phase 2B — Frontend data-domain / camera-preset split — **1 day** (M)

Only after 2A lands.

| Concept | Meaning | URL key |
|---|---|---|
| **Data domain** | Which artifacts are fetched — published build-region ID | `domain=` (new) |
| **Camera preset** | Viewport only — `conus`, `midwest`, `northwest`, `global`, … | `region=` (unchanged) |

`frontend/src/App.tsx:746` deliberately keys data requests to the model's canonical `dataRegion` while `region` changes only the viewport. That was a recent, deliberate fix. **Extend it; do not revert it.**

Permalink compatibility is non-negotiable — TWF has shared links in the wild. Links without `domain=` resolve to the model's canonical domain, identically to today.

### Compare — decision made, not deferred

Revision 1 left this as "independently or explicitly constrained," which was an unresolved product decision dressed up as an acceptance criterion. `frontend/src/pages/compare.tsx:794` currently feeds a shared `conus` region to both data loaders.

**v1 decision:**
- One **shared** data domain across both panes.
- Only offer a domain supported by **both** selected model/variable pairs.
- Camera stays shared and independent of domain.
- **No NA-versus-global pane comparisons this week.**

Independent per-pane domains would introduce regridding, diff semantics, unequal extents, and a much larger test surface. Not this week.

> [!NOTE]
> **No global artifacts exist until Phase 3.** Every `domain=global` assertion in this phase is tested against **mocked/synthetic region-scoped fixtures** built on the Phase 2A layout. Phase 2B proves the selector, request keying, and permalink behavior are correct; it does not and cannot prove real global data renders.

**Stop-and-verify:** real TWF permalinks resolve to identical data and viewport; `domain=global` routes requests to the correct region-scoped paths **against synthetic fixtures** without altering camera behavior; `region=` alone still changes only the viewport; Compare enforces the shared-domain rule and degrades cleanly when one model lacks a domain; G4 green on fixed URLs.

---

## Phase 3 — Global rollout at 25 km (L)

### Uniform deterministic-grid policy — locked 2026-07-27

- GFS, AIGFS, AIFS, and ECMWF publish at **25 km in every supported data
  domain**. Domain selects artifact extent and variable availability; camera
  preset selects viewport. Neither selects resolution.
- GFS and AIGFS canonical artifacts are already 25 km. AIFS and ECMWF
  canonical artifacts migrate from 9 km to 25 km as part of their respective
  global rollout, not as an unrelated early production change.
- Canonical and global artifacts remain separate. This preserves existing
  canonical URLs and smaller canonical payloads; the viewer must not fetch a
  global artifact merely because its camera is over NA/CONUS.
- NA-only anomaly variables remain canonical-only until global ERA5 baselines
  exist, but their canonical artifacts follow the same 25 km model grid.
- The 9 km canonical grid oversamples CartoSky's 0.25° open ECMWF/AIFS source.
  Its smoother interpolation is not additional source-resolved forecast
  detail. The cutover nevertheless requires visual, contour, sampling, and
  playback A/B verification because presentation can change.

Rationale for 25 km: close to the delivered resolution of the open ECMWF/AIFS
source; removes viewport/domain-dependent model resolution; reduces the
four-model disk projection to ~54% (~59% with ensembles); and avoids the
multi-hour ECMWF/AIFS bursts and live-service interference observed during the
9 km global sizing run.

**Order: GFS → AIGFS → AIFS → ECMWF.** GFS first — cleanest global GRIB, and it surfaces the antimeridian bugs before the pattern is repeated. ECMWF last — cycle-length asymmetry and longest burst build.

Per model: global retention, publication, manifest, and latest-pointer behavior; global sampling; admin visibility for build duration, disk usage, incomplete runs, and per-region publication status; rollout control so a model can be wired but left dark.

**Per-model stop-and-verify — all required before the next model:**

- [ ] Manifest correct against the model's **actual** global availability, not assumed from NA
- [ ] G1 antimeridian oracle passes (reference agreement within packing tolerance; contour termination correct)
- [ ] G2 Cloudflare: first response recorded, second identical request returns `HIT`, no `DYNAMIC` on any binary
- [ ] G3 performance contract measured against the NA LOD baseline; if materially regressed, operator signoff obtained before rollout
- [ ] G4 screenshot and GIF export verified for the new domain, both capture paths
- [ ] G5 ran within existing caps; RSS compared against Phase 0 baselines
- [ ] G6 both cycle types tested; anomaly variables absent from global capabilities; sanity ranges global-aware
- [ ] Canonical and global manifests report the same 25 km model grid; changing only the camera preset does not change resolution or weather-artifact identity
- [ ] For AIFS/ECMWF, canonical 9 km → 25 km A/B verification passes for representative small/medium/large variables: source-reference sampling, contour geometry, playback, screenshots, and GIF export
- [ ] Domain isolation holds under load: no `LATEST`/retention/pruning crossover observed
- [ ] Disk utilization checkpoint recorded
- [ ] Mobile: viewer usable and performant at global extent on a real device

**Realistic expectation: GFS ships this week; AIGFS is the stretch.** If AIFS/ECMWF cannot clear gates, leave them wired behind rollout controls and dark. Two solid global models beat four shaky ones going into October.

---

## Parallel track — Skew-T data-contract spike (backend only) (M)

**A spike, not a committed architecture.** On-demand Herbie extraction with a rounded-coordinate cache is a candidate, not the decision.

The question most likely to force the answer: **what happens when a run exists in CartoSky's published tree but has aged out upstream?** You retain runs Herbie can no longer fetch. If that gap is material, compact published profile artifacts win over on-demand extraction — a different pipeline decision entirely.

Scope — GFS only:

1. Validated sounding JSON for one run / one forecast hour / one location, using `metpy.calc`.
2. **Validate against a known RAOB** or an independent calculation. Accuracy gate first.
3. Measure: upstream call count, latency under repeated interactive clicking, throttling behavior, payload size, MetPy CPU cost, cache cardinality for arbitrary coordinates.
4. Determine which models and pressure levels reliably supply all required fields.
5. Written comparison: bounded on-demand extraction vs. compact published profile artifacts, with a recommendation.
6. Define failure behavior: hard timeout and a graceful "sounding unavailable" state, never a hung request.

**No frontend work this week.** No endpoint architecture commitment until the spike reports.

MetPy is a justified dependency — nothing in rasterio/pyproj does parcel paths, CAPE/CIN, or layer thermodynamics, and hand-rolling those against CartoSky's accuracy bar is a bad trade. `metpy.calc` only; `metpy.plots.SkewT` produces static matplotlib and has no place here. The eventual frontend is a native Canvas/SVG React component.

This track is not fully independent — it touches model fetching, API contracts, dependencies, and production operations. Hold it behind Phase 2A's most invasive backend changes.

---

## Stretch — OISST absolute SST (M, only if global is genuinely green)

- **OISST as the single source** for SST and eventual anomalies; source consistency matters more than freshness.
- Preliminary data first, replaced by final when available.
- **Standalone daily global layer**, not tied to a forecast-model run.
- **Absolute SST only.** Anomaly is a separate release gate requiring a same-source climatology baseline.
- Geo-Polar offers fresher, higher-resolution absolute SST, but mixing it with an OISST climatology produces a dataset-bias artifact that will look like signal. Evaluate later.

---

## Explicitly deferred

| Item | Why not this week |
|---|---|
| **RRFS** | Upstream parallel data not available until 2026-08-11; NAM replacement pushed to early October. At most, a source-contract checklist after Aug 11. |
| **AIFS Ensembles** | Needs its own sizing/access/retention spike. Ensembles consume capacity far faster than deterministic global expansion. |
| **NEXRAD Level II** | A separate ingestion/decoding/tiling/retention/operations program, not a map-variable addition. |
| **GOES GLM** | Always-on event-stream pipeline with unexamined scheduler cadence risk and its own aggregation and visualization decisions. |
| **GDPS / RDPS / HRDPS** | GDPS is the right *next* model after global infrastructure settles — it benefits directly from Phase 2A. Do not onboard several Canadian models at once. |
| **RAP** | Low marginal value while HRRR exists and the RRFS transition approaches. |
| **Animated wind barbs** | Touches `GridWebglLayerController` and the screenshot path — the same files global work is churning. Fails its own isolation criterion. |
| **Climate / Forecast / meteogram chart additions** | Valuable but incremental. Ordinary usage, not the expensive week. |
| **Global ERA5 climatology baseline rebuild** | Hard prerequisite for every global anomaly variable (G6 excludes them for exactly this reason). Not a refactor — `climatology.py` is already region-parameterized and paths already carry region — the cost is archive acquisition and storage, neither of which is sized, and neither appears in the disk table above. Two baseline families are in scope, not just the runbook's three pilot fields: instantaneous (`tmp2m`, `tmp850`, `hgt500`) and precip accumulation (`precip_5d/7d/10d/15d/16d_anom`), each with `__mean` variants. **Start CDS retrieval early regardless of when the rest is scheduled** — ERA5 archive pulls are slow and rate-limited, so the lead time, not the compute, is what blocks global anomalies. |
| **True Color RGB** | Fix the service unit (Phase 0); do not launch the product. |
| **SEO, marketing, sharing targets, mobile polish** | Phase 4 polish/freeze period. |
| **Monetization, R2 migration, AI integrations** | Post-busy-season or too undefined. |

---

## Operating discipline

- **Deploy workflow, no exceptions:** edit on Mac → `git push` → `sudo git pull` on prod → confirm the commit landed → restart services. Never direct server edits.
- **Prod execution model:** Claude Code writes and commits scripts on Mac through version control; Brian executes on prod under `/opt/cartosky-dev` and pastes results back. Agents do not execute on prod.
- **Production Python:** `/opt/cartosky/.venv/bin/python3` (system `python3` lacks rasterio and pipeline deps).
- **Spike runs on prod:** `systemd-run` with `MemoryHigh=4G` / `MemoryMax=6G`, `CPUWeight=50` / `IOWeight=50`, one model block at a time.
- **Context separation:** separate agent contexts for investigation, implementation, and adversarial review. Expensive model time is worth most on deletion safety, dependency tracing, operational contracts, and independent verification.
- **Every large cross-cutting prompt ends with an explicit test/canary gate.**
- **The ordering below is an ordering, not a schedule.** Gates govern progression.

| Order | Work |
|---|---|
| 1 | Phase 0 operational gate |
| 2–3 | Phase 1 legacy COG removal + raster utility extraction |
| 3–5 | Phase 2A backend artifact-domain contract (design → review → implement) |
| 5 | Phase 2B frontend domain/camera split |
| 6–7 | Phase 3 global GFS end-to-end |
| If time | Phase 3 AIGFS |
| Parallel | Skew-T spike |
| Stretch | OISST absolute SST |

---

## Agent kickoff prompts

Paste into a fresh context. Each assumes the agent reads relevant source before writing.

### Phase 1 — COG removal

> You are working in the CartoSky repo. The COG-to-binary-sampling migration is complete across all products; binary grids are the single artifact for both WebGL rendering and point/meteogram sampling.
>
> Task: remove all **legacy value-COG** machinery while preserving raster code that is still live.
>
> Before changing anything, produce an inventory with file:line citations of: value-COG writers; the emergency COG sampling/write fallback; conditional COG branches in sample endpoints and meteogram paths; COG-aware scheduler and telemetry logic; resolution helpers, endpoint resolvers, migration flags, and canary tooling; obsolete sampling-source cache-key branches; COG-only tests and fixtures; and any standalone publisher importing `write_value_cog`. Note that `_resolve_val_cog` at `backend/app/services/sampling.py:579` is in scope for deletion.
>
> Separately inventory what must be **preserved**: warping, grid geometry, and RGBA-writing functions inside `cog_writer.py`, plus any raster code used for visualization, contour generation, source decoding, or warping. This task must not become "remove rasterio."
>
> Stop after the inventory and wait for review. In a second pass, remove the dead paths and extract the live helpers into an accurately named module, leaving `cog_writer.py` either deleted or accurately scoped.
>
> Acceptance: backend suite green, `ruff` clean on `backend/app backend/tests backend/scripts`, Playwright green, and sampling, meteograms, city values, screenshots, GIF export, observed products, and binary quality gates verified working. Minimal diff. Flag any scope change before making it.

### Phase 2A — Backend artifact-domain contract (design pass)

> In CartoSky's backend, many artifact-path functions accept a `region` argument and then discard it. Confirmed examples: `backend/app/services/scheduler.py:369` (`_build_regions_for_var` always returns only the canonical region), `scheduler.py:604` (`_frame_sidecar_path` executes `del region`), `backend/app/services/grid.py:1261` (`grid_dir` executes `del region` at 1262), and `backend/app/services/sampling.py` lines 430, 588, 608, 637, 778.
>
> CartoSky needs to publish global artifacts alongside existing canonical-region artifacts without collision. **This is a design task. Produce a written design; write no implementation code.**
>
> Deliver:
>
> 1. A complete inventory, with file:line citations, of every place the region argument is accepted and discarded, and every artifact path that would collide if a global run were published today. Cover staging and published run roots, manifests, latest pointers, grid manifests, frame URLs, sampling, meteograms, promotion, retention, pruning, telemetry, admin status, and scheduler build targets.
> 2. A proposed physical directory layout that preserves today's canonical paths **byte-for-byte** and places non-canonical domains in an explicit namespace. State the exact path shapes.
> 3. Confirmation that promotion remains an atomic same-filesystem rename under the proposed layout.
> 4. The type/enum design for the domain concept. Do **not** hardcode a `na | global` union — `_default_build_region` reads `capabilities.canonical_region` with a fallback to `CANONICAL_COVERAGE = "conus"`, and models including HRRR, NAM, NBM, MRMS, and observed products use their own canonical region IDs. The concept is a capability-driven "published build-region ID."
> 5. The test list proving: existing NA/CONUS URLs unchanged; global and canonical builds of the same model/run/variable coexist; `LATEST`, manifests, retention, and pruning cannot cross domains; absent `domain=` resolves exactly as today.
>
> Stop and wait for review before implementing.

### Phase 2B — Frontend domain / camera split

> In the CartoSky frontend, `region` currently serves two conflated purposes. `frontend/src/App.tsx:746` deliberately keys data requests to the model's canonical `dataRegion` while `region` changes only the viewport — correct, and must be preserved.
>
> Task: introduce **data domain** (a capability-driven published build-region ID, not a hardcoded `na | global` union) as a concept separate from **camera preset**. The backend artifact-domain contract from Phase 2A has already landed; build against it.
>
> Permalinks: `region=` keeps its current meaning. Add `domain=`. A link with no `domain=` must resolve to the model's canonical domain, identically to today. Real TWF links depend on this.
>
> Compare (`frontend/src/pages/compare.tsx:794` currently feeds a shared `conus` region to both loaders): v1 uses **one shared data domain across both panes**, offering only domains supported by both selected model/variable pairs, with the camera shared and independent. Do not implement independent per-pane domains.
>
> First produce a plan with file:line citations of every place the two concepts are conflated, and stop for review.
>
> No global artifacts exist yet — they arrive in Phase 3. Build mocked/synthetic region-scoped fixtures on the Phase 2A layout and test `domain=global` against those. Do not attempt to fetch real global data, and do not treat the absence of it as a failure.
>
> Acceptance: supplied real permalinks resolve to identical data and viewport; `domain=global` routes to the correct region-scoped paths against synthetic fixtures without altering camera behavior; Compare enforces the shared-domain rule and degrades cleanly when one model lacks a domain; screenshot output passes pixel-diff threshold on fixed URLs.

### Phase 3 — Global model (repeat per model)

> Task: add global 25 km support for **{MODEL}** in CartoSky, on top of the
> Phase 2A artifact-domain contract. The locked policy is one 25 km model grid
> across every supported domain: domain changes artifact extent and
> availability, never resolution; camera changes viewport only. Canonical and
> global artifacts remain separately region-scoped, and the canonical viewer
> must never fetch a global binary and crop it client-side. For AIFS or ECMWF,
> migrate the canonical grid from 9 km to 25 km within this model rollout and
> verify the presentation cutover; GFS and AIGFS canonical grids are already
> 25 km.
>
> Read the existing canonical-region publication path for this model before proposing anything. Do not assume this model shares forecast hours, cadence, variables, or cycle structure with any other model. {If ECMWF: this model has a cycle-length asymmetry — 06z/18z short-horizon, 00z/12z full-horizon. Test both.}
>
> Deliver: global retention, publication, manifest, and latest-pointer behavior; global sampling; admin visibility for build duration, disk usage, incomplete runs, and per-region publication status; a rollout control so this model can be wired but left dark.
>
> Exclude anomaly variables from the global capability contract explicitly — ERA5 baselines are NA-scoped. Make scientific sanity ranges global-aware so Antarctic and tropical extremes do not generate false warnings.
>
> Do not raise any scheduler `MemoryHigh` cap. Run within current caps with frame work serialized and report measured RSS against the Phase 0 baseline.
>
> Acceptance, all required: manifest correct against actual global
> availability; canonical and global manifests report the same 25 km model
> grid; changing only the camera preset leaves resolution and weather-artifact
> identity unchanged; for AIFS/ECMWF, representative canonical variables pass
> the 9 km → 25 km source-reference sampling, contour, playback, screenshot,
> and GIF A/B gate; sampled points at 179°E, 179°W, 0°, and a spread of
> near-seam longitudes each agree with the source/warped reference within
> packing tolerance (do **not** assert 179°E equals 179°W — they are distinct
> locations); contours terminate or wrap correctly at the boundary with no
> globe-spanning polygons; world-copy rendering visually continuous; for
> global binaries and contour GeoJSON, the first response status recorded and
> a second identical request returning `CF-Cache-Status: HIT`, with no
> `DYNAMIC` on any binary (a cold first `MISS` is expected and is not a
> failure); performance contract measured per the plan's G3 definition against
> the NA LOD baseline, with rollout blocked pending operator signoff if
> materially regressed; screenshot and GIF export verified via both
> live-canvas and Playwright paths; no `LATEST`/retention/pruning crossover
> between domains; disk utilization checkpoint recorded.

### Parallel — Skew-T spike

> This is an **architecture spike**, not an implementation task. Do not build a frontend component and do not commit to an endpoint design.
>
> Target: GFS only. Produce validated Skew-T sounding JSON for one run, one forecast hour, one location, using `metpy.calc`. Do not use `metpy.plots.SkewT`.
>
> Validate the output against a known RAOB or an independent calculation and report the comparison explicitly. Accuracy is the first gate.
>
> Then measure and report: upstream call count and latency under repeated interactive clicking; source throttling behavior; payload size; CPU cost of the MetPy calculations; cache cardinality implications for arbitrary coordinates; and which models and pressure levels reliably supply all required fields.
>
> Answer specifically: what happens when a run exists in CartoSky's published tree but has aged out upstream? CartoSky retains runs Herbie can no longer fetch.
>
> Conclude with a written comparison of bounded on-demand Herbie extraction versus compact published profile artifacts, plus a recommendation and proposed failure behavior (hard timeout, graceful "sounding unavailable" state, never a hung request).

---

## Open items to record as the week progresses

- Disk checkpoint after each global publish, against the per-model table above
- Whether `MALLOC_ARENA_MAX=2` shows measurable benefit on the single canary unit
- Skew-T spike verdict: on-demand vs. published profile artifacts
- Any scheduler that required a cap increase, and the justification
- Measured G3 numbers per variable — these become the baseline for any future resolution-tier argument
- The locked Phase 2A directory layout, recorded here once decided
- Global ERA5 baseline rebuild: sizing (raw archive + built assets, as a row in
  the disk table), and the date CDS retrieval actually starts — global anomaly
  variables stay blocked until both exist

---

## Verification provenance

Directly verified in source during this session: `scheduler.py`
(`_build_regions_for_var`, `_frame_sidecar_path`, `_frame_value_path`,
`_default_build_region`, `CANONICAL_COVERAGE`), `grid.py:1261–1262`,
`sampling.py` lines 430, 588, 608, 637, 778, `backend/app/main.py:2781`,
`frontend/src/App.tsx:746`, `frontend/src/pages/compare.tsx:794`,
`docs/MEMORY_REGIME_SHIFT_INVESTIGATION_2026-07-26.md`,
`docs/GLOBAL_MODEL_SIZING_SPIKE_2026-07-22.md:78-83` (including the
390.7 / 58.8 GiB per-model figures), and
`deployment/systemd/csky-satellite-rgb-scheduler.service`.

Disk figures were measured directly on prod and supersede all prior estimates.
