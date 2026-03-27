from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling

from app.services.builder.colorize import float_to_rgba
from app.services.render_resampling import (
    compute_loop_output_shape,
    high_quality_loop_resampling,
    log_fixed_loop_size_once,
    loop_fixed_width_for_tier,
    loop_max_dim_for_tier,
    loop_quality_for_tier,
    loop_webp_save_kwargs,
    rasterio_resampling_for_loop,
    use_value_render_for_variable,
    variable_color_map_id,
    variable_kind,
)

logger = logging.getLogger(__name__)


def convert_rgba_cog_to_loop_webp(
    *,
    model_id: str,
    run_id: str,
    var_key: str,
    cog_path: Path,
    value_cog_path: Path | None,
    out_path: Path,
    quality: int,
    max_dim: int,
    fixed_width: int,
    tier: int,
) -> tuple[bool, str]:
    mode_used = "rgba"

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        kind = variable_kind(model_id, var_key)
        resolved_max_dim = loop_max_dim_for_tier(
            model_id=model_id,
            var_key=var_key,
            tier=tier,
            default_max_dim=max_dim,
        )
        resolved_quality = loop_quality_for_tier(
            model_id=model_id,
            var_key=var_key,
            tier=tier,
            default_quality=quality,
        )
        with rasterio.open(cog_path) as ds:
            src_h = int(ds.height)
            src_w = int(ds.width)
            resolved_fixed_width = loop_fixed_width_for_tier(
                model_id=model_id,
                var_key=var_key,
                tier=tier,
                default_width=fixed_width,
            )
            out_h, out_w, fixed_applied = compute_loop_output_shape(
                model_id=model_id,
                var_key=var_key,
                src_h=src_h,
                src_w=src_w,
                max_dim=resolved_max_dim,
                fixed_width=resolved_fixed_width,
            )
            if out_h <= 0 or out_w <= 0:
                return False, mode_used
            if fixed_applied:
                log_fixed_loop_size_once(
                    model_id=model_id,
                    run_id=run_id,
                    var_key=var_key,
                    tier=tier,
                    src_h=src_h,
                    src_w=src_w,
                    out_h=out_h,
                    out_w=out_w,
                )

            base_resampling = rasterio_resampling_for_loop(model_id=model_id, var_key=var_key, kind=kind)
            value_render_active = use_value_render_for_variable(model_id=model_id, var_key=var_key)
            prefer_high_quality_resize = fixed_applied or (
                value_render_active and (out_h < src_h or out_w < src_w)
            )
            render_resampling = (
                high_quality_loop_resampling()
                if prefer_high_quality_resize and base_resampling != Resampling.nearest
                else base_resampling
            )

            if value_render_active and value_cog_path is not None and value_cog_path.is_file():
                color_map_id = variable_color_map_id(model_id, var_key)
                if color_map_id:
                    try:
                        with rasterio.open(value_cog_path) as value_ds:
                            sampled_values = value_ds.read(
                                1,
                                out_shape=(out_h, out_w),
                                resampling=render_resampling,
                            ).astype(np.float32, copy=False)
                        rgba, _ = float_to_rgba(
                            sampled_values,
                            color_map_id,
                            meta_var_key=var_key,
                        )
                        rgba_hwc = np.moveaxis(rgba, 0, -1)
                        image = Image.fromarray(rgba_hwc, mode="RGBA")
                        image.save(
                            out_path,
                            format="WEBP",
                            **loop_webp_save_kwargs(
                                model_id=model_id,
                                var_key=var_key,
                                quality=resolved_quality,
                            ),
                        )
                        return True, "value"
                    except Exception:
                        logger.exception(
                            "Loop value-render failed; falling back to RGBA path: model=%s var=%s src=%s val=%s out=%s",
                            model_id,
                            var_key,
                            cog_path,
                            value_cog_path,
                            out_path,
                        )

            if render_resampling == Resampling.nearest:
                data = ds.read(
                    indexes=(1, 2, 3, 4),
                    out_shape=(4, out_h, out_w),
                    resampling=render_resampling,
                )
            else:
                rgb = ds.read(
                    indexes=(1, 2, 3),
                    out_shape=(3, out_h, out_w),
                    resampling=render_resampling,
                )
                alpha = ds.read(
                    indexes=4,
                    out_shape=(out_h, out_w),
                    resampling=Resampling.nearest,
                )
                data = np.concatenate((rgb, alpha[np.newaxis, :, :]), axis=0)

        rgba = np.moveaxis(data, 0, -1)
        image = Image.fromarray(rgba, mode="RGBA")
        image.save(
            out_path,
            format="WEBP",
            **loop_webp_save_kwargs(
                model_id=model_id,
                var_key=var_key,
                quality=resolved_quality,
            ),
        )
        return True, mode_used
    except Exception:
        logger.exception("Loop WebP conversion failed: %s -> %s", cog_path, out_path)
        return False, mode_used
