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
| **4 — Parcel & indices** | MetPy server-side: SB/ML parcel path, CAPE/CIN, LCL/LFC/EL markers, readout panel | Values match TT/SHARPpy same-data within tolerance (document chosen tolerance) |
| **5 — Overlays** | Wet-bulb, hodograph (U/V already in stack), omega strip (VVEL already in stack per decision 2026-07-30), DGZ, θe inset | Per-overlay visual gates |

Phases 1–2 are backend-only and shippable dark; Phase 3 is the first user-visible ship.

## 8. Decisions

1. **VVEL from day one — RESOLVED yes (Brian, 2026-07-30).** +~25% storage/fetch; avoids
   a later format bump and unbackfillable runs. Reflected in §1/§3/§4/§5.
2. **Retention — RESOLVED (Brian, 2026-07-30): match HRRR raster retention.** Stacks age
   out with the runs they belong to.
3. **Td clip threshold** (T < −40 °C fade vs hard clip vs draw-it-all). Cosmetic, open,
   decidable at Phase 3 review.
4. **Nearest-gridpoint vs interpolation** at pick time. v1: nearest (12 km cell, honest
   and cheap; the response reports the snap distance). Open; revisit only if users notice.

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
