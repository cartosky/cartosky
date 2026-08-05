# ECMWF 9 km Fast-Path Prototype — Findings & Decisions (2026-08-04)

Consolidated record of the Open-Meteo S3 ingestion prototype: the original
handoff's assumptions verified against this codebase, all empirical findings,
decisions made, and what remains. Supersedes the chat-only handoff doc.
Detail lives beside the code: `scripts/spikes/openmeteo_9km/README.md`
(phase scripts + numbers) and `scripts/openmeteo_monitor/README.md` (prod
monitor/archive). Commits: `fde9268b` (Phase 1), `be8f8222` (monitor),
`b3d0f888` (Phase 2+3 core), `4aa70efb` (Phase 3), `18931000` (Phase 4).

## 1. Mission recap

Ingest ECMWF IFS HRES surface data at native 9 km (O1280) from Open-Meteo's
public AWS bucket (`s3://openmeteo`, CC-BY-4.0 ECMWF open data) incrementally
during dissemination — serving Euro surface/snow products ~2 h earlier than
the delayed 0.25° path, at real 9 km NA resolution.

## 2. Decisions made

| Decision | Resolution |
|---|---|
| NA vs global resolution | **NA: native 9 km** onto the existing 9 km EPSG:3857 grid (zero grid/renderer change — today's grid is bilinear-upscaled 0.25°, so the fast path only upgrades the information content). **Global: stays 0.25°** EPSG:4326. |
| Global source | Can come from O1280 downsampled (validated, MAE 0.020 °C vs ECMWF's own 0.25°) or stay on the delayed path — ship decision open, bandwidth ≈ 4.8 MB/var/step for full-globe reads. |
| Resampling | **Bilinear**, both directions. Box-mean loses to bilinear against ECMWF's own 0.25° product (which is itself interpolated) and strands 63k empty polar cells. |
| Ingestion architecture | **Model-agnostic source** (Brian, 2026-08-04): same bucket layout/manifest/`.om` format serves ICON, GEM, UKMO, HRRR-15min, etc. not available via Herbie. Parameterize model dir, variables, grid decode, per-cycle cadence/horizon. ECMWF is the only model in scope this season. |
| Kuchera | Stays on the delayed path for now (see §5). AIFS-profile hybrid is the free parity candidate, being validated via the prod archive through fall. GPLv2 `omfiles` reader stays in an isolated module + dedicated venv. |

## 3. Handoff §3 assumptions — verified/corrected

| # | Handoff assumed | Reality |
|---|---|---|
| 2 | Pipeline waits for the full run upstream | **Already per-frame incremental** (frames build as Herbie indices appear; manifest exposes `expected_frames`/`available_frames`/`ready_through_fh`, `scheduler.py:2045`). The ~2 h win is purely upstream availability. The *delayed* 0.25° source itself publishes all-at-once (every FH same `Last-Modified`). |
| 3 | NA grid is "9 km-equivalent upscaled" | Confirmed: TAP-snapped 9 km EPSG:3857, 1825×1893 for `na` (`raster_grid.py`), bilinear rasterio warp from 0.25°. Global: native 0.25° point-registered 4326, 721×1440, row 0 north. |
| 4 | Per-frame readiness contract plausibly exists | Exists exactly as hoped ("Building X/N hrs" reads the manifest fields above). |
| 5 | Six-run retention | **Four** by default (`DEFAULT_KEEP_RUNS`, `scheduler.py:77`), 6 for auto-detected categories, env-overridable. |
| 6 | Info card resolution label = small change | True, but **neither ECMWF/Open-Meteo attribution nor any resolution label exists today** — both are launch gates (CC-BY-4.0). |

## 4. Empirical findings (all measured 2026-08-04, live bucket)

### Timing (7+ consecutive cycles, now logged continuously on prod)

| Source | Start lag after cycle time | Upload span |
|---|---|---|
| `ecmwf_ifs` (9 km surface) | ~5.5 h | 26–55 min (dissemination relay) |
| `ecmwf_aifs025_single` (full pressure levels) | ~5.6 h | **2–6 min** — complete before the 9 km stream ends |
| `ecmwf_ifs025` (0.25°, full suite) | ~7.0–7.6 h | 4–18 min — a batch relay **1–5 min behind ECMWF's own portal** |

### Phase verdicts

- **Phase 1 (bandwidth): GO.** NA-band (4.5–82.5°N) read of 10 winter vars =
  16.85 MB vs the 111 MB file; ≈8.5 GB/day upper bound, ~2.4× cut available
  via lon subsetting. Naive `omfiles` = 1 HTTP request per 1024-pt chunk
  (231 s/var); a 512 KB block-cache layer gets identical bytes in 3.8 s —
  mandatory. Metadata open ≈ 5 KB.
- **Phase 2 (grid correctness): PASSED.** One reduced-Gaussian bilinear
  sampler (exact Legendre-root latitudes) serves both targets. NA: 0.5 s
  one-time build, 0.06 s/field apply; flat-site point-API oracle ≤ 0.4 °C.
  Global: matches `ifs025` to **MAE 0.020 °C** — validates decode,
  latitudes, orientation, and antimeridian wrap in one number.
- **Phase 3 (accumulation semantics): PASSED.** `.om` accumulation fields are
  **per-step de-accumulated, step length = the product's cadence at that fh**
  (9 km: hourly→FH90, 3 h→144, 6 h→360) — NOT run-start-cumulative GRIB `tp`.
  Proven for precip + snowfall (hourly-sum matches ifs025; 3 h/6 h regimes
  match directly, bias ≤ 0.0012 mm) and showers (cp ≤ tp bound). Builder
  derives need a per-source step ladder + step-sum accumulation.
- **Phase 4 (cross-source reconcile): PASSED.** Fast vs legacy on the exact
  production NA grid: bias ≈ 0, synoptic (1°) MAE 0.02–0.10, corr ≥ 0.97,
  genuine fine-scale deltas (t2m p99 1.9 °C at terrain/coasts = the added
  9 km information). Side-by-side PNGs committed in the spike dir.

### Upstream gotchas (do not rediscover)

1. **Grid orientation differs per product** in the same bucket: 9 km O1280
   files are north-first (lon 0→360°E); 0.25° `ifs025`/`aifs025` are S→N.
   Detect per run (±45° land/ocean variance — ±23° is unsafe), never assume.
2. **Units differ per product for the same variable name**: 9 km
   `pressure_msl` is Pa, ifs025's is hPa. Per-product units audit is
   mandatory before wiring any variable.
3. Per-chunk HTTP reads (see Phase 1) — block-cache/coalesce always.
4. FH000 files lack accumulation/gust fields; temps are °C not K; 9 km arrays
   are shape `(1, 6599680)` (dispatch on length, not ndim); object existence
   is truth, `latest.json`/`completed` lag reality.
5. NA fetch band must be **4.5–82.5°N** — the NA grid's edge cell centers sit
   at 4.967°N and 82.006°N, outside the naive 5–82 box.

## 5. Kuchera: the winter question

Kuchera needs 925/850/700/600 hPa temps (`ecmwf.py:469`); the 9 km set is
surface-only (43 vars, zero pressure levels), and no free source publishes
IFS pressure levels before ECMWF's ~2 h-delayed portal (`ifs025` relays
*behind* it). But Kuchera never needed the full run — it builds per-frame —
and its profile source is hint-swappable (`kuchera_profile_product`).

Options, in order:
1. **AIFS-profile hybrid (free, at parity):** AIFS carries all four levels and
   completes before the 9 km stream ends. First measured agreement is strong —
   max-column-T MAE ≈ 0.4 K, SLR-ratio MAE ≈ 0.4 (scale 5–30), |ΔSLR| > 4 on
   ~0% of snow-plausible area — but that is one summer cycle. The prod
   archive (below) accumulates the multi-cycle + warm-nose case library
   through fall; NA shadow comparison in Oct–Nov is the gate. Caveats: AIFS
   is 6-hourly (needs 6→3 h profile interpolation, testable against IFS's own
   3-hourly truth), and the product must not be labeled identically to true
   ECMWF Kuchera.
2. **Paid ECMWF real-time feed:** true IFS profiles at dissemination time,
   ~4 fields × ~85 steps ≈ 2–3 GB/day — price via the Product Requirements
   Catalogue (Band-2 ≈ €1,500/yr is the ceiling). The only path to *true*
   parity (what WeatherBell/Pivotal pay for).
3. **Interim ship:** 10:1 `snowfall_total` needs only `sf` → full ~2 h-early
   snow map on the fast path; Kuchera follows ~2 h later as the refined product.

## 6. Prod monitor/archive (live since 2026-08-04)

`csky-openmeteo-monitor.timer`, hourly at :20 (`/opt/cartosky/.venv-ommonitor`,
data in `/var/lib/cartosky/openmeteo-monitor`): logs per-run upload timing for
any configured bucket model (`timing.jsonl`) and archives AIFS + IFS 0.25°
profile temps + IFS snowfall over the NA window for 00z/12z runs (~76 MB/day,
int16 °C×100) before the bucket's ~7-day purge. Acceptance tests for the AIFS
hybrid (per GPT review, adopted): multi-cycle maxT/SLR diffs, warm-nose and
transition cases specifically, 6→3 h interpolation cost measured separately
against IFS 3-hourly truth, final snowfall deltas at 1"/3"/6" thresholds on
run totals, and AIFS-arrival reliability distribution.

### Phase 5 — visual gate: APPROVED (Brian, 2026-08-04)

tmp2m fh12 from the fast path was rendered end-to-end through the real
pipeline (real writers via `phase5_inject.py`, real packing verified to
half-quantum, real legend/viewer) and judged side-by-side against the prod
0.25°-upscaled product and Pivotal's licensed render of the same run/hour.
Same synoptic pattern; visibly more terrain detail at 9 km. **Brian approved.**
All six prototype validation phases that gate correctness (1–5) are now
closed; the handoff's acceptance gates 1–3 are met.

## 7. Remaining work

1. **Phase 6 — dev-flagged incremental loop:** poller (manifest hint,
   object-HEAD truth) → per-frame build → existing manifest/readiness;
   fallback drill (bucket stall → delayed path picks up, silent to users,
   loud to ops).
2. **Integration risks to design around:** memory headroom (prod already
   swaps during ECMWF builds — fast path must *replace* the delayed build
   for surface vars, not duplicate it), run-identity reconciliation between
   the two sources, per-source step ladder in the cumulative derives.
3. **Launch gates:** attribution line ("ECMWF IFS data © ECMWF, via
   Open-Meteo (CC-BY-4.0)"), per-variable source-resolution labels, ops
   metrics (ingestion lag vs dissemination, per-run object counts, alerts).
4. **Fall:** AIFS-vs-IFS Kuchera shadow analysis from the archive; paid-feed
   pricing exercise in parallel; Kuchera go/no-go before winter.
