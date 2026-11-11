"""집 쪽 워커: 요청 큐에서 꺼냄 → 모델에 물어봄 → 답장 큐에 넣음 → 요청 지움."""
from __future__ import annotations

import time
from typing import Callable

from app.modules.model_queue.messages import MessageError, ModelRequest, ModelResult

Handler = Callable[[dict], dict]


def handle_one(sqs, queue_url: str, message: dict, handlers: dict[str, Handler],
               log: Callable[[str], None] = print) -> str:
    """메시지 하나 처리. 돌려주는 값은 로그용 상태: replied | expired | bad | send-failed"""
    receipt = message["ReceiptHandle"]
    try:
        req = ModelRequest.from_json(message.get("Body", ""))
    except MessageError as error:
        log(f"버림(양식 오류): {error}")
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        return "bad"
    if req.expired():
        log(f"버림(기한 지남): {req.request_id}")
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        return "expired"

    handler = handlers.get(req.task)
    started = time.perf_counter()
    if handler is None:
        res = ModelResult(req.request_id, False, error=f"이 워커는 '{req.task}' 일을 모름")
    else:
        try:
            res = ModelResult(req.request_id, True, result=handler(req.payload))
        except Exception as error:  # 모델 오류도 답장으로 알려서 서버가 바로 대체 처리하게
            res = ModelResult(req.request_id, False, error=f"{type(error).__name__}: {error}"[:500])
    res.expires_at = req.expires_at

    try:
        sqs.send_message(QueueUrl=req.reply_to, MessageBody=res.to_json())
    except Exception as error:  # 못 보냈으면 지우지 않음 → 숨김 시간 지나면 다시 처리
        log(f"답장 실패(나중에 다시): {req.request_id} {type(error).__name__}: {error}")
        return "send-failed"
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
    ms = (time.perf_counter() - started) * 1000
    log(f"답장: {req.request_id} task={req.task} ok={res.ok} {ms:.0f}ms" + ("" if res.ok else f" ({res.error})"))
    return "replied"


def run(sqs, queue_url: str, handlers: dict[str, Handler], stop: Callable[[], bool] = lambda: False,
        log: Callable[[str], None] = print, wait_seconds: int = 20) -> None:
    """stop()이 True가 될 때까지 롱 폴링으로 계속 처리."""
    log(f"대기 중: {queue_url} (일: {', '.join(handlers)})")
    while not stop():
        try:
            got = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=wait_seconds)
        except Exception as error:  # 인터넷 끊김 등 → 잠깐 쉬고 다시
            log(f"받기 실패: {type(error).__name__}: {error}")
            time.sleep(5)
            continue
        for message in got.get("Messages", []):
            handle_one(sqs, queue_url, message, handlers, log)
