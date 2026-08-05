"""Fast-path run promotion: LATEST advances through the SHARED gates (WP3).

Design §3 rule 4 is what makes this safe: the run manifest is per-variable
incremental (``available_frames`` / ``ready_through_fh``), so a run whose
Kuchera and upper-air variables are still building reads to the viewer exactly
like today's catch-up window. There is therefore no fast-specific promotion
criterion — the fast run is gated by ``_promotion_ready_regions`` over the
scheduler's own primary variables, the same function the delayed loop uses.

These tests run on the canonical ``na`` domain, because that is the only ECMWF
domain ``_promotion_ready_regions`` considers with the global-domain flag off,
and a LATEST pointer only exists for a declared domain.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.registry import MODEL_REGISTRY
from app.services import scheduler as scheduler_module
from app.services.fastpath import ownership
from app.services.fastpath import subloop as subloop_module

from tests.test_fastpath_subloop import RUN_DT, RUN_ID, FakeSource, _LAUNCH_VARS

PRIMARY_VARS = ["tmp2m"]


def _plugin():
    return MODEL_REGISTRY.get("ecmwf")


def _na_only(monkeypatch: pytest.MonkeyPatch, *, fast_vars: tuple[str, ...]) -> None:
    """Pin ownership to ``na`` for ``fast_vars`` and delayed for everything else."""
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    overrides = [f"ecmwf:{var_id}:global=delayed" for var_id in _LAUNCH_VARS]
    overrides += [
        f"ecmwf:{var_id}:na=delayed" for var_id in _LAUNCH_VARS if var_id not in fast_vars
    ]
    monkeypatch.setenv(ownership.ENV_FASTPATH_VAR_OVERRIDES, ",".join(overrides))


def _latest(data_root: Path, run_region: str = "na") -> dict | None:
    path = scheduler_module._latest_pointer_path(data_root, "ecmwf", region=run_region)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _run_pass(data_root: Path, source: FakeSource, *, run_dt=RUN_DT, **kwargs):
    return subloop_module.run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=data_root,
        run_dt=run_dt,
        source=source,
        primary_vars=PRIMARY_VARS,
        **kwargs,
    )


def _delayed_publish(data_root: Path, run_id: str, *, targets=None) -> list[str]:
    """What ``_process_run``'s ``_publish_run_snapshot`` closure does, verbatim."""
    plugin = _plugin()
    cycle_hour = int(run_id.split("_")[1].rstrip("z"))
    return scheduler_module._publish_run_snapshot_for(
        data_root=data_root,
        model_id="ecmwf",
        run_id=run_id,
        plugin=plugin,
        primary_vars=PRIMARY_VARS,
        promotion_fhs=scheduler_module._resolve_promotion_fhs(
            plugin, PRIMARY_VARS, cycle_hour
        ),
        targets=targets if targets is not None else [("na", "tmp2m", 0)],
        reason="delayed_catchup",
        domains=["na"],
        canonical_region="na",
        strict_canonical=True,
    )


# ---------------------------------------------------------------------------
# Promotion criteria
# ---------------------------------------------------------------------------


def test_fast_only_run_reaches_latest_when_criteria_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _na_only(monkeypatch, fast_vars=("tmp2m",))
    result = _run_pass(tmp_path, FakeSource(max_fh=0))

    assert not result.stalled, result.stall_reasons
    assert result.frames_written == 1
    assert "na" in result.promotion_ready_regions

    pointer = _latest(tmp_path)
    assert pointer is not None, "a fast-built run must be able to become latest"
    assert pointer["run_id"] == RUN_ID
    assert pointer["region"] == "na"
    # And the promotion decision came from the shared gate, not a fast-path one.
    assert scheduler_module._should_promote(
        tmp_path, "ecmwf", RUN_ID, PRIMARY_VARS, [0, 3, 6]
    )


def test_fast_run_does_not_promote_before_criteria_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frames still promote to the published tree every timestep — but LATEST
    stays put until the PRIMARY variable satisfies the existing gate."""
    _na_only(monkeypatch, fast_vars=("dp2m",))
    result = _run_pass(tmp_path, FakeSource(max_fh=0))

    assert result.frames_written == 1
    assert result.promotion_ready_regions == ()
    assert _latest(tmp_path) is None

    # The frame itself did reach the published tree.
    published = scheduler_module._published_model_root(tmp_path, "ecmwf", "na") / RUN_ID
    assert (published / "dp2m" / "fh000.json").is_file()
    assert not scheduler_module._should_promote(
        tmp_path, "ecmwf", RUN_ID, PRIMARY_VARS, [0, 3, 6]
    )


def test_partial_run_manifest_explains_the_still_building_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design §3 rule 4: promoting a partial run is safe because readiness is
    per-variable in the manifest the viewer already reads."""
    _na_only(monkeypatch, fast_vars=("tmp2m",))
    _run_pass(tmp_path, FakeSource(max_fh=0))

    manifest = json.loads(
        scheduler_module._manifest_path(tmp_path, "ecmwf", RUN_ID, region="na").read_text()
    )
    entry = manifest["variables"]["tmp2m"]
    assert entry["ready_through_fh"] == 0
    assert entry["available_frames"] == 1
    assert entry["expected_frames"] > 1, "the rest of the run is visibly still building"
    # Nothing was fabricated for the delayed-owned variables.
    assert "tmp850" not in manifest["variables"]


# ---------------------------------------------------------------------------
# Delayed arrival on an already-promoted fast run
# ---------------------------------------------------------------------------


def test_delayed_arrival_on_a_promoted_fast_run_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _na_only(monkeypatch, fast_vars=("tmp2m",))
    _run_pass(tmp_path, FakeSource(max_fh=0))
    before = _latest(tmp_path)
    assert before is not None

    ready = _delayed_publish(tmp_path, RUN_ID)

    after = _latest(tmp_path)
    assert ready == ["na"]
    assert after["run_id"] == before["run_id"] == RUN_ID
    assert after["region"] == "na"
    # The fast frames survive the delayed publish untouched.
    published = scheduler_module._published_model_root(tmp_path, "ecmwf", "na") / RUN_ID
    sidecar = json.loads((published / "tmp2m" / "fh000.json").read_text())
    assert sidecar["source_id"] == "openmeteo"


def test_delayed_publish_of_an_older_run_never_walks_latest_backwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fast poller sees a run ~2 h before the delayed probe does, so the
    delayed loop can still be finishing run N while LATEST already points at
    the fast-built N+1. That must not regress users a cycle."""
    _na_only(monkeypatch, fast_vars=("tmp2m",))
    older_dt = RUN_DT - timedelta(hours=12)
    older_id = scheduler_module._run_id_from_dt(older_dt)

    class _OlderSource(FakeSource):
        @staticmethod
        def _fh_from_url(url: str) -> int:
            stem = url.rsplit("/", 1)[-1].removesuffix(".om")
            valid = datetime.strptime(stem, "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
            return int((valid - older_dt).total_seconds() // 3600)

    _run_pass(tmp_path, _OlderSource(max_fh=0), run_dt=older_dt)
    _run_pass(tmp_path, FakeSource(max_fh=0))
    assert _latest(tmp_path)["run_id"] == RUN_ID

    _delayed_publish(tmp_path, older_id, targets=[("na", "tmp2m", 0)])

    assert _latest(tmp_path)["run_id"] == RUN_ID, "LATEST must not move backwards"
    # The older run was still promoted — only the pointer was withheld.
    older_published = (
        scheduler_module._published_model_root(tmp_path, "ecmwf", "na") / older_id
    )
    assert (older_published / "tmp2m" / "fh000.json").is_file()


def test_a_fully_revoked_run_still_promotes_via_the_delayed_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failover interaction: once every fast pair is revoked the run's
    promotion is entirely the delayed loop's business, and nothing the fast
    path left behind blocks it."""
    _na_only(monkeypatch, fast_vars=("tmp2m",))
    _run_pass(tmp_path, FakeSource(max_fh=0))

    revoked = subloop_module.enforce_failover_deadline(
        plugin=_plugin(),
        model_id="ecmwf",
        run_dt=RUN_DT,
        data_root=tmp_path,
        delayed_available=True,
    )
    assert ("tmp2m", "na") in revoked

    # The delayed builder now owns every pair...
    targets = scheduler_module._delayed_targets_for_cycle(
        _plugin(), ["tmp2m"], RUN_DT.hour, data_root=tmp_path, run_id=RUN_ID
    )
    assert ("na", "tmp2m", 0) in targets

    # ...and its own publish promotes the run normally.
    ready = _delayed_publish(tmp_path, RUN_ID, targets=targets)
    assert ready == ["na"]
    assert _latest(tmp_path)["run_id"] == RUN_ID


def test_promotion_hook_is_inert_with_the_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ownership.ENV_FASTPATH_MODELS, raising=False)
    result = subloop_module.run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=tmp_path,
        run_dt=RUN_DT,
        source=FakeSource(max_fh=0),
    )
    assert result.frames_written == 0
    assert result.promotion_ready_regions == ()
    assert _latest(tmp_path) is None
