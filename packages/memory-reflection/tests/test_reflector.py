"""Task 3: Reflector 单测
说明:
- 不依赖真实 Redis,用 AsyncMock 隔离 redis 调用
- 不依赖真实 LLM,llm.call 用 AsyncMock 返回预设文本
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_reflector_persists_last_reflect_to_redis():
    """I9 fix:反思成功后,last_reflect 应被写入 Redis(防进程重启后立即触发)"""
    fake_redis = AsyncMock()
    # 模拟 redis.set 返回值
    fake_redis.set = AsyncMock(return_value=True)
    fake_redis.get = AsyncMock(return_value=None)
    stm = ShortTermMemory(fake_redis)
    ltm = LongTermMemory(fake_redis)
    stm.recent = AsyncMock(return_value=[
        Event(agent_id="a1", kind="dialogue", content="e1", ts=datetime.now() - timedelta(hours=1)),
        Event(agent_id="a1", kind="decision", content="e2", ts=datetime.now() - timedelta(hours=2)),
        Event(agent_id="a1", kind="observation", content="e3", ts=datetime.now() - timedelta(hours=3)),
    ])
    ltm.add_summary = AsyncMock()
    llm = AsyncMock()
    llm.call = AsyncMock(return_value="summary")
    rf = Reflector(llm, stm, ltm)
    await rf.maybe_reflect("a1")
    # redis.set 应被调用一次,key=reflect:last:a1,带 TTL
    fake_redis.set.assert_called_once()
    args, kwargs = fake_redis.set.call_args
    assert args[0] == "reflect:last:a1"
    # TTL 必须设置
    assert "ex" in kwargs and kwargs["ex"] > 6 * 86400


@pytest.mark.asyncio
async def test_reflector_reads_last_reflect_from_redis_on_restart():
    """I9 fix:进程重启后,内存 cache 为空,应从 Redis 恢复 last_reflect
    而不立即再次触发反思
    """
    fake_redis = AsyncMock()
    # 模拟 Redis 里有 30 分钟前的时间戳 → 距 now < 6h → 应跳过
    last_ts = (datetime.now() - timedelta(minutes=30)).isoformat()
    fake_redis.get = AsyncMock(return_value=last_ts)
    fake_redis.set = AsyncMock()
    stm = ShortTermMemory(fake_redis)
    ltm = LongTermMemory(fake_redis)
    llm = AsyncMock()
    llm.call = AsyncMock()
    rf = Reflector(llm, stm, ltm)
    result = await rf.maybe_reflect("a1")
    assert result is None
    llm.call.assert_not_called()
    # Redis get 应被调一次
    fake_redis.get.assert_called_once_with("reflect:last:a1")


@pytest.mark.asyncio
async def test_reflector_events_sorted_ascending_in_prompt():
    """I10 fix:events_text 应按 ts 升序排(LLM 读起来是早→晚)"""
    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    stm = ShortTermMemory(fake_redis)
    ltm = LongTermMemory(fake_redis)
    # 故意乱序:3h 前,1h 前,2h 前
    now = datetime.now()
    stm.recent = AsyncMock(return_value=[
        Event(agent_id="a1", kind="observation", content="E3_now-3h", ts=now - timedelta(hours=3)),
        Event(agent_id="a1", kind="observation", content="E1_now-1h", ts=now - timedelta(hours=1)),
        Event(agent_id="a1", kind="observation", content="E2_now-2h", ts=now - timedelta(hours=2)),
    ])
    ltm.add_summary = AsyncMock()
    llm = AsyncMock()

    captured: dict = {}

    async def _capture_call(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "summary"

    llm.call = AsyncMock(side_effect=_capture_call)
    rf = Reflector(llm, stm, ltm)
    await rf.maybe_reflect("a1")
    prompt = captured["prompt"]
    pos_E1 = prompt.find("E1_now-1h")
    pos_E2 = prompt.find("E2_now-2h")
    pos_E3 = prompt.find("E3_now-3h")
    assert pos_E3 < pos_E2 < pos_E1, (
        f"events should be sorted ascending; got order: E3={pos_E3}, E2={pos_E2}, E1={pos_E1}"
    )


@pytest.mark.asyncio
async def test_maybe_reflect_publishes_to_bus():
    """V6:maybe_reflect 末尾必须 publish Topic.MEMORY_REFLECT 事件到 bus"""
    from memory_reflection import Reflector
    from event_bus import Topic

    llm = MagicMock(call=AsyncMock(return_value="张三最近比较累"))
    redis = MagicMock(
        get=AsyncMock(return_value=None),
        set=AsyncMock(),
        expire=AsyncMock(),
    )
    ltm = MagicMock(add_summary=AsyncMock(), redis=redis)
    bus = MagicMock(publish=AsyncMock())

    reflector = Reflector(llm, MagicMock(), ltm)
    # 强制 last_reflect 早于 6h 前,触发反思
    # Event 创建必须在 patch context 内,否则 ts=datetime.now() 走真实时间,CI runner 上 ts 会远离 mock now > 6h
    with patch("memory_reflection.reflector.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 6, 29, 18, 0)
        mock_dt.fromisoformat = datetime.fromisoformat
        # mock stm.recent 在 context 内返回 Event,datetime.now() 也走 mock
        events = [
            Event(agent_id="lisi", kind="decision", content="go to park", ts=mock_dt.now()),
            Event(agent_id="lisi", kind="decision", content="talk to wangwu", ts=mock_dt.now()),
            Event(agent_id="lisi", kind="decision", content="sleep", ts=mock_dt.now()),
        ]
        reflector.stm.recent = AsyncMock(return_value=events)
        await reflector.maybe_reflect("lisi", bus=bus)

    assert bus.publish.await_count == 1
    args = bus.publish.call_args
    assert args.args[0] == Topic.MEMORY_REFLECT
    payload = args.args[1]
    assert payload["agent_id"] == "lisi"
    assert payload["text"] == "张三最近比较累"


@pytest.mark.asyncio
async def test_maybe_reflect_no_publish_when_bus_is_none():
    """V6:bus=None 时(向后兼容)必须不 publish,不能 crash"""
    from memory_reflection import Reflector

    llm = MagicMock(call=AsyncMock(return_value="ok"))
    redis = MagicMock(
        get=AsyncMock(return_value=None),
        set=AsyncMock(),
        expire=AsyncMock(),
    )
    ltm = MagicMock(add_summary=AsyncMock(), redis=redis)
    reflector = Reflector(llm, MagicMock(), ltm)
    # 不传 bus,不能报错(正常触发反思但不发事件)
    # Event 创建必须在 patch context 内,否则 CI runner datetime.now() 远离 mock now > 6h
    with patch("memory_reflection.reflector.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 6, 29, 18, 0)
        mock_dt.fromisoformat = datetime.fromisoformat
        events = [
            Event(agent_id="lisi", kind="decision", content="e1", ts=mock_dt.now()),
            Event(agent_id="lisi", kind="decision", content="e2", ts=mock_dt.now()),
            Event(agent_id="lisi", kind="decision", content="e3", ts=mock_dt.now()),
        ]
        reflector.stm.recent = AsyncMock(return_value=events)
        result = await reflector.maybe_reflect("lisi")  # 无 bus 参数
    assert result == "ok"
