"""Open-Meteo ``.om`` fetch source (WP1 of the fast-path integration design).

Layout follows ``docs/ECMWF_FAST_PATH_INTEGRATION_DESIGN.md`` §2:

``reader.py``
    The ``omfiles`` access layer. **The only module in the tree that touches
    the GPLv2 ``omfiles`` package**, and it imports it lazily so the rest of
    this family imports cleanly in environments without it installed.
``grids.py``
    Grid decode + samplers: O1280 reduced-Gaussian geometry, bilinear
    samplers for both source grid families, production target-grid builders,
    the process-wide sampler cache, and orientation detection.
``catalog.py``
    Model-agnostic per-model configuration (pure data, no I/O). Adding a
    bucket model means adding a catalog entry, not code.
``source.py``
    The poll / list / fetch API the scheduler and builder call. No scheduler
    coupling lives here — that is WP3.

Nothing in this package writes artifacts or touches the scheduler; WP2/WP3
consume it.
"""

from .accumulation import (
    CHECKPOINT_SCHEMA_VERSION,
    SOURCE_ID,
    AccumulationError,
    AccumulationLedger,
    CheckpointError,
    CheckpointMismatch,
    MissingStepsError,
    OutOfOrderStepError,
    RetentionError,
    cadence_version,
)
from .catalog import (
    ATTRIBUTION_ECMWF_IFS,
    ECMWF_IFS_CATALOG,
    OM_CATALOGS,
    AggregationWindow,
    CadenceSegment,
    GridKind,
    OmModelCatalog,
    OmVariable,
    VariableKind,
    aggregation_boundaries,
    catalog_for,
    expected_fhs,
)

__all__ = [
    "ATTRIBUTION_ECMWF_IFS",
    "CHECKPOINT_SCHEMA_VERSION",
    "SOURCE_ID",
    "AccumulationError",
    "AccumulationLedger",
    "CheckpointError",
    "CheckpointMismatch",
    "MissingStepsError",
    "OutOfOrderStepError",
    "RetentionError",
    "cadence_version",
    "ECMWF_IFS_CATALOG",
    "OM_CATALOGS",
    "AggregationWindow",
    "CadenceSegment",
    "GridKind",
    "OmModelCatalog",
    "OmVariable",
    "VariableKind",
    "aggregation_boundaries",
    "catalog_for",
    "expected_fhs",
]
