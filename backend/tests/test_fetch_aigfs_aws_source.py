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


class _TemplateHost:
    """Bare stand-in for a Herbie instance during template execution."""

    def __init__(self, date: datetime, product: str, fxx: int) -> None:
        self.date = date
        self.product = product
        self.fxx = fxx
        self.get_remoteFileName = "aigfs.grib2"


def test_aigfs_template_patch_injects_aws_source(monkeypatch: pytest.MonkeyPatch) -> None:
    herbie_gfs_models = pytest.importorskip("herbie.models.gfs")

    monkeypatch.setattr(fetch_module, "_HERBIE_AIGFS_AWS_PATCH_ATTEMPTED", False)
    monkeypatch.setattr(
        herbie_gfs_models.aigfs,
        "template",
        herbie_gfs_models.aigfs.template,
    )
    fetch_module._ensure_herbie_aigfs_aws_source()

    host = _TemplateHost(datetime(2026, 7, 21, 6), "sfc", 6)
    herbie_gfs_models.aigfs.template(host)

    assert list(host.SOURCES) == ["aws", "nomads"]
    assert host.SOURCES["aws"] == (
        "https://noaa-nws-graphcastgfs-pds.s3.amazonaws.com/"
        "aigfs.20260721/06/model/atmos/grib2/aigfs.t06z.sfc.f006.grib2"
    )
    assert host.SOURCES["nomads"] == (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/aigfs/prod/"
        "aigfs.20260721/06/model/atmos/grib2/aigfs.t06z.sfc.f006.grib2"
    )

    pres_host = _TemplateHost(datetime(2026, 7, 21, 18), "pres", 384)
    herbie_gfs_models.aigfs.template(pres_host)
    assert pres_host.SOURCES["aws"] == (
        "https://noaa-nws-graphcastgfs-pds.s3.amazonaws.com/"
        "aigfs.20260721/18/model/atmos/grib2/aigfs.t18z.pres.f384.grib2"
    )


def test_aigfs_template_patch_degrades_without_herbie_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch_module, "_HERBIE_AIGFS_AWS_PATCH_ATTEMPTED", False)
    fake_pkg = types.ModuleType("herbie")
    monkeypatch.setitem(sys.modules, "herbie", fake_pkg)
    monkeypatch.delitem(sys.modules, "herbie.models", raising=False)
    monkeypatch.delitem(sys.modules, "herbie.models.gfs", raising=False)

    fetch_module._ensure_herbie_aigfs_aws_source()

    assert fetch_module._HERBIE_AIGFS_AWS_PATCH_ATTEMPTED is True
