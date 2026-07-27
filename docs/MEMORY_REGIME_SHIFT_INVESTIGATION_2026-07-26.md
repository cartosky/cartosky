# Memory / swap regime shift investigation — 2026-07-17 event

Investigated 2026-07-27. Read-only; no fixes implemented, no code or config
changed. Triggered by a Grafana regime change in host Swap Usage: swap
troughs stopped returning to ~0-2 GiB and began plateauing above ~4 GiB with
peaks riding near the 8 GiB ceiling, with a matching step in CPU volatility
and load average.

Method: (1) local git archaeology over all 68 commits merged to `main`
between 2026-07-08 and 2026-07-20, reading the full backend diff of every
commit touching caching, cross-request in-memory structures, aggregation,
retention, or fetch lifecycle; (2) deploy-time correlation via the prod
deploy checkout's `git reflog`, which records every `git pull` with a
timestamp; (3) plateau-vs-leak determination from a live per-process
cross-section on prod.

Numeric host claims are from prod point samples taken 2026-07-27 ~13:05 CDT;
the per-model memory bands are operator-read from the Grafana Per-Process
Memory panel over an 18-day window.

---

## TLDR

`2036b104` (deployed **2026-07-17 10:17:47 CDT**) moved every Herbie
construction and inventory read onto a freshly-spawned `threading.Thread`,
raising steady-state per-process RSS by a roughly uniform ~1.5-2x across all
schedulers regardless of model. It is a **plateau, not a leak** — three
independent lines of evidence, strongest being that the four schedulers which
never restart have run 5-7 days on the affected code and show the *lowest*
RSS on the box.

**The 64 GB upgrade is correctly sized and should proceed.** It is sized
against a stable ceiling, not a moving target. The code-level regression is a
separate efficiency follow-up, not a prerequisite.

---

## Summary

| # | Sev | Area | Finding | Disposition |
|---|-----|------|---------|-------------|
| M1 | HIGH | regression | `2036b104` put a thread spawn on the hot path of every Herbie call for every model; ~1.5-2x uniform RSS step at its 07/17 10:17 deploy | follow-up, scoped separately |
| M2 | — | determination | Plateau, not leak: 2 timeouts/10d, flat thread counts across 7d uptimes, and never-restarting schedulers hold the lowest RSS | closes the sizing question |
| M3 | MED | mitigation | `MALLOC_ARENA_MAX` confirmed absent from the live process env; the documented canary's trigger condition is now live | promote the already-scoped canary |
| M4 | MED | observability | `herbie_call_timeout` and all fetch runtime counters are reachable only from `get_herbie_runtime_metrics_for_tests()`; prod has no metric, only a log line | small fix |
| M5 | LOW | process | A 526-line `fetch.py` change with a thread-lifecycle rewrite shipped under a frontend-sounding commit title, against this repo's own stated deploy discipline | process note |
| M6 | — | open | The 07/14 swap step is neither confirmed nor eliminated; `53748c0a` remains a live candidate for an earlier, smaller step | needs a narrower Grafana window |
| M7 | LOW | fragmentation | `nam` holds 612 anonymous mappings at 216 MB RSS — 2-3x the mapping count of peers at comparable RSS | observe; folds into M3 |

---

## Findings

### M1 HIGH — Thread-per-Herbie-call on every model's hot path

`2036b104` "feat(loader): implement hex signal ring and non-blocking top
progress bar" added **+526 lines to `backend/app/services/builder/fetch.py`**.
The memory-relevant part is `_run_herbie_call_with_deadline`
(`fetch.py:800-849`), which runs its callback in a newly-spawned
`threading.Thread` and waits on an `Event` with a 90 s deadline
(`DEFAULT_HERBIE_INTERNAL_CALL_DEADLINE_SECONDS`, `fetch.py:169`).

The commit rewired six call sites onto it, replacing direct
`H = Herbie(herbie_date, **run_kwargs)` constructions:

- `fetch.py:2715`, `:3161`, `:3392`, `:3495`, `:4755` — via `_construct_herbie` (`fetch.py:852`)
- `fetch.py:2036` — the `H.index_as_dataframe` inventory read
- `fetch.py:902` — `_download_herbie_subset_isolated`, which additionally
  `copy.copy(H)`-clones the Herbie object per attempt (`fetch.py:877`)

All six are model-agnostic. GFS, HRRR, ECMWF, NAM, NBM, AIFS, AIGFS and the
ensembles all traverse them, which is why the step is uniform across models
with no shared workload.

**Mechanism.** The schedulers went from single-threaded Herbie access to
high-frequency thread churn on the fetch path. glibc allocates a fresh
per-thread malloc arena on first allocation from a new thread, caps the count
at 8 x ncores, and **never returns arena memory to the OS** — freed chunks
stay in the arena. The result is a fixed multiplier on RSS, uniform across
processes, bounded above by the arena cap. That matches the observed data
shape better than any per-model workload story.

This mechanism was already on file as an unresolved swap suspect
(`BUILD_PIPELINE_AUDIT_2026-07-07.md:160`, listing "glibc arena growth" among
the narrowed candidates). `2036b104` converted that latent risk into a live
one.

**Observed effect** (Grafana Per-Process Memory, operator-read):

| Model | Pre-07/17 band | Post-07/17 band |
|---|---|---|
| HRRR | ~1.5 GB | 2.5-3 GB |
| GFS | ~2 GB, spikes 3-4 GB | ~3 GB, spikes 5-6 GB |
| ECMWF | 1-1.5 GB | 2-3 GB |

**Onset timing is explained exactly.** `RESTART_ON_SUCCESS_MODELS =
{"gfs", "hrrr", "eps", "gefs", "ecmwf"}` (`scheduler.py:183`, gate at
`:1809`) — these self-exit after a successful run and systemd restarts them,
so a pull goes live within one cycle without operator action. GFS and ECMWF
cycle frequently and stepped on 07/17; HRRR stepped on 07/18.

### M2 — Determination: plateau, not leak

Three independent lines, all concordant.

**1. The abandoned-thread mechanism is refuted.** On deadline expiry the
caller raises `HerbieCallTimeoutError` (`fetch.py:841`) but the daemon worker
keeps running, retaining `state`, the Herbie object, and any in-flight
payload — an unbounded-retention path with no cap on concurrent abandoned
threads. Measured on prod:

```
sudo journalctl -u 'csky-*' --since "2026-07-17" | grep -c "Herbie internal call deadline exceeded"
2
```

Two events in ten days. This path cannot account for GB-scale RSS.

**2. Thread counts are steady-state.** Every scheduler sits at 35 threads
baseline (transient excursions to 40-41), independent of uptime — identical
at 2,056 s and at 611,360 s. No thread accumulation.

**3. Decisive — the never-restarting schedulers are a built-in control
group.** `aifs`, `aigfs`, `nam`, `nbm` are absent from
`RESTART_ON_SUCCESS_MODELS`, so they have run continuously on post-07/17 code
for 5-7 days:

| Model | Uptime | RSS (sample 1 / 2) | anon maps |
|---|---|---|---|
| aifs | 611,360 s (7.1 d) | 686 / 684 MB | 299 |
| nam | 611,360 s (7.1 d) | 383 / 216 MB | 612 |
| nbm | 611,360 s (7.1 d) | 347 / 209 MB | 324 |
| aigfs | 449,873 s (5.2 d) | 678 / 843 MB | 322 |

A genuine unbounded leak in shared fetch-layer code would be *most* visible
in exactly the processes that never reset. Instead they hold the lowest RSS
on the box and are flat after a full week.

The elevated band is therefore **within-cycle peak RSS**, raised by a
constant factor. It does not accumulate. This is consistent with the arena
mechanism, which is inherently bounded (capped at 8 x ncores, fully reset on
process restart).

### M3 MED — `MALLOC_ARENA_MAX` absent; its canary is now indicated

Read from the live GFS scheduler's process environment:

```
GDAL_CACHEMAX=256
```

`GDAL_CACHEMAX` is present as intended (`40306f52` codified it into the unit
templates; confirmed live). `MALLOC_ARENA_MAX` is **not set** — arenas are
uncapped.

`MALLOC_ARENA_MAX=2` is already scoped in this repo as a deliberately
deferred, isolated, measured canary
(`BUILD_PIPELINE_AUDIT_2026-07-07.md:160` and `:565`;
`BUILD_PIPELINE_ROADMAP_2026-07-14.md:88`), explicitly held back from the
`40306f52` prune-policy deploy so that any RSS improvement would remain
attributable. Its trigger condition — high thread churn in the schedulers —
is now confirmed live rather than hypothetical, which raises its priority
without changing its scope.

The roadmap's stated protocol for that canary — one host, isolated,
before/after RSS, **no bundled memory changes in the same deploy**
(`BUILD_PIPELINE_ROADMAP_2026-07-14.md:88-89`) — should be preserved.

### M4 MED — Herbie runtime counters are unreachable in production

`_FETCH_RUNTIME_COUNTERS` / `_FETCH_RUNTIME_TIMERS_MS` (`fetch.py:443-445`)
accumulate `herbie_call_timeout`, `herbie_<op>_timeout`, `idx_cache_*`,
`eps_full_file_cache_*` and friends, but the only accessor is
`get_herbie_runtime_metrics_for_tests()` (`fetch.py:784`). Nothing exports
them — no Prometheus surface, no admin status field, no periodic log line.

The sole production signal for a deadline breach is the `logger.warning` at
`fetch.py:838`, which is why M2's timeout count had to be obtained by
grepping journald. Note the journal is also not a durable substitute: at 3.9
GB total it did not retain far enough back to answer the 07/15-07/20 restart
query at all (that query returned empty; deploy timing was recovered from the
deploy checkout's reflog instead).

### M5 LOW — Deploy hygiene

`2036b104`'s subject line describes a frontend loader change. The commit also
carries a 526-line `fetch.py` rewrite that changes thread lifecycle on every
model's fetch path, alongside `HexSignalRing.tsx`, `ViewerTopProgressBar.tsx`,
`globals.css` and five other frontend files.

This is the same class of bundling that `BUILD_PIPELINE_ROADMAP_2026-07-14.md:88-89`
explicitly guards against for memory-affecting changes. The practical cost
here was diagnostic: on first pass this commit was ranked *below* several
smaller, correctly-labelled commits precisely because its title carried no
signal, and it was initially scored as net memory-*reducing* on the strength
of its sibling commits.

Two same-day siblings compound this. `d552b55c` (deployed 07/17 11:01)
introduced a `remote_payloads` dict holding every range payload concurrently;
`a36d8554` (deployed 07/17 13:21) replaced it with a bounded sliding-window
generator (`_iter_ordered_range_payloads`, `fetch.py:~980`). A peak-RSS
regression and its fix shipped 2h20m apart on the same day as M1, so their
Grafana signatures are not separable from it.

### M6 — Open: the 07/14 swap step is not eliminated

The investigation was opened against a swap regime change placed at
07/14-07/16. The per-process evidence lands unambiguously on **07/17**.

Either the 18-day dashboard view blurred the date, or there were two steps.
`53748c0a` (deployed 2026-07-14 11:12:19) inverted a global default —
binary-only sampling went from a two-model opt-in allowlist to the default
for every model and the API in one deploy (`config/__init__.py:87-134`), and
flipped `check_pre_encode_value_sanity` from a `try/except` shadow gate to an
**enforced** gate running on the full warped array for every frame of every
model (`pipeline.py:1819-1836`).

That is a plausible mechanism for a smaller earlier step and is not ruled
out. Resolving it needs a Grafana window narrowed to 07/13-07/19 on the swap
panel; it does not affect the sizing verdict below.

### M7 LOW — Fragmentation signal on `nam`

`nam` holds **612** anonymous `rw-p` mappings at 216 MB RSS, against 324
(`nbm`) and 299 (`aifs`) at comparable RSS and identical uptime. High mapping
count at low resident size is the fragmentation signature, and `nam` is a
7-day-uptime process.

Stated as a signal, not a proof: the mapping count is a proxy that also
counts thread stacks and numpy allocations, and no clean arena-count
measurement was taken. It is consistent with M1/M3 and would be the natural
thing to re-measure either side of the M3 canary.

---

## Ranked Phase 1 suspects and rule-outs

Retained for the record, since several plausible-looking candidates were
eliminated by evidence rather than by omission.

**Eliminated by the per-process cross-section** (these cannot explain a
uniform step across HRRR/GFS/ECMWF, none of which have a member pipeline or
stats pass):

- `39d3c019` (07/13) — enables the full member pipeline for tmp850 on EPS (50
  members) and GEFS (30+control), `models/eps.py:780`, `models/gefs.py:946`.
  The largest workload expansion in the window and the initial top-ranked
  suspect; **refuted** — EPS/GEFS only.
- `8029b2cd` / `ca6c0c5f` / `3390c32c` (07/15) — cumulative cache key gained
  `:s=<strategy>:r=<rev>:h=<hints_hash>` (`derive.py:605-627`) plus revision
  bumps (`derive.py:498-504`). Applies only to models running cumulative
  derive strategies, and `_prune_kuchera_cumulative_cache` (`derive.py:221`)
  bounds retention to `keep_fhs={fh}` per frame.

**Eliminated by magnitude:**

- `7989d725` (07/14) — frames/grid 404 telemetry. Exact date match for the
  original window, but state is bounded to kilobytes: `_recent` is a
  `deque(maxlen=50)`, `_per_day` prunes to 14 days, `_cumulative` holds
  endpoint x reason integers.
- `35533433` / `dfba799f` (07/14, 07/16) — ensemble stats health tracking is
  entirely file-backed; loads read-and-discard, writes are atomic. No
  retained in-memory state.

**Eliminated as directionally reducing:**

- `188a5419` (07/13) — seek-based binary sampling reads one pixel per point
  instead of decoding a full frame (`sampling.py:519-570`).
- `a36d8554` (07/17) — bounded sliding window replaces an all-payloads-at-once
  dict.
- `b27ad6fb` (07/16) — 1 GiB cap on full-GRIB fallback.
- `40306f52` (07/12) — prune-allowlist inversion (`derive.py:234`),
  `working_dtype=np.float32` on member warps, `GDAL_CACHEMAX=256` codified.
  Net reducer; its one increase is `prob_non_exceedance` adding a third
  product-array family in `_process_stats_unit` (`stats.py:339-360`).

**Not in scope — frontend only** (browser memory, cannot affect server swap):
`ec6b729c`, `56f8cae6`, `3386a99b`, `db01b34a`, `cf959c01`, `cbce9912`,
`1372fc89`, `8fe3e60f`, `4a889c55`, `45ee61f6`, `2bb2f832`, `20a8d543`,
`1e44424f`, `c7be99a5`, `4d870c29`, `b28cdff9`, `9f0e8e4c`, `38edf2f6`.

Note `a1debbdd` "increase grid frame cache size" is a **red herring** — that
is `frontend/src/lib/grid-frame-cache.ts`, browser-side.

Also examined and carrying no new retained allocation: `e1a833b9`,
`636c3573`, `37fb767b`, `597755d1`, `216d6d6f`, `640efbc2`, `2af7f222`,
`b94f46bd`, `f5a45284`, `58ae2819`, `2c0a49b5`, `6f15c81c`, `e0aa0d1a`,
`596da153`, `82a42913`, `4a2cabb0`. `174bf848` (07/13) adds a full-grid
`.copy()` per decoded MRMS frame (`mrms_poller.py:1017`) — transient churn on
the highest-cadence unit, not a steady-state plateau. `676d0e88` (07/10) adds
a new always-on process (`csky-serving-canary.service`) that buffers full
grid responses via `_ = resp.content` (`serving_canary.py:180`) every 300 s —
small, bounded, but net-new RSS.

---

## Deploy timeline

Recovered from the prod deploy checkout's reflog. Restart-log inference was
not usable — journald did not retain to 07/15.

| Deployed (CDT) | Commit | Relevance |
|---|---|---|
| 2026-07-13 15:59:31 | `39d3c019` | tmp850 members (EPS/GEFS) — ruled out |
| 2026-07-14 11:12:19 | `53748c0a` | global binary-only flip — M6, open |
| 2026-07-14 15:30:41 | `7989d725` | 404 telemetry — ruled out |
| 2026-07-15 15:46:06 | `ca6c0c5f` | cumulative revisions — ruled out |
| 2026-07-16 14:43:28 | `b27ad6fb` | fallback cap — reducer |
| **2026-07-17 10:17:47** | **`2036b104`** | **M1 — root cause** |
| 2026-07-17 11:01:55 | `d552b55c` | all-payloads dict (regression) |
| 2026-07-17 13:21:02 | `a36d8554` | sliding-window fix |
| 2026-07-17 15:56:45 | `3386a99b` | frontend only |
| *(no deploys)* | | **07/17 15:56 → 07/20 09:01 code freeze** |
| 2026-07-20 09:01:55 | `56f8cae6` | frontend only |

The 2.5-day freeze immediately after the M1 deploy is a clean natural
experiment: the step occurred and persisted with zero intervening code
changes.

---

## Sizing verdict: 64 GB is correct — proceed

**Current host** (2026-07-27 13:05 CDT): 31 GB usable RAM, 7 GB usable swap.
14 GB used, 11 GB buff/cache, 16 GB available; **swap 5 of 7 GB used (71%)**.

**Peak envelope estimate.** Synoptic-cycle overlap at 00z/12z puts GFS
(5-6 GB), ECMWF (2-3 GB), GEFS and EPS (~3 GB each at member-pass peak, per
`STATS_AUDIT_2026-07-14.md:29`) in flight together, with HRRR (2.5-3 GB)
effectively always active on its hourly cadence. That is ~18 GB, plus AIFS
and AIGFS (~1.7 GB combined observed), the eight remaining light schedulers
(~4 GB combined), and the API plus serving canary (~2 GB): **~25-26 GB
anonymous at concurrent peak.**

Against 31 GB, with the grid-serving path wanting 10+ GB of page cache, the
box is over-subscribed at peak — which is precisely the observed behavior:
14 + 11 + 5 swapped ≈ 30 GB. The kernel is evicting anonymous pages because
there is no headroom, not because anything is growing without bound.

**At 64 GB:** ~26 GB peak plus 11-15 GB page cache is ~40 GB, leaving ~24 GB
headroom. Adequate with real margin, roughly 2.5x the measured peak envelope.

**The upgrade is correctly sized because M2 holds.** The finding is a
plateau, so 64 GB is sized against a stable ceiling. Had this been an
unbounded leak, the upgrade would have bought time only and the code fix
would have been a prerequisite. It is not.

The M1 regression is nonetheless a permanent ~1.5-2x tax that was never
reviewed as a memory change. Recovering it would drop the envelope to roughly
15-18 GB. That makes it an efficiency follow-up worth doing on its own
merits — it would turn 64 GB from adequate into generous — but it is **not a
blocker for the upgrade**, and the upgrade should not wait on it.

---

## Recommended sequence

Follow-ups only; scoping and implementation belong in their own phased work
per existing convention. No fix is designed here.

1. **Proceed with the 64 GB upgrade.** Independent of everything below.
2. **M3 — promote the `MALLOC_ARENA_MAX=2` canary** from deferred to next.
   Its trigger condition is now confirmed live. Run it under the protocol
   already written down: one host, isolated, before/after RSS, no bundled
   memory changes in the same deploy. Cheapest possible probe of the M1
   mechanism, and re-measuring M7's mapping counts either side of it is a
   free confirmation.
3. **M4 — export the fetch runtime counters.** Small, and a prerequisite for
   measuring anything in this area properly; this investigation had to grep
   journald for a number that is already being counted in-process.
4. **M1 — scope the thread-lifecycle regression separately.** The deadline
   wrapper solves a real problem (uncancellable Herbie calls pinning a build
   slot) and should not simply be reverted. Needs its own design pass with a
   measured before/after, sequenced after step 2 so the canary result informs
   whether the arena mechanism is in fact the dominant term.
5. **M6 — narrow the Grafana swap window to 07/13-07/19** and determine
   whether a distinct 07/14 step exists. Cheap; resolves whether `53748c0a`
   needs its own entry.

M5 is a process note with no action item beyond visibility.
