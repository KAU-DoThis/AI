"""Generic tool registry and executor; identity comes from the host, not the LLM."""
from __future__ import annotations
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

@dataclass(frozen=True)
class ToolContext:
    user_id: int
    session_id: str
    request_id: str
    snapshot: dict[str, Any]

class InvalidToolArguments(ValueError):
    pass

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    validate: Callable[[dict[str, Any]], dict[str, Any]]
    handler: Callable[[dict[str, Any], ToolContext], Awaitable[Any]]

class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", tool.name):
                raise ValueError("Invalid tool name")
            if tool.name in self._tools:
                raise ValueError("Duplicate tool name")
            self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        # The existing Fintris BaseLLM accepts FLAT schemas, not OpenAI wrappers.
        return deepcopy([{"name": t.name, "description": t.description,
                          "parameters": t.parameters} for t in self._tools.values()])

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

class ToolExecutor:
    def __init__(self, registry: ToolRegistry, context: ToolContext, max_result_chars: int = 32000):
        self._registry = registry
        self._context = context
        self._max_result_chars = max_result_chars

    def schemas(self) -> list[dict[str, Any]]:
        return self._registry.schemas()

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self._registry.get(name)
        if tool is None:
            return {"ok": False, "error": "TOOL_NOT_FOUND"}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "INVALID_ARGUMENTS"}
        try:
            validated = tool.validate(deepcopy(arguments))
        except (ValueError, TypeError):
            return {"ok": False, "error": "INVALID_ARGUMENTS"}
        try:
            # One request-specific identity; never accept userId/token as arguments.
            result = await tool.handler(validated, deepcopy(self._context))
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(encoded) > self._max_result_chars:
                return {"ok": False, "error": "TOOL_RESULT_TOO_LARGE"}
            return {"ok": True, "data": json.loads(encoded)}
        except Exception:
            # asyncio cancellation is a BaseException and propagates normally.
            return {"ok": False, "error": "TOOL_FAILED"}
