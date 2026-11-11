"""Runtime regressions updated for text/transfer. No real backend/LLM calls."""
from __future__ import annotations
import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from hmac import compare_digest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ValidationError

from app.agent.chat_manager import AgentManager, ChatError, ManagerLimits
from app.api.v1.coach_chat import build_coach_chat_router
from app.core.chat_factory import build_chat_manager
from app.core.chat_llm import SyncLLMChat
from app.modules.agent.contracts import AgentError, AgentInput, AgentResult, ModelReply, ToolCall
from app.modules.agent.simple_agent import SimpleAgent
from app.modules.agent.tools import Tool, ToolContext, ToolExecutor, ToolRegistry
from app.schemas.coach_chat import CoachChatRequest
from app.tools.fintris_chat import build_fintris_chat_tools


def request(n=1, user=1, session="room-1", message=None, **kwargs):
    return CoachChatRequest(userId=user, sessionId=session, requestId=f"request-{n}",
                            text=message or f"message-{n}", **kwargs)


class FakeAgent:
    def __init__(self, gate=None):
        self.requests = []
        self.gate = gate
        self.started = asyncio.Event()
        self.active = 0
        self.max_active = 0
        self.closed = False

    async def run(self, req, tools):
        self.requests.append(deepcopy(req))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
            text = f"reply:{req.message}"
            return AgentResult(text, [{"role": "user", "content": req.message},
                                      {"role": "assistant", "content": text}])
        finally:
            self.active -= 1

    async def aclose(self):
        self.closed = True


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.closed = False

    async def complete(self, messages, tools):
        self.calls.append((deepcopy(messages), deepcopy(tools)))
        value = self.replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def aclose(self):
        self.closed = True


def executor(snapshot=None):
    return ToolExecutor(build_fintris_chat_tools(), ToolContext(1, "s1", "r1", snapshot or {}))


@pytest.mark.asyncio
async def test_same_user_reuses_engine_and_complete_history():
    agent = FakeAgent()
    factory = Mock(return_value=agent)
    manager = AgentManager(factory)
    try:
        a = await manager.chat(request(1))
        b = await manager.chat(request(2))
        assert a.agent_id == b.agent_id
        assert factory.call_count == 1
        assert len(agent.requests[1].history) == 2
        assert agent.requests[1].history[0]["content"] == "message-1"
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_other_sessions_share_identity_but_not_history():
    agent = FakeAgent()
    manager = AgentManager(lambda: agent)
    try:
        a = await manager.chat(request(1, session="a"))
        b = await manager.chat(request(2, session="b"))
        assert a.agent_id == b.agent_id
        assert agent.requests[1].history == []
        await manager.chat(request(3, session="a"))
        assert len(agent.requests[2].history) == 2
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_same_user_fifo_and_other_users_concurrent():
    gate = asyncio.Event()
    agents = []
    def factory():
        agent = FakeAgent(gate)
        agents.append(agent)
        return agent
    manager = AgentManager(factory)
    tasks = []
    try:
        tasks.append(asyncio.create_task(manager.chat(request(1))))
        await asyncio.sleep(.01)
        tasks.append(asyncio.create_task(manager.chat(request(2))))
        tasks.append(asyncio.create_task(manager.chat(request(3, user=2))))
        await asyncio.sleep(.01)
        assert len(agents) == 2
        assert [len(a.requests) for a in agents] == [1, 1]
        gate.set()
        result = await asyncio.gather(*tasks)
        assert [r.result.message for r in result] == ["reply:message-1", "reply:message-2", "reply:message-3"]
        assert agents[0].max_active == 1
        assert agents[1].requests[0].history == []
        assert result[0].agent_id != result[2].agent_id
    finally:
        gate.set()
        await manager.aclose()


@pytest.mark.asyncio
async def test_concurrent_retry_is_one_execution():
    gate = asyncio.Event()
    agent = FakeAgent(gate)
    manager = AgentManager(lambda: agent)
    try:
        one = asyncio.create_task(manager.chat(request()))
        await agent.started.wait()
        two = asyncio.create_task(manager.chat(request()))
        await asyncio.sleep(.01)
        gate.set()
        a, b = await asyncio.gather(one, two)
        assert a == b and len(agent.requests) == 1
        again = await manager.chat(request())
        assert again == a and len(agent.requests) == 1
        again.result.messages[0]["content"] = "client mutation"
        assert (await manager.chat(request())).result.messages[0]["content"] != "client mutation"
    finally:
        gate.set()
        await manager.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"message": "changed"}, {"session": "other"}, {"blocks": []}])
async def test_retry_with_changed_body_conflicts(kwargs):
    manager = AgentManager(FakeAgent)
    try:
        await manager.chat(request())
        with pytest.raises(ChatError, match="REQUEST_ID_CONFLICT"):
            await manager.chat(request(**kwargs))
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_cancel_accepted_work():
    gate = asyncio.Event()
    agent = FakeAgent(gate)
    manager = AgentManager(lambda: agent)
    try:
        call = asyncio.create_task(manager.chat(request()))
        await agent.started.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        retry = asyncio.create_task(manager.chat(request()))
        gate.set()
        assert (await retry).result.message == "reply:message-1"
        assert len(agent.requests) == 1
    finally:
        gate.set()
        await manager.aclose()


@pytest.mark.asyncio
async def test_failure_not_committed_and_same_id_not_rerun():
    agent = FakeAgent()
    original = agent.run
    async def fail_once(req, tools):
        if req.message == "bad":
            agent.requests.append(req)
            raise AgentError("PROVIDER_ERROR")
        return await original(req, tools)
    agent.run = fail_once
    manager = AgentManager(lambda: agent)
    try:
        for _ in range(2):
            with pytest.raises(ChatError, match="PROVIDER_ERROR"):
                await manager.chat(request(message="bad"))
        await manager.chat(request(2))
        assert len(agent.requests) == 2
        assert agent.requests[1].history == []
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_history_bounded_in_complete_turns():
    agent = FakeAgent()
    manager = AgentManager(lambda: agent, limits=ManagerLimits(max_history_turns=1))
    try:
        for i in range(3):
            await manager.chat(request(i))
        assert len(agent.requests[-1].history) == 2
        assert agent.requests[-1].history[0]["content"] == "message-1"
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_history_character_cap():
    agent = FakeAgent()
    manager = AgentManager(lambda: agent, limits=ManagerLimits(max_history_chars=10))
    try:
        await manager.chat(request())
        await manager.chat(request(2))
        assert agent.requests[-1].history == []
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_queue_limit():
    gate = asyncio.Event()
    agent = FakeAgent(gate)
    manager = AgentManager(lambda: agent, limits=ManagerLimits(max_queue_per_user=1))
    try:
        first = asyncio.create_task(manager.chat(request(1)))
        await agent.started.wait()
        second = asyncio.create_task(manager.chat(request(2)))
        await asyncio.sleep(.01)
        with pytest.raises(ChatError, match="QUEUE_FULL"):
            await manager.chat(request(3))
        gate.set()
        await asyncio.gather(first, second)
    finally:
        gate.set()
        await manager.aclose()


@pytest.mark.asyncio
async def test_capacity_limits():
    manager = AgentManager(FakeAgent, limits=ManagerLimits(max_users=1, max_sessions_per_user=1))
    try:
        await manager.chat(request())
        with pytest.raises(ChatError, match="CAPACITY"):
            await manager.chat(request(2, user=2))
        with pytest.raises(ChatError, match="CAPACITY"):
            await manager.chat(request(2, session="second"))
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_idle_cleanup_and_new_agent_after_eviction():
    created = []
    def factory():
        agent = FakeAgent()
        created.append(agent)
        return agent
    manager = AgentManager(factory, limits=ManagerLimits(idle_seconds=.01))
    try:
        one = await manager.chat(request())
        user = await manager.get_agent(1)
        user.last_activity -= 1
        assert await manager.cleanup_idle() == 1
        assert created[0].closed
        assert await manager.get_agent(1) is None
        two = await manager.chat(request(2))
        assert one.agent_id != two.agent_id
        assert created[1].requests[0].history == []
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_cleanup_never_removes_busy_agent():
    gate = asyncio.Event()
    agent = FakeAgent(gate)
    manager = AgentManager(lambda: agent, limits=ManagerLimits(idle_seconds=.01))
    try:
        call = asyncio.create_task(manager.chat(request()))
        await agent.started.wait()
        (await manager.get_agent(1)).last_activity -= 10
        assert await manager.cleanup_idle() == 0
        gate.set()
        await call
    finally:
        gate.set()
        await manager.aclose()


@pytest.mark.asyncio
async def test_archive_hook_receives_history_and_failed_archive_retains_it():
    archived = []
    async def archive(data):
        archived.append(data)
        raise RuntimeError("database offline")
    manager = AgentManager(FakeAgent, on_idle=archive, limits=ManagerLimits(idle_seconds=.01))
    try:
        await manager.chat(request())
        user = await manager.get_agent(1)
        user.last_activity -= 1
        assert await manager.cleanup_idle() == 0
        assert user is await manager.get_agent(1)
        assert not user.retiring
        assert archived[0]["sessions"]["room-1"][0][0]["content"] == "message-1"
        archived[0]["sessions"].clear()
        assert user.rooms
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_shutdown_resolves_active_and_pending_requests():
    gate = asyncio.Event()
    agent = FakeAgent(gate)
    manager = AgentManager(lambda: agent)
    first = asyncio.create_task(manager.chat(request()))
    await agent.started.wait()
    second = asyncio.create_task(manager.chat(request(2)))
    await asyncio.sleep(.01)
    await manager.aclose()
    for result in await asyncio.gather(first, second, return_exceptions=True):
        assert isinstance(result, ChatError) and result.code == "UNAVAILABLE"
    assert agent.closed
    with pytest.raises(ChatError, match="UNAVAILABLE"):
        await manager.chat(request(3))


@pytest.mark.asyncio
async def test_automatic_sweeper_lifecycle():
    manager = AgentManager(FakeAgent, limits=ManagerLimits(idle_seconds=.01, cleanup_interval_seconds=.01))
    await manager.start()
    try:
        await manager.chat(request())
        await asyncio.sleep(.06)
        assert await manager.get_agent(1) is None
    finally:
        await manager.aclose()
    assert manager._sweeper.done()


@pytest.mark.asyncio
async def test_manager_timeout_and_not_configured():
    gate = asyncio.Event()
    manager = AgentManager(lambda: FakeAgent(gate), limits=ManagerLimits(turn_timeout_seconds=.01))
    try:
        with pytest.raises(ChatError, match="TIMEOUT"):
            await manager.chat(request())
    finally:
        await manager.aclose()
    with pytest.raises(ChatError, match="NOT_CONFIGURED"):
        await AgentManager().chat(request())


@pytest.mark.parametrize("kwargs", [{"idle_seconds": 0}, {"max_queue_per_user": -1}])
def test_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        ManagerLimits(**kwargs)


@pytest.mark.asyncio
async def test_generic_loop_reads_tools_and_preserves_protocol():
    model = FakeModel([ModelReply(None, [ToolCall("call-1", "get_blocks", {})], "tool_calls"),
                       ModelReply("생활비 잔액은 10만 원이에요.", finish_reason="stop")])
    result = await SimpleAgent(model, "test system").run(AgentInput("잔액 알려줘"),
            executor({"blocks": [{"blockId": 1, "name": "생활비", "balance": 100000}]}))
    assert result.tools_used == ["get_blocks"]
    assert [x["role"] for x in result.messages] == ["user", "assistant", "tool", "assistant"]
    assert model.calls[0][1][0]["name"] == "get_blocks"
    assert "function" not in model.calls[0][1][0]
    tool_message = model.calls[1][0][-1]
    assert tool_message["tool_call_id"] == "call-1"
    assert json.loads(tool_message["content"])["data"]["blocks"][0]["balance"] == 100000


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [ModelReply(""), ModelReply(None), ModelReply("partial", finish_reason="length"),
                                   ModelReply("x" * 16001)])
async def test_invalid_model_outputs_rejected(reply):
    with pytest.raises(AgentError, match="INVALID_RESPONSE"):
        await SimpleAgent(FakeModel([reply]), "test").run(AgentInput("hi"), executor())


@pytest.mark.asyncio
async def test_unknown_write_tool_is_not_executed():
    model = FakeModel([ModelReply(None, [ToolCall("1", "transfer_funds", {"amount": 50000})]),
                       ModelReply("아직 이동 기능은 연결되지 않았어요.")])
    result = await SimpleAgent(model, "test").run(AgentInput("옮겨줘"), executor())
    assert result.tools_used == []
    assert json.loads(result.messages[2]["content"])["error"] == "TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_tool_limit_and_duplicate_ids():
    repeating = [ModelReply(None, [ToolCall(str(i), "get_blocks", {})]) for i in range(2)]
    with pytest.raises(AgentError, match="STEP_LIMIT"):
        await SimpleAgent(FakeModel(repeating), "test", max_steps=2).run(AgentInput("hi"), executor())
    duplicate = ModelReply(None, [ToolCall("same", "get_blocks", {}), ToolCall("same", "get_blocks", {})])
    with pytest.raises(AgentError, match="INVALID_RESPONSE"):
        await SimpleAgent(FakeModel([duplicate]), "test").run(AgentInput("hi"), executor())


@pytest.mark.asyncio
async def test_snapshot_unknown_and_empty_are_different():
    unknown = await executor().execute("get_blocks", {})
    empty = await executor({"blocks": []}).execute("get_blocks", {})
    assert unknown["data"] == {"available": False, "source": "current_request_snapshot", "blocks": None}
    assert empty["data"]["available"] is True and empty["data"]["blocks"] == []


@pytest.mark.asyncio
async def test_tool_identity_cannot_be_supplied_by_llm_and_results_are_copied():
    tools = executor({"blocks": [{"name": "private", "balance": 1}]})
    assert (await tools.execute("get_blocks", {"userId": 999}))["error"] == "INVALID_ARGUMENTS"
    result = await tools.execute("get_blocks", {})
    result["data"]["blocks"].clear()
    assert (await tools.execute("get_blocks", {}))["data"]["blocks"]


@pytest.mark.asyncio
async def test_tool_failure_hides_raw_exception():
    async def broken(args, ctx):
        raise RuntimeError("token=SUPERSECRET")
    registry = ToolRegistry([Tool("fail", "test", {}, lambda x: x, broken)])
    result = await ToolExecutor(registry, ToolContext(1, "s", "r", {})).execute("fail", {})
    assert result == {"ok": False, "error": "TOOL_FAILED"}


@pytest.mark.parametrize("update", [
    {"userId": True}, {"userId": 0}, {"text": "  "}, {"requestId": ""},
    {"sessionId": ""}, {"token": "not-accepted"}, {"blocks": [{"blockId": 1, "name": "x", "balance": 1.2}]},
])
def test_schema_rejects_invalid_requests(update):
    body = {"userId": 1, "sessionId": "s", "requestId": "r", "text": "hello"}
    body.update(update)
    with pytest.raises(ValidationError):
        CoachChatRequest.model_validate(body)


def test_snapshot_ignores_unknown_sensitive_fields_and_accepts_backend_block_id():
    req = request(blocks=[{"blockId": 5, "name": "living", "balance": 42, "bankPassword": "SECRET"}])
    assert req.snapshot()["blocks"][0] == {"blockId": 5, "name": "living", "balance": 42}


@pytest.mark.asyncio
async def test_sync_llm_adapter_existing_signature_and_no_client_close():
    captured = []
    class Provider:
        def chat(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(content="hello", tool_calls=[], finish_reason="stop")
        def close(self):
            pytest.fail("shared provider must not be closed for one user's eviction")
    options = SimpleNamespace(temperature=.2, max_tokens=512, extra={"request": {"timeout": 10}})
    model = SyncLLMChat(Provider(), "served-model", options)
    result = await model.complete([{"role": "user", "content": "hello"}], [])
    assert result.content == "hello"
    assert captured[0]["model"] == "served-model"
    assert captured[0]["tools"] is None
    assert captured[0]["options"].extra["request"]["timeout"] == 10
    await model.aclose()


@pytest.mark.asyncio
async def test_cancelled_sync_llm_drains_without_executing_late_tools():
    started = threading.Event()
    finish = threading.Event()
    class Provider:
        def chat(self, **kwargs):
            started.set()
            assert finish.wait(2)
            return SimpleNamespace(content=None, tool_calls=[SimpleNamespace(id="late", name="get_blocks", arguments={})], finish_reason="tool_calls")
    model = SyncLLMChat(Provider(), "model", None)
    agent = SimpleAgent(model, "test")
    tools = executor()
    tools.execute = Mock(side_effect=AssertionError("late tool must not execute"))
    task = asyncio.create_task(agent.run(AgentInput("hello"), tools))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.sleep(.01)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tools.execute.call_count == 0
    await agent.aclose()


@pytest.mark.asyncio
async def test_sync_provider_error_is_sanitized():
    class Provider:
        def chat(self, **kwargs):
            raise RuntimeError("secret-token-here")
    model = SyncLLMChat(Provider(), "test", None)
    with pytest.raises(AgentError) as error:
        await model.complete([], [])
    assert error.value.code == "PROVIDER_ERROR"
    assert "secret" not in str(error.value)
    await model.aclose()


def authentication(x_internal_token: str | None = Header(default=None)):
    if x_internal_token is None or not compare_digest(x_internal_token, "test-internal-token"):
        raise HTTPException(401, "Invalid internal service token")


@asynccontextmanager
async def client_for(manager):
    app = FastAPI()
    app.state.agent_manager = manager
    app.include_router(build_coach_chat_router(authentication))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        try:
            yield client
        finally:
            if manager is not None:
                await manager.aclose()


@pytest.mark.asyncio
async def test_http_auth_and_success_contract():
    async with client_for(AgentManager(FakeAgent)) as client:
        body = request().model_dump(by_alias=True, mode="json")
        assert (await client.post("/internal/ai/coach/chat", json=body)).status_code == 401
        response = await client.post("/internal/ai/coach/chat", json=body, headers={"X-Internal-Token": "test-internal-token"})
        assert response.status_code == 200
        assert set(response.json()) == {"text", "transfer"}
        assert response.json()["text"] == "reply:message-1"


@pytest.mark.asyncio
async def test_http_conflict_validation_and_not_initialized():
    headers = {"X-Internal-Token": "test-internal-token"}
    async with client_for(AgentManager(FakeAgent)) as client:
        body = request().model_dump(by_alias=True, mode="json")
        assert (await client.post("/internal/ai/coach/chat", json=body, headers=headers)).status_code == 200
        body["text"] = "changed"
        conflict = await client.post("/internal/ai/coach/chat", json=body, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "COACH_CHAT_REQUEST_ID_CONFLICT"
        body["text"] = ""
        assert (await client.post("/internal/ai/coach/chat", json=body, headers=headers)).status_code == 422
    async with client_for(None) as client:
        assert (await client.post("/internal/ai/coach/chat", json=request().model_dump(by_alias=True), headers=headers)).status_code == 503


@pytest.mark.asyncio
async def test_http_provider_error_hides_sensitive_details():
    class FailingAgent(FakeAgent):
        async def run(self, req, tools):
            raise AgentError("PROVIDER_ERROR")
    async with client_for(AgentManager(FailingAgent)) as client:
        r = await client.post("/internal/ai/coach/chat", json=request().model_dump(by_alias=True),
                              headers={"X-Internal-Token": "test-internal-token"})
        assert r.status_code == 502
        assert r.json()["detail"]["code"] == "COACH_CHAT_PROVIDER_ERROR"


@pytest.mark.asyncio
async def test_factory_uses_injected_llm_and_latest_snapshot_only():
    seen = []
    class Provider:
        def chat(self, messages, **kwargs):
            if messages[-1]["role"] == "user":
                return SimpleNamespace(content=None, tool_calls=[SimpleNamespace(id=f"call-{len(seen)}", name="get_blocks", arguments={})], finish_reason="tool_calls")
            data = json.loads(messages[-1]["content"])["data"]
            seen.append(data)
            return SimpleNamespace(content="자료를 확인했어요.", tool_calls=[], finish_reason="stop")
    manager = build_chat_manager(Provider(), "fake-model", SimpleNamespace(extra={}))
    try:
        await manager.chat(request(blocks=[{"blockId": 1, "name": "x", "balance": 10}]))
        await manager.chat(request(2))
        assert seen[0]["available"] is True
        assert seen[1]["available"] is False
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_legacy_event_contract_same_logical_agent(monkeypatch):
    # Exercise the event DTOs included in the uploaded app.
    manager = AgentManager(FakeAgent)
    try:
        event = SimpleNamespace(user_id=1, event_id="event-1")
        receipt = await manager.accept(event)
        retry = await manager.accept(event)
        chat = await manager.chat(request())
        assert receipt.agent_created and retry.duplicate
        assert receipt.agent_id == chat.agent_id
        assert (await manager.get_status(1)).pending_events == 1
        user = await manager.get_agent(1)
        user.last_activity -= 10000
        assert await manager.cleanup_idle() == 0  # unprocessed events are preserved
        assert user.drain_inbox() == [event]
        assert await manager.cleanup_idle() == 1
    finally:
        await manager.aclose()
