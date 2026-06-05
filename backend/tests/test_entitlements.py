from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TWF_BASE", "https://forums.example.test")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://cartosky.example.test/auth/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://cartosky.example.test/viewer")
os.environ.setdefault("TOKEN_DB_PATH", ":memory:")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from backend.app import config  # noqa: E402
from backend.app.auth import clerk as clerk_auth  # noqa: E402
from backend.app.auth import entitlements  # noqa: E402
from app.main import app  # noqa: E402


def _clear_config_cache() -> None:
    config.clerk_secret_key.cache_clear()
    config.clerk_auth_enabled.cache_clear()
    config.clerk_jwt_audience.cache_clear()
    config.clerk_authorized_parties.cache_clear()
    config.billing_enabled.cache_clear()
    config.pro_gating_enabled.cache_clear()


@pytest.fixture(autouse=True)
def isolate_billing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARTOSKY_BILLING_ENABLED", raising=False)
    monkeypatch.delenv("CARTOSKY_PRO_GATING_ENABLED", raising=False)
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    _clear_config_cache()
    yield
    _clear_config_cache()


def test_gating_disabled_allows_ecmwf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARTOSKY_BILLING_ENABLED", "true")
    monkeypatch.setenv("CARTOSKY_PRO_GATING_ENABLED", "false")
    _clear_config_cache()

    assert entitlements.billing_enabled() is True
    assert entitlements.pro_gating_enabled() is False
    assert entitlements.can_access_product(None, "ecmwf") is True


def test_gating_enabled_denies_ecmwf_without_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARTOSKY_PRO_GATING_ENABLED", "true")
    _clear_config_cache()

    assert entitlements.can_access_product({"fea": ""}, "ecmwf") is False

    with pytest.raises(entitlements.HTTPException) as exc_info:
        entitlements.require_product_access({"fea": ""}, "ecmwf")

    assert exc_info.value.status_code == 403


def test_gating_enabled_allows_ecmwf_with_scoped_fea_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARTOSKY_PRO_GATING_ENABLED", "true")
    _clear_config_cache()

    assert entitlements.get_user_features({"fea": "u:ecmwf,o:comparison_tools"}) == {"ecmwf", "comparison_tools"}
    assert entitlements.can_access_product({"fea": "u:ecmwf"}, "ecmwf") is True


def test_non_protected_products_always_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARTOSKY_PRO_GATING_ENABLED", "true")
    _clear_config_cache()

    assert entitlements.get_required_feature_for_product("hrrr") is None
    assert entitlements.can_access_product(None, "hrrr") is True


def test_beta_rollback_defaults_allow_everything() -> None:
    assert entitlements.billing_enabled() is False
    assert entitlements.pro_gating_enabled() is False
    assert entitlements.can_access_product(None, "ecmwf") is True


def test_billing_preview_mode_keeps_product_access_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARTOSKY_BILLING_ENABLED", "true")
    monkeypatch.setenv("CARTOSKY_PRO_GATING_ENABLED", "false")
    _clear_config_cache()

    assert entitlements.billing_enabled() is True
    assert entitlements.can_access_product({"fea": ""}, "ecmwf") is True


@pytest.mark.anyio
async def test_protected_manifest_endpoint_returns_403_without_entitlement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARTOSKY_PRO_GATING_ENABLED", "true")
    _clear_config_cache()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v4/ecmwf/latest/manifest")

    assert response.status_code == 403


@pytest.mark.anyio
async def test_protected_manifest_endpoint_accepts_entitlement_before_data_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CARTOSKY_PRO_GATING_ENABLED", "true")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_123")
    _clear_config_cache()

    async def fake_verify_token_async(token: str, options: object) -> dict[str, object]:
        assert token == "test-token"
        return {"sub": "user_123", "fea": "u:ecmwf"}

    monkeypatch.setattr(clerk_auth, "verify_token_async", fake_verify_token_async)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v4/ecmwf/latest/manifest",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code != 403
