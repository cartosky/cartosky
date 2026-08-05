"""Fast-source sub-loop end-to-end against a faked bucket (WP3, design §4/§6/§8).

No network: a fake source module hands the sub-loop synthetic full-globe O1280
fields, and everything downstream — regrid, unit conversion, the WP2 ledger,
packing, sidecars, manifests, staging→promote — is the real production code.
That is the whole point of the fast path being a *fetch source* rather than a
pipeline (design §2), so the tests exercise the real writers rather than
mocking them.

The heavy tests pin ownership to the ``global`` 0.25° domain (721×1440) with a
per-domain override so a full test run does not pack 1825×1893 NA frames for
every case; :func:`test_na_domain_frames_land_on_the_production_9km_grid`
covers the NA target once.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.registry import MODEL_REGISTRY
from app.services import scheduler as scheduler_module
from app.services.fastpath import ownership
from app.services.fastpath import subloop as subloop_module
from app.services.fastpath.state import FastpathRunState, fastpath_namespace_dir  # noqa: F401
from app.services.sources.openmeteo.catalog import ECMWF_IFS_CATALOG
from app.services.sources.openmeteo.source import RunObject

RUN_DT = datetime(2026, 8, 4, 0, tzinfo=timezone.utc)
RUN_ID = "20260804_00z"
SOURCE_POINTS = ECMWF_IFS_CATALOG.source_points

#: Per-step synthetic values, in ``.om`` source units.
STEP_PRECIP_MM = 0.5
STEP_SNOW_MM_SWE = 0.1
GUST_MS = 5.0
WIND_U_MS = 3.0
WIND_V_MS = 4.0

MM_TO_IN = 0.03937007874015748
MS_TO_MPH = 2.23694


@pytest.fixture(autouse=True)
def _fastpath_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ownership.ENV_FASTPATH_MODELS, "ecmwf")
    monkeypatch.delenv(ownership.ENV_FASTPATH_VAR_OVERRIDES, raising=False)
    # The fast path may only own a domain this environment actually builds, so
    # every global-domain test has to enable global the same way production
    # would (see ownership's "Domain enablement").
    monkeypatch.setenv("CARTOSKY_GLOBAL_DOMAIN_MODELS", "ecmwf")


@pytest.fixture()
def global_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the fast path to the 0.25° global target for speed."""
    monkeypatch.setenv(
        ownership.ENV_FASTPATH_VAR_OVERRIDES,
        ",".join(f"ecmwf:{var_id}:na=delayed" for var_id in _LAUNCH_VARS),
    )


_LAUNCH_VARS = ("tmp2m", "dp2m", "precip_total", "snowfall_total", "wgst10m", "wspd10m")


# ---------------------------------------------------------------------------
# Fake source
# ---------------------------------------------------------------------------


def _ramped(base: float) -> np.ndarray:
    """A cheap spatially varying field (constant fields make degenerate legends)."""
    values = np.empty(SOURCE_POINTS, dtype=np.float32)
    values[:] = base
    values[::4096] = np.float32(base + 1.0)
    return values


def _constant(value: float) -> np.ndarray:
    return np.full(SOURCE_POINTS, np.float32(value), dtype=np.float32)


class FakeSource:
    """Stands in for ``sources.openmeteo.source``.

    ``poison`` marks ``(fh, om_name)`` pairs whose decode should come back
    unusable, which is how the design §6 integrity gates get exercised without
    a corrupt object on disk.
    """

    def __init__(
        self,
        *,
        max_fh: int = 6,
        poison: set[tuple[int, str]] | None = None,
        fail_after_fh: int | None = None,
    ) -> None:
        self.max_fh = int(max_fh)
        self.poison = set(poison or ())
        self.fail_after_fh = fail_after_fh
        self.fetched: list[tuple[int, tuple[str, ...]]] = []

    # -- listing ------------------------------------------------------------

    def list_run_objects(self, catalog, run_dt, **_kwargs):
        del catalog
        objects = []
        for fh in range(0, self.max_fh + 1):
            valid = run_dt + timedelta(hours=fh)
            objects.append(
                RunObject(
                    valid_dt=valid,
                    fh=fh,
                    url=f"https://example.invalid/{valid:%Y-%m-%dT%H%M}.om",
                    last_modified=valid,
                    etag=f"etag-{fh:03d}",
                )
            )
        return objects

    def read_in_progress(self, catalog, **_kwargs):
        del catalog
        return SimpleNamespace(reference_time=RUN_DT, completed=False)

    # -- fetch --------------------------------------------------------------

    def fetch_frame_fields(self, catalog, url, var_names, window="global", **_kwargs):
        del catalog
        fh = self._fh_from_url(url)
        names = tuple(var_names)
        self.fetched.append((fh, names))
        assert window == "global", "design Decision 1: one full-globe read feeds both targets"
        if self.fail_after_fh is not None and fh > self.fail_after_fh:
            raise RuntimeError(f"simulated bucket stall at FH{fh:03d}")

        fields: dict[str, np.ndarray] = {}
        for name in names:
            # FH000 legitimately carries no accumulation or gust fields.
            if fh == 0 and name in (
                "precipitation",
                "snowfall_water_equivalent",
                "wind_gusts_10m",
            ):
                continue
            if (fh, name) in self.poison:
                fields[name] = np.full(SOURCE_POINTS, np.nan, dtype=np.float32)
                continue
            fields[name] = self._field(name)
        return fields

    def _field(self, name: str) -> np.ndarray:
        if name == "temperature_2m":
            return _ramped(15.0)
        if name == "dew_point_2m":
            return _ramped(5.0)
        if name == "precipitation":
            return _constant(STEP_PRECIP_MM)
        if name == "snowfall_water_equivalent":
            return _constant(STEP_SNOW_MM_SWE)
        if name == "wind_gusts_10m":
            return _constant(GUST_MS)
        if name == "wind_u_component_10m":
            return _constant(WIND_U_MS)
        if name == "wind_v_component_10m":
            return _constant(WIND_V_MS)
        raise AssertionError(f"fake source asked for an unexpected variable {name!r}")

    @staticmethod
    def _fh_from_url(url: str) -> int:
        stem = url.rsplit("/", 1)[-1].removesuffix(".om")
        valid = datetime.strptime(stem, "%Y-%m-%dT%H%M").replace(tzinfo=timezone.utc)
        return int((valid - RUN_DT).total_seconds() // 3600)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plugin():
    return MODEL_REGISTRY.get("ecmwf")


def _sidecar(data_root: Path, var_id: str, fh: int, domain: str = "global") -> dict | None:
    path = scheduler_module._frame_sidecar_path(
        data_root, "ecmwf", RUN_ID, var_id, fh, region=domain
    )
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _frame_exists(data_root: Path, var_id: str, fh: int, domain: str = "global") -> bool:
    return scheduler_module._frame_artifacts_exist(
        data_root, "ecmwf", RUN_ID, var_id, fh, region=domain
    )


def _run_pass(data_root: Path, source: FakeSource, **kwargs):
    return subloop_module.run_fastpath_pass(
        plugin=_plugin(),
        model_id="ecmwf",
        data_root=data_root,
        run_dt=RUN_DT,
        source=source,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.fixture()
def completed_pass(tmp_path: Path, global_only):
    source = FakeSource(max_fh=6)
    result = _run_pass(tmp_path, source)
    return tmp_path, source, result


def test_pass_writes_frames_on_the_3h_ladder_only(completed_pass) -> None:
    data_root, _source, result = completed_pass
    assert result.run_id == RUN_ID
    assert not result.stalled, result.stall_reasons

    # Instantaneous variables publish at FH000 and every 3 h boundary.
    for var_id in ("tmp2m", "dp2m", "wspd10m"):
        assert _frame_exists(data_root, var_id, 0), var_id
        assert _frame_exists(data_root, var_id, 3), var_id
        assert _frame_exists(data_root, var_id, 6), var_id
        # Design Decision 2: hourly source steps are NOT published as frames.
        assert not _frame_exists(data_root, var_id, 1), var_id
        assert not _frame_exists(data_root, var_id, 2), var_id

    # Accumulation + gust variables have no FH000 frame at all.
    for var_id in ("precip_total", "snowfall_total", "wgst10m"):
        assert not _frame_exists(data_root, var_id, 0), var_id
        assert _frame_exists(data_root, var_id, 3), var_id
        assert _frame_exists(data_root, var_id, 6), var_id


def test_source_steps_are_fetched_in_fh_order_including_unpublished_hours(
    completed_pass,
) -> None:
    data_root, source, _result = completed_pass
    fetched_fhs = [fh for fh, _names in source.fetched]
    assert fetched_fhs == sorted(fetched_fhs), "dissemination is sequential"
    # Hourly steps below FH90 are fetched (the ledger needs them) even though
    # they publish no frame.
    assert set(fetched_fhs) >= {0, 1, 2, 3, 4, 5, 6}


def test_accumulation_is_run_cumulative_not_per_step(completed_pass) -> None:
    data_root, _source, _result = completed_pass
    # 3 hourly steps of 0.5 mm -> 1.5 mm run total -> inches.
    expected_fh3 = 3 * STEP_PRECIP_MM * MM_TO_IN
    expected_fh6 = 6 * STEP_PRECIP_MM * MM_TO_IN
    sidecar_3 = _sidecar(data_root, "precip_total", 3)
    sidecar_6 = _sidecar(data_root, "precip_total", 6)
    assert sidecar_3 is not None and sidecar_6 is not None
    assert sidecar_3["max"] == pytest.approx(expected_fh3, rel=1e-4)
    assert sidecar_6["max"] == pytest.approx(expected_fh6, rel=1e-4)
    assert sidecar_6["max"] > sidecar_3["max"]

    # Snowfall carries the 10:1 ratio inside its unit conversion.
    snow_3 = _sidecar(data_root, "snowfall_total", 3)
    assert snow_3["max"] == pytest.approx(3 * STEP_SNOW_MM_SWE * MM_TO_IN * 10.0, rel=1e-4)


def test_units_match_the_production_storage_units(completed_pass) -> None:
    data_root, _source, _result = completed_pass
    tmp2m = _sidecar(data_root, "tmp2m", 0)
    assert tmp2m["units"] in ("F", "°F")
    # 15 °C -> 59 °F (the ramp adds at most 1 °C -> 1.8 °F).
    assert tmp2m["min"] == pytest.approx(59.0, abs=0.2)

    gust = _sidecar(data_root, "wgst10m", 3)
    assert gust["max"] == pytest.approx(GUST_MS * MS_TO_MPH, rel=1e-4)


def test_derived_wind_speed_matches_the_production_derive(completed_pass) -> None:
    """``hypot(u, v)`` on the TARGET grid, then ``ms_to_mph`` — the exact order
    ``derive._derive_wspd10m`` uses (components warped first, then magnitude,
    then ``convert_units``)."""
    data_root, _source, _result = completed_pass
    expected_mph = float(np.hypot(WIND_U_MS, WIND_V_MS)) * MS_TO_MPH
    sidecar = _sidecar(data_root, "wspd10m", 3)
    assert sidecar is not None
    assert sidecar["max"] == pytest.approx(expected_mph, rel=1e-4)
    assert sidecar["min"] == pytest.approx(expected_mph, rel=1e-4)


def test_checkpoint_appears_at_each_published_boundary(completed_pass) -> None:
    data_root, _source, _result = completed_pass
    checkpoints = sorted(fastpath_namespace_dir(data_root, "ecmwf").glob("*.accum.npz"))
    assert len(checkpoints) == 1, checkpoints
    with np.load(checkpoints[0], allow_pickle=False) as payload:
        meta = json.loads(str(payload["__meta__"]))
    assert meta["frontier_published_fh"] == 6
    assert meta["last_ingested_fh"] == 6
    assert meta["source_id"] == "openmeteo"


def test_frames_are_promoted_to_the_published_tree(completed_pass) -> None:
    data_root, _source, result = completed_pass
    assert result.timesteps_promoted >= 2
    published = (
        scheduler_module._published_model_root(data_root, "ecmwf", "global") / RUN_ID
    )
    assert (published / "tmp2m" / "fh000.json").is_file()
    assert (published / "precip_total" / "fh006.json").is_file()


# ---------------------------------------------------------------------------
# Provenance (design §8)
# ---------------------------------------------------------------------------


def test_frame_provenance_keys_are_present_and_additive(completed_pass) -> None:
    data_root, _source, _result = completed_pass
    sidecar = _sidecar(data_root, "tmp2m", 3)
    assert sidecar["source_id"] == "openmeteo"
    assert sidecar["source_resolution"] == "9 km native"
    assert sidecar["source_object_key"].endswith(".om")
    assert sidecar["source_object_etag"] == "etag-003"
    assert sidecar["source_variables"] == ["temperature_2m"]
    assert isinstance(sidecar["cadence_version"], str) and sidecar["cadence_version"]
    assert sidecar["generation"] == 1
    assert sidecar["source_attribution"].startswith("ECMWF IFS data")

    # Every pre-existing sidecar key is untouched.
    for key in ("contract_version", "model", "run", "var", "fh", "valid_time", "units",
                "kind", "min", "max", "legend", "quality", "quality_flags"):
        assert key in sidecar, key
    assert sidecar["model"] == "ecmwf"
    assert sidecar["var"] == "tmp2m"
    assert sidecar["fh"] == 3
    assert sidecar["region"] == "global"


def test_derived_wind_records_both_component_objects(completed_pass) -> None:
    data_root, _source, _result = completed_pass
    sidecar = _sidecar(data_root, "wspd10m", 3)
    assert sidecar["source_variables"] == [
        "wind_u_component_10m",
        "wind_v_component_10m",
    ]


def test_run_manifest_summarizes_homogeneous_source_ranges(completed_pass) -> None:
    data_root, _source, _result = completed_pass
    manifest_path = scheduler_module._manifest_path(
        data_root, "ecmwf", RUN_ID, region="global"
    )
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["variables"]["tmp2m"]
    assert entry["ready_through_fh"] == 6
    ranges = entry["source_ranges"]
    assert len(ranges) == 1, ranges
    assert ranges[0] == {
        "from_fh": 0,
        "to_fh": 6,
        "source_id": "openmeteo",
        "source_resolution": "9 km native",
        "generation": 1,
    }


def test_delayed_only_manifest_has_no_source_ranges_key(tmp_path: Path) -> None:
    """The manifest addition is additive: a run with no provenance-bearing
    sidecars serialises exactly as it did before WP3."""
    assert scheduler_module._summarize_source_ranges({}) == []


# ---------------------------------------------------------------------------
# FH000 / integrity gates (design §6)
# ---------------------------------------------------------------------------


def test_fh000_handles_absent_accumulation_and_gust_fields(tmp_path: Path, global_only) -> None:
    source = FakeSource(max_fh=0)
    result = _run_pass(tmp_path, source)
    assert not result.stalled, result.stall_reasons
    assert _frame_exists(tmp_path, "tmp2m", 0)
    assert _frame_exists(tmp_path, "dp2m", 0)
    assert _frame_exists(tmp_path, "wspd10m", 0)
    assert not _frame_exists(tmp_path, "precip_total", 0)
    assert not _frame_exists(tmp_path, "wgst10m", 0)


def test_integrity_gate_skips_a_poisoned_instantaneous_frame(
    tmp_path: Path, global_only
) -> None:
    source = FakeSource(max_fh=3, poison={(3, "temperature_2m")})
    result = _run_pass(tmp_path, source)

    assert not _frame_exists(tmp_path, "tmp2m", 3), "never publish a failed decode"
    # Its neighbours at the same timestep are unaffected.
    assert _frame_exists(tmp_path, "dp2m", 3)
    assert _frame_exists(tmp_path, "precip_total", 3)
    assert _frame_exists(tmp_path, "tmp2m", 0)


def test_integrity_gate_on_an_accumulation_step_stops_the_run(
    tmp_path: Path, global_only
) -> None:
    """Skipping an accumulation step would desync the run-cumulative series, so
    the pass stalls instead — the failover deadline is the recovery path."""
    source = FakeSource(max_fh=6, poison={(2, "precipitation")})
    result = _run_pass(tmp_path, source)

    assert result.stalled
    assert any("accumulation_integrity" in reason for reason in result.stall_reasons)
    assert not _frame_exists(tmp_path, "precip_total", 3)
    # Frames written before the bad step survive.
    assert _frame_exists(tmp_path, "tmp2m", 0)


def test_out_of_range_temperature_is_rejected(tmp_path: Path) -> None:
    values = np.full(SOURCE_POINTS, np.float32(400.0), dtype=np.float32)
    with pytest.raises(subloop_module.IntegrityError, match="physical band"):
        subloop_module.check_field_integrity(ECMWF_IFS_CATALOG, "temperature_2m", values)


def test_wrong_shape_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(subloop_module.IntegrityError, match="expected"):
        subloop_module.check_field_integrity(
            ECMWF_IFS_CATALOG, "temperature_2m", np.zeros(10, dtype=np.float32)
        )


# ---------------------------------------------------------------------------
# Resume / idempotence
# ---------------------------------------------------------------------------


def test_second_pass_resumes_from_the_checkpoint_without_refetching(
    tmp_path: Path, global_only
) -> None:
    first = FakeSource(max_fh=3)
    _run_pass(tmp_path, first, max_steps=4)
    assert _frame_exists(tmp_path, "precip_total", 3)

    second = FakeSource(max_fh=6)
    result = _run_pass(tmp_path, second)

    refetched = [fh for fh, _names in second.fetched if fh <= 3]
    assert refetched == [], "the checkpoint must make already-ingested steps unnecessary"
    assert _frame_exists(tmp_path, "precip_total", 6)
    sidecar = _sidecar(tmp_path, "precip_total", 6)
    assert sidecar["max"] == pytest.approx(6 * STEP_PRECIP_MM * MM_TO_IN, rel=1e-4)
    assert not result.stalled, result.stall_reasons


# ---------------------------------------------------------------------------
# FH90 cadence transition
# ---------------------------------------------------------------------------


def test_fh90_cadence_transition_is_crossed_correctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hourly→3 h ladder change at FH90 (design §4, prototype Phase 3).

    Below FH90 three hourly ``.om`` steps make one published 3 h frame; at and
    beyond FH90 the ladders coincide 1:1, so FH093 is a single step. Both sides
    of that seam have to come out of the ledger as correct RUN-CUMULATIVE
    totals, and the frame set must stay on the production ladder across it.

    Scoped to ``tmp2m`` + ``precip_total`` on the global target with
    ``promote=False``: 97 timesteps through the real packer is the point, 97
    promote copytrees of six variables is not.
    """
    monkeypatch.setenv(
        ownership.ENV_FASTPATH_VAR_OVERRIDES,
        ",".join(
            [f"ecmwf:{var_id}:na=delayed" for var_id in _LAUNCH_VARS]
            + [
                f"ecmwf:{var_id}:global=delayed"
                for var_id in _LAUNCH_VARS
                if var_id not in ("tmp2m", "precip_total")
            ]
        ),
    )

    checkpointed: list[int] = []
    from app.services.sources.openmeteo.accumulation import AccumulationLedger

    original_save = AccumulationLedger.save_checkpoint

    def _spy(self, directory):
        checkpointed.append(self.frontier_published_fh)
        return original_save(self, directory)

    monkeypatch.setattr(AccumulationLedger, "save_checkpoint", _spy)

    source = FakeSource(max_fh=96)
    result = _run_pass(tmp_path, source, max_steps=None, promote=False)
    assert not result.stalled, result.stall_reasons

    # -- the WP2 windows either side of the seam ---------------------------
    ledger = AccumulationLedger(ECMWF_IFS_CATALOG, RUN_DT)
    assert ledger.window_for(87).source_fhs == (85, 86, 87)
    assert ledger.window_for(90).source_fhs == (88, 89, 90)
    assert ledger.window_for(93).source_fhs == (93,)
    assert ledger.window_for(96).source_fhs == (96,)

    # -- the published frame set around the boundary -----------------------
    published = [fh for fh in range(84, 97) if _frame_exists(tmp_path, "tmp2m", fh)]
    assert published == [84, 87, 90, 93, 96]
    assert [fh for fh in range(84, 97) if _frame_exists(tmp_path, "precip_total", fh)] == [
        84,
        87,
        90,
        93,
        96,
    ]

    # -- run-cumulative totals across the seam -----------------------------
    # Steps 1..90 are hourly, then 93 and 96 add one step each. The totals
    # therefore go 87 -> 90 -> 91 -> 92 steps, NOT 87 -> 90 -> 93 -> 96.
    for fh, steps in ((87, 87), (90, 90), (93, 91), (96, 92)):
        sidecar = _sidecar(tmp_path, "precip_total", fh)
        assert sidecar is not None, fh
        assert sidecar["max"] == pytest.approx(
            steps * STEP_PRECIP_MM * MM_TO_IN, rel=1e-4
        ), f"FH{fh:03d} should be {steps} steps of accumulation"

    # -- a checkpoint at every published boundary --------------------------
    expected_boundaries = [fh for fh in range(3, 97, 3)]
    assert checkpointed == expected_boundaries
    with np.load(
        next(fastpath_namespace_dir(tmp_path, "ecmwf").glob("*.accum.npz")),
        allow_pickle=False,
    ) as payload:
        meta = json.loads(str(payload["__meta__"]))
    assert meta["frontier_published_fh"] == 96
    assert meta["last_ingested_fh"] == 96


# ---------------------------------------------------------------------------
# NA domain
# ---------------------------------------------------------------------------


def test_na_domain_frames_land_on_the_production_9km_grid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        ownership.ENV_FASTPATH_VAR_OVERRIDES,
        ",".join(
            [f"ecmwf:{var_id}:global=delayed" for var_id in _LAUNCH_VARS]
            + [f"ecmwf:{var_id}:na=delayed" for var_id in _LAUNCH_VARS if var_id != "tmp2m"]
        ),
    )
    source = FakeSource(max_fh=0)
    result = _run_pass(tmp_path, source)
    assert not result.stalled, result.stall_reasons
    assert result.active_pairs == (("tmp2m", "na"),)

    meta_path = scheduler_module._frame_primary_artifact_path(
        tmp_path, "ecmwf", RUN_ID, "tmp2m", 0, region="na"
    )
    meta = json.loads(meta_path.read_text())
    assert (meta["height"], meta["width"]) == (1825, 1893)
    assert _sidecar(tmp_path, "tmp2m", 0, domain="na")["region"] == "na"
