from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clerk_backend_api.security.types import TokenVerificationError, VerifyTokenOptions
from clerk_backend_api.security.verifytoken import verify_token_async
from fastapi import HTTPException, Request, status

from backend.app import config


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

    for metadata_key in ("metadata", "public_metadata", "private_metadata", "unsafe_metadata"):
        metadata = claims.get(metadata_key)
        if not isinstance(metadata, dict):
            continue
        metadata_role = metadata.get("role")
        if isinstance(metadata_role, str) and metadata_role.strip():
            return metadata_role.strip()

    return None


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


async def require_clerk_admin(request: Request) -> ClerkPrincipal:
    principal = await require_clerk_user(request)
    if not principal.is_admin:
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            "CLERK_ADMIN_REQUIRED",
            "Admin access required.",
        )
    return principal