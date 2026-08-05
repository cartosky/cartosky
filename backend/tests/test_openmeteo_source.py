"""Poll/list/fetch API tests for the Open-Meteo fast path (WP1).

All HTTP is faked. The one live-bucket test is skipped unless
``CARTOSKY_OPENMETEO_LIVE_TESTS=1`` — the suite never touches the network.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import numpy as np
import pytest

from app.services.sources.openmeteo import source as om_source
from app.services.sources.openmeteo.catalog import ECMWF_IFS_CATALOG

RUN = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)

_LIST_XML_PAGE_1 = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <IsTruncated>true</IsTruncated>
  <NextContinuationToken>tok</NextContinuationToken>
  <Contents>
    <Key>data_spatial/ecmwf_ifs/2026/08/04/1800Z/2026-08-04T1900.om</Key>
    <LastModified>2026-08-04T23:31:00.000Z</LastModified>
  </Contents>
  <Contents>
    <Key>data_spatial/ecmwf_ifs/2026/08/04/1800Z/meta.json</Key>
    <LastModified>2026-08-04T23:55:08.000Z</LastModified>
  </Contents>
</ListBucketResult>
"""

_LIST_XML_PAGE_2 = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <IsTruncated>false</IsTruncated>
  <Contents>
    <Key>data_spatial/ecmwf_ifs/2026/08/04/1800Z/2026-08-04T1800.om</Key>
    <LastModified>2026-08-04T23:30:00.000Z</LastModified>
  </Contents>
  <Contents>
    <Key>data_spatial/ecmwf_ifs/2026/08/04/1800Z/not-a-timestamp.om</Key>
    <LastModified>2026-08-04T23:30:00.000Z</LastModified>
  </Contents>
</ListBucketResult>
"""


class _XmlResponse:
    def __init__(self, body: str) -> None:
        self.content = body.encode()
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


class _JsonResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")

    def json(self) -> object:
        return self._payload


class _ListSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append(dict(params or {}))
        if (params or {}).get("continuation-token") == "tok":
            return _XmlResponse(_LIST_XML_PAGE_2)
        return _XmlResponse(_LIST_XML_PAGE_1)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_run_prefix_follows_the_bucket_layout() -> None:
    assert (
        om_source.run_prefix(ECMWF_IFS_CATALOG, RUN)
        == "data_spatial/ecmwf_ifs/2026/08/04/1800Z/"
    )
    # Naive datetimes are treated as UTC.
    assert om_source.run_prefix(
        ECMWF_IFS_CATALOG, datetime(2026, 8, 4, 6)
    ) == "data_spatial/ecmwf_ifs/2026/08/04/0600Z/"


def test_list_run_objects_paginates_and_returns_fh_ordered_tuples() -> None:
    session = _ListSession()

    objects = om_source.list_run_objects(ECMWF_IFS_CATALOG, RUN, session=session)

    assert len(objects) == 2
    assert [obj.fh for obj in objects] == [0, 1]

    valid_dt, fh, url, last_modified, _etag = objects[0]
    assert valid_dt == datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
    assert fh == 0
    assert url.endswith("/2026/08/04/1800Z/2026-08-04T1800.om")
    assert last_modified == datetime(2026, 8, 4, 23, 30, tzinfo=timezone.utc)
    assert objects[0].key.startswith("data_spatial/ecmwf_ifs/")

    assert len(session.calls) == 2
    assert session.calls[0]["prefix"] == "data_spatial/ecmwf_ifs/2026/08/04/1800Z/"
    assert session.calls[1]["continuation-token"] == "tok"


def test_list_run_objects_drops_non_om_and_unparseable_keys() -> None:
    objects = om_source.list_run_objects(ECMWF_IFS_CATALOG, RUN, session=_ListSession())

    assert all(obj.url.endswith(".om") for obj in objects)
    assert all("meta.json" not in obj.url for obj in objects)
    assert all("not-a-timestamp" not in obj.url for obj in objects)


# ---------------------------------------------------------------------------
# Manifest hints
# ---------------------------------------------------------------------------


class _ManifestSession:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.urls: list[str] = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.urls.append(url)
        return _JsonResponse(self.payload, self.status_code)


_MANIFEST = {
    "completed": True,
    "crs_wkt": 'GEOGCRS["Reduced Gaussian Grid"]',
    "last_modified_time": "2026-08-04T23:55:08Z",
    "reference_time": "2026-08-04T18:00:00Z",
    "valid_times": ["2026-08-04T18:00Z", "2026-08-04T19:00Z", "garbage"],
    "variables": ["temperature_2m", "precipitation"],
}


def test_read_in_progress_parses_the_model_level_hint() -> None:
    session = _ManifestSession(_MANIFEST)

    hint = om_source.read_in_progress(ECMWF_IFS_CATALOG, session=session)

    assert session.urls[0].endswith("data_spatial/ecmwf_ifs/in-progress.json")
    assert hint is not None
    assert hint.completed is True
    assert hint.reference_time == datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
    assert len(hint.valid_times) == 2  # the unparseable entry is dropped
    assert hint.variables == ("temperature_2m", "precipitation")


def test_read_run_meta_uses_the_run_directory() -> None:
    session = _ManifestSession(_MANIFEST)

    hint = om_source.read_run_meta(ECMWF_IFS_CATALOG, RUN, session=session)

    assert session.urls[0].endswith("2026/08/04/1800Z/meta.json")
    assert hint is not None


def test_manifest_readers_return_none_on_404() -> None:
    session = _ManifestSession({}, status_code=404)

    assert om_source.read_in_progress(ECMWF_IFS_CATALOG, session=session) is None
    assert om_source.read_run_meta(ECMWF_IFS_CATALOG, RUN, session=session) is None


# ---------------------------------------------------------------------------
# Frame fetch
# ---------------------------------------------------------------------------


class _StubOmFile:
    def __init__(self, variables: dict[str, np.ndarray]) -> None:
        self._variables = variables
        self.band_reads: list[tuple[int, int]] = []
        self.global_reads: list[str] = []
        self.bytes_transferred = 1_000
        self.requests_made = 3

    def list_variables(self):
        return tuple(self._variables)

    def read_global_1d(self, name, *, expected_points=None):
        self.global_reads.append(name)
        return self._variables[name]

    def read_band_1d(self, name, start, stop, *, expected_points=None):
        self.band_reads.append((start, stop))
        return self._variables[name][start:stop]

    def read_grid_2d(self, name, nlat, nlon):
        return self._variables[name].reshape(nlat, nlon)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


@pytest.fixture()
def stub_open(monkeypatch: pytest.MonkeyPatch):
    total = ECMWF_IFS_CATALOG.source_points
    stub = _StubOmFile(
        {
            "temperature_2m": np.arange(total, dtype=np.float32),
            "wind_gusts_10m": np.zeros(total, dtype=np.float32),
        }
    )
    monkeypatch.setattr(om_source, "open_om", lambda url, fs=None: stub)
    return stub


def test_fetch_frame_fields_reads_the_full_globe_by_default(stub_open) -> None:
    fields = om_source.fetch_frame_fields(
        ECMWF_IFS_CATALOG,
        "https://example.invalid/x.om",
        ["temperature_2m", "wind_gusts_10m"],
    )

    assert set(fields) == {"temperature_2m", "wind_gusts_10m"}
    assert fields["temperature_2m"].shape == (ECMWF_IFS_CATALOG.source_points,)
    assert stub_open.global_reads == ["temperature_2m", "wind_gusts_10m"]
    assert stub_open.band_reads == []


def test_fetch_frame_fields_na_band_reads_only_the_band(stub_open) -> None:
    from app.services.sources.openmeteo import grids

    fields = om_source.fetch_frame_fields(
        ECMWF_IFS_CATALOG,
        "https://example.invalid/x.om",
        ["temperature_2m"],
        window="na_band",
    )

    start, stop = grids.na_band_index_range()
    assert stub_open.band_reads == [(start, stop)]
    assert fields["temperature_2m"].shape == (stop - start,)
    assert fields["temperature_2m"][0] == pytest.approx(start)


def test_fetch_frame_fields_omits_variables_absent_from_the_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FH000 legitimately carries no accumulation or gust fields."""
    stub = _StubOmFile({"temperature_2m": np.zeros(4, dtype=np.float32)})
    monkeypatch.setattr(om_source, "open_om", lambda url, fs=None: stub)

    fields = om_source.fetch_frame_fields(
        ECMWF_IFS_CATALOG,
        "https://example.invalid/fh000.om",
        ["temperature_2m", "precipitation", "wind_gusts_10m"],
    )

    assert set(fields) == {"temperature_2m"}


def test_fetch_frame_fields_rejects_uncatalogued_and_disabled_variables(
    stub_open,
) -> None:
    with pytest.raises(KeyError, match="cloud_cover"):
        om_source.fetch_frame_fields(
            ECMWF_IFS_CATALOG, "https://example.invalid/x.om", ["cloud_cover"]
        )
    with pytest.raises(ValueError, match="disabled in the catalog"):
        om_source.fetch_frame_fields(
            ECMWF_IFS_CATALOG, "https://example.invalid/x.om", ["snow_depth"]
        )


def test_fetch_frame_fields_rejects_an_unknown_window(stub_open) -> None:
    with pytest.raises(ValueError, match="unknown fetch window"):
        om_source.fetch_frame_fields(
            ECMWF_IFS_CATALOG,
            "https://example.invalid/x.om",
            ["temperature_2m"],
            window="conus",  # type: ignore[arg-type]
        )


def test_ladder_helpers_are_re_exported_from_source() -> None:
    assert om_source.expected_file_count(ECMWF_IFS_CATALOG, 0) == 145
    assert om_source.expected_fhs(ECMWF_IFS_CATALOG, 18)[-1] == 144
    assert len(om_source.aggregation_boundaries(ECMWF_IFS_CATALOG, 0)) == 84


# ---------------------------------------------------------------------------
# Optional live-bucket smoke test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("CARTOSKY_OPENMETEO_LIVE_TESTS") != "1",
    reason="live bucket test; set CARTOSKY_OPENMETEO_LIVE_TESTS=1 to run",
)
def test_live_bucket_in_progress_hint_is_readable() -> None:  # pragma: no cover
    hint = om_source.read_in_progress(ECMWF_IFS_CATALOG)

    assert hint is not None
    assert "temperature_2m" in hint.variables
    assert hint.reference_time is not None
