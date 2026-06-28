"""短期记忆:每个 agent 一个 Redis list(上限 100 条),跨进程可见"""
from .models import Event


class ShortTermMemory:
    """每个 agent 一个 deque,最多 100 条;Redis-backed for cross-process access"""

    def __init__(self, redis, max_size: int = 100):
        self.redis = redis
        self.max_size = max_size

    def _key(self, agent_id: str) -> str:
        return f"stm:{agent_id}"

    async def add(self, event: Event):
        key = self._key(event.agent_id)
        await self.redis.lpush(key, event.model_dump_json())
        await self.redis.ltrim(key, 0, self.max_size - 1)

    async def recent(self, agent_id: str, n: int = 10) -> list[Event]:
        key = self._key(agent_id)
        raw = await self.redis.lrange(key, 0, n - 1)
        return [Event.model_validate_json(s) for s in raw]