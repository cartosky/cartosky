# Telemetry Overhaul

## Summary

Replace CartoSky's current all-in-one custom telemetry approach with a split system:

- PostHog Cloud for product analytics and replay
- Web Vitals plus a very small CartoSky-specific RUM layer for trusted frontend UX
- Prometheus for backend and infrastructure metrics
- OpenTelemetry for request tracing and correlation
- The existing custom admin/status layer only for CartoSky-specific pipeline and QA health

Use Option A for `/admin`: keep CartoSky as the authenticated admin shell and summary layer, then embed selected PostHog and Grafana views where practical and deep-link to native replay/trace drill-down UX. Do not build a custom visualization system that tries to replace Grafana or PostHog.

## Execution Context

- Do not assume local development matches production exactly.
- Production may differ from local in:
  - environment variables
  - feature-flag defaults
  - database contents and size
  - service topology and resource limits
  - scrape targets, auth, and embed behavior
- Any task that depends on real traffic, historical telemetry volume, production-only data, or production deployment shape must be validated on production, not inferred from local state.
- Production changes, exports, or validation steps that require access to the live host should be called out explicitly and treated as operator actions to be run by you or with your approval.
- Local work should be used for implementation, tests, mock validation, and documentation. Production should be used for baseline measurement, deployment validation, cutover confirmation, and archival of the live legacy telemetry DB.

## Implementation Changes

### Phase 0: Inventory and De-risk the Current System

- Audit the current custom telemetry surface in the frontend emitter, backend ingestion endpoints, and admin pages.
- Catalog each existing custom event into one of four buckets: product analytics, frontend UX baseline, CartoSky-specific frontend diagnostics, or obsolete metric.
- Query the legacy SQLite telemetry DB to measure current session volume before any PostHog rollout:
  - p50 and p95 discrete usage events per session
  - p50 and p95 total legacy events per session
  - top event emitters by count and by sample rate
  - candidate PostHog event volume after removing perf diagnostics
- Record baseline frontend and backend comparison numbers from the legacy system before replacing it:
  - `viewer_first_frame`
  - `frame_change`
  - `scrub_latency`
  - `variable_switch`
  - `loop_start`
- Mark the current custom frontend performance dashboards as non-authoritative immediately and plan for a short comparison window only.
- Preserve the custom operational status and QA checks as first-party admin functionality.
- Freeze the initial contracts for:
  - PostHog event taxonomy
  - Prometheus metric names and label sets
  - feature flags and rollback switches
- Phase 0 status note:
  - local code audit and dashboard demotion can be completed locally
  - Phase 0 is not complete until the production telemetry DB baseline is measured and the real production environment is checked against the assumptions in this document
- Production baseline findings from the live legacy DB:
  - `perf_events`: 74,389 rows
  - `usage_events`: 742 rows
  - `synthetic_perf_runs`: 0 rows
  - `qa_reviews`: 2,441 rows
  - usage-only sessions: 14
  - usage-only session volume: avg 53, p50 30, p95 122, max 203
  - all legacy-event sessions: 26
  - all-event session volume: avg 2889.65, p50 53, p95 17183, max 18338
  - high-volume legacy perf emitters: `tile_fetch`, `loop_decode_ready`, `scrub_latency`, `loop_queue_to_visible`, `loop_first_visible_paint`
  - stale schema entries are still present historically in production data: `loop_queue_to_visible`, `loop_first_visible_paint`
- Phase 0 conclusion:
  - the split architecture is confirmed by production data
  - the live SQLite DB still backs surviving custom `qa_reviews` data and cannot be removed wholesale at the Phase 6 cutoff
  - PostHog event budgeting must be based on production usage behavior, not local assumptions

### Phase 1: Restructure `/admin` Into a Unified Portal

- Keep `/admin` as the CartoSky-owned authenticated shell with shared navigation, layout, and summary cards.
- Change `/admin/performance` into `/admin/overview` or equivalent executive-summary behavior inside the admin shell.
- Add tool-oriented destinations under the same portal:
  - `/admin` or `/admin/overview` for native summary cards and active warnings
  - `/admin/analytics` for PostHog-backed product analytics and replay launch points
  - `/admin/observability` for Grafana-backed Prometheus metrics
  - `/admin/traces` for Grafana trace entrypoints
  - `/admin/status` for first-party pipeline and QA health
- Build first-party overview cards for the small cross-tool summary set CartoSky actually owns:
  - current LCP, INP, and CLS health
  - p95 API latency
  - tile error rate
  - latest run freshness and completeness
  - top models and variables by usage
  - active incidents and warnings
- Implement selective embedding only for high-level PostHog and Grafana dashboards.
- Use deep links, not full embeds, for PostHog session replay and trace exploration.
- Put every new admin destination behind feature flags so sections can be enabled independently.
- Phase 1 deliverables:
  - admin shell navigation and route structure
  - placeholder pages for analytics, observability, and traces
  - native status and incident cards that do not depend on new telemetry systems
  - placeholder overview cards for Web Vitals, Prometheus, and PostHog data that populate incrementally as later phases land

### Phase 2: Add Web Vitals and Replace Frontend Truth Metrics

- Add Web Vitals collection for LCP, INP, and CLS in the frontend.
- Treat Web Vitals as the primary frontend truth baseline.
- Keep only a narrow CartoSky-specific RUM layer for diagnostics that Web Vitals does not cover well:
  - manifest fetch duration
  - first map render duration
  - first overlay visible duration
  - tile request failure count
  - animation stall count
  - frame-drop buckets rather than raw per-frame telemetry
- The CartoSky-specific RUM layer must remain minimal and limited to a small, fixed set of diagnostics.
- New frontend metrics should not be added unless they map directly to a user-visible outcome or a clear debugging need.
- Attach these diagnostics to the same session context used by PostHog where possible.
- Stop using existing internal render-path timings as the primary frontend performance source of truth.
- Gate Web Vitals and CartoSky RUM behind separate frontend feature flags so either emitter can be disabled without a deploy.
- Add frontend tests for sampling, payload shape, and deduplication of the new RUM emitter.
- Phase 2 deliverables:
  - Web Vitals collection and transport
  - minimal CartoSky RUM emitter with fixed diagnostic set
  - feature flags for Web Vitals and RUM
  - `/admin/overview` cards for LCP, INP, and CLS health

### Phase 3: Introduce PostHog Cloud for Product Analytics and Replay

- Add PostHog to the frontend for pageviews, sessions, feature usage, event taxonomy, and sampled session replay.
- Replace the current custom `usage_events` pipeline for product analytics with PostHog ownership.
- Use the v1 event taxonomy defined in the appendix below as the contract for implementation.
- Standardize useful event properties such as model, variable, region, device class, viewport bucket, release SHA, and login state.
- Do not send high-frequency or per-frame events to PostHog.
- PostHog events must map to discrete user actions, not internal rendering or system events.
- Enforce a PostHog event budget of p50 <= 25 analytics events per session, target p95 <= 75, and require review if p95 exceeds 100 in production.
- Sample or cohort-gate session replay to avoid unnecessary noise and cost.
- Surface PostHog in `/admin/analytics` through native summary cards plus embedded shared dashboards and replay launch links.
- Gate PostHog analytics and session replay behind separate frontend feature flags.
- Add a smoke-test script or contract check that validates the shipped PostHog event names and required properties against the taxonomy appendix.
- Phase 3 deliverables:
  - PostHog client integration
  - v1 event taxonomy implementation
  - replay sampling and cohort capture rules
  - `/admin/analytics` summary cards, embeds, and replay deep links
  - feature flags for analytics and replay

### Phase 4: Add Prometheus Metrics for Backend and Infrastructure

- Add Prometheus instrumentation to the FastAPI app and backend services.
- Expose a Prometheus scrape endpoint and instrument the concrete metric set defined in the appendix below.
- Use histograms for latency-oriented metrics and plan dashboards around p50/p95/p99 rather than averages.
- Enforce low-cardinality labels in Prometheus metrics.
- Do not include high-cardinality fields such as `run_id`, `forecast_hour`, `region_id`, or user/session identifiers in metric labels.
- Build Grafana dashboards on top of Prometheus and surface them through `/admin/observability` via selective embeds and deep links.
- Gate Prometheus exposition behind a backend flag so metrics can be disabled independently during rollout.
- Add integration tests that scrape `/metrics` and assert presence of the required CartoSky metric families and label keys.
- Phase 4 deliverables:
  - backend instrumentation for required metric families
  - `/metrics` exposition on API and tile services
  - Prometheus scrape configuration
  - Grafana dashboards for latency, errors, cache, scheduler, and freshness
  - `/admin/observability` embeds and deep links
  - integration tests for exposition and label contracts

### Phase 4.5: Deploy Prometheus and Grafana on the Shared Host

- Install Prometheus and Grafana on the existing production host alongside the API and tile services.
- Use Prometheus to scrape the CartoSky API immediately, with tile-server and host scrapes added once those targets exist.
- Provision a starter Grafana dashboard from version-controlled repo assets rather than building it manually in production.
- Wire the resulting Grafana URL and dashboard URL into `/admin/observability` as deep links first; treat iframe embedding as optional follow-up work.
- Production-only operator steps for this phase should be driven from [OBSERVABILITY_SETUP.md](/Users/brianaustin/cartosky/docs/OBSERVABILITY_SETUP.md).
- Phase 4.5 deliverables:
  - same-host Prometheus service with working CartoSky scrape
  - same-host Grafana service with provisioned datasource and starter dashboard
  - `/admin/observability` deep links configured in production
  - optional embed only after auth and iframe policy review

### Phase 5: Add OpenTelemetry Tracing and Correlation

- Add OpenTelemetry tracing to the backend first, not the browser first.
- Instrument spans around key CartoSky request-path operations such as viewer data load, manifest lookup, cache lookup, raster read/decode, tile generation, and response serialization.
- Export traces via OTLP to a local OpenTelemetry Collector on the production host, then into a local Tempo backend paired with Grafana for exploration under `/admin/traces`.
- Propagate correlation IDs or trace identifiers back to the frontend where practical so bad sessions can be connected to slow backend paths.
- Treat browser OpenTelemetry as optional and secondary to Web Vitals for frontend truth.
- Gate backend tracing behind a backend flag and leave browser tracing disabled by default for the initial rollout.
- Add integration checks that confirm trace creation, OTLP export, and trace ID propagation on sampled requests.
- Phase 5 deliverables:
  - backend OTel instrumentation
  - local Collector and Tempo pipeline
  - trace propagation and correlation IDs
  - `/admin/traces` entrypoints and deep links
  - tracing smoke checks and rollback flag

### Phase 6: Migrate and Retire the Old Custom Frontend Perf Stack

- Keep a short dual-write or comparison period only long enough to validate Web Vitals and new diagnostics against the current custom numbers.
- Use a release-gated cutoff, not a calendar-date cutoff. The cutoff release is the first production release that has:
  - Web Vitals and CartoSky RUM enabled by default
  - PostHog analytics enabled by default
  - `/admin/overview`, `/admin/analytics`, and `/admin/observability` available by default
  - successful comparison signoff against the Phase 0 baseline
- At the cutoff point, stop production writes to the legacy custom telemetry SQLite tables for frontend perf, usage, and synthetic performance data.
- Export the legacy SQLite telemetry data to archival JSON and CSV snapshots, store them outside production for reference, and include a short README with schema notes and cutoff context.
- Treat `perf_events`, `usage_events`, and `synthetic_perf_runs` as archive-and-remove legacy tables at the cutoff.
- Do not remove the live legacy telemetry SQLite file from production until `qa_reviews` has been migrated to a separate store or otherwise isolated from the retired legacy telemetry tables.
- Remove or repurpose the current custom performance admin pages once the new baseline is validated.
- Eliminate panels that depend on mean latency or implementation-specific internal timings as decision-making tools.
- Retain only custom frontend diagnostics that remain clearly user-visible or uniquely CartoSky-specific.
- Phase 6 deliverables:
  - dual-write comparison signoff
  - cutoff release validation
  - legacy SQLite export and archive
  - production write disable for legacy telemetry
  - removal or repurposing of legacy frontend perf pages

### Phase 7: Narrow the Custom Admin Layer to CartoSky-Specific Health

- Keep custom first-party admin ownership for:
  - published run completeness
  - stale run detection
  - missing artifact counts
  - unreadable raster/value-grid counts
  - QA review status and warnings
  - domain-specific pipeline health
- Continue exposing those via `/admin/status` and related native summary cards on `/admin/overview`.
- Ensure this custom layer does not attempt to replace product analytics, metrics storage, or distributed tracing.
- Split surviving `qa_reviews` storage from the retired legacy telemetry DB before the final removal of that DB from production.
- Phase 7 deliverables:
  - separate storage path for surviving custom status and QA data
  - `/admin/status` verification against the new surviving store
  - confirmation that the retired legacy telemetry DB is no longer required for ongoing status/QA reads

### Phase 8: Rollout, Validation, and Trust Rebuild

- Roll out in this order: Web Vitals, PostHog, Prometheus/Grafana, OpenTelemetry, removal of old perf dashboards.
- Validate that admin summary cards read from the correct underlying owner for each metric class.
- Add release-level checks that confirm key telemetry is emitting before broad rollout.
- Confirm that slow-session workflows work end-to-end from `/admin` summary to PostHog replay and from observability spikes to trace drill-down.
- Document new ownership rules so future telemetry additions land in the right system.
- Keep the operator-facing validation and ownership runbook in [TELEMETRY_VALIDATION.md](/Users/brianaustin/cartosky/docs/TELEMETRY_VALIDATION.md).

## Execution by Phase

### Phase 0

- Local:
  - audit code paths, event inventory, and admin surfaces
  - document ownership mapping
  - demote the current `/admin/performance` page to legacy comparison status
- Production:
  - query the live legacy SQLite DB for actual session and event volume
  - confirm actual production env vars, service layout, and feature-flag defaults
  - verify whether any surviving status or QA data is currently stored in the production telemetry DB
- Sequencing:
  - do not move to Phase 1 implementation until the production baseline and environment check are complete
- Current status:
  - production baseline measurement is complete
  - `qa_reviews` is confirmed live in the production telemetry DB
  - Phase 1 can proceed, but the final DB-retirement path must preserve or migrate `qa_reviews`

### Phase 1

- Local:
  - build the `/admin` shell structure, routes, placeholders, and native summary-card scaffolding
  - add feature-flagged embed and deep-link plumbing
- Production:
  - no production-only command is required beyond normal deployment once the phase is ready
  - after deployment, verify route visibility, auth, and embed behavior in the real admin environment

### Phase 2

- Local:
  - implement Web Vitals and minimal CartoSky RUM emitters
  - add tests and local validation for payloads, sampling, and flags
- Production:
  - validate real emission rates and Web Vitals behavior against live traffic
  - confirm `/admin/overview` cards reflect production data correctly after deployment

### Phase 3

- Local:
  - implement the PostHog client integration and event contract
  - add smoke checks for event names and required properties
- Production:
  - configure the real PostHog project keys and replay settings
  - validate event volume, replay capture rate, and free-tier budget behavior against production traffic
  - verify analytics and replay links from `/admin/analytics`

### Phase 4

- Local:
  - implement Prometheus instrumentation, metric schema, tests, and admin integration
- Production:
  - deploy Prometheus and Grafana on the shared production host
  - configure scrape targets against the real API and tile services
  - validate live metric cardinality, scrape health, and dashboard usefulness under production traffic

### Phase 5

- Local:
  - implement backend tracing instrumentation and local OTLP export validation
- Production:
  - deploy the Collector and Tempo on the shared production host
  - validate sampled traces, slow-request capture, and trace drill-down from `/admin/traces`
  - confirm tracing overhead is acceptable on the production host

### Phase 6

- Local:
  - remove or repurpose legacy UI and write archival/export tooling if needed
  - cutoff plumbing now available in repo:
    - backend runtime flag: `CARTOSKY_LEGACY_TELEMETRY_WRITE_ENABLED=0`
    - frontend compile-time flags:
      - `VITE_CARTOSKY_LEGACY_PERF_TELEMETRY_ENABLED=0`
      - `VITE_CARTOSKY_LEGACY_USAGE_TELEMETRY_ENABLED=0`
    - legacy archive script:
      - [export_legacy_telemetry.py](/Users/brianaustin/cartosky/scripts/export_legacy_telemetry.py)
- Production:
  - perform the release-gated cutoff validation
  - disable legacy production writes
  - export `perf_events`, `usage_events`, and `synthetic_perf_runs` from the live legacy SQLite DB to JSON and CSV archives
  - confirm archives are stored safely outside the production runtime path
  - do not remove the live legacy telemetry DB yet if `qa_reviews` still lives there

### Phase 7

- Local:
  - narrow the custom admin implementation to CartoSky-specific health only
  - phase plumbing now available in repo:
    - dedicated QA/status DB env: `CARTOSKY_STATUS_DB_PATH`
    - QA migration script:
      - [migrate_qa_reviews.py](/Users/brianaustin/cartosky/scripts/migrate_qa_reviews.py)
- Production:
  - verify status and QA surfaces still work after the legacy telemetry cutover
  - confirm no production path still treats the old perf or usage system as authoritative
  - migrate or isolate `qa_reviews` before final DB retirement

### Phase 8

- Local:
  - finalize docs, rollout checklists, and residual cleanup
- Production:
  - verify the full end-to-end operator workflow in the live admin environment
  - confirm all enabled telemetry systems are emitting within expected budgets and resource limits

## Public Interfaces and Admin Surface

- New admin information architecture under the existing admin shell:
  - `/admin/overview`
  - `/admin/analytics`
  - `/admin/observability`
  - `/admin/traces`
  - `/admin/status`
- The existing custom usage ingestion path should be deprecated in favor of PostHog-owned analytics events.
- The existing custom perf ingestion path should be reduced to temporary migration support and any retained CartoSky-specific frontend diagnostics.
- Admin summary APIs should aggregate small first-party overview cards only; they should not try to proxy full Grafana or PostHog functionality.
- Admin overview must include a minimal telemetry health check (e.g., last emission timestamps for Web Vitals, Prometheus scrape success, and PostHog ingestion activity).

## Observability Infrastructure

- Initial deployment uses the shared production host footprint that already runs the API and tile services.
- Prometheus, Grafana, the OpenTelemetry Collector, and Tempo run alongside the existing API and tile services on the current production host for the initial rollout.
- Prometheus scrapes:
  - CartoSky API metrics endpoint
  - CartoSky tile server metrics endpoint
  - scheduler or worker metrics endpoint if exposed
  - host metrics via node exporter or equivalent host collector
- OpenTelemetry flow for initial rollout:
  - API and tile services export OTLP traces to the local collector
  - the local collector forwards traces to local Tempo
  - Grafana reads Prometheus and Tempo as datasources
- Grafana and Prometheus UIs should remain internal-only and be surfaced to operators through the CartoSky `/admin` shell via selective embeds and deep links.
- The deployment must remain portable so Prometheus, Grafana, Collector, and Tempo can be moved to a separate host or managed service later without changing frontend contracts or metric names.

## Feature Flags and Rollback

- Frontend feature flags:
  - `VITE_CARTOSKY_WEB_VITALS_ENABLED`
  - `VITE_CARTOSKY_RUM_ENABLED`
  - `VITE_CARTOSKY_POSTHOG_ENABLED`
  - `VITE_CARTOSKY_POSTHOG_REPLAY_ENABLED`
  - `VITE_CARTOSKY_ADMIN_EMBEDS_ENABLED`
- Backend feature flags:
  - `CARTOSKY_PROMETHEUS_ENABLED`
  - `CARTOSKY_OTEL_ENABLED`
  - `CARTOSKY_LEGACY_TELEMETRY_WRITE_ENABLED`
- Rollback policy:
  - if a new emitter causes visible frontend regressions, event floods, or third-party failures, disable the emitter flag first and keep the UI online
  - if Prometheus or tracing causes backend overhead or instability, disable metric or trace export independently without reverting unrelated telemetry phases
  - if PostHog replay volume exceeds budget or causes privacy concerns, disable replay while keeping analytics enabled
  - if admin embeds fail, keep the native `/admin` shell and summary cards available with deep links only
- Every phase must ship with a corresponding flag defaulting to off in staging until the phase-specific smoke checks pass.

## Telemetry Guardrails

- Every metric or event must have exactly one owning system:
  - PostHog for product analytics
  - Web Vitals and CartoSky RUM for frontend UX
  - Prometheus for backend and system metrics
  - OpenTelemetry for traces
  - Custom admin for CartoSky-specific pipeline and QA health
- Web Vitals must be collected at 100% sample rate.
- Custom frontend diagnostics must use 5% to 10% sampling.
- PostHog session replay must use 5% to 20% sampling or cohort-based capture for errors and slow sessions.
- OpenTelemetry tracing must use 1% to 10% sampling by default, with errors and slow requests always sampled.
- Define a "slow session" as one where LCP > 2.5s or INP > 200ms for sampling, replay capture, and tracing prioritization.
- Do not use mean/average latency as a primary decision metric; prefer percentile-based metrics (p50/p95/p99).
- Production baseline note:
  - the current legacy usage-only session distribution is avg 53, p50 30, p95 122, max 203
  - the initial PostHog event budget is intentionally higher than the earlier draft because production behavior already exceeds the earlier draft budget
- Slow session classification must drive replay capture and tracing upsampling.
- Prometheus default retention should be capped (e.g., 7–14 days) for initial rollout.
- PostHog relies on managed retention; no additional storage layer should be added.

## Test Plan

- Frontend unit tests:
  - Web Vitals and RUM sampling behavior
  - payload shape and required properties
  - deduplication and guardrails for replay and analytics emitters
- Frontend smoke tests:
  - verify the v1 PostHog event contract for model, variable, region, and animation actions
  - verify `/admin` summary cards still render when embeds are disabled
- Backend integration tests:
  - scrape `/metrics` and assert required Prometheus metric families and label keys
  - verify metrics are absent when `CARTOSKY_PROMETHEUS_ENABLED` is off
  - verify traces are exported when `CARTOSKY_OTEL_ENABLED` is on and absent when off
  - verify trace IDs or correlation IDs are attached to sampled slow or failed requests
- End-to-end validation:
  - verify Web Vitals emission for LCP, INP, and CLS on real viewer flows
  - verify session replay sampling and replay launch links from `/admin/analytics`
  - verify Grafana dashboards show correct latency, error, cache, scheduler, and freshness data
  - verify OpenTelemetry traces exist for critical request paths and can be reached from `/admin/traces`
  - verify `/admin/overview` summary cards correctly combine first-party status with embedded or linked analytics and observability destinations
  - verify old custom frontend perf panels are removed or clearly demoted after the cutoff release
  - verify CartoSky-specific status pages still show run health, artifact failures, and QA warnings correctly

## Appendix A: PostHog v1 Event Taxonomy

- Shared event properties on every PostHog event:
  - `model_id`
  - `variable_id`
  - `region_id`
  - `device_class`
  - `viewport_bucket`
  - `release_sha`
  - `is_logged_in`
  - `page`
- Optional event properties only when they materially describe the action:
  - `forecast_hour`
  - `render_mode`
  - `entrypoint`
  - `share_surface`
- Required v1 events:
  - `viewer_opened`
  - `model_selected`
  - `variable_selected`
  - `region_selected`
  - `animation_play`
  - `animation_pause`
  - `legend_opened`
  - `share_clicked`
- Required event-specific properties:
  - `viewer_opened`: `entrypoint`, `render_mode`, `forecast_hour`
  - `model_selected`: `from_model_id`, `to_model_id`, `forecast_hour`
  - `variable_selected`: `from_variable_id`, `to_variable_id`, `forecast_hour`
  - `region_selected`: `from_region_id`, `to_region_id`, `forecast_hour`
  - `animation_play`: `render_mode`, `forecast_hour`
  - `animation_pause`: `render_mode`, `elapsed_play_seconds`
  - `legend_opened`: no additional required properties
  - `share_clicked`: `share_surface`
- Person property rules:
  - use PostHog `$set_once` for stable properties first observed at profile creation time, such as initial device class
  - use PostHog `$set` for mutable person properties used for cohorting, such as `is_logged_in`
  - keep session-specific or event-specific fields such as `forecast_hour`, `page`, and `render_mode` on events rather than person profiles
- Event naming rules:
  - use lower_snake_case
  - model user actions, not internal rendering or system phases
  - preserve existing names where they already match this convention
- Explicit exclusions:
  - do not send frame-level events
  - do not send internal loop decode phases
  - do not send resource timing events
  - do not send backend or infrastructure state to PostHog

## Appendix B: Prometheus v1 Metric Schema

- HTTP:
  - `cartosky_http_requests_total{service,route,method,status_class}`
  - `cartosky_http_request_duration_seconds{service,route,method,status_class}` histogram
- Tile and render path:
  - `cartosky_tile_requests_total{service,render_mode,cache_result,status_class}`
  - `cartosky_tile_render_duration_seconds{service,model_id,render_mode,result}` histogram
  - `cartosky_manifest_build_duration_seconds{service,model_id,result}` histogram
- Cache:
  - `cartosky_cache_operations_total{service,cache_name,result}`
- Database:
  - `cartosky_db_query_duration_seconds{service,query_group,result}` histogram
- Scheduler and jobs:
  - `cartosky_scheduler_run_duration_seconds{job_name,model_id,result}` histogram
  - `cartosky_scheduler_failures_total{job_name,model_id,reason}`
- Freshness and completion:
  - `cartosky_published_run_age_hours{model_id}`
  - `cartosky_published_run_completion_ratio{model_id}`
- Host metrics:
  - use node exporter or equivalent standard host metrics rather than custom CartoSky-prefixed process metrics when possible
- Label rules:
  - `route` must use route templates, not raw paths
  - `status_class` must use coarse values like `2xx`, `4xx`, `5xx`
  - `result` and `reason` must be low-cardinality enumerations
  - do not use `run_id`, `forecast_hour`, `region_id`, session identifiers, user identifiers, or raw URLs as labels
  - do not add new labels without documenting them here first
- Histogram bucket defaults:
  - `cartosky_http_request_duration_seconds`: `0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5`
  - `cartosky_tile_render_duration_seconds`: `0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6`
  - `cartosky_manifest_build_duration_seconds`: `0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10`
  - `cartosky_db_query_duration_seconds`: `0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1`
  - `cartosky_scheduler_run_duration_seconds`: `0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300`

## Assumptions and Defaults

- PostHog will use managed cloud on the free tier for product analytics and sampled replay.
- Prometheus, Grafana, the OpenTelemetry Collector, Tempo, and the CartoSky admin shell will be self-hosted on the current production host for the initial rollout.
- `/admin` uses Option A: native summary cards plus selective embeds plus deep links, not a custom all-in-one dashboard renderer.
- PostHog and Grafana embeds are limited to high-level dashboard views; replay and trace drill-down remain native tool experiences.
- The current custom frontend performance dashboards are replaced quickly after a short validation period rather than kept long-term.
- The production legacy telemetry SQLite DB is only removed after Phase 6 archival and after any surviving `qa_reviews` dependency has been migrated or isolated.
