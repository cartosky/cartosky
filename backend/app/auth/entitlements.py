from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, status

from backend.app import config
from backend.app.auth.clerk import ClerkPrincipal
from backend.app.config.protected_products import PROTECTED_PRODUCTS


ACTIVE_PAID_PLANS = {"pro"}


def billing_enabled() -> bool:
    return config.billing_enabled()


def pro_gating_enabled() -> bool:
    return config.pro_gating_enabled()


def get_required_feature_for_product(product_id: str) -> str | None:
    product = PROTECTED_PRODUCTS.get(str(product_id or "").strip().lower())
    if product is None:
        return None
    return product.get("required_feature")


def protected_product_ids() -> tuple[str, ...]:
    return tuple(PROTECTED_PRODUCTS.keys())


def _normalize_feature_slug(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _claims_from_user_session(user_session: ClerkPrincipal | Mapping[str, Any] | None) -> Mapping[str, Any]:
    if isinstance(user_session, ClerkPrincipal):
        return user_session.claims
    if isinstance(user_session, Mapping):
        return user_session
    return {}


def _plan_from_claims(claims: Mapping[str, Any]) -> str:
    plan = claims.get("plan")
    normalized_plan = _normalize_feature_slug(plan)
    if normalized_plan:
        return normalized_plan

    metadata = claims.get("metadata")
    if isinstance(metadata, Mapping):
        metadata_plan = _normalize_feature_slug(metadata.get("plan"))
        if metadata_plan:
            return metadata_plan

    return "free"


def get_user_features(user_session: ClerkPrincipal | Mapping[str, Any] | None) -> set[str]:
    claims = _claims_from_user_session(user_session)
    plan = _plan_from_claims(claims)
    if plan not in ACTIVE_PAID_PLANS:
        return set()
    return {product["required_feature"] for product in PROTECTED_PRODUCTS.values() if _normalize_feature_slug(product.get("required_feature"))}


def can_access_feature(user_session: ClerkPrincipal | Mapping[str, Any] | None, feature_slug: str) -> bool:
    if not pro_gating_enabled():
        return True
    normalized = _normalize_feature_slug(feature_slug)
    if not normalized:
        return True
    return normalized in get_user_features(user_session)


def can_access_product(user_session: ClerkPrincipal | Mapping[str, Any] | None, product_id: str) -> bool:
    required_feature = get_required_feature_for_product(product_id)
    if required_feature is None:
        return True
    return can_access_feature(user_session, required_feature)


def require_product_access(user_session: ClerkPrincipal | Mapping[str, Any] | None, product_id: str) -> None:
    if can_access_product(user_session, product_id):
        return
    product = PROTECTED_PRODUCTS.get(str(product_id or "").strip().lower(), {})
    label = product.get("label") or str(product_id or "").strip() or "This product"
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": {
                "code": "PRO_FEATURE_REQUIRED",
                "message": f"{label} requires an active CartoSky Pro entitlement.",
                "required_feature": get_required_feature_for_product(product_id),
            }
        },
    )
