"""A small bounded LLM/tool loop. Conversation ownership stays with the manager."""
from __future__ import annotations
import json
from copy import deepcopy
from .contracts import AgentError, AgentInput, AgentResult, ChatModel, ModelReply
from .tools import ToolExecutor

class SimpleAgent:
    def __init__(self, model: ChatModel, system_prompt: str, max_steps: int = 6,
                 max_tool_calls: int = 12):
        if max_steps < 1 or max_tool_calls < 1:
            raise ValueError("Agent limits must be positive")
        self._model = model
        self._system_prompt = system_prompt
        self._max_steps = max_steps
        self._max_tool_calls = max_tool_calls

    async def run(self, request: AgentInput, tools: ToolExecutor) -> AgentResult:
        turn = [{"role": "user", "content": request.message}]
        messages = [{"role": "system", "content": self._system_prompt},
                    *deepcopy(request.history), *deepcopy(turn)]
        used: list[str] = []
        count = 0
        seen_ids: set[str] = set()
        for _ in range(self._max_steps):
            reply = await self._model.complete(deepcopy(messages), tools.schemas())
            if not isinstance(reply, ModelReply):
                raise AgentError("INVALID_RESPONSE")
            if reply.content is not None and not isinstance(reply.content, str):
                raise AgentError("INVALID_RESPONSE")
            if len(reply.content or "") > 16000:
                raise AgentError("INVALID_RESPONSE")
            if reply.tool_calls:
                if reply.finish_reason not in {None, "stop", "tool_calls"}:
                    raise AgentError("INVALID_RESPONSE")
                count += len(reply.tool_calls)
                if count > self._max_tool_calls:
                    raise AgentError("STEP_LIMIT")
                for call in reply.tool_calls:
                    if (not isinstance(call.id, str) or not call.id or call.id in seen_ids
                            or not isinstance(call.name, str) or not isinstance(call.arguments, dict)):
                        raise AgentError("INVALID_RESPONSE")
                    seen_ids.add(call.id)
                assistant = {"role": "assistant", "content": reply.content or "",
                             "tool_calls": [{"id": c.id, "name": c.name,
                                             "arguments": c.arguments} for c in reply.tool_calls]}
                messages.append(deepcopy(assistant))
                turn.append(deepcopy(assistant))
                for call in reply.tool_calls:
                    output = await tools.execute(call.name, call.arguments)
                    tool_message = {"role": "tool", "tool_call_id": call.id, "name": call.name,
                                    "content": json.dumps(output, ensure_ascii=False)}
                    messages.append(tool_message)
                    turn.append(deepcopy(tool_message))
                    if output["ok"] and call.name not in used:
                        used.append(call.name)
                continue
            if reply.finish_reason not in {None, "stop"} or not (reply.content or "").strip():
                raise AgentError("INVALID_RESPONSE")
            text = reply.content.strip()
            turn.append({"role": "assistant", "content": text})
            return AgentResult(text, turn, used)
        raise AgentError("STEP_LIMIT")

    async def aclose(self) -> None:
        await self._model.aclose()
