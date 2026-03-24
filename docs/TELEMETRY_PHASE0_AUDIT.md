# Telemetry Phase 0 Audit

## Purpose

This document captures the current telemetry footprint in CartoSky at the start of the telemetry overhaul. Its goals are:

- inventory the existing custom telemetry surface
- classify each event into its future owning system
- identify stale or misleading telemetry paths
- establish the current admin pages that must be preserved, replaced, or demoted

This is an audit artifact for Phase 0 of [TELEMETRY_OVERHAUL.md](./TELEMETRY_OVERHAUL.md).

## Current Telemetry Surface

### Frontend emitter

The current frontend telemetry entrypoint is [frontend/src/lib/telemetry.ts](../frontend/src/lib/telemetry.ts).

- `trackPerfEvent(...)` posts to `/api/v4/telemetry/perf`
- `trackUsageEvent(...)` posts to `/api/v4/telemetry/usage`
- payload enrichment adds:
  - `session_id`
  - `device_type`
  - `viewport_bucket`
  - `page`

The main producer of telemetry events is [frontend/src/App.tsx](../frontend/src/App.tsx).

### Backend ingestion and storage

The current backend ingestion endpoints live in [backend/app/main.py](../backend/app/main.py):

- `POST /api/v4/telemetry/perf`
- `POST /api/v4/telemetry/usage`
- `GET /api/v4/admin/performance/summary`
- `GET /api/v4/admin/performance/timeseries`
- `GET /api/v4/admin/performance/breakdown`
- `GET /api/v4/admin/usage/summary`
- `GET /api/v4/admin/status/results`

The storage and aggregation layer lives in [backend/app/services/admin_telemetry.py](../backend/app/services/admin_telemetry.py).

Today this service owns three different concerns:

- custom frontend performance event storage and summarization
- custom usage event storage and summarization
- CartoSky-specific run, artifact, and QA operational checks

## Current Admin Surfaces

### `/admin/performance`

Current page: [frontend/src/pages/admin/performance.tsx](../frontend/src/pages/admin/performance.tsx)

Current role:

- displays custom frontend performance timings
- treats custom p50 and p95 charts as the main viewer telemetry dashboard
- mixes user-visible timings with deep render-path internals

Phase 0 decision:

- keep this page temporarily as a comparison and migration aid
- mark it as non-authoritative immediately
- use it only until Web Vitals and the new RUM baseline are in place

### `/admin/usage`

Current page: [frontend/src/pages/admin/usage.tsx](../frontend/src/pages/admin/usage.tsx)

Current role:

- displays low-volume first-party usage counts
- only covers a very small set of product events

Phase 0 decision:

- this page is a migration candidate for PostHog-backed analytics
- the current event set is small enough to replace rather than expand

### `/admin/status`

Current page: [frontend/src/pages/admin/status.tsx](../frontend/src/pages/admin/status.tsx)

Current role:

- tracks retained published runs
- reports stale or stalled runs
- reports manifest and artifact failures
- reports completion and readiness issues

Phase 0 decision:

- preserve this page and its backend support
- this is valid CartoSky-specific operational telemetry and should remain first-party

## Event Inventory and Future Ownership

### Product analytics events

These should move to PostHog and stop being owned by the custom telemetry backend:

- `model_selected`
- `variable_selected`
- `region_selected`
- `animation_play`

Why:

- these are discrete user actions
- they fit product analytics, funnels, and usage reporting
- they do not belong in a custom SQLite-backed analytics system long term

### User-visible frontend timing events

These are the closest current metrics to user-visible frontend behavior, but they should not remain the primary source of truth after migration:

- `viewer_first_frame`
- `frame_change`
- `scrub_latency`
- `variable_switch`
- `loop_start`

Phase 0 decision:

- keep them only as comparison and migration metrics for now
- validate them against Web Vitals and the future minimal CartoSky RUM layer
- do not continue treating them as authoritative UX truth

### Render-path and debugging diagnostics

These are implementation-shaped frontend diagnostics. Some may survive as minimal CartoSky RUM, but only if they serve a direct debugging purpose:

- `tile_fetch`
- `animation_stall`
- `loop_manifest_resolve`
- `loop_decode_ready`
- `loop_decode_to_commit`
- `loop_commit_to_visible`
- `long_task_blocking`
- `loop_frame_drop_gap`

Phase 0 decision:

- keep these as engineering diagnostics only during migration
- narrow them down in later phases to a small fixed set
- do not let them define frontend performance health on their own

### Stale schema entries

The backend still allows some perf event names that are not part of the active frontend event inventory:

- `loop_queue_to_visible`
- `loop_first_visible_paint`

Phase 0 decision:

- treat these as stale schema entries
- either remove them during migration or leave them unsupported and undocumented
- do not build new dashboards or decisions around them

## Key Risks Observed in the Current System

- One custom system currently acts as product analytics, frontend performance telemetry, and operational status.
- The performance dashboard mixes user-visible timings with internal implementation milestones.
- The current custom metrics are summarized centrally in one backend service, which makes ownership boundaries easy to blur.
- Some frontend metrics are sampled but still shaped around render internals rather than user-perceived outcomes.
- Backend schema has drifted slightly from actual frontend emission, as shown by stale allowed metric names.

## Phase 0 Decisions Locked In

- The custom operational status and QA path remains first-party and survives the overhaul.
- The current performance dashboard is demoted to a legacy comparison surface immediately.
- The current usage event pipeline is a replacement target, not a growth area.
- Every existing event must be reassigned to exactly one future owner:
  - PostHog for product analytics
  - Web Vitals and minimal CartoSky RUM for frontend UX
  - Prometheus for backend and system metrics
  - OpenTelemetry for traces
  - Custom admin for CartoSky-specific pipeline and QA health

## Immediate Next Steps

- keep the current `/admin/performance` page online, but label it as legacy comparison telemetry
- begin Web Vitals implementation
- define the minimal CartoSky RUM diagnostic set before adding any new frontend timings
- prepare the PostHog event taxonomy using the existing usage event inventory as the seed set
