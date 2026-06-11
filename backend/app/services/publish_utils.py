from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .run_ids import format_run_id, parse_run_id_datetime
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
            generated_at = sidecar.get("generated_at")
            if isinstance(generated_at, str) and generated_at:
                frame_entry["generated_at"] = generated_at
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


def extract_herbie_cache_run_id(path: Path, *, model_root: Path) -> str | None:
    try:
        relative = path.relative_to(model_root)
    except ValueError:
        return None
    if len(relative.parts) < 2:
        return None

    day_token = next((part for part in relative.parts[:-1] if re.fullmatch(r"\d{8}", part)), None)
    if day_token is None:
        return None

    name = path.name.lower()
    if name.endswith(".lock"):
        name = name[:-5]

    timestamp_match = re.search(r"(?P<stamp>\d{14})-\d+h-", name)
    if timestamp_match is not None:
        stamp = timestamp_match.group("stamp")
        try:
            return format_run_id(
                datetime(
                    int(stamp[0:4]),
                    int(stamp[4:6]),
                    int(stamp[6:8]),
                    int(stamp[8:10]),
                    int(stamp[10:12]),
                    tzinfo=timezone.utc,
                )
            )
        except ValueError:
            return None

    match = re.search(r"t(?P<hour>\d{2})(?P<minute>\d{2})?z", name)
    if match is None:
        return None
    try:
        return format_run_id(
            datetime(
                int(day_token[0:4]),
                int(day_token[4:6]),
                int(day_token[6:8]),
                int(match.group("hour")),
                int(match.group("minute") or 0),
                tzinfo=timezone.utc,
            )
        )
    except ValueError:
        return None


def prune_empty_dirs(root: Path) -> None:
    if not root.is_dir():
        return
    for child in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        try:
            child.rmdir()
        except OSError:
            continue


def enforce_herbie_cache_retention(root: Path, model_id: str, keep_runs: int) -> None:
    if keep_runs < 1:
        return

    normalized_model_id = str(model_id).strip().lower()
    model_roots = (
        [
            child
            for child in root.iterdir()
            if child.is_dir() and child.name.strip().lower() == normalized_model_id
        ]
        if root.is_dir() and normalized_model_id
        else []
    )
    if not model_roots:
        return

    for model_root in model_roots:
        run_files: dict[str, list[Path]] = {}
        for path in model_root.rglob("*"):
            if not path.is_file():
                continue
            run_id = extract_herbie_cache_run_id(path, model_root=model_root)
            if run_id is None:
                continue
            run_files.setdefault(run_id, []).append(path)

        if len(run_files) <= keep_runs:
            continue

        sorted_runs = sorted(
            run_files,
            key=lambda run_id: parse_run_id_datetime(run_id) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for run_id in sorted_runs[keep_runs:]:
            for path in run_files.get(run_id, []):
                logger.info("Removing old Herbie cache file: %s", path)
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    logger.warning("Failed removing old Herbie cache file: %s", path)
        prune_empty_dirs(model_root)
