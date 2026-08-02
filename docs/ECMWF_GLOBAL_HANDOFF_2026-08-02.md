# ECMWF Global Support — Agent Handoff Brief (2026-08-02)

Fourth and final model in the locked order GFS → AIGFS → AIFS → **ECMWF**.
ECMWF is deliberately last: cycle-length asymmetry and the heaviest build.
Prerequisite: AIFS global shipped.

**Read first:** `docs/AIGFS_GLOBAL_HANDOFF_2026-08-02.md` (pattern,
gotchas, process — apply verbatim) and
`docs/AIFS_GLOBAL_HANDOFF_2026-08-02.md` (the Change-A decoupling framing
and the re-derive-tolerances rule — both apply identically to ECMWF, whose
canonical is also 9 km and also stays untouched). Then the normative docs.
This brief covers only what is ECMWF-specific.

## ECMWF-specific work and landmines

1. **Cycle asymmetry is THE gate here.** ECMWF's cycle types differ in
   horizon (long vs short cycles). The checklist line "G6 both cycle
   types tested" is routine for other models and critical for this one:
   expected-fh manifests, timeline hatching, retention, and the readiness
   boundary must be correct PER CYCLE TYPE, derived from ECMWF's actual
   schedule. A manifest built from the long cycle's expectations will
   permanently show the short cycle as incomplete.
2. **Known catalog landmine — resolve BEFORE declaring build regions:**
   `ecmwf precip_16d_anom` is *packed but uncataloged* (serving-canary
   finding, 2026-07). Any sweep that derives global declarations from the
   catalog will silently skip it, and any sweep that derives from packed
   artifacts will crash on its missing catalog entry. Reconcile that
   inconsistency first, as its own small verified change.
3. **Model-id plumbing has history.** The 2026-07 18z outage was caused by
   an internal id (`eps`) leaking to Herbie instead of the request model
   (`ifs`). ECMWF-family ids (ecmwf / ifs / eps) cross several boundaries —
   when threading the global domain through fetch/build/serve, verify the
   id at each boundary rather than assuming; the readiness probe is now
   fail-closed and will bite loudly if this regresses.
4. **Heaviest build — measure, schedule, and gate.** Even with the
   native-4326 roll replacing the warp, ECMWF has the longest cycles and
   the largest variable surface of the four. Before the flag flip:
   same-session A/B of full-cycle build wall-clock and RSS vs the
   pre-global baseline (G5, against the Phase-0 memory baselines), and
   confirm no live-service interference during the burst (the sizing-spike
   incident is the cautionary tale). If build time materially stretches
   the publish window, that is an operator decision, not a silent
   acceptance.
5. **Anomalies**: same shared-baseline rule as AIFS — instantaneous
   anomalies get the global ERA5 baselines free (add hints + allowlist per
   the GFS pattern, including the °C ladder behavior for tmp850_anom which
   is derive-time and model-independent); precip anomalies follow the
   Wave-2 flip state (the 15-day ECMWF window exists in the global precip
   baselines).
6. **Ensembles (EPS) remain out of scope** — deterministic ECMWF only;
   member/mean global is a separate deferred decision.

## Definition of done

Dark builds on the contract grid for BOTH cycle types; the full per-model
checklist on prod including the cycle-asymmetry arms; disk checkpoint
against a freshly computed projection (old sizing rows are void); operator
flips `ecmwf` into the flag on both units; Coverage toggle appears;
canonical ECMWF provably untouched. This completes the four-model global
rollout — after this, the remaining global roadmap items are the LOD chain
→ default-to-global flip and any ensemble-global decision, both separately
briefed.
