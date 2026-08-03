"""Sounding "profile stack" artifacts (Skew-T design 2026-07-30, Phase 1).

A sounding stack is one binary file per (run, forecast hour) holding an entire
vertical profile for every point of a decimated model grid, so a Skew-T panel
can be served with a **single seek and a single small read** per point.

This is deliberately *not* a raster variable: it is never registered in a
model plugin's ``vars``, never appears in the capabilities API, and is never
warped to Web Mercator. It is a point-sampling-only artifact and this module
owns its whole lifecycle — fetch spec, packing table, writer, sidecar and
reader.

Layout (design §3) — pixel-major uint16::

    offset(row, col, plane) = ((row * width + col) * n_planes + plane) * 2
    plane = level_index * 5 + var_index          # 0 .. 184  (isobaric)
    plane = 185 + surface_field_index            # 185 ..     (surface block)

Ladders, variables, products and decimation are **per model** and declared in
:data:`MODEL_SPECS` (design §10 / Phase 6). HRRR runs 1000 -> 100 hPa every
25 hPa (37 levels, the extrapolated "1013.2 mb" deliberately excluded); GFS
runs 21 levels and ECMWF 12, both at 0.5 deg. Isobaric variables are ordered
t, td, u, v, w in every model. One pixel owns a contiguous run of
``n_planes * 2`` bytes — 380 B for HRRR at format_version 1, 382 B at 2, 222 B
for GFS, 132 B for ECMWF. **Nothing outside this module may assume any of those
numbers**: readers take ``n_planes`` from the sidecar, which is what lets one
deployment serve all of them.

Where a model publishes no isobaric dewpoint (GFS, ECMWF) the ``td`` plane is
fetched as specific humidity and converted at build time via vapour pressure
(:func:`dewpoint_from_q`, design §8 #7). Deriving Td from RH stays forbidden.

The sidecar written next to each stack is fully self-describing: a reader
needs the JSON and nothing else (no import of this module, no model
registry lookup) to decode a profile.

Manual spot-check entry point::

    python -m app.services.sounding --run 20260730_12z --fh 6 --out /tmp/sndg
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------

#: Bumped whenever the on-disk layout, level ladder or variable set changes.
#: Old runs are never migrated — they age out with run retention (design §3).
#:
#: * v1 — 185 isobaric planes + 5 surface planes (190 planes / 380 B per pixel).
#: * v2 — adds ``CAPE:surface`` as a sixth surface plane (191 / 382 B), so the
#:   panel can show HRRR's own SBCAPE next to ours (decision #5). Readers are
#:   sidecar-driven and serve v1 stacks unchanged; those simply have no
#:   ``cape_sfc`` row.
SOUNDING_FORMAT_VERSION = 2

#: HRRR ladder: 1000 -> 100 hPa every 25 hPa. "1013.2 mb" is excluded on
#: purpose: it is a sub-surface extrapolated level (design §1). Kept as a
#: module-level name because it *is* HRRR's ladder and a lot of Phase 1-5 code
#: and tests read it; per-model ladders live on :class:`SoundingModelSpec`.
LEVELS_HPA: tuple[int, ...] = tuple(range(1000, 99, -25))

#: GFS pgrb2 0.25 deg isobaric ladder (design §10, MEASURED 2026-08-02):
#: 1000/975/950/925/900 then 50 hPa to 100. The 25-hPa infill lives in pgrb2b
#: but WITHOUT SPFH, and Td must come from q (§8 #7), so 21 is the usable ladder.
GFS_LEVELS_HPA: tuple[int, ...] = (1000, 975, 950, 925) + tuple(range(900, 99, -50))

#: ECMWF open-data `pl` ladder (design §10, MEASURED 2026-08-02). Coarse by
#: construction — 1000->925->850->700 is a 150 hPa boundary-layer gap — and
#: shipped anyway per decision §8 #6.
ECMWF_LEVELS_HPA: tuple[int, ...] = (
    1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100,
)

#: Native-grid decimation stride, both axes (HRRR 1799x1059 -> 450x265).
DECIMATION_STRIDE = 4

#: Global 0.25 deg -> 0.5 deg (1440x721 -> 720x361), design §10.
GLOBAL_DECIMATION_STRIDE = 2

NODATA_CODE = 65535
BYTES_PER_SAMPLE = 2
STACK_DTYPE = "<u2"


@dataclass(frozen=True)
class PackSpec:
    """uint16 packing constants: ``value = code * scale + offset``."""

    units: str
    scale: float
    offset: float
    nodata: int = NODATA_CODE
    # Physical sanity window. A plane whose finite values fall outside this is
    # a units surprise (K vs degC, Pa vs hPa) and must fail the build loudly
    # rather than quietly writing clipped garbage.
    valid_min: float = -math.inf
    valid_max: float = math.inf

    @property
    def max_code(self) -> int:
        return int(self.nodata) - 1

    @property
    def precision(self) -> float:
        """Worst-case round-trip error introduced by quantization."""
        return float(self.scale) / 2.0


# Precision targets (orchestrator spec): T/Td <= 0.05 degC over -120..+60;
# winds/VVEL <= 0.05 over +/-163; surface pressure <= 0.05 hPa over 300..1100.
# Every entry below quantizes at least 2x finer than its target.
TEMPERATURE_PACK = PackSpec(
    units="degC", scale=0.01, offset=-120.0, valid_min=-150.0, valid_max=80.0
)
WIND_PACK = PackSpec(
    units="m s-1", scale=0.01, offset=-163.0, valid_min=-200.0, valid_max=200.0
)
VVEL_PACK = PackSpec(
    units="Pa s-1", scale=0.01, offset=-163.0, valid_min=-200.0, valid_max=200.0
)
# 300..1100 hPa needs 80000 codes at 0.01; 0.02 fits uint16 with 0.01 hPa error.
PRESSURE_PACK = PackSpec(
    units="hPa", scale=0.02, offset=300.0, valid_min=200.0, valid_max=1200.0
)
# Surface CAPE (format_version 2). 0.15 J/kg per code covers 0..9830 J/kg — far
# above any observed HRRR surface CAPE — at a precision two orders finer than
# the quantity's own uncertainty. Values beyond the top of that window clip
# rather than wrap (the Phase 1 encode behaviour); the physical window below is
# wider still so a genuine units surprise fails the build instead of clipping.
CAPE_PACK = PackSpec(
    units="J kg-1", scale=0.15, offset=0.0, valid_min=-1.0, valid_max=15000.0
)


def _identity(values: np.ndarray) -> np.ndarray:
    return values


def _pa_to_hpa(values: np.ndarray) -> np.ndarray:
    return (values / np.float32(100.0)).astype(np.float32, copy=False)


@dataclass(frozen=True)
class SoundingVariable:
    """One isobaric variable, present at every level of the model's ladder.

    ``grib_element`` is the **GDAL** ``GRIB_ELEMENT`` tag of the message that
    lands in this slot; ``idx_name`` is the token the *index* uses (identical
    for the NOAA products, lowercase ECMWF short names for IFS). They differ
    because the fetch selects by idx line and the decode identifies by tag.

    For a ``td_from_q`` model the ``td`` slot is fetched as **specific
    humidity** and converted in place after the fetch (see
    :func:`derive_td_from_q_planes`), so ``grib_element`` there is SPFH/Q.
    """

    id: str
    grib_element: str
    pack: PackSpec
    convert: Callable[[np.ndarray], np.ndarray] = _identity
    idx_name: str | None = None
    #: Extra ``GRIB_ELEMENT`` spellings accepted for this slot.
    element_aliases: tuple[str, ...] = ()

    @property
    def search_token(self) -> str:
        return self.idx_name or self.grib_element

    @property
    def elements(self) -> tuple[str, ...]:
        return (self.grib_element.upper(),) + tuple(a.upper() for a in self.element_aliases)


@dataclass(frozen=True)
class SurfaceField:
    """One near-surface field, stored as a trailing plane (design §2)."""

    id: str
    grib_element: str
    # GDAL ``GRIB_SHORT_NAME`` level token: "0-SFC", "2-HTGL", "10-HTGL".
    level_token: str
    level_label: str
    pack: PackSpec
    convert: Callable[[np.ndarray], np.ndarray] = _identity
    idx_name: str | None = None
    #: Additional accepted ``GRIB_SHORT_NAME`` level tokens. ECMWF's ``mucape``
    #: is a local parameter whose GDAL level token is not pinned by a WMO
    #: template, so it is matched against a small allowlist.
    level_token_aliases: tuple[str, ...] = ()
    element_aliases: tuple[str, ...] = ()
    #: Human label for this diagnostic, echoed to the client so the UI never
    #: hardcodes "HRRR SBCAPE" (design §10 / Phase 6).
    display_label: str | None = None
    #: Optional fields may be absent from a build without failing it.
    required: bool = True

    @property
    def search_token(self) -> str:
        return self.idx_name or self.grib_element

    @property
    def elements(self) -> tuple[str, ...]:
        return (self.grib_element.upper(),) + tuple(a.upper() for a in self.element_aliases)

    @property
    def level_tokens(self) -> tuple[str, ...]:
        return (self.level_token.upper(),) + tuple(t.upper() for t in self.level_token_aliases)


ISOBARIC_VARIABLES: tuple[SoundingVariable, ...] = (
    SoundingVariable("t", "TMP", TEMPERATURE_PACK),
    SoundingVariable("td", "DPT", TEMPERATURE_PACK),
    SoundingVariable("u", "UGRD", WIND_PACK),
    SoundingVariable("v", "VGRD", WIND_PACK),
    SoundingVariable("w", "VVEL", VVEL_PACK),
)

SURFACE_FIELDS: tuple[SurfaceField, ...] = (
    SurfaceField("pres_sfc", "PRES", "0-SFC", "surface", PRESSURE_PACK, _pa_to_hpa),
    SurfaceField("t2m", "TMP", "2-HTGL", "2 m above ground", TEMPERATURE_PACK),
    SurfaceField("td2m", "DPT", "2-HTGL", "2 m above ground", TEMPERATURE_PACK),
    SurfaceField("u10m", "UGRD", "10-HTGL", "10 m above ground", WIND_PACK),
    SurfaceField("v10m", "VGRD", "10-HTGL", "10 m above ground", WIND_PACK),
    # format_version 2. Appended, never inserted: plane indices of the v1 fields
    # must not move, or a half-written run would decode as garbage.
    SurfaceField("cape_sfc", "CAPE", "0-SFC", "surface", CAPE_PACK, display_label="HRRR SBCAPE"),
)

N_ISOBARIC_PLANES = len(LEVELS_HPA) * len(ISOBARIC_VARIABLES)
N_PLANES = N_ISOBARIC_PLANES + len(SURFACE_FIELDS)
BYTES_PER_PIXEL = N_PLANES * BYTES_PER_SAMPLE

ISOBARIC_PRODUCT = "prs"
SURFACE_PRODUCT = "sfc"

#: Td derivation policies (design §8 #7). ``native_dpt`` = the model publishes
#: isobaric DPT and we take it verbatim (HRRR). ``td_from_q`` = no isobaric
#: dewpoint exists, so it is derived from specific humidity via vapour pressure
#: at BUILD time. RH-derivation is forbidden in both cases.
TD_NATIVE = "native_dpt"
TD_FROM_Q = "td_from_q"

#: Index dialects. ``noaa`` idx lines read ``:TMP:850 mb:``; ECMWF's JSON-lines
#: ``.index`` is rendered by the fetch layer as ``:t:850:pl:...``.
IDX_NOAA = "noaa"
IDX_ECMWF = "ecmwf"


@dataclass(frozen=True)
class FetchGroup:
    """One Herbie subset download: a product plus which planes it carries.

    HRRR splits isobaric (``prs``) and surface (``sfc``) across two files; GFS
    ``pgrb2.0p25`` and ECMWF ``oper`` carry both in one, so those models fetch
    a single subset per forecast hour.
    """

    product: str
    isobaric: bool = True
    surface: bool = True


def _gfs_isobaric_variables() -> tuple[SoundingVariable, ...]:
    # The td slot is fetched as SPFH and converted in place (design §8 #7).
    return (
        SoundingVariable("t", "TMP", TEMPERATURE_PACK),
        SoundingVariable("td", "SPFH", TEMPERATURE_PACK),
        SoundingVariable("u", "UGRD", WIND_PACK),
        SoundingVariable("v", "VGRD", WIND_PACK),
        SoundingVariable("w", "VVEL", VVEL_PACK),
    )


def _ecmwf_isobaric_variables() -> tuple[SoundingVariable, ...]:
    # GDAL decodes IFS GRIB2 through the same WMO tables, so the elements are
    # the NOAA spellings; the lowercase names are the *index* tokens.
    return (
        SoundingVariable("t", "TMP", TEMPERATURE_PACK, idx_name="t", element_aliases=("T",)),
        SoundingVariable("td", "SPFH", TEMPERATURE_PACK, idx_name="q", element_aliases=("Q",)),
        SoundingVariable("u", "UGRD", WIND_PACK, idx_name="u", element_aliases=("U",)),
        SoundingVariable("v", "VGRD", WIND_PACK, idx_name="v", element_aliases=("V",)),
        SoundingVariable("w", "VVEL", VVEL_PACK, idx_name="w", element_aliases=("W",)),
    )


def _gfs_surface_fields() -> tuple[SurfaceField, ...]:
    return (
        SurfaceField("pres_sfc", "PRES", "0-SFC", "surface", PRESSURE_PACK, _pa_to_hpa),
        SurfaceField("t2m", "TMP", "2-HTGL", "2 m above ground", TEMPERATURE_PACK),
        SurfaceField("td2m", "DPT", "2-HTGL", "2 m above ground", TEMPERATURE_PACK),
        SurfaceField("u10m", "UGRD", "10-HTGL", "10 m above ground", WIND_PACK),
        SurfaceField("v10m", "VGRD", "10-HTGL", "10 m above ground", WIND_PACK),
        SurfaceField("cape_sfc", "CAPE", "0-SFC", "surface", CAPE_PACK, display_label="GFS SBCAPE"),
    )


def _ecmwf_surface_fields() -> tuple[SurfaceField, ...]:
    return (
        SurfaceField(
            "pres_sfc", "PRES", "0-SFC", "surface", PRESSURE_PACK, _pa_to_hpa, idx_name="sp"
        ),
        SurfaceField(
            "t2m", "TMP", "2-HTGL", "2 m above ground", TEMPERATURE_PACK, idx_name="2t"
        ),
        SurfaceField(
            "td2m", "DPT", "2-HTGL", "2 m above ground", TEMPERATURE_PACK, idx_name="2d"
        ),
        SurfaceField("u10m", "UGRD", "10-HTGL", "10 m above ground", WIND_PACK, idx_name="10u"),
        SurfaceField("v10m", "VGRD", "10-HTGL", "10 m above ground", WIND_PACK, idx_name="10v"),
        # Most-unstable CAPE, NOT surface-based: never comparable to GFS/HRRR
        # ``CAPE:surface``, hence the explicit MUCAPE label (design §10 Q8).
        # ``mucape`` is an ECMWF *local* parameter, so its GDAL element/level
        # tags are not pinned by a WMO template — hence the alias lists and
        # ``required=False``: an unrecognised message costs the diagnostic row,
        # never the build.
        SurfaceField(
            "cape_sfc",
            "CAPE",
            "0-SFC",
            "surface",
            CAPE_PACK,
            idx_name="mucape",
            # "0-" is what GDAL actually emits for the live message (measured
            # 2026-08-02 from the open-data mirror: ELEMENT='CAPE',
            # SHORT_NAME='0-' — level type 17 renders as a bare prefix).
            level_token_aliases=("0-", "0-EATM", "0-UNKNOWN", "255-UNKNOWN", "0-RESERVED"),
            element_aliases=("MUCAPE", "CAPES", "MXCAPES"),
            display_label="MUCAPE",
            required=False,
        ),
    )


@dataclass(frozen=True)
class SoundingModelSpec:
    """Everything model-specific about a sounding stack, in one place.

    Adding a model is adding a row here: the writer, sidecar, reader, endpoint
    and client are all driven by the ladder/variable/surface lists this carries
    (the reader entirely through the sidecar).
    """

    model_id: str
    #: Model name Herbie is constructed with — ECMWF is Herbie's ``ifs``.
    herbie_model: str
    levels_hpa: tuple[int, ...]
    isobaric_variables: tuple[SoundingVariable, ...]
    surface_fields: tuple[SurfaceField, ...]
    fetch_groups: tuple[FetchGroup, ...]
    stride: int = DECIMATION_STRIDE
    td_policy: str = TD_NATIVE
    idx_dialect: str = IDX_NOAA
    #: Herbie source order for THIS model's sounding fetch. Empty = use the
    #: shared fetch-layer default (``CARTOSKY_HERBIE_PRIORITY`` /
    #: ``DEFAULT_HERBIE_PRIORITY``), which is what HRRR and GFS want.
    source_priority: tuple[str, ...] = ()

    # -- derived geometry ---------------------------------------------------

    @property
    def n_vars(self) -> int:
        return len(self.isobaric_variables)

    @property
    def n_isobaric_planes(self) -> int:
        return len(self.levels_hpa) * self.n_vars

    @property
    def n_planes(self) -> int:
        return self.n_isobaric_planes + len(self.surface_fields)

    @property
    def bytes_per_pixel(self) -> int:
        return self.n_planes * BYTES_PER_SAMPLE

    def isobaric_plane_index(self, level_index: int, var_index: int) -> int:
        if not 0 <= level_index < len(self.levels_hpa):
            raise ValueError(f"level_index out of range: {level_index}")
        if not 0 <= var_index < self.n_vars:
            raise ValueError(f"var_index out of range: {var_index}")
        return level_index * self.n_vars + var_index

    def surface_plane_index(self, field_index: int) -> int:
        if not 0 <= field_index < len(self.surface_fields):
            raise ValueError(f"field_index out of range: {field_index}")
        return self.n_isobaric_planes + field_index

    def variable_index(self, var_id: str) -> int:
        for index, var in enumerate(self.isobaric_variables):
            if var.id == var_id:
                return index
        raise KeyError(f"{self.model_id} has no isobaric variable {var_id!r}")

    @property
    def optional_planes(self) -> frozenset[int]:
        return frozenset(
            self.surface_plane_index(index)
            for index, field in enumerate(self.surface_fields)
            if not field.required
        )

    # -- packing ------------------------------------------------------------

    def pack_for_plane(self, plane: int) -> PackSpec:
        if plane < self.n_isobaric_planes:
            return self.isobaric_variables[plane % self.n_vars].pack
        return self.surface_fields[plane - self.n_isobaric_planes].pack

    def converter_for_plane(self, plane: int) -> Callable[[np.ndarray], np.ndarray]:
        if plane < self.n_isobaric_planes:
            return self.isobaric_variables[plane % self.n_vars].convert
        return self.surface_fields[plane - self.n_isobaric_planes].convert

    def plane_label(self, plane: int) -> str:
        if plane < self.n_isobaric_planes:
            var = self.isobaric_variables[plane % self.n_vars]
            level = self.levels_hpa[plane // self.n_vars]
            return f"{var.id}@{level}hPa"
        return self.surface_fields[plane - self.n_isobaric_planes].id

    # -- fetch selection ----------------------------------------------------

    def isobaric_search_pattern(self) -> str:
        elements = "|".join(var.search_token for var in self.isobaric_variables)
        levels = "|".join(str(level) for level in self.levels_hpa)
        if self.idx_dialect == IDX_ECMWF:
            return f":({elements}):({levels}):pl:"
        return f":({elements}):({levels}) mb:"

    def surface_search_pattern(self) -> str:
        if self.idx_dialect == IDX_ECMWF:
            tokens = "|".join(field.search_token for field in self.surface_fields)
            # Two ECMWF index renderings exist in this codebase and the pattern
            # has to match BOTH, or a surface subset silently selects zero
            # messages (the §4 Herbie gotcha, in a new dress):
            #   * Herbie's own eccodes inventory joins every column, so a
            #     surface record's empty `levelist` becomes the literal "nan":
            #     `:sp:nan:sfc:...`. This is what ``H.download(search)`` sees,
            #     which is the path the sounding fetch takes.
            #   * the repo's JSON-lines parser (``_ecmwf_search_this_from_record``)
            #     drops empty fields: `:sp:sfc:...`.
            # Isobaric records carry a real levelist and are identical in both.
            return f":({tokens}):(?:nan:)?sfc:"
        clauses = "|".join(
            f"{field.search_token}:{field.level_label}" for field in self.surface_fields
        )
        return f":({clauses}):"

    def search_pattern_for_group(self, group: FetchGroup) -> str:
        parts: list[str] = []
        if group.isobaric:
            parts.append(self.isobaric_search_pattern())
        if group.surface:
            parts.append(self.surface_search_pattern())
        if not parts:
            raise ValueError(f"Fetch group {group.product} selects no planes")
        if len(parts) == 1:
            return parts[0]
        return "|".join(f"(?:{part})" for part in parts)

    def planes_for_group(self, group: FetchGroup) -> set[int]:
        planes: set[int] = set()
        if group.isobaric:
            planes |= set(range(self.n_isobaric_planes))
        if group.surface:
            planes |= set(range(self.n_isobaric_planes, self.n_planes))
        return planes

    # -- band identification ------------------------------------------------

    def plane_index_for_band_tags(self, tags: dict[str, Any]) -> int | None:
        """Map GDAL GRIB band tags to a plane of THIS model, or ``None``.

        Band order in a Herbie subset follows the upstream idx order, which is
        not guaranteed and differs between products, so planes are addressed by
        tag identity rather than by position.
        """
        element = str(tags.get("GRIB_ELEMENT", "") or "").strip().upper()
        short_name = str(tags.get("GRIB_SHORT_NAME", "") or "").strip().upper()
        if not element or not short_name:
            return None

        for index, field in enumerate(self.surface_fields):
            if element in field.elements and short_name in field.level_tokens:
                return self.surface_plane_index(index)

        match = _ISOBARIC_LEVEL_RE.match(short_name)
        if match is None:
            return None
        var_index: int | None = None
        for index, var in enumerate(self.isobaric_variables):
            if element in var.elements:
                var_index = index
                break
        if var_index is None:
            return None

        raw_level = float(match.group(1))
        # GDAL reports ISBL levels in hPa for HRRR/GFS, but normalize
        # defensively: anything above the top of a troposphere-scale ladder is Pa.
        level_hpa = raw_level / 100.0 if raw_level > 1100.0 else raw_level
        if not float(level_hpa).is_integer():
            return None
        try:
            level_index = self.levels_hpa.index(int(level_hpa))
        except ValueError:
            return None
        return self.isobaric_plane_index(level_index, var_index)


HRRR_SPEC = SoundingModelSpec(
    model_id="hrrr",
    herbie_model="hrrr",
    levels_hpa=LEVELS_HPA,
    isobaric_variables=ISOBARIC_VARIABLES,
    surface_fields=SURFACE_FIELDS,
    fetch_groups=(
        FetchGroup(ISOBARIC_PRODUCT, isobaric=True, surface=False),
        FetchGroup(SURFACE_PRODUCT, isobaric=False, surface=True),
    ),
    stride=DECIMATION_STRIDE,
    td_policy=TD_NATIVE,
    idx_dialect=IDX_NOAA,
)

GFS_SPEC = SoundingModelSpec(
    model_id="gfs",
    herbie_model="gfs",
    levels_hpa=GFS_LEVELS_HPA,
    isobaric_variables=_gfs_isobaric_variables(),
    surface_fields=_gfs_surface_fields(),
    # pgrb2 0.25 deg carries the isobaric ladder AND the surface block, so one
    # subset per fh (111 messages / 68 coalesced ranges, MEASURED §10).
    fetch_groups=(FetchGroup("pgrb2.0p25", isobaric=True, surface=True),),
    stride=GLOBAL_DECIMATION_STRIDE,
    td_policy=TD_FROM_Q,
    idx_dialect=IDX_NOAA,
)

ECMWF_SPEC = SoundingModelSpec(
    model_id="ecmwf",
    herbie_model="ifs",
    levels_hpa=ECMWF_LEVELS_HPA,
    isobaric_variables=_ecmwf_isobaric_variables(),
    surface_fields=_ecmwf_surface_fields(),
    # `oper` for EVERY cycle: `scda` is retired (MEASURED 2026-08-02, §10) and
    # must not be branched on.
    fetch_groups=(FetchGroup("oper", isobaric=True, surface=True),),
    stride=GLOBAL_DECIMATION_STRIDE,
    td_policy=TD_FROM_Q,
    idx_dialect=IDX_ECMWF,
    # The spike measured 1.49 MB/s (33.6 s/fh, ~48 min/run) against Herbie's
    # `aws` source for IFS, which is the eu-central-1 bucket — a transatlantic
    # pull from a US origin. Herbie's IFS class also exposes `google`
    # (storage.googleapis.com/ecmwf-open-data, multi-region) which the repo's
    # raster path has never tried for this model; `azure` is West Europe, i.e.
    # the same ocean. So: Google first, then exactly the raster plugin's
    # existing order as the fallback. UNVERIFIED from this session (no network
    # measurement available) — a failure just falls through, so the downside is
    # one extra 404-ish attempt, but the throughput win should be measured on
    # prod before the ECMWF run time is quoted anywhere.
    source_priority=("google", "azure", "aws", "ecmwf"),
)

MODEL_SPECS: dict[str, SoundingModelSpec] = {
    spec.model_id: spec for spec in (HRRR_SPEC, GFS_SPEC, ECMWF_SPEC)
}

#: Models wired for stack builds. Derived from the registry so a new row is the
#: only edit needed; the ``CARTOSKY_SOUNDING_MODELS`` flag is still the gate.
SUPPORTED_MODELS = frozenset(MODEL_SPECS)


def model_spec(model_id: str) -> SoundingModelSpec:
    model_norm = str(model_id).strip().lower()
    try:
        return MODEL_SPECS[model_norm]
    except KeyError:
        raise KeyError(f"no sounding spec for model {model_norm!r}") from None


def isobaric_plane_index(level_index: int, var_index: int) -> int:
    """Plane number for the *var_index*-th variable at the *level_index*-th level."""
    return HRRR_SPEC.isobaric_plane_index(level_index, var_index)


def surface_plane_index(field_index: int) -> int:
    return HRRR_SPEC.surface_plane_index(field_index)


def pixel_byte_offset(
    row: int, col: int, *, width: int, plane: int = 0, n_planes: int = N_PLANES
) -> int:
    """Byte offset of ``plane`` within pixel ``(row, col)`` (design §3).

    ``n_planes`` defaults to the *writer's* current layout but must be passed
    from the sidecar when reading, so a v1 stack stays readable after the
    module's own plane count moves on.
    """
    return ((int(row) * int(width) + int(col)) * int(n_planes) + int(plane)) * BYTES_PER_SAMPLE


def expected_stack_size_bytes(*, width: int, height: int, n_planes: int = N_PLANES) -> int:
    return int(width) * int(height) * int(n_planes) * BYTES_PER_SAMPLE


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------


def encode_plane(values: np.ndarray, pack: PackSpec) -> np.ndarray:
    """Quantize physical values to uint16 codes; non-finite -> nodata sentinel.

    Mirrors the house ``code * scale + offset`` convention (grid.py
    ``_encode_values``); values are clipped into the representable window so a
    freak outlier saturates instead of wrapping.
    """
    array = np.asarray(values, dtype=np.float32)
    encoded = np.full(array.shape, int(pack.nodata), dtype=np.uint16)
    valid = np.isfinite(array)
    if not np.any(valid):
        return encoded
    scaled = np.rint((array[valid] - float(pack.offset)) / float(pack.scale))
    encoded[valid] = np.clip(scaled, 0, pack.max_code).astype(np.uint16, copy=False)
    return encoded


def decode_plane(codes: np.ndarray, pack: PackSpec) -> np.ndarray:
    """Inverse of :func:`encode_plane`; nodata codes decode to NaN."""
    array = np.asarray(codes)
    values = array.astype(np.float32) * np.float32(pack.scale) + np.float32(pack.offset)
    return np.where(array == int(pack.nodata), np.float32(np.nan), values).astype(
        np.float32, copy=False
    )


def pack_spec_from_sidecar_entry(entry: dict[str, Any]) -> PackSpec:
    """Rebuild a :class:`PackSpec` from a sidecar variable/surface entry."""
    return PackSpec(
        units=str(entry.get("units", "")),
        scale=float(entry["scale"]),
        offset=float(entry["offset"]),
        nodata=int(entry.get("nodata", NODATA_CODE)),
    )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def sounding_dir(run_root: Path) -> Path:
    """Stacks live inside the run directory so retention is free (design §3)."""
    return Path(run_root) / "sounding"


def stack_paths(run_root: Path, fh: int) -> tuple[Path, Path]:
    base = sounding_dir(run_root)
    return base / f"fh{int(fh):03d}.stack.bin", base / f"fh{int(fh):03d}.stack.json"


def stack_exists(run_root: Path, fh: int) -> bool:
    stack_path, sidecar_path = stack_paths(run_root, fh)
    try:
        return stack_path.is_file() and stack_path.stat().st_size > 0 and sidecar_path.is_file()
    except OSError:
        return False


def available_stack_fhs(run_root: Path) -> list[int]:
    base = sounding_dir(run_root)
    if not base.is_dir():
        return []
    fhs: list[int] = []
    for child in sorted(base.glob("fh*.stack.bin")):
        match = re.match(r"^fh(\d+)\.stack\.bin$", child.name)
        if match is None:
            continue
        fh = int(match.group(1))
        if stack_exists(run_root, fh):
            fhs.append(fh)
    return sorted(fhs)


# ---------------------------------------------------------------------------
# GRIB search patterns (design §4 — never anchor a field name with "^")
# ---------------------------------------------------------------------------


def isobaric_search_pattern(spec: SoundingModelSpec = HRRR_SPEC) -> str:
    """One regex covering every variable x level of *spec*'s isobaric ladder.

    The level list is enumerated explicitly rather than using a ``\\d+ mb``
    wildcard, which would also drag in 1013.2/75/50 mb.
    """
    return spec.isobaric_search_pattern()


def surface_search_pattern(spec: SoundingModelSpec = HRRR_SPEC) -> str:
    return spec.surface_search_pattern()


# ---------------------------------------------------------------------------
# Band identification
# ---------------------------------------------------------------------------

_ISOBARIC_LEVEL_RE = re.compile(r"^(\d+(?:\.\d+)?)-ISBL$")


def plane_index_for_band_tags(
    tags: dict[str, Any], spec: SoundingModelSpec = HRRR_SPEC
) -> int | None:
    """Map GDAL GRIB band tags to a stack plane, or ``None`` if not wanted."""
    return spec.plane_index_for_band_tags(tags)


def pack_for_plane(plane: int, spec: SoundingModelSpec = HRRR_SPEC) -> PackSpec:
    return spec.pack_for_plane(plane)


def _converter_for_plane(
    plane: int, spec: SoundingModelSpec = HRRR_SPEC
) -> Callable[[np.ndarray], np.ndarray]:
    return spec.converter_for_plane(plane)


def _plane_label(plane: int, spec: SoundingModelSpec = HRRR_SPEC) -> str:
    return spec.plane_label(plane)


# ---------------------------------------------------------------------------
# Dewpoint from specific humidity (design §8 #7, Phase 6)
# ---------------------------------------------------------------------------


def vapor_pressure_from_q(q: np.ndarray, pressure_hpa: float) -> np.ndarray:
    """Water-vapour partial pressure (hPa) from specific humidity (kg/kg).

    ``e = q * p / (0.622 + 0.378 * q)`` — the exact inversion of
    ``q = 0.622 e / (p - 0.378 e)``, with 0.622 = Rd/Rv. Nothing here is
    phase-dependent: *e* is a partial pressure, not a saturation quantity.
    """
    values = np.asarray(q, dtype=np.float64)
    return (values * float(pressure_hpa) / (0.622 + 0.378 * values)).astype(
        np.float64, copy=False
    )


#: Coldest dewpoint the uint16 temperature packing can represent. Vapour
#: pressures below ~1e-8 hPa (a genuinely stratospheric q) invert to dewpoints
#: colder than this and are clipped rather than written as nodata: a trace that
#: stops has to be distinguishable from one that is simply very dry.
_TD_FLOOR_C = -120.0


def dewpoint_from_q(q: np.ndarray, pressure_hpa: float) -> np.ndarray:
    """Dewpoint (degC) from specific humidity at a known pressure level.

    Ice/liquid-unambiguous by construction (design §8 #7): the vapour pressure
    is computed from the mixing ratio, and MetPy's :func:`dewpoint` inverts the
    **liquid-water** saturation curve, so the result is a plain
    liquid-referenced dewpoint everywhere on the ladder. That is precisely the
    quantity the §1 RH trap destroyed — HRRR's RH is ice-referenced aloft, so
    deriving from it put ~11 degC of error on the upper half of the trace.

    Non-finite / non-positive q decodes to NaN (nodata), never to -inf.
    """
    from metpy.calc import dewpoint as metpy_dewpoint
    from metpy.units import units

    values = np.asarray(q, dtype=np.float64)
    finite = np.isfinite(values) & (values > 0.0)
    result = np.full(values.shape, np.nan, dtype=np.float32)
    if not np.any(finite):
        return result

    e = vapor_pressure_from_q(values[finite], pressure_hpa)
    e = np.maximum(e, 1e-12)
    td = np.asarray(
        metpy_dewpoint(e * units.hPa).to(units.degC).magnitude, dtype=np.float64
    )
    td = np.where(np.isfinite(td), np.maximum(td, _TD_FLOOR_C), np.nan)
    result[finite] = td.astype(np.float32, copy=False)
    return result


def derive_td_from_q_planes(
    planes: dict[int, np.ndarray], spec: SoundingModelSpec
) -> dict[int, np.ndarray]:
    """Convert a ``td_from_q`` model's td planes from q to dewpoint, in place.

    The td slot is *fetched* as specific humidity (there is no isobaric DPT in
    GFS pgrb2 or ECMWF open data — MEASURED, design §10), so this runs once
    after the fetch and before packing. Idempotence is not claimed: it must run
    exactly once per build, which is why it lives in
    :func:`build_stack_for_fh` rather than in the packer.
    """
    if spec.td_policy != TD_FROM_Q:
        return planes
    td_index = spec.variable_index("td")
    for level_index, level_hpa in enumerate(spec.levels_hpa):
        plane = spec.isobaric_plane_index(level_index, td_index)
        q_values = planes.get(plane)
        if q_values is None:
            continue
        planes[plane] = dewpoint_from_q(q_values, float(level_hpa))
    return planes


# ---------------------------------------------------------------------------
# Stack assembly
# ---------------------------------------------------------------------------


def decimate(values: np.ndarray, stride: int = DECIMATION_STRIDE) -> np.ndarray:
    """Take every *stride*-th point in both axes (design §3)."""
    return np.asarray(values)[:: int(stride), :: int(stride)]


def decimated_transform(transform: Any, stride: int = DECIMATION_STRIDE) -> Any:
    """Affine for the decimated grid: ``T'(c, r) == T(stride*c, stride*r)``."""
    from rasterio.transform import Affine

    return transform * Affine.scale(float(stride), float(stride))


def _check_physical_range(values: np.ndarray, *, pack: PackSpec, label: str) -> None:
    finite = np.isfinite(values)
    if not np.any(finite):
        return
    lo = float(np.min(values[finite]))
    hi = float(np.max(values[finite]))
    if lo < pack.valid_min or hi > pack.valid_max:
        raise ValueError(
            f"Sounding plane {label} outside its physical window "
            f"[{pack.valid_min}, {pack.valid_max}] ({pack.units}): min={lo} max={hi} — "
            "likely a units mismatch in the GRIB decode"
        )


def assemble_stack(
    planes: dict[int, np.ndarray],
    *,
    stride: int | None = None,
    require_complete: bool = True,
    spec: SoundingModelSpec = HRRR_SPEC,
) -> np.ndarray:
    """Decimate, range-check, quantize and interleave planes into the stack.

    Parameters
    ----------
    planes
        ``{plane_index: full-resolution 2-D float array}`` in *physical* units.
    Returns
    -------
    np.ndarray
        uint16 array shaped ``(height, width, N_PLANES)``; its C-order buffer
        is exactly the pixel-major on-disk layout.
    """
    if stride is None:
        stride = spec.stride
    if not planes:
        raise ValueError("Cannot assemble a sounding stack from zero planes")
    # Optional planes (ECMWF's local-parameter mucape) may legitimately go
    # missing without failing the build; every other plane is mandatory.
    missing = sorted(set(range(spec.n_planes)) - set(planes) - spec.optional_planes)
    if missing and require_complete:
        raise ValueError(
            f"Sounding stack missing {len(missing)} plane(s), e.g. "
            f"{[spec.plane_label(plane) for plane in missing[:5]]}"
        )

    shapes = {np.asarray(plane).shape for plane in planes.values()}
    if len(shapes) != 1:
        raise ValueError(f"Sounding planes disagree on shape: {sorted(shapes)}")

    sample = decimate(np.asarray(next(iter(planes.values()))), stride)
    height, width = sample.shape
    stack = np.full((height, width, spec.n_planes), NODATA_CODE, dtype=np.uint16)

    for plane, values in planes.items():
        pack = spec.pack_for_plane(plane)
        decimated = decimate(np.asarray(values, dtype=np.float32), stride)
        _check_physical_range(decimated, pack=pack, label=spec.plane_label(plane))
        stack[:, :, plane] = encode_plane(decimated, pack)
    return stack


def build_sidecar(
    *,
    model: str,
    run_id: str,
    fh: int,
    run_date: datetime,
    width: int,
    height: int,
    transform: Any,
    projection: str,
    source_width: int,
    source_height: int,
    stride: int | None = None,
    spec: SoundingModelSpec | None = None,
) -> dict[str, Any]:
    """Self-describing sidecar — a reader must need nothing beyond this JSON."""
    if spec is None:
        spec = MODEL_SPECS.get(str(model).strip().lower(), HRRR_SPEC)
    if stride is None:
        stride = spec.stride
    valid_time = run_date + timedelta(hours=int(fh))
    if valid_time.tzinfo is None:
        valid_time = valid_time.replace(tzinfo=timezone.utc)
    return {
        "format_version": SOUNDING_FORMAT_VERSION,
        "artifact": "sounding_stack",
        "model": str(model),
        "run": str(run_id),
        "fh": int(fh),
        "valid_time": valid_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "width": int(width),
        "height": int(height),
        "transform": [float(value) for value in tuple(transform)[:6]],
        "projection": str(projection),
        "source_width": int(source_width),
        "source_height": int(source_height),
        "decimation_stride": int(stride),
        "levels_hPa": [int(level) for level in spec.levels_hpa],
        "variables": [
            {
                "id": var.id,
                "grib_element": var.grib_element,
                "units": var.pack.units,
                "scale": float(var.pack.scale),
                "offset": float(var.pack.offset),
                "nodata": int(var.pack.nodata),
            }
            for var in spec.isobaric_variables
        ],
        "surface_fields": [
            {
                "id": field.id,
                "grib_element": field.grib_element,
                "level": field.level_label,
                "units": field.pack.units,
                "scale": float(field.pack.scale),
                "offset": float(field.pack.offset),
                "nodata": int(field.pack.nodata),
                **({"display_label": field.display_label} if field.display_label else {}),
            }
            for field in spec.surface_fields
        ],
        # How the td plane got there. Not read by anything today; it is the one
        # provenance fact a served profile cannot be re-derived from.
        "td_policy": str(spec.td_policy),
        "layout": "pixel_major",
        "n_planes": int(spec.n_planes),
        "n_isobaric_planes": int(spec.n_isobaric_planes),
        "bytes_per_pixel": int(spec.bytes_per_pixel),
        "bytes_per_sample": int(BYTES_PER_SAMPLE),
        "dtype": "uint16",
        "byte_order": "little",
        "plane_formula": "((row * width + col) * n_planes + plane) * bytes_per_sample",
        "isobaric_plane_formula": "level_index * n_variables + var_index",
        "surface_plane_offset": int(spec.n_isobaric_planes),
    }


def write_stack(
    *,
    run_root: Path,
    fh: int,
    stack: np.ndarray,
    sidecar: dict[str, Any],
) -> tuple[Path, Path]:
    """Write ``fhNNN.stack.bin`` + ``.json`` atomically (binary first).

    The expected plane count comes from the sidecar, not from this module's
    own constants: one deployment writes several models' layouts.
    """
    if stack.dtype != np.uint16:
        raise ValueError(f"Sounding stack must be uint16, got {stack.dtype}")
    expected_planes = int(sidecar.get("n_planes", N_PLANES))
    if stack.ndim != 3 or stack.shape[2] != expected_planes:
        raise ValueError(f"Sounding stack has wrong shape: {stack.shape}")

    stack_path, sidecar_path = stack_paths(run_root, fh)
    stack_path.parent.mkdir(parents=True, exist_ok=True)

    stack_tmp = stack_path.with_suffix(stack_path.suffix + ".tmp")
    stack_tmp.write_bytes(np.ascontiguousarray(stack).astype(STACK_DTYPE, copy=False).tobytes())
    stack_tmp.replace(stack_path)

    sidecar_tmp = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
    sidecar_tmp.write_text(json.dumps(sidecar, indent=2) + "\n")
    sidecar_tmp.replace(sidecar_path)
    return stack_path, sidecar_path


# ---------------------------------------------------------------------------
# Reader (used by the tests now, by the Phase 2 endpoint later)
# ---------------------------------------------------------------------------


def read_pixel_codes(
    stack_path: Path, *, width: int, row: int, col: int, n_planes: int = N_PLANES
) -> np.ndarray:
    """One seek + one small read -> the raw uint16 codes for a grid point.

    ``n_planes`` comes from the sidecar (380 B for v1, 382 B for v2).
    """
    n_planes = int(n_planes)
    nbytes = n_planes * BYTES_PER_SAMPLE
    offset = pixel_byte_offset(row, col, width=width, n_planes=n_planes)
    with open(stack_path, "rb") as handle:
        handle.seek(offset)
        payload = handle.read(nbytes)
    if len(payload) != nbytes:
        raise ValueError(
            f"Short read at offset {offset} in sounding stack: {stack_path} "
            f"({len(payload)} of {nbytes} bytes)"
        )
    return np.frombuffer(payload, dtype=STACK_DTYPE)


def _finite_or_none(value: float) -> float | None:
    return None if not math.isfinite(value) else float(value)


def read_profile(stack_path: Path, sidecar: dict[str, Any], *, row: int, col: int) -> dict[str, Any]:
    """Decode one column into physical units, driven entirely by *sidecar*.

    Below-ground levels are returned as stored: masking against the surface
    pressure is a display concern (design §6), never a storage one.
    """
    width = int(sidecar["width"])
    height = int(sidecar["height"])
    if not (0 <= int(row) < height and 0 <= int(col) < width):
        raise IndexError(f"({row}, {col}) outside sounding grid {height}x{width}")

    n_planes = int(sidecar["n_planes"])
    codes = read_pixel_codes(stack_path, width=width, row=row, col=col, n_planes=n_planes)
    if codes.size != n_planes:
        raise ValueError(f"Sounding pixel has {codes.size} planes, sidecar declares {n_planes}")

    levels = [int(level) for level in sidecar["levels_hPa"]]
    variables = list(sidecar["variables"])
    surface_entries = list(sidecar["surface_fields"])
    n_vars = len(variables)

    profile: dict[str, Any] = {"levels_hPa": levels}
    for var_index, entry in enumerate(variables):
        pack = pack_spec_from_sidecar_entry(entry)
        plane_indices = [level_index * n_vars + var_index for level_index in range(len(levels))]
        decoded = decode_plane(codes[plane_indices], pack)
        profile[str(entry["id"])] = [_finite_or_none(value) for value in decoded]

    surface_offset = int(sidecar.get("surface_plane_offset", len(levels) * n_vars))
    surface: dict[str, float | None] = {}
    for field_index, entry in enumerate(surface_entries):
        pack = pack_spec_from_sidecar_entry(entry)
        decoded = decode_plane(codes[surface_offset + field_index : surface_offset + field_index + 1], pack)
        surface[str(entry["id"])] = _finite_or_none(float(decoded[0]))
    profile["surface"] = surface
    return profile


def read_sidecar(run_root: Path, fh: int) -> dict[str, Any]:
    _stack_path, sidecar_path = stack_paths(run_root, fh)
    return json.loads(Path(sidecar_path).read_text())


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _fetch_product_planes(
    *,
    model_id: str,
    product: str,
    search_pattern: str,
    run_date: datetime,
    fh: int,
    wanted_planes: set[int],
    spec: SoundingModelSpec = HRRR_SPEC,
) -> tuple[dict[int, np.ndarray], Any, Any]:
    """Download ONE Herbie subset for *product* and split it into planes.

    Sequential and AWS-first: the priority sequence comes from the shared
    fetch layer (``CARTOSKY_HERBIE_PRIORITY`` / ``DEFAULT_HERBIE_PRIORITY``),
    and each priority is tried once before falling through.
    """
    from app.services.builder import fetch as fetch_module

    herbie_type = fetch_module._import_herbie()
    herbie_date = run_date.replace(tzinfo=None) if run_date.tzinfo else run_date
    candidates = list(spec.source_priority) or fetch_module._priority_candidates(None)
    priorities = [
        fetch_module._priority_normalized(item)
        for item in candidates
        if str(item).strip()
    ]

    work_dir = Path(tempfile.mkdtemp(prefix=f"cartosky-sounding-{product}-"))
    last_error: Exception | None = None
    try:
        for priority in priorities:
            subset_hint = work_dir / f"{model_id}.{product}.fh{int(fh):03d}.{priority}.grib2"
            try:
                herbie = fetch_module._construct_herbie(
                    herbie_type,
                    herbie_date,
                    {
                        "model": model_id,
                        "product": product,
                        "fxx": int(fh),
                        "priority": priority,
                        "verbose": False,
                    },
                )
                # Asserts the file exists and is non-empty: Herbie can return a
                # path it never wrote (design §4 gotcha).
                grib_path = fetch_module._download_herbie_subset_isolated(
                    herbie, search_pattern=search_pattern, subset_hint=subset_hint
                )
            except Exception as exc:  # noqa: BLE001 - try the next source
                last_error = exc
                logger.warning(
                    "Sounding fetch failed: model=%s product=%s fh%03d priority=%s: %s",
                    model_id,
                    product,
                    fh,
                    priority,
                    exc,
                )
                continue

            planes, crs, transform = _planes_from_grib(
                grib_path, wanted_planes=wanted_planes, spec=spec
            )
            logger.info(
                "Sounding fetch ok: model=%s product=%s fh%03d priority=%s planes=%d bytes=%d",
                model_id,
                product,
                fh,
                priority,
                len(planes),
                grib_path.stat().st_size,
            )
            return planes, crs, transform
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    raise RuntimeError(
        f"Sounding fetch exhausted all sources: model={model_id} product={product} "
        f"fh={fh} priorities={priorities}"
    ) from last_error


def _planes_from_grib(
    grib_path: Path, *, wanted_planes: set[int], spec: SoundingModelSpec = HRRR_SPEC
) -> tuple[dict[int, np.ndarray], Any, Any]:
    """Read every band of a multi-message GRIB and index it by plane."""
    import rasterio

    from app.services.builder.fetch import _read_rasterio_band

    planes: dict[int, np.ndarray] = {}
    with rasterio.open(grib_path) as src:
        band_count = int(getattr(src, "count", 0))
        if band_count <= 0:
            raise RuntimeError(f"Sounding subset contains no GRIB bands: {grib_path}")
        for band_index in range(1, band_count + 1):
            tags = src.tags(band_index)
            plane = spec.plane_index_for_band_tags(tags)
            if plane is None or plane not in wanted_planes:
                continue
            if plane in planes:
                raise RuntimeError(
                    f"Duplicate GRIB message for sounding plane "
                    f"{spec.plane_label(plane)} in {grib_path}"
                )
            # ``_read_rasterio_band`` already masks nodata and converts K -> degC.
            planes[plane] = spec.converter_for_plane(plane)(
                _read_rasterio_band(src, band_index=band_index)
            )
        crs = src.crs
        transform = src.transform
    return planes, crs, transform


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_stack_for_fh(
    *,
    model_id: str,
    run_id: str,
    run_date: datetime,
    fh: int,
    run_root: Path,
    overwrite: bool = False,
) -> Path | None:
    """Fetch, pack and write the sounding stack for one forecast hour.

    Returns the stack path, or ``None`` when a usable stack already exists
    (resume idempotence). Raises on failure; the scheduler wrapper swallows.
    """
    model_norm = str(model_id).strip().lower()
    if model_norm not in SUPPORTED_MODELS:
        logger.info("Sounding build skipped: model=%s has no sounding spec", model_norm)
        return None
    if not overwrite and stack_exists(run_root, fh):
        logger.debug("Sounding stack already present: run=%s fh%03d", run_id, fh)
        return None

    spec = model_spec(model_norm)

    planes: dict[int, np.ndarray] = {}
    crs: Any = None
    transform: Any = None
    for group in spec.fetch_groups:
        group_planes, group_crs, group_transform = _fetch_product_planes(
            model_id=spec.herbie_model,
            product=group.product,
            search_pattern=spec.search_pattern_for_group(group),
            run_date=run_date,
            fh=fh,
            wanted_planes=spec.planes_for_group(group),
            spec=spec,
        )
        planes.update(group_planes)
        # The isobaric group defines the grid; HRRR's sfc file is the same grid
        # and its geometry is deliberately ignored (Phase 1 behaviour).
        if crs is None or group.isobaric:
            crs, transform = group_crs, group_transform

    # Td-from-q runs on the RAW planes, before decimation and packing: the
    # inversion is per-level and non-linear, so deriving after decimation would
    # be the same answer but deriving after *packing* would quantize q first.
    derive_td_from_q_planes(planes, spec)

    source_height, source_width = np.asarray(next(iter(planes.values()))).shape
    stack = assemble_stack(planes, spec=spec)
    height, width = stack.shape[0], stack.shape[1]
    sidecar = build_sidecar(
        model=model_norm,
        run_id=run_id,
        fh=fh,
        run_date=run_date,
        width=width,
        height=height,
        transform=decimated_transform(transform, spec.stride),
        projection=crs.to_wkt() if hasattr(crs, "to_wkt") else str(crs),
        source_width=source_width,
        source_height=source_height,
        spec=spec,
    )
    stack_path, _sidecar_path = write_stack(run_root=run_root, fh=fh, stack=stack, sidecar=sidecar)
    logger.info(
        "Sounding stack written: run=%s fh%03d path=%s bytes=%d grid=%dx%d",
        run_id,
        fh,
        stack_path,
        stack_path.stat().st_size,
        width,
        height,
    )
    return stack_path


def build_stacks_for_run(
    *,
    model_id: str,
    run_id: str,
    run_date: datetime,
    run_root: Path,
    fhs: Iterable[int],
) -> tuple[int, int]:
    """Build every missing stack in *fhs*; never raises.

    Returns ``(built, failed)``. A stack failure must never fail the run
    publish — the raster pipeline is entirely independent of this artifact.
    """
    model_norm = str(model_id).strip().lower()
    if model_norm not in SUPPORTED_MODELS:
        logger.info(
            "Sounding pass skipped: model=%s enabled by flag but has no sounding spec",
            model_norm,
        )
        return 0, 0

    built = 0
    failed = 0
    for fh in sorted({int(value) for value in fhs}):
        try:
            if build_stack_for_fh(
                model_id=model_norm,
                run_id=run_id,
                run_date=run_date,
                fh=fh,
                run_root=run_root,
            ) is not None:
                built += 1
        except Exception:
            failed += 1
            logger.exception(
                "Sounding stack build failed (run publish unaffected): model=%s run=%s fh%03d",
                model_norm,
                run_id,
                fh,
            )
    return built, failed


def manifest_section(
    *,
    run_root: Path,
    expected_fhs: Sequence[int],
) -> dict[str, Any]:
    """Top-level ``sounding`` block for the run manifest (additive)."""
    return {
        "format_version": SOUNDING_FORMAT_VERSION,
        "path_template": "sounding/fh{fh:03d}.stack.bin",
        "sidecar_template": "sounding/fh{fh:03d}.stack.json",
        "expected_fhs": [int(fh) for fh in sorted(set(int(v) for v in expected_fhs))],
        "available_fhs": available_stack_fhs(run_root),
    }


# ---------------------------------------------------------------------------
# Manual dry run
# ---------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - operator tool
    import argparse

    parser = argparse.ArgumentParser(description="Build one sounding stack for spot checks.")
    parser.add_argument("--model", default="hrrr")
    parser.add_argument("--run", required=True, help="Run id, e.g. 20260730_12z")
    parser.add_argument("--fh", type=int, required=True)
    parser.add_argument("--out", required=True, help="Run root directory to write into")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--row", type=int, default=None, help="Decimated-grid row to dump a profile for"
    )
    parser.add_argument("--col", type=int, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    run_date = datetime.strptime(args.run.replace("z", ""), "%Y%m%d_%H").replace(tzinfo=timezone.utc)
    run_root = Path(args.out)
    build_stack_for_fh(
        model_id=args.model,
        run_id=args.run,
        run_date=run_date,
        fh=args.fh,
        run_root=run_root,
        overwrite=bool(args.overwrite),
    )
    sidecar = read_sidecar(run_root, args.fh)
    print(json.dumps(sidecar, indent=2))
    if args.row is not None and args.col is not None:
        stack_path, _ = stack_paths(run_root, args.fh)
        profile = read_profile(stack_path, sidecar, row=args.row, col=args.col)
        print(json.dumps(profile, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
