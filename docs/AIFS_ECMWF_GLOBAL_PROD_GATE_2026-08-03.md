# AIFS + ECMWF Global — Operator Prod Gate (2026-08-03)

Dark implementations are committed and adversarially verified:

- `9a76a1c3` — ecmwf `precip_16d_anom` packed-but-uncataloged reconciliation
  (ECMWF handoff landmine #2, pre-work)
- `d9683233` — AIFS global declarations (dark) + mirrored suites
- `de61769d` — ECMWF global declarations (dark) + mirrored suites

Nothing is live. Everything below is operator work (Mac → push → prod pull;
never edit on the server; agents never execute on prod). Rollout order per
the locked plan: AIFS first, then ECMWF. AIGFS's own prod gate
(2936382a) is still pending — run it before or with AIFS's; the per-model
checklists are independent, but the flag-flip order should follow
AIGFS → AIFS → ECMWF.

Sequencing note: these dark implementations landed before AIGFS's prod gate
closed, deviating from the handoff's "do not start before AIGFS is shipped"
line. Deviation was deliberate (operator-directed); nothing here can go
live without the flag flips below.

## Shared steps (each model)

1. ⛔ **Disk checkpoint before first dark build** (old sizing rows VOID):
   - AIFS: 852 global frames/run → **≈7.5 GiB/run, ≈30 GiB at keep_runs=4**
   - ECMWF: long cycle 1949 frames ≈17.1 GiB, short 1121 ≈9.8 GiB →
     **≈54 GiB steady state at keep_runs=4** (2 long + 2 short) — heaviest
     of the four models (~2.3× AIFS)
   Confirm free disk on both units covers this plus AIGFS's ~23 GiB if its
   flip is still pending.
2. Dark prod builds: add the model to `CARTOSKY_GLOBAL_DOMAIN_MODELS` on the
   **scheduler** unit only (or use the established dark-build flow used for
   GFS/AIGFS), restart scheduler; capabilities must NOT advertise and API
   domain routes must 404 until the API unit is also flipped.
3. Per-model checklist (plan doc Phase 3, G1–G6):
   - **G1** antimeridian oracle — tolerances must be **RE-DERIVED**, not
     copied: AIFS/ECMWF canonicals are 9 km (GFS-derived thresholds are for
     25 km canonical; copied values would be vacuous or flaky). Measure the
     noise floor fresh on the first dark builds.
   - **G1b (AIFS-specific, first dark build)**: verify live AIFS GRIBs —
     ALL variables — are 0.25° 0–360 grid-registered so the identity-roll
     fast path applies (AI model; per-variable grid exceptions possible).
     The roll-branch pin exists in tests; this checks the *real source*.
   - **G2** CF HIT / no-DYNAMIC on domain paths.
   - **G3** performance vs NA baseline, same-session A/B only.
   - **G4** screenshot/share/GIF, both capture paths.
   - **G5** build wall-clock + RSS vs pre-global baseline, same-session A/B
     (ECMWF: against the Phase-0 memory baselines; confirm no live-service
     interference during the burst — sizing-spike cautionary tale. If build
     time materially stretches the publish window, that is an explicit
     operator decision).
   - **G6** both cycle types + anomaly capabilities matching Phase 3A wave
     state. For ECMWF this is THE gate: verify manifests/hatching/
     readiness per cycle type (00/12z = 85 fhs to 360 h; 06/18z = 49 fhs
     to 144 h). Test-side negative control exists; verify on real runs.
   - Domain isolation under load; mobile spot-check.
4. ⛔ **Flag flip** (per model, after its checklist passes): add the model id
   to `CARTOSKY_GLOBAL_DOMAIN_MODELS` in `/etc/cartosky/*.env` on **both
   units**, restart **scheduler AND api** (descriptor-flip lesson). Coverage
   toggle appears model-agnostically once capabilities advertise.
5. Post-flip: canonical model provably untouched (spot-check canonical
   frames/goldens), hover parity on the global domain (re-derived
   tolerances), anomaly variables present with correct baselines
   (instantaneous only; precip windows stay NA until Wave 2 flip).

## Model-specific notes

**AIFS**
- 14 declaring vars: tmp2m, dp2m, rh2m, rh700, tmp850, wspd850, wspd300,
  precip_total, pwat, snowfall_total, wspd10m + tmp2m/tmp850/hgt500_anom.
  Precip anomalies are 15-day family (5/7/10/15), all canonical-only.
- Anomaly hints added AIFS-locally (ECMWF spec objects verified unmutated).
- Single product `oper`, 0–360/6 h all cycles — no cycle asymmetry.
- `rh` colormap has no `range_by_region["global"]` — deliberate: RH is
  physically 0–100 %, NA envelope covers the globe. If global builds ever
  warn on rh, revisit.

**ECMWF**
- 23 declaring vars incl. ptype_intensity + its 3 components (inherit by
  copy), mucape, wgst10m, snowfall_kuchera_total, ice_total.
- Herbie id pinned: `herbie_request().model == "ifs"`; `ecmwf` remains
  internal-only at the fetch boundary (18z-outage guard).
- Colormap global envelopes: all present except rh (deliberate), the
  anomaly ramps, and the ptype family — those gaps are shared verbatim
  with live GFS global. **Prod observation item**: watch for warn-only
  gate trips on ptype/anomaly ramps on tropical/polar frames.
- `precip_16d_anom` is an input alias to `precip_15d_anom` only; packing
  entry removed (9a76a1c3). Catalog↔packing now pinned equal for all six
  models.

## Known residue (accepted, documented)

- `current_analysis|mslp` is a pre-existing packed-but-uncataloged stray
  (outside the six models the scope helper covers). Out of scope here.
- RegionSpec `bbox_wgs84` for `global` is decorative for grid derivation
  (grid comes from shared `REGION_BBOX_4326`); true for all four models by
  mirrored design.
- Two of the ECMWF per-cycle tests recompute expectations from the plugin
  (circular); the absolute pins (85/49, 1949/1121, negative control) carry
  the real load.
- Deterministic pre-existing full-suite failures on this Mac: 2
  (`scripts/test_pipeline.py::test_phase2_value_grid_semantics`,
  `test_hrrr_invariants` snapshot). twf_error_guards ×2, aigfs-aws, goes,
  sounding-multimodel, sounding-pool failures observed in some runs are
  order/environment flakes, not deterministic.

## After both flips

Four-model global rollout complete. Remaining global roadmap: LOD chain →
default-to-global flip; ensemble-global decision. Both separately briefed.
