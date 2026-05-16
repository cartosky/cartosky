import os
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
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
feedback_service = main_module.feedback_service

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def isolate_databases(tmp_path: Path) -> None:
    token_db = tmp_path / "tokens.sqlite3"
    feedback_db = tmp_path / "feedback.sqlite3"
    twf_oauth.TOKEN_DB_PATH = str(token_db)
    feedback_service.FEEDBACK_DB_PATH = feedback_db
    feedback_service._db_initialized = False
    main_module.ADMIN_MEMBER_IDS = {42}
    for env_name in (
        "FEEDBACK_NOTIFY_EMAIL",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "SMTP_FROM",
        "CARTOSKY_ADMIN_BASE_URL",
    ):
        os.environ.pop(env_name, None)


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


async def test_feedback_submission_persists_and_admin_lists_record(client: httpx.AsyncClient) -> None:
    _create_session(session_id="beta-session", member_id=777, name="Beta Tester")
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    response = await client.post(
        "/api/v4/feedback",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "beta-session"},
        headers={"user-agent": "pytest-browser"},
        json={
            "category": "bug",
            "message": "The loop controls disappeared after switching models.",
            "page_context": "/viewer",
            "model_context": "hrrr",
            "fhr_context": 12,
            "app_version": "test-sha",
        },
    )

    assert response.status_code == 201
    submit_payload = response.json()
    assert submit_payload["ok"] is True
    assert submit_payload["id"] == 1

    admin_response = await client.get(
        "/api/v4/admin/feedback",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert admin_response.status_code == 200
    admin_payload = admin_response.json()
    assert admin_payload["total"] == 1
    assert admin_payload["summary"]["total"] == 1
    assert admin_payload["summary"]["by_category"]["bug"] == 1
    assert admin_payload["daily_volume"][0]["count"] == 1
    item = admin_payload["items"][0]
    assert item["member_id"] == 777
    assert item["forums_display_name"] == "Beta Tester"
    assert item["category"] == "bug"
    assert item["model_context"] == "hrrr"
    assert item["fhr_context"] == 12
    assert item["user_agent"] == "pytest-browser"


async def test_feedback_rate_limit_enforced_by_member_id(client: httpx.AsyncClient) -> None:
    _create_session(session_id="beta-session-a", member_id=777, name="Original Name")
    _create_session(session_id="beta-session-b", member_id=777, name="Renamed User")

    payload = {
        "category": "performance",
        "message": "The map feels slow while scrubbing.",
        "page_context": "/viewer",
    }
    for index in range(10):
        session_id = "beta-session-a" if index % 2 == 0 else "beta-session-b"
        response = await client.post(
            "/api/v4/feedback",
            cookies={twf_oauth.SESSION_COOKIE_NAME: session_id},
            json=payload,
        )
        assert response.status_code == 201

    limited_response = await client.post(
        "/api/v4/feedback",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "beta-session-b"},
        json=payload,
    )

    assert limited_response.status_code == 429
    assert limited_response.json()["error"] == {
        "code": "FEEDBACK_RATE_LIMITED",
        "message": "Too many feedback submissions. Please try again later.",
    }


async def test_feedback_submission_validates_category_and_required_fields(client: httpx.AsyncClient) -> None:
    _create_session(session_id="beta-session", member_id=777, name="Beta Tester")

    invalid_category = await client.post(
        "/api/v4/feedback",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "beta-session"},
        json={
            "category": "other",
            "message": "Something happened.",
            "page_context": "/viewer",
        },
    )
    missing_message = await client.post(
        "/api/v4/feedback",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "beta-session"},
        json={
            "category": "bug",
            "page_context": "/viewer",
        },
    )

    assert invalid_category.status_code == 400
    assert invalid_category.json()["error"]["code"] == "TWF_VALIDATION_ERROR"
    assert missing_message.status_code == 400
    assert missing_message.json()["error"]["code"] == "TWF_VALIDATION_ERROR"


async def test_admin_feedback_returns_aggregate_metadata(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_session(session_id="admin-session", member_id=42, name="Admin")
    base_now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    current_now = {"value": base_now}
    monkeypatch.setattr(feedback_service, "_utc_now", lambda: current_now["value"])

    def insert_at(dt: datetime, *, category: str, display_name: str) -> None:
        current_now["value"] = dt
        feedback_service.insert_feedback(
            category=category,
            message=f"{category} feedback",
            member_id=100 + len(display_name),
            forums_display_name=display_name,
            page_context="/viewer",
            model_context="hrrr",
            fhr_context=6,
            user_agent="pytest-browser",
            app_version="test-sha",
        )

    insert_at(base_now - timedelta(hours=1), category="bug", display_name="Alpha")
    insert_at(base_now - timedelta(days=2), category="performance", display_name="Bravo")
    insert_at(base_now - timedelta(days=9), category="feature", display_name="Charlie")
    current_now["value"] = base_now

    response = await client.get(
        "/api/v4/admin/feedback?page=1&page_size=2",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 2
    assert payload["total"] == 3
    assert len(payload["items"]) == 2
    assert payload["summary"] == {
        "total": 3,
        "last_24h": 1,
        "last_7d": 2,
        "by_category": {
            "bug": 1,
            "performance": 1,
            "feature": 1,
            "data_accuracy": 0,
            "ui_ux": 0,
        },
    }
    assert payload["daily_volume"] == [
        {"date": "2026-05-07", "count": 1},
        {"date": "2026-05-14", "count": 1},
        {"date": "2026-05-16", "count": 1},
    ]

    filtered_response = await client.get(
        "/api/v4/admin/feedback?category=bug&display_name=alp",
        cookies={twf_oauth.SESSION_COOKIE_NAME: "admin-session"},
    )

    assert filtered_response.status_code == 200
    filtered_payload = filtered_response.json()
    assert filtered_payload["total"] == 1
    assert filtered_payload["summary"]["by_category"]["bug"] == 1
    assert filtered_payload["filters"]["display_name"] == "alp"