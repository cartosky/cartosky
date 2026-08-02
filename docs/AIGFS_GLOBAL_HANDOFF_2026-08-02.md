# AIGFS Global Support — Agent Handoff Brief (2026-08-02)

You are picking up the second model in a proven rollout pattern. GFS global
shipped end-to-end in the week of 2026-07-28 → 08-02: native EPSG:4326
storage, antimeridian seam, user-facing Coverage toggle, globe projection,
and global ERA5 anomaly baselines — all live on prod. AIGFS is next, per
the locked order GFS → AIGFS → AIFS → ECMWF. Your job is to repeat the
pattern, not re-derive it.

## Normative documents (read in this order)

1. `docs/GLOBAL_DOMAIN_4326_CONTRACT.md` — the artifact contract. Grid:
   1440×721 point-registered 0.25°, cell-edge bbox
   (−180.125, −90.125, 179.875, 90.125), projection declared in frame
   metadata + grid manifest, lon +180 wraps to the −180 column. AIGFS is a
   0.25° global source, so it adopts this contract verbatim.
2. `docs/MAX_WEEK_EXECUTION_PLAN_2026-07-27.md` — Phase 3 section: the
   amended grid policy, the per-model stop-and-verify checklist (G1–G6,
   with the two retracted lines), and the AIGFS disk row marked "recompute
   under native-4326".
3. `docs/GLOBAL_STORAGE_G1_INVESTIGATION_2026-07-29.md` — why the format
   is what it is. Background only.

## What already exists (do not rebuild)

- **Backend mechanism is model-generic.** A model opts in by declaring
  `grid_native_degrees_by_region = {"global": 0.25}` on its plugin
  (see `backend/app/models/gfs.py` for the pattern) plus per-variable
  `supported_build_regions` extras. `get_target_grid()` in
  `backend/app/services/builder/raster_grid.py` resolves the contract grid;
  `warp_to_target_grid` takes a bit-exact longitude-roll identity path for
  aligned 0.25° sources (test-pinned for GFS — pin it for AIGFS too).
- **The gate flag** `CARTOSKY_GLOBAL_DOMAIN_MODELS` (comma-separated model
  ids) gates scheduler builds, API domain routes, and capability
  advertising, read per-unit from `/etc/cartosky/*.env`. Adding `aigfs`
  is an operator step at rollout time.
- **The frontend needs ZERO work.** The Coverage toggle, globe, seam
  handling, hover/labels, share/export, and run-aware degradation are all
  capabilities-driven and model-agnostic. When AIGFS's capabilities
  declare global, the UI appears. Do not touch `frontend/`.
- **ERA5 anomaly baselines are shared.** The baseline path has no model
  segment (`/opt/cartosky/data/climatology/v1/era5/baseline/{field}/global/…`)
  — the 17 GiB installed for GFS serves AIGFS free. If AIGFS's catalog has
  the instantaneous anomaly variables (hgt500/tmp2m/tmp850), they need the
  same `baseline_region_by_build_region: "global=global"` hint the GFS
  specs carry (`gfs.py` ~:427/:489/:519) plus allowlist entries mirroring
  `GFS_GLOBAL_ANOMALY_VAR_KEYS`. Precip anomalies stay excluded (Wave 2's
  capability flip is a separate, GFS-first step).
- **Retention, CF caching, manifests, sampling, contours** — all
  domain-aware and live-proven for GFS. Domain retention is per-domain and
  isolated (`scheduler.py` ~:2880); the CF `/contours/` rule covers domain
  paths (verified live 2026-07-30).

## What AIGFS specifically needs

1. **Declarations**: `grid_native_degrees_by_region`, the global RegionSpec
   (±90 bbox_wgs84), `supported_build_regions` extras on the appropriate
   variable set (mirror how `GFS_GLOBAL_BUILD_REGIONS` applies to
   non-anomaly vars, plus anomaly hints if the catalog has them). Check
   which composite/companion variables AIGFS carries — GFS needed a
   composite-inheritance fix (`ptype_intensity_` components inherit the
   parent's build regions; test exists, extend it).
2. **Source verification**: confirm AIGFS GRIBs are 0.25° 0–360 global so
   the identity roll applies. If any AIGFS variable's source grid differs,
   the reproject fallback handles it — but then the exactness pins must
   reflect that honestly, not be copied from GFS.
3. **Fetch path caution**: AIGFS fetches are AWS-EAGLE-mirror-first with
   NOMADS fallback (anti-abuse throttling incident 2026-07; the mirror lags
   NOMADS ~4–6 h). Global builds reuse the same GRIB files as canonical —
   no new download volume — but verify the warp-count increase doesn't
   change fetch behavior.
4. **Disk checkpoint**: the old sizing row (58.8 GiB at 25 km 3857) is
   void. Expect roughly 1/2.478 of a same-variable-set 3857 footprint;
   GFS measured 23 GiB/run × keep_runs. Compute AIGFS's expected number
   from its variable set BEFORE the first prod build, and verify after.
5. **Tests**: mirror `backend/tests/test_gfs_global_domain.py` and the
   antimeridian suite (`test_gfs_global_antimeridian.py`) for AIGFS —
   dark-by-default, activation, grid pins, roll-exactness (assert the
   BRANCH taken, not just values — value-equality is vacuous on aligned
   grids), anomaly allowlist in both directions.

## Gotchas that actually bit us (avoid re-learning)

- **Dual import-name landmine**: prod API runs as `backend.app`, scheduler
  and pytest as `app`. Absolute `from app....` imports inside try/except
  fail silently ONLY in the API process. Structural fix exists in
  raster_grid; don't reintroduce the pattern. Symptom: works in
  scheduler/tests, 404s on API.
- **Domain manifests list only domain-declared variables** (deliberate,
  review M3). Anything populating UI from a run manifest must not assume
  the full catalog.
- **Value-equality tests on aligned grids are vacuous** — reproject is
  bit-identical to roll there. Pin the code path (spy/monkeypatch), not
  just outputs. Same for "byte-identity" claims: accumulator seeding
  (identity-seeded reduce vs first-element) changes signed-zero bits.
- **Commit hygiene**: multiple sessions share this tree. Commit by
  explicit path list, never `git add -A`/`-a` (a sweep commit once
  swallowed another stream's files and deleted a runbook).
- **`.gitignore` allowlist trap**: `frontend/tests/*` and several dirs
  ignore new files by default; every new test file needs an explicit `!`
  entry (structural fix exists for e2e; verify with `git add -n`).
- **Playwright on this Mac**: run golden suites ALONE (SwiftShader context
  exhaustion under parallel WebGL suites causes phantom failures);
  goldens' timing.json is a generated artifact.
- **Operator prod flow**: Mac → push → prod pull; never edit on the
  server; scheduler AND api both need restarts for descriptor changes;
  agents never execute on prod — scripts are committed, Brian runs them.

## Process requirements (non-negotiable)

- **Phased with hard stop-and-verify gates.** Implement dark → fresh-
  context adversarial verifier round (expect it to refute something; fix
  and re-verify) → operator prod gate with explicit ⛔ checkpoints and
  expected outputs → flag flip. Brian tests each phase before the next.
- **The per-model checklist** (plan doc, Phase 3) is the rollout gate:
  manifest vs actual availability, G1 antimeridian oracle, G2 CF
  HIT/no-DYNAMIC, G3 performance vs NA baseline with the decision rule, G4
  screenshot/share/GIF both capture paths, G5 memory vs baselines, G6 both
  cycle types + anomaly capabilities matching the Phase 3A wave state,
  domain isolation under load, disk checkpoint, mobile spot-check.
- **Timing comparisons are same-session A/B only.**
- **Direct disagreement is preferred over diplomatic smoothing.** If
  something in this brief contradicts what you find in the code, the code
  and the contract doc win — surface the contradiction, don't accommodate
  it. Confidently wrong values are worse than missing ones; hover/labels
  must never disagree with the picture.
- **Nothing goes live until the operator flips the flag** (adds `aigfs` to
  the env on both units and restarts). Everything before that must be
  provably dark: capabilities must not advertise, routes must 404.

## Definition of done

AIGFS global builds publish on the contract grid behind the flag; the full
per-model checklist passes on prod; the operator flips the flag; the
Coverage toggle appears for AIGFS with correct data, anomalies included
(if catalog applies); goldens and canonical AIGFS behavior bit-identical
throughout. Two solid global models beat four shaky ones — AIFS/ECMWF are
explicitly out of scope for this effort.
