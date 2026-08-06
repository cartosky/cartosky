"""SST poller cycle logic — probe-driven, network mocked.

The behaviours pinned here are the ones that decide whether the poller is safe
to leave running unattended: it never downloads to answer "is there a new day?",
a first-ever start walks back one bundle window, a fetch miss keeps the last
known good bundle instead of crashing the loop, and retention is applied per
domain.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import sst_fetch, sst_poller, sst_publish  # noqa: E402
from app.services.builder.raster_grid import TargetGrid  # noqa: E402

_STUB_TRANSFORM = from_origin(-101.0, 46.0, 1.0, 1.0)


def _stub_grid(model: str, region: str) -> TargetGrid:
    del model
    return TargetGrid(
        crs="EPSG:4326" if region == "global" else "EPSG:3857",
        bbox=(-101.0, 44.0, -98.0, 46.0),
        resolution=1.0,
        transform=_STUB_TRANSFORM,
        height=2,
        width=3,
    )


def _day(dom: int) -> datetime:
    return datetime(2026, 8, dom, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def stubbed_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", raising=False)
    monkeypatch.setattr(sst_publish, "get_target_grid", _stub_grid)
    monkeypatch.setattr(
        sst_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), _STUB_TRANSFORM),
    )


def _config(tmp_path: Path, *, bundle_frame_count: int = 3, keep_runs: int = 6) -> sst_poller.SSTPollerConfig:
    return sst_poller.SSTPollerConfig(
        data_root=tmp_path,
        poll_seconds=3600,
        keep_runs=keep_runs,
        timeout_seconds=60.0,
        bundle_frame_count=bundle_frame_count,
    )


def _sst_values() -> np.ndarray:
    return np.array([[10.0, 11.0, 12.0], [13.0, np.nan, 15.0]], dtype=np.float32)


def _anom_values() -> np.ndarray:
    # Signed by construction, including negatives — the whole point of the anomaly.
    return np.array([[-1.5, -0.5, 0.5], [1.5, np.nan, 2.5]], dtype=np.float32)


def _install_fake_upstream(
    monkeypatch: pytest.MonkeyPatch,
    *,
    newest: datetime | None,
    available_days: set[int] | None = None,
    anomaly_days: set[int] | None = None,
) -> dict[str, list[datetime]]:
    """Stub probe + per-day fetch for BOTH variables.

    ``anomaly_days`` is the set of day-of-month values whose CRW anomaly upstream
    has published; ``None`` means "every day the SST has". Returns
    ``{"sst": [...], "sst_anom": [...]}`` of days actually fetched, so a test can
    assert that an already-published SST day is never re-downloaded.
    """
    fetched: dict[str, list[datetime]] = {"sst": [], "sst_anom": []}

    monkeypatch.setattr(
        sst_poller.sst_fetch, "probe_latest_available_day", lambda **kwargs: newest  # noqa: ARG005
    )

    def anomaly_ready(day: datetime) -> bool:
        return anomaly_days is None or day.day in anomaly_days

    def fake_anomaly_frame(
        day: datetime, *, download_dir: Path, timeout: tuple[float, float], probe_timeout: tuple[float, float]
    ):
        del download_dir, timeout, probe_timeout
        if not anomaly_ready(day):
            return None
        fetched["sst_anom"].append(day)
        return sst_publish.SSTBundleFrame(
            valid_time=day,
            source_transform=_STUB_TRANSFORM,
            anomaly_values=_anom_values(),
            anomaly_source_url=f"https://example.invalid/anom/{day:%Y%m%d}.nc",
            anomaly_source_filename=f"ssta_{day:%Y%m%d}.nc",
        )

    def fake_frame(
        day: datetime,
        *,
        download_dir: Path,
        timeout: tuple[float, float],
        probe_timeout: tuple[float, float],
        with_anomaly: bool,
    ):
        del download_dir, timeout, probe_timeout
        if available_days is not None and day.day not in available_days:
            raise sst_fetch.SSTFetchError(f"no upstream path for {day:%Y%m%d}")
        fetched["sst"].append(day)
        anomaly = (
            fake_anomaly_frame(
                day, download_dir=Path("."), timeout=(1.0, 1.0), probe_timeout=(1.0, 1.0)
            )
            if with_anomaly
            else None
        )
        return sst_publish.SSTBundleFrame(
            valid_time=day,
            values=_sst_values(),
            source_transform=_STUB_TRANSFORM,
            source_url=f"https://example.invalid/{day:%Y%m%d}.nc",
            source_filename=f"{day:%Y%m%d}.nc",
            anomaly_values=None if anomaly is None else anomaly.anomaly_values,
            anomaly_source_url=None if anomaly is None else anomaly.anomaly_source_url,
            anomaly_source_filename=None if anomaly is None else anomaly.anomaly_source_filename,
        )

    monkeypatch.setattr(sst_poller, "_build_frame_for_day", fake_frame)
    monkeypatch.setattr(sst_poller, "_build_anomaly_frame_for_day", fake_anomaly_frame)
    return fetched


# ---------------------------------------------------------------------------
# Missing-day arithmetic
# ---------------------------------------------------------------------------

def test_first_ever_start_walks_back_one_bundle_window() -> None:
    days = sst_poller._missing_days(
        latest_available=_day(4), newest_published=None, bundle_frame_count=14
    )
    assert len(days) == 14
    assert days[0] == _day(4) - timedelta(days=13)
    assert days[-1] == _day(4)
    assert days == sorted(days)


def test_incremental_cycle_fetches_only_the_new_days() -> None:
    assert sst_poller._missing_days(
        latest_available=_day(4), newest_published=_day(2), bundle_frame_count=14
    ) == [_day(3), _day(4)]


def test_nothing_new_yields_no_days() -> None:
    assert (
        sst_poller._missing_days(
            latest_available=_day(4), newest_published=_day(4), bundle_frame_count=14
        )
        == []
    )
    # Published ahead of upstream (e.g. a re-probe against a lagging mirror).
    assert (
        sst_poller._missing_days(
            latest_available=_day(3), newest_published=_day(4), bundle_frame_count=14
        )
        == []
    )


def test_long_outage_backfill_is_capped_at_the_bundle_size() -> None:
    days = sst_poller._missing_days(
        latest_available=_day(28), newest_published=datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
        bundle_frame_count=14,
    )
    assert len(days) == 14
    assert days[-1] == _day(28)


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------

def test_probe_miss_keeps_last_known_good(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_upstream(monkeypatch, newest=None)
    result = sst_poller.run_once(_config(tmp_path))
    assert result.action == "probe_miss"
    assert result.published_run_ids == ()


def test_first_cycle_publishes_the_backfill_window(
    tmp_path: Path, stubbed_publish: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched = _install_fake_upstream(monkeypatch, newest=_day(4))

    result = sst_poller.run_once(_config(tmp_path, bundle_frame_count=3))

    assert result.action == "published"
    assert fetched["sst"] == [_day(2), _day(3), _day(4)]
    # One run per day, oldest first, each id derived from its newest frame.
    assert result.published_run_ids == ("20260802_12z", "20260803_12z", "20260804_12z")

    manifest = json.loads((tmp_path / "manifests" / "sst" / "20260804_12z.json").read_text())
    frames = manifest["variables"]["sst"]["frames"]
    assert [frame["valid_time"] for frame in frames] == [
        "2026-08-02T12:00:00Z",
        "2026-08-03T12:00:00Z",
        "2026-08-04T12:00:00Z",
    ]
    assert sst_poller.newest_published_valid_time(tmp_path, "na") == _day(4)


def test_second_cycle_is_a_noop_when_upstream_has_not_moved(
    tmp_path: Path, stubbed_publish: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_upstream(monkeypatch, newest=_day(4))
    config = _config(tmp_path, bundle_frame_count=3)
    assert sst_poller.run_once(config).action == "published"

    fetched = _install_fake_upstream(monkeypatch, newest=_day(4))
    result = sst_poller.run_once(config)
    assert result.action == "noop"
    assert fetched["sst"] == []


def test_fetch_miss_does_not_publish_or_raise(
    tmp_path: Path, stubbed_publish: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_upstream(monkeypatch, newest=_day(4), available_days=set())
    result = sst_poller.run_once(_config(tmp_path, bundle_frame_count=3))
    assert result.action == "fetch_miss"
    assert result.published_run_ids == ()
    assert not (tmp_path / "published" / "sst").exists()


def test_partial_upstream_skips_the_missing_day(
    tmp_path: Path, stubbed_publish: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched = _install_fake_upstream(monkeypatch, newest=_day(4), available_days={2, 4})
    result = sst_poller.run_once(_config(tmp_path, bundle_frame_count=3))
    assert result.action == "published"
    assert fetched["sst"] == [_day(2), _day(4)]
    assert result.published_run_ids == ("20260802_12z", "20260804_12z")
    manifest = json.loads((tmp_path / "manifests" / "sst" / "20260804_12z.json").read_text())
    assert [frame["valid_time"] for frame in manifest["variables"]["sst"]["frames"]] == [
        "2026-08-02T12:00:00Z",
        "2026-08-04T12:00:00Z",
    ]


def test_global_flag_flip_on_an_existing_na_only_deployment_backfills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D2: the flip must be self-healing on the very NEXT cycle.

    A canonical-only decision reported ``noop`` here until a new upstream day
    appeared, and even then ``global`` would have started at one frame and grown
    a frame a day — never reaching parity with ``na``.
    """
    monkeypatch.setattr(sst_publish, "get_target_grid", _stub_grid)
    monkeypatch.setattr(
        sst_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), _STUB_TRANSFORM),
    )
    config = _config(tmp_path, bundle_frame_count=3)

    # Phase 1: SST live with the flag OFF — canonical only.
    monkeypatch.delenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", raising=False)
    _install_fake_upstream(monkeypatch, newest=_day(4))
    assert sst_poller.run_once(config).action == "published"
    assert not (tmp_path / "published" / "sst" / "domains").exists()
    na_window = [
        frame["valid_time"]
        for frame in json.loads(
            (tmp_path / "manifests" / "sst" / "20260804_12z.json").read_text()
        )["variables"]["sst"]["frames"]
    ]
    assert len(na_window) == 3

    # Phase 2: operator flips the flag and restarts. Upstream has NOT moved.
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "sst")
    fetched = _install_fake_upstream(monkeypatch, newest=_day(4))
    result = sst_poller.run_once(config)

    assert result.action == "published"
    # Backfilled the whole window for the lagging domain, oldest first.
    assert fetched["sst"] == [_day(2), _day(3), _day(4)]

    global_root = tmp_path / "published" / "sst" / "domains" / "global"
    assert json.loads((global_root / "LATEST.json").read_text())["run_id"] == "20260804_12z"
    global_manifest = json.loads(
        (tmp_path / "manifests" / "sst" / "domains" / "global" / "20260804_12z.json").read_text()
    )
    global_window = [frame["valid_time"] for frame in global_manifest["variables"]["sst"]["frames"]]
    # Parity: global now covers exactly the same window as na.
    assert global_window == na_window
    assert sst_poller.newest_published_valid_time(tmp_path, "global") == _day(4)

    # Days na already had were published to global ONLY — na's older runs were
    # not rewritten with a trailing run id.
    for run_id in ("20260802_12z", "20260803_12z"):
        na_manifest = json.loads((tmp_path / "manifests" / "sst" / f"{run_id}.json").read_text())
        newest_na_frame = na_manifest["variables"]["sst"]["frames"][-1]["valid_time"]
        assert newest_na_frame == f"2026-08-0{run_id[7]}T12:00:00Z"

    # Third cycle: both domains level with upstream -> genuine noop.
    fetched = _install_fake_upstream(monkeypatch, newest=_day(4))
    assert sst_poller.run_once(config).action == "noop"
    assert fetched["sst"] == []


def test_incremental_cycle_publishes_both_domains_for_the_new_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "sst")
    monkeypatch.setattr(sst_publish, "get_target_grid", _stub_grid)
    monkeypatch.setattr(
        sst_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), _STUB_TRANSFORM),
    )
    config = _config(tmp_path, bundle_frame_count=3)

    _install_fake_upstream(monkeypatch, newest=_day(3))
    assert sst_poller.run_once(config).action == "published"

    fetched = _install_fake_upstream(monkeypatch, newest=_day(4))
    result = sst_poller.run_once(config)

    assert result.action == "published"
    assert fetched["sst"] == [_day(4)]
    assert result.published_run_ids == ("20260804_12z",)
    for domain_root in (
        tmp_path / "manifests" / "sst",
        tmp_path / "manifests" / "sst" / "domains" / "global",
    ):
        manifest = json.loads((domain_root / "20260804_12z.json").read_text())
        assert [frame["valid_time"] for frame in manifest["variables"]["sst"]["frames"]] == [
            "2026-08-02T12:00:00Z",
            "2026-08-03T12:00:00Z",
            "2026-08-04T12:00:00Z",
        ]


def _manifest(tmp_path: Path, run_id: str, domain: str = "na") -> dict:
    root = tmp_path / "manifests" / "sst"
    if domain != "na":
        root = root / "domains" / domain
    return json.loads((root / f"{run_id}.json").read_text())


def _valid_times(manifest: dict, var_id: str) -> list[str]:
    entry = manifest["variables"].get(var_id)
    return [frame["valid_time"] for frame in entry["frames"]] if entry else []


# ---------------------------------------------------------------------------
# Phase 2 — anomaly must never block or delay SST
# ---------------------------------------------------------------------------

def test_anomaly_published_alongside_sst_when_upstream_has_both(
    tmp_path: Path, stubbed_publish: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched = _install_fake_upstream(monkeypatch, newest=_day(4))
    result = sst_poller.run_once(_config(tmp_path, bundle_frame_count=3))

    assert result.action == "published"
    assert fetched["sst"] == [_day(2), _day(3), _day(4)]
    assert fetched["sst_anom"] == [_day(2), _day(3), _day(4)]

    manifest = _manifest(tmp_path, "20260804_12z")
    assert _valid_times(manifest, "sst") == _valid_times(manifest, "sst_anom")
    for var_id in ("sst", "sst_anom"):
        entry = manifest["variables"][var_id]
        assert entry["available_frames"] == 3
        assert entry["expected_frames"] == 3
        assert entry["units"] == "°C"


def test_anomaly_lagging_sst_is_backfilled_without_refetching_sst(
    tmp_path: Path, stubbed_publish: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core Phase 2 constraint: SST ships on time, anomaly catches up later.

    Cycle 1 has no anomaly upstream at all, so SST publishes alone. Cycle 2 has
    the anomaly for the same days and no new SST day — the run must be amended
    with both variables, and the SST must NOT be re-downloaded.
    """
    config = _config(tmp_path, bundle_frame_count=3)

    # Cycle 1: anomaly upstream is empty.
    fetched = _install_fake_upstream(monkeypatch, newest=_day(4), anomaly_days=set())
    assert sst_poller.run_once(config).action == "published"
    assert fetched["sst"] == [_day(2), _day(3), _day(4)]
    assert fetched["sst_anom"] == []

    manifest = _manifest(tmp_path, "20260804_12z")
    assert len(_valid_times(manifest, "sst")) == 3
    # Honesty rule: the anomaly is ABSENT, not silently hidden.
    assert "sst_anom" not in manifest["variables"]

    # Cycle 2: anomaly now available; upstream SST has NOT moved.
    fetched = _install_fake_upstream(monkeypatch, newest=_day(4))
    result = sst_poller.run_once(config)

    assert result.action == "published"
    # SST was never re-fetched — carried forward by hardlink instead.
    assert fetched["sst"] == []
    assert fetched["sst_anom"] == [_day(2), _day(3), _day(4)]
    # Same run id amended, not a new trailing one.
    assert result.published_run_ids == ("20260804_12z",)

    manifest = _manifest(tmp_path, "20260804_12z")
    assert _valid_times(manifest, "sst") == [
        "2026-08-02T12:00:00Z",
        "2026-08-03T12:00:00Z",
        "2026-08-04T12:00:00Z",
    ]
    assert _valid_times(manifest, "sst_anom") == _valid_times(manifest, "sst")


def test_partial_anomaly_availability_shows_as_available_less_than_expected(
    tmp_path: Path, stubbed_publish: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A day whose anomaly upstream has not published reads as absent, not hidden."""
    _install_fake_upstream(monkeypatch, newest=_day(4), anomaly_days={2, 3})
    sst_poller.run_once(_config(tmp_path, bundle_frame_count=3))

    manifest = _manifest(tmp_path, "20260804_12z")
    assert manifest["variables"]["sst"]["available_frames"] == 3
    anomaly = manifest["variables"]["sst_anom"]
    assert anomaly["available_frames"] == 2
    assert anomaly["expected_frames"] == 3
    assert _valid_times(manifest, "sst_anom") == [
        "2026-08-02T12:00:00Z",
        "2026-08-03T12:00:00Z",
    ]


def test_anomaly_missing_forever_never_blocks_sst(
    tmp_path: Path, stubbed_publish: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, bundle_frame_count=3)
    for cycle_newest in (_day(4), _day(5), _day(6)):
        fetched = _install_fake_upstream(
            monkeypatch, newest=cycle_newest, anomaly_days=set()
        )
        result = sst_poller.run_once(config)
        assert result.action == "published", result
        assert fetched["sst_anom"] == []

    manifest = _manifest(tmp_path, "20260806_12z")
    assert _valid_times(manifest, "sst") == [
        "2026-08-04T12:00:00Z",
        "2026-08-05T12:00:00Z",
        "2026-08-06T12:00:00Z",
    ]
    assert "sst_anom" not in manifest["variables"]

    # A further cycle with nothing new is a NOOP, not a fetch_miss: SST is healthy
    # and only the anomaly is outstanding, which is the normal steady state.
    _install_fake_upstream(monkeypatch, newest=_day(6), anomaly_days=set())
    idle = sst_poller.run_once(config)
    assert idle.action == "noop"
    assert "anomaly pending" in idle.message
    # And a --once smoke test still reports success.
    _install_fake_upstream(monkeypatch, newest=_day(6), anomaly_days=set())
    assert sst_poller.run_poller(config, once=True) == 0


def test_anomaly_catch_up_covers_both_domains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "sst")
    monkeypatch.setattr(sst_publish, "get_target_grid", _stub_grid)
    monkeypatch.setattr(
        sst_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), _STUB_TRANSFORM),
    )
    config = _config(tmp_path, bundle_frame_count=3)

    _install_fake_upstream(monkeypatch, newest=_day(4), anomaly_days=set())
    assert sst_poller.run_once(config).action == "published"

    fetched = _install_fake_upstream(monkeypatch, newest=_day(4))
    assert sst_poller.run_once(config).action == "published"
    assert fetched["sst"] == []

    for domain in ("na", "global"):
        manifest = _manifest(tmp_path, "20260804_12z", domain)
        assert _valid_times(manifest, "sst_anom") == _valid_times(manifest, "sst")
        assert manifest["variables"]["sst_anom"]["available_frames"] == 3


def test_skewed_domains_converge_anomalies_in_a_single_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-1: the catch-up exclusion must be PER DOMAIN, not a flat set of days.

    Skew: ``na`` has each day's SST but no anomaly, while ``global`` has neither.
    So pass 1 fetches those days for ``global`` (which needs the SST) and picks up
    their anomalies along the way — while ``na`` still needs exactly those days'
    anomalies via catch-up.

    With a flat exclude set, pass 1 marking the days "done" (because *global* got
    them) suppressed ``na``'s catch-up entirely, costing a cycle of latency. Both
    domains must converge in ONE cycle.
    """
    monkeypatch.setattr(sst_publish, "get_target_grid", _stub_grid)
    monkeypatch.setattr(
        sst_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), _STUB_TRANSFORM),
    )
    config = _config(tmp_path, bundle_frame_count=3)

    # Build the skew: canonical-only deployment, anomaly upstream still empty.
    monkeypatch.delenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", raising=False)
    _install_fake_upstream(monkeypatch, newest=_day(4), anomaly_days=set())
    assert sst_poller.run_once(config).action == "published"
    assert _valid_times(_manifest(tmp_path, "20260804_12z"), "sst") != []
    assert "sst_anom" not in _manifest(tmp_path, "20260804_12z")["variables"]

    # Repair cycle: global switched on AND the anomaly is now available upstream.
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "sst")
    fetched = _install_fake_upstream(monkeypatch, newest=_day(4))
    result = sst_poller.run_once(config)
    assert result.action == "published"

    # global's SST was genuinely missing, so pass 1 legitimately fetched it...
    assert fetched["sst"] == [_day(2), _day(3), _day(4)]

    # ...and BOTH domains end this single cycle with a full anomaly window.
    for domain in ("na", "global"):
        manifest = _manifest(tmp_path, "20260804_12z", domain)
        sst_times = _valid_times(manifest, "sst")
        anomaly_times = _valid_times(manifest, "sst_anom")
        assert sst_times == [
            "2026-08-02T12:00:00Z",
            "2026-08-03T12:00:00Z",
            "2026-08-04T12:00:00Z",
        ], domain
        assert anomaly_times == sst_times, f"{domain} anomaly lagged a cycle behind"
        assert manifest["variables"]["sst_anom"]["available_frames"] == 3, domain


def test_a_day_is_head_probed_at_most_once_per_cycle(
    tmp_path: Path, stubbed_publish: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-3: pass 2 must not re-probe a day pass 1 already found missing."""
    probes: list[datetime] = []

    def counting_probe(day: datetime, **kwargs: object) -> bool:
        del kwargs
        probes.append(day)
        return False  # upstream never has it

    monkeypatch.setattr(sst_poller.sst_fetch, "probe_latest_available_day", lambda **k: _day(4))  # noqa: ARG005
    monkeypatch.setattr(sst_poller.sst_fetch, "probe_anomaly_available", counting_probe)

    def real_frame(
        day: datetime,
        *,
        download_dir: Path,
        timeout: tuple[float, float],
        probe_timeout: tuple[float, float],
        with_anomaly: bool,
    ):
        # Exercise the real anomaly-probe branch, stubbing only the SST download.
        anomaly = (
            sst_poller._build_anomaly_frame_for_day(
                day, download_dir=download_dir, timeout=timeout, probe_timeout=probe_timeout
            )
            if with_anomaly
            else None
        )
        return sst_publish.SSTBundleFrame(
            valid_time=day,
            values=_sst_values(),
            source_transform=_STUB_TRANSFORM,
            anomaly_values=None if anomaly is None else anomaly.anomaly_values,
        )

    monkeypatch.setattr(sst_poller, "_build_frame_for_day", real_frame)

    sst_poller.run_once(_config(tmp_path, bundle_frame_count=3))

    # Three days in the window, so three probes — not six.
    assert sorted(probes) == [_day(2), _day(3), _day(4)]
    assert len(probes) == len(set(probes)) == 3


def test_anomaly_catch_up_is_bounded_to_the_bundle_window(
    tmp_path: Path, stubbed_publish: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch-up may never queue days that have already aged out of the window."""
    config = _config(tmp_path, bundle_frame_count=2)
    _install_fake_upstream(monkeypatch, newest=_day(6), anomaly_days=set())
    assert sst_poller.run_once(config).action == "published"

    fetched = _install_fake_upstream(monkeypatch, newest=_day(6))
    sst_poller.run_once(config)
    # Window is 2 days (Aug 5-6); the older backfilled days are not revisited.
    assert fetched["sst_anom"] == [_day(5), _day(6)]


def _fail_publish_for_domain(monkeypatch: pytest.MonkeyPatch, failing_domain: str) -> None:
    """Force one domain's publish to blow up, leaving the others working."""
    real_publish_domain = sst_publish._publish_domain

    def flaky(*, domain: str, **kwargs: object) -> int:
        if domain == failing_domain:
            raise RuntimeError(f"forced {domain} publish failure")
        return real_publish_domain(domain=domain, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sst_publish, "_publish_domain", flaky)


def test_total_publish_failure_on_a_canonical_free_backfill_is_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D5: a global-only backfill whose every domain fails must NOT read green.

    The relaxed D2 guard made this reachable: with no canonical domain in the
    requested set, the per-domain log-and-skip swallowed the only failure, so
    publish_sst_bundle returned normally with frame_counts={}, the poller counted
    the run id, and --once exited 0 while nothing existed on disk — re-downloading
    the same window every cycle, forever, reporting success.
    """
    monkeypatch.setattr(sst_publish, "get_target_grid", _stub_grid)
    monkeypatch.setattr(
        sst_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), _STUB_TRANSFORM),
    )
    config = _config(tmp_path, bundle_frame_count=3)

    # na caught up with the flag off.
    monkeypatch.delenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", raising=False)
    _install_fake_upstream(monkeypatch, newest=_day(4))
    assert sst_poller.run_once(config).action == "published"
    na_runs_before = sorted(p.name for p in (tmp_path / "manifests" / "sst").glob("*.json"))

    # Flag flipped; the global backfill days are canonical-free AND global fails.
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "sst")
    _install_fake_upstream(monkeypatch, newest=_day(4))
    _fail_publish_for_domain(monkeypatch, "global")

    with pytest.raises(RuntimeError, match="every requested domain failed"):
        sst_poller.run_once(config)

    # Nothing fabricated on disk, and na is untouched.
    assert not (tmp_path / "published" / "sst" / "domains").exists()
    assert not (tmp_path / "manifests" / "sst" / "domains").exists()
    assert sorted(p.name for p in (tmp_path / "manifests" / "sst").glob("*.json")) == na_runs_before

    # And the operator smoke test fails instead of reporting green.
    assert sst_poller.run_poller(config, once=True) == 1


def test_partial_failure_with_canonical_intact_stays_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The D5 fix must not make a lagging global domain fatal to na.

    Scoped to a cycle where the canonical domain IS in the requested set for
    every day — i.e. a fresh root with the flag already on. (Once na is caught up
    and only global lags, the backfill days become canonical-free and total
    failure is *correctly* fatal — that is
    ``test_total_publish_failure_on_a_canonical_free_backfill_is_loud``.)
    """
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "sst")
    monkeypatch.setattr(sst_publish, "get_target_grid", _stub_grid)
    monkeypatch.setattr(
        sst_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), _STUB_TRANSFORM),
    )
    _install_fake_upstream(monkeypatch, newest=_day(4))
    _fail_publish_for_domain(monkeypatch, "global")

    config = _config(tmp_path, bundle_frame_count=3)
    # Both domains are requested for every day, so na carries the cycle and the
    # operator smoke test stays green.
    assert sst_poller.run_poller(config, once=True) == 0

    assert (tmp_path / "manifests" / "sst" / "20260804_12z.json").is_file()
    assert not (tmp_path / "published" / "sst" / "domains").exists()
    assert not (tmp_path / "manifests" / "sst" / "domains").exists()
    assert sst_poller.newest_published_valid_time(tmp_path, "na") == _day(4)
    na_manifest = json.loads((tmp_path / "manifests" / "sst" / "20260804_12z.json").read_text())
    assert [frame["valid_time"] for frame in na_manifest["variables"]["sst"]["frames"]] == [
        "2026-08-02T12:00:00Z",
        "2026-08-03T12:00:00Z",
        "2026-08-04T12:00:00Z",
    ]


def test_publish_raises_when_every_requested_domain_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The publish-layer half of the D5 fix, named domains included."""
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "sst")
    monkeypatch.setattr(sst_publish, "get_target_grid", _stub_grid)
    monkeypatch.setattr(
        sst_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), _STUB_TRANSFORM),
    )
    _fail_publish_for_domain(monkeypatch, "global")

    frame = sst_publish.SSTBundleFrame(
        valid_time=_day(4),
        values=np.array([[10.0, 11.0, 12.0], [13.0, np.nan, 15.0]], dtype=np.float32),
        source_transform=_STUB_TRANSFORM,
    )
    with pytest.raises(RuntimeError, match="global"):
        sst_publish.publish_sst_bundle(
            data_root=tmp_path, frames=[frame], domains=("global",)
        )


def test_once_exits_non_zero_when_the_cycle_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_config: object) -> None:
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(sst_poller, "run_once", boom)
    assert sst_poller.run_poller(_config(tmp_path), once=True) == 1


def test_once_exits_zero_on_a_healthy_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_upstream(monkeypatch, newest=None)
    assert sst_poller.run_poller(_config(tmp_path), once=True) == 0


def test_retention_prunes_old_runs_in_every_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "sst")
    monkeypatch.setattr(sst_publish, "get_target_grid", _stub_grid)
    monkeypatch.setattr(
        sst_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), _STUB_TRANSFORM),
    )
    _install_fake_upstream(monkeypatch, newest=_day(6))

    result = sst_poller.run_once(_config(tmp_path, bundle_frame_count=5, keep_runs=2))

    assert result.action == "published"
    assert len(result.published_run_ids) == 5
    for root in (
        tmp_path / "published" / "sst",
        tmp_path / "published" / "sst" / "domains" / "global",
        tmp_path / "manifests" / "sst",
        tmp_path / "manifests" / "sst" / "domains" / "global",
    ):
        runs = sorted(
            child.name.removesuffix(".json")
            for child in root.iterdir()
            if child.name not in {"LATEST.json", "domains"} and not child.name.startswith(".")
        )
        assert runs == ["20260805_12z", "20260806_12z"], f"{root}: {runs}"
