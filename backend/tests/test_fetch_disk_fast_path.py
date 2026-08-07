from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.builder import fetch as fetch_module


RUN_DATE = datetime(2026, 8, 7, 12, 0)
SEARCH_PATTERN = ":TMP:2 m above ground:"
MODEL_ID = "hrrr"
PRODUCT = "sfc"
FH = 3


# ---------------------------------------------------------------------------
# Phase 1: disk-first fast path
# ---------------------------------------------------------------------------


class _FakeHerbie:
    """Minimal Herbie stand-in whose subset path is a pre-planted file."""

    def __init__(self, subset_path: Path) -> None:
        self._subset_path = subset_path

    def get_localFilePath(self, _search_pattern: str) -> Path:
        return self._subset_path


def _install_fake_fetch_stack(
    monkeypatch: pytest.MonkeyPatch,
    subset_path: Path,
    *,
    construct_calls: list[str],
    precheck_calls: list[str],
) -> None:
    monkeypatch.setattr(fetch_module, "_import_herbie", lambda: object)

    def _fake_construct(_herbie_type, _herbie_date, run_kwargs):
        construct_calls.append(str(run_kwargs.get("priority", "")))
        return _FakeHerbie(subset_path)

    def _fake_precheck(_H, **kwargs):
        precheck_calls.append(str(kwargs.get("priority", "")))
        return True, "ok"

    monkeypatch.setattr(fetch_module, "_construct_herbie", _fake_construct)
    monkeypatch.setattr(fetch_module, "_precheck_subset_available", _fake_precheck)
    monkeypatch.setattr(
        fetch_module,
        "_inventory_meta_from_herbie",
        lambda _H, **kwargs: {
            "inventory_line": "1:0:d=2026080712:TMP:2 m above ground:3 hour fcst:",
            "search_pattern": str(kwargs.get("search_pattern", "")),
            "fh": int(kwargs.get("fh", 0)),
            "product": str(kwargs.get("product", "")),
        },
    )
    monkeypatch.setattr(
        fetch_module,
        "_read_grib_raster",
        lambda _path: (np.zeros((2, 2), dtype=np.float32), "crs", "transform"),
    )


def _fetch(**overrides):
    kwargs = {
        "model_id": MODEL_ID,
        "product": PRODUCT,
        "search_pattern": SEARCH_PATTERN,
        "run_date": RUN_DATE,
        "fh": FH,
        "herbie_kwargs": {"priority": ["aws", "nomads"]},
    }
    kwargs.update(overrides)
    return fetch_module.fetch_variable(**kwargs)


def test_second_fetch_skips_herbie_construction_and_precheck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    subset_path = tmp_path / "subset.grib2"
    subset_path.write_bytes(b"GRIB-bytes")
    construct_calls: list[str] = []
    precheck_calls: list[str] = []
    _install_fake_fetch_stack(
        monkeypatch,
        subset_path,
        construct_calls=construct_calls,
        precheck_calls=precheck_calls,
    )

    _fetch()
    assert construct_calls == ["aws"]
    assert precheck_calls == ["aws"]

    def _forbidden_construct(*_args, **_kwargs):
        raise AssertionError("fast path must not construct Herbie")

    def _forbidden_precheck(*_args, **_kwargs):
        raise AssertionError("fast path must not precheck the remote index")

    monkeypatch.setattr(fetch_module, "_construct_herbie", _forbidden_construct)
    monkeypatch.setattr(fetch_module, "_precheck_subset_available", _forbidden_precheck)

    with caplog.at_level("INFO", logger=fetch_module.logger.name):
        data, crs, transform, meta = _fetch(return_meta=True)

    assert data.shape == (2, 2)
    assert (crs, transform) == ("crs", "transform")
    # Inventory meta captured on the slow path is replayed verbatim.
    assert meta["inventory_line"].endswith("3 hour fcst:")
    assert any("fast_path=true" in record.getMessage() for record in caplog.records)
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("subset_disk_fast_path_hit", 0) == 1


def test_fast_path_probes_priorities_in_preference_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    subset_path = tmp_path / "subset.grib2"
    subset_path.write_bytes(b"GRIB-bytes")
    construct_calls: list[str] = []
    precheck_calls: list[str] = []
    _install_fake_fetch_stack(
        monkeypatch,
        subset_path,
        construct_calls=construct_calls,
        precheck_calls=precheck_calls,
    )

    # Only the second-preference source resolved on the slow path.
    _fetch(herbie_kwargs={"priority": ["nomads"]})
    assert construct_calls == ["nomads"]

    monkeypatch.setattr(
        fetch_module,
        "_construct_herbie",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not construct Herbie")),
    )
    # "aws" has no memo entry, so the probe must fall through to "nomads".
    _fetch(herbie_kwargs={"priority": ["aws", "nomads"]})
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("subset_disk_fast_path_hit", 0) == 1


def test_extra_herbie_kwargs_do_not_share_a_memo_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """member="mean" and member=1 resolve to different subset files.

    Herbie folds the remaining kwargs into get_localFilePath(), so a memo key
    that ignored them would replay the wrong file (and the wrong inventory meta)
    across GEFS member/mean views.
    """
    fetch_module.reset_herbie_runtime_caches_for_tests()
    paths = {
        "mean": tmp_path / "subset-mean.grib2",
        "1": tmp_path / "subset-member1.grib2",
    }
    for path in paths.values():
        path.write_bytes(b"GRIB-bytes")

    construct_calls: list[str] = []
    read_paths: list[Path] = []

    def _fake_construct(_herbie_type, _herbie_date, run_kwargs):
        member = str(run_kwargs.get("member", ""))
        construct_calls.append(member)
        return _FakeHerbie(paths[member])

    monkeypatch.setattr(fetch_module, "_import_herbie", lambda: object)
    monkeypatch.setattr(fetch_module, "_construct_herbie", _fake_construct)
    monkeypatch.setattr(fetch_module, "_precheck_subset_available", lambda _H, **_k: (True, "ok"))
    monkeypatch.setattr(
        fetch_module,
        "_inventory_meta_from_herbie",
        lambda _H, **kwargs: {
            "inventory_line": f"member={kwargs.get('product', '')}",
            "search_pattern": str(kwargs.get("search_pattern", "")),
            "fh": int(kwargs.get("fh", 0)),
            "product": str(kwargs.get("product", "")),
        },
    )

    def _fake_read(path):
        read_paths.append(Path(path))
        return np.zeros((2, 2), dtype=np.float32), "crs", "transform"

    monkeypatch.setattr(fetch_module, "_read_grib_raster", _fake_read)

    _fetch(herbie_kwargs={"priority": ["aws"], "member": "mean"})
    _fetch(herbie_kwargs={"priority": ["aws"], "member": 1})

    # The second fetch differs only in `member`, so it must resolve its own path
    # rather than replaying the memoized "mean" subset.
    assert construct_calls == ["mean", "1"]
    assert read_paths == [paths["mean"], paths["1"]]
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("subset_disk_fast_path_hit", 0) == 0

    # Repeating the first variant still takes the fast path.
    monkeypatch.setattr(
        fetch_module,
        "_construct_herbie",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not construct Herbie")),
    )
    _fetch(herbie_kwargs={"priority": ["aws"], "member": "mean"})
    assert read_paths[-1] == paths["mean"]
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("subset_disk_fast_path_hit", 0) == 1


@pytest.mark.parametrize("mode", ["missing", "zero_byte"])
def test_invalid_cached_file_falls_through_to_normal_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    subset_path = tmp_path / "subset.grib2"
    subset_path.write_bytes(b"GRIB-bytes")
    construct_calls: list[str] = []
    precheck_calls: list[str] = []
    _install_fake_fetch_stack(
        monkeypatch,
        subset_path,
        construct_calls=construct_calls,
        precheck_calls=precheck_calls,
    )

    _fetch()
    assert len(construct_calls) == 1

    if mode == "missing":
        subset_path.unlink()
        # The normal path re-downloads; the fake Herbie only hands back a path,
        # so re-plant the file to let the download branch find it.
        monkeypatch.setattr(
            fetch_module,
            "_download_herbie_subset_isolated",
            lambda _H, **kwargs: (kwargs["subset_hint"].write_bytes(b"GRIB-bytes"), kwargs["subset_hint"])[1],
        )
    else:
        subset_path.write_bytes(b"")
        monkeypatch.setattr(
            fetch_module,
            "_download_herbie_subset_isolated",
            lambda _H, **kwargs: (kwargs["subset_hint"].write_bytes(b"GRIB-bytes"), kwargs["subset_hint"])[1],
        )

    _fetch()
    assert len(construct_calls) == 2
    assert len(precheck_calls) == 2
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("subset_disk_fast_path_hit", 0) == 0


def test_kill_switch_disables_the_fast_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    subset_path = tmp_path / "subset.grib2"
    subset_path.write_bytes(b"GRIB-bytes")
    construct_calls: list[str] = []
    precheck_calls: list[str] = []
    _install_fake_fetch_stack(
        monkeypatch,
        subset_path,
        construct_calls=construct_calls,
        precheck_calls=precheck_calls,
    )

    _fetch()
    monkeypatch.setenv("CARTOSKY_FETCH_DISK_FAST_PATH", "0")
    _fetch()

    assert len(construct_calls) == 2
    assert len(precheck_calls) == 2
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("subset_disk_fast_path_hit", 0) == 0


# ---------------------------------------------------------------------------
# Phase 2: decoded-field cache
# ---------------------------------------------------------------------------


class _FakeDataset:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _install_fake_decoder(
    monkeypatch: pytest.MonkeyPatch, *, elements: int = 4
) -> list[str]:
    decoded: list[str] = []
    opened: list[str] = []

    def _fake_open(source, *_args, **_kwargs):
        opened.append(str(source))
        return _FakeDataset()

    def _fake_read(_src):
        decoded.append("decode")
        return np.arange(elements, dtype=np.float32), "crs", "transform"

    monkeypatch.setattr(fetch_module.rasterio, "open", _fake_open)
    monkeypatch.setattr(fetch_module, "_read_rasterio_dataset", _fake_read)
    return decoded


def test_repeat_read_is_served_from_the_decoded_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    decoded = _install_fake_decoder(monkeypatch)
    grib = tmp_path / "a.grib2"
    grib.write_bytes(b"GRIB-a")

    first = fetch_module._read_grib_raster(grib)
    second = fetch_module._read_grib_raster(grib)

    assert len(decoded) == 1
    assert first[0] is second[0]
    assert not first[0].flags.writeable
    with pytest.raises(ValueError):
        first[0][0] = 1.0

    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("decoded_grib_cache_miss", 0) == 1
    assert metrics["counters"].get("decoded_grib_cache_hit", 0) == 1


def test_mtime_or_size_change_invalidates_the_decoded_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    decoded = _install_fake_decoder(monkeypatch)
    grib = tmp_path / "a.grib2"
    grib.write_bytes(b"GRIB-a")

    fetch_module._read_grib_raster(grib)
    assert len(decoded) == 1

    grib.write_bytes(b"GRIB-a-longer-payload")
    fetch_module._read_grib_raster(grib)
    assert len(decoded) == 2


def test_byte_cap_evicts_least_recently_used_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    # 65536 float32 == 256 KiB per entry; a 1 MiB cap holds exactly four.
    _install_fake_decoder(monkeypatch, elements=65536)
    monkeypatch.setenv("CARTOSKY_DECODED_CACHE_MB", "1")

    paths = []
    for index in range(5):
        path = tmp_path / f"{index}.grib2"
        path.write_bytes(b"GRIB" + bytes([index]))
        paths.append(path)

    for path in paths[:4]:
        fetch_module._read_grib_raster(path)
    assert len(fetch_module._DECODED_GRIB_CACHE) == 4

    # Touch the oldest so it is no longer the LRU victim.
    fetch_module._read_grib_raster(paths[0])
    fetch_module._read_grib_raster(paths[4])

    cached_paths = {key[0] for key in fetch_module._DECODED_GRIB_CACHE}
    assert len(fetch_module._DECODED_GRIB_CACHE) == 4
    assert str(paths[1].resolve()) not in cached_paths
    assert str(paths[0].resolve()) in cached_paths
    assert str(paths[4].resolve()) in cached_paths
    metrics = fetch_module.get_herbie_runtime_metrics_for_tests()
    assert metrics["counters"].get("decoded_grib_cache_evicted", 0) == 1


def test_zero_megabytes_disables_the_decoded_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    decoded = _install_fake_decoder(monkeypatch)
    monkeypatch.setenv("CARTOSKY_DECODED_CACHE_MB", "0")
    grib = tmp_path / "a.grib2"
    grib.write_bytes(b"GRIB-a")

    first = fetch_module._read_grib_raster(grib)
    second = fetch_module._read_grib_raster(grib)

    assert len(decoded) == 2
    assert first[0] is not second[0]
    assert first[0].flags.writeable
    assert len(fetch_module._DECODED_GRIB_CACHE) == 0


def test_concurrent_readers_get_consistent_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fetch_module.reset_herbie_runtime_caches_for_tests()
    started = threading.Barrier(8)
    counter = {"decodes": 0}
    lock = threading.Lock()

    def _fake_open(_source, *_args, **_kwargs):
        return _FakeDataset()

    def _fake_read(_src):
        with lock:
            counter["decodes"] += 1
        return np.arange(16, dtype=np.float32), "crs", "transform"

    monkeypatch.setattr(fetch_module.rasterio, "open", _fake_open)
    monkeypatch.setattr(fetch_module, "_read_rasterio_dataset", _fake_read)

    grib = tmp_path / "a.grib2"
    grib.write_bytes(b"GRIB-a")

    def _read(_index: int):
        started.wait(timeout=5.0)
        return fetch_module._read_grib_raster(grib)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_read, range(8)))

    expected = np.arange(16, dtype=np.float32)
    for data, crs, transform in results:
        assert np.array_equal(data, expected)
        assert (crs, transform) == ("crs", "transform")
        assert not data.flags.writeable
    assert len(fetch_module._DECODED_GRIB_CACHE) == 1
    assert counter["decodes"] <= 8
