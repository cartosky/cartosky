"""Ownership resolution + delayed-path skip (fast-path design §3, WP3).

The flag-off case is the load-bearing one: with ``CARTOSKY_FASTPATH_MODELS``
unset every pair must resolve ``delayed`` and the ``sources.openmeteo`` family
must never be imported, so a production scheduler process that has not opted in
is byte-for-byte the process it was before WP3.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import scheduler as scheduler_module
from app.services.fastpath import ownership
from app.services.fastpath.state import FastpathRunState

LAUNCH_SET = {"tmp2m", "dp2m", "precip_total", "snowfall_total", "wgst10m", "wspd10m"}


@pytest.fixture(autouse=True)
def _clear_fastpath_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ownership.ENV_FASTPATH_MODELS, raising=False)
    monkeypatch.delenv(ownership.ENV_FASTPATH_VAR_OVERRIDES, raising=False)
    monkeypatch.delenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", raising=False)


@pytest.fixture()
def global_domain_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment switch the DELAYED path already obeys.

    Fast ownership of a non-canonical domain is gated on it too, so any test
    that expects a ``(var, global)`` pair has to turn it on."""
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "ecmwf")


# ---------------------------------------------------------------------------
# Flag off
# ---------------------------------------------------------------------------


def test_flag_off_resolves_everything_delayed() -> None:
    assert ownership.fastpath_models() == frozenset()
    assert not ownership.fastpath_enabled("ecmwf")
    for var_id in sorted(LAUNCH_SET):
        for domain in ("na", "global"):
            assert ownership.source_for("ecmwf", var_id, domain) == ownership.SOURCE_DELAYED
            assert not ownership.is_fast_owned("ecmwf", var_id, domain)
    assert ownership.fast_owned_pairs("ecmwf") == frozenset()
    assert ownership.launch_set_pairs("ecmwf") == frozenset()
    assert ownership.fast_domains_for_model("ecmwf") == ()


def test_flag_off_imports_no_openmeteo_module() -> None:
    """A fresh interpreter must import the scheduler + resolve ownership with
    neither ``sources.openmeteo`` nor ``omfiles`` ever entering ``sys.modules``.

    Run in a subprocess on purpose: any other test in this session may have
    imported the package already, which would make an in-process assertion
    vacuous.
    """
    script = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, %r)
        from app.services import scheduler  # noqa: F401
        from app.services.fastpath import ownership

        assert ownership.source_for("ecmwf", "tmp2m", "na") == "delayed"
        assert ownership.fast_owned_pairs("ecmwf") == frozenset()

        leaked = sorted(
            name for name in sys.modules
            if "sources.openmeteo" in name or name.split(".")[0] == "omfiles"
        )
        assert not leaked, leaked
        print("clean")
        """
    ) % str(BACKEND_ROOT)
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert "clean" in completed.stdout


# ---------------------------------------------------------------------------
# Flag on
# ---------------------------------------------------------------------------


def test_launch_set_flips_fast_in_both_domains(
    monkeypatch: pytest.MonkeyPatch, global_domain_enabled
) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")

    assert ownership.fast_domains_for_model("ecmwf") == ("na", "global")
    pairs = ownership.fast_owned_pairs("ecmwf")
    assert pairs == {(var_id, domain) for var_id in LAUNCH_SET for domain in ("na", "global")}
    for var_id in sorted(LAUNCH_SET):
        for domain in ("na", "global"):
            assert ownership.source_for("ecmwf", var_id, domain) == ownership.SOURCE_FAST


def test_non_launch_variables_stay_delayed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    # Pressure-level and mixed-derive variables stay delayed-owned (design §3
    # rule 3): they need inputs the 9 km surface set does not carry.
    for var_id in ("tmp850", "snowfall_kuchera_total", "ptype_intensity", "hgt500"):
        for domain in ("na", "global"):
            assert ownership.source_for("ecmwf", var_id, domain) == ownership.SOURCE_DELAYED


def test_other_models_unaffected_by_an_ecmwf_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    assert ownership.source_for("gfs", "tmp2m", "na") == ownership.SOURCE_DELAYED
    assert ownership.fast_owned_pairs("hrrr") == frozenset()


def test_a_pair_can_be_fast_in_one_domain_and_delayed_in_the_other(
    monkeypatch: pytest.MonkeyPatch, global_domain_enabled
) -> None:
    """Design §3 demands a contract test that the ownership key really is
    ``(variable, domain)`` — variable-only ownership could not express this."""
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.setenv(ownership.ENV_FASTPATH_VAR_OVERRIDES, "ecmwf:tmp2m:global=delayed")

    assert ownership.source_for("ecmwf", "tmp2m", "na") == ownership.SOURCE_FAST
    assert ownership.source_for("ecmwf", "tmp2m", "global") == ownership.SOURCE_DELAYED
    pairs = ownership.fast_owned_pairs("ecmwf")
    assert ("tmp2m", "na") in pairs
    assert ("tmp2m", "global") not in pairs
    # Every other launch-set variable is untouched.
    assert ("dp2m", "global") in pairs


def test_override_wildcard_and_precedence(
    monkeypatch: pytest.MonkeyPatch, global_domain_enabled
) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.setenv(
        ownership.ENV_FASTPATH_VAR_OVERRIDES,
        "ecmwf:precip_total:*=delayed,ecmwf:precip_total:na=fast",
    )
    # The explicit domain wins over the wildcard.
    assert ownership.source_for("ecmwf", "precip_total", "na") == ownership.SOURCE_FAST
    assert ownership.source_for("ecmwf", "precip_total", "global") == ownership.SOURCE_DELAYED
    pairs = ownership.fast_owned_pairs("ecmwf")
    assert ("precip_total", "na") in pairs
    assert ("precip_total", "global") not in pairs


def test_a_fast_override_for_an_unproducible_variable_fails_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``ecmwf:tmp850:na=fast`` would otherwise carve tmp850 out of the delayed
    work list while the fast plan cannot build it — nobody would build it until
    the failover deadline fired an hour or two later. It must fail closed."""
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.setenv(ownership.ENV_FASTPATH_VAR_OVERRIDES, "ecmwf:tmp850:na=fast")

    with caplog.at_level("ERROR", logger="app.services.fastpath.ownership"):
        assert ownership.source_for("ecmwf", "tmp850", "na") == ownership.SOURCE_DELAYED
    assert ("tmp850", "na") not in ownership.fast_owned_pairs("ecmwf")
    assert any(
        "cannot produce" in record.getMessage()
        for record in caplog.records
        if record.levelname == "ERROR"
    ), "an invalid fast override must be loud, not silent"


def test_a_fast_override_for_a_producible_variable_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validation must not break the legitimate use: re-enabling a
    launch-set pair that a previous override turned off."""
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.setenv(
        ownership.ENV_FASTPATH_VAR_OVERRIDES,
        "ecmwf:tmp2m:*=delayed,ecmwf:tmp2m:na=fast",
    )
    assert ownership.source_for("ecmwf", "tmp2m", "na") == ownership.SOURCE_FAST
    assert ("tmp2m", "na") in ownership.fast_owned_pairs("ecmwf")


def test_every_launch_set_variable_is_producible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the validation itself: if the catalog and the launch set ever
    disagree, the override validator would start rejecting valid pairs."""
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    for var_id in sorted(LAUNCH_SET):
        assert ownership._fast_can_produce("ecmwf", var_id), var_id


def test_overrides_are_inert_while_the_allowlist_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_VAR_OVERRIDES, "ecmwf:tmp850:na=fast")
    assert ownership.source_for("ecmwf", "tmp850", "na") == ownership.SOURCE_DELAYED


@pytest.mark.parametrize(
    "raw",
    ["garbage", "ecmwf:tmp2m=delayed", "ecmwf:tmp2m:na=sideways", "ecmwf::na=fast", ""],
)
def test_malformed_overrides_are_ignored_not_fatal(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.setenv(ownership.ENV_FASTPATH_VAR_OVERRIDES, raw)
    # An unparseable kill switch must not take the model down with it.
    assert ownership.source_for("ecmwf", "tmp2m", "na") == ownership.SOURCE_FAST


# ---------------------------------------------------------------------------
# Domain enablement (CARTOSKY_GLOBAL_DOMAIN_MODELS)
# ---------------------------------------------------------------------------


def test_global_is_not_fast_owned_until_the_environment_enables_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The catalog's ``fast_domains`` is a declaration, not a licence.

    If the fast path owned ``global`` in an environment where the delayed path
    does not build it, two things break: the sub-loop would publish global
    artifacts nothing else produces, and failover for a ``(var, global)`` pair
    would revoke ownership to a delayed builder that never builds that domain
    — leaving the pair with nobody to hand back to.
    """
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    # CARTOSKY_GLOBAL_DOMAIN_MODELS is unset by the autouse fixture.

    assert ownership.fast_domains_for_model("ecmwf") == ("na",)
    pairs = ownership.fast_owned_pairs("ecmwf")
    assert pairs == {(var_id, "na") for var_id in LAUNCH_SET}
    for var_id in sorted(LAUNCH_SET):
        assert ownership.source_for("ecmwf", var_id, "na") == ownership.SOURCE_FAST
        assert ownership.source_for("ecmwf", var_id, "global") == ownership.SOURCE_DELAYED
        assert not ownership.is_fast_owned("ecmwf", var_id, "global")


def test_enabling_the_global_domain_flips_it_fast(
    monkeypatch: pytest.MonkeyPatch, global_domain_enabled
) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    assert ownership.fast_domains_for_model("ecmwf") == ("na", "global")
    assert ownership.source_for("ecmwf", "tmp2m", "global") == ownership.SOURCE_FAST
    assert ("tmp2m", "global") in ownership.fast_owned_pairs("ecmwf")


def test_an_override_cannot_force_a_domain_the_environment_disables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The override is a kill switch and an emergency lever, not a way to
    route around domain enablement."""
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.setenv(ownership.ENV_FASTPATH_VAR_OVERRIDES, "ecmwf:tmp2m:global=fast")

    assert ownership.source_for("ecmwf", "tmp2m", "global") == ownership.SOURCE_DELAYED
    assert ("tmp2m", "global") not in ownership.fast_owned_pairs("ecmwf")
    # ...and the canonical domain is unaffected.
    assert ownership.source_for("ecmwf", "tmp2m", "na") == ownership.SOURCE_FAST


def test_a_wildcard_override_expands_only_over_enabled_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``global`` is disabled here, so a producible variable's ``*`` override
    reaches ``na`` only."""
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.setenv(
        ownership.ENV_FASTPATH_VAR_OVERRIDES,
        "ecmwf:tmp2m:*=delayed,ecmwf:dp2m:*=fast",
    )
    pairs = ownership.fast_owned_pairs("ecmwf")
    assert ("dp2m", "na") in pairs
    assert ("dp2m", "global") not in pairs
    assert ("tmp2m", "na") not in pairs


def test_a_delayed_override_still_applies_to_a_disabled_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turning something OFF must never depend on enablement — the kill switch
    has to work in every environment."""
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.setenv(ownership.ENV_FASTPATH_VAR_OVERRIDES, "ecmwf:tmp2m:*=delayed")
    assert ownership.source_for("ecmwf", "tmp2m", "na") == ownership.SOURCE_DELAYED
    assert ownership.source_for("ecmwf", "tmp2m", "global") == ownership.SOURCE_DELAYED


def test_delayed_path_keeps_global_when_the_domain_is_disabled(
    _two_domain_plugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verifier's symptom, at the scheduler seam: with global disabled the
    fast path must not carve global pairs out of the delayed work list."""
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    targets = scheduler_module._delayed_targets_for_cycle(
        _two_domain_plugin, ["tmp2m"], 0
    )
    assert ("global", "tmp2m", 0) in targets
    assert not [target for target in targets if target[0] == "na"]


# ---------------------------------------------------------------------------
# Delayed-path skip
# ---------------------------------------------------------------------------


class _FakeEcmwfPlugin:
    id = "ecmwf"

    def normalize_var_id(self, var_id: str) -> str:
        return str(var_id).strip().lower()

    def scheduled_fhs_for_var(self, var_key: str, cycle_hour: int) -> list[int]:
        del var_key, cycle_hour
        return [0, 3]

    def get_region(self, region: str):
        return object()

    capabilities = None


@pytest.fixture()
def _two_domain_plugin(monkeypatch: pytest.MonkeyPatch) -> _FakeEcmwfPlugin:
    plugin = _FakeEcmwfPlugin()
    monkeypatch.setattr(
        scheduler_module, "declared_domains_for_var", lambda _plugin, _var: ["na", "global"]
    )
    monkeypatch.setattr(scheduler_module, "canonical_domain", lambda _plugin: "na")
    return plugin


def test_delayed_targets_unchanged_with_flag_off(_two_domain_plugin) -> None:
    targets = scheduler_module._delayed_targets_for_cycle(
        _two_domain_plugin, ["tmp2m", "tmp850"], 0
    )
    assert targets == scheduler_module._scheduled_targets_for_cycle(
        _two_domain_plugin, ["tmp2m", "tmp850"], 0
    )
    assert ("na", "tmp2m", 0) in targets
    assert ("global", "tmp2m", 3) in targets


def test_delayed_targets_skip_fast_owned_pairs_by_config(
    _two_domain_plugin, monkeypatch: pytest.MonkeyPatch, global_domain_enabled
) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    targets = scheduler_module._delayed_targets_for_cycle(
        _two_domain_plugin, ["tmp2m", "tmp850"], 0
    )
    # tmp2m is fast-owned in both domains -> absent from the delayed work list
    # entirely, whether or not its artifacts already exist (skip-by-config).
    assert not [target for target in targets if target[1] == "tmp2m"]
    # tmp850 is delayed-owned in both domains -> untouched.
    assert ("na", "tmp850", 0) in targets
    assert ("global", "tmp850", 3) in targets


def test_delayed_targets_respect_a_per_domain_override(
    _two_domain_plugin, monkeypatch: pytest.MonkeyPatch, global_domain_enabled
) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.setenv(ownership.ENV_FASTPATH_VAR_OVERRIDES, "ecmwf:tmp2m:global=delayed")
    targets = scheduler_module._delayed_targets_for_cycle(_two_domain_plugin, ["tmp2m"], 0)
    assert {target[0] for target in targets} == {"global"}


def test_delayed_targets_reclaim_revoked_pairs(
    _two_domain_plugin, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    global_domain_enabled,
) -> None:
    """A revoked pair is delayed-owned again — that is what failover MEANS."""
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    run_id = "20260804_00z"
    state = FastpathRunState.load_or_create(
        data_root=tmp_path, model="ecmwf", run_id=run_id
    )
    state.revoke("tmp2m", "na", reason="test")
    state.save()

    targets = scheduler_module._delayed_targets_for_cycle(
        _two_domain_plugin, ["tmp2m"], 0, data_root=tmp_path, run_id=run_id
    )
    assert ("na", "tmp2m", 0) in targets
    assert ("na", "tmp2m", 3) in targets
    # The still-fast global domain stays skipped.
    assert not [target for target in targets if target[0] == "global"]


def test_run_id_helper_matches_state_file_naming(tmp_path: Path) -> None:
    run_dt = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    run_id = scheduler_module._run_id_from_dt(run_dt)
    state = FastpathRunState.load_or_create(
        data_root=tmp_path, model="ecmwf", run_id=run_id
    )
    assert state.path.name == f"{run_id}.state.json"
    assert state.path.parent.name == "_fastpath"
    # Never inside a run directory -> invisible to run retention and promotion.
    assert state.path.parent.parent.name == "ecmwf"
