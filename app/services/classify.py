"""거래 분류 서비스: 분류기 준비, 거래 분류, 첫 연동(분류 + 반복지출).

입구(app/api/v1/classify.py)는 이 함수들만 부른다.
"""

from app.core.classify_config import Settings
from app.modules.analysis.recurring import find_recurring
from app.modules.classifier import jeff_stage, slm_stage
from app.modules.classifier.dictionary import MerchantDictionary
from app.modules.classifier.pipeline import Classifier
from app.schemas.classify import (
    ClassificationOut,
    InitialClassifyResponse,
    RecurringCandidate,
    TransactionIn,
)


def build_classifier(settings: Settings) -> Classifier:
    """사전 읽기·Jeff·sLM 큐 연결. 큐 주소가 비면 Jeff·sLM은 꺼진다 (규칙·사전만)."""
    return Classifier(
        MerchantDictionary.load(),
        question_threshold=settings.question_threshold,
        fuzzy_min_score=settings.fuzzy_min_score,
        jeff=jeff_stage.from_settings(settings),
        slm=slm_stage.from_settings(settings),
    )


def classify(classifier: Classifier, transactions: list[TransactionIn]) -> list[ClassificationOut]:
    return [classifier.classify(tx) for tx in transactions]


def classify_initial(classifier: Classifier, transactions: list[TransactionIn]) -> InitialClassifyResponse:
    """첫 계좌 연동: 3개월 전체를 분류하고 반복지출 후보까지 찾는다."""
    results = classify(classifier, transactions)
    recurring = find_recurring(transactions, results, classifier.brand_keywords())
    return InitialClassifyResponse(results=quiet_recurring(results, recurring), recurring=recurring)


def quiet_recurring(results: list[ClassificationOut], recurring: list[RecurringCandidate]) -> list[ClassificationOut]:
    """반복지출 후보에 들어간 거래는 묻지 않는다. 같은 응답의 고정지출 블록 후보가 그 질문을 대신한다.

    예: 매달 25일 김OO에게 60만 원 → 월세 블록 후보로 보여주는데, 거래 3건마다 '누구에게 보낸 돈?'을 또 물으면 이상하다.
    카테고리는 그대로 둔다(월세 송금은 null). 블록을 확정하면 BE가 그 거래들을 정한다.
    """
    ids = {i for c in recurring for i in c.transaction_ids}
    return [r.model_copy(update={"needs_question": False, "question_type": None, "question_options": []})
            if r.transaction_id in ids and r.needs_question else r for r in results]
