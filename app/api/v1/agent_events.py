"""Existing Agent event/status routes, moved from main.py into api/v1/."""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.agent.manager import AgentManager
from app.core.internal_auth import require_internal_token
from app.models.agent_event import AgentEvent, EventReceipt
from app.models.agent_status import AgentStatus

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal-agent"],
    dependencies=[Depends(require_internal_token)],
)


def manager_from_request(request: Request) -> AgentManager:
    return request.app.state.agent_manager


@router.post(
    "/agent/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EventReceipt,
)
async def receive_event(
    event: AgentEvent,
    manager: AgentManager = Depends(manager_from_request),
) -> EventReceipt:
    # Intentionally no AI execution here.
    return await manager.accept(event)


@router.get("/agents/{user_id}", response_model=AgentStatus)
async def get_agent_status(
    user_id: int,
    manager: AgentManager = Depends(manager_from_request),
) -> AgentStatus:
    agent_status = await manager.get_status(user_id)
    if agent_status is None:
        raise HTTPException(status_code=404, detail="User agent not created")
    return agent_status
