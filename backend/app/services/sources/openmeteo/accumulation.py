"""Run-cumulative accumulation provider for the Open-Meteo fast path (WP2).

Why this module exists
======================

``.om`` accumulation fields are **per-step de-accumulated** — each object
carries only that step's increment, and the step *length* changes with the
product's cadence (hourly → FH90, 3 h → FH144, 6 h → FH360;
``docs/ECMWF_OPENMETEO_9KM_PROTOTYPE_2026-08-04.md`` §4 Phase 3). Production,
however, feeds ECMWF ``precip_total`` / ``snowfall_total`` the GRIB's native
**run-cumulative** value directly (``primary=True``, no ``derive=`` —
``app/models/ecmwf.py:329``, ``:451``), so the cumulative-derive machinery is
not on their path.

The fast path therefore owes the builder a *run-cumulative field at any
published fh*, reconstructed by summing ``.om`` steps from run start
(``docs/ECMWF_FAST_PATH_INTEGRATION_DESIGN.md`` §4, "Accumulation provider").
:class:`AccumulationLedger` is that provider.

Values stay in **source units** (mm / mm SWE) throughout. Unit conversion to
production units happens downstream at frame-write time via
:meth:`~.catalog.OmVariable.convert`, exactly as the WP1 catalog documents —
the ledger never converts.

Streaming contract (and why memory is O(2 arrays/var))
======================================================

A full-globe O1280 field is 6 599 680 points; one float64 copy is ~53 MB.
Retaining the step history for a 360 h run (up to 145 steps × 2 vars) is
several gigabytes on a box that already swaps during ECMWF builds. So the
ledger keeps exactly two arrays per variable:

``_cumulative``
    the run-total **at the last completed published boundary**
    (:attr:`~AccumulationLedger.frontier_published_fh`);
``_partial``
    the sum of steps ingested since that boundary.

When a step lands on a published boundary the partial is folded into the
cumulative and zeroed. The consequence is a real API constraint, not an
oversight: **the caller must consume a published fh before ingesting steps
past it.** That is the natural order for sequential dissemination (each new
object completes or advances the current window), and asking for a boundary
the ledger has already advanced past raises :class:`RetentionError` rather
than returning something wrong. Resuming an earlier boundary means replaying
from a checkpoint.

Which source steps make up a published frame is **not** re-derived here: WP1's
:func:`~.catalog.aggregation_boundaries` is the single source of truth (design
Decision 2 — hourly ``.om`` steps are summed into today's 3 h ladder below
FH90; at and beyond FH90 the ladders coincide 1:1).

Float discipline
================

Running sums accumulate in **float64** and are exposed as **float32**,
mirroring :func:`~.grids.apply_sampler`. A 360 h run folds 90 hourly steps
plus 54 coarse ones into one total; adding ~0.05 mm increments into a total of
a few hundred mm in float32 loses ~1e-5 mm per add at the top end and the
error is systematically one-signed, whereas float64 keeps the whole run below
the packer's 0.01 in quantum by a factor of ~1e9. float32 on the way out
matches what the packer and the samplers already handle.

NaN semantics
=============

NaN is *not* zero-filled. A NaN in any ingested step propagates through the
running sum to every later cumulative for those cells, which is the honest
answer: a cell whose history contains an undecodable value has no known
run total. (Per-file integrity checks — design §6 — are expected to reject
corrupt objects *before* they reach the ledger; this is the backstop.)

FH000 carries no accumulation fields at all (prototype §4 gotcha 4). The
ledger starts at zero by construction and rejects an FH000 ingest.

Concurrency
===========

The ledger is **not thread-safe**: ``ingest_step`` mutates shared running-sum
arrays in place. One ledger belongs to one owner — in production, the
scheduler's fast-source sub-loop, which is single-threaded over a run by
design (dissemination is sequential). Do not share a ledger across threads
without external locking; the same applies to ``BlockCachedHTTPFS`` byte
counters (see WP1 verification notes).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

from .catalog import (
    AggregationWindow,
    OmModelCatalog,
    VariableKind,
    aggregation_boundaries,
    expected_fhs,
)

logger = logging.getLogger(__name__)

#: Provenance ``source_id`` for every artifact and checkpoint this path writes
#: (design §8 per-frame provenance).
SOURCE_ID = "openmeteo"

#: Bumped when the on-disk checkpoint *layout* changes. Distinct from the
#: cadence version, which tracks the *ladder* (see :func:`cadence_version`).
CHECKPOINT_SCHEMA_VERSION = 1

__all__ = [
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
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AccumulationError(Exception):
    """Base class for every ledger failure."""


class OutOfOrderStepError(AccumulationError):
    """A step arrived out of order, twice, or with a gap before it.

    Dissemination is sequential, so a gap means a *missing file*: the ledger
    refuses to sum across it and the caller decides whether to wait or fail
    the run (design §4/§6).
    """


class MissingStepsError(AccumulationError):
    """A published fh was requested before all its constituent steps landed."""


class RetentionError(AccumulationError):
    """A published fh was requested after the ledger advanced past it.

    See the module docstring: only the frontier boundary is retained.
    """


class CheckpointError(AccumulationError):
    """A checkpoint could not be read (missing, truncated, not an npz…)."""


class CheckpointMismatch(CheckpointError):
    """A checkpoint exists but belongs to a different lineage.

    Different run, model, source, domain, variable set, schema, or cadence
    ladder — "keyed so a source switch or cadence-ladder change invalidates
    them rather than silently mixing lineages" (design §4).
    """


# ---------------------------------------------------------------------------
# Cadence version
# ---------------------------------------------------------------------------


def cadence_version(catalog: OmModelCatalog, cycle_hour: int) -> str:
    """Stable short hash of everything that decides window membership.

    Covers the model id, the cycle's ``.om`` step ladder, the production
    target ladder, and the accumulating variable set. A checkpoint written
    under a different ladder must not load (design §4: no silently mixed
    lineages), and hashing is preferable to a hand-maintained constant
    because it cannot go stale when a catalog entry is edited.
    """
    payload = {
        "model_id": catalog.model_id,
        "cycle_hour": int(cycle_hour),
        "source_cadence": [
            [int(segment.step_hours), int(segment.through_fh)]
            for segment in catalog.cadence(cycle_hour)
        ],
        "target_cadence": [
            [int(segment.step_hours), int(segment.through_fh)]
            for segment in catalog.target_cadence
        ],
        "accumulation_variables": sorted(
            var.om_name for var in catalog.accumulation_variables()
        ),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    return (
        value.astimezone(timezone.utc)
        if value.tzinfo
        else value.replace(tzinfo=timezone.utc)
    )


def _run_token(run_dt: datetime) -> str:
    return f"{_as_utc(run_dt):%Y%m%dT%H%M}Z"


class AccumulationLedger:
    """Run-cumulative sums for one ``(run, model, domain)``.

    Grid-agnostic: it sums whatever arrays it is handed, as long as their
    shape is consistent. The normal call passes the full-globe 1D O1280 vector
    from :func:`~.source.fetch_frame_fields` so one running sum serves both the
    NA 9 km and global 0.25° targets (design Decision 1) — regridding happens
    after :meth:`cumulative_at`, not before ingest.
    """

    def __init__(
        self,
        catalog: OmModelCatalog,
        run_dt: datetime,
        *,
        domain: str | None = None,
        var_names: Iterable[str] | None = None,
    ) -> None:
        self._catalog = catalog
        self._run_dt = _as_utc(run_dt)
        self._domain = domain
        self._cycle_hour = self._run_dt.hour

        if var_names is None:
            names: tuple[str, ...] = tuple(
                var.om_name for var in catalog.accumulation_variables()
            )
        else:
            names = tuple(dict.fromkeys(str(name) for name in var_names))
            for name in names:
                variable = catalog.variable(name)  # raises on an unknown name
                if variable.kind is not VariableKind.ACCUMULATION:
                    raise ValueError(
                        f"{catalog.model_id}: {name!r} is a "
                        f"{variable.kind.value} variable — the ledger only "
                        f"tracks accumulation variables"
                    )
                if not variable.enabled:
                    raise ValueError(
                        f"{catalog.model_id}: .om variable {name!r} is disabled "
                        f"in the catalog ({variable.note or 'no CartoSky counterpart'})"
                    )
        if not names:
            raise ValueError(
                f"{catalog.model_id}: no accumulation variables to track"
            )
        self._var_names = names

        self._windows: tuple[AggregationWindow, ...] = aggregation_boundaries(
            catalog, self._cycle_hour
        )
        self._window_by_target = {w.target_fh: w for w in self._windows}
        #: Every ``.om`` step this cycle should deliver, FH000 excluded (it has
        #: no accumulation fields).
        self._source_fhs: tuple[int, ...] = tuple(
            fh for fh in expected_fhs(catalog, self._cycle_hour) if fh > 0
        )
        self._source_index = {fh: i for i, fh in enumerate(self._source_fhs)}
        self._cadence_version = cadence_version(catalog, self._cycle_hour)

        self._cumulative: dict[str, np.ndarray] = {}
        self._partial: dict[str, np.ndarray] = {}
        self._shape: tuple[int, ...] | None = None
        self._last_fh = 0
        self._frontier_fh = 0

    # -- identity -----------------------------------------------------------

    @property
    def catalog(self) -> OmModelCatalog:
        return self._catalog

    @property
    def model_id(self) -> str:
        return self._catalog.model_id

    @property
    def run_dt(self) -> datetime:
        return self._run_dt

    @property
    def domain(self) -> str | None:
        return self._domain

    @property
    def cycle_hour(self) -> int:
        return self._cycle_hour

    @property
    def cadence_version(self) -> str:
        """Ladder identity — see :func:`cadence_version`."""
        return self._cadence_version

    @property
    def var_names(self) -> tuple[str, ...]:
        return self._var_names

    # -- progress -----------------------------------------------------------

    @property
    def last_ingested_fh(self) -> int:
        """Highest ``.om`` step folded in; ``0`` means nothing yet."""
        return self._last_fh

    @property
    def frontier_published_fh(self) -> int:
        """Newest published fh whose run-total is materialised; ``0`` if none."""
        return self._frontier_fh

    @property
    def published_fhs(self) -> tuple[int, ...]:
        """Every published fh this cycle will produce (from WP1's ladder)."""
        return tuple(window.target_fh for window in self._windows)

    @property
    def expected_source_fhs(self) -> tuple[int, ...]:
        return self._source_fhs

    def window_for(self, published_fh: int) -> AggregationWindow:
        """The WP1 window backing ``published_fh``."""
        try:
            return self._window_by_target[int(published_fh)]
        except KeyError:
            raise ValueError(
                f"{self.model_id} {self._cycle_hour:02d}z: FH{int(published_fh):03d} "
                f"is not a published frame hour "
                f"(ladder ends at FH{self.published_fhs[-1]:03d})"
            ) from None

    def next_expected_fh(self) -> int | None:
        """The only ``.om`` step :meth:`ingest_step` will accept next."""
        if self._last_fh == 0:
            return self._source_fhs[0] if self._source_fhs else None
        index = self._source_index[self._last_fh] + 1
        if index >= len(self._source_fhs):
            return None
        return self._source_fhs[index]

    def ready_published_fhs(self) -> list[int]:
        """Published fhs whose run-total can be produced **right now**.

        At most one element — the frontier (see the module docstring on
        retention). Empty until the first published window completes.
        """
        return [self._frontier_fh] if self._frontier_fh else []

    # -- ingest -------------------------------------------------------------

    def ingest_step(self, fh: int, fields: Mapping[str, np.ndarray]) -> None:
        """Add one ``.om`` step's per-step fields to the running sums.

        ``fields`` must carry every tracked variable, in source units, with a
        shape consistent across the run. Steps must arrive in ladder order
        with no gaps.
        """
        fh = int(fh)
        if fh == 0:
            raise ValueError(
                "FH000 carries no accumulation fields (prototype §4 gotcha 4); "
                "the ledger starts at zero by construction and must not be fed it"
            )
        if fh not in self._source_index:
            raise ValueError(
                f"{self.model_id} {self._cycle_hour:02d}z: FH{fh:03d} is not an "
                f"expected .om step for this cycle "
                f"(horizon FH{self._source_fhs[-1]:03d})"
            )
        expected = self.next_expected_fh()
        if fh <= self._last_fh:
            raise OutOfOrderStepError(
                f"{self.model_id} {self._cycle_hour:02d}z: FH{fh:03d} arrived after "
                f"FH{self._last_fh:03d} — steps must be ingested in ascending "
                f"order (next expected: "
                f"{'none, run complete' if expected is None else f'FH{expected:03d}'})"
            )
        if expected is not None and fh != expected:
            gap = [
                step
                for step in self._source_fhs
                if self._last_fh < step < fh
            ]
            raise OutOfOrderStepError(
                f"{self.model_id} {self._cycle_hour:02d}z: FH{fh:03d} skips "
                f"{len(gap)} step(s) {gap} — a gap means a missing .om object; "
                f"wait for it or fail the run rather than summing across it"
            )

        missing = [name for name in self._var_names if name not in fields]
        if missing:
            raise ValueError(
                f"{self.model_id} FH{fh:03d}: ingest is missing accumulation "
                f"variable(s) {missing}"
            )
        extra = [name for name in fields if name not in self._var_names]
        if extra:
            raise ValueError(
                f"{self.model_id} FH{fh:03d}: ingest carries untracked "
                f"variable(s) {sorted(extra)} (tracked: {list(self._var_names)})"
            )

        prepared: dict[str, np.ndarray] = {}
        # Validate every variable's shape BEFORE latching self._shape, so a
        # malformed first ingest (vars inconsistent among themselves) leaves
        # the ledger fully untouched rather than pinned to a shape it never
        # accumulated.
        candidate_shape = self._shape
        for name in self._var_names:
            values = np.asarray(fields[name])
            if candidate_shape is None:
                candidate_shape = values.shape
            elif values.shape != candidate_shape:
                raise ValueError(
                    f"{self.model_id} FH{fh:03d}: {name} has shape "
                    f"{values.shape}, ledger established {candidate_shape}"
                )
            prepared[name] = values
        self._shape = candidate_shape

        if not self._cumulative:
            assert self._shape is not None
            for name in self._var_names:
                self._cumulative[name] = np.zeros(self._shape, dtype=np.float64)
                self._partial[name] = np.zeros(self._shape, dtype=np.float64)

        # float64 accumulation: NaN propagates by construction (no nansum).
        for name in self._var_names:
            self._partial[name] += prepared[name].astype(np.float64, copy=False)

        self._last_fh = fh

        window = self._window_by_target.get(fh)
        if window is not None:
            for name in self._var_names:
                self._cumulative[name] += self._partial[name]
                self._partial[name][...] = 0.0
            self._frontier_fh = fh

    # -- read ---------------------------------------------------------------

    def cumulative_at(self, published_fh: int) -> dict[str, np.ndarray]:
        """The run-cumulative field at ``published_fh``, in source units.

        Returns fresh float32 arrays (the caller owns them; the ledger's own
        float64 state is never handed out).
        """
        published_fh = int(published_fh)
        window = self.window_for(published_fh)  # raises for non-ladder hours

        if published_fh == self._frontier_fh:
            return {
                name: self._cumulative[name].astype(np.float32)
                for name in self._var_names
            }
        if published_fh < self._frontier_fh:
            raise RetentionError(
                f"{self.model_id} {self._cycle_hour:02d}z: FH{published_fh:03d} is "
                f"behind the ledger frontier FH{self._frontier_fh:03d}. The ledger "
                f"retains only the frontier run-total (one array per variable); "
                f"consume each published frame before ingesting past it, or "
                f"replay from a checkpoint"
            )

        needed = [
            fh
            for candidate in self._windows
            if candidate.target_fh <= published_fh
            for fh in candidate.source_fhs
        ]
        absent = [fh for fh in needed if fh > self._last_fh]
        raise MissingStepsError(
            f"{self.model_id} {self._cycle_hour:02d}z: FH{published_fh:03d} needs "
            f"{len(window.source_fhs)} step(s) {list(window.source_fhs)} and the "
            f"ledger has only reached FH{self._last_fh:03d} — missing .om steps "
            f"{absent}"
        )

    # -- checkpointing ------------------------------------------------------

    def checkpoint_name(self) -> str:
        """Filename encoding the checkpoint key ``(run, source, domain)``."""
        domain = (self._domain or "all").replace("/", "_")
        return (
            f"{SOURCE_ID}-{self.model_id}-{_run_token(self._run_dt)}-{domain}"
            f".accum.npz"
        )

    def checkpoint_path(self, directory: str | os.PathLike[str]) -> Path:
        return Path(directory) / self.checkpoint_name()

    def _meta(self) -> dict[str, object]:
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "source_id": SOURCE_ID,
            "model_id": self.model_id,
            "run": _run_token(self._run_dt),
            "cycle_hour": self._cycle_hour,
            "domain": self._domain,
            "cadence_version": self._cadence_version,
            "var_names": list(self._var_names),
            "last_ingested_fh": self._last_fh,
            "frontier_published_fh": self._frontier_fh,
        }

    def save_checkpoint(self, directory: str | os.PathLike[str]) -> Path:
        """Persist the running sums atomically (tmp + ``os.replace``).

        A scheduler restart resumes from here instead of refetching the whole
        step history (design §4). Arrays are stored at full float64 precision
        so a resumed ledger is bit-identical to a cold one.
        """
        target = self.checkpoint_path(directory)
        target.parent.mkdir(parents=True, exist_ok=True)
        arrays: dict[str, np.ndarray] = {
            "__meta__": np.array(
                json.dumps(self._meta(), sort_keys=True, separators=(",", ":"))
            )
        }
        for name in self._var_names:
            if name in self._cumulative:
                arrays[f"cum::{name}"] = self._cumulative[name]
                arrays[f"part::{name}"] = self._partial[name]
        tmp_path = target.with_name(
            f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with open(tmp_path, "wb") as handle:
                np.savez_compressed(handle, **arrays)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        logger.info(
            "openmeteo: checkpointed %s through FH%03d (frontier FH%03d)",
            target.name,
            self._last_fh,
            self._frontier_fh,
        )
        return target

    @classmethod
    def load_checkpoint(
        cls,
        directory: str | os.PathLike[str],
        catalog: OmModelCatalog,
        run_dt: datetime,
        *,
        domain: str | None = None,
        var_names: Iterable[str] | None = None,
    ) -> "AccumulationLedger":
        """Restore a ledger, refusing anything from a different lineage.

        Raises :class:`CheckpointMismatch` when the stored run / model /
        source / domain / variable set / schema / cadence version disagrees
        with the ledger being asked for, and :class:`CheckpointError` when the
        file is absent or unreadable.
        """
        ledger = cls(catalog, run_dt, domain=domain, var_names=var_names)
        path = ledger.checkpoint_path(directory)
        if not path.exists():
            raise CheckpointError(f"no accumulation checkpoint at {path}")
        try:
            with np.load(path, allow_pickle=False) as payload:
                raw_meta = payload["__meta__"]
                meta = json.loads(str(raw_meta))
                stored = {
                    key: np.asarray(payload[key])
                    for key in payload.files
                    if key != "__meta__"
                }
        except CheckpointError:
            raise
        except Exception as exc:  # truncated file, not an npz, bad json…
            raise CheckpointError(
                f"unreadable accumulation checkpoint {path}: {exc}"
            ) from exc

        if not isinstance(meta, dict):
            raise CheckpointError(f"malformed accumulation checkpoint {path}")

        expected = ledger._meta()
        for key in (
            "schema_version",
            "source_id",
            "model_id",
            "run",
            "domain",
            "cadence_version",
        ):
            if meta.get(key) != expected[key]:
                raise CheckpointMismatch(
                    f"accumulation checkpoint {path} has {key}={meta.get(key)!r}, "
                    f"expected {expected[key]!r} — refusing to mix lineages"
                )
        if list(meta.get("var_names") or ()) != list(ledger._var_names):
            raise CheckpointMismatch(
                f"accumulation checkpoint {path} tracks "
                f"{list(meta.get('var_names') or ())}, expected "
                f"{list(ledger._var_names)} — refusing to mix lineages"
            )

        last_fh = int(meta.get("last_ingested_fh") or 0)
        frontier_fh = int(meta.get("frontier_published_fh") or 0)
        if last_fh and last_fh not in ledger._source_index:
            raise CheckpointMismatch(
                f"accumulation checkpoint {path} stopped at FH{last_fh:03d}, which "
                f"is not a step of the {ledger._cycle_hour:02d}z ladder"
            )
        if frontier_fh and frontier_fh not in ledger._window_by_target:
            raise CheckpointMismatch(
                f"accumulation checkpoint {path} claims frontier FH{frontier_fh:03d}, "
                f"which is not a published frame hour"
            )

        if stored:
            shape: tuple[int, ...] | None = None
            for name in ledger._var_names:
                try:
                    cumulative = stored[f"cum::{name}"]
                    partial = stored[f"part::{name}"]
                except KeyError as exc:
                    raise CheckpointError(
                        f"accumulation checkpoint {path} is missing array {exc}"
                    ) from None
                if shape is None:
                    shape = cumulative.shape
                if cumulative.shape != shape or partial.shape != shape:
                    raise CheckpointError(
                        f"accumulation checkpoint {path}: inconsistent array shapes "
                        f"for {name}"
                    )
                ledger._cumulative[name] = cumulative.astype(np.float64, copy=True)
                ledger._partial[name] = partial.astype(np.float64, copy=True)
            ledger._shape = shape
        elif last_fh:
            raise CheckpointError(
                f"accumulation checkpoint {path} claims FH{last_fh:03d} but carries "
                f"no arrays"
            )

        ledger._last_fh = last_fh
        ledger._frontier_fh = frontier_fh
        return ledger

    # -- introspection ------------------------------------------------------

    def state_arrays(self) -> Sequence[np.ndarray]:
        """Every array the ledger holds — used by the memory-shape test."""
        return [*self._cumulative.values(), *self._partial.values()]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AccumulationLedger {self.model_id} {_run_token(self._run_dt)} "
            f"domain={self._domain!r} vars={list(self._var_names)} "
            f"last_fh={self._last_fh} frontier={self._frontier_fh}>"
        )
