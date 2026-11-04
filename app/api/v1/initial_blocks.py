"""Spring 백엔드의 초기 블록 생성 데이터 수신 API.

지금은 사용자/거래 데이터를 검증하고 수신 확인만 한다.
추후 app/services/initial_blocks.py를 호출하도록 확장할 수 있다.
"""

import logging

from fastapi import APIRouter, Depends, status

from app.core.internal_auth import require_internal_token
from app.schemas.initial_blocks import InitialBlocksReceipt, InitialBlocksRequest

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/v1/initial-blocks",
    tags=["internal-initial-blocks"],
    dependencies=[Depends(require_internal_token)],
)


@router.post(
    "",
    response_model=InitialBlocksReceipt,
    status_code=status.HTTP_200_OK,
    summary="초기 블록 생성 요청 수신 확인",
)
async def receive_initial_blocks(request: InitialBlocksRequest) -> InitialBlocksReceipt:
    """데이터를 확인하고 receipt 반환. 원본 거래정보는 로그/응답에 출력하지 않는다.

    현재는 영구 저장/분석/Agent 생성/중복 요청 차단을 하지 않는다.
    """
    logger.info(
        "initial_blocks.received request_id=%s user_id=%s transaction_count=%s",
        request.request_id,
        request.user.user_id,
        len(request.transactions),
    )
    return InitialBlocksReceipt(
        request_id=request.request_id,
        user_id=request.user.user_id,
        transaction_count=len(request.transactions),
    )
