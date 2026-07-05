from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.app.models.base import VariableCapability
from backend.scripts import canary_binary_sampler as canary


def test_tolerance_classification_handles_model_specific_fourth_group() -> None:
    assert canary._classify_variable("gfs", "tmp2m") == 1
    assert canary._classify_variable("gfs", "precip_total") == 2
    assert canary._classify_variable("gfs", "ptype_intensity") == 3
    assert canary._classify_variable("hrrr", "radar_ptype") == 4


def test_scope_is_derived_from_requested_model() -> None:
    hrrr_scope, _, _, _ = canary._scope_for_model("hrrr")
    nbm_scope, _, _, _ = canary._scope_for_model("nbm")

    assert "radar_ptype" in hrrr_scope
    assert "tmp850_anom" in hrrr_scope
    assert "tmp2m" in nbm_scope
    assert "radar_ptype" not in nbm_scope


def test_group4_divergence_uses_integer_category_equality() -> None:
    result = canary.SampleResult(
        cog_file_missing=False,
        binary_frame_file_missing=False,
        binary_meta_file_missing=False,
        cog_value=1.49,
        binary_value=1.51,
        cog_latency_s=0.0,
        binary_latency_s=0.0,
    )

    assert canary._is_divergent(result, 4, "radar_ptype") is True


def test_group4_divergence_is_blocking() -> None:
    stats = {
        "total_comparisons": 1,
        "missing_binary_frame_files": 0,
        "missing_binary_meta_files": 0,
        "divergences": {"by_group": {"4": 1}},
    }

    assert canary._exit_code(stats, missing_run=False) == 4


# ── Buildable scope filter ──────────────────────────────────────────


def _cap(
    var_key: str,
    *,
    buildable: bool,
    companions: list[str] | None = None,
    artifact_map: dict[str, str] | None = None,
    supported_views: list[str] | None = None,
) -> VariableCapability:
    frontend: dict[str, object] = {}
    if companions is not None:
        frontend["companion_vars"] = companions
    if not buildable:
        frontend["internal_only"] = True
    ensemble: dict[str, object] = {}
    if supported_views is not None:
        ensemble["supported_views"] = supported_views
    if artifact_map is not None:
        ensemble["artifact_map"] = artifact_map
    return VariableCapability(
        var_key=var_key,
        name=var_key,
        buildable=buildable,
        frontend=frontend,
        ensemble=ensemble,
    )


def test_split_scope_excludes_non_buildable_non_companion_vars() -> None:
    catalog = {
        "alpha": _cap("alpha", buildable=True),
        "combo": _cap("combo", buildable=True, companions=["part_a", "part_b"]),
        "part_a": _cap("part_a", buildable=False),
        "part_b": _cap("part_b", buildable=False),
        "hidden_input": _cap("hidden_input", buildable=False),
    }
    packed = ["alpha", "combo", "hidden_input", "part_a", "part_b", "unlisted"]

    in_scope, excluded, dead_alias, uncataloged = canary._split_scope_by_buildable(
        packed, catalog
    )

    # buildable stays; companion-published non-buildables stay; vars with no
    # catalog entry at all are excluded as uncataloged (nothing to consult).
    assert in_scope == ["alpha", "combo", "part_a", "part_b"]
    assert excluded == ["hidden_input"]
    assert dead_alias == []
    assert uncataloged == ["unlisted"]


def test_companions_of_non_buildable_vars_do_not_rescue_scope() -> None:
    catalog = {
        "parent": _cap("parent", buildable=False, companions=["child"]),
        "child": _cap("child", buildable=False),
    }

    in_scope, excluded, dead_alias, uncataloged = canary._split_scope_by_buildable(
        ["child", "parent"], catalog
    )

    assert in_scope == []
    assert excluded == ["child", "parent"]
    assert dead_alias == []
    assert uncataloged == []


def test_hrrr_scope_excludes_radar_ptype_components() -> None:
    scope, excluded, dead_alias, uncataloged = canary._scope_for_model("hrrr")

    assert excluded == [
        "radar_ptype_frzr",
        "radar_ptype_rain",
        "radar_ptype_sleet",
        "radar_ptype_snow",
    ]
    assert dead_alias == []
    assert uncataloged == []
    assert not set(excluded) & set(scope)
    assert "radar_ptype" in scope


def test_gfs_scope_keeps_companion_published_ptype_components() -> None:
    # GFS's ptype_intensity_* components are buildable=False but are published
    # as companions of the buildable ptype_intensity composite, so they have a
    # real COG-vs-binary parity question and must stay in scope.
    scope, excluded, dead_alias, uncataloged = canary._scope_for_model("gfs")

    assert excluded == []
    assert dead_alias == []
    assert uncataloged == []
    assert "ptype_intensity_rain" in scope
    assert "ptype_intensity_snow" in scope
    assert "ptype_intensity_ice" in scope


def test_nbm_scope_has_no_exclusions() -> None:
    scope, excluded, dead_alias, uncataloged = canary._scope_for_model("nbm")

    assert excluded == []
    assert dead_alias == []
    assert uncataloged == []
    assert scope


def test_split_scope_composes_all_three_publish_paths() -> None:
    # One catalog mixing every pattern: plain buildable, companion-published,
    # ensemble-artifact-published, and dead-alias buildables whose artifact_map
    # redirects them. They must compose without double-counting or rescuing
    # genuinely internal vars.
    catalog = {
        "plain": _cap("plain", buildable=True),
        "combo": _cap("combo", buildable=True, companions=["part_a"]),
        "part_a": _cap("part_a", buildable=False),
        # Buildable but redirected: frames exist only under ens__mean.
        "ens": _cap(
            "ens",
            buildable=True,
            supported_views=["mean"],
            artifact_map={"mean": "ens__mean", "members": "ens__members"},
        ),
        "ens__mean": _cap("ens__mean", buildable=False, supported_views=["mean"]),
        # Mapped by a reachable view AND companion-published: still one entry.
        "both__mean": _cap("both__mean", buildable=False),
        "both": _cap(
            "both",
            buildable=True,
            companions=["both__mean"],
            supported_views=["mean"],
            artifact_map={"mean": "both__mean"},
        ),
        # "members" is not in supported_views, so this artifact is unreachable.
        "ens__members": _cap("ens__members", buildable=False),
        "hidden_input": _cap("hidden_input", buildable=False),
    }
    packed = [
        "both", "both__mean", "combo", "ens", "ens__mean", "ens__members",
        "hidden_input", "part_a", "plain",
    ]

    in_scope, excluded, dead_alias, uncataloged = canary._split_scope_by_buildable(
        packed, catalog
    )

    assert in_scope == ["both__mean", "combo", "ens__mean", "part_a", "plain"]
    assert excluded == ["ens__members", "hidden_input"]
    # "ens" and "both" are buildable but their artifact_map redirects every
    # reachable view elsewhere — never written under their own names.
    assert dead_alias == ["both", "ens"]
    assert uncataloged == []
    assert not set(dead_alias) & set(excluded)


def test_buildable_with_redirecting_artifact_map_is_dead_alias() -> None:
    catalog = {
        "alias": _cap(
            "alias",
            buildable=True,
            supported_views=["mean"],
            artifact_map={"mean": "alias__mean"},
        ),
        "alias__mean": _cap("alias__mean", buildable=False),
    }

    in_scope, excluded, dead_alias, uncataloged = canary._split_scope_by_buildable(
        ["alias", "alias__mean"], catalog
    )

    assert in_scope == ["alias__mean"]
    assert excluded == []
    assert dead_alias == ["alias"]
    assert uncataloged == []


def test_buildable_without_artifact_map_is_not_dead_alias() -> None:
    # GFS/HRRR/NBM's normal case: buildable, no ensemble redirection —
    # frames are written under the variable's own name.
    catalog = {"plain": _cap("plain", buildable=True)}

    in_scope, excluded, dead_alias, uncataloged = canary._split_scope_by_buildable(
        ["plain"], catalog
    )

    assert in_scope == ["plain"]
    assert excluded == []
    assert dead_alias == []
    assert uncataloged == []


def test_artifact_map_of_non_buildable_var_does_not_rescue_scope() -> None:
    catalog = {
        "parent": _cap(
            "parent",
            buildable=False,
            supported_views=["mean"],
            artifact_map={"mean": "parent__mean"},
        ),
        "parent__mean": _cap("parent__mean", buildable=False),
    }

    in_scope, excluded, dead_alias, uncataloged = canary._split_scope_by_buildable(
        ["parent", "parent__mean"], catalog
    )

    assert in_scope == []
    # Non-buildable entries are never dead aliases — that class is reserved
    # for buildable ids; "parent" stays in the non-buildable bucket.
    assert excluded == ["parent", "parent__mean"]
    assert dead_alias == []
    assert uncataloged == []


_GEFS_EPS_DEAD_ALIASES = {
    "gefs": [
        "hgt500_anom", "precip_10d_anom", "precip_16d_anom", "precip_5d_anom",
        "precip_7d_anom", "tmp2m_anom", "tmp850_anom",
    ],
    "eps": [
        "hgt500_anom", "precip_10d_anom", "precip_15d_anom", "precip_5d_anom",
        "precip_7d_anom", "tmp2m_anom", "tmp850_anom",
    ],
}


def test_gefs_scope_is_published_mean_artifacts_only() -> None:
    # GEFS publishes exclusively under the runtime __mean artifact ids; the
    # bare buildable ids are runtime aliases with no frames of their own.
    scope, excluded, dead_alias, uncataloged = canary._scope_for_model("gefs")

    assert excluded == []
    assert dead_alias == _GEFS_EPS_DEAD_ALIASES["gefs"]
    assert uncataloged == []
    assert "tmp2m__mean" in scope
    assert "precip_total__mean" in scope
    assert "tmp2m_anom__mean" in scope
    assert not set(dead_alias) & set(scope)
    assert not set(dead_alias) & set(excluded)


def test_eps_scope_is_published_mean_artifacts_only() -> None:
    scope, excluded, dead_alias, uncataloged = canary._scope_for_model("eps")

    # hgt500__mean is a contour-component input, not an artifact_map value of
    # any buildable entry — a different exclusion class than the dead aliases.
    assert excluded == ["hgt500__mean"]
    assert dead_alias == _GEFS_EPS_DEAD_ALIASES["eps"]
    assert uncataloged == []
    assert "tmp2m__mean" in scope
    assert "precip_total__mean" in scope
    assert not set(dead_alias) & set(scope)
    assert not set(dead_alias) & set(excluded)


def test_packed_var_without_catalog_entry_is_excluded_uncataloged() -> None:
    # "ghost" is packed and would be rescued by BOTH remaining publish paths —
    # it is a companion of a buildable entry AND an artifact_map value of a
    # reachable view — but it has no catalog entry of its own. The uncataloged
    # check is the most fundamental and runs first: with no capability to
    # consult, no publish path can vouch for it.
    catalog = {
        "host": _cap(
            "host",
            buildable=True,
            companions=["ghost"],
            supported_views=["mean"],
            artifact_map={"mean": "ghost"},
        ),
    }

    in_scope, excluded, dead_alias, uncataloged = canary._split_scope_by_buildable(
        ["ghost", "host"], catalog
    )

    assert uncataloged == ["ghost"]
    assert in_scope == []  # "host" is itself a dead alias (redirects to ghost)
    assert dead_alias == ["host"]
    assert excluded == []


def test_ecmwf_scope_excludes_only_uncataloged_precip_16d_anom() -> None:
    # The cross-model precip-anomaly packing loop in grid.py registers
    # ("ecmwf", "precip_16d_anom"), but ecmwf's own catalog uses the 15d
    # window and has no entry for the 16d key — the exact "packed for a model
    # whose catalog opted out" failure mode this bucket exists for.
    scope, excluded, dead_alias, uncataloged = canary._scope_for_model("ecmwf")

    assert uncataloged == ["precip_16d_anom"]
    assert excluded == []
    assert dead_alias == []
    assert "precip_16d_anom" not in scope
    assert "precip_15d_anom" in scope

    # This fix only adds the uncataloged check — the previously audited
    # models' exclusions must be byte-identical to before (zero uncataloged,
    # existing buckets pinned by the model-specific tests above).
    for model in ("gfs", "hrrr", "nbm", "gefs", "eps"):
        _scope, _non_buildable, _dead, model_uncataloged = canary._scope_for_model(model)
        assert model_uncataloged == [], f"{model}: unexpected uncataloged packed vars"


# ── Benchmark variable selection ─────────────────────────────────────


def test_benchmark_var_is_first_group1_in_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An ensemble-style scope with no bare "tmp2m" (the old hardcoded pick,
    # which is never published for such models): the benchmark must target
    # the first Group 1 variable actually in scope.
    probed: list[str] = []

    def fake_discover(published_root: Path, model: str, run: str, var: str) -> list[int]:
        probed.append(var)
        return []

    monkeypatch.setattr(canary, "_discover_frames_for_run_var", fake_discover)

    scope = ["precip_total__mean", "tmp2m__mean", "tmp850__mean"]
    group_index = {"precip_total__mean": 2, "tmp2m__mean": 1, "tmp850__mean": 1}

    result = canary._run_benchmarks(
        Path("/nonexistent"), "gefs", [], "20260704_00z", 1, scope, group_index
    )

    assert result == []  # no frames on the fake disk — bench aborts cleanly
    assert probed == ["tmp2m__mean"]


def test_benchmarks_skipped_when_scope_has_no_group1(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_discover(*args: object, **kwargs: object) -> list[int]:
        raise AssertionError("frame discovery must not run when benchmarks are skipped")

    monkeypatch.setattr(canary, "_discover_frames_for_run_var", fail_discover)

    with caplog.at_level(logging.WARNING, logger="canary"):
        result = canary._run_benchmarks(
            Path("/nonexistent"), "hypo", [], "20260704_00z", 1,
            ["only_upscaled"], {"only_upscaled": 2},
        )

    assert result == []
    assert any("skipping benchmarks" in record.message for record in caplog.records)


# ── Binary substrate failure (bin_meta_invalid) ─────────────────────


def _sample_result(**overrides: object) -> canary.SampleResult:
    base: dict[str, object] = {
        "cog_file_missing": False,
        "binary_frame_file_missing": False,
        "binary_meta_file_missing": False,
        "cog_value": 5.0,
        "binary_value": 5.0,
        "cog_latency_s": 0.0,
        "binary_latency_s": 0.0,
    }
    base.update(overrides)
    return canary.SampleResult(**base)  # type: ignore[arg-type]


def test_bin_meta_invalid_when_cog_sampled_but_binary_returned_no_value() -> None:
    assert canary._is_bin_meta_invalid(_sample_result(binary_value=None)) is True


def test_bin_meta_invalid_not_counted_for_shared_no_data() -> None:
    assert canary._is_bin_meta_invalid(
        _sample_result(cog_value=None, binary_value=None)
    ) is False


def test_bin_meta_invalid_not_counted_when_binary_file_missing() -> None:
    # Missing files are already their own blocking classification.
    assert canary._is_bin_meta_invalid(
        _sample_result(binary_meta_file_missing=True, binary_value=None)
    ) is False


def test_bin_meta_invalid_is_blocking_regardless_of_group() -> None:
    stats = {
        "total_comparisons": 100,
        "missing_binary_frame_files": 0,
        "missing_binary_meta_files": 0,
        "bin_meta_invalid_count": 1,
        "divergences": {"by_group": {}},
    }

    assert canary._exit_code(stats, missing_run=False) == 4


def test_asymmetric_no_value_rate_is_blocking() -> None:
    stats = {
        "total_comparisons": 100,
        "missing_binary_frame_files": 0,
        "missing_binary_meta_files": 0,
        "bin_meta_invalid_count": 0,
        "cog_no_value_samples": 2,
        "binary_no_value_samples": 25,
        "divergences": {"by_group": {}},
    }

    assert canary._no_value_rate_asymmetric(stats) is True
    assert canary._exit_code(stats, missing_run=False) == 4


def test_symmetric_no_value_rate_is_not_blocking() -> None:
    # Matching high no-value rates are legitimate shared no-data.
    stats = {
        "total_comparisons": 100,
        "missing_binary_frame_files": 0,
        "missing_binary_meta_files": 0,
        "bin_meta_invalid_count": 0,
        "cog_no_value_samples": 25,
        "binary_no_value_samples": 25,
        "divergences": {"by_group": {}},
    }

    assert canary._no_value_rate_asymmetric(stats) is False
    assert canary._exit_code(stats, missing_run=False) == 0


# ── --vars filter ───────────────────────────────────────────────────


def test_vars_filter_restricts_scope_preserving_order() -> None:
    scope = ["dp2m", "radar_ptype", "tmp2m"]

    assert canary._parse_vars_filter("tmp2m, radar_ptype", scope) == [
        "radar_ptype",
        "tmp2m",
    ]


def test_vars_filter_none_returns_full_scope() -> None:
    scope = ["dp2m", "tmp2m"]

    assert canary._parse_vars_filter(None, scope) == scope


def test_vars_filter_rejects_unknown_variables() -> None:
    with pytest.raises(ValueError, match="not in comparison scope"):
        canary._parse_vars_filter("tmp2m,nope", ["dp2m", "tmp2m"])


def test_vars_filter_rejects_empty_selection() -> None:
    with pytest.raises(ValueError, match="no variable names"):
        canary._parse_vars_filter(" , ", ["tmp2m"])


# ── Zero-comparison coverage warning ────────────────────────────────


def test_warns_when_in_scope_variable_gets_zero_comparisons(
    tmp_path, caplog
) -> None:
    published_root = tmp_path / "published"
    published_root.mkdir()
    anchors = [canary.AnchorPoint(label="Somewhere", lat=40.0, lon=-100.0)]

    with caplog.at_level(logging.WARNING, logger="canary"):
        stats = canary._run_comparison(
            published_root,
            "hrrr",
            ["tmp2m"],
            ["20260702_18z"],
            anchors,
            {"tmp2m": 1},
            log_path=None,
            sample_limit=5,
        )

    assert stats["vars_with_zero_comparisons"] == ["tmp2m"]
    warning = next(
        rec for rec in caplog.records
        if rec.levelno == logging.WARNING and "zero comparisons" in rec.message
    )
    assert "tmp2m" in warning.getMessage()
    assert "--sample-limit" in warning.getMessage()


# ── Meteogram benchmark frame-count derivation ──────────────────────


def test_expected_meteogram_frames_derive_from_model_schedule() -> None:
    from backend.app.models.registry import MODEL_REGISTRY

    for model, run in (
        ("hrrr", "20260702_18z"),  # extended 48-hour cycle
        ("hrrr", "20260702_17z"),  # standard 18-hour cycle
        ("gfs", "20260702_12z"),
        ("nbm", "20260702_12z"),
    ):
        cycle_hour = int(run[9:11])
        expected = len(MODEL_REGISTRY[model].scheduled_fhs_for_var("tmp2m", cycle_hour))
        assert canary._expected_meteogram_frame_count(model, "tmp2m", run) == expected


def test_expected_meteogram_frames_differ_across_hrrr_cycle_types() -> None:
    extended = canary._expected_meteogram_frame_count("hrrr", "tmp2m", "20260702_18z")
    standard = canary._expected_meteogram_frame_count("hrrr", "tmp2m", "20260702_17z")

    assert extended is not None and standard is not None
    assert extended > standard


def test_expected_meteogram_frames_none_for_unknown_model_or_bad_run() -> None:
    assert canary._expected_meteogram_frame_count("nosuchmodel", "tmp2m", "20260702_18z") is None
    assert canary._expected_meteogram_frame_count("hrrr", "tmp2m", "not-a-run-id") is None
