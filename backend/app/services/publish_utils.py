from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .run_ids import parse_run_id_datetime

logger = logging.getLogger(__name__)

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

    shutil.copytree(stage_run, tmp_run, copy_function=os.link)

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

            full_capability_catalog = getattr(getattr(plugin, "capabilities", None), "variable_catalog", {}) or {}
            raw_capability = full_capability_catalog.get(var_id) if isinstance(full_capability_catalog, dict) else None
            raw_frontend = getattr(raw_capability, "frontend", {}) if raw_capability is not None else {}
            if isinstance(raw_frontend, dict) and bool(raw_frontend.get("internal_only")):
                continue

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
