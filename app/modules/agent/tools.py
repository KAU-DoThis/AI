"""Generic tools with request-scoped execution receipts; no financial concepts."""
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


class ToolRejected(Exception):
    """A safe, deliberately public code, never an exception/SDK error string."""
    def __init__(self, code: str):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code):
            raise ValueError("Invalid public tool error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    validate: Callable[[dict[str, Any]], dict[str, Any]]
    handler: Callable[[dict[str, Any], ToolContext], Awaitable[Any]]


@dataclass(frozen=True)
class ToolExecution:
    """A receipt created by the executor, never reconstructed from LLM prose."""
    name: str
    output: dict[str, Any]


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
        # Existing BaseLLM accepts FLAT schemas, not provider-specific wrappers.
        return deepcopy([
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self._tools.values()
        ])

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, context: ToolContext,
                 max_result_chars: int = 32000):
        if max_result_chars < 1:
            raise ValueError("max_result_chars must be positive")
        self._registry = registry
        self._context = deepcopy(context)
        self._max_result_chars = max_result_chars
        self._executions: list[ToolExecution] = []

    def schemas(self) -> list[dict[str, Any]]:
        return self._registry.schemas()

    def executions(self) -> tuple[ToolExecution, ...]:
        """Return isolated receipts for THIS turn only; callers cannot edit them."""
        return tuple(deepcopy(self._executions))

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        output = await self._execute(name, arguments)
        # Keep an isolated copy before the agent sees or mutates the return value.
        self._executions.append(ToolExecution(name, deepcopy(output)))
        return output

    async def _execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(name, str):
            return {"ok": False, "error": "TOOL_NOT_FOUND"}
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
            # Identity is host-owned; changes to the copied context do not persist.
            result = await tool.handler(validated, deepcopy(self._context))
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(encoded) > self._max_result_chars:
                return {"ok": False, "error": "TOOL_RESULT_TOO_LARGE"}
            return {"ok": True, "data": json.loads(encoded)}
        except ToolRejected as exc:
            return {"ok": False, "error": exc.code}
        except Exception:
            # asyncio cancellation is a BaseException and must propagate.
            return {"ok": False, "error": "TOOL_FAILED"}
