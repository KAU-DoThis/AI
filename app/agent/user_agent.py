"""In-memory UserAgent placeholder (no LLM, tools, or running loop)."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from app.models.agent_event import AgentEvent
from app.models.agent_status import AgentStatus


@dataclass
class UserAgent:
    user_id: int
    agent_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    inbox: deque[AgentEvent] = field(default_factory=deque)
    event_ids: set[str] = field(default_factory=set)

    def enqueue(self, event: AgentEvent) -> bool:
        """Return False for retried events; preserve first-received FIFO order."""
        if event.event_id in self.event_ids:
            return False
        self.event_ids.add(event.event_id)
        self.inbox.append(event)
        return True

    def drain_inbox(self) -> list[AgentEvent]:
        """Future agent loop may consume queued events at a safe boundary."""
        events = list(self.inbox)
        self.inbox.clear()
        return events

    def status(self) -> AgentStatus:
        return AgentStatus(
            user_id=self.user_id,
            agent_id=self.agent_id,
            created_at=self.created_at.isoformat(),
            pending_events=len(self.inbox),
        )
