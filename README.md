# The Weather Models (TWM)

A weather model pipeline and interactive map viewer. The system ingests GRIB2 output from NWP models (HRRR, optionally GFS/NAM), produces Cloud Optimized GeoTIFF (COG) artifacts plus packed grid frames, and serves them through the main API and a grid-rendered frontend.

## Architecture

```
Scheduler ──► Builder pipeline ──► Published artifacts
                                        │
                              ┌─────────┴─────────┐
                              API Server      Frontend
                         (discovery, grid,   (MapLibre GL,
                          sampling,            WebGL weather,
                          boundaries)          boundary vectors)
```

Three deployable components:

| Component | Entry point | Default port |
|-----------|-------------|--------------|
| API server | `backend.app.main:app` | 8200 |
| Scheduler | `python -m app.services.scheduler` | — |
| Frontend | Vite dev server / static build | 5173 |

## Models & Variables

**HRRR** (primary model):

| Variable key | Description | Units |
|---|---|---|
| `tmp2m` | 2 m Temperature | °F |
| `dp2m` | 2 m Dew Point | °F |
| `tmp850` | 850 mb Temperature | °F |
| `precip_total` | Total Precipitation | in |
| `snowfall_total` | Snowfall Total | in |
| `wspd10m` | 10 m Wind Speed | mph |
| `wgst10m` | 10 m Wind Gust | mph |
| `refc` | Composite Reflectivity | dBZ |
| `radar_ptype` | Radar Precip Type | — |

**Regions:** `conus` (default), `pnw`

Cycle hours 0 / 6 / 12 / 18 Z produce 48 forecast hours; all other cycles produce 18 forecast hours.

## Artifact Contract

Each published run contains per-forecast-hour files in `$CARTOSKY_DATA_ROOT/published/{model}/{region}/{run_id}/`:

| File | Format | Description |
|---|---|---|
| `fhNNN.val.cog.tif` | 1-band float32 COG | Raw values for hover sampling and backend analysis |
| `fhNNN.json` | JSON sidecar | `contract_version`, `model`, `region`, `run`, `var`, `fh`, `valid_time`, `units`, `kind` |
| `grid_v1/fhNNN.l0.u16.bin` | Packed uint16 binary frame | Primary weather rendering artifact |
| `grid_v1/fhNNN.l0.meta.json` | JSON frame metadata | Grid packing metadata |
| `grid_v1/manifest.json` | JSON grid manifest | Grid frame index and display contract |

Run manifests live at `$CARTOSKY_DATA_ROOT/manifests/{model}/{region}/{run_id}.json`.

## Prerequisites

- Python ≥ 3.11
- GDAL (system-level, required by `rasterio`)
- Node.js ≥ 20 (frontend only)
- An MBTiles file for vector boundary tiles

## Backend Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

Dev tools (ruff, pytest):

```bash
pip install -r backend/requirements-dev.txt
```

## Running the Services

> [!NOTE]
> The API and scheduler read from the same `CARTOSKY_DATA_ROOT`. Point them at the same data directory.

**API server:**

```bash
uvicorn backend.app.main:app --host 127.0.0.1 --port 8200
```

**Scheduler** (polls for new runs and builds value/grid artifacts):

```bash
cd backend
python -m app.services.scheduler --model hrrr
```

## Frontend Setup

```bash
cd frontend
npm install
npm run dev        # dev server on :5173
npm run build      # production build → frontend/dist/
```

## Environment Variables

### API server (`/etc/cartosky/api.env`)

| Variable | Default | Description |
|---|---|---|
| `CARTOSKY_DATA_ROOT` | `./data` | Root data directory |
| `CARTOSKY_JSON_CACHE_RECHECK_SECONDS` | `1.0` | Filesystem recheck interval for cached JSON |
| `CARTOSKY_SAMPLE_CACHE_TTL_SECONDS` | `2.0` | Point-sample result cache TTL |
| `CARTOSKY_SAMPLE_RATE_LIMIT_WINDOW_SECONDS` | `1.0` | Sampling rate-limit window (seconds) |
| `CARTOSKY_SAMPLE_RATE_LIMIT_MAX_REQUESTS` | `240` | Max sampling requests per window |

### Boundary tiles (`/etc/cartosky/api.env`)

| Variable | Default | Description |
|---|---|---|
| `CARTOSKY_BOUNDARIES_MBTILES` | — | Path to boundaries MBTiles file |
| `CARTOSKY_BOUNDARIES_TILESET_ID` | `cartosky-boundaries-v1` | TileJSON id |
| `CARTOSKY_BOUNDARIES_TILESET_NAME` | `CartoSky Boundaries v1` | TileJSON name |
| `CARTOSKY_TILES_PUBLIC_BASE_URL` | — | Public base URL for tile URL templates |

### Scheduler (`/etc/cartosky/scheduler.env`)

| Variable | Default | Description |
|---|---|---|
| `CARTOSKY_DATA_ROOT` | `./data` | Root data directory |
| `CARTOSKY_WORKERS` | — | Parallel frame build workers |
| `CARTOSKY_SCHEDULER_VARS` | `tmp2m,tmp850,dp2m,…` | Variables to build each run |
| `CARTOSKY_SCHEDULER_PRIMARY_VARS` | `tmp2m` | Variables built first (probe for availability) |
| `CARTOSKY_SCHEDULER_POLL_SECONDS` | `300` | Idle poll interval |
| `CARTOSKY_SCHEDULER_KEEP_RUNS` | `4` | Number of completed runs to retain |
| `CARTOSKY_HERBIE_PRIORITY` | `aws,nomads,…` | Herbie data source priority order |
| `CARTOSKY_HERBIE_SUBSET_RETRIES` | `4` | GRIB subset download retries |
| `HERBIE_SAVE_DIR` | — | Herbie GRIB cache directory |

### GFS Scheduler Rollout (`/etc/cartosky/scheduler-gfs.env`)

Use a dedicated env file for GFS so HRRR remains isolated. Initial rollout should use only:

| Variable | Recommended value | Description |
|---|---|---|
| `CARTOSKY_SCHEDULER_VARS` | `tmp2m,dp2m,tmp850,wspd10m,wgst10m,precip_total,snowfall_total,precip_ptype` | Core rollout vars for GFS |
| `CARTOSKY_SCHEDULER_PRIMARY_VARS` | `tmp2m` | Promotion/probe gate var |
| `CARTOSKY_SCHEDULER_PROBE_VAR` | `tmp2m` | Run-availability probe var |

### NAM Scheduler Rollout (`/etc/cartosky/scheduler-nam.env`)

Use a dedicated env file for NAM so rollout scope stays isolated:

| Variable | Recommended value | Description |
|---|---|---|
| `CARTOSKY_SCHEDULER_VARS` | `tmp2m,dp2m,tmp850,wspd10m,wgst10m,precip_total,snowfall_total,radar_ptype` | Initial NAM rollout vars |
| `CARTOSKY_SCHEDULER_PRIMARY_VARS` | `tmp2m` | Promotion/probe gate var |
| `CARTOSKY_SCHEDULER_PROBE_VAR` | `tmp2m` | Run-availability probe var |

### NBM Scheduler Rollout (`/etc/cartosky/scheduler-nbm.env`)

Use a dedicated env file for NBM so rollout scope stays isolated:

| Variable | Recommended value | Description |
|---|---|---|
| `CARTOSKY_SCHEDULER_VARS` | `tmp2m,precip_total,snowfall_total,wspd10m` | Initial NBM rollout vars |
| `CARTOSKY_SCHEDULER_PRIMARY_VARS` | `tmp2m` | Promotion/probe gate var |
| `CARTOSKY_SCHEDULER_PROBE_VAR` | `tmp2m` | Run-availability probe var |

### Frontend

| Variable | Default | Description |
|---|---|---|
| `VITE_API_BASE` | — | Override the API origin used by the frontend |

## API Reference

Base: `/api/v4`

Example: the health endpoint is `GET /api/v4/health`.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/` | API info |
| GET | `/capabilities` | All models, variables, and current availability (includes `latest_run_ready*` readiness fields) |
| GET | `/models/{model}/capabilities` | Single-model capabilities |
| GET | `/{model}/runs` | Available published runs |
| GET | `/{model}/{run}/manifest` | Run manifest |
| GET | `/{model}/{run}/vars` | Variables available for a run |
| GET | `/{model}/{run}/{var}/frames` | Frame list with sidecar-backed metadata |
| GET | `/{model}/{run}/{var}/grid-manifest` | Grid manifest for packed binary frame playback |
| GET | `/grid/{model}/{run}/{var}/{filename}` | Grid frame binary or metadata file |
| GET | `/sample` | Point-sample a raw value from a val COG |
| GET | `/{model}/{run}/{var}/{fh}/contours/{key}` | GeoJSON contour layer |
| GET | `/api/regions` | Region presets (bbox, center, zoom) |

### GFS Rollout Semantics

1. Backend capabilities may advertise the full GFS model catalog.
2. Scheduler configuration controls which vars are actually published during rollout.
3. `GET /api/v4/{model}/{run}/vars` is manifest-driven and only returns published vars for that run.
4. `GET /api/v4/{model}/{run}/{var}/frames` returns `[]` when the var is not published for that run.

**Vector boundary MVT tiles:**

```
/tiles/v3/boundaries/v1/{z}/{x}/{y}.mvt
```

## Rendering

The frontend renders weather overlays from packed grid manifests and binary frames. Boundary vectors are served from `/tiles/v3/boundaries/v1/*` through the main API.

## Deployment

Systemd unit files are in `deployment/systemd/`. Copy the example env files and adjust paths:

```bash
cp deployment/systemd/api.env.example           /etc/cartosky/api.env
cp deployment/systemd/scheduler.env.example     /etc/cartosky/scheduler.env
cp deployment/systemd/scheduler-gfs.env.example /etc/cartosky/scheduler-gfs.env
cp deployment/systemd/scheduler-nam.env.example /etc/cartosky/scheduler-nam.env
cp deployment/systemd/scheduler-nbm.env.example /etc/cartosky/scheduler-nbm.env
```

Services expect the virtualenv at `/opt/cartosky/.venv` and the project at `/opt/cartosky/`. Boundary vector tiles are now served directly by the main API under `/tiles/v3/boundaries/v1/*`; see `docs/NGINX_V3.md` for the recommended nginx routing.

For model schedulers, deploy both units and keep env files isolated per model:

```bash
sudo systemctl enable csky-hrrr-scheduler csky-gfs-scheduler csky-nam-scheduler csky-nbm-scheduler csky-api
```

## Adding a New Model

Models are self-contained plugins. Adding one requires three steps: create the plugin module, register it, and add color maps for its variables.

### 1. Create the plugin module

Create `backend/app/models/mymodel.py`, following the pattern in [backend/app/models/hrrr.py](backend/app/models/hrrr.py).

**Define regions** — one `RegionSpec` per geographic coverage area:

```python
from .base import BaseModelPlugin, ModelCapabilities, RegionSpec, VarSelectors, VarSpec, VariableCapability

MY_REGIONS: dict[str, RegionSpec] = {
    "conus": RegionSpec(
        id="conus",
        name="CONUS",
        bbox_wgs84=(-134.0, 24.0, -60.0, 55.0),
        clip=True,
    ),
}
```

**Define variables** — one `VarSpec` per field. Use `primary=True` for variables fetched directly from GRIB, `derived=True` for variables computed from other fields:

```python
MY_VARS: dict[str, VarSpec] = {
    "tmp2m": VarSpec(
        id="tmp2m",
        name="2m Temperature",
        selectors=VarSelectors(
            search=[":TMP:2 m above ground:"],
            filter_by_keys={"shortName": "2t", "typeOfLevel": "heightAboveGround", "level": "2"},
            hints={"upstream_var": "t2m"},
        ),
        primary=True,
        kind="continuous",   # "continuous" | "discrete"
        units="F",
    ),
    # derived example — computed from u/v components:
    "wspd10m": VarSpec(
        id="wspd10m",
        name="10m Wind Speed",
        selectors=VarSelectors(hints={"u_component": "10u", "v_component": "10v"}),
        derived=True,
        derive="wspd10m",   # derive strategy id used by builder/derive.py
        kind="continuous",
        units="mph",
    ),
}
```

**Implement the plugin class** — extend `BaseModelPlugin` and implement at minimum `target_fhs()`, which returns the list of forecast hours to build for a given cycle hour:

```python
class MyModelPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        # Example: 6-hourly cycles produce 240 hours, others produce 120
        if cycle_hour in {0, 6, 12, 18}:
            return list(range(0, 241, 3))
        return list(range(0, 121, 3))

    def normalize_var_id(self, var_id: str) -> str:
        # Optional: map alternative names to canonical keys
        if var_id.lower() in {"t2m", "2t"}:
            return "tmp2m"
        return var_id
```

**Build `ModelCapabilities`** — maps variable keys to `VariableCapability` objects and declares run-discovery config:

```python
MY_COLOR_MAP_BY_VAR_KEY = {"tmp2m": "tmp2m", "wspd10m": "wspd10m"}
MY_CONVERSION_BY_VAR_KEY = {"tmp2m": "c_to_f", "wspd10m": "ms_to_mph"}

MY_VARIABLE_CATALOG: dict[str, VariableCapability] = {
    var_key: VariableCapability(
        var_key=var_key,
        name=spec.name,
        selectors=spec.selectors,
        primary=spec.primary,
        derived=spec.derived,
        derive_strategy_id=spec.derive,
        kind=spec.kind,
        units=spec.units,
        color_map_id=MY_COLOR_MAP_BY_VAR_KEY.get(var_key),
        conversion=MY_CONVERSION_BY_VAR_KEY.get(var_key),
        buildable=bool(spec.primary or spec.derived),
    )
    for var_key, spec in MY_VARS.items()
}

MY_CAPABILITIES = ModelCapabilities(
    model_id="mymodel",
    name="My Model",
    product="sfc",
    canonical_region="conus",
    grid_meters_by_region={"conus": 13_000.0},
    run_discovery={
        "probe_var_key": "tmp2m",
        "probe_enabled": True,
        "cycle_cadence_hours": 6,
        "probe_attempts": 4,
        "fallback_lag_hours": 6,
    },
    ui_defaults={"default_var_key": "tmp2m", "default_run": "latest"},
    variable_catalog=MY_VARIABLE_CATALOG,
)

MY_MODEL = MyModelPlugin(
    id="mymodel",
    name="My Model",
    regions=MY_REGIONS,
    vars=MY_VARS,
    product="sfc",
    capabilities=MY_CAPABILITIES,
)
```

### 2. Register the plugin

Add the model to `MODEL_REGISTRY` in [backend/app/models/registry.py](backend/app/models/registry.py):

```python
from .mymodel import MY_MODEL

MODEL_REGISTRY: dict[str, ModelPlugin] = {
    HRRR_MODEL.id: HRRR_MODEL,
    MY_MODEL.id: MY_MODEL,
}
```

For optional models (e.g., those with extra dependencies), wrap the import in a try/except as done for GFS.

### 3. Add color maps

Each `color_map_id` referenced in `MY_COLOR_MAP_BY_VAR_KEY` must be present in the palette catalog in [backend/app/services/colormaps.py](backend/app/services/colormaps.py). Add an entry to the `COLOR_MAPS` dict keyed by the `color_map_id` string. See existing entries in that file for the expected format (stops, kind, units, etc.).

### 4. Run the scheduler

```bash
cd backend
python -m app.services.scheduler --model mymodel
```

Configure which variables to build via `CARTOSKY_SCHEDULER_VARS` in the scheduler env file.

## Building Vector Boundaries

```bash
bash scripts/build_boundaries_tileset.sh
```

Produces the MBTiles file referenced by `CARTOSKY_BOUNDARIES_MBTILES`.
