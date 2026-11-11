"""거래 분류 1~2단 파이프라인 (기능명세서 F-20).

규칙: 본인 이체 / 입금 / 카드대금 / 현금 인출 / 간편결제만 찍힌 건 / 개인 송금
1단 사전 → 2단 퍼지 → 4단 Jeff(slm/ask 고르기, jeff_stage.py) → sLM(카테고리 고르기, slm_stage.py).
어디에도 안 걸리면 category_code=None + 질문. (3단 임베딩은 이후)

"항상 묻기"(F-20 처리 규칙): 간편결제·오픈마켓(품목 불명), 1만 원 이상 개인 송금.
"묻지 않기"(반복 송금, 저장된 맥락 규칙)는 BE가 판단한다.
밤 10시 이후 배달·외식은 카테고리는 확정하고 맥락(야근 등)만 묻는다 (피그마 07·13-1).

AI는 금액·상태를 계산하지 않는다. 여기서 정하는 건 카테고리와 '소비에서 뺄지' 표시뿐.
"""

import logging
from dataclasses import dataclass

from app.modules.classifier import rules
from app.modules.classifier.dictionary import MerchantDictionary
from app.modules.classifier.jeff_stage import JeffStage
from app.modules.classifier.normalize import compact, normalize
from app.modules.classifier.slm_stage import SlmStage
from app.schemas.classify import ClassificationOut, QuestionType, TransactionIn

# 신뢰도 기준값: 규칙·사전 일치는 높게, 퍼지는 점수에 비례해 낮춘다
_CONF_RULE = 0.95
_CONF_DICT = 0.9
_CONF_FUZZY_SCALE = 0.85
_CONF_SLM = 0.75  # 질문 기준 0.7은 넘되 사전보다 낮게. 모델이 준 확률이 아니라 고정값
_PERSONAL_TRANSFER_ASK_MIN = 10_000
_LATE_NIGHT_START, _LATE_NIGHT_END = 22, 5  # 22:00 ~ 04:59
_LATE_NIGHT_CODES = frozenset({"DELIVERY_DINING"})

# 질문 시트 선택지 (피그마 09-1). 맨 앞이 가장 그럴듯한 답
_OPTIONS: dict[QuestionType, tuple[str, ...]] = {
    "UNKNOWN_ITEM": ("LIVING", "FOOD", "SHOPPING", "ETC", "DUTCH_PAY"),
    "PERSONAL_TRANSFER": ("DUTCH_PAY", "GIFT", "LOAN_OUT", "ETC"),
    "LATE_NIGHT_FOOD": ("야근", "약속", "그냥"),
    "LOW_CONFIDENCE": ("FOOD", "LIVING", "SHOPPING", "ETC"),  # F-15: 상위 후보 3개 + 기타
}
_MAX_OPTIONS = 5
logger = logging.getLogger("fintris.ai.classify")


@dataclass(frozen=True)
class _Decision:
    code: str | None
    stage: int | None
    conf: float
    excluded: bool = False
    question: QuestionType | None = None
    keyword: str | None = None


_UNKNOWN = _Decision(None, None, 0.0)  # 규칙·사전·퍼지 어디에도 안 걸림 → 4단 Jeff 대상


class Classifier:
    def __init__(self, dictionary: MerchantDictionary, question_threshold: float, fuzzy_min_score: int,
                 jeff: JeffStage | None = None, slm: SlmStage | None = None):
        self._dict = dictionary
        self._threshold = question_threshold
        self._fuzzy_min = fuzzy_min_score
        self._jeff = jeff
        self._slm = slm

    def brand_keywords(self) -> frozenset[str]:
        return self._dict.brand_keywords()

    def classify(self, tx: TransactionIn) -> ClassificationOut:
        d = self._decide(tx)
        if d == _UNKNOWN:
            d = self._ask_models(tx)
        question = d.question or ("LOW_CONFIDENCE" if d.conf < self._threshold else None)
        if question is None and d.code in _LATE_NIGHT_CODES and _is_late_night(tx):
            question = "LATE_NIGHT_FOOD"
        # 야간 질문은 맥락만 묻는 것이라 카테고리는 확정 상태로 둔다
        review = question is not None and question != "LATE_NIGHT_FOOD"
        return ClassificationOut(
            transaction_id=tx.transaction_id,
            occurred_at=tx.occurred_at,
            category_code=d.code,
            classify_stage=d.stage,
            confidence=round(d.conf, 3),
            status="NEEDS_REVIEW" if review else "CONFIRMED",
            excluded=d.excluded,
            needs_question=question is not None,
            question_type=question,
            question_options=_options(question, d.code),
            matched_keyword=d.keyword,
        )

    def _decide(self, tx: TransactionIn) -> _Decision:
        if tx.is_own_account_transfer:
            return _Decision("SELF_TRANSFER", 1, 1.0, excluded=True)
        if tx.direction == "IN" or tx.amount < 0:  # 음수 = 취소 전표 (은행·카드사 표기), 소비 아님
            return _Decision(None, 1, _CONF_RULE, excluded=True)

        text = normalize(tx.merchant_or_payee or "")
        if not compact(text):
            # 이름이 없으면 사전도 Jeff도 판단할 게 없다
            return _bank_transfer(tx) or _Decision(None, None, 0.0, question="LOW_CONFIDENCE")
        # 카드 승인 내역에 찍힌 카드사 이름은 연회비·포인트몰 같은 소비다. 카드대금은 은행 출금으로만 온다
        if tx.account_type != "CARD" and (kw := rules.is_card_payment(text)):
            return _Decision(None, 1, _CONF_RULE, excluded=True, keyword=kw)
        if kw := rules.is_cash_withdrawal(text):
            return _Decision(None, 1, _CONF_RULE, excluded=True, keyword=kw)
        if tx.account_type == "BANK" and (kw := rules.is_saving_or_student_loan(text)):
            return _Decision(None, 1, _CONF_RULE, excluded=True, keyword=kw)
        if kw := rules.pg_only(text):
            return _Decision(None, None, 0.0, question="UNKNOWN_ITEM", keyword=kw)

        if m := self._dict.exact(text):
            if rules.is_open_market(m.keyword):
                return _Decision(None, None, 0.0, question="UNKNOWN_ITEM", keyword=m.keyword)
            return _Decision(m.category_code, 1, _CONF_DICT, keyword=m.keyword)
        if m := self._dict.fuzzy(text, self._fuzzy_min):
            if rules.is_open_market(m.keyword):
                return _Decision(None, None, 0.0, question="UNKNOWN_ITEM", keyword=m.keyword)
            return _Decision(m.category_code, 2, _CONF_FUZZY_SCALE * m.score / 100, keyword=m.keyword)

        return _bank_transfer(tx) or _UNKNOWN

    def _ask_models(self, tx: TransactionIn) -> _Decision:
        """Jeff가 맡기라고 하면 sLM이 카테고리를 고른다. Jeff가 꺼져 있으면 sLM이 바로 판단한다.

        Jeff가 ask를 골랐거나 답이 없거나, sLM이 모르면 사용자에게 묻는다.
        로그엔 거래 ID·고른 길만 (가맹점·금액은 남기지 않는다).
        """
        route = "slm"
        if self._jeff is not None:
            route = self._jeff.route(tx)
            logger.info("jeff tx=%s route=%s", tx.transaction_id, route or "none")
        if route == "slm" and self._slm is not None and (code := self._slm.category(tx)):
            return _Decision(code, 4, _CONF_SLM)
        return _UNKNOWN


def _bank_transfer(tx: TransactionIn) -> _Decision | None:
    """사전에 없는 은행 출금은 개인 송금으로 본다. 카드 거래면 None."""
    if tx.account_type != "BANK":
        return None
    if tx.amount >= _PERSONAL_TRANSFER_ASK_MIN:
        return _Decision(None, None, 0.0, question="PERSONAL_TRANSFER")
    # 소액 송금은 묻지 않는다: 틀려도 손해가 작고 질문은 하루 3건뿐 (24 정답 기준표)
    return _Decision("ETC", 1, _CONF_RULE)


def _is_late_night(tx: TransactionIn) -> bool:
    h = tx.occurred_at.hour  # BE가 보내는 시각(KST) 그대로
    return h >= _LATE_NIGHT_START or h < _LATE_NIGHT_END


def _options(question: QuestionType | None, guess: str | None) -> list[str]:
    if question is None:
        return []
    base = _OPTIONS[question]
    # 퍼지로 추정한 카테고리가 있으면 맨 앞에. 후보 3개 + 기타 유지
    if question == "LOW_CONFIDENCE" and guess and guess != "ETC":
        top = (guess, *(c for c in base if c not in (guess, "ETC")))[:3]
        base = (*top, "ETC")
    return list(base[:_MAX_OPTIONS])
