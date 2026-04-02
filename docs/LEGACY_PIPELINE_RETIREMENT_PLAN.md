# Legacy Pipeline Retirement Plan

## Purpose

Define a safe, methodical plan for removing the legacy weather rendering pipeline from the repo while preserving the working grid pipeline and avoiding user-visible regressions.

This plan combines:

- the cautious sequencing and contract-first approach from the internal code review
- the concrete file-level inventory and execution detail from the parallel review

The goal is not a one-shot deletion. The goal is to remove the old system in phases, with each phase independently testable and deployable.

## Current State

The repo currently contains two weather rendering substrates:

- `legacy`
  - server-produced RGBA/value COGs
  - weather tile serving from `backend/app/services/tile_server.py`
  - loop WebP generation and serving
  - frontend loop/tile playback logic in `frontend/src/App.tsx` and `frontend/src/components/map-canvas.tsx`
- `grid`
  - packed `uint16` frame generation in `backend/app/services/grid.py`
  - grid manifest and binary frame serving from `backend/app/main.py`
  - WebGL rendering in `frontend/src/lib/grid-webgl.ts`

Important constraints:

- The shared publish/build pipeline stays for now.
  - `backend/app/services/builder/pipeline.py`
  - `backend/app/services/builder/cog_writer.py`
  - value COGs
  - sidecars
- the grid artifact build currently depends on published value COGs and sidecars.
- Boundary vector tiles are independent of the legacy weather pipeline and must survive.
- RGBA COG output should be kept for now unless and until admin/share/screenshot and other consumers are audited.
- The frontend is still interleaved enough that removing backend contracts before decoupling the UI is risky.

## Design Principles

1. Test before delete.
- Expand regression coverage for grid-only operation before removing runtime code.

2. Decouple contracts before removing implementations.
- If the frontend still consumes legacy-shaped frame or manifest contracts, keep those contracts until the frontend is independent.

3. Prefer small confidence-building PRs.
- Remove dead compatibility and obviously unused paths before touching user-facing behavior.

4. Keep shared artifact production stable.
- Do not start by deleting value COGs, sidecars, or the core publish path.

5. Separate weather rendering retirement from boundaries.
- Boundary vector tile support should not be treated as legacy weather debt.

## Observations From Code Review

- `legacy` is currently overloaded.
  - It acts as a substrate label.
  - It also implies loop support, old frame contracts, and some non-grid viewer behavior.
- Grid-supported selections appear to be using grid rendering even at high zoom now, but this should be verified with network-level assertions before deleting weather tile serving.
- There is low-risk cleanup already available:
  - old co-located `fhNNN.loop.webp` fallback support in `backend/app/main.py`
  - tier-1 WebP compatibility residue
  - duplicated loop pregeneration code paths

## Recommended Phases

### Phase 0: Guardrails And Validation

Objective:
- Prove the current grid-only behavior for supported pairs before removing code.

Work:

- Expand backend tests for grid-supported selections:
  - capabilities
  - bootstrap
  - frames
  - grid manifest
  - grid frame file serving
  - observed products such as MRMS
- Add explicit assertions that supported grid selections do not require legacy loop fields to function.
- Add at least one frontend smoke path or equivalent integration coverage for:
  - grid selection
  - scrubbing
  - playback
  - variable switching
  - latest-run resolution
  - zoom behavior
- Verify at runtime whether grid-supported selections still request:
  - `/tiles/v3/{model}/{run}/{var}/{fh}/...png`
  - `/api/v4/{model}/{run}/{var}/loop-manifest`
  - `/api/v4/{model}/{run}/{var}/{fh}/loop.webp`

Exit criteria:

- We have explicit test coverage for grid-only operation.
- We know which legacy endpoints are truly still exercised by the viewer.

Concrete test plan:

- Extend `backend/tests/test_grid.py` to cover:
  - capabilities exposure for grid-supported pairs
  - bootstrap success for grid-supported pairs
  - frames endpoint success even if loop artifacts are absent
  - grid manifest and grid frame file serving
- Extend `backend/tests/test_mrms_invariants.py` and `backend/tests/test_api_frames_mrms.py` to confirm the observed-data path works in grid-supported mode.
- Extend `backend/tests/test_frames_cache_control.py` to add a grid-supported case that asserts the frontend-relevant frame contract remains sufficient when legacy loop artifacts are absent.
- Add one explicit backend test module focused on grid-only contract viability.
  - Suggested file: `backend/tests/test_grid_only_contracts.py`
  - Focus:
    - supported model/var pairs return usable capabilities
    - bootstrap returns renderable state
    - frames metadata remains sufficient for hour selection and valid-time display
    - no loop artifact presence is required for grid-supported selections
- Add one frontend smoke path.
  - Recommended scope:
    - load a grid-supported selection
    - verify grid manifest fetch succeeds
    - scrub frames
    - play frames
    - switch variables
    - zoom in and out
    - assert whether weather tile PNG or loop endpoints are requested
- If a frontend harness does not yet exist, treat the first smoke path as a small test-infrastructure investment rather than deferring the validation entirely.

### Phase 1: Dead Compatibility Cleanup

Objective:
- Remove the safest legacy residue first without changing intended behavior.

Work:

- Remove the old co-located published `fhNNN.loop.webp` fallback path from `backend/app/main.py`.
- Remove tier-1 WebP compatibility knobs and dead code if unused.
- Consolidate duplicate loop pregeneration helpers where practical so there is one obvious removal surface later.

Primary surgical targets:

- `backend/app/main.py`
  - `_legacy_loop_webp_path`
  - `_legacy_loop_webp_url`
  - the legacy-path branch inside `_resolve_existing_loop_urls`
  - the legacy fallback branch in `get_loop_webp`
  - `_static_loop_webp_url` if it is confirmed unused after the fallback removal
- tier-1 compatibility residue in:
  - `backend/app/main.py`
  - `backend/app/services/scheduler.py`
  - `backend/app/services/publish_utils.py`
  - env example files and tests that preserve tier-1 compatibility only
- duplicate loop pregeneration logic in:
  - `backend/app/services/scheduler.py`
  - `backend/app/services/publish_utils.py`

Likely targets:

- `backend/app/main.py`
- `backend/app/services/scheduler.py`
- `backend/app/services/publish_utils.py`
- tests that only exist to preserve retired compatibility

Exit criteria:

- Dead fallback behavior is gone.
- Active runtime behavior is unchanged.

### Phase 2: Contract Decoupling For Grid-Supported Selections

Objective:
- Make the frontend and API contracts truly independent of the legacy loop pipeline for grid-supported pairs.

Work:

- Stop relying on loop-specific fields for grid-supported selections in:
  - `frontend/src/App.tsx`
  - `frontend/src/lib/api.ts`
  - `frontend/src/components/map-canvas.tsx`
- Remove implicit coupling between substrate selection and loop availability.
- Keep existing fields in backend responses until the frontend no longer needs them.
- Prefer simplifying existing fields and logic over adding a large new capability system just for transition management.

Specific focus:

- untangle `legacy` as a label from actual loop/tile runtime dependencies
- ensure grid-supported selections can boot, render, scrub, and play without loop manifests or loop frame URLs
- verify that high-zoom behavior for grid-supported selections is still correct after cleanup

Target state for this phase:

- The frontend still fetches `frames` for grid-supported selections.
  - Reason:
    - forecast-hour lists
    - valid-time labels
    - sidecar-backed metadata
    - keeping bootstrap and viewer state changes incremental rather than rewriting them all at once
- For grid-supported selections, the frontend does not fetch or depend on:
  - `loop-manifest`
  - `loop_webp_url`
  - `loop_webp_tier0_url`
- Grid-supported selections derive weather rendering solely from:
  - capabilities
  - frames metadata
  - grid manifest
  - grid frame binaries
- Unsupported or intentionally non-grid selections may continue using legacy paths until later phases retire them.

Recommended implementation shape:

- In `frontend/src/App.tsx`, split the current meaning of `legacy` into narrower concepts:
  - supports grid rendering
  - supports loop playback
  - supports weather tile rendering
- Do not introduce a large new public API surface if existing capabilities can be interpreted more narrowly.
- Gate loop-manifest fetching behind actual loop support rather than substrate labels that also cover non-loop behavior.
- Keep `frames` responses stable through this phase, but make loop URL fields ignored for grid-supported selections.
- Only remove loop URL fields from backend responses after the frontend has shipped and been verified without them.

Open design question to answer during this phase:

- whether grid-supported selections should remain dual-described in capabilities during transition, or whether they should stop advertising `legacy` once loop/tile dependencies are truly gone

Recommended answer:

- keep capability changes minimal until runtime behavior is decoupled
- then remove `legacy` advertisement only after verification proves the frontend no longer depends on it

Exit criteria:

- For grid-supported selections, the frontend no longer depends on loop contracts.
- Grid-supported selections still render correctly across models and products in scope.

### Phase 3: Remove Loop WebP Pipeline

Objective:
- Retire the loop generation and serving path once nothing depends on it.

Work:

- Remove loop WebP generation modules and scripts:
  - `backend/app/services/builder/loop_webp.py`
  - `backend/scripts/generate_loop_webp.py`
- Remove loop pregeneration from:
  - `backend/app/services/scheduler.py`
  - `backend/app/services/publish_utils.py`
  - `backend/app/services/mrms_publish.py`
  - `backend/app/services/mrms_poller.py`
- Remove loop serving and manifest routes from `backend/app/main.py`.
- Remove loop-specific URL fields from frame responses when safe.
- Trim loop-only helpers from shared utility modules such as:
  - `backend/app/services/render_resampling.py`

Tests to revisit:

- `backend/tests/test_loop_webp_value_render.py`
- `backend/tests/test_loop_manifest_bbox.py`
- `backend/tests/test_frames_cache_control.py`
- `backend/tests/test_scheduler_catchup.py`
- MRMS publish and poller tests with loop-specific assertions

Config and docs to revisit:

- `CARTOSKY_LOOP_*` env vars
- loop sections in `README.md`
- loop routing references in `docs/NGINX_V3.md`

Exit criteria:

- No weather loop artifacts are generated or served.
- Grid-supported selections still work.
- No backend or frontend code still expects loop manifests or loop URLs.

### Phase 4: Frontend Legacy Rendering Removal

Objective:
- Strip the remaining legacy weather playback and rendering code from the frontend.

Recommended sub-phases:

#### 4a. Simplify substrate selection

- Collapse substrate decision logic once grid is the only live weather substrate.
- Remove permalink substrate override support if no longer needed.
- Simplify capability reads related to render substrates.

#### 4b. Remove loop playback and decode machinery

- Remove loop manifest fetching.
- Remove WebP decode caches and playback scheduling.
- Remove loop display state and related refs.

#### 4c. Remove tile playback and tile buffer bookkeeping

- Remove weather tile swap logic.
- Remove weather prefetch tile logic.
- Remove legacy tile URL helpers if no longer used for weather rendering.

#### 4d. Simplify `map-canvas`

- Remove dual weather raster buffers.
- Remove weather loop image/canvas plumbing.
- Keep only what is required for:
  - basemap
  - boundaries
  - grid layer
  - overlays that still matter

#### 4e. Clean up telemetry and config

- Remove legacy weather rendering flags and telemetry that no longer apply.
- Keep operational or non-render telemetry that is still useful.

Primary files:

- `frontend/src/App.tsx`
- `frontend/src/components/map-canvas.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/config.ts`
- `frontend/src/lib/tiles.ts`
- `frontend/src/lib/permalink-read.ts`
- `frontend/src/lib/permalink.ts`

Exit criteria:

- The frontend no longer contains weather loop or weather tile rendering code.
- Grid rendering is the only weather visualization path.

### Phase 5: Tile Server Retirement Or Reduction

Objective:
- Retire weather tile serving once it is confirmed unused, while preserving boundary vector tiles.

Work:

- Verify whether grid-supported weather selections still ever request weather PNG tiles.
- If no longer needed, remove weather tile serving from `backend/app/services/tile_server.py`.
- Preserve boundary tile support by either:
  - migrating boundary endpoints into `backend/app/main.py`, or
  - keeping a slimmed-down boundary-only tile server

Recommended decision:

- Migrate boundary endpoints into `backend/app/main.py` and retire the standalone tile server service if weather PNG serving is no longer needed.

Why this is the preferred end state:

- removes an entire deployable service
- simplifies nginx and systemd footprint
- keeps boundary support under the same application entrypoint as the rest of the public API
- avoids carrying a boundary-only service unless there is a clear operational reason to keep it

Weather tile targets:

- `/tiles/v3/{model}/{run}/{var}/{fh}/{z}/{x}/{y}.png`
- value-render weather PNG helpers in `tile_server.py`
- tile-only resampling helpers such as `rio_tiler_resampling_kwargs` and any remaining tile-server-only render-resampling functions

Boundary endpoints that must survive:

- `/tiles/v3/boundaries/v1/tilejson.json`
- `/tiles/v3/boundaries/v1/{z}/{x}/{y}.mvt`

Deployment/config surfaces:

- `deployment/systemd/csky-tile-server.service`
- `deployment/systemd/tile-server.env.example`
- nginx routing and related docs

Exit criteria:

- Weather tile serving is gone or intentionally retained with a clear reason.
- Boundary tiles continue to function with the same public contract.

### Phase 6: Final Backend And Config Simplification

Objective:
- Remove transition-era substrate/config complexity once the legacy weather path is gone.

Work:

- Simplify `backend/app/config/__init__.py`:
  - grid build toggles
  - substrate helpers
- Simplify capability serialization in `backend/app/models/serialization.py`.
- Simplify tests that currently assert legacy-plus-grid pair behavior.
- Remove outdated migration notes and feature flags from docs and examples.

Examples:

- `render_substrates`
- `default_render_substrate`
- `CARTOSKY_GRID_WORKERS`
- allowlist/denylist env vars if no longer needed

Exit criteria:

- Grid is the only supported weather pipeline in code, config, docs, and tests.

## Suggested First PR

The safest first PR should do three things only:

1. Add grid-only regression coverage.
2. Remove dead compatibility:
   - old published `fhNNN.loop.webp` fallback
   - tier-1 WebP residue if unused
3. Add runtime verification or assertions showing whether grid-supported selections still call legacy weather endpoints.

Why this first:

- It reduces risk for every later phase.
- It removes complexity immediately without changing the intended product path.
- It answers the open question about whether weather tiles are still live dependencies.

## Deployment Sequencing

Some phases require coordinated backend and frontend deploy behavior.

### Backend-first safe phases

- Phase 0
- Phase 1

These should be safe to deploy independently.

### Coordinated phases

- Phase 2
  - frontend changes should land before or alongside backend contract tightening
- Phase 3
  - do not remove loop endpoints until the frontend has already shipped without needing them
- Phase 5
  - boundary routing changes should be deployed with nginx and service changes in a coordinated window

Practical rule:

- do not delete a backend endpoint if a currently deployed frontend may still call it
- prefer one deploy cycle where the frontend ignores an old field or route before the backend removes it

## Rollback Guidance

### Phase 0

- rollback: revert tests or harness changes if they cause instability
- production risk: minimal

### Phase 1

- rollback: revert and redeploy
- production risk: low because only dead compatibility should be removed

### Phase 2

- rollback:
  - revert frontend contract-decoupling changes
  - keep backend contracts intact until the frontend is verified
- deployment note:
  - do not remove backend loop fields or routes during this phase

### Phase 3

- rollback:
  - revert backend loop deletions and redeploy
  - if needed, temporarily restore loop-serving routes before re-enabling frontend usage
- deployment note:
  - only begin this phase after at least one successful frontend deploy cycle without loop dependency

### Phase 4

- rollback:
  - revert frontend legacy-render removal changes
  - if substrate override or transition logic still exists at this point, keep it available until the phase is validated
- production risk: highest of all phases

### Phase 5

- rollback:
  - restore weather tile serving or keep the tile server alive until boundary migration is validated
  - revert nginx/systemd changes together

### Phase 6

- rollback: revert config simplifications if any deployment assumptions were removed too early

## Phase 2 Pre-Work Checklist

Do not start Phase 2 until all are true:

- Phase 0 verification has answered whether grid-supported selections still request weather tiles or loop endpoints
- the first frontend smoke path exists and passes
- the dead compatibility cleanup from Phase 1 has shipped cleanly
- the team agrees on the target contract shape:
  - `frames` retained
  - loop fields ignored by grid-supported selections
  - grid manifest becomes the only weather-rendering manifest for those selections
- deployment order for frontend and backend changes is understood

## Risk Assessment

### Low Risk

- test hardening
- dead compatibility cleanup
- removing co-located loop fallback support
- pruning unused tier-1 settings

### Medium Risk

- loop contract decoupling
- loop pipeline deletion after decoupling
- boundary endpoint migration

### High Risk

- large `App.tsx` cleanup
- large `map-canvas.tsx` cleanup
- deleting weather tile serving before confirming it is unused

## Verification Strategy

Each phase should include both automated and manual verification.

Automated:

- backend `pytest`
- targeted frontend/integration coverage where available
- contract assertions for capabilities, frames, manifests, and playback prerequisites

Manual:

- HRRR
- GFS
- NAM
- NBM
- MRMS

For each representative model/variable pairing, verify:

- initial map load
- latest-run resolution
- variable switch
- frame scrubbing
- playback
- observed vs forecast behavior where applicable
- high-zoom behavior
- boundary visibility
- permalink restore

## Explicit Non-Goals For Early Phases

- removing value COGs
- removing sidecars
- removing the core publish/build pipeline
- removing boundary vector tiles
- removing RGBA COG generation before downstream consumers are audited

## Open Questions To Resolve During Phase 0

- Do any grid-supported selections still issue weather tile requests at runtime?
- Do any grid-supported selections still rely on loop manifest or loop frame URLs even if the user sees only grid output?
- Are there any non-viewer consumers of loop URLs or loop manifests that need migration?
- Which RGBA COG consumers still need to remain after legacy weather rendering is gone?

## Summary

The safest path is:

1. strengthen guardrails
2. remove dead compatibility
3. decouple frontend and API contracts from legacy loop behavior
4. delete the loop pipeline
5. remove the remaining frontend legacy rendering code
6. retire weather tile serving while preserving boundaries
7. simplify config, tests, and docs

This sequencing preserves momentum while minimizing the chance of hidden regressions during the migration to a grid-only weather rendering stack.
