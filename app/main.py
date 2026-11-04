"""Fintris AI server v0: receive backend events and create/reuse UserAgents."""

import os
from contextlib import asynccontextmanager
from hmac import compare_digest

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status

from app.manager import AgentManager
from app.models import AgentEvent, AgentStatus, EventReceipt


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("FINTRIS_INTERNAL_TOKEN"):
        raise RuntimeError("Set FINTRIS_INTERNAL_TOKEN for Spring -> AI authentication")
    app.state.agent_manager = AgentManager()
    yield


app = FastAPI(
    title="Fintris AI Server",
    version="0.1.01",
    lifespan=lifespan,
)


def authenticate_internal(
    x_internal_token: str | None = Header(default=None),
) -> None:
    configured = os.environ.get("FINTRIS_INTERNAL_TOKEN", "")
    if not configured or x_internal_token is None or not compare_digest(
        x_internal_token, configured
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal service token",
        )


def manager_from_request(request: Request) -> AgentManager:
    return request.app.state.agent_manager


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent_runtime": "stub"}


@app.post(
    "/internal/v1/agent/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EventReceipt,
    dependencies=[Depends(authenticate_internal)],
)
async def receive_event(
    event: AgentEvent,
    manager: AgentManager = Depends(manager_from_request),
) -> EventReceipt:
    # Deliberately do not run any AI here.
    return await manager.accept(event)


@app.get(
    "/internal/v1/agents/{user_id}",
    response_model=AgentStatus,
    dependencies=[Depends(authenticate_internal)],
)
async def get_agent_status(
    user_id: int,
    manager: AgentManager = Depends(manager_from_request),
) -> AgentStatus:
    agent_status = await manager.get_status(user_id)
    if agent_status is None:
        raise HTTPException(status_code=404, detail="User agent not created")
    return agent_status
