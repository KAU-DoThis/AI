"""Adapter for the existing synchronous BaseLLM.chat API; no new LLM SDK."""
from __future__ import annotations
import asyncio
from copy import deepcopy
from typing import Any
from app.modules.agent.contracts import AgentError, ModelReply, ToolCall

class SyncLLMChat:
    def __init__(self, llm: Any, model: str, options: Any):
        if not model.strip():
            raise ValueError("LLM model is required")
        self._llm, self._model, self._options = llm, model, options
        self._inflight: set[asyncio.Task] = set()

    async def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        call = asyncio.create_task(asyncio.to_thread(
            self._llm.chat, model=self._model, messages=deepcopy(messages),
            tools=deepcopy(tools) or None, options=deepcopy(self._options)))
        self._inflight.add(call)
        try:
            try:
                result = await asyncio.shield(call)
            except asyncio.CancelledError:
                # A Python thread cannot be killed. Drain the SDK call before
                # allowing this user's next turn; never execute its late tool calls.
                try:
                    await asyncio.shield(call)
                except Exception:
                    pass
                raise
            return ModelReply(result.content, [ToolCall(c.id, c.name, deepcopy(c.arguments))
                                               for c in result.tool_calls], result.finish_reason)
        except TimeoutError:
            raise AgentError("TIMEOUT") from None
        except AgentError:
            raise
        except Exception as exc:
            # SDK error strings can contain URLs, tokens and financial payloads.
            timeout_types = {"TimeoutException", "TimeoutError", "APITimeoutError",
                             "ReadTimeout", "ConnectTimeout", "PoolTimeout", "WriteTimeout"}
            category = "TIMEOUT" if any(c.__name__ in timeout_types for c in type(exc).__mro__) else "PROVIDER_ERROR"
            raise AgentError(category) from None
        finally:
            if call.done():
                self._inflight.discard(call)

    async def aclose(self) -> None:
        # The shared BaseLLM/HTTP client belongs to the application's lifespan.
        # Closing one user's engine must not close the provider used by others.
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
            self._inflight.clear()
