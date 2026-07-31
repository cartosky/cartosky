"""Parallel per-frame thermo execution (Skew-T design 2026-07-30, Phase 5).

The pool is a pure performance optimisation, so the contract under test is that
it is *invisible*: same results as the serial path, same per-frame containment,
and a transparent fall back to serial wherever subprocesses are unavailable.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import Future
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import sounding_indices  # noqa: E402
from app.services import sounding_thermo  # noqa: E402

# Checked-in copy of the 2026-07-30 spike's OKC reference profile, so the one
# test that spawns real pool workers runs in every checkout (it previously
# skipped when the session-local scratchpad artefact was absent).
SPIKE_PATH = Path(__file__).resolve().parent / "data" / "sounding_profile_okc_v2.json"


def _spike_frame() -> dict:
    """The OKC reference column as one ``compute_frame_indices`` payload."""
    spike = json.loads(SPIKE_PATH.read_text())
    surface = spike["surface"]
    return {
        "levels_hpa": list(spike["pressure_hPa"]),
        "t": list(spike["T_C"]),
        "td": list(spike["Td_C"]),
        "u": list(spike["U_ms"]),
        "v": list(spike["V_ms"]),
        "surface": {
            "pres_sfc": surface["psfc_hPa"],
            "t2m": surface["t2m_C"],
            "td2m": surface["td2m_C"],
            "u10m": surface["u10_ms"],
            "v10m": surface["v10_ms"],
        },
    }


@pytest.fixture(autouse=True)
def _fresh_pool_state():
    """Each test starts with no pool and the disable latch cleared."""
    sounding_thermo.shutdown_pool()
    yield
    sounding_thermo.shutdown_pool()


# ---------------------------------------------------------------------------
# Worker-count policy
# ---------------------------------------------------------------------------


def test_default_worker_count_takes_at_most_half_the_box(monkeypatch) -> None:
    monkeypatch.delenv(sounding_thermo.WORKERS_ENV_VAR, raising=False)
    monkeypatch.setattr(sounding_thermo.os, "cpu_count", lambda: 16)
    assert sounding_thermo.configured_workers() == sounding_thermo.MAX_WORKERS_CAP

    monkeypatch.setattr(sounding_thermo.os, "cpu_count", lambda: 4)
    assert sounding_thermo.configured_workers() == 2

    # A single-core box must still answer a usable (serial) count, never 0.
    monkeypatch.setattr(sounding_thermo.os, "cpu_count", lambda: 1)
    assert sounding_thermo.configured_workers() == 1


def test_env_var_overrides_and_zero_means_serial(monkeypatch) -> None:
    monkeypatch.setenv(sounding_thermo.WORKERS_ENV_VAR, "3")
    assert sounding_thermo.configured_workers() == 3
    monkeypatch.setenv(sounding_thermo.WORKERS_ENV_VAR, "0")
    assert sounding_thermo.configured_workers() == 0


def test_a_malformed_env_var_falls_back_to_the_default(monkeypatch) -> None:
    monkeypatch.setattr(sounding_thermo.os, "cpu_count", lambda: 8)
    monkeypatch.setenv(sounding_thermo.WORKERS_ENV_VAR, "lots")
    assert sounding_thermo.configured_workers() == 4


# ---------------------------------------------------------------------------
# Serial path
# ---------------------------------------------------------------------------


def test_forced_serial_never_starts_a_subprocess(monkeypatch) -> None:
    """The ops escape hatch has to be absolute, not merely a smaller pool."""
    monkeypatch.setenv(sounding_thermo.WORKERS_ENV_VAR, "0")

    def _explode(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("the pool must not be created when workers=0")

    monkeypatch.setattr(sounding_thermo, "ProcessPoolExecutor", _explode)

    calls: list[int] = []
    monkeypatch.setattr(
        sounding_indices,
        "compute_frame_indices",
        lambda **_kwargs: (calls.append(1), {"indices": 1, "parcel": 2, "profiles": 3})[1],
    )

    results = sounding_thermo.compute_frames([{"levels_hpa": [], "t": [], "td": []}] * 6)
    assert len(calls) == 6
    assert results == [{"indices": 1, "parcel": 2, "profiles": 3}] * 6


def test_a_short_request_stays_in_process(monkeypatch) -> None:
    """Spawn + pickle overhead is not worth it for a handful of frames."""
    monkeypatch.delenv(sounding_thermo.WORKERS_ENV_VAR, raising=False)

    def _explode(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("no pool for a request below the fan-out threshold")

    monkeypatch.setattr(sounding_thermo, "ProcessPoolExecutor", _explode)
    monkeypatch.setattr(
        sounding_indices, "compute_frame_indices", lambda **_kwargs: {"indices": None}
    )
    frames = [{"levels_hpa": [], "t": [], "td": []}] * (sounding_thermo.MIN_FRAMES_FOR_POOL - 1)
    assert len(sounding_thermo.compute_frames(frames)) == len(frames)


def test_no_frames_is_no_work() -> None:
    assert sounding_thermo.compute_frames([]) == []


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


def test_a_pool_that_cannot_start_degrades_to_serial(monkeypatch) -> None:
    """A sandbox that forbids subprocesses must still serve the response."""
    monkeypatch.delenv(sounding_thermo.WORKERS_ENV_VAR, raising=False)

    def _no_subprocesses(*_args, **_kwargs):
        raise OSError("subprocesses are not permitted here")

    monkeypatch.setattr(sounding_thermo, "ProcessPoolExecutor", _no_subprocesses)
    monkeypatch.setattr(
        sounding_indices, "compute_frame_indices", lambda **_kwargs: {"indices": "serial"}
    )

    results = sounding_thermo.compute_frames([{"levels_hpa": []}] * 8)
    assert results == [{"indices": "serial"}] * 8
    # Latched off: the next request does not pay the failed start again.
    assert sounding_thermo._pool_disabled is True


def test_a_worker_exception_nulls_only_that_frame(monkeypatch) -> None:
    monkeypatch.delenv(sounding_thermo.WORKERS_ENV_VAR, raising=False)

    class _FakePool:
        def submit(self, _func, **kwargs):
            future: Future = Future()
            if kwargs.get("marker") == "bad":
                future.set_exception(RuntimeError("worker died on this column"))
            else:
                future.set_result({"indices": kwargs.get("marker"), "parcel": None,
                                   "profiles": None})
            return future

    monkeypatch.setattr(sounding_thermo, "_get_pool", lambda _workers: _FakePool())

    frames = [{"marker": name} for name in ("a", "b", "bad", "d", "e")]
    results = sounding_thermo.compute_frames(frames)

    assert [result["indices"] for result in results] == ["a", "b", None, "d", "e"]
    assert results[2] == {"indices": None, "parcel": None, "profiles": None}


def test_a_broken_executor_redoes_the_whole_request_serially(monkeypatch) -> None:
    """Half a response is worse than a slow one: fall back for every frame."""
    from concurrent.futures import BrokenExecutor

    monkeypatch.delenv(sounding_thermo.WORKERS_ENV_VAR, raising=False)

    class _BreakingPool:
        def __init__(self) -> None:
            self.calls = 0

        def submit(self, _func, **_kwargs):
            future: Future = Future()
            self.calls += 1
            if self.calls > 2:
                future.set_exception(BrokenExecutor("pool is gone"))
            else:
                future.set_result({"indices": "pooled", "parcel": None, "profiles": None})
            return future

    monkeypatch.setattr(sounding_thermo, "_get_pool", lambda _workers: _BreakingPool())
    monkeypatch.setattr(
        sounding_indices, "compute_frame_indices", lambda **_kwargs: {"indices": "serial"}
    )

    results = sounding_thermo.compute_frames([{"levels_hpa": []}] * 6)
    assert results == [{"indices": "serial"}] * 6
    assert sounding_thermo._pool_disabled is True


def test_a_raising_serial_frame_is_contained(monkeypatch) -> None:
    monkeypatch.setenv(sounding_thermo.WORKERS_ENV_VAR, "0")

    def _sometimes_raises(**kwargs):
        if kwargs.get("marker") == "bad":
            raise RuntimeError("synthetic failure")
        return {"indices": kwargs.get("marker"), "parcel": None, "profiles": None}

    monkeypatch.setattr(sounding_indices, "compute_frame_indices", _sometimes_raises)
    results = sounding_thermo.compute_frames([{"marker": "a"}, {"marker": "bad"}])
    assert [result["indices"] for result in results] == ["a", None]


# ---------------------------------------------------------------------------
# The real pool
# ---------------------------------------------------------------------------


def test_real_pool_reproduces_the_serial_result_exactly(monkeypatch) -> None:
    """The one test that actually spawns workers.

    Slow (~3 s: spawn plus one MetPy import per worker) and the only place the
    real subprocess path is exercised, because every other suite pins itself to
    serial so it can monkeypatch the computation.
    """
    frame = _spike_frame()
    frames = [frame] * 6

    monkeypatch.setenv(sounding_thermo.WORKERS_ENV_VAR, "0")
    serial = sounding_thermo.compute_frames(frames)

    monkeypatch.setenv(sounding_thermo.WORKERS_ENV_VAR, "2")
    pooled = sounding_thermo.compute_frames(frames)

    assert len(pooled) == len(serial) == len(frames)
    # Byte-for-byte: the pool must be a scheduling change and nothing else.
    assert pooled == serial
    assert pooled[0]["indices"]["sbcape"] is not None
    assert pooled[0]["profiles"]["tw"] is not None
    assert sounding_thermo._pool_disabled is False
