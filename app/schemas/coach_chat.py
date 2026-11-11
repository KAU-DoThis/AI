"""Backend -> AI chat contract; actual mutations belong to the backend."""
from typing import Annotated

from pydantic import (
    BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator,
)

# Use the exact same financial DTOs as /room. In particular, waitingFundId
# is retained and a missing label is valid. Do not modify the room endpoint.
from app.schemas.coach_room import BlockContext, WaitingFundContext

# Keep these imports usable for code that referenced the earlier class names.
BlockSnapshot = BlockContext
WaitingFundSnapshot = WaitingFundContext

ChatText = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=8000)
]
OpaqueId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]


class CoachChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    user_id: int = Field(alias="userId", strict=True, gt=0)
    # The backend owns sessionId. Validate it but never trim/rewrite it.
    session_id: str = Field(alias="sessionId", strict=True, min_length=1)
    text: ChatText
    # Optional extension. Missing IDs create new work; not synthetic deduplication.
    request_id: OpaqueId | None = Field(default=None, alias="requestId")
    # None means unknown; [] means the backend confirms an empty collection.
    blocks: list[BlockContext] | None = Field(default=None, max_length=128)
    waiting_funds: list[WaitingFundContext] | None = Field(
        default=None, alias="waitingFunds", max_length=128
    )

    @field_validator("session_id", "request_id")
    @classmethod
    def identifiers_are_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Identifier must not be blank")
        return value

    def snapshot(self) -> dict:
        return {
            "blocks": None if self.blocks is None else [
                block.model_dump(by_alias=True, exclude_none=True) for block in self.blocks
            ],
            "waitingFunds": None if self.waiting_funds is None else [
                fund.model_dump(by_alias=True, exclude_none=True) for fund in self.waiting_funds
            ],
        }


class TransferProposal(BaseModel):
    """One proposed internal budget movement, NOT an executed transfer."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    from_block_id: int = Field(alias="fromBlockId", strict=True, gt=0)
    to_block_id: int = Field(alias="toBlockId", strict=True, gt=0)
    amount: int = Field(strict=True, gt=0, description="Positive integer amount in KRW")

    @model_validator(mode="after")
    def different_blocks(self):
        if self.from_block_id == self.to_block_id:
            raise ValueError("Source and destination must be different blocks")
        return self


class CoachChatResponse(BaseModel):
    """The response consumed by Spring: no internal agent metadata."""
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strict=True, min_length=1)]
    transfer: TransferProposal | None = None
