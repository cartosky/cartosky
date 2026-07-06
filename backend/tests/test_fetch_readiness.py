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


def test_product_hour_probe_fails_closed_on_unclassified_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unclassified Herbie error must never be read as "run exists".

    Reproduces the 2026-07-06 incident: herbie's azure source lookup raises
    ``KeyError('href')`` when the blob is absent (and a NOMADS 403 block
    raises similarly unclassified errors); the probe previously failed OPEN
    and invented a phantom GEFS 18z run ~90 minutes early.
    """

    class _FakeHerbie:
        calls = 0

        def __init__(self, date, **kwargs):
            del date, kwargs
            type(self).calls += 1
            raise KeyError("href")

    fake_core = types.ModuleType("herbie.core")
    fake_core.Herbie = _FakeHerbie
    fake_pkg = types.ModuleType("herbie")
    fake_pkg.core = fake_core
    monkeypatch.setitem(sys.modules, "herbie", fake_pkg)
    monkeypatch.setitem(sys.modules, "herbie.core", fake_core)

    fetch_module.reset_herbie_runtime_caches_for_tests()

    ready = fetch_module.product_hour_has_any_idx(
        model_id="gefs",
        product="atmos.5",
        run_date=datetime(2026, 7, 6, 18, 0),
        fh=6,
        herbie_kwargs={"priority": ["azure"]},
    )

    assert ready is False
    assert _FakeHerbie.calls == 1


def test_product_hour_probe_errored_priority_falls_through_to_next(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blocked/erroring priority is skipped, not trusted: a later priority
    with a real idx still reports ready."""

    class _FakeHerbie:
        calls: list[str] = []

        def __init__(self, date, **kwargs):
            del date
            priority = str(kwargs.get("priority"))
            type(self).calls.append(priority)
            if priority == "nomads":
                raise RuntimeError("403 Forbidden (blocked)")
            self.priority = priority
            self.idx = f"https://{priority}.example/file.grib2.idx"
            self.grib = f"https://{priority}.example/file.grib2"

    fake_core = types.ModuleType("herbie.core")
    fake_core.Herbie = _FakeHerbie
    fake_pkg = types.ModuleType("herbie")
    fake_pkg.core = fake_core
    monkeypatch.setitem(sys.modules, "herbie", fake_pkg)
    monkeypatch.setitem(sys.modules, "herbie.core", fake_core)

    fetch_module.reset_herbie_runtime_caches_for_tests()

    ready = fetch_module.product_hour_has_any_idx(
        model_id="gefs",
        product="atmos.5",
        run_date=datetime(2026, 7, 6, 18, 0),
        fh=6,
        herbie_kwargs={"priority": ["nomads", "aws"]},
    )

    assert ready is True
    assert _FakeHerbie.calls == ["nomads", "aws"]
