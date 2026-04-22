from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.build_regions import FIRST_NA_BUILD_COHORT_BY_MODEL
from app.models.registry import MODEL_REGISTRY
from app.services import scheduler as scheduler_module


@pytest.mark.parametrize(
    ("model_id", "var_id"),
    [
        ("gfs", "tmp2m"),
        ("gfs", "pwat"),
        ("gefs", "tmp2m"),
        ("gefs", "pwat"),
        ("ecmwf", "tmp2m"),
        ("ecmwf", "pwat"),
        ("aigfs", "tmp2m"),
        ("aifs", "tmp2m"),
        ("aifs", "pwat"),
        ("eps", "tmp2m"),
    ],
)
def test_first_na_cohort_vars_opt_into_na_build_region(model_id: str, var_id: str) -> None:
    plugin = MODEL_REGISTRY[model_id]

    capability = plugin.get_var_capability(var_id)
    assert capability is not None
    assert capability.supported_build_regions == ["conus", "na"]
    assert scheduler_module._build_regions_for_var(plugin, var_id) == ["conus", "na"]


@pytest.mark.parametrize("model_id", sorted(FIRST_NA_BUILD_COHORT_BY_MODEL.keys()))
def test_first_na_cohort_matches_rollout_table(model_id: str) -> None:
    plugin = MODEL_REGISTRY[model_id]

    enabled = {
        capability.var_key
        for capability in plugin.capabilities.variable_catalog.values()
        if capability.supported_build_regions == ["conus", "na"]
    }
    assert enabled == set(FIRST_NA_BUILD_COHORT_BY_MODEL[model_id])


@pytest.mark.parametrize(
    ("model_id", "var_id"),
    [
        ("gfs", "precip_total"),
        ("gfs", "tmp850"),
        ("gfs", "wspd10m"),
        ("gefs", "precip_total"),
        ("gefs", "tmp850"),
        ("ecmwf", "precip_total"),
        ("ecmwf", "hgt500"),
        ("aigfs", "precip_total"),
        ("aigfs", "tmp850"),
        ("aifs", "precip_total"),
        ("aifs", "wspd10m"),
        ("gefs", "hgt500__mean"),
        ("eps", "tmp2m__mean"),
        ("eps", "wspd10m"),
    ],
)
def test_non_opted_vars_still_default_to_conus_only(model_id: str, var_id: str) -> None:
    plugin = MODEL_REGISTRY[model_id]

    capability = plugin.get_var_capability(var_id)
    assert capability is not None
    assert capability.supported_build_regions == []
    assert scheduler_module._build_regions_for_var(plugin, var_id) == ["conus"]