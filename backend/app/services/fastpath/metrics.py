"""Fast-path ops metrics: scheduler → JSON snapshot → Prometheus (design §8, WP4).

Why a JSON handoff
==================

The scheduler cannot register Prometheus gauges of its own. It runs as its own
per-model systemd unit (``csky-{model}-scheduler``) with no HTTP server, while
Prometheus scrapes two endpoints: the standalone serving canary on ``:9105``
(``backend/scripts/serving_canary.py``, which registers ``cartosky_canary_*``
directly because it *is* the exporter process) and the API on ``:8200``.

So this follows the pattern the scheduler already uses for its Herbie fetch
runtime metrics: write an atomic JSON snapshot under
``{data_root}/status/`` and let the API turn it into gauges on its next
``/metrics`` scrape (``admin_telemetry.write_fetch_runtime_snapshot`` →
``main._refresh_prometheus_gauges`` → ``prometheus_metrics.replace_*``). No new
export infrastructure — the same three-step path, one more file.

What is exported and why
========================

``fastpath_ready_through_fh{model,var,domain}``
    Per-pair publish frontier. The product-visible number: how far into the run
    a fast-owned variable is actually usable.
``fastpath_stall_count{model,run}``
    Increments on a failed LIST/fetch, an unusable accumulation step, a refused
    ledger ingest, and on every failover revocation.
``fastpath_blocked_pairs{model}``
    **The critical alert.** A blocked pair is revoked from the fast source *and*
    withheld from the delayed one, because its accumulation frames could not be
    verifiably cleared and a delayed rebuild would silently mix sources
    (``subloop._purge_fast_frames``). Nobody builds it until an operator
    intervenes. Any value > 0 needs a human, which is exactly why it must be a
    metric and not only the ``fastpath_blocked`` log line. Counts **unique**
    pairs across retained runs: one pair blocked over three cycles is one
    outage, and counting occurrences would make any alert threshold drift with
    retention depth. Per-run detail stays in ``blocked_pairs_by_run``.
``fastpath_promotion_retries_pending{model}``
    Timesteps staged but not published. Sustained non-zero means the promotion
    path is failing and the published tree is falling behind staging.
``fastpath_canary_{bias,mae,synoptic_mae,corr}{model,var,domain}``
``fastpath_canary_failed{model,var,domain}``
    The §6 canary's per-run verdict, so drift is visible as a trend rather than
    only as a log line at the moment it crosses a threshold.
``fastpath_canary_seam_failures{model,var,domain}``
    The §8 failover-seam continuity verdict. A failed check means an
    accumulation series was spliced from two sources — the condition
    ``enforce_failover_deadline``'s purge-then-hand-over exists to prevent — so
    it pages like any other canary failure rather than living only in the log.
    Published only for seams that were actually continuity-checked.

Snapshot scope
==============

The snapshot aggregates **every retained run's** fast-path state, not just the
run the current pass touched. That matters for the alert-worthy conditions: a
blocked pair is recorded against the run that failed over, which by the time
anyone looks is a cycle or two behind whatever the bucket is publishing now. A
per-run-only snapshot would drop exactly the signal an operator needs.

Flag gating: :func:`write_pass_snapshot` returns ``None`` when the model is not
in ``CARTOSKY_FASTPATH_MODELS``, and no snapshot file is ever created, so the
API-side loader finds nothing and registers no series.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ownership import fastpath_enabled
from .state import iter_run_states

logger = logging.getLogger(__name__)

#: Bumped when the payload shape changes. The loader ignores other versions,
#: so a rolling deploy never mixes shapes.
FASTPATH_SNAPSHOT_SCHEMA_VERSION = 1

#: Snapshots older than this are ignored by the loader: a stopped scheduler
#: must stop publishing gauges rather than pin them at their last value forever.
DEFAULT_SNAPSHOT_MAX_AGE_SECONDS = 24 * 60 * 60

_MODEL_TOKEN_RE = re.compile(r"^[a-z0-9_]+$")

__all__ = [
    "DEFAULT_SNAPSHOT_MAX_AGE_SECONDS",
    "FASTPATH_SNAPSHOT_SCHEMA_VERSION",
    "build_snapshot",
    "fastpath_snapshot_dir",
    "fastpath_snapshot_path",
    "load_fastpath_snapshots",
    "write_pass_snapshot",
]


def _model_token(model_id: Any) -> str | None:
    token = str(model_id or "").strip().lower()
    return token if _MODEL_TOKEN_RE.fullmatch(token) else None


def fastpath_snapshot_dir(data_root: Path | str) -> Path:
    return Path(data_root) / "status" / "fastpath"


def fastpath_snapshot_path(data_root: Path | str, model_id: str) -> Path:
    token = _model_token(model_id)
    if token is None:
        raise ValueError(f"invalid model id for a fastpath snapshot: {model_id!r}")
    return fastpath_snapshot_dir(data_root) / f"{token}.json"


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def build_snapshot(
    *,
    data_root: Path | str,
    model_id: str,
    pass_result: Any | None = None,
    canary: Any | None = None,
    recorded_at: float | None = None,
) -> dict[str, Any]:
    """Aggregate the fast-path namespace into one exportable payload.

    ``pass_result`` is the :class:`~.subloop.FastpathPassResult` the current
    pass returned (optional — a pass that never ran still has state worth
    exporting), ``canary`` a :class:`~.canary.CanaryRun` if one just completed.
    Canary values for *earlier* runs come from their state files, so the gauges
    keep the last verdict rather than dropping to nothing between cycles.
    """
    token = _model_token(model_id) or str(model_id)
    ready: list[dict[str, Any]] = []
    #: Unique pairs — a pair blocked across several retained runs is ONE
    #: outage, not one per run. Counting occurrences would make the alert
    #: threshold drift with retention depth.
    blocked: set[tuple[str, str]] = set()
    blocked_by_run: dict[str, list[list[str]]] = {}
    stall_by_run: dict[str, int] = {}
    canary_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    seam_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    canary_runs: list[dict[str, Any]] = []

    states = list(iter_run_states(data_root, token))  # newest run id first
    for state in states:
        stall_by_run[state.run_id] = int(state.stall_count)
        for key, entry in sorted(state.pairs.items()):
            var_id, _, domain = key.partition("|")
            if entry.blocked:
                blocked.add((var_id, domain))
                blocked_by_run.setdefault(state.run_id, []).append([var_id, domain])
        recorded = state.canary
        if isinstance(recorded, dict) and recorded:
            canary_runs.append(
                {
                    "run": state.run_id,
                    "status": str(recorded.get("status") or ""),
                    "completed_at": str(recorded.get("completed_at") or ""),
                }
            )
            for result in recorded.get("results") or ():
                if not isinstance(result, dict):
                    continue
                pair = (str(result.get("var") or ""), str(result.get("domain") or ""))
                # Newest run first (iter_run_states is reverse-sorted), so the
                # first entry seen for a pair is the freshest verdict.
                canary_by_pair.setdefault(pair, {**result, "run": state.run_id})
            # Seam audits are canary verdicts too: a failed continuity check
            # means two sources were spliced into one accumulation series, and
            # it has to reach a gauge rather than only the structured log.
            for seam in recorded.get("seams") or ():
                if not isinstance(seam, dict):
                    continue
                pair = (str(seam.get("var") or ""), str(seam.get("domain") or ""))
                seam_by_pair.setdefault(pair, {**seam, "run": state.run_id})

    # ready_through_fh comes from the CURRENT run only: a stale run's frontier
    # is not a statement about the model's present state, and exporting several
    # runs under the same label set would just fight over one series.
    current_run = getattr(pass_result, "run_id", None)
    if current_run:
        for state in states:
            if state.run_id != current_run:
                continue
            for key, entry in sorted(state.pairs.items()):
                var_id, _, domain = key.partition("|")
                ready.append(
                    {
                        "var": var_id,
                        "domain": domain,
                        "ready_through_fh": (
                            None
                            if entry.ready_through_fh is None
                            else int(entry.ready_through_fh)
                        ),
                    }
                )
            break

    if canary is not None:
        for result in getattr(canary, "results", ()):  # freshest of all
            payload = result.to_json()
            canary_by_pair[(payload["var"], payload["domain"])] = {
                **payload,
                "run": getattr(canary, "run_id", ""),
            }
        for seam in getattr(canary, "seams", ()):
            payload = seam.to_json()
            seam_by_pair[(payload["var"], payload["domain"])] = {
                **payload,
                "run": getattr(canary, "run_id", ""),
            }
        canary_runs.insert(
            0,
            {
                "run": str(getattr(canary, "run_id", "")),
                "status": str(getattr(canary, "status", "")),
                "completed_at": str(getattr(canary, "completed_at", "")),
            },
        )

    return {
        "schema_version": FASTPATH_SNAPSHOT_SCHEMA_VERSION,
        "model_id": token,
        "recorded_at": float(time.time() if recorded_at is None else recorded_at),
        "run": str(current_run or ""),
        "ready_through_fh": ready,
        "stall_count_by_run": stall_by_run,
        "blocked_pairs": [list(pair) for pair in sorted(blocked)],
        "blocked_pair_count": len(blocked),
        "blocked_pairs_by_run": {
            run_id: sorted(pairs) for run_id, pairs in sorted(blocked_by_run.items())
        },
        "promotion_retries_pending": int(
            getattr(pass_result, "promotion_retries_pending", 0) or 0
        ),
        "canary_results": [
            value for _key, value in sorted(canary_by_pair.items())
        ],
        "canary_seams": [value for _key, value in sorted(seam_by_pair.items())],
        "canary_runs": canary_runs[:8],
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def write_pass_snapshot(
    *,
    data_root: Path | str,
    model_id: str,
    pass_result: Any | None = None,
    canary: Any | None = None,
) -> Path | None:
    """Write the model's fast-path metrics snapshot. Never raises.

    Returns the path written, or ``None`` when the fast path is off for this
    model or the write failed (in which case the scheduler carries on — a
    missing metric must not stop a build).
    """
    if not fastpath_enabled(model_id):
        return None
    try:
        payload = build_snapshot(
            data_root=data_root,
            model_id=model_id,
            pass_result=pass_result,
            canary=canary,
        )
        return _write_atomic(fastpath_snapshot_path(data_root, model_id), payload)
    except Exception:
        logger.exception(
            "fastpath: could not write the metrics snapshot for model=%s", model_id
        )
        return None


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    return path


# ---------------------------------------------------------------------------
# Loading (API side)
# ---------------------------------------------------------------------------


def load_fastpath_snapshots(
    *,
    data_root: Path | str,
    now: float | None = None,
    max_age_seconds: float = DEFAULT_SNAPSHOT_MAX_AGE_SECONDS,
) -> list[dict[str, Any]]:
    """Every fresh fast-path snapshot, for the API's Prometheus refresh.

    Mirrors ``admin_telemetry.load_fetch_runtime_snapshots``: unknown schema
    versions, filename/payload model mismatches, future timestamps and stale
    files are all dropped rather than exported. Deliberately import-light and
    flag-independent — the API does not know or care whether any scheduler has
    the fast path enabled; it just finds no files when none do.
    """
    current_time = float(time.time() if now is None else now)
    rows: list[dict[str, Any]] = []
    directory = fastpath_snapshot_dir(data_root)
    if not directory.is_dir():
        return rows
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if int(payload.get("schema_version") or 0) != FASTPATH_SNAPSHOT_SCHEMA_VERSION:
            continue
        model_id = _model_token(payload.get("model_id"))
        if model_id is None or model_id != path.stem:
            continue
        try:
            recorded_at = float(payload.get("recorded_at"))
        except (TypeError, ValueError):
            continue
        if recorded_at > current_time + 60:
            continue
        if current_time - recorded_at > max(0.0, float(max_age_seconds)):
            continue
        rows.append(payload)
    return rows


def blocked_pairs_from_snapshots(rows: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    """Flattened ``[model, var, domain]`` triples — the page-worthy condition."""
    out: list[list[str]] = []
    for row in rows:
        model_id = str(row.get("model_id") or "")
        for pair in row.get("blocked_pairs") or ():
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                out.append([model_id, str(pair[0]), str(pair[1])])
    return out
