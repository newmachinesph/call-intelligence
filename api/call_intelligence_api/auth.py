"""Verify Better Auth API JWTs and resolve one active local membership."""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from call_intelligence_api.db import create_domain_engine
from call_intelligence_api.models import MemberRole, Membership


class JWTSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jwt_jwks_url: str
    jwt_issuer: str
    jwt_audience: str
    jwt_allowed_algorithm: str = "ES256"


class AuthenticatedMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    organization_id: UUID
    role: MemberRole


bearer = HTTPBearer(auto_error=False)

# ponytail: per-process membership cache saves a Neon round trip per request. A deactivated member keeps
# access for at most this long (JWTs already live 15 minutes); clear it or shorten it if instant revocation matters.
MEMBERSHIP_CACHE_SECONDS = 30
_membership_cache: dict[str, tuple[float, AuthenticatedMember]] = {}


@lru_cache
def jwt_settings() -> JWTSettings:
    settings = JWTSettings()
    if settings.jwt_allowed_algorithm != "ES256":
        raise RuntimeError("Only ES256 Better Auth API tokens are supported")
    if not settings.jwt_jwks_url.startswith(settings.jwt_issuer + "/api/auth/"):
        raise RuntimeError("JWT_JWKS_URL must be on the configured issuer")
    return settings


@lru_cache
def jwks_client() -> PyJWKClient:
    return PyJWKClient(jwt_settings().jwt_jwks_url, timeout=5, lifespan=300)


def verify_api_token(token: str, settings: JWTSettings, client: PyJWKClient) -> str:
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=[settings.jwt_allowed_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject:
            raise ValueError("missing subject")
        return subject
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@lru_cache
def domain_engine():
    return create_domain_engine()


def get_current_member(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedMember:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = verify_api_token(credentials.credentials, jwt_settings(), jwks_client())
    cached = _membership_cache.get(user_id)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    with Session(domain_engine()) as session:
        memberships = session.scalars(
            select(Membership).where(Membership.user_id == user_id, Membership.active.is_(True))
        ).all()
    if len(memberships) != 1:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Active membership required")
    member = memberships[0]
    resolved = AuthenticatedMember(
        user_id=user_id,
        organization_id=member.organization_id,
        role=MemberRole(member.role),
    )
    _membership_cache[user_id] = (time.monotonic() + MEMBERSHIP_CACHE_SECONDS, resolved)
    return resolved


def require_owner(member: Annotated[AuthenticatedMember, Depends(get_current_member)]) -> AuthenticatedMember:
    if member.role is not MemberRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner role required")
    return member


def ensure_organization(member: AuthenticatedMember, resource_organization_id: UUID) -> None:
    """Hide cross-organization resources even when their IDs are known."""
    if member.organization_id != resource_organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
