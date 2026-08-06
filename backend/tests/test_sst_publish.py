"""SST Phase 1 — publish layout, bundle/valid-time semantics, capabilities.

Properties pinned here:

* the canonical (``na``) publish writes today's literal layout;
* the ``global`` domain publish mirrors ``scheduler._publish_domain_locked``
  byte-for-byte in *path* terms (``domains/global/`` under staging, published
  and manifests, plus a domain-scoped LATEST) and is **dark** until
  ``CARTOSKY_GLOBAL_DOMAIN_MODELS`` names ``sst``;
* a run is a rolling window of daily frames, fh000 oldest -> fhNNN newest, each
  frame carrying its own ``valid_time``;
* a republish reuses the previous run's grid binaries by hardlink rather than
  re-encoding them;
* the ``("sst", "sst")`` packing entry round-trips °C through uint16.

The real 1825x1893 / 721x1440 target grids are replaced with a 2x3 stand-in so
the tests exercise the publish plumbing, not GDAL throughput (same technique as
``test_mrms_publish._configure_small_grid``).
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
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.registry import MODEL_REGISTRY  # noqa: E402
from app.models.serialization import serialize_model_capability  # noqa: E402
from app.models.sst import SST_MODEL  # noqa: E402
from app.services import sst_publish  # noqa: E402
from app.services.builder.raster_grid import TargetGrid, get_target_grid  # noqa: E402
from app.services.colormaps import get_color_map_spec  # noqa: E402
from app.services.grid import _PACKING_BY_MODEL_VAR, grid_code_supported  # noqa: E402
from app.services.observed_bundle_health import build_observed_bundle_health  # noqa: E402

FLAG = "CARTOSKY_GLOBAL_DOMAIN_MODELS"
_STUB_TRANSFORM = from_origin(-101.0, 46.0, 1.0, 1.0)
_STUB_SHAPE = (2, 3)


def _stub_grid(model: str, region: str) -> TargetGrid:
    del model
    crs = "EPSG:4326" if region == "global" else "EPSG:3857"
    return TargetGrid(
        crs=crs,
        bbox=(-101.0, 44.0, -98.0, 46.0),
        resolution=1.0,
        transform=_STUB_TRANSFORM,
        height=_STUB_SHAPE[0],
        width=_STUB_SHAPE[1],
    )


@pytest.fixture()
def small_grids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sst_publish, "get_target_grid", _stub_grid)
    monkeypatch.setattr(
        sst_publish,
        "warp_to_target_grid",
        lambda values, *args, **kwargs: (np.asarray(values, dtype=np.float32), _STUB_TRANSFORM),
    )


def _frame(day: datetime, base: float) -> sst_publish.SSTBundleFrame:
    values = np.array(
        [[base, base + 1.0, base + 2.0], [base + 3.0, np.nan, base + 5.0]], dtype=np.float32
    )
    return sst_publish.SSTBundleFrame(
        valid_time=day,
        values=values,
        source_transform=_STUB_TRANSFORM,
        source_url=f"https://example.invalid/sst/{day:%Y%m%d}.nc",
        source_filename=f"{day:%Y%m%d}.nc",
        metadata={"source_units": "degree_C", "kelvin_converted": False},
    )


def _day(year: int, month: int, dom: int) -> datetime:
    return datetime(year, month, dom, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Real grid geometry (no stub) — the spike's pinned shapes
# ---------------------------------------------------------------------------

def test_sst_resolves_both_production_target_grids() -> None:
    na = get_target_grid("sst", "na")
    assert (na.crs, na.resolution, na.height, na.width) == ("EPSG:3857", 9000.0, 1825, 1893)

    world = get_target_grid("sst", "global")
    assert (world.crs, world.resolution, world.height, world.width) == ("EPSG:4326", 0.25, 721, 1440)


# ---------------------------------------------------------------------------
# Capabilities + packing + colormap
# ---------------------------------------------------------------------------

def test_registry_exposes_sst_as_an_observed_model() -> None:
    plugin = MODEL_REGISTRY["sst"]
    assert plugin.product == "obs"
    capabilities = plugin.capabilities
    assert capabilities is not None
    assert capabilities.canonical_region == "na"
    assert capabilities.ui_constraints == {
        "canonical_region": "na",
        "time_axis_mode": "observed",
        "latest_only": True,
        "supports_sampling": True,
    }
    assert capabilities.grid_meters_by_region == {"na": 9000.0}
    assert capabilities.grid_native_degrees_by_region == {"global": 0.25}


def test_capability_payload_declares_global_only_behind_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    dark = serialize_model_capability("sst", SST_MODEL.capabilities)
    dark_var = dark["variables"]["sst"]
    assert dark_var["supported_build_regions"] == []
    assert dark_var["color_map_id"] == "sst"
    assert dark_var["units"] == "C"

    monkeypatch.setenv(FLAG, "sst")
    live = serialize_model_capability("sst", SST_MODEL.capabilities)
    assert live["variables"]["sst"]["supported_build_regions"] == ["na", "global"]


@pytest.mark.parametrize(
    ("lag_hours", "expected_state"),
    [
        # Observed steady-state band for the ERDDAP DN axis (measured 2026-08-06:
        # 54.2h on a gapless daily axis). None of this may read as stale.
        (26.0, "live"),
        (54.2, "live"),
        (56.0, "live"),
        (59.0, "live"),
        # One missed upstream day beyond normal.
        (62.0, "delayed"),
        (90.0, "stale"),
    ],
)
def test_observed_freshness_band_matches_the_measured_upstream_cadence(
    lag_hours: float, expected_state: str
) -> None:
    now = datetime(2026, 8, 6, 18, 11, tzinfo=timezone.utc)
    newest_frame = now - timedelta(hours=lag_hours)
    health = build_observed_bundle_health(
        latest_run="20260804_12z",
        manifest={
            "last_updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "variables": {
                "sst": {
                    "expected_frames": 14,
                    "available_frames": 14,
                    "frames": [
                        {"fh": 0, "valid_time": newest_frame.strftime("%Y-%m-%dT%H:%M:%SZ")}
                    ],
                }
            },
        },
        source="sst",
        now_utc=now,
    )
    assert health["freshness_state"] == expected_state, health


def test_sst_packing_entry_round_trips_celsius() -> None:
    assert grid_code_supported("sst", "sst")
    packing = _PACKING_BY_MODEL_VAR[("sst", "sst")]
    assert packing["units"] == "C"
    assert packing["scale"] == 0.01
    assert packing["offset"] == -5.0
    assert packing["nodata"] == 65535

    scale, offset, nodata = packing["scale"], packing["offset"], packing["nodata"]
    for celsius in (-2.0, 0.0, 12.34, 28.5, 35.0):
        code = int(round((celsius - offset) / scale))
        assert 0 <= code < nodata
        assert abs((code * scale + offset) - celsius) <= scale / 2.0 + 1e-9

    # The whole ramp range is representable, and the ramp itself is °C.
    spec = get_color_map_spec("sst")
    assert spec["units"] == "C"
    assert spec["range"] == (-2.0, 35.0)
    assert "°F" not in spec["legend_title"]
    anchors = spec["anchors"]
    assert [value for value, _ in anchors] == [float(t) for t in range(-2, 36)]


# ---------------------------------------------------------------------------
# Publish layout + bundle semantics
# ---------------------------------------------------------------------------

def test_canonical_publish_writes_run_bundle_and_latest(
    tmp_path: Path, small_grids: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    days = [_day(2026, 8, 1) + timedelta(days=offset) for offset in range(3)]
    frames = [_frame(day, 10.0 + offset) for offset, day in enumerate(days)]

    result = sst_publish.publish_sst_bundle(data_root=tmp_path, frames=frames)

    # Run id comes from the NEWEST frame's valid time, not the wall clock.
    assert result.run_id == "20260803_12z"
    assert result.frame_counts == {"na": 3}

    run_root = tmp_path / "published" / "sst" / result.run_id
    assert run_root.is_dir()
    assert not (tmp_path / "published" / "sst" / "domains").exists()

    for fh, day in enumerate(days):
        sidecar = json.loads((run_root / "sst" / f"fh{fh:03d}.json").read_text())
        assert sidecar["valid_time"] == day.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert sidecar["units"] == "°C"
        assert (run_root / "sst" / "grid" / f"fh{fh:03d}.l0.u16.bin").is_file()

    manifest = json.loads((tmp_path / "manifests" / "sst" / f"{result.run_id}.json").read_text())
    assert manifest["model"] == "sst"
    assert manifest["region"] == "na"
    var_entry = manifest["variables"]["sst"]
    assert var_entry["units"] == "°C"
    assert var_entry["expected_frames"] == 3
    assert var_entry["available_frames"] == 3
    assert [frame["fh"] for frame in var_entry["frames"]] == [0, 1, 2]
    assert [frame["valid_time"] for frame in var_entry["frames"]] == [
        day.strftime("%Y-%m-%dT%H:%M:%SZ") for day in days
    ]

    latest = json.loads((tmp_path / "published" / "sst" / "LATEST.json").read_text())
    assert latest["run_id"] == result.run_id
    assert latest["region"] == "na"


def test_republish_reuses_previous_frames_by_hardlink(
    tmp_path: Path, small_grids: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    day1, day2 = _day(2026, 8, 1), _day(2026, 8, 2)

    first = sst_publish.publish_sst_bundle(data_root=tmp_path, frames=[_frame(day1, 10.0)])
    second = sst_publish.publish_sst_bundle(data_root=tmp_path, frames=[_frame(day2, 20.0)])

    assert first.run_id == "20260801_12z"
    assert second.run_id == "20260802_12z"
    assert second.frame_counts == {"na": 2}

    first_root = tmp_path / "published" / "sst" / first.run_id
    second_root = tmp_path / "published" / "sst" / second.run_id

    # Day 1 slid from fh000 of run 1 to fh000 of run 2 and its binary was linked,
    # not rewritten.
    carried = second_root / "sst" / "grid" / "fh000.l0.u16.bin"
    origin = first_root / "sst" / "grid" / "fh000.l0.u16.bin"
    assert carried.is_file()
    assert carried.samefile(origin)

    # The fresh day got its own newly written binary.
    fresh = second_root / "sst" / "grid" / "fh001.l0.u16.bin"
    assert fresh.is_file()
    assert not fresh.samefile(origin)

    manifest = json.loads((tmp_path / "manifests" / "sst" / f"{second.run_id}.json").read_text())
    frames = manifest["variables"]["sst"]["frames"]
    assert [frame["valid_time"] for frame in frames] == [
        day1.strftime("%Y-%m-%dT%H:%M:%SZ"),
        day2.strftime("%Y-%m-%dT%H:%M:%SZ"),
    ]
    # Reused frame metadata was retargeted to the new fh/run.
    carried_meta = json.loads((second_root / "sst" / "grid" / "fh000.l0.meta.json").read_text())
    assert carried_meta["fh"] == 0
    assert carried_meta["file"] == "fh000.l0.u16.bin"


def test_bundle_window_is_capped_to_the_target_frame_count(
    tmp_path: Path, small_grids: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    run_id = None
    for offset in range(4):
        result = sst_publish.publish_sst_bundle(
            data_root=tmp_path,
            frames=[_frame(_day(2026, 8, 1) + timedelta(days=offset), 10.0 + offset)],
            target_frame_count=3,
        )
        run_id = result.run_id

    manifest = json.loads((tmp_path / "manifests" / "sst" / f"{run_id}.json").read_text())
    var_entry = manifest["variables"]["sst"]
    assert var_entry["available_frames"] == 3
    # The oldest day (Aug 1) aged out; the window is Aug 2-4, oldest at fh000.
    assert [frame["valid_time"] for frame in var_entry["frames"]] == [
        "2026-08-02T12:00:00Z",
        "2026-08-03T12:00:00Z",
        "2026-08-04T12:00:00Z",
    ]


def test_global_domain_publish_mirrors_the_scheduler_layout(
    tmp_path: Path, small_grids: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "sst")
    assert sst_publish.publish_domains() == ("na", "global")

    day1, day2 = _day(2026, 8, 1), _day(2026, 8, 2)
    sst_publish.publish_sst_bundle(data_root=tmp_path, frames=[_frame(day1, 10.0)])
    result = sst_publish.publish_sst_bundle(data_root=tmp_path, frames=[_frame(day2, 20.0)])

    assert result.frame_counts == {"na": 2, "global": 2}

    # Canonical tree stays literal; the domain tree lives under domains/global/.
    assert (tmp_path / "published" / "sst" / result.run_id / "sst" / "fh001.json").is_file()

    domain_run = tmp_path / "published" / "sst" / "domains" / "global" / result.run_id
    assert domain_run.is_dir()
    assert (domain_run / "sst" / "fh001.json").is_file()
    assert (domain_run / "sst" / "grid" / "fh001.l0.u16.bin").is_file()
    assert (
        tmp_path / "manifests" / "sst" / "domains" / "global" / f"{result.run_id}.json"
    ).is_file()

    domain_latest = json.loads(
        (tmp_path / "published" / "sst" / "domains" / "global" / "LATEST.json").read_text()
    )
    assert domain_latest["run_id"] == result.run_id
    assert domain_latest["region"] == "global"

    domain_manifest = json.loads(
        (tmp_path / "manifests" / "sst" / "domains" / "global" / f"{result.run_id}.json").read_text()
    )
    assert domain_manifest["region"] == "global"
    assert [frame["fh"] for frame in domain_manifest["variables"]["sst"]["frames"]] == [0, 1]

    # Domain reuse is domain-local: global fh000 links the global tree's own
    # previous binary, never the canonical one.
    global_carried = domain_run / "sst" / "grid" / "fh000.l0.u16.bin"
    global_origin = (
        tmp_path / "published" / "sst" / "domains" / "global" / "20260801_12z"
        / "sst" / "grid" / "fh000.l0.u16.bin"
    )
    canonical_origin = (
        tmp_path / "published" / "sst" / "20260801_12z" / "sst" / "grid" / "fh000.l0.u16.bin"
    )
    assert global_carried.samefile(global_origin)
    assert not global_carried.samefile(canonical_origin)

    # Staging mirrors the same split.
    assert (tmp_path / "staging" / "sst" / result.run_id).is_dir()
    assert (tmp_path / "staging" / "sst" / "domains" / "global" / result.run_id).is_dir()


def test_publish_requires_a_fresh_frame(tmp_path: Path, small_grids: None) -> None:
    with pytest.raises(ValueError):
        sst_publish.publish_sst_bundle(data_root=tmp_path, frames=[])
