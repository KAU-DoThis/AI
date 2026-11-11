"""POST /internal/ai/coach/chat. Reuse the host's INTERNAL auth dependency."""
from typing import Any, Callable
from fastapi import APIRouter, Depends, HTTPException, Request
from app.agent.chat_manager import AgentManager, ChatError
from app.schemas.coach_chat import CoachChatRequest, CoachChatResponse

ERRORS = {
    "REQUEST_ID_CONFLICT": (409, "requestId가 다른 요청에 이미 사용되었습니다."),
    "QUEUE_FULL": (429, "사용자의 대기 요청이 많습니다."),
    "CAPACITY": (503, "현재 처리 용량이 부족합니다."),
    "UNAVAILABLE": (503, "에이전트가 종료 또는 정리 중입니다."),
    "NOT_CONFIGURED": (503, "채팅 에이전트가 설정되지 않았습니다."),
    "TIMEOUT": (504, "AI 응답 제한 시간을 초과했습니다."),
    "PROVIDER_ERROR": (502, "LLM 호출에 실패했습니다."),
    "INVALID_RESPONSE": (502, "유효한 AI 응답을 받지 못했습니다."),
    "STEP_LIMIT": (502, "에이전트의 도구 실행 한도를 초과했습니다."),
    "INTERNAL_ERROR": (500, "채팅 처리 중 오류가 발생했습니다."),
}

def manager_from_request(request: Request) -> AgentManager:
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is None or not callable(getattr(manager, "chat", None)):
        raise HTTPException(503, detail={"code": "COACH_CHAT_NOT_CONFIGURED", "message": "채팅 에이전트가 설정되지 않았습니다."})
    return manager

def build_coach_chat_router(authenticate_internal: Callable[..., Any]) -> APIRouter:
    # A required argument makes it impossible to accidentally register a no-auth
    # router. Pass the SAME auth function used by the room/internal endpoints.
    router = APIRouter(prefix="/internal/ai/coach", tags=["coach-chat"],
                       dependencies=[Depends(authenticate_internal)])

    @router.post("/chat", response_model=CoachChatResponse)
    async def chat(request: CoachChatRequest,
                   manager: AgentManager = Depends(manager_from_request)) -> CoachChatResponse:
        try:
            return await manager.chat(request)
        except ChatError as exc:
            status, text = ERRORS.get(exc.code, ERRORS["INTERNAL_ERROR"])
            code = exc.code if exc.code in ERRORS else "INTERNAL_ERROR"
            raise HTTPException(status, detail={"code": "COACH_CHAT_" + code, "message": text}) from None

    return router
