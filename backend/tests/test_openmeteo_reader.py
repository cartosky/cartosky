"""Reader tests for the Open-Meteo fast path (WP1).

No network and no ``omfiles``: the block-cached filesystem is driven by a fake
HTTP session and the ``.om`` reader by a fake ``omfiles``-shaped object. The
lazy-import behaviour is asserted explicitly — the module family must import
in environments where the GPLv2 reader is not installed.
"""

from __future__ import annotations

import builtins
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from app.services.sources.openmeteo import reader as om_reader


# ---------------------------------------------------------------------------
# Lazy GPLv2 import
# ---------------------------------------------------------------------------


_IMPORT_WITHOUT_OMFILES = """
import sys

class _Blocker:
    def find_module(self, name, path=None):
        return None
    def find_spec(self, name, path=None, target=None):
        if name == "omfiles" or name.startswith("omfiles."):
            raise ImportError("No module named 'omfiles'")
        return None

sys.meta_path.insert(0, _Blocker())

import app.services.sources.openmeteo as pkg
from app.services.sources.openmeteo import catalog, grids, reader, source

assert "omfiles" not in sys.modules, "importing the package pulled in omfiles"
assert pkg.ECMWF_IFS_CATALOG.bucket_dir == "ecmwf_ifs"
assert grids.O1280_TOTAL_POINTS == 6599680
assert catalog.expected_file_count(pkg.ECMWF_IFS_CATALOG, 0) == 145
assert callable(source.fetch_frame_fields)
assert callable(reader.open_om)
print("OK")
"""


def test_module_family_imports_without_omfiles() -> None:
    """Importing the package must not require the GPLv2 reader.

    Run in a subprocess: reloading these modules in-process would rebind the
    enums other tests already hold references to.
    """
    backend_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(backend_root))

    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_WITHOUT_OMFILES],
        capture_output=True,
        text=True,
        cwd=str(backend_root),
        env=env,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_require_omfiles_error_names_the_dedicated_venv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "omfiles":
            raise ImportError("No module named 'omfiles'")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "omfiles", raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)

    with pytest.raises(ImportError) as excinfo:
        om_reader._require_omfiles()

    message = str(excinfo.value)
    assert "omfiles" in message
    assert "venv" in message
    assert "GPLv2" in message


def test_open_om_surfaces_the_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        om_reader, "_require_omfiles", lambda: (_ for _ in ()).throw(ImportError("nope"))
    )

    with pytest.raises(ImportError):
        om_reader.open_om("https://example.invalid/a.om")


# ---------------------------------------------------------------------------
# Block-cached HTTP filesystem
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.content = content
        self.headers = headers or {}
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    """Serves byte ranges out of an in-memory payload and counts requests."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.head_calls = 0
        self.range_headers: list[str] = []

    def head(self, url: str, **kwargs: object) -> _FakeResponse:
        self.head_calls += 1
        return _FakeResponse(headers={"Content-Length": str(len(self.payload))})

    def get(self, url: str, headers: dict[str, str] | None = None, **kwargs: object):
        header = (headers or {})["Range"]
        self.range_headers.append(header)
        start, end = header.removeprefix("bytes=").split("-")
        return _FakeResponse(self.payload[int(start) : int(end) + 1])

    def close(self) -> None:
        return None


@pytest.fixture()
def payload() -> bytes:
    return bytes(range(256)) * 40  # 10,240 bytes


def test_block_cache_serves_exact_ranges_and_counts_bytes(payload: bytes) -> None:
    session = _FakeSession(payload)
    fs = om_reader.BlockCachedHTTPFS(block_bytes=1024, session=session)

    assert fs.size("u") == len(payload)
    assert fs.cat_file("u", 0, 10) == payload[0:10]
    assert fs.cat_file("u", 1020, 1030) == payload[1020:1030]  # spans two blocks
    assert fs.cat_file("u") == payload

    assert fs.bytes_transferred == len(payload)  # every block fetched once
    assert fs.requests_made == 10
    assert session.head_calls == 1  # size is cached


def test_block_cache_reuses_cached_blocks(payload: bytes) -> None:
    session = _FakeSession(payload)
    fs = om_reader.BlockCachedHTTPFS(block_bytes=4096, session=session)

    first = fs.cat_file("u", 100, 200)
    requests_after_first = fs.requests_made
    second = fs.cat_file("u", 150, 250)

    assert first == payload[100:200]
    assert second == payload[150:250]
    assert fs.requests_made == requests_after_first == 1


def test_block_cache_default_block_size_is_512kb() -> None:
    assert om_reader.DEFAULT_BLOCK_BYTES == 512 * 1024
    assert om_reader.BlockCachedHTTPFS().block_bytes == 512 * 1024
    assert om_reader.BlockCachedHTTPFS(block_bytes=64 * 1024).block_bytes == 64 * 1024
    with pytest.raises(ValueError):
        om_reader.BlockCachedHTTPFS(block_bytes=0)


def test_block_cache_clear_drops_blocks_but_keeps_size(payload: bytes) -> None:
    session = _FakeSession(payload)
    fs = om_reader.BlockCachedHTTPFS(block_bytes=1024, session=session)
    fs.cat_file("u", 0, 10)

    fs.clear()
    fs.cat_file("u", 0, 10)

    assert fs.requests_made == 2
    assert session.head_calls == 1


# ---------------------------------------------------------------------------
# Shape dispatch
# ---------------------------------------------------------------------------


class _FakeChild:
    def __init__(self, name: str, values: np.ndarray | None = None, scalar=None) -> None:
        self.name = name
        self._values = values
        self._scalar = scalar
        self.is_array = values is not None
        self.reads: list[tuple] = []

    @property
    def shape(self):
        return self._values.shape

    def read_array(self, selector):
        self.reads.append(selector)
        return self._values[selector]

    def read_scalar(self):
        return self._scalar


class _FakeReader:
    def __init__(self, children: list[_FakeChild]) -> None:
        self._children = children

    @property
    def num_children(self) -> int:
        return len(self._children)

    def get_child_by_index(self, index: int) -> _FakeChild:
        return self._children[index]


def _fake_om(children: list[_FakeChild]) -> om_reader.OmFile:
    fs = om_reader.BlockCachedHTTPFS(block_bytes=1024, session=_FakeSession(b""))
    return om_reader.OmFile("https://example.invalid/x.om", fs, _FakeReader(children))


def test_list_variables_excludes_scalars() -> None:
    om = _fake_om(
        [
            _FakeChild("temperature_2m", np.zeros((1, 8), dtype=np.float32)),
            _FakeChild("precipitation", np.zeros((1, 8), dtype=np.float32)),
            _FakeChild("valid_time", None, scalar=1785909600),
        ]
    )

    assert om.list_variables() == ("precipitation", "temperature_2m")
    assert om.has_variable("temperature_2m")
    assert not om.has_variable("valid_time")
    assert om.shape("temperature_2m") == (1, 8)


def test_read_global_1d_dispatches_on_last_axis_not_ndim() -> None:
    values = np.arange(12, dtype=np.float32).reshape(1, 12)
    child = _FakeChild("temperature_2m", values)
    om = _fake_om([child])

    out = om.read_global_1d("temperature_2m", expected_points=12)

    assert out.shape == (12,)
    assert out.dtype == np.float32
    assert np.array_equal(out, np.arange(12))
    assert child.reads == [(slice(0, 1), slice(0, 12))]


def test_read_global_1d_accepts_a_flat_layout() -> None:
    om = _fake_om([_FakeChild("t", np.arange(12, dtype=np.float32))])

    assert om.read_global_1d("t", expected_points=12).shape == (12,)


def test_read_global_1d_rejects_a_point_count_mismatch() -> None:
    om = _fake_om([_FakeChild("t", np.zeros((1, 11), dtype=np.float32))])

    with pytest.raises(ValueError, match="grid mismatch"):
        om.read_global_1d("t", expected_points=12)


def test_read_global_1d_rejects_a_multi_field_array() -> None:
    om = _fake_om([_FakeChild("t", np.zeros((3, 12), dtype=np.float32))])

    with pytest.raises(ValueError, match="not a single field"):
        om.read_global_1d("t", expected_points=12)


def test_read_band_1d_reads_only_the_requested_range() -> None:
    child = _FakeChild("t", np.arange(100, dtype=np.float32).reshape(1, 100))
    om = _fake_om([child])

    out = om.read_band_1d("t", 30, 40, expected_points=100)

    assert np.array_equal(out, np.arange(30, 40))
    assert child.reads == [(slice(0, 1), slice(30, 40))]


def test_read_band_1d_validates_the_range() -> None:
    om = _fake_om([_FakeChild("t", np.zeros((1, 100), dtype=np.float32))])

    with pytest.raises(ValueError, match="out of range"):
        om.read_band_1d("t", 90, 200, expected_points=100)


def test_read_grid_2d_reads_a_native_2d_layout() -> None:
    values = np.arange(6, dtype=np.float32).reshape(2, 3)
    child = _FakeChild("t", values)
    om = _fake_om([child])

    out = om.read_grid_2d("t", 2, 3)

    assert out.shape == (2, 3)
    assert np.array_equal(out, values)
    assert child.reads == [(slice(0, 2), slice(0, 3))]


def test_read_grid_2d_reshapes_a_flat_layout() -> None:
    values = np.arange(6, dtype=np.float32).reshape(1, 6)
    om = _fake_om([_FakeChild("t", values)])

    out = om.read_grid_2d("t", 2, 3)

    assert out.shape == (2, 3)
    assert np.array_equal(out, np.arange(6).reshape(2, 3))


def test_read_grid_2d_rejects_an_incompatible_shape() -> None:
    om = _fake_om([_FakeChild("t", np.zeros((1, 7), dtype=np.float32))])

    with pytest.raises(ValueError, match="matches neither"):
        om.read_grid_2d("t", 2, 3)


def test_missing_and_scalar_variables_raise_keyerror() -> None:
    om = _fake_om([_FakeChild("valid_time", None, scalar=1)])

    with pytest.raises(KeyError, match="no variable named"):
        om.read_global_1d("temperature_2m")
    with pytest.raises(KeyError, match="is a scalar"):
        om.read_global_1d("valid_time")


def test_scalar_reads_decode_epochs_and_wkt() -> None:
    om = _fake_om(
        [
            _FakeChild("valid_time", None, scalar=1785909600),
            _FakeChild("forecast_reference_time", None, scalar=1785866400),
            _FakeChild("crs_wkt", None, scalar='GEOGCRS["Reduced Gaussian Grid"]'),
        ]
    )

    assert om.valid_time == datetime(2026, 8, 5, 6, 0, tzinfo=timezone.utc)
    assert om.forecast_reference_time == datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
    assert "Reduced Gaussian Grid" in om.crs_wkt
