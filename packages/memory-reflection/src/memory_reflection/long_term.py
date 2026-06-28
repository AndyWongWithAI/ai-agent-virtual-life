"""长期记忆:Redis sorted set,score=ts,value=summary json,保留 30 天
I11 fix:add_summary 后给 key 加 35d TTL,兜底防 key 长期占内存
"""
from .models import Summary

LTM_KEY_TTL_SEC = 35 * 86400  # 35d 兜底(数据 30d,留 5d 余量)


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
        # I11 fix:给 key 加 TTL,30d 后 zset 已被 zremrangebyscore 清空,
        # key 自身 5d 兜底 expire,避免长期空 set 占内存。
        await self.redis.expire(key, LTM_KEY_TTL_SEC)

    async def recent_summaries(self, agent_id: str, n: int = 5) -> list[Summary]:
        key = self._key(agent_id)
        raw = await self.redis.zrevrange(key, 0, n - 1)
        return [Summary.model_validate_json(s) for s in raw]