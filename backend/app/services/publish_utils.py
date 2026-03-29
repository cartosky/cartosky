from __future__ import annotations

import concurrent.futures
import json
import logging
import shutil
from datetime import datetime, timezone
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .builder.loop_webp import convert_rgba_cog_to_loop_webp
from .run_ids import parse_run_id_datetime

logger = logging.getLogger(__name__)

DEFAULT_LOOP_WEBP_QUALITY = 82
DEFAULT_LOOP_WEBP_MAX_DIM = 1600
DEFAULT_LOOP_WEBP_TIER1_QUALITY = 86
DEFAULT_LOOP_WEBP_TIER1_MAX_DIM = 2400
DEFAULT_LOOP_WEBP_TIER0_FIXED_W = 1600
DEFAULT_LOOP_WEBP_TIER1_FIXED_W = 2400


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    tmp.replace(path)


def write_latest_pointer(
    *,
    data_root: Path,
    model: str,
    run_id: str,
    source: str = "scheduler_v3",
) -> None:
    run_dt = parse_run_id_datetime(run_id)
    if run_dt is None:
        raise ValueError(f"Cannot write LATEST.json for invalid run_id={run_id!r}")
    payload = {
        "run_id": run_id,
        "cycle_utc": run_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
    }
    latest_path = data_root / "published" / model / "LATEST.json"
    write_json_atomic(latest_path, payload)


def promote_run(*, data_root: Path, model: str, run_id: str) -> None:
    stage_run = data_root / "staging" / model / run_id
    if not stage_run.is_dir():
        raise ValueError(f"Cannot promote missing staging run dir: {stage_run}")

    published_model = data_root / "published" / model
    published_model.mkdir(parents=True, exist_ok=True)

    published_run = published_model / run_id
    tmp_run = published_model / f".{run_id}.tmp"

    if tmp_run.exists():
        shutil.rmtree(tmp_run, ignore_errors=True)
    if tmp_run.exists():
        raise ValueError(f"Cannot clear temporary promotion dir: {tmp_run}")

    shutil.copytree(stage_run, tmp_run)

    if published_run.exists():
        shutil.rmtree(published_run, ignore_errors=True)
    if published_run.exists():
        raise ValueError(f"Cannot clear existing published run dir: {published_run}")

    shutil.move(str(tmp_run), str(published_run))


def write_run_manifest(
    *,
    data_root: Path,
    model: str,
    run_id: str,
    targets: list[tuple[str, int]],
    plugin: Any | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    run_dt = parse_run_id_datetime(run_id)
    if run_dt is None:
        raise ValueError(f"Invalid run id for manifest: {run_id}")

    expected_by_var: dict[str, list[int]] = {}
    for var_id, fh in targets:
        expected_by_var.setdefault(var_id, []).append(int(fh))

    variables: dict[str, dict[str, Any]] = {}
    for var_id, fhs in sorted(expected_by_var.items()):
        expected_fhs = sorted(set(fhs))
        frames: list[dict[str, Any]] = []
        units = ""
        kind = ""
        display_name = var_id

        if plugin is not None:
            capability = plugin.get_var_capability(var_id) if hasattr(plugin, "get_var_capability") else None
            if capability is not None and getattr(capability, "name", None):
                display_name = str(getattr(capability, "name"))
            else:
                var_spec = plugin.get_var(var_id) if hasattr(plugin, "get_var") else None
                if var_spec is not None and getattr(var_spec, "name", None):
                    display_name = str(getattr(var_spec, "name"))

        for fh in expected_fhs:
            sidecar_path = data_root / "published" / model / run_id / var_id / f"fh{fh:03d}.json"
            if not sidecar_path.exists():
                continue
            try:
                sidecar = json.loads(sidecar_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue

            if not units:
                units = str(sidecar.get("units", ""))
            if not kind:
                kind = str(sidecar.get("kind", ""))

            valid_time = sidecar.get("valid_time")
            frame_entry: dict[str, Any] = {"fh": fh}
            if isinstance(valid_time, str) and valid_time:
                frame_entry["valid_time"] = valid_time
            frames.append(frame_entry)

        variables[var_id] = {
            "display_name": display_name,
            "kind": kind,
            "units": units,
            "expected_frames": len(expected_fhs),
            "available_frames": len(frames),
            "frames": sorted(frames, key=lambda item: item["fh"]),
        }

    payload: dict[str, Any] = {
        "contract_version": "3.0",
        "model": model,
        "run": run_id,
        "variables": variables,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if metadata:
        payload["metadata"] = dict(metadata)

    manifest_path = data_root / "manifests" / model / f"{run_id}.json"
    write_json_atomic(manifest_path, payload)


def pregenerate_loop_webp_for_run(
    *,
    data_root: Path,
    model: str,
    run_id: str,
    loop_cache_root: Path,
    workers: int,
    tier0_quality: int,
    tier0_max_dim: int,
    tier0_fixed_w: int,
    tier1_quality: int | None = None,
    tier1_max_dim: int | None = None,
    tier1_fixed_w: int | None = None,
    variables: Iterable[str] | None = None,
    forecast_hours: Iterable[int] | None = None,
    tiers: Iterable[int] | None = None,
) -> tuple[int, int]:
    published_run = data_root / "published" / model / run_id
    if not published_run.is_dir():
        return 0, 0

    allowed_variables = {str(item).strip().lower() for item in (variables or []) if str(item).strip()}
    allowed_fhs = {int(item) for item in (forecast_hours or [])}
    allowed_tiers = {int(item) for item in (tiers or [])}
    # Tier 1 remains legacy-only. Active pre-generation now writes tier 0 only.
    tier_specs = ((0, int(tier0_quality), int(tier0_max_dim), int(tier0_fixed_w)),)

    jobs: list[tuple[str, Path, Path | None, Path, int, int, int, int]] = []
    for var_dir in sorted([path for path in published_run.iterdir() if path.is_dir()]):
        variable = var_dir.name.strip().lower()
        if allowed_variables and variable not in allowed_variables:
            continue
        for cog_path in sorted(var_dir.glob("fh*.rgba.cog.tif")):
            fh_token = cog_path.name.split(".")[0]
            fh = int(fh_token.removeprefix("fh"))
            if allowed_fhs and fh not in allowed_fhs:
                continue
            value_cog_path = var_dir / f"{fh_token}.val.cog.tif"
            for tier, quality, max_dim, fixed_w in tier_specs:
                if allowed_tiers and tier not in allowed_tiers:
                    continue
                out_path = loop_cache_root / model / run_id / variable / f"tier{tier}" / f"{fh_token}.loop.webp"
                if out_path.is_file():
                    continue
                jobs.append((variable, cog_path, value_cog_path, out_path, quality, max_dim, fixed_w, tier))

    if not jobs:
        return 0, 0

    ok = 0
    fail = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [
            pool.submit(
                convert_rgba_cog_to_loop_webp,
                model_id=model,
                run_id=run_id,
                var_key=variable,
                cog_path=cog_path,
                value_cog_path=value_cog_path,
                out_path=out_path,
                quality=quality,
                max_dim=max_dim,
                fixed_width=fixed_w,
                tier=tier,
            )
            for variable, cog_path, value_cog_path, out_path, quality, max_dim, fixed_w, tier in jobs
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                converted, _ = future.result()
            except Exception:
                converted = False
            if converted:
                ok += 1
            else:
                fail += 1
    return ok, fail


def enforce_run_artifact_retention(root: Path, keep_runs: int) -> None:
    if keep_runs < 1 or not root.exists():
        return

    run_entries: list[tuple[datetime, Path]] = []
    for child in root.iterdir():
        if child.name.startswith("."):
            continue
        run_id = child.stem if child.is_file() else child.name
        run_dt = parse_run_id_datetime(run_id)
        if run_dt is None:
            continue
        run_entries.append((run_dt, child))

    if len(run_entries) <= keep_runs:
        return

    run_entries.sort(key=lambda pair: pair[0], reverse=True)
    for _, old_path in run_entries[keep_runs:]:
        logger.info("Removing old run artifact: %s", old_path)
        if old_path.is_dir():
            shutil.rmtree(old_path, ignore_errors=True)
        else:
            old_path.unlink(missing_ok=True)
