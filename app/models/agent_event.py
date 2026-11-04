"""Backend -> AI Agent event API contract (proposed; Spring DTO not finalized)."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    INITIAL_BLOCKS = "initial_blocks"
    CHAT_MESSAGE = "chat_message"
    MONTHLY_REPORT = "monthly_report"


class AgentEvent(BaseModel):
    """A message from trusted Spring backend for one Fintris user."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    user_id: int = Field(gt=0)
    event_type: EventType
    payload: dict[str, Any]

    @field_validator("payload")
    @classmethod
    def payload_is_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        # More specific DTOs will be agreed with the Spring team later.
        return value


class EventReceipt(BaseModel):
    event_id: str
    user_id: int
    agent_id: str
    agent_created: bool
    duplicate: bool
    status: str = "queued"
    pending_events: int
