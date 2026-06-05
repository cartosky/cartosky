from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, status

from backend.app import config
from backend.app.auth.clerk import ClerkPrincipal
from backend.app.config.protected_products import PROTECTED_PRODUCTS


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


def _features_from_scoped_string(raw: str) -> set[str]:
    features: set[str] = set()
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        scope, separator, value = item.partition(":")
        if separator and scope in {"u", "o", "ou", "uo", "user", "org"}:
            slug = _normalize_feature_slug(value)
        elif separator:
            slug = None
        else:
            slug = _normalize_feature_slug(item)
        if slug:
            features.add(slug)
    return features


def _features_from_claim_value(value: Any) -> set[str]:
    if isinstance(value, str):
        return _features_from_scoped_string(value)
    if isinstance(value, (list, tuple, set)):
        features: set[str] = set()
        for item in value:
            features.update(_features_from_claim_value(item))
        return features
    if isinstance(value, Mapping):
        features: set[str] = set()
        for key in ("features", "feature", "slugs", "enabled", "user", "org"):
            if key in value:
                features.update(_features_from_claim_value(value.get(key)))
        slug = _normalize_feature_slug(value.get("slug"))
        if slug:
            features.add(slug)
        return features
    return set()


def _claims_from_user_session(user_session: ClerkPrincipal | Mapping[str, Any] | None) -> Mapping[str, Any]:
    if isinstance(user_session, ClerkPrincipal):
        return user_session.claims
    if isinstance(user_session, Mapping):
        return user_session
    return {}


def get_user_features(user_session: ClerkPrincipal | Mapping[str, Any] | None) -> set[str]:
    claims = _claims_from_user_session(user_session)
    return _features_from_claim_value(claims.get("fea"))


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
