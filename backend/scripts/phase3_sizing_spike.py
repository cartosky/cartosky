#!/usr/bin/env python3
# THROWAWAY — Phase 3 sizing spike, see docs/MODEL_GUIDANCE_PHASE3_SIZING_SPIKE.md
"""Phase 3 ensemble-member sizing spike (standalone, throwaway).

Measures the real disk / inode / latency / memory cost of publishing one full
GEFS ensemble (all perturbation members + control) for ONE variable (`tmp2m`)
and ONE run, so Brian can make an informed go/no-go on Phase 3 per Section 7 of
docs/MODEL_GUIDANCE_IMPLEMENTATION_PLAN.md.

This script is NOT part of the scheduler, is NOT imported by it, and must be run
on the production server (not local dev) so the measured numbers transfer to the
go/no-go decision. See the deployment block printed at the end of the chat turn.

What it does
------------
1. Resolves the latest available GEFS run the same way the scheduler does
   (``_resolve_latest_run_dt`` + plugin run-discovery probe).
2. Confirms the actual upstream ``atmos.5`` member set via live IDX probes —
   does NOT assume 30+1. Reports the literal Herbie ``member`` kwarg used for the
   control run vs perturbation members (resolves Section 8 open question #2).
3. Reads the full forecast-hour list the same way the scheduler does for the
   mean artifact (``scheduled_fhs_for_var`` / ``target_fhs`` — not hardcoded).
4. Fetches + warps + writes a value COG (``fhNNN.val.cog.tif``) for every
   ``(member, fh)`` work unit through a 4-worker ThreadPoolExecutor (matching the
   deployed ``CARTOSKY_WORKERS=4`` on scheduler-gefs.env).
5. Samples peak RSS of this process throughout and reports disk/inode/latency.

All output (measurement JSON + the disk-consuming test COGs) goes under
``/tmp/phase3-spike/`` (override with ``CARTOSKY_PHASE3_SPIKE_ROOT``) so a runaway
result is trivially deletable and never touches the live ``published/`` tree or
``/var/lib/cartosky`` / the real data root. The script deliberately does NOT read
or write the production data root.

Scope note: only the value COG (``*.val.cog.tif``) is produced — that is the only
member artifact the meteogram sampler reads (Section 2/7); members are not map-
rendered, so no RGBA COG / sidecar / grid frame is written. The disk figure is
therefore the member-publishing footprint the meteogram path actually needs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- Make `app...` importable regardless of CWD (mirrors how the deployed -----
#     scheduler resolves its package root; no hardcoded local paths). ----------
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import numpy as np  # noqa: E402

from app.models.gefs import GEFS_MODEL  # noqa: E402
from app.services.builder.cog_writer import warp_to_target_grid, write_value_cog  # noqa: E402
from app.services.builder.fetch import (  # noqa: E402
    convert_units,
    fetch_variable,
    product_hour_has_any_idx,
)
# Reuse the project's RSS helpers (psutil-optional, with a `resource` fallback) —
# the same module the production build pipeline uses for its memory audit, so the
# peak figure here is measured the same way. peak_rss_bytes() is the OS high-water
# mark (ru_maxrss / VmHWM); current_rss_bytes() is sampled for a cross-check series.
from app.services.process_memory import current_rss_bytes, peak_rss_bytes  # noqa: E402
from app.services.render_resampling import resampling_name_for_kind  # noqa: E402

# Resolve the latest run + run-id formatting EXACTLY the way the scheduler does.
from app.services.scheduler import (  # noqa: E402
    _resolve_latest_run_dt,
    _run_id_from_dt,
)

MODEL_ID = "gefs"
PRODUCT = "atmos.5"
VAR_KEY = "tmp2m"
# GEFS publishes its mean artifacts on the canonical region; members would follow.
REGION = str(getattr(GEFS_MODEL.capabilities, "canonical_region", "na") or "na")

# Output root — intentionally NOT the production data root. Trivially deletable.
SPIKE_ROOT = Path(os.environ.get("CARTOSKY_PHASE3_SPIKE_ROOT", "/tmp/phase3-spike"))

# 4 workers, matching deployed CARTOSKY_WORKERS on scheduler-gefs.env. Honors the
# same env var if present but defaults to 4; we do NOT test sequential or
# unconstrained parallelism — 4 is what makes this representative of production.
WORKERS = max(1, int(os.environ.get("CARTOSKY_WORKERS", "4") or "4"))

RSS_SAMPLE_INTERVAL_S = 2.0


# ---------------------------------------------------------------------------
# Peak-RSS sampler
# ---------------------------------------------------------------------------
class PeakRSSSampler:
    """Background sampler tracking peak process RSS via the project helpers.

    ``sampled_peak_rss_bytes`` is the max of the sampled ``current_rss_bytes()``
    series; ``os_peak_rss_bytes`` is the OS high-water mark from
    ``peak_rss_bytes()`` (ru_maxrss / VmHWM) read at exit — the authoritative
    peak regardless of sampling cadence.
    """

    def __init__(self, interval_s: float = RSS_SAMPLE_INTERVAL_S) -> None:
        self._interval = float(interval_s)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rss-sampler", daemon=True)
        self.sampled_peak_rss_bytes = 0
        self.os_peak_rss_bytes = 0
        self.samples = 0

    def _record(self) -> None:
        try:
            rss = int(current_rss_bytes())
        except Exception:
            return
        self.sampled_peak_rss_bytes = max(self.sampled_peak_rss_bytes, rss)
        self.samples += 1

    def _run(self) -> None:
        while not self._stop.is_set():
            self._record()
            self._stop.wait(self._interval)

    def __enter__(self) -> "PeakRSSSampler":
        self._record()  # baseline before any work
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval + 1.0)
        self._record()  # final reading
        try:
            self.os_peak_rss_bytes = int(peak_rss_bytes())
        except Exception:
            self.os_peak_rss_bytes = self.sampled_peak_rss_bytes

    @property
    def peak_rss_bytes(self) -> int:
        return max(self.os_peak_rss_bytes, self.sampled_peak_rss_bytes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _herbie_priority() -> list[str]:
    """Source priority matching production GEFS run-discovery config."""
    cfg = GEFS_MODEL.run_discovery_config() if hasattr(GEFS_MODEL, "run_discovery_config") else {}
    pri = cfg.get("source_priority")
    if isinstance(pri, (list, tuple)) and pri:
        return [str(p).strip().lower() for p in pri if str(p).strip()]
    return ["aws", "nomads", "google", "azure"]


def _probe_fhs() -> list[int]:
    cfg = GEFS_MODEL.run_discovery_config() if hasattr(GEFS_MODEL, "run_discovery_config") else {}
    raw = cfg.get("probe_fhs")
    if isinstance(raw, (list, tuple)) and raw:
        return [int(fh) for fh in raw]
    return [0, 6]


def _search_pattern() -> str:
    spec = GEFS_MODEL.get_var(VAR_KEY)
    patterns = list(getattr(getattr(spec, "selectors", None), "search", []) or [])
    if not patterns:
        raise RuntimeError(f"No search pattern resolved for {VAR_KEY!r}")
    return str(patterns[0])


def _forecast_hours(run_dt: datetime) -> list[int]:
    """Resolve the full forecast-hour list the same way the scheduler does."""
    cycle_hour = int(run_dt.hour)
    if hasattr(GEFS_MODEL, "scheduled_fhs_for_var"):
        fhs = list(GEFS_MODEL.scheduled_fhs_for_var(VAR_KEY, cycle_hour))
    else:
        fhs = [int(fh) for fh in GEFS_MODEL.target_fhs(cycle_hour)]
    return sorted({int(fh) for fh in fhs})


def _rendered_member(run_dt: datetime, member_kwarg: Any) -> str:
    """Resolve the file token Herbie renders for a given member kwarg (e.g. c00/p01).

    Constructing a Herbie object resolves URLs/templates without downloading GRIB.
    """
    try:
        from herbie.core import Herbie

        herbie_date = run_dt.replace(tzinfo=None) if run_dt.tzinfo else run_dt
        H = Herbie(
            herbie_date,
            model=MODEL_ID,
            product=PRODUCT,
            fxx=int(_probe_fhs()[0]),
            member=member_kwarg,
            priority=_herbie_priority()[0],
            verbose=False,
        )
        return str(getattr(H, "member", "")) or "?"
    except Exception:
        return "?"


def confirm_members(run_dt: datetime, *, max_perturbation_probe: int) -> dict[str, Any]:
    """Confirm the live ``atmos.5`` member set via IDX probes (no assumptions).

    Returns the confirmed work members plus the literal Herbie ``member`` kwarg
    used for control vs perturbations, and a rendered-token mapping for the doc.
    """
    priority = _herbie_priority()
    probe_fhs = _probe_fhs()

    def _exists(member_kwarg: Any) -> bool:
        for fh in probe_fhs:
            try:
                ready = product_hour_has_any_idx(
                    model_id=MODEL_ID,
                    product=PRODUCT,
                    run_date=run_dt,
                    fh=int(fh),
                    herbie_kwargs={"member": member_kwarg, "priority": priority},
                )
            except Exception:
                ready = False
            if ready:
                return True
        return False

    # Control: Herbie maps member=0 -> file token "c00".
    control_present = _exists(0)

    # Perturbations: member=N (int) -> "pNN". Probe past the expected cap so we
    # detect the REAL count rather than assuming 30.
    perturbations: list[int] = []
    for n in range(1, max_perturbation_probe + 1):
        if _exists(n):
            perturbations.append(n)

    members: list[dict[str, Any]] = []
    if control_present:
        members.append(
            {
                "label": "control",
                "member_kwarg": 0,
                "rendered": _rendered_member(run_dt, 0),
            }
        )
    for n in perturbations:
        members.append(
            {
                "label": f"m{n:02d}",
                "member_kwarg": n,
                "rendered": _rendered_member(run_dt, n),
            }
        )

    return {
        "control_present": control_present,
        "perturbation_count": len(perturbations),
        "perturbation_member_kwargs": perturbations,
        "total_member_count": len(members),
        "max_perturbation_probed": max_perturbation_probe,
        "control_member_kwarg": 0,
        "control_rendered": _rendered_member(run_dt, 0) if control_present else None,
        "m01_member_kwarg": 1,
        "m01_rendered": _rendered_member(run_dt, 1) if perturbations else None,
        "members": members,
    }


# ---------------------------------------------------------------------------
# Single (member, fh) work unit — mirrors the scheduler's per-unit granularity
# ---------------------------------------------------------------------------
def build_member_fh(
    *,
    member_label: str,
    member_kwarg: Any,
    fh: int,
    run_dt: datetime,
    search_pattern: str,
    resampling: str,
    var_capability: Any,
) -> dict[str, Any]:
    """Fetch -> convert -> warp -> write one member value COG. Returns metrics.

    The member-qualified runtime var id (e.g. ``tmp2m__m01`` / ``tmp2m__control``)
    is used only to lay out the output path — matching the Section 7 storage schema.
    """
    runtime_var = f"{VAR_KEY}__{member_label}"
    out_path = (
        SPIKE_ROOT
        / "staging"
        / MODEL_ID
        / _run_id_from_dt(run_dt)
        / runtime_var
        / f"fh{int(fh):03d}.val.cog.tif"
    )
    started = time.perf_counter()
    try:
        raw, src_crs, src_transform = fetch_variable(
            model_id=MODEL_ID,
            product=PRODUCT,
            search_pattern=search_pattern,
            run_date=run_dt,
            fh=int(fh),
            herbie_kwargs={"member": member_kwarg, "priority": _herbie_priority()},
        )
        converted = convert_units(raw, VAR_KEY, model_id=MODEL_ID, var_capability=var_capability)
        warped, _dst_transform = warp_to_target_grid(
            converted,
            src_crs,
            src_transform,
            model=MODEL_ID,
            region=REGION,
            resampling=resampling,
        )
        write_value_cog(np.asarray(warped, dtype=np.float32), out_path, model=MODEL_ID, region=REGION)
        size = int(out_path.stat().st_size)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "member": member_label,
            "fh": int(fh),
            "ok": True,
            "bytes": size,
            "elapsed_ms": elapsed_ms,
            "path": str(out_path),
        }
    except Exception as exc:  # never abort sibling work units
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "member": member_label,
            "fh": int(fh),
            "ok": False,
            "bytes": 0,
            "elapsed_ms": elapsed_ms,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=4),
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 3 GEFS tmp2m ensemble-member sizing spike")
    parser.add_argument(
        "--run",
        default=None,
        help="Run id YYYYMMDD_HHz to pin (default: resolve latest like the scheduler).",
    )
    parser.add_argument(
        "--max-perturbation-probe",
        type=int,
        default=35,
        help="Probe perturbation members 1..N to detect the real upstream cap (default 35).",
    )
    parser.add_argument(
        "--limit-fhs",
        type=int,
        default=0,
        help="DEBUG ONLY: cap forecast hours to the first N. 0 = full range (default).",
    )
    args = parser.parse_args()

    wall_start = time.perf_counter()
    started_at = datetime.now(timezone.utc)

    # --- Resolve run (same path as the scheduler) ---
    if args.run:
        from app.services.scheduler import _parse_run_id_datetime

        run_dt = _parse_run_id_datetime(args.run)
        if run_dt is None:
            print(f"ERROR: invalid --run {args.run!r}; expected YYYYMMDD_HHz", file=sys.stderr)
            return 2
    else:
        run_dt = _resolve_latest_run_dt(plugin=GEFS_MODEL, probe_var=VAR_KEY)
    run_id = _run_id_from_dt(run_dt)

    search_pattern = _search_pattern()
    resampling = resampling_name_for_kind(model_id=MODEL_ID, var_key=VAR_KEY, kind="continuous")
    var_capability = GEFS_MODEL.get_var_capability(VAR_KEY)
    conversion_id = getattr(var_capability, "conversion", None)

    print(f"[phase3-spike] model={MODEL_ID} product={PRODUCT} var={VAR_KEY} region={REGION}")
    print(f"[phase3-spike] run_id={run_id} (run_dt={run_dt.isoformat()})")
    print(f"[phase3-spike] search_pattern={search_pattern!r} resampling={resampling} conversion={conversion_id}")
    print(f"[phase3-spike] workers={WORKERS} output_root={SPIKE_ROOT}")

    # --- Confirm members live ---
    print("[phase3-spike] confirming upstream member set via live IDX probes ...")
    member_info = confirm_members(run_dt, max_perturbation_probe=args.max_perturbation_probe)
    print(
        f"[phase3-spike] members confirmed: total={member_info['total_member_count']} "
        f"(control_present={member_info['control_present']} "
        f"perturbations={member_info['perturbation_count']})"
    )
    print(
        f"[phase3-spike] control: member_kwarg={member_info['control_member_kwarg']!r} "
        f"-> rendered={member_info['control_rendered']!r}"
    )
    print(
        f"[phase3-spike] m01:     member_kwarg={member_info['m01_member_kwarg']!r} "
        f"-> rendered={member_info['m01_rendered']!r}"
    )
    if not member_info["members"]:
        print("ERROR: no members confirmed present for this run; aborting.", file=sys.stderr)
        return 3

    # --- Forecast hours (same source as the scheduler mean artifact) ---
    fhs = _forecast_hours(run_dt)
    if args.limit_fhs and args.limit_fhs > 0:
        fhs = fhs[: args.limit_fhs]
        print(f"[phase3-spike] WARNING: --limit-fhs active, using first {len(fhs)} fhs (NOT a full-scope measurement)")
    print(f"[phase3-spike] forecast_hours={len(fhs)} (min={fhs[0]} max={fhs[-1]})")

    work = [
        (m["label"], m["member_kwarg"], fh)
        for m in member_info["members"]
        for fh in fhs
    ]
    expected_units = len(member_info["members"]) * len(fhs)
    print(f"[phase3-spike] work units = {len(member_info['members'])} members x {len(fhs)} fhs = {len(work)}")

    # --- Run the 4-worker pool, sampling peak RSS throughout ---
    results: list[dict[str, Any]] = []
    build_start = time.perf_counter()
    with PeakRSSSampler() as rss:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [
                pool.submit(
                    build_member_fh,
                    member_label=label,
                    member_kwarg=kwarg,
                    fh=fh,
                    run_dt=run_dt,
                    search_pattern=search_pattern,
                    resampling=resampling,
                    var_capability=var_capability,
                )
                for (label, kwarg, fh) in work
            ]
            done = 0
            for future in as_completed(futures):
                res = future.result()
                results.append(res)
                done += 1
                if not res["ok"]:
                    print(f"  [fail] {res['member']} fh{res['fh']:03d}: {res.get('error')}")
                if done % 25 == 0 or done == len(futures):
                    print(f"  [progress] {done}/{len(futures)} units complete")
    build_elapsed_s = time.perf_counter() - build_start

    # --- Aggregate metrics ---
    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    sizes = [r["bytes"] for r in ok]
    total_bytes = int(sum(sizes))
    file_count = len(ok)
    avg_bytes = int(total_bytes / file_count) if file_count else 0
    max_bytes = int(max(sizes)) if sizes else 0
    min_bytes = int(min(sizes)) if sizes else 0
    per_unit_ms = (build_elapsed_s * 1000.0 / len(work)) if work else 0.0
    wall_elapsed_s = time.perf_counter() - wall_start

    measurement: dict[str, Any] = {
        "spike_version": "phase3-sizing-spike-1",
        "started_at_utc": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": MODEL_ID,
        "product": PRODUCT,
        "variable": VAR_KEY,
        "region": REGION,
        "run_id": run_id,
        "run_dt_utc": run_dt.isoformat(),
        "workers": WORKERS,
        "search_pattern": search_pattern,
        "resampling": resampling,
        "conversion_id": conversion_id,
        "limit_fhs_active": bool(args.limit_fhs and args.limit_fhs > 0),
        "member_confirmation": member_info,
        "forecast_hours": {"count": len(fhs), "min": fhs[0], "max": fhs[-1], "list": fhs},
        "work_units_expected": expected_units,
        "work_units_attempted": len(work),
        "disk": {
            "total_bytes": total_bytes,
            "total_gib": round(total_bytes / (1024 ** 3), 4),
            "file_count": file_count,
            "avg_bytes_per_file": avg_bytes,
            "min_bytes_per_file": min_bytes,
            "max_bytes_per_file": max_bytes,
            "formula": "total_bytes = avg_bytes_per_file x file_count",
        },
        "inodes": {
            "files_created": file_count,
            "expected": len(member_info["members"]) * len(fhs),
            "note": "member_count x forecast_hour_count (one .val.cog.tif per unit)",
        },
        "latency": {
            "build_wall_clock_s": round(build_elapsed_s, 2),
            "build_wall_clock_min": round(build_elapsed_s / 60.0, 2),
            "avg_per_unit_ms": round(per_unit_ms, 1),
            "total_script_wall_s": round(wall_elapsed_s, 2),
        },
        "memory": {
            "peak_rss_bytes": rss.peak_rss_bytes,
            "peak_rss_gib": round(rss.peak_rss_bytes / (1024 ** 3), 4),
            "os_peak_rss_bytes": rss.os_peak_rss_bytes,
            "os_peak_rss_gib": round(rss.os_peak_rss_bytes / (1024 ** 3), 4),
            "sampled_peak_rss_bytes": rss.sampled_peak_rss_bytes,
            "sampled_peak_rss_gib": round(rss.sampled_peak_rss_bytes / (1024 ** 3), 4),
            "rss_samples": rss.samples,
            "sample_interval_s": RSS_SAMPLE_INTERVAL_S,
            "note": "peak_rss = max(os high-water mark, sampled peak). os_peak from ru_maxrss/VmHWM.",
        },
        "herbie_member_kwargs": {
            "control_kwarg": member_info["control_member_kwarg"],
            "control_rendered": member_info["control_rendered"],
            "m01_kwarg": member_info["m01_member_kwarg"],
            "m01_rendered": member_info["m01_rendered"],
            "note": "kwarg is the value passed to Herbie(member=...); rendered is the file token GEFS uses.",
        },
        "failures": failed[:50],
        "failure_count": len(failed),
    }

    results_dir = SPIKE_ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_json = results_dir / f"phase3_sizing_spike_{run_id}.json"
    out_json.write_text(json.dumps(measurement, indent=2))

    # --- Console summary ---
    print("\n" + "=" * 72)
    print("PHASE 3 SIZING SPIKE — SUMMARY")
    print("=" * 72)
    print(f"run_id              : {run_id}")
    print(f"members             : {member_info['total_member_count']} "
          f"(control + {member_info['perturbation_count']} perturbations)")
    print(f"forecast hours      : {len(fhs)} (max fh {fhs[-1]})")
    print(f"files written       : {file_count} / {len(work)} attempted "
          f"({len(failed)} failed)")
    print(f"total disk          : {measurement['disk']['total_gib']} GiB "
          f"({total_bytes:,} bytes)")
    print(f"per-file size       : avg {avg_bytes:,} B / max {max_bytes:,} B")
    print(f"inodes (files)      : {file_count}")
    print(f"build wall-clock    : {measurement['latency']['build_wall_clock_min']} min "
          f"({build_elapsed_s:.1f}s) @ {WORKERS} workers")
    print(f"avg per (member,fh) : {per_unit_ms:.1f} ms")
    print(f"peak RSS            : {measurement['memory']['peak_rss_gib']} GiB")
    print(f"control member kwarg: {member_info['control_member_kwarg']!r} -> {member_info['control_rendered']!r}")
    print(f"m01 member kwarg    : {member_info['m01_member_kwarg']!r} -> {member_info['m01_rendered']!r}")
    print("-" * 72)
    print(f"measurement JSON    : {out_json}")
    print(f"test COGs under     : {SPIKE_ROOT / 'staging' / MODEL_ID / run_id}")
    print(f"cleanup             : rm -rf {SPIKE_ROOT}")
    print("=" * 72)
    if failed:
        print(f"\nNOTE: {len(failed)} unit(s) failed — see 'failures' in the JSON. "
              f"Disk/inode/latency reflect successful units only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
