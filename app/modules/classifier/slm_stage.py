"""4단 뒤: 사전이 모르는 가게의 카테고리를 sLM에게 고르게 한다 (SQS 큐 → sLM 워커 → 수업 Qwen / 승현 vLLM).

서버는 큐에 가맹점 이름·업종만 넣는다. 금액·시각·계좌는 보내지 않는다.
프롬프트·sLM 주소·키는 모델 PC의 sLM 워커에만 있다 — 서버엔 키가 없다.
답이 선택지에 없거나 UNKNOWN이거나 실패하면 None → 호출한 쪽이 사용자에게 묻는다.
한 번 실패하면 cooldown 동안 건너뛴다: 1000건 묶음이 건마다 기다리며 멈추지 않게.
큐 주소가 없으면(로컬·테스트) 이 단계는 꺼진다.
"""

import logging
import time

from app.modules.classifier.categories import CATEGORIES
from app.schemas.classify import TransactionIn

logger = logging.getLogger("fintris.ai.slm")

_ALLOWED = frozenset(c.code for c in CATEGORIES if c.category_type == "EXPENSE")
_MAX_CACHE = 5000


def valid(code) -> str | None:
    """워커 답을 그대로 믿지 않는다: 소비 카테고리 코드만 통과 (UNKNOWN·이체 유형·지어낸 코드는 None)."""
    return code if isinstance(code, str) and code in _ALLOWED else None


class SlmStage:
    def __init__(self, queue, timeout: float, cooldown: float, clock=time.monotonic):
        self._queue = queue  # model_queue.ModelQueue 모양: call(task, payload, timeout) -> ModelResult
        self._timeout = timeout
        self._cooldown = cooldown
        self._clock = clock
        self._skip_until = 0.0
        self._cache: dict[tuple[str, str | None], str | None] = {}  # 같은 가게를 또 묻지 않는다

    def category(self, tx: TransactionIn) -> str | None:
        key = (tx.merchant_or_payee or "", tx.merchant_type)
        if key in self._cache:
            return self._cache[key]
        if self._clock() < self._skip_until:
            return None
        payload = {"name": key[0]}
        if key[1]:
            payload["merchant_type"] = key[1]
        try:
            res = self._queue.call("slm", payload, timeout=self._timeout)
        except Exception as error:  # 시간 초과(워커 꺼짐)·큐 권한·네트워크 — 분류 전체를 멈추지 않는다
            return self._fail(type(error).__name__)
        if not res.ok or not isinstance(res.result, dict):
            # 실패는 저장하지 않는다. 워커가 돌아오면 다시 묻는다. 로그에 가맹점·키를 남기지 않는다
            return self._fail("워커 오류")
        code = valid(res.result.get("category_code"))
        if len(self._cache) >= _MAX_CACHE:
            self._cache.clear()
        self._cache[key] = code
        return code

    def _fail(self, why: str) -> None:
        self._skip_until = self._clock() + self._cooldown
        logger.warning("slm 실패: %s → %.0fs 동안 건너뜀", why, self._cooldown)
        return None


def from_settings(settings) -> SlmStage | None:
    if not (settings.slm_requests_url and settings.model_replies_url):
        return None
    import boto3
    from app.modules.model_queue import ModelQueue

    sqs = boto3.client("sqs", region_name=settings.aws_region)
    queue = ModelQueue(sqs, {"slm": settings.slm_requests_url}, settings.model_replies_url)
    return SlmStage(queue, timeout=settings.slm_timeout, cooldown=settings.slm_cooldown)
