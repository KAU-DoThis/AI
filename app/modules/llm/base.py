from abc import ABC, abstractmethod
from typing import Any

from .types import ChatOptions, LLMResponse


class BaseLLM(ABC):

    @abstractmethod
    def models(self) -> list[str]:
        """
        return available model list
        """
        pass

    @abstractmethod
    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        options: ChatOptions | None = None,
    ) -> LLMResponse:
        """
        perform chat request with the specified model
        """
        pass


    def close(self) -> None:
        """Release provider resources when the owning application shuts down."""
        return None


def build_function_tools(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """
    ToolRegistry.schemas()의 공통 Tool 형식을
    LLM API의 function tool 형식으로 변환한다.

    입력 예:

    {
        "name": "add",
        "description": "두 숫자를 더합니다.",
        "parameters": {...}
    }

    출력 예:

    {
        "type": "function",
        "function": {
            "name": "add",
            "description": "...",
            "parameters": {...}
        }
    }
    """

    if not tools:
        return []

    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in tools
    ]
