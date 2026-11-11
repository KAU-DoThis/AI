"""Fintris AI server: application setup and service lifecycle."""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.agent.chat_manager import AgentManager, ManagerLimits
from app.api.v1 import classify as classify_api
from app.api.v1.agent_events import router as agent_events_router
from app.api.v1.coach_chat import build_coach_chat_router
from app.api.v1.coach_room import router as coach_room_router
from app.api.v1.initial_blocks import router as initial_blocks_router
from app.core.chat_factory import build_chat_manager
from app.core.classify_config import get_settings as get_classify_settings
from app.core.llm import LLMSettings, create_llm
from app.core.internal_auth import require_internal_token
from app.modules.llm import ChatOptions
from app.services.classify import build_classifier
from app.services.coach_room import CoachRoomService


# Backwards-compatible import name; authentication logic has a single owner.
authenticate_internal = require_internal_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("FINTRIS_INTERNAL_TOKEN"):
        raise RuntimeError(
            "Set FINTRIS_INTERNAL_TOKEN for Spring -> AI authentication"
        )

    app.state.agent_manager = None
    app.state.classifier = None
    app.state.llm = None
    app.state.coach_room_service = None

    llm = None
    chat_manager = None

    try:
        # Preserve the transaction classification service.
        app.state.classifier = build_classifier(get_classify_settings())

        # Reuse one LLM provider for Coach Room and Chat.
        settings = LLMSettings.from_env()
        llm = create_llm(settings)
        app.state.llm = llm

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

        limits = ManagerLimits(
            idle_seconds=float(os.getenv("CHAT_IDLE_SECONDS", "1800")),
            turn_timeout_seconds=float(os.getenv("CHAT_TIMEOUT_SECONDS", "90")),
        )

        if llm is None:
            # The legacy event endpoints still work without an LLM.
            # Chat requests return COACH_CHAT_NOT_CONFIGURED.
            chat_manager = AgentManager(limits=limits)
        else:
            extra = {}
            if str(settings.provider).lower() == "openai":
                # OpenAI-compatible vLLM uses the same request timeout option.
                extra["request"] = {
                    "timeout": float(os.getenv("CHAT_LLM_TIMEOUT_SECONDS", "45"))
                }

            chat_options = ChatOptions(
                temperature=0.3,
                max_tokens=1024,
                extra=extra,
            )
            chat_manager = build_chat_manager(
                llm=llm,
                model=settings.model,
                options=chat_options,
                limits=limits,
            )

        # Both legacy event APIs and the new chat endpoint use this manager.
        app.state.agent_manager = chat_manager
        await chat_manager.start()
        yield
    finally:
        try:
            # Stop per-user agents before closing their shared LLM client.
            if chat_manager is not None:
                await chat_manager.aclose()
        finally:
            app.state.agent_manager = None
            app.state.coach_room_service = None
            app.state.llm = None
            if llm is not None:
                await asyncio.to_thread(llm.close)


def create_app() -> FastAPI:
    application = FastAPI(
        title="Fintris AI Server",
        version="0.2.0",
        lifespan=lifespan,
    )

    # Existing endpoints.
    application.include_router(agent_events_router)
    application.include_router(initial_blocks_router)
    application.include_router(classify_api.router)
    application.include_router(coach_room_router)

    # New chat endpoint: reuse the existing internal token authentication.
    application.include_router(build_coach_chat_router(authenticate_internal))

    @application.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request,
        exc: RequestValidationError,
    ):
        # Neither room nor chat validation errors should echo financial snapshots.
        if request.url.path.rstrip("/") in {
            "/internal/ai/coach/room",
            "/internal/ai/coach/chat",
        }:
            errors = [
                {key: error[key] for key in ("loc", "type", "msg")}
                for error in exc.errors()
            ]
            return JSONResponse(status_code=422, content={"detail": errors})
        return await request_validation_exception_handler(request, exc)

    @application.get("/health")
    def health() -> dict[str, str]:
        # Keep the existing contract unchanged for backend compatibility.
        return {"status": "ok", "agent_runtime": "stub"}

    return application


app = create_app()
