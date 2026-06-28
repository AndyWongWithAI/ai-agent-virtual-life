import pytest
from unittest.mock import AsyncMock, MagicMock
from event_bus.bus import EventBus
from event_bus.topics import Topic


@pytest.mark.asyncio
async def test_publish_calls_handlers():
    bus = EventBus(redis_url="redis://localhost:6380/0")
    bus.redis = AsyncMock()
    handler = MagicMock()
    bus.subscribe(Topic.AGENT_DECISION, handler)
    await bus.publish(Topic.AGENT_DECISION, {"agent_id": "a1", "action": "go_home"})
    bus.redis.publish.assert_called_once()
    args = bus.redis.publish.call_args
    assert args[0][0] == "agent.decision"
