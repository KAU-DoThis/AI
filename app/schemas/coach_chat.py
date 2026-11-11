"""Proposed internal chat contract. CamelCase matches the room API style."""
from typing import Annotated, Literal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints

Identifier = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128)]
Name = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
Money = Annotated[int, Field(strict=True, ge=0)]

class SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

class BlockSnapshot(SnapshotModel):
    block_id: int = Field(gt=0, strict=True, validation_alias=AliasChoices("blockId", "id"), serialization_alias="blockId")
    name: Name
    balance: Money
    type: str | None = Field(default=None, max_length=64)
    capacity: Money | None = None
    target_amount: Money | None = Field(default=None, alias="targetAmount")
    status: str | None = Field(default=None, max_length=64)
    version: int | None = Field(default=None, ge=0, strict=True)

class WaitingFundSnapshot(SnapshotModel):
    label: Name
    amount: Money

class CoachChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    user_id: int = Field(alias="userId", gt=0, strict=True)
    session_id: Identifier = Field(alias="sessionId")
    request_id: Identifier = Field(alias="requestId")
    message: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=8000)]
    # None = not provided/unknown. [] = the backend confirms no items.
    blocks: list[BlockSnapshot] | None = Field(default=None, max_length=128)
    waiting_funds: list[WaitingFundSnapshot] | None = Field(default=None, alias="waitingFunds", max_length=128)

    def snapshot(self) -> dict:
        return {"blocks": None if self.blocks is None else [b.model_dump(by_alias=True, exclude_none=True) for b in self.blocks],
                "waitingFunds": None if self.waiting_funds is None else [f.model_dump(by_alias=True) for f in self.waiting_funds]}

class CoachChatResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    request_id: str = Field(alias="requestId")
    user_id: int = Field(alias="userId")
    session_id: str = Field(alias="sessionId")
    agent_id: str = Field(alias="agentId")
    message: str
    tools_used: list[str] = Field(default_factory=list, alias="toolsUsed")
