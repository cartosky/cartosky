# Build Pipeline Roadmap — remaining audit items (2026-07-14)

Execution plan for everything still open in `BUILD_PIPELINE_AUDIT_2026-07-07.md`
after the §7 quick-wins list was completed (2026-07-14). Finding numbers below
refer to that document. This roadmap supersedes the audit's original
"medium projects / larger refactor" sequencing.

**Ordering principle:** hybrid risk/dependency, not HIGH-before-MED. Several MED
findings can silently ship wrong data and outrank HIGH perf items. Two hard
dependencies drive the wave structure:

1. **Validity semantics (1.3/1.7/1.9) must settle before the 5.1 refactor** —
   consolidating five copies of the cumulative skeleton around undecided
   semantics would preserve or obscure the divergences the refactor exists to
   eliminate.
2. **Fetch safety (4.5/4.6/3.10/3.8) must precede fh-parallelism (3.1)** —
   added concurrency amplifies full-file fallbacks, temp-file races, hung
   requests, and poison-frame retries.

**Process gates (every wave):** each behavioral fix lands with a narrow
regression test demonstrated to fail against pre-fix code; non-trivial
correctness, scheduling, and publication changes get a fresh-context
verification pass before merge. Waves are an *ordering*, not a serialization —
independent items within and across adjacent waves may proceed in parallel
where no listed dependency applies.

---

## Wave 0 — Decisions, validation, and observability (all independent)

1. **1.5 exposure check. — DONE 2026-07-14: exposed → Wave 1 item 7 promoted.**
   Determine whether the 2×-boosted ptype snow component planes reach value
   sampling (sample/binary-sampling API). The vars are
   `internal_only`/`buildable=False`, but per the canary-script findings
   `buildable=False` does not imply unpublished.
   *Result:* no sampling entry point validates the variable against the
   catalog — `/api/v4/sample`, `/api/v4/sample/batch`, and the meteogram path
   all resolve whatever artifact exists on disk (`internal_only` is enforced
   only in the capabilities serialization, `serialization.py:132-135`, i.e.
   discovery-level only). The component planes are published with packing
   configs (grid.py GFS 849-865, ECMWF 669-685) as companions of
   `ptype_intensity`. Live probe against `api.cartosky.com` (gfs 20260714_12z):
   `ptype_intensity_{rain,snow,ice}` all return HTTP 200 with numeric values
   (`noData:false`) to unauthenticated requests. Reachable by any client that
   knows the id string; no first-party UI surface requests them.
2. **Post-4.1 GEFS production canary.** One member pass on a live run:
   confirm member pending clears, no `cumulative rebase failed` errors, and
   percentile/probability frames continue through the run. (Left over from the
   4.1 close-out checklist.)
3. **Persistent `skipped_incomplete` alerting.** Alert when the same
   (var, fh) stats unit stays `skipped_incomplete` across N consecutive
   passes. Pure observability, zero data risk, and the only detection layer
   for 4.1-class member wedges — do not wait for Wave 2.
4. **2.1 telemetry check.** Instrument before deciding: a counter on the
   frames route distinguishing 404s within ~1 s of a publish swap (residual
   2.1 rename-gap class) from stale-run-id 404s (the fixed 2.2 class). A week
   of data answers whether Wave 5's atomic-pointer work is warranted.
5. **2.3 `MALLOC_ARENA_MAX=2` canary.** One host, isolated, measured
   (before/after RSS). No bundled memory changes in the same deploy.
6. **Dedicated `stats.py` audit.** Read-only; per the 4.1 scope note: retry and
   error handling, `sorted_nanpercentile`/`prob_exceedance` math, RSS behavior
   at the member-stack decode, interaction with run retention. Blocks nothing;
   should complete before Wave 4's concurrency work.
7. **Validity-semantics note (one document covering 1.9 + 1.3 + 1.7).**
   Decide once: what makes a step valid; how validity propagates across
   cumulative steps (AND/NaN vs degraded-quality flags); when fractional masks
   stay fractional; NaN vs degraded-quality behavior at the frame level;
   incremental-cache invalidation on semantic change. Waves 1's items
   implement this note.
8. **Fail-closed Kuchera-gate decision (left open by 1.2).** Product decision:
   on csnow fetch failure, does the gate stay open (all precip counts as snow,
   flagged degraded — current behavior) or fail closed (zeros)?
   **Trap set on purpose:** the 1.2 regression tests deliberately pin the
   current fail-open behavior (`test_kuchera_ptype_gate_fallback_records_quality_flag`
   asserts `data > 0` under fallback). If the decision flips, those assertions
   must be inverted intentionally in the same PR.

## Wave 1 — Cache safety, then silent correctness

1. **Cumulative cache fingerprint (promoted 1.10 bullet — land first).**
   The cumulative cache key is `run/var/fh/grid[:manual version]`; the version
   is a hand-maintained hint. Replace with a stable fingerprint of
   (a) normalized accuracy-relevant hints, (b) strategy identity, and (c) an
   **explicit per-strategy algorithm revision** — hints alone are blind to
   code-only semantic changes like 1.7. Test: prior caches with a mismatched
   fingerprint/revision are ignored. **Every 1.7/1.6/1.3 change below must
   bump the revision in the same PR**; ship this item atomically with the
   first of them at the latest. Without this, a semantic change deployed
   mid-run blends old- and new-semantics frames via the prior-cumulative cache.
2. **1.8 → 4.8.** NaN-safe radar-ptype argmax (`nan_to_num` guard, mirroring
   the intensity paths); one-line `len(colors) == len(levels)-1` validation on
   discrete colormap specs.
3. **1.9.** Standardize per-step validity (`isfinite & >= 0` via a shared
   helper) per the Wave 0 validity note.
4. **1.7.** Binarize ptype-accumulation masks only when `snow_mask_threshold`
   is explicitly configured; otherwise keep the fractional mean (mirror
   snowfall). Bump the cumulative algorithm revision. **Visible data change:**
   GEFS-mean ice accumulation goes from ~0 to real values — produce
   before/after frames and a release note stating the newly visible ice is the
   corrected result (same discipline as the tmp850_anom °C switch).
5. **1.6.** Replace the `nanmax > 1.5` percent-vs-fraction heuristic with
   explicit `probability_units` metadata on the component spec. Bump affected
   strategy revisions (feeds the Kuchera gate's csnow normalization).
6. **1.3.** Implement the chosen cross-step validity propagation
   (AND-validity/NaN or per-var degraded flags) and quality persistence,
   including the incremental-resume case so pre-change cached state cannot
   contaminate later frames. Bump affected strategy revisions.
7. **1.5 fix (confirmed exposed by the Wave 0 check, 2026-07-14):** store
   unboosted rates in family/component planes (boost only in index binning)
   and hoist the 4× hardcoded `2.0` constant.

## Wave 2 — Member and artifact integrity

1. **4.2.** Pin the pf band→member mapping invariant: cross-check each band's
   perturbation number from GRIB band metadata against the index-derived
   number, and/or pin fetch.py's byte-range write-sort contract with a test.
2. **4.7.** Replace the dead `np.to_numeric` member sort (`pd.to_numeric`) —
   currently an AttributeError swallowed on every call.
3. **4.10 `<u2` bullet.** `_decode_member_frame` decodes with the configured
   packing dtype instead of hardcoded `<u2`.
4. **4.9.** Failure cleanup deletes only the failed fh's contour geojson, not
   the variable's whole shared contours directory.

## Wave 3 — Fetch reliability

1. **4.5.** wgrib2-style idx: emit an open-ended `Range: bytes={start}-` for
   the file's final record (removes a deterministic full-file-fallback
   trigger).
2. **4.6.** Unique temp names + atomic `replace` for full-GRIB downloads;
   wall-clock deadline; fix the 8 s lock-timeout-vs-multi-GB-download mismatch.
3. **3.10.** Deadlines on Herbie-internal calls; cap the inventory follower
   wait at 60–90 s independent of cache TTL.
4. **3.2.** Retry a failed range request 2–3× with backoff before the
   full-file fallback; cap fallback by Content-Length; route it through the
   EPS full-file cache when enabled.
5. **3.4.** Decouple cached-subset reuse from the disk-lock flag
   (`_subset_file_status` check + `overwrite=False` in both branches).
6. **4.4 retry/cache work.** Shared `_is_*_error` classification across the
   four retry-loop copies; run-aware negative cache so the doomed direct-mean
   attempt is skipped while a run is incomplete (re-probe after the frontier
   passes a late-fh threshold); cache the terminal statistics file's parsed
   inventory once per run.
7. **3.11 high-value bullets.** Module-level pooled `requests.Session` sized
   ≥ range workers (measured: ~125 ms/request × ~50 ranges per EPS mean
   variable per fh); stream pf range payloads to disk with a bounded window
   instead of holding ~51 in RAM.
8. **3.8.** Persist per-(run, var, fh) failure counts across `_process_run`
   calls with a cap/backoff; classify deterministic vs transient failures.

**`tmp850__mean` direct-mean rerouting (4.4) stays behind a parity gate.**
CartoSky's PF mean averages only `type == "pf"` inventory rows (50 perturbed
members; the control is excluded by the inventory filter in
`_fetch_ecmwf_pf_mean_variable`). ECMWF's `em` product is their official
ensemble mean and public documentation does not pin whether it includes the
control member. Before rerouting: compare `em` against the current PF mean on
a completed run — identical fhs and grid, including packed-value/LSB
differences. If materially different, keep the PF mean unless deliberately
redefining the product, with documentation. (Note: the plan-review citation of
`number_values > 0` at fetch.py ~2085 points into
`_ecmwf_pf_mean_from_xarray_result`, which is **dead code with no callers** —
delete it in Wave 6/5.3; the live exclusion is the `type == "pf"` filter.)

## Wave 4 — Measured performance

1. **3.6 first.** Per-phase `perf_counter` deltas in the existing Step-N logs
   with model/var/fh attribution, plus publish-snapshot timing. Current logs
   cannot attribute frame time to fetch vs warp vs colorize vs artifact write,
   and 3.1 sizing needs that data.
2. **3.5.** Metadata-only colorization path (the full RGBA array is currently
   computed and discarded per frame, ×51 in member passes).
3. **3.7.** Compute the shared warped component once per frame for contour and
   pressure-center paths (also fixes the hardcoded-bilinear cache-key
   mismatch).
4. **3.1 last.** Rolling/streaming fh submission replacing the
   one-ThreadPool-per-round barrier (observed: a fast variable idle ~85% of
   round wall-clock), sized from 3.6 measurements, protected by Wave 3's
   reliability work. Needs per-fh readiness-cache keys (done, 2.6) and a
   FetchContext strategy for non-cumulative vars.

## Wave 5 — Publication

1. **3.9.** Incremental promote: frame files are immutable once written, so
   additive in-place hardlinking replaces the two O(total-frames) copytree
   passes per progress publish.
2. **2.1, only if Wave 0 telemetry shows residual swap-window 404s.** True
   atomic publication (single pointer/symlink repoint or
   `renameat2(RENAME_EXCHANGE)`), designed together with 3.9 — not a
   standalone symlink redesign without production evidence.

## Wave 6 — Cleanup and refactoring

1. **Remaining umbrella bullets** from 1.10 (rain-bin count, `min_step_lwe`
   asymmetry documentation, vort500 dateline note, per-pixel SLR-fallback
   flag), 4.10 (string-match exception classification, temp-artifact TTLs,
   precheck fail-open cap, retry-scaffolding dedup), and 3.11
   (savez compression, unicode ptype arrays, float64 climatology-warp
   promotion, inventory batching, memory-audit log gating, frontier stat
   churn, member pending-scan cost, retry jitter, colorize transpose copy).
2. **5.3.** Delete dead/misleading surface: unused `DeriveStrategy` metadata
   (or enforce it), `scale_divisor`, the dead wspd double-raise, the unreachable
   `rebuild_existing` parallel branch, the ~200 lines of dead loop-pregeneration
   plumbing, and `_ecmwf_pf_mean_from_xarray_result` (dead, no callers).
3. **5.4.** Make contracts explicit: structured grid-id instead of the
   stringly-typed climatology format; declare `data_root` /
   `kuchera_cumulative_cache` as real `FetchContext` fields.
4. **5.2.** Mechanical dedup (~400 lines). Includes the 2.5 residual, stated
   precisely: **resolve the Herbie model id via `plugin.herbie_request().model`
   at derive.py's Kuchera fetch sites** (`_kuchera_inventory_lines`,
   `_resolve_apcp_step_data`, `_ptype_intensity_fetch_step_intensity`) — these
   parameters are *live with a wrong value for ECMWF*, not dead; today the
   internal-id guard fires there and is swallowed to `[]`, benign only because
   ECMWF's `:APCP:surface:` inventory is empty. Also remove the now-`del`'d raw
   `model_id` parameter from `_component_precheck_available` (that one *is*
   dead).
5. **5.1 last.** Extract the incremental-cumulative orchestrator
   (`_run_incremental_cumulative` with a `process_step` callback) behind
   per-model parity tests and canaries — ~1,000 lines of dedup across five
   strategies, eliminating the shipped mismatch-handling divergence. Only
   after Wave 1 settles the semantics it would consolidate.

---

## Dependency summary

```
Wave 0 (decisions/observability, all parallel)
  ├─ validity note ──► Wave 1 items 3–6
  ├─ 1.5 exposure (DONE: exposed) ──► Wave 1 item 7
  ├─ 2.1 telemetry ──► Wave 5 item 2 (go/no-go)
  └─ stats.py audit ──► before Wave 4 concurrency
Wave 1 item 1 (cache fingerprint) ──► atomically with first of 1.7/1.6/1.3
Waves 2–3 (integrity + fetch safety) ──► Wave 4 item 4 (3.1 parallelism)
Wave 4 item 1 (3.6 timings) ──► Wave 4 items 2–4 sizing
Wave 5 item 1 (3.9) ──► co-designed with 2.1 if telemetry warrants
Wave 1 (semantics settled) ──► Wave 6 item 5 (5.1 refactor)
```
