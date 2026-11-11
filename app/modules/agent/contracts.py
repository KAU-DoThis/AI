"""Replaceable agent contract. No FastAPI, database or Fintris dependency."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol, TYPE_CHECKING
if TYPE_CHECKING:
    from .tools import ToolExecutor

Message = dict[str, Any]

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass(frozen=True)
class ModelReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None

@dataclass(frozen=True)
class AgentInput:
    message: str
    history: list[Message] = field(default_factory=list)

@dataclass(frozen=True)
class AgentResult:
    message: str
    # A complete new turn, including tool calls/results, NOT the entire history.
    messages: list[Message]
    tools_used: list[str] = field(default_factory=list)

class ChatModel(Protocol):
    async def complete(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelReply: ...
    async def aclose(self) -> None: ...

class Agent(Protocol):
    async def run(self, request: AgentInput, tools: ToolExecutor) -> AgentResult: ...
    async def aclose(self) -> None: ...

class AgentError(Exception):
    """Only a public category; never put SDK responses or secrets in API errors."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
