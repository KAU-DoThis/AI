"""Backend -> AI contract for the coach room introduction."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


NonEmptyText = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]


class BlockContext(BaseModel):
    """The room needs a small subset of the backend's full block DTO."""

    model_config = ConfigDict(extra="ignore")

    block_id: int = Field(alias="blockId", strict=True, gt=0)
    name: NonEmptyText
    balance: int = Field(strict=True, description="Current block balance in KRW")
    type: NonEmptyText | None = None
    capacity: int | None = Field(default=None, strict=True, ge=0)
    target_amount: int | None = Field(
        default=None, alias="targetAmount", strict=True, ge=0
    )
    status: NonEmptyText | None = None
    status_label: str | None = Field(default=None, alias="statusLabel", strict=True)


class WaitingFundContext(BaseModel):
    """Accept the backend's waiting-fund records, not a single total amount."""

    model_config = ConfigDict(extra="ignore")

    waiting_fund_id: int = Field(alias="waitingFundId", strict=True, gt=0)
    amount: int = Field(strict=True, ge=0, description="Remaining amount in KRW")
    label: str | None = Field(default=None, strict=True)
    description: str | None = Field(default=None, strict=True)


class CoachRoomRequest(BaseModel):
    """A backend-issued session and its current financial context.

    Both arrays are required: an empty snapshot is different from a missing one.
    Extra backend DTO fields are accepted but discarded before generation.
    """

    model_config = ConfigDict(extra="ignore")

    user_id: int = Field(alias="userId", strict=True, gt=0)
    session_id: str = Field(alias="sessionId", strict=True, min_length=1)
    blocks: list[BlockContext]
    waiting_funds: list[WaitingFundContext] = Field(alias="waitingFunds")

    @field_validator("session_id")
    @classmethod
    def session_id_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sessionId must not be blank")
        # The backend owns this opaque identifier; do not rewrite it.
        return value


class CoachRoomResponse(BaseModel):
    """Only the visible greeting and three distinct quick-action texts."""

    model_config = ConfigDict(extra="forbid", strict=True)

    greeting: NonEmptyText
    suggestions: list[NonEmptyText] = Field(min_length=3, max_length=3)

    @field_validator("suggestions")
    @classmethod
    def suggestions_are_distinct(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("suggestions must contain three distinct texts")
        return value
