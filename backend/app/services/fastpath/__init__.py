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

``canary``
    The once-per-run cross-source comparison (design §6) — Phase 4's reconcile
    metrics, recomputed automatically against the 0.25° reference product, plus
    the failover-seam audit (design §8).
``metrics``
    The ops-metrics handoff (design §8): a JSON snapshot under
    ``{data_root}/status/fastpath/`` that the API turns into Prometheus gauges,
    mirroring how the scheduler already publishes its Herbie runtime metrics.

The scheduler imports ``ownership`` and ``state`` eagerly (both are pure-stdlib
leaves) and ``subloop``, ``canary`` and ``metrics`` lazily, from inside the
flag-guarded branch — so nothing below ``ownership`` is imported at all in a
process whose fast path is off. That is why none of them are re-exported here.
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
