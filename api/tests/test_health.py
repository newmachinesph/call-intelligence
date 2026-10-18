from uuid import UUID

from fastapi.testclient import TestClient

from call_intelligence_api.main import app


def test_live_response_has_safe_correlation_id() -> None:
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    UUID(response.json()["request_id"])


def test_openapi_exposes_live_contract() -> None:
    response = TestClient(app).get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert "/health/live" in response.json()["paths"]
