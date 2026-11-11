"""반복지출(고정지출) 후보 찾기 — 초기 블록 구성안의 FIXED 후보 (API 명세 v4 `GET /blocks/proposals`).

조건 (v4 비고): 같은 받는 곳에 3회 이상, 금액 편차 10% 이내, 날짜 편차 3일 이내.
여기에 '한 달에 한 번'을 더한다: 같은 가게 같은 값 커피처럼 자주 쓰는 소비가 잡히지 않게.
- 금액이 중앙값 ±10% 안인 거래만 본다 (가끔 끼는 다른 금액 결제는 무시)
- 그 거래들이 한 달에 하나씩, 연속된 달에 있어야 한다
- 날짜(일)가 기준일 ±3일 안이어야 한다 (월말·월초는 이어서 본다)

계산만 한다(LLM 없음). 저장·블록 금액 확정은 BE, 이유 문장은 지금 템플릿(나중에 문장 생성으로 바꿀 수 있음).
11/09 회의: 반복지출 찾기는 AI 담당. 입구는 첫 연동용 `POST /v1/classify/initial` (app/classify_api.py).
"""

import calendar
import re
from collections import Counter, defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from statistics import median

from app.modules.classifier.normalize import compact, normalize
from app.schemas.classify import (
    ClassificationOut,
    RecurringCandidate,
    RecurringMatch,
    RecurringRule,
    TransactionIn,
)

MIN_OCCURRENCES = 3
AMOUNT_TOLERANCE = 0.10
DAY_WINDOW = 3
# 사람 이름으로 매달 이만큼 이상 보내면 월세로 본다. 집주인 개인 계좌 이체는 통장에 이름만 찍혀서 패턴밖에 단서가 없다
# (자취생 확인 11/10). 20만 원은 임시값: 원룸 월세보다 낮고, 친구 더치페이·소액 송금보다 높게
RENT_MIN_AMOUNT = 200_000

# 카테고리별 블록 이름. 없으면 받는 곳 이름을 쓴다 (구독은 여러 개라 서비스 이름 그대로)
_NAMES = {"HOUSING": "월세/관리비", "TELECOM": "통신비"}
# 은행이 가린 사람 이름 ('김OO', '김*수'). 보험사처럼 송금으로 분류된 회사에 '님께'를 붙이지 않게
_MASKED_PERSON = re.compile(r"^[가-힣][*Oo○0]+[가-힣]?$")


@dataclass(frozen=True)
class _Tx:
    tx: TransactionIn
    category_code: str | None
    person: bool = False  # 개인 송금으로 분류됨 (월세를 집주인에게 보내는 경우 등)

    @property
    def day(self) -> date:
        return self.tx.occurred_at.date()


def find_recurring(transactions: list[TransactionIn], results: list[ClassificationOut],
                   brand_keywords: Collection[str] = ()) -> list[RecurringCandidate]:
    """분류까지 끝난 거래로 고정지출 후보를 만든다. 금액 큰 순서.

    brand_keywords: 사전의 브랜드 단어 (`MerchantDictionary.brand_keywords()`). 이 단어로 걸린 거래는
    표기가 달라도('NETFLIX.COM'·'NETFLIX') 한 묶음. 나머지는 정규화한 가게 이름으로 묶는다.
    """
    by_id = {r.transaction_id: r for r in results}
    groups: dict[str, list[_Tx]] = defaultdict(list)
    for tx in transactions:
        r = by_id.get(tx.transaction_id)
        if not _is_candidate(tx, r):
            continue
        brand = r.matched_keyword if r and r.matched_keyword in brand_keywords else None
        key = brand or compact(normalize(tx.merchant_or_payee or ""))
        if key:
            person = bool(r and r.question_type == "PERSONAL_TRANSFER"
                          and _MASKED_PERSON.match((tx.merchant_or_payee or "").strip()))
            groups[key].append(_Tx(tx, r.category_code if r else None, person))

    found = [c for g in groups.values() if (c := _detect(g))]
    found.sort(key=lambda c: (-c.rule.pay_amount, c.name))
    return [c.model_copy(update={"proposal_key": f"fixed-{i}"}) for i, c in enumerate(found, 1)]


def _is_candidate(tx: TransactionIn, r: ClassificationOut | None) -> bool:
    # 소비에서 뺀 것(카드대금·현금 인출·본인 이체·적금)과 취소는 고정지출 블록 대상이 아니다
    if tx.direction != "OUT" or tx.amount <= 0 or tx.is_canceled or tx.is_own_account_transfer:
        return False
    return not (r and r.excluded)


def _detect(group: list[_Tx]) -> RecurringCandidate | None:
    if len(group) < MIN_OCCURRENCES:
        return None
    mid = median(t.tx.amount for t in group)
    core = sorted((t for t in group if abs(t.tx.amount - mid) <= mid * AMOUNT_TOLERANCE), key=lambda t: t.tx.occurred_at)
    if len(core) < MIN_OCCURRENCES:
        return None

    months = [(t.day.year, t.day.month) for t in core]
    if len(set(months)) != len(months):  # 한 달에 두 번 이상 = 고정지출 아님 (자주 가는 가게)
        return None
    if any(_month_index(b) - _month_index(a) != 1 for a, b in zip(months, months[1:])):
        return None  # 빠진 달이 있음 (격월·가끔)

    pay_day = _center_day([t.day.day for t in core])
    if any(_day_distance(t.day.day, pay_day) > DAY_WINDOW for t in core):
        return None

    last = core[-1]
    pay_amount = last.tx.amount
    lo, hi = _round_won(pay_amount * (1 - AMOUNT_TOLERANCE)), _round_won(pay_amount * (1 + AMOUNT_TOLERANCE))
    code = _most_common([t.category_code for t in core])
    payee = (last.tx.merchant_or_payee or "").strip()
    rent = code is None and all(t.person for t in core) and pay_amount >= RENT_MIN_AMOUNT
    if rent:
        code = "HOUSING"
    return RecurringCandidate(
        proposal_key="",
        name="월세" if rent else _NAMES.get(code or "", payee),
        category_code=code,
        balance=pay_amount,
        rule=RecurringRule(
            pay_day=pay_day,
            pay_amount=pay_amount,
            match_condition=RecurringMatch(
                payee=payee, amount_mode="RANGE",
                min_amount=lo, max_amount=hi, day_window=DAY_WINDOW,
            ),
        ),
        reason=_reason(len(core), pay_day, payee, pay_amount, last.tx.account_type, last.person),
        occurrence_count=len(core),
        transaction_ids=[t.tx.transaction_id for t in core],
        last_paid_at=last.day,
        next_expected_date=_next_date(last.day, pay_day),
    )


def _month_index(ym: tuple[int, int]) -> int:
    return ym[0] * 12 + ym[1]


def _day_distance(a: int, b: int) -> int:
    """날짜(일) 거리. 30일과 1일은 1~2일 차이로 본다 (월말 결제가 다음 달 1일로 밀리는 경우)."""
    d = abs(a - b)
    return min(d, 31 - d)


def _center_day(days: list[int]) -> int:
    # 가장 먼 날까지 거리가 가장 짧은 날을 기준일로 (월말·월초가 섞여도 동작). 같으면 거리 합이 작은 날
    return min(sorted(set(days)), key=lambda c: (max(_day_distance(d, c) for d in days),
                                                 sum(_day_distance(d, c) for d in days), c))


def _next_date(last: date, pay_day: int) -> date:
    y, m = (last.year + 1, 1) if last.month == 12 else (last.year, last.month + 1)
    return date(y, m, min(pay_day, calendar.monthrange(y, m)[1]))


def _most_common(codes: list[str | None]) -> str | None:
    known = [c for c in codes if c]
    return Counter(known).most_common(1)[0][0] if known else None


def _round_won(x: float) -> int:
    return int(round(x / 100) * 100)


def _won(amount: int) -> str:
    if amount >= 10_000 and amount % 10_000 == 0:
        return f"{amount // 10_000:,}만 원"
    return f"{amount:,}원"


def _reason(count: int, day: int, payee: str, amount: int, account_type: str | None, person: bool) -> str:
    """v4 예시 문장 형식의 템플릿 ('지난 3개월 매월 25일 전후 김OO님께 60만 원을 보냈어요')."""
    if person:
        return f"지난 {count}개월 매월 {day}일 전후 {payee}님께 {_won(amount)}을 보냈어요"
    if account_type == "BANK":
        return f"지난 {count}개월 매월 {day}일 전후 {payee}에 {_won(amount)}을 보냈어요"
    return f"지난 {count}개월 매월 {day}일 전후 {payee}에서 {_won(amount)}이 결제됐어요"
