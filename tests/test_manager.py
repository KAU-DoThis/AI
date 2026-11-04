import asyncio

import pytest

from app.agent.manager import AgentManager
from app.models.agent_event import AgentEvent, EventType


@pytest.mark.asyncio
async def test_concurrent_first_events_create_exactly_one_user_agent():
    manager = AgentManager()

    def event(number):
        return AgentEvent(
            event_id=f"evt-{number}",
            user_id=123,
            event_type=EventType.CHAT_MESSAGE,
            payload={"text": str(number)},
        )

    receipts = await asyncio.gather(*(manager.accept(event(n)) for n in range(100)))
    assert len({receipt.agent_id for receipt in receipts}) == 1
    assert sum(receipt.agent_created for receipt in receipts) == 1
    assert (await manager.get_status(123)).pending_events == 100
    agent = await manager.get_agent(123)
    queued = agent.drain_inbox()
    assert len(queued) == 100
    assert [item.event_id for item in queued] == [f"evt-{n}" for n in range(100)]
