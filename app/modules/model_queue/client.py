"""서버 쪽: 요청 큐에 넣고 답장 큐에서 내 답을 기다림. 시간 안에 없으면 ModelTimeout."""
from __future__ import annotations

import time

from .messages import MessageError, ModelRequest, ModelResult


class ModelTimeout(TimeoutError):
    """기다리는 시간 안에 답이 없음 (집 PC 꺼짐 등) → 호출한 쪽이 대체 처리(예: 사용자에게 묻기)."""


class ModelQueue:
    def __init__(self, sqs, request_urls: dict[str, str], reply_url: str):
        self.sqs, self.request_urls, self.reply_url = sqs, request_urls, reply_url

    def call(self, task: str, payload: dict, timeout: float = 10.0) -> ModelResult:
        if task not in self.request_urls:
            raise KeyError(f"모르는 일: {task}")
        deadline = time.time() + timeout
        req = ModelRequest(task=task, payload=payload, reply_to=self.reply_url, expires_at=deadline)
        self.sqs.send_message(QueueUrl=self.request_urls[task], MessageBody=req.to_json())

        while (left := deadline - time.time()) > 0:
            got = self.sqs.receive_message(QueueUrl=self.reply_url, MaxNumberOfMessages=10,
                                           WaitTimeSeconds=max(1, min(20, int(left))))
            for message in got.get("Messages", []):
                receipt = message["ReceiptHandle"]
                try:
                    res = ModelResult.from_json(message.get("Body", ""))
                except MessageError:
                    self.sqs.delete_message(QueueUrl=self.reply_url, ReceiptHandle=receipt)
                    continue
                if res.request_id == req.request_id:
                    self.sqs.delete_message(QueueUrl=self.reply_url, ReceiptHandle=receipt)
                    return res
                if res.expired():  # 이미 아무도 안 기다리는 답장
                    self.sqs.delete_message(QueueUrl=self.reply_url, ReceiptHandle=receipt)
                else:              # 다른 요청의 답장 → 바로 돌려놓기
                    self.sqs.change_message_visibility(QueueUrl=self.reply_url, ReceiptHandle=receipt,
                                                       VisibilityTimeout=0)
        raise ModelTimeout(f"{task} 답이 {timeout:.0f}초 안에 안 옴 ({req.request_id})")
