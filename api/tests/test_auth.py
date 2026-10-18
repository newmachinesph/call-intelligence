"""JWT boundary tests use synthetic identities and a local signing key."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

import call_intelligence_api.auth as auth_module
from call_intelligence_api.auth import (
    AuthenticatedMember,
    JWTSettings,
    verify_api_token,
)
from call_intelligence_api.main import app
from call_intelligence_api.models import MemberRole

SETTINGS = JWTSettings(
    _env_file=None,
    jwt_jwks_url="https://auth.example/api/auth/jwks",
    jwt_issuer="https://auth.example",
    jwt_audience="https://auth.example",
    jwt_allowed_algorithm="ES256",
)
PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())


class LocalJwks:
    def get_signing_key_from_jwt(self, _token: str):
        return type("SigningKey", (), {"key": PRIVATE_KEY.public_key()})()


def make_token(**overrides: object) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": "synthetic-user",
        "iss": SETTINGS.jwt_issuer,
        "aud": SETTINGS.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=15),
        **overrides,
    }
    return jwt.encode(claims, PRIVATE_KEY, algorithm="ES256", headers={"kid": "synthetic"})


def test_valid_api_token_subject() -> None:
    assert verify_api_token(make_token(), SETTINGS, LocalJwks()) == "synthetic-user"


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://wrong.example"},
        {"aud": "https://wrong.example"},
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        {"sub": ""},
    ],
)
def test_invalid_claims_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(HTTPException) as error:
        verify_api_token(make_token(**overrides), SETTINGS, LocalJwks())
    assert error.value.status_code == 401


def test_api_requires_bearer_token() -> None:
    response = TestClient(app).get("/api/v1/me")
    assert response.status_code == 401


def test_user_without_active_membership_is_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptySession:
        def __init__(self, _engine: object) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def scalars(self, _query: object):
            return type("Rows", (), {"all": lambda self: []})()

    monkeypatch.setattr(auth_module, "verify_api_token", lambda *_args: "foreign-user")
    monkeypatch.setattr(auth_module, "jwt_settings", lambda: SETTINGS)
    monkeypatch.setattr(auth_module, "jwks_client", lambda: LocalJwks())
    monkeypatch.setattr(auth_module, "domain_engine", lambda: object())
    monkeypatch.setattr(auth_module, "Session", EmptySession)

    with pytest.raises(HTTPException) as error:
        auth_module.get_current_member(HTTPAuthorizationCredentials(scheme="Bearer", credentials="synthetic"))
    assert error.value.status_code == 403


def test_reviewer_cannot_use_owner_action() -> None:
    reviewer = AuthenticatedMember(user_id="synthetic-user", organization_id=uuid4(), role=MemberRole.REVIEWER)
    with pytest.raises(HTTPException) as error:
        auth_module.require_owner(reviewer)
    assert error.value.status_code == 403


def test_foreign_organization_resource_is_hidden() -> None:
    reviewer = AuthenticatedMember(user_id="synthetic-user", organization_id=uuid4(), role=MemberRole.REVIEWER)
    with pytest.raises(HTTPException) as error:
        auth_module.ensure_organization(reviewer, uuid4())
    assert error.value.status_code == 404
