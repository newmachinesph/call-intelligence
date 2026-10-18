"""Error envelope mapping: codes, retryability, safe validation summaries and no leaked internals."""

from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from call_intelligence_api.auth import AuthenticatedMember, get_current_member
from call_intelligence_api.errors import register_error_handlers
from call_intelligence_api.main import app
from call_intelligence_api.models import MemberRole


def _app_raising(exc: Exception) -> TestClient:
    local = FastAPI()
    register_error_handlers(local)

    @local.get("/boom")
    def boom() -> None:
        raise exc

    return TestClient(local, raise_server_exceptions=False)


def test_missing_token_keeps_auth_header_and_envelope() -> None:
    response = TestClient(app).get("/api/v1/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    error = response.json()["error"]
    assert error["code"] == "unauthorized" and error["retryable"] is False
    UUID(error["request_id"])


def test_unknown_route_is_not_found_envelope() -> None:
    error = TestClient(app).get("/api/v1/no-such-route").json()["error"]
    assert error["code"] == "not_found"


def test_validation_error_lists_fields_without_echoing_input() -> None:
    member = AuthenticatedMember(user_id="synthetic", organization_id=uuid4(), role=MemberRole.OWNER)
    app.dependency_overrides[get_current_member] = lambda: member
    try:
        response = TestClient(app).get("/api/v1/calls", params={
            "limit": "9999", "call_at_from": "2026-01-01T00:00:00", "rep_id": "secret-rep-value"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    fields = [item["field"] for item in error["fields"]]
    assert "query.limit" in fields and any(field.startswith("query.rep_id") for field in fields)
    assert "secret-rep-value" not in response.text and "9999" not in response.text


def test_authored_validator_message_is_surfaced() -> None:
    member = AuthenticatedMember(user_id="synthetic", organization_id=uuid4(), role=MemberRole.OWNER)
    app.dependency_overrides[get_current_member] = lambda: member
    try:
        response = TestClient(app).get("/api/v1/calls", params={"call_at_from": "2026-01-01T00:00:00"})
    finally:
        app.dependency_overrides.clear()
    assert any(item.get("message") == "Date filters need a UTC offset" for item in response.json()["error"]["fields"])


def test_provider_errors_are_retryable_and_detail_becomes_message() -> None:
    response = _app_raising(HTTPException(status_code=503, detail="Private audio storage is unavailable")).get("/boom")
    assert response.json()["error"] == {**response.json()["error"], "code": "provider_unavailable",
                                         "retryable": True, "message": "Private audio storage is unavailable"}


def test_dict_detail_refines_code_and_context() -> None:
    call_id = str(uuid4())
    detail = {"code": "address_validation_not_configured", "message": "Address checks are off", "call_id": call_id,
              "internal": "dropped"}
    error = _app_raising(HTTPException(status_code=409, detail=detail)).get("/boom").json()["error"]
    assert error["code"] == "address_validation_not_configured" and error["call_id"] == call_id
    assert "internal" not in error


def test_unhandled_exception_is_generic_500() -> None:
    response = _app_raising(RuntimeError("transcript says private customer detail")).get("/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "private customer detail" not in response.text


def test_concurrent_duplicate_write_is_a_conflict_not_a_500() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import IntegrityError

    from call_intelligence_api.errors import register_error_handlers

    app = FastAPI()
    register_error_handlers(app)

    @app.post("/race")
    def race() -> None:
        raise IntegrityError("INSERT", {}, Exception("uq_message_client_id"))

    response = TestClient(app, raise_server_exceptions=False).post("/race")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert "uq_message_client_id" not in response.text
