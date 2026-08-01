# Skew-T Viewer Panel — Design — 2026-07-30

Design for adding interactive Skew-T/log-p soundings to the map viewer, based on the
2026-07-30 feasibility spike (both halves run **on prod** — fetch-path throughput was
measured on cartosky-server against AWS BDP, and the rendering prototype was verified
against known-good products). Decisions already locked with Brian:

- **Model scope v1:** HRRR only (CONUS, hourly cycles).
- **v1 plot scope:** basic profile — T, Td, wind barbs on true skew-t/log-p axes with
  precomputed background lines. Parcel/indices/hodograph are later phases.
- **Data architecture:** bundled **profile-stack artifact** — all levels + variables in one
  binary file per forecast hour, point-sampled by seek (meteogram pattern).
- **UI:** map click → sounding panel with forecast-hour scrubbing.
- **Visual target:** Tropical Tidbits sounding layout, restyled to CartoSky dark.
- **Thermo policy:** MetPy computes (backend/build-time), JS only draws. The client
  contains zero thermodynamics.

Spike artifacts: `profile_okc.json`, `background_lines.json`, `gen_background.py`,
self-contained `skewt_prototype.html` (session scratchpad `skewt-spike/`; spike scripts
also at `/tmp/skewt-spike` on prod — note that path is tmpfs).

---

## 1. Source data (measured, HRRR `prs` 2026-07-30 12z fh=6)

HRRR pressure-level GRIB carries TMP, DPT, RH, SPFH, UGRD, VGRD, HGT at 40 levels:
"1013.2 mb" plus 1000→50 mb every 25 mb.

**Ship set: TMP, DPT, UGRD, VGRD, VVEL at the 37 levels 1000→100 hPa**, plus a surface
block (§2). VVEL is included from day one (decision 2026-07-30): it exists only to feed
the Phase 5 omega strip, but carrying it now (+~25%) avoids a format bump and
unbackfillable old runs later. Exclusions, each deliberate:

- **RH dropped, DPT taken natively — hard requirement.** HRRR reports upper-level RH with
  respect to ice; MetPy (and most re-derivations) saturate over liquid. Measured
  divergence of derived-vs-native Td: ≤0.15 °C below 700 hPa but **up to ~11 °C at
  200–150 hPa** (inverse RH error up to 24.5%). Deriving Td would put visibly wrong
  moisture on the upper half of every sounding. Never derive what the model publishes.
- **1013.2 mb excluded** — sub-surface/extrapolated level.
- **HGT omitted** (+25% cost for an axis we can reconstruct hypsometrically from p/T/Td
  at read time if a height ladder is ever drawn).
- 75/50 mb exist free of charge if the plot top ever extends above 100 hPa.

## 2. Surface block — required, learned from product comparison

Comparing our prototype to Pivotal/Tropical Tidbits soundings of the **same run/fh/point**
exposed two defects that the isobaric ladder alone cannot fix:

1. The 1000 hPa level is **below ground** over most of CONUS (OKC: real surface ≈970 hPa,
   36.7 °C; extrapolated 1000 hPa plotted a fictitious ~39 °C).
2. A surface-based parcel origin is unrepresentable: spike SBCAPE from isobaric-only data
   was **279 J/kg vs 966** from the anchored product (identical data; the ML flavors
   agreed, confirming this is parcel-origin error, not data disagreement).

Therefore each profile carries a 5-value surface block: **PRES:surface, TMP:2m, DPT:2m,
UGRD:10m, VGRD:10m**. The client masks isobaric levels with p > surface pressure and
anchors the trace at the surface values. Fetch cost is ~5 small GRIB messages per fh
(negligible next to the ladder; estimate, not measured).

## 3. Profile-stack artifact

### Grid and packing

- Horizontal: HRRR 3 km grid (1799×1059) decimated **every 4th point → 450×265
  (~12 km, 119,250 pts)**. Soundings are point products; 3 km pick-precision is not
  needed, and full-res stacks are not viable (~108 GB/day at 4 long runs/day).
- Values: **uint16**, existing `code * scale + offset` convention, 65535 nodata sentinel,
  **per-variable** scale/offset (T/Td/U/V spans differ). 8-bit rejected: ~0.5 °C
  quantization visibly stair-steps a trace.

### Layout — pixel-major, one profile = one read

The existing seek sampler (`read_binary_sample_value_seek`,
`backend/app/services/sampling.py`) reads `(row*width + col) * itemsize` from one C-order
plane. Plane-major stacking would cost 185 seeks per sounding (adjacent planes ~239 KB
apart at the decimated grid; 3.81 MB at full res). The stack is instead **pixel-major**:

    offset(row, col, plane) = ((row*width + col) * n_planes + plane) * 2
    plane = level_index * n_vars + var_index        # level outer, var inner

Each pixel owns a contiguous **185 planes × 2 B = 370 B** run (+10 B surface block, stored
as 5 additional trailing planes per pixel → 380 B total): one seek, one small read —
cheaper per request than today's per-frame meteogram reads. Tradeoff: not mmap-able as a
raster; irrelevant, this artifact is sampling-only. Map rendering keeps using existing
per-variable plane files.

### Sidecar (per fh, alongside the stack)

Extends the existing frame sidecar: `format_version`, `width`, `height`, `transform`
(decimated-grid affine), `projection`, plus ordered `levels` (hPa), ordered `variables`,
`surface_fields`, and per-variable `scale`/`offset`/`nodata`. Because the reader is fully
sidecar-driven (n_planes, ordering, scaling all declared), adding VVEL or levels later is
a `format_version` bump on new runs, no migration of old ones.

### Sizing (measured grid, derived arithmetic)

| Config | Per fh | 18-fh run | 48-fh run | Day (4×48 + 20×18)¹ |
|---|---|---|---|---|
| **Ship: 5 vars + sfc, 12 km, u16** | **45.6 MB** | 0.82 GB | **2.19 GB** | **≈25 GB** |
| without VVEL (4 vars + sfc) | 36.5 MB | 0.66 GB | 1.75 GB | ≈20 GB |
| (rejected) 4 vars, 3 km, u16 | 563.9 MB | 10.15 GB | 27.07 GB | ≈108 GB |

¹ HRRR cadence: 00/06/12/18z run 48 fh, others 18 fh. **Retention: match the HRRR raster
retention window** (decision 2026-07-30) — stacks age out with the runs they belong to.

## 4. Fetch (measured on the prod path)

- 4-var × 37-level idx-subset for one fh: **~101 MB, ~12.6 s at the measured 7.99 MB/s**
  steady from AWS BDP (185 messages when RH was included: 129.17 MB / 16.17 s). VVEL was
  not in the measured set; by analogy to the measured per-variable sizes (20.8–35.2 MB)
  the 5-var ship set is **~121–126 MB/fh, ~15–16 s** (estimate). **No throttling, no
  302s** — but fetch stays AWS-first with sequential, paced requests per the AIGFS/NOMADS
  throttle incident playbook.
- Per run (5-var, derived): ~2.2 GB / ~5 min (18 fh); **~5.9 GB / ~13 min (48 fh)**,
  download-dominated.
  Note the asymmetry: decimation shrinks storage 16× but **not** download — full-res GRIB
  is pulled, decimated after decode. Levels/variables are the only download levers.
- Herbie gotcha (cost a spike false-start): `search` regexes match the **whole idx line**,
  so a `^TMP:` anchor silently matches zero messages and `download()` returns a path to a
  file it never created. Anchor patterns on `:`.

## 5. Serving

New endpoint, meteogram-shaped: **`POST /api/v4/forecast/sounding`** with
`{model, lat, lon, run?}` → all forecast hours in one response (a sounding panel scrubs
time; per-fh requests would waterfall):

- Resolve run via existing manifest/run-resolution logic (same stale-run fallback rules).
- **Row-convention warning (bit us at the Phase 1 prod gate):** the stack affine is
  north-up (row 0 = north), while xarray/cfgrib GRIB decodes index south-up. The spike's
  OKC point "row 417, col 899" (south-up, full grid) is stack row 160, col 225 (north-up,
  decimated). Always derive (row, col) from the sidecar affine — never reuse
  xarray-derived indices.
- lat/lon → (row, col) once via the decimated affine; one 306 B seek-read per fh.
- Response per fh: `{fh, valid_time, surface: {...}, levels: [...], t: [...], td: [...],
  u: [...], v: [...], w: [...]}` in physical units (server decodes scale/offset) +
  grid-point lat/lon and its distance from the request point. Omega (`w`) rides along
  from day one even though nothing draws it until Phase 5.
- v1 returns raw profiles only. Indices/parcel curves join the response in Phase 4,
  computed server-side with MetPy at request time (37-level column math is trivial;
  no precomputation or caching until measured need).

## 6. Frontend

- **Entry:** map click (likely a "Sounding" mode/affordance rather than hijacking plain
  click — exact interaction TBD in Phase 3 with the viewer chrome) → panel/modal, marker
  at the sampled grid point. Forecast-hour scrubber synced to the viewer's global fh
  state. URL-syncable (`sounding=lat,lon`) like other viewer state.
- **Rendering:** SVG, following the validated prototype: exact 45° skew
  (`x = x0 + (t−Tmin)·scale + (yBottom−y)`), log-p y-axis 1050→100 hPa, isobar/isotherm
  labels, wind-barb margin (half=5 kt, full=10, pennant=50, barbs left-of-travel).
- **Background lines are frozen constants:** `gen_background.py` (MetPy: `dry_lapse`,
  `moist_lapse`, mixing-ratio via `dewpoint`) emits 42 curves as data-coordinate
  polylines — 25 KB JSON, ~4 KB gzipped — checked in as a generated asset with the script.
  Regenerated only if the plot domain changes.
- **Display policies** (from product comparison):
  - Mask levels below surface pressure; anchor traces at the surface block.
  - **Upper-level Td: clip/fade where T < −40 °C** (native ice-consistent DPT converges
    toward T near 100 hPa; stratospheric moisture is display noise). Open decision #3.
- Prototype styling (near-black bg, red T / green Td, faint tone-coded backgrounds)
  carries over; TT layout is the composition reference.

## 7. Phases

| Phase | Scope | Gate |
|---|---|---|
| **1 — Pipeline** | Fetch ship-set + surface block, stack writer + sidecar, scheduler wiring behind `CARTOSKY_SOUNDING_MODELS` (empty default, HRRR when on), retention hookup | Stacks publishing on prod for full runs; spot-check vs direct GRIB decode |
| **2 — Endpoint** | `/forecast/sounding`, seek sampler for stacks, tests in the Layer-1/2 style | Served profile byte-identical to decoded stack; OKC parity vs spike profile |
| **3 — Viewer panel (v1 ship)** | Map-click entry, panel, SVG plot, scrubber, URL sync, dark styling | Prod gate: real-phone + desktop review vs TT reference |
| **4 — Parcel & indices** | MetPy server-side: SB/ML parcel path, CAPE/CIN, LCL/LFC/EL markers, readout panel. **First prod MetPy dependency** — this phase adds `metpy` to backend/requirements.txt + installs into the prod API venv (Phases 1–3 never run MetPy on prod; Phase 3 background lines are generated offline and checked in) | Values match TT/SHARPpy same-data within tolerance (document chosen tolerance) — **requires decision #5 first**; see v2 prototype finding below |
| **5 — Overlays** | Wet-bulb, hodograph (U/V already in stack), omega strip (VVEL already in stack per decision 2026-07-30), DGZ, θe inset | Per-overlay visual gates |
| **6 — Multi-model (GFS + ECMWF)** | Designed 2026-08-01, NOT started — see §10 | Per-model parity + visual gates |

Phases 1–2 are backend-only and shippable dark; Phase 3 is the first user-visible ship.

**Phase 1 status: COMPLETE — prod gate closed 2026-07-30.** Deployed with
`CARTOSKY_SOUNDING_MODELS=hrrr` on csky-hrrr-scheduler; live backfill verified (fetch
109 MB prs + 8.5 MB sfc from AWS, stacks exactly 450×265×380 B, published-promotion and
manifest sweep working); CLI spot-check at OKC (stack row 160/col 225) matched the spike
reference — surface pressure 969.2 vs 968.5 hPa, column agreement within diurnal
evolution. Band-tag→plane mapping validated against real GRIB.
Implementation: `backend/app/services/sounding.py` (new), `sounding_models()` in config,
scheduler hook + top-level `sounding` manifest section, 67 new tests; verifier
hand-decoded a planted pixel's 380-byte block with independent offset arithmetic and
confirmed exception containment, frontier gating, dark-by-default, zero catalog
contamination. Watch items: (a) the sounding pass is synchronous inside the catch-up
loop — each newly eligible fh adds one ~15 s GRIB fetch per round (publish unaffected;
later-fh build wall clock grows) — thread it if it matters; (b) encode clips silently
between the physical-validity floor and the representable floor (e.g. −130 °C → −120),
all physically unreachable for HRRR.

**Phase 2 status: COMPLETE — prod gate closed 2026-07-31.** Deployed with the flag on
csky-api; live endpoint-vs-direct-decode parity at OKC was exact (worst |Δ| = 0.0 across
all 190 planes, run 20260731_02z), serving partial runs correctly while stacks trail the
build frontier. `backend/app/services/sounding_api.py` (new) + route in main.py +
`tests/test_forecast_sounding_api.py` (18 tests; 105 green across the four
sounding/meteogram modules). Verification found and we fixed a **run-pin path traversal**
(`run: "../secret"` could read stacks outside the model root) — now gated by the shared
`RUN_ID_RE` before any filesystem access, with regression tests that plant a readable
decoy and prove it is never read. Also hardened: unprojectable points (poles) → 400 not
500; `run` field length-capped. Deferred to Phase 3: rate limiting (map-click panels are
chattier than meteograms) and an in-process response cache if needed. Prod gate: add
`CARTOSKY_SOUNDING_MODELS=hrrr` to csky-api.service + restart, then endpoint-vs-CLI
parity at OKC.

**Phase 3 status: implemented + verified 2026-07-31** (uncommitted; prod gate = deploy +
real-device pass). Sounding toggle in the top bar (overflow menu on mobile), armed
map-click → reticle + docked right panel (bottom sheet <768px), SkewTChart SVG ported
from the v4 prototype with constants verified verbatim against the template, surface
anchoring/below-ground masking client-side, `sounding=lat,lon` permalink, one-way fh
follow with panel-local scrub override (no refetch on scrub), route rate limit 40/60s.
Tests: 16 geometry unit tests, 10-test e2e (deep-link test hardened against dev-server
compile latency after a flake under worker contention), 19 backend. Visual gate passed
locally against the live prod endpoint (OKC deep link, Denver re-pick with
elevation-correct ~835 hPa anchoring, mobile sheet). Accepted quirks: rate limiter keys
on client.host without XFF (matches meteogram precedent — revisit both together);
viewer→panel fh follow verified by inspection, not e2e.

**Phase 4 status: COMPLETE — prod gate closed 2026-07-31.** (Deploy hiccup worth
remembering: the first restart round ran against a prod checkout one commit behind —
symptom was a Phase 3-shaped response with no `indices` key; fix was just the pull.)
Live verification: OKC afternoon SBCAPE 1557–2120 J/kg decaying to 491 by evening,
parcel path every frame, definition caption served; stable evening profiles correctly
show 0/no-markers. Native-SBCAPE row appears from the first post-restart (v2) run. `sounding_indices.py` (new): per-frame SB parcel per decision #5
(2 m T/Td, Tv-corrected — identity gap +122 J/kg over plain SB, pinned by test), ML
CAPE/CIN, LCL/LFC/EL (from the drawn parcel curve so markers sit on the visible
intersection), PWAT, 37-level parcel path; reproduces the spike reference to the digit
(independent MetPy cross-check). Stack format_version 2 adds native `CAPE:surface`
(191 planes / 382 B); reader fully sidecar-driven, mixed v1+v2 runs serve (fingerprint
excludes surface fields — accepted: masks a theoretical surface-unit flip, writer-owned
constants today). Endpoint degrades per-frame; MetPy missing → nulls, never 500.
Measured: ~1.2–1.4 s MetPy per 49-frame request on dev hardware — no cache yet;
the pre-staged lever is an in-process TTL response cache if prod feels slow. Frontend:
dashed parcel path beneath traces, LCL/LFC/EL edge ticks, indices readout w/ conditional
HRRR SBCAPE row + server-supplied parcel_definition caption. Verification initially
REFUTED two endpoint tests — the synthetic fixture column was convectively stable
(SBCAPE 0.496 → rounds to 0, originally passed on a rounding knife-edge); fixed by
steepening the fixture lapse to ~8 K/km. `CAPE:surface` confirmed present in real HRRR
sfc files at f00 (anl) and f06. Backend sounding modules 115 green; e2e 12; build/tsc
clean.

**Phase 5 status: implemented + verified 2026-07-31** (uncommitted; prod gate =
per-overlay visual review). All four overlays plus the response cache.
Layout is Brian's locked decision: **stacked sections** in the panel — Skew-T
chart → side-by-side `[hodograph | θe]` row → indices box, same order in the
mobile sheet, collapsing to one column under 384 px. No floating insets, no
tabs. Backend: `sounding_indices` gained a per-frame `profiles` block
(`tw`, `theta_e`, `height_m_agl` + the two surface anchors) computed on the same
anchored column as the indices and shipped **level-aligned with nulls below
ground**, so the client's parallel-array indexing is unchanged. Heights are
integrated hypsometrically per §1 (layer-mean virtual temperature, vectorised;
pinned against MetPy's `thickness_hydrostatic` layer by layer to 0.5 m —
`pressure_to_height_std` would answer the standard atmosphere, not this column).
Frontend: wet-bulb trace, left-gutter omega strip (ascent = gold toward the
plot, ±2 Pa/s clamp, |w| ≥ 0.05), DGZ band with **interpolated** crossing
pressures (multi-slab capable), and the two new inset components; all the math
lives in `lib/skewt-geometry.ts` + the new `lib/sounding-insets.ts`.
Cost and the two levers, measured on dev hardware for a 49-frame request.
Serial, the profiles cost 0.93 s → **2.16 s**: MetPy's `wet_bulb_temperature`
is ~1.3 s of that and dominates the phase. Two things were measured before
being built, and the results are worth keeping:

- **Batching the MetPy calls does NOT work.** `wet_bulb_temperature` runs one
  SciPy `solve_ivp` moist-lapse integration **per element** — ~660 µs/element
  at n=36 degrading to ~1060 µs/element at n=1764 — so concatenating all 49
  frames into one call was *slower* than 49 sequential calls (best case 0.92×,
  bit-identical results). It does not vectorise. `equivalent_potential_temperature`
  genuinely does (1.97 ms for 1764 points) and is not a contributor.
- **Frames are independent and the work is GIL-bound, so a process pool is the
  lever.** `sounding_thermo.py` (new) fans the whole per-frame thermo pass —
  Phase 4 indices/parcel *and* Phase 5 profiles — across a lazily created,
  process-lifetime `ProcessPoolExecutor` using the **spawn** context (never
  fork: the API is an async server with helper threads). Workers import only
  `sounding_indices` + numpy, with MetPy lazy inside it. Anything that can go
  wrong — pool won't start, submit fails, executor breaks mid-request —
  degrades transparently to the serial loop and latches off, logged once.
  `CARTOSKY_SOUNDING_THERMO_WORKERS=0` forces serial as an ops escape hatch.

| 49-frame request | |
|---|---|
| serial, Phase 4 shape (no profiles) | 929 ms |
| serial, Phase 5 | 2163 ms |
| **pooled (4 workers), warm** | **622 ms** |
| pooled, first request (spawn + MetPy import per worker) | 3478 ms |
| cache hit | 0.2 ms |

Pooled output is byte-identical to serial across all 49 frames. The warm
pooled request is now cheaper than Phase 4's serial baseline was; the one-off
~3.5 s first request per process is the accepted cost of spawn. The Phase 4
pre-staged cache is also in: 300 s / 128-entry in-process TTL in `sounding_api`
keyed `(model, run, row, col, fh-count, newest fh)` — the fh set is in the key
so a partially built run cannot serve a stale short list.
Rejected and recorded: precomputing wet-bulb into the stack at build time is
infeasible without abandoning MetPy (4.4 M per-element ODE solves per fh);
revisit only alongside a closed-form (Stull/Davies-Jones) decision at some
future `format_version` bump. Tests: 142 backend (was 115), 128 frontend unit
(hodograph band splitting, ring auto-scale, θe domain, DGZ interpolation,
omega sign/clamp, `anchorSeries`), 14 sounding e2e (was 12) including a
fixture frame engineered with no DGZ and one with no `profiles` at all.

**Verification round (2026-07-31, fresh-context):** initially REFUTED on one
real bug — the response cache served **request-scoped fields** (`location`,
`grid_point.distance_km`) from one caller to another in the same 12 km cell
("~0.0 km from click" for a pick 2.2 km away). Fixed: `_with_request_fields`
attaches location/grid_point/requested_run AFTER cache retrieval; the cached
body is cell-scoped only; regression test covers the two-callers-one-cell case
and re-verification proved B gets its own echo on a genuine cache hit. The
pool-parity test now runs from a checked-in fixture
(`backend/tests/data/sounding_profile_okc_v2.json`) instead of skipping outside
this session. Everything else confirmed independently: physics to ≤0.005 °C/K
and ≤0.05 m of the verifier's own MetPy/hypsometric computations, spawn-context
isolation (worker sys.modules carries no FastAPI/app.main), all degradation
paths, geometry invariants after the margin change, cache eviction at the cap.
Known pre-existing issue tracked separately: flaky map-click request-log e2e
tests (nondeterministic missed clicks; not introduced by this work).

## 8. Decisions

1. **VVEL from day one — RESOLVED yes (Brian, 2026-07-30).** +~25% storage/fetch; avoids
   a later format bump and unbackfillable runs. Reflected in §1/§3/§4/§5.
2. **Retention — RESOLVED (Brian, 2026-07-30): match HRRR raster retention.** Stacks age
   out with the runs they belong to.
3. **Td upper-level policy — RESOLVED (Brian, 2026-07-30, v3 prototype review): draw
   full**, no fade/clip. Also from that review, color semantics: green is reserved for
   the dewpoint trace — moist adiabats render steel-blue (TT pseudoadiabat convention),
   mixing-ratio lines faint dotted teal, dry adiabats warm tan. Density (v4 review):
   mixing-ratio lines draw only below 440 hPa (TT convention), and dark-theme background
   opacity sits a step lower than a light theme would use (dry .5 / moist .55 / mixr .7)
   so the same line count doesn't glow — reference values live in the prototype template.
4. **Nearest-gridpoint vs interpolation** at pick time. v1: nearest (12 km cell, honest
   and cheap; the response reports the snap distance). Open; revisit only if users notice.
5. **SB parcel origin definition (Phase 4 blocker), found by the v2 prototype
   (2026-07-30).** With real anchoring, ML flavors and PWAT match Tropical Tidbits within
   a few percent (MLCAPE 83 vs 93, MLCIN −101 vs −106, PWAT 44.0 vs 43.4 mm) — but SBCAPE
   is 367 vs TT's 966 on identical data. Diagnosed as *surface-parcel definition
   sensitivity*, not a column error: this hot/dry column (LCL 721 hPa) gives SBCAPE
   367→758→1276 for 2m Td of 17→18→19 °C, and TT's displayed 65 °F surface Td reproduces
   ~926. We use HRRR's native `DPT:2m` grid value; TT evidently uses something else
   (interpolation, lowest-model-level parcel, or the model's own SBCAPE field).
   Virtual-temperature correction adds ~120 J/kg more. Sensitivity table recorded in the
   spike's `indices_v2.json` diagnostics.
   **RESOLVED (Brian, 2026-07-31): primary SB parcel = HRRR native 2 m T/Td with the
   standard virtual-temperature correction, labeled explicitly in the UI. Scientifically
   defensible and reproducible beats reverse-engineering TT's undocumented convention;
   TT is a visual reference, not a numerical authority — parity tolerance is set on ML
   quantities (already within a few %), not SB. HRRR's native SBCAPE ships as a separate
   diagnostic readout (stack format_version 2 adds CAPE:surface as a 6th surface plane;
   older v1 stacks simply lack the row), never as a reason to bend the parcel
   definition.**
6. **Phase 6 multi-model scope — RESOLVED (Brian, 2026-08-01):** expand to GFS and
   ECMWF at **0.5° stack resolution**, **full horizon** (GFS 384 h, ECMWF 360 h), and
   **ship the ECMWF sounding despite its coarse (~10–13 level) ladder**. Details §10.
7. **Td derivation amendment (Phase 6, 2026-08-01):** the §1 "never derive Td" rule
   was written for HRRR, which publishes isobaric DPT. GFS/ECMWF do not, so derivation
   is unavoidable there — but it MUST come from **specific humidity (q)** via vapor
   pressure (ice/liquid-unambiguous), never from RH (the measured ~11 °C ice-saturation
   trap that created the rule). Rule restated: *use native DPT when published; else
   derive from q; RH-derivation remains forbidden.*

## 9. Spike verification record (2026-07-30)

- OKC profile (35.477N, −97.506W, row 417/col 899 of full grid): Td ≤ T at all 37 levels
  (min depression 3.90 K), θ monotonic 310.2→386.4 K, coldest −73.03 °C at 100 hPa,
  tropopause flattening 125→100 hPa, zero NaNs. MetPy 1.7.1: SBCAPE 279 J/kg (isobaric
  origin — see §2), CIN −100, PWAT 48.3 mm.
- Renderer checks, measured not eyeballed: constant-T profile plots at exactly 45.000°;
  dry adiabats match Poisson `T = 303.15·(p/1000)^0.2854` within 0.07 °C; moist adiabats
  shallower than dry at warm temps (screen dx/dy −0.967 dry vs +0.079 moist over
  1000→700 hPa) converging aloft (−0.431 vs −0.445 over 400→200); barb sweep
  3/5/10/25/50/75/125 kt renders correct pennant/barb counts and reproduces bearings to
  0.1°; 64 paths / 92 lines / 26 barbs emitted with zero NaN/Infinity attributes.
- Visual cross-checks: same-run/fh/point comparison against Tropical Tidbits (trace
  shapes and capping structure match; deltas explained by §2 surface anchoring and §6 Td
  policy) and layout comparison against Pivotal/SHARPpy.

## 10. Phase 6 — multi-model expansion: GFS + ECMWF (designed 2026-08-01, NOT started)

Decisions locked (§8 #6–#7): 0.5° stacks, full horizon (GFS 384 h / ECMWF 360 h), ship
ECMWF despite its coarse ladder, Td-from-q where isobaric DPT is absent. No code exists
yet; numbers below are derived estimates pending a Phase-6 inventory spike.

### What the existing architecture already covers

The stack format, sidecar-driven reader, endpoint, thermo pool, cache, and chart are
model-agnostic: ladders, grid geometry, and projections ride in each sidecar (the
EPSG:4326 short-circuit in the coordinate path is already tested), the flag accepts
model lists, and the frontend tolerates absent omega/profiles. What is HRRR-specific
today: the fetch spec + `SUPPORTED_MODELS` in `sounding.py`, and the client's hardcoded
`SOUNDING_MODELS = ["hrrr"]`.

### Per-model data plan

| | GFS | ECMWF (open data) |
|---|---|---|
| Isobaric Td | NOT published → derive from SPFH (q) per §8 #7 | NOT published → derive from Q |
| Ladder | ~37 levels 1000→100 expected — confirm via inventory | ~10–13 levels — coarse; ship anyway (decision #6) |
| VVEL / omega strip | available → strip ships | not in open data → strip absent (client already tolerates) |
| Surface block | PRES:sfc, 2m T/DPT, 10m U/V + CAPE:surface | 2t/2d/10u/10v/sp; native CAPE availability unconfirmed — diagnostic row only if present |
| Grid → stack | 0.25° source decimated ×2 → 720×361 (0.5°, ~55 km pick) | same |
| Fetch path | AWS BDP idx subsets (HRRR pattern) | ECMWF open-data index protocol — borrow the repo's existing ECMWF fetch machinery |

### Sizing at the locked decisions (derived, pending inventory)

| | per fh | per run | per day | retention steady state |
|---|---|---|---|---|
| GFS (~191 planes, 382 B/px) | ~99 MB | ~13.4 GB @ ~135 fhs | ~54 GB (4 runs) | **~94 GB @ 7 runs** |
| ECMWF (~58 planes, 116 B/px) | ~30 MB | ~2.6 GB full / ~1.5 GB short | ~8 GB (2+2) | ~12 GB @ 6-run mix |

GFS dominates and is the number to sanity-check against disk headroom at build time;
the 1° fallback divides it by 4 if it ever matters. Download: GFS ~100–190 MB/fh ≈
14–26 GB / 30–55 min per run at the measured 8 MB/s — which makes **threading the
scheduler's synchronous sounding pass a prerequisite**, not a watch item, before GFS
joins.

### Work items (rough order)

1. Thread the scheduler sounding pass (prerequisite; currently serial in the catch-up
   loop, fine for HRRR's ~15 s/fh, not for a 65–135-frame global model).
2. Generalize `sounding.py`: per-model spec table (products, fields, ladder, derivation
   policy, decimation stride, surface set); expand `SUPPORTED_MODELS`.
3. Td-from-q derivation in the stack builder (build-time, MetPy, per §8 #7) + parity
   tests mirroring the HRRR native-DPT tests.
4. ECMWF fetch adapter reusing the repo's existing ECMWF machinery.
5. Endpoint: antimeridian/longitude-wrap handling for global grids in
   `locate_grid_point` (coordinate with the Global 4326 Phase 3 antimeridian work).
6. Response/pool scaling: ~135-frame GFS responses put the warm pooled thermo at
   ~1.7 s (est) — likely raise workers on prod and/or measure before gating.
7. Frontend: capabilities-driven sounding-model list (replace the hardcoded array);
   coarse-ladder labeling for ECMWF indices; fh-cadence-aware scrubber labels.
8. Per-model gates: inventory spike first (exact ladders, ECMWF CAPE, GFS published-fh
   cadence), then the usual parity + visual gates per model.

### Open questions for the Phase-6 inventory spike

- Exact GFS/ECMWF isobaric ladders and CartoSky's published GFS fh cadence (the ~135
  figure is inherited from the raster pipeline, unverified for soundings).
- ECMWF open-data CAPE availability (diagnostic row) and index-subset ergonomics.
- Prod thermo-pool sizing for 135-frame responses (measure, don't assume).
