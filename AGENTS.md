# AGENTS.md

## Cursor Cloud specific instructions

CartoSky is a single product with two dev services: a FastAPI backend (`backend/app/main.py`, port `8200`) and a React/Vite frontend (`frontend/`, port `5173`). Standard install/run/test commands live in `README.md` ("Getting Started" / "Testing"). Notes below are the non-obvious caveats discovered during setup.

### Running services
- Backend (activate venv first): `source .venv/bin/activate` then `uvicorn backend.app.main:app --host 127.0.0.1 --port 8200`. Health check: `GET http://127.0.0.1:8200/api/v4/health`.
- Frontend: `cd frontend && npm run dev -- --host 127.0.0.1 --port 5173`. The Vite dev server proxies `/api`, `/auth`, `/twf`, `/tiles` to the backend.

### Required local env files (gitignored — not in git, but persisted in the VM snapshot)
- `backend/.env.local` is **required**: `main.py` calls `load_dotenv("backend/.env.local")` at import, and `backend/app/auth/twf_oauth.py` raises `RuntimeError: Missing required env var` at import if these are unset. Minimum keys: `TWF_BASE`, `TWF_CLIENT_ID`, `TWF_CLIENT_SECRET`, `TWF_REDIRECT_URI`, `FRONTEND_RETURN`, `TOKEN_DB_PATH`, and `TOKEN_ENC_KEY` (must be a valid Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). Placeholder values are fine for local dev; also set `CARTOSKY_DATA_ROOT` and `CORS_ORIGINS`. If this file is ever missing, recreate it (see `deployment/systemd/api.env.example` for the full key reference).
- `frontend/.env.local` should set `VITE_API_BASE=http://127.0.0.1:8200` (and `VITE_TILES_BASE`). Without it the frontend defaults to the production API `https://api.cartosky.com`.

### Testing caveats
- Backend `pytest backend/tests`: ~722 pass, but ~70 fail on a clean checkout (e.g. `test_grid.py` assertion mismatches like `scale 0.01 != 0.1`, some `test_kuchera_*`). These are pre-existing repo test/code discrepancies, not environment problems.
- Backend `ruff check ...` reports pre-existing lint errors (e.g. `E703`) in committed code; ruff itself is installed and runs.
- Frontend e2e: run `cd frontend && npx playwright test` (it auto-launches its own Vite server on port `4173`). Only set `PLAYWRIGHT_USE_EXISTING_SERVER=1` if you already have a server running on `4173` — otherwise tests fail with `ERR_CONNECTION_REFUSED` (the dev server on 5173 is not used by the tests).
- The backend "screenshot service" uses the **venv** Playwright browser build, which differs from the frontend npm Playwright build; both chromium builds must be installed (the update script handles this). A missing browser only degrades the optional screenshot/share feature, not core API startup.

### Data dependency
- The map viewer (`/viewer`) needs published model artifacts under `CARTOSKY_DATA_ROOT`. These are produced by schedulers (`python -m app.services.scheduler --model <id>` from `backend/`) which fetch live NOAA/Herbie data over the network; without a scheduler run the viewer has no model frames.
- The Forecast page (`/forecast`) works without local data — it calls live Open-Meteo (geocoding) and NWS (`api.weather.gov`) APIs, so it needs outbound network access.
