import os
import sys
from collections.abc import AsyncIterator, Iterator
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

pytestmark = pytest.mark.anyio

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfakepngdata"


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    previous_overrides = dict(main_module.app.dependency_overrides)
    yield
    main_module.app.dependency_overrides.clear()
    main_module.app.dependency_overrides.update(previous_overrides)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


def _authenticate_clerk_user(*, user_id: str = "user_share_media") -> None:
    async def require_test_clerk_user() -> main_module.ClerkPrincipal:
        return main_module.ClerkPrincipal(user_id=user_id, claims={"sub": user_id}, token="test-token")

    main_module.app.dependency_overrides[main_module.require_clerk_user] = require_test_clerk_user


async def test_share_media_upload_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v4/share/media",
        files={"file": ("share.png", PNG_BYTES, "image/png")},
    )

    assert response.status_code == 401


async def test_share_media_upload_accepts_valid_png(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _authenticate_clerk_user()
    captured: dict[str, object] = {}

    def fake_upload_share_png(*, data: bytes, filename_hint: str | None, content_type: str) -> dict[str, str]:
        captured["data"] = data
        captured["filename_hint"] = filename_hint
        captured["content_type"] = content_type
        return {
            "key": "share/2026/03/07/cartosky_hrrr_20260304_03z_fh1_radar-ptype_conus_deadbeef.png",
            "url": "https://cdn.cartosky.com/share/2026/03/07/cartosky_hrrr_20260304_03z_fh1_radar-ptype_conus_deadbeef.png",
        }

    monkeypatch.setattr(main_module.share_media_service, "upload_share_png", fake_upload_share_png)

    response = await client.post(
        "/api/v4/share/media",
        headers={"Authorization": "Bearer test-token"},
        data={
            "model": "HRRR",
            "run": "20260304_03z",
            "fh": "1",
            "variable": "Radar_PType",
            "region": "CONUS",
        },
        files={"file": ("share.png", PNG_BYTES, "image/png")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "key": "share/2026/03/07/cartosky_hrrr_20260304_03z_fh1_radar-ptype_conus_deadbeef.png",
        "url": "https://cdn.cartosky.com/share/2026/03/07/cartosky_hrrr_20260304_03z_fh1_radar-ptype_conus_deadbeef.png",
    }
    assert captured["data"] == PNG_BYTES
    assert captured["content_type"] == "image/png"
    assert captured["filename_hint"] == "cartosky_hrrr_20260304_03z_fh1_radar-ptype_conus.png"


async def test_share_media_upload_rejects_invalid_content_type(client: httpx.AsyncClient) -> None:
    _authenticate_clerk_user()

    response = await client.post(
        "/api/v4/share/media",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("share.txt", b"not-a-png", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_CONTENT_TYPE",
            "message": "Only PNG or GIF uploads are supported.",
        }
    }


async def test_share_media_upload_rejects_invalid_png_signature(client: httpx.AsyncClient) -> None:
    _authenticate_clerk_user()

    response = await client.post(
        "/api/v4/share/media",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("share.png", b"not-a-png", "image/png")},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_IMAGE",
            "message": "Uploaded file does not match its image type.",
        }
    }


async def test_share_media_upload_accepts_valid_gif(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _authenticate_clerk_user()
    captured: dict[str, object] = {}
    gif_bytes = b"GIF89a" + b"\x00" * 32

    def fake_upload_share_png(*, data: bytes, filename_hint: str | None, content_type: str) -> dict[str, str]:
        captured["data"] = data
        captured["content_type"] = content_type
        return {
            "key": "share/2026/07/06/cartosky_hrrr_deadbeef.gif",
            "url": "https://cdn.cartosky.com/share/2026/07/06/cartosky_hrrr_deadbeef.gif",
        }

    monkeypatch.setattr(main_module.share_media_service, "upload_share_png", fake_upload_share_png)

    response = await client.post(
        "/api/v4/share/media",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("share.gif", gif_bytes, "image/gif")},
    )

    assert response.status_code == 200
    assert captured["data"] == gif_bytes
    assert captured["content_type"] == "image/gif"


async def test_share_media_upload_rejects_mismatched_gif_signature(client: httpx.AsyncClient) -> None:
    _authenticate_clerk_user()

    response = await client.post(
        "/api/v4/share/media",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("share.gif", PNG_BYTES, "image/gif")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMAGE"


async def test_share_media_upload_rejects_oversized_png(client: httpx.AsyncClient) -> None:
    _authenticate_clerk_user()
    large_png = PNG_BYTES + (b"0" * (main_module.share_media_service.MAX_SHARE_PNG_BYTES - len(PNG_BYTES) + 1))

    response = await client.post(
        "/api/v4/share/media",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("large.png", large_png, "image/png")},
    )

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "FILE_TOO_LARGE",
            "message": "Upload exceeds the 10 MB limit.",
        }
    }


async def test_share_media_upload_rate_limited_per_user(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _authenticate_clerk_user(user_id="user_rate_limited")
    monkeypatch.setattr(main_module, "_SHARE_MEDIA_USER_LIMIT", 1)

    def fake_upload_share_png(*, data: bytes, filename_hint: str | None, content_type: str) -> dict[str, str]:
        return {"key": "share/test.png", "url": "https://cdn.cartosky.com/share/test.png"}

    monkeypatch.setattr(main_module.share_media_service, "upload_share_png", fake_upload_share_png)

    first = await client.post(
        "/api/v4/share/media",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("share.png", PNG_BYTES, "image/png")},
    )
    second = await client.post(
        "/api/v4/share/media",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("share.png", PNG_BYTES, "image/png")},
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {
        "error": {
            "code": "SHARE_MEDIA_RATE_LIMITED",
            "message": "Too many share image uploads. Try again later.",
        }
    }
