"""Provider-independent errors with explicit, conservative classification."""


class ContextOverflow(Exception):
    """The provider confirmed input context exhaustion (not output truncation)."""

    def __init__(self, message="Provider context window exceeded", *, limit_tokens=None):
        super().__init__(message)
        self.limit_tokens = limit_tokens if type(limit_tokens) is int and limit_tokens > 0 else None


class ContextUnrecoverable(Exception):
    """Compaction cannot reduce the request while preserving required input."""


def context_overflow(exc):
    """Recognize structured provider codes only; never guess from arbitrary text."""
    if isinstance(exc, ContextOverflow):
        return exc
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        body = body.get("error", body)
    code = body.get("code") if isinstance(body, dict) else getattr(exc, "code", None)
    if code in {"context_length_exceeded", "context_window_exceeded", "input_too_long"}:
        return ContextOverflow(str(exc))
    return None
