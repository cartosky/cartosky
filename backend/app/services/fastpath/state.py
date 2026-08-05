"""Per-run failover state machine for the fast path (design §6).

Both sources live in one scheduler process (design §4), so failover is an
**in-process state machine, not a cross-process race**. Each
``(run, var, domain)`` carries a *source generation* token:

* the fast sub-loop stamps its current generation into every frame it writes
  (see the ``generation`` provenance key, design §8);
* the failover transition — taken inside the same publish-lock critical
  section the catch-up loop already uses — bumps the generation and flips the
  pair's owner to ``delayed`` **before** any delayed build of that pair starts;
* a fast sub-loop that resumes after revocation observes the bumped generation
  and stops for that pair.

Crash-restart replays ownership from this file, and the per-frame provenance
mirrors it so a promoted run stays auditable without it.

On-disk location and shape
==========================

``{data_root}/staging/{model}/_fastpath/{run_id}.state.json``

Deliberately a *sibling* of the run directories rather than a file inside one:
``_fastpath`` cannot match ``RUN_ID_RE`` (the same trick that makes the
``domains`` namespace invisible to run scanners), so run retention, the
promotion copy and every existing staging scanner skip it unchanged — no
fast-path bookkeeping ever leaks into a published run tree. The accumulation
checkpoints (WP2) live in the same directory for the same reason.

::

    {
      "schema_version": 1,
      "source_id": "openmeteo",
      "model": "ecmwf",
      "run": "20260804_12z",
      "created_at": "2026-08-04T17:20:03Z",
      "updated_at": "2026-08-04T18:41:55Z",
      "stall_count": 0,
      "canary": {                  # WP4: written once per run, or {} until then
        "status": "ok",            # ok | failed | skipped
        "completed_at": "2026-08-04T19:02:11Z",
        "results": [...]           # see canary.CanaryResult.to_json
      },
      "pairs": {
        "tmp2m|na": {
          "owner": "fast",           # fast | delayed  (delayed == revoked)
          "generation": 1,           # bumped on every revocation
          "ready_through_fh": 12,    # contiguous frontier of fast-written frames
          "published_fhs": [0, 3, 6, 9, 12],
          "revoked_at": null,
          "revoke_reason": null,
          "delayed_rebuild_required": false
        }
      }
    }

``generation`` starts at 1 for a pair the fast source claims. A revoked pair
keeps its generation so a resumed loop can compare; re-claiming is not a thing
the launch design does (rollback is a config flip, which restarts the run's
bookkeeping from a fresh generation only on a fresh run).

Concurrency: one writer (the scheduler process). Writes are atomic
(tmp + ``os.replace``) so a crash mid-write cannot truncate the file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

logger = logging.getLogger(__name__)

#: Bumped when this file's on-disk layout changes.
STATE_SCHEMA_VERSION = 1

#: Directory name under ``staging/{model}/`` (never matches ``RUN_ID_RE``).
FASTPATH_NAMESPACE = "_fastpath"

OWNER_FAST = "fast"
OWNER_DELAYED = "delayed"

#: ``20260804_12z`` inside a filename (scheduler run id spelling).
_RUN_ID_IN_NAME_RE = re.compile(r"(\d{8})_(\d{1,2})z", re.IGNORECASE)
#: ``20260804T1200Z`` inside a filename (WP2 ledger run-token spelling).
_RUN_TOKEN_IN_NAME_RE = re.compile(r"\d{8}T\d{4}Z")

__all__ = [
    "FASTPATH_NAMESPACE",
    "OWNER_DELAYED",
    "OWNER_FAST",
    "STATE_SCHEMA_VERSION",
    "FastpathRunState",
    "PairState",
    "fastpath_namespace_dir",
    "fastpath_state_path",
    "iter_run_states",
    "prune_orphan_state",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _token(value: Any) -> str:
    return str(value or "").strip().lower()


def pair_key(var_id: Any, domain: Any) -> str:
    return f"{_token(var_id)}|{_token(domain)}"


def fastpath_namespace_dir(data_root: Path | str, model: str) -> Path:
    """``staging/{model}/_fastpath`` — state files AND WP2 checkpoints."""
    return Path(data_root) / "staging" / _token(model) / FASTPATH_NAMESPACE


def fastpath_state_path(data_root: Path | str, model: str, run_id: str) -> Path:
    return fastpath_namespace_dir(data_root, model) / f"{run_id}.state.json"


@dataclass
class PairState:
    """Failover state for one ``(var, domain)`` pair within a run."""

    owner: str = OWNER_FAST
    generation: int = 1
    ready_through_fh: int | None = None
    #: Frames written to STAGING. A frame is staged before it is promoted, so
    #: ``staged_fhs`` is always a superset of :attr:`published_fhs`; the
    #: difference is the promotion retry queue.
    staged_fhs: list[int] = field(default_factory=list)
    #: Frames that reached the PUBLISHED tree. Only this list counts as
    #: progress: readiness, the manifest frontier and the failover deadline all
    #: read it, so a swallowed promotion failure can never look like success.
    published_fhs: list[int] = field(default_factory=list)
    revoked_at: str | None = None
    revoke_reason: str | None = None
    #: Set when revocation requires the delayed path to rebuild the WHOLE
    #: series rather than fill the tail (accumulation variables — see
    #: :meth:`FastpathRunState.revoke`).
    delayed_rebuild_required: bool = False
    #: Revoked, but the delayed path must NOT pick it up: the fast frames could
    #: not be verifiably cleared, so a delayed rebuild would produce a
    #: mixed-source accumulation series. Needs operator action.
    blocked: bool = False

    @property
    def revoked(self) -> bool:
        return self.owner != OWNER_FAST

    @property
    def reclaimable(self) -> bool:
        """Revoked AND safe for the delayed path to build."""
        return self.revoked and not self.blocked

    @property
    def pending_promotion_fhs(self) -> list[int]:
        """Staged frames that never reached the published tree."""
        return sorted(set(self.staged_fhs) - set(self.published_fhs))

    def to_json(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "generation": int(self.generation),
            "ready_through_fh": self.ready_through_fh,
            "staged_fhs": sorted({int(fh) for fh in self.staged_fhs}),
            "published_fhs": sorted({int(fh) for fh in self.published_fhs}),
            "revoked_at": self.revoked_at,
            "revoke_reason": self.revoke_reason,
            "delayed_rebuild_required": bool(self.delayed_rebuild_required),
            "blocked": bool(self.blocked),
        }

    @classmethod
    def from_json(cls, payload: Any) -> "PairState":
        if not isinstance(payload, dict):
            return cls()

        def _fh_list(key: str) -> list[int]:
            raw = payload.get(key)
            out: list[int] = []
            if isinstance(raw, list):
                for item in raw:
                    try:
                        out.append(int(item))
                    except (TypeError, ValueError):
                        continue
            return sorted(set(out))

        fhs = _fh_list("published_fhs")
        # A state file written before staged/published were split records only
        # published_fhs; those frames did promote, so staged == published.
        staged = _fh_list("staged_fhs") or list(fhs)
        ready = payload.get("ready_through_fh")
        try:
            ready_through_fh = None if ready is None else int(ready)
        except (TypeError, ValueError):
            ready_through_fh = None
        owner = _token(payload.get("owner")) or OWNER_FAST
        try:
            generation = max(1, int(payload.get("generation") or 1))
        except (TypeError, ValueError):
            generation = 1
        return cls(
            owner=owner if owner in (OWNER_FAST, OWNER_DELAYED) else OWNER_FAST,
            generation=generation,
            ready_through_fh=ready_through_fh,
            staged_fhs=sorted(set(staged) | set(fhs)),
            published_fhs=fhs,
            revoked_at=payload.get("revoked_at") or None,
            revoke_reason=payload.get("revoke_reason") or None,
            delayed_rebuild_required=bool(payload.get("delayed_rebuild_required")),
            blocked=bool(payload.get("blocked")),
        )


class FastpathRunState:
    """Crash-replayable ownership/generation state for one run."""

    def __init__(
        self,
        *,
        data_root: Path | str,
        model: str,
        run_id: str,
        source_id: str = "openmeteo",
    ) -> None:
        self.data_root = Path(data_root)
        self.model = _token(model)
        self.run_id = str(run_id)
        self.source_id = str(source_id)
        self.path = fastpath_state_path(self.data_root, self.model, self.run_id)
        self.created_at = _utc_now()
        self.updated_at = self.created_at
        self.stall_count = 0
        self.pairs: dict[str, PairState] = {}
        #: WP4 canary record for this run — written at most once (design §6:
        #: "once per run, after both sources are complete"). Empty until the
        #: canary has run; its presence is the once-per-run latch, which is why
        #: it lives here rather than in a separate marker file.
        self.canary: dict[str, Any] = {}

    # -- persistence --------------------------------------------------------

    @classmethod
    def load_or_create(
        cls,
        *,
        data_root: Path | str,
        model: str,
        run_id: str,
        source_id: str = "openmeteo",
    ) -> "FastpathRunState":
        """Read the state file if present; a corrupt file restarts from empty.

        A corrupt state file must not wedge the scheduler: the per-frame
        provenance (design §8) is the durable record, and starting from empty
        only means the fast loop re-derives its frontier from disk.
        """
        state = cls(data_root=data_root, model=model, run_id=run_id, source_id=source_id)
        if not state.path.exists():
            return state
        try:
            payload = json.loads(state.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "fastpath: unreadable state file %s (%s) — starting fresh", state.path, exc
            )
            return state
        if not isinstance(payload, dict):
            return state
        if int(payload.get("schema_version") or 0) != STATE_SCHEMA_VERSION:
            logger.warning(
                "fastpath: state file %s has schema_version=%r, expected %d — starting fresh",
                state.path,
                payload.get("schema_version"),
                STATE_SCHEMA_VERSION,
            )
            return state
        state.created_at = str(payload.get("created_at") or state.created_at)
        state.updated_at = str(payload.get("updated_at") or state.updated_at)
        try:
            state.stall_count = int(payload.get("stall_count") or 0)
        except (TypeError, ValueError):
            state.stall_count = 0
        raw_canary = payload.get("canary")
        if isinstance(raw_canary, dict):
            state.canary = dict(raw_canary)
        raw_pairs = payload.get("pairs")
        if isinstance(raw_pairs, dict):
            for key, entry in raw_pairs.items():
                state.pairs[str(key)] = PairState.from_json(entry)
        return state

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "source_id": self.source_id,
            "model": self.model,
            "run": self.run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stall_count": int(self.stall_count),
            "canary": dict(self.canary),
            "pairs": {key: value.to_json() for key, value in sorted(self.pairs.items())},
        }

    def _merge_disk_revocations(self) -> None:
        """Adopt revocations written since this object was loaded.

        Revocation is monotonic and is the only transition another writer can
        make (the failover deadline takes it inside the publish lock). Without
        this merge, a sub-loop that loaded the state before a revoke would
        clobber that revoke on its next frame write — the exact
        resume-during-failover interleaving design §6 asks to be tested.
        Frame bookkeeping (``published_fhs``) is unioned, so neither writer
        loses progress.
        """
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != (
            STATE_SCHEMA_VERSION
        ):
            return
        try:
            self.stall_count = max(int(self.stall_count), int(payload.get("stall_count") or 0))
        except (TypeError, ValueError):
            pass
        # The canary record is write-once per run, so "somebody already wrote
        # one" always wins over an in-memory blank — otherwise a sub-loop that
        # loaded the state before the canary ran would clear the latch and the
        # canary would repeat (and re-fetch) on the next pass.
        disk_canary = payload.get("canary")
        if isinstance(disk_canary, dict) and disk_canary and not self.canary:
            self.canary = dict(disk_canary)
        raw_pairs = payload.get("pairs")
        if not isinstance(raw_pairs, dict):
            return
        for key, raw_entry in raw_pairs.items():
            disk_entry = PairState.from_json(raw_entry)
            memory_entry = self.pairs.get(str(key))
            if memory_entry is None:
                self.pairs[str(key)] = disk_entry
                continue
            memory_entry.published_fhs = sorted(
                set(memory_entry.published_fhs) | set(disk_entry.published_fhs)
            )
            memory_entry.staged_fhs = sorted(
                set(memory_entry.staged_fhs)
                | set(disk_entry.staged_fhs)
                | set(memory_entry.published_fhs)
            )
            if disk_entry.generation > memory_entry.generation or (
                disk_entry.revoked and not memory_entry.revoked
            ):
                memory_entry.owner = disk_entry.owner
                memory_entry.generation = max(
                    memory_entry.generation, disk_entry.generation
                )
                memory_entry.revoked_at = disk_entry.revoked_at
                memory_entry.revoke_reason = disk_entry.revoke_reason
                memory_entry.delayed_rebuild_required = disk_entry.delayed_rebuild_required
                memory_entry.blocked = disk_entry.blocked
            elif disk_entry.blocked:
                memory_entry.blocked = True

    def save(self) -> Path:
        self._merge_disk_revocations()
        self.updated_at = _utc_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            tmp_path.write_text(json.dumps(self.to_json(), indent=2, sort_keys=True))
            os.replace(tmp_path, self.path)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        return self.path

    # -- pair access --------------------------------------------------------

    def pair(self, var_id: str, domain: str, *, create: bool = True) -> PairState:
        key = pair_key(var_id, domain)
        existing = self.pairs.get(key)
        if existing is not None:
            return existing
        fresh = PairState()
        if create:
            self.pairs[key] = fresh
        return fresh

    def generation(self, var_id: str, domain: str) -> int:
        return self.pair(var_id, domain, create=False).generation

    def is_revoked(self, var_id: str, domain: str) -> bool:
        return self.pair(var_id, domain, create=False).revoked

    def revoked_pairs(self) -> frozenset[tuple[str, str]]:
        """Every revoked pair — the fast loop stops for all of these."""
        out: set[tuple[str, str]] = set()
        for key, entry in self.pairs.items():
            if not entry.revoked:
                continue
            var_id, _, domain = key.partition("|")
            out.add((var_id, domain))
        return frozenset(out)

    def reclaimable_pairs(self) -> frozenset[tuple[str, str]]:
        """Revoked pairs the DELAYED path may build.

        Excludes blocked pairs: their fast frames could not be verifiably
        cleared, so a delayed rebuild would mix sources inside one accumulation
        series. Nobody builds a blocked pair until an operator intervenes —
        deliberately preferring a visible gap to silent corruption.
        """
        out: set[tuple[str, str]] = set()
        for key, entry in self.pairs.items():
            if not entry.reclaimable:
                continue
            var_id, _, domain = key.partition("|")
            out.add((var_id, domain))
        return frozenset(out)

    def blocked_pairs(self) -> frozenset[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for key, entry in self.pairs.items():
            if not entry.blocked:
                continue
            var_id, _, domain = key.partition("|")
            out.add((var_id, domain))
        return frozenset(out)

    def rebuild_required_pairs(self) -> frozenset[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for key, entry in self.pairs.items():
            if not (entry.revoked and entry.delayed_rebuild_required):
                continue
            var_id, _, domain = key.partition("|")
            out.add((var_id, domain))
        return frozenset(out)

    # -- transitions --------------------------------------------------------

    def record_staged(self, var_id: str, domain: str, fh: int) -> PairState:
        """Note a frame written to STAGING (not yet promoted)."""
        entry = self.pair(var_id, domain)
        entry.staged_fhs = sorted(set(entry.staged_fhs) | {int(fh)})
        return entry

    def record_published(
        self,
        var_id: str,
        domain: str,
        fh: int,
        *,
        expected_fhs: Iterable[int] | None = None,
    ) -> PairState:
        """Note a frame that reached the PUBLISHED tree, advancing the frontier.

        Called only after a promote succeeds — a promotion that raised must
        leave the pair looking incomplete, so the next pass retries it instead
        of treating the timestep as done.

        ``ready_through_fh`` is the contiguity edge over ``expected_fhs`` (the
        same rule ``_write_run_manifest`` uses), not ``max(published)``.
        """
        entry = self.pair(var_id, domain)
        entry.staged_fhs = sorted(set(entry.staged_fhs) | {int(fh)})
        published = set(entry.published_fhs)
        published.add(int(fh))
        entry.published_fhs = sorted(published)
        if expected_fhs is None:
            entry.ready_through_fh = max(published)
        else:
            frontier: int | None = None
            for candidate in sorted({int(item) for item in expected_fhs}):
                if candidate not in published:
                    break
                frontier = candidate
            entry.ready_through_fh = frontier
        return entry

    #: Back-compat alias used by tests written before the staged/published
    #: split; records a frame that both staged AND promoted.
    record_frame = record_published

    def pending_promotions(self) -> dict[int, set[str]]:
        """``fh -> domains`` for every staged frame that never got promoted.

        The retry queue. A pass drains this before ingesting new steps, so a
        transient promote failure costs one poll rather than silently dropping
        the timestep from the published tree forever.
        """
        pending: dict[int, set[str]] = {}
        for key, entry in self.pairs.items():
            if entry.revoked:
                continue
            _var_id, _, domain = key.partition("|")
            for fh in entry.pending_promotion_fhs:
                pending.setdefault(fh, set()).add(domain)
        return pending

    def revoke(
        self,
        var_id: str,
        domain: str,
        *,
        reason: str,
        rebuild_required: bool = False,
    ) -> PairState:
        """Revoke fast ownership: bump the generation and hand the pair back.

        Callers MUST hold the scheduler publish lock (design §6) so no delayed
        build of the pair can start before the generation moves.
        """
        entry = self.pair(var_id, domain)
        if entry.revoked:
            return entry
        entry.owner = OWNER_DELAYED
        entry.generation = int(entry.generation) + 1
        entry.revoked_at = _utc_now()
        entry.revoke_reason = str(reason)
        entry.delayed_rebuild_required = bool(rebuild_required)
        logger.warning(
            "fastpath: revoked %s/%s %s|%s generation=%d reason=%s rebuild_required=%s",
            self.model,
            self.run_id,
            _token(var_id),
            _token(domain),
            entry.generation,
            reason,
            entry.delayed_rebuild_required,
        )
        return entry

    def note_stall(self, count: int = 1) -> int:
        self.stall_count = int(self.stall_count) + int(count)
        return self.stall_count

    # -- canary latch (WP4) -------------------------------------------------

    @property
    def canary_done(self) -> bool:
        """Whether the once-per-run canary has already been recorded.

        A *skipped* canary counts as done: the skip reasons are all terminal
        for the run (nothing fast-owned survived, the reference frame is gone),
        so retrying every poll would only re-do the work that produced the skip.
        """
        return bool(self.canary)

    def mark_canary(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Record the canary result. First writer wins (see :attr:`canary_done`)."""
        self.canary = dict(payload)
        return self.canary


def iter_run_states(data_root: Path | str, model: str) -> Iterator["FastpathRunState"]:
    """Every retained run's state file for one model, newest run id first.

    The ops sweep (WP4 metrics) needs this because the alert-worthy conditions
    are *not* all on the run the current pass is working: a blocked pair is
    recorded against the run that failed over, which by then is one or two
    cycles behind the run the bucket is publishing. Retention keeps the
    namespace to a handful of files, so scanning it per pass is free.
    """
    directory = fastpath_namespace_dir(data_root, model)
    if not directory.is_dir():
        return
    for path in sorted(_iter_namespace_files(directory), reverse=True):
        if not path.name.endswith(".state.json"):
            continue
        run_id = path.name[: -len(".state.json")]
        try:
            yield FastpathRunState.load_or_create(
                data_root=data_root, model=model, run_id=run_id
            )
        except Exception:  # a single unreadable file must not kill the sweep
            logger.warning("fastpath: could not read state file %s", path)


def prune_orphan_state(
    data_root: Path | str,
    model: str,
    *,
    older_than: datetime | None,
) -> int:
    """Delete state files and checkpoints for runs older than ``older_than``.

    The ``_fastpath`` namespace is invisible to run retention by design, so it
    needs its own sweep — otherwise a box accumulates one npz per run forever.

    **Age, not presence.** An earlier version kept a file only while a run
    *directory* existed for it, which was wrong in exactly the case the state
    file matters most: a total fast-path outage. No fast frames are written, so
    no run directory exists, so the state the failover deadline had just
    written was deleted a statement later — losing the revocations, the
    generation bump, ``delayed_rebuild_required`` and the stall counters, and
    letting a resumed fast loop re-claim at a stale generation. The rule is now
    "prune only what retention has already aged out"; when the cutoff cannot be
    determined, ``older_than`` is ``None`` and nothing is pruned. Orphan state
    files are a few KB; premature deletion is a correctness bug.

    Files whose run id does not parse are always kept.
    """
    directory = fastpath_namespace_dir(data_root, model)
    if not directory.is_dir() or older_than is None:
        return 0
    cutoff = _as_utc_dt(older_than)
    removed = 0
    for path in _iter_namespace_files(directory):
        run_dt = _run_dt_from_namespace_filename(path.name)
        if run_dt is None or run_dt >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            logger.warning("fastpath: could not prune %s", path)
    # Quarantined frame directories (failed-purge evidence, _purge_fast_frames)
    # are directories, not files, so the loop above never sees them. Age them
    # out on the same cutoff or the quarantine grows without bound.
    quarantine = directory / "quarantine"
    if quarantine.is_dir():
        for entry in quarantine.iterdir():
            if not entry.is_dir():
                continue
            run_dt = _run_dt_from_namespace_filename(entry.name)
            if run_dt is None or run_dt >= cutoff:
                continue
            try:
                shutil.rmtree(entry)
                removed += 1
            except OSError:
                logger.warning("fastpath: could not prune quarantined %s", entry)
    return removed


def _as_utc_dt(value: datetime) -> datetime:
    return (
        value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    )


def _run_dt_from_namespace_filename(name: str) -> datetime | None:
    """Parse the run instant out of either filename convention.

    ``{run_id}.state.json`` uses the scheduler run id (``20260804_12z``);
    ``openmeteo-{model}-{token}-{domain}.accum.npz`` uses the WP2 ledger's own
    run token (``20260804T1200Z``). Anything unrecognised returns ``None`` and
    is therefore kept.
    """
    for match in _RUN_ID_IN_NAME_RE.finditer(name):
        date_part, cycle_part = match.group(1), match.group(2)
        try:
            return datetime.strptime(f"{date_part}{int(cycle_part):02d}", "%Y%m%d%H").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
    for match in _RUN_TOKEN_IN_NAME_RE.finditer(name):
        try:
            return datetime.strptime(match.group(0), "%Y%m%dT%H%MZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
    return None


def _iter_namespace_files(directory: Path) -> Iterator[Path]:
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return
    for path in entries:
        if path.is_file() and not path.name.startswith("."):
            yield path
