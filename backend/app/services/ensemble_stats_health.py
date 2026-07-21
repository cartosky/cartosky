from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


ENSEMBLE_STATS_HEALTH_CONTRACT_VERSION = "1.1"
ENSEMBLE_STATS_ALERT_AFTER_PASSES = 3


def ensemble_stats_health_path(
    data_root: Path,
    model_id: str,
    run_id: str,
) -> Path:
    return (
        Path(data_root)
        / "status"
        / "ensemble_stats"
        / str(model_id)
        / f"{run_id}.json"
    )


def load_ensemble_stats_health(
    data_root: Path,
    model_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    path = ensemble_stats_health_path(data_root, model_id, run_id)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    units = payload.get("units")
    if not isinstance(units, list):
        return None
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _unit_key(unit: dict[str, Any]) -> tuple[str, int]:
    return str(unit.get("base_var") or ""), int(unit.get("forecast_hour") or 0)


def update_ensemble_stats_health(
    *,
    data_root: Path,
    model_id: str,
    run_id: str,
    incomplete_units: list[dict[str, Any]],
    pass_complete: bool,
    now_ts: int | None = None,
) -> dict[str, Any] | None:
    """Persist consecutive incomplete-unit observations for one stats run.

    A complete stats pass clears units it no longer observes. A preempted pass
    leaves prior state unchanged because it did not evaluate the full plan.
    """
    observed_at = int(time.time()) if now_ts is None else int(now_ts)
    path = ensemble_stats_health_path(data_root, model_id, run_id)
    previous = load_ensemble_stats_health(data_root, model_id, run_id) or {}
    if not pass_complete:
        return previous or None
    previous_units = {
        _unit_key(unit): unit
        for unit in previous.get("units", [])
        if isinstance(unit, dict) and str(unit.get("base_var") or "").strip()
    }
    next_units: dict[tuple[str, int], dict[str, Any]] = {}

    for observed in incomplete_units:
        key = _unit_key(observed)
        if not key[0]:
            continue
        prior = previous_units.get(key, {})
        consecutive_passes = int(prior.get("consecutive_passes") or 0) + 1
        missing_members = sorted(
            {
                str(member)
                for member in observed.get("missing_members", [])
                if str(member).strip()
            }
        )
        failure_statuses = sorted(
            {
                str(status)
                for status in observed.get("failure_statuses", [])
                if str(status).strip()
            }
        )
        next_units[key] = {
            "base_var": key[0],
            "forecast_hour": key[1],
            "missing_members": missing_members,
            "failure_statuses": failure_statuses,
            "consecutive_passes": consecutive_passes,
            "first_seen_at": int(prior.get("first_seen_at") or observed_at),
            "last_seen_at": observed_at,
            "alerting": consecutive_passes >= ENSEMBLE_STATS_ALERT_AFTER_PASSES,
        }

    if not next_units:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return None

    payload: dict[str, Any] = {
        "contract_version": ENSEMBLE_STATS_HEALTH_CONTRACT_VERSION,
        "model_id": str(model_id),
        "run_id": str(run_id),
        "updated_at": observed_at,
        "alert_after_passes": ENSEMBLE_STATS_ALERT_AFTER_PASSES,
        "units": [next_units[key] for key in sorted(next_units)],
    }
    _write_json_atomic(path, payload)
    return payload
