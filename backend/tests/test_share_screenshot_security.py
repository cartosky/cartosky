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

from app import main as main_module
from app.auth.clerk import ClerkPrincipal

pytestmark = pytest.mark.anyio


async def _fake_clerk_user() -> ClerkPrincipal:
    return ClerkPrincipal(user_id="user_test", claims={}, token="clerk-test-token")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    main_module.app.dependency_overrides.pop(main_module.require_clerk_user, None)


async def test_share_screenshot_requires_clerk_authentication(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_render(_url: str, *, basemap: str = "light") -> bytes:
        return b"png"

    monkeypatch.setattr(main_module.screenshot_service, "render", fake_render)

    response = await client.post(
        "/api/v4/share/screenshot",
        json={"url": "https://cartosky.com/share/example", "basemap": "light"},
    )

    assert response.status_code == 401


async def test_share_screenshot_rejects_loopback_hosts_for_authenticated_users(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_module.app.dependency_overrides[main_module.require_clerk_user] = _fake_clerk_user

    async def fail_render(_url: str, *, basemap: str = "light") -> bytes:
        raise AssertionError("loopback URL should be rejected before rendering")

    monkeypatch.setattr(main_module.screenshot_service, "render", fail_render)

    response = await client.post(
        "/api/v4/share/screenshot",
        json={"url": "http://127.0.0.1:8000/internal", "basemap": "light"},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "URL not allowed"}
