import json
from typing import Any

from ..base import BaseLLM, build_function_tools
from ..types import ChatOptions, LLMResponse, ToolCall
from ..observation import observe, openai_response
from ..errors import context_overflow


class OpenAILLM(BaseLLM):

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
    ):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "OpenAI Compatible provider를 사용하려면 "
                "'openai' 패키지가 필요합니다. "
                "pip install openai"
            ) from e

        self.base_url = base_url
        self.api_key = api_key

        client_options: dict[str, Any] = {"base_url": base_url, "api_key": api_key}
        if timeout is not None:
            client_options["timeout"] = timeout
        if max_retries is not None:
            client_options["max_retries"] = max_retries
        self.client = OpenAI(**client_options)

    def close(self) -> None:
        self.client.close()

    def get_available_models(self) -> list[str]:
        response = self.client.models.list()

        return [
            model.id
            for model in response.data
            if model.id
        ]

    def models(self) -> list[str]:
        return self.get_available_models()

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        options: ChatOptions | None = None,
    ) -> LLMResponse:

        options = options or ChatOptions()

        api_messages = self._convert_messages(messages)
        api_tools = build_function_tools(tools)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": api_messages,
        }

        if api_tools:
            kwargs["tools"] = api_tools

        # -----------------------------
        # 공통 옵션
        # -----------------------------

        if options.temperature is not None:
            kwargs["temperature"] = options.temperature

        if options.top_p is not None:
            kwargs["top_p"] = options.top_p

        if options.max_tokens is not None:
            kwargs["max_tokens"] = options.max_tokens

        if options.seed is not None:
            kwargs["seed"] = options.seed

        if options.stop is not None:
            kwargs["stop"] = options.stop

        # -----------------------------
        # OpenAI-compatible 전용 옵션
        # -----------------------------
        #
        # vLLM 예:
        #
        # extra={
        #     "extra_body": {
        #         "top_k": 40
        #     }
        # }
        #

        extra_body = options.extra.get("extra_body")

        if isinstance(extra_body, dict):
            kwargs["extra_body"] = extra_body

        # 추가 OpenAI ChatCompletion 옵션을 직접 전달하고 싶을 때
        #
        # extra={
        #     "request": {
        #         "frequency_penalty": 0.2,
        #         "presence_penalty": 0.1
        #     }
        # }
        request_options = options.extra.get("request")

        if isinstance(request_options, dict):
            kwargs.update(request_options)

        observe("adapter_request", {"adapter": "openai", "boundary": "sdk_kwargs", "kwargs": kwargs})
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            overflow = context_overflow(exc)
            if overflow is not None:
                raise overflow from exc
            raise
        observe("adapter_response", lambda: {"adapter": "openai", "boundary": "sdk_response_projection",
                                              "response": openai_response(response)})

        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []

        if message.tool_calls:
            for call in message.tool_calls:

                arguments = self._parse_arguments(
                    call.function.arguments
                )

                tool_calls.append(
                    ToolCall(
                        id=call.id,
                        name=call.function.name,
                        arguments=arguments,
                    )
                )

        usage = (
            response.usage.model_dump(exclude_none=True)
            if response.usage is not None
            else None
        )

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            thinking=self._extract_thinking(message),
            usage=usage,
        )

    def _convert_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:

        result: list[dict[str, Any]] = []

        for message in messages:

            role = message["role"]

            # Assistant Tool Call
            if role == "assistant" and message.get("tool_calls"):

                tool_calls = []

                for call in message["tool_calls"]:

                    tool_calls.append(
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(
                                    call["arguments"],
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    )

                result.append(
                    {
                        "role": "assistant",
                        "content": message.get("content", ""),
                        "tool_calls": tool_calls,
                    }
                )

            # Tool 실행 결과
            elif role == "tool":

                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": message["tool_call_id"],
                        "content": str(message["content"]),
                    }
                )

            else:

                result.append(
                    {
                        "role": role,
                        "content": message.get("content", ""),
                    }
                )

        return result

    @staticmethod
    def _extract_thinking(message: Any) -> str | None:
        """OpenAI 호환 서버가 노출하는 추론 필드만 읽는다.

        공식 Chat Completions가 내부 추론을 제공하지 않는 경우에는 None이다.
        """
        for name in ("reasoning_content", "reasoning"):
            value = getattr(message, name, None)
            if isinstance(value, str):
                return value

        extra = getattr(message, "model_extra", None)
        if isinstance(extra, dict):
            for name in ("reasoning_content", "reasoning"):
                value = extra.get(name)
                if isinstance(value, str):
                    return value

        return None

    @staticmethod
    def _parse_arguments(
        arguments: str,
    ) -> dict[str, Any]:

        if not arguments:
            return {}

        try:
            result = json.loads(arguments)

            if isinstance(result, dict):
                return result

        except json.JSONDecodeError:
            pass

        # JSON 파싱에 실패해도 호출 자체를 버리지는 않는다.
        return {
            "_raw": arguments
        }
