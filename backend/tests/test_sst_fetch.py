"""SST upstream fetch unit tests — no live network.

The two gotchas that bit the spike on prod are the ones pinned here: the
non-default User-Agent (ERDDAP 403s ``python-requests``) and per-file
Kelvin-vs-Celsius detection (the ERDDAP DN dataset serves ``degree_C`` while
the NCEI archive granule for the same day serves ``kelvin``).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import sst_fetch  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", chunks: list[bytes] | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}
        self._chunks = chunks or []

    def iter_content(self, chunk_size: int = 0):  # noqa: ARG002
        yield from self._chunks

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


# ---------------------------------------------------------------------------
# Units auto-detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("units", "expected"),
    [
        ("degree_C", False),
        ("degrees_C", False),
        ("celsius", False),
        ("C", False),
        ("kelvin", True),
        ("Kelvin", True),
        ("K", True),
        ("degrees_K", True),
    ],
)
def test_detect_kelvin_prefers_the_units_attribute(units: str, expected: bool) -> None:
    # Magnitudes deliberately contradict the attribute: the attribute wins.
    values = np.array([290.0, 295.0, 300.0], dtype=np.float32)
    is_kelvin, reason = sst_fetch.detect_kelvin(units, values)
    assert is_kelvin is expected
    assert "units attribute" in reason


def test_detect_kelvin_magnitude_fallback_when_attribute_missing() -> None:
    kelvin_like = np.array([271.5, 290.0, 305.0], dtype=np.float32)
    celsius_like = np.array([-1.6, 17.0, 31.8], dtype=np.float32)

    is_kelvin, reason = sst_fetch.detect_kelvin(None, kelvin_like)
    assert is_kelvin is True
    assert "magnitude fallback" in reason

    is_kelvin, reason = sst_fetch.detect_kelvin("", celsius_like)
    assert is_kelvin is False
    assert "magnitude fallback" in reason


def test_detect_kelvin_unrecognised_attribute_falls_back_to_magnitude() -> None:
    is_kelvin, reason = sst_fetch.detect_kelvin(
        "some_unknown_unit", np.array([288.0, 291.0], dtype=np.float32)
    )
    assert is_kelvin is True
    assert "magnitude fallback" in reason


def test_detect_kelvin_all_nan_assumes_celsius() -> None:
    is_kelvin, reason = sst_fetch.detect_kelvin(None, np.array([np.nan, np.nan], dtype=np.float32))
    assert is_kelvin is False
    assert "no finite values" in reason


# ---------------------------------------------------------------------------
# URLs + User-Agent
# ---------------------------------------------------------------------------

def test_user_agent_is_not_the_requests_default() -> None:
    agent = sst_fetch.HTTP_HEADERS["User-Agent"]
    assert agent == "CartoSky-SST/1.0"
    assert "python-requests" not in agent.lower()


def test_erddap_url_shape_matches_the_verified_prod_request() -> None:
    url = sst_fetch.erddap_griddap_url(datetime(2026, 8, 4, tzinfo=timezone.utc))
    assert url == (
        "https://coastwatch.noaa.gov/erddap/griddap/noaacwBLENDEDsstDNDaily.nc"
        "?analysed_sst[(2026-08-04T12:00:00Z)]"
        "[(-89.975):(89.975)][(-179.975):(179.975)]"
    )


def test_ncei_fallback_url_uses_day_of_year_directory() -> None:
    url = sst_fetch.ncei_archive_url(datetime(2026, 8, 4, tzinfo=timezone.utc))
    assert url == (
        "https://www.ncei.noaa.gov/data/oceans/ghrsst/L4/GLOB/OSPO/Geo_Polar_Blended/"
        "2026/216/20260804000000-OSPO-L4_GHRSST-SSTfnd-Geo_Polar_Blended-GLOB-v02.0-fv01.0.nc"
    )


def test_normalize_day_pins_valid_time_to_1200z() -> None:
    normalized = sst_fetch.normalize_day(datetime(2026, 8, 4, 3, 27, 9, tzinfo=timezone.utc))
    assert normalized == datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Cheap availability probe
# ---------------------------------------------------------------------------

_ERDDAP_TIME_PAYLOAD = json.dumps(
    {
        "table": {
            "columnNames": ["time"],
            "columnTypes": ["String"],
            "columnUnits": ["UTC"],
            "rows": [["2026-08-04T12:00:00Z"]],
        }
    }
)


def test_probe_uses_the_cheap_time_axis_query_with_the_ua(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append((url, dict(kwargs)))
        return _FakeResponse(200, text=_ERDDAP_TIME_PAYLOAD)

    def fail_head(*args: object, **kwargs: object) -> _FakeResponse:
        raise AssertionError("HEAD fallback must not run when the ERDDAP probe answers")

    monkeypatch.setattr(sst_fetch.requests, "get", fake_get)
    monkeypatch.setattr(sst_fetch.requests, "head", fail_head)

    newest = sst_fetch.probe_latest_available_day()

    assert newest == datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == sst_fetch.erddap_time_probe_url()
    assert "time%5Blast%5D" in url
    # A probe must not pull the ~104 MB grid subset.
    assert ".nc?" not in url
    assert kwargs["headers"]["User-Agent"] == "CartoSky-SST/1.0"


def test_probe_falls_back_to_ncei_head_when_erddap_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, **kwargs: object) -> _FakeResponse:  # noqa: ARG001
        return _FakeResponse(403)

    head_urls: list[str] = []

    def fake_head(url: str, **kwargs: object) -> _FakeResponse:
        head_urls.append(url)
        # Only the day-before-yesterday granule exists.
        return _FakeResponse(200 if "20260804" in url else 404)

    monkeypatch.setattr(sst_fetch.requests, "get", fake_get)
    monkeypatch.setattr(sst_fetch.requests, "head", fake_head)

    newest = sst_fetch.probe_latest_available_day(
        now=datetime(2026, 8, 6, 6, 0, tzinfo=timezone.utc)
    )

    assert newest == datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    assert head_urls  # the fallback actually probed
    assert all(url.endswith(".nc") for url in head_urls)


def test_probe_returns_none_when_nothing_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sst_fetch.requests, "get", lambda *a, **k: _FakeResponse(500)  # noqa: ARG005
    )
    monkeypatch.setattr(
        sst_fetch.requests, "head", lambda *a, **k: _FakeResponse(404)  # noqa: ARG005
    )
    assert sst_fetch.probe_latest_available_day() is None


# ---------------------------------------------------------------------------
# Coastal fringe fill (pre-warp edge handling)
# ---------------------------------------------------------------------------

def _coast_grid() -> np.ndarray:
    """A 12-column strip: ocean on the left, a wide NaN 'landmass' on the right.

    Columns 0-2 are ocean, 3-11 are NaN. With a 3-cell reach, columns 3-5 are the
    coastal fringe and columns 6-11 are 'deep inland' that must stay NaN.
    """
    grid = np.full((5, 12), np.nan, dtype=np.float32)
    grid[:, 0] = 10.0
    grid[:, 1] = 12.0
    grid[:, 2] = 14.0
    return grid


def test_fringe_fill_extends_valid_data_into_the_coastal_band() -> None:
    filled = sst_fetch.fill_coastal_fringe(_coast_grid(), radius_cells=3)
    # The three cells adjacent to the ocean edge are now covered...
    assert np.isfinite(filled[:, 3]).all()
    assert np.isfinite(filled[:, 4]).all()
    assert np.isfinite(filled[:, 5]).all()
    # ...and nothing beyond the reach is.
    assert np.isnan(filled[:, 6:]).all()


def test_fringe_fill_leaves_deep_interior_nan_alone() -> None:
    """A NaN region far from any valid cell must never acquire a value."""
    grid = np.full((9, 9), np.nan, dtype=np.float32)
    grid[0, 0] = 20.0  # one lonely ocean cell in the corner
    filled = sst_fetch.fill_coastal_fringe(grid, radius_cells=3)
    # Reach is 3 cells (Chebyshev distance) from the seed, nothing further.
    assert np.isfinite(filled[3, 3])
    assert np.isnan(filled[8, 8])
    assert np.isnan(filled[4, 4])


def test_fringe_fill_values_track_local_neighbours_not_a_global_constant() -> None:
    """Filled cells must carry the LOCAL coastal value, not one flooded constant.

    A laterally uniform edge correctly extends as a plateau, so the discriminating
    case is a coast with an along-shore gradient: each filled cell has to follow
    the ocean beside it.
    """
    grid = np.full((5, 8), np.nan, dtype=np.float32)
    along_shore = np.array([2.0, 8.0, 14.0, 20.0, 26.0], dtype=np.float32)
    grid[:, 0] = along_shore
    grid[:, 1] = along_shore

    filled = sst_fetch.fill_coastal_fringe(grid, radius_cells=3)
    fringe = filled[:, 2]

    # Not a constant flood: the fringe varies along the shore...
    assert len(np.unique(fringe)) > 1
    # ...monotonically, in the same direction as the coast beside it...
    assert np.all(np.diff(fringe) > 0)
    # ...each cell far nearer its own neighbour than the field's global mean...
    global_mean = float(np.nanmean(grid))
    for row in (0, 4):
        assert abs(fringe[row] - along_shore[row]) < abs(fringe[row] - global_mean)
    # ...and every filled value stays inside the source's physical envelope.
    finite = filled[np.isfinite(filled)]
    assert finite.min() >= float(along_shore.min())
    assert finite.max() <= float(along_shore.max())


def test_fringe_fill_extends_a_uniform_edge_as_a_plateau() -> None:
    """The complement: with no along-shore variation the edge value carries out."""
    filled = sst_fetch.fill_coastal_fringe(_coast_grid(), radius_cells=3)
    for column in (3, 4, 5):
        assert filled[2, column] == pytest.approx(14.0, abs=1e-4)


def test_fringe_fill_preserves_existing_values_and_does_not_mutate_input() -> None:
    grid = _coast_grid()
    original = grid.copy()
    filled = sst_fetch.fill_coastal_fringe(grid, radius_cells=3)
    np.testing.assert_array_equal(grid, original, err_msg="input was mutated")
    # Real ocean cells are untouched.
    np.testing.assert_allclose(filled[:, :3], original[:, :3])


def test_fringe_fill_does_not_reach_an_enclosed_masked_lake() -> None:
    """Great Lakes behaviour: enclosed water with no valid cell in reach stays empty.

    Not special-cased anywhere — dilation only spreads *from* finite cells, and a
    lake the producer masked out is surrounded by masked land.
    """
    grid = np.full((7, 20), np.nan, dtype=np.float32)
    grid[:, 0] = 15.0            # ocean on the far west
    lake = (slice(2, 5), slice(12, 16))  # enclosed, >3 cells from any ocean cell
    filled = sst_fetch.fill_coastal_fringe(grid, radius_cells=3)
    assert np.isnan(filled[lake]).all()


def test_fringe_fill_radius_zero_is_a_passthrough() -> None:
    grid = _coast_grid()
    filled = sst_fetch.fill_coastal_fringe(grid, radius_cells=0)
    np.testing.assert_array_equal(np.isnan(filled), np.isnan(grid))


@pytest.mark.parametrize("grid", [np.full((4, 4), np.nan, dtype=np.float32), np.full((4, 4), 8.0, dtype=np.float32)])
def test_fringe_fill_handles_degenerate_fields(grid: np.ndarray) -> None:
    filled = sst_fetch.fill_coastal_fringe(grid, radius_cells=3)
    np.testing.assert_array_equal(np.isnan(filled), np.isnan(grid))


def test_fringe_reach_grows_with_radius() -> None:
    covered = [
        int(np.isfinite(sst_fetch.fill_coastal_fringe(_coast_grid(), radius_cells=r)).sum())
        for r in (0, 1, 2, 3)
    ]
    assert covered == sorted(covered)
    assert covered[0] < covered[-1]
    # The shipped default is what the na 9 km target cell needs.
    assert sst_fetch.SST_COASTAL_FRINGE_SOURCE_CELLS == 3


# ---------------------------------------------------------------------------
# Download path ordering
# ---------------------------------------------------------------------------

def test_fetch_falls_back_to_the_archive_when_erddap_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested: list[str] = []

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        requested.append(url)
        assert kwargs["headers"]["User-Agent"] == "CartoSky-SST/1.0"
        if "erddap" in url:
            return _FakeResponse(403)
        return _FakeResponse(200, chunks=[b"netcdf-bytes"])

    monkeypatch.setattr(sst_fetch.requests, "get", fake_get)
    monkeypatch.setattr(sst_fetch.time, "sleep", lambda _seconds: None)

    source = sst_fetch.fetch_sst_day(
        datetime(2026, 8, 4, tzinfo=timezone.utc), download_dir=tmp_path
    )

    assert source.label == "ncei-ghrsst-geopolar-dn"
    assert source.variable == "analysed_sst"
    assert source.valid_time == datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    assert source.path.read_bytes() == b"netcdf-bytes"
    assert source.bytes_downloaded == len(b"netcdf-bytes")
    # ERDDAP was tried first, and retried, before the archive.
    assert "erddap" in requested[0]
    assert requested[-1] == sst_fetch.ncei_archive_url(datetime(2026, 8, 4, tzinfo=timezone.utc))


def test_fetch_raises_when_no_path_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sst_fetch.requests, "get", lambda *a, **k: _FakeResponse(404)  # noqa: ARG005
    )
    monkeypatch.setattr(sst_fetch.time, "sleep", lambda _seconds: None)
    with pytest.raises(sst_fetch.SSTFetchError):
        sst_fetch.fetch_sst_day(datetime(2026, 8, 4, tzinfo=timezone.utc), download_dir=tmp_path)
