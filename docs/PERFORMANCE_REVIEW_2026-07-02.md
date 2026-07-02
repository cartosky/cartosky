# Map Viewer Performance Review — 2026-07-02

Scope: animation/scrubbing performance, product load time (CPC/SPC 20–30s reports), plus
general findings. All prod measurements taken 2026-07-02 against `api.cartosky.com`.
Findings are labeled **[measured]** (verified against prod or code this review) or
**[code-read]** (traced in code, timing estimated).

---

## 1. CPC 20–30s load: root cause found **[measured]**

**Every CPC outlook vector payload is ~32.4 MB of raw GeoJSON (~5.5 MB gzipped on the wire).**

Measured from prod (`/api/v4/cpc/latest/{var}/0/vectors/primary`):

| Variable | Wire (gzip) | Raw |
|---|---|---|
| cpc_610_temp | 5.50 MB | 32.41 MB |
| cpc_610_precip | 5.49 MB | 32.36 MB |
| cpc_814_temp | 5.50 MB | 32.36 MB |
| cpc_1m_temp | 5.49 MB | 32.34 MB |
| cpc_3m_temp | 5.49 MB | 32.33 MB |

The backend is NOT slow: `Server-Timing: vector_total;dur=44.0` (44 ms), and Cloudflare
caches the response (`s-maxage=86400`, observed `cf-cache-status: HIT`). The entire
20–30 s is client-side cost of the payload:

1. **Download**: 5.5 MB gzipped → 1–4 s on good broadband, 20 s+ on slow/mobile links.
2. **Inflate + `response.json()`** of 32 MB on the main thread
   ([map-canvas.tsx:2842](frontend/src/components/map-canvas.tsx:2842)) → 1–3 s freeze.
3. **MapLibre `setData`** → structured-clone of the 32 MB object to the worker +
   geojson-vt tiling of the geometry → multi-second.

Why the payload is so big: the file has only **24 features but 309,538 vertices**, with
coordinates at full float64 precision (e.g. `-124.33301935060878` — 14+ decimals,
sub-millimeter). The CPC poller stores NOAA MapServer geometry verbatim; there is no
simplification or coordinate rounding anywhere in
[cpc_poller.py](backend/app/services/cpc_poller.py) / [cpc_outlook.py](backend/app/services/cpc_outlook.py).

### Fix (high confidence, measured on real data)

Simplify + round at **publish time** in the poller:

- `shapely.simplify(0.01°, preserve_topology=True)` (≈1 km tolerance — invisible for
  national-scale outlook contours) + round coordinates to 4 decimals (≈11 m).
- Measured result on the live cpc_610_temp payload: **977 KB raw / 287 KB gzipped** —
  a 95% wire reduction and 97% parse reduction.
- Expected user-visible result: CPC load drops from 20–30 s to ~1–2 s (dominated by the
  normal request waterfall).

Notes:
- Republish/backfill existing runs (or let the next poll cycle replace them) and purge the
  Cloudflare cache for `/vectors/` URLs after deploying.
- SPC data is already tiny (see below) but running the same simplify+round pass on all
  vector publishes is cheap insurance for future vector products (NWS hazards etc.).

### Side note: CPC run ID age is by design, not a bug **[corrected]**

`/api/v4/cpc/runs` shows the latest run as `20260618_1245z`, which initially looked like
a stalled poller. It is not: the bundle run_id is intentionally derived from the *oldest*
product issue time (see the `build_cpc_products_fingerprint` docstring,
[cpc_outlook.py:699-706](backend/app/services/cpc_outlook.py:699)) — June 18 matches the
Three-Month outlook's actual last issuance on the CPC site, and the daily 6-10/8-14 day
products republish fresh data into the same run_id via the fingerprint check. No action
needed.

## 2. SPC: could not reproduce today **[measured]**

All SPC vector payloads are small (extended 1.6 KB, tornado_prob 2.5 KB, hail 13.7 KB,
wind 25 KB, convective 8.5 KB) and every endpoint in the chain measured fast even on
Cloudflare cache MISS (vectors ~180 ms, runs 133 ms, manifest 135 ms, frames 135 ms).

Likely explanations for the reported slowness:
- Perceived together with CPC in the same session (both live in the same picker area).
- Intermittent origin latency — the May 2026 audit found multi-GiB swap pressure on the
  origin (unbounded GDAL block cache + glibc arenas). An origin in swap makes *any*
  CDN-miss request take tens of seconds.

**Recommendation:** add a per-product RUM metric (`product_first_paint_duration` tagged
by model) so the next "X is slow" report comes with data. The RUM plumbing already exists
([rum.ts](frontend/src/lib/rum.ts), `first_overlay_visible_duration`).

---

## 3. Animation & scrubbing

Your instinct is right: the pipeline works but is convoluted, and the two highest-impact
defects are wiring issues, not architecture.

### 3a. Scrub prefetch chases the painted frame, not the user **[measured]**

[App.tsx:1994-2031](frontend/src/App.tsx:1994) — `gridPrefetchPivotHour` falls back to
`requestedGridDisplayHour`/`resolvedGridDisplayHour` (the frame currently painted) during
a live drag. `scrubCommitIntent` only overrides the pivot *after release*, and only when
the jump exceeds `SCRUB_COMMIT_NEIGHBOR_WINDOW`. So during a fast scrub the prefetcher
is always loading frames around where the user *was*, guaranteeing misses at the target.
The specific pivot behavior flagged in the May review is still present, though the
surrounding system has evolved since (partial `scrubCommitIntent` wiring, idle warmup,
and a `scrubColdPrefetchBoost` during cold scrubs at
[App.tsx:4873](frontend/src/App.tsx:4873)) — so in practice the lag mostly bites during
the not-yet-warm window after a product switch.

**Fix:** while a scrub is active, pivot prefetch on `scrubRequestedHour` (the live slider
target, direction-aware via `scrubDirectionRef`). On release, immediately promote the
committed frame to top fetch priority and abort unprotected in-flight prefetches.

### 3b. Preloading all frames: extend the existing warmup to 100% **[measured]**

*(Corrected after external review: an idle warmup already exists — this is an extension,
not a new architecture.)*

The viewer already warms frames in the background: after first paint, when the user is
idle (not scrubbing/playing), it preloads until **70% of the run is ready**
(`PRELOAD_START_RATIO = 0.7`, [App.tsx:1963-1993](frontend/src/App.tsx:1963)), with
stall detection, and there is a `scrubColdPrefetchBoost` during cold scrubs
([App.tsx:4873](frontend/src/App.tsx:4873)). So the recommendation is:

1. **Raise the warm target to 100% for forecast products that fit budget**, and keep
   warming (at reduced concurrency) during playback instead of pausing.
2. **Make the threshold product-aware.** A typical forecast run is ~65 frames × ~8 MB ≈
   520 MB, which fits the 768 MB CPU frame budget but slightly exceeds the 512 MB GPU
   texture budget ([grid-webgl.ts:12-15](frontend/src/lib/grid-webgl.ts:12)) — so full
   warm should target the frame cache and let texture residency stay LRU, or compute the
   cutoff per product from frame size × count against both budgets.
3. High-res observed products (MRMS-class, ~50 MB/frame) can't fully fit; keep windowed
   direction-aware prefetch there and on mobile.

Once a run is fully warm, autoplay and scrubbing never touch the network, and the
scrub-pivot fix (3a) only matters for the not-yet-warm window and capped products.

### 3c. Playback loop gates on texture readiness **[code-read]**

The rAF loop ([App.tsx:3942-3993](frontend/src/App.tsx:3942)) polls `gridReadyHourSet`
every frame and stalls (then skips after `AUTOPLAY_STALL_SKIP_MS`, 200–300 ms for
high-res) when the next texture isn't ready. With full-run warm (3b) this stops
happening; independently, the loop could be event-driven (frame-ready callback from the
grid controller) instead of 60–120 Hz polling — measurable CPU/battery win on mobile.

### 3d. React render overhead during scrub **[measured]**

No `React.memo` on `BottomForecastControls`, `MapCanvas`, or `MapLegend` (grep confirms
zero memo usage). Slider drags re-render the whole App tree at input rate (~60/s).
Autoplay is throttled (`AUTOPLAY_UI_SYNC_MS` = 120 ms) so it's ~8 renders/s — fine — but
scrubbing pays full price.

**Fix:** memoize the heavy children + stabilize their callback props. Cheap, measurable
with React Profiler.

### 3e. Permalink sync during autoplay **[code-read]**

[use-permalink-sync.ts:80-104](frontend/src/lib/use-permalink-sync.ts:80) calls
`history.replaceState()` every ~200 ms while playing. Skip URL sync while
`isPlaying`, flush once on stop.

### 3f. Minor
- WebGL1 fallback expands uint16→RGBA in a main-thread loop
  ([grid-webgl.ts:~1943](frontend/src/lib/grid-webgl.ts:1943)) — 50–200 ms stall per
  frame on old Safari/mobile. Low priority (WebGL2 is dominant); fix by moving to a
  worker if RUM shows WebGL1 share matters.
- [grid-frame-cache.ts](frontend/src/lib/grid-frame-cache.ts) is only used by
  compare-diff v1 — not part of the main pipeline; ignore/remove when convenient.

---

## 4. Initial load (time-to-first-frame)

The May capabilities minute-bucket etag bug is **fixed** — availability now sits behind a
10 s TTL cache (`CARTOSKY_CAPABILITIES_AVAILABILITY_CACHE_TTL_SECONDS`, main.py:161-166).
Measured capabilities today: 315–380 ms cold, 120 KB. Remaining issues:

### 4a. Frontend refuses to cache capabilities **[measured]**

`fetchJson()` hardcodes `cache: "no-store"` ([api.ts:~424](frontend/src/lib/api.ts:424)),
and `fetchCapabilities()` has no ETag/localStorage reuse — while `fetchRegionPresets()`
directly above it ([api.ts:483-536](frontend/src/lib/api.ts:483)) already implements the
exact localStorage + `If-None-Match` pattern needed. Every page load pays the full
~350 ms + parse.

**Fix:** replicate the region-presets caching pattern for capabilities. ~Zero risk,
saves 300–600 ms on every warm visit.

### 4b. Startup waterfall **[measured — corrected]**

*(Corrected after external review: the original claim of a fully serial waterfall was
stale.)* Capabilities + region presets are already fetched in parallel
([App.tsx:3146](frontend/src/App.tsx:3146)), and runs + manifest are already fetched in
parallel via `Promise.all` ([App.tsx:3325](frontend/src/App.tsx:3325)) with an
optimistic "latest" manifest fetch. The remaining serialization is
capabilities → (runs ‖ manifest) → grid-manifest → first frame (~3 dependent stages).
The main remaining lever is booting from the existing `/api/v4/bootstrap` endpoint
(resolved run + capabilities in one round trip) — worth evaluating, lower priority than
originally stated.

### 4c. Bundle & basemap: mostly healthy **[code-read]**

manualChunks split is good (maplibre/clerk/radix/recharts separated); auth is
non-blocking. Two small wins: preload the Stadia glyph range in `index.html`
(city labels currently wait ~300 ms on fonts), and consider splitting grid-webgl out of
the viewer chunk for non-grid landings.

---

## 5. Backend serving

One correction to a common misconception: the hot-path endpoints are sync `def`, which
FastAPI runs in the **anyio threadpool (40 threads)** — they do not block the event loop.
The real risk is **pool exhaustion** under concurrent scrub load. Grid frame bytes are
already offloaded (accel redirect + Cloudflare immutable caching, confirmed live May
2026), which removes the worst weight from the pool. Remaining hygiene, in priority
order:

1. **Cache `_scan_manifest_runs()`** ([main.py:2845-2867](backend/app/main.py:2845)) —
   every `/runs` and every availability rebuild does `glob("*.json")` + one `is_dir()`
   stat per run, per model, per request. A 30–60 s TTL cache (or invalidate-on-publish)
   removes filesystem scans from the request path entirely.
2. **Raise `CARTOSKY_JSON_CACHE_RECHECK_SECONDS`** from 1 s to 10–30 s (main.py:159) —
   manifests are re-stat'd every second per file today.
3. **GDAL dataset cache** ([sampling.py:54-74](backend/app/services/sampling.py:54)):
   16 entries with FIFO eviction thrashes under concurrent sampling across frames. Bump
   to 64 + LRU (`OrderedDict.move_to_end`). Also set `GDAL_CACHEMAX` (e.g. 256 MB) in
   the service env — ties directly into the May swap-pressure finding; the block cache
   is currently unbounded.
4. **Bound the manifest caches** (`_manifest_cache`/`_grid_manifest_cache` are unbounded
   dicts) — slow leak under many models/runs.
5. Grid-manifest deep validation ([main.py:5218-5320](backend/app/main.py:5218)) stats
   every LOD frame per request (~45 stats); fine today at 60 s TTL, cache if it shows in
   Server-Timing.

---

## 6. Prioritized roadmap

*(Reordered after external review: client caching moved ahead of the warmup work —
smaller and lower risk; runs+manifest parallelization removed — already implemented.)*

| # | Change | Fixes | Effort | Expected win |
|---|---|---|---|---|
| 1 | Simplify+round CPC/SPC vector GeoJSON at publish, republish + CF purge | CPC 20–30 s load | S | 20–30 s → ~1–2 s **[measured 95% payload cut]** — ✅ implemented 2026-07-02 |
| 2 | Capabilities ETag/localStorage caching (copy region-presets pattern) | Load time | S | −300–600 ms every visit — ✅ implemented 2026-07-02 (304 verified: 300 B vs 120 KB) |
| 3 | Scrub prefetch pivots on live scrub target; promote committed frame; reduce scrub-time React churn (memo heavy children) | Scrub lag/jank | S–M | Biggest scrub-feel win |
| 4 | Extend existing idle warmup to product-aware full-run warm (70% → 100% where CPU+GPU budgets allow) | Animation stutter, scrub misses | M | Zero-network playback after warm |
| 5 | Skip permalink sync during autoplay | Autoplay hiccups | S | Fewer main-thread stalls |
| 6 | Backend: cache manifest scans, JSON TTL 1→10 s, GDAL LRU 64 + GDAL_CACHEMAX | Origin tail latency, swap pressure | S | Removes FS scans from hot path |
| 7 | Per-product first-paint RUM metric | Diagnosing reports like "SPC is slow" | S | Observability |
| 8 | Evaluate booting viewer from /bootstrap | Load time | M | ~1 fewer dependent round trip |

Items 1–5 are independent and individually shippable. Item 4 is the strategic one — a
fully warm run makes playback effectively local.
