from __future__ import annotations

import http.client
import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from clerk_backend_api.security.types import TokenVerificationError, VerifyTokenOptions
from clerk_backend_api.security.verifytoken import verify_token_async
from fastapi import HTTPException, Request, status

from backend.app import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClerkPrincipal:
    user_id: str
    claims: dict[str, Any]
    token: str

    @property
    def role(self) -> str | None:
        return clerk_role_from_claims(self.claims)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True)
class ClerkUserProfile:
    display_name: str | None
    email_address: str | None


def _auth_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "").strip()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "CLERK_AUTH_MISSING",
            "Missing Clerk bearer token.",
        )
    return token.strip()


def clerk_role_from_claims(claims: dict[str, Any]) -> str | None:
    role = claims.get("role")
    if isinstance(role, str) and role.strip():
        return role.strip()

    for metadata_key in ("metadata", "public_metadata", "private_metadata"):
        metadata = claims.get(metadata_key)
        if not isinstance(metadata, dict):
            continue
        metadata_role = metadata.get("role")
        if isinstance(metadata_role, str) and metadata_role.strip():
            return metadata_role.strip()

    return None


def _clean_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _primary_email_from_clerk_user(payload: dict[str, Any]) -> str | None:
    primary_email_id = _clean_string(payload.get("primary_email_address_id"))
    email_addresses = payload.get("email_addresses")
    if isinstance(email_addresses, list):
        fallback_email: str | None = None
        for item in email_addresses:
            if not isinstance(item, dict):
                continue
            email = _clean_string(item.get("email_address"))
            if not email:
                continue
            if fallback_email is None:
                fallback_email = email
            if primary_email_id and item.get("id") == primary_email_id:
                return email
        return fallback_email
    return None


def clerk_user_profile_from_payload(payload: dict[str, Any]) -> ClerkUserProfile:
    first_name = _clean_string(payload.get("first_name"))
    last_name = _clean_string(payload.get("last_name"))
    full_name = " ".join(part for part in (first_name, last_name) if part)
    username = _clean_string(payload.get("username"))
    email_address = _primary_email_from_clerk_user(payload)
    display_name = full_name or username or email_address
    return ClerkUserProfile(display_name=display_name, email_address=email_address)


def fetch_clerk_user_profile(user_id: str) -> ClerkUserProfile | None:
    secret_key = config.clerk_secret_key()
    normalized_user_id = user_id.strip()
    if not secret_key or not normalized_user_id:
        return None

    conn: http.client.HTTPSConnection | None = None
    try:
        conn = http.client.HTTPSConnection("api.clerk.com", timeout=5)
        conn.request(
            "GET",
            f"/v1/users/{quote(normalized_user_id, safe='')}",
            headers={"Authorization": f"Bearer {secret_key}"},
        )
        response = conn.getresponse()
        body = response.read()
        if response.status == 404:
            return None
        if response.status < 200 or response.status >= 300:
            logger.warning("Clerk user profile lookup failed status=%s", response.status)
            return None
        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict):
            return None
        return clerk_user_profile_from_payload(decoded)
    except Exception as exc:
        logger.warning("Clerk user profile lookup failed: %s", exc)
        return None
    finally:
        if conn is not None:
            conn.close()


def _verify_options() -> VerifyTokenOptions:
    secret_key = config.clerk_secret_key()
    if not secret_key:
        raise _auth_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "CLERK_SECRET_KEY_MISSING",
            "Clerk auth is not configured on the API server.",
        )

    return VerifyTokenOptions(
        secret_key=secret_key,
        audience=config.clerk_jwt_audience() or None,
        authorized_parties=config.clerk_authorized_parties() or None,
    )


async def require_clerk_user(request: Request) -> ClerkPrincipal:
    token = _bearer_token(request)
    try:
        claims = await verify_token_async(token, _verify_options())
    except TokenVerificationError as exc:
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "CLERK_TOKEN_INVALID",
            str(exc) or "Invalid Clerk token.",
        ) from exc

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "CLERK_SUB_MISSING",
            "Verified Clerk token is missing a user subject.",
        )

    return ClerkPrincipal(user_id=user_id.strip(), claims=claims, token=token)


async def maybe_clerk_user(request: Request) -> ClerkPrincipal | None:
    if not request.headers.get("authorization", "").strip():
        return None
    return await require_clerk_user(request)


async def require_clerk_admin(request: Request) -> ClerkPrincipal:
    principal = await require_clerk_user(request)
    if not principal.is_admin:
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            "CLERK_ADMIN_REQUIRED",
            "Admin access required.",
        )
    return principal
