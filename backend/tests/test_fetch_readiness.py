from __future__ import annotations

import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import fetch as fetch_module


def test_product_hour_ready_allows_grib_fallback_when_idx_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeHerbie:
        calls = 0

        def __init__(self, date, **kwargs):
            del date
            type(self).calls += 1
            self.priority = kwargs.get("priority")
            self.idx = None
            self.grib = f"https://{self.priority}.example/file.grib2"

    fake_core = types.ModuleType("herbie.core")
    fake_core.Herbie = _FakeHerbie
    fake_pkg = types.ModuleType("herbie")
    fake_pkg.core = fake_core
    monkeypatch.setitem(sys.modules, "herbie", fake_pkg)
    monkeypatch.setitem(sys.modules, "herbie.core", fake_core)

    fetch_module.reset_herbie_runtime_caches_for_tests()
    monkeypatch.setenv("TWF_HERBIE_PRIORITY", "azure")

    ready = fetch_module.product_hour_has_any_idx(
        model_id="ifs",
        product="oper",
        run_date=datetime(2026, 4, 16, 12, 0),
        fh=0,
        herbie_kwargs={"priority": ["azure"]},
        allow_grib_without_idx=True,
    )

    assert ready is True
    assert _FakeHerbie.calls == 1
