# Ensemble Products — Deferred Backlog

> Split out 2026-07-09 so deferred items live in ONE place instead of
> scattered across the pipeline plan, scheduler design, and stats design.
> Each entry lists status, the gate to start, and where the detailed scope
> lives. The shipped system (Phases 1–6: members, plumes, stats grids,
> viewer product selector, compare/diff products) is documented in
> `ENSEMBLE_MEMBER_PIPELINE_PLAN.md` and its design docs — not here.

| # | Item | Status | Gate / trigger | Scope lives in |
|---|------|--------|----------------|----------------|
| B1 | **Meteogram percentile band + probability charts** (Ensembles tab) | SHIPPED 2026-07-09 (working tree) | — | Stats design §8 (data path); design note below |
| B2 | **tmp2m ensemble products** | Deferred (D-A) | Product-design conversation — temperature wants threshold/risk maps (P(< 32°F), P(> 100°F)), which requires implementing the RESERVED `__prob_lt_{thr}` suffix (grammar already claims it) | Stats design §3; `classify_ensemble_var_id` in models/base.py |
| B3 | **`__spread` map product** (P90 − P10 as a published grid) | Idea, unscoped | Wanted when spread maps matter; deliberately NOT a compare-tool mode | One descriptor-family addition to the stats pass; needs its own colormap + diff scale entries |
| B4 | **pf-mean missing-member tolerance** (build EPS mean from ≥45 members when ranges fail validation) | SCOPED, deferred by Brian | Next upstream index/file corruption incident that costs a run | `ENSEMBLE_MEMBER_SCHEDULER_DESIGN.md` §15 (full scope incl. env knob, logging, member-pass asymmetry) |
| B5 | **MSLP + member low locations** | Double-gated | Its own data-source sizing spike first (net-new variable for both models) | Pipeline plan Phase 6 note; reuses pressure-center machinery across member fields |
| B6 | **Tier 3 — browsable per-member maps** | NO-GO (2026-07-06 sign-off) | Server resources expanded | Sizing spike doc §10; measured ~15 GB/run/var full-profile |
| B7 | **Global coverage** (all tiers) | Deferred (2026-07-06 sign-off) | Brian green-lights global | Plan §3.7 keeps all member/stats code region-agnostic — no bboxes/dims hardcoded anywhere |
| B8 | **Probability diff scale tuning** | Shipped at ±50 pp | Only if prod use shows the range too wide/narrow | One constant: `PROBABILITY_DIFF_SCALE` in compare-diff-scales.ts |

## B1 design note — meteogram percentile/probability charts (SHIPPED)

Data path (settled, stats design §8): stats vars are ordinary
binary-sampling run-manifest variables — the meteogram samples
`precip_total__p50` etc. as plain `variables` entries with ZERO backend
changes, and values match the maps exactly (same packed artifacts).

Ratified display (Brian, 2026-07-09): the charts render as ADDITIONAL
cards under the existing member views — View "GEFS members" + Variable
"Precipitation" stacks plume → percentile bands → probabilities on one
page (no new View entries; all like-variable content stays together).
Band chart = 10–90th percentile fill (light, model hue) + 25–75th fill
(darker) + bold white median + dashed white mean overlay. Probability
chart = one line per descriptor threshold on a FIXED 0–100% axis,
cool→hot stroke ramp by threshold severity.

Implementation notes:
- Components: `EnsemblePercentileBandChart` / `EnsembleProbabilityChart`
  (uPlot native `bands`); config + id grammar mirrors in
  `chart-constants.ts` (`ENSEMBLE_STATS_PERCENTILES`,
  `ENSEMBLE_STATS_PROB_THRESHOLDS` — adding a variable there is the whole
  frontend change when B2/snowfall land).
- TWO meteogram requests, not one: the request schema caps `variables`
  at 6 (main.py MeteogramRequest) and the band vars (base + 5
  percentiles) and prob vars (6 thresholds) each exactly fit.
- Both requests are pinned to the run the MEMBER payload serves so the
  page is run-consistent; the backend silently falls back to the latest
  complete run when a pin can't serve the vars, so card subtitles read
  the SERVED `run_id`, never the pin.
- Gate-skipped products degrade gracefully: missing thresholds are
  omitted (colors stay stable via configured-list position); a partial
  percentile set renders the empty state rather than a misleading
  half-band.
- tmp2m member views show the plume only until B2 adds its descriptor.
