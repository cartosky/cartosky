# Tracing Setup

This document covers the initial same-host OpenTelemetry tracing rollout for CartoSky after the Phase 5 backend code has landed.

It assumes:

- the CartoSky API exports OTLP traces when `CARTOSKY_OTEL_ENABLED=1`
- traces are sent to a local OpenTelemetry Collector on `127.0.0.1:4318`
- the local Collector forwards traces into a local Tempo backend
- Grafana remains the primary operator UI for trace search and drill-down

## Repo Assets

- API tracing env example:
  - [api.env.example](/Users/brianaustin/cartosky/deployment/systemd/api.env.example)
- OpenTelemetry Collector config:
  - [collector-config.yml](/Users/brianaustin/cartosky/deployment/observability/otel/collector-config.yml)
- Tempo config:
  - [tempo.yml](/Users/brianaustin/cartosky/deployment/observability/tempo/tempo.yml)
- Grafana Tempo datasource provisioning:
  - [cartosky-tempo.yml](/Users/brianaustin/cartosky/deployment/observability/grafana/provisioning/datasources/cartosky-tempo.yml)

## What This Phase Adds

- backend request traces for the CartoSky API
- manual child spans around bootstrap, manifest, frames, loop-manifest, sample, and sample-batch flows
- `X-Trace-ID` response headers for correlation
- `/api/v4/admin/traces/summary` for the CartoSky admin shell
- `/admin/traces` summary cards and recent-trace visibility

## Recommended Rollout

### 1. Production-only: install or place a Collector and Tempo

Use the package source or binary/source layout you already trust on the production host. The important part for CartoSky is the config shape, not a specific installer.

### 2. Production-only: place the Collector config

```bash
sudo mkdir -p /etc/otelcol
sudo cp /opt/cartosky/deployment/observability/otel/collector-config.yml /etc/otelcol/config.yaml
```

### 3. Production-only: place the Tempo config

```bash
sudo mkdir -p /etc/tempo
sudo cp /opt/cartosky/deployment/observability/tempo/tempo.yml /etc/tempo/tempo.yml
sudo mkdir -p /var/lib/tempo/traces
```

### 4. Production-only: provision Grafana with Tempo

```bash
sudo cp /opt/cartosky/deployment/observability/grafana/provisioning/datasources/cartosky-tempo.yml /etc/grafana/provisioning/datasources/cartosky-tempo.yml
sudo systemctl restart grafana-server
```

After restart, Grafana should show a datasource named `CartoSky Tempo`.

### 5. Production-only: enable API tracing

Add these to `/etc/cartosky/api.env`:

```bash
CARTOSKY_OTEL_ENABLED=1
CARTOSKY_OTEL_SERVICE_NAME=cartosky-api
CARTOSKY_OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces
CARTOSKY_OTEL_SAMPLE_RATIO=0.05
CARTOSKY_OTEL_SLOW_REQUEST_MS=1000
```

Then restart the API:

```bash
sudo systemctl restart csky-api.service
sudo systemctl status csky-api.service --no-pager
```

### 6. Production-only: wire Grafana trace links into the admin shell

Once Grafana trace search is reachable, add to `/opt/cartosky/frontend/.env.production`:

```bash
VITE_CARTOSKY_GRAFANA_TRACES_URL=https://grafana.cartosky.com/explore
```

Then rebuild the frontend:

```bash
cd /opt/cartosky/frontend
npm run build
```

## Validation

### API-level validation

- hit a few API routes such as:
  - `/api/v4/bootstrap`
  - `/api/v4/{model}/{run}/manifest`
  - `/api/v4/{model}/{run}/{var}/frames`
  - `/api/v4/sample`
- confirm responses now include `X-Trace-ID`

### Admin validation

- open `/admin/traces`
- confirm:
  - tracing status is on
  - recent exported traces count increases
  - recent trace rows appear
  - no export error is shown

### Grafana validation

- confirm `CartoSky Tempo` datasource exists
- use Grafana trace search to find recent API traces

## Current Scope

- this first rollout traces the CartoSky API only
- the separate tile server is intentionally deferred to a follow-up pass
- browser OpenTelemetry remains off for now; Web Vitals and minimal RUM stay the frontend truth source
