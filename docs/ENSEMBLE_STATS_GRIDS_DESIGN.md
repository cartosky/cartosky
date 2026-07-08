# Ensemble Stats Grids — Phase 6 Design (Tier 2)

> Status: DRAFT — awaiting Brian's approval before implementation (same
> recommend-first gate as the Phase 2 scheduler design).
> Scope decisions D-A…D-E ratified 2026-07-08; recorded in §2.
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

**Adding tmp2m later = one descriptor on gefs/eps tmp2m.** Adding a
threshold = one list entry. No schema, pass, or frontend changes.

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
`np.nanpercentile` on stacks with NaN fringes, scattered gaps, all-NaN
pixels, and single-valid-member pixels.

## 5. The stats pass (D-B) — `builder/stats.py`

Same skeleton as the member pass, one stage later:

- **Plan**: `build_stats_plan(plugin, model_id, run_id, region)` from the
  descriptors; fhs from `scheduled_fhs_for_var` (region/global-agnostic per
  §3.7; no constants).
- **Unit of work = (base_var, fh)**: decode the member frames for that fh
  from **published** (members are promote-gated, so published = complete
  sets), one sort → all percentile frames + all probability frames for that
  fh. Spike-measured stack cost: 53 MiB + ~0.1 s decode — memory is a
  non-issue even on the EPS box.
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

Frontend scope: capabilities plumbing, product selector component, permalink
param, hover-sample label (shows the product's units — `%` for
probabilities). No member data touches the client.

## 8. Meteogram feed (future, designed-for now)

Stats variables are ordinary runtime vars on the binary-sampling path:
packed frames + run-manifest entries (full-profile publish registers them)
+ existing runtime-var resolution. **The meteogram can therefore sample
`precip_total__p50` etc. today's-code-style with zero backend changes** —
a client that requests those var ids gets point series. Feeding plume-style
percentile bands (e.g. p10–p90 shading on the Ensembles tab) is
frontend-only work: request the stats ids alongside the mean, render bands.
Explicitly out of Phase 6 scope, deliberately cheap next.

## 9. Config / knobs

- `CARTOSKY_STATS_PUBLISH_MODELS` — allowlist, empty default (mirrors
  members).
- Descriptor lists are the product knobs (per D-A's easy-additions bar).
- Optional (only if the first-run wall demands it): per-descriptor
  `prob_fh_stride` to thin probability frames; not implemented up front.

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
7. First capped prod run: wall + RSS observation (§5 estimate validated),
   spot-check per (2), then eps enable.

## 11. Explicitly out of scope

MSLP member lows (D-E); tmp2m stats (D-A, one descriptor away); global
regions (all-tiers deferral stands; nothing here hardcodes region); meteogram
band charts (§8 — next, frontend-only); Model Guidance probability *table*
(orthogonal, its locked fh windows unchanged).

*Draft 2026-07-08 — implementation starts on approval.*
