"""Room-entry text generation for the trusted backend caller."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_coach_room_service
from app.core.internal_auth import require_internal_token
from app.schemas.coach_room import CoachRoomRequest, CoachRoomResponse
from app.services.coach_room import (
    CoachRoomInvalidResponseError,
    CoachRoomProviderError,
    CoachRoomService,
    CoachRoomTimeoutError,
)


router = APIRouter(
    prefix="/internal/ai/coach",
    tags=["internal-coach"],
    dependencies=[Depends(require_internal_token)],
)


@router.post(
    "/room",
    response_model=CoachRoomResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate the coach room greeting and three quick-action texts",
    responses={
        401: {"description": "Invalid internal service token"},
        502: {"description": "LLM request failed or produced an invalid response"},
        503: {"description": "Internal authentication or LLM is not configured"},
        504: {"description": "LLM request timed out"},
    },
)
async def enter_coach_room(
    request: CoachRoomRequest,
    service: CoachRoomService = Depends(get_coach_room_service),
) -> CoachRoomResponse:
    try:
        return await service.generate(request)
    except CoachRoomTimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": "COACH_ROOM_TIMEOUT",
                "message": "Room text generation timed out",
            },
        ) from None
    except CoachRoomProviderError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "COACH_ROOM_PROVIDER_ERROR",
                "message": "The language model request failed",
            },
        ) from None
    except CoachRoomInvalidResponseError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "COACH_ROOM_INVALID_RESPONSE",
                "message": "The language model returned an invalid room response",
            },
        ) from None
