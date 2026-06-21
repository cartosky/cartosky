<div align="center">

<img src="./frontend/public/assets/new_logo.png" alt="CartoSky logo" width="720" />

# CartoSky

Map-first weather analysis built around fast model switching, forecast-time scrubbing, and operational trust signals.

[Overview](#overview) • [Features](#features) • [Project-Layout](#project-layout) • [Getting-Started](#getting-started) • [Testing](#testing) • [Deployment-and-Operations](#deployment-and-operations) • [Documentation](#documentation)

</div>

## Overview

CartoSky is a weather guidance platform with a React and MapLibre frontend, a FastAPI backend, and a set of scheduler and publishing workflows for forecast data. The repository combines the public-facing map viewer, a location-first forecast page, and the operational pieces needed to ingest, publish, observe, and validate model output.

The current product surface is centered on a technical, map-dominant workflow: open the viewer, switch between supported deterministic and ensemble models, scrub forecast hours quickly, inspect derived products and anomalies, and keep run freshness visible. The repo also includes a location-first forecast page with normalized NWS/Open-Meteo data, admin telemetry dashboards, boundary/vector tile support, and The Weather Forums sharing integration.

> [!IMPORTANT]
> The frontend defaults to `https://api.cartosky.com` when `VITE_API_BASE` is not set. For local development, point it at your local API explicitly.

## Features

- Map-first viewer built with React 19, Vite, and MapLibre GL.
- FastAPI API serving manifests, frames, grid binaries, point sampling, contours, vectors, and bootstrap metadata under `api/v4`.
- Supported guidance catalog includes HRRR, NAM, GFS, NBM, ECMWF, AIFS, AIGFS, GEFS, EPS, SPC outlooks Day 1-8, CPC outlooks, NWS hazards, and MRMS.
- Satellite imagery via GOES bands, including Band 13 Clean IR, Band 9 Mid-Level WV, and Band 8 Upper-Level WV.
- Location-first Forecast page backed by geocoding, current conditions, hourly/daily forecasts, alerts, NWS forecast discussions, attribution, and freshness metadata.
- Location favorites on the Forecast page for quick return navigation.
- Forecast and anchor workflows for location-based weather summaries, NWS anchor-city modals, and handoff into the map viewer.
- Derived and advanced variables such as 10m/upper-level wind speed, 500 mb height/vorticity overlays, total precipitation, precipitation-type intensity, 10:1 and Kuchera snowfall, ice accumulation, and ERA5-baseline anomaly products.
- Ensemble-aware products for GEFS and EPS, including mean fields and temperature/height/precipitation anomaly workflows.
- CPC outlook coverage for 6-10 Day, 8-14 Day, Weeks 3-4, One Month, and Three Month products.
- NWS warning polygons overlay.
- Climate indices page covering AO, NAO, PNA, MJO, and ENSO.
- Side-by-side model comparison tool at `/compare`.
- Admin surfaces for performance telemetry, usage summaries, operational health, analytics, and observability rollouts.
- TWF sharing integration with server-side screenshot generation, post drafting, and direct thread posting.
- Production-oriented deployment assets for systemd, nginx, Prometheus, Tempo, and Grafana.
- Extensive backend test coverage plus Playwright end-to-end coverage for the frontend.

## Project Layout

```text
.
├── backend/              FastAPI app, model logic, services, tests, and scheduler scripts
├── frontend/             React + Vite client, Playwright tests, static assets
├── data/                 Local data root for published/manifests/staging artifacts
├── deployment/           nginx, systemd, Prometheus, Tempo, and Grafana config
├── docs/                 Implementation plans, roadmap, and operational specs
├── scripts/              Root-level utility and migration scripts
├── extract_colorbar.py   Standalone utility script
└── probe_herbie.py       Standalone probing script
```

### Main components

- `backend/app/main.py`: primary API application and route surface.
- `backend/app/services/`: publishing, telemetry, grid, boundary tile, tracing, and weather-domain services.
- `backend/tests/`: backend regression and API contract coverage.
- `frontend/src/App.tsx`: main weather viewer.
- `frontend/src/pages/compare.tsx`: side-by-side model comparison viewer.
- `frontend/src/pages/home.tsx`: marketing and product overview surface.
- `frontend/src/pages/forecast.tsx`: location-first forecast workflow.
- `frontend/src/pages/models.tsx` and `frontend/src/pages/variables.tsx`: public catalog surfaces for supported guidance and variables.
- `frontend/src/pages/admin/`: admin shell for performance, usage, analytics, status, and observability.
- `frontend/src/pages/admin/roadmap.tsx`: internal admin roadmap tracker.

## Getting Started

### Prerequisites

- Python 3 with virtual environment support.
- Node.js and npm.
- Native geospatial dependencies required by packages such as `rasterio` and `pyproj`.

> [!NOTE]
> `backend/requirements.txt` includes `rasterio`, `rio-tiler`, and `pyproj`. On a fresh machine you may need GDAL/PROJ-related system libraries before `pip install` succeeds.

### Authentication

> [!NOTE]
> CartoSky uses Clerk for authentication. To run locally you need `VITE_CLERK_PUBLISHABLE_KEY` for the frontend and `CLERK_SECRET_KEY` for the backend. These values are not included in the example env files and must be obtained from the Clerk dashboard.

### 1. Start the backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt

export CARTOSKY_DATA_ROOT="$PWD/data"
export CORS_ORIGINS="http://127.0.0.1:5173,http://localhost:5173"

uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8200
```

Useful local env defaults and production examples live in:

- `deployment/systemd/api.env.example`
- `deployment/systemd/scheduler.env.example`

### 2. Start the frontend

```bash
cd frontend
npm install

cat > .env.local <<'EOF'
VITE_API_BASE=http://127.0.0.1:8200
EOF

npm run dev
```

Open `http://127.0.0.1:5173` in your browser.

### 3. Expected local flow

With both services running, the frontend talks to the FastAPI backend at `api/v4`. The API serves capability/bootstrap metadata, frame manifests, grid files, sample endpoints, Forecast page responses, forecast-anchor responses, admin telemetry summaries, and health endpoints.

If you want to work with published artifacts or scheduler output locally, keep `CARTOSKY_DATA_ROOT` pointed at the repo `data/` directory or another compatible data root.

## Testing

### Backend

```bash
source .venv/bin/activate
pytest backend/tests
ruff check backend/app backend/tests backend/scripts
```

### Frontend

```bash
cd frontend
npm run build
npm test
```

Playwright is configured to launch a local Vite server on port `4173` unless `PLAYWRIGHT_USE_EXISTING_SERVER=1` is set.

## Deployment and Operations

The repository already includes the scaffolding for a production-style deployment rather than just an app prototype.

- `deployment/systemd/` contains API and scheduler unit files plus example env files.
- `deployment/nginx/` contains reverse-proxy and internal grid offload examples.
- `deployment/observability/prometheus/prometheus.yml` defines API scraping for `/metrics`.
- `backend/app/services/prometheus_metrics.py` and `backend/app/services/otel_tracing.py` back the Prometheus and tracing integration.

Operational features already present in the codebase include:

- `/metrics` for Prometheus scraping.
- Admin telemetry stored in SQLite-backed status and telemetry databases.
- Optional PostHog and RUM wiring in the frontend.
- OpenTelemetry hooks for slow-request and trace correlation.
- nginx `X-Accel-Redirect` support for immutable grid binaries.

> [!TIP]
> If you are standing up a new environment, start from `deployment/systemd/api.env.example` and only enable optional features like Prometheus, OTEL, PostHog, or nginx grid acceleration after the base viewer and publish pipeline are healthy.

## Documentation

The `docs/` directory is the operational memory of the project. A few especially useful entry points:

- `docs/ROADMAP.md`: current product and platform roadmap.
- `docs/FORECAST_PAGE_BACKEND.md`: Forecast page API contract and provider routing.
- `docs/PERFORMANCE_SCALING_IMPLEMENTATION_PLAN.md`: API and deployment performance work.
- `docs/TELEMETRY_OVERHAUL.md`: telemetry ownership and rollout direction.
- `docs/VARIABLE_ROLLOUT.md`: supported-variable expansion planning.
- `docs/ANOMALY_VARIABLES_IMPLEMENTATION_PLAN.md`: ERA5-baseline anomaly product architecture.
- `docs/ERA5_CLIMATOLOGY_RUNBOOK.md`: off-prod climatology asset generation workflow.
- `docs/KUCHERA_PROFILE_LEVELS.md`: operational Kuchera profile-level guidance.
- `docs/MRMS_RADAR_IMPLEMENTATION_PLAN.md`: MRMS rollout details.
- `docs/SPC_PROBABILISTIC_OUTLOOKS_IMPLEMENTATION_PLAN.md`: SPC product rollout notes.
- `docs/BOUNDARY_TILESET.md`: boundary tile generation and serving details.

## Development Notes

- The backend is organized around published artifacts under the data root, not just transient API responses.
- The frontend is intentionally performance-sensitive: viewer interactions, freshness, and telemetry are first-class concerns.
- Admin and observability routes are part of the main product surface, not an afterthought.
- Many changes in this repo are driven by implementation plans in `docs/`; reading the relevant plan before a larger refactor usually saves time.