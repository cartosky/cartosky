# Ensemble Member Scheduler Design (Phase 2)

> **Status:** APPROVED (Brian, 2026-07-06, concurring with independent Codex review) — D1–D5 all approved; see the decisions table for conditions. One explicit Phase 3 requirement carried from review: the meteogram members probe must be repointed at the `ensemble.members` descriptor (Section 7, D1 note). Phase 3 implementation may start.
>
> **Scope (per the 2026-07-06 Phase 1 sign-off):** Tier 1 only — slim member grid binaries for meteogram consumption, 6-run parity retention. This design must not preclude Tier 2 stats grids (conditional GO, 6-run parity, performant-percentile precondition) or future global regions, but implements neither. Tier 3 is NO GO and appears here only as a non-precluded future.
>
> **Inputs:** `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md` (locked Sections 3–4, corrected 2026-07-06) and `docs/ENSEMBLE_MEMBER_SIZING_SPIKE.md` (measurements). Code claims below were verified against `scheduler.py`, `grid.py`, `builder/pipeline.py`, `builder/fetch.py` on 2026-07-06.

---

## 1. Summary of recommendations

| # | Area | Recommendation |
|---|------|----------------|
| R1 | Write path | Refactor `write_grid_frame_for_run_root` internals into a shared artifact writer; add a slim variant. Default path stays bit-identical. |
| R2 | Member build | Dedicated `build_member_frame` reusing fetch/convert/warp/gate primitives (the spike-validated shape) — not a profile threaded through `build_frame`'s 6-step orchestration. |
| R3 | Packing | Member-suffix fallback inside `_packing_config` via a shared normalization helper — the single seam that also makes `grid_supported`, manifest iteration, and the binary sampler member-aware. |
| R4 | GEFS loop | Member pass inside the GEFS scheduler poll loop, strictly after the run's mean catchup completes; resumable; preemption check between frames so a new run's mean build always wins. |
| R5 | Publish | Members written to the **staging** run root; carried to published by the existing manifest-build + hardlink-merge promote. Retention is inherited run-level — parity by construction. |
| R6 | EPS (Phase 4 preview) | Member encode at end-of-bundle per fh from the already-cached pf subset, one band at a time, with an explicit band→member-number mapping (byte-order ≠ member-order). |
| R7 | Registration | No per-member catalog entries. Canonical var's `ensemble` block gains a `members` descriptor; per-member dirs get ordinary grid manifests for free via R3. **Keep map-facing `supported_views = ["mean"]`** (deviation from plan wording — decision D1). |
| R8 | Rollout | Env allowlist `CARTOSKY_MEMBER_PUBLISH_MODELS` (empty default = off), enabled vars per plugin capability metadata. Phase 3 starts gefs/tmp2m only. |

Estimated steady-state cost (spike-measured basis): GEFS tmp2m member pass ≈ 12–15 min per run at fetch-parallelism 2, +~0.5 GiB process peak, 1.81 GB/run/var disk.

---

## 2. Write path: profile-aware without touching the default (R1, plan §3.2)

**Verified constraint:** `write_grid_frame_for_run_root` unconditionally applies display prep (per-`(model,var)` table lookup inside the call) and gates gz/brotli sidecars on process-global env flags — it cannot express "slim" per call.

**Design:** extract the tail of that function into a private shared writer:

```
_write_grid_frame_artifacts(*, run_root, model, var, fh, values, bounds, projection,
                            packing, level, compression_sidecars: bool,
                            display_prep_meta: dict | None) -> frame_meta
```

— encode (`_encode_values`) → atomic tmp+rename `.bin` → optional sidecars → meta JSON (`format_version`, dims, bbox from pre-upscale dims, effective post-upscale transform, projection — the exact math the spike replicated and unit-tested). Then:

- `write_grid_frame_for_run_root(...)` — unchanged signature and behavior: runs `prepare_grid_display_values`, passes env-gated sidecar flags. Env flags stay authoritative for every existing mean/deterministic frame.
- `write_slim_grid_frame_for_run_root(...)` — new: no display prep, `compression_sidecars=False`, resolves packing through the R3 fallback. Slim members are native 1× (spike-verified: `tmp2m__mean` has no display-prep entry; the mixed-resolution question is moot until Tier 3, which is NO GO).

Why not a profile parameter on the existing function: the plan allows either; the shared-internals variant avoids touching existing call sites and makes "default behavior unchanged" trivially reviewable (the existing function's diff is mechanical extraction only). The plan's §3.2 "build profile" intent is satisfied at the member-build layer (Section 3): the stages colorize/contours/pressure-centers/sidecar-JSON/compression/display-prep are all absent from the member path by construction, not by flag-checks inside `build_frame`.

## 3. Member frame build (R2)

New `build_member_frame(...)` in `builder/` (or a small `builder/members.py`):

```
fetch_variable(member kwarg) → convert_units → warp_to_target_grid → 
check_pre_encode_value_sanity (ENFORCED — failure: frame NOT written, loud log) → 
write_slim_grid_frame_for_run_root
```

All six primitives are reused as-is; this is exactly the pipeline the spike ran 2,015 times with zero gate failures and values verified through the production sampler. Region, resampling, search patterns, and conversions resolve from the plugin/capability exactly as `build_frame` does (no `na` hardcoding — global non-preclusion). Per-frame memory ≈ one member grid + warp buffers (spike: 489 MiB process peak including everything).

GEFS member identity → Herbie kwarg (spike-confirmed, herbie 2026.3.0): `member=1..30` → `gepNN`, `member=0` → `gec00`. Roster = 30 + control. EPS has **no control** (plan §2.2 correction); its path is Section 6.

## 4. Packing resolution (R3, plan §3.4)

One shared helper in `grid.py` (written once, per plan §4.1):

```
def normalize_grid_pack_var_id(var: str) -> str:
    # tmp2m__m07 / tmp2m__control -> tmp2m__mean ; everything else unchanged.
    # (Percentile __p{NN} -> base mapping is specified here but wired only when
    #  Tier 2's precondition is met.)
```

`_packing_config` gains the fallback: exact `(model, var)` lookup first; on miss, retry with the normalized id **only when the id carries a member suffix**. Members and mean therefore share packing constants structurally (silent-corruption class eliminated; spike verified member bytes == mean-packed bytes).

**Verified consequence that makes this the load-bearing change:** `grid_supported` → `grid_supported_pair` → packing membership, and `_iter_grid_variable_run_roots` gates on `grid_supported`. With the fallback in place, member var dirs are automatically recognized by `build_grid_manifests_for_run_root` (per-member grid manifests for free) and `read_binary_sample_value`/`_decode_values` decode member frames with no caller-side normalization (the spike had to do this locally; production won't).

Guards:
- **No new `_PACKING_BY_MODEL_VAR` entries** for members — canary scope derivation (keyed off packing keys) is untouched. The canary's scope classifier gains member-suffix awareness via the same helper when member ids first appear on disk (plan §4.1 lesson).
- **Probability grids never fall through the fallback** (plan §3.4): the `("gefs"/"eps", "{var}__prob_gt_*")` band is an explicit entry — `scale=0.1, offset=0.0, nodata=65535, units="%"` — specified here, added only with Tier 2.
- Tests: suffix round-trips (m01/m30/control/passthrough/malformed), member encode/decode == mean packing identity, canary `_scope_for_model` output unchanged, manifest iteration picks up a member dir.

## 5. GEFS member pass: placement, freshness, resumability (R4, R5)

**Placement.** A `run_member_pass(...)` hook in the GEFS scheduler poll loop, eligible only when the current run's mean catchup is complete (the `catchup_complete` publish state) — "strictly after mean publish" is structural, not scheduled. GEFS cycles every 6 h and the mean run completes ~2.5 h in; the pass needs ~12–15 min of the remaining idle window (spike-measured).

**Freshness protection (the plan's untouchable constraint).** Between every (member, fh) work unit the pass checks: (a) a newer run has been detected → stop immediately (the poll loop proceeds to the new mean build; the superseded run's member pass never resumes — meteograms use latest-run members only), (b) shutdown/config-reload signals. Fetch parallelism default 2 (max 4) with the spike's backoff schedule; upstream showed zero throttling at this rate.

**Worklist and resumability.** Worklist = enabled member vars × roster × `scheduled_fhs_for_var(var, cycle_hour)` (never constants). A frame is skipped when its slim `.bin` + meta already exist and are size-sane (spike semantics) — the pass is idempotent and re-entrant on every poll iteration until complete, so a crash/restart (schedulers restart after every completed build) or preemption resumes naturally. Fetch failures are recorded and retried on the next pass, never fatal. Gate failures are never retried blindly — the frame is absent, loudly logged, and the acceptance criterion "zero gate bypasses" is checked per run.

**Write target and promote.** Members are written into the **staging** run root. Verified: `_promote_run` merges staging over published via hardlink copytree (`_copy_or_link_file`), so member files ride the existing manifest-build + promote step the pass triggers on completion (and at most once mid-pass for early meteogram availability — implementation may batch per member). Writing directly to published is rejected: it would break the invariant that only promote writes the published tree and that published is reconstructible from staging∪published.

**Retention (resolved: 6-run parity).** Verified: `_enforce_run_retention` rmtree's whole run dirs beyond `keep_runs` on both staging and published. Members live inside run roots → **parity with mean retention by construction, zero new retention code**; sweep cost measured at 0.76 s per 4,030 files. *Note for D3 (corrected on review, code-verified):* although `DEFAULT_KEEP_RUNS = 4`, `_resolved_keep_runs_for_scheduler_plugin` maps ensemble-category plugins (GEFS/EPS both have `ensemble` capability blocks) to `ENSEMBLE_CATEGORY_KEEP_RUNS = 6` whenever no explicit non-default override is supplied — so the effective default for these models is already the 6 runs the budget assumed. Residual check only: confirm the prod env sets no explicit non-default `keep_runs` override for GEFS/EPS (if it does, align it to 6 or revise the budget language). Not a design issue.

**Memory.** The pass runs inside the GEFS scheduler unit (cgroup caps apply to the whole unit). Spike peak +~0.5 GiB, sequential with (never concurrent to) mean build work; GEFS build peak ≈ 1.1 GiB against `MemoryHigh=3G` — comfortable. Existing `CARTOSKY_FRAME_MEMORY_AUDIT` checkpoints are reused in the member path.

**Fetch bundling (plan §3.6).** With Phase 3 starting as tmp2m-only, per-var fetch is used initially (spike-proven: 2,009 requests, zero failures). Member-bundled fetch — one subset per (member, fh) covering all enabled member vars via a combined search pattern — keeps the request count at 31×65 regardless of variable count. The byte-range subset machinery already accepts multiple inventory rows (code-verified). **Per D5: bundling is a hard prerequisite for enabling the second member variable** (`precip_total`/`snowfall_total`) — the allowlist must not grow past tmp2m until it lands.

## 6. EPS interleaved member encode (R6 — Phase 4 preview, designed now)

Per plan §3.6 and the §2.2 correction (50 pf members, no control, no cf fetch):

- During the bundle build, the mean fetch already writes the pf subset (`*.cartosky_pf.grib2`, all 50 bands) and holds its path. The fetch layer records `(subset_path, band→member-number mapping)` into the `FetchContext` for member-enabled vars.
- **Band order is byte-offset order, not member order** (verified: `_download_subset_with_inventory_rows` sorts ranges by `start_byte`). The mapping must be captured from the inventory rows' `number` fields at download time — encoding "band k = member k" would be a silent mislabeling bug. This is the one genuinely new correctness invariant in the EPS path.
- Member encode runs **per fh at the end of the bundle build**, after bundle variables release their arrays (Phase 0 memory correction: synoptic bundles plateau 2.5–2.6 GiB against `MemoryHigh=3G`). One band at a time: read band → warp → gate → slim write (~3.4 MB decoded per member; ~50 × ~35 ms per fh). Same-build subset reads don't depend on long-term Herbie cache survival (the plan's prohibition targets separate later passes).
- Backstop if observation shows pressure: raise EPS `MemoryHigh` to 3.5G (`MemoryMax=4G` already permits) — a server drop-in change, Brian's call (D2), validated by close memory observation on Phase 4's first capped synoptic build (plan requirement).
- Completeness expectation keys off the actual pf subset member count (assert 50; a deviation is logged loudly — upstream roster changes should be a page, not a silent shift).

## 7. Registration, manifest, and views (R7 — resolves open decision #5)

**Recommendation: members-as-metadata, not per-member catalog entries.**

- Per-member var dirs get **ordinary grid manifests** via the existing `build_grid_manifests_for_run_root` — free once R3 lands. Per-member frame availability (a member frame can be individually absent after a gate failure) is read from each member's own manifest; no globbing.
- The canonical variable's capability `ensemble` block gains a descriptor, e.g. GEFS tmp2m: `"members": {"count": 30, "control": true, "prefix": "m", "enabled": true}` (EPS: `count: 50, control: false`). Consumers (meteogram `include_members`, any future map consumer) enumerate ids deterministically (`{var}__m01…mNN`, `+ __control` when `control: true`) and then consult member manifests for frames.
- Rejected: full per-member capability/catalog entries — 30–50 entries per var per model, bootstrap payload bloat, canary scope churn, and no consumer needs them.

**`supported_views` (D1 — deliberate deviation from the plan's Phase 2 bullet wording).** Recommend map-facing `supported_views` stays `["mean"]`. Under Tier 1 there is no servable "members" map view — adding one to capabilities would leak a non-functional view into capabilities-driven UI. The `members` descriptor above carries everything the API layer needs.

**D1 implementation note (from review, code-verified 2026-07-06 — REQUIRED in Phase 3 plumbing or Phase 5):** the meteogram's current gate, `_model_supports_members` in `forecast_page.py`, probes `plugin.supported_ensemble_views("tmp2m")` for `"members"`. With `supported_views` staying `["mean"]`, that probe must be repointed at the `ensemble.members` descriptor — otherwise `include_members=true` keeps returning 400 even after member frames exist on disk. Track this as an explicit Phase 3 checklist item so it cannot fall between Phase 3 (pipeline) and Phase 5 (meteogram).

## 8. Configuration and rollout (R8)

- `CARTOSKY_MEMBER_PUBLISH_MODELS` — comma-separated model allowlist, **empty default = feature off** (same pattern as `CARTOSKY_BINARY_SAMPLING_MODELS`). Removing a model is the kill switch; already-published member frames age out with retention.
- Enabled member variables per model come from the plugin capability metadata (the `members` descriptor's presence/`enabled`), keeping catalog and scheduler on one source of truth.
- Phase 3 rollout: `gefs` + `tmp2m` only → acceptance criteria green across ≥2 consecutive runs (plan) → extend to `precip_total`, `snowfall_total` (enables bundled fetch) → Phase 4 EPS.
- Operational note carried from the spike: any member tooling run *outside* the scheduler unit (backfills, canaries) must use an isolated Herbie cache; the scheduler unit itself owns its cache, so the production pass needs nothing special.

## 9. Observability and acceptance

- Per-pass summary log line: run, vars, frames written/resumed/gate-failed/fetch-failed, wall, fetch retry count, RSS peak — mirroring the spike's `results.json` fields so Phase 3 verification can diff against spike baselines.
- Gate failures: ERROR-level with member/fh identity (never skipped, never bypassed).
- Acceptance criteria are the plan's (Phases 3–4 block) unchanged, checked per run: full expected member frames per schedule, zero gate bypasses, mean publish latency unchanged, RSS within caps, disk delta within the Tier 1 sign-off, retention removes member dirs, sampler spot-checks (interior/near-edge/out-of-coverage) pass.

## 10. Non-preclusion checks (Tier 2, global)

- **Tier 2:** stats pass (plan §3.3) will consume member manifests + the completeness gate; nothing here blocks it. The probability packing band is specified (Section 4) but not added. The percentile-performance precondition is Phase 6's to satisfy.
- **Global:** no region constants anywhere in the member path — region flows from the plugin, geometry from per-frame meta, fhs from `scheduled_fhs_for_var`, rosters from config/subset counts. Budget re-approval at global rollout per plan open decision #8.

---

## 11. Decisions (recorded 2026-07-06 — Brian, concurring with independent Codex review)

| # | Decision | Outcome |
|---|----------|---------|
| D1 | `supported_views` stays `["mean"]`; members exposed via `ensemble.members` descriptor | **APPROVED, with required implementation note** (Section 7): repoint `_model_supports_members` (`forecast_page.py`) at the descriptor in Phase 3 plumbing or Phase 5, else `include_members=true` keeps returning 400 after members exist. Explicit Phase 3 checklist item. |
| D2 | EPS `MemoryHigh` 3.5G backstop (server drop-in) | **APPROVED as a conditional lever** — apply only if Phase 4's first capped synoptic build shows pressure; no premature server change. |
| D3 | Deployed GEFS/EPS retention vs. the 6-run budget figure | **APPROVED as "confirm prod"** — risk lower than first written: ensemble-category plugins already resolve to `ENSEMBLE_CATEGORY_KEEP_RUNS = 6` absent an explicit override (Section 5, code-verified). Residual check: confirm the prod env sets no explicit non-default override; if one exists, align it to 6 or revise budget language. Not a design issue. |
| D4 | Newer-run preemption check between every (member, fh) frame | **APPROVED** (~0.7 s frames make this a clean freshness guard). |
| D5 | Defer member-bundled fetch until the second GEFS member variable | **APPROVED** — and bundling is a **hard prerequisite** before enabling `precip_total`/`snowfall_total` (Section 5). |

Sign-off: Brian Austin (recorded at Brian's direction, concurring with Codex review) — Date: 2026-07-06

---

## 12. Addendum — derived member variables + bundled fetch (pre-implementation, awaiting D6)

> Added 2026-07-06, before enabling `precip_total`/`snowfall_total` members.
> The original Section 5 treated bundled fetch as a fetch-shape change; this
> addendum covers what it actually entails, because **both remaining member
> variables are derived**, which the R2 build shape (fetch → convert → warp →
> gate → write) does not handle.

**Problem (code-verified 2026-07-06):**
- `precip_total` = cumulative APCP sum over all steps up to fh (kg/m² → in);
  `snowfall_total` = 10:1 cumulative with csnow endpoint sampling
  (`step_endpoints`, `skip_zero_hour_sample`, `min_step_lwe_kgm2=0.01`, SLR
  10 — hints on the GEFS var specs). A member's fh-384 frame depends on that
  member's fields at every prior step.
- The production derive strategies cannot be reused as-is for members:
  (a) component fetches resolve the MEAN artifacts — `apcp_step__mean`'s
  search pattern contains `":ens mean:"`, which member (`gepNN`) files do
  not carry; (b) the FetchContext cumulative caches
  (`resolved_apcp_cache`, `kuchera_cumulative_cache`) are keyed
  `(model, run, var, fh, grid_key)` with **no member identity** — reuse
  would silently mix members' accumulations, the exact silently-wrong-stats
  failure class the plan treats as worst-case.

**Option A — member-sequential derive loop in `builder/members.py` (RECOMMENDED):**
- Per member, process fhs in ascending order holding running cumulative
  state (precip kg/m² sum; snow inches sum) on the native grid.
- Per (member, fh), ONE bundled subset download — combined search pattern
  (TMP + APCP + CSNOW), band→field mapping from GRIB band tags — which IS
  D5's bundled fetch: 31×65 requests for all three variables. tmp2m writes
  directly from the TMP field; precip/snow update the running sums and
  write warped cumulative frames.
- Cumulative semantics are **parameterized from the same GEFS var-spec
  hints the mean path reads** (slr, sample mode, min_step_lwe), so a future
  hint change flows to both; only the loop mechanics are member-local.
- **Mandatory parity test:** identical synthetic APCP/CSNOW step inputs fed
  through the production derive strategies and the member loop must produce
  equal fields — converting the duplicate-semantics divergence risk into a
  tested invariant.
- Resume for cumulative vars mirrors production practice (production itself
  reloads prior cumulative bases from staged artifacts): the resume base is
  the decode of the member's own last complete cumulative frame; the
  packing quantization (0.01 in precip / 0.1 in snow) is a one-time base
  offset, not a compounding error.
- Parallelism shifts to per-member (a member's fh sequence is
  order-dependent); workers=2 → two members in flight. Frame counts and
  preemption/resume semantics otherwise unchanged. precip/snow schedules
  have `min_fh=6` (64 fhs); the fh-0 bundle carries TMP only.
- A bundle fetch failure marks that (member, fh) absent for all three vars;
  retried next pass, as today.

**Option B — thread member identity through production derive/fetch (REJECTED):**
single source of derive truth, but it modifies the hottest mean-path
machinery, requires member-aware FetchContext cache keys (today's keys
would silently cross-contaminate members), and still needs member-pattern
component specs — a much larger blast radius for the same output.

| # | Decision | Recommendation | Approved? |
|---|----------|----------------|-----------|
| D6 | Derived member variables via Option A (member-sequential derive + bundled multi-field fetch in members.py, hint-parameterized semantics, production-parity test required) | Yes | **APPROVED (Brian, 2026-07-06)** — implemented same day; parity tests pin the member step math to the production derive strategies |

Implementation notes recorded post-D6 (2026-07-06): cumulative state accumulates in
target-grid (warped) space — bilinear warping is linear, so accumulating warped
steps equals warping the accumulated sum up to nodata-edge effects; this is what
makes the approved decode-based resume rebase possible and halves the warp count.
The member APCP search pattern is deliberately NOT end-anchored (member idx lines
carry an ENS suffix, unlike deterministic GFS lines — a silent-mismatch trap).
A bundle failure at a cumulative step aborts that member's remaining fhs for the
pass (the chain cannot continue past a missing step); the next pass rebases and
continues.

---

## 13. Addendum — Phase 4 EPS members via the decoupled pf-subset pass (D7)

> Added 2026-07-06 with the Phase 4 implementation. This amends Section 6's
> interleave wording; the plan's §3.6 fetch-economics rationale is honored,
> the mechanism differs.

**What Phase 4 actually needs (code-verified):** every EPS member variable is
a DIRECT field — `tmp2m` (2t bands) and `precip_total` (tp bands, natively
run-cumulative in ECMWF output, `derived=False`, `conversion="m_to_in"`).
There is no member derive chain at all, and no control member exists
upstream (§2.2). Each `(var, fh)` pf subset (`*.cartosky_pf.grib2`) that the
MEAN build downloads already contains all 50 member bands.

**D7 — decouple instead of interleave.** EPS members are built by the SAME
scheduler member pass Phase 3 shipped (model-generic hook, allowlist,
resume/preemption/promote semantics unchanged), in a pf-subset mode:

- Unit of work = `(var, fh)` (not per-member): resolve the pf subset at the
  SAME deterministic path the mean fetch used; **reuse it from the Herbie
  cache** (the pass runs minutes after catchup completes, so the current
  run's subsets are present) or re-download the same byte ranges via the
  same production primitive on a miss. One subset yields all 50 members —
  request count ≈ vars × fhs index reads, ~zero payload re-download in the
  normal case. §3.6's economics preserved.
- **Band→member mapping is re-derived from the same .index**: bands in the
  subset follow ascending byte order (how
  `_download_subset_with_inventory_rows` writes), so pf rows sorted by
  `start_byte` give the member `number` per band. Validated per subset:
  band count == row count == descriptor count (50), numbers unique — any
  mismatch fails the unit loudly, never mislabels.
- Per band: `_read_rasterio_band` (the exact primitive the mean aggregation
  reads bands with) → `convert_units` (same capability conversion as the
  mean) → warp → enforced gate → slim write `{var}__m{NN}`.

**Why not the Section 6 interleave:** it modifies the EPS bundle build — the
memory-tight unit (2.5–2.6 GiB plateau vs `MemoryHigh=3G`) whose placement
risk the plan itself flagged — and threads member context through the fetch
layer's hottest path. The decoupled pass runs when the scheduler is idle
(post-catchup RSS ~200–400 MB), holds one band at a time, and required ZERO
changes to the mean/bundle/scheduler code (eps.py descriptors + members.py
pf mode only). The plan's warning against a later pass was about
cache-survival dependence and re-download cost; the deterministic-path
reuse + graceful range re-download removes both. Estimated pass wall:
~3–5 min per synoptic run (122 units × 50 bands), well inside the idle
window; D2's `MemoryHigh` lever remains pre-approved but is not expected
to be needed.

**Scope:** `tmp2m` + `precip_total` members (the vars EPS publishes means
for and the Ensembles tab consumes). `snowfall_total` stays gated behind
its plugin/derive deliverable (plan open decision #4 note). Enable on prod
by appending `eps` to `CARTOSKY_MEMBER_PUBLISH_MODELS`; the frontend's
`MEMBER_PLUME_MODELS` gains `"eps"` after the first green EPS run.

| # | Decision | Recommendation | Approved? |
|---|----------|----------------|-----------|
| D7 | EPS members via the decoupled pf-subset member pass (deterministic subset reuse + index-derived band mapping) instead of interleaving member encode into the bundle build | Yes — same outputs, zero hot-path blast radius, §3.6 economics preserved | ✅ 2026-07-08 (approved after the prod gate closed: 12z synoptic + 18z off-cycle both green first-pass) |

---

## 14. Addendum — idle-time member backfill for superseded runs (D8)

> Added 2026-07-08, after prod incident: EPS run 20260708_00z stuck at
> 654/705 mean targets for ~5 h (upstream ECMWF index defect — the fh060+
> step files' final GRIB message, an r700 pf band, was one byte shorter than
> its own `.index` claimed; our range validator correctly refused it every
> retry), then superseded by 06z. The post-complete member hook never fired
> and the mean loop never revisits superseded runs, so the run showed a
> mean-only plume permanently: 51 unbuildable mean frames blocked 5,999
> buildable member frames.

**D8 — backfill semantics.** During the idle branch (latest run complete),
after the latest run's own member hook, the scheduler scans the kept
published runs newest-first (skipping the latest) and runs ONE pending
run's member pass per idle iteration, with two deviations from the normal
pass:

- **Mean-coverage cap** (`mean_coverage_only`): the plan builds members
  only for fhs whose MEAN artifact exists (staging or published) — exactly
  the frames the plume can display (member candidate fhs come from the mean
  series), and exactly the pf subsets likely still in the Herbie cache.
  The pending scans apply the same cap so a mean-incomplete run stops
  reading as pending once its covered frames exist. The normal
  post-complete pass is unchanged (coverage == schedule there).
- **Probe reference = the LATEST run**: preempt when a run newer than the
  latest appears (new mean work imminent), not newer than the backfill
  target — the latter would preempt instantly and the backfill would never
  run.

Approved by Brian 2026-07-08 ("Option 1 is fine for a backfill resolve")
after the incident diagnosis. Not changed: the mean loop still never
rebuilds a superseded run's missing MEAN frames — the 51 rh700 frames stay
absent (upstream defect); the backfill only closes the member gap for the
654 frames the run did publish.

*Document version: 2026-07-06 (draft → approved same day with review tweaks: D1 API-probe note, D3 clarification, D5 bundling prerequisite; Section 12 addendum approved and implemented same day; Section 13 added with the Phase 4 implementation, D7 approved 2026-07-08; Section 14 backfill addendum added and approved 2026-07-08).*
