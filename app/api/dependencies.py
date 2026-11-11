"""Application-owned dependencies shared by internal API routes."""

from fastapi import HTTPException, Request, status

from app.services.coach_room import CoachRoomService


def get_coach_room_service(request: Request) -> CoachRoomService:
    service = getattr(request.app.state, "coach_room_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "LLM_NOT_CONFIGURED",
                "message": "LLM_BASE_URL and LLM_MODEL must be configured for room generation.",
            },
        )
    return service
