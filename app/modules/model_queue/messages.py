"""서버와 집 모델(Jeff·vLLM)이 SQS로 주고받는 편지 양식."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field


class MessageError(ValueError):
    """편지 모양이 양식과 다름."""


@dataclass
class ModelRequest:
    task: str                      # "jeff" | "slm" ...
    payload: dict
    reply_to: str                  # 답장 넣을 큐 주소
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    expires_at: float = 0.0        # 이 시각(epoch 초) 지나면 아무도 안 기다림. 0 = 기한 없음

    def expired(self, now: float | None = None) -> bool:
        return bool(self.expires_at) and (now if now is not None else time.time()) > self.expires_at

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "ModelRequest":
        data = _load(text)
        try:
            req = cls(task=data["task"], payload=data["payload"], reply_to=data["reply_to"],
                      request_id=data["request_id"], expires_at=float(data.get("expires_at") or 0))
        except (KeyError, TypeError, ValueError) as error:
            raise MessageError(f"요청 양식 오류: {error}") from None
        if not isinstance(req.payload, dict) or not req.task or not req.reply_to or not req.request_id:
            raise MessageError("요청 양식 오류: task·payload·reply_to·request_id 확인")
        return req


@dataclass
class ModelResult:
    request_id: str
    ok: bool
    result: dict | None = None
    error: str | None = None
    expires_at: float = 0.0        # 요청의 기한을 그대로 복사 (지난 답장은 받는 쪽이 버림)

    def expired(self, now: float | None = None) -> bool:
        return bool(self.expires_at) and (now if now is not None else time.time()) > self.expires_at

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "ModelResult":
        data = _load(text)
        try:
            return cls(request_id=data["request_id"], ok=bool(data["ok"]), result=data.get("result"),
                       error=data.get("error"), expires_at=float(data.get("expires_at") or 0))
        except (KeyError, TypeError, ValueError) as error:
            raise MessageError(f"답장 양식 오류: {error}") from None


def _load(text: str) -> dict:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise MessageError(f"JSON 아님: {error}") from None
    if not isinstance(data, dict):
        raise MessageError("JSON 객체가 아님")
    return data
