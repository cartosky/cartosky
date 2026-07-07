# Share Modal Overhaul & GIF Export — Implementation Plan

**Status:** Phase 4 implemented (2026-07-06) — gate (deliberately-missing-run degradation + cross-model cadence spot check on prod) pending. Phases 0–3 complete, gates passed on prod (Phase 3 including three rounds of gate-feedback fixes). Landed: live-canvas capture as the signed-out image path (compose-only exporter; offscreen rebuild deleted), Download / Copy image / native Share signed-out, compare-path repaint-hook capture (split + diff, server hook and signed-out local share), exporter anchor-chip compositing deleted (root cause of overlapping/cut-off city labels — see §3.4), `fadeDuration: 0` in screenshot mode. Signed-in flow unchanged (server render = TWF post artifact, preview-as-artifact holds until Phase 2 tabs). Phase 0 complete: blank-capture root cause (cold WebGL read-back) confirmed and fixed via `window.__cartoskyViewerCapture`; analytics channels verified in Mixpanel.
**Priority:** GIF export is the highest-priority busy-season feature (October feature freeze target). The anonymous image path (Phase 1) is a prerequisite for GIF and the primary share-funnel fix.
**Owner:** Brian Austin (sole production operator). Implementation via Codex/Claude Code agents; Brian executes all production commands and verifies each phase gate before the next proceeds.

---

## 1. Problem statement

The current share modal (`frontend/src/components/twf-share-modal.tsx`) has three problems:

1. **Share funnel is broken for signed-out users.** Metrics show users failing to complete the share process, and there have been zero CartoSky account sign-ups. The modal is effectively a sign-in wall in front of the image.
2. **No GIF export.** Forecast-hour progression GIFs and run-over-run trend GIFs are the top requested busy-season capability.
3. **Screenshot reliability.** Opening the share modal shortly after changing product/variable can produce a screenshot with a blank/basemap-only map.

## 2. Root-cause findings (code evidence)

These findings reframe the overhaul: the funnel problem is an auth-architecture problem, not primarily a layout problem.

### 2.1 Anonymous users cannot generate a weather screenshot at all — by construction

- `generateServerScreenshot` in `twf-share-modal.tsx` calls `POST /api/v4/share/screenshot` through `twfFetch`, which **throws when `!isSignedIn`**. The auto-screenshot effect fires this on modal open regardless of auth state. This is the "Sign in to CartoSky before generating a share image" state observed in production.
- The client-side fallback (`generatePreviewScreenshot` → offscreen map rebuild in `frontend/src/lib/screenshot_export.ts`) renders `buildMapStyle(...)` from `map-canvas.tsx`, which contains **basemap, boundaries, contours, and vectors only**. The weather grid is rendered by `GridWebglLayerController`, a custom WebGL layer attached imperatively in `map-canvas.tsx` — it is **not part of the style**. The offscreen-rebuild path can therefore never show grid data. It is dead weight that produces misleading (data-less) output.
- The settled-frame cache (`latestMapDataUrlRef` in `map-canvas.tsx`) is declared, exposed via getter, and nulled — but never written. `capturedMapDataUrl` from `buildScreenshotExportState` is always `undefined` in the live client path.

**Consequence:** the only path that produces a real data screenshot is the server Playwright render, and it requires Clerk auth. Signed-out users get nothing.

### 2.2 Blank-basemap capture: readiness-gate race candidate (server path)

`isViewerScreenshotReady` (`screenshot_export.ts`) returns `true` on `mapIdle` alone when both `selectionSupportsGrid` and `selectionSupportsRasterRgb` are `false`. Those refs derive from `selectedVariableRenderSubstrates` (async capability/bootstrap metadata) via a `useLayoutEffect` in `App.tsx`. If the headless page's first `idle` fires before substrates resolve, `data-viewer-ready="1"` is set with basemap only, and `screenshot_service.py` dutifully captures it.

Secondary hazards noted during audit:

- `mapIdleRef` is set once via `map.once("idle")` in `handleMapReady` and is **not reset** by the `selectionKey` reset effect (which clears only `gridFrameReadyRef`, `rgbFrameReadyRef`, `cityLabelsReadyRef`). Irrelevant for fresh headless loads, but a landmine if the ready gate is ever reused in-session.
- `waitForMapIdle` in the client exporter **resolves silently** on its 15s timeout instead of failing. Silent-degrade paths are how blank images escape.
- Opening the modal right after a variable change may snapshot an `fh` from the previous variable's frame axis into the permalink; if the new variable lacks that frame, the headless render can settle without data.

Phase 0 instruments and confirms before any fix lands. Do not fix on hypothesis alone.

**Phase 0 field evidence — ROOT CAUSE CONFIRMED (2026-07-06, 3 prod samples):** two blank captures and one good capture, same permalink shape, **all with healthy gate logs** (substrates resolved → grid_frame_ready → map_idle → viewer_ready_set, all gates true, `tilesLoaded:true` at signal). The decisive sample: `bytes=20831` for a 1280×720 capture (an empty canvas; real frames are hundreds of KB) while the gate log showed a fully rendered viewer — user-held PNG shows overlay/legend/logo intact with a transparent (white) map area. **Diagnosis: the blank-capture failure is the WebGL read-back, not the readiness gate.** `screenshot_service.py` does a cold `canvas.toDataURL()` on a `preserveDrawingBuffer:false` canvas after idle + settle; once the compositor has presented the frame, the drawing buffer is cleared and the read returns transparent pixels. Intermittent successes occur when an incidental repaint lands just before the read. The client draft path (`captureDraftDataUrl`) avoids this exact hazard by forcing a repaint and reading synchronously in the same frame.

- **Fix (lands per §3.4):** expose the proven repaint-then-read capture on `window` in screenshot mode and have `screenshot_service.py` call it instead of the cold `toDataURL`; keep cold read only as a logged fallback. The compare path (`data-compare-ready` branch) has the same cold-read hazard and needs the same treatment.
- **Tri-state race status:** never observed across all samples (substrates consistently resolve seconds before first idle in headless loads). Not the production failure. The §3.4 tri-state/`mapIdleRef` hardening remains cheap hygiene but is no longer the motivating fix.
- Blank captures are now detectable in logs via `bytes=` on the `phase_timings` line.

### 2.3 Component health

`twf-share-modal.tsx` is ~1,500 lines with ~35 `useState` hooks and interdependent effects. Adding tabs + GIF state to this component as-is is how the next generation of dep-array bugs happens. The overhaul includes a behavior-preserving hook extraction (Phase 2) before new feature surface lands.

## 3. Recommended architecture

### 3.1 Image: live-canvas capture becomes the default path for local channels

**Channel split (core design decision).** Viewport consistency is a TWF-post requirement, not a screenshot requirement:

- **Download / Copy / native Share (the anonymous funnel):** the correct output is *what's on my screen right now*. WYSIWYG viewport-shaped capture is the spec, not a compromise. No normalization needed.
- **TWF post:** the correct output is a consistent, forum-quality 16:9 artifact. Normalization is scoped here — and this channel is auth-gated anyway.

**Prior art (why the last client-side attempt was abandoned, and why this differs):** an earlier client-side capture implementation was nearly instant with legend and branding included, but forcing a fixed output resolution onto a viewport-shaped capture made the generated screenshot differ from what was on screen — a trust problem — so server-side was adopted as the reliable path. The failure was not client capture itself; it was making one capture path serve two incompatible goals (WYSIWYG *and* normalized). The channel split above removes that conflict: local channels never normalize, and the TWF path keeps the server render.

**Trust mechanism = preview-as-artifact.** Note the server path has the same nominal mismatch (a portrait phone user posts a 1280×720 render — different visible extent than their screen); it feels trustworthy because the modal previews the exact artifact before posting. Any path that shows the exact bytes being shared before the action clears the trust bar. This rule carries into GIF: the preview must be the encoded output, playable, before post/download.

Elevate the `captureDraftDataUrl` mechanism (synchronous canvas read after a repaint — already proven in `map-canvas.tsx`; works with `preserveDrawingBuffer: false`, includes the WebGL grid layer) from "draft preview" to the primary capture for local channels:

- Capture the live canvas at device resolution.
- Feed it through the existing `capturedMapDataUrl` branch of `exportViewerScreenshotPng`, which already composes overlay, legend, logo, and anchors on top of a supplied image.
- Works signed-out. Instant (no headless render, no 28s worst case). Zero server load.
- **Delete or demote the data-less offscreen-rebuild path.**
- Keep the server Playwright render for **TWF posts** (normalized 1280×720 output; the media upload endpoint requires auth anyway). This is the resolved default through busy season — see §3.5 and §7.
- Do **not** flip `preserveDrawingBuffer` on the live map — that is a pan-performance regression.

Local capture matches the user's viewport aspect/resolution by design (WYSIWYG is the spec for these channels); the export pipeline already handles portrait via `PORTRAIT_OUTPUT_WIDTH`.

### 3.2 GIF: client-side generation, not server-side

Server-side GIF of FH 150–200 ≈ 25–50 headless renders per request through `screenshot_service` on a 32GB box where schedulers consume the headroom — at exactly the moment share volume peaks. Rejected.

Client-side approach:

- Step forecast hours on the live map, capturing each frame with the **same per-frame readiness gate** (grid-frame-ready for that hour) — the dual-boolean gate applies per frame, not once.
- Encode in a Web Worker.
- **New dependency:** `gifenc` (~5KB, zero deps, fast palette quantization). Justification: nothing in the existing stack encodes GIF; canvas `toBlob` cannot. This is the minimal viable addition and passes the new-dependency bar.
- Caps: default 720px-wide output; hard frame cap ~60 desktop / ~30 mobile; fixed dither palette; estimated size shown before generating.
- Zero server disk/RAM impact. No sizing spike required (a server-side variant would have needed one plus a queue).

**Trends mode (run-over-run)** is meaningfully harder and ships as its own phase:

- Align frames by **valid time**, not forecast hour. Fixed-FH run comparison is meteorologically misleading (each frame shows a different valid time). Per-run `fh = validTime − runTime`.
- Nearest-available-frame handling where cadence differs (e.g., GFS 3-hourly → 6-hourly past FH 120). Do not assume identical fh availability across runs or models.
- Per-frame run labels burned into the overlay.
- Graceful skip when a run lacks the frame — retention eviction of the oldest of the N runs mid-capture is a real case.
- Trends mode must not block FH-progression GIF for the October freeze.

### 3.3 Modal: three tabs, TWF as destination — not a gate

Tabs: **Image | GIF | Link**.

- Image/GIF tabs: preview + Download / Copy / native Share (Web Share API with `navigator.share({ files })` — the mobile-first win), fully functional signed-out.
- TWF becomes a "Post to The Weather Forums" section inside the Image and GIF tabs: signed-in-and-linked users get the existing composer (forum/topic/message — reuse the topic-cache and `share_prefs` logic unchanged); everyone else sees one quiet row: "Post directly to TWF threads — connect your account." Visible on every share, never blocking the primary action. De-emphasis without neutering.
- Link tab: copy link, copy text+link. It is thin; that is acceptable for discoverability — do not invent features to fill it.
- Mobile keeps the existing bottom-sheet presentation; action row collapses to Download + native Share.

### 3.4 Screenshot reliability fixes

- Make the supports flags **tri-state** (`unknown | true | false`); refuse `data-viewer-ready` while `unknown`. (Pending Phase 0 confirmation of the race.)
- Reset `mapIdleRef` alongside the other readiness refs on `selectionKey` change.
- Client exporter: `waitForMapIdle` timeout should surface a retryable error, not silently proceed.
- Screenshot-trust rule extended to the new paths: **no share flow ever silently produces a basemap-only image** — fail loudly with retry.
- **Preview-as-artifact:** the modal preview must always show the exact artifact (image or encoded GIF) that will be downloaded/copied/posted.
- **Never less extent than the screen:** any aspect normalization uses contain semantics (bounds-fit adds margin), never crop. Silent crop is a hard failure.
- **City value label collision — RESOLVED in Phase 1 (diagnosis corrected 2026-07-06):** the overlapping/cut-off labels in captures were NOT the in-map city label system colliding with itself. They were **two independent label systems drawn on top of each other**: the exporter (`exportViewerScreenshotPng`) composited legacy anchor chips (`drawAnchors`, thinned by km-radius in `getActiveAnchorLabels`) over a capture that already contained the in-map city value pills (thinned by screen-rect in `queryVisibleCityPoints`). Different thinning algorithms picked different winners, so chip-for-city-A landed on pill-for-city-B — the exact pairs in the prod PNG (visible as gray "twin" labels under each white chip). The live map never rendered the chips at all (`getActiveAnchorLabels` had exactly one caller: the screenshot state builder). Fix: the anchor-chip compositing was deleted; captures are WYSIWYG and show only the in-map, collision-managed labels. The `text-allow-overlap` flags in `city-labels.ts` were left untouched — the in-map pre-thinning has not been observed to leak, and naive engine collision would break the pill/name two-layer pairing (a city's own name would collide with its own pill). Revisit only if in-map pill-vs-pill overlap is actually observed.

### 3.5 Post-freeze option: client-side fixed-viewport render for TWF posts

Documented for later, **not** in scope for any busy-season phase. If revisited, the design that respects the no-silent-crop rule:

1. Offscreen MapLibre map at 1280×720, `buildMapStyle` as base.
2. **Attach a `GridWebglLayerController` instance** pointing at the current frame binary — the piece the old client offscreen path was missing entirely (it rendered no data; §2.1). The binary is already in browser/Cloudflare cache, so load is fast.
3. **Fit by bounds, not center+zoom:** take the live map's current bounds and `cameraForBounds` into the 16:9 canvas with contain semantics. Aspect mismatch then *adds* margin context rather than cropping — everything the user saw is guaranteed present, plus a bit more. This is the honest resolution of the earlier trust problem: never less extent than the screen, clearly previewed. (The prior attempt copied center+zoom into a different aspect, which silently changes extent.)
4. Dual readiness gate per project standard: offscreen `idle` + the controller's frame-ready callback, per frame.

**Why this is deferred (real cost):** the headless render runs the actual app, so every layer type comes for free — grid, composite grids, RGB raster controller, SPC/CPC vectors, hazard polygons, contours, city value labels, compare mode. A client offscreen render must reattach *each* of those, and every future layer type becomes a second integration point that can silently drop content — a permanent maintenance tax for a solo operator. Build this only if (a) Grafana shows Playwright renders actually pressuring the box during busy season, or (b) TWF-post latency becomes a measured complaint. Gate: layer-matrix spike (enumerate every renderable layer type and its offscreen attach path) before implementation.

**Cheaper experiment to run first:** question whether 16:9 normalization for TWF is a hard requirement or an aesthetic default. Forums render portrait images fine, and phone-screenshot posts are native behavior for the TWF audience. Option: WYSIWYG capture as the default for TWF posts too, with a "Forum layout (16:9)" toggle routing through the server render. If nobody toggles it (measurable via the Phase 0 channel analytics), the headless path can be retired without building §3.5 at all; if portrait posts look bad in threads, nothing is lost.

## 4. Risks and tradeoffs

| Risk | Mitigation |
| --- | --- |
| Live capture quality varies with viewport | Accepted for local share; server render retained for TWF post consistency |
| GIF capture hijacks the map during frame stepping | Capture-lock overlay with progress + cancel; clean abort on variable change / tab close / modal close |
| Mobile memory during GIF encode (30 × full-res canvases) | Frame cap, downscale before encode, encode in worker |
| Trends-mode correctness (valid-time alignment, uneven cadence, missing frames) | Isolated in Phase 4; explicit edge-case list above |
| Hook extraction regresses TWF posting (load-bearing dep arrays) | Behavior-preserving extraction gated on existing Playwright coverage, before new features |
| Anonymous abuse of server endpoints | Server screenshot + media upload stay auth-gated; anonymous flows are entirely client-local |

## 5. Phased plan (stop-and-verify gates)

Each phase is a separate agent implementation prompt with: explicit execution-model section (agent writes, Brian executes), resumability, machine-readable evidence output (`results.json` where applicable), and runbook-first gate structure.

### Phase 0 — Diagnose and instrument
- Add gate-state logging to the server screenshot path: substrate resolution timing vs. first `idle` vs. `data-viewer-ready` set (extend existing phase-timing telemetry in `screenshot_service.py` / viewer screenshot mode).
- Confirm or kill the tri-state race hypothesis (§2.2). Capture reproduction with logged gate states, or produce a ruled-out list with the alternate cause.
- Add `share_completed` channel breakdown to product analytics: `download | copy | native_share | twf_post | gif` (extend the existing `captureProductAnalyticsEvent` calls).

**Gate:** blank-basemap reproduction with logged gate states, or a documented ruled-out list. Analytics events visible end to end.

### Phase 1 — Anonymous image path (implemented 2026-07-06, gate pending)
- [x] Live-canvas capture wired into the `capturedMapDataUrl` compose path (`captureMapPng` prop → repaint-then-read PNG; signed-out and dev default; signed-in keeps the server render until Phase 2 tabs split the channels).
- [x] Download / Copy image / native Share available signed-out (`share_completed` channels wired; native Share button renders only where `navigator.share` exists).
- [x] Data-less offscreen rebuild deleted — `exportViewerScreenshotPng` is compose-only and throws without a captured image (no silent basemap-only output). The anchor-projection offscreen map went with it.
- [x] §3.4 fixes confirmed by Phase 0: capture read-back fixed (Phase 0 capstone); tri-state race ruled out, so the tri-state/`mapIdleRef` hardening was intentionally skipped; `fadeDuration: 0` in screenshot mode for deterministic captures.
- [x] Compare-path capture fix: `window.__cartoskyCompareCapture` (split compose + diff) preferred by `screenshot_service.py` (`capture_mode=` logged, cold reads kept as fallback); the same capture powers the compare page's signed-out local share with composite-aspect output (no crop).
- [x] City label overlap: fixed by deleting the exporter's duplicate anchor-chip compositing — diagnosis corrected, see §3.4.
- [x] Gate-verification fixes (2026-07-06, both pre-existing): (1) exporter compare divider deleted — the model-string heuristic drew a bogus center line on **diff** exports, and split captures already carry the gutter baked in; (2) `generateServerScreenshot` now measures the returned image and uses its real dimensions instead of hardcoded 1280×720 — compare split composites are wider than 16:9, so the hardcoded dims cover-cropped their left/right edges (no-silent-crop rule). Compare capture also hardened: render-event timeout (a removed map can no longer hang the modal) + one retry against fresh refs (intermittent first-attempt failure on panel remount).
- [x] Diff server screenshots missing city values (2026-07-06, pre-existing, two stacked causes): (1) **compare maps ignored the permalink camera** — lat/lon/z was parsed into state and written back on moves, but never applied to the maps, which always opened at the region fit; at the headless 1280×720 viewport that fit sits below the city-label min zoom (3.5), so headless renders showed no labels *and* a different extent than the user shared. Fixed: the permalink (or user-committed) camera is applied at map-ready in all three compare maps, using per-instance `manualLocationJumpRef` flags to skip MapCanvas's one-shot region fit. This also fixes shared compare links not honoring the sender's view. (2) The diff readiness gate had no city-label condition — `data-compare-ready` now additionally waits for the diff map's `onCityLabelsReady` (gate step 5), matching the viewer's dual-gate standard. Split keeps its two-frame gate (values verified present; scope kept tight). (3) **Residual hit-or-miss after (1)+(2):** `refreshCityValueLabels` marked labels-ready *without values* when the in-canvas sampler was transiently keyed to a prior compute/selection and no fallback sampler exists (compare maps take the no-handler branch; the viewer wires `onCityFrameSampled` and is unaffected). Fixed with a bounded retry: up to 10 × 250ms re-refreshes before giving up and proceeding without values (preserves the original no-hang guarantee for genuinely evicted frames).

**Gate:** signed-out user on prod gets a correct data image in <~2s p90 with zero authenticated requests. Screenshot regression pass across: grid variables, RGB/satellite, compare mode, SPC/CPC categorical legends, observed-mode products, portrait mobile.

### Phase 2 — Modal restructure (implemented 2026-07-06, gate pending)
- [x] Tabs + hook extraction: `components/share/` now holds `ShareModal.tsx` (shell), `useScreenshotCapture.ts`, `useTwfPosting.ts` (forum/topic/prefs/submit logic moved verbatim — dep arrays untouched), `useGifExport.ts` (stub), `share-utils.ts` (types + pure helpers). `twf-share-modal.tsx` deleted; App/compare mounts updated.
- [x] TWF composer moved into the Image tab as a "Post to The Weather Forums" destination section (linked users); everyone else sees the quiet one-row connect prompt — never blocking Download/Copy/Share. Link tab = copy link / copy text+link (replaces the old bottom Copy menu). GIF tab renders the stub; hidden on /compare (`gifTabEnabled={false}`, per §7 open decision).
- Note: the old single open-reset effect was split per-hook with identical open-transition guards; `share_initiated` still fires once per open. Playwright coverage of posting is thin (share-button reachability only), so the prod gate below is the real regression check.

**Gate:** full TWF post flow (existing topic, new topic, forum switching, prefs persistence, rate-limit handling) verified against prod TWF. No behavior change in posting.

### Phase 3 — GIF: forecast-hour progression (implemented 2026-07-06, gate pending)
- [x] Client-side capture with per-frame readiness gate + `gifenc` worker encode, caps, capture-lock UI with progress/cancel, GIF tab wiring. Implementation notes:
  - `GifFrameDriver` (App.tsx) steps the live pipeline: sets `forecastHour` **and** `targetForecastHour` (outside playback the display follows `forecastHour` — targeting only `targetForecastHour` never presents), then awaits the per-frame dual gate (`gridReadyHourSet.has(hour)` AND `presentedGridDisplayHour === hour`); 6s per-frame timeout skips the frame rather than aborting the run; timeline snapshot restored after done/cancel/close.
  - Per-frame capture: `onCaptureCanvas` in map-canvas — repaint-then-read into a downscaled 2D canvas (no PNG round-trip). Frames composed via the shared `composeShareFrame` (same overlay/legend/logo as stills, overlay rebuilt per frame with that frame's fh/valid time), streamed to the `gifenc` worker as transferable RGBA buffers (≤1 frame held on the main thread).
  - Palette quantized once from the first frame and reused (fixed palette). Loop = infinite; 200ms/frame with 1.2s final-frame hold.
  - Capture lock = the modal backdrop (map is uninteractable while open) + progress bar with Cancel; aborts cleanly on cancel/modal close/unmount (worker terminated, blob URL revoked, timeline restored).
  - Measured locally: 19-frame HRRR 720px GIF = 1.5MB, ~15s cold (per-frame binary fetch dominates) / ~4s warm; valid GIF89a playing in-tab (preview-as-artifact); cancel at mid-run verified with timeline restore.
- [x] Gate feedback round 3 (2026-07-06): (1) mobile preview clamp moved onto the `<img>` itself — the %-max-height-against-auto-parent version clipped portrait GIFs (preview-as-artifact violation); full frame now always visible with actions above the fold. (2) **In-modal range preview**: the modal covers/blurs the live map, so slider scrubbing now renders a live thumbnail *inside* the GIF tab (driver `captureFrame(480)` → JPEG data URL, token-guarded latest-wins, seeded on tab open, "Previewing FH n" caption). (3) `twf_post` analytics carry `artifact: image|gif`. (4) GIF chrome scale floor settled at **0.65** — 0.8 fixed the blur but read as oversized; with flat shadows + integer-aligned draws carrying the crispness, 0.65 keeps legibility while covering far less map.
- [x] Gate feedback round 2 (2026-07-06): (1) mobile — GIF preview height-clamped (38dvh) so Download/Share actions stay above the fold on portrait captures; (2) chrome clarity — card shadows disabled in GIF compose (`chromeShadows: false`; the soft blur quantized into a dark halo in the 256-color palette) and logo drawn at integer pixel positions; (3) FH range is now a dual-thumb slider that **steps the live map behind the modal to the dragged handle** (debounced `previewFrame` via the frame driver; timeline snapshot restored on modal close); (4) **TWF GIF posting** — the media upload accepts `image/gif` (signature-validated, `.gif` keys, correct ContentType; same 10MB cap) and the TWF destination section renders on the GIF tab with a Post GIF button that uploads the encoded GIF (cached per blob) as the post image.

**Gate:** 50-frame HRRR reflectivity GIF on desktop and a mid-tier phone; size and duration within caps; zero server CPU/RAM delta (Grafana per-process graphs are authoritative); clean abort paths verified.

### Phase 4 — GIF: run trend (implemented 2026-07-06, gate pending)
- [x] Valid-time alignment, per-frame run labels, nearest-frame cadence handling, missing-frame skip. Implementation notes:
  - Driver run stepping (`showRunFrame(runId, validTimeISO)`): sets the viewer's run, waits for the selection to resolve to that run with a frames list, snaps to the frame whose valid time (`runTime + fh`) is nearest the target with a **3h tolerance** (covers GFS 6-hourly late cadence), then applies the standard per-frame dual gate. Outside tolerance / timeout → `shown:false` → the run is skipped, not the loop (evicted-run degradation).
  - Trend generate: last **N runs** (user-selectable 2–6 via a Runs chip row, default 3 — see §7), oldest → newest, each frame's overlay state carries that run's label + resolved fh + actual frame valid time. Anchor valid time = the slider-selected hour on the current run (defaults to the hour the user was viewing).
  - GIF tab mode toggle **Forecast loop | Run trend** (trend hidden when unsupported: non-forecast axis, <2 runs, non-grid). Trend gets its own speed presets (1400/1000/600ms — 3 frames want ~1s holds). Restore token now includes the run; verified run+hour restore after close.
  - Verified locally: HRRR 3-run trend GIF (0.2MB, delays 100/100/120 hundredths, per-frame run labels e.g. "23Z 7/06 • FH 4" aligned to one valid time). The deliberate-eviction and GFS mixed-cadence checks are the prod gate (not reproducible locally).
- [x] **Blank-frame fix (gate feedback 2026-07-06** — "run ID changes in the overlay but no weather data, just the basemap"). Run switches settle in waves (`run` state → resolved run → grid manifest → frame bytes → texture upload → paint), and every React-mirrored readiness signal can be stale or flicker between a gate check and the capture's render callback. Three-layer fix:
  1. `showRunFrame`'s settled-wait now also requires `gridManifest.run === runId` (previously `resolvedRun` flipped instantly while the frames list still described the old run → false settle ~200ms in → blank capture).
  2. The per-frame gate additionally requires `visibleGridFrameHour === target` — the selection-epoch-guarded fresh-paint signal that is nulled on selection change — because the bytes-ready set (`gridReadyFrameUrlsRef`) is *not* cleared across run switches (HTTP-cached manifests made returns to a previous run pass the old gate before the texture was re-uploaded).
  3. **Atomic in-render check (the load-bearing one):** `captureCanvasSnapshot(maxWidth, expectGridHour)` consults `GridWebglLayerController.visibleFrameHour()` *inside the same `map.once("render")` callback that reads the pixels* and resolves `null` unless the grid layer is actively drawing exactly the expected hour. `useGifExport` passes `frameState.fh` and retries null captures for up to 4s (150ms backoff). Signals 1–2 plus a 300ms stability re-check reduce retries; only the atomic check is race-free — a follow-up prop commit could still clear the texture between any polling gate and the capture render.
  - Verified: fresh-load trend generate + two regenerates all 3/3 frames with data (per-frame decoded pixel check ~79.6% colored vs 1.2% for a basemap-only frame); 19-frame hours-mode loop unaffected (64–82%).

- [x] **Post-gate polish (2026-07-07):** GIF tab de-cluttered (removed the trend caption, the size-estimate line, and the "map will step through…" helper; the red not-enough-frames validation message stays). Share overlay line 1 reformatted for stills AND GIF frames: `{run id} {model} • FH n • h:MM AM/PM M/DD/YY` in the viewer's local timezone ("Latest (…)" wrapper stripped to the bare run id in `defaultOverlayLines`).
- [x] **Post-gate polish round 2 (2026-07-07):**
  - GIF tab layout: the frame preview now spans the full modal width (mirrors the ready state's presentation; capture width bumped 480 → 720 to stay sharp) with the "Previewing FH n" caption beneath, and all controls (mode toggle, sliders, Runs chips, speed presets, Generate) moved into their own card below.
  - Chrome trim: `CHROME_COMPACT_FACTOR = 0.85` applied to the derived scale in `composeShareFrame` — overlay, logo, and legend all shrink ~15% in stills and GIFs; verified legible at GIF scale (0.65 × 0.85 ≈ 0.55, ~9px overlay text at 720px).
  - Server-render timezone: the client sends `Intl…().timeZone` with `/api/v4/share/screenshot`; the endpoint validates it via `zoneinfo` (invalid → server default, never a failed render) and Playwright's context gets `timezone_id`, so TWF forum stills stamp the poster's local time like every client-composed artifact.
  - Mixpanel: `gif_mode` (`hours`|`trend`), `gif_frame_count`, and `trend_run_count` (trend only) added to `share_completed` for gif download / native share and twf_post (via new `postAnalytics` param on `useTwfPosting`).

**Gate:** GFS 3-run trend GIF with a deliberately evicted/missing oldest run degrades gracefully and labels frames correctly. Cross-model spot check (HRRR hourly vs. GFS mixed cadence).

## 6. Acceptance criteria (overall)

- Signed-out share-to-image under ~2s p90, zero authenticated requests.
- No share flow ever silently produces a basemap-only image; failures are loud and retryable.
- Preview always shows the exact artifact being shared (preview-as-artifact), including the encoded GIF before post/download.
- No share path ever shows less map extent than the user's screen at capture time (contain, never crop).
- TWF post success rate unchanged post-refactor.
- GIF generation causes zero additional server CPU/RAM (verifiable in Grafana).
- `share_completed` segmented by channel so the funnel impact of the redesign is measurable.
- Mobile bottom-sheet behavior and Web Share flow verified on iOS Safari and Android Chrome.

## 7. Open decisions

- [x] **Resolved:** server render retained for TWF posts through busy season. Client fixed-viewport render (§3.5: bounds-contain + `GridWebglLayerController` attach) is a post-freeze option gated on a layer-matrix spike **and** Grafana evidence that Playwright load actually matters. Cheaper first experiment: 16:9 as an opt-in "Forum layout" toggle with WYSIWYG default, measured via channel analytics (§3.5).
- [x] **Resolved (Phase 3):** GIF defaults — 720px-wide output, 200ms/frame (~5fps) with 1.2s end hold, hard frame cap 60 desktop / 30 mobile (`isMobile` from the export state), even stride-sampling when a run exceeds the cap. Constants in `useGifExport.ts`. **Gate feedback additions (2026-07-06):** user-tunable From/To forecast-hour range + speed presets (Slow 350ms / Normal 200ms / Fast 120ms) in the GIF tab, defaults restored per modal open; and `chromeScale` floor of 0.8 for GIF frame composition — the width-derived 720/1280 scale left overlay/logo/legend small and blurry at pixelRatio 1.
- [x] **Resolved (Phase 4, revised post-gate):** trend run count is user-selectable 2–6 (`GIF_TREND_RUN_DEFAULT = 3`, `GIF_TREND_RUN_MAX = 6` — backend retains ~6 runs). Settings store only the **count**, never run ids: the ids resolve from the live runs list at render time (chip options + oldest → newest span label in the caption) and again at generate time, so runs publishing or aging out while the modal is open can't strand a stale selection; eviction mid-generate still degrades via the existing skip-on-missing path.
- [x] **Resolved (Phase 2):** compare-mode GIF out of scope for v1 — the GIF tab is hidden on `/compare` (`gifTabEnabled={false}`).

## 8. Reference — key files

| Area | Path |
| --- | --- |
| Share modal | `frontend/src/components/twf-share-modal.tsx` |
| Client export/compose + readiness helper | `frontend/src/lib/screenshot_export.ts` |
| Media upload | `frontend/src/lib/share_media.ts` |
| Prefs/topic cache | `frontend/src/lib/share_prefs.ts` |
| Summary builder | `frontend/src/lib/share-summary.ts` |
| Readiness wiring, `buildScreenshotExportState`, modal mount | `frontend/src/App.tsx` |
| `buildMapStyle`, grid controllers, draft capture | `frontend/src/components/map-canvas.tsx` |
| Server headless render | `backend/app/services/screenshot_service.py` |

---

*Corrections to this plan go in-place per project convention. Update status/gates as phases complete.*
