from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    from pyresample.geometry import AreaDefinition

    _CONUS_AREA_DEF = AreaDefinition(
        "cartosky_conus",
        "CartoSky CONUS EPSG:3857",
        "cartosky_conus",
        {"proj": "merc", "a": 6378137, "b": 6378137, "lat_ts": 0, "lon_0": 0},
        2061,
        1153,
        (-14920000.0, 2752000.0, -6676000.0, 7364000.0),
    )
except ImportError:
    _CONUS_AREA_DEF = None


class GOESRGBProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class GOESRGBFrame:
    valid_time: datetime
    slot_time: datetime
    rgba: np.ndarray
    width: int
    height: int
    source_metadata: dict[str, Any]


def decode_goes_l1b_triplet(
    band1_path: Path,
    band2_path: Path,
    band3_path: Path,
    *,
    slot_time: datetime,
    composite_name: str = "cimss_true_color_sunz_rayleigh",
    resampler: str = "nearest",
) -> GOESRGBFrame:
    try:
        if _CONUS_AREA_DEF is None:
            raise GOESRGBProcessingError("pyresample is not installed")

        from satpy import Scene
        from satpy.enhancements.enhancer import get_enhanced_image

        scn = Scene(
            reader="abi_l1b",
            filenames=[str(band1_path), str(band2_path), str(band3_path)],
        )
        scn.load([composite_name])
        resampled = scn.resample(_CONUS_AREA_DEF, resampler=resampler)
        rgb_dataset = resampled[composite_name]

        img_enhanced = get_enhanced_image(rgb_dataset)
        raw = np.array(img_enhanced.data)
        if raw.ndim != 3:
            raise GOESRGBProcessingError(f"Unexpected composite shape: {raw.shape}")

        if raw.shape[0] == 4:
            rgb_channels = raw[:3]
            alpha_channel = raw[3]
        elif raw.shape[0] == 3:
            rgb_channels = raw
            alpha_channel = np.where(np.all(np.isfinite(rgb_channels), axis=0), 1.0, 0.0)
        else:
            raise GOESRGBProcessingError(f"Unexpected composite shape: {raw.shape}")

        rgb_channels = np.nan_to_num(rgb_channels, nan=0.0)
        alpha_channel = np.nan_to_num(alpha_channel, nan=0.0)
        rgb_hw = np.moveaxis(rgb_channels, 0, -1)
        alpha_hw = alpha_channel[:, :, np.newaxis]
        rgba_float = np.concatenate([rgb_hw, alpha_hw], axis=-1)
        rgba_uint8 = np.clip(rgba_float * 255, 0, 255).astype(np.uint8)

        valid_time = _extract_valid_time(scn, composite_name) or _as_utc_datetime(slot_time)
        slot_time_utc = _as_utc_datetime(slot_time)
        source_metadata = {
            "composite": composite_name,
            "band1_filename": Path(band1_path).name,
            "band2_filename": Path(band2_path).name,
            "band3_filename": Path(band3_path).name,
            "slot_time": slot_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        return GOESRGBFrame(
            valid_time=valid_time,
            slot_time=slot_time_utc,
            rgba=rgba_uint8,
            width=int(rgba_uint8.shape[1]),
            height=int(rgba_uint8.shape[0]),
            source_metadata=source_metadata,
        )
    except GOESRGBProcessingError:
        raise
    except Exception as exc:
        raise GOESRGBProcessingError("Unable to decode GOES L1b RGB triplet") from exc


def _extract_valid_time(scn: Any, composite_name: str) -> datetime | None:
    try:
        value = getattr(scn, "start_time", None)
        if value is None:
            return None
        if isinstance(value, datetime):
            return _as_utc_datetime(value)
        return None
    except Exception:
        return None


def encode_rgba_webp(
    rgba: np.ndarray,
    *,
    quality: int = 85,
    lossless: bool = False,
) -> bytes:
    try:
        from PIL import Image

        _require_rgba_uint8(rgba)
        img = Image.fromarray(rgba, mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=quality, lossless=lossless)
        return buf.getvalue()
    except GOESRGBProcessingError:
        raise
    except Exception as exc:
        raise GOESRGBProcessingError("Unable to encode GOES RGB frame as WebP") from exc


def encode_rgba_png(rgba: np.ndarray) -> bytes:
    try:
        from PIL import Image

        _require_rgba_uint8(rgba)
        img = Image.fromarray(rgba, mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except GOESRGBProcessingError:
        raise
    except Exception as exc:
        raise GOESRGBProcessingError("Unable to encode GOES RGB frame as PNG") from exc


def _require_rgba_uint8(rgba: np.ndarray) -> None:
    if not isinstance(rgba, np.ndarray):
        raise GOESRGBProcessingError("RGBA input must be a numpy array")
    if rgba.dtype != np.uint8:
        raise GOESRGBProcessingError("RGBA input must have dtype uint8")
    if rgba.ndim != 3 or rgba.shape[-1] != 4:
        raise GOESRGBProcessingError(f"RGBA input must have shape (H, W, 4), got {rgba.shape}")


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
