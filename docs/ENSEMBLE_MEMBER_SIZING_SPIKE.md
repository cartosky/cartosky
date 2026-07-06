# Ensemble Member Sizing Spike — Results (Phase 1)

> **Status:** COMPLETE — measurements recorded (Gate B, 2026-07-05); go/no-go recorded (Section 10, 2026-07-06): Tier 1 GO at 6-run parity, Tier 2 conditional at 6-run parity, Tier 3 no-go, global deferred for all tiers.
>
> **Protocol:** `docs/ENSEMBLE_MEMBER_PIPELINE_PLAN.md` Section 7 ("binary edition"). Executed by Brian on prod via `backend/scripts/ensemble_member_sizing_spike.py` under `nice -n 10 ionice -c2 -n7`, `--parallel 2 --resume`.
>
> **Source artifacts (authoritative):** `/opt/cartosky/canary/gefs_members/20260705_12z/results.json` + `spike.log`, plus bounded `journalctl -u csky-gefs-scheduler` slices for the 18z build windows of 2026-07-05 (spike day) and 2026-07-04 (baseline). This document is authored strictly from those artifacts. Every claim is tagged **[measured]** (from the spike run), **[code-verified]** (read from source / live upstream index), or **[estimated]** (extrapolation).
>
> **Convention:** corrections are recorded in place. Plan corrections this spike triggers are listed in Section 9.

## 0. Run configuration

| | |
|---|---|
| Target run | `gefs/20260705_12z` — newest retained run, full 65-frame `tmp2m__mean` coverage verified before member fetches [measured] |
| Scope | `tmp2m` members m01–m30 + control × 65 fh = 2,015 frames, slim profile (`.bin` + meta only, native 1×) |
| Frame reconciliation | 2,015 expected = 2,009 written + 6 resumed from the Gate A dry run; **0 gate failures, 0 fetch failures** [measured] |
| Environment | prod, python 3.13.5, herbie 2026.3.0; isolated Herbie cache under the canary run dir (292 MiB after the run) [measured] |
| Spike window | 2026-07-05 21:57:38–22:10:25 UTC (767 s total incl. post-pass measurements) [measured] |

---

## 1. Bytes and file counts (measurement 1)

**Slim member tree** (31 members × 65 fh) [measured]:

| Metric | Value |
|---|---|
| Total | **1,806,388,476 B ≈ 1.81 GB** (1.68 GiB), 4,030 files |
| Per frame | 896,148 B `.bin` (682×657×2, exact) + ~323 B meta = 896,471 B |
| Per member (65 fh) | ~58.3 MB |

**Comparison subsets** (m01–m05 × fh {0,48,96,192,384} = 25 frames; per-frame bytes extrapolate linearly) [measured]:

| Set | Per frame | Factor vs slim |
|---|---|---|
| Full-ish profile (`.bin` + gzip + brotli sidecars + meta) | 1,504,780 B | **×1.68** (gz sidecar 319,620 B = 35.7% of bin; br 288,690 B = 32.2%) |
| 3× display-prep, slim | 8,065,873 B | **×9.00** (pixel count, exactly as predicted) |

Caveats: the "full-ish" set covers compression sidecars only — production full profile adds sidecar JSON / contour artifacts not measured here (small relative to sidecars). The 3× set used a measurement-only equivalent of the continuous display-prep branch (tmp2m has no display-prep config); byte sizes are exact, values double-quantized (irrelevant to sizing).

---

## 2. Wall time and mean-build non-interference (measurement 2)

**Member batch** [measured]: 2,009 frames in **733 s (12.2 min)** at `--parallel 2`. Per-frame mean 0.70–0.75 s, p95 ≤ 0.82 s across all 31 members. Stage totals (cumulative across workers): fetch 1,387.5 s (**95%**), warp 64.2 s, encode+write 5.8 s, gate 2.9 s. Fetch dominates completely; encode/write are negligible.

**Mean publish not delayed** [measured, from scheduler journal slices]:
- The 18z GEFS mean build started 21:48:20 UTC ("Promotion gate: run=20260705_18z"), so the spike (21:57–22:10 UTC) ran **inside an active mean build window** — a stronger test than an idle overlap.
- Spike day: by 00:29 UTC the scheduler had completed 20260705_18z and was already cycling on 20260706_00z (upstream not yet available). Baseline day (2026-07-04, same clock window): 20260704_18z was still at 981/1016 at 00:29 UTC, waiting on upstream long-tail frames (fh378/384 "transiently unavailable" — normal upstream cadence).
- Conclusion: the spike-day 18z cycle finished **at least as early as the baseline cycle**; per-frame build cadence at the head of both builds is indistinguishable (~0.5–2 s/frame). No delay attributable to the spike. (Both builds are upstream-cadence-dominated, so this is a no-observed-interference result, not a controlled A/B.)

---

## 3. RSS vs. scheduler caps (measurement 3)

[measured] Spike process peak RSS **489 MiB** (ru_maxrss 512,991,232 B; sampled max 492,195,840 B), including the 31-member stats pass. Against the GEFS caps:

| Cap | Value | Headroom at spike peak |
|---|---|---|
| `MemoryHigh` | 3 GiB | **+2,583 MiB** |
| `MemoryMax` | 3,500 MB | **+3,011 MiB** |

A member loop of this shape adds ~0.5 GiB peak to whatever process hosts it — far inside the GEFS scheduler's observed ~1.1 GiB build peak + 3 GiB `MemoryHigh`. The optional `systemd-run --scope` throttle-counter bonus was not run; given 6× headroom it would not change the conclusion. [estimated: hosting the loop inside the scheduler process lands ≈1.6 GiB peak if fully additive — still comfortable.]

---

## 4. Fetch feasibility, member roster, control kwargs (measurement 4)

[measured] **2,009/2,009 requests succeeded, 0 retries, 0 failures, no throttling evidence** (no 403/429/5xx anywhere; aws priority served everything on attempt 1) across the ~2,015-request pattern (per-var fetch; member-bundled fetch not yet implemented — Section 3.6 of the plan collapses multi-var request counts to this same 31×65 shape). Fetch wall ≈ 0.69 s/frame mean.

**GEFS member roster and control kwarg (plan open decision #3 — resolved)** [measured]:
- 30 perturbed members + 1 control, all fetched successfully (`members_with_written_frames: 31`).
- Herbie kwarg: `member=<int 1..30>` → `gepNN`; **control: `member=0` → `gec00`** (first candidate; `"c00"` never needed). herbie 2026.3.0.

**EPS control mini-check (plan open decision #2 — resolved negatively)** [measured + code-verified]:
- The enfo inventory for `:2t:` at fh0 contains **only `type == "pf"` rows — 50 members, numbers 1–50 contiguous, zero `cf` rows** (`types_present: {"pf": 50}`).
- Independently confirmed against the live upstream index on data.ecmwf.int for two cycles (2026-07-05 12z fh0: 8,500 index rows, all `pf`; 2026-07-05 00z fh24: same) [code-verified 2026-07-05].
- **Consequence: ECMWF open data exposes no EPS control forecast. The EPS member roster is m01–m50 only; no `tmp2m__control` (or any `__control`) artifact is fetchable for EPS.** The `type == "cf"` selection mechanism itself is implemented and correct — there is simply nothing for it to select.

---

## 5. Promote + retention sweep simulation (measurement 5)

[measured, on the canary tree — production published tree untouched]:

| Operation | Result |
|---|---|
| Atomic rename of full slim tree (out / back) | **< 1 ms each way** (same-filesystem rename; `0.0 s` at ms resolution) |
| Recursive walk+delete of a copy (4,030 files, 1.81 GB) | **0.76 s** |

Member directories add no meaningful promote or retention-sweep cost at `na` scale. [estimated: ×3 vars ×2 models stays low single-digit seconds per swept run.]

---

## 6. EPS `snowfall_total` feasibility (measurement 6, inventory-level only)

[measured] The enfo inventory at fh24 exposes, **per perturbed member** (50 rows each): **`sf`** (snowfall accumulation), `sd` (snow depth), `asn` (snow albedo). No `csnow`-style categorical field.

Finding: **a direct per-member snowfall field (`sf`) exists** — EPS member snowfall does not require the GFS-style csnow/apcp derivation chain. Remaining integration work (units/accumulation-window handling, SLR conversion to the 10:1 display product, plugin + derive wiring) is a Phase 4 scoping item per plan open decision #4; nothing here suggests it is nontrivial in the way the plan feared. Flag, don't block — unchanged.

---

## 7. Stats-pass prototype (measurement 7)

[measured] One fh (fh000), all 31 member frames, completeness gate enforced (asserted 31/31 present before compute):

| Metric | Value |
|---|---|
| Decode (31 × `_decode_values`) | **0.10 s**; stack 53.0 MiB float32 (657×682×31) |
| Compute (p10/p25/p50/p75/p90 + P(>32 °F) per pixel) | **13.7 s** |
| RSS delta | +145 MiB (process peak stayed ≤ 489 MiB) |

Memory matches the plan's arithmetic (~56 MB stack + overhead) — RAM is a non-issue. **The 13.7 s compute is an implementation artifact, not a data cost**: `np.nanpercentile` falls back to a per-pixel Python loop when NaNs are present; the Gate A dry run measured 13.3 s for only 2 members, confirming the cost is pixel-count-bound, not member-count-bound. [estimated] A naive per-fh stats pass over 65 fh would spend ~15 min/run/var in percentile compute; a production Phase 6 implementation should use a faster nan-aware method (e.g. sort-based with the shared member NaN mask). Not a Phase 1–4 blocker.

**Sampler spot check** [measured]: 9/9 passes across 3 frames (m01/fh000, m16/fh192, control/fh384) × {interior, near-edge, out-of-coverage}; out-of-coverage registered as expected-missing (`no_data=true`), never an error. Values plausible (69–89 °F interior/near-edge July temps).

---

## 8. Extrapolation table (against ~1.1 TB free, 2026-07-04 basis)

Basis: GEFS slim = **1.81 GB/run/var [measured]**. EPS slim/frame = 1.7 MB raw (Phase 0 measured EPS mean `.bin` ≈ 1.65–1.7 MB; member dims identical) → synoptic 61 fh × **50 members** = 5.19 GB, off-cycle 25 fh × 50 = 2.13 GB per run/var [estimated from measured frame size, corrected to the 50-member roster]. "3 member vars" = tmp2m, precip_total, snowfall_total (slim `.bin` size is value-independent). 6-run EPS retention ≈ 3 synoptic + 3 off-cycle. Tier 3 factors: sidecars ×1.68 [measured], 3× display-prep ×9.0 [measured], applied to GEFS precip+snow only; EPS has no display-prep entries (Phase 0 confirmed).

| Tier (3 member vars) | Retention | `na` | Global (×5.8) |
|---|---|---|---|
| **Tier 1 — slim (meteogram-only)** | 6-run parity | **98 GB** (9% of free) | 571 GB (52%) |
| | 2-run | **33 GB** (3%) | 190 GB (17%) |
| **Tier 2 — + stats grids** [estimated: +~15 GB/6-run, +~5 GB/2-run, full-profile ordinary artifacts] | 6-run | ~113 GB (10%) | ~660 GB (60%) |
| | 2-run | ~38 GB (3.5%) | ~220 GB (20%) |
| **Tier 3 — + full-profile served members** [extrapolated from measured ×1.68 / ×9.0 factors] | 6-run | ~456 GB (41%) | ~2.65 TB (**over budget**) |
| | 2-run | ~152 GB (14%) | ~880 GB (80%) |

Component detail (na, 6-run): Tier 1 = GEFS 32.5 GB + EPS 65.9 GB. Tier 3 = GEFS 346 GB (tmp2m ×1.68; precip+snow ×9.0×1.68) + EPS 111 GB (×1.68).

Notes:
- Tier 1 and Tier 2 fit comfortably at `na` under either retention; **Tier 1 at 6-run parity costs 9% of current free space**.
- Any go recorded against `na` numbers does **not** hold for Tier 3 at global scale (plan Section 6 rule): Tier 3 global exceeds the budget outright at parity retention and requires its own sign-off regardless.
- Inodes: slim = 4,030 files/run/var [measured]; 3 vars × 2 models × 6 runs ≈ 140k files — trivial for ext4, and sweep cost measured at 0.76 s per 4,030 files.

---

## 9. Anomalies, surprises, and plan corrections required

1. **EPS control does not exist upstream** (Section 4). Corrections to record in `ENSEMBLE_MEMBER_PIPELINE_PLAN.md` (in place, per its convention): §2.2's "control fetch is a small new inventory selection" premise is void; "EPS 51 incl. control" → **50** throughout (§4.1 examples, §6 tables — footprint −2%); open decision #2 closed. EPS naming needs no `__control` id; the stats completeness gate's expected member set for EPS is 50.
2. **`np.nanpercentile` is pathologically slow** on this workload (13.7 s/fh, pixel-bound — Section 7). Phase 6 design note; does not gate Phases 2–4.
3. **Operational: Herbie cache ownership.** The first Gate B attempt failed instantly with EACCES because the operator shell's `HERBIE_SAVE_DIR` pointed at the scheduler-owned cache. The spike now forces an isolated per-run cache (292 MiB for the full run). Phase 3 relevance: the production member loop runs inside the scheduler unit and owns its cache, so this is a spike-operator concern only — but any operator-run backfill tooling should inherit the same isolation pattern.
4. Fetch was cleaner than budgeted: zero retries/failures across 2,009 requests, all served by aws on first attempt; the plan's rate-limit risk did not materialize at `--parallel 2` with per-var fetch. [measured on one run; sustained-schedule behavior still to be observed in Phase 3.]
5. Minor artifact: per-member `frame_time_p95_s` can read lower than `frame_time_mean_s` (e.g. m07: mean 0.803 vs p95 0.767) — a single slow outlier frame skews the mean; harmless.

Post-sign-off housekeeping: run the spike's `--cleanup --run 20260705_12z` (records tree sizes to `cleanup.json`, then deletes slim/comparison/cache trees ≈ 2.3 GB; results.json and spike.log are kept).

---

## 10. GO / NO-GO (Brian — recorded 2026-07-06)

Per the plan's hard gate: sign-off on a **specific tier + retention combination**, recorded here, before any work beyond the spike.

- **Tier 1 (slim, meteogram-only): GO** — member retention at **6-run parity**.
- **Tier 2 (stats grids): CONDITIONAL GO** — at **6-run parity** retention (not 2-run); proceeds only after the percentile implementation is replaced with a performant one (the naive `np.nanpercentile` pass measured in Section 7 is not acceptable for production; see plan Phase 6 precondition).
- **Tier 3 (full-profile served members): NO GO** — not something the server can support until resources are expanded at a later date. Requires its own fresh sign-off if revisited.
- **Global coverage: not supported for any tier at this time.** Design and planning must nonetheless keep global support a first-class future path (region always parameterized, per-frame meta as geometry authority, no `na`/CONUS hardcoding — plan Sections 1/3.7) so it is straightforward to incorporate when that time comes.

Signed: Brian Austin (recorded at Brian's direction) — Date: 2026-07-06
