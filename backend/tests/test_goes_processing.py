from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import xarray as xr
from rasterio.crs import CRS
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import goes_processing


def _dataset() -> xr.Dataset:
    y = np.array([0.00002, -0.00002], dtype=np.float32)
    x = np.array([-0.00002, 0.00002], dtype=np.float32)
    ds = xr.Dataset(
        data_vars={
            "CMI": (("y", "x"), np.array([[250.0, 260.0], [270.0, 280.0]], dtype=np.float32)),
            "DQF": (("y", "x"), np.array([[0.0, 2.0], [1.0, 0.0]], dtype=np.float32)),
            "goes_imager_projection": ((), np.int32(-2147483647)),
        },
        coords={
            "x": ("x", x),
            "y": ("y", y),
            "t": np.datetime64("2026-05-21T12:02:36"),
            "band_id": ("band", np.array([13], dtype=np.int32)),
            "band_wavelength": ("band", np.array([10.33], dtype=np.float32)),
        },
    )
    ds["goes_imager_projection"].attrs.update(
        {
            "grid_mapping_name": "geostationary",
            "perspective_point_height": 35786023.0,
            "semi_major_axis": 6378137.0,
            "semi_minor_axis": 6356752.31414,
            "longitude_of_projection_origin": -75.2,
            "sweep_angle_axis": "x",
        }
    )
    ds.attrs.update(
        {
            "time_coverage_start": "2026-05-21T12:01:17.5Z",
            "time_coverage_end": "2026-05-21T12:03:56.0Z",
            "date_created": "2026-05-21T12:04:07.1Z",
            "dataset_name": "OR_ABI-L2-CMIPC-M6C13_G19_s20261411201175_e20261411203560_c20261411204071.nc",
        }
    )
    return ds


def test_abi_source_geometry_uses_projection_metadata_dynamically() -> None:
    crs, transform, meta = goes_processing.abi_source_geometry(_dataset())
    assert isinstance(crs, CRS)
    assert "+lon_0=-75.2" in crs.to_proj4()
    assert meta["longitude_of_projection_origin"] == -75.2
    assert transform.a > 0
    assert transform.e < 0


def test_decode_goes_scan_masks_dqf_and_uses_h5netcdf(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "scan.nc"
    _dataset().to_netcdf(path, engine="h5netcdf")

    monkeypatch.setattr(goes_processing, "get_grid_params", lambda *_: ((0.0, 0.0, 2.0, 2.0), 1.0))
    monkeypatch.setattr(
        goes_processing,
        "compute_transform_and_shape",
        lambda *_: (from_origin(0.0, 2.0, 1.0, 1.0), 2, 2),
    )

    captured = {}

    def fake_reproject(**kwargs):
        captured["source"] = np.asarray(kwargs["source"], dtype=np.float32).copy()
        kwargs["destination"][:] = kwargs["source"]

    monkeypatch.setattr(goes_processing, "reproject", fake_reproject)
    decoded = goes_processing.decode_goes_scan(path)
    assert decoded.valid_time == datetime(2026, 5, 21, 12, 2, 36, tzinfo=timezone.utc)
    assert np.isnan(captured["source"][0, 1])
    np.testing.assert_allclose(decoded.values, np.array([[250.0, np.nan], [270.0, 280.0]], dtype=np.float32), equal_nan=True)
    assert decoded.source_metadata["time_coverage_start"] == "2026-05-21T12:01:17.5Z"
    assert decoded.source_metadata["time_coverage_end"] == "2026-05-21T12:03:56.0Z"
