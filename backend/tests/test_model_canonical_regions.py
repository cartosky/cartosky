from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.registry import MODEL_REGISTRY
from app.services import scheduler as scheduler_module


@pytest.mark.parametrize("model_id", ["gfs", "gefs", "ecmwf", "aigfs", "aifs", "eps"])
def test_global_models_use_na_as_canonical_build_region(model_id: str) -> None:
    plugin = MODEL_REGISTRY[model_id]

    assert plugin.capabilities.canonical_region == "na"
    assert scheduler_module._build_regions_for_var(plugin, "tmp2m") == ["na"]


@pytest.mark.parametrize("model_id", ["hrrr", "nam", "nbm", "mrms", "current_analysis", "spc", "nws_hazards"])
def test_regional_models_keep_existing_canonical_region(model_id: str) -> None:
    plugin = MODEL_REGISTRY[model_id]

    assert plugin.capabilities.canonical_region == "conus"
    assert scheduler_module._build_regions_for_var(plugin, "tmp2m") == ["conus"]
