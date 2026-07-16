# Audit Backlog — 2026-07-16

Synthesis of two independent doc-review passes (Claude, GPT), each given the
full `/docs` corpus (24 files, ~7,200 lines) plus repo access, asked to
identify the highest-value audits *not* already covered by existing docs.
Findings were cross-checked against each other; several items below were
verified directly against the live repo during synthesis (marked
**CONFIRMED**) rather than taken on either model's word.

**TLDR:** The builder pipeline (derive/fetch/scheduler/stats) has had
exhaustive, line-cited audit coverage across three docs. Everything
*downstream* of the builder — frontend state/concurrency, deployment
hygiene, cache correctness, security, and operational resilience — has had
partial-to-zero coverage. Two concrete bugs were found and confirmed during
this synthesis pass (not hypothetical): a scheduler unit running as root in
the checked-in template, and fully unpinned backend dependencies. Both are
listed as immediate fixes, not audit items — they don't need investigation,
they need a PR.

---

## 1. Already covered — do not re-audit

| Area | Doc | Depth |
|---|---|---|
| Builder correctness (derive/fetch/scheduler/members) | `BUILD_PIPELINE_AUDIT_2026-07-07.md` + `_ROADMAP_2026-07-14.md` | Exhaustive, line-cited, mostly fixed |
| Ensemble stats/percentile pipeline | `STATS_AUDIT_2026-07-14.md` | Exhaustive |
| Cumulative-derive validity semantics | `VALIDITY_SEMANTICS_2026-07-14.md` | Decision-complete |
| Viewer animation/scrub/load perf (point-in-time) | `PERFORMANCE_REVIEW_2026-07-02.md` | Measured, mostly fixed |
| Share/screenshot/GIF architecture | `SHARE_MODAL_OVERHAUL_IMPLEMENTATION_PLAN.md` | Shipped; §10 follow-up log exists |
| Compare/diff mode | `COMPARE_DIFFERENCE_MODE_DESIGN.md` | Design-locked |

---

## 2. Confirmed during this synthesis (not audit items — fix directly)

### 2.1 HIGH — `csky-satellite-rgb-scheduler.service` runs as root in the checked-in template

**CONFIRMED** by direct read of `deployment/systemd/csky-satellite-rgb-scheduler.service`:

```
[Service]
Type=simple
User=root
EnvironmentFile=/etc/cartosky/scheduler-satellite-rgb.env
Environment=GDAL_CACHEMAX=256
...
```

No `Nice=`, no `CPUWeight=`/`IOWeight=`, no `Group=`, no `UMask=`. Contrast
with `csky-gfs-scheduler.service` (checked same pass): `User=cartosky`,
`Group=cartosky`, `UMask=0027`, `Nice=10`, `CPUWeight=50`, `IOWeight=50`.
This directly contradicts the operator's own stated standard ("all scheduler
units should have `User=cartosky`, nice priority parity, and
`MemoryHigh`/`MemoryMax` caps") — and it's in the repo template, not just
prod drift, so a fresh deploy from this template reintroduces the exact
class of incident the GOES RGB scheduler already caused once (hand-placed
root unit, started inadvertently by a `csky-*scheduler*` glob restart).

**Fix (S):** align to the GFS template — `User=cartosky`, `Group=cartosky`,
`UMask=0027`, `Nice=10`, `CPUWeight=50`, `IOWeight=50`, plus a `MemoryHigh`/
`MemoryMax` cap sized from the sizing-spike numbers for this pipeline. No
audit needed; this is a direct copy-the-working-template fix.

### 2.2 HIGH — `backend/requirements.txt` has zero version pins

**CONFIRMED** by direct read — every dependency is unpinned except
`playwright>=1.44.0`:

```
fastapi
uvicorn[standard]
numpy
xarray
h5netcdf[h5py]
rasterio
rio-tiler
scipy
herbie-data
pyproj
...
```

This is a direct threat to correctness work already paid for: if a rebuilt
or newly provisioned host pulls a different rasterio/GDAL/pyproj minor
version, warp/resampling/GRIB-decode behavior can drift silently out from
under the exact numerical guarantees `BUILD_PIPELINE_AUDIT` spent three
passes verifying (e.g. the Kuchera SLR formula, unit-conversion constants,
`GRIB_NORMALIZE_UNITS` handling) — with no signal that anything changed.

**Fix (S–M):** pin exact versions (`pip freeze` from the current known-good
venv), commit a lockfile-equivalent, and re-verify the full `pytest
backend/tests` suite against the pinned set before treating it as
production-authoritative. Folds naturally into item A6 below if that audit
is commissioned, but the pin itself shouldn't wait for it.

### 2.3 MED — No CI workflow

**CONFIRMED** — `.github/` contains `agents/`, `instructions/`, `prompts/`,
and a PR template, but no `workflows/` directory. Nothing enforces
`pytest`, `ruff`, `npm run build`, or Playwright on a PR; quality currently
depends entirely on remembering to run the right commands locally. Scoped
under item A6 (Release engineering audit) rather than fixed standalone,
since "what should the gates actually check" is a real design question, not
a one-liner.

### 2.4 Reported by GPT, not independently verified this session

These are plausible, specific, and worth checking, but weren't re-confirmed
against the repo during synthesis — verify before acting:

- Privacy policy (`frontend/src/pages/privacy.tsx`) describes Stripe
  processing as future-facing and lists PostHog but not Mixpanel, while
  both Stripe and Mixpanel are reportedly present in code/`package.json`.
- Playwright config reportedly defines five browser/device projects with no
  CI enforcement and no confirmed evidence all five are exercised regularly.

---

## 3. Prioritized audit backlog

Severity/priority reflects impact **and** the October busy-season freeze as
a hard scheduling constraint — Tier 1 must clear before the freeze; Tier 2
can run during/just after it; Tier 3 is opportunistic.

| # | Audit | Source | Tier | One-line rationale |
|---|---|---|---|---|
| A1 | Frontend state/concurrency/recovery audit | GPT (primary), Claude (narrower version) | 1 | Same bug species already caught twice in prod (runs-list race, NWS retry state); explicitly flagged as a systemic risk in operator's own instructions |
| A2 | Retention/orphan sweep across data root | Claude | 1 | Same bug shape already caught twice (manifests — fixed; ensemble-stats health JSON — open); cheap, mechanical, high hit-rate |
| A3 | Cache-status + cross-surface consistency audit | Claude + GPT (GPT extends scope) | 1 | Direct check against a stated hard rule (`DYNAMIC` on a binary is a bug); GPT's addition — do map/hover-sample/city-labels/meteogram/compare/screenshot all resolve the same run? — is a sharper, non-obvious risk neither of us had standalone |
| A4 | Screenshot/GIF adversarial hunt-for-bugs pass | Claude | 1 | Highest-priority busy-season feature and explicitly "never acceptable to break"; only verified against its own phase gates so far, never audited adversarially |
| A5 | Forecast-page external-data fallback audit (NWS/Open-Meteo/geocoding) | Claude | 1 | Real incident already shipped a patch for this exact bug class (`2026-07-13-forecast-nws-enrichment-recovery.md`); likely siblings in geocoding/alerts |
| A6 | Release engineering / CI / dependency audit | GPT | 1 (pins go now, rest can follow) | Confirmed: no CI, no pinned deps (see §2.2–2.3). Design the actual gate matrix; don't just pin and stop |
| A7 | Security/auth/billing audit | GPT (primary), Claude (narrower Stripe-only version) | 2 | GPT's scope is more complete: SSRF via the screenshot service, per-process vs deployment-wide rate limiting (confirmed 4 uvicorn workers), CSP/HSTS/header baseline, Stripe webhook idempotency/replay. Do before flipping `CARTOSKY_BILLING_ENABLED`, which is already post-freeze per roadmap |
| A8 | Scheduler-fleet RAM budget (holistic) | Claude | 2 | EPS is the known tight *individual* unit; nobody's modeled worst-case simultaneous RSS across all 17+ schedulers against the 32GB ceiling |
| A9 | Cross-browser / responsive / visual-regression audit | GPT | 2 | Playwright matrix exists but (reportedly) has no screenshot baselines; mobile-first is a stated priority with no dedicated visual audit to date |
| A10 | Operational resilience / disaster recovery | GPT | 2 (right-sized down from GPT's framing) | Single-server, solo-operator — no documented backup/restore story for SQLite state, OAuth linkage, or config. Scope as a practical restore-tested checklist, not a formal RTO/RPO program |
| A11 | Continuous performance budgets / regression gates | GPT | 3 | Turns the point-in-time Performance Review into enforced numeric budgets (LCP/INP/CLS, GPU texture memory); a process investment, not a bug hunt |
| A12 | Scientific golden-case validation | Claude | 3 (arguably underrated by both) | Frozen historical storms, cross-source agreement, map/sample/meteogram unit consistency — the most direct test of the #1 stated priority (weather data accuracy), and neither review gave it top billing |
| A13 | Accessibility / WCAG 2.2 AA audit | GPT | 3 | Real, well-scoped gap (weather palettes under CVD, non-visual data alternatives, meteogram semantics) — but absent from every stated CartoSky priority, so ranked below anything touching data accuracy, money, or the freeze |
| A14 | Dependency/CVE scan (`npm audit`/`pip-audit`) | Claude | 3 | Cheap and mechanical; effectively subsumed by A6 if A6 is commissioned |
| A15 | Frontend E2E coverage-gap audit | Claude | 3 | Mirrors the builder's own §6 test-gap section; do after A1/A4 land so new coverage targets the fixed behavior, not the old bug |
| A16 | SEO/discoverability audit | GPT | 3 | Real but lowest-leverage relative to every other item here |

---

## 4. Where the two reviews disagreed

Recorded deliberately, not smoothed over — useful for calibrating future
audit requests.

- **Accessibility ranking.** GPT ranked it #2 overall ("the single largest
  gap between polished and best-in-class"). Claude ranked it Tier 3,
  reasoning that none of CartoSky's stated priorities (weather accuracy,
  WebGL smoothness, mobile-first, fallback robustness, RAM/disk safety,
  screenshot reliability) mention accessibility, and a solo operator racing
  a hard freeze should sort by *stated* priority, not generic SaaS-polish
  norms. Content of GPT's scope (A13) is kept in full; only the ranking
  changed.
- **Freeze anchoring.** Claude's original ranking sorted primarily by
  "must happen before October freeze"; GPT's ranking sorted by
  abstract severity/impact with no reference to the freeze at all. The
  combined table above uses freeze-anchoring as the primary sort key,
  since it's an explicit, hard operator constraint, not a modeling choice.
- **Sizing of operational-resilience work.** GPT's framing (A10) reads as
  enterprise SRE scope — SBOM generation, formal RTO/RPO, artifact
  provenance/rollback verification. For one operator on one server, that's
  oversized. Kept the finding, shrunk the deliverable to a restore-tested
  backup checklist.
- **Deliverable format.** Claude's original response included a ready-to-
  paste Codex/Claude Code kickoff prompt (§6 below); GPT's did not. Given
  the operator's stated agent-driven workflow, a taxonomy without a handoff
  artifact is an incomplete deliverable — this doc exists specifically to
  close that gap.

---

## 5. Recommended sequencing

**Now (immediate fixes, not audits):** §2.1 (root scheduler unit) and §2.2
(dependency pins). Both are small, both are already confirmed, neither
needs investigation.

**Before the October freeze (Tier 1, A1–A6):** run A2 and A3 first — both
mechanical, fast, high hit-rate. Queue A1, A4, A5 next since each needs a
genuine multi-pass read-through comparable to the builder audits, and
findings need time to land before the freeze. A6 (CI gate design) can run
in parallel with any of the above — it doesn't compete for the same code
surface.

**During/after the freeze (Tier 2, A7–A10):** none of these block
busy-season features. A7 (security/billing) should specifically land before
`CARTOSKY_BILLING_ENABLED` is flipped, which is already scoped post-freeze.

**Opportunistic (Tier 3, A11–A16):** schedule as time allows; A12
(scientific golden-case) is worth pulling forward if a spare cycle opens up,
given it tests the platform's core value proposition directly.

---

## 6. Ready-to-use kickoff prompt — A1 (frontend state/concurrency audit)

```
Audit frontend state, concurrency, and recovery correctness across
App.tsx, map-canvas.tsx, grid-webgl.ts, compare.tsx, and forecast.tsx.

Context: this codebase has already shipped two confirmed production bugs
in this exact family — a runs-list race that deadlocked the viewer
(PERFORMANCE_REVIEW_2026-07-02.md §8: a useEffect completion guard checked
a generation counter whose dependency array omitted `variable`, discarding
valid completions and leaving `runs` permanently empty) and a stale NWS
retry state that didn't recover on tab-visibility restoration
(docs/plans/2026-07-13-forecast-nws-enrichment-recovery.md). Treat these
as the known pattern, not isolated incidents — look for siblings.

For every model/run/variable/forecast-hour/region transition and every
async data-fetching effect, check:
- Effect dependency-array completeness (the operator's own standing note:
  "React effect dep arrays are load-bearing" — audit `isLoaded`,
  `basemapMode`, `isAnimating`, and any other gating boolean/ref for
  correct inclusion).
- AbortController ownership and cleanup on unmount / rapid re-selection.
- Stale-closure risk in callbacks captured before a generation/selection
  change.
- Visibility, offline/online, and background-tab-throttling recovery for
  every retry/polling loop, not just the one already fixed.
- WebGL context loss and recovery (`grid-webgl.ts`) — is there any handler
  at all, and does the viewer recover without a full reload?
- Rapid scrub + product switch + compare-mode swap + permalink restoration,
  fired in combination, not just individually.
- Behavior under slow, reordered, partial, or malformed API responses.

Use the same severity legend as BUILD_PIPELINE_AUDIT_2026-07-07.md
(HIGH = confirmed or near-certain user-facing hang/wrong-state/crash;
MED = latent race with a plausible but unconfirmed trigger; LOW = cleanup).
For each HIGH/MED finding, cite file:line and state whether it's the same
pattern as the two known incidents above or a new one.

Output: a findings doc at docs/FRONTEND_CONCURRENCY_AUDIT_<date>.md
matching the existing audit doc format (TLDR, severity-tagged findings
with fix sizing, recommended sequence, a "verified sound" section for
anything checked and found correct).
```

---

## 7. Sources

- Claude (this session), full `/docs` review + direct repo verification of
  §2.1–2.3 via Filesystem MCP.
- GPT, given the identical prompt in a separate session; full findings
  preserved in conversation history, synthesized above with attribution.
