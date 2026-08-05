"""Fast-path source integration (WP3 of ``docs/ECMWF_FAST_PATH_INTEGRATION_DESIGN.md``).

Three concerns, three modules:

``ownership``
    Who builds a ``(variable, domain)`` pair — the delayed Herbie/GRIB path or
    the fast Open-Meteo ``.om`` source (design §3). **Import-light and
    flag-gated**: with ``CARTOSKY_FASTPATH_MODELS`` unset nothing here imports
    the ``sources.openmeteo`` family, so a non-fastpath scheduler process never
    pulls in ``omfiles``/``scipy`` and behaves exactly as before.
``state``
    The per-run failover state machine (design §6) — source-generation tokens
    per ``(run, var, domain)``, persisted as JSON so a crash replays.
``subloop``
    The scheduler's fast-source sub-loop (design §4): poll → fetch → regrid →
    accumulate → write through the real production writers. This is the only
    module that imports ``sources.openmeteo``, and it is only ever reached
    from inside a flag-guarded branch.

The scheduler imports ``ownership`` and ``state`` eagerly (both are pure-stdlib
leaves) and ``subloop`` lazily.
"""

from __future__ import annotations

from .ownership import (
    ENV_FASTPATH_MODELS,
    ENV_FASTPATH_VAR_OVERRIDES,
    SOURCE_DELAYED,
    SOURCE_FAST,
    fast_owned_pairs,
    fastpath_enabled,
    fastpath_models,
    is_fast_owned,
    source_for,
)

__all__ = [
    "ENV_FASTPATH_MODELS",
    "ENV_FASTPATH_VAR_OVERRIDES",
    "SOURCE_DELAYED",
    "SOURCE_FAST",
    "fast_owned_pairs",
    "fastpath_enabled",
    "fastpath_models",
    "is_fast_owned",
    "source_for",
]
