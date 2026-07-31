# Global go-live UI — design proposal (2026-07-31)

Status: **APPROVED 2026-07-31** (U1 `Coverage`, U2 sticky+note, U3
coverage-gated World preset) — with the explicit operator requirement that
**default-to-global must be a trivial future flip** (§7).
Scope: the user-facing controls that expose the global data domain. Everything
below is capabilities-driven — deploying the frontend with these controls IS
go-live; there is no separate flag to flip.

## 1. Coverage control (the domain selector)

- **Location:** SOURCE section of the rail, new row directly under **Model**
  (order: Model → Coverage → Variable → Run). It is a data-selection act, so
  it lives with the data controls — never in the VIEW section, whose "Region"
  is a camera preset (Phase 2B split; the two must not share a name).
- **Label:** `Coverage`. ("Region" collides with the camera preset section;
  "Domain" is jargon.)
- **Control:** two-segment toggle — `North America | Global` (canonical label
  derived from the model's canonical region; segment set derived from
  declared build regions, so future models/domains need no UI change).
- **Visibility:** rendered only when the selected model's capabilities
  declare ≥1 variable with a non-canonical build region. Today that is GFS
  only; AIGFS appears automatically when its rollout lands. Models without
  global show no control — zero visual change for them.
- **Default:** canonical. Selection is per-session + URL (`domain=` param,
  already sticky per Phase 2B). No cross-session persistence.
- Carries a `data-tour-target` for a future tour step (deferred polish).

## 2. Degraded state (variable without global)

Phase 2B semantics: an unsupported `domain=` silently degrades to canonical
while staying sticky in the URL. The UI must make that visible (goal 3 —
never imply what isn't true):

- The Coverage toggle **stays on `Global`** (mirrors URL stickiness), with a
  compact inline note beneath: *"Not available for this variable — showing
  North America."*
- Switching to a supported variable restores global with no further action —
  same behavior as variable-stickiness elsewhere.

## 3. Variable picker badges

When requested coverage is Global, variable rows whose
`supported_build_regions` lack `global` get a small **`NA`** chip (right-
aligned, muted). No hiding, no disabling — selection remains allowed and
degrades per §2. With Wave 1 live this marks exactly the four precip
anomalies; it is fully capabilities-driven (if baselines are ever pulled,
the affected vars re-badge automatically via the same data).

## 4. World camera preset

- Add a `world` entry to `REGION_PRESETS` (backend `config/regions.py`) —
  full-extent camera, zoom ~1.2 centered ~(30, -40).
- The existing coverage filter (`filterRegionOptionsForDataDomain`) already
  shows all presets when a non-canonical domain is active and filters by
  canonical coverage otherwise — so **World appears exactly when Global is
  active, with zero new filter logic.**
- **The camera never moves on Coverage change** (the locked invariant's
  spirit: data selection and camera stay decoupled; no auto-flyout). The
  World preset is the discoverable one-click path to the full extent.

## 5. Mobile

Coverage row reuses the same control inside the mobile SOURCE sheet, under
Model. Badges and degraded note identical. No new mobile states.

## 6. Explicitly out of scope

- Tour step for the Coverage control (post-launch polish).
- Any camera auto-movement on domain switch.
- Model-level "globe projection" — separate stretch-goal conversation.
- Share/permalink/export changes — domain threading already shipped.

## 7. Future: global by default (operator-requested end state)

The intended end state is **global as the default data domain** for
global-capable models — users just pan/zoom one worldwide dataset; the
toggle becomes an opt-down or disappears. Deferred at launch for one
reason: payload (a global frame is ~2.0 MB vs ~an order of magnitude less
for NA canonical; full-run playback ~200+ MB vs ~25 MB), unacceptable for
the majority-NA audience until a global LOD chain exists.

Implementation requirements that make the flip trivial (binding on the
launch implementation):

- **One resolution point:** the default data domain for a (model, variable)
  comes from a single function (e.g. `defaultDataDomainForSelection()` in
  app-utils), which returns `null` (canonical) today. The flip is changing
  that one function — no other call site may hardcode the default.
- The Coverage toggle, permalink absence-semantics, share/compare/feedback
  serializers, and `resolveDataDomain` all consume that function's output
  rather than assuming canonical.
- Instrument from day one (existing Mixpanel pattern): toggle flips,
  effective domain per session, global frame fetch durations — the
  flip decision is made on this data.
- **Prerequisite phase before the flip:** global LOD chain (downsampled
  tiers; existing grid-manifest LOD structure; ~+33% disk on the global
  tree). Display LOD within an artifact is sanctioned; domain identity
  still never follows the camera.
- Sequencing: launch toggle → instrument → LOD chain phase → operator
  flips the default (data-informed; possibly post-season).
- **Flip-time decision, recorded 2026-07-31 (verifier finding):** in the
  flipped world a fresh no-param load currently rewrites the URL to
  `domain=global` while an explicit mid-session global selection collapses
  to no param. Both re-resolve identically, so it round-trips — but decide
  at flip time whether the default state should stay absent in the URL
  (cosmetic-only today; the both-worlds tests pin current behavior).

## Decisions for sign-off

| # | Question | Recommendation |
|---|---|---|
| U1 | Control label | `Coverage` |
| U2 | Degraded state | Sticky `Global` + inline note (not auto-snapping the toggle back) |
| U3 | World camera preset | Coverage-gated (appears only when Global active) |

## Gate plan (after approval + implementation)

Desktop: toggle GFS NA↔Global, degrade note on a precip anomaly, badges,
World preset appears/disappears, URL round-trip, share/screenshot from a
global+World view. Mobile: same via sheet on a real phone. Non-global models:
confirm zero visual change (HRRR/MRMS rail unchanged). Then the per-model
G1–G6 checklist run, then deploy = go-live.
