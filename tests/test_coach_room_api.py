"""Room contract, provider failures, and request isolation using offline providers."""

import asyncio
import copy
import importlib
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_coach_room_service
from app.main import app
from app.modules.llm import BaseLLM, LLMResponse, ToolCall
from app.schemas.coach_room import CoachRoomRequest
from app.services.coach_room import CoachRoomService


ROOM_PATH = "/internal/ai/coach/room"
TOKEN = "room-test-internal-token"
ROOM_RESULT = {
    "greeting": "안녕하세요. 이번 달 블록을 함께 살펴볼까요?",
    "suggestions": ["이번 달 소비 알려줘", "블록 현황 보여줘", "확인할 거래 보여줘"],
}


class RecordingLLM(BaseLLM):
    def __init__(self, response=None):
        self.response = response or LLMResponse(
            content=json.dumps(ROOM_RESULT, ensure_ascii=False), finish_reason="stop"
        )
        self.calls = []

    def models(self):
        return ["test-room-model"]

    def chat(self, model, messages, tools=None, options=None):
        self.calls.append(
            {
                "model": model,
                "messages": copy.deepcopy(messages),
                "tools": tools,
                "options": options,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def make_service(llm):
    return CoachRoomService(
        llm=llm, model="test-room-model", timeout_seconds=1.0
    )


@pytest.fixture
def room_context():
    return {
        "userId": 42,
        "sessionId": "room-session-test",
        "blocks": [
            {"blockId": 10, "name": "생활비", "balance": 150000},
            {"blockId": 11, "name": "여행", "balance": 200000},
        ],
        "waitingFunds": [{"waitingFundId": 20, "amount": 100000, "label": "급여"}],
    }


@pytest.fixture
def room_env(monkeypatch):
    monkeypatch.setenv("FINTRIS_INTERNAL_TOKEN", TOKEN)
    for name in (
        "LLM_PROVIDER",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_TOKENS",
        "LLM_TEMPERATURE",
        "LLM_OUTPUT_MODE",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def provider():
    return RecordingLLM()


@pytest.fixture
def client(room_env, provider):
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_coach_room_service] = lambda: make_service(provider)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def post_room(client, payload, token=TOKEN):
    headers = {"X-Internal-Token": token} if token is not None else {}
    return client.post(ROOM_PATH, json=payload, headers=headers)


def test_real_app_returns_greeting_and_exactly_three_suggestions(
    client, provider, room_context
):
    response = post_room(client, room_context)

    assert response.status_code == 200
    assert response.json() == ROOM_RESULT
    assert len(provider.calls) == 1
    assert provider.calls[0]["model"] == "test-room-model"
    assert not provider.calls[0]["tools"]
    prompt = json.dumps(provider.calls[0]["messages"], ensure_ascii=False)
    assert "생활비" in prompt
    assert "150000" in prompt
    assert "100000" in prompt
    assert "room-session-test" not in prompt


def test_empty_context_lists_are_allowed(client, provider, room_context):
    room_context["blocks"] = []
    room_context["waitingFunds"] = []

    response = post_room(client, room_context)

    assert response.status_code == 200
    assert len(response.json()["suggestions"]) == 3
    assert len(provider.calls) == 1


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_invalid_internal_token_does_not_call_provider(
    client, provider, room_context, token
):
    assert post_room(client, room_context, token).status_code == 401
    assert provider.calls == []


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("userId", True),
        ("userId", 0),
        ("sessionId", "  "),
        ("blocks", None),
        ("waitingFunds", None),
        ("waitingFunds", [{"waitingFundId": 1, "amount": -1}]),
    ],
)
def test_invalid_context_is_rejected_before_provider(
    client, provider, room_context, field, invalid_value
):
    room_context[field] = invalid_value
    assert post_room(client, room_context).status_code == 422
    assert provider.calls == []


@pytest.mark.parametrize("field", ["blocks", "waitingFunds"])
def test_context_lists_are_required(client, provider, room_context, field):
    del room_context[field]
    assert post_room(client, room_context).status_code == 422
    assert provider.calls == []


def test_validation_error_does_not_echo_financial_snapshot(client, provider, room_context):
    room_context["waitingFunds"][0]["description"] = "PRIVATE_FINANCIAL_DETAIL_912"
    del room_context["blocks"]
    response = post_room(client, room_context)
    assert response.status_code == 422
    assert "PRIVATE_FINANCIAL_DETAIL_912" not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "blocks"]
    assert set(response.json()["detail"][0]) == {"loc", "type", "msg"}
    assert provider.calls == []


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        " ",
        None,
        "[]",
        json.dumps({"greeting": "안녕", "suggestions": ["하나", "둘"]}),
        json.dumps({"greeting": "안녕", "suggestions": ["하나", "둘", "셋", "넷"]}),
        json.dumps({"greeting": "안녕", "suggestions": ["하나", "하나", "셋"]}),
        json.dumps({"greeting": "안녕", "suggestions": ["하나", " ", "셋"]}),
        json.dumps({"greeting": " ", "suggestions": ["하나", "둘", "셋"]}),
    ],
)
def test_invalid_provider_output_is_not_reported_as_success(
    client, provider, room_context, content
):
    provider.response = LLMResponse(content=content, finish_reason="stop")

    response = post_room(client, room_context)

    assert response.status_code == 502
    assert "suggestions" not in response.json()
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        LLMResponse(content=json.dumps(ROOM_RESULT), finish_reason="length"),
        LLMResponse(
            content=json.dumps(ROOM_RESULT),
            finish_reason="tool_calls",
            tool_calls=[ToolCall(name="transfer_funds", arguments={})],
        ),
    ],
)
def test_incomplete_or_tool_call_response_is_rejected(
    client, provider, room_context, response
):
    provider.response = response
    assert post_room(client, room_context).status_code == 502
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    ("failure", "status_code"),
    [(TimeoutError("private timeout detail"), 504), (RuntimeError("private provider detail"), 502)],
)
def test_provider_failure_returns_bounded_error_without_private_detail(
    client, provider, room_context, failure, status_code
):
    provider.response = failure

    response = post_room(client, room_context)

    assert response.status_code == status_code
    assert "private" not in response.text
    assert len(provider.calls) == 1


def test_room_unconfigured_does_not_break_existing_routes(room_env, room_context):
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        room_response = post_room(client, room_context)
        assert room_response.status_code == 503
        initial_response = client.post(
            "/internal/v1/initial-blocks",
            json={"request_id": "empty-onboarding", "user": {"user_id": 42}, "transactions": []},
            headers={"X-Internal-Token": TOKEN},
        )
        assert initial_response.status_code == 200


def test_application_closes_its_provider_on_shutdown(
    room_env, room_context, monkeypatch
):
    class ClosableLLM(RecordingLLM):
        closed = False

        def close(self):
            self.closed = True

    provider = ClosableLLM()
    monkeypatch.setenv("LLM_BASE_URL", "http://unused.test/v1")
    monkeypatch.setenv("LLM_MODEL", "test-room-model")
    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "create_llm", lambda settings: provider)

    with TestClient(app) as client:
        assert provider.calls == [], "Starting the app must not call the model"
        assert provider.closed is False
        assert post_room(client, room_context).status_code == 200
        assert len(provider.calls) == 1

    assert provider.closed is True
    assert app.state.llm is None
    assert app.state.coach_room_service is None


@pytest.mark.asyncio
async def test_sync_provider_does_not_block_event_loop(room_context):
    started = threading.Event()
    release = threading.Event()
    loop_thread = threading.get_ident()
    provider_threads = []

    class WaitingLLM(RecordingLLM):
        def chat(self, model, messages, tools=None, options=None):
            provider_threads.append(threading.get_ident())
            started.set()
            if not release.wait(timeout=1.5):
                raise RuntimeError("The event loop did not release the provider.")
            return super().chat(model, messages, tools=tools, options=options)

    task = asyncio.create_task(make_service(WaitingLLM()).generate(CoachRoomRequest(**room_context)))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.005)
        assert started.is_set(), "Provider was never invoked"
        assert provider_threads[0] != loop_thread
        assert not task.done(), "Provider should still be waiting while the loop is free"
    finally:
        release.set()
    result = await task
    assert result.model_dump() == ROOM_RESULT


@pytest.mark.asyncio
async def test_shared_service_keeps_each_requests_context_separate(room_context):
    provider = RecordingLLM()
    service = make_service(provider)
    context_a = copy.deepcopy(room_context)
    context_b = copy.deepcopy(room_context)
    context_a["blocks"][0]["name"] = "USER_ALPHA_ONLY_721"
    context_b["blocks"][0]["name"] = "USER_BETA_ONLY_842"
    context_b["userId"] = 43
    context_b["sessionId"] = "another-opaque-session"
    requests = [CoachRoomRequest(**context_a), CoachRoomRequest(**context_b)]
    before = [request.model_dump() for request in requests]

    results = await asyncio.gather(*(service.generate(request) for request in requests))

    assert [request.model_dump() for request in requests] == before
    assert len(provider.calls) == 2
    prompts = [json.dumps(call["messages"], ensure_ascii=False) for call in provider.calls]
    assert sum("USER_ALPHA_ONLY_721" in prompt for prompt in prompts) == 1
    assert sum("USER_BETA_ONLY_842" in prompt for prompt in prompts) == 1
    assert all(not ("USER_ALPHA_ONLY_721" in prompt and "USER_BETA_ONLY_842" in prompt) for prompt in prompts)
    assert all(result.model_dump() == ROOM_RESULT for result in results)
