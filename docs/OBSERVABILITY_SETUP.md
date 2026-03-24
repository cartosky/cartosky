# Observability Setup

This document covers the initial same-host Prometheus and Grafana rollout for CartoSky after Phase 4 backend instrumentation is live.

It assumes:

- the API already exposes `http://127.0.0.1:8200/metrics`
- Prometheus and Grafana will run on the current production host alongside the API and tile services
- CartoSky `/admin/observability` remains the operator entrypoint, with Grafana used for deeper drill-down

## What This Phase Adds

- Prometheus scrapes the CartoSky API on `127.0.0.1:8200`
- Grafana reads Prometheus as its default datasource
- a starter `CartoSky Observability` dashboard is provisioned automatically
- `/admin/observability` can deep-link to Grafana immediately, with embeds optional later

## Repo Assets

- Prometheus config:
  - [prometheus.yml](/Users/brianaustin/cartosky/deployment/observability/prometheus/prometheus.yml)
- Grafana datasource provisioning:
  - [cartosky-prometheus.yml](/Users/brianaustin/cartosky/deployment/observability/grafana/provisioning/datasources/cartosky-prometheus.yml)
- Grafana dashboard provisioning:
  - [cartosky-observability.yml](/Users/brianaustin/cartosky/deployment/observability/grafana/provisioning/dashboards/cartosky-observability.yml)
- Grafana starter dashboard:
  - [cartosky-observability.json](/Users/brianaustin/cartosky/deployment/observability/grafana/dashboards/cartosky-observability.json)

## Recommended Initial Rollout

### 1. Production-only: install Prometheus and Grafana

On the shared production host:

```bash
sudo apt-get update
sudo apt-get install -y prometheus
```

Then install Grafana using the package source you already trust on that host:

- if your host already has a `grafana` package source configured, install it with your package manager
- otherwise, add the official Grafana APT repository first and then install `grafana`

The initial rollout should still use distro-managed systemd services, which keeps the first deployment simple.

### 2. Production-only: copy the Prometheus config

```bash
sudo cp /opt/cartosky/deployment/observability/prometheus/prometheus.yml /etc/prometheus/prometheus.yml
```

Then validate and restart Prometheus:

```bash
sudo promtool check config /etc/prometheus/prometheus.yml
sudo systemctl restart prometheus
sudo systemctl status prometheus --no-pager
```

### 3. Production-only: provision Grafana datasource and dashboard

Create the target directories if needed:

```bash
sudo mkdir -p /etc/grafana/provisioning/datasources
sudo mkdir -p /etc/grafana/provisioning/dashboards
sudo mkdir -p /etc/grafana/provisioning/dashboards/cartosky
```

Copy the provisioning files:

```bash
sudo cp /opt/cartosky/deployment/observability/grafana/provisioning/datasources/cartosky-prometheus.yml /etc/grafana/provisioning/datasources/cartosky-prometheus.yml
sudo cp /opt/cartosky/deployment/observability/grafana/provisioning/dashboards/cartosky-observability.yml /etc/grafana/provisioning/dashboards/cartosky-observability.yml
sudo cp /opt/cartosky/deployment/observability/grafana/dashboards/cartosky-observability.json /etc/grafana/provisioning/dashboards/cartosky/cartosky-observability.json
```

Restart Grafana:

```bash
sudo systemctl enable grafana-server
sudo systemctl restart grafana-server
sudo systemctl status grafana-server --no-pager
```

### 4. Production-only: validate Prometheus scrape health

Confirm Prometheus can see the CartoSky API target:

```bash
curl -s http://127.0.0.1:9090/api/v1/targets | rg cartosky-api
```

Confirm the important metric families are queryable:

```bash
curl -s 'http://127.0.0.1:9090/api/v1/query?query=cartosky_http_requests_total' | head
```

### 5. Production-only: validate Grafana locally

Open Grafana on the host or through your existing nginx approach and confirm the provisioned dashboard appears:

- dashboard title: `CartoSky Observability`
- dashboard UID: `cartosky-observability`

### 6. Production-only: wire Grafana into the CartoSky admin shell

Once you know the Grafana host and dashboard URL, add them to `/opt/cartosky/frontend/.env.production`:

```bash
VITE_CARTOSKY_GRAFANA_URL=https://grafana.cartosky.com
VITE_CARTOSKY_GRAFANA_DASHBOARD_URL=https://grafana.cartosky.com/d/cartosky-observability/cartosky-observability
```

For the initial rollout, I recommend deep-linking only and leaving embeds off until auth and iframe policy are settled.

If you do want iframe embeds later, add:

```bash
VITE_CARTOSKY_GRAFANA_EMBED_URL=https://grafana.cartosky.com/d-solo/cartosky-observability/cartosky-observability?orgId=1
```

Then rebuild the frontend:

```bash
cd /opt/cartosky/frontend
npm run build
```

## Grafana Access Pattern

Recommended initial posture:

- keep Grafana internal-only or operator-only
- use `/admin/observability` as the main entrypoint
- use native Grafana for deeper Prometheus exploration
- do not require iframe embed on day one

This keeps the first rollout simpler and avoids immediate CSP, cookie, and embedding policy work.

## Optional Nginx Follow-up

If you want a stable external operator URL such as `grafana.cartosky.com`, put Grafana behind nginx and TLS separately from the public frontend and API.

Repo config:

- [grafana.cartosky.com.conf](/Users/brianaustin/cartosky/deployment/nginx/grafana.cartosky.com.conf)

Recommended production-only rollout:

1. Create a DNS record for `grafana.cartosky.com` pointing at the production host.
2. Copy the repo nginx file into place:

```bash
sudo cp /opt/cartosky/deployment/nginx/grafana.cartosky.com.conf /etc/nginx/sites-available/grafana.cartosky.com
sudo ln -sf /etc/nginx/sites-available/grafana.cartosky.com /etc/nginx/sites-enabled/grafana.cartosky.com
```

3. Request a certificate after DNS is live:

```bash
sudo certbot --nginx -d grafana.cartosky.com
```

4. Validate and reload nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

5. Confirm Grafana now opens at:

```text
https://grafana.cartosky.com
```

Once that works, add these to `/opt/cartosky/frontend/.env.production`:

```bash
VITE_CARTOSKY_GRAFANA_URL=https://grafana.cartosky.com
VITE_CARTOSKY_GRAFANA_DASHBOARD_URL=https://grafana.cartosky.com/d/cartosky-observability/cartosky-observability
```

Then rebuild the frontend:

```bash
cd /opt/cartosky/frontend
npm run build
```

This keeps `/admin/observability` pointing at a stable operator URL rather than a personal SSH tunnel.

## Dashboard Contents

The starter dashboard includes:

- API p95 latency
- API error rate
- sample-cache hit rate
- oldest published run
- HTTP request rate by route and status class
- HTTP p95 by route
- sample-cache outcomes over time
- published run completion ratio by model

## Current Limitations

- The tile server does not yet expose a Prometheus `/metrics` endpoint, so it is not included in the initial scrape config.
- Host metrics are not included until node exporter or equivalent is installed.
- Grafana embed is optional and should wait until auth and iframe behavior are acceptable.

## Cutover Guidance

This setup is a Phase 4.5 operational rollout:

- complete it after Phase 4 backend metrics are validated
- before Phase 5 tracing
- so Grafana is already in place when Tempo arrives later for `/admin/traces`
