"""HTTP entry point for the domain service."""

import asyncio
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text as sql_text

from call_intelligence_api.address_routes import router as address_router
from call_intelligence_api.auth import (
    AuthenticatedMember,
    domain_engine,
    get_current_member,
)
from call_intelligence_api.call_routes import router as call_router
from call_intelligence_api.chat_routes import router as chat_router
from call_intelligence_api.connection_routes import router as connection_router
from call_intelligence_api.crm_routes import router as crm_router
from call_intelligence_api.deletion import router as deletion_router
from call_intelligence_api.dispatcher import reconcile_forever
from call_intelligence_api.errors import register_error_handlers
from call_intelligence_api.insight_chat import router as insight_chat_router
from call_intelligence_api.insight_routes import router as insight_router
from call_intelligence_api.task_routes import router as task_router
from call_intelligence_api.telemetry import configure_telemetry
from call_intelligence_api.workflow import register_workflow


class LiveResponse(BaseModel):
    status: str
    request_id: str


class MeResponse(BaseModel):
    user_id: str
    organization_id: str
    role: str
    request_id: str


@asynccontextmanager
async def lifespan(_app: FastAPI):
    reconciler = asyncio.create_task(reconcile_forever())
    try:
        yield
    finally:
        reconciler.cancel()
        try:
            await reconciler
        except asyncio.CancelledError:
            pass


configure_telemetry()

app = FastAPI(
    title="Call Intelligence API",
    version="0.1.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(address_router)
app.include_router(call_router)
app.include_router(chat_router)
app.include_router(connection_router)
app.include_router(crm_router)
app.include_router(deletion_router)
app.include_router(insight_router)
app.include_router(insight_chat_router)
app.include_router(task_router)
register_workflow(app)
register_error_handlers(app)


@app.get("/health/live", response_model=LiveResponse, tags=["health"])
def live() -> LiveResponse:
    return LiveResponse(status="ok", request_id=str(uuid4()))


@app.get("/health/ready", response_model=LiveResponse, tags=["health"])
def ready() -> LiveResponse:
    try:
        with domain_engine().connect() as connection:
            connection.execute(sql_text("SELECT 1"))
    except Exception as exc:  # readiness never exports database error details
        raise HTTPException(status_code=503, detail="Domain database unavailable") from exc
    return LiveResponse(status="ready", request_id=str(uuid4()))


@app.get("/api/v1/me", response_model=MeResponse, tags=["identity"])
def me(member: Annotated[AuthenticatedMember, Depends(get_current_member)]) -> MeResponse:
    return MeResponse(
        user_id=member.user_id,
        organization_id=str(member.organization_id),
        role=member.role.value,
        request_id=str(uuid4()),
    )
