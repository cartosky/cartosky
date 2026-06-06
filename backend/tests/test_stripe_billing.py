from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app import config  # noqa: E402
from backend.app.services import stripe_billing  # noqa: E402


def _clear_config_cache() -> None:
    config.clerk_secret_key.cache_clear()
    config.clerk_auth_enabled.cache_clear()
    config.clerk_jwt_audience.cache_clear()
    config.clerk_authorized_parties.cache_clear()
    config.billing_enabled.cache_clear()
    config.pro_gating_enabled.cache_clear()
    config.stripe_secret_key.cache_clear()
    config.stripe_webhook_secret.cache_clear()
    config.stripe_pro_price_id.cache_clear()
    config.stripe_checkout_success_url.cache_clear()
    config.stripe_checkout_cancel_url.cache_clear()
    config.stripe_portal_return_url.cache_clear()


@pytest.fixture(autouse=True)
def stripe_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_clerk_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_123")
    monkeypatch.setenv("STRIPE_PRO_PRICE_ID", "price_test_123")
    _clear_config_cache()
    yield
    _clear_config_cache()


def _event(event_type: str, obj: dict[str, Any], *, event_id: str = "evt_test") -> dict[str, Any]:
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": obj},
    }


def test_handle_webhook_signature_failure_raises_400(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_construct_event(payload: bytes, sig_header: str, secret: str) -> dict[str, Any]:
        raise stripe_billing.stripe.error.SignatureVerificationError("bad signature", sig_header)

    monkeypatch.setattr(stripe_billing.stripe.Webhook, "construct_event", fake_construct_event)

    with pytest.raises(stripe_billing.HTTPException) as exc_info:
        stripe_billing.handle_webhook_event(b"{}", "bad")

    assert exc_info.value.status_code == 400


def test_checkout_session_completed_writes_pro_plan_and_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    updates: list[dict[str, Any]] = []

    monkeypatch.setattr(stripe_billing.stripe.Webhook, "construct_event", lambda payload, sig_header, secret: _event(
        "checkout.session.completed",
        {
            "object": "checkout.session",
            "customer": "cus_123",
            "subscription": "sub_123",
            "metadata": {"clerk_user_id": "user_123"},
        },
    ))
    monkeypatch.setattr(stripe_billing, "_update_clerk_user_plan", lambda clerk_user_id, plan, stripe_customer_id=None, stripe_subscription_id=None, stripe_subscription_status=None: updates.append({
        "clerk_user_id": clerk_user_id,
        "plan": plan,
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "stripe_subscription_status": stripe_subscription_status,
    }))
    monkeypatch.setattr(stripe_billing.stripe.Subscription, "retrieve", lambda subscription_id: {"id": subscription_id, "status": "active"})

    stripe_billing.handle_webhook_event(b"{}", "sig")

    assert updates == [{
        "clerk_user_id": "user_123",
        "plan": "pro",
        "stripe_customer_id": "cus_123",
        "stripe_subscription_id": "sub_123",
        "stripe_subscription_status": "active",
    }]


@pytest.mark.parametrize(
    ("event_type", "subscription_status", "expected_plan"),
    [
        ("customer.subscription.updated", "active", "pro"),
        ("customer.subscription.updated", "trialing", "pro"),
        ("customer.subscription.updated", "canceled", "free"),
        ("customer.subscription.updated", "past_due", "free"),
        ("customer.subscription.deleted", "canceled", "free"),
    ],
)
def test_subscription_events_write_expected_plan(
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    subscription_status: str,
    expected_plan: str,
) -> None:
    updates: list[dict[str, Any]] = []

    monkeypatch.setattr(stripe_billing.stripe.Webhook, "construct_event", lambda payload, sig_header, secret: _event(
        event_type,
        {
            "object": "subscription",
            "id": "sub_123",
            "customer": "cus_123",
            "status": subscription_status,
            "metadata": {"clerk_user_id": "user_123"},
        },
    ))
    monkeypatch.setattr(stripe_billing, "_update_clerk_user_plan", lambda clerk_user_id, plan, stripe_customer_id=None, stripe_subscription_id=None, stripe_subscription_status=None: updates.append({
        "clerk_user_id": clerk_user_id,
        "plan": plan,
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "stripe_subscription_status": stripe_subscription_status,
    }))

    stripe_billing.handle_webhook_event(b"{}", "sig")

    assert updates == [{
        "clerk_user_id": "user_123",
        "plan": expected_plan,
        "stripe_customer_id": "cus_123",
        "stripe_subscription_id": "sub_123",
        "stripe_subscription_status": subscription_status,
    }]


def test_missing_clerk_user_metadata_logs_safely(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(stripe_billing.stripe.Webhook, "construct_event", lambda payload, sig_header, secret: _event(
        "customer.subscription.updated",
        {
            "object": "subscription",
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "metadata": {},
        },
    ))
    monkeypatch.setattr(stripe_billing.stripe.Customer, "retrieve", lambda customer_id: {"id": customer_id, "metadata": {}})

    stripe_billing.handle_webhook_event(b"{}", "sig")

    assert "Unable to resolve Clerk user" in caplog.text


def test_duplicate_events_do_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    event = _event(
        "customer.subscription.updated",
        {
            "object": "subscription",
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "metadata": {"clerk_user_id": "user_123"},
        },
        event_id="evt_duplicate",
    )

    monkeypatch.setattr(stripe_billing.stripe.Webhook, "construct_event", lambda payload, sig_header, secret: event)
    monkeypatch.setattr(stripe_billing, "_update_clerk_user_plan", lambda *args, **kwargs: calls.append("updated"))

    stripe_billing.handle_webhook_event(b"{}", "sig")
    stripe_billing.handle_webhook_event(b"{}", "sig")

    assert calls == ["updated", "updated"]