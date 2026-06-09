import os
import sys
import json
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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
def isolate_databases(tmp_path: Path) -> Iterator[None]:
    previous_overrides = dict(main_module.app.dependency_overrides)
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
    yield
    main_module.app.dependency_overrides.clear()
    main_module.app.dependency_overrides.update(previous_overrides)


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


def _authenticate_clerk_user(*, user_id: str = "user_beta", claims: dict[str, object] | None = None) -> None:
    merged_claims = {"sub": user_id, **(claims or {})}

    async def require_test_clerk_user() -> main_module.ClerkPrincipal:
        return main_module.ClerkPrincipal(user_id=user_id, claims=merged_claims, token="test-token")

    main_module.app.dependency_overrides[main_module.require_clerk_user] = require_test_clerk_user
    main_module.app.dependency_overrides[main_module.maybe_clerk_user] = require_test_clerk_user


async def test_feedback_submission_persists_and_admin_lists_record(client: httpx.AsyncClient) -> None:
    _authenticate_clerk_user(user_id="user_beta", claims={"name": "Beta Tester"})
    _create_session(session_id="admin-session", member_id=42, name="Admin")

    response = await client.post(
        "/api/v4/feedback",
        headers={"user-agent": "pytest-browser"},
        json={
            "category": "bug",
            "message": "The loop controls disappeared after switching models.",
            "page_context": "/viewer",
            "model_context": "hrrr",
            "variable_context": "reflectivity",
            "run_context": "2026060912",
            "fhr_context": 12,
            "animation_state_context": "paused",
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
    assert item["clerk_user_id"] == "user_beta"
    assert item["member_id"] == 0
    assert item["forums_display_name"] == "Beta Tester"
    assert item["category"] == "bug"
    assert item["model_context"] == "hrrr"
    assert item["variable_context"] == "reflectivity"
    assert item["run_context"] == "2026060912"
    assert item["fhr_context"] == 12
    assert item["animation_state_context"] == "paused"
    assert item["user_agent"] == "pytest-browser"


async def test_feedback_submission_uses_clerk_profile_email_when_claims_lack_identity(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _authenticate_clerk_user(user_id="user_without_claims")

    def fake_fetch_clerk_user_profile(user_id: str) -> SimpleNamespace:
        assert user_id == "user_without_claims"
        return SimpleNamespace(display_name="beta@example.com", email_address="beta@example.com")

    monkeypatch.setattr(main_module, "fetch_clerk_user_profile", fake_fetch_clerk_user_profile)

    response = await client.post(
        "/api/v4/feedback",
        json={
            "category": "bug",
            "message": "The profile fallback should use email.",
            "page_context": "/viewer",
        },
    )

    assert response.status_code == 201
    feedback = feedback_service.get_admin_feedback(page=1, page_size=10)
    assert feedback["items"][0]["forums_display_name"] == "beta@example.com"


async def test_feedback_notification_receives_clerk_identity(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _authenticate_clerk_user(
        user_id="user_beta",
        claims={"name": "Beta Tester", "email": "beta@example.com"},
    )
    twf_oauth.upsert_session(
        twf_oauth.TwfSession(
            session_id="linked-session",
            member_id=12345,
            display_name="Beta Forums",
            photo_url=None,
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=2_000_000_000,
            clerk_user_id="user_beta",
        )
    )
    captured: dict[str, object] = {}

    def fake_send_feedback_notification(submission: dict[str, object], settings: object) -> None:
        del settings
        captured.update(submission)

    monkeypatch.setattr(feedback_service, "send_feedback_notification", fake_send_feedback_notification)

    response = await client.post(
        "/api/v4/feedback",
        json={
            "category": "bug",
            "message": "Include Clerk identity in the notification.",
            "page_context": "/viewer",
        },
    )

    assert response.status_code == 201
    assert captured["clerk_user_id"] == "user_beta"
    assert captured["clerk_display_name"] == "Beta Tester"
    assert captured["clerk_email_address"] == "beta@example.com"
    assert captured["twf_account_display"] == "Beta Forums"
    assert captured["twf_account_member_id"] == "12345"


async def test_feedback_rate_limit_enforced_by_clerk_user_id(client: httpx.AsyncClient) -> None:
    _authenticate_clerk_user(user_id="user_rate_limited", claims={"email": "beta@example.com"})

    payload = {
        "category": "performance",
        "message": "The map feels slow while scrubbing.",
        "page_context": "/viewer",
    }
    for _index in range(10):
        response = await client.post(
            "/api/v4/feedback",
            json=payload,
        )
        assert response.status_code == 201

    limited_response = await client.post(
        "/api/v4/feedback",
        json=payload,
    )

    assert limited_response.status_code == 429
    assert limited_response.json()["error"] == {
        "code": "FEEDBACK_RATE_LIMITED",
        "message": "Too many feedback submissions. Please try again later.",
    }


async def test_feedback_submission_validates_category_and_required_fields(client: httpx.AsyncClient) -> None:
    _authenticate_clerk_user(user_id="user_beta", claims={"name": "Beta Tester"})

    invalid_category = await client.post(
        "/api/v4/feedback",
        json={
            "category": "other",
            "message": "Something happened.",
            "page_context": "/viewer",
        },
    )
    missing_message = await client.post(
        "/api/v4/feedback",
        json={
            "category": "bug",
            "page_context": "/viewer",
        },
    )

    assert invalid_category.status_code == 400
    assert invalid_category.json()["error"]["code"] == "TWF_VALIDATION_ERROR"
    assert missing_message.status_code == 400
    assert missing_message.json()["error"]["code"] == "TWF_VALIDATION_ERROR"


async def test_feedback_submission_allows_anonymous_reports(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v4/feedback",
        json={
            "category": "bug",
            "message": "Something happened.",
            "reporter_name": "StormChaser42",
            "page_context": "/viewer",
        },
    )

    assert response.status_code == 201
    feedback = feedback_service.get_admin_feedback(page=1, page_size=10)
    assert feedback["items"][0]["clerk_user_id"] is None
    assert feedback["items"][0]["forums_display_name"] == "StormChaser42"


async def test_feedback_submission_defaults_anonymous_display_name(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v4/feedback",
        json={
            "category": "bug",
            "message": "Anonymous report.",
            "page_context": "/viewer",
        },
    )

    assert response.status_code == 201
    feedback = feedback_service.get_admin_feedback(page=1, page_size=10)
    assert feedback["items"][0]["forums_display_name"] == "Anonymous"


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


def test_send_feedback_notification_posts_to_resend(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 201

        def read(self) -> bytes:
            return b'{"id":"email-id"}'

    class FakeHTTPSConnection:
        def __init__(self, host: str, timeout: int) -> None:
            captured["host"] = host
            captured["timeout"] = timeout
            captured["closed"] = False

        def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
            captured["method"] = method
            captured["path"] = path
            captured["body"] = body
            captured["headers"] = headers

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(feedback_service.http.client, "HTTPSConnection", FakeHTTPSConnection)

    submission = {
        "category": "bug",
        "submitted_at": "2026-05-16T12:00:00Z",
        "forums_display_name": "Beta Tester",
        "clerk_user_id": "user_beta",
        "clerk_display_name": "Beta Tester",
        "clerk_email_address": "beta@example.com",
        "twf_account_display": "Beta Forums",
        "twf_account_member_id": "12345",
        "message": "Production smoke test",
        "page_context": "/viewer",
        "model_context": "hrrr",
        "variable_context": "reflectivity",
        "run_context": "2026051612",
        "fhr_context": 12,
        "animation_state_context": "playing",
        "app_version": "test-sha",
        "user_agent": "pytest-browser",
    }
    settings = feedback_service.Settings(
        feedback_notify_email="ops@example.com",
        smtp_password="resend-api-key",
        smtp_from="feedback@cartosky.com",
        cartosky_admin_base_url="https://cartosky.com",
    )

    feedback_service.send_feedback_notification(submission, settings)

    assert captured["host"] == "api.resend.com"
    assert captured["timeout"] == 10
    assert captured["method"] == "POST"
    assert captured["path"] == "/emails"
    assert captured["headers"] == {
        "Authorization": "Bearer resend-api-key",
        "Content-Type": "application/json",
    }
    assert captured["closed"] is True
    payload = json.loads(bytes(captured["body"]).decode())
    assert payload["from"] == "feedback@cartosky.com"
    assert payload["to"] == ["ops@example.com"]
    assert payload["subject"] == "[CartoSky Beta Feedback] [BUG] from Beta Tester"
    assert "Production smoke test" in payload["text"]
    assert "TWF account: Beta Forums (member id 12345)" in payload["text"]
    assert "Clerk user id: user_beta" in payload["text"]
    assert "Clerk display name: Beta Tester" in payload["text"]
    assert "Clerk email: beta@example.com" in payload["text"]
    assert "Variable context: reflectivity" in payload["text"]
    assert "Run timestamp: 2026051612z" in payload["text"]
    assert "Animation state: playing" in payload["text"]
    assert "Admin: https://cartosky.com/admin/feedback" in payload["text"]