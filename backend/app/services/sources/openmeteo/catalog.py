"""Per-model configuration for the Open-Meteo fast path — pure data, no I/O.

This is where model-agnosticism lives (design §2): adding ICON/GEM/UKMO later
means adding an :class:`OmModelCatalog` entry — bucket directory, grid kind,
per-cycle cadence ladder, variable map — not writing code.

===========================================================================
UNITS AUDIT — ECMWF ``ecmwf_ifs`` (9 km O1280), 2026-08-05
===========================================================================

**Why this audit is mandatory:** the *same* variable name carries *different*
units in different products of the same bucket — ``pressure_msl`` is Pa in
``ecmwf_ifs`` and hPa in ``ecmwf_ifs025``
(``docs/ECMWF_OPENMETEO_9KM_PROTOTYPE_2026-08-04.md`` §4 gotcha 2). Never
reuse a mapping across products.

**Verification method.** Every ``.om`` unit below was confirmed against the
Open-Meteo point API for model ``ecmwf_ifs`` (the same product), run
2026-08-04 18z, valid 2026-08-05T06Z, nearest O1280 point to 41.863°N /
272.351°E, with ``wind_speed_unit=ms``:

===========================  ==============  ==========================
field                        ``.om`` raw     point API (SI/°C/hPa)
===========================  ==============  ==========================
``temperature_2m``           22.45           22.5 °C          → °C
``pressure_msl``             101560.0        1015.6 hPa       → **Pa**
``wind_gusts_10m``           5.30            5.3 m/s          → m/s
``hypot(u10, v10)``          2.5676          2.57 m/s         → m/s
``atan2`` of (u10, v10)      186.7°          187°             → sign OK
===========================  ==============  ==========================

Accumulation units come from prototype Phase 3 (``.om`` per-step values
reconciled against ``ecmwf_ifs025`` to ≤ 0.0012 mm bias, i.e. mm) and gusts
additionally from Phase 4 (fast-vs-legacy MAE 0.24 **m/s**, bias +0.000 — a
km/h field would have shown a ~2.6× bias).

**Production side.** For each row, "production stores" is the value the
existing builder hands to the packer, i.e. *after*
``fetch.convert_units`` and *before* ``grid.write_grid_frame_for_run_root``
packs it.

+---------------------------+----------------+---------+-----------------+--------------------------+
| ``.om`` variable          | ``.om`` unit   | CartoSky| conversion here | production stored unit   |
+===========================+================+=========+=================+==========================+
| ``temperature_2m``        | °C             |``tmp2m``| ``c_to_f``      | °F                       |
+---------------------------+----------------+---------+-----------------+--------------------------+
| ``dew_point_2m``          | °C             |``dp2m`` | ``c_to_f``      | °F                       |
+---------------------------+----------------+---------+-----------------+--------------------------+
| ``precipitation``         | mm (per step)  |``precip_| ``mm_to_in``    | in                       |
|                           |                |total``  |                 |                          |
+---------------------------+----------------+---------+-----------------+--------------------------+
| ``snowfall_water_         | mm SWE         |``snow-  | ``mm_swe_to_    | in (10:1 snow)           |
| equivalent``              | (per step)     |fall_    | in_10to1``      |                          |
|                           |                |total``  |                 |                          |
+---------------------------+----------------+---------+-----------------+--------------------------+
| ``pressure_msl``          | **Pa**         |``msl``  | none            | Pa (component)           |
+---------------------------+----------------+---------+-----------------+--------------------------+
| ``wind_gusts_10m``        | m/s            |``wgst10m|``ms_to_mph``    | mph                      |
|                           |                |``       |                 |                          |
+---------------------------+----------------+---------+-----------------+--------------------------+
| ``wind_u_component_10m``  | m/s            |``10u``  | none            | m/s (component)          |
+---------------------------+----------------+---------+-----------------+--------------------------+
| ``wind_v_component_10m``  | m/s            |``10v``  | none            | m/s (component)          |
+---------------------------+----------------+---------+-----------------+--------------------------+
| ``showers``               | mm (per step)  | —       | —               | DISABLED (no counterpart)|
+---------------------------+----------------+---------+-----------------+--------------------------+
| ``snow_depth``            | —              | —       | —               | DISABLED (not published) |
+---------------------------+----------------+---------+-----------------+--------------------------+

Per-row citations
-----------------

``temperature_2m`` → ``tmp2m``
    ``app/models/ecmwf.py:274-292`` (VarSpec, ``primary=True``, ``units="F"``);
    ``app/models/ecmwf.py:1150-1151`` (``ECMWF_CONVERSION_BY_VAR_KEY["tmp2m"]
    == "c_to_f"``); ``app/services/builder/fetch.py:5251-5258``
    (``_celsius_to_fahrenheit``: ``data * 9.0 / 5.0 + 32.0``);
    ``app/services/grid.py:295-300`` (packing ``units="F"``, scale 0.1,
    offset −100). GDAL's GRIB driver already normalises Kelvin → °C
    (``fetch.py:5254-5256``), so the delayed builder's input is °C too —
    identical conversion, no offset difference.

``dew_point_2m`` → ``dp2m``
    ``ecmwf.py:293-311`` (``units="F"``); ``ecmwf.py:1152``
    (``"dp2m": "c_to_f"``); ``grid.py:559-564`` (packing ``units="F"``).

``precipitation`` → ``precip_total``
    ``ecmwf.py:329-349`` — **``primary=True``, no ``derive=``**: production
    feeds this variable the GRIB's *run-cumulative* ``tp`` directly, the
    cumulative-derive machinery is not on its path (design §4, Codex finding
    4). ``ecmwf.py:1156`` (``"precip_total": "m_to_in"``) —
    the GRIB value is **metres**, so ``fetch.py:5278-5280``
    (``_meters_to_inches``, ×39.37007874015748) yields **inches**;
    ``grid.py:649-654`` packs ``units="in"``, scale 0.01. The ``.om`` field is
    **mm per step**, so the fast path multiplies by 0.03937007874015748
    (exactly 1/1000 of the production factor, identical to
    ``_kgm2_to_inches``, ``fetch.py:5283-5288``) *after* summing steps into a
    run-cumulative total. ``ecmwf.py:1163-1166`` pins ``min_fh: 3``.

``snowfall_water_equivalent`` → ``snowfall_total``
    ``ecmwf.py:451-467`` (``primary=True``, ``units="in"``, name "Total
    Snowfall (10:1)"); ``ecmwf.py:1158``
    (``"snowfall_total": "m_swe_to_in_10to1"``);
    ``fetch.py:5291-5293`` (``_meters_swe_to_10to1_snow_inches``,
    ×39.37007874015748×10) — production's GRIB ``sf`` is **metres of water
    equivalent** (``ecmwf.py:518-533``, the raw ``sf`` component VarSpec
    declares ``units="m"``), and the 10:1 ratio is applied *inside* the unit
    conversion, not by a derive. ``grid.py:703-708`` packs ``units="in"``,
    scale 0.1. The ``.om`` field is **mm SWE per step**, so the fast path uses
    ×0.03937007874015748×10 (``_kgm2_swe_to_in_10to1``'s factor,
    ``fetch.py:5296-5301``) on the run-cumulative sum. ``ecmwf.py:1170-1172``
    pins ``min_fh: 3``. Kuchera (``snowfall_kuchera_total``) is **not** fast-
    owned — it needs 925/850/700/600 hPa temps the 9 km set does not carry
    (design §3 rule 3, Decision 3: two-tier snow).

``pressure_msl`` → ``msl``
    ``ecmwf.py:807-822`` — VarSpec declares ``units="Pa"`` and there is **no**
    entry in ``ECMWF_CONVERSION_BY_VAR_KEY`` (``ecmwf.py:1150-1161``) and
    **no** packing entry in ``_PACKING_BY_MODEL_VAR``
    (``grid.py:90-1213``): ``msl`` is a *component*, not a published artifact.
    It is consumed as the MSLP contour/pressure-centre overlay of other
    products, which applies ``pressure_pa_to_hpa`` at overlay time
    (``ecmwf.py:361-367`` ``contour_component``/``contour_conversion``;
    ``fetch.py:5309-5311``). The ``.om`` value is already **Pa**, so the fast
    path stores it unconverted — matching the component contract exactly.
    (``ecmwf_ifs025``'s ``pressure_msl`` is hPa; do not copy this row to that
    product.)

``wind_gusts_10m`` → ``wgst10m``
    ``ecmwf.py:989-1003`` (``primary=True``, ``units="mph"``);
    ``ecmwf.py:1160`` (``"wgst10m": "ms_to_mph"``);
    ``fetch.py:5268-5270`` (×2.23694); ``grid.py:739-744`` packs
    ``units="mph"``, scale 0.1. ``ecmwf.py:1179-1181`` pins ``min_fh: 3``
    (and ``ecmwf.py:220`` special-cases gust selection by fh) — gusts are a
    max-over-interval field, so FH000 has none (prototype §4 gotcha 4).

``wind_u_component_10m`` / ``wind_v_component_10m`` → ``10u`` / ``10v``
    ``ecmwf.py:824-838`` / ``ecmwf.py:857-871`` — bare component VarSpecs with
    no ``units=``, no conversion entry and no packing entry: they exist only
    to feed ``wspd10m``, which is ``derived=True, derive="wspd10m"``
    (``ecmwf.py:975-987``) and carries the ``ms_to_mph`` conversion itself
    (``ecmwf.py:1159``, packed ``units="mph"`` at ``grid.py:727-732``). So
    the components stay in **m/s** and **WP3 wires them into the existing
    ``wspd10m`` derive** — they are kept ``enabled=True`` as fetch-only
    components (``component_only=True``), not as publishable variables.

``showers`` — DISABLED
    ECMWF has no convective-precipitation variable at all
    (``ECMWF_VARS``, ``ecmwf.py:273-1010``, has no ``cp``/``showers``
    counterpart). It was fetched in the prototype purely as the
    ``cp ≤ tp`` bound proving per-step accumulation semantics
    (prototype Phase 3). No CartoSky mapping → not in the launch set.

``snow_depth`` — DISABLED
    Not published by ``ecmwf_ifs`` at all: the run manifest's ``variables``
    list (verified live 2026-08-04 18z) has 35 entries and ``snow_depth`` is
    not among them, and neither is there a CartoSky ECMWF counterpart. Two
    independent reasons to leave it off.

Launch set = the 8 enabled rows (5 publishable + the ``10u``/``10v``/``msl``
component-only entries),
matching design Decision 4 ("the validated 10 winter vars", of which two have
no CartoSky counterpart).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping

import numpy as np

#: Required on every public frame and export before launch (design §8).
ATTRIBUTION_ECMWF_IFS = "ECMWF IFS data © ECMWF, via Open-Meteo (CC-BY-4.0)"

#: Public S3 base for the Open-Meteo relay bucket.
BUCKET_BASE_URL = "https://openmeteo.s3.amazonaws.com"


class GridKind(str, Enum):
    """Source grid family — dispatches the decode and the sampler."""

    O1280 = "o1280"
    REGULAR_025 = "regular025"


class VariableKind(str, Enum):
    """``instantaneous`` values are used as read; ``accumulation`` values are
    **per-step** in ``.om`` (prototype Phase 3) and must be summed from run
    start before conversion."""

    INSTANTANEOUS = "instantaneous"
    ACCUMULATION = "accumulation"


class OmConversion(str, Enum):
    """Source-unit → production-stored-unit conversions.

    Named for the ``.om`` source unit, deliberately **not** reusing the
    production conversion ids: production's ECMWF ``precip_total`` conversion
    is ``m_to_in`` because GRIB ``tp`` is metres, while the ``.om`` field is
    mm. Same destination, different source. Factors are pinned to the
    production constants so the two paths agree bit-for-bit where the physics
    agrees (see the module docstring's per-row citations).
    """

    NONE = "none"
    C_TO_F = "c_to_f"
    MM_TO_IN = "mm_to_in"
    MM_SWE_TO_IN_10TO1 = "mm_swe_to_in_10to1"
    MS_TO_MPH = "ms_to_mph"


def _c_to_f(values: np.ndarray) -> np.ndarray:
    """°C → °F. Mirrors ``fetch.py:5251-5258``."""
    return values * 9.0 / 5.0 + 32.0


def _mm_to_in(values: np.ndarray) -> np.ndarray:
    """mm → inches. Mirrors ``fetch.py:5283-5288`` (1 mm ≡ 1 kg/m²)."""
    return values * 0.03937007874015748


def _mm_swe_to_in_10to1(values: np.ndarray) -> np.ndarray:
    """mm SWE → inches of 10:1 snow. Mirrors ``fetch.py:5296-5301``."""
    return values * 0.03937007874015748 * 10.0


def _ms_to_mph(values: np.ndarray) -> np.ndarray:
    """m/s → mph. Mirrors ``fetch.py:5268-5270``."""
    return values * 2.23694


#: Conversion id → callable. ``NONE`` maps to ``None``: the caller stores the
#: value unchanged.
OM_UNIT_CONVERTERS: Mapping[OmConversion, Callable[[np.ndarray], np.ndarray] | None] = {
    OmConversion.NONE: None,
    OmConversion.C_TO_F: _c_to_f,
    OmConversion.MM_TO_IN: _mm_to_in,
    OmConversion.MM_SWE_TO_IN_10TO1: _mm_swe_to_in_10to1,
    OmConversion.MS_TO_MPH: _ms_to_mph,
}


@dataclass(frozen=True)
class OmVariable:
    """One bucket variable and how it feeds a CartoSky variable."""

    om_name: str
    cartosky_var: str | None
    conversion: OmConversion
    kind: VariableKind
    om_unit: str
    production_unit: str
    enabled: bool = True
    #: Fetched to feed a derive/overlay, never published as its own artifact
    #: (``10u``/``10v`` → ``wspd10m``; ``msl`` → MSLP contours).
    component_only: bool = False
    note: str = ""

    @property
    def converter(self) -> Callable[[np.ndarray], np.ndarray] | None:
        return OM_UNIT_CONVERTERS[self.conversion]

    def convert(self, values: np.ndarray) -> np.ndarray:
        """Apply the unit conversion (identity when ``NONE``).

        For :attr:`VariableKind.ACCUMULATION` the input must already be the
        **run-cumulative** sum of ``.om`` per-step values: production stores
        run-cumulative totals for ECMWF ``precip_total`` / ``snowfall_total``
        (design §4).
        """
        converter = self.converter
        if converter is None:
            return values
        return converter(values)


@dataclass(frozen=True)
class CadenceSegment:
    """``step_hours`` spacing out to and including ``through_fh``."""

    step_hours: int
    through_fh: int


@dataclass(frozen=True)
class AggregationWindow:
    """One published accumulation frame and the ``.om`` steps that make it.

    ``source_fhs`` are the ``.om`` forecast hours whose **per-step** values sum
    to this frame's incremental accumulation; ``step_hours`` is the production
    frame's own interval length.
    """

    target_fh: int
    source_fhs: tuple[int, ...]
    step_hours: int


@dataclass(frozen=True)
class OmModelCatalog:
    """Everything model-specific about one bucket model."""

    model_id: str
    bucket_dir: str
    grid: GridKind
    #: cycle hour → the ``.om`` step ladder for that cycle.
    source_cadence: Mapping[int, tuple[CadenceSegment, ...]]
    #: The production publish ladder this source must match (design Decision 2:
    #: hourly ``.om`` steps are aggregated into today's 3 h frames below FH90).
    target_cadence: tuple[CadenceSegment, ...]
    variables: tuple[OmVariable, ...]
    attribution: str
    #: Honest per-variable resolution label (design §8).
    source_resolution_label: str
    #: Points on the source grid — the reader's shape-dispatch expectation.
    source_points: int | None = None
    _by_om_name: dict[str, OmVariable] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "_by_om_name", {var.om_name: var for var in self.variables}
        )

    # -- variables ----------------------------------------------------------

    def variable(self, om_name: str) -> OmVariable:
        try:
            return self._by_om_name[om_name]
        except KeyError:
            raise KeyError(
                f"{self.model_id}: no catalog entry for .om variable {om_name!r}"
            ) from None

    def enabled_variables(self) -> tuple[OmVariable, ...]:
        return tuple(var for var in self.variables if var.enabled)

    def publishable_variables(self) -> tuple[OmVariable, ...]:
        """Enabled variables that map to a published CartoSky artifact."""
        return tuple(
            var for var in self.variables if var.enabled and not var.component_only
        )

    def accumulation_variables(self) -> tuple[OmVariable, ...]:
        return tuple(
            var
            for var in self.enabled_variables()
            if var.kind is VariableKind.ACCUMULATION
        )

    # -- cadence ------------------------------------------------------------

    def cadence(self, cycle_hour: int) -> tuple[CadenceSegment, ...]:
        try:
            return self.source_cadence[int(cycle_hour)]
        except KeyError:
            raise KeyError(
                f"{self.model_id}: no cadence ladder for cycle {cycle_hour!r} "
                f"(known: {sorted(self.source_cadence)})"
            ) from None

    def horizon(self, cycle_hour: int) -> int:
        return self.cadence(cycle_hour)[-1].through_fh


# ---------------------------------------------------------------------------
# Ladder helpers (pure functions; re-exported from source.py)
# ---------------------------------------------------------------------------


def _ladder_fhs(segments: tuple[CadenceSegment, ...], *, horizon: int) -> tuple[int, ...]:
    """Forecast hours produced by a cadence ladder, starting at FH000."""
    fhs: list[int] = [0]
    previous = 0
    for segment in segments:
        through = min(int(segment.through_fh), int(horizon))
        step = int(segment.step_hours)
        if step <= 0:
            raise ValueError(f"cadence step_hours must be positive, got {step}")
        fh = previous + step
        while fh <= through:
            fhs.append(fh)
            fh += step
        previous = max(previous, through)
        if previous >= horizon:
            break
    return tuple(fhs)


def expected_fhs(catalog: OmModelCatalog, cycle_hour: int) -> tuple[int, ...]:
    """The ``.om`` forecast hours this cycle should publish, FH000 first.

    Object existence is truth for liveness (design §4); this is the
    *expectation* used for completeness accounting only.
    """
    return _ladder_fhs(catalog.cadence(cycle_hour), horizon=catalog.horizon(cycle_hour))


def expected_file_count(catalog: OmModelCatalog, cycle_hour: int) -> int:
    """Object count for a complete cycle (145 for ECMWF 00z/12z, 109 for 06z/18z)."""
    return len(expected_fhs(catalog, cycle_hour))


def aggregation_boundaries(
    catalog: OmModelCatalog, cycle_hour: int
) -> tuple[AggregationWindow, ...]:
    """Map ``.om`` per-step accumulation fhs onto the production frame ladder.

    Below FH90 the ``.om`` cadence is hourly while production publishes 3 h
    frames, so steps 1–3 sum into FH003, 4–6 into FH006, and so on (design
    Decision 2). At and beyond FH90 the ladders coincide 1:1 — the FH90 and
    FH144 cadence transitions are the two boundaries that must line up, and
    they do because every production frame hour is also an ``.om`` step hour.

    The returned windows **partition** ``expected_fhs`` minus FH000 exactly:
    no gaps, no overlaps. FH000 is excluded because it carries no accumulation
    fields at all (prototype §4 gotcha 4).
    """
    horizon = catalog.horizon(cycle_hour)
    source = set(expected_fhs(catalog, cycle_hour)) - {0}
    targets = _ladder_fhs(catalog.target_cadence, horizon=horizon)[1:]

    windows: list[AggregationWindow] = []
    previous = 0
    for target_fh in targets:
        if target_fh not in source:
            raise ValueError(
                f"{catalog.model_id} {cycle_hour:02d}z: production frame FH{target_fh:03d} "
                f"has no matching .om step — cadence ladders have diverged"
            )
        members = tuple(fh for fh in sorted(source) if previous < fh <= target_fh)
        if not members:
            raise ValueError(
                f"{catalog.model_id} {cycle_hour:02d}z: no .om steps for frame "
                f"FH{target_fh:03d}"
            )
        windows.append(
            AggregationWindow(
                target_fh=target_fh,
                source_fhs=members,
                step_hours=target_fh - previous,
            )
        )
        previous = target_fh
    return tuple(windows)


# ---------------------------------------------------------------------------
# ECMWF IFS HRES 9 km
# ---------------------------------------------------------------------------

#: ``.om`` step ladders. 00z/12z run to FH360, 06z/18z stop at FH144. Both
#: start hourly to FH90 then 3-hourly to FH144 (verified live against the run
#: ``meta.json`` ``valid_times`` list, 2026-08-04 18z: 91 hourly + 18 three-
#: hourly = 109 objects).
_ECMWF_LONG_CADENCE = (
    CadenceSegment(step_hours=1, through_fh=90),
    CadenceSegment(step_hours=3, through_fh=144),
    CadenceSegment(step_hours=6, through_fh=360),
)
_ECMWF_SHORT_CADENCE = (
    CadenceSegment(step_hours=1, through_fh=90),
    CadenceSegment(step_hours=3, through_fh=144),
)

#: Production's ECMWF publish ladder: 3 h to FH144, 6 h beyond
#: (``app/models/ecmwf.py:358-360``: ``step_hours=3``,
#: ``step_transition_fh=144``, ``step_hours_after_fh=6``).
_ECMWF_TARGET_CADENCE = (
    CadenceSegment(step_hours=3, through_fh=144),
    CadenceSegment(step_hours=6, through_fh=360),
)

ECMWF_IFS_CATALOG = OmModelCatalog(
    model_id="ecmwf",
    bucket_dir="ecmwf_ifs",
    grid=GridKind.O1280,
    source_points=6_599_680,
    source_cadence={
        0: _ECMWF_LONG_CADENCE,
        6: _ECMWF_SHORT_CADENCE,
        12: _ECMWF_LONG_CADENCE,
        18: _ECMWF_SHORT_CADENCE,
    },
    target_cadence=_ECMWF_TARGET_CADENCE,
    attribution=ATTRIBUTION_ECMWF_IFS,
    source_resolution_label="9 km native",
    variables=(
        OmVariable(
            om_name="temperature_2m",
            cartosky_var="tmp2m",
            conversion=OmConversion.C_TO_F,
            kind=VariableKind.INSTANTANEOUS,
            om_unit="degC",
            production_unit="F",
        ),
        OmVariable(
            om_name="dew_point_2m",
            cartosky_var="dp2m",
            conversion=OmConversion.C_TO_F,
            kind=VariableKind.INSTANTANEOUS,
            om_unit="degC",
            production_unit="F",
        ),
        OmVariable(
            om_name="precipitation",
            cartosky_var="precip_total",
            conversion=OmConversion.MM_TO_IN,
            kind=VariableKind.ACCUMULATION,
            om_unit="mm/step",
            production_unit="in",
            note=(
                "primary=True in production (ecmwf.py:329) — the source must "
                "provide the run-cumulative total, not a per-step value"
            ),
        ),
        OmVariable(
            om_name="snowfall_water_equivalent",
            cartosky_var="snowfall_total",
            conversion=OmConversion.MM_SWE_TO_IN_10TO1,
            kind=VariableKind.ACCUMULATION,
            om_unit="mm SWE/step",
            production_unit="in",
            note=(
                "10:1 ratio lives inside the unit conversion (ecmwf.py:1158); "
                "Kuchera stays delayed-owned"
            ),
        ),
        OmVariable(
            om_name="pressure_msl",
            cartosky_var="msl",
            conversion=OmConversion.NONE,
            kind=VariableKind.INSTANTANEOUS,
            om_unit="Pa",
            production_unit="Pa",
            component_only=True,
            note="Pa in ecmwf_ifs but hPa in ecmwf_ifs025 — per-product audit",
        ),
        OmVariable(
            om_name="wind_gusts_10m",
            cartosky_var="wgst10m",
            conversion=OmConversion.MS_TO_MPH,
            kind=VariableKind.INSTANTANEOUS,
            om_unit="m/s",
            production_unit="mph",
            note="absent from FH000 files (max-over-interval field)",
        ),
        OmVariable(
            om_name="wind_u_component_10m",
            cartosky_var="10u",
            conversion=OmConversion.NONE,
            kind=VariableKind.INSTANTANEOUS,
            om_unit="m/s",
            production_unit="m/s",
            component_only=True,
            note="WP3 wires this into the existing wspd10m derive (ecmwf.py:975)",
        ),
        OmVariable(
            om_name="wind_v_component_10m",
            cartosky_var="10v",
            conversion=OmConversion.NONE,
            kind=VariableKind.INSTANTANEOUS,
            om_unit="m/s",
            production_unit="m/s",
            component_only=True,
            note="WP3 wires this into the existing wspd10m derive (ecmwf.py:975)",
        ),
        OmVariable(
            om_name="showers",
            cartosky_var=None,
            conversion=OmConversion.NONE,
            kind=VariableKind.ACCUMULATION,
            om_unit="mm/step",
            production_unit="",
            enabled=False,
            note="ECMWF has no convective-precip variable in CartoSky",
        ),
        OmVariable(
            om_name="snow_depth",
            cartosky_var=None,
            conversion=OmConversion.NONE,
            kind=VariableKind.INSTANTANEOUS,
            om_unit="",
            production_unit="",
            enabled=False,
            note="not published by ecmwf_ifs, and no CartoSky counterpart",
        ),
    ),
)

#: Catalog registry. Adding a bucket model = adding an entry here.
OM_CATALOGS: Mapping[str, OmModelCatalog] = {
    ECMWF_IFS_CATALOG.model_id: ECMWF_IFS_CATALOG,
}


def catalog_for(model_id: str) -> OmModelCatalog:
    try:
        return OM_CATALOGS[str(model_id).strip().lower()]
    except KeyError:
        raise KeyError(
            f"no Open-Meteo catalog for model {model_id!r} (known: {sorted(OM_CATALOGS)})"
        ) from None
