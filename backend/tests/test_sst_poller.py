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


def _install_fake_upstream(
    monkeypatch: pytest.MonkeyPatch,
    *,
    newest: datetime | None,
    available_days: set[int] | None = None,
) -> list[datetime]:
    """Stub probe + per-day fetch. Returns the list of days actually fetched."""
    fetched: list[datetime] = []

    monkeypatch.setattr(
        sst_poller.sst_fetch, "probe_latest_available_day", lambda **kwargs: newest  # noqa: ARG005
    )

    def fake_frame(day: datetime, *, download_dir: Path, timeout: tuple[float, float]):
        del download_dir, timeout
        if available_days is not None and day.day not in available_days:
            raise sst_fetch.SSTFetchError(f"no upstream path for {day:%Y%m%d}")
        fetched.append(day)
        return sst_publish.SSTBundleFrame(
            valid_time=day,
            values=np.array([[10.0, 11.0, 12.0], [13.0, np.nan, 15.0]], dtype=np.float32),
            source_transform=_STUB_TRANSFORM,
            source_url=f"https://example.invalid/{day:%Y%m%d}.nc",
            source_filename=f"{day:%Y%m%d}.nc",
        )

    monkeypatch.setattr(sst_poller, "_build_frame_for_day", fake_frame)
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
    assert fetched == [_day(2), _day(3), _day(4)]
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
    assert fetched == []


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
    assert fetched == [_day(2), _day(4)]
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
    assert fetched == [_day(2), _day(3), _day(4)]

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
    assert fetched == []


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
    assert fetched == [_day(4)]
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
