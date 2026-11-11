"""Optional per-call observation hooks.

Context variables propagate into asyncio.to_thread. A consumer must marshal
records back to its owning loop; adapters never own a shared task recorder.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable


Observer = Callable[[str, Any], None]
_observer: ContextVar[Observer | None] = ContextVar("shs_llm_observer", default=None)
_missing = object()


@dataclass(frozen=True)
class ObservationUnavailable:
    """A failed projection, distinct from a body containing diagnostic-looking keys."""
    reason: str


@contextmanager
def observation_scope(observer: Observer | None):
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)


def observe(kind: str, data: Any) -> None:
    observer = _observer.get()
    if observer is not None:
        if callable(data):
            try:
                data = data()
            except Exception as exc:
                data = ObservationUnavailable(f"projection_error:{type(exc).__name__}")
        observer(kind, data)


def fields(value: Any, names: tuple[str, ...]) -> dict[str, Any]:
    """Project explicitly named response data, not an arbitrary SDK object's internals."""
    result = {}
    for name in names:
        try:
            item = value.get(name, _missing) if isinstance(value, dict) else getattr(value, name, _missing)
        except Exception as exc:
            item = ObservationUnavailable(f"attribute_error:{type(exc).__name__}")
        if item is not _missing:
            result[name] = item
    return result


def function_fields(call: Any, names: tuple[str, ...]) -> Any:
    function = fields(call, ("function",)).get("function")
    return function if isinstance(function, ObservationUnavailable) else fields(function, names)


def common_response(response: Any) -> dict[str, Any]:
    result = fields(response, ("content", "thinking", "reasoning", "reasoning_content",
                               "tool_calls", "finish_reason", "usage", "metadata"))

    calls = result.get("tool_calls")
    if isinstance(calls, (list, tuple)):
        result["tool_calls"] = [fields(call, ("id", "name", "arguments")) or call for call in calls]
    if not result:
        result["unavailable_response"] = response

    return {"response_type": f"{type(response).__module__}.{type(response).__qualname__}",
            "fields": result}


def openai_response(response: Any) -> dict[str, Any]:
    result = fields(response, ("id", "object", "model", "created", "service_tier", "system_fingerprint", "model_extra"))
    usage = getattr(response, "usage", None)
    result["usage"] = fields(usage, ("prompt_tokens", "completion_tokens", "total_tokens", "model_extra")) if usage else None

    if usage is not None:
        for name, names in (
            ("prompt_tokens_details", ("audio_tokens", "cached_tokens")),
            ("completion_tokens_details", ("audio_tokens", "reasoning_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens")),
        ):
            detail = getattr(usage, name, None)
            if detail is not None:
                result["usage"][name] = fields(detail, names + ("model_extra",))

    result["choices"] = []
    for choice in getattr(response, "choices", ()):
        entry = fields(choice, ("index", "finish_reason", "model_extra"))
        message = getattr(choice, "message", None)
        entry["message"] = fields(message, ("role", "content", "thinking", "reasoning", "reasoning_content",
                                              "refusal", "annotations", "audio", "model_extra"))
        calls = getattr(message, "tool_calls", None)
        entry["message"]["tool_calls"] = None if calls is None else [
            {**fields(call, ("id", "type")),
             "function": function_fields(call, ("name", "arguments"))}
            for call in calls]
        logprobs = getattr(choice, "logprobs", None)
        if logprobs is not None:
            entry["logprobs"] = fields(logprobs, ("content", "refusal"))
        result["choices"].append(entry)
    return result


def ollama_response(response: Any) -> dict[str, Any]:
    result = fields(response, ("model", "created_at", "done", "done_reason", "total_duration", "load_duration",
                               "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration", "model_extra"))
    message = getattr(response, "message", None)
    result["message"] = fields(message, ("role", "content", "thinking", "reasoning", "images", "model_extra"))
    calls = getattr(message, "tool_calls", None)

    result["message"]["tool_calls"] = None if calls is None else [
        {**fields(call, ("id", "type")),
         "function": function_fields(call, ("name", "arguments", "index"))}
        for call in calls]

    return result
