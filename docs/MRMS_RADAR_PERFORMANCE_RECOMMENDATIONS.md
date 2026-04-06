# MRMS Radar Performance Recommendations

## Based On Competitive Analysis Of tehuanolabs.com

This document captures actionable performance recommendations for CartoSky's MRMS radar implementation. The recommendations are informed by a competitive analysis of tehuanolabs.com's weather map viewer, but they are ranked here based on CartoSky's actual backend, frontend, and delivery-path constraints.

## Executive Summary

CartoSky's WebGL rendering approach is architecturally stronger than the competitor's CPU canvas tile rendering for frame switching, zoom, and pan. The main remaining MRMS problem is not that the renderer is wrong. The problem is that the system still ships, materializes, caches, and uploads very large full-resolution grid frames.

CartoSky serves MRMS frames as 8238 x 4609 uint16 grids, which is about 72.4 MB raw per frame before transfer compression. The competitor gets much smaller payloads by using a much smaller display representation, but that comes with meaningful quality and architectural tradeoffs that CartoSky should not copy blindly.

The highest-leverage improvements are:

1. use uint8 packing for MRMS reflectivity and `mrms_radar_ptype`
2. retune MRMS warming and upload policy after frame size drops
3. add Brotli sidecars as a follow-on compression win
4. keep multi-LOD MRMS delivery on the roadmap as the long-term zoom-aware architecture
5. keep worker-based body consumption profile-gated after the format and compression wins land

## The Numbers

### Per-Frame Transfer Comparison

| Metric | CartoSky | Competitor |
|--------|----------|------------|
| Grid dimensions | 8,238 x 4,609 | 1,400 x 700 |
| Total pixels | 37,968,942 | 980,000 |
| Encoding | uint16 (2 bytes/px) | uint8x2ch (2 bytes/px) for binary; WebP for display |
| Raw frame size | 72.4 MB | 1.9 MB (binary) |
| Transfer size | ~1-5 MB (gzip) | ~117 KB (WebP) or ~1.9 MB (binary) |
| Full timeline (25 frames) | ~25-125 MB (gzip transfer), ~1.81 GB raw | ~2.8 MB (WebP) or ~47 MB (binary) |

### Frame Switch Cost Comparison

| Operation | CartoSky | Competitor |
|-----------|----------|------------|
| If texture/image cached | Single WebGL texture bind (~0ms) | Re-render all visible canvas tiles (per-pixel CPU) |
| If not cached | fetch + response body consumption + texImage2D | fetch + putImageData on each tile |
| Zoom/pan during animation | Free matrix transform of the raster quad | Requires tile redraws |

**Key insight:** CartoSky wins on rendering architecture and steady-state frame presentation. It loses on how expensive each full-resolution MRMS frame still is to deliver and materialize.

## Recommendation 1: Use uint8 Encoding For MRMS Reflectivity And Ptype

**Priority: Critical - strongest near-term lever**

### Problem

The current grid path is still built around uint16 packing for MRMS. That means every pixel costs 2 bytes before compression even though the display path does not need 65,534 usable bins for either MRMS reflectivity or the indexed `mrms_radar_ptype` product.

For reflectivity specifically, the current scale is finer than the display palette needs. A uint8 packing strategy can preserve visible quality while cutting every MRMS frame roughly in half before transfer compression.

### Recommendation

Add a uint8 encoding path for MRMS variables that fit cleanly into 256 bins.

For reflectivity, a representative encoding would be:

- physical value = encoded * 0.5 - 10.0
- range = -10.0 to 117.5 dBZ at 0.5 dBZ steps
- nodata sentinel = 255

For `mrms_radar_ptype`, uint8 is an even cleaner fit because the product uses a small indexed category set rather than a dense continuous value space.

### Why This Comes First

This is the best quality-preserving size reduction available in the current architecture.

It reduces:

1. transfer size
2. response body materialization cost
3. byte-cache pressure
4. texture upload size

Unlike a global resolution downgrade, it does not require giving up full 1000 m detail at regional and metro zoom.

### Implementation

Backend work:

1. add per-variable packing metadata rather than assuming uint16 for every grid product
2. add a uint8 encode path alongside the existing uint16 path in `grid.py`
3. keep manifest `grid.dtype` authoritative so the client can select the correct decode path

Frontend work:

1. add a single-channel uint8 texture path alongside the current RG8 uint16 path
2. branch shader decode by manifest `grid.dtype`
3. preserve existing LUT and transition behavior

### Important Notes

1. The manifest already has a top-level `dtype` field. This work does not require inventing a new per-frame `dtype` field.
2. This is still a moderate change because the renderer currently assumes RG8 uint16 uploads.

### Risk

Low if the packed range and nodata semantics are validated carefully. The main risk is implementation correctness across both WebGL2 and the WebGL1 fallback.

## Recommendation 2: Tune MRMS Warming And Upload Policy

**Priority: High - low-to-medium effort after uint8**

### Problem

The frontend already intends to prefetch the full MRMS timeline nearest-first. The remaining bottleneck is that warming raw bytes and warming GPU textures are still constrained by conservative observed-grid warming limits and by cache capacity.

At current frame sizes, the full 25-frame MRMS timeline cannot remain resident in the existing byte and texture caches. That means the current MRMS experience is constrained not just by fetch ordering but by how expensive each warmed frame remains once materialized.

### Recommendation

After uint8 packing lands and frame cost drops materially:

1. raise the observed-grid texture warm batch size
2. re-evaluate the animation throttle that drops warm batch size during playback
3. consider slightly more aggressive desktop warming while keeping tighter mobile guardrails

### Important Clarification

Do not frame this as "prefetch the whole MRMS timeline" as if that work is missing today. The current client already aims to do that.

The real follow-up recommendation is narrower:

1. keep the existing nearest-first MRMS fetch ordering
2. make warming and upload policy less conservative once per-frame cost is lower
3. confirm with traces that the new policy improves readiness without introducing render stalls

### Implementation

Tune the MRMS-specific warming constants and re-profile:

1. observed-grid warm queue limit
2. observed-grid warm batch size
3. playback-time warm throttling
4. mobile-vs-desktop divergence if required

### Risk

Low to medium. The main risk is over-warming textures during playback and reintroducing frame drops on weaker devices.

## Recommendation 3: Add Brotli Compression For Grid Binaries

**Priority: Medium - useful follow-on, but smaller than uint8**

### Problem

The current backend writes gzip sidecars only. For sparse radar grids with repetitive nodata patterns, Brotli can improve on gzip, but it is still a second-order win compared with halving raw payload size via uint8.

### Recommendation

Generate `.br` sidecars alongside `.gz` and configure nginx to prefer Brotli where supported.

### Why This Is Not First

Improving compression ratio by 15-25% is materially smaller than cutting the raw payload format roughly in half. Brotli should follow the format change rather than replace it.

### Implementation

1. add a Brotli sidecar writer alongside the existing gzip sidecar writer
2. wire nginx to prefer `.br` when the client advertises Brotli support
3. validate Cloudflare and origin behavior before assuming the edge makes this redundant

### Risk

Low. This is operationally straightforward if the deployment layer supports `brotli_static` or an equivalent configuration.

## Recommendation 4: Add Multi-LOD MRMS Delivery

**Priority: High long-term architecture, larger scope**

### Problem

A blanket shift from 1000 m MRMS to 2000 m or 4000 m would improve transfer cost, but it would also permanently degrade zoomed-in detail. That is the wrong trade as a default display policy for CartoSky.

The deeper issue is that the current stack still serves the same full-resolution frame regardless of zoom level.

### Recommendation

Keep full 1000 m MRMS detail for closer zoom levels, but add lower LODs for broader zooms so continental and regional views do not fetch and upload the full-resolution frame unnecessarily.

Example direction:

1. LOD 0: current 1000 m grid for close-in viewing
2. LOD 1: ~2000 m or ~3000 m equivalent for broad regional and continental views
3. choose LOD based on map zoom and viewport needs

### Why This Is Better Than Global Downsampling

It preserves quality where users can actually see the extra detail while removing waste at zoom levels where the full-resolution grid is unnecessary.

### Important Caveat

The manifest schema already has `lods`, but the current repo is still effectively level-0-only in practice. This is real frontend and backend work, not just a one-line backend configuration change.

### Implementation

Backend:

1. generate additional LOD artifacts during publish
2. emit real multi-LOD manifests
3. keep each LOD's grid dimensions and frame metadata internally consistent

Frontend:

1. select LOD based on zoom and viewport
2. make the renderer honor LOD-specific dimensions
3. ensure cache keys and prefetch policy stay correct across LOD transitions

### Risk

Medium to high. This is the cleanest structural solution, but it is broader than uint8 or Brotli and should follow the cheaper wins.

## Recommendation 5: Do Not Reintroduce Pre-Rendered WebP For The Main Viewer

**Priority: N/A - decided against**

### Background

The competitor serves pre-rendered WebP images alongside binary data files per MRMS frame. That can reduce display payload size dramatically, but it also gives up key advantages of CartoSky's current WebGL grid path.

CartoSky previously had a loop WebP pipeline and moved away from it for good reasons.

### Why Not

Reintroducing pre-rendered images for the main viewer would be a quality and architecture regression:

1. lossy image compression introduces artifacts at precipitation boundaries
2. fixed-resolution images cannot match the current zoom behavior of the grid quad
3. palette changes would require re-rendering images server-side
4. display-path and sampling-path divergence increases maintenance surface area
5. the current WebGL path already wins on animation and steady-state frame switching once frames are resident

### When It Might Still Be Useful

Pre-rendered images remain appropriate for non-viewer surfaces:

1. static embeds
2. social previews
3. thumbnails
4. external non-WebGL consumers

That does not justify bringing them back into the main MRMS viewer path.

## Recommendation 6: Keep Worker-Based Body Consumption Profile-Gated

**Priority: Deferred - revisit after uint8 and compression work**

### Problem

Large `response.arrayBuffer()` calls can still block responsiveness, but the current telemetry should not be overinterpreted as pure decompression time. The measured duration includes response body consumption and materialization work more broadly.

### Recommendation

After uint8 packing and any compression changes land:

1. re-profile MRMS body-consumption timings
2. inspect whether UI starvation remains visible on the target browser and device mix
3. only then decide whether to move fetch plus body consumption into a worker

### Why This Is Deferred

If frame size drops enough, worker complexity may no longer pay for itself. If traces still show meaningful main-thread stalls, the worker becomes a justified next step.

## Recommendation 7: Keep The Unified Binary Display And Sampling Path

**Priority: Low - no change recommended for V1**

### Background

The competitor uses separate display and sampling assets for MRMS. CartoSky currently uses one binary artifact per frame for both display and sampling.

### Recommendation

Keep the unified approach for the main viewer. It is cleaner and remains the right default unless CartoSky intentionally introduces a fundamentally different non-grid display path in the future.

## Implementation Priority And Sequencing

### Immediate

1. **Recommendation 1**: add uint8 packing for MRMS reflectivity and `mrms_radar_ptype`
2. re-profile MRMS fetch, body consumption, cache churn, and texture upload after the format change

### Near-Term

3. **Recommendation 2**: retune MRMS warm batch size and playback-time warm throttling
4. **Recommendation 3**: add Brotli sidecars for grid binaries

### Longer-Term

5. **Recommendation 4**: implement multi-LOD MRMS delivery

### Deferred

6. **Recommendation 6**: only consider a worker-based fetch/body-consumption path if post-uint8 traces still justify it

### Decided Against

7. **Recommendation 5**: do not reintroduce pre-rendered WebP for the main viewer
8. **Recommendation 7**: do not split hover/sampling into a second binary path for V1

## Relationship To Existing Performance Plan

This document is specifically focused on MRMS radar performance and is complementary to `PERFORMANCE_SCALING_IMPLEMENTATION_PLAN.md`.

The relationship is:

1. Phase 1B direct grid serving via nginx still benefits MRMS by reducing origin-side cost for binary fetches
2. Phase 3B profiling remains the right gate for any worker-based body-consumption work
3. Phase 4C compression strategy should include Brotli evaluation for MRMS specifically
4. uint8 packing for MRMS is a new recommendation not covered explicitly in the broader plan
5. MRMS multi-LOD delivery is also a new recommendation not covered explicitly in the broader plan

## What The Competitor Gets Right And Wrong

### What they get right

1. Small per-frame transfer cost
2. A manageable total timeline size
3. A willingness to trade detail for load speed

### What they get wrong

1. CPU canvas tile rendering is weaker than CartoSky's WebGL path for frame switching, zoom, and pan
2. They give up the current grid quad's zoom behavior and transition quality
3. Their architecture appears more willing to accept lower-quality display representations to hide transfer cost

### Net assessment

CartoSky has the better display architecture. The competitor has the lighter-weight delivery payload. The right path for CartoSky is not to copy a globally lower-quality display model. The right path is to reduce the cost of the current grid path first, then add zoom-aware delivery where it actually pays off.