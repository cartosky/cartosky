# Fast-Path Ingestion Integration Design (Open-Meteo `.om` source)

**Status:** v2 APPROVED — 2026-08-05, all six §9 decisions resolved by Brian; cleared for Phase 6 implementation. v1 was adversarially reviewed by Codex
(verdict: DISAGREE, five findings); this revision accepts all five and
restructures accordingly. The material changes: **one scheduler process per
model** (the fast source multiplexes into the existing loop rather than a
second systemd unit — the per-model `flock` at `scheduler.py:1399` makes two
loops impossible and unsafe to bypass); **ownership is keyed
(variable, domain)** not variable; **the source provider returns
run-cumulative values for primary accumulation vars** (ECMWF `precip_total`
and `snowfall_total` are `primary=True` GRIB-cumulative fetches, not derives);
**failover is an in-process state machine with per-frame provenance**.
Follows the validated prototype
(`docs/ECMWF_OPENMETEO_9KM_PROTOTYPE_2026-08-04.md`, Phases 1–5 all passed,
visual gate approved). This document settles the architecture for Phase 6 and
production integration. Open decisions are marked **[DECISION]** and collected
in §9 — nothing below assumes their outcome unless stated.

## 1. Goal and non-goals

Serve ECMWF surface products from the Open-Meteo S3 relay at native 9 km
(NA) roughly 2 h earlier than the delayed 0.25° path, per-frame during
dissemination, without destabilizing the existing pipeline.

Non-goals (unchanged from the prototype): no other bucket models wired this
season (the *architecture* is model-agnostic; the *scope* is ECMWF), no
Kuchera/upper-air changes (delayed path remains their source pending the fall
AIFS decision), no public exposure before launch gates (attribution +
resolution labels + ops metrics).

## 2. Core architectural decision: a second fetch source, one build pipeline

The prototype proved the fast path can produce arrays on the exact production
target grids (Phase 2) and that everything downstream of the array — packing,
sidecars, manifests, promotion — works untouched (Phase 5 used the real
writers end-to-end). Therefore:

**The fast path is a new *fetch source*, not a new pipeline.** Everything
from "array on target grid" onward is shared. Concretely, a new module family:

```
backend/app/services/sources/openmeteo/
    reader.py      # GPLv2-isolated omfiles wrapper (block-cached HTTP FS)
    grids.py       # per-product grid decode: O1280 reduced-Gaussian sampler,
                   #   regular 0.25 sampler; orientation detection; sampler cache
    catalog.py     # per-model config: bucket dir, variable map (name, units,
                   #   scale), cadence ladder per cycle, horizon per cycle
    source.py      # poll/list/fetch API the scheduler + builder call
```

`catalog.py` is where model-agnosticism lives: adding ICON later means adding
a catalog entry (bucket dir, var map, grid decode kind, cadence), not code.

### GPLv2 isolation

`omfiles` stays imported only inside `reader.py`. Server-side use is not
distribution, so in-process import is legally fine (not legal advice); the
module boundary is what keeps it swappable if we ever want a clean-room `.om`
reader. **[DECISION 6]** if Brian prefers hard isolation (separate worker
process + IPC), that is a contained change to `reader.py` at the cost of
operational complexity. Recommendation: in-process import behind the module
boundary; revisit only if licensing posture changes.

## 3. Variable source ownership (the reconciliation model)

The single most load-bearing choice. Prod already swaps during ECMWF builds,
so the fast path must **replace** the delayed build for the variables it
owns — never duplicate it.

Ownership is keyed **(variable, domain)** — not variable alone. The
scheduler expands every selected variable across all declared build domains
(ECMWF declares both `na` and `global` for most vars), so variable-only
ownership could not express "NA fast, global delayed" [Codex finding 2]:

```
source_by_var_domain:            # per resolved Decision 1: surface set is
    (tmp2m, na): fast        (tmp2m, global): fast     # fast in BOTH domains
    (dp2m, na): fast         (dp2m, global): fast
    (precip_total, na): fast (precip_total, global): fast
    ... surface set ...      # (na, global) can still diverge per var — the
                             #  key exists so failover/rollback can flip one
                             #  domain independently
    (tmp850, *): delayed     # all pressure-level vars
    (snowfall_kuchera_total, *): delayed
    (ptype_intensity, *): delayed   # uses tmp925/tmp850 signals
```

A contract test must prove a var can be fast-owned in `na` while
delayed-owned in `global` (independent readiness, manifests, canary).

Rules:

1. **One run identity.** Both sources build into the same `20260804_12z` run
   root. The run is discovered by whichever source sees it first (in practice
   the fast poller, ~2 h earlier).
2. **A (variable, domain) is built exactly once, by its owner.** The delayed
   source skips fast-owned pairs entirely (not "skip if present" — skip by
   config, so a fast-path outage doesn't trigger surprise double memory load
   at the delayed build window; failover is explicit, §6).
3. **Mixed-derive vars are owned by the source of their coarsest input** and
   build at that source's availability time. `ptype_intensity` and Kuchera
   stay delayed-owned. (A future hybrid — fast `sf` + delayed profile — is a
   per-component sourcing question inside the derive, out of scope here.)
4. **The manifest is per-var incremental already** (`available_frames`,
   `ready_through_fh` per variable, scheduler.py:2045) — a run whose surface
   vars are ready at T+5.6h while Kuchera arrives at T+7.6h requires **no
   frontend contract change**. The viewer already renders per-var readiness.

Consequence for UX: for ~2 h per cycle, some variables in the picker are
ready and some are still "Building" — same as today's catch-up window, just
with a source split. The per-variable resolution label (§8) doubles as the
explanation.

## 4. Scheduler integration

**One scheduler process per model — the fast source multiplexes into the
existing per-model loop.** [Codex finding 1] `run_scheduler` holds a
nonblocking per-model `flock` for its whole lifetime (`scheduler.py:1399`),
and publication/promotion/retention are serialized only by process-local
state (`scheduler.py:1332`, shared `.tmp`/`.trash` paths) — a second unit on
the same run root would either refuse to start or corrupt promotion. So the
existing `csky-ecmwf-scheduler` unit gains a fast-source sub-loop: an async
task (or interleaved poll step) inside the same process, sharing the model
lock, publish lock, and retention machinery. Single lifecycle writer by
construction; no cross-process transaction protocol needed.

- **Poll:** `in-progress.json` as a hint on a short interval inside the
  expected dissemination window (cycle + ~5h20m to cycle + ~6h30m, per the
  monitor's measured distribution), slow interval otherwise. Object existence
  (LIST/HEAD) is truth; manifest flags are never trusted for liveness.
- **Build:** for each new timestep object, one block-cached full-globe pass
  for all fast-owned vars (~38 MB/file; the NA band alone would be ~17 MB,
  but per resolved Decision 1 the same read feeds both the NA 9 km and
  global 0.25° targets), regrid via the two cached samplers (0.06 s/field
  each), convert units per catalog, run the normal derive/write path,
  publish both domains' frames, update the manifest. Sequential dissemination means frames build in fh order
  naturally.
- **Complete:** when all expected objects for the cycle's horizon are built
  (per-cycle: 145 files for 00z/12z, 109 for 06z/18z), mark fast-owned vars
  complete; reconcile counts against `meta.json` for bookkeeping only.
- **Resources:** the fast build is GRIB-free (no GDAL, no Herbie): numpy
  gather + the existing packer. Expected steady-state well under 1 GiB RSS;
  set GDAL_CACHEMAX-equivalent caps anyway and `Nice=10` like siblings. It
  must run on the prod box (artifacts are local-disk); dev first (Phase 6).

### Accumulation provider (per-source step ladder)

`.om` accumulation fields are per-step with cadence-dependent length
(hourly→FH90, 3 h→144, 6 h→360 for 00z/12z). Critically, ECMWF
`precip_total` and `snowfall_total` are **primary, non-derived** variables
(`primary=True`, no `derive=` — `ecmwf.py:329`, `:451`): production feeds
them the GRIB's native *run-cumulative* value directly; the cumulative-derive
machinery is not on their path [Codex finding 4]. Design:

- **The source provider contract is: return the run-cumulative field at any
  published fh** for primary accumulation vars — computed by summing all
  `.om` steps from run start, maintained incrementally as files land
  (dissemination is sequential, so each new file adds one step to a running
  sum).
- **Running sums are checkpointed to disk** per (run, source, var, domain,
  cadence-version): a scheduler restart resumes from the checkpoint instead
  of refetching the whole step history. Checkpoints are keyed so a source
  switch or cadence-ladder change invalidates them rather than silently
  mixing lineages.
- FH90 and FH144 cadence transitions are just step-length changes in the
  running sum; both boundaries get explicit tests against summed hourly
  truth (the Phase 3 methodology, now as a regression test).
- For genuinely derived accumulation products on other models/paths, the
  same provider exposes the per-step series; distinct
  `cumulative_cache_version` strings keep fast/delayed derive caches
  separate.
- The hourly cadence below FH90 raises a product question: **[DECISION 2]**
  publish hourly frames (more frames than today's 3 h ECMWF cadence — better
  product, ~3× more artifacts below FH90) or aggregate to the current 3 h
  ladder (zero storage/UX change). Recommendation: 3 h at launch to keep the
  diff-vs-delayed canary apples-to-apples; hourly as a fast follow.

## 5. Run lifecycle and retention

- Run discovery, retention (`DEFAULT_KEEP_RUNS`), and pruning stay owned by
  the existing scheduler machinery; the fast scheduler creates runs through
  the same helpers so retention sees one namespace.
- Staging→promote flow is unchanged (Phase 5 exercised it); the fast path
  promotes per-frame exactly as the catch-up loop does today.
- If the delayed path later re-probes the same run: it builds only
  delayed-owned vars into the existing run root. No artifact overwrites
  by construction (rule 2 above).

## 6. Failure modes and failover

Because both sources now live in one process (§4), failover is an
**in-process state machine, not a cross-process race** [Codex finding 3]:
each (run, var, domain) carries a source-generation token; the failover
transition atomically revokes fast ownership (in the same publish-lock
critical section the catch-up loop already uses) before any delayed build of
that pair starts, and a fast source that resumes after revocation finds its
generation stale and stops. Crash-restart replays ownership state from the
per-frame provenance records (§8), which carry the generation. Interleavings
to test explicitly: stall→failover, resume-during-failover, crash mid-frame,
retry-after-partial-write.

| Failure | Behavior |
|---|---|
| Bucket stalls mid-run / objects never appear | Fast-owned pairs simply stop advancing. At the delayed source's availability (T+~7.6h), a **failover deadline** fires: revoke-then-build as above for any pair whose `ready_through_fh` trails the delayed source's horizon. Users see frames arrive late (as today); ops get `fastpath_stalled` alert. Cumulative vars rebuild from the delayed source's own GRIB values for **all** frames beyond the checkpointed seam — never by mixing sources within one accumulation series. |
| Bucket serves corrupt/truncated object | Per-file integrity: decoded array shape + NaN/land-fraction sanity + (for temps) physical-range check before write; failures skip the frame, log, and count toward a stall metric. Never publish a failed decode. |
| Upstream layout/orientation change | Orientation is detected per run (±45° variance check) with the tropics-vs-arctic self-check on output (monitor already does this); a mismatch aborts the run's fast build loudly → failover path covers users. |
| Units drift upstream | Catalog pins expected units per var; a per-run spot-check against the delayed source (see canary) catches drift within one cycle. |
| Fast and delayed disagree (data bug) | The **canary** (below) alerts; kill switch = flip var ownership back to `delayed` in config (no deploy). |

**Automated canary:** once per run, after both sources are complete, recompute
the Phase 4 reconcile metrics (bias, synoptic-scale MAE, corr) for 2–3
fast-owned vars against a delayed-source rebuild of one frame. Thresholds from
the measured Phase 4 baselines (bias ≈ 0, synoptic MAE ≤ 0.2 °C-equivalent).
This is cheap (one frame) and converts the prototype's one-day evidence into
a continuous guarantee. Alert, don't auto-disable, at launch.

## 7. Rollout plan

1. **Phase 6 (dev):** fast source dev-flagged (`CARTOSKY_FASTPATH_MODELS=ecmwf`
   empty-default env allowlist, mirroring the binary-sampling allowlist
   pattern) inside the existing scheduler process, building into the dev
   data root. Observe ≥3 live cycles: frames
   appear per-frame in the dev viewer; horizon per-cycle correct; simulated
   stall (block the bucket host) exercises failover.
2. **Dark on prod:** fast scheduler builds into prod under a run-root suffix
   or staging-only mode for ≥1 week alongside the canary; zero user exposure;
   watch RSS/swap on the box.
3. **Flip:** ownership config switches the surface set to `fast` for real
   runs. The delayed ECMWF scheduler config drops those vars the same deploy.
   Rollback = revert the two config lines.
4. **Post-flip:** monitor catch-up behavior, then the follow-ups (hourly
   frames below FH90, global decision, additional variables).

## 8. Launch gates (product/legal, unchanged from prototype doc)

- Attribution: `ECMWF IFS data © ECMWF, via Open-Meteo (CC-BY-4.0)` in the
  attribution dialog + export footers. Required before any public frame.
- **Per-frame provenance** [Codex finding 5]: frame meta/sidecar gains
  `source_id`, `source_resolution`, upstream object key + ETag,
  adapter/cadence version, and the failover generation. The run manifest
  summarizes homogeneous fh ranges per (var, domain). This is what makes a
  post-failover run auditable (where is the seam?) and drives the UI label
  from recorded truth rather than static config.
- Per-variable source-resolution label in the info card ("9 km native" vs
  "0.25° source"), driven by the provenance above — honest labeling for
  mixed-resolution products, including the failover case where one variable's
  early frames are 9 km and later frames are 0.25°. The canary explicitly
  checks value continuity across any recorded failover seam.
- Ops metrics: ingestion lag vs dissemination per cycle, per-run object
  counts, canary results, stall alerts — join the existing :9105 exporter.

## 9. Decisions — RESOLVED (Brian, 2026-08-05)

| # | Decision | Resolution |
|---|---|---|
| 1 | Global fast path | **At flip** — both NA 9 km and global 0.25° build from the fast source at launch. One full-globe read per file serves both targets (~19 GB/day). Ownership table: the surface set is `fast` for **both** domains. |
| 2 | Sub-FH90 cadence | **Match today's 3 h ladder.** Hourly steps are summed into 3 h frames below FH90; hourly frames revisit later. |
| 3 | Interim snow product | **Two-tier:** 10:1 `snowfall_total` ships early on the fast path; Kuchera follows ~2 h later from the delayed path. Labeling per §8. Revisit at the fall Kuchera decision. |
| 4 | Launch variable set | **The validated 10** winter vars; batch-add later with per-var units audits. |
| 5 | Dark-prod duration | **1–2 hours, if needed** — i.e., observe one live cycle dark with the canary green, then flip. Consequence: the automated canary + failover deadline are the primary safety net rather than soak time; rollback stays two config lines. |
| 6 | GPLv2 posture | **In-process** `omfiles` behind the `reader.py` module boundary — lowest resource cost (no IPC, no extra process), no build-time impact. |

Original decision framing preserved below for context.

### (superseded) Decisions required

1. **Global fast path now or later?** One full-globe read serves both NA and
   global targets (the NA band is 44% of the bytes), so adding global
   downsample costs ~19 GB/day total vs ~8.5 GB/day NA-only, plus global
   artifact builds ~2 h earlier. Recommendation: **NA-only at flip, global as
   a fast follow** — it halves the moving parts during the risky window and
   the global product is unchanged meanwhile. Decide before Phase 6 config
   is written (it's one catalog flag either way).
2. **Sub-FH90 cadence:** hourly frames (better product, ~3× artifacts <FH90,
   diverges from delayed-path cadence) vs matching today's 3 h ladder.
   Recommendation: **3 h at launch**, hourly later.
3. **Interim snow product:** when winter arrives and Kuchera still lags by
   ~2 h, do we ship 10:1 `snowfall_total` early on the fast path (two-tier
   snow: quick 10:1 now, refined Kuchera later) or hold all snow products to
   the delayed timeline? Product/labeling call; no architectural impact.
   Can be deferred until the fall Kuchera decision.
4. **Variable launch set:** the validated 10 winter vars only, or the full
   ECMWF surface subset (~20 of the 43 bucket vars map to existing CartoSky
   vars)? Each added var needs a units-audit line item (gotcha: per-product
   units differ). Recommendation: **the 10, then batch-add**.
5. **Dark-prod duration:** 1 week recommended minimum between dev validation
   and the flip. Shorten only if the winter clock forces it (it doesn't —
   it's August).
6. **GPLv2 posture:** in-process `omfiles` behind the module boundary
   (recommended) vs separate worker process.

## 10. Explicitly deferred

- ICON/GEM/UKMO wiring (catalog entries when wanted; nothing in this design
  assumes ECMWF-only except the ownership table).
- Kuchera hybrid sourcing (fall decision, own design note when the archive
  has warm-nose cases).
- Lon-subsetting the NA band read (2.4× bandwidth cut available; not needed
  at 8.5 GB/day).
- Clean-room `.om` reader (only if GPLv2 posture changes).
