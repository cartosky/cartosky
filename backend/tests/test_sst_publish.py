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

import inspect
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


def test_coastal_fringe_reach_is_per_target_domain() -> None:
    """A 9 km cell and a 0.25° cell need different dilation; one constant cannot
    serve both."""
    assert sst_publish.SST_COASTAL_FRINGE_CELLS_BY_DOMAIN == {"na": 6, "global": 8}
    assert sst_publish.coastal_fringe_cells_for_domain("na") == 6
    assert sst_publish.coastal_fringe_cells_for_domain("global") == 8
    # Global's target cell is the coarser of the two, so it must reach further.
    assert (
        sst_publish.coastal_fringe_cells_for_domain("global")
        > sst_publish.coastal_fringe_cells_for_domain("na")
    )
    # Unknown domain falls back to canonical rather than to zero (no fill).
    assert sst_publish.coastal_fringe_cells_for_domain("nonesuch") == 6


def test_published_manifest_carries_the_clip_to_water_render_hint(
    tmp_path: Path, small_grids: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The frontend land-mask layer keys off manifest.display_prep.clip_to_water,
    the same channel grid-webgl already reads edge_fade from."""
    monkeypatch.delenv(FLAG, raising=False)
    result = sst_publish.publish_sst_bundle(
        data_root=tmp_path, frames=[_frame(_day(2026, 8, 4), 12.0)]
    )
    grid_manifest = json.loads(
        (
            tmp_path / "published" / "sst" / result.run_id / "sst" / "grid" / "manifest.json"
        ).read_text()
    )
    display_prep = grid_manifest["display_prep"]
    assert display_prep["clip_to_water"] is True
    assert display_prep["id"] == "sst_clip_to_water_v1"
    # No geometry-changing prep: the binary stays on the target grid as-is.
    assert display_prep["upscale_factor"] == 1
    assert display_prep["smooth_sigma"] == 0.0
    assert "edge_fade" not in display_prep


def test_sub_zero_sst_survives_display_prep(tmp_path: Path, small_grids: None) -> None:
    """Polar water reaches about -2 C. GridDisplayPrepConfig.clamp_negative
    defaults True, which would rewrite every sub-zero pixel to 0.0 — the sst
    entry must switch it off."""
    from app.services.grid_display_prep import prepare_grid_display_values

    values = np.array([[-1.8, -0.5, 0.0], [4.0, np.nan, 21.0]], dtype=np.float32)
    prepared, prep_meta = prepare_grid_display_values(model="sst", var="sst", values=values)

    assert prep_meta is not None and prep_meta["clip_to_water"] is True
    np.testing.assert_allclose(prepared[0, :2], [-1.8, -0.5], rtol=1e-6)
    assert np.isnan(prepared[1, 1])

    # And it survives the full pack/decode round trip rather than only in memory.
    result = sst_publish.publish_sst_bundle(
        data_root=tmp_path,
        frames=[
            sst_publish.SSTBundleFrame(
                valid_time=_day(2026, 8, 4),
                values=values,
                source_transform=_STUB_TRANSFORM,
            )
        ],
    )
    grid_dir = tmp_path / "published" / "sst" / result.run_id / "sst" / "grid"
    meta = json.loads((grid_dir / "manifest.json").read_text())["grid"]
    encoded = np.frombuffer((grid_dir / "fh000.l0.u16.bin").read_bytes(), dtype="<u2")
    decoded = encoded.astype(np.float64) * meta["scale"] + meta["offset"]
    assert decoded[0] == pytest.approx(-1.8, abs=0.01)
    assert decoded[1] == pytest.approx(-0.5, abs=0.01)


# ---------------------------------------------------------------------------
# Phase 2 — sst_anom
# ---------------------------------------------------------------------------

def _anom_frame(day: datetime, base: float, *, with_sst: bool = False) -> sst_publish.SSTBundleFrame:
    anomaly = np.array(
        [[base - 2.0, base - 0.5, base + 0.5], [base + 2.0, np.nan, base + 3.0]],
        dtype=np.float32,
    )
    return sst_publish.SSTBundleFrame(
        valid_time=day,
        source_transform=_STUB_TRANSFORM,
        values=_frame(day, 12.0).values if with_sst else None,
        anomaly_values=anomaly,
        anomaly_source_url=f"https://example.invalid/anom/{day:%Y%m%d}.nc",
        anomaly_source_filename=f"ssta_{day:%Y%m%d}.nc",
    )


def test_sst_anom_capability_and_packing() -> None:
    capability = SST_MODEL.get_var_capability("sst_anom")
    assert capability is not None
    assert capability.units == "C"
    assert capability.color_map_id == "sst_anom"
    assert capability.group == "Ocean"
    assert list(capability.supported_build_regions) == ["na", "global"]
    assert grid_code_supported("sst", "sst_anom")

    packing = _PACKING_BY_MODEL_VAR[("sst", "sst_anom")]
    assert packing == {
        "dtype": "uint16",
        "scale": 0.01,
        "offset": -30.0,
        "nodata": 65535,
        "units": "C",
    }
    # Signed round trip, including the negatives that are half the field.
    scale, offset, nodata = packing["scale"], packing["offset"], packing["nodata"]
    for celsius in (-8.0, -3.2, -0.05, 0.0, 0.05, 5.0, 15.0):
        code = int(round((celsius - offset) / scale))
        assert 0 <= code < nodata
        assert abs((code * scale + offset) - celsius) <= scale / 2.0 + 1e-9


def test_sst_anom_ramp_is_diverging_half_degree_celsius() -> None:
    spec = get_color_map_spec("sst_anom")
    assert spec["units"] == "C"
    assert spec["range"] == (-6.0, 6.0)
    assert spec["transparent_below_min"] is False
    assert "°F" not in spec["legend_title"]
    levels = spec["levels"]
    assert levels[0] == -6.0 and levels[-1] == 6.0
    assert len(levels) == 37
    # Non-uniform step widths across the ladder — steps tighten near zero.
    steps = {round(b - a, 9) for a, b in zip(levels, levels[1:])}
    assert steps == {0.2, 0.4, 0.5, 0.6, 0.8}
    # One colour per bin, and the ramp really diverges: cool end blue-dominant,
    # warm end red-dominant, centre near-neutral.
    colors = spec["colors"]
    assert len(colors) == len(levels) - 1
    assert len(colors) == 36

    def rgb(value: str) -> tuple[int, int, int]:
        return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]

    cool_r, _, cool_b = rgb(colors[0])
    warm_r, _, warm_b = rgb(colors[-1])
    mid_r, mid_g, mid_b = rgb(colors[len(colors) // 2 - 1])
    assert cool_b > cool_r
    assert warm_r > warm_b
    assert min(mid_r, mid_g, mid_b) > 0xC0


def test_negative_anomalies_survive_display_prep(tmp_path: Path, small_grids: None) -> None:
    """clamp_negative defaults True and would erase the entire cool half of the
    anomaly ramp; the sst_anom entry must switch it off."""
    from app.services.grid_display_prep import prepare_grid_display_values

    values = np.array([[-3.2, -1.0, -0.05], [0.0, np.nan, 4.5]], dtype=np.float32)
    prepared, prep_meta = prepare_grid_display_values(model="sst", var="sst_anom", values=values)
    assert prep_meta is not None
    assert prep_meta["clip_to_water"] is True
    assert prep_meta["id"] == "sst_anom_clip_to_water_v1"
    np.testing.assert_allclose(prepared[0], [-3.2, -1.0, -0.05], rtol=1e-6)

    # And through the real pack/decode round trip, not just in memory.
    result = sst_publish.publish_sst_bundle(
        data_root=tmp_path,
        frames=[
            sst_publish.SSTBundleFrame(
                valid_time=_day(2026, 8, 4),
                source_transform=_STUB_TRANSFORM,
                values=_frame(_day(2026, 8, 4), 12.0).values,
                anomaly_values=values,
            )
        ],
    )
    grid_dir = tmp_path / "published" / "sst" / result.run_id / "sst_anom" / "grid"
    meta = json.loads((grid_dir / "manifest.json").read_text())["grid"]
    encoded = np.frombuffer((grid_dir / "fh000.l0.u16.bin").read_bytes(), dtype="<u2")
    decoded = encoded.astype(np.float64) * meta["scale"] + meta["offset"]
    assert decoded[0] == pytest.approx(-3.2, abs=0.01)
    assert decoded[1] == pytest.approx(-1.0, abs=0.01)
    assert decoded[2] == pytest.approx(-0.05, abs=0.01)


def test_publish_writes_both_variables_side_by_side(
    tmp_path: Path, small_grids: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    day = _day(2026, 8, 4)
    frame = _anom_frame(day, 1.0, with_sst=True)
    result = sst_publish.publish_sst_bundle(data_root=tmp_path, frames=[frame])

    assert result.variable_frame_counts == {"na": {"sst": 1, "sst_anom": 1}}
    run_root = tmp_path / "published" / "sst" / result.run_id
    for var_id in ("sst", "sst_anom"):
        assert (run_root / var_id / "fh000.json").is_file()
        assert (run_root / var_id / "grid" / "fh000.l0.u16.bin").is_file()
        prep = json.loads((run_root / var_id / "grid" / "manifest.json").read_text())["display_prep"]
        assert prep["clip_to_water"] is True

    manifest = json.loads((tmp_path / "manifests" / "sst" / f"{result.run_id}.json").read_text())
    assert sorted(manifest["variables"]) == ["sst", "sst_anom"]
    assert manifest["variables"]["sst_anom"]["display_name"] == "Sea Surface Temperature Anomaly"
    for entry in manifest["variables"].values():
        assert entry["units"] == "°C"
        assert entry["frames"] == [{"fh": 0, "valid_time": "2026-08-04T12:00:00Z"}]


def test_anomaly_only_frame_reuses_the_published_sst_by_hardlink(
    tmp_path: Path, small_grids: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The "anomaly arrived late" publish: SST carried forward, not re-encoded."""
    monkeypatch.delenv(FLAG, raising=False)
    day = _day(2026, 8, 4)

    first = sst_publish.publish_sst_bundle(data_root=tmp_path, frames=[_frame(day, 12.0)])
    assert first.variable_frame_counts == {"na": {"sst": 1}}
    sst_origin = tmp_path / "published" / "sst" / first.run_id / "sst" / "grid" / "fh000.l0.u16.bin"
    assert sst_origin.is_file()

    # Amend the same run with the anomaly only.
    second = sst_publish.publish_sst_bundle(
        data_root=tmp_path, frames=[_anom_frame(day, 1.0)], run_valid_time=day
    )
    assert second.run_id == first.run_id
    assert second.variable_frame_counts == {"na": {"sst": 1, "sst_anom": 1}}

    run_root = tmp_path / "published" / "sst" / second.run_id
    carried = run_root / "sst" / "grid" / "fh000.l0.u16.bin"
    assert carried.samefile(sst_origin), "SST binary was re-encoded instead of hardlinked"
    assert (run_root / "sst_anom" / "grid" / "fh000.l0.u16.bin").is_file()

    manifest = json.loads((tmp_path / "manifests" / "sst" / f"{second.run_id}.json").read_text())
    assert sorted(manifest["variables"]) == ["sst", "sst_anom"]


def test_anomaly_only_frame_without_run_valid_time_would_trail_the_window() -> None:
    """Why publish_sst_bundle takes run_valid_time: an older anomaly-only frame
    must not mint a run id behind the window's newest frame."""
    assert "run_valid_time" in inspect.signature(sst_publish.publish_sst_bundle).parameters


def test_a_frame_must_carry_at_least_one_field() -> None:
    with pytest.raises(ValueError, match="carries no field"):
        sst_publish.SSTBundleFrame(
            valid_time=_day(2026, 8, 4), source_transform=_STUB_TRANSFORM
        )


def test_publish_requires_a_fresh_frame(tmp_path: Path, small_grids: None) -> None:
    with pytest.raises(ValueError):
        sst_publish.publish_sst_bundle(data_root=tmp_path, frames=[])
