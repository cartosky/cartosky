"""Open-Meteo bucket timing monitor + profile-field archive.

Two jobs, one idempotent pass per invocation (run it from a systemd timer):

1. Timing monitor: for each configured model, record when each run's files
   actually landed in the bucket (S3 LastModified is ground truth, so polling
   cadence does not affect accuracy — we just have to look within the ~7 day
   bucket retention). One JSONL row per finalized (model, run).

2. Profile archive: for configured model pairs (default AIFS vs IFS 0.25°),
   persist the Kuchera-relevant fields over the NA window for every shared
   valid time, so AIFS-vs-IFS agreement can be evaluated over months of real
   cases (incl. warm-nose events) long after the bucket has purged the runs.

Model-agnostic by design: the bucket layout (data_spatial/{model}/YYYY/MM/DD/
HHMMZ/*.om + meta.json) is uniform across ECMWF/ICON/GEM/UKMO/..., so adding a
model to MONITOR_MODELS is one string. Only ARCHIVE_PAIRS carries
variable-specific config.
"""
from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

from omblock import BlockCachedHTTPFS

BUCKET = "https://openmeteo.s3.amazonaws.com"
S3NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

MONITOR_MODELS = [
    m.strip()
    for m in os.environ.get(
        "OM_MONITOR_MODELS", "ecmwf_ifs,ecmwf_ifs025,ecmwf_aifs025_single"
    ).split(",")
    if m.strip()
]
ARCHIVE_CYCLES = [
    c.strip()
    for c in os.environ.get("OM_ARCHIVE_CYCLES", "0000,1200").split(",")
    if c.strip()
]
ARCHIVE_MAX_FH = int(os.environ.get("OM_ARCHIVE_MAX_FH", "144"))
DATA_DIR = Path(os.environ.get("OM_MONITOR_DATA_DIR", "/var/lib/cartosky/openmeteo-monitor"))

PROFILE_VARS = [
    "temperature_925hPa",
    "temperature_850hPa",
    "temperature_700hPa",
    "temperature_600hPa",
    "temperature_2m",
]
# (model, variables) pairs archived at shared valid times. snowfall from IFS
# only: it is the held-fixed precip component in the Kuchera comparison.
ARCHIVE_PAIRS = [
    ("ecmwf_aifs025_single", PROFILE_VARS),
    ("ecmwf_ifs025", PROFILE_VARS + ["snowfall_water_equivalent"]),
]
NLAT, NLON = 721, 1440  # 0.25 deg global, both poles
NA_LAT = (5.0, 82.0)
NA_LON = (-178.0, -25.0)
FINALIZE_IDLE_HOURS = 3.0  # fallback if meta.json never flips completed


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def list_objects(prefix: str) -> list[tuple[str, datetime]]:
    """List (key, last_modified) under a prefix, paginated."""
    out: list[tuple[str, datetime]] = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        r = requests.get(BUCKET, params=params, timeout=60)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for c in root.findall(f"{S3NS}Contents"):
            key = c.find(f"{S3NS}Key").text
            lm = datetime.fromisoformat(
                c.find(f"{S3NS}LastModified").text.replace("Z", "+00:00")
            )
            out.append((key, lm))
        if root.findtext(f"{S3NS}IsTruncated") != "true":
            return out
        token = root.findtext(f"{S3NS}NextContinuationToken")


def run_prefix(model: str, run: datetime) -> str:
    return f"data_spatial/{model}/{run:%Y/%m/%d}/{run:%H%M}Z/"


def recent_runs() -> list[datetime]:
    now = utcnow()
    runs = []
    for day_back in (0, 1):
        day = (now - timedelta(days=day_back)).date()
        for hh in (0, 6, 12, 18):
            run = datetime(day.year, day.month, day.day, hh, tzinfo=timezone.utc)
            if run < now:
                runs.append(run)
    return runs


def load_state() -> dict:
    p = DATA_DIR / "state.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"finalized": {}, "archived": []}


def save_state(state: dict) -> None:
    p = DATA_DIR / "state.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(p)


def meta_completed(prefix: str) -> bool | None:
    r = requests.get(f"{BUCKET}/{prefix}meta.json", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return bool(r.json().get("completed"))


def monitor_timing(state: dict) -> None:
    log = DATA_DIR / "timing.jsonl"
    for model in MONITOR_MODELS:
        for run in recent_runs():
            key = f"{model}/{run:%Y%m%dT%H%M}"
            if key in state["finalized"]:
                continue
            prefix = run_prefix(model, run)
            objs = [(k, lm) for k, lm in list_objects(prefix) if k.endswith(".om")]
            if not objs:
                continue
            first = min(lm for _, lm in objs)
            last = max(lm for _, lm in objs)
            completed = meta_completed(prefix)
            idle_h = (utcnow() - last).total_seconds() / 3600.0
            if completed is True:
                reason = "meta_completed"
            elif idle_h >= FINALIZE_IDLE_HOURS:
                reason = f"idle_{idle_h:.1f}h"
            else:
                continue  # still rolling in; revisit next pass
            row = {
                "model": model,
                "run": run.isoformat(),
                "n_om_files": len(objs),
                "first_upload": first.isoformat(),
                "last_upload": last.isoformat(),
                "upload_span_s": int((last - first).total_seconds()),
                "start_lag_s": int((first - run).total_seconds()),
                "finalized_reason": reason,
                "recorded_at": utcnow().isoformat(),
            }
            with log.open("a") as f:
                f.write(json.dumps(row) + "\n")
            state["finalized"][key] = {"last_upload": last.isoformat()}
            print(f"timing: finalized {key} files={len(objs)} span={row['upload_span_s']}s")


def detect_south_to_north(t2m: np.ndarray) -> bool:
    """Season-independent orientation check. The 45N row crosses N America and
    Eurasia (high land fraction -> high lon-variance of t2m); 45S is nearly
    pure Southern Ocean (low variance) in every season. Note ±23 is NOT safe
    here: the Capricorn row crosses Australia/Kalahari/Andes and its variance
    can rival the Sahara's (bit us on 2026-08-04)."""
    i_n = round((45.0 + 90.0) / 0.25)  # row index if grid is S->N
    i_s = round((-45.0 + 90.0) / 0.25)
    var_if_s2n = float(np.nanvar(t2m[i_n]))
    var_if_n2s = float(np.nanvar(t2m[i_s]))
    return var_if_s2n >= var_if_n2s


def archive_run(model: str, run: datetime, variables: list[str], state: dict) -> bool:
    key = f"archive/{model}/{run:%Y%m%dT%H%M}"
    if key in state["archived"]:
        return True
    prefix = run_prefix(model, run)
    objs = [k for k, _ in list_objects(prefix) if k.endswith(".om")]
    if not objs or meta_completed(prefix) is not True:
        return False
    from omfiles import OmFileReader  # GPLv2 reader: keep import isolated here

    out_dir = DATA_DIR / "archive" / f"{run:%Y%m%dT%H%M}"
    out_dir.mkdir(parents=True, exist_ok=True)
    lats = np.linspace(-90, 90, NLAT)
    lons = np.linspace(-180, 179.75, NLON)
    arrays: dict[str, np.ndarray] = {}
    meta: dict = {
        "model": model,
        "run": run.isoformat(),
        "units": "degC x100 int16",
        "lat_range": NA_LAT,
        "lon_range": NA_LON,
        "valid_times": [],
        "variables": variables,
    }
    orientation_logged = False
    for obj_key in sorted(objs):
        stem = obj_key.rsplit("/", 1)[-1].removesuffix(".om")  # YYYY-MM-DDTHHMM
        valid = datetime.strptime(stem, "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
        fh = (valid - run).total_seconds() / 3600.0
        if fh <= 0 or fh > ARCHIVE_MAX_FH:
            continue
        fs = BlockCachedHTTPFS()
        rd = OmFileReader.from_fsspec(fs, f"{BUCKET}/{obj_key}")
        fields = {}
        for var in variables:
            child = rd.get_child_by_name(var)
            if child is None:
                continue
            if len(child.shape) == 2:
                arr = np.asarray(child.read_array((slice(0, NLAT), slice(0, NLON))))
            else:
                arr = np.asarray(
                    child.read_array((slice(0, 1), slice(0, NLAT * NLON)))
                ).reshape(NLAT, NLON)
            fields[var] = arr
        # Detect orientation ONCE per run (first file with t2m), then apply the
        # same normalization to every step: per-file detection can flip on
        # borderline fields and silently mirror individual steps.
        if not orientation_logged and "temperature_2m" in fields:
            s2n = detect_south_to_north(fields["temperature_2m"])
            meta["south_to_north"] = s2n
            orientation_logged = True
            if not s2n:
                print(f"NOTE: {model} {run:%Y%m%dT%H%M} grid is N->S; normalizing to S->N")
        s2n = meta.get("south_to_north", True)
        na = np.ix_(
            (lats >= NA_LAT[0]) & (lats <= NA_LAT[1]),
            (lons >= NA_LON[0]) & (lons <= NA_LON[1]),
        )
        for var, arr in fields.items():
            if not s2n:
                arr = arr[::-1]  # normalize to S->N, then window canonically
            window = arr[na]
            scaled = np.round(window * 100.0)
            arrays[f"{var}@{stem}"] = np.clip(scaled, -32768, 32767).astype(np.int16)
        # Physical self-check: window rows run lat 5N -> 82N, so t2m must be
        # warmer at the tropics edge than the Arctic edge.
        if "temperature_2m" in fields:
            t2m_win = arrays[f"temperature_2m@{stem}"]
            if float(t2m_win[0].mean()) < float(t2m_win[-1].mean()):
                print(
                    f"WARNING: {model} {run:%Y%m%dT%H%M} {stem}: window orientation "
                    "suspect (Arctic edge warmer than tropics edge)",
                    file=sys.stderr,
                )
        meta["valid_times"].append(stem)
    if not arrays:
        return False
    np.savez_compressed(out_dir / f"{model}.npz", **arrays)
    (out_dir / f"{model}.meta.json").write_text(json.dumps(meta, indent=1))
    state["archived"].append(key)
    mb = (out_dir / f"{model}.npz").stat().st_size / 1e6
    print(f"archive: {key} steps={len(meta['valid_times'])} size={mb:.1f}MB")
    return True


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    try:
        monitor_timing(state)
        for run in recent_runs():
            if f"{run:%H%M}" not in ARCHIVE_CYCLES:
                continue
            for model, variables in ARCHIVE_PAIRS:
                try:
                    archive_run(model, run, variables, state)
                except Exception as exc:  # one bad run must not block the rest
                    print(f"ERROR archiving {model} {run:%Y%m%dT%H%M}: {exc}", file=sys.stderr)
    finally:
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
