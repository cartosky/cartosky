import json
import os
import re
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

from app import main as main_module
from app.auth.clerk import ClerkPrincipal
from app.auth import twf_oauth

pytestmark = pytest.mark.anyio
AUTH_HEADERS = {"Authorization": "Bearer clerk-test-token"}


async def _fake_clerk_user() -> ClerkPrincipal:
    return ClerkPrincipal(user_id="user_test", claims={}, token="clerk-test-token")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    main_module.app.dependency_overrides[main_module.require_clerk_user] = _fake_clerk_user
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    main_module.app.dependency_overrides.pop(main_module.require_clerk_user, None)


async def test_request_json_with_variants_inlines_params_for_index_php_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
            del exc_type, exc, tb
            return None

    async def fake_request_json(
        client: object,
        method: str,
        url: str,
        **kwargs: object,
    ) -> dict[str, object]:
        del client
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(twf_oauth.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(twf_oauth, "_request_json", fake_request_json)

    result = await twf_oauth._request_json_with_variants(
        method="GET",
        urls=["https://example.com/api/index.php?/forums/topics"],
        headers={"Authorization": "Bearer token"},
        timeout=5,
        params={"forum": "4", "pinned": "1", "sortBy": "updated"},
    )

    assert result == {"ok": True}
    sent_url = str(captured["url"])
    sent_kwargs = captured["kwargs"]
    assert sent_url.endswith("index.php?/forums/topics&forum=4&pinned=1&sortBy=updated")
    assert "index.php?/forums/topics?&" not in sent_url
    assert "index.php?/forums/topics?forum=4" not in sent_url
    assert isinstance(sent_kwargs, dict)
    assert "params" not in sent_kwargs


async def test_twf_topics_without_session_returns_enveloped_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/twf/topics", params={"forum_id": 4})

    assert response.status_code == 401
    payload = response.json()
    assert payload == {
        "error": {
            "code": "TWF_SESSION_NOT_FOUND",
            "message": "Session not found",
        }
    }


async def test_twf_topics_forum_id_validation_uses_twf_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/twf/topics", params={"forum_id": 0})

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "TWF_VALIDATION_ERROR"
    assert isinstance(payload["error"]["message"], str)


async def test_twf_topics_merges_dedupes_orders_and_filters_to_requested_forum(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = twf_oauth.TwfSession(
        session_id="sid-topics",
        member_id=42,
        display_name="tester",
        photo_url=None,
        access_token="token",
        refresh_token="refresh",
        expires_at=9999999999,
    )
    monkeypatch.setattr(main_module.twf_oauth, "get_session_for_clerk_user", lambda _user_id: sess)

    calls: list[dict[str, str]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self.status_code = 200
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
            del exc_type, exc, tb
            return None

        async def get(self, url: str, **kwargs: object) -> FakeResponse:
            resolved: dict[str, str] = {}
            params = kwargs.get("params")
            if isinstance(params, dict):
                resolved.update({k: str(v) for k, v in params.items()})
            for key in ("forums", "forum", "pinned", "perPage"):
                match = re.search(rf"[?&]{re.escape(key)}=([^&]+)", url)
                if match and key not in resolved:
                    resolved[key] = match.group(1)
            calls.append(resolved)
            forum = str(resolved.get("forums", resolved.get("forum", "")))
            pinned = str(resolved.get("pinned", "0"))
            if forum == "4" and pinned == "1":
                return FakeResponse(
                    {
                        "results": [
                            {
                                "id": 201,
                                "title": "March 2026 West Monthly Thread",
                                "url": "https://forums.example.com/topic/201-march-2026-west-monthly-thread/",
                                "pinned": True,
                                "updated": "2026-03-02T12:00:00Z",
                                "forum": {"id": 4},
                            },
                            {
                                "id": 901,
                                "title": "East Topic leaked into West response",
                                "url": "https://forums.example.com/topic/901-east-leak/",
                                "pinned": True,
                                "updated": "2026-02-20T10:00:00Z",
                                "forum": {"id": 9},
                            },
                        ]
                    }
                )
            if forum == "4" and pinned == "0":
                return FakeResponse(
                    {
                        "results": [
                            {
                                "id": 202,
                                "title": "West Nowcasting",
                                "url": "https://forums.example.com/topic/202-west-nowcasting/",
                                "pinned": False,
                                "updated": "2026-03-03T09:00:00Z",
                                "forum": {"id": 4},
                            },
                            {
                                "id": 902,
                                "title": "East chatter leaked into West response",
                                "url": "https://forums.example.com/topic/902-east-leak/",
                                "pinned": False,
                                "updated": "2026-02-28T08:00:00Z",
                                "forum": {"id": 9},
                            },
                        ]
                    }
                )
            if forum == "9" and pinned == "1":
                return FakeResponse(
                    {
                        "results": [
                            {
                                "id": 101,
                                "title": "March 2026 East Monthly Thread",
                                "url": "https://forums.example.com/topic/101-march-2026-east-monthly-thread/",
                                "pinned": True,
                                "updated": "2026-03-02T12:00:00Z",
                                "forum": {"id": 9},
                            },
                            {
                                "id": 801,
                                "title": "West Topic leaked into East response",
                                "url": "https://forums.example.com/topic/801-west-leak/",
                                "pinned": True,
                                "updated": "2026-02-20T10:00:00Z",
                                "forum": {"id": 4},
                            },
                        ]
                    }
                )
            return FakeResponse(
                {
                    "results": [
                        {
                            "id": 103,
                            "title": "East Nowcasting",
                            "url": "https://forums.example.com/topic/103-east-nowcasting/",
                            "pinned": False,
                            "updated": "2026-03-03T09:00:00Z",
                            "forum": {"id": 9},
                        },
                        {
                            "id": 101,
                            "title": "March 2026 East Monthly Thread",
                            "url": "https://forums.example.com/topic/101-march-2026-east-monthly-thread/",
                            "pinned": False,
                            "updated": "2026-03-01T03:00:00Z",
                            "forum": {"id": 9},
                        },
                        {
                            "id": 802,
                            "title": "West chatter leaked into East response",
                            "url": "https://forums.example.com/topic/802-west-leak/",
                            "pinned": False,
                            "updated": "2026-02-28T08:00:00Z",
                            "forum": {"id": 4},
                        },
                    ]
                }
            )

    monkeypatch.setattr(main_module.twf_oauth.httpx, "AsyncClient", FakeAsyncClient)

    west_response = await client.get("/twf/topics", params={"forum_id": 4, "limit": 15}, headers=AUTH_HEADERS)
    assert west_response.status_code == 200
    west_payload = west_response.json()
    assert west_payload["forum_id"] == 4
    west_ids = [row["id"] for row in west_payload["results"]]
    assert west_ids == [201, 202]

    east_response = await client.get("/twf/topics", params={"forum_id": 9, "limit": 15}, headers=AUTH_HEADERS)
    assert east_response.status_code == 200
    east_payload = east_response.json()
    assert east_payload["forum_id"] == 9
    east_ids = [row["id"] for row in east_payload["results"]]
    assert east_ids == [101, 103]


async def test_twf_topics_keeps_items_without_forum_metadata_for_requested_forum(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = twf_oauth.TwfSession(
        session_id="sid-topics-missing-forum",
        member_id=42,
        display_name="tester",
        photo_url=None,
        access_token="token",
        refresh_token="refresh",
        expires_at=9999999999,
    )
    monkeypatch.setattr(main_module.twf_oauth, "get_session_for_clerk_user", lambda _user_id: sess)

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self.status_code = 200
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
            del exc_type, exc, tb
            return None

        async def get(self, url: str, **kwargs: object) -> FakeResponse:
            resolved: dict[str, str] = {}
            params = kwargs.get("params")
            if isinstance(params, dict):
                resolved.update({k: str(v) for k, v in params.items()})
            for key in ("forums", "forum", "pinned", "perPage"):
                match = re.search(rf"[?&]{re.escape(key)}=([^&]+)", url)
                if match and key not in resolved:
                    resolved[key] = match.group(1)

            pinned = str(resolved.get("pinned", "0"))
            if pinned == "1":
                return FakeResponse(
                    {
                        "results": [
                            {
                                "id": 601,
                                "title": "Staff Monthly Thread",
                                "pinned": True,
                                "updated": "2026-05-20T12:00:00Z",
                            },
                            {
                                "id": 999,
                                "title": "Wrong Forum Topic",
                                "url": "https://forums.example.com/topic/999-wrong-forum/",
                                "pinned": True,
                                "updated": "2026-05-19T12:00:00Z",
                                "forum": {"id": 4},
                            },
                        ]
                    }
                )

            return FakeResponse(
                {
                    "results": [
                        {
                            "id": 602,
                            "title": "Staff Operations",
                            "pinned": False,
                            "updated": "2026-05-21T09:00:00Z",
                        },
                        {
                            "id": 998,
                            "title": "Another Wrong Forum Topic",
                            "url": "https://forums.example.com/topic/998-wrong-forum/",
                            "pinned": False,
                            "updated": "2026-05-18T09:00:00Z",
                            "forum_id": 9,
                        },
                    ]
                }
            )

    monkeypatch.setattr(main_module.twf_oauth.httpx, "AsyncClient", FakeAsyncClient)

    response = await client.get("/twf/topics", params={"forum_id": 60, "limit": 15}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["forum_id"] == 60
    assert [row["id"] for row in payload["results"]] == [601, 602]
    assert payload["results"][0].get("url") is None
    assert payload["results"][1].get("url") is None


async def test_list_topics_falls_back_when_first_param_variant_is_unscoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = twf_oauth.TwfSession(
        session_id="sid-list-topics-variants",
        member_id=42,
        display_name="tester",
        photo_url=None,
        access_token="token",
        refresh_token="refresh",
        expires_at=9999999999,
    )
    calls: list[dict[str, str]] = []

    async def fake_ensure_fresh_tokens(current: twf_oauth.TwfSession) -> twf_oauth.TwfSession:
        return current

    async def fake_request_json_with_variants(
        *,
        method: str,
        urls: list[str],
        headers: dict[str, str],
        timeout: float,
        data: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, object]:
        del method, urls, headers, timeout, data
        assert params is not None
        calls.append(params)
        if params.get("forums") == "60":
            return {
                "results": [
                    {
                        "id": 999,
                        "title": "Wrong forum topic",
                        "url": "https://forums.example.com/topic/999-wrong-forum/",
                        "forum": {"id": 4},
                    }
                ]
            }
        if params.get("forum") == "60":
            return {
                "results": [
                    {
                        "id": 601,
                        "title": "Staff discussion",
                        "url": "https://forums.example.com/topic/601-staff-discussion/",
                        "forum": {"id": 60},
                    }
                ]
            }
        raise AssertionError(f"Unexpected params: {params}")

    monkeypatch.setattr(twf_oauth, "ensure_fresh_tokens", fake_ensure_fresh_tokens)
    monkeypatch.setattr(twf_oauth, "_request_json_with_variants", fake_request_json_with_variants)

    payload = await twf_oauth.list_topics(sess, forum_id=60, pinned=False, per_page=15)

    assert [call.get("forums") or call.get("forum") for call in calls] == ["60", "60"]
    assert "forums" in calls[0]
    assert "forum" in calls[1]
    assert [row["id"] for row in payload["results"]] == [601]
