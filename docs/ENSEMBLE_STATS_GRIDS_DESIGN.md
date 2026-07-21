# Ensemble Stats Grids — Phase 6 Design (Tier 2)

> Status: APPROVED 2026-07-08; IMPLEMENTED and **ROLLED OUT — 6A/6B/6C all
> gates PASSED 2026-07-08** (see §9 table). Phase 6 closed.
> Scope decisions D-A…D-E ratified 2026-07-08; recorded in §2.
> Amended 2026-07-08 after external (GPT) review: staged enable rollout
> (§9), numeric first-run acceptance budget (§10.7), published-members
> input invariant (§5), `method="linear"` parity pin (§4), display-label +
> availability behavior (§7), tmp2m product-approval rephrase + `prob_lt`
> naming reservation (§3). Its state-file suggestion was REJECTED in favor
> of presence-derived state + a structured summary log (§5 rationale).
> Parent plan: `ENSEMBLE_MEMBER_PIPELINE_PLAN.md` (§3.3 architecture, §4
> naming/thresholds/layout — all LOCKED there and not re-litigated here).
> Sibling: `ENSEMBLE_MEMBER_SCHEDULER_DESIGN.md` (the member pass this
> builds on; its R-numbers are referenced below).

## 1. What this ships

Percentile (`{var}__p{NN}`) and probability-of-exceedance
(`{var}__prob_gt_{threshold}`) **map products**, computed from the published
member grid binaries in a second pass and published as ordinary full-profile
variables (colorize, tiles, sidecars, grid binary — the client never touches
a member raster). Initial matrix per D-A:

| Model | Variable | Percentiles | Prob thresholds (in) |
|-------|----------|-------------|----------------------|
| gefs | precip_total | p10/p25/p50/p75/p90 | 0.10, 0.25, 0.50, 1.00, 1.50, 2.00 |
| gefs | snowfall_total | p10/p25/p50/p75/p90 | 1, 3, 6, 12 |
| eps | precip_total | p10/p25/p50/p75/p90 | 0.10, 0.25, 0.50, 1.00, 1.50, 2.00 |

tmp2m deferred (D-A); EPS snowfall n/a (no snowfall member var); MSLP member
lows OUT (D-E — double-gated on its own data-source spike, "Phase 6b").

## 2. Ratified decisions

| # | Decision | Call (2026-07-08) |
|---|----------|-------------------|
| D-A | Initial product matrix | precip + snow only; tmp2m deferred; **additions must be one-descriptor cheap** (see §3) |
| D-B | Where the pass runs | Third **in-scheduler** pass (member-pass pattern), not a separate service |
| D-C | Percentile engine | Sort-based pure-numpy nan-aware percentile, parity-pinned to `np.nanpercentile`; no new deps |
| D-D | Viewer exposure | **Option B out of the gate**: product sub-selector on the parent variable, not flat picker entries |
| D-E | MSLP member lows | Out of Phase 6 |
| — | Meteogram feed | Design so stats can feed meteogram percentile bands with frontend-only work (§8) |

## 3. Registration: `ensemble.stats` descriptor (the easy-additions seam)

Mirroring the members descriptor (design R7/D1), stats products are declared
as metadata on the **base variable's capability** — never as hand-written
catalog entries per product:

```python
"precip_total": VariableCapability(
    ...,
    ensemble={
        ...,
        "members": {...},                       # existing (Phase 3/4)
        "stats": {                               # NEW (Phase 6)
            "percentiles": [10, 25, 50, 75, 90],
            "prob_thresholds": [0.10, 0.25, 0.50, 1.00, 1.50, 2.00],
            "enabled": True,
        },
    },
)
```

`ensemble_stats_descriptors(plugin)` (base.py, twin of
`ensemble_member_descriptors`) enumerates them; a shared
`stats_var_ids(base_var, descriptor)` helper derives the runtime ids
(`precip_total__p50`, `precip_total__prob_gt_0p50`, …) using the LOCKED §4.1
naming, including the threshold→`0p50` formatting, written ONCE and reused by
the pass, packing resolution, capabilities serialization, manifest tooling,
and the canary scope classifier.

**Adding tmp2m later is mechanically one descriptor on gefs/eps tmp2m** —
adding a threshold is one list entry; no schema, pass, or frontend changes.
**But product approval is still required per variable** (external review
point, adopted 2026-07-08): temperature ensemble products likely want
threshold/risk semantics (P(< 32°F), P(> 100°F)) rather than generic
percentile maps — and "below" thresholds need a `__prob_lt_{threshold}`
suffix that §4.1 does not yet define. The shared id helper RESERVES
`prob_lt` in its parse grammar now (rejecting it as unimplemented) so the
naming space is claimed before any consumer invents an alternative.

> **B2 amendment (2026-07-10, ratified):** tmp2m shipped on BOTH models with
> `__prob_lt_` implemented end-to-end — descriptor key
> `prob_lt_thresholds: [0, 20, 32]` (°F) alongside
> `prob_thresholds: [50, 70, 90, 100]`; `prob_non_exceedance` engine twin
> (strict `<`; a member exactly AT a threshold counts toward neither
> direction); packing regex, serialization (`P(< 32°F)` labels, "F"/"C"
> units render as °F/°C), and colormap classification all accept
> `prob_(gt|lt)`. Thresholds must be >= 0 — the id token grammar carries no
> sign, and `ensemble_stats_product_ids` raises on negatives. Product order:
> percentiles, then lt ascending, then gt ascending. See
> `ENSEMBLE_BACKLOG.md` B2 design note for the meteogram chart layout.

## 4. Percentile engine (D-C)

Benchmarked 2026-07-08 on a GEFS-shaped stack (31 × 721 × 1049, NaN fringe +
2% scattered gaps): `np.nanpercentile` 17.1 s/fh (matches the spike's 13.7 s
— pixel-bound NaN fallback); sort-based **0.25 s/fh for all five percentiles
at once** (67×), max diff 2e-6 (float32 noise), identical NaN pattern.

Approach: one `np.sort(stack, axis=0)` (NaN sorts last) + per-pixel valid
counts + linear interpolation at fractional ranks — the single sort serves
every percentile, and the same valid-count array serves every probability
threshold (`100 * count(member > thr) / valid`, NaN where valid == 0,
~50 ms/threshold). Pure numpy. Pinned by a parity test against
**`np.nanpercentile(..., method="linear")`** — the method is named
explicitly in the test so a numpy default change or refactor cannot shift
output silently — on stacks with NaN fringes, scattered gaps, all-NaN
pixels, and single-valid-member pixels.

## 5. The stats pass (D-B) — `builder/stats.py`

Same skeleton as the member pass, one stage later:

- **Plan**: `build_stats_plan(plugin, model_id, run_id, region)` from the
  descriptors; fhs from `scheduled_fhs_for_var` (region/global-agnostic per
  §3.7; no constants).
- **Unit of work = (base_var, fh)**: decode the member frames for that fh
  from **published**, one sort → all percentile frames + all probability
  frames for that fh. Spike-measured stack cost: 53 MiB + ~0.1 s decode —
  memory is a non-issue even on the EPS box.
  **INVARIANT (named, per external review 2026-07-08): stats may only
  consume member frames after those frames have been PROMOTED and are
  manifest-visible in the published tree.** Members are promote-gated as
  complete sets, which is what makes published a safe input root; never
  "optimize" this to read staging members — a half-written staging set is
  exactly the partial-roster poison the completeness gate exists to block.
- **Completeness gate (LOCKED §3.3)**: before computing, verify the FULL
  member roster is present for that fh (`member_frame_is_complete` per
  member). Missing any → skip the unit, retry next pass. Never publish a
  stat from a partial member set. This composes with the D8 backfill: a
  mean-coverage-capped run has full rosters exactly at its covered fhs, so
  stats appear for those fhs and nothing else.
- **Write**: FULL-profile publish via the normal writer
  (`write_grid_frame_for_run_root`) into STAGING — these are map products;
  they inherit the base variable's display-prep/colormap behavior (percentiles)
  or the probability colormap (§6). Pre-encode sanity gate enforced.
- **Statuses / resume / preemption / promote**: identical semantics to the
  member pass (written/resumed/gate_failed/error/preempted; frame-complete
  resume checks; `should_stop` between units; manifest build for stats var
  ids + `_promote_run` via the same scheduler hook machinery).
  **Deliberately NO persistent completion marker/state file** (external
  review suggested one; rejected): pending/complete is derived from
  filesystem presence, the single source of truth that makes resume,
  crash windows, and backfill drift-proof — a state file reintroduces the
  dual-source-of-truth failure class the member pass designed out. The
  observability half of that ask IS adopted: the pass summary log line
  carries the full structured payload (per-status counts,
  skipped-incomplete units, wall seconds, RSS peak).
- **Scheduler hook**: `_maybe_run_member_pass` grows a stats stage — after
  the member pass completes (and on the idle/backfill paths), run
  `stats_pass_pending` → `run_stats_pass` for models on a new
  `CARTOSKY_STATS_PUBLISH_MODELS` allowlist (rollout: gefs first, then eps —
  the Phase 3/4 pattern). Member work always precedes stats work for a run
  by construction.

**Wall estimate (validated on first prod run — a gate item):** compute is
~0.5 s/unit; the cost is full-profile *publish* of ~1,280 frames/run for
GEFS (20 products × 64 fhs) and ~660 for EPS synoptic (11 × 60). At
0.5–1.5 s/frame publish that's roughly **15–35 min/run for GEFS, 8–18 min
for EPS**, decoupled and preemptible like the member pass. If the first
capped run shows this crowding the idle window, the §9 knobs (product set,
fh stride for probabilities) are the pressure valves — cutting publish
profile is NOT one (map products need the full profile).

## 6. Packing and colormaps

- **Percentiles**: resolve packing via the existing member suffix fallback
  extended to `__p{NN}` → the base var's `__mean` twin (LOCKED §3.4 —
  identical quantization to the field they summarize). Colormap: the base
  variable's (snow p50 renders like snow).
- **Probabilities**: a NEW explicit packing band — `scale=0.1, offset=0.0,
  nodata=65535, units="%"` (0.1% precision) — added as deliberate
  `_PACKING_BY_MODEL_VAR` entries generated from the descriptors (one per
  (model, prob var id), auditable in one block, listed in canary scope).
  **Never** via suffix fallback (LOCKED §3.4). Colormap: one new shared
  0–100% spec (perceptual single-hue ramp, `allow_dry_frame: true` — an
  all-zero probability field is a valid summer/July product, the same lesson
  as the dry snowfall member frames).

## 7. Viewer exposure (D-D, option B)

Capabilities: the base variable's ensemble block gains a serialized
`products` map derived from the descriptor —
`{"mean": "precip_total__mean", "p50": "precip_total__p50",
"prob_gt_1p0": "precip_total__prob_gt_1p0", ...}` with display labels/order.
The variable picker keeps ONE entry per base variable; selecting a variable
with >1 product renders a **product sub-selector** (pill row / select,
consistent with the Ensembles tab's Radix controls) that swaps the runtime
var id used for tiles/grid/sampling. URL: a `product=` param (whitelisted in
the forecast/viewer permalink rebuild — the deep-link lesson from Phase 5),
default `mean` (byte-identical URLs for existing links). `supported_views`
stays `["mean"]` (design D1 unchanged; products are not views).

**Display labels** ship in the serialized products map (derived from the
descriptor, human-formatted from display units): `P50`, `P(> 0.50")`,
`P(> 6")` for pills, with full-form tooltip/mobile labels
("Probability of snowfall > 6\"") so context survives small screens —
runtime ids like `prob_gt_0p50` never reach the UI.

**Availability behavior (rollout-critical — GEFS will have stats while EPS
does not):** the selector renders only products that are BOTH declared in
capabilities AND available for the selected run (manifest presence). Mean
is always available. A deep link to an unavailable product falls back to
mean with a small notice, preserving the `product=` param so the choice
re-applies when the product appears.

Frontend scope: capabilities plumbing, product selector component, permalink
param, availability wiring, hover-sample label (shows the product's units —
`%` for probabilities). No member data touches the client.

## 8. Meteogram feed (future, designed-for now)

Stats variables are ordinary runtime vars on the binary-sampling path:
packed frames + run-manifest entries (full-profile publish registers them)
+ existing runtime-var resolution. **The meteogram can therefore sample
`precip_total__p50` etc. today's-code-style with zero backend changes** —
a client that requests those var ids gets point series. Feeding plume-style
percentile bands (e.g. p10–p90 shading on the Ensembles tab) is
frontend-only work: request the stats ids alongside the mean, render bands.
Explicitly out of Phase 6 scope, deliberately cheap next.

## 9. Config / knobs / rollout

- `CARTOSKY_STATS_PUBLISH_MODELS` — allowlist, empty default (mirrors
  members).
- Descriptor lists are the product knobs (per D-A's easy-additions bar).
- Optional (only if the first-run wall demands it): per-descriptor
  `prob_fh_stride` to thin probability frames; not implemented up front.

**Rollout is an ENABLE sequence, not an implementation sequence** (external
review's phasing, adopted 2026-07-08 with that clarification —
implementation builds the full §1 matrix behind descriptor flags):

| Stage | Enable | Gate to advance |
|-------|--------|-----------------|
| 6A | gefs `precip_total` only (snowfall descriptor ships `enabled: False`; eps off the allowlist) | §10 first-run acceptance budget green — **PASSED 2026-07-08 on 20260708_18z: wall 100.8s (budget ≤20 min), 704/704 written, zero gate bypasses, manual tally exact at packing precision, maps spot-checked vs WeatherBell; the D8 backfill also stats-healed 20260707_18z unprompted** |
| 6B | flip gefs `snowfall_total` descriptor — **flipped 2026-07-08** | same checks incl. dry-frame behavior (July snow = all-zero products, the exact dry-frame path 6A could not exercise) |
| 6C | add eps to the allowlist (precip) | same checks on the EPS unit — **PASSED 2026-07-08: written=660 synoptic / 264 off-cycle (both exactly as predicted), complete=True, RSS well under MemoryHigh, tally within packing tolerance; six-run backfill drain confirmed one-run-per-tick with zero repeats** |
| 6D | threshold tuning / frontend polish | — |

GEFS precip alone exercises the whole architecture: descriptor enumeration,
id generation, both engines, packing, probability colormap, full-profile
publish, manifests, product selector, sampling labels, retention, CF.

## 10. Verification & gate

1. Percentile parity test vs `np.nanpercentile` (§4 cases).
2. **Manual member tally spot-check** (the plan's gate): at N test points
   (generated from the region bbox, not CONUS-copied — §3.7), decode all
   member values, compute percentile/probability by hand, compare against
   the published stat frame within packing quantization.
3. Completeness-gate tests: missing one member frame → unit skipped +
   pending; appears after the member backfill fills it.
4. Resume/preemption/promote tests (member-pass twins).
5. Canary scope: suffix classifier keeps stats ids out of parity-canary
   scope (same lesson as `_ensemble_dead_alias_vars`).
6. CF `HIT` verified on stats tiles/binaries after first prod run.
7. **First-run acceptance budget (6A, gefs precip_total — numeric, per
   external review; thresholds adjustable but a number must exist):**
   - stats pass wall ≤ 20 minutes for the run;
   - zero mean-publish latency regression attributable to the pass
     (mean freshness check, the Phase 3 method);
   - RSS comfortably below the unit's `MemoryHigh` throughout;
   - zero completeness-gate bypasses; zero stat frames from partial
     rosters (journal + spot-audit);
   - CF `HIT` on stats tiles and binaries;
   - meteogram point-samples of a stats var resolve correctly.
   6B/6C advance only on a green 6A (then 6B) budget.

## 11. Explicitly out of scope

MSLP member lows (D-E); tmp2m stats (D-A, one descriptor away); global
regions (all-tiers deferral stands; nothing here hardcodes region); meteogram
band charts (§8 — next, frontend-only); Model Guidance probability *table*
(orthogonal, its locked fh windows unchanged).

*Draft 2026-07-08 — implementation starts on approval.*
