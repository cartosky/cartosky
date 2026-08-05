# WP5 — Fast-Path Dev Observation Runbook

Phase 6's final gate (design §7 step 1): watch ≥3 live cycles build per-frame
on the dev environment, answer the two open UX questions, and run one
failover drill. Everything here is dark: a dedicated data root, a second dev
API port, prod untouched.

## Topology

- Fast scheduler: runs from `/opt/cartosky-dev`, **own data root**
  `/opt/cartosky-dev/data-fastpath` (never the prod root — the prod API and
  the prod ECMWF scheduler must not see fast frames). The per-model flock is
  keyed on (data_root, model), so it cannot collide with the prod scheduler.
- `CARTOSKY_SCHEDULER_VARS` restricted to the six fast variables →
  the delayed loop's work list is empty in normal operation (all six are
  fast-owned), so dev adds **no** heavyweight delayed ECMWF build. During the
  failover drill the delayed loop builds exactly the revoked six — bounded.
- Observation viewer: a **second** dev API instance on port **8202** pointed
  at the fastpath root (leave the normal 8201 instance alone), proxied to the
  Mac's vite via ssh tunnel.
- Seed: hardlink-copy the latest complete prod ECMWF run into the fastpath
  root first, so the viewer has a full 24-variable previous run — that is
  what makes the "what does a delayed-variable user see during the window?"
  question answerable.

## One-time setup (dev box)

```bash
# 0. code
cd /opt/cartosky-dev && sudo -u cartosky git pull   # needs the Mac's `git push` first
sudo -u cartosky /opt/cartosky-dev/.venv/bin/pip install omfiles

# 1. data root + seed newest complete prod ECMWF run (hardlinks, ~free on same fs)
PROD=/opt/cartosky/data
DEV=/opt/cartosky-dev/data-fastpath
sudo mkdir -p $DEV/{published/ecmwf/domains/global,staging/ecmwf,manifests/ecmwf/domains/global,status}
RUN=$(grep -o '"run_id"[^,]*' $PROD/published/ecmwf/LATEST.json | grep -oE '[0-9]{8}_[0-9]+z')
echo "seeding run $RUN"
# NA/canonical tree + pointer + manifest
sudo cp -al "$PROD/published/ecmwf/$RUN" "$DEV/published/ecmwf/$RUN"
sudo cp -a  "$PROD/published/ecmwf/LATEST.json" "$DEV/published/ecmwf/LATEST.json"
sudo cp -a  "$PROD/manifests/ecmwf/$RUN.json" "$DEV/manifests/ecmwf/" 2>/dev/null || true
# global-domain tree + pointer + manifest (scheduler.py:1160-1170 — domains/global is a separate root)
sudo cp -al "$PROD/published/ecmwf/domains/global/$RUN" "$DEV/published/ecmwf/domains/global/$RUN" 2>/dev/null || true
sudo cp -a  "$PROD/published/ecmwf/domains/global/LATEST.json" "$DEV/published/ecmwf/domains/global/LATEST.json" 2>/dev/null || true
sudo cp -a  "$PROD/manifests/ecmwf/domains/global/$RUN.json" "$DEV/manifests/ecmwf/domains/global/" 2>/dev/null || true
sudo chown -R cartosky:cartosky $DEV
```

## The scheduler script — `/opt/cartosky-dev/fastpath-dev.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
set -a
source /etc/cartosky/api.env
source /etc/cartosky/api-dev.env
set +a

export CARTOSKY_DATA_ROOT=/opt/cartosky-dev/data-fastpath
export CARTOSKY_FASTPATH_MODELS=ecmwf
export CARTOSKY_SCHEDULER_VARS=tmp2m,dp2m,precip_total,snowfall_total,wgst10m,wspd10m
export CARTOSKY_SCHEDULER_KEEP_RUNS=2
# mirror the prod scheduler's global-domain setting (check with:
#   sudo grep GLOBAL_DOMAIN /etc/cartosky/scheduler-ecmwf.env)
export CARTOSKY_GLOBAL_DOMAIN_MODELS=ecmwf
export GDAL_CACHEMAX=256

cd /opt/cartosky-dev/backend
source /opt/cartosky-dev/.venv/bin/activate
exec nice -n 10 python -m app.services.scheduler --model ecmwf
```

Run it in tmux so it survives the ssh session:

```bash
sudo chmod +x /opt/cartosky-dev/fastpath-dev.sh
sudo -u cartosky tmux new -s fastpath -d '/opt/cartosky-dev/fastpath-dev.sh 2>&1 | tee -a /opt/cartosky-dev/fastpath-dev.log'
tail -f /opt/cartosky-dev/fastpath-dev.log
```

## The observation API — port 8202

`/opt/cartosky-dev/dev-start-fastpath.sh`: copy of `dev-start.sh` with two
lines changed —

```
export CARTOSKY_DATA_ROOT=/opt/cartosky-dev/data-fastpath
... --port 8202 (drop --reload; this instance is read-only observation)
```

Run it in its own tmux (`tmux new -s fastpath-api -d ...`).

## Viewer (on the Mac)

```bash
ssh -N -L 8202:127.0.0.1:8202 brian@cartosky-server &
cd ~/cartosky && CARTOSKY_DEV_PROXY_TARGET=http://127.0.0.1:8202 npm run dev --prefix frontend
```

Open `http://localhost:5173/viewer?m=ecmwf&v=tmp2m&domain=na`.

## Timing

Fast frames start ~T+5h30m from cycle time and complete by ~T+6h30m:
00z→05:30Z, 06z→11:35Z, 12z→17:30Z, 18z→23:30Z. The scheduler polls slowly
outside those windows; nothing needs babysitting.

## Observation checklist (≥3 cycles)

1. Frames appear per-frame in the viewer during dissemination; "Building
   X/N hrs" advances; N matches the cycle horizon (360 for 00z/12z, 144
   for 06z/18z); LATEST flips to the new run once promotion criteria pass.
2. **UX question #1 (the decision-relevant one):** with the new run promoted
   and only the fast six ready, select `tmp850` / `snowfall_kuchera_total` /
   `ptype_intensity`. Record exactly what renders: previous (seeded) run
   silently, a Building state, or anything broken. Screenshot.
3. **UX question #2:** does any overlay that reads the `msl` component
   (MSLP contours, H/L pressure centers) appear on fast-built maps? If
   missing and it matters visually, add `msl` to the fast fetch set.
4. Values sanity: hover readouts vs prod's viewer for the same run/hour once
   the delayed prod build catches up (~2h later).
5. `status/fastpath/ecmwf.json` appears; canary runs once the reference is
   available; `fastpath_blocked_pairs` stays 0; scheduler log shows no
   `fastpath_stalled`/`fastpath_blocked`/ERROR lines.
6. Disk + RSS: watch `du -sh /opt/cartosky-dev/data-fastpath` (expect a few
   GB per retained run) and the scheduler's RSS (expect < 1 GiB).

## Failover drill (once, after a clean cycle)

Simulate a bucket stall mid-run and watch the handover:

```bash
# on the dev box, DURING a fast window after some frames have built:
echo '127.0.0.1 openmeteo.s3.amazonaws.com' | sudo tee -a /etc/hosts
```

Expect: fast LISTs fail → stall_count climbs → once the delayed source for
that run is probe-verified, revocation (generation bump) → the delayed loop
builds the six revoked pairs from GRIB (bounded; this is the one time dev
does a real delayed build) → accumulation frames quarantined + rebuilt whole
→ `source_ranges` in the run manifest shows the seam, per-frame provenance
flips to herbie. Then:

```bash
sudo sed -i '/openmeteo.s3.amazonaws.com/d' /etc/hosts
```

and confirm the *next* run goes back to fast cleanly (fresh generation).

## Teardown

```bash
sudo -u cartosky tmux kill-session -t fastpath
sudo -u cartosky tmux kill-session -t fastpath-api
# keep or delete /opt/cartosky-dev/data-fastpath as desired
```

## Notes

- The prod flip later needs `omfiles` in the **prod** scheduler venv and
  `CARTOSKY_FASTPATH_MODELS=ecmwf` in `/etc/cartosky/scheduler-ecmwf.env` —
  not part of WP5.
- If anything looks wrong, kill the tmux session; nothing here can touch
  prod data or prod services.
