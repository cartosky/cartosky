from __future__ import annotations

from dataclasses import replace

from .base import VariableCapability


FIRST_NA_BUILD_COHORT_BY_MODEL: dict[str, tuple[str, ...]] = {
    "gfs": ("tmp2m", "pwat"),
    "gefs": ("tmp2m", "pwat"),
    "ecmwf": ("tmp2m", "pwat"),
    "aigfs": ("tmp2m",),
    "aifs": ("tmp2m", "pwat"),
    "eps": ("tmp2m",),
}


def apply_supported_build_regions(
    variable_catalog: dict[str, VariableCapability],
    *,
    var_keys: tuple[str, ...] | list[str] | set[str],
    supported_regions: tuple[str, ...] = ("conus", "na"),
) -> None:
    for var_key in var_keys:
        if var_key not in variable_catalog:
            continue
        variable_catalog[var_key] = replace(
            variable_catalog[var_key],
            supported_build_regions=list(supported_regions),
        )