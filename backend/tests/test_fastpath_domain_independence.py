"""Ownership is keyed ``(variable, domain)``, and the two are independent.

Design §3 makes this a required contract test:

    "A contract test must prove a var can be fast-owned in ``na`` while
    delayed-owned in ``global`` (independent readiness, manifests, canary)."

It is the reason ownership is keyed on the pair rather than on the variable
alone [Codex finding 2]: the scheduler expands every selected variable across
all declared build domains, so variable-only ownership could not express "NA
fast, global delayed" at all — and failover could not flip one domain without
flipping the other.

``tmp2m`` is fast-owned in ``na`` and delayed-owned in ``global`` here; every
other variable is delayed everywhere, which keeps this to two 1825×1893 frames.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.registry import MODEL_REGISTRY
from app.services import scheduler as scheduler_module
from app.services.fastpath import canary as canary_module
from app.services.fastpath import ownership
from app.services.fastpath import subloop as subloop_module
from app.services.fastpath.state import FastpathRunState

from tests.test_fastpath_canary import FakeReferenceSource
from tests.test_fastpath_subloop import (
    RUN_DT,
    RUN_ID,
    FakeSource,
    _LAUNCH_VARS,
    _frame_exists,
    _sidecar,
)

SPLIT_VAR = "tmp2m"


@pytest.fixture(autouse=True)
def _split_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    """``tmp2m`` fast in ``na``, delayed in ``global``; everything else delayed."""
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "ecmwf")
    monkeypatch.setenv(
        ownership.ENV_FASTPATH_VAR_OVERRIDES,
        ",".join(
            [f"ecmwf:{SPLIT_VAR}:global=delayed"]
            + [f"ecmwf:{var_id}:*=delayed" for var_id in _LAUNCH_VARS if var_id != SPLIT_VAR]
        ),
    )


def _plugin():
    return MODEL_REGISTRY.get("ecmwf")


def _state(data_root: Path) -> FastpathRunState:
    return FastpathRunState.load_or_create(
        data_root=data_root, model="ecmwf", run_id=RUN_ID
    )


@pytest.fixture()
def split_run(tmp_path: Path):
    source = FakeSource(max_fh=3)
    result = subloop_module.run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=tmp_path,
        run_dt=RUN_DT,
        source=source,
    )
    assert not result.stalled, result.stall_reasons
    return tmp_path, result


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def test_the_same_variable_resolves_to_different_sources_per_domain() -> None:
    assert ownership.source_for("ecmwf", SPLIT_VAR, "na") == ownership.SOURCE_FAST
    assert ownership.source_for("ecmwf", SPLIT_VAR, "global") == ownership.SOURCE_DELAYED
    assert ownership.fast_owned_pairs("ecmwf") == frozenset({(SPLIT_VAR, "na")})


# ---------------------------------------------------------------------------
# The fast pass builds only its own domain
# ---------------------------------------------------------------------------


def test_only_the_na_pair_is_active_in_the_fast_pass(split_run) -> None:
    _data_root, result = split_run
    assert result.active_pairs == ((SPLIT_VAR, "na"),)


def test_na_frames_are_fast_built_and_global_frames_are_not(split_run) -> None:
    data_root, _result = split_run
    for fh in (0, 3):
        assert _frame_exists(data_root, SPLIT_VAR, fh, domain="na"), fh
        assert not _frame_exists(data_root, SPLIT_VAR, fh, domain="global"), fh


def test_na_frames_carry_fast_provenance_on_the_9km_grid(split_run) -> None:
    data_root, _result = split_run
    sidecar = _sidecar(data_root, SPLIT_VAR, 3, domain="na")
    assert sidecar is not None
    assert sidecar["region"] == "na"
    assert sidecar["source_id"] == "openmeteo"
    assert sidecar["source_resolution"] == "9 km native"
    assert sidecar["generation"] == 1

    meta = json.loads(
        scheduler_module._frame_primary_artifact_path(
            data_root, "ecmwf", RUN_ID, SPLIT_VAR, 3, region="na"
        ).read_text()
    )
    assert (meta["height"], meta["width"]) == (1825, 1893)


def test_no_other_variable_is_touched_by_the_fast_pass(split_run) -> None:
    data_root, _result = split_run
    for var_id in _LAUNCH_VARS:
        if var_id == SPLIT_VAR:
            continue
        assert not _frame_exists(data_root, var_id, 3, domain="na"), var_id
        assert not _frame_exists(data_root, var_id, 3, domain="global"), var_id


# ---------------------------------------------------------------------------
# The delayed work list keeps the other domain
# ---------------------------------------------------------------------------


def _delayed_targets(data_root: Path):
    return set(
        scheduler_module._delayed_targets_for_cycle(
            _plugin(),
            [SPLIT_VAR],
            RUN_DT.hour,
            data_root=data_root,
            run_id=RUN_ID,
        )
    )


def test_the_delayed_builder_keeps_the_global_pair_and_drops_the_na_one(
    split_run,
) -> None:
    """Skip-by-config, per domain (design §3 rule 2).

    The delayed path must not merely skip what the fast path already wrote — it
    must never schedule the fast-owned pair at all, while continuing to schedule
    the same variable in the domain it still owns.
    """
    data_root, _result = split_run
    targets = _delayed_targets(data_root)
    regions = {region for region, var_id, _fh in targets if var_id == SPLIT_VAR}
    assert regions == {"global"}


def test_revoking_one_domain_leaves_the_other_domains_ownership_alone(
    split_run,
) -> None:
    """The whole point of the pair key: failover flips one domain, not both."""
    data_root, _result = split_run
    state = _state(data_root)
    state.revoke(SPLIT_VAR, "na", reason="delayed_source_available")
    state.save()

    targets = _delayed_targets(data_root)
    regions = {region for region, var_id, _fh in targets if var_id == SPLIT_VAR}
    # NA comes back to the delayed builder; global was never taken away.
    assert regions == {"na", "global"}
    assert ownership.source_for("ecmwf", SPLIT_VAR, "global") == (
        ownership.SOURCE_DELAYED
    )


# ---------------------------------------------------------------------------
# Readiness and manifests are per domain
# ---------------------------------------------------------------------------


def test_failover_state_tracks_only_the_fast_owned_domain(split_run) -> None:
    data_root, _result = split_run
    state = _state(data_root)
    assert set(state.pairs) == {f"{SPLIT_VAR}|na"}
    assert state.pair(SPLIT_VAR, "na", create=False).ready_through_fh == 3


def test_manifests_are_independent_per_domain(split_run) -> None:
    data_root, _result = split_run
    na_manifest = json.loads(
        scheduler_module._manifest_path(
            data_root, "ecmwf", RUN_ID, region="na"
        ).read_text()
    )
    entry = na_manifest["variables"][SPLIT_VAR]
    assert entry["ready_through_fh"] == 3
    assert [frame["fh"] for frame in entry["frames"]] == [0, 3]
    # Provenance is recorded for the domain the fast source owns...
    assert entry["source_ranges"] == [
        {
            "from_fh": 0,
            "to_fh": 3,
            "source_id": "openmeteo",
            "source_resolution": "9 km native",
            "generation": 1,
        }
    ]

    # ...and the global manifest is untouched: the fast pass never wrote that
    # domain, so the delayed build owns it start to finish.
    global_manifest_path = scheduler_module._manifest_path(
        data_root, "ecmwf", RUN_ID, region="global"
    )
    if global_manifest_path.exists():
        global_entry = json.loads(global_manifest_path.read_text())["variables"].get(
            SPLIT_VAR, {}
        )
        assert "source_ranges" not in global_entry
        assert not global_entry.get("frames")


# ---------------------------------------------------------------------------
# The canary is per domain too
# ---------------------------------------------------------------------------


def test_the_canary_compares_the_fast_owned_domain_only(split_run) -> None:
    data_root, _result = split_run
    canary = canary_module.run_canary_for_run(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=data_root,
        run_dt=RUN_DT,
        run_id=RUN_ID,
        state=_state(data_root),
        var_ids=(SPLIT_VAR,),
        source=FakeReferenceSource(),
        fh=3,
    )
    assert canary.status == canary_module.STATUS_OK
    assert [(result.var_id, result.domain) for result in canary.results] == [
        (SPLIT_VAR, "na")
    ]


def test_the_canary_pools_at_the_domains_own_scale(split_run) -> None:
    """~1° is 12 cells on the NA 9 km grid and 4 on the global 0.25° grid.

    A single hard-coded factor would silently measure a different length scale
    on each domain, which is precisely the kind of drift the ``(var, domain)``
    key exists to keep visible.
    """
    assert canary_module.COARSEN_FACTOR_BY_DOMAIN["na"] == 12
    assert canary_module.COARSEN_FACTOR_BY_DOMAIN["global"] == 4
