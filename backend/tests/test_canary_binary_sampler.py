from __future__ import annotations

from backend.scripts import canary_binary_sampler as canary


def test_tolerance_classification_handles_model_specific_fourth_group() -> None:
    assert canary._classify_variable("gfs", "tmp2m") == 1
    assert canary._classify_variable("gfs", "precip_total") == 2
    assert canary._classify_variable("gfs", "ptype_intensity") == 3
    assert canary._classify_variable("hrrr", "radar_ptype") == 4


def test_scope_is_derived_from_requested_model() -> None:
    hrrr_scope = canary._scope_for_model("hrrr")
    nbm_scope = canary._scope_for_model("nbm")

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
