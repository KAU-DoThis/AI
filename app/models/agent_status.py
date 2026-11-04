"""Status representation of a single user's in-memory Agent stub."""

from pydantic import BaseModel


class AgentStatus(BaseModel):
    user_id: int
    agent_id: str
    created_at: str
    pending_events: int
    status: str = "idle_stub"
