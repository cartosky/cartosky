from __future__ import annotations

import pytest
import sys
from pathlib import Path
from starlette.requests import Request

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import config
from app.auth import clerk as clerk_auth


pytestmark = pytest.mark.anyio


def _request(token: str | None = "test-token") -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def _clear_clerk_config_cache() -> None:
    config.clerk_secret_key.cache_clear()
    config.clerk_auth_enabled.cache_clear()
    config.clerk_jwt_audience.cache_clear()
    config.clerk_authorized_parties.cache_clear()


@pytest.fixture(autouse=True)
def isolate_clerk_config_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    monkeypatch.delenv("CLERK_JWT_AUDIENCE", raising=False)
    monkeypatch.delenv("CLERK_AUTHORIZED_PARTIES", raising=False)
    _clear_clerk_config_cache()
    yield
    _clear_clerk_config_cache()


async def test_require_clerk_user_extracts_user_and_metadata_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_123")
    _clear_clerk_config_cache()

    async def fake_verify_token_async(token: str, options: object) -> dict[str, object]:
        assert token == "test-token"
        return {"sub": "user_123", "metadata": {"role": "admin"}}

    monkeypatch.setattr(clerk_auth, "verify_token_async", fake_verify_token_async)

    principal = await clerk_auth.require_clerk_user(_request())

    assert principal.user_id == "user_123"
    assert principal.role == "admin"
    assert principal.is_admin is True


async def test_require_clerk_admin_rejects_non_admin_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_123")
    _clear_clerk_config_cache()

    async def fake_verify_token_async(token: str, options: object) -> dict[str, object]:
        return {"sub": "user_123", "metadata": {"role": "member"}}

    monkeypatch.setattr(clerk_auth, "verify_token_async", fake_verify_token_async)

    with pytest.raises(clerk_auth.HTTPException) as exc_info:
        await clerk_auth.require_clerk_admin(_request())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "error": {
            "code": "CLERK_ADMIN_REQUIRED",
            "message": "Admin access required.",
        }
    }


async def test_require_clerk_user_requires_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_123")
    _clear_clerk_config_cache()

    with pytest.raises(clerk_auth.HTTPException) as exc_info:
        await clerk_auth.require_clerk_user(_request(token=None))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == {
        "error": {
            "code": "CLERK_AUTH_MISSING",
            "message": "Missing Clerk bearer token.",
        }
    }