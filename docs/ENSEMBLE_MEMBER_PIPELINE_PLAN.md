# Ensemble Member Pipeline Plan (GEFS + EPS)

> **Audience:** AI agents executing discrete phases. Each phase is self-contained and must pass its verification gate before the next begins.
>
> **Status (2026-07-04):** Design/pre-spike. **Nothing in this plan may start until Phase 0's prerequisites pass** — in particular, GEFS and EPS must complete the binary-sampling migration (`docs/VALUE_COG_TO_BINARY_SAMPLING_MIGRATION_PLAN.md`) and be live on `CARTOSKY_BINARY_SAMPLING_MODELS` with COG writes off.
>
> **Hard gate (unchanged from the original Model Guidance plan):** no member scheduler work of any kind — beyond the explicitly scoped sizing spike — until the spike is complete and Brian's go-ahead is recorded in the spike doc.
>
> **Document lineage:** This plan supersedes the "Individual member data pipeline" section and Phase 3 scheduler/storage material of `docs/MODEL_GUIDANCE_IMPLEMENTATION_PLAN.md`, which were written pre-migration and specified member **value COGs** — an artifact allowlisted models no longer produce. This document follows the migration plan's convention: corrections are documented in place, and claims verified against code are marked as such.

---

## 1. Scope: what this pipeline serves

Per-member ensemble data is **shared infrastructure with three consumer families**, not a meteogram feature. Designing for only the first family would paint us into a corner before the fall/winter busy season.

| # | Consumer family | Serving shape | Cost profile |
|---|-----------------|---------------|--------------|
| 1 | **Meteogram members** (Model Guidance Phase 3: spaghetti plumes, snowfall member histogram/detail) | Server-side point sampling of member binaries; members never leave the server as rasters | Cheapest; slim member artifacts suffice |
| 2 | **Derived stats grids for maps** (percentile maps e.g. snow P25/P50/P75; probability-of-exceedance maps e.g. P(QPF > 1.0"); MSLP member low locations) | Computed **from** members, published **as ordinary single grid artifacts** through the normal pipeline — colorize, sidecar, grid binary, WebGL, Cloudflare caching all unchanged. Client never touches a member raster. | Cheap (~one extra "variable" per stat); highest winter user value |
| 3 | **Per-member browse maps** (WB-style: scrub through individual member snowfall maps in the viewer) | Members become first-class **served** artifacts: brotli sidecars, sidecar JSON with colormap meta, manifest entries, CF `HIT` | Expensive (31–51× served-artifact surface); supported by design, **not scheduled** — see Appendix A |

Two standing design constraints from the roadmap:

- **Global map support is planned.** The binary migration was partly motivated by it. Nothing in this pipeline may hardcode CONUS or `na` assumptions — region is always a parameter (it already is, via `MODEL_REGISTRY` + `get_grid_params`), the per-frame meta transform is the geometry authority, and all footprint budgets in this doc carry a global multiplier (Section 6) so decisions made now survive the region expansion. When global regions land, retention and member-variable scope must be re-evaluated against that multiplier — that re-evaluation is an explicit checklist item, not an assumption.
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

### 2.3 The GEFS reality: members are net-new fetch load

GEFS mean uses the upstream **precomputed `geavg` product** (`herbie_kwargs["member"] = "mean"`, verified in `gefs.py`'s `herbie_request`). Member fields live in separate upstream files (`gep01`–`gep30`, `gec00`). Per-member GEFS therefore requires ~31 Herbie subset downloads × 65 fh per run — **~2,015 HTTP fetches per member-variable per run if fetched per-variable**. Request count, not bytes, is the risk (upstream rate limits). Mitigation is member-bundled fetch (Section 3.6).

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

**The one genuine fork is `display_prep`:** GEFS `precip_total__mean`/`snowfall_total__mean` are upscale-3× for map smoothness (9× pixels). Slim members at native 1× are correct for sampling and for stats computation; a *served* 25 km member snowfall map at 1× would look chunkier than the mean beside it, and 3× members cost ~9× disk. Mixed resolutions break nothing (sampler and client are transform-driven per frame), so this defers cleanly — but it is measured in the spike (Section 7) and decided per-variable only if/when browse maps are scheduled. Open decision #6.

### 3.3 Stats grids are a second pass over published member binaries

Percentile and probability grids are computed by **reading published member `.bin` frames** (decode via `_decode_values` → per-pixel stats across the member axis → publish the result as an ordinary derived variable through the **normal, full-profile** pipeline). Not computed inline during fetch/warp.

Why: decouples stats entirely from the member build profile and fetch orchestration; tolerates a late/backfilled member frame; reuses the decode primitive exactly as the migration designed; and the memory cost is trivial — all members of one fh in float32 ≈ **~175 MB for EPS (51 × ~3.4 MB)**, **~56 MB for GEFS (31 × ~1.8 MB)** at `na` resolution. Quantization noise from 0.1-precision decoded inputs is irrelevant at these product scales. Stats outputs are grid binaries served like any other frame — **they must get `CF-Cache-Status: HIT`**, same rules as every grid binary.

**Runtime completeness gate (required):** before publishing a stat frame for an fh, the stats pass verifies the **full expected member set** is present for that fh (member count per `scheduled_fhs_for_var` and the model's member roster). If incomplete — e.g. the scheduler unit was `MemoryMax`-killed mid-member-loop, or a member frame failed the pre-encode gate — skip that fh and retry on the next pass; never publish a percentile/probability grid computed from a partial member set. Silently-wrong stats on the map is the worst failure mode this pipeline can produce.

### 3.4 Packing resolution: suffix normalization, not entry explosion

Extend `_packing_config` (or a wrapper it calls) with a fallback: if `(model, var)` misses and `var` ends in `__m{NN}` or `__control`, strip the suffix and resolve the **`__mean` twin's** packing. Members and mean MUST share packing constants — they quantize the same physical field; divergent constants would be a silent-corruption bug class. Percentile grids (`__p{NN}`) resolve the same way to the base variable's packing.

**Probability grids are a new packing band** — a deliberate, explicit `_PACKING_BY_MODEL_VAR` addition (recommended: `scale=0.1, offset=0.0`, units `%`, uint16 → 0.1% precision), audited with the same signed-offset discipline the migration's Phase G addendum mandates. Do not let it fall through any suffix fallback.

The migration's **packing-fix retroactivity addendum applies to member frames identically**: a packing fix does not retro-correct already-published member binaries; they age out with retention. Any stats pass consuming members must therefore run against post-fix frames only if a packing fix landed mid-window.

### 3.5 Naming (LOCKED — see Section 4)

### 3.6 Fetch strategy per model

- **EPS — interleave with the mean build.** Design the member encode loop to run inside (or immediately adjacent to) the same frame build that downloads the pf subset: one subset read yields mean + N member binaries. Do **not** design a separate later member pass that depends on Herbie cache survival or re-downloads. Control (`cf`) is fetched via its own inventory selection in the same pass.
- **GEFS — decoupled member loop after mean publish, member-bundled fetch.** Per `(member, fh)`, download **one** subset covering **all** member variables (the byte-range subset machinery already accepts multiple inventory rows), collapsing `vars × 31 × 65` fetches to `31 × 65` per run. The member loop runs strictly after mean publish at reduced priority (`nice -n 10 ionice -c2 -n7`, consistent with canary hygiene) with a parallelism/backoff knob so mean freshness and upstream goodwill are protected.

### 3.7 Region- and global-agnostic implementation

No hardcoded bboxes, region names, or grid dims anywhere in member/stats code: region flows from the model plugin as it does today; frame geometry comes from the per-frame meta. Anchor/point lists used in verification are generated from the model's region bbox, not copied from CONUS lists (lesson already learned in the GEFS/EPS Phase G audit).

---

## 4. Naming and manifest schema (LOCKED)

### 4.1 Runtime var ids

| Kind | Pattern | Examples |
|------|---------|----------|
| Perturbation member | `{var}__m{NN}` (zero-padded 2-digit) | `tmp2m__m01` … `tmp2m__m30` (GEFS), `…__m50` (EPS) |
| Control | `{var}__control` (distinct from `m01`) | `tmp2m__control` |
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
    {run}/  (same pattern; m01–m50 + control)
```

### 4.4 Manifest

Register member/stat runtime vars so `list_frames` and the meteogram's frame enumeration work unchanged. Recommended shape: stats vars are ordinary catalog/manifest entries (they are ordinary products); members are registered under the canonical var as `members: { count, prefix, control: bool, frames: … }` metadata **or** as full var entries — the design doc (Phase 2) decides, with the constraint that both the meteogram (`include_members`) and any future map consumer can enumerate member frame lists without globbing directories.

---

## 5. Retention (OPEN DECISION — resolved by the spike)

**Target: parity with mean retention (6 runs).** If the spike's extrapolation shows parity is not comfortably affordable (Section 6 budget), fall back to **per-view retention**: members retained for the latest 1–2 runs while mean products keep 6. That lever cuts member footprint 3–6× and is product-defensible (meteograms use `latest_per_model` only; the run selector simply shows fewer runs for member views). Stats grids are cheap and follow normal retention regardless.

The retention/cleanup job must handle member directories under whichever policy is chosen, and the spike measures sweep duration with member file counts present (Section 7).

---

## 6. Server budgets and planning estimates

**Constraints (2026-07-04):** disk ~878 GB used of 2 TB (~1.1 TB free); RAM 32 GB total, baseline 17–22 GB available depending on scheduler load. Both schedulers are memory-capped via systemd drop-ins (EPS `MemoryHigh=3G`/`MemoryMax=4G`; GEFS `3G`/`3500M`) — note the semantics: `MemoryHigh` throttles via reclaim (slows builds, evicts page cache — which the EPS pf-subset read path benefits from), `MemoryMax` **kills the unit** (see the stats completeness gate, Section 3.3). Drop-ins must be captured in `deployment/systemd/` (Phase 0).

**Everything below is a planning estimate — the spike replaces these numbers with measurements.** Basis: `na` region grid dims ≈ 680×655 px (GEFS 25 km) and ≈ 945×910 px (EPS 18 km); uint16 → ~0.9 MB (GEFS) / ~1.7 MB (EPS) raw per frame; slim profile = 2 files/frame (`.bin` + meta), no compression sidecars, no display-prep upscale.

| Tier | GEFS (31 members) | EPS (51 incl. control) | Combined, 3 member vars, 6-run retention |
|------|-------------------|------------------------|------------------------------------------|
| **Tier 1 — meteogram-only (slim, 1×)** | ~1.8 GB/run/var (65 fh) | ~5.3 GB synoptic / ~2.2 GB off-cycle per run/var | **~100 GB** (GEFS ~32 GB + EPS ~68 GB) |
| **Tier 2 — + stats grids** (≈5 percentiles + ≈4–6 prob thresholds per var, full profile) | +~0.5–1 GB/run total | +~1–2 GB/run total | **+~10–20 GB** — noise relative to Tier 1 |
| **Tier 3 — + full-profile served members** (br sidecars; 3× display-prep on GEFS precip/snow ≈ 9× those variables' pixels) | multiply affected vars ~2–9× | +~40–60% (sidecars) | **several hundred GB — requires its own budget sign-off; not scheduled** |

Per-view retention fallback (members latest 2 runs): Tier 1 drops to roughly **~33 GB**.

**Global multiplier:** full-extent web-mercator (±85°) at these grid spacings ≈ 1603 px (25 km) / 2226 px (18 km) square → **~5.8× the `na` per-frame footprint for both models**. Any go/no-go recorded against Tier numbers must note whether it holds under the global multiplier or requires re-approval at global rollout.

**RAM:** member builds hold one member grid at a time (verified: the EPS mean aggregation already streams band-by-band); expected worker peak ≈ today's. Stats passes hold all members of one fh: ~175 MB (EPS) / ~56 MB (GEFS) plus overhead — comfortably inside headroom, but the spike measures real RSS, not this arithmetic. Inodes: slim GEFS ≈ 31 × 65 × 2 ≈ 4,030 files/run/var — fine for ext4; the operational question is publish-promote and retention-sweep wall time, which the spike measures.

---

## 7. Sizing spike protocol (binary edition)

One-run GEFS `tmp2m` member publish (`tmp2m__m01`–`__m30` + `tmp2m__control`, all 65 fh), slim profile, via the deploy workflow (never patched on the server), member loop at reduced priority. **Deliverable:** `docs/ENSEMBLE_MEMBER_SIZING_SPIKE.md` with Brian's explicit go/no-go recorded. Measure and document:

1. Total bytes and file count under `published/gefs/{run}/tmp2m__m*/` + `__control/` (slim profile), and **one member variable additionally written at full profile and at 3× display-prep** for the Tier 3 extrapolation row.
2. End-to-end member-batch publish latency, and confirmation the concurrent/next mean publish was not delayed.
3. Scheduler peak RSS during the member loop, recorded as **headroom against the configured caps** (EPS `MemoryHigh=3G`/`MemoryMax=4G`; GEFS `3G`/`3500M`), plus RSS of a prototype stats pass over the 31 published member frames for one fh. Note whether `MemoryHigh` throttling engaged (build slowdown + reclaim), not just whether `MemoryMax` was hit.
4. **Fetch feasibility:** wall time, failure/retry rate, and any upstream throttling across the ~2,015-fetch pattern (member-bundled if the bundling lands first; per-var otherwise — record which). Confirm upstream member count (expect 30 pf + 1 control) and the Herbie `member` kwarg for GEFS control **and** EPS control.
5. Staging→published promote time and retention-sweep duration with member directories present.
6. **EPS `snowfall_total` feasibility** (direct GRIB field vs derivation complexity) — flag, don't block.
7. Extrapolation table: Tier 1/2/3 × {parity retention, 2-run retention} × {`na`, global ~5.8×}, against the ~1.1 TB free budget.

**Gate:** Brian's sign-off on a specific tier + retention combination, recorded in the spike doc, before any work beyond the spike.

---

## 8. Phases

Each phase gates on the previous. Recommend-first: Phases 2's design doc goes to Brian before implementation.

**Phase 0 — Prerequisites (no member work of any kind before all pass):**
- [ ] GEFS and EPS on `CARTOSKY_BINARY_SAMPLING_MODELS`, COG writes off, migration Phase F evidence complete for both.
- [x] Scheduler `MemoryHigh`/`MemoryMax` caps deployed for GEFS and EPS — **done (2026-07-04)**: server drop-ins `csky-eps-scheduler.service.d/memory-limits.conf` (`MemoryHigh=3G`, `MemoryMax=4G`) and `csky-gefs-scheduler.service.d/memory-limits.conf` (`MemoryHigh=3G`, `MemoryMax=3500M`). **Remaining sub-item:** capture both drop-ins in `deployment/systemd/` — they currently exist only on the server (same config-drift pattern as the satellite-rgb incident).
- [ ] Pre-spike RSS baseline: record current mean-build peak RSS for both schedulers over a few cycles (memory-checkpoint logs / `systemctl` `MemoryPeak`) against the caps above, so spike-era slowness from `MemoryHigh` throttling is distinguishable from member-loop cost.
- [ ] One post-cutover run per model measured on prod: per-variable **binary** footprint (the extrapolation base; migration checklist item 7, binary edition).
- [ ] This document's locked decisions re-confirmed by Brian if anything material changed since 2026-07-04.

**Phase 1 — Sizing spike** (Section 7). Gate: recorded sign-off on tier + retention.

**Phase 2 — Scheduler design doc** (short, recommend-first): profile-parameterized member build through `build_frame`/`write_grid_frame_for_run_root`; EPS interleaved member encode from the pf subset + control fetch; GEFS decoupled, member-bundled, deprioritized loop; packing suffix fallback + probability packing entry; manifest member registration shape; retention implementation per the chosen policy; `supported_views` extension to `["mean", "members"]`. Gate: Brian approves before any implementation agent starts.

**Phase 3 — GEFS member publish:** `tmp2m` first (matches spike config), verify against the acceptance criteria below, then extend to `precip_total` and `snowfall_total`. Gate: criteria green across ≥2 consecutive runs.

**Phase 4 — EPS member publish:** interleaved design; control included; off-cycle schedule handling (25-frame runs; anomaly vars aren't member targets, but frame-count expectations must key off `scheduled_fhs_for_var`, never constants). `snowfall_total` scoped per spike finding #6 — separately if derivation is nontrivial. Gate: criteria green across one synoptic **and** one off-cycle run.

**Phase 5 — Meteogram members:** hand back to `MODEL_GUIDANCE_IMPLEMENTATION_PLAN.md` Section 7 (its `include_members` contract and chart specs stand; its pipeline gates now point here).

**Phase 6 — Derived stats grids + map products:** second-pass stats service (Section 3.3) publishing percentile and probability variables per Section 4; viewer exposure as ordinary variables; CF `HIT` verified on stats binaries. **MSLP + member low locations is scoped here but double-gated** (net-new variable for both models → its own data-source sizing spike first; low detection reuses the existing pressure-center machinery across member fields, output aggregated into one vector-overlay payload). Gate: stats values spot-checked against a manual member tally at test points (same bar as the Model Guidance Phase 3 checklist).

**Acceptance criteria (Phases 3–4):** all expected member frames present per `scheduled_fhs_for_var` for the run's cycle hour; zero pre-encode gate bypasses (any member frame failing the gate is rejected, never published); mean publish latency unchanged vs. pre-member baseline; RSS within the capped budget; disk delta per run within the signed-off tier; retention sweep removes member directories on schedule; binary sampler successfully samples member frames at interior/near-edge/out-of-coverage points (out-of-coverage = expected-missing, not error).

## Appendix A — Per-member browse maps (supported by design, not scheduled)

WB-style panel browsing maps onto CartoSky's viewer as a **member selector scrubbing frames** — the WebGL pipeline is indifferent to whether the next frame URL is `fh+6` or `m+1`, so this is feasible without compromising the sub-100ms frame-load bar. What it costs: flipping affected member variables to full profile (sidecar JSON with colormap meta, brotli sidecars, manifest/bootstrap exposure, CF cache rules — binaries must be `HIT`), deciding display-prep resolution per variable (Section 3.2 fork), and Tier 3 storage with its own explicit budget sign-off. Frontend surface (member selector UX, entitlements, mobile behavior) would be its own recommend-first plan. Nothing in Phases 0–6 forecloses this; nothing in it is committed.

---

## Open decisions

| # | Decision | Resolved by | Notes |
|---|----------|-------------|-------|
| 1 | Member retention count (target: parity, 6 runs) | Phase 1 spike + Brian sign-off | Per-view retention (1–2 runs) is the fallback lever |
| 2 | EPS control Herbie `member`/inventory selection | Phase 1 spike (item 4) | `cf` rows excluded by current pf filter; small new fetch code |
| 3 | GEFS upstream member count + control kwarg | Phase 1 spike (item 4) | Expect 30 pf + 1 control |
| 4 | EPS `snowfall_total` derivation complexity | Phase 1 spike (item 6) | Plugin + derive deliverable before member deliverable; scope separately if nontrivial |
| 5 | Manifest member registration shape | Phase 2 design doc | Metadata-under-canonical-var vs. full var entries |
| 6 | Display-prep resolution for *served* member maps (1× vs 3× on GEFS precip/snow) | Deferred until browse maps are scheduled; spike measures both | Mixed resolution is safe meanwhile |
| 7 | Tier 3 (browse maps) budget | Its own sign-off if ever scheduled | Appendix A |
| 8 | Global-region re-approval | At global rollout | ~5.8× per-frame multiplier; re-run Section 6 math |

---

*Document version: 2026-07-04 (initial). Code-verified findings dated 2026-07-04 against `gefs.py`, `eps.py`, `scheduler.py`, `grid.py`, `builder/pipeline.py`, `builder/fetch.py`.*
