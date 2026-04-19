import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_test_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
os.environ.setdefault("TWM_ADMIN_MEMBER_IDS", "42")

from app import main as main_module
from app.services.grid import grid_supported

twf_oauth = main_module.twf_oauth
admin_telemetry = main_module.admin_telemetry

pytestmark = pytest.mark.anyio


def _create_session(*, session_id: str, member_id: int, name: str) -> None:
    twf_oauth.upsert_session(
        twf_oauth.TwfSession(
            session_id=session_id,
            member_id=member_id,
            display_name=name,
            photo_url=None,
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=2_000_000_000,
        )
    )


def _write_value_grid(path: Path, data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype="float32",
        transform=from_origin(0, float(data.shape[0]), 1.0, 1.0),
        crs="EPSG:3857",
    ) as dataset:
        dataset.write(data.astype("float32"), 1)


def _write_sidecar(path: Path, *, model_id: str, variable_id: str, run_id: str, forecast_hour: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "contract_version": "3.0",
                "model": model_id,
                "run": run_id,
                "var": variable_id,
                "fh": forecast_hour,
                "units": "in",
                "kind": "continuous",
                "min": 0.0,
                "max": 1.0,
            }
        )
    )


def _write_grid_runtime(
    root: Path,
    *,
    model_id: str,
    run_id: str,
    variable_id: str,
    hours: list[int],
    include_contours: bool = False,
) -> None:
    grid_dir = root / "published" / model_id / run_id / variable_id / "grid"
    grid_dir.mkdir(parents=True, exist_ok=True)
    lod_frames = []
    for forecast_hour in hours:
        filename = f"fh{forecast_hour:03d}.l0.u16.bin"
        (grid_dir / filename).write_bytes((b"\x00\x00") * 4)
        lod_frames.append({"fh": forecast_hour, "file": filename})
        if include_contours:
            sidecar_path = root / "published" / model_id / run_id / variable_id / f"fh{forecast_hour:03d}.json"
            sidecar_path.parent.mkdir(parents=True, exist_ok=True)
            sidecar_path.write_text(
                json.dumps(
                    {
                        "contract_version": "3.0",
                        "model": model_id,
                        "run": run_id,
                        "var": variable_id,
                        "fh": forecast_hour,
                        "contours": {
                            "primary": {
                                "path": f"contours/fh{forecast_hour:03d}_primary.geojson",
                            }
                        },
                    }
                )
            )
            contour_path = sidecar_path.parent / "contours" / f"fh{forecast_hour:03d}_primary.geojson"
            contour_path.parent.mkdir(parents=True, exist_ok=True)
            contour_path.write_text(json.dumps({"type": "FeatureCollection", "features": []}))

    manifest_payload = {
        "manifest_version": "1.0",
        "subtype": "grid",
        "model": model_id,
        "run": run_id,
        "var": variable_id,
        "bbox": [0.0, 0.0, 2.0, 2.0],
        "grid": {
            "width": 2,
            "height": 2,
            "dtype": "uint16",
            "endianness": "little",
            "scale": 1.0,
            "offset": 0.0,
            "nodata": 65535,
            "units": "in",
        },
        "lods": [
            {
                "level": 0,
                "width": 2,
                "height": 2,
                "frames": lod_frames,
            }
        ],
    }
    if include_contours:
        manifest_payload["contours"] = {"primary": {"label": "Primary"}}
    (grid_dir / "manifest.json").write_text(json.dumps(manifest_payload))


def _write_manifest(path: Path, *, model_id: str, run_id: str, variables: dict[str, list[int]], available_override: dict[str, int] | None = None) -> None:
    payload = {
        "contract_version": "3.0",
        "model": model_id,
        "run": run_id,
        "last_updated": "2026-04-17T18:00:00Z",
        "variables": {},
    }
    for variable_id, hours in variables.items():
        available = len(hours)
        if available_override and variable_id in available_override:
            available = int(available_override[variable_id])
        payload["variables"][variable_id] = {
            "display_name": variable_id,
            "kind": "continuous",
            "units": "in",
            "expected_frames": len(hours),
            "available_frames": available,
            "frames": [{"fh": forecast_hour} for forecast_hour in hours[:available]],
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _seed_run(root: Path, *, model_id: str, run_id: str, variables: dict[str, list[int]], available_override: dict[str, int] | None = None, missing_value_grid: tuple[str, int] | None = None) -> None:
    _write_manifest(
        root / "manifests" / model_id / f"{run_id}.json",
        model_id=model_id,
        run_id=run_id,
        variables=variables,
        available_override=available_override,
    )
    for variable_id, hours in variables.items():
        available = available_override.get(variable_id, len(hours)) if available_override else len(hours)
        if grid_supported(model_id, variable_id):
            _write_grid_runtime(
                root,
                model_id=model_id,
                run_id=run_id,
                variable_id=variable_id,
                hours=hours[:available],
            )
        for forecast_hour in hours[:available]:
            value_path = root / "published" / model_id / run_id / variable_id / f"fh{forecast_hour:03d}.val.cog.tif"
            sidecar_path = root / "published" / model_id / run_id / variable_id / f"fh{forecast_hour:03d}.json"
            if missing_value_grid == (variable_id, forecast_hour):
                _write_sidecar(sidecar_path, model_id=model_id, variable_id=variable_id, run_id=run_id, forecast_hour=forecast_hour)
                continue
            _write_value_grid(value_path, np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32))
            _write_sidecar(sidecar_path, model_id=model_id, variable_id=variable_id, run_id=run_id, forecast_hour=forecast_hour)


@pytest.fixture(autouse=True)
def isolate_environment(tmp_path: Path) -> None:
    token_db = tmp_path / "tokens.sqlite3"
    telemetry_db = tmp_path / "telemetry.sqlite3"
    data_root = tmp_path / "data"

    twf_oauth.TOKEN_DB_PATH = str(token_db)
    admin_telemetry.TELEMETRY_DB_PATH = telemetry_db
    admin_telemetry._db_initialized = False
    admin_telemetry.clear_operational_status_cache()

    main_module.DATA_ROOT = data_root
    main_module.PUBLISHED_ROOT = data_root / "published"
    main_module.MANIFESTS_ROOT = data_root / "manifests"
    main_module.ADMIN_MEMBER_IDS = {42}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


async def test_status_results_reports_incomplete_and_artifact_failures(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    real_datetime = admin_telemetry.datetime

    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return real_datetime(2026, 3, 11, 14, 0, tzinfo=tz)

    monkeypatch.setattr(admin_telemetry, "datetime", FrozenDateTime)

    _seed_run(
        main_module.DATA_ROOT,
        model_id="hrrr",
        run_id="20260311_13z",
        variables={"tmp2m": [0, 1], "precip_total": [0, 1]},
        available_override={"precip_total": 1},
    )
    _write_manifest(
        main_module.DATA_ROOT / "manifests" / "spc" / "20260311_1200z.json",
        model_id="spc",
        run_id="20260311_1200z",
        variables={"convective": [0]},
    )
    spc_var_dir = main_module.DATA_ROOT / "published" / "spc" / "20260311_1200z" / "convective"
    spc_var_dir.mkdir(parents=True, exist_ok=True)
    (spc_var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "contract_version": "3.0",
                "model": "spc",
                "run": "20260311_1200z",
                "var": "convective",
                "fh": 0,
                "vector_layers": {
                    "primary": {
                        "format": "geojson",
                        "path": "vectors/fh000.geojson",
                    }
                },
            }
        )
    )

    response = await client.get(
        "/api/v4/admin/status/results?window=30d&include_details=true",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert response.status_code == 200
    rows = response.json()["results"]
    assert any(row["issue_type"] == "run_incomplete" and row["model_id"] == "hrrr" for row in rows)
    artifact_row = next(row for row in rows if row["issue_type"] == "artifact_failure")
    assert artifact_row["model_id"] == "spc"
    assert artifact_row["missing_artifact_count"] >= 1
    assert artifact_row["sample_paths"]


async def test_status_results_treats_grid_runtime_artifacts_as_healthy_without_legacy_value_files(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    real_datetime = admin_telemetry.datetime

    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return real_datetime(2026, 4, 17, 14, 0, tzinfo=tz)

    monkeypatch.setattr(admin_telemetry, "datetime", FrozenDateTime)

    _write_manifest(
        main_module.DATA_ROOT / "manifests" / "gefs" / "20260417_12z.json",
        model_id="gefs",
        run_id="20260417_12z",
        variables={"tmp2m": [0, 6]},
    )
    _write_grid_runtime(
        main_module.DATA_ROOT,
        model_id="gefs",
        run_id="20260417_12z",
        variable_id="tmp2m__mean",
        hours=[0, 6],
    )

    response = await client.get(
        "/api/v4/admin/status/results?window=30d&model=gefs",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert response.status_code == 200
    rows = response.json()["results"]
    assert len(rows) == 1
    assert rows[0]["status"] == "healthy"
    assert rows[0]["issue_type"] == "healthy"
    assert rows[0]["missing_artifact_count"] == 0
    assert rows[0]["unreadable_artifact_count"] == 0
    assert rows[0]["sample_paths"] == []


async def test_status_results_reuses_cached_operational_scan(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    scan_calls: list[str] = []

    def fake_published_run_ids(data_root: Path, model_id: str, *, keep_runs: int) -> list[str]:
        assert data_root == main_module.DATA_ROOT
        assert model_id == "hrrr"
        assert keep_runs == admin_telemetry.STATUS_KEEP_RUNS_PER_MODEL
        return ["20260311_13z"]

    def fake_scan_run_issue(*, data_root: Path, model_id: str, run_id: str, latest_run_id: str | None, include_details: bool = True) -> dict[str, object]:
        scan_calls.append(f"{model_id}:{run_id}")
        assert data_root == main_module.DATA_ROOT
        assert latest_run_id == "20260311_13z"
        assert include_details is False
        return {
            "id": f"{model_id}:{run_id}",
            "model_id": model_id,
            "run_id": run_id,
            "run_timestamp": 1_773_497_600,
            "run_age_hours": 1.0,
            "last_updated_at": 1_773_497_600,
            "status": "healthy",
            "issue_type": "healthy",
            "summary": "Retained published run looks healthy.",
            "expected_frames": 1,
            "available_frames": 1,
            "completion_pct": 100.0,
            "missing_artifact_count": 0,
            "unreadable_artifact_count": 0,
            "incomplete_variable_count": 0,
            "incomplete_variables": [],
            "sample_paths": [],
        }

    monkeypatch.setattr(admin_telemetry, "_published_run_ids", fake_published_run_ids)
    monkeypatch.setattr(admin_telemetry, "_scan_run_issue", fake_scan_run_issue)

    first_response = await client.get(
        "/api/v4/admin/status/results?window=30d&model=hrrr",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )
    second_response = await client.get(
        "/api/v4/admin/status/results?window=30d&model=hrrr",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert len(scan_calls) == 1
    assert first_response.json()["results"] == second_response.json()["results"]


async def test_status_results_skips_irrelevant_sidecar_parsing_for_grid_runtime(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    real_datetime = admin_telemetry.datetime

    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return real_datetime(2026, 4, 17, 14, 0, tzinfo=tz)

    monkeypatch.setattr(admin_telemetry, "datetime", FrozenDateTime)

    _write_manifest(
        main_module.DATA_ROOT / "manifests" / "gefs" / "20260417_12z.json",
        model_id="gefs",
        run_id="20260417_12z",
        variables={"tmp2m": [0, 6]},
    )
    _write_grid_runtime(
        main_module.DATA_ROOT,
        model_id="gefs",
        run_id="20260417_12z",
        variable_id="tmp2m__mean",
        hours=[0, 6],
    )
    for forecast_hour in [0, 6]:
        _write_sidecar(
            main_module.DATA_ROOT / "published" / "gefs" / "20260417_12z" / "tmp2m__mean" / f"fh{forecast_hour:03d}.json",
            model_id="gefs",
            variable_id="tmp2m__mean",
            run_id="20260417_12z",
            forecast_hour=forecast_hour,
        )

    real_load_json_file = admin_telemetry._load_json_file
    loaded_paths: list[str] = []

    def tracking_load_json_file(path: Path):
        loaded_paths.append(str(path))
        return real_load_json_file(path)

    monkeypatch.setattr(admin_telemetry, "_load_json_file", tracking_load_json_file)

    response = await client.get(
        "/api/v4/admin/status/results?window=30d&model=gefs",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert response.status_code == 200
    rows = response.json()["results"]
    assert len(rows) == 1
    assert rows[0]["status"] == "healthy"
    sidecar_reads = [path for path in loaded_paths if "/published/gefs/20260417_12z/tmp2m__mean/fh" in path]
    assert sidecar_reads == []


async def test_status_run_detail_returns_full_diagnostics(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    real_datetime = admin_telemetry.datetime

    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return real_datetime(2026, 3, 11, 14, 0, tzinfo=tz)

    monkeypatch.setattr(admin_telemetry, "datetime", FrozenDateTime)

    _write_manifest(
        main_module.DATA_ROOT / "manifests" / "spc" / "20260311_1200z.json",
        model_id="spc",
        run_id="20260311_1200z",
        variables={"convective": [0]},
    )
    spc_var_dir = main_module.DATA_ROOT / "published" / "spc" / "20260311_1200z" / "convective"
    spc_var_dir.mkdir(parents=True, exist_ok=True)
    (spc_var_dir / "fh000.json").write_text(
        json.dumps(
            {
                "contract_version": "3.0",
                "model": "spc",
                "run": "20260311_1200z",
                "var": "convective",
                "fh": 0,
                "vector_layers": {
                    "primary": {
                        "format": "geojson",
                        "path": "vectors/fh000.geojson",
                    }
                },
            }
        )
    )

    response = await client.get(
        "/api/v4/admin/status/run?model=spc&run=20260311_1200z",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["issue_type"] == "artifact_failure"
    assert result["sample_paths"]


async def test_status_results_only_scans_retained_published_runs(client: httpx.AsyncClient) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")
    run_ids = [
        "20260310_00z",
        "20260310_06z",
        "20260310_12z",
        "20260310_18z",
        "20260311_00z",
    ]
    for run_id in run_ids:
        _seed_run(
            main_module.DATA_ROOT,
            model_id="gfs",
            run_id=run_id,
            variables={"tmp2m": [0]},
        )

    response = await client.get(
        "/api/v4/admin/status/results?window=30d&model=gfs",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert response.status_code == 200
    rows = response.json()["results"]
    returned_runs = [row["run_id"] for row in rows]
    assert "20260310_00z" not in returned_runs
    assert set(returned_runs) == {"20260311_00z", "20260310_18z", "20260310_12z", "20260310_06z"}
    assert len(returned_runs) == 4


async def test_status_results_flags_stale_latest_run(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")
    _seed_run(
        main_module.DATA_ROOT,
        model_id="hrrr",
        run_id="20260311_08z",
        variables={"tmp2m": [0]},
    )

    real_datetime = admin_telemetry.datetime

    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return real_datetime(2026, 3, 11, 15, 0, tzinfo=tz)

    monkeypatch.setattr(admin_telemetry, "datetime", FrozenDateTime)

    response = await client.get(
        "/api/v4/admin/status/results?window=30d&model=hrrr",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert response.status_code == 200
    rows = response.json()["results"]
    assert rows[0]["issue_type"] == "stale_run"
    assert rows[0]["status"] == "warning"


async def test_status_results_treats_vector_only_spc_run_as_valid_bundle(client: httpx.AsyncClient) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    model_id = "spc"
    run_id = "20260401_0630z"
    manifest_path = main_module.DATA_ROOT / "manifests" / model_id / f"{run_id}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "contract_version": "3.0",
                "model": model_id,
                "run": run_id,
                "variables": {
                    "convective": {
                        "display_name": "SPC Convective Outlook",
                        "kind": "categorical",
                        "units": "",
                        "expected_frames": 3,
                        "available_frames": 3,
                        "frames": [
                            {"fh": 0, "valid_time": "2026-04-01T12:00:00Z"},
                            {"fh": 1, "valid_time": "2026-04-02T12:00:00Z"},
                            {"fh": 2, "valid_time": "2026-04-03T12:00:00Z"},
                        ],
                    }
                },
            }
        )
    )

    latest_path = main_module.DATA_ROOT / "published" / model_id / "LATEST.json"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps({"run_id": run_id}))

    var_dir = main_module.DATA_ROOT / "published" / model_id / run_id / "convective"
    (var_dir / "vectors").mkdir(parents=True, exist_ok=True)
    for fh in range(3):
        (var_dir / f"fh{fh:03d}.json").write_text(
            json.dumps(
                {
                    "contract_version": "3.0",
                    "model": model_id,
                    "run": run_id,
                    "var": "convective",
                    "fh": fh,
                    "kind": "categorical",
                    "valid_time": f"2026-04-0{fh + 1}T12:00:00Z",
                    "vector_layers": {
                        "primary": {
                            "format": "geojson",
                            "path": f"vectors/fh{fh:03d}.geojson",
                        }
                    },
                }
            )
        )
        (var_dir / "vectors" / f"fh{fh:03d}.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": []})
        )

    response = await client.get(
        "/api/v4/admin/status/results?window=30d&model=spc",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert response.status_code == 200
    rows = response.json()["results"]
    assert len(rows) == 1
    assert rows[0]["model_id"] == "spc"
    assert rows[0]["time_axis_mode"] == "valid"
    assert rows[0]["status"] == "healthy"
    assert rows[0]["missing_artifact_count"] == 0


async def test_status_results_requires_admin(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v4/admin/status/results?window=30d")
    assert response.status_code == 401
