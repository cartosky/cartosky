# Ensemble stats pipeline audit — 2026-07-14

Roadmap Wave 0 item 6 (`BUILD_PIPELINE_ROADMAP_2026-07-14.md`). Read-only; no
fixes implemented. Scope per the 4.1 close-out note: retry/error handling,
`sorted_nanpercentile`/`prob_exceedance` math, RSS behavior at the
member-stack decode, and interaction with run retention.

Method: four parallel audit passes (error-handling/atomicity, math,
memory, retention/lifecycle) over `backend/app/services/builder/stats.py`
(522 lines), `stats_math.py`, the decode/write path in `members.py`/`grid.py`,
`ensemble_stats_health.py`, and the scheduler call sites; numeric claims
verified by executing the actual functions; the two action-driving findings
(S1, S4) independently re-verified against source by the operator session.

Known findings NOT re-reported: 4.10 `<u2` hardcode in `_decode_member_frame`
(Wave 2 item 3) — though see S2 for how it compounds; 4.7 dead member sort.

---

## Summary

| # | Sev | Area | Finding | Disposition |
|---|-----|------|---------|-------------|
| S1 | MED | atomicity | Crash between meta and sidecar writes → frame resumes as complete, `valid_time` sidecar lost forever | fold into Wave 2 |
| S2 | MED | retry | Persistent per-unit decode/gate failure retries every pass forever; invisible to health alerting (roster-only) | extend Wave 0 #3 alerting; backoff with Wave 3 3.8 |
| S3 | MED | idempotency | Stats never invalidated if a member input is rewritten post-compute | document; accept (Wave 6 note) |
| S4 | MED | math/product | Strict `>`/`<` (documented): members exactly at a quantized threshold count toward neither product; P(>32)+P(<32) can drop far below 100% at the freezing line | product decision → Wave 0 decision list |
| S5 | MED | math/product | No minimum-valid-count mask: 1-3 valid members at coverage fringes produce confident probabilities/flat percentiles | product decision → Wave 0 decision list |
| S6 | — | observability | `rss_peak_mb` in Stats pass summary is process-lifetime `ru_maxrss`; stats' own peak ≈ 550-580 MB (EPS) — the ~3 GB belongs to the member pass | interpretation note; per-pass RSS delta belongs in Wave 4 3.6 |
| S7 | MED | memory | `frames` list pinned via `meta = frames[0][1]` keeps 50 decoded arrays (~173 MB EPS) alive alongside `stack`; in-place sort after probs would save another ~173 MB | Wave 6 (3.11 family) |
| S8 | MED | lifecycle | `status/ensemble_stats/{model}/{run}.json` never pruned; wedged runs strand health files forever once `published/` ages out | small fix, Wave 2/6 |
| S9 | LOW | various | see Low findings | as noted |

## Findings

### S1 MED — Sidecar not covered by the resume completeness check

**STATUS: FIXED 2026-07-16 (Wave 2 PR B, local).** Stats outputs now use a
stats-specific completeness helper requiring bin, grid meta, and the atomic
`fh{NNN}.json` sidecar. Pending, per-unit resume, and promotion checks all use
that contract; member roster inputs deliberately retain their bin+meta-only
contract. Regressions cover recomputation after a missing sidecar and both
staging/published promotion states.

`stats.py:293-296` resumes a product when `member_frame_is_complete` passes,
which validates only `.bin` + `.l*.meta.json` (`members.py:183-200`). The
per-frame sidecar `fh{NNN}.json` is written *after* the grid frames
(`stats.py:381-398`). A crash/OOM-kill in that window leaves bin+meta on disk;
every later pass records `STATUS_RESUMED` and never writes the sidecar. The
manifest builder takes `valid_time` from the sidecar (`grid.py:2143-2171`), so
the frame is served permanently without it, and the pass reports
`complete=True`. Fix shape: include sidecar existence in the resume check (or
write the sidecar first). The stat `.bin` itself is size-validated on resume,
so bin truncation self-heals — the gap is cross-file only.

### S2 MED — Poison stats unit: forever-retry, invisible to alerting

`STATUS_ERROR`/`STATUS_GATE_FAILED` units (`stats.py:360-366, 400-405`) never
write frames, so the unit stays pending and re-runs on every scheduler cycle —
re-decoding the full 50-member roster each time — with no retry cap, backoff,
or failure classification (the 3.8 concern, scoped to stats). The new health
tracker sees only *roster incompleteness*: `run_stats_pass` feeds it
`summary.incomplete_units` alone (`stats.py:479-485`), so a persistent
error/gate-failure streak never sets `alerting=true`. Concrete trigger already
in the codebase: a uint8-packed stats var would pass the size gate (uses
configured dtype) but raise every pass in `_decode_member_frame`'s hardcoded
`<u2` reshape (`members.py:1151`, known 4.10). Fix shape: count error/gate
streaks in `ensemble_stats_health` alongside incomplete rosters; add a
cap/backoff when Wave 3's 3.8 failure-classification work lands.

**2026-07-16 note:** Wave 2 PR B removed the concrete hardcoded-`<u2` trigger;
the general invisible forever-retry behavior remains open for Wave 2 item 6
and Wave 3 item 8.

### S3 MED — No input invalidation

The only idempotency signal is output-file presence (`stats.py:295`). If a
member frame is rewritten after its stats were computed (backfill/re-fetch),
the stale stat resumes forever. Members promote as complete sets, making this
rare; accept + document unless member rewrite paths grow.

### S4 MED (product decision) — Equality exclusion at quantized thresholds

`prob_exceedance` uses strict `>` and `prob_non_exceedance` strict `<`, and
the docstring pins this deliberately ("a member exactly at the threshold
counts toward neither product", `stats_math.py:64-111`). Because member frames
are quantized (tmp2m packs at 0.1 °F steps), members land *exactly* on round
thresholds like 32.0 °F routinely. Verified numerically: members
`[31.9, 32.0, 32.0, 32.1, 33.0]` → P(>32)=40%, P(<32)=20% — 40% of mass
uncounted, precisely at the rain/snow decision boundary the product exists
for. Options: make one side inclusive (`>=` on gt, keeping lt strict, so the
pair partitions); or offset shipped thresholds off the quantization grid
(31.95); or accept + document. Needs a deliberate call (same discipline as
the Kuchera fail-open decision, Wave 0 #8); any change is a visible data
change on prob products → release-note + revision-bump discipline.

### S5 MED (product decision) — No minimum-valid-count mask

The completeness gate is frame-level; per-pixel valid counts at coverage
fringes can be 1-3 of 50. `safe_valid = max(valid, 1)` (`stats_math.py:80`)
then yields e.g. P=100% from 3 members, and a 1-valid pixel returns that value
at every percentile (flat spread = false certainty). Decide whether to mask
(NaN) pixels below a `valid >= k` floor; visible data change if so.

### S6 — `rss_peak_mb` attribution (observability correction, not a bug)

`_rss_peak_mb()` (`stats.py:274-277`) returns `getrusage(RUSAGE_SELF)`
`ru_maxrss` — the monotonic *process lifetime* peak. The same process runs the
member pass (threaded fetch/warp) before stats. Reconstructed stats-unit peak
for EPS (50 × 913×947 f32): frames list ~173 MB + stack ~173 MB + sort copy
~173 MB + temporaries ≈ **550-580 MB**, one-fifth of the logged 2841-3031 MB.
The ~3 GB is the member pass surfacing through the shared counter.
Implications: (a) don't read Stats pass `rss_peak_mb` as the stats footprint;
(b) the MALLOC_ARENA_MAX canary metric remains valid — the member pass
dominates it and is exactly the threaded, arena-relevant workload (the stats
pass's large allocations exceed glibc's mmap threshold and bypass arenas
entirely); (c) per-pass RSS deltas belong in the Wave 4 item 1 (3.6) timing
instrumentation.

### S7 MED — Avoidable ~173 MB pin in the stats unit

`meta = frames[0][1]` (`stats.py:326`) keeps the whole `frames` list — 50
decoded arrays, ~173 MB EPS — referenced for the entire compute even though
`np.stack` already copied them. Extract meta, `del frames` before the math:
~30% off the unit peak. Optional second step: compute probabilities before
percentiles and sort in place (~173 MB more; `np.sort` copy at
`stats_math.py:42` is required only by the current call order). Wave 6 /
3.11-family cleanup; pure perf, no semantics.

### S8 MED — Health-file leak for wedged runs

Retention prunes only `staging/`, `published/`, `manifests/`
(`scheduler.py:2576-2578`); nothing prunes `status/ensemble_stats/`. The only
unlink is a complete pass observing zero incomplete units
(`ensemble_stats_health.py:113-118`) — but once `published/` ages out,
`_maybe_run_stats_pass` early-returns (`scheduler.py:2776`) and the file is
orphaned. Exactly the alerting-worthy (wedged) runs leak one file per run,
unbounded. Small fix: prune `status/ensemble_stats/{model}` against kept runs
during retention.

### S9 LOW

- **Error over-count:** a mid-loop failure records `STATUS_ERROR` for the
  whole `missing` list including products already counted `WRITTEN`
  (`stats.py:400-405`); on-disk state self-heals, counts are inconsistent.
- **Pending-scan abort scope:** `build_stats_plan`'s registration `ValueError`
  (`stats.py:136-139`) escapes via `stats_pass_pending` to the scheduler
  catch-all, suppressing the whole model's stats (loud-by-design for a config
  bug; the compute path wraps its own call).
- **LATEST flips before stats exist:** pointer updates at mean-publish
  (`scheduler.py:2023-2024`); stats vars join the manifest only after stats
  promote. Manifest-gated → viewer degrades gracefully; direct stats-frame
  requests on `latest` 404 transiently. For the frames-404 telemetry:
  these classify as `not_published` (benign), not `swap_gap` — expected.
- **Negative thresholds:** blocked by the id grammar (`base.py:514-529`), not
  the math (verified: math handles negatives fine). P(< -10 °F) is
  inexpressible until the token grammar carries a sign.
- **No per-member transform-equality assertion:** same-shape different-
  transform members would stack silently misregistered (`stats.py:325-327`);
  all members warp to one target grid in practice.
- **Minor float64 temporaries:** `100.0 * count` promotes per-threshold
  (~7 MB transient, `stats_math.py:84,110`).

## Verified sound

- **Math parity:** `sorted_nanpercentile` matches `np.nanpercentile`
  (linear method) to 3.8e-6 across NaN fringes, all-NaN and single-valid
  pixels, percentiles 0/100; percentile ordering holds before and after u16
  quantization (single shared sort; monotone rint).
- **Sentinel round-trip:** decode maps 65535→NaN; encode maps non-finite→65535
  and clips finite to [0, 65534] — NaN never becomes 0; a real 0.0% stays
  distinct from missing (`grid.py:1662-1673, 1714-1716`).
- **Denominators:** valid (non-NaN) members only; NaN members never count
  toward numerators; NaN output where no member valid.
- **No double-scaling:** prob products are percent end-to-end with
  `scale=0.1` packing and explicit generated registry entries.
- **Scale/offset consistency:** packing config comes from the static registry
  keyed by the shared var (never per-member meta) — cross-member scale
  mismatch cannot happen by construction.
- **Partial roster never publishes:** hard frame-level completeness gate →
  `skipped_incomplete`, nothing written (`stats.py:303-315`).
- **Per-file atomicity everywhere** (bin, meta, sidecar, health JSON: each
  tmp+rename); **promote** is copytree + atomic double-rename with rollback;
  the published run dir never disappears mid-swap.
- **Preemption only at unit boundaries** (`stats.py:455`) — no partial product
  families for an fh from preemption (crash windows: see S1).
- **No retention/stats race:** one process per model (flock,
  `scheduler.py:1394-1427`), single-threaded sequential
  retention-then-members-then-stats; stats reads `published/` only; backfill
  iterates existing dirs only; aged-out pending runs return not-pending
  cleanly. Health updates fail open and reset correctly on recovery;
  preempted passes leave health untouched.
- **Stats pass is strictly serial** (peak = one unit) and holds no
  cross-unit array references; decode path is float32 end-to-end.

## Dispositions into the roadmap

- Wave 0 decision list: **S4** (equality semantics) and **S5** (min-valid
  mask) — both product calls, both visible-data changes if flipped.
- Wave 2 (member/artifact integrity): **S1** (sidecar in resume check),
  **S8** (health-file retention), and note S2's `<u2` trigger alongside the
  existing 4.10 item.
- Wave 3 (3.8 failure classification): extend to stats-unit error streaks
  (**S2** cap/backoff + health visibility).
- Wave 4 (3.6 instrumentation): per-pass RSS deltas (**S6**).
- Wave 6: **S7** memory trims, **S3** documentation, S9 minor items.
