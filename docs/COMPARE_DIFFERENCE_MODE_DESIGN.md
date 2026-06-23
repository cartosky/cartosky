# Compare Difference Mode — Design Plan

## Architectural Decisions Locked

The following decisions are fixed for implementation. Do not revisit without explicit product approval.

1. **Grid data access (Option 2 + `GridFrameCache`).** `compare-diff.ts` owns all frame fetching and decoding independently of `GridWebglLayerController`. A diff-only `GridFrameCache` singleton (keyed by frame URL) deduplicates fetches within the diff pipeline only — it is **not** wired into split-mode `ComparePanel` or `grid-webgl` in v1. Diff inputs are deterministic and decoupled from WebGL controller lifecycle. The WebGL controller remains render-only — no `getDecodedFrame()` or similar data extraction from it.

2. **Loader/state ownership.** `compare.tsx` orchestrates all model, run, and variable selections and all `useModelLoader` instances. `CompareDiffPanel` receives resolved frame metadata and data as props only; it does not own or instantiate loaders. This is a hard constraint.

3. **Screenshot readiness contract (four-step gate).** In diff mode, `data-compare-ready` fires only after all four steps complete **successfully** (fail closed): (1) left frame bytes fetched, (2) right frame bytes fetched, (3) diff compute finished, (4) `MapCanvas` render complete. Any fetch or compute failure withholds the signal. This replaces the current dual-panel gate and is a first-class Phase 2 concern, not an afterthought.

4. **Temperature diff scale.** Use **±15°F** as the default symmetric scale for `tmp2m` and `dp2m`. ±10°F saturates too frequently at long forecast ranges. All scale values are named constants (e.g. in `compare-diff-scales.ts`), not literals in components.

5. **`fetchGridFrameBytes` extraction — resolved.** Phase 2 no longer extracts private fetch logic from `grid-webgl`. Frame bytes are fetched via `compare-diff.ts` + `GridFrameCache` (see decision 1).

---

## Open Decisions — Resolved

All items below are locked for v1 implementation. Do not reopen without explicit product approval.

1. **Variable eligibility strategy.** Allowlist for v1 only. Do **not** use a `same var_key + continuous kind` blocklist. The allowlist is the confirmed final strategy; do not reopen this in Phase 2 without explicit instruction.

2. **Independent run pickers in diff mode.** Keep left and right run pickers fully independent. Do not constrain to matching run cycles. Forecast-hour intersection in the scrubber is the only synchronization constraint.

3. **Auto-scale vs fixed symmetric scales.** Fixed scales only in v1. No dynamic or p95 scaling for any variable family, including precip.

4. **Legend tick marks.** Ticks are always derived from `COMPARE_DIFF_SCALES` using the formula `[-max, -2/3·max, -1/3·max, 0, +1/3·max, +2/3·max, +max]`, rounded to clean integers. For ±15°F this produces `-15, -10, -5, 0, +5, +10, +15`. Do not hand-pick ticks per variable family. Derivation logic lives in `compare-diff-scales.ts` alongside the scale constants.

5. **Diff-mode variable auto-correction UX.** Show an **inline notice** (not a toast) when variables are auto-corrected on entering diff mode. Display **once per session**, not on every toggle.

6. **`GridFrameCache` scope in v1.** Diff-only singleton. Do not modify `ComparePanel`, `grid-webgl`, or any split-mode rendering path in Phase 2. Cache is scoped exclusively to `compare-diff.ts` in this release.

7. **Cache policy.** URL-keyed LRU, capped at **20 entries** or **~50MB** whichever comes first. Short TTL (match browser session). No manual invalidation on run change — frame URLs are already run-scoped, so URL-keying provides implicit invalidation. Let LRU and TTL handle eviction.

8. **Reference grid when LODs tie.** Left model (`lm` side) wins. Hard rule for permalink and screenshot reproducibility.

9. **`rh` on the allowlist.** Dropped from v1 allowlist (palette kind unconfirmed). Add back in v2 once continuous treatment is verified.

10. **Diff compute location.** Dedicated `useCompareDiff` hook (consumed by `compare.tsx`). Do not use inline `useEffect` orchestration for the diff pipeline.

11. **Partial failure behavior.** Fail closed. If either frame fetch fails or diff compute throws, show a full error state on the diff panel. No left-only fallback. `data-compare-ready` does not fire unless all four steps complete **successfully** — unconditional.

12. **Debouncing and cancellation.** **150ms** scrub debounce. Cancel in-flight fetches via `AbortController` on selection change. Use a **request epoch counter** to validate compute results — if the epoch has incremented by the time compute finishes, discard the result even if the fetch was not aborted in time.

13. **Backend diverging colormaps.** Client-only legend for v1. Do **not** add `compare_*_diff` entries to `colormaps.py` in this release.

14. **Web Worker for diff compute.** Main-thread only in v1. Do not scope-creep into Web Worker architecture during Phase 2.

---

## Current State

The compare page is **side-by-side only**:

- Two independent `ComparePanel` instances, each with its own model / variable / run
- Shared forecast hour (intersection of both panels’ frame lists via `CompareScrubber`)
- Synced map viewport; hover tooltip shows **L** and **R** absolute values
- Grid-backed variables only (`useModelLoader` + `MapCanvas` + `GridWebglLayerController`)
- Permalink params: `lm`, `lv`, `lr`, `rm`, `rv`, `rr`, `fh`, viewport — **no mode flag**
- Legends use each variable’s absolute colormap (top-right per panel)

There is **no delta rendering path** today. Grid frames are uint8/uint16 packed values decoded via `scale`/`offset` in the manifest (`grid-sample.ts`, `grid-webgl.ts`).

---

## Product Goal

Match the competitor pattern:

- Toggle: **Side by side** | **Difference**
- Difference mode: **one full-width map** showing `Left − Right`
- **Diverging colormap** (blue = left lower, red = left higher, white ≈ 0)
- Bottom-centered legend: `Difference: ECMWF − GFS (Δ°F)` with symmetric tick marks
- Tooltip shows delta plus optional breakdown

CartoSky’s extra complexity: **different variables per panel are allowed in split mode**. Difference mode needs stricter rules so deltas are 1:1 meaningful.

---

## Approach Options

### A. Client-side diff (recommended for v1)

Fetch both grid frames in the browser, decode to physical units, resample to a common grid, subtract, re-pack, render through existing `MapCanvas` with a synthetic diverging legend.

| Pros | Cons |
|------|------|
| No backend/API work | Must handle resolution mismatch (HRRR vs GFS) |
| Reuses existing loaders + WebGL path | CPU cost per frame change at full LOD |
| Fast to ship incrementally | Requires dedicated frame fetch/cache layer (see Architectural Decisions Locked) |

### B. Server-side diff endpoint

`GET /api/v4/compare/diff?...` returns a pre-aligned diff grid + manifest.

| Pros | Cons |
|------|------|
| Correct resampling (rasterio) | New API surface, caching, auth, tests |
| Lighter client | Higher latency; harder to iterate on UX |
| Natural place for unit normalization | Overkill until client path proves insufficient |

### C. GPU two-texture shader

Extend `grid-webgl` to bind left + right textures and subtract in the fragment shader.

| Pros | Cons |
|------|------|
| Efficient per-pixel | Still needs resampling if dimensions differ |
| No re-encoded grid | Largest change to battle-tested WebGL layer |
| | Diverging LUT is a separate concern anyway |

**Recommendation: A for v1**, with a clean interface so B can replace the compute step later without rewriting UI.

---

## UI / Layout

### Mode toggle

Add a segmented control in the control bar (competitor-style), persisted as `mode=split` (default) or `mode=diff` in the permalink.

### Split mode (unchanged)

Keep current two-panel layout, draggable divider, swap button, per-panel legends.

### Difference mode

```
┌─────────────────────────────────────────────────────────────┐
│ LEFT MODEL │ RIGHT MODEL │ VARIABLE (shared) │ L-RUN │ R-RUN │
│                              [Side by side] [Difference*]    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                    Single full-width map                    │
│                                                             │
│         ┌─────────────────────────────────────┐             │
│         │ Difference: ECMWF − GFS  (Δ°F)      │  ← bottom   │
│         │  -15   -10   -5   0   +5  +10  +15│    legend   │
│         └─────────────────────────────────────┘             │
│  [scrubber]                                                 │
└─────────────────────────────────────────────────────────────┘
```

- Hide split divider and right panel container
- **Single shared variable picker** (competitor model) — left/right model + run pickers stay independent
- Legend moves to **bottom center** (screenshot reference), not top-right
- Swap button **still swaps models** and **inverts the delta sign** (`A−B` → `B−A`)
- Desktop control bar: collapse from 3-column split layout to a single row (or left/right model columns + shared variable column)
- Mobile: already stacked controls; diff mode shows one map (natural fit)

### Styling

Match existing compare chrome:

- Toggle: same pill style as settings (`border-white/[0.09]`, active = `cyan-300` accent like Ag Weather’s green)
- Bottom legend: reuse `MapLegend` with a new **horizontal diverging** variant, or a dedicated `CompareDiffLegend` component
- Palette: simple **blue → white → red** for v1 (competitor); anomaly palettes (`tmp2m_anom` in `colormaps.py`) are more complex purple–white–warm — fine as v2 polish per variable family

---

## Variable Compatibility (critical)

### The rule

**Difference mode requires the same `var_key` on both sides, and that variable must be diff-eligible.**

Do **not** allow arbitrary cross-variable comparison (e.g. `tmp2m` vs `dp2m`, `tmp2m` vs `tmp2m_anom`, `precip_5d` vs `precip_7d`).

### Why

| Comparison | Problem |
|------------|---------|
| Same var, different models | ✅ Intended use case |
| Different vars, same units | Semantically different fields |
| Same semantic field, different var_keys | Rare but possible confusion |
| Categorical / indexed (radar ptype, SPC, RH bins) | Subtraction is meaningless |
| Anomaly vs absolute | Different baselines |
| Different accumulation windows | Incomparable totals |

### Enforcement strategy

**v1: shared variable + allowlist** (locked — see Open Decisions — Resolved #1)

1. In diff mode, show **one Variable** control bound to both panels (`setLVariable` + `setRVariable` together).
2. Variable picker options = **intersection** of:
   - variables supported by **both** selected models (grid-backed)
   - **diff-eligible** set (see below)
3. On entering diff mode with mismatched variables: auto-set right variable to left (if eligible on right); else pick first mutual eligible variable; show **inline notice once per session** if adjustment was needed (not a toast; not on every toggle).
4. If no mutual eligible variable exists: show blocking empty state (“These models have no comparable continuous variables in common”).

**Diff-eligible allowlist (v1)** — conservative, high-value fields:

| Family | var_keys |
|--------|----------|
| Temperature | `tmp2m`, `dp2m`, `tmp850`, `tmp2m_anom`, `tmp850_anom` |
| Wind | `wspd10m`, `wgst10m`, `wspd850`, `wspd300` |
| Height / dynamics | `hgt500`, `hgt500_anom`, `vort500` |
| Moisture | `pwat` |
| Precip | `apcp`, `precip_total`, `snowfall_total`, `*_anom` precip keys |
| Instability | `mlcape` |

**Explicitly excluded:** `rh` (v1 — revisit in v2), `reflectivity`, `ptype_*`, `radar_*`, SPC/WPC outlooks, satellite/RGB, anything with `palette.kind` of `indexed` / `categorical` / `radar_ptype`.

Implementation: `frontend/src/lib/compare-diff-eligibility.ts` with `isDiffEligible(varKey, capability)` and `mutualDiffEligibleVariables(lModel, rModel, capabilities)`.

**Future (v2):** `rh` may return to the allowlist once continuous palette treatment is verified. Do not switch to a `same var_key + continuous kind` blocklist without explicit product instruction.

---

## Colormapping & Scale

### Sign convention

**Δ = Left − Right** everywhere: legend title, tooltip, URL semantics, swap behavior.

- Blue: left **colder / lower / less**
- Red: left **warmer / higher / more**
- White: ~0

### Per-variable symmetric scales

All values are **named constants** in `frontend/src/lib/compare-diff-scales.ts` (not inline in components). Example structure:

```ts
export const COMPARE_DIFF_SCALES = {
  tmp2m: { maxAbs: 15, units: "°F" },
  dp2m:  { maxAbs: 15, units: "°F" },
  // ...
} as const;
```

| Variable family | Display max (±) | Units | Notes |
|-----------------|-----------------|-------|-------|
| `tmp2m`, `dp2m` | **15** | °F | **Locked v1 default** (see Architectural Decisions Locked) |
| `tmp850` | 5 | °C | Smaller typical model spread aloft |
| `tmp*_anom` | 10 | °F | Reuse anomaly magnitudes |
| `wspd10m`, `wgst10m` | 15 | mph | |
| `wspd850`, `wspd300` | 20 | kt | |
| `hgt500` | 20 | dam | |
| `hgt500_anom` | 40 | dam | Align with existing anomaly range |
| `pwat` | 0.5 | in | |
| `apcp` / rate fields | 0.25 | in/hr | |
| Accum totals | 2 | in | Wider for storm totals |
| `mlcape` | 500 | J/kg | |
| Precip anomalies | 2 | in | |

Values outside ±max **clamp** for display (saturation at palette ends) — same as absolute fields. v1 uses fixed scales only (no dynamic/p95 scaling); auto-scale remains a possible v2 enhancement.

### Legend construction

New helper `buildDiffLegend({ leftModel, rightModel, varKey, scale })` → `LegendPayload` with:

- `title`: `Difference: ECMWF − GFS`
- `units`: `Δ°F` (or `Δ` + units)
- `kind`: `continuous` (diverging stops)
- `entries`: symmetric stops derived from `COMPARE_DIFF_SCALES`
- **tick marks:** derived via `deriveDiffLegendTicks(maxAbs)` in `compare-diff-scales.ts` using `[-max, -2/3·max, -1/3·max, 0, +1/3·max, +2/3·max, +max]` rounded to clean integers (e.g. ±15°F → `-15, -10, -5, 0, +5, +10, +15`)

Feed this to `MapCanvas` as `gridLegend` for the diff panel.

### Backend colormaps

**Out of scope for v1.** Legend is built client-side only. Do not add `compare_*_diff` entries to `colormaps.py` in this release. Server-side diverging palettes may be considered in a later release if needed.

---

## Data Pipeline (client-side v1)

### Ownership and orchestration

**`compare.tsx` orchestrates; `CompareDiffPanel` only renders.**

- `compare.tsx` holds all selection state, both `useModelLoader` instances, and delegates diff pipeline orchestration to **`useCompareDiff`** (fetch → decode → resample → subtract).
- `useCompareDiff` calls into `compare-diff.ts` + `GridFrameCache`; it does **not** use inline `useEffect` orchestration in `compare.tsx`.
- `compare.tsx` passes resolved props into `CompareDiffPanel`: active frame URLs/metadata, pre-computed diff grid bytes, synthetic manifest, diverging legend, loading/error flags, map callbacks.
- `CompareDiffPanel` does **not** instantiate loaders, call `useModelLoader`, or trigger fetches on its own.

```
compare.tsx
  leftLoader / rightLoader
       │
       ▼
  useCompareDiff → compare-diff.ts (+ GridFrameCache, diff-only singleton)
       │  fetch frame bytes (cache-keyed by URL)
       │  decode → resample → subtract → encode
       ▼
  CompareDiffPanel (props only)
       │
       ▼
  MapCanvas + diverging legend
```

### Grid data access: Option 2 + `GridFrameCache`

**Resolved** — do not extract fetch logic from `GridWebglLayerController`.

- `compare-diff.ts` owns **all** frame fetching and decoding for diff mode, using a diff-only **`GridFrameCache`** singleton keyed by frame URL.
- The cache is **not** shared with split-mode `ComparePanel` or `grid-webgl` in v1 — it deduplicates fetches only within the diff pipeline (e.g. re-entering diff mode or overlapping URLs in the same session).
- Diff calculation is **not** coupled to render lifecycle — inputs are deterministic from loader-resolved URLs + manifest metadata, regardless of WebGL controller state.
- `GridWebglLayerController` remains **render-only**. No `getDecodedFrame()`, no reading internal frame caches from the controller.

**New modules:**

| Module | Responsibility |
|--------|----------------|
| `frontend/src/lib/grid-frame-cache.ts` | Diff-only URL-keyed LRU cache for raw frame `Uint8Array` bytes (20 entries or ~50MB, session TTL); used exclusively by `compare-diff.ts` in v1 |
| `frontend/src/lib/compare-diff.ts` | `fetchGridFrameBytes(url, cache)`, decode, resample, `computeDiffGrid`, `buildDiffManifest` |
| `frontend/src/lib/compare-diff-scales.ts` | Named `COMPARE_DIFF_SCALES` constants, `deriveDiffLegendTicks()`, diverging stop builders |
| `frontend/src/lib/use-compare-diff.ts` | Dedicated hook: debounce, `AbortController`, epoch validation, readiness steps 1–3 |

### Grid alignment

Models share CONUS bbox (~`-134, 24, -60, 55`) but **different width/height** per LOD.

**Reference grid:** use the **coarser** of the two active LODs (fewer pixels → cheaper, avoids false precision). When LOD resolution ties, **left model (`lm` side) wins** — hard rule for permalink and screenshot reproducibility.

**Resampling:** for each reference pixel center (lon/lat), bilinear sample both source grids using existing `lonLatToGridUv` + `sampleBilinearValue` from `grid-sample.ts`.

**Bbox intersection:** if manifests differ slightly, clip to intersection; pixels outside either extent → nodata/transparent.

### `CompareDiffPanel.tsx` (render-only)

- Accepts resolved diff payload as props from `compare.tsx`
- Renders single `MapCanvas` with synthetic manifest + diff legend
- Emits `onGridFrameReady` / map-ready callbacks upward for screenshot gate step 4
- No loaders, no `useEffect` that fetches frames independently of parent

### Performance notes

- **150ms** scrub debounce on diff compute (via `useCompareDiff`)
- Cancel in-flight fetches with `AbortController` on selection change; discard stale compute via request **epoch counter**
- Use display LOD, not native HRRR resolution
- `GridFrameCache` avoids duplicate fetches when re-entering diff mode or scrubbing within the same session
- Main-thread compute only in v1 — no Web Worker in Phase 2

---

## Tooltip

**Diff mode tooltip** (single map hover):

```
Δ  +3.2 °F
─────────────
L  72.1 °F   (ECMWF)
R  68.9 °F   (GFS)
```

Reuse two `useSampleTooltip` hooks in `compare.tsx`; compute delta client-side. If either sample is nodata, show `Δ —`.

---

## Permalink & State

Extend `ComparePermalinkState`:

```ts
mode?: "split" | "diff";
```

- `mode=diff` restores difference layout
- In diff mode, `lv` and `rv` should stay equal (write both on variable change)
- `handleSwap` in diff mode: swap models/runs **and** invert legend title order (swap already exchanges sides — delta sign flips naturally if convention is always `left − right` after swap)

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| No overlapping forecast hours | Existing scrubber warning (unchanged) |
| One panel loading / error | Diff map shows loading overlay; on fetch/compute failure, **full error state** (fail closed, no partial diff) |
| Enter diff with ineligible variable | Auto-correct to nearest mutual eligible var, or block with message; **inline notice once per session** if auto-corrected |
| Only one model supports var | Var not in picker |
| Categorical var selected in split, switch to diff | Auto-switch variable |
| Different runs, same valid time label | Scrubber shows FH; consider subtitle with both run IDs in diff legend |
| Nodata on either side | Transparent pixel (no false zero) |
| Swap | Models swap → Δ becomes new left − new right |
| Screenshot / TWF share | See Screenshot readiness contract below |
| `?screenshot=1` | Force desktop layout (already exists); render single diff map |
| Playback | Disabled in scrubber today — unchanged for v1 |

---

## Screenshot Readiness Contract

**First-class Phase 2 requirement** — not a minor follow-up.

### Split mode (current)

`data-compare-ready="1"` when **both** panels have fired first frame ready (left + right).

### Diff mode (new four-step gate)

`data-compare-ready="1"` only when **all four** steps complete **successfully** (fail closed — any fetch or compute failure withholds the signal):

1. **Left frame bytes fetched** — `GridFrameCache` holds bytes for the active left frame URL
2. **Right frame bytes fetched** — same for right frame URL
3. **Diff compute finished** — `computeDiffGrid` produced a packed diff grid + synthetic manifest (epoch-valid, not discarded)
4. **`MapCanvas` render complete** — `onGridFrameReady` / map idle equivalent for the diff panel

`compare.tsx` owns the readiness state machine via `useCompareDiff`. `CompareDiffPanel` reports step 4 via callbacks; steps 1–3 are tracked in `useCompareDiff`.

On selection or forecast-hour change, clear `data-compare-ready` until all four steps complete again (same pattern as current `clearCompareReadySignal`).

`backend/app/services/screenshot_service.py` continues to wait on `data-compare-ready === '1'` — no Playwright change required if the attribute semantics are correct.

Update `sharePayload.summary` in diff mode to reflect Δ mode (e.g. include “Difference” and model pair).

---

## Files to Touch (implementation reference)

| File | Change |
|------|--------|
| `frontend/src/pages/compare.tsx` | Mode state, toggle, conditional layout, shared variable in diff, **`useCompareDiff` consumption + readiness gate** |
| `frontend/src/lib/compare-permalink.ts` | `mode` param |
| `frontend/src/lib/grid-frame-cache.ts` | Diff-only URL-keyed LRU frame byte cache (new; 20 entries / ~50MB / session TTL) |
| `frontend/src/lib/use-compare-diff.ts` | Dedicated hook: debounce, abort, epoch, fetch/compute orchestration (new) |
| `frontend/src/lib/compare-diff.ts` | Fetch (via cache), decode, resample, subtract, manifest build (new) |
| `frontend/src/lib/compare-diff-scales.ts` | `COMPARE_DIFF_SCALES`, `deriveDiffLegendTicks()`, diverging stops (new) |
| `frontend/src/lib/compare-diff-eligibility.ts` | v1 allowlist rules (new; `rh` excluded) |
| `frontend/src/components/compare/CompareDiffPanel.tsx` | **Render-only** single diff map (new) |
| `frontend/src/components/compare/CompareDiffLegend.tsx` | Bottom legend (new) |
| `frontend/src/components/compare/CompareModeToggle.tsx` | Segmented control (new) |
| `frontend/src/components/compare/CompareTooltip.tsx` | Diff variant |
| `frontend/src/lib/grid-sample.ts` | Export/resurface bilinear sampling for batch use |
| `backend/app/services/screenshot_service.py` | No contract change if `data-compare-ready` gate is correct client-side |

**Do not modify** `ComparePanel`, `grid-webgl`, or split-mode paths for `GridFrameCache` in v1. **Avoid** changing `GridWebglLayerController` shader unless profiling demands it. **Do not** add data-extraction APIs to the WebGL controller. **Do not** add `compare_*_diff` to `colormaps.py` in v1.

---

## Testing

**Unit tests**

- Eligibility: allowed/blocked var_keys; `rh` excluded in v1
- Diff legend: correct title, symmetric stops, units; `tmp2m` uses ±15°F; ticks via `deriveDiffLegendTicks` (e.g. `-15 … +15`)
- `GridFrameCache`: hit/miss, LRU eviction (20 / ~50MB), URL dedupes network; no split-mode coupling
- `useCompareDiff`: epoch discard, `AbortController` cancellation, 150ms debounce
- `computeDiffGrid`: known 2×2 fixtures, nodata propagation, scale/offset round-trip; left-wins on LOD tie
- Resample: same grid → identity; constant offset → uniform delta
- Readiness gate: attribute not set on partial failure; set only after all four successful steps

**E2E**

- Toggle split ↔ diff, permalink round-trip
- Shared variable sync when entering diff
- Swap inverts visual sign (fixture with asymmetric left/right values)
- Screenshot mode (`?screenshot=1`) sets `data-compare-ready` in diff mode

**Manual**

- ECMWF vs GFS `tmp2m` at long lead time (±15°F saturation check)
- HRRR vs GFS (resolution mismatch stress test)
- Mobile layout

---

## Phased Rollout

### Phase 1 — UX shell (no diff render yet)

Mode toggle, permalink, layout switch, shared variable picker, eligibility guards (v1 allowlist), empty states, inline auto-correction notice (once per session).

### Phase 2 — Diff rendering

Diff-only `GridFrameCache`, `compare-diff.ts` pipeline, `useCompareDiff` hook, render-only `CompareDiffPanel`, diverging legend with derived ticks, loading/error states (fail closed), **four-step screenshot readiness gate**. No changes to `ComparePanel`, `grid-webgl`, or `colormaps.py`. Main-thread compute only.

### Phase 3 — Polish

Diff tooltip, swap semantics, share summary copy, mobile tuning.

### Phase 4 — Optional

Server-side diff API, auto-scale to visible data, playback in diff mode, variable-family palette refinement, `rh` allowlist return, optional wiring of `GridFrameCache` into grid-webgl for broader dedup, backend diverging colormaps.

---

## Summary

Difference mode is a **third view** of the same compare state: one map, `Left − Right`, diverging blue–white–red legend at the bottom, and a **shared variable** restricted to a **v1 allowlist**. **`compare.tsx` orchestrates** loaders and delegates diff work to **`useCompareDiff`** → **`compare-diff.ts` + diff-only `GridFrameCache`**; **`CompareDiffPanel` is render-only**. Compute the delta client-side on the main thread by decoding both grid frames, resampling to a common LOD (coarser wins; left wins on tie), and subtracting in physical units — then render through `MapCanvas` with a synthetic manifest and client-built legend. Split mode stays as-is; `mode=diff` in the permalink wires it in without breaking current shares.
