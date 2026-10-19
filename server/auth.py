from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Identity:
    account_id: str
    workspace_id: str
    email: str | None = None
    display_name: str | None = None


def auth_mode() -> str:
    mode = os.getenv("AUTH_MODE", "optional").strip().lower()
    if mode not in {"disabled", "optional", "required"}:
        raise RuntimeError("AUTH_MODE must be disabled, optional, or required")
    return mode


@lru_cache(maxsize=4)
def _jwks_client(issuer: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{issuer.rstrip('/')}/.well-known/jwks.json")


def verify_bearer_token(request: Request) -> Identity | None:
    mode = auth_mode()
    authorization = request.headers.get("authorization", "")
    if not authorization:
        if mode == "required":
            raise HTTPException(status_code=401, detail="Sign in is required")
        return None
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    issuer = os.getenv("OIDC_ISSUER_URL", "").strip().rstrip("/")
    audience = os.getenv("OIDC_AUDIENCE", "authenticated").strip()
    if not issuer:
        raise HTTPException(status_code=503, detail="Authentication is not configured")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        signing_key = _jwks_client(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="The sign-in session is invalid or expired") from exc

    subject = str(claims.get("sub", "")).strip()
    if not subject:
        raise HTTPException(status_code=401, detail="The sign-in session has no user identity")
    metadata = claims.get("user_metadata") if isinstance(claims.get("user_metadata"), dict) else {}
    display_name = metadata.get("full_name") or metadata.get("name")
    return Identity(
        account_id=subject,
        workspace_id=subject,
        email=claims.get("email"),
        display_name=str(display_name) if display_name else None,
    )
