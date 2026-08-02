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
yet.

**Inventory spike run 2026-08-02** (measurement only, no code changes). Numbers below are
now MEASURED unless marked DERIVED. Sources: GFS `noaa-gfs-bdp-pds` 2026-08-02 06z idx at
f000/f120/f123/f240/f384 (pgrb2 + pgrb2b); ECMWF open data `ecmwf-forecasts`
(eu-central-1) 2026-08-02 00z/06z `.index` at f0/f120/f240/f360; prod manifests and prod
`sounding_thermo` on the deployed venv. Three spike findings change the plan — see
"Spike deltas" below.

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
| Isobaric Td | NOT published (MEASURED: zero `DPT:* mb` in pgrb2/pgrb2b) → derive from SPFH (q) per §8 #7 | NOT published (MEASURED: no `d`-as-dewpoint on `pl`) → derive from `q` |
| Ladder | **21 levels 1000→100** (MEASURED, pgrb2): 1000/975/950/925/900 then 50-hPa 900→100. Identical at f000/f120/f123/f240/f384. | **12 levels 1000→100** (MEASURED): 1000/925/850/700/600/500/400/300/250/200/150/100. Identical at f0/f120/f240/f360. |
| Ladder ceiling cause | pgrb2b carries the 16 intermediate 25-hPa levels for TMP/UGRD/VGRD/VVEL/RH — but **NOT SPFH** (MEASURED: pgrb2b has only 5 SPFH msgs, all `mb above ground` layers). Td-from-q therefore caps the usable ladder at 21. | Native to the open-data product; 1000→925→850→700 is a 150-hPa boundary-layer gap. |
| RH | present on all 37 levels (pgrb2 21 + pgrb2b 16) — NOT used, per §8 #7 | `r` present on all 12 — not used |
| VVEL / omega strip | available on all 21 → strip ships | **`w` IS available on all 12** (MEASURED — corrects the earlier "not in open data") → strip ships |
| Surface block | PRES:surface, TMP/DPT:2 m, UGRD/VGRD:10 m, CAPE:surface — all 6 present at every probed fh, no late-horizon dropouts (MEASURED) | `sp`, `2t`, `2d`, `10u`, `10v` all present; **`mucape` present** at every probed fh (`cape`/`cin` absent) → diagnostic row uses `mucape` (MEASURED) |
| Grid → stack | 0.25° source decimated ×2 → 720×361 (0.5°, ~55 km pick) | same |
| Fetch path | AWS BDP idx subsets (HRRR pattern) | `https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/{YYYYMMDD}/{HH}z/ifs/0p25/oper/{YYYYMMDDHHMMSS}-{fxx}h-oper-fc.grib2`, sidecar `-oper-fc.index` (JSON-lines, `_offset`/`_length`). 06z/18z are now `oper` too — `scda` is gone (404). |

### Published fh cadence (MEASURED, prod manifests 2026-08-01/02, `na` and `global` agree)

| model | cycles | fhs/run | spacing |
|---|---|---|---|
| GFS | 00/06/12/18z | **105** | 0–240 @ 3 h (81), 246–384 @ 6 h (24) |
| ECMWF full (00/12z) | 2/day | **85** | 0–144 @ 3 h (49), 150–360 @ 6 h (36) |
| ECMWF short (06/18z) | 2/day | **49** | 0–144 @ 3 h |

§10's inherited "~135" figure was wrong: **GFS publishes 105 fhs, not 135**. Retention on
prod is **6 runs on disk** for both models (MEASURED: `ls published/{gfs,ecmwf}`), i.e.
1.5 days — not the 7 assumed earlier.

### Sizing at the locked decisions (MEASURED ladders + cadences, DERIVED arithmetic)

Grid 720×361 = 259,920 px; u16 → 2 B/px/plane (same packing as §3).

| | planes | B/px | per fh | per run | per day | retention steady state |
|---|---|---|---|---|---|---|
| GFS (5 vars × 21 lev + 6 sfc) | 111 | 222 | **57.7 MB** | **6.06 GB @ 105 fhs** | 24.2 GB (4 runs) | **36.3 GB @ 6 runs** |
| ECMWF (5 vars × 12 lev + 6 sfc) | 66 | 132 | **34.3 MB** | 2.92 GB full / 1.68 GB short | 9.2 GB (2+2) | **13.8 GB @ 6-run mix** |

Combined steady state **≈50 GB** — against 1.2 TB free on prod (`/dev/vda4` 2.0 T, 744 G
used, 39%; `published/gfs` alone is 197 G today). **Disk verdict: comfortable, ~2.4× under
the old ~94 GB projection.** The 1° fallback is not needed and the 37-level ambition (if
the Td-from-q constraint is ever relaxed) would only take GFS to ~62 GB.

Download (MEASURED on the prod path, sequential paced range requests, no throttling, no 302s):

| | bytes/fh | msgs | HTTP ranges | wall | rate | per run |
|---|---|---|---|---|---|---|
| GFS pgrb2 f120 | **96.66 MB** (f240 103.9, f384 106.2) | 111 | 68 | **15.75 s** | 6.14 MB/s | ~10.4 GB, **~28 min** |
| ECMWF oper f120 | **50.04 MB** (with `w`; 35.4 MB without) | 65 | 31 | **33.56 s** | 1.49 MB/s | 4.25 GB full, **~48 min**; 2.45 GB / ~27 min short |

GFS download is ~half the earlier 14–26 GB/run estimate. ECMWF's transatlantic rate
(1.49 MB/s from eu-central-1) makes its 85-fh full run the **slower** of the two despite
being 2.4× smaller — worth a source-preference check (google/azure mirrors) at build time.
Either way **threading the scheduler's synchronous sounding pass remains a prerequisite**,
not a watch item.

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
6. Response/pool scaling: raise `MAX_WORKERS_CAP` (see the benchmark below) — the default
   4 already clears the budget, but 6–8 is nearly free on the 16-core box.
7. Frontend: capabilities-driven sounding-model list (replace the hardcoded array);
   coarse-ladder labeling for ECMWF indices; fh-cadence-aware scrubber labels.
8. Per-model gates: ~~inventory spike first~~ (done 2026-08-02), then the usual parity +
   visual gates per model.

### Open questions — ANSWERED by the 2026-08-02 inventory spike

1. **GFS isobaric ladder** — MEASURED. 21 levels 1000→100 in pgrb2 0.25°
   (1000/975/950/925/900, then 50 hPa to 100). Not a 25-hPa ladder above 900. The
   25-hPa infill lives in pgrb2b but **without SPFH**, so under §8 #7 (Td from q) the
   usable ladder is 21. Identical at f000/f120/f123/f240/f384 — no late-horizon dropouts.
2. **GFS isobaric DPT** — MEASURED absent (0 messages, both products). RH MEASURED
   present at all 37 levels; still unused per §8 #7.
3. **GFS surface set** — MEASURED: all six (PRES:surface, TMP:2 m, DPT:2 m, UGRD:10 m,
   VGRD:10 m, CAPE:surface) present at every probed fh.
4. **GFS byte cost** — MEASURED: 96.66 MB / 111 messages / 68 coalesced ranges /
   15.75 s at 6.14 MB/s for f120; 106.2 MB at f384.
5. **ECMWF ladder** — MEASURED: 12 levels 1000→100 for `t`/`q`/`u`/`v` (and `w`, `r`,
   `gh`, `z`, `d`, `vo`). Stable across f0/f120/f240/f360 and across 00z/06z.
6. **ECMWF isobaric dewpoint** — MEASURED absent; `q` present → same Td-from-q path.
7. **ECMWF VVEL** — MEASURED **present** (`w`, all 12 levels). §10's earlier "not in open
   data" was wrong; the omega strip ships for ECMWF too.
8. **ECMWF surface / CAPE** — MEASURED: `2t`/`2d`/`10u`/`10v`/`sp` all present;
   **`mucape` present** at every probed fh; no `cape`, no `cin`. Diagnostic row uses
   `mucape` (label it MU, not SB — it is not comparable to GFS `CAPE:surface`).
9. **ECMWF byte cost** — MEASURED: 50.04 MB / 65 messages / 31 ranges / 33.56 s at
   1.49 MB/s (f120, with `w`). Index ergonomics are good: one JSON-lines `.index` per fh
   with `_offset`/`_length`, 184 records, trivially filterable by `param`/`levtype`/`levelist`.
10. **Published fh cadence** — MEASURED: GFS 105, ECMWF 85 full / 49 short (table above).
11. **Prod thermo-pool sizing** — MEASURED on the deployed `/opt/cartosky/backend` +
    `/opt/cartosky/.venv` (Python 3.13.5, 16 cores), pool pre-warmed, one run per config,
    load average 6.0 → 10.0 across the sweep (builds running concurrently):

    | frames × levels | serial (workers=0) | default (4) | workers=6 | workers=8 |
    |---|---|---|---|---|
    | **GFS 105 × 21** | 6.53 s (62.2 ms/frame) | **1.78 s** (16.9) | 1.28 s (12.2) | 0.89 s (8.5) |
    | GFS 105 × 37 (reference) | 8.72 s (83.0) | 2.10 s (20.0) | 1.61 s (15.3) | 1.09 s (10.4) |
    | **ECMWF 85 × 12** | 4.18 s (49.2) | **1.13 s** (13.3) | 0.75 s (8.8) | 0.54 s (6.4) |

    `configured_workers()` honours `CARTOSKY_SOUNDING_THERMO_WORKERS` **uncapped**, so the
    env var is the lever; `MAX_WORKERS_CAP = 4` only bounds the default. All 105/85 frames
    returned non-null indices in every config.

### Spike deltas — what changes in the work order

- **GFS ladder is 21, not ~37.** Halves the GFS stack (57.7 MB/fh, not ~99) and the
  response payload. It also means the Phase-6 spec table must carry an explicit per-model
  ladder rather than assuming HRRR's 37 — and the ECMWF/GFS ladders differ from each other
  and from HRRR, so the frontend's level labelling must be sidecar-driven (it already is).
- **`SPFH` is the ladder-limiting field, not the level list.** If a 37-level GFS profile is
  ever wanted, the only routes are RH-derived Td above 900 hPa (contradicts §8 #7) or
  q-interpolation across the pgrb2b levels. Recommend staying at 21 and revisiting only if
  the coarse mid-levels visibly hurt the parcel curve.
- **ECMWF ships omega after all**, so the Phase-6 ECMWF stack gains 12 planes
  (+8.6 MB/fh, +14.6 MB/fh of download) versus the earlier no-VVEL assumption. Cheap;
  take it. If ECMWF download time becomes the scheduler's long pole, dropping `w` is the
  first, lowest-cost lever (50.0 → 35.4 MB/fh).
- **Pool item (work item 6) is nearly a no-op.** 105-frame GFS responses are 1.78 s at the
  *current* default of 4 workers, close to the 1.7 s estimate; raising the cap to 6–8 buys
  1.78 → 0.89 s. Do it, but it is not a gate.
- **Storage is a non-issue** (~50 GB combined steady state vs 1.2 TB free), so the 1°
  fallback and the retention-shortening contingency can both be dropped from the plan.
- **ECMWF `scda` is retired** — 06z/18z are `oper` at 144 h. The fetch adapter (work
  item 4) must not carry an scda branch.
