# Phase 3 — GFS global prod runbook (operator)

Companion to `docs/PHASE_3_GFS_GLOBAL_PLAN_2026-07-29.md` §7. Everything here
runs on prod by Brian; agents do not execute on prod. Sequence is
**deploy dark → scheduler-only canary build → gates → API flip (visible)**.
Any gate failure: stop, unset the flag, restart the unit — canonical is
untouched throughout.

## 0. Pre-flight (Mac already done)

- Phase 3 commits present on `main` and pushed.
- Phase 0 baselines in hand for comparison: GFS scheduler cgroup
  131 MiB current / 154 MiB peak at idle (2026-07-27 table); spike reference
  peak during global conversion 1 181 MB; disk 584 GiB / 31% on vda4.

## 1. Deploy (still fully dark)

```bash
cd /opt/cartosky && sudo git pull && git log --oneline -3
```

Confirm the Phase 3 commits landed. Restart API + GFS scheduler as usual.
`CARTOSKY_GLOBAL_DOMAIN_MODELS` is unset everywhere → behavior identical;
capabilities payload byte-identical (pinned by tests).

Sanity: `curl -s localhost:8000/api/v4/capabilities | grep -c global` should
show no GFS `supported_build_regions` change.

## 2. Scheduler-only canary build (global builds, API stays dark)

Add to the GFS scheduler unit's env (drop-in or env file, matching how other
`CARTOSKY_*` vars are set for that unit):

```
CARTOSKY_GLOBAL_DOMAIN_MODELS=gfs
```

`sudo systemctl daemon-reload && sudo systemctl restart csky-gfs-scheduler`.
Do NOT set the flag on the API unit yet — global artifacts build and publish
into `published/gfs/domains/global/` but nothing serves or advertises them.

Watch one full GFS cycle (current caps, no `MemoryHigh` change — G5):

```bash
systemctl show csky-gfs-scheduler -p MemoryCurrent,MemoryPeak
journalctl -u csky-gfs-scheduler --since "-2h" | grep -iE "global|domain|sanity|warn" | tail -50
du -sh /opt/cartosky/data/published/gfs/domains/global
ls /opt/cartosky/data/published/gfs/domains/global/
cat /opt/cartosky/data/published/gfs/domains/global/LATEST.json
df -h / && du -sh /opt/cartosky/data/published
```

Record (→ paste back for the plan doc):
- [ ] Build wall-clock for the cycle (expect canonical + ~1.5 h global tail;
      frame work serialized)
- [ ] Peak RSS vs the 1 181 MB spike reference and the 3 GiB-class caps (G5)
- [ ] Canonical GFS run ids and `LATEST` unchanged mid-build (isolation)
- [ ] Retention: after a second cycle, old global runs pruned per retention,
      canonical retention untouched (domain isolation under load)
- [ ] Disk checkpoint vs the ~52% projection (55.8 GiB/run × retained runs)
- [ ] Sanity-range warnings: zero NA-range false alarms from global frames
      (global ranges active); any real warnings investigated

Expected log noise (NOT incidents — pinned by the G1 test suite):
- `PROJ: webmerc: Invalid latitude` warnings during global warps — the GFS
  source spans ±90° while Mercator clips at ±85.05°; clipped rows are nodata.
- Grid geometry: the global grid snaps outward to ±20 050 000 m (1604²), so
  the outermost column centres sit at ≈±179.888°; a lon=180 sample reads the
  edge column centre, not an exact-180 value. Within packing tolerance.

## 3. G2 — edge cache on the new URL shapes

The X-Accel serving path needs no nginx change (internal alias covers the
whole published tree). The CACHE RULES do need verification — they live in
the live `api.cartosky.com` server block and the Cloudflare dashboard, not
the repo:

- [ ] nginx: confirm the location rules that set immutable/cache headers for
      `/api/v4/grid/...` are prefix rules (`^~ /api/v4/grid/`) — they then
      cover `/api/v4/grid/domains/...` automatically. If they enumerate
      deeper paths, extend them.
- [ ] nginx: the `/contours/` (and `/vectors/`) `s-maxage=86400` rules —
      canonical shape is `/api/v4/{model}/.../contours/{key}`, the new shape
      is `/api/v4/domains/{d}/{model}/.../contours/{key}`. A regex on
      `/contours/` covers both; a prefix rule does not. Verify, extend if
      needed.
- [ ] Cloudflare cache rules: same two checks on the dashboard rules.

Then per artifact type (grid binary, contour GeoJSON) on a real global URL:
first request → record status (cold `MISS` is fine, `DYNAMIC` is a bug);
identical second request → require `CF-Cache-Status: HIT`:

```bash
curl -sI "https://api.cartosky.com/api/v4/grid/domains/global/gfs/<run>/<var>/<file>?v=..." | grep -iE "cf-cache-status|cache-control"
```

## 4. G4 — screenshot / share / export (API flip on staging of one box, or
after §5 flip)

- [ ] `?m=gfs&v=tmp2m&domain=global` screenshot via BOTH capture paths
      (live-canvas WYSIWYG + server-side Playwright): weather pixels, legend,
      attribution, correct global bounds
- [ ] GIF export (fh progression + run-over-run) on a global selection
- [ ] A canonical no-domain permalink still screenshots pixel-identical

## 5. API flip (go visible) — only after §2–§4 green

Set `CARTOSKY_GLOBAL_DOMAIN_MODELS=gfs` on the API unit, restart API
(scheduler AND API must both carry the flag — descriptor-flip lesson).
Capabilities now expose `supported_build_regions=["na","global"]`;
`?domain=global` links go live (viewer UI selector ships separately).

## 6. G3 — performance contract (operator decision gate)

Per the plan's G3 definition: named desktop + named mobile device, stated
network, warm-CF p50/p95 vs the NA LOD baseline, one small/medium/large
variable, transfer/decode/GPU-upload/first-visible-frame separated.
**Decision rule agreed before measuring; materially regressed ⇒ rollout
blocked pending your signoff.** Global LOD-0 is ≈5.1 MB vs NA ≈0.9 MB — the
LOD chain is the first lever if first-visible-frame regresses.

## 7. G1 spot-checks on real data (tests cover synthetic)

- [ ] Point-sample 179°E, 179°W, 0° and near-seam spread on a real global
      frame; values agree with source within packing tolerance (they are
      DIFFERENT locations — no cross-seam equality)
- [ ] World-copy panning across the seam: no tearing/duplication
- [ ] Contours at the seam: no globe-spanning polygons

## 8. Mobile

- [ ] Real phone: global extent usable and performant (pan/zoom/scrub)

## Rollback

Unset `CARTOSKY_GLOBAL_DOMAIN_MODELS` on both units, restart both. Canonical
serving never depended on the flag. Optionally
`rm -rf /opt/cartosky/data/published/gfs/domains/global` to reclaim disk —
retention will otherwise age it out.
