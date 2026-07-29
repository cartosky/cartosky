# Phase 2B — Frontend data-domain / camera-preset split — plan (2026-07-29)

Status: LOCKED with amendments (§Review record). Fresh-context agent review
was unavailable (persistent API 529s); the orchestrator ran the source-level
review directly. The post-implementation verifier gate still applies. Implements the Phase 2B section of
`docs/MAX_WEEK_EXECUTION_PLAN_2026-07-27.md` against the landed Phase 2A
backend contract (`docs/PHASE_2A_DOMAIN_CONTRACT_DESIGN_2026-07-28.md`,
commits `d560256c..cff81ef4`).

## Backend contract being consumed (verified in source)

- Control/selection APIs accept `domain=` as a query parameter; **absent
  `domain=` resolves canonical**, identically to today
  (`backend/app/main.py:2739` `_request_domain_or_404`, threaded through
  `_resolve_run`/`_resolve_latest_run`/`_load_manifest`/`_load_grid_manifest`).
- Non-canonical **edge-served artifact URLs carry `domains/{d}` in the path**,
  inserted immediately before `{model}`:
  `/api/v4/grid/domains/{d}/{model}/{run}/{var}/{file}?v=...`,
  `/api/v4/domains/{d}/{model}/{run}/{var}/{fh}/contours/{key}`, same for
  `/vectors/`. Canonical artifact URLs are byte-identical to today and must
  **never** carry `domain=` (pinned by
  `test_canonical_artifact_routes_declare_no_domain_query_param`).
- Grid **binary frame URLs are emitted by the backend** inside the grid
  manifest (`_grid_file_url` adds the prefix for non-canonical domains), so
  the client does not construct them. The client **does** construct contour
  and vector URLs itself.
- Domain availability is declared at **variable level** via the existing
  capability field `supported_build_regions`
  (`frontend/src/lib/api.ts:59`); model canonical region via
  `canonical_region` (`api.ts:44`, `api.ts:87`). In the 2A shipping state no
  model declares a non-canonical domain — activation is Phase 3 capability
  data.

## Current-state inventory (scout-verified, spot-checked)

Conflation is narrower than feared. The viewer already splits the concepts
internally; the gaps are the API layer, permalinks, and Compare.

| Area | State | Citations |
|---|---|---|
| Viewer data requests | Already keyed to `dataRegion` = model `canonical_region` | `App.tsx:913-917` (derivation), `App.tsx:1639,1661,1916,3729,3742,3780,3937` (fetch sites) |
| Viewer camera | `region` state drives viewport presets only | `App.tsx:430,1125-1147`, `map-canvas.tsx:1582-1586`, `map-region-views.ts:16-41` |
| Permalink | `reg` param = camera region only; no domain concept | `permalink-read.ts:65-68`, `permalink.ts:37-38`, `use-permalink-sync.ts:21,51` |
| API layer | `region?` params exist on manifest/grid-manifest/frames fetchers but backend ignores them; no `domain` support anywhere | `api.ts:699,735,793` |
| Contour/vector URLs | Client-built, no domain support | `api.ts:1000-1009` `buildContourUrl`, `app-utils.ts:1763-1791` `buildVectorLayerUrl` |
| Point sampling | `fetchSample`/`fetchSampleBatch`/meteogram carry no region/domain | `api.ts:879-1000`, `meteogram-cache.ts:150` |
| Compare | Both panes' `useModelLoader` receive the shared camera `region` (`conus`) as their data region | `compare.tsx:794-812`, `use-model-loader.ts:319,358,378,477` |
| Compare permalink | No region/domain field at all | `compare-permalink.ts:1-105,166-170` |
| Bootstrap | Initial `region` is camera-only before capabilities load; `fetchBootstrap` `region=` is viewport hydration | `App.tsx:392-406,430`, `api.ts:635-664` |
| Region option filtering | Already capability-driven via `supported_build_regions` + canonical bbox | `app-utils.ts:903-938`, `App.tsx:3589-3658` |

No hardcoded `na | global` union exists client-side; region IDs are plain
strings validated against capabilities. Keep it that way.

## Design decisions

### D1 — Domain state and typing

New viewer state `domain: string | null` in `App.tsx` (`null` = model
canonical). It is a **published build-region ID**, a plain string validated
against capabilities at use time — no union type. Bootstrap viewport presets
and `MAP_VIEW_DEFAULTS.region` never flow into it.

### D2 — Effective-domain resolution (single helper, single call path)

One pure helper in `app-utils.ts`:

```
resolveDataDomain(requestedDomain, modelCapability, variableCapability):
  canonical = modelCapability?.constraints?.canonical_region
              ?? modelCapability?.canonical_region ?? null
  if requestedDomain is null or requestedDomain === canonical → null  // canonical
  if variableCapability?.supported_build_regions includes requestedDomain
      → requestedDomain
  → null  // graceful degrade to canonical, never an error
```

`App.tsx:913-917`'s `dataRegion` derivation is extended (not replaced): data
requests key to `resolveDataDomain(...) ?? dataRegion`. **`App.tsx:746`
semantics are preserved — camera `region` never reaches a data request.**
Degrading (variable/model lacks the requested domain) keeps the URL's
`domain=` intact (stickiness across model/variable switches, mirroring
variable-stickiness behavior) but issues canonical requests.

### D3 — API layer

Add optional `domain?: string | null` (last param or options-object member,
matching each signature's existing style) to: `fetchManifest`,
`fetchGridManifest`, `fetchFrames`, `fetchRuns`, `fetchVars`, `fetchSample`,
`fetchSampleBatch`. Behavior: **append `domain=` only when non-null.** When
null, emitted URLs are byte-identical to today (regression-pinned in tests).

`buildContourUrl` and `buildVectorLayerUrl` gain the same optional param and
insert `domains/{encodeURIComponent(d)}/` immediately before the model
segment when non-null.

The existing dead `region?` params on these fetchers are left untouched
(backend ignores them; removal is unrelated cleanup, not this phase).

**Out of scope:** meteogram requests (multi-model, canonical-only until
Phase 3 global sampling gates), RGB/observed routes (canonical-only per 2A),
`fetchBootstrap` (`region=` there is viewport hydration, not data domain).

### D4 — Permalink

- Key is literally **`domain`** (the plan doc names it; do not abbreviate to
  match `reg`).
- `permalink-read.ts`: parse `domain` → `state.domain: string | null`.
- `permalink.ts` `buildPermalinkSearch`: serialize only when non-null.
- `use-permalink-sync.ts`: thread it.
- **A link without `domain=` must produce byte-identical request URLs and
  identical viewport to today** — this is the TWF-permalink compatibility
  gate and gets its own tests.
- `reg=` keeps its exact current meaning (camera preset).

### D5 — No new viewer UI control in 2B

In the 2A shipping state, no model declares a non-canonical domain, so a
domain selector would render permanently empty and be untestable against
real capabilities. Phase 2B ships the plumbing (URL-driven domain, request
keying, Compare rule); the visible selector ships with Phase 3 alongside the
first model that declares `global`. This also avoids touching
`ViewerRail.tsx`/`ViewerTopBar.tsx`, which carry uncommitted
viewer-redesign work. If review finds a cheap, isolated place to surface the
active non-canonical domain (e.g. a settings-menu row), it may be added, but
it is not an acceptance requirement.

### D6 — Compare (v1 decision from the max-week plan, locked)

- **One shared `domain` across both panes.** Add `domain` to
  `ComparePermalinkState` (`compare-permalink.ts`), same absent-means-
  canonical rule.
- Effective domain = `resolveDataDomain` evaluated against **both** selected
  model/variable pairs; only if both support it does either pane use it —
  otherwise both degrade to their canonical domains. No per-pane domains, no
  NA-vs-global pane comparisons.
- Fix `use-model-loader.ts` to stop treating the camera `region` argument as
  a data region: derive each pane's data region from that pane's model
  capability (`canonical_region`), mirroring `App.tsx:913-917`, then apply
  the shared domain via `resolveDataDomain`. This corrects the latent
  Compare bug scout flagged (both panes request whatever the shared camera
  region string is) — in practice backend ignored the param, so behavior is
  unchanged today, but Phase 3 would weaponize it.
- Camera stays shared and domain-independent.

### D7 — Tests (synthetic fixtures only — no global artifacts exist)

Unit (vitest, colocated with existing suites):
1. `resolveDataDomain`: null/canonical/supported/unsupported/no-capability
   matrix.
2. Permalink round-trip: `domain` absent → null → not serialized;
   non-null round-trips; `reg` untouched.
3. URL builders: `domain=null` output **byte-identical** to current output
   (golden strings); non-null appends `domain=` on control APIs and inserts
   `domains/{d}` on contour/vector builders at the correct position.
4. Compare shared-domain rule: both-support → applied; one-lacks → both
   canonical; per-pane data region derived from each model's canonical.

E2E (Playwright, mocked network per G4 discipline):
5. Synthetic capabilities fixture declaring `supported_build_regions:
   ["na","global"]` on one model/variable; load viewer with `?domain=global`;
   assert intercepted manifest/grid-manifest/frames requests carry
   `domain=global` and grid file URLs from the mocked manifest are fetched
   as-emitted; assert camera unchanged vs `region=` handling.
6. TWF-permalink compatibility: a stored real-shape permalink (no `domain=`)
   produces request URLs identical to a baseline capture and correct
   viewport.
7. G4: screenshot pixel-diff on fixed URLs unchanged (existing spec run).

Known environment fact: Playwright collection currently fails on
`compare-map-regressions.spec.ts` (pre-existing, Vite-only import, tracked).
Acceptance is "new specs green, no new failures," not full-suite green.

### D8 — Working-tree hygiene

Uncommitted viewer-redesign edits exist in `ViewerRail.tsx`,
`ViewerTopBar.tsx`, `ViewerMobileBar/Sheet`, `TimelineTrack.tsx`,
`globals.css`, `index.html`, and five e2e specs. Phase 2B must not modify
any of those files. Files Phase 2B touches (`App.tsx`, `api.ts`,
`app-utils.ts`, `permalink*.ts`, `use-permalink-sync.ts`,
`use-model-loader.ts`, `compare.tsx`, `compare-permalink.ts`, new tests) are
currently clean, so the two workstreams can coexist; Phase 2B commits must
stage only its own files.

## Acceptance (from the max-week plan, restated as checks)

- [ ] Real TWF permalinks (no `domain=`) resolve to identical data requests
      and viewport (D7.6).
- [ ] `domain=global` routes control-API requests with `domain=` and consumes
      backend-emitted `domains/`-prefixed artifact URLs against synthetic
      fixtures, without altering camera behavior (D7.5).
- [ ] `region=` alone still changes only the viewport.
- [ ] Compare enforces the shared-domain rule and degrades cleanly when one
      model lacks the domain (D7.4).
- [ ] G4 screenshot pixel-diff green on fixed URLs.
- [ ] Frontend production build green; vitest green; new Playwright specs
      green with no new failures.
- [ ] No modifications to viewer-redesign-touched files (D8).

## Review record — 2026-07-29 (source-verified amendments)

All citations re-verified against source before locking:

1. **Backend `domain=` route coverage confirmed** — `/api/v4/bootstrap`
   (`main.py:4818`), `/{model}/runs` (5003), `/{run}/manifest` (5031),
   `/{run}/vars` (5088), `/frames` (5128), `/grid-manifest` (5241),
   `/sample` (5844) all accept `domain: str | None = Query(None)`.
   `/sample/batch` takes `domain` as a **POST body field**
   (`SampleBatchIn.domain`, `main.py:2609`, applied at 6033) — D3 must send
   it in the body there, not as a query param. `/bootstrap` stays out of 2B
   scope (its `region=` is viewport hydration).
2. **Amendment to D6/implementation:** `use-model-loader.ts` uses `region`
   in the selection cache key (`use-model-loader.ts:205`) and in three
   effect dependency arrays (338, 409, 532), not just fetch args. The
   effective domain must replace it in the selection key and dep arrays,
   otherwise domain changes will not retrigger loads. Because the backend
   ignores the legacy `region=` query on these routes, swapping it out
   produces no network-behavior change today.
3. **Contour/vector insertion is unambiguous** — `buildContourUrl`
   (`api.ts:1000-1009`) and `buildVectorLayerUrl` (`app-utils.ts:1763-1791`)
   both build from a literal `/api/v4/` prefix; `domains/{d}/` inserts
   directly after it, matching backend routes `main.py:6189` (contours) and
   `main.py:6297` (vectors). Grid-binary URL prefixing is server-side
   (`main.py:5779, 5791`) and needs no client work.
4. **Share/screenshot paths are safe by construction** —
   `screenshotUrlForState` (`share-utils.ts:343`) inherits the live URL's
   search params and only overrides lat/lon/z/fh, so a synced `domain=`
   flows into screenshot URLs automatically. Add one assertion to D7.5
   covering this.
5. **Compare permalink key space clear** — `lr`/`rr` are runs
   (`compare-permalink.ts:123-159`); `domain` collides with nothing.
6. **Degrade-semantics attack (D2) considered and accepted** — a shared
   link with sticky `domain=` also pins model and variable in the same URL,
   so the recipient resolves the identical capability pair and degrades
   identically. No trap.
7. **Amendment to D7:** new Playwright spec files must be added to the
   `.gitignore` e2e allowlist (known trap — silently uncommitted specs
   otherwise). Follow the existing `viewer-*.fixtures.ts` + `page.route`
   interception pattern (e.g. `viewer-first-paint.fixtures.ts`).
