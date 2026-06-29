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
    # I12 fix:last_reflect 不再有内存缓存,通过 Redis 返回 30 分钟前的时间戳
    fake_redis.get = AsyncMock(
        return_value=(datetime.now() - timedelta(minutes=30)).isoformat()
    )
    fake_redis.set = AsyncMock()
    stm = ShortTermMemory(fake_redis)
    ltm = LongTermMemory(fake_redis)
    llm = AsyncMock()
    rf = Reflector(llm, stm, ltm)
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
    # I12 fix:Redis 无 last_reflect (返回 None) -> 触发反思
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


# --- 任务 #123(Bug2):strip 思考链 — LLM 不应把 chain-of-thought 泄漏到 LTM ---


def test_strip_think_blocks_removes_think_block():
    """``...`` 必须被剥离,落 LTM 的只是最终输出"""
    raw = "<think>think_internal_thought\nthink_more</think>\n最终摘要:李四今天睡了 8 小时。"
    cleaned = Reflector._strip_think_blocks(raw)
    assert "think_internal_thought" not in cleaned
    assert "think_more" not in cleaned
    assert "<think>" not in cleaned
    assert "最终摘要:李四今天睡了 8 小时。" in cleaned


def test_strip_think_blocks_removes_analysis_block():
    """另一种思考包裹形式 ``...`` 也应被剥离"""
    raw = "<analysis>analysis_internal</analysis>\n李四吃了早饭。"
    cleaned = Reflector._strip_think_blocks(raw)
    assert "analysis_internal" not in cleaned
    assert "<analysis>" not in cleaned
    assert "李四吃了早饭。" in cleaned


def test_strip_think_blocks_passthrough_clean_text():
    """普通文本不应被改动"""
    raw = "李四今天和王五去公园散步,中午吃了一碗面。"
    cleaned = Reflector._strip_think_blocks(raw)
    assert cleaned == raw


def test_strip_think_blocks_empty_returns_fallback():
    """全被 strip 掉的话返回 (空摘要) 占位,避免 LTM 存空字符串"""
    raw = "<think>all_thinking_content</think>"
    cleaned = Reflector._strip_think_blocks(raw)
    assert cleaned == "(空摘要)"


def test_strip_think_blocks_collapses_extra_newlines():
    """strip 后多余的空行应被合并"""
    raw = "<think>block1</think>\n\n\n\n\n李四散步。"
    cleaned = Reflector._strip_think_blocks(raw)
    # 三个以上连续换行应被合并
    assert "\n\n\n" not in cleaned
    assert "李四散步。" in cleaned
    assert "<think>" not in cleaned


@pytest.mark.asyncio
async def test_reflector_writes_stripped_text_to_ltm_and_publish():
    """任务 #123:LLM 返回带 think 块时,ltm.add_summary 与 bus.publish 都用干净文本,
    思考链不应泄漏到 LTM 和 WS。"""
    from memory_reflection import Reflector
    from event_bus import Topic

    captured_ltm_text: dict = {}
    captured_payload: dict = {}

    class _FakeLtm:
        def __init__(self):
            self.redis = MagicMock(
                get=AsyncMock(return_value=None),
                set=AsyncMock(),
            )

        async def add_summary(self, summary):
            captured_ltm_text["text"] = summary.text

    class _FakeBus:
        async def publish(self, topic, payload):
            captured_payload["text"] = payload.get("text")

        async def run_forever(self):
            return None

    llm = MagicMock(call=AsyncMock(return_value="<think>think_thinking_content\nthink_more</think>\n李四今天吃三顿饭,无异常。"))
    stm = MagicMock(recent=AsyncMock(return_value=[
        Event(agent_id="lisi", kind="decision", content="e1", ts=datetime(2026, 6, 29, 17, 0)),
        Event(agent_id="lisi", kind="decision", content="e2", ts=datetime(2026, 6, 29, 17, 30)),
        Event(agent_id="lisi", kind="decision", content="e3", ts=datetime(2026, 6, 29, 18, 0)),
    ]))
    rf = Reflector(llm, stm, _FakeLtm())
    with patch("memory_reflection.reflector.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 6, 29, 18, 0)
        mock_dt.fromisoformat = datetime.fromisoformat
        await rf.maybe_reflect("lisi", bus=_FakeBus())

    assert "<think>" not in captured_ltm_text["text"]
    assert "think_thinking_content" not in captured_ltm_text["text"]
    assert "李四今天吃三顿饭" in captured_ltm_text["text"]
    assert "<think>" not in captured_payload["text"]
    assert "think_thinking_content" not in captured_payload["text"]
    assert "李四今天吃三顿饭" in captured_payload["text"]


# --- 任务 #124(Bug3):反思 prompt 不应要求 LLM 下评判 ---


@pytest.mark.asyncio
async def test_reflector_prompt_asks_for_neutral_description():
    """任务 #124:反思 prompt 必须包含「不下判断/不下诊断」「客观」类约束,
    并且不应出现「情绪倾向」等会诱导价值判断的字段。"""
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.set = AsyncMock()
    stm = ShortTermMemory(fake_redis)
    ltm = LongTermMemory(fake_redis)
    now = datetime(2026, 6, 29, 18, 0)
    stm.recent = AsyncMock(return_value=[
        Event(agent_id="a1", kind="decision", content="E1", ts=now - timedelta(hours=1)),
        Event(agent_id="a1", kind="decision", content="E2", ts=now - timedelta(hours=2)),
        Event(agent_id="a1", kind="decision", content="E3", ts=now - timedelta(hours=3)),
    ])
    ltm.add_summary = AsyncMock()
    llm = AsyncMock()

    captured: dict = {}

    async def _capture(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return "ok"

    llm.call = AsyncMock(side_effect=_capture)

    rf = Reflector(llm, stm, ltm)
    with patch("memory_reflection.reflector.datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat
        await rf.maybe_reflect("a1")

    prompt = captured["prompt"]
    # 关键词约束
    assert "不下判断" in prompt, "prompt 应明确禁止下判断"
    assert "客观" in prompt, "prompt 应强调客观描述"
    assert "卡顿" in prompt, "prompt 应点名禁用词"
    # 旧 prompt 不应再出现
    assert "情绪倾向" not in prompt, "prompt 不应再要求情绪倾向(会诱导判断)"
    assert "与谁关系有变化" not in prompt


# --- I12 fix:Reflector 内存缓存 stale bug (task #83) ---


@pytest.mark.asyncio
async def test_get_last_no_memory_cache_returns_from_redis():
    """_get_last 必须直接读 Redis,不能有内存缓存(避免 stale 值卡住 6h gate)"""
    from memory_reflection import Reflector

    llm = MagicMock(call=AsyncMock(return_value="ok"))
    redis = MagicMock()
    # 模拟 Redis 里有 7h 前的 last_reflect
    seven_hours_ago = (datetime.now() - timedelta(hours=7)).isoformat()
    redis.get = AsyncMock(return_value=seven_hours_ago)
    redis.set = AsyncMock()
    redis.expire = AsyncMock()
    stm = MagicMock(
        add=AsyncMock(),
        recent=AsyncMock(
            return_value=[
                Event(agent_id="lisi", kind="decision", content="e", ts=datetime.now()),
                Event(agent_id="lisi", kind="decision", content="e", ts=datetime.now()),
                Event(agent_id="lisi", kind="decision", content="e", ts=datetime.now()),
            ]
        ),
    )
    ltm = MagicMock(add_summary=AsyncMock(), redis=redis)
    reflector = Reflector(llm, stm, ltm)
    last = await reflector._get_last("lisi")
    assert last is not None
    # 内存缓存必须不存在(verify attribute 删了或不存在)
    assert not hasattr(reflector, "last_reflect") or not getattr(reflector, "last_reflect", None), (
        "Reflector 不应有 last_reflect 内存缓存"
    )


@pytest.mark.asyncio
async def test_set_last_raises_on_redis_failure():
    """_set_last 写 Redis 失败必须 re-raise,不能被吞(避免上层以为成功)"""
    from memory_reflection import Reflector

    llm = MagicMock(call=AsyncMock(return_value="ok"))
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(side_effect=Exception("redis down"))
    redis.expire = AsyncMock()
    stm = MagicMock()
    ltm = MagicMock(add_summary=AsyncMock(), redis=redis)
    reflector = Reflector(llm, stm, ltm)
    with pytest.raises(Exception, match="redis down"):
        await reflector._set_last("lisi", datetime.now())


@pytest.mark.asyncio
async def test_maybe_reflect_triggers_when_redis_only_has_old_last():
    """e2e:Redis 里有 7h 前的 last_reflect,memory cache 即使有也不该 block 触发"""
    from memory_reflection import Reflector

    llm = MagicMock(call=AsyncMock(return_value="new summary"))
    seven_hours_ago_iso = (datetime.now() - timedelta(hours=7)).isoformat()
    redis = MagicMock()
    redis.get = AsyncMock(return_value=seven_hours_ago_iso)
    redis.set = AsyncMock()
    redis.expire = AsyncMock()
    now = datetime.now()
    events = [
        Event(
            agent_id="lisi",
            kind="decision",
            content=f"e{i}",
            ts=now - timedelta(minutes=30),
        )
        for i in range(3)
    ]
    stm = MagicMock(add=AsyncMock(), recent=AsyncMock(return_value=events))
    ltm = MagicMock(add_summary=AsyncMock(), redis=redis)
    bus = MagicMock(publish=AsyncMock())
    reflector = Reflector(llm, stm, ltm)
    # 即使注入内存缓存(模拟其他代码 path 错误地设了),也不该被读
    reflector.last_reflect = {"lisi": now}  # 现在时间,但 Redis 是 7h 前
    text = await reflector.maybe_reflect("lisi", bus=bus)
    assert text == "new summary"  # 触发了
    assert bus.publish.await_count == 1  # publish 了
