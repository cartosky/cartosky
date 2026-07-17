# Loader Redesign — Implementation Plan

**Status:** Design locked. Not started.
**Scope:** Replace the generic ring spinner with a hex signal ring (cold boot / no prior frame) and introduce a new non-blocking top progress bar (in-app model/variable/run-time/region switches). No changes to the screenshot readiness gate — confirmed independent, see below.

---

## 1. Locked decisions

- **Cold boot / no prior frame:** hex signal ring — the brand hexagon outline with a bright segment chasing around it. Replaces the current ring-and-dot spinner used in `SiteLoadingOverlay` and `ViewerMapSkeleton`.
- **In-app switches with a valid prior frame** (model, variable, run time, region — *not* forecast-hour scrubbing/playback, see §5): a new non-blocking top progress bar under the viewer header. No dimming, no modal, map stays interactive.
- **Not attempting to reproduce the illustrated logo** (hex + wave ribbons + faceted terrain) as an animated asset. Confirmed no SVG/vector source exists anywhere in the repo (`frontend/public/assets/` contains only `new_logo.png`; `BRAND_LOGO_SRC` in `frontend/src/lib/branding.ts` points at the PNG). Auto-trace attempts (mine and Brian's, independently) both hit the same gradient/glow banding problem. The hex ring is original vector work referencing the mark's shape, not a derivative of the PNG.
- **Readiness gate is out of scope** — see §4. No changes needed.

---

## 2. Current architecture (as of this session)

Three separate things currently render loading UI, with duplicated markup:

| Component | File | Role |
|---|---|---|
| `SiteLoadingOverlay` | `frontend/src/components/site-loading-overlay.tsx` | Global blocking modal. Driven by a stack (`SiteLoadingProvider`/`useSiteLoading` in `frontend/src/lib/site-loading.tsx`). Renders the ring+dot spinner seen today. |
| `ViewerMapSkeleton` | `frontend/src/components/ViewerMapSkeleton.tsx` | React Suspense `fallback` for the lazy-loaded `/viewer` and `/compare` routes (`AppLayout.tsx`), shown only while the route's JS chunk itself is downloading. **Re-implements the same ring+dot spinner markup independently** — not a shared component with `SiteLoadingOverlay`. |
| `useFrameStatusBadge` | `frontend/src/lib/use-frame-status-badge.ts` | Existing transient text badge (`frameStatusMessage`), currently scoped to animation/playback only ("Starting grid playback", "Buffering grid frames"). Rendered inside `BottomForecastControls`, bottom bar. **Timer-based auto-dismiss**, not tied to actual fetch completion — do not copy this pattern for the new top bar (see §5). |

**Cold-boot trigger** (`frontend/src/App.tsx` ~L1834–1847):
```
const showInitialMapSkeleton = loading || !isMapReady || shouldWaitForInitialGridFrame;
```
`loading` is set once during the bootstrap effect (~L3238 `setLoading(true)` → ~L3341 `finally { setLoading(false) }`) and is **not** re-triggered by model/variable/run/region switches — confirmed by reading the effect. `shouldWaitForInitialGridFrame` is gated on `!firstWeatherFramePainted`, and `firstWeatherFramePainted` is a set-once flag (`setFirstWeatherFramePainted(true)` at L3048, no reset call anywhere in the file). So **the existing full-block skeleton is already correctly scoped to true cold start** — it does not currently re-fire on in-app switches.

**Important finding: there is currently no loading indicator at all for in-app model/variable/run-time/region switches.** The manifest-fetch effects (`fetchManifest` calls at App.tsx ~L3438, ~L3451, ~L3489, ~L3926) don't expose any in-flight boolean today. The top bar is net-new, not a restyle of something that already blocks the map on switches.

---

## 3. Hex signal ring — component spec

Geometry measured directly from the alpha channel of `frontend/public/assets/new_logo.png` (not eyeballed):

- Icon bounding box: 322×343px. Top and bottom vertices are single points; left/right edges are vertical between y=90 and y=253.
- **viewBox:** `0 0 322 343`
- **Points:** `161,0 320,90 320,253 161,343 2,253 2,90`
- Perimeter ≈ 1057.

```css
.hexGhost   { fill: none; stroke: rgba(165,243,252,.16); stroke-width: 4; } /* cyan-200 @ 16% */
.hexComet   {
  fill: none;
  stroke: #a5f3fc; /* cyan-200 — matches existing border-t-cyan-200 in site-loading-overlay.tsx */
  stroke-width: 5;
  stroke-linecap: round;
  stroke-dasharray: 110 950;
  filter: drop-shadow(0 0 4px rgba(103,232,249,.42)); /* reuses the existing glow value from site-loading-overlay.tsx */
  animation: hexChase 2s linear infinite;
}
@keyframes hexChase { to { stroke-dashoffset: -1060; } }
```

Colors are pulled from what `site-loading-overlay.tsx` already uses (`cyan-200`, and the `rgba(103,232,249,0.42)` glow) rather than introducing new values — this matters because Phase 3 makes this the loader for marketing/admin route transitions too, not just the viewer.

---

## 4. Screenshot readiness gate — confirmed independent, no action needed

Flagged as an open risk two sessions ago; now verified false alarm. The dual-boolean gate (`gridFrameReadyRef`, `mapIdleRef`, plus `rgbFrameReadyRef` / `cityLabelsReadyRef`) that backs `data-viewer-ready` and `maybeSignalViewerReady()` (App.tsx ~L4429–4450, ~L4680–4696) is driven entirely by MapLibre's `idle` event and the grid layer's own ready callback — it has **no dependency on `SiteLoadingOverlay`, `showInitialMapSkeleton`, or any loading-UI visibility state.** Swapping the overlay's spinner for the hex ring, and adding the new top bar, touches none of this. No changes needed here; this section exists so the plan doesn't re-raise it as a risk.

---

## 5. Top progress bar — component spec

**Mount point:** `frontend/src/components/ViewerSiteHeader.tsx`. Header is `fixed inset-x-0 top-0 z-[80]`. Mount the bar as `absolute inset-x-0 bottom-0 h-[3px]` on that same header element so it sits under the toolbar row (rendered via `ViewerNavDesktop`/`ViewerNavMobile` inside this header) regardless of desktop/mobile layout variant.

**New state needed (does not exist today):** an in-flight boolean for the manifest-fetch effects, e.g. `isFrameSwitching`. Set `true` at the start of each `fetchManifest(...)` call site (App.tsx ~L3438, ~L3451, ~L3489, ~L3926), cleared in a `finally` — mirror the bootstrap effect's own `finally { setLoading(false) }` pattern (~L3341) so it clears on error paths too, not just success. Do **not** reuse or extend the existing `loading` flag — that must stay cold-boot-only per §2.

**Trigger condition:** show the bar when `isFrameSwitching && firstWeatherFramePainted`. The `firstWeatherFramePainted` check is what cleanly partitions this from the hex ring — before first paint, only the hex ring can show; after, only the bar can.

**Scope decision:** bar covers model / variable / run-time / region switches only. Forecast-hour scrubbing and playback keep using the existing `frameStatusMessage` badge in `BottomForecastControls` — don't fold that in here. Reason: that system already works, is tuned for animation specifically, and conflating the two adds risk this close to the October freeze for no clear benefit. Revisit post-freeze if the two indicators feel inconsistent side by side.

```css
.topbar    { position:absolute; inset-inline:0; bottom:0; height:3px; overflow:hidden; background:rgba(255,255,255,.06); }
.progress  { position:absolute; inset-block:0; left:-40%; width:40%;
             background:linear-gradient(90deg, transparent, #a5f3fc, transparent);
             animation: slide 1.3s ease-in-out infinite; }
@keyframes slide { to { left:100%; } }
```

---

## 6. Phases

**Phase 1 — Hex signal ring + dedupe**
- Build the hex ring as a shared component (e.g. `HexSignalRing`), used by both `SiteLoadingOverlay` and `ViewerMapSkeleton` in place of their currently-duplicated ring+dot markup.
- *Stop and verify:* cold-boot the viewer (hard reload), confirm the ring renders correctly at both the Suspense-fallback size (`ViewerMapSkeleton`, full page) and the overlay size (`SiteLoadingOverlay`, card). Confirm marketing/admin route transitions (which also use `SiteLoadingOverlay`) now show the hex ring too — this is an intentional side effect of the dedupe, not a bug, but worth eyeballing before moving on.

**Phase 2 — Top progress bar**
- Add `isFrameSwitching` state and wire it into the four `fetchManifest` call sites per §5.
- Build the bar component, mount in `ViewerSiteHeader.tsx`.
- *Stop and verify:* switch model, variable, run time, and region independently on an already-loaded viewer. Confirm the bar appears/disappears correctly, never appears during cold boot, and never gets stuck visible if a fetch fails (kill network mid-switch to check the `finally` path).

**Phase 3 — Cleanup**
- Confirm no leftover duplicated spinner markup remains in either `SiteLoadingOverlay` or `ViewerMapSkeleton` after Phase 1.
- *Stop and verify:* grep the frontend for the old ring-and-dot class combination (`border-t-cyan-200.*animate-spin`) to confirm nothing else independently duplicated it beyond the two files identified in §2.

**Phase 4 — deferred, not in this pass**
- Small-scale (favicon-size) version of the hex ring for any other inline spinners in the codebase — not audited this session. Separate task.

---

## 7. Explicit won't-do

- Won't attempt pixel-accurate reproduction of the illustrated logo (waves/facets/gradients) as an animated asset — no source vector exists, confirmed.
- Won't reuse `useFrameStatusBadge`'s fixed-timer dismissal pattern for the top bar — it needs to track real fetch state, not a guessed duration.
- Won't touch the screenshot readiness gate — confirmed already independent (§4).
- Won't fold forecast-hour scrubbing/playback status into the new top bar this pass (§5).

## 8. Open items for Brian

- None blocking. Geometry, colors, and mount point are all pulled from the actual codebase rather than assumed — ready for Phase 1 whenever you want to hand this to Codex/Claude Code.
