from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import scheduler as scheduler_module
from app.models.base import ModelCapabilities, VariableCapability


class _FakePlugin:
    id = "hrrr"
    capabilities = None

    def normalize_var_id(self, var_id: str) -> str:
        return str(var_id)

    def scheduled_fhs_for_var(self, var_key: str, cycle_hour: int) -> list[int]:
        del var_key, cycle_hour
        return [0, 1, 2, 3, 4]


class _FakeCapabilityPlugin(_FakePlugin):
    capabilities = ModelCapabilities(
        model_id="hrrr",
        name="HRRR",
        ui_defaults={"default_var_key": "radar_ptype"},
        variable_catalog={
            "tmp2m": VariableCapability(var_key="tmp2m", name="TMP", default_fh=0),
            "radar_ptype": VariableCapability(var_key="radar_ptype", name="PType", default_fh=2),
        },
    )

    def get_var_capability(self, var_key: str) -> VariableCapability | None:
        return self.capabilities.variable_catalog.get(var_key)

    def normalize_var_id(self, var_id: str) -> str:
        return str(var_id).strip().lower()


class _FakeProbePlugin:
    id = "ecmwf"
    product = "oper"

    def normalize_var_id(self, var_id: str) -> str:
        return str(var_id).strip().lower()

    def get_var_capability(self, var_key: str):
        del var_key
        return None

    def get_var(self, var_key: str):
        if var_key != "tmp2m":
            return None
        return types.SimpleNamespace(selectors=types.SimpleNamespace(search=[":2t:"]))

    def run_discovery_config(self) -> dict[str, object]:
        return {
            "probe_fhs": [0, 3],
            "source_priority": ["azure", "aws"],
        }

    def herbie_request(self, *, product=None, var_key=None, run_date=None, fh=None, search_pattern=None):
        del var_key, run_date, search_pattern
        return types.SimpleNamespace(model="ifs", product=product or self.product, herbie_kwargs={"priority": ["azure", "aws"]})


def test_parse_vars_or_auto_supports_auto_tokens() -> None:
    assert scheduler_module._parse_vars_or_auto(None) == []
    assert scheduler_module._parse_vars_or_auto("") == []
    assert scheduler_module._parse_vars_or_auto("auto") == []
    assert scheduler_module._parse_vars_or_auto("ALL") == []
    assert scheduler_module._parse_vars_or_auto("tmp2m, mlcape") == ["tmp2m", "mlcape"]


def test_main_uses_auto_vars_when_env_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.delenv("CARTOSKY_SCHEDULER_VARS", raising=False)
    monkeypatch.setenv("CARTOSKY_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        scheduler_module,
        "run_scheduler",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    rc = scheduler_module.main(["--model", "hrrr", "--once"])

    assert rc == 0
    assert captured["model"] == "hrrr"
    assert captured["vars_to_build"] == []


def test_main_uses_auto_vars_when_env_requests_auto(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setenv("CARTOSKY_SCHEDULER_VARS", "auto")
    monkeypatch.setenv("CARTOSKY_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        scheduler_module,
        "run_scheduler",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    rc = scheduler_module.main(["--model", "hrrr", "--once"])

    assert rc == 0
    assert captured["vars_to_build"] == []


def test_main_parses_explicit_env_vars_when_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setenv("CARTOSKY_SCHEDULER_VARS", "tmp2m,mlcape")
    monkeypatch.setenv("CARTOSKY_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        scheduler_module,
        "run_scheduler",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    rc = scheduler_module.main(["--model", "hrrr", "--once"])

    assert rc == 0
    assert captured["vars_to_build"] == ["tmp2m", "mlcape"]


def test_probe_run_exists_checks_multiple_probe_fhs(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_fxxs: list[tuple[int, str]] = []

    class _FakeHerbie:
        def __init__(self, run_dt, *, model, product, fxx, priority, **kwargs):
            del run_dt, model, product, kwargs
            self.fxx = int(fxx)
            self.priority = str(priority)

        def inventory(self, pattern: str):
            seen_fxxs.append((self.fxx, self.priority))
            if self.fxx == 3 and self.priority == "azure" and pattern == ":2t:":
                return [object()]
            return []

    monkeypatch.setitem(sys.modules, "herbie.core", types.SimpleNamespace(Herbie=_FakeHerbie))

    found = scheduler_module._probe_run_exists(
        plugin=_FakeProbePlugin(),
        run_dt=datetime(2026, 4, 13, 12, tzinfo=timezone.utc),
        probe_var="tmp2m",
    )

    assert found is True
    assert seen_fxxs == [(0, "azure"), (0, "aws"), (3, "azure")]


def test_resolve_probe_fhs_defaults_to_zero() -> None:
    plugin = types.SimpleNamespace(run_discovery_config=lambda: {})
    assert scheduler_module._resolve_probe_fhs(plugin) == [0]


class _FakeGFSPlugin:
    id = "gfs"

    def normalize_var_id(self, var_id: str) -> str:
        return str(var_id)

    def scheduled_fhs_for_var(self, var_key: str, cycle_hour: int) -> list[int]:
        del var_key, cycle_hour
        return [0, 3, 6, 9]


def test_resolve_promotion_fhs_uses_model_schedule() -> None:
    assert scheduler_module._resolve_promotion_fhs(_FakeGFSPlugin(), ["tmp2m"], 18) == (0, 3, 6)


def test_resolve_loop_prewarm_targets_use_default_var_and_default_fh() -> None:
    plugin = _FakeCapabilityPlugin()

    assert scheduler_module._resolve_loop_prewarm_var(plugin, ["tmp2m", "radar_ptype"], ["tmp2m"]) == "radar_ptype"
    assert scheduler_module._resolve_loop_prewarm_fhs(plugin, "radar_ptype", 12, limit=3) == (2, 3, 4)


def test_process_run_uses_resolved_promotion_fhs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dt = datetime(2026, 2, 27, 18, tzinfo=timezone.utc)
    seen_promotion_fhs: list[tuple[int, ...]] = []

    def fake_frame_artifacts_exist(
        data_root: Path,
        model: str,
        run: str,
        var_id: str,
        fh: int,
    ) -> bool:
        del data_root, model, run, var_id, fh
        return False

    def fake_build_one(
        *,
        model_id: str,
        var_id: str,
        fh: int,
        run_dt: datetime,
        data_root: Path,
        plugin: object,
    ) -> tuple[str, int, bool]:
        del model_id, run_dt, data_root, plugin
        return var_id, fh, False

    def fake_should_promote(
        data_root: Path,
        model: str,
        run_id: str,
        primary_vars: list[str],
        promotion_fhs: tuple[int, ...],
    ) -> bool:
        del data_root, model, run_id, primary_vars
        seen_promotion_fhs.append(tuple(int(fh) for fh in promotion_fhs))
        return False

    monkeypatch.setattr(scheduler_module, "_frame_artifacts_exist", fake_frame_artifacts_exist)
    monkeypatch.setattr(scheduler_module, "_build_one", fake_build_one)
    monkeypatch.setattr(scheduler_module, "_should_promote", fake_should_promote)
    monkeypatch.setattr(scheduler_module, "_enforce_run_retention", lambda *args, **kwargs: None)

    scheduler_module._process_run(
        plugin=_FakeGFSPlugin(),
        model_id="gfs",
        vars_to_build=["tmp2m"],
        primary_vars=["tmp2m"],
        run_dt=run_dt,
        data_root=tmp_path,
        workers=1,
        keep_runs=2,
        loop_pregenerate_enabled=False,
        loop_cache_root=tmp_path / "loop-cache",
        loop_workers=1,
        loop_tier0_quality=82,
        loop_tier0_max_dim=2300,
        loop_tier0_fixed_w=2300,
        loop_tier1_quality=86,
        loop_tier1_max_dim=2400,
        loop_tier1_fixed_w=2400,
    )

    assert seen_promotion_fhs
    assert seen_promotion_fhs[0] == (0, 3, 6)


def test_process_run_catches_up_consecutive_available_hours(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dt = datetime(2026, 2, 27, 12, tzinfo=timezone.utc)
    run_id = scheduler_module._run_id_from_dt(run_dt)
    model_id = "hrrr"

    built: set[tuple[str, int]] = set()
    attempted: list[tuple[str, int]] = []
    available_up_to = {"tmp2m": 3}

    def fake_frame_artifacts_exist(
        data_root: Path,
        model: str,
        run: str,
        var_id: str,
        fh: int,
    ) -> bool:
        del data_root, model, run
        return (var_id, fh) in built

    def fake_build_one(
        *,
        model_id: str,
        var_id: str,
        fh: int,
        run_dt: datetime,
        data_root: Path,
        plugin: object,
    ) -> tuple[str, int, bool]:
        del model_id, run_dt, data_root, plugin
        attempted.append((var_id, fh))
        ok = fh <= available_up_to[var_id]
        if ok:
            built.add((var_id, fh))
        return var_id, fh, ok

    monkeypatch.setattr(scheduler_module, "_frame_artifacts_exist", fake_frame_artifacts_exist)
    monkeypatch.setattr(scheduler_module, "_build_one", fake_build_one)
    monkeypatch.setattr(scheduler_module, "_should_promote", lambda *args, **kwargs: False)
    monkeypatch.setattr(scheduler_module, "_enforce_run_retention", lambda *args, **kwargs: None)

    processed_run_id, available, total = scheduler_module._process_run(
        plugin=_FakePlugin(),
        model_id=model_id,
        vars_to_build=["tmp2m"],
        primary_vars=["tmp2m"],
        run_dt=run_dt,
        data_root=tmp_path,
        workers=1,
        keep_runs=2,
        loop_pregenerate_enabled=False,
        loop_cache_root=tmp_path / "loop-cache",
        loop_workers=1,
        loop_tier0_quality=82,
        loop_tier0_max_dim=2300,
        loop_tier0_fixed_w=2300,
        loop_tier1_quality=86,
        loop_tier1_max_dim=2400,
        loop_tier1_fixed_w=2400,
    )

    assert processed_run_id == run_id
    assert total == 5
    assert available == 4
    assert attempted == [("tmp2m", 0), ("tmp2m", 1), ("tmp2m", 2), ("tmp2m", 3), ("tmp2m", 4)]


def test_process_run_publishes_early_then_refreshes_after_more_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dt = datetime(2026, 2, 27, 12, tzinfo=timezone.utc)
    model_id = "hrrr"

    built: set[tuple[str, int]] = set()
    available_up_to = {"tmp2m": 3}

    def fake_frame_artifacts_exist(
        data_root: Path,
        model: str,
        run: str,
        var_id: str,
        fh: int,
    ) -> bool:
        del data_root, model, run
        return (var_id, fh) in built

    def fake_build_one(
        *,
        model_id: str,
        var_id: str,
        fh: int,
        run_dt: datetime,
        data_root: Path,
        plugin: object,
    ) -> tuple[str, int, bool]:
        del model_id, run_dt, data_root, plugin
        ok = fh <= available_up_to[var_id]
        if ok:
            built.add((var_id, fh))
        return var_id, fh, ok

    publish_promote_snapshots: list[list[int]] = []
    manifest_calls = 0
    pointer_calls = 0

    def fake_should_promote(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        return ("tmp2m", 2) in built

    def fake_promote_run(data_root: Path, model: str, run_id: str) -> None:
        del data_root, model, run_id
        publish_promote_snapshots.append(sorted(fh for var_id, fh in built if var_id == "tmp2m"))

    def fake_write_run_manifest(*args: object, **kwargs: object) -> None:
        del args, kwargs
        nonlocal manifest_calls
        manifest_calls += 1

    def fake_write_latest_pointer(data_root: Path, model: str, run_id: str) -> None:
        del data_root, model, run_id
        nonlocal pointer_calls
        pointer_calls += 1

    monkeypatch.setattr(scheduler_module, "_frame_artifacts_exist", fake_frame_artifacts_exist)
    monkeypatch.setattr(scheduler_module, "_build_one", fake_build_one)
    monkeypatch.setattr(scheduler_module, "_should_promote", fake_should_promote)
    monkeypatch.setattr(scheduler_module, "_promote_run", fake_promote_run)
    monkeypatch.setattr(scheduler_module, "_write_run_manifest", fake_write_run_manifest)
    monkeypatch.setattr(scheduler_module, "_write_latest_pointer", fake_write_latest_pointer)
    monkeypatch.setattr(scheduler_module, "_enforce_run_retention", lambda *args, **kwargs: None)

    scheduler_module._process_run(
        plugin=_FakePlugin(),
        model_id=model_id,
        vars_to_build=["tmp2m"],
        primary_vars=["tmp2m"],
        run_dt=run_dt,
        data_root=tmp_path,
        workers=1,
        keep_runs=2,
        loop_pregenerate_enabled=False,
        loop_cache_root=tmp_path / "loop-cache",
        loop_workers=1,
        loop_tier0_quality=82,
        loop_tier0_max_dim=2300,
        loop_tier0_fixed_w=2300,
        loop_tier1_quality=86,
        loop_tier1_max_dim=2400,
        loop_tier1_fixed_w=2400,
    )

    assert publish_promote_snapshots == [[0, 1, 2], [0, 1, 2, 3]]
    assert manifest_calls == 2
    assert pointer_calls == 2


class _TimingPlugin:
    id = "gfs"

    def scheduled_fhs_for_var(self, var_key: str, cycle_hour: int) -> list[int]:
        del var_key, cycle_hour
        return [0]

    def normalize_var_id(self, var_key: str) -> str:
        return str(var_key)


def test_process_run_logs_frame_timing_for_single_and_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run_dt = datetime(2026, 2, 27, 12, tzinfo=timezone.utc)

    monkeypatch.setenv("CARTOSKY_DERIVE_BUNDLE", "1")
    monkeypatch.setattr(scheduler_module, "_frame_artifacts_exist", lambda *args, **kwargs: False)
    monkeypatch.setattr(scheduler_module, "_should_promote", lambda *args, **kwargs: False)
    monkeypatch.setattr(scheduler_module, "_enforce_run_retention", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        scheduler_module,
        "_is_derive_bundle_candidate",
        lambda plugin, var_id: str(var_id) == "snowfall_total",
    )
    monkeypatch.setattr(
        scheduler_module,
        "_build_bundle",
        lambda **kwargs: [("snowfall_total", 0, True, 111)],
    )
    monkeypatch.setattr(
        scheduler_module,
        "_build_one",
        lambda **kwargs: (str(kwargs["var_id"]), int(kwargs["fh"]), True, 222),
    )

    with caplog.at_level("INFO"):
        scheduler_module._process_run(
            plugin=_TimingPlugin(),
            model_id="gfs",
            vars_to_build=["snowfall_total", "tmp2m"],
            primary_vars=["tmp2m"],
            run_dt=run_dt,
            data_root=tmp_path,
            workers=1,
            keep_runs=2,
            loop_pregenerate_enabled=False,
            loop_cache_root=tmp_path / "loop-cache",
            loop_workers=1,
            loop_tier0_quality=82,
            loop_tier0_max_dim=2300,
            loop_tier0_fixed_w=2300,
            loop_tier1_quality=86,
            loop_tier1_max_dim=2400,
            loop_tier1_fixed_w=2400,
        )

    assert "Frame timing: run=20260227_12z model=gfs var=snowfall_total fh000 ok=true mode=bundle elapsed_ms=111" in caplog.text
    assert "Frame timing: run=20260227_12z model=gfs var=tmp2m fh000 ok=true mode=single elapsed_ms=222" in caplog.text


def test_process_run_republishes_progress_during_long_catchup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dt = datetime(2026, 2, 27, 12, tzinfo=timezone.utc)
    model_id = "hrrr"

    built: set[tuple[str, int]] = set()
    available_up_to = {"tmp2m": 4}
    publish_promote_snapshots: list[list[int]] = []

    def fake_frame_artifacts_exist(
        data_root: Path,
        model: str,
        run: str,
        var_id: str,
        fh: int,
    ) -> bool:
        del data_root, model, run
        return (var_id, fh) in built

    def fake_build_one(
        *,
        model_id: str,
        var_id: str,
        fh: int,
        run_dt: datetime,
        data_root: Path,
        plugin: object,
    ) -> tuple[str, int, bool]:
        del model_id, run_dt, data_root, plugin
        ok = fh <= available_up_to[var_id]
        if ok:
            built.add((var_id, fh))
        return var_id, fh, ok

    def fake_should_promote(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        return ("tmp2m", 1) in built

    def fake_promote_run(data_root: Path, model: str, run_id: str) -> None:
        del data_root, model, run_id
        publish_promote_snapshots.append(sorted(fh for var_id, fh in built if var_id == "tmp2m"))

    monkeypatch.setenv("CARTOSKY_PROGRESS_PUBLISH_MIN_NEW_FRAMES", "1")
    monkeypatch.setattr(scheduler_module, "_frame_artifacts_exist", fake_frame_artifacts_exist)
    monkeypatch.setattr(scheduler_module, "_build_one", fake_build_one)
    monkeypatch.setattr(scheduler_module, "_should_promote", fake_should_promote)
    monkeypatch.setattr(scheduler_module, "_promote_run", fake_promote_run)
    monkeypatch.setattr(scheduler_module, "_write_run_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler_module, "_write_latest_pointer", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler_module, "_enforce_run_retention", lambda *args, **kwargs: None)

    scheduler_module._process_run(
        plugin=_FakePlugin(),
        model_id=model_id,
        vars_to_build=["tmp2m"],
        primary_vars=["tmp2m"],
        run_dt=run_dt,
        data_root=tmp_path,
        workers=1,
        keep_runs=2,
        loop_pregenerate_enabled=False,
        loop_cache_root=tmp_path / "loop-cache",
        loop_workers=1,
        loop_tier0_quality=82,
        loop_tier0_max_dim=2300,
        loop_tier0_fixed_w=2300,
        loop_tier1_quality=86,
        loop_tier1_max_dim=2400,
        loop_tier1_fixed_w=2400,
    )

    assert publish_promote_snapshots == [
        [0, 1],
        [0, 1, 2],
        [0, 1, 2, 3],
        [0, 1, 2, 3, 4],
    ]


def test_enforce_herbie_cache_retention_keeps_latest_four_runs(tmp_path: Path) -> None:
    herbie_root = tmp_path / "herbie_cache"
    model_root = herbie_root / "hrrr"
    kept = {
        "20260227_18z",
        "20260227_12z",
        "20260227_06z",
        "20260227_00z",
    }
    removed = {
        "20260226_18z",
        "20260226_12z",
    }

    files = {
        "20260227_18z": [
            model_root / "20260227" / "hrrr.t18z.wrfsfcf00.grib2",
            model_root / "20260227" / "subset_deadbeef__hrrr.t18z.wrfsfcf00.grib2",
            model_root / "20260227" / "subset_deadbeef__hrrr.t18z.wrfsfcf00.grib2.lock",
        ],
        "20260227_12z": [model_root / "20260227" / "hrrr.t12z.wrfsfcf00.grib2"],
        "20260227_06z": [model_root / "20260227" / "hrrr.t06z.wrfsfcf00.grib2"],
        "20260227_00z": [model_root / "20260227" / "hrrr.t00z.wrfsfcf00.grib2"],
        "20260226_18z": [model_root / "20260226" / "hrrr.t18z.wrfsfcf00.grib2"],
        "20260226_12z": [model_root / "20260226" / "subset_badcafe__hrrr.t12z.wrfsfcf00.grib2"],
    }
    for paths in files.values():
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")

    scheduler_module._enforce_herbie_cache_retention(herbie_root, "hrrr", 4)

    for run_id in kept:
        for path in files[run_id]:
            assert path.exists()
    for run_id in removed:
        for path in files[run_id]:
            assert not path.exists()
    assert not (model_root / "20260226").exists()


def test_enforce_herbie_cache_retention_preserves_unparsed_files(tmp_path: Path) -> None:
    herbie_root = tmp_path / "herbie_cache"
    model_root = herbie_root / "gfs"
    legacy_note = model_root / "20260226" / "README.txt"
    legacy_note.parent.mkdir(parents=True, exist_ok=True)
    legacy_note.write_text("keep me")

    run_ids = [
        "20260227_18z",
        "20260227_12z",
        "20260227_06z",
        "20260227_00z",
        "20260226_18z",
    ]
    for run_id in run_ids:
        day, hour = run_id.split("_")
        path = model_root / day / f"gfs.t{hour[:2]}z.pgrb2.0p25.f000"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(run_id)

    scheduler_module._enforce_herbie_cache_retention(herbie_root, "gfs", 4)

    assert legacy_note.exists()


def test_promote_run_merges_existing_published_vars(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    model = "gfs"
    run_id = "20260406_12z"
    published_tmp2m = data_root / "published" / model / run_id / "tmp2m"
    staging_mlcape = data_root / "staging" / model / run_id / "mlcape"

    published_tmp2m.mkdir(parents=True, exist_ok=True)
    staging_mlcape.mkdir(parents=True, exist_ok=True)
    (published_tmp2m / "fh000.json").write_text("published tmp2m")
    (staging_mlcape / "fh000.json").write_text("staged mlcape")

    scheduler_module._promote_run(data_root, model, run_id)

    published_run = data_root / "published" / model / run_id
    assert (published_run / "tmp2m" / "fh000.json").read_text() == "published tmp2m"
    assert (published_run / "mlcape" / "fh000.json").read_text() == "staged mlcape"


def test_promote_run_tolerates_same_inode_recopy_during_progress_publish(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    model = "hrrr"
    run_id = "20260407_16z"
    stage_tmp2m = data_root / "staging" / model / run_id / "tmp2m"
    stage_mlcape = data_root / "staging" / model / run_id / "mlcape"

    stage_tmp2m.mkdir(parents=True, exist_ok=True)
    (stage_tmp2m / "fh014.json").write_text("stage tmp2m")

    scheduler_module._promote_run(data_root, model, run_id)

    published_file = data_root / "published" / model / run_id / "tmp2m" / "fh014.json"
    stage_file = stage_tmp2m / "fh014.json"
    assert published_file.stat().st_ino == stage_file.stat().st_ino

    stage_mlcape.mkdir(parents=True, exist_ok=True)
    (stage_mlcape / "fh014.json").write_text("stage mlcape")

    scheduler_module._promote_run(data_root, model, run_id)

    published_run = data_root / "published" / model / run_id
    assert (published_run / "tmp2m" / "fh014.json").read_text() == "stage tmp2m"
    assert (published_run / "mlcape" / "fh014.json").read_text() == "stage mlcape"


def test_scheduler_model_lock_blocks_second_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_fcntl = types.SimpleNamespace(
        LOCK_EX=1,
        LOCK_NB=2,
        LOCK_UN=8,
    )
    calls: list[int] = []

    def fake_flock(fd: int, operation: int) -> None:
        del fd
        if operation == (fake_fcntl.LOCK_EX | fake_fcntl.LOCK_NB):
            calls.append(operation)
            raise BlockingIOError()

    fake_fcntl.flock = fake_flock
    monkeypatch.setitem(sys.modules, "fcntl", fake_fcntl)

    with pytest.raises(scheduler_module.SchedulerConfigError, match="Another scheduler is already running"):
        with scheduler_module._scheduler_model_lock(tmp_path, "nam"):
            pytest.fail("lock acquisition should have failed")


def test_scheduler_model_lock_allows_single_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_fcntl = types.SimpleNamespace(
        LOCK_EX=1,
        LOCK_NB=2,
        LOCK_UN=8,
    )
    operations: list[int] = []

    def fake_flock(fd: int, operation: int) -> None:
        del fd
        operations.append(operation)

    fake_fcntl.flock = fake_flock
    monkeypatch.setitem(sys.modules, "fcntl", fake_fcntl)

    with scheduler_module._scheduler_model_lock(tmp_path, "nam"):
        assert (tmp_path / ".locks" / "nam.scheduler.lock").is_file()

    assert operations == [fake_fcntl.LOCK_EX | fake_fcntl.LOCK_NB, fake_fcntl.LOCK_UN]


def test_write_run_manifest_preserves_existing_vars_for_subset_update(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    model = "gfs"
    run_id = "20260406_12z"
    manifest_path = data_root / "manifests" / model / f"{run_id}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "contract_version": "3.0",
                "model": model,
                "run": run_id,
                "variables": {
                    "tmp2m": {
                        "display_name": "Surface Temp",
                        "kind": "continuous",
                        "units": "F",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [{"fh": 0, "valid_time": "2026-04-06T12:00:00Z"}],
                    }
                },
            }
        )
    )

    mlcape_stage = data_root / "staging" / model / run_id / "mlcape"
    mlcape_stage.mkdir(parents=True, exist_ok=True)
    (mlcape_stage / "fh000.json").write_text(
        json.dumps({"fh": 0, "units": "J/kg", "kind": "continuous", "valid_time": "2026-04-06T12:00:00Z"})
    )

    scheduler_module._write_run_manifest(
        data_root=data_root,
        model=model,
        run_id=run_id,
        targets=[("mlcape", 0)],
        plugin=None,
    )

    payload = json.loads(manifest_path.read_text())
    assert set(payload["variables"].keys()) == {"tmp2m", "mlcape"}
    assert payload["variables"]["tmp2m"]["units"] == "F"
    assert payload["variables"]["mlcape"]["units"] == "J/kg"
    assert payload["variables"]["mlcape"]["available_frames"] == 1


def test_process_run_skips_loop_pregen_for_incomplete_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dt = datetime(2026, 2, 27, 12, tzinfo=timezone.utc)
    model_id = "hrrr"

    built: set[tuple[str, int]] = set()
    available_up_to = {"tmp2m": 2}
    def fake_frame_artifacts_exist(
        data_root: Path,
        model: str,
        run: str,
        var_id: str,
        fh: int,
    ) -> bool:
        del data_root, model, run
        return (var_id, fh) in built

    def fake_build_one(
        *,
        model_id: str,
        var_id: str,
        fh: int,
        run_dt: datetime,
        data_root: Path,
        plugin: object,
    ) -> tuple[str, int, bool]:
        del model_id, run_dt, data_root, plugin
        ok = fh <= available_up_to[var_id]
        if ok:
            built.add((var_id, fh))
        return var_id, fh, ok

    def fake_should_promote(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        return ("tmp2m", 2) in built

    monkeypatch.setattr(scheduler_module, "_frame_artifacts_exist", fake_frame_artifacts_exist)
    monkeypatch.setattr(scheduler_module, "_build_one", fake_build_one)
    monkeypatch.setattr(scheduler_module, "_should_promote", fake_should_promote)
    monkeypatch.setattr(scheduler_module, "_promote_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler_module, "_write_run_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler_module, "_write_latest_pointer", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler_module, "_enforce_run_retention", lambda *args, **kwargs: None)

    scheduler_module._process_run(
        plugin=_FakePlugin(),
        model_id=model_id,
        vars_to_build=["tmp2m"],
        primary_vars=["tmp2m"],
        run_dt=run_dt,
        data_root=tmp_path,
        workers=1,
        keep_runs=2,
        loop_pregenerate_enabled=True,
        loop_cache_root=tmp_path / "loop-cache",
        loop_workers=1,
        loop_tier0_quality=82,
        loop_tier0_max_dim=2300,
        loop_tier0_fixed_w=2300,
        loop_tier1_quality=86,
        loop_tier1_max_dim=2400,
        loop_tier1_fixed_w=2400,
    )

    assert built == {("tmp2m", 0), ("tmp2m", 1), ("tmp2m", 2)}


def test_process_run_does_not_pregenerate_loop_cache_when_run_is_complete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dt = datetime(2026, 2, 27, 12, tzinfo=timezone.utc)
    model_id = "hrrr"

    built: set[tuple[str, int]] = set()
    available_up_to = {"tmp2m": 4}
    def fake_frame_artifacts_exist(
        data_root: Path,
        model: str,
        run: str,
        var_id: str,
        fh: int,
    ) -> bool:
        del data_root, model, run
        return (var_id, fh) in built

    def fake_build_one(
        *,
        model_id: str,
        var_id: str,
        fh: int,
        run_dt: datetime,
        data_root: Path,
        plugin: object,
    ) -> tuple[str, int, bool]:
        del model_id, run_dt, data_root, plugin
        ok = fh <= available_up_to[var_id]
        if ok:
            built.add((var_id, fh))
        return var_id, fh, ok

    def fake_should_promote(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        return ("tmp2m", 2) in built

    monkeypatch.setattr(scheduler_module, "_frame_artifacts_exist", fake_frame_artifacts_exist)
    monkeypatch.setattr(scheduler_module, "_build_one", fake_build_one)
    monkeypatch.setattr(scheduler_module, "_should_promote", fake_should_promote)
    monkeypatch.setattr(scheduler_module, "_promote_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler_module, "_write_run_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler_module, "_write_latest_pointer", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler_module, "_enforce_run_retention", lambda *args, **kwargs: None)

    scheduler_module._process_run(
        plugin=_FakePlugin(),
        model_id=model_id,
        vars_to_build=["tmp2m"],
        primary_vars=["tmp2m"],
        run_dt=run_dt,
        data_root=tmp_path,
        workers=1,
        keep_runs=2,
        loop_pregenerate_enabled=True,
        loop_cache_root=tmp_path / "loop-cache",
        loop_workers=1,
        loop_tier0_quality=82,
        loop_tier0_max_dim=2300,
        loop_tier0_fixed_w=2300,
        loop_tier1_quality=86,
        loop_tier1_max_dim=2400,
        loop_tier1_fixed_w=2400,
    )

    assert built == {("tmp2m", 0), ("tmp2m", 1), ("tmp2m", 2), ("tmp2m", 3), ("tmp2m", 4)}


def _write_sidecar(tmp_path: Path, run_id: str, var_id: str, fh: int, *, quality: str, quality_flags: list[str]) -> None:
    sidecar = tmp_path / "staging" / "hrrr" / run_id / var_id / f"fh{fh:03d}.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        scheduler_module.json.dumps(
            {
                "contract_version": "3.0",
                "model": "hrrr",
                "run": run_id,
                "var": var_id,
                "fh": fh,
                "quality": quality,
                "quality_flags": quality_flags,
            }
        )
    )


class _FakeKucheraPlugin:
    id = "hrrr"

    def normalize_var_id(self, var_id: str) -> str:
        return str(var_id)

    def scheduled_fhs_for_var(self, var_key: str, cycle_hour: int) -> list[int]:
        del var_key, cycle_hour
        return [0, 1]


def test_process_run_requeues_only_slr_fallback_degraded_frames(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dt = datetime(2026, 3, 5, 17, tzinfo=timezone.utc)
    run_id = scheduler_module._run_id_from_dt(run_dt)

    _write_sidecar(
        tmp_path,
        run_id,
        "snowfall_kuchera_total",
        0,
        quality="degraded",
        quality_flags=["apcp_cumulative_fallback"],
    )
    _write_sidecar(
        tmp_path,
        run_id,
        "snowfall_kuchera_total",
        1,
        quality="degraded",
        quality_flags=["slr_fallback_10to1"],
    )

    attempted: list[tuple[str, int]] = []

    def fake_build_one(
        *,
        model_id: str,
        var_id: str,
        fh: int,
        run_dt: datetime,
        data_root: Path,
        plugin: object,
    ) -> tuple[str, int, bool]:
        del model_id, run_dt, plugin
        attempted.append((var_id, fh))
        _write_sidecar(
            data_root,
            run_id,
            var_id,
            fh,
            quality="full",
            quality_flags=[],
        )
        return var_id, fh, True

    monkeypatch.setattr(scheduler_module, "_frame_artifacts_exist", lambda *args, **kwargs: True)
    monkeypatch.setattr(scheduler_module, "_build_one", fake_build_one)
    monkeypatch.setattr(scheduler_module, "_should_promote", lambda *args, **kwargs: False)
    monkeypatch.setattr(scheduler_module, "_enforce_run_retention", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler_module, "_kuchera_rebuild_profile_ready", lambda **kwargs: True)
    monkeypatch.setattr(scheduler_module, "_run_is_superseded", lambda **kwargs: False)

    scheduler_module._process_run(
        plugin=_FakeKucheraPlugin(),
        model_id="hrrr",
        vars_to_build=["snowfall_kuchera_total"],
        primary_vars=["snowfall_kuchera_total"],
        run_dt=run_dt,
        data_root=tmp_path,
        workers=1,
        keep_runs=2,
        loop_pregenerate_enabled=False,
        loop_cache_root=tmp_path / "loop-cache",
        loop_workers=1,
        loop_tier0_quality=82,
        loop_tier0_max_dim=2300,
        loop_tier0_fixed_w=2300,
        loop_tier1_quality=86,
        loop_tier1_max_dim=2400,
        loop_tier1_fixed_w=2400,
    )

    assert attempted == [("snowfall_kuchera_total", 1)]


def test_process_run_caps_degraded_rebuild_attempts_at_two(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dt = datetime(2026, 3, 5, 17, tzinfo=timezone.utc)
    run_id = scheduler_module._run_id_from_dt(run_dt)

    _write_sidecar(
        tmp_path,
        run_id,
        "snowfall_kuchera_total",
        1,
        quality="degraded",
        quality_flags=["slr_fallback_10to1"],
    )

    attempted: list[tuple[str, int]] = []

    def fake_build_one(
        *,
        model_id: str,
        var_id: str,
        fh: int,
        run_dt: datetime,
        data_root: Path,
        plugin: object,
    ) -> tuple[str, int, bool]:
        del model_id, run_dt, data_root, plugin
        attempted.append((var_id, fh))
        return var_id, fh, False

    monkeypatch.setattr(scheduler_module, "_frame_artifacts_exist", lambda *args, **kwargs: True)
    monkeypatch.setattr(scheduler_module, "_build_one", fake_build_one)
    monkeypatch.setattr(scheduler_module, "_should_promote", lambda *args, **kwargs: False)
    monkeypatch.setattr(scheduler_module, "_enforce_run_retention", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler_module, "_kuchera_rebuild_profile_ready", lambda **kwargs: True)
    monkeypatch.setattr(scheduler_module, "_run_is_superseded", lambda **kwargs: False)

    scheduler_module._process_run(
        plugin=_FakeKucheraPlugin(),
        model_id="hrrr",
        vars_to_build=["snowfall_kuchera_total"],
        primary_vars=["snowfall_kuchera_total"],
        run_dt=run_dt,
        data_root=tmp_path,
        workers=1,
        keep_runs=2,
        loop_pregenerate_enabled=False,
        loop_cache_root=tmp_path / "loop-cache",
        loop_workers=1,
        loop_tier0_quality=82,
        loop_tier0_max_dim=2300,
        loop_tier0_fixed_w=2300,
        loop_tier1_quality=86,
        loop_tier1_max_dim=2400,
        loop_tier1_fixed_w=2400,
    )

    assert attempted == [
        ("snowfall_kuchera_total", 1),
        ("snowfall_kuchera_total", 1),
    ]


def test_process_run_abandons_rebuilds_when_superseded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dt = datetime(2026, 3, 5, 17, tzinfo=timezone.utc)
    run_id = scheduler_module._run_id_from_dt(run_dt)

    _write_sidecar(
        tmp_path,
        run_id,
        "snowfall_kuchera_total",
        1,
        quality="degraded",
        quality_flags=["slr_fallback_10to1"],
    )

    attempted: list[tuple[str, int]] = []

    def fake_build_one(
        *,
        model_id: str,
        var_id: str,
        fh: int,
        run_dt: datetime,
        data_root: Path,
        plugin: object,
    ) -> tuple[str, int, bool]:
        del model_id, run_dt, data_root, plugin
        attempted.append((var_id, fh))
        return var_id, fh, True

    monkeypatch.setattr(scheduler_module, "_frame_artifacts_exist", lambda *args, **kwargs: True)
    monkeypatch.setattr(scheduler_module, "_build_one", fake_build_one)
    monkeypatch.setattr(scheduler_module, "_should_promote", lambda *args, **kwargs: False)
    monkeypatch.setattr(scheduler_module, "_enforce_run_retention", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler_module, "_kuchera_rebuild_profile_ready", lambda **kwargs: True)
    monkeypatch.setattr(scheduler_module, "_run_is_superseded", lambda **kwargs: True)

    scheduler_module._process_run(
        plugin=_FakeKucheraPlugin(),
        model_id="hrrr",
        vars_to_build=["snowfall_kuchera_total"],
        primary_vars=["snowfall_kuchera_total"],
        run_dt=run_dt,
        data_root=tmp_path,
        workers=1,
        keep_runs=2,
        loop_pregenerate_enabled=False,
        loop_cache_root=tmp_path / "loop-cache",
        loop_workers=1,
        loop_tier0_quality=82,
        loop_tier0_max_dim=2300,
        loop_tier0_fixed_w=2300,
        loop_tier1_quality=86,
        loop_tier1_max_dim=2400,
        loop_tier1_fixed_w=2400,
    )

    assert attempted == []
