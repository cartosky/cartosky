from __future__ import annotations

import concurrent.futures
import brotli
import gzip
import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.transform import Affine, array_bounds
from scipy.ndimage import zoom as ndimage_zoom

from ..config import grid_supported_pair
from .colormaps import RADAR_PTYPE_BREAKS, RADAR_PTYPE_ORDER, get_color_map_spec
from .grid_display_prep import prepare_grid_display_values
from .render_resampling import resampling_name_for_kind, variable_color_map_id

logger = logging.getLogger(__name__)

GRID_MANIFEST_VERSION = 1
GRID_SUBTYPE = "grid"
GRID_PROJECTION = "EPSG:3857"
GRID_DTYPE_UINT8 = "uint8"
GRID_DTYPE_UINT16 = "uint16"
GRID_DTYPE = "uint16"
GRID_ENDIANNESS = "little"
GRID_LEVEL = 0
GRID_DIRNAME = "grid"
LEGACY_GRID_DIRNAME = "grid_v1"
CONTOUR_GRID_DTYPE = GRID_DTYPE_UINT16
CONTOUR_GRID_SCALE = 0.25
CONTOUR_GRID_OFFSET = -1000.0
CONTOUR_GRID_NODATA = 65535
CONTOUR_GRID_UNITS = ""

_GRID_LOD_CONFIG_BY_MODEL_VAR: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {
    ("mrms", "reflectivity"): (
        {"level": 0, "scale_factor": 1, "min_zoom": 5.5},
        {"level": 1, "scale_factor": 2, "min_zoom": 4.0, "max_zoom": 5.5},
        {"level": 2, "scale_factor": 4, "max_zoom": 4.0},
    ),
    ("mrms", "mrms_radar_ptype"): (
        {"level": 0, "scale_factor": 1, "min_zoom": 5.5},
        {"level": 1, "scale_factor": 2, "min_zoom": 4.0, "max_zoom": 5.5},
        {"level": 2, "scale_factor": 4, "max_zoom": 4.0},
    ),
    ("hrrr", "radar_ptype"): (
        {"level": 0, "scale_factor": 1, "min_zoom": 5.5},
        {"level": 1, "scale_factor": 2, "min_zoom": 4.0, "max_zoom": 5.5},
        {"level": 2, "scale_factor": 4, "max_zoom": 4.0},
    ),
    ("nam", "radar_ptype"): (
        {"level": 0, "scale_factor": 1, "min_zoom": 5.5},
        {"level": 1, "scale_factor": 2, "min_zoom": 4.0, "max_zoom": 5.5},
        {"level": 2, "scale_factor": 4, "max_zoom": 4.0},
    ),
}

GRID_GZIP_SIDECARS_ENABLED = str(os.getenv("CARTOSKY_GRID_GZIP_SIDECARS_ENABLED", "1")).strip().lower() not in {
    "",
    "0",
    "false",
    "no",
    "off",
}
try:
    GRID_GZIP_COMPRESSLEVEL = max(1, min(9, int(os.getenv("CARTOSKY_GRID_GZIP_COMPRESSLEVEL", "5"))))
except ValueError:
    GRID_GZIP_COMPRESSLEVEL = 5
GRID_BROTLI_SIDECARS_ENABLED = str(os.getenv("CARTOSKY_GRID_BROTLI_SIDECARS_ENABLED", "1")).strip().lower() not in {
    "",
    "0",
    "false",
    "no",
    "off",
}
try:
    GRID_BROTLI_QUALITY = max(0, min(11, int(os.getenv("CARTOSKY_GRID_BROTLI_QUALITY", "5"))))
except ValueError:
    GRID_BROTLI_QUALITY = 5

_PACKING_BY_MODEL_VAR: dict[tuple[str, str], dict[str, Any]] = {
    ("current_analysis", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("current_analysis", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("current_analysis", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("current_analysis", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("current_analysis", "spres"): {
        "scale": 0.1,
        "offset": 800.0,
        "nodata": 65535,
        "units": "hPa",
    },
    ("current_analysis", "mslp"): {
        "scale": 0.1,
        "offset": 800.0,
        "nodata": 65535,
        "units": "hPa",
    },
    ("hrrr", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("hrrr", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("hrrr", "rh2m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
    ("hrrr", "rh700"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
    ("hrrr", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("hrrr", "vort500"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "10^-5 s^-1",
    },
    ("hrrr", "sbcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("hrrr", "mlcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("hrrr", "mucape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("hrrr", "pwat"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("hrrr", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("hrrr", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("hrrr", "radar_ptype"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("hrrr", "radar_ptype_rain"): {
        "scale": 0.5,
        "offset": -10.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("hrrr", "radar_ptype_snow"): {
        "scale": 0.5,
        "offset": -10.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("hrrr", "radar_ptype_sleet"): {
        "scale": 0.5,
        "offset": -10.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("hrrr", "radar_ptype_frzr"): {
        "scale": 0.5,
        "offset": -10.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("hrrr", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("hrrr", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("hrrr", "snowfall_kuchera_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("hrrr", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("hrrr", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("hrrr", "tmp850_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "tmp2m_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "tmp850_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "hgt500_anom"): {
        "scale": 0.1,
        "offset": -60.0,
        "nodata": 65535,
        "units": "dam",
    },
    ("ecmwf", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("ecmwf", "tmp2m_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("ecmwf", "tmp850_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aifs", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aifs", "tmp2m_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aifs", "tmp850_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aifs", "rh2m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
    ("aigfs", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aigfs", "tmp2m_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gefs", "tmp2m__mean"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gefs", "tmp2m_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gefs", "tmp2m_anom__mean"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gefs", "rh2m__mean"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
    ("gefs", "rh700__mean"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
    ("gefs", "hgt500_anom"): {
        "scale": 0.1,
        "offset": -60.0,
        "nodata": 65535,
        "units": "dam",
    },
    ("gefs", "hgt500_anom__mean"): {
        "scale": 0.1,
        "offset": -60.0,
        "nodata": 65535,
        "units": "dam",
    },
    ("eps", "tmp2m__mean"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("eps", "tmp2m_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("eps", "tmp2m_anom__mean"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("eps", "rh2m__mean"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
    ("eps", "rh700__mean"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
    ("eps", "hgt500__mean"): {
        "scale": 0.1,
        "offset": -60.0,
        "nodata": 65535,
        "units": "dam",
    },
    ("eps", "hgt500_anom"): {
        "scale": 0.1,
        "offset": -60.0,
        "nodata": 65535,
        "units": "dam",
    },
    ("eps", "hgt500_anom__mean"): {
        "scale": 0.1,
        "offset": -60.0,
        "nodata": 65535,
        "units": "dam",
    },
    ("eps", "wspd10m__mean"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("gefs", "tmp850__mean"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gefs", "tmp850_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gefs", "tmp850_anom__mean"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("eps", "tmp850__mean"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("eps", "tmp850_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("eps", "tmp850_anom__mean"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gefs", "wspd850__mean"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("gefs", "wspd300__mean"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("gefs", "sbcape__mean"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("gefs", "wspd10m__mean"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("gefs", "snowfall_total__mean"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gefs", "pwat__mean"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("eps", "pwat__mean"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gefs", "precip_total__mean"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("eps", "precip_total__mean"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("aigfs", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("aigfs", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aigfs", "tmp850_anom"): {
        "scale": 0.1,
        "offset": -80.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aifs", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("ecmwf", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("ecmwf", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("aifs", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("ecmwf", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("aifs", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("aifs", "rh700"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
        ("ecmwf", "hgt500_anom"): {
            "scale": 0.1,
            "offset": -60.0,
            "nodata": 65535,
            "units": "dam",
        },
    ("aigfs", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("ecmwf", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("aifs", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("aifs", "hgt500_anom"): {
        "scale": 0.1,
        "offset": -60.0,
        "nodata": 65535,
        "units": "dam",
    },
    ("aigfs", "hgt500_anom"): {
        "scale": 0.1,
        "offset": -60.0,
        "nodata": 65535,
        "units": "dam",
    },
    ("aigfs", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("ecmwf", "vort500"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "10^-5 s^-1",
    },
    ("aigfs", "vort500"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "10^-5 s^-1",
    },
    ("ecmwf", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("aigfs", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("aifs", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("ecmwf", "ptype_intensity"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "index",
    },
    ("ecmwf", "ptype_intensity_rain"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("ecmwf", "ptype_intensity_snow"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("ecmwf", "ptype_intensity_ice"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("ecmwf", "pwat"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("aifs", "pwat"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("ecmwf", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("aifs", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("ecmwf", "snowfall_kuchera_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("ecmwf", "ice_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("ecmwf", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("aifs", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("ecmwf", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("ecmwf", "mucape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("ecmwf", "rh2m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
    ("ecmwf", "rh700"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
    ("gfs", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "rh2m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
    ("gfs", "rh700"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "%",
    },
    ("gfs", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("gfs", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("gfs", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("gfs", "vort500"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "10^-5 s^-1",
    },
    ("gfs", "sbcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("gfs", "mlcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("gfs", "mucape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("gfs", "pwat"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gfs", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("gfs", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("gfs", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gfs", "ptype_intensity"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "index",
    },
    ("gfs", "ptype_intensity_rain"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("gfs", "ptype_intensity_snow"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("gfs", "ptype_intensity_ice"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in/hr",
    },
    ("gfs", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gfs", "snowfall_kuchera_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("gfs", "ice_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nam", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nam", "dp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nam", "tmp850"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nam", "vort500"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "10^-5 s^-1",
    },
    ("nam", "sbcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("nam", "mlcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("nam", "mucape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("nam", "pwat"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nam", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("nam", "wspd850"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("nam", "wspd300"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "kt",
    },
    ("nam", "wgst10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("nam", "radar_ptype"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("nam", "radar_ptype_rain"): {
        "scale": 0.5,
        "offset": -10.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("nam", "radar_ptype_snow"): {
        "scale": 0.5,
        "offset": -10.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("nam", "radar_ptype_sleet"): {
        "scale": 0.5,
        "offset": -10.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("nam", "radar_ptype_frzr"): {
        "scale": 0.5,
        "offset": -10.0,
        "nodata": 65535,
        "units": "dBZ",
    },
    ("nam", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nam", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nam", "snowfall_kuchera_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nbm", "tmp2m"): {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    ("nbm", "sbcape"): {
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "J/kg",
    },
    ("nbm", "wspd10m"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    ("nbm", "precip_total"): {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("nbm", "snowfall_total"): {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("mrms", "reflectivity"): {
        "dtype": GRID_DTYPE_UINT8,
        "scale": 0.5,
        "offset": -10.0,
        "nodata": 255,
        "units": "dBZ",
    },
    ("mrms", "mrms_radar_ptype"): {
        "dtype": GRID_DTYPE_UINT8,
        "scale": 1.0,
        "offset": 0.0,
        "nodata": 255,
        "units": "index",
    },
    ("mrms", "mrms_recent_precip_6h"): {
        "dtype": GRID_DTYPE_UINT16,
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("mrms", "mrms_recent_precip_24h"): {
        "dtype": GRID_DTYPE_UINT16,
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("mrms", "mrms_recent_precip_72h"): {
        "dtype": GRID_DTYPE_UINT16,
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    ("goes-east", "ir13"): {
        "dtype": GRID_DTYPE_UINT16,
        "scale": 0.01,
        "offset": -100.0,
        "nodata": 65535,
        "units": "C",
    },
    ("goes-east", "wv9"): {
        "dtype": GRID_DTYPE_UINT16,
        "scale": 0.01,
        "offset": -100.0,
        "nodata": 65535,
        "units": "C",
    },
    ("goes-east", "wv8"): {
        "dtype": GRID_DTYPE_UINT16,
        "scale": 0.01,
        "offset": -100.0,
        "nodata": 65535,
        "units": "C",
    },
    ("goes-east", "vis2"): {
        "dtype": GRID_DTYPE_UINT16,
        "scale": 1.0 / 65534.0,
        "offset": 0.0,
        "nodata": 65535,
        "units": "reflectance",
    },
}

_PRECIP_ANOM_PACKING = {
    "scale": 0.01,
    "offset": -128.0,
    "nodata": 65535,
    "units": "in",
}
_PRECIP_ANOM_VARS = (
    "precip_5d_anom",
    "precip_7d_anom",
    "precip_10d_anom",
    "precip_15d_anom",
)
for _precip_anom_var in ("precip_5d_anom", "precip_7d_anom", "precip_10d_anom", "precip_16d_anom"):
    for _precip_anom_model in ("gfs", "ecmwf", "aigfs"):
        _PACKING_BY_MODEL_VAR[(_precip_anom_model, _precip_anom_var)] = dict(_PRECIP_ANOM_PACKING)
    _PACKING_BY_MODEL_VAR[("gefs", _precip_anom_var)] = dict(_PRECIP_ANOM_PACKING)
    _PACKING_BY_MODEL_VAR[("gefs", f"{_precip_anom_var}__mean")] = dict(_PRECIP_ANOM_PACKING)
_PACKING_BY_MODEL_VAR[("ecmwf", "precip_15d_anom")] = dict(_PRECIP_ANOM_PACKING)
for _precip_anom_var in ("precip_5d_anom", "precip_7d_anom", "precip_10d_anom", "precip_15d_anom"):
    _PACKING_BY_MODEL_VAR[("aifs", _precip_anom_var)] = dict(_PRECIP_ANOM_PACKING)
for _precip_anom_var in ("precip_5d_anom", "precip_7d_anom", "precip_10d_anom", "precip_15d_anom"):
    _PACKING_BY_MODEL_VAR[("eps", _precip_anom_var)] = dict(_PRECIP_ANOM_PACKING)
    _PACKING_BY_MODEL_VAR[("eps", f"{_precip_anom_var}__mean")] = dict(_PRECIP_ANOM_PACKING)

_NDFD_GRID_PACKING_BY_VAR: dict[str, dict[str, Any]] = {
    "mint": {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    "maxt": {
        "scale": 0.1,
        "offset": -100.0,
        "nodata": 65535,
        "units": "F",
    },
    "qpf_6h": {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    "qpf_24h": {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    "qpf_48h": {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    "snow_6h": {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    "snow_24h": {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    "snow_48h": {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    "ice_6h": {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    "ice_24h": {
        "scale": 0.01,
        "offset": 0.0,
        "nodata": 65535,
        "units": "in",
    },
    "wgust_6h_max": {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
    "wgust_24h_max": {
        "scale": 0.1,
        "offset": 0.0,
        "nodata": 65535,
        "units": "mph",
    },
}
for _ndfd_var, _ndfd_packing in _NDFD_GRID_PACKING_BY_VAR.items():
    _PACKING_BY_MODEL_VAR[("ndfd", _ndfd_var)] = dict(_ndfd_packing)

_PACKING_BY_MODEL_VAR[("wpc", "precip_total")] = {
    "scale": 0.01,
    "offset": 0.0,
    "nodata": 65535,
    "units": "in",
}


def grid_code_supported(model_id: str, var_key: str) -> bool:
    normalized_model = str(model_id or "").strip().lower()
    normalized_var = str(var_key or "").strip().lower()
    return _packing_config(normalized_model, normalized_var) is not None


def grid_supported(model_id: str, var_key: str) -> bool:
    return grid_supported_pair(model_id, var_key)


def grid_dir_for_run_root(run_root: Path, var: str) -> Path:
    return Path(run_root) / var / GRID_DIRNAME


def resolved_grid_dir_for_run_root(run_root: Path, var: str) -> Path:
    preferred = grid_dir_for_run_root(run_root, var)
    if preferred.is_dir():
        return preferred
    legacy = Path(run_root) / var / LEGACY_GRID_DIRNAME
    if legacy.is_dir():
        return legacy
    return preferred


def _iter_grid_variable_run_roots(run_root: Path, model: str) -> list[tuple[Path, str]]:
    root = Path(run_root)
    discovered: list[tuple[Path, str]] = []
    seen: set[tuple[str, str]] = set()

    for child in sorted(path for path in root.iterdir() if path.is_dir()):
        var = child.name.strip().lower()
        if grid_supported(model, var):
            key = (str(root), var)
            if key not in seen:
                seen.add(key)
                discovered.append((root, var))
            continue

        for nested in sorted(path for path in child.iterdir() if path.is_dir()):
            nested_var = nested.name.strip().lower()
            if not grid_supported(model, nested_var):
                continue
            region_root = child
            key = (str(region_root), nested_var)
            if key in seen:
                continue
            seen.add(key)
            discovered.append((region_root, nested_var))

    return discovered


def grid_dir(data_root: Path, model: str, run: str, var: str, *, region: str | None = None) -> Path:
    del region
    return resolved_grid_dir_for_run_root(data_root / "published" / model / run, var)


def grid_manifest_path(data_root: Path, model: str, run: str, var: str, *, region: str | None = None) -> Path:
    return grid_dir(data_root, model, run, var, region=region) / "manifest.json"


def grid_manifest_path_for_run_root(run_root: Path, var: str) -> Path:
    return grid_dir_for_run_root(run_root, var) / "manifest.json"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    tmp_path.replace(path)


def grid_dtype(dtype: str | None) -> str:
    normalized = str(dtype or GRID_DTYPE).strip().lower()
    return GRID_DTYPE_UINT8 if normalized == GRID_DTYPE_UINT8 else GRID_DTYPE_UINT16


def grid_bytes_per_sample(dtype: str | None) -> int:
    return 1 if grid_dtype(dtype) == GRID_DTYPE_UINT8 else 2


def grid_frame_dtype_token(dtype: str | None) -> str:
    return "u8" if grid_dtype(dtype) == GRID_DTYPE_UINT8 else "u16"


def grid_frame_filename(fh: int, *, level: int = GRID_LEVEL, dtype: str = GRID_DTYPE) -> str:
    return f"fh{int(fh):03d}.l{int(level)}.{grid_frame_dtype_token(dtype)}.bin"


def _safe_contour_key(key: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(key or "").strip())
    return safe or "contour"


def contour_grid_frame_filename(
    fh: int,
    key: str,
    *,
    level: int = GRID_LEVEL,
    dtype: str = CONTOUR_GRID_DTYPE,
) -> str:
    return f"fh{int(fh):03d}.contour-{_safe_contour_key(key)}.l{int(level)}.{grid_frame_dtype_token(dtype)}.bin"


def grid_frame_path(
    data_root: Path,
    model: str,
    run: str,
    var: str,
    fh: int,
    *,
    region: str | None = None,
    level: int = GRID_LEVEL,
    dtype: str = GRID_DTYPE,
) -> Path:
    return grid_dir(data_root, model, run, var, region=region) / grid_frame_filename(fh, level=level, dtype=dtype)


def grid_frame_path_for_run_root(
    run_root: Path,
    var: str,
    fh: int,
    *,
    level: int = GRID_LEVEL,
    dtype: str = GRID_DTYPE,
) -> Path:
    return grid_dir_for_run_root(run_root, var) / grid_frame_filename(fh, level=level, dtype=dtype)


def grid_frame_meta_filename(fh: int, *, level: int = GRID_LEVEL) -> str:
    return f"fh{int(fh):03d}.l{int(level)}.meta.json"


def contour_grid_frame_meta_filename(fh: int, key: str, *, level: int = GRID_LEVEL) -> str:
    return f"fh{int(fh):03d}.contour-{_safe_contour_key(key)}.l{int(level)}.meta.json"


def grid_frame_meta_path(data_root: Path, model: str, run: str, var: str, fh: int, *, level: int = GRID_LEVEL) -> Path:
    return grid_dir(data_root, model, run, var) / grid_frame_meta_filename(fh, level=level)


def grid_frame_meta_path_for_run_root(run_root: Path, var: str, fh: int, *, level: int = GRID_LEVEL) -> Path:
    return grid_dir_for_run_root(run_root, var) / grid_frame_meta_filename(fh, level=level)


def contour_grid_frame_path_for_run_root(
    run_root: Path,
    var: str,
    fh: int,
    key: str,
    *,
    level: int = GRID_LEVEL,
    dtype: str = CONTOUR_GRID_DTYPE,
) -> Path:
    return grid_dir_for_run_root(run_root, var) / contour_grid_frame_filename(fh, key, level=level, dtype=dtype)


def contour_grid_frame_meta_path_for_run_root(
    run_root: Path,
    var: str,
    fh: int,
    key: str,
    *,
    level: int = GRID_LEVEL,
) -> Path:
    return grid_dir_for_run_root(run_root, var) / contour_grid_frame_meta_filename(fh, key, level=level)


def resolved_grid_frame_path_for_run_root(
    run_root: Path,
    var: str,
    fh: int,
    *,
    level: int = GRID_LEVEL,
    dtype: str = GRID_DTYPE,
) -> Path:
    return resolved_grid_dir_for_run_root(run_root, var) / grid_frame_filename(fh, level=level, dtype=dtype)


def resolved_grid_frame_meta_path_for_run_root(run_root: Path, var: str, fh: int, *, level: int = GRID_LEVEL) -> Path:
    return resolved_grid_dir_for_run_root(run_root, var) / grid_frame_meta_filename(fh, level=level)


def expected_grid_frame_size_bytes(*, width: int, height: int, dtype: str = GRID_DTYPE) -> int:
    return max(0, int(width) * int(height) * grid_bytes_per_sample(dtype))


def grid_lod_specs(model: str, var: str) -> tuple[dict[str, Any], ...]:
    configured = _GRID_LOD_CONFIG_BY_MODEL_VAR.get((str(model).strip().lower(), str(var).strip().lower()))
    if not configured:
        return ({"level": GRID_LEVEL, "scale_factor": 1},)

    normalized: list[dict[str, Any]] = []
    for raw_spec in configured:
        level = int(raw_spec.get("level", GRID_LEVEL))
        scale_factor = max(1, int(raw_spec.get("scale_factor", 1)))
        spec: dict[str, Any] = {"level": level, "scale_factor": scale_factor}
        min_zoom = raw_spec.get("min_zoom")
        max_zoom = raw_spec.get("max_zoom")
        if isinstance(min_zoom, (int, float)) and not isinstance(min_zoom, bool):
            spec["min_zoom"] = float(min_zoom)
        if isinstance(max_zoom, (int, float)) and not isinstance(max_zoom, bool):
            spec["max_zoom"] = float(max_zoom)
        normalized.append(spec)
    normalized.sort(key=lambda item: int(item["level"]))
    return tuple(normalized)


def _lod_target_shape(height: int, width: int, scale_factor: int) -> tuple[int, int]:
    return (
        max(1, int(np.ceil(height / max(1, scale_factor)))),
        max(1, int(np.ceil(width / max(1, scale_factor)))),
    )


def _resize_continuous_grid(values: np.ndarray, *, target_height: int, target_width: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    if source.shape == (target_height, target_width):
        return source

    finite_mask = np.isfinite(source)
    if not finite_mask.any():
        return np.full((target_height, target_width), np.nan, dtype=np.float32)

    zoom_factors = (target_height / source.shape[0], target_width / source.shape[1])
    filled = np.where(finite_mask, source, 0.0).astype(np.float32, copy=False)
    weights = finite_mask.astype(np.float32, copy=False)
    resized_values = np.asarray(
        ndimage_zoom(filled, zoom_factors, order=1, mode="nearest", prefilter=False),
        dtype=np.float32,
    )
    resized_weights = np.asarray(
        ndimage_zoom(weights, zoom_factors, order=1, mode="nearest", prefilter=False),
        dtype=np.float32,
    )
    output = np.full((target_height, target_width), np.nan, dtype=np.float32)
    np.divide(resized_values, resized_weights, out=output, where=resized_weights > 1e-6)
    return output


def _resize_nearest_grid(values: np.ndarray, *, target_height: int, target_width: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    if source.shape == (target_height, target_width):
        return source
    zoom_factors = (target_height / source.shape[0], target_width / source.shape[1])
    resized = ndimage_zoom(source, zoom_factors, order=0, mode="nearest", prefilter=False)
    return np.asarray(resized, dtype=np.float32)


_RADAR_PTYPE_CODE_COUNT = len(RADAR_PTYPE_ORDER)
# Per-type bin counts are not uniform (rain has fewer bins than the others),
# so type classification must use the real offsets rather than a fixed divisor.
_RADAR_PTYPE_OFFSETS = np.array(
    [int(RADAR_PTYPE_BREAKS[code]["offset"]) for code in RADAR_PTYPE_ORDER], dtype=np.int32
)
_RADAR_PTYPE_COUNTS = np.array(
    [int(RADAR_PTYPE_BREAKS[code]["count"]) for code in RADAR_PTYPE_ORDER], dtype=np.int32
)
_RADAR_PTYPE_TOTAL_BINS = int(_RADAR_PTYPE_OFFSETS[-1] + _RADAR_PTYPE_COUNTS[-1])


def _resize_radar_ptype_grid(values: np.ndarray, *, target_height: int, target_width: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    if source.shape == (target_height, target_width):
        return source
    if target_height >= source.shape[0] or target_width >= source.shape[1]:
        return _resize_nearest_grid(source, target_height=target_height, target_width=target_width)

    output = np.full((target_height, target_width), np.nan, dtype=np.float32)
    source_height, source_width = source.shape
    for y in range(target_height):
        y0 = int(np.floor((y * source_height) / target_height))
        y1 = int(np.floor(((y + 1) * source_height) / target_height))
        y1 = max(y0 + 1, min(source_height, y1))
        for x in range(target_width):
            x0 = int(np.floor((x * source_width) / target_width))
            x1 = int(np.floor(((x + 1) * source_width) / target_width))
            x1 = max(x0 + 1, min(source_width, x1))
            block = source[y0:y1, x0:x1]
            finite = block[np.isfinite(block)]
            if finite.size == 0:
                continue

            rounded = np.rint(finite).astype(np.int32, copy=False)
            valid = (rounded >= 0) & (rounded < _RADAR_PTYPE_TOTAL_BINS)
            if not np.any(valid):
                continue

            rounded = rounded[valid]
            codes = np.searchsorted(_RADAR_PTYPE_OFFSETS, rounded, side="right") - 1
            counts = np.bincount(codes, minlength=_RADAR_PTYPE_CODE_COUNT)
            selected_code = int(np.argmax(counts))
            offset = int(_RADAR_PTYPE_OFFSETS[selected_code])
            count = int(_RADAR_PTYPE_COUNTS[selected_code])
            selected = rounded[codes == selected_code]
            local = np.clip(selected - offset, 0, count - 1)
            output[y, x] = float(offset + min(count - 1, int(np.rint(float(np.mean(local))))))
    return output


def _values_for_lod(values: np.ndarray, *, model: str, var: str, scale_factor: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    if scale_factor <= 1:
        return source

    target_height, target_width = _lod_target_shape(source.shape[0], source.shape[1], scale_factor)
    if variable_color_map_id(model, var) == "radar_ptype":
        return _resize_radar_ptype_grid(source, target_height=target_height, target_width=target_width)
    if resampling_name_for_kind(model_id=model, var_key=var) == "nearest":
        return _resize_nearest_grid(source, target_height=target_height, target_width=target_width)
    return _resize_continuous_grid(source, target_height=target_height, target_width=target_width)


def grid_gzip_sidecar_path(frame_path: Path) -> Path:
    return frame_path.with_name(f"{frame_path.name}.gz")


def grid_brotli_sidecar_path(frame_path: Path) -> Path:
    return frame_path.with_name(f"{frame_path.name}.br")


def write_grid_gzip_sidecar(frame_path: Path, payload: bytes, *, compresslevel: int = GRID_GZIP_COMPRESSLEVEL) -> Path:
    sidecar_path = grid_gzip_sidecar_path(frame_path)
    tmp_path = sidecar_path.with_suffix(f"{sidecar_path.suffix}.tmp")
    compressed = gzip.compress(payload, compresslevel=compresslevel, mtime=0)
    tmp_path.write_bytes(compressed)
    tmp_path.replace(sidecar_path)
    return sidecar_path


def write_grid_brotli_sidecar(frame_path: Path, payload: bytes, *, quality: int = GRID_BROTLI_QUALITY) -> Path:
    sidecar_path = grid_brotli_sidecar_path(frame_path)
    tmp_path = sidecar_path.with_suffix(f"{sidecar_path.suffix}.tmp")
    compressed = brotli.compress(payload, quality=quality)
    tmp_path.write_bytes(compressed)
    tmp_path.replace(sidecar_path)
    return sidecar_path


def ensure_grid_gzip_sidecar(
    frame_path: Path,
    *,
    compresslevel: int = GRID_GZIP_COMPRESSLEVEL,
    force: bool = False,
) -> Path | None:
    if not frame_path.is_file():
        raise FileNotFoundError(f"Missing grid frame artifact: {frame_path}")
    sidecar_path = grid_gzip_sidecar_path(frame_path)
    if sidecar_path.is_file() and not force:
        return sidecar_path
    payload = frame_path.read_bytes()
    return write_grid_gzip_sidecar(frame_path, payload, compresslevel=compresslevel)


def ensure_grid_brotli_sidecar(
    frame_path: Path,
    *,
    quality: int = GRID_BROTLI_QUALITY,
    force: bool = False,
) -> Path | None:
    if not frame_path.is_file():
        raise FileNotFoundError(f"Missing grid frame artifact: {frame_path}")
    sidecar_path = grid_brotli_sidecar_path(frame_path)
    if sidecar_path.is_file() and not force:
        return sidecar_path
    payload = frame_path.read_bytes()
    return write_grid_brotli_sidecar(frame_path, payload, quality=quality)


def _packing_config(model: str, var: str) -> dict[str, Any] | None:
    return _PACKING_BY_MODEL_VAR.get((str(model).strip().lower(), str(var).strip().lower()))


def _encode_values(values: np.ndarray, *, scale: float, offset: float, nodata: int, dtype: str) -> np.ndarray:
    resolved_dtype = grid_dtype(dtype)
    encoded_dtype: np.dtype[Any] = np.dtype(np.uint8 if resolved_dtype == GRID_DTYPE_UINT8 else np.uint16)
    encoded = np.full(values.shape, int(nodata), dtype=encoded_dtype)
    valid_mask = np.isfinite(values)
    if not np.any(valid_mask):
        return encoded

    scaled = np.rint((values[valid_mask] - float(offset)) / float(scale))
    clipped = np.clip(scaled, 0, int(nodata) - 1).astype(encoded_dtype, copy=False)
    encoded[valid_mask] = clipped
    return encoded


def write_grid_frame_for_run_root(
    *,
    run_root: Path,
    model: str,
    var: str,
    fh: int,
    values: np.ndarray,
    level: int = GRID_LEVEL,
    transform: Affine | None = None,
    bbox: list[float] | tuple[float, float, float, float] | None = None,
    projection: str = GRID_PROJECTION,
) -> dict[str, Any]:
    packing = _packing_config(model, var)
    if packing is None:
        raise ValueError(f"Unsupported grid pack target: {model}/{var}")
    packing_dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))

    values_array = np.asarray(values, dtype=np.float32)

    # Compute bbox from original (pre-display-prep) dimensions so that
    # upscaling in prepare_grid_display_values does not inflate the extent.
    if bbox is None:
        if transform is None:
            raise ValueError(f"Missing transform/bbox for grid frame: {model}/{var}/fh{int(fh):03d}")
        orig_h, orig_w = values_array.shape[:2]
        left, bottom, right, top = array_bounds(orig_h, orig_w, transform)
        bounds = [float(left), float(bottom), float(right), float(top)]
    else:
        bounds = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]

    display_values, prep_meta = prepare_grid_display_values(model=model, var=var, values=values_array)
    encoded = _encode_values(
        display_values,
        scale=float(packing["scale"]),
        offset=float(packing["offset"]),
        nodata=int(packing["nodata"]),
        dtype=packing_dtype,
    )
    height, width = encoded.shape
    crs_text = str(projection or GRID_PROJECTION)
    if packing_dtype == GRID_DTYPE_UINT8:
        encoded_bytes = encoded.astype(np.uint8, copy=False).tobytes(order="C")
    else:
        encoded_bytes = encoded.astype("<u2", copy=False).tobytes(order="C")

    out_path = grid_frame_path_for_run_root(run_root, var, fh, level=level, dtype=packing_dtype)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_bytes(encoded_bytes)
    tmp_path.replace(out_path)
    if GRID_GZIP_SIDECARS_ENABLED:
        write_grid_gzip_sidecar(out_path, encoded_bytes)
    if GRID_BROTLI_SIDECARS_ENABLED:
        write_grid_brotli_sidecar(out_path, encoded_bytes)

    frame_meta = {
        "fh": int(fh),
        "level": int(level),
        "file": out_path.name,
        "width": width,
        "height": height,
        "bbox": bounds,
        "projection": crs_text,
    }
    if prep_meta:
        frame_meta["display_prep"] = prep_meta
    write_json_atomic(grid_frame_meta_path_for_run_root(run_root, var, fh, level=level), frame_meta)
    return frame_meta


def write_grid_frames_for_run_root(
    *,
    run_root: Path,
    model: str,
    var: str,
    fh: int,
    values: np.ndarray,
    transform: Affine | None = None,
    bbox: list[float] | tuple[float, float, float, float] | None = None,
    projection: str = GRID_PROJECTION,
) -> list[dict[str, Any]]:
    values_array = np.asarray(values, dtype=np.float32)
    if bbox is None:
        if transform is None:
            raise ValueError(f"Missing transform/bbox for grid frame: {model}/{var}/fh{int(fh):03d}")
        source_height, source_width = values_array.shape[:2]
        left, bottom, right, top = array_bounds(source_height, source_width, transform)
        bounds = [float(left), float(bottom), float(right), float(top)]
    else:
        bounds = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]

    written: list[dict[str, Any]] = []
    for lod_spec in grid_lod_specs(model, var):
        level = int(lod_spec.get("level", GRID_LEVEL))
        scale_factor = max(1, int(lod_spec.get("scale_factor", 1)))
        lod_values = _values_for_lod(values_array, model=model, var=var, scale_factor=scale_factor)
        written.append(
            write_grid_frame_for_run_root(
                run_root=run_root,
                model=model,
                var=var,
                fh=fh,
                values=lod_values,
                level=level,
                bbox=bounds,
                projection=projection,
            )
        )
    return written


def write_contour_grid_frames_for_run_root(
    *,
    run_root: Path,
    model: str,
    var: str,
    fh: int,
    key: str,
    values: np.ndarray,
    interval: float,
    levels: list[float] | tuple[float, ...],
    label: str,
    transform: Affine | None = None,
    bbox: list[float] | tuple[float, float, float, float] | None = None,
    projection: str = GRID_PROJECTION,
) -> list[dict[str, Any]]:
    values_array = np.asarray(values, dtype=np.float32)
    if bbox is None:
        if transform is None:
            raise ValueError(f"Missing transform/bbox for contour grid frame: {model}/{var}/fh{int(fh):03d}/{key}")
        source_height, source_width = values_array.shape[:2]
        left, bottom, right, top = array_bounds(source_height, source_width, transform)
        bounds = [float(left), float(bottom), float(right), float(top)]
    else:
        bounds = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]

    written: list[dict[str, Any]] = []
    for lod_spec in grid_lod_specs(model, var):
        level = int(lod_spec.get("level", GRID_LEVEL))
        scale_factor = max(1, int(lod_spec.get("scale_factor", 1)))
        lod_values = _values_for_lod(values_array, model=model, var=var, scale_factor=scale_factor)
        encoded = _encode_values(
            lod_values,
            scale=CONTOUR_GRID_SCALE,
            offset=CONTOUR_GRID_OFFSET,
            nodata=CONTOUR_GRID_NODATA,
            dtype=CONTOUR_GRID_DTYPE,
        )
        height, width = encoded.shape
        encoded_bytes = encoded.astype("<u2", copy=False).tobytes(order="C")
        out_path = contour_grid_frame_path_for_run_root(
            run_root,
            var,
            fh,
            key,
            level=level,
            dtype=CONTOUR_GRID_DTYPE,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp_path.write_bytes(encoded_bytes)
        tmp_path.replace(out_path)
        if GRID_GZIP_SIDECARS_ENABLED:
            write_grid_gzip_sidecar(out_path, encoded_bytes)
        if GRID_BROTLI_SIDECARS_ENABLED:
            write_grid_brotli_sidecar(out_path, encoded_bytes)

        frame_meta = {
            "fh": int(fh),
            "level": int(level),
            "key": _safe_contour_key(key),
            "file": out_path.name,
            "width": width,
            "height": height,
            "bbox": bounds,
            "projection": str(projection or GRID_PROJECTION),
            "interval": float(interval),
            "levels": [float(item) for item in levels],
            "label": str(label or key),
        }
        write_json_atomic(
            contour_grid_frame_meta_path_for_run_root(run_root, var, fh, key, level=level),
            frame_meta,
        )
        written.append(frame_meta)
    return written


def write_grid_frame_from_value_cog_for_run_root(
    *,
    run_root: Path,
    model: str,
    var: str,
    fh: int,
    value_cog_path: Path,
) -> dict[str, Any]:
    if not value_cog_path.is_file():
        raise FileNotFoundError(f"Missing grid source value COG: {value_cog_path}")
    try:
        with rasterio.open(value_cog_path) as ds:
            frame_entries = write_grid_frames_for_run_root(
                run_root=run_root,
                model=model,
                var=var,
                fh=fh,
                values=ds.read(1).astype(np.float32, copy=False),
                transform=ds.transform,
                projection=ds.crs.to_string() if ds.crs is not None else GRID_PROJECTION,
            )
            return next((entry for entry in frame_entries if int(entry.get("level", GRID_LEVEL)) == GRID_LEVEL), frame_entries[0])
    except RasterioIOError as exc:
        raise FileNotFoundError(f"Unreadable grid source value COG: {value_cog_path}") from exc


def _build_palette_block(model: str, var: str) -> dict[str, Any]:
    color_map_id = variable_color_map_id(model, var)
    palette: dict[str, Any] = {"color_map_id": color_map_id}
    if color_map_id:
        try:
            spec = get_color_map_spec(color_map_id)
        except KeyError:
            spec = {}
        spec_type = str(spec.get("display_palette_kind") or spec.get("type") or "").strip()
        if spec_type:
            palette["kind"] = spec_type
        gamma = spec.get("power_norm_gamma")
        if gamma is not None:
            palette["power_norm_gamma"] = float(gamma)
        transparent_below_min = spec.get("transparent_below_min")
        if isinstance(transparent_below_min, (int, float)) and not isinstance(transparent_below_min, bool):
            palette["transparent_below_min"] = float(transparent_below_min)
        elif transparent_below_min is True and spec_type == "discrete":
            levels = spec.get("levels")
            if isinstance(levels, list) and levels:
                first_level = levels[0]
                if isinstance(first_level, (int, float)) and not isinstance(first_level, bool):
                    palette["transparent_below_min"] = float(first_level)
        transparent_zero = spec.get("transparent_zero")
        if isinstance(transparent_zero, bool):
            palette["transparent_zero"] = transparent_zero
        ptype_order = spec.get("ptype_order")
        ptype_breaks = spec.get("ptype_breaks")
        if color_map_id == "radar_ptype" and isinstance(ptype_order, list) and isinstance(ptype_breaks, dict):
            palette["ptype_order"] = list(ptype_order)
            palette["ptype_breaks"] = {
                str(key): {
                    "offset": int(value.get("offset", 0)),
                    "count": int(value.get("count", 0)),
                }
                for key, value in ptype_breaks.items()
                if isinstance(value, dict)
            }
    return palette


def _read_composite_sidecar_metadata(run_root: Path, var: str) -> dict[str, Any]:
    var_dir = Path(run_root) / var
    if not var_dir.is_dir():
        return {}
    for sidecar_path in sorted(var_dir.glob("fh*.json")):
        try:
            sidecar = json.loads(sidecar_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        composite_layers = sidecar.get("composite_layers")
        if not isinstance(composite_layers, list) or not composite_layers:
            continue
        payload: dict[str, Any] = {"composite_layers": composite_layers}
        composite_mode = sidecar.get("composite_mode")
        if isinstance(composite_mode, str) and composite_mode.strip():
            payload["composite_mode"] = composite_mode.strip()
        display_name = sidecar.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            payload["display_name"] = display_name.strip()
        return payload
    return {}


def _build_manifest_for_var_from_run_root(
    *,
    run_root: Path,
    model: str,
    run: str,
    var: str,
) -> bool:
    packing = _packing_config(model, var)
    if packing is None:
        return False
    packing_dtype = grid_dtype(str(packing.get("dtype") or GRID_DTYPE))
    lod_specs_by_level = {
        int(spec.get("level", GRID_LEVEL)): spec
        for spec in grid_lod_specs(model, var)
    }

    var_dir = Path(run_root) / var
    if not var_dir.is_dir():
        return False

    grid_dir_path = resolved_grid_dir_for_run_root(run_root, var)
    if not grid_dir_path.is_dir():
        return False

    projection = GRID_PROJECTION
    units = str(packing.get("units") or "")
    display_prep: dict[str, Any] | None = None
    valid_time_by_fh: dict[int, str] = {}
    manifest_display_name: str | None = None
    manifest_legend: dict[str, Any] | None = None
    manifest_contours: dict[str, Any] | None = None
    contour_lod_entries: dict[str, dict[int, dict[str, Any]]] = {}
    lod_entries: dict[int, dict[str, Any]] = {}

    for sidecar_path in sorted(var_dir.glob("fh*.json")):
        fh_token = sidecar_path.stem
        if not fh_token.startswith("fh"):
            continue
        try:
            fh = int(fh_token.removeprefix("fh"))
        except ValueError:
            continue
        try:
            sidecar = json.loads(sidecar_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not units:
            units = str(sidecar.get("units") or units or "")
        if manifest_display_name is None:
            display_name = sidecar.get("display_name")
            if isinstance(display_name, str) and display_name.strip():
                manifest_display_name = display_name.strip()
        if manifest_legend is None:
            legend = sidecar.get("legend")
            if isinstance(legend, dict):
                manifest_legend = dict(legend)
        if manifest_contours is None:
            contours = sidecar.get("contours")
            if isinstance(contours, dict):
                manifest_contours = dict(contours)
        valid_time = sidecar.get("valid_time")
        if isinstance(valid_time, str) and valid_time.strip():
            valid_time_by_fh[fh] = valid_time.strip()

    for frame_meta_path in sorted(grid_dir_path.glob("fh*.l*.meta.json")):
        if ".contour-" in frame_meta_path.name:
            continue
        try:
            frame_meta = json.loads(frame_meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        raw_fh = frame_meta.get("fh")
        raw_level = frame_meta.get("level")
        fh = int(raw_fh) if raw_fh is not None else -1
        level = int(raw_level) if raw_level is not None else GRID_LEVEL
        filename = str(frame_meta.get("file") or "").strip()
        frame_width = int(frame_meta.get("width") or 0)
        frame_height = int(frame_meta.get("height") or 0)
        frame_bbox = frame_meta.get("bbox")
        frame_projection = str(frame_meta.get("projection") or GRID_PROJECTION)
        if fh < 0 or not filename or frame_width <= 0 or frame_height <= 0:
            continue

        frame_path = grid_dir_path / filename
        if not frame_path.is_file():
            continue

        expected_size_bytes = expected_grid_frame_size_bytes(
            width=frame_width,
            height=frame_height,
            dtype=packing_dtype,
        )
        actual_size_bytes = frame_path.stat().st_size
        if actual_size_bytes != expected_size_bytes:
            logger.warning(
                "Skipping invalid grid frame in manifest: model=%s run=%s var=%s fh=%s level=%s actual_bytes=%s expected_bytes=%s",
                model,
                run,
                var,
                fh,
                level,
                actual_size_bytes,
                expected_size_bytes,
            )
            continue

        next_level = lod_entries.setdefault(
            level,
            {
                "level": level,
                "width": frame_width,
                "height": frame_height,
                "bbox": [float(frame_bbox[0]), float(frame_bbox[1]), float(frame_bbox[2]), float(frame_bbox[3])]
                if isinstance(frame_bbox, list) and len(frame_bbox) == 4
                else None,
                "projection": frame_projection,
                "frames": [],
            },
        )
        next_level["frames"].append(
            {
                "fh": fh,
                "file": filename,
                **({"valid_time": valid_time_by_fh[fh]} if fh in valid_time_by_fh else {}),
            }
        )
        if display_prep is None and level == GRID_LEVEL and isinstance(frame_meta.get("display_prep"), dict):
            display_prep = dict(frame_meta["display_prep"])

    for frame_meta_path in sorted(grid_dir_path.glob("fh*.contour-*.l*.meta.json")):
        try:
            frame_meta = json.loads(frame_meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        raw_fh = frame_meta.get("fh")
        raw_level = frame_meta.get("level")
        key = str(frame_meta.get("key") or "").strip()
        fh = int(raw_fh) if raw_fh is not None else -1
        level = int(raw_level) if raw_level is not None else GRID_LEVEL
        filename = str(frame_meta.get("file") or "").strip()
        frame_width = int(frame_meta.get("width") or 0)
        frame_height = int(frame_meta.get("height") or 0)
        frame_bbox = frame_meta.get("bbox")
        frame_projection = str(frame_meta.get("projection") or GRID_PROJECTION)
        if not key or fh < 0 or not filename or frame_width <= 0 or frame_height <= 0:
            continue

        frame_path = grid_dir_path / filename
        if not frame_path.is_file():
            continue
        expected_size_bytes = expected_grid_frame_size_bytes(
            width=frame_width,
            height=frame_height,
            dtype=CONTOUR_GRID_DTYPE,
        )
        if frame_path.stat().st_size != expected_size_bytes:
            continue

        key_entries = contour_lod_entries.setdefault(key, {})
        next_level = key_entries.setdefault(
            level,
            {
                "level": level,
                "width": frame_width,
                "height": frame_height,
                "bbox": [float(frame_bbox[0]), float(frame_bbox[1]), float(frame_bbox[2]), float(frame_bbox[3])]
                if isinstance(frame_bbox, list) and len(frame_bbox) == 4
                else None,
                "projection": frame_projection,
                "interval": float(frame_meta.get("interval") or 0.0),
                "levels": frame_meta.get("levels") if isinstance(frame_meta.get("levels"), list) else [],
                "label": str(frame_meta.get("label") or key),
                "frames": [],
            },
        )
        next_level["frames"].append(
            {
                "fh": fh,
                "file": filename,
                **({"valid_time": valid_time_by_fh[fh]} if fh in valid_time_by_fh else {}),
            }
        )

    if not lod_entries:
        return False

    sorted_levels = sorted(lod_entries)
    base_level = GRID_LEVEL if GRID_LEVEL in lod_entries else sorted_levels[0]
    base_lod = lod_entries[base_level]
    base_bbox = base_lod.get("bbox")
    if not isinstance(base_bbox, list) or len(base_bbox) != 4:
        return False

    manifest_lods: list[dict[str, Any]] = []
    for level in sorted_levels:
        lod_entry = lod_entries[level]
        lod_frames = sorted(lod_entry["frames"], key=lambda item: int(item["fh"]))
        next_lod = {
            "level": int(level),
            "width": int(lod_entry["width"]),
            "height": int(lod_entry["height"]),
            "frames": lod_frames,
        }
        lod_spec = lod_specs_by_level.get(level, {})
        if isinstance(lod_spec.get("min_zoom"), (int, float)):
            next_lod["min_zoom"] = float(lod_spec["min_zoom"])
        if isinstance(lod_spec.get("max_zoom"), (int, float)):
            next_lod["max_zoom"] = float(lod_spec["max_zoom"])
        manifest_lods.append(next_lod)

    manifest = {
        "manifest_version": GRID_MANIFEST_VERSION,
        "subtype": GRID_SUBTYPE,
        "model": model,
        "run": run,
        "var": var,
        "projection": str(base_lod.get("projection") or GRID_PROJECTION),
        "bbox": base_bbox,
        "grid": {
            "width": int(base_lod["width"]),
            "height": int(base_lod["height"]),
            "dtype": packing_dtype,
            "endianness": GRID_ENDIANNESS,
            "scale": float(packing["scale"]),
            "offset": float(packing["offset"]),
            "nodata": int(packing["nodata"]),
            "units": units,
        },
        "palette": _build_palette_block(model, var),
        "lods": manifest_lods,
    }
    if manifest_display_name:
        manifest["display_name"] = manifest_display_name
    if manifest_legend:
        manifest["legend"] = manifest_legend
    if manifest_contours or contour_lod_entries:
        next_contours = dict(manifest_contours or {})
        for key, entries_by_level in contour_lod_entries.items():
            if not entries_by_level:
                continue
            sorted_contour_levels = sorted(entries_by_level)
            base_contour_level = GRID_LEVEL if GRID_LEVEL in entries_by_level else sorted_contour_levels[0]
            base_contour_lod = entries_by_level[base_contour_level]
            contour_lods: list[dict[str, Any]] = []
            for level in sorted_contour_levels:
                lod_entry = entries_by_level[level]
                contour_lods.append(
                    {
                        "level": int(level),
                        "width": int(lod_entry["width"]),
                        "height": int(lod_entry["height"]),
                        "frames": sorted(lod_entry["frames"], key=lambda item: int(item["fh"])),
                    }
                )
            contour_meta = dict(next_contours.get(key) if isinstance(next_contours.get(key), dict) else {})
            contour_meta["grid"] = {
                "width": int(base_contour_lod["width"]),
                "height": int(base_contour_lod["height"]),
                "dtype": CONTOUR_GRID_DTYPE,
                "endianness": GRID_ENDIANNESS,
                "scale": CONTOUR_GRID_SCALE,
                "offset": CONTOUR_GRID_OFFSET,
                "nodata": CONTOUR_GRID_NODATA,
                "units": CONTOUR_GRID_UNITS,
            }
            contour_meta["lods"] = contour_lods
            contour_meta["interval"] = float(base_contour_lod.get("interval") or contour_meta.get("level") or 0.0)
            if "levels" not in contour_meta and isinstance(base_contour_lod.get("levels"), list):
                contour_meta["levels"] = base_contour_lod["levels"]
            if "label" not in contour_meta and base_contour_lod.get("label"):
                contour_meta["label"] = base_contour_lod["label"]
            next_contours[key] = contour_meta
        manifest["contours"] = next_contours
    composite_meta = _read_composite_sidecar_metadata(run_root, var)
    if composite_meta:
        manifest.update(composite_meta)
    if display_prep:
        manifest["display_prep"] = display_prep
    write_json_atomic(grid_manifest_path_for_run_root(run_root, var), manifest)
    return True


def build_grid_manifests_for_run_root(
    *,
    run_root: Path,
    model: str,
    run: str,
    variables: tuple[str, ...] | None = None,
) -> int:
    run_root_path = Path(run_root)
    if not run_root_path.is_dir():
        return 0

    requested_vars = {str(item).strip().lower() for item in (variables or ()) if str(item).strip()}
    manifest_ok = 0
    for variable_run_root, var in _iter_grid_variable_run_roots(run_root_path, model):
        if requested_vars and var not in requested_vars:
            continue
        try:
            if _build_manifest_for_var_from_run_root(run_root=variable_run_root, model=model, run=run, var=var):
                manifest_ok += 1
        except Exception:
            logger.exception("grid manifest build failed: model=%s run=%s var=%s", model, run, var)
    return manifest_ok


def build_grid_for_run(
    *,
    data_root: Path,
    model: str,
    run: str,
    workers: int,
    variables: tuple[str, ...] | None = None,
) -> tuple[int, int, int]:
    published_run = data_root / "published" / model / run
    if not published_run.is_dir():
        return 0, 0, 0

    requested_vars = {str(item).strip().lower() for item in (variables or ()) if str(item).strip()}
    jobs: list[tuple[Path, str, int, Path]] = []
    manifest_roots_by_var: dict[str, set[Path]] = {}

    for variable_run_root, var in _iter_grid_variable_run_roots(published_run, model):
        var_dir = variable_run_root / var
        if requested_vars and var not in requested_vars:
            continue
        manifest_roots_by_var.setdefault(var, set()).add(variable_run_root)
        for value_cog_path in sorted(var_dir.glob("fh*.val.cog.tif")):
            if not value_cog_path.is_file():
                logger.warning(
                    "Skipping missing grid source value COG: model=%s run=%s var=%s path=%s",
                    model,
                    run,
                    var,
                    value_cog_path,
                )
                continue
            fh_token = value_cog_path.name.split(".")[0]
            try:
                fh = int(fh_token.removeprefix("fh"))
            except ValueError:
                continue
            sidecar_path = var_dir / f"{fh_token}.json"
            if not sidecar_path.is_file():
                continue
            jobs.append((variable_run_root, var, fh, value_cog_path))

    if not jobs:
        return 0, 0, 0

    ok = 0
    fail = 0
    max_workers = max(1, int(workers))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(
                write_grid_frame_from_value_cog_for_run_root,
                run_root=variable_run_root,
                model=model,
                var=var,
                fh=fh,
                value_cog_path=value_cog_path,
            )
            for variable_run_root, var, fh, value_cog_path in jobs
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception:
                logger.exception("grid frame build failed for model=%s run=%s", model, run)
                fail += 1
                continue
            ok += 1

    manifest_ok = 0
    for var, roots in manifest_roots_by_var.items():
        for variable_run_root in sorted(roots):
            manifest_ok += build_grid_manifests_for_run_root(
                run_root=variable_run_root,
                model=model,
                run=run,
                variables=(var,),
            )

    return ok, fail, manifest_ok
