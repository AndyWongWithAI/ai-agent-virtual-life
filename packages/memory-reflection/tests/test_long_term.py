"""LongTermMemory 单测 — I11 fix 验证"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock

from memory_reflection.long_term import LongTermMemory, LTM_KEY_TTL_SEC
from memory_reflection.models import Summary


@pytest.mark.asyncio
async def test_add_summary_calls_expire_with_35d_ttl():
    """I11 fix:add_summary 后应给 key 加 35d TTL,防 key 长期占内存"""
    fake_redis = AsyncMock()
    ltm = LongTermMemory(fake_redis)
    s = Summary(
        agent_id="a1",
        period_start=datetime.now(),
        period_end=datetime.now(),
        text="hello",
    )
    await ltm.add_summary(s)
    # zadd + zremrangebyscore + expire 三连
    assert fake_redis.zadd.await_count == 1
    assert fake_redis.zremrangebyscore.await_count == 1
    assert fake_redis.expire.await_count == 1
    # expire 应该是 35d
    args, kwargs = fake_redis.expire.call_args
    assert args[0] == "ltm:a1"
    assert args[1] == LTM_KEY_TTL_SEC
    assert LTM_KEY_TTL_SEC == 35 * 86400
