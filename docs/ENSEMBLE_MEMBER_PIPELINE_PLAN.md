# Ensemble Member Pipeline Plan (GEFS + EPS)

> **Audience:** AI agents executing discrete phases. Each phase is self-contained and must pass its verification gate before the next begins.
>
> **Status (2026-07-04):** Design/pre-spike. **Nothing in this plan may start until Phase 0's prerequisites pass** — in particular, GEFS and EPS must complete the binary-sampling migration (`docs/VALUE_COG_TO_BINARY_SAMPLING_MIGRATION_PLAN.md`) and be live on `CARTOSKY_BINARY_SAMPLING_MODELS` with COG writes off.
>
> **Status update (2026-07-06):** Phase 0 and Phase 1 (sizing spike) are **complete**. Brian's decision is recorded in `docs/ENSEMBLE_MEMBER_SIZING_SPIKE.md` Section 10: **Tier 1 GO at 6-run parity retention; Tier 2 CONDITIONAL GO, also at 6-run parity (performant percentile implementation required first — see Phase 6 precondition); Tier 3 NO GO (server cannot support it until resources are expanded); global coverage deferred for ALL tiers**, with the standing requirement that design/planning keep global support a first-class future path. Spike-measured corrections are folded into this document in place, dated 2026-07-06 — the largest being that **EPS has no upstream control member (roster = 50, not 51; §2.2)**.
>
> **Hard gate (unchanged from the original Model Guidance plan):** no member scheduler work of any kind — beyond the explicitly scoped sizing spike — until the spike is complete and Brian's go-ahead is recorded in the spike doc. **Satisfied 2026-07-06** (scope of the go-ahead per the status update above).
>
> **Document lineage:** This plan supersedes the "Individual member data pipeline" section and Phase 3 scheduler/storage material of `docs/MODEL_GUIDANCE_IMPLEMENTATION_PLAN.md`, which were written pre-migration and specified member **value COGs** — an artifact allowlisted models no longer produce. This document follows the migration plan's convention: corrections are documented in place, and claims verified against code are marked as such.

---

## 1. Scope: what this pipeline serves

Per-member ensemble data is **shared infrastructure with three consumer families**, not a meteogram feature. Designing for only the first family would paint us into a corner before the fall/winter busy season.

| # | Consumer family | Serving shape | Cost profile |
|---|-----------------|---------------|--------------|
| 1 | **Meteogram members** (Model Guidance Phase 3: spaghetti plumes, snowfall member histogram/detail) | Server-side point sampling of member binaries; members never leave the server as rasters | Cheapest; slim member artifacts suffice |
| 2 | **Derived stats grids for maps** (percentile maps e.g. snow P25/P50/P75; probability-of-exceedance maps e.g. P(QPF > 1.0"); MSLP member low locations) | Computed **from** members, published **as ordinary single grid artifacts** through the normal pipeline — colorize, sidecar, grid binary, WebGL, Cloudflare caching all unchanged. Client never touches a member raster. | Cheap (~one extra "variable" per stat); highest winter user value |
| 3 | **Per-member browse maps** (WB-style: scrub through individual member snowfall maps in the viewer) | Members become first-class **served** artifacts: brotli sidecars, sidecar JSON with colormap meta, manifest entries, CF `HIT` | Expensive (31–50× served-artifact surface); supported by design, **NO GO recorded 2026-07-06** — see Appendix A |

Two standing design constraints from the roadmap:

- **Global map support is planned.** The binary migration was partly motivated by it. Nothing in this pipeline may hardcode CONUS or `na` assumptions — region is always a parameter (it already is, via `MODEL_REGISTRY` + `get_grid_params`), the per-frame meta transform is the geometry authority, and all footprint budgets in this doc carry a global multiplier (Section 6) so decisions made now survive the region expansion. When global regions land, retention and member-variable scope must be re-evaluated against that multiplier — that re-evaluation is an explicit checklist item, not an assumption. **Decision (Brian, 2026-07-06): global coverage is NOT supported for any member tier at this time; this design constraint stands in full so incorporation is straightforward when global support arrives.**
- **Mean-product freshness is untouchable.** Member work never delays mean publish. The map's core promise (fresh mean/deterministic guidance) outranks every member product.

---

## 2. Verified current state (code-verified 2026-07-04)

Every claim below was verified by direct read of `gefs.py`, `eps.py`, `services/scheduler.py`, `services/grid.py`, `services/builder/pipeline.py`, and `services/builder/fetch.py` — not assumed.

### 2.1 Publish path and artifact ids

Both models publish exclusively under `__mean` runtime ids via `resolve_runtime_var_id` + `ensemble.artifact_map` (`supported_views: ["mean"]` everywhere). Full audit: migration plan, "Phase G audit — GEFS and EPS static readiness." Frame schedules (from that audit, authoritative source `scheduled_fhs_for_var`):

- **GEFS:** fh 0–384 step 6 = 65 frames, every cycle, cycle-hour independent. Region `na`, 25 km, EPSG:3857.
- **EPS:** synoptic (00z/12z) fh 0–360 step 6 = 61 frames; off-cycle (06z/18z) fh 0–144 = 25 frames. Region `na`, 18 km, EPSG:3857. Three anomaly variables legitimately publish **zero** frames off-cycle; EPS publishes 390–450 min after nominal cycle time.

### 2.2 The EPS finding that shapes everything: member data is already downloaded

`_fetch_ecmwf_pf_mean_variable` (`builder/fetch.py`) byte-range-downloads a subset GRIB containing **all perturbed member bands** per `(var, fh)` (inventory rows filtered to `type == "pf"`), caches it on disk (`*.cartosky_pf.grib2` next to the Herbie-local path, re-download guarded by `_subset_file_status` + a download lock), then `_aggregate_grib_subset_mean` streams the bands **one member at a time** to compute the mean — and discards the member fields.

**Consequence:** per-member EPS has **near-zero incremental fetch cost** if member encoding happens in the same frame build, iterating the bands of the already-downloaded subset. The expensive-looking model (51 members) is the cheap one for fetch. The EPS **control** member is *not* in the pf subset (the filter excludes `type == "cf"`); control fetch is a small new inventory selection.

**Correction (spike-verified 2026-07-06):** the EPS control member **does not exist upstream at all**. The ECMWF open-data enfo `ef` index contains only `type == "pf"` rows — members 1–50, contiguous, zero `cf` rows — verified in the spike's prod mini-check (azure mirror) and against the live index on `data.ecmwf.int` for two cycles (2026-07-05 12z fh0: 8,500 rows, all pf; 00z fh24: same). There is no cf row for the pf filter to exclude and no control fetch to build. **EPS member roster = 50 perturbed members; every "51 incl. control" figure in this document is corrected to 50** (≈2% footprint reduction). The `type == "cf"` selection mechanism was implemented and works — there is simply nothing for it to select. Resolves open decision #2 negatively.

### 2.3 The GEFS reality: members are net-new fetch load

GEFS mean uses the upstream **precomputed `geavg` product** (`herbie_kwargs["member"] = "mean"`, verified in `gefs.py`'s `herbie_request`). Member fields live in separate upstream files (`gep01`–`gep30`, `gec00`). Per-member GEFS therefore requires ~31 Herbie subset downloads × 65 fh per run — **~2,015 HTTP fetches per member-variable per run if fetched per-variable**. Request count, not bytes, is the risk (upstream rate limits). Mitigation is member-bundled fetch (Section 3.6).

**Spike measurement (2026-07-06):** the full per-var 31×65 pattern (2,009 fresh fetches) completed with **zero failures, zero retries, and no throttling** at parallelism 2 (~0.7 s/fetch, aws served every request on attempt 1; batch wall 12.2 min). The rate-limit risk did not materialize at this scale on a single run; member-bundled fetch remains the multi-var design (Section 3.6). GEFS roster confirmed 30 pf + 1 control; control kwarg `member=0` → `gec00` (herbie 2026.3.0) — resolves open decision #3.

### 2.4 What a frame build does today (and what members don't need)

`build_frame` (`builder/pipeline.py`) per frame: fetch/derive → warp → **colorize** (`float_to_rgba` over the full array; RGBA output discarded, only metadata kept for the sidecar) → quality gate → **value COG write + COG gates** *(skipped for allowlisted models; the pre-encode array sanity gate is **enforced** for them — a failure rejects the frame)* → **contour metadata** → **pressure-center detection** → **sidecar JSON** → grid binary write.

`write_grid_frame_for_run_root` (`grid.py`) per frame writes: `fh{NNN}.l0.u16.bin` (atomic tmp+replace) + optional gzip sidecar + optional brotli sidecar (env-gated) + `fh{NNN}.l0.meta.json` (`format_version`, dims, bbox, **effective post-upscale transform**, projection, display-prep meta). The meta transform is per-frame and region-agnostic — mixed resolutions and future global regions are already handled by the read side.

**Members consumed only by samplers and stats passes need none of:** colorize, contours, pressure centers, sidecar JSON, compression sidecars, or display-prep upscaling. They **do** need the enforced pre-encode sanity gate — one silently-bad member poisons every distribution, percentile, and probability product downstream.

### 2.5 Packing lookup is exact-match

`_packing_config` does an exact `(model, var)` dict lookup in `_PACKING_BY_MODEL_VAR`. `tmp2m__m01` etc. have **no entries** — member ids cannot be encoded today. Resolution strategy in Section 3.4 (do **not** register ~hundreds of dict entries).

### 2.6 Catalog gaps relevant to the product list

- **Neither model has MSLP** (`mslp`/`prmsl` absent from both catalogs; GEFS = 18 published artifacts, EPS = 14, per the Phase G audit). "MSLP + member low locations" requires a **net-new variable for both models**, which independently triggers the standing new-data-source sizing-spike gate. Two gates, not one.
- **EPS has no `snowfall_total`** even as a mean product. It is a plugin + derive deliverable before it can be a member deliverable (open decision #3, carried over from the Model Guidance plan).

---

## 3. Design principles (LOCKED)

### 3.1 Member artifacts are grid binaries, never value COGs

`fh{NNN}.l0.u16.bin` + meta sidecar under `published/{model}/{run}/{var}__m{NN}/`. Members are born post-migration; there is no COG era for them. All member reads go through the migration's single decode authority (`_decode_values`) via the binary sampler.

### 3.2 Profile-parameterized member build ("slim" default)

The member build path takes a **build profile** — explicit flags for which stages run: `colorize`, `contours`, `pressure_centers`, `sidecar_json`, `compression_sidecars`, `display_prep`. Default member profile: **all off** (fetch/derive → warp → enforced pre-encode gate → encode → `.bin` + meta only).

Why a profile and not a hardcoded slim path: consumer family 3 (browse maps) would need full-profile members later. Most slim-profile skips are **two-way doors** — sidecars/sidecar JSON are generated from data available at write time, so flipping a variable to full profile self-corrects within one retention turnover, or via a trivial backfill script (`.bin` → `.br`). The profile flag makes that a config change, not a rewrite.

**Implementation constraint (verified 2026-07-05, flagged by design review):** the profile cannot be bolted onto the existing write path unchanged. `write_grid_frame_for_run_root` (`grid.py`) unconditionally calls `prepare_grid_display_values` (display-prep is a per-`(model, var)` table lookup inside the call, not a caller choice) and gates gz/brotli sidecars on **process-global env flags** (`GRID_GZIP_SIDECARS_ENABLED`/`GRID_BROTLI_SIDECARS_ENABLED`), which cannot differ per artifact within one scheduler process. The Phase 2 design must produce a genuinely profile-aware write/build path — per-call profile parameter (or a member-specific variant sharing the encode/atomic-write/meta internals) — without changing default behavior for every existing mean/deterministic frame. Env flags stay authoritative for non-member frames.

**The one genuine fork is `display_prep`:** GEFS `precip_total__mean`/`snowfall_total__mean` are upscale-3× for map smoothness (9× pixels). Slim members at native 1× are correct for sampling and for stats computation; a *served* 25 km member snowfall map at 1× would look chunkier than the mean beside it, and 3× members cost ~9× disk. Mixed resolutions break nothing (sampler and client are transform-driven per frame), so this defers cleanly — but it is measured in the spike (Section 7) and decided per-variable only if/when browse maps are scheduled. Open decision #6.

### 3.3 Stats grids are a second pass over published member binaries

Percentile and probability grids are computed by **reading published member `.bin` frames** (decode via `_decode_values` → per-pixel stats across the member axis → publish the result as an ordinary derived variable through the **normal, full-profile** pipeline). Not computed inline during fetch/warp.

Why: decouples stats entirely from the member build profile and fetch orchestration; tolerates a late/backfilled member frame; reuses the decode primitive exactly as the migration designed; and the memory cost is trivial — all members of one fh in float32 ≈ **~170 MB for EPS (50 × ~3.4 MB; corrected to the 50-member roster)**, **~56 MB for GEFS (31 × ~1.8 MB)** at `na` resolution. Quantization noise from 0.1-precision decoded inputs is irrelevant at these product scales. Stats outputs are grid binaries served like any other frame — **they must get `CF-Cache-Status: HIT`**, same rules as every grid binary.

**Spike measurement (2026-07-06):** memory confirmed trivial (GEFS 31-member stack 53 MiB, +145 MiB process RSS, decode 0.10 s) — but the naive `np.nanpercentile` compute took **13.7 s for one fh** (per-pixel Python fallback in the presence of NaNs; pixel-count-bound, not member-count-bound). This is the basis of the Tier 2 CONDITIONAL GO: a performant nan-aware percentile implementation is a Phase 6 precondition.

**Runtime completeness gate (required):** before publishing a stat frame for an fh, the stats pass verifies the **full expected member set** is present for that fh (member count per `scheduled_fhs_for_var` and the model's member roster). If incomplete — e.g. the scheduler unit was `MemoryMax`-killed mid-member-loop, or a member frame failed the pre-encode gate — skip that fh and retry on the next pass; never publish a percentile/probability grid computed from a partial member set. Silently-wrong stats on the map is the worst failure mode this pipeline can produce.

### 3.4 Packing resolution: suffix normalization, not entry explosion

Extend `_packing_config` (or a wrapper it calls) with a fallback: if `(model, var)` misses and `var` ends in `__m{NN}` or `__control`, strip the suffix and resolve the **`__mean` twin's** packing. Members and mean MUST share packing constants — they quantize the same physical field; divergent constants would be a silent-corruption bug class. Percentile grids (`__p{NN}`) resolve the same way to the base variable's packing.

**Probability grids are a new packing band** — a deliberate, explicit `_PACKING_BY_MODEL_VAR` addition (recommended: `scale=0.1, offset=0.0`, units `%`, uint16 → 0.1% precision), audited with the same signed-offset discipline the migration's Phase G addendum mandates. Do not let it fall through any suffix fallback.

The migration's **packing-fix retroactivity addendum applies to member frames identically**: a packing fix does not retro-correct already-published member binaries; they age out with retention. Any stats pass consuming members must therefore run against post-fix frames only if a packing fix landed mid-window.

### 3.5 Naming (LOCKED — see Section 4)

### 3.6 Fetch strategy per model

- **EPS — interleave with the mean build, but place the encode loop deliberately.** Design the member encode to run within the same frame-build lifecycle that downloads the pf subset: one subset read yields mean + N member binaries. Do **not** design a separate later member pass that depends on Herbie cache survival or re-downloads. Control (`cf`) is fetched via its own inventory selection in the same pass. **Memory placement constraint (added 2026-07-05):** EPS synoptic bundle builds already plateau at ~2.5–2.6 GB against `MemoryHigh=3G` — member encode must run where bundle memory allows (e.g. after bundle variables release their arrays), and/or EPS `MemoryHigh` is raised (~3.5G fits under the existing `MemoryMax=4G`). Placement is specified in the Phase 2 design doc and validated by close memory observation on Phase 4's first capped synoptic build (the GEFS-scoped spike cannot answer this). The fetch-economics case for interleaving is unaffected.
- **GEFS — decoupled member loop after mean publish, member-bundled fetch.** Per `(member, fh)`, download **one** subset covering **all** member variables (the byte-range subset machinery already accepts multiple inventory rows), collapsing `vars × 31 × 65` fetches to `31 × 65` per run. The member loop runs strictly after mean publish at reduced priority (`nice -n 10 ionice -c2 -n7`, consistent with canary hygiene) with a parallelism/backoff knob so mean freshness and upstream goodwill are protected.

### 3.7 Region- and global-agnostic implementation

No hardcoded bboxes, region names, or grid dims anywhere in member/stats code: region flows from the model plugin as it does today; frame geometry comes from the per-frame meta. Anchor/point lists used in verification are generated from the model's region bbox, not copied from CONUS lists (lesson already learned in the GEFS/EPS Phase G audit).

---

## 4. Naming and manifest schema (LOCKED)

### 4.1 Runtime var ids

| Kind | Pattern | Examples |
|------|---------|----------|
| Perturbation member | `{var}__m{NN}` (zero-padded 2-digit) | `tmp2m__m01` … `tmp2m__m30` (GEFS), `…__m50` (EPS) |
| Control | `{var}__control` (distinct from `m01`) — **GEFS only**; EPS has no upstream control (§2.2 correction, 2026-07-06) | `tmp2m__control` |
| Percentile stat | `{var}__p{NN}` | `snowfall_total__p25`, `snowfall_total__p50`, `snowfall_total__p75` (set: p10/p25/p50/p75/p90) |
| Probability of exceedance | `{var}__prob_gt_{threshold}` — threshold in the variable's display units, decimal point as `p` | `precip_total__prob_gt_0p50`, `snowfall_total__prob_gt_6p0` |

Var-id parsing/normalization for these suffixes is written **once** (shared helper), used by packing resolution, manifest tooling, and any scope-derivation logic (the canary script's scope filter will need to classify these ids when they exist — same class of lesson as `_ensemble_dead_alias_vars`).

### 4.2 Threshold sets (initial; extensible per-variable)

| Variable | Thresholds (display units, inches) |
|----------|-------------------------------------|
| `precip_total` | 0.10, 0.25, 0.50, 1.00, 1.50, 2.00 |
| `snowfall_total` | 1, 3, 6, 12 |

These supersede nothing — the Model Guidance plan's locked fh windows for the probability **table** (fh 24/168/360) are unchanged and orthogonal; these thresholds define the **map** products and are a superset of the table's QPF thresholds. Adding a threshold later = one new derived variable; no schema change.

### 4.3 Storage layout

```text
published/
  gefs/
    {run}/
      tmp2m__mean/fh000.l0.u16.bin + fh000.l0.meta.json      # existing (post-cutover)
      tmp2m__m01/fh000.l0.u16.bin + fh000.l0.meta.json        # slim member: 2 files/frame
      ...
      tmp2m__m30/… , tmp2m__control/…
      snowfall_total__p50/fh000.l0.u16.bin (+ full-profile artifacts)   # stats: normal pipeline
      precip_total__prob_gt_1p0/…
  eps/
    {run}/  (same pattern; m01–m50 — no control; §2.2 correction 2026-07-06)
```

### 4.4 Manifest

Register member/stat runtime vars so `list_frames` and the meteogram's frame enumeration work unchanged. Recommended shape: stats vars are ordinary catalog/manifest entries (they are ordinary products); members are registered under the canonical var as `members: { count, prefix, control: bool, frames: … }` metadata **or** as full var entries — the design doc (Phase 2) decides, with the constraint that both the meteogram (`include_members`) and any future map consumer can enumerate member frame lists without globbing directories.

---

## 5. Retention (RESOLVED — Brian, 2026-07-06: **6-run parity** for Tier 1 members)

**Decision (2026-07-06):** Tier 1 slim members retained at **6-run parity** with mean products (spike-measured cost ≈ 98 GB at `na` for 3 member vars across both models — 9% of free space at spike time). **Tier 2 stats grids, when their conditional GO is exercised, also run at 6-run parity — not 2-run.** The per-view fallback below stays documented as the lever if the footprint picture changes; it is not needed now.

**Target: parity with mean retention (6 runs).** If the spike's extrapolation shows parity is not comfortably affordable (Section 6 budget), fall back to **per-view retention**: members retained for the latest 1–2 runs while mean products keep 6. That lever cuts member footprint 3–6× and is product-defensible (meteograms use `latest_per_model` only; the run selector simply shows fewer runs for member views). Stats grids are cheap and follow normal retention regardless.

The retention/cleanup job must handle member directories under whichever policy is chosen, and the spike measures sweep duration with member file counts present (Section 7).

---

## 6. Server budgets and planning estimates

**Constraints (2026-07-04):** disk ~878 GB used of 2 TB (~1.1 TB free); RAM 32 GB total, baseline 17–22 GB available depending on scheduler load. Both schedulers are memory-capped via systemd drop-ins (EPS `MemoryHigh=3G`/`MemoryMax=4G`; GEFS `3G`/`3500M`) — note the semantics: `MemoryHigh` throttles via reclaim (slows builds, evicts page cache), `MemoryMax` **kills the unit** (see the stats completeness gate, Section 3.3). Drop-ins are server-side by convention, not repo-tracked (Phase 0 note). Baselines (Grafana per-process memory, corrected 2026-07-05 — see Phase 0): **EPS ≈ 2.5–2.6 GB during synoptic bundle builds (~85% of its `MemoryHigh`; the tight unit)**, GEFS ≈ 1.1 GB peak. Schedulers restart after every completed build, so `systemctl` `MemoryPeak` is a per-window figure and not a valid baseline source.

**Everything below is a planning estimate — the spike replaces these numbers with measurements.** Basis: `na` region grid dims ≈ 680×655 px (GEFS 25 km) and ≈ 945×910 px (EPS 18 km); uint16 → ~0.9 MB (GEFS) / ~1.7 MB (EPS) raw per frame; slim profile = 2 files/frame (`.bin` + meta), no compression sidecars, no display-prep upscale.

**Spike measurements (2026-07-06, full detail in `docs/ENSEMBLE_MEMBER_SIZING_SPIKE.md`):** GEFS slim member frame = 896,148 B `.bin` (682×657, exact) → **1.81 GB/run/var measured** — the estimate below confirmed. Compression sidecars **×1.68 measured** (gz 35.7% + br 32.2% of bin); 3× display-prep **×9.0 measured**. EPS column corrected to the 50-member roster (§2.2): Tier 1 combined ≈ **98 GB at 6-run parity / 33 GB at 2-run** (`na`). Spike-process peak RSS 489 MiB including a 31-member stats pass; promote rename <1 ms; retention sweep 0.76 s per 4,030 files; the concurrent 18z mean build showed no delay vs. the previous day's baseline cycle. Tier 3 extrapolates to ≈456 GB `na` / ≈2.65 TB global at 6-run — the recorded **Tier 3 NO GO** stands until server resources are expanded; **global is deferred for all tiers** (design keeps the global path first-class per Section 1).

| Tier | GEFS (31 members) | EPS (50 members — corrected 2026-07-06, no control) | Combined, 3 member vars, 6-run retention |
|------|-------------------|------------------------|------------------------------------------|
| **Tier 1 — meteogram-only (slim, 1×)** | ~1.8 GB/run/var (65 fh) | ~5.3 GB synoptic / ~2.2 GB off-cycle per run/var | **~100 GB** (GEFS ~32 GB + EPS ~68 GB) |
| **Tier 2 — + stats grids** (≈5 percentiles + ≈4–6 prob thresholds per var, full profile) | +~0.5–1 GB/run total | +~1–2 GB/run total | **+~10–20 GB** — noise relative to Tier 1 |
| **Tier 3 — + full-profile served members** (br sidecars; 3× display-prep on GEFS precip/snow ≈ 9× those variables' pixels) | multiply affected vars ~2–9× | +~40–60% (sidecars) | **several hundred GB — requires its own budget sign-off; not scheduled** |

Per-view retention fallback (members latest 2 runs): Tier 1 drops to roughly **~33 GB**.

**Global multiplier:** full-extent web-mercator (±85°) at these grid spacings ≈ 1603 px (25 km) / 2226 px (18 km) square → **~5.8× the `na` per-frame footprint for both models**. Any go/no-go recorded against Tier numbers must note whether it holds under the global multiplier or requires re-approval at global rollout.

**RAM:** member builds hold one member grid at a time (verified: the EPS mean aggregation already streams band-by-band); expected worker peak ≈ today's. Stats passes hold all members of one fh: ~175 MB (EPS) / ~56 MB (GEFS) plus overhead — comfortably inside headroom, but the spike measures real RSS, not this arithmetic. Inodes: slim GEFS ≈ 31 × 65 × 2 ≈ 4,030 files/run/var — fine for ext4; the operational question is publish-promote and retention-sweep wall time, which the spike measures.

---

## 7. Sizing spike protocol (binary edition)

> **COMPLETE (2026-07-06).** Executed as specified against `gefs/20260705_12z` via `backend/scripts/ensemble_member_sizing_spike.py` (2,015/2,015 frames, zero gate/fetch failures). The deliverable exists with all seven measurements and the recorded decision: **Tier 1 GO at 6-run parity; Tier 2 CONDITIONAL GO at 6-run parity (performant percentile first); Tier 3 NO GO; global deferred for all tiers.** Operational note for future member tooling run outside the scheduler unit: use an isolated Herbie cache (the scheduler's cache is not writable by the operator user; the spike script does this automatically).

One-run GEFS `tmp2m` member publish (`tmp2m__m01`–`__m30` + `tmp2m__control`, all 65 fh), slim profile, via the deploy workflow (never patched on the server), member loop at reduced priority. **Deliverable:** `docs/ENSEMBLE_MEMBER_SIZING_SPIKE.md` with Brian's explicit go/no-go recorded.

> **Warning — stale predecessor script:** `backend/scripts/phase3_sizing_spike.py` is the **value-COG-era** spike from the original Model Guidance plan. It writes member COGs and predates the binary migration. **Do not reuse or extend it for this plan.** Write the binary-era spike fresh (it may crib fetch scaffolding, but its artifact writing, measurement targets, and output schema are obsolete); recommend renaming or banner-deprecating the old script when the new one lands.

Measure and document:

1. Total bytes and file count under `published/gefs/{run}/tmp2m__m*/` + `__control/` (slim profile), and **one member variable additionally written at full profile and at 3× display-prep** for the Tier 3 extrapolation row.
2. End-to-end member-batch publish latency, and confirmation the concurrent/next mean publish was not delayed.
3. Scheduler peak RSS during the member loop, recorded as **headroom against the configured caps** (EPS `MemoryHigh=3G`/`MemoryMax=4G`; GEFS `3G`/`3500M`), plus RSS of a prototype stats pass over the 31 published member frames for one fh. Note whether `MemoryHigh` throttling engaged (build slowdown + reclaim), not just whether `MemoryMax` was hit.
4. **Fetch feasibility:** wall time, failure/retry rate, and any upstream throttling across the ~2,015-fetch pattern (member-bundled if the bundling lands first; per-var otherwise — record which). Confirm upstream member count (expect 30 pf + 1 control) and the Herbie `member` kwarg for GEFS control **and** EPS control.
5. Staging→published promote time and retention-sweep duration with member directories present.
6. **EPS `snowfall_total` feasibility** (direct GRIB field vs derivation complexity) — flag, don't block.
7. Extrapolation table: Tier 1/2/3 × {parity retention, 2-run retention} × {`na`, global ~5.8×}, against the ~1.1 TB free budget.

**Gate:** Brian's sign-off on a specific tier + retention combination, recorded in the spike doc, before any work beyond the spike. **Satisfied 2026-07-06** (spike doc Section 10).

---

## 8. Phases

Each phase gates on the previous. Recommend-first: Phases 2's design doc goes to Brian before implementation.

**Phase 0 — Prerequisites (no member work of any kind before all pass):**
- [x] GEFS and EPS on `CARTOSKY_BINARY_SAMPLING_MODELS`, COG writes off, migration Phase F evidence complete for both — **done (flipped 2026-07-04, post-flip verification complete 2026-07-05; see migration plan Phase G closure)**.
- [x] Scheduler `MemoryHigh`/`MemoryMax` caps deployed for GEFS and EPS — **done (2026-07-04)**: server drop-ins `csky-eps-scheduler.service.d/memory-limits.conf` (`MemoryHigh=3G`, `MemoryMax=4G`) and `csky-gefs-scheduler.service.d/memory-limits.conf` (`MemoryHigh=3G`, `MemoryMax=3500M`). Repo capture of the drop-ins: **won't-do (Brian, 2026-07-05)** — memory caps are applied as server-side drop-ins uniformly across all prod scheduler units and are intentionally not tracked in `deployment/systemd/`; documented elsewhere. Future agents: do not re-flag this as config drift.
- [x] Pre-spike RSS baseline — **recorded 2026-07-05, corrected same day.** *Correction:* the first recording used `systemctl show -p MemoryPeak` (GEFS ≈1.85 GiB, EPS ≈340 MiB) and concluded GEFS was the tight unit — wrong, because schedulers restart after every completed build, so `MemoryPeak` covers only the since-last-restart window (EPS's window contained no build), and cgroup peak includes page cache atop RSS. Grafana per-process memory is the authoritative baseline: **EPS ≈ 2.5–2.6 GB sustained plateau during synoptic builds** (idle ~200–400 MB) — **~85% of `MemoryHigh=3G` on RSS alone, ~400–500 MB headroom to throttle, and cgroup accounting adds page cache on top**; **GEFS ≈ 1.1 GB peak** (other builds ~600–650 MB) — comfortable. EPS is the tight scheduler. The earlier claim that EPS's low number "empirically confirms the streaming pf aggregation" is retracted — streaming is code-verified, but EPS builds run hot regardless because every EPS variable is bundle-built and the bundle holds many variables' arrays and derive/climatology inputs simultaneously. **Consequence:** the Section 3.6 EPS interleave must place member encode where bundle memory allows — after bundle variables release, and/or with EPS `MemoryHigh` raised (~3.5G; `MemoryMax=4G` already permits) — specified in the Phase 2 design doc, validated on Phase 4's first capped synoptic build.
- [x] One post-cutover run per model measured on prod — **done (2026-07-05, runs `gefs/20260705_00z` and `eps/20260705_00z`, first fully binary-only run each; distinct from the migration's COG-era item 7).** Full per-variable `du` output retained in ops notes; headline figures: **GEFS** typical Group 1 variable 74–104 MB/run (≈ 1.1–1.6 MB/frame full profile → ≈ 0.85–0.9 MB raw `.bin`), Group 2 confirmed as the outliers exactly as predicted — `precip_total__mean` 717 MB (≈11 MB/frame) and `snowfall_total__mean` 536 MB (≈8.2 MB/frame), the 3×-upscale ≈ 9×-pixel cost measured; GEFS per-run binary total ≈ 2.77 GB. **EPS** typical variable 129–181 MB/run synoptic (≈ 2.1–3.0 MB/frame → ≈ 1.65–1.7 MB raw `.bin`), `precip_total__mean` an unremarkable 150 MB — empirically confirming EPS has no display-prep entries; EPS synoptic per-run total ≈ 1.9 GB. Constraint-windowed anomaly vars show their expected reduced/single-frame sizes (`precip_16d/15d_anom__mean` at 2–3 MB). **Section 6's estimate basis is validated — slim-member math (≈0.9 / ≈1.7 MB raw per frame) stands; the Tier 3 warning about 3× member display-prep is now a measured fact (≈15 GB/run/var), not a projection.** Cross-check against the migration's item 7: GEFS 2.77 GB vs 4.5 GB COG-era (−38% ≈ the 41% COG share), EPS 1.9 GB vs 4.5 GB (−58% = the 58% share exactly) — the migration storage win is confirmed end-to-end. *Minor open observation, not a gate:* `wspd850`/`wspd300` (≈3.3 MB/frame) and `hgt500_anom` (≈2.5 GEFS / ≈4.5 EPS MB/frame) run heavier than bin+sidecars alone explains — likely contour/vector artifacts co-located in the var directory; an `ls` on one fh would make the artifact inventory exact. Irrelevant to slim members (bin+meta only).
- [x] This document's locked decisions re-confirmed — **confirmed by Brian, 2026-07-05. Phase 0 complete.** Independent design review (Codex, 2026-07-05) approved the locked decisions as the design baseline with no changes, flagging three execution risks now folded into this plan: the profile-aware write path (Section 3.2), EPS interleave memory placement (Section 3.6 — already visible), and the stale COG-era spike script (Section 7 warning).

**Phase 1 — Sizing spike** (Section 7) — **done (2026-07-06)**. Gate satisfied: **Tier 1 GO at 6-run parity retention; Tier 2 CONDITIONAL GO at the same 6-run parity (performant percentile implementation is a Phase 6 precondition); Tier 3 NO GO until server resources are expanded; global coverage deferred for all tiers** while design continues to keep global support first-class (Sections 1/3.7).

**Phase 2 — Scheduler design doc** (short, recommend-first) — **done (2026-07-06): `docs/ENSEMBLE_MEMBER_SCHEDULER_DESIGN.md`, APPROVED same day (Brian, concurring with independent Codex review; decisions D1–D5 recorded there).** Covers: profile-parameterized member build through `build_frame`/`write_grid_frame_for_run_root` (shared-internals writer + slim variant); EPS interleaved member encode from the pf subset (~~+ control fetch~~ — no EPS control exists upstream, §2.2 correction 2026-07-06); GEFS decoupled, deprioritized in-scheduler member pass (control via `member=0` → `gec00`, spike-confirmed; member-bundled fetch is a hard prerequisite for the second member variable, per D5); packing suffix fallback (+ probability packing entry specified, deferred to Tier 2); manifest member registration shape (open decision #5 resolved: members-as-metadata + per-member grid manifests); retention per the resolved 6-run parity policy (parity by construction — run-dir retention); ~~`supported_views` extension to `["mean", "members"]`~~ **superseded by design D1: `supported_views` stays `["mean"]`, members exposed via an `ensemble.members` descriptor — with the required Phase 3 follow-through of repointing the meteogram's `_model_supports_members` probe at the descriptor.** Scope per the 2026-07-06 sign-off: Tier 1 only (slim members); the design must not preclude Tier 2 stats or future global regions, but implements neither. Gate: Brian approves before any implementation agent starts — **satisfied 2026-07-06.**

**Phase 3 — GEFS member publish:** `tmp2m` first (matches spike config), verify against the acceptance criteria below, then extend to `precip_total` and `snowfall_total`. Gate: criteria green across ≥2 consecutive runs.

**Phase 4 — EPS member publish:** ~~interleaved design~~ **implemented 2026-07-06 as the decoupled pf-subset member pass (design doc §13 / D7, approval pending)** — the interleave's fetch-economics rationale is honored by reusing the mean's `*.cartosky_pf.grib2` subsets from the Herbie cache (band→member mapping re-derived from the .index by byte order; count/uniqueness-validated per subset), while the memory-tight EPS bundle build (see the §3.6 placement constraint above) is left untouched. Scope: `tmp2m` + `precip_total` members (ECMWF `tp` is natively run-cumulative, so both are direct per-band reads — no derive chain). ~~control included~~ **no control member (corrected 2026-07-06 — upstream exposes 50 pf rows only, §2.2; the stats completeness gate's expected EPS member set is 50)**; off-cycle schedule handling verified (fhs key off `scheduled_fhs_for_var`, never constants: 61+60 units synoptic, 25+24 off-cycle). `snowfall_total` deferred to its own plugin deliverable — spike finding #6 stands (direct per-member `sf` exists; units/accumulation-window/SLR + plugin wiring), but EPS publishes no snowfall mean today, so it is not a member target yet. Gate: criteria green across one synoptic **and** one off-cycle run.

**Phase 5 — Meteogram members:** hand back to `MODEL_GUIDANCE_IMPLEMENTATION_PLAN.md` Section 7 (its `include_members` contract and chart specs stand; its pipeline gates now point here). **Backend done + prod-verified for GEFS (2026-07-06):** `include_members=true` returns the Section 7 members block (mean/control/m01–m30) sampled from member grid binaries via a new seek-based point sampler (equality-pinned to the full-read sampler; the members × fhs fan-out made full-frame decodes untenable). Member candidate frames come from the mean series' fhs — member vars are deliberately absent from the run manifest (design R7). EPS correctly 400s until Phase 4 publishes its members. Charts (spaghetti plumes, snowfall histogram) remain open per the Model Guidance Section 7 specs.

**Phase 6 — Derived stats grids + map products:** **design drafted 2026-07-08 — `ENSEMBLE_STATS_GRIDS_DESIGN.md`, pending approval; scope decisions D-A…D-E ratified same day** (initial matrix = precip + snow only, tmp2m deferred behind a one-descriptor addition; third in-scheduler pass, not a service; sort-based pure-numpy percentile — **precondition SATISFIED: benchmarked 0.25 s/fh for all five percentiles vs 17.1 s naive, 67×, parity-identical**; viewer exposure via a product sub-selector on the parent variable; MSLP member lows deferred out of Phase 6). ~~second-pass stats service~~ third in-scheduler pass (Section 3.3 architecture otherwise unchanged) publishing percentile and probability variables per Section 4; viewer exposure as ordinary variables behind the product selector; CF `HIT` verified on stats binaries; stats vars are designed to feed meteogram percentile bands with frontend-only follow-up work. **MSLP + member low locations remains double-gated** (net-new variable for both models → its own data-source sizing spike first; low detection reuses the existing pressure-center machinery across member fields, output aggregated into one vector-overlay payload). Gate: stats values spot-checked against a manual member tally at test points (same bar as the Model Guidance Phase 3 checklist).

**Acceptance criteria (Phases 3–4):** all expected member frames present per `scheduled_fhs_for_var` for the run's cycle hour; zero pre-encode gate bypasses (any member frame failing the gate is rejected, never published); mean publish latency unchanged vs. pre-member baseline; RSS within the capped budget; disk delta per run within the signed-off tier; retention sweep removes member directories on schedule; binary sampler successfully samples member frames at interior/near-edge/out-of-coverage points (out-of-coverage = expected-missing, not error).

## Appendix A — Per-member browse maps (supported by design, not scheduled)

> **NO GO recorded (Brian, 2026-07-06):** Tier 3 is not something the server can support until resources are expanded. Spike extrapolation: ≈456 GB `na` / ≈2.65 TB global at 6-run parity. Revisiting requires a fresh budget sign-off after resource expansion; nothing below is committed.

WB-style panel browsing maps onto CartoSky's viewer as a **member selector scrubbing frames** — the WebGL pipeline is indifferent to whether the next frame URL is `fh+6` or `m+1`, so this is feasible without compromising the sub-100ms frame-load bar. What it costs: flipping affected member variables to full profile (sidecar JSON with colormap meta, brotli sidecars, manifest/bootstrap exposure, CF cache rules — binaries must be `HIT`), deciding display-prep resolution per variable (Section 3.2 fork), and Tier 3 storage with its own explicit budget sign-off. Frontend surface (member selector UX, entitlements, mobile behavior) would be its own recommend-first plan. Nothing in Phases 0–6 forecloses this; nothing in it is committed.

---

## Open decisions

| # | Decision | Resolved by | Notes |
|---|----------|-------------|-------|
| 1 | Member retention count | **RESOLVED 2026-07-06: 6-run parity** (Brian sign-off, spike doc §10) | Per-view retention (1–2 runs) remains the documented fallback lever |
| 2 | EPS control Herbie `member`/inventory selection | **RESOLVED 2026-07-06 (negative):** no `cf` rows exist upstream — EPS roster = 50 pf members, no `__control` artifact (§2.2 correction) | The cf selection mechanism was built and verified; nothing to select |
| 3 | GEFS upstream member count + control kwarg | **RESOLVED 2026-07-06:** 30 pf + 1 control confirmed (all 31 fetched); control kwarg `member=0` → `gec00` (herbie 2026.3.0) | Spike measurement 4 |
| 4 | EPS `snowfall_total` derivation complexity | **RESOLVED at inventory level 2026-07-06:** direct per-member `sf` field exists (also `sd`, `asn`); no csnow-style derivation chain needed | Remaining scope (units/SLR/plugin wiring) lands in Phase 4 |
| 5 | Manifest member registration shape | **RESOLVED 2026-07-06 (Phase 2 design, approved):** metadata-under-canonical-var (`ensemble.members` descriptor) + ordinary per-member grid manifests; no per-member catalog entries | Includes the D1 follow-through: meteogram `_model_supports_members` probe repointed at the descriptor (Phase 3 checklist item) |
| 6 | Display-prep resolution for *served* member maps (1× vs 3× on GEFS precip/snow) | Moot while Tier 3 is NO GO (2026-07-06); spike measured both factors (×1.68 sidecars, ×9.0 upscale) | Mixed resolution is safe meanwhile |
| 7 | Tier 3 (browse maps) budget | **NO GO recorded 2026-07-06** — server cannot support until resources expanded; fresh sign-off required if revisited | Appendix A |
| 8 | Global-region re-approval | **Decision 2026-07-06: global deferred for ALL tiers.** Design/planning must keep global support first-class (no `na` hardcoding — Sections 1/3.7) so incorporation is straightforward later | ~5.8× per-frame multiplier; Tier 1 global ≈ 571 GB (6-run) / 190 GB (2-run); Tier 3 global exceeds the budget outright |

---

*Document version: 2026-07-04 (initial). Code-verified findings dated 2026-07-04 against `gefs.py`, `eps.py`, `scheduler.py`, `grid.py`, `builder/pipeline.py`, `builder/fetch.py`. Updated 2026-07-06 with Phase 1 spike measurements, the EPS 50-member correction, and Brian's recorded tier/retention decision (`docs/ENSEMBLE_MEMBER_SIZING_SPIKE.md` §10).*
