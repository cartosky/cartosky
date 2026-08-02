# AIFS Global Support — Agent Handoff Brief (2026-08-02)

Third model in the locked rollout order GFS → AIGFS → **AIFS** → ECMWF.
Prerequisite: AIGFS global is shipped and its checklist learnings folded
back into the pattern. Do not start AIFS before that.

**Read first:** `docs/AIGFS_GLOBAL_HANDOFF_2026-08-02.md` — its sections
"What already exists (do not rebuild)", "Gotchas that actually bit us",
and "Process requirements" apply to this effort VERBATIM and are not
repeated here. Then the normative docs it lists (contract, plan Phase 3,
G1 investigation). This brief covers only what is AIFS-specific.

## The one framing rule that outranks everything

**Change A (2026-07-29) decoupled AIFS's global publish from its canonical
grid migration.** The original plan tied "AIFS goes global" to "AIFS
canonical migrates 9 km → 25 km" as one cutover. That coupling is
RETRACTED. Your scope is the global domain ONLY:

- AIFS canonical stays 9 km EPSG:3857, byte-identical. Zero canonical
  changes, zero canonical re-publish.
- The canonical 9 km → 25 km question is a separate future decision with
  its own A/B gate (visual, contour, sampling, playback) if ever taken.
  If you find yourself touching canonical grid params, stop — you have
  left your scope.
- Note the plan's sizing rows for AIFS/ECMWF are VOID (they assumed the
  coupling, including a negative net from the migration). Recompute disk
  from AIFS's actual variable set before the first prod build.

## AIFS-specific work and landmines

1. **Source grid verification comes first.** The open AIFS source is
   delivered at 0.25° — verify the GRIBs are 0–360, grid-registered
   compatibly with the contract (the identity-roll fast path requires
   exact alignment; the reproject fallback is correct but forfeits the
   exactness pins). AIFS is an AI model — confirm ALL its variables share
   one source grid; any exceptions go through the fallback with honest
   tests.
2. **Parity tolerances must be RE-DERIVED, not copied.** Every existing
   G1-oracle and hover-parity tolerance was derived for GFS's 25 km
   canonical vs 0.25° global comparison. AIFS canonical is 9 km — the
   resampling error term is entirely different (9 km canonical bilinear
   from a 0.25° source vs exact 0.25° global). Measure the noise floor
   fresh; copied thresholds would be either vacuous or flaky.
3. **Catalog scope**: determine which AIFS variables declare global
   (mirror the non-anomaly rule), which composites/companions exist, and
   whether AIFS carries the instantaneous anomaly variables. If yes, the
   shared global ERA5 baselines serve them free (no model segment in the
   baseline path) — add the `global=global` hints + allowlist entries per
   the GFS pattern. Precip anomalies follow the Wave-2 capability-flip
   state at the time you ship (the 15-day window — the AIFS/ECMWF window —
   is included in the global precip baseline build).
4. **Cycle/horizon truth**: AIFS's cycles and forecast horizons are NOT
   GFS's. The per-model checklist line "manifest correct against the
   model's ACTUAL global availability, not assumed from NA" exists for
   exactly this. Derive expected fhs from the model's own schedule.
5. **Build-load measurement**: the old fear of "multi-hour 9 km global
   bursts" is mostly retired by native-4326 (a longitude roll, not a
   warp), but AIFS shares scheduler infrastructure — measure build-time
   and RSS deltas (G5) on the first dark builds, same-session A/B.
6. **Ensembles are out of scope.** EPS/ensemble-member global remains
   deferred (member-pipeline decision). AIFS deterministic only.

## Definition of done

Identical in shape to AIGFS's: dark builds on the contract grid, full
per-model checklist (G1–G6 + isolation + disk + mobile) passing on prod,
operator flips the flag (adds `aifs` to `CARTOSKY_GLOBAL_DOMAIN_MODELS`
on both units), Coverage toggle appears with correct data, canonical AIFS
provably untouched throughout.
