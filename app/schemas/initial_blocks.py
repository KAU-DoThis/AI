"""Spring -> AI 초기 블록 생성 요청/수신 응답 계약(협의용 v1).

현재 단계에서는 데이터를 검증해서 *수신 확인*만 한다.
거래 분류, Agent 생성, AI DB 저장은 여기서 실행하지 않는다.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class InitialBlockUser(BaseModel):
    """Spring의 사용자 식별 정보. 비밀번호, 인증 토큰은 전달하지 않는다."""

    model_config = ConfigDict(extra="allow")

    user_id: int = Field(strict=True, gt=0)
    nickname: str | None = None


class InitialBlockTransaction(BaseModel):
    """분류에 필요한 최소 거래 항목.

    참고: 백엔드 ERD는 linked_account_id를 사용한다.
    예전 API 논의에서 사용한 account_id도 임시로 수신 가능하다.
    """

    model_config = ConfigDict(extra="allow")

    transaction_id: int = Field(strict=True, gt=0)
    linked_account_id: int | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices("linked_account_id", "account_id"),
    )
    occurred_at: datetime
    amount: int = Field(strict=True, gt=0, description="원 단위 양수; 방향은 direction으로 구분")
    direction: Literal["IN", "OUT"]
    merchant_or_payee: str | None = None
    description: str | None = None


class InitialBlocksRequest(BaseModel):
    """초기 블록 생성 이벤트의 본문.

    기존 블록/계좌/카테고리 구조는 아직 백엔드와 미합의라 확정하지 않는다.
    추가 필드는 보존하여 계약 협의 중의 확장을 허용한다.
    """

    model_config = ConfigDict(extra="allow")

    request_id: str = Field(min_length=1, max_length=128)
    user: InitialBlockUser
    transactions: list[InitialBlockTransaction]
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    existing_blocks: list[dict[str, Any]] = Field(default_factory=list)
    categories: list[dict[str, Any]] = Field(default_factory=list)


class InitialBlocksReceipt(BaseModel):
    """HTTP 200: 검증을 마치고 요청을 읽었음. 분석/저장/큐 적재 완료는 아님."""

    status: Literal["received"] = "received"
    request_id: str
    user_id: int
    transaction_count: int
