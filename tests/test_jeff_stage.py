from datetime import datetime

from app.modules.classifier.dictionary import MerchantDictionary
from app.modules.classifier.jeff_stage import JeffStage, jeff_state
from app.modules.classifier.pipeline import Classifier
from app.schemas.classify import TransactionIn
from app.modules.model_queue import ModelResult, ModelTimeout


def tx(merchant="명동연가", **kw):
    return TransactionIn(transaction_id=kw.pop("transaction_id", 1), occurred_at=datetime(2026, 10, 10, 12, 5),
                         amount=9000, direction="OUT", merchant_or_payee=merchant, account_type="CARD", **kw)


class FakeQueue:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def call(self, task, payload, timeout):
        self.calls.append((task, payload))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def ok(ask_probability, choice="slm"):
    return ModelResult(request_id="r", ok=True, result={
        "choice": choice, "probabilities": {"slm": 1 - ask_probability, "ask": ask_probability}})


def test_state_matches_training_format():
    # 학습 데이터와 칸·시각 형식이 같아야 학습한 판단이 맞는다
    assert jeff_state(tx()) == {"merchant_or_payee": "명동연가", "amount": 9000, "direction": "OUT",
                                "account_type": "CARD", "occurred_at": "2026-10-10T12:05"}


def test_route_uses_ask_probability_threshold():
    stage = lambda p: JeffStage(FakeQueue(ok(p)), 5, 60, ask_threshold=0.2).route(tx())
    assert stage(0.19) == "slm"
    assert stage(0.2) == "ask"  # 문턱과 같으면 묻는다
    assert stage(0.9) == "ask"


def test_threshold_overrides_jeff_choice():
    # Jeff가 slm을 골랐어도(ask 0.3 < slm 0.7) ask 확률이 문턱 이상이면 묻는다
    assert JeffStage(FakeQueue(ok(0.3, choice="slm")), 5, 60, ask_threshold=0.2).route(tx()) == "ask"
    assert JeffStage(FakeQueue(ok(0.3, choice="slm")), 5, 60, ask_threshold=0.5).route(tx()) == "slm"


def test_bad_answers_fall_back_to_none():
    for answer in [ModelResult(request_id="r", ok=False, error="jeff-serve 연결 실패"),
                   ModelResult(request_id="r", ok=True, result={"choice": "slm"}),  # 확률 없음
                   ModelResult(request_id="r", ok=True, result={"probabilities": {"slm": 1.0}}),
                   ModelResult(request_id="r", ok=True, result={"probabilities": {"ask": "높음"}}),
                   ModelResult(request_id="r", ok=True, result=None), RuntimeError("AccessDenied")]:
        assert JeffStage(FakeQueue(answer), 5, 60).route(tx()) is None, answer


def test_timeout_skips_jeff_during_cooldown():
    clock, q = Clock(), FakeQueue(ModelTimeout("꺼짐"))
    stage = JeffStage(q, 5, 60, clock=clock)
    assert stage.route(tx()) is None
    assert stage.route(tx()) is None and len(q.calls) == 1  # 쉬는 중 → 큐에 안 넣음
    clock.t = 61
    q.answer = ok(0.9)
    assert stage.route(tx()) == "ask" and len(q.calls) == 2


def test_classifier_asks_jeff_only_for_unknown():
    q = FakeQueue(ok(0.1))
    c = Classifier(MerchantDictionary.load(), 0.7, 90, jeff=JeffStage(q, 5, 60))
    c.classify(tx("스타벅스 강남점"))   # 사전
    c.classify(tx("네이버페이"))        # 간편결제 규칙
    r = c.classify(tx("명동연가"))    # 처음 보는 가게 → Jeff
    assert [p["state"]["merchant_or_payee"] for _, p in q.calls] == ["명동연가"]
    # sLM이 붙기 전까지는 slm이어도 질문 그대로 (응답 모양 변화 없음)
    assert r.category_code is None and r.question_type == "LOW_CONFIDENCE"


def test_jeff_off_without_queue_urls(client):
    # 로컬·테스트: 큐 주소 없으면 Jeff 없이 지금처럼 동작
    assert client.app.state.classifier._jeff is None
