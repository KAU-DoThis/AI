"""One logical user agent per process. The manager knows only the Agent protocol."""
from __future__ import annotations
import asyncio
import json
import logging
import time
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Awaitable, Callable
from app.agent.chat_session import ChatJob, UserSession
from app.modules.agent.contracts import Agent, AgentError, AgentInput, AgentResult
from app.modules.agent.tools import ToolContext, ToolExecutor, ToolRegistry
from app.schemas.coach_chat import CoachChatRequest, CoachChatResponse

log = logging.getLogger(__name__)

class ChatError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

@dataclass(frozen=True)
class ManagerLimits:
    idle_seconds: float = 1800
    cleanup_interval_seconds: float = 30
    turn_timeout_seconds: float = 90
    max_users: int = 256
    max_queue_per_user: int = 16
    max_sessions_per_user: int = 16
    max_history_turns: int = 12
    max_history_chars: int = 48000
    max_receipts_per_user: int = 128

    def __post_init__(self):
        if any(value <= 0 for value in vars(self).values()):
            raise ValueError("Manager limits must be positive")

class AgentManager:
    def __init__(self, agent_factory: Callable[[], Agent] | None = None,
                 tools: ToolRegistry | None = None, limits: ManagerLimits | None = None,
                 on_idle: Callable[[dict[str, Any]], Awaitable[None]] | None = None):
        self._factory = agent_factory
        self._tools = tools if tools is not None else ToolRegistry([])
        self._limits = limits or ManagerLimits()
        self._on_idle = on_idle
        self._agents: dict[int, UserSession] = {}
        self._lock = asyncio.Lock()
        self._closing = False
        self._sweeper: asyncio.Task | None = None

    async def start(self) -> None:
        if self._closing:
            raise ChatError("UNAVAILABLE")
        if self._sweeper is None:
            self._sweeper = asyncio.create_task(self._sweep(), name="fintris-agent-idle-cleanup")

    def _get_or_create(self, user_id: int) -> tuple[UserSession, bool]:
        # Caller owns _lock. No awaits in this method.
        if self._closing:
            raise ChatError("UNAVAILABLE")
        existing = self._agents.get(user_id)
        if existing is not None:
            if existing.retiring:
                raise ChatError("UNAVAILABLE")
            return existing, False
        if len(self._agents) >= self._limits.max_users:
            raise ChatError("CAPACITY")
        user = UserSession(user_id=user_id)
        self._agents[user_id] = user
        return user, True

    async def chat(self, request: CoachChatRequest) -> CoachChatResponse:
        if self._factory is None:
            raise ChatError("NOT_CONFIGURED")
        request = request.model_copy(deep=True)
        payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        fingerprint = sha256(payload.encode()).hexdigest()
        async with self._lock:
            user, _ = self._get_or_create(request.user_id)
            user.last_activity = time.monotonic()
            previous = user.receipts.get(request.request_id)
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    raise ChatError("REQUEST_ID_CONFLICT")
                future = previous.future
            else:
                if user.queue.qsize() >= self._limits.max_queue_per_user:
                    raise ChatError("QUEUE_FULL")
                if request.session_id not in user.rooms:
                    if len(user.rooms) >= self._limits.max_sessions_per_user:
                        raise ChatError("CAPACITY")
                future = asyncio.get_running_loop().create_future()
                # Consume exceptions even when the HTTP caller disconnects.
                future.add_done_callback(lambda f: None if f.cancelled() else f.exception())
                job = ChatJob(request, fingerprint, future)
                user.rooms.setdefault(request.session_id, [])
                user.receipts[request.request_id] = job
                user.queue.put_nowait(job)
                self._prune_receipts(user)
                if user.worker is None or user.worker.done():
                    user.worker = asyncio.create_task(self._worker(user), name="fintris-user-worker")
        # Cancellation of an HTTP wait does not cancel accepted work.
        # Retry the SAME requestId and body to retrieve that work's result.
        return (await asyncio.shield(future)).model_copy(deep=True)

    def _prune_receipts(self, user: UserSession) -> None:
        for key in list(user.receipts):
            if len(user.receipts) <= self._limits.max_receipts_per_user:
                break
            if user.receipts[key].future.done():
                del user.receipts[key]

    async def _worker(self, user: UserSession) -> None:
        while True:
            job = await user.queue.get()
            user.running = True
            try:
                if user.engine is None:
                    assert self._factory is not None
                    user.engine = self._factory()
                turns = user.rooms[job.request.session_id]
                history = [deepcopy(message) for turn in turns for message in turn]
                tool_context = ToolContext(user.user_id, job.request.session_id,
                                           job.request.request_id, job.request.snapshot())
                async with asyncio.timeout(self._limits.turn_timeout_seconds):
                    result = await user.engine.run(
                        AgentInput(job.request.message, history), ToolExecutor(self._tools, tool_context))
                if not isinstance(result, AgentResult) or not result.message.strip():
                    raise AgentError("INVALID_RESPONSE")
                response = CoachChatResponse(request_id=job.request.request_id, user_id=user.user_id,
                                             session_id=job.request.session_id, agent_id=user.agent_id,
                                             message=result.message, tools_used=result.tools_used)
                # Only commit successful COMPLETE turns; never split tool sequences.
                turns.append(deepcopy(result.messages))
                while len(turns) > self._limits.max_history_turns or (
                    turns and len(json.dumps(turns, ensure_ascii=False)) > self._limits.max_history_chars
                ):
                    turns.pop(0)
                if not job.future.done():
                    job.future.set_result(response)
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.set_exception(ChatError("UNAVAILABLE"))
                raise
            except TimeoutError:
                if not job.future.done():
                    job.future.set_exception(ChatError("TIMEOUT"))
            except AgentError as exc:
                if not job.future.done():
                    job.future.set_exception(ChatError(exc.code))
            except Exception:
                if not job.future.done():
                    job.future.set_exception(ChatError("INTERNAL_ERROR"))
            finally:
                user.running = False
                user.last_activity = time.monotonic()
                user.queue.task_done()
                self._prune_receipts(user)

    # Existing receive-only event endpoints can use this SAME manager instance.
    # Events remain queued; this patch does not silently run classification work.
    async def accept(self, event):
        from app.models import EventReceipt
        async with self._lock:
            user, created = self._get_or_create(event.user_id)
            accepted = user.enqueue(event)
            user.last_activity = time.monotonic()
            return EventReceipt(event_id=event.event_id, user_id=event.user_id,
                                agent_id=user.agent_id, agent_created=created,
                                duplicate=not accepted, pending_events=len(user.inbox))

    async def get_status(self, user_id: int):
        async with self._lock:
            user = self._agents.get(user_id)
            return None if user is None else user.status()

    async def get_agent(self, user_id: int) -> UserSession | None:
        async with self._lock:
            return self._agents.get(user_id)

    async def cleanup_idle(self) -> int:
        now = time.monotonic()
        async with self._lock:
            stale = [u for u in self._agents.values()
                     if not u.retiring and not u.running and u.queue.empty() and not u.inbox
                     and now - u.last_activity >= self._limits.idle_seconds]
            for user in stale:
                user.retiring = True
        removed = 0
        for user in stale:
            try:
                if self._on_idle is not None:
                    await self._on_idle({"userId": user.user_id, "agentId": user.agent_id,
                                         "sessions": deepcopy(user.rooms)})
            except Exception:
                # Never discard history after a configured persistence hook fails.
                user.retiring = False
                user.last_activity = time.monotonic()
                log.warning("Agent archive hook failed; retaining in-memory state")
                continue
            await self._stop_user(user)
            async with self._lock:
                if self._agents.get(user.user_id) is user:
                    del self._agents[user.user_id]
                    removed += 1
        return removed

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(self._limits.cleanup_interval_seconds)
            await self.cleanup_idle()

    async def _stop_user(self, user: UserSession) -> None:
        if user.worker is not None:
            user.worker.cancel()
            await asyncio.gather(user.worker, return_exceptions=True)
        while not user.queue.empty():
            job = user.queue.get_nowait()
            if not job.future.done():
                job.future.set_exception(ChatError("UNAVAILABLE"))
            user.queue.task_done()
        if user.engine is not None:
            try:
                await user.engine.aclose()
            except Exception:
                log.warning("Agent close failed")

    async def aclose(self) -> None:
        async with self._lock:
            if self._closing:
                return
            self._closing = True
        if self._sweeper is not None:
            self._sweeper.cancel()
            await asyncio.gather(self._sweeper, return_exceptions=True)
        await asyncio.gather(*(self._stop_user(u) for u in list(self._agents.values())))
        self._agents.clear()
