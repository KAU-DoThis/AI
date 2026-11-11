from typing import Any

from ..base import BaseLLM, build_function_tools
from ..types import ChatOptions, LLMResponse, ToolCall
from ..observation import observe, ollama_response


class OllamaLLM(BaseLLM):

    def __init__(
        self,
        host: str = "http://localhost:11434",
        *,
        timeout: float | None = None,
    ):
        try:
            from ollama import Client
        except ImportError as e:
            raise RuntimeError(
                "Ollama provider를 사용하려면 "
                "'ollama' 패키지가 필요합니다. "
                "pip install ollama"
            ) from e

        self.host = host
        self.client = Client(host=host, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def get_available_models(self) -> list[str]:
        response = self.client.list()

        return [model.model for model in response.models if model.model]

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
        # 공통 옵션 → Ollama options 변환
        # -----------------------------

        ollama_options: dict[str, Any] = {}

        if options.temperature is not None:
            ollama_options["temperature"] = options.temperature

        if options.top_p is not None:
            ollama_options["top_p"] = options.top_p

        if options.max_tokens is not None:
            ollama_options["num_predict"] = options.max_tokens

        if options.seed is not None:
            ollama_options["seed"] = options.seed

        if options.stop is not None:
            ollama_options["stop"] = options.stop

        # Ollama 전용 generation options
        #
        # extra={
        #     "options": {
        #         "top_k": 40,
        #         "repeat_penalty": 1.1
        #     }
        # }
        extra_options = options.extra.get("options")

        if isinstance(extra_options, dict):
            ollama_options.update(extra_options)

        if ollama_options:
            kwargs["options"] = ollama_options

        # -----------------------------
        # Ollama top-level 전용 옵션
        # -----------------------------

        top_level_keys = (
            "think",
            "format",
            "keep_alive",
            "logprobs",
            "top_logprobs",
        )

        for key in top_level_keys:
            if key in options.extra:
                kwargs[key] = options.extra[key]

        observe("adapter_request", {"adapter": "ollama", "boundary": "sdk_kwargs", "kwargs": kwargs})
        response = self.client.chat(**kwargs)
        observe("adapter_response", lambda: {"adapter": "ollama", "boundary": "sdk_response_projection",
                                              "response": ollama_response(response)})

        message = response.message

        tool_calls: list[ToolCall] = []

        if message.tool_calls:
            for call in message.tool_calls:
                arguments = call.function.arguments or {}

                tool_calls.append(
                    ToolCall(
                        name=call.function.name,
                        arguments=dict(arguments),
                    )
                )

        # Ollama는 일반 답변(content), 추론(thinking), Tool Calls를
        # 별도로 반환한다. content가 빈 문자열이어도 Tool Call이면 정상이다.
        prompt_tokens = getattr(response, "prompt_eval_count", None)
        completion_tokens = getattr(response, "eval_count", None)

        usage: dict[str, Any] = {}
        if prompt_tokens is not None:
            usage["prompt_tokens"] = prompt_tokens
        if completion_tokens is not None:
            usage["completion_tokens"] = completion_tokens
        if prompt_tokens is not None and completion_tokens is not None:
            usage["total_tokens"] = prompt_tokens + completion_tokens

        # Ollama의 시간 값은 나노초 단위다.
        for key in (
            "total_duration",
            "load_duration",
            "prompt_eval_duration",
            "eval_duration",
        ):
            value = getattr(response, key, None)
            if value is not None:
                usage[f"{key}_ns"] = value

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=getattr(response, "done_reason", None),
            thinking=getattr(message, "thinking", None),
            usage=usage or None,
        )

    def _convert_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:

        result: list[dict[str, Any]] = []

        for message in messages:
            role = message["role"]

            # Assistant가 Tool을 호출했던 메시지
            if role == "assistant" and message.get("tool_calls"):

                tool_calls = []

                for call in message["tool_calls"]:
                    tool_calls.append(
                        {
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"],
                            }
                        }
                    )

                api_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.get("content", ""),
                    "tool_calls": tool_calls,
                }
                # Ollama는 Tool Call과 함께 생성된 thinking도
                # 다음 대화의 assistant 메시지로 받을 수 있다.
                if message.get("thinking") is not None:
                    api_message["thinking"] = message["thinking"]
                result.append(api_message)

            # Tool 실행 결과
            elif role == "tool":

                tool_message: dict[str, Any] = {
                    "role": "tool",
                    "content": str(message["content"]),
                }

                if "name" in message:
                    tool_message["tool_name"] = message["name"]

                result.append(tool_message)

            else:

                api_message = {
                    "role": role,
                    "content": message.get("content", ""),
                }
                if role == "assistant" and message.get("thinking") is not None:
                    api_message["thinking"] = message["thinking"]
                result.append(api_message)

        return result
