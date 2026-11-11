"""Host-owned user identity, session histories, pending work and retry receipts."""
from __future__ import annotations
import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from app.modules.agent.contracts import Agent
from app.schemas.coach_chat import CoachChatRequest, CoachChatResponse

@dataclass
class ChatJob:
    request: CoachChatRequest
    fingerprint: str
    future: asyncio.Future[CoachChatResponse]

@dataclass
class UserSession:
    user_id: int
    agent_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    inbox: deque[Any] = field(default_factory=deque)
    event_ids: set[str] = field(default_factory=set)
    queue: asyncio.Queue[ChatJob] = field(default_factory=asyncio.Queue)
    rooms: dict[str, list[list[dict[str, Any]]]] = field(default_factory=dict)
    receipts: OrderedDict[str, ChatJob] = field(default_factory=OrderedDict)
    engine: Agent | None = None
    worker: asyncio.Task | None = None
    running: bool = False
    retiring: bool = False
    last_activity: float = field(default_factory=time.monotonic)

    def enqueue(self, event: Any) -> bool:
        if event.event_id in self.event_ids:
            return False
        self.event_ids.add(event.event_id)
        self.inbox.append(event)
        return True

    def drain_inbox(self) -> list[Any]:
        events = list(self.inbox)
        self.inbox.clear()
        return events

    def status(self):
        from app.models import AgentStatus
        return AgentStatus(user_id=self.user_id, agent_id=self.agent_id,
                           created_at=self.created_at.isoformat(),
                           pending_events=len(self.inbox) + self.queue.qsize(),
                           status="busy" if self.running else ("idle" if self.engine else "idle_stub"))
