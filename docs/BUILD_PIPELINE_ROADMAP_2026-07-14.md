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

~~1. 1.5 exposure check.~~ **— DONE 2026-07-14: exposed → Wave 1 item 7 promoted.**
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
~~2. Post-4.1 GEFS production canary.~~ **— DONE 2026-07-14: PASSED.** One member
   pass on a live run: confirm member pending clears, no `cumulative rebase
   failed` errors, and percentile/probability frames continue through the run.
   *Result:* gefs 20260714_12z member pass on the restarted (post-d92dfd3a)
   scheduler: `Member pass summary … written=7998 complete=True
   preempted=False`, `Stats pass summary … written=2060 complete=True`, zero
   `cumulative rebase failed`, zero `derived schedule invalid`,
   no pending-scan failures. Counts exactly match the pre-4.1 baseline runs
   (00z/06z same day), and percentile/probability products
   (`tmp2m__p50/__prob_gt_*`, `snowfall_total__p90`,
   `precip_total__prob_gt_1p0`) serve real values through terminal fh384 via
   the sample API. Ops note: scheduler units load code at process start —
   a repo pull alone does not deploy to a running unit (the first canary
   attempt observed pre-4.1 passes for exactly this reason).
~~3. Persistent `skipped_incomplete` alerting.~~ **— DONE 2026-07-14
   (Codex, 35533433):** per-(model, run) ensemble stats health JSON under
   `data_root/status/ensemble_stats/`, consecutive-pass tracking wired into
   stats.py, `alerting=true` once the same unit stays `skipped_incomplete`
   for ≥3 consecutive passes; surfaced via `/api/v4/admin/status/results`
   and the admin status page ("Stats roster incomplete" issue type).
   Original scope: alert when the same (var, fh) stats unit stays
   `skipped_incomplete` across N consecutive passes — the only detection
   layer for 4.1-class member wedges.
~~4. 2.1 telemetry check.~~ **— INSTRUMENTED 2026-07-14 (verifier-CONFIRMED);
   decision pending ~1 week of prod data after deploy.** Instrument before
   deciding: a counter on the frames route distinguishing 404s within ~1 s of
   a publish swap (residual 2.1 rename-gap class) from stale-run-id 404s (the
   fixed 2.2 class). A week of data answers whether Wave 5's atomic-pointer
   work is warranted.
   *Implementation:* `frames_404_telemetry.py` service — all 404 sites on
   `_get_grid_file`/`list_frames`/`get_grid_manifest` classified as
   `stale_run` / `swap_gap` (file missing but listed in current manifest, the
   2.1 signature) / `manifest_skew` (file present, manifest unlisted) /
   `not_published` / `manifest_missing` / `size_mismatch` / `not_supported`,
   with `seconds_since_publish` recency buckets (lt1s/lt5s/gte5s) on the two
   swap classes. Persisted JSON at `data_root/status/frames_404/telemetry.json`
   (14-day retention, survives API restarts), Prometheus
   `cartosky_frames_404_total{endpoint,reason}`, and a "Frame 404 telemetry"
   panel on the admin /status page (Codex #3 pattern). Responses verified
   byte-identical; zero added work on 2xx paths. **Decision rule:** if
   `swap_gap`+`manifest_skew` stay ~0 over a week while `stale_run` carries
   the volume, Wave 5 item 2 is not warranted.
5. **2.3 `MALLOC_ARENA_MAX=2` canary.** One host, isolated, measured
   (before/after RSS). No bundled memory changes in the same deploy.
~~6. Dedicated `stats.py` audit.~~ **— DONE 2026-07-14: see
   `STATS_AUDIT_2026-07-14.md`.** Read-only; per the 4.1 scope note: retry and
   error handling, `sorted_nanpercentile`/`prob_exceedance` math, RSS behavior
   at the member-stack decode, interaction with run retention. Blocks nothing;
   should complete before Wave 4's concurrency work.
   *Result:* no HIGH-severity code defects; math verified to numpy parity with
   safe sentinel round-trips. 8 findings dispositioned into waves — notable:
   sidecar-resume atomicity gap (→ Wave 2), poison stats unit retries forever
   invisible to health alerting (→ extend #3 / Wave 3 3.8), `rss_peak_mb` is
   process-lifetime so the ~3 GB belongs to the member pass not stats
   (stats-unit true peak ≈ 550-580 MB; MALLOC canary interpretation
   unaffected), health-JSON retention leak (→ Wave 2/6), and two NEW product
   decisions for this Wave 0 list: prob equality-exclusion at quantized
   thresholds (P(>32)+P(<32) ≪ 100% at the freezing line) and no
   minimum-valid-count mask at coverage fringes.
~~7. Validity-semantics note (one document covering 1.9 + 1.3 + 1.7).~~
   **— DONE 2026-07-14: `VALIDITY_SEMANTICS_2026-07-14.md`, D1-D5 decided
   (operator sign-off).** Decisions: D1 shared step-validity helper
   (`isfinite & >= 0`, masks also `<= 1`; negatives invalid, not clamped);
   D2 OR-merged totals + mandatory persisted `accum_step_gap` quality flags
   with affected-pixel percentage, exposed in admin telemetry — coverage
   floor reconsidered only if telemetry warrants; D3 fractional masks by
   default, binarize only on explicit threshold hint (visible GEFS-mean ice
   change, release note); D4 NaN strictly = "no defined value", imperfection
   carried by sidecar quality flags only; D5 every semantic change bumps the
   cumulative algorithm revision, cache schema gains persisted flags,
   pre-change entries recompute. Waves 1's items implement this note.
~~8. Fail-closed Kuchera-gate decision (left open by 1.2).~~ **— DECIDED
   2026-07-14 (operator): neither numeric fallback. Fail the frame
   transiently.** Both roadmap options publish believable wrong data: ones
   paints warm rain as multi-inch snow; zeros permanently undercounts a
   cumulative product via the prior-cumulative cache even after csnow
   recovers. Decision:
   - **Zero valid csnow samples for a required step → raise
     `HerbieTransientUnavailableError`;** no Kuchera frame publishes for that
     fh; `build_frame`'s existing transient path (pipeline.py, cleans partial
     artifacts, `transient_unavailable`) lets the scheduler retry on a later
     pass; already-published good frames are preserved; the missing frame
     surfaces through existing status/incompleteness monitoring.
   - **Partial interval coverage (≥1 valid sample) → publishable but
     degraded:** compute from available samples (current averaging behavior)
     and record a new `ptype_gate_partial_coverage` quality flag.
   - **Shape/contract errors → hard failure** (retries cannot repair those;
     already the behavior).
   - **Accepted consequence, stated deliberately:** Kuchera is cumulative, so
     a step whose csnow never materializes upstream blocks every later fh's
     Kuchera frame for that run (absence instead of the old flagged-wrong
     frames). Rare — rejection requires ALL interval samples missing — and
     bounded by Wave 3 3.8 backoff; retries are cheap (prior-cumulative cache
     reuse means only the missing step refetches).
   - **Sequencing: NOT a standalone edit at the all-ones site.** Land with
     the Wave 1 item 1 fingerprint work and bump the Kuchera strategy
     revision, or a run can reuse prior-cumulative caches still containing
     old fail-open (all-ones) contributions after deploy. Scope: GFS, HRRR,
     NAM (`kuchera_use_ptype_gate` catalogs).
   - **Tests:** all-samples-missing → transient rejection; scheduler retry
     eligibility; partial-coverage flagging; clean-path parity; prior-revision
     caches rejected. **Trap disarmed by PR B on 2026-07-15:** the former 1.2
     fail-open regression was intentionally replaced with transient-rejection
     assertions, and partial coverage now has its own persisted-flag test.
   → Implementation moved to Wave 1 item 8.
9. **Prob threshold equality semantics (stats audit S4).** Product decision:
   `prob_gt` uses strict `>` and `prob_lt` strict `<` (deliberate per the
   `stats_math.py` docstring), so members exactly at a quantized threshold
   count toward neither product — with 0.1 °F packing, P(>32)+P(<32) can fall
   well below 100% exactly at the freezing line. Options: make one side
   inclusive (the pair then partitions), offset shipped thresholds off the
   quantization grid (e.g. 31.95), or accept + document. Any flip is a
   visible data change on prob products → release note, and the
   `test_ensemble_stats_phase6` assertions that pin strictness must be
   inverted intentionally in the same PR (same trap discipline as #8).
10. **Minimum-valid-count mask for stats (stats audit S5).** Product decision:
   per-pixel valid member counts at coverage fringes can be 1-3 of 50, yet
   probabilities publish unmasked (3/3 members above threshold → 100%) and a
   1-valid pixel returns one value at every percentile (flat spread = false
   certainty). Decide whether to NaN-mask pixels below a `valid >= k` floor;
   visible data change at grid edges if adopted.

## Wave 1 — Cache safety, then silent correctness

~~1. Cumulative cache fingerprint (promoted 1.10 bullet — land first).~~
   **— DONE 2026-07-14, verifier-CONFIRMED (50 tests green).** The cumulative
   cache key is now
   `{native|warped:grid:resampling}:s={strategy_id}:r={revision}:h={sha256-12
   of sorted-JSON hints}` with revisions in `CUMULATIVE_ALGORITHM_REVISIONS`
   (derive.py, all five strategies at 1; unknown id fails loud). Deliberate
   deviation from the original text: the hash covers ALL selector hints, not
   a curated accuracy-relevant subset — curation has the same blind-spot
   failure mode this item exists to kill, and over-invalidation costs one
   deploy-time recompute. The manual `cumulative_cache_version` hint still
   works (flows through the hash). Load-time exact-string validation rejects
   legacy/mismatched entries → recompute, no schema change. Cross-strategy
   fix included — THREE precip-cache readers (Kuchera apcp seed, 10:1
   snowfall incremental reuse, GFS ptype incremental reuse; the latter two
   found by external Codex review after the first pass caught only Kuchera)
   now compute the precip read's key with the WRITER's identity
   (`_precip_seed_cache_key`: precip_total_cumulative + the precip var's own
   spec hints, incl. GEFS `precip_total__mean`) — reader-identity keys
   always-missed, and for snowfall/ptype that disabled incremental reuse
   entirely (quadratic rebuild). Regression guard: reader==writer key
   equality pinned against REAL registry specs for all three pairs (stub
   mocks discard grid_cache_key and cannot catch this class); verifier also
   traced pipeline's `_resolve_model_var_spec` to the identical
   get_var(normalize_var_id()) path the seed helper uses.
   **Every 1.7/1.6/1.3/Kuchera-gate change below must bump the strategy's
   revision in the same PR.** Deploy note: all existing cumulative caches
   miss once post-deploy (one-time recompute per in-flight run); scheduler
   restarts required as usual.
~~2. 1.8 → 4.8.~~ **— DONE 2026-07-14 (Codex, PR #38, reviewed +
   verifier-CONFIRMED).** NaN-safe radar-ptype argmax (`nan_to_num` guard,
   mirroring the intensity paths — also fixed a second NaN bug in the
   freezing-rain transition path); discrete colormap validation accepts BOTH
   live conventions (N-color lower-bound tables and N-1 boundary tables —
   the audit's N-1-only rule would have broken nine working specs) and
   rejects everything else.
~~3. **1.9.** Standardize per-step validity (`isfinite & >= 0` via a shared
   helper) per the Wave 0 validity note.~~ **— DONE 2026-07-15 (PR A, local).**
   Negative scalar steps are now invalid rather than clamped to valid zero;
   mask validity uses the same helper with the upper bound enforced. Revision
   bump: `precip_total_cumulative` only (the sole strategy whose validity
   behavior changes — the others already enforced this). **Follow-up
   2026-07-15:** the GEFS member-pass `precip_step_contribution` helper was
   brought into the same finite-and-nonnegative contract after its production
   parity test exposed the stale finite-only behavior; a negative-sentinel
   regression now pins the member path too.
~~4. **1.7.** Binarize ptype-accumulation masks only when the threshold hint
   (`ptype_mask_threshold`) is explicitly configured; otherwise keep the
   fractional mean (mirror snowfall).~~ **— DONE 2026-07-15 (PR A, local).**
   Revision bump:
   `ptype_accumulation_cumulative` only (the ECMWF strategy has no
   binarization site). **Scope correction (Codex review, 2026-07-15,
   verified):** there is NO live behavior change — the only current
   `ptype_accumulation_cumulative` product is GFS `ice_total`, which pins
   `ptype_mask_threshold: "0.5"` explicitly (gfs.py), and GEFS has no
   ice/ptype-accumulation product at all. The audit's "GEFS-mean ice goes
   from ~0 to real values" consequence was hypothetical; the before/after
   release-note requirement is dropped. This item hardens the default for
   future fractional (ensemble-mean) products; adding GEFS ice would be a
   separately scoped feature.
~~5. **1.6.** Replace the `nanmax > 1.5` percent-vs-fraction heuristic with
   explicit `probability_units` metadata on the component spec (feeds the
   Kuchera gate's csnow normalization). Consumers are the non-persistent
   ptype intensity paths + the Kuchera gate, so the only cumulative revision
   bump is `snowfall_kuchera_total_cumulative` — shared with item 8's bump
   in the same PR.~~ **— DONE 2026-07-15 (PR B, local):** normalization now
   requires explicit `fraction` or `percent` units; GFS, HRRR, and NAM
   categorical ptype components declare `fraction`; and a sparse percent
   regression proves values such as 1.2% remain 0.012 instead of clipping to
   1.0. Unknown or missing unit metadata fails loudly.
~~6. **1.3.** Implement the decided cross-step validity propagation (validity
   note D2: OR-merged totals + mandatory `accum_step_gap` flags with
   affected-pixel percentage, persisted through the cumulative cache schema,
   exposed in admin telemetry; flags coverage extended to precip_total /
   10to1 / GFS ptype which record none today) and quality persistence,
   including the incremental-resume case so pre-change cached state cannot
   contaminate later frames. Revision bump: all five cumulative strategies
   (the cache entry schema itself changes).~~ **— DONE 2026-07-15 (PR C,
   local):** all five cumulative strategies retain OR-merged totals while
   recording `accum_step_gap` plus affected-pixel percentage; flags/details
   survive incremental cache resume; an internal boolean gap mask preserves
   the exact affected-pixel union across resumed frames; disk/in-memory
   entries missing the new quality schema recompute; and all five strategy
   revisions are bumped. Published
   sidecars carry the details, while `/admin/status` marks affected runs as
   warnings with a dedicated Accum gaps count/filter and per-variable detail.
   Regression coverage pins partial-step totals/never-valid NaN behavior,
   resume persistence, legacy-cache rejection, sidecar metadata plumbing,
   and status-API exposure.
~~7. **1.5 fix (confirmed exposed by the Wave 0 check, 2026-07-14).~~ —
   **DONE 2026-07-14, verifier-CONFIRMED:** unboosted rates stored in
   family/component planes (GFS family storage + ECMWF component access);
   boost applied only inside index binning via the hoisted
   `PTYPE_SNOW_DISPLAY_BOOST` constant, so indexed/rendered output is
   bit-identical. Regression test pins both halves (plane unboosted, binning
   still boosted) and was demonstrated RED pre-fix. No cache/fingerprint
   coupling needed: the plane's only consumer is the published component
   artifact — accumulations use categorical masks + precip, the family cache
   is per-frame, and no cumulative cache stores these planes. Already-written
   frames keep 2× values until aged out (tmp850_anom precedent).
~~8. **Kuchera gate transient-fail (decided Wave 0 #8).** Implement per the
   decision recorded there: zero-valid-csnow-sample steps raise
   `HerbieTransientUnavailableError` (frame rejected via build_frame's
   transient path, retried later; good frames preserved); partial coverage
   publishes with the new `ptype_gate_partial_coverage` flag; bump the
   Kuchera strategy revision in the same PR (requires item 1); invert the
   1.2 fail-open test assertions intentionally. Scope: GFS/HRRR/NAM.
   **Ordering: land AFTER item 6** — the partial-coverage flag needs item
   6's flag persistence through cumulative-cache resume, or later frames
   reuse the degraded contribution while losing its warning (the 1.2 known
   limitation applied to a brand-new flag).~~ **— DONE 2026-07-15 (PR B,
   local):** zero usable interval samples now reject transiently instead of
   falling back to all ones; available samples still average when coverage is
   partial and emit `ptype_gate_partial_coverage`, which is persisted in the
   cumulative cache. The Kuchera algorithm revision is bumped once for the
   combined items 5+8 semantic change. Spatial csnow gaps keep dry pixels at
   valid zero, invalidate only precipitating pixels without a gate value, and
   recover on later valid steps identically across full rebuild and cache
   resume.

**PR grouping for items 3-8 (agreed with Codex review, 2026-07-15):**
three PRs, each internal fix RED-tested as its own commit, merged in order
**PR A (items 3+4) → PR C (item 6) → PR B (items 5+8)**. C precedes B so
flag persistence exists before item 8 introduces a new flag. Revision-bump
scopes: A → precip_total_cumulative (item 3) + ptype_accumulation_cumulative
(item 4); C → all five; B → snowfall_kuchera_total_cumulative only.

## Wave 2 — Member and artifact integrity

~~1. **4.2.** Pin the pf band→member mapping invariant: cross-check each band's
   perturbation number from GRIB band metadata against the index-derived
   number, and/or pin fetch.py's byte-range write-sort contract with a test.~~
   **— DONE 2026-07-16 (PR A, local):** a direct local-source regression pins
   sorted, deduplicated range writes against shuffled inventory input.
~~2. **4.7.** Replace the dead `np.to_numeric` member sort (`pd.to_numeric`) —
   currently an AttributeError swallowed on every call.~~ **— DONE 2026-07-16
   (PR A, local):** the stable sort now uses `pd.to_numeric`; a string-member
   regression distinguishes numeric `1, 2, 10` order from raw/lexical order.
3. **4.10 `<u2` bullet.** `_decode_member_frame` decodes with the configured
   packing dtype instead of hardcoded `<u2`. (Stats audit S2 note: on a
   non-u16-packed stats var this raises every pass while the size gate —
   which uses the configured dtype — passes, creating exactly the poison
   stats unit of item 6 below.)
4. **4.9.** Failure cleanup deletes only the failed fh's contour geojson, not
   the variable's whole shared contours directory.
5. **Stats sidecar in the resume check (stats audit S1).** The stats-unit
   resume check (`member_frame_is_complete`) validates only bin+meta; the
   `fh{NNN}.json` sidecar is written after them, so a crash in that window
   resumes the frame as complete with `valid_time` permanently missing.
   Include sidecar existence in the resume check (or write the sidecar
   first). Regression test: bin+meta present, sidecar absent → unit recomputes.
6. **Stats-unit error-streak visibility + health-file retention (stats audit
   S2 + S8).** (a) `ensemble_stats_health` tracks only roster-incomplete
   units — persistent `error`/`gate_failed` units retry every cycle forever
   (re-decoding the full roster) and never alert; count those streaks in the
   health JSON too (cap/backoff itself belongs with Wave 3's 3.8). (b) Run
   retention never prunes `status/ensemble_stats/{model}/`; wedged runs
   strand one health file each, unbounded — prune it against kept runs.

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
   **Extend to stats units (stats audit S2):** a persistently erroring
   (var, fh) stats unit currently retries every scheduler cycle forever with
   no cap — apply the same failure classification and backoff there.

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
   and 3.1 sizing needs that data. **Add per-pass RSS deltas (stats audit
   S6):** the logged `rss_peak_mb` is process-lifetime `ru_maxrss`, so member
   and stats pass footprints are indistinguishable today (the ~3 GB EPS
   readings belong to the member pass; the stats unit's own peak is
   ~550-580 MB).
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
   **Plus stats-audit fold-ins:** S7 memory trims in `_process_stats_unit`
   (drop the `frames` list pin after extracting `meta` — ~173 MB off the EPS
   unit peak; optionally probs-before-percentiles + in-place sort for ~173 MB
   more), S3 documentation (stats are never invalidated if a member input is
   rewritten post-compute — presence-only idempotency), and S9 minors
   (error over-count on mid-loop failure, `build_stats_plan` ValueError
   escaping the pending scan to the whole-model catch-all, per-member
   transform-equality assertion, float64 temporaries in `stats_math`,
   sign-less prob-threshold id grammar blocking sub-zero thresholds).
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
  ├─ stats.py audit (DONE) ──► items 9/10 above + Wave 2 items 5–6,
  │                            Wave 3 item 8 stats extension, Wave 4 RSS deltas
  └─ prob semantics (item 9) ──► any flip needs release note + test inversion
Wave 1 item 1 (cache fingerprint) ──► atomically with first of 1.7/1.6/1.3
Waves 2–3 (integrity + fetch safety) ──► Wave 4 item 4 (3.1 parallelism)
Wave 4 item 1 (3.6 timings) ──► Wave 4 items 2–4 sizing
Wave 5 item 1 (3.9) ──► co-designed with 2.1 if telemetry warrants
Wave 1 (semantics settled) ──► Wave 6 item 5 (5.1 refactor)
```
