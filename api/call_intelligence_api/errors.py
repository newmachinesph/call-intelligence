"""Central mapping from exceptions to the `{"error": {...}}` envelope used by every non-2xx response."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict",
    413: "payload_too_large", 422: "invalid_request", 429: "rate_limited",
    502: "provider_unavailable", 503: "provider_unavailable", 500: "internal_error",
}
RETRYABLE = {429, 502, 503}
# Keys a route may pass in a dict `detail` to refine the envelope, e.g. {"code": "...", "message": "..."}.
DETAIL_KEYS = {"code", "message", "retryable", "call_id", "available_stages"}


def error_body(status_code: int, message: str, **extra: Any) -> dict[str, Any]:
    fallback = "bad_request" if status_code < 500 else "internal_error"
    error = {"code": CODES.get(status_code, fallback), "message": message,
             "retryable": status_code in RETRYABLE, "request_id": str(uuid4())}
    error.update({key: value for key, value in extra.items() if key in DETAIL_KEYS and value is not None})
    return {"error": error}


def http_error_body(status_code: int, detail: Any) -> dict[str, Any]:
    if isinstance(detail, dict):
        extra = {key: value for key, value in detail.items() if key != "message"}
        return error_body(status_code, str(detail.get("message") or "Request failed"), **extra)
    return error_body(status_code, detail if isinstance(detail, str) and detail else "Request failed")


def validation_summary(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Field locations and error types only; never echo submitted values."""
    summary = []
    for item in errors[:20]:
        field = ".".join(str(part) for part in item.get("loc", ()))[:200]
        entry = {"field": field, "type": str(item.get("type", "invalid"))}
        # Messages we author in validators are safe; library messages can quote input fragments.
        if entry["type"] == "value_error":
            entry["message"] = str(item.get("msg", "")).removeprefix("Value error, ")[:200]
        summary.append(entry)
    return summary


async def _http_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(http_error_body(exc.status_code, exc.detail), status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = validation_summary(list(exc.errors()))
    body = error_body(422, "Request validation failed")
    body["error"]["fields"] = fields
    return JSONResponse(body, status_code=422)


async def _unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
    body = error_body(500, "Unexpected server error")
    logger.error("unhandled_error request_id=%s error_type=%s", body["error"]["request_id"], type(exc).__name__)
    return JSONResponse(body, status_code=500)


async def _integrity_handler(_request: Request, _exc: IntegrityError) -> JSONResponse:
    # A unique/idempotency constraint lost a race with a concurrent identical request.
    return JSONResponse(error_body(409, "A concurrent request already saved this change; refresh and retry"),
                        status_code=409)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(IntegrityError, _integrity_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
