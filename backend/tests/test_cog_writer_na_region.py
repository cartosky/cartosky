from __future__ import annotations

from app.services.builder.cog_writer import REGION_BBOX_3857, compute_transform_and_shape, get_grid_params


def test_get_grid_params_supports_na_with_normalized_inputs() -> None:
    bbox, grid_m = get_grid_params("GEFS", " NA ")

    assert bbox == REGION_BBOX_3857["na"]
    assert grid_m == 25000.0

    transform, height, width = compute_transform_and_shape(bbox, grid_m)
    assert height > 0
    assert width > 0
    assert float(transform.a) == 25000.0
    assert float(transform.e) == -25000.0
