"""Offline tests against the uploaded app plus this patch; real services excluded."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.chat_manager import AgentManager, ChatError, ManagerLimits
from app.core.chat_factory import build_chat_manager
from app.modules.agent.contracts import AgentError, AgentInput, AgentResult, ModelReply, ToolCall
from app.modules.agent.simple_agent import SimpleAgent
from app.modules.agent.tools import ToolContext, ToolExecutor
from app.modules.llm import ChatOptions, LLMResponse
from app.schemas.coach_chat import CoachChatRequest, CoachChatResponse, TransferProposal
from app.schemas.coach_room import BlockContext, WaitingFundContext
from app.services.coach_chat import build_chat_response, finalize_chat_execution
from app.tools.fintris_chat import build_fintris_chat_tools, TRANSFER_TOOL_NAME

BLOCKS = [
    {"blockId": 1, "name": "생활비", "balance": 250000, "statusLabel": "사용 중"},
    {"blockId": 2, "name": "여행", "balance": 100000},
    {"blockId": 3, "name": "저축", "balance": 300000},
]
FUNDS = [{"waitingFundId": 7, "amount": 10000, "description": "급여"}]
ARGS = {"from_block_id": 1, "to_block_id": 2, "amount": 50000}
HEADERS = {"X-Internal-Token": "offline-test-token"}
CHAT_PATH = "/internal/ai/coach/chat"


def body(**updates):
    data = {"userId": 123, "sessionId": "room-123", "text": "생활비에서 여행으로 5만 원 옮겨줘.",
            "blocks": deepcopy(BLOCKS), "waitingFunds": deepcopy(FUNDS)}
    data.update(updates)
    return data


def request(**updates):
    return CoachChatRequest.model_validate(body(**updates))


def executor(snapshot=None):
    return ToolExecutor(build_fintris_chat_tools(), ToolContext(
        123, "room-123", "internal-trace", request().snapshot() if snapshot is None else snapshot
    ))


class ScriptedAgent:
    """Different engine implementation: it depends only on run/aclose/tools.execute."""
    def __init__(self, attempts=None, fail=False, wait=False):
        self.attempts = attempts if attempts is not None else [ARGS]
        self.fail, self.wait = fail, wait
        self.inputs = []
        self.closed = False

    async def run(self, req, tools):
        self.inputs.append(deepcopy(req))
        for args in self.attempts:
            await tools.execute(TRANSFER_TOOL_NAME, args)
        if self.wait:
            await asyncio.sleep(100)
        if self.fail:
            raise AgentError("PROVIDER_ERROR")
        # Intentionally false model wording: the host must not publish it.
        text = "이동을 완료했어요."
        return AgentResult(text, [{"role": "user", "content": req.message},
                                  {"role": "assistant", "content": text}])

    async def aclose(self):
        self.closed = True


def manager_for(agent, **kw):
    return AgentManager(lambda: agent, tools=build_fintris_chat_tools(),
                        result_processor=finalize_chat_execution, **kw)


def test_request_uses_text_and_request_id_is_optional():
    data = request()
    assert data.text.startswith("생활비")
    assert data.request_id is None
    assert isinstance(data.blocks[0], BlockContext)
    assert isinstance(data.waiting_funds[0], WaitingFundContext)
    assert data.snapshot()["waitingFunds"] == FUNDS
    assert data.snapshot()["blocks"][0]["statusLabel"] == "사용 중"


def test_room_session_id_is_preserved_exactly():
    assert request(sessionId=" room-123 ").session_id == " room-123 "
    assert request(requestId=" r-1 ").request_id == " r-1 "


@pytest.mark.parametrize("update", [
    {"text": " "}, {"text": 5}, {"text": None}, {"userId": True}, {"userId": 0},
    {"sessionId": " "}, {"sessionId": 123}, {"requestId": " "}, {"requestId": 5},
    {"waitingFunds": [{"amount": 5}]}, {"message": "legacy"},
])
def test_invalid_input_rejected(update):
    with pytest.raises(ValidationError):
        request(**update)


def test_old_message_only_contract_is_rejected():
    data = body()
    data["message"] = data.pop("text")
    with pytest.raises(ValidationError):
        CoachChatRequest.model_validate(data)


def test_none_empty_and_shared_room_balance_semantics():
    assert request(blocks=None).snapshot()["blocks"] is None
    assert request(blocks=[]).snapshot()["blocks"] == []
    # Room allows a negative balance; chat must not reject the same backend DTO.
    assert request(blocks=[{"blockId": 1, "name": "x", "balance": -1}]).blocks[0].balance == -1
    assert request(waitingFunds=[{"waitingFundId": 7, "amount": 0, "label": None}]).waiting_funds[0].label is None


@pytest.mark.parametrize("amount", [0, -1, True, 1.5, "50000", None])
def test_transfer_amount_is_a_strict_positive_integer(amount):
    with pytest.raises(ValidationError):
        TransferProposal(fromBlockId=1, toBlockId=2, amount=amount)


def test_same_block_and_extra_identity_are_rejected():
    with pytest.raises(ValidationError):
        TransferProposal(fromBlockId=1, toBlockId=1, amount=5)
    with pytest.raises(ValidationError):
        TransferProposal(fromBlockId=1, toBlockId=2, amount=5, confirmed=True)
    assert CoachChatResponse(text="hi").model_dump(by_alias=True) == {"text": "hi", "transfer": None}


@pytest.mark.asyncio
async def test_tool_creates_receipt_without_mutating_snapshot():
    snapshot = request().snapshot()
    original = deepcopy(snapshot)
    tools = executor(snapshot)
    result = await tools.execute(TRANSFER_TOOL_NAME, ARGS)
    assert result["ok"] is True
    assert result["data"]["transfer"] == {"fromBlockId": 1, "toBlockId": 2, "amount": 50000}
    assert result["data"]["status"] == "PROPOSAL_PREPARED"
    assert snapshot == original
    assert (await tools.execute("get_blocks", {}))["data"]["blocks"] == original["blocks"]


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {**ARGS, "amount": 0}, {**ARGS, "amount": True}, {**ARGS, "from_block_id": True},
    {**ARGS, "to_block_id": 1}, {**ARGS, "amount": "50000"},
    {**ARGS, "userId": 999}, {**ARGS, "confirmed": True},
    {"fromBlockId": 1, "toBlockId": 2, "amount": 50000}, {},
])
async def test_bad_tool_arguments_do_not_create_success(args):
    tools = executor()
    assert (await tools.execute(TRANSFER_TOOL_NAME, args)) == {"ok": False, "error": "INVALID_ARGUMENTS"}
    assert not tools.executions()[0].output["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot,error", [
    ({}, "BLOCKS_NOT_PROVIDED"), ({"blocks": None}, "BLOCKS_NOT_PROVIDED"),
    ({"blocks": []}, "BLOCK_NOT_FOUND"),
    ({"blocks": [BLOCKS[0]]}, "BLOCK_NOT_FOUND"),
    ({"blocks": [BLOCKS[0], BLOCKS[0], BLOCKS[1]]}, "AMBIGUOUS_BLOCK_ID"),
])
async def test_only_current_snapshot_ids_are_usable(snapshot, error):
    assert (await executor(snapshot).execute(TRANSFER_TOOL_NAME, ARGS))["error"] == error


@pytest.mark.asyncio
async def test_executor_receipts_are_isolated_from_model_mutation():
    tools = executor()
    output = await tools.execute(TRANSFER_TOOL_NAME, ARGS)
    output["data"]["transfer"]["amount"] = 999
    receipt = tools.executions()
    receipt[0].output["data"]["transfer"]["amount"] = 888
    assert tools.executions()[0].output["data"]["transfer"]["amount"] == 50000
    assert executor().executions() == ()


@pytest.mark.asyncio
async def test_backend_validates_balance_not_snapshot_tool():
    # This only builds a proposal. Ownership, balance, version and limits are
    # revalidated by the backend; the tool cannot authorize a real mutation.
    result = await executor().execute(TRANSFER_TOOL_NAME, {**ARGS, "amount": 99999999})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_replaceable_agent_and_safe_wording_match_stored_history():
    agent = ScriptedAgent()
    manager = manager_for(agent)
    try:
        run = await manager.chat(request())
        out = build_chat_response(run)
        assert out.transfer.amount == 50000
        assert "아직 잔액은 변경되지 않았어요" in out.text
        assert "이동을 완료" not in out.text
        user = await manager.get_agent(123)
        assert user.rooms["room-123"][-1][-1]["content"] == out.text
        assert run.result.message == out.text
    finally:
        await manager.aclose()
    assert agent.closed


@pytest.mark.asyncio
async def test_identical_calls_yield_one_proposal():
    manager = manager_for(ScriptedAgent([ARGS, ARGS]))
    try:
        assert build_chat_response(await manager.chat(request())).transfer.amount == 50000
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_different_successes_fail_closed_instead_of_selecting_last():
    manager = manager_for(ScriptedAgent([ARGS, {**ARGS, "to_block_id": 3}]))
    try:
        out = build_chat_response(await manager.chat(request()))
        assert out.transfer is None
        assert "한 건" in out.text
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_rejected_later_attempt_does_not_resurrect_earlier_proposal():
    manager = manager_for(ScriptedAgent([ARGS, {**ARGS, "to_block_id": 999}]))
    try:
        assert build_chat_response(await manager.chat(request())).transfer is None
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_corrected_invalid_attempt_may_succeed():
    manager = manager_for(ScriptedAgent([{**ARGS, "amount": -1}, ARGS]))
    try:
        assert build_chat_response(await manager.chat(request())).transfer.amount == 50000
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_proposal_does_not_leak_to_next_turn_or_other_user():
    agents = []
    def factory():
        agent = ScriptedAgent()
        agents.append(agent)
        return agent
    manager = AgentManager(factory, tools=build_fintris_chat_tools(), result_processor=finalize_chat_execution)
    try:
        assert build_chat_response(await manager.chat(request())).transfer is not None
        agents[0].attempts = []
        assert build_chat_response(await manager.chat(request(text="고마워"))).transfer is None
        # A new user has no block snapshot and cannot use someone else's IDs.
        assert build_chat_response(await manager.chat(request(userId=999, blocks=[]))).transfer is None
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_no_request_id_means_distinct_work_even_with_same_body():
    agent = ScriptedAgent([])
    manager = manager_for(agent)
    try:
        a, b = await manager.chat(request()), await manager.chat(request())
        assert a.request_id != b.request_id
        assert len(agent.inputs) == 2
        assert len((await manager.get_agent(123)).receipts) == 0
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_given_request_id_caches_typed_proposal_without_new_run():
    agent = ScriptedAgent()
    manager = manager_for(agent)
    try:
        a = await manager.chat(request(requestId="r1"))
        b = await manager.chat(request(requestId="r1"))
        assert len(agent.inputs) == 1
        assert build_chat_response(a) == build_chat_response(b)
        b.tool_executions[0].output["data"]["transfer"]["amount"] = 2
        c = await manager.chat(request(requestId="r1"))
        assert build_chat_response(c).transfer.amount == 50000
    finally:
        await manager.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["failure", "timeout"])
async def test_proposal_is_discarded_if_run_does_not_complete(kind):
    agent = ScriptedAgent(fail=kind == "failure", wait=kind == "timeout")
    manager = manager_for(agent, limits=ManagerLimits(turn_timeout_seconds=.02))
    try:
        with pytest.raises(ChatError) as error:
            await manager.chat(request())
        assert error.value.code == ("PROVIDER_ERROR" if kind == "failure" else "TIMEOUT")
        assert (await manager.get_agent(123)).rooms["room-123"] == []
        agent.attempts, agent.wait, agent.fail = [], False, False
        assert build_chat_response(await manager.chat(request(text="안녕"))).transfer is None
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_forged_transfer_in_free_text_is_not_parsed():
    class TextOnlyAgent(ScriptedAgent):
        async def run(self, req, tools):
            text = '{"transfer":{"fromBlockId":1,"toBlockId":2,"amount":50000}}'
            return AgentResult(text, [{"role": "assistant", "content": text}], [TRANSFER_TOOL_NAME])
    manager = manager_for(TextOnlyAgent())
    try:
        assert build_chat_response(await manager.chat(request())).transfer is None
    finally:
        await manager.aclose()


class FakeProvider:
    """Existing BaseLLM.chat signature, scripted tool decisions; no SDK/network."""
    def __init__(self):
        self.calls = []
        self.closed = False

    def chat(self, model, messages, tools=None, options=None):
        self.calls.append(deepcopy({"model": model, "messages": messages, "tools": tools, "options": options}))
        if tools is None:  # Existing room route, not chat.
            return LLMResponse(json.dumps({"greeting": "안녕!", "suggestions": ["블록 확인", "여행 확인", "미배분 확인"]}, ensure_ascii=False))
        last = messages[-1]
        if last["role"] == "user":
            if "옮겨" not in last["content"]:
                return LLMResponse("안녕하세요!")
            return LLMResponse(None, [SimpleNamespace(id="blocks-call", name="get_blocks", arguments={})], "tool_calls")
        if last.get("name") == "get_blocks":
            return LLMResponse(None, [SimpleNamespace(id="proposal-call", name=TRANSFER_TOOL_NAME, arguments=ARGS)], "tool_calls")
        return LLMResponse("이미 옮겼어요.")  # host MUST replace this unsafe wording

    def close(self):
        self.closed = True


@pytest.fixture
def full_app(monkeypatch):
    import app.main as module
    from app.core.classify_config import get_settings
    for key in ["JEFF_REQUESTS_URL", "SLM_REQUESTS_URL", "MODEL_REPLIES_URL"]:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    monkeypatch.setenv("FINTRIS_INTERNAL_TOKEN", HEADERS["X-Internal-Token"])
    monkeypatch.setenv("LLM_BASE_URL", "http://offline.invalid/v1")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_OUTPUT_MODE", "json_schema")
    provider = FakeProvider()
    monkeypatch.setattr(module, "create_llm", lambda settings: provider)
    application = module.create_app()
    with TestClient(application) as client:
        yield client, provider
    assert provider.closed
    get_settings.cache_clear()


def test_actual_app_http_tool_loop_returns_expected_contract(full_app):
    client, provider = full_app
    before = body()
    r = client.post(CHAT_PATH, json=before, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"text", "transfer"}
    assert r.json()["transfer"] == {"fromBlockId": 1, "toBlockId": 2, "amount": 50000}
    assert "아직 잔액은 변경되지 않았어요" in r.json()["text"]
    assert "이미 옮겼어요" not in r.json()["text"]
    assert len(provider.calls) == 3
    assert provider.calls[0]["tools"][2]["name"] == TRANSFER_TOOL_NAME
    assert "response_format" not in provider.calls[0]["options"].extra.get("request", {})
    assert body() == before  # this process made no balance changes
    normal = client.post(CHAT_PATH, json=body(text="고마워"), headers=HEADERS)
    assert normal.json() == {"text": "안녕하세요!", "transfer": None}
    assert "아직 잔액은 변경되지 않았어요" in provider.calls[-1]["messages"][-2]["content"]


def test_actual_app_internal_auth_and_sanitized_validation(full_app):
    client, provider = full_app
    assert client.post(CHAT_PATH, json=body()).status_code == 401
    assert client.post(CHAT_PATH, json=body(), headers={"X-Internal-Token": "wrong"}).status_code == 401
    bad = body(blocks=[{"blockId": "PRIVATE-ACCOUNT-STRING", "name": "PRIVATE-NAME", "balance": "SECRET"}])
    r = client.post(CHAT_PATH, json=bad, headers=HEADERS)
    assert r.status_code == 422
    assert "PRIVATE" not in r.text and "SECRET" not in r.text
    assert provider.calls == []


def test_openapi_contract_matches_backend(full_app):
    client, _ = full_app
    spec = client.get("/openapi.json").json()
    req = spec["components"]["schemas"]["CoachChatRequest"]
    res = spec["components"]["schemas"]["CoachChatResponse"]
    assert "text" in req["required"]
    assert "requestId" not in req["required"]
    assert "message" not in req["properties"]
    assert set(res["properties"]) == {"text", "transfer"}
    assert spec["paths"][CHAT_PATH]["post"]["security"]


def test_existing_room_health_categories_events_and_classify_still_work(full_app):
    client, _ = full_app
    assert client.get("/health").json() == {"status": "ok", "agent_runtime": "stub"}
    room = body()
    room.pop("text")
    r = client.post("/internal/ai/coach/room", json=room, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"greeting", "suggestions"}
    assert client.get("/internal/v1/categories", headers=HEADERS).status_code == 200
    event = {"event_id": "e1", "user_id": 123, "event_type": "chat_message", "payload": {"text": "hello"}}
    assert client.post("/internal/v1/agent/events", json=event, headers=HEADERS).status_code == 202
    assert client.get("/internal/v1/agents/123", headers=HEADERS).json()["pending_events"] == 1
    tx = {"transactions": [{"transactionId": 1, "occurredAt": "2026-11-01T12:00:00",
                            "amount": 5000, "direction": "OUT", "merchantOrPayee": "스타벅스",
                            "accountType": "CARD", "isCanceled": False, "isOwnAccountTransfer": False}]}
    result = client.post("/internal/v1/classify", json=tx, headers=HEADERS)
    assert result.status_code == 200, result.text
    assert len(result.json()["results"]) == 1
    initial = client.post("/internal/v1/classify/initial", json=tx, headers=HEADERS)
    assert initial.status_code == 200, initial.text


def test_auth_dependency_has_one_implementation():
    from app.main import authenticate_internal
    from app.core.internal_auth import require_internal_token
    assert authenticate_internal is require_internal_token


def test_missing_llm_keeps_event_endpoints_but_chat_returns_503(monkeypatch):
    import app.main as module
    from app.core.classify_config import get_settings
    monkeypatch.setenv("FINTRIS_INTERNAL_TOKEN", HEADERS["X-Internal-Token"])
    for key in ("JEFF_REQUESTS_URL", "SLM_REQUESTS_URL", "MODEL_REPLIES_URL"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(module, "create_llm", lambda settings: None)
    with TestClient(module.create_app()) as client:
        response = client.post(CHAT_PATH, json=body(), headers=HEADERS)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "COACH_CHAT_NOT_CONFIGURED"
        event = {"event_id": "e-no-llm", "user_id": 123, "event_type": "chat_message", "payload": {}}
        assert client.post("/internal/v1/agent/events", json=event, headers=HEADERS).status_code == 202
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_invalid_receipt_does_not_commit_a_successful_turn():
    from app.modules.agent.tools import Tool, ToolRegistry
    async def broken_handler(args, context):
        return {"status": "PROPOSAL_PREPARED", "requiresUserConfirmation": True,
                "transfer": {"fromBlockId": 1, "toBlockId": 2, "amount": -10},
                "fromBlockName": "x", "toBlockName": "y"}
    tools = ToolRegistry([Tool(TRANSFER_TOOL_NAME, "broken test adapter", {}, lambda args: args, broken_handler)])
    manager = AgentManager(ScriptedAgent, tools=tools, result_processor=finalize_chat_execution)
    try:
        with pytest.raises(ChatError, match="INVALID_RESPONSE"):
            await manager.chat(request())
        assert (await manager.get_agent(123)).rooms["room-123"] == []
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_engine_step_limit_does_not_publish_prepared_transfer():
    class LoopingModel:
        async def complete(self, messages, tools):
            return ModelReply(None, [ToolCall("call", TRANSFER_TOOL_NAME, ARGS)], "tool_calls")
        async def aclose(self):
            pass
    manager = AgentManager(
        lambda: SimpleAgent(LoopingModel(), "test", max_steps=1),
        tools=build_fintris_chat_tools(), result_processor=finalize_chat_execution,
    )
    try:
        with pytest.raises(ChatError, match="STEP_LIMIT"):
            await manager.chat(request())
        assert (await manager.get_agent(123)).rooms["room-123"] == []
    finally:
        await manager.aclose()
