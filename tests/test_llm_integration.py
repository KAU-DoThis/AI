"""Exercise the user's provider through the real SDK without making network calls."""

import json

import httpx
import openai
import pytest

from app.core.llm import LLMSettings, create_llm
from app.modules.llm import ChatOptions, ContextOverflow
from app.modules.llm.provider.openai import OpenAILLM
from app.schemas.coach_room import CoachRoomRequest
from app.services.coach_room import CoachRoomService


ROOM_RESULT = {
    "greeting": "안녕하세요. 이번 달 블록을 함께 살펴볼까요?",
    "suggestions": ["이번 달 소비 알려줘", "블록 현황 보여줘", "확인할 거래 보여줘"],
}


def completion_payload():
    return {
        "id": "completion-offline-1",
        "object": "chat.completion",
        "created": 1,
        "model": "served-test-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(ROOM_RESULT, ensure_ascii=False),
                    "reasoning_content": "Internal model reasoning is separate.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
    }


@pytest.fixture
def provider_factory(monkeypatch):
    """Keep the adapter and SDK real; replace only HTTP transport."""
    original_openai = openai.OpenAI
    providers = []

    def make(handler):
        def offline_client(**kwargs):
            return original_openai(
                **kwargs,
                http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            )

        monkeypatch.setattr(openai, "OpenAI", offline_client)
        provider = OpenAILLM(
            base_url="http://offline.test/v1",
            api_key="offline-test-key",
            timeout=11.0,
            max_retries=0,
        )
        providers.append(provider)
        return provider

    yield make
    for provider in providers:
        provider.client.close()


def test_provider_forwards_json_schema_and_per_call_timeout(provider_factory):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=completion_payload())

    provider = provider_factory(handle)
    output_schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "coach_room",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "greeting": {"type": "string"},
                    "suggestions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                },
                "required": ["greeting", "suggestions"],
                "additionalProperties": False,
            },
        },
    }
    result = provider.chat(
        model="served-test-model",
        messages=[{"role": "system", "content": "Return the room JSON object."}],
        options=ChatOptions(
            temperature=0.4,
            max_tokens=512,
            extra={"request": {"response_format": output_schema, "timeout": 2.5}},
        ),
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/v1/chat/completions"
    body = json.loads(request.content)
    assert body["response_format"] == output_schema
    assert body["model"] == "served-test-model"
    assert body["max_tokens"] == 512
    assert "tools" not in body
    assert set(request.extensions["timeout"].values()) == {2.5}
    assert json.loads(result.content) == ROOM_RESULT
    assert result.tool_calls == []
    assert result.finish_reason == "stop"
    assert result.thinking == "Internal model reasoning is separate."
    assert result.usage["total_tokens"] == 50


@pytest.mark.parametrize(
    ("status_code", "error_code", "exception_type"),
    [
        (400, "context_length_exceeded", ContextOverflow),
        (503, "service_unavailable", openai.InternalServerError),
    ],
)
def test_provider_preserves_error_category_without_sdk_retries(
    provider_factory, status_code, error_code, exception_type
):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            status_code,
            json={
                "error": {
                    "message": "Offline provider rejected this request.",
                    "type": "invalid_request_error",
                    "code": error_code,
                }
            },
        )

    provider = provider_factory(handle)
    with pytest.raises(exception_type):
        provider.chat(model="served-test-model", messages=[])

    assert len(requests) == 1
    assert set(requests[0].extensions["timeout"].values()) == {11.0}


def test_provider_surfaces_http_timeout(provider_factory):
    requests = []

    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout("Offline read deadline expired", request=request)

    provider = provider_factory(handle)
    with pytest.raises(openai.APITimeoutError):
        provider.chat(model="served-test-model", messages=[])

    assert len(requests) == 1


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_TIMEOUT_SECONDS", "0"),
        ("LLM_TIMEOUT_SECONDS", "nan"),
        ("LLM_MAX_TOKENS", "2.5"),
        ("LLM_PROVIDER", "unknown-provider"),
        ("LLM_OUTPUT_MODE", "unsupported-mode"),
        ("LLM_BASE_URL", "not-a-url"),
    ],
)
def test_invalid_provider_configuration_names_the_setting(name, value):
    with pytest.raises(ValueError, match=name):
        LLMSettings.from_env({name: value})


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["openai", "ollama"])
async def test_room_service_uses_native_sdk_schema_and_closes_transport(
    monkeypatch, provider_name
):
    """Cover factory -> service -> adapter -> SDK with only HTTP replaced."""
    requests = []

    def handle(request):
        requests.append(request)
        if provider_name == "openai":
            return httpx.Response(200, json=completion_payload())
        return httpx.Response(
            200,
            json={
                "model": "served-test-model",
                "created_at": "2026-11-10T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(ROOM_RESULT, ensure_ascii=False),
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 20,
                "eval_count": 30,
            },
        )

    class TrackedTransport(httpx.MockTransport):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    transport = TrackedTransport(handle)
    if provider_name == "openai":
        original_client = openai.OpenAI

        def offline_client(**kwargs):
            return original_client(**kwargs, http_client=httpx.Client(transport=transport))

        monkeypatch.setattr(openai, "OpenAI", offline_client)
        base_url = "http://offline.test/v1"
    else:
        ollama = pytest.importorskip("ollama")
        original_client = ollama.Client

        def offline_client(**kwargs):
            return original_client(**kwargs, transport=transport)

        monkeypatch.setattr(ollama, "Client", offline_client)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        base_url = "http://offline.test"

    settings = LLMSettings.from_env(
        {
            "LLM_PROVIDER": provider_name,
            "LLM_BASE_URL": base_url,
            "LLM_MODEL": "served-test-model",
            "LLM_API_KEY": "offline-test-key",
            "LLM_TIMEOUT_SECONDS": "3",
        }
    )
    provider = create_llm(settings)
    assert provider is not None
    assert requests == [], "Factory initialization must not contact the model"
    try:
        service = CoachRoomService(
            llm=provider,
            model=settings.model,
            timeout_seconds=settings.timeout_seconds,
            provider=settings.provider,
        )
        result = await service.generate(
            CoachRoomRequest(
                userId=42, sessionId="offline-session", blocks=[], waitingFunds=[]
            )
        )
        assert result.model_dump() == ROOM_RESULT
        assert len(requests) == 1
        request = requests[0]
        body = json.loads(request.content)
        if provider_name == "openai":
            assert request.url.path == "/v1/chat/completions"
            schema = body["response_format"]["json_schema"]["schema"]
        else:
            assert request.url.path == "/api/chat"
            schema = body["format"]
            assert body["options"]["num_predict"] == 512
        assert schema["properties"]["suggestions"]["minItems"] == 3
        assert schema["properties"]["suggestions"]["maxItems"] == 3
        assert set(request.extensions["timeout"].values()) == {3.0}
        assert transport.closed is False
    finally:
        provider.close()
    assert transport.closed is True
