"""Spring -> AI 거래 분류 API.

경로·JSON 칸 이름(camelCase)은 BE 연동 명세 기준. 경로는 다른 내부 API와 같은 /internal/v1 아래에 둔다.
"""

from fastapi import APIRouter, Depends, Request

from app.core.internal_auth import require_internal_token
from app.modules.classifier.categories import CATEGORIES
from app.modules.classifier.pipeline import Classifier
from app.schemas.classify import (
    CategoryOut,
    ClassifyRequest,
    ClassifyResponse,
    InitialClassifyRequest,
    InitialClassifyResponse,
)
from app.services import classify as service

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal-classify"],
    dependencies=[Depends(require_internal_token)],
)


def _classifier(request: Request) -> Classifier:
    return request.app.state.classifier


@router.get("/categories", response_model=list[CategoryOut], summary="카테고리 코드 목록 (BE 시드 16개와 같음)")
def categories() -> list[CategoryOut]:
    return [CategoryOut(**c.__dict__) for c in CATEGORIES]


@router.post("/classify", response_model=ClassifyResponse, summary="거래 분류 (1~1000건)")
def classify(body: ClassifyRequest, classifier: Classifier = Depends(_classifier)) -> ClassifyResponse:
    return ClassifyResponse(results=service.classify(classifier, body.transactions))


@router.post("/classify/initial", response_model=InitialClassifyResponse,
             summary="첫 계좌 연동: 3개월 전체 분류 + 반복지출 후보 (1~5000건)")
def classify_initial(body: InitialClassifyRequest, classifier: Classifier = Depends(_classifier)) -> InitialClassifyResponse:
    return service.classify_initial(classifier, body.transactions)
