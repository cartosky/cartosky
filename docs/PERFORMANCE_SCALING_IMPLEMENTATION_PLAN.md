# CartoSky Performance And Scaling Implementation Plan

## Summary

This plan turns the corrected performance review into an implementation-ready roadmap for backend scalability, frontend responsiveness, and future product growth.

The repo-specific and production-specific conclusions locked by this plan are:

1. The API is already behind nginx in production.
2. nginx is currently proxying API traffic, not directly serving the heaviest grid artifacts.
3. Cloudflare is already caching some high-volume API paths, including grid binaries, boundary tiles, and `capabilities`.
4. The API should be treated as single-process in production until the updated service unit is deployed and the worker count is verified live.
5. The highest-leverage work is now split between:
   - origin efficiency for cold-cache and bypass traffic
   - direct user-facing smoothness on the frontend
6. Frontend optimization should be driven by measured map-viewer hot paths, not by generic React cleanup alone.
7. `App.tsx` size is a scaling and maintainability risk, but it is not the first place to chase runtime wins.

This plan is intended to prepare CartoSky for:

1. NWS Hazards
2. additional variables
3. additional models
4. higher concurrent usage
5. a continued shift toward a best-in-class interactive map viewer

## Validated Current State

These facts are treated as confirmed for planning.

### Production And Deployment

1. `api.cartosky.com` is reverse-proxied through nginx.
2. `cartosky.com` is served directly from nginx using the built frontend bundle.
3. The repo's API systemd unit now starts uvicorn with an explicit worker flag:
   - `deployment/systemd/csky-api.service`
4. The repo's API env example now separates API worker count from scheduler worker count:
   - `deployment/systemd/api.env.example`
5. The production nginx config proxies `/api/v4/` to uvicorn rather than serving grid files from disk.
6. The production nginx config provided during review does not confirm HTTP/2 for the API vhost.
7. Cloudflare cache rules provided during review show active edge caching for:
   - `/api/v4/grid/...`
   - `/tiles/v3/boundaries/v1/...`
   - `/api/v4/capabilities`
8. Cloudflare also explicitly bypasses caching for:
   - `/api/v4/sample`
   - `/tiles/v3/health`
9. No Cloudflare cache rule was provided for `/api/v4/bootstrap`, so bootstrap should still be treated as an origin-sensitive path unless confirmed otherwise.

### Backend Hot Paths

1. Grid files are served through FastAPI `FileResponse`:
   - `backend/app/main.py`
2. GZip middleware wraps the application and applies to responses without an existing `Content-Encoding`:
   - `backend/app/main.py`
3. Contour and vector GeoJSON endpoints parse files into Python objects and then reserialize them:
   - `backend/app/main.py`
4. Boundary MBTiles lookups open and close a SQLite connection per request:
   - `backend/app/services/boundary_tiles.py`
5. `capabilities` and `bootstrap` compute ETags from fully built payloads:
   - `backend/app/main.py`

### Frontend Hot Paths

1. Grid frame uploads and cache bookkeeping happen on the main thread:
   - `frontend/src/lib/grid-webgl.ts`
2. The expensive byte expansion path only applies to the WebGL1 fallback:
   - `frontend/src/lib/grid-webgl.ts`
3. Anchor markers are snapped on every MapLibre `render` event:
   - `frontend/src/components/map-canvas.tsx`
4. Grid frame and texture cache eviction currently scan linearly for the least-recently-used entry:
   - `frontend/src/lib/grid-webgl.ts`
5. PostHog is imported and initialized eagerly at app startup:
   - `frontend/src/main.tsx`
   - `frontend/src/lib/posthog.ts`
6. `frontend/src/App.tsx` is large and operationally dense, which increases future feature risk.

## Goals

### Primary Goals

1. Reduce frame-delivery latency and latency variance for the map viewer.
2. Increase API concurrency headroom without changing product behavior.
3. Remove avoidable Python CPU cost from static-like and cacheable responses.
4. Protect frontend smoothness during scrub, autoplay, and variable switching.
5. Make the system easier to extend as more overlays, variables, and models are added.

### Secondary Goals

1. Improve observability for viewer and API performance before and after changes.
2. Keep rollback simple at each phase.
3. Preserve current API contracts where practical.

## Non-Goals

1. Rewriting the viewer architecture before the current hot paths are fixed.
2. Refactoring all of `App.tsx` before higher-impact delivery-path work lands.
3. Introducing speculative browser-worker complexity without profiling evidence.
4. Changing public route structure unless the performance gain clearly justifies it.

## Success Metrics

Track these before implementation and after every phase.

### Viewer Metrics

1. Time to first visible weather frame after viewer open
2. Time to first visible weather frame after variable switch
3. Frame stutter rate during autoplay
4. Scrub responsiveness under rapid direction changes
5. Main-thread blocking time during grid frame display
6. JS heap growth during extended use

### API And Delivery Metrics

1. p50, p95, and p99 latency for:
   - `/api/v4/grid/...`
   - `/api/v4/bootstrap`
   - `/api/v4/capabilities`
   - contour/vector GeoJSON endpoints
   - `/tiles/v3/...`
2. API process CPU and memory by worker/process
3. nginx upstream response time for proxied API routes
4. Cloudflare edge cache status split by route family:
   - `HIT`
   - `MISS`
   - `BYPASS`
   - `EXPIRED` or `REVALIDATED` if available
5. number of concurrent origin requests the system can sustain without severe tail-latency growth
6. cache hit behavior for immutable assets

### System Health Metrics

1. worker restarts
2. file descriptor usage
3. open SQLite connections for boundary traffic
4. error rate by endpoint

## Phase Overview

The implementation order is intentionally bundled so that each phase produces measurable value and has a coherent rollback story.

### Phase 0

Baseline and measurement hardening

### Phase 1

API concurrency and delivery-path optimization

### Phase 2

Cheap backend CPU wins on dynamic and semi-static endpoints

### Phase 3

Frontend map-viewer hot-path optimization

### Phase 4

Codebase hygiene and scaling readiness

## Phase 0: Baseline And Measurement Hardening

### Why This Phase Exists

Several recommendations are directionally correct but should still be measured in the context of CartoSky's actual traffic and device mix. This phase prevents the team from improving the wrong thing first.

### Scope

1. Establish current baseline measurements for grid delivery, bootstrap, capabilities, contour/vector fetches, and tile fetches.
2. Add any missing viewer timing instrumentation needed to compare phases cleanly.
3. Confirm which user-facing routes are primarily edge-served versus origin-served in real traffic.
4. Record current production settings for:
   - uvicorn/gunicorn process model
   - nginx buffering and compression behavior
   - TLS and HTTP protocol support
   - CDN involvement, if any
5. Confirm whether API HTTP/2 is enabled in the live origin path, but do not over-prioritize this if Cloudflare already terminates browser-facing HTTP/2 or HTTP/3.

### Implementation Notes

1. Use existing `Server-Timing` headers on `bootstrap` as a starting point.
2. Add timing headers or internal timing logs where the current code lacks observability for hot endpoints.
3. Capture Chrome performance traces for:
   - viewer open
   - autoplay for an observed source
   - forecast scrub
   - variable switch
4. Capture nginx access logs with upstream timing fields for the heaviest routes.
5. Capture Cloudflare-side cache status and origin-fill behavior for:
   - `/api/v4/grid/...`
   - `/tiles/v3/boundaries/v1/...`
   - `/api/v4/capabilities`
   - `/api/v4/bootstrap`

### Likely Touchpoints

1. `backend/app/main.py`
2. deployment-level nginx config outside the repo
3. existing observability config under `deployment/observability/`
4. frontend telemetry modules:
   - `frontend/src/lib/rum.ts`
   - `frontend/src/lib/telemetry.ts`

### Exit Criteria

1. A before-state performance snapshot exists and is archived.
2. The team can compare pre-phase and post-phase p95/p99 latencies.
3. The team can split route performance by edge cache status.
4. The team can answer whether API HTTP/2 is actually enabled where it matters.

## Phase 1: API Concurrency And Delivery Path

## Objective

Increase concurrency headroom and remove Python from the hottest static-like traffic path.

This is the highest-priority bundle because it directly affects:

1. multi-user scalability
2. cold-cache and bypass-path smoothness under load
3. CPU efficiency on the API host
4. the future cost of adding more models and variables

### Phase 1A: Make API Worker Count Effective

#### Current Status

Repo wiring complete. Production rollout and live verification still pending.

#### Tasks

1. Deploy the updated systemd unit so worker count is explicitly controlled in production.
2. Verify the chosen worker count is coming from `CARTOSKY_API_WORKERS`, with `CARTOSKY_WORKERS` only acting as a backward-compatible fallback.
3. Decide whether to stay on:
   - `uvicorn --workers N`
   - `gunicorn` with uvicorn workers
4. Document the chosen default and how to scale it on the current host class.

#### Recommended Direction

Prefer an explicit production-grade process model rather than implicit assumptions.

If minimal change is preferred:

1. Keep uvicorn
2. keep the new systemd wiring that reads `CARTOSKY_API_WORKERS`
3. make the worker count explicit in deployment docs and env files

If process supervision and operational controls matter more:

1. switch to gunicorn with uvicorn workers
2. centralize worker and timeout controls there

#### Acceptance Criteria

1. The API runs with more than one worker in production.
2. Process count is visible and unambiguous during operation.
3. p95/p99 latency under concurrent load improves materially.
4. No regressions occur in auth, sampling, or long-lived requests.

### Phase 1B: Serve Grid Artifacts Without Going Through Python

#### Current Status

Repo support complete. Backend can now offload validated grid binaries through nginx via `X-Accel-Redirect`, and the repo includes an example nginx internal location. Production nginx rollout and live verification still pending.

#### Tasks

1. Deploy the nginx internal location for immutable grid artifacts.
2. Keep the frontend contract stable if possible by preserving the current `/api/v4/grid/...` route shape.
3. Make nginx serve files directly from disk using the new internal redirect strategy.
4. Keep authentication-sensitive routes on the proxied API path.
5. Preserve immutable cache behavior for completed runs.

#### Notes

The key outcome is not "use nginx more." The key outcome is:

1. grid requests no longer traverse Python middleware
2. grid requests no longer consume Python CPU for per-request compression
3. grid requests can leverage direct file serving and better protocol behavior

Because Cloudflare already caches `/api/v4/grid/...`, the main value of this phase is now:

1. cheaper origin fills on edge misses
2. lower origin CPU during new-run warmup and long-tail requests
3. better resilience when cache bypasses or purges occur
4. improved performance for any traffic that still reaches origin directly

#### Design Constraints

1. The existing frontend should not need a major routing rewrite.
2. Path validation and traversal safety must remain strong.
3. The deployment should preserve the current cache semantics for immutable artifacts.
4. `latest` resolution may still need API involvement unless the URL contract is adjusted.

#### Optional Subtask

After direct grid serving is in place, evaluate whether build-time precompression of grid artifacts is still needed. It may still be useful, but it should not block the larger win.

#### Acceptance Criteria

1. Completed-run grid artifact requests are served directly by nginx, not proxied through FastAPI.
2. API CPU for grid traffic drops substantially under playback and scrub load.
3. Viewer frame fetch latency variance drops materially.
4. The frontend behavior is unchanged from the user's perspective.

### Phase 1C: Verify Protocol Layer Behavior And Enable Origin HTTP/2 Only If Needed

#### Tasks

1. Confirm whether Cloudflare is already giving clients HTTP/2 or HTTP/3 for `api.cartosky.com`.
2. Confirm whether the nginx origin listener is serving HTTP/2.
3. If origin HTTP/2 is missing and testing shows a meaningful benefit for Cloudflare-to-origin or direct-origin scenarios, enable it at the nginx layer.
4. Verify actual protocol negotiation after deployment.

#### Why It Belongs In Phase 1

This item remains worth checking, but Cloudflare may already solve the browser-facing multiplexing concern. It should no longer be treated as a blind top-priority optimization.

#### Acceptance Criteria

1. The team knows whether the browser-facing protocol path is already optimized at the edge.
2. Origin HTTP/2 is enabled only if it is actually beneficial.
3. Concurrent fetch behavior improves or at minimum does not regress.

### Phase 1 Rollout Notes

1. Land worker-count changes first.
2. Then land direct grid serving in a reversible deployment change.
3. Then validate Cloudflare edge-fill behavior and compare origin load before and after.
4. Only then decide whether origin HTTP/2 work is still worthwhile.
5. If needed, deploy direct grid serving behind a temporary flag or narrow route prefix during validation.

### Phase 1 Exit Criteria

1. API worker count is real and operational.
2. Grid artifact delivery bypasses Python for the steady-state path.
3. Edge-fill and origin-load behavior for grid traffic measurably improve.
4. Protocol-layer questions are resolved and origin HTTP/2 is enabled only if needed.
5. The map viewer shows measurable gains in frame fetch stability and responsiveness.

## Phase 2: Cheap Backend CPU Wins

## Objective

Reduce unnecessary backend work on endpoints that should be lightweight or cache-friendly.

These are lower-risk than Phase 1 and should be bundled after the delivery path is improved.

Because Cloudflare already caches `capabilities` and boundary tiles, the main value of this phase is now more about origin protection and cold-cache efficiency than universal end-user latency.

### Phase 2A: Serve Contour And Vector GeoJSON As Raw Bytes

#### Tasks

1. Replace deserialize-and-reserialize GeoJSON responses with raw-byte responses.
2. Preserve content type correctness.
3. Keep error handling and file validation behavior intact.

#### Acceptance Criteria

1. Contour and vector endpoints stop doing JSON object reconstruction for pass-through files.
2. Endpoint CPU cost drops.
3. Client behavior is unchanged.

### Phase 2B: Replace Expensive Payload-Hash ETags On Bootstrap And Capabilities

#### Tasks

1. Introduce cheaper version tokens for `capabilities`, `bootstrap`, and any similarly structured endpoints where possible.
2. Use file mtimes, manifest mtimes, or stable version strings where appropriate.
3. Allow 304 decisions without fully serializing the entire response body first.

#### Notes

This work matters more as the model catalog, variable catalog, and availability payloads keep growing.

#### Acceptance Criteria

1. `bootstrap` and `capabilities` can short-circuit more cheaply.
2. CPU spent generating ETags drops materially.
3. Cache correctness remains intact.
4. The work is validated against actual Cloudflare behavior so effort is not wasted on a route whose origin load is already negligible.

### Phase 2C: Reuse Boundary MBTiles Connections

#### Tasks

1. Replace per-request SQLite open/close churn with a persistent per-process connection strategy or small connection pool.
2. Validate thread/process safety for the chosen model.
3. Preserve existing gzip behavior for precompressed MVT payloads.

#### Acceptance Criteria

1. Boundary tile requests stop paying connection setup cost per request.
2. Tile latency improves under pan/zoom bursts.
3. No SQLite locking regressions are introduced.
4. Origin-side measurement justifies the change despite Cloudflare caching on this route family.

### Phase 2D: Revisit Manifest Scanning And Availability Assembly

#### Tasks

1. Profile `_scan_manifest_runs` and availability assembly under the current and projected model count.
2. If needed, memoize or cache repeated filesystem scans across short windows.
3. Avoid premature optimization if metrics show this is still minor after earlier phases.

#### Why This Is Last In Phase 2

This is a real scaling concern, but it is still behind grid delivery, GeoJSON pass-through, and cheaper ETags.

### Phase 2 Exit Criteria

1. Semi-static endpoint CPU cost is noticeably lower.
2. Bootstrap and capabilities are cheaper at equal functionality.
3. Boundary tile traffic is more efficient under bursty map interactions when requests reach origin.

## Phase 3: Frontend Map-Viewer Hot Path

## Objective

Improve viewer smoothness by attacking the most credible map-render hot paths first and keeping speculative work profile-driven.

### Phase 3A: Reduce Anchor Marker Work On Every Render

#### Current Status

Implemented in repo. Anchor marker snapping no longer subscribes to every map `render` event. Marker sync and pixel snapping now flow through the existing rAF-throttled anchor sync path and only run on map movement, movement completion, or resize.

#### Tasks

1. Remove or reduce per-render pixel snapping where possible.
2. Re-evaluate whether snapping is needed continuously or only during specific movement states.
3. Prefer throttled or state-aware updates over unconditional `render`-event work.

#### Acceptance Criteria

1. Marker update work no longer runs every render frame unless strictly necessary.
2. Pan and zoom traces show reduced main-thread work.
3. Label alignment remains visually acceptable.

### Phase 3B: Profile Grid Upload And Decode Before Adding Worker Complexity

#### Current Status

Implemented in repo. The grid viewer now records separate client-side timings for grid binary fetch, `response.arrayBuffer()`, texture preparation, texture upload, and the WebGL1 byte-expansion fallback. Admin network diagnostics also break these metrics down by WebGL backend so WebGL2 and WebGL1 behavior can be compared directly.

#### Tasks

1. Profile the current WebGL2 path separately from the WebGL1 fallback.
2. Measure:
   - `response.arrayBuffer()`
   - byte bookkeeping
   - texture upload time
   - fallback byte expansion cost
3. Decide whether a worker-based decode path is justified.
4. If worker work is justified, scope it narrowly to the measured bottleneck.

#### Important Decision Rule

Do not implement a complex worker/offscreen-canvas path solely because it sounds performant. Land it only if traces show meaningful main-thread blocking on target devices.

#### Acceptance Criteria

1. The team has a measured answer on whether worker-based decode/upload work is worth it.
2. If implemented, the change reduces blocking on the actual hot devices and browsers that matter.

### Phase 3C: Replace O(n) Cache Eviction In GridWebglLayerController

#### Tasks

1. Replace linear oldest-entry scans with a proper O(1) LRU structure or a similarly efficient alternative.
2. Preserve the controller's existing semantics around:
   - current frame protection
   - previous texture protection
   - warm-queue awareness

#### Acceptance Criteria

1. Cache eviction no longer scans all entries on insert.
2. Scrub and autoplay remain correct.
3. Memory budgets remain enforced.

### Phase 3D: Revisit Texture Allocation Strategy Only After Profiling

#### Tasks

1. Measure whether texture creation/deletion churn is a real bottleneck on target devices.
2. If justified, add a texture reuse strategy.

#### Notes

Texture pooling is worth considering, but it should not outrank the more obviously hot paths above.

### Phase 3 Exit Criteria

1. The map viewer shows lower main-thread cost during pan, scrub, and autoplay.
2. Any worker-based or pooling work is justified by trace data.
3. No visual regressions are introduced in weather rendering or labels.

## Phase 4: Codebase Hygiene And Scaling Readiness

## Objective

Reduce the odds that future features introduce accidental performance or correctness regressions.

This phase is about team velocity and safer product growth more than immediate frame-rate wins.

### Phase 4A: Break Apart `App.tsx`

#### Tasks

1. Extract feature-local state and effects into focused hooks and modules.
2. Separate concerns such as:
   - bootstrap and selection hydration
   - autoplay and scrub state machine
   - grid playback state
   - anchor batching
   - permalink synchronization
   - modal and share state
3. Keep behavior identical while shrinking the blast radius of future changes.

#### Acceptance Criteria

1. `App.tsx` is materially smaller and easier to reason about.
2. New features can land without touching unrelated viewer logic as often.
3. There is no runtime regression from the extraction work.

### Phase 4B: Lazy-Load Analytics

#### Tasks

1. Defer PostHog loading behind the existing enablement checks.
2. Keep current feature-flag behavior intact.
3. Verify that analytics still initialize correctly when enabled.

#### Acceptance Criteria

1. Analytics code no longer loads eagerly for disabled sessions.
2. Initial app startup JS cost decreases.

### Phase 4C: Reassess Compression Strategy

#### Tasks

1. Revisit Brotli and build-time precompression after Phase 1 and Phase 2 land.
2. Apply them where the measured payload mix supports the effort.

#### Notes

Brotli is a valid optimization, but it should follow the much larger wins from direct grid serving and cheaper backend work.

### Phase 4 Exit Criteria

1. The viewer codebase is easier to extend safely.
2. Startup overhead is slightly lower.
3. Compression work is applied only where it clearly pays off.

## Implementation Sequence

Use this as the intended execution order unless new measurements prove otherwise.

1. Phase 0 baseline and measurement hardening
2. Phase 1A make API worker count effective
3. Phase 1B direct grid serving through nginx
4. Phase 1C verify protocol-layer behavior and only enable origin HTTP/2 if justified
5. Phase 2A raw-byte GeoJSON responses
6. Phase 2B cheaper ETags and 304 path
7. Phase 2C boundary SQLite connection reuse
8. Phase 2D manifest-scan tuning if metrics justify it
9. Phase 3A anchor marker render-path reduction
10. Phase 3B profile-driven grid upload/decode decision
11. Phase 3C cache eviction improvement
12. Phase 3D texture pooling only if proven necessary
13. Phase 4A break apart `App.tsx`
14. Phase 4B lazy-load analytics
15. Phase 4C reassess compression strategy

## Rollback Strategy

Each phase should be independently reversible.

### Deployment-Focused Rollbacks

1. API workers:
   - revert process-model change
2. direct grid serving:
   - restore proxy-to-uvicorn path
3. origin HTTP/2:
   - remove listener change if protocol issues appear

### Code-Focused Rollbacks

1. GeoJSON raw-byte serving:
   - restore existing JSON object return path
2. ETag changes:
   - restore payload-hash ETag logic
3. boundary SQLite reuse:
   - restore per-request connection flow if locking appears
4. frontend hot-path work:
   - revert individual optimization commits rather than bundling too many changes together

## Risks And Watchouts

1. Direct nginx grid serving must not create path traversal or stale-file correctness issues.
2. Multi-worker API deployment must account for any per-process in-memory caches.
3. Boundary SQLite reuse must be implemented in a way that is safe for the chosen worker/process model.
4. Frontend worker-based rendering changes can add considerable complexity if not tightly scoped.
5. Cloudflare caching can hide serious origin inefficiencies if route metrics are not split by edge status.
6. Refactoring `App.tsx` too early can burn time without moving viewer performance enough.

## Definition Of Success

This plan succeeds when CartoSky can:

1. open and animate weather layers faster and more smoothly under real user load
2. absorb additional variables and models without a proportional increase in API pain
3. ship new viewer features with less regression risk
4. demonstrate measurable backend and frontend performance gains phase by phase

## Short Version

If execution needs to stay brutally focused, the first major wins are:

1. make API workers real
2. move grid delivery out of Python
3. validate Cloudflare edge-fill behavior and only then decide whether origin protocol work matters
4. stop wasting CPU on pass-through GeoJSON and expensive ETags where origin still pays the bill
5. then optimize the frontend render path with profiling, not guesswork
