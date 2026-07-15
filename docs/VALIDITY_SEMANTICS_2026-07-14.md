# Validity semantics for cumulative derives — decision note (2026-07-14)

Roadmap Wave 0 item 7: the one-document decision covering audit findings
**1.9** (three step-validity definitions), **1.3** (OR-merged validity silently
undercounts), and **1.7** (ptype binarizes fractional masks; snowfall
doesn't). Wave 1 items 3-6 implement this note; Wave 1 item 1 (cache
fingerprint) ships with or before the first change here.

**Status: D1-D5 DECIDED 2026-07-14 (operator sign-off).** D2 = Option B with
two refinements made binding at sign-off: the `accum_step_gap` flag payload
records the affected-pixel percentage, and gap flags are exposed in admin
telemetry (the /status dashboard pattern), not just sidecars. Wave 1 items
3-6 implement exactly what follows.

---

## Current state (verified 2026-07-14)

- **Step validity diverges (1.9):** precip_total accepts any finite value and
  clamps negatives to valid 0 (`derive.py:5443-5450`); snowfall, Kuchera, and
  both ptype accumulations require `isfinite & >= 0` (and `<= 1` for masks)
  (`derive.py:5804/5839/5868, 6783-6808, 7174-7229, 5276`). A negative
  sentinel that survives fetch scrubbing is "0.00 in" in precip_total and
  invalid everywhere else.
- **Cross-step merge is OR everywhere:** `valid_mask = logical_or(valid_mask,
  step_valid)`; final frame NaNs only never-valid pixels
  (`derive.py:4099-4104`); the incremental-resume merge is also OR
  (`derive.py:5942-5947`). A pixel invalid in N-1 of N steps renders a
  confident finite total.
- **Quality flags exist but don't persist across resume:**
  `_record_derive_quality` → sidecar `quality: full|degraded` +
  `quality_flags` (`derive.py:815-833`, `pipeline.py:1037-1041, 1666,
  1956-1957`); flags cover Kuchera/ECMWF-ptype paths only — precip_total,
  10to1 snowfall, and GFS ptype accumulation record none. The cumulative
  cache npz stores `data/crs_wkt/transform/grid_cache_key/coverage_start_fh`
  only (`derive.py:549-561, 658-679`) — **flags from cached steps are lost on
  resume** (1.2's known limitation).
- **Binarization diverges (1.7):** snowfall binarizes only when
  `snow_mask_threshold` is explicitly configured, else fractional mean
  (`derive.py:5856-5861`); ptype accumulation binarizes unconditionally at
  `ptype_mask_threshold` default 0.5 (`derive.py:7218-7222`) — GEFS-mean
  cfrzr (fractional, rarely ≥ 0.5) collapses ice accumulation to ~0.
- **Frame-level NaN is one bucket:** packed nodata sentinel; nothing
  distinguishes off-domain from data gap for downstream consumers.

---

## D1 — What makes a step valid (implements 1.9 → Wave 1 item 3)

**DECIDED:** one shared helper, used by all five strategies:
scalar fields `valid = isfinite(x) & (x >= 0)`; mask/probability fields
`valid = isfinite(m) & (m >= 0) & (m <= 1)`. **Negative scalar values are
invalid, not clamped** — a negative sentinel is missing data, not zero
precipitation. Invalid pixels contribute 0 to the running total and False to
step validity (their downstream meaning is D2's decision).

Consequence: precip_total is the only behavior change (clamped-valid-0 →
invalid). Regression test: a negative-sentinel swath in one step must produce
identical totals in all five strategies and must not read as valid zero.

Alternative (rejected): keep precip_total's clamp — preserves the historical
"finite means valid" but keeps the 1.9 divergence and hides sentinels.

## D2 — Cross-step validity propagation (implements 1.3 → Wave 1 item 6)
**The main product decision in this note.**

Options:

- **A. AND-validity / NaN:** pixel NaN in the total once any contributing
  step was invalid there. Maximally honest; but one transient upstream gap
  (a corrupt 6 h record over a swath) permanently holes every later fh of
  the run — up to fh 384 — and per-pixel NaN patches read as rendering bugs
  to users. Harsh for a rare, usually-small defect.
- **B. OR-merge + mandatory degraded-quality flags (DECIDED):** keep
  rendering the accumulated value, but make the undercount *loud*: every
  strategy records a per-var flag (`accum_step_gap`) whenever any step
  contributes invalid pixels that were valid in other steps; sidecar flips to
  `quality: degraded`; flags **persist through the cumulative cache** (see
  D5) so resume cannot launder them. Binding refinements from sign-off: the
  flag payload records the **affected-pixel percentage**, and gap flags are
  **exposed in admin telemetry** (the /status dashboard pattern alongside
  ensemble-stats health and frames-404), not sidecars alone. NaN remains
  reserved for never-valid/off-domain pixels (D4).
- **C. Coverage-floor hybrid:** B, plus NaN pixels whose valid-step coverage
  falls below a floor (e.g. missing > K steps or > X% of the window).
  Honest *and* pretty, but adds a per-pixel missing-count plane to the
  cumulative cache and a second tunable — more machinery than the observed
  failure rate justifies today.

**Rationale:** the audit's complaint is the word *silently* — B removes
the silence at ~10% of C's complexity. Weather-consumer UX tolerates a
flagged approximate total far better than mid-run map holes; upstream gaps
are transient and small in practice; and B reuses the shipped 1.2 flag
machinery (`_record_derive_quality` + sidecar). Escalation path documented:
if post-ship telemetry shows `accum_step_gap` firing frequently or over large
areas, promote to C with the floor informed by real data. (Also note: whole-
step total failures already raise and retry — 1.3 is about per-pixel
partial-step invalidity, which bounds the blast radius B accepts.)

Scope note: flags-coverage extension is part of this decision — precip_total,
10to1 snowfall, and GFS ptype accumulation currently record **no** flags at
all; B requires all five strategies to record them.

## D3 — When fractional masks stay fractional (implements 1.7 → Wave 1 item 4)

**DECIDED:** mirror snowfall
everywhere — binarize only when the threshold hint (`snow_mask_threshold` /
`ptype_mask_threshold`) is **explicitly configured**; default is the
fractional mean. For ensemble-mean inputs the fractional mean is the expected
accumulation (30% of members say freezing rain → 30% of the precip counts as
ice), which is the defensible statistical meaning; unconditional 0.5
binarization is only correct for deterministic 0/1 masks, which pass through
the fractional mean unchanged anyway.

**Visible data change:** GEFS-mean ice accumulation goes from ~0 to real
values. Ship with before/after frames + release note (tmp850_anom °C
discipline), and a cumulative algorithm revision bump (D5). Note the default
`ptype_mask_threshold="0.5"` hint must be *removed from the code default*,
not from var specs — check which specs configure it explicitly and intend it.

## D4 — Frame-level semantics: NaN vs degraded

**DECIDED:**
- **NaN (packed nodata) means "no defined value here":** off-domain pixels,
  never-valid pixels, and (if C is ever adopted) below-floor pixels. Nothing
  else may write NaN into a published accumulation frame.
- **Degraded quality means "value present, known imperfect":** carried
  exclusively by the sidecar `quality` + `quality_flags` contract — the
  existing 1.2 machinery, extended per D2. No per-pixel provenance plane is
  published (deferred indefinitely; revisit only if a concrete consumer
  needs it).
- Downstream contract: sampling/meteogram/viewer treat NaN as nodata
  (unchanged); admin/status surfaces may aggregate `quality_flags` counts
  (fits the frames-404/ensemble-health dashboard pattern).
- **Frame-level extension (Kuchera gate decision, Wave 0 #8, 2026-07-14):**
  when a *whole frame's* required input cannot resolve at all
  (zero valid csnow samples for a step), the frame is rejected transiently
  (`HerbieTransientUnavailableError` → retry) rather than published with
  fabricated contributions — the same principle as pixel-NaN, one level up:
  absence over believable wrong data. Partial coverage stays publishable +
  flagged (`ptype_gate_partial_coverage`), consistent with D2.

## D5 — Incremental-cache invalidation on semantic change

**DECIDED:**
- Any change to D1-D4 semantics is a **cumulative algorithm revision bump**
  under Wave 1 item 1's fingerprint (hints hash + strategy identity +
  explicit revision). Wave 1 item 1 must therefore land with or before the
  first implementing PR (already the roadmap rule).
- The cumulative cache entry schema gains **accumulated quality flags**
  (D2-B requires it: flags computed over steps 1..k must survive a resume at
  step k+1). Entries missing the flags field (pre-change schema) are treated
  as fingerprint mismatches → recompute from scratch. No attempt to
  reinterpret old cache entries under new semantics — recompute is cheap
  relative to correctness (worst case one full-run rebuild per var).
- Mixed-semantics runs at deploy time are acceptable per the tmp850_anom /
  1.5 precedent: already-published frames keep old semantics until aged out;
  the fingerprint guarantees no *blending* within a frame.

---

## Implementation order (Wave 1 mapping)

1. Wave 1 item 1 (fingerprint + revision) — prerequisite.
2. Item 3 (1.9): D1 shared helper + regression test. Revision bump.
3. Item 4 (1.7): D3 conditional binarization + before/after frames + release
   note. Revision bump.
4. Item 6 (1.3): D2-B propagation + flag persistence in the cache schema
   (D5) + flags coverage for the three unflagged strategies. Revision bump.
   Regression tests: (a) mid-run invalid swath → total unchanged vs today but
   sidecar degraded with `accum_step_gap`; (b) resume-after-gap → flag
   survives from cached steps; (c) never-valid pixel → NaN (unchanged).
5. Item 5 (1.6, `probability_units` metadata) rides alongside; it feeds the
   same mask-normalization path but needs no decision here.
