"""长期记忆:Redis sorted set,score=ts,value=summary json,保留 30 天"""
from .models import Summary


class LongTermMemory:
    """Redis sorted set,score=ts,value=summary json"""

    def __init__(self, redis):
        self.redis = redis

    def _key(self, agent_id: str) -> str:
        return f"ltm:{agent_id}"

    async def add_summary(self, s: Summary):
        key = self._key(s.agent_id)
        score = s.period_end.timestamp()
        await self.redis.zadd(key, {s.model_dump_json(): score})
        await self.redis.zremrangebyscore(key, "-inf", score - 30 * 86400)  # 保留 30 天

    async def recent_summaries(self, agent_id: str, n: int = 5) -> list[Summary]:
        key = self._key(agent_id)
        raw = await self.redis.zrevrange(key, 0, n - 1)
        return [Summary.model_validate_json(s) for s in raw]