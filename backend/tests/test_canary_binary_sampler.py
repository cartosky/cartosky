from __future__ import annotations

import logging

import pytest

from backend.app.models.base import VariableCapability
from backend.scripts import canary_binary_sampler as canary


def test_tolerance_classification_handles_model_specific_fourth_group() -> None:
    assert canary._classify_variable("gfs", "tmp2m") == 1
    assert canary._classify_variable("gfs", "precip_total") == 2
    assert canary._classify_variable("gfs", "ptype_intensity") == 3
    assert canary._classify_variable("hrrr", "radar_ptype") == 4


def test_scope_is_derived_from_requested_model() -> None:
    hrrr_scope, _ = canary._scope_for_model("hrrr")
    nbm_scope, _ = canary._scope_for_model("nbm")

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


def _cap(var_key: str, *, buildable: bool, companions: list[str] | None = None) -> VariableCapability:
    frontend: dict[str, object] = {}
    if companions is not None:
        frontend["companion_vars"] = companions
    if not buildable:
        frontend["internal_only"] = True
    return VariableCapability(
        var_key=var_key,
        name=var_key,
        buildable=buildable,
        frontend=frontend,
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

    in_scope, excluded = canary._split_scope_by_buildable(packed, catalog)

    # buildable stays; companion-published non-buildables stay; vars without
    # a catalog entry cannot be cross-referenced and stay.
    assert in_scope == ["alpha", "combo", "part_a", "part_b", "unlisted"]
    assert excluded == ["hidden_input"]


def test_companions_of_non_buildable_vars_do_not_rescue_scope() -> None:
    catalog = {
        "parent": _cap("parent", buildable=False, companions=["child"]),
        "child": _cap("child", buildable=False),
    }

    in_scope, excluded = canary._split_scope_by_buildable(["child", "parent"], catalog)

    assert in_scope == []
    assert excluded == ["child", "parent"]


def test_hrrr_scope_excludes_radar_ptype_components() -> None:
    scope, excluded = canary._scope_for_model("hrrr")

    assert excluded == [
        "radar_ptype_frzr",
        "radar_ptype_rain",
        "radar_ptype_sleet",
        "radar_ptype_snow",
    ]
    assert not set(excluded) & set(scope)
    assert "radar_ptype" in scope


def test_gfs_scope_keeps_companion_published_ptype_components() -> None:
    # GFS's ptype_intensity_* components are buildable=False but are published
    # as companions of the buildable ptype_intensity composite, so they have a
    # real COG-vs-binary parity question and must stay in scope.
    scope, excluded = canary._scope_for_model("gfs")

    assert excluded == []
    assert "ptype_intensity_rain" in scope
    assert "ptype_intensity_snow" in scope
    assert "ptype_intensity_ice" in scope


def test_nbm_scope_has_no_exclusions() -> None:
    scope, excluded = canary._scope_for_model("nbm")

    assert excluded == []
    assert scope


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
