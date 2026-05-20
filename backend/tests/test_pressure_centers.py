from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from affine import Affine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.pressure_centers import PressureCenterConfig, detect_pressure_centers


def test_detect_pressure_centers_finds_high_and_low() -> None:
    values = np.array(
        [
            [1002, 1002, 1002, 1002, 1002],
            [1002, 980, 1000, 1001, 1002],
            [1002, 1000, 1028, 1001, 1002],
            [1002, 1001, 1001, 1001, 1002],
            [1002, 1002, 1002, 1002, 1002],
        ],
        dtype=np.float32,
    )
    transform = Affine.translation(-250_000, 250_000) * Affine.scale(100_000, -100_000)

    centers = detect_pressure_centers(
        values,
        transform=transform,
        config=PressureCenterConfig(
            source="mslp",
            units="hPa",
            radius_km=100,
            min_delta=8,
            min_separation_km=150,
            max_centers=4,
        ),
    )

    assert {center["type"] for center in centers} == {"H", "L"}
    high = next(center for center in centers if center["type"] == "H")
    low = next(center for center in centers if center["type"] == "L")
    assert high["value"] == 1028
    assert low["value"] == 980
    assert high["units"] == "hPa"
    assert high["source"] == "mslp"
    assert abs(float(high["lat"])) < 1.0
    assert abs(float(high["lon"])) < 1.0


def test_detect_pressure_centers_suppresses_nearby_duplicate_highs() -> None:
    values = np.full((7, 7), 1000.0, dtype=np.float32)
    values[3, 3] = 1030.0
    values[3, 4] = 1028.0
    values[5, 5] = 1026.0
    transform = Affine.translation(-350_000, 350_000) * Affine.scale(100_000, -100_000)

    centers = detect_pressure_centers(
        values,
        transform=transform,
        config=PressureCenterConfig(
            source="mslp",
            units="hPa",
            radius_km=100,
            min_delta=10,
            min_separation_km=350,
            max_centers=6,
            detect_lows=False,
        ),
    )

    highs = [center for center in centers if center["type"] == "H"]
    assert len(highs) == 1
    assert highs[0]["value"] == 1030