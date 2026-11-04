"""One logical UserAgent per user_id within this Python process.

The agent is a placeholder; no LLM, tools, background worker, or run loop.
No persistence across restarts or shared state across multiple processes.
"""

import asyncio

from app.agent.user_agent import UserAgent
from app.models.agent_event import AgentEvent, EventReceipt
from app.models.agent_status import AgentStatus


class AgentManager:
    def __init__(self) -> None:
        self._agents: dict[int, UserAgent] = {}
        self._lock = asyncio.Lock()

    async def accept(self, event: AgentEvent) -> EventReceipt:
        # Create/reuse one UserAgent, then enqueue atomically within this process.
        async with self._lock:
            agent = self._agents.get(event.user_id)
            created = agent is None
            if agent is None:
                agent = UserAgent(user_id=event.user_id)
                self._agents[event.user_id] = agent
            accepted = agent.enqueue(event)
            return EventReceipt(
                event_id=event.event_id,
                user_id=event.user_id,
                agent_id=agent.agent_id,
                agent_created=created,
                duplicate=not accepted,
                pending_events=len(agent.inbox),
            )

    async def get_status(self, user_id: int) -> AgentStatus | None:
        async with self._lock:
            agent = self._agents.get(user_id)
            return None if agent is None else agent.status()

    async def get_agent(self, user_id: int) -> UserAgent | None:
        """For future TaskRuntime/TaskSession integration (not an HTTP endpoint)."""
        async with self._lock:
            return self._agents.get(user_id)
