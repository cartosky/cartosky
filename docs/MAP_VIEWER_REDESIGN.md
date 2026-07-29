# MAP_VIEWER_REDESIGN

**Status:** revised (v3) after repo and live-behavior verification. Ready for phase-specific execution
planning; do not implement directly from this design document.
**Scope:** `/viewer` desktop + mobile chrome, plus two prerequisite defects that block the value of any chrome work.
**Audience:** implementing agent. Brian tests each phase in production before the next proceeds.
**Date:** 2026-07-28

---

## Changes through v3

v1 was written from a black-box audit of the running site. It had not read the repository, and five
of its requirements did not survive contact with the source. Corrected here:

| v1 defect | Correction |
|---|---|
| §4.5 told the viewer to "queue a priority build" past the ready boundary, while §7.4 forbade request-fanout changes — a direct self-contradiction, and no viewer-facing endpoint exists | Removed entirely. Snap back and report. §5.5 |
| Phase 2 shrank the header to 48px while leaving the labeled selectors in place; they need the current taller header and its width | Top bar and rail merged into one phase. Phase 6 |
| Availability spec required contiguous `readyThroughFh` and per-frame building/queued state; the manifest exposes neither the expected FH schedule nor per-frame state | Add one server-computed `readyThroughFh` scalar for forecast products; keep the track two-state and define separate observed/valid semantics. §5.4 |
| Phase 0 was simultaneously "not part of this redesign" and a numbered phase; it delegated specs to three documents absent from the repo | P0-1 and P0-2 inlined in full. Single phase numbering, no Phase 0/0.5. §3, §4 |
| Phase 0.5 assumed a hardcoded-inset refactor was needed; export already captures the live canvas and reads container dimensions | Rewritten as verify-first baseline. Change code only if the test exposes a mismatch. Phase 1 |
| "One control component" implied deleting a working picker implementation | One shared *trigger primitive*; specialized panels preserved. §2.3 |
| Touch-target test used `getBoundingClientRect()` against `::after` hit expansion, which it cannot measure | Test measures the interactive element itself. §9 |
| Font-floor audit was unscoped | Scoped to viewer chrome; excludes sr-only, MapLibre labels, portals. §9 |
| §5 proposed a `legendKind` catalog; the legend already carries `kind` and ptype metadata | Inventory and normalize what exists before extending. §7 |
| Range clamp sat in the legend section of a "chrome-only" doc; it changes the WebGL color domain and GIF frame consistency | Moved to the colormap phase, where it belongs. §4 |
| URL sync during playback listed as a fix; it already pauses and flushes | Converted to a preservation test. §9 |

The v3 repo-verification pass made five additional corrections:

| v2 issue | v3 correction |
|---|---|
| Three implementation contracts remained open | Resolved against the current picker, scheduler, manifest, time-axis, and legend code. §2.3, §5.4, §7, §11 |
| A warm-site observation was presented as a verified 11.1 s cold FCP baseline, and the current loading surface was described as near-white | Recast as an unverified historical observation. Phase 2 now begins with a reproducible cold measurement; the current overlay is correctly described as dark. §3 |
| Changing a color clamp was said to invalidate cached frame textures | Corrected to update only the legend LUT/domain uniforms and repaint. Raw frame textures are independent of the color domain. §4 |
| Golden tests used live/latest weather data with zero tolerance | Replaced with fixed fixtures and synthetic crop markers. Zero tolerance applies to crop geometry and app-owned compositing, not nondeterministic browser text/WebGL pixels. §9 |
| Header, rail, and legend were one oversized phase | Split into header/rail (Phase 6) and legend normalization (Phase 7); mobile becomes Phase 8. §6–§8 |

---

## 1. How to use this document

Each phase has a **spec**, a **stop-and-verify gate**, and a **handoff prompt**. Do not proceed past a
gate until Brian has verified in production.

Read the repo before writing code. This document describes intent, constraints, and contracts. It
does not describe CartoSky's file layout or component names — where v1 guessed at those, it was
wrong. Inventory before you extend.

Before each phase, write a phase-specific execution plan under
`docs/plans/YYYY-MM-DD-<phase>-<topic>.md`. It must name the exact files, tests, red/green commands,
rollback condition, and production verification gate. Brian approves that execution plan before code
changes begin. The prompts in §13 are scoping handoffs, not implementation-ready task lists.

### Non-goals

Do not build, scaffold for, or add abstractions in preparation for:

- Merging `/compare` into the viewer as panes
- A user-composable layer stack
- Click-to-meteogram inspector
- Run-trend strip
- Units toggle (°F/°C, mph/kt)
- **Any user-triggered build, prefetch, or scheduler request**
- Any change to grid/contour data pipeline, retention, or cadence

---

## 2. Design rules (Phase 4)

### 2.1 Interactive target sizes

```
@media (pointer: coarse)  → interactive element ≥ 44 × 44 CSS px
@media (pointer: fine)    → interactive element ≥ 32 × 32 CSS px
```

**The element itself must meet the minimum**, achieved with padding. A smaller visual child (icon,
glyph) sits inside it. Pseudo-element hit expansion is not acceptable here — it is unmeasurable by
the acceptance test and unreliable across pointer types.

Known current violations: toolbar icons (32×28), zoom controls (34×34), play/speed (36×36),
Product/Variable/Run triggers (32 tall).

### 2.2 Type floor

| Use | Minimum |
|---|---|
| Any label required to operate the UI | **12 px** |
| Tabular numeric adjuncts (tick labels, opacity %, FH) | **11 px** |

No text below 11px in viewer chrome. Scope excludes screen-reader-only text, MapLibre-owned map
labels, and portals rendered outside the viewer.

### 2.3 One shared trigger primitive

Product, Variable, and Run Time are peers and must look like peers. Today Product/Variable are custom
buttons and Run Time is a shadcn `Select` trigger, which is why Run Time reads as disabled.

**Standardize the trigger/field primitive only.** The panels behind them stay as they are — Product
and Variable have specialized two-pane pickers with categories, search, and favorites; Run has a
different list and different actions. Do not delete a working panel implementation to satisfy a
visual consistency rule.

**Resolved contract:** preserve the existing Product/Variable custom-picker trigger appearance as the
viewer field contract. Extract its shared styles/behavior into a small `ViewerFieldTrigger` primitive
or equivalent shared variant. Apply that contract to the native Product/Variable buttons and the
Radix-backed Run trigger. This unifies the closed-field appearance, sizing, focus, and states without
unifying or replacing the specialized panels behind them.

### 2.4 No internal terminology in user chrome

No cache status, scheduler state, or infrastructure vocabulary.

### 2.5 Focus

Every interactive element needs a designed focus ring at ≥3:1 contrast against the dark chrome. The
current UA default (`1px auto rgb(0,95,204)`) is effectively invisible on `#04101E`.

---

## 3. First paint (Phase 2 — prerequisite)

### Verified current state

The current source mounts a full-screen, fixed, **dark** `SiteLoadingOverlay` while
`showInitialMapSkeleton` is true. A bootstrap loading surface can paint before the viewer bundle
finishes. The overlay therefore obscures and disables the whole shell, but the repo does not support
the earlier claim that the loading surface itself is near-white.

The required cold baseline was captured on 2026-07-28 using Chromium 145.0.7632.6 at 1440×900,
disabled cache, one fresh browser context per run, deterministic Fast 4G (40 ms latency, 10 Mbps
down, 5 Mbps up), and 4× CPU slowdown. The first-run tour was marked complete so onboarding did not
contaminate product-load interactivity. Five pinned runs each used GFS `20260728_12z` and HRRR
`20260728_18z`, Surface Temp, FH 12, and CONUS.

| Metric | GFS median / p95 | HRRR median / p95 |
|---|---:|---:|
| TTFB | 130 / 183 ms | 131 / 135 ms |
| Load | 461 / 659 ms | 454 / 463 ms |
| FCP | 352 / 492 ms | 340 / 360 ms |
| LCP | 1,364 / 2,372 ms | 1,344 / 1,432 ms |
| Full shell in DOM | 1,878 / 3,233 ms | 1,871 / 1,964 ms |
| Requested frame ready | 4,535 / 6,024 ms | 11,647 / 11,822 ms |
| Chrome stably interactive | 6,220 / 7,740 ms | 13,825 / 13,967 ms |

No run produced a navigation error, requested-frame timeout, page error, or console error. The early
paint sequence was dark; no white flash was observed or attributable to `SiteLoadingOverlay`.

The result narrows Phase 2: FCP already passes. The verified defect is the chain of full-screen
blockers and blanket toolbar disablement. Both requested-frame baselines exceed 3 seconds, so the
implementation must keep chrome usable and show continuous, accurate frame-stage progress; it must
not claim a speculative network-speed fix.

The local Phase 2 execution plan records the exact URLs, artifact paths, implementation boundaries,
test sequence, and stop gates. The tracked directive below remains self-contained because
`docs/plans/` is intentionally excluded from version control.

### Spec

- Paint the shell (top bar, basemap, timeline) within ~500 ms of the load event under the recorded
  baseline profile. None of it depends on grid data.
- Gate **only the map canvas**, and gate it on **the requested frame**, not the run.
- Replace the full-app blur with a dark scrim confined to the map canvas. Keep chrome interactive so
  a user who landed on the wrong model can switch without waiting.
- Surface the existing `Building n/N` counter inside that scrim.

### Gate

Baseline artifacts recorded before code changes. FCP < 1.5 s on the same cold GFS profile. Chrome
interactive before the first grid frame resolves.
Requested frame visible within 3 s, or a visible per-frame progress state explaining why not.
No white flash on a dark basemap.

---

## 4. Colormap and range clamp (Phase 3 — prerequisite)

**This is renderer and export work, not chrome.** It is in this document because it is a prerequisite,
and because v1 wrongly filed the range clamp under legend presentation.

### Measured

The `Surface Temp (°F)` ramp spans −60 → 120 °F with roughly half its length on −60 → 10 °F
(cyan → blue → purple → near-black). The 50–100 °F band — essentially all CONUS summer surface
temperature — occupies a narrow, low-luminance dark-red segment. At HRRR FH 12 on a July afternoon
the entire eastern two-thirds of the domain renders as one flat maroon; the point labels are the only
reason the map is readable. Legend tick spacing (−60, −20, 10, 50, 80, 120) is visibly non-linear, so
the bar cannot be read quantitatively even where colors do separate.

### Spec

Phase 3 starts with a renderer design spike, then implements the smallest option that passes the
visual gate:

1. **Static-ramp correction first:** improve separation and perceived lightness through the
   operational 70–95 °F band. This can satisfy the product need without new range state.
2. **Optional dynamic auto-range:** only if the static correction is insufficient, derive the
   displayed frame's 2nd–98th percentile from decoded valid values, excluding nodata, and show the
   resolved endpoints on the legend.
3. **Regime presets:** consider Summer / Winter / Full only if they outperform the corrected static
   ramp and are simpler than a trustworthy automatic range.

**Range state is renderer state, not legend state.** It sets the WebGL color domain. Therefore:

- If automatic range is implemented, manual single-frame navigation may recompute it. Starting
  playback freezes the currently resolved range until playback stops.
- GIF export resolves one domain from the full fixed export-frame set before capture and applies it to
  every frame. Compare resolves one shared domain from both panes' fixed inputs. Neither may silently
  use independent per-frame or per-pane domains.
- A range change updates the legend lookup texture and the `u_valueMin` / `u_valueMax` uniforms, then
  repaints. **Do not invalidate, re-fetch, or re-upload raw cached frame textures solely because the
  color domain changed.**
- Difference mode needs a **diverging ramp centered on zero**, not a percentile clamp. Specify
  separately or exclude difference mode from the clamp entirely.

### Gate

At a fixed-fixture HRRR FH 12 summer case, a 10 °F difference in the 70–95 °F band is visually
distinguishable without reading labels. If automatic range ships, its percentile algorithm is
deterministic for the fixture, exported GIF frames share one domain, and Compare panes share one
domain. A range-only change causes no frame-binary request and no raw frame-texture replacement.
Difference mode is unaffected or explicitly handled.

---

## 5. Timeline (Phase 5)

### 5.1 Axis: linear in valid time — product requirement

```
x = (validTime(frame) − validTime(first))
    ────────────────────────────────────── × trackWidth
    (validTime(last) − validTime(first))
```

**Position maps to valid time, never to frame index.** GFS is 3-hourly to FH 240 then 6-hourly to
FH 384 (`GFS_INITIAL_FHS`, verified 2026-07-28); HRRR is sub-hourly in places. Index-based spacing reintroduces a non-linear time axis.

Frame markers will therefore be visibly unevenly spaced where cadence changes. **That is correct and
must not be normalized away.**

Equal time intervals get equal width, end to end. Labels may be omitted at narrow widths; the scale
may not change. No ellipsis, no compression, no fisheye.

### 5.2 Tick and label density

Computed from available width. ~89 px/day for a 15-day run at ~1340 px.

| Width per day | Day labels | Hour ticks | Hour labels |
|---|---|---|---|
| ≥ 80 px | full (`Wed 7/29`) | 6-hourly | 00Z, 12Z |
| 48–80 px | short (`Wed 29`) | 6-hourly | 00Z only |
| 24–48 px | day number | 12-hourly | none |
| < 24 px | alternate days | daily | none |

Night shading (local solar time at map center) renders behind the scrub track, not in the label row.

### 5.3 Playback

Steps frame-to-frame through available frames in valid-time order. Constant wall-clock dwell per
frame — a 3-hourly stretch therefore advances valid time 3× faster, which is standard and matches how
forecasters read a loop. Skips gaps. A forecast stops at `readyThroughFh`; observed and valid products
stop at their last available timestamp or period.

### 5.4 Availability vocabulary — by time-axis mode

The viewer has three time-axis modes: `forecast`, `observed`, and `valid`. They do not share one
publishing metaphor. The timeline adapter must expose a mode-specific availability view model rather
than deriving all three from maximum frame hour.

#### Forecast

The normal scheduler path advances per variable in forecast-hour order, but that is not a universal
manifest contract: other publication and rebuild paths may complete concurrently, and the manifest
sorts whatever frames exist without asserting a contiguous prefix. **Maximum published FH is
therefore not a truthful ready boundary.**

Add one backend scalar per forecast variable:

```
readyThroughFh: number | null
```

The publisher computes it from that variable's authoritative expected target-hour list: the greatest
scheduled FH for which every scheduled frame from the start through that FH is published. A cadence
change such as GFS 240 → 246 is not a hole because 241–245 are not scheduled targets. `null`
means no contiguous frame is ready. The frontend must consume this scalar; it must not reconstruct it
from `max(frame.fh)`.

| Concept | Source | UI string |
|---|---|---|
| Run horizon | maximum expected FH | `Run horizon FH 360` |
| Publication progress | published frame count / expected frame count | `248 of 273 frames published` |
| Ready boundary | `readyThroughFh` | `Ready through FH 248` |

Track rendering is solid through `readyThroughFh` and hatched after it. **Two states only** — do not
render a building-vs-queued distinction without per-frame publisher state. Never use an hour value as
a frame count, and never render `hours available` unless it literally describes a contiguous duration.

An `expectedFrameHours: number[]` manifest addition remains a possible later enhancement for explicit
hole rendering. Per-frame `state: 'published' | 'building' | 'queued'` is a separate backend feature,
not part of this redesign.

#### Observed

Observed products expose a retained timestamp window, not a forecast horizon or future queue. Show
the latest observation timestamp, its age/freshness, and the retained-window start when available.
Render only existing timestamps. Do not show hatching, `queued`, `publishing`, a future ghost handle,
or `ready through FH`.

#### Valid

Valid/outlook products expose discrete issued/valid periods. Show the selected valid period and issue
time using product metadata. Render only published periods; do not infer forecast-run progress or use
forecast-hour vocabulary.

### 5.5 Forecast scrubbing past the ready boundary

The committed handle must never sit beyond the ready boundary while the map shows a rendered frame —
that is a silent lie about what is on screen.

**While dragging past the boundary:** handle renders as an amber ghost outline, not the solid
committed handle. Map stays on the last ready frame. Readout shows target time plus
`not published yet`.

**On release past the boundary:** snap the committed selection back to the ready boundary. Show a
brief inline note that the target is not published.

**Do not request a build.** No priority queue, no prefetch, no fetch for the unavailable frame. A
user-triggered build is an unauthenticated amplification vector against a 32 GB / 2 TB server — one
user scrubbing across five models could trigger hundreds of builds. If this is ever wanted it needs
its own design as an authenticated, rate-limited, explicitly-scoped backend feature.

**Keyboard and step buttons clamp at the ready boundary.**

For observed and valid modes, pointer, keyboard, and step controls clamp to the first/last available
timestamp or valid period. They never enter a synthetic unpublished region.

### 5.6 Keyboard

Global handlers at app level, suppressed inside text inputs and modals. **Suppress MapLibre's
`keyboard` handler for arrow keys** — panning moves to drag and WASD.

| Key | Action |
|---|---|
| ← / → | ±1 frame |
| Shift + ← / → | ±6 h |
| Alt + ← / → | ±24 h |
| Home / End | first frame / ready boundary |
| Space | play / pause |
| `[` / `]` | previous / next run |
| `?` | shortcut sheet |

Currently arrows pan the map (measured: lon −97.000 → −103.124, FH unchanged) and only step FH after
the slider is clicked. Add the shortcut sheet as a step in the existing onboarding tour.

### 5.7 Accessibility

The slider has `aria-valuemin/max/now` but no name and no text value.

- Mode-specific name: `Forecast time`, `Observation time`, or `Valid period`
- `aria-valuetext="FH 12, Wednesday July 29, 9:00 PM CDT"`, updated on change
- `aria-describedby` → availability state

### 5.8 Heights

Standard **88 px** (day band 20 + ticks 12 + track 26 + transport 30). Compact **64 px** (track +
transport; day bands on hover/scrub). User-toggled, persisted. Mobile uses compact at rest.

### Gate

Axis-linearity test passes on GFS across the cadence change (§9). The forecast boundary comes from
`readyThroughFh`, including a fixture with an out-of-order published frame beyond a gap. Observed and
valid fixtures show no forecast publishing vocabulary. A committed forecast handle never sits past
the ready boundary. No network request fires for an unpublished frame. Arrows step from a cold load
with no prior click. Export regression passes.

---

## 6. Chrome — top bar and rail (Phase 6)

**Top bar and rail ship together.** The selectors cannot stay in a shrunk header — the current labeled
Product/Variable/Run controls need the existing header height and width. Shrinking the bar while
leaving them in place is not a shippable intermediate state, and a temporary second-row layout is
throwaway work with its own acceptance criteria.

```
┌──────────────────────────────────────────────────────────────┐
│ TOP BAR 48px                                                 │
│ logo │ Viewer Forecast Climate     ⌘K  [Share]  [•••]  [BA]  │
├───────────┬──────────────────────────────────────────────────┤
│ RAIL      │                    MAP                           │
│ 288 / 72  │        [compact legend chip after Phase 7]        │
├───────────┴──────────────────────────────────────────────────┤
│ TIME RAIL  (Phase 5)                                         │
└──────────────────────────────────────────────────────────────┘
```

### 6.1 Top bar (48 px)

- **Left:** logo → `/`, then `Viewer · Forecast · Climate` segmented switcher.
- **Right:** `⌘K Jump to…`, **labeled `Share`**, `•••` overflow, account.
- **Overflow:** Send feedback, Replay tour, Keyboard shortcuts, attribution, sign in.
- Center stays empty.

Share is high-frequency and is the product's distribution mechanism — it earns a label. Feedback is
episodic — overflow.

### 6.2 Rail expanded (288 px)

Section order is normative and mirrored on mobile.

**SOURCE** — Model, Variable, Run stepper (`◀ 14Z Jul 28 ▶`), freshness dot + `Updated 12 min ago`,
availability line per §5.4. Arbitrary run selection stays available on the run label.

**VIEW** — Region (**current value visible as text**, not behind a glyph), NWS Warnings toggle (when
the product supports it), City labels, Dark basemap, Opacity.

Phase 6 preserves the current legend behavior and reserves a stable bottom mount for Phase 7. It does
not normalize, restyle, or relocate legend content in the same change that restructures navigation.

### 6.3 Rail collapsed (72 px)

Icon + **11 px caption** for `Source` and `View`. Phase 7 adds `Legend`. Icon-only rails are the
problem this redesign exists to fix. Clicking a caption expands scrolled to that section.

Until Phase 7 lands, preserve the current map legend when the rail collapses. Phase 7 replaces that
intermediate behavior with the product-aware map-corner chip in §7.2.

### 6.4 Responsive rule — from map width, not viewport

```
availableMapWidth = viewportWidth − 288 − mapInsets
expanded  if availableMapWidth ≥ 1024
collapsed otherwise
viewportWidth < 768 → mobile (§8, Phase 8)
```

User override persists in `localStorage`, keyed by breakpoint class so a laptop preference does not
follow the user to an external monitor. 1400 px is not a magic number.

### Gate

Forecast/Climate/account reachable from the viewer. Share labeled. Feedback in overflow. Region
value visible without opening anything. Rail default computed from map width. Current legend remains
available in both rail states. Export regression passes.

---

## 7. Legend system (Phase 7)

### 7.1 Inventory first

The current catalog and frontend use four raw legend kinds:

| Raw kind | Full rendering |
|---|---|
| `continuous` | gradient plus numeric ticks and units |
| `discrete` | threshold/swatches plus numeric break values when the categories encode numeric ranges |
| `categorical` | named nominal swatches; numeric values only when the metadata defines a numeric threshold |
| `indexed` | indexed swatches/labels, with values when semantically meaningful |

Precipitation-type intensity, radar precipitation type, and composite-layer grouping are **derived
frontend presentation modes**, selected from the raw kind plus existing `id`, `ptype_breaks`,
`ptype_order`, and layer metadata. Do not add `categorical-multi` or `composite` as new backend kinds
unless a later inventory proves that the existing metadata cannot express a real product.

Numeric truth is normative where the data is numeric. The current precipitation-type legend
advertises `(dBZ)` but renders only `LIGHT → HEAVY`; show its shared numeric dBZ break scale. Purely
nominal categories such as hazard names do not invent numeric values.

### 7.2 Compact rendering (collapsed rail, mobile chip)

Product-aware. **Do not show a "dominant ramp + N more"** — on a mixed-precipitation map the snow ramp
is frequently the one that matters.

| Presentation mode | Compact form |
|---|---|
| continuous | mini gradient + min/max endpoints + units |
| ptype-intensity / radar-ptype | **all** applicable named ramps, with the shared numeric break scale once when defined; tap to expand |
| discrete / categorical / indexed | labeled swatches or a truthful summary count expanding to the full list |
| composite group | one row per component, labeled by layer |

Under width pressure on a precipitation-type presentation, drop the repeated break labels before
dropping a precipitation-type ramp.

### 7.3 Export interaction

The export renderer bakes its own legend. **The compact map-corner chip must be explicitly hidden from
the export composition**, or exports ship two legends. Verify this with the fixed Phase 1 fixture; do
not rely only on the chip's normal screen position being outside a crop.

### Gate

Fixtures cover all four raw kinds and the precipitation-type, radar-ptype, and composite-group derived
modes. Numeric thresholds appear wherever metadata defines them; nominal categories do not invent
numbers. Every applicable precipitation-type ramp remains reachable in compact mode. The fixed export
fixture contains exactly one baked legend and no compact chip.

---

## 8. Mobile (Phase 8)

Three states. Information stays available; density appears only when asked for.

### State A — at rest

```
TOP BAR      52px    logo │ ⌕  [Share]  •••
MAP          flex    ≥ 72% of viewport height
                     [badge: HRRR · Composite Reflectivity]
                     [compact legend chip]
TIMELINE     64px    [▶]  ●━━━━━━━  Mon Aug 10 / 7 PM · FH 360
SHEET PEEK   84px    00Z 7/27 · 18 min ago · ready through FH 248   [◀][▶]
```

390×844 → 644 px map = **76%**. Treat 72% as the floor; shrink the sheet peek first, then the timeline.

**No duplication.** The map badge owns source identity. The sheet peek owns run state only.

### State B — while scrubbing

On `pointerdown` on the track; reverts on `pointerup` + 400 ms.

- Day strip fades in.
- Target readout appears over the **bottom-center of the map**, above the timeline — the user's thumb
  covers the bottom of the screen, so a readout below the track cannot be read while it is being set.

**No layout reflow during drag.** The day strip is an absolutely-positioned overlay, not an inserted
flex row. Resizing the map mid-gesture triggers a MapLibre resize and a visible jump under the finger.

### State C — sheet expanded

Snap points peek (84) → half (~50vh) → full (62vh). Map clamps to ≥180 px and stays visible. Timeline
hides; its valid time moves to the sheet header. Sections in rail order: Source → View → Legend. All
controls ≥44 px.

### Gate

Map ≥72% at rest on 390×844. Map element height unchanged through the drag gesture. All targets
≥44 px. Export regression passes.

---

## 9. Acceptance tests

**Export baseline (Phase 1, re-run every phase)**
```
Use fixed, repo-owned manifests/frame fixtures for MRMS reflectivity CONUS, HRRR ptype CONUS,
GFS tmp2m CONUS, and one short GIF. Never point a golden test at latest/live weather.

Add a synthetic canvas fixture with distinct one-pixel corner/edge markers. Assert exact decoded RGBA
for those marker regions and exact crop dimensions; this is the zero-tolerance crop-geometry test.

For app-owned compositing with fixed raster inputs, require exact decoded output. If a test includes
browser-rendered text or WebGL, pin browser/renderer settings and declare a small reviewed tolerance
instead of claiming nondeterministic pixels are exact.

Assert the compact legend chip is absent from exported images from Phase 7 onward.
```

**Axis linearity (Phase 5) — the important one**
```
For any two frames A, B in a run:
  |x(B) − x(A)| / |validTime(B) − validTime(A)|  constant within 1 px across the whole track.
Test explicitly on GFS across the 3-hourly → 6-hourly transition at FH 240 → 246.
```

**Availability truthfulness (Phase 5)**
```
No rendered string contains "hours available" unless published === total.
Committed handle x ≤ x(readyBoundary) at all times.
No network request fires for a frame beyond the ready boundary — assert via request interception.
Fixture: published FHs include a later frame beyond a missing scheduled target.
Assert readyBoundary equals manifest readyThroughFh, not max published FH.
Observed and valid fixtures contain no forecast publishing or queued vocabulary.
```

**Touch targets (Phase 4, re-run each phase)**
```
Under (pointer: coarse): every button, a[href], [role=button], [role=slider], input, select
has getBoundingClientRect() ≥ 44×44 on the interactive element itself.
Padding counts. Pseudo-element hit expansion does not — do not rely on it.
```

**Type floor (Phase 4)**
```
Within the viewer chrome subtree only:
no computed font-size < 11px on any element with non-whitespace text.
Exclude: [class*=sr-only], [aria-hidden=true], .maplibregl-* , portals outside the viewer root.
```

**Mobile geometry (Phase 8)**
```
390×844: map height / viewport height ≥ 0.72 at rest.
Through pointerdown → pointerup on the track: map element height unchanged.
```

**Preservation tests (assert unchanged, all phases)**
```
URL params m, r, v, fh, reg, lat, lon, z unchanged in name and semantics.
/compare params lm, lv, lr, rm, rv, rr, fh, lat, lon, z unchanged.
URL sync continues to pause during playback and flush afterward — this is existing correct
behavior, not a bug to fix.
Grid binaries return CF-Cache-Status: HIT. Contour rules at /contours/ unchanged.
Readiness gate (MapLibre idle + onGridFrameReady) unchanged.
```

---

## 10. Phases

| # | Scope | Gate |
|---|---|---|
| **1** | **Export baseline.** Verify how the renderer derives crop bounds today. Add deterministic crop/content regression coverage. Change code **only if** the test exposes a mismatch. | Baseline captured; regression suite green on unchanged layout |
| **2** | First paint (§3) | Reproducible cold baseline captured; FCP < 1.5 s on the same profile; chrome interactive before first frame; no white flash |
| **3** | Colormap + range clamp (§4) — renderer + export | 10 °F separation visible in 70–95 °F band; GIF frames and Compare panes share one domain |
| **4** | Design tokens (§2) | Target and type audits pass on the scoped subtree |
| **5** | Timeline (§5), including the `readyThroughFh` manifest contract | Axis linearity on GFS; all three time-axis modes truthful; no fetch past forecast boundary; arrows step cold |
| **6** | Top bar + SOURCE/VIEW rail (§6) | Nav reachable; region visible; rail default from map width; existing legend preserved |
| **7** | Legend normalization and compact chip (§7) | Raw kinds and derived modes render truthfully; numeric thresholds where defined; chip excluded from export |
| **8** | Mobile three states (§8) | Map ≥72% at rest; no reflow during drag; targets ≥44 px |

Before each row begins, create and approve its phase-specific execution plan as required by §1. Do
not combine Phase 6 and Phase 7 into one implementation branch or production gate.

**Phase 1 completed 2026-07-28.** The fixed-fixture baseline covers exact horizontal, vertical, and
no-crop geometry; default normalized dimensions; and a pinned two-frame GIF. Real Viewer → Share and
`useGifExport.generate` integrations cover the production capture wiring, live map-container aspect,
four exact export edges, GIF structure, frame count, and chronological frame order. The focused gate
passes 9/9 and the frontend build passes. No runtime export or readiness-gate code changed.

**Phase 2 implemented 2026-07-28 (production gate pending).** A route-aware static viewer shell in
`index.html` paints the header/map/timeline shape for `/viewer` before the React bundle; every other
route keeps the generic boot card and `SiteLoadingOverlay`. The viewer's full-screen
`startSiteLoading` overlay is replaced by `ViewerInitialMapScrim`, confined to the map stacking
context (above tooltips/notices, below timeline and header) and driven by the unchanged
requested-frame gate (`loading || !isMapReady || shouldWaitForInitialGridFrame`). Product is
data-gated (enabled once model options exist) on desktop and mobile surfaces; Variable, Statistic,
Run, and Region keep their existing contracts. The scrim shows truthful stages (`Loading model
data` → `Preparing map` → `Downloading and drawing FH n`) plus the timeline's `Building
available/total hrs` counter via a shared `resolveRunBuildProgress` helper consumed by both
surfaces. Contract suite `tests/e2e/viewer-first-paint.spec.ts` (3/3, deferred-binary fixtures)
proves Product opens while the frame is blocked, only the map center hit-tests to the scrim, and
`grid_frame_ready` precedes scrim removal. Phase 1 export regression re-run green (9/9);
`firstWeatherFramePainted` / `onGridFrameReady` / MapLibre idle / screenshot readiness untouched.
Local built-bundle probe under the recorded Fast 4G / 4× CPU profile paints the shell at 236–264 ms
FCP. Production FCP re-measured 2026-07-28 (pre-deploy prod build): GFS 364 ms / HRRR 348 ms median.
Phase 2 production gate passed 2026-07-28.

**Phase 3 completed 2026-07-28 — production gate passed.** Brian verified all §4 gates in
production against a freshly published GFS run (10 °F separation in the 70–95 °F band, dew point
unchanged, exports and Compare consistent). Static ramp only; auto-range was not needed and
remains unauthorized without its own plan. The approved Candidate A spectral warm band replaces
`TMP2M_F_COLOR_ANCHORS` (cold end −60→49 °F unchanged); dew point is decoupled onto a verbatim
copy (`DP2M_F_COLOR_ANCHORS`) and pinned by test. Perceptual gate
`backend/tests/test_tmp2m_ramp_gate.py` enforces sliding-min ΔE ≥ 18 across every 10 °F pair in
70–95 (old ramp failed at 13.4, 70v80; new minimum 23.6) plus an L* span ≥ 20 floor and a
frontend-fixture cross-pin. `buildLegendLut` was extracted byte-identically into exported
`src/lib/grid-lut.ts`. `tests/e2e/viewer-colormap.spec.ts` proves: exact LUT faithfulness through
the exported seam; distinct band rendering on the live map; the **real Share export** (dialog →
composed preview) carrying the same cells plus baked-legend probe colors that cannot come from map
data; both **Compare pane canvases** sampled pixel-for-pixel distinct and agreeing cell-for-cell;
and an **exact fetch pin** — precisely one grid-binary request per fixture frame, held through a
pixel-confirmed FH1 render (each fixture frame shifts +2 °F so the step is observable). Sidecars
bake legends at publish, so the new ramp appears only on runs built after deploy; old runs age out
on the old ramp (tmp850_anom precedent). Export regression and Phase 2 suites green; no
fetch/cache, exporter-legend, diff-mode, or readiness-gate code changed. Older runs published
before the deploy retain the previous ramp until retention ages them out — expected, not a defect.

**Phase 4 completed 2026-07-28 — production gate passed** (after one follow-up round: slider
thumb reverted, see below; captions/icons accepted until Phase 6). Tokens: a `pointer-coarse`
Tailwind variant (§2.1 sizing: 32px fine / 44px coarse floors applied across header icons,
triggers, picker internals, zoom, play/speed, slider thumb, Share dialog, display panel,
attribution chips); one designed `:focus-visible` outline token (2px cyan, ~10:1 on the dark
chrome, `--ring` recolored to match) applied globally with per-component focus killers removed
from the audited chrome; the viewer-chrome type floor swept (no text <11px; operational labels
≥12px; field captions tightened tracking, not font); the §11.1 `viewerFieldTriggerClassName`
primitive consumed by Product, Variable, Statistic, and Run so the four read as one field family;
and a measured `--viewer-header-extra` contract (ResizeObserver) so a coarse-pointer wrapped
header moves the map padding, scrim, zoom stack, and panels together — unwrapped layouts are
pixel-identical to before. Audit suite `tests/e2e/viewer-design-tokens.spec.ts` (10 tests, stable
2×): 9-surface interaction matrix plus a touchscreen-desktop state, both pointer regimes,
identity-keyed exhaustive Tab-walk focus audit, trigger-family computed-style equality,
wrapped-header geometry at tablet sizes, compare coarse smoke, mobile-layout pass, and a
slider-dot geometry pin (the enlarged thumb hit area provably does not move the visual dot).
Verified through two adversarial review rounds; export regression, first-paint, colormap, and
compare suites green. Known scope note: tablet-touch has no display-settings surface (the panel
is desktop-only and the controls button is hidden there) — recorded for Phases 6/8.

**Phase 5: implementation complete 2026-07-28 — pending verification.** Backend: additive
per-variable `ready_through_fh` + `expected_max_fh` scalars baked in `_write_run_manifest` from
the authoritative expected-hour list (7 tests incl. the out-of-order-publisher fixture where
`max(frame.fh)` gives the provably wrong boundary; keys absent for observed/valid modes; old
runs lack the fields until aged out — the frontend falls back to solid-only rendering, never
reconstructing from max FH). Frontend: a purpose-built valid-time `TimelineTrack` (per-marker
linearity ≤1 px across the real GFS 240→246 cadence change; solid→boundary, hatched→horizon
with beyond-boundary published frames shown dimmed and uncommittable; §5.2 dated ticks; 88/64 px
density toggle persisted at `twf.timeline.density`; single focusable thumb meeting 32/44
natively — the Phase 4 `[role=slider]` exception is closed; amber ghost + snap-back past the
boundary); ONE identity-keyed eligible-frame list clamps every commit/fetch path including the
cold-deep-link seam (race-probed: a 4 s manifest delay leaks nothing); MapLibre keyboard off
with the full §5.6 map reimplemented in `useViewerKeyboard` (interactive-focus/IME/modifier
deference, directional Shift/Alt snapping, `[`/`]` run stepping, `?` shortcut sheet + tour
step); mode-truthful availability adapters; slider a11y (name/valuetext/describedby). Deviations
from plan, all reviewed: eligibility enforced at one manifest-level seam instead of a second
grid-webgl guard (verified stronger); the display-panel opacity slider keeps its stock 16 px
thumb under a narrowly-scoped audit exception owned by Phase 6 (enlarging Radix thumbs re-treads
the Phase 4 regressions); desktop timeline panel widened 45rem→min(96vw,90rem) per §5.2's width
assumptions; buffered frames render as brightened markers (index-space fill couldn't survive
valid-time positioning); a genuine pre-hydration URL-sync drop was fixed and permalink floats no
longer emit trailing zeros. Contract suite: 16 tests (verification round added tick-rendering,
exact play/pause labels, and the longer→shorter run-switch scenario after refuting the first
pass). The deploy needs scheduler AND API restarts; the production window opens with the first
run baked after deploy.

**Phase 6: implementation complete 2026-07-29 — pending production verification.** The 48 px top
bar (logo → `/`, `Viewer · Forecast · Climate` switcher, labeled `Share`, `•••` overflow with
Send feedback / Compare / Replay tour / Keyboard shortcuts / Attribution / sign-in, Clerk
account affordance; center empty) and the 288/72 px SOURCE/VIEW rail landed together per
`docs/plans/2026-07-29-map-viewer-redesign-phase-6-chrome.md` (see its resolved scope decisions:
⌘K deferred with no placeholder, Statistic in SOURCE, Compare in overflow, zoom-controls toggle
kept in VIEW, rail-level collapse only). State model `src/lib/viewer-rail.ts`: default from
`availableMapWidth = viewportWidth − 288 − 0 ≥ 1024`, override persisted per breakpoint class at
`twf.rail.mode.wide|narrow`, written only by user toggles — the tour's forced expansion passes
`persist: false` (a fresh-context verification round caught both a tour↔rail re-render/scroll
loop, fixed by depending on the stable `expandTo` callback instead of the rail memo object, and
an under-budgeted geometry poll in the new spec). Geometry seam: `--viewer-topbar-height` +
`--viewer-rail-width` root variables whose defaults reproduce the old literals, so `/compare`
and other routes are pixel-unchanged; the map slot starts right of the rail; the timeline
centers within the map area (the one intentional Phase 5 contract re-point, re-based in
`viewer-timeline.spec.ts`). Mobile threshold moved 639 → 767 (width-only, mirrored in the
`index.html` boot shell, which paints bar + rail pre-React from the same inlined constants and
override keys); the mobile sheet itself is untouched; tablet-touch gains the rail and with it
the previously missing display-settings surface. Legend: expanded rail hosts the existing
`MapLegend` inline in the reserved `data-legend-mount="rail"` Phase 7 mount; a collapsed rail
keeps it available as the floating map overlay (desktop gains no hide toggle until Phase 7 —
recorded). Contract suite `tests/e2e/viewer-chrome.spec.ts` (8 tests, red-first) covers the bar,
both rail states, §6.4 defaults from map width at five viewports, class-keyed override
independence, zero grid-binary requests on rail toggle, and §5.4 truthfulness in the rail; the
Phase 4 audit matrix was re-pointed to 11 surfaces (adds collapsed rail, rail VIEW, overflow
menu, attribution dialog) with its exhaustive focus/target/type walks intact, catching three
coarse-pointer sizing fixes (both logo links, region field trigger). Export baseline suite
passed byte-untouched; first-paint, colormap, timeline, compare, and grid-smoke suites green
(the first-paint Product-while-blocked test now pins a 1440×900 viewport; three grid-smoke
readiness selectors re-pointed). No fetch, readiness-gate, permalink-sync, or backend changes.

**Phase 7: implementation complete 2026-07-29 — pending production verification.** One pure
model (`src/lib/legend-model.ts`) now derives every legend rendering per
`docs/plans/2026-07-29-map-viewer-redesign-phase-7-legend.md`; `map-legend.tsx` is
rendering-only; no backend changes, no new kinds (§11.3). The three derived modes come from
existing metadata: ptype-intensity and radar-ptype from `id` + `ptype_order`/`ptype_breaks`
(with a ≥2-ramp guard on the legacy zero-delimiter fallback — a fresh-context verification
round caught single-type HRRR/NAM `radar_ptype_*` continuous ladders being fabricated into a
"Rain 1.2–75 dBZ" ramp; they now fall through to continuous, fixture-pinned), and
composite-group from the per-layer legends the App seam already holds (no new fetches).
**Numeric truth:** the §7.1 shared scale renders exactly once iff per-type ladders are
identical; real MRMS ladders diverge (rain 5–70 dBZ, others 0–60) so real products render
truthful per-ramp endpoints — the `(dBZ)`/`(in/hr)` titles are backed by rendered numbers
either way; nominal categoricals invent nothing; bare 0..n indexed codes are not presented as
measurements; continuous endpoint ticks no longer round past the metadata range (a 0–75
ladder no longer advertises "80" — interior ticks may still round). The §7.2 compact chip
(`CompactLegendChip.tsx`) replaces the Phase 6 interim floating legend in the collapsed-rail
state: all applicable ramps always (never "dominant + N more"), width pressure drops repeated
labels before ramps, tap expands in place to the full body (rendered directly — no nested
duplicate title/landmark), `data-export-exclude="legend-chip"`. Collapsed rail is now
Source/View/**Legend** (§6.3 closed); the Legend item and the expanded-rail legend mount work
independently of `twf.map.legend_visible`, so touch-tablet users (where the chip pref
defaults off) keep a one-tap path to the full legend. Contract suite
`tests/e2e/viewer-legend.spec.ts` (18 tests, red-first) covers all four raw kinds, all three
derived modes, the fabrication regression, chip behavior under both pointer regimes, and an
export-exclusion proof that decodes the real composed Share PNG (chip visible over the map,
probe anchors unreachable from fixture data, centre-crop remapping — not a screen-position
argument). Audit matrix 11 → 13 surfaces (chip + expanded chip). Exporter, GIF, LUT,
permalink, fetch, and readiness paths untouched; `share-export-baseline.spec.ts` passed
byte-untouched; chrome, first-paint, colormap, timeline, compare, and grid-smoke suites
green. Known interim quirks, recorded: `twf.legend.collapsed` now governs only `/compare`
and the mobile popover; the exporter keeps its own duplicated ptype rendering helpers
(deliberate — export pixels must not change; candidate post-redesign cleanup).

**Phase 8: implementation complete 2026-07-29 — pending production verification. Final phase.**
Mobile (<768) restructured into the three §8 states per
`docs/plans/2026-07-29-map-viewer-redesign-phase-8-mobile.md`. **State A:** 52 px bar (logo, ⌕
location search, labeled Share, `•••` with Send feedback/Compare/Replay tour/Attribution/
sign-in — no keyboard-shortcuts item on touch, the recorded §6.1 divergence); map element
truly 644 px = 76.3% at 390×844 via constant `--viewer-map-top/bottom-inset` vars (unset → 0
off-mobile; desktop/tablet pixel-unchanged, proven); `MapSourceBadge` owns `MODEL · Variable`;
compact chip mounted (position variant only); 64 px single-row timeline (`TimelineTrack
singleRow`, density locked, tick marks not labels; speed moved to the sheet's Source section
per the 44 px audit); 84 px persistent peek owning run state only — run label + compact
freshness + the full §5.4 availability line on its own line (verified unclipped at 390; the
old "X/Y hrs available" strip is deleted). The 72% floor uses a floored clamp (rounding
overspend fixed; numerically swept H=480-1200) and documents the exact hard-minimum collision:
bar 52 + peek 64 + timeline 48 = 164 → the floor is reachable only for viewports ≥ ~586 px,
below which minimum usable control sizes win. **State B:** day strip + bottom-center-of-map
target readout as absolute overlays, gone 400 ms after pointerup; the map box is bit-identical
through the gesture by construction (one clip bug found by screenshot — `overflow-x-hidden`
computing `overflow-y: auto` silently swallowed both overlays while rect assertions passed;
the suite now asserts IntersectionObserver visibility). **State C:** snaps peek 84 / half
50vh-equivalent / full 62vh-equivalent computed from `innerHeight` (no vh/dvh — safer under
iOS dynamic toolbars, flagged for real-device eyeballing); never "closed"; ≥180 px map above
the full sheet; timeline hides via visibility with its valid time in the sheet header;
sections Source → View → Legend (tabs retired; Legend = full inline legend, replacing the
mobile popover). Tour rebuilt for the new chrome and proven completable end-to-end (a
verification round caught below-the-fold section steps wedging the tour — fixed via
`requestMobileSheetSection` + TourOverlay re-measure-after-scroll + capture-phase scroll
listener; bottom offset now derives from the 148 px stack). Two verification rounds ran:
round 1 REFUTED on six defects (untracked contract suite via the `.gitignore` deny-list, the
tour wedge, the 130 px tooltip offset, a 4 px boot/fallback header seam at 56-vs-52, the
short-viewport floor, unreadable availability); all six fixed and independently CONFIRMED
(production-build seam probe: boot 52 = React 52 = slot 52; real-touchscreen tour walk; clamp
sweep). Contract suite `tests/e2e/viewer-mobile.spec.ts` (19 tests, red-first, git-tracked)
covers §9 geometry at 844/660/568, the no-reflow gesture contract, all three states, no
duplication, §5.4 truthfulness + readability, the tour walk, and the boot-handoff seam. Scrub
emit contract byte-equivalent; no fetch/readiness/permalink/exporter/backend changes;
`share-export-baseline` byte-untouched and green; all suites green (known pre-existing
grid-smoke glass-surface failure predates this phase — its regex asserts a stale 0.88 alpha
vs the 0.93 token on main). Known interim: tablet-touch (≥768) legend popover dead-end from
Phase 6 left as-is; slider thumb at 100% slightly overlaps the readout's first character
(cosmetic).

Production-gate follow-up (Phase 4, 2026-07-28): the slider thumb hit-area enlargement caused two visual
regressions (dot trailing the finger at range max on mobile; dot below the track centerline on
desktop) because the hit box proved inseparable from Radix's wrapper positioning math. The thumb
shipped reverted to its exact pre-Phase-4 form and **[role="slider"] carries a documented
exception to the §2.1 size floors, owned by the Phase 5 timeline rebuild** (the audit records the
exception inline). The 12 px field captions and 32 px icon boxes are §2-compliant and accepted
as-is until Phase 6 restructures the header; no further density tuning before then.

---

## 11. Resolved repository decisions

### 11.1 Viewer trigger

Product and Variable already share the intended native-button appearance; Run uses a separately
styled Radix `SelectTrigger`. Preserve the Product/Variable appearance as the viewer contract and
adapt the Run trigger to it through a shared primitive or variant. Preserve all specialized panels.

### 11.2 Forecast publication boundary

The normal per-variable scheduler path is ordered, but ordering is not guaranteed by every
publication/rebuild path or by the manifest contract. Maximum published FH is not safe as a
contiguous boundary. Phase 5 therefore includes the small backend `readyThroughFh` manifest addition
defined in §5.4.

### 11.3 Legend taxonomy

Current raw kinds are `continuous`, `discrete`, `categorical`, and `indexed`. Precipitation-type and
composite layouts are derived frontend modes based on existing metadata. Phase 7 normalizes those
paths without introducing a second backend taxonomy.

---

## 12. Constraints

**Export pipeline.** Breaking screenshot, share, or export is never acceptable. Phase 1 establishes
the baseline; every later phase re-runs it and does not merge on a diff. The readiness gate
(MapLibre `idle` + `onGridFrameReady`) is unchanged.

**React effect dependency arrays.** When diagnosing fetch/abort/render bugs, audit `isLoaded`,
`basemapMode`, `isAnimating` in dep arrays before proposing other fixes. Phases 5 and 6 both change
what owns map state.

**Deploy.** Mac → `git push` → `sudo git pull` on server → rebuild/restart. Never edit on the server.

**Resource envelope.** Phases 4 and 6–8 are presentation/control-surface work and must not change disk,
RAM, or request fan-out. Phase 5's only backend scope is the `readyThroughFh` manifest scalar; it does
not change build cadence or scheduling. Phase 3 may change the color LUT and range uniforms, but a
range-only change must not invalidate raw frame textures. Flag memory impact if the eviction policy
changes. If any phase introduces additional concurrent grid fetches, **stop and flag it.**

**Cloudflare.** Grid binaries must return `CF-Cache-Status: HIT`; `DYNAMIC` on a binary is a bug.
Contour GeoJSON needs matching rules at `/contours/`. Neither changes here.

---

## 13. Handoff prompts

One per phase. Do not run more than one at a time. Every prompt starts with the phase-specific
execution-plan gate in §1; approving this design document alone does not authorize code changes.

**Phase 1 — export baseline**
> Establish an export regression baseline for the CartoSky viewer. First, read the share/export path
> and report back how it actually derives the crop region and dimensions today — do not change
> anything yet. Then write the phase-specific execution plan required by §1. Use fixed repo-owned
> frame/manifest fixtures for MRMS reflectivity CONUS, HRRR ptype CONUS, GFS tmp2m CONUS, and one GIF
> export — never latest/live weather. Add a synthetic canvas with distinct corner/edge markers and
> require exact decoded RGBA plus exact crop dimensions for those markers. If browser text or WebGL is
> included, pin the renderer and document a reviewed tolerance. Only if the tests expose a mismatch
> between the crop region and rendered map container should you change product code. Leave the
> readiness gate (MapLibre `idle` + `onGridFrameReady`) alone.

**Phase 2 — first paint**
> **Baseline and execution plan complete; implementation not started.** First add a fixed-fixture
> blocked-frame test that proves the Product selector remains usable while the map is gated. Paint
> the route-aware shell within ~500 ms of load under the recorded profile, gate only the map canvas
> on the requested frame, replace the full-app dark loading overlay with a dark scrim confined to the
> canvas, and surface continuous frame-stage progress plus the existing `Building n/N` counter when
> the run is incomplete. Preserve the requested-frame readiness gate, keep other routes' loading
> overlays intact, and re-run the fixed Phase 1 export regression.

**Phase 3 — colormap**
> Write the Phase 3 execution plan, then implement MAP_VIEWER_REDESIGN §4. Start with a corrected
> static temperature ramp that separates the 70–95 °F operational band. Add a 2nd–98th percentile
> auto-range only if the fixed-fixture visual gate still fails. If auto-range ships, exclude nodata,
> show resolved endpoints, freeze one domain for playback, resolve one domain over the full GIF frame
> set, and share one domain across Compare panes. A range change updates the legend LUT and
> `u_valueMin` / `u_valueMax`, then repaints; **do not invalidate or re-fetch raw frame textures**.
> Difference mode uses a zero-centered diverging ramp or is explicitly excluded. Prove with request
> interception that a range-only change performs no frame-binary request.

**Phase 4 — tokens**
> Write the Phase 4 execution plan, then apply MAP_VIEWER_REDESIGN §2. (a) Every interactive element
> ≥44×44 under `(pointer: coarse)` and
> ≥32×32 under `(pointer: fine)`, achieved with padding on the element itself — do not use
> pseudo-element hit expansion, the acceptance test cannot measure it. (b) No text below 11 px in
> viewer chrome; no operational label below 12 px; exclude sr-only, MapLibre labels, and portals
> outside the viewer root. (c) Standardize the shared trigger/field primitive across Product,
> Variable and Run Time so they read as peers — **keep the specialized picker panels behind them**,
> this is a trigger change only. Use the resolved §11.1 contract: preserve the Product/Variable
> appearance and adapt the Radix-backed Run trigger through the shared viewer primitive. (d) Designed
> focus ring at ≥3:1 on the dark chrome.

**Phase 5 — timeline**
> Write the Phase 5 execution plan, then implement MAP_VIEWER_REDESIGN §5. Critical: horizontal
> position maps linearly to **valid time**,
> never frame index — GFS is 3-hourly to FH 240 then 6-hourly to FH 384, so frame markers will be
> visibly unevenly spaced. That is correct; do not normalize it. Write a Phase 5 execution plan that
> includes the backend `readyThroughFh` manifest scalar from §5.4. Compute it from the authoritative
> expected target list and consume it directly; never substitute max published FH. Implement
> mode-specific forecast, observed, and valid vocabulary. Only forecast gets the amber ghost beyond
> its boundary. **Do not request a build, prefetch, or fetch for unavailable frames**. Add the global
> keyboard map with MapLibre's arrow handler suppressed and mode-specific slider accessibility text.
> Show the axis-linearity test across the GFS cadence change plus the out-of-order publication fixture.

**Phase 6 — chrome**
> Write the Phase 6 execution plan, then implement MAP_VIEWER_REDESIGN §6. The top-bar shrink to 48 px
> and the move of Product/Variable/Run into the rail must land together. Build the 288 px expanded
> SOURCE/VIEW rail and 72 px collapsed rail with icon + 11 px caption. Region shows its current value
> as text. Default expanded/collapsed from computed map width, not a viewport breakpoint; persist the
> override keyed by breakpoint class. Preserve the current legend in both rail states and reserve its
> stable Phase 7 mount; do not normalize or relocate it in this phase. Re-run the Phase 1 regression.

**Phase 7 — legend**
> Write the Phase 7 execution plan, then implement MAP_VIEWER_REDESIGN §7 using the resolved §11.3
> taxonomy. Normalize the existing `continuous`, `discrete`, `categorical`, and `indexed` kinds.
> Derive ptype-intensity, radar-ptype, and composite-group layouts in the frontend from existing
> metadata; do not add parallel backend kinds. Numeric products show actual thresholds — the ptype
> legend's `(dBZ)` claim must gain its shared dBZ break scale — while nominal categories do not invent
> numbers. Compact precipitation-type rendering retains every applicable ramp. Explicitly hide the
> compact chip from export composition and re-run the fixed Phase 1 regression.

**Phase 8 — mobile**
> Write the Phase 8 execution plan, then implement MAP_VIEWER_REDESIGN §8. Three states: at rest (map
> ≥72% of viewport height, 64 px one-row
> timeline, 84 px sheet peek), scrubbing (day strip and target readout fade in — the readout renders
> over the **bottom-center of the map**, above the timeline, because the user's thumb covers the
> bottom of the screen), expanded (sheet snaps peek → half → 62vh, map clamps to ≥180 px, sections in
> Source → View → Legend order). **The map element must not resize during the drag gesture** — render
> the day strip as an absolutely-positioned overlay, not an inserted flex row, or MapLibre will resize
> and the map will jump under the finger. Source identity lives in the map badge only; the sheet peek
> shows run state only. All controls ≥44 px. Run the mobile geometry tests.

---

## 14. Provenance

Synthesized from a live interactive audit of `cartosky.com/viewer` (2026-07-28, desktop and narrow
viewport, with DOM, accessibility and performance observations), two rounds of mockup iteration, two
rounds of external design critique, and two repo-grounded reviews. The second review resolved the
remaining contracts and corrected the performance, renderer-cache, test-fixture, and phase-sequencing
requirements in v2.

Supporting analysis (design rationale and measurements; **not required to implement this document** —
all specs needed for implementation are inlined above) lives in the Claude project:
`viewer-uiux-review-2026-07-28.md`, `viewer-refactor-plan-2026-07-28.md`,
`viewer-deck-lite-corrections-2026-07-28.md`.
