import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

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

twf_oauth = main_module.twf_oauth
admin_telemetry = main_module.admin_telemetry

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def isolate_databases(tmp_path: Path) -> None:
    token_db = tmp_path / "tokens.sqlite3"
    telemetry_db = tmp_path / "telemetry.sqlite3"
    twf_oauth.TOKEN_DB_PATH = str(token_db)
    admin_telemetry.TELEMETRY_DB_PATH = telemetry_db
    admin_telemetry._db_initialized = False


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


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


async def test_perf_telemetry_ingest_and_admin_summary(client: httpx.AsyncClient) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    response = await client.post(
        "/api/v4/telemetry/perf",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
        json={
            "event_name": "frame_change",
            "duration_ms": 186.4,
            "session_id": "viewer-session-1",
            "model_id": "hrrr",
            "variable_id": "tmp2m",
            "run_id": "20260308_00z",
            "region_id": "conus",
            "forecast_hour": 18,
            "device_type": "desktop",
            "viewport_bucket": "xl",
            "page": "/viewer",
            "meta": {"source": "slider"},
        },
    )

    assert response.status_code == 204

    summary = await client.get(
        "/api/v4/admin/performance/summary?window=7d",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert summary.status_code == 200
    body = summary.json()
    assert body["metrics"]["frame_change"]["count"] == 1
    assert body["metrics"]["frame_change"]["p95_ms"] == 186.4
    assert body["metrics"]["frame_change"]["target_ms"] == 250.0


async def test_perf_telemetry_summary_supports_phase1_loop_metrics(client: httpx.AsyncClient) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    payloads = [
        {
            "event_name": "loop_decode_to_commit",
            "duration_ms": 42.0,
            "session_id": "viewer-session-1",
            "model_id": "hrrr",
            "variable_id": "tmp2m",
            "run_id": "20260308_00z",
            "region_id": "conus",
            "forecast_hour": 3,
            "device_type": "desktop",
            "viewport_bucket": "xl",
            "page": "/viewer",
        },
        {
            "event_name": "loop_commit_to_visible",
            "duration_ms": 18.0,
            "session_id": "viewer-session-1",
            "model_id": "hrrr",
            "variable_id": "tmp2m",
            "run_id": "20260308_00z",
            "region_id": "conus",
            "forecast_hour": 3,
            "device_type": "desktop",
            "viewport_bucket": "xl",
            "page": "/viewer",
        },
    ]

    for payload in payloads:
        response = await client.post(
            "/api/v4/telemetry/perf",
            cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
            json=payload,
        )
        assert response.status_code == 204

    summary = await client.get(
        "/api/v4/admin/performance/summary?window=7d",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert summary.status_code == 200
    body = summary.json()["metrics"]
    assert body["loop_decode_to_commit"] == {
        "count": 1,
        "avg_ms": 42.0,
        "min_ms": 42.0,
        "max_ms": 42.0,
        "p50_ms": 42.0,
        "p95_ms": 42.0,
        "target_ms": 120.0,
    }
    assert body["loop_commit_to_visible"] == {
        "count": 1,
        "avg_ms": 18.0,
        "min_ms": 18.0,
        "max_ms": 18.0,
        "p50_ms": 18.0,
        "p95_ms": 18.0,
        "target_ms": 80.0,
    }


async def test_admin_perf_summary_requires_admin_membership(client: httpx.AsyncClient) -> None:
    _create_session(session_id="normal-session", member_id=99, name="User")

    response = await client.get(
        "/api/v4/admin/performance/summary?window=7d",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "normal-session"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "TWF_ADMIN_REQUIRED",
            "message": "Admin access required",
        }
    }


async def test_usage_telemetry_summary_returns_counts(client: httpx.AsyncClient) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    response = await client.post(
        "/api/v4/telemetry/usage",
        json={
            "event_name": "model_selected",
            "session_id": "viewer-session-1",
            "model_id": "gfs",
            "variable_id": "tmp2m",
            "run_id": "20260308_00z",
            "region_id": "conus",
            "forecast_hour": 24,
            "device_type": "desktop",
            "viewport_bucket": "xl",
            "page": "/viewer",
        },
    )

    assert response.status_code == 204

    summary = await client.get(
        "/api/v4/admin/usage/summary?window=30d",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert summary.status_code == 200
    assert summary.json()["events"] == [{"event_name": "model_selected", "count": 1}]


async def test_rum_telemetry_ingest_and_admin_overview_summary(client: httpx.AsyncClient) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    payloads = [
        {
            "metric_name": "lcp",
            "metric_value": 2100.0,
            "metric_unit": "ms",
            "sample_rate": 1.0,
            "session_id": "viewer-session-1",
            "model_id": "hrrr",
            "variable_id": "tmp2m",
            "run_id": "20260308_00z",
            "region_id": "conus",
            "forecast_hour": 18,
            "device_type": "desktop",
            "viewport_bucket": "xl",
            "page": "/viewer",
        },
        {
            "metric_name": "lcp",
            "metric_value": 2800.0,
            "metric_unit": "ms",
            "sample_rate": 1.0,
            "session_id": "viewer-session-2",
            "model_id": "hrrr",
            "variable_id": "tmp2m",
            "run_id": "20260308_00z",
            "region_id": "conus",
            "forecast_hour": 19,
            "device_type": "desktop",
            "viewport_bucket": "xl",
            "page": "/viewer",
        },
        {
            "metric_name": "inp",
            "metric_value": 180.0,
            "metric_unit": "ms",
            "sample_rate": 1.0,
            "session_id": "viewer-session-1",
            "page": "/viewer",
        },
        {
            "metric_name": "cls",
            "metric_value": 0.08,
            "metric_unit": "score",
            "sample_rate": 1.0,
            "session_id": "viewer-session-1",
            "page": "/viewer",
        },
        {
            "metric_name": "manifest_fetch_duration",
            "metric_value": 340.0,
            "metric_unit": "ms",
            "sample_rate": 0.1,
            "session_id": "viewer-session-1",
            "page": "/viewer",
        },
    ]

    for payload in payloads:
        response = await client.post(
            "/api/v4/telemetry/rum",
            cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
            json=payload,
        )
        assert response.status_code == 204

    summary = await client.get(
        "/api/v4/admin/overview/summary?window=7d",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert summary.status_code == 200
    body = summary.json()
    assert body["web_vitals"]["lcp"] == {
        "count": 2,
        "unit": "ms",
        "avg": 2450.0,
        "min": 2100.0,
        "max": 2800.0,
        "p50": 2450.0,
        "p75": 2625.0,
        "p95": 2765.0,
        "total_value": 4900.0,
        "good_threshold": 2500.0,
        "needs_improvement_threshold": 4000.0,
    }
    assert body["web_vitals"]["inp"]["p75"] == 180.0
    assert body["web_vitals"]["inp"]["good_threshold"] == 200.0
    assert body["web_vitals"]["cls"]["p75"] == 0.08
    assert body["web_vitals"]["cls"]["good_threshold"] == 0.1
    assert body["rum_diagnostics"]["manifest_fetch_duration"]["count"] == 1
    assert body["rum_diagnostics"]["manifest_fetch_duration"]["p95"] == 340.0


async def test_admin_overview_summary_requires_admin_membership(client: httpx.AsyncClient) -> None:
    _create_session(session_id="normal-session", member_id=99, name="User")

    response = await client.get(
        "/api/v4/admin/overview/summary?window=7d",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "normal-session"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "TWF_ADMIN_REQUIRED",
            "message": "Admin access required",
        }
    }


async def test_admin_perf_breakdown_supports_animation_stall_by_model_and_variable(client: httpx.AsyncClient) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    payloads = [
        {
            "event_name": "animation_stall",
            "duration_ms": 910.0,
            "session_id": "viewer-session-1",
            "model_id": "hrrr",
            "variable_id": "tmp2m",
            "run_id": "20260308_00z",
            "region_id": "conus",
            "forecast_hour": 1,
            "device_type": "desktop",
            "viewport_bucket": "xl",
            "page": "/viewer",
        },
        {
            "event_name": "animation_stall",
            "duration_ms": 980.0,
            "session_id": "viewer-session-2",
            "model_id": "hrrr",
            "variable_id": "tmp2m",
            "run_id": "20260308_00z",
            "region_id": "conus",
            "forecast_hour": 2,
            "device_type": "desktop",
            "viewport_bucket": "xl",
            "page": "/viewer",
        },
        {
            "event_name": "animation_stall",
            "duration_ms": 840.0,
            "session_id": "viewer-session-3",
            "model_id": "gfs",
            "variable_id": "apcp",
            "run_id": "20260308_00z",
            "region_id": "conus",
            "forecast_hour": 3,
            "device_type": "desktop",
            "viewport_bucket": "xl",
            "page": "/viewer",
        },
    ]

    for payload in payloads:
        response = await client.post(
            "/api/v4/telemetry/perf",
            cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
            json=payload,
        )
        assert response.status_code == 204

    model_breakdown = await client.get(
        "/api/v4/admin/performance/breakdown?metric=animation_stall&by=model&window=7d",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert model_breakdown.status_code == 200
    assert model_breakdown.json()["items"][:2] == [
        {
            "key": "hrrr",
            "count": 2,
            "avg_ms": 945.0,
            "min_ms": 910.0,
            "max_ms": 980.0,
            "p50_ms": 945.0,
            "p95_ms": 976.5,
            "target_ms": 750.0,
        },
        {
            "key": "gfs",
            "count": 1,
            "avg_ms": 840.0,
            "min_ms": 840.0,
            "max_ms": 840.0,
            "p50_ms": 840.0,
            "p95_ms": 840.0,
            "target_ms": 750.0,
        },
    ]

    variable_breakdown = await client.get(
        "/api/v4/admin/performance/breakdown?metric=animation_stall&by=variable&window=7d",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert variable_breakdown.status_code == 200
    assert variable_breakdown.json()["items"][:2] == [
        {
            "key": "tmp2m",
            "count": 2,
            "avg_ms": 945.0,
            "min_ms": 910.0,
            "max_ms": 980.0,
            "p50_ms": 945.0,
            "p95_ms": 976.5,
            "target_ms": 750.0,
        },
        {
            "key": "apcp",
            "count": 1,
            "avg_ms": 840.0,
            "min_ms": 840.0,
            "max_ms": 840.0,
            "p50_ms": 840.0,
            "p95_ms": 840.0,
            "target_ms": 750.0,
        },
    ]


async def test_admin_perf_queries_can_limit_to_latest_runs_per_model(client: httpx.AsyncClient) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    payloads = [
        ("hrrr", "20260308_00z", 110.0),
        ("hrrr", "20260308_06z", 210.0),
        ("hrrr", "20260308_12z", 310.0),
        ("gfs", "20260308_00z", 410.0),
        ("gfs", "20260308_06z", 510.0),
        ("gfs", "20260308_12z", 610.0),
    ]

    for index, (model_id, run_id, duration_ms) in enumerate(payloads, start=1):
        response = await client.post(
            "/api/v4/telemetry/perf",
            cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
            json={
                "event_name": "frame_change",
                "duration_ms": duration_ms,
                "session_id": f"viewer-session-{index}",
                "model_id": model_id,
                "variable_id": "tmp2m",
                "run_id": run_id,
                "region_id": "conus",
                "forecast_hour": index,
                "device_type": "desktop",
                "viewport_bucket": "xl",
                "page": "/viewer",
            },
        )
        assert response.status_code == 204

    summary = await client.get(
        "/api/v4/admin/performance/summary?window=7d&latest_runs=2",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert summary.status_code == 200
    summary_body = summary.json()
    assert summary_body["filters"]["latest_runs"] == 2
    assert summary_body["metrics"]["frame_change"] == {
        "count": 4,
        "avg_ms": 410.0,
        "min_ms": 210.0,
        "max_ms": 610.0,
        "p50_ms": 410.0,
        "p95_ms": 595.0,
        "target_ms": 250.0,
    }

    breakdown = await client.get(
        "/api/v4/admin/performance/breakdown?metric=frame_change&by=model&window=7d&latest_runs=2",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert breakdown.status_code == 200
    breakdown_body = breakdown.json()
    assert breakdown_body["filters"]["latest_runs"] == 2
    assert breakdown_body["items"][:2] == [
        {
            "key": "hrrr",
            "count": 2,
            "avg_ms": 260.0,
            "min_ms": 210.0,
            "max_ms": 310.0,
            "p50_ms": 260.0,
            "p95_ms": 305.0,
            "target_ms": 250.0,
        },
        {
            "key": "gfs",
            "count": 2,
            "avg_ms": 560.0,
            "min_ms": 510.0,
            "max_ms": 610.0,
            "p50_ms": 560.0,
            "p95_ms": 605.0,
            "target_ms": 250.0,
        },
    ]
