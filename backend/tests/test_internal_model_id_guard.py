from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import fetch as fetch_module
from app.services import scheduler as scheduler_module


RUN_DT = datetime(2026, 7, 6, 18, 0)


@pytest.mark.parametrize("internal_id", ["eps", "ecmwf", "EPS", " ecmwf "])
def test_fetch_variable_rejects_internal_model_ids(internal_id: str) -> None:
    # The guard must fire before any network/Herbie work — no mocking needed.
    with pytest.raises(ValueError, match="internal CartoSky model id"):
        fetch_module.fetch_variable(
            model_id=internal_id,
            product="enfo",
            search_pattern=":gh:500:",
            run_date=RUN_DT,
            fh=6,
        )


@pytest.mark.parametrize("internal_id", ["eps", "ecmwf"])
def test_readiness_probe_rejects_internal_model_ids(internal_id: str) -> None:
    # July 6 incident path: _ensure_products_ready probed Herbie with the
    # internal id "eps". The probe must now fail loudly instead.
    with pytest.raises(ValueError, match="internal CartoSky model id"):
        fetch_module.product_hour_has_any_idx(
            model_id=internal_id,
            product="enfo",
            run_date=RUN_DT,
            fh=6,
        )


@pytest.mark.parametrize("internal_id", ["eps", "ecmwf"])
def test_inventory_lines_reject_internal_model_ids(internal_id: str) -> None:
    with pytest.raises(ValueError, match="internal CartoSky model id"):
        fetch_module.inventory_lines_for_pattern(
            model_id=internal_id,
            product="enfo",
            run_date=RUN_DT,
            fh=6,
            search_pattern=":gh:500:",
        )


class _InternalIdPlugin:
    """Plugin whose internal id differs from its Herbie model id (eps → ifs)."""

    id = "eps"
    capabilities = None

    def normalize_var_id(self, var_id: str) -> str:
        return str(var_id)

    def get_var(self, var_key: str) -> SimpleNamespace:
        del var_key
        return SimpleNamespace(
            selectors=SimpleNamespace(search=[":t:850:"], filter_by_keys={}, hints={})
        )

    def default_ensemble_view(self, var_key: str) -> None:
        del var_key
        return None

    def herbie_request(self, **kwargs) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(model="ifs", product="enfo", herbie_kwargs=None)


def test_component_precheck_fetches_with_request_model_not_internal_id(monkeypatch) -> None:
    seen: list[str] = []

    def _fake_fetch_variable(*, model_id, product, search_pattern, run_date, fh, herbie_kwargs=None):
        del product, search_pattern, run_date, fh, herbie_kwargs
        seen.append(str(model_id))
        return object(), object(), object()

    monkeypatch.setattr(scheduler_module, "fetch_variable", _fake_fetch_variable)

    available = scheduler_module._component_precheck_available(
        plugin=_InternalIdPlugin(),
        model_id="eps",
        product="enfo",
        run_dt=RUN_DT,
        fh=6,
        var_key="tmp850",
    )

    assert available is True
    # The precheck must resolve the Herbie id via plugin.herbie_request();
    # passing the raw internal id is the July 6 eps/ifs incident class.
    assert seen == ["ifs"]
