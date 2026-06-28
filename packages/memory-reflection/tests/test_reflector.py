"""Task 3: Reflector 单测

说明:
- 不依赖真实 Redis,用 AsyncMock 隔离 redis 调用
- 不依赖真实 LLM,llm.call 用 AsyncMock 返回预设文本
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from memory_reflection.short_term import ShortTermMemory
from memory_reflection.long_term import LongTermMemory
from memory_reflection.reflector import Reflector
from memory_reflection.models import Event


@pytest.mark.asyncio
async def test_reflector_skips_when_recent():
    """6h 内已反思过 -> 跳过,LLM 不调用"""
    fake_redis = AsyncMock()
    stm = ShortTermMemory(fake_redis)
    ltm = LongTermMemory(fake_redis)
    llm = AsyncMock()
    rf = Reflector(llm, stm, ltm)
    rf.last_reflect["a1"] = datetime.now()
    result = await rf.maybe_reflect("a1")
    assert result is None
    llm.call.assert_not_called()


@pytest.mark.asyncio
async def test_reflector_generates_summary():
    """首次反思 + 窗口内事件 >= 3 -> LLM 调用 + LTM 写入"""
    fake_redis = AsyncMock()
    stm = ShortTermMemory(fake_redis)
    ltm = LongTermMemory(fake_redis)
    stm.recent = AsyncMock(return_value=[
        Event(agent_id="a1", kind="dialogue", content="和李四吵架", ts=datetime.now() - timedelta(hours=1)),
        Event(agent_id="a1", kind="decision", content="回家", ts=datetime.now() - timedelta(hours=2)),
        Event(agent_id="a1", kind="observation", content="看到王五", ts=datetime.now() - timedelta(hours=3)),
        Event(agent_id="a1", kind="dialogue", content="和王五吃午饭", ts=datetime.now() - timedelta(hours=4)),
    ])
    ltm.add_summary = AsyncMock()
    llm = AsyncMock()
    llm.call = AsyncMock(return_value="今天和王五吃午饭,后与李四发生冲突,情绪低落回家。")
    rf = Reflector(llm, stm, ltm)
    result = await rf.maybe_reflect("a1")
    assert result is not None
    assert "王五" in result
    ltm.add_summary.assert_called_once()