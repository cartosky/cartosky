from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import wpc_poller, wpc_publish, wpc_source
from app.services.grid import grid_code_supported
from app.models.wpc import WPC_VARIABLE_CATALOG
from app.services.run_ids import format_run_id
from app.services.wpc_source import WPCSourceField


def _write_test_value_raster(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(-101.0, 46.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as ds:
        ds.write(values.astype(np.float32), 1)


def test_discover_refs_and_select_latest_complete_run() -> None:
    listing_html = """
    <a href="p06m_2026052600f006.grb">p06m_2026052600f006.grb</a>
    <a href="p06m_2026052600f012.grb">p06m_2026052600f012.grb</a>
    <a href="p06m_2026052612f006.grb">p06m_2026052612f006.grb</a>
    <a href="p06m_2026052612f012.grb">p06m_2026052612f012.grb</a>
    <a href="p06m_2026052612f018.grb">p06m_2026052612f018.grb</a>
    """
    refs = wpc_source.discover_wpc_source_refs_from_listing(
        listing_html,
        listing_url="https://ftp.wpc.ncep.noaa.gov/5km_qpf/",
    )

    run_time, selected_refs = wpc_source.select_latest_complete_run(
        refs,
        max_forecast_hour=12,
        cadence_hours=6,
    )

    assert run_time == datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    assert [ref.forecast_hour for ref in selected_refs] == [6, 12]


def test_cumulative_fields_roll_step_qpf_forward() -> None:
    issue_time = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    step_1 = WPCSourceField(
        forecast_hour=6,
        valid_time=datetime(2026, 5, 26, 18, 0, tzinfo=timezone.utc),
        issue_time=issue_time,
        values=np.array([[1.0, 2.0]], dtype=np.float32),
        transform=from_origin(-130.0, 55.0, 0.01, 0.01),
        crs="EPSG:4326",
        source_url="https://example.com/f006.grb",
        source_filename="f006.grb",
        source_units="[kg/(m^2)]",
    )
    step_2 = WPCSourceField(
        forecast_hour=12,
        valid_time=datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc),
        issue_time=issue_time,
        values=np.array([[3.0, 4.0]], dtype=np.float32),
        transform=from_origin(-130.0, 55.0, 0.01, 0.01),
        crs="EPSG:4326",
        source_url="https://example.com/f012.grb",
        source_filename="f012.grb",
        source_units="[kg/(m^2)]",
    )

    cumulative = wpc_source._cumulative_fields([step_1, step_2])

    assert np.array_equal(cumulative[0].values, np.array([[1.0, 2.0]], dtype=np.float32))
    assert np.array_equal(cumulative[1].values, np.array([[4.0, 6.0]], dtype=np.float32))


def test_publish_wpc_bundle_warps_native_grid_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(wpc_publish, "grid_build_enabled", lambda: False)
    monkeypatch.setattr(
        wpc_publish,
        "_build_sidecar_json",
        lambda **kwargs: {
            "model": kwargs["model"],
            "run": kwargs["run_id"],
            "variable": kwargs["var_id"],
            "fh": kwargs["fh"],
            "valid_time": kwargs["valid_time_override"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    def _warp(values, *args, **kwargs):
        captured["input_shape"] = np.asarray(values).shape
        return np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32), from_origin(-101.0, 46.0, 1.0, 1.0)

    def _write_value(values, output_path, **_kwargs):
        captured["written_shape"] = np.asarray(values).shape
        return _write_test_value_raster(Path(output_path), np.asarray(values, dtype=np.float32)) or Path(output_path)

    monkeypatch.setattr(wpc_publish, "warp_to_target_grid", _warp)
    monkeypatch.setattr(wpc_publish, "write_value_cog", _write_value)

    issue_time = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    frame = WPCSourceField(
        forecast_hour=6,
        valid_time=datetime(2026, 5, 26, 18, 0, tzinfo=timezone.utc),
        issue_time=issue_time,
        values=np.ones((4, 5), dtype=np.float32),
        transform=from_origin(-130.0, 55.0, 0.01, 0.01),
        crs="EPSG:4326",
        source_url="https://example.com/p06m_2026052612f006.grb",
        source_filename="p06m_2026052612f006.grb",
        source_units="[kg/(m^2)]",
    )

    result = wpc_publish.publish_wpc_bundle(
        data_root=tmp_path,
        issue_time=issue_time,
        frames_by_var={"precip_total": [frame]},
    )

    assert result.run_id == "20260526_1200z"
    assert captured["input_shape"] == (4, 5)
    assert captured["written_shape"] == (2, 3)
    latest_payload = json.loads((tmp_path / "published" / "wpc" / "LATEST.json").read_text())
    assert latest_payload["run_id"] == result.run_id


def test_run_once_noops_when_latest_bundle_matches_formatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_time = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    run_id = format_run_id(issue_time, include_minutes=True)
    model_root = tmp_path / "published" / "wpc" / run_id
    model_root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "published" / "wpc" / "LATEST.json").write_text(json.dumps({"run_id": run_id}))
    manifests_dir = tmp_path / "manifests" / "wpc"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    (manifests_dir / f"{run_id}.json").write_text(json.dumps({
        "variables": {"precip_total": {}},
        "metadata": {"source": wpc_publish.WPC_PUBLISH_SOURCE},
    }));

    frame = WPCSourceField(
        forecast_hour=6,
        valid_time=datetime(2026, 5, 26, 18, 0, tzinfo=timezone.utc),
        issue_time=issue_time,
        values=np.ones((2, 2), dtype=np.float32),
        transform=from_origin(-130.0, 55.0, 0.01, 0.01),
        crs="EPSG:4326",
        source_url="https://example.com/p06m_2026052612f006.grb",
        source_filename="p06m_2026052612f006.grb",
        source_units="[kg/(m^2)]",
    )

    monkeypatch.setattr(
        wpc_poller,
        "collect_latest_wpc_fields",
        lambda **kwargs: (issue_time, {"precip_total": [frame]}),
    )
    monkeypatch.setattr(
        wpc_poller,
        "publish_wpc_bundle",
        lambda **kwargs: type("PublishResult", (), {"run_id": run_id, "frame_count": 1})(),
    )

    result = wpc_poller.run_once(
        wpc_poller.WPCPollerConfig(
            data_root=tmp_path,
            listing_url="https://ftp.wpc.ncep.noaa.gov/5km_qpf/",
            poll_seconds=3600,
            keep_runs=8,
            timeout_seconds=30.0,
            max_forecast_hours=168,
            forecast_step_hours=6,
        )
    )

    assert result.action == "noop"
    assert result.published_run_id == run_id


def test_wpc_variables_are_registered_for_grid_packing() -> None:
    unsupported = [var_id for var_id in WPC_VARIABLE_CATALOG if not grid_code_supported("wpc", var_id)]
    assert unsupported == []