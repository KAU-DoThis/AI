"""Fintris AI server: assemble routes and own the provider lifecycle."""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.agent.manager import AgentManager
from app.api.v1.agent_events import router as agent_events_router
from app.api.v1.coach_room import router as coach_room_router
from app.api.v1.initial_blocks import router as initial_blocks_router
from app.core.llm import LLMSettings, create_llm
from app.services.coach_room import CoachRoomService


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("FINTRIS_INTERNAL_TOKEN"):
        raise RuntimeError("Set FINTRIS_INTERNAL_TOKEN for Spring -> AI authentication")
    app.state.agent_manager = AgentManager()
    app.state.llm = None
    app.state.coach_room_service = None
    settings = LLMSettings.from_env()
    llm = create_llm(settings)
    app.state.llm = llm
    try:
        if llm is not None:
            app.state.coach_room_service = CoachRoomService(
                llm=llm,
                model=settings.model,
                timeout_seconds=settings.timeout_seconds,
                max_tokens=settings.max_tokens,
                temperature=settings.temperature,
                output_mode=settings.output_mode,
                provider=settings.provider,
            )
        yield
    finally:
        app.state.coach_room_service = None
        app.state.llm = None
        if llm is not None:
            await asyncio.to_thread(llm.close)


def create_app() -> FastAPI:
    application = FastAPI(title="Fintris AI Server", version="0.2.0", lifespan=lifespan)
    application.include_router(agent_events_router)
    application.include_router(initial_blocks_router)
    application.include_router(coach_room_router)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.rstrip("/") == "/internal/ai/coach/room":
            # A missing field can otherwise echo the entire financial snapshot.
            errors = [
                {key: error[key] for key in ("loc", "type", "msg")}
                for error in exc.errors()
            ]
            return JSONResponse(status_code=422, content={"detail": errors})
        return await request_validation_exception_handler(request, exc)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "agent_runtime": "stub"}

    return application


app = create_app()
