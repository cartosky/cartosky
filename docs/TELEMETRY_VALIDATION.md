# Telemetry Validation

## Ownership Map

Use these ownership rules when adding or validating telemetry in CartoSky:

- Product analytics, funnels, and replay:
  - Owner: PostHog
  - Admin route: `/admin/analytics`
  - Source of truth: PostHog project UI and dashboards
- Frontend UX baseline and minimal viewer diagnostics:
  - Owner: Web Vitals plus CartoSky RUM
  - Admin route: `/admin/overview`
  - Source of truth: `rum_events` summaries exposed through `/api/v4/admin/overview/summary`
- Backend and infrastructure metrics:
  - Owner: Prometheus plus Grafana
  - Admin route: `/admin/observability`
  - Source of truth: `/metrics`, Prometheus targets, Grafana dashboards
- Request tracing and slow-path drill-down:
  - Owner: OpenTelemetry plus Tempo
  - Admin route: `/admin/traces`
  - Source of truth: Tempo-backed Grafana Explore and trace summaries
- Pipeline, retained-run, artifact, and QA health:
  - Owner: first-party CartoSky status layer
  - Admin route: `/admin/status`
  - Source of truth: retained-run inspection plus `qa_reviews`

## Release Checks

Run these checks on production before trusting a telemetry rollout:

1. Frontend UX health:
   - Open `/admin/overview`
   - Confirm LCP, INP, CLS, and manifest-fetch cards show recent samples
   - Confirm `web_vitals_last_seen_at` is recent through `/api/v4/admin/overview/summary`

2. Product analytics:
   - Open `/admin/analytics`
   - Confirm PostHog links open the correct project
   - In PostHog, verify recent events such as `viewer_opened`, `model_selected`, and `share_clicked`

3. Backend metrics:
   - Confirm Prometheus target health is `up`
   - Confirm `/admin/observability` shows live API latency and request counts
   - Confirm Grafana dashboard panels populate

4. Tracing:
   - Confirm a live API response includes `X-Trace-ID`
   - Confirm `/admin/traces` shows recent exported traces
   - Confirm Grafana Explore returns recent `cartosky-api` traces

5. Status and QA:
   - Open `/admin/status`
   - Confirm retained-run results load
   - Confirm `QA Store` shows `Separate` after the Phase 7 migration

6. Legacy cutoff:
   - Confirm legacy perf and usage tables stop growing after normal site activity
   - Confirm `/admin/analytics` and `/admin/legacy-performance` show retirement messaging rather than active legacy telemetry comparisons

## Suggested Production Commands

Use these checks when validating a production deploy:

```bash
curl -s http://127.0.0.1:8200/metrics | grep cartosky_
```

```bash
curl -s http://127.0.0.1:9090/api/v1/targets | rg cartosky-api
```

```bash
curl -s http://127.0.0.1:3200/ready
```

```bash
curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=rate(cartosky_http_requests_total[5m])'
```

## What Does Not Belong in CartoSky Anymore

- Do not add new high-frequency frontend render timings to first-party dashboards.
- Do not add product analytics metrics to the custom SQLite admin layer.
- Do not build first-party replacements for Grafana dashboards, PostHog funnels, or Tempo trace search when deep links and summary cards are enough.
