# Open-Meteo bucket monitor + profile archive

Hourly systemd-timer job with two purposes (see `monitor.py` docstring):

1. **Timing monitor** — logs when each run's files actually landed in the
   Open-Meteo S3 bucket, per model (`timing.jsonl`, one row per finalized
   model+run). Answers "does AIFS *always* beat the IFS 9 km stream?" with a
   distribution instead of two data points. S3 `LastModified` is ground truth,
   so hourly polling loses nothing.
2. **Profile archive** — persists the Kuchera profile temps (925/850/700/600
   hPa + t2m) from AIFS and IFS 0.25°, plus IFS snowfall SWE, over the NA
   window for 00z/12z runs (`archive/<run>/<model>.npz`, int16 °C×100).
   The bucket purges after ~7 days; this builds the case library (incl.
   warm-nose events) needed to validate the AIFS-profile Kuchera hybrid
   before winter.

Model-agnostic: add any bucket model (e.g. `dwd_icon`) to `OM_MONITOR_MODELS`
to start logging its timing; archive pairs are config in `ARCHIVE_PAIRS`.

## Env knobs (via /etc/cartosky/openmeteo-monitor.env)

- `OM_MONITOR_MODELS` (default `ecmwf_ifs,ecmwf_ifs025,ecmwf_aifs025_single`)
- `OM_ARCHIVE_CYCLES` (default `0000,1200`)
- `OM_ARCHIVE_MAX_FH` (default `144`)
- `OM_MONITOR_DATA_DIR` (default `/var/lib/cartosky/openmeteo-monitor`)

Disk: roughly 50–60 MB/day at defaults (~5–6 GB by December). No pruning by
design — the history is the point.

## Prod setup (run on the prod box)

```bash
cd /opt/cartosky && sudo -u cartosky git pull
sudo -u cartosky python3 -m venv /opt/cartosky/.venv-ommonitor
sudo -u cartosky /opt/cartosky/.venv-ommonitor/bin/pip install omfiles numpy requests
sudo mkdir -p /var/lib/cartosky/openmeteo-monitor
sudo chown cartosky:cartosky /var/lib/cartosky/openmeteo-monitor
sudo cp deployment/systemd/csky-openmeteo-monitor.service deployment/systemd/csky-openmeteo-monitor.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now csky-openmeteo-monitor.timer
```

Verify:

```bash
sudo systemctl start csky-openmeteo-monitor.service
journalctl -u csky-openmeteo-monitor.service -n 50 --no-pager
systemctl list-timers csky-openmeteo-monitor.timer --no-pager
ls -la /var/lib/cartosky/openmeteo-monitor
```

First manual start will take a few minutes (it backfills timing + archives for
today's and yesterday's completed runs); subsequent hourly runs are seconds
unless a new run just completed.

The dedicated `.venv-ommonitor` keeps the GPLv2 `omfiles` reader isolated from
the main app venv (swappable, no license entanglement).
