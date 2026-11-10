"""4단: 사전·퍼지로 못 잡은 거래를 Jeff에게 묻는다 (SQS 큐 → 재혁 PC jeff-serve).

Jeff는 고르기만 한다: slm(이름에 업종이 보여 sLM이 카테고리를 고를 수 있음) / ask(사용자에게 묻기).
Jeff가 고른 답(choice)이 아니라 ask 확률로 정한다: ask 확률 >= ask_threshold 이면 ask, 아니면 slm.
물어봐야 할 거래를 놓치지 않으려고 문턱을 0.5보다 낮게 둔다 (JEFF_ASK_THRESHOLD).
집 PC가 꺼져 답이 없거나 오류면 ask로 대체 — 사용자에게 묻는 지금 동작과 같다.
한 번 시간 초과가 나면 cooldown 동안 Jeff를 건너뛴다: 1000건 묶음이 건마다 기다리며 멈추지 않게.

큐 주소가 없으면(로컬·테스트) 이 단계는 꺼진다. EC2에선 키 없이 서버 역할(출입증)로 큐를 쓴다.
"""

import logging
import time
from typing import Literal

from app.schemas.classify import TransactionIn

logger = logging.getLogger("fintris.ai.jeff")

Route = Literal["slm", "ask"]


def jeff_state(tx: TransactionIn) -> dict:
    """학습 데이터(scripts/make_jeff_data.py)와 같은 칸·형식. 칸이 다르면 학습한 판단이 안 맞는다."""
    return {
        "merchant_or_payee": tx.merchant_or_payee,
        "amount": tx.amount,
        "direction": tx.direction,
        "account_type": tx.account_type or "CARD",
        "occurred_at": tx.occurred_at.strftime("%Y-%m-%dT%H:%M"),
    }


class JeffStage:
    def __init__(self, queue, timeout: float, cooldown: float, ask_threshold: float = 0.2, clock=time.monotonic):
        self._queue = queue  # model_queue.ModelQueue 모양: call(task, payload, timeout) -> ModelResult
        self._timeout = timeout
        self._cooldown = cooldown
        self._ask_threshold = ask_threshold
        self._clock = clock
        self._skip_until = 0.0

    def route(self, tx: TransactionIn) -> Route | None:
        """None = Jeff 답 없음(꺼짐·오류·쉬는 중) → 호출한 쪽이 ask로 처리."""
        if self._clock() < self._skip_until:
            return None
        try:
            res = self._queue.call("jeff", {"state": jeff_state(tx)}, timeout=self._timeout)
        except TimeoutError:  # model_queue.ModelTimeout 포함
            self._skip_until = self._clock() + self._cooldown
            logger.warning("jeff 시간 초과 %.0fs → %.0fs 동안 건너뜀", self._timeout, self._cooldown)
            return None
        except Exception as error:  # 큐 권한·네트워크 오류도 분류 전체를 멈추지 않는다
            logger.warning("jeff 호출 실패: %s", type(error).__name__)
            return None
        probabilities = (res.result or {}).get("probabilities") if res.ok else None
        ask = probabilities.get("ask") if isinstance(probabilities, dict) else None
        if isinstance(ask, bool) or not isinstance(ask, (int, float)):
            logger.warning("jeff 답 이상: ok=%s error=%s", res.ok, res.error)
            return None
        return "ask" if ask >= self._ask_threshold else "slm"


def from_settings(settings) -> JeffStage | None:
    if not (settings.jeff_requests_url and settings.model_replies_url):
        return None
    import boto3
    from app.modules.model_queue import ModelQueue

    sqs = boto3.client("sqs", region_name=settings.aws_region)
    queue = ModelQueue(sqs, {"jeff": settings.jeff_requests_url}, settings.model_replies_url)
    return JeffStage(queue, timeout=settings.jeff_timeout, cooldown=settings.jeff_cooldown,
                     ask_threshold=settings.jeff_ask_threshold)
