from __future__ import annotations

import http.client
import json
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import stripe
from fastapi import HTTPException, status

from backend.app import config

logger = logging.getLogger(__name__)

ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}
FREE_SUBSCRIPTION_STATUSES = {
    "canceled",
    "incomplete",
    "incomplete_expired",
    "past_due",
    "paused",
    "unpaid",
}


def _object_get(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except Exception:
            pass
    return getattr(value, key, None)


def _normalize_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _stripe_config_error(name: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "error": {
                "code": "STRIPE_CONFIG_MISSING",
                "message": f"{name} must be configured when CartoSky billing is enabled.",
            }
        },
    )


def _require_stripe_api_key() -> str:
    secret_key = config.stripe_secret_key()
    if not secret_key:
        raise _stripe_config_error("STRIPE_SECRET_KEY")
    stripe.api_key = secret_key
    return secret_key


def _require_price_id() -> str:
    price_id = config.stripe_pro_price_id()
    if not price_id:
        raise _stripe_config_error("STRIPE_PRO_PRICE_ID")
    return price_id


def _require_webhook_secret() -> str:
    secret = config.stripe_webhook_secret()
    if not secret:
        raise _stripe_config_error("STRIPE_WEBHOOK_SECRET")
    return secret


def _status_to_plan(subscription_status: str | None) -> str:
    normalized_status = _normalize_string(subscription_status)
    if normalized_status in ACTIVE_SUBSCRIPTION_STATUSES:
        return "pro"
    if normalized_status in FREE_SUBSCRIPTION_STATUSES:
        return "free"
    return "free"


def _clerk_headers() -> dict[str, str]:
    secret_key = config.clerk_secret_key()
    if not secret_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "code": "CLERK_SECRET_KEY_MISSING",
                    "message": "Clerk auth is not configured on the API server.",
                }
            },
        )
    return {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }


def _fetch_clerk_user_payload(clerk_user_id: str) -> dict[str, Any] | None:
    normalized_user_id = _normalize_string(clerk_user_id)
    if not normalized_user_id:
        return None

    conn: http.client.HTTPSConnection | None = None
    try:
        conn = http.client.HTTPSConnection("api.clerk.com", timeout=5)
        conn.request(
            "GET",
            f"/v1/users/{quote(normalized_user_id, safe='')}",
            headers=_clerk_headers(),
        )
        response = conn.getresponse()
        body = response.read()
        if response.status == 404:
            return None
        if response.status < 200 or response.status >= 300:
            logger.error("Clerk user fetch failed user_id=%s status=%s", normalized_user_id, response.status)
            return None
        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict):
            return None
        return decoded
    except Exception as exc:
        logger.error("Clerk user fetch failed user_id=%s error=%s", normalized_user_id, exc)
        return None
    finally:
        if conn is not None:
            conn.close()


def _update_clerk_user_plan(
    clerk_user_id: str,
    plan: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    stripe_subscription_status: str | None = None,
) -> None:
    normalized_user_id = _normalize_string(clerk_user_id)
    normalized_plan = _normalize_string(plan) or "free"
    if not normalized_user_id:
        logger.error("Skipping Clerk plan update because clerk_user_id is missing")
        return

    current_payload = _fetch_clerk_user_payload(normalized_user_id)
    if current_payload is None:
        logger.error("Unable to update Clerk plan because user lookup failed user_id=%s", normalized_user_id)
        return

    public_metadata = current_payload.get("public_metadata")
    if not isinstance(public_metadata, dict):
        public_metadata = {}
    merged_public_metadata = dict(public_metadata)
    merged_public_metadata["plan"] = normalized_plan

    private_metadata = current_payload.get("private_metadata")
    if not isinstance(private_metadata, dict):
        private_metadata = {}
    merged_private_metadata = dict(private_metadata)
    if stripe_customer_id is not None:
        merged_private_metadata["stripe_customer_id"] = stripe_customer_id
    if stripe_subscription_id is not None:
        merged_private_metadata["stripe_subscription_id"] = stripe_subscription_id
    if stripe_subscription_status is not None:
        merged_private_metadata["stripe_subscription_status"] = stripe_subscription_status

    conn: http.client.HTTPSConnection | None = None
    try:
        conn = http.client.HTTPSConnection("api.clerk.com", timeout=5)
        conn.request(
            "PATCH",
            f"/v1/users/{quote(normalized_user_id, safe='')}",
            body=json.dumps(
                {
                    "public_metadata": merged_public_metadata,
                    "private_metadata": merged_private_metadata,
                }
            ).encode("utf-8"),
            headers=_clerk_headers(),
        )
        response = conn.getresponse()
        body = response.read()
        if response.status < 200 or response.status >= 300:
            logger.error(
                "Clerk plan update failed user_id=%s status=%s body=%s",
                normalized_user_id,
                response.status,
                body.decode("utf-8", errors="replace"),
            )
            return
        logger.info(
            "Updated Clerk billing metadata user_id=%s plan=%s customer_id=%s subscription_id=%s subscription_status=%s",
            normalized_user_id,
            normalized_plan,
            stripe_customer_id,
            stripe_subscription_id,
            stripe_subscription_status,
        )
    except Exception as exc:
        logger.error("Clerk plan update failed user_id=%s error=%s", normalized_user_id, exc)
    finally:
        if conn is not None:
            conn.close()


def _clerk_private_metadata(clerk_user_id: str) -> dict[str, Any]:
    payload = _fetch_clerk_user_payload(clerk_user_id)
    if not isinstance(payload, dict):
        return {}
    private_metadata = payload.get("private_metadata")
    return private_metadata if isinstance(private_metadata, dict) else {}


def _customer_for_checkout(clerk_user_id: str, user_email: str) -> str:
    metadata = _clerk_private_metadata(clerk_user_id)
    existing_customer_id = _normalize_string(metadata.get("stripe_customer_id"))
    if existing_customer_id:
        return existing_customer_id

    customer = stripe.Customer.create(
        email=user_email,
        metadata={"clerk_user_id": clerk_user_id},
    )
    customer_id = _normalize_string(_object_get(customer, "id"))
    if not customer_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": {
                    "code": "STRIPE_CUSTOMER_CREATE_FAILED",
                    "message": "Stripe did not return a customer id.",
                }
            },
        )
    _update_clerk_user_plan(clerk_user_id, "free", stripe_customer_id=customer_id)
    return customer_id


def create_checkout_session(user_id: str, user_email: str, success_url: str, cancel_url: str) -> str:
    _require_stripe_api_key()
    price_id = _require_price_id()

    normalized_user_id = _normalize_string(user_id)
    normalized_email = _normalize_string(user_email)
    normalized_success_url = _normalize_string(success_url)
    normalized_cancel_url = _normalize_string(cancel_url)

    if not normalized_user_id:
        raise HTTPException(status_code=400, detail={"error": {"code": "CLERK_USER_MISSING", "message": "Missing Clerk user id."}})
    if not normalized_email:
        raise HTTPException(status_code=400, detail={"error": {"code": "CLERK_EMAIL_MISSING", "message": "Missing Clerk user email address."}})
    if not normalized_success_url or not normalized_cancel_url:
        raise HTTPException(status_code=400, detail={"error": {"code": "BILLING_RETURN_URL_MISSING", "message": "Billing success and cancel URLs are required."}})

    customer_id = _customer_for_checkout(normalized_user_id, normalized_email)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        customer_email=normalized_email,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=normalized_success_url,
        cancel_url=normalized_cancel_url,
        metadata={"clerk_user_id": normalized_user_id},
        subscription_data={"metadata": {"clerk_user_id": normalized_user_id}},
    )
    url = _normalize_string(_object_get(session, "url"))
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": {
                    "code": "STRIPE_CHECKOUT_URL_MISSING",
                    "message": "Stripe did not return a Checkout URL.",
                }
            },
        )
    return url


def create_portal_session(clerk_user_id: str, return_url: str) -> str:
    _require_stripe_api_key()

    normalized_user_id = _normalize_string(clerk_user_id)
    normalized_return_url = _normalize_string(return_url)
    if not normalized_user_id:
        raise HTTPException(status_code=400, detail={"error": {"code": "CLERK_USER_MISSING", "message": "Missing Clerk user id."}})
    if not normalized_return_url:
        raise HTTPException(status_code=400, detail={"error": {"code": "PORTAL_RETURN_URL_MISSING", "message": "Missing Stripe portal return URL."}})

    metadata = _clerk_private_metadata(normalized_user_id)
    customer_id = _normalize_string(metadata.get("stripe_customer_id"))
    if not customer_id:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "STRIPE_CUSTOMER_NOT_FOUND",
                    "message": "No Stripe customer is linked to this Clerk account.",
                }
            },
        )

    session = stripe.billing_portal.Session.create(customer=customer_id, return_url=normalized_return_url)
    url = _normalize_string(_object_get(session, "url"))
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": {
                    "code": "STRIPE_PORTAL_URL_MISSING",
                    "message": "Stripe did not return a Customer Portal URL.",
                }
            },
        )
    return url


def _metadata_clerk_user_id(value: Any) -> str | None:
    metadata = _object_get(value, "metadata")
    if isinstance(metadata, Mapping):
        return _normalize_string(metadata.get("clerk_user_id"))
    return None


def _retrieve_subscription(subscription_id: str | None) -> Any | None:
    normalized_subscription_id = _normalize_string(subscription_id)
    if not normalized_subscription_id:
        return None
    try:
        return stripe.Subscription.retrieve(normalized_subscription_id)
    except Exception as exc:
        logger.warning("Stripe subscription lookup failed subscription_id=%s error=%s", normalized_subscription_id, exc)
        return None


def _retrieve_customer(customer_id: str | None) -> Any | None:
    normalized_customer_id = _normalize_string(customer_id)
    if not normalized_customer_id:
        return None
    try:
        return stripe.Customer.retrieve(normalized_customer_id)
    except Exception as exc:
        logger.warning("Stripe customer lookup failed customer_id=%s error=%s", normalized_customer_id, exc)
        return None


def _resolve_clerk_user_id(event_object: Any, subscription_id: str | None, customer_id: str | None) -> tuple[str | None, Any | None, Any | None]:
    object_user_id = _metadata_clerk_user_id(event_object)
    subscription_object = event_object if _object_get(event_object, "object") == "subscription" else None
    customer_object = event_object if _object_get(event_object, "object") == "customer" else None

    if object_user_id:
        return object_user_id, subscription_object, customer_object

    if subscription_object is None and subscription_id:
        subscription_object = _retrieve_subscription(subscription_id)
    subscription_user_id = _metadata_clerk_user_id(subscription_object)
    if subscription_user_id:
        return subscription_user_id, subscription_object, customer_object

    if customer_object is None and customer_id:
        customer_object = _retrieve_customer(customer_id)
    customer_user_id = _metadata_clerk_user_id(customer_object)
    if customer_user_id:
        return customer_user_id, subscription_object, customer_object

    return None, subscription_object, customer_object


def _handle_checkout_session_completed(event_id: str, event_object: Any) -> None:
    customer_id = _normalize_string(_object_get(event_object, "customer"))
    subscription_id = _normalize_string(_object_get(event_object, "subscription"))
    clerk_user_id, subscription_object, _ = _resolve_clerk_user_id(event_object, subscription_id, customer_id)
    subscription_status = _normalize_string(_object_get(subscription_object, "status")) or "active"

    logger.info(
        "Processing Stripe event id=%s type=checkout.session.completed customer_id=%s subscription_id=%s clerk_user_id=%s",
        event_id,
        customer_id,
        subscription_id,
        clerk_user_id,
    )
    if not clerk_user_id:
        logger.error(
            "Unable to resolve Clerk user for Stripe checkout completion event id=%s customer_id=%s subscription_id=%s",
            event_id,
            customer_id,
            subscription_id,
        )
        return
    _update_clerk_user_plan(
        clerk_user_id,
        "pro",
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        stripe_subscription_status=subscription_status,
    )


def _handle_subscription_event(event_id: str, event_type: str, subscription: Any) -> None:
    customer_id = _normalize_string(_object_get(subscription, "customer"))
    subscription_id = _normalize_string(_object_get(subscription, "id"))
    subscription_status = _normalize_string(_object_get(subscription, "status"))
    clerk_user_id, _, _ = _resolve_clerk_user_id(subscription, subscription_id, customer_id)

    logger.info(
        "Processing Stripe event id=%s type=%s customer_id=%s subscription_id=%s clerk_user_id=%s",
        event_id,
        event_type,
        customer_id,
        subscription_id,
        clerk_user_id,
    )
    if not clerk_user_id:
        logger.error(
            "Unable to resolve Clerk user for Stripe subscription event id=%s type=%s customer_id=%s subscription_id=%s",
            event_id,
            event_type,
            customer_id,
            subscription_id,
        )
        return

    _update_clerk_user_plan(
        clerk_user_id,
        _status_to_plan(subscription_status),
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        stripe_subscription_status=subscription_status,
    )


def handle_webhook_event(payload: bytes, sig_header: str) -> None:
    _require_stripe_api_key()
    webhook_secret = _require_webhook_secret()

    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=webhook_secret)
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "STRIPE_SIGNATURE_INVALID", "message": "Invalid Stripe webhook signature."}},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "STRIPE_PAYLOAD_INVALID", "message": "Invalid Stripe webhook payload."}},
        ) from exc

    event_id = _normalize_string(_object_get(event, "id")) or "unknown"
    event_type = _normalize_string(_object_get(event, "type")) or "unknown"
    event_data = _object_get(event, "data") or {}
    event_object = _object_get(event_data, "object")
    customer_id = _normalize_string(_object_get(event_object, "customer"))
    subscription_id = _normalize_string(_object_get(event_object, "subscription")) or _normalize_string(_object_get(event_object, "id"))
    clerk_user_id, _, _ = _resolve_clerk_user_id(event_object, subscription_id, customer_id)

    logger.info(
        "Received Stripe event id=%s type=%s customer_id=%s subscription_id=%s clerk_user_id=%s",
        event_id,
        event_type,
        customer_id,
        subscription_id,
        clerk_user_id,
    )

    if event_type == "checkout.session.completed":
        _handle_checkout_session_completed(event_id, event_object)
        return
    if event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        _handle_subscription_event(event_id, event_type, event_object)
        return
    if event_type == "invoice.payment_failed":
        logger.warning(
            "Stripe invoice payment failed event id=%s customer_id=%s subscription_id=%s clerk_user_id=%s",
            event_id,
            customer_id,
            subscription_id,
            clerk_user_id,
        )
        return

    logger.info("Ignoring unhandled Stripe event id=%s type=%s", event_id, event_type)