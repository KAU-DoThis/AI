"""규칙: 소비가 아닌 거래, 간편결제·오픈마켓(품목 불명)을 가른다."""

import re

from app.modules.classifier.normalize import compact

_CARD_COMPANIES = (
    "신한카드", "삼성카드", "현대카드", "롯데카드", "하나카드", "우리카드",
    # 'kb국민'만으로는 매칭하지 않는다: 'KB국민은행' 출금·ATM까지 카드대금으로 빠진다
    "국민카드", "kb카드", "비씨카드", "bc카드", "nh카드", "농협카드",
    "씨티카드", "카드대금", "카드결제대금",
)
# 은행 적요에 '체크'가 있으면 체크카드 결제(=소비)라서 카드대금이 아니다
_CHECK_CARD = re.compile(r"체크")

# 결제처가 간편결제사로만 찍혀 실제 가맹점을 알 수 없는 경우
_PG_ONLY = (
    "네이버페이", "naverpay", "npay", "카카오페이", "kakaopay", "토스페이", "tosspay",
    "페이코", "payco", "쿠팡페이", "스마일페이", "ssgpay", "삼성페이",
    # 휴대폰 소액결제 대행사: 무엇을 샀는지 알 수 없다
    "다날", "모빌리언스", "kg모빌리언스", "갤럭시아머니트리",
)
# 'SKT 소액결제'가 통신비로 잡히지 않게 통신사 이름이 있어도 먼저 본다
_MICROPAYMENT = ("소액결제", "휴대폰결제")


def is_card_payment(text: str) -> str | None:
    """카드대금 출금이면 매칭된 키워드를 돌려준다."""
    c = compact(text)
    if _CHECK_CARD.search(c):
        return None
    return next((k for k in _CARD_COMPANIES if k in c), None)


# 현금 인출: 소비 제외. 현금으로 쓴 건 빠른 입력(MANUAL)으로 따로 기록된다
_CASH_WITHDRAWAL = ("현금인출", "cd출금")
# 'atm'은 단어 경계로만: 'BATMAN' 같은 가맹점이 소비에서 빠지면 안 된다
_ATM = re.compile(r"(?<![0-9a-z])atm(?![0-9a-z])")


def is_cash_withdrawal(text: str) -> str | None:
    if _ATM.search(text):
        return "atm"
    c = compact(text)
    return next((k for k in _CASH_WITHDRAWAL if k in c), None)


# 저축·투자·학자금 상환은 돈이 다른 곳으로 옮겨 간 것이라 소비가 아니다.
# 명세에 없는 임시 규칙(팀 확인 전). 은행 출금에만 쓴다
_NOT_SPENDING_BANK = ("적금", "청약", "도약계좌", "증권", "장학재단", "학자금")


def is_saving_or_student_loan(text: str) -> str | None:
    c = compact(text)
    return next((k for k in _NOT_SPENDING_BANK if k in c), None)


def pg_only(text: str) -> str | None:
    """간편결제사 이름만 있고 가맹점이 없으면 키워드를 돌려준다."""
    c = compact(text)
    if kw := next((k for k in _MICROPAYMENT if k in c), None):
        return kw
    for k in _PG_ONLY:
        if c == k or (c.startswith(k) and len(c) - len(k) <= 2):
            return k
    return None

# 오픈마켓은 가맹점은 알아도 무엇을 샀는지 몰라 항상 묻는다 (F-20 처리 규칙). 사전 키워드(정규화된 값)로 비교
_OPEN_MARKETS = frozenset({"쿠팡", "11번가", "g마켓", "지마켓", "옥션", "ssg닷컴"})


def is_open_market(keyword: str) -> bool:
    return keyword in _OPEN_MARKETS
