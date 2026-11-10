from datetime import datetime

import pytest

from app.modules.classifier.dictionary import MerchantDictionary
from app.modules.classifier.pipeline import Classifier
from app.modules.classifier.slm_stage import SlmStage
from app.core.classify_config import Settings
from app.modules.classifier import slm_stage
from app.schemas.classify import TransactionIn
from app.modules.model_queue import ModelResult, ModelTimeout


def tx(name="명동칼국수", **kw) -> TransactionIn:
    return TransactionIn(
        transaction_id=1, occurred_at=datetime(2026, 10, 10, 12), amount=15000, direction="OUT",
        merchant_or_payee=name, account_type="CARD", **kw,
    )


class FakeQueue:
    """model_queue.ModelQueue 대역: 보낸 것을 모으고, 정해 둔 답(또는 예외)을 차례로 돌려준다."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent = []

    def call(self, task, payload, timeout):
        self.sent.append((task, payload))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


def ok(code):
    return ModelResult("r", True, result={"category_code": code})


def stage(*answers, clock=lambda: 0.0):
    q = FakeQueue(*answers)
    return SlmStage(q, timeout=10, cooldown=60, clock=clock), q


class FakeJeff:
    def __init__(self, route):
        self._route = route

    def route(self, t):
        return self._route


class FakeSlm:
    def __init__(self, code):
        self.code = code
        self.calls = 0

    def category(self, t):
        self.calls += 1
        return self.code


def classifier(**kw) -> Classifier:
    return Classifier(MerchantDictionary.load(), question_threshold=0.7, fuzzy_min_score=90, **kw)


@pytest.mark.parametrize(
    "code, expect",
    [
        ("DELIVERY_DINING", "DELIVERY_DINING"),
        ("UNKNOWN", None),
        ("DUTCH_PAY", None),  # 이체 유형은 사용자 답으로만 정해진다
        ("PIZZA", None),
        (None, None),
        (3, None),
    ],
)
def test_only_allowed_answer_passes(code, expect):
    assert stage(ok(code))[0].category(tx()) == expect


def test_sends_only_name_and_type():
    s, q = stage(ok("FOOD"))
    s.category(tx(merchant_type="일반한식"))
    assert q.sent == [("slm", {"name": "명동칼국수", "merchant_type": "일반한식"})]
    s.category(tx("해탄"))
    assert q.sent[-1] == ("slm", {"name": "해탄"})


def test_same_merchant_is_asked_once():
    s, q = stage(ok("FOOD"))
    assert [s.category(tx()), s.category(tx())] == ["FOOD", "FOOD"]
    assert len(q.sent) == 1


@pytest.mark.parametrize("failure", [ModelTimeout("x"), ConnectionError("x"),
                                     ModelResult("r", False, error="sLM 500"), ModelResult("r", True, result=None)])
def test_failure_returns_none_then_cools_down(failure):
    now = [0.0]
    s, q = stage(failure, clock=lambda: now[0])
    assert s.category(tx("가게1")) is None
    assert s.category(tx("가게2")) is None
    assert len(q.sent) == 1  # 쉬는 동안 안 보낸다
    now[0] = 61.0
    assert s.category(tx("가게3")) is None
    assert len(q.sent) == 2


def test_failure_is_not_cached():
    now = [0.0]
    s, _ = stage(ModelTimeout("x"), ok("CAFE"), clock=lambda: now[0])
    assert s.category(tx()) is None
    now[0] = 61.0
    assert s.category(tx()) == "CAFE"


def test_off_without_queue_urls():
    base = dict(app_env="t", log_level="INFO", question_threshold=0.7, fuzzy_min_score=90)
    assert slm_stage.from_settings(Settings(**base)) is None
    assert slm_stage.from_settings(Settings(**base, slm_requests_url="https://q/slm")) is None
    assert slm_stage.from_settings(Settings(**base, slm_requests_url="https://q/slm", model_replies_url="https://q/r")) is not None


def test_jeff_slm_route_gets_category():
    r = classifier(jeff=FakeJeff("slm"), slm=FakeSlm("TELECOM")).classify(tx("해탄"))
    assert (r.category_code, r.classify_stage, r.status, r.needs_question) == ("TELECOM", 4, "CONFIRMED", False)


@pytest.mark.parametrize("route", ["ask", None])
def test_jeff_ask_or_silent_never_calls_slm(route):
    slm = FakeSlm("TELECOM")
    r = classifier(jeff=FakeJeff(route), slm=slm).classify(tx("해탄"))
    assert slm.calls == 0
    assert (r.category_code, r.question_type) == (None, "LOW_CONFIDENCE")


def test_jeff_off_goes_straight_to_slm():
    assert classifier(slm=FakeSlm("CAFE")).classify(tx("해탄")).category_code == "CAFE"


def test_slm_unknown_falls_back_to_question():
    r = classifier(slm=FakeSlm(None)).classify(tx("해탄"))
    assert (r.category_code, r.question_type, r.status) == (None, "LOW_CONFIDENCE", "NEEDS_REVIEW")


@pytest.mark.parametrize("name, kw", [("스타벅스", {}), ("네이버페이", {}), (None, {})])
def test_slm_not_called_when_earlier_steps_decide(name, kw):
    slm = FakeSlm("ETC")
    classifier(slm=slm).classify(tx(name, **kw))
    assert slm.calls == 0


def test_bank_transfer_never_reaches_slm():
    slm = FakeSlm("ETC")
    t = TransactionIn(transaction_id=1, occurred_at=datetime(2026, 10, 10, 12), amount=50000, direction="OUT",
                      merchant_or_payee="김OO", account_type="BANK")
    assert classifier(slm=slm).classify(t).question_type == "PERSONAL_TRANSFER"
    assert slm.calls == 0


def test_late_night_slm_dining_still_asks_context():
    t = TransactionIn(transaction_id=1, occurred_at=datetime(2026, 10, 10, 23), amount=20000, direction="OUT",
                      merchant_or_payee="해탄", account_type="CARD")
    r = classifier(slm=FakeSlm("DELIVERY_DINING")).classify(t)
    assert (r.category_code, r.question_type, r.status) == ("DELIVERY_DINING", "LATE_NIGHT_FOOD", "CONFIRMED")
