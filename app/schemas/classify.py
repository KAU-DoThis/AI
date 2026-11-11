"""AI 서버 입출력 스키마.

입력·출력 칸 이름·값은 BE `db/schema.sql`의 `transactions` 기준
(direction IN/OUT, status, classify_stage, confidence, classified_by, excluded).
extra="ignore": 정의 안 된 칸(source, 계좌번호, 송금 메모 등)은 받자마자 버린다. 분류·sLM에 닿지 않고,
로그에는 칸 이름만 남긴다(값은 안 남김). BE 형식이 조금 달라도 요청 전체가 422로 막히지 않게.
JSON 칸 이름은 BE API 명세에 맞춰 camelCase (docs/BE_AI서버_연동명세.md). 파이썬 안에서는 snake_case 그대로 쓴다.
"""

import logging
from collections import Counter
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.modules.classifier.categories import CategoryType

logger = logging.getLogger("fintris.ai.schema")

# 안 오면 오류 없이 판정만 틀어지는 칸. 칸 자체가 없으면 경고한다 (값이 null인 건 정상일 수 있음)
_IMPORTANT_FIELDS = ("account_type", "is_canceled", "is_own_account_transfer")
_MAX_LOGGED_NAMES = 20

# BE context_questions.type 값 (기능명세서 F-15 질문 대상)
QuestionType = Literal["UNKNOWN_ITEM", "LATE_NIGHT_FOOD", "PERSONAL_TRANSFER", "LOW_CONFIDENCE"]
# BE transactions.status 중 AI가 정하는 값. PROVISIONAL(빠른 입력)은 BE가 붙인다
Status = Literal["CONFIRMED", "NEEDS_REVIEW"]

# 밖(JSON)은 camelCase, 안(파이썬)은 snake_case. 테스트·스크립트가 snake_case로 만들 수 있게 이름도 받는다
_CAMEL = ConfigDict(alias_generator=to_camel, validate_by_alias=True, validate_by_name=True, serialize_by_alias=True)


class TransactionIn(BaseModel):
    model_config = ConfigDict(**_CAMEL, extra="ignore")

    transaction_id: int
    occurred_at: datetime
    amount: int = Field(description="원화. 음수는 취소 전표로 보고 소비에서 뺀다 (한 건 때문에 전체가 422로 막히지 않게)")
    direction: Literal["OUT", "IN"] = Field(description="OUT: 출금·결제, IN: 입금")
    merchant_or_payee: str | None = Field(
        default=None, max_length=255, description="가맹점명 또는 받는 사람. 은행 적요가 비면 null (DB도 NULL 허용)"
    )
    account_type: Literal["CARD", "BANK"] | None = Field(
        default=None, description="linked_accounts.type. BANK 출금이 사전에 없으면 개인 송금으로 본다"
    )
    merchant_type: str | None = Field(
        default=None, max_length=100, description="카드 가맹점 업종 (CODEF resMemberStoreType). 은행·없음이면 null. 아직 분류에 안 씀"
    )
    is_canceled: bool = False
    is_own_account_transfer: bool = Field(
        default=False, description="BE가 본인 계좌 목록으로 판정한 본인 계좌 간 이체"
    )


def _warn_input_gaps(rows: list[Any]) -> None:
    """버린 칸과 빠진 중요 칸을 요청당 한 번씩 경고한다. 칸 이름과 건수만 남기고 값은 남기지 않는다."""
    known: set[str] = set()
    for name, field in TransactionIn.model_fields.items():
        known |= {name, field.alias or name}

    unknown: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue  # 형식 오류는 뒤의 검증이 422로 돌려준다
        unknown.update(str(key)[:40] for key in row if key not in known)
        for name in _IMPORTANT_FIELDS:
            if name not in row and to_camel(name) not in row:
                missing[to_camel(name)] += 1

    if unknown:
        logger.warning("분류 입력: 모르는 칸을 버림 %s", unknown.most_common(_MAX_LOGGED_NAMES))
    if missing:
        logger.warning("분류 입력: 판정에 필요한 칸이 빠짐 %s", missing.most_common())


class ClassifyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")  # 칸이 transactions 하나라 camelCase 변환 불필요

    transactions: list[TransactionIn] = Field(min_length=1, max_length=1000)

    @model_validator(mode="before")
    @classmethod
    def _report_input_gaps(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("transactions"), list):
            _warn_input_gaps(data["transactions"])
        return data


class ClassificationOut(BaseModel):
    model_config = _CAMEL

    transaction_id: int
    occurred_at: datetime = Field(description="거래 날짜·시각. 요청에 온 값 그대로 (은행이 시각을 안 주면 00:00:00)")
    category_code: str | None = Field(description="None = 확정 못 함(질문·다음 단계) 또는 카테고리 없는 거래(입금·카드대금)")
    classify_stage: int | None = Field(description="1 사전·규칙, 2 퍼지, 3 임베딩, 4 Jeff. None = 아직 못 정함")
    confidence: float = Field(ge=0, le=1)
    classified_by: Literal["AUTO"] = "AUTO"
    status: Status = Field(description="NEEDS_REVIEW = 카테고리를 못 정해 사용자 확인 필요 (화면 '확인 필요')")
    excluded: bool = Field(description="소비에서 제외 (입금·카드대금·본인 이체)")
    needs_question: bool
    question_type: QuestionType | None = None
    question_options: list[str] = Field(
        default_factory=list,
        description="질문 시트 선택지. 카테고리 코드, LATE_NIGHT_FOOD만 context_tag 값. '직접 적기'는 FE가 붙인다",
    )
    matched_keyword: str | None = None


class ClassifyResponse(BaseModel):
    results: list[ClassificationOut]


class RecurringMatch(BaseModel):
    """v4 고정지출형 `rule.matchCondition`. `linkedAccountId`는 AI가 모르는 값이라 BE가 붙인다."""

    model_config = _CAMEL

    payee: str
    amount_mode: Literal["EXACT", "RANGE"]
    min_amount: int
    max_amount: int
    day_window: int


class RecurringRule(BaseModel):
    """v4 고정지출형 `rule` (alerts는 v4 예시 기본값)."""

    model_config = _CAMEL

    pay_day: int = Field(ge=1, le=31)
    pay_amount: int
    match_condition: RecurringMatch
    alerts: dict[str, bool] = Field(
        default_factory=lambda: {"shortageBefore3Days": True, "askFillFromOther": True, "paidConfirmed": False}
    )


class RecurringCandidate(BaseModel):
    """초기 블록 구성안의 FIXED 후보 (v4 `GET /blocks/proposals` proposals[]). icon·color는 BE·FE가 정한다."""

    model_config = _CAMEL

    proposal_key: str
    type: Literal["FIXED"] = "FIXED"
    name: str
    category_code: str | None
    balance: int = Field(description="제안 금액 = 마지막 납부액. 확정 전엔 저장 안 함")
    rule: RecurringRule
    found_from: Literal["TRANSACTIONS"] = "TRANSACTIONS"
    reason: str = Field(description="템플릿 문장. 나중에 문장 생성으로 바꿀 수 있음")
    occurrence_count: int
    transaction_ids: list[int]
    last_paid_at: date
    next_expected_date: date


class InitialClassifyRequest(ClassifyRequest):
    """첫 계좌 연동의 3개월 전체. 반복지출은 전체를 한 번에 봐야 찾을 수 있어 나눠 보내면 안 된다."""

    transactions: list[TransactionIn] = Field(min_length=1, max_length=5000)


class InitialClassifyResponse(BaseModel):
    results: list[ClassificationOut]
    recurring: list[RecurringCandidate] = Field(description="고정지출 후보. 금액 큰 순서")


class CategoryOut(BaseModel):
    model_config = _CAMEL

    code: str
    name: str
    category_type: CategoryType
    display_order: int
