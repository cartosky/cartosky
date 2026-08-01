# Wave 1 execution sequence — from tmp2m download onward (2026-07-31)

Operator reference for the remainder of Phase 3A Wave 1. Instance-specific
companion to `docs/GLOBAL_ANOMALY_WAVE1_RUNBOOK.md` (which stays canonical for
rationale); this doc is the exact command sequence for the current setup:

- **VM:** Hetzner CPX/CX42 (`root@<VM_IP>`), 286 GB free at `/`, repo rsynced
  to `/root/cartosky`, both venvs built, CDS creds installed.
- **Mac archive:** raw ERA5 (~63 GB/field) + built baselines (17 GB) are kept
  at `~/era5-archive/`; staged GeoTIFFs are NOT archived (re-derivable).
- **Upload path:** Mac → prod (`brian@cartosky-server`). The VM never touches
  prod.
- **State at time of writing:** Wave-1 code committed+pushed, NOT deployed to
  prod. tmp2m download running on the VM in tmux (`tmux attach -t era5`);
  1991 complete, ~2.1 GB/yr packed → ~63 GB and ~7 h per field.

Checkpoints marked ⛔ are stop-points: do not continue past a failed one.

---

## Per-field sequence

Run fields strictly in order: `tmp2m` → `tmp850` → `hgt500`. Every command
for all three fields is written out below — do not substitute by hand.

Optional overlap: the NEXT field's download (step 1) may start as soon as the
CURRENT field's staging loop (step 5) has finished (its raw dirs are gone).
If overlapping, check `df -h /` daily; abort the overlap below ~60 GB free.

### Field 1 — tmp2m  (download already running)

**1. Download** (VM, in tmux — already started):
```bash
/root/.era5-prep-venv/bin/python /root/download_era5.py tmp2m
```
Done when 30 year-dirs × 12 files exist. Re-running skips finished files.

**2. Archive raw to Mac** (Mac) — MUST complete before step 4 deletes raw:
```bash
rsync -a --info=progress2 root@<VM_IP>:/data/era5-raw/single-levels/tmp2m/ ~/era5-archive/raw/single-levels/tmp2m/
```

**3. Stage 1991 + ⛔ grid assertion** (VM):
```bash
cd /root/cartosky && /root/.era5-prep-venv/bin/python backend/scripts/stage_era5_climatology_source.py --input-root /data/era5-raw/single-levels/tmp2m --stage-root /data/era5-stage --field tmp2m --start-year 1991 --end-year 1991 --hours 0 6 12 18
```
```bash
/root/.era5-prep-venv/bin/python -c "
import rasterio
with rasterio.open('/data/era5-stage/era5/single-levels/tmp2m/1991/1991010100_tmp2m.tif') as ds:
    print(ds.crs, ds.width, ds.height, ds.transform)
"
```
⛔ EXPECT exactly `EPSG:4326 1440 721` and transform
`| 0.25, 0.00,-180.12| 0.00,-0.25, 90.12|`. Anything else: STOP.

**4-5. Stage remaining years, deleting raw per year** (VM; raw is safe on the
Mac from step 2; `|| break` stops on failure instead of deleting):
```bash
cd /root/cartosky && rm -rf /data/era5-raw/single-levels/tmp2m/1991 && for Y in $(seq 1992 2020); do /root/.era5-prep-venv/bin/python backend/scripts/stage_era5_climatology_source.py --input-root /data/era5-raw/single-levels/tmp2m --stage-root /data/era5-stage --field tmp2m --start-year $Y --end-year $Y --hours 0 6 12 18 && rm -rf /data/era5-raw/single-levels/tmp2m/$Y || break; done
```
```bash
find /data/era5-stage/era5/single-levels/tmp2m -name '*.tif' | wc -l
```
⛔ EXPECT `43832`.

**6. Build** (VM):
```bash
cd /root/cartosky && PYTHONPATH=backend /root/cartosky/.venv/bin/python backend/scripts/build_climatology_baseline_assets.py --source-root /data/era5-stage/era5/single-levels/tmp2m --data-root /data/cartosky-global-baselines --version v1 --baseline-source era5 --field tmp2m --region global --reference-period 1991-2020 --units-in K --smoothing-window-days 15 --start-year 1991 --end-year 2020 --require-complete
```
⛔ Summary must contain
`'target_crs': 'EPSG:4326', 'target_resolution': 0.25, 'target_shape': [721, 1440]`.

**7. Verify count, free staged** (VM):
```bash
find /data/cartosky-global-baselines/climatology/v1/era5/baseline/tmp2m/global/1991-2020 -name 'doy_*_h*.tif' | wc -l && rm -rf /data/era5-stage/era5/single-levels/tmp2m
```
⛔ EXPECT `1464`.

**8. Pull built baselines to Mac** (Mac; incremental — picks up each new field):
```bash
rsync -a --info=progress2 root@<VM_IP>:/data/cartosky-global-baselines/climatology/ ~/era5-archive/baselines/climatology/
```

### Field 2 — tmp850  (same beats; note `pressure-levels` paths)

```bash
/root/.era5-prep-venv/bin/python /root/download_era5.py tmp850
```
```bash
rsync -a --info=progress2 root@<VM_IP>:/data/era5-raw/pressure-levels/tmp850/ ~/era5-archive/raw/pressure-levels/tmp850/
```
```bash
cd /root/cartosky && /root/.era5-prep-venv/bin/python backend/scripts/stage_era5_climatology_source.py --input-root /data/era5-raw/pressure-levels/tmp850 --stage-root /data/era5-stage --field tmp850 --start-year 1991 --end-year 1991 --hours 0 6 12 18 && /root/.era5-prep-venv/bin/python -c "
import rasterio
with rasterio.open('/data/era5-stage/era5/pressure-levels/tmp850/1991/1991010100_tmp850.tif') as ds:
    print(ds.crs, ds.width, ds.height, ds.transform)
"
```
⛔ Same expected grid line as Field 1.
```bash
cd /root/cartosky && rm -rf /data/era5-raw/pressure-levels/tmp850/1991 && for Y in $(seq 1992 2020); do /root/.era5-prep-venv/bin/python backend/scripts/stage_era5_climatology_source.py --input-root /data/era5-raw/pressure-levels/tmp850 --stage-root /data/era5-stage --field tmp850 --start-year $Y --end-year $Y --hours 0 6 12 18 && rm -rf /data/era5-raw/pressure-levels/tmp850/$Y || break; done
```
```bash
find /data/era5-stage/era5/pressure-levels/tmp850 -name '*.tif' | wc -l
```
⛔ EXPECT `43832`.
```bash
cd /root/cartosky && PYTHONPATH=backend /root/cartosky/.venv/bin/python backend/scripts/build_climatology_baseline_assets.py --source-root /data/era5-stage/era5/pressure-levels/tmp850 --data-root /data/cartosky-global-baselines --version v1 --baseline-source era5 --field tmp850 --region global --reference-period 1991-2020 --units-in K --smoothing-window-days 15 --start-year 1991 --end-year 2020 --require-complete
```
```bash
find /data/cartosky-global-baselines/climatology/v1/era5/baseline/tmp850/global/1991-2020 -name 'doy_*_h*.tif' | wc -l && rm -rf /data/era5-stage/era5/pressure-levels/tmp850
```
⛔ EXPECT `1464`. Then re-run the Mac baseline pull (Field 1 step 8 command).

### Field 3 — hgt500  (⚠ the ONLY units difference: `--units-in m`)

```bash
/root/.era5-prep-venv/bin/python /root/download_era5.py hgt500
```
```bash
rsync -a --info=progress2 root@<VM_IP>:/data/era5-raw/pressure-levels/hgt500/ ~/era5-archive/raw/pressure-levels/hgt500/
```
```bash
cd /root/cartosky && /root/.era5-prep-venv/bin/python backend/scripts/stage_era5_climatology_source.py --input-root /data/era5-raw/pressure-levels/hgt500 --stage-root /data/era5-stage --field hgt500 --start-year 1991 --end-year 1991 --hours 0 6 12 18 && /root/.era5-prep-venv/bin/python -c "
import rasterio
with rasterio.open('/data/era5-stage/era5/pressure-levels/hgt500/1991/1991010100_hgt500.tif') as ds:
    print(ds.crs, ds.width, ds.height, ds.transform)
"
```
⛔ Same expected grid line.
```bash
cd /root/cartosky && rm -rf /data/era5-raw/pressure-levels/hgt500/1991 && for Y in $(seq 1992 2020); do /root/.era5-prep-venv/bin/python backend/scripts/stage_era5_climatology_source.py --input-root /data/era5-raw/pressure-levels/hgt500 --stage-root /data/era5-stage --field hgt500 --start-year $Y --end-year $Y --hours 0 6 12 18 && rm -rf /data/era5-raw/pressure-levels/hgt500/$Y || break; done
```
```bash
find /data/era5-stage/era5/pressure-levels/hgt500 -name '*.tif' | wc -l
```
⛔ EXPECT `43832`.
```bash
cd /root/cartosky && PYTHONPATH=backend /root/cartosky/.venv/bin/python backend/scripts/build_climatology_baseline_assets.py --source-root /data/era5-stage/era5/pressure-levels/hgt500 --data-root /data/cartosky-global-baselines --version v1 --baseline-source era5 --field hgt500 --region global --reference-period 1991-2020 --units-in m --smoothing-window-days 15 --start-year 1991 --end-year 2020 --require-complete
```
(`--units-in m`: staged hgt500 is metres — the stage script already divided
geopotential by g — and the build divides by 10 to store decametres.)
```bash
find /data/cartosky-global-baselines/climatology/v1/era5/baseline/hgt500/global/1991-2020 -name 'doy_*_h*.tif' | wc -l && rm -rf /data/era5-stage/era5/pressure-levels/hgt500
```
⛔ EXPECT `1464`. Then re-run the Mac baseline pull.

---

## Phase D — final validation, upload, decom

**D1. ⛔ Loader check on the VM** — runs the real prod loader (validates CRS,
shape AND transform); from `/root/cartosky`:
```bash
cd /root/cartosky && PYTHONPATH=backend /root/cartosky/.venv/bin/python - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
from app.services.climatology import configure_data_root, load_climatology_baseline
configure_data_root(Path("/data/cartosky-global-baselines"))
for field in ("tmp2m", "tmp850", "hgt500"):
    arr, crs, transform, meta = load_climatology_baseline(
        version="v1", baseline_source="era5", field=field,
        valid_time=datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
        region="global", reference_period="1991-2020",
    )
    print(field, arr.shape, crs, transform, float(arr[360, 720]))
PY
```
⛔ EXPECT `(721, 1440) EPSG:4326 | 0.25, 0.00,-180.12| ...` for all three, no
exception. A raise here is the check working — investigate before shipping.

**D2. Final Mac baseline pull** (Mac), then verify the Mac copy:
```bash
rsync -a --info=progress2 root@<VM_IP>:/data/cartosky-global-baselines/climatology/ ~/era5-archive/baselines/climatology/ && for f in tmp2m tmp850 hgt500; do echo -n "$f: "; find ~/era5-archive/baselines/climatology/v1/era5/baseline/$f/global/1991-2020 -name 'doy_*_h*.tif' | wc -l; done
```
⛔ EXPECT `1464` × 3 on the Mac.

**D3. Decom the VM.** After D1 + D2, the VM holds nothing irreplaceable (raw
+ built are on the Mac). Delete the Hetzner server; billing stops.

**D4. Upload Mac → prod** (Mac; hidden `.incoming` then atomic rename):
```bash
for f in tmp2m tmp850 hgt500; do DEST=/opt/cartosky/data/climatology/v1/era5/baseline/$f/global; ssh brian@cartosky-server "mkdir -p $DEST"; rsync -a --info=progress2 ~/era5-archive/baselines/climatology/v1/era5/baseline/$f/global/1991-2020/ brian@cartosky-server:$DEST/.1991-2020.incoming/ && ssh brian@cartosky-server "mv $DEST/.1991-2020.incoming $DEST/1991-2020"; done
```

**D5. ⛔ Post-copy checks on prod:**
```bash
for f in tmp2m tmp850 hgt500; do echo -n "$f: "; find /opt/cartosky/data/climatology/v1/era5/baseline/$f/global/1991-2020 -name 'doy_*_h*.tif' | wc -l; done; du -sh /opt/cartosky/data/climatology/v1/era5/baseline/*/global; df -h /opt/cartosky/data; ls -l /opt/cartosky/data/climatology/v1/era5/baseline/tmp2m/na/1991-2020 | head -3; ls -l /opt/cartosky/data/climatology/v1/era5/baseline/tmp2m/global/1991-2020 | head -3
```
⛔ EXPECT 1464 per field, ~5.7G each, volume well under 50%, and group/
readability matching the `na` listing (if group differs:
`chgrp -R cartosky /opt/cartosky/data/climatology/v1/era5/baseline/*/global`).

---

## Phase E — prod deploy + gate (runbook §6-§7)

**E1. Deploy** (prod; baselines are now installed, so this is the required
order):
```bash
cd /opt/cartosky && git pull && sudo systemctl restart csky-gfs-scheduler csky-api
```
Confirm the flag still includes gfs:
```bash
grep CARTOSKY_GLOBAL_DOMAIN_MODELS /etc/cartosky/api.env /etc/cartosky/scheduler-gfs.env
```

**E2. Watch the first GFS cycle:**
```bash
sudo journalctl -u csky-gfs-scheduler -f | grep -E 'climatology_baseline_missing|skipped_missing_baseline|global/(tmp2m|tmp850|hgt500)_anom'
```
⛔ ZERO `climatology_baseline_missing` lines (one names the exact missing
asset); `Build success ... global/hgt500_anom` lines appear.

**E3. API + frames checks:**
```bash
curl -s 'https://api.cartosky.com/api/v4/capabilities' | jq '.model_catalog.gfs.variables | with_entries(select(.key|endswith("_anom"))) | map_values(.supported_build_regions)'
```
⛔ EXPECT the three instantaneous vars → `["na","global"]`, all four
precip vars → `[]`. All-empty = stale API process, restart it.
```bash
RUN=$(curl -s 'https://api.cartosky.com/api/v4/gfs/runs?domain=global' | jq -r '.[0]'); curl -s "https://api.cartosky.com/api/v4/gfs/$RUN/hgt500_anom/frames?domain=global" | jq 'length'; curl -s "https://api.cartosky.com/api/v4/gfs/$RUN/hgt500_anom/grid-manifest?domain=global" | jq '{projection, width, height}'
```
⛔ EXPECT `105` frames (complete cycle) and
`{"projection":"EPSG:4326","width":1440,"height":721}` — a 3857 here means
the derive path warped when it should have rolled.

**E4. ⛔ NA-overlap parity table** (the plan-required evidence; record the
output in phase notes):
```bash
RUN=$(curl -s 'https://api.cartosky.com/api/v4/gfs/runs?domain=global' | jq -r '.[0]'); FH=24; for P in "39.74 -104.98 Denver" "40.71 -74.01 NewYork" "47.61 -122.33 Seattle" "25.76 -80.19 Miami" "61.22 -149.90 Anchorage"; do set -- $P; LAT=$1; LON=$2; NAME=$3; for VAR in hgt500_anom tmp2m_anom tmp850_anom; do NA=$(curl -s "https://api.cartosky.com/api/v4/sample?model=gfs&run=$RUN&var=$VAR&fh=$FH&lat=$LAT&lon=$LON" | jq -r '.value'); GL=$(curl -s "https://api.cartosky.com/api/v4/sample?model=gfs&run=$RUN&var=$VAR&fh=$FH&lat=$LAT&lon=$LON&domain=global" | jq -r '.value'); echo "$NAME $VAR na=$NA global=$GL"; done; done
```
Acceptance (interior points; Anchorage is a high-latitude probe, not a
tight-tolerance point): hgt500_anom ≤10 m (typ <4); tmp2m_anom ≤2 °F flat
terrain (mountain disagreement that shrinks at flat points = terrain
aliasing, expected); tmp850_anom ≤1 °C. Sign flips or field-scale
differences = real failure (units or roll — if hgt500 is off by hundreds of
metres, check the decametre storage). Full rules: runbook §7.4.

**E5. Viewer check:** load `?m=gfs&v=hgt500_anom&domain=global` — renders
globally, hover values plausible, °C/°F/dam units right in the legend.

Rollback at any point: runbook §8 — delete the three `global/1991-2020`
dirs on prod; the skip path resumes within one cycle; canonical untouched.
